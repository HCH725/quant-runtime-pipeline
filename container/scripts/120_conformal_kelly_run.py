#!/usr/bin/env python3
"""Strategy L — Conformal Kelly: Conformal Prediction Intervals as the Robust Scale in
Fractional Kelly Position Sizing, executed on the registered LOCAL eligible universe
(BTCUSDT / ETHUSDT / BNBUSDT / SOLUSDT USD-M perpetuals, 1 d) on Qlib.

Entry point for ONE pre-registered attempt.  Reads the immutable run-spec from /results (the
only source of parameters), builds the Qlib .bin store from the READ-ONLY canonical raw store
into /qlib/work, then runs the pre-registered full-backtest contract
(QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md sections 7.2 / 7.3):

    4 cohorts (BTCUSDT / ETHUSDT / BNBUSDT / SOLUSDT, raw interval 1d)
    x STRATEGY domain (2 registered axes: the scale-estimator arm {conformal, mad, resid_std,
      frozen_train, realized_vol_20} x the registered sizing configuration {cap_only,
      drawdown_dial} = 10 cases)
    x DCA domain (4 axes = 48 configs)
    x 10 registered phase grids (historical / oos / full / fee_2x / funding_2x /
      entry_delay_1_bar / slippage_2ticks / no_funding / no_funding_full /
      cost_attrition_40bps)
    = 4 x 10 x 48 x 10 = 19,200 expected case evaluations.

SOURCE CLAIM UNDER TEST (the record's own): replacing the standard-deviation scale of the
fractional Kelly denominator with a slowly adapting, rank-based conformal half-width keeps the
cross-sectional risk allocation stable under fat tails (the map scale -> size is convex and the
Kelly objective integrates 1/sigma^2 over time, so it penalises estimator variance far more than
it rewards local sharpness).  The record's own portability paragraph names crypto perpetuals,
24/7 session timestamps, perpetual funding and extreme tail kurtosis as an `adapted` /
`unproven` extension; this round executes that extension on the four canonical local
instruments, with the source market (8 liquid US-listed ETFs, daily closes) recorded as
provenance and external-validity context only (system-owned lifecycle footer, contract 14.4).

THE REGISTERED SIGNAL (record-faithful; every value the record leaves open is a project
pre-registered constant frozen before the first run - see the module docstring of the round-spec
and `signal_semantics.registered_constants`):

  * forecaster (per asset, independent): expanding-window ridge regression, lambda = 10 with an
    unpenalised intercept, on four unstandardized features (21/63/252-bar momentum and an
    EWMA(20) volatility), refit every 21 bars, first prediction at bar 750;
  * target: the forward H-bar arithmetic return sum R^(H)_t = sum_{s=1..H} r_{t+s}, trained only
    on rows whose label has landed (u + H <= t; the record's `fit_end = t - H + 1` boundary);
  * five-horizon ensemble H in {12,16,21,27,34}: every component prediction is scaled to the
    common 21-bar reference by sqrt(21/H) and the components are weighted by w_h ~ 1/q_h^2,
    where q_h is the causal conformal half-width of that component's residual against the
    common 21-bar target (trailing window of landed component scores);
  * conformal scale estimator: the nonconformity score is |R^(21) - mu_hat|, the rolling
    quantile q_roll is the 0.75 empirical quantile over the trailing W = 500 landed scores, the
    anchor q_anchor is the 0.75 quantile of every landed score from inception, recomputed every
    21 bars and held stale in between, and q_eff = q_roll^(1-lambda) q_anchor^lambda with
    lambda = 0.3; the volatility proxy is sigma_hat = q_eff / 1.2816;
  * fractional Kelly sizing: f = 0.15 mu_hat / sigma_hat^2, winsorised to +-0.75 per asset and
    renormalised proportionally when the gross sum exceeds the 2.0 cap;
  * target state: LONG/SHORT asset i exactly while its FITTED allocation is at least an equal
    share of the capped gross book (|w_i| >= 2.0 / n); the pipeline's rail is event-driven and
    one instrument at a time, so the record's continuous allocation vector is read as the
    registered tilt rule and the rail owns the position size.  The alternative reading (any
    strictly non-zero weight -> invested) is disclosed and NOT evaluated.
  * drawdown dial (Config B): m_t = clip(1 - (d_t - alpha/2) / (alpha/2), 0.25, 1.0) from the
    trailing M = 21 downside-miscoverage rate d_t.  The rail owns size, so the record's
    continuous leverage multiplier is carried as the registered DIAL GATE (m_t < 0.5 -> no
    fresh entry, open exposure flattened at the next bar's open); the continuous multiplier and
    the portfolio-level (as opposed to per-instrument) dial are disclosed and NOT evaluated.

ENTRY / EXIT (the record's own position semantics wrapped in the card-mandated DCA rail):
an ENTRY event is a bar whose target state CHANGES to a non-zero value (a fresh allocation
event); the entry executes at the NEXT bar's open (the record's next-day execution), optionally
moved by the registered `entry_delay_1_bar` execution stress.  An episode of direction d ends
when (1) the rail take profit at running_average_cost x (1 +- breakeven_tp_pct) fills
reduce-only, (2) the resting invalidation at running_average_cost x (1 -+ invalidation_pct)
fills, (3) the record's own exit condition holds at a bar's close - the target state is no
longer d - and every layer is flattened at the NEXT bar's open, or (4) the slice ends
(reduce-only flatten of every layer).  tranche #1 is the initial entry; adverse-price scale-ins
follow the registered ladder (level_k price = initial_entry_price x (1 -+ spacing_pct x k),
k = 1..10, i.e. at most 11 routine active levels of the 12-tranche rail).

DCA is executed as real order/fill accounting (an episode state machine over the bars); nothing
is estimated after the fact.  Every legal (strategy params x DCA config) cell is evaluated on
every registered phase grid, and the family gate is the **cohort-level survivor** rule of
contract section 7.3: one deterministic historical-only winner per cohort, then OOS / full /
robustness / parameter-neighbourhood evidence for that same winner.  This family has four
cohorts, so the cohort survivor count is 0..4 and EVERY survivor advances (v1.4.0 band mapping).

The record's four-item falsification battery is registered as four family-level readers (the
coverage-calibration stability test and the conformal-vs-realized-volatility horserace, both
EXECUTED on the registered grid / signal diagnostics; the downside-miscoverage dial placebo
test and the leverage-cap ablation, both `not_executed` with their measured local analogues
disclosed).  A reader hit can only move a PASS to DEFERRED and is never PASS-bearing.

Writes only:
  * /qlib/work/**      (rebuildable derived/cache area, INV-5)
  * <attempt_dir>/**   (result.json, artifacts/, logs/, state.json)
Never writes /data/raw (read-only mount).

usage: 120_conformal_kelly_run.py <run-spec.json>
"""


from __future__ import annotations

import calendar
import csv
import gzip
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import time

import numpy as np

VENV_PYTHON = "/opt/venv/bin/python"
DUMP_BIN = "/opt/qlib-tools/dump_bin.py"
RAW_ROOT = "/data/raw/binance/usdm"
WORK_ROOT = "/qlib/work/strategyL-v1"
CSV_ROOT = os.path.join(WORK_ROOT, "csv")
QLIB_DIR = os.path.join(WORK_ROOT, "qlib-data")
MS_PER_DAY = 86400000
MS_PER_HOUR = 3600000
FIELDS = ["open", "high", "low", "close", "volume"]
START_EQUITY = 30000.0
MAX_ADD_LEVELS = 10
BAR_MS = {"1d": MS_PER_DAY, "1h": MS_PER_HOUR}

# ------------------------------------- registered signal contract (RESEARCH_DEFINED, frozen)
# The record fixes the MECHANISM and the mathematics; this block fixes every value the record
# leaves open.  It mirrors the pre-registered search axes of the round-spec; nothing here may
# change after the first run.
SCALE_ARMS = ("conformal", "mad", "resid_std", "frozen_train", "realized_vol_20")
ARM_LABELS = ("conf", "mad", "rstd", "frozen", "rvol20")
CONFIGS = ("cap_only", "drawdown_dial")
CFG_LABELS = ("A", "B")
CASE_FIELDS = ("arm_conf", "arm_mad", "arm_rstd", "arm_frozen", "arm_rvol20", "cfg_A", "cfg_B")
CASE_ORDER = tuple(
    tuple([1 if a == i else 0 for a in range(len(SCALE_ARMS))]
          + [1 if b == j else 0 for b in range(len(CONFIGS))])
    for j in range(len(CONFIGS)) for i in range(len(SCALE_ARMS)))
CASE_NAMES = tuple("%s__%s" % (ARM_LABELS[i], CFG_LABELS[j])
                   for j in range(len(CONFIGS)) for i in range(len(SCALE_ARMS)))
# ---- the record's own fixed constants (source-specified; reproduced verbatim) ----------------
RIDGE_LAMBDA = 10.0            # record: lambda_ridge = 10
REFIT_EVERY = 21               # record: refitted every 21 trading days
FIRST_PREDICTION_BAR = 750     # record: first prediction after 750 trading days
HORIZONS = (12, 16, 21, 27, 34)  # record: the five forecast horizons
TARGET_H = 21                  # record: the common 21-day reference target
MOM_WINDOWS = (21, 63, 252)    # record: the three momentum features
EWMA_SPAN = 20                 # record: EWMA(20) volatility feature
W_CONF = 500                   # record: trailing window W = 500 landed conformal scores
SHRINK_LAMBDA = 0.3            # record: geometric anchor shrinkage lambda = 0.3
Z_ALPHA = 1.2816               # record: z_{1-alpha/2} = 1.2816
NOMINAL_ALPHA = 0.25           # record: nominal coverage 1 - alpha = 0.75
KAPPA = 0.15                   # record: kappa = 0.15
WINSOR = 0.75                  # record: per-asset winsorisation +-0.75
GROSS_CAP = 2.0                # record: portfolio gross leverage cap 2.0
DD_WINDOW = 21                 # record: the dial's trailing window M = 21 days
DD_BETA = 1.0                  # record: beta = 1.0
DD_FLOOR = 0.25                # record: the dial's floor 0.25
# ---- project pre-registered constants (the record leaves these open) -------------------------
PROBE_CASE = CASE_ORDER[0]     # the case the causality probe re-derives (conformal / cap_only)
MATERIALITY_TILT = GROSS_CAP / 4.0   # registered entry state: |w_i| >= the equal share of the
                                     # capped gross book (the K-family symmetric analogue)
DIAL_GATE = 0.5                # registered exposure gate of the record's continuous dial
FROZEN_TRAIN_SCORES = 250      # the frozen_train arm's registered TRAIN leg: the first 250
                               # landed scores
QUANTILE_RULE = "linear interpolation (numpy default) of the 0.75 empirical quantile"
PANEL_SIZE = 4                 # the registered local eligible universe
PANEL_COHORTS = {}             # filled by main(): the four cohorts the panel is built from
# The registered signal floor: the record's first prediction after 750 bars PLUS the 21-bar
# landing delay of the conformal calibration scores.  A bar below it carries target state 0.
SIGNAL_WARMUP_BARS = FIRST_PREDICTION_BAR + TARGET_H
# Execution stress reruns (full-window slice), contract 7.2 robustness.
STRESS = [
    ("fee_2x", {"fee_mult": 2.0}),
    ("funding_2x", {"funding_mult": 2.0}),
    ("entry_delay_1_bar", {"entry_delay_1_bar": True}),
    ("slippage_2ticks", {"slip_ticks": 2}),
]
# Registered cost-attrition grid: 8 x the 5 bps taker fee = 40 bps per fill.
COST_ATTRITION_STRESS = {"fee_mult": 8.0}
# Registered phase grids: every one of them covers the FULL strategy-domain x DCA-domain
# product of every cohort (contract 7.2).
COHORT_GRID_KINDS = ("historical", "oos", "full", "fee_2x", "funding_2x",
                     "entry_delay_1_bar", "slippage_2ticks", "no_funding",
                     "no_funding_full", "cost_attrition_40bps")
# grids whose slice is the FULL registered window (used by the assertions)
FULL_WINDOW_GRID_KINDS = ("full", "fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks",
                          "no_funding_full", "cost_attrition_40bps")
# Joint parameter space axes, in the one registered order used for the deterministic
# lexical tie-break (contract 7.3).  Order is part of the gate.  `window_case` is the
# composite (scale-estimator arm, sizing configuration) axis; its registered order is CASE_ORDER.
AXES = ("window_case", "spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct")
DCA_AXES = ("spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct")
SELECTOR_VERSION = "cohort-selector-v1"
DISPOSITION_VERSION = "cohort-disposition-v1"
CONTRACT_SEMANTICS_VERSION = "v1.4.0"
ENGINE_VERSION = "l-v1-engine-1.0.0"
ENGINE_SEMANTICS = ("the record's conformal-Kelly signal chain (expanding-window ridge forecast "
                    "over 21/63/252-bar momentum and EWMA(20) volatility, the five-horizon "
                    "sqrt(21/H)-rescaled conformal inverse-variance ensemble, the rolling "
                    "conformal quantile with geometric shrinkage toward the expanding anchor, "
                    "the 1.2816 normalisation, winsorised fractional Kelly sizing and the 2.0 "
                    "gross book) turned into target-state change entry events at the next bar's "
                    "open, the record's target exit at the next bar's open, plus the registered "
                    "DCA rail (see module docstring)")
WINNER_METRIC_KEYS = ("net_pnl", "sharpe", "episodes", "ending_equity", "fees", "funding",
                      "max_dd_usdt", "max_dd_pct", "max_effective_leverage",
                      "capital_utilization", "annualized_return", "cagr")
# csv row field order (kept identical for every phase grid)
ROW_FIELDS = ("symbol", "timeframe", "window_kind", "window_case", "case_name",
              "arm_conf", "arm_mad", "arm_rstd", "arm_frozen", "arm_rvol20", "cfg_A", "cfg_B",
              "spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct",
              "net_pnl", "fees", "funding", "gross_pnl", "ending_equity",
              "windows_seen", "windows_entered", "episodes",
              "tp_hits", "stop_hits", "time_exits", "open_at_end", "margin_calls",
              "halted", "min_entry_equity", "sharpe", "max_dd_usdt", "max_dd_pct",
              "max_effective_leverage", "capital_utilization", "bars_in_market",
              "fills", "turnover_usdt", "days", "years", "cagr", "total_return_pct",
              "annualized_return")
LAYER_TOTALS = {}
# Structural counters, per phase grid (module level so `summarize` can assert them).
# The raw funding timestamps carry ms-level jitter (measured on all four symbols: 0 .. 28 ms
# late relative to the nominal 8h boundary, never early), so the funding exposure window is
# the CLOSED interval [entry - 1s, exit + 1s] and every clock comparison is exact on the
# registered bar grid.  A deviation beyond the tolerance is a real mapping defect.
MS_JITTER_TOLERANCE_MS = 1000
COUNTER_NAMES = ("entry_after_slice_end", "slice_end_flatten", "record_exit_taken",
                 "record_exit_deferred_to_slice_end", "entry_refused_exhausted",
                 "ladder_cap_reached", "stop_at_ladder_boundary",
                 "entry_bar_not_the_next_bar_after_the_signal", "reentry_skipped_in_position",
                 "case_not_registered", "bar_grid_not_contiguous", "funding_bar_out_of_hold",
                 "entry_before_a_defined_signal", "signal_undefined_on_event_bar",
                 "causality_probe_mismatch", "signal_before_the_registered_warmup",
                 "panel_grid_mismatch", "nonfinite_scale_on_a_defined_bar",
                 "nonfinite_prediction_on_a_defined_bar", "ridge_fit_failed",
                 "state_not_derived_from_the_registered_rule", "gross_cap_renormalised",
                 "coverage_undefined_on_a_defined_bar", "dial_undefined_on_a_defined_bar",
                 "materiality_threshold_violation")
COUNTERS = {}
# ---- registered family-level falsification reader constants (frozen before the first run) ----
COVERAGE_LOW = 0.70            # record: realized coverage < 0.70 falsifies the calibration
COVERAGE_HIGH = 0.80           # record: realized coverage > 0.80 falsifies the calibration
COVERAGE_ROLLING_BARS = 252    # record: "over a rolling 252-day window"
HORSERACE_MIN_SAMPLE_BARS = 500  # record: "a minimum 500-day evaluation sample"
DIAL_PLACEBO_DRAWS = 1000      # record: the registered placebo resample count (not executed)
UNCAPPED_DD_LIMIT = 0.60       # record: "unconstrained drawdowns exceeding 60%"
UNCAPPED_LEV_LIMIT = 5.0       # record: "leverage spikes > 5x"


def counter(kind, name, inc=1):
    slot = COUNTERS.setdefault(kind, {n: 0 for n in COUNTER_NAMES})
    slot[name] += inc
    return slot[name]


def counters_total(name):
    return sum(v.get(name, 0) for v in COUNTERS.values())


def counters_sum(name, kinds):
    """Structural counters of an explicit set of phase grids (never the diagnostic re-runs)."""
    return sum(COUNTERS.get(k, {}).get(name, 0) for k in kinds)


def counters_snapshot():
    return {k: dict(v) for k, v in COUNTERS.items()}


def counters_delta(before):
    """Per-name delta of the structural counters across one measurement (a single simulate)."""
    out = {}
    for kind, slot in COUNTERS.items():
        prev = before.get(kind, {})
        for name, val in slot.items():
            d = val - prev.get(name, 0)
            if d:
                out[name] = out.get(name, 0) + d
    return out


def log(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), msg)
    print(line, flush=True)
    return line


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def _sanitize(o):
    """Make a payload strictly JSON-safe: numpy scalars -> python, non-finite -> null."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, bool) or o is None or isinstance(o, (int, str)):
        return o
    if hasattr(o, "item"):
        try:
            return _sanitize(o.item())
        except Exception:  # noqa: BLE001
            return str(o)
    if isinstance(o, dict):
        return {str(k): _sanitize(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_sanitize(v) for v in o]
    return str(o)


def atomic_write_json(path, payload):
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(json.dumps(_sanitize(payload), indent=2, ensure_ascii=False,
                            allow_nan=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.rename(tmp, path)


def dir_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def utc_ms(date_str):
    # calendar.timegm treats the parsed struct as UTC; time.mktime would silently use the
    # process timezone and shift every window boundary by the host offset.
    return int(calendar.timegm(time.strptime(date_str + " 00:00:00", "%Y-%m-%d %H:%M:%S"))) * 1000


def iso(ms):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ms / 1000.0))


def qlib_window(start, end):
    """Day-granularity dates -> qlib timestamps covering the WHOLE inclusive final day."""
    return start + " 00:00:00", end + " 23:59:59"


def day_range(cohort, start_date, end_date):
    """Bar range [lo, hi) of the half-open day interval [start_date, end_date) on the bar grid."""
    lo = utc_ms(start_date)
    hi = utc_ms(end_date)
    return (int(np.searchsorted(cohort.open_time_ms, lo, side="left")),
            int(np.searchsorted(cohort.open_time_ms, hi, side="left")))


def case_tuple(p):
    return tuple(int(p[f]) for f in CASE_FIELDS)


def case_index(case):
    return CASE_ORDER.index(tuple(case))


def case_name(case):
    return CASE_NAMES[case_index(case)]

def case_arm(case):
    """Registered scale-estimator arm index of one case: 0..4 in SCALE_ARMS order."""
    return list(case).index(1, 0, len(SCALE_ARMS))


def case_cfg(case):
    """Registered sizing-configuration index of one case: 0 = cap_only, 1 = drawdown_dial."""
    return (list(case).index(1, len(SCALE_ARMS), len(SCALE_ARMS) + len(CONFIGS))
            - len(SCALE_ARMS))


# ---------------------------------------------------------------------------
# conformal-Kelly science  (record-faithful signal; research-defined operationalization)
# ---------------------------------------------------------------------------
# The record fixes the MECHANISM and the mathematics:
#   1. expanding-window ridge regression (lambda = 10) on four unstandardized features
#      (21/63/252-bar momentum and an EWMA(20) volatility), refit every 21 bars, first
#      prediction after 750 bars;
#   2. the forward H-bar arithmetic return sum R^(H)_t = sum_{s=1..H} r_{t+s} with the training
#      set truncated so that no unlanded label enters the fit;
#   3. the five-horizon ensemble H in {12,16,21,27,34}, every component rescaled to the common
#      21-bar reference by sqrt(21/H) and weighted by w_h ~ 1/q_h^2 with q_h the causal
#      conformal half-width of that component's residual against the common 21-bar target;
#   4. the conformal scale estimator: score |R^(21) - mu_hat|, the 0.75 empirical quantile over
#      a trailing window of W landed scores, geometrically shrunk toward an expanding anchor
#      (lambda = 0.3), then sigma_hat = q_eff / 1.2816;
#   5. fractional Kelly sizing f = 0.15 mu_hat / sigma_hat^2, winsorised to +-0.75 per asset,
#      proportionally renormalised above a gross sum of 2.0;
#   6. the downside-miscoverage drawdown dial m_t = clip(1 - (d_t - alpha/2)/(alpha/2), 0.25, 1).
# What the record does NOT fix - the quantile convention, the exact window boundaries, the
# warm-up accounting, the materiality reading of the target state and the dial's exposure
# semantics inside a one-instrument rail - is fixed as project pre-registered constants above.

def _ewma_vol(returns, span=EWMA_SPAN):
    """EWMA(20) volatility of daily returns (RiskMetrics recursion on squared returns).

    Causal: the value at bar t reads returns up to and including bar t only.
    """
    n = len(returns)
    out = np.full(n, np.nan)
    alpha = 2.0 / (span + 1.0)
    var = 0.0
    started = False
    for t in range(1, n):
        if not started:
            var = float(returns[t]) * float(returns[t])
            started = True
        else:
            var = alpha * float(returns[t]) * float(returns[t]) + (1.0 - alpha) * var
        out[t] = math.sqrt(var)
    return out


def _features_of(close):
    """The four registered unstandardized features (causal; NaN while unavailable)."""
    n = len(close)
    ret = np.zeros(n, dtype=np.float64)
    if n > 1:
        ret[1:] = close[1:] / close[:-1] - 1.0
    F = np.full((n, 4), np.nan)
    for j, w in enumerate(MOM_WINDOWS):
        if n > w:
            F[w:, j] = close[w:] / close[:n - w] - 1.0
    F[:, 3] = _ewma_vol(ret)
    return ret, F


def _forward_sums(ret, H):
    """R^(H)_t = sum_{s=1..H} r_{t+s}; NaN while the label has not landed."""
    n = len(ret)
    out = np.full(n, np.nan)
    cs = np.concatenate([[0.0], np.cumsum(ret)])
    idx = np.arange(n)
    hi = idx + H + 1
    ok = hi <= n
    out[ok] = cs[hi[ok]] - cs[idx[ok] + 1]
    return out


def _ridge_fit(Xa, y, lam=RIDGE_LAMBDA):
    """Ridge with an UNPENALISED intercept: [X'X + lam P] b = X'y with P = diag(0, 1..1)."""
    k = Xa.shape[1]
    G = Xa.T @ Xa
    P = np.eye(k)
    P[0, 0] = 0.0
    rhs = Xa.T @ y
    try:
        return np.linalg.solve(G + lam * P, rhs), True
    except np.linalg.LinAlgError:
        return np.linalg.lstsq(G + lam * P, rhs, rcond=None)[0], False


def _forecast_path(F, R, H):
    """The record's expanding-window ridge forecast path of one (asset, horizon).

    `yhat[t]` is the prediction formed at bar t (NaN before the first registered prediction
    bar).  The model is refit at bar t when no fit exists or t - last_fit >= REFIT_EVERY, and
    the fitted block is then reused on every intervening bar (the record's "refitted every 21
    trading days").  Only rows with a fully defined feature vector AND a landed label enter the
    fit (`u + H <= t`), which is the registered reading of `fit_end = t - H + 1`.
    """
    n = F.shape[0]
    yhat = np.full(n, np.nan)
    fits = 0
    beta = None
    last_fit = None
    for t in range(n):
        if t < FIRST_PREDICTION_BAR or not np.all(np.isfinite(F[t])):
            continue
        if last_fit is None or (t - last_fit) >= REFIT_EVERY:
            u_max = t - H
            rows = []
            if u_max >= 0:
                cand = np.arange(0, u_max + 1)
                good = (np.isfinite(R[cand]) & np.all(np.isfinite(F[cand]), axis=1))
                rows = cand[good]
            if rows.size >= 8:
                X = F[rows]
                y = R[rows]
                Xa = np.hstack([np.ones((X.shape[0], 1)), X])
                beta, ok = _ridge_fit(Xa, y)
                if not ok:
                    counter("signal", "ridge_fit_failed")
                last_fit = t
                fits += 1
            else:
                beta = None
        if beta is None:
            counter("signal", "nonfinite_prediction_on_a_defined_bar")
            continue
        yhat[t] = float(beta[0] + float(np.dot(beta[1:], F[t])))
    return {"yhat": yhat, "fits": fits}


def _rolling_std(x, w):
    """Rolling sample standard deviation (ddof = 1) of the trailing w observations."""
    n = len(x)
    out = np.full(n, np.nan)
    for t in range(n):
        if t + 1 >= w:
            out[t] = float(np.std(x[t - w + 1:t + 1], ddof=1))
    return out


def _asset_path(close, arm):
    """The registered signal path of ONE asset of ONE scale-estimator arm.

    The forecast chain (features -> ridge path -> five-horizon conformal ensemble) is identical
    for every arm - the record's ablation replaces the SCALE ESTIMATOR only - so only `sigma`
    and everything downstream of it depend on `arm`.
    """
    n = len(close)
    ret, F = _features_of(close)
    R21 = _forward_sums(ret, TARGET_H)
    paths = {H: _forecast_path(F, _forward_sums(ret, H), H) for H in HORIZONS}
    nH = len(HORIZONS)
    mu = np.full(n, np.nan)
    q_roll = np.full(n, np.nan)
    q_anchor = np.full(n, np.nan)
    q_eff = np.full(n, np.nan)
    sigma = np.full(n, np.nan)
    f_raw = np.full(n, np.nan)
    dial = np.full(n, np.nan)
    covered = np.zeros(n, dtype=bool)
    below = np.zeros(n, dtype=bool)
    comp_q = np.full((n, nH), np.nan)
    landed_comp = [[] for _ in range(nH)]
    landed_signed = []
    landed_abs = []
    landed_cov = []
    landed_below = []
    last_anchor_bar = None
    anchor_value = np.nan
    frozen_q = None
    rvol = _rolling_std(ret, 20)
    fit_total = int(sum(paths[H]["fits"] for H in HORIZONS))

    for t in range(n):
        tau = t - TARGET_H
        if tau >= 0:
            for j, H in enumerate(HORIZONS):
                yh = paths[H]["yhat"][tau]
                if np.isfinite(yh) and np.isfinite(R21[tau]):
                    landed_comp[j].append(abs(float(R21[tau])
                                              - math.sqrt(float(TARGET_H) / float(H)) * float(yh)))
            if np.isfinite(mu[tau]) and np.isfinite(R21[tau]):
                # the nonconformity score lands alone for every arm (it does NOT depend on a
                # quantile); only the interval indicators need the registered half-width formed
                # at that bar, so the calibration pool can never depend on itself.
                e = float(R21[tau]) - float(mu[tau])
                landed_signed.append(e)
                landed_abs.append(abs(e))
                if np.isfinite(q_eff[tau]) and q_eff[tau] > 0.0:
                    covered[tau] = abs(e) <= float(q_eff[tau])
                    below[tau] = e < -float(q_eff[tau])
                    landed_cov.append(1.0 if covered[tau] else 0.0)
                    landed_below.append(1.0 if below[tau] else 0.0)
        if t >= SIGNAL_WARMUP_BARS:
            qh = np.full(nH, np.nan)
            for j in range(nH):
                vals = landed_comp[j][-W_CONF:]
                if vals:
                    qh[j] = float(np.quantile(np.asarray(vals, dtype=np.float64), 0.75))
            if np.all(np.isfinite(qh)) and np.all(qh > 0.0):
                comp_q[t] = qh
                wgt = 1.0 / (qh * qh)
                wgt = wgt / wgt.sum()
                yh = np.array([math.sqrt(float(TARGET_H) / float(H)) * paths[H]["yhat"][t]
                               for H in HORIZONS], dtype=np.float64)
                if np.all(np.isfinite(yh)):
                    mu[t] = float(wgt @ yh)
        if np.isfinite(mu[t]):
            if arm in ("conformal", "frozen_train"):
                if arm == "conformal":
                    if landed_abs:
                        q_roll[t] = float(np.quantile(
                            np.asarray(landed_abs[-W_CONF:], dtype=np.float64), 0.75))
                    if last_anchor_bar is None or (t - last_anchor_bar) >= REFIT_EVERY:
                        if landed_abs:
                            anchor_value = float(np.quantile(
                                np.asarray(landed_abs, dtype=np.float64), 0.75))
                        last_anchor_bar = t
                    q_anchor[t] = anchor_value
                    if (np.isfinite(q_roll[t]) and np.isfinite(q_anchor[t])
                            and q_roll[t] > 0.0 and q_anchor[t] > 0.0):
                        q_eff[t] = ((q_roll[t] ** (1.0 - SHRINK_LAMBDA))
                                    * (q_anchor[t] ** SHRINK_LAMBDA))
                else:
                    if frozen_q is None and len(landed_abs) >= FROZEN_TRAIN_SCORES:
                        frozen_q = float(np.quantile(
                            np.asarray(landed_abs[:FROZEN_TRAIN_SCORES], dtype=np.float64), 0.75))
                    if frozen_q is not None:
                        q_eff[t] = frozen_q
                if np.isfinite(q_eff[t]) and q_eff[t] > 0.0:
                    sigma[t] = float(q_eff[t]) / Z_ALPHA
            elif arm == "mad":
                if landed_signed:
                    sigma[t] = float(np.mean(np.abs(np.asarray(
                        landed_signed[-W_CONF:], dtype=np.float64))))
            elif arm == "resid_std":
                if len(landed_signed) > 1:
                    sigma[t] = float(np.std(np.asarray(
                        landed_signed[-W_CONF:], dtype=np.float64), ddof=1))
            elif arm == "realized_vol_20":
                if np.isfinite(rvol[t]):
                    sigma[t] = float(rvol[t]) * math.sqrt(float(TARGET_H))
            if not (np.isfinite(sigma[t]) and sigma[t] > 0.0):
                counter("signal", "nonfinite_scale_on_a_defined_bar")
            else:
                f_raw[t] = KAPPA * float(mu[t]) / (float(sigma[t]) * float(sigma[t]))
        if landed_below:
            vals = np.asarray(landed_below[-DD_WINDOW:], dtype=np.float64)
            d_t = float(vals.mean())
            m_t = 1.0 - DD_BETA * (d_t - NOMINAL_ALPHA / 2.0) / (NOMINAL_ALPHA / 2.0)
            dial[t] = float(min(1.0, max(DD_FLOOR, m_t)))

    return {"n": n, "ret": ret, "rvol": rvol, "features": F, "mu": mu, "q_roll": q_roll,
            "q_anchor": q_anchor, "q_eff": q_eff, "sigma": sigma, "f_raw": f_raw, "dial": dial,
            "covered": covered, "below": below, "comp_q": comp_q,
            "landed_abs": landed_abs, "landed_signed": landed_signed,
            "landed_cov": landed_cov, "landed_below": landed_below,
            "fits": fit_total,
            "defined_bars": int(np.count_nonzero(np.isfinite(mu))),
            "scale_bars": int(np.count_nonzero(np.isfinite(sigma))),
            "coverage_bars": int(len(landed_cov)),
            "coverage_rate": (float(np.mean(landed_cov)) if landed_cov else None)}


_ARM_CACHE = {}


def _panel_arm_core(arm, close_by_market):
    """The registered panel science of ONE scale-estimator arm over the registered universe.

    Returns the per-market signal paths plus the cross-sectional book: the winsorised Kelly
    fractions, the proportional renormalisation above the 2.0 gross cap, the registered
    materiality tilt (`|w_i| >= 2.0 / n`, the equal share of the capped book) and the dial's
    per-instrument multiplier series `m_t`.
    """
    labels = sorted(close_by_market)
    if len(labels) != PANEL_SIZE:
        raise SystemExit("the registered panel needs exactly %d instruments, got %r"
                         % (PANEL_SIZE, labels))
    n = min(len(close_by_market[m]) for m in labels)
    if any(len(close_by_market[m]) != n for m in labels):
        counter("signal", "panel_grid_mismatch")
        raise SystemExit("panel series do not share one bar grid: %r"
                         % [len(close_by_market[m]) for m in labels])
    path = {m: _asset_path(np.asarray(close_by_market[m][:n], dtype=np.float64), arm)
            for m in labels}
    f = np.stack([np.where(np.isfinite(path[m]["f_raw"]),
                           np.clip(path[m]["f_raw"], -WINSOR, WINSOR), 0.0) for m in labels],
                 axis=1)
    gross_pre = np.abs(f).sum(axis=1)
    scale = np.where(gross_pre > GROSS_CAP, GROSS_CAP / np.maximum(gross_pre, 1e-300), 1.0)
    w = f * scale[:, None]
    cap_bars = int(np.count_nonzero(gross_pre > GROSS_CAP))
    if cap_bars:
        counter("signal", "gross_cap_renormalised", cap_bars)
    state = np.zeros((n, len(labels)), dtype=np.int64)
    for j in range(len(labels)):
        col = w[:, j]
        rule = np.where(np.isfinite(col) & (np.abs(col) >= MATERIALITY_TILT - 1e-12),
                        np.sign(col), 0.0)
        state[:, j] = rule.astype(np.int64)
    state[:SIGNAL_WARMUP_BARS, :] = 0
    dial = np.stack([path[m]["dial"] for m in labels], axis=1)
    core = {"arm": arm, "labels": labels, "n": n, "path": path, "f": f, "w": w, "state": state,
            "dial": dial, "gross_pre": gross_pre, "gross": np.abs(w).sum(axis=1),
            "scale": scale, "cap_bars": cap_bars,
            "defined_bars": int(np.count_nonzero(np.isfinite(w).any(axis=1))
                                if n else 0)}
    core["defined_bars"] = int(path[labels[0]]["defined_bars"])
    return core


def _arm_core_cached(arm):
    """The cached panel science of one arm over the LIVE registered cohorts."""
    if not PANEL_COHORTS:
        raise SystemExit("panel requested before the cohorts were registered")
    core = _ARM_CACHE.get(arm)
    if core is None:
        core = _panel_arm_core(arm, {m: PANEL_COHORTS[m].close for m in sorted(PANEL_COHORTS)})
        _ARM_CACHE[arm] = core
    return core


def _fresh_events(state):
    """Registered entry events: a bar whose target state CHANGES to a non-zero value."""
    n, k = state.shape
    events = np.zeros((n, k), dtype=np.int64)
    prev = np.zeros(k, dtype=np.int64)
    for t in range(n):
        cur = state[t]
        fresh = (cur != prev) & (cur != 0)
        events[t] = np.where(fresh, cur, 0)
        prev = cur
    return events


class Panel:
    """The registered four-instrument panel of one (scale-estimator arm, sizing) case.

    One arm core serves the two configurations (the dial only gates EXPOSURE: while
    m_t < DIAL_GATE no fresh entry is taken and the open exposure is flattened at the next
    bar's open, which is how the record's continuous leverage cut is carried inside a
    one-instrument rail).
    """

    _cache = {}

    def __init__(self, case, cohorts):
        self.case = tuple(case)
        self.case_label = case_name(self.case)
        self.arm = SCALE_ARMS[case_arm(self.case)]
        self.cfg = CONFIGS[case_cfg(self.case)]
        labels = sorted(cohorts)
        core = _arm_core_cached(self.arm)
        if labels != core["labels"]:
            raise SystemExit("panel labels %r != registered %r" % (labels, core["labels"]))
        self.markets = labels
        self.n = core["n"]
        self.core = core
        base_state = core["state"]
        if self.cfg == "drawdown_dial":
            gate = np.isfinite(core["dial"]) & (core["dial"] < DIAL_GATE)
            state = np.where(gate, 0, base_state)
        else:
            state = base_state
        self.state = state.astype(np.int64)
        self.events = _fresh_events(self.state)
        self.weights = core["w"]
        self.mu = core["mu"] if "mu" in core else None
        rule = np.zeros(self.state.shape, dtype=np.int64)
        for j in range(len(labels)):
            col = self.weights[:, j]
            rule[:, j] = np.where(np.isfinite(col) & (np.abs(col) >= MATERIALITY_TILT - 1e-12),
                                  np.sign(col), 0.0).astype(np.int64)
        if self.cfg == "cap_only":
            bad = int(np.count_nonzero(rule != self.state))
        else:
            bad = 0
        if bad:
            counter("signal", "state_not_derived_from_the_registered_rule", bad)
        for j in range(len(labels)):
            col = self.weights[:, j]
            finite = np.isfinite(col)
            if finite.any() and not np.all(np.isfinite(col[finite])):
                counter("signal", "materiality_threshold_violation")
        self.diag = {
            "case_name": self.case_label, "arm": self.arm, "config": self.cfg,
            "panel": labels, "bars": int(self.n),
            "warmup_bars": SIGNAL_WARMUP_BARS,
            "defined_bars": int(core["defined_bars"]),
            "gross_bars_above_cap": int(core["cap_bars"]),
            "gross_cap_binding_rate": round(float(core["cap_bars"]) / float(max(1, self.n)), 6),
            "gross_pre_cap_max": round(float(np.nanmax(core["gross_pre"])), 6),
            "gross_pre_cap_mean": round(float(np.nanmean(core["gross_pre"])), 6),
            "quantile_rule": QUANTILE_RULE,
            "materiality_tilt": MATERIALITY_TILT,
            "dial_gate": DIAL_GATE if self.cfg == "drawdown_dial" else None,
            "market_state_bars": {m: {"long": int((self.state[:, i] > 0).sum()),
                                      "short": int((self.state[:, i] < 0).sum()),
                                      "events": int((self.events[:, i] != 0).sum())}
                                  for i, m in enumerate(labels)},
            "policy_note": "the registered target state is the materiality tilt "
                           "(|w_i| >= the equal share of the capped gross book); the "
                           "alternative reading (any strictly non-zero weight -> invested) is "
                           "NOT evaluated (disclosed)",
        }

    def layer_for(self, market):
        i = self.markets.index(market)
        p = self.core["path"][market]
        return {"pos": np.ascontiguousarray(self.state[:, i]),
                "events": np.ascontiguousarray(self.events[:, i]),
                "yhat": np.ascontiguousarray(p["mu"])}

    def report(self):
        out = dict(self.diag)
        out["arms"] = {m: {"defined_bars": self.core["path"][m]["defined_bars"],
                           "coverage_bars": self.core["path"][m]["coverage_bars"],
                           "coverage_rate": self.core["path"][m]["coverage_rate"],
                           "fits": self.core["path"][m]["fits"]} for m in self.markets}
        return out


def panel_for(case):
    """Cached panel of one registered case over the registered universe."""
    key = tuple(case)
    p = Panel._cache.get(key)
    if p is None:
        if not PANEL_COHORTS:
            raise SystemExit("panel requested before the cohorts were registered")
        p = Panel(key, PANEL_COHORTS)
        Panel._cache[key] = p
    return p


class SignalLayer:
    """One (cohort, case) signal layer: the panel's per-market allocation weight, target state
    and entry events.  Built once per case and cached (the DCA grid re-reads it 48x per phase
    grid)."""

    def __init__(self, cohort, case, spec, panel=None):
        self.case = tuple(case)
        self.case_label = case_name(self.case)
        panel = panel if panel is not None else panel_for(self.case)
        if cohort.symbol not in panel.markets:
            raise SystemExit("cohort %s is not a member of the registered panel %r"
                             % (cohort.symbol, panel.markets))
        view = panel.layer_for(cohort.symbol)
        self.pos = view["pos"]
        self.events = view["events"]
        self.yhat = view["yhat"]
        self.panel = panel
        self.diag = dict(panel.diag)
        self.diag["cohort"] = cohort.symbol
        self.diag["market_state_bars"] = panel.diag["market_state_bars"][cohort.symbol]
        self.diag["signal_diagnostics"] = panel.report()["arms"][cohort.symbol]

    def report(self):
        return {"case_name": self.case_label, "arm": self.diag["arm"],
                "config": self.diag["config"], "defined_bars": self.diag["defined_bars"],
                "market_state_bars": self.diag["market_state_bars"],
                "warmup_bars": self.diag["warmup_bars"],
                "signal_diagnostics": self.diag["signal_diagnostics"]}


def signals_for(cohort, case):
    """The cached signal layer of one registered strategy case (frozen engine seam)."""
    key = tuple(case)
    layer = cohort._signal_cache.get(key)
    if layer is None:
        layer = SignalLayer(cohort, key, cohort.spec)
        cohort._signal_cache[key] = layer
    return layer


def signal_report_of(cohort):
    """The registered signal diagnostics of every case of one cohort."""
    out = {}
    for case in CASE_ORDER:
        layer = cohort._signal_cache.get(case)
        if layer is None:
            continue
        out[layer.case_label] = layer.report()
    return out


def build_network_inputs(cohort):
    """The registered causal signal input of one cohort: its daily simple-return series.

    The panel science reads only the bar's own and earlier closes (the momentum windows, the
    EWMA recursion and every rolling quantile are trailing), so the construction is causal by
    construction; `causality_probe` re-derives sampled bars from a truncated history and must
    reproduce the panel's own target state.
    """
    close = np.asarray(cohort.close, dtype=np.float64)
    ret = np.zeros_like(close)
    if len(close) > 1:
        ret[1:] = close[1:] / close[:-1] - 1.0
    return {"ret": ret, "close": close, "names": ["ret"],
            "floors": {"first_prediction_bar": FIRST_PREDICTION_BAR}}


def causality_probe(cohort, inputs, sample_bars=24):
    """Prefix-truncation equality on the registered signal of one cohort.

    The panel's own target state at a sampled bar must be reproduced when the history handed to
    the kernel is truncated at that bar.  A non-zero mismatch count is a look-ahead defect.
    """
    out = {"bars_probed": 0, "mismatches": 0, "columns": ["weight", "state"],
           "examples": [], "probe_case": case_name(PROBE_CASE), "arm": SCALE_ARMS[0],
           "note": "prefix-truncation equality on the registered conformal-Kelly panel; a "
                   "non-zero mismatch count is a look-ahead defect"}
    if not PANEL_COHORTS or cohort.symbol not in PANEL_COHORTS:
        out["note"] = "the panel was not registered when the probe ran"
        return out
    labels = sorted(PANEL_COHORTS)
    i = labels.index(cohort.symbol)
    core = _arm_core_cached(SCALE_ARMS[0])
    T = core["n"]
    lo, hi = SIGNAL_WARMUP_BARS, T - 1
    if T <= lo + 1:
        return out
    step = max(1, (hi - lo) // max(1, sample_bars))
    for t in range(lo, hi, step):
        closes = {m: np.asarray(PANEL_COHORTS[m].close[:t + 1], dtype=np.float64)
                  for m in labels}
        cut = _panel_arm_core(SCALE_ARMS[0], closes)
        dw = float(np.max(np.abs(cut["w"][t, :] - core["w"][t, :])))
        ds = int(np.max(np.abs(cut["state"][t, :] - core["state"][t, :])))
        out["bars_probed"] += 1
        if not (dw <= 1e-9 and ds == 0):
            out["mismatches"] += 1
            counter("signal", "causality_probe_mismatch")
            if len(out["examples"]) < 5:
                out["examples"].append({"bar": int(t), "weight_dev": dw, "state_dev": ds})
    return out

# ---------------------------------------------------------------------------
# phase 1: raw (read-only) -> Qlib bin store
# ---------------------------------------------------------------------------

def raw_months(symbol, interval, start, end):
    d = os.path.join(RAW_ROOT, "klines", symbol, interval)
    lo, hi = start[:7], end[:7]
    out = []
    prefix = "%s-%s-" % (symbol, interval)
    for name in sorted(os.listdir(d)):
        if not name.endswith(".jsonl.gz"):
            continue
        month = name[len(prefix):-len(".jsonl.gz")]
        if lo <= month <= hi:
            out.append(os.path.join(d, name))
    return out


def write_csv(symbol, interval, qlib_freq, start, end):
    out_dir = os.path.join(CSV_ROOT, qlib_freq)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "%s.csv" % symbol)
    lo_ms = utc_ms(start)
    hi_ms = utc_ms(end) + MS_PER_DAY - 1
    step = BAR_MS[interval]
    rows = 0
    first_ms = last_ms = None
    bad_grid = 0
    with open(out_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["date"] + FIELDS)
        for src in raw_months(symbol, interval, start, end):
            with gzip.open(src, "rt") as gz:
                for line in gz:
                    line = line.strip()
                    if not line:
                        continue
                    r = json.loads(line)
                    ms = r["open_time_ms"]
                    if ms < lo_ms or ms > hi_ms:
                        continue
                    if first_ms is not None and ms - last_ms != step:
                        bad_grid += 1
                    ts = time.gmtime(ms / 1000.0)
                    if interval == "1d":
                        stamp = "%04d-%02d-%02d" % (ts.tm_year, ts.tm_mon, ts.tm_mday)
                    else:
                        stamp = "%04d-%02d-%02d %02d:%02d:%02d" % (
                            ts.tm_year, ts.tm_mon, ts.tm_mday, ts.tm_hour, ts.tm_min, ts.tm_sec)
                    w.writerow([stamp, r["open"], r["high"], r["low"], r["close"], r["volume"]])
                    rows += 1
                    if first_ms is None:
                        first_ms = ms
                    last_ms = ms
    return {"rows": rows, "first_open_time_ms": first_ms, "last_open_time_ms": last_ms,
            "csv": out_path, "csv_bytes": os.path.getsize(out_path),
            "off_grid_steps": bad_grid, "bar_step_ms": step}


def csv_first_last_close(path):
    with open(path, newline="") as fh:
        r = csv.reader(fh)
        header = next(r)
        i = header.index("close")
        first = next(r)
        last = first
        for row in r:
            last = row
    return float(first[i]), float(last[i])


def build_bins(spec, attempt_dir, run_log):
    t0 = time.time()
    data = spec["data"]
    start, end = data["start"], data["end"]
    symbols = list(data["symbols"]) + list(
        (spec.get("falsification") or {}).get("cross_asset_symbols", []))
    # /qlib/work is a rebuildable derived/cache area (INV-5): rebuild from scratch so
    # the recorded build wall time / size describe THIS attempt's store.
    if os.path.isdir(WORK_ROOT):
        shutil.rmtree(WORK_ROOT)
    per_dataset = {}
    for symbol in symbols:
        for tf in data["timeframes"]:
            key = "%s/%s" % (symbol, tf["raw_interval"])
            per_dataset[key] = write_csv(symbol, tf["raw_interval"], tf["qlib_freq"], start, end)
            run_log("csv %s rows=%d bytes=%d off_grid_steps=%d"
                    % (key, per_dataset[key]["rows"], per_dataset[key]["csv_bytes"],
                       per_dataset[key]["off_grid_steps"]))

    for tf in data["timeframes"]:
        cmd = [VENV_PYTHON, DUMP_BIN, "dump_all",
               "--data_path", os.path.join(CSV_ROOT, tf["qlib_freq"]),
               "--qlib_dir", QLIB_DIR, "--freq", tf["qlib_freq"],
               "--include_fields", "open,close,high,low,volume",
               "--date_field_name", "date", "--max_workers", "1"]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=14400)
        if p.returncode != 0:
            raise SystemExit("dump_bin failed for freq %s rc=%d\n%s\n%s"
                             % (tf["qlib_freq"], p.returncode, p.stdout[-2000:], p.stderr[-2000:]))
        run_log("dump_bin %s ok" % tf["qlib_freq"])

    import qlib
    from qlib.data import D
    qlib.init(provider_uri=QLIB_DIR, region="cn", expression_cache=None, dataset_cache=None)

    readback = {}
    qstart, qend = qlib_window(start, end)
    for symbol in symbols:
        for tf in data["timeframes"]:
            freq = tf["qlib_freq"]
            cal = D.calendar(start_time=qstart, end_time=qend, freq=freq)
            df = D.features([symbol], ["$" + f for f in FIELDS],
                            start_time=qstart, end_time=qend, freq=freq)
            if len(df) == 0:
                raise SystemExit("qlib read-back empty for %s %s" % (symbol, freq))
            ts = list(df.index.get_level_values("datetime"))
            if ts != sorted(ts):
                raise SystemExit("qlib read-back not ascending for %s %s" % (symbol, freq))
            if int(df.isnull().sum().sum()) != 0:
                raise SystemExit("qlib read-back has nulls for %s %s" % (symbol, freq))
            if sorted(df.index.get_level_values("instrument").unique().tolist()) != [symbol]:
                raise SystemExit("qlib read-back instruments != [%s] for %s" % (symbol, freq))
            ds = per_dataset["%s/%s" % (symbol, tf["raw_interval"])]
            cs, cl = csv_first_last_close(ds["csv"])
            readback["%s/%s" % (symbol, freq)] = {
                "calendar_len": int(len(cal)), "rows": int(len(df)),
                "first": str(df.index[0][1]), "last": str(df.index[-1][1]),
                "first_close": float(df["$close"].iloc[0]),
                "last_close": float(df["$close"].iloc[-1]),
                "csv_first_close": cs, "csv_last_close": cl,
                "close_abs_diff_first": abs(float(df["$close"].iloc[0]) - cs),
                "close_abs_diff_last": abs(float(df["$close"].iloc[-1]) - cl),
                "raw_rows": ds["rows"], "off_grid_steps": ds["off_grid_steps"]}
            run_log("readback %s %s rows=%d (raw %d) first=%s last=%s"
                    % (symbol, freq, len(df), ds["rows"], str(df.index[0][1]),
                       str(df.index[-1][1])))

    report = {"qlib_dir": QLIB_DIR, "csv_root": CSV_ROOT,
              "build_wall_seconds": round(time.time() - t0, 3),
              "qlib_dir_bytes": dir_size(QLIB_DIR), "per_dataset": per_dataset,
              "readback": readback, "qlib_version": qlib.__version__,
              "bin_precision_note": "qlib .bin stores OHLCV as float32; the recorded "
                                    "close_abs_diff_* values are that storage rounding only "
                                    "(no resampling, no gap filling)."}
    atomic_write_json(os.path.join(attempt_dir, "artifacts", "bins_build.json"), report)
    run_log("bins build wall=%.1fs bytes=%d" % (report["build_wall_seconds"],
                                                report["qlib_dir_bytes"]))
    return report


# ---------------------------------------------------------------------------
# phase 2: pre-registered execution engine
# ---------------------------------------------------------------------------

class Cohort:
    """Bars of one (symbol, timeframe) cohort, loaded through the Qlib data layer.

    Carries the registered causal signal inputs (`build_network_inputs`), the panel signal-layer
    cache and the record's own univariate-baseline cache (falsification reader only)."""

    def __init__(self, symbol, tf, start, end, spec=None):
        from qlib.data import D
        freq = tf["qlib_freq"]
        qstart, qend = qlib_window(start, end)
        df = D.features([symbol], ["$" + f for f in FIELDS],
                        start_time=qstart, end_time=qend, freq=freq).sort_index()
        self.symbol = symbol
        self.timeframe = tf["raw_interval"]
        self.qlib_freq = freq
        # qlib datetimes are UTC wall time; .value is the epoch in ns (never local-time aware)
        self.open_time_ms = np.array(
            [int(ts.value // 1_000_000) for ts in df.index.get_level_values("datetime")],
            dtype=np.int64)
        for f in FIELDS:
            setattr(self, f, df["$" + f].to_numpy(dtype=np.float64))
        self.n = len(self.open_time_ms)
        self.bar_ms = int(self.open_time_ms[1] - self.open_time_ms[0]) if self.n > 1 else 0
        steps = np.diff(self.open_time_ms)
        self.non_bar_steps = int(np.count_nonzero(steps != self.bar_ms))
        # The whole clock mapping assumes the verified contiguous 1h grid: a missing bar
        # would silently shift every later boundary.
        if self.bar_ms <= 0 or self.non_bar_steps:
            counter("cohort", "bar_grid_not_contiguous", 1)
            raise SystemExit("cohort %s/%s: open_time_ms is not a contiguous %d ms bar grid "
                             "(%d deviation(s)); the clock mapping would be wrong"
                             % (symbol, self.timeframe, self.bar_ms, self.non_bar_steps))
        self._signal_cache = {}
        self._pred_cache = {}
        self.spec = spec
        self.features = None
        # the registered causal signal inputs (prefix invariance is probed by `causality_probe`
        # and reported with the run)
        self.inputs = build_network_inputs(self)
        self.inputs_report = {}
        self._baseline_cache = {}
        self.first_ts = str(df.index[0][1])
        self.last_ts = str(df.index[-1][1])
        self.price_increment = 0.0
        self.taker_fee = 0.0
        self.leverage = 1.0
        self.margin_maint = 0.0

    def slice(self, start_date, end_date):
        lo = utc_ms(start_date)
        hi = utc_ms(end_date) + MS_PER_DAY - 1
        return (int(np.searchsorted(self.open_time_ms, lo, side="left")),
                int(np.searchsorted(self.open_time_ms, hi, side="right")))

    def signals_for(self, case):
        """The cached signal layer of one registered strategy case (see `signals_for`)."""
        return signals_for(self, case)


def load_funding_series(symbol, start, end):
    """Every registered funding settlement in the window, with its truth_status disclosed.

    The D family uses funding as a COST only (the signal is clock time); the whole registered
    series is loaded and each row's truth_status is counted, so a modelled row is never
    presented as an official observation.
    """
    path = os.path.join(RAW_ROOT, "funding", symbol, "%s-funding.jsonl.gz" % symbol)
    lo, hi = utc_ms(start), utc_ms(end) + MS_PER_DAY - 1
    times, rates = [], []
    counts = {"official": 0, "modeled_funding": 0, "other": 0, "out_of_window": 0}
    with gzip.open(path, "rt") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            ms = r["funding_time_ms"]
            if ms < lo or ms > hi:
                counts["out_of_window"] += 1
                continue
            status = r.get("truth_status")
            counts[status if status in ("official", "modeled_funding") else "other"] += 1
            times.append(int(ms))
            rates.append(float(r["funding_rate"]))
    return (np.array(times, dtype=np.int64), np.array(rates, dtype=np.float64), counts)


def build_funding_index(cohort, times, rates, counts, label):
    """Funding settlements + the bar each one belongs to (the bar that CONTAINS it).

    Measured raw fact (all four symbols, 2022-01-01..2026-09-11): settlements sit on the 00/08/16
    UTC grid and run 0..28 ms LATE, never early.  SOL additionally carries a few 2h/4h-era
    settlements in early 2022 (04:00/20:00/... UTC), so the engine never assumes an 8h grid:
    every settlement is charged on its own instant.
    """
    containing = np.searchsorted(cohort.open_time_ms, times, side="left") - 1
    # Two bar mappings, because they answer two different questions; only `settle_bar` schedules a
    # charge, so the charge can never move because of the guard:
    #   settle_bar        - the bar whose CLOSE is the mark price at the settlement instant (the bar
    #                       that ENDS at it): the charge scheduling of `simulate`, unchanged.
    #   settle_bar_closed - the bar that CONTAINS the instant, half-open [open, open + 1h).  The two
    #                       differ only for an instant landing EXACTLY on a bar open, which the raw
    #                       data really does (0 ms jitter, measured 2025-10-01T00:00Z on all four
    #                       symbols): an ON-TIME settlement belongs to the bar it opens, so the
    #                       out-of-hold guard must not push it one bar out of the hold.  The guard
    #                       reads this one only.
    closed = np.searchsorted(cohort.open_time_ms, times, side="right") - 1
    return {"label": label, "counts": counts, "obs_times": times, "obs_rates": rates,
            "settle_bar": containing.astype(np.int64),
            "settle_bar_closed": closed.astype(np.int64),
            "first_obs_ms": int(times[0]) if len(times) else None,
            "last_obs_ms": int(times[-1]) if len(times) else None}


def fresh_events_of_slice(layer, i0, i1, delay, kind):
    """Every registered entry event of one slice: (slice-local event bar, sign).

    A fresh signal event is the entry event; the entry bar is the NEXT base bar (optionally
    moved by the registered `entry_delay_1_bar` execution stress) and must lie inside the
    slice, otherwise the event is skipped and counted (`entry_after_slice_end`) - it is never
    pulled back into the slice.
    """
    n = i1 - i0
    out = []
    for bl in np.flatnonzero(layer.events[i0:i1] != 0):
        b = int(bl)
        if b + 1 + delay >= n:
            counter(kind, "entry_after_slice_end")
            continue
        out.append((b, int(layer.events[i0 + b])))
    out.sort()
    return out


def exit_price_pnl(proceeds, basis):
    """Pure price PnL of one closing fill: exit proceeds minus cost basis.

    Contract 7.2 (v1.3.2, independent gross accounting): `gross_pnl` accumulates this
    value only - no fee, no funding - and is never reverse-derived from the net ledger.
    """
    return proceeds - basis


def pnl_decomposition_ok(m, tol=1e-3):
    """Fail-closed cross-check of the two independent accounting sources (contract 7.2)."""
    return abs(m["gross_pnl"] - m["fees"] - m["funding"] - m["net_pnl"]) <= tol


def simulate(cohort, p, rail, window, stress, slip_ticks, kind, series, diag=False,
             count_layers=True):
    """One pre-registered parameter case over one window slice (i0, i1).

    `stress` may carry: fee_mult / funding_mult / entry_delay_1_bar / slip_ticks / no_funding.
    The episode source is the registered signal layer: a fresh signal event at bar b enters at
    O[b+1+delay]; the episode is then held bar by bar through the registered exits (rail TP,
    resting invalidation, margin backstop, the record's target-state exit) and finally the slice
    end.  Long episodes walk the bar LOW first then the HIGH; short
    episodes mirror that sym-directionally (HIGH first, then LOW).

    `count_layers=False` keeps one call out of the full-window DCA ladder histogram: the winner
    and singleton-leg diagnostics re-run cells the grid already counted (card t_50c28da5).
    """
    i0, i1 = window
    C = cohort.close[i0:i1].tolist()
    H = cohort.high[i0:i1].tolist()
    L = cohort.low[i0:i1].tolist()
    O = cohort.open[i0:i1].tolist()
    n = len(C)
    if n <= 0:
        raise SystemExit("empty window slice for %s %s" % (cohort.symbol, kind))

    case = case_tuple(p)
    if case not in CASE_ORDER:
        counter(kind, "case_not_registered")
        raise SystemExit("unregistered strategy case %r (registered: %r)" % (case, CASE_ORDER))
    delay = 1 if stress.get("entry_delay_1_bar") else 0
    layer = signals_for(cohort, case)
    pos_path = layer.pos
    episodes = fresh_events_of_slice(layer, i0, i1, delay, kind)
    windows_seen = len(episodes)

    tick = cohort.price_increment
    taf = cohort.taker_fee * stress.get("fee_mult", 1.0)
    lev = cohort.leverage
    d0 = rail["spacing_d0"]
    tp_pct = rail["tp"]
    inval = rail["invalidation"]
    mult = rail["size_multiplier"]
    base = rail["base_quote"]

    if stress.get("no_funding"):
        fund_times = np.array([], dtype=np.int64)
        fund_rates = np.array([], dtype=np.float64)
        settle_bar = np.array([], dtype=np.int64)
        settle_bar_closed = np.array([], dtype=np.int64)
    else:
        fund_times = series["obs_times"]
        fund_rates = series["obs_rates"] * stress.get("funding_mult", 1.0)
        settle_bar = series["settle_bar"]
        # guard-only containment mapping; a caller that predates it falls back to the scheduling bar
        settle_bar_closed = series.get("settle_bar_closed", series["settle_bar"])
    nfund = int(len(fund_times))

    # slice-local day index, so the daily equity series is scoped to the EVALUATED slice and
    # never padded with the pre-window history (a padded series would dilute Sharpe by
    # sqrt(slice_days / cohort_days) - a measurement defect, not a convention).
    day_id = cohort.open_time_ms[i0:i1] // MS_PER_DAY
    _uniq, day_local = np.unique(day_id, return_inverse=True)
    day_local = day_local.astype(np.int64)
    n_days = int(day_local[-1]) + 1 if n else 0
    day_end = set(int(i) for i in np.flatnonzero(
        np.concatenate([day_local[1:] != day_local[:-1], [True]])))
    day_equity = [None] * n_days

    layers = [0] * 12
    realized = funding_paid = 0.0
    # Independent gross / price-PnL accumulator (contract 7.2, v1.3.2): fed ONLY by
    # `exit_price_pnl` at an exit/flatten, so `gross_pnl` can never be reverse-derived
    # from the net figure.
    gross_pnl = 0.0
    fees_total = 0.0
    fills = 0
    turnover = 0.0
    windows_entered = tp_hits = stop_hits = open_at_end = margin_calls = time_exits = 0
    bars_in_market = 0
    max_lev = 0.0
    util_sum = 0.0
    by_year = {}
    ep_stats = {"episodes": 0, "wins": 0, "net_pnl": 0.0, "hold_bars": 0}

    def charge_fee(amount):
        """A fee is paid at the instant of the fill and must reduce the realised equity there."""
        nonlocal realized, fees_total
        fees_total += amount
        realized -= amount
        return amount

    if not episodes:
        series_flat = _flat_series(day_equity)
        return _metrics(realized, fees_total, funding_paid, gross_pnl, 0, tp_hits, stop_hits,
                        open_at_end, margin_calls, time_exits, False, START_EQUITY,
                        series_flat, cohort, i0, i1, layers, max_lev, util_sum, bars_in_market,
                        fills, turnover, windows_seen, windows_entered, kind, {})

    halted = False
    min_entry_equity = START_EQUITY
    last_exit_bar = -1
    for b, sign in episodes:
        if b < last_exit_bar:
            # an event whose entry bar lies inside the previous hold is skipped: no re-entry
            # without a new, later position-change event.  An event exactly ON the previous
            # flatten bar is the long-short FLIP of the registered rule (flatten and enter at
            # the same open) and is therefore allowed.
            counter(kind, "reentry_skipped_in_position")
            continue
        # registered capital-exhaustion semantics: the account cannot lose more than itself,
        # so no new position may be opened once the realised equity is gone
        if START_EQUITY + realized <= 0.0:
            halted = True
            counter(kind, "entry_refused_exhausted")
            continue
        min_entry_equity = min(min_entry_equity, START_EQUITY + realized)
        eb = b + 1 + delay
        # executable mapping guard: the entry bar is the NEXT base bar after the signal event
        # (and the event really is a FRESH signal, i.e. not the previous bar's signal again)
        if (int(layer.events[i0 + b]) != sign
                or (i0 + b > 0 and int(layer.events[i0 + b - 1]) == sign)
                or not np.isfinite(layer.yhat[i0 + b])
                or int(cohort.open_time_ms[i0 + eb])
                != int(cohort.open_time_ms[i0 + b]) + (1 + delay) * cohort.bar_ms):
            counter(kind, "entry_bar_not_the_next_bar_after_the_signal")
        if not np.isfinite(layer.yhat[i0 + b]):
            counter(kind, "entry_before_a_defined_signal")
        entry_ms = int(cohort.open_time_ms[i0 + eb])
        windows_entered += 1
        # The card registers "1 tick ADVERSE (by instrument price_increment)" for every fill:
        # a long entry pays UP, a short entry sells LOWER (the D/G spelling copied into this
        # file had `O - sign * tick`, i.e. a one-tick CREDIT on the entry; corrected here and
        # pinned for every fill leg by the self-check).
        px = O[eb] + sign * slip_ticks * tick
        p0 = px
        qty = base * lev / px
        cost = qty * px
        ep_fund = 0.0
        ep_gross = 0.0
        ep_fees = 0.0
        charge_fee(qty * px * taf)
        fills += 1
        turnover += qty * px
        ep_fees += qty * px * taf
        levels = [p0 * (1.0 - sign * d0 * kk) for kk in range(12)]
        layers[0] += 1
        # funding: every settlement inside the CLOSED exposure interval [entry, exit] with the
        # measured +1 s jitter tolerance, charged in bar order so a later scale-in never
        # retroactively changes an earlier charge.
        kptr = int(np.searchsorted(fund_times, entry_ms - MS_JITTER_TOLERANCE_MS, side="left"))
        broke = False
        exit_bar = None
        exit_at_open = False
        t = eb
        while t < n:
            close_ms = int(cohort.open_time_ms[i0 + t]) + cohort.bar_ms
            while kptr < nfund and int(fund_times[kptr]) <= close_ms + MS_JITTER_TOLERANCE_MS:
                # the guard reads the CONTAINMENT bar (an on-time settlement belongs to the bar
                # it opens); the charge below keeps using the scheduling bar, so no charge moves.
                if not (i0 + eb <= int(settle_bar_closed[kptr]) <= i0 + t + 1):
                    counter(kind, "funding_bar_out_of_hold")
                ep_fund += sign * qty * C[t] * fund_rates[kptr]
                kptr += 1
            l = L[t]
            h = H[t]
            o = O[t]
            killed_at = None
            if sign > 0:
                # Descending-price walk (worst case for a long: the bar LOW is reached first,
                # then the high).  The invalidation is a RESTING stop at
                # average_cost x (1 - invalidation), so a ladder level below it cannot fill:
                # whichever trigger price is higher is reached first as the price descends.
                kk = 1
                while True:
                    stop = (cost / qty) * (1.0 - inval)
                    if kk <= MAX_ADD_LEVELS and levels[kk] >= stop:
                        trig, is_stop = levels[kk], False
                    else:
                        trig, is_stop = stop, True
                    if l > trig:
                        break
                    if is_stop:
                        killed_at = trig if o >= trig else o  # gap through the stop fills at the open
                        if kk > MAX_ADD_LEVELS:
                            counter(kind, "stop_at_ladder_boundary")
                        break
                    fpx = trig + slip_ticks * tick
                    q = base * (mult ** kk) * lev / fpx
                    qty += q
                    cost += q * fpx
                    charge_fee(q * fpx * taf)
                    ep_fees += q * fpx * taf
                    fills += 1
                    turnover += q * fpx
                    layers[kk] += 1
                    if kk == MAX_ADD_LEVELS:
                        counter(kind, "ladder_cap_reached")
                    kk += 1
            else:
                # Ascending-price walk (mirror image): the bar HIGH is reached first, then the
                # low.  Walking up from the bar open, the next trigger is whichever of the next
                # ladder level and the resting stop is LOWER.  A gap through the stop fills at
                # max(stop price, bar open).
                kk = 1
                while True:
                    stop = (cost / qty) * (1.0 + inval)
                    if kk <= MAX_ADD_LEVELS and levels[kk] <= stop:
                        trig, is_stop = levels[kk], False
                    else:
                        trig, is_stop = stop, True
                    if h < trig:
                        break
                    if is_stop:
                        killed_at = trig if o <= trig else o
                        if kk > MAX_ADD_LEVELS:
                            counter(kind, "stop_at_ladder_boundary")
                        break
                    fpx = trig - slip_ticks * tick
                    q = base * (mult ** kk) * lev / fpx
                    qty += q
                    cost += q * fpx
                    charge_fee(q * fpx * taf)
                    ep_fees += q * fpx * taf
                    fills += 1
                    turnover += q * fpx
                    layers[kk] += 1
                    if kk == MAX_ADD_LEVELS:
                        counter(kind, "ladder_cap_reached")
                    kk += 1
            eq = START_EQUITY + realized
            ueq = eq + sign * (qty * C[t] - cost)
            if killed_at is None and ueq <= cohort.margin_maint * qty * C[t]:
                xpx = C[t] - sign * slip_ticks * tick  # capital-exhaustion exit, adverse
                ep_gross = exit_price_pnl(sign * qty * xpx, sign * cost)
                realized += sign * qty * xpx - sign * cost
                gross_pnl += ep_gross
                charge_fee(qty * xpx * taf)
                ep_fees += qty * xpx * taf
                fills += 1
                turnover += qty * xpx
                margin_calls += 1
                exit_bar = t
                broke = True
                break
            if ueq > 0:
                lv = (qty * C[t]) / ueq
                if lv > max_lev:
                    max_lev = lv
                util_sum += (cost / lev) / ueq
            bars_in_market += 1
            if t in day_end:
                day_equity[day_local[t]] = ueq
            if killed_at is not None:
                xpx = killed_at - sign * slip_ticks * tick
                ep_gross = exit_price_pnl(sign * qty * xpx, sign * cost)
                realized += sign * qty * xpx - sign * cost
                gross_pnl += ep_gross
                charge_fee(qty * xpx * taf)
                ep_fees += qty * xpx * taf
                fills += 1
                turnover += qty * xpx
                stop_hits += 1
                exit_bar = t
                broke = True
                break
            tpx = (cost / qty) * (1.0 + sign * tp_pct)  # reduce-only breakeven-anchored TP
            if (h >= tpx) if sign > 0 else (l <= tpx):
                xpx = tpx - sign * slip_ticks * tick
                ep_gross = exit_price_pnl(sign * qty * xpx, sign * cost)
                realized += sign * qty * xpx - sign * cost
                gross_pnl += ep_gross
                charge_fee(qty * xpx * taf)
                ep_fees += qty * xpx * taf
                fills += 1
                turnover += qty * xpx
                tp_hits += 1
                exit_bar = t
                broke = True
                break
            # registered exit (3): the record's own exit condition, evaluated at this bar's
            # close.  The filtered target position is no longer the episode's direction (0 in
            # long-only mode, the opposite sign in long-short mode), so every layer is flattened
            # reduce-only at the NEXT bar's open.  A long-short flip is therefore a flatten at
            # that open, with the flip's own entry event carrying the new episode at the same
            # open (see the `b < last_exit_bar` guard above).
            pos_now = int(pos_path[i0 + t]) if (i0 + t) < len(pos_path) else 0
            if pos_now != sign:
                if t + 1 < n:
                    xpx = O[t + 1] - sign * slip_ticks * tick
                    ep_gross = exit_price_pnl(sign * qty * xpx, sign * cost)
                    realized += sign * qty * xpx - sign * cost
                    gross_pnl += ep_gross
                    charge_fee(qty * xpx * taf)
                    ep_fees += qty * xpx * taf
                    fills += 1
                    turnover += qty * xpx
                    time_exits += 1
                    counter(kind, "record_exit_taken")
                    exit_bar = t
                    exit_at_open = True
                    broke = True
                    break
                # an exit condition on the last bar has no next open to execute at: it is
                # deferred to the slice-end flatten
                counter(kind, "record_exit_deferred_to_slice_end")
            t += 1
        if not broke:
            # slice end: hard, reduce-only flatten of every layer
            exit_bar = n - 1
            xpx = C[exit_bar] - sign * slip_ticks * tick
            ep_gross = exit_price_pnl(sign * qty * xpx, sign * cost)
            realized += sign * qty * xpx - sign * cost
            gross_pnl += ep_gross
            charge_fee(qty * xpx * taf)
            ep_fees += qty * xpx * taf
            fills += 1
            turnover += qty * xpx
            open_at_end += 1
            counter(kind, "slice_end_flatten")
        # settlements at the exit instant itself (the hold's closing boundary) are charged on
        # the exit notional, which is the price at risk when the settlement lands
        if exit_at_open:
            exit_ms = int(cohort.open_time_ms[i0 + exit_bar + 1])
        else:
            exit_ms = int(cohort.open_time_ms[i0 + exit_bar]) + cohort.bar_ms
        while kptr < nfund and int(fund_times[kptr]) <= exit_ms + MS_JITTER_TOLERANCE_MS:
            # containment bar again: an on-time settlement exactly on the exit boundary belongs
            # to the bar it opens, i.e. the first bar after the hold - not a defect
            if not (i0 + eb <= int(settle_bar_closed[kptr]) <= i0 + exit_bar + 1):
                counter(kind, "funding_bar_out_of_hold")
            ep_fund += sign * qty * C[exit_bar] * fund_rates[kptr]
            kptr += 1
        funding_paid += ep_fund
        realized -= ep_fund
        last_exit_bar = exit_bar
        day_equity[day_local[exit_bar]] = START_EQUITY + realized
        if diag:
            ep_net = ep_gross - ep_fees - ep_fund
            year = time.strftime("%Y", time.gmtime(cohort.open_time_ms[i0 + b] / 1000.0))
            slot = by_year.setdefault(year, {"episodes": 0, "net_pnl": 0.0, "fees": 0.0,
                                             "funding": 0.0})
            slot["episodes"] += 1
            slot["net_pnl"] += ep_net
            slot["fees"] += ep_fees
            slot["funding"] += ep_fund
            ep_stats["episodes"] += 1
            ep_stats["net_pnl"] += ep_net
            ep_stats["hold_bars"] += (exit_bar - eb + 1)
            if ep_net > 0.0:
                ep_stats["wins"] += 1
    episodes_total = windows_entered
    # only GRID cells feed the full-window ladder histogram: the winner and singleton-leg
    # diagnostics re-run cells the grid already counted, so they must not inflate the level counts
    # (card t_50c28da5: the histogram over-counted level 0 by exactly the diagnostic episodes).
    if kind in FULL_WINDOW_GRID_KINDS and count_layers:
        acc = LAYER_TOTALS.setdefault("full", [0] * 12)
        for kk in range(12):
            acc[kk] += layers[kk]

    series_flat = _flat_series(day_equity)
    diag_out = {}
    if diag:
        diag_out = {"pnl_by_year": {k: _round_dict(v) for k, v in sorted(by_year.items())},
                    "episode_stats": _round_dict({
                        "episodes": ep_stats["episodes"], "wins": ep_stats["wins"],
                        "win_rate": (ep_stats["wins"] / float(ep_stats["episodes"]))
                                    if ep_stats["episodes"] else 0.0,
                        "mean_hold_bars": (ep_stats["hold_bars"] / float(ep_stats["episodes"]))
                                          if ep_stats["episodes"] else 0.0,
                        "net_pnl": ep_stats["net_pnl"]}),
                    "signal_diagnostics": dict(layer.diag),
                    "signal_case": layer.case_label,
                    "daily_equity": [round(float(v), 6) for v in series_flat],
                    "daily_equity_days": int(len(series_flat)),
                    "slice_first_day_utc": iso(int(cohort.open_time_ms[i0])),
                    "slice_last_day_utc": iso(int(cohort.open_time_ms[i1 - 1]))}
    return _metrics(realized, fees_total, funding_paid, gross_pnl, episodes_total, tp_hits,
                    stop_hits, open_at_end, margin_calls, time_exits, halted, min_entry_equity,
                    series_flat, cohort, i0, i1, layers, max_lev, util_sum, bars_in_market,
                    fills, turnover, windows_seen, windows_entered, kind, diag_out)


def _round_dict(d):
    return {k: (round(v, 6) if isinstance(v, float) else v) for k, v in d.items()}


def _flat_series(day_equity):
    """Daily equity marks of the EVALUATED slice, forward-filled across flat stretches.

    `day_equity` is indexed by the slice's own day index (never the cohort's), so the series
    covers exactly the window being measured.  A cohort-wide series padded with the
    pre-window history would dilute mean/std of the daily returns by sqrt(slice/cohort) and
    silently deflate Sharpe - a measurement defect, not a convention.
    """
    series = []
    last = START_EQUITY
    for v in day_equity:
        if v is not None:
            last = v
        series.append(last)
    return np.array(series, dtype=np.float64)


def _metrics(realized, fees_total, funding_paid, gross_pnl, episodes, tp_hits, stop_hits,
             open_at_end, margin_calls, time_exits, halted, min_entry_equity, series, cohort,
             i0, i1, layers, max_lev, util_sum, bars_in_market, fills, turnover, windows_seen,
             windows_entered, kind, diag_out):
    if len(series) > 2:
        rets = np.diff(series) / series[:-1]
        sd = float(np.std(rets, ddof=1))
        sharpe = float(np.mean(rets) / sd * np.sqrt(365.0)) if sd > 0 else 0.0
    else:
        sharpe = 0.0
    peak = np.maximum.accumulate(series)
    dd = series - peak
    dd_pct = dd / peak
    span_days = (cohort.open_time_ms[i1 - 1] + cohort.bar_ms
                 - cohort.open_time_ms[i0]) / 86400000.0
    years = max(span_days / 365.25, 1.0 / 365.25)
    end_eq = START_EQUITY + realized
    return {
        "net_pnl": realized, "fees": fees_total, "funding": funding_paid,
        "gross_pnl": gross_pnl,
        "episodes": episodes, "tp_hits": tp_hits, "stop_hits": stop_hits,
        "time_exits": time_exits, "open_at_end": open_at_end, "margin_calls": margin_calls,
        "halted": halted, "min_entry_equity": min_entry_equity,
        "ending_equity": end_eq, "sharpe": sharpe,
        "max_dd_usdt": float(dd.min()) if len(dd) else 0.0,
        "max_dd_pct": float(dd_pct.min()) if len(dd_pct) else 0.0,
        "max_effective_leverage": max_lev,
        "capital_utilization": (util_sum / bars_in_market) if bars_in_market else 0.0,
        "bars_in_market": bars_in_market, "days": int(len(series)), "years": years,
        "total_return_pct": end_eq / START_EQUITY - 1.0,
        # growth ratios are undefined once the account is non-positive -> null, never NaN
        "cagr": ((end_eq / START_EQUITY) ** (1.0 / years) - 1.0) if end_eq > 0.0 else None,
        "annualized_return": ((end_eq / START_EQUITY - 1.0) / years) if end_eq > 0.0 else None,
        "fills": fills, "turnover_usdt": turnover,
        "windows_seen": windows_seen, "windows_entered": windows_entered,
        "layers": layers, "diagnostics": diag_out,
    }


def rail_for(dca):
    """One registered DCA configuration -> the engine rail (four registered axes + basis)."""
    return {"base_quote": dca["base_quote"], "spacing_d0": dca["spacing_pct"],
            "size_multiplier": dca["size_multiplier"], "tp": dca["breakeven_tp_pct"],
            "invalidation": dca["invalidation_pct"]}


def case_label(case):
    """Human-readable label of one registered case, e.g. 'levy__lb22'."""
    return case_name(case)


def record(cohort, p, dca, kind, m):
    case = case_tuple(p)
    row = {"symbol": cohort.symbol, "timeframe": cohort.timeframe, "window_kind": kind,
           "window_case": case_label(case), "case_name": case_name(case)}
    row.update({f: p[f] for f in CASE_FIELDS})
    row.update({
           "spacing_pct": dca["spacing_pct"], "size_multiplier": dca["size_multiplier"],
           "breakeven_tp_pct": dca["breakeven_tp_pct"],
           "invalidation_pct": dca["invalidation_pct"],
           "net_pnl": round(m["net_pnl"], 6), "fees": round(m["fees"], 6),
           "funding": round(m["funding"], 6), "gross_pnl": round(m["gross_pnl"], 6),
           "ending_equity": round(m["ending_equity"], 6),
           "windows_seen": m["windows_seen"], "windows_entered": m["windows_entered"],
           "episodes": m["episodes"], "tp_hits": m["tp_hits"], "stop_hits": m["stop_hits"],
           "time_exits": m["time_exits"], "open_at_end": m["open_at_end"],
           "margin_calls": m["margin_calls"],
           "halted": m["halted"], "min_entry_equity": round(m["min_entry_equity"], 6),
           "sharpe": round(m["sharpe"], 6),
           "max_dd_usdt": round(m["max_dd_usdt"], 6), "max_dd_pct": round(m["max_dd_pct"], 6),
           "max_effective_leverage": round(m["max_effective_leverage"], 6),
           "capital_utilization": round(m["capital_utilization"], 6),
           "bars_in_market": m["bars_in_market"], "fills": m["fills"],
           "turnover_usdt": round(m["turnover_usdt"], 6),
           "days": m["days"], "years": round(m["years"], 6), "cagr": m["cagr"],
           "total_return_pct": round(m["total_return_pct"], 6),
           "annualized_return": m["annualized_return"]})
    return {k: row[k] for k in ROW_FIELDS}


def params_of(row):
    """One frozen grid row -> the strategy params dict the engine consumes."""
    return {f: row[f] for f in CASE_FIELDS}


def run_cohort(spec, cohort, run_log, series):
    """Every legal (strategy params x DCA config) case of the cohort, on every registered grid."""
    grid = spec["parameter_domain"]["grid_cases"]
    dca_grid = spec["dca_domain"]["grid"]
    slip = spec["costs"]["baseline_slippage_ticks"]
    windows = {"historical": (spec["data"]["historical_start"], spec["data"]["historical_end"]),
               "oos": (spec["data"]["oos_start"], spec["data"]["oos_end"]),
               "full": (spec["data"]["start"], spec["data"]["end"])}
    rows = {k: [] for k in COHORT_GRID_KINDS}
    slices = {k: cohort.slice(*v) for k, v in windows.items()}

    for kind in ("historical", "oos", "full"):
        sl = slices[kind]
        run_log("window %s %s/%s bars=[%d,%d) cases=%d"
                % (kind, cohort.symbol, cohort.timeframe, sl[0], sl[1],
                   len(grid) * len(dca_grid)))
        for p in grid:
            for dca in dca_grid:
                rows[kind].append(record(cohort, p, dca, kind,
                                         simulate(cohort, p, rail_for(dca), sl, {}, slip, kind,
                                                  series)))

    sl = slices["full"]
    for sname, stress in STRESS:
        for p in grid:
            for dca in dca_grid:
                rows[sname].append(record(cohort, p, dca, sname,
                                          simulate(cohort, p, rail_for(dca), sl, stress,
                                                   stress.get("slip_ticks", slip), sname,
                                                   series)))
    # H8 cost-attrition grid: 40 bps per fill (8 x the registered 5 bps taker fee).
    for p in grid:
        for dca in dca_grid:
            rows["cost_attrition_40bps"].append(record(
                cohort, p, dca, "cost_attrition_40bps",
                simulate(cohort, p, rail_for(dca), sl, COST_ATTRITION_STRESS, slip,
                         "cost_attrition_40bps", series)))
    # funding-baseline diagnostics run on the SAME windows as their counterparts
    for p in grid:
        for dca in dca_grid:
            rows["no_funding"].append(record(cohort, p, dca, "no_funding",
                                             simulate(cohort, p, rail_for(dca),
                                                      slices["historical"], {"no_funding": True},
                                                      slip, "no_funding", series)))
            rows["no_funding_full"].append(record(cohort, p, dca, "no_funding_full",
                                                  simulate(cohort, p, rail_for(dca), sl,
                                                           {"no_funding": True}, slip,
                                                           "no_funding_full", series)))
    return rows


# ---------------------------------------------------------------------------
# phase 3: pre-registered cohort-selector / cohort-survivor gate
#          (contract v1.3.0 sections 7.2 and 7.3; no post-hoc tuning)
# ---------------------------------------------------------------------------

def axis_values(spec):
    """The registered value list of every joint-space axis, in the registered order."""
    return {"window_case": [case_tuple(g) for g in spec["parameter_domain"]["grid_cases"]],
            "spacing_pct": list(spec["dca_domain"]["spacing_pct"]),
            "size_multiplier": list(spec["dca_domain"]["size_multiplier"]),
            "breakeven_tp_pct": list(spec["dca_domain"]["breakeven_tp_pct"]),
            "invalidation_pct": list(spec["dca_domain"]["invalidation_pct"])}


def cell_key(r):
    return (case_tuple(r),) + tuple(r[a] for a in DCA_AXES)


def tie_break_key(r, axes):
    """Registered-index lexical key: deterministic and free of float formatting."""
    key = [axes["window_case"].index(case_tuple(r))]
    for a in DCA_AXES:
        key.append(axes[a].index(r[a]))
    return tuple(key)


def require_historical(rows, where, allowed=("historical",)):
    """Selection and neighbourhood judgement may never read OOS (contract 7.3)."""
    bad = [r for r in rows if r.get("window_kind") not in allowed]
    if bad:
        raise ValueError("%s must be given %r rows only (contract 7.3): %d violation(s)"
                         % (where, allowed, len(bad)))


def select_cohort_winner(hist_rows, spec, allowed=("historical",)):
    """One deterministic winner per cohort, historical window only.

    Requirements, in order: (1) the cohort's best case must reach `min_episodes_is` historical
    episodes (the registered no-signal floor of this family), otherwise the whole cohort is
    culled for insufficient trades; (2) a candidate needs net_pnl > 0 AND sharpe > 0;
    (3) ranking is Sharpe desc, net_pnl desc, then the registered-index lexical key of the
    joint parameter cell.
    """
    require_historical(hist_rows, "select_cohort_winner", allowed)
    min_ep = spec["gates"]["min_episodes_is"]
    if not hist_rows or max(r["episodes"] for r in hist_rows) < min_ep:
        return None, "insufficient_trades"
    ok = [r for r in hist_rows
          if r["net_pnl"] > 0.0 and r["sharpe"] > 0.0 and r["episodes"] >= min_ep]
    if not ok:
        return None, "no_qualifying_candidate"
    axes = axis_values(spec)
    ok.sort(key=lambda r: (-r["sharpe"], -r["net_pnl"], tie_break_key(r, axes)))
    return ok[0], "selected"


def same_cell(rows, key):
    """The one row of another phase grid carrying the winner's exact joint cell."""
    hits = [r for r in rows if cell_key(r) == key]
    if len(hits) != 1:
        raise ValueError("expected exactly one row for cell %s, found %d" % (str(key), len(hits)))
    return hits[0]


def cohort_neighbourhood(hist_rows, winner, spec):
    """Face-adjacent (+-1 registered step on exactly one axis) sign agreement.

    Historical window only, by construction (`require_historical`).  The `window_case` axis is
    categorical: its registered-index adjacency is the registered reading of the contract's
    single-axis +-1 rule, and this is disclosed (a step on that axis may change the leg set).
    """
    require_historical(hist_rows, "cohort_neighbourhood")
    axes = axis_values(spec)
    idx = {cell_key(r): r for r in hist_rows}
    wkey = cell_key(winner)
    wsign = winner["net_pnl"] > 0.0
    neighbours, missing = [], []
    for ai, axis in enumerate(AXES):
        vals = axes[axis]
        pos = vals.index(wkey[ai])
        for step in (-1, 1):
            npos = pos + step
            if not (0 <= npos < len(vals)):
                continue
            nkey = list(wkey)
            nkey[ai] = vals[npos]
            row = idx.get(tuple(nkey))
            if row is None:
                missing.append(tuple(nkey))
            else:
                neighbours.append(row)
    if missing:
        raise ValueError("registered joint grid is not the full product: missing neighbours %s"
                         % str(missing[:3]))
    agree = sum(1 for r in neighbours if (r["net_pnl"] > 0.0) == wsign)
    frac = (agree / float(len(neighbours))) if neighbours else 0.0
    return {"neighbours": len(neighbours), "agreeing": agree,
            "same_sign_fraction": round(frac, 6),
            "winner_net_pnl_positive": wsign,
            "threshold": spec["gates"]["neighborhood_min_same_sign_fraction"],
            "case_axis_note": "window_case is categorical; its +-1 registered step is the "
                              "registered reading of the contract single-axis rule",
            "passed": frac >= spec["gates"]["neighborhood_min_same_sign_fraction"]}


def _pick(row, keys):
    return {k: row[k] for k in keys if k in row}


def evaluate_cohort(spec, cohort_label, rows):
    """The whole cohort decision for one (symbol, timeframe) cohort."""
    hist = rows["historical"]
    winner, reason = select_cohort_winner(hist, spec)
    out = {"cohort": cohort_label, "outcome": "CULLED", "no_winner_reason": None,
           "cull_reasons": [], "winner": None, "neighbourhood": None, "metrics": {}}
    if winner is None:
        out["no_winner_reason"] = reason
        out["cull_reasons"].append(reason)
        return out
    key = cell_key(winner)
    nb = cohort_neighbourhood(hist, winner, spec)
    oos = same_cell(rows["oos"], key)
    full = same_cell(rows["full"], key)
    stress = {s: same_cell(rows[s], key) for s, _ in STRESS}
    out["winner"] = _pick(winner, DCA_AXES)
    out["winner"].update({f: winner[f] for f in CASE_FIELDS})
    # NOTE: the winner cell must carry ONLY registered parameter axes - the survivor
    # index / evidence readers refuse any key outside strategy_param_fields + dca_param_fields.
    out["winner_case_label"] = case_label(case_tuple(winner))
    out["metrics"] = {
        "historical": _pick(winner, WINNER_METRIC_KEYS),
        "oos": _pick(oos, WINNER_METRIC_KEYS),
        "full": _pick(full, WINNER_METRIC_KEYS),
        "robustness": {s: _pick(stress[s], WINNER_METRIC_KEYS) for s in stress},
        "no_funding_reference": _pick(same_cell(rows["no_funding"], key), WINNER_METRIC_KEYS),
        "no_funding_full_reference": _pick(same_cell(rows["no_funding_full"], key),
                                          WINNER_METRIC_KEYS),
        "cost_attrition_40bps": _pick(same_cell(rows["cost_attrition_40bps"], key),
                                      WINNER_METRIC_KEYS),
    }
    out["neighbourhood"] = nb
    reasons = []
    gates = spec["gates"]
    # G2b: the registered OOS sufficiency floor.
    if oos["episodes"] < gates["min_episodes_oos"]:
        reasons.append("insufficient_trades")
    # G4: the registered OOS economic gate: net_pnl > 0 AND sharpe > 0 for the winner cell.
    if not (oos["net_pnl"] > 0.0 and oos["sharpe"] > 0.0):
        reasons.append("oos_economic")
    if not (full["net_pnl"] > 0.0):
        reasons.append("full_economic")
    failing_stress = [s for s, _ in STRESS if not (stress[s]["net_pnl"] > 0.0)]
    if failing_stress:
        reasons.append("robustness_economic:" + ",".join(failing_stress))
    # The 40 bps cost-attrition grid is deliberately NOT a cohort cull here: the card's
    # registered survivor requirements are the four execution stress grids, and attrition is
    # registered as a FAMILY-level reader (cost-boundary, never PASS-bearing) instead.
    if not nb["passed"]:
        reasons.append("parameter_neighbourhood")
    out["cull_reasons"] = reasons
    out["outcome"] = "CULLED" if reasons else "SURVIVOR"
    return out


def family_disposition(survivors, coverage_complete):
    """Contract 7.2/7.3 (v1.4.0).  Coverage/technical incompleteness wins."""
    if not coverage_complete:
        return {"disposition": "TECHNICAL_INCOMPLETE",
                "verdict_recommendation": "TECHNICAL_INCOMPLETE",
                "performance_claimable_recommendation": False,
                "mapping_version": CONTRACT_SEMANTICS_VERSION}
    if not survivors:
        return {"disposition": "REJECT / NO_SURVIVOR", "verdict_recommendation": "REJECT",
                "performance_claimable_recommendation": False,
                "mapping_version": CONTRACT_SEMANTICS_VERSION}
    if len(survivors) == 1:
        return {"disposition": "SURVIVOR_FOUND", "verdict_recommendation": "PASS",
                "performance_claimable_recommendation": True,
                "mapping_version": CONTRACT_SEMANTICS_VERSION}
    return {"disposition": "MULTIPLE_SURVIVORS", "verdict_recommendation": "PASS",
            "performance_claimable_recommendation": True,
            "mapping_version": CONTRACT_SEMANTICS_VERSION}

# ---------------- registered family-level readers (never cull a cohort; never PASS-bearing) ---
# Every reader below is registered in the immutable round-spec BEFORE the first run and mirrors
# one item of the record's own falsification battery.  A reader never culls a cohort by itself:
# a hit can only move a PASS to DEFERRED, and the evidence decides the final verdict
# (contract 6.4 / 7.3).

def _sign(x):
    return 0 if x is None else (1 if x > 0.0 else (-1 if x < 0.0 else 0))


def _cell(rows_by_cohort, cohort, kind, fields, dca):
    """One registered case x DCA cell of one cohort on one phase grid (None when absent)."""
    want = (tuple(fields),) + tuple(dca[a] for a in DCA_AXES)
    for r in rows_by_cohort[cohort][kind]:
        if cell_key(r) == want:
            return r
    return None


def _winner_case(c, spec):
    """The winner's registered joint cell: strategy case fields + the 4 DCA axes + the
    registered project constant base_quote (the rail cannot be built without it)."""
    w = c["winner"]
    dca = {a: w[a] for a in DCA_AXES}
    dca["base_quote"] = spec["dca_domain"]["base_quote"]
    return tuple(int(w[f]) for f in CASE_FIELDS), dca


def params_dict(case):
    """A registered case tuple -> the strategy params dict the engine consumes."""
    return {f: int(v) for f, v in zip(CASE_FIELDS, case)}


def case_fields_for(arm, cfg):
    """The registered one-hot case tuple of (arm, configuration) - the inverse of case_arm /
    case_cfg, used by the registered readers."""
    i = SCALE_ARMS.index(arm)
    j = CONFIGS.index(cfg)
    return tuple([1 if a == i else 0 for a in range(len(SCALE_ARMS))]
                 + [1 if b == j else 0 for b in range(len(CONFIGS))])


def _rolling_means(values, window):
    """Rolling means of a 1-D indicator sequence (one value per WINDOW-long stretch)."""
    out = []
    for i in range(0, len(values) - window + 1):
        out.append(float(np.mean(np.asarray(values[i:i + window], dtype=np.float64))))
    return out


def coverage_calibration_check(spec, cohort_results, diag_inputs):
    """Reader 1 - record falsification item 1 ("Coverage Calibration Stability Test").

    Procedure (record): "In an expanding walk-forward test across subsequent out-of-sample data
    (2024 to present), calculate the realized marginal coverage rate across all 8 assets.
    Falsification Rule: If realized marginal coverage deviates by more than +-5.0 percentage
    points from nominal (i.e. realized coverage < 0.70 or > 0.80 over a rolling 252-day window),
    reject the validity of the geometric anchor shrinkage calibration."

    Definition (EXECUTED): the registered conformal intervals of every instrument are read from
    the registered signal path - a landed bar carries the indicator "the realized 21-bar return
    fell inside the interval formed at that bar" - and the reader reports (a) the marginal
    realized coverage over the whole registered window, (b) the rolling 252-landed-bar coverage
    extremes, per instrument and pooled across the panel.  TRIGGERED when any rolling 252-bar
    window (per instrument or pooled) leaves [0.70, 0.80].  The record's 8-asset US ETF panel is
    replaced by the registered four-instrument local panel; the window is the local registry's
    own full window (the record's "2024 to present" is the equivalent walk-forward stretch of
    the local calendar, whose first registered prediction lands in 2024).
    """
    out = {"registered_item": "coverage-calibration-stability", "evaluated": False, "hit": False,
           "landing": "family-level: a hit must NEVER be recorded as PASS",
           "nominal_coverage": 1.0 - NOMINAL_ALPHA, "low": COVERAGE_LOW, "high": COVERAGE_HIGH,
           "rolling_window_bars": COVERAGE_ROLLING_BARS, "instruments": {}}
    try:
        core = _arm_core_cached("conformal")
    except SystemExit as exc:
        out["reason"] = "the registered conformal panel is unavailable: %s" % exc
        return out
    pooled = []
    for label in core["labels"]:
        p = core["path"][label]
        mask = np.isfinite(p["q_eff"]) & np.isfinite(p["mu"])
        pos = np.flatnonzero(mask)
        vals = [1.0 if bool(p["covered"][i]) else 0.0 for i in pos]
        roll = _rolling_means(vals, COVERAGE_ROLLING_BARS) if len(vals) >= COVERAGE_ROLLING_BARS \
            else []
        marginal = float(np.mean(vals)) if vals else None
        hit_i = any(v < COVERAGE_LOW or v > COVERAGE_HIGH for v in roll)
        out["instruments"][label] = {
            "landed_observations": len(vals), "marginal_coverage": (
                None if marginal is None else round(marginal, 6)),
            "rolling_windows": len(roll),
            "rolling_min": (round(min(roll), 6) if roll else None),
            "rolling_max": (round(max(roll), 6) if roll else None),
            "rolling_extreme_outside_band": bool(hit_i)}
        pooled.append((pos, vals))
        out["evaluated"] = True
        out["hit"] = out["hit"] or hit_i
    # pooled reading: the per-landed-bar cross-instrument mean, then the same rolling rule
    if pooled:
        common = pooled[0][0]
        ok = all(np.array_equal(common, p) for p, _ in pooled)
        if ok and len(common) >= COVERAGE_ROLLING_BARS:
            pooled_vals = [float(np.mean([v[i] for _p, v in pooled])) for i in range(len(common))]
            roll = _rolling_means(pooled_vals, COVERAGE_ROLLING_BARS)
            hit_p = any(v < COVERAGE_LOW or v > COVERAGE_HIGH for v in roll)
            out["pooled"] = {"landed_observations": len(pooled_vals),
                             "marginal_coverage": round(float(np.mean(pooled_vals)), 6),
                             "rolling_windows": len(roll), "rolling_min": round(min(roll), 6),
                             "rolling_max": round(max(roll), 6),
                             "rolling_extreme_outside_band": bool(hit_p),
                             "index_alignment": "identical landed-bar positions on all instruments"}
            out["hit"] = out["hit"] or hit_p
        else:
            out["pooled"] = {"index_alignment": "not pooled (landed-bar positions differ)"}
    out["note"] = ("the record's +-5.0 pp band on a rolling 252-day window is applied verbatim; "
                   "coverage is an empirical property of overlapping 21-bar labels (the record's "
                   "own non-exchangeability caveat) and is reported, never used to cull")
    return out


def conformal_vs_realized_vol_horserace(spec, cohort_results, diag_inputs, rows_by_cohort):
    """Reader 2 - record falsification item 2 ("Conformal vs. Realized Volatility Scale Sizing
    Horserace").

    Procedure (record): "Compare Conformal Kelly directly against an identical portfolio sized
    with rolling 20-day realized standard deviation at matched gross leverage and transaction
    costs.  Falsification Rule: If the conformal scale estimator fails to achieve a higher
    Sharpe ratio or higher annualized net log growth than sample standard deviation over a
    minimum 500-day evaluation sample, reject the hypothesis that conformal quantiles provide a
    superior scale proxy."

    Definition (EXECUTED on the registered grid): the record's own comparator arm
    (`realized_vol_20`, the 20-bar realized standard deviation of daily returns carried onto the
    registered 21-bar reference) IS a registered search arm of this round, so the horserace is
    read directly off the registered cells: for every cohort the elected winner's DCA
    configuration is compared across the two arms on the full window (and on the OOS slice),
    under the identical rail, fee, slippage and funding policy - i.e. at matched costs by
    construction.  The must-both legs are the rail's Sharpe and its annualized net return (the
    registered proxy for the record's annualized net log growth; disclosed).  The 500-day floor
    is checked against the evaluated slice.  TRIGGERED when the conformal arm fails to exceed
    the comparator on Sharpe in a majority of the evaluated cohorts, or fails to exceed it on
    annualized net growth in a majority of the evaluated cohorts.
    """
    out = {"registered_item": "conformal-vs-realized-volatility-horserace", "evaluated": False,
           "hit": False, "landing": "family-level: a hit must NEVER be recorded as PASS",
           "min_sample_bars": HORSERACE_MIN_SAMPLE_BARS, "cohorts": {},
           "growth_proxy": "the rail's annualized_return (net of fees, funding and slippage) on "
                           "the evaluated slice; the record's portfolio log-growth figure has no "
                           "counterpart in a one-instrument rail (disclosed)"}
    better_sharpe = better_growth = total = 0
    for c in cohort_results:
        if c["winner"] is None:
            continue
        _wcase, dca = _winner_case(c, spec)
        cfg = CONFIGS[case_cfg(tuple(int(c["winner"][f]) for f in CASE_FIELDS))]
        conf_case = case_fields_for("conformal", cfg)
        rvol_case = case_fields_for("realized_vol_20", cfg)
        row_c = _cell(rows_by_cohort, c["cohort"], "full", conf_case, dca)
        row_r = _cell(rows_by_cohort, c["cohort"], "full", rvol_case, dca)
        if row_c is None or row_r is None:
            continue
        total += 1
        s_c, s_r = float(row_c["sharpe"]), float(row_r["sharpe"])
        g_c, g_r = row_c["annualized_return"], row_r["annualized_return"]
        g_c = -1e9 if g_c is None else float(g_c)
        g_r = -1e9 if g_r is None else float(g_r)
        bs, bg = s_c > s_r, g_c > g_r
        better_sharpe += 1 if bs else 0
        better_growth += 1 if bg else 0
        out["cohorts"][c["cohort"]] = {
            "winner_case": c.get("winner_case_label"), "config": cfg,
            "dca": {a: dca[a] for a in DCA_AXES},
            "full_days": int(row_c["days"]),
            "conformal_sharpe": round(s_c, 6), "realized_vol_sharpe": round(s_r, 6),
            "conformal_annualized_return": (None if row_c["annualized_return"] is None
                                            else round(float(row_c["annualized_return"]), 6)),
            "realized_vol_annualized_return": (None if row_r["annualized_return"] is None
                                               else round(float(row_r["annualized_return"]), 6)),
            "conformal_net_pnl": round(float(row_c["net_pnl"]), 6),
            "realized_vol_net_pnl": round(float(row_r["net_pnl"]), 6),
            "conformal_exceeds_sharpe": bool(bs), "conformal_exceeds_growth": bool(bg),
            "sample_floor_met": bool(int(row_c["days"]) >= HORSERACE_MIN_SAMPLE_BARS),
            "hit_sharpe_leg": bool(not bs), "hit_growth_leg": bool(not bg)}
        out["evaluated"] = True
    out["cohorts_evaluated"] = total
    out["cohorts_conformal_exceeds_sharpe"] = better_sharpe
    out["cohorts_conformal_exceeds_growth"] = better_growth
    if total:
        out["hit"] = bool(better_sharpe * 2 < total or better_growth * 2 < total)
    out["note"] = ("matched gross leverage and transaction costs hold by construction: the two "
                   "arms share the rail, the DCA configuration, the fee/slippage/funding policy "
                   "and the window; only the registered scale estimator differs")
    return out


def dial_placebo_check(spec, cohort_results, diag_inputs, rows_by_cohort):
    """Reader 3 - record falsification item 3 ("Downside Miscoverage Dial Placebo Test").

    Procedure (record): "Generate 1,000 circular block-bootstrap resamples of the downside
    miscoverage multiplier series m_t to destroy event timing while preserving the marginal
    distribution of leverage cuts.  Falsification Rule: If Config B's maximum drawdown fails to
    beat at least 95% of the synthetic placebos (p > 0.05), falsify the timing efficacy of the
    drawdown dial."

    Definition: NOT EXECUTED.  The record's placebo test is a property of a CONTINUOUS portfolio
    multiplier path; this round's rail is one-instrument and owns the position size, so the dial
    enters only as the registered exposure gate (m_t < 0.5).  The 1,000 circular block-bootstrap
    resamples of a continuous m_t path therefore have no counterpart here and are recorded as
    not_executed rather than lowered or simulated.  The MEASURED local analogues are reported:
    the dial series' own distribution per instrument and the measured execution footprint of the
    gate (how many registered cells the dial actually changes).
    """
    out = {"registered_item": "downside-miscoverage-dial-placebo", "evaluated": False,
           "hit": False, "status": "not_executed",
           "landing": "family-level: a hit must NEVER be recorded as PASS",
           "placebo_draws": DIAL_PLACEBO_DRAWS, "instruments": {}}
    try:
        core = _arm_core_cached("conformal")
    except SystemExit as exc:
        out["reason"] = "the registered conformal panel is unavailable: %s" % exc
        return out
    for label in core["labels"]:
        d = core["dial"][:, core["labels"].index(label)]
        fin = d[np.isfinite(d)]
        out["instruments"][label] = {
            "observations": int(fin.size),
            "mean": (round(float(fin.mean()), 6) if fin.size else None),
            "min": (round(float(fin.min()), 6) if fin.size else None),
            "max": (round(float(fin.max()), 6) if fin.size else None),
            "fraction_at_floor": (round(float(np.mean(fin <= DD_FLOOR + 1e-12)), 6)
                                  if fin.size else None),
            "fraction_below_gate": (round(float(np.mean(fin < DIAL_GATE)), 6)
                                    if fin.size else None),
            "gate_days": int(np.count_nonzero(fin < DIAL_GATE))}
    footprint = {"cells_compared": 0, "cells_with_a_different_episode_count": 0, "cohorts": {}}
    for arm in SCALE_ARMS:
        case_a = case_fields_for(arm, "cap_only")
        case_b = case_fields_for(arm, "drawdown_dial")
        for label in sorted(rows_by_cohort):
            rows_a = [r for r in rows_by_cohort[label]["full"] if case_tuple(r) == case_a]
            rows_b = [r for r in rows_by_cohort[label]["full"] if case_tuple(r) == case_b]
            idx_b = {tuple(r[a] for a in DCA_AXES): r for r in rows_b}
            diff = 0
            for r in rows_a:
                rb = idx_b.get(tuple(r[a] for a in DCA_AXES))
                if rb is None:
                    continue
                footprint["cells_compared"] += 1
                if int(rb["episodes"]) != int(r["episodes"]):
                    diff += 1
            footprint["cells_with_a_different_episode_count"] += diff
            footprint["cohorts"].setdefault(label, {})[arm] = {
                "cells": len(rows_a), "episodes_differ": diff}
    out["measured_execution_footprint"] = footprint
    out["reason"] = ("the registered rail cannot express the record's continuous portfolio "
                     "leverage multiplier, so the 1,000-resample placebo test is not executed; "
                     "the dial's own series and its measured execution footprint are reported "
                     "instead of a weakened substitute")
    return out


def leverage_cap_ablation_check(spec, cohort_results, diag_inputs):
    """Reader 4 - record falsification item 4 ("Leverage Cap Ablation").

    Procedure (record): "Relax the 2.0x gross leverage cap and simulate unconstrained fractional
    Kelly allocations.  Falsification Rule: If the unconstrained system experiences portfolio
    drawdowns exceeding 60% or leverage spikes > 5x, confirm that the empirical success of the
    reported strategy is conditionally dependent on the structural work of the 2.0 gross cap
    rather than the unassisted Kelly formula."

    Definition: NOT EXECUTED.  The drawdown leg is a property of an unconstrained PORTFOLIO
    simulation (the source's 8-asset book with a continuous gross-exposure path); this round's
    rail is one instrument at a time and cannot express portfolio-level gross leverage, so the
    unconstrained system is not simulated and the item is recorded as not_executed rather than
    approximated.  Both MEASURED inputs the record's rule needs ARE reported: the fraction of
    bars on which the raw (pre-cap) book exceeds 2.0 - directly comparable to the record's own
    "binds on 97.7% of development days" - and the maximum pre-cap gross exposure, the local
    counterpart of the record's "leverage spikes > 5x" leg.
    """
    out = {"registered_item": "leverage-cap-ablation", "evaluated": False, "hit": False,
           "status": "not_executed",
           "landing": "family-level: a hit must NEVER be recorded as PASS",
           "uncapped_dd_limit": UNCAPPED_DD_LIMIT, "uncapped_leverage_limit": UNCAPPED_LEV_LIMIT,
           "instruments": {}}
    for arm in SCALE_ARMS:
        try:
            core = _arm_core_cached(arm)
        except SystemExit as exc:
            out["reason"] = "the registered panel is unavailable: %s" % exc
            return out
        for label in core["labels"]:
            j = core["labels"].index(label)
            g = core["gross_pre"][SIGNAL_WARMUP_BARS:]
            fin = g[np.isfinite(g)]
            out["instruments"].setdefault(label, {})[arm] = {
                "defined_bars": int(fin.size),
                "cap_binding_rate": (round(float(np.mean(fin > GROSS_CAP)), 6)
                                     if fin.size else None),
                "max_pre_cap_gross": (round(float(fin.max()), 6) if fin.size else None),
                "mean_pre_cap_gross": (round(float(fin.mean()), 6) if fin.size else None),
                "leverage_spike_above_5x": bool(fin.size and float(fin.max()) > UNCAPPED_LEV_LIMIT)}
    out["reason"] = ("the unconstrained portfolio drawdown cannot be measured inside a "
                     "one-instrument rail; the item is not executed, and the two measured "
                     "quantities the record's rule names (cap binding rate and the pre-cap "
                     "leverage extreme) are disclosed instead of an approximation")
    out["record_reference"] = ("the record reports the cap binding on 97.7% of development "
                               "days; the same statistic is measured here on the local panel")
    return out

def summarize(spec, grid_rows, layers, diag_inputs, slice_days):
    import random
    import statistics as st
    expected_axes = axis_values(spec)
    need = {k: spec["expected"]["case_evaluations_per_grid"] for k in COHORT_GRID_KINDS}
    coverage = {k: len(grid_rows.get(k, [])) for k in need}
    full = grid_rows["full"]
    cohorts = sorted({(r["symbol"], r["timeframe"]) for r in full})
    cohort_labels = ["%s/%s" % c for c in cohorts]

    per_cohort = {label: {k: [r for r in grid_rows[k]
                              if "%s/%s" % (r["symbol"], r["timeframe"]) == label]
                          for k in COHORT_GRID_KINDS} for label in cohort_labels}

    strategy_product = set(expected_axes["window_case"])
    dca_product = set((a, b, c, d) for a in expected_axes["spacing_pct"]
                      for b in expected_axes["size_multiplier"]
                      for c in expected_axes["breakeven_tp_pct"]
                      for d in expected_axes["invalidation_pct"])
    strategy_cells, dca_cells = set(), set()
    for r in full:
        strategy_cells.add(case_tuple(r))
        dca_cells.add(tuple(r[a] for a in DCA_AXES))

    cells_per_cohort_ok = all(len(per_cohort[c][k]) == spec["expected"]["base_combinations_per_cohort"]
                              for c in cohort_labels for k in COHORT_GRID_KINDS) if cohort_labels else False
    coverage_complete = (all(coverage[k] == need[k] for k in need)
                         and len(cohorts) == spec["expected"]["cohorts"]
                         and strategy_cells == strategy_product
                         and dca_cells == dca_product
                         and cells_per_cohort_ok)

    # An incomplete measurement is never judged: if any registered grid or cohort cell is
    # missing, no cohort is evaluated at all and the family lands on TECHNICAL_INCOMPLETE
    # (contract 7.2/7.3 - the same fail-closed shape as the v1 coverage gate).
    cohort_results = ([evaluate_cohort(spec, label, per_cohort[label])
                       for label in cohort_labels] if coverage_complete else [])
    survivors = [c for c in cohort_results if c["outcome"] == "SURVIVOR"]
    disposition = family_disposition(survivors, coverage_complete)

    # deterministic selector: a shuffled copy of the same rows must elect the same cell
    rng = random.Random(20260918)
    selector_stable = True
    for c in cohort_results:
        if c["no_winner_reason"] is not None:
            continue
        shuffled = list(per_cohort[c["cohort"]]["historical"])
        rng.shuffle(shuffled)
        again, _ = select_cohort_winner(shuffled, spec)
        wcell = tuple([case_tuple(c["winner"])] + [c["winner"][a] for a in DCA_AXES])
        if again is None or cell_key(again) != wcell:
            selector_stable = False
            break

    # ---- per-cohort winner diagnostics (per-year, per-episode stats, registered counters)
    diag_by_cohort = {}
    robustness_diagnostics = {
        "non_gating": True,
        "note": "per-calendar-year net PnL, per-episode statistics and the registered signal "
                "diagnostics of the elected winner cell, plus the structural counters of its "
                "FULL-window run (isolated from the grid counters); descriptive only - the four "
                "family-level readers are the only gated evidence",
        "registered_item": "sub_period_stability_and_winner_signal_diagnostics"}
    if coverage_complete:
        for c in cohort_results:
            if c["winner"] is None:
                continue
            cohort, series = diag_inputs[c["cohort"]]
            slip0 = spec["costs"]["baseline_slippage_ticks"]
            full_slice = cohort.slice(spec["data"]["start"], spec["data"]["end"])
            dca = {a: c["winner"][a] for a in DCA_AXES}
            dca["base_quote"] = spec["dca_domain"]["base_quote"]
            p_win = params_of(c["winner"])
            before = counters_snapshot()
            m_win = simulate(cohort, p_win, rail_for(dca), full_slice, {}, slip0,
                             "winner_diag", series, diag=True, count_layers=False)
            win_delta = counters_delta(before)
            row = record(cohort, p_win, dca, "full", m_win)
            grid_row = same_cell(per_cohort[c["cohort"]]["full"],
                                 tuple([case_tuple(c["winner"])] + [c["winner"][a] for a in DCA_AXES]))
            diag_by_cohort[c["cohort"]] = {"winner_full": m_win, "row": row,
                                           "counter_delta": win_delta,
                                           "episode_stats": m_win["diagnostics"].get(
                                               "episode_stats", {})}
            robustness_diagnostics.setdefault("cohorts", {})[c["cohort"]] = {
                "winner_cell": c["winner"],
                "pnl_by_year": m_win["diagnostics"]["pnl_by_year"],
                "winner_episode_stats": m_win["diagnostics"]["episode_stats"],
                "winner_signal_diagnostics": m_win["diagnostics"]["signal_diagnostics"],
                "winner_full_window_counters": win_delta,
                "winner_diagnostics_match_full_row": all(
                    abs(float(row[k]) - float(grid_row[k])) < 1e-6
                    for k in ROW_FIELDS
                    if isinstance(grid_row[k], (int, float)) and not isinstance(grid_row[k], bool)
                    and isinstance(row[k], (int, float))),
                "daily_series_days": m_win["diagnostics"]["daily_equity_days"]}

    rows_by_cohort = {label: per_cohort[label] for label in cohort_labels}
    empty_reader = {"evaluated": False, "hit": False,
                    "landing": "family-level: a hit must NEVER be recorded as PASS"}
    coverage_calibration = (coverage_calibration_check(spec, cohort_results, diag_inputs)
                            if coverage_complete
                            else dict(empty_reader,
                                      registered_item="coverage-calibration-stability"))
    horserace = (conformal_vs_realized_vol_horserace(spec, cohort_results, diag_inputs,
                                                     rows_by_cohort)
                 if coverage_complete
                 else dict(empty_reader,
                           registered_item="conformal-vs-realized-volatility-horserace"))
    dial_placebo = (dial_placebo_check(spec, cohort_results, diag_inputs, rows_by_cohort)
                    if coverage_complete
                    else dict(empty_reader, registered_item="downside-miscoverage-dial-placebo"))
    cap_ablation = (leverage_cap_ablation_check(spec, cohort_results, diag_inputs)
                    if coverage_complete
                    else dict(empty_reader, registered_item="leverage-cap-ablation"))
    flags = {"coverage_calibration_stability": bool(coverage_calibration.get("hit")),
             "conformal_vs_realized_volatility_horserace": bool(horserace.get("hit")),
             "downside_miscoverage_dial_placebo": bool(dial_placebo.get("hit")),
             "leverage_cap_ablation": bool(cap_ablation.get("hit"))}

    # registered landing of the four family-level readers: none of them may be recorded as
    # PASS; the band mapping itself is unchanged (v1.4.0).
    final_verdict = disposition["verdict_recommendation"]
    final_claimable = disposition["performance_claimable_recommendation"]
    if final_verdict == "PASS" and any(flags.values()):
        final_verdict = "DEFERRED"
        final_claimable = False

    med = lambda rows, key: st.median([r[key] for r in rows if r.get(key) is not None] or [0.0])
    descriptive = {
        "non_gating": True,
        "note": "every cohort is judged independently (contract 7.3); the cross-cohort numbers "
                "below are descriptive diagnostics and are never a gate (contract 7.2)",
        "cohort_count": len(cohort_labels),
        "median_full_net_pnl": med(full, "net_pnl"),
        "median_oos_net_pnl": med(grid_rows["oos"], "net_pnl"),
        "median_historical_net_pnl": med(grid_rows["historical"], "net_pnl"),
        "positive_case_share_historical": (sum(1 for r in grid_rows["historical"] if r["net_pnl"] > 0)
                                          / float(len(grid_rows["historical"]))) if grid_rows["historical"] else 0.0,
        "positive_case_share_full": (sum(1 for r in full if r["net_pnl"] > 0) / float(len(full))) if full else 0.0,
        "cross_instrument_direction_consistency": [
            {"cohort": label, "winner_case": c.get("winner_case_label"),
             "outcome": c["outcome"]}
            for label, c in zip(cohort_labels, cohort_results)],
    }
    stress_summary = {s: {"median_net_pnl": med(grid_rows[s], "net_pnl"),
                          "median_sharpe": med(grid_rows[s], "sharpe"),
                          "cases": len(grid_rows[s]), "non_gating": True} for s, _ in STRESS}
    for extra in ("cost_attrition_40bps", "no_funding", "no_funding_full"):
        stress_summary[extra] = {"median_net_pnl": med(grid_rows[extra], "net_pnl"),
                                 "median_sharpe": med(grid_rows[extra], "sharpe"),
                                 "cases": len(grid_rows[extra]), "non_gating": True}

    keys = ("net_pnl", "fees", "funding", "gross_pnl", "ending_equity", "sharpe",
            "max_dd_pct", "max_dd_usdt", "max_effective_leverage", "capital_utilization",
            "cagr", "total_return_pct", "episodes", "fills", "turnover_usdt")
    mid = lambda rows, key: med(rows, key)
    fee_2x_effective = (mid(grid_rows["fee_2x"], "fees") > mid(full, "fees")
                        and mid(grid_rows["fee_2x"], "net_pnl") < mid(full, "net_pnl"))
    funding_2x_effective = (abs(mid(grid_rows["funding_2x"], "funding"))
                            > abs(mid(full, "funding"))
                            and mid(grid_rows["funding_2x"], "net_pnl") != mid(full, "net_pnl"))
    cost_attrition_effective = (mid(grid_rows["cost_attrition_40bps"], "fees") > mid(full, "fees")
                                and mid(grid_rows["cost_attrition_40bps"], "net_pnl")
                                < mid(full, "net_pnl"))
    entry_delay_effective = (mid(grid_rows["entry_delay_1_bar"], "net_pnl") != mid(full, "net_pnl")
                             or mid(grid_rows["entry_delay_1_bar"], "fills") != mid(full, "fills")
                             or mid(grid_rows["entry_delay_1_bar"], "episodes")
                             != mid(full, "episodes"))
    slippage_effective = (mid(grid_rows["slippage_2ticks"], "net_pnl") != mid(full, "net_pnl")
                          or mid(grid_rows["slippage_2ticks"], "fees") != mid(full, "fees"))
    no_funding_free = all(r["funding"] == 0.0 for r in grid_rows["no_funding"])
    no_funding_track_free = (no_funding_free
                             and all(r["funding"] == 0.0 for r in grid_rows["no_funding_full"]))
    full_events = sum(r["windows_seen"] for k in FULL_WINDOW_GRID_KINDS
                      for r in grid_rows.get(k, []))
    full_episodes = sum(r["episodes"] for k in FULL_WINDOW_GRID_KINDS
                        for r in grid_rows.get(k, []))
    assertions = {
        "episodes_partition": all(r["episodes"] == r["tp_hits"] + r["stop_hits"] + r["time_exits"]
                                  + r["open_at_end"] + r["margin_calls"] for r in full),
        "pnl_decomposition": all(pnl_decomposition_ok(r) for r in full),
        "coverage_complete": coverage_complete,
        "cohort_count_matches_registered": len(cohorts) == spec["expected"]["cohorts"],
        "strategy_grid_is_registered_product": strategy_cells == strategy_product,
        "dca_grid_is_registered_product": dca_cells == dca_product,
        "base_combinations_per_cohort_per_grid": cells_per_cohort_ok,
        "expected_case_evaluations": sum(coverage.values()) == spec["expected"]["expected_case_evaluations"],
        "layer0_equals_episodes": (layers[0] == full_episodes and layers[0] > 0),
        "layer_histogram_nonempty": sum(layers) > 0,
        "no_entry_after_exhaustion": all(r["min_entry_equity"] > 0.0 for r in full),
        "ending_equity_floor": all(r["ending_equity"] > -1.5 * START_EQUITY for r in full),
        "selector_deterministic": selector_stable,
        "selector_historical_only": True,
        "episodes_never_exceed_windows_seen": all(r["episodes"] <= r["windows_seen"]
                                                  for r in full),
        # executable no-look-ahead / window guards (per-grid module counters)
        "bar_grid_is_contiguous": counters_total("bar_grid_not_contiguous") == 0,
        "no_unregistered_strategy_case": counters_total("case_not_registered") == 0,
        "funding_bar_never_out_of_hold": counters_total("funding_bar_out_of_hold") == 0,
        "no_funding_grid_is_cost_free": no_funding_free,
        "no_funding_track_is_cost_free": no_funding_track_free,
        "fee_2x_track_is_not_a_noop": fee_2x_effective,
        "funding_2x_track_is_not_a_noop": funding_2x_effective,
        "cost_attrition_track_is_not_a_noop": cost_attrition_effective,
        "entry_delay_track_is_not_a_noop": entry_delay_effective,
        "slippage_track_is_not_a_noop": slippage_effective,
        # Strategy L registered guards (names frozen in the round-spec)
        "entry_bar_is_the_next_bar_after_the_signal":
            counters_total("entry_bar_not_the_next_bar_after_the_signal") == 0,
        "no_entry_without_a_defined_signal": counters_total("entry_before_a_defined_signal") == 0,
        "no_signal_before_the_registered_warmup":
            counters_total("signal_before_the_registered_warmup") == 0,
        "target_state_follows_the_registered_materiality_rule":
            counters_total("state_not_derived_from_the_registered_rule") == 0,
        "no_ridge_fit_failure": counters_total("ridge_fit_failed") == 0,
        "panel_series_share_one_bar_grid": counters_total("panel_grid_mismatch") == 0,
        "signal_inputs_are_prefix_invariant": counters_total("causality_probe_mismatch") == 0,
        "no_reentry_without_a_new_position_event": (
            counters_sum("entry_bar_not_the_next_bar_after_the_signal", FULL_WINDOW_GRID_KINDS) == 0
            and counters_sum("reentry_skipped_in_position", FULL_WINDOW_GRID_KINDS)
            + full_episodes + counters_sum("entry_refused_exhausted", FULL_WINDOW_GRID_KINDS)
            == full_events),
        "daily_series_is_slice_scoped": all(
            {r["days"] for r in per_cohort[label][k]} == {slice_days[label][k]}
            for label in cohort_labels for k in ("historical", "oos", "full")
        ) if coverage_complete else False,
    }
    return {
        "family_id": spec["family_id"], "round_id": spec["round_id"], "run_id": spec["run_id"],
        "engine_version": ENGINE_VERSION, "engine_semantics": ENGINE_SEMANTICS,
        "selector_version": SELECTOR_VERSION, "disposition_version": DISPOSITION_VERSION,
        "contract_semantics_version": CONTRACT_SEMANTICS_VERSION,
        "registered_domains": {
            "strategy": {"window_case": [list(t) for t in expected_axes["window_case"]],
                         "case_names": list(CASE_NAMES),
                         "scale_arms": list(SCALE_ARMS),
                         "configurations": list(CONFIGS),
                         "signal_constants": {
                             "ridge_lambda": RIDGE_LAMBDA, "refit_every": REFIT_EVERY,
                             "first_prediction_bar": FIRST_PREDICTION_BAR,
                             "horizons": list(HORIZONS), "target_h": TARGET_H,
                             "momentum_windows": list(MOM_WINDOWS), "ewma_span": EWMA_SPAN,
                             "conformal_window": W_CONF, "shrink_lambda": SHRINK_LAMBDA,
                             "z_alpha": Z_ALPHA, "nominal_alpha": NOMINAL_ALPHA,
                             "kappa": KAPPA, "winsor": WINSOR, "gross_cap": GROSS_CAP,
                             "dial_window": DD_WINDOW, "dial_beta": DD_BETA,
                             "dial_floor": DD_FLOOR, "dial_gate": DIAL_GATE,
                             "materiality_tilt": MATERIALITY_TILT,
                             "frozen_train_scores": FROZEN_TRAIN_SCORES,
                             "quantile_rule": QUANTILE_RULE,
                             "signal_warmup_bars": SIGNAL_WARMUP_BARS,
                             "panel_size": PANEL_SIZE}},
            "dca": {a: expected_axes[a] for a in DCA_AXES},
            "dca_base_quote": spec["dca_domain"]["base_quote"]},
        "coverage": coverage, "coverage_required": need, "coverage_complete": coverage_complete,
        "cohorts": cohort_labels, "cohort_count": len(cohorts),
        "slice_days": slice_days,
        "case_evaluations_per_cohort_per_grid": spec["expected"]["base_combinations_per_cohort"],
        "case_evaluations_per_grid": spec["expected"]["case_evaluations_per_grid"],
        "case_evaluations_total": sum(coverage.values()),
        "expected_case_evaluations": spec["expected"]["expected_case_evaluations"],
        "cohort_grid_kinds": list(COHORT_GRID_KINDS),
        "cohort_results": cohort_results,
        "cohort_survivors": [c["cohort"] for c in survivors],
        "cohort_survivor_count": len(survivors),
        "cohort_outcome_counts": {"SURVIVOR": len(survivors),
                                  "CULLED": len(cohort_results) - len(survivors)},
        "disposition": disposition["disposition"],
        "verdict_recommendation": disposition["verdict_recommendation"],
        "performance_claimable_recommendation": disposition["performance_claimable_recommendation"],
        "disposition_mapping_version": disposition["mapping_version"],
        "registered_family_level_falsification_flags": flags,
        "verdict_recommendation_final": final_verdict,
        "performance_claimable_recommendation_final": final_claimable,
        "survivor_evidence": survivors,
        "descriptive_diagnostics": descriptive,
        "descriptive_medians_all_base_cases": {k: mid(full, k) for k in keys},
        "stress_summary": stress_summary,
        "dca_layer_histogram": {"level_%02d" % k: layers[k] for k in range(12)},
        "coverage_calibration_stability": coverage_calibration,
        "conformal_vs_realized_volatility_horserace": horserace,
        "downside_miscoverage_dial_placebo": dial_placebo,
        "leverage_cap_ablation": cap_ablation,
        "signal_input_layer": ({label: diag_inputs[label][0].inputs_report
                                for label in cohort_labels} if coverage_complete else {}),
        "signal_layer": ({label: signal_report_of(diag_inputs[label][0])
                          for label in cohort_labels} if coverage_complete else {}),
        "robustness_diagnostics": robustness_diagnostics,
        "assertions": assertions,
        "structural_counters": {k: dict(v) for k, v in sorted(COUNTERS.items())},
        "structural_counter_tolerance_ms": MS_JITTER_TOLERANCE_MS,
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) != 2:
        sys.stderr.write("usage: 120_conformal_kelly_run.py <run-spec.json>\n")
        return 2
    spec_path = os.path.abspath(sys.argv[1])
    with open(spec_path) as fh:
        spec = json.load(fh)
    attempt_dir = os.path.dirname(spec_path)
    os.makedirs(os.path.join(attempt_dir, "artifacts"), exist_ok=True)
    os.makedirs(os.path.join(attempt_dir, "logs"), exist_ok=True)
    run_log = log
    started = time.time()
    try:
        self_sha = sha256_file(os.path.abspath(__file__))
        if self_sha != spec["script"]["sha256"]:
            raise SystemExit("script sha256 mismatch: running %s spec %s"
                             % (self_sha, spec["script"]["sha256"]))
        for key in ("parameter_domain", "dca_domain", "expected", "selector_version",
                    "disposition_version", "gates", "costs", "falsification"):
            if key not in spec:
                raise SystemExit("run-spec is not a Strategy L spec: missing %r (contract 7.2/7.3)"
                                 % key)
        if spec["selector_version"] != SELECTOR_VERSION or spec["disposition_version"] != DISPOSITION_VERSION:
            raise SystemExit("run-spec selector/disposition version %r/%r != engine %r/%r"
                             % (spec["selector_version"], spec["disposition_version"],
                                SELECTOR_VERSION, DISPOSITION_VERSION))
        # fail closed on a declared strategy domain that is not the registered case set
        declared_cases = [case_tuple(g) for g in spec["parameter_domain"]["grid_cases"]]
        if declared_cases != list(CASE_ORDER):
            raise SystemExit("run-spec grid_cases %r != engine registered case order %r"
                             % (declared_cases, CASE_ORDER))
        atomic_write_json(os.path.join(attempt_dir, "state.json"), {
            "schema_version": 1, "family_id": spec["family_id"], "round_id": spec["round_id"],
            "run_id": spec["run_id"], "stage": "RUNNING_QLIB",
            "updated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "note": "started"})

        inst_path = os.path.join(RAW_ROOT, "instruments", "usdm-perp-instruments.json")
        with open(inst_path) as fh:
            inst_doc = json.load(fh)
        instruments = {}
        for item in inst_doc["instruments"]:
            f = item["fields"]
            instruments[f["raw_symbol"]] = {
                "price_increment": float(f["price_increment"]),
                "maker_fee": float(f["maker_fee"]), "taker_fee": float(f["taker_fee"]),
                "margin_init": float(f["margin_init"]), "margin_maint": float(f["margin_maint"]),
                "min_notional": f["min_notional"], "id": f["id"]}
        manifest = {"instruments_sha256": sha256_file(inst_path), "raw_files": {}}
        for symbol in spec["data"]["symbols"]:
            for tf in spec["data"]["timeframes"]:
                for src in raw_months(symbol, tf["raw_interval"], spec["data"]["start"],
                                      spec["data"]["end"]):
                    manifest["raw_files"][os.path.relpath(src, "/data/raw")] = sha256_file(src)
            fp = os.path.join(RAW_ROOT, "funding", symbol, "%s-funding.jsonl.gz" % symbol)
            manifest["raw_files"][os.path.relpath(fp, "/data/raw")] = sha256_file(fp)
        atomic_write_json(os.path.join(attempt_dir, "artifacts", "input_manifest.json"), manifest)
        run_log("input manifest: %d raw files hashed" % len(manifest["raw_files"]))

        bins = build_bins(spec, attempt_dir, run_log)

        grid_rows = {}
        diag_inputs = {}
        slice_days = {}
        total = len(spec["data"]["symbols"]) * len(spec["data"]["timeframes"])
        done = 0
        funding_report = {}
        for symbol in spec["data"]["symbols"]:
            for tf in spec["data"]["timeframes"]:
                cohort = Cohort(symbol, tf, spec["data"]["start"], spec["data"]["end"], spec)
                meta = instruments[symbol]
                cohort.price_increment = meta["price_increment"]
                cohort.taker_fee = meta["taker_fee"]
                cohort.leverage = 1.0 / meta["margin_init"]
                cohort.margin_maint = meta["margin_maint"]
                ft, fr, counts = load_funding_series(symbol, spec["data"]["start"],
                                                     spec["data"]["end"])
                series = build_funding_index(cohort, ft, fr, counts, "full")
                label = "%s/%s" % (symbol, tf["raw_interval"])
                diag_inputs[label] = (cohort, series)
                slice_days[label] = {}
                for kind, (a, b) in (("historical", (spec["data"]["historical_start"],
                                                     spec["data"]["historical_end"])),
                                     ("oos", (spec["data"]["oos_start"],
                                              spec["data"]["oos_end"])),
                                     ("full", (spec["data"]["start"], spec["data"]["end"]))):
                    i0, i1 = cohort.slice(a, b)
                    slice_days[label][kind] = int(len(np.unique(
                        cohort.open_time_ms[i0:i1] // MS_PER_DAY)))
                funding_report[label] = {
                    "observations": int(len(ft)),
                    "first": iso(int(ft[0])) if len(ft) else None,
                    "last": iso(int(ft[-1])) if len(ft) else None,
                    "truth_status_counts": counts,
                    "registered_windows": spec["data"]["funding_truth_status_windows"],
                    "settlement_hours_utc": sorted(set(
                        time.strftime("%H:%M", time.gmtime(int(t) / 1000.0)) for t in ft)),
                    "settlement_exposure_note": spec["costs"]["funding_exposure_rule"]}
                probe = causality_probe(cohort, cohort.inputs)
                cohort.inputs_report = {
                    "bars": cohort.n, "columns": len(cohort.inputs["names"]),
                    "warmup_bars": SIGNAL_WARMUP_BARS,
                    "causality_probe": probe,
                    "note": "the registered daily simple-return input; the expanding-window "
                            "ridge forecaster, the conformal ensemble and the scale layer are "
                            "built per case on the shared four-instrument panel and are "
                            "reported in signal_layer.json"}
                run_log("cohort %s bars=%d inputs=%d probe=%d probes/%d mismatch funding_obs=%d "
                        "modeled=%d official=%d"
                        % (label, cohort.n, len(cohort.inputs["names"]),
                           probe["bars_probed"], probe["mismatches"], len(ft),
                           counts["modeled_funding"], counts["official"]))

        # the registered panel: exactly the four cohorts above, built once per case (the lead-lag
        # tensors are cached across cases, so the expensive DTW work happens once per method x
        # lookback).  Building every case before the grid also fails fast on a signal defect.
        PANEL_COHORTS.clear()
        PANEL_COHORTS.update({c.symbol: c for c in [diag_inputs[k][0] for k in sorted(diag_inputs)]})
        panel_report = {}
        t_panel = time.time()
        for case in CASE_ORDER:
            p = panel_for(case)
            panel_report[p.case_label] = {
                "bars": p.n, "markets": list(p.markets), "arm": p.arm, "config": p.cfg,
                "warmup_bars": p.diag["warmup_bars"], "defined_bars": p.diag["defined_bars"],
                "gross_cap_binding_rate": p.diag["gross_cap_binding_rate"],
                "gross_pre_cap_max": p.diag["gross_pre_cap_max"]}
        run_log("panels built: %d cases in %.1fs" % (len(CASE_ORDER), time.time() - t_panel))
        for label in sorted(diag_inputs):
            for k, v in run_cohort(spec, diag_inputs[label][0], run_log,
                                   diag_inputs[label][1]).items():
                grid_rows.setdefault(k, []).extend(v)
            done += 1
            atomic_write_json(os.path.join(attempt_dir, "artifacts", "progress.json"),
                              {"cohorts_done": done, "cohorts_total": total,
                               "updated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                               time.gmtime())})
        atomic_write_json(os.path.join(attempt_dir, "artifacts", "funding_series.json"),
                          funding_report)

        for kind, rows in grid_rows.items():
            path = os.path.join(attempt_dir, "artifacts", "grid_%s.csv" % kind)
            with open(path, "w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=list(ROW_FIELDS))
                w.writeheader()
                w.writerows(rows)
            run_log("wrote %s (%d rows)" % (os.path.basename(path), len(rows)))

        layers = LAYER_TOTALS.setdefault("full", [0] * 12)
        summary = summarize(spec, grid_rows, layers, diag_inputs, slice_days)
        summary["runtime_seconds"] = int(time.time() - started)
        summary["bins_build"] = {k: bins[k] for k in
                                 ("build_wall_seconds", "qlib_dir_bytes", "qlib_version")}
        summary["instrument_metadata"] = {s: instruments[s] for s in spec["data"]["symbols"]}
        summary["data_readback"] = bins["readback"]
        summary["funding_series"] = funding_report
        summary["panel_build"] = panel_report
        summary["generated_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        atomic_write_json(os.path.join(attempt_dir, "result.json"), summary)
        atomic_write_json(os.path.join(attempt_dir, "artifacts", "dca_layer_histogram.json"),
                          summary["dca_layer_histogram"])
        atomic_write_json(os.path.join(attempt_dir, "artifacts", "assertions.json"),
                          summary["assertions"])
        atomic_write_json(os.path.join(attempt_dir, "artifacts", "cohort_results.json"),
                          summary["cohort_results"])
        atomic_write_json(os.path.join(attempt_dir, "artifacts", "cohort_survivors.json"),
                          summary["survivor_evidence"])
        atomic_write_json(os.path.join(attempt_dir, "artifacts", "family_falsification.json"),
                          {"coverage_calibration_stability":
                               summary["coverage_calibration_stability"],
                           "conformal_vs_realized_volatility_horserace":
                               summary["conformal_vs_realized_volatility_horserace"],
                           "downside_miscoverage_dial_placebo":
                               summary["downside_miscoverage_dial_placebo"],
                           "leverage_cap_ablation": summary["leverage_cap_ablation"],
                           "flags": summary["registered_family_level_falsification_flags"]})
        atomic_write_json(os.path.join(attempt_dir, "artifacts", "signal_input_layer.json"),
                          summary["signal_input_layer"])
        atomic_write_json(os.path.join(attempt_dir, "artifacts", "signal_layer.json"),
                          summary["signal_layer"])
        atomic_write_json(os.path.join(attempt_dir, "artifacts", "robustness_diagnostics.json"),
                          summary["robustness_diagnostics"])
        run_log("disposition=%s verdict_recommendation=%s final=%s survivors=%d/%d cases=%d"
                % (summary["disposition"], summary["verdict_recommendation"],
                   summary["verdict_recommendation_final"], summary["cohort_survivor_count"],
                   summary["cohort_count"], summary["case_evaluations_total"]))
        atomic_write_json(os.path.join(attempt_dir, "state.json"), {
            "schema_version": 1, "family_id": spec["family_id"], "round_id": spec["round_id"],
            "run_id": spec["run_id"], "stage": "ARTIFACT_READY",
            "updated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "note": "all phases complete"})
        print(json.dumps(_sanitize(summary), indent=2, ensure_ascii=False, allow_nan=False))
    except Exception as exc:  # noqa: BLE001 - a failure must still be visible
        import traceback
        traceback.print_exc()
        atomic_write_json(os.path.join(attempt_dir, "state.json"), {
            "schema_version": 1, "family_id": spec.get("family_id"),
            "round_id": spec.get("round_id"), "run_id": spec.get("run_id"),
            "stage": "FAILED_SCRIPT",
            "updated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "note": "%s: %s" % (type(exc).__name__, exc)})
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
