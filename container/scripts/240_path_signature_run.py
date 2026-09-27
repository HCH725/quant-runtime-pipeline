#!/usr/bin/env python3
"""Path Portfolio Optimization direct-family Qlib runner (round r1).

Registered record: Miquel Noguer i Alonso, "Path Portfolio Optimization:
Defect, Lift, and the Price of Path Complexity", arXiv:2608.02355v1
[q-fin.PM], 2026-08-03.

Registered mechanism (level-2 path signature of a d-asset log-price path):
  1. signature coordinates S^i = X_T^i - X_0^i and S^ij = int (X_t^i - X_0^i) dX_t^j
  2. defect form D(u,v) = E[<u,S><v,S>] - E[<u,S>]E[<v,S>]  (record Prop. 3/Cor. 6:
     the covariance of signature coordinate payoffs IS the defect form; the record's
     own Evidence section reports the defect form reproducing the empirical sample
     covariance with maximum relative error 0.0036)
  3. Marchenko-Pastur ridge shrinkage + risk-aversion allocation ell* = D_ridge^-1 mu / gamma
  4. dynamic feedback execution pi_t^j = ell_(j) + sum_i ell_(ij) (X_t^i - X_0^i)

Every execution detail the source does not specify is registered as
``research-defined`` in the frozen round/run spec; no core hypothesis, universe
or falsification threshold is changed here.

Contract: QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md v2.0.0.
Direct family: ownership is family_id + round_id + run_id only.
"""
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
import sys
import time
from pathlib import Path

import numpy as np

FAMILY_ID = "path-portfolio-optimization-signature-defect-lift-2026-09-02"
ENGINE_VERSION = "path_signature_qlib_v1"
RAW_USDM = "/data/raw/binance/usdm"
RAW_META = "/data/raw/_meta"
RESULTS_ROOT = "/results"  # the only tree run() may ever open
WORK_ROOT = "/qlib/work/path-signature-v1"
CSV_ROOT = os.path.join(WORK_ROOT, "csv")
QLIB_DIR = os.path.join(WORK_ROOT, "qlib-data")
INSTRUMENTS_PATH = RAW_USDM + "/instruments/usdm-perp-instruments.json"
VENV_PYTHON = "/opt/venv/bin/python"
DUMP_BIN = "/opt/qlib-tools/dump_bin.py"
MS_DAY = 86_400_000
FIELDS = ("open", "high", "low", "close", "volume")
SYMBOLS = ("BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT")
# Complete local raw interval set: every canonical intraday-to-weekly kline grid.
TIMEFRAMES = [
    {"raw_interval": "5m", "qlib_freq": "5min", "bars_per_day": 288, "bar_ms": 300_000},
    {"raw_interval": "15m", "qlib_freq": "15min", "bars_per_day": 96, "bar_ms": 900_000},
    {"raw_interval": "30m", "qlib_freq": "30min", "bars_per_day": 48, "bar_ms": 1_800_000},
    {"raw_interval": "1h", "qlib_freq": "60min", "bars_per_day": 24, "bar_ms": 3_600_000},
    {"raw_interval": "4h", "qlib_freq": "240min", "bars_per_day": 6, "bar_ms": 14_400_000},
    {"raw_interval": "1d", "qlib_freq": "day", "bars_per_day": 1, "bar_ms": MS_DAY},
    {"raw_interval": "1w", "qlib_freq": "week", "bars_per_day": 1, "bar_ms": 7 * MS_DAY},
]
TIMEFRAME_IDS = [tf["raw_interval"] for tf in TIMEFRAMES]
COHORTS = [(s, tf["raw_interval"]) for tf in TIMEFRAMES for s in SYMBOLS]

START_EQUITY = 30_000.0
BASE_QUOTE = 1_000.0
LEVERAGE = 10.0
# The registered 12th tranche is a reserve, never a routine order: entry + ten
# adverse scale-ins max, so active levels never exceed 11.  Slot 11 of the
# histogram therefore always measures zero.
MAX_ADD_LEVELS = 10
LADDER_LEVELS = 12

# research-defined strategy axes (source specifies neither): the registered
# trading windows T (record: "e.g., 1-hour or 4-hour rebalance horizons") map to
# 12 and 48 bars of the cohort's own clock, so on the 5m grid they are exactly
# 1h and 4h; gamma > 0 is required by the record with no numeric value.
PATH_BARS_AXIS = (12, 48)
GAMMA_AXIS = (0.5, 1.0)
STRATEGY_AXES = {"path_bars": PATH_BARS_AXIS, "gamma": GAMMA_AXIS}
STRATEGIES = tuple(dict(zip(STRATEGY_AXES, values)) for values in
                   itertools.product(*STRATEGY_AXES.values()))

# research-defined estimator controls: p = d + d^2 = 20 for d = 4.
M_CAP = 200          # record tests M/p in [0.5, 10.0] -> M in [10, 200]
M_MIN = 20           # decisions below M/p = 1.0 emit no signal (traced, not silent)
MIN_WEIGHT = 0.01    # research-defined minimum absolute target weight for a qualified signal
DEFECT_DIM = len(SYMBOLS) + len(SYMBOLS) ** 2
# Registered raw plug-in singularity gate (pre-registered in FALSIFICATION): a defect
# system this ill-conditioned has no numerically finite solution, which is exactly the
# "raw plug-in is singular" branch of the Shrinkage Boundary collapse definition.
RAW_CONDITION_LIMIT = 1.0e12

DCA_AXES = {"spacing_pct": (0.01, 0.02, 0.03, 0.04),
            "size_multiplier": (1.0, 1.1), "breakeven_tp_pct": (0.01, 0.02, 0.03),
            "invalidation_pct": (0.05, 0.10)}
DCA_GRID = tuple(dict(zip(DCA_AXES, values)) for values in
                 itertools.product(*DCA_AXES.values()))
GRIDS = ("historical", "oos", "full", "fee_2x", "funding_2x",
         "entry_delay_1_bar", "slippage_2ticks", "no_funding", "no_funding_full",
         "cost_attrition_40bps")
REQUIRED_STRESS = ("fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks")

MIN_EPISODES_IS = 10    # record states no threshold; frozen pipeline-standard floor
MIN_EPISODES_OOS = 3
MIN_NEIGHBOUR = 0.60
SEED = 260927
GATES = {"min_episodes_is": MIN_EPISODES_IS, "min_episodes_oos": MIN_EPISODES_OOS,
         "min_neighbour_same_sign_fraction": MIN_NEIGHBOUR}

PHASES = {"historical": ("2022-01-01", "2025-09-30"),
          "oos": ("2025-10-01", "2026-09-11"),
          "full": ("2022-01-01", "2026-09-11")}

# The record's own falsification battery, thresholds verbatim; carried in the
# frozen run/round spec before any compute (no post-hoc threshold edits).
FALSIFICATION = {
    "record_tests": {
        "levy_area_asymmetry": {
            "failure_rule": "If empirical mean cross-area is statistically indistinguishable "
                            "from zero (t-stat < 2.0) across all market regimes, the hypothesis "
                            "that path-dependent lead-lag delivers directional alpha is falsified.",
            "t_stat_threshold": 2.0, "path_window_bars": 12,
            "regimes": "calendar year of the window end bar",
            "pairs": "all 6 unordered pairs of the 4 local USD-M contracts",
            "scope": "every one of the 7 local kline intervals (superset of the record's example)",
            "windowing": "non-overlapping windows anchored at each window start",
            "evaluable_min_windows": 20,
            "falsified_when": "every evaluable (interval, pair, regime) cell has |t| < 2.0"},
        "shrinkage_boundary": {
            "failure_rule": "If raw plug-in does not exhibit catastrophic collapse at "
                            "M/p < 2.5, the analytical sample-complexity barrier is refuted.",
            "m_grid": [10, 20, 50, 100, 200], "p": DEFECT_DIM,
            "ratios": [0.5, 1.0, 2.5, 5.0, 10.0],
            "gamma": 1.0, "path_window_bars": 12,
            "raw_condition_limit": 1.0e12,
            "collapse_definition": "the raw plug-in system is singular (no finite solution), or "
                                   "its OOS four-leg projected-sleeve net Sharpe is negative AND "
                                   "at least 0.50 below the Marchenko-Pastur ridge sleeve net "
                                   "Sharpe at the same M",
            "falsified_when": "no M with M/p < 2.5 exhibits catastrophic collapse",
            "window": "OOS 2025-10-01..2026-09-11, causal trailing estimation sample"},
        "lift_ruin": {
            "failure_rule": "If forward-lifted continuous-time portfolio models fail to show "
                            "insolvency when geometric Marcus models remain solvent, the "
                            "execution lift gap theorem is falsified.",
            "seed": SEED, "paths": 10_000, "steps": 64, "step_drift": 0.0, "step_vol": 0.04,
            "jump_probability": 0.05, "jump_size": -0.30,
            "weights": [0.25, 0.25, 0.25, 0.25], "leverage": LEVERAGE,
            "forward_form": "W_fwd = prod_t (1 + leverage * sum_i w_i * r_{t,i})  (discrete Forward lift)",
            "marcus_form": "W_mar = exp(leverage * sum_t sum_i w_i * r_{t,i})  (geometric Marcus lift, strictly positive)",
            "returns": "arithmetic one-step returns of a 4-asset jump-diffusion with the market gap applied to every asset",
            "falsified_when": "no simulated path shows forward-lifted insolvency while the "
                              "Marcus/geometric lift stays strictly positive"}},
    "required_stress_grids": list(REQUIRED_STRESS),
    "failure_policy": "any FAIL or INDETERMINATE test culls every cohort as "
                      "falsification:<test>:<status>; never reworded into PASS",
}

ARTIFACTS = ("state.json", "result.json", "logs/run.log", "artifacts/progress.json",
             "artifacts/bins_build.json", "artifacts/assertions.json",
             "artifacts/stress_effects.json", "artifacts/cohort_results.json",
             "artifacts/cohort_survivors.json", "artifacts/signature_build.json",
             "artifacts/signal_layer.json", "artifacts/falsification_battery.json",
             "artifacts/dca_layer_histogram.json", "artifacts/funding_coverage.json",
             *(("artifacts/grid_%s.csv" % g) for g in GRIDS))


def expected_counts():
    return {"cohorts": len(COHORTS), "strategy_cases_per_cohort": len(STRATEGIES),
            "dca_configs_per_cohort": len(DCA_GRID),
            "base_combinations_per_cohort": len(STRATEGIES) * len(DCA_GRID),
            "case_evaluations_per_grid": len(COHORTS) * len(STRATEGIES) * len(DCA_GRID),
            "grid_count": len(GRIDS),
            "case_evaluations_total": len(COHORTS) * len(STRATEGIES) * len(DCA_GRID) * len(GRIDS)}


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def utc_ms(day):
    return int(dt.datetime.fromisoformat(day[:10]).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def utc_stamp(ms):
    return dt.datetime.fromtimestamp(int(ms) / 1000.0, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def stamp(ms, interval):
    # Intraday grids carry a full UTC timestamp (dump_bin's date field is free
    # text); 1d/1w keep the plain calendar day the shipped runners use.
    return utc_stamp(ms)[:10] if interval in ("1d", "1w") else utc_stamp(ms)


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


def signal_constants():
    return {"truncation_level": 2, "defect_dimension": DEFECT_DIM,
            "estimation_cap_paths": M_CAP, "estimation_min_paths": M_MIN,
            "min_weight": MIN_WEIGHT, "anchor_rule": "path anchor X_0 = log price at decision_bar - path_bars",
            "entry_rule": "level-2 feedback evaluated at the decision bar, executed next bar open",
            "seed": SEED}


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
        "script": {"path": "/scripts/240_path_signature_run.py", "sha256": sha256_file(here),
                   "deployed_host_path":
                       "/Users/hong/workspace/qlib-apple-container/scripts/240_path_signature_run.py"},
        "engine": {"name": ENGINE_VERSION, "script": "container/scripts/240_path_signature_run.py",
                   "container": "qlib-run", "qlib_version": "0.9.7", "seed": SEED,
                   "self_check": "container/scripts/tests/test_path_signature_engine.py",
                   "self_check_sha256": sha256_file(here.with_name("tests") / "test_path_signature_engine.py")},
        "data": {"venue": "Binance USD-M perpetual canonical raw archive",
                 "raw_root": RAW_USDM, "start": PHASES["full"][0], "end": PHASES["full"][1],
                 "historical_start": PHASES["historical"][0], "historical_end": PHASES["historical"][1],
                 "oos_start": PHASES["oos"][0], "oos_end": PHASES["oos"][1], "timezone": "UTC",
                 "fields": list(FIELDS), "symbols": list(SYMBOLS),
                 "timeframes": [dict(tf) for tf in TIMEFRAMES],
                 "point_in_time": "the 4 fixed local USD-M perpetual contracts; no membership/survivorship claim",
                 "missing_data": "absent bars stay absent; no fill and no resample"},
        "grids": list(GRIDS), "params": [dict(c) for c in STRATEGIES],
        "split": {"historical_start": PHASES["historical"][0], "historical_end": PHASES["historical"][1],
                  "oos_start": PHASES["oos"][0], "oos_end": PHASES["oos"][1],
                  "rule": "chronological; every signature window, defect estimate and funding "
                          "settlement is causal and strictly backward-looking from the decision "
                          "bar; OOS is read-only and never used for parameter selection"},
        "dca_domain": {**{k: list(v) for k, v in DCA_AXES.items()},
                       "grid": [dict(d) for d in DCA_GRID], "base_quote": BASE_QUOTE,
                       "base_quote_status": "PROJECT_PRE_REGISTERED_CONSTANT",
                       **{k + "_status": "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN" for k in DCA_AXES}},
        "selector_version": "cohort-selector-v1", "disposition_version": "cohort-disposition-v1",
        "expected": expected_counts(),
        "signal_constants": signal_constants(),
        "gates": dict(GATES),
        "costs": {"taker_fee": "canonical instrument taker_fee; doubled in fee_2x; overridden to 40 bps in cost_attrition_40bps",
                  "slippage_ticks": 1, "slippage_robustness_ticks": 2,
                  "slippage_basis": "canonical price_increment, adverse on every fill, both directions",
                  "funding": "official observations only, at their own timestamp and mark price; a missing official observation is zero cost for that interval with coverage disclosed; modeled rows are never charged"},
        "falsification": FALSIFICATION,
        "expected_outputs": list(ARTIFACTS),
        "notes": "direct family (handoff.execution=direct_hermes): ownership is family_id + "
                 "round_id + run_id only; no Kanban task/board keys are present by contract.",
    }


def validate_spec(spec, script_path=None, test_path=None, meta_path=None):
    """Fail before opening the result tree. Neither this function nor import writes anything."""
    if (spec.get("family_id") != FAMILY_ID
            or spec.get("selector_version") != "cohort-selector-v1"
            or spec.get("disposition_version") != "cohort-disposition-v1"):
        raise ValueError("wrong family/selector/disposition identity")
    rid = spec.get("round_id")
    if (not isinstance(rid, str)
            or not re.fullmatch(re.escape(FAMILY_ID) + r"-r[1-9][0-9]*", rid)
            or not isinstance(spec.get("run_id"), str)
            or not re.fullmatch(re.escape(rid) + r"-u[1-9][0-9]*", spec["run_id"])):
        raise ValueError("round/run identity mismatch")
    if any(k in spec for k in ("task_id", "kanban_task_id", "kanban_board")):
        raise ValueError("direct family must not carry task/board ids")
    if not spec.get("created_at_utc"):
        raise ValueError("missing created_at_utc")
    data = spec["data"]
    if (not str(data.get("raw_root", "")).startswith("/data/raw")
            or data.get("fields") != list(FIELDS)
            or data.get("symbols") != list(SYMBOLS)
            or data.get("timeframes") != [dict(tf) for tf in TIMEFRAMES]):
        raise ValueError("canonical local multi-timeframe panel mismatch")
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
        raise ValueError("strategy domain must be the exact 2x2 registered product")
    dca = spec["dca_domain"]
    if (any(not _same_axis(dca.get(k, ()), values) for k, values in DCA_AXES.items())
            or dca.get("grid") != list(DCA_GRID) or dca.get("base_quote") != BASE_QUOTE):
        raise ValueError("DCA domain incomplete/changed")
    if (dca.get("base_quote_status") != "PROJECT_PRE_REGISTERED_CONSTANT"
            or any(dca.get(k + "_status") != "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN" for k in DCA_AXES)):
        raise ValueError("DCA provenance mismatch")
    if spec.get("gates") != GATES:
        raise ValueError("selector gate mismatch")
    if spec.get("signal_constants") != signal_constants():
        raise ValueError("registered signal constants mismatch")
    counts = expected_counts()
    if spec.get("expected") != counts:
        raise ValueError("coverage counts not pinned: %r" % (spec.get("expected"),))
    if spec.get("expected_outputs") != list(ARTIFACTS):
        raise ValueError("expected artifact list not pinned")
    if spec.get("falsification") != FALSIFICATION:
        raise ValueError("all three record falsification tests must be pre-registered")
    script = spec["script"]
    if (script.get("path") != "/scripts/240_path_signature_run.py"
            or script.get("sha256") != sha256_file(script_path or __file__)):
        raise ValueError("script bytes do not match pinned script identity")
    engine = spec.get("engine", {})
    if engine.get("name") != ENGINE_VERSION or engine.get("qlib_version") != "0.9.7" or engine.get("seed") != SEED:
        raise ValueError("engine identity mismatch")
    check_path = Path(test_path) if test_path else (
        Path(__file__).resolve().with_name("tests") / "test_path_signature_engine.py")
    if engine.get("self_check_sha256") != sha256_file(check_path):
        raise ValueError("self-check bytes do not match pinned test identity")
    with open(meta_path or (RAW_META + "/CONFIG.json"), encoding="utf-8") as stream:
        catalog = json.load(stream)
    if (catalog.get("market_type") != "usdm_perp"
            or sorted(catalog["symbols"]) != sorted(SYMBOLS)
            or not set(TIMEFRAME_IDS) <= set(catalog["intervals"])
            or not {"klines", "funding"} <= set(catalog["datasets"])):
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
        result[symbol] = {"tick": tick, "taker_fee": fee, "source": path,
                          "source_sha256": sha256_file(path)}
    return result


# ---------------------------------------------------------------------------------------------
# canonical raw -> Qlib .bin (multi-timeframe) + official-only funding
# ---------------------------------------------------------------------------------------------

def raw_months(symbol, interval, start, end):
    directory = os.path.join(RAW_USDM, "klines", symbol, interval)
    if not os.path.isdir(directory):
        raise RuntimeError("missing raw directory: %s" % directory)
    lo, hi = start[:7], end[:7]
    prefix = "%s-%s-" % (symbol, interval)
    out = []
    for name in sorted(os.listdir(directory)):
        if name.startswith(prefix) and name.endswith(".jsonl.gz"):
            month = name[len(prefix):-len(".jsonl.gz")]
            if lo <= month <= hi:
                out.append(os.path.join(directory, name))
    if not out:
        raise RuntimeError("no raw months for %s %s in %s..%s" % (symbol, interval, start, end))
    return out


def write_csv(symbol, interval, qlib_freq, start, end):
    out_dir = os.path.join(CSV_ROOT, qlib_freq)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "%s.csv" % symbol)
    lo_ms, hi_ms = utc_ms(start), utc_ms(end) + MS_DAY - 1
    bar_ms = next(tf["bar_ms"] for tf in TIMEFRAMES if tf["raw_interval"] == interval)
    rows = 0
    first_ms = last_ms = None
    bad_grid = 0
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["date"] + list(FIELDS))
        for source in raw_months(symbol, interval, start, end):
            with gzip.open(source, "rt", encoding="utf-8") as gz:
                for line in gz:
                    if not line.strip():
                        continue
                    rec = json.loads(line)
                    ms = int(rec["open_time_ms"])
                    if ms < lo_ms or ms > hi_ms:
                        continue
                    if last_ms is not None and ms - last_ms != bar_ms:
                        bad_grid += 1
                    if any(not math.isfinite(float(rec[k])) for k in FIELDS):
                        raise RuntimeError("nonfinite raw OHLCV for %s %s" % (symbol, interval))
                    writer.writerow([stamp(ms, interval)] + [rec[k] for k in FIELDS])
                    first_ms = ms if first_ms is None else first_ms
                    last_ms = ms
                    rows += 1
    if rows < 3:
        raise RuntimeError("too few raw rows for %s %s: %d" % (symbol, interval, rows))
    # Frequency-aware completeness guard: a missing/partial raw month fails closed
    # instead of silently shortening the sample.
    expected_rows = (hi_ms - lo_ms + 1) / float(bar_ms)
    if rows < 0.5 * expected_rows:
        raise RuntimeError("raw coverage too thin for %s %s: rows=%d expected~%.0f"
                           % (symbol, interval, rows, expected_rows))
    return {"rows": rows, "first_open_time_ms": first_ms, "last_open_time_ms": last_ms,
            "off_grid_steps": bad_grid, "csv": out_path, "csv_bytes": os.path.getsize(out_path),
            "bar_step_ms": bar_ms}


def csv_float_columns(path, fields=FIELDS):
    """Whole-file float read-back of the generated CSV, in CSV row order (P1 evidence)."""
    out = {f: [] for f in fields}
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        if header != ["date"] + list(fields):
            raise RuntimeError("unexpected CSV header for %s: %r" % (path, header))
        for row in reader:
            if len(row) != 1 + len(fields):
                raise RuntimeError("ragged CSV row in %s" % path)
            for i, f in enumerate(fields, start=1):
                out[f].append(float(row[i]))
    return {f: np.asarray(v, dtype=np.float64) for f, v in out.items()}


def csv_first_last_close(path):
    with open(path, newline="", encoding="utf-8") as fh:
        rows = csv.DictReader(fh)
        first = next(rows)
        last = first
        for last in rows:
            pass
    return float(first["close"]), float(last["close"])


def qlib_week_aliases():
    """Expose dump_bin's weekly files under Qlib 0.9.7's normalized names."""
    calendar = os.path.join(QLIB_DIR, "calendars", "week.txt")
    if os.path.isfile(calendar):
        shutil.copyfile(calendar, os.path.join(QLIB_DIR, "calendars", "1week.txt"))
    copied = 0
    for directory in Path(os.path.join(QLIB_DIR, "features")).glob("*"):
        for source in directory.glob("*.week.bin"):
            target = source.with_name(source.name.replace(".week.bin", ".1week.bin"))
            shutil.copyfile(source, target)
            copied += 1
    if not os.path.isfile(os.path.join(QLIB_DIR, "calendars", "1week.txt")) or copied != len(SYMBOLS) * len(FIELDS):
        raise RuntimeError("weekly Qlib normalized aliases incomplete: feature_files=%d" % copied)
    return copied


def build_qlib(spec, attempt_dir, log):
    """Raw JSONL.gz -> CSV -> dump_bin -> qlib.data.D readback (never host-pandas evidence)."""
    started = time.time()
    if os.path.isdir(WORK_ROOT):
        shutil.rmtree(WORK_ROOT)
    os.makedirs(CSV_ROOT, exist_ok=True)
    per_dataset, panels = {}, {}
    for tf in TIMEFRAMES:
        for symbol in SYMBOLS:
            key = "%s/%s" % (symbol, tf["raw_interval"])
            report = write_csv(symbol, tf["raw_interval"], tf["qlib_freq"],
                               spec["data"]["start"], spec["data"]["end"])
            if report["off_grid_steps"]:
                raise RuntimeError("non-contiguous raw grid for %s: %r" % (key, report["off_grid_steps"]))
            per_dataset[key] = report
        cmd = [VENV_PYTHON, DUMP_BIN, "dump_all",
               "--data_path", os.path.join(CSV_ROOT, tf["qlib_freq"]),
               "--qlib_dir", QLIB_DIR, "--freq", tf["qlib_freq"],
               "--include_fields", "open,close,high,low,volume",
               "--date_field_name", "date", "--max_workers", "1"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=14400)
        if proc.returncode:
            raise RuntimeError("dump_bin %s failed rc=%d\n%s\n%s"
                               % (tf["qlib_freq"], proc.returncode, proc.stdout[-2000:],
                                  proc.stderr[-2000:]))
        log("dump_bin %s ok (%s)" % (tf["qlib_freq"], tf["raw_interval"]))
        if tf["raw_interval"] == "1w":
            log("weekly Qlib aliases=%d" % qlib_week_aliases())
    import qlib
    from qlib.data import D
    if qlib.__version__ != "0.9.7":
        raise RuntimeError("Qlib version mismatch: %s" % qlib.__version__)
    qlib.init(provider_uri=QLIB_DIR, region="cn", expression_cache=None, dataset_cache=None)
    readback = {}
    for tf in TIMEFRAMES:
        panels[tf["raw_interval"]] = {}
        for symbol in SYMBOLS:
            df = D.features([symbol], ["$" + f for f in FIELDS],
                            start_time=spec["data"]["start"],
                            end_time=spec["data"]["end"] + " 23:59:59",
                            freq=tf["qlib_freq"]).sort_index()
            if len(df) == 0 or int(df.isnull().sum().sum()) != 0:
                raise RuntimeError("Qlib read-back empty/null for %s %s" % (symbol, tf["raw_interval"]))
            times = np.array([int(ts.value // 1_000_000)
                              for ts in df.index.get_level_values("datetime")], dtype=np.int64)
            if np.any(np.diff(times) <= 0):
                raise RuntimeError("Qlib timestamps not strict ascending for %s %s"
                                   % (symbol, tf["raw_interval"]))
            values = {f: df["$" + f].to_numpy(dtype=np.float64) for f in FIELDS}
            if any(not np.isfinite(v).all() for v in values.values()):
                raise RuntimeError("nonfinite Qlib values for %s %s" % (symbol, tf["raw_interval"]))
            if not np.all((values["open"] > 0) & (values["close"] > 0) & (values["low"] > 0)):
                raise RuntimeError("nonpositive Qlib prices for %s %s" % (symbol, tf["raw_interval"]))
            key = "%s/%s" % (symbol, tf["raw_interval"])
            if len(times) != per_dataset[key]["rows"]:
                raise RuntimeError("Qlib row count != raw CSV rows for %s" % key)
            csv_values = csv_float_columns(per_dataset[key]["csv"])
            rel = {}
            for field in FIELDS:
                denom = np.maximum(1.0, np.abs(csv_values[field]))
                rel[field] = float(np.max(np.abs(values[field] - csv_values[field]) / denom))
            # Qlib dump_bin stores feature bins as float32, so CSV -> .bin quantises every
            # field on every row. The read-back gate is therefore a relative float32
            # round-trip bound over the WHOLE series, not a claim of bit equality.
            worst = max(rel.values())
            if worst > 1e-6:
                raise RuntimeError("Qlib/CSV mismatch beyond float32 round-trip for %s: %r"
                                   % (key, rel))
            readback[key] = {"rows": len(times),
                             "first": stamp(times[0], tf["raw_interval"]),
                             "last": stamp(times[-1], tf["raw_interval"]),
                             "max_field_rel_diff": worst, "per_field_rel_diff": rel,
                             "tolerance": "relative 1e-6 (float32 dump_bin storage, ~16 ulp)",
                             "rows_compared": int(len(times)) * len(FIELDS)}
            panels[tf["raw_interval"]][symbol] = {"symbol": symbol,
                                                  "timeframe": tf["raw_interval"],
                                                  "qlib_freq": tf["qlib_freq"],
                                                  "open_ms": times, **values}
    for tf in TIMEFRAMES:
        grid = panels[tf["raw_interval"]][SYMBOLS[0]]["open_ms"]
        for symbol in SYMBOLS[1:]:
            if not np.array_equal(grid, panels[tf["raw_interval"]][symbol]["open_ms"]):
                raise RuntimeError("cross-section timestamp mismatch %s/%s"
                                   % (symbol, tf["raw_interval"]))
    report = {"qlib_dir": QLIB_DIR, "csv_root": CSV_ROOT, "qlib_version": qlib.__version__,
              "read_path": "qlib.data.D.features",
              "build_wall_seconds": round(time.time() - started, 3),
              "qlib_rows_identical_to_raw": True,
              "per_dataset": {k: {x: v for x, v in r.items() if x != "raw_files"}
                              for k, r in per_dataset.items()},
              "readback": readback,
              "readback_policy": "every row of every field compared against the generated "
                                 "CSV with a relative 1e-6 (float32 dump_bin) bound",
              "note": "raw JSONL.gz -> Qlib CSV -> Qlib .bin; no resampling, no fill, no rewrite"}
    atomic_json(os.path.join(attempt_dir, "artifacts", "bins_build.json"), report)
    return panels, report


def load_funding(symbol, open_ms):
    """No synthetic settlements: only official events are charged, exact official mark price."""
    path = Path(RAW_USDM) / "funding" / symbol / (symbol + "-funding.jsonl.gz")
    if not path.is_file():
        # Lifecycle footer: absent official observations are zero cost with the gap
        # disclosed, never a launch blocker and never a modeled replacement.
        return ([[] for _ in open_ms], {"official": 0, "modeled_ignored": 0, "other_ignored": 0,
                                        "out_of_window": 0, "bars_with_official": 0,
                                        "bars_total": len(open_ms), "file_missing": True,
                                        "source_sha256": None,
                                        "missing_funding_intervals_are_zero_not_modeled": True})
    events = [[] for _ in open_ms]
    seen = set()
    report = {"official": 0, "modeled_ignored": 0, "other_ignored": 0, "out_of_window": 0,
              "file_missing": False, "missing_funding_intervals_are_zero_not_modeled": True,
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
            if t in seen:
                raise RuntimeError("duplicate official funding observation")
            seen.add(t)
            events[bar].append((t, rate, mark))
            report["official"] += 1
    report["bars_with_official"] = sum(bool(e) for e in events)
    report["bars_total"] = len(events)
    report["official_events_per_bar"] = report["official"] / max(1, len(events))
    return events, report


def window_indices(open_ms, start, end):
    lo = int(np.searchsorted(open_ms, utc_ms(start), side="left"))
    hi = int(np.searchsorted(open_ms, utc_ms(end) + MS_DAY - 1, side="right"))
    return lo, hi


# ---------------------------------------------------------------------------------------------
# level-2 path signature + defect form + dynamic feedback (the registered mechanism)
# ---------------------------------------------------------------------------------------------

def signature_bank(logcloses, path_bars, chunk=8000):
    """Level-<=2 signature coordinate vector for every trailing window of ``path_bars`` bars.

    S^i  = X_T^i - X_0^i                      (record section 1)
    S^ij = sum_k X_{t_k}^i * (X_{t_k+1}^j - X_{t_k}^j)   (record section 1, forward lift)

    ``logcloses`` is (N, d) natural log prices; window with endpoint ``e`` covers
    bars ``[e - path_bars + 1, e]`` anchored at its own first bar, so the returned
    array is strictly causal: index j corresponds to endpoint e = j + path_bars - 1.
    Returns (E, p) with p = d + d^2.
    """
    logcloses = np.asarray(logcloses, dtype=np.float64)
    n, d = logcloses.shape
    length = int(path_bars)
    if length < 2 or n < length + 1:
        raise ValueError("path window %d unusable for %d bars" % (length, n))
    windows = np.lib.stride_tricks.sliding_window_view(logcloses, length, axis=0)
    out = np.empty((n - length + 1, d + d * d), dtype=np.float64)
    for start in range(0, len(out), chunk):
        stop = min(len(out), start + chunk)
        win = np.ascontiguousarray(windows[start:stop]).transpose(0, 2, 1)  # (m, L, d)
        anchored = win - win[:, :1, :]                                      # X_0 = 0
        inc = np.diff(anchored, axis=1)                                     # (m, L-1, d)
        left = anchored[:, :-1, :]                                          # value at each step start
        level1 = anchored[:, -1, :]
        level2 = np.einsum("mki,mkj->mij", left, inc).reshape(stop - start, d * d)
        out[start:stop] = np.concatenate([level1, level2], axis=1)
    if not np.isfinite(out).all():
        raise RuntimeError("nonfinite signature bank")
    return out


def decision_bars(n, path_bars, minimum_samples=M_MIN):
    """Rebalance instants: multiples of path_bars with a causal M >= minimum behind them."""
    minimum = max(2, int(minimum_samples))
    first = int(path_bars) * int(math.ceil((path_bars + minimum - 1) / float(path_bars)))
    if first >= n:
        return np.zeros(0, dtype=np.int64)
    return np.arange(first, n, int(path_bars), dtype=np.int64)


def allocate(block, gamma, shrinkage="ridge"):
    """Defect form -> Marchenko-Pastur ridge (or raw plug-in) -> risk-averse weight vector.

    block is (M, p) of level-2 signature coordinate payoffs.  D is their 1/M
    covariance, i.e. the defect form estimator of record Prop. 3.
    Returns (ell, info); ell is None when the estimator has no finite solution.
    """
    m, p = block.shape
    mean = block.mean(axis=0)
    second = block.T @ block / float(m)
    defect = second - np.outer(mean, mean)
    trace = float(np.trace(defect))
    info = {"m": int(m), "p": int(p), "trace": trace, "shrinkage": shrinkage}
    if trace <= 0.0 or not math.isfinite(trace):
        info["reason"] = "zero_trace"
        return None, info
    if shrinkage == "ridge":
        delta = math.sqrt(p / float(m)) * (trace / p)
        system = defect + delta * np.eye(p)
        info["ridge_delta"] = delta
    elif shrinkage == "raw":
        system = defect
        # Registered rule: the raw plug-in has "no finite solution" exactly when
        # its defect system is numerically singular (rank <= M-1 once M <= p).
        try:
            condition = float(np.linalg.cond(system))
        except (np.linalg.LinAlgError, ValueError):
            info["reason"] = "singular"
            return None, info
        info["condition_number"] = condition if math.isfinite(condition) else None
        if not math.isfinite(condition) or condition > RAW_CONDITION_LIMIT:
            info["reason"] = "singular"
            return None, info
    else:
        raise ValueError("unknown shrinkage " + str(shrinkage))
    try:
        ell = np.linalg.solve(system, mean) / float(gamma)
    except np.linalg.LinAlgError:
        info["reason"] = "singular"
        return None, info
    if not np.isfinite(ell).all():
        info["reason"] = "nonfinite"
        return None, info
    return ell, info


def signal_layer(logcloses, bank, path_bars, gamma, m_cap=None, m_min=None, shrinkage="ridge"):
    """Causal dynamic-feedback layer for one (interval, path_bars, gamma) triple.

    At decision bar t the estimation sample is the M <= M_CAP trailing windows that
    END strictly before t (window endpoints L-1 .. t-1), so nothing at or after t is
    ever read.  The registered feedback anchor X_0 is the start of the just-completed
    path window, i.e. bar t - path_bars.

    ``m_cap`` / ``m_min`` / ``shrinkage`` exist so the record's Shrinkage Boundary
    falsification test can replay the identical layer at a fixed M and allocator;
    the registered grid always runs with the module defaults.

    Returns:
      weights   (N, d)  target weight used for entry (0 off decision bars / below MIN_WEIGHT)
      feedback  (N, d)  pi_s under the same anchor, used for the registered sign-flip exit
      decisions (K,)    rebalance instants, used for the scheduled next-open flatten
      diag              causal sample counts, solve failures, weight coverage
    """
    n, d = logcloses.shape
    p = bank.shape[1]
    cap = M_CAP if m_cap is None else int(m_cap)
    floor = M_MIN if m_min is None else min(int(m_min), cap)
    weights = np.zeros((n, d), dtype=np.float64)
    feedback = np.zeros((n, d), dtype=np.float64)
    bars = decision_bars(n, path_bars, floor)
    samples, solve_fails, zero_signal = [], 0, 0
    for t in bars:
        available = int(t) - int(path_bars) + 1          # endpoints L-1 .. t-1
        m = min(cap, available)
        if m < floor:
            continue                                     # registered warm-up, traced in diag
        high = int(t) - int(path_bars)                   # index of endpoint t-1
        block = bank[high - m + 1:high + 1]
        ell, info = allocate(block, gamma, shrinkage)
        if ell is None:
            solve_fails += 1
            continue
        samples.append(info["m"])
        level1 = ell[:d]
        level2 = ell[d:].reshape(d, d)
        anchor = logcloses[int(t) - int(path_bars)]
        stop = min(n, int(t) + int(path_bars))
        segment = logcloses[int(t):stop]
        pi = level1 + (segment - anchor) @ level2.T
        feedback[int(t):stop] = pi
        entry_target = pi[0]                 # registered rule: feedback at the decision bar
        qualified = np.abs(entry_target) >= MIN_WEIGHT
        if not bool(qualified.any()):
            zero_signal += 1
            continue
        weights[int(t)] = np.where(qualified, np.clip(entry_target, -1.0, 1.0), 0.0)
    diag = {"decisions": int(len(bars)), "estimations": len(samples),
            "solve_failures": solve_fails, "below_min_weight": zero_signal,
            "shrinkage": shrinkage, "m_cap": cap, "m_min": floor,
            "mean_estimation_paths": float(np.mean(samples)) if samples else 0.0,
            "min_estimation_paths": int(min(samples)) if samples else 0,
            "max_estimation_paths": int(max(samples)) if samples else 0,
            "causal": True,
            "causality_rule": "every estimation window endpoint < decision bar; anchor = decision - path_bars",
            "decisions_with_signal": int(np.count_nonzero(np.any(weights, axis=1))),
            "weight_abs_mean": float(np.abs(weights).mean()),
            "feedback_nonzero_bars": int(np.count_nonzero(np.any(feedback, axis=1)))}
    return {"weights": weights, "feedback": feedback, "decisions": bars, "diag": diag}


# ---------------------------------------------------------------------------------------------
# registered DCA execution rail (per-fill fee/funding accounting, independent gross ledger)
# ---------------------------------------------------------------------------------------------

def empty_metric(i0, i1):
    return {"gross_pnl": 0.0, "fees": 0.0, "funding": 0.0, "net_pnl": 0.0,
            "ending_equity": START_EQUITY, "episodes": 0, "fills": 0, "adds": 0,
            "turnover_usdt": 0.0, "sharpe": 0.0, "max_dd_pct": 0.0,
            "max_dd_usdt": 0.0, "annualized_return": 0.0, "max_effective_leverage": 0.0,
            "capital_utilization": 0.0, "tp_hits": 0, "stop_hits": 0,
            "margin_calls": 0, "rebalance_exits": 0, "feedback_exits": 0,
            "open_at_end": 0, "layer_hist": [0] * LADDER_LEVELS,
            "decomposition_ok": True,
            "daily_equity": [START_EQUITY] * max(0, i1 - i0)}


def gross_price_pnl(sign, quantity, price, basis):
    """Independent price-only ledger, deliberately separate from cash's PnL expression."""
    return sign * (quantity * price - basis)


def simulate(panel, funding_events, layer, symbol, dca, i0, i1, cost, instrument):
    """Per-fill DCA rail for one (cohort, strategy, DCA, grid, phase) cell.  No RNG.

    Episode semantics are the registered rail: entry at the next bar's open (plus the
    registered entry_delay), up to MAX_ADD_LEVELS adverse scale-ins behind a resting
    breakeven-anchored stop, reduce-only flatten, and a portfolio transition at the
    open of the bar after the next decision (exit processed before entry; an entry is
    refused while a book is open and on the exit bar of any intrabar exit).

    Two properties belong to this record only:
      * ``layer["feedback"]`` is the registered dynamic-execution target
        pi_s = ell1 + ell2 . (X_s - X_0).  When its sign turns opposite to the
        holding, the book flattens reduce-only ("feedback" exit).
      * ``layer["decisions"]`` is the panel's full decision grid, so a symbol whose
        target has gone to zero still flattens on schedule.
    The feedback is read at each bar's close, so it never looks at a future bar.

    Sizing is base_quote x leverage x |target weight|, so a cohort cell's gross notional
    equals base_quote x leverage when the weight is 1.  Fees are debited at the fill
    instant, official funding is charged at its own timestamps, and the gross ledger is
    an independent price-only accumulator (never derived from net).
    """
    O, H, L, C = (panel[f] for f in ("open", "high", "low", "close"))
    ms = panel["open_ms"]
    tick = instrument["tick"] * int(cost.get("slip_ticks", 1))
    fee_rate = float(cost.get("fee_override",
                              instrument["taker_fee"] * cost.get("fee_mult", 1.0)))
    if fee_rate < 0 or i1 <= i0 or i0 < 0 or i1 > len(ms):
        raise ValueError("invalid cost/window")
    mult = float(dca["size_multiplier"])
    cash_changes = np.zeros(i1 - i0)
    unrealized = np.zeros(i1 - i0)
    gross = fees = funding = turnover = realized = 0.0
    eps = fills = adds = tp_hits = stop_hits = 0
    margin_calls = rebalance_exits = feedback_exits = 0
    max_lev = max_util = 0.0
    layer_hist = [0] * LADDER_LEVELS
    is_dict = isinstance(layer, dict)
    weights = layer["weights"] if is_dict else layer
    feedback = layer.get("feedback") if is_dict else None
    decisions = layer["decisions"] if is_dict else np.flatnonzero(np.any(layer, axis=1))
    idx = SYMBOLS.index(symbol)
    signals = np.flatnonzero(weights[:, idx])
    last_exit = i0 - 1
    last_exit_open = False
    reason = "end"
    for si, t in enumerate(signals):
        if t < i0 - 1 or t >= i1 - 1:
            continue
        entry_bar = int(t) + 1 + int(cost.get("entry_delay", 0))
        if (entry_bar >= i1 or entry_bar < last_exit
                or (entry_bar == last_exit and not last_exit_open)):
            continue
        weight = float(weights[t, idx])
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
        # flatten on the registered decision grid, not only on this symbol's own
        # nonzero targets, so removal from the portfolio still closes the book.
        future = decisions[decisions > t]
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
            # Registered exit: the dynamic-feedback target turning against the
            # holding flattens the book reduce-only at this bar's close.
            if feedback is not None and sign * float(feedback[bar, idx]) <= 0.0:
                reason = "feedback"; exit_bar = bar
                exit_price = float(C[bar]) - sign * tick
                feedback_exits += 1; break
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
                equity_at_add = (START_EQUITY + realized
                                 + sign * (qty * fill_px - basis) - cost_fee)
                if equity_at_add <= 0 or (qty + add_qty) * fill_px / LEVERAGE > equity_at_add:
                    reason = "margin"; exit_bar = bar; exit_price = fill_px - sign * tick
                    margin_calls += 1; break
                realized -= cost_fee; fees += cost_fee; turnover += quote
                fills += 1; adds += 1
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
                reached = [x for x, crossed in ((old_stop, pre_hit_stop), (stop, new_hit_stop))
                           if crossed]
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
        return empty_metric(i0, i1)
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
            "sharpe": sharpe,
            "max_dd_pct": float(np.max(dd_usdt / np.maximum(high[1:], 1e-9)) * 100),
            "max_dd_usdt": float(np.max(dd_usdt)), "annualized_return": annual,
            "max_effective_leverage": float(max_lev),
            "capital_utilization": float(max_util),
            "tp_hits": tp_hits, "stop_hits": stop_hits, "margin_calls": margin_calls,
            "rebalance_exits": rebalance_exits, "feedback_exits": feedback_exits,
            "open_at_end": int(reason == "end"),
            "layer_hist": layer_hist,
            "decomposition_ok": abs(gross - fees - funding - net) < 1e-3 and
            abs(net - independent_cash) < 1e-3 and
            abs(net - (float(equity[-1]) - START_EQUITY)) < 1e-3,
            "daily_equity": equity.tolist()}


def metric_block(m):
    return {key: m[key] for key in ("gross_pnl", "fees", "funding", "net_pnl",
                                    "ending_equity", "episodes", "fills", "adds",
                                    "turnover_usdt", "sharpe", "max_dd_pct", "max_dd_usdt",
                                    "annualized_return", "max_effective_leverage",
                                    "capital_utilization", "tp_hits", "stop_hits",
                                    "margin_calls", "rebalance_exits", "feedback_exits",
                                    "open_at_end")}


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
                neighbour = list(key)
                neighbour[pos] = domain[ix]
                row = found[tuple(neighbour)]
                count += 1
                agrees += float(row["net_pnl"]) > 0
    return {"neighbours": count, "agreeing": agrees,
            "same_sign_fraction": agrees / count if count else 0.0,
            "passed": bool(count) and agrees / count >= MIN_NEIGHBOUR}


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
    axes = {**STRATEGY_AXES, **DCA_AXES}

    def order(row):
        return (-float(row["sharpe"]), -float(row["net_pnl"]),
                tuple(axis.index(row[name]) for name, axis in axes.items()))

    winner = min(candidates, key=order)
    key = cell_key(winner)
    lookup = {g: next(r for r in rows[g] if cell_key(r) == key) for g in GRIDS}
    reasons = []
    oos = lookup["oos"]
    if not (float(oos["net_pnl"]) > 0 and float(oos["sharpe"]) > 0
            and int(oos["episodes"]) >= MIN_EPISODES_OOS):
        reasons.append("oos_economic")
    if float(lookup["full"]["net_pnl"]) <= 0:
        reasons.append("full_economic")
    weak = [g for g in REQUIRED_STRESS if float(lookup[g]["net_pnl"]) <= 0]
    if weak:
        reasons.append("robustness_economic:" + ",".join(weak))
    neighbours = neighbourhood(winner, hist)
    if not neighbours["passed"]:
        reasons.append("parameter_neighbourhood")
    detail = {"winner": {**{k: winner[k] for k in (*STRATEGY_AXES, *DCA_AXES)},
                         "grid": winner["grid"]},
              "best_historical_episodes": best, "neighbourhood": neighbours,
              "phases": {g: metric_block(lookup[g]) for g in ("historical", "oos", "full")},
              "robustness": {g: metric_block(lookup[g]) for g in REQUIRED_STRESS},
              "cull_reasons": reasons}
    return (winner if not reasons else None), detail


def projected_sleeve(panel_tf, funding_tf, layer, dca, i0, i1, cost, instruments):
    """Four-leg jointly marked sleeve Sharpe; the same projected cohort engine as the grid."""
    legs = [simulate(panel_tf[s], funding_tf[s][0], layer, s, dca, i0, i1, cost, instruments[s])
            for s in SYMBOLS]
    equity = START_EQUITY + sum((np.asarray(m["daily_equity"]) - START_EQUITY for m in legs),
                                np.zeros(max(0, i1 - i0)))
    returns = np.diff(np.r_[START_EQUITY, equity]) / np.maximum(
        np.r_[START_EQUITY, equity[:-1]], 1e-9)
    sd = float(np.std(returns, ddof=1)) if len(returns) > 1 else 0.0
    return {"sharpe": float(np.mean(returns) / sd * math.sqrt(365)) if sd > 1e-12 else 0.0,
            "net_pnl": float(equity[-1] - START_EQUITY),
            "episodes": sum(m["episodes"] for m in legs),
            "decomposition_ok": all(m["decomposition_ok"] for m in legs)}


# ---------------------------------------------------------------------------------------------
# record falsification battery (three tests, thresholds verbatim from the record)
# ---------------------------------------------------------------------------------------------

def levy_area_test(panels, cfg):
    """Record test 1: sample cross-area A^ij is indistinguishable from zero everywhere."""
    length = int(cfg["path_window_bars"])
    threshold = float(cfg["t_stat_threshold"])
    need = int(cfg["evaluable_min_windows"])
    cells = []
    for tf in TIMEFRAMES:
        tid = tf["raw_interval"]
        close = np.column_stack([panels[tid][s]["close"] for s in SYMBOLS])
        n = len(close)
        if n < length + 1:
            continue
        x = np.log(close)
        open_ms = panels[tid][SYMBOLS[0]]["open_ms"]
        windows = n // length
        if windows < 1:
            continue
        starts = np.arange(windows) * length
        ends = starts + length - 1
        years = np.array([dt.datetime.fromtimestamp(int(v) / 1000.0, dt.timezone.utc).year
                          for v in open_ms[ends]])
        for i, j in itertools.combinations(range(len(SYMBOLS)), 2):
            xi, yi = x[:, i], x[:, j]
            c = xi[:-1] * np.diff(yi) - yi[:-1] * np.diff(xi)
            prefix = np.zeros(n)
            prefix[1:] = np.cumsum(c)          # prefix[m] = sum_{k<m} c_k
            area = 0.5 * ((prefix[ends] - prefix[starts])
                          - xi[starts] * (yi[ends] - yi[starts])
                          + yi[starts] * (xi[ends] - xi[starts]))
            for year in sorted(set(int(v) for v in years)):
                sample = area[years == year]
                if len(sample) < need:
                    continue
                mean = float(np.mean(sample))
                sd = float(np.std(sample, ddof=1))
                if sd <= 0.0:
                    t_abs, significant, degenerate = None, mean != 0.0, True
                else:
                    t_abs = abs(mean / (sd / math.sqrt(len(sample))))
                    significant, degenerate = t_abs >= threshold, False
                cells.append({"timeframe": tid, "pair": "%s/%s" % (SYMBOLS[i], SYMBOLS[j]),
                              "regime": year, "windows": int(len(sample)),
                              "mean_cross_area": mean, "t_abs": t_abs,
                              "statistically_distinct_from_zero": bool(significant),
                              "degenerate_zero_variance": degenerate})
    if not cells:
        status = "INDETERMINATE"
    elif all(not c["statistically_distinct_from_zero"] for c in cells):
        status = "FAIL"
    else:
        status = "PASS"
    return {"status": status, "failure_rule": cfg["failure_rule"],
            "t_stat_threshold": threshold, "evaluable_cells": len(cells),
            "significant_cells": sum(c["statistically_distinct_from_zero"] for c in cells),
            "evaluability_min_windows": need,
            "scope": cfg["scope"], "windowing": cfg["windowing"],
            "falsified_when": cfg["falsified_when"], "cells": cells}


def shrinkage_boundary_test(panels, funding, instruments, windows, cfg):
    """Record test 2: raw plug-in must catastrophically collapse below M/p = 2.5."""
    gamma = float(cfg["gamma"])
    length = int(cfg["path_window_bars"])
    dca = dict(DCA_GRID[0])
    rows = []
    collapse_seen = False
    for tf in TIMEFRAMES:
        tid = tf["raw_interval"]
        panel_tf = {s: panels[tid][s] for s in SYMBOLS}
        funding_tf = {s: funding[(s, tid)] for s in SYMBOLS}
        logcloses = np.log(np.column_stack([panel_tf[s]["close"] for s in SYMBOLS]))
        bank = signature_bank(logcloses, length)
        i0, i1 = windows[tid]["oos"]
        for m in cfg["m_grid"]:
            per_alloc = {}
            for allocator in ("ridge", "raw"):
                layer = signal_layer(logcloses, bank, length, gamma,
                                     m_cap=int(m), m_min=min(int(m), M_MIN),
                                     shrinkage=allocator)
                singular = int(layer["diag"]["solve_failures"])
                sleeve = projected_sleeve(panel_tf, funding_tf, layer, dca, i0, i1, {},
                                          instruments)
                per_alloc[allocator] = {"sleeve": sleeve, "singular_decisions": singular}
            ridge = per_alloc["ridge"]["sleeve"]
            raw = per_alloc["raw"]["sleeve"]
            ratio = float(m) / float(cfg["p"])
            below = ratio < 2.5
            singular = per_alloc["raw"]["singular_decisions"] > 0
            collapsed = bool(below) and (
                singular or (raw["sharpe"] < 0.0 and raw["sharpe"] < ridge["sharpe"] - 0.50))
            collapse_seen = collapse_seen or collapsed
            rows.append({"timeframe": tid, "m": int(m), "ratio_m_over_p": ratio,
                         "below_2_5": bool(below),
                         "ridge_sharpe": ridge["sharpe"], "raw_sharpe": raw["sharpe"],
                         "ridge_net_pnl": ridge["net_pnl"], "raw_net_pnl": raw["net_pnl"],
                         "raw_singular": singular, "raw_singular_decisions":
                             per_alloc["raw"]["singular_decisions"],
                         "collapse": collapsed})
        del bank
    status = "FAIL" if not collapse_seen else "PASS"
    return {"status": status, "failure_rule": cfg["failure_rule"],
            "collapse_definition": cfg["collapse_definition"],
            "falsified_when": cfg["falsified_when"], "window": cfg["window"],
            "collapse_observed": collapse_seen, "m_grid": list(cfg["m_grid"]),
            "p": cfg["p"], "gamma": gamma, "path_window_bars": length,
            "fixed_dca_cell": dca, "rows": rows}


def lift_ruin_test(cfg):
    """Record test 3: forward-lifted wealth must show insolvency where Marcus stays solvent."""
    rng = np.random.default_rng(int(cfg["seed"]))
    paths, steps = int(cfg["paths"]), int(cfg["steps"])
    d = len(cfg["weights"])
    weights = np.asarray(cfg["weights"], dtype=np.float64)
    leverage = float(cfg["leverage"])
    shocks = rng.normal(float(cfg["step_drift"]), float(cfg["step_vol"]), size=(paths, steps, d))
    jumps = rng.random((paths, steps)) < float(cfg["jump_probability"])
    shocks[jumps] += float(cfg["jump_size"])
    portfolio = shocks @ weights                       # (paths, steps)
    forward_factor = 1.0 + leverage * portfolio
    insolvent = np.any(forward_factor <= 0.0, axis=1)
    forward_wealth = np.prod(forward_factor, axis=1)
    marcus_wealth = np.exp(leverage * portfolio.sum(axis=1))
    marcus_positive = bool(np.all(marcus_wealth > 0.0))
    observed = bool(np.any(insolvent)) and marcus_positive
    status = "PASS" if observed else "FAIL"
    return {"status": status, "failure_rule": cfg["failure_rule"],
            "falsified_when": cfg["falsified_when"],
            "forward_form": cfg["forward_form"], "marcus_form": cfg["marcus_form"],
            "paths": paths, "steps": steps, "jump_probability": cfg["jump_probability"],
            "jump_size": cfg["jump_size"], "leverage": leverage,
            "seed": int(cfg["seed"]),
            "insolvent_paths": int(np.count_nonzero(insolvent)),
            "insolvent_fraction": float(np.mean(insolvent)),
            "forward_min_wealth": float(np.min(forward_wealth)),
            "marcus_min_wealth": float(np.min(marcus_wealth)),
            "marcus_always_positive": marcus_positive,
            "observed_gap": observed}


def falsification_battery(panels, funding, instruments, windows, log):
    cfg = FALSIFICATION["record_tests"]
    tests = {}
    tests["levy_area_asymmetry"] = levy_area_test(panels, cfg["levy_area_asymmetry"])
    log("falsification levy_area_asymmetry=%s evaluable_cells=%d"
        % (tests["levy_area_asymmetry"]["status"],
           tests["levy_area_asymmetry"]["evaluable_cells"]))
    tests["shrinkage_boundary"] = shrinkage_boundary_test(
        panels, funding, instruments, windows, cfg["shrinkage_boundary"])
    log("falsification shrinkage_boundary=%s collapse_observed=%s"
        % (tests["shrinkage_boundary"]["status"],
           tests["shrinkage_boundary"]["collapse_observed"]))
    tests["lift_ruin"] = lift_ruin_test(cfg["lift_ruin"])
    log("falsification lift_ruin=%s insolvent_paths=%d"
        % (tests["lift_ruin"]["status"], tests["lift_ruin"]["insolvent_paths"]))
    statuses = [t["status"] for t in tests.values()]
    overall = "FAIL" if "FAIL" in statuses else (
        "INDETERMINATE" if "INDETERMINATE" in statuses else "PASS")
    return {"tests": tests, "overall": overall, "required_tests": list(cfg),
            "policy": FALSIFICATION["failure_policy"],
            "registered_before_compute": True}


def write_grid(path, rows):
    cols = ["symbol", "timeframe", *STRATEGY_AXES, *DCA_AXES, "grid",
            *metric_block(empty_metric(0, 0)), "decomposition_ok"]
    with open(path, "w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def disposition_for_survivor_count(count):
    if count == 0:
        return "REJECT / NO_SURVIVOR"
    if count == 1:
        return "SURVIVOR_FOUND"
    return "MULTIPLE_SURVIVORS"


GRID_PHASE = {"historical": "historical", "no_funding": "historical", "oos": "oos"}
GRID_COST = {"fee_2x": {"fee_mult": 2}, "funding_2x": {"funding_mult": 2},
             "entry_delay_1_bar": {"entry_delay": 1}, "slippage_2ticks": {"slip_ticks": 2},
             "no_funding": {"no_funding": True}, "no_funding_full": {"no_funding": True},
             "cost_attrition_40bps": {"fee_override": 0.004}}


def run(spec, attempt_dir):
    counts = validate_spec(spec)
    path = Path(attempt_dir)
    expected = (Path(RESULTS_ROOT) / FAMILY_ID / "rounds" / spec["round_id"]
                / "attempts" / spec["run_id"])
    if path != expected or any((path / x).exists()
                               for x in ("DONE", "FAILED", "INCOMPLETE", "result.json")):
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
                "cohorts_done": 0, "cohorts_total": len(COHORTS),
                "expected_case_evaluations": counts["case_evaluations_total"]}

    def write_progress():
        progress["updated_at_utc"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        progress["runtime_seconds"] = round(time.monotonic() - started, 3)
        atomic_json(artifacts / "progress.json", progress)

    write_progress()
    log("validate_spec ok; Qlib 0.9.7 multi-timeframe build starting for %s x %s"
        % (len(SYMBOLS), TIMEFRAME_IDS))
    try:
        instruments = instrument_metadata()
        panels, build = build_qlib(spec, attempt_dir, log)
        funding = {}
        for tfid in TIMEFRAME_IDS:
            for symbol in SYMBOLS:
                funding[(symbol, tfid)] = load_funding(symbol, panels[tfid][symbol]["open_ms"])
        atomic_json(artifacts / "funding_coverage.json",
                    {"%s/%s" % key: value[1] for key, value in funding.items()})
        windows = {tfid: {phase: window_indices(panels[tfid][SYMBOLS[0]]["open_ms"], *span)
                          for phase, span in PHASES.items()} for tfid in TIMEFRAME_IDS}
        battery = falsification_battery(panels, funding, instruments, windows, log)
        atomic_json(artifacts / "falsification_battery.json", battery)
        grid_rows = {g: [] for g in GRIDS}
        results, survivors, winner_rows = [], [], []
        hist_total = [0] * LADDER_LEVELS
        signature_build, layer_diags = [], []
        for tfid in TIMEFRAME_IDS:
            rows_by_symbol = {s: {g: [] for g in GRIDS} for s in SYMBOLS}
            logcloses = np.log(np.column_stack(
                [panels[tfid][s]["close"] for s in SYMBOLS]))
            n_bars = len(logcloses)
            for path_bars in PATH_BARS_AXIS:
                bank = signature_bank(logcloses, path_bars)
                signature_build.append({"timeframe": tfid, "path_bars": path_bars,
                                        "bars": n_bars, "signature_windows": len(bank),
                                        "defect_dimension": DEFECT_DIM,
                                        "first_window_endpoint_bar": path_bars - 1,
                                        "last_window_endpoint_bar": n_bars - 1})
                for gamma in GAMMA_AXIS:
                    layer = signal_layer(logcloses, bank, path_bars, gamma)
                    layer_diags.append({"timeframe": tfid, "path_bars": path_bars,
                                        "gamma": gamma, **layer["diag"]})
                    for symbol in SYMBOLS:
                        panel = panels[tfid][symbol]
                        events = funding[(symbol, tfid)][0]
                        for dca in DCA_GRID:
                            for grid in GRIDS:
                                phase = GRID_PHASE.get(grid, "full")
                                cost = dict(GRID_COST.get(grid, {}))
                                metric = simulate(panel, events, layer, symbol, dca,
                                                  *windows[tfid][phase], cost,
                                                  instruments[symbol])
                                row = {"symbol": symbol, "timeframe": tfid, **path_bars_row(path_bars),
                                       "gamma": gamma, **dca, "grid": grid,
                                       **metric_block(metric),
                                       "decomposition_ok": metric["decomposition_ok"]}
                                rows_by_symbol[symbol][grid].append(row)
                                grid_rows[grid].append(row)
                                progress["case_evaluations"] += 1
                                if progress["case_evaluations"] % 1000 == 0:
                                    write_progress()
                                if grid == "full":
                                    for i, val in enumerate(metric["layer_hist"]):
                                        hist_total[i] += val
                    del layer
                del bank
                log("signature grid %s path_bars=%d complete (%d case evaluations)"
                    % (tfid, path_bars, progress["case_evaluations"]))
            del logcloses
            for symbol in SYMBOLS:
                selected, info = select_cohort(rows_by_symbol[symbol])
                label = "%s/%s" % (symbol, tfid)
                for name, measured in battery["tests"].items():
                    if measured["status"] != "PASS":
                        info["cull_reasons"].append(
                            "falsification:%s:%s" % (name, measured["status"]))
                if info["cull_reasons"]:
                    selected = None
                if info.get("winner"):
                    winner_rows.append(info["winner"])
                record = {"cohort": label,
                          "outcome": "SURVIVOR" if selected is not None else "CULLED",
                          "falsification_battery": battery["overall"], **info}
                results.append(record)
                if selected is not None:
                    survivors.append(record)
                log("cohort %s outcome=%s cull=%s"
                    % (label, record["outcome"], ",".join(record["cull_reasons"]) or "none"))
                progress["cohorts_done"] += 1
            write_progress()
        progress["stage"] = "SELECTED"
        write_progress()
        expected_keys = {(s, tfid, *(c[k] for k in STRATEGY_AXES), *(d[k] for k in DCA_AXES))
                         for (s, tfid) in COHORTS for c in STRATEGIES for d in DCA_GRID}
        coverage = {g: ({cell_key(r) for r in grid_rows[g]} == expected_keys
                        and len(grid_rows[g]) == len(expected_keys)) for g in GRIDS}
        base_full = sum(r["net_pnl"] for r in grid_rows["full"])
        stress_delta = {g: sum(r["net_pnl"] for r in grid_rows[g]) - base_full
                        for g in ("fee_2x", "funding_2x", "slippage_2ticks",
                                  "cost_attrition_40bps")}
        traded = any(r["turnover_usdt"] > 0 for r in grid_rows["full"])
        official_events = {("%s/%s" % key): sum(len(e) for e in value[0])
                           for key, value in funding.items()}
        official_reports = {("%s/%s" % key): value[1] for key, value in funding.items()}
        assertions = {
            "qlib_readback_0_9_7": (build["qlib_version"] == "0.9.7"
                                    and build["qlib_rows_identical_to_raw"]),
            "coverage_complete": all(coverage.values()),
            "dca_histogram_reconciles": hist_total[0] == sum(
                r["episodes"] for r in grid_rows["full"]),
            "independent_gross_net_decomposition": all(
                r["decomposition_ok"] for g in GRIDS for r in grid_rows[g]),
            "cost_stress_effective": (not traded) or all(
                stress_delta[g] < 0 for g in ("fee_2x", "slippage_2ticks",
                                              "cost_attrition_40bps")),
            "official_funding_only_no_modeled_charges": all(
                official_events[k] == official_reports[k]["official"]
                for k in official_events),
            "selector_winner_is_historical_row": all(
                r["grid"] == "historical" for r in winner_rows),
            "falsification_battery_ran": all(
                t["status"] in ("PASS", "FAIL", "INDETERMINATE")
                for t in battery["tests"].values()),
            "signature_estimations_causal": all(d["causal"] for d in layer_diags),
            "defect_form_well_posed": all(d["solve_failures"] == 0 for d in layer_diags),
            "signature_bank_finite": all(s["signature_windows"] > 0 for s in signature_build),
        }
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
        atomic_json(artifacts / "signature_build.json", signature_build)
        atomic_json(artifacts / "signal_layer.json", {
            "layers": layer_diags,
            "mechanism": "level-2 path signature -> defect form (Prop. 3 covariance of "
                         "signature coordinate payoffs) -> Marchenko-Pastur ridge -> "
                         "risk-aversion allocation -> dynamic feedback execution",
            "anchor_rule": signal_constants()["anchor_rule"],
            "entry_rule": signal_constants()["entry_rule"],
            "scope": "4 fixed local USD-M contracts x all 7 local kline intervals; "
                     "conclusions limited to this local universe",
            "research_defined": ["path windows 12/48 bars (exactly 1h/4h on the 5m grid)",
                                 "gamma endpoints 0.5/1.0", "M cap 200, M floor 20",
                                 "min |target weight| 0.01", "next-open entries",
                                 "projected signed per-cohort DCA and deterministic "
                                 "adverse-first OHLC ordering"]})
        atomic_json(artifacts / "falsification_battery.json", battery)
        atomic_json(artifacts / "cohort_results.json", results)
        atomic_json(artifacts / "cohort_survivors.json", survivors)
        atomic_json(artifacts / "dca_layer_histogram.json",
                    {"level_%02d" % i: v for i, v in enumerate(hist_total)})
        atomic_json(artifacts / "assertions.json", assertions)
        for g in GRIDS:
            write_grid(artifacts / ("grid_%s.csv" % g), grid_rows[g])
        result = {"schema_version": 1, "family_id": FAMILY_ID, "round_id": spec["round_id"],
                  "run_id": spec["run_id"], "engine": ENGINE_VERSION,
                  "script_sha256": sha256_file(__file__), "qlib_version": build["qlib_version"],
                  "status": "ARTIFACT_READY", "coverage_complete": all(coverage.values()),
                  "coverage_by_grid": coverage,
                  "assertions_all_true": all(assertions.values()),
                  "assertion_failures": [k for k, v in assertions.items() if not v],
                  "case_evaluations_total": sum(map(len, grid_rows.values())),
                  "expected_case_evaluations": counts["case_evaluations_total"],
                  "cohort_count": len(results), "cohort_survivor_count": len(survivors),
                  "cohort_survivors": [x["cohort"] for x in survivors],
                  "family_falsification_overall": battery["overall"],
                  "family_falsification_status": {k: v["status"]
                                                  for k, v in battery["tests"].items()},
                  "signal_decisions_total": sum(d["decisions_with_signal"]
                                                for d in layer_diags),
                  "signature_estimations_total": sum(d["estimations"] for d in layer_diags),
                  "official_funding_events": official_events,
                  "cost_stress_noop": None if traded else
                      "cost rails unexercised: no fills in the full grid",
                  "disposition": disposition_for_survivor_count(len(survivors)),
                  "verdict_recommendation": "PASS" if survivors else "REJECT",
                  "selector_version": "cohort-selector-v1",
                  "disposition_version": "cohort-disposition-v1",
                  "disposition_mapping_version": "v1.4.0", "grid_kinds": list(GRIDS),
                  "stress_net_delta": stress_delta,
                  "portfolio_caveat": "cohort-level projected trading metrics, not a jointly "
                                      "executed portfolio; scope limited to the local 4-name "
                                      "x 7-interval universe",
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
        log("ARTIFACT_READY cohorts=%d survivors=%d rows=%d falsification=%s assertions=%s"
            % (len(results), len(survivors), result["case_evaluations_total"],
               battery["overall"],
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


def path_bars_row(path_bars):
    return {"path_bars": int(path_bars)}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-spec", required=True)
    parser.add_argument("--attempt-dir")
    args = parser.parse_args(argv)
    with open(args.run_spec, encoding="utf-8") as stream:
        spec = json.load(stream)
    result = run(spec, args.attempt_dir or str(Path(args.run_spec).parent))
    print(json.dumps({"status": result["status"],
                      "case_evaluations_total": result["case_evaluations_total"],
                      "assertions_all_true": result["assertions_all_true"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

