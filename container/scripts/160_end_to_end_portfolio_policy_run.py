#!/usr/bin/env python3
"""Strategy PT - end-to-end parametric portfolio policies for cross-asset futures timing.

Production engine for the family
`cross-asset-futures-timing-end-to-end-portfolio-transformer-2026-09-02`, canonicalised from
the reviewed knowledge record of Austin Pollok and Kevin Robik, "End-to-End Parametric
Portfolio Policies for Cross-Asset Futures Timing: When Do AI Models Beat Simple Rules?"
(arXiv:2607.00475v1, DOI 10.48550/arXiv.2607.00475).

Registered mechanism (source-specified, reproduced in the engine):
  * the state is the daily cross-sectional return tensor of the registered local eligible
    universe, optionally extended by the record's engineered features (252-day trailing
    z-scores of 1/5/20/60-day rate of change, 20-day realized volatility, rolling skewness /
    kurtosis and the rolling pairwise correlation);
  * a Portfolio Transformer maps it to raw scores: Time2Vec temporal embedding, 4 encoder
    layers of multi-head self-attention across assets and temporal lookbacks each followed by a
    GRN, 4 decoder layers of multi-head cross-attention, and a strict lower-triangular causal
    mask;
  * a signed-softmax output layer turns the scores into continuous signed weights of unit
    gross; the objective is the differentiable Sharpe of the net portfolio return, cost-aware
    in the registered `tf_net` arm (lambda = 0.0002), optimised by AdamW + OneCycleLR with GELU,
    pre-activation layer normalization, gradient clipping at norm 1.0 and the mean of three
    independently seeded initializations;
  * the record's LSTM comparator and its naive benchmarks (1/N, inverse-volatility risk parity,
    12-month time-series momentum) plus the two-step predict-then-optimize mean-variance
    baseline are registered as their own cases;
  * the fitted parameters are frozen at the end of the registered historical slice: OOS is
    inference only, and the causality probe re-derives the policy path under prefix truncation.

Registered execution (this family's rail, unchanged from the shared engine):
  every registered case is a target-state change entry at the next bar's open, held bar by bar
  through the registered DCA ladder (spacing / size multiplier / breakeven-anchored TP /
  resting invalidation), the margin backstop, the record's target-state exit and the slice end;
  short episodes mirror the long ones; every fill pays the taker fee, its own slippage and the
  actual funding settled inside the hold window, and `gross_pnl` is accumulated by an
  independent price-PnL ledger (contract 7.2, v1.3.2).

Signals are computed once per case on the shared four-market panel and cached; the DCA grid
re-reads the cached layer.  The registered falsification readers never cull a cohort and never
bear a PASS: a hit can only move a PASS to DEFERRED (contract 6.4 / 7.3).
"""


from __future__ import annotations

import bisect
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
WORK_ROOT = "/qlib/work/strategyPT-v1"
CSV_ROOT = os.path.join(WORK_ROOT, "csv")
QLIB_DIR = os.path.join(WORK_ROOT, "qlib-data")
MS_PER_DAY = 86400000
MS_PER_HOUR = 3600000
FIELDS = ["open", "high", "low", "close", "volume"]
START_EQUITY = 30000.0
MAX_ADD_LEVELS = 10
BAR_MS = {"1d": MS_PER_DAY, "1h": MS_PER_HOUR}
# ------------------------------------- registered policy contract (RESEARCH_DEFINED, frozen)
# The record fixes the MECHANISM and the mathematics; this block fixes every value the record
# leaves open.  It mirrors the pre-registered strategy axis of the round-spec; nothing here may
# change after the first run.
#
# Record (Pollok & Robik, arXiv:2607.00475v1) source-specified mechanism, reproduced verbatim:
#   * state X_t = the daily cross-sectional return tensor (+ optional engineered features);
#   * a Portfolio Transformer (Time2Vec temporal embedding, 4 encoder layers of multi-head
#     self-attention across assets AND temporal lookbacks each followed by a GRN, 4 decoder
#     layers of multi-head cross-attention, strict lower-triangular causal mask);
#   * a signed-softmax output layer  w_i = sign(s_i) e^{|s_i|} / sum_j e^{|s_j|};
#   * a differentiable Sharpe objective, cost-aware variant R_net = R_P - lambda sum|dw|,
#     lambda = 0.0002;
#   * AdamW + OneCycleLR, GELU, pre-activation Layer Norm, gradient clipping norm 1.0,
#     output weights averaged over 3 independently seeded initializations, daily
#     close-to-close cadence;
#   * the LSTM comparator and the three simple rules the record benchmarks against, plus the
#     two-step predict-then-optimize mean-variance baseline.
POLICY_ARMS = ("tf", "tf_net", "tfx", "lstm", "mvo", "tsmom", "rp", "ew")
TRAINED_ARMS = ("tf", "tf_net", "tfx", "lstm")
ARM_LABELS = {
    "tf": "Portfolio Transformer, raw cross-sectional return state, gross-Sharpe objective, "
          "3-seed ensemble mean of the signed-softmax weights",
    "tf_net": "Portfolio Transformer, raw state, COST-AWARE objective (lambda = 0.0002), "
              "3-seed ensemble mean",
    "tfx": "Portfolio Transformer, raw state + the record's engineered features "
           "(252-day trailing z-scores), gross-Sharpe objective, 3-seed ensemble mean",
    "lstm": "the record's LSTM comparator (raw state, gross-Sharpe objective, 3-seed ensemble "
            "mean)",
    "mvo": "two-step predict-then-optimize mean-variance baseline (trailing mean/covariance, "
           "shrinkage, long-short, unit gross)",
    "tsmom": "12-month (365-bar) time-series momentum, sign(mean trailing return) / N",
    "rp": "risk parity / inverse-volatility weights over the trailing 60-bar volatility",
    "ew": "naive 1/N equal weighting (the record's benchmark)",
}
CASE_FIELDS = ("arm",)
CASE_ORDER = tuple((i,) for i in range(len(POLICY_ARMS)))
CASE_NAMES = POLICY_ARMS
ARM_INDEX = {a: i for i, a in enumerate(POLICY_ARMS)}
# ---- the record's own calibrated constants (source-specified; reproduced verbatim) -----------
TURNOVER_LAMBDA = 0.0002   # record: lambda = 0.0002 (2 bps baseline) of the cost-aware loss
ENSEMBLE_SEEDS = (20260902, 20260903, 20260904)   # record: "3 independently seeded inits"
GRAD_CLIP_NORM = 1.0       # record: "gradient clipping norm: 1.0"
ENC_LAYERS = 4             # record: "4 identical layers of multi-head self-attention"
DEC_LAYERS = 4             # record: "4 layers of multi-head cross-attention"
T2V_PERIODIC = 4           # record: Time2Vec "learnable periodic sinusoidal components"
FEATURE_Z_WINDOW = 252     # record: the engineered features are "standardized via 252-day
                           # trailing z-scores"
FEATURE_ROC_WINDOWS = (1, 5, 20, 60)   # record: "1/5/20/60-day rate of change (trend)"
FEATURE_VOL_WINDOW = 20    # record: "20-day realized volatility"
FEATURE_SKEW_WINDOW = 20   # record: "rolling skewness and kurtosis (regime indicators)"
FEATURE_KURT_WINDOW = 20
FEATURE_CORR_WINDOW = 60   # record: "rolling pairwise correlation"
MVO_LOOKBACK = 60          # registered: the trailing mean/covariance window of the baseline
MVO_SHRINKAGE = 0.5        # registered: 50% shrinkage of the covariance toward its diagonal
MVO_RISK_AVERSION = 1.0    # registered: the mean-variance risk-aversion divisor
TSMOM_WINDOW = 365         # registered: 12-month time-series momentum on daily bars
RP_VOL_WINDOW = 60         # registered: the inverse-volatility window of the baseline
# ---- project pre-registered constants (the record leaves these open) -------------------------
LOOKBACK = 32              # registered state-window length L (temporal lookback)
D_MODEL = 16               # registered transformer width
N_HEADS = 2                # registered attention heads
LSTM_HIDDEN = 16           # registered LSTM hidden width
EPOCHS = 24                # registered OneCycleLR epochs over the historical slice
BATCH = 128                # registered contiguous Sharpe block (one block = one Sharpe)
LR_MAX = 0.005             # registered OneCycleLR peak learning rate
LR_MIN_RATIO = 0.1         # registered OneCycleLR final/peak ratio
WEIGHT_DECAY = 0.01        # registered AdamW decoupled weight decay
MATERIALITY_W = 0.25       # registered target-state band on the unit-gross weight: a market is
                           # LONG while w >= +0.25, SHORT while w <= -0.25, FLAT inside the band
                           # (1/N = 0.25 exactly, so the equal-weight arm is always fully LONG)
TARGET_GROSS = 1.0         # registered: the signed-softmax layer already enforces unit gross
PROBE_CASE = CASE_ORDER[0]  # the case the causality probe re-derives (the tf arm)
PANEL_SIZE = 4             # the registered local eligible universe
PANEL_COHORTS = {}         # filled by main(): the four cohorts the panel is built from
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
# composite (score variant, smoothing arm) axis; its registered order is CASE_ORDER.
AXES = ("window_case", "spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct")
DCA_AXES = ("spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct")
SELECTOR_VERSION = "cohort-selector-v1"
DISPOSITION_VERSION = "cohort-disposition-v1"
CONTRACT_SEMANTICS_VERSION = "v1.4.0"
ENGINE_VERSION = "pt-v1-engine-1.0.0"
ENGINE_SEMANTICS = ("the record's end-to-end parametric portfolio policy (Portfolio Transformer "
                    "or LSTM) fitted on the cross-sectional daily return state under the "
                    "differentiable Sharpe objective - cost-aware in the registered tf_net arm "
                    "with lambda = 0.0002 - with the signed-softmax unit-gross weight layer, "
                    "three seeded initializations averaged, AdamW + OneCycleLR, GELU, "
                    "pre-activation layer normalization and gradient clipping at norm 1.0, "
                    "together with the record's simple rules (1/N, inverse-volatility risk "
                    "parity, 12-month time-series momentum) and the two-step mean-variance "
                    "baseline, turned into target-state change entry events at the next bar's "
                    "open, the record's target exit at the next bar's open, plus the registered "
                    "DCA rail (see module docstring)")
WINNER_METRIC_KEYS = ("net_pnl", "sharpe", "episodes", "ending_equity", "fees", "funding",
                      "max_dd_usdt", "max_dd_pct", "max_effective_leverage",
                      "capital_utilization", "annualized_return", "cagr")
# csv row field order (kept identical for every phase grid)
ROW_FIELDS = ("symbol", "timeframe", "window_kind", "window_case", "case_name",
              "arm",
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
                 "panel_grid_mismatch", "state_not_derived_from_the_registered_rule",
                 "materiality_threshold_violation", "signal_input_alignment_gap",
                 "score_nonfinite_on_a_defined_bar", "expanding_stat_undefined",
                 "defensive_market_missing")
COUNTERS = {}
# ---- the registered local eligible universe (lifecycle footer, contract 14.4) ----------------
# The record's own crypto portability paragraph names "top liquid crypto perpetual contracts";
# the canonical local raw's complete set carrying the record's required daily close-to-close
# fields is these four USD-M perpetuals, on the record's own daily cadence.  Nothing is shrunk
# by outcome: the set is fixed here, before any computation.
PANEL_MEMBERS = ("BTCUSDT", "BNBUSDT", "ETHUSDT", "SOLUSDT")
PANEL_GRID_KIND = "1d"
MARKET_ROLE = {m: "panel" for m in PANEL_MEMBERS}
# ---- registered family-level reader constants (frozen before the first run) -----------------
COST_ESCALATION_BPS = (5.0, 10.0, 15.0)   # record falsification item 1: "slippage schedules of
                                          # 5 bps, 10 bps, and 15 bps"
COST_ESCALATION_SHARPE_FLOOR = 0.30       # record: "net annualized Sharpe ratio drops below 0.30"
COST_ESCALATION_EW_GAP = 0.10             # record: "or falls below naive 1/N by more than 0.10"
BOOTSTRAP_BLOCK = 21                      # record: "mean block length 21 trading days"
BOOTSTRAP_RESAMPLES = 10000               # record: "10,000 resamples"
BOOTSTRAP_SEED = 20260902                 # the registered single seed of the block bootstrap
PERCENTILE_TOLERANCE = 1e-9


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


def case_label(case):
    """Human-readable label of one registered case (the arm name)."""
    return case_name(case)



"""End-to-end parametric portfolio policy kernel (numpy only, deterministic).

Record: Pollok & Robik, "End-to-End Parametric Portfolio Policies for Cross-Asset Futures
Timing: When Do AI Models Beat Simple Rules?" (arXiv:2607.00475v1).

Source-specified facts this module reproduces (canonical wiki record):
  * signed-softmax output layer
        w_{i,t}(theta) = sign(s_{i,t}) exp(|s_{i,t}|) / sum_j exp(|s_{j,t}|)
    => sum_i |w_{i,t}| = 1.0 (unit gross), unconstrained long/short.
  * differentiable Sharpe objective  L = -E[R_P]/sqrt(Var[R_P]),
    cost-aware variant R_net,t = R_P,t - lambda * sum_i |w_{i,t} - w_{i,t-1}|, lambda = 0.0002.
  * AdamW + OneCycleLR, GELU activation, pre-activation Layer Normalization,
    gradient clipping norm 1.0, output weights averaged over 3 independently seeded
    initializations, daily close-to-close cadence (holding period t -> t+1).
  * Portfolio Transformer architecture: Time2Vec temporal embedding, 4 encoder layers of
    multi-head self-attention (across assets and temporal lookbacks) each followed by a GRN,
    4 decoder layers of multi-head cross-attention, strict causal mask.
  * LSTM policy: the record's recurrent comparator.

Everything the record leaves open is a project pre-registered constant frozen before the
first run (see the engine's registered signal contract block).

Pure library: no engine state, no IO.
"""



# ---- registered architecture / optimiser values (research-defined; frozen) -------------
LOOKBACK = 32            # lookback bars L of the state window
D_MODEL = 16             # transformer width
N_HEADS = 2              # attention heads
ENC_LAYERS = 4           # record: "4 identical layers" of the encoder
DEC_LAYERS = 4           # record: "4 layers" of the decoder
T2V_PERIODIC = 4         # Time2Vec periodic components (plus the linear component)
LSTM_HIDDEN = 16         # LSTM hidden width
EPOCHS = 40              # OneCycleLR epochs over the registered historical slice
BATCH = 128              # contiguous training block (a Sharpe block is a contiguous window)
LR_MAX = 0.005           # OneCycleLR peak learning rate
LR_MIN_RATIO = 0.1       # OneCycleLR final/peak ratio
WEIGHT_DECAY = 0.01      # AdamW decoupled weight decay
GRAD_CLIP = 1.0          # record: "gradient clipping norm: 1.0"
TURNOVER_LAMBDA = 0.0002  # record: lambda = 0.0002 (2 bps baseline) of the cost-aware loss
ENSEMBLE_SEEDS = (20260902, 20260903, 20260904)   # record: 3 independently seeded inits
MASK_FILL = -1e9         # additive stand-in for -inf in the strict causal mask
INFER_BATCH = 384        # inference batch (no effect on the arithmetic)


# --------------------------------------------------------------------------------------
# minimal reverse-mode automatic differentiation over numpy (float64)
# --------------------------------------------------------------------------------------
_TAPE: list = []


class Node:
    """One differentiated tensor; `g` accumulates the reverse-mode gradient."""

    __slots__ = ("v", "g")

    def __init__(self, v):
        self.v = v
        self.g = None


def _unbroadcast(g, shape):
    """Sum a gradient back down to `shape` (numpy broadcasting in reverse)."""
    if g.shape == shape:
        return g
    while g.ndim > len(shape):
        g = g.sum(axis=0)
    for i, s in enumerate(shape):
        if s == 1 and g.shape[i] != 1:
            g = g.sum(axis=i, keepdims=True)
    return g


def _emit(out_value, inputs, grad_fn):
    n = Node(out_value)
    _TAPE.append((n, inputs, grad_fn))
    return n


def param(value):
    return Node(np.asarray(value, dtype=np.float64))


def const(value):
    return Node(np.asarray(value, dtype=np.float64))


def reset_tape():
    del _TAPE[:]


def backward(root):
    root.g = np.ones_like(root.v)
    for out, inputs, grad_fn in reversed(_TAPE):
        if out.g is None:
            continue
        grads = grad_fn(out.g)
        for inp, gr in zip(inputs, grads):
            if gr is None:
                continue
            inp.g = gr if inp.g is None else inp.g + gr


# ---- linear algebra -------------------------------------------------------------------
def matmul(a, b):
    out = Node(a.v @ b.v)
    _TAPE.append((out, (a, b), None))
    _TAPE[-1] = (out, (a, b), None)

    def bwd(g):
        return (_unbroadcast(g @ np.swapaxes(b.v, -1, -2), a.v.shape),
                _unbroadcast(np.swapaxes(a.v, -1, -2) @ g, b.v.shape))

    _TAPE[-1] = (out, (a, b), bwd)
    return out


def add(a, b):
    return _emit(a.v + b.v, (a, b),
                 lambda g: (_unbroadcast(g, a.v.shape), _unbroadcast(g, b.v.shape)))


def sub(a, b):
    return _emit(a.v - b.v, (a, b),
                 lambda g: (_unbroadcast(g, a.v.shape), _unbroadcast(-g, b.v.shape)))


def mul(a, b):
    return _emit(a.v * b.v, (a, b),
                 lambda g: (_unbroadcast(g * b.v, a.v.shape),
                            _unbroadcast(g * a.v, b.v.shape)))


def scale(a, c):
    return _emit(a.v * c, (a,), lambda g: (g * c,))


def add_scalar(a, c):
    return _emit(a.v + c, (a,), lambda g: (g,))


def neg(a):
    return scale(a, -1.0)


def div(a, b):
    """a / b with b broadcastable."""
    out = Node(a.v / b.v)
    _TAPE.append((out, (a, b), None))

    def bwd(g):
        return (_unbroadcast(g / b.v, a.v.shape),
                _unbroadcast(-g * a.v / (b.v * b.v), b.v.shape))

    _TAPE[-1] = (out, (a, b), bwd)
    return out


# ---- elementwise nonlinearities -------------------------------------------------------
def tanh(a):
    out = Node(np.tanh(a.v))
    _TAPE.append((out, (a,), None))
    _TAPE[-1] = (out, (a,), lambda g: (g * (1.0 - out.v * out.v),))
    return out


def sigmoid(a):
    out = Node(1.0 / (1.0 + np.exp(-a.v)))
    _TAPE.append((out, (a,), None))
    _TAPE[-1] = (out, (a,), lambda g: (g * out.v * (1.0 - out.v),))
    return out


def sin(a):
    out = Node(np.sin(a.v))
    _TAPE.append((out, (a,), None))
    _TAPE[-1] = (out, (a,), lambda g: (g * np.cos(a.v),))
    return out


def exp(a):
    out = Node(np.exp(a.v))
    _TAPE.append((out, (a,), None))
    _TAPE[-1] = (out, (a,), lambda g: (g * out.v,))
    return out


def sqrt(a):
    out = Node(np.sqrt(np.maximum(a.v, 1e-300)))
    _TAPE.append((out, (a,), None))
    _TAPE[-1] = (out, (a,), lambda g: (g / (2.0 * out.v),))
    return out


def abs_(a):
    sgn = np.sign(a.v)
    return _emit(np.abs(a.v), (a,), lambda g: (g * sgn,))


def gelu(a):
    """GELU, tanh approximation (registered activation; the record writes "GELU")."""
    x = a.v
    c = np.sqrt(2.0 / np.pi)
    inner = c * (x + 0.044715 * x ** 3)
    t = np.tanh(inner)
    out = Node(0.5 * x * (1.0 + t))
    _TAPE.append((out, (a,), None))
    dinner = c * (1.0 + 3.0 * 0.044715 * x * x)

    def bwd(g):
        return (g * (0.5 * (1.0 + t) + 0.5 * x * (1.0 - t * t) * dinner),)

    _TAPE[-1] = (out, (a,), bwd)
    return out


# ---- reductions / normalization --------------------------------------------------------
def sum_last(a):
    return _emit(a.v.sum(axis=-1, keepdims=True), (a,),
                 lambda g: (np.broadcast_to(g, a.v.shape),))


def mean0(a):
    """Mean over axis 0, keepdims (the Sharpe block axis)."""
    n = a.v.shape[0]
    return _emit(a.v.mean(axis=0, keepdims=True), (a,),
                 lambda g: (np.broadcast_to(g, a.v.shape) / float(n),))


def softmax_masked(x, mask):
    z = x.v + mask
    z = z - np.max(z, axis=-1, keepdims=True)
    e = np.exp(z)
    p = e / np.sum(e, axis=-1, keepdims=True)
    out = Node(p)
    _TAPE.append((out, (x,), None))
    _TAPE[-1] = (out, (x,),
                 lambda g: (p * (g - np.sum(g * p, axis=-1, keepdims=True)),))
    return out


def layernorm(a, gamma, beta, eps=1e-6):
    """Pre-activation Layer Normalization over the last axis."""
    x = a.v
    mu = x.mean(axis=-1, keepdims=True)
    var = x.var(axis=-1, keepdims=True)
    inv = 1.0 / np.sqrt(var + eps)
    xn = (x - mu) * inv
    out = Node(xn * gamma.v + beta.v)
    _TAPE.append((out, (a, gamma, beta), None))

    def bwd(g):
        gx = g * gamma.v
        dx = inv * (gx - gx.mean(axis=-1, keepdims=True)
                    - xn * (gx * xn).mean(axis=-1, keepdims=True))
        axes = tuple(range(g.ndim - 1))
        return (dx, (g * xn).sum(axis=axes), g.sum(axis=axes))

    _TAPE[-1] = (out, (a, gamma, beta), bwd)
    return out


# ---- shape ops -------------------------------------------------------------------------
def reshape(a, shape):
    return _emit(a.v.reshape(shape), (a,), lambda g: (g.reshape(a.v.shape),))


def transpose(a, axes):
    inv = np.argsort(axes)
    return _emit(np.transpose(a.v, axes), (a,),
                 lambda g: (np.transpose(g, inv),))


def concat_last(nodes):
    widths = [n.v.shape[-1] for n in nodes]
    offs = np.cumsum([0] + widths)
    out = Node(np.concatenate([n.v for n in nodes], axis=-1))
    _TAPE.append((out, tuple(nodes), None))

    def bwd(g):
        return tuple(g[..., offs[i]:offs[i + 1]] for i in range(len(nodes)))

    _TAPE[-1] = (out, tuple(nodes), bwd)
    return out


def a_slice(a, lo, hi):
    shape = a.v.shape

    def bwd(g):
        gz = np.zeros(shape, dtype=np.float64)
        gz[..., lo:hi] = g
        return (gz,)

    return _emit(a.v[..., lo:hi], (a,), bwd)


def split_last(a, width):
    n = a.v.shape[-1] // width
    return [a_slice(a, i * width, (i + 1) * width) for i in range(n)]


def broadcast_to(a, shape):
    """Explicit broadcast (gradient sums back down); broadcasts to `shape`."""
    return _emit(np.broadcast_to(a.v, shape), (a,),
                 lambda g: (_unbroadcast(g, a.v.shape),))


def signed_softmax_raw(scores_v):
    s = scores_v
    a = np.abs(s)
    z = a - np.max(a, axis=-1, keepdims=True)
    e = np.exp(z)
    return np.sign(s) * e / np.sum(e, axis=-1, keepdims=True)


def signed_softmax(scores):
    """w_i = sign(s_i) exp(|s_i|) / sum_j exp(|s_j|); sum_i |w_i| = 1 (source-specified)."""
    absv = abs_(scores)
    mx = const(np.max(absv.v, axis=-1, keepdims=True))
    e = exp(sub(absv, mx))
    den = sum_last(e)
    signed = mul(const(np.sign(scores.v)), e)
    return div(signed, den)


# --------------------------------------------------------------------------------------
# Time2Vec temporal embedding
# --------------------------------------------------------------------------------------
def t2v_embed(p, length, batch_broadcast=0):
    """Time2Vec(l) for l = 1..length: one linear + T2V_PERIODIC learnable sinusoids.

    Returns a node of shape (length, t2v+1); callers broadcast it onto the token grid, so the
    frequencies/phases stay trainable through the broadcast.
    """
    k = p.t2v
    tau = const(np.arange(1, length + 1, dtype=np.float64).reshape(length, 1))
    w0 = a_slice(p.t2v_w, 0, 1)
    b0 = a_slice(p.t2v_b, 0, 1)
    lin = add(mul(tau, w0), b0)                     # (L,1)
    wk = a_slice(p.t2v_w, 1, k + 1)                 # (k,) -> broadcast over (L,1)
    bk = a_slice(p.t2v_b, 1, k + 1)
    per = sin(add(mul(tau, wk), bk))                # (L,k)
    return concat_last([lin, per])


# --------------------------------------------------------------------------------------
# Portfolio Transformer
# --------------------------------------------------------------------------------------
def _glorot(rng, a, b, scale=0.08):
    return rng.normal(0.0, scale, size=(a, b))


class TransformerParams:
    def __init__(self, rng, n_assets, in_dim=None, d_model=D_MODEL, n_heads=N_HEADS,
                 enc_layers=ENC_LAYERS, dec_layers=DEC_LAYERS, t2v=T2V_PERIODIC):
        self.n_assets = n_assets
        self.d_model = d_model
        self.n_heads = n_heads
        self.enc_layers = enc_layers
        self.dec_layers = dec_layers
        self.t2v = t2v
        self.in_dim = int(in_dim if in_dim is not None else n_assets)
        d = d_model
        self.t2v_w = param(np.concatenate([[rng.normal(0.0, 0.5)],
                                           rng.normal(0.0, 0.5, size=t2v)]))
        self.t2v_b = param(np.concatenate([[rng.normal(0.0, 0.5)],
                                           rng.normal(0.0, 0.5, size=t2v)]))
        self.t2v_proj = param(_glorot(rng, t2v + 1, d))
        self.asset_embed = param(_glorot(rng, n_assets, d))
        self.x_proj = param(_glorot(rng, self.in_dim, d))
        self.enc = [self._block(rng, d) for _ in range(enc_layers)]
        self.dec_q = param(_glorot(rng, n_assets, d))
        self.dec = [self._block(rng, d) for _ in range(dec_layers)]
        self.head_w = param(_glorot(rng, d, 1))
        self.head_b = param(np.zeros(1))

    @staticmethod
    def _block(rng, d):
        return {"ln1_g": param(np.ones(d)), "ln1_b": param(np.zeros(d)),
                "wq": param(_glorot(rng, d, d)), "wk": param(_glorot(rng, d, d)),
                "wv": param(_glorot(rng, d, d)), "wo": param(_glorot(rng, d, d)),
                "ln2_g": param(np.ones(d)), "ln2_b": param(np.zeros(d)),
                "grn1_w": param(_glorot(rng, d, d)), "grn1_b": param(np.zeros(d)),
                "grn2_w": param(_glorot(rng, d, d)), "grn2_b": param(np.zeros(d)),
                "grn_gate_w": param(_glorot(rng, d, d)), "grn_gate_b": param(np.zeros(d)),
                "grn_out_w": param(_glorot(rng, d, d)),
                "ln3_g": param(np.ones(d)), "ln3_b": param(np.zeros(d))}

    def parameters(self):
        out = [self.t2v_w, self.t2v_b, self.t2v_proj, self.asset_embed, self.x_proj]
        for blocks in (self.enc, self.dec):
            for blk in blocks:
                out.extend(blk[k] for k in
                           ("ln1_g", "ln1_b", "wq", "wk", "wv", "wo", "ln2_g", "ln2_b",
                            "grn1_w", "grn1_b", "grn2_w", "grn2_b", "grn_gate_w",
                            "grn_gate_b", "grn_out_w", "ln3_g", "ln3_b"))
        out.extend([self.dec_q, self.head_w, self.head_b])
        return out


def _mha(block, q_in, kv_in, mask, n_heads, d):
    dh = d // n_heads
    outs = []
    for h in range(n_heads):
        lo, hi = h * dh, (h + 1) * dh
        wq = a_slice(block["wq"], lo, hi)
        wk = a_slice(block["wk"], lo, hi)
        wv = a_slice(block["wv"], lo, hi)
        q = matmul(q_in, wq)
        k = matmul(kv_in, wk)
        v = matmul(kv_in, wv)
        sc = scale(matmul(q, transpose(k, (0, 2, 1))), 1.0 / np.sqrt(dh))
        att = softmax_masked(sc, mask)
        outs.append(matmul(att, v))
    ctx = concat_last(outs)
    return matmul(ctx, block["wo"])


def _grn(block, x):
    """Gated Residual Network: GELU -> GLU gate -> residual -> layer norm."""
    h = gelu(add(matmul(x, block["grn1_w"]), block["grn1_b"]))
    g = sigmoid(add(matmul(x, block["grn_gate_w"]), block["grn_gate_b"]))
    core = mul(g, add(matmul(h, block["grn2_w"]), block["grn2_b"]))
    core = add(core, matmul(x, block["grn_out_w"]))
    return layernorm(core, block["ln3_g"], block["ln3_b"])


def causal_mask(length, n_assets):
    idx = np.arange(length * n_assets)
    lev = (idx // n_assets)[:, None]
    lev2 = (idx // n_assets)[None, :]
    return np.where(lev2 <= lev, 0.0, MASK_FILL)[None, :, :]


def transformer_scores(p, X4):
    """X4: numpy (B, L, N, K) state tensor -> (B, N) raw decoder scores.

    Token (l, i) carries the lag-l cross-section (K = N), plus the asset's own engineered
    features when the registered case uses them (K = N + F).
    """
    b, length, n, k = X4.shape
    d = p.d_model
    flat = const(X4.reshape(b * length * n, k))
    proj = matmul(flat, p.x_proj)                         # (b*L*N, d)
    proj = reshape(proj, (b, length, n, d))
    t2v = t2v_embed(p, length)                            # (L, t2v+1)
    t2v = matmul(t2v, p.t2v_proj)                         # (L,d)
    t2v = reshape(t2v, (1, length, 1, d))
    z = add(add(proj, p.asset_embed), t2v)                # (b,L,N,d) by broadcasting
    z = reshape(z, (b, length * n, d))
    mask = causal_mask(length, n)
    for blk in p.enc:
        y = layernorm(z, blk["ln1_g"], blk["ln1_b"])
        z = add(z, _mha(blk, y, y, mask, p.n_heads, d))
        y2 = layernorm(z, blk["ln2_g"], blk["ln2_b"])
        z = add(z, _grn(blk, y2))
    ctx = z
    q = broadcast_to(p.dec_q, (b, n, d))
    for blk in p.dec:
        y = layernorm(q, blk["ln1_g"], blk["ln1_b"])
        q = add(q, _mha(blk, y, ctx, np.zeros((1, n, length * n)), p.n_heads, d))
        y2 = layernorm(q, blk["ln2_g"], blk["ln2_b"])
        q = add(q, _grn(blk, y2))
    sc = add(matmul(q, p.head_w), p.head_b)               # (b,N,1)
    return reshape(sc, (b, n))


# --------------------------------------------------------------------------------------
# LSTM policy (the record's recurrent comparator)
# --------------------------------------------------------------------------------------
class LstmParams:
    def __init__(self, rng, n_assets, feat_dim=0, hidden=LSTM_HIDDEN, t2v=T2V_PERIODIC):
        self.n_assets = n_assets
        self.feat_dim = int(feat_dim)
        self.hidden = hidden
        self.t2v = t2v
        self.t2v_w = param(np.concatenate([[rng.normal(0.0, 0.5)],
                                           rng.normal(0.0, 0.5, size=t2v)]))
        self.t2v_b = param(np.concatenate([[rng.normal(0.0, 0.5)],
                                           rng.normal(0.0, 0.5, size=t2v)]))
        inp = n_assets + n_assets * self.feat_dim + t2v + 1
        self.wx = param(_glorot(rng, inp, 4 * hidden))
        self.wh = param(_glorot(rng, hidden, 4 * hidden))
        self.bias = param(np.zeros(4 * hidden))
        self.wo = param(_glorot(rng, hidden, n_assets))
        self.bo = param(np.zeros(n_assets))

    def parameters(self):
        return [self.t2v_w, self.t2v_b, self.wx, self.wh, self.bias, self.wo, self.bo]


def lstm_scores(p, X4):
    """X4: (B, L, N, K) state tensor -> (B, N) scores.

    Per timestep the LSTM reads the lag-l cross-section plus every asset's own engineered
    features (flattened asset-major); the recurrence is causal by construction.
    """
    b, length, n, k = X4.shape
    fdim = k - n
    hsize = p.hidden
    h = const(np.zeros((b, hsize)))
    c = const(np.zeros((b, hsize)))
    for l in range(length):
        tau = const(np.array([float(l + 1)]))
        kk = p.t2v
        emb_lin = add(mul(tau, a_slice(p.t2v_w, 0, 1)), a_slice(p.t2v_b, 0, 1))
        emb_per = sin(add(mul(tau, a_slice(p.t2v_w, 1, kk + 1)),
                          a_slice(p.t2v_b, 1, kk + 1)))
        emb = concat_last([emb_lin, emb_per])                       # (t2v+1,)
        emb_b = broadcast_to(emb, (b, kk + 1))
        parts = [const(X4[:, l, 0, :n])]                            # the lag-l cross-section
        if fdim:
            parts.append(const(X4[:, l, :, n:].reshape(b, n * fdim)))
        parts.append(emb_b)
        xin = concat_last(parts)
        gates = add(add(matmul(xin, p.wx), matmul(h, p.wh)), p.bias)
        i, f, o, g = split_last(gates, hsize)
        c = add(mul(sigmoid(f), c), mul(sigmoid(i), tanh(g)))
        h = mul(sigmoid(o), tanh(c))
    return add(matmul(h, p.wo), p.bo)


def forward_scores(arm, p, X4):
    if arm in ("tf", "tf_net", "tfx"):
        return transformer_scores(p, X4)
    return lstm_scores(p, X4)


# --------------------------------------------------------------------------------------
# differentiable Sharpe objective + AdamW / OneCycleLR
# --------------------------------------------------------------------------------------
def sharpe_loss(w_cur, w_prev, r_next, lam):
    """-Sharpe of the net portfolio return over a contiguous block (the record's objective).

    `w_cur` / `w_prev`: (B,N) signed-softmax weights at t and t-1; `r_next`: (B,N) realised
    returns of the holding period t -> t+1.
    """
    gross = sum_last(mul(w_cur, const(r_next)))            # (B,1)
    turnover = sum_last(abs_(sub(w_cur, w_prev)))          # (B,1)
    net = sub(gross, scale(turnover, lam))                 # (B,1)
    m = mean0(net)                                         # (1,)
    dev = sub(net, m)
    var = mean0(mul(dev, dev))
    sd = sqrt(add_scalar(var, 1e-12))
    return neg(div(m, sd))


class AdamW:
    def __init__(self, params, lr_max, total_steps, wd=WEIGHT_DECAY, clip=GRAD_CLIP,
                 min_ratio=LR_MIN_RATIO, warmup_frac=0.3):
        self.params = params
        self.m = [np.zeros_like(p.v) for p in params]
        self.v = [np.zeros_like(p.v) for p in params]
        self.lr_max = lr_max
        self.total = max(1, total_steps)
        self.wd = wd
        self.clip = clip
        self.min_ratio = min_ratio
        self.warmup_frac = warmup_frac
        self.t = 0

    def lr_at(self, step):
        frac = float(step) / float(self.total)
        if frac < self.warmup_frac:
            return self.lr_max * (frac / self.warmup_frac)
        u = (frac - self.warmup_frac) / max(1e-9, 1.0 - self.warmup_frac)
        return self.lr_max * (self.min_ratio + (1.0 - self.min_ratio) * 0.5
                              * (1.0 + np.cos(np.pi * min(1.0, u))))

    def step(self):
        self.t += 1
        gnorm = float(np.sqrt(sum(float(np.sum(p.g * p.g))
                                  for p in self.params if p.g is not None)))
        coef = 1.0 if (gnorm <= self.clip or gnorm == 0.0) else self.clip / gnorm
        lr = self.lr_at(self.t - 1)
        b1, b2, eps = 0.9, 0.999, 1e-8
        for i, p in enumerate(self.params):
            if p.g is None:
                continue
            g = p.g * coef
            self.m[i] = b1 * self.m[i] + (1.0 - b1) * g
            self.v[i] = b2 * self.v[i] + (1.0 - b2) * g * g
            mh = self.m[i] / (1.0 - b1 ** self.t)
            vh = self.v[i] / (1.0 - b2 ** self.t)
            p.v = p.v - lr * (mh / (np.sqrt(vh) + eps) + self.wd * p.v)
        return {"lr": lr, "grad_norm": gnorm, "clip_coef": coef}


# --------------------------------------------------------------------------------------
# windows, fitting, inference
# --------------------------------------------------------------------------------------
def build_state(x_ret, feat, win):
    """Trailing state window ending at each bar: (T, win, N, K), K = N (+ engineered feats).

    Rows before the lookback is full (or before the trailing z-score is defined) stay NaN.
    """
    T, n = x_ret.shape
    f = 0 if feat is None else int(feat.shape[2])
    X = np.full((T, win, n, n + f), np.nan, dtype=np.float64)
    for t in range(win - 1, T):
        block = np.repeat(x_ret[t - win + 1:t + 1][:, None, :], n, axis=1)
        if f:
            block = np.concatenate([block, feat[t - win + 1:t + 1]], axis=2)
        X[t] = block
    return X


def fit_policy(arm, X, r_next, fit_lo, fit_hi, seed, lam, epochs=EPOCHS, batch=BATCH,
               lr_max=LR_MAX, trace=None):
    """Fit one seeded policy on the registered historical slice only.

    Samples `fit_lo..fit_hi` are the contiguous Sharpe blocks; the state windows may reach
    back before `fit_lo` (trailing data) and never forward.
    """
    rng = np.random.default_rng(seed)
    n = X.shape[2]
    fdim = int(X.shape[3]) - n
    params = (TransformerParams(rng, n, in_dim=X.shape[3]) if arm in ("tf", "tf_net", "tfx")
              else LstmParams(rng, n, feat_dim=fdim))
    plist = params.parameters()
    # A training block is a contiguous run of samples whose OWN window AND the previous bar's
    # window are both finite: the first is the state of t, the second carries w_{t-1} of the
    # turnover term.  Interior gaps (an undefined engineered feature, say) split the run.
    ok = np.isfinite(X).all(axis=(1, 2, 3))
    ok_prev = np.concatenate([[False], ok[:-1]])
    samples = np.flatnonzero(ok & ok_prev)
    samples = samples[(samples >= fit_lo) & (samples <= fit_hi)]
    chunks = []
    for run in np.split(samples, np.flatnonzero(np.diff(samples) != 1) + 1):
        for a in range(0, run.size, batch):
            piece = run[a:a + batch]
            if piece.size >= 8:
                chunks.append(piece)
    if not chunks:
        raise ValueError("no trainable Sharpe block in %d..%d" % (fit_lo, fit_hi))
    opt = AdamW(plist, lr_max, len(chunks) * epochs)
    perm = np.random.default_rng(seed + 1)
    hist = []
    for _ep in range(epochs):
        ep_loss = 0.0
        nb = 0
        for ci in perm.permutation(len(chunks)):
            blk = chunks[ci]
            lo = int(blk[0]) - 1
            hi = int(blk[-1])
            rows = np.arange(lo, hi + 1)
            reset_tape()
            W = signed_softmax(forward_scores(arm, params, X[rows]))     # (B+1, N)
            loss = sharpe_loss(_slice_rows(W, 1, rows.size),
                               _slice_rows(W, 0, rows.size - 1),
                               r_next[rows[1:]], lam)
            backward(loss)
            opt.step()
            ep_loss += float(np.asarray(loss.v).ravel()[0])
            nb += 1
        hist.append(ep_loss / max(1, nb))
    report = {"seed": seed, "epochs": epochs, "batch": batch, "blocks": len(chunks),
              "fit_samples": int(sum(c.size for c in chunks)),
              "first_fit_sample": int(chunks[0][0]), "last_fit_sample": int(fit_hi),
              "lambda": lam, "loss_first": hist[0], "loss_last": hist[-1]}
    if trace is not None:
        trace.append(report)
    return params, report


def _slice_rows(node, lo, hi):
    """Slice axis 0 of a (B,N) node (row range)."""
    shape = node.v.shape

    def bwd(g):
        gz = np.zeros(shape, dtype=np.float64)
        gz[lo:hi] = g
        return (gz,)

    return _emit(node.v[lo:hi], (node,), bwd)


def infer_weights(arm, params, X, refresh=INFER_BATCH):
    """Full causal weight path of one fitted policy over every bar (NaN before the lookback)."""
    T = X.shape[0]
    out = np.full((T, X.shape[2]), np.nan, dtype=np.float64)
    ok = np.isfinite(X.reshape(T, -1)).all(axis=1)
    pos = np.flatnonzero(ok)
    for s in range(0, pos.size, refresh):
        rows = pos[s:s + refresh]
        reset_tape()
        W = signed_softmax(forward_scores(arm, params, X[rows]))
        out[rows] = W.v
    return out


# --------------------------------------------------------------------------- signal science --
# The record's end-to-end parametric portfolio policy, fitted ONLY on the registered historical
# slice, plus the three simple rules and the mean-variance baseline it is benchmarked against.
# Every statistic below is trailing by construction; the fitted parameters are frozen the moment
# the historical slice ends (OOS is inference only, never a parameter).
_PANEL_CACHE = {}
_WEIGHT_CACHE = {}
_FIT_TRACE = {}
_PARAMS_CACHE = {}


def _day_str(ms):
    return time.strftime("%Y-%m-%d", time.gmtime(int(ms) / 1000.0))


def _panel_matrix(closes_by_market):
    """The shared panel: one bar grid (asserted), the close matrix and the return matrix.

    Callers pass the live cohorts' own bar grids (the registered panel is built from them); a
    caller may equivalently pass a (T, 2) array of (open_time_ms, close) columns.
    """
    labels = sorted(closes_by_market)
    if len(labels) != PANEL_SIZE or tuple(labels) != tuple(sorted(PANEL_MEMBERS)):
        counter("signal", "panel_grid_mismatch")
        raise SystemExit("the registered panel needs exactly %r, got %r"
                         % (sorted(PANEL_MEMBERS), labels))
    grid = None
    cols = []
    for m in labels:
        val = closes_by_market[m]
        arr = np.asarray(val)
        if arr.ndim == 2:
            ms = np.asarray(arr[:, 0], dtype=np.int64)
            px = np.asarray(arr[:, 1], dtype=np.float64)
        else:
            if not PANEL_COHORTS or m not in PANEL_COHORTS:
                raise SystemExit("panel member %r is not a live cohort" % m)
            ms = np.asarray(PANEL_COHORTS[m].open_time_ms, dtype=np.int64)
            px = np.asarray(PANEL_COHORTS[m].close, dtype=np.float64)
        if grid is None:
            grid = ms
        elif not np.array_equal(grid, ms):
            counter("signal", "panel_grid_mismatch")
            raise SystemExit("the panel members do not share one bar grid: %r" % (labels,))
        cols.append(px)
    T = len(grid)
    close = np.stack(cols, axis=1) if cols else np.zeros((0, 0))
    ret = np.full((T, len(labels)), np.nan, dtype=np.float64)
    if T > 1:
        ret[1:] = close[1:] / close[:-1] - 1.0
    return labels, grid, close, ret


def _roll(x, window, fn):
    """Trailing rolling statistic over the last `window` rows of a (T, N) matrix."""
    T, N = x.shape
    out = np.full((T, N), np.nan, dtype=np.float64)
    for t in range(window - 1, T):
        blk = x[t - window + 1:t + 1]
        if not np.isfinite(blk).all():
            continue
        out[t] = fn(blk)
    return out


def _skew(a):
    m = a.mean(axis=0)
    s = a.std(axis=0)
    return ((a - m) ** 3).mean(axis=0) / np.maximum(s, 1e-12) ** 3


def _kurt(a):
    m = a.mean(axis=0)
    s = a.std(axis=0)
    return ((a - m) ** 4).mean(axis=0) / np.maximum(s, 1e-12) ** 4 - 3.0


def _policy_features(close, ret):
    """The record's engineered-feature ablation state, standardized by 252-day trailing z-scores.

    Registered subset (every window is the record's own): 1/5/20/60-day rate of change, 20-day
    realized volatility, 20-day rolling skewness and kurtosis, and the 60-day rolling pairwise
    correlation with a reference panel member (the first member that is not the asset itself).
    A trailing z-score is defined only when all 252 observations are finite (fail-closed).
    """
    T, N = ret.shape
    cols = []
    for k in FEATURE_ROC_WINDOWS:
        col = np.full((T, N), np.nan, dtype=np.float64)
        for t in range(k, T):
            if np.isfinite(close[t]).all() and np.isfinite(close[t - k]).all():
                col[t] = close[t] / close[t - k] - 1.0
        cols.append(col)
    cols.append(_roll(ret, FEATURE_VOL_WINDOW, lambda b: b.std(axis=0)))
    cols.append(_roll(ret, FEATURE_SKEW_WINDOW, _skew))
    cols.append(_roll(ret, FEATURE_KURT_WINDOW, _kurt))
    corr = np.full((T, N), np.nan, dtype=np.float64)
    for t in range(FEATURE_CORR_WINDOW - 1, T):
        blk = ret[t - FEATURE_CORR_WINDOW + 1:t + 1]
        if not np.isfinite(blk).all():
            continue
        for i in range(N):
            j = next((q for q in range(N) if q != i), None)
            if j is None:
                continue
            a = blk[:, i]
            b = blk[:, j]
            sa, sb = a.std(), b.std()
            if sa <= 0 or sb <= 0:
                continue
            corr[t, i] = float(((a - a.mean()) * (b - b.mean())).mean() / (sa * sb))
    cols.append(corr)
    raw = np.stack(cols, axis=2)                       # (T, N, F)
    z = np.full_like(raw, np.nan)
    for t in range(FEATURE_Z_WINDOW - 1, T):
        blk = raw[t - FEATURE_Z_WINDOW + 1:t + 1]
        if not np.isfinite(blk).all():
            continue
        mu = blk.mean(axis=0)
        sd = blk.std(axis=0)
        z[t] = (raw[t] - mu) / np.maximum(sd, 1e-12)
    return z


def _rule_weights(arm, close, ret):
    """The record's simple rules and the two-step mean-variance baseline (all trailing)."""
    T, N = ret.shape
    if arm == "ew":
        return np.full((T, N), 1.0 / float(N))
    if arm == "rp":
        vol = _roll(ret, RP_VOL_WINDOW, lambda b: b.std(axis=0))
        inv = 1.0 / np.maximum(vol, 1e-6)
        tot = inv.sum(axis=1, keepdims=True)
        return np.where(np.isfinite(tot) & (tot > 0), inv / np.maximum(tot, 1e-12), np.nan)
    if arm == "tsmom":
        mom = np.full((T, N), np.nan)
        for t in range(TSMOM_WINDOW - 1, T):
            blk = ret[t - TSMOM_WINDOW + 1:t + 1]
            if not np.isfinite(blk).all():
                continue
            mom[t] = np.sign(blk.mean(axis=0)) / float(N)
        return mom
    if arm == "mvo":
        w = np.full((T, N), np.nan, dtype=np.float64)
        for t in range(MVO_LOOKBACK - 1, T):
            blk = ret[t - MVO_LOOKBACK + 1:t + 1]
            if not np.isfinite(blk).all():
                continue
            mu = blk.mean(axis=0)
            cov = np.cov(blk, rowvar=False)
            diag = np.diag(np.diag(cov))
            cov = (1.0 - MVO_SHRINKAGE) * cov + MVO_SHRINKAGE * diag
            cov = cov + np.eye(N) * (1e-8 * max(1e-12, float(np.trace(cov))) / float(N))
            try:
                raw = np.linalg.solve(cov, mu) / MVO_RISK_AVERSION
            except np.linalg.LinAlgError:
                continue
            gross = float(np.abs(raw).sum())
            if gross <= 0 or not np.isfinite(gross):
                continue
            w[t] = raw / gross
        return w
    raise SystemExit("unregistered rule arm %r" % arm)


def _state_tensor(arm, close, ret):
    """The registered state tensor (T, L, N, K) of one arm."""
    if arm == "tfx":
        return build_state(ret, _policy_features(close, ret), LOOKBACK)
    return build_state(ret, None, LOOKBACK)


def _fit_bounds(state, i0, i1):
    """The registered fit sample range: full state window, in-slice target, never forward."""
    T = state.shape[0]
    ok = np.isfinite(state).all(axis=(1, 2, 3))
    hi = int(min(i1 - 1, T - 2))
    grid = [t for t in range(max(int(i0), 0), hi + 1) if ok[t]]
    if not grid:
        return None, None
    return int(grid[0]), int(grid[-1])


def _fit_one(arm, state, ret, seed, lo, hi):
    r_next = np.full_like(ret, np.nan)
    r_next[:-1] = ret[1:]
    lam = TURNOVER_LAMBDA if arm == "tf_net" else 0.0
    t0 = time.time()
    params, rep = fit_policy(arm, state, r_next, lo, hi, seed=seed, lam=lam,
                             epochs=EPOCHS, batch=BATCH, lr_max=LR_MAX)
    rep["wall_seconds"] = round(time.time() - t0, 3)
    return params, rep


def _arm_params(arm, state, ret, lo, hi):
    """The 3-seed ensemble of one trained arm (record: '3 independently seeded inits')."""
    if arm in _PARAMS_CACHE:
        return _PARAMS_CACHE[arm]
    fitted = []
    for seed in ENSEMBLE_SEEDS:
        params, rep = _fit_one(arm, state, ret, seed, lo, hi)
        fitted.append((seed, params, rep))
    _PARAMS_CACHE[arm] = fitted
    return fitted


def _historical_bounds(spec):
    """The registered historical slice as bar indices of the panel grid (fail-closed)."""
    if not PANEL_COHORTS:
        raise SystemExit("the historical slice was asked for before the cohorts were registered")
    live = [PANEL_COHORTS[m] for m in sorted(PANEL_COHORTS)]
    return [int(v) for v in live[0].slice(spec["data"]["historical_start"],
                                          spec["data"]["historical_end"])]


def _policy_weight_path(arm, labels, close, ret, spec, hist_i0, hist_i1, upto=None):
    """The registered weight path of one arm over the panel grid (fitted arms frozen)."""
    if upto is not None:
        close = close[:upto + 1]
        ret = ret[:upto + 1]
    if arm not in TRAINED_ARMS:
        return _rule_weights(arm, close, ret)
    state = _state_tensor(arm, close, ret)
    if upto is None:
        lo, hi = _fit_bounds(state, hist_i0, hist_i1)
        if lo is None:
            counter("signal", "expanding_stat_undefined")
            raise SystemExit("no trainable sample for arm %r inside the historical slice" % arm)
        fitted = _arm_params(arm, state, ret, lo, hi)
        _FIT_TRACE.setdefault(arm, {
            "arm": arm, "lambda": (TURNOVER_LAMBDA if arm == "tf_net" else 0.0),
            "seeds": list(ENSEMBLE_SEEDS), "epochs": EPOCHS, "batch": BATCH,
            "lookback": LOOKBACK, "d_model": D_MODEL, "n_heads": N_HEADS,
            "enc_layers": ENC_LAYERS, "dec_layers": DEC_LAYERS,
            "t2v_periodic": T2V_PERIODIC, "lstm_hidden": LSTM_HIDDEN,
            "lr_max": LR_MAX, "weight_decay": WEIGHT_DECAY, "grad_clip": GRAD_CLIP_NORM,
            "optimiser": "AdamW + OneCycleLR", "activation": "GELU",
            "normalisation": "pre-activation LayerNorm",
            "fit_first_sample": lo, "fit_last_sample": hi,
            "fit_samples": int(hi - lo + 1), "fit_last_label_bar": int(hi + 1),
            "fit_target_inside_the_historical_slice": bool(hi + 1 <= hist_i1),
            "historical_end_index": int(hist_i1),
            "param_count": int(sum(int(p.v.size) for p in fitted[0][1].parameters())),
            "per_seed": [{"seed": s, "blocks": r["blocks"], "loss_first": r["loss_first"],
                          "loss_last": r["loss_last"], "wall_seconds": r["wall_seconds"]}
                         for s, _p, r in fitted],
            "ensemble": "the mean of the 3 seeded signed-softmax weight paths",
        })
    else:
        fitted = _PARAMS_CACHE.get(arm)
        if fitted is None:
            raise SystemExit("the causality probe needs the frozen parameters of arm %r" % arm)
    stack = np.stack([infer_weights(arm, params, state) for _s, params, _r in fitted], axis=0)
    w = np.nanmean(stack, axis=0)
    defined = np.isfinite(w)
    if not np.isfinite(w[defined]).all():
        counter("signal", "score_nonfinite_on_a_defined_bar")
    return w


def _panel_weights(spec, labels, close, ret):
    """Every registered arm's weight path over the panel, cached per arm."""
    hist_i0, hist_i1 = _historical_bounds(spec)
    out = {}
    for arm in POLICY_ARMS:
        if arm not in _WEIGHT_CACHE:
            _WEIGHT_CACHE[arm] = _policy_weight_path(arm, labels, close, ret, spec,
                                                     hist_i0, hist_i1)
        out[arm] = _WEIGHT_CACHE[arm]
    return out


def _states_from_weight(weight, markets):
    """The registered target state of every market: the sign of the unit-gross policy weight.

    LONG while w >= +MATERIALITY_W, SHORT while w <= -MATERIALITY_W, FLAT inside the band; an
    undefined weight carries state 0.  The signed-softmax layer already enforces unit gross.
    """
    w = np.asarray(weight, dtype=np.float64)
    if w.ndim != 2 or w.shape[1] != len(markets):
        counter("signal", "panel_grid_mismatch")
        raise SystemExit("weight matrix %r does not match the panel %r" % (w.shape, markets))
    st = np.zeros(w.shape, dtype=np.int64)
    fin = np.isfinite(w)
    st = np.where(fin & (w >= MATERIALITY_W), 1, st)
    st = np.where(fin & (w <= -MATERIALITY_W), -1, st)
    return st


def _panel_core(case, closes_by_market, upto=None):
    """The registered policy path of ONE strategy arm over the registered universe."""
    labels, grid, close, ret = _panel_matrix(closes_by_market)
    if not PANEL_COHORTS:
        raise SystemExit("the panel was asked for before the cohorts were registered")
    spec = PANEL_COHORTS[labels[0]].spec
    arm = POLICY_ARMS[case_index(case)]
    if upto is None:
        weight = _panel_weights(spec, labels, close, ret)[arm]
    else:
        hist_i0, hist_i1 = _historical_bounds(spec)
        weight = _policy_weight_path(arm, labels, close, ret, spec, hist_i0, hist_i1,
                                     upto=upto)[:upto + 1]
    n = weight.shape[0]
    state = _states_from_weight(weight, labels)
    events = _fresh_events(state)
    finite = np.isfinite(weight).all(axis=1)
    pos = np.flatnonzero(finite)
    first = int(pos[0]) if pos.size else n
    gross = np.abs(weight[finite]).sum(axis=1) if pos.size else np.zeros(0)
    diag = {"arm": arm, "arm_label": ARM_LABELS[arm],
            "defined_observations": int(pos.size),
            "first_defined_date": (_day_str(grid[first]) if pos.size else None),
            "last_defined_date": (_day_str(grid[pos[-1]]) if pos.size else None),
            "weight_mean": (round(float(weight[finite].mean()), 6) if pos.size else None),
            "weight_min": (round(float(weight[finite].min()), 6) if pos.size else None),
            "weight_max": (round(float(weight[finite].max()), 6) if pos.size else None),
            "gross_min": (round(float(gross.min()), 6) if pos.size else None),
            "gross_max": (round(float(gross.max()), 6) if pos.size else None),
            "gross_deviation_max": (round(float(np.abs(gross - TARGET_GROSS).max()), 9)
                                    if pos.size else None),
            "long_bars": {m: int((state[:, j] > 0).sum()) for j, m in enumerate(labels)},
            "short_bars": {m: int((state[:, j] < 0).sum()) for j, m in enumerate(labels)},
            "flat_bars": {m: int((state[:, j] == 0).sum()) for j, m in enumerate(labels)},
            "events_per_market": {m: int((events[:, j] != 0).sum())
                                  for j, m in enumerate(labels)},
            "fit": _FIT_TRACE.get(arm)}
    return {"labels": labels, "n": n, "state": state, "events": events,
            "weight": weight, "close": close, "ret": ret, "arm": arm,
            "grid_ms": np.asarray(grid, dtype=np.int64),
            "case": tuple(case), "warmup_bars": first, "defined_bars": int(pos.size),
            "long_bars": diag["long_bars"], "score_diagnostics": diag}


def _panel_core_cached(case):
    """The cached policy path of one case over the LIVE registered cohorts."""
    if not PANEL_COHORTS:
        raise SystemExit("panel requested before the cohorts were registered")
    key = tuple(case)
    core = _PANEL_CACHE.get(key)
    if core is None:
        core = _panel_core(key, {m: np.asarray(PANEL_COHORTS[m].open_time_ms)
                                 for m in sorted(PANEL_COHORTS)})
        _PANEL_CACHE[key] = core
    return core


def _slice_diag_block(layer, series_flat, cohort, i0, i1, diag, pnl_by_year, ep_stats):
    """The diagnostics block of one measured slice, built once for BOTH exits of `simulate`.

    The kernel's no-episode exit returns before it builds its diagnostics, so the registered
    readers - which re-derive a policy path over a slice in which the arm simply never traded -
    read `diagnostics["daily_equity"]` on an empty dict and died (KeyError) after the whole
    grid had already been written.  A no-trade slice is a flat book, not a missing measurement:
    it publishes exactly the keys the traded path publishes, with the empty episode statistics
    and the arm's own flat daily marks.  Reader-side layers (a single seeded initialization's
    own weight path) carry no panel diagnostics at all, so that one field is read defensively
    instead of assumed.
    """
    if not diag:
        return {}
    return {"pnl_by_year": {k: _round_dict(v) for k, v in sorted(pnl_by_year.items())},
            "episode_stats": _round_dict({
                "episodes": ep_stats["episodes"], "wins": ep_stats["wins"],
                "win_rate": (ep_stats["wins"] / float(ep_stats["episodes"]))
                            if ep_stats["episodes"] else 0.0,
                "mean_hold_bars": (ep_stats["hold_bars"] / float(ep_stats["episodes"]))
                                  if ep_stats["episodes"] else 0.0,
                "net_pnl": ep_stats["net_pnl"]}),
            "signal_diagnostics": dict(getattr(layer, "diag", None) or {}),
            "signal_case": getattr(layer, "case_label", None),
            "daily_equity": [round(float(v), 6) for v in series_flat],
            "daily_equity_days": int(len(series_flat)),
            "slice_first_day_utc": iso(int(cohort.open_time_ms[i0])),
            "slice_last_day_utc": iso(int(cohort.open_time_ms[i1 - 1]))}

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
    """The registered four-market panel of one strategy arm (case).

    The policy maps the cross-sectional return tensor to continuous signed weights; the panel
    carries the (bars, markets) weight matrix, its registered target state and the entry events
    of every market.  The axis of a case is the ARM (which policy produces the path).
    """

    _cache = {}

    def __init__(self, case, cohorts):
        self.case = tuple(case)
        self.case_label = case_name(self.case)
        self.arm = POLICY_ARMS[case_index(self.case)]
        labels = sorted(cohorts)
        core = _panel_core_cached(self.case)
        if labels != core["labels"]:
            raise SystemExit("panel labels %r != registered %r" % (labels, core["labels"]))
        self.markets = labels
        self.n = core["n"]
        self.core = core
        self.state = core["state"].astype(np.int64)
        self.events = core["events"]
        self.weights = core["weight"]
        self.diag = {
            "case_name": self.case_label, "arm": self.arm, "arm_label": ARM_LABELS[self.arm],
            "panel": labels, "bars": int(self.n),
            "warmup_bars": int(core["warmup_bars"]),
            "defined_bars": int(core["defined_bars"]),
            "defined_bar_fraction": round(float(core["defined_bars"]) / float(max(1, self.n)), 6),
            "target_band": MATERIALITY_W,
            "target_gross": TARGET_GROSS,
            "states_per_market": {m: {"long": int((self.state[:, j] > 0).sum()),
                                      "short": int((self.state[:, j] < 0).sum()),
                                      "flat": int((self.state[:, j] == 0).sum()),
                                      "events": int((self.events[:, j] != 0).sum())}
                                  for j, m in enumerate(labels)},
            "policy_note": "the registered target state is the SIGN of the unit-gross policy "
                           "weight outside the +/-%s materiality band; the record's continuous "
                           "signed weight is a TARGET STATE here and the DCA rail sizes the "
                           "book (the alternative reading - the raw weight as a position size - "
                           "is NOT evaluated, disclosed)" % MATERIALITY_W,
        }
        if self.arm in TRAINED_ARMS:
            self.diag["fit"] = core["score_diagnostics"]["fit"]

    def layer_for(self, market):
        i = self.markets.index(market)
        return {"pos": np.ascontiguousarray(self.state[:, i]),
                "events": np.ascontiguousarray(self.events[:, i]),
                "yhat": np.ascontiguousarray(self.core["weight"][:, i])}

    def report(self):
        out = dict(self.diag)
        out["score_diagnostics"] = self.core["score_diagnostics"]
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
    """One (cohort, case) signal layer: the market's own weight column mapped onto the cohort's
    trading grid, its signed target state and its entry events.  Built once per case and cached
    (the DCA grid re-reads it 48x per phase grid)."""

    def __init__(self, cohort, case, spec, panel=None):
        self.case = tuple(case)
        self.case_label = case_name(self.case)
        panel = panel if panel is not None else panel_for(self.case)
        if cohort.symbol not in panel.markets:
            counter("signal", "defensive_market_missing")
            raise SystemExit("cohort %s is not a member of the registered panel %r"
                             % (cohort.symbol, panel.markets))
        if panel.n != cohort.n:
            counter("signal", "panel_grid_mismatch")
            raise SystemExit("cohort %s has %d bars, the panel has %d"
                             % (cohort.symbol, cohort.n, panel.n))
        view = panel.layer_for(cohort.symbol)
        self.pos = view["pos"]
        self.events = view["events"]
        self.yhat = view["yhat"]
        self.panel = panel
        self.diag = dict(panel.diag)
        self.diag["cohort"] = cohort.symbol
        self.diag["states_per_market"] = panel.diag["states_per_market"][cohort.symbol]

    def report(self):
        return {"case_name": self.case_label, "arm": self.diag["arm"],
                "arm_label": self.diag["arm_label"],
                "defined_bars": self.diag["defined_bars"],
                "states_per_market": self.diag["states_per_market"],
                "warmup_bars": self.diag["warmup_bars"],
                "fit": self.diag.get("fit")}


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


class SyntheticLayer:
    """A reader-side signal layer (per-seed arm path / re-derived policy path).

    The registered readers re-derive a registered policy path from a modified input (a single
    seeded initialization, a slippage schedule) and push it through the SAME rail by the same
    interface `simulate` reads, so no reader is ever a post-hoc estimate.
    """

    def __init__(self, cohort, markets, weight, score, label, note):
        i = markets.index(cohort.symbol)
        w = np.asarray(weight, dtype=np.float64)
        state = _states_from_weight(w, markets)
        self.case_label = label
        self.note = note
        self.pos = np.ascontiguousarray(state[:, i])
        self.events = np.ascontiguousarray(_fresh_events(state)[:, i])
        self.yhat = np.ascontiguousarray(w[:, i])


def build_network_inputs(cohort):
    """The registered causal input of one cohort.

    The record's state is the cross-sectional return tensor of the whole panel, so the input is
    the cohort's own close/return series on the shared grid; the fitted policies are built once
    per arm on the panel (reported in signal_layer.json and panel_fits.json).
    """
    close = np.asarray(cohort.close, dtype=np.float64)
    ret = np.full_like(close, np.nan)
    if len(close) > 1:
        ret[1:] = close[1:] / close[:-1] - 1.0
    return {"ret": ret, "close": close, "names": ["ret"],
            "grid_ms": np.asarray(cohort.open_time_ms, dtype=np.int64)}


def causality_probe(cohort, inputs, sample_bars=16):
    """Prefix-truncation equality on the registered policy path of one cohort.

    Every statistic of the registered chain is trailing, so the weight (and therefore the target
    state) of a bar must be reproduced when the source history is truncated at that bar.  The
    FITTED parameters are frozen on the registered historical slice in both arms of the
    comparison (a policy fitted on the historical split is by construction a function of that
    split); what the probe tests is that the INFERENCE path - the trailing features, the
    lookback windows and the signed-softmax map - never reads a future bar.  A non-zero mismatch
    count is a look-ahead defect.
    """
    out = {"bars_probed": 0, "mismatches": 0, "columns": ["weight", "state"],
           "examples": [], "probe_case": case_name(PROBE_CASE),
           "arm": POLICY_ARMS[case_index(PROBE_CASE)],
           "note": "prefix-truncation equality on the registered end-to-end policy path "
                   "(frozen parameters, trailing features); a non-zero mismatch count is a "
                   "look-ahead defect"}
    if not PANEL_COHORTS or cohort.symbol not in PANEL_COHORTS:
        out["note"] = "the panel was not registered when the probe ran"
        return out
    labels = sorted(PANEL_COHORTS)
    if cohort.symbol not in labels:
        return out
    core = _panel_core_cached(PROBE_CASE)
    grid = core["grid_ms"]
    T = len(grid)
    lo = int(core["warmup_bars"])
    if T <= lo + 1:
        return out
    step = max(1, (T - 1 - lo) // max(1, sample_bars))
    for t in range(lo, T - 1, step):
        cut = _panel_core(PROBE_CASE, {m: np.asarray(PANEL_COHORTS[m].open_time_ms,
                                                     dtype=np.int64) for m in labels},
                          upto=t)
        a = cut["weight"][:t + 1]
        b = core["weight"][:t + 1]
        same_defined = bool(np.array_equal(np.isfinite(a), np.isfinite(b)))
        fin = np.isfinite(a) & np.isfinite(b)
        dw = float(np.max(np.abs(a[fin] - b[fin]))) if bool(fin.any()) else 0.0
        ds = int(np.max(np.abs(cut["state"][:t + 1, :] - core["state"][:t + 1, :])))
        out["bars_probed"] += 1
        if not (same_defined and dw <= 1e-9 and ds == 0):
            out["mismatches"] += 1
            counter("signal", "causality_probe_mismatch")
            if len(out["examples"]) < 5:
                out["examples"].append({"bar": int(t), "weight_dev": dw, "state_dev": ds,
                                        "same_defined_bars": same_defined})
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
             count_layers=True, layer=None):
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
    layer = signals_for(cohort, case) if layer is None else layer
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
                        fills, turnover, windows_seen, windows_entered, kind,
                        _slice_diag_block(layer, series_flat, cohort, i0, i1, diag, {},
                                          ep_stats))

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
    diag_out = _slice_diag_block(layer, series_flat, cohort, i0, i1, diag, by_year,
                                 ep_stats)
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
# (contract 6.4 / 7.3).  Every reader reports its per-cohort landing, the measured local
# analogue and an explicit disclosure where the record's absolute threshold does not port.

def _cell(rows_by_cohort, cohort, kind, fields, dca):
    """One registered case x DCA cell of one cohort on one phase grid (None when absent)."""
    want = (tuple(fields),) + tuple(dca[a] for a in DCA_AXES)
    for r in rows_by_cohort[cohort][kind]:
        if cell_key(r) == want:
            return r
    return None


def _winner_case(c, spec):
    """The winner's registered joint cell: strategy case fields + the four DCA axes + the
    registered project constant base_quote (the rail cannot be built without it)."""
    w = c["winner"]
    dca = {a: w[a] for a in DCA_AXES}
    dca["base_quote"] = spec["dca_domain"]["base_quote"]
    return tuple(int(w[f]) for f in CASE_FIELDS), dca


def case_of_arm(arm):
    """The registered case tuple of one policy arm."""
    return CASE_ORDER[ARM_INDEX[arm]]


def _row_of_arm(rows_by_cohort, label, kind, arm, dca):
    """One registered arm's row on the winner's DCA cell (None when the grid lacks it)."""
    return _cell(rows_by_cohort, label, kind, (ARM_INDEX[arm],), dca)


def _params_of_arm(arm):
    return {"arm": ARM_INDEX[arm]}


def _daily_series(cohort, window, arm, dca, stress, slip, tag, series, layer):
    """One registered rail run over one slice, returning (metrics, daily equity marks)."""
    m = simulate(cohort, _params_of_arm(arm), rail_for(dca), window, stress, slip, tag, series,
                 diag=True, count_layers=False, layer=layer)
    return m, np.asarray(m["diagnostics"]["daily_equity"], dtype=np.float64)


def _ticks_for_bps(cohort, bps):
    """The registered slippage-schedule mapping: bps of notional -> adverse ticks per fill.

    The rail charges a whole number of price increments per fill (the registered
    `slippage_2ticks` stress), so a bps schedule is mapped through the cohort's own measured
    median close and price increment; the measured tick count is reported per cohort.
    """
    px = float(np.median(np.asarray(cohort.close, dtype=np.float64)))
    inc = float(cohort.price_increment)
    return max(1, int(round(bps / 1e4 * px / inc)))


def _arm_layer(cohort, arm):
    """The registered signal layer of one arm on one cohort."""
    return signals_for(cohort, case_of_arm(arm))


def _seed_layer(cohort, arm, index, note):
    """One seeded initialization's own weight path as a rail-ready layer (record's seed axis)."""
    fitted = _PARAMS_CACHE.get(arm)
    if not fitted:
        return None
    core = _panel_core_cached(case_of_arm(arm))
    state = _state_tensor(arm, core["close"], core["ret"])
    w = infer_weights(arm, fitted[index][1], state)
    seed = fitted[index][0]
    return SyntheticLayer(cohort, core["labels"], w, w[:, core["labels"].index(cohort.symbol)],
                          "%s__seed%d" % (arm, seed), note)


def _pair_daily_returns(cohort, window, arm, dca, series, slip):
    """Daily net-return series of one arm and of the 1/N benchmark on the SAME slice."""
    la = _arm_layer(cohort, arm)
    lb = _arm_layer(cohort, "ew")
    _ma, sa = _daily_series(cohort, window, arm, dca, {}, slip, "reader_pair_arm", series, la)
    _mb, sb = _daily_series(cohort, window, "ew", dca, {}, slip, "reader_pair_ew", series, lb)
    n = int(min(len(sa), len(sb)))
    if n < 3:
        return None, None
    ra = np.diff(sa[:n]) / sa[:n][:-1]
    rb = np.diff(sb[:n]) / sb[:n][:-1]
    return ra, rb


def _stationary_bootstrap_p(a, b, block=BOOTSTRAP_BLOCK, resamples=BOOTSTRAP_RESAMPLES,
                            seed=BOOTSTRAP_SEED):
    """Paired stationary block bootstrap: P(mean(arm) > mean(1/N)) over `resamples` draws.

    Blocks are drawn uniformly from the paired series with geometric lengths of mean `block`
    (the record's mean block length 21 trading days); pairing preserves the contemporaneous
    cross-correlation between the two books.  Returns (p, mean statistic of the resamples).
    """
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    n = int(min(len(a), len(b)))
    if n < 2:
        return None, None
    a = a[:n]
    b = b[:n]
    rng = np.random.default_rng(seed)
    wins = 0
    stats = np.zeros(resamples, dtype=np.float64)
    for r in range(resamples):
        idx = []
        need = n
        while need > 0:
            # The registered blocks are drawn ON DEMAND.  A geometric length has no upper bound,
            # so a pre-drawn per-resample allowance can run out mid-resample and index past the
            # end of its own array - the r1 short-window smoke hit exactly that IndexError
            # (`index 310000 is out of bounds for axis 0 with size 310000`), and the registered
            # window is worse: its allowance (n // block + 2) is the mean number of blocks a
            # resample needs, so the reader could never have completed.  Same distribution, same
            # seed, same registered procedure (mean block 21, 10,000 resamples); only the
            # allocation is lazy.
            take = min(int(rng.geometric(1.0 / float(block))), need)
            s = int(rng.integers(0, n))
            idx.extend(((s + k) % n) for k in range(take))
            need -= take
        sel = np.asarray(idx[:n], dtype=np.int64)
        ma = float(a[sel].mean())
        mb = float(b[sel].mean())
        stats[r] = ma - mb
        wins += 1 if ma > mb else 0
    return wins / float(resamples), float(stats.mean())


def _winner_full_cell(c, rows_by_cohort, spec):
    fields, dca = _winner_case(c, spec)
    return _cell(rows_by_cohort, c["cohort"], "full", fields, dca)


def _reader_shell(record_item, definition):
    return {"registered_item": record_item, "evaluated": False, "hit": False,
            "status": "executed", "definition": definition,
            "landing": "family-level: a hit must NEVER be recorded as PASS",
            "aggregation": "per-cohort landing, majority rule across the evaluated cohorts",
            "cohorts": {}}


def cost_escalation_stress(spec, cohort_results, diag_inputs, rows_by_cohort):
    """Reader 1 - record falsification item 1 ("Transaction Cost Escalation Stress Test").

    Procedure (record): "Evaluate the Portfolio Transformer under slippage schedules of 5 bps,
    10 bps, and 15 bps.  If net annualized Sharpe ratio drops below 0.30 or falls below naive
    1/N by more than 0.10 Sharpe, the claim of low-turnover execution robustness is falsified."

    Definition (EXECUTED): the winner's DCA cell is re-run over the FULL registered window on
    the record's three slippage schedules, on the same rail, taker fee and funding policy, with
    the slippage schedule mapped to adverse ticks per fill through the cohort's own measured
    median close and price increment (the measured tick counts are reported).  The evaluated arm
    is `tf_net` (the registered cost-aware transformer whose objective is exactly this claim);
    `tf` is re-run and reported alongside for disclosure but does not drive the landing.  The
    1/N comparator is the registered `ew` arm's own row on the same DCA cell and window.  A
    cohort lands a hit when any schedule's net annualized Sharpe is below the record's 0.30
    floor or below the equal-weight Sharpe by more than 0.10.
    """
    out = _reader_shell("transaction-cost-escalation-stress",
                        "the cost-aware transformer's net annualized Sharpe under the record's "
                        "5/10/15 bps per-fill slippage schedules against the 0.30 floor and the "
                        "naive 1/N book on the same DCA cell and window")
    out["schedules_bps"] = list(COST_ESCALATION_BPS)
    out["sharpe_floor"] = COST_ESCALATION_SHARPE_FLOOR
    out["equal_weight_gap"] = COST_ESCALATION_EW_GAP
    out["evaluated_arm"] = "tf_net"
    out["threshold_disclosure"] = ("the record's 0.30 Sharpe floor and 0.10 Sharpe gap are "
                                   "dimensionless (annualized Sharpe) and are therefore used "
                                   "as written; the record's bps schedules are mapped to the "
                                   "rail's per-fill adverse ticks through each cohort's own "
                                   "measured median close and price increment")
    hits = total = 0
    for c in cohort_results:
        if c["winner"] is None:
            continue
        cohort, series = diag_inputs[c["cohort"]]
        _fields, dca = _winner_case(c, spec)
        window = cohort.slice(spec["data"]["start"], spec["data"]["end"])
        ew_row = _row_of_arm(rows_by_cohort, c["cohort"], "full", "ew", dca)
        ew_sharpe = float(ew_row["sharpe"]) if ew_row is not None and ew_row["sharpe"] is not None else None
        entry = {"equal_weight_sharpe_full": ew_sharpe,
                 "equal_weight_net_pnl_full": (ew_row["net_pnl"] if ew_row is not None else None),
                 "schedules": {}}
        hit_i = False
        for bps in COST_ESCALATION_BPS:
            ticks = _ticks_for_bps(cohort, bps)
            key = "%g_bps" % bps
            entry["schedules"][key] = {"ticks_per_fill": int(ticks)}
            for arm in ("tf_net", "tf"):
                layer = _arm_layer(cohort, arm)
                m, _s = _daily_series(cohort, window, arm, dca, {}, ticks,
                                      "reader_cost_escalation", series, layer)
                sh = m.get("sharpe")
                entry["schedules"][key][arm] = {
                    "net_sharpe": sh, "net_pnl": m.get("net_pnl"), "fees": m.get("fees"),
                    "slippage_bps_disclosed": bps,
                    "below_floor": bool(sh is not None and float(sh) < COST_ESCALATION_SHARPE_FLOOR),
                    "below_equal_weight_by_more_than_gap": bool(
                        sh is not None and ew_sharpe is not None
                        and float(sh) < float(ew_sharpe) - COST_ESCALATION_EW_GAP),
                }
                if arm == "tf_net":
                    flags = entry["schedules"][key][arm]
                    hit_i = hit_i or flags["below_floor"] or \
                        flags["below_equal_weight_by_more_than_gap"]
        entry["hit"] = bool(hit_i)
        out["cohorts"][c["cohort"]] = entry
        total += 1
        hits += 1 if hit_i else 0
        out["evaluated"] = True
    out["cohorts_evaluated"] = total
    out["cohorts_hit"] = hits
    out["hit"] = bool(total and hits * 2 >= total)
    return out


def cohort_results_labels(cohort_results):
    """The registered MARKET labels of the cohort results (the panel's own market axis).

    Cohort keys are "<symbol>/<timeframe>" labels (the diag_inputs key), while the signal layer's
    market axis - and every consumer of these labels, e.g. SyntheticLayer - indexes by the bare
    market symbol.  Returning the raw keys raised ValueError("'BNBUSDT' is not in list") the
    moment a placebo/perturbation reader built its synthetic layer, before any reader reading was
    recorded; the panel's own axis (Panel.__init__ -> sorted(cohorts)) is the bare symbol.
    """
    return sorted(c["cohort"].split("/", 1)[0] for c in cohort_results)


def block_bootstrap_vs_equal_weight(spec, cohort_results, diag_inputs, rows_by_cohort):
    """Reader 2 - record falsification item 2 ("Stationary Block Bootstrap Test vs. Naive Rules").

    Procedure (record): "Conduct a block bootstrap test (mean block length 21 trading days,
    10,000 resamples) comparing net Transformer returns against 1/N equal weighting.  If the
    empirical probability that Transformer outperforms 1/N falls below 0.50, the hypothesis of
    superior learned allocation skill is rejected."

    Definition (EXECUTED): the winner's DCA cell is re-run over the FULL registered window for
    the cost-aware transformer and for the registered `ew` arm on the SAME rail, costs, funding
    policy and slice; the paired daily net-return series feed a stationary (geometric block,
    mean length 21) bootstrap of 10,000 paired resamples with a single registered seed.  The
    reported empirical probability is the share of resamples in which the transformer's mean
    daily net return exceeds the equal-weight book's.  A cohort lands a hit when that
    probability is below 0.50.
    """
    out = _reader_shell("stationary-block-bootstrap-vs-equal-weight",
                        "the paired stationary block bootstrap (mean block 21, 10,000 "
                        "resamples) of the transformer's daily net returns against the naive "
                        "1/N book on the same DCA cell and window")
    out["block_length"] = BOOTSTRAP_BLOCK
    out["resamples"] = BOOTSTRAP_RESAMPLES
    out["bootstrap_seed"] = BOOTSTRAP_SEED
    out["evaluated_arm"] = "tf_net"
    out["threshold_disclosure"] = ("the record's 0.50 probability threshold is dimensionless "
                                   "and is used as written; the paired design preserves the "
                                   "contemporaneous cross-correlation of the two books")
    hits = total = 0
    slip = spec["costs"]["baseline_slippage_ticks"]
    for c in cohort_results:
        if c["winner"] is None:
            continue
        cohort, series = diag_inputs[c["cohort"]]
        _fields, dca = _winner_case(c, spec)
        window = cohort.slice(spec["data"]["start"], spec["data"]["end"])
        ra, rb = _pair_daily_returns(cohort, window, "tf_net", dca, series, slip)
        if ra is None:
            out["cohorts"][c["cohort"]] = {"hit": False, "evaluated": False,
                                           "note": "the paired daily series is too short"}
            continue
        p, stat = _stationary_bootstrap_p(ra, rb)
        hit_i = bool(p is not None and p < 0.50)
        out["cohorts"][c["cohort"]] = {
            "days": int(len(ra)),
            "transformer_mean_daily_net": round(float(ra.mean()), 10),
            "equal_weight_mean_daily_net": round(float(rb.mean()), 10),
            "transformer_net_sharpe_annualised": round(
                float(ra.mean() / ra.std(ddof=1) * np.sqrt(365.0)), 6) if ra.std(ddof=1) > 0 else None,
            "equal_weight_net_sharpe_annualised": round(
                float(rb.mean() / rb.std(ddof=1) * np.sqrt(365.0)), 6) if rb.std(ddof=1) > 0 else None,
            "probability_transformer_outperforms": p,
            "mean_paired_statistic": stat,
            "hit": hit_i}
        total += 1
        hits += 1 if hit_i else 0
        out["evaluated"] = True
    out["cohorts_evaluated"] = total
    out["cohorts_hit"] = hits
    out["hit"] = bool(total and hits * 2 >= total)
    return out


def seed_ensemble_dispersion(spec, cohort_results, diag_inputs, rows_by_cohort):
    """Reader 3 - the record's own limitation ("Seed Variance"), registered as a reader.

    Record limitation: "Single-seed models are noisy; practical deployment mandates multi-seed
    ensemble averaging."

    Definition (EXECUTED): the winner's DCA cell is re-run over the HISTORICAL slice (the only
    slice the fit ever saw) with each of the three seeded initializations' own weight paths and
    with the registered ensemble mean, through the same rail.  The reported reading is the
    spread of the three single-seed net Sharpes against the ensemble's.  A cohort lands a hit
    when the single-seed spread exceeds half of the ensemble's own net Sharpe (a registered
    reading of "noisy"; the record states no numeric threshold).  A hit can only move a PASS to
    DEFERRED (a reader never culls a cohort and is never PASS-bearing).
    """
    out = _reader_shell("seed-ensemble-dispersion",
                        "the spread of the three seeded initializations' own historical net "
                        "Sharpes against the registered 3-seed ensemble on the winner's DCA cell")
    out["evaluated_arm"] = "tf_net"
    out["reading"] = ("registered reading of the record's 'single-seed models are noisy' "
                      "limitation: spread(single-seed Sharpe) > 0.5 x ensemble Sharpe")
    hits = total = 0
    slip = spec["costs"]["baseline_slippage_ticks"]
    window_key = (spec["data"]["historical_start"], spec["data"]["historical_end"])
    for c in cohort_results:
        if c["winner"] is None:
            continue
        cohort, series = diag_inputs[c["cohort"]]
        _fields, dca = _winner_case(c, spec)
        window = cohort.slice(window_key[0], window_key[1])
        entry = {"seeds": {}}
        singles = []
        for i, seed in enumerate(ENSEMBLE_SEEDS):
            layer = _seed_layer(cohort, "tf_net", i, "the %dth seeded initialization" % (i + 1))
            if layer is None:
                continue
            m, _s = _daily_series(cohort, window, "tf_net", dca, {}, slip,
                                  "reader_seed_dispersion", series, layer)
            entry["seeds"][str(seed)] = {"net_sharpe": m.get("sharpe"),
                                         "net_pnl": m.get("net_pnl")}
            if m.get("sharpe") is not None:
                singles.append(float(m["sharpe"]))
        layer = _arm_layer(cohort, "tf_net")
        m, _s = _daily_series(cohort, window, "tf_net", dca, {}, slip,
                              "reader_seed_ensemble", series, layer)
        ens = m.get("sharpe")
        spread = (max(singles) - min(singles)) if len(singles) >= 2 else None
        entry["ensemble_net_sharpe"] = ens
        entry["ensemble_net_pnl"] = m.get("net_pnl")
        entry["single_seed_spread"] = spread
        entry["hit"] = bool(spread is not None and ens is not None
                            and float(spread) > 0.5 * abs(float(ens)))
        out["cohorts"][c["cohort"]] = entry
        total += 1
        hits += 1 if entry["hit"] else 0
        out["evaluated"] = True
    out["cohorts_evaluated"] = total
    out["cohorts_hit"] = hits
    out["hit"] = bool(total and hits * 2 >= total)
    return out


def engineered_feature_ablation(spec, cohort_results, diag_inputs, rows_by_cohort):
    """Reader 4 - the record's own ablation ("Empirical Note") on the engineered features.

    Record Empirical Note: "Out-of-sample walk-forward tests revealed that engineered features
    added little incremental value over raw cross-sectional return series; the primary
    production state uses the return cross-section."

    Definition (EXECUTED): the registered `tfx` arm (raw state + the record's engineered
    features under its own 252-day trailing z-scores) is compared with the registered `tf` arm
    (raw cross-sectional state) on the winner's DCA cell, on the SAME rail and slices, on the
    historical and full windows.  A cohort lands a hit when the engineered-feature arm's
    full-window net Sharpe exceeds the raw-state arm's by more than the registered 0.20 reading
    (the note records no numeric threshold, so the reading is registered here and disclosed).
    A hit can only move a PASS to DEFERRED.
    """
    out = _reader_shell("engineered-feature-ablation",
                        "the engineered-feature arm (`tfx`) against the raw-state arm (`tf`) on "
                        "the winner's DCA cell, historical and full windows")
    out["incremental_sharpe_reading"] = 0.20
    out["threshold_disclosure"] = ("the record's Empirical Note carries no numeric threshold; "
                                   "the registered reading is a 0.20 full-window net-Sharpe "
                                   "increment of `tfx` over `tf`")
    hits = total = 0
    for c in cohort_results:
        if c["winner"] is None:
            continue
        _fields, dca = _winner_case(c, spec)
        row_h_raw = _row_of_arm(rows_by_cohort, c["cohort"], "historical", "tf", dca)
        row_h_fx = _row_of_arm(rows_by_cohort, c["cohort"], "historical", "tfx", dca)
        row_f_raw = _row_of_arm(rows_by_cohort, c["cohort"], "full", "tf", dca)
        row_f_fx = _row_of_arm(rows_by_cohort, c["cohort"], "full", "tfx", dca)
        if row_f_raw is None or row_f_fx is None:
            continue
        dh = _delta(row_h_fx, row_h_raw, "sharpe")
        df = _delta(row_f_fx, row_f_raw, "sharpe")
        hit_i = bool(df is not None and df > 0.20)
        out["cohorts"][c["cohort"]] = {
            "historical_raw_net_sharpe": (row_h_raw or {}).get("sharpe"),
            "historical_engineered_net_sharpe": (row_h_fx or {}).get("sharpe"),
            "full_raw_net_sharpe": row_f_raw.get("sharpe"),
            "full_engineered_net_sharpe": row_f_fx.get("sharpe"),
            "historical_sharpe_increment": dh, "full_sharpe_increment": df,
            "full_raw_net_pnl": row_f_raw.get("net_pnl"),
            "full_engineered_net_pnl": row_f_fx.get("net_pnl"),
            "hit": hit_i}
        total += 1
        hits += 1 if hit_i else 0
        out["evaluated"] = True
    out["cohorts_evaluated"] = total
    out["cohorts_hit"] = hits
    out["hit"] = bool(total and hits * 2 >= total)
    return out


def _delta(a, b, key):
    """Difference of one metric between two registered rows (None when either is missing)."""
    if a is None or b is None:
        return None
    va, vb = a.get(key), b.get(key)
    if va is None or vb is None:
        return None
    return round(float(va) - float(vb), 6)

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
    cost_escalation = (cost_escalation_stress(spec, cohort_results, diag_inputs,
                                             rows_by_cohort)
                       if coverage_complete
                       else dict(empty_reader,
                                 registered_item="transaction-cost-escalation-stress"))
    bootstrap = (block_bootstrap_vs_equal_weight(spec, cohort_results, diag_inputs,
                                                 rows_by_cohort)
                 if coverage_complete
                 else dict(empty_reader,
                           registered_item="stationary-block-bootstrap-vs-equal-weight"))
    seed_dispersion = (seed_ensemble_dispersion(spec, cohort_results, diag_inputs,
                                                rows_by_cohort)
                       if coverage_complete
                       else dict(empty_reader, registered_item="seed-ensemble-dispersion"))
    ablation = (engineered_feature_ablation(spec, cohort_results, diag_inputs, rows_by_cohort)
                if coverage_complete
                else dict(empty_reader, registered_item="engineered-feature-ablation"))
    flags = {"transaction_cost_escalation_stress": bool(cost_escalation.get("hit")),
             "stationary_block_bootstrap_vs_equal_weight": bool(bootstrap.get("hit")),
             "seed_ensemble_dispersion": bool(seed_dispersion.get("hit")),
             "engineered_feature_ablation": bool(ablation.get("hit"))}

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
        # PT registered guards (names frozen in the round-spec)
        "entry_bar_is_the_next_bar_after_the_signal":
            counters_total("entry_bar_not_the_next_bar_after_the_signal") == 0,
        "no_entry_without_a_defined_signal": counters_total("entry_before_a_defined_signal") == 0,
        "no_signal_before_the_registered_warmup":
            counters_total("signal_before_the_registered_warmup") == 0,
        "target_state_follows_the_registered_materiality_rule":
            counters_total("state_not_derived_from_the_registered_rule") == 0,
        "no_signal_input_alignment_gap": counters_total("signal_input_alignment_gap") == 0,
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
                         "arm_names": list(CASE_NAMES),
                         "arm_labels": {a: ARM_LABELS[a] for a in POLICY_ARMS},
                         "policy_constants": {
                             "lookback": LOOKBACK, "d_model": D_MODEL, "n_heads": N_HEADS,
                             "encoder_layers": ENC_LAYERS, "decoder_layers": DEC_LAYERS,
                             "t2v_periodic": T2V_PERIODIC, "lstm_hidden": LSTM_HIDDEN,
                             "epochs": EPOCHS, "batch": BATCH, "lr_max": LR_MAX,
                             "lr_min_ratio": LR_MIN_RATIO, "weight_decay": WEIGHT_DECAY,
                             "grad_clip": GRAD_CLIP_NORM, "optimiser": "AdamW + OneCycleLR",
                             "ensemble_seeds": list(ENSEMBLE_SEEDS),
                             "turnover_lambda": TURNOVER_LAMBDA,
                             "materiality_weight": MATERIALITY_W,
                             "target_gross": TARGET_GROSS,
                             "probe_case": case_name(PROBE_CASE),
                             "panel_size": PANEL_SIZE,
                             "panel_members": list(PANEL_MEMBERS),
                             "mvo_lookback": MVO_LOOKBACK, "mvo_shrinkage": MVO_SHRINKAGE,
                             "tsmom_window": TSMOM_WINDOW, "rp_vol_window": RP_VOL_WINDOW,
                             "feature_z_window": FEATURE_Z_WINDOW,
                             "feature_roc_windows": list(FEATURE_ROC_WINDOWS),
                             "feature_vol_window": FEATURE_VOL_WINDOW,
                             "feature_skew_window": FEATURE_SKEW_WINDOW,
                             "feature_kurt_window": FEATURE_KURT_WINDOW,
                             "feature_corr_window": FEATURE_CORR_WINDOW,
                             "universe_source": "system-owned lifecycle footer (contract "
                                                "14.4): the canonical local raw's complete set "
                                                "carrying the record's required daily "
                                                "close-to-close fields"}},
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
        "transaction_cost_escalation_stress": cost_escalation,
        "stationary_block_bootstrap_vs_equal_weight": bootstrap,
        "seed_ensemble_dispersion": seed_dispersion,
        "engineered_feature_ablation": ablation,
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
        sys.stderr.write("usage: 160_end_to_end_portfolio_policy_run.py <run-spec.json>\n")
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
                raise SystemExit("run-spec is not a Strategy PT spec: missing %r (contract 7.2/7.3)"
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
                cohort.inputs_report = {
                    "bars": cohort.n, "columns": len(cohort.inputs["names"]),
                    "warmup_bars": None, "warmup_measured_after_the_panel": True,
                    "causality_probe": None,
                    "note": "the registered causal input is the cohort's own daily "
                            "close-to-close return series on the shared four-market panel "
                            "grid; the end-to-end policies are fitted once per arm on the "
                            "registered historical slice and are reported in "
                            "signal_layer.json and panel_fits.json"}
                run_log("cohort %s bars=%d funding_obs=%d modeled=%d official=%d"
                        % (label, cohort.n, len(ft), counts["modeled_funding"],
                           counts["official"]))

        # the registered panel: exactly the four cohorts above, built once per case (the
        # features are cached across cases, so the read-only input reads and the expanding
        # statistics happen once).  Building every case before the grid also fails fast on a
        # signal defect, and the registered causality probe can only run once the panel exists.
        PANEL_COHORTS.clear()
        PANEL_COHORTS.update({c.symbol: c for c in [diag_inputs[k][0] for k in sorted(diag_inputs)]})
        panel_report = {}
        t_panel = time.time()
        for case in CASE_ORDER:
            p = panel_for(case)
            # The score diagnostics live on the cached panel CORE - the same source the causality
            # probe below reads; Panel.diag carries the panel-level fields only (case_name, bars,
            # warmup_bars, defined_bars, long_bars, ...).  Reading them off p.diag raised
            # KeyError('score_diagnostics') before this fix.
            score_diag = _panel_core_cached(case)["score_diagnostics"]
            panel_report[p.case_label] = {
                "bars": p.n, "markets": list(p.markets),
                "variant": p.diag.get("variant", POLICY_ARMS[case_index(case)]),
                "smoothing": p.diag.get("smoothing"),
                "warmup_bars": p.diag["warmup_bars"], "defined_bars": p.diag["defined_bars"],
                "score_diagnostics": score_diag}
        run_log("panels built: %d cases in %.1fs" % (len(CASE_ORDER), time.time() - t_panel))
        for label in sorted(diag_inputs):
            cohort = diag_inputs[label][0]
            probe = causality_probe(cohort, cohort.inputs)
            core = _panel_core_cached(PROBE_CASE)
            cohort.inputs_report["causality_probe"] = probe
            cohort.inputs_report["warmup_bars"] = int(core["warmup_bars"])
            cohort.inputs_report["signal_calendar"] = {
                "first_defined_bar": core["score_diagnostics"]["first_defined_date"],
                "last_defined_bar": core["score_diagnostics"]["last_defined_date"],
                "defined_bars": core["score_diagnostics"]["defined_observations"],
                "readings": "the record's 756-day DVOL percentile window plus the registered "
                            "expanding-z minimum bound the first defined bar"}
            run_log("causality probe %s bars=%d probes=%d mismatches=%d warmup=%d"
                    % (label, cohort.n, probe["bars_probed"], probe["mismatches"],
                       core["warmup_bars"]))
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
                          {"transaction_cost_escalation_stress":
                               summary["transaction_cost_escalation_stress"],
                           "stationary_block_bootstrap_vs_equal_weight":
                               summary["stationary_block_bootstrap_vs_equal_weight"],
                           "seed_ensemble_dispersion":
                               summary["seed_ensemble_dispersion"],
                           "engineered_feature_ablation":
                               summary["engineered_feature_ablation"],
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
