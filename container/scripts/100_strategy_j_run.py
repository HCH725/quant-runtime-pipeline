#!/usr/bin/env python3
"""Strategy J — Commodity Futures Network Momentum: Signature Levy Area and Dynamic Time
Warping Graph Learning, executed on the registered LOCAL eligible universe (BTCUSDT / ETHUSDT /
BNBUSDT / SOLUSDT USD-M perpetuals, 1 d) on Qlib.

Entry point for ONE pre-registered attempt.  Reads the immutable run-spec from /results (the
only source of parameters), builds the Qlib .bin store from the READ-ONLY canonical raw store
into /qlib/work, then runs the pre-registered full-backtest contract
(QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md sections 7.2 / 7.3):

    4 cohorts (BTCUSDT / ETHUSDT / BNBUSDT / SOLUSDT, raw interval 1d)
    x STRATEGY domain (2 registered axes: the lead-lag estimator {levy, dtw, ddtw}
      x the lead-lag lookback {22, 44, 66, 88, 110, 132, ensemble} = 21 cases)
    x DCA domain (4 axes = 48 configs)
    x 10 registered phase grids (historical / oos / full / fee_2x / funding_2x /
      entry_delay_1_bar / slippage_2ticks / no_funding / no_funding_full /
      cost_attrition_40bps)
    = 4 x 21 x 48 x 10 = 40,320 expected case evaluations.

SOURCE CLAIM UNDER TEST (the record's own): cross-market lead-lag structure carries directional
momentum spillover, and filtering pairwise lead-lag matrices into a sparse graph makes a
network-aggregated trend following signal that beats its univariate MACD counterpart.  The
record's own portability paragraph names crypto perpetuals, funding carry and a BTC/ETH-leader
hierarchy as an `adapted` / `unproven` extension; this round executes that extension on the four
canonical instruments, with the source market (28 commodity futures) recorded as provenance and
external-validity context only (operator override, card t_5551afc1, 2026-09-18).

THE REGISTERED SIGNAL (record-faithful; every unstated alignment/scale detail is a project
pre-registered constant frozen before the first run - see the module docstring of the round-spec):

  * volatility scaling  : 22-day EWMA std of daily deltas, scaled deltas and the reconstructed
                           scaled price series P~
  * TSMOM oscillators    : R^k for k in {1..6}, alpha_k = (k sqrt2)^-1, beta_k = (rho k sqrt2)^-1,
                           rho = 4, R^k = (mu(P~, alpha_k) - mu(P~, beta_k)) / sigma(P~, alpha_k)
  * lead-lag matrices    : skew-symmetric V_t over delta in {22,44,66,88,110,132} trading days,
                           either the 2nd-level signature Levy area or the DTW / DDTW mode-lag
                           (mode of j_l - i_l along the optimal warping path, Sakoe-Chiba band)
  * graph learning       : W_ij = ||v_i - v_j||^2 on the max-abs normalised row profiles, the
                           registered convex problem solved in closed form, row-normalised;
                           ensemble variant averages the six normalised adjacencies
  * network oscillator   : R~_m = sum_n A_mn R_n            (own market excluded: diag(A) = 0)
  * sizing               : the reverting sigmoid r(x) = c_lambda x exp(-x^2/(2 lambda^2)),
                           lambda = sqrt(2), aggregated over the six speeds
  * target state         : sign of the aggregated score (long-short, the record's signed target);
                           the registered DCA rail owns the actual position size, so only the
                           direction and the exit condition are taken from the record

ENTRY / EXIT (the record's own position semantics wrapped in the card-mandated DCA rail):
an ENTRY event is a bar whose target state CHANGES to a non-zero value (a fresh signal); the
entry executes at the NEXT bar's open (the record's next-day execution), optionally moved by the
registered `entry_delay_1_bar` execution stress.  An episode of direction d ends when (1) the
rail take profit at running_average_cost x (1 +- breakeven_tp_pct) fills reduce-only, (2) the
resting invalidation at running_average_cost x (1 -+ invalidation_pct) fills, (3) the record's
own exit condition holds at a bar's close - the target state is no longer d - and every layer is
flattened at the NEXT bar's open, or (4) the slice ends (reduce-only flatten of every layer).
tranche #1 is the initial entry; adverse-price scale-ins follow the registered ladder
(level_k price = initial_entry_price x (1 -+ spacing_pct x k), k = 1..10, i.e. at most 11 routine
active levels of the 12-tranche rail).

DCA is executed as real order/fill accounting (an episode state machine over the bars); nothing
is estimated after the fact.  Every legal (strategy params x DCA config) cell is evaluated on
every registered phase grid, and the family gate is the **cohort-level survivor** rule of
contract section 7.3: one deterministic historical-only winner per cohort, then OOS / full /
robustness / parameter-neighbourhood evidence for that same winner.  This family has four
cohorts, so the cohort survivor count is 0..4 and EVERY survivor advances (v1.4.0 band mapping).

The record's four-item falsification battery is registered as four family-level readers
(permutation test on the cross-sectional alignment, execution-lag dissipation against the
record's own univariate baseline, the DTW descriptor ablation inside registered shock windows,
and the out-of-sample universe expansion, which cannot be executed on the four local
instruments and is recorded as `not_executed`); a reader hit can only move a PASS to DEFERRED
and is never PASS-bearing.

Writes only:
  * /qlib/work/**      (rebuildable derived/cache area, INV-5)
  * <attempt_dir>/**   (result.json, artifacts/, logs/, state.json)
Never writes /data/raw (read-only mount).

usage: 100_strategy_j_run.py <run-spec.json>
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
WORK_ROOT = "/qlib/work/strategyJ-v1"
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
LL_METHODS = ("levy", "dtw", "ddtw")                  # searched axis 1: the lead-lag estimator
LOOKBACKS = (22, 44, 66, 88, 110, 132)                # the record's own lookback set
LOOKBACK_LABELS = tuple([str(d) for d in LOOKBACKS] + ["ensemble"])
CASE_FIELDS = ("ll_levy", "ll_dtw", "ll_ddtw",
               "lb_22", "lb_44", "lb_66", "lb_88", "lb_110", "lb_132", "lb_ensemble")
CASE_ORDER = tuple(
    tuple([1 if a == i else 0 for a in range(3)] + [1 if b == j else 0 for b in range(7)])
    for j in range(len(LOOKBACK_LABELS)) for i in range(len(LL_METHODS)))
CASE_NAMES = tuple("%s__lb%s" % (LL_METHODS[i], LOOKBACK_LABELS[j])
                   for j in range(len(LOOKBACK_LABELS)) for i in range(len(LL_METHODS)))
VOL_WINDOW = 22                # source-specified: the record's 22-day volatility scaling window
SPEED_INDEXES = (1, 2, 3, 4, 5, 6)      # source-specified speed set
RHO = 4.0                      # source-specified fast/slow ratio
SIGMOID_LAMBDA = math.sqrt(2.0)         # source-specified
C_LAMBDA = 1.0                 # project pre-registered scale constant (the record leaves c_lambda open)
GRAPH_ALPHA = 1.0              # project pre-registered (cancels in row normalisation, see below)
GRAPH_BETA = 1e-2              # project pre-registered sparsity floor of the graph kernel
DTW_BAND_FRACTION = 0.25       # project pre-registered Sakoe-Chiba band (computational scope)
DTW_MIN_BAND = 2
# Registered warm-up floor: the longest lead-lag lookback (132) + the volatility window (22) +
# the slowest oscillator's smoothing transient (1 / beta_6 = 6*sqrt2*4 ~ 34 bars).
SIGNAL_WARMUP_BARS = 188
PANEL_SIZE = 4                 # the registered local eligible universe
PANEL_COHORTS = {}             # filled by main(): the four cohorts the panel is built from
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
# composite (lead-lag estimator, lookback) axis; its registered order is CASE_ORDER.
AXES = ("window_case", "spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct")
DCA_AXES = ("spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct")
SELECTOR_VERSION = "cohort-selector-v1"
DISPOSITION_VERSION = "cohort-disposition-v1"
CONTRACT_SEMANTICS_VERSION = "v1.4.0"
ENGINE_VERSION = "j-v1-engine-1.0.0"
ENGINE_SEMANTICS = ("the record's network-momentum signal chain (22-day volatility scaling, six "
                    "TSMOM oscillators, Levy-area / DTW / DDTW lead-lag matrices over six "
                    "lookbacks, the registered convex graph adjacency in closed form, the row "
                    "normalised network aggregation and the reverting sigmoid) turned into "
                    "target-state change entry events at the next bar's open, the record's target "
                    "exit at the next bar's open, plus the registered DCA rail (see module "
                    "docstring)")
WINNER_METRIC_KEYS = ("net_pnl", "sharpe", "episodes", "ending_equity", "fees", "funding",
                      "max_dd_usdt", "max_dd_pct", "max_effective_leverage",
                      "capital_utilization", "annualized_return", "cagr")
# csv row field order (kept identical for every phase grid)
ROW_FIELDS = ("symbol", "timeframe", "window_kind", "window_case", "case_name",
              "ll_levy", "ll_dtw", "ll_ddtw",
              "lb_22", "lb_44", "lb_66", "lb_88", "lb_110", "lb_132", "lb_ensemble",
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
                 "causality_probe_mismatch", "dtw_path_empty", "lead_lag_window_incomplete",
                 "dtw_warmup_window_skipped", "dtw_warmup_window_outside_warmup",
                 "graph_kernel_degenerate", "shuffled_signal_undefined")
COUNTERS = {}
# ---- registered family-level falsification reader constants (frozen before the first run)
N_PERM_DRAWS = 24              # cross-sectional permutation draws (paired one-sided t-test, 1%)
PERM_SEED = 20260918
PERM_ALPHA = 0.01
PERM_MARGIN_SHARPE = 0.05      # the record's "+0.05 net Sharpe over the shuffled baseline"
LAG_DISSIPATION_FRACTION = 0.5  # the record's "more than 50% of the enhancement dissipates"
# Registered local shock windows for the DTW descriptor ablation (the record's commodity shock
# regimes are outside the local store; these are the registered local crypto analogues).
SHOCK_WINDOWS = (("luna_collapse_2022", "2022-05-01", "2022-06-30"),
                 ("ftx_collapse_2022", "2022-11-01", "2022-12-31"),
                 ("carry_unwind_2024", "2024-07-25", "2024-08-31"))



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


# ---------------------------------------------------------------------------
# optional inert trace hook (contract v1.6.0 section 28.2, unchanged in v1.8.0)
#
# `TRACE` is the module-level sink used by the survivor-evidence replay driver
# (container/scripts/130_family_survivor_replay.py).  It is None on every
# production path, and every emit point sits behind an explicit
# `if TRACE is not None` guard, so with tracing off the engine executes the very
# same arithmetic in the very same order and returns the very same aggregate
# (enforced by the trace off/on equality check the replay driver runs on every
# replayed cell, and by container/scripts/tests/test_survivor_trace_jkl.py).
# A traced value is only ever observed: nothing recorded here is read back by
# any decision, accounting or return value of the engine.
# ---------------------------------------------------------------------------
TRACE = None


def _trace(event, **fields):
    """Emit one trace record to the optional sink; a no-op when TRACE is None."""
    if TRACE is None:
        return
    TRACE(event, fields)


def _trace_equity(kind, cohort, i0, day_local, series_flat, day_equity):
    """Trace-only equity ledger of one evaluated slice (contract section 28.2).

    `day_local` is the slice's own day index (`np.unique(..., return_inverse=True)`
    inside `simulate`), so the emitted marks are the very series `_flat_series`
    feeds to `_metrics`; `day_start_ms` carries the first bar's open time of each
    day so a replay can label the rows without re-deriving the mapping.
    """
    starts = np.searchsorted(day_local, np.arange(len(series_flat)), side="left")
    _trace("equity_marks", window_kind=kind,
           cohort="%s/%s" % (cohort.symbol, cohort.timeframe),
           day_index=list(range(len(series_flat))),
           day_start_ms=[int(cohort.open_time_ms[i0 + int(s)]) for s in starts],
           equity=[float(v) for v in series_flat],
           in_window=[v is not None for v in day_equity])


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


def case_method(case):
    """Registered lead-lag estimator index of one case: 0 = levy, 1 = dtw, 2 = ddtw."""
    return list(case).index(1, 0, 3)


def case_lookback(case):
    """Registered lookback index of one case: 0..5 = the six lookbacks, 6 = ensemble."""
    return list(case).index(1, 3, 10) - 3


# ---------------------------------------------------------------------------
# network-momentum science  (record-faithful signal; research-defined operationalization)
# ---------------------------------------------------------------------------
# The record fixes the MECHANISM and the mathematics:
#   1. volatility-scaled cumulative price series  P~ (22-day EWMA std of daily deltas)
#   2. six TSMOM oscillators R^k (alpha_k = (k sqrt2)^-1, beta_k = (rho k sqrt2)^-1, rho = 4)
#   3. pairwise skew-symmetric lead-lag matrices V_t over lookbacks delta in
#      {22,44,66,88,110,132}: the 2nd-level signature Levy area, and the DTW mode-lag
#   4. the convex graph-learning adjacency  min_{A>=0, diag=0} 1/2||A o W||_F^2
#      - alpha 1^T log(A1) + beta/2 ||A||_F^2,  W_ij = ||v_i - v_j||^2, then row normalisation
#   5. the network oscillator  R~_m = sum_n A_mn R_n  and the reverting sigmoid
#      r(x) = c_lambda x exp(-x^2/(2 lambda^2)), lambda = sqrt(2)
# What the record does NOT fix is any alignment/normalisation/scale detail; every such choice
# below is a project pre-registered constant (`research-defined`), frozen before the first run
# and mirrored in runtime/strategy_j_prereg: see the module docstring of the round-spec.
#
# Numerical identities that make this implementation exact rather than iterative:
#   * the registered graph problem is convex; its stationarity condition is
#     A_ij (W_ij^2 + beta) = alpha / d_i  with d_i = sum_j A_ij (the log-barrier derivative of
#     -alpha sum_i log(d_i) is -alpha/d_i), hence  d_i = sqrt(alpha * S_i),  S_i = sum_j 1/(W_ij^2+beta)
#     and the ROW-NORMALISED solution is  A~_ij = (1/(W_ij^2+beta)) / sum_j (1/(W_ij^2+beta)),
#     i.e. alpha cancels exactly.  The self-check test cross-checks this closed form against a
#     projected-gradient solve of the same objective.
#   * "1/2 ||A o W||_F^2" is read literally (the elementwise product is squared as written,
#     because the record already defines W as the SQUARED distance); the alternative reading
#     (penalise A by the un-squared distance) is not evaluated and is disclosed in the round-spec.


def _ewma(x, alpha):
    """The registered exponential smoothing recursion: mu_0 = x_0, mu_t = a x_t + (1-a) mu_{t-1}."""
    n = len(x)
    out = np.array(x, dtype=np.float64, copy=True)
    a = float(alpha)
    for t in range(1, n):
        out[t] = a * x[t] + (1.0 - a) * out[t - 1]
    return out


def _ewmvar(x, alpha, mu):
    """RiskMetrics exponentially weighted variance ABOUT the same recursion's mean.

    v_0 = 0,  v_t = a (x_t - mu_t)^2 + (1-a) v_{t-1};  sigma_t = sqrt(v_t).
    This is the registered EWMA standard deviation (the record names it but fixes no bias
    convention; the disclosed convention is this recursion).
    """
    n = len(x)
    out = np.zeros(n, dtype=np.float64)
    a = float(alpha)
    for t in range(1, n):
        e = x[t] - mu[t]
        out[t] = a * e * e + (1.0 - a) * out[t - 1]
    return out


def build_network_inputs(cohort):
    """The registered signal INPUTS of one cohort: volatility-scaled deltas, the reconstructed
    scaled price series and the six TSMOM oscillators.  Every recursion is causal, so the block
    is prefix-invariant by construction and `causality_probe` re-verifies it executably."""
    close = np.asarray(cohort.close, dtype=np.float64)
    n = len(close)
    delta = np.zeros(n, dtype=np.float64)
    delta[1:] = close[1:] - close[:-1]
    a_vol = 2.0 / (VOL_WINDOW + 1.0)
    mu = _ewma(delta, a_vol)
    var = _ewmvar(delta, a_vol, mu)
    sigma = np.sqrt(np.maximum(var, 0.0))
    dsc = np.full(n, np.nan, dtype=np.float64)
    ok = sigma > 0.0
    dsc[ok] = delta[ok] / sigma[ok]
    dsc[0] = 0.0
    scaled_price = np.cumsum(np.nan_to_num(dsc, nan=0.0))
    osc = {}
    floors = {"vol_window": VOL_WINDOW}
    for k in SPEED_INDEXES:
        alpha_k = 1.0 / (k * math.sqrt(2.0))
        beta_k = 1.0 / (RHO * k * math.sqrt(2.0))
        fast = _ewma(scaled_price, alpha_k)
        slow = _ewma(scaled_price, beta_k)
        sd = np.sqrt(np.maximum(_ewmvar(scaled_price, alpha_k, fast), 0.0))
        with np.errstate(divide="ignore", invalid="ignore"):
            osc[k] = np.where(sd > 0.0, (fast - slow) / sd, np.nan)
        floors["osc_%d" % k] = int(min(n - 1, SIGNAL_WARMUP_BARS))
    return {"delta": delta, "delta_scaled": dsc, "scaled_price": scaled_price,
            "sigma": sigma, "osc": osc, "floors": floors,
            "names": ["delta_scaled", "scaled_price"] + ["osc_k%d" % k for k in SPEED_INDEXES],
            "warmup_bars": SIGNAL_WARMUP_BARS}


def causality_probe(cohort, inputs, sample_bars=60):
    """Executable no-look-ahead control on the signal-input layer.

    For a deterministic sample of bars, the registered recursions are recomputed from the
    TRUNCATED prefix close[0..t] and compared with the full-window series.  Prefix invariance is
    what causality means here: a column that can see the future disagrees as soon as the future
    is removed.
    """
    close = np.asarray(cohort.close, dtype=np.float64)
    n = len(close)
    if n < SIGNAL_WARMUP_BARS + 8:
        return {"bars_probed": 0, "mismatches": 0, "columns": [],
                "note": "window shorter than the registered warm-up floor"}
    step = max(1, (n - SIGNAL_WARMUP_BARS) // sample_bars)
    probed = mismatched = 0
    bad = []
    for t in range(SIGNAL_WARMUP_BARS, n, step):
        sub = np.zeros(t + 1, dtype=np.float64)
        sub[1:] = close[1:t + 1] - close[:t]
        a_vol = 2.0 / (VOL_WINDOW + 1.0)
        mu = _ewma(sub, a_vol)
        sd = np.sqrt(np.maximum(_ewmvar(sub, a_vol, mu), 0.0))
        sc = np.where(sd > 0.0, sub / np.where(sd > 0.0, sd, 1.0), 0.0)
        sc[0] = 0.0
        ps = np.cumsum(sc)
        probed += 1
        checks = {"delta_scaled": (sc[t], inputs["delta_scaled"][t]),
                  "scaled_price": (ps[t], inputs["scaled_price"][t])}
        for k in SPEED_INDEXES:
            alpha_k = 1.0 / (k * math.sqrt(2.0))
            beta_k = 1.0 / (RHO * k * math.sqrt(2.0))
            fast = _ewma(ps, alpha_k)
            slow = _ewma(ps, beta_k)
            sdv = np.sqrt(np.maximum(_ewmvar(ps, alpha_k, fast), 0.0))
            checks["osc_k%d" % k] = ((fast[t] - slow[t]) / sdv[t] if sdv[t] > 0.0 else np.nan,
                                     inputs["osc"][k][t])
        for nm, (val, ref) in checks.items():
            same = (np.isfinite(val) and np.isfinite(ref) and abs(val - ref) <= 1e-9) or \
                   (not np.isfinite(val) and not np.isfinite(ref))
            if not same:
                mismatched += 1
                if len(bad) < 8:
                    bad.append({"bar": int(t), "column": nm, "truncated": float(val),
                                "full": float(ref)})
    if mismatched:
        counter("signal", "causality_probe_mismatch", mismatched)
    return {"bars_probed": probed, "mismatches": mismatched, "columns": sorted(checks),
            "examples": bad,
            "note": "prefix-truncation equality on the registered volatility-scaling and "
                    "oscillator columns; a non-zero mismatch count is a look-ahead defect"}


# --------------------------------------------------------------- lead-lag tensors (panel level)

def _levy_tensor(dsc_win):
    """The record's Levy area over one window, vectorised over every pair at once.

    V_ij = sum_a (Xi_a - Xi_{a-1})(Xj_{a-1} + Xj_a) - (Xj_a - Xj_{a-1})(Xi_{a-1} + Xi_a)
    with X~ the volatility-scaled cumulative price series.
    """
    # xi: (L+1, M) cumulative scaled prices of the window, dxi: (L, M) their increments
    dxi = np.diff(dsc_win, axis=0)
    lo = dsc_win[:-1, :]
    hi = dsc_win[1:, :]
    # term_i[a, i, j] = dxi[a, i] * (lo[a, j] + hi[a, j])
    t1 = dxi[:, :, None] * (lo + hi)[:, None, :]
    t2 = dxi[:, None, :] * (lo + hi)[:, :, None]
    return (t1 - t2).sum(axis=0)


def _dtw_local_cost(xi, xj, method):
    """(P, L, L) local cost (squared difference) of every pair's window under the descriptor."""
    return (xi[:, :, None] - xj[:, None, :]) ** 2


def _dtw_panel_lags(series_pairs, band):
    """Banded DTW / DDTW mode-lag for every pair of one window, diagonal-vectorised.

    All cells of one anti-diagonal of the DTW cost matrix depend only on strictly lower
    anti-diagonals, so the DP is vectorised over each anti-diagonal and over the P pairs.
    Returns (P,) integer mode-lags (mode of j_l - i_l along the optimal path) and (P,) path
    lengths.  A deterministic tie-break is used everywhere: predecessor order (up-left, up,
    left) in the DP, and (|lag|, lag) in the mode.
    """
    P, L = series_pairs[0].shape
    D = _dtw_local_cost(series_pairs[0], series_pairs[1], "dtw")
    if L == 1:
        return np.zeros(P, dtype=np.int64), np.ones(P, dtype=np.int64)
    INF = np.float64(1e18)
    Cp = np.full((P, L + 1, L + 1), INF, dtype=np.float64)
    Cp[:, 0, 0] = 0.0
    for dg in range(0, 2 * L - 1):
        i0 = max(0, dg - (L - 1))
        i1 = min(L - 1, dg)
        i_arr = np.arange(i0, i1 + 1)
        j_arr = dg - i_arr
        keep = np.abs(i_arr - j_arr) <= band
        if not keep.any():
            continue
        i_arr = i_arr[keep]
        j_arr = j_arr[keep]
        up = Cp[:, i_arr, j_arr + 1]
        left = Cp[:, i_arr + 1, j_arr]
        ul = Cp[:, i_arr, j_arr]
        best = np.minimum(np.minimum(ul, up), left)
        Cp[:, i_arr + 1, j_arr + 1] = D[:, i_arr, j_arr] + best
    lags = np.zeros(P, dtype=np.int64)
    lens = np.zeros(P, dtype=np.int64)
    for p in range(P):
        if not np.isfinite(Cp[p, L, L]):
            continue
        i, j = L - 1, L - 1
        path = []
        while True:
            path.append(j - i)
            if i == 0 and j == 0:
                break
            cand = []
            if i > 0 and j > 0:
                cand.append((Cp[p, i, j], i - 1, j - 1))
            if i > 0:
                cand.append((Cp[p, i, j + 1], i - 1, j))
            if j > 0:
                cand.append((Cp[p, i + 1, j], i, j - 1))
            if not cand:
                break
            cand.sort(key=lambda c: (c[0], c[1] - c[2]))
            _c, i, j = cand[0]
            if len(path) > 4 * L:
                break
        arr = np.array(path, dtype=np.int64)
        vals, counts = np.unique(arr, return_counts=True)
        best_count = counts.max()
        tied = vals[counts == best_count]
        order = np.lexsort((tied, np.abs(tied)))
        lags[p] = int(tied[order[0]])
        lens[p] = int(len(path))
    return lags, lens


_LL_CACHE = {}


def _cached_lead_lag(method, delta, dsc, fresh):
    """The lead-lag tensor of one (method, lookback) over the shared unshifted panel.

    Keyed on the panel's own content digest, so a shifted (permutation) panel never reads a
    cached tensor, and the twelve expensive DTW/DDTW tensors are computed once per run.
    """
    if fresh:
        return lead_lag_tensor(dsc, method, delta)
    key = (method, delta, hashlib.sha256(np.ascontiguousarray(dsc).tobytes()).hexdigest()[:16])
    t = _LL_CACHE.get(key)
    if t is None:
        t = lead_lag_tensor(dsc, method, delta)
        _LL_CACHE[key] = t
    return t


def lead_lag_tensor(dsc, method, delta_lookback):
    """(n, M, M) skew-symmetric lead-lag tensor V_t for one method and one lookback.

    Row i / column j entries carry the pairwise lead-lag of market i against market j; the
    market's own diagonal is 0 by construction (the graph step forbids it anyway).
    """
    n, M = dsc.shape
    V = np.zeros((n, M, M), dtype=np.float64)
    if method == "levy":
        # cumulative scaled price series and its increments (causal by construction)
        ps = np.cumsum(np.nan_to_num(dsc, nan=0.0), axis=0)
        inc = np.diff(ps, axis=0)                     # (n-1, M)
        lo = ps[:-1, :]
        hi = ps[1:, :]
        t1 = inc[:, :, None] * (lo + hi)[:, None, :]
        t2 = inc[:, None, :] * (lo + hi)[:, :, None]
        term = t1 - t2                                # (n-1, M, M) contribution of bar a
        cs = np.cumsum(term, axis=0)
        for t in range(delta_lookback, n):
            V[t] = cs[t - 1] - (cs[t - delta_lookback - 1] if t - delta_lookback - 1 >= 0 else 0.0)
        return V
    band = max(DTW_MIN_BAND, int(round(DTW_BAND_FRACTION * delta_lookback)))
    if method == "ddtw":
        src = ddtw_derivative_series(dsc)
        end_shift = 1          # causal slice of the centred estimator
    else:
        src = dsc
        end_shift = 0
    M_pairs = [(i, j) for i in range(M) for j in range(M) if i != j]
    for t in range(delta_lookback, n):
        t_end = t - end_shift
        t_start = t_end - delta_lookback + 1
        if t_start < 0:
            continue
        xi = np.stack([src[t_start:t_end + 1, i] for i, _j in M_pairs], axis=0)
        xj = np.stack([src[t_start:t_end + 1, j] for _i, j in M_pairs], axis=0)
        # A descriptor slice that still carries the derivative estimator's leading NaN is not a
        # DTW failure: the window lies inside the registered signal warm-up and its bar can never
        # open an episode.  Such windows are counted apart from real empty paths, and the
        # registered assertion requires every skipped window to sit below the warm-up floor.
        defined = np.isfinite(xi).all(axis=1) & np.isfinite(xj).all(axis=1)
        if not defined.all():
            for p, ok in enumerate(defined):
                if not ok:
                    counter("signal", "dtw_warmup_window_skipped")
                    if t >= SIGNAL_WARMUP_BARS:
                        counter("signal", "dtw_warmup_window_outside_warmup")
        lags, lens = _dtw_panel_lags((xi, xj), band)
        for p, (i, j) in enumerate(M_pairs):
            if not defined[p]:
                continue
            if lens[p] == 0:
                counter("signal", "dtw_path_empty")
                continue
            V[t, i, j] = float(lags[p])
    return V


def ddtw_derivative_series(dsc):
    """The registered DDTW derivative estimate D_i = ((x_i - x_{i-2}) + (x_{i+1} - x_{i-1})/2)/2.

    The estimate is centred (it reads x_{i+1}); the tensor builder therefore slices the window
    one bar earlier so no value used by a signal at bar t depends on a bar after t.  Rows below
    the floor are NaN.
    """
    n, M = dsc.shape
    d = np.full((n, M), np.nan, dtype=np.float64)
    for i in range(2, n - 1):
        d[i] = ((dsc[i] - dsc[i - 2]) + (dsc[i + 1] - dsc[i - 1]) / 2.0) / 2.0
    return d


def graph_adjacency(V, beta=None):
    """The row-normalised closed-form solution of the registered convex graph problem.

    W_ij = ||v_i - v_j||^2 with v_i the (max-abs normalised) i-th row profile of V;
    the row-normalised optimum is  A~_ij ∝ 1/(W_ij^2 + beta),  A~_ii = 0.
    Accepts one (M, M) matrix or a stack of them; returns the same shape.
    """
    b = GRAPH_BETA if beta is None else float(beta)
    single = (V.ndim == 2)
    Vs = V[None, :, :] if single else V
    scale = np.max(np.abs(Vs), axis=(1, 2), keepdims=True)
    scale = np.where(scale > 0.0, scale, 1.0)
    v = Vs / scale
    diff = v[:, :, None, :] - v[:, None, :, :]
    W = (diff ** 2).sum(axis=-1)
    K = 1.0 / (W ** 2 + b)
    idx = np.arange(Vs.shape[1])
    K[:, idx, idx] = 0.0
    rs = K.sum(axis=2, keepdims=True)
    out = np.divide(K, rs, out=np.zeros_like(K), where=rs > 0.0)
    return out[0] if single else out


def graph_adjacency_iterative(V, beta=None, iters=4000, step=0.05):
    """Projected-gradient reference solve of the SAME objective (test/reference only).

    Kept in the engine so the closed form above can be cross-checked inside the container; the
    production path never calls it.
    """
    b = GRAPH_BETA if beta is None else float(beta)
    scale = float(np.max(np.abs(V))) if np.max(np.abs(V)) > 0 else 1.0
    v = V / scale
    diff = v[:, None, :] - v[None, :, :]
    W2 = ((diff ** 2).sum(axis=-1)) ** 2
    alpha = GRAPH_ALPHA
    A = np.full(W2.shape, 0.05, dtype=np.float64)
    idx = np.arange(W2.shape[0])
    A[idx, idx] = 0.0
    for _ in range(iters):
        deg = A.sum(axis=1)
        deg = np.maximum(deg, 1e-9)
        grad = A * (W2 + b) - alpha / deg[:, None]
        A = np.maximum(A - step * grad, 0.0)
        A[idx, idx] = 0.0
    rs = A.sum(axis=1, keepdims=True)
    return np.divide(A, rs, out=np.zeros_like(A), where=rs > 0.0)


def reverting_sigmoid(x):
    """The record's reverting sigmoid response r(x) = c_lambda x exp(-x^2/(2 lambda^2))."""
    return C_LAMBDA * x * np.exp(-(x * x) / (2.0 * SIGMOID_LAMBDA * SIGMOID_LAMBDA))


class Panel:
    """The registered four-instrument panel of one strategy case.

    Holds the shared lead-lag tensors, the shared row-normalised adjacency tensors and the
    per-market score / target-state paths.  One panel serves all four cohorts (the universe IS
    the panel), so the expensive lead-lag work happens once per case.
    """

    _cache = {}

    def __init__(self, case, cohorts, shifts=None):
        self.case = tuple(case)
        self.case_label = case_name(self.case)
        self.method = LL_METHODS[case_method(self.case)]
        self.lookback = LOOKBACK_LABELS[case_lookback(self.case)]
        labels = sorted(cohorts)
        assert len(labels) == PANEL_SIZE, "the registered panel needs exactly %d instruments" % PANEL_SIZE
        self.markets = labels
        ds = [cohorts[m].inputs["delta_scaled"] for m in labels]
        self.n = min(len(x) for x in ds)
        assert all(len(x) == self.n for x in ds), "panel series must share one bar grid"
        self.dsc = np.stack([np.asarray(x[:self.n], dtype=np.float64) for x in ds], axis=1)
        self.osc = {k: np.stack([np.asarray(cohorts[m].inputs["osc"][k][:self.n],
                                            dtype=np.float64) for m in labels], axis=1)
                    for k in SPEED_INDEXES}
        self.shifts = None if shifts is None else [int(s) for s in shifts]
        if self.shifts:
            # the registered permutation test: one independent CIRCULAR shift per market, which
            # leaves every market's own autocorrelation exactly intact and destroys the
            # cross-sectional alignment (the wrap perturbation is disclosed in the round-spec)
            self.dsc = np.stack([np.roll(self.dsc[:, i], self.shifts[i])
                                 for i in range(len(labels))], axis=1)
            self.osc = {k: np.stack([np.roll(self.osc[k][:, i], self.shifts[i])
                                     for i in range(len(labels))], axis=1)
                        for k in SPEED_INDEXES}
        deltas = [int(e) for e in (LOOKBACKS if self.lookback == "ensemble" else [int(self.lookback)])]
        self.V = {d: _cached_lead_lag(self.method, d, self.dsc, self.shifts is not None)
                  for d in deltas}
        self.A = {d: np.stack([graph_adjacency(self.V[d][t]) for t in range(self.n)], axis=0)
                  for d in deltas}
        if self.lookback == "ensemble":
            A_use = np.mean(np.stack([self.A[d] for d in deltas], axis=0), axis=0)
        else:
            A_use = self.A[deltas[0]]
        self.A_use = A_use
        K = len(SPEED_INDEXES)
        score = np.zeros((self.n, len(labels)), dtype=np.float64)
        for k in SPEED_INDEXES:
            R = self.osc[k]
            Rnet = np.einsum("tmn,tn->tm", A_use, R)
            score += reverting_sigmoid(Rnet)
        score /= float(K)
        score[:SIGNAL_WARMUP_BARS, :] = np.nan
        self.score = score
        self.state = np.where(np.isfinite(score), np.sign(score), 0.0).astype(np.int64)
        self.events = np.zeros_like(self.state)
        prev = np.zeros(self.state.shape[1], dtype=np.int64)
        for t in range(self.n):
            cur = self.state[t]
            fresh = (cur != prev) & (cur != 0)
            self.events[t] = np.where(fresh, cur, 0)
            prev = cur
        self.diag = {
            "case_name": self.case_label, "method": self.method, "lookback": self.lookback,
            "panel": labels, "bars": int(self.n),
            "warmup_bars": SIGNAL_WARMUP_BARS,
            "adjacency_row_mass_min": float(np.min(A_use.sum(axis=2))),
            "lead_lag_abs_mean": {str(d): float(np.mean(np.abs(self.V[d][SIGNAL_WARMUP_BARS:])))
                                  for d in deltas},
            "dtw_band": (None if self.method == "levy" else
                         {str(d): max(DTW_MIN_BAND, int(round(DTW_BAND_FRACTION * d)))
                          for d in deltas}),
            "market_state_bars": {m: {"long": int((self.state[:, i] > 0).sum()),
                                      "short": int((self.state[:, i] < 0).sum()),
                                      "events": int((self.events[:, i] != 0).sum())}
                                  for i, m in enumerate(labels)},
            "policy_note": "the directional asymmetry of V enters the aggregation only through "
                           "the distance weighting W of the registered graph problem; a "
                           "leader-only neighbour-set variant is NOT evaluated (disclosed)",
        }

    def layer_for(self, market):
        i = self.markets.index(market)
        return {"pos": np.ascontiguousarray(self.state[:, i]),
                "events": np.ascontiguousarray(self.events[:, i]),
                "yhat": np.ascontiguousarray(self.score[:, i])}

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
    """One (cohort, case) signal layer: the panel's per-market score, target state and entry
    events.  Built once per case and cached (the DCA grid re-reads it 48x per phase grid)."""

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
        self.diag = dict(panel.diag)
        self.diag["cohort"] = cohort.symbol
        self.diag["market_state_bars"] = panel.diag["market_state_bars"][cohort.symbol]

    def report(self):
        return {"case_name": self.case_label, "method": self.diag["method"],
                "lookback": self.diag["lookback"],
                "market_state_bars": self.diag["market_state_bars"],
                "warmup_bars": self.diag["warmup_bars"]}


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


class BaselineLayer:
    """The record's own BASELINE reading: the same six-speed oscillator chain with NO network
    aggregation (the univariate MACD analogue the record compares against).  Used only by the
    registered family-level falsification reader; never a cohort, never a case."""

    def __init__(self, cohort):
        K = len(SPEED_INDEXES)
        score = np.zeros(cohort.n, dtype=np.float64)
        for k in SPEED_INDEXES:
            score += reverting_sigmoid(cohort.inputs["osc"][k])
        score /= float(K)
        score[:SIGNAL_WARMUP_BARS] = np.nan
        state = np.where(np.isfinite(score), np.sign(score), 0.0).astype(np.int64)
        events = np.zeros_like(state)
        prev = 0
        for t in range(cohort.n):
            cur = int(state[t])
            if cur != prev and cur != 0:
                events[t] = cur
            prev = cur
        self.pos, self.events, self.yhat = state, events, score


def baseline_layer_for(cohort):
    lay = cohort._baseline_cache.get("macd")
    if lay is None:
        lay = BaselineLayer(cohort)
        cohort._baseline_cache["macd"] = lay
    return lay


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
    # Trace-only per-episode bookkeeping (contract 28.2): computed alongside the engine's
    # own ledgers and never read back by the engine.  `ep_reason` / `ep_trigger` / `ep_ref`
    # are set at the exit that closes the episode and read only at the one emit point below.
    ep_mae = ep_mfe = 0.0
    ep_reason = ep_trigger = ep_ref = None

    def charge_fee(amount):
        """A fee is paid at the instant of the fill and must reduce the realised equity there."""
        nonlocal realized, fees_total
        fees_total += amount
        realized -= amount
        return amount

    if not episodes:
        series_flat = _flat_series(day_equity)
        if TRACE is not None:
            _trace_equity(kind, cohort, i0, day_local, series_flat, day_equity)
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
        if TRACE is not None:
            ep_mae = 0.0
            ep_mfe = 0.0
            _trace("fill", episode=windows_entered - 1, event_type="ENTRY",
                   bar_index=i0 + eb, open_time_ms=entry_ms, price=px, qty=qty, dca_level=0,
                   trigger_price=None, ref_price=O[eb], fee=qty * px * taf,
                   slip_ticks=slip_ticks, sign=sign)
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
                    if TRACE is not None:
                        _trace("fill", episode=windows_entered - 1, event_type="DCA_ADD",
                               bar_index=i0 + t, open_time_ms=int(cohort.open_time_ms[i0 + t]),
                               price=fpx, qty=q, dca_level=kk, trigger_price=trig,
                               ref_price=trig, fee=q * fpx * taf, slip_ticks=slip_ticks,
                               sign=sign)
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
                    if TRACE is not None:
                        _trace("fill", episode=windows_entered - 1, event_type="DCA_ADD",
                               bar_index=i0 + t, open_time_ms=int(cohort.open_time_ms[i0 + t]),
                               price=fpx, qty=q, dca_level=kk, trigger_price=trig,
                               ref_price=trig, fee=q * fpx * taf, slip_ticks=slip_ticks,
                               sign=sign)
                    fills += 1
                    turnover += q * fpx
                    layers[kk] += 1
                    if kk == MAX_ADD_LEVELS:
                        counter(kind, "ladder_cap_reached")
                    kk += 1
            eq = START_EQUITY + realized
            ueq = eq + sign * (qty * C[t] - cost)
            if TRACE is not None:
                excursion = ueq - eq
                ep_mae = min(ep_mae, excursion)
                ep_mfe = max(ep_mfe, excursion)
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
                if TRACE is not None:
                    ep_reason, ep_trigger, ep_ref = "MARGIN_CALL", C[t], C[t]
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
                if TRACE is not None:
                    ep_reason, ep_trigger, ep_ref = "STOP", killed_at, L[t]
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
                if TRACE is not None:
                    ep_reason, ep_trigger, ep_ref = "TP", tpx, H[t]
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
                    if TRACE is not None:
                        ep_reason, ep_trigger, ep_ref = "TIME_EXIT", None, O[t + 1]
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
            if TRACE is not None:
                ep_reason, ep_trigger, ep_ref = "EOD_FLATTEN", C[-1], C[-1]
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
        if TRACE is not None:
            exit_bar_index = i0 + exit_bar + (1 if exit_at_open else 0)
            _trace("fill", episode=windows_entered - 1,
                   event_type="FLATTEN" if ep_reason == "EOD_FLATTEN" else "EXIT",
                   bar_index=exit_bar_index, open_time_ms=exit_ms, price=xpx, qty=qty,
                   dca_level=kk, trigger_price=ep_trigger, ref_price=ep_ref,
                   fee=qty * xpx * taf, slip_ticks=slip_ticks, sign=sign)
            _trace("episode", episode=windows_entered - 1, exit_reason=ep_reason,
                   entry_bar_index=i0 + eb, entry_time_ms=entry_ms, entry_price=p0,
                   exit_bar_index=exit_bar_index, exit_time_ms=exit_ms, exit_price=xpx,
                   gross_pnl=ep_gross, fees=ep_fees, funding=ep_fund,
                   net_pnl=ep_gross - ep_fees - ep_fund,
                   holding_bars=exit_bar_index - (i0 + eb), layers_used=kk,
                   mae_usdt=ep_mae, mfe_usdt=ep_mfe, sign=sign)
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
    if TRACE is not None:
        _trace_equity(kind, cohort, i0, day_local, series_flat, day_equity)
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


def permutation_test_check(spec, cohort_results, diag_inputs):
    """Reader 1 - record falsification item 1 ("Synthetic Lead-Lag Permutation Test"): "Randomly
    shuffle cross-sectional time alignment across commodities while keeping individual
    autocorrelation intact.  Falsification threshold: if NMM net Sharpe fails to exceed the
    shuffled baseline by at least +0.05 (p < 0.01), reject the hypothesis that cross-sectional
    lead-lag spillover drives the edge."

    Definition: the elected winner's (case, DCA) cell is re-simulated over the FULL window once
    on the real panel and once per draw on a panel whose four market series were CIRCULARLY
    shifted by independent uniform offsets - a shift leaves every market's own autocorrelation
    exactly intact while destroying the cross-sectional alignment.  The registered test is the
    one-sided paired t-test of (real - shuffled) over the N_PERM_DRAWS draws per cohort at
    alpha = 0.01 (the record fixes the threshold, not the test machinery; the empirical
    exceedance count is reported alongside and the circular wrap is disclosed).  TRIGGERED when
    the mean surplus over the shuffled baseline is < +0.05 net Sharpe, or the paired test does
    not reach p < 0.01.
    """
    import random
    from math import sqrt
    out = {"registered_item": "synthetic-lead-lag-permutation-test", "evaluated": False,
           "hit": False, "landing": "family-level: a hit must NEVER be recorded as PASS",
           "draws": N_PERM_DRAWS, "seed": PERM_SEED, "alpha": PERM_ALPHA,
           "margin_sharpe": PERM_MARGIN_SHARPE, "cohorts": {},
           "method_note": "one circular per-market shift set per DRAW, applied to the shared "
                          "panel and then measured on every cohort's own winner cell "
                          "(autocorrelation preserved exactly, cross-sectional alignment "
                          "destroyed); a block-shuffle variant is not evaluated"}
    winners = {}
    real_sharpe = {}
    for c in cohort_results:
        if c["winner"] is None:
            continue
        winner_case, dca = _winner_case(c, spec)
        cohort, series = diag_inputs[c["cohort"]]
        slip = spec["costs"]["baseline_slippage_ticks"]
        full = cohort.slice(spec["data"]["start"], spec["data"]["end"])
        p_win = params_of(c["winner"])
        real = simulate(cohort, p_win, rail_for(dca), full, {}, slip, "perm_real_diag",
                        series, count_layers=False)
        real_sharpe[c["cohort"]] = float(real["sharpe"])
        winners[c["cohort"]] = {"case": tuple(winner_case), "dca": dca, "p": p_win,
                                "cohort": cohort, "series": series, "full": full,
                                "slip": slip, "real_net": float(real["net_pnl"]),
                                "case_name": c.get("winner_case_label")}
    draws = {k: [] for k in winners}
    rng = random.Random(PERM_SEED)
    for _i in range(N_PERM_DRAWS):
        shifts = [rng.randrange(next(iter(winners.values()))["cohort"].n)
                  for _k in range(PANEL_SIZE)] if winners else []
        panels = {}
        for label, w in winners.items():
            key = tuple(w["case"])
            if key not in panels:
                try:
                    panel = Panel(key, PANEL_COHORTS, shifts=shifts)
                except SystemExit:
                    counter("perm_diag", "shuffled_signal_undefined")
                    panel = None
                panels[key] = panel
            if panel is None:
                draws[label].append(0.0)
                continue
            layer = SignalLayer(w["cohort"], key, spec, panel=panel)
            m = with_layer(w["cohort"], key, layer,
                           lambda w=w: simulate(w["cohort"], w["p"], rail_for(w["dca"]),
                                                w["full"], {}, w["slip"], "perm_shift_diag",
                                                w["series"], count_layers=False))
            draws[label].append(float(m["sharpe"]))
    for label, w in winners.items():
        shuffled = draws[label]
        n = len(shuffled)
        mean_sh = sum(shuffled) / float(n) if n else 0.0
        diffs = [real_sharpe[label] - s for s in shuffled]
        dbar = sum(diffs) / float(n) if n else 0.0
        if n > 1:
            var = sum((d - dbar) ** 2 for d in diffs) / float(n - 1)
            sd = sqrt(var)
            tstat = (dbar / (sd / sqrt(n))) if sd > 0.0 else (float("inf") if dbar > 0 else 0.0)
        else:
            sd, tstat = 0.0, 0.0
        # one-sided t critical value at alpha for the registered draw count (24 df = 23)
        tcrit = 2.4999
        exceeds = sum(1 for s in shuffled if real_sharpe[label] > s + PERM_MARGIN_SHARPE)
        hit = bool(dbar < PERM_MARGIN_SHARPE or tstat < tcrit)
        out["evaluated"] = True
        out["hit"] = out["hit"] or hit
        out["cohorts"][label] = {
            "case_name": w["case_name"],
            "real_sharpe": round(real_sharpe[label], 6),
            "real_net_pnl": round(w["real_net"], 6),
            "shuffled_mean_sharpe": round(mean_sh, 6),
            "mean_surplus_sharpe": round(dbar, 6),
            "t_stat": round(tstat, 6), "t_crit_alpha01": tcrit, "draws": n,
            "empirical_exceedances": exceeds,
            "empirical_p_upper": round((exceeds + 1) / float(n + 1), 6),
            "hit": hit}
    return out


def execution_lag_check(spec, cohort_results, diag_inputs, rows_by_cohort):
    """Reader 2 - record falsification item 2 ("Execution Lag & Slippage Sensitivity"): "Delay
    execution by 1 additional trading bar (rebalance at t+2).  Falsification threshold: if > 50%
    of the Sharpe enhancement over MACD dissipates, reject deployability under execution latency."

    Definition: the record's own univariate baseline is the identical six-speed oscillator chain
    WITHOUT the network aggregation (the univariate MACD analogue the record reports against).
    For the elected winner's cell the reader measures, on the FULL window: (a) the network
    signal, (b) the univariate baseline, (c) the network signal with the registered
    `entry_delay_1_bar` stress, (d) the registered `slippage_2ticks` stress.  The enhancement is
    (a) - (b); TRIGGERED when a positive enhancement dissipates by more than 50% under (c).
    """
    out = {"registered_item": "execution-lag-and-slippage-sensitivity", "evaluated": False,
           "hit": False, "landing": "family-level: a hit must NEVER be recorded as PASS",
           "dissipation_threshold": LAG_DISSIPATION_FRACTION, "cohorts": {}}
    for c in cohort_results:
        if c["winner"] is None:
            continue
        winner_case, dca = _winner_case(c, spec)
        cohort, series = diag_inputs[c["cohort"]]
        slip = spec["costs"]["baseline_slippage_ticks"]
        full = cohort.slice(spec["data"]["start"], spec["data"]["end"])
        p_win = params_of(c["winner"])
        rail = rail_for(dca)
        m_nmm = simulate(cohort, p_win, rail, full, {}, slip, "lag_nmm_diag", series,
                         count_layers=False)
        m_delay = simulate(cohort, p_win, rail, full, {"entry_delay_1_bar": True}, slip,
                           "lag_delay_diag", series, count_layers=False)
        m_slip = simulate(cohort, p_win, rail, full, {"slip_ticks": 2}, 2, "lag_slip_diag",
                          series, count_layers=False)
        base = baseline_layer_for(cohort)
        m_macd = with_layer(cohort, tuple(winner_case), base,
                            lambda: simulate(cohort, p_win, rail, full, {}, slip,
                                             "lag_macd_diag", series, count_layers=False))
        enh = float(m_nmm["sharpe"]) - float(m_macd["sharpe"])
        enh_delay = float(m_delay["sharpe"]) - float(m_macd["sharpe"])
        dissip = None if enh <= 0.0 else (1.0 - enh_delay / enh)
        hit = bool(enh > 0.0 and dissip is not None and dissip > LAG_DISSIPATION_FRACTION)
        grid_row = _cell(rows_by_cohort, c["cohort"], "entry_delay_1_bar", winner_case, dca)
        out["evaluated"] = True
        out["hit"] = out["hit"] or hit
        out["cohorts"][c["cohort"]] = {
            "case_name": c.get("winner_case_label"),
            "nmm_sharpe": round(float(m_nmm["sharpe"]), 6),
            "macd_baseline_sharpe": round(float(m_macd["sharpe"]), 6),
            "enhancement_over_macd": round(enh, 6),
            "enhancement_positive": bool(enh > 0.0),
            "delayed_sharpe": round(float(m_delay["sharpe"]), 6),
            "delayed_enhancement": round(enh_delay, 6),
            "dissipated_fraction": None if dissip is None else round(dissip, 6),
            "slippage_2ticks_sharpe": round(float(m_slip["sharpe"]), 6),
            "nmm_net_pnl": round(float(m_nmm["net_pnl"]), 6),
            "macd_net_pnl": round(float(m_macd["net_pnl"]), 6),
            "cross_check_vs_entry_delay_grid": {
                "grid_net_pnl": None if grid_row is None else grid_row["net_pnl"],
                "reader_net_pnl": round(float(m_delay["net_pnl"]), 6),
                "matches": bool(grid_row is not None
                                and abs(float(grid_row["net_pnl"])
                                        - float(m_delay["net_pnl"])) < 1e-6)},
            "hit": hit}
    return out


def dtw_ablation_check(spec, cohort_results, diag_inputs):
    """Reader 3 - record falsification item 3 ("Dynamic Time Warping Descriptor Ablation"):
    "Test whether simple Euclidean distance matches shapeDTW/shapeDDTW performance across
    volatile commodity shock regimes.  Falsification threshold: if DDTW fails to outperform
    standard DTW in turbulent sub-periods, reject the necessity of derivative warping."

    Definition: inside every registered local shock window (the record's commodity shock regimes
    are outside the canonical store; the registered local analogues are listed in the round-spec)
    and for every cohort, the elected winner's DCA config is run for all twelve DTW-family cases
    (dtw and ddtw x the six lookbacks) and compared by the MEDIAN net Sharpe of each descriptor.
    TRIGGERED when the DDTW median net Sharpe does not exceed the standard DTW median in the
    majority of the registered (cohort x shock window) cells.
    """
    out = {"registered_item": "dtw-descriptor-ablation", "evaluated": False, "hit": False,
           "landing": "family-level: a hit must NEVER be recorded as PASS",
           "shock_windows": [{"name": n, "start": a, "end": b} for n, a, b in SHOCK_WINDOWS],
           "cohorts": {}}
    cells_dtw_better = 0
    cells_total = 0
    for c in cohort_results:
        if c["winner"] is None:
            continue
        _wcase, dca = _winner_case(c, spec)
        cohort, series = diag_inputs[c["cohort"]]
        slip = spec["costs"]["baseline_slippage_ticks"]
        rail = rail_for(dca)
        per_window = {}
        for name, a, b in SHOCK_WINDOWS:
            sl = cohort.slice(a, b)
            med = {}
            for method in ("dtw", "ddtw"):
                sh = []
                for lb in LOOKBACK_LABELS:
                    case = case_fields_for(method, lb)
                    m = simulate(cohort, params_dict(case), rail, sl, {}, slip,
                                 "%s_diag" % method, series, count_layers=False)
                    sh.append(float(m["sharpe"]))
                med[method] = sorted(sh)[len(sh) // 2]
            cells_total += 1
            dtw_better = bool(med["ddtw"] > med["dtw"])
            if dtw_better:
                cells_dtw_better += 1
            per_window[name] = {"window": [a, b], "bars": sl[1] - sl[0],
                                "ddtw_median_sharpe": round(med["ddtw"], 6),
                                "dtw_median_sharpe": round(med["dtw"], 6),
                                "ddtw_outperforms_dtw": dtw_better}
        out["evaluated"] = True
        out["cohorts"][c["cohort"]] = {"case_name": c.get("winner_case_label"),
                                       "windows": per_window}
    majority = (cells_dtw_better * 2 > cells_total) if cells_total else False
    out["cells_total"] = cells_total
    out["cells_ddtw_outperforms"] = cells_dtw_better
    out["hit"] = bool(out["evaluated"] and not majority)
    out["majority_rule_note"] = ("triggered when DDTW does NOT out-perform standard DTW in the "
                                 "majority of the registered cohort x shock-window cells")
    if not cells_total:
        out["hit"] = False
    return out


def oos_universe_expansion_check(spec, cohort_results, diag_inputs):
    """Reader 4 - record falsification item 4 ("Out-of-Sample Portfolio Expansion"): evaluate on
    20 liquid crypto perpetual contracts and 30 international sovereign bond futures.

    Definition: NOT EXECUTED.  The canonical local store holds exactly four USD-M perpetual
    contracts and no sovereign bond futures; the card's operator override registers those four as
    the round's eligible universe and forbids shrinking or substituting a universe, so the
    expansion cannot be executed and is recorded as not_executed rather than lowered, removed or
    simulated on a shrunken panel.
    """
    return {"registered_item": "out-of-sample-universe-expansion", "evaluated": False,
            "hit": False, "status": "not_executed",
            "landing": "family-level: a hit must NEVER be recorded as PASS",
            "required_universe": "20 liquid crypto perpetual contracts and 30 international "
                                 "sovereign bond futures",
            "local_available": "4 USD-M perpetual contracts (BTCUSDT/ETHUSDT/BNBUSDT/SOLUSDT), "
                               "0 sovereign bond futures",
            "reason": "the registered local eligible universe is the four canonical instruments "
                      "(operator override, card t_5551afc1); a shrunken or substituted universe "
                      "is forbidden, so the expansion is not executed rather than weakened"}


def case_fields_for(method, lookback_label):
    """The registered one-hot case tuple of (estimator, lookback) - the inverse of case_method /
    case_lookback, used by the ablation reader."""
    i = LL_METHODS.index(method)
    j = LOOKBACK_LABELS.index(str(lookback_label))
    return tuple([1 if a == i else 0 for a in range(len(LL_METHODS))]
                 + [1 if b == j else 0 for b in range(len(LOOKBACK_LABELS))])


def params_dict(case):
    """A registered case tuple -> the strategy params dict the engine consumes."""
    return {f: int(v) for f, v in zip(CASE_FIELDS, case)}


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
    permutation = (permutation_test_check(spec, cohort_results, diag_inputs)
                   if coverage_complete
                   else dict(empty_reader, registered_item="synthetic-lead-lag-permutation-test"))
    execution_lag = (execution_lag_check(spec, cohort_results, diag_inputs, rows_by_cohort)
                     if coverage_complete
                     else dict(empty_reader, registered_item="execution-lag-and-slippage-sensitivity"))
    dtw_ablation = (dtw_ablation_check(spec, cohort_results, diag_inputs)
                    if coverage_complete
                    else dict(empty_reader, registered_item="dtw-descriptor-ablation"))
    expansion = (oos_universe_expansion_check(spec, cohort_results, diag_inputs)
                 if coverage_complete
                 else dict(empty_reader, registered_item="out-of-sample-universe-expansion"))
    flags = {"synthetic_lead_lag_permutation_test": bool(permutation.get("hit")),
             "execution_lag_and_slippage_sensitivity": bool(execution_lag.get("hit")),
             "dtw_descriptor_ablation": bool(dtw_ablation.get("hit")),
             "out_of_sample_universe_expansion": bool(expansion.get("hit"))}

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
        # Strategy J registered guards (names frozen in the round-spec)
        "entry_bar_is_the_next_bar_after_the_signal":
            counters_total("entry_bar_not_the_next_bar_after_the_signal") == 0,
        "no_entry_without_a_defined_signal": counters_total("entry_before_a_defined_signal") == 0,
        "no_dtw_path_was_empty_for_a_defined_window": counters_total("dtw_path_empty") == 0,
        "dtw_skipped_windows_are_inside_the_registered_warmup":
            counters_total("dtw_warmup_window_outside_warmup") == 0,
        "graph_kernel_never_degenerate": counters_total("graph_kernel_degenerate") == 0,
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
                         "lead_lag_methods": list(LL_METHODS),
                         "lookbacks": list(LOOKBACK_LABELS),
                         "signal_constants": {"vol_window": VOL_WINDOW,
                                              "speed_indexes": list(SPEED_INDEXES), "rho": RHO,
                                              "sigmoid_lambda": SIGMOID_LAMBDA,
                                              "c_lambda": C_LAMBDA, "graph_alpha": GRAPH_ALPHA,
                                              "graph_beta": GRAPH_BETA,
                                              "dtw_band_fraction": DTW_BAND_FRACTION,
                                              "dtw_min_band": DTW_MIN_BAND,
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
        "synthetic_lead_lag_permutation_test": permutation,
        "execution_lag_and_slippage_sensitivity": execution_lag,
        "dtw_descriptor_ablation": dtw_ablation,
        "out_of_sample_universe_expansion": expansion,
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
                raise SystemExit("run-spec is not a Strategy J spec: missing %r (contract 7.2/7.3)"
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
                                          "dtw_band": p.diag["dtw_band"]}
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
                          {"synthetic_lead_lag_permutation_test":
                               summary["synthetic_lead_lag_permutation_test"],
                           "execution_lag_and_slippage_sensitivity":
                               summary["execution_lag_and_slippage_sensitivity"],
                           "dtw_descriptor_ablation": summary["dtw_descriptor_ablation"],
                           "out_of_sample_universe_expansion":
                               summary["out_of_sample_universe_expansion"],
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
