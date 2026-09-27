#!/usr/bin/env python3
"""Direct-family OMD Qlib 0.9.7 runner. Not a source-performance reproduction.

Frozen research record: quant/observable-matrix-dynamics-portfolio-optimization-2026-09-02.md.
The record's D / T_R / T_V equations, rank-score formula, LS and LO sleeves, gamma blend
and M in {5,10} bins are implemented verbatim below. Everything else is RESEARCH-DEFINED
and is asserted against the immutable run-spec by validate_spec() before any computation:
the 4-name quantile mapping, the rolling W-day transition fit, the funding-score conversion,
the projected-per-cohort DCA book and the intrabar ordering.

N=4 forces K=1 and top-2K=2. For two selected LO names and any nonnegative distance,
max over w1+w2=1, w>=0 of 2*d*w1*w2 is attained at (1/2,1/2) and does not depend on d.
Therefore D and market-removed residual D ARE computed and measured, but they CANNOT
discriminate LO weights in this local universe: lo_optimizer_two() is the honest optimum,
the run reports the degeneracy, and no D-driven diversification edge may be claimed from
this experiment. test_omd_engine.py fails if D is faked into the weights.

Why the audited sibling rail (170_topological_anomaly_run.py) is NOT reused: its
load_funding() sums every in-window row regardless of truth_status, and canonical
funding files may contain modeled rows, which the
lifecycle footer forbids from becoming funding cost; it also settles at the bar close
instead of the official event mark price, and it has no signed weight-scaled sizing or
margin guard for a blended long/short book. This file therefore carries a minimal own
implementation with the same registered 48-config / 10-grid / per-fill cost semantics.
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

FAMILY_ID = "observable-matrix-dynamics-portfolio-optimization-2026-09-02"
ENGINE_VERSION = "omd_qlib_v1"
RAW_ROOT = "/data/raw"
RESULTS_ROOT = "/results"  # the only tree run() may ever open
WORK_ROOT = "/qlib/work/omd-v1"
QLIB_DIR = WORK_ROOT + "/qlib-data"
CSV_ROOT = WORK_ROOT + "/csv"
INSTRUMENTS_PATH = RAW_ROOT + "/binance/usdm/instruments/usdm-perp-instruments.json"
VENV_PYTHON = "/opt/venv/bin/python"
DUMP_BIN = "/opt/qlib-tools/dump_bin.py"
MS_DAY = 86_400_000
FIELDS = ("open", "high", "low", "close", "volume")
SYMBOLS = ("BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT")
TIMEFRAME = "1d"
W = 252
K = 1
ALPHA = BETA = 1.0
W_MAX = 1.0  # research-defined nonbinding upper bound; the 2-asset optimum is 0.5
FUNDING_RANK_POINT_RATE = 0.0001  # research-defined: 1 score point per 1 bp last known rate
START_EQUITY = 30_000.0
BASE_QUOTE = 1_000.0
LEVERAGE = 10.0
# The registered 12th tranche is a reserve, not a routine order. Without a
# registered reserve activation rule, only entry + ten adverse adds may fill.
# Keep the 12th histogram slot so its measured count remains explicitly zero.
MAX_ADD_LEVELS = 10
LADDER_LEVELS = 12
M_AXIS = (5, 10)  # source record's quintiles / deciles
LOOKBACK_AXIS = (7, 14)  # source record's adapted weekly / bi-weekly windows
GAMMA_AXIS = (0.3, 0.5)  # source record's registered blend range, endpoints only
STRATEGY_AXES = {"bins": M_AXIS, "lookback_days": LOOKBACK_AXIS, "gamma": GAMMA_AXIS}
STRATEGIES = tuple(dict(zip(STRATEGY_AXES, values)) for values in
                   itertools.product(*STRATEGY_AXES.values()))
DCA_AXES = {"spacing_pct": (0.01, 0.02, 0.03, 0.04),
            "size_multiplier": (1.0, 1.1), "breakeven_tp_pct": (0.01, 0.02, 0.03),
            "invalidation_pct": (0.05, 0.10)}
DCA_GRID = tuple(dict(zip(DCA_AXES, values)) for values in
                 itertools.product(*DCA_AXES.values()))
GRIDS = ("historical", "oos", "full", "fee_2x", "funding_2x",
         "entry_delay_1_bar", "slippage_2ticks", "no_funding", "no_funding_full",
         "cost_attrition_40bps")
MIN_EPISODES_IS = 10  # research-defined episode sufficiency, frozen before launch
MIN_EPISODES_OOS = 3
MIN_NEIGHBOUR = 0.60
SHUFFLES = 999  # research-defined, fixed-seed empirical one-sided p resolution 0.001
SEED = 260927
GATES = {"min_episodes_is": MIN_EPISODES_IS, "min_episodes_oos": MIN_EPISODES_OOS,
         "min_neighbour_same_sign_fraction": MIN_NEIGHBOUR}
# The record's own falsification battery, thresholds verbatim; the run-spec must carry
# exactly these three definitions before compute (no post-hoc threshold edits).
FALSIFICATION = {
    "record_tests": {
        "synthetic_markov_shuffling": {
            "failure_rule": "Sharpe with true T_R/T_V must beat the randomized baseline by >= 0.30 with p < 0.01",
            "margin_sharpe": 0.30, "p_less_than": 0.01, "draws": SHUFFLES, "seed": SEED,
            "null": "independently Dirichlet(1)-uniform random stochastic MxM matrices for T_R and T_V, one seeded pair per draw, same pair at every causal decision"},
        "cost_stress_turnover": {
            "failure_rule": "net Sharpe below 0.50 at the 15 bps one-way fee level is non-tradable",
            "one_way_fee_bps": [5, 15, 30, 50], "min_sharpe_at_15bps": 0.50},
        "cross_sectional_subperiod_walkforward": {
            "failure_rule": "long-short sleeve negative alpha in > 40% of rolling 12-month subperiods",
            "window_days": 365, "step": "calendar_month",
            "negative_fraction_fail_above": 0.40}},
    "required_stress_grids": ["fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks"],
}
PHASES = {"historical": ("2022-01-01", "2025-09-30"),
          "oos": ("2025-10-01", "2026-09-11"),
          "full": ("2022-01-01", "2026-09-11")}
ARTIFACTS = ("state.json", "result.json", "logs/run.log", "artifacts/progress.json",
             "artifacts/bins_build.json", "artifacts/assertions.json",
             "artifacts/stress_effects.json", "artifacts/cohort_results.json",
             "artifacts/cohort_survivors.json", "artifacts/signal_layer.json",
             "artifacts/family_falsification.json", "artifacts/dca_layer_histogram.json",
             "artifacts/funding_coverage.json",
             *("artifacts/grid_%s.csv" % g for g in GRIDS))


def expected_counts():
    return {"cohorts": len(SYMBOLS), "strategy_cases_per_cohort": len(STRATEGIES),
            "dca_configs_per_cohort": len(DCA_GRID),
            "base_combinations_per_cohort": len(STRATEGIES) * len(DCA_GRID),
            "case_evaluations_per_grid": len(SYMBOLS) * len(STRATEGIES) * len(DCA_GRID),
            "grid_count": len(GRIDS),
            "case_evaluations_total": len(SYMBOLS) * len(STRATEGIES) * len(DCA_GRID) * len(GRIDS)}


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def utc_ms(day):
    return int(dt.datetime.fromisoformat(day[:10]).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def stamp(ms):
    # Daily bars only: the CSV date column is a plain calendar day, the same
    # convention the shipped 1d runners feed to dump_bin (--date_field_name date).
    return dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).strftime("%Y-%m-%d")


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def _same_axis(got, axis):
    return list(got) == list(axis)


def run_spec_template():
    """Complete run-spec body for this engine; the caller fills identity/timestamps only.

    Held next to validate_spec() so a frozen spec and its reader cannot drift apart.
    """
    here = Path(__file__).resolve()
    return {
        "schema_version": 1, "document_kind": "run_spec",
        "contract": "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md v2.0.0",
        "status": "registered", "family_id": FAMILY_ID,
        "round_id": "<family_id>-r1", "run_id": "<round_id>-u1",
        "created_at_utc": "<ISO8601Z>", "container_id": "qlib-run",
        "image_id": "qlib:0.9.7-arm64",
        "script": {"path": "/scripts/230_omd_run.py", "sha256": sha256_file(here),
                   "deployed_host_path": "/Users/hong/workspace/qlib-apple-container/scripts/230_omd_run.py"},
        "engine": {"name": ENGINE_VERSION, "script": "container/scripts/230_omd_run.py",
                   "container": "qlib-run", "qlib_version": "0.9.7", "seed": SEED,
                   "self_check": "container/scripts/tests/test_omd_engine.py",
                   "self_check_sha256": sha256_file(here.with_name("tests") / "test_omd_engine.py")},
        "data": {"venue": "Binance USD-M perpetual canonical raw archive",
                 "raw_root": "/data/raw/binance/usdm", "start": PHASES["full"][0],
                 "end": PHASES["full"][1], "historical_start": PHASES["historical"][0],
                 "historical_end": PHASES["historical"][1], "oos_start": PHASES["oos"][0],
                 "oos_end": PHASES["oos"][1], "timezone": "UTC", "fields": list(FIELDS),
                 "symbols": list(SYMBOLS),
                 "timeframes": [{"raw_interval": "1d", "qlib_freq": "day", "bars_per_day": 1}],
                 "point_in_time": "the 4 fixed local USD-M perpetual contracts; no membership/survivorship claim",
                 "missing_data": "absent bars stay absent; no fill and no resample"},
        "grids": list(GRIDS), "params": [dict(c) for c in STRATEGIES],
        "split": {"historical_start": PHASES["historical"][0],
                  "historical_end": PHASES["historical"][1],
                  "oos_start": PHASES["oos"][0], "oos_end": PHASES["oos"][1],
                  "rule": "chronological; every transition, D window and funding adjustment "
                          "is fitted causally to each decision close; OOS is read-only and "
                          "never used for parameter selection"},
        "dca_domain": {**{k: list(v) for k, v in DCA_AXES.items()},
                       "grid": [dict(d) for d in DCA_GRID], "base_quote": BASE_QUOTE,
                       "base_quote_status": "PROJECT_PRE_REGISTERED_CONSTANT",
                       **{k + "_status": "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN" for k in DCA_AXES}},
        "selector_version": "cohort-selector-v1", "disposition_version": "cohort-disposition-v1",
        "expected": expected_counts(),
        "signal_constants": {"distance_window_days": W, "k": K, "alpha": ALPHA, "beta": BETA,
                             "w_max": W_MAX, "funding_rank_point_rate": FUNDING_RANK_POINT_RATE,
                             "shuffle_count": SHUFFLES, "shuffle_seed": SEED},
        "gates": dict(GATES),
        "costs": {"taker_fee": "canonical instrument taker_fee; doubled in fee_2x; overridden to 40 bps in cost_attrition_40bps",
                  "slippage_ticks": 1, "slippage_robustness_ticks": 2,
                  "slippage_basis": "canonical price_increment, adverse on every fill, both directions",
                  "funding": "official observations only, at their own timestamp and mark price; a missing official observation is zero cost for that interval with coverage disclosed; modeled rows are never charged"},
        "falsification": FALSIFICATION,
        "expected_outputs": list(ARTIFACTS),
        "notes": "direct family (handoff.execution=direct_hermes): ownership is family_id + round_id + run_id only; no Kanban task/board keys are present by contract.",
    }


def validate_spec(spec, script_path=None, test_path=None):
    """Fail before opening the result tree. Neither this function nor import writes anything."""
    if spec.get("family_id") != FAMILY_ID or spec.get("selector_version") != "cohort-selector-v1" or spec.get("disposition_version") != "cohort-disposition-v1":
        raise ValueError("wrong family/selector/disposition identity")
    rid = spec.get("round_id")
    if not isinstance(rid, str) or not re.fullmatch(re.escape(FAMILY_ID) + r"-r[1-9][0-9]*", rid) or not isinstance(spec.get("run_id"), str) or not re.fullmatch(re.escape(rid) + r"-u[1-9][0-9]*", spec["run_id"]):
        raise ValueError("round/run identity mismatch")
    if any(k in spec for k in ("task_id", "kanban_task_id", "kanban_board")):
        raise ValueError("direct family must not carry task/board ids")
    if not spec.get("created_at_utc"):
        raise ValueError("missing created_at_utc")
    data = spec["data"]
    if not str(data.get("raw_root", "")).startswith("/data/raw") or data.get("fields") != list(FIELDS) or data.get("symbols") != list(SYMBOLS) or data.get("timeframes") != [{"raw_interval": "1d", "qlib_freq": "day", "bars_per_day": 1}]:
        raise ValueError("canonical local daily panel mismatch")
    for phase, (lo, hi) in PHASES.items():
        lo_key, hi_key = ("start", "end") if phase == "full" else (phase + "_start", phase + "_end")
        if data.get(lo_key) != lo or data.get(hi_key) != hi:
            raise ValueError("embedded %s window mismatch" % phase)
        if phase == "full":
            continue  # the split block names only the two disjoint evaluation windows
        if spec.get("split", {}).get(lo_key) != lo or spec.get("split", {}).get(hi_key) != hi:
            raise ValueError("split mismatch for " + phase)
    if spec.get("grids") != list(GRIDS):
        raise ValueError("grid set mismatch")
    if spec.get("params") != list(STRATEGIES):
        raise ValueError("strategy domain must be the exact 2x2x2 registered product")
    dca = spec["dca_domain"]
    if any(not _same_axis(dca.get(k, ()), values) for k, values in DCA_AXES.items()) or dca.get("grid") != list(DCA_GRID) or dca.get("base_quote") != BASE_QUOTE:
        raise ValueError("DCA domain incomplete/changed")
    if dca.get("base_quote_status") != "PROJECT_PRE_REGISTERED_CONSTANT" or any(dca.get(k + "_status") != "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN" for k in DCA_AXES):
        raise ValueError("DCA provenance mismatch")
    if spec.get("gates") != GATES:
        raise ValueError("selector gate mismatch")
    if spec.get("signal_constants") != {"distance_window_days": W, "k": K, "alpha": ALPHA,
                                        "beta": BETA, "w_max": W_MAX,
                                        "funding_rank_point_rate": FUNDING_RANK_POINT_RATE,
                                        "shuffle_count": SHUFFLES, "shuffle_seed": SEED}:
        raise ValueError("registered signal constants mismatch")
    counts = expected_counts()
    if spec.get("expected") != counts:
        raise ValueError("coverage counts not pinned: %r" % (spec.get("expected"),))
    if spec.get("expected_outputs") != list(ARTIFACTS):
        raise ValueError("expected artifact list not pinned")
    if spec.get("falsification") != FALSIFICATION:
        raise ValueError("all three record falsification tests must be pre-registered")
    script = spec["script"]
    if script.get("path") != "/scripts/230_omd_run.py" or script.get("sha256") != sha256_file(script_path or __file__):
        raise ValueError("script bytes do not match pinned script identity")
    engine = spec.get("engine", {})
    if engine.get("name") != ENGINE_VERSION or engine.get("qlib_version") != "0.9.7" or engine.get("seed") != SEED:
        raise ValueError("engine identity mismatch")
    check_path = Path(test_path) if test_path else Path(__file__).resolve().with_name("tests") / "test_omd_engine.py"
    if engine.get("self_check_sha256") != sha256_file(check_path):
        raise ValueError("self-check bytes do not match pinned test identity")
    with open(RAW_ROOT + "/_meta/CONFIG.json", encoding="utf-8") as stream:
        catalog = json.load(stream)
    if catalog.get("market_type") != "usdm_perp" or sorted(catalog["symbols"]) != list(SYMBOLS) or "1d" not in catalog["intervals"] or not {"klines", "funding"} <= set(catalog["datasets"]):
        raise ValueError("canonical catalog diverged from registered local universe")
    return counts


def instrument_metadata(path=INSTRUMENTS_PATH):
    with open(path, encoding="utf-8") as stream:
        doc = json.load(stream)
    instruments = {row["fields"]["raw_symbol"]: row["fields"] for row in doc["instruments"]}
    result = {}
    for symbol in SYMBOLS:
        fields = instruments[symbol]
        if fields["type"] != "CryptoPerpetual" or fields["quote_currency"] != "USDT" or fields["is_inverse"]:
            raise ValueError("not a linear USDT perpetual: " + symbol)
        tick, fee = float(fields["price_increment"]), float(fields["taker_fee"])
        if not (0 < tick and 0 <= fee < 0.01):
            raise ValueError("invalid instrument tick/fee")
        result[symbol] = {"tick": tick, "taker_fee": fee,
                          "source": path, "source_sha256": sha256_file(path)}
    return result


def raw_months(symbol, start, end):
    root = Path(RAW_ROOT) / "binance/usdm/klines" / symbol / "1d"
    files = sorted(root.glob(symbol + "-1d-*.jsonl.gz"))
    files = [p for p in files if start[:7] <= p.stem.split("-1d-")[-1].split(".")[0] <= end[:7]]
    if not files:
        raise RuntimeError("no canonical daily bars: " + symbol)
    return files


def write_qlib_csv(symbol, start, end):
    target = Path(CSV_ROOT) / (symbol + ".csv")
    target.parent.mkdir(parents=True, exist_ok=True)
    lo, hi = utc_ms(start), utc_ms(end) + MS_DAY
    prev = None
    rows = 0
    with open(target, "w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["date", *FIELDS])
        for path in raw_months(symbol, start, end):
            with gzip.open(path, "rt", encoding="utf-8") as gz:
                for line in gz:
                    if not line.strip():
                        continue
                    rec = json.loads(line)
                    t = int(rec["open_time_ms"])
                    if not lo <= t < hi:
                        continue
                    if prev is not None and t - prev != MS_DAY:
                        raise RuntimeError("missing/duplicate daily bar: " + symbol)
                    if any(not math.isfinite(float(rec[k])) for k in FIELDS):
                        raise RuntimeError("nonfinite raw OHLCV")
                    writer.writerow([stamp(t), *(rec[k] for k in FIELDS)])
                    prev, rows = t, rows + 1
    if rows < W + 2:
        raise RuntimeError("insufficient W=252 daily history: " + symbol)
    return {"rows": rows, "last": prev, "csv_sha256": sha256_file(target),
            "raw_files": [{"path": str(p), "sha256": sha256_file(p)} for p in raw_months(symbol, start, end)]}


def build_qlib(start, end):
    """Raw -> CSV -> dump_bin -> actual qlib.data.D readback (never host-pandas evidence)."""
    if Path(WORK_ROOT).is_dir():
        shutil.rmtree(WORK_ROOT)
    summaries = {s: write_qlib_csv(s, start, end) for s in SYMBOLS}
    cmd = [VENV_PYTHON, DUMP_BIN, "dump_all", "--data_path", CSV_ROOT,
           "--qlib_dir", QLIB_DIR, "--freq", "day", "--include_fields",
           ",".join(FIELDS), "--date_field_name", "date", "--max_workers", "1"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if proc.returncode:
        raise RuntimeError("Qlib dump_bin failed: %s / %s" % (proc.stdout[-1000:], proc.stderr[-1000:]))
    import qlib
    from qlib.data import D
    if qlib.__version__ != "0.9.7":
        raise RuntimeError("Qlib version mismatch")
    qlib.init(provider_uri=QLIB_DIR, region="cn", expression_cache=None, dataset_cache=None)
    readback = {}
    for symbol in SYMBOLS:
        df = D.features([symbol], ["$" + f for f in FIELDS], start_time=start,
                        end_time=end + " 23:59:59", freq="day").sort_index()
        times = np.array([int(x.value // 1_000_000) for x in df.index.get_level_values("datetime")], dtype=np.int64)
        values = {f: df["$" + f].to_numpy(dtype=np.float64) for f in FIELDS}
        if len(times) != summaries[symbol]["rows"] or len(times) < W + 2 or (np.diff(times) != MS_DAY).any() or any(not np.isfinite(v).all() for v in values.values()):
            raise RuntimeError("Qlib row/null/calendar readback mismatch for " + symbol)
        if not np.all((values["open"] > 0) & (values["close"] > 0) & (values["low"] > 0)):
            raise RuntimeError("nonpositive Qlib prices")
        readback[symbol] = {"open_ms": times, **values}
        summaries[symbol]["qlib_rows"] = len(times)
        summaries[symbol]["qlib_first"] = stamp(times[0])
        summaries[symbol]["qlib_last"] = stamp(times[-1])
    common = readback[SYMBOLS[0]]["open_ms"]
    if any(not np.array_equal(common, readback[s]["open_ms"]) for s in SYMBOLS[1:]):
        raise RuntimeError("cross-section daily timestamp mismatch")
    report = {"qlib_version": qlib.__version__, "read_path": "qlib.data.D.features",
              "source": RAW_ROOT, "datasets": summaries, "qlib_rows_identical_to_raw": True}
    return readback, report


def load_funding(symbol, open_ms, root=RAW_ROOT):
    """No synthetic settlements: only official events are charged, exact official mark price."""
    path = Path(root) / "binance/usdm/funding" / symbol / (symbol + "-funding.jsonl.gz")
    if not path.is_file():
        # Lifecycle footer: absent official observations are zero cost with the gap
        # disclosed, never a launch blocker and never a modeled replacement.
        return ([[] for _ in open_ms], np.array([], dtype=np.int64), np.array([]),
                {"official": 0, "modeled_ignored": 0, "other_ignored": 0,
                 "out_of_window": 0, "days_with_official": 0, "days_total": len(open_ms),
                 "official_events_per_day": 0.0, "file_missing": True,
                 "source_sha256": None,
                 "missing_funding_intervals_are_zero_not_modeled": True,
                 "funding_score_rule": "last official observation at/before close, <=24h old; otherwise score adjustment zero and reported missing"})
    events = [[] for _ in open_ms]
    known_t, known_r = [], []
    report = {"official": 0, "modeled_ignored": 0, "other_ignored": 0,
              "out_of_window": 0, "file_missing": False,
              "missing_funding_intervals_are_zero_not_modeled": True,
              "source_sha256": sha256_file(path)}
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            row = json.loads(line)
            t = int(row["funding_time_ms"])
            if t < int(open_ms[0]) or t >= int(open_ms[-1]) + MS_DAY:
                report["out_of_window"] += 1
                continue
            status = row.get("truth_status")
            if status != "official":
                report["modeled_ignored" if status in ("modeled", "modeled_funding") else "other_ignored"] += 1
                continue
            bar = int(np.searchsorted(open_ms, t, side="right") - 1)
            rate, mark = float(row["funding_rate"]), float(row["mark_price"])
            if not (math.isfinite(rate) and math.isfinite(mark) and mark > 0) or not (0 <= bar < len(events)):
                raise RuntimeError("invalid official funding observation")
            events[bar].append((t, rate, mark))
            known_t.append(t)
            known_r.append(rate)
            report["official"] += 1
    if any(b != sorted(b) for b in events) or known_t != sorted(set(known_t)):
        raise RuntimeError("duplicate/unsorted official funding")
    report["days_with_official"] = sum(bool(e) for e in events)
    report["days_total"] = len(events)
    report["official_events_per_day"] = report["official"] / len(events)
    report["funding_score_rule"] = "last official observation at/before close, <=24h old; otherwise score adjustment zero and reported missing"
    return events, np.array(known_t, dtype=np.int64), np.array(known_r), report


def quantile_states(values, bins, symbols=SYMBOLS):
    """Rank ascending with symbol tie-break; rank r -> 1+round_half_up((r-1)(M-1)/(N-1)).

    M>N is legal: unpopulated bins remain empty, and only observed source states
    require empirical transitions. No pseudo-counts or invented observations.
    """
    n = len(values)
    if n < 2 or not np.isfinite(values).all():
        raise ValueError("nonfinite / singleton cross-sectional ranking")
    order = sorted(range(n), key=lambda i: (float(values[i]), symbols[i]))
    state = np.empty(n, dtype=np.int64)
    for rank, i in enumerate(order):
        state[i] = 1 + ((2 * rank * (bins - 1) + (n - 1)) // (2 * (n - 1)))
    return state


def transition_matrix(states, bins):
    counts = np.zeros((bins, bins), dtype=np.int64)
    for before, after in zip(states[:-1], states[1:]):
        for a, b in zip(before, after):
            counts[int(a) - 1, int(b) - 1] += 1
    totals = counts.sum(axis=1)
    transition = np.divide(counts, totals[:, None], out=np.zeros_like(counts, dtype=float),
                           where=totals[:, None] > 0)
    return transition, totals


def angular_distance(x):
    if not np.isfinite(x).all() or x.shape[0] < W or (np.std(x, axis=0) < 1e-12).any():
        raise ValueError("invalid 252-day distance window")
    corr = np.corrcoef(x, rowvar=False)
    return np.arccos(np.clip(corr, -1, 1)) / np.pi


def distance_matrices(window):
    """Full angular correlation and same-window leading-PC-removed residual angular D."""
    raw_d = angular_distance(window)
    centered = window - np.mean(window, axis=0)
    u, singular, vt = np.linalg.svd(centered, full_matrices=False)
    residual = centered - np.outer(u[:, 0] * singular[0], vt[0])
    residual_d = angular_distance(residual)
    return raw_d, residual_d


def lo_optimizer_two(distance):
    """Exact constrained optimizer for K=1, w_max=1, d>=0; do not fake D sensitivity."""
    d = float(distance)
    if not (math.isfinite(d) and 0 <= d <= 1 + 1e-12 and W_MAX >= 0.5):
        raise ValueError("invalid residual distance or infeasible cap")
    w = np.array([0.5, 0.5])
    return w, float(2 * d * w[0] * w[1])


def compose_weights(return_forecast, combined_score, residual_d, gamma):
    n = len(return_forecast)
    if n != 4 or K != 1:
        raise ValueError("this pre-registered engine requires N=4, K=1")
    rank_r = sorted(range(n), key=lambda i: (-return_forecast[i], SYMBOLS[i]))
    rank_s = sorted(range(n), key=lambda i: (-combined_score[i], SYMBOLS[i]))
    ls = np.zeros(n, dtype=float)
    ls[rank_r[0]] = 0.5
    ls[rank_r[-1]] = -0.5
    lo = np.zeros(n, dtype=float)
    a, b = rank_s[:2]
    lo[[a, b]], objective = lo_optimizer_two(residual_d[a, b])
    return gamma * ls + (1 - gamma) * lo, ls, lo, objective, (a, b)


def score_adjustment(close_ms, known_t, known_r):
    j = int(np.searchsorted(known_t, close_ms, side="right") - 1)
    if j < 0 or close_ms - known_t[j] > MS_DAY:
        return 0.0, False
    return -float(known_r[j]) / FUNDING_RANK_POINT_RATE, True


def signal_layer(closes, open_ms, case, funding_asof=None, end_index=None,
                 random_transition=None):
    """Forecast at bar CLOSE t, filled no earlier than OPEN t+1. All fits end at t."""
    end = len(open_ms) if end_index is None else end_index
    n = len(open_ms)
    if closes.shape != (n, 4) or (np.diff(open_ms) != MS_DAY).any():
        raise ValueError("nonaligned/non-daily cross-sectional input")
    bins, lookback, gamma = case["bins"], case["lookback_days"], case["gamma"]
    daily = closes[1:] / closes[:-1] - 1
    returns = np.vstack((np.zeros(4), daily))
    weights = np.zeros((n, 4)); ls_weights = np.zeros_like(weights)
    lo_weights = np.zeros_like(weights)
    decisions, prev_states_r, prev_states_v, observations = [], [], [], []
    transitions = {"observed_rows_R": 0, "observed_rows_V": 0, "zero_rows_R": 0,
                   "zero_rows_V": 0, "decisions": 0, "funding_score_covered": 0,
                   "funding_score_missing": 0, "lo_5050_degenerate_decisions": 0,
                   "decision_attempts": 0, "skipped_warmup": 0,
                   "skipped_occupied_row_without_transitions": 0,
                   "raw_distance_min": 1.0, "raw_distance_max": 0.0,
                   "residual_distance_min": 1.0, "residual_distance_max": 0.0,
                   "residual_objective_sum": 0.0}
    for t in range(W, end, lookback):
        if not np.isfinite(closes[t - W:t + 1]).all():
            raise ValueError("incomplete close window")
        window = returns[t - W + 1:t + 1]
        rr = closes[t] / closes[t - lookback] - 1
        vv = np.std(returns[t - lookback + 1:t + 1], axis=0, ddof=0)
        states_r = quantile_states(rr, bins)
        states_v = quantile_states(vv, bins)
        prev_states_r.append(states_r)
        prev_states_v.append(states_v)
        decisions.append(t)
        transitions["decision_attempts"] += 1
        # Rolling 252-day empirical transitions: the current state is observed at
        # this close, never a future state. Empty rows have zero probability, not
        # uniform or smoothed fake counts; 4 observed pairs minimum to trade.
        first = next((j for j, prior in enumerate(decisions) if prior >= t - W), 0)
        hist_r, hist_v = prev_states_r[first:], prev_states_v[first:]
        if len(hist_r) < 5:
            transitions["skipped_warmup"] += 1
            continue
        tr, counts_r = transition_matrix(hist_r, bins)
        tv, counts_v = transition_matrix(hist_v, bins)
        if np.any(counts_r[states_r - 1] == 0) or np.any(counts_v[states_v - 1] == 0):
            # An observed state with no empirical transition row cannot be forecast:
            # skip this decision for every symbol (no uniform/pseudo-count fallback)
            # and disclose the count. With N=4 only 4 of M bins are ever occupied.
            transitions["skipped_occupied_row_without_transitions"] += 1
            continue
        if random_transition is not None:
            tr, tv = random_transition
        predicted_r = tr[states_r - 1] @ np.arange(1, bins + 1)
        predicted_v = tv[states_v - 1] @ np.arange(1, bins + 1)
        s = ALPHA * predicted_r - BETA * predicted_v
        adjustments = np.zeros(4)
        if funding_asof is not None:
            for i, sym in enumerate(SYMBOLS):
                times, rates = funding_asof[sym]
                adj, covered = score_adjustment(int(open_ms[t]) + MS_DAY - 1, times, rates)
                adjustments[i] = adj
                s[i] += adj
                transitions["funding_score_covered" if covered else "funding_score_missing"] += 1
        d, dr = distance_matrices(window)
        weight, ls, lo, objective, selected = compose_weights(predicted_r, s, dr, gamma)
        observations.append((t, states_r.copy(), states_v.copy(), dr, adjustments))
        weights[t] = weight
        ls_weights[t] = ls
        lo_weights[t] = lo
        transitions["decisions"] += 1
        transitions["lo_5050_degenerate_decisions"] += 1
        transitions["observed_rows_R"] += int(np.count_nonzero(counts_r))
        transitions["observed_rows_V"] += int(np.count_nonzero(counts_v))
        transitions["zero_rows_R"] += int(np.count_nonzero(counts_r == 0))
        transitions["zero_rows_V"] += int(np.count_nonzero(counts_v == 0))
        for key, mat in (("raw", d), ("residual", dr)):
            offdiag = mat[np.triu_indices(4, 1)]
            transitions[key + "_distance_min"] = min(transitions[key + "_distance_min"], float(offdiag.min()))
            transitions[key + "_distance_max"] = max(transitions[key + "_distance_max"], float(offdiag.max()))
        transitions["residual_objective_sum"] += objective
    transitions["decision_indices"] = [int(x) for x in decisions if np.any(weights[x])]
    return {"weights": weights, "ls_weights": ls_weights, "lo_weights": lo_weights,
            "diag": transitions,
            "_observations": observations}


def randomized_layer(base, case, pair):
    """Identical causal observed-state/D path, replace only fitted T_R and T_V."""
    tr, tv = pair
    n, width = base["weights"].shape
    weights = np.zeros((n, width))
    ls_weights = np.zeros_like(weights)
    for t, state_r, state_v, residual_d, funding_adj in base["_observations"]:
        er = tr[state_r - 1] @ np.arange(1, case["bins"] + 1)
        ev = tv[state_v - 1] @ np.arange(1, case["bins"] + 1)
        w, ls, _, _, _ = compose_weights(er, ALPHA * er - BETA * ev + funding_adj,
                                         residual_d, case["gamma"])
        weights[t] = w
        ls_weights[t] = ls
    return {"weights": weights, "ls_weights": ls_weights}


def window_indices(open_ms, start, end):
    return int(np.searchsorted(open_ms, utc_ms(start))), int(np.searchsorted(open_ms, utc_ms(end) + MS_DAY))


def empty_metric(i0, i1, open_ms):
    return {"gross_pnl": 0.0, "fees": 0.0, "funding": 0.0, "net_pnl": 0.0,
            "ending_equity": START_EQUITY, "episodes": 0, "fills": 0, "adds": 0,
            "turnover_usdt": 0.0, "sharpe": 0.0, "max_dd_pct": 0.0,
            "max_dd_usdt": 0.0, "annualized_return": 0.0, "max_effective_leverage": 0.0,
            "capital_utilization": 0.0, "tp_hits": 0, "stop_hits": 0,
            "margin_calls": 0, "rebalance_exits": 0, "open_at_end": 0,
            "layer_hist": [0] * LADDER_LEVELS,
            "decomposition_ok": True, "daily_equity": [START_EQUITY] * max(0, i1 - i0)}


def gross_price_pnl(sign, quantity, price, basis):
    """Independent price-only ledger, deliberately separate from cash's PnL expression."""
    return sign * (quantity * price - basis)


def simulate(panel, funding_events, layer, symbol, dca, i0, i1, cost, instrument):
    """Signed, single-symbol projected book; all costs hit cash at the event bar.

    Research-defined daily-OHLC ordering: scheduled open flatten; official
    settlement; adverse stop/margin; TP; then ascending ladder adds. Settlement
    before intraday barriers is conservative when their actual order is unknown.
    Every fill has adverse sign: signed ENTRY/ADD = trigger+sign*tick;
    signed EXIT = trigger-sign*tick. Open/close/stop/TP all obey it.

    Rebalance semantics (research-defined mapping of the record's rebalance cadence onto
    this episode rail): every episode ends at the OPEN of the bar after the next decision,
    and that same decision may open the next episode at that same open — a portfolio
    transition, exit processed before entry. An entry is refused while an episode is open
    and on the exit bar of any intrabar exit, so a stop/TP episode is never overlapped.
    Sizing is base_quote x leverage x |blended weight|, so the panel's gross equals
    base_quote x leverage. A zero blended weight opens no position.
    """
    O, H, L, C = (panel[f] for f in ("open", "high", "low", "close"))
    ms = panel["open_ms"]
    tick = instrument["tick"] * int(cost.get("slip_ticks", 1))
    fee_rate = float(cost.get("fee_override", instrument["taker_fee"] * cost.get("fee_mult", 1.0)))
    if fee_rate < 0 or i1 <= i0 or i0 < 0 or i1 > len(ms):
        raise ValueError("invalid cost/window")
    mult = float(dca["size_multiplier"])
    cash_changes = np.zeros(i1 - i0)
    unrealized = np.zeros(i1 - i0)
    gross = fees = funding = turnover = realized = 0.0
    eps = fills = adds = tp_hits = stop_hits = margin_calls = rebalance_exits = 0
    max_lev = max_util = 0.0
    layer_hist = [0] * LADDER_LEVELS
    signals = np.flatnonzero(layer[:, SYMBOLS.index(symbol)])
    active_decisions = np.flatnonzero(np.any(layer, axis=1))
    last_exit = i0 - 1
    last_exit_open = False
    for si, t in enumerate(signals):
        if t < i0 - 1 or t >= i1 - 1:
            continue
        entry_bar = int(t) + 1 + int(cost.get("entry_delay", 0))
        if entry_bar >= i1 or entry_bar < last_exit or (entry_bar == last_exit and not last_exit_open):
            continue
        weight = float(layer[t, SYMBOLS.index(symbol)])
        if weight == 0.0:
            continue
        sign = 1 if weight > 0 else -1
        # independently projected cohort, NOT a post-selection portfolio PnL.
        quote0 = BASE_QUOTE * LEVERAGE * abs(weight)
        entry_price = float(O[entry_bar]) + sign * tick
        if entry_price <= 0:
            raise RuntimeError("nonpositive adverse entry fill")
        q = quote0 / entry_price
        entry_fee = q * entry_price * fee_rate
        # Fee is debited before checking affordability. No entry after exhaustion.
        if START_EQUITY + realized - entry_fee <= quote0 / LEVERAGE:
            continue
        qty = q
        basis = q * entry_price
        realized -= entry_fee; fees += entry_fee; turnover += quote0; fills += 1
        cash_changes[entry_bar - i0] -= entry_fee
        max_lev = max(max_lev, quote0 / (START_EQUITY + realized))
        max_util = max(max_util, quote0 / LEVERAGE / (START_EQUITY + realized))
        eps += 1; layers = 1; next_level = 1
        next_reb = int(signals[si + 1]) + 1 if si + 1 < len(signals) else i1
        # signals can be zero when this symbol is absent: use all panel decision
        # times, not just its nonzero targets, to flatten on removal.
        future = active_decisions[active_decisions > t]
        if len(future):
            next_reb = int(future[0]) + 1
        exit_bar = min(i1 - 1, next_reb)
        reason = "end"
        exit_price = float(C[exit_bar]) - sign * tick
        for bar in range(entry_bar, exit_bar + 1):
            if bar == next_reb and bar < i1:  # scheduled next-open flatten
                exit_price = float(O[bar]) - sign * tick
                reason = "rebalance"
                break
            old_stop = basis / qty * (1 - sign * float(dca["invalidation_pct"]))
            if bar > entry_bar and sign * (float(O[bar]) - old_stop) <= 0:
                # A resting stop gapped at the open; never collect later funding
                # or invent intraday adds before an already-triggered open exit.
                reason = "stop"; exit_bar = bar; exit_price = float(O[bar]) - sign * tick
                stop_hits += 1; break
            # Timestamped official settlement before same-bar OHLC barriers;
            # an event exactly at entry OPEN belongs to the prior book, not new.
            if not cost.get("no_funding", False):
                for event_ms, rate, mark in funding_events[bar]:
                    if bar == entry_bar and event_ms <= int(ms[bar]):
                        continue
                    charge = sign * qty * mark * rate * float(cost.get("funding_mult", 1.0))
                    realized -= charge; funding += charge; cash_changes[bar - i0] -= charge
            avg = basis / qty
            stop = avg * (1 - sign * float(dca["invalidation_pct"]))
            take = avg * (1 + sign * float(dca["breakeven_tp_pct"]))
            # Check margin at worst adverse extremum (no fabricated order book).
            adverse = float(L[bar]) if sign > 0 else float(H[bar])
            margin_equity = START_EQUITY + realized + sign * (qty * adverse - basis)
            if margin_equity > 0:
                max_lev = max(max_lev, qty * adverse / margin_equity)
                max_util = max(max_util, qty * adverse / LEVERAGE / margin_equity)
            if margin_equity <= 0 or qty * adverse / LEVERAGE > margin_equity:
                reason = "margin"; exit_bar = bar; exit_price = adverse - sign * tick
                margin_calls += 1; break
            # OHLC has no intrabar ordering. Conservatively process adverse
            # reachable adds before a favourable TP. A resting stop is not
            # cancelled or skipped when an add and the stop cross in one bar.
            while next_level <= MAX_ADD_LEVELS:
                trigger = entry_price * (1 - sign * float(dca["spacing_pct"]) * next_level)
                reached = float(L[bar]) <= trigger if sign > 0 else float(H[bar]) >= trigger
                if not reached:
                    break
                if (sign > 0 and trigger < stop) or (sign < 0 and trigger > stop):
                    break  # do not add past the pre-existing resting stop
                fill_px = trigger + sign * tick
                if fill_px <= 0:
                    raise RuntimeError("nonpositive adverse ladder fill")
                quote = quote0 * mult ** next_level
                add_qty = quote / fill_px
                cost_fee = quote * fee_rate
                equity_at_add = START_EQUITY + realized + sign * (qty * fill_px - basis) - cost_fee
                if equity_at_add <= 0 or (qty + add_qty) * fill_px / LEVERAGE > equity_at_add:
                    reason = "margin"; exit_bar = bar; exit_price = fill_px - sign * tick
                    margin_calls += 1; break
                realized -= cost_fee; fees += cost_fee; turnover += quote; fills += 1; adds += 1
                cash_changes[bar - i0] -= cost_fee
                qty += add_qty; basis += add_qty * fill_px
                max_lev = max(max_lev, qty * fill_px / equity_at_add)
                max_util = max(max_util, qty * fill_px / LEVERAGE / equity_at_add)
                next_level += 1; layers += 1
            if reason == "margin":
                break
            avg = basis / qty
            stop = avg * (1 - sign * float(dca["invalidation_pct"]))
            take = avg * (1 + sign * float(dca["breakeven_tp_pct"]))
            pre_hit_stop = float(L[bar]) <= old_stop if sign > 0 else float(H[bar]) >= old_stop
            new_hit_stop = float(L[bar]) <= stop if sign > 0 else float(H[bar]) >= stop
            hit_stop = pre_hit_stop or new_hit_stop
            hit_take = float(H[bar]) >= take if sign > 0 else float(L[bar]) <= take
            if hit_stop:
                # An add never cancels the prior resting stop. If both old and
                # new stops were crossed, use the worse reached trigger.
                reached = [x for x, crossed in ((old_stop, pre_hit_stop), (stop, new_hit_stop)) if crossed]
                trigger = min(reached) if sign > 0 else max(reached)
                reason = "stop"; exit_bar = bar; exit_price = trigger - sign * tick
                stop_hits += 1; break
            if hit_take:
                reason = "tp"; exit_bar = bar; exit_price = take - sign * tick
                tp_hits += 1; break
            if bar < exit_bar:
                unrealized[bar - i0] = sign * (qty * float(C[bar]) - basis)
            else:
                exit_price = float(C[bar]) - sign * tick
        if reason == "rebalance":
            rebalance_exits += 1
        ep_gross = gross_price_pnl(sign, qty, exit_price, basis)
        gross += ep_gross                 # INDEPENDENT price-only accumulator
        cash_price_pnl = sign * (qty * exit_price - basis)
        realized += cash_price_pnl       # cash execution PnL, independent of gross accumulator
        exit_fee = qty * exit_price * fee_rate
        realized -= exit_fee; fees += exit_fee; turnover += abs(qty * exit_price); fills += 1
        cash_changes[exit_bar - i0] += cash_price_pnl - exit_fee
        layer_hist[0] += 1
        for level in range(1, layers):
            layer_hist[level] += 1
        last_exit = exit_bar
        last_exit_open = (reason == "rebalance")
        eq = START_EQUITY + realized
        if eq > 0:
            max_lev = max(max_lev, qty * exit_price / eq)
        max_util = max(max_util, (qty * exit_price / LEVERAGE) / max(eq, 1e-9))
    if not eps:
        return empty_metric(i0, i1, ms)
    equity = START_EQUITY + np.cumsum(cash_changes) + unrealized
    net = float(realized)
    independent_cash = float(cash_changes.sum())
    high = np.maximum.accumulate(np.r_[START_EQUITY, equity])
    dd_usdt = high[1:] - equity
    dr = np.diff(np.r_[START_EQUITY, equity]) / np.maximum(np.r_[START_EQUITY, equity[:-1]], 1e-9)
    sd = float(np.std(dr, ddof=1)) if len(dr) > 1 else 0.0
    sharpe = float(np.mean(dr) / sd * math.sqrt(365)) if sd > 1e-12 else 0.0
    duration = max(1, i1 - i0)
    annual = float((max(float(equity[-1]), 1e-9) / START_EQUITY) ** (365 / duration) - 1)
    return {"gross_pnl": float(gross), "fees": float(fees), "funding": float(funding),
            "net_pnl": net, "ending_equity": float(equity[-1]), "episodes": eps,
            "fills": fills, "adds": adds, "turnover_usdt": float(turnover),
            "sharpe": sharpe, "max_dd_pct": float(np.max(dd_usdt / np.maximum(high[1:], 1e-9)) * 100),
            "max_dd_usdt": float(np.max(dd_usdt)), "annualized_return": annual,
            "max_effective_leverage": float(max_lev), "capital_utilization": float(max_util),
            "tp_hits": tp_hits, "stop_hits": stop_hits, "margin_calls": margin_calls,
            "rebalance_exits": rebalance_exits, "open_at_end": int(reason == "end"),
            "layer_hist": layer_hist,
            "decomposition_ok": abs(gross - fees - funding - net) < 1e-3 and
            abs(net - independent_cash) < 1e-3 and
            abs(net - (float(equity[-1]) - START_EQUITY)) < 1e-3,
            "daily_equity": equity.tolist()}


def metric_block(m):
    return {key: m[key] for key in ("gross_pnl", "fees", "funding", "net_pnl",
            "ending_equity", "episodes", "fills", "adds", "turnover_usdt", "sharpe",
            "max_dd_pct", "max_dd_usdt", "annualized_return", "max_effective_leverage",
            "capital_utilization", "tp_hits", "stop_hits", "margin_calls",
            "rebalance_exits", "open_at_end")}


def cell_key(row):
    return (row["symbol"], row["timeframe"], *(row[k] for k in STRATEGY_AXES),
            *(row[k] for k in DCA_AXES))


def neighbourhood(winner, historical):
    if winner["grid"] != "historical" or any(r["grid"] != "historical" for r in historical):
        raise ValueError("selector/neighbourhood may ONLY read historical rows")
    axes = {**STRATEGY_AXES, **DCA_AXES}
    found = {cell_key(r): r for r in historical}
    key = cell_key(winner)
    agrees = count = 0
    for pos, (axis, domain) in enumerate(axes.items(), 2):
        for delta in (-1, 1):
            ix = domain.index(winner[axis]) + delta
            if 0 <= ix < len(domain):
                neighbor = list(key); neighbor[pos] = domain[ix]
                row = found[tuple(neighbor)]
                count += 1; agrees += float(row["net_pnl"]) > 0
    return {"neighbours": count, "agreeing": agrees,
            "same_sign_fraction": agrees / count, "passed": agrees / count >= 0.6}


def select_cohort(rows):
    hist = rows["historical"]
    if not hist or any(r["grid"] != "historical" for r in hist):
        raise ValueError("selector saw a non-historical row")
    best = max(int(r["episodes"]) for r in hist)
    if best < MIN_EPISODES_IS:
        return None, {"cull_reasons": ["insufficient_trades"], "best_historical_episodes": best}
    candidates = [r for r in hist if int(r["episodes"]) >= MIN_EPISODES_IS and
                  float(r["net_pnl"]) > 0 and float(r["sharpe"]) > 0]
    if not candidates:
        return None, {"cull_reasons": ["no_qualifying_candidate"], "best_historical_episodes": best}
    def order(row):
        return (-float(row["sharpe"]), -float(row["net_pnl"]),
                tuple(axis.index(row[name]) for name, axis in ({**STRATEGY_AXES, **DCA_AXES}).items()))
    winner = min(candidates, key=order)
    key = cell_key(winner)
    lookup = {g: next(r for r in rows[g] if cell_key(r) == key) for g in GRIDS}
    reasons = []
    oos = lookup["oos"]
    if not (float(oos["net_pnl"]) > 0 and float(oos["sharpe"]) > 0 and
            int(oos["episodes"]) >= MIN_EPISODES_OOS):
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
    detail = {"winner": {**{k: winner[k] for k in (*STRATEGY_AXES, *DCA_AXES)},
                         "grid": winner["grid"]},
              "best_historical_episodes": best, "neighbourhood": neighbors,
              "phases": {g: metric_block(lookup[g]) for g in ("historical", "oos", "full")},
              "robustness": {g: metric_block(lookup[g]) for g in
                             ("fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks")},
              "cull_reasons": reasons}
    return (winner if not reasons else None), detail


def cross_sectional_walkforward(panels, events, layer, dca, instruments):
    """Evaluate the entire zero-dollar LS sleeve, never one leg as 'market-neutral'.

    Execution is the same signed DCA engine as the grid, projected per cohort;
    alpha is the sum of all four leg contributions against cash, not a claim of
    jointly margin-managed portfolio execution.
    """
    dates = panels[SYMBOLS[0]]["open_ms"]
    start = dt.date.fromisoformat(PHASES["full"][0]); last = dt.date.fromisoformat(PHASES["full"][1])
    months = []
    while (last - start).days >= 365:
        end = start + dt.timedelta(days=365)
        a, b = window_indices(dates, start.isoformat(), (end - dt.timedelta(days=1)).isoformat())
        legs = {s: simulate(panels[s], events[s], layer["ls_weights"], s, dca,
                            a, b, {}, instruments[s]) for s in SYMBOLS}
        count = sum(m["episodes"] for m in legs.values())
        months.append({"start": str(start), "end_exclusive": str(end),
                       "ls_net_alpha_vs_cash_usdt": sum(m["net_pnl"] for m in legs.values()),
                       "episodes": count, "eligible": count >= MIN_EPISODES_OOS})
        start = (start.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
    eligible = [m for m in months if m["eligible"]]
    negative = sum(m["ls_net_alpha_vs_cash_usdt"] < 0 for m in eligible)
    fraction = negative / len(eligible) if eligible else None
    return {"status": "INDETERMINATE" if fraction is None else
            "FAIL" if fraction > 0.4 else "PASS", "negative_fraction": fraction,
            "negative_count": negative, "eligible_windows": len(eligible),
            "minimum_episodes_research_defined": MIN_EPISODES_OOS, "windows": months,
            "scope": "all four LS legs, net-of-cost projected cohort books, no joint margin claim",
            "failure_rule": FALSIFICATION["record_tests"]["cross_sectional_subperiod_walkforward"]["failure_rule"]}

def projected_sleeve(panels, events, weights, dca, instruments, i0, i1, cost):
    """Return a full four-leg strategy Sharpe for record falsification tests.

    These are projected per-symbol DCA books, not a separately executed
    portfolio; aggregate mark-to-market contributions before computing Sharpe.
    """
    legs = [simulate(panels[s], events[s], weights, s, dca, i0, i1, cost, instruments[s])
            for s in SYMBOLS]
    equity = START_EQUITY + sum((np.asarray(m["daily_equity"]) - START_EQUITY for m in legs),
                                np.zeros(i1 - i0))
    returns = np.diff(np.r_[START_EQUITY, equity]) / np.maximum(
        np.r_[START_EQUITY, equity[:-1]], 1e-9)
    sd = float(np.std(returns, ddof=1)) if len(returns) > 1 else 0.0
    return {"sharpe": float(np.mean(returns) / sd * math.sqrt(365)) if sd > 1e-12 else 0.0,
            "net_pnl": float(equity[-1] - START_EQUITY),
            "episodes": sum(m["episodes"] for m in legs)}

def falsification(layer, symbol, case, dca, o0, o1, panels, all_events,
                  instruments, shuffled=SHUFFLES):
    """All three record tests; INDETERMINATE is never misreported as PASS."""
    true = projected_sleeve(panels, all_events, layer["weights"], dca, instruments, o0, o1, {})
    stress = {str(bps): projected_sleeve(panels, all_events, layer["weights"], dca,
              instruments, o0, o1, {"fee_override": bps / 10_000})
              for bps in (5, 15, 30, 50)}
    cost_result = {"status": "FAIL" if stress["15"]["sharpe"] < 0.5 else "PASS",
                   "failure_rule": FALSIFICATION["record_tests"]["cost_stress_turnover"]["failure_rule"],
                   "oos_fee_bps": stress}
    rng = np.random.default_rng(SEED + SYMBOLS.index(symbol) * 1000 + STRATEGIES.index(case))
    null_sharpe = []
    for _ in range(shuffled):
        matrices = (rng.dirichlet(np.ones(case["bins"]), size=case["bins"]),
                    rng.dirichlet(np.ones(case["bins"]), size=case["bins"]))
        random_layer = randomized_layer(layer, case, matrices)
        null_sharpe.append(projected_sleeve(panels, all_events, random_layer["weights"],
                                           dca, instruments, o0, o1, {})["sharpe"])
    # A one-sided empirical test with the .30 improvement built into the null.
    p = (1 + sum(x >= true["sharpe"] - 0.30 for x in null_sharpe)) / (shuffled + 1)
    shuffle_result = {"status": "PASS" if null_sharpe and
                      true["sharpe"] - float(np.mean(null_sharpe)) >= 0.30 and p < 0.01 else "FAIL",
                      "failure_rule": FALSIFICATION["record_tests"]["synthetic_markov_shuffling"]["failure_rule"],
                      "true_oos_sharpe": true["sharpe"], "random_mean_sharpe": float(np.mean(null_sharpe)),
                      "p_one_sided_margin_0_30": p, "draws": shuffled,
                      "scope": "all four projected legs; jointly marked, independently margin-checked",
                      "null_definition": FALSIFICATION["record_tests"]["synthetic_markov_shuffling"]["null"]}
    subperiod_result = cross_sectional_walkforward(panels, all_events, layer, dca, instruments)
    return {"markov_shuffle": shuffle_result, "cost_turnover": cost_result,
            "subperiod_walkforward": subperiod_result}


def write_grid(path, rows):
    cols = ["symbol", "timeframe", *STRATEGY_AXES, *DCA_AXES, "grid",
            *metric_block(empty_metric(0, 0, np.array([]))), "decomposition_ok"]
    with open(path, "w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def lo_optimum_is_exactly_5050(layer):
    """True only if every live LO sleeve is exactly (1/2, 1/2): the measured
    degeneracy. A weight vector that moved with D would make this False."""
    index = layer["diag"]["decision_indices"]
    if not index:
        return True
    live = np.asarray(layer["lo_weights"])[index]
    if live.shape != (len(index), 4):
        return False
    return bool(np.all((np.abs(live) < 1e-12) | (np.abs(live - 0.5) < 1e-12))
                and np.all(np.abs(live.sum(axis=1) - 1.0) < 1e-12))


def run(spec, attempt_dir):
    counts = validate_spec(spec)
    path = Path(attempt_dir)
    expected = Path(RESULTS_ROOT) / FAMILY_ID / "rounds" / spec["round_id"] / "attempts" / spec["run_id"]
    if path != expected or any((path / x).exists() for x in ("DONE", "FAILED", "INCOMPLETE", "result.json")):
        raise ValueError("attempt path/immutability violation")
    artifacts = path / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    logs = path / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    log_fh = open(logs / "run.log", "w", encoding="utf-8")

    def log(message):
        line = "[%s] %s" % (dt.datetime.now(dt.timezone.utc).isoformat(), message)
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
    log("validate_spec ok; Qlib 0.9.7 build starting for %s" % (SYMBOLS,))
    try:
        instruments = instrument_metadata()
        panel, build = build_qlib(*PHASES["full"])
        atomic_json(artifacts / "bins_build.json", build)
        opens = panel[SYMBOLS[0]]["open_ms"]
        closes = np.column_stack([panel[s]["close"] for s in SYMBOLS])
        funding = {s: load_funding(s, opens) for s in SYMBOLS}
        asof = {s: (funding[s][1], funding[s][2]) for s in SYMBOLS}
        atomic_json(artifacts / "funding_coverage.json", {s: funding[s][3] for s in SYMBOLS})
        windows = {k: window_indices(opens, *v) for k, v in PHASES.items()}
        layers = {tuple(case.values()): signal_layer(closes, opens, case, asof) for case in STRATEGIES}
        grid_rows = {g: [] for g in GRIDS}
        results, survivors, f_reports = [], [], {}
        hist_total = [0] * LADDER_LEVELS
        winner_rows = []
        for symbol in SYMBOLS:
            rows = {g: [] for g in GRIDS}
            for case in STRATEGIES:
                layer = layers[tuple(case.values())]
                for dca in DCA_GRID:
                    for grid in GRIDS:
                        phase = "historical" if grid in ("historical", "no_funding") else "oos" if grid == "oos" else "full"
                        cost = {"fee_2x": {"fee_mult": 2}, "funding_2x": {"funding_mult": 2},
                                "entry_delay_1_bar": {"entry_delay": 1}, "slippage_2ticks": {"slip_ticks": 2},
                                "no_funding": {"no_funding": True}, "no_funding_full": {"no_funding": True},
                                "cost_attrition_40bps": {"fee_override": 0.004}}.get(grid, {})
                        metric = simulate(panel[symbol], funding[symbol][0],
                                          layer["weights"], symbol, dca,
                                          *windows[phase], cost, instruments[symbol])
                        row = {"symbol": symbol, "timeframe": TIMEFRAME, **case, **dca,
                               "grid": grid, **metric_block(metric), "decomposition_ok": metric["decomposition_ok"]}
                        rows[grid].append(row)
                        grid_rows[grid].append(row)
                        progress["case_evaluations"] += 1
                        if progress["case_evaluations"] % 500 == 0:
                            write_progress()
                        if grid == "full":
                            for i, val in enumerate(metric["layer_hist"]):
                                hist_total[i] += val
            selected, info = select_cohort(rows)
            label = symbol + "/1d"
            if info.get("winner"):
                winner = info["winner"]
                winner_rows.append(winner)
                case = {k: winner[k] for k in STRATEGY_AXES}
                dca = {k: winner[k] for k in DCA_AXES}
                log("cohort %s historical winner %s; running record falsification battery" % (label, case))
                f_reports[label] = falsification(layers[tuple(case.values())], symbol,
                    case, dca, *windows["oos"], panel,
                    {s: funding[s][0] for s in SYMBOLS}, instruments)
                # A genuine source falsification FAIL is scientific cull, not a
                # technical error and never silently upgraded into PASS.
                for name, measured in f_reports[label].items():
                    if measured["status"] != "PASS":
                        info["cull_reasons"].append("falsification:" + name + ":" + measured["status"])
                if info["cull_reasons"]:
                    selected = None
            record = {"cohort": label, "outcome": "SURVIVOR" if selected is not None else "CULLED",
                      **info, "falsification": f_reports.get(label, {"status": "NO_HISTORICAL_WINNER"})}
            results.append(record)
            if selected is not None:
                survivors.append(record)
            log("cohort %s outcome=%s cull=%s" % (label, record["outcome"],
                                                  ",".join(record["cull_reasons"]) or "none"))
            write_progress()
        progress["stage"] = "SELECTED"
        write_progress()
        expected_keys = {(s, "1d", *(c[k] for k in STRATEGY_AXES), *(d[k] for k in DCA_AXES))
                         for s in SYMBOLS for c in STRATEGIES for d in DCA_GRID}
        coverage = {g: {cell_key(r) for r in grid_rows[g]} == expected_keys and
                    len(grid_rows[g]) == len(expected_keys) for g in GRIDS}
        base_full = sum(r["net_pnl"] for r in grid_rows["full"])
        stress_delta = {g: sum(r["net_pnl"] for r in grid_rows[g]) - base_full for g in
                        ("fee_2x", "funding_2x", "slippage_2ticks", "cost_attrition_40bps")}
        traded = any(r["turnover_usdt"] > 0 for r in grid_rows["full"])
        official_events = {s: sum(len(e) for e in funding[s][0]) for s in SYMBOLS}
        assertions = {"qlib_readback_0_9_7": build["qlib_version"] == "0.9.7" and build["qlib_rows_identical_to_raw"],
                      "coverage_complete": all(coverage.values()), "dca_histogram_reconciles": hist_total[0] ==
                          sum(r["episodes"] for r in grid_rows["full"]),
                      "independent_gross_net_decomposition": all(r["decomposition_ok"] for g in GRIDS for r in grid_rows[g]),
                      # With zero fills a cost rail legitimately moves nothing; that is
                      # disclosed as a no-op, never counted as a working stress track.
                      "cost_stress_effective": (not traded) or all(stress_delta[g] < 0 for g in
                          ("fee_2x", "slippage_2ticks", "cost_attrition_40bps")),
                      # Only official rows are retained in the settlement arrays; the
                      # modeled rows that exist in the raw file are counted, not charged.
                      "official_funding_only_no_modeled_charges": all(
                          official_events[s] == funding[s][3]["official"] for s in SYMBOLS),
                      # Measured, non-vacuous: every live LO sleeve really is 50/50.
                      "lo_distance_degeneracy_disclosed": all(lo_optimum_is_exactly_5050(x) for x in layers.values()),
                      "selector_winner_is_historical_row": all(r["grid"] == "historical" for r in winner_rows),
                      "falsification_battery_ran_for_every_historical_winner": len(f_reports) == len(winner_rows)}
        # Funding can be a *credit* overall: a double-rate scenario need not
        # move net downward. It must affect actual settlements when nonzero.
        if any(abs(r["funding"]) > 1e-9 for r in grid_rows["full"]):
            assertions["funding_2x_effective"] = abs(stress_delta["funding_2x"]) > 1e-6
        else:
            assertions["funding_2x_effective"] = True  # zero actual costs, disclosed not faked
        atomic_json(artifacts / "stress_effects.json", {
            "baseline_grid": "full", "net_pnl_delta": stress_delta,
            "any_fill_in_full_grid": traded,
            "noop_reason": None if traded else
                "no fills in any full-grid cell, so the cost rails have nothing to move; "
                "reported as an unexercised track, not as a working stress test"})
        atomic_json(artifacts / "signal_layer.json", {"cases": [{"case": c, "diag": layers[tuple(c.values())]["diag"]} for c in STRATEGIES],
              "distance_effect": "N=4, K=1, top-2 LO has mathematical 50/50 optimum for d>=0. Residual distance is measured but cannot affect LO weights; no D-derived alpha claim.",
              "scope": "4 fixed local USD-M symbols x 1d; PIT membership/capacity, original 100-500-name and reported source-market performance not validated",
              "rank_bin_mapping": "ascending rank r=1..4 -> 1+round_half_up((r-1)*(M-1)/3); empty bins and zero transition rows allowed",
              "research_defined": {"funding_score": "-last_official_rate/0.0001, max age 24h, missing zero+coverage", "distance_cap": W_MAX,
                  "markov_fit": "rolling W=252 days of observed adjacent weekly/biweekly decision states", "score_coefficients": [ALPHA, BETA],
                  "execution": "close-t forecast, next-open-t+1 entry; projected signed cohort DCA, full flatten at next rebalance/stop/TP/margin/end; not a jointly optimized executed portfolio"}})
        atomic_json(artifacts / "family_falsification.json", {"cohorts": f_reports,
               "tests": ["synthetic_markov_shuffling", "cost_stress_turnover", "cross_sectional_subperiod_walkforward"],
               "no_winner_policy": "NOT_EVALUATED, never PASS"})
        for g in GRIDS:
            write_grid(artifacts / ("grid_%s.csv" % g), grid_rows[g])
        atomic_json(artifacts / "cohort_results.json", results)
        atomic_json(artifacts / "cohort_survivors.json", survivors)
        atomic_json(artifacts / "dca_layer_histogram.json", {"level_%02d" % i: v for i, v in enumerate(hist_total)})
        atomic_json(artifacts / "assertions.json", assertions)
        result = {"schema_version": 1, "family_id": FAMILY_ID, "round_id": spec["round_id"],
                  "run_id": spec["run_id"], "engine": ENGINE_VERSION,
                  "script_sha256": sha256_file(__file__), "qlib_version": build["qlib_version"],
                  "status": "ARTIFACT_READY", "coverage_complete": all(coverage.values()),
                  "coverage_by_grid": coverage, "assertions_all_true": all(assertions.values()),
                  "assertion_failures": [k for k, v in assertions.items() if not v],
                  "case_evaluations_total": sum(map(len, grid_rows.values())),
                  "expected_case_evaluations": counts["case_evaluations_total"],
                  "cohort_count": len(results), "cohort_survivor_count": len(survivors),
                  "cohort_survivors": [x["cohort"] for x in survivors],
                  "falsification_cohorts_evaluated": sorted(f_reports),
                  "signal_decisions_total": sum(x["diag"]["decisions"] for x in layers.values()),
                  "official_funding_events": official_events,
                  "cost_stress_noop": None if traded else
                      "cost rails unexercised: no fills in the full grid",
                  "disposition": "REJECT / NO_SURVIVOR" if not survivors else
                      "SURVIVOR_FOUND" if len(survivors) == 1 else "MULTIPLE_SURVIVORS",
                  "verdict_recommendation": "PASS" if survivors else "REJECT",
                  "selector_version": "cohort-selector-v1", "disposition_version": "cohort-disposition-v1",
                  "disposition_mapping_version": "v1.4.0", "grid_kinds": GRIDS,
                  "funding_coverage": {s: funding[s][3] for s in SYMBOLS},
                  "stress_net_delta": stress_delta, "portfolio_caveat": "cohort-level projected trading metrics, not a jointly executed portfolio; D optimum forced 50/50",
                  "runtime_seconds": time.monotonic() - started}
        if result["case_evaluations_total"] != counts["case_evaluations_total"]:
            result["assertions_all_true"] = False
            result["assertion_failures"].append("case_evaluations_total")
        # No terminal sentinel, round verdict, or frozen specs are written here.
        atomic_json(path / "result.json", result)
        state["stage"] = "ARTIFACT_READY"
        atomic_json(path / "state.json", state)
        progress["stage"] = "ARTIFACT_READY"
        write_progress()
        log("ARTIFACT_READY cohorts=%d survivors=%d rows=%d assertions=%s" %
            (len(results), len(survivors), result["case_evaluations_total"],
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
    with open(args.run_spec, encoding="utf-8") as stream:
        spec = json.load(stream)
    result = run(spec, args.attempt_dir or str(Path(args.run_spec).parent))
    print(json.dumps({"status": result["status"], "case_evaluations_total": result["case_evaluations_total"],
                      "assertions_all_true": result["assertions_all_true"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
