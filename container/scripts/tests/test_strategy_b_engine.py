#!/usr/bin/env python3
"""Executable check for the Strategy B v2 execution engine (mirrored DCA rail + walk-forward).

Runs inside the qlib container:
    SB_ENGINE_PATH=/scripts/30_strategy_b_run.py /opt/venv/bin/python /scripts/tests/test_strategy_b_engine.py

Drives `simulate()` / `select_train_pairs()` / `wf_case()` / `run_cohort()` with crafted bars
whose fills are known by hand, so a regression in the mirrored ladder walk, the resting
invalidation, the (short) take profit, the per-fill fee accounting, the independent gross
accumulator or the walk-forward case semantics fails loudly instead of silently changing the
science.  stdlib unittest + numpy only; no market data, no qlib, no container state.
"""
import csv
import importlib.util
import json
import os
import random
import shutil
import tempfile
import unittest
from unittest import mock

import numpy as np

ENGINE = os.environ.get("SB_ENGINE_PATH", "/scripts/30_strategy_b_run.py")
_spec = importlib.util.spec_from_file_location("sb_engine", ENGINE)
sb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sb)

RAIL = {"base_quote": 1000.0, "size_multiplier": 1.1, "spacing_d0": 0.02,
        "tp": 0.012, "invalidation": 0.05}
# the registered DCA configuration the RAIL above is built from (engine API takes the
# registered config, `rail_for()` builds the rail)
DCA = {"base_quote": 1000.0, "spacing_pct": 0.02, "size_multiplier": 1.1,
       "breakeven_tp_pct": 0.012, "invalidation_pct": 0.05}
# a rail whose first ladder level sits BELOW the resting invalidation: the ladder can never
# absorb the move, which is the only shape in which a gap-through-stop is observable
RAIL_WIDE = {"base_quote": 1000.0, "size_multiplier": 1.1, "spacing_d0": 0.10,
             "tp": 0.012, "invalidation": 0.05}
LEV = 10.0
COST0 = 10000.0            # notional of the first tranche at the entry price


class FakeCohort:
    """Bar container with the same public surface the engine reads from a real cohort."""

    def __init__(self, rows, tick=0.0, taf=0.0, funding=None, margin_maint=0.1,
                 symbol="SYNTH", timeframe="5m", day_bars=1, start_ms=1640995200000,
                 bar_ms=300000):
        self.symbol = symbol
        self.timeframe = timeframe
        self.open = np.array([r[0] for r in rows], dtype=np.float64)
        self.high = np.array([r[1] for r in rows], dtype=np.float64)
        self.low = np.array([r[2] for r in rows], dtype=np.float64)
        self.close = np.array([r[3] for r in rows], dtype=np.float64)
        self.n = len(rows)
        self.open_time_ms = np.array([start_ms + i * bar_ms for i in range(self.n)],
                                     dtype=np.int64)
        self.rebuild_days()
        self.funding = np.zeros(self.n) if funding is None else np.array(funding, dtype=np.float64)
        self.price_increment = tick
        self.taker_fee = taf
        self.leverage = LEV
        self.margin_maint = margin_maint

    def rebuild_days(self):
        """Re-derive the day structure after `open_time_ms` has been (re)assigned."""
        day_id = self.open_time_ms // sb.MS_PER_DAY
        uniq, inv = np.unique(day_id, return_inverse=True)
        self.day_index = inv.astype(np.int64)
        self.n_days = int(len(uniq))
        self.day_end = set(int(i) for i in np.flatnonzero(
            np.concatenate([day_id[1:] != day_id[:-1], [True]])))
        self.day_start = np.append(
            np.flatnonzero(np.concatenate([[True], day_id[1:] != day_id[:-1]])),
            self.n).astype(np.int64)

    def slice(self, start_date, end_date):
        lo = sb.utc_ms(start_date)
        hi = sb.utc_ms(end_date) + sb.MS_PER_DAY - 1
        return (int(np.searchsorted(self.open_time_ms, lo, side="left")),
                int(np.searchsorted(self.open_time_ms, hi, side="right")))


def dirs(*vals):
    return np.array(vals, dtype=np.int8)


def run(cohort, direction, slip=0.0, fee_mult=1.0, funding_mult=1.0, use_funding=True,
        base_equity=sb.START_EQUITY, rail=RAIL):
    return sb.simulate(cohort, (0, cohort.n), direction, rail, slip, fee_mult,
                       funding_mult, use_funding, base_equity)


class TestSignal(unittest.TestCase):
    """1-bar-lagged EMAs, next-bar execution, ties carry the previous direction."""

    def test_lag_and_execution(self):
        fast = np.array([1.0, 2.0, 3.0, 3.0])
        slow = np.array([2.0, 2.0, 2.0, 4.0])
        d = sb.direction_array(fast, slow, 0)
        # sign(fast-slow) = [-1, tie(-1), +1, -1]; exec[u] = sign(u-2)
        self.assertEqual(list(d), [0, 0, -1, -1])
        self.assertEqual(list(sb.direction_array(fast, slow, 1)), [0, 0, 0, -1])

    def test_ties_carry_the_previous_direction(self):
        # all-tie input: every comparison is undefined, so the carried direction is +1 (the
        # registered rule: "ties carry the previous direction, which starts at +1") and the
        # execution array only starts two bars later
        fast = np.array([1.0, 1.0, 1.0])
        slow = np.array([1.0, 1.0, 1.0])
        self.assertEqual(list(sb.direction_array(fast, slow, 0)), [0, 0, 1])
        # fast < slow on the first defined bar: the previous direction is carried until the
        # first defined comparison, and the execution array is shifted by the registered lag
        fast2 = np.array([1.0, 1.0, 2.0, 2.0])
        slow2 = np.array([3.0, 3.0, 2.0, 1.0])
        self.assertEqual(list(sb.direction_array(fast2, slow2, 0)), [0, 0, -1, -1])


class TestLongRail(unittest.TestCase):
    """Long leg = the audited Strategy A rail semantics."""

    def test_single_tp_exact_fees_and_gross(self):
        rows = [(100, 100, 100, 100), (100, 100, 100, 100), (100, 100.2, 100, 100.2),
                (100.2, 102.5, 100.0, 101.0), (101, 101, 101, 101)]
        c = FakeCohort(rows, taf=0.0005)
        m = run(c, dirs(0, 0, 1, 1, 1))
        qty = COST0 / 100.2
        tp = 100.2 * 1.012
        expect_gross = qty * tp - COST0
        expect_fees = COST0 * 0.0005 + qty * tp * 0.0005
        self.assertEqual(m["episodes"], 1)
        self.assertEqual(m["episodes_long"], 1)
        self.assertEqual(m["episodes_short"], 0)
        self.assertEqual(m["tp_hits"], 1)
        self.assertAlmostEqual(m["gross_pnl"], expect_gross, places=9)
        self.assertAlmostEqual(m["fees"], expect_fees, places=9)
        self.assertAlmostEqual(m["net_pnl"], expect_gross - expect_fees - m["funding"], places=9)
        self.assertAlmostEqual(m["ending_equity"], sb.START_EQUITY + m["net_pnl"], places=9)
        self.assertTrue(sb.pnl_decomposition_ok(m))
        self.assertEqual(m["layers"][0], 1)
        self.assertEqual(m["layers"][1:], [0] * 11)

    def test_ladder_add_at_level1_only(self):
        # entry at 100 -> level 1 = 100*0.98 = 98; the bar only reaches level 1 (level 2 = 96)
        rows = [(100, 100, 100, 100), (100, 100, 100, 100), (100, 100, 100, 100),
                (100, 100, 98.0, 99.0), (99, 99.5, 98.5, 99.0)]
        c = FakeCohort(rows, taf=0.0)
        m = run(c, dirs(0, 0, 1, 1, 1))
        q1 = COST0 * 1.1 / 98.0
        qty = COST0 / 100.0 + q1
        cost = COST0 * 2.1
        self.assertEqual(m["layers"][1], 1)
        self.assertEqual(m["layers"][2:], [0] * 10)
        self.assertEqual(m["open_at_end"], 1)          # still open, force-flattened at the end
        self.assertAlmostEqual(m["gross_pnl"], qty * 99.0 - cost, places=9)

    def test_level_touched_twice_fills_twice(self):
        """Audited rail parity: the walk restarts at level 1 on every bar, so a level that is
        touched again really fills again at that level price (identical to Strategy A v2)."""
        rows = [(100, 100, 100, 100), (100, 100, 100, 100), (100, 100, 100, 100),
                (100, 100, 98.0, 99.0), (99, 99.5, 98.0, 99.0)]
        c = FakeCohort(rows, taf=0.0)
        m = run(c, dirs(0, 0, 1, 1, 1))
        self.assertEqual(m["layers"][1], 2)

    def test_resting_stop_above_the_ladder(self):
        # invalidation 1% sits ABOVE level 1 (2% away): the resting stop is the first trigger
        rail = dict(RAIL, invalidation=0.01)
        rows = [(100, 100, 100, 100), (100, 100, 100, 100), (100, 100, 100, 100),
                (100, 100, 98.5, 99.0), (99.0, 99.4, 98.9, 99.2)]
        c = FakeCohort(rows, taf=0.0)
        m = run(c, dirs(0, 0, 1, 1, 1), rail=rail)
        self.assertEqual(m["stop_hits"], 1)
        self.assertEqual(m["layers"][1:], [0] * 11)
        # the stop fills at the stop price when the bar trades through it (open above the stop)
        self.assertAlmostEqual(m["gross_pnl"], (COST0 / 100.0) * 99.0 - COST0, places=9)

    def test_gap_through_stop_fills_at_open(self):
        # first ladder level (90) sits BELOW the resting stop (95): the stop is the trigger and
        # the bar gaps through it, so the fill is the OPEN, never the stop price
        rows = [(100, 100, 100, 100), (100, 100, 100, 100), (100, 100, 100, 100),
                (90, 90, 90, 90), (90, 90, 90, 90)]
        c = FakeCohort(rows, taf=0.0)
        m = run(c, dirs(0, 0, 1, 1, 1), rail=RAIL_WIDE)
        self.assertEqual(m["stop_hits"], 1)
        self.assertEqual(m["layers"][1:], [0] * 11)
        self.assertAlmostEqual(m["gross_pnl"], (COST0 / 100.0) * 90.0 - COST0, places=9)


class TestShortRail(unittest.TestCase):
    """Short leg = the registered mirror of the same rail."""

    def test_short_tp_exact(self):
        rows = [(100, 100, 100, 100), (100, 100, 100, 100), (100, 100, 100, 100),
                (100, 100, 98.0, 99.0), (98, 99, 98, 98)]
        c = FakeCohort(rows, taf=0.0005)
        m = run(c, dirs(0, 0, -1, -1, -1))
        qty = COST0 / 100.0
        tp = 100.0 * (1.0 - 0.012)
        expect_gross = COST0 - qty * tp
        expect_fees = COST0 * 0.0005 + qty * tp * 0.0005
        self.assertEqual(m["episodes"], 1)
        self.assertEqual(m["episodes_short"], 1)
        self.assertEqual(m["tp_hits"], 1)
        self.assertAlmostEqual(m["gross_pnl"], expect_gross, places=9)
        self.assertAlmostEqual(m["net_pnl"], expect_gross - expect_fees, places=9)
        self.assertAlmostEqual(m["ending_equity"], sb.START_EQUITY + m["net_pnl"], places=9)

    def test_short_ladder_add_uses_upper_levels(self):
        # level_1 for a short = 100 * 1.02 = 102.0, reached by the bar HIGH (walked first)
        rows = [(100, 100, 100, 100), (100, 100, 100, 100), (100, 100, 100, 100),
                (100, 102.5, 100, 102.0), (101.5, 101.5, 99.0, 100.0), (100, 100, 100, 100)]
        c = FakeCohort(rows, taf=0.0)
        m = run(c, dirs(0, 0, -1, -1, -1, -1))
        q1 = COST0 * 1.1 / 102.0
        qty = COST0 / 100.0 + q1
        cost = COST0 * 2.1
        avg = cost / qty
        tp = avg * (1.0 - 0.012)
        self.assertEqual(m["episodes"], 1)
        self.assertEqual(m["layers"][1], 1)
        self.assertEqual(m["tp_hits"], 1)
        self.assertAlmostEqual(m["gross_pnl"], cost - qty * tp, places=9)

    def test_short_stop_gap_fills_at_max_of_stop_and_open(self):
        rows = [(100, 100, 100, 100), (100, 100, 100, 100), (100, 100, 100, 100),
                (108, 108, 108, 108), (108, 108, 108, 108)]
        c = FakeCohort(rows, taf=0.0)
        m = run(c, dirs(0, 0, -1, -1, -1), rail=RAIL_WIDE)
        stop = 100.0 * 1.05
        self.assertEqual(m["stop_hits"], 1)
        self.assertEqual(m["layers"][1:], [0] * 11)
        # max(stop price, bar open) = 108 (the open), never the 105 stop price
        self.assertAlmostEqual(m["gross_pnl"], COST0 - (COST0 / 100.0) * 108.0, places=9)
        self.assertLess(m["gross_pnl"], COST0 - (COST0 / 100.0) * stop)

    def test_short_receives_funding_when_rate_positive(self):
        """A short pays a negative rate / receives a positive rate (the engine sign rule)."""
        rows = [(100, 100, 100, 100), (100, 100, 100, 100), (100, 100, 100, 100),
                (100, 100, 99, 99.5), (99.5, 99.5, 99.5, 99.5)]
        funding = np.zeros(5)
        funding[3] = 0.001                      # charged on the bar that closes at 99.5
        c = FakeCohort(rows, taf=0.0, funding=funding)
        m = run(c, dirs(0, 0, -1, -1, -1))
        self.assertLess(m["funding"], 0.0)      # a negative funding cost = a credit
        self.assertGreater(m["net_pnl"], m["gross_pnl"])
        # a long on the same bars pays the same rate
        rows_long = [(100, 100, 100, 100), (100, 100, 100, 100), (100, 100, 100, 100),
                     (100, 101, 100, 100.5), (100.5, 100.5, 100.5, 100.5)]
        c2 = FakeCohort(rows_long, taf=0.0, funding=funding)
        m2 = run(c2, dirs(0, 0, 1, 1, 1))
        self.assertGreater(m2["funding"], 0.0)
        self.assertLess(m2["net_pnl"], m2["gross_pnl"])


class TestDirectionChangeAndFlatten(unittest.TestCase):

    def test_flip_is_flatten_then_reentry(self):
        rows = [(100, 100, 100, 100), (100, 100, 100, 100), (100, 100, 100, 100),
                (100, 100, 100, 100), (100, 100, 100, 100)]
        c = FakeCohort(rows, taf=0.0)
        m = run(c, dirs(0, 0, 1, -1, -1))
        self.assertEqual(m["episodes"], 2)
        self.assertEqual(m["episodes_long"], 1)
        self.assertEqual(m["episodes_short"], 1)
        self.assertEqual(m["flips"], 1)
        self.assertEqual(m["tp_hits"] + m["stop_hits"], 0)

    def test_slice_is_force_flattened(self):
        rows = [(100, 100, 100, 100), (100, 100, 100, 100), (100, 100, 100, 100),
                (100, 100.5, 100, 100.5)]
        c = FakeCohort(rows, taf=0.0)
        m = run(c, dirs(0, 0, 1, 1))
        self.assertEqual(m["open_at_end"], 1)
        self.assertEqual(m["episodes"], 1)
        self.assertEqual(m["tp_hits"], 0)
        self.assertAlmostEqual(m["gross_pnl"], (COST0 / 100.0) * 100.5 - COST0, places=9)


class TestAccountingNegativeControls(unittest.TestCase):
    """The two independent ledgers must be able to disagree (contract 7.2 v1.3.1/v1.3.2)."""

    ROWS = [(100, 100, 100, 100), (100, 100, 100, 100), (100, 100.2, 100, 100.2),
            (100.2, 102.5, 100.0, 101.0), (101, 101, 101, 101)]

    def test_fee_pressure_track_is_not_a_no_op(self):
        c = FakeCohort(self.ROWS, taf=0.0005)
        base = run(c, dirs(0, 0, 1, 1, 1))
        double = run(c, dirs(0, 0, 1, 1, 1), fee_mult=2.0)
        self.assertLess(double["net_pnl"], base["net_pnl"])
        self.assertAlmostEqual(double["fees"], 2.0 * base["fees"], places=9)
        # the fee ledger really moved the realised equity, not just a side counter
        self.assertAlmostEqual(base["ending_equity"] - double["ending_equity"],
                               base["fees"], places=9)

    def test_gross_accumulator_is_a_second_source(self):
        c = FakeCohort(self.ROWS, taf=0.0005)
        base = run(c, dirs(0, 0, 1, 1, 1))
        self.assertTrue(sb.pnl_decomposition_ok(base))
        with mock.patch.object(sb, "exit_price_pnl", lambda *a: 0.0):
            broken = run(c, dirs(0, 0, 1, 1, 1))
        self.assertEqual(broken["gross_pnl"], 0.0)
        self.assertFalse(sb.pnl_decomposition_ok(broken))
        # a hand-tampered fee ledger is caught by the cross-check as well
        tampered = dict(base)
        tampered["fees"] = base["fees"] + 1.0
        self.assertFalse(sb.pnl_decomposition_ok(tampered))

    def test_pnl_decomposition_tolerance(self):
        c = FakeCohort(self.ROWS, taf=0.0005)
        base = run(c, dirs(0, 0, 1, 1, 1))
        near = dict(base)
        near["fees"] = base["fees"] + 1e-4
        self.assertTrue(sb.pnl_decomposition_ok(near))


class TestWalkForwardCase(unittest.TestCase):
    """Pinned-pair walk-forward: only test segments trade, the case's pair is executed."""

    def make_cohort(self):
        """4 days x 4 bars: day 0 flat, day 1 up, day 2 flat, day 3 up."""
        day = [(100, 100, 100, 100)] * 4
        up = [(100, 101, 100, 101), (101, 102, 101, 102), (102, 103, 102, 103),
              (103, 104, 103, 104)]
        flat = [(104, 104, 104, 104)] * 4
        up2 = [(104, 105, 104, 105), (105, 106, 105, 106), (106, 107, 106, 107),
               (107, 108, 107, 108)]
        rows = day + up + flat + up2
        c = FakeCohort(rows, taf=0.0, day_bars=4)
        start = sb.utc_ms("2022-01-01")
        c.open_time_ms = np.array([start + i * 86400000 // 4 for i in range(len(rows))],
                                  dtype=np.int64)
        c.rebuild_days()
        return c

    def test_step_grid(self):
        self.assertEqual(sb.steps_for(0, 4, (1, 1)), [0, 2])
        self.assertEqual(sb.steps_for(0, 5, (2, 2)), [0])
        self.assertEqual(sb.steps_for(0, 3, (3, 3)), [])

    def test_case_trades_only_test_segments_with_its_own_pair(self):
        c = self.make_cohort()
        pairs = [(5, 40), (7, 50)]
        d_long = dirs(*([0, 0] + [1] * (c.n - 2)))
        d_short = dirs(*([0, 0] + [-1] * (c.n - 2)))
        dir_sets = [d_long, d_short]
        days = (0, 4, c.day_start)
        cell = (1, 1)
        sel = sb.select_train_pairs(c, days, cell, pairs, DCA, 0.0, 1.0, 1.0, True, dir_sets)
        self.assertEqual(len(sel), 2)
        # the training segment of step 0 is flat, so the tie-break elects the first pair
        self.assertEqual(sel[0]["selected_pair_index"], 0)
        m_long = sb.wf_case(c, days, cell, 0, dir_sets, RAIL, 0.0, 1.0, 1.0, True,
                            sb.START_EQUITY, sel)
        m_short = sb.wf_case(c, days, cell, 1, dir_sets, RAIL, 0.0, 1.0, 1.0, True,
                             sb.START_EQUITY, sel)
        self.assertEqual(m_long["n_steps"], 2)
        # the two pins are really different configurations (the ema_pair axis is not vacuous)
        self.assertNotAlmostEqual(m_long["net_pnl"], m_short["net_pnl"])
        # the test segments (days 1 and 3) are the only traded bars: 8 of 16 bars at most
        self.assertLessEqual(m_long["bars_in_market"], 8)
        # independent replay through the same engine, slice by slice, must agree exactly
        manual_gross = 0.0
        eq = sb.START_EQUITY
        for s in (0, 2):
            te = sb.simulate(c, (int(c.day_start[s + 1]), int(c.day_start[s + 2])), d_long,
                             RAIL, 0.0, 1.0, 1.0, True, eq)
            manual_gross += te["net_pnl"]
            eq = te["ending_equity"]
        self.assertAlmostEqual(m_long["net_pnl"], manual_gross, places=9)
        self.assertEqual(m_long["_trace"][0]["executed_pair_index"], 0)
        self.assertEqual(m_short["_trace"][0]["executed_pair_index"], 1)

    def test_case_is_deterministic(self):
        c = self.make_cohort()
        pairs = [(5, 40), (7, 50)]
        d_long = dirs(*([0, 0] + [1] * (c.n - 2)))
        dir_sets = [d_long, d_long]
        days = (0, 4, c.day_start)
        sel = sb.select_train_pairs(c, days, (1, 1), pairs, DCA, 0.0, 1.0, 1.0, True, dir_sets)
        a = sb.wf_case(c, days, (1, 1), 0, dir_sets, RAIL, 0.0, 1.0, 1.0, True, sb.START_EQUITY, sel)
        b = sb.wf_case(c, days, (1, 1), 0, dir_sets, RAIL, 0.0, 1.0, 1.0, True, sb.START_EQUITY, sel)
        self.assertEqual(a["net_pnl"], b["net_pnl"])
        self.assertEqual(a["sharpe"], b["sharpe"])


def mini_spec():
    return {
        "family_id": "synth-b-v2", "round_id": "synth-b-v2-r1", "run_id": "synth-b-v2-r1-u1",
        "params": {"ema_pair": [{"fast": 5, "slow": 40}, {"fast": 7, "slow": 50}],
                   "walk_forward": [{"train_days": 1, "test_days": 1}],
                   "grid_size": 2},
        "dca_domain": {"base_quote": 1000,
                       "spacing_pct": [0.02], "size_multiplier": [1.1],
                       "breakeven_tp_pct": [0.012], "invalidation_pct": [0.05],
                       "grid": [{"spacing_pct": 0.02, "size_multiplier": 1.1,
                                 "breakeven_tp_pct": 0.012, "invalidation_pct": 0.05,
                                 "base_quote": 1000}]},
        "costs": {"baseline_slippage_ticks": 1},
        "gates": {"min_episodes_is": 1, "neighborhood_min_same_sign_fraction": 0.6},
        "data": {"start": "2022-01-01", "end": "2022-01-04",
                 "historical_start": "2022-01-01", "historical_end": "2022-01-02",
                 "oos_start": "2022-01-03", "oos_end": "2022-01-04"},
    }


class TestRunCohortSynthetic(unittest.TestCase):
    """End-to-end wiring of one tiny cohort through run_cohort / evaluate_cohort."""

    def test_rows_coverage_and_selection(self):
        spec = mini_spec()
        rows = []
        day = [(100, 100, 100, 100)] * 4
        up = [(100, 101, 100, 101), (101, 102, 101, 102), (102, 103, 102, 103),
              (103, 104, 103, 104)]
        rows = day + up + day + up
        c = FakeCohort(rows, taf=0.0, tick=0.0, day_bars=4)
        start = sb.utc_ms("2022-01-01")
        c.open_time_ms = np.array([start + i * 86400000 // 4 for i in range(len(rows))],
                                  dtype=np.int64)
        c.rebuild_days()
        tmp = tempfile.mkdtemp(prefix="sb-test-")
        try:
            writers, handles = {}, []
            for kind in sb.COHORT_GRID_KINDS:
                fh = open(os.path.join(tmp, "grid_%s.csv" % kind), "w", newline="")
                handles.append(fh)
                w = csv.DictWriter(fh, fieldnames=list(sb.ROW_FIELDS))
                w.writeheader()
                writers[kind] = w
            agg = {"counts": {k: 0 for k in sb.COHORT_GRID_KINDS}, "per_cohort": {},
                   "strategy_cells": set(), "dca_cells": set(), "pnl_decomp_all": True,
                   "partition_all": True, "selector_stable": True,
                   "no_entry_after_exhaustion": True, "ending_equity_floor": True,
                   "entries_after_exhaustion_total": 0,
                   "episodes_long_total": 0, "episodes_short_total": 0,
                   "episodes_full_total": 0, "layer_totals": [0] * 12,
                   "stress_delta": {k: 0 for k in ("fee_2x", "funding_2x",
                                                   "entry_delay_1_bar", "slippage_2ticks",
                                                   "cost_attrition_40bps")},
                   "cohort_labels": [], "hist_rows_sample": [], "full_rows_sample": [],
                   "funding_coverage": {}, "winner_traces": {}}
            for k in sb.COHORT_GRID_KINDS:
                agg["per_cohort"][("SYNTH/5m", k)] = 0
            hist, winner, reason, winner_rows = sb.run_cohort(spec, c, lambda m: None,
                                                              writers, agg)
            for fh in handles:
                fh.close()
            self.assertEqual(agg["counts"]["historical"], 2)     # 2 pairs x 1 cell x 1 DCA
            self.assertEqual(agg["counts"]["full"], 2)
            self.assertEqual(len(hist), 2)
            # the walk-forward segments are DAY ranges, so a case on this cohort must really
            # execute its steps (a day-index/bar-index unit mix silently yields empty cases)
            self.assertTrue(all(r["n_steps"] > 0 for r in hist), [r["n_steps"] for r in hist])
            self.assertTrue(any(r["episodes"] > 0 for r in hist))
            self.assertIn(reason, ("selected", "insufficient_trades", "no_qualifying_candidate"))
            if winner is not None:
                self.assertIn("full", winner_rows)
                self.assertEqual(winner_rows["full"]["ema_fast"], winner["ema_fast"])
                self.assertIn("SYNTH/5m", agg["winner_traces"])
            self.assertTrue(agg["pnl_decomp_all"])
            self.assertTrue(agg["partition_all"])
            # the exhaustion assertion is wired to the measured entry count (row field), not to
            # the carry-in-seeded `min_entry_equity` diagnostic (card t_3c3f0a12)
            self.assertTrue(agg["no_entry_after_exhaustion"])
            self.assertEqual(agg["entries_after_exhaustion_total"], 0)
            self.assertIn("entries_after_exhaustion", hist[0])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# registered composite-axis selector / neighbourhood (card t_9afe04ad)
# ---------------------------------------------------------------------------
# The registered joint space of round r1, mirrored from the run-spec's `params`/`dca_domain`:
# 30 ema pairs x 4 walk-forward cells = 120 strategy cases, x 48 DCA configs = 5,760 cells.
# `ema_pair` and `walk_forward` are COMPOSITE registered axes: one registered value is a whole
# tuple.  The production u2 attempt crashed in the selector because a scalar of the 8-scalar
# cell key was used to index those composite axes (`ValueError: 10 is not in list`).  These
# tests pin the registered-index mapping, the composite-axis face adjacency (+-1 registered
# step = one whole tuple) and the fail-closed errors.
REG_FAST = (5, 7, 10, 15, 20, 30)
REG_SLOW = (40, 50, 100, 150, 200)
REG_PAIRS = [(f, s) for f in REG_FAST for s in REG_SLOW]
REG_WF = [(3, 3), (7, 7), (14, 14), (28, 28)]
REG_DCA = {"spacing_pct": [0.01, 0.02, 0.03, 0.04], "size_multiplier": [1.0, 1.1],
           "breakeven_tp_pct": [0.01, 0.02, 0.03], "invalidation_pct": [0.05, 0.1]}
REG_MIN_EP = 20


def registered_spec():
    """The registered joint-space domains (params + dca_domain + gates of round r1)."""
    return {"params": {"ema_pair": [{"fast": f, "slow": s} for f, s in REG_PAIRS],
                       "walk_forward": [{"train_days": a, "test_days": b} for a, b in REG_WF],
                       "grid_size": len(REG_PAIRS) * len(REG_WF)},
            "dca_domain": dict(REG_DCA, base_quote=1000),
            "gates": {"min_episodes_is": REG_MIN_EP,
                      "neighborhood_min_same_sign_fraction": 0.6}}


def registered_rows(net_pnl_of=None, kind="historical", sharpe=1.0, episodes=100):
    """Every one of the 5,760 registered joint cells, in `record()`'s row shape."""
    rows = []
    for pi, pair in enumerate(REG_PAIRS):
        for ci, cell in enumerate(REG_WF):
            for sp in REG_DCA["spacing_pct"]:
                for mu in REG_DCA["size_multiplier"]:
                    for be in REG_DCA["breakeven_tp_pct"]:
                        for inv in REG_DCA["invalidation_pct"]:
                            row = {"symbol": "SYNTH", "timeframe": "5m", "window_kind": kind,
                                   "ema_fast": pair[0], "ema_slow": pair[1],
                                   "wf_train_days": cell[0], "wf_test_days": cell[1],
                                   "ema_pair_index": pi, "walk_forward_index": ci,
                                   "spacing_pct": sp, "size_multiplier": mu,
                                   "breakeven_tp_pct": be, "invalidation_pct": inv,
                                   "base_quote": 1000, "sharpe": sharpe, "episodes": episodes,
                                   "net_pnl": 1.0}
                            if net_pnl_of is not None:
                                row["net_pnl"] = net_pnl_of(row)
                            rows.append(row)
    return rows


def expected_neighbour_cells(w, axes):
    """Independent hand-written face adjacency of the 8-scalar cell.

    A composite axis moves as ONE registered step: both of its scalars change together, and
    no neighbour may share just one component of the winner's pair / walk-forward cell.
    """
    pairs, wfs = axes["ema_pair"], axes["walk_forward"]
    w = list(w)
    out = []
    for step in (-1, 1):
        pi = pairs.index((w[0], w[1])) + step
        if 0 <= pi < len(pairs):
            out.append((pairs[pi][0], pairs[pi][1]) + tuple(w[2:]))
        wi = wfs.index((w[2], w[3])) + step
        if 0 <= wi < len(wfs):
            out.append(tuple(w[0:2]) + (wfs[wi][0], wfs[wi][1]) + tuple(w[4:]))
        for axis, comp in (("spacing_pct", 4), ("size_multiplier", 5),
                           ("breakeven_tp_pct", 6), ("invalidation_pct", 7)):
            vals, i = axes[axis], axes[axis].index(w[comp])
            if 0 <= i + step < len(vals):
                cell = list(w)
                cell[comp] = vals[i + step]
                out.append(tuple(cell))
    return out


def cell_row(rows, cell):
    return sb.same_cell(rows, cell)


class TestRegisteredAxisMapping(unittest.TestCase):
    """t_9afe04ad: rows are addressed through registered axes, never through a split tuple."""

    def test_full_5760_cell_grid_maps_and_tie_breaks_uniquely(self):
        spec = registered_spec()
        axes = sb.axis_values(spec)
        self.assertEqual(len(REG_PAIRS) * len(REG_WF), 120)
        self.assertEqual(len(REG_DCA["spacing_pct"]) * len(REG_DCA["size_multiplier"])
                         * len(REG_DCA["breakeven_tp_pct"]) * len(REG_DCA["invalidation_pct"]),
                         48)
        rows = registered_rows()
        self.assertEqual(len(rows), 5760)
        keys, cells = set(), set()
        for r in rows:
            key = sb.axis_key(r)
            self.assertEqual(len(key), len(sb.AXES))
            for i, axis in enumerate(sb.AXES):
                self.assertIn(key[i], axes[axis])
            tb = sb.tie_break_key(r, axes)
            self.assertEqual(len(tb), len(sb.AXES))
            for i, axis in enumerate(sb.AXES):
                self.assertTrue(0 <= tb[i] < len(axes[axis]))
            keys.add(tb)
            cells.add(sb.cell_key(r))
            self.assertEqual(sb.axis_key_cell(key), sb.cell_key(r))
        self.assertEqual(len(cells), 5760)
        self.assertEqual(len(keys), 5760)

    def test_legacy_scalar_indexing_of_composite_axes_is_red(self):
        """The pre-fix expression verbatim: it must raise on the legal cell u2 crashed on."""
        def legacy_tie_break_key(row, axes):
            return tuple(axes[a].index(sb.cell_key(row)[i]) for i, a in enumerate(sb.AXES))

        axes = sb.axis_values(registered_spec())
        row = next(r for r in registered_rows() if (r["ema_fast"], r["ema_slow"]) == (10, 40))
        with self.assertRaises(ValueError):
            legacy_tie_break_key(row, axes)
        fixed = sb.tie_break_key(row, axes)
        self.assertEqual(fixed[0], REG_PAIRS.index((10, 40)))
        self.assertEqual(fixed[1], REG_WF.index((3, 3)))
        # ema_fast=10 is a legal registered fast value (the u2 crash value)
        self.assertIn(10, REG_FAST)

    def test_tie_on_sharpe_and_net_pnl_picks_the_first_registered_cell(self):
        spec = registered_spec()
        spec["gates"]["min_episodes_is"] = 5
        axes = sb.axis_values(spec)
        rows = registered_rows(episodes=5)          # identical sharpe/net_pnl on every cell
        rows.reverse()                              # input order must not decide the tie
        winner, reason = sb.select_cohort_winner(rows, spec)
        self.assertEqual(reason, "selected")
        self.assertEqual(sb.tie_break_key(winner, axes),
                         min(sb.tie_break_key(r, axes) for r in rows))
        self.assertEqual((winner["ema_fast"], winner["ema_slow"]), REG_PAIRS[0])
        self.assertEqual((winner["wf_train_days"], winner["wf_test_days"]), REG_WF[0])
        self.assertEqual((winner["spacing_pct"], winner["size_multiplier"],
                          winner["breakeven_tp_pct"], winner["invalidation_pct"]),
                         (REG_DCA["spacing_pct"][0], REG_DCA["size_multiplier"][0],
                          REG_DCA["breakeven_tp_pct"][0], REG_DCA["invalidation_pct"][0]))
        shuffled = list(rows)
        random.Random(20260914).shuffle(shuffled)
        again, _ = sb.select_cohort_winner(shuffled, spec)
        self.assertEqual(sb.cell_key(again), sb.cell_key(winner))

    def test_unregistered_value_fails_closed_naming_axis_value_and_cell(self):
        spec = registered_spec()
        axes = sb.axis_values(spec)
        row = next(r for r in registered_rows() if (r["ema_fast"], r["ema_slow"]) == (10, 40))
        row = dict(row, spacing_pct=0.09)           # not a registered spacing value
        with self.assertRaises(ValueError) as caught:
            sb.tie_break_key(row, axes)
        text = str(caught.exception)
        self.assertIn("spacing_pct", text)
        self.assertIn("0.09", text)
        self.assertIn(str(sb.cell_key(row)), text)
        # no silent fallback in the neighbourhood either: an unregistered winner value fails
        with self.assertRaises(ValueError) as caught_nb:
            sb.cohort_neighbourhood(registered_rows(), row, spec)
        self.assertIn("spacing_pct", str(caught_nb.exception))


class TestRegisteredAxisNeighbourhood(unittest.TestCase):
    """t_9afe04ad: face adjacency on the six registered axes, composite axes move as one."""

    INTERIOR = (10, 50, 7, 7, 0.02, 1.1, 0.02, 0.1)   # interior on every registered axis
    CORNER = (5, 40, 3, 3, 0.01, 1.0, 0.01, 0.05)      # first registered index everywhere

    def neighbourhood(self, cell, net_pnl_of=None):
        spec = registered_spec()
        rows = registered_rows(net_pnl_of)
        winner = cell_row(rows, cell)
        return sb.cohort_neighbourhood(rows, winner, spec), rows

    def test_interior_and_corner_neighbour_counts(self):
        expected, _ = self.neighbourhood(self.INTERIOR)
        axes = sb.axis_values(registered_spec())
        self.assertEqual(expected["neighbours"],
                         len(expected_neighbour_cells(self.INTERIOR, axes)))
        self.assertEqual(expected["neighbours"], 10)   # 2+2+2+1+2+1 registered steps
        self.assertEqual(expected["agreeing"], 10)
        self.assertEqual(expected["same_sign_fraction"], 1.0)
        self.assertTrue(expected["passed"])
        corner, _ = self.neighbourhood(self.CORNER)
        self.assertEqual(corner["neighbours"], 6)      # one +1 step per registered axis
        self.assertEqual(corner["agreeing"], 6)
        self.assertEqual(corner["axis_steps"],
                         {a: len(axes[a]) for a in sb.AXES})

    def test_neighbours_are_the_registered_face_adjacent_cells_only(self):
        axes = sb.axis_values(registered_spec())
        want = set(expected_neighbour_cells(self.INTERIOR, axes))
        self.assertEqual(len(want), 10)
        positive = want | {self.INTERIOR}          # the winner itself is the sign reference
        nb, rows = self.neighbourhood(
            self.INTERIOR, lambda r: 1.0 if sb.cell_key(r) in positive else -1.0)
        self.assertEqual(nb["neighbours"], 10)
        self.assertEqual(nb["agreeing"], 10)
        self.assertEqual(nb["same_sign_fraction"], 1.0)
        self.assertTrue(nb["passed"])
        # A cell that shares exactly ONE scalar of the winner's ema_pair is NOT a neighbour:
        # under the registered-index semantics the only ema_pair neighbours are the pairs at
        # index +-1 of the registered list ((10,40) and (10,100) here), while a "split the
        # pair into two scalar axes" reading would also count the fast neighbours (5,50) and
        # (15,50) - the pre-fix code indexed exactly those scalar values.  Both split cells are
        # legal registered cells of this grid, so only the adjacency rule can exclude them.
        split_only = {(5, 50) + self.INTERIOR[2:], (15, 50) + self.INTERIOR[2:]}
        registered_cells = {sb.cell_key(r) for r in rows}
        for cell in split_only:
            self.assertIn(cell, registered_cells)
            self.assertNotIn(cell, want)
        self.assertIn((10, 40) + self.INTERIOR[2:], want)
        self.assertIn((10, 100) + self.INTERIOR[2:], want)
        nb2, _ = self.neighbourhood(
            self.INTERIOR,
            lambda r: 1.0 if sb.cell_key(r) in (split_only | {self.INTERIOR}) else -1.0)
        self.assertEqual(nb2["neighbours"], 10)
        self.assertEqual(nb2["agreeing"], 0)
        self.assertEqual(nb2["same_sign_fraction"], 0.0)
        self.assertFalse(nb2["passed"])

    def test_missing_legal_neighbour_fails_closed(self):
        spec = registered_spec()
        axes = sb.axis_values(spec)
        rows = registered_rows()
        winner = cell_row(rows, self.INTERIOR)
        gone = expected_neighbour_cells(self.INTERIOR, axes)[0]
        self.assertNotEqual(gone, self.INTERIOR)
        kept = [r for r in rows if sb.cell_key(r) != gone]
        self.assertEqual(len(kept), 5759)
        with self.assertRaises(ValueError) as caught:
            sb.cohort_neighbourhood(kept, winner, spec)
        text = str(caught.exception)
        self.assertIn("missing neighbours", text)
        self.assertIn(str(gone), text)

    def test_neighbourhood_rejects_non_historical_rows(self):
        spec = registered_spec()
        rows = registered_rows(kind="oos")
        winner = rows[0]
        with self.assertRaises(ValueError):
            sb.cohort_neighbourhood(rows, winner, spec)


# ---------------------------------------------------------------------------
# G8 cost-attrition cohort gate + the exhaustion measurement (card t_3c3f0a12)
# ---------------------------------------------------------------------------
# `gates.verdict_rule` counts the cohorts passing G3-G8 and `robustness_plan` R4 registers
# `cost_attrition_40bps` as a robustness gate (not a diagnostic).  The engine measured that grid
# but never added it to `cull_reasons`, so a winner whose 40 bps attrition cell is net-negative
# was still elected SURVIVOR.  The assertion `no_entry_after_exhaustion` had the mirror-image
# defect: it read `min_entry_equity > 0`, a minimum over entry-moment equities that is *seeded
# with the slice's carry-in equity*, so a walk-forward step opened on a negative carry looked
# like an entry although the engine had halted.


class TestCohortCostAttritionGate(unittest.TestCase):
    """t_3c3f0a12: G8 is wired into the registered `cull_reasons` vocabulary."""

    CELL = (10, 50, 7, 7, 0.02, 1.1, 0.02, 0.1)     # interior on every registered axis

    def evaluate(self, attrition_net_pnl):
        spec, rows = registered_spec(), registered_rows()  # every historical cell net_pnl = +1.0
        winner = cell_row(rows, self.CELL)
        # the winner's cell clears every other registered gate on its own grid: the attrition
        # cell is the only variable under test
        winner_rows = {k: dict(winner, window_kind=k, net_pnl=1.0)
                       for k in sb.COHORT_GRID_KINDS if k != "historical"}
        winner_rows["cost_attrition_40bps"] = dict(winner, window_kind="cost_attrition_40bps",
                                                  net_pnl=attrition_net_pnl)
        return sb.evaluate_cohort(spec, "SYNTH/5m", winner, "selected", rows, winner_rows)

    def test_attrition_cell_sign_decides_the_cohort(self):
        for attrition, expected in ((0.0, ["robustness_economic:cost_attrition_40bps"]),
                                    (-1.0, ["robustness_economic:cost_attrition_40bps"]),
                                    (1.0, [])):             # the gate is strict `net_pnl > 0`
            out = self.evaluate(attrition)
            self.assertEqual(out["cull_reasons"], expected)
            self.assertEqual(out["outcome"], "CULLED" if expected else "SURVIVOR")
            # the measurement stays reported for the audit trail
            self.assertEqual(out["metrics"]["cost_attrition_40bps"]["net_pnl"], attrition)


class TestNoEntryAfterExhaustionMetric(unittest.TestCase):
    """t_3c3f0a12: the assertion counts entries on an exhausted account, and no path re-enters."""

    def make_cohort(self, funding=None):
        """4 days x 2 bars at a flat price: isolates the funding settlement of a flip close."""
        rows = [(100, 100, 100, 100)] * 8
        c = FakeCohort(rows, taf=0.0, tick=0.0, funding=funding, day_bars=2)
        start = sb.utc_ms("2022-01-01")
        c.open_time_ms = np.array([start + i * 86400000 // 2 for i in range(len(rows))],
                                  dtype=np.int64)
        c.rebuild_days()
        return c

    def test_nonpositive_carry_in_is_not_an_entry(self):
        """A step opened on a nonpositive carry-in is a halt, not an entry: the pre-fix row
        expression (`min_entry_equity > 0`) is false on this very row."""
        c = self.make_cohort()
        d_long = dirs(*([0, 0] + [1] * (c.n - 2)))
        sel = sb.select_train_pairs(c, (0, 4, c.day_start), (1, 1), [(5, 40)], DCA, 0.0, 1.0,
                                    1.0, True, [d_long])
        m = sb.wf_case(c, (0, 4, c.day_start), (1, 1), 0, [d_long], RAIL, 0.0, 1.0, 1.0, True,
                       -1000.0, sel)                # the previous step blew the account up
        self.assertEqual(m["episodes"], 0)              # the engine halted: no entry happened
        self.assertLessEqual(m["min_entry_equity"], 0.0)  # the polluted carry-in diagnostic
        self.assertEqual(m["entries_after_exhaustion"], 0)  # the direct count stays clean

    def test_flip_close_below_zero_does_not_re_enter(self):
        """`close_episode()` settles the funding of the closed episode, so a flip can leave the
        account nonpositive on that bar although the margin backstop (which reads the equity
        BEFORE that settlement) passed: the re-establishment needs the flat entry's exhaustion
        guard (card t_3c3f0a12, the single-point execution bug)."""
        # bar 2 opens the long, bar 3 flips it to short; the funding accrued over bar 2 is
        # settled (and only settled) when the flip closes that episode
        funding = [0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        c = self.make_cohort(funding=funding)
        d_flip = dirs(0, 0, 1, -1, 0, 0, 0, 0)
        te = sb.simulate(c, (2, 4), d_flip, RAIL, 0.0, 1.0, 1.0, True, 1100.0)
        self.assertEqual(te["flips"], 1)                # the flip still flattens ...
        self.assertLess(te["ending_equity"], 0.0)       # ... on a nonpositive account ...
        self.assertEqual(te["episodes"], 1)             # ... and does not re-enter
        self.assertTrue(te["halted"])
        self.assertTrue(te["capital_exhausted"])        # the flip left the account nonpositive
        self.assertEqual(te["entries_after_exhaustion"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
