#!/usr/bin/env python3
"""Executable check for the Strategy H execution engine (Markov-chain volume-price state).

Runs inside the qlib container:
    container exec qlib-run env SH_ENGINE_PATH=/scripts/80_strategy_h_run.py \
        /opt/venv/bin/python /scripts/tests/test_strategy_h_engine.py
and on the host with a numpy interpreter:
    SH_ENGINE_PATH=<repo>/container/scripts/80_strategy_h_run.py python3 <this file>

Drives the pure functions with synthetic 1 h bars and synthetic 4 h state sequences whose
fills, fees and funding charges are known by hand, so a regression in the 4 h bin construction
(alignment, availability, the look-ahead correction), the state partition (price/volume
boundaries, ATR/volume warm-up), the point-in-time transition matrix (strict causality), the
signal rule (trained rows only, exact tie is NONE), the fresh-signal entry, the record's
profitability-gated opposite-signal exit, the window-bounded rail, the per-fill fee ledger, the
independent gross accumulator, the funding-exposure rule or the cohort gate fails loudly
instead of silently changing the science.  No market data, no container state, no network:
stdlib unittest + numpy only.
"""
import importlib.util
import itertools
import os
import unittest

import numpy as np

ENGINE = os.environ.get("SH_ENGINE_PATH", "/scripts/80_strategy_h_run.py")
_spec = importlib.util.spec_from_file_location("sh_engine", ENGINE)
sh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sh)

BAR_MS = 3600000                    # the registered 1h base grid
BIN_BARS = 4                        # exactly four base bars per 4 h bin
BASE_MS = 1735689600000             # 2025-01-01T00:00:00Z (a clean 4 h / UTC midnight)
MS_DAY = 86400000
P0 = 100.0
LEV = 10.0
# rails: FLAT closes only at the slice end (nothing else can trigger), TIGHT is the canonical
# registered rail, LADDER isolates one scale-in, STOP isolates the resting invalidation.
RAIL_FLAT = {"base_quote": 1000.0, "size_multiplier": 1.1, "spacing_d0": 0.5, "tp": 0.5,
             "invalidation": 0.5}
RAIL_TIGHT = {"base_quote": 1000.0, "size_multiplier": 1.1, "spacing_d0": 0.02, "tp": 0.01,
              "invalidation": 0.05}
RAIL_LADDER = {"base_quote": 1000.0, "size_multiplier": 1.1, "spacing_d0": 0.02, "tp": 0.5,
               "invalidation": 0.5}
RAIL_STOP = {"base_quote": 1000.0, "size_multiplier": 1.1, "spacing_d0": 0.10, "tp": 0.5,
             "invalidation": 0.05}

LONG_RUN = [0] * 10 + [6] * 20        # bins 0..9 bullish state, bins 10..29 bearish state
SHORT_RUN = [6] * 30
LONG_ONLY = [0] * 30                  # a single fresh LONG event (bar 24), persistent after
SHORT_ONLY = [6] * 30                 # a single fresh SHORT event (bar 24), persistent after


def case_params(thr_i=0, seq_i=0, minp_i=0):
    """The registered one-hot strategy params of (threshold, sequence, min-probability)."""
    return {f: int(v) for f, v in zip(sh.CASE_FIELDS, sh.case_fields(thr_i, seq_i, minp_i))}


def case_tuple_of(thr_i=0, seq_i=0, minp_i=0):
    return sh.case_fields(thr_i, seq_i, minp_i)


class FakeCohort:
    """1 h bars on a clean UTC grid; bar i opens at base_ms + i * 1 h."""

    def __init__(self, closes, highs=None, lows=None, opens=None, vols=None,
                 base_ms=BASE_MS, bar_ms=BAR_MS):
        n = len(closes)
        self.symbol = "SYNTH"
        self.timeframe = "1h"
        self.close = np.array(closes, dtype=np.float64)
        self.open = np.array(opens if opens is not None else closes, dtype=np.float64)
        self.high = np.array(highs if highs is not None else closes, dtype=np.float64)
        self.low = np.array(lows if lows is not None else closes, dtype=np.float64)
        self.volume = np.array(vols if vols is not None else [1.0] * n, dtype=np.float64)
        self.n = n
        self.open_time_ms = np.array([base_ms + i * bar_ms for i in range(n)], dtype=np.int64)
        self.bar_ms = bar_ms
        self.price_increment = 0.0
        self.taker_fee = 0.0
        self.leverage = LEV
        self.margin_maint = 0.1
        self._signal_cache = {}
        self.state_report = sh.build_state_layer(self)

    def slice(self, _a, _b):
        return 0, self.n

    def signals_for(self, case):
        return sh.signals_for(self, case)


def mk_cohort(n_bars, price=P0, vols=None):
    """A flat 1 h cohort (a flat close makes every REAL state undefined, which is exactly what
    the crafted-state fixtures need)."""
    return FakeCohort([price] * n_bars, vols=vols)


def craft_states(cohort, states):
    """Attach a crafted 4 h state sequence to every registered threshold (signal-layer input).

    The state layer itself is exercised with real price paths elsewhere; the transitions,
    the signal rule and the episode machinery only consume the state sequence, so crafting it
    here makes those layers deterministic without weakening them.
    """
    st = np.array(states, dtype=np.int8)
    if len(st) != cohort.bins_per_bar:
        raise ValueError("crafted %d states for %d bins" % (len(st), cohort.bins_per_bar))
    cohort.bin_state = st
    cohort.bin_state_by_threshold = (st, st.copy(), st.copy())
    cohort._signal_cache = {}
    return cohort


def mk_series(cohort, settlements=()):
    times = np.array([int(t) for t, _r in settlements], dtype=np.int64)
    rates = np.array([float(r) for _t, r in settlements], dtype=np.float64)
    containing = (np.searchsorted(cohort.open_time_ms, times, side="left") - 1
                  if len(times) else np.array([], dtype=np.int64))
    closed = (np.searchsorted(cohort.open_time_ms, times, side="right") - 1
              if len(times) else np.array([], dtype=np.int64))
    return {"label": "synthetic", "counts": {}, "obs_times": times, "obs_rates": rates,
            "settle_bar": containing.astype(np.int64),
            "settle_bar_closed": closed.astype(np.int64),
            "first_obs_ms": None, "last_obs_ms": None}


def run(cohort, params, rail=None, series=None, slip=0.0, stress=None, kind="full", diag=False,
        count_layers=True, window=None):
    w = window or (0, cohort.n)
    return sh.simulate(cohort, params, rail or RAIL_FLAT, w, stress or {}, slip, kind,
                       series or mk_series(cohort), diag=diag, count_layers=count_layers)


def reset_counters():
    sh.COUNTERS.clear()


def naive_detector(bin_close_ms, open_time_ms, source_bin):
    """TEST-LOCAL look-ahead detector: count the bars whose attached state came from a bin that
    had NOT closed yet at that bar's open (the record's left-edge / backward-as-of defect)."""
    bad = 0
    for t in range(len(open_time_ms)):
        b = int(source_bin[t])
        if b >= 0 and int(bin_close_ms[b]) > int(open_time_ms[t]):
            bad += 1
    return bad


def naive_full_matrix(states, seq_len):
    """TEST-LOCAL naive matrix: ALL transitions, including outcome bins that had not completed
    at the decision bar (the defect the engine's point-in-time matrix must not have)."""
    out = {}
    for j in range(seq_len, len(states)):
        key = tuple(int(s) for s in states[j - seq_len:j])
        if -1 in key or int(states[j]) < 0:
            continue
        slot = out.setdefault(key, [0] * sh.STATE_COUNT)
        slot[int(states[j])] += 1
    return out


def winner_stub(thr_i=0, seq_i=0, minp_i=0):
    """The winner cell the family-level readers consume (strategy params + DCA params only)."""
    d = {f: v for f, v in zip(sh.CASE_FIELDS, sh.case_fields(thr_i, seq_i, minp_i))}
    d.update({"spacing_pct": 0.02, "size_multiplier": 1.1, "breakeven_tp_pct": 0.01,
              "invalidation_pct": 0.05})
    return d


def row_for(kind, net_pnl):
    d = {f: v for f, v in zip(sh.CASE_FIELDS, sh.case_fields(0, 0, 0))}
    d.update({"spacing_pct": 0.02, "size_multiplier": 1.1, "breakeven_tp_pct": 0.01,
              "invalidation_pct": 0.05, "window_kind": kind, "net_pnl": net_pnl})
    return d


def alternating_price_cohort(n_bins, big=0.06, tiny=0.0005, base_ms=BASE_MS):
    """A real price path whose bin CLOSE jumps strongly every 4th bin while the interior bars
    stay flat (the fixture of design_spec section 4: a bin close very different from its own
    interior bars).  Every bin holds exactly four 1 h bars; volume is flat."""
    closes, opens, highs, lows = [], [], [], []
    price = P0
    for b in range(n_bins):
        step = big if (b % BIN_BARS == 0 and b) else tiny
        target = price * (1.0 + step)
        for k in range(BIN_BARS):
            o = price
            c = target if k == BIN_BARS - 1 else price
            opens.append(o)
            closes.append(c)
            highs.append(max(o, c))
            lows.append(min(o, c))
        price = target
    return FakeCohort(closes, highs, lows, opens)


class TestStateLayer(unittest.TestCase):

    def setUp(self):
        reset_counters()

    def test_bins_hold_exactly_four_bars_and_are_4h_aligned(self):
        c = mk_cohort(40)
        self.assertEqual(c.bins_per_bar, 10)
        self.assertEqual(c.state_tail_bars, 0)
        self.assertEqual(list(c.bin_first_bar), [0, 4, 8, 12, 16, 20, 24, 28, 32, 36])
        self.assertEqual(list(c.bin_last_bar), [3, 7, 11, 15, 19, 23, 27, 31, 35, 39])
        for b in range(c.bins_per_bar):
            self.assertEqual(int(c.bin_open_ms[b]) % sh.MS_PER_BIN, 0)
            self.assertEqual(int(c.bin_close_ms[b]) - int(c.bin_open_ms[b]), sh.MS_PER_BIN)
            self.assertEqual(int(c.bin_available_bar[b]), int(c.bin_last_bar[b]) + 1)
            self.assertEqual(int(c.bin_open_ms[b]),
                             int(c.open_time_ms[int(c.bin_first_bar[b])]))
            if int(c.bin_available_bar[b]) < c.n:
                self.assertEqual(int(c.bin_close_ms[b]),
                                 int(c.open_time_ms[int(c.bin_available_bar[b])]))
        self.assertEqual(int(c.bin_volume[0]), BIN_BARS)
        self.assertEqual(sh.counters_total("bin_grid_not_aligned"), 0)
        self.assertEqual(sh.counters_total("bin_availability_mismatch"), 0)

    def test_a_partial_tail_never_forms_a_bin(self):
        c = mk_cohort(43)                     # 10 complete bins + 3 trailing bars
        self.assertEqual(c.bins_per_bar, 10)
        self.assertEqual(c.state_tail_bars, 3)
        self.assertTrue(np.all(c.bin_index_of_bar[40:] == -1))
        self.assertEqual(int(c.bin_index_of_bar[39]), 9)
        self.assertEqual(int(c.state_of_bar[39]), int(c.bin_state[8]))

    def test_state_of_bar_is_the_PREVIOUS_bin_state_never_its_own_bin(self):
        c = alternating_price_cohort(30)
        n_bins = c.bins_per_bar
        self.assertEqual(n_bins, 30)
        defined = [b for b in range(n_bins) if int(c.bin_state[b]) >= 0]
        self.assertTrue(defined, "the engineered fixture must produce defined bins")
        for t in range(c.n):
            b = int(c.bin_index_of_bar[t])
            if b <= 0:
                self.assertEqual(int(c.state_of_bar[t]), -1)
            else:
                self.assertEqual(int(c.state_of_bar[t]), int(c.bin_state[b - 1]))
        # the engineered strong-close bins have an interior carrying the OTHER (previous) state
        for b in range(n_bins):
            if b >= 1 and int(c.bin_state[b]) >= 0 and int(c.bin_state[b - 1]) >= 0 \
                    and b % BIN_BARS == 0:
                for t in range(int(c.bin_first_bar[b]), int(c.bin_last_bar[b]) + 1):
                    self.assertEqual(int(c.state_of_bar[t]), int(c.bin_state[b - 1]))
                self.assertNotEqual(int(c.state_of_bar[int(c.bin_first_bar[b])]),
                                    int(c.bin_state[b]))

    def test_lookahead_red_control_naive_mapping_is_detected_and_the_engine_is_clean(self):
        c = alternating_price_cohort(30)
        times = c.open_time_ms
        close_ms = c.bin_close_ms
        engine_bad = naive_detector(close_ms, times, c.state_source_bin)
        naive_bad = naive_detector(close_ms, times, c.bin_index_of_bar)
        print("look-ahead detector: engine=%d violation(s), naive left-edge=%d violation(s)"
              % (engine_bad, naive_bad))
        self.assertEqual(engine_bad, 0)                # the engine's mapping is clean
        self.assertGreater(naive_bad, 0)               # the RED control really can fail
        self.assertEqual(sh.counters_total("attached_state_before_availability"), 0)
        self.assertGreater(c.state_lookahead_control["naive_attachments_before_the_bin_closed"], 0)
        self.assertEqual(
            c.state_lookahead_control["naive_attachments_before_the_bin_closed"], naive_bad)
        self.assertGreater(c.state_lookahead_control["engine_vs_naive_divergent_bars"], 0)

    def test_a_grid_hole_or_an_unaligned_first_bar_fails_closed(self):
        c = mk_cohort(40)
        c.open_time_ms[11] += BAR_MS
        with self.assertRaises(SystemExit):
            sh.build_state_layer(c)
        self.assertGreater(sh.counters_total("bin_grid_not_aligned"), 0)
        reset_counters()
        c2 = mk_cohort(40)
        c2.open_time_ms = c2.open_time_ms + BAR_MS          # no longer on a 4 h boundary
        with self.assertRaises(SystemExit):
            sh.build_state_layer(c2)
        self.assertGreater(sh.counters_total("bin_grid_not_aligned"), 0)

    def test_state_summary_reports_the_layer_shape(self):
        c = alternating_price_cohort(30)
        s = sh.state_summary(c)
        self.assertEqual(s["bins"], 30)
        self.assertEqual(s["base_bars_per_bin"], BIN_BARS)
        self.assertEqual(sorted(s["state_histogram_canonical_threshold"].keys()),
                         sorted(str(i) for i in range(12)))
        self.assertEqual(s["availability_margin_bars"], 1)
        self.assertGreaterEqual(s["availability_margin_ms"]["min"], 0)
        self.assertGreater(s["lookahead_control"]["naive_attachments_before_the_bin_closed"], 0)
        self.assertFalse(s["raw_resample_check"].get("available", False))   # no raw on the host


class TestStatePartition(unittest.TestCase):

    def test_price_partition_boundaries_are_exact(self):
        thr = 1.5
        self.assertEqual(sh.price_state_of(thr, thr), 0)            # r == thr -> strong up
        self.assertEqual(sh.price_state_of(thr + 1e-12, thr), 0)
        self.assertEqual(sh.price_state_of(0.0, thr), 1)            # r == 0 -> mild up
        self.assertEqual(sh.price_state_of(thr - 1e-12, thr), 1)
        self.assertEqual(sh.price_state_of(-1e-12, thr), 2)         # slightly negative -> mild down
        self.assertEqual(sh.price_state_of(-thr + 1e-12, thr), 2)
        self.assertEqual(sh.price_state_of(-thr, thr), 3)           # r == -thr -> strong down
        self.assertEqual(sh.price_state_of(-thr - 1e-9, thr), 3)
        self.assertEqual(sh.price_state_of(float("nan"), thr), -1)  # no ATR -> no state
        self.assertEqual(sh.price_state_of(1.0, float("nan")), -1)

    def test_volume_partition_treats_equality_as_normal(self):
        vsma = 10.0
        self.assertEqual(sh.volume_state_of(4.999999, vsma), 0)
        self.assertEqual(sh.volume_state_of(5.0, vsma), 1)          # equality -> normal
        self.assertEqual(sh.volume_state_of(12.0, vsma), 1)
        self.assertEqual(sh.volume_state_of(15.0, vsma), 1)         # equality -> normal
        self.assertEqual(sh.volume_state_of(15.000001, vsma), 2)
        self.assertEqual(sh.volume_state_of(1.0, float("nan")), -1)  # incomplete window
        self.assertEqual(sh.volume_state_of(1.0, 0.0), -1)

    def test_state_layer_needs_the_complete_atr_and_volume_windows(self):
        c = alternating_price_cohort(25)              # 25 bins: 14 for the ATR, 24 for the vsma
        states = c.bin_state
        self.assertTrue(np.all(states[:sh.VOLUME_SMA_PERIOD - 1] == -1))
        self.assertGreaterEqual(int(states[sh.VOLUME_SMA_PERIOD - 1]), 0)
        self.assertTrue(np.all(np.isnan(c.bin_atr[:sh.ATR_PERIOD])))
        self.assertTrue(np.isfinite(c.bin_atr[sh.ATR_PERIOD]))
        self.assertTrue(np.all(np.isnan(c.bin_vsma[:sh.VOLUME_SMA_PERIOD - 1])))
        self.assertTrue(np.isfinite(c.bin_vsma[sh.VOLUME_SMA_PERIOD - 1]))

    def test_state_index_is_price_state_times_three_plus_volume_state(self):
        c = mk_cohort(30 * BIN_BARS)
        for b in range(c.bins_per_bar):
            if int(c.bin_state[b]) < 0:
                continue
            self.assertEqual(int(c.bin_state[b]),
                             int(c.bin_price_state[b]) * 3 + int(c.bin_volume_state[b]))
            self.assertIn(int(c.bin_state[b]), list(range(sh.STATE_COUNT)))


class TestSignalRule(unittest.TestCase):

    def test_long_short_none_tie_and_untrained(self):
        bull = [6, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
        bear = [0, 0, 0, 0, 0, 0, 6, 0, 0, 0, 0, 0]
        tie = [3, 0, 0, 0, 0, 0, 3, 0, 0, 0, 0, 0]
        self.assertEqual(sh.signal_of_row(bull, 0.5), 1)
        self.assertEqual(sh.signal_of_row(bear, 0.5), -1)
        self.assertEqual(sh.signal_of_row(tie, 0.5), 0)                 # exact tie -> NONE
        self.assertEqual(sh.signal_of_row([4, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], 0.5), 0)
        self.assertEqual(sh.signal_of_row(bull, 1.0), 0)                # p == floor -> NONE
        self.assertEqual(sh.signal_of_row([5, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], 0.5), 1)
        two = [4, 0, 0, 0, 0, 0, 2, 0, 0, 0, 0, 0]
        self.assertEqual(sh.signal_of_row(two, 0.6), 1)                 # 4/6 > 0.6
        self.assertEqual(sh.signal_of_row(two, 0.7), 0)                 # 4/6 < 0.7 -> NONE

    def test_signal_layer_emits_a_trained_long_only_after_five_observations(self):
        c = craft_states(mk_cohort(30 * BIN_BARS), LONG_RUN)
        layer = c.signals_for(case_tuple_of(0, 0, 0))
        self.assertEqual(layer.seq_len, 1)
        self.assertEqual(layer.minp, 0.5)
        self.assertTrue(np.all(layer.signals[:6 * BIN_BARS] == 0))      # untrained rows
        self.assertTrue(np.all(layer.signals[6 * BIN_BARS:11 * BIN_BARS] == 1))
        self.assertTrue(np.all(layer.signals[11 * BIN_BARS:16 * BIN_BARS] == 0))
        self.assertTrue(np.all(layer.signals[16 * BIN_BARS:] == -1))
        self.assertEqual(layer.diag["untrained_rows"], (5 + 5) * BIN_BARS)
        self.assertEqual(layer.diag["matrix_future_outcome_uses"], 0)
        self.assertGreater(layer.diag["fresh_events"], 0)

    def test_higher_min_probability_and_longer_sequences_delay_the_signal(self):
        c = craft_states(mk_cohort(30 * BIN_BARS), LONG_RUN)
        strict = c.signals_for(case_tuple_of(0, 0, 2))                   # p0.7
        self.assertTrue(np.all(strict.signals[6 * BIN_BARS:11 * BIN_BARS] == 1))
        seq3 = c.signals_for(case_tuple_of(0, 2, 0))                     # L=3, p0.5
        self.assertTrue(seq3.seq_len == 3)
        self.assertEqual(int(seq3.signals[6 * BIN_BARS]), 0)
        self.assertEqual(int(seq3.signals[9 * BIN_BARS]), 1)

    def test_unregistered_case_fails_closed_in_both_layers(self):
        c = craft_states(mk_cohort(10 * BIN_BARS), LONG_RUN[:10])
        bogus = {"thr_1_0": 0, "thr_1_5": 0, "thr_2_0": 0, "seq_len_1": 0, "seq_len_2": 0,
                 "seq_len_3": 0, "minp_0_5": 0, "minp_0_6": 0, "minp_0_7": 0}
        with self.assertRaises(SystemExit):
            sh.signals_for(c, tuple(bogus[f] for f in sh.CASE_FIELDS))
        self.assertGreater(sh.counters_total("case_not_registered"), 0)


class TestMatrixCausality(unittest.TestCase):

    def setUp(self):
        reset_counters()
        # 6 identical bullish bins, then an alternating 0 -> 6 pattern: the PIT row of the key
        # (0,) visible at bin 6 is 5 bullish observations, while the naive all-transitions
        # matrix would let the FUTURE 0 -> 6 transitions flip that row to bearish.
        self.states = np.array([0] * 6 + [6, 0] * 10, dtype=np.int8)
        self.cohort = craft_states(mk_cohort(len(self.states) * BIN_BARS), self.states)
        self.layer = self.cohort.signals_for(case_tuple_of(0, 0, 0))

    def test_the_pit_row_never_contains_an_incomplete_outcome_bin(self):
        key, row = self.layer.matrix_row(6)
        self.assertEqual(key, (0,))
        self.assertEqual(sum(row), 5)
        self.assertEqual(sh.signal_of_row(row, 0.5), 1)
        naive = naive_full_matrix(self.states, 1)
        self.assertGreater(sum(naive[(0,)]), sum(row))
        self.assertEqual(sh.signal_of_row(naive[(0,)], 0.5), -1)
        print("row at bin 6: engine=%s (obs=%d, signal=%d), naive all-transitions=%s "
              "(obs=%d, signal=%d)"
              % (row, sum(row), sh.signal_of_row(row, 0.5), naive[(0,)], sum(naive[(0,)]),
                 sh.signal_of_row(naive[(0,)], 0.5)))

    def test_the_engine_signal_differs_from_the_naive_matrix_signal(self):
        self.assertEqual(int(self.layer.signals[6 * BIN_BARS]), 1)
        self.assertEqual(self.layer.diag["matrix_future_outcome_uses"], 0)
        self.assertEqual(sh.counters_total("matrix_row_contained_future_outcome"), 0)
        # the naive all-transitions matrix at the SAME bar would report the opposite direction
        naive = naive_full_matrix(self.states, 1)
        self.assertEqual(sh.signal_of_row(naive[(0,)], 0.5), -1)
        self.assertNotEqual(sh.signal_of_row(naive[(0,)], 0.5),
                            int(self.layer.signals[6 * BIN_BARS]))
        _, row = self.layer.matrix_row(6)
        self.assertEqual(sum(row[6:]), 0)          # the engine's row holds no future bearish fill
        self.assertGreater(sum(naive[(0,)][6:]), 0)


class TestEpisodes(unittest.TestCase):

    def setUp(self):
        reset_counters()

    def test_entry_is_the_open_of_the_bar_after_a_fresh_signal(self):
        c = craft_states(mk_cohort(30 * BIN_BARS), LONG_RUN)
        c.taker_fee = 0.0005
        m = run(c, case_params(0, 0, 0), rail=RAIL_FLAT)
        self.assertEqual(m["windows_seen"], 2)          # the +1 event (bar 24) and the -1 (bar 64)
        self.assertEqual(m["episodes"], 1)
        qty = 1000.0 * LEV / P0
        self.assertEqual(m["fills"], 2)                 # entry + slice-end flatten
        self.assertAlmostEqual(m["fees"], 2 * qty * P0 * 0.0005, places=6)
        self.assertAlmostEqual(m["gross_pnl"], 0.0, places=6)
        self.assertAlmostEqual(m["net_pnl"], -m["fees"], places=6)
        self.assertEqual(m["open_at_end"], 1)
        self.assertEqual(sh.counters_total("slice_end_flatten"), 1)
        self.assertEqual(sh.counters_total("entry_bar_not_the_next_bar_after_the_signal"), 0)
        self.assertTrue(sh.pnl_decomposition_ok(m))

    def test_signal_exit_when_profitable_flattens_at_the_next_bar_open(self):
        c = craft_states(mk_cohort(30 * BIN_BARS), LONG_RUN)
        c.taker_fee = 0.0005
        closes = [P0] * c.n
        for i in range(26, c.n):
            closes[i] = 100.5                            # the long is profitable against avg=P0
        c.close = np.array(closes, dtype=np.float64)
        c.high = np.array([max(a, b) for a, b in zip(c.open, c.close)], dtype=np.float64)
        c.low = np.array([min(a, b) for a, b in zip(c.open, c.close)], dtype=np.float64)
        c.open[65] = 101.0                               # the exit executes here
        c.high[65] = 101.0
        m = run(c, case_params(0, 0, 0), rail=RAIL_FLAT)
        self.assertEqual(m["episodes"], 1)
        self.assertEqual(sh.counters_total("signal_exit_taken"), 1)
        self.assertEqual(sh.counters_total("signal_exit_blocked_unprofitable"), 0)
        self.assertEqual(sh.counters_total("signal_exit_not_profitable"), 0)
        self.assertEqual(sh.counters_total("entry_bar_not_the_next_bar_after_the_signal"), 0)
        qty = 1000.0 * LEV / P0
        self.assertAlmostEqual(m["gross_pnl"], qty * (101.0 - P0), places=6)
        self.assertAlmostEqual(m["fees"], (qty * P0 + qty * 101.0) * 0.0005, places=6)
        self.assertAlmostEqual(m["net_pnl"], m["gross_pnl"] - m["fees"], places=6)
        self.assertEqual(m["time_exits"], 1)             # the signal exit is H's only time exit
        self.assertEqual(m["open_at_end"], 0)
        self.assertEqual(m["tp_hits"] + m["stop_hits"] + m["margin_calls"], 0)
        self.assertTrue(sh.pnl_decomposition_ok(m))

    def test_signal_exit_when_unprofitable_is_blocked_and_held(self):
        c = craft_states(mk_cohort(30 * BIN_BARS), LONG_RUN)
        c.taker_fee = 0.0
        closes = [P0] * c.n
        for i in range(26, c.n):
            closes[i] = 99.5                             # the long is NOT profitable
        c.close = np.array(closes, dtype=np.float64)
        c.high = np.array([max(a, b) for a, b in zip(c.open, c.close)], dtype=np.float64)
        c.low = np.array([min(a, b) for a, b in zip(c.open, c.close)], dtype=np.float64)
        m = run(c, case_params(0, 0, 0), rail=RAIL_FLAT)
        self.assertEqual(m["episodes"], 1)
        self.assertEqual(sh.counters_total("signal_exit_taken"), 0)
        # bars 64..119 carry the opposite signal while the position is unprofitable
        self.assertEqual(sh.counters_total("signal_exit_blocked_unprofitable"), c.n - 64)
        self.assertEqual(m["open_at_end"], 1)
        self.assertAlmostEqual(m["gross_pnl"],
                               RAIL_FLAT["base_quote"] * LEV / P0 * (99.5 - P0), places=6)
        self.assertTrue(sh.pnl_decomposition_ok(m))

    def test_a_persistent_signal_never_re_enters_after_a_flatten(self):
        c = craft_states(mk_cohort(30 * BIN_BARS), SHORT_ONLY)
        c.taker_fee = 0.0
        m = run(c, case_params(0, 0, 0), rail=RAIL_TIGHT)
        self.assertEqual(m["episodes"], 1)
        self.assertGreater(m["windows_seen"], 0)
        self.assertEqual(m["windows_seen"], 1)           # exactly ONE fresh event, at bar 24
        layer = c.signals_for(case_tuple_of(0, 0, 0))
        self.assertGreater(layer.diag["same_sign_signal_ignored"], 0)
        self.assertEqual(m["fills"], 2)                  # the entry and the slice-end flatten
        self.assertEqual(m["open_at_end"], 1)
        self.assertEqual(m["tp_hits"] + m["stop_hits"] + m["margin_calls"], 0)

    def test_an_event_whose_entry_bar_is_outside_the_slice_is_counted_not_pulled_in(self):
        c = craft_states(mk_cohort(30 * BIN_BARS), LONG_RUN)
        m = run(c, case_params(0, 0, 0), rail=RAIL_FLAT, window=(0, 25), kind="oos")
        self.assertEqual(m["windows_seen"], 0)
        self.assertEqual(m["episodes"], 0)
        self.assertEqual(sh.counters_total("entry_after_slice_end"), 1)
        m2 = run(c, case_params(0, 0, 0), rail=RAIL_FLAT, window=(0, 26), kind="oos")
        self.assertEqual(m2["episodes"], 1)

    def test_entry_delay_stress_moves_the_entry_bar_by_exactly_one(self):
        c = craft_states(mk_cohort(30 * BIN_BARS), LONG_ONLY)
        c.taker_fee = 0.0005
        c.open[26] = 100.2                       # the delayed entry fills at a distinct price
        c.high[26] = 100.2
        base = run(c, case_params(0, 0, 0), rail=RAIL_FLAT, kind="full")
        delayed = run(c, case_params(0, 0, 0), rail=RAIL_FLAT,
                      stress={"entry_delay_1_bar": True}, kind="entry_delay_1_bar")
        self.assertEqual(base["episodes"], delayed["episodes"])
        self.assertEqual(base["windows_seen"], delayed["windows_seen"])
        self.assertNotEqual(base["net_pnl"], delayed["net_pnl"])
        self.assertEqual(sh.counters_total("entry_bar_not_the_next_bar_after_the_signal"), 0)

    def test_the_episode_partition_and_the_registered_counters_hold(self):
        c = craft_states(mk_cohort(30 * BIN_BARS), LONG_RUN)
        m = run(c, case_params(0, 0, 0), rail=RAIL_FLAT, kind="full")
        delta = sh.counters_delta({})
        self.assertIn("signal_exit_blocked_unprofitable", delta)
        # every in-slice fresh event is entered, skipped (in position) or refused (exhausted)
        self.assertEqual(m["episodes"] + delta.get("reentry_skipped_in_position", 0)
                         + delta.get("entry_refused_exhausted", 0), m["windows_seen"])
        self.assertEqual(delta.get("entry_after_slice_end", 0), 0)


class TestFamilyReaders(unittest.TestCase):
    """The four registered family-level readers (never cull, never PASS-bearing)."""

    def setUp(self):
        reset_counters()
        self.spec = {"data": {"historical_start": "2025-01-01", "historical_end": "2025-01-06"}}

    def test_transition_matrix_stability_triggers_on_an_unstable_second_half(self):
        # 600 completed bins (2400 base bars), L=1, two disjoint halves of 300 bins, four
        # sequences observed >= 10 times in EACH half, and one of them (state 0) flips its
        # bullish share from 0.00 to 0.333 between the halves.
        cycle_a = [0, 6, 0, 6, 3, 9]
        cycle_b = [0, 0, 6, 3, 0, 9]
        states = (cycle_a * 50) + (cycle_b * 50)
        self.assertEqual(len(states), 600)
        c = craft_states(mk_cohort(600 * BIN_BARS), states)
        layer = c.signals_for(case_tuple_of(0, 0, 0))
        spec = {"data": {"historical_start": "2025-01-01", "historical_end": "2025-04-11"}}
        st = layer.stability(*sh.bin_range_of(c, spec["data"]["historical_start"],
                                              spec["data"]["historical_end"]))
        self.assertEqual((st["bin_lo"], st["bin_hi"], st["bin_mid"]), (0, 600, 300))
        self.assertEqual(st["comparable_rows"], 4)
        self.assertTrue(st["evidence_sufficient"])
        # state 3's row flips from an all-bearish outcome set (3 -> 9) in the first half to an
        # all-bullish one (3 -> 0) in the second half: the extreme admissible deviation, read
        # off 50 observations in EACH half.
        self.assertAlmostEqual(st["max_abs_pbull_deviation"], 1.0, places=6)
        out = sh.transition_matrix_stability_check(
            spec, [{"cohort": "SYNTH/1h", "winner": winner_stub(), "metrics": {}}], {},
            {"SYNTH/1h": (c, None)})
        self.assertTrue(out["evaluated"])
        self.assertTrue(out["hit"])
        self.assertTrue(out["cohorts"]["SYNTH/1h"]["deviation_hit"])
        self.assertEqual(out["cohorts"]["SYNTH/1h"]["threshold_pbull_deviation"], 0.25)
        self.assertEqual(out["cohorts"]["SYNTH/1h"]["min_observations_per_half"], 10)

    def test_a_thin_unstable_layer_does_not_trigger_the_deviation_part(self):
        """Anti-vacuity control: a large split-half deviation read off rows that do not carry
        the registered evidence floor (10 observations in EACH half, 3 comparable rows) must
        NOT trigger the reader - that would be a measurement of sparsity, not instability."""
        spec = {"data": {"historical_start": "2025-01-01", "historical_end": "2025-01-06"}}
        states = [0] * 8 + [6] * 7 + [0] + [6] * 14
        c = craft_states(mk_cohort(30 * BIN_BARS), states)
        layer = c.signals_for(case_tuple_of(0, 0, 0))
        st = layer.stability(*sh.bin_range_of(c, spec["data"]["historical_start"],
                                              spec["data"]["historical_end"]))
        self.assertFalse(st["evidence_sufficient"])
        self.assertLess(st["comparable_rows"], 3)
        self.assertIsNone(st["max_abs_pbull_deviation"])
        out = sh.transition_matrix_stability_check(
            spec, [{"cohort": "SYNTH/1h", "winner": winner_stub(), "metrics": {}}], {},
            {"SYNTH/1h": (c, None)})
        self.assertTrue(out["evaluated"])
        self.assertFalse(out["cohorts"]["SYNTH/1h"]["deviation_hit"])

    def test_transition_matrix_stability_stays_quiet_on_a_stable_layer(self):
        c = craft_states(mk_cohort(30 * BIN_BARS), LONG_ONLY)
        out = sh.transition_matrix_stability_check(
            self.spec, [{"cohort": "SYNTH/1h", "winner": winner_stub(), "metrics": {}}], {},
            {"SYNTH/1h": (c, None)})
        self.assertTrue(out["evaluated"])
        self.assertFalse(out["hit"])
        self.assertLess(out["cohorts"]["SYNTH/1h"]["untrained_share"], 0.5)

    def test_exit_asymmetry_reader_uses_the_isolated_full_window_counters(self):
        results = [{"cohort": "SYNTH/1h", "winner": winner_stub(), "metrics": {}}]
        for blocked, expect in ((3, True), (0, False)):
            diag = {"SYNTH/1h": {
                "counter_delta": {"signal_exit_taken": 1,
                                  "signal_exit_blocked_unprofitable": blocked},
                "episode_stats": {"win_rate": 0.5, "mean_hold_bars": 3.0, "episodes": 4}}}
            out = sh.exit_asymmetry_check({}, results, {}, diag)
            self.assertEqual(out["hit"], expect)
            self.assertEqual(out["cohorts"]["SYNTH/1h"]["opposite_signal_evaluations"],
                             1 + blocked)
            self.assertEqual(out["cohorts"]["SYNTH/1h"]["winner_win_rate"], 0.5)

    def test_cross_asset_dispersion_reader_thresholds_at_half(self):
        def results(n_pos):
            return [{"cohort": "C%d" % i, "winner": {"x": 1},
                     "metrics": {"historical": {"net_pnl": 1.0 if i < n_pos else -1.0}}}
                    for i in range(4)]
        self.assertTrue(sh.cross_asset_dispersion_check({}, results(1), {})["hit"])
        out = sh.cross_asset_dispersion_check({}, results(2), {})
        self.assertFalse(out["hit"])
        self.assertEqual(out["positive_cohort_share"], 0.5)
        self.assertEqual(out["cohorts_evaluated"], 4)

    def test_cost_boundary_reader_flags_a_sign_flip(self):
        rows = {"SYNTH/1h": {"full": [row_for("full", 100.0)],
                             "cost_attrition_40bps": [row_for("cost_attrition_40bps", -5.0)]}}
        out = sh.cost_boundary_check({}, [{"cohort": "SYNTH/1h", "winner": winner_stub()}], rows)
        self.assertTrue(out["hit"])
        rows2 = {"SYNTH/1h": {"full": [row_for("full", 100.0)],
                              "cost_attrition_40bps": [row_for("cost_attrition_40bps", 7.0)]}}
        self.assertFalse(sh.cost_boundary_check({}, [{"cohort": "SYNTH/1h",
                                                      "winner": winner_stub()}], rows2)["hit"])


class TestSummarizeIntegration(unittest.TestCase):
    """One end-to-end smoke of the coverage / selector / assertion / report machinery."""

    def setUp(self):
        reset_counters()
        sh.LAYER_TOTALS.clear()
        self.cohort = craft_states(mk_cohort(30 * BIN_BARS), LONG_RUN)
        dca_axes = {"spacing_pct": [0.01, 0.02, 0.03, 0.04], "size_multiplier": [1.0, 1.1],
                    "breakeven_tp_pct": [0.01, 0.02, 0.03], "invalidation_pct": [0.05, 0.1]}
        self.dca_grid = [dict({"base_quote": 1000},
                              **{a: v for a, v in zip(sh.DCA_AXES, cell)})
                         for cell in itertools.product(*[dca_axes[a] for a in sh.DCA_AXES])]
        per_grid = len(sh.CASE_ORDER) * len(self.dca_grid)
        self.spec = {
            "family_id": "synthetic-h", "round_id": "synthetic-h-r1",
            "run_id": "synthetic-h-r1-u1",
            "parameter_domain": {"grid_cases": [
                {f: v for f, v in zip(sh.CASE_FIELDS, c)} for c in sh.CASE_ORDER]},
            "dca_domain": dict(dca_axes, base_quote=1000, grid=self.dca_grid),
            "gates": {"min_episodes_is": 100, "min_episodes_oos": 10,
                      "neighborhood_min_same_sign_fraction": 0.6},
            "costs": {"baseline_slippage_ticks": 1},
            "data": {"start": "2025-01-01", "end": "2025-01-06",
                     "historical_start": "2025-01-01", "historical_end": "2025-01-06",
                     "oos_start": "2025-01-01", "oos_end": "2025-01-06"},
            "expected": {"cohorts": 1, "strategy_cases_per_cohort": len(sh.CASE_ORDER),
                         "dca_configs_per_cohort": len(self.dca_grid),
                         "base_combinations_per_cohort": per_grid,
                         "case_evaluations_per_grid": per_grid,
                         "expected_case_evaluations": per_grid * len(sh.COHORT_GRID_KINDS)}}

    @staticmethod
    def metrics_stub(**over):
        m = {"net_pnl": 10.0, "fees": 1.0, "funding": 0.5, "gross_pnl": 11.5,
             "ending_equity": 30010.0, "windows_seen": 400, "windows_entered": 400,
             "episodes": 400, "tp_hits": 0, "stop_hits": 0, "time_exits": 400,
             "open_at_end": 0, "margin_calls": 0, "halted": False,
             "min_entry_equity": 30000.0, "sharpe": 1.0, "max_dd_usdt": -1.0,
             "max_dd_pct": -0.0001, "max_effective_leverage": 1.0, "capital_utilization": 0.2,
             "bars_in_market": 10, "fills": 2, "turnover_usdt": 2000.0, "days": 5,
             "years": 1.0, "cagr": 0.001, "total_return_pct": 0.0003,
             "annualized_return": 0.0003}
        m.update(over)
        return m

    def test_summarize_smoke_over_the_full_registered_product(self):
        rows = {}
        for kind in sh.COHORT_GRID_KINDS:
            rows[kind] = [sh.record(self.cohort, p, dca, kind, self.metrics_stub())
                          for p in self.spec["parameter_domain"]["grid_cases"]
                          for dca in self.dca_grid]
        layers = [sum(r["episodes"] for k in sh.FULL_WINDOW_GRID_KINDS
                      for r in rows[k])] + [0] * 11
        diag_inputs = {"SYNTH/1h": (self.cohort, mk_series(self.cohort))}
        slice_days = {"SYNTH/1h": {"historical": 5, "oos": 5, "full": 5}}
        summary = sh.summarize(self.spec, rows, layers, diag_inputs, slice_days)
        self.assertTrue(summary["coverage_complete"])
        self.assertEqual(summary["case_evaluations_total"],
                         self.spec["expected"]["expected_case_evaluations"])
        self.assertEqual(summary["cohort_survivor_count"], 1)
        self.assertEqual(summary["disposition"], "SURVIVOR_FOUND")
        a = summary["assertions"]
        for name in ("coverage_complete", "episodes_partition", "pnl_decomposition",
                     "layer0_equals_episodes", "no_reentry_without_a_fresh_signal",
                     "bin_grid_is_aligned_and_complete",
                     "attached_state_was_available_before_bar", "matrix_counts_are_causal",
                     "signal_requires_trained_row",
                     "entry_bar_is_the_next_bar_after_the_signal",
                     "signal_exit_required_profitability",
                     "state_index_covers_the_registered_twelve", "lookahead_control_reported",
                     "selector_deterministic", "bar_grid_is_contiguous",
                     "funding_bar_never_out_of_hold", "daily_series_is_slice_scoped"):
            self.assertIn(name, a)
            self.assertTrue(a[name], "registered assertion %s is false" % name)
        for key in ("coverage", "cohort_results", "cohort_survivors", "cohort_grid_kinds",
                    "transition_matrix_stability", "exit_asymmetry", "cost_boundary",
                    "cross_asset_dispersion", "state_report", "signal_layer",
                    "robustness_diagnostics", "structural_counters",
                    "registered_family_level_falsification_flags", "dca_layer_histogram"):
            self.assertIn(key, summary)
        self.assertEqual(summary["state_report"]["SYNTH/1h"]["bins"], 30)
        self.assertEqual(len(summary["signal_layer"]["SYNTH/1h"]["cases"]), len(sh.CASE_ORDER))
        self.assertEqual(summary["dca_layer_histogram"]["level_00"], layers[0])
        self.assertEqual(set(summary["registered_family_level_falsification_flags"]),
                         {"transition_matrix_stability", "exit_asymmetry", "cost_boundary",
                          "cross_asset_dispersion"})
        self.assertTrue(all(v is False or v is True
                            for v in summary["registered_family_level_falsification_flags"].values()))


class TestRail(unittest.TestCase):

    def setUp(self):
        reset_counters()

    def test_flat_long_pays_entry_and_exit_fees_only(self):
        c = craft_states(mk_cohort(30 * BIN_BARS), LONG_ONLY)
        c.taker_fee = 0.0005
        m = run(c, case_params(0, 0, 0), rail=RAIL_FLAT)
        qty = RAIL_FLAT["base_quote"] * LEV / P0
        self.assertEqual(m["fills"], 2 * m["episodes"])
        self.assertAlmostEqual(m["fees"], 2 * m["episodes"] * qty * P0 * 0.0005, places=6)
        self.assertAlmostEqual(m["gross_pnl"], 0.0, places=6)
        self.assertAlmostEqual(m["net_pnl"], -m["fees"], places=6)
        self.assertTrue(sh.pnl_decomposition_ok(m))

    def test_long_tp_fires_on_the_high_and_keeps_the_identity(self):
        c = craft_states(mk_cohort(30 * BIN_BARS), LONG_ONLY)
        c.taker_fee = 0.0005
        highs = list(c.high)
        for i in range(25, c.n):
            highs[i] = 101.5
        c.high = np.array(highs, dtype=np.float64)
        m = run(c, case_params(0, 0, 0), rail=RAIL_TIGHT)
        self.assertEqual(m["tp_hits"], 1)
        self.assertEqual(m["episodes"], 1)
        qty = RAIL_TIGHT["base_quote"] * LEV / P0
        self.assertAlmostEqual(m["gross_pnl"], qty * (P0 * 1.01 - P0), places=6)
        self.assertAlmostEqual(m["fees"], (qty * P0 + qty * P0 * 1.01) * 0.0005, places=6)
        self.assertTrue(sh.pnl_decomposition_ok(m))

    def test_one_ladder_scale_in_is_hand_computed(self):
        c = craft_states(mk_cohort(30 * BIN_BARS), LONG_ONLY)
        c.taker_fee = 0.0005
        c.low[25] = 97.5                    # one bar reaches level 1 = P0 x (1 - 0.02) only
        m = run(c, case_params(0, 0, 0), rail=RAIL_LADDER)
        q1 = RAIL_LADDER["base_quote"] * LEV / P0
        q2 = RAIL_LADDER["base_quote"] * RAIL_LADDER["size_multiplier"] * LEV / 98.0
        qty = q1 + q2
        cost = q1 * P0 + q2 * 98.0
        self.assertEqual(m["fills"], 3)                     # entry + one add + slice-end exit
        self.assertAlmostEqual(m["gross_pnl"], qty * P0 - cost, places=6)
        self.assertAlmostEqual(m["fees"],
                               (q1 * P0 + q2 * 98.0 + qty * P0) * 0.0005, places=6)
        self.assertAlmostEqual(m["net_pnl"], m["gross_pnl"] - m["fees"] - m["funding"], places=6)
        self.assertTrue(sh.pnl_decomposition_ok(m))
        self.assertEqual(sh.counters_total("ladder_cap_reached"), 0)
        self.assertEqual(m["layers"][1], 1)

    def test_resting_stop_fills_at_the_stop_and_counts_the_hit(self):
        c = craft_states(mk_cohort(30 * BIN_BARS), LONG_ONLY)
        c.taker_fee = 0.0
        c.low[25] = 94.0                    # below the resting stop at P0 x (1 - 0.05)
        m = run(c, case_params(0, 0, 0), rail=RAIL_STOP)
        qty = RAIL_STOP["base_quote"] * LEV / P0
        self.assertEqual(m["stop_hits"], 1)
        self.assertEqual(m["fills"], 2)
        self.assertAlmostEqual(m["gross_pnl"], qty * (95.0 - P0), places=6)
        self.assertTrue(sh.pnl_decomposition_ok(m))
        self.assertEqual(sh.counters_total("stop_at_ladder_boundary"), 0)

    def test_short_mirror_profits_when_the_price_falls(self):
        c = craft_states(mk_cohort(30 * BIN_BARS), SHORT_ONLY)
        c.taker_fee = 0.0005
        closes = [P0] * c.n
        for i in range(26, c.n):
            closes[i] = 99.0
        c.close = np.array(closes, dtype=np.float64)
        c.high = np.array([max(a, b) for a, b in zip(c.open, c.close)], dtype=np.float64)
        c.low = np.array([min(a, b) for a, b in zip(c.open, c.close)], dtype=np.float64)
        m = run(c, case_params(0, 0, 0), rail=RAIL_FLAT)
        qty = RAIL_FLAT["base_quote"] * LEV / P0
        self.assertEqual(m["episodes"], 1)
        self.assertGreater(m["gross_pnl"], 0.0)
        self.assertAlmostEqual(m["gross_pnl"], qty * (P0 - 99.0), places=6)
        self.assertTrue(sh.pnl_decomposition_ok(m))

    def test_slippage_is_adverse_on_every_scale_in_and_every_exit_on_both_sides(self):
        tick = 0.01
        # LONG: one scale-in at level 1 then the slice-end flatten.  EVERY fill is adverse
        # (the card's registered convention): the entry pays UP, the add buys above its level
        # and the flatten sells below the final close.
        c = craft_states(mk_cohort(30 * BIN_BARS), LONG_ONLY)
        c.taker_fee = 0.0005
        c.price_increment = tick
        c.low[25] = 97.5
        m = run(c, case_params(0, 0, 0), rail=RAIL_LADDER, slip=2)
        p_entry = P0 + 2 * tick                                   # buying pays up
        p_add = p_entry * (1.0 - RAIL_LADDER["spacing_d0"]) + 2 * tick
        p_exit = P0 - 2 * tick
        q1 = RAIL_LADDER["base_quote"] * LEV / p_entry
        q2 = RAIL_LADDER["base_quote"] * RAIL_LADDER["size_multiplier"] * LEV / p_add
        qty = q1 + q2
        self.assertEqual(m["fills"], 3)
        self.assertAlmostEqual(m["gross_pnl"], qty * p_exit - (q1 * p_entry + q2 * p_add),
                               places=6)
        self.assertAlmostEqual(m["fees"], (q1 * p_entry + q2 * p_add + qty * p_exit) * 0.0005,
                               places=6)
        # SHORT: the mirror rail fills strictly WORSE on both the add and the exit
        c2 = craft_states(mk_cohort(30 * BIN_BARS), SHORT_ONLY)
        c2.taker_fee = 0.0005
        c2.price_increment = tick
        c2.high[25] = 102.5
        m2 = run(c2, case_params(0, 0, 0), rail=RAIL_LADDER, slip=2)
        p2_entry = P0 - 2 * tick                                  # selling sells lower
        p2_add = p2_entry * (1.0 + RAIL_LADDER["spacing_d0"]) - 2 * tick
        p2_exit = P0 + 2 * tick
        w1 = RAIL_LADDER["base_quote"] * LEV / p2_entry
        w2 = RAIL_LADDER["base_quote"] * RAIL_LADDER["size_multiplier"] * LEV / p2_add
        wqty = w1 + w2
        self.assertEqual(m2["fills"], 3)
        self.assertAlmostEqual(m2["gross_pnl"], (w1 * p2_entry + w2 * p2_add) - wqty * p2_exit,
                               places=6)
        self.assertGreater(p2_add, p2_entry)      # the short scale-in sells below its own level
        self.assertLess(p2_add, p2_entry * (1.0 + RAIL_LADDER["spacing_d0"]))
        self.assertGreater(p2_exit, P0)           # the short flatten buys above the close
        self.assertGreater(p_add, p_entry * (1.0 - RAIL_LADDER["spacing_d0"]))
        self.assertLess(p_add, p_entry)           # the long scale-in buys above its own level
        self.assertLess(p_exit, P0)               # the long flatten sells below the close

    def test_adverse_slippage_is_pinned_on_the_entry_stop_and_flatten_legs(self):
        """The card registers 1 tick ADVERSE per fill (by instrument price_increment): the sign
        is pinned here on the legs the copied D/G spelling had as a one-tick CREDIT (entry,
        resting stop, capital-exhaustion exit), both directions."""
        tick = 0.01
        # LONG: entry pays up, the resting stop sells lower than its own trigger.
        c = craft_states(mk_cohort(30 * BIN_BARS), LONG_ONLY)
        c.taker_fee = 0.0
        c.price_increment = tick
        c.low[25] = 94.0                          # through the resting stop at P0 x (1 - 0.05)
        m = run(c, case_params(0, 0, 0), rail=RAIL_STOP, slip=2)
        p_entry = P0 + 2 * tick
        stop_trigger = p_entry * (1.0 - RAIL_STOP["invalidation"])   # avg cost x (1 - inval)
        p_stop = stop_trigger - 2 * tick
        qty = RAIL_STOP["base_quote"] * LEV / p_entry
        self.assertEqual(m["stop_hits"], 1)
        self.assertGreater(p_entry, P0)           # buying pays up
        self.assertLess(p_stop, stop_trigger)     # selling out pays down
        self.assertAlmostEqual(m["gross_pnl"], qty * (p_stop - p_entry), places=6)
        # SHORT: entry sells lower, the slice-end flatten buys higher.
        c2 = craft_states(mk_cohort(30 * BIN_BARS), SHORT_ONLY)
        c2.taker_fee = 0.0
        c2.price_increment = tick
        m2 = run(c2, case_params(0, 0, 0), rail=RAIL_FLAT, slip=2)
        p2_entry = P0 - 2 * tick
        p2_exit = P0 + 2 * tick
        wqty = RAIL_FLAT["base_quote"] * LEV / p2_entry
        self.assertEqual(m2["episodes"], 1)
        self.assertEqual(m2["open_at_end"], 1)
        self.assertLess(p2_entry, P0)             # selling sells lower
        self.assertGreater(p2_exit, P0)           # buying back pays up
        self.assertAlmostEqual(m2["gross_pnl"], wqty * (p2_entry - p2_exit), places=6)

    def test_fee_2x_moves_net_and_never_gross(self):
        c = craft_states(mk_cohort(30 * BIN_BARS), LONG_ONLY)
        c.taker_fee = 0.0005
        base = run(c, case_params(0, 0, 0), rail=RAIL_FLAT)
        stressed = run(c, case_params(0, 0, 0), rail=RAIL_FLAT, stress={"fee_mult": 2.0},
                       kind="fee_2x")
        self.assertAlmostEqual(base["gross_pnl"], stressed["gross_pnl"], places=6)
        self.assertGreater(stressed["fees"], base["fees"])
        self.assertLess(stressed["net_pnl"], base["net_pnl"])

    def test_fee_is_charged_at_each_fill_not_once_per_episode(self):
        c = craft_states(mk_cohort(30 * BIN_BARS), LONG_ONLY)
        c.taker_fee = 0.0005
        c.low[25] = 97.5
        m = run(c, case_params(0, 0, 0), rail=RAIL_LADDER)
        q1 = RAIL_LADDER["base_quote"] * LEV / P0
        q2 = RAIL_LADDER["base_quote"] * RAIL_LADDER["size_multiplier"] * LEV / 98.0
        self.assertEqual(m["fills"], 3)
        self.assertAlmostEqual(m["fees"], (q1 * P0 + q2 * 98.0 + (q1 + q2) * P0) * 0.0005,
                               places=6)

    def test_funding_inside_the_hold_is_charged_and_outside_is_not(self):
        c = craft_states(mk_cohort(30 * BIN_BARS), LONG_ONLY)
        c.taker_fee = 0.0
        entry_ms = int(c.open_time_ms[25])
        before = entry_ms - 5 * 3600000
        inside = entry_ms + 5 * 3600000
        series = mk_series(c, [(before, 0.0001), (inside, 0.0001)])
        m = run(c, case_params(0, 0, 0), rail=RAIL_FLAT, series=series)
        qty = RAIL_FLAT["base_quote"] * LEV / P0
        self.assertAlmostEqual(m["funding"], qty * P0 * 0.0001, places=6)
        self.assertEqual(sh.counters_total("funding_bar_out_of_hold"), 0)
        self.assertTrue(sh.pnl_decomposition_ok(m))

    def test_the_funding_hold_guard_can_fire_on_an_early_settlement(self):
        c = craft_states(mk_cohort(30 * BIN_BARS), LONG_ONLY)
        c.taker_fee = 0.0
        entry_ms = int(c.open_time_ms[25])
        # 500 ms BEFORE the entry instant: inside the registered +-1 s jitter tolerance (so it
        # is charged at the entry bar) but its CONTAINMENT bar is the one that ends at the
        # entry, i.e. outside the hold - the guard the round-spec asserts must stay silent
        series = mk_series(c, [(entry_ms - 500, 0.0001)])
        m = run(c, case_params(0, 0, 0), rail=RAIL_FLAT, series=series)
        self.assertEqual(sh.counters_total("funding_bar_out_of_hold"), 1)
        qty = RAIL_FLAT["base_quote"] * LEV / P0
        self.assertAlmostEqual(m["funding"], qty * P0 * 0.0001, places=6)
        self.assertTrue(sh.pnl_decomposition_ok(m))

    def test_no_funding_stress_is_cost_free(self):
        c = craft_states(mk_cohort(30 * BIN_BARS), LONG_ONLY)
        c.taker_fee = 0.0005
        entry_ms = int(c.open_time_ms[25])
        series = mk_series(c, [(entry_ms + 5 * 3600000, 0.0001)])
        base = run(c, case_params(0, 0, 0), rail=RAIL_FLAT, series=series)
        free = run(c, case_params(0, 0, 0), rail=RAIL_FLAT, series=series,
                   stress={"no_funding": True}, kind="no_funding")
        self.assertGreater(abs(base["funding"]), 0.0)
        self.assertEqual(free["funding"], 0.0)
        self.assertGreater(free["net_pnl"], base["net_pnl"])

    def test_layer_histogram_counts_only_grid_cells(self):
        c = craft_states(mk_cohort(30 * BIN_BARS), LONG_ONLY)
        c.taker_fee = 0.0005
        sh.LAYER_TOTALS.clear()
        run(c, case_params(0, 0, 0), rail=RAIL_FLAT, kind="full", count_layers=True)
        after_grid = list(sh.LAYER_TOTALS["full"])
        self.assertEqual(after_grid[0], 1)
        run(c, case_params(0, 0, 0), rail=RAIL_FLAT, kind="full", count_layers=False)
        self.assertEqual(list(sh.LAYER_TOTALS["full"]), after_grid)
        run(c, case_params(0, 0, 0), rail=RAIL_FLAT, kind="winner_diag", count_layers=True)
        self.assertEqual(list(sh.LAYER_TOTALS["full"]), after_grid)

    def test_daily_equity_series_is_slice_scoped(self):
        c = craft_states(mk_cohort(30 * BIN_BARS), LONG_ONLY)
        c.taker_fee = 0.0005
        m = run(c, case_params(0, 0, 0), rail=RAIL_FLAT, diag=True)
        self.assertEqual(m["days"], 5)                    # 120 h = 5 UTC days
        self.assertEqual(len(m["diagnostics"]["daily_equity"]), 5)
        self.assertEqual(m["diagnostics"]["episode_stats"]["episodes"], 1)
        self.assertEqual(m["diagnostics"]["signal_case"], "thr1__L1__p0.5")


class TestGates(unittest.TestCase):

    def setUp(self):
        reset_counters()

    def spec_stub(self):
        """The minimum spec surface the selector/tie-break needs (registered axes + floor)."""
        return {"family_id": "synthetic", "round_id": "synthetic-r1",
                "run_id": "synthetic-r1-u1",
                "parameter_domain": {"grid_cases": [
                    {f: v for f, v in zip(sh.CASE_FIELDS, case)} for case in sh.CASE_ORDER]},
                "dca_domain": {"spacing_pct": [0.01, 0.02, 0.03, 0.04],
                               "size_multiplier": [1.0, 1.1],
                               "breakeven_tp_pct": [0.01, 0.02, 0.03],
                               "invalidation_pct": [0.05, 0.1]},
                "gates": {"min_episodes_is": 100, "min_episodes_oos": 10,
                          "neighborhood_min_same_sign_fraction": 0.6}}

    def test_registered_contract_block_matches_the_preregistration(self):
        self.assertEqual(len(sh.CASE_ORDER), 27)
        self.assertEqual(len(sh.CASE_NAMES), 27)
        self.assertEqual(sh.CASE_NAMES[0], "thr1__L1__p0.5")
        self.assertEqual(sh.CASE_NAMES[-1], "thr2__L3__p0.7")
        self.assertEqual(sh.CASE_FIELDS[0], "thr_1_0")
        self.assertEqual(sh.ENGINE_VERSION, "h-v1-engine-1.0.0")
        self.assertEqual((sh.BIN_HOURS, sh.MS_PER_BIN, sh.BASE_BARS_PER_BIN), (4, 14400000, 4))
        self.assertEqual(sh.STATE_COUNT, 12)
        self.assertEqual(list(sh.BULL_STATES), [0, 1, 2, 3, 4, 5])
        self.assertEqual(list(sh.BEAR_STATES), [6, 7, 8, 9, 10, 11])
        self.assertEqual((sh.ATR_PERIOD, sh.VOLUME_SMA_PERIOD, sh.MIN_ROW_OBSERVATIONS),
                         (14, 24, 5))
        self.assertEqual(len(sh.COHORT_GRID_KINDS), 10)
        self.assertEqual(sh.case_thr(sh.case_fields(2, 1, 0)), 2)
        self.assertEqual(sh.case_seq(sh.case_fields(2, 1, 0)), 1)
        self.assertEqual(sh.case_minp(sh.case_fields(2, 1, 0)), 0)
        for name in ("entry_after_slice_end", "slice_end_flatten",
                     "signal_exit_blocked_unprofitable", "signal_exit_taken",
                     "signal_exit_deferred_to_slice_end", "untrained_row_no_signal",
                     "same_sign_signal_ignored", "state_undefined_bar",
                     "attached_state_before_availability", "entry_refused_exhausted",
                     "ladder_cap_reached", "stop_at_ladder_boundary"):
            self.assertIn(name, sh.COUNTER_NAMES)

    def test_disposition_mapping_is_the_v140_table(self):
        self.assertEqual(sh.family_disposition([], True)["verdict_recommendation"], "REJECT")
        self.assertEqual(sh.family_disposition([{"x": 1}], True)["verdict_recommendation"], "PASS")
        self.assertEqual(sh.family_disposition([{"x": 1}, {"x": 2}], True)["disposition"],
                         "MULTIPLE_SURVIVORS")
        self.assertEqual(sh.family_disposition([{"x": 1}], False)["disposition"],
                         "TECHNICAL_INCOMPLETE")
        self.assertFalse(sh.family_disposition([], True)["performance_claimable_recommendation"])
        self.assertFalse(sh.family_disposition([{"x": 1}], False)
                         ["performance_claimable_recommendation"])

    def test_selector_ranks_sharpe_first_and_is_deterministic(self):
        rows = []
        for i, case in enumerate(sh.CASE_ORDER):
            rows.append({"symbol": "SYNTH", "timeframe": "1h", "window_kind": "historical",
                         "window_case": sh.CASE_NAMES[i],
                         **{f: v for f, v in zip(sh.CASE_FIELDS, case)},
                         "spacing_pct": 0.02, "size_multiplier": 1.1, "breakeven_tp_pct": 0.01,
                         "invalidation_pct": 0.05, "net_pnl": 10.0 + i, "sharpe": 1.0,
                         "episodes": 400})
        rows[5]["sharpe"] = 3.0
        winner, reason = sh.select_cohort_winner(rows, self.spec_stub())
        self.assertEqual(reason, "selected")
        self.assertEqual(sh.case_index(sh.case_tuple(winner)), 5)
        again, _ = sh.select_cohort_winner(list(reversed(rows)), self.spec_stub())
        self.assertEqual(sh.cell_key(again), sh.cell_key(winner))

    def test_selector_refuses_oos_rows(self):
        rows = [{"symbol": "SYNTH", "timeframe": "1h", "window_kind": "oos",
                 **{f: v for f, v in zip(sh.CASE_FIELDS, sh.CASE_ORDER[0])},
                 "spacing_pct": 0.02, "size_multiplier": 1.1, "breakeven_tp_pct": 0.01,
                 "invalidation_pct": 0.05, "net_pnl": 100.0, "sharpe": 9.0, "episodes": 400}]
        with self.assertRaises(ValueError):
            sh.select_cohort_winner(rows, self.spec_stub())

    def test_neighbourhood_uses_the_historical_joint_space_only(self):
        spec = self.spec_stub()
        rows = []
        for i, case in enumerate(sh.CASE_ORDER):
            for sp in spec["dca_domain"]["spacing_pct"]:
                for sm in spec["dca_domain"]["size_multiplier"]:
                    for tp in spec["dca_domain"]["breakeven_tp_pct"]:
                        for inv in spec["dca_domain"]["invalidation_pct"]:
                            rows.append({"symbol": "SYNTH", "timeframe": "1h",
                                         "window_kind": "historical",
                                         **{f: v for f, v in zip(sh.CASE_FIELDS, case)},
                                         "spacing_pct": sp, "size_multiplier": sm,
                                         "breakeven_tp_pct": tp, "invalidation_pct": inv,
                                         "net_pnl": 5.0, "sharpe": 1.0, "episodes": 400})
        nb = sh.cohort_neighbourhood(rows, rows[0], spec)
        self.assertTrue(nb["passed"])
        self.assertEqual(nb["agreeing"], nb["neighbours"])
        oos = [dict(r, window_kind="oos") for r in rows]
        with self.assertRaises(ValueError):
            sh.cohort_neighbourhood(oos, oos[0], spec)


if __name__ == "__main__":
    unittest.main(verbosity=2)
