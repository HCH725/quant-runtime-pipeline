#!/usr/bin/env python3
"""Executable check for the Strategy C execution engine (funding-decile signal + 24h DCA rail).

Runs inside the qlib container:
    container exec qlib-run env SC_ENGINE_PATH=/scripts/40_strategy_c_run.py \
        /opt/venv/bin/python /scripts/tests/test_strategy_c_engine.py
and on the host with a numpy interpreter:
    SC_ENGINE_PATH=<repo>/container/scripts/40_strategy_c_run.py python3 <this file>

Drives the pure functions with synthetic bars/settlements whose fills and percentiles are
known by hand, so a regression in the percentile formula, the one-bar entry lag, the
window-bounded rake, the ladder walk, the per-fill fee ledger or the independent gross
accumulator fails loudly instead of silently changing the science.  No market data, no
container state, no network: stdlib unittest + numpy only.
"""
import gzip
import importlib.util
import json
import os
import tempfile
import unittest

import numpy as np

ENGINE = os.environ.get("SC_ENGINE_PATH", "/scripts/40_strategy_c_run.py")
_spec = importlib.util.spec_from_file_location("sc_engine", ENGINE)
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)

BAR_MS = 900000           # 15m bars
LOOKBACK = 180
DECILE = 0.10
RAIL = {"base_quote": 1000.0, "size_multiplier": 1.1, "spacing_d0": 0.02,
        "tp": 0.01, "invalidation": 0.05}
PARAMS = {"decile": DECILE, "lookback_days": LOOKBACK}
LEV = 10.0
P0 = 100.0


class FakeCohort:
    """Bars with a controllable price path; bar i opens at i * 15m."""

    def __init__(self, closes, highs=None, lows=None, opens=None, n_days=1):
        n = len(closes)
        self.symbol = "SYNTH"
        self.timeframe = "15m"
        self.close = np.array(closes, dtype=np.float64)
        self.open = np.array(opens if opens is not None else closes, dtype=np.float64)
        self.high = np.array(highs if highs is not None else closes, dtype=np.float64)
        self.low = np.array(lows if lows is not None else closes, dtype=np.float64)
        self.n = n
        self.open_time_ms = np.arange(n, dtype=np.int64) * BAR_MS
        self.day_index = np.zeros(n, dtype=np.int64)
        self.n_days = n_days
        self.day_end = {n - 1}
        self.price_increment = 0.0
        self.taker_fee = 0.0
        self.leverage = LEV
        self.margin_maint = 0.1


def mk_series(cohort, signals, funding=None):
    """signals: list of (bar_index, percentile, funding_rate)."""
    n = cohort.n
    return {"label": "synthetic", "counts": {},
            "obs_times": np.array([b * BAR_MS for b, _p, _r in signals], dtype=np.int64),
            "obs_rates": np.array([r for _b, _p, r in signals], dtype=np.float64),
            "funding_per_bar": np.zeros(n) if funding is None else np.array(funding, dtype=np.float64),
            "sig_pct": {LOOKBACK: np.array([p for _b, p, _r in signals], dtype=np.float64)},
            "sig_bar": {LOOKBACK: np.array([b for b, _p, _r in signals], dtype=np.int64)},
            "sig_time": {LOOKBACK: np.array([b * BAR_MS for b, _p, _r in signals], dtype=np.int64)}}


def run(cohort, series, slip=0.0, stress=None, kind="full", rail=None):
    return sc.simulate(cohort, PARAMS, rail or RAIL, (0, cohort.n), stress or {}, slip, kind,
                       series)


def flat(n=200, price=P0):
    """A perfectly flat price path; the 24h window of bar b ends at bar b+95."""
    closes = [price] * n
    return FakeCohort(closes, closes, closes, closes)


def walk_expect(low, p0=P0, open_=P0, rail=RAIL):
    """Independent replay of the descending-price trigger walk (shallow -> deep)."""
    qty = rail["base_quote"] * LEV / p0
    cost = qty * p0
    fills = 0
    k = 1
    while True:
        stop = (cost / qty) * (1.0 - rail["invalidation"])
        lvl = p0 * (1.0 - rail["spacing_d0"] * k)
        if k <= 10 and lvl >= stop:
            trig, is_stop = lvl, False
        else:
            trig, is_stop = stop, True
        if low > trig:
            return fills, None
        if is_stop:
            return fills, (trig if open_ >= trig else open_)
        amt = rail["base_quote"] * (rail["size_multiplier"] ** k) * LEV
        qty += amt / trig
        cost += amt
        fills = k
        k += 1


# ---------------------------------------------------------------------------
# 1. the registered percentile formula and its warm-up rule
# ---------------------------------------------------------------------------

class TestPercentile(unittest.TestCase):

    def setUp(self):
        # five settlements 8h apart, starting at t = 0
        self.times = np.array([0, 8, 16, 24, 32], dtype=np.int64) * 3600000
        self.rates = np.array([0.0001, -0.0002, 0.0003, -0.0001, 0.0001])

    def test_midrank_formula_hand_computed(self):
        pct = sc.trailing_midrank_percentiles(self.times, self.rates, 1)
        self.assertTrue(np.isnan(pct[0]))
        self.assertTrue(np.isnan(pct[1]))
        self.assertTrue(np.isnan(pct[2]))
        # t = 24h: window = [0h, 24h] -> 4 observations; strictly below -0.0001 is only -0.0002
        self.assertAlmostEqual(pct[3], (1 + 0.5) / 4.0, places=12)
        # t = 32h: window = [8h, 32h] -> 4 observations; below +0.0001 are 2 values
        self.assertAlmostEqual(pct[4], (2 + 0.5) / 4.0, places=12)

    def test_percentile_is_not_look_ahead(self):
        """A later observation can never change an earlier percentile."""
        full = sc.trailing_midrank_percentiles(self.times, self.rates, 1)
        trunc = sc.trailing_midrank_percentiles(self.times[:4], self.rates[:4], 1)
        self.assertFalse(bool(np.isnan(trunc[3])))
        self.assertAlmostEqual(full[3], trunc[3], places=12)

    def test_warmup_requires_the_full_window(self):
        # 2 days of lookback with only 1 day of data -> no percentile at all
        pct = sc.trailing_midrank_percentiles(self.times, self.rates, 2)
        self.assertTrue(all(np.isnan(pct)))

    def test_brute_force_agreement(self):
        rng = np.random.RandomState(7)
        n = 60
        times = np.arange(n, dtype=np.int64) * 3600000 * 8
        rates = np.round(rng.normal(0, 0.0002, n), 8)
        pct = sc.trailing_midrank_percentiles(times, rates, 1)
        for i in range(n):
            t = int(times[i])
            if t - 86400000 < int(times[0]):
                continue
            window = [r for r, tt in zip(rates, times) if t - 86400000 <= tt <= t]
            below = sum(1 for r in window if r < rates[i])
            self.assertAlmostEqual(pct[i], (below + 0.5) / float(len(window)), places=12)


# ---------------------------------------------------------------------------
# 2. funding load / provenance filter
# ---------------------------------------------------------------------------

class TestFundingLoad(unittest.TestCase):

    def test_official_only_filter_and_window(self):
        rows = []
        for i in range(5):
            rows.append({"symbol": "SYNTH", "funding_time_ms": i * 28800000,
                         "funding_rate": "0.0001",
                         "truth_status": "modeled_funding" if i < 2 else "official"})
        with tempfile.TemporaryDirectory() as tmp:
            d = os.path.join(tmp, "funding", "SYNTH")
            os.makedirs(d)
            with gzip.open(os.path.join(d, "SYNTH-funding.jsonl.gz"), "wt") as fh:
                for r in rows:
                    fh.write(json.dumps(r) + "\n")
            old = sc.RAW_ROOT
            sc.RAW_ROOT = tmp
            try:
                t, r, c = sc.load_funding_series("SYNTH", "1970-01-01", "1970-01-02",
                                                 official_only=False)
                self.assertEqual(len(t), 5)
                self.assertEqual(c["modeled_funding"], 2)
                self.assertEqual(c["official"], 3)
                t2, r2, c2 = sc.load_funding_series("SYNTH", "1970-01-01", "1970-01-02",
                                                    official_only=True)
                self.assertEqual(len(t2), 3)
                self.assertEqual(c2["excluded_by_filter"], 2)
                self.assertTrue((t2 >= 2 * 28800000).all())
            finally:
                sc.RAW_ROOT = old


# ---------------------------------------------------------------------------
# 3. the signal -> entry lag and the 24h window
# ---------------------------------------------------------------------------

class TestSignalAndWindow(unittest.TestCase):

    def test_entry_is_the_close_of_the_bar_starting_at_the_settlement(self):
        # bar 10 opens at the settlement; its close (100) is the next executable price
        c = flat(200)
        s = mk_series(c, [(10, 0.05, -0.0001)])
        m = run(c, s)
        self.assertEqual(m["signals_seen"], 1)
        self.assertEqual(m["signals_entered"], 1)
        self.assertEqual(m["episodes"], 1)
        self.assertEqual(m["layers"][0], 1)

    def test_entry_delay_1_bar_shifts_the_fill_one_bar_later(self):
        # bar 10 opens at the settlement (close 100); bar 11 closes at 50.  A 50% ladder
        # spacing and an unreachable TP keep the rail out of the way, so the only difference
        # between the primary and the stress rerun is WHICH bar's close the entry filled on.
        closes = [P0] * 200
        closes[10] = 100.0
        closes[11] = 50.0
        c = FakeCohort(closes, [100.5] * 200, [99.5] * 200, closes)
        s = mk_series(c, [(10, 0.05, -0.0001)])
        wide = dict(RAIL, spacing_d0=0.5, tp=10.0)
        m0 = run(c, s, rail=wide)
        m1 = run(c, s, stress={"entry_delay_1_bar": True}, rail=wide)
        self.assertEqual(m0["episodes"], 1)
        self.assertEqual(m1["episodes"], 1)
        self.assertEqual(m0["fills"], 2)
        self.assertEqual(m1["fills"], 2)
        # primary: 10,000 notional at 100 -> qty 100, time exit at 100 -> flat
        self.assertAlmostEqual(m0["turnover_usdt"], 20000.0, places=6)
        self.assertAlmostEqual(m0["net_pnl"], 0.0, places=6)
        # delayed: the same notional filled at 50 -> qty 200, time exit at 100 -> +10,000
        self.assertAlmostEqual(m1["turnover_usdt"], 30000.0, places=6)
        self.assertAlmostEqual(m1["net_pnl"], 10000.0, places=6)

    def test_time_exit_flattens_after_exactly_24h(self):
        closes = [P0] * 200
        closes[10] = 100.0                       # entry at the close of bar 10
        closes[105] = 130.0                      # bar 10 + 95 closes the 24h window
        for i in range(106, 200):
            closes[i] = 90.0                     # never captured: the position is already flat
        c = FakeCohort(closes, [100.5] * 200, [99.9] * 200, closes)
        s = mk_series(c, [(10, 0.05, -0.0001)])
        m = run(c, s)
        self.assertEqual(m["time_exits"], 1)
        self.assertEqual(m["open_at_end"], 0)
        self.assertEqual(m["tp_hits"], 0)
        self.assertEqual(m["stop_hits"], 0)
        self.assertEqual(m["fills"], 2)
        # the exit fills on bar 105's close (the bar that ends at T+24h): qty 100 x +30
        self.assertAlmostEqual(m["net_pnl"], 3000.0, places=6)
        self.assertAlmostEqual(m["turnover_usdt"], 10000.0 + 13000.0, places=6)
        self.assertEqual(sc.COUNTERS["hold_over_24h"], 0)
        self.assertEqual(sc.COUNTERS["entry_before_signal"], 0)

    def test_no_entry_while_a_position_is_open(self):
        c = flat(200)
        # three signals: one at bar 10, two inside its 24h window (bars 42 and 74)
        s = mk_series(c, [(10, 0.05, -0.0001), (42, 0.05, -0.0001), (74, 0.05, -0.0001)])
        m = run(c, s)
        self.assertEqual(m["signals_seen"], 3)
        self.assertEqual(m["signals_entered"], 1)
        self.assertEqual(m["episodes"], 1)

    def test_signal_outside_the_decile_never_enters(self):
        c = flat(200)
        s = mk_series(c, [(10, 0.11, -0.0001), (42, 0.10, -0.0001)])
        m = run(c, s)
        self.assertEqual(m["signals_entered"], 1)
        self.assertEqual(m["episodes"], 1)

    def test_next_signal_after_the_window_is_taken(self):
        c = flat(200)
        # bar 106 = 10 + 96 -> the first settlement strictly after the 24h window
        s = mk_series(c, [(10, 0.05, -0.0001), (106, 0.05, -0.0001)])
        m = run(c, s)
        self.assertEqual(m["episodes"], 2)


class TestStructuralGuards(unittest.TestCase):
    """The two executable window guards tolerate the raw ms-level settlement jitter and still
    trip on a real mapping defect (a guard that can never fail proves nothing)."""

    def test_observed_settlement_jitter_does_not_trip_the_guards(self):
        c = flat(200)
        for delta in (1, 4, 31, 900):     # the measured BTCUSDT range is 0..31 ms late
            s = mk_series(c, [(10, 0.05, -0.0001)])
            s["obs_times"] = s["obs_times"] + delta
            before = dict(sc.COUNTERS)
            sc.simulate(c, PARAMS, RAIL, (0, c.n), {}, 0.0, "full", s)
            self.assertEqual(sc.COUNTERS["hold_over_24h"], before["hold_over_24h"])
            self.assertEqual(sc.COUNTERS["signal_bar_mismatch"], before["signal_bar_mismatch"])

    def test_a_settlement_off_its_own_bar_trips_the_mapping_guard(self):
        c = flat(200)
        s = mk_series(c, [(10, 0.05, -0.0001)])
        s["obs_times"] = s["obs_times"] + 60000        # a whole minute into the next bar
        saved = dict(sc.COUNTERS)
        try:
            sc.simulate(c, PARAMS, RAIL, (0, c.n), {}, 0.0, "full", s)
            self.assertGreaterEqual(sc.COUNTERS["signal_bar_mismatch"], 1)
        finally:
            sc.COUNTERS.update(saved)

    def test_ms_late_settlements_map_to_their_own_bar(self):
        """Regression (card t_56ca0634): the exchange timestamps run up to ~31 ms late, and a
        ceil mapping would push 43% of the entries one whole bar out."""
        c = flat(64)
        times = np.array([10 * BAR_MS, 10 * BAR_MS + 1, 10 * BAR_MS + 6, 10 * BAR_MS + 31,
                          34 * BAR_MS, 34 * BAR_MS + 26], dtype=np.int64)
        rates = np.full(len(times), -0.0002)
        series = sc.build_signal_series(c, times, rates, [LOOKBACK], {}, "synthetic")
        self.assertEqual(list(series["sig_bar"][LOOKBACK]), [10, 10, 10, 10, 34, 34])

    def test_the_geometry_guard_trips_on_a_late_exit_and_on_a_stray_settlement(self):
        bar = 900000
        t = 10 * bar
        # healthy geometry: settlement at the boundary, exit closes exactly 24h later
        self.assertEqual(sc.window_geometry_ok(t, t, t + 96 * bar, bar), (True, True))
        # the observed late jitter stays inside the registered tolerance
        self.assertEqual(sc.window_geometry_ok(t + 31, t, t + 96 * bar, bar), (True, True))
        self.assertEqual(sc.window_geometry_ok(t + 900, t, t + 96 * bar, bar), (True, True))
        # an EARLY timestamp would map into the previous bar (a look-ahead) -> fail closed
        self.assertEqual(sc.window_geometry_ok(t - 1, t, t + 96 * bar, bar)[0], False)
        # a settlement a minute into the next bar is a mapping defect
        self.assertEqual(sc.window_geometry_ok(t + 60000, t, t + 96 * bar, bar)[0], False)
        # an exit one bar late violates the registered 24h window
        self.assertEqual(sc.window_geometry_ok(t, t, t + 97 * bar, bar)[1], False)


# ---------------------------------------------------------------------------
# 4. the window-bounded rail: ladder, TP, resting stop, per-fill fees
# ---------------------------------------------------------------------------

class TestRail(unittest.TestCase):

    def test_flat_hold_pays_only_fees(self):
        c = flat(200)
        c.taker_fee = 0.0005
        s = mk_series(c, [(10, 0.05, -0.0001)])
        m = run(c, s)
        # entry + exit = 2 fills of 10,000 notional at 5 bps
        self.assertEqual(m["fills"], 2)
        self.assertAlmostEqual(m["fees"], 2 * 1000.0 * LEV * 0.0005, places=6)
        self.assertAlmostEqual(m["net_pnl"], -m["fees"], places=6)
        self.assertAlmostEqual(m["gross_pnl"], 0.0, places=6)

    def test_breakeven_tp_fires_on_the_high_and_is_hand_checkable(self):
        c = flat(200)
        c.high = np.full(200, P0 * 1.02)
        c.low = np.full(200, P0 * 0.999)
        s = mk_series(c, [(10, 0.05, -0.0001)])
        m = run(c, s)
        self.assertEqual(m["tp_hits"], 1)
        self.assertEqual(m["time_exits"], 0)
        self.assertAlmostEqual(m["net_pnl"], 1000.0 * LEV * RAIL["tp"], places=6)

    def test_ladder_walk_then_resting_stop_hand_checked(self):
        closes = [P0] * 200
        c = FakeCohort(closes, closes, closes, closes)
        c.high = np.full(200, P0)
        c.low = np.full(200, P0)
        c.low[12] = 85.0
        c.high[12] = P0
        c.open[12] = P0
        s = mk_series(c, [(10, 0.05, -0.0001)])
        fills, kill = walk_expect(85.0)
        self.assertEqual(fills, 4)
        self.assertIsNotNone(kill)
        m = run(c, s)
        self.assertEqual(m["stop_hits"], 1)
        self.assertEqual(m["layers"][:5], [1, 1, 1, 1, 1])
        self.assertEqual(sum(m["layers"][5:]), 0)
        self.assertLess(m["net_pnl"], 0.0)

    def test_a_level_below_the_resting_stop_can_never_fill(self):
        closes = [P0] * 200
        c = FakeCohort(closes, closes, closes, closes)
        c.high = np.full(200, P0)
        c.low = np.full(200, 60.0)
        c.open[11] = P0
        s = mk_series(c, [(10, 0.05, -0.0001)])
        m = run(c, s)
        self.assertEqual(m["stop_hits"], 1)
        self.assertLessEqual(sum(1 for x in m["layers"][1:] if x), 10)
        self.assertEqual(m["layers"][11], 0)

    def test_fee_is_charged_at_every_fill(self):
        closes = [P0] * 200
        c = FakeCohort(closes, closes, closes, closes)
        c.taker_fee = 0.0005
        c.low = np.full(200, 97.9)      # crosses ladder level 1 (98.0) only
        c.high = np.full(200, P0)
        c.open = np.full(200, P0)
        s = mk_series(c, [(10, 0.05, -0.0001)])
        m = run(c, s)
        self.assertEqual(m["fills"], 3)                 # entry + 1 ladder add + exit
        self.assertEqual(m["layers"][1], 1)
        self.assertAlmostEqual(m["net_pnl"], m["gross_pnl"] - m["fees"] - m["funding"], places=3)
        self.assertTrue(sc.pnl_decomposition_ok(m))
        self.assertGreater(m["fees"], 2 * 1000.0 * LEV * 0.0005)   # the add pays a fee too

    def test_fee_2x_moves_net_but_never_gross(self):
        c = flat(200)
        c.taker_fee = 0.0005
        c.high = np.full(200, P0 * 1.02)
        s = mk_series(c, [(10, 0.05, -0.0001)])
        base = run(c, s)
        twice = run(c, s, stress={"fee_mult": 2.0})
        self.assertAlmostEqual(base["gross_pnl"], twice["gross_pnl"], places=6)
        self.assertAlmostEqual(twice["fees"], 2.0 * base["fees"], places=6)
        self.assertLess(twice["net_pnl"], base["net_pnl"])

    def test_cost_attrition_8x_is_not_a_noop(self):
        c = flat(200)
        c.taker_fee = 0.0005
        s = mk_series(c, [(10, 0.05, -0.0001)])
        base = run(c, s)
        attr = run(c, s, stress=sc.COST_ATTRITION_STRESS)
        self.assertAlmostEqual(attr["fees"], 8.0 * base["fees"], places=6)
        self.assertLess(attr["net_pnl"], base["net_pnl"])


# ---------------------------------------------------------------------------
# 5. funding cost accounting
# ---------------------------------------------------------------------------

class TestFundingCost(unittest.TestCase):

    def test_negative_funding_pays_the_long(self):
        c = flat(200)
        s = mk_series(c, [(10, 0.05, -0.0001)], funding=[0.0] * 200)
        s["funding_per_bar"][42] = -0.0005          # one settlement inside the hold
        m = run(c, s)
        self.assertEqual(m["episodes"], 1)
        self.assertLess(m["funding"], 0.0)          # a negative rate is a credit to a long
        self.assertAlmostEqual(m["net_pnl"], -m["funding"], places=6)

    def test_funding_2x_doubles_the_charge(self):
        c = flat(200)
        s = mk_series(c, [(10, 0.05, -0.0001)], funding=[0.0] * 200)
        s["funding_per_bar"][42] = -0.0005
        base = run(c, s)
        twice = run(c, s, stress={"funding_mult": 2.0})
        self.assertAlmostEqual(twice["funding"], 2.0 * base["funding"], places=6)

    def test_no_funding_grid_zeroes_the_cost(self):
        c = flat(200)
        s = mk_series(c, [(10, 0.05, -0.0001)], funding=[0.0] * 200)
        s["funding_per_bar"][42] = -0.0005
        m = run(c, s, stress={"no_funding": True})
        self.assertEqual(m["funding"], 0.0)

    def test_gross_is_independent_of_fees_and_funding(self):
        c = flat(200)
        c.taker_fee = 0.0005
        s = mk_series(c, [(10, 0.05, -0.0001)], funding=[0.0] * 200)
        s["funding_per_bar"][42] = 0.0003
        m = run(c, s)
        clean = run(c, s, stress={"no_funding": True})
        self.assertAlmostEqual(m["gross_pnl"], clean["gross_pnl"], places=6)
        self.assertNotAlmostEqual(m["net_pnl"], clean["net_pnl"], places=6)


# ---------------------------------------------------------------------------
# 6. selector / disposition / family-level registered checks
# ---------------------------------------------------------------------------

SPEC = {
    "gates": {"min_episodes_is": 2, "neighborhood_min_same_sign_fraction": 0.6,
              "min_oos_sharpe": 0.40, "min_oos_annualized_return": 0.0,
              "max_provenance_relative_difference": 0.5},
    "parameter_domain": {"grid_deciles": [0.05, 0.10, 0.20], "grid_lookbacks": [180]},
    "dca_domain": {"spacing_pct": [0.01, 0.02], "size_multiplier": [1.0, 1.1],
                   "breakeven_tp_pct": [0.01, 0.02], "invalidation_pct": [0.05, 0.10]},
}


def row(decile=0.10, spacing=0.01, size=1.0, tp=0.01, inval=0.05, kind="historical",
        net=100.0, sharpe=1.0, episodes=10):
    return {"symbol": "SYNTH", "timeframe": "15m", "window_kind": kind, "decile": decile,
            "lookback_days": 180, "spacing_pct": spacing, "size_multiplier": size,
            "breakeven_tp_pct": tp, "invalidation_pct": inval, "net_pnl": net,
            "sharpe": sharpe, "episodes": episodes, "annualized_return": 0.1}


class TestSelector(unittest.TestCase):

    def test_selector_refuses_oos_rows(self):
        with self.assertRaises(ValueError):
            sc.select_cohort_winner([row(kind="oos")], SPEC)

    def test_selector_accepts_the_official_only_era_when_registered(self):
        w, reason = sc.select_cohort_winner([row(kind="official_only")], SPEC,
                                            allowed=("official_only",))
        self.assertEqual(reason, "selected")
        self.assertEqual(w["window_kind"], "official_only")

    def test_insufficient_trades_culls_the_cohort(self):
        rows = [row(episodes=1), row(episodes=1)]
        w, reason = sc.select_cohort_winner(rows, SPEC)
        self.assertIsNone(w)
        self.assertEqual(reason, "insufficient_trades")

    def test_no_qualifying_candidate_when_every_case_loses(self):
        rows = [row(net=-5.0), row(net=-1.0, sharpe=2.0), row(net=10.0, sharpe=-1.0)]
        w, reason = sc.select_cohort_winner(rows, SPEC)
        self.assertIsNone(w)
        self.assertEqual(reason, "no_qualifying_candidate")

    def test_tie_break_is_the_registered_axis_index_order(self):
        # identical Sharpe and net_pnl: the registered lexical order decides
        rows = [row(decile=0.20), row(decile=0.05), row(decile=0.10)]
        w, _ = sc.select_cohort_winner(rows, SPEC)
        self.assertEqual(w["decile"], 0.05)

    def test_selector_is_order_independent(self):
        rows = [row(decile=d, sharpe=s) for d, s in
                ((0.05, 1.0), (0.10, 3.0), (0.20, 2.0))]
        w1, _ = sc.select_cohort_winner(rows, SPEC)
        w2, _ = sc.select_cohort_winner(list(reversed(rows)), SPEC)
        self.assertEqual(w1["decile"], w2["decile"])
        self.assertEqual(w1["decile"], 0.10)


class TestCohortGate(unittest.TestCase):

    def _rows(self, oos_net=50.0, oos_sharpe=1.0, full_net=50.0, stress_net=50.0,
              cost_attr_net=50.0):
        axes = [(d, sp, sz, tp, iv)
                for d in SPEC["parameter_domain"]["grid_deciles"]
                for sp in SPEC["dca_domain"]["spacing_pct"]
                for sz in SPEC["dca_domain"]["size_multiplier"]
                for tp in SPEC["dca_domain"]["breakeven_tp_pct"]
                for iv in SPEC["dca_domain"]["invalidation_pct"]]

        def build(kind, net, sharpe=1.0):
            return [row(decile=d, spacing=sp, size=sz, tp=tp, inval=iv, kind=kind,
                        net=net, sharpe=sharpe)
                    for (d, sp, sz, tp, iv) in axes]

        rows = {"historical": build("historical", 100.0),
                "oos": build("oos", oos_net, oos_sharpe),
                "full": build("full", full_net),
                "cost_attrition_40bps": build("cost_attrition_40bps", cost_attr_net)}
        for s, _st in sc.STRESS:
            rows[s] = build(s, stress_net)
        rows["no_funding"] = build("no_funding", 100.0)
        rows["no_funding_full"] = build("no_funding_full", 100.0)
        return rows

    def test_all_requirements_met_is_a_survivor(self):
        r = self._rows()
        out = sc.evaluate_cohort(SPEC, "SYNTH/15m", r, self._rows()["historical"])
        self.assertEqual(out["outcome"], "SURVIVOR")
        self.assertEqual(out["cull_reasons"], [])

    def test_oos_sharpe_floor_is_enforced(self):
        r = self._rows(oos_sharpe=0.39)
        out = sc.evaluate_cohort(SPEC, "SYNTH/15m", r, r["historical"])
        self.assertIn("oos_economic", out["cull_reasons"])

    def test_negative_oos_annualized_return_is_rejected(self):
        r = self._rows()
        for x in r["oos"]:
            x["annualized_return"] = -0.01
        out = sc.evaluate_cohort(SPEC, "SYNTH/15m", r, r["historical"])
        self.assertIn("oos_economic", out["cull_reasons"])

    def test_g8_cost_attrition_is_a_cohort_gate(self):
        r = self._rows(cost_attr_net=-1.0)
        out = sc.evaluate_cohort(SPEC, "SYNTH/15m", r, r["historical"])
        self.assertIn("robustness_economic:cost_attrition_40bps", out["cull_reasons"])

    def test_neighbourhood_uses_historical_rows_only(self):
        r = self._rows()
        # flip one face-adjacent neighbour negative on the OOS grid: the neighbourhood
        # judgement must not move (it may only read historical rows)
        r["oos"][1]["net_pnl"] = -1.0
        out = sc.evaluate_cohort(SPEC, "SYNTH/15m", r, r["historical"])
        self.assertTrue(out["neighbourhood"]["passed"])


class TestFamilyLevelChecks(unittest.TestCase):

    def test_threshold_instability_detects_disagreeing_tracks(self):
        oos = []
        for d, net in ((0.05, 10.0), (0.10, -5.0), (0.20, 20.0)):
            oos.append(row(decile=d, kind="oos", net=net))
        res = sc.threshold_instability_check(SPEC, row(decile=0.10), oos)
        self.assertTrue(res["hit"])
        self.assertFalse(res["signs_agree"])

    def test_threshold_instability_clean_when_tracks_agree(self):
        oos = [row(decile=d, kind="oos", net=5.0) for d in (0.05, 0.10, 0.20)]
        res = sc.threshold_instability_check(SPEC, row(decile=0.10), oos)
        self.assertFalse(res["hit"])
        self.assertTrue(res["signs_agree"])

    def test_provenance_check_fails_on_a_sign_flip(self):
        winner = row(decile=0.05, kind="historical")
        oo_winner = row(decile=0.20, kind="official_only")
        oos = [row(decile=0.05, kind="oos", sharpe=1.0),
               row(decile=0.20, kind="oos", sharpe=-0.9)]
        res = sc.funding_provenance_check(SPEC, winner, oo_winner, oos)
        self.assertTrue(res["hit"])

    def test_provenance_check_fails_on_a_large_difference(self):
        winner = row(decile=0.05, kind="historical")
        oo_winner = row(decile=0.20, kind="official_only")
        oos = [row(decile=0.05, kind="oos", sharpe=2.0),
               row(decile=0.20, kind="oos", sharpe=0.5)]
        res = sc.funding_provenance_check(SPEC, winner, oo_winner, oos)
        self.assertTrue(res["hit"])          # |2.0-0.5| / 2.0 = 0.75 > 0.5

    def test_provenance_check_clean_when_selection_is_stable(self):
        winner = row(decile=0.10, kind="historical")
        oo_winner = row(decile=0.10, kind="official_only")
        oos = [row(decile=0.10, kind="oos", sharpe=1.0)]
        res = sc.funding_provenance_check(SPEC, winner, oo_winner, oos)
        self.assertFalse(res["hit"])
        self.assertTrue(res["same_cell_selected"])

    def test_family_disposition_bands(self):
        self.assertEqual(sc.family_disposition([], True)["verdict_recommendation"], "REJECT")
        self.assertEqual(sc.family_disposition([{"cohort": "A"}], True)["disposition"],
                         "SURVIVOR_FOUND")
        self.assertEqual(sc.family_disposition([{"cohort": "A"}, {"cohort": "B"}], True)["disposition"],
                         "MULTIPLE_SURVIVORS")
        d = sc.family_disposition([{"cohort": "A"}], False)
        self.assertEqual(d["verdict_recommendation"], "TECHNICAL_INCOMPLETE")
        self.assertFalse(d["performance_claimable_recommendation"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
