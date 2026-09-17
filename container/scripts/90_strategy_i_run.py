#!/usr/bin/env python3
"""Strategy I — Hourly Bitcoin machine-learning return forecasting with cost-aware execution
filtering (BTCUSDT USD-M perp, 1 h; direct portability), on Qlib.

Entry point for ONE pre-registered attempt.  Reads the immutable run-spec from /results (the
only source of parameters), builds the Qlib .bin store from the READ-ONLY canonical raw store
into /qlib/work, then runs the pre-registered full-backtest contract
(QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md sections 7.2 / 7.3):

    1 cohort (BTCUSDT / 1h)
    x STRATEGY domain (2 registered axes: forecast/trade direction {long_only, long_short}
      x the tabular gradient-boosting implementation {lightgbm, sklearn_hist_gbm} = 4 cases)
    x DCA domain (4 axes = 48 configs)
    x 10 registered phase grids (historical / oos / full / fee_2x / funding_2x /
      entry_delay_1_bar / slippage_2ticks / no_funding / no_funding_full /
      cost_attrition_40bps)
    = 1 x 4 x 48 x 10 = 1,920 expected case evaluations.

SOURCE CLAIM UNDER TEST (the record's own): hourly BTC/USDT log returns contain weak
non-linear predictive structure that is economically exploitable only through a cost-aware
execution hurdle.  A walk-forward retrained gradient-boosted regression forecasts the next
hour's log return from OHLCV, rolling technical indicators and EGARCH conditional-variance
features; converting that forecast straight into a sign position collapses under 10 bps
proportional costs, while the cost-aware rule

    pos_t = pos*_t   if |r_hat_{t+1}| > lambda * c * |pos*_t - pos_{t-1}|
    pos_t = pos_{t-1} otherwise            (lambda = 2.0, c = 0.0010)

cuts turnover by an order of magnitude and restores net-of-cost performance.

THE REGISTERED FORECAST CONTRACT (research-defined; the record fixes the mechanism, not its
alignment/normalisation detail — the intake caveat explicitly refuses to read its parenthetical
feature list as the source's exact feature set):

  * features  : 5 OHLCV-derived columns (1 h return, high-low range, close-open, overnight
                gap, log volume ratio against a 24-bar volume SMA) + 40 rolling technical
                candidate columns (SMA / EMA / RSI / ATR / MACD over w in {3,6,12,24,48,72,
                168,336} h) + 2 EGARCH(1,1)-Student-t columns (ln sigma_t, z_t = r_t/sigma_t).
  * selection : per fold, the 10 candidate technical columns with the largest |Spearman rank
                correlation| against the fold's training target are the "10 selected rolling
                technical indicators"; the selection reads the TRAIN segment only.
  * normalise : per fold, every model column is z-scored with TRAIN-segment mean/std.
  * folds     : 14 sequential non-anchored rolling folds, 12-month train / 3-month validation /
                3-month test, 3-month steps, the final test window clipped to the data end (the
                record's 27-fold design is a function of its 2017-12..2026-01 sample, of which
                49.5% is absent from the canonical raw; the fold STRUCTURE is preserved).
  * model     : tabular gradient-boosted regression on (X_t -> r_{t+1}) pairs; the pinned
                production image ships LightGBM as its declared gradient-boosting extra and has
                no XGBoost, so the registered case axis carries both implementations the image
                provides (lightgbm 4.7.0 and sklearn 1.9.1 HistGradientBoostingRegressor) rather
                than one library name.  Every hyper-parameter is a project pre-registered
                constant, identical for both implementations; the 3-month validation segment is
                measured (validation MSE per fold) but selects nothing in this round (the
                source's loss-/IC-/IR*-best selectors are a disclosed not-evaluated item).
  * signal    : the record's cost-aware filter, applied to the forecast of the bar's own fold,
                evaluated causally bar by bar from the window start (FLAT at the start).

ENTRY / EXIT (the record's own position semantics wrapped in the card-mandated DCA rail):
an ENTRY event is a bar whose filtered target position CHANGES to a non-zero value (long-only:
0 -> 1; long-short: 0 -> +-1 or a sign flip); execution is the OPEN of the next bar.  A
persistent position never re-enters after a flatten: only a new position-change event opens a
new episode.  An episode of direction d ends when (1) the rail take profit at
running_average_cost x (1 +- breakeven_tp_pct) fills reduce-only, (2) the resting invalidation
at running_average_cost x (1 -+ invalidation_pct) fills, (3) the record's own exit condition
holds at a bar's close - the filtered target position is no longer d (0 in long-only, the
opposite sign in long-short) - and every layer is flattened at the NEXT bar's open, or (4) the
slice ends (reduce-only flatten of every layer).  tranche #1 is the initial entry; adverse-price
scale-ins follow the registered ladder (level_k price = initial_entry_price x (1 -+ spacing_pct
x k), k = 1..10, i.e. at most 11 routine active levels of the 12-tranche rail).

DCA is executed as real order/fill accounting (an episode state machine over the bars); nothing
is estimated after the fact.  Every legal (strategy params x DCA config) cell is evaluated on
every registered phase grid, and the family gate is the **cohort-level survivor** rule of
contract section 7.3: one deterministic historical-only winner per cohort, then OOS / full /
robustness / parameter-neighbourhood evidence for that same winner.  This family has exactly
one cohort, so the cohort survivor count is 0 or 1.  The record's three-item falsification
battery is registered as three FAMILY-level readers (forward 12-month Sharpe, cost sensitivity
at c = 15/20/25 bps, cross-asset replication on ETHUSDT and SOLUSDT with the identical
walk-forward pipeline); a reader hit can only move a PASS to DEFERRED and is never PASS-bearing.

Writes only:
  * /qlib/work/**      (rebuildable derived/cache area, INV-5)
  * <attempt_dir>/**   (result.json, artifacts/, logs/, state.json)
Never writes /data/raw (read-only mount).

usage: 90_strategy_i_run.py <run-spec.json>
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
WORK_ROOT = "/qlib/work/strategyI-v1"
CSV_ROOT = os.path.join(WORK_ROOT, "csv")
QLIB_DIR = os.path.join(WORK_ROOT, "qlib-data")
MS_PER_DAY = 86400000
MS_PER_HOUR = 3600000
FIELDS = ["open", "high", "low", "close", "volume"]
START_EQUITY = 30000.0
MAX_ADD_LEVELS = 10
# ------------------------------------- registered forecast contract (RESEARCH_DEFINED, frozen)
# The record fixes the MECHANISM (walk-forward retrained tree-ensemble regression on OHLCV +
# selected rolling technical indicators + EGARCH conditional-variance features, then the
# lambda/c cost-aware execution filter) but none of its timing/alignment/feature detail.  This
# block mirrors `runtime/strategy_i_v1_counts.py` (FORECAST_CONTRACT / FORECAST_CONSTANTS) and
# the pre-registered search axes; every value is fixed BEFORE the first run.
DIRECTION_MODES = ("long_only", "long_short")        # searched axis 1: the leg set
MODEL_IMPLS = ("lightgbm", "sklearn_hist_gbm")        # searched axis 2: the GBM implementation
CASE_FIELDS = ("dir_long_only", "dir_long_short", "model_lightgbm", "model_sklearn")
CASE_ORDER = tuple(
    tuple([1 if a == i else 0 for a in range(2)] + [1 if b == j else 0 for b in range(2)])
    for j in range(len(MODEL_IMPLS)) for i in range(len(DIRECTION_MODES)))
CASE_NAMES = tuple("%s__%s" % (DIRECTION_MODES[i], MODEL_IMPLS[j])
                   for j in range(len(MODEL_IMPLS)) for i in range(len(DIRECTION_MODES)))
LAMBDA_COST = 2.0            # source-specified (the record fixes lambda = 2.0)
COST_C = 0.0010              # source-specified: 10 bps per unit of turnover
TA_WINDOWS = (3, 6, 12, 24, 48, 72, 168, 336)   # the record's own lookback set, in hours
TA_FAMILIES = ("sma", "ema", "rsi", "atr", "macd")
SELECTED_TA_COUNT = 10       # the record's "10 selected rolling technical indicators"
VOLUME_SMA_PERIOD = 24       # project pre-registered constant (volume-ratio denominator)
EGARCH_P, EGARCH_Q = 1, 1    # the record names p,q but fixes neither; (1,1) is the constant
EGARCH_MAXITER = 200         # project pre-registered optimiser budget
FOLD_TRAIN_MONTHS = 12
FOLD_VAL_MONTHS = 3
FOLD_TEST_MONTHS = 3
FOLD_STEP_MONTHS = 3
MAX_FOLDS = 14               # bounded by the locally available window (13 complete + 1 clipped)
SEED = 20260917
LGBM_PARAMS = {"n_estimators": 300, "learning_rate": 0.05, "num_leaves": 31,
               "min_child_samples": 50, "subsample": 0.8, "subsample_freq": 1,
               "colsample_bytree": 0.8, "random_state": SEED, "verbose": -1}
HGB_PARAMS = {"max_iter": 300, "learning_rate": 0.05, "max_leaf_nodes": 31,
              "min_samples_leaf": 50, "l2_regularization": 0.0, "random_state": SEED}
# Execution stress reruns (full-window slice), contract 7.2 robustness.
STRESS = [
    ("fee_2x", {"fee_mult": 2.0}),
    ("funding_2x", {"funding_mult": 2.0}),
    ("entry_delay_1_bar", {"entry_delay_1_bar": True}),
    ("slippage_2ticks", {"slip_ticks": 2}),
]
# Registered cost-attrition grid: 8 x the 5 bps taker fee = 40 bps per fill.
COST_ATTRITION_STRESS = {"fee_mult": 8.0}
# The record's cost-sensitivity falsification item, expressed in the same fee_mult currency:
# the registered taker fee is 5 bps, so multiples 2/3/4/5 are exactly c = 10/15/20/25 bps.
COST_SENSITIVITY_MULTS = (1.0, 2.0, 3.0, 4.0, 5.0)
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
# composite (direction, model implementation) axis; its registered order is CASE_ORDER.
AXES = ("window_case", "spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct")
DCA_AXES = ("spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct")
SELECTOR_VERSION = "cohort-selector-v1"
DISPOSITION_VERSION = "cohort-disposition-v1"
CONTRACT_SEMANTICS_VERSION = "v1.4.0"
ENGINE_VERSION = "i-v1-engine-1.0.0"
ENGINE_SEMANTICS = ("walk-forward retrained tabular gradient-boosting regression (14 "
                    "non-anchored rolling folds, 12m train / 3m val / 3m test) on OHLCV + the "
                    "10 train-selected rolling technical indicators + EGARCH(1,1)-t "
                    "conditional-variance features, the record's lambda/c cost-aware execution "
                    "filter turned into position-change entry events at the next bar's open, "
                    "the record's position exit at the next bar's open, plus the registered DCA "
                    "rail (see module docstring)")
WINNER_METRIC_KEYS = ("net_pnl", "sharpe", "episodes", "ending_equity", "fees", "funding",
                      "max_dd_usdt", "max_dd_pct", "max_effective_leverage",
                      "capital_utilization", "annualized_return", "cagr")
# csv row field order (kept identical for every phase grid)
ROW_FIELDS = ("symbol", "timeframe", "window_kind", "window_case", "case_name",
              "dir_long_only", "dir_long_short", "model_lightgbm", "model_sklearn",
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
# the CLOSED interval [entry - 1s, exit + 1s] and every clock comparison is exact on the 1h
# bar grid.  A deviation beyond the tolerance is a real mapping defect and trips a counter.
MS_JITTER_TOLERANCE_MS = 1000
COUNTER_NAMES = ("entry_after_slice_end", "slice_end_flatten", "record_exit_taken",
                 "record_exit_deferred_to_slice_end", "entry_refused_exhausted",
                 "ladder_cap_reached", "stop_at_ladder_boundary",
                 "entry_bar_not_the_next_bar_after_the_signal", "reentry_skipped_in_position",
                 "case_not_registered", "bar_grid_not_contiguous", "funding_bar_out_of_hold",
                 "entry_before_a_defined_forecast", "prediction_unavailable_on_test_bar",
                 "feature_not_finite_on_test_bar", "train_segment_too_short",
                 "egarch_fit_failed", "ta_selection_insufficient_candidates",
                 "causality_probe_mismatch", "fold_test_window_empty")
COUNTERS = {}

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


def case_tuple(p):
    return tuple(int(p[f]) for f in CASE_FIELDS)


def case_index(case):
    return CASE_ORDER.index(tuple(case))


def case_name(case):
    return CASE_NAMES[case_index(case)]


def case_direction(case):
    """Registered direction index of one case: 0 = long_only, 1 = long_short."""
    return list(case).index(1, 0, 2)


def case_model(case):
    """Registered model-implementation index of one case: 0 = lightgbm, 1 = sklearn_hist_gbm."""
    return list(case).index(1, 2, 4) - 2


def case_fields(dir_i, model_i):
    """The registered one-hot case tuple of (direction index, model-implementation index)."""
    return tuple([1 if k == dir_i else 0 for k in range(2)]
                 + [1 if k == model_i else 0 for k in range(2)])


def day_range(cohort, start_date, end_date):
    """Bar range [lo, hi) of the half-open day interval [start_date, end_date) on the bar grid."""
    lo = utc_ms(start_date)
    hi = utc_ms(end_date)
    return (int(np.searchsorted(cohort.open_time_ms, lo, side="left")),
            int(np.searchsorted(cohort.open_time_ms, hi, side="left")))

# --------------------------------------------------------------------------- forecast science

def _sma(x, w):
    out = np.full(len(x), np.nan)
    if w > 0 and len(x) >= w:
        c = np.cumsum(np.insert(x, 0, 0.0))
        out[w - 1:] = (c[w:] - c[:-w]) / float(w)
    return out


def _ema(x, w):
    """Recursive EMA seeded at the first bar (the registered warm-up floor hides the seed)."""
    n = len(x)
    out = np.full(n, np.nan)
    if n == 0 or w <= 0:
        return out
    a = 2.0 / (w + 1.0)
    prev = float(x[0])
    out[0] = prev
    for t in range(1, n):
        prev = a * float(x[t]) + (1.0 - a) * prev
        out[t] = prev
    return out


def _wilder(x, w):
    """Wilder's recursive average: seed = mean of the first w observations, then a(w-1)/w step."""
    n = len(x)
    out = np.full(n, np.nan)
    if n < w or w <= 0:
        return out
    acc = 0.0
    for t in range(w):
        acc += float(x[t])
    prev = acc / float(w)
    out[w - 1] = prev
    for t in range(w, n):
        prev = (prev * (w - 1) + float(x[t])) / float(w)
        out[t] = prev
    return out


def _true_range(h, l, c):
    tr = np.empty(len(h))
    tr[0] = h[0] - l[0]
    if len(h) > 1:
        pc = c[:-1]
        tr[1:] = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - pc), np.abs(l[1:] - pc)))
    return tr


def _rsi(c, w):
    n = len(c)
    d = np.zeros(n)
    d[1:] = np.diff(c)
    gain = np.where(d > 0.0, d, 0.0)
    loss = np.where(d < 0.0, -d, 0.0)
    ag = _wilder(gain, w)
    al = _wilder(loss, w)
    out = np.full(n, np.nan)
    for t in range(n):
        g, x = ag[t], al[t]
        if not (np.isfinite(g) and np.isfinite(x)):
            continue
        if x <= 0.0:
            out[t] = 100.0 if g > 0.0 else 50.0     # a flat window is neutral, registered
        else:
            rs = g / x
            out[t] = 100.0 - 100.0 / (1.0 + rs)
    return out


# Registered per-column warm-up floor: the first bar index at which a column is regarded as
# defined.  An EMA/RSI/ATR recursion carries a seed whose influence decays geometrically, so
# 3 x w bars (6 x w for the MACD leg, which pairs EMA_w with EMA_2w) are dropped; the floors are
# applied as NaN on the matrix and are therefore visible in every consumer.
TA_WARMUP_MULTIPLIER = {"sma": 1, "ema": 3, "rsi": 3, "atr": 3, "macd": 6}
MIN_SELECTION_ROWS = 500
MIN_TRAIN_ROWS = 1000


def feature_names():
    names = ["ret1", "range_hl", "close_open", "gap", "vol_ratio"]
    for fam in TA_FAMILIES:
        for w in TA_WINDOWS:
            names.append("%s_%d" % (fam, w))
    return names


def ta_column_names():
    return ["%s_%d" % (f, w) for f in TA_FAMILIES for w in TA_WINDOWS]


def build_feature_matrix(cohort):
    """The registered causal candidate matrix (n x 45): 5 OHLCV-derived + 40 technical columns."""
    c, h, l, o, v = cohort.close, cohort.high, cohort.low, cohort.open, cohort.volume
    n = cohort.n
    cols = {}
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        ret1 = np.full(n, np.nan)
        ret1[1:] = np.log(c[1:] / c[:-1])
        cols["ret1"] = ret1
        cols["range_hl"] = np.log(h / l)
        cols["close_open"] = np.log(c / o)
        gap = np.full(n, np.nan)
        gap[1:] = np.log(o[1:] / c[:-1])
        cols["gap"] = gap
        cols["vol_ratio"] = np.log(v / _sma(v, VOLUME_SMA_PERIOD))
        ema_cache = {}
        for fam in TA_FAMILIES:
            for w in TA_WINDOWS:
                if fam == "sma":
                    cols["%s_%d" % (fam, w)] = np.log(c / _sma(c, w))
                elif fam == "ema":
                    cols["%s_%d" % (fam, w)] = np.log(c / _ema(c, w))
                elif fam == "rsi":
                    cols["%s_%d" % (fam, w)] = (_rsi(c, w) - 50.0) / 50.0
                elif fam == "atr":
                    cols["%s_%d" % (fam, w)] = _wilder(_true_range(h, l, c), w) / c
                else:
                    e1 = ema_cache.setdefault(w, _ema(c, w))
                    e2 = ema_cache.setdefault(2 * w, _ema(c, 2 * w))
                    cols["%s_%d" % (fam, w)] = (e1 - e2) / c
    names = feature_names()
    M = np.empty((n, len(names)), dtype=np.float64)
    for j, nm in enumerate(names):
        M[:, j] = cols[nm]
    floors = {"ret1": 1, "gap": 1, "range_hl": 0, "close_open": 0,
              "vol_ratio": VOLUME_SMA_PERIOD - 1}
    for fam in TA_FAMILIES:
        for w in TA_WINDOWS:
            floors["%s_%d" % (fam, w)] = TA_WARMUP_MULTIPLIER[fam] * w
    for j, nm in enumerate(names):
        M[:max(0, floors[nm]), j] = np.nan
    return {"names": names, "matrix": M, "floors": floors, "n": n}


def rank_data(x):
    """Average ranks (ties share the mean rank).  stdlib+numpy, so the self-check needs no scipy."""
    n = len(x)
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty(n, dtype=np.float64)
    ranks[order] = np.arange(1, n + 1, dtype=np.float64)
    xs = x[order]
    i = 0
    while i < n:
        j = i + 1
        while j < n and xs[j] == xs[i]:
            j += 1
        if j - i > 1:
            ranks[order[i:j]] = (i + 1 + j) / 2.0
        i = j
    return ranks


def select_technical_columns(matrix, names, target, rows):
    """The record's "10 selected rolling technical indicators", chosen on the TRAIN rows only.

    |Spearman rank correlation| against the training target, descending; ties break on the
    registered column name (lexical), so the selection is deterministic and OOS-blind.
    """
    cand = ta_column_names()
    idx = [names.index(nm) for nm in cand]
    sub = matrix[rows][:, idx]
    yv = target[rows]
    ok = np.isfinite(sub).all(axis=1) & np.isfinite(yv)
    if int(ok.sum()) < MIN_SELECTION_ROWS:
        counter("forecast", "ta_selection_insufficient_candidates")
        return list(cand[:SELECTED_TA_COUNT]), []
    r = rank_data(yv[ok])
    scored = []
    for j, nm in enumerate(cand):
        col = sub[ok, j]
        if np.all(col == col[0]):
            scored.append((0.0, nm))
            continue
        rho = float(np.corrcoef(rank_data(col), r)[0, 1])
        scored.append((abs(rho) if np.isfinite(rho) else 0.0, nm))
    scored.sort(key=lambda kv: (-kv[0], kv[1]))
    return [nm for _s, nm in scored[:SELECTED_TA_COUNT]], scored


def _e_abs_z(nu):
    """E|z| of the standardised (unit-variance) Student-t with nu degrees of freedom."""
    return (2.0 * math.sqrt(nu - 2.0) * math.gamma((nu + 1.0) / 2.0)
            / (math.sqrt(math.pi) * math.gamma(nu / 2.0) * (nu - 1.0)))


EGARCH_BOUNDS = ((-0.01, 0.01), (-5.0, 5.0), (-1.5, 1.5), (-1.5, 1.5), (0.5, 0.999),
                 (2.5, 30.0))


def _egarch_nll(theta, r):
    """-log-likelihood of EGARCH(1,1) with standardised Student-t innovations and constant mean."""
    mu, omega, alpha, gamma, beta, nu = theta
    if not (2.05 < nu < 60.0 and 0.0 < beta < 0.9999):
        return 1e12
    n = len(r)
    # the recursion is seeded with the PARAMETRIC steady state (omega / (1 - beta)): a sample
    # variance seed would make the likelihood depend on bars outside the segment being sized
    lns2 = min(max(omega / max(1e-6, 1.0 - beta), -30.0), 30.0)
    eabs = _e_abs_z(nu)
    lg1 = math.lgamma((nu + 1.0) / 2.0)
    lg2 = math.lgamma(nu / 2.0)
    cst = 0.5 * math.log(math.pi * (nu - 2.0))
    nll = 0.0
    for t in range(1, n):
        z = (r[t - 1] - mu) / math.exp(0.5 * lns2)
        lns2 = omega + alpha * (abs(z) - eabs) + gamma * z + beta * lns2
        lns2 = min(max(lns2, -30.0), 30.0)
        zt = (r[t] - mu) / math.exp(0.5 * lns2)
        nll += 0.5 * lns2 + cst + (nu + 1.0) / 2.0 * math.log1p(zt * zt / (nu - 2.0)) - lg1 + lg2
    return nll


def egarch_fit(r):
    """MLE of EGARCH(1,1)-t on one training segment; returns (theta, converged)."""
    from scipy.optimize import minimize
    r = np.asarray(r, dtype=np.float64)
    r = r[np.isfinite(r)]
    if len(r) < 200:
        return None, False
    var = float(np.var(r))
    var = var if var > 0.0 else 1e-8
    x0 = [float(np.mean(r)), 0.10 * math.log(var), 0.10, -0.05, 0.90, 5.0]
    try:
        res = minimize(_egarch_nll, x0, args=(r,), method="L-BFGS-B", bounds=EGARCH_BOUNDS,
                       options={"maxiter": EGARCH_MAXITER})
    except Exception:  # noqa: BLE001 - a failed fit is counted, never silently ignored
        return None, False
    if res is None or not np.isfinite(res.fun) or not np.all(np.isfinite(res.x)):
        return None, False
    theta = [float(v) for v in res.x]
    ok = bool(res.success) and 0.0 < theta[4] < 0.999
    return theta, ok


def egarch_sigma(r, theta):
    """Causal conditional-volatility path: sigma_t uses r_{<=t-1} and the fitted parameters."""
    mu, omega, alpha, gamma, beta, nu = theta
    n = len(r)
    # the recursion is seeded with the PARAMETRIC steady state (omega / (1 - beta)), never with
    # a sample variance of the array handed in: a sample seed would make sigma_t depend on bars
    # after t and break prefix invariance (the self-check asserts it).
    lns2 = min(max(omega / max(1e-6, 1.0 - beta), -30.0), 30.0)
    eabs = _e_abs_z(nu)
    out = np.empty(n, dtype=np.float64)
    out[0] = math.exp(0.5 * lns2)
    for t in range(1, n):
        prev = r[t - 1]
        # the registered return series always starts with a non-finite element (bar 0 has no
        # previous close) and a fold prefix can carry one; a non-finite observation carries the
        # variance forward unchanged instead of poisoning the whole path with NaN.
        if prev == prev and prev not in (float("inf"), float("-inf")):
            z = (prev - mu) / math.exp(0.5 * lns2)
            lns2 = omega + alpha * (abs(z) - eabs) + gamma * z + beta * lns2
            lns2 = min(max(lns2, -30.0), 30.0)
        out[t] = math.exp(0.5 * lns2)
    return out


def fold_bounds(spec):
    """The registered non-anchored rolling folds: 12m train / 3m val / 3m test, 3m steps.

    The fold STRUCTURE is the record's; the fold COUNT is bounded by the locally available
    window (the record's own 2017-12..2026-01 sample is only 49.5% present in the canonical
    raw).  Boundaries are half-open [start, end) day strings, so a bar belongs to exactly one
    segment of exactly one fold.
    """
    start, end = spec["data"]["start"], spec["data"]["end"]
    y0, m0 = int(start[:4]), int(start[5:7])

    def add_months(k):
        y = y0 + (m0 - 1 + k) // 12
        m = (m0 - 1 + k) % 12 + 1
        return "%04d-%02d-01" % (y, m)

    folds = []
    for k in range(MAX_FOLDS):
        t0 = add_months(FOLD_STEP_MONTHS * k + FOLD_TRAIN_MONTHS + FOLD_VAL_MONTHS)
        if t0 >= end:
            break
        t1 = add_months(FOLD_STEP_MONTHS * k + FOLD_TRAIN_MONTHS + FOLD_VAL_MONTHS
                        + FOLD_TEST_MONTHS)
        folds.append({"fold": k,
                      "train": (add_months(FOLD_STEP_MONTHS * k),
                                add_months(FOLD_STEP_MONTHS * k + FOLD_TRAIN_MONTHS)),
                      "val": (add_months(FOLD_STEP_MONTHS * k + FOLD_TRAIN_MONTHS), t0),
                      "test": (t0, min(t1, end))})
    return folds


def fit_model(model_impl, X, y):
    """The registered tabular gradient-boosting regression of one implementation."""
    if model_impl == "lightgbm":
        import lightgbm as lgb
        model = lgb.LGBMRegressor(**LGBM_PARAMS)
        model.fit(X, y)
        return model
    if model_impl == "sklearn_hist_gbm":
        from sklearn.ensemble import HistGradientBoostingRegressor
        model = HistGradientBoostingRegressor(**HGB_PARAMS)
        model.fit(X, y)
        return model
    counter("forecast", "case_not_registered")
    raise SystemExit("unregistered model implementation %r (registered: %r)"
                     % (model_impl, MODEL_IMPLS))


def _model_matrix(feats, selected, sigma, ret1):
    """The registered model columns: 5 OHLCV-derived + the 10 selected technical + 2 EGARCH.

    EGARCH columns: ln sigma_t and the standardised residual z_t = r_t / sigma_t.  `sigma` is
    the fold's causal path over the prefix [0, te1); rows beyond that prefix are left NaN,
    because no fold ever trains or predicts on them (the regression self-check covers the
    short-prefix case that shipped as a bug in the first attempt).
    """
    names = feats["names"]
    keep = ["ret1", "range_hl", "close_open", "gap", "vol_ratio"] + list(selected)
    rows = [names.index(nm) for nm in keep]
    base = feats["matrix"][:, rows]
    n = base.shape[0]
    m = min(len(sigma), n)
    eg = np.full((n, 2), np.nan, dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        eg[:m, 0] = np.log(sigma[:m])
        eg[:m, 1] = ret1[:m] / sigma[:m]
    return np.column_stack([base, eg]), keep + ["egarch_log_sigma", "egarch_z"]


def walk_forward_predictions(cohort, model_impl, spec):
    """Every registered fold's fit + test-segment forecast, plus the fold report.

    Cached per cohort and implementation: the two direction cases of one implementation share
    exactly the same forecasts (only the position rule differs).
    """
    cache = getattr(cohort, "_pred_cache", None)
    if cache is None:
        cache = cohort._pred_cache = {}
    if model_impl in cache:
        return cache[model_impl]
    feats = cohort.features
    n = cohort.n
    c = cohort.close
    ret1 = np.full(n, np.nan)
    ret1[1:] = np.log(c[1:] / c[:-1])
    yhat = np.full(n, np.nan)
    folds = []
    for fd in fold_bounds(spec):
        tr0, tr1 = day_range(cohort, *fd["train"])
        va0, va1 = day_range(cohort, *fd["val"])
        te0, te1 = day_range(cohort, *fd["test"])
        rep = {"fold": fd["fold"], "train": list(fd["train"]), "val": list(fd["val"]),
               "test": list(fd["test"]), "train_bars": tr1 - tr0, "test_bars": te1 - te0,
               "selected_columns": [], "val_mse": None, "test_predictions": 0,
               "egarch_theta": None, "egarch_converged": False}
        if te1 <= te0:
            counter("forecast", "fold_test_window_empty")
            folds.append(rep)
            continue
        if tr1 - tr0 < MIN_TRAIN_ROWS:
            counter("forecast", "train_segment_too_short")
        theta, conv = egarch_fit(ret1[tr0:tr1])
        if theta is None:
            counter("forecast", "egarch_fit_failed")
            theta = [0.0, 0.0, 0.0, 0.0, 0.0, 5.0]
        rep["egarch_theta"] = [round(v, 6) for v in theta]
        rep["egarch_converged"] = bool(conv)
        sigma = egarch_sigma(ret1[:te1], theta)
        rows_tr = np.arange(tr0, max(tr0, tr1 - 1))
        y_tr = ret1[rows_tr + 1]
        selected, scored = select_technical_columns(feats["matrix"], feats["names"], ret1, rows_tr)
        rep["selected_columns"] = list(selected)
        rep["selection_top_scores"] = [[nm, round(float(s), 6)] for s, nm in scored[:12]]
        X_all, col_names = _model_matrix(feats, selected, sigma, ret1)
        rep["model_columns"] = col_names
        ok_tr = np.isfinite(X_all[rows_tr]).all(axis=1) & np.isfinite(y_tr)
        tr_rows = rows_tr[ok_tr]
        if len(tr_rows) < MIN_TRAIN_ROWS:
            counter("forecast", "train_segment_too_short")
        Xtr = X_all[tr_rows]
        ytr = ret1[tr_rows + 1]
        mu = Xtr.mean(axis=0)
        sd = Xtr.std(axis=0, ddof=1)
        sd = np.where(sd > 0.0, sd, 1.0)
        model = fit_model(model_impl, (Xtr - mu) / sd, ytr)
        rows_te = np.arange(te0, te1)
        ok_te = np.isfinite(X_all[rows_te]).all(axis=1)
        if not bool(ok_te.all()):
            counter("forecast", "feature_not_finite_on_test_bar",
                    int((~ok_te).sum()))
        te_rows = rows_te[ok_te]
        if len(te_rows):
            yhat[te_rows] = model.predict((X_all[te_rows] - mu) / sd)
        if int(ok_te.sum()) != te1 - te0:
            counter("forecast", "prediction_unavailable_on_test_bar",
                    int((te1 - te0) - int(ok_te.sum())))
        rep["test_predictions"] = int(len(te_rows))
        rows_va = np.arange(va0, va1)
        if len(rows_va):
            ok_va = np.isfinite(X_all[rows_va]).all(axis=1)
            va_rows = rows_va[ok_va]
            if len(va_rows):
                pred = model.predict((X_all[va_rows] - mu) / sd)
                err = pred - ret1[va_rows + 1]
                rep["val_mse"] = float(np.mean(err * err))
        folds.append(rep)
    out = {"yhat": yhat, "folds": folds, "model_impl": model_impl}
    cache[model_impl] = out
    return out


def build_position_path(yhat, direction_mode):
    """The record's cost-aware execution filter, evaluated causally from the window start.

        pos*_t = 1 if yhat_t > 0 else (0 in long-only mode / -1 in long-short mode)
        pos_t  = pos*_t    iff |yhat_t| > lambda * c * |pos*_t - pos_{t-1}|
        pos_t  = pos_{t-1} otherwise                       (lambda = 2.0, c = 0.0010)

    A bar whose forecast is undefined carries the previous position forward (no update, no
    event).  `events[t]` is the ENTRY event of the registered episode source: a bar at which the
    filtered position CHANGES to a non-zero value.
    """
    n = len(yhat)
    pos = np.zeros(n, dtype=np.int64)
    events = np.zeros(n, dtype=np.int64)
    prev = 0
    hurdle_cleared = flips = 0
    for t in range(n):
        p = yhat[t]
        if not np.isfinite(p):
            pos[t] = prev
            continue
        star = 1 if p > 0.0 else (0 if direction_mode == "long_only" else -1)
        if abs(p) > LAMBDA_COST * COST_C * abs(star - prev):
            new = star
            hurdle_cleared += 1
        else:
            new = prev
        pos[t] = new
        if new != prev:
            if new != 0:
                events[t] = new
            if prev != 0 and new != 0:
                flips += 1
        prev = new
    return {"pos": pos, "events": events,
            "diag": {"bars_with_forecast": int(np.isfinite(yhat).sum()),
                     "hurdle_cleared_bars": int(hurdle_cleared),
                     "position_events": int(np.count_nonzero(events)),
                     "sign_flips": int(flips),
                     "bars_long": int((pos > 0).sum()), "bars_short": int((pos < 0).sum()),
                     "position_change_bars": int(np.count_nonzero(np.diff(pos))),
                     "direction_mode": direction_mode,
                     "lambda": LAMBDA_COST, "c": COST_C,
                     "one_unit_hurdle": LAMBDA_COST * COST_C,
                     "two_unit_hurdle": 2.0 * LAMBDA_COST * COST_C}}


class SignalLayer:
    """One (cohort, case) signal layer: the fold forecasts, the filtered position path and the
    registered entry events.  Built once per case and cached (the DCA grid re-reads it 48x)."""

    def __init__(self, cohort, case, spec):
        self.case = tuple(case)
        self.case_label = case_name(self.case)
        self.direction_mode = DIRECTION_MODES[case_direction(self.case)]
        self.model_impl = MODEL_IMPLS[case_model(self.case)]
        pred = walk_forward_predictions(cohort, self.model_impl, spec)
        self.yhat = pred["yhat"]
        self.fold_report = pred["folds"]
        path = build_position_path(self.yhat, self.direction_mode)
        self.pos = path["pos"]
        self.events = path["events"]
        self.diag = dict(path["diag"], model_impl=self.model_impl, case_name=self.case_label)

    def report(self):
        return {"case_name": self.case_label, "direction_mode": self.direction_mode,
                "model_impl": self.model_impl,
                "folds_measured": len(self.fold_report),
                "folds_egarch_converged": sum(1 for f in self.fold_report
                                              if f["egarch_converged"]),
                "folds": self.fold_report, "position_diagnostics": self.diag}


def signals_for(cohort, case):
    """The cached signal layer of one registered strategy case."""
    key = tuple(case)
    layer = cohort._signal_cache.get(key)
    if layer is None:
        layer = SignalLayer(cohort, key, cohort.spec)
        cohort._signal_cache[key] = layer
    return layer


def signal_report_of(cohort):
    """The registered forecast/position diagnostics of every case of one cohort."""
    out = {}
    for case in CASE_ORDER:
        layer = cohort._signal_cache.get(case)
        if layer is None:
            continue
        out[layer.case_label] = layer.report()
    return out


def fold_range_of(cohort, start_date, end_date):
    """The bar range of a date window, half-open on the day grid."""
    return day_range(cohort, start_date, end_date)


def causality_probe(cohort, feats, sample_bars=120):
    """Executable no-look-ahead control on the feature layer.

    For a deterministic sample of bars, the registered technical columns are recomputed from
    the TRUNCATED prefix bars[0..t] and compared with the full-window matrix.  Prefix invariance
    is what causality means here: a column that can see the future disagrees as soon as the
    future is removed.  (The EGARCH recursion is causal by construction - sigma_t depends on
    z_{t-1} and sigma_{t-1} only - and its column is rebuilt from the same prefix below.)
    """
    n = cohort.n
    if n < 4:
        return {"bars_probed": 0, "mismatches": 0, "columns": []}
    c, h, l, o, v = cohort.close, cohort.high, cohort.low, cohort.open, cohort.volume
    names = feats["names"]
    floors = feats["floors"]
    step = max(1, n // sample_bars)
    probed = mismatched = 0
    bad = []
    for t in range(step, n, step):
        sub = {"close": c[:t + 1], "high": h[:t + 1], "low": l[:t + 1], "open": o[:t + 1],
               "volume": v[:t + 1]}
        with np.errstate(divide="ignore", invalid="ignore"):
            chk = {
                "rsi_24": (_rsi(sub["close"], 24)[t] - 50.0) / 50.0,
                "atr_48": (_wilder(_true_range(sub["high"], sub["low"], sub["close"]), 48)[t]
                           / sub["close"][t]),
                "ema_168": math.log(sub["close"][t] / _ema(sub["close"], 168)[t]),
                "sma_72": math.log(sub["close"][t] / _sma(sub["close"], 72)[t]),
                "vol_ratio": math.log(sub["volume"][t] / _sma(sub["volume"], 24)[t]),
            }
        probed += 1
        for nm, val in chk.items():
            if t < floors[nm]:
                continue          # below the registered warm-up floor the matrix is NaN by design
            ref = feats["matrix"][t, names.index(nm)]
            if not (np.isfinite(val) and np.isfinite(ref) and abs(val - ref) <= 1e-9):
                mismatched += 1
                if len(bad) < 8:
                    bad.append({"bar": int(t), "column": nm, "truncated": val, "full": ref})
    if mismatched:
        counter("forecast", "causality_probe_mismatch", mismatched)
    return {"bars_probed": probed, "mismatches": mismatched, "columns": sorted(chk),
            "examples": bad, "note": "prefix-truncation equality on the registered technical "
                                     "columns; a non-zero mismatch count is a look-ahead defect"}

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
                    if first_ms is not None and ms - last_ms != MS_PER_HOUR:
                        bad_grid += 1
                    ts = time.gmtime(ms / 1000.0)
                    w.writerow(["%04d-%02d-%02d %02d:%02d:%02d" % (
                        ts.tm_year, ts.tm_mon, ts.tm_mday, ts.tm_hour, ts.tm_min, ts.tm_sec),
                        r["open"], r["high"], r["low"], r["close"], r["volume"]])
                    rows += 1
                    if first_ms is None:
                        first_ms = ms
                    last_ms = ms
    return {"rows": rows, "first_open_time_ms": first_ms, "last_open_time_ms": last_ms,
            "csv": out_path, "csv_bytes": os.path.getsize(out_path),
            "non_hourly_steps": bad_grid}


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
            run_log("csv %s rows=%d bytes=%d non_hourly_steps=%d"
                    % (key, per_dataset[key]["rows"], per_dataset[key]["csv_bytes"],
                       per_dataset[key]["non_hourly_steps"]))

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
                "raw_rows": ds["rows"], "non_hourly_steps": ds["non_hourly_steps"]}
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

    Carries the registered causal feature matrix (`build_feature_matrix`), the per-model
    walk-forward forecast cache and the per (cohort, case) signal-layer cache.
    """

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
        # the registered causal feature matrix (prefix invariance is probed by
        # `causality_probe` and reported with the run)
        self.features = build_feature_matrix(self)
        self.feature_report = {}
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
    resting invalidation, margin backstop, the record's profitability-gated opposite-signal
    exit) and finally the slice end.  Long episodes walk the bar LOW first then the HIGH; short
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
            counter(kind, "entry_before_a_defined_forecast")
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
    """Human-readable label of one registered case, e.g. 'thr1.5__L2__p0.6'."""
    return case_name(case)


def record(cohort, p, dca, kind, m):
    case = case_tuple(p)
    row = {"symbol": cohort.symbol, "timeframe": cohort.timeframe, "window_kind": kind,
           "window_case": case_label(case), "case_name": case_name(case),
           "dir_long_only": p["dir_long_only"], "dir_long_short": p["dir_long_short"],
           "model_lightgbm": p["model_lightgbm"], "model_sklearn": p["model_sklearn"],
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
           "annualized_return": m["annualized_return"]}
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
# one item of the record's own falsification battery (or one of its limitations).  A reader
# never culls a cohort by itself: a hit can only move a PASS to DEFERRED, and the evidence
# decides the final verdict (contract 6.4 / 7.3).

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


def _trailing_year_window(cohort, end_date):
    """The record's "12 consecutive months" forward slice ending at the data end (inclusive)."""
    y, m, d = int(end_date[:4]), int(end_date[5:7]), int(end_date[8:10])
    start = "%04d-%02d-%02d" % (y - 1, m, d)
    return cohort.slice(start, end_date), start


def forward_extension_sharpe_check(spec, cohort_results, diag_inputs):
    """Reader 1 - record falsification item 1: "Out-of-Sample Walk-Forward Extension (2026+):
    Apply the trained XGBoost model and cost-aware filter to live/forward 2026 data.  The
    strategy is falsified if net-of-cost Sharpe ratio drops below 0.0 over 12 consecutive
    months."

    Definition: the elected winner's cell is re-simulated over the registered TRAILING
    12-MONTH slice ending at the data end (the locally available forward extension), canonical
    cost track.  TRIGGERED if that trailing-12-month net-of-cost Sharpe < 0.0.  The registered
    OOS slice is reported alongside as the cohort gate's own window.
    """
    out = {"registered_item": "forward-extension-12m-sharpe", "evaluated": False, "hit": False,
           "landing": "family-level: a hit must NEVER be recorded as PASS",
           "threshold_sharpe": 0.0, "cohorts": {}}
    for c in cohort_results:
        if c["winner"] is None:
            continue
        winner_case, dca = _winner_case(c, spec)
        cohort, series = diag_inputs[c["cohort"]]
        slip = spec["costs"]["baseline_slippage_ticks"]
        (lo, hi), start_date = _trailing_year_window(cohort, spec["data"]["end"])
        m = simulate(cohort, params_of(c["winner"]), rail_for(dca), (lo, hi), {}, slip,
                     "fwd12_diag", series, count_layers=False)
        hit = bool(m["sharpe"] < 0.0)
        out["evaluated"] = True
        out["hit"] = out["hit"] or hit
        out["cohorts"][c["cohort"]] = {
            "slice": [start_date, spec["data"]["end"]], "bars": hi - lo,
            "sharpe": round(float(m["sharpe"]), 6), "net_pnl": round(float(m["net_pnl"]), 6),
            "threshold_sharpe": 0.0, "hit": hit,
            "oos_slice_sharpe": (c.get("metrics") or {}).get("oos", {}).get("sharpe"),
            "case_name": c.get("winner_case_label")}
    return out


def cost_sensitivity_check(spec, cohort_results, diag_inputs, rows_by_cohort):
    """Reader 2 - record falsification item 2: "Transaction Cost Sensitivity Threshold:
    Increment cost parameter c from 10 bps to 15, 20, and 25 bps.  If the strategy's Sharpe
    ratio turns negative at c <= 15 bps, the edge is too fragile for live execution."

    Definition: the elected winner's cell is re-simulated over the FULL window at fee_mult
    in {2,3,4,5} x the registered 5 bps taker fee, i.e. c = 10/15/20/25 bps per fill (the
    record's c = 10 bps all-in assumption is the fee_mult 2.0 track, which is also the
    registered `fee_2x` grid; the reader re-runs it and cross-checks the two readings).
    TRIGGERED if the Sharpe at c = 10 bps or at c = 15 bps is < 0.0.
    """
    out = {"registered_item": "cost-sensitivity-c-15-25bps", "evaluated": False, "hit": False,
           "landing": "family-level: a hit must NEVER be recorded as PASS",
           "taker_fee_bps": None, "c_bps_tracks": {}, "threshold_c_bps": 15.0,
           "threshold_sharpe": 0.0, "cohorts": {}}
    for c in cohort_results:
        if c["winner"] is None:
            continue
        winner_case, dca = _winner_case(c, spec)
        cohort, series = diag_inputs[c["cohort"]]
        slip = spec["costs"]["baseline_slippage_ticks"]
        full = cohort.slice(spec["data"]["start"], spec["data"]["end"])
        taf_bps = round(cohort.taker_fee * 10000.0, 4)
        out["taker_fee_bps"] = taf_bps
        tracks = {}
        for mult in COST_SENSITIVITY_MULTS:
            m = simulate(cohort, params_of(c["winner"]), rail_for(dca), full,
                         {"fee_mult": mult}, slip, "cost_sens_diag", series, count_layers=False)
            c_bps = round(taf_bps * mult, 4)
            tracks["c_%g_bps" % c_bps] = {
                "fee_mult": mult, "c_bps": c_bps,
                "sharpe": round(float(m["sharpe"]), 6),
                "net_pnl": round(float(m["net_pnl"]), 6),
                "fees": round(float(m["fees"]), 6), "sharpe_negative": bool(m["sharpe"] < 0.0)}
        grid_row = _cell(rows_by_cohort, c["cohort"], "fee_2x", winner_case, dca)
        tracks["cross_check_vs_fee_2x_grid"] = {
            "net_pnl_grid": None if grid_row is None else grid_row["net_pnl"],
            "net_pnl_reader": tracks.get("c_10_bps", {}).get("net_pnl"),
            "matches": bool(grid_row is not None
                            and abs(float(grid_row["net_pnl"])
                                    - float(tracks["c_10_bps"]["net_pnl"])) < 1e-6)}
        relevant = [v for k, v in tracks.items() if k.startswith("c_") and v["c_bps"] <= 15.0]
        hit = any(v["sharpe_negative"] for v in relevant)
        out["evaluated"] = True
        out["hit"] = out["hit"] or hit
        out["c_bps_tracks"] = tracks
        out["cohorts"][c["cohort"]] = {
            "tracks": tracks, "hit": hit, "case_name": c.get("winner_case_label"),
            "fragile_below_or_at_c_bps": [v["c_bps"] for v in relevant if v["sharpe_negative"]]}
    return out


def cross_asset_replication_check(spec, cohort_results, diag_inputs, xasset_inputs):
    """Reader 3 - record falsification item 3: "Cross-Asset Replication: Test identical
    walk-forward pipeline on ETH/USDT and SOL/USDT.  If the cost-aware execution filter fails to
    produce positive net returns on other major crypto pairs, the Bitcoin result is an artifact
    of asset-specific trending behavior."

    Definition: the elected winner's (strategy case, DCA config) is run through the IDENTICAL
    registered walk-forward pipeline (same folds, same feature/selection/EGARCH/model constants,
    same filter, same rail) on ETHUSDT/1h and SOLUSDT/1h over the FULL window - both assets are
    present in the canonical raw store.  TRIGGERED if any replicated asset's FULL-window
    net_pnl <= 0.  These are falsification MEASUREMENTS, not cohorts: they never enter the
    coverage product, the selector or the cohort disposition.
    """
    out = {"registered_item": "cross-asset-replication", "evaluated": False, "hit": False,
           "landing": "family-level: a hit must NEVER be recorded as PASS",
           "assets": {}, "non_cohort_note": "falsification measurements only - never cohorts"}
    for c in cohort_results:
        if c["winner"] is None:
            continue
        winner_case, dca = _winner_case(c, spec)
        slip = spec["costs"]["baseline_slippage_ticks"]
        for asset, (aco, aseries) in sorted(xasset_inputs.items()):
            full = aco.slice(spec["data"]["start"], spec["data"]["end"])
            m = simulate(aco, params_of(c["winner"]), rail_for(dca), full, {}, slip,
                         "xasset_diag", aseries, count_layers=False)
            hit = bool(m["net_pnl"] <= 0.0)
            out["evaluated"] = True
            out["hit"] = out["hit"] or hit
            out["assets"][asset] = {
                "case_name": c.get("winner_case_label"), "bars": full[1] - full[0],
                "net_pnl": round(float(m["net_pnl"]), 6),
                "sharpe": round(float(m["sharpe"]), 6),
                "episodes": int(m["episodes"]), "fees": round(float(m["fees"]), 6),
                "funding": round(float(m["funding"]), 6),
                "positive_net_return": bool(m["net_pnl"] > 0.0), "hit": hit}
    return out

def summarize(spec, grid_rows, layers, diag_inputs, slice_days, xasset_inputs):
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
    # (no singleton-leg diagnostics here: every registered case already carries exactly one
    # (threshold, sequence length, min probability) cell, so the case axis is its own
    # decomposition)

    for r in full:
        strategy_cells.add(case_tuple(r))
        dca_cells.add(tuple(r[a] for a in DCA_AXES))
    # (no per-leg presence check: the case axis is fully registered, see CASE_ORDER)

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
    rng = random.Random(20260915)
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
            # the diagnostic re-run carries its own counter kind, so the grid counters of
            # `full` stay exactly the grid's own (no double counting anywhere)
            before = counters_snapshot()
            m_win = simulate(cohort, p_win, rail_for(dca), full_slice, {}, slip0,
                             "winner_diag", series, diag=True, count_layers=False)
            win_delta = counters_delta(before)
            row = record(cohort, p_win, dca, "full", m_win)
            # the cell lookup must stay inside THIS cohort's rows: grid_rows is the global
            # accumulation over all four cohorts, so the same cell appears once per symbol
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
    forward_extension = (forward_extension_sharpe_check(spec, cohort_results, diag_inputs)
                         if coverage_complete
                         else dict(empty_reader, registered_item="forward-extension-12m-sharpe"))
    cost_sensitivity = (cost_sensitivity_check(spec, cohort_results, diag_inputs, rows_by_cohort)
                        if coverage_complete
                        else dict(empty_reader, registered_item="cost-sensitivity-c-15-25bps"))
    cross_asset = (cross_asset_replication_check(spec, cohort_results, diag_inputs, xasset_inputs)
                   if coverage_complete
                   else dict(empty_reader, registered_item="cross-asset-replication"))
    flags = {"forward_extension_12m_sharpe": bool(forward_extension.get("hit")),
             "cost_sensitivity_c_15_25bps": bool(cost_sensitivity.get("hit")),
             "cross_asset_replication": bool(cross_asset.get("hit"))}

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
    state_defs = [diag_inputs[label][0].feature_report for label in cohort_labels] \
        if coverage_complete else []
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
        # Strategy I registered guards (names frozen in the round-spec)
        "entry_bar_is_the_next_bar_after_the_signal":
            counters_total("entry_bar_not_the_next_bar_after_the_signal") == 0,
        "no_entry_without_a_defined_forecast":
            counters_total("entry_before_a_defined_forecast") == 0,
        "forecast_covers_every_test_bar":
            counters_total("prediction_unavailable_on_test_bar") == 0,
        "features_finite_on_every_test_bar":
            counters_total("feature_not_finite_on_test_bar") == 0,
        "egarch_fit_converged_on_every_fold": counters_total("egarch_fit_failed") == 0,
        "technical_selection_had_enough_rows":
            counters_total("ta_selection_insufficient_candidates") == 0,
        "feature_layer_is_prefix_invariant": counters_total("causality_probe_mismatch") == 0,
        "no_fold_was_truncated": counters_total("fold_test_window_empty") == 0,
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
                         "direction_modes": list(DIRECTION_MODES),
                         "model_implementations": list(MODEL_IMPLS),
                         "forecast_constants": {"lambda_cost": LAMBDA_COST, "c": COST_C,
                                                "ta_windows": list(TA_WINDOWS),
                                                "ta_families": list(TA_FAMILIES),
                                                "selected_ta_count": SELECTED_TA_COUNT,
                                                "egarch_pq": [EGARCH_P, EGARCH_Q],
                                                "volume_sma_period": VOLUME_SMA_PERIOD,
                                                "fold_train_months": FOLD_TRAIN_MONTHS,
                                                "fold_val_months": FOLD_VAL_MONTHS,
                                                "fold_test_months": FOLD_TEST_MONTHS,
                                                "fold_step_months": FOLD_STEP_MONTHS,
                                                "max_folds": MAX_FOLDS, "seed": SEED}},
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
        "forward_extension_12m_sharpe": forward_extension,
        "cost_sensitivity_c_15_25bps": cost_sensitivity,
        "cross_asset_replication": cross_asset,
        "feature_layer": ({label: diag_inputs[label][0].feature_report
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
        sys.stderr.write("usage: 90_strategy_i_run.py <run-spec.json>\n")
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
                raise SystemExit("run-spec is not a Strategy I spec: missing %r (contract 7.2/7.3)"
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
                probe = causality_probe(cohort, cohort.features)
                cohort.feature_report = {
                    "bars": cohort.n, "candidate_columns": len(cohort.features["names"]),
                    "technical_candidates": len(ta_column_names()),
                    "warmup_floors": cohort.features["floors"],
                    "causality_probe": probe}
                run_log("cohort %s bars=%d features=%d causality_probe=%d probes/%d mismatch "
                        "funding_obs=%d modeled=%d official=%d"
                        % (label, cohort.n, len(cohort.features["names"]),
                           probe["bars_probed"], probe["mismatches"], len(ft),
                           counts["modeled_funding"], counts["official"]))
                for k, v in run_cohort(spec, cohort, run_log, series).items():
                    grid_rows.setdefault(k, []).extend(v)
                done += 1
                atomic_write_json(os.path.join(attempt_dir, "artifacts", "progress.json"),
                                  {"cohorts_done": done, "cohorts_total": total,
                                   "updated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                                   time.gmtime())})
        atomic_write_json(os.path.join(attempt_dir, "artifacts", "funding_series.json"),
                          funding_report)

        # ---- registered falsification MEASUREMENT cohorts (ETHUSDT / SOLUSDT, never cohorts) ---
        xasset_inputs = {}
        for symbol in (spec.get("falsification") or {}).get("cross_asset_symbols", []):
            tf = spec["data"]["timeframes"][0]
            aco = Cohort(symbol, tf, spec["data"]["start"], spec["data"]["end"], spec)
            meta = instruments[symbol]
            aco.price_increment = meta["price_increment"]
            aco.taker_fee = meta["taker_fee"]
            aco.leverage = 1.0 / meta["margin_init"]
            aco.margin_maint = meta["margin_maint"]
            aft, afr, acounts = load_funding_series(symbol, spec["data"]["start"],
                                                    spec["data"]["end"])
            aseries = build_funding_index(aco, aft, afr, acounts, "full")
            xasset_inputs[symbol] = (aco, aseries)
            aprobe = causality_probe(aco, aco.features)
            aco.feature_report = {"bars": aco.n, "causality_probe": aprobe,
                                  "role": "cross-asset falsification measurement (not a cohort)"}
            run_log("falsification measurement asset %s bars=%d probe=%d/%d mismatch"
                    % (symbol, aco.n, aprobe["bars_probed"], aprobe["mismatches"]))

        for kind, rows in grid_rows.items():
            path = os.path.join(attempt_dir, "artifacts", "grid_%s.csv" % kind)
            with open(path, "w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=list(ROW_FIELDS))
                w.writeheader()
                w.writerows(rows)
            run_log("wrote %s (%d rows)" % (os.path.basename(path), len(rows)))

        layers = LAYER_TOTALS.setdefault("full", [0] * 12)
        summary = summarize(spec, grid_rows, layers, diag_inputs, slice_days, xasset_inputs)
        summary["runtime_seconds"] = int(time.time() - started)
        summary["bins_build"] = {k: bins[k] for k in
                                 ("build_wall_seconds", "qlib_dir_bytes", "qlib_version")}
        summary["instrument_metadata"] = {s: instruments[s] for s in spec["data"]["symbols"]}
        summary["data_readback"] = bins["readback"]
        summary["funding_series"] = funding_report
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
                          {"forward_extension_12m_sharpe": summary["forward_extension_12m_sharpe"],
                           "cost_sensitivity_c_15_25bps": summary["cost_sensitivity_c_15_25bps"],
                           "cross_asset_replication": summary["cross_asset_replication"],
                           "flags": summary["registered_family_level_falsification_flags"]})
        atomic_write_json(os.path.join(attempt_dir, "artifacts", "forecast_layer.json"),
                          summary["feature_layer"])
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
