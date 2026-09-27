#!/usr/bin/env python3
"""Bayesian Parametric Portfolio Policies direct-family Qlib runner (round r1).

Registered record: Miguel C. Herculano, 'Bayesian Parametric Portfolio Policies',
arXiv:2602.21173v1 [q-fin.PM, econ.EM], 2026-02-23. DOI: 10.48550/arXiv.2602.21173.

Registered mechanism (candidate body, verbatim core):
  1. cross-sectionally standardized characteristics x_{k,i,t}
     (sum_i x = 0 and (1/N) sum_i x^2 = 1 at every decision bar)
  2. parametric portfolio policy  w_i = wbar_i + (1/N) sum_k theta_k x_{k,i,t}
  3. portfolio return r_p = r_m + theta' f  with  f = (1/N) sum_i x_i r_{i,t+1}
  4. Bayesian policy (policy-risk internalization + quadratic variance regularization)

         theta* = ( Sigma_f + (1/gamma) Omega_0^-1 + Var(theta|D_t) )^-1
                  * ( (1/gamma) mu_f - sigma_mf )

     Omega_0 = sigma_0^2 I is the registered Gaussian prior covariance and
     Var(theta|D_t) is the causal estimating-equation (posterior) covariance of theta.

The plug-in PPP comparator used by the record's falsification battery drops BOTH
(1/gamma) Omega_0^-1 and Var(theta|D_t), leaving theta = Sigma_f^-1 b.

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

FAMILY_ID = "portfolio-bayesian-parametric-policies-policy-risk-regularization-2026-09-02"
ENGINE_VERSION = "bayesian_ppp_qlib_v1"
RAW_USDM = "/data/raw/binance/usdm"
RAW_META = "/data/raw/_meta"
RESULTS_ROOT = "/results"  # the only tree run() may ever open
WORK_ROOT = "/qlib/work/bayesian-ppp-v1"
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

# --- research-defined strategy axes (the record fixes neither a numeric gamma nor the
# estimation length): gamma is the CRRA risk aversion of the registered Bayesian objective
# and est_window is the number of trailing REBALANCE observations used to estimate mu_f,
# Sigma_f, sigma_mf and Var(theta|D_t).  2 x 2 = 4 registered cases per cohort.
GAMMA_AXIS = (0.5, 1.0)
EST_WINDOW_AXIS = (30, 90)
STRATEGY_AXES = {"gamma": GAMMA_AXIS, "est_window": EST_WINDOW_AXIS}
STRATEGIES = tuple(dict(zip(STRATEGY_AXES, values)) for values in
                   itertools.product(*STRATEGY_AXES.values()))

# research-defined rebalance clock: the record's own crypto portability section moves the
# cadence from monthly to daily / 4-hourly, so every cohort rebalances every 4 hours of its
# OWN clock (max(1, ...) keeps the 4h / 1d / 1w cohorts at one bar).
CADENCE_MS = 4 * 3_600_000
REBALANCE_BARS = {tf["raw_interval"]: max(1, int(round(CADENCE_MS / tf["bar_ms"])))
                  for tf in TIMEFRAMES}

# research-defined characteristic set.  K is capped at N = 4 because the linear policy is
# identified only while the K characteristic vectors stay inside R^N (theta enters the
# weights through theta' X, so K > N leaves a null direction).  Horizons follow the record
# where it states one (12-1m momentum, 1-month reversal).  The value/quality trio and the
# bid-ask spread are absent from the canonical raw and are registered as research
# exclusions in the frozen round-spec; the 6-1m momentum and idiosyncratic skewness would
# push K past N = 4 on this local cross-section.
CHAR_SPECS = (
    {"name": "momentum_12_1m", "days": 335, "kind": "log_return"},
    {"name": "short_term_reversal_1m", "days": 30, "kind": "log_return"},
    {"name": "realized_volatility", "days": 30, "kind": "std"},
    {"name": "amihud_illiquidity", "days": 30, "kind": "amihud"},
)
N_CHAR = len(CHAR_SPECS)
# sigma_0^2: registered constant inside the record's own perturbation range [0.01, 10.0].
PRIOR_SCALE = 1.0
MIN_WEIGHT = 0.002      # research-defined minimum |active tilt| for a qualified signal
# Registered plug-in singularity gate: a Sigma_f this ill-conditioned has no finite
# plug-in policy, which is the record's own error-maximization premise.
SIGMA_CONDITION_LIMIT = 1.0e10

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
_FIXED_DCA_CELL = {"spacing_pct": 0.01, "size_multiplier": 1.0,
                   "breakeven_tp_pct": 0.01, "invalidation_pct": 0.05}
FALSIFICATION = {
    "record_tests": {
        "oos_sharpe_and_turnover": {
            "failure_rule": "Out-of-Sample Sharpe & Turnover Test: Run walk-forward "
                            "backtest (expanding 10-year training window) on a 50-signal "
                            "cross section. Falsification threshold: If BPPP does not "
                            "achieve at least 15% higher net Sharpe ratio and at least 25% "
                            "lower turnover than standard plug-in PPP after accounting for "
                            "10 bps round-trip costs, reject the policy risk internalization "
                            "thesis.",
            "thresholds": {"min_sharpe_improvement": 0.15, "max_turnover_ratio": 0.75},
            "window": "OOS 2025-10-01..2026-09-11",
            "cost": "10 bps round trip = 0.0005 taker per fill, official funding not "
                    "charged, baseline 1 tick adverse slippage",
            "walk_forward": "causal trailing estimation windows ending before each decision "
                            "bar; the local canonical raw spans 2022-01-01..2026-09-11 so "
                            "the record's 10-year expanding window is unreachable and is "
                            "disclosed as research-defined",
            "cross_section": "the complete local eligible universe of 4 USD-M perpetuals; "
                             "the record's 50-signal equity cross section is provenance "
                             "and external-validity reference only (system lifecycle rule)",
            "gamma": 1.0, "est_window": 90, "prior_scale": PRIOR_SCALE,
            "fee_per_side": 0.0005, "fixed_dca_cell": dict(_FIXED_DCA_CELL),
            "aggregation": "unweighted mean over every local timeframe where BOTH policies "
                           "produced at least one OOS episode; fewer than one evaluable "
                           "timeframe -> INDETERMINATE",
            "falsified_when": "mean BPPP net Sharpe < 1.15 x mean plug-in net Sharpe OR "
                              "mean BPPP turnover > 0.75 x mean plug-in turnover"},
        "crisis_tail_risk_compression": {
            "failure_rule": "Crisis Tail-Risk Compression Test: Evaluate portfolio "
                            "drawdowns specifically during market stress quarters (e.g., "
                            "2008 Q4, 2020 Q1, 2022 Q2). Falsification threshold: If BPPP "
                            "maximum drawdown is not at least 20% smaller than plug-in PPP "
                            "drawdown, reject the crisis-robustness claim.",
            "thresholds": {"max_drawdown_ratio": 0.80},
            "stress_period_definition": "the worst calendar quarters by equal-weight "
                                        "cross-sectional market return of the local 1d grid "
                                        "over the registered full window (research-defined "
                                        "local analogue of the record's example quarters)",
            "stress_quarter_count": 4,
            "window": "full 2022-01-01..2026-09-11 at the registered cost model",
            "gamma": 1.0, "est_window": 90, "prior_scale": PRIOR_SCALE,
            "fixed_dca_cell": dict(_FIXED_DCA_CELL),
            "aggregation": "sum of per-timeframe stress maximum drawdown in USDT over every "
                           "timeframe where BOTH policies produced at least one full-window "
                           "episode; fewer than one evaluable timeframe -> INDETERMINATE",
            "falsified_when": "aggregate BPPP stress max drawdown > 0.80 x aggregate "
                              "plug-in stress max drawdown"},
        "prior_sensitivity": {
            "failure_rule": "Prior Sensitivity Perturbation: Perturb prior scale "
                            "hyperparameter sigma_0^2 in [0.01, 10.0]. Falsification "
                            "threshold: If realized return varies by more than 40% across "
                            "plausible non-informative priors, reject model parameter "
                            "stability.",
            "prior_grid": [0.01, 0.1, 1.0, 10.0],
            "variation_threshold": 0.40,
            "variation_definition": "(max - min) / max(|max|, |min|) over the four realised "
                                    "returns",
            "realized_return_definition": "sum over the 7 local timeframes of the "
                                          "full-window four-leg sleeve net_pnl divided by "
                                          "starting_equity 30000 USDT, registered cost model",
            "gamma": 1.0, "est_window": 90, "fixed_dca_cell": dict(_FIXED_DCA_CELL),
            "falsified_when": "variation > 0.40 (all four realised returns numerically zero "
                              "-> INDETERMINATE)"},
    },
    "required_stress_grids": list(REQUIRED_STRESS),
    "failure_policy": "any FAIL or INDETERMINATE test culls every cohort as "
                      "falsification:<test>:<status>; never reworded into PASS",
}

ARTIFACTS = ("state.json", "result.json", "logs/run.log", "artifacts/progress.json",
             "artifacts/bins_build.json", "artifacts/assertions.json",
             "artifacts/stress_effects.json", "artifacts/cohort_results.json",
             "artifacts/cohort_survivors.json", "artifacts/policy_build.json",
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
    return {"characteristics": [s["name"] for s in CHAR_SPECS],
            "characteristic_days": [s["days"] for s in CHAR_SPECS],
            "cross_section_size": len(SYMBOLS), "characteristic_dim": N_CHAR,
            "identification_rule": "K <= N: the registered K=4 characteristics are exactly "
                                   "the local cross-section dimension, so theta has no null direction",
            "benchmark_weight": "equal weight 1/N (the record's value-weighted market share "
                                "is unavailable: no market-cap field in the canonical raw) -> research-defined",
            "rebalance_bars": dict(REBALANCE_BARS),
            "cadence_rule": "record crypto portability: daily / 4-hourly; implemented as 4 "
                            "hours of each cohort's own bar grid, max(1 bar)",
            "prior_scale": PRIOR_SCALE, "min_weight": MIN_WEIGHT,
            "sigma_condition_limit": SIGMA_CONDITION_LIMIT,
            "normalization": "mu_f, Sigma_f, sigma_mf and Var(theta|D_t) are estimated on "
                             "returns scaled by the causal trailing standard deviation of the "
                             "market return over the same estimation window -> research-defined",
            "entry_rule": "active tilt delta_i = (1/N) theta' x_i formed at the decision "
                          "bar close, executed at the next bar open",
            "exit_rule": "breakeven-anchored TP / resting invalidation / margin, plus a "
                         "reduce-only flatten at the open of the bar after the next decision",
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
        "script": {"path": "/scripts/250_bayesian_ppp_run.py", "sha256": sha256_file(here),
                   "deployed_host_path":
                       "/Users/hong/workspace/qlib-apple-container/scripts/250_bayesian_ppp_run.py"},
        "engine": {"name": ENGINE_VERSION, "script": "container/scripts/250_bayesian_ppp_run.py",
                   "container": "qlib-run", "qlib_version": "0.9.7", "seed": SEED,
                   "self_check": "container/scripts/tests/test_bayesian_ppp_engine.py",
                   "self_check_sha256": sha256_file(here.with_name("tests") / "test_bayesian_ppp_engine.py")},
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
                  "rule": "chronological; every characteristic, estimation window and funding "
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
    if (script.get("path") != "/scripts/250_bayesian_ppp_run.py"
            or script.get("sha256") != sha256_file(script_path or __file__)):
        raise ValueError("script bytes do not match pinned script identity")
    engine = spec.get("engine", {})
    if engine.get("name") != ENGINE_VERSION or engine.get("qlib_version") != "0.9.7" or engine.get("seed") != SEED:
        raise ValueError("engine identity mismatch")
    check_path = Path(test_path) if test_path else (
        Path(__file__).resolve().with_name("tests") / "test_bayesian_ppp_engine.py")
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
# registered mechanism: standardized cross-sectional characteristics -> PPP -> Bayesian policy
# ---------------------------------------------------------------------------------------------

def bar_lookbacks(bar_ms):
    """Trailing length, in this cohort's own bars, of every registered characteristic."""
    return [max(2, int(round(s["days"] * MS_DAY / float(bar_ms)))) for s in CHAR_SPECS]


def warmup_bars(bar_ms, est_window):
    """First bar at which a decision is causally possible for this (clock, window) pair."""
    return max(bar_lookbacks(bar_ms)) + int(est_window) + 2


def decision_bars(n, rebalance_bars, first):
    """Rebalance instants: multiples of the cohort's own 4-hourly clock, after warm-up."""
    if first >= n:
        return np.zeros(0, dtype=np.int64)
    return np.arange(int(first), int(n), int(rebalance_bars), dtype=np.int64)


def _trailing_mean(x, w):
    """Causal trailing mean of the last w observations ending at each index, O(n)."""
    c = np.concatenate([[0.0], np.cumsum(x)])
    out = np.full(len(x), np.nan)
    if len(x) >= w:
        out[w - 1:] = (c[w:] - c[:-w]) / float(w)
    return out


def _trailing_std(x, w):
    """Causal trailing population standard deviation of the last w observations, O(n)."""
    m = _trailing_mean(x, w)
    c = np.concatenate([[0.0], np.cumsum(x * x)])
    out = np.full(len(x), np.nan)
    if len(x) >= w:
        second = (c[w:] - c[:-w]) / float(w)
        out[w - 1:] = np.sqrt(np.maximum(second - m[w - 1:] ** 2, 0.0))
    return out


def raw_characteristics(close, volume, bar_ms):
    """(n, K) raw registered characteristics for one asset; NaN until each lookback is met."""
    close = np.asarray(close, dtype=np.float64)
    volume = np.asarray(volume, dtype=np.float64)
    logc = np.log(close)
    n = len(close)
    out = np.full((n, N_CHAR), np.nan)
    ret = np.zeros(n)
    ret[1:] = np.diff(logc)
    for k, spec in enumerate(CHAR_SPECS):
        w = max(2, int(round(spec["days"] * MS_DAY / float(bar_ms))))
        if spec["kind"] == "log_return":
            if n > w:
                out[w:, k] = logc[w:] - logc[:-w]
        elif spec["kind"] == "std":
            out[:, k] = _trailing_std(ret, w)
        elif spec["kind"] == "amihud":
            dollar = np.maximum(close * volume, 1e-12)
            out[:, k] = _trailing_mean(np.abs(ret) / dollar, w)
        else:
            raise ValueError("unknown characteristic kind " + str(spec["kind"]))
    seen = out[np.isfinite(out)]
    if len(seen) and not np.isfinite(seen).all():
        raise RuntimeError("nonfinite characteristic value")
    return out


def standardized_characteristics(panels_tf, bar_ms):
    """(n, N, K) cross-sectionally standardized characteristics: mean 0, unit variance.

    The record fixes sum_i x_{k,i,t} = 0 and (1/N) sum_i x_{k,i,t}^2 = 1 at every t, so the
    standardization runs over the N assets of the registered local cross-section (population
    standard deviation, which is exactly the record's unit-variance constraint).  Bars before
    the longest lookback is met stay at 0 and can never become decision bars.
    """
    raw = np.stack([raw_characteristics(panels_tf[s]["close"], panels_tf[s]["volume"], bar_ms)
                    for s in SYMBOLS], axis=1)             # (n, N, K)
    good = np.isfinite(raw).all(axis=2).all(axis=1)        # every asset, every characteristic
    z = np.zeros_like(raw)
    if good.any():
        sub = raw[good]
        mu = sub.mean(axis=1, keepdims=True)
        sd = sub.std(axis=1, keepdims=True)                # ddof=0 -> unit cross-sectional var
        z[good] = (sub - mu) / np.where(sd > 1e-300, sd, 1.0)
    if not np.isfinite(z).all():
        raise RuntimeError("nonfinite standardized characteristics")
    return z, good


def characteristic_payoffs(z, panels_tf):
    """f_t = (1/N) sum_i x_{i,t} r_{i,t+1} plus the equal-weight benchmark return r_m,t+1.

    f[t] pairs bar t's characteristics with the return realised at bar t+1, so f[t] only
    exists once bar t+1 does; the estimator therefore never reads a payoff at or after the
    decision bar.  The record's value-weighted benchmark is unavailable (no market-cap field
    in the canonical raw), so the benchmark is the equal weight 1/N (research-defined).
    """
    close = np.column_stack([panels_tf[s]["close"] for s in SYMBOLS]).astype(np.float64)
    n = len(close)
    r = close[1:] / close[:-1] - 1.0                       # (n-1, N), realised at bar t+1
    r_m = r.mean(axis=1)
    f = (z[:n - 1] * r[:, :, None]).mean(axis=1)           # (n-1, K)
    if not (np.isfinite(r).all() and np.isfinite(r_m).all() and np.isfinite(f).all()):
        raise RuntimeError("nonfinite cross-sectional payoff layer")
    return f, r_m


def estimate(f_block, m_block, gamma):
    """Causal estimation sample -> (Sigma_f, mu_f, sigma_mf, b, Var(theta|D_t), plug-in theta).

    Research-defined normalisation: every return entering the estimator is divided by the
    causal trailing standard deviation of the market return over the SAME estimation window,
    so the record's prior range sigma_0^2 in [0.01, 10.0] means the same thing on all seven
    local clocks.  Var(theta|D_t) is the sandwich / asymptotic covariance of the PPP
    estimating equation (the large-sample posterior under a flat prior); the proper prior
    enters the registered objective through its own (1/gamma) Omega_0^-1 term.
    """
    T = len(f_block)
    if T < 3:
        return None
    scale = float(np.std(m_block, ddof=1))
    if not (math.isfinite(scale) and scale > 0.0):
        return None
    try:
        F = f_block / scale
        m = m_block / scale
        fbar = F.mean(axis=0)
        Sf = (F - fbar).T @ (F - fbar) / float(T)
        mbar = float(m.mean())
        smf = (F - fbar).T @ (m - mbar) / float(T)         # cov(r_m, f), K-vector
        b = fbar / float(gamma) - smf
        cond = float(np.linalg.cond(Sf))
        theta_plugin = np.linalg.pinv(Sf) @ b
        p = m + F @ theta_plugin
        moment = F * (1.0 - float(gamma) * (p - float(p.mean())))[:, None]
        gbar = moment.mean(axis=0)
        B = (moment - gbar).T @ (moment - gbar) / float(T)
        A = float(gamma) * Sf
        Ainv = np.linalg.pinv(A)
        var = (Ainv @ B @ Ainv) / float(T)
    except (np.linalg.LinAlgError, ValueError):
        return None
    if not (np.isfinite(Sf).all() and np.isfinite(b).all() and np.isfinite(theta_plugin).all()):
        return None
    var = 0.5 * (var + var.T)
    if not np.isfinite(var).all():
        return None
    eigval, eigvec = np.linalg.eigh(var)
    var = (eigvec * np.maximum(eigval, 0.0)) @ eigvec.T    # numerical PSD projection
    return {"sigma_f": Sf, "mu_f": fbar, "sigma_mf": smf, "b": b, "var_theta": var,
            "theta_plugin": theta_plugin, "sigma_condition": cond,
            "return_scale": scale, "observations": int(T)}


def solve_policy(info, gamma, prior_scale, allocator):
    """theta_bppp (registered) or theta_plugin (falsification comparator); None if singular."""
    if allocator == "plugin":
        if not (math.isfinite(info["sigma_condition"])
                and info["sigma_condition"] <= SIGMA_CONDITION_LIMIT):
            return None
        theta = info["theta_plugin"]
    elif allocator == "bayes":
        system = (info["sigma_f"]
                  + np.eye(info["sigma_f"].shape[0]) / (float(gamma) * float(prior_scale))
                  + info["var_theta"])
        try:
            theta = np.linalg.solve(system, info["b"])
        except np.linalg.LinAlgError:
            return None
    else:
        raise ValueError("unknown allocator " + str(allocator))
    if not np.isfinite(theta).all():
        return None
    return theta


def policy_layer(z, f, r_m, rebalance_bars, est_window, gamma, first,
                 prior_scale=PRIOR_SCALE, allocator="bayes"):
    """Causal PPP / BPPP layer for one (clock, est_window, gamma, allocator) triple.

    At decision bar d the estimation sample is the est_window observations f[d-T] .. f[d-1];
    f[d-1] pairs bar d-1's characteristics with the return realised AT bar d, so nothing at
    or after bar d is ever read.  The active tilt of asset i is delta_i = (1/N) theta' x_{i,d};
    a decision bar whose |delta_i| < MIN_WEIGHT emits no signal for that asset (traced, not
    silent).  weights[] is written only on decision bars and decisions[] is the full
    rebalance grid, so the DCA rail flattens at the open of the bar after the next decision
    whether or not that asset is still in the policy.
    """
    n = len(r_m) + 1
    T = int(est_window)
    weights = np.zeros((n, len(SYMBOLS)), dtype=np.float64)
    bars = decision_bars(n, rebalance_bars, first)
    samples, solve_fails, zero_signal, singular = [], 0, 0, 0
    for raw_d in bars:
        d = int(raw_d)
        if d < T:
            continue
        info = estimate(f[d - T:d], r_m[d - T:d], gamma)
        if info is None:
            solve_fails += 1
            continue
        theta = solve_policy(info, gamma, prior_scale, allocator)
        if theta is None:
            solve_fails += 1
            singular += 1
            continue
        samples.append(info["observations"])
        tilt = (z[d] @ theta) / float(len(SYMBOLS))
        qualified = np.abs(tilt) >= MIN_WEIGHT
        if not bool(qualified.any()):
            zero_signal += 1
            continue
        weights[d] = np.where(qualified, tilt, 0.0)
    diag = {"decisions": int(len(bars)), "estimations": len(samples),
            "solve_failures": solve_fails, "singular_policies": singular,
            "below_min_weight": zero_signal, "allocator": allocator,
            "prior_scale": float(prior_scale), "gamma": float(gamma),
            "est_window": T, "rebalance_bars": int(rebalance_bars),
            "mean_estimation_observations": float(np.mean(samples)) if samples else 0.0,
            "min_estimation_observations": int(min(samples)) if samples else 0,
            "max_estimation_observations": int(max(samples)) if samples else 0,
            "causal": True,
            "causality_rule": "every estimation payoff f[d-k] was realised at or before "
                              "decision bar d and every characteristic reads only bars <= d",
            "decisions_with_signal": int(np.count_nonzero(np.any(weights, axis=1))),
            "weight_abs_mean": float(np.abs(weights).mean()),
            "active_tilt_abs_mean": float(np.abs(weights).mean())}
    return {"weights": weights, "decisions": bars, "diag": diag}



# ---------------------------------------------------------------------------------------------
# registered DCA execution rail (per-fill fee/funding accounting, independent gross ledger)
# ---------------------------------------------------------------------------------------------

def empty_metric(i0, i1):
    return {"gross_pnl": 0.0, "fees": 0.0, "funding": 0.0, "net_pnl": 0.0,
            "ending_equity": START_EQUITY, "episodes": 0, "fills": 0, "adds": 0,
            "turnover_usdt": 0.0, "sharpe": 0.0, "max_dd_pct": 0.0,
            "max_dd_usdt": 0.0, "annualized_return": 0.0, "max_effective_leverage": 0.0,
            "capital_utilization": 0.0, "tp_hits": 0, "stop_hits": 0,
            "margin_calls": 0, "rebalance_exits": 0,
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

    ``layer["weights"]`` carries the registered active tilt
    delta_i = (1/N) theta' x_i at each decision bar, and ``layer["decisions"]`` is the
    panel's full rebalance grid, so a symbol whose tilt has dropped out of the policy still
    flattens reduce-only on the next scheduled decision.

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
    margin_calls = rebalance_exits = 0
    max_lev = max_util = 0.0
    layer_hist = [0] * LADDER_LEVELS
    is_dict = isinstance(layer, dict)
    weights = layer["weights"] if is_dict else layer
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
            "rebalance_exits": rebalance_exits,
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
                                    "margin_calls", "rebalance_exits",
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


def sleeve_metrics(panel_tf, funding_tf, layer, dca, i0, i1, cost, instruments):
    """Four-leg projected sleeve summary + equity curve; the same engine as the grid."""
    legs = [simulate(panel_tf[s], funding_tf[s][0], layer, s, dca, i0, i1, cost, instruments[s])
            for s in SYMBOLS]
    equity = START_EQUITY + sum((np.asarray(m["daily_equity"]) - START_EQUITY for m in legs),
                                np.zeros(max(0, i1 - i0)))
    returns = np.diff(np.r_[START_EQUITY, equity]) / np.maximum(
        np.r_[START_EQUITY, equity[:-1]], 1e-9)
    sd = float(np.std(returns, ddof=1)) if len(returns) > 1 else 0.0
    return {"sharpe": float(np.mean(returns) / sd * math.sqrt(365)) if sd > 1e-12 else 0.0,
            "net_pnl": float(equity[-1] - START_EQUITY) if len(equity) else 0.0,
            "turnover_usdt": float(sum(m["turnover_usdt"] for m in legs)),
            "episodes": int(sum(m["episodes"] for m in legs)),
            "equity": equity,
            "decomposition_ok": all(m["decomposition_ok"] for m in legs)}


def stress_quarters(panels, count):
    """Research-defined local stress quarters: the worst calendar quarters by market return.

    The record names equity crisis quarters (2008 Q4, 2020 Q1, 2022 Q2) as examples; the
    local canonical raw carries no equity history, so the local analogue is the worst
    calendar quarters of the equal-weight cross-sectional market return on the 1d grid.
    """
    close = np.column_stack([panels["1d"][s]["close"] for s in SYMBOLS]).astype(np.float64)
    open_ms = panels["1d"][SYMBOLS[0]]["open_ms"]
    r = close[1:] / close[:-1] - 1.0
    market = r.mean(axis=1)
    totals = {}
    for i, stamp_ms in enumerate(open_ms[1:]):
        when = dt.datetime.fromtimestamp(int(stamp_ms) / 1000.0, dt.timezone.utc)
        key = "%04dQ%d" % (when.year, (when.month - 1) // 3 + 1)
        totals[key] = totals.get(key, 0.0) + float(market[i])
    ranked = sorted(totals.items(), key=lambda kv: kv[1])[:int(count)]
    return [{"quarter": k, "market_return": v} for k, v in ranked]


def quarter_bar_range(open_ms, quarter):
    """[lo, hi) bar indices of one calendar quarter on this cohort's own clock."""
    year, q = int(quarter[:4]), int(quarter[-1])
    first_month = (q - 1) * 3 + 1
    lo_ms = utc_ms("%04d-%02d-01" % (year, first_month))
    last_month = first_month + 2
    hi_year, hi_month = (year + 1, 1) if last_month > 12 else (year, last_month + 1)
    hi_ms = utc_ms("%04d-%02d-01" % (hi_year, hi_month))
    lo = int(np.searchsorted(open_ms, lo_ms, side="left"))
    hi = int(np.searchsorted(open_ms, hi_ms, side="left"))
    return lo, hi


def stress_drawdown(equity, ranges):
    """Max peak-to-trough drawdown of ``equity`` over the given (lo, hi) bar slices."""
    best = 0.0
    for lo, hi in ranges:
        seg = np.asarray(equity[lo:hi], dtype=np.float64)
        if len(seg) == 0:
            continue
        high = np.maximum.accumulate(np.r_[START_EQUITY, seg])
        dd = high[1:] - seg
        if len(dd):
            best = max(best, float(np.max(dd)))
    return best



# ---------------------------------------------------------------------------------------------
# record falsification battery (three tests, thresholds verbatim from the record)
# ---------------------------------------------------------------------------------------------

def _battery_layers(panel_tf, bar_ms, cfg, reb):
    """Causal BPPP and plug-in PPP layers at the battery's fixed (gamma, est_window)."""
    z, _ = standardized_characteristics(panel_tf, bar_ms)
    f, r_m = characteristic_payoffs(z, panel_tf)
    first = int(math.ceil(warmup_bars(bar_ms, int(cfg["est_window"])) / float(reb))) * reb
    bayes = policy_layer(z, f, r_m, reb, int(cfg["est_window"]), float(cfg["gamma"]), first,
                         prior_scale=float(cfg["prior_scale"]), allocator="bayes")
    plugin = policy_layer(z, f, r_m, reb, int(cfg["est_window"]), float(cfg["gamma"]), first,
                          allocator="plugin")
    return bayes, plugin


def _battery_cost(cfg):
    """Test 1 uses the record's 10 bps round trip; tests 2 and 3 use the registered model."""
    return {}


def oos_sharpe_turnover_test(panels, funding, instruments, windows, cfg, log):
    """Record test 1: BPPP must beat plug-in PPP on net Sharpe AND on turnover."""
    fixed = dict(cfg["fixed_dca_cell"])
    cost = {"fee_override": float(cfg["fee_per_side"]), "no_funding": True, "slip_ticks": 1}
    per_tf, evaluable = [], 0
    rows = []
    for tf in TIMEFRAMES:
        tid = tf["raw_interval"]
        panel_tf = {s: panels[tid][s] for s in SYMBOLS}
        funding_tf = {s: funding[(s, tid)] for s in SYMBOLS}
        reb = REBALANCE_BARS[tid]
        bayes, plugin = _battery_layers(panel_tf, tf["bar_ms"], cfg, reb)
        i0, i1 = windows[tid]["oos"]
        a = sleeve_metrics(panel_tf, funding_tf, bayes, fixed, i0, i1, cost, instruments)
        p = sleeve_metrics(panel_tf, funding_tf, plugin, fixed, i0, i1, cost, instruments)
        ok = a["episodes"] >= 1 and p["episodes"] >= 1
        evaluable += int(ok)
        entry = {"timeframe": tid, "evaluable": ok,
                 "bayes_sharpe": a["sharpe"], "plugin_sharpe": p["sharpe"],
                 "bayes_turnover_usdt": a["turnover_usdt"],
                 "plugin_turnover_usdt": p["turnover_usdt"],
                 "bayes_net_pnl": a["net_pnl"], "plugin_net_pnl": p["net_pnl"],
                 "bayes_episodes": a["episodes"], "plugin_episodes": p["episodes"],
                 "bayes_decomposition_ok": a["decomposition_ok"],
                 "plugin_decomposition_ok": p["decomposition_ok"]}
        per_tf.append(entry)
        if ok:
            rows.append(entry)
        log("falsification oos %s bayes_sharpe=%.4f plugin_sharpe=%.4f evaluable=%s"
            % (tid, a["sharpe"], p["sharpe"], ok))
    if evaluable == 0:
        status = "INDETERMINATE"
        detail = {"reason": cfg["aggregation"]}
        means = None
    else:
        bs = float(np.mean([r["bayes_sharpe"] for r in rows]))
        ps = float(np.mean([r["plugin_sharpe"] for r in rows]))
        bt = float(np.mean([r["bayes_turnover_usdt"] for r in rows]))
        pt = float(np.mean([r["plugin_turnover_usdt"] for r in rows]))
        sharpe_ok = bs >= (1.0 + float(cfg["thresholds"]["min_sharpe_improvement"])) * ps
        turnover_ok = bt <= float(cfg["thresholds"]["max_turnover_ratio"]) * pt
        means = {"mean_bayes_sharpe": bs, "mean_plugin_sharpe": ps,
                 "mean_bayes_turnover_usdt": bt, "mean_plugin_turnover_usdt": pt,
                 "sharpe_ratio_required": 1.0 + float(cfg["thresholds"]["min_sharpe_improvement"]),
                 "turnover_ratio_allowed": float(cfg["thresholds"]["max_turnover_ratio"]),
                 "sharpe_condition_met": bool(sharpe_ok),
                 "turnover_condition_met": bool(turnover_ok)}
        status = "PASS" if (sharpe_ok and turnover_ok) else "FAIL"
        detail = means
    return {"status": status, "failure_rule": cfg["failure_rule"],
            "falsified_when": cfg["falsified_when"], "window": cfg["window"],
            "cost": cfg["cost"], "cross_section": cfg["cross_section"],
            "walk_forward": cfg["walk_forward"], "aggregation": cfg["aggregation"],
            "thresholds": cfg["thresholds"], "evaluable_timeframes": evaluable,
            "means": detail, "per_timeframe": per_tf}


def crisis_tail_risk_test(panels, funding, instruments, windows, cfg, log):
    """Record test 2: BPPP stress-quarter max drawdown must be at least 20% smaller."""
    fixed = dict(cfg["fixed_dca_cell"])
    cost = _battery_cost(cfg)
    quarters = stress_quarters(panels, int(cfg["stress_quarter_count"]))
    per_tf, evaluable, dd_bayes, dd_plugin = [], 0, 0.0, 0.0
    for tf in TIMEFRAMES:
        tid = tf["raw_interval"]
        panel_tf = {s: panels[tid][s] for s in SYMBOLS}
        funding_tf = {s: funding[(s, tid)] for s in SYMBOLS}
        reb = REBALANCE_BARS[tid]
        bayes, plugin = _battery_layers(panel_tf, tf["bar_ms"], cfg, reb)
        i0, i1 = windows[tid]["full"]
        a = sleeve_metrics(panel_tf, funding_tf, bayes, fixed, i0, i1, cost, instruments)
        p = sleeve_metrics(panel_tf, funding_tf, plugin, fixed, i0, i1, cost, instruments)
        open_ms = panel_tf[SYMBOLS[0]]["open_ms"]
        ranges = []
        for q in quarters:
            lo, hi = quarter_bar_range(open_ms, q["quarter"])
            lo, hi = max(lo, i0), min(hi, i1)
            if hi > lo:
                ranges.append((lo - i0, hi - i0))
        ok = a["episodes"] >= 1 and p["episodes"] >= 1
        evaluable += int(ok)
        da = stress_drawdown(a["equity"], ranges)
        dp = stress_drawdown(p["equity"], ranges)
        if ok:
            dd_bayes += da
            dd_plugin += dp
        per_tf.append({"timeframe": tid, "evaluable": ok,
                       "bayes_stress_max_dd_usdt": da, "plugin_stress_max_dd_usdt": dp,
                       "stress_ranges": list(ranges), "stress_range_count": len(ranges),
                       "bayes_episodes": a["episodes"], "plugin_episodes": p["episodes"]})
        log("falsification stress %s bayes_dd=%.2f plugin_dd=%.2f evaluable=%s"
            % (tid, da, dp, ok))
    if evaluable == 0:
        status = "INDETERMINATE"
        detail = {"reason": cfg["aggregation"]}
    else:
        ok2 = dd_bayes <= float(cfg["thresholds"]["max_drawdown_ratio"]) * dd_plugin
        detail = {"aggregate_bayes_stress_max_dd_usdt": dd_bayes,
                  "aggregate_plugin_stress_max_dd_usdt": dd_plugin,
                  "ratio_allowed": float(cfg["thresholds"]["max_drawdown_ratio"]),
                  "observed_ratio": (dd_bayes / dd_plugin) if dd_plugin > 0 else None,
                  "condition_met": bool(ok2)}
        status = "PASS" if ok2 else "FAIL"
    return {"status": status, "failure_rule": cfg["failure_rule"],
            "falsified_when": cfg["falsified_when"], "window": cfg["window"],
            "thresholds": cfg["thresholds"],
            "stress_period_definition": cfg["stress_period_definition"],
            "aggregation": cfg["aggregation"], "stress_quarters": quarters,
            "evaluable_timeframes": evaluable, "detail": detail, "per_timeframe": per_tf}


def prior_sensitivity_test(panels, funding, instruments, windows, cfg, log):
    """Record test 3: realised return must not move more than 40% over the prior range."""
    fixed = dict(cfg["fixed_dca_cell"])
    cost = _battery_cost(cfg)
    per_tf = {str(p): [] for p in cfg["prior_grid"]}
    for tf in TIMEFRAMES:
        tid = tf["raw_interval"]
        panel_tf = {s: panels[tid][s] for s in SYMBOLS}
        funding_tf = {s: funding[(s, tid)] for s in SYMBOLS}
        bar_ms = tf["bar_ms"]
        reb = REBALANCE_BARS[tid]
        first = int(math.ceil(warmup_bars(bar_ms, int(cfg["est_window"])) / float(reb))) * reb
        z, _ = standardized_characteristics(panel_tf, bar_ms)
        f, r_m = characteristic_payoffs(z, panel_tf)
        i0, i1 = windows[tid]["full"]
        for prior in cfg["prior_grid"]:
            layer = policy_layer(z, f, r_m, reb, int(cfg["est_window"]), float(cfg["gamma"]),
                                 first, prior_scale=float(prior), allocator="bayes")
            m = sleeve_metrics(panel_tf, funding_tf, layer, fixed, i0, i1, cost, instruments)
            per_tf[str(prior)].append({"timeframe": tid, "net_pnl": m["net_pnl"],
                                       "episodes": m["episodes"],
                                       "net_return": m["net_pnl"] / START_EQUITY})
        log("falsification prior sweep %s done" % tid)
    realised = [sum(r["net_pnl"] for r in per_tf[str(p)]) / START_EQUITY
                for p in cfg["prior_grid"]]
    peak = max(abs(v) for v in realised)
    if peak < 1e-9:
        status = "INDETERMINATE"
        detail = {"reason": cfg["falsified_when"],
                  "realised_returns": realised}
    else:
        spread = (max(realised) - min(realised)) / peak
        status = "FAIL" if spread > float(cfg["variation_threshold"]) else "PASS"
        detail = {"realised_returns": realised, "variation": float(spread),
                  "variation_threshold": float(cfg["variation_threshold"]),
                  "condition_met": bool(status == "PASS")}
    return {"status": status, "failure_rule": cfg["failure_rule"],
            "falsified_when": cfg["falsified_when"], "prior_grid": list(cfg["prior_grid"]),
            "variation_definition": cfg["variation_definition"],
            "realized_return_definition": cfg["realized_return_definition"],
            "gamma": cfg["gamma"], "est_window": cfg["est_window"],
            "detail": detail, "per_timeframe": per_tf}


def falsification_battery(panels, funding, instruments, windows, log):
    cfg = FALSIFICATION["record_tests"]
    tests = {}
    tests["oos_sharpe_and_turnover"] = oos_sharpe_turnover_test(
        panels, funding, instruments, windows, cfg["oos_sharpe_and_turnover"], log)
    tests["crisis_tail_risk_compression"] = crisis_tail_risk_test(
        panels, funding, instruments, windows, cfg["crisis_tail_risk_compression"], log)
    tests["prior_sensitivity"] = prior_sensitivity_test(
        panels, funding, instruments, windows, cfg["prior_sensitivity"], log)
    for name, measured in tests.items():
        log("falsification battery %s=%s" % (name, measured["status"]))
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
        policy_build, layer_diags = [], []
        for tfid in TIMEFRAME_IDS:
            rows_by_symbol = {s: {g: [] for g in GRIDS} for s in SYMBOLS}
            bar_ms = next(tf["bar_ms"] for tf in TIMEFRAMES if tf["raw_interval"] == tfid)
            panel_tf = {s: panels[tfid][s] for s in SYMBOLS}
            z, characterized = standardized_characteristics(panel_tf, bar_ms)
            f, r_m = characteristic_payoffs(z, panel_tf)
            n_bars = len(r_m) + 1
            reb = REBALANCE_BARS[tfid]
            good_bars = np.flatnonzero(characterized)
            policy_build.append({"timeframe": tfid, "rebalance_bars": reb, "bars": n_bars,
                                 "characteristics": [s["name"] for s in CHAR_SPECS],
                                 "characteristic_days": [s["days"] for s in CHAR_SPECS],
                                 "lookback_bars": bar_lookbacks(bar_ms),
                                 "cross_section_size": len(SYMBOLS),
                                 "characteristic_dim": N_CHAR,
                                 "characterized_bars": int(len(good_bars)),
                                 "first_characterized_bar": int(good_bars[0])
                                 if len(good_bars) else None,
                                 "last_characterized_bar": int(good_bars[-1])
                                 if len(good_bars) else None})
            for gamma in GAMMA_AXIS:
                for est_window in EST_WINDOW_AXIS:
                    first = int(math.ceil(
                        warmup_bars(bar_ms, est_window) / float(reb))) * reb
                    layer = policy_layer(z, f, r_m, reb, est_window, gamma, first)
                    layer_diags.append({"timeframe": tfid, "gamma": gamma,
                                        "est_window": est_window, **layer["diag"]})
                    for symbol in SYMBOLS:
                        panel = panel_tf[symbol]
                        events = funding[(symbol, tfid)][0]
                        for dca in DCA_GRID:
                            for grid in GRIDS:
                                phase = GRID_PHASE.get(grid, "full")
                                cost = dict(GRID_COST.get(grid, {}))
                                metric = simulate(panel, events, layer, symbol, dca,
                                                  *windows[tfid][phase], cost,
                                                  instruments[symbol])
                                row = {"symbol": symbol, "timeframe": tfid,
                                       "gamma": gamma, "est_window": est_window,
                                       **dca, "grid": grid,
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
                log("policy grid %s gamma=%s complete (%d case evaluations)"
                    % (tfid, gamma, progress["case_evaluations"]))
            del z, f, r_m
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
            "policy_estimations_causal": all(d["causal"] for d in layer_diags),
            "bayes_policy_well_posed": all(d["solve_failures"] == 0 for d in layer_diags),
            "characteristics_finite": all(s["characterized_bars"] > 0 for s in policy_build),
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
        atomic_json(artifacts / "policy_build.json", policy_build)
        atomic_json(artifacts / "signal_layer.json", {
            "layers": layer_diags,
            "mechanism": "cross-sectionally standardized characteristics -> parametric "
                         "portfolio policy w_i = wbar_i + (1/N) theta' x_i -> Bayesian "
                         "objective with (1/gamma) Omega_0^-1 prior penalty and causal "
                         "Var(theta|D_t) -> signed active-tilt execution",
            "benchmark": signal_constants()["benchmark_weight"],
            "entry_rule": signal_constants()["entry_rule"],
            "exit_rule": signal_constants()["exit_rule"],
            "normalization": signal_constants()["normalization"],
            "identification_rule": signal_constants()["identification_rule"],
            "scope": "4 fixed local USD-M contracts x all 7 local kline intervals; "
                     "conclusions limited to this local universe",
            "research_defined": ["K = N = 4 identification limit on the local cross-section",
                                 "gamma endpoints 0.5/1.0", "estimation windows 30/90 rebalances",
                                 "prior scale sigma_0^2 = 1.0 (inside the record's [0.01, 10.0])",
                                 "4-hour rebalance clock on each cohort's own bar grid",
                                 "min |active tilt| 0.002", "equal-weight benchmark 1/N",
                                 "volatility-normalized estimation sample",
                                 "next-open entries and scheduled next-decision flatten",
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
                  "policy_estimations_total": sum(d["estimations"] for d in layer_diags),
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

