#!/usr/bin/env python3
"""Direct-family Qlib runner: asymmetric volatility + coherent tail-risk allocation.

Registered hypothesis, record excerpts and the frozen candidate body live in the
family round-spec; this engine implements only what they require.

Registered mechanism (source = canonical wiki record, excerpted in the candidate body):
    1. GJR-GARCH(1,1)-t conditional variance per symbol,
           sigma_t^2 = omega + alpha*e_{t-1}^2 + gamma*I(e_{t-1}<0)*e_{t-1}^2
                       + beta*sigma_{t-1}^2,   e_t/sigma_t ~ standardized Student-t(nu),
       volatility persistence alpha + beta + gamma/2 (record eq. for asymmetry gamma).
    2. Hill tail index on ordered left-tail losses with the record's threshold
       m = floor(n^0.5):  alpha_hat = { (1/k) * sum_{i<=k} (ln L_(i) - ln L_(k+1)) }^-1.
    3. Long memory d on squared returns via GPH log-periodogram regression and the
       Gaussian semiparametric (local Whittle) estimator.
    4. Rockafellar-Uryasev CVaR allocation for alpha in {0.05, 0.01} against the
       Minimum-Variance Portfolio, under the record's two constraint regimes:
       LO  = short_budget 0.00 (w_i >= 0),  LS = short_budget 0.30 (-0.30 <= w_i <= 1.30),
       both with sum(w) = 1. The optimizer runs cross-sectionally over the complete
       local eligible universe every session, point-in-time.

Control mapping (research-defined ladder mapping of the record's allocation rule):
    the sign of the optimized weight is the per-cohort direction; a weight inside the
    registered deadband is FLAT. The signal is computed at day t's close from data
    strictly through day t and executed at day t+1's open (record: "Next-day market
    open following close-of-day signal computation") => point-in-time clean, asserted
    by construction and by a perturbation test in the self-check. tranche #1 opens on
    a qualified signal, adverse scale-ins are anchored to the initial entry price,
    breakeven TP and resting invalidation work against running average cost, a
    direction flip or a flat signal flattens the book reduce-only (then FLAT, never
    re-added to until the next qualified signal). The ladder is the frozen 48-config
    DCA rail; per-fill taker fee, adverse tick slippage and official funding are
    charged at their own time, and gross PnL comes from an independent price-PnL
    accumulator checked against net with a negative control.

Not reimplemented (disclosed adaptation boundary, never silently substituted): the
record's 1,008-session rolling estimation window (the local raw window cannot host
it; 252 sessions is registered research-defined), continuous cross-asset rebalancing
of the source's portfolio accounting (the candidate body freezes the tranche ladder
as this family's execution rail), and source-market ETF borrow mechanics (the local
universe is linear USD-M perpetuals, short legs are native).

Execution: daily bars, Qlib 0.9.7 dump/readback proves the daily signal source,
official funding only at its own timestamp/mark (missing intervals are zero and
disclosed, modeled rows are never charged), risk-free rate from the canonical FRED
DGS3MO official series feeds the record's coherent metrics (Rachev / STARR /
Sortino), and the per-cohort ladder Sharpe keeps the pipeline's equity-return
convention (rf = 0) exactly as the other direct families do.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import hashlib
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
from scipy.optimize import linprog, minimize

FAMILY_ID = "taiwan-semiconductor-etf-asymmetric-volatility-cvar-rachev-ratio-2026-09-02"
ENGINE_VERSION = "tail_cvar_gjr_garch_qlib_v1"
RAW_ROOT = "/data/raw"
RESULTS_ROOT = "/results"
WORK_ROOT = "/qlib/work/tail-cvar-v1"
QLIB_DIR = WORK_ROOT + "/qlib-data"
CSV_ROOT = WORK_ROOT + "/csv"
INSTRUMENTS_PATH = RAW_ROOT + "/binance/usdm/instruments/usdm-perp-instruments.json"
RISK_FREE_PATH = RAW_ROOT + "/fred/macro/fred-DGS3MO.jsonl.gz"
VENV_PYTHON = "/opt/venv/bin/python"
DUMP_BIN = "/opt/qlib-tools/dump_bin.py"
MS_DAY = 86_400_000
FIELDS = ("open", "high", "low", "close", "volume")
SYMBOLS = ("BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT")
# The record's crypto-portability section names BTC and ETH as the concentration
# analogue of TSMC/EWT/SMH/SOXX; falsification item 2 removes exactly these.
CONCENTRATION_SYMBOLS = ("BTCUSDT", "ETHUSDT")
TIMEFRAME = "1d"
START_EQUITY = 30_000.0
BASE_QUOTE = 1_000.0
LEVERAGE = 10.0
MAX_ADD_LEVELS = 10          # tranche #1 + 10 scale-ins = 11 routine active levels
LADDER_LEVELS = 12           # tranche #12 stays reserve, never routinely deployed
SEED = 20260902
# Source-specified strategy axes (record: alpha in {0.05, 0.01}; regimes LO / LS with
# -0.30 <= w <= 1.30). short_budget is the registered numeric encoding of the regime.
STRATEGY_AXES = {"cvar_alpha": (0.05, 0.01), "short_budget": (0.0, 0.30)}
CASES = tuple({"case_code": i, "cvar_alpha": a, "short_budget": b,
               "label": ("a%02d_" % round(a * 100)) + ("lo" if b == 0.0 else "ls")}
              for i, (a, b) in enumerate(itertools.product(*STRATEGY_AXES.values())))
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
# Record falsification item 4 asks for a pure down-cycle subsample; the local
# analogue of the 2000-2002 / 2008 equity bust is the 2022 crypto bear market.
DOWN_CYCLE = ("2022-01-01", "2022-12-31")
COST_STRESS_BPS = (0.0010, 0.0025)     # record falsification item 3: 10 bps .. 25 bps
# Registered econometric execution details (record leaves them unspecified locally).
EST_WINDOW = 252
REFIT_EVERY = 21
WEIGHT_DEADBAND = 0.02
T_MIN_NU = 2.01
SIGNAL_PARAMS = {
    "est_window": EST_WINDOW,
    "refit_every": REFIT_EVERY,
    "weight_deadband": WEIGHT_DEADBAND,
    "cvar_alphas": [0.05, 0.01],
    "short_budgets": [0.0, 0.30],
    "gjr_garch_spec": "GJR-GARCH(1,1)-t, standardized Student-t innovations, Gaussian-map MLE",
    "hill_threshold_rule": "m = floor(n^0.5) ordered left-tail losses (record formula)",
    "long_memory": "GPH log-periodogram + Gaussian semiparametric local Whittle on squared returns, m = floor(n^0.5)",
    "execution_timing": "signal at day t close from data through day t, executed at day t+1 open (record execution assumption)",
    "provenance": "source-specified: alpha set {0.05,0.01}, LO/LS regimes with -0.30..1.30, GJR-GARCH-t, Hill threshold; research-defined: 252-session estimation window, 21-session refit cadence, 0.02 weight deadband (the record's 1008-session window does not fit the local raw window)",
}
PIT_STATUS = "PASS_NO_LOOKAHEAD"
FALSIFICATION_REGISTRY = {
    "gaussian_tail_control": "record item 1: replace empirical return innovations with multivariate Gaussian noise calibrated to sample means and covariances; if CVaR optimization continues to generate statistically significant differences in portfolio weights and Rachev ratios versus mean-variance optimization, the tail-specific alpha hypothesis is falsified",
    "concentration_removal": "record item 2: remove the concentration symbols (EWT/SMH/SOXX in the source universe; BTCUSDT/ETHUSDT as the record's own crypto-portability concentration analogue) from the local universe; if the divergence between CVaR and MVP allocations does NOT collapse, the claim that the mechanism depends strictly on concentration is falsified",
    "turnover_cost_stress": "record item 3: impose 10 to 25 bps transaction costs on daily rolling rebalancing; if daily turnover costs erode the cumulative wealth of the long-short CVaR portfolio below a static buy-and-hold benchmark, the strategy is deemed practically unexecutable",
    "subsample_regime_inversion": "record item 4: test the allocation rule across a pure down-cycle subsample; the long-short CVaR portfolio must achieve lower maximum drawdown and higher Sortino and Rachev ratios than both the equally weighted portfolio and MVP to survive falsification",
}
FALSIFICATION = [
    "Gaussian-calibrated tail control: median CVaR-vs-MVP per-asset weight divergence under synthetic Gaussian scenarios >= the empirical divergence falsifies the tail-specific alpha hypothesis (record item 1)",
    "Concentration removal: per-asset median CVaR-vs-MVP divergence without BTCUSDT/ETHUSDT >= the full-universe divergence means no collapse, falsifying the strict concentration-dependence claim (record item 2)",
    "Turnover cost stress: long-short CVaR full-window wealth at 25 bps turnover cost below static 1/N buy-and-hold falsifies practical executability (record item 3)",
    "Subsample regime inversion: long-short CVaR on the 2022 down-cycle window failing any of maxDD <, Sortino >, Rachev > both EWP and MVP falsifies downside dominance (record item 4)",
]
ARTIFACTS = ("state.json", "result.json", "logs/run.log", "artifacts/progress.json",
             "artifacts/raw_build.json", "artifacts/funding_coverage.json",
             "artifacts/risk_free_coverage.json", "artifacts/econometrics.json",
             "artifacts/signal_metrics.json", "artifacts/optimizer_diagnostics.json",
             "artifacts/portfolio_diagnostics.json", "artifacts/baseline_equal_weight.json",
             "artifacts/stress_effects.json", "artifacts/falsification.json",
             "artifacts/dca_layer_histogram.json", "artifacts/cohort_results.json",
             "artifacts/cohort_survivors.json", "artifacts/assertions.json",
             *("artifacts/grid_%s.csv" % g for g in GRIDS))


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
        "script": {"path": "/scripts/280_tail_cvar_run.py", "sha256": sha256_file(here),
                   "deployed_host_path": "/Users/hong/workspace/qlib-apple-container/scripts/280_tail_cvar_run.py"},
        "engine": {"name": ENGINE_VERSION, "script": "container/scripts/280_tail_cvar_run.py",
                   "container": "qlib-run", "qlib_version": "0.9.7", "seed": SEED,
                   "self_check": "container/scripts/tests/test_tail_cvar_engine.py",
                   "self_check_sha256": sha256_file(here.with_name("tests") / "test_tail_cvar_engine.py")},
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
        "risk_free": {"series": "fred/macro/fred-DGS3MO.jsonl.gz", "role": "record required data (3-month US Treasury bill yield) feeding the coherent metrics; ladder Sharpe keeps the pipeline rf=0 convention",
                      "conversion": "annual percent / 100 / 365, last official observation carried over non-trading days"},
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
        raise ValueError("strategy domain must be the exact registered case set")
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
        raise ValueError("signal constants must stay the registered values")
    script = spec.get("script", {})
    if script.get("path") != "/scripts/280_tail_cvar_run.py" \
            or script.get("sha256") != sha256_file(script_path or __file__):
        raise ValueError("script bytes do not match pinned identity")
    engine = spec.get("engine", {})
    test = Path(test_path) if test_path else Path(__file__).resolve().with_name("tests") / "test_tail_cvar_engine.py"
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
    """Canonical monthly shards whose month falls inside [start, end]."""
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
              "modeled_ignored": 0, "other_ignored": 0, "out_of_window": 0, "days_with_official": 0,
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


def load_risk_free(open_ms, root=RAW_ROOT):
    """Canonical official 3-month T-bill yield (record required data) -> daily rf rate."""
    path = Path(root) / "fred/macro/fred-DGS3MO.jsonl.gz"
    report = {"series": "DGS3MO", "path": str(path), "file_missing": not path.is_file(),
              "rows_in_window": 0, "truth_status_official_only": True,
              "carry_forward": "last official observation held over non-trading days",
              "first_official_date": None, "last_official_date": None,
              "conversion": "annual percent / 100 / 365"}
    if not path.is_file():
        raise RuntimeError("canonical risk-free series missing: %s" % path)
    report["source_sha256"] = sha256_file(path)
    by_date = {}
    # bounded lookback: the window's first calendar day need not itself carry an
    # observation (weekend/holiday), so the carry-forward may be seeded from the last
    # official observation at most 10 days before the window opens.  Nothing beyond
    # that bounded lookback is read, and a missing seed still fails closed.
    lo_window, hi = int(open_ms[0]), int(open_ms[-1]) + MS_DAY
    lo_seed = lo_window - 10 * MS_DAY
    report["seed_lookback_days"] = 10
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            t = int(row["open_time_ms"])
            if not lo_seed <= t < hi:
                continue
            if row.get("truth_status") not in (None, "official"):
                continue
            value = float(row["value"])
            if not math.isfinite(value) or value < 0:
                raise RuntimeError("invalid risk-free observation")
            by_date[stamp(t)] = value
            if lo_window <= t < hi:
                report["rows_in_window"] += 1
            else:
                report["seed_rows_before_window"] = report.get("seed_rows_before_window", 0) + 1
    if not by_date:
        raise RuntimeError("no risk-free observations inside the registered window")
    pre = [d for d in by_date if d < stamp(lo_window)]
    last = by_date[max(pre)] if pre else None      # seed the carry-forward across the boundary
    report["carry_forward_seeded_from"] = max(pre) if pre else None
    rf = []
    for t in open_ms:
        key = stamp(t)
        if key in by_date:
            last = by_date[key]
        if last is None:
            raise RuntimeError("no official risk-free observation within 10 days before %s" % key)
        rf.append(last / 100.0 / 365.0)
    dates = sorted(by_date)
    report["first_official_date"], report["last_official_date"] = dates[0], dates[-1]
    report["days_aligned"] = len(rf)
    return np.asarray(rf, dtype=np.float64), report


# --------------------------------------------------------------------------------------
# Econometrics: GJR-GARCH(1,1)-t, Hill tail index, GPH / local Whittle long memory
# --------------------------------------------------------------------------------------
GARCH_BOUNDS = ((1e-14, None), (0.0, 1.0), (-0.5, 1.0), (0.0, 1.0), (T_MIN_NU, 60.0))


def _garch_path(theta, rets, want_nll=True):
    """Filter GJR-GARCH(1,1)-t over rets.

    Returns (nll, forecast_variance_for_next_obs, variance_path_used).  The first
    observation is filtered with the stationary starting variance; every later
    observation uses the one-step recursion, so the forecast returned after the last
    observation uses information strictly through that observation.
    """
    omega, alpha, gamma, beta, nu = float(theta[0]), float(theta[1]), float(theta[2]), float(theta[3]), float(theta[4])
    if not (omega > 0 and 0 <= alpha <= 1 and 0 <= beta <= 1 and nu > T_MIN_NU):
        return 1e12, float("nan"), None
    if alpha + min(gamma, 0.0) < 0:
        return 1e12, float("nan"), None
    denom = 1.0 - alpha - beta - 0.5 * gamma
    if denom <= 5e-4:                     # stationarity margin: persistence < 0.9995
        return 1e12, float("nan"), None
    n = len(rets)
    log_c = math.lgamma(0.5 * (nu + 1.0)) - math.lgamma(0.5 * nu) - 0.5 * math.log(math.pi * (nu - 2.0))
    s2 = omega / denom
    path = [0.0] * n
    prev_e = 0.0
    prev_s2 = s2
    nll = 0.0
    for i in range(n):
        if i:
            ind = 1.0 if prev_e < 0.0 else 0.0
            s2 = omega + (alpha + gamma * ind) * prev_e * prev_e + beta * prev_s2
            if not (s2 > 0.0) or not math.isfinite(s2):
                return 1e12, float("nan"), None
        e = float(rets[i])
        z2 = e * e / s2
        if want_nll:
            term = 0.5 * math.log(s2) - log_c + 0.5 * (nu + 1.0) * math.log1p(z2 / (nu - 2.0))
            if not math.isfinite(term):
                return 1e12, float("nan"), None
            nll += term
        path[i] = s2
        prev_e, prev_s2 = e, s2
    ind = 1.0 if prev_e < 0.0 else 0.0
    forecast = omega + (alpha + gamma * ind) * prev_e * prev_e + beta * prev_s2
    if not (forecast > 0.0) or not math.isfinite(forecast):
        return 1e12, float("nan"), None
    return (nll if want_nll else 0.0), forecast, path


def fit_gjr_garch_t(rets, prev=None):
    """MLE fit of the record's GJR-GARCH(1,1)-t.  Raises when nothing converges."""
    rets = [float(x) for x in rets]
    n = len(rets)
    if n < 50:
        raise RuntimeError("estimation window too short for GJR-GARCH-t")
    var = float(np.var(rets, ddof=1))
    if not (var > 0):
        raise RuntimeError("degenerate return window")
    starts = []
    if isinstance(prev, dict):
        starts.append(np.array([float(prev[k]) for k in
                                ("omega", "alpha", "gamma", "beta", "nu")]))
    starts += [np.array([max(var * 0.1, 1e-10), 0.05, 0.05, 0.90, 6.0]),
               np.array([max(var * 0.02, 1e-12), 0.10, 0.00, 0.80, 5.0])]
    lo = [b[0] if b[0] is not None else -1e18 for b in GARCH_BOUNDS]
    hi = [b[1] if b[1] is not None else 1e18 for b in GARCH_BOUNDS]
    best, best_f = None, float("inf")
    for x0 in starts:
        x0 = np.clip(np.asarray(x0, dtype=float), lo, hi)
        for method in ("L-BFGS-B", "Nelder-Mead"):
            try:
                res = minimize(lambda th: _garch_path(th, rets)[0], x0, method=method,
                               bounds=GARCH_BOUNDS,
                               options={"maxiter": 400} if method == "Nelder-Mead"
                               else {"maxiter": 300})
            except Exception:
                continue
            cand = np.asarray(res.x, dtype=float)
            if not np.isfinite(cand).all():
                continue
            # only candidates that pass the model's own validity region may win, so a
            # boundary wander can never become the accepted parameter set
            value = float(_garch_path(cand, rets)[0])
            if value >= 1e11:
                continue
            if value < best_f:
                best_f, best = value, cand
    if best is None or not math.isfinite(best_f) or best_f >= 1e11:
        raise RuntimeError("GJR-GARCH-t fit failed to converge")
    omega, alpha, gamma, beta, nu = (float(v) for v in best)
    if not (omega > 0 and nu > T_MIN_NU and alpha + beta + 0.5 * gamma < 1.0):
        raise RuntimeError("GJR-GARCH-t fit outside the valid parameter region")
    return {"omega": omega, "alpha": alpha, "gamma": gamma, "beta": beta, "nu": nu,
            "nll": best_f, "persistence": alpha + beta + 0.5 * gamma}


def hill_tail_index(losses, k=None):
    """Record's Hill estimator: k = m = floor(n^0.5) ordered left-tail losses."""
    arr = np.asarray([x for x in losses if x > 0], dtype=float)
    n = len(arr)
    if n < 8:
        return None
    m = int(math.floor(math.sqrt(n))) if k is None else int(k)
    m = max(4, min(m, n - 1))
    ordered = np.sort(arr)[::-1]              # L_(1) >= ... >= L_(n)
    lk1 = ordered[m]                          # L_(k+1), 0-based index m
    if not (lk1 > 0):
        return None
    terms = np.log(ordered[:m]) - math.log(lk1)
    mean_term = float(np.mean(terms))
    if not (mean_term > 0):
        return None
    return {"alpha_hat": 1.0 / mean_term, "k": m, "sample_size": n}


def gph_d(squared, m=None):
    """Geweke-Porter-Hudak log-periodogram regression of squared returns."""
    x = np.asarray(squared, dtype=float)
    x = x[np.isfinite(x) & (x > 0)]
    n = len(x)
    if n < 64:
        return None
    q = int(math.floor(math.sqrt(n))) if m is None else int(m)
    q = max(8, min(q, n // 4))
    spec = np.abs(np.fft.rfft(x - x.mean())) ** 2
    j = np.arange(1, q + 1)
    lam = 2.0 * math.pi * j / n
    I = spec[1:q + 1]
    I = np.maximum(I, 1e-300)
    slope, _ = np.polyfit(np.log(lam), np.log(I), 1)
    return float(0.5 * (slope + 1.0))


def local_whittle_d(squared, q=None):
    """Gaussian semiparametric (local Whittle) estimate of d on squared returns."""
    x = np.asarray(squared, dtype=float)
    x = x[np.isfinite(x) & (x > 0)]
    n = len(x)
    if n < 64:
        return None
    qq = int(math.floor(n ** 0.5)) if q is None else int(q)
    qq = max(8, min(qq, n // 4))
    spec = np.abs(np.fft.rfft(x - x.mean())) ** 2
    I = np.maximum(spec[1:qq + 1], 1e-300)
    j = np.arange(1, qq + 1)
    lam = 2.0 * math.pi * j / n
    grid = np.linspace(-0.45, 0.49, 189)
    best, best_d = float("inf"), None
    for d in grid:
        value = math.log(float(np.mean((lam ** (2.0 * d)) * I)))
        if value < best:
            best, best_d = value, float(d)
    return best_d


# --------------------------------------------------------------------------------------
# Portfolio optimization: Rockafellar-Uryasev CVaR (LP) and MVP (convex QP)
# --------------------------------------------------------------------------------------
def solve_cvar(R, alpha, budget, A_ub=None):
    """Rockafellar-Uryasev CVaR allocation: min gamma + 1/(alpha*T) * sum max(0, -r'w - gamma)."""
    R = np.asarray(R, dtype=float)
    T, N = R.shape
    if A_ub is None:
        A_ub = np.zeros((T, N + 1 + T))
        A_ub[:, :N] = -R
        A_ub[:, N] = -1.0
        A_ub[:, N + 1:] = -np.eye(T)
    c = np.r_[np.zeros(N), 1.0, np.full(T, 1.0 / (alpha * T))]
    bounds = [(-budget, 1.0 + budget)] * N + [(None, None)] + [(0.0, None)] * T
    res = linprog(c, A_ub=A_ub, b_ub=np.zeros(T), A_eq=np.r_[np.ones(N), 0.0, np.zeros(T)][None, :],
                  b_eq=[1.0], bounds=bounds, method="highs")
    if not res.success or res.x is None:
        raise RuntimeError("CVaR LP failed (alpha=%s budget=%s): %s" % (alpha, budget, res.message))
    w = np.asarray(res.x[:N], dtype=float)
    return w


def solve_mvp(Sigma, budget, x0=None):
    """Minimum-variance portfolio under the same box constraints and sum(w)=1."""
    Sigma = np.asarray(Sigma, dtype=float)
    N = Sigma.shape[0]
    start = np.full(N, 1.0 / N) if x0 is None else np.clip(np.asarray(x0, float), -budget, 1.0 + budget)
    if abs(start.sum() - 1.0) > 1e-9:
        start = np.full(N, 1.0 / N)
    bounds = [(-budget, 1.0 + budget)] * N
    cons = ({"type": "eq", "fun": lambda w: float(np.sum(w) - 1.0),
             "jac": lambda w: np.ones(N)},)
    last = None
    for guess in (start, np.full(N, 1.0 / N)):
        res = minimize(lambda w: float(w @ Sigma @ w), guess,
                       jac=lambda w: 2.0 * Sigma @ w, method="SLSQP",
                       bounds=bounds, constraints=cons, options={"maxiter": 200, "ftol": 1e-12})
        w = np.asarray(res.x, dtype=float)
        feasible = (abs(float(w.sum()) - 1.0) <= 1e-6
                    and float(w.min()) >= -budget - 1e-6 and float(w.max()) <= 1.0 + budget + 1e-6
                    and np.isfinite(w).all())
        if feasible and np.isfinite(res.fun):
            return w
        last = res
    raise RuntimeError("MVP QP failed: %s" % (getattr(last, "message", "infeasible"),))


def psd_fix(corr):
    """Symmetrize and clip a correlation matrix onto the PSD cone (numerical hygiene)."""
    corr = 0.5 * (corr + corr.T)
    vals, vecs = np.linalg.eigh(corr)
    vals = np.clip(vals, 1e-8, None)
    fixed = vecs @ np.diag(vals) @ vecs.T
    d = np.sqrt(np.diag(fixed))
    return fixed / np.outer(d, d)


def psd_cov(mat):
    """Symmetrize and clip a covariance matrix onto the PSD cone (no rescaling)."""
    mat = 0.5 * (mat + mat.T)
    vals, vecs = np.linalg.eigh(mat)
    vals = np.clip(vals, 1e-12, None)
    return vecs @ np.diag(vals) @ vecs.T


def direction_from_weight(w, deadband=WEIGHT_DEADBAND):
    out = np.zeros(len(w), dtype=np.int8)
    out[w > deadband] = 1
    out[w < -deadband] = -1
    return out


def build_signal_layer(daily, report=None):
    """Point-in-time allocation layer: per-day weights for every registered case.

    Every estimation window ends at the signal day t, every covariance and every
    scenario row comes from data through t, and the resulting direction for bar t+1 is
    therefore observable at t's close (record execution timing).  A construction flag
    ``max_data_index_used`` is written per solve so the self-check can prove it.
    """
    ref = daily[SYMBOLS[0]]
    n = len(ref["close"])
    rets = {s: np.r_[0.0, daily[s]["close"][1:] / daily[s]["close"][:-1] - 1.0] for s in SYMBOLS}
    per_symbol = {}
    econ = {}
    for s in SYMBOLS:
        r = rets[s]
        sigma = np.full(n, np.nan)
        params = None
        last_refit = -10 ** 9
        forecast = float("nan")
        carried = 0
        fits = 0
        z_at = {}
        diag_at = []
        for t in range(EST_WINDOW - 1, n - 1):
            window = r[t - EST_WINDOW + 1:t + 1]
            if params is None or (t - last_refit) >= REFIT_EVERY:
                try:
                    fitted = fit_gjr_garch_t(window, prev=params)
                    params = fitted
                    fits += 1
                except Exception:
                    if params is None:
                        raise
                    carried += 1          # registered: carry the last converged set forward
                last_refit = t
                _, forecast, path = _garch_path((params["omega"], params["alpha"], params["gamma"],
                                                 params["beta"], params["nu"]), window, want_nll=False)
                if not math.isfinite(forecast):
                    raise RuntimeError("non-finite GARCH forecast for %s" % s)
                z = np.asarray(window) / np.sqrt(np.asarray(path))
                z_at[t] = z
                losses = [-x for x in window if x < 0]
                hill = hill_tail_index(losses)
                sq = np.asarray(window) ** 2
                diag_at.append({"day_index": t, "date": stamp(int(ref["open_ms"][t])),
                                "alpha_arch": params["alpha"], "gamma_asym": params["gamma"],
                                "beta_garch": params["beta"], "omega": params["omega"],
                                "nu_t": params["nu"], "persistence": params["persistence"],
                                "hill_alpha_hat": (hill or {}).get("alpha_hat"),
                                "hill_k": (hill or {}).get("k"),
                                "gph_d": gph_d(sq), "local_whittle_d": local_whittle_d(sq)})
            else:
                e = float(r[t])
                ind = 1.0 if e < 0.0 else 0.0
                forecast = (params["omega"] + (params["alpha"] + params["gamma"] * ind) * e * e
                            + params["beta"] * forecast)
                if not (forecast > 0) or not math.isfinite(forecast):
                    raise RuntimeError("GARCH filter diverged for %s" % s)
            sigma[t] = math.sqrt(forecast)
        if not np.isfinite(sigma[EST_WINDOW - 1:n - 1]).all():
            raise RuntimeError("missing sigma forecasts for %s" % s)
        per_symbol[s] = {"sigma": sigma, "z_at": z_at, "params": params}
        econ[s] = {"fits_converged": fits, "fits_carried_forward": carried,
                   "refit_days": sorted(z_at), "windows": diag_at}
    if report is not None:
        report["garch"] = {s: {"fits": econ[s]["fits_converged"], "carried": econ[s]["fits_carried_forward"]}
                           for s in SYMBOLS}

    rho_at = {}
    refit_days = sorted(set.intersection(*[set(per_symbol[s]["z_at"]) for s in SYMBOLS]))
    for t in refit_days:
        Z = np.vstack([per_symbol[s]["z_at"][t] for s in SYMBOLS])
        rho_at[t] = psd_fix(np.corrcoef(Z))

    weights = {i: np.zeros((n, len(SYMBOLS))) for i in range(len(CASES))}
    defined = np.zeros(n, dtype=bool)
    mvp_weights = {b: np.zeros((n, len(SYMBOLS))) for b in STRATEGY_AXES["short_budget"]}
    divergence_emp = {i: np.full(n, np.nan) for i in range(len(CASES))}
    gauss_div = {i: [] for i in range(len(CASES))}
    reduced_div = {i: [] for i in range(len(CASES))}
    worst_eq = 0.0
    worst_box = 0.0
    max_data_index_used = -1
    rho_ptr = 0
    keep = [i for i, s in enumerate(SYMBOLS) if s not in CONCENTRATION_SYMBOLS]
    for t in range(EST_WINDOW - 1, n - 1):
        while rho_ptr + 1 < len(refit_days) and refit_days[rho_ptr + 1] <= t:
            rho_ptr += 1
        rho = rho_at[refit_days[rho_ptr]] if refit_days[rho_ptr] <= t else rho_at[refit_days[0]]
        sig = np.array([per_symbol[s]["sigma"][t] for s in SYMBOLS])
        D = np.diag(sig)
        Sigma = psd_cov(D @ rho @ D)
        Sigma = 0.5 * (Sigma + Sigma.T)
        R = np.vstack([rets[s][t - EST_WINDOW + 1:t + 1] for s in SYMBOLS]).T
        if R.shape != (EST_WINDOW, len(SYMBOLS)) or not np.isfinite(R).all():
            raise RuntimeError("bad scenario matrix at day %d" % t)
        A_ub = np.zeros((EST_WINDOW, len(SYMBOLS) + 1 + EST_WINDOW))
        A_ub[:, :len(SYMBOLS)] = -R
        A_ub[:, len(SYMBOLS)] = -1.0
        A_ub[:, len(SYMBOLS) + 1:] = -np.eye(EST_WINDOW)
        defined[t] = True
        max_data_index_used = max(max_data_index_used, t)
        for budget in STRATEGY_AXES["short_budget"]:
            mvp_w = solve_mvp(Sigma, budget, x0=mvp_weights[budget][t - 1] if t else None)
            mvp_weights[budget][t] = mvp_w
        for case in CASES:
            w = solve_cvar(R, case["cvar_alpha"], case["short_budget"], A_ub=A_ub)
            weights[case["case_code"]][t] = w
            mvp_w = mvp_weights[case["short_budget"]][t]
            divergence_emp[case["case_code"]][t] = float(np.mean(np.abs(w - mvp_w)))
            worst_eq = max(worst_eq, abs(float(w.sum()) - 1.0))
            worst_box = max(worst_box, float(max(-budget_min(case, w), budget_max(case, w))))
        # --- record falsification probes measured on their own refit days only ---
        if t in rho_at:
            rng = np.random.default_rng(SEED + t)
            mu = R.mean(axis=0)
            cov = np.cov(R, rowvar=False)
            L = np.linalg.cholesky(psd_cov(cov))
            R_gauss = mu + rng.standard_normal(R.shape) @ L.T
            A_g = np.zeros((EST_WINDOW, len(SYMBOLS) + 1 + EST_WINDOW))
            A_g[:, :len(SYMBOLS)] = -R_gauss
            A_g[:, len(SYMBOLS)] = -1.0
            A_g[:, len(SYMBOLS) + 1:] = -np.eye(EST_WINDOW)
            cov_g = np.cov(R_gauss, rowvar=False)
            for case in CASES:
                wg = solve_cvar(R_gauss, case["cvar_alpha"], case["short_budget"], A_ub=A_g)
                mvg = solve_mvp(cov_g, case["short_budget"])
                gauss_div[case["case_code"]].append((t, float(np.mean(np.abs(wg - mvg)))))
                # concentration-removal universe (record item 2)
                Rr = R[:, keep]
                A_r = np.zeros((EST_WINDOW, len(keep) + 1 + EST_WINDOW))
                A_r[:, :len(keep)] = -Rr
                A_r[:, len(keep)] = -1.0
                A_r[:, len(keep) + 1:] = -np.eye(EST_WINDOW)
                wr = solve_cvar(Rr, case["cvar_alpha"], case["short_budget"], A_ub=A_r)
                mvr = solve_mvp(Sigma[np.ix_(keep, keep)], case["short_budget"])
                reduced_div[case["case_code"]].append((t, float(np.mean(np.abs(wr - mvr)))))
    directions = {}
    for case in CASES:
        code = case["case_code"]
        directions[code] = {s: direction_from_weight(weights[code][:, SYMBOLS.index(s)])
                            for s in SYMBOLS}
    if report is not None:
        report["max_data_index_used"] = max_data_index_used
        report["signal_days"] = int(defined.sum())
        report["worst_sum_w_deviation"] = worst_eq
        report["worst_box_violation"] = worst_box
    return {"weights": weights, "mvp_weights": mvp_weights, "directions": directions,
            "defined": defined, "econ": econ, "divergence_emp": divergence_emp,
            "gauss_div": gauss_div, "reduced_div": reduced_div, "refit_days": refit_days,
            "report": report or {}}


def budget_min(case, w):
    return float(max(0.0, -float(np.min(w)) - case["short_budget"]))


def budget_max(case, w):
    return float(max(0.0, float(np.max(w)) - (1.0 + case["short_budget"])))


# --------------------------------------------------------------------------------------
# DCA ladder execution (candidate body DCA rail, per-fill accounting)
# --------------------------------------------------------------------------------------
def window_indices(ms, start, end):
    return int(np.searchsorted(ms, utc_ms(start))), int(np.searchsorted(ms, utc_ms(end) + MS_DAY))


def empty_metric(i0, i1):
    return {"gross_pnl": 0.0, "fees": 0.0, "funding": 0.0, "net_pnl": 0.0,
            "ending_equity": START_EQUITY, "episodes": 0, "fills": 0, "adds": 0,
            "turnover_usdt": 0.0, "sharpe": 0.0, "max_dd_pct": 0.0, "max_dd_usdt": 0.0,
            "annualized_return": 0.0, "max_effective_leverage": 0.0, "capital_utilization": 0.0,
            "tp_hits": 0, "stop_hits": 0, "margin_calls": 0, "end_exits": 0, "open_at_end": 0,
            "signal_flat_exits": 0, "rebalance_exits": 0, "layer_hist": [0] * LADDER_LEVELS,
            "decomposition_ok": True, "daily_equity": [START_EQUITY] * max(0, i1 - i0)}


def metric_block(m):
    keys = ("gross_pnl", "fees", "funding", "net_pnl", "ending_equity", "episodes", "fills",
            "adds", "turnover_usdt", "sharpe", "max_dd_pct", "max_dd_usdt", "annualized_return",
            "max_effective_leverage", "capital_utilization", "tp_hits", "stop_hits", "margin_calls",
            "end_exits", "open_at_end", "signal_flat_exits", "rebalance_exits")
    return {k: m[k] for k in keys}


def simulate(panel, funding_events, direction, case, dca, i0, i1, cost, instrument):
    """Run the registered ladder on daily bars for one (signal, case, DCA, phase) cell.

    Daily bar = one deterministic conservative ordering: open actions (signal-flat or
    direction-flip flatten, then tranche #1) at the open with adverse tick slippage,
    then that day's official settlements while the book is live, then the intraday rail
    (open-gap invalidation, adverse scale-ins, margin guard, invalidation, breakeven TP),
    then the window-end flatten.  Gross PnL is accumulated only from the independent
    price-PnL expression.
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
    n_days = i1 - i0
    cash = np.zeros(n_days)
    unreal = np.zeros(n_days)
    gross = fees = funding_paid = turnover = realized = 0.0
    eps = fills = adds = tp_hits = stop_hits = margin_calls = end_exits = 0
    signal_flat_exits = rebalance_exits = 0
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
        nonlocal signal_flat_exits, rebalance_exits, end_exits, hist
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
        if reason == "signal_flat":
            signal_flat_exits += 1
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
        if qty != 0 and target == 0:
            # the record's allocation withdraws this leg: reduce-only flatten, then FLAT
            side = 1.0 if held_dir > 0 else -1.0
            close_position(open_px - tick * side, base, "signal_flat")
        elif qty != 0 and target != 0 and target != held_dir:
            side = 1.0 if held_dir > 0 else -1.0
            close_position(open_px - tick * side, base, "rebalance")
        if qty == 0 and target != 0:
            side = 1.0 if target > 0 else -1.0
            fill = open_px + tick * side
            equity_before = START_EQUITY + realized
            quote0 = min(BASE_QUOTE * LEVERAGE, equity_before * LEVERAGE)
            if quote0 > 0 and equity_before > quote0 / LEVERAGE:
                qty0 = target * quote0 / fill
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
            "end_exits": end_exits, "open_at_end": 0, "signal_flat_exits": signal_flat_exits,
            "rebalance_exits": rebalance_exits, "layer_hist": hist,
            "decomposition_ok": abs(gross - fees - funding_paid - net) < 1e-3
            and abs(net - independent_cash) < 1e-3
            and abs(net - (float(equity[-1]) - START_EQUITY)) < 1e-3,
            "daily_equity": equity.tolist()}


# --------------------------------------------------------------------------------------
# Selector / disposition (cohort-selector-v1, cohort-disposition-v1)
# --------------------------------------------------------------------------------------
def cell_key(row):
    return (row["symbol"], row["timeframe"], row["cvar_alpha"], row["short_budget"],
            *(row[k] for k in DCA_AXES))


def _axis_index(axis_name, value):
    domain = STRATEGY_AXES.get(axis_name) or DCA_AXES[axis_name]
    return list(domain).index(value)


def neighbourhood(winner, historical):
    if winner["grid"] != "historical" or any(r["grid"] != "historical" for r in historical):
        raise ValueError("selector may only read historical rows")
    found = {cell_key(r): r for r in historical}
    # Frozen round-spec parameter_contract: TWO strategy axes (cvar_alpha, short_budget)
    # plus the four atomic DCA axes; face neighbours are walked in that joint space.
    steps = [((2,), list(STRATEGY_AXES["cvar_alpha"])), ((3,), list(STRATEGY_AXES["short_budget"]))]
    steps += [((pos,), list(domain)) for pos, domain in enumerate(DCA_AXES.values(), start=4)]
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


def _order_key(r):
    """Sharpe desc, net_pnl desc, then registered-index lexical tie-break."""
    return (-float(r["sharpe"]), -float(r["net_pnl"]),
            _axis_index("cvar_alpha", r["cvar_alpha"]), _axis_index("short_budget", r["short_budget"]),
            *(_axis_index(k, r[k]) for k in DCA_AXES))


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
    winner = min(candidates, key=_order_key)
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
    detail = {"winner": {k: winner[k] for k in ("cvar_alpha", "short_budget", *DCA_AXES)},
              "winner_source_grid": "historical",
              "best_historical_episodes": best, "neighbourhood": neighbors,
              "phases": phases, "robustness": robustness,
              "metrics": {**phases, "robustness": robustness, "neighbourhood": neighbors},
              "cull_reasons": reasons}
    return (winner if not reasons else None), detail


def write_grid(path, rows):
    metric_keys = list(metric_block(empty_metric(0, 0)))
    cols = ["symbol", "timeframe", "cvar_alpha", "short_budget", "case_label", *DCA_AXES,
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


# --------------------------------------------------------------------------------------
# Portfolio-level diagnostics: benchmarks, coherent metrics, falsification battery
# --------------------------------------------------------------------------------------
def simple_returns(daily):
    mat = np.vstack([daily[s]["close"] for s in SYMBOLS])
    ref = daily[SYMBOLS[0]]["open_ms"]
    for s in SYMBOLS:
        if len(daily[s]["open_ms"]) != len(ref) or not np.array_equal(daily[s]["open_ms"], ref):
            raise RuntimeError("symbol calendars are not aligned: " + s)
    out = np.zeros(mat.shape)
    out[:, 1:] = mat[:, 1:] / mat[:, :-1] - 1.0
    return out


def sharpe_from_returns(rets):
    r = np.asarray(rets, dtype=np.float64)
    if len(r) < 3:
        return 0.0
    sd = float(np.std(r, ddof=1))
    return float(np.mean(r) / sd * math.sqrt(365)) if sd > 1e-12 else 0.0


def upper_tail_mean(x, tail):
    """E[X | X >= VaR_{1-tail}] with the average-excess (CVaR) convention."""
    x = np.sort(np.asarray(x, dtype=float))
    k = max(1, int(math.ceil(tail * len(x))))
    return float(np.mean(x[-k:]))


def coherent_metrics(excess, rf_daily):
    """Rachev / STARR / Sortino on excess returns (record's coherent evaluation set).

    The record's displayed Rachev formula and its prose describe reciprocal
    orientations; the standard definition named by the prose (expected extreme gains /
    expected extreme losses) is implemented and disclosed in the round-spec.
    """
    x = np.asarray(excess, dtype=float)
    if len(x) < 5:
        return None
    gains = upper_tail_mean(x, 0.05)
    losses = upper_tail_mean(-x, 0.05)
    gains99 = upper_tail_mean(x, 0.01)
    losses99 = upper_tail_mean(-x, 0.01)
    downside = x[x < 0]
    dd = float(np.std(downside, ddof=1)) if len(downside) > 1 else 0.0
    mean = float(np.mean(x))
    return {"rachev_95": (gains / losses) if losses > 1e-12 else None,
            "rachev_99": (gains99 / losses99) if losses99 > 1e-12 else None,
            "starr_95": (mean / losses) if losses > 1e-12 else None,
            "sortino_annualized": (mean * 365.0 / (dd * math.sqrt(365.0))) if dd > 1e-12 else None,
            "mean_annualized": mean * 365.0,
            "sharpe_annualized": sharpe_from_returns(x),
            "rf_annualized_mean": float(np.mean(rf_daily)) * 365.0}


def portfolio_returns(weights_apply, rets, cost_rate=0.0):
    """Rebalanced portfolio daily returns with an explicit turnover cost.

    weights_apply[:, t] is the weight held during day t (the decision made at t-1's
    close), so no day trades on its own close.
    """
    rp = np.einsum("in,in->n", weights_apply, rets)
    turnover = np.zeros(rets.shape[1])
    turnover[1:] = np.abs(weights_apply[:, 1:] - weights_apply[:, :-1]).sum(axis=0)
    return rp - cost_rate * turnover, turnover


def buy_and_held_weights(rets, start_weights=None):
    n = rets.shape[1]
    w = (np.full(rets.shape[0], 1.0 / rets.shape[0]) if start_weights is None
         else np.asarray(start_weights, float).copy())
    out = np.zeros_like(rets)
    for t in range(n):
        out[:, t] = w
        if t + 1 < n:
            grown = w * (1.0 + rets[:, t])
            total = grown.sum()
            w = grown / total if total > 0 else w
    return out


def max_drawdown_pct(equity_curve):
    eq = np.asarray(equity_curve, dtype=float)
    if len(eq) < 2:
        return 0.0
    high = np.maximum.accumulate(eq)
    dd = (high - eq) / np.maximum(high, 1e-9)
    return float(np.max(dd) * 100.0)


def falsification_report(layer, rets, rf, windows, refit_days, seeds):
    """All four registered record falsification items, measured locally.

    These probes are portfolio-level diagnostics over the registered allocation rule;
    they never enter the selector, never alter the eligible universe and never feed the
    ladder grids.  Every item is computable here, so no item is left unevaluated.
    """
    n = rets.shape[1]
    out = {"items": {}, "scope": "local adapted USD-M perp universe, cross-sectional allocation",
           "selection_leak": "none: the probes read the frozen allocation layer only",
           "down_cycle_window": list(DOWN_CYCLE)}
    weights = layer["weights"]
    mvp = layer["mvp_weights"]
    eq_w = np.full((len(SYMBOLS), n), 1.0 / len(SYMBOLS))
    # --- item 1: synthetic Gaussian tail control -----------------------------------
    item1 = {}
    for case in CASES:
        code = case["case_code"]
        emp = [float(v) for v in layer["divergence_emp"][code][layer["refit_days"]]]
        syn = [v for _, v in layer["gauss_div"][code]]
        if not emp or not syn:
            continue
        med_emp, med_syn = float(np.median(emp)), float(np.median(syn))
        item1[case["label"]] = {"alpha": case["cvar_alpha"], "short_budget": case["short_budget"],
                                "days": len(emp), "empirical_median_divergence": med_emp,
                                "gaussian_median_divergence": med_syn,
                                "falsified": bool(med_syn >= med_emp)}
    out["items"]["gaussian_tail_control"] = {
        "registered_rule": FALSIFICATION_REGISTRY["gaussian_tail_control"],
        "status": _aggregate(item1), "cases": item1,
        "divergence": "mean absolute per-asset weight gap between the CVaR and MVP allocations",
        "gaussian_draws": "multivariate Gaussian calibrated to the trailing window mean/covariance, seeded per refit day"}
    # --- item 2: concentration removal ---------------------------------------------
    item2 = {}
    for case in CASES:
        code = case["case_code"]
        full = [float(v) for v in layer["divergence_emp"][code][layer["refit_days"]]]
        red = [v for _, v in layer["reduced_div"][code]]
        if not full or not red:
            continue
        med_full, med_red = float(np.median(full)), float(np.median(red))
        item2[case["label"]] = {"removed": list(CONCENTRATION_SYMBOLS),
                                "full_median_divergence": med_full,
                                "reduced_median_divergence": med_red,
                                "collapsed": bool(med_red < med_full),
                                "falsified": bool(med_red >= med_full)}
    out["items"]["concentration_removal"] = {
        "registered_rule": FALSIFICATION_REGISTRY["concentration_removal"],
        "status": _aggregate(item2), "cases": item2,
        "concentration_analogue": "record crypto portability names BTC/ETH as the TSMC concentration analogue"}
    # --- item 3: 10..25 bps turnover stress vs static buy-and-hold -----------------
    item3 = {}
    i0, i1 = windows["full"]
    bh = buy_and_held_weights(rets)
    bh_r, _ = portfolio_returns(bh, rets, 0.0)
    bh_wealth = float(np.prod(1.0 + bh_r[i0 + 1:i1]))
    for case in CASES:
        if case["short_budget"] != 0.30:
            continue
        row = {"alpha": case["cvar_alpha"], "buy_and_hold_wealth": bh_wealth}
        for bps in COST_STRESS_BPS:
            r, _ = portfolio_returns(weights[case["case_code"]].T, rets, bps)
            row["wealth_at_%dbps" % int(bps * 10000)] = float(np.prod(1.0 + r[i0 + 1:i1]))
        row["falsified"] = bool(row["wealth_at_%dbps" % int(COST_STRESS_BPS[1] * 10000)] < bh_wealth)
        item3[case["label"]] = row
    out["items"]["turnover_cost_stress"] = {
        "registered_rule": FALSIFICATION_REGISTRY["turnover_cost_stress"],
        "status": _aggregate(item3), "cases": item3,
        "benchmark": "static 1/N buy-and-hold, no rebalancing cost", "window": "full"}
    # --- item 4: down-cycle subsample ----------------------------------------------
    j0, j1 = windows["down_cycle"]
    item4 = {}
    for case in CASES:
        if case["short_budget"] != 0.30:
            continue
        r_strat, _ = portfolio_returns(weights[case["case_code"]].T, rets, 0.0)
        r_mvp, _ = portfolio_returns(mvp[0.30].T, rets, 0.0)
        stats = {}
        for name, series in (("cvar_ls", r_strat), ("ewp", rets.mean(axis=0)), ("mvp", r_mvp)):
            eq = START_EQUITY * np.cumprod(1.0 + series[j0 + 1:j1])
            stats[name] = {"max_dd_pct": max_drawdown_pct(np.r_[START_EQUITY, eq]),
                           **(coherent_metrics(series[j0 + 1:j1] - rf[j0 + 1:j1],
                                               rf[j0 + 1:j1]) or {})}
        ok_dd = stats["cvar_ls"]["max_dd_pct"] < min(stats["ewp"]["max_dd_pct"], stats["mvp"]["max_dd_pct"])
        ok_sortino = (stats["cvar_ls"].get("sortino_annualized") is not None
                      and stats["cvar_ls"]["sortino_annualized"] > max(
                          stats["ewp"].get("sortino_annualized") or -1e18,
                          stats["mvp"].get("sortino_annualized") or -1e18))
        ok_rachev = (stats["cvar_ls"].get("rachev_95") is not None
                     and stats["cvar_ls"]["rachev_95"] > max(stats["ewp"].get("rachev_95") or -1e18,
                                                             stats["mvp"].get("rachev_95") or -1e18))
        item4[case["label"]] = {"alpha": case["cvar_alpha"], "stats": stats,
                                "survives": bool(ok_dd and ok_sortino and ok_rachev),
                                "falsified": not bool(ok_dd and ok_sortino and ok_rachev),
                                "checks": {"max_dd_lower_than_both": bool(ok_dd),
                                           "sortino_higher_than_both": bool(ok_sortino),
                                           "rachev_higher_than_both": bool(ok_rachev)}}
    out["items"]["subsample_regime_inversion"] = {
        "registered_rule": FALSIFICATION_REGISTRY["subsample_regime_inversion"],
        "status": _aggregate(item4), "cases": item4,
        "window": list(DOWN_CYCLE),
        "window_note": "research-defined local down-cycle analogue of the record's 2000-2002 / 2008 subsamples: the 2022 crypto bear market"}
    hits = sorted(k for k, v in out["items"].items() if v.get("status") == "FALSIFIED")
    out["falsification_hits"] = hits
    out["indeterminate_items"] = sorted(k for k, v in out["items"].items()
                                        if v.get("status") == "INDETERMINATE")
    out["battery_status"] = "FAIL" if hits else (
        "NOT_FALSIFIED_WITH_INDETERMINATE" if out["indeterminate_items"] else "NOT_FALSIFIED")
    out["scoped_note"] = ("the four registered record items are measured on the cross-sectional "
                          "allocation layer; a hit is reported as a family-level science failure "
                          "per the body FAILURE TAXONOMY and is never converted into a pass")
    return out


def _aggregate(details):
    flags = [v.get("falsified") for v in details.values()]
    if not flags or all(f is None for f in flags):
        return "INDETERMINATE"
    if any(f is True for f in flags):
        return "FALSIFIED"
    return "NOT_FALSIFIED"


def equal_weight_report(rets, windows):
    report = {"definition": "daily rebalanced 1/N over the four-symbol local universe",
              "sharpe": {}, "annualized": {}}
    for g, (a, b) in windows.items():
        series = rets[:, a:b].mean(axis=0)
        report["sharpe"][g] = sharpe_from_returns(series)
        eq = START_EQUITY * np.cumprod(1.0 + series)
        report["annualized"][g] = float((max(float(eq[-1]), 1e-9) / START_EQUITY)
                                        ** (365.0 / max(1, b - a)) - 1)
    return report


# --------------------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------------------
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
        atomic_json(artifacts / "raw_build.json",
                    {"qlib": build, "daily_files": {s: daily[s]["raw_files"] for s in SYMBOLS}})
        funding = {s: load_funding(s, daily[s]["open_ms"], daily[s]["open_ms"][-1]) for s in SYMBOLS}
        atomic_json(artifacts / "funding_coverage.json", {s: funding[s][1] for s in SYMBOLS})
        rf, rf_report = load_risk_free(daily[SYMBOLS[0]]["open_ms"])
        atomic_json(artifacts / "risk_free_coverage.json", rf_report)
        rets = simple_returns(daily)
        windows = {k: window_indices(daily[SYMBOLS[0]]["open_ms"], *v)
                   for k, v in {**PHASES, "down_cycle": DOWN_CYCLE}.items()}
        log("building the point-in-time allocation layer (GJR-GARCH-t fits + CVaR/MVP solves)")
        sig_report = {}
        layer = build_signal_layer(daily, report=sig_report)
        econ_rows = {}
        for s in SYMBOLS:
            wins = layer["econ"][s]["windows"]
            def _col(key):
                vals = [w[key] for w in wins if w.get(key) is not None]
                return {"median": float(np.median(vals)) if vals else None,
                        "min": float(np.min(vals)) if vals else None,
                        "max": float(np.max(vals)) if vals else None,
                        "n": len(vals)}
            econ_rows[s] = {"fits_converged": layer["econ"][s]["fits_converged"],
                            "fits_carried_forward": layer["econ"][s]["fits_carried_forward"],
                            "refit_count": len(wins),
                            "gjr_gamma_asymmetry": _col("gamma_asym"),
                            "gjr_persistence": _col("persistence"),
                            "student_t_nu": _col("nu_t"),
                            "hill_alpha_hat_left_tail": _col("hill_alpha_hat"),
                            "gph_d_squared_returns": _col("gph_d"),
                            "local_whittle_d_squared_returns": _col("local_whittle_d"),
                            "note": "source-reported reference: EWT gamma=0.0746, persistence 0.9020, nu=4.738, Hill alpha 2.5-3.5, GPH d 0.163-0.187"}
        atomic_json(artifacts / "econometrics.json", econ_rows)
        signal_metrics = {}
        for case in CASES:
            code = case["case_code"]
            for s in SYMBOLS:
                d = layer["directions"][code][s]
                signal_metrics["%s/%s" % (case["label"], s)] = {
                    "cvar_alpha": case["cvar_alpha"], "short_budget": case["short_budget"],
                    "direction_counts": {"long": int(np.sum(d == 1)), "short": int(np.sum(d == -1)),
                                         "flat": int(np.sum(d == 0))},
                    "weight_mean_abs": float(np.mean(np.abs(layer["weights"][code][:, SYMBOLS.index(s)]))),
                    "pit_status": PIT_STATUS,
                    "pit_disclosure": "weights for day t+1 are solved from estimation scenarios, covariance and GARCH forecasts that all end at day t; the decision is executed at day t+1's open (record execution assumption), so no day trades on its own close",
                    "deadband": WEIGHT_DEADBAND}
        signal_metrics["_layer"] = {k: v for k, v in sig_report.items()}
        atomic_json(artifacts / "signal_metrics.json", signal_metrics)
        ew_report = equal_weight_report(rets, {k: v for k, v in windows.items()})
        atomic_json(artifacts / "baseline_equal_weight.json", ew_report)
        # ----------------------------------------------------------------------------------
        grid_rows = {g: [] for g in GRIDS}
        results, survivors = [], []
        hist_total = [0] * LADDER_LEVELS
        for s in SYMBOLS:
            rows = {g: [] for g in GRIDS}
            inst = instruments[s]
            for case in CASES:
                direction = layer["directions"][case["case_code"]][s]
                for dca in DCA_GRID:
                    for grid in GRIDS:
                        phase = "historical" if grid in ("historical", "no_funding") \
                            else "oos" if grid == "oos" else "full"
                        metric = simulate(daily[s], funding[s][0], direction,
                                          case, dca, *windows[phase], cost_for(grid), inst)
                        row = {"symbol": s, "timeframe": TIMEFRAME,
                               "cvar_alpha": case["cvar_alpha"], "short_budget": case["short_budget"],
                               "case_label": case["label"], **dca, "grid": grid,
                               **metric_block(metric), "decomposition_ok": metric["decomposition_ok"]}
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
        expected_keys = {(s, TIMEFRAME, c["cvar_alpha"], c["short_budget"], *(d[k] for k in DCA_AXES))
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
            return any(abs(float(x["net_pnl"]) - float(y["net_pnl"])) > tol
                       for x, y in zip(grid_rows[a], grid_rows[b]))

        cells_with_funding = [i for i, r in enumerate(grid_rows["full"])
                              if abs(float(r["funding"])) > 1e-9]
        falsification = falsification_report(layer, rets, rf, windows, layer["refit_days"],
                                             {s: SEED + 77 + i for i, s in enumerate(SYMBOLS)})
        atomic_json(artifacts / "falsification.json", falsification)
        for rec in survivors:
            rec["falsification_battery"] = falsification["battery_status"]
        # portfolio-level coherent metrics for the registered cases and benchmarks
        portfolio = {"rf_series": "DGS3MO official daily, annual percent / 100 / 365",
                     "windows": {}, "cases": {}}
        for gname, (a, b) in windows.items():
            for cname, series in (
                    ("ewp", rets.mean(axis=0)),
                    ("mvp_lo", np.einsum("in,in->n", layer["mvp_weights"][0.0].T, rets)),
                    ("mvp_ls", np.einsum("in,in->n", layer["mvp_weights"][0.30].T, rets))):
                portfolio["windows"].setdefault(gname, {})[cname] = coherent_metrics(
                    series[a:b] - rf[a:b], rf[a:b])
        for case in CASES:
            series = np.einsum("in,in->n", layer["weights"][case["case_code"]].T, rets)
            portfolio["cases"][case["label"]] = {
                gname: coherent_metrics(series[a:b] - rf[a:b], rf[a:b])
                for gname, (a, b) in windows.items()}
        portfolio["note"] = ("coherent metrics are computed on the cross-sectional allocation "
                             "layer with the canonical 3-month T-bill risk-free rate; the "
                             "per-cohort ladder Sharpe keeps the pipeline rf=0 convention")
        atomic_json(artifacts / "portfolio_diagnostics.json", portfolio)
        atomic_json(artifacts / "optimizer_diagnostics.json", {
            "layer_report": sig_report,
            "refit_days": len(layer["refit_days"]),
            "estimation_window": EST_WINDOW, "refit_every": REFIT_EVERY,
            "weight_deadband": WEIGHT_DEADBAND,
            "cases": [{"label": c["label"], "cvar_alpha": c["cvar_alpha"],
                       "short_budget": c["short_budget"],
                       "mean_abs_weight": float(np.mean(np.abs(layer["weights"][c["case_code"]]))),
                       "mean_divergence_vs_mvp": float(np.nanmean(layer["divergence_emp"][c["case_code"]]))}
                      for c in CASES],
            "constraints": "sum(w)=1, -short_budget <= w <= 1+short_budget (record LO / LS regimes)",
            "solver": "scipy linprog HiGHS (Rockafellar-Uryasev LP) + SLSQP (MVP QP), feasibility-checked"})
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
            "strategy_constants_match_record": (
                SIGNAL_PARAMS["cvar_alphas"] == [0.05, 0.01]
                and SIGNAL_PARAMS["short_budgets"] == [0.0, 0.30]
                and [c["cvar_alpha"] for c in CASES] == [0.05, 0.05, 0.01, 0.01]
                and [c["short_budget"] for c in CASES] == [0.0, 0.3, 0.0, 0.3]),
            "point_in_time_no_lookahead": (
                sig_report.get("max_data_index_used", -1) == EST_WINDOW + sig_report.get("signal_days", -1) - 2
                and sig_report.get("signal_days", 0) > 0),
            "optimizer_feasibility": (
                sig_report.get("worst_sum_w_deviation", 1.0) <= 1e-6
                and sig_report.get("worst_box_violation", 1.0) <= 1e-6),
            "gjr_garch_fit_valid": all(
                layer["econ"][s]["fits_converged"] > 0
                and layer["econ"][s]["fits_converged"] + layer["econ"][s]["fits_carried_forward"] >= 1
                and all(w["persistence"] < 1.0 and w["nu_t"] > 2.0 and w["omega"] > 0
                        for w in layer["econ"][s]["windows"])
                for s in SYMBOLS),
            "hill_and_long_memory_computed": all(
                econ_rows[s]["hill_alpha_hat_left_tail"]["n"] > 0
                and econ_rows[s]["gph_d_squared_returns"]["n"] > 0
                and econ_rows[s]["local_whittle_d_squared_returns"]["n"] > 0
                for s in SYMBOLS),
            "risk_free_series_official": (
                rf_report.get("rows_in_window", 0) > 1000
                and rf_report.get("file_missing") is False),
            "falsification_battery_evaluated": all(
                v.get("status") in ("FALSIFIED", "NOT_FALSIFIED", "INDETERMINATE")
                for v in falsification["items"].values())
            and sorted(falsification["items"]) == sorted(FALSIFICATION_REGISTRY),
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
                      "missing_conditions": [],
                      "pit_note": "the registered signal is computed at day t's close from data "
                                  "through day t and executed at day t+1's open; the construction "
                                  "assertion and the self-check perturbation test both gate this"},
                  "selector_version": "cohort-selector-v1", "disposition_version": "cohort-disposition-v1",
                  "disposition_mapping_version": "v1.4.0", "grid_kinds": GRIDS,
                  "funding_coverage": {s: funding[s][1] for s in SYMBOLS},
                  "risk_free_coverage": rf_report,
                  "stress_net_delta": stress_delta,
                  "signal_layer_report": sig_report,
                  "falsification_summary": {k: v.get("status")
                                            for k, v in falsification["items"].items()},
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
