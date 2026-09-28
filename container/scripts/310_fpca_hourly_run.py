#!/usr/bin/env python3
"""Direct-family Qlib runner: Bitcoin rolling FPCA one-step-ahead hourly direction.

Registered hypothesis, record excerpts and the frozen candidate body live in the
family round-spec; this engine implements only what they require.

Registered mechanism (source = canonical wiki record, excerpted in the candidate body):
    Bitcoin trades continuously, so a UTC "day" is only a conventional partition. A
    sequence of 24-hour hourly-return functions can be shifted by one hour to create
    highly overlapping functional observations. The rolling FPCA method decomposes a
    set of completed 24-hour return functions and a one-hour-shifted auxiliary set into
    functional principal components, learns a mapping from the shifted-function
    eigenscores (alpha) to the target-function eigenscores (beta), then reconstructs the
    target function and evaluates it at the next hourly point to obtain the
    one-hour-ahead BTC return forecast.

Construction implemented here (record "Source-reported forecasting construction", k = 1):
    signal time n = close of bar n (ret[] = hourly log returns, ret[m] is the return of
    bar m+1), data strictly through ret[n-1]:
      auxiliary (observed)   = ret[n-24 .. n-1]        (24 points, s = 0..23)
      target (to reconstruct)= ret[n-23 .. n]          (24 points, t = 1..24)
      the two share 23 of 24 hourly returns (record: k = 1 overlap);
      the target's last point ret[n] is the unknown next-hour return.
    training pairs at time n: m in [n-W, n-1] (W = registered window_functions),
      auxiliary_m = ret[m-24 .. m-1], target_m = ret[m-23 .. m] - all observed by n.
    Steps: point-in-time demeaning of both function families with their own sample
    means, separate FPCA on each family, per-target-component LASSO regression of beta
    on alpha, prediction from the current auxiliary eigenscore vector, reconstruction
    Xhat(t) = mu(t) + sum_j beta_hat_j phi_j(t), forecast r_hat = Xhat(24).

Point-in-time rule (record "Point-in-time requirement"): only returns fully observed by
each forecast timestamp enter the auxiliary function, FPCA estimation, coefficient
fitting, tuning and normalization. There is NO selection of window/J/lambda from any
label: the registered domain is searched on the historical grid only (cohort-selector-v1)
and frozen before OOS is read. Asserted in-run by a suffix-perturbation recheck of five
sampled timestamps and re-tested by the self-check.

Falsification battery: the card body quotes record items 1-2 as an excerpt ("節錄");
the canonical record carries seven items and all seven are registered here (restored
verbatim from the record, disclosed in round-spec.record_excerpts; the frozen candidate
body is never rewritten).

Trading translation (record "Research-proposed trading operationalization"):
    r_hat > 0 -> long, r_hat < 0 -> short, r_hat == 0 -> flat (tie); tranche #1 fills at
    the first executable price after signal computation (the next bar's open) with one
    tick adverse slippage; the position is closed at the end of the forecasted one-hour
    interval (the bar's close), so no position is carried beyond its own one-hour
    forecast horizon. Because the registered exit is the end of that same bar, the DCA
    ladder can only ever deploy inside one hourly bar: adverse scale-ins fire when the
    bar trades spacing_pct*k against the initial fill, and breakeven TP / resting
    invalidation are evaluated intra-bar against running average cost. That is a direct
    consequence of the registered one-hour exit, disclosed before compute in round-spec
    dca_execution_semantics - not a simplification of the rail.

Execution (research-defined): per-fill taker fee (canonical instrument metadata) and
one tick adverse slippage on EVERY leg of BOTH directions; official funding observations
only, charged at their own timestamp/mark (missing intervals = zero cost, coverage
disclosed, modeled rows never charged); gross PnL from an independent price-PnL
accumulator cross-checked against net with a negative control (v1.3.2).

Not reimplemented (disclosed adaptation boundary, never silently substituted): the
record's unknown 2019 source venue (a data gap in the record itself), any venue other
than Binance (the local canonical raw holds one venue), estimator shopping (the record's
primary variant LASSO is fixed; OLS/Ridge/SVM/RF/NN are not searched), and horizon
k != 1 as a trading rule (k = 2,4,8 exist only inside the registered falsification
item 6 forecast-level diagnostic).

Execution: production compute runs only inside the qlib-run container via the existing
`container exec -d qlib-run` semantics; the hourly signal source is a Qlib 0.9.7
dump/readback of the canonical raw; raw is read through /data/raw read-only; no second
engine, no host pandas backtest, no external coding agent.
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

FAMILY_ID = "bitcoin-rolling-fpca-hourly-return-direction-2026-09-03"
ENGINE_VERSION = "rolling_fpca_hourly_direction_qlib_v1"
RAW_ROOT = "/data/raw"
RESULTS_ROOT = "/results"
WORK_ROOT = "/qlib/work/fpca-hourly-v1"
INSTRUMENTS_PATH = RAW_ROOT + "/binance/usdm/instruments/usdm-perp-instruments.json"
VENV_PYTHON = "/opt/venv/bin/python"
DUMP_BIN = "/opt/qlib-tools/dump_bin.py"
MS_DAY = 86_400_000
MS_HOUR = 3_600_000
FIELDS = ("open", "high", "low", "close", "volume")
SYMBOLS = ("BTCUSDT",)
SPOT_SYMBOL = "BTCUSDT"
TIMEFRAME = "1h"
QLIB_FREQ = "60min"
# The record fixes no sample calendar for its own rolling exercise; the candidate body
# DATA WINDOW clause adopts the local canonical raw window (research-defined).
PHASES = {"historical": ("2022-01-01", "2025-09-30"),
          "oos": ("2025-10-01", "2026-09-11"),
          "full": ("2022-01-01", "2026-09-11")}

START_EQUITY = 30_000.0
BASE_QUOTE = 1_000.0
LEVERAGE = 10.0
MAX_ADD_LEVELS = 10          # tranche #1 + 10 scale-ins = 11 routine active levels
LADDER_LEVELS = 12           # tranche #12 stays reserve, never routinely deployed
SEED = 20260903
TRACE = None                 # inert section 28 replay hook; never populated in grid search

# --------------------------------------------------------------------------- #
# Registered strategy domain (all values numeric; frozen before any compute)    #
# --------------------------------------------------------------------------- #
FUNCTION_LENGTH = 24          # record: 24-hour return functions
HORIZON = 1                   # record k = 1 one-hour-ahead forecast
WINDOWS_FN = (90, 100, 110)   # arXiv-v1 reported initialization range [90, 110]
FPCA_J = (2, 4, 6)            # record: exact rolling J underspecified (research-defined)
LASSO_LAMBDA = 0.01           # record does not report lambda (research-defined)
CD_ITERS = 50                 # fixed coordinate-descent sweeps, no label-driven tuning
REFERENCE_CASE = {"window_functions": 100, "fpca_dim_j": 4}   # midpoint, pre-registered
STRATEGY_AXES = {"window_functions": WINDOWS_FN, "fpca_dim_j": FPCA_J}
HORIZONS_DIAGNOSTIC = (1, 2, 4, 8)   # record falsification item 6
CHUNK = 256

CASES = tuple({"case_code": i, "window_functions": w, "fpca_dim_j": j,
               "label": "w%d_j%d" % (w, j)}
              for i, (w, j) in enumerate(itertools.product(WINDOWS_FN, FPCA_J)))

DCA_AXES = {"spacing_pct": (0.01, 0.02, 0.03, 0.04),
            "size_multiplier": (1.0, 1.1),
            "breakeven_tp_pct": (0.01, 0.02, 0.03),
            "invalidation_pct": (0.05, 0.10)}
DCA_GRID = tuple(dict(zip(DCA_AXES, vals)) for vals in itertools.product(*DCA_AXES.values()))

GRIDS = ("historical", "oos", "full", "fee_2x", "funding_2x", "entry_delay_1_bar",
         "slippage_2ticks", "no_funding", "no_funding_full", "cost_attrition_40bps")
STRESS_GRIDS = ("fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks")

MIN_EPISODES_IS = 10
MIN_EPISODES_OOS = 3
MIN_NEIGHBOUR = 0.60
SELECTOR_VERSION = "cohort-selector-v1"
DISPOSITION_VERSION = "cohort-disposition-v1"
DISPOSITION_MAPPING_VERSION = "v1.4.0"
PIT_STATUS = "PASS_NO_LOOKAHEAD"

FALSIFICATION = [
    "Modern walk-forward directional test: reject the directional-alpha hypothesis if "
    "out-of-sample sign accuracy is <= 50% over the predeclared test sample (record item 1; "
    "research-defined threshold); action: retain the method only as a forecasting/risk "
    "research artifact, not as directional alpha",
    "Benchmark incremental-value test: reject incremental FPCA value if the FPCA forecast does "
    "not improve the predeclared directional loss (0-1 sign error) over the best simple baseline "
    "(unconditional-sign, AR(1)/random-walk, simple last-hour momentum/reversal, UTC-hour "
    "seasonality) on identical timestamps, OR if a paired directional-accuracy test fails to "
    "reject equal performance at the 5% level (record item 2)",
    "Leakage/model-selection audit: reproduce every prediction using only state available before "
    "the forecasted hour; any forecast whose selected window, J, normalization, basis or "
    "regression hyperparameter uses future/test labels invalidates that result (record item 3); "
    "action: discard contaminated results and rerun from a clean temporal split",
    "Cost-aware trading translation: apply the research-proposed one-hour long/short rule with "
    "observed/realistic fee, spread, slippage, funding and signal-computation delay; reject the "
    "tradable-alpha translation if mean net return per position is <= 0 or aggregate net PnL is "
    "non-positive over the locked out-of-sample test (record item 4); action: do not progress "
    "the trade mapping",
    "Overlap/competing-explanation ablation: control for last-hour return, 24-hour return, "
    "realized volatility and UTC hour-of-day, and compare against a model using the same raw "
    "lagged returns without FPCA; if FPCA loses all incremental predictive improvement after "
    "these controls, reject the claim that functional decomposition itself adds information "
    "(record item 5)",
    "Horizon perturbation: predeclare k = 1, 2, 4, 8 hour forecasts without retuning to each "
    "test result; the source expects explanatory power to weaken as overlap decreases, and a "
    "flat or erratic profile is not automatically failure, but reversal of the one-hour result "
    "together with failure of the k=1 modern OOS test materially weakens the mechanism "
    "(record item 6)",
    "Venue robustness: where data permit, repeat on at least one major crypto venue distinct "
    "from the source-data reconstruction; if the one-hour signal is positive only on a single "
    "venue and disappears after common timestamp/cost normalization, classify portability as "
    "venue-specific/unproven rather than market-wide (record item 7)",
]

FALSIFICATION_REGISTRY = (
    "walk_forward_directional", "benchmark_incremental_value", "leakage_model_selection",
    "cost_aware_trading_translation", "overlap_competing_explanation_ablation",
    "horizon_perturbation", "venue_robustness")

ARTIFACTS = ("state.json", "result.json", "logs/run.log", "artifacts/progress.json",
             "artifacts/raw_build.json", "artifacts/funding_coverage.json",
             "artifacts/signal_metrics.json", "artifacts/falsification.json",
             "artifacts/stress_effects.json", "artifacts/dca_layer_histogram.json",
             "artifacts/cohort_results.json", "artifacts/cohort_survivors.json",
             "artifacts/assertions.json",
             *(("artifacts/grid_%s.csv" % g) for g in GRIDS))


# --------------------------------------------------------------------------- #
# Small helpers                                                                #
# --------------------------------------------------------------------------- #
def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return "sha256:" + h.hexdigest()


def utc_ms(day):
    return int(dt.datetime.strptime(day, "%Y-%m-%d")
               .replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def hour_stamp(ms):
    return dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def now_utc():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
    return tuple(got) == tuple(registered)


def expected_counts():
    per = len(CASES) * len(DCA_GRID)
    return {"cohorts": len(SYMBOLS), "strategy_cases_per_cohort": len(CASES),
            "dca_configs_per_cohort": len(DCA_GRID),
            "base_combinations_per_cohort": per, "case_evaluations_per_grid": per,
            "grid_count": len(GRIDS), "case_evaluations_total": per * len(GRIDS)}


def parameter_contract():
    """Frozen generic Strategy Family Parameter Contract (contract section 29)."""
    dca_fields = list(DCA_AXES)
    strategy_fields = list(STRATEGY_AXES)
    axes = [{"name": name, "kind": "atomic", "members": [name],
             "registered_values": list(dom), "row_fields": [name]}
            for name, dom in STRATEGY_AXES.items()]
    axes += [{"name": name, "kind": "atomic", "members": [name],
              "registered_values": list(dom), "row_fields": [name]}
             for name, dom in DCA_AXES.items()]
    return {
        "parameter_contract_version": 1, "family_id": FAMILY_ID,
        "contract_ref": "generic Strategy Family Parameter Contract, contract v2.0.0 "
                        "section 29; frozen copy lives here and is the single source of truth",
        "research_axes_ordered": axes,
        "row_fields": strategy_fields + dca_fields, "composite_map": {},
        "strategy_param_fields": strategy_fields, "dca_param_fields": dca_fields,
        "canonical_recipe": {"sort_keys": True, "separators": [",", ":"],
                             "ensure_ascii": False,
                             "numeric_rule": "JSON number finite, bool excluded"},
        "row_match_recipe": {"keys": ["symbol", "timeframe"] + strategy_fields + dca_fields,
                             "equality": "exact, numeric == float compare, rest bytewise"},
        "non_params": ["symbol", "timeframe", "case_label", "grid", "decomposition_ok"],
        "domain_cardinality": {"strategy": len(CASES), "dca": len(DCA_GRID),
                               "per_cohort": len(CASES) * len(DCA_GRID)}}


def run_spec_template(created_at=None):
    here = Path(__file__).resolve()
    return {
        "schema_version": 1, "document_kind": "run_spec",
        "contract": "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md v2.0.0",
        "family_id": FAMILY_ID, "round_id": "<family_id>-r1", "run_id": "<round_id>-u1",
        "created_at_utc": created_at or "<ISO8601Z>", "container_id": "qlib-run",
        "image_id": "qlib:0.9.7-arm64",
        "ownership_mode": "direct_hermes",
        "ownership_note": "direct family: family_id + round_id + run_id only; no Kanban "
                          "ownership key exists for this round and none may be written",
        "script": {"path": "/scripts/310_fpca_hourly_run.py",
                   "sha256": sha256_file(here),
                   "deployed_host_path": "/Users/hong/workspace/qlib-apple-container/"
                                         "scripts/310_fpca_hourly_run.py"},
        "engine": {"name": ENGINE_VERSION, "script": "container/scripts/310_fpca_hourly_run.py",
                   "container": "qlib-run", "qlib_version": "0.9.7", "seed": SEED,
                   "self_check": "container/scripts/tests/test_fpca_hourly_engine.py",
                   "self_check_sha256": sha256_file(
                       here.with_name("tests") / "test_fpca_hourly_engine.py")},
        "data": {"raw_root": "/data/raw/binance/usdm", "start": PHASES["full"][0],
                 "end": PHASES["full"][1], "timezone": "UTC", "symbols": list(SYMBOLS),
                 "fields": list(FIELDS),
                 "signal_timeframe": {"raw_interval": TIMEFRAME, "qlib_freq": QLIB_FREQ},
                 "execution_timeframe": {"raw_interval": TIMEFRAME, "qlib_freq": QLIB_FREQ,
                                         "bars_per_day": 24}},
        "split": {"historical_start": PHASES["historical"][0],
                  "historical_end": PHASES["historical"][1],
                  "oos_start": PHASES["oos"][0], "oos_end": PHASES["oos"][1],
                  "rule": "chronological; frozen before compute; OOS never used for selection"},
        "params": [dict(c) for c in CASES],
        "dca_domain": {**{k: list(v) for k, v in DCA_AXES.items()},
                       "grid": [dict(d) for d in DCA_GRID], "base_quote": BASE_QUOTE,
                       "base_quote_status": "PROJECT_PRE_REGISTERED_CONSTANT",
                       **{k + "_status": "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN"
                          for k in DCA_AXES}},
        "selector_version": SELECTOR_VERSION, "disposition_version": DISPOSITION_VERSION,
        "expected": expected_counts(),
        "gates": {"min_episodes_is": MIN_EPISODES_IS, "min_episodes_oos": MIN_EPISODES_OOS,
                  "min_neighbour_same_sign_fraction": MIN_NEIGHBOUR},
        "signal_constants": signal_constants(),
        "costs": {"taker_fee": "canonical instrument taker_fee, per fill", "slippage_ticks": 1,
                  "slippage_robustness_ticks": 2,
                  "funding": "official observations only at timestamp/mark; missing intervals "
                             "zero and disclosed",
                  "accounting": "per-fill fee and funding deducted at the fill's own time; "
                                "net/ending_equity/marks/Sharpe are net-of-fee (v1.3.1)"},
        "falsification": list(FALSIFICATION), "expected_outputs": list(ARTIFACTS),
        "notes": "direct family: family_id + round_id + run_id only; no Kanban ownership keys."}


def signal_constants():
    return {"function_length": FUNCTION_LENGTH, "horizon": HORIZON,
            "window_functions": list(WINDOWS_FN), "fpca_dim_j": list(FPCA_J),
            "estimator": "LASSO (record primary variant; not searched)",
            "lasso_lambda": LASSO_LAMBDA, "coordinate_descent_iters": CD_ITERS,
            "standardization": "point-in-time z-score of both score matrices inside the "
                               "rolling window (ddof=0, zero-variance guarded)",
            "reference_case": dict(REFERENCE_CASE),
            "horizons_diagnostic": list(HORIZONS_DIAGNOSTIC),
            "entry_timing": "signal from data through bar n's close, tranche #1 at bar n+1's "
                            "open with 1 tick adverse slippage",
            "exit_timing": "flatten at the close of the bar in which the position was opened "
                           "(record: end of the forecasted one-hour interval), 1 tick adverse",
            "direction_rule": "r_hat > 0 long, r_hat < 0 short, r_hat == 0 flat",
            "pit_status": PIT_STATUS}


def validate_spec(spec, script_path=None, test_path=None):
    if spec.get("family_id") != FAMILY_ID \
            or spec.get("selector_version") != SELECTOR_VERSION \
            or spec.get("disposition_version") != DISPOSITION_VERSION:
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
            or data.get("symbols") != list(SYMBOLS) or data.get("start") != PHASES["full"][0] \
            or data.get("end") != PHASES["full"][1]:
        raise ValueError("canonical local universe/window mismatch")
    if data.get("signal_timeframe") != {"raw_interval": TIMEFRAME, "qlib_freq": QLIB_FREQ} \
            or data.get("execution_timeframe") != {"raw_interval": TIMEFRAME,
                                                   "qlib_freq": QLIB_FREQ, "bars_per_day": 24}:
        raise ValueError("signal/execution timeframe mismatch")
    if spec.get("split") != {"historical_start": PHASES["historical"][0],
                             "historical_end": PHASES["historical"][1],
                             "oos_start": PHASES["oos"][0], "oos_end": PHASES["oos"][1],
                             "rule": "chronological; frozen before compute; OOS never used "
                                     "for selection"}:
        raise ValueError("frozen split changed")
    if spec.get("params") != [dict(c) for c in CASES]:
        raise ValueError("strategy domain must be the exact registered case set")
    dca = spec.get("dca_domain", {})
    if any(not axis(dca.get(k, ()), v) for k, v in DCA_AXES.items()) \
            or dca.get("grid") != [dict(d) for d in DCA_GRID] \
            or dca.get("base_quote") != BASE_QUOTE:
        raise ValueError("DCA domain incomplete/changed")
    if dca.get("base_quote_status") != "PROJECT_PRE_REGISTERED_CONSTANT" \
            or any(dca.get(k + "_status") != "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN"
                   for k in DCA_AXES):
        raise ValueError("DCA provenance mismatch")
    if spec.get("expected") != expected_counts() \
            or spec.get("expected_outputs") != list(ARTIFACTS) \
            or spec.get("falsification") != list(FALSIFICATION):
        raise ValueError("coverage/artifact/falsification registration mismatch")
    if spec.get("gates") != {"min_episodes_is": MIN_EPISODES_IS,
                             "min_episodes_oos": MIN_EPISODES_OOS,
                             "min_neighbour_same_sign_fraction": MIN_NEIGHBOUR}:
        raise ValueError("selector gates changed")
    if spec.get("signal_constants") != signal_constants():
        raise ValueError("signal constants must stay the registered values")
    script = spec.get("script", {})
    if script.get("path") != "/scripts/310_fpca_hourly_run.py" \
            or script.get("sha256") != sha256_file(script_path or __file__):
        raise ValueError("script bytes do not match pinned identity")
    engine = spec.get("engine", {})
    test = Path(test_path) if test_path else \
        Path(__file__).resolve().with_name("tests") / "test_fpca_hourly_engine.py"
    if engine.get("name") != ENGINE_VERSION or engine.get("qlib_version") != "0.9.7" \
            or engine.get("seed") != SEED or engine.get("self_check_sha256") != sha256_file(test):
        raise ValueError("engine/self-check identity mismatch")
    with open(RAW_ROOT + "/_meta/CONFIG.json", encoding="utf-8") as fh:
        catalog = json.load(fh)
    if catalog.get("venue") != "BINANCE" or catalog.get("market_type") != "usdm_perp" \
            or not set(SYMBOLS) <= set(catalog.get("symbols", [])) \
            or TIMEFRAME not in set(catalog.get("intervals", [])) \
            or not {"klines", "funding", "spot_klines"} <= set(catalog.get("datasets", {})):
        raise ValueError("canonical catalog diverged from registered universe")
    return expected_counts()


# --------------------------------------------------------------------------- #
# Canonical raw readers                                                        #
# --------------------------------------------------------------------------- #
def instrument_metadata(path=INSTRUMENTS_PATH):
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    by_symbol = {r["fields"]["raw_symbol"]: r["fields"] for r in doc["instruments"]}
    out = {}
    for s in SYMBOLS:
        f = by_symbol[s]
        if f["type"] != "CryptoPerpetual" or f["quote_currency"] != "USDT" or f["is_inverse"]:
            raise ValueError("not a linear USDT perp: " + s)
        tick, fee = float(f["price_increment"]), float(f["taker_fee"])
        if not (tick > 0 and 0 <= fee < 0.01):
            raise ValueError("invalid instrument metadata: " + s)
        out[s] = {"tick": tick, "taker_fee": fee, "source_sha256": sha256_file(path)}
    return out


def _shards(symbol, interval, market, start, end):
    root = Path(RAW_ROOT) / "binance" / market / "klines" / symbol / interval
    return [p for p in sorted(root.glob("%s-%s-*.jsonl.gz" % (symbol, interval)))
            if start[:7] <= p.name.split("-%s-" % interval)[1][:7] <= end[:7]]


def raw_panel(symbol, start=None, end=None, market="usdm", interval=TIMEFRAME):
    """Hourly OHLCV panel with a fail-closed gap/duplicate/geometry check."""
    start = start or PHASES["full"][0]
    end = end or PHASES["full"][1]
    files = _shards(symbol, interval, market, start, end)
    if not files:
        raise RuntimeError("no canonical %s shards: %s/%s" % (interval, market, symbol))
    lo, hi = utc_ms(start), utc_ms(end) + MS_DAY
    times, vals = [], {k: [] for k in FIELDS}
    for path in files:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                row = json.loads(line)
                t = int(row["open_time_ms"])
                if not lo <= t < hi:
                    continue
                if times and t - times[-1] != MS_HOUR:
                    raise RuntimeError("hourly gap/duplicate %s/%s %s" % (
                        market, symbol, hour_stamp(t)))
                for k in FIELDS:
                    v = float(row[k])
                    if not math.isfinite(v) or (v <= 0 if k != "volume" else v < 0):
                        raise RuntimeError("invalid %s/%s" % (symbol, k))
                    vals[k].append(v)
                times.append(t)
    if not times or times[0] != lo or times[-1] != hi - MS_HOUR:
        raise RuntimeError("incomplete hourly window: %s/%s (%s..%s)" % (
            market, symbol, hour_stamp(times[0]) if times else "-",
            hour_stamp(times[-1]) if times else "-"))
    if any(vals["high"][i] < max(vals["open"][i], vals["close"][i])
           or vals["low"][i] > min(vals["open"][i], vals["close"][i])
           or vals["low"][i] > vals["high"][i] for i in range(len(times))):
        raise RuntimeError("invalid OHLC geometry: " + symbol)
    return {"open_ms": np.asarray(times, np.int64),
            **{k: np.asarray(vals[k], np.float64) for k in FIELDS},
            "raw_files": [{"path": str(p), "sha256": sha256_file(p)} for p in files]}


def raw_panel_segmented(symbol, start=None, end=None, market="spot", interval=TIMEFRAME):
    """Same reader for a non-cohort diagnostic series; a missing bar is absent.

    Returns (panel, gaps) where panel arrays cover only the first contiguous run that
    reaches the window end, and gaps lists every discontinuity. The registered spot
    market-type diagnostic fails the affected window rather than imputing (record:
    'fail the affected window rather than silently impute').
    """
    start = start or PHASES["full"][0]
    end = end or PHASES["full"][1]
    files = _shards(symbol, interval, market, start, end)
    lo, hi = utc_ms(start), utc_ms(end) + MS_DAY
    times, vals = [], {k: [] for k in FIELDS}
    for path in files:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                row = json.loads(line)
                t = int(row["open_time_ms"])
                if lo <= t < hi:
                    times.append(int(t))
                    for k in FIELDS:
                        vals[k].append(float(row[k]))
    gaps = []
    for i in range(len(times) - 1):
        if times[i + 1] - times[i] != MS_HOUR:
            gaps.append({"after": hour_stamp(times[i]),
                         "gap_hours": int((times[i + 1] - times[i]) // MS_HOUR)})
    return {"open_ms": np.asarray(times, np.int64),
            **{k: np.asarray(vals[k], np.float64) for k in FIELDS},
            "gaps": gaps, "raw_files": [{"path": str(p), "sha256": sha256_file(p)}
                                         for p in files]}


def qlib_readback(panel):
    """Dump the canonical hourly panel through Qlib 0.9.7 and read it back.

    The readback - not the raw parse - is the signal source of the production run.
    """
    work = Path(WORK_ROOT)
    if work.exists():
        shutil.rmtree(work)
    csv_root, qlib_root = work / "csv", work / "qlib-data"
    csv_root.mkdir(parents=True)
    with open(csv_root / (SYMBOLS[0] + ".csv"), "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["date", *FIELDS])
        for i, ms in enumerate(panel["open_ms"]):
            writer.writerow([hour_stamp(ms), *(panel[k][i] for k in FIELDS)])
    proc = subprocess.run([VENV_PYTHON, DUMP_BIN, "dump_all", "--data_path", str(csv_root),
                           "--qlib_dir", str(qlib_root), "--freq", QLIB_FREQ,
                           "--include_fields", ",".join(FIELDS), "--date_field_name", "date",
                           "--max_workers", "1"], capture_output=True, text=True, timeout=3600)
    if proc.returncode:
        raise RuntimeError("Qlib dump_bin failed: " + proc.stderr[-700:])
    import qlib
    from qlib.data import D
    if qlib.__version__ != "0.9.7":
        raise RuntimeError("wrong Qlib version")
    qlib.init(provider_uri=str(qlib_root), region="cn", expression_cache=None,
              dataset_cache=None)
    df = D.features([SYMBOLS[0]], ["$" + k for k in FIELDS],
                    start_time=hour_stamp(int(panel["open_ms"][0]))[:10],
                    end_time=hour_stamp(int(panel["open_ms"][-1])), freq=QLIB_FREQ).sort_index()
    ms = np.asarray([int(x.value // 1_000_000)
                     for x in df.index.get_level_values("datetime")], np.int64)
    if not np.array_equal(ms, panel["open_ms"]):
        raise RuntimeError("Qlib timestamp mismatch (rows %d vs %d)" % (len(ms), len(panel["open_ms"])))
    cols = {k: df["$" + k].to_numpy(dtype=np.float64) for k in FIELDS}
    for k in FIELDS:
        if not np.isfinite(cols[k]).all() or not np.allclose(cols[k], panel[k], rtol=1e-6,
                                                             atol=1e-9):
            raise RuntimeError("Qlib value mismatch on field " + k)
    readback = {"open_ms": ms, **cols}
    return readback, {"qlib_version": qlib.__version__, "read_path": "qlib.data.D.features",
                      "qlib_freq": QLIB_FREQ,
                      "qlib_rows_identical_to_raw": True, "rows": int(len(ms)),
                      "float32_roundtrip_rtol": 1e-6}


def load_funding(symbol, open_ms, end_ms, root=RAW_ROOT):
    """Official funding settlements bucketed into their hourly bar (modeled rows ignored)."""
    path = Path(root) / "binance/usdm/funding" / symbol / (symbol + "-funding.jsonl.gz")
    events = [[] for _ in open_ms]
    report = {"file_missing": not path.is_file(), "official": 0, "charged_events": 0,
              "modeled_ignored": 0, "other_ignored": 0,
              "missing_intervals_are_zero_not_modeled": True,
              "bars_total": len(open_ms), "bars_with_official": 0,
              "funding_time_offset_note": "Binance stamps funding_time_ms a few ms after the "
                                           "boundary, so the event lands inside its own bar and "
                                           "is charged to a book live at that bar's open"}
    if not path.is_file():
        return events, report
    report["source_sha256"] = sha256_file(path)
    lo, hi = int(open_ms[0]), int(end_ms) + MS_HOUR
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            t = int(row["funding_time_ms"])
            if not lo <= t < hi:
                continue
            if row.get("truth_status") != "official":
                report["modeled_ignored" if row.get("truth_status") in ("modeled",
                                                                        "modeled_funding")
                       else "other_ignored"] += 1
                continue
            rate, mark = float(row["funding_rate"]), float(row["mark_price"])
            if not math.isfinite(rate) or not math.isfinite(mark) or mark <= 0:
                raise RuntimeError("invalid official funding row")
            index = int(np.searchsorted(open_ms, t, side="right") - 1)
            if not 0 <= index < len(events):
                raise RuntimeError("funding observation outside the registered window")
            events[index].append((t, rate * mark))
            report["official"] += 1
    # independent recount from the bucket structure the simulator actually consumes
    report["charged_events"] = sum(len(bucket) for bucket in events)
    report["bars_with_official"] = sum(bool(x) for x in events)
    if report["charged_events"] != report["official"]:
        raise RuntimeError("official funding rows dropped while bucketing")
    return events, report


# --------------------------------------------------------------------------- #
# Signal layer: rolling FPCA one-step-ahead hourly return forecast             #
# --------------------------------------------------------------------------- #
def _lasso_fit(Za, Zb, lam, iters):
    """Batched coordinate descent for every target component.

    Za: (C, W, J) point-in-time standardized auxiliary scores,
    Zb: (C, W, J) point-in-time standardized target scores.
    Returns W_ with W_[c, jt, k] (C, J, J). Deterministic: fixed sweep count, no RNG.
    """
    C, W, J = Za.shape
    W_ = np.zeros((C, J, J), dtype=np.float64)
    z = np.einsum("cwk,cwk->ck", Za, Za) / W            # per-feature energy (=1 when z-scored)
    z = np.where(z > 1e-12, z, 1.0)
    for _ in range(iters):
        for jt in range(J):
            pred = np.matmul(Za, W_[:, jt, :, None])[:, :, 0]          # (C, W)
            resid = Zb[:, :, jt] - pred
            grad = np.matmul(Za.transpose(0, 2, 1), resid[:, :, None])[:, :, 0] / W  # (C, J)
            g = grad + z * W_[:, jt, :]
            W_[:, jt, :] = np.sign(g) * np.maximum(np.abs(g) - lam, 0.0) / z
    return W_


def forecast_series(ret, window, horizon=HORIZON, js=FPCA_J, lam=LASSO_LAMBDA,
                    iters=CD_ITERS, n_lo=None, n_hi=None, chunk=CHUNK):
    """Rolling FPCA forecasts; returns {j: float array of len(ret)} with NaN before defined.

    Point-in-time by construction: forecast at index n only reads ret[0 .. n-1].
    """
    ret = np.asarray(ret, np.float64)
    M = len(ret)
    js = tuple(sorted(set(int(j) for j in js)))
    out = {int(j): np.full(M, np.nan, dtype=np.float64) for j in js}
    if M < window + horizon + FUNCTION_LENGTH + 1:
        return out
    tw = np.lib.stride_tricks.sliding_window_view(ret, FUNCTION_LENGTH)  # (M-23, 24)
    # numpy appends the sliding window axis last: (L-W+1, 24, W) -> (L-W+1, W, 24)
    sw = np.lib.stride_tricks.sliding_window_view(tw, window, axis=0).transpose(0, 2, 1)
    lo = max(int(n_lo) if n_lo is not None else 0, horizon + window + FUNCTION_LENGTH - 1)
    hi = min(int(n_hi) if n_hi is not None else M, M - horizon + 1)
    for start in range(lo, hi, chunk):
        stop = min(start + chunk, hi)
        if stop <= start:
            break
        Xt = sw[start - window - FUNCTION_LENGTH + 1: stop - window - FUNCTION_LENGTH + 1]
        Xa = sw[start - horizon - window - FUNCTION_LENGTH + 1:
                stop - horizon - window - FUNCTION_LENGTH + 1]
        cur = tw[start - FUNCTION_LENGTH: stop - FUNCTION_LENGTH]   # aux of pair m=n
        if len(Xt) != stop - start or len(Xa) != stop - start or len(cur) != stop - start:
            raise RuntimeError("forecast window misalignment")
        n = stop - start
        mu_t = Xt.mean(axis=1)
        mu_a = Xa.mean(axis=1)
        Dt = Xt - mu_t[:, None, :]
        Da = Xa - mu_a[:, None, :]
        Ct = np.matmul(Dt.transpose(0, 2, 1), Dt) / window
        Ca = np.matmul(Da.transpose(0, 2, 1), Da) / window
        _, ev_t = np.linalg.eigh(Ct)
        _, ev_a = np.linalg.eigh(Ca)
        jmax = max(js)
        phi_t = ev_t[:, :, ::-1][:, :, :jmax]           # descending eigenvalue order
        phi_a = ev_a[:, :, ::-1][:, :, :jmax]
        Bt = np.matmul(Dt, phi_t)                        # (n, W, jmax)
        Ba = np.matmul(Da, phi_a)
        mB, sB = Bt.mean(axis=1), Bt.std(axis=1)
        mA, sA = Ba.mean(axis=1), Ba.std(axis=1)
        sB = np.where(sB > 1e-12, sB, 1.0)
        sA = np.where(sA > 1e-12, sA, 1.0)
        Zb = (Bt - mB[:, None, :]) / sB[:, None, :]
        Za = (Ba - mA[:, None, :]) / sA[:, None, :]
        ac = cur - mu_a                                  # current auxiliary, demeaned
        acs = np.einsum("ci,cij->cj", ac, phi_a)         # (n, jmax) row-wise scores
        zcur = (acs - mA) / sA
        for j in js:
            W_ = _lasso_fit(Za[:, :, :j], Zb[:, :, :j], lam, iters)
            yhat = np.einsum("cj,cjk->ck", zcur[:, :j], W_)   # (n, j)
            beta = yhat * sB[:, :j] + mB[:, :j]
            rhat = mu_t[:, FUNCTION_LENGTH - 1] + np.einsum("cj,cj->c", beta,
                                                            phi_t[:, FUNCTION_LENGTH - 1, :j])
            if not np.isfinite(rhat).all():
                raise RuntimeError("non-finite FPCA forecast")
            out[int(j)][start:stop] = rhat
    return out


def direction_of(fc):
    """Record direction rule: r_hat > 0 long, < 0 short, == 0 flat."""
    d = np.zeros(len(fc), dtype=np.int8)
    ok = np.isfinite(fc)
    d[ok & (fc > 0)] = 1
    d[ok & (fc < 0)] = -1
    return d


def utc_hours(open_ms):
    """UTC hour-of-day of each hourly log return (ret[m] is bar m+1's return)."""
    return ((np.asarray(open_ms[1:], np.int64) // MS_HOUR) % 24).astype(np.int8)


def baseline_series(ret, window, hours):
    """Record item 2 simple baselines on identical timestamps, point-in-time only."""
    ret = np.asarray(ret, np.float64)
    M = len(ret)
    out = {name: np.zeros(M, dtype=np.int8) for name in
           ("unconditional_sign", "ar1_random_walk", "momentum_last_hour",
            "reversal_last_hour", "utc_hour_seasonality")}
    if M < window + FUNCTION_LENGTH + 1:
        return out
    idx = np.arange(window + FUNCTION_LENGTH - 1, M)
    prev = ret[idx - 1]
    out["momentum_last_hour"][idx] = np.sign(prev)
    out["reversal_last_hour"][idx] = -np.sign(prev)
    for i, n in enumerate(idx):
        tail = ret[n - window:n]
        out["unconditional_sign"][n] = np.sign(tail.sum())
        num = float(np.sum(ret[n - window:n] * ret[n - window - 1:n - 1]))
        den = float(np.sum(ret[n - window - 1:n - 1] ** 2))
        phi = num / den if den > 0 else 0.0
        out["ar1_random_walk"][n] = np.sign(phi * ret[n - 1])
        bucket = int(hours[n])
        same = ret[n - window:n][hours[n - window:n] == bucket]
        out["utc_hour_seasonality"][n] = np.sign(same.sum()) if len(same) else 0
    return out


def control_series(ret, window, hours, ridge=1e-6):
    """Record item 5 control models (no FPCA), fitted on the same W training pairs.

    (a) raw_lagged_returns: intercept + the same 24 lagged returns.
    (b) named_controls: intercept + last-hour return + 24-hour return + 24h realized
        volatility + UTC hour-of-day dummies.
    Returns {name: int8 direction array}.
    """
    ret = np.asarray(ret, np.float64)
    M = len(ret)
    out = {"raw_lagged_returns_ols": np.zeros(M, dtype=np.int8),
           "named_controls_ols": np.zeros(M, dtype=np.int8)}
    if M < window + FUNCTION_LENGTH:
        return out
    tw = np.lib.stride_tricks.sliding_window_view(ret, FUNCTION_LENGTH)
    sw = np.lib.stride_tricks.sliding_window_view(tw, window, axis=0).transpose(0, 2, 1)
    hw = np.lib.stride_tricks.sliding_window_view(hours, window)
    # first decidable n: training pair m = n-W must still have aux ret[m-24..m-1] in range
    lo, hi = window + FUNCTION_LENGTH, M
    for start in range(lo, hi, CHUNK):
        stop = min(start + CHUNK, hi)
        # pair m in [n-W, n-1]: aux = TW[m-24] (sw idx m-24), target = TW[m-23] (sw idx m-23)
        aux = sw[start - window - FUNCTION_LENGTH: stop - window - FUNCTION_LENGTH]
        tgt = sw[start - window - FUNCTION_LENGTH + 1:
                 stop - window - FUNCTION_LENGTH + 1]
        y = tgt[:, :, FUNCTION_LENGTH - 1]                       # ret[m], m in [n-W, n-1]
        hours_matrix = hw[start - window:stop - window]           # hours[m], same pairs
        designs = {}
        designs["raw_lagged_returns_ols"] = np.concatenate(
            [np.ones((stop - start, window, 1)), aux], axis=2)
        dummies = np.zeros((stop - start, window, 24), dtype=np.float64)
        row = np.arange(window)[None, :]
        dummies[np.arange(stop - start)[:, None], row[None, :], hours_matrix] = 1.0
        named = [np.ones((stop - start, window, 1)),
                 aux[:, :, FUNCTION_LENGTH - 1][:, :, None],
                 aux.sum(axis=2, keepdims=True),
                 aux.std(axis=2, keepdims=True),
                 dummies]
        designs["named_controls_ols"] = np.concatenate(named, axis=2)
        for name, X in designs.items():
            p = X.shape[2]
            XtX = np.matmul(X.transpose(0, 2, 1), X) + ridge * np.eye(p)[None, :, :]
            Xty = np.matmul(X.transpose(0, 2, 1), y[:, :, None])
            coef = np.linalg.solve(XtX, Xty)[:, :, 0]
            # current aux must be ret[n-24..n-1]: no ret[n] may enter the design
            cur_tw = tw[start - FUNCTION_LENGTH: stop - FUNCTION_LENGTH]
            if name == "raw_lagged_returns_ols":
                pred = np.einsum("ck,ck->c",
                                 np.concatenate([np.ones((stop - start, 1)), cur_tw],
                                                axis=1), coef)
            else:
                cur_hours = hours[start:stop]
                cur_dum = np.zeros((stop - start, 24), dtype=np.float64)
                cur_dum[np.arange(stop - start), cur_hours] = 1.0
                cur_named = np.concatenate(
                    [np.ones((stop - start, 1)), cur_tw[:, -1:],
                     cur_tw.sum(axis=1, keepdims=True), cur_tw.std(axis=1, keepdims=True),
                     cur_dum], axis=1)
                pred = np.einsum("ck,ck->c", cur_named, coef)
            out[name][start:stop] = np.sign(pred)
    return out


def segment_returns(panel, min_bars):
    """Contiguous runs of an hourly panel -> list of (start_index, ret array)."""
    ms = np.asarray(panel["open_ms"], np.int64)
    close = np.asarray(panel["close"], np.float64)
    if len(ms) < 2:
        return []
    edges = [0]
    edges.extend((np.flatnonzero(np.diff(ms) != MS_HOUR) + 1).tolist())
    edges.append(len(ms))
    runs = []
    for a, b in zip(edges[:-1], edges[1:]):
        if b - a >= min_bars:
            seg_close = close[a:b]
            seg = np.diff(np.log(seg_close))
            runs.append((a, seg))
    return runs


def accuracy(dir_series, realized_sign, n_lo, n_hi):
    """Directional accuracy over identical timestamps (0-1 loss complement)."""
    lo, hi = max(n_lo, 0), min(n_hi, len(dir_series))
    if hi <= lo:
        return None, 0
    d = dir_series[lo:hi]
    y = realized_sign[lo:hi]
    mask = (d != 0) & (y != 0)
    total = int(mask.sum())
    if total == 0:
        return None, 0
    return float(np.mean(d[mask] == y[mask])), total


def mcnemar_test(a_ok, b_ok):
    """Paired directional-accuracy test (McNemar, chi-square with continuity correction)."""
    n01 = int(np.sum(a_ok & ~b_ok))
    n10 = int(np.sum(~a_ok & b_ok))
    if n01 + n10 == 0:
        return {"n01": 0, "n10": 0, "chi2": 0.0, "p_value": 1.0, "reject_equal_5pct": False}
    chi2 = (abs(n01 - n10) - 1) ** 2 / (n01 + n10)
    p = math.erfc(math.sqrt(max(chi2, 0.0) / 2.0))
    return {"n01": n01, "n10": n10, "chi2": float(chi2), "p_value": float(p),
            "reject_equal_5pct": bool(p < 0.05)}


# --------------------------------------------------------------------------- #
# DCA ladder execution (candidate body DCA rail, per-fill accounting)          #
# --------------------------------------------------------------------------- #
def window_indices(ms, start, end):
    return int(np.searchsorted(ms, utc_ms(start))), \
        int(np.searchsorted(ms, utc_ms(end) + MS_DAY))


def ret_slice(window):
    """Forecast index range whose realised return falls inside a bar window.

    Forecast n (made at bar n's close) realises during bar n+1, so bars [lo, hi) are
    realised by forecasts n in [lo-1, hi-1).  Accuracy never straddles a phase edge.
    """
    return max(0, int(window[0]) - 1), max(0, int(window[1]) - 1)


def empty_metric(i0, i1):
    return {"gross_pnl": 0.0, "fees": 0.0, "funding": 0.0, "net_pnl": 0.0,
            "ending_equity": START_EQUITY, "episodes": 0, "fills": 0, "adds": 0,
            "turnover_usdt": 0.0, "sharpe": 0.0, "max_dd_pct": 0.0, "max_dd_usdt": 0.0,
            "annualized_return": 0.0, "max_effective_leverage": 0.0,
            "capital_utilization": 0.0, "tp_hits": 0, "stop_hits": 0, "margin_calls": 0,
            "end_exits": 0, "open_at_end": 0, "horizon_exits": 0, "signal_flat_exits": 0,
            "rebalance_exits": 0, "layer_hist": [0] * LADDER_LEVELS,
            "decomposition_ok": True, "daily_equity": [START_EQUITY] * max(0, i1 - i0)}


def metric_block(m):
    keys = ("gross_pnl", "fees", "funding", "net_pnl", "ending_equity", "episodes", "fills",
            "adds", "turnover_usdt", "sharpe", "max_dd_pct", "max_dd_usdt",
            "annualized_return", "max_effective_leverage", "capital_utilization", "tp_hits",
            "stop_hits", "margin_calls", "end_exits", "open_at_end", "horizon_exits",
            "signal_flat_exits", "rebalance_exits")
    return {k: m[k] for k in keys}


def simulate(panel, funding_events, direction, dca, i0, i1, cost, instrument):
    """Registered ladder on hourly bars for one (signal, DCA, phase, cost) cell.

    One hourly bar = one deterministic conservative ordering:
      open actions (signal-flat / direction-flip reduce-only flatten, then tranche #1 at
      open + one adverse tick), then that bar's official funding settlements while the
      book is live, then the intrabar rail (open-gap invalidation, margin guard, adverse
      scale-ins in level order with stop-before-adds, resting invalidation, breakeven TP),
      then the record's registered exit - flatten at the close of the bar in which the
      position was opened (end of the forecasted one-hour interval) - one adverse tick.
    Gross PnL accumulates only from the independent price-PnL expression.
    """
    if i1 <= i0:
        return empty_metric(i0, i1)
    if i0 < 0 or i1 > len(panel["open"]):
        raise ValueError("window outside panel")
    tick = instrument["tick"] * int(cost.get("slip_ticks", 1))
    fee_rate = float(cost.get("fee_override",
                              instrument["taker_fee"] * float(cost.get("fee_mult", 1.0))))
    if fee_rate < 0:
        raise ValueError("negative fee")
    mult = float(dca["size_multiplier"])
    spacing = float(dca["spacing_pct"])
    tp_pct = float(dca["breakeven_tp_pct"])
    inv_pct = float(dca["invalidation_pct"])
    delay = int(cost.get("entry_delay", 0))
    funding_mult = float(cost.get("funding_mult", 1.0))
    no_funding = bool(cost.get("no_funding", False))
    n_bars = i1 - i0
    cash = np.zeros(n_bars)
    unreal = np.zeros(n_bars)
    gross = fees = funding_paid = turnover = realized = 0.0
    eps = fills = adds = tp_hits = stop_hits = margin_calls = 0
    horizon_exits = end_exits = signal_flat_exits = rebalance_exits = 0
    max_lev = max_util = 0.0
    hist = [0] * LADDER_LEVELS
    O, H, L, C, ms = (panel["open"], panel["high"], panel["low"], panel["close"],
                      panel["open_ms"])
    qty = basis = 0.0
    entry_price = 0.0
    quote0 = 0.0
    entry_bar = -1
    next_level = 1
    layers_used = 0
    held_dir = 0

    def close_position(exit_px, base, reason):
        nonlocal qty, basis, gross, fees, realized, turnover, fills, funding_paid
        nonlocal horizon_exits, end_exits, signal_flat_exits, rebalance_exits, hist
        ep_gross = qty * exit_px - basis              # independent price-only accumulator
        gross += ep_gross
        exit_fee = abs(qty) * abs(exit_px) * fee_rate
        realized += ep_gross - exit_fee
        fees += exit_fee
        turnover += abs(qty * exit_px)
        fills += 1
        cash[base] += ep_gross - exit_fee
        hist[0] += 1
        hist[min(LADDER_LEVELS - 1, layers_used)] += 1
        if reason == "horizon":
            horizon_exits += 1
        elif reason == "close":
            end_exits += 1
        elif reason == "signal_flat":
            signal_flat_exits += 1
        elif reason == "rebalance":
            rebalance_exits += 1
        if TRACE is not None:
            TRACE.append({"kind": "exit", "bar": i0 + base, "price": float(exit_px),
                          "qty": float(qty), "fee": float(exit_fee), "gross": float(ep_gross),
                          "reason": reason, "layers": layers_used})
        qty = basis = 0.0

    for i in range(i0, i1):
        base = i - i0
        sig_idx = i - 1 - delay
        target = int(direction[sig_idx]) if 0 <= sig_idx < len(direction) else 0
        open_px = float(O[i])
        if open_px <= 0:
            raise RuntimeError("nonpositive open")
        if qty != 0 and target == 0:
            side = 1.0 if held_dir > 0 else -1.0
            close_position(open_px - tick * side, base, "signal_flat")
        elif qty != 0 and target != 0 and target != held_dir:
            side = 1.0 if held_dir > 0 else -1.0
            close_position(open_px - tick * side, base, "rebalance")
        if qty == 0 and target != 0:
            side = 1.0 if target > 0 else -1.0
            fill = open_px + tick * side               # one adverse tick, both directions
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
                    entry_bar = i
                    next_level = 1
                    layers_used = 1
                    held_dir = target
                    eps += 1
                    if TRACE is not None:
                        TRACE.append({"kind": "entry", "bar": i, "price": float(fill),
                                      "qty": float(qty0), "fee": float(fee0),
                                      "layers": layers_used})
        if qty != 0:
            side = 1.0 if qty > 0 else -1.0
            old_avg = basis / qty
            old_stop = old_avg * (1.0 - side * inv_pct)
            if not no_funding:
                for ev_t, amount in funding_events[i]:
                    if ev_t <= int(ms[i]):
                        continue
                    charge = qty * amount * funding_mult   # long pays, short receives
                    realized -= charge
                    funding_paid += charge
                    cash[base] -= charge
                    if TRACE is not None:
                        TRACE.append({"kind": "funding", "bar": i, "timestamp_ms": ev_t,
                                      "amount": float(charge)})
            if i > entry_bar and ((side > 0 and open_px <= old_stop)
                                  or (side < 0 and open_px >= old_stop)):
                close_position(open_px - tick * side, base, "stop")
                stop_hits += 1
            if qty != 0:
                adverse = float(L[i]) if side > 0 else float(H[i])
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
                    reached = float(L[i]) <= trigger if side > 0 else float(H[i]) >= trigger
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
                    if TRACE is not None:
                        TRACE.append({"kind": "add", "bar": i, "price": float(fill),
                                      "qty": float(add_qty), "fee": float(add_fee),
                                      "layers": layers_used})
            if qty != 0:
                avg = basis / qty
                stop = avg * (1.0 - side * inv_pct)
                take = avg * (1.0 + side * tp_pct)
                pre_hit = float(L[i]) <= old_stop if side > 0 else float(H[i]) >= old_stop
                new_hit = float(L[i]) <= stop if side > 0 else float(H[i]) >= stop
                if pre_hit or new_hit:
                    reached = [x for x, crossed in ((old_stop, pre_hit), (stop, new_hit))
                               if crossed]
                    trigger = min(reached) if side > 0 else max(reached)
                    close_position(trigger - tick * side, base, "stop")
                    stop_hits += 1
                elif (float(H[i]) >= take) if side > 0 else (float(L[i]) <= take):
                    close_position(take - tick * side, base, "tp")
                    tp_hits += 1
            # record exit: end of the forecasted one-hour interval = this bar's close
            if qty != 0 and i == entry_bar:
                close_position(float(C[i]) - tick * side, base, "horizon")
            if qty != 0 and i == i1 - 1:              # window-boundary safety net
                close_position(float(C[i]) - tick * side, base, "close")
            if qty != 0 and TRACE is not None:
                TRACE.append({"kind": "bar_end_position", "bar": i, "qty": float(qty)})
        unreal[base] = qty * float(C[i]) - basis if qty != 0 else 0.0
        if TRACE is not None:
            eq_now = START_EQUITY + realized + float(unreal[base])
            TRACE.append({"kind": "equity", "bar": i, "equity": float(eq_now),
                          "realized": float(START_EQUITY + realized),
                          "unrealized": float(unreal[base])})
    equity = START_EQUITY + np.cumsum(cash) + unreal
    net = float(realized)
    independent_cash = float(cash.sum())
    high = np.maximum.accumulate(np.r_[START_EQUITY, equity])
    dd = high[1:] - equity
    returns = np.diff(np.r_[START_EQUITY, equity]) / np.maximum(np.r_[START_EQUITY, equity[:-1]],
                                                                1e-9)
    sd = float(np.std(returns, ddof=1)) if len(returns) > 1 else 0.0
    sharpe = float(np.mean(returns) / sd * math.sqrt(365 * 24)) if sd > 1e-12 else 0.0
    duration = max(1, n_bars)
    annual = float((max(float(equity[-1]), 1e-9) / START_EQUITY)
                   ** (365 * 24 / duration) - 1)
    return {"gross_pnl": float(gross), "fees": float(fees), "funding": float(funding_paid),
            "net_pnl": net, "ending_equity": float(equity[-1]) if len(equity) else START_EQUITY,
            "episodes": eps, "fills": fills, "adds": adds, "turnover_usdt": float(turnover),
            "sharpe": sharpe,
            "max_dd_pct": float(np.max(dd / np.maximum(high[1:], 1e-9)) * 100) if len(dd) else 0.0,
            "max_dd_usdt": float(np.max(dd)) if len(dd) else 0.0, "annualized_return": annual,
            "max_effective_leverage": float(max_lev), "capital_utilization": float(max_util),
            "tp_hits": tp_hits, "stop_hits": stop_hits, "margin_calls": margin_calls,
            "end_exits": end_exits, "open_at_end": 1 if qty != 0 else 0,
            "horizon_exits": horizon_exits,
            "signal_flat_exits": signal_flat_exits, "rebalance_exits": rebalance_exits,
            "layer_hist": hist,
            "decomposition_ok": abs(gross - fees - funding_paid - net) < 1e-3
            and abs(net - independent_cash) < 1e-3
            and abs(net - (float(equity[-1]) - START_EQUITY)) < 1e-3,
            "daily_equity": equity.tolist()}


# --------------------------------------------------------------------------- #
# Selector / disposition (cohort-selector-v1, cohort-disposition-v1)           #
# --------------------------------------------------------------------------- #
def cell_key(row):
    return (row["symbol"], row["timeframe"], row["window_functions"], row["fpca_dim_j"],
            *(row[k] for k in DCA_AXES))


def _axis_index(axis_name, value):
    domain = STRATEGY_AXES.get(axis_name) or DCA_AXES[axis_name]
    return list(domain).index(value)


def neighbourhood(winner, historical):
    if winner["grid"] != "historical" or any(r["grid"] != "historical" for r in historical):
        raise ValueError("selector may only read historical rows")
    found = {cell_key(r): r for r in historical}
    steps = [((2,), list(STRATEGY_AXES["window_functions"])),
             ((3,), list(STRATEGY_AXES["fpca_dim_j"]))]
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
            _axis_index("window_functions", r["window_functions"]),
            _axis_index("fpca_dim_j", r["fpca_dim_j"]),
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
        return None, {"cull_reasons": ["no_qualifying_candidate"],
                      "best_historical_episodes": best}
    winner = min(candidates, key=_order_key)
    lookup = {g: next(r for r in rows[g] if cell_key(r) == cell_key(winner)) for g in GRIDS}
    reasons = []
    oos = lookup["oos"]
    if not (float(oos["net_pnl"]) > 0 and float(oos["sharpe"]) > 0
            and int(oos["episodes"]) >= MIN_EPISODES_OOS):
        reasons.append("oos_economic")
    if float(lookup["full"]["net_pnl"]) <= 0:
        reasons.append("full_economic")
    weak = [g for g in STRESS_GRIDS if float(lookup[g]["net_pnl"]) <= 0]
    if weak:
        reasons.append("robustness_economic:" + ",".join(weak))
    neighbors = neighbourhood(winner, hist)
    if not neighbors["passed"]:
        reasons.append("parameter_neighbourhood")
    phases = {g: metric_block(lookup[g]) for g in ("historical", "oos", "full")}
    robustness = {g: metric_block(lookup[g]) for g in STRESS_GRIDS}
    detail = {"winner": {k: winner[k] for k in ("window_functions", "fpca_dim_j", *DCA_AXES)},
              "winner_source_grid": "historical", "best_historical_episodes": best,
              "neighbourhood": neighbors, "phases": phases, "robustness": robustness,
              "metrics": {**phases, "robustness": robustness, "neighbourhood": neighbors},
              "cull_reasons": reasons}
    return (winner if not reasons else None), detail


def write_grid(path, rows):
    metric_keys = list(metric_block(empty_metric(0, 0)))
    cols = ["symbol", "timeframe", "window_functions", "fpca_dim_j", "case_label", *DCA_AXES,
            "grid", *metric_keys, "decomposition_ok"]
    with open(path, "x", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def cost_for(grid):
    return {"fee_2x": {"fee_mult": 2.0}, "funding_2x": {"funding_mult": 2.0},
            "entry_delay_1_bar": {"entry_delay": 1}, "slippage_2ticks": {"slip_ticks": 2},
            "no_funding": {"no_funding": True}, "no_funding_full": {"no_funding": True},
            "cost_attrition_40bps": {"fee_override": 0.004}}.get(grid, {})


# --------------------------------------------------------------------------- #
# Falsification battery (record items 1-7; all seven registered before compute) #
# --------------------------------------------------------------------------- #
def _status(flag):
    return "FALSIFIED" if flag else "NOT_FALSIFIED"


def falsification_report(fc_ref, horizons, baselines, controls, spot, pit, windows,
                         realized_sign, oos_rows_ref, ref_case):
    """Evaluate every registered record item on the pre-registered reference case."""
    n0, n1 = ret_slice(windows["oos"])
    h0, h1 = ret_slice(windows["historical"])
    acc_ref, k_ref = accuracy(direction_of(fc_ref), realized_sign, n0, n1)
    acc_hist, k_hist = accuracy(direction_of(fc_ref), realized_sign, h0, h1)
    items = {}

    # item 1 -----------------------------------------------------------------
    if acc_ref is None:
        items["walk_forward_directional"] = {
            "status": "INDETERMINATE", "oos_sign_accuracy": None,
            "reason": "no decidable OOS forecast for the reference case"}
        item1_falsified = False
    else:
        item1_falsified = acc_ref <= 0.50
        items["walk_forward_directional"] = {
            "status": _status(item1_falsified), "oos_sign_accuracy": acc_ref,
            "oos_decidable": k_ref, "threshold": "<= 0.50 falsifies",
            "action_if_falsified": "retain as forecasting/risk research artifact only"}

    # item 2 -----------------------------------------------------------------
    base_acc = {}
    for name, series in baselines.items():
        a, k = accuracy(series, realized_sign, n0, n1)
        base_acc[name] = {"accuracy": a, "decidable": k}
    comparable = {n: v for n, v in base_acc.items() if v["accuracy"] is not None}
    if acc_ref is None or not comparable:
        items["benchmark_incremental_value"] = {
            "status": "INDETERMINATE", "reason": "missing reference or baseline accuracies"}
        item2_falsified = False
        best_name, best_acc, test = None, None, None
    else:
        best_name = min(sorted(comparable), key=lambda n: (-comparable[n]["accuracy"], n))
        best_acc = comparable[best_name]["accuracy"]
        # identical timestamps for BOTH sides: fpca decidable AND best baseline decidable
        f_dir = direction_of(fc_ref)[n0:n1]
        b_series = baselines[best_name][n0:n1]
        mask_ok = (f_dir != 0) & (realized_sign[n0:n1] != 0) & (b_series != 0)
        paired_n = int(mask_ok.sum())
        a_ok = (f_dir == realized_sign[n0:n1]) & mask_ok
        b_ok = (b_series == realized_sign[n0:n1]) & mask_ok
        test = mcnemar_test(a_ok, b_ok)
        paired_acc = (float(a_ok.sum()) / paired_n) if paired_n else None
        paired_base = (float(b_ok.sum()) / paired_n) if paired_n else None
        item2_falsified = bool(paired_n == 0
                               or (paired_acc is not None and paired_base is not None
                                   and paired_acc <= paired_base)
                               or not test["reject_equal_5pct"])
        items["benchmark_incremental_value"] = {
            "status": _status(item2_falsified), "fpca_accuracy": acc_ref,
            "best_baseline": best_name, "best_baseline_accuracy": best_acc,
            "paired": {"timestamps": paired_n, "fpca_accuracy": paired_acc,
                       "baseline_accuracy": paired_base},
            "baselines": base_acc, "mcnemar": test,
            "predeclared_loss": "0-1 directional loss on identical timestamps",
            "rule": "falsified if paired accuracy <= best baseline OR paired test p >= 0.05"}

    # item 3 -----------------------------------------------------------------
    items["leakage_model_selection"] = {
        "status": ("INDETERMINATE" if not pit.get("checked")
                   else _status(bool(pit.get("failed_samples")))),
        "pit_status": PIT_STATUS if not pit.get("failed_samples") else "LOOKAHEAD_DETECTED",
        **pit, "selection": "window_functions/fpca_dim_j are searched on the historical grid "
                            "only; lambda and the CD sweep count are fixed constants; the "
                            "reference case is the pre-registered domain midpoint",
        "action_if_falsified": "discard contaminated results and rerun from a clean split"}

    # item 4 -----------------------------------------------------------------
    if not oos_rows_ref:
        items["cost_aware_trading_translation"] = {
            "status": "INDETERMINATE", "reason": "no OOS rows for the reference case"}
        item4_falsified = False
        cost_aware = None
    else:
        best_row = max(oos_rows_ref, key=lambda r: (float(r["net_pnl"]), -int(r["episodes"])))
        per_pos = (float(best_row["net_pnl"]) / int(best_row["episodes"])
                   if int(best_row["episodes"]) else None)
        item4_falsified = float(best_row["net_pnl"]) <= 0
        cost_aware = {"reference_case": ref_case, "dca_configs_evaluated": len(oos_rows_ref),
                      "best_dca": {k: best_row[k] for k in DCA_AXES},
                      "oos_net_pnl_best_dca": float(best_row["net_pnl"]),
                      "oos_episodes_best_dca": int(best_row["episodes"]),
                      "oos_net_pnl_per_position_best_dca": per_pos,
                      "max_oos_net_pnl_over_dca": max(float(r["net_pnl"]) for r in oos_rows_ref)}
        items["cost_aware_trading_translation"] = {
            "status": _status(item4_falsified), **cost_aware,
            "threshold": "aggregate OOS net PnL <= 0 OR mean net return per position <= 0",
            "action_if_falsified": "do not progress the trade mapping"}

    # item 5 -----------------------------------------------------------------
    ctrl_acc = {}
    for name, series in controls.items():
        a, k = accuracy(series, realized_sign, n0, n1)
        ctrl_acc[name] = {"accuracy": a, "decidable": k}
    ctrl_vals = [v["accuracy"] for v in ctrl_acc.values() if v["accuracy"] is not None]
    if acc_ref is None or not ctrl_vals:
        items["overlap_competing_explanation_ablation"] = {
            "status": "INDETERMINATE", "reason": "missing reference or control accuracies"}
        item5_falsified = False
    else:
        item5_falsified = acc_ref <= min(ctrl_vals)      # loses ALL incremental value
        items["overlap_competing_explanation_ablation"] = {
            "status": _status(item5_falsified), "fpca_accuracy": acc_ref,
            "controls": ctrl_acc, "rule": "falsified if FPCA loses all incremental "
                                          "improvement (accuracy <= every control)"}

    # item 6 -----------------------------------------------------------------
    profile = {}
    for k in HORIZONS_DIAGNOSTIC:
        series = horizons.get(k, fc_ref if k == 1 else None)
        if series is None:
            profile[str(k)] = {"accuracy": None, "decidable": 0}
            continue
        a, n = accuracy(direction_of(series), realized_sign, n0, n1)
        profile[str(k)] = {"accuracy": a, "decidable": n}
    acc_by_k = {k: v["accuracy"] for k, v in profile.items() if v["accuracy"] is not None}
    others = [a for k, a in acc_by_k.items() if k != "1"]
    reversal = bool(others and acc_ref is not None and max(others) > acc_ref)
    item6_falsified = bool(reversal and item1_falsified)
    items["horizon_perturbation"] = {
        "status": _status(item6_falsified), "profile": profile,
        "overlap_by_k": {str(k): FUNCTION_LENGTH - k for k in HORIZONS_DIAGNOSTIC},
        "one_hour_reversal": reversal, "k1_modern_oos_failed": item1_falsified,
        "rule": "record item 6: a flat/erratic profile alone is not failure; FALSIFIED only "
                "when reversal of the one-hour result coincides with k=1 OOS failure",
        "no_retuning": "window_functions/fpca_dim_j of the reference case are frozen for "
                       "every k"}

    # item 7 -----------------------------------------------------------------
    items["venue_robustness"] = {"status": "INDETERMINATE", **spot,
                                 "rule": "record item 7 requires a second major venue; the "
                                         "local canonical raw holds exactly one venue "
                                         "(BINANCE), so the venue dimension cannot be "
                                         "evaluated locally and conclusions stay scoped to it"}

    hits = [k for k, v in items.items() if v.get("status") == "FALSIFIED"]
    indeterminate = [k for k, v in items.items() if v.get("status") == "INDETERMINATE"]
    return {"reference_case": ref_case, "items": items,
            "item_order": list(FALSIFICATION_REGISTRY),
            "reference_case_sign_accuracy": {"oos": acc_ref, "oos_decidable": k_ref,
                                             "historical": acc_hist,
                                             "historical_decidable": k_hist},
            "falsification_hits": hits, "indeterminate_items": indeterminate,
            "battery_status": "FALSIFIED" if hits else
            ("INDETERMINATE" if indeterminate else "NOT_FALSIFIED"),
            "registered_item_count": len(FALSIFICATION_REGISTRY),
            "scope": "forecast-level items are evaluated on identical timestamps across the "
                     "whole OOS slice; item 4 uses the locked OOS grid of the reference case"}


def pit_sample_recheck(ret, window, j, fc_ref, samples=(0.15, 0.35, 0.5, 0.7, 0.9)):
    """Suffix-perturbation proof that forecast n never reads ret[n:] (j fixed by caller)."""
    M = len(ret)
    lo = window + HORIZON + FUNCTION_LENGTH - 1
    valid = np.flatnonzero(np.isfinite(fc_ref))
    if len(valid) == 0:
        return {"checked": 0, "failed_samples": 0, "sample_indices": []}
    picks = sorted({int(valid[min(len(valid) - 1, int(f * (len(valid) - 1)))])
                    for f in samples})
    failed, shown = 0, []
    for n in picks:
        probe = ret.copy()
        probe[n:] = probe[n:] * -1.37 + 11.0
        alt = forecast_series(probe, window, js=(j,), n_lo=n, n_hi=n + 1)
        value = alt[j][n]
        ok = (value == fc_ref[n]) if math.isfinite(fc_ref[n]) else math.isnan(value)
        if not ok:
            failed += 1
        shown.append({"n": n, "in_window": bool(n >= lo and n < M),
                      "unchanged": bool(ok)})
    return {"checked": len(picks), "failed_samples": failed, "sample_indices": shown,
            "method": "ret[n:] replaced by an arbitrary deterministic series; forecast n must "
                      "be bit-identical"}


# --------------------------------------------------------------------------- #
# Main run                                                                     #
# --------------------------------------------------------------------------- #
def run(spec, attempt_dir, log_path=None):
    counts = validate_spec(spec)
    path = Path(attempt_dir)
    expected = (Path(RESULTS_ROOT) / FAMILY_ID / "rounds" / spec["round_id"]
                / "attempts" / spec["run_id"])
    if path != expected or any((path / x).exists()
                               for x in ("DONE", "FAILED", "INCOMPLETE", "result.json",
                                         "state.json")):
        raise ValueError("attempt path/immutability violation")
    artifacts, logs = path / "artifacts", path / "logs"
    artifacts.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    log_fh = open(log_path or (logs / "run.log"), "w", encoding="utf-8")

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
        progress["updated_at_utc"] = now_utc()
        progress["runtime_seconds"] = round(time.monotonic() - started, 3)
        atomic_json(artifacts / "progress.json", progress)

    write_progress()
    try:
        inst = instrument_metadata()
        panel = raw_panel(SYMBOLS[0])
        readback, build = qlib_readback(panel)
        atomic_json(artifacts / "raw_build.json",
                    {"qlib": build, "raw_files": panel["raw_files"],
                     "rows": int(len(panel["open_ms"])),
                     "first_bar_utc": hour_stamp(int(panel["open_ms"][0])),
                     "last_bar_utc": hour_stamp(int(panel["open_ms"][-1])),
                     "gap_check": "fail-closed: hourly gap/duplicate raises"})
        funding, funding_report = load_funding(SYMBOLS[0], readback["open_ms"],
                                               readback["open_ms"][-1])
        atomic_json(artifacts / "funding_coverage.json", {SYMBOLS[0]: funding_report})
        close = readback["close"]
        if not np.isfinite(close).all() or np.any(close <= 0):
            raise RuntimeError("invalid close series after readback")
        ret = np.diff(np.log(close))
        hours = utc_hours(readback["open_ms"])
        windows = {k: window_indices(readback["open_ms"], *v) for k, v in PHASES.items()}
        if windows["historical"][1] != windows["oos"][0] or windows["full"] != (
                windows["historical"][0], windows["oos"][1]):
            raise RuntimeError("non-contiguous split")
        log("loaded %d hourly bars, %d log returns, splits %s"
            % (len(close), len(ret), {k: list(v) for k, v in windows.items()}))

        # ---- signal layer (Qlib readback is the signal source) -------------- #
        global TRACE
        forecasts = {}
        for case in CASES:
            fc = forecast_series(ret, case["window_functions"], js=(case["fpca_dim_j"],))
            forecasts[case["label"]] = fc[case["fpca_dim_j"]]
            progress.setdefault("signal_cases_done", 0)
            progress["signal_cases_done"] += 1
            write_progress()
            log("forecast case %s done (%d defined)"
                % (case["label"], int(np.isfinite(forecasts[case["label"]]).sum())))
        ref_case = dict(REFERENCE_CASE)
        ref_key = "w%d_j%d" % (ref_case["window_functions"], ref_case["fpca_dim_j"])
        fc_ref = forecasts[ref_key]
        if not np.isfinite(fc_ref).any():
            raise RuntimeError("reference case produced no forecast")

        horizons = {1: fc_ref}
        for k in HORIZONS_DIAGNOSTIC[1:]:
            horizons[k] = forecast_series(ret, ref_case["window_functions"], horizon=k,
                                          js=(ref_case["fpca_dim_j"],))[ref_case["fpca_dim_j"]]
        baselines = baseline_series(ret, ref_case["window_functions"], hours)
        controls = control_series(ret, ref_case["window_functions"], hours)
        pit = pit_sample_recheck(ret, ref_case["window_functions"],
                                    ref_case["fpca_dim_j"], fc_ref)
        log("PIT suffix recheck: checked=%d failed=%d" % (pit["checked"], pit["failed_samples"]))

        signal_report = {}
        degenerate = []
        for case in CASES:
            d = direction_of(forecasts[case["label"]])
            defined = int(np.isfinite(forecasts[case["label"]]).sum())
            nonzero = int(np.sum(d != 0))
            signal_report[case["label"]] = {
                "window_functions": case["window_functions"], "fpca_dim_j": case["fpca_dim_j"],
                "defined_forecasts": defined, "long": int(np.sum(d == 1)),
                "short": int(np.sum(d == -1)), "flat": int(np.sum(d == 0)),
                "nonzero_forecasts": nonzero,
                "first_defined_index": int(np.flatnonzero(np.isfinite(forecasts[case["label"]]))[0]),
                "pit": "forecast n reads ret[0..n-1] only; tranche #1 fills at bar n+1's open",
                "degenerate": bool(nonzero == 0)}
            if nonzero == 0:
                degenerate.append(case["label"])
        signal_report["_reference_case"] = ref_case
        signal_report["_pit_recheck"] = pit
        signal_report["_pit_status"] = PIT_STATUS
        signal_report["_direction_rule"] = "r_hat > 0 long, r_hat < 0 short, r_hat == 0 flat"

        # ---- spot market-type diagnostic (record item 7 evidence) ----------- #
        # Index spaces differ from the perp after the spot gap, so the spot diagnostic
        # stays entirely inside the spot panel and is matched to the OOS phase by the
        # spot panel's own hourly timestamps.
        spot_panel = raw_panel_segmented(SPOT_SYMBOL, market="spot")
        spot_ms = np.asarray(spot_panel["open_ms"], np.int64)
        spot_ret = np.full(max(len(spot_ms) - 1, 0), np.nan, dtype=np.float64)
        runs = segment_returns(spot_panel, ref_case["window_functions"]
                               + HORIZON + FUNCTION_LENGTH + 2)
        spot_fc = np.full(len(spot_ret), np.nan, dtype=np.float64)
        spot_ran = []
        for start_idx, seg in runs:
            if len(seg) < ref_case["window_functions"] + HORIZON + FUNCTION_LENGTH + 1:
                continue
            spot_ret[start_idx:start_idx + len(seg)] = seg      # seg[i] = return of bar a+i+1
            seg_fc = forecast_series(seg, ref_case["window_functions"],
                                     js=(ref_case["fpca_dim_j"],))[ref_case["fpca_dim_j"]]
            spot_fc[start_idx:start_idx + len(seg_fc)] = seg_fc
            spot_ran.append({"segment_start": hour_stamp(int(spot_panel["open_ms"][start_idx])),
                             "segment_returns": int(len(seg)),
                             "defined_in_segment": int(np.isfinite(seg_fc).sum())})
        spot_dir = direction_of(spot_fc)
        spot_sign = np.zeros(len(spot_ret), dtype=np.float64)
        seen = np.isfinite(spot_ret)
        spot_sign[seen] = np.sign(spot_ret[seen])
        spot_oos = (int(np.searchsorted(spot_ms, utc_ms(PHASES["oos"][0]))),
                    int(np.searchsorted(spot_ms, utc_ms(PHASES["oos"][1]) + MS_DAY)))
        spot_acc, spot_n = accuracy(spot_dir, spot_sign, *ret_slice(spot_oos))
        perp_acc, perp_n = accuracy(direction_of(fc_ref), np.sign(ret),
                                    *ret_slice(windows["oos"]))
        spot = {"market_type": "spot (Binance spot klines, BTCUSDT 1h)",
                "same_venue_as_cohort": True,
                "contiguous_segments_used": spot_ran,
                "gaps_not_imputed": spot_panel["gaps"],
                "oos_sign_accuracy_spot": spot_acc, "oos_decidable_spot": spot_n,
                "oos_sign_accuracy_perp_reference": perp_acc,
                "oos_decidable_perp_reference": perp_n,
                "realized_series": "spot's own hourly log returns (never the perp's)",
                "timestamp_alignment": "spot OOS slice taken from the spot panel's own "
                                       "hourly timestamps; gap rows stay NaN and are "
                "excluded by the identical decidable mask",
                "classification": "venue dimension NOT testable locally: the canonical raw "
                                  "holds one venue (BINANCE); spot is a second market type "
                                  "on the same venue, so portability stays venue-scoped",
                "used_in_a_registered_gate": False,
                "note": "diagnostic evidence inside record item 7 only; never a cohort, never "
                        "a selection input; reads the canonical raw directly (diagnostic), "
                        "while the production signal path is the Qlib readback"}

        # ---- grid search ---------------------------------------------------- #
        realized_sign = np.sign(ret)
        grid_rows = {g: [] for g in GRIDS}
        results, survivors = [], []
        hist_total = [0] * LADDER_LEVELS
        rows = {g: [] for g in GRIDS}
        for case in CASES:
            direction = direction_of(forecasts[case["label"]])
            for dca in DCA_GRID:
                for grid in GRIDS:
                    phase = ("historical" if grid in ("historical", "no_funding")
                             else "oos" if grid == "oos" else "full")
                    metric = simulate(readback, funding, direction, dca, *windows[phase],
                                      cost_for(grid), inst[SYMBOLS[0]])
                    row = {"symbol": SYMBOLS[0], "timeframe": TIMEFRAME,
                           "window_functions": case["window_functions"],
                           "fpca_dim_j": case["fpca_dim_j"], "case_label": case["label"],
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
        # one cohort = the complete registered local eligible universe; select once,
        # after every registered case x DCA x grid cell of that cohort exists
        selected, info = select_cohort(rows)
        record = {"cohort": SYMBOLS[0] + "/" + TIMEFRAME, "symbol": SYMBOLS[0],
                  "outcome": "SURVIVOR" if selected is not None else "CULLED", **info}
        results.append(record)
        if selected is not None:
            survivors.append(record)
        log("cohort %s outcome=%s cull=%s" % (record["cohort"], record["outcome"],
                                              ",".join(info["cull_reasons"]) or "none"))
        write_progress()

        signal_report["_degenerate_cases"] = degenerate
        atomic_json(artifacts / "signal_metrics.json", signal_report)

        expected_keys = {(SYMBOLS[0], TIMEFRAME, c["window_functions"], c["fpca_dim_j"],
                         *(d[k] for k in DCA_AXES)) for c in CASES for d in DCA_GRID}
        coverage = {g: {cell_key(r) for r in grid_rows[g]} == expected_keys
                    and len(grid_rows[g]) == len(expected_keys) for g in GRIDS}
        base_full = sum(float(r["net_pnl"]) for r in grid_rows["full"])
        stress_delta = {g: sum(float(r["net_pnl"]) for r in grid_rows[g]) - base_full
                        for g in ("fee_2x", "funding_2x", "slippage_2ticks",
                                  "cost_attrition_40bps")}
        delay_delta = (sum(float(r["net_pnl"]) for r in grid_rows["entry_delay_1_bar"])
                       - base_full)
        traded = any(float(r["turnover_usdt"]) > 0 for r in grid_rows["full"])

        def cell_delta(a, b, tol=1e-9):
            return any(abs(float(x["net_pnl"]) - float(y["net_pnl"])) > tol
                       for x, y in zip(grid_rows[a], grid_rows[b]))

        cells_with_funding = [i for i, r in enumerate(grid_rows["full"])
                              if abs(float(r["funding"])) > 1e-9]
        oos_rows_ref = [r for r in grid_rows["oos"]
                        if r["window_functions"] == ref_case["window_functions"]
                        and r["fpca_dim_j"] == ref_case["fpca_dim_j"]]
        falsification = falsification_report(fc_ref, horizons, baselines, controls, spot, pit,
                                             windows, realized_sign, oos_rows_ref, ref_case)
        falsification["record_item_count_card_excerpt"] = 2
        falsification["record_item_count_registered"] = len(FALSIFICATION_REGISTRY)
        atomic_json(artifacts / "falsification.json", falsification)
        for rec in survivors:
            rec["falsification_battery"] = {k: v.get("status")
                                            for k, v in falsification["items"].items()}

        # forecast index bounds are exact: lo = k + W + 23, hi = M - k (exclusive)
        pit_bounds_ok = all(
            signal_report[c["label"]]["first_defined_index"]
            == c["window_functions"] + HORIZON + FUNCTION_LENGTH - 1
            and signal_report[c["label"]]["defined_forecasts"]
            == len(ret) - c["window_functions"] - HORIZON - FUNCTION_LENGTH + 1
            for c in CASES)
        assertions = {
            "qlib_readback_0_9_7": build["qlib_version"] == "0.9.7"
            and build["qlib_rows_identical_to_raw"],
            "coverage_complete": all(coverage.values()),
            "case_evaluations_total_exact":
                sum(map(len, grid_rows.values())) == counts["case_evaluations_total"],
            "independent_gross_net_decomposition":
                all(r["decomposition_ok"] for g in GRIDS for r in grid_rows[g]),
            "dca_histogram_reconciles": hist_total[0] == sum(int(r["episodes"])
                                                            for r in grid_rows["full"]),
            # contract 7.2: a cost track must MATERIALLY change net PnL/equity (a no-op is
            # "not implemented"); the sign is not part of that requirement and is undecidable
            # in a domain where every cell ends at the base_quote entry floor, so every grid
            # is floored alike. The per-simulation sign/doubling is still gated by the
            # self-check test_cost_stresses_change_net_not_gross.
            "cost_stress_effective": (not traded) or all(
                cell_delta("full", g) for g in ("fee_2x", "slippage_2ticks",
                                                "cost_attrition_40bps")),
            "funding_2x_effective": not cells_with_funding or all(
                abs(float(grid_rows["funding_2x"][i]["net_pnl"])
                    - float(grid_rows["full"][i]["net_pnl"])) > 1e-9
                for i in cells_with_funding),
            "entry_delay_1_bar_effective": (not traded) or cell_delta("entry_delay_1_bar",
                                                                      "full"),
            "official_funding_only_no_modeled_charges":
                funding_report["charged_events"] == funding_report["official"]
                and funding_report["missing_intervals_are_zero_not_modeled"] is True,
            "point_in_time_no_lookahead":
                pit["checked"] >= 3 and pit["failed_samples"] == 0
                and pit_bounds_ok,
            "forecast_supply_nonzero_every_case": not degenerate,
            "registered_signal_constants_match":
                FUNCTION_LENGTH == 24 and HORIZON == 1 and list(WINDOWS_FN) == [90, 100, 110]
                and list(FPCA_J) == [2, 4, 6] and LASSO_LAMBDA == 0.01 and CD_ITERS == 50
                and REFERENCE_CASE == {"window_functions": 100, "fpca_dim_j": 4},
            "falsification_battery_evaluated":
                sorted(falsification["items"]) == sorted(FALSIFICATION_REGISTRY)
                and all(v.get("status") in ("FALSIFIED", "NOT_FALSIFIED", "INDETERMINATE")
                        for v in falsification["items"].values())
                and falsification["registered_item_count"] == 7,
            "selector_winner_is_historical_row":
                all(x.get("winner_source_grid") == "historical"
                    for x in results if x.get("winner")),
            "one_hour_exit_only": all(
                int(r["open_at_end"]) == 0 and int(r["horizon_exits"]) + int(r["stop_hits"])
                + int(r["tp_hits"]) + int(r["margin_calls"]) >= int(r["episodes"])
                for r in grid_rows["full"]),
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
                  "official_funding_events": funding_report["official"],
                  "disposition": "REJECT / NO_SURVIVOR" if not survivors else
                  ("SURVIVOR_FOUND" if len(survivors) == 1 else "MULTIPLE_SURVIVORS"),
                  "falsification_hits": hits,
                  "falsification_indeterminate": falsification.get("indeterminate_items", []),
                  "falsification_advice": {"rule": "record falsification battery hit => cull "
                                                   "or family REJECT (body FAILURE TAXONOMY); "
                                                   "verdict_recommendation is the v1.4.0 "
                                                   "disposition band only",
                                           "battery_status": falsification.get("battery_status"),
                                           "advice": "REJECT" if hits else None},
                  "verdict_recommendation": verdict,
                  "verdict_recommendation_basis":
                      "disposition band %s (0 survivors = REJECT, >=1 = PASS); the final "
                      "verdict is written to verdict.json by default (10.7)"
                      % DISPOSITION_MAPPING_VERSION,
                  "pit_status": PIT_STATUS,
                  "claimability_evidence": {
                      "basis": "performance_claimable is decided in verdict.json under "
                               "contract 9.6, not recommended here",
                      "missing_conditions": [],
                      "pit_note": "forecast n reads only ret[0..n-1]; tranche #1 fills at bar "
                                  "n+1's open and the position is flattened at that bar's "
                                  "close (record exit); in-run suffix recheck + self-check "
                                  "perturbation both gate this"},
                  "selector_version": SELECTOR_VERSION,
                  "disposition_version": DISPOSITION_VERSION,
                  "disposition_mapping_version": DISPOSITION_MAPPING_VERSION,
                  "grid_kinds": GRIDS, "funding_coverage": funding_report,
                  "stress_net_delta": stress_delta,
                  "signal_layer_report": {k: v for k, v in signal_report.items()
                                          if not k.startswith("_")},
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
        log("ARTIFACT_READY cohorts=%d survivors=%d rows=%d falsification=%s assertions=%s "
            "runtime=%.1fs" % (len(results), len(survivors),
                               result["case_evaluations_total"], ",".join(hits) or "none",
                               "all_true" if result["assertions_all_true"]
                               else str(result["assertion_failures"]),
                               time.monotonic() - started))
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


def emit_template(kind):
    if kind == "run-spec":
        doc = run_spec_template(created_at=now_utc())
        doc["round_id"] = FAMILY_ID + "-r1"
        doc["run_id"] = doc["round_id"] + "-u1"
    elif kind == "round-spec":
        doc = round_spec_template()
    else:
        raise SystemExit("unknown template kind: %s" % kind)
    print(json.dumps(doc, indent=2, ensure_ascii=False, allow_nan=False))
    return 0


def round_spec_template():
    """Round-spec skeleton; the host authoring step fills provenance/excerpts verbatim."""
    fam_path = Path(RESULTS_ROOT) / FAMILY_ID / "family.json"
    family_sha = sha256_file(fam_path) if fam_path.is_file() else None
    return {
        "schema_version": 1, "document_kind": "round_spec",
        "contract": "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md v2.0.0",
        "family_id": FAMILY_ID, "round_id": FAMILY_ID + "-r1", "created_at_utc": now_utc(),
        "ownership_mode": "direct_hermes",
        "ownership_note": "direct family: ownership is family_id + round_id + run_id on the "
                          "fixed results path. No Kanban card, board, dispatcher or task id "
                          "exists for this round and none may be written into any spec or "
                          "evidence file.",
        "provenance": {"source_record": "wiki brain quant/%s.md" % FAMILY_ID,
                       "family_json_sha256": family_sha,
                       "family_handoff_execution": "direct_hermes"},
        "parameter_contract": parameter_contract(),
        "params": [dict(c) for c in CASES],
        "parameter_domain": {"strategy_axes": list(STRATEGY_AXES),
                             "legal_cases_per_cohort": len(CASES),
                             "cases": [dict(c) for c in CASES]},
        "dca_domain": {**{k: list(v) for k, v in DCA_AXES.items()},
                       "grid": [dict(d) for d in DCA_GRID], "base_quote": BASE_QUOTE,
                       "config_count": len(DCA_GRID),
                       "base_quote_status": "PROJECT_PRE_REGISTERED_CONSTANT",
                       **{k + "_status": "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN"
                          for k in DCA_AXES}},
        "selector_disposition": {"selector_version": SELECTOR_VERSION,
                                 "disposition_version": DISPOSITION_VERSION,
                                 "disposition_mapping_version": DISPOSITION_MAPPING_VERSION,
                                 "gates": {"min_episodes_is": MIN_EPISODES_IS,
                                           "min_episodes_oos": MIN_EPISODES_OOS,
                                           "min_neighbour_same_sign_fraction": MIN_NEIGHBOUR}},
        "grids": list(GRIDS),
        "coverage_gate_g1": {"product": "each registered grid must contain exactly "
                                        "cohorts x strategy cases x 48 DCA configs",
                             "expected": expected_counts()},
        "robustness": {"standard_stress_grids": list(STRESS_GRIDS),
                       "additional_grids": ["no_funding", "no_funding_full",
                                            "cost_attrition_40bps"]},
        "falsification": list(FALSIFICATION),
        "engine": {"name": ENGINE_VERSION,
                   "script": {"repo_path": "container/scripts/310_fpca_hourly_run.py",
                              "container_path": "/scripts/310_fpca_hourly_run.py",
                              "deployed_host_path": "/Users/hong/workspace/qlib-apple-container/"
                                                    "scripts/310_fpca_hourly_run.py",
                              "sha256": sha256_file(Path(__file__).resolve())},
                   "self_check": {"repo_path": "container/scripts/tests/"
                                               "test_fpca_hourly_engine.py",
                                  "container_path": "/scripts/tests/test_fpca_hourly_engine.py",
                                  "sha256": sha256_file(Path(__file__).resolve().with_name(
                                      "tests") / "test_fpca_hourly_engine.py")},
                   "container": "qlib-run", "image": "qlib:0.9.7-arm64",
                   "qlib_version": "0.9.7", "seed": SEED},
        "data": {"raw_root": "/data/raw/binance/usdm", "start": PHASES["full"][0],
                 "end": PHASES["full"][1], "timezone": "UTC", "symbols": list(SYMBOLS),
                 "fields": list(FIELDS),
                 "signal_timeframe": {"raw_interval": TIMEFRAME, "qlib_freq": QLIB_FREQ},
                 "execution_timeframe": {"raw_interval": TIMEFRAME, "qlib_freq": QLIB_FREQ,
                                         "bars_per_day": 24}},
        "split": {**{k + "_start": v[0] for k, v in PHASES.items() if k in
                     ("historical", "oos")},
                  **{k + "_end": v[1] for k, v in PHASES.items() if k in
                     ("historical", "oos")},
                  "rule": "chronological; frozen before compute; OOS never used for selection"},
        "signal_constants": signal_constants(),
        "expected": expected_counts(), "expected_outputs": list(ARTIFACTS),
        "notes": "direct family: family_id + round_id + run_id only; no Kanban ownership keys "
                 "in this spec, in run-spec.json, or in any evidence file."}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-spec")
    parser.add_argument("--attempt-dir")
    parser.add_argument("--emit-template", choices=("run-spec", "round-spec"))
    args = parser.parse_args(argv)
    if args.emit_template:
        return emit_template(args.emit_template)
    if not args.run_spec:
        raise SystemExit("--run-spec is required (or --emit-template)")
    with open(args.run_spec, encoding="utf-8") as fh:
        spec = json.load(fh)
    result = run(spec, args.attempt_dir or str(Path(args.run_spec).parent))
    print(json.dumps({"status": result["status"],
                      "case_evaluations_total": result["case_evaluations_total"],
                      "assertions_all_true": result["assertions_all_true"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
