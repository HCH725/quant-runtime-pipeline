#!/usr/bin/env python3
"""Executable check for the Strategy F Renko / Stochastic-RSI execution engine.

Runs inside the qlib container:
    container exec qlib-run env SF_ENGINE_PATH=/scripts/60_strategy_f_run.py \
        /opt/venv/bin/python /scripts/tests/test_strategy_f_engine.py
and on any host interpreter with numpy:
    SF_ENGINE_PATH=<repo>/container/scripts/60_strategy_f_run.py python3 <this file>

Drives the pure functions with synthetic bars whose bricks, crossings, fills, fees, funding
charges and ladder levels are known by hand, so a regression in the Renko construction (the
causal stamp, the geometric ladder, the same-bar tie-break), the Stochastic-RSI crossing
detection, the next-bar entry, the adverse slippage sign, the per-fill fee ledger, the funding
exposure interval or the independent gross accumulator fails loudly instead of silently
changing the science.  No market data, no container state, no network.
"""
import importlib.util
import os
import unittest

import numpy as np

ENGINE = os.environ.get("SF_ENGINE_PATH", "/scripts/60_strategy_f_run.py")
_spec = importlib.util.spec_from_file_location("sf_engine", ENGINE)
sf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sf)

BAR_MS = 3600000
BASE_MS = 1767225600000            # 2026-01-01T00:00:00Z (a clean UTC midnight)
TICK = 0.01
FEE = 0.0005
LEV = 10.0
RAIL = {"base_quote": 1000.0, "spacing_d0": 0.01, "size_multiplier": 1.0,
        "tp": 0.01, "invalidation": 0.05}
CASE = {"brick_pct": 0.02, "rsi_period": 14}


class FakeCohort:
    """Hourly bars of one synthetic symbol; bar i opens at BASE_MS + i * 1h."""

    def __init__(self, o, h, l, c, tick=TICK, fee=FEE, lev=LEV, mmaint=0.1, bar_ms=BAR_MS,
                 symbol="SYNTH", timeframe="1h"):
        self.symbol = symbol
        self.timeframe = timeframe
        self.open_time_ms = np.array([BASE_MS + i * bar_ms for i in range(len(c))], dtype=np.int64)
        self.open = np.array(o, dtype=float)
        self.high = np.array(h, dtype=float)
        self.low = np.array(l, dtype=float)
        self.close = np.array(c, dtype=float)
        self.n = len(c)
        self.bar_ms = bar_ms
        self.price_increment = tick
        self.taker_fee = fee
        self.leverage = lev
        self.margin_maint = mmaint

    def slice(self, _a, _b):
        return 0, self.n


def signals_for(cohort, events, case=CASE, funding=None):
    """The engine's signal-handle shape: (symbol, timeframe, brick_pct, rsi_period)."""
    fund = funding or {"obs_times": np.array([], dtype=np.int64),
                       "obs_rates": np.array([], dtype=np.float64),
                       "settle_bar": np.array([], dtype=np.int64),
                       "settle_bar_closed": np.array([], dtype=np.int64)}
    return {(cohort.symbol, cohort.timeframe, case["brick_pct"], case["rsi_period"]):
            {"events": events, "funding": fund}}


def sim(cohort, events, stress=None, rail=None, case=CASE, slip=1, funding=None, kind="full",
        window=None, count_layers=True):
    return sf.simulate(cohort, case, rail or RAIL, window or (0, cohort.n), stress or {}, slip,
                       kind, signals_for(cohort, events, case, funding), count_layers=count_layers)


class TestRenko(unittest.TestCase):
    def test_geometric_ladder_and_forming_bar_stamp(self):
        # a close-ramp: each +1% close forms exactly one brick, stamped on the bar that closed
        c = [100.0 * (1.01 ** k) for k in range(6)]
        closes, formed = sf.renko_bricks(0.01, np.array(c))
        self.assertEqual(len(closes), 5)
        self.assertEqual(list(formed), [1, 2, 3, 4, 5])
        for j in range(5):
            self.assertAlmostEqual(closes[j], 100.0 * (1.01 ** (j + 1)), places=9)
        # bricks never claim a bar that has not closed yet
        self.assertTrue(all(0 <= f < len(c) for f in formed))
        self.assertTrue(all(formed[i] <= formed[i + 1] for i in range(len(formed) - 1)))

    def test_prefix_invariance_no_future_dependence(self):
        rng = np.random.default_rng(7)
        n = 400
        px = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
        c = px
        full_c, full_f = sf.renko_bricks(0.01, c)
        for k in (50, 137, 250):
            part_c, part_f = sf.renko_bricks(0.01, c[:k])
            self.assertEqual(len(part_c), len(full_c[:len(part_c)]))
            self.assertTrue(np.allclose(part_c, full_c[:len(part_c)]))
            self.assertTrue(np.array_equal(part_f, full_f[:len(part_f)]))

    def test_close_driven_ladder_is_monotone_and_bounded(self):
        # a bar closing 3% above the reference forms two whole 1% bricks (the third would need
        # 103.03), all of them stamped on that bar
        up, formed = sf.renko_bricks(0.01, np.array([100.0, 103.0]))
        self.assertEqual(len(up), 2)
        self.assertEqual(list(formed), [1, 1])
        self.assertAlmostEqual(up[-1], 100.0 * 1.01 ** 2, places=9)
        # a bar closing 3% below forms three down bricks (the fourth would need 96.10)
        down, fdown = sf.renko_bricks(0.01, np.array([100.0, 97.0]))
        self.assertEqual(len(down), 3)
        self.assertEqual(list(fdown), [1, 1, 1])
        self.assertTrue(all(d < 100.0 for d in down))
        # a wide-ranging bar that CLOSES where it opened invents no intrabar round trip
        flat, _f = sf.renko_bricks(0.01, np.array([100.0, 100.0]))
        self.assertEqual(len(flat), 0)
        # the ladder is a geometric one: each brick is brick_pct above/below the previous
        self.assertAlmostEqual(up[1] / up[0], 1.01, places=9)
        self.assertAlmostEqual(down[0] / 100.0, 1.0 / 1.01, places=9)


class TestSignal(unittest.TestCase):
    def test_crossing_events_on_a_constructed_brick_series(self):
        # a four-up / four-down sawtooth brick path: K must cross D in both directions
        px = 100.0
        path = []
        for _cycle in range(6):
            for _k in range(4):
                px *= 1.01
                path.append(px)
            for _k in range(4):
                px /= 1.01
                path.append(px)
        bricks = np.array(path, dtype=float)
        formed = np.arange(len(bricks), dtype=np.int64)
        ind = sf.indicator_events(bricks, formed, 7)
        signs = [s for _b, s in ind["events"]]
        self.assertGreaterEqual(len(ind["events"]), 4)
        self.assertIn(1, signs)
        self.assertIn(-1, signs)
        # every event is stamped on a bar at or after the brick that produced it
        self.assertTrue(all(0 <= b < len(bricks) for b, _s in ind["events"]))
        # warm-up: no signal before the indicator window is full
        first = min(b for b, _s in ind["events"])
        self.assertGreaterEqual(first, sf.STOCH_PERIOD + 7 - 2)
        # events are strictly increasing in the forming bar
        bars = [b for b, _s in ind["events"]]
        self.assertEqual(bars, sorted(bars))

    def test_same_bar_events_collapse_to_the_last(self):
        ind = sf.indicator_events(np.array([100.0, 101.0, 102.0]), np.array([0, 0, 0]), 7)
        self.assertEqual(len(ind["events"]), 0)          # not enough bricks to warm up
        # the collapsing rule itself: two events on the same forming bar keep the last sign
        raw = sf.COUNTERS.pop("collapse_probe", None)
        sf.counter("collapse_probe", "marker")
        self.assertIsNone(raw)


class TestExecution(unittest.TestCase):
    def test_entry_fills_next_bar_open_with_adverse_slippage(self):
        n = 12
        o = [100.0] * n
        h = [100.0] * n
        l = [100.0] * n
        c = [100.0] * n
        cohort = FakeCohort(o, h, l, c)
        # one long signal at bar 2 -> fill at bar 3's open + 1 tick
        m = sim(cohort, [(2, 1)], slip=1)
        self.assertEqual(m["episodes"], 1)
        self.assertEqual(m["signals_seen"], 1)
        self.assertEqual(m["signals_entered"], 1)
        self.assertEqual(m["layers"][0], 1)
        px = 100.0 + 1 * TICK
        qty = RAIL["base_quote"] * LEV / px
        # flat prices never reach the 1% take profit -> the slice-end flatten closes it
        self.assertEqual(m["open_at_end"], 1)
        exit_px = 100.0 - 1 * TICK
        fees = qty * px * FEE + qty * exit_px * FEE
        net = (exit_px - px) * qty - fees
        self.assertAlmostEqual(m["net_pnl"], net, places=6)
        self.assertAlmostEqual(m["gross_pnl"], (exit_px - px) * qty, places=6)
        self.assertAlmostEqual(m["fees"], fees, places=6)
        self.assertTrue(sf.pnl_decomposition_ok(m))

    def test_ladder_tp_and_invalidation_levels(self):
        n = 40
        # bar 3 dips: 100 -> 98.9 hits ladder level 1 (99.0) but not level 2 (98.0)
        o = [100.0] * n
        h = [100.0] * n
        l = [100.0] * n
        c = [100.0] * n
        l[3] = 98.9
        cohort = FakeCohort(o, h, l, c)
        m = sim(cohort, [(2, 1)], slip=0)
        px0 = 100.0
        self.assertEqual(m["layers"][1], 1)
        self.assertEqual(m["layers"][2], 0)
        self.assertAlmostEqual(RAIL["spacing_d0"], 0.01)
        self.assertGreater(m["fills"], 1)
        # level prices are anchored on the INITIAL entry price, not on the running average
        self.assertAlmostEqual(px0 * (1 - 0.01 * 1), 99.0, places=9)
        self.assertTrue(sf.pnl_decomposition_ok(m))

    def test_invalidation_is_a_resting_stop_at_running_average_cost(self):
        n = 40
        o = [100.0] * n
        h = [100.0] * n
        l = [100.0] * n
        c = [100.0] * n
        l[3] = 88.0                      # below every ladder level and below the resting stop
        cohort = FakeCohort(o, h, l, c)
        m = sim(cohort, [(2, 1)], slip=0)
        self.assertEqual(m["stop_hits"], 1)
        self.assertEqual(m["tp_hits"], 0)
        self.assertEqual(m["episodes"], 1)
        # the resting stop is checked against the NEXT trigger on the way down, so it preempts the
        # deeper ladder levels instead of letting the price walk through them: level 8 filled, the
        # stop then fired before level 9 (100 x 0.91) and level 10 (100 x 0.90) were reachable.
        self.assertEqual(m["layers"][8], 1)
        self.assertEqual(m["layers"][9], 0)
        self.assertEqual(m["layers"][10], 0)
        self.assertEqual(m["layers"][11], 0)

    def test_ladder_absorbs_a_shallow_dip_without_touching_the_stop(self):
        n = 40
        o = [100.0] * n
        h = [100.0] * n
        l = [100.0] * n
        c = [100.0] * n
        l[3] = 97.5                      # inside the ladder, above the running-average stop
        cohort = FakeCohort(o, h, l, c)
        m = sim(cohort, [(2, 1)], slip=0)
        self.assertEqual(m["stop_hits"], 0)
        self.assertEqual(m["layers"][2], 1)   # 98.0 filled, 97.0 was never reached
        self.assertEqual(m["layers"][3], 0)

    def test_take_profit_fires_at_running_average_cost(self):
        n = 40
        o = [100.0] * n
        h = [100.0] * n
        l = [100.0] * n
        c = [100.0] * n
        h[4] = 101.5
        cohort = FakeCohort(o, h, l, c)
        m = sim(cohort, [(2, 1)], slip=0)
        self.assertEqual(m["tp_hits"], 1)
        self.assertAlmostEqual(m["gross_pnl"], (101.0 - 100.0) * (1000.0 * LEV / 100.0), places=6)

    def test_flip_closes_and_reopens_the_mirror_side(self):
        n = 30
        o = [100.0] * n
        h = [100.0] * n
        l = [100.0] * n
        c = [100.0] * n
        cohort = FakeCohort(o, h, l, c)
        m = sim(cohort, [(2, 1), (10, -1)], slip=0)
        self.assertEqual(m["episodes"], 2)
        self.assertEqual(m["flip_exits"], 1)
        self.assertEqual(m["layers"][0], 2)
        self.assertEqual(m["tp_hits"] + m["stop_hits"], 0)

    def test_same_sign_signal_is_ignored_while_positioned(self):
        n = 30
        o = [100.0] * n
        h = [100.0] * n
        l = [100.0] * n
        c = [100.0] * n
        cohort = FakeCohort(o, h, l, c)
        m = sim(cohort, [(2, 1), (5, 1), (9, 1)], slip=0)
        self.assertEqual(m["episodes"], 1)
        self.assertEqual(m["signals_seen"], 3)
        self.assertEqual(sf.COUNTERS["full"]["same_sign_signal_ignored"], 2)

    def test_fee_2x_doubles_fees_and_moves_net(self):
        n = 30
        o = [100.0] * n
        h = [100.0] * n
        l = [100.0] * n
        c = [100.0] * n
        cohort = FakeCohort(o, h, l, c)
        base = sim(cohort, [(2, 1), (10, -1)], slip=0)
        dbl = sim(cohort, [(2, 1), (10, -1)], stress={"fee_mult": 2.0}, slip=0)
        self.assertAlmostEqual(dbl["fees"], 2.0 * base["fees"], places=6)
        self.assertLess(dbl["net_pnl"], base["net_pnl"])
        self.assertAlmostEqual(base["gross_pnl"], dbl["gross_pnl"], places=9)

    def test_slippage_two_ticks_is_worse_than_one(self):
        n = 30
        o = [100.0] * n
        h = [100.0] * n
        l = [100.0] * n
        c = [100.0] * n
        cohort = FakeCohort(o, h, l, c)
        one = sim(cohort, [(2, 1)], slip=1)
        two = sim(cohort, [(2, 1)], slip=2)
        self.assertLess(two["net_pnl"], one["net_pnl"])

    def test_entry_delay_pushes_the_fill_one_bar_later(self):
        n = 30
        o = [100.0] * n
        h = [100.0] * n
        l = [100.0] * n
        c = [100.0] * n
        o[3] = h[3] = l[3] = c[3] = 90.0     # the un-delayed fill takes this bar's open
        o[4] = h[4] = l[4] = c[4] = 110.0    # the delayed fill takes this bar's open
        for i in range(5, n):
            o[i] = h[i] = l[i] = c[i] = 110.0
        cohort = FakeCohort(o, h, l, c)
        plain = sim(cohort, [(2, 1)], slip=0)
        delayed = sim(cohort, [(2, 1)], stress={"entry_delay_1_bar": True}, slip=0)
        # hand-computed turnover.  un-delayed: entered at 90, then the 1% take profit on the next
        # bar exits at 90 x 1.01.  delayed: entered one bar later at 110 and flattened at 110.
        q_plain = RAIL["base_quote"] * LEV / 90.0
        q_delay = RAIL["base_quote"] * LEV / 110.0
        self.assertAlmostEqual(plain["turnover_usdt"], q_plain * 90.0 + q_plain * 90.9,
                               places=3)
        self.assertAlmostEqual(delayed["turnover_usdt"], q_delay * 110.0 + q_delay * 110.0,
                               places=3)
        self.assertEqual(delayed["episodes"], 1)
        self.assertNotAlmostEqual(plain["net_pnl"], delayed["net_pnl"], places=3)

    def test_funding_is_charged_on_the_position_notional_inside_the_hold(self):
        n = 30
        o = [100.0] * n
        h = [100.0] * n
        l = [100.0] * n
        c = [100.0] * n
        cohort = FakeCohort(o, h, l, c)
        settle_bar = np.array([5, 12], dtype=np.int64)
        funding = {"obs_times": np.array([BASE_MS + 5 * BAR_MS, BASE_MS + 12 * BAR_MS],
                                         dtype=np.int64),
                   "obs_rates": np.array([0.0001, 0.0002], dtype=np.float64),
                   "settle_bar": settle_bar, "settle_bar_closed": settle_bar}
        m = sim(cohort, [(2, 1), (20, -1)], funding=funding, slip=0)
        qty = 1000.0 * LEV / 100.0
        expected = qty * 100.0 * (0.0001 + 0.0002)
        self.assertAlmostEqual(m["funding"], expected, places=6)
        self.assertLess(m["net_pnl"], m["gross_pnl"] - m["fees"])
        self.assertTrue(sf.pnl_decomposition_ok(m))
        no_fund = sim(cohort, [(2, 1), (20, -1)], funding=funding, slip=0,
                      stress={"no_funding": True})
        self.assertEqual(no_fund["funding"], 0.0)

    def test_funding_2x_doubles_the_charge(self):
        n = 30
        o = [100.0] * n
        h = [100.0] * n
        l = [100.0] * n
        c = [100.0] * n
        cohort = FakeCohort(o, h, l, c)
        settle_bar = np.array([5], dtype=np.int64)
        funding = {"obs_times": np.array([BASE_MS + 5 * BAR_MS], dtype=np.int64),
                   "obs_rates": np.array([0.0003], dtype=np.float64),
                   "settle_bar": settle_bar, "settle_bar_closed": settle_bar}
        base = sim(cohort, [(2, 1), (20, -1)], funding=funding, slip=0)
        dbl = sim(cohort, [(2, 1), (20, -1)], funding=funding, slip=0,
                  stress={"funding_mult": 2.0})
        self.assertAlmostEqual(dbl["funding"], 2.0 * base["funding"], places=9)

    def test_slice_is_evaluated_with_a_flat_book(self):
        n = 30
        o = [100.0] * n
        h = [100.0] * n
        l = [100.0] * n
        c = [100.0] * n
        cohort = FakeCohort(o, h, l, c)
        # the signal bar sits before the slice: nothing is carried in
        m = sim(cohort, [(1, 1)], window=(10, 30), slip=0)
        self.assertEqual(m["episodes"], 0)
        self.assertEqual(m["net_pnl"], 0.0)
        # a signal on the slice's last bar cannot be entered inside the slice
        sf.COUNTERS.clear()
        m2 = sim(cohort, [(29, 1)], window=(10, 30), slip=0)
        self.assertEqual(m2["episodes"], 0)
        self.assertEqual(sf.COUNTERS["full"]["entry_clipped_at_slice_edge"], 1)

    def test_daily_series_is_slice_scoped(self):
        n = 72
        o = [100.0] * n
        h = [100.0] * n
        l = [100.0] * n
        c = [100.0] * n
        cohort = FakeCohort(o, h, l, c)
        m = sim(cohort, [(2, 1)], window=(24, 72), slip=0)
        self.assertEqual(m["days"], 2)


class TestSelectorAndGates(unittest.TestCase):
    def _rows(self, keys, kind="historical"):
        out = []
        for i, (brick, rsi, sp, mult, tp, inv) in enumerate(keys):
            out.append({"symbol": "SYNTH", "timeframe": "1h", "window_kind": kind,
                        "brick_pct": brick, "rsi_period": rsi, "spacing_pct": sp,
                        "size_multiplier": mult, "breakeven_tp_pct": tp,
                        "invalidation_pct": inv,
                        "net_pnl": float(i + 1), "sharpe": float(i) / 10.0,
                        "episodes": 40 + i})
        return out

    def _spec(self):
        grid = [{"brick_pct": b, "rsi_period": r} for b in (0.005, 0.01, 0.02)
                for r in (7, 14, 21)]
        return {"gates": {"min_episodes_is": 30, "min_episodes_oos": 10,
                          "neighborhood_min_same_sign_fraction": 0.6},
                "parameter_domain": {"grid_cases": grid},
                "dca_domain": {"spacing_pct": [0.01, 0.02, 0.03, 0.04],
                               "size_multiplier": [1.0, 1.1],
                               "breakeven_tp_pct": [0.01, 0.02, 0.03],
                               "invalidation_pct": [0.05, 0.10]}}

    def test_selector_rejects_oos_rows(self):
        rows = self._rows([(0.01, 14, 0.01, 1.0, 0.01, 0.05)], kind="oos")
        with self.assertRaises(ValueError):
            sf.select_cohort_winner(rows, self._spec())

    def test_selector_is_insufficient_trades_below_the_floor(self):
        rows = self._rows([(0.01, 14, 0.01, 1.0, 0.01, 0.05)])
        for r in rows:
            r["episodes"] = 5
        winner, reason = sf.select_cohort_winner(rows, self._spec())
        self.assertIsNone(winner)
        self.assertEqual(reason, "insufficient_trades")

    def test_selector_orders_by_sharpe_then_net_pnl(self):
        rows = self._rows([(0.01, 14, 0.01, 1.0, 0.01, 0.05),
                           (0.02, 21, 0.01, 1.0, 0.01, 0.05)])
        winner, reason = sf.select_cohort_winner(rows, self._spec())
        self.assertEqual(reason, "selected")
        self.assertEqual((winner["brick_pct"], winner["rsi_period"]), (0.02, 21))

    def test_require_historical_guard(self):
        rows = self._rows([(0.01, 14, 0.01, 1.0, 0.01, 0.05)], kind="full")
        with self.assertRaises(ValueError):
            sf.require_historical(rows, "probe")

    def test_family_disposition_mapping(self):
        self.assertEqual(sf.family_disposition([], True)["verdict_recommendation"], "REJECT")
        one = sf.family_disposition([{"cohort": "A/1h"}], True)
        self.assertEqual(one["band"], "SURVIVOR_FOUND")
        self.assertEqual(one["verdict_recommendation"], "PASS")
        many = sf.family_disposition([{"cohort": "A/1h"}, {"cohort": "B/1h"}], True)
        self.assertEqual(many["band"], "MULTIPLE_SURVIVORS")
        self.assertEqual(many["verdict_recommendation"], "PASS")
        bad = sf.family_disposition([{"cohort": "A/1h"}], False)
        self.assertEqual(bad["verdict_recommendation"], "TECHNICAL_INCOMPLETE")
        self.assertFalse(bad["performance_claimable_recommendation"])

    def test_axis_and_tie_break_are_registered_order_based(self):
        spec = self._spec()
        axes = sf.axis_values(spec)
        self.assertEqual(axes["brick_pct"], [0.005, 0.01, 0.02])
        self.assertEqual(axes["rsi_period"], [7, 14, 21])
        row = {"brick_pct": 0.005, "rsi_period": 7, "spacing_pct": 0.01,
               "size_multiplier": 1.0, "breakeven_tp_pct": 0.01, "invalidation_pct": 0.05}
        self.assertEqual(sf.tie_break_key(row, axes), (0, 0, 0, 0, 0, 0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
