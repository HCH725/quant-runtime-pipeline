#!/usr/bin/env python3
"""Strategy K — Compact-RIEnet: Neural Network-Driven Volatility Drag Mitigation and Liquidation
Delay under Aggressive Portfolio Leverage, executed on the registered LOCAL eligible universe
(BTCUSDT / ETHUSDT / BNBUSDT / SOLUSDT USD-M perpetuals, 1 d) on Qlib.

Entry point for ONE pre-registered attempt.  Reads the immutable run-spec from /results (the only
source of parameters), builds the Qlib .bin store from the READ-ONLY canonical raw store into
/qlib/work, then runs the pre-registered full-backtest contract
(QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md sections 7.2 / 7.3):

    4 cohorts (BTCUSDT / ETHUSDT / BNBUSDT / SOLUSDT, raw interval 1d)
    x STRATEGY domain (2 registered axes: the spectral cleaning arm {raw, shrink_const, mp_clip}
      x the lookback dt_in in {250, 500, 750, 1200} bars = 12 cases)
    x DCA domain (4 axes = 48 configs)
    x 10 registered phase grids (historical / oos / full / fee_2x / funding_2x /
      entry_delay_1_bar / slippage_2ticks / no_funding / no_funding_full /
      cost_attrition_40bps)
    = 4 x 12 x 48 x 10 = 23,040 expected case evaluations.

SOURCE CLAIM UNDER TEST (the record's own): a neural-network-assembled inverse covariance matrix
(lag-transformed returns -> cleaned correlation eigen-spectrum -> inverse covariance -> long-only
global-minimum-variance allocation) compresses realised portfolio variance so that a leveraged
book suffers materially less quadratic volatility drag and delays the first forced liquidation.
The record's own portability paragraph names leveraged crypto perpetual baskets as an `adapted` /
`unproven` extension; this round executes that extension on the four canonical local instruments,
with the source market (US NYSE/NASDAQ equity panel, 1990-2024) recorded as provenance and
external-validity context only (system-owned lifecycle footer, contract section 14.4).

THE REGISTERED SIGNAL (record-faithful; every value the record leaves open is a project
pre-registered constant frozen before the first run - see the module docstring of the round-spec
and `signal_semantics.registered_constants`):

  * Module 1 - parametric lag transformation on the lookback window dt_in:
        alpha_l = theta1 * l^-theta2,  beta_l = theta3 - theta4 * exp(-theta5 * l),
        r~_{l,i} = (alpha_l / beta_l) * tanh(beta_l * r_{l,i}),  l = 1 (most recent) .. dt_in
  * Module 2 - correlation cleaning of the eigen-spectrum of the sample correlation matrix C of
        the lag-transformed window (C = Q Lambda Q^T, lambda_1 >= ... >= lambda_n).  The record's
        trained BiGRU is NOT published (no weights, no training artifacts), so the registered
        operator is the parameter-free reading the record itself names: the identity/raw sample
        spectrum, constant scalar shrinkage, and Marchenko-Pastur noise-floor clipping.  The
        trained arm itself is NOT evaluated and is carried as a `not_executed` reader.
  * Module 3 - marginal volatility rescaling sigma~_i (empirical std of the transformed window),
        registered as the identity reading of the record's per-asset MLP: sigma_{i,NN}^-1 = 1/sigma~_i
  * Module 4 - Sigma_NN = D Q Lambda_NN Q^T D with D = diag(sigma~), and the record's practical
        allocation solved exactly:  min_w w^T Sigma_NN w  s.t.  w >= 0, sum w = 1
  * target state - LONG asset i exactly while its registered long-only weight is at least an
        equal share of the panel (w_i >= 1/n): the pipeline's rail is event-driven and one
        instrument at a time, so the record's allocation vector is read as the registered tilt
        rule; the alternative reading (any strictly positive weight -> invested, which is true on
        almost every bar) is disclosed and NOT evaluated.  The registered rail owns the position
        size; only the direction and the exit condition come from the record.

ENTRY / EXIT (the record's own position semantics wrapped in the card-mandated DCA rail):
an ENTRY event is a bar whose target state CHANGES to LONG (a fresh allocation event); the entry
executes at the NEXT bar's open (the record's next-day execution), optionally moved by the
registered `entry_delay_1_bar` execution stress.  An episode ends when (1) the rail take profit at
running_average_cost x (1 + breakeven_tp_pct) fills reduce-only, (2) the resting invalidation at
running_average_cost x (1 - invalidation_pct) fills, (3) the record's own exit condition holds at a
bar's close - the registered long-only weight is no longer at least an equal share - and every
layer is flattened at the NEXT bar's open, or (4) the slice ends (reduce-only flatten of every
layer).  tranche #1 is the initial entry; adverse-price scale-ins follow the registered ladder
(level_k price = initial_entry_price x (1 - spacing_pct x k), k = 1..10, i.e. at most 11 routine
active levels of the 12-tranche rail).

DCA is executed as real order/fill accounting (an episode state machine over the bars); nothing is
estimated after the fact.  Every legal (strategy params x DCA config) cell is evaluated on every
registered phase grid, and the family gate is the **cohort-level survivor** rule of contract
section 7.3: one deterministic historical-only winner per cohort, then OOS / full / robustness /
parameter-neighbourhood evidence for that same winner.  This family has four cohorts, so the
cohort survivor count is 0..4 and EVERY survivor advances (v1.4.0 band mapping).

The record's five-item falsification battery is registered as five family-level readers (the BiGRU
spectral-denoiser ablation, which IS evaluated as the registered cleaning-arm comparison; the
Almgren-Chriss market-impact haircut, the cross-asset transferability test and the intraday
tick-level margin audit, which cannot be executed on the local store and are recorded as
`not_executed` with their measured reason; and the record's rejection/freeze rule as a disclosed
local analogue).  A reader hit can only move a PASS to DEFERRED and is never PASS-bearing.

Writes only:
  * /qlib/work/**      (rebuildable derived/cache area, INV-5)
  * <attempt_dir>/**   (result.json, artifacts/, logs/, state.json)
Never writes /data/raw (read-only mount).

usage: 110_strategy_k_run.py <run-spec.json>
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
WORK_ROOT = "/qlib/work/strategyK-v1"
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
CLEAN_ARMS = ("raw", "shrink_const", "mp_clip")   # searched axis 1: the spectral cleaning arm
CLEAN_LABELS = ("raw", "shrink", "mp")
LOOKBACKS = (250, 500, 750, 1200)                 # searched axis 2: dt_in, the record's own range
LOOKBACK_LABELS = tuple(str(d) for d in LOOKBACKS)
CASE_FIELDS = ("cl_raw", "cl_shrink", "cl_mp", "lb_250", "lb_500", "lb_750", "lb_1200")
CASE_ORDER = tuple(
    tuple([1 if a == i else 0 for a in range(len(CLEAN_ARMS))]
          + [1 if b == j else 0 for b in range(len(LOOKBACKS))])
    for j in range(len(LOOKBACKS)) for i in range(len(CLEAN_ARMS)))
CASE_NAMES = tuple("%s__lb%s" % (CLEAN_LABELS[i], LOOKBACK_LABELS[j])
                   for j in range(len(LOOKBACKS)) for i in range(len(CLEAN_ARMS)))
PANEL_SIZE = 4                 # the registered local eligible universe
PANEL_COHORTS = {}             # filled by main(): the four cohorts the panel is built from
# The registered signal floor: a case's first defined bar is its own lookback (no value of the
# case reads a bar before it), so the panel-wide floor is the largest registered lookback.
SIGNAL_WARMUP_BARS = int(max(LOOKBACKS))
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
# composite (cleaning arm, lookback) axis; its registered order is CASE_ORDER.
AXES = ("window_case", "spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct")
DCA_AXES = ("spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct")
SELECTOR_VERSION = "cohort-selector-v1"
DISPOSITION_VERSION = "cohort-disposition-v1"
CONTRACT_SEMANTICS_VERSION = "v1.4.0"
ENGINE_VERSION = "k-v1-engine-1.0.0"
ENGINE_SEMANTICS = ("the record's Compact-RIEnet signal chain (the five-parameter hyperbolic "
                    "lag transformation, the spectral cleaning of the sample correlation matrix "
                    "of the transformed window, the marginal-volatility rescaling and the exact "
                    "long-only global-minimum-variance allocation) turned into target-state "
                    "change entry events at the next bar's open, the record's allocation exit at "
                    "the next bar's open, plus the registered DCA rail (see module docstring)")
WINNER_METRIC_KEYS = ("net_pnl", "sharpe", "episodes", "ending_equity", "fees", "funding",
                      "max_dd_usdt", "max_dd_pct", "max_effective_leverage",
                      "capital_utilization", "annualized_return", "cagr")
# csv row field order (kept identical for every phase grid)
ROW_FIELDS = ("symbol", "timeframe", "window_kind", "window_case", "case_name",
              "cl_raw", "cl_shrink", "cl_mp",
              "lb_250", "lb_500", "lb_750", "lb_1200",
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
                 "causality_probe_mismatch", "allocation_degenerate",
                 "allocation_weight_sum_violation", "state_not_derived_from_the_registered_rule",
                 "signal_before_the_registered_warmup", "panel_grid_mismatch",
                 "cleaning_undefined_on_a_defined_bar", "nonfinite_weight")
COUNTERS = {}
# ---- registered family-level falsification reader constants (frozen before the first run)
FULL_WINDOW_NET_PNL_TOL = 1e-9   # the ablation reader's "at least as much" comparison tolerance
FREEZE_MARGIN_CALLS = 2          # the record's "> 2 margin liquidations" freeze leg (local analogue)
FREEZE_OOS_SHARPE = 0.40         # the record's "OOS Sharpe below 0.40" freeze leg (disclosed
                                 # local analogue: the local OOS slice is 11.5 months, the record's
                                 # window is a trailing 36-month rolling window at leverage 2.0)


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


# ---------------------------------------------------------------------------
# case axes (registered one-hot encoding of the two strategy axes)
# ---------------------------------------------------------------------------

def case_tuple(p):
    return tuple(int(p[f]) for f in CASE_FIELDS)


def case_index(case):
    return CASE_ORDER.index(tuple(case))


def case_name(case):
    return CASE_NAMES[case_index(case)]


def case_clean(case):
    """Registered cleaning-arm index of one case: 0 = raw, 1 = shrink_const, 2 = mp_clip."""
    return list(case).index(1, 0, len(CLEAN_ARMS))


def case_lookback(case):
    """Registered lookback index of one case: 0..3 = the four registered lookbacks."""
    return list(case).index(1, len(CLEAN_ARMS), len(CLEAN_ARMS) + len(LOOKBACKS)) - len(CLEAN_ARMS)


# ---------------------------------------------------------------------------
# Compact-RIEnet science  (record-faithful signal; research-defined operationalization)
# ---------------------------------------------------------------------------
# The record fixes the MECHANISM and the mathematics (module docstring and the round-spec's
# `signal_semantics` block).  What the record does NOT fix - the five theta parameters, the
# cleaning operator, the marginal-volatility operator, the entry state - is fixed below as
# project pre-registered constants, frozen before the first run.
#
# Numerical identities that make this implementation exact rather than iterative:
#   * the long-only minimum-variance allocation is solved by active-set enumeration over every
#     non-empty support, which is exact for a positive-definite covariance matrix (the optimum is
#     the feasible support with the smallest variance); no solver dependency, no iteration.
#   * Sigma_NN is assembled directly from the cleaned eigenvalues as D Q Lambda_NN Q^T D, so the
#     registered assembly Sigma_NN^-1 = D^-1 C_NN^-1 D^-1 is inverted analytically instead of
#     numerically; every registered arm keeps Lambda_NN strictly positive by construction.

#!/usr/bin/env python3


# ---------------------------------------------------------------- registered constants (frozen)
PANEL_SIZE = 4
THETA = (1.0, 0.5, 2.0, 1.0, 0.05)   # theta1..theta5, project pre-registered (record's values unpublished)
SHRINK_DELTA = 0.5                   # constant scalar shrinkage weight toward the mean eigenvalue
CLEAN_ARMS = ("raw", "shrink_const", "mp_clip")
LOOKBACKS = (250, 500, 750, 1200)    # dt_in bars; the record's registered range [250, 1200]
EQUAL_WEIGHT = 1.0 / PANEL_SIZE      # the registered long/flat tilt threshold
SIGNAL_WARMUP_BARS = int(max(LOOKBACKS))
LAG_TOL = 1e-12


def lag_transform(window, lb):
    """Module 1: the parametric lag transformation of one (lb x n) raw-return window.

    Row 0 of `window` is the OLDEST lag (l = lb), the last row is the most recent (l = 1), so the
    alpha/beta vectors are built for l = 1..lb and then reversed onto the window's chronology.
    """
    lags = np.arange(lb, 0, -1, dtype=np.float64)          # oldest first: lb, lb-1, ..., 1
    th1, th2, th3, th4, th5 = THETA
    alpha = th1 * np.power(lags, -th2)
    beta = th3 - th4 * np.exp(-th5 * lags)
    return (alpha / beta)[:, None] * np.tanh(beta[:, None] * window)


def clean_eigenvalues(lam_desc, arm):
    """Module 2 applied to the descending sample eigenvalues -> the cleaned eigenvalues Lambda_NN.

    The record's BiGRU outputs INVERSE eigenvalues; each registered parameter-free arm is stated
    directly as the cleaned eigenvalue it implies (`lambda_{i,NN} = 1 / lambda_{i,NN}^-1`):

      raw          lambda_{i,NN} = lambda_i                       (identity: no cleaning)
      shrink_const lambda_{i,NN} = (1-d) lambda_i + d lambda_bar   (constant scalar shrinkage)
      mp_clip      lambda_{i,NN} = max(lambda_i, lambda_bulk_bar)  (RMT noise-floor clipping;
                   lambda_bulk_bar is the mean of the eigenvalues at or below the
                   Marchenko-Pastur edge lambda_+ = (1 + sqrt(n / dt_in))^2, rescaled to the
                   spectrum actually observed: the edge is used only to SPLIT the spectrum; the
                   floor is the measured bulk mean, so no asymptotic constant enters the value)
    """
    lam = np.asarray(lam_desc, dtype=np.float64)
    if arm == "raw":
        return lam.copy()
    if arm == "shrink_const":
        return (1.0 - SHRINK_DELTA) * lam + SHRINK_DELTA * float(np.mean(lam))
    raise AssertionError("unregistered cleaning arm %r" % (arm,))


def clean_eigenvalues_q(lam_desc, arm, dt_in):
    """`clean_eigenvalues` with the window length supplied (mp_clip needs q = n / dt_in)."""
    lam = np.asarray(lam_desc, dtype=np.float64)
    if arm != "mp_clip":
        return clean_eigenvalues(lam, arm)
    n = lam.size
    edge = (1.0 + np.sqrt(float(n) / float(dt_in))) ** 2
    below = lam <= edge
    floor = float(np.mean(lam[below])) if np.any(below) else float(np.mean(lam))
    return np.maximum(lam, floor)


def long_only_gmv(Sigma):
    """Module 4's practical long-only allocation.

    `min_w w^T Sigma w  s.t. w >= 0, 1^T w = 1` for a symmetric positive-definite Sigma, solved
    exactly by active-set enumeration over every non-empty support (the standard Markowitz
    structure: the optimum is the feasible support whose unconstrained GMV solution is strictly
    positive and whose variance is the smallest among the feasible supports).  Deterministic, no
    iteration, no solver dependency.

    Returns (weights, n_feasible).  A caller that gets an all-zero vector back must treat the bar
    as having no usable allocation (counted, never silently defaulted).
    """
    n = Sigma.shape[0]
    best = None
    feasible = 0
    for mask in range(1, 1 << n):
        idx = [i for i in range(n) if (mask >> i) & 1]
        sub = Sigma[np.ix_(idx, idx)]
        ones = np.ones(len(idx), dtype=np.float64)
        try:
            z = np.linalg.solve(sub, ones)
        except np.linalg.LinAlgError:
            continue
        denom = float(ones @ z)
        if not np.isfinite(denom) or denom <= LAG_TOL:
            continue
        w = z / denom
        if not np.all(np.isfinite(w)) or np.any(w <= 0.0):
            continue
        feasible += 1
        var = float(w @ (sub @ w))
        if best is None or var < best[0]:
            best = (var, idx, w)
    out = np.zeros(n, dtype=np.float64)
    if best is not None:
        for j, i in enumerate(best[1]):
            out[i] = best[2][j]
    return out, feasible


def panel_paths(returns, lb, arm):
    """The full registered signal path of one panel and one registered case.

    `returns` is the (T x n) matrix of CRYPTO daily simple returns; row t is the return realised in
    bar t (close[t] / close[t-1] - 1), so at the close of bar t the lags 1..lb are exactly the
    rows t, t-1, ..., t-lb+1 - the record's `t = 1 is the most recent day`.  Causality: no value
    used at bar t reads a bar after t.

    Returns a dict with
        weights : (T x n) long-only GMV weights (0 before the case's first defined bar)
        states  : (T x n) int64 target state: +1 when w_i >= 1/n (the registered tilt rule), else 0
        events  : (T x n) int64 fresh state-change events (+1 on the first bar of a long run)
        diag    : structural diagnostics (degenerate bars, weight sums, min/max weights)
    """
    T, n = returns.shape
    weights = np.zeros((T, n), dtype=np.float64)
    states = np.zeros((T, n), dtype=np.int64)
    diag = {"bars": int(T), "lookback": int(lb), "arm": arm, "defined_bars": 0,
            "degenerate_bars": 0, "min_weight_sum": None, "max_weight_sum": None,
            "max_weight": 0.0, "min_positive_weight": None, "feasible_supports_min": None}
    if lb >= T:
        diag["note"] = "lookback exceeds the available history: the case is never defined"
        events = np.zeros_like(states)
        return {"weights": weights, "states": states, "events": events, "diag": diag}
    for t in range(lb, T):
        window = returns[t - lb + 1:t + 1, :]
        R = lag_transform(window, lb)
        sd = R.std(axis=0, ddof=1)
        if not np.all(np.isfinite(sd)) or np.any(sd <= 0.0):
            diag["degenerate_bars"] += 1
            continue
        C = np.corrcoef(R.T)
        if not np.all(np.isfinite(C)):
            diag["degenerate_bars"] += 1
            continue
        lam, Q = np.linalg.eigh(C)                  # ascending
        lam_desc = lam[::-1]
        Q = Q[:, ::-1]
        lam_nn = clean_eigenvalues_q(lam_desc, arm, lb)
        Sigma = (sd[:, None] * Q) @ np.diag(lam_nn) @ (Q.T * sd[None, :])
        Sigma = 0.5 * (Sigma + Sigma.T)             # exact symmetry for the solver
        w, feasible = long_only_gmv(Sigma)
        s = float(w.sum())
        diag["min_weight_sum"] = s if diag["min_weight_sum"] is None else min(diag["min_weight_sum"], s)
        diag["max_weight_sum"] = s if diag["max_weight_sum"] is None else max(diag["max_weight_sum"], s)
        diag["feasible_supports_min"] = feasible if diag["feasible_supports_min"] is None else min(
            diag["feasible_supports_min"], feasible)
        if s <= 0.0:
            diag["degenerate_bars"] += 1
            continue
        weights[t] = w
        states[t] = np.where(w >= EQUAL_WEIGHT - LAG_TOL, 1, 0).astype(np.int64)
        diag["defined_bars"] += 1
        diag["max_weight"] = max(diag["max_weight"], float(w.max()))
        pos = w[w > 0.0]
        if pos.size:
            mn = float(pos.min())
            diag["min_positive_weight"] = (mn if diag["min_positive_weight"] is None
                                           else min(diag["min_positive_weight"], mn))
    events = np.zeros_like(states)
    prev = np.zeros(n, dtype=np.int64)
    for t in range(T):
        cur = states[t]
        fresh = (cur != prev) & (cur != 0)
        events[t] = np.where(fresh, cur, 0)
        prev = cur
    return {"weights": weights, "states": states, "events": events, "diag": diag}


# ---------------------------------------------------------------------------
# engine adapters around the registered kernel
# ---------------------------------------------------------------------------

def build_network_inputs(cohort):
    """The registered causal signal input of one cohort: its daily simple-return series.

    A case reads only the returns realised at or before the bar it is formed on (the window is
    `t-lb+1 .. t`), so the panel construction is causal by construction; `causality_probe`
    re-derives sampled bars from a truncated history and must reproduce the panel's own values.
    """
    close = np.asarray(cohort.close, dtype=np.float64)
    ret = np.zeros_like(close)
    ret[1:] = close[1:] / close[:-1] - 1.0
    return {"ret": ret, "close": close,
            "names": ["ret"],
            "floors": {"lookback": SIGNAL_WARMUP_BARS}}


def _registered_probe_case():
    """The case the causality probe re-derives: the longest registered lookback, raw arm."""
    idx = CASE_NAMES.index("raw__lb%d" % SIGNAL_WARMUP_BARS)
    return CASE_ORDER[idx]


def causality_probe(cohort, inputs, sample_bars=48):
    """Prefix-truncation equality on the registered signal of one cohort.

    The panel's own value at a sampled bar must be reproduced when the history handed to the
    kernel is truncated at that bar.  A non-zero mismatch count is a look-ahead defect.
    """
    out = {"bars_probed": 0, "mismatches": 0, "columns": ["weight", "state"], "examples": [],
           "note": "prefix-truncation equality on the registered long-only allocation; a non-zero "
                   "mismatch count is a look-ahead defect"}
    if not PANEL_COHORTS or cohort.symbol not in PANEL_COHORTS:
        out["note"] = "the panel was not registered when the probe ran"
        return out
    labels = sorted(PANEL_COHORTS)
    case = _registered_probe_case()
    panel = panel_for(case)
    i = labels.index(cohort.symbol)
    lb = panel.lookback
    T = panel.n
    if T <= lb + 1:
        return out
    lo, hi = lb, T - 1
    step = max(1, (hi - lo) // max(1, sample_bars))
    for t in range(lo, hi, step):
        w, s = _weights_state_of_window(panel.R[:t + 1, :], lb, panel.arm)
        dw = float(np.max(np.abs(np.asarray(w) - panel.weights[t])))
        ds = int(np.max(np.abs(np.asarray(s) - panel.state[t])))
        out["bars_probed"] += 1
        if dw > 1e-9 or ds != 0:
            out["mismatches"] += 1
            counter("signal", "causality_probe_mismatch")
            if len(out["examples"]) < 5:
                out["examples"].append({"bar": int(t), "weight_dev": dw, "state_dev": ds})
    return out


def _weights_state_of_window(R, lb, arm):
    """One bar's registered allocation from an explicit history slice (probe seam)."""
    T = R.shape[0]
    if T <= lb:
        return np.zeros(R.shape[1]), np.zeros(R.shape[1], dtype=np.int64)
    window = R[T - lb:, :]
    Tr = lag_transform(window, lb)
    sd = Tr.std(axis=0, ddof=1)
    if not np.all(np.isfinite(sd)) or np.any(sd <= 0.0):
        return np.zeros(R.shape[1]), np.zeros(R.shape[1], dtype=np.int64)
    C = np.corrcoef(Tr.T)
    lam, Q = np.linalg.eigh(C)
    lam_desc = lam[::-1]
    Q = Q[:, ::-1]
    lam_nn = clean_eigenvalues_q(lam_desc, arm, lb)
    Sigma = (sd[:, None] * Q) @ np.diag(lam_nn) @ (Q.T * sd[None, :])
    Sigma = 0.5 * (Sigma + Sigma.T)
    w, _feasible = long_only_gmv(Sigma)
    s = np.where(w >= EQUAL_WEIGHT - LAG_TOL, 1, 0).astype(np.int64)
    return w, s


class Panel:
    """The registered four-instrument panel of one strategy case.

    Holds the shared return matrix and the per-market registered allocation / target-state /
    event paths.  One panel serves all four cohorts (the universe IS the panel), so each case's
    allocation path is computed once per run and re-read 48x per phase grid.
    """

    _cache = {}

    def __init__(self, case, cohorts):
        self.case = tuple(case)
        self.case_label = case_name(self.case)
        self.arm = CLEAN_ARMS[case_clean(self.case)]
        self.lookback = int(LOOKBACK_LABELS[case_lookback(self.case)])
        labels = sorted(cohorts)
        if len(labels) != PANEL_SIZE:
            raise SystemExit("the registered panel needs exactly %d instruments, got %r"
                             % (PANEL_SIZE, labels))
        self.markets = labels
        rets = [np.asarray(cohorts[m].inputs["ret"], dtype=np.float64) for m in labels]
        self.n = min(len(x) for x in rets)
        if any(len(x) != self.n for x in rets):
            counter("signal", "panel_grid_mismatch")
            raise SystemExit("panel series do not share one bar grid: %r"
                             % [len(x) for x in rets])
        self.R = np.stack([x[:self.n] for x in rets], axis=1)
        paths = panel_paths(self.R, self.lookback, self.arm)
        self.weights = paths["weights"]
        self.state = paths["states"]
        self.events = paths["events"]
        d = paths["diag"]
        if d["degenerate_bars"]:
            counter("signal", "allocation_degenerate", int(d["degenerate_bars"]))
        if d["max_weight_sum"] is not None and abs(float(d["max_weight_sum"]) - 1.0) > 1e-9:
            counter("signal", "allocation_weight_sum_violation")
        if self.n > self.lookback and np.any(self.state[:self.lookback] != 0):
            counter("signal", "signal_before_the_registered_warmup")
        defined = self.weights.sum(axis=1) > 0.0
        rule = np.where(self.weights >= EQUAL_WEIGHT - LAG_TOL, 1, 0).astype(np.int64)
        bad = int(np.count_nonzero((self.state != rule) & defined[:, None]))
        if bad:
            counter("signal", "state_not_derived_from_the_registered_rule", bad)
        if not np.all(np.isfinite(self.weights)):
            counter("signal", "nonfinite_weight")
        if d["defined_bars"] != int(np.count_nonzero(defined)):
            counter("signal", "cleaning_undefined_on_a_defined_bar")
        self.diag = {
            "case_name": self.case_label, "cleaning_arm": self.arm,
            "lookback": self.lookback, "panel": labels, "bars": int(self.n),
            "warmup_bars": self.lookback, "defined_bars": int(d["defined_bars"]),
            "degenerate_bars": int(d["degenerate_bars"]),
            "max_weight_sum": (None if d["max_weight_sum"] is None
                               else round(float(d["max_weight_sum"]), 12)),
            "min_weight_sum": (None if d["min_weight_sum"] is None
                               else round(float(d["min_weight_sum"]), 12)),
            "max_weight": round(float(d["max_weight"]), 6),
            "feasible_supports_min": d["feasible_supports_min"],
            "market_state_bars": {m: {"long": int((self.state[:, i] > 0).sum()),
                                      "short": 0,
                                      "events": int((self.events[:, i] != 0).sum())}
                                  for i, m in enumerate(labels)},
            "policy_note": "the registered target state is the long-only allocation tilt "
                           "(w_i >= 1/n); the alternative reading (any strictly positive weight "
                           "-> invested, true on almost every bar) is NOT evaluated (disclosed)",
        }

    def layer_for(self, market):
        i = self.markets.index(market)
        return {"pos": np.ascontiguousarray(self.state[:, i]),
                "events": np.ascontiguousarray(self.events[:, i]),
                "yhat": np.ascontiguousarray(np.where(self.weights[:, i] > 0.0,
                                                      self.weights[:, i], np.nan)),
                "weight": np.ascontiguousarray(self.weights[:, i])}

    def report(self):
        return dict(self.diag)


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
    """One (cohort, case) signal layer: the panel's per-market allocation weight, target state and
    entry events.  Built once per case and cached (the DCA grid re-reads it 48x per phase grid)."""

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
        self.weight = view["weight"]
        self.diag = dict(panel.diag)
        self.diag["cohort"] = cohort.symbol
        self.diag["market_state_bars"] = panel.diag["market_state_bars"][cohort.symbol]

    def report(self):
        return {"case_name": self.case_label, "cleaning_arm": self.diag["cleaning_arm"],
                "lookback": self.diag["lookback"],
                "market_state_bars": self.diag["market_state_bars"],
                "warmup_bars": self.diag["warmup_bars"],
                "defined_bars": self.diag["defined_bars"]}


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


def with_layer(cohort, case, layer, fn):
    """Run `fn` with one temporary signal layer installed for (cohort, case), then restore.

    `simulate` resolves its episode source through `signals_for(cohort, case)`; a falsification
    reader that must measure a DIFFERENT signal path (a shuffled panel, or the record's own
    univariate baseline) installs it here instead of mutating any cached production layer.
    """
    key = tuple(case)
    prev = cohort._signal_cache.get(key)
    cohort._signal_cache[key] = layer
    try:
        return fn()
    finally:
        if prev is None:
            cohort._signal_cache.pop(key, None)
        else:
            cohort._signal_cache[key] = prev


_RAW_SURFACES = {}


def _rows_of(rows_by_cohort, label, kind):
    return rows_by_cohort[label][kind]


def _arm_hist_rows(hist_rows, arm):
    """Every registered historical cell of one cleaning arm (episodes floor applied later)."""
    return [r for r in hist_rows if int(r["cl_%s" % CLEAN_LABELS[CLEAN_ARMS.index(arm)]]) == 1]


def _arm_winner(hist_rows, spec, arm):
    """One cleaning arm's best historical cell under the registered cohort ordering.

    The registered ordering is reused verbatim (Sharpe desc, net_pnl desc, registered-index
    lexical key); the economic positivity requirements of the cohort selector are NOT applied
    here, because this reader compares arms, it does not elect the cohort winner.
    """
    min_ep = spec["gates"]["min_episodes_is"]
    cand = [r for r in _arm_hist_rows(hist_rows, arm) if r["episodes"] >= min_ep]
    if not cand:
        return None
    axes = axis_values(spec)
    cand.sort(key=lambda r: (-r["sharpe"], -r["net_pnl"], tie_break_key(r, axes)))
    return cand[0]


def bigru_spectral_ablation_reader(spec, cohort_results, diag_inputs, rows_by_cohort):
    """Reader 1 - record falsification item 2 ("Ablation of BiGRU Spectral Denoiser").

    The record's own ablation threshold is stated on a portfolio Sharpe at leverage 3.0, an object
    this frame does not produce (the registered rail owns one fixed 10x notional per instrument).
    This reader therefore executes the ablation in the units the frame does produce: for every
    cohort, each registered cleaning arm's own best historical cell is carried to the FULL window
    and the identity/raw arm's full-window net PnL is compared against the best cleaning arm's.

    TRIGGERED when the identity arm is at least as good as every cleaning arm (within the
    registered tolerance) in the majority of cohorts: on this local universe the record's
    incremental-cleaning claim then has no economic support.  Descriptive, never PASS-bearing.
    """
    out = {"registered_item": "bigru-spectral-denoiser-ablation", "evaluated": False, "hit": False,
           "landing": "family-level: a hit must NEVER be recorded as PASS",
           "units_note": "the record's own threshold is a portfolio Sharpe at leverage 3.0; this "
                         "frame reports the registered rail's full-window net PnL per arm, and the "
                         "trained BiGRU arm itself is not evaluable (see the "
                         "not_executed reader)",
           "tolerance": FULL_WINDOW_NET_PNL_TOL, "cohorts": {}}
    if not cohort_results:
        out["status"] = "not_evaluated"
        return out
    cells = 0
    identity_at_least_as_good = 0
    for c in cohort_results:
        label = c["cohort"]
        hist = _rows_of(rows_by_cohort, label, "historical")
        full = _rows_of(rows_by_cohort, label, "full")
        arms = {}
        for arm in CLEAN_ARMS:
            w = _arm_winner(hist, spec, arm)
            if w is None:
                arms[arm] = None
                continue
            dca = {a: w[a] for a in DCA_AXES}
            dca["base_quote"] = spec["dca_domain"]["base_quote"]
            row = _cell(rows_by_cohort, label, "full", case_tuple(w), dca)
            arms[arm] = {"case_name": case_name(case_tuple(w)),
                         "historical_sharpe": round(float(w["sharpe"]), 6),
                         "historical_net_pnl": round(float(w["net_pnl"]), 6),
                         "historical_episodes": int(w["episodes"]),
                         "full_net_pnl": (None if row is None
                                          else round(float(row["net_pnl"]), 6)),
                         "full_sharpe": None if row is None else round(float(row["sharpe"]), 6)}
        complete = all(arms[a] is not None and arms[a]["full_net_pnl"] is not None
                       for a in CLEAN_ARMS)
        best_other = None
        if complete:
            best_other = max(arms[a]["full_net_pnl"] for a in CLEAN_ARMS if a != "raw")
            at_least = arms["raw"]["full_net_pnl"] >= best_other - FULL_WINDOW_NET_PNL_TOL
            cells += 1
            if at_least:
                identity_at_least_as_good += 1
        else:
            at_least = None
        out["cohorts"][label] = {"arms": arms, "identity_at_least_as_good": at_least}
    out["evaluated"] = True
    out["cells"] = cells
    out["cells_identity_at_least_as_good"] = identity_at_least_as_good
    out["majority_rule_note"] = ("triggered when the identity/raw arm's full-window net PnL is at "
                                 "least the best cleaning arm's in the MAJORITY of the registered "
                                 "cohorts")
    out["hit"] = bool(cells and identity_at_least_as_good * 2 > cells)
    return out


def _measure_raw_surfaces():
    """Measured availability of the inputs the record's own falsification items need.

    Read-only walk of the canonical raw store: the kline interval set actually present and any
    dataset family whose name carries trade/tick/tick-level market microstructure.  Memoised.
    A missing store is reported as an empty measurement (never as a silent True).
    """
    cached = _RAW_SURFACES.get("value")
    if cached is not None:
        return cached
    klines = os.path.join(RAW_ROOT, "klines")
    intervals, symbols = set(), []
    if os.path.isdir(klines):
        for sym in sorted(os.listdir(klines)):
            if not os.path.isdir(os.path.join(klines, sym)):
                continue
            symbols.append(sym)
            for iv in sorted(os.listdir(os.path.join(klines, sym))):
                if os.path.isdir(os.path.join(klines, sym, iv)):
                    intervals.add(iv)
    top = []
    for root in ("/data/raw", RAW_ROOT):
        if os.path.isdir(root):
            top.extend(sorted(os.listdir(root)))
    token_hits = [n for n in top
                  if any(t in n.lower() for t in ("trade", "tick", "agg", "book", "depth"))]
    _RAW_SURFACES["value"] = {
        "kline_intervals_present": sorted(intervals),
        "kline_symbols_present": symbols,
        "market_dirs": (sorted(os.listdir(RAW_ROOT)) if os.path.isdir(RAW_ROOT) else []),
        "top_level_names_probed": sorted(set(top)),
        "trade_tick_token_hits": sorted(set(token_hits)),
        "finest_interval_present": (sorted(intervals, key=_interval_rank)[0] if intervals else None),
    }
    return _RAW_SURFACES["value"]


def _interval_rank(iv):
    """Sort key for a Binance interval string ('5m' < '1h' < '1d' < '1w')."""
    unit = iv[-1]
    n = int(iv[:-1])
    return ({"m": 0, "h": 1, "d": 2, "w": 3}.get(unit, 9), n)


def market_impact_haircut_reader(spec, cohort_results, diag_inputs):
    """Reader 2 - record falsification item 1 ("Market Impact Haircut Test"): NOT EXECUTED.

    The item is defined on an Almgren-Chriss square-root impact model calibrated at institutional
    AUM ($10M / $50M / $100M) and on the record's `first forced liquidation leverage` object.
    Neither is constructible here: the canonical store has no order-book / depth surface from
    which an impact coefficient could be identified, and the registered rail is a fixed 30,000
    USDT account at 10x notional rather than a leverage ladder.  Recorded as `not_executed` with
    the measured reason - never weakened into a substitute test.
    """
    surfaces = _measure_raw_surfaces()
    return {"registered_item": "market-impact-haircut-test", "evaluated": False, "hit": False,
            "status": "not_executed",
            "landing": "family-level: a hit must NEVER be recorded as PASS",
            "required_inputs": ["Almgren-Chriss impact coefficient (order-book / depth surface)",
                                "institutional AUM levels 10M / 50M / 100M USD",
                                "the first-forced-liquidation leverage of a leverage ladder"],
            "measured_available": {"kline_intervals_present": surfaces["kline_intervals_present"],
                                   "order_book_or_depth_surface": False,
                                   "trade_tick_token_hits": surfaces["trade_tick_token_hits"]},
            "reason": "no depth/order-book surface exists in the canonical store, so an impact "
                      "coefficient cannot be identified, and the registered rail runs one fixed "
                      "10x notional rather than the record's leverage ladder; the item is not "
                      "executed rather than replaced by a weaker proxy"}


def cross_asset_transferability_reader(spec, cohort_results, diag_inputs):
    """Reader 3 - record falsification item 3 ("Cross-Asset Transferability Without Retraining"):
    NOT EXECUTED.  The item requires the pre-trained model evaluated on European (STOXX 600) and
    Japanese (Nikkei 225) equity panels; the canonical store holds exactly one venue with four
    USD-M perpetual instruments and no equity market at all.
    """
    surfaces = _measure_raw_surfaces()
    return {"registered_item": "cross-asset-transferability-without-retraining",
            "evaluated": False, "hit": False, "status": "not_executed",
            "landing": "family-level: a hit must NEVER be recorded as PASS",
            "required_panels": ["STOXX 600 equity panel", "Nikkei 225 equity panel"],
            "measured_available": {"market_dirs": surfaces["market_dirs"],
                                   "kline_symbols_present": surfaces["kline_symbols_present"],
                                   "equity_market_present": False},
            "reason": "the local store holds no equity market, so the transfer panels do not "
                      "exist; the item is not executed, and the pre-trained weights it needs are "
                      "absent from the workspace as well"}


def intraday_tick_margin_audit_reader(spec, cohort_results, diag_inputs):
    """Reader 4 - record falsification item 4 ("Intraday Tick-Level Margin Breach Audit"):
    NOT EXECUTED.  The item requires actual tick-level intraday drawdown histories; the store's
    finest kline interval is measured below and no trade/tick dataset exists, so the daily-low
    approximation cannot be replaced by the tick-level object the item demands.
    """
    surfaces = _measure_raw_surfaces()
    return {"registered_item": "intraday-tick-level-margin-breach-audit",
            "evaluated": False, "hit": False, "status": "not_executed",
            "landing": "family-level: a hit must NEVER be recorded as PASS",
            "required_inputs": ["tick-level (or aggTrade) intraday price history"],
            "measured_available": {"kline_intervals_present": surfaces["kline_intervals_present"],
                                   "finest_interval_present": surfaces["finest_interval_present"],
                                   "trade_tick_token_hits": surfaces["trade_tick_token_hits"]},
            "reason": "no tick/trade dataset exists in the canonical store (the finest kline "
                      "interval is %r), so a tick-level drawdown audit cannot be run; the item is "
                      "not executed" % (surfaces["finest_interval_present"],)}


def freeze_rule_reader(spec, cohort_results, diag_inputs, rows_by_cohort):
    """Reader 5 - record falsification item 5 ("Rejection Rule"), local analogue.

    The record freezes model evaluation when an out-of-sample Sharpe at leverage 2.0 over any
    trailing 36-month rolling window falls below 0.40, or when more than 2 margin liquidations
    occur.  Two disclosures: (a) the registered rail's liquidation event is its margin backstop,
    read through the engine's own `margin_calls` metric, and the registered threshold is
    `margin_calls >= 2`; (b) the 36-month rolling window and the leverage-2.0 portfolio object do
    not exist on this store (the local OOS slice is 11.5 months), so the Sharpe leg is reported
    descriptively and is NOT part of the hit rule.
    """
    out = {"registered_item": "rejection-rule-freeze", "evaluated": False, "hit": False,
           "landing": "family-level: a hit must NEVER be recorded as PASS",
           "margin_calls_threshold": FREEZE_MARGIN_CALLS,
           "disclosed_oos_sharpe_threshold": FREEZE_OOS_SHARPE,
           "sharpe_leg_note": "the record's trailing-36-month rolling OOS Sharpe at leverage 2.0 "
                              "does not exist in this frame; the winner's OOS Sharpe is reported "
                              "descriptively and is not part of the hit rule",
           "cohorts": {}}
    if not cohort_results:
        out["status"] = "not_evaluated"
        return out
    hits = []
    for c in cohort_results:
        if c["winner"] is None:
            continue
        label = c["cohort"]
        key = (tuple(int(c["winner"][f]) for f in CASE_FIELDS),) + tuple(
            c["winner"][a] for a in DCA_AXES)
        grid_kinds = list(FULL_WINDOW_GRID_KINDS) + ["historical", "oos"]
        margin_calls = {}
        for kind in grid_kinds:
            row = _cell(rows_by_cohort, label, kind, key[0], dict(
                zip(DCA_AXES, key[1:])))
            if row is not None:
                margin_calls[kind] = int(row["margin_calls"])
        worst = max(margin_calls.values()) if margin_calls else 0
        legs = {"max_margin_calls_any_registered_grid": worst,
                "freeze_margin_leg": bool(worst >= FREEZE_MARGIN_CALLS)}
        out["cohorts"][label] = {
            "margin_calls_by_grid": margin_calls,
            "oos_sharpe_descriptive": (None if c["metrics"]["oos"] is None
                                       else c["metrics"]["oos"].get("sharpe")),
            **legs}
        if legs["freeze_margin_leg"]:
            hits.append(label)
    out["evaluated"] = True
    out["cohorts_triggering"] = hits
    out["hit"] = bool(hits)
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
    # The record's five-item falsification battery, registered as five family-level readers:
    # two are executed (the spectral-denoiser ablation comparison and the disclosed local
    # analogue of the rejection rule), three are `not_executed` with their measured reason.
    ablation = (bigru_spectral_ablation_reader(spec, cohort_results, diag_inputs, rows_by_cohort)
                if coverage_complete
                else dict(empty_reader, registered_item="bigru-spectral-denoiser-ablation",
                          status="not_evaluated"))
    impact = market_impact_haircut_reader(spec, cohort_results, diag_inputs)
    transfer = cross_asset_transferability_reader(spec, cohort_results, diag_inputs)
    tick_audit = intraday_tick_margin_audit_reader(spec, cohort_results, diag_inputs)
    freeze = (freeze_rule_reader(spec, cohort_results, diag_inputs, rows_by_cohort)
              if coverage_complete
              else dict(empty_reader, registered_item="rejection-rule-freeze",
                        status="not_evaluated"))
    flags = {"bigru_spectral_denoiser_ablation": bool(ablation.get("hit")),
             "market_impact_haircut_test": bool(impact.get("hit")),
             "cross_asset_transferability_without_retraining": bool(transfer.get("hit")),
             "intraday_tick_level_margin_breach_audit": bool(tick_audit.get("hit")),
             "rejection_rule_freeze": bool(freeze.get("hit"))}

    # registered landing of the five family-level readers: none of them may be recorded as
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
        # Strategy K registered guards (names frozen in the round-spec)
        "entry_bar_is_the_next_bar_after_the_signal":
            counters_total("entry_bar_not_the_next_bar_after_the_signal") == 0,
        "no_entry_without_a_defined_signal": counters_total("entry_before_a_defined_signal") == 0,
        "allocation_is_never_degenerate": counters_total("allocation_degenerate") == 0,
        "long_only_weights_sum_to_one": counters_total("allocation_weight_sum_violation") == 0,
        "state_is_derived_from_the_registered_weight_rule":
            counters_total("state_not_derived_from_the_registered_rule") == 0,
        "no_signal_before_the_registered_warmup":
            counters_total("signal_before_the_registered_warmup") == 0,
        "panel_series_share_one_bar_grid": counters_total("panel_grid_mismatch") == 0,
        "cleaning_is_defined_on_every_defined_bar":
            counters_total("cleaning_undefined_on_a_defined_bar") == 0,
        "no_nonfinite_allocation_weight": counters_total("nonfinite_weight") == 0,
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
                         "cleaning_arms": list(CLEAN_ARMS),
                         "lookbacks": list(LOOKBACK_LABELS),
                         "signal_constants": {"theta": list(THETA),
                                              "shrink_delta": SHRINK_DELTA,
                                              "mp_edge_formula": "(1 + sqrt(n / dt_in))^2",
                                              "equal_weight_tilt": EQUAL_WEIGHT,
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
        "bigru_spectral_denoiser_ablation": ablation,
        "market_impact_haircut_test": impact,
        "cross_asset_transferability_without_retraining": transfer,
        "intraday_tick_level_margin_breach_audit": tick_audit,
        "rejection_rule_freeze": freeze,
        "raw_surface_measurement": _measure_raw_surfaces(),
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
        sys.stderr.write("usage: 100_strategy_j_run.py <run-spec.json>\n")
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
                raise SystemExit("run-spec is not a Strategy K spec: missing %r (contract 7.2/7.3)"
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
                    "note": "the registered volatility-scaling / oscillator inputs; the lead-lag "
                            "and graph layers are built per case on the shared four-instrument "
                            "panel and are reported in signal_layer.json"}
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
            panel_report[p.case_label] = {"bars": p.n, "markets": list(p.markets),
                                          "warmup_bars": p.diag["warmup_bars"],
                                          "cleaning_arm": p.diag["cleaning_arm"],
                                          "lookback": p.diag["lookback"]}
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
                          {"bigru_spectral_denoiser_ablation":
                               summary["bigru_spectral_denoiser_ablation"],
                           "market_impact_haircut_test": summary["market_impact_haircut_test"],
                           "cross_asset_transferability_without_retraining":
                               summary["cross_asset_transferability_without_retraining"],
                           "intraday_tick_level_margin_breach_audit":
                               summary["intraday_tick_level_margin_breach_audit"],
                           "rejection_rule_freeze": summary["rejection_rule_freeze"],
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
