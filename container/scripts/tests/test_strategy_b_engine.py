#!/usr/bin/env python3
"""Executable check for the Strategy B execution engine (sym-directional DCA ladder).

Runs inside the qlib container:
    /opt/venv/bin/python /scripts/tests/test_strategy_b_engine.py

Drives `simulate()` with crafted bars and hand-replayed fills so that a regression in the
ladder walk, the resting invalidation, the take profit, the direction flip, the "no add after
a kill" rule, the fill lags, the day-marking or the fee/funding accounting fails loudly
instead of silently changing the science.  stdlib unittest + numpy only; no market data,
no container state.

Hand-checked reference numbers are recomputed independently in `walk_expect` (long) and
`walk_expect_short` (short) rather than copied from the engine's own output.
"""
import importlib.util
import json
import os
import unittest

import numpy as np

ENGINE = os.environ.get("SB_ENGINE_PATH", "/scripts/30_strategy_b_run.py")
_spec = importlib.util.spec_from_file_location("sb_engine", ENGINE)
sb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sb)

RAIL = {"base_quote": 1000.0, "size_multiplier": 1.1, "spacing_d0": 0.02,
        "tp": 0.012, "invalidation": 0.05}
LEV = 10.0
P0 = 100.0
NOTIONAL0 = RAIL["base_quote"] * LEV


class FakeCohort:
    def __init__(self, rows, dirs, tick=0.0, taf=0.0, funding=None, margin_maint=0.1):
        self.open = np.array([r[0] for r in rows], dtype=np.float64)
        self.high = np.array([r[1] for r in rows], dtype=np.float64)
        self.low = np.array([r[2] for r in rows], dtype=np.float64)
        self.close = np.array([r[3] for r in rows], dtype=np.float64)
        self.n = len(rows)
        self.dirs = np.array(dirs, dtype=np.int8)
        self.open_time_ms = np.arange(self.n, dtype=np.int64) * 300000
        self.day_index = (np.arange(self.n, dtype=np.int64) // 12)
        self.n_days = int(self.day_index[-1]) + 1
        is_de = np.zeros(self.n, dtype=bool)
        is_de[np.concatenate([self.day_index[1:] != self.day_index[:-1], [True]])] = True
        self.is_day_end = is_de
        self.day_start = np.searchsorted(self.day_index, np.arange(self.n_days + 1),
                                         side="left").astype(np.int64)
        self.funding = np.zeros(self.n) if funding is None else np.array(funding, dtype=np.float64)
        self.price_increment = tick
        self.taker_fee = taf
        self.leverage = LEV
        self.margin_maint = margin_maint


def run(rows, dirs, **kw):
    slip = kw.pop("slip", 0.0)
    f = FakeCohort(rows, dirs, **kw)
    return sb.simulate(f, 0, f.n, f.dirs, RAIL, slip)


def walk_expect(low, open_=P0):
    """Independent replay of the LONG descending-price trigger walk (shallow -> deep)."""
    qty = NOTIONAL0 / P0
    cost = NOTIONAL0
    fills = 0
    k = 1
    while True:
        stop = (cost / qty) * (1.0 - RAIL["invalidation"])
        lvl = P0 * (1.0 - RAIL["spacing_d0"] * k)
        if k <= 10 and lvl >= stop:
            trig, is_stop = lvl, False
        else:
            trig, is_stop = stop, True
        if low > trig:
            return fills, None
        if is_stop:
            return fills, (trig if open_ >= trig else open_)
        qty += (RAIL["base_quote"] * RAIL["size_multiplier"] ** k * LEV) / trig
        cost += RAIL["base_quote"] * RAIL["size_multiplier"] ** k * LEV
        fills = k
        k += 1


def walk_expect_short(high, open_=P0):
    """Independent replay of the SHORT ascending-price trigger walk (mirror image)."""
    qty = NOTIONAL0 / P0
    cost = NOTIONAL0
    fills = 0
    k = 1
    while True:
        stop = (cost / qty) * (1.0 + RAIL["invalidation"])
        lvl = P0 * (1.0 + RAIL["spacing_d0"] * k)
        if k <= 10 and lvl <= stop:
            trig, is_stop = lvl, False
        else:
            trig, is_stop = stop, True
        if high < trig:
            return fills, None
        if is_stop:
            return fills, (trig if open_ <= trig else open_)
        qty += (RAIL["base_quote"] * RAIL["size_multiplier"] ** k * LEV) / trig
        cost += RAIL["base_quote"] * RAIL["size_multiplier"] ** k * LEV
        fills = k
        k += 1


class TestLongLeg(unittest.TestCase):

    def test_entry_then_take_profit(self):
        m = run([(100.0, 100.0, 100.0, 100.0), (100.0, 101.3, 99.9, 101.0)], [1, 1])
        self.assertEqual(m["episodes"], 1)
        self.assertEqual(m["tp_hits"], 1)
        self.assertEqual(m["layers"][0], 1)
        self.assertEqual(sum(m["layers"][1:]), 0)
        self.assertAlmostEqual(m["net_pnl"], 0.012 * NOTIONAL0, places=6)
        self.assertAlmostEqual(m["ending_equity"], 30120.0, places=6)

    def test_ladder_walk_then_resting_stop(self):
        fills, kill = walk_expect(85.0, 100.0)
        self.assertEqual(fills, 4)
        self.assertIsNotNone(kill)
        m = run([(100.0, 100.0, 100.0, 100.0), (100.0, 101.0, 85.0, 86.0)], [1, 1])
        self.assertEqual(m["episodes"], 1)
        self.assertEqual(m["stop_hits"], 1)
        self.assertEqual(m["tp_hits"], 0)
        self.assertEqual(m["layers"][:6], [1, 1, 1, 1, 1, 0])
        self.assertEqual(sum(m["layers"][6:]), 0)
        cost = sum(RAIL["base_quote"] * 1.1 ** k * LEV for k in range(5))
        qty = sum((RAIL["base_quote"] * 1.1 ** k * LEV) / (P0 * (1 - 0.02 * k)) for k in range(5))
        self.assertAlmostEqual(m["net_pnl"], qty * kill - cost, places=6)
        self.assertLess(m["net_pnl"], 0.0)

    def test_gap_through_the_stop_fills_at_the_open(self):
        fills, kill = walk_expect(79.0, open_=80.0)
        m = run([(100.0, 100.0, 100.0, 100.0), (80.0, 81.0, 79.0, 80.0)], [1, 1])
        self.assertEqual(m["stop_hits"], 1)
        self.assertEqual(kill, 80.0)
        cost = sum(RAIL["base_quote"] * 1.1 ** k * LEV for k in range(fills + 1))
        qty = sum((RAIL["base_quote"] * 1.1 ** k * LEV) / (P0 * (1 - 0.02 * k))
                  for k in range(fills + 1))
        self.assertAlmostEqual(m["net_pnl"], qty * 80.0 - cost, places=6)

    def test_take_profit_uses_the_same_bar_adds(self):
        # P0 = 100 -> level 1 = 98.0 and level 2 = 96.0, both touched by the bar low
        m = run([(100.0, 100.0, 100.0, 100.0), (100.0, 101.5, 96.0, 100.0)], [1, 1])
        self.assertEqual(m["layers"][0], 1)
        self.assertEqual(m["layers"][:4], [1, 1, 1, 0])
        self.assertEqual(m["tp_hits"], 1)
        cost = sum(NOTIONAL0 * 1.1 ** k for k in range(3))
        self.assertAlmostEqual(m["net_pnl"], 0.012 * cost, places=6)

    def test_no_add_after_a_kill(self):
        rows = [(100.0, 100.0, 100.0, 100.0), (100.0, 101.0, 85.0, 86.0),
                (86.0, 86.0, 80.0, 81.0), (81.0, 81.0, 70.0, 71.0)]
        m = run(rows, [1, 1, 1, 1])
        self.assertEqual(m["episodes"], 1)
        self.assertEqual(m["layers"][:6], [1, 1, 1, 1, 1, 0])
        self.assertEqual(sum(m["layers"][6:]), 0)


class TestShortLeg(unittest.TestCase):

    def test_short_entry_then_take_profit(self):
        m = run([(100.0, 100.0, 100.0, 100.0), (100.0, 100.1, 98.7, 99.0)], [-1, -1])
        self.assertEqual(m["episodes"], 1)
        self.assertEqual(m["tp_hits"], 1)
        self.assertAlmostEqual(m["net_pnl"], 0.012 * NOTIONAL0, places=6)
        self.assertAlmostEqual(m["ending_equity"], 30120.0, places=6)

    def test_short_ladder_is_the_mirror_of_the_long_walk(self):
        fills, kill = walk_expect_short(115.0, 100.0)
        self.assertEqual(fills, 4)
        self.assertIsNotNone(kill)
        m = run([(100.0, 100.0, 100.0, 100.0), (100.0, 115.0, 99.0, 114.0)], [-1, -1])
        self.assertEqual(m["stop_hits"], 1)
        self.assertEqual(m["layers"][:6], [1, 1, 1, 1, 1, 0])
        cost = sum(RAIL["base_quote"] * 1.1 ** k * LEV for k in range(5))
        qty = sum((RAIL["base_quote"] * 1.1 ** k * LEV) / (P0 * (1 + 0.02 * k)) for k in range(5))
        self.assertAlmostEqual(m["net_pnl"], -(qty * kill - cost), places=6)
        self.assertLess(m["net_pnl"], 0.0)

    def test_short_gap_through_the_stop_fills_at_the_open(self):
        fills, kill = walk_expect_short(115.0, open_=112.0)
        m = run([(100.0, 100.0, 100.0, 100.0), (112.0, 115.0, 111.0, 114.0)], [-1, -1])
        self.assertEqual(m["stop_hits"], 1)
        self.assertEqual(kill, 112.0)
        cost = sum(RAIL["base_quote"] * 1.1 ** k * LEV for k in range(fills + 1))
        qty = sum((RAIL["base_quote"] * 1.1 ** k * LEV) / (P0 * (1 + 0.02 * k))
                  for k in range(fills + 1))
        self.assertAlmostEqual(m["net_pnl"], -(qty * 112.0 - cost), places=6)

    def test_long_and_short_are_exact_mirrors(self):
        # fee-free mirror: the fee is charged on the traded notional, so a long exiting at a
        # higher price legitimately pays more than a short exiting at the mirrored lower price.
        up = [(100.0, 100.0, 100.0, 100.0), (100.0, 115.0, 99.0, 114.0),
              (114.0, 114.0, 80.0, 90.0)]
        dn = [(100.0, 100.0, 100.0, 100.0), (100.0, 101.0, 85.0, 86.0),
              (86.0, 120.0, 86.0, 110.0)]
        long_m = run(up, [1, 1, 1], taf=0.0, tick=0.1)
        short_m = run(dn, [-1, -1, -1], taf=0.0, tick=0.1)
        self.assertAlmostEqual(long_m["net_pnl"], short_m["net_pnl"], places=6)
        self.assertEqual(long_m["episodes"], short_m["episodes"])
        long_fee = run(up, [1, 1, 1], taf=0.0005, tick=0.1)
        short_fee = run(dn, [-1, -1, -1], taf=0.0005, tick=0.1)
        self.assertGreater(long_fee["fees"], short_fee["fees"])

    def test_short_pays_no_positive_funding_and_receives_it(self):
        fund = [0.0, 0.001, 0.0]
        long_m = run([(100.0, 100.0, 100.0, 100.0), (100.0, 101.0, 99.0, 100.0),
                      (100.0, 101.0, 99.0, 100.0)], [1, 1, 1], funding=fund)
        short_m = run([(100.0, 100.0, 100.0, 100.0), (100.0, 101.0, 99.0, 100.0),
                       (100.0, 101.0, 99.0, 100.0)], [-1, -1, -1], funding=fund)
        self.assertGreater(long_m["funding"], 0.0)
        self.assertLess(short_m["funding"], 0.0)
        self.assertLess(long_m["net_pnl"], short_m["net_pnl"])


class TestDirectionSwitching(unittest.TestCase):

    def test_direction_flip_is_a_reduce_only_close_then_a_reopen(self):
        rows = [(100.0, 100.0, 100.0, 100.0), (100.0, 100.5, 99.0, 100.0),
                (100.0, 100.6, 99.4, 100.0), (100.0, 100.7, 99.3, 100.0)]
        m = run(rows, [1, 1, -1, -1])
        self.assertEqual(m["flips"], 1)
        self.assertEqual(m["episodes"], 2)
        self.assertEqual(m["layers"][0], 2)

    def test_a_killed_leg_waits_for_a_flip_before_re_entry(self):
        rows = [(100.0, 100.0, 100.0, 100.0), (100.0, 101.0, 85.0, 86.0),
                (86.0, 86.0, 86.0, 86.0), (86.0, 86.0, 86.0, 86.0)]
        m = run(rows, [1, 1, 1, 1])
        self.assertEqual(m["episodes"], 1)
        m2 = run(rows, [1, 1, -1, -1])
        self.assertEqual(m2["episodes"], 2)
        self.assertEqual(m2["flips"], 0)   # the first leg was killed, not flipped

    def test_open_episode_is_force_flattened_at_the_slice_end(self):
        m = run([(100.0, 100.0, 100.0, 100.0), (100.0, 100.1, 99.9, 100.0)], [1, 1],
                tick=0.1, slip=1.0)
        self.assertEqual(m["open_at_end"], 1)
        self.assertEqual(m["episodes"], 1)
        self.assertEqual(m["episodes"], m["tp_hits"] + m["stop_hits"] + m["flips"]
                         + m["open_at_end"] + m["margin_calls"])
        self.assertLess(m["net_pnl"], 0.0)


class TestTimingAndMetrics(unittest.TestCase):

    def test_direction_array_composes_both_registered_lags(self):
        f = np.array([1.0, 3.0, 5.0, 7.0, 9.0])
        s = np.array([5.0, 5.0, 5.0, 5.0, 5.0])
        self.assertEqual(list(sb.direction_array(f, s, 0)), [0, 0, -1, -1, -1])
        self.assertEqual(list(sb.direction_array(f, s, 1)), [0, 0, 0, -1, -1])

    def test_ties_and_the_warm_up_default_to_long(self):
        eq = np.array([4.0, 4.0, 4.0, 4.0])
        self.assertEqual(list(sb.direction_array(eq, eq, 0)), [0, 0, 1, 1])

    def test_daily_series_forward_fills_and_sharpe_is_null_on_a_non_positive_path(self):
        series = sb.daily_series({2: 101.0}, 0, 5, 100.0)
        self.assertEqual(list(series), [100.0, 100.0, 101.0, 101.0, 101.0])
        bad = np.array([100.0, 50.0, -5.0, 10.0])
        self.assertIsNone(sb.sharpe_of(bad))
        self.assertIsNotNone(sb.sharpe_of(np.array([100.0, 101.0, 102.0, 103.0])))

    def test_robust_sharpe_averages_own_and_in_domain_neighbours(self):
        grid = [[1.0, 2.0], [3.0, 4.0]]
        self.assertAlmostEqual(sb.robust_sharpe(grid, 0, 0, 2, 2), 0.5 * 1.0 + 0.5 * 2.5)
        self.assertAlmostEqual(sb.robust_sharpe(grid, 1, 1, 2, 2), 0.5 * 4.0 + 0.5 * 2.5)
        self.assertIsNone(sb.robust_sharpe([[None, 1.0], [1.0, 1.0]], 0, 0, 2, 2))

    def test_costs_and_funding_are_not_estimated(self):
        fund = [0.0, 0.001, 0.0]
        m = run([(100.0, 100.0, 100.0, 100.0), (100.0, 101.0, 99.0, 100.0),
                 (100.0, 101.3, 99.0, 101.0)], [1, 1, 1], funding=fund, taf=0.0005, tick=0.1,
                slip=1.0)
        self.assertGreater(m["fees"], 0.0)
        self.assertGreater(m["funding"], 0.0)
        self.assertAlmostEqual(m["gross_pnl"] - m["fees"] - m["funding"], m["net_pnl"], places=6)
        self.assertLess(m["net_pnl"], 0.012 * NOTIONAL0)

    def test_backstop_is_unreachable_with_the_calibrated_maintenance_margin(self):
        for high, low in ((101.0, 90.0), (101.0, 60.0), (101.0, 20.0)):
            m = run([(100.0, 100.0, 100.0, 100.0), (100.0, high, low, 100.0)], [1, 1])
            self.assertEqual(m["margin_calls"], 0, "low=%s" % low)

    def test_capital_exhaustion_backstop_is_reachable(self):
        m = run([(100.0, 100.0, 100.0, 100.0), (100.0, 101.0, 99.5, 100.0)], [1, 1],
                margin_maint=5.0)
        self.assertEqual(m["margin_calls"], 1)
        self.assertTrue(m["capital_exhausted"])

    def test_no_entry_once_the_account_is_exhausted(self):
        rail = dict(RAIL)
        rail["base_quote"] = 24000.0
        f = FakeCohort([(100.0, 100.0, 100.0, 100.0), (100.0, 101.0, 85.0, 86.0),
                        (86.0, 130.0, 86.0, 130.0)], [1, -1, -1])
        m = sb.simulate(f, 0, f.n, f.dirs, rail, 0.0)
        self.assertTrue(m["halted"])
        self.assertLess(m["ending_equity"], 0.0)
        self.assertEqual(m["min_entry_equity"], 30000.0)

    def test_json_payload_is_strictly_valid(self):
        out = sb._sanitize({"a": float("nan"), "b": float("inf"), "c": np.bool_(True),
                            "d": np.int64(3), "e": [float("-inf")], "f": np.float32(1.5)})
        self.assertIsNone(out["a"])
        self.assertIsNone(out["b"])
        self.assertIs(out["c"], True)
        self.assertEqual(out["d"], 3)
        self.assertEqual(out["e"], [None])
        self.assertAlmostEqual(out["f"], 1.5, places=5)
        json.dumps(out, allow_nan=False)

    def test_episode_accounting_is_deterministic(self):
        rows = [(100.0, 100.0, 100.0, 100.0), (100.0, 115.0, 99.0, 114.0)]
        a = run(rows, [1, 1])
        b = run(rows, [1, 1])
        self.assertEqual(a["net_pnl"], b["net_pnl"])
        self.assertEqual(a["layers"], b["layers"])


class TestAccountingAndWiring(unittest.TestCase):
    """Regression tests for the two defects found in r1-u1: fees tracked but never deducted
    from the realised equity (which made every cost stress track a no-op) and the entry-delay
    stress not actually reaching the direction set."""

    def test_fees_reduce_the_realised_equity_at_the_fill(self):
        rows = [(100.0, 100.0, 100.0, 100.0), (100.0, 101.3, 99.9, 101.0)]
        free = run(rows, [1, 1], taf=0.0)
        costly = run(rows, [1, 1], taf=0.0005)
        doubled = run(rows, [1, 1], taf=0.001)
        self.assertGreater(costly["fees"], 0.0)
        self.assertLess(costly["net_pnl"], free["net_pnl"])
        self.assertLess(doubled["net_pnl"], costly["net_pnl"])
        self.assertAlmostEqual(costly["ending_equity"], 30000.0 + costly["net_pnl"], places=6)
        self.assertAlmostEqual(costly["gross_pnl"] - costly["fees"] - costly["funding"],
                               costly["net_pnl"], places=6)
        self.assertAlmostEqual(costly["net_pnl"], 120.0 - 5.0 - 5.06, places=6)

    def test_fee_multiplier_moves_the_number(self):
        rows = [(100.0, 100.0, 100.0, 100.0), (100.0, 101.0, 85.0, 86.0)]
        f = FakeCohort(rows, [1, 1], taf=0.0005)
        base = sb.simulate(f, 0, f.n, f.dirs, RAIL, 0.0, 1.0)
        x2 = sb.simulate(f, 0, f.n, f.dirs, RAIL, 0.0, 2.0)
        self.assertLess(x2["net_pnl"], base["net_pnl"])
        self.assertAlmostEqual(x2["fees"], 2.0 * base["fees"], places=6)

    def test_entry_delay_direction_set_changes_the_walk_forward(self):
        n = 720
        rows = [(100.0 + 0.05 * i,) * 4 for i in range(n)]
        c = FakeCohort(rows, [0] * n)
        fast = np.full(n, 10.0)
        slow = np.full(n, 10.0)
        # the flip must land inside a TEST block ((3,3) blocks are 36 bars, the 6th test block
        # is bars 396..432), otherwise both lags are only visible in a train block
        fast[:400], slow[:400] = 11.0, 10.0
        fast[400:], slow[400:] = 9.0, 10.0
        d0 = sb.direction_array(fast, slow, 0)
        d1 = sb.direction_array(fast, slow, 1)
        self.assertNotEqual(list(d0), list(d1))
        r0 = sb.wf_run(c, 0, 60, (3, 3), [d0], [(5, 40)], RAIL, 0.0, 30000.0)
        r1 = sb.wf_run(c, 0, 60, (3, 3), [d1], [(5, 40)], RAIL, 0.0, 30000.0)
        self.assertGreater(r0["n_steps"], 0)
        self.assertEqual(r0["n_steps"], r1["n_steps"])
        self.assertNotEqual(r0["net_pnl"], r1["net_pnl"])

    def test_walk_forward_chains_the_blocks_into_one_account(self):
        n = 720
        rows = [(100.0 + 0.05 * i,) * 4 for i in range(n)]
        c = FakeCohort(rows, [0] * n)
        fast = np.full(n, 11.0)
        slow = np.full(n, 10.0)
        r = sb.wf_run(c, 0, 60, (3, 3), [sb.direction_array(fast, slow, 0)], [(5, 40)],
                      RAIL, 0.0, 30000.0)
        step_sum = sum(s["test_net_pnl"] for s in r["steps"])
        self.assertAlmostEqual(r["net_pnl"], step_sum, places=6)
        self.assertAlmostEqual(r["ending_equity"], 30000.0 + r["net_pnl"], places=6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
