#!/usr/bin/env python3
"""Strategy H — Markov-chain VOLUME-PRICE STATE on the 1h base grid, with a CAUSAL 4h state
mapping (4 USD-M perpetual cohorts; adapted portability), on Qlib.

Entry point for ONE pre-registered attempt.  Reads the immutable run-spec from /results (the
only source of parameters), builds the Qlib .bin store from the READ-ONLY canonical raw store
into /qlib/work, then runs the pre-registered full-backtest contract
(QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md sections 7.2 / 7.3):

    4 cohorts (BNBUSDT / BTCUSDT / ETHUSDT / SOLUSDT x the registered 1h base grid)
    x STRATEGY domain (the 27 registered (price_state_threshold, sequence_length,
      min_signal_probability) cells)
    x DCA domain (4 axes = 48 configs)
    x 10 registered phase grids (historical / oos / full / fee_2x / funding_2x /
      entry_delay_1_bar / slippage_2ticks / no_funding / no_funding_full /
      cost_attrition_40bps)
    = 4 x 27 x 48 x 10 = 51,840 expected case evaluations.

SOURCE CLAIM UNDER TEST (the record's own): 12 states per candle = price_state(0..3) x 3 +
volume_state(0..2); price state from diff_close/atr, volume state from volume against a
rolling 24-period volume SMA with high/low multipliers; a transition matrix built empirically
from state sequences of length `sequence_length` to the subsequent state; LONG when the
bullish transition probability exceeds `min_signal_probability`, SHORT when a bearish
transition is expected; entry on the signal; exit on an opposite signal ONLY IF profitable, or
via ATR-anchored stop / take profit.

THE CORRECTED AVAILABILITY CONTRACT (the record's material source-code caveat): the record's
4h resample is left-edge / backward-as-of merged, so lower-timeframe rows inside a 4h bin can
see that bin's eventual close/high/low/volume.  This engine implements the frozen
`MARKOV_CONTRACT` of `runtime/strategy_h_v1_counts.py` instead:

  * bins      : 4 h bins aligned to 00:00 UTC, resampled from the cohort's OWN 1h bars
                (O = first open, H = max high, L = min low, C = last close, V = sum volume);
                every bin must hold exactly 4 base bars.
  * availability: a bin's state is visible only at/after that bin's close, so a base bar NEVER
                carries its own bin's state — it carries the most recent COMPLETED bin state.
                `bin_available_bar[b] == bin_last_bar[b] + 1` is an executable guard and the
                look-ahead control (`lookahead_control`) is reported per cohort.
  * atr       : Wilder over 14 completed bins (atr_14 = mean of the first 14 true ranges,
                atr_i = (13*atr_{i-1} + TR_i)/14); a bin without a positive, complete ATR and a
                complete 24-bin volume window carries NO state (-1).
  * price_state: 0 if r >= thr, 1 if 0 <= r < thr, 2 if -thr < r < 0, 3 if r <= -thr, with
                r = (bin close - previous bin close) / atr.  The threshold is a SEARCHED axis,
                so the state sequence is built once per registered threshold.
  * volume_state: 0 if V < 0.5 * vsma, 2 if V > 1.5 * vsma, else 1; vsma = mean of the last 24
                completed bins including the current one (equality is normal).
  * decision  : the decision sequence at base bar t = the last `sequence_length` COMPLETED bin
                states; the bin containing t is excluded until it completes.
  * matrix    : expanding-window point-in-time counts of (sequence -> next state); a transition
                with outcome bin i enters the row only once bin i has completed, so nothing
                from the future of the decision bar can enter its row.  A row with fewer than
                5 observations yields NO signal (counted as untrained).
  * signal    : p_bull = share of the row landing in states 0..5, p_bear = share landing in
                6..11; LONG if p_bull > min_signal_probability AND p_bull > p_bear; SHORT if
                p_bear > min_signal_probability AND p_bear > p_bull; an exact tie or an
                untrained row is NONE.
  * entry     : a FRESH signal (differs from the previous base bar's signal) is the entry
                event; entry at the OPEN of the next base bar after the event.  A persistent
                signal never re-enters after a flatten.
  * exits     : (1) rail TP at running_average_cost x (1 +/- breakeven_tp_pct); (2) resting
                invalidation at running_average_cost x (1 -/+ invalidation_pct); (3) the
                record's own signal exit — an opposite signal while the position is profitable
                against its running average cost -> reduce-only flatten of every layer at the
                NEXT base bar open (an opposite signal while unprofitable is held, and that
                asymmetry is measured as `signal_exit_blocked_unprofitable`); (4) slice end ->
                reduce-only flatten of every layer at the last bar close.

EXECUTION (inherited DCA rail semantics): tranche #1 opens at the entry bar's open, adverse
price scale-ins follow the registered ladder (level_k price = initial_entry_price x
(1 -/+ spacing_pct x k), k = 1..10, i.e. at most 11 routine active levels of the 12-tranche
rail), the take profit is reduce-only at running_average_cost x (1 +/- breakeven_tp_pct), the
invalidation is a RESTING stop at running_average_cost x (1 -/+ invalidation_pct), and the
slice end ALWAYS reduce-only flattens every layer.  No new episode is opened once the realised
equity is gone.  Superseded-with-disclosure: the source's ATR-anchored sl_multiplier /
tp_multiplier and its equal-division sizing are replaced by the card-mandated DCA execution
layer (the 4-axis DCA domain fixes the TP and invalidation parameterisation and the tranche
rail fixes sizing).

DCA is executed as real order/fill accounting (an episode state machine over the bars);
nothing is estimated after the fact.  Every legal (strategy params x DCA config) cell is
evaluated on every registered phase grid, and the family gate is the **cohort-level survivor**
rule of contract section 7.3: one deterministic historical-only winner per cohort, then
OOS / full / robustness / parameter-neighbourhood evidence for that same winner.  This family
has 4 cohorts, so the cohort survivor count is 0..4.  The four registered family-level readers
(transition-matrix stability, exit asymmetry, cost boundary, cross-asset dispersion) never cull
a cohort and can only move a PASS to DEFERRED, never to PASS.

COUNTERS.  The twelve registered simulate-level counters are `entry_after_slice_end`,
`slice_end_flatten`, `signal_exit_blocked_unprofitable`, `signal_exit_taken`,
`signal_exit_deferred_to_slice_end`, `untrained_row_no_signal`, `same_sign_signal_ignored`,
`state_undefined_bar`, `attached_state_before_availability`, `entry_refused_exhausted`,
`ladder_cap_reached` and `stop_at_ladder_boundary`.  Alongside them the engine carries the
structural guard counters that make the registered assertions of the round-spec executable
(never inventing a new number anywhere): `entry_bar_not_the_next_bar_after_the_signal`,
`signal_exit_not_profitable`, `reentry_skipped_in_position`, `matrix_row_contained_future_outcome`,
`signal_from_untrained_row`, `bin_grid_not_aligned`, `bin_availability_mismatch`,
`state_index_out_of_range`, plus the inherited `bar_grid_not_contiguous`, `case_not_registered`
and `funding_bar_out_of_hold`.  A guard counter that is non-zero is a contract violation.

Writes only:
  * /qlib/work/**      (rebuildable derived/cache area, INV-5)
  * <attempt_dir>/**   (result.json, artifacts/, logs/, state.json)
Never writes /data/raw (read-only mount).

usage: 80_strategy_h_run.py <run-spec.json>
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
WORK_ROOT = "/qlib/work/strategyD-v1"
CSV_ROOT = os.path.join(WORK_ROOT, "csv")
QLIB_DIR = os.path.join(WORK_ROOT, "qlib-data")
MS_PER_DAY = 86400000
MS_PER_HOUR = 3600000
FIELDS = ["open", "high", "low", "close", "volume"]
START_EQUITY = 30000.0
MAX_ADD_LEVELS = 10
# ------------------------------------- registered markov contract (RESEARCH_DEFINED, frozen)
# The record fixes the MECHANISM (12 states on price/volume, a transition matrix over state
# sequences, a probability threshold for the signal, a profitability-gated opposite-signal exit
# plus ATR-anchored stops) but none of its timing/alignment detail.  This block mirrors
# `runtime/strategy_h_v1_counts.py` (MARKOV_CONTRACT / MARKOV_CONSTANTS) and the pre-registered
# search axes; every value is fixed BEFORE the first run.
PRICE_STATE_THRESHOLDS = (1.0, 1.5, 2.0)   # searched axis: |r| threshold of the price state
SEQUENCE_LENGTHS = (1, 2, 3)               # searched axis: length of the decision sequence
MIN_SIGNAL_PROBABILITIES = (0.5, 0.6, 0.7)  # searched axis: signal probability floor
CASE_FIELDS = ("thr_1_0", "thr_1_5", "thr_2_0",
               "seq_len_1", "seq_len_2", "seq_len_3",
               "minp_0_5", "minp_0_6", "minp_0_7")
CASE_ORDER = tuple(
    tuple([1 if t == i else 0 for t in range(3)] + [1 if l == j else 0 for l in range(3)]
          + [1 if m == k else 0 for m in range(3)])
    for i in range(3) for j in range(3) for k in range(3))
CASE_NAMES = tuple(
    "thr%g__L%d__p%g" % (PRICE_STATE_THRESHOLDS[i], SEQUENCE_LENGTHS[j],
                         MIN_SIGNAL_PROBABILITIES[k])
    for i in range(3) for j in range(3) for k in range(3))
BIN_HOURS = 4
MS_PER_BIN = BIN_HOURS * MS_PER_HOUR
BASE_BARS_PER_BIN = 4
STATE_COUNT = 12
BULL_STATES = tuple(range(6))
BEAR_STATES = tuple(range(6, 12))
ATR_PERIOD = 14
VOLUME_SMA_PERIOD = 24
VOLUME_HIGH_MULTIPLIER = 1.5
VOLUME_LOW_MULTIPLIER = 0.5
MIN_ROW_OBSERVATIONS = 5
# Registered stability-reader evidence floor: the split-half p_bull deviation is only read off
# sequences with at least this many observed transitions in EACH half, and at least this many
# such sequences must exist, otherwise the deviation part reports insufficient_evidence and does
# not trigger (a deviation read off a nearly empty row measures sparsity, not instability).
MIN_STABILITY_OBSERVATIONS_PER_HALF = 10
MIN_STABILITY_ROWS = 3
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
# grids whose schedule mapping is never shifted: only these can assert that no episode is
# clipped at the slice edge beyond the registered boundary effect (the entry-delay stress
# moves the entry bar by one, never the exit)
UNSHIFTED_GRID_KINDS = ("historical", "oos", "full", "fee_2x", "funding_2x",
                        "slippage_2ticks", "no_funding", "no_funding_full",
                        "cost_attrition_40bps")
# Joint parameter space axes, in the one registered order used for the deterministic
# lexical tie-break (contract 7.3).  Order is part of the gate.  `window_case` is the
# composite (price_state_threshold, sequence_length, min_signal_probability) axis; its
# registered order is CASE_ORDER.
AXES = ("window_case", "spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct")
DCA_AXES = ("spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct")
SELECTOR_VERSION = "cohort-selector-v1"
DISPOSITION_VERSION = "cohort-disposition-v1"
CONTRACT_SEMANTICS_VERSION = "v1.4.0"
ENGINE_VERSION = "h-v1-engine-1.0.0"
ENGINE_SEMANTICS = ("causal 4h volume-price state layer (12 states from price_state x 3 + "
                    "volume_state, built from the cohort's own 1h bars, a bin's state visible "
                    "only at/after its close), expanding-window point-in-time transition "
                    "counts over the last 1..3 completed bin states, fresh-signal entry at the "
                    "next bar's open, profitability-gated opposite-signal exit plus the "
                    "registered DCA rail (see module docstring)")
WINNER_METRIC_KEYS = ("net_pnl", "sharpe", "episodes", "ending_equity", "fees", "funding",
                      "max_dd_usdt", "max_dd_pct", "max_effective_leverage",
                      "capital_utilization", "annualized_return", "cagr")
# csv row field order (kept identical for every phase grid)
ROW_FIELDS = ("symbol", "timeframe", "window_kind", "window_case", "case_name",
              "thr_1_0", "thr_1_5", "thr_2_0",
              "seq_len_1", "seq_len_2", "seq_len_3",
              "minp_0_5", "minp_0_6", "minp_0_7",
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
COUNTER_NAMES = ("entry_after_slice_end", "slice_end_flatten", "signal_exit_blocked_unprofitable",
                 "signal_exit_taken", "signal_exit_deferred_to_slice_end",
                 "untrained_row_no_signal", "same_sign_signal_ignored", "state_undefined_bar",
                 "attached_state_before_availability", "entry_refused_exhausted",
                 "ladder_cap_reached", "stop_at_ladder_boundary",
                 "entry_bar_not_the_next_bar_after_the_signal", "signal_exit_not_profitable",
                 "reentry_skipped_in_position", "matrix_row_contained_future_outcome",
                 "signal_from_untrained_row", "bin_grid_not_aligned", "bin_availability_mismatch",
                 "state_index_out_of_range", "case_not_registered", "bar_grid_not_contiguous",
                 "funding_bar_out_of_hold")
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


def case_thr(case):
    """Registered price-state-threshold index of one case: 0 = 1.0 / 1 = 1.5 / 2 = 2.0."""
    return list(case).index(1, 0, 3)


def case_seq(case):
    """Registered sequence-length index of one case: 0 = 1 / 1 = 2 / 2 = 3 bins."""
    return list(case).index(1, 3, 6) - 3


def case_minp(case):
    """Registered min-signal-probability index of one case: 0 = 0.5 / 1 = 0.6 / 2 = 0.7."""
    return list(case).index(1, 6, 9) - 6


def case_fields(thr_i, seq_i, minp_i):
    """The registered one-hot case tuple of (threshold index, sequence index, minp index)."""
    return tuple([1 if k == thr_i else 0 for k in range(3)]
                 + [1 if k == seq_i else 0 for k in range(3)]
                 + [1 if k == minp_i else 0 for k in range(3)])


# --------------------------------------------------------------------------- state science

def price_state_of(r, thr):
    """Registered price partition of one completed bin (MARKOV_CONTRACT).

    r >= thr -> 0 (strong up); 0 <= r < thr -> 1 (mild up); -thr < r < 0 -> 2 (mild down);
    r <= -thr -> 3 (strong down).  A non-finite r carries NO state (-1).
    """
    if r != r or thr != thr:                       # NaN never compares equal to itself
        return -1
    if r >= thr:
        return 0
    if r >= 0.0:
        return 1
    if r > -thr:
        return 2
    return 3


def volume_state_of(volume, vsma):
    """Registered volume partition: < 0.5 * vsma -> 0; > 1.5 * vsma -> 2; else 1 (equality is
    normal).  A bin without a complete 24-bin volume window carries NO state (-1)."""
    if volume != volume or vsma != vsma or not (vsma > 0.0):
        return -1
    if volume < VOLUME_LOW_MULTIPLIER * vsma:
        return 0
    if volume > VOLUME_HIGH_MULTIPLIER * vsma:
        return 2
    return 1


def signal_of_row(row, minp):
    """Registered signal rule over one PIT transition row.

    A row with fewer than `MIN_ROW_OBSERVATIONS` observations is untrained -> NONE; otherwise
    LONG when p_bull > minp and p_bull > p_bear, SHORT when p_bear > minp and p_bear > p_bull;
    an exact tie is NONE.
    """
    obs = int(sum(row))
    if obs < MIN_ROW_OBSERVATIONS:
        return 0
    p_bull = sum(row[:6]) / float(obs)
    p_bear = sum(row[6:]) / float(obs)
    if p_bull > minp and p_bull > p_bear:
        return 1
    if p_bear > minp and p_bear > p_bull:
        return -1
    return 0


def transition_counts(states, seq_len, lo_out, hi_out):
    """Counts of (sequence -> next state) over OUTCOME bins j in [lo_out, hi_out).

    The key of outcome bin j is the sequence of the `seq_len` bins before it; a transition is
    only counted when all `seq_len` + 1 bins carry a defined state.  This is the raw counting
    primitive behind both the point-in-time matrix of the signal layer and the split-half
    stability reader.
    """
    n = int(len(states))
    counts = {}
    for j in range(max(int(lo_out), int(seq_len)), min(int(hi_out), n)):
        key = tuple(int(s) for s in states[j - seq_len:j])
        if -1 in key or int(states[j]) < 0:
            continue
        slot = counts.get(key)
        if slot is None:
            slot = counts[key] = [0] * STATE_COUNT
        slot[int(states[j])] += 1
    return counts


def build_state_layer(cohort):
    """The registered 4h volume-price state layer of one cohort (MARKOV_CONTRACT).

    Sets the layer attributes on the cohort (names are part of the round-spec) and returns the
    measured build report.  `bin_state` is the canonical attribute under the FIRST registered
    price-state threshold; because that threshold is a SEARCHED axis, the per-threshold state
    sequences are carried in `bin_state_by_threshold` (and `state_of_bar_by_threshold`) and the
    signal layer consumes the sequence of its own case.

    Guards (a violation raises and trips a counter):
      * the base grid divides the 4 h bin and the first bar opens on a 00:00 UTC boundary;
      * every bin holds exactly `BASE_BARS_PER_BIN` contiguous base bars;
      * `bin_available_bar[b] == bin_last_bar[b] + 1`;
      * no base bar ever carries a state whose bin closes after that bar (the look-ahead
        correction); the naive left-edge/own-bin mapping is measured as the reported control.
    """
    times = cohort.open_time_ms
    n = cohort.n
    bar_ms = cohort.bar_ms
    if bar_ms <= 0 or MS_PER_BIN % bar_ms:
        counter("state", "bin_grid_not_aligned")
        raise SystemExit("cohort %s/%s: %d ms bars do not divide the registered 4 h bin grid"
                         % (cohort.symbol, cohort.timeframe, bar_ms))
    if int(times[0]) % MS_PER_BIN:
        counter("state", "bin_grid_not_aligned")
        raise SystemExit("cohort %s/%s: the first bar does not open on a 00:00 UTC 4 h boundary"
                         % (cohort.symbol, cohort.timeframe))
    n_bins = n // BASE_BARS_PER_BIN
    tail_bars = n - n_bins * BASE_BARS_PER_BIN
    bin_index_of_bar = np.full(n, -1, dtype=np.int64)
    if n_bins:
        bin_index_of_bar[:n_bins * BASE_BARS_PER_BIN] = np.repeat(
            np.arange(n_bins, dtype=np.int64), BASE_BARS_PER_BIN)
    for b in range(n_bins):
        lo = b * BASE_BARS_PER_BIN
        if (int(times[lo]) != int(times[0]) + lo * bar_ms
                or int(times[lo + BASE_BARS_PER_BIN - 1])
                != int(times[lo]) + (BASE_BARS_PER_BIN - 1) * bar_ms):
            counter("state", "bin_grid_not_aligned")
            raise SystemExit("cohort %s/%s: bin %d does not hold exactly %d contiguous base bars"
                             % (cohort.symbol, cohort.timeframe, b, BASE_BARS_PER_BIN))
    core = n_bins * BASE_BARS_PER_BIN
    bin_first_bar = np.arange(n_bins, dtype=np.int64) * BASE_BARS_PER_BIN
    bin_last_bar = bin_first_bar + (BASE_BARS_PER_BIN - 1)
    if n_bins:
        g_o = cohort.open[:core].reshape(n_bins, BASE_BARS_PER_BIN)
        g_h = cohort.high[:core].reshape(n_bins, BASE_BARS_PER_BIN)
        g_l = cohort.low[:core].reshape(n_bins, BASE_BARS_PER_BIN)
        g_c = cohort.close[:core].reshape(n_bins, BASE_BARS_PER_BIN)
        g_v = cohort.volume[:core].reshape(n_bins, BASE_BARS_PER_BIN)
        bin_open = g_o[:, 0].astype(np.float64).copy()
        bin_high = g_h.max(axis=1).astype(np.float64)
        bin_low = g_l.min(axis=1).astype(np.float64)
        bin_close = g_c[:, -1].astype(np.float64).copy()
        bin_volume = g_v.sum(axis=1).astype(np.float64)
        bin_open_ms = times[bin_first_bar].astype(np.int64).copy()
    else:
        bin_open = bin_high = bin_low = bin_close = bin_volume = np.zeros(0, dtype=np.float64)
        bin_open_ms = np.zeros(0, dtype=np.int64)
    bin_close_ms = bin_open_ms + MS_PER_BIN
    bin_available_bar = np.searchsorted(times, bin_close_ms, side="left").astype(np.int64)
    if n_bins:
        mismatch = bin_available_bar != (bin_last_bar + 1)
        if bool(mismatch.any()):
            counter("state", "bin_availability_mismatch", int(np.count_nonzero(mismatch)))
            raise SystemExit("cohort %s/%s: %d bin(s) are not available exactly one bar after "
                             "their last base bar" % (cohort.symbol, cohort.timeframe,
                                                      int(np.count_nonzero(mismatch))))

    # ---- ATR (Wilder over 14 completed bins): tr_i needs close_{i-1}, so tr_1..tr_14 are the
    # true ranges of bins 1..14 and atr_14 = mean(tr_1..tr_14) is the first defined ATR.
    bin_atr = np.full(n_bins, np.nan, dtype=np.float64)
    if n_bins > ATR_PERIOD:
        tr = np.empty(n_bins, dtype=np.float64)
        tr[0] = np.nan
        prev_close = bin_close[:-1]
        tr[1:] = np.maximum.reduce([bin_high[1:] - bin_low[1:],
                                    np.abs(bin_high[1:] - prev_close),
                                    np.abs(bin_low[1:] - prev_close)])
        bin_atr[ATR_PERIOD] = float(np.mean(tr[1:ATR_PERIOD + 1]))
        for i in range(ATR_PERIOD + 1, n_bins):
            bin_atr[i] = (13.0 * bin_atr[i - 1] + tr[i]) / 14.0
    # ---- rolling 24-bin volume SMA, the current bin included
    bin_vsma = np.full(n_bins, np.nan, dtype=np.float64)
    if n_bins >= VOLUME_SMA_PERIOD:
        csum = np.concatenate([[0.0], np.cumsum(bin_volume)])
        bin_vsma[VOLUME_SMA_PERIOD - 1:] = ((csum[VOLUME_SMA_PERIOD:] - csum[:-VOLUME_SMA_PERIOD])
                                           / float(VOLUME_SMA_PERIOD))
    # ---- per-threshold states
    bin_state_by_threshold = []
    bin_price_state_by_threshold = []
    bin_volume_state = np.full(n_bins, -1, dtype=np.int8)
    for thr in PRICE_STATE_THRESHOLDS:
        ps = np.full(n_bins, -1, dtype=np.int8)
        st = np.full(n_bins, -1, dtype=np.int8)
        for i in range(n_bins):
            a = float(bin_atr[i])
            if not np.isfinite(a) or a <= 0.0 or not np.isfinite(bin_vsma[i]):
                continue
            ps_i = price_state_of((float(bin_close[i]) - float(bin_close[i - 1])) / a, thr) \
                if i >= 1 else -1
            vs_i = volume_state_of(float(bin_volume[i]), float(bin_vsma[i]))
            if ps_i < 0 or vs_i < 0:
                continue
            ps[i] = ps_i
            bin_volume_state[i] = vs_i
            st[i] = ps_i * 3 + vs_i
        bin_state_by_threshold.append(st)
        bin_price_state_by_threshold.append(ps)
    bin_state = bin_state_by_threshold[0]
    if bool((bin_state < -1).any()) or bool((bin_state >= STATE_COUNT).any()):
        counter("state", "state_index_out_of_range")
        raise SystemExit("cohort %s/%s: a computed state lies outside 0..%d"
                         % (cohort.symbol, cohort.timeframe, STATE_COUNT - 1))

    # ---- availability: a base bar may only carry the last bin whose close is at/before it
    state_source_bin = (np.searchsorted(bin_close_ms, times, side="right") - 1).astype(np.int64)
    state_available = (state_source_bin < 0) | (bin_close_ms[np.maximum(state_source_bin, 0)]
                                                <= times)
    bad = int(np.count_nonzero(~state_available))
    if bad:
        counter("state", "attached_state_before_availability", bad)
        raise SystemExit("cohort %s/%s: %d base bar(s) would carry a state that was not "
                         "available before them (look-ahead)" % (cohort.symbol, cohort.timeframe,
                                                                 bad))
    own_bin = bin_index_of_bar
    naive_source_bin = own_bin.copy()
    naive_ctrl = {
        "bars": int(n),
        "naive_attachments_before_the_bin_closed": int(np.count_nonzero(
            (own_bin >= 0) & (bin_close_ms[np.maximum(own_bin, 0)] > times))),
        "engine_attachments_before_availability": 0,
        "engine_mapping": "the last bin whose close is at/before the bar's open",
        "naive_mapping": "the bar's OWN bin (left-edge/backward-as-of merge of the record) "
                         "would attach a state that is not yet knowable",
    }
    state_of_bar_by_threshold = []
    for st in bin_state_by_threshold:
        sb = np.full(n, -1, dtype=np.int8)
        m = state_source_bin >= 0
        sb[m] = st[state_source_bin[m]]
        state_of_bar_by_threshold.append(sb)
    naive_state_of_bar = np.full(n, -1, dtype=np.int8)
    m = own_bin >= 0
    naive_state_of_bar[m] = bin_state[own_bin[m]]
    naive_ctrl["engine_vs_naive_divergent_bars"] = int(np.count_nonzero(
        naive_state_of_bar != state_of_bar_by_threshold[0]))

    cohort.bins_per_bar = int(n_bins)
    cohort.base_bars_per_bin = BASE_BARS_PER_BIN
    cohort.state_tail_bars = int(tail_bars)
    cohort.bin_index_of_bar = bin_index_of_bar
    cohort.bin_first_bar = bin_first_bar
    cohort.bin_last_bar = bin_last_bar
    cohort.bin_open_ms = bin_open_ms
    cohort.bin_close_ms = bin_close_ms
    cohort.bin_open = bin_open
    cohort.bin_high = bin_high
    cohort.bin_low = bin_low
    cohort.bin_close = bin_close
    cohort.bin_volume = bin_volume
    cohort.bin_atr = bin_atr
    cohort.bin_vsma = bin_vsma
    cohort.bin_price_state = bin_price_state_by_threshold[0]
    cohort.bin_volume_state = bin_volume_state
    cohort.bin_state = bin_state
    cohort.bin_state_by_threshold = tuple(bin_state_by_threshold)
    cohort.bin_price_state_by_threshold = tuple(bin_price_state_by_threshold)
    cohort.bin_available_bar = bin_available_bar
    cohort.state_source_bin = state_source_bin
    cohort.state_available = state_available
    cohort.state_of_bar = state_of_bar_by_threshold[0]
    cohort.state_of_bar_by_threshold = tuple(state_of_bar_by_threshold)
    cohort.naive_state_of_bar = naive_state_of_bar
    cohort.state_lookahead_control = naive_ctrl
    cohort.state_report = {
        "bins": int(n_bins), "base_bars_per_bin": BASE_BARS_PER_BIN,
        "bars": int(n), "tail_base_bars_without_a_completed_bin": int(tail_bars),
        "defined_state_bins": int(np.count_nonzero(bin_state >= 0)),
        "state_index_domain": list(range(STATE_COUNT)),
        "bull_states": list(BULL_STATES), "bear_states": list(BEAR_STATES),
        "lookahead_control": dict(naive_ctrl),
    }
    return cohort.state_report


def raw_4h_klines(symbol):
    """The raw 4h klines of one symbol, or None when the raw store has none (resample check)."""
    d = os.path.join(RAW_ROOT, "klines", symbol, "4h")
    if not os.path.isdir(d):
        return None
    prefix = "%s-4h-" % symbol
    times, closes, volumes = [], [], []
    for name in sorted(os.listdir(d)):
        if not name.endswith(".jsonl.gz") or not name.startswith(prefix):
            continue
        with gzip.open(os.path.join(d, name), "rt") as gz:
            for line in gz:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                times.append(int(r["open_time_ms"]))
                closes.append(float(r["close"]))
                volumes.append(float(r["volume"]))
    if not times:
        return None
    return (np.array(times, dtype=np.int64), np.array(closes, dtype=np.float64),
            np.array(volumes, dtype=np.float64))


def _raw_resample_check(cohort):
    """close/volume agreement of every bin against the raw 4h klines (report only, never a gate).

    The registered state basis is "resampled from the cohort's own 1h bars"; the raw 4h klines
    are the independent reference of the same aggregation, so the deviation between the two is
    the measured evidence that the resample is the registered one.
    """
    try:
        raw = raw_4h_klines(cohort.symbol)
    except OSError as exc:  # noqa: BLE001
        return {"available": False, "reason": "raw 4h klines unreadable: %s" % exc}
    if raw is None:
        return {"available": False,
                "reason": "the canonical raw store holds no 4h klines for %s" % cohort.symbol}
    times, closes, volumes = raw
    if not cohort.bins_per_bar:
        return {"available": False, "reason": "the cohort carries no completed bin"}
    pos = np.searchsorted(times, cohort.bin_open_ms, side="left")
    pos = np.clip(pos, 0, max(len(times) - 1, 0))
    matched = (len(times) > 0) & (times[pos] == cohort.bin_open_ms)
    k = int(np.count_nonzero(matched))
    if not k:
        return {"available": True, "bins": int(cohort.bins_per_bar), "matched_bins": 0,
                "note": "no raw 4h kline shares a bin's open instant"}
    dc = np.abs(closes[pos] - cohort.bin_close)
    rel_v = np.abs(volumes[pos] - cohort.bin_volume) / np.maximum(np.abs(volumes[pos]), 1e-12)
    return {"available": True, "bins": int(cohort.bins_per_bar), "matched_bins": k,
            "match_share": k / float(cohort.bins_per_bar),
            "exact_close_bins": int(np.count_nonzero(dc < 1e-9)),
            "exact_volume_bins": int(np.count_nonzero(rel_v < 1e-9)),
            "close_agreement_share_1e_6": float(np.mean(dc < 1e-6)),
            "max_abs_close_deviation": float(np.max(dc)),
            "max_rel_volume_deviation": float(np.max(rel_v)),
            "note": "the state layer is always resampled from the cohort's own 1h bars (the "
                    "registered basis); this is the independent agreement check against the "
                    "raw 4h klines, reported as evidence and never used as a gate"}


def state_summary(cohort):
    """Measured shape of the registered 4h state layer (evidence artifact, not a gate)."""
    n_bins = int(cohort.bins_per_bar)
    states = cohort.bin_state
    hist = {str(s): int(np.count_nonzero(states == s)) for s in range(STATE_COUNT)}
    margins = [int(cohort.open_time_ms[cohort.bin_available_bar[b]]) - int(cohort.bin_close_ms[b])
               for b in range(n_bins) if int(cohort.bin_available_bar[b]) < cohort.n]
    hist_by_thr = {}
    for thr in PRICE_STATE_THRESHOLDS:
        i = PRICE_STATE_THRESHOLDS.index(thr)
        st = cohort.bin_state_by_threshold[i]
        hist_by_thr["thr_%g" % thr] = {str(s): int(np.count_nonzero(st == s))
                                       for s in range(STATE_COUNT)}
    return {
        "symbol": cohort.symbol, "timeframe": cohort.timeframe,
        "bins": n_bins, "bars": int(cohort.n),
        "base_bars_per_bin": BASE_BARS_PER_BIN,
        "tail_base_bars_without_a_completed_bin": int(cohort.state_tail_bars),
        "defined_state_bins": int(np.count_nonzero(states >= 0)),
        "defined_state_share": (int(np.count_nonzero(states >= 0)) / float(n_bins))
                               if n_bins else 0.0,
        "state_histogram_canonical_threshold": hist,
        "state_histogram_by_threshold": hist_by_thr,
        "state_index_domain": list(range(STATE_COUNT)),
        "first_bin_open_utc": iso(int(cohort.bin_open_ms[0])) if n_bins else None,
        "last_bin_close_utc": iso(int(cohort.bin_close_ms[-1])) if n_bins else None,
        "availability_margin_bars": 1 if n_bins else 0,
        "availability_margin_ms": {"min": int(min(margins)) if margins else None,
                                   "max": int(max(margins)) if margins else None,
                                   "note": "ms between a bin's close instant and the open of "
                                           "the first base bar that may carry its state; a "
                                           "negative value would be a look-ahead defect"},
        "lookahead_control": dict(cohort.state_lookahead_control),
        "raw_resample_check": _raw_resample_check(cohort),
    }


class SignalLayer:
    """The registered signal layer of one (cohort, strategy case) pair.

    Built once and cached per (cohort, case): it depends only on the 4 h state layer and the
    three searched strategy axes, never on the DCA rail or on any execution stress, so every
    DCA configuration and every phase grid reuses the same object.

    Carries the per-bar signal array (+1/-1/0), the fresh-event array and the diagnostic counts
    (untrained_rows, decision_bars, rows_with_observations, state_undefined_bars,
    attached_state_before_availability) plus the registered historical split-half diagnostics
    consumed by the transition-matrix stability reader.
    """

    def __init__(self, cohort, case):
        case = tuple(int(v) for v in case)
        if case not in CASE_ORDER:
            counter("signal", "case_not_registered")
            raise SystemExit("unregistered strategy case %r (registered: %r)"
                             % (case, CASE_ORDER))
        self.case = case
        self.case_label = case_name(case)
        self.thr = PRICE_STATE_THRESHOLDS[case_thr(case)]
        self.seq_len = SEQUENCE_LENGTHS[case_seq(case)]
        self.minp = MIN_SIGNAL_PROBABILITIES[case_minp(case)]
        self.states = cohort.bin_state_by_threshold[case_thr(case)]
        n_bins = int(len(self.states))
        self.signals = np.zeros(cohort.n, dtype=np.int8)
        self.events = np.zeros(cohort.n, dtype=np.int8)
        self.row_obs = np.zeros(n_bins, dtype=np.int64)
        self.row_has_decision = np.zeros(n_bins, dtype=bool)
        self.row_untrained = np.zeros(n_bins, dtype=bool)
        self.diag = {"decision_bars": 0, "rows_with_observations": 0, "untrained_rows": 0,
                     "untrained_share": 0.0, "state_undefined_bars": 0, "long_bars": 0,
                     "short_bars": 0, "none_bars": 0, "fresh_events": 0,
                     "same_sign_signal_ignored": 0, "matrix_future_outcome_uses": 0,
                     "attached_state_before_availability": 0, "bin_count": n_bins}
        self._build(n_bins)

    def _build(self, n_bins):
        """One forward pass over the completed bins: PIT counts first, then the bin's bars, then
        the outcome transition of this bin (which is therefore unavailable to its own bars)."""
        states = self.states
        seq_len = self.seq_len
        counts = {}
        last_outcome_bin = -1
        sig = 0
        for b in range(n_bins):
            first = b * BASE_BARS_PER_BIN
            has_decision = b >= seq_len
            key = None
            if has_decision:
                key = tuple(int(s) for s in states[b - seq_len:b])
                row = counts.get(key)
                obs = int(sum(row)) if row is not None else 0
                self.row_has_decision[b] = True
                self.row_obs[b] = obs
                self.diag["decision_bars"] += BASE_BARS_PER_BIN
                if last_outcome_bin >= b:
                    self.diag["matrix_future_outcome_uses"] += 1
                    counter("signal", "matrix_row_contained_future_outcome")
                if -1 in key:
                    self.diag["state_undefined_bars"] += BASE_BARS_PER_BIN
                    counter("signal", "state_undefined_bar", BASE_BARS_PER_BIN)
                if obs < MIN_ROW_OBSERVATIONS:
                    self.diag["untrained_rows"] += BASE_BARS_PER_BIN
                    self.row_untrained[b] = True
                    counter("signal", "untrained_row_no_signal", BASE_BARS_PER_BIN)
                    sig = 0
                else:
                    self.diag["rows_with_observations"] += BASE_BARS_PER_BIN
                    sig = signal_of_row(row, self.minp)
                    if sig != 0 and obs < MIN_ROW_OBSERVATIONS:  # never: fail-closed guard
                        counter("signal", "signal_from_untrained_row")
            else:
                sig = 0
            for k in range(BASE_BARS_PER_BIN):
                t = first + k
                prev = int(self.signals[t - 1]) if t > 0 else 0
                self.signals[t] = sig
                if sig == 0:
                    self.diag["none_bars"] += 1
                else:
                    if sig == prev:
                        self.diag["same_sign_signal_ignored"] += 1
                        counter("signal", "same_sign_signal_ignored")
                    else:
                        self.events[t] = sig
                        self.diag["fresh_events"] += 1
                    if sig > 0:
                        self.diag["long_bars"] += 1
                    else:
                        self.diag["short_bars"] += 1
            # the outcome transition of THIS bin only becomes visible from the next bin on
            if key is not None and int(states[b]) >= 0 and -1 not in key:
                slot = counts.get(key)
                if slot is None:
                    slot = counts[key] = [0] * STATE_COUNT
                slot[int(states[b])] += 1
                last_outcome_bin = b
        self.diag["untrained_share"] = (self.diag["untrained_rows"]
                                        / float(self.diag["decision_bars"])) \
            if self.diag["decision_bars"] else 0.0

    def matrix_row(self, b):
        """The PIT transition row a decision at bin `b` may read: outcome bins < b only.

        Returns (key, counts) where `counts` is the 12-state histogram of the key's observed
        transitions - the exact row `signal_of_row` consumes.  The counting range is why no
        transition whose outcome bin had not completed at the decision bar can appear here.
        """
        key = tuple(int(s) for s in self.states[b - self.seq_len:b])
        slot = transition_counts(self.states, self.seq_len, 0, b).get(key)
        return key, (list(slot) if slot is not None else [0] * STATE_COUNT)

    def window_diagnostics(self, lo_bin, hi_bin):
        """Registered diagnostics of one bin range (used by the historical stability reader)."""
        lo = max(int(lo_bin), 0)
        hi = min(int(hi_bin), int(len(self.states)))
        decisions = int(np.count_nonzero(self.row_has_decision[lo:hi])) * BASE_BARS_PER_BIN
        untrained = int(np.count_nonzero(self.row_untrained[lo:hi])) * BASE_BARS_PER_BIN
        return {"bin_lo": lo, "bin_hi": hi, "decision_bars": decisions,
                "untrained_rows": untrained,
                "untrained_share": (untrained / float(decisions)) if decisions else 0.0}

    def stability(self, lo_bin, hi_bin):
        """Registered split-half diagnostics of one bin range: two DISJOINT halves of the
        completed bins, both count matrices, and the max |p_bull| deviation over the sequences
        that carry at least MIN_STABILITY_OBSERVATIONS_PER_HALF observed transitions in EACH
        half (a deviation read off thinner rows measures sparsity, not instability), plus the
        untrained share of the range's decision bars.  The deviation part reports
        `insufficient_evidence` and never triggers unless at least MIN_STABILITY_ROWS
        comparable sequences exist.
        """
        lo = max(int(lo_bin), 0)
        hi = min(int(hi_bin), int(len(self.states)))
        mid = lo + (hi - lo) // 2
        a = transition_counts(self.states, self.seq_len, lo, mid)
        b_counts = transition_counts(self.states, self.seq_len, mid, hi)
        common = sorted(set(a) & set(b_counts))
        comparable = [k for k in common
                      if sum(a[k]) >= MIN_STABILITY_OBSERVATIONS_PER_HALF
                      and sum(b_counts[k]) >= MIN_STABILITY_OBSERVATIONS_PER_HALF]
        worst = None
        for key in comparable:
            ta = sum(a[key])
            tb = sum(b_counts[key])
            if ta <= 0 or tb <= 0:
                continue
            dev = abs(sum(a[key][:6]) / float(ta) - sum(b_counts[key][:6]) / float(tb))
            if worst is None or dev > worst:
                worst = dev
        evidence_sufficient = len(comparable) >= MIN_STABILITY_ROWS
        diag = self.window_diagnostics(lo, hi)
        return {"bin_lo": lo, "bin_hi": hi, "bin_mid": mid,
                "bins_half_a": max(mid - lo, 0), "bins_half_b": max(hi - mid, 0),
                "sequences_half_a": len(a), "sequences_half_b": len(b_counts),
                "sequences_observed_in_both_halves": len(common),
                "min_observations_per_half": MIN_STABILITY_OBSERVATIONS_PER_HALF,
                "min_comparable_rows": MIN_STABILITY_ROWS,
                "comparable_rows": len(comparable),
                "evidence_sufficient": evidence_sufficient,
                "max_abs_pbull_deviation": worst,
                "max_abs_pbull_deviation_is_evidence_backed": bool(
                    evidence_sufficient and worst is not None),
                "threshold_pbull_deviation": 0.25,
                "decision_bars": diag["decision_bars"],
                "untrained_rows": diag["untrained_rows"],
                "untrained_share": diag["untrained_share"],
                "threshold_untrained_share": 0.5}


def signals_for(cohort, case):
    """Per (cohort, case) cached signal layer (never recomputed per DCA config or per grid)."""
    key = tuple(int(v) for v in case)
    cache = getattr(cohort, "_signal_cache", None)
    if cache is None:
        cache = cohort._signal_cache = {}
    layer = cache.get(key)
    if layer is None:
        layer = SignalLayer(cohort, case)
        cache[key] = layer
    return layer


def bin_range_of(cohort, start_date, end_date):
    """The completed bins of one registered window: those whose CLOSE instant lies inside it."""
    lo = utc_ms(start_date)
    hi = utc_ms(end_date) + MS_PER_DAY - 1
    return (int(np.searchsorted(cohort.bin_close_ms, lo, side="left")),
            int(np.searchsorted(cohort.bin_close_ms, hi, side="right")))


def signal_report_of(cohort):
    """Measured shape of the registered signal layer of one cohort (evidence, not a gate)."""
    out = {"cases": {}, "registered_cases": len(CASE_ORDER),
           "cached_cases": len(getattr(cohort, "_signal_cache", {}))}
    for case in CASE_ORDER:
        layer = signals_for(cohort, case)
        out["cases"][layer.case_label] = dict(layer.diag, **{
            "thr": layer.thr, "seq_len": layer.seq_len, "minp": layer.minp})
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
    # /qlib/work is a rebuildable derived/cache area (INV-5): rebuild from scratch so
    # the recorded build wall time / size describe THIS attempt's store.
    if os.path.isdir(WORK_ROOT):
        shutil.rmtree(WORK_ROOT)
    per_dataset = {}
    for symbol in data["symbols"]:
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
    for symbol in data["symbols"]:
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

    Carries the registered causal 4 h volume-price state layer (`build_state_layer`) and the
    per (cohort, case) signal-layer cache.
    """

    def __init__(self, symbol, tf, start, end):
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
        # the registered 4 h state layer (guards + look-ahead control)
        self.state_report = build_state_layer(self)
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
    signals = layer.signals
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
        if b <= last_exit_bar:
            # a later fresh event that fires while a position is still open (or on the flatten
            # bar itself) is skipped: no re-entry without a new, later fresh signal
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
        if (int(signals[i0 + b]) != sign
                or (i0 + b > 0 and int(signals[i0 + b - 1]) == sign)
                or int(cohort.open_time_ms[i0 + eb])
                != int(cohort.open_time_ms[i0 + b]) + (1 + delay) * cohort.bar_ms):
            counter(kind, "entry_bar_not_the_next_bar_after_the_signal")
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
            # registered exit (3): the record's own signal exit, evaluated at this bar's close.
            # An opposite signal while the position is PROFITABLE against its running average
            # cost flattens every layer at the NEXT bar's open; an opposite signal while the
            # position is NOT profitable is held (and measured).
            opp = int(signals[i0 + t]) if (i0 + t) < len(signals) else 0
            if opp != 0 and opp != sign:
                avg = cost / qty
                profitable = (C[t] > avg) if sign > 0 else (C[t] < avg)
                if not profitable:
                    counter(kind, "signal_exit_blocked_unprofitable")
                elif t + 1 < n:
                    # fail-closed re-check of the registered profitability condition against the
                    # running average cost the accounting actually uses (must never fire)
                    avg_chk = cost / qty
                    if not ((C[t] > avg_chk) if sign > 0 else (C[t] < avg_chk)):
                        counter(kind, "signal_exit_not_profitable")
                    xpx = O[t + 1] - sign * slip_ticks * tick
                    ep_gross = exit_price_pnl(sign * qty * xpx, sign * cost)
                    realized += sign * qty * xpx - sign * cost
                    gross_pnl += ep_gross
                    charge_fee(qty * xpx * taf)
                    ep_fees += qty * xpx * taf
                    fills += 1
                    turnover += qty * xpx
                    time_exits += 1
                    counter(kind, "signal_exit_taken")
                    exit_bar = t
                    exit_at_open = True
                    broke = True
                    break
                else:
                    # an opposite PROFITABLE signal on the last bar cannot be executed at the
                    # next bar's open (there is none): it is deferred to the slice-end flatten
                    counter(kind, "signal_exit_deferred_to_slice_end")
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
           "thr_1_0": p["thr_1_0"], "thr_1_5": p["thr_1_5"], "thr_2_0": p["thr_2_0"],
           "seq_len_1": p["seq_len_1"], "seq_len_2": p["seq_len_2"],
           "seq_len_3": p["seq_len_3"],
           "minp_0_5": p["minp_0_5"], "minp_0_6": p["minp_0_6"], "minp_0_7": p["minp_0_7"],
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
# Every reader below is registered in the immutable round-spec BEFORE the first run.  A reader
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


def _winner_case(c):
    w = c["winner"]
    return tuple(int(w[f]) for f in CASE_FIELDS), {a: w[a] for a in DCA_AXES}


def transition_matrix_stability_check(spec, cohort_results, rows_by_cohort, diag_inputs):
    """Reader 1 - record: "verify if transition probabilities are stable over time or if the
    matrix requires constant refitting".

    Definition: from the winner's registered state layer over the HISTORICAL window only, the
    completed bins are split into two disjoint halves, both count matrices are built, and over
    every sequence observed in BOTH halves the max absolute deviation of p_bull is reported,
    together with the share of the winner's historical decision bars whose row is untrained.
    TRIGGERED if the evidence-sufficient max_abs_pbull_deviation > 0.25 or untrained_share >
    0.5.
    """
    out = {"registered_item": "transition-matrix-stability", "evaluated": False, "hit": False,
           "landing": "family-level: a hit must NEVER be recorded as PASS", "cohorts": {},
           "threshold_pbull_deviation": 0.25, "threshold_untrained_share": 0.5,
           "min_observations_per_half": MIN_STABILITY_OBSERVATIONS_PER_HALF,
           "min_comparable_rows": MIN_STABILITY_ROWS}
    for c in cohort_results:
        if c["winner"] is None:
            continue
        winner_case, _dca = _winner_case(c)
        cohort = diag_inputs[c["cohort"]][0]
        layer = signals_for(cohort, winner_case)
        lo, hi = bin_range_of(cohort, spec["data"]["historical_start"],
                              spec["data"]["historical_end"])
        st = layer.stability(lo, hi)
        deviation_hit = bool(st["max_abs_pbull_deviation_is_evidence_backed"]
                             and st["max_abs_pbull_deviation"]
                             > st["threshold_pbull_deviation"])
        untrained_hit = bool(st["untrained_share"] > st["threshold_untrained_share"])
        hit = bool(deviation_hit or untrained_hit)
        out["evaluated"] = True
        out["hit"] = out["hit"] or hit
        out["cohorts"][c["cohort"]] = dict(st, case_name=layer.case_label, hit=hit,
                                           deviation_hit=deviation_hit,
                                           untrained_hit=untrained_hit)
    return out


def exit_asymmetry_check(spec, cohort_results, rows_by_cohort, diag_by_cohort):
    """Reader 2 - record: "stress test the exit on sell signal only if profitable rule".

    Definition: blocked opposite-signal exits / total opposite-signal exits over the FULL
    window of the winner's cell (which is re-run with its structural counters isolated).
    TRIGGERED if the blocked share >= 0.5; the winner's win rate and mean hold are reported
    alongside (descriptive).
    """
    out = {"registered_item": "exit-asymmetry", "evaluated": False, "hit": False,
           "landing": "family-level: a hit must NEVER be recorded as PASS", "cohorts": {},
           "threshold_blocked_share": 0.5}
    for c in cohort_results:
        if c["winner"] is None:
            continue
        delta = (diag_by_cohort.get(c["cohort"]) or {}).get("counter_delta") or {}
        taken = int(delta.get("signal_exit_taken", 0))
        blocked = int(delta.get("signal_exit_blocked_unprofitable", 0))
        deferred = int(delta.get("signal_exit_deferred_to_slice_end", 0))
        total = taken + blocked + deferred
        share = (blocked / float(total)) if total else None
        hit = bool(share is not None and share >= 0.5)
        stats = (diag_by_cohort.get(c["cohort"]) or {}).get("episode_stats") or {}
        out["evaluated"] = True
        out["hit"] = out["hit"] or hit
        out["cohorts"][c["cohort"]] = {
            "signal_exit_taken": taken, "signal_exit_blocked_unprofitable": blocked,
            "signal_exit_deferred_to_slice_end": deferred,
            "opposite_signal_evaluations": total,
            "blocked_share": share, "threshold": 0.5, "evalulated_scope": "full window",
            "winner_win_rate": stats.get("win_rate"), "winner_mean_hold_bars":
                stats.get("mean_hold_bars"), "winner_episodes": stats.get("episodes"),
            "case_name": c.get("winner_case_label"), "hit": hit}
    return out


def cost_boundary_check(spec, cohort_results, rows_by_cohort):
    """Reader 3 - record falsification item 2: "the effect disappears after realistic fees,
    spread, and slippage".

    Definition: for every elected winner, the FULL-window net-PnL sign under the canonical cost
    track must equal its sign under the registered 40 bps-per-fill attrition grid.
    """
    out = {"registered_item": "cost-boundary", "evaluated": False, "hit": False,
           "landing": "family-level: a hit must NEVER be recorded as PASS",
           "cohorts": {}}
    for c in cohort_results:
        if c["winner"] is None:
            continue
        winner_case, dca = _winner_case(c)
        base = _cell(rows_by_cohort, c["cohort"], "full", winner_case, dca)
        att = _cell(rows_by_cohort, c["cohort"], "cost_attrition_40bps", winner_case, dca)
        if base is None or att is None:
            out["cohorts"][c["cohort"]] = {"evaluated": False, "hit": False}
            continue
        s0, s1 = _sign(base["net_pnl"]), _sign(att["net_pnl"])
        hit = s0 != s1
        out["evaluated"] = True
        out["hit"] = out["hit"] or hit
        out["cohorts"][c["cohort"]] = {"full_net_pnl": base["net_pnl"],
                                       "full_net_pnl_sign": s0,
                                       "cost_attrition_40bps_net_pnl": att["net_pnl"],
                                       "cost_attrition_40bps_net_pnl_sign": s1,
                                       "evaluated": True, "hit": hit}
    return out


def cross_asset_dispersion_check(spec, cohort_results, rows_by_cohort):
    """Reader 4 - record limitation: "underspecified robustness of transition probabilities
    across different assets".

    Definition: the share of the FOUR registered cohorts whose historical winner carries
    net_pnl > 0.  TRIGGERED if that share < 0.5 (the transition probabilities then do not
    travel across the registered asset set).
    """
    out = {"registered_item": "cross-asset-dispersion", "evaluated": False, "hit": False,
           "landing": "family-level: a hit must NEVER be recorded as PASS",
           "threshold_positive_cohort_share": 0.5, "cohorts": {}}
    if not cohort_results:
        return out
    positive = 0
    for c in cohort_results:
        net = None
        if c["winner"] is not None:
            net = (c.get("metrics") or {}).get("historical", {}).get("net_pnl")
        ok = bool(net is not None and net > 0.0)
        positive += 1 if ok else 0
        out["cohorts"][c["cohort"]] = {"winner_historical_net_pnl": net,
                                       "winner_net_pnl_positive": ok}
    share = positive / float(len(cohort_results))
    out["evaluated"] = True
    out["positive_cohorts"] = positive
    out["cohorts_evaluated"] = len(cohort_results)
    out["positive_cohort_share"] = share
    out["hit"] = bool(share < 0.5)
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
    stability = (transition_matrix_stability_check(spec, cohort_results, rows_by_cohort, diag_inputs)
                 if coverage_complete else dict(empty_reader,
                                                registered_item="transition-matrix-stability"))
    exit_asymmetry = (exit_asymmetry_check(spec, cohort_results, rows_by_cohort, diag_by_cohort)
                      if coverage_complete else dict(empty_reader,
                                                     registered_item="exit-asymmetry"))
    cost_boundary = (cost_boundary_check(spec, cohort_results, rows_by_cohort)
                     if coverage_complete else dict(empty_reader, registered_item="cost-boundary"))
    cross_asset = (cross_asset_dispersion_check(spec, cohort_results, rows_by_cohort)
                   if coverage_complete else dict(empty_reader,
                                                  registered_item="cross-asset-dispersion"))
    flags = {"transition_matrix_stability": bool(stability.get("hit")),
             "exit_asymmetry": bool(exit_asymmetry.get("hit")),
             "cost_boundary": bool(cost_boundary.get("hit")),
             "cross_asset_dispersion": bool(cross_asset.get("hit"))}

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
    state_defs = [diag_inputs[label][0].state_report for label in cohort_labels] \
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
        # H-specific (registered names, section 3 of the design spec)
        "bin_grid_is_aligned_and_complete": (counters_total("bin_grid_not_aligned") == 0
                                             and counters_total("bin_availability_mismatch") == 0),
        "attached_state_was_available_before_bar":
            counters_total("attached_state_before_availability") == 0,
        "matrix_counts_are_causal":
            counters_total("matrix_row_contained_future_outcome") == 0,
        "signal_requires_trained_row": counters_total("signal_from_untrained_row") == 0,
        "entry_bar_is_the_next_bar_after_the_signal":
            counters_total("entry_bar_not_the_next_bar_after_the_signal") == 0,
        "signal_exit_required_profitability":
            counters_total("signal_exit_not_profitable") == 0,
        "no_reentry_without_a_fresh_signal": (
            counters_sum("entry_bar_not_the_next_bar_after_the_signal", FULL_WINDOW_GRID_KINDS) == 0
            and counters_sum("reentry_skipped_in_position", FULL_WINDOW_GRID_KINDS)
            + full_episodes + counters_sum("entry_refused_exhausted", FULL_WINDOW_GRID_KINDS)
            == full_events),
        "state_index_covers_the_registered_twelve": (
            counters_total("state_index_out_of_range") == 0
            and all(min(s["state_index_domain"]) == 0
                    and max(s["state_index_domain"]) == STATE_COUNT - 1 for s in state_defs)),
        "lookahead_control_reported": (
            counters_total("attached_state_before_availability") == 0
            and all(s["lookahead_control"]["naive_attachments_before_the_bin_closed"] > 0
                    for s in state_defs)),
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
                         "price_state_thresholds": list(PRICE_STATE_THRESHOLDS),
                         "sequence_lengths": list(SEQUENCE_LENGTHS),
                         "min_signal_probabilities": list(MIN_SIGNAL_PROBABILITIES),
                         "markov_constants": {"bin_hours": BIN_HOURS,
                                              "base_bars_per_bin": BASE_BARS_PER_BIN,
                                              "state_count": STATE_COUNT,
                                              "bull_states": list(BULL_STATES),
                                              "bear_states": list(BEAR_STATES),
                                              "atr_period": ATR_PERIOD,
                                              "volume_sma_period": VOLUME_SMA_PERIOD,
                                              "volume_high_multiplier": VOLUME_HIGH_MULTIPLIER,
                                              "volume_low_multiplier": VOLUME_LOW_MULTIPLIER,
                                              "min_row_observations": MIN_ROW_OBSERVATIONS}},
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
        "transition_matrix_stability": stability,
        "exit_asymmetry": exit_asymmetry,
        "cost_boundary": cost_boundary,
        "cross_asset_dispersion": cross_asset,
        "state_report": ({label: state_summary(diag_inputs[label][0])
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
        sys.stderr.write("usage: 80_strategy_h_run.py <run-spec.json>\n")
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
                    "disposition_version", "gates", "costs"):
            if key not in spec:
                raise SystemExit("run-spec is not a Strategy H spec: missing %r (contract 7.2/7.3)"
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
                cohort = Cohort(symbol, tf, spec["data"]["start"], spec["data"]["end"])
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
                sr = cohort.state_report
                run_log("cohort %s bars=%d bins=%d defined_states=%d funding_obs=%d "
                        "modeled=%d official=%d"
                        % (label, cohort.n, sr["bins"], sr["defined_state_bins"], len(ft),
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
                          {"transition_matrix_stability": summary["transition_matrix_stability"],
                           "exit_asymmetry": summary["exit_asymmetry"],
                           "cost_boundary": summary["cost_boundary"],
                           "cross_asset_dispersion": summary["cross_asset_dispersion"],
                           "flags": summary["registered_family_level_falsification_flags"]})
        atomic_write_json(os.path.join(attempt_dir, "artifacts", "state_layer.json"),
                          summary["state_report"])
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
