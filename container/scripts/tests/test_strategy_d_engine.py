#!/usr/bin/env python3
"""Executable check for the Strategy D execution engine (clock-hour legs + window-bounded DCA rail).

Runs inside the qlib container:
    container exec qlib-run env SD_ENGINE_PATH=/scripts/40_strategy_d_run.py \
        /opt/venv/bin/python /scripts/tests/test_strategy_d_engine.py
and on the host with a numpy interpreter:
    SD_ENGINE_PATH=<repo>/container/scripts/40_strategy_d_run.py python3 <this file>

Drives the pure functions with synthetic hourly bars whose fills, fees and funding charges are
known by hand, so a regression in the clock mapping, the window-bounded rail, the long/short
mirror, the per-fill fee ledger, the independent gross accumulator or the funding-exposure rule
fails loudly instead of silently changing the science.  No market data, no container state, no
network: stdlib unittest + numpy only.
"""
import importlib.util
import os
import unittest

import numpy as np

ENGINE = os.environ.get("SD_ENGINE_PATH", "/scripts/40_strategy_d_run.py")
_spec = importlib.util.spec_from_file_location("sd_engine", ENGINE)
sd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sd)

BAR_MS = 3600000
BASE_MS = 1767225600000            # 2026-01-01T00:00:00Z (a clean UTC midnight)
RAIL = {"base_quote": 1000.0, "size_multiplier": 1.1, "spacing_d0": 0.02,
        "tp": 0.01, "invalidation": 0.05}
LEV = 10.0
P0 = 100.0
LONG = {"leg_long": 1, "leg_short": 0, "leg_secondary": 0}
SHORT = {"leg_long": 0, "leg_short": 1, "leg_secondary": 0}
SECONDARY = {"leg_long": 0, "leg_short": 0, "leg_secondary": 1}
ALL = {"leg_long": 1, "leg_short": 1, "leg_secondary": 1}


class FakeCohort:
    """Hourly bars on a clean UTC grid; bar i opens at BASE_MS + i * 1h."""

    def __init__(self, closes, highs=None, lows=None, opens=None, base_ms=BASE_MS):
        n = len(closes)
        self.symbol = "SYNTH"
        self.timeframe = "1h"
        self.close = np.array(closes, dtype=np.float64)
        self.open = np.array(opens if opens is not None else closes, dtype=np.float64)
        self.high = np.array(highs if highs is not None else closes, dtype=np.float64)
        self.low = np.array(lows if lows is not None else closes, dtype=np.float64)
        self.n = n
        self.open_time_ms = np.array([base_ms + i * BAR_MS for i in range(n)], dtype=np.int64)
        self.bar_ms = BAR_MS
        hours = (self.open_time_ms // 3600000) % 24
        self.leg_boundaries = {leg["name"]: np.flatnonzero(hours == leg["start_hour"]).astype(
            np.int64) for leg in sd.LEGS}
        self.price_increment = 0.0
        self.taker_fee = 0.0
        self.leverage = LEV
        self.margin_maint = 0.1

    def slice(self, _a, _b):
        return 0, self.n


def mk_series(cohort, settlements=()):
    """settlements: iterable of (epoch_ms, rate)."""
    times = np.array([int(t) for t, _r in settlements], dtype=np.int64)
    rates = np.array([float(r) for _t, r in settlements], dtype=np.float64)
    containing = (np.searchsorted(cohort.open_time_ms, times, side="left") - 1
                  if len(times) else np.array([], dtype=np.int64))
    return {"label": "synthetic", "counts": {}, "obs_times": times, "obs_rates": rates,
            "settle_bar": containing.astype(np.int64), "first_obs_ms": None, "last_obs_ms": None}


def run(cohort, params, rail=None, series=None, slip=0.0, stress=None, kind="full", diag=False):
    return sd.simulate(cohort, params, rail or RAIL, (0, cohort.n), stress or {}, slip, kind,
                       series or mk_series(cohort), diag=diag)


def flat(n=48, price=P0):
    closes = [price] * n
    return FakeCohort(closes, closes, closes, closes)


def ms_at(day_offset, hour):
    return BASE_MS + day_offset * 86400000 + hour * BAR_MS


def reset_counters():
    sd.COUNTERS.clear()


# ---------------------------------------------------------------------------
# 1. the registered clock mapping
# ---------------------------------------------------------------------------

class TestClockMapping(unittest.TestCase):

    def setUp(self):
        reset_counters()

    def test_long_leg_is_the_15_00_bar_only(self):
        c = flat(48)
        eps = sd.window_episodes(c, 0, 48, (1, 0, 0), 0, 0, "full")
        self.assertEqual([(b, t, li) for b, t, li in eps], [(15, 15, 0), (39, 39, 0)])
        self.assertEqual(int(c.open_time_ms[15] % 86400000) // 3600000, 15)

    def test_short_leg_spans_01_00_to_05_00(self):
        c = flat(48)
        eps = sd.window_episodes(c, 0, 48, (0, 1, 0), 0, 0, "full")
        self.assertEqual(eps, [(1, 4, 1), (25, 28, 1)])
        self.assertEqual(int(c.open_time_ms[4] + c.bar_ms) % 86400000 // 3600000, 5)

    def test_secondary_leg_spans_21_00_to_24_00(self):
        c = flat(48)
        eps = sd.window_episodes(c, 0, 48, (0, 0, 1), 0, 0, "full")
        self.assertEqual(eps, [(21, 23, 2), (45, 47, 2)])
        self.assertEqual(int(c.open_time_ms[23] + c.bar_ms) % 86400000 // 3600000, 0)

    def test_all_legs_case_is_the_disjoint_union_in_time_order(self):
        c = flat(48)
        eps = sd.window_episodes(c, 0, 48, (1, 1, 1), 0, 0, "full")
        self.assertEqual([li for _b, _t, li in eps], [1, 0, 2, 1, 0, 2])
        self.assertEqual(sd.case_label((1, 1, 1)), "long+short+secondary_long")
        # strictly non-overlapping: every window starts after the previous one flattened
        for (b1, t1, _l1), (b2, _t2, _l2) in zip(eps, eps[1:]):
            self.assertGreater(b2, t1, "registered windows must never overlap")

    def test_boundary_alt_shifts_the_whole_schedule_one_bar_earlier(self):
        c = flat(48)
        eps = sd.window_episodes(c, 1, 48, (1, 1, 1), -1, 0, "boundary_alt_full")
        # slice-local indices; the 01:00 window of day 0 shifts to bar 0, outside the slice
        self.assertEqual([li for _b, _t, li in eps], [0, 2, 1, 0, 2])
        self.assertEqual([b for b, _t, _l in eps], [13, 19, 23, 37, 43])
        self.assertEqual(sd.COUNTERS["boundary_alt_full"]["window_clipped_at_slice_edge"], 1)
        c = flat(48)
        eps = sd.window_episodes(c, 0, 48, (1, 1, 1), -1, 0, "boundary_alt_full")
        self.assertEqual([b for b, _t, _l in eps], [0, 14, 20, 24, 38, 44])
        self.assertEqual(sd.counters_total("entry_bar_not_at_registered_boundary"), 0)
        self.assertEqual(sd.counters_total("exit_bar_not_at_window_end"), 0)

    def test_entry_delay_keeps_the_holding_length(self):
        c = flat(48)
        eps = sd.window_episodes(c, 0, 48, (1, 0, 0), 0, 1, "entry_delay_1_bar")
        self.assertEqual(eps, [(16, 16, 0), (40, 40, 0)])
        eps = sd.window_episodes(c, 0, 48, (0, 1, 0), 0, 1, "entry_delay_1_bar")
        self.assertEqual(eps, [(2, 5, 1), (26, 29, 1)])

    def test_a_corrupted_boundary_set_trips_the_clock_guard(self):
        """The guard is live: an off-by-one boundary set can never pass silently."""
        c = FakeCohort([P0] * 48, [P0] * 48, [P0] * 48, [P0] * 48)
        c.leg_boundaries["long"] = c.leg_boundaries["long"] + 1
        eps = sd.window_episodes(c, 0, 48, (1, 0, 0), 0, 0, "full")
        self.assertEqual(len(eps), 2)
        self.assertEqual(sd.COUNTERS["full"]["entry_bar_not_at_registered_boundary"], 2)
        self.assertEqual(sd.COUNTERS["full"]["exit_bar_not_at_window_end"], 2)

    def test_unregistered_case_fails_closed(self):
        c = flat(8)
        with self.assertRaises(SystemExit):
            sd.simulate(c, {"leg_long": 1, "leg_short": 1, "leg_secondary": 0, "bogus": 1} if False
                       else {"leg_long": 0, "leg_short": 0, "leg_secondary": 0}, RAIL, (0, 8),
                       {}, 0.0, "full", mk_series(c))


# ---------------------------------------------------------------------------
# 2. the long rail
# ---------------------------------------------------------------------------

class TestLongRail(unittest.TestCase):

    def setUp(self):
        reset_counters()

    def test_flat_long_pays_entry_and_exit_fees_only(self):
        c = flat(24)
        c.taker_fee = 0.0005
        m = run(c, LONG)
        self.assertEqual(m["episodes"], 1)
        self.assertEqual(m["tp_hits"] + m["stop_hits"] + m["time_exits"], 1)
        self.assertAlmostEqual(m["net_pnl"], -2 * (1000 * LEV * 0.0005), places=6)
        self.assertAlmostEqual(m["gross_pnl"], 0.0, places=6)
        self.assertAlmostEqual(m["gross_pnl"] - m["fees"] - m["funding"], m["net_pnl"], places=9)

    def test_long_tp_fires_on_the_high_of_the_window_bar(self):
        closes = [P0] * 24
        highs = list(closes)
        highs[15] = P0 * 1.02
        lows = list(closes)
        c = FakeCohort(closes, highs, lows, closes)
        m = run(c, LONG)
        self.assertEqual(m["tp_hits"], 1)
        # TP at running_average_cost x (1 + tp) = 101 on a single tranche
        self.assertAlmostEqual(m["gross_pnl"], (P0 * 1.01 - P0) * (1000 * LEV / P0), places=6)

    def test_ladder_walk_then_resting_stop_hand_checked(self):
        closes = [P0] * 24
        highs, lows, opens = list(closes), list(closes), list(closes)
        lows[15] = P0 * 0.90           # deep enough to walk the ladder and reach the stop
        c = FakeCohort(closes, highs, lows, opens)
        m = run(c, LONG)
        self.assertEqual(m["stop_hits"], 1)
        qty = 1000 * LEV / P0
        cost = qty * P0
        k = 1
        while True:
            stop = (cost / qty) * (1.0 - RAIL["invalidation"])
            lvl = P0 * (1.0 - RAIL["spacing_d0"] * k)
            trig, is_stop = (lvl, False) if k <= 10 and lvl >= stop else (stop, True)
            if lows[15] > trig:
                break
            if is_stop:
                xpx = trig if opens[15] >= trig else opens[15]
                break
            q = 1000 * (RAIL["size_multiplier"] ** k) * LEV / trig
            qty += q
            cost += q * trig
            k += 1
        self.assertAlmostEqual(m["gross_pnl"], qty * xpx - cost, places=6)

    def test_a_level_below_the_resting_stop_can_never_fill(self):
        closes = [P0] * 24
        highs, lows, opens = list(closes), list(closes), list(closes)
        lows[15] = 0.01                    # everything triggers
        c = FakeCohort(closes, highs, lows, opens)
        m = run(c, LONG)
        self.assertEqual(m["stop_hits"], 1)
        self.assertLess(m["fills"], 12, "the stop is reached before the deep ladder levels")

    def test_fee_is_charged_on_every_fill_and_fee_2x_moves_net_not_gross(self):
        c = flat(24)
        c.taker_fee = 0.0005
        m = run(c, LONG)
        m2 = run(c, LONG, stress={"fee_mult": 2.0})
        self.assertAlmostEqual(m2["fees"], 2 * m["fees"], places=6)
        self.assertNotAlmostEqual(m2["net_pnl"], m["net_pnl"], places=6)
        self.assertAlmostEqual(m2["gross_pnl"], m["gross_pnl"], places=9)


# ---------------------------------------------------------------------------
# 3. the short mirror
# ---------------------------------------------------------------------------

class TestShortMirror(unittest.TestCase):

    def setUp(self):
        reset_counters()

    def test_flat_short_pays_fees_only_and_mirrors_the_exit(self):
        c = flat(24)
        c.taker_fee = 0.0005
        m = run(c, SHORT)
        self.assertEqual(m["episodes"], 1)
        self.assertAlmostEqual(m["net_pnl"], -2 * (1000 * LEV * 0.0005), places=6)

    def test_short_profits_when_the_window_falls(self):
        closes = [P0] * 24
        opens, highs, lows = list(closes), list(closes), list(closes)
        for t in range(1, 5):                      # 01:00-05:00 window, -0.1% per bar
            closes[t] = P0 * (1.0 - 0.001 * t)
            opens[t] = closes[t - 1]
            highs[t] = closes[t - 1]
            lows[t] = closes[t]
        c = FakeCohort(closes, highs, lows, opens)
        m = run(c, SHORT)
        self.assertEqual(m["episodes"], 1)
        self.assertEqual(m["stop_hits"] + m["tp_hits"], 0, "the slow drift must not hit the rail")
        self.assertEqual(m["time_exits"], 1)
        qty = 1000 * LEV / P0
        self.assertAlmostEqual(m["gross_pnl"], (P0 - closes[4]) * qty, places=6)

    def test_short_tp_fires_on_the_low(self):
        closes = [P0] * 24
        opens, highs, lows = list(closes), list(closes), list(closes)
        lows[1] = P0 * 0.97
        c = FakeCohort(closes, highs, lows, opens)
        m = run(c, SHORT)
        self.assertEqual(m["tp_hits"], 1)
        self.assertAlmostEqual(m["gross_pnl"], (P0 - P0 * 0.99) * (1000 * LEV / P0), places=6)

    def test_short_stop_gap_fills_at_the_bar_open_not_the_stop(self):
        """A gap UP through the resting stop fills at max(stop, bar open), on a held-through bar."""
        closes = [P0] * 24
        opens, highs, lows = list(closes), list(closes), list(closes)
        opens[2] = P0 * 1.10                 # 02:00 bar gaps up straight through the stop
        highs[2] = P0 * 1.12
        closes[2] = P0 * 1.05
        c = FakeCohort(closes, highs, lows, opens)
        # wide spacing keeps the first ladder level (110) above the runway to the stop (105)
        rail = {"base_quote": 1000.0, "size_multiplier": 1.0, "spacing_d0": 0.10,
                "tp": 0.01, "invalidation": 0.05}
        m = run(c, SHORT, rail=rail)
        self.assertEqual(m["stop_hits"], 1)
        self.assertEqual(m["fills"], 2, "initial entry + the gapped stop flatten, no ladder add")
        qty = 1000 * LEV / P0
        self.assertAlmostEqual(m["gross_pnl"], (P0 - opens[2]) * qty, places=6)

    def test_short_ladder_walks_up_then_the_stop_is_reached(self):
        closes = [P0] * 24
        opens, highs, lows = list(closes), list(closes), list(closes)
        highs[1] = P0 * 1.20
        c = FakeCohort(closes, highs, lows, opens)
        m = run(c, SHORT)
        self.assertEqual(m["stop_hits"], 1)
        qty = 1000 * LEV / P0
        cost = qty * P0
        k = 1
        while True:
            stop = (cost / qty) * (1.0 + RAIL["invalidation"])
            lvl = P0 * (1.0 + RAIL["spacing_d0"] * k)
            trig, is_stop = (lvl, False) if k <= 10 and lvl <= stop else (stop, True)
            if highs[1] < trig:
                break
            if is_stop:
                xpx = trig if opens[1] <= trig else opens[1]
                break
            q = 1000 * (RAIL["size_multiplier"] ** k) * LEV / trig
            qty += q
            cost += q * trig
            k += 1
        self.assertAlmostEqual(m["gross_pnl"], (cost - qty * xpx), places=6)


# ---------------------------------------------------------------------------
# 4. funding exposure of the hourly windows
# ---------------------------------------------------------------------------

class TestFundingExposure(unittest.TestCase):

    def setUp(self):
        reset_counters()

    def test_long_pays_the_settlement_on_its_closing_boundary(self):
        c = flat(24)
        series = mk_series(c, [(ms_at(0, 16) + 28, 0.0001)])   # 16:00:00.028Z, measured jitter
        m = run(c, LONG, series=series)
        qty = 1000 * LEV / P0
        self.assertAlmostEqual(m["funding"], qty * P0 * 0.0001, places=6)
        self.assertAlmostEqual(m["net_pnl"], m["gross_pnl"] - m["fees"] - m["funding"], places=9)

    def test_settlement_outside_the_window_is_not_charged(self):
        c = flat(24)
        series = mk_series(c, [(ms_at(0, 8) + 28, 0.0001)])
        m = run(c, LONG, series=series)
        self.assertEqual(m["funding"], 0.0)

    def test_jitter_beyond_the_tolerance_is_not_charged(self):
        c = flat(24)
        series = mk_series(c, [(ms_at(0, 16) + 5000, 0.0001)])
        m = run(c, LONG, series=series)
        self.assertEqual(m["funding"], 0.0)

    def test_short_receives_positive_funding_on_the_short_window(self):
        c = flat(24)
        series = mk_series(c, [(ms_at(0, 2) + 12, 0.0001)])    # inside 01:00-05:00
        m = run(c, SHORT, series=series)
        self.assertLess(m["funding"], 0.0, "a positive rate is a credit to a short position")

    def test_funding_2x_doubles_and_no_funding_zeroes(self):
        c = flat(24)
        series = mk_series(c, [(ms_at(0, 16) + 28, 0.0001)])
        m = run(c, LONG, series=series)
        m2 = run(c, LONG, series=series, stress={"funding_mult": 2.0})
        m3 = run(c, LONG, series=series, stress={"no_funding": True})
        self.assertAlmostEqual(m2["funding"], 2 * m["funding"], places=6)
        self.assertEqual(m3["funding"], 0.0)
        # dropping a cost raises the net result by exactly that cost
        self.assertAlmostEqual(m3["net_pnl"] - m["net_pnl"], m["funding"], places=6)


# ---------------------------------------------------------------------------
# 5. the slice-scoped daily equity series
# ---------------------------------------------------------------------------

class TestDailySeries(unittest.TestCase):

    def setUp(self):
        reset_counters()

    def test_daily_series_covers_exactly_the_evaluated_slice(self):
        c = flat(96)
        forced = {}
        orig = sd.simulate

        def spy(*a, **kw):
            forced["m"] = orig(*a, **kw)
            return forced["m"]
        spy(c, LONG, RAIL, (24, 72), {}, 0.0, "oos", mk_series(c), diag=True)
        self.assertEqual(forced["m"]["diagnostics"]["daily_equity_days"], 2)
        self.assertEqual(forced["m"]["days"], 2)

    def test_historical_marks_are_forward_filled_to_the_end_of_the_slice(self):
        c = flat(72)
        m = sd.simulate(c, LONG, RAIL, (0, 72), {}, 0.0, "full", mk_series(c), diag=True)
        self.assertEqual(m["diagnostics"]["daily_equity_days"], 3)
        self.assertEqual(len(m["diagnostics"]["daily_equity"]), 3)


# ---------------------------------------------------------------------------
# 6. selector / cohort gate
# ---------------------------------------------------------------------------

def synth_row(cohort, case, spacing, mult, tp, inval, net_pnl, sharpe, episodes, kind="historical"):
    return {"symbol": cohort, "timeframe": "1h", "window_kind": kind,
            "leg_long": case[0], "leg_short": case[1], "leg_secondary": case[2],
            "spacing_pct": spacing, "size_multiplier": mult, "breakeven_tp_pct": tp,
            "invalidation_pct": inval, "net_pnl": net_pnl, "sharpe": sharpe,
            "episodes": episodes, "ending_equity": 30000 + net_pnl, "fees": 0.0, "funding": 0.0,
            "gross_pnl": net_pnl, "max_dd_usdt": 0.0, "max_dd_pct": 0.0,
            "max_effective_leverage": 3.0, "capital_utilization": 0.1,
            "annualized_return": 0.1, "cagr": 0.1, "days": 100, "years": 1.0,
            "min_entry_equity": 30000.0, "tp_hits": episodes, "stop_hits": 0, "time_exits": 0,
            "open_at_end": 0, "margin_calls": 0, "halted": False, "windows_seen": episodes,
            "windows_entered": episodes, "bars_in_market": episodes, "fills": episodes,
            "turnover_usdt": 0.0, "total_return_pct": 0.0}


SPEC = {
    "family_id": "synthetic", "round_id": "synthetic-r1", "run_id": "synthetic-r1-u1",
    "parameter_domain": {"grid_cases": [dict(zip(sd.CASE_FIELDS, c)) for c in sd.CASE_ORDER]},
    "dca_domain": {"base_quote": 1000, "spacing_pct": [0.01, 0.02],
                   "size_multiplier": [1.0, 1.1], "breakeven_tp_pct": [0.01, 0.02],
                   "invalidation_pct": [0.05, 0.10]},
    "gates": {"min_episodes_is": 200, "min_episodes_oos": 60,
              "neighborhood_min_same_sign_fraction": 0.6,
              "min_oos_sharpe": 0.40, "min_oos_annualized_return": 0.0},
}
WIN_CELL = ((1, 0, 0), 0.01, 1.0, 0.01, 0.05)


def full_product_rows(kind, cohort="BTCUSDT", net_pnl=5.0, sharpe=1.0, episodes=300):
    """Every cell of the registered joint product (needed by the neighbourhood test)."""
    rows = []
    for case in sd.CASE_ORDER:
        for sp in SPEC["dca_domain"]["spacing_pct"]:
            for mult in SPEC["dca_domain"]["size_multiplier"]:
                for tp in SPEC["dca_domain"]["breakeven_tp_pct"]:
                    for inval in SPEC["dca_domain"]["invalidation_pct"]:
                        rows.append(synth_row(cohort, case, sp, mult, tp, inval, net_pnl,
                                              sharpe, episodes, kind))
    return rows


class TestSelector(unittest.TestCase):

    def setUp(self):
        reset_counters()

    def test_selector_refuses_oos_rows(self):
        rows = [synth_row("BTCUSDT", (1, 0, 0), 0.01, 1.0, 0.01, 0.05, 10.0, 1.0, 300, "oos")]
        with self.assertRaises(ValueError):
            sd.select_cohort_winner(rows, SPEC)

    def test_insufficient_trades_culls_the_cohort(self):
        rows = [synth_row("BTCUSDT", (1, 0, 0), 0.01, 1.0, 0.01, 0.05, 10.0, 1.0, 199)]
        got, reason = sd.select_cohort_winner(rows, SPEC)
        self.assertIsNone(got)
        self.assertEqual(reason, "insufficient_trades")

    def test_no_qualifying_candidate_when_every_case_loses(self):
        rows = [synth_row("BTCUSDT", (1, 0, 0), 0.01, 1.0, 0.01, 0.05, -10.0, 1.0, 300)]
        got, reason = sd.select_cohort_winner(rows, SPEC)
        self.assertIsNone(got)
        self.assertEqual(reason, "no_qualifying_candidate")

    def test_tie_break_is_the_registered_axis_index_order(self):
        a = synth_row("BTCUSDT", (1, 0, 0), 0.01, 1.0, 0.01, 0.05, 10.0, 1.0, 300)
        b = synth_row("BTCUSDT", (0, 1, 0), 0.01, 1.0, 0.01, 0.05, 10.0, 1.0, 300)
        got, _ = sd.select_cohort_winner([b, a], SPEC)
        self.assertEqual(sd.cell_key(got), sd.cell_key(a), "lower registered case index wins")

    def test_neighbourhood_is_historical_only_and_counts_registered_steps(self):
        rows = full_product_rows("historical", net_pnl=5.0)
        winner = [r for r in rows if sd.cell_key(r) == WIN_CELL][0]
        nb = sd.cohort_neighbourhood(rows, winner, SPEC)
        # case +1, spacing +, mult +, tp +, inval +  (case -1 and the lower bounds are absent)
        self.assertEqual(nb["neighbours"], 5)
        self.assertTrue(nb["passed"])
        oos_rows = [dict(r, window_kind="oos", net_pnl=-5.0) for r in rows]
        with self.assertRaises(ValueError):
            sd.cohort_neighbourhood(oos_rows, winner, SPEC)
        with self.assertRaises(ValueError):
            sd.cohort_neighbourhood(rows + oos_rows, winner, SPEC)


class TestCohortGate(unittest.TestCase):

    def setUp(self):
        reset_counters()

    def build(self, **over):
        grid = {kind: full_product_rows(kind) for kind in sd.COHORT_GRID_KINDS}
        for kind, rows in grid.items():
            for r in rows:
                if sd.cell_key(r) == WIN_CELL:
                    r.update(over)
        return grid

    def test_all_requirements_met_is_a_survivor(self):
        out = sd.evaluate_cohort(SPEC, "BTCUSDT/1h", self.build())
        self.assertEqual(out["outcome"], "SURVIVOR")
        self.assertEqual(out["cull_reasons"], [])
        self.assertEqual(out["winner"], {"leg_long": 1, "leg_short": 0, "leg_secondary": 0,
                                         "spacing_pct": 0.01, "size_multiplier": 1.0,
                                         "breakeven_tp_pct": 0.01, "invalidation_pct": 0.05})
        for k in out["winner"]:
            self.assertIn(k, tuple(sd.CASE_FIELDS) + tuple(sd.DCA_AXES))
        self.assertEqual(out["winner_case_label"], "long")

    def test_oos_sharpe_floor_is_enforced(self):
        grid = self.build()
        for r in grid["oos"]:
            if sd.cell_key(r) == WIN_CELL:
                r.update({"net_pnl": 5.0, "sharpe": 0.39, "annualized_return": 0.5})
        out = sd.evaluate_cohort(SPEC, "BTCUSDT/1h", grid)
        self.assertIn("oos_economic", out["cull_reasons"])

    def test_negative_oos_annualized_return_is_rejected(self):
        grid = self.build()
        for r in grid["oos"]:
            if sd.cell_key(r) == WIN_CELL:
                r.update({"net_pnl": 5.0, "sharpe": 1.0, "annualized_return": -0.01})
        out = sd.evaluate_cohort(SPEC, "BTCUSDT/1h", grid)
        self.assertIn("oos_economic", out["cull_reasons"])

    def test_oos_episode_floor_culls_for_insufficient_trades(self):
        grid = self.build()
        for r in grid["oos"]:
            if sd.cell_key(r) == WIN_CELL:
                r.update({"episodes": 59})
        out = sd.evaluate_cohort(SPEC, "BTCUSDT/1h", grid)
        self.assertIn("insufficient_trades", out["cull_reasons"])

    def test_g8_cost_attrition_is_a_cohort_gate(self):
        grid = self.build()
        for r in grid["cost_attrition_40bps"]:
            if sd.cell_key(r) == WIN_CELL:
                r.update({"net_pnl": -1.0})
        out = sd.evaluate_cohort(SPEC, "BTCUSDT/1h", grid)
        self.assertIn("robustness_economic:cost_attrition_40bps", out["cull_reasons"])

    def test_execution_stress_grids_are_cohort_gates(self):
        grid = self.build()
        for r in grid["entry_delay_1_bar"]:
            if sd.cell_key(r) == WIN_CELL:
                r.update({"net_pnl": -1.0})
        out = sd.evaluate_cohort(SPEC, "BTCUSDT/1h", grid)
        self.assertIn("robustness_economic:entry_delay_1_bar", out["cull_reasons"])

    def test_full_window_failure_is_a_cohort_gate(self):
        grid = self.build()
        for r in grid["full"]:
            if sd.cell_key(r) == WIN_CELL:
                r.update({"net_pnl": -1.0})
        out = sd.evaluate_cohort(SPEC, "BTCUSDT/1h", grid)
        self.assertIn("full_economic", out["cull_reasons"])


class TestFamilyLevelItems(unittest.TestCase):

    def setUp(self):
        reset_counters()

    def test_timezone_fragility_hits_on_an_oos_sign_flip(self):
        case = (1, 0, 0)
        base = [(1, 0, 0), (0, 1, 0)]
        grid = {}
        for kind in ("oos", "full", "boundary_alt_oos", "boundary_alt_full"):
            grid[kind] = []
            for c in base:
                for sp in (0.01, 0.02):
                    grid[kind].append(synth_row("BTCUSDT", c, sp, 1.0, 0.01, 0.05,
                                                net_pnl=5.0, sharpe=1.0, episodes=300, kind=kind))
        for r in grid["boundary_alt_oos"]:
            if sd.cell_key(r) == (case, 0.01, 1.0, 0.01, 0.05):
                r["net_pnl"] = -5.0
        res = [{"cohort": "BTCUSDT/1h", "winner": {"leg_long": 1, "leg_short": 0,
                                                   "leg_secondary": 0, "spacing_pct": 0.01,
                                                   "size_multiplier": 1.0, "breakeven_tp_pct": 0.01,
                                                   "invalidation_pct": 0.05},
                "outcome": "SURVIVOR"}]
        out = sd.timezone_fragility_check(SPEC, res, {"BTCUSDT/1h": grid})
        self.assertTrue(out["hit"])
        self.assertTrue(out["evaluated"])

    def test_window_instability_needs_both_legs_non_positive_after_2024(self):
        def leg_m(recent):
            return {"episodes": 100, "diagnostics": {"pnl_by_year": {
                "2022": {"episodes": 10, "net_pnl": 50.0},
                "2024": {"episodes": 10, "net_pnl": recent},
                "2025": {"episodes": 10, "net_pnl": 0.0}}}}
        res = [{"cohort": "BTCUSDT/1h", "winner": {"leg_long": 1, "leg_short": 0,
                                                   "leg_secondary": 0, "spacing_pct": 0.01,
                                                   "size_multiplier": 1.0, "breakeven_tp_pct": 0.01,
                                                   "invalidation_pct": 0.05}, "outcome": "SURVIVOR"}]
        diag = {"BTCUSDT/1h": {"leg_runs": {"long_only": leg_m(-1.0), "short_only": leg_m(-2.0)}}}
        self.assertTrue(sd.window_instability_check(SPEC, res, diag)["hit"])
        diag = {"BTCUSDT/1h": {"leg_runs": {"long_only": leg_m(-1.0), "short_only": leg_m(3.0)}}}
        self.assertFalse(sd.window_instability_check(SPEC, res, diag)["hit"])


class TestDisposition(unittest.TestCase):

    def test_band_mapping(self):
        self.assertEqual(sd.family_disposition([], True)["verdict_recommendation"], "REJECT")
        self.assertEqual(sd.family_disposition([1], True)["disposition"], "SURVIVOR_FOUND")
        self.assertEqual(sd.family_disposition([1, 2], True)["disposition"], "MULTIPLE_SURVIVORS")
        self.assertEqual(sd.family_disposition([1, 2], True)["verdict_recommendation"], "PASS")
        self.assertEqual(sd.family_disposition([1], False)["verdict_recommendation"],
                         "TECHNICAL_INCOMPLETE")


class TestEngineConstants(unittest.TestCase):

    def test_axis_values_reads_grid_cases_as_dicts(self):
        """The registered case axis must be the case tuples, never a dict's key names."""
        spec = {"parameter_domain": {
                    "grid_cases": [dict(zip(sd.CASE_FIELDS, c)) for c in sd.CASE_ORDER]},
                "dca_domain": {"spacing_pct": [0.01, 0.02], "size_multiplier": [1.0],
                               "breakeven_tp_pct": [0.01], "invalidation_pct": [0.05]}}
        axes = sd.axis_values(spec)
        self.assertEqual(axes["window_case"], [tuple(c) for c in sd.CASE_ORDER])
        self.assertEqual(axes["window_case"][0], (1, 0, 0))

    def test_case_tuple_accepts_spec_dicts_and_grid_rows(self):
        self.assertEqual(sd.case_tuple({"leg_long": 1, "leg_short": 0, "leg_secondary": 0}),
                         (1, 0, 0))
        self.assertEqual(sd.case_tuple(dict(zip(sd.CASE_FIELDS, (0, 1, 1)), symbol="BTCUSDT")),
                         (0, 1, 1))

    def test_registered_case_order_is_all_non_empty_subsets(self):
        self.assertEqual(len(sd.CASE_ORDER), 7)
        self.assertEqual(len(set(sd.CASE_ORDER)), 7)
        self.assertNotIn((0, 0, 0), sd.CASE_ORDER)
        self.assertEqual(sd.case_index((1, 1, 1)), 6)
        self.assertEqual(sd.case_name((0, 1, 0)), "short_only")

    def test_registered_legs(self):
        self.assertEqual(sd.LEGS[0]["start_hour"], 15)
        self.assertEqual(sd.LEGS[0]["bars"], 1)
        self.assertEqual(sd.LEGS[1]["start_hour"], 1)
        self.assertEqual(sd.LEGS[1]["bars"], 4)
        self.assertEqual(sd.LEGS[1]["direction"], -1)
        self.assertEqual(sd.LEGS[2]["start_hour"], 21)
        self.assertEqual(sd.LEGS[2]["bars"], 3)

    def test_phase_grid_set_is_registered(self):
        self.assertEqual(len(sd.COHORT_GRID_KINDS), 12)
        self.assertIn("boundary_alt_oos", sd.COHORT_GRID_KINDS)
        self.assertIn("boundary_alt_full", sd.COHORT_GRID_KINDS)
        self.assertEqual(sd.ENGINE_VERSION, "d-v1-engine-1.0.0")


if __name__ == "__main__":
    unittest.main(verbosity=2)
