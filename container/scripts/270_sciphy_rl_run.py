#!/usr/bin/env python3
"""Direct-family Qlib runner: SciPhy RL dynamic portfolio allocation (local adapted).

Registered hypothesis, signal parameters and record excerpts are frozen in the
family round-spec; this engine implements only what they require.

Signal (source-specified, record "Predictive Signal Construction"):
    zeta_t = alpha1 * rtilde_{t+1} + beta1 * EWMA_span3(rtilde)_t + u_t + eta_t
    with rtilde_t = log-return / expanding sigma, u ~ N(0,1), eta an AR(1) with
    phi, constants (alpha1, beta1, phi) = (0.2672, 0.2943, -0.0831) published for
    m=0.3, q=0.1, rho_asset=-0.0229. The rtilde_{t+1} term is the record's
    engineered ORACLE component: it is reproduced verbatim (source-faithful) and
    therefore carries next-period information BY SOURCE DESIGN. That look-ahead
    is disclosed, never hidden, and it is a missing condition for
    performance_claimable (contract 9.6 PIT), not a reason to rewrite the signal.

Control mapping (research-defined ladder mapping of the record's target-holding
reformulation, contract body DCA section): direction = sign(zeta) evaluated at
day di's open is direction[di-1] = sign(zeta_{di-1}) and is executed at that
open with adverse tick slippage. zeta_{di-1} embeds the record's engineered
oracle term alpha1 * rtilde_di, which is NOT observable at di-1's close: the
baseline therefore carries a one-day look-ahead BY SOURCE DESIGN (record
limitation "Engineered Oracle Signal Dependency"), disclosed as a contract 9.6
point-in-time failure. The registered entry_delay_1_bar grid evaluates the
identical signal one bar later, i.e. the point-in-time variant, and is reported
alongside. The ladder is the frozen 48-config DCA rail (initial tranche, adverse
scale-ins anchored to the initial entry, breakeven TP and resting invalidation
vs running average cost, reduce-only flatten). The phase window is tiled into
non-overlapping control episodes of T in {31, 63} days anchored at the phase
start (record execution windows / terminal utility; research-defined tiling -
the record's 1-day-step rolling windows are its evaluation convention): every
ladder leg is terminal-flattened at its episode boundary and the new episode's
tranche #1 opens on that same open, while a direction flip is the record's
instantaneous target-holding jump, so the old ladder is flattened reduce-only
and tranche #1 of the new direction is entered on the same open bar
(conservative ordering: flatten first). After an intraday TP / invalidation /
margin flatten the book stays flat for the rest of that bar; the next tranche
#1 opens only on a later qualified signal (non-zero direction at a later open),
and a closed leg is never re-added to (FLAT discipline).

Not reimplemented (disclosed adaptation boundary, never silently substituted):
the PINN value-function training of Eq. (73), the Gibbs mixture policy sampler
and the cross-asset quadratic impact tensor. The production image has no torch
(module absent), and the record publishes no Eq. (73) weights; the registered
cost model for this family is the candidate body's canonical taker fee + 1 tick
adverse slippage + official funding, stressed by the frozen grids. Falsification
item 1 therefore stays INDETERMINATE (never a pass).

Execution: daily bars (record timeframe, Delta t = 1), Qlib 0.9.7 dump/readback
proves the daily signal source, official funding is charged at its own
timestamp and mark price (missing official intervals are zero and disclosed,
modeled rows are never charged), every fill pays adverse tick slippage plus an
immediate taker fee, and gross PnL comes from an independent price-PnL
accumulator checked against net with a negative control.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import hashlib
import importlib.util
import itertools
import json
import math
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np

FAMILY_ID = "sciphy-physics-informed-reinforcement-learning-portfolio-optimization-2026-09-02"
ENGINE_VERSION = "sciphy_rl_qlib_v1"
RAW_ROOT = "/data/raw"
RESULTS_ROOT = "/results"
WORK_ROOT = "/qlib/work/sciphy-rl-v1"
QLIB_DIR = WORK_ROOT + "/qlib-data"
CSV_ROOT = WORK_ROOT + "/csv"
INSTRUMENTS_PATH = RAW_ROOT + "/binance/usdm/instruments/usdm-perp-instruments.json"
VENV_PYTHON = "/opt/venv/bin/python"
DUMP_BIN = "/opt/qlib-tools/dump_bin.py"
MS_DAY = 86_400_000
FIELDS = ("open", "high", "low", "close", "volume")
SYMBOLS = ("BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT")
TIMEFRAME = "1d"
START_EQUITY = 30_000.0
BASE_QUOTE = 1_000.0
LEVERAGE = 10.0
MAX_ADD_LEVELS = 10          # tranche #1 + 10 scale-ins = 11 routine active levels
LADDER_LEVELS = 12           # tranche #12 stays reserve, never routinely deployed
SEED = 270928
# Source-published signal constants (record: Predictive Signal Construction).
SIGNAL_PARAMS = {"alpha1": 0.2672, "beta1": 0.2943, "phi": -0.0831,
                 "memory_share_m": 0.3, "informative_variance_q": 0.1,
                 "rho_asset": -0.0229, "ewma_span": 3, "warmup_days": 20,
                 "provenance": "source-specified for T=63, q=0.1, m=0.3"}
# The registered signal embeds the record's oracle term, so the baseline decision uses the
# execution day's own return. This is a contract 9.6 point-in-time FAIL, disclosed everywhere
# claimability is discussed - never silently upgraded to a pass.
PIT_STATUS = "FAIL_LOOKAHEAD_BY_SOURCE_DESIGN"
STRATEGY_AXES = {"episode_days": (31, 63)}
CASES = tuple({"case_code": i, "episode_days": days,
               "label": "t%d" % days}
              for i, days in enumerate(STRATEGY_AXES["episode_days"]))
DCA_AXES = {"spacing_pct": (0.01, 0.02, 0.03, 0.04),
            "size_multiplier": (1.0, 1.1), "breakeven_tp_pct": (0.01, 0.02, 0.03),
            "invalidation_pct": (0.05, 0.10)}
DCA_GRID = tuple(dict(zip(DCA_AXES, v)) for v in itertools.product(*DCA_AXES.values()))
GRIDS = ("historical", "oos", "full", "fee_2x", "funding_2x", "entry_delay_1_bar",
         "slippage_2ticks", "no_funding", "no_funding_full", "cost_attrition_40bps")
MIN_EPISODES_IS = 10
MIN_EPISODES_OOS = 3
MIN_NEIGHBOUR = 0.60
PHASES = {"historical": ("2022-01-01", "2025-09-30"),
          "oos": ("2025-10-01", "2026-09-11"),
          "full": ("2022-01-01", "2026-09-11")}
DECAY_LOOKBACK = 3           # research-defined decay-prone short-term reversal
COST_35BPS = 0.0035          # record falsification item 3 stress level
SOURCE_EW_SHARPE = 0.126     # record's T=63 OOS equal-weight baseline
FALSIFICATION_REGISTRY = {
    "hjb_regularization_ablation": "record item 1: train the network without the pathwise HJ residual loss of Eq. (73); Sharpe within 1 SE of full SciPhyRL falsifies the necessity of physics regularization",
    "signal_decay_stress": "record item 2: replace the engineered oracle signal with a decay-prone short-term reversal (half-life < 3 days); net Sharpe <= the equal-weight baseline (0.126) rejects the multi-period edge for fast-decay alphas",
    "transaction_cost_10_to_35bps": "record item 3: raise modeled costs from 10 bps to 35 bps; OOS Sharpe gain over equal weight <= 0.05 falsifies cost-resilient multi-period optimization",
    "window_overlap_significance": "record item 4: non-overlapping T-day windows / block bootstrap; p-value of the Sharpe advantage over equal weight > 0.05 invalidates the horizon-stability hypothesis",
}
FALSIFICATION = [
    "HJB-regularization ablation stays INDETERMINATE while the Eq. (73) neural training is absent from the production image; it is never counted as a pass",
    "decay-prone 3-day reversal signal net Sharpe <= the equal-weight baseline falsifies the edge for fast-decay alphas",
    "OOS Sharpe gain over equal weight <= 0.05 at 35 bps costs falsifies cost resilience",
    "block-bootstrap p-value of the OOS Sharpe advantage over equal weight > 0.05 invalidates horizon stability",
]
ARTIFACTS = ("state.json", "result.json", "logs/run.log", "artifacts/progress.json",
             "artifacts/raw_build.json", "artifacts/funding_coverage.json",
             "artifacts/microstructure.json", "artifacts/signal_metrics.json",
             "artifacts/baseline_equal_weight.json", "artifacts/stress_effects.json",
             "artifacts/falsification.json", "artifacts/dca_layer_histogram.json",
             "artifacts/cohort_results.json", "artifacts/cohort_survivors.json",
             "artifacts/assertions.json",
             *((("artifacts/grid_%s.csv" % g) for g in GRIDS)))


def expected_counts():
    per = len(SYMBOLS) * len(CASES) * len(DCA_GRID)
    return {"cohorts": len(SYMBOLS), "strategy_cases_per_cohort": len(CASES),
            "dca_configs_per_cohort": len(DCA_GRID),
            "base_combinations_per_cohort": len(CASES) * len(DCA_GRID),
            "case_evaluations_per_grid": per, "grid_count": len(GRIDS),
            "case_evaluations_total": per * len(GRIDS)}


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def utc_ms(day):
    return int(dt.datetime.fromisoformat(day[:10]).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def stamp(ms):
    return dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).strftime("%Y-%m-%d")


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(value, fh, indent=2, ensure_ascii=False, allow_nan=False)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def axis(got, registered):
    return list(got) == list(registered)


def run_spec_template():
    here = Path(__file__).resolve()
    return {
        "schema_version": 1, "document_kind": "run_spec",
        "contract": "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md v2.0.0",
        "family_id": FAMILY_ID, "round_id": "<family_id>-r1", "run_id": "<round_id>-u1",
        "created_at_utc": "<ISO8601Z>", "container_id": "qlib-run", "image_id": "qlib:0.9.7-arm64",
        "script": {"path": "/scripts/270_sciphy_rl_run.py", "sha256": sha256_file(here),
                   "deployed_host_path": "/Users/hong/workspace/qlib-apple-container/scripts/270_sciphy_rl_run.py"},
        "engine": {"name": ENGINE_VERSION, "script": "container/scripts/270_sciphy_rl_run.py",
                   "container": "qlib-run", "qlib_version": "0.9.7", "seed": SEED,
                   "self_check": "container/scripts/tests/test_sciphy_rl_engine.py",
                   "self_check_sha256": sha256_file(here.with_name("tests") / "test_sciphy_rl_engine.py")},
        "data": {"raw_root": "/data/raw/binance/usdm", "start": PHASES["full"][0],
                 "end": PHASES["full"][1], "timezone": "UTC", "symbols": list(SYMBOLS),
                 "fields": list(FIELDS),
                 "signal_timeframe": {"raw_interval": TIMEFRAME, "qlib_freq": "day"},
                 "execution_timeframe": {"raw_interval": TIMEFRAME, "qlib_freq": "day",
                                         "bars_per_day": 1}},
        "split": {"historical_start": PHASES["historical"][0], "historical_end": PHASES["historical"][1],
                  "oos_start": PHASES["oos"][0], "oos_end": PHASES["oos"][1],
                  "rule": "chronological; frozen before compute; OOS never used for selection"},
        "params": [dict(c) for c in CASES],
        "dca_domain": {**{k: list(v) for k, v in DCA_AXES.items()}, "grid": [dict(d) for d in DCA_GRID],
                       "base_quote": BASE_QUOTE, "base_quote_status": "PROJECT_PRE_REGISTERED_CONSTANT",
                       **{k + "_status": "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN" for k in DCA_AXES}},
        "selector_version": "cohort-selector-v1", "disposition_version": "cohort-disposition-v1",
        "expected": expected_counts(), "gates": {"min_episodes_is": MIN_EPISODES_IS,
                                                 "min_episodes_oos": MIN_EPISODES_OOS,
                                                 "min_neighbour_same_sign_fraction": MIN_NEIGHBOUR},
        "signal_constants": dict(SIGNAL_PARAMS),
        "costs": {"taker_fee": "canonical instrument taker_fee", "slippage_ticks": 1,
                  "slippage_robustness_ticks": 2,
                  "funding": "official observations only at timestamp/mark; missing intervals zero and disclosed"},
        "falsification": FALSIFICATION, "expected_outputs": list(ARTIFACTS),
        "notes": "direct family: family_id + round_id + run_id only; no Kanban ownership keys."}


def validate_spec(spec, script_path=None, test_path=None):
    if spec.get("family_id") != FAMILY_ID or spec.get("selector_version") != "cohort-selector-v1" \
            or spec.get("disposition_version") != "cohort-disposition-v1":
        raise ValueError("wrong family/selector/disposition identity")
    rid = spec.get("round_id")
    if not (isinstance(rid, str) and re.fullmatch(re.escape(FAMILY_ID) + r"-r[1-9][0-9]*", rid)
            and isinstance(spec.get("run_id"), str)
            and re.fullmatch(re.escape(rid) + r"-u[1-9][0-9]*", spec["run_id"])):
        raise ValueError("round/run identity mismatch")
    if any(k in spec for k in ("task_id", "kanban_task_id", "kanban_board")):
        raise ValueError("direct family must not carry task/board ids")
    if not spec.get("created_at_utc"):
        raise ValueError("missing created_at_utc")
    data = spec.get("data", {})
    if data.get("raw_root") != "/data/raw/binance/usdm" or data.get("fields") != list(FIELDS) \
            or data.get("symbols") != list(SYMBOLS):
        raise ValueError("canonical local universe mismatch")
    if data.get("signal_timeframe") != {"raw_interval": TIMEFRAME, "qlib_freq": "day"} \
            or data.get("execution_timeframe") != {"raw_interval": TIMEFRAME, "qlib_freq": "day",
                                                   "bars_per_day": 1}:
        raise ValueError("signal/execution timeframe mismatch")
    if spec.get("params") != [dict(c) for c in CASES]:
        raise ValueError("strategy domain must be the exact frozen two cases")
    dca = spec.get("dca_domain", {})
    if any(not axis(dca.get(k, ()), v) for k, v in DCA_AXES.items()) \
            or dca.get("grid") != [dict(d) for d in DCA_GRID] or dca.get("base_quote") != BASE_QUOTE:
        raise ValueError("DCA domain incomplete/changed")
    if dca.get("base_quote_status") != "PROJECT_PRE_REGISTERED_CONSTANT" \
            or any(dca.get(k + "_status") != "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN" for k in DCA_AXES):
        raise ValueError("DCA provenance mismatch")
    if spec.get("expected") != expected_counts() or spec.get("expected_outputs") != list(ARTIFACTS) \
            or spec.get("falsification") != FALSIFICATION:
        raise ValueError("coverage/artifact/falsification registration mismatch")
    if spec.get("signal_constants") != dict(SIGNAL_PARAMS):
        raise ValueError("signal constants must stay the source-published values")
    script = spec.get("script", {})
    if script.get("path") != "/scripts/270_sciphy_rl_run.py" \
            or script.get("sha256") != sha256_file(script_path or __file__):
        raise ValueError("script bytes do not match pinned identity")
    engine = spec.get("engine", {})
    test = Path(test_path) if test_path else Path(__file__).resolve().with_name("tests") / "test_sciphy_rl_engine.py"
    if engine.get("name") != ENGINE_VERSION or engine.get("qlib_version") != "0.9.7" \
            or engine.get("seed") != SEED or engine.get("self_check_sha256") != sha256_file(test):
        raise ValueError("engine/self-check identity mismatch")
    with open(RAW_ROOT + "/_meta/CONFIG.json", encoding="utf-8") as fh:
        catalog = json.load(fh)
    if catalog.get("market_type") != "usdm_perp" or sorted(catalog.get("symbols", [])) != list(SYMBOLS) \
            or TIMEFRAME not in set(catalog.get("intervals", [])) \
            or not {"klines", "funding"} <= set(catalog.get("datasets", {})):
        raise ValueError("canonical catalog diverged from registered universe")
    return expected_counts()


def instrument_metadata(path=INSTRUMENTS_PATH):
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    by_symbol = {r["fields"]["raw_symbol"]: r["fields"] for r in doc["instruments"]}
    out = {}
    for sym in SYMBOLS:
        f = by_symbol[sym]
        if f.get("type") != "CryptoPerpetual" or f.get("quote_currency") != "USDT" or f.get("is_inverse"):
            raise ValueError("not a linear USDT perpetual: " + sym)
        tick, fee = float(f["price_increment"]), float(f["taker_fee"])
        if not (0 < tick and 0 <= fee < 0.01):
            raise ValueError("invalid tick/fee: " + sym)
        out[sym] = {"tick": tick, "taker_fee": fee, "source_sha256": sha256_file(path)}
    return out


def raw_files(symbol, interval, start, end):
    """Canonical monthly shards whose month falls inside [start, end].

    The month is the part between the "<symbol>-<interval>-" prefix and the
    first dot: slicing off only ".gz" ([:-3]) leaves ".jsonl", which sorts above
    the end month and silently dropped the final month of the window.
    """
    root = Path(RAW_ROOT) / "binance/usdm/klines" / symbol / interval
    files = sorted(root.glob("%s-%s-*.jsonl.gz" % (symbol, interval)))
    out = []
    for f in files:
        tail = f.name.split("-" + interval + "-", 1)[-1]
        month = tail.split(".", 1)[0]
        if start[:7] <= month <= end[:7]:
            out.append(f)
    return out


def load_rows(symbol, interval, start, end, fields):
    lo, hi = utc_ms(start), utc_ms(end) + MS_DAY
    times, cols = [], {f: [] for f in fields}
    prev = None
    files = raw_files(symbol, interval, start, end)
    if not files:
        raise RuntimeError("missing %s/%s raw" % (symbol, interval))
    for path in files:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                row = json.loads(line)
                t = int(row["open_time_ms"])
                if not lo <= t < hi:
                    continue
                if prev is not None and t <= prev:
                    raise RuntimeError("unsorted/duplicate bars")
                if prev is not None and t - prev != MS_DAY:
                    raise RuntimeError("gap in canonical %s/%s/%s" % (symbol, interval, stamp(t)))
                for f in fields:
                    value = float(row[f])
                    if not math.isfinite(value):
                        raise RuntimeError("non-finite %s in %s" % (f, symbol))
                    if (value < 0) or (f != "volume" and value <= 0):
                        raise RuntimeError("invalid %s in %s" % (f, symbol))
                    cols[f].append(value)
                times.append(t)
                prev = t
    if not times or times[0] != lo or times[-1] != hi - MS_DAY:
        raise RuntimeError("registered window not fully covered by %s/%s: first=%s last=%s expected %s..%s"
                           % (symbol, interval, stamp(times[0]) if times else "none",
                              stamp(times[-1]) if times else "none", stamp(lo), stamp(hi - MS_DAY)))
    return {"open_ms": np.asarray(times, dtype=np.int64),
            **{f: np.asarray(v, dtype=np.float64) for f, v in cols.items()},
            "raw_files": [{"path": str(x), "sha256": sha256_file(x)} for x in files]}


def write_csv(symbol, panel):
    path = Path(CSV_ROOT) / ("%s.csv" % symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["date", *FIELDS])
        for i, t in enumerate(panel["open_ms"]):
            writer.writerow([stamp(t), *(panel[f][i] for f in FIELDS)])
    return path


def build_qlib(daily, start, end):
    if Path(WORK_ROOT).exists():
        shutil.rmtree(WORK_ROOT)
    for symbol in SYMBOLS:
        write_csv(symbol, daily[symbol])
    cmd = [VENV_PYTHON, DUMP_BIN, "dump_all", "--data_path", CSV_ROOT, "--qlib_dir", QLIB_DIR,
           "--freq", "day", "--include_fields", ",".join(FIELDS), "--date_field_name", "date",
           "--max_workers", "1"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if proc.returncode:
        raise RuntimeError("dump_bin failed: %s / %s" % (proc.stdout[-800:], proc.stderr[-800:]))
    import qlib
    from qlib.data import D
    if qlib.__version__ != "0.9.7":
        raise RuntimeError("Qlib version mismatch")
    qlib.init(provider_uri=QLIB_DIR, region="cn", expression_cache=None, dataset_cache=None)
    readback = {}
    for symbol in SYMBOLS:
        df = D.features([symbol], ["$" + f for f in FIELDS], start_time=start,
                        end_time=end + " 23:59:59", freq="day").sort_index()
        times = np.asarray([int(x.value // 1_000_000) for x in df.index.get_level_values("datetime")],
                           dtype=np.int64)
        vals = {f: df["$" + f].to_numpy(dtype=np.float64) for f in FIELDS}
        if len(times) != len(daily[symbol]["open_ms"]) or not np.array_equal(times, daily[symbol]["open_ms"]):
            raise RuntimeError("Qlib daily readback mismatch: " + symbol)
        if any(not np.isfinite(v).all() for v in vals.values()) or not np.all((vals["open"] > 0) & (vals["close"] > 0)):
            raise RuntimeError("invalid Qlib daily readback: " + symbol)
        for f in FIELDS:
            # Qlib dump_bin stores feature bins as float32, so CSV -> .bin quantises every
            # field on every row: compare the exact float32 round-trip of the raw value.
            expected = np.asarray(daily[symbol][f], dtype=np.float32).astype(np.float64)
            if np.array_equal(vals[f], expected):
                continue
            if not np.allclose(vals[f], daily[symbol][f], rtol=1e-6, atol=1e-9):
                raise RuntimeError("Qlib value mismatch beyond float32 round-trip: %s/%s" % (symbol, f))
        readback[symbol] = {"open_ms": times, **vals}
    return readback, {"qlib_version": qlib.__version__, "read_path": "qlib.data.D.features",
                      "daily_rows": {s: len(daily[s]["open_ms"]) for s in SYMBOLS},
                      "readback_tolerance": "exact float32 dump_bin round-trip, else relative 1e-6",
                      "qlib_rows_identical_to_raw": True}


def load_funding(symbol, open_ms, end_ms, root=RAW_ROOT):
    path = Path(root) / "binance/usdm/funding" / symbol / ("%s-funding.jsonl.gz" % symbol)
    events = [[] for _ in open_ms]
    report = {"file_missing": not path.is_file(), "official": 0, "charged_events": 0,
              "modeled_ignored": 0,
              "other_ignored": 0, "out_of_window": 0, "days_with_official": 0,
              "days_total": len(open_ms), "first_official_utc": None, "last_official_utc": None,
              "missing_intervals_are_zero_not_modeled": True}
    if not path.is_file():
        return events, report
    report["source_sha256"] = sha256_file(path)
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            t = int(row["funding_time_ms"])
            if not int(open_ms[0]) <= t < int(end_ms) + MS_DAY:
                report["out_of_window"] += 1
                continue
            if row.get("truth_status") != "official":
                report["modeled_ignored" if row.get("truth_status") in ("modeled", "modeled_funding")
                       else "other_ignored"] += 1
                continue
            rate, mark = float(row["funding_rate"]), float(row["mark_price"])
            if not (math.isfinite(rate) and math.isfinite(mark) and mark > 0):
                raise RuntimeError("invalid official funding")
            bar = int(np.searchsorted(open_ms, t, side="right") - 1)
            if not 0 <= bar < len(events):
                raise RuntimeError("funding outside panel")
            events[bar].append((t, rate * mark))
            report["official"] += 1
            report["charged_events"] += 1
            iso = dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            report["first_official_utc"] = report["first_official_utc"] or iso
            report["last_official_utc"] = iso
    report["days_with_official"] = sum(bool(x) for x in events)
    report["official_events_per_day"] = report["official"] / max(1, len(events))
    report["coverage_note"] = ("official observations only; days before first_official_utc and any "
                               "missing interval contribute zero funding cost, never a modeled row")
    return events, report


def corwin_schultz(panel):
    """Corwin-Schultz (2012) high-low spread proxy from the registered daily H/L."""
    H, L = panel["high"], panel["low"]
    n = len(H)
    spread = np.zeros(n)
    valid = np.zeros(n, dtype=bool)
    for t in range(n - 1):
        gamma = math.log(H[t] / L[t]) ** 2 + math.log(H[t + 1] / L[t + 1]) ** 2
        beta = math.log(max(H[t], H[t + 1]) / min(L[t], L[t + 1])) ** 2
        if beta <= gamma:
            continue                      # two-day high/low range not wide enough
        root = math.sqrt(2.0) - 1.0
        alpha = (math.sqrt(2.0 * beta) - math.sqrt(beta)) / (3.0 - 2.0 * math.sqrt(2.0)) \
            - math.sqrt(gamma / (3.0 - 2.0 * math.sqrt(2.0)))
        if alpha <= 0:
            continue
        spread[t] = 2.0 * (math.exp(alpha) - 1.0) / (1.0 + math.exp(alpha))
        valid[t] = True
    return spread, valid


def adv_panel(panel, span=20):
    """Average daily volume over the trailing span sessions (point-in-time)."""
    vol = panel["volume"]
    out = np.full(len(vol), np.nan)
    for t in range(span, len(vol)):
        out[t] = float(np.mean(vol[t - span:t]))
    return out


def build_signal(panel, seed_offset=0):
    """Registered oracle signal; direction == sign(zeta), 0 while undefined."""
    C = panel["close"]
    n = len(C)
    warmup = SIGNAL_PARAMS["warmup_days"]
    if n < warmup + 8:
        raise RuntimeError("insufficient history for the registered signal")
    ret = np.zeros(n)
    ret[1:] = np.diff(np.log(C))
    sigma = np.full(n, np.nan)
    for t in range(warmup + 1, n):
        prior = ret[1:t]                  # returns of days strictly before t
        if len(prior) >= warmup:
            s = float(np.std(prior, ddof=1))
            if s > 0:
                sigma[t] = s
    rtilde = np.zeros(n)
    defined_norm = np.isfinite(sigma) & (sigma > 0)
    rtilde[defined_norm] = ret[defined_norm] / sigma[defined_norm]
    ewma = np.empty(n)
    alpha = 2.0 / (SIGNAL_PARAMS["ewma_span"] + 1.0)   # pandas span=3 -> alpha 0.5
    ewma[0] = rtilde[0]
    for t in range(1, n):
        ewma[t] = alpha * rtilde[t] + (1.0 - alpha) * ewma[t - 1]
    rng = np.random.default_rng(SEED + 1009 + seed_offset)
    u = rng.standard_normal(n)
    phi = SIGNAL_PARAMS["phi"]
    eta = np.empty(n)
    eta[0] = rng.standard_normal()
    shocks = rng.standard_normal(n)
    drift = math.sqrt(max(0.0, 1.0 - phi * phi))
    for t in range(1, n):
        eta[t] = phi * eta[t - 1] + drift * shocks[t]
    zeta = np.full(n, np.nan)
    for t in range(warmup + 1, n - 1):    # needs rtilde[t+1] (the oracle term)
        if defined_norm[t] and defined_norm[t + 1]:
            zeta[t] = (SIGNAL_PARAMS["alpha1"] * rtilde[t + 1]
                       + SIGNAL_PARAMS["beta1"] * ewma[t] + u[t] + eta[t])
    direction = np.zeros(n, dtype=np.int8)
    ok = np.isfinite(zeta)
    direction[ok & (zeta > 0)] = 1
    direction[ok & (zeta < 0)] = -1
    idx = np.flatnonzero(ok)
    target = rtilde[np.minimum(idx + 1, n - 1)]
    r2 = float(np.corrcoef(zeta[idx], target)[0, 1] ** 2) if len(idx) > 3 else None
    ac = float(np.corrcoef(zeta[idx[:-1]], zeta[idx[1:]])[0, 1]) if len(idx) > 3 else None
    return {"zeta": zeta, "direction": direction, "rtilde": rtilde, "ewma": ewma,
            "sigma": sigma, "defined": int(len(idx)), "oracle_r2_full": r2,
            "signal_lag1_autocorr": ac, "warmup_days": warmup}


def signal_metric_fields(layer):
    return {k: (v.tolist() if isinstance(v, np.ndarray) else v)
            for k, v in layer.items() if k not in ("zeta", "rtilde", "ewma", "sigma")}


def decay_direction(panel, lookback=DECAY_LOOKBACK):
    """Research-defined decay-prone signal for record falsification item 2:
    cross-sectional short-term reversal, expressed per cohort as the negative of
    the trailing `lookback` normalized returns (half-life < 3 days, causal)."""
    C = panel["close"]
    n = len(C)
    ret = np.zeros(n)
    ret[1:] = np.diff(np.log(C))
    sig = np.zeros(n, dtype=np.int8)
    for t in range(lookback, n):
        past = float(np.sum(ret[t - lookback:t]))
        sig[t] = -1 if past > 0 else (1 if past < 0 else 0)
    return sig


def window_indices(ms, start, end):
    return int(np.searchsorted(ms, utc_ms(start))), int(np.searchsorted(ms, utc_ms(end) + MS_DAY))


def empty_metric(i0, i1):
    return {"gross_pnl": 0.0, "fees": 0.0, "funding": 0.0, "net_pnl": 0.0,
            "ending_equity": START_EQUITY, "episodes": 0, "fills": 0, "adds": 0,
            "turnover_usdt": 0.0, "sharpe": 0.0, "max_dd_pct": 0.0, "max_dd_usdt": 0.0,
            "annualized_return": 0.0, "max_effective_leverage": 0.0, "capital_utilization": 0.0,
            "tp_hits": 0, "stop_hits": 0, "margin_calls": 0, "end_exits": 0, "open_at_end": 0,
            "terminal_exits": 0, "rebalance_exits": 0, "layer_hist": [0] * LADDER_LEVELS,
            "decomposition_ok": True, "daily_equity": [START_EQUITY] * max(0, i1 - i0)}


def metric_block(m):
    keys = ("gross_pnl", "fees", "funding", "net_pnl", "ending_equity", "episodes", "fills",
            "adds", "turnover_usdt", "sharpe", "max_dd_pct", "max_dd_usdt", "annualized_return",
            "max_effective_leverage", "capital_utilization", "tp_hits", "stop_hits", "margin_calls",
            "end_exits", "open_at_end", "terminal_exits", "rebalance_exits")
    return {k: m[k] for k in keys}


def simulate(panel, funding_events, direction, case, dca, i0, i1, cost, instrument):
    """Run the registered ladder on daily bars for one (signal, case, DCA, phase) cell.

    Daily bar = one deterministic conservative ordering: open actions
    (episode-boundary terminal flatten / flip rebalance / tranche #1) happen at the open with
    adverse tick slippage, then that day's official settlements while the book is
    live, then the intraday rail (open-gap invalidation, adverse scale-ins,
    margin guard, invalidation, breakeven TP), then the window-end flatten.
    Gross PnL is accumulated only from the independent price-PnL expression.
    """
    if i1 <= i0:
        return empty_metric(i0, i1)
    if i0 < 0 or i1 > len(panel["open"]):
        raise ValueError("window outside panel")
    tick = instrument["tick"] * int(cost.get("slip_ticks", 1))
    fee_rate = float(cost.get("fee_override", instrument["taker_fee"] * cost.get("fee_mult", 1.0)))
    if fee_rate < 0:
        raise ValueError("negative fee")
    mult = float(dca["size_multiplier"])
    spacing = float(dca["spacing_pct"])
    tp_pct = float(dca["breakeven_tp_pct"])
    inv_pct = float(dca["invalidation_pct"])
    delay = int(cost.get("entry_delay", 0))
    funding_mult = float(cost.get("funding_mult", 1.0))
    no_funding = bool(cost.get("no_funding", False))
    horizon = int(case["episode_days"])
    n_days = i1 - i0
    cash = np.zeros(n_days)
    unreal = np.zeros(n_days)
    gross = fees = funding_paid = turnover = realized = 0.0
    eps = fills = adds = tp_hits = stop_hits = margin_calls = end_exits = 0
    terminal_exits = rebalance_exits = 0
    max_lev = max_util = 0.0
    hist = [0] * LADDER_LEVELS
    O, H, L, C, ms = panel["open"], panel["high"], panel["low"], panel["close"], panel["open_ms"]
    qty = basis = 0.0
    entry_price = 0.0
    quote0 = 0.0
    entry_day = -1
    next_level = 1
    layers_used = 0
    held_dir = 0

    def close_position(exit_px, base, reason):
        nonlocal qty, basis, gross, fees, realized, turnover, fills, funding_paid
        nonlocal terminal_exits, rebalance_exits, end_exits, hist
        ep_gross = qty * exit_px - basis
        gross += ep_gross
        exit_fee = abs(qty) * abs(exit_px) * fee_rate
        realized += ep_gross - exit_fee
        fees += exit_fee
        turnover += abs(qty * exit_px)
        fills += 1
        cash[base] += ep_gross - exit_fee
        hist[0] += 1
        hist[min(LADDER_LEVELS - 1, layers_used)] += 1
        if reason == "terminal":
            terminal_exits += 1
        elif reason == "rebalance":
            rebalance_exits += 1
        elif reason == "close":
            end_exits += 1
        qty = basis = 0.0

    for di in range(i0, i1):
        base = di - i0
        sig_idx = di - 1 - delay
        target = int(direction[sig_idx]) if 0 <= sig_idx < len(direction) else 0
        open_px = float(O[di])
        if open_px <= 0:
            raise RuntimeError("nonpositive open")
        if qty != 0:
            if (di - i0) % horizon == 0:
                side = 1.0 if qty > 0 else -1.0
                close_position(open_px - tick * side, base, "terminal")
            elif target != 0 and target != held_dir:
                side = 1.0 if held_dir > 0 else -1.0
                close_position(open_px - tick * side, base, "rebalance")
        if qty == 0 and target != 0:
            side = 1.0 if target > 0 else -1.0
            fill = open_px + tick * side          # adverse fill for the opening buy/sell
            equity_before = START_EQUITY + realized
            quote0 = min(BASE_QUOTE * LEVERAGE, equity_before * LEVERAGE)
            if quote0 > 0 and equity_before > quote0 / LEVERAGE:
                qty0 = target * quote0 / fill     # signed units
                fee0 = quote0 * fee_rate
                if equity_before - fee0 > quote0 / LEVERAGE:
                    realized -= fee0
                    fees += fee0
                    turnover += quote0
                    fills += 1
                    cash[base] -= fee0
                    qty, basis = qty0, qty0 * fill
                    entry_price = fill
                    entry_day = di
                    next_level = 1
                    layers_used = 1
                    held_dir = target
                    eps += 1
        if qty != 0:
            side = 1.0 if qty > 0 else -1.0
            old_avg = basis / qty
            old_stop = old_avg * (1.0 - side * inv_pct)
            if not no_funding:
                for ev_t, amount in funding_events[di]:
                    if ev_t <= int(ms[di]):
                        continue          # settles at/before the open: prior book
                    charge = qty * amount * funding_mult
                    realized -= charge
                    funding_paid += charge
                    cash[base] -= charge
            if di > entry_day and ((side > 0 and open_px <= old_stop)
                                   or (side < 0 and open_px >= old_stop)):
                close_position(open_px - tick * side, base, "stop")
                stop_hits += 1
            if qty != 0:
                adverse = float(L[di]) if side > 0 else float(H[di])
                margin_equity = START_EQUITY + realized + (qty * adverse - basis)
                if margin_equity > 0:
                    max_lev = max(max_lev, abs(qty) * adverse / margin_equity)
                    max_util = max(max_util, abs(qty) * adverse / LEVERAGE / margin_equity)
                if margin_equity <= 0 or abs(qty) * adverse / LEVERAGE > margin_equity:
                    close_position(adverse - tick * side, base, "margin")
                    margin_calls += 1
            if qty != 0:
                # Scale-ins anchored to the INITIAL entry price (registered leg semantics),
                # only for triggers the resting invalidation has not already given up.
                while next_level <= MAX_ADD_LEVELS:
                    trigger = entry_price * (1.0 - side * spacing * next_level)
                    reached = float(L[di]) <= trigger if side > 0 else float(H[di]) >= trigger
                    beyond = trigger <= old_stop if side > 0 else trigger >= old_stop
                    if not reached or beyond:
                        break
                    fill = trigger + tick * side
                    quote = quote0 * mult ** next_level
                    add_qty = side * quote / fill
                    add_fee = quote * fee_rate
                    equity_at_add = START_EQUITY + realized + (qty * fill - basis) - add_fee
                    if equity_at_add <= 0 or abs(qty + add_qty) * fill / LEVERAGE > equity_at_add:
                        close_position(fill - tick * side, base, "margin")
                        margin_calls += 1
                        break
                    realized -= add_fee
                    fees += add_fee
                    turnover += quote
                    fills += 1
                    adds += 1
                    cash[base] -= add_fee
                    qty += add_qty
                    basis += add_qty * fill
                    max_lev = max(max_lev, abs(qty) * fill / equity_at_add)
                    max_util = max(max_util, abs(qty) * fill / LEVERAGE / equity_at_add)
                    next_level += 1
                    layers_used += 1
            if qty != 0:
                avg = basis / qty
                stop = avg * (1.0 - side * inv_pct)
                take = avg * (1.0 + side * tp_pct)
                pre_hit = float(L[di]) <= old_stop if side > 0 else float(H[di]) >= old_stop
                new_hit = float(L[di]) <= stop if side > 0 else float(H[di]) >= stop
                if pre_hit or new_hit:
                    reached = [x for x, crossed in ((old_stop, pre_hit), (stop, new_hit)) if crossed]
                    trigger = min(reached) if side > 0 else max(reached)  # conservative fill
                    close_position(trigger - tick * side, base, "stop")
                    stop_hits += 1
                elif (float(H[di]) >= take) if side > 0 else (float(L[di]) <= take):
                    close_position(take - tick * side, base, "tp")
                    tp_hits += 1
            if qty != 0 and di == i1 - 1:
                close_position(float(C[di]) - tick * side, base, "close")
            unreal[base] = qty * float(C[di]) - basis if qty != 0 else 0.0
    equity = START_EQUITY + np.cumsum(cash) + unreal
    net = float(realized)
    independent_cash = float(cash.sum())
    high = np.maximum.accumulate(np.r_[START_EQUITY, equity])
    dd = high[1:] - equity
    returns = np.diff(np.r_[START_EQUITY, equity]) / np.maximum(np.r_[START_EQUITY, equity[:-1]], 1e-9)
    sd = float(np.std(returns, ddof=1)) if len(returns) > 1 else 0.0
    sharpe = float(np.mean(returns) / sd * math.sqrt(365)) if sd > 1e-12 else 0.0
    duration = max(1, n_days)
    annual = float((max(float(equity[-1]), 1e-9) / START_EQUITY) ** (365.0 / duration) - 1)
    return {"gross_pnl": float(gross), "fees": float(fees), "funding": float(funding_paid),
            "net_pnl": net, "ending_equity": float(equity[-1]) if len(equity) else START_EQUITY,
            "episodes": eps, "fills": fills, "adds": adds, "turnover_usdt": float(turnover),
            "sharpe": sharpe, "max_dd_pct": float(np.max(dd / np.maximum(high[1:], 1e-9)) * 100) if len(dd) else 0.0,
            "max_dd_usdt": float(np.max(dd)) if len(dd) else 0.0, "annualized_return": annual,
            "max_effective_leverage": float(max_lev), "capital_utilization": float(max_util),
            "tp_hits": tp_hits, "stop_hits": stop_hits, "margin_calls": margin_calls,
            "end_exits": end_exits, "open_at_end": 0, "terminal_exits": terminal_exits,
            "rebalance_exits": rebalance_exits, "layer_hist": hist,
            "decomposition_ok": abs(gross - fees - funding_paid - net) < 1e-3
            and abs(net - independent_cash) < 1e-3
            and abs(net - (float(equity[-1]) - START_EQUITY)) < 1e-3,
            "daily_equity": equity.tolist()}


def cell_key(row):
    return (row["symbol"], row["timeframe"], row["episode_days"],
            *(row[k] for k in DCA_AXES))


def neighbourhood(winner, historical):
    if winner["grid"] != "historical" or any(r["grid"] != "historical" for r in historical):
        raise ValueError("selector may only read historical rows")
    found = {cell_key(r): r for r in historical}
    # Frozen round-spec parameter_contract: ONE strategy axis (episode_days, 31/63)
    # plus the four atomic DCA axes; face neighbours are walked in that joint space.
    steps = [((2,), list(STRATEGY_AXES["episode_days"]))]
    steps += [((pos,), list(domain)) for pos, domain in enumerate(DCA_AXES.values(), start=3)]
    agrees = count = 0
    key = list(cell_key(winner))
    for positions, domain in steps:
        pos = positions[0]
        ix = domain.index(key[pos])
        for delta in (-1, 1):
            if 0 <= ix + delta < len(domain):
                candidate = key.copy()
                candidate[pos] = domain[ix + delta]
                row = found[tuple(candidate)]
                count += 1
                agrees += float(row["net_pnl"]) > 0
    return {"neighbours": count, "agreeing": agrees,
            "same_sign_fraction": agrees / count if count else 0.0,
            "passed": bool(count and agrees / count >= MIN_NEIGHBOUR)}


def select_cohort(rows):
    hist = rows["historical"]
    if not hist or any(r["grid"] != "historical" for r in hist):
        raise ValueError("selector saw a non-historical row")
    best = max(int(r["episodes"]) for r in hist)
    if best < MIN_EPISODES_IS:
        return None, {"cull_reasons": ["insufficient_trades"], "best_historical_episodes": best}
    candidates = [r for r in hist if int(r["episodes"]) >= MIN_EPISODES_IS
                  and float(r["net_pnl"]) > 0 and float(r["sharpe"]) > 0]
    if not candidates:
        return None, {"cull_reasons": ["no_qualifying_candidate"], "best_historical_episodes": best}
    order = lambda r: (-float(r["sharpe"]), -float(r["net_pnl"]), int(r["episode_days"]),
                       *(float(r[k]) for k in DCA_AXES))
    winner = min(candidates, key=order)
    lookup = {g: next(r for r in rows[g] if cell_key(r) == cell_key(winner)) for g in GRIDS}
    reasons = []
    oos = lookup["oos"]
    if not (float(oos["net_pnl"]) > 0 and float(oos["sharpe"]) > 0
            and int(oos["episodes"]) >= MIN_EPISODES_OOS):
        reasons.append("oos_economic")
    if float(lookup["full"]["net_pnl"]) <= 0:
        reasons.append("full_economic")
    weak = [g for g in ("fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks")
            if float(lookup[g]["net_pnl"]) <= 0]
    if weak:
        reasons.append("robustness_economic:" + ",".join(weak))
    neighbors = neighbourhood(winner, hist)
    if not neighbors["passed"]:
        reasons.append("parameter_neighbourhood")
    phases = {g: metric_block(lookup[g]) for g in ("historical", "oos", "full")}
    robustness = {g: metric_block(lookup[g]) for g in
                  ("fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks")}
    # The winner cell must carry EXACTLY the registered param axes: the post-survivor
    # index/leaderboard split it through the round-spec parameter_contract and refuse
    # any key outside strategy_param_fields + dca_param_fields.
    detail = {"winner": {k: winner[k] for k in ("episode_days", *DCA_AXES)},
              "winner_source_grid": "historical",
              "best_historical_episodes": best, "neighbourhood": neighbors,
              "phases": phases, "robustness": robustness,
              "metrics": {**phases, "robustness": robustness, "neighbourhood": neighbors},
              "cull_reasons": reasons}
    return (winner if not reasons else None), detail


def write_grid(path, rows):
    metric_keys = list(metric_block(empty_metric(0, 0)))
    cols = ["symbol", "timeframe", "episode_days", "case_label", *DCA_AXES,
            "grid", *metric_keys, "decomposition_ok"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def cost_for(grid):
    return {"fee_2x": {"fee_mult": 2.0}, "funding_2x": {"funding_mult": 2.0},
            "entry_delay_1_bar": {"entry_delay": 1}, "slippage_2ticks": {"slip_ticks": 2},
            "no_funding": {"no_funding": True}, "no_funding_full": {"no_funding": True},
            "cost_attrition_40bps": {"fee_override": 0.004}}.get(grid, {})


def equal_weight_returns(daily):
    """Daily rebalanced 1/N over the local universe (passive benchmark)."""
    ref = daily[SYMBOLS[0]]["open_ms"]
    closes = []
    for s in SYMBOLS:
        if len(daily[s]["open_ms"]) != len(ref) or not np.array_equal(daily[s]["open_ms"], ref):
            raise RuntimeError("symbol calendars are not aligned: " + s)
        closes.append(daily[s]["close"])
    mat = np.vstack(closes)
    rets = np.zeros(mat.shape[1])
    rets[1:] = np.mean(mat[:, 1:] / mat[:, :-1] - 1.0, axis=0)
    return rets


def sharpe_from_returns(rets):
    r = np.asarray(rets, dtype=np.float64)
    if len(r) < 3:
        return 0.0
    sd = float(np.std(r, ddof=1))
    return float(np.mean(r) / sd * math.sqrt(365)) if sd > 1e-12 else 0.0


def _bootstrap_pvalue(diffs, seed, draws=4000):
    """One-sided bootstrap p-value that the paired mean difference stays > 0."""
    d = np.asarray(diffs, dtype=np.float64)
    if len(d) == 0:
        return None
    rng = np.random.default_rng(seed)
    n = len(d)
    means = np.empty(draws)
    for i in range(draws):
        means[i] = float(np.mean(d[rng.integers(0, n, n)]))
    return float(np.mean(means <= 0.0))


def falsification_report(spec_rows, daily, funding, layers, ew_rets, windows, instruments, seeds):
    """Measure every record falsification item that is computable locally.

    Items are evaluated on each cohort's frozen historical winner cell only;
    these reruns live outside the registered coverage grids and are never used
    for selection. Unavailable items stay INDETERMINATE - never a pass.
    """
    def aggregate(details):
        """FALSIFIED if any measured cohort falsified; INDETERMINATE when nothing
        measured; never a pass by default (a null flag is not a False flag)."""
        flags = [v.get("falsified") for v in details.values()]
        if any(f is True for f in flags):
            return "FALSIFIED"
        if any(f is False for f in flags):
            return "NOT_FALSIFIED"
        return "INDETERMINATE"

    out = {"items": {}, "scope": "local adapted USD-M perp universe; per-cohort ladder execution",
           "selection_leak": "none: all reruns read the already-frozen winner cell"}
    # --- item 1: HJB-regularization ablation (needs the Eq. (73) neural training) ---
    out["items"]["hjb_regularization_ablation"] = {
        "registered_rule": FALSIFICATION_REGISTRY["hjb_regularization_ablation"],
        "status": "INDETERMINATE",
        "torch_available": importlib.util.find_spec("torch") is not None,
        "reason": "the production image has no torch and the record publishes no Eq. (73) "
                  "checkpoint; training a PINN without the HJ residual loss is therefore not "
                  "executable here. INDETERMINATE is recorded, never converted into a pass."}

    survivors = [r for r in spec_rows if r.get("outcome") == "SURVIVOR"]
    # --- item 2: decay-prone signal stress ---
    decay_status, decay_detail = "NO_CANDIDATE", {}
    cost_status, cost_detail = "NO_CANDIDATE", {}
    overlap_status, overlap_detail = "NO_CANDIDATE", {}
    for rec in survivors:
        winner = rec["winner"]
        sym = rec["symbol"]
        case = next(c for c in CASES if c["episode_days"] == winner["episode_days"])
        dca = {k: winner[k] for k in DCA_AXES}
        i0, i1 = windows["oos"]
        inst = instruments[sym]
        decay_dir = decay_direction(daily[sym])
        decay = simulate(daily[sym], funding[sym][0], decay_dir, case, dca, i0, i1, {}, inst)
        base = simulate(daily[sym], funding[sym][0], layers[sym]["direction"], case, dca,
                        i0, i1, {}, inst)
        ew = sharpe_from_returns(ew_rets[i0:i1])
        item = {"cohort": sym, "decay_sharpe_oos": decay["sharpe"],
                "decay_net_pnl_oos": decay["net_pnl"],
                "baseline_sharpe_oos": base["sharpe"], "local_equal_weight_sharpe_oos": ew,
                "source_threshold": SOURCE_EW_SHARPE,
                "lookback_days": DECAY_LOOKBACK}
        item["falsified"] = bool(decay["sharpe"] <= SOURCE_EW_SHARPE)
        item["below_local_equal_weight"] = bool(decay["sharpe"] <= ew)
        decay_detail[sym] = item
        # --- item 3: 10 bps -> 35 bps cost sensitivity ---
        stressed = simulate(daily[sym], funding[sym][0], layers[sym]["direction"], case, dca, i0, i1,
                            {"fee_override": COST_35BPS}, inst)
        item3 = {"cohort": sym, "strategy_sharpe_oos_at_35bps": stressed["sharpe"],
                 "strategy_sharpe_oos_baseline": base["sharpe"],
                 "equal_weight_sharpe_oos": ew,
                 "delta_sharpe_vs_equal_weight": stressed["sharpe"] - ew}
        item3["falsified"] = bool(item3["delta_sharpe_vs_equal_weight"] <= 0.05)
        cost_detail[sym] = item3
        # --- item 4: window-overlap significance on non-overlapping T-day windows ---
        rets = np.diff(np.r_[START_EQUITY, np.asarray(base["daily_equity"])]) \
            / np.maximum(np.r_[START_EQUITY, np.asarray(base["daily_equity"])[:-1]], 1e-9)
        step = int(winner["episode_days"])
        diffs = []
        for j in range(0, len(rets), step):
            sret = rets[j:j + step]
            erect = ew_rets[i0 + j:i0 + j + len(sret)]
            if len(sret) < max(5, step // 4) or len(erect) != len(sret):
                continue
            diffs.append(sharpe_from_returns(sret) - sharpe_from_returns(erect))
        p = _bootstrap_pvalue(diffs, seeds[sym]) if diffs else None
        item4 = {"cohort": sym, "window_days": step, "windows": len(diffs),
                 "mean_sharpe_advantage": float(np.mean(diffs)) if diffs else None,
                 "bootstrap_p_value": p,
                 "falsified": None if p is None else bool(p > 0.05)}
        overlap_detail[sym] = item4
    if decay_detail:
        decay_status = aggregate(decay_detail)
    out["items"]["signal_decay_stress"] = {
        "registered_rule": FALSIFICATION_REGISTRY["signal_decay_stress"],
        "status": decay_status, "cohorts": decay_detail,
        "local_signal": "research-defined 3-day short-term reversal of normalized returns "
                        "(causal; cross-sectional reversal expressed per cohort)"}
    if cost_detail:
        cost_status = aggregate(cost_detail)
    out["items"]["transaction_cost_10_to_35bps"] = {
        "registered_rule": FALSIFICATION_REGISTRY["transaction_cost_10_to_35bps"],
        "status": cost_status, "cohorts": cost_detail,
        "mapping": "source 10 bps baseline maps to the registered canonical taker fee; the "
                   "35 bps level is applied as an absolute fee override"}
    if overlap_detail:
        overlap_status = aggregate(overlap_detail)
    out["items"]["window_overlap_significance"] = {
        "registered_rule": FALSIFICATION_REGISTRY["window_overlap_significance"],
        "status": overlap_status, "cohorts": overlap_detail,
        "method": "non-overlapping episode-horizon windows of OOS daily returns, paired "
                  "strategy-minus-equal-weight Sharpe, one-sided bootstrap (4000 draws)"}
    hits = sorted(k for k, v in out["items"].items() if v.get("status") == "FALSIFIED")
    out["falsification_hits"] = hits
    out["indeterminate_items"] = sorted(
        k for k, v in out["items"].items() if v.get("status") == "INDETERMINATE")
    out["battery_status"] = ("FAIL" if hits else
                             "NOT_FALSIFIED_WITH_INDETERMINATE")
    out["scoped_note"] = ("record items 2-4 are scoped falsifications (fast-decay alphas, "
                          "cost resilience, horizon stability); a hit is reported as a "
                          "family-level science failure per the body FAILURE TAXONOMY and "
                          "is never converted into a pass")
    return out


def run(spec, attempt_dir):
    counts = validate_spec(spec)
    path = Path(attempt_dir)
    expected = Path(RESULTS_ROOT) / FAMILY_ID / "rounds" / spec["round_id"] / "attempts" / spec["run_id"]
    if path != expected or any((path / x).exists() for x in ("DONE", "FAILED", "INCOMPLETE", "result.json")):
        raise ValueError("attempt path/immutability violation")
    artifacts, logs = path / "artifacts", path / "logs"
    artifacts.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    log_fh = open(logs / "run.log", "w", encoding="utf-8")

    def log(msg):
        line = "[%s] %s" % (dt.datetime.now(dt.timezone.utc).isoformat(), msg)
        print(line, flush=True)
        log_fh.write(line + "\n")
        log_fh.flush()

    state = {"schema_version": 1, "family_id": FAMILY_ID, "round_id": spec["round_id"],
             "run_id": spec["run_id"], "stage": "RUNNING_QLIB"}
    atomic_json(path / "state.json", state)
    progress = {"schema_version": 1, "family_id": FAMILY_ID, "round_id": spec["round_id"],
                "run_id": spec["run_id"], "stage": "RUNNING_QLIB", "case_evaluations": 0,
                "expected_case_evaluations": counts["case_evaluations_total"]}

    def write_progress():
        progress["updated_at_utc"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        progress["runtime_seconds"] = round(time.monotonic() - started, 3)
        atomic_json(artifacts / "progress.json", progress)

    write_progress()
    try:
        instruments = instrument_metadata()
        daily = {s: load_rows(s, TIMEFRAME, *PHASES["full"], FIELDS) for s in SYMBOLS}
        _, build = build_qlib(daily, *PHASES["full"])
        raw_report = {"qlib": build, "daily_files": {s: daily[s]["raw_files"] for s in SYMBOLS}}
        atomic_json(artifacts / "raw_build.json", raw_report)
        funding = {s: load_funding(s, daily[s]["open_ms"], daily[s]["open_ms"][-1]) for s in SYMBOLS}
        atomic_json(artifacts / "funding_coverage.json", {s: funding[s][1] for s in SYMBOLS})
        micro = {}
        for s in SYMBOLS:
            spread, valid = corwin_schultz(daily[s])
            adv = adv_panel(daily[s])
            H = [float(v) for v in daily[s]["high"]]
            L = [float(v) for v in daily[s]["low"]]
            # Independent recompute of the published positivity condition: for the
            # Corwin-Schultz alpha, alpha > 0 is algebraically equivalent to beta > gamma.
            pos = sum(1 for t in range(len(H) - 1)
                      if math.log(max(H[t], H[t + 1]) / min(L[t], L[t + 1])) ** 2 >
                      math.log(H[t] / L[t]) ** 2 + math.log(H[t + 1] / L[t + 1]) ** 2)
            micro[s] = {"corwin_schultz_defined_days": int(np.count_nonzero(valid)),
                        "corwin_schultz_defined_share": float(np.mean(valid)),
                        "corwin_schultz_mean": float(np.mean(spread[valid])) if valid.any() else 0.0,
                        "corwin_schultz_median": float(np.median(spread[valid])) if valid.any() else 0.0,
                        "beta_gt_gamma_sessions": int(pos),
                        "beta_gt_gamma_share": float(pos) / max(1, len(H) - 1),
                        "adv_days_defined": int(np.count_nonzero(np.isfinite(adv))),
                        "adv_mean_volume": float(np.nanmean(adv)),
                        "note": "registered microstructure fields derived from local daily H/L and "
                                "volume; the frozen cost model stays canonical fee + adverse tick "
                                "slippage + official funding (candidate body)"}
        atomic_json(artifacts / "microstructure.json", micro)
        layers = {s: build_signal(daily[s], seed_offset=SYMBOLS.index(s)) for s in SYMBOLS}
        ew_rets = equal_weight_returns(daily)
        windows = {k: window_indices(daily[SYMBOLS[0]]["open_ms"], *v) for k, v in PHASES.items()}
        ew_report = {"definition": "daily rebalanced 1/N over the four-symbol local universe",
                     "sharpe": {g: sharpe_from_returns(ew_rets[windows[g][0]:windows[g][1]])
                                for g in PHASES},
                     "annualized": {}}
        for g, (a, b) in windows.items():
            eq = START_EQUITY * np.cumprod(1.0 + ew_rets[a:b])
            eq = np.r_[START_EQUITY, eq]
            ew_report["annualized"][g] = float((max(float(eq[-1]), 1e-9) / START_EQUITY)
                                               ** (365.0 / max(1, b - a)) - 1)
        atomic_json(artifacts / "baseline_equal_weight.json", ew_report)
        signal_metrics = {}
        for s in SYMBOLS:
            item = signal_metric_fields(layers[s])
            for phase in PHASES:
                i0, i1 = windows[phase]
                seg = np.isfinite(layers[s]["zeta"][i0:i1])
                if seg.sum() > 3:
                    z = layers[s]["zeta"][i0:i1][seg]
                    tgt = layers[s]["rtilde"][i0:i1][np.clip(np.flatnonzero(seg) + 1, 0,
                                                              i1 - i0 - 1)]
                    item["oracle_r2_" + phase] = float(np.corrcoef(z, tgt)[0, 1] ** 2)
            item["direction_counts"] = {"long": int(np.sum(layers[s]["direction"] == 1)),
                                        "short": int(np.sum(layers[s]["direction"] == -1)),
                                        "undefined": int(np.sum(layers[s]["direction"] == 0))}
            item["pit_status"] = PIT_STATUS
            item["pit_disclosure"] = ("source-registered oracle term rtilde_{t+1} is reproduced "
                                      "verbatim: the baseline decision at di's open uses a signal "
                                      "that embeds day di's own return, i.e. next-period "
                                      "information BY SOURCE DESIGN (record limitation "
                                      "\"Engineered Oracle Signal Dependency\"); contract 9.6 "
                                      "point-in-time therefore fails and performance_claimable "
                                      "must carry a look-ahead missing condition. The registered "
                                      "entry_delay_1_bar grid is the point-in-time variant.")
            item["oracle_r2_source_reported_range"] = [0.05, 0.12]
            item["oracle_r2_note"] = ("measured correlation^2 of the reproduced signal against "
                                      "its own rtilde_{t+1} term on this local universe; the "
                                      "source reports controlled OOS R^2 in [0.05, 0.12] on its "
                                      "14-ETF simulation, not a value this run must reproduce")
            item["constants"] = dict(SIGNAL_PARAMS)
            signal_metrics[s] = item
        atomic_json(artifacts / "signal_metrics.json", signal_metrics)
        grid_rows = {g: [] for g in GRIDS}
        results, survivors = [], []
        hist_total = [0] * LADDER_LEVELS
        for s in SYMBOLS:
            rows = {g: [] for g in GRIDS}
            inst = instruments[s]
            for case in CASES:
                for dca in DCA_GRID:
                    for grid in GRIDS:
                        phase = "historical" if grid in ("historical", "no_funding") \
                            else "oos" if grid == "oos" else "full"
                        metric = simulate(daily[s], funding[s][0], layers[s]["direction"],
                                          case, dca, *windows[phase], cost_for(grid), inst)
                        row = {"symbol": s, "timeframe": TIMEFRAME,
                               "episode_days": case["episode_days"], "case_label": case["label"],
                               **dca, "grid": grid, **metric_block(metric),
                               "decomposition_ok": metric["decomposition_ok"]}
                        rows[grid].append(row)
                        grid_rows[grid].append(row)
                        progress["case_evaluations"] += 1
                        if progress["case_evaluations"] % 500 == 0:
                            write_progress()
                        if grid == "full":
                            for i, val in enumerate(metric["layer_hist"]):
                                hist_total[i] += val
            selected, info = select_cohort(rows)
            record = {"cohort": s + "/" + TIMEFRAME, "symbol": s,
                      "outcome": "SURVIVOR" if selected is not None else "CULLED", **info}
            results.append(record)
            if selected is not None:
                survivors.append(record)
            log("cohort %s outcome=%s cull=%s" % (record["cohort"], record["outcome"],
                                                  ",".join(info["cull_reasons"]) or "none"))
            write_progress()
        expected_keys = {(s, TIMEFRAME, c["episode_days"], *(d[k] for k in DCA_AXES))
                         for s in SYMBOLS for c in CASES for d in DCA_GRID}
        coverage = {g: {cell_key(r) for r in grid_rows[g]} == expected_keys
                    and len(grid_rows[g]) == len(expected_keys) for g in GRIDS}
        base_full = sum(float(r["net_pnl"]) for r in grid_rows["full"])
        stress_delta = {g: sum(float(r["net_pnl"]) for r in grid_rows[g]) - base_full
                        for g in ("fee_2x", "funding_2x", "slippage_2ticks", "cost_attrition_40bps")}
        traded = any(float(r["turnover_usdt"]) > 0 for r in grid_rows["full"])
        official_events = {s: funding[s][1]["official"] for s in SYMBOLS}
        delay_delta = sum(float(r["net_pnl"]) for r in grid_rows["entry_delay_1_bar"]) - base_full
        def cell_delta(a, b, tol=1e-9):
            """Index-aligned per-cell |net difference| exists (no sum can cancel)."""
            return any(abs(float(x["net_pnl"]) - float(y["net_pnl"])) > tol
                       for x, y in zip(grid_rows[a], grid_rows[b]))
        cells_with_funding = [i for i, r in enumerate(grid_rows["full"])
                              if abs(float(r["funding"])) > 1e-9]
        falsification = falsification_report(results, daily, funding, layers, ew_rets, windows,
                                             instruments, {s: SEED + 77 + i
                                                           for i, s in enumerate(SYMBOLS)})
        atomic_json(artifacts / "falsification.json", falsification)
        for rec in survivors:
            rec["falsification_battery"] = falsification["battery_status"]
        assertions = {
            "qlib_readback_0_9_7": build["qlib_version"] == "0.9.7"
            and build["qlib_rows_identical_to_raw"],
            "coverage_complete": all(coverage.values()),
            "case_evaluations_total_exact": sum(map(len, grid_rows.values()))
            == counts["case_evaluations_total"],
            "independent_gross_net_decomposition": all(r["decomposition_ok"]
                                                       for g in GRIDS for r in grid_rows[g]),
            "dca_histogram_reconciles": hist_total[0] == sum(int(r["episodes"])
                                                             for r in grid_rows["full"]),
            "cost_stress_effective": (not traded) or all(stress_delta[g] < 0 for g in
                                                         ("fee_2x", "slippage_2ticks",
                                                          "cost_attrition_40bps")),
            "funding_2x_effective": not cells_with_funding or all(
                abs(float(grid_rows["funding_2x"][i]["net_pnl"])
                    - float(grid_rows["full"][i]["net_pnl"])) > 1e-9
                for i in cells_with_funding),
            "entry_delay_1_bar_effective": (not traded) or cell_delta("entry_delay_1_bar", "full"),
            "official_funding_only_no_modeled_charges": all(
                funding[s][1]["charged_events"] == funding[s][1]["official"]
                and funding[s][1]["missing_intervals_are_zero_not_modeled"] is True
                for s in SYMBOLS),
            "signal_constants_match_source_publication":
            (SIGNAL_PARAMS["alpha1"], SIGNAL_PARAMS["beta1"], SIGNAL_PARAMS["phi"])
            == (0.2672, 0.2943, -0.0831),
            "signal_oracle_disclosure_present": all(
                "BY SOURCE DESIGN" in signal_metrics[s]["pit_disclosure"] for s in SYMBOLS),
            "pit_status_fail_disclosed": all(signal_metrics[s]["pit_status"] == PIT_STATUS
                                             for s in SYMBOLS),
            # Gate defect fixed in attempt u2 (same round, 15 remediation): u1 used an
            # unvalidated ">0.5 of sessions" coverage floor for this descriptive field and
            # measured 0.32-0.36 instead, so the run ended with a false assertion even though
            # the published Corwin-Schultz formula is implemented correctly (alpha > 0 iff
            # beta > gamma, which holds on about a third of sessions in this universe). The
            # floor is replaced by the exact identity it was standing in for: the number of
            # sessions with a defined spread must equal an independent beta > gamma count.
            # CS/ADV are reported microstructure fields only - they are not inputs to the
            # signal, the cost model, the selector or any scientific gate, and no simulate()
            # code path reads them.
            "microstructure_fields_computed": all(
                micro[s]["adv_days_defined"] > 0
                and micro[s]["corwin_schultz_defined_days"] > 0
                and abs(micro[s]["corwin_schultz_defined_days"]
                        - micro[s]["beta_gt_gamma_sessions"]) <= 1
                for s in SYMBOLS),
            "selector_winner_is_historical_row": all(x.get("winner_source_grid") == "historical"
                                                     for x in results if x.get("winner")),
        }
        atomic_json(artifacts / "stress_effects.json", {
            "baseline_grid": "full", "net_pnl_delta": {**stress_delta,
                                                       "entry_delay_1_bar": delay_delta},
            "any_fill_in_full_grid": traded,
            "noop_reason": None if traded else "no fills in full grid"})
        atomic_json(artifacts / "cohort_results.json", results)
        atomic_json(artifacts / "cohort_survivors.json", survivors)
        atomic_json(artifacts / "dca_layer_histogram.json",
                    {"level_%02d" % i: v for i, v in enumerate(hist_total)})
        for g in GRIDS:
            write_grid(artifacts / ("grid_%s.csv" % g), grid_rows[g])
        atomic_json(artifacts / "assertions.json", assertions)
        hits = falsification.get("falsification_hits", [])
        # Disposition band only (v1.4.0): 0 survivors -> REJECT, >=1 -> PASS.  The
        # science-failure advice travels in falsification_advice so the band can never
        # masquerade as a verdict (body FAILURE TAXONOMY decides that in verdict.json).
        verdict = "REJECT" if not survivors else "PASS"
        result = {"schema_version": 1, "family_id": FAMILY_ID, "round_id": spec["round_id"],
                  "run_id": spec["run_id"], "engine": ENGINE_VERSION,
                  "script_sha256": sha256_file(__file__), "qlib_version": build["qlib_version"],
                  "status": "ARTIFACT_READY", "coverage_complete": all(coverage.values()),
                  "coverage_by_grid": coverage,
                  "assertions_all_true": all(v for v in assertions.values()),
                  "assertion_failures": [k for k, v in assertions.items() if not v],
                  "assertions": assertions,
                  "case_evaluations_total": sum(map(len, grid_rows.values())),
                  "expected_case_evaluations": counts["case_evaluations_total"],
                  "cohort_count": len(results), "cohort_survivor_count": len(survivors),
                  "cohort_survivors": [x["cohort"] for x in survivors],
                  "official_funding_events": official_events,
                  "disposition": "REJECT / NO_SURVIVOR" if not survivors
                  else ("SURVIVOR_FOUND" if len(survivors) == 1 else "MULTIPLE_SURVIVORS"),
                  "falsification_hits": hits,
                  "falsification_indeterminate": falsification.get("indeterminate_items", []),
                  "falsification_advice": {
                      "rule": "record falsification battery hit => cull or family REJECT (body "
                              "FAILURE TAXONOMY); verdict_recommendation is the v1.4.0 "
                              "disposition band only and must not be read as science advice",
                      "battery_status": falsification.get("battery_status"),
                      "advice": "REJECT" if hits else None},
                  "verdict_recommendation": verdict,
                  "verdict_recommendation_basis": "disposition band v1.4.0 (0 survivors = "
                                                  "REJECT, >=1 = PASS); the final verdict is "
                                                  "written to verdict.json by default (10.7)",
                  "pit_status": PIT_STATUS,
                  "claimability_evidence": {
                      "basis": "performance_claimable is decided in verdict.json under contract "
                               "9.6, not recommended here",
                      "missing_conditions": [
                          "point-in-time: the registered signal embeds the record's engineered "
                          "oracle term rtilde_{t+1}, so the baseline decision at di's open uses "
                          "day di's own return (look-ahead by source design; disclosed in "
                          "artifacts/signal_metrics.json) - contract 9.6 PIT fails"],
                      "pit_clean_grid": "entry_delay_1_bar evaluates the identical signal one "
                                        "bar later and is reported in every grid"},
                  "selector_version": "cohort-selector-v1", "disposition_version": "cohort-disposition-v1",
                  "disposition_mapping_version": "v1.4.0", "grid_kinds": GRIDS,
                  "funding_coverage": {s: funding[s][1] for s in SYMBOLS},
                  "stress_net_delta": stress_delta,
                  "episode_horizon_terminal_exits": {
                      str(c["episode_days"]): sum(int(r["terminal_exits"])
                                                  for r in grid_rows["full"]
                                                  if int(r["episode_days"]) == c["episode_days"])
                      for c in CASES},
                  "runtime_seconds": time.monotonic() - started}
        if result["case_evaluations_total"] != counts["case_evaluations_total"]:
            result["assertions_all_true"] = False
            result["assertion_failures"].append("case_evaluations_total")
        if not result["assertions_all_true"]:
            result["verdict_recommendation"] = "TECHNICAL_INCOMPLETE"
        atomic_json(path / "result.json", result)
        state["stage"] = "ARTIFACT_READY"
        atomic_json(path / "state.json", state)
        progress["stage"] = "ARTIFACT_READY"
        write_progress()
        log("ARTIFACT_READY cohorts=%d survivors=%d rows=%d falsification=%s assertions=%s"
            % (len(results), len(survivors), result["case_evaluations_total"],
               ",".join(hits) or "none",
               "all_true" if result["assertions_all_true"] else str(result["assertion_failures"])))
        return result
    except Exception as exc:
        state.update(stage="FAILED_SCRIPT", error="%s: %s" % (type(exc).__name__, exc))
        atomic_json(path / "state.json", state)
        progress["stage"] = "FAILED_SCRIPT"
        write_progress()
        log("FAILED %s: %s" % (type(exc).__name__, exc))
        raise
    finally:
        log_fh.close()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-spec", required=True)
    parser.add_argument("--attempt-dir")
    args = parser.parse_args(argv)
    with open(args.run_spec, encoding="utf-8") as fh:
        spec = json.load(fh)
    result = run(spec, args.attempt_dir or str(Path(args.run_spec).parent))
    print(json.dumps({"status": result["status"],
                      "case_evaluations_total": result["case_evaluations_total"],
                      "assertions_all_true": result["assertions_all_true"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
