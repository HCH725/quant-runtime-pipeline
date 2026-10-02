#!/usr/bin/env python3
"""Focused host test for 430_two_level_uncertainty_cross_sectional_ranker_regime_trust_gate_tail_cap_run.py.

Runs on the stdlib interpreter from the scripts directory (the host preparation runner's focused
test gate): it loads the family runner as a module and exercises the registered core primitives on
synthetic data only -- the seven PIT-safe features, the deterministic histogram gradient-boosting
engine, the rank-displacement / rank-IC / AUROC / EWMA-maturity utilities, the regime-trust gate
assembly, the monthly leg state machine's event consumers, the multi-direction DCA episode
accounting (independent gross-PnL accumulator, per-fill fees/funding, the registered epistemic
tail-cap quote scale) and the registered domain/count invariants.  No canonical raw data, no
container and no strategy run are touched.
"""

import importlib.util
import json
import math
import random
import unittest
from pathlib import Path

RUNNER_NAME = "430_two_level_uncertainty_cross_sectional_ranker_regime_trust_gate_tail_cap_run.py"
HERE = Path(__file__).resolve().parent


def load_runner():
    spec = importlib.util.spec_from_file_location("family_runner_430", HERE.parent / RUNNER_NAME)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


R = load_runner()


def synth_daily(days, base=100.0, seed=11, vol=0.01):
    rng = random.Random(seed)
    bars = []
    step = 86400000
    start = 1640995200000  # 2022-01-01T00:00:00Z
    price = base
    for d in range(days):
        o = price
        price = max(1e-6, price * (1.0 + rng.gauss(0.0, vol)))
        t = start + d * step
        bars.append({"open_time_ms": t, "close_time_ms": t + step - 1,
                     "open": o, "high": max(o, price) * 1.002, "low": min(o, price) * 0.998,
                     "close": price, "volume": 10.0})
    return bars


def make_table(bars):
    return {"bars": bars, "closes": [b["close"] for b in bars],
            "rows": R.symbol_feature_table(bars),
            "index": {R.bar_date(b): i for i, b in enumerate(bars)}}


def grid_row(grid, symbol="BTCUSDT", **overrides):
    row = {"symbol": symbol, "timeframe": R.TIMEFRAMES[0], "strategy_case": 0, "grid": grid,
           "spacing_pct": 0.02, "size_multiplier": 1.0, "breakeven_tp_pct": 0.02,
           "invalidation_pct": 0.10}
    row.update({k: 0.0 for k in (
        "gross_pnl", "fees", "funding", "net_pnl", "ending_equity", "sharpe", "max_dd_pct",
        "max_dd_usdt", "annualized_return", "max_effective_leverage", "capital_utilization")})
    row.update({"episodes": 12, "fills": 30, "adds": 10, "turnover_usdt": 1000.0,
                "tp_hits": 12, "stop_hits": 0, "family_exits": 0, "margin_calls": 0,
                "end_exits": 0, "open_at_end": 0, "long_episodes": 10, "short_episodes": 2,
                "capped_episodes": 0, "max_active_tranches": 5,
                "decomposition_ok": True, "funding_events_charged": 0,
                "formations_in_phase": 12, "missing_formations": 0,
                "out_of_window_formations": 0})
    row.update(overrides)
    return row


DCA = {"spacing_pct": 0.01, "size_multiplier": 1.0, "breakeven_tp_pct": 0.03,
       "invalidation_pct": 0.05}
COST = {"fee_bps": 5.0, "funding_mult": 1.0, "entry_delay_bars": 0,
        "slippage_ticks": 1.0, "tick_size": 0.01}


def mini_bars(prices):
    step = 86400000
    out = []
    for i, p in enumerate(prices):
        out.append({"open_time_ms": i * step, "close_time_ms": (i + 1) * step - 1,
                    "open": p, "high": p, "low": p, "close": p, "volume": 1.0})
    return out


class TestProtocolUtilities(unittest.TestCase):
    def test_spearman_auroc_and_ks(self):
        self.assertAlmostEqual(R._spearman([1, 2, 3, 4], [2, 1, 4, 3]), 0.6, places=9)
        self.assertEqual(R._auroc([1, 1, 0, 0], [0.9, 0.8, 0.2, 0.1]), 1.0)
        self.assertIsNone(R._auroc([1, 1], [0.5, 0.6]))
        self.assertAlmostEqual(R._ks_distance([1.0, 2.0], [1.0, 2.0]), 0.0, places=9)
        self.assertGreater(R._ks_distance([10.0, 20.0], [1.0, 2.0]), 0.5)

    def test_quantile_and_percentile_rank(self):
        self.assertEqual(R._quantile_nearest([1.0, 2.0, 3.0, 4.0], 0.85), 4.0)
        self.assertIsNone(R._quantile_nearest([], 0.5))
        self.assertEqual(R._percentile_rank([1.0, 2.0, 3.0, 4.0], 4.0), 0.875)
        self.assertEqual(R._percentile_rank([1.0, 2.0, 3.0, 4.0], 1.0), 0.125)

    def test_expanding_z_and_ewma_maturity(self):
        z = R._expanding_z([1.0 + 0.001 * i for i in range(60)] + [2.0], 60)
        self.assertIsNone(z[0])
        self.assertIsNone(z[59])          # only 59 strictly-earlier observations so far
        self.assertIsNotNone(z[60])       # 60 earlier observations -> scored
        raw = [0.5, 0.4, 0.9, 0.1]
        matured = R._ewma_matured_series(raw, 2, 2, 1)
        self.assertIsNone(matured[0])
        self.assertIsNone(matured[1])
        self.assertAlmostEqual(matured[2], 0.5, places=9)   # only raw[0] is matured at t=2
        self.assertTrue(0.0 < matured[3] < 0.9)

    def test_vol_sized_score_and_gate_clip(self):
        c_vol = 1.0
        self.assertAlmostEqual(R._vol_sized_score(2.0, 1.0, c_vol), 2.0, places=9)
        self.assertAlmostEqual(R._vol_sized_score(2.0, 4.0, c_vol), 1.0, places=9)
        self.assertIsNone(R._vol_sized_score(None, 1.0, c_vol))
        self.assertEqual(R._clamp((1.0 - 0.3) / 0.4, 0.0, 1.0), 1.0)
        self.assertEqual(R._clamp(0.0, 0.0, 1.0), 0.0)


class TestGBDT(unittest.TestCase):
    def test_learns_a_monotone_target_and_is_deterministic(self):
        rng = random.Random(3)
        X = [[rng.uniform(-1, 1), rng.uniform(-1, 1)] for _ in range(150)]
        y = [3.0 * x[0] - 0.5 * x[1] for x in X]
        model_a = R.fit_gbdt((30, 2, 0.1, 0.0, 1.0), X, y)
        model_b = R.fit_gbdt((30, 2, 0.1, 0.0, 1.0), X, y)
        pred = model_a.predict(X)
        self.assertAlmostEqual(pred[0], model_b.predict(X)[0], places=12)
        self.assertGreater(R._pearson(pred, y), 0.85)

    def test_ranker_and_deup_engines_are_registered(self):
        self.assertEqual(len(R.RANKER_PARAMS), 5)
        self.assertEqual(len(R.DEUP_PARAMS), 5)
        self.assertGreaterEqual(R.MIN_TRAIN_SAMPLES, 100)
        self.assertEqual(R.REFIT_EVERY, 21)
        self.assertEqual(R.EMBARGO_DAYS, 90)


class TestCrossSectionalPrimitives(unittest.TestCase):
    def test_feature_table_windows(self):
        bars = synth_daily(300)
        rows = R.symbol_feature_table(bars)
        self.assertIsNone(rows[10]["mom_1m"])
        self.assertIsNotNone(rows[21]["mom_1m"])
        self.assertIsNotNone(rows[63]["mom_3m"])
        self.assertIsNotNone(rows[252]["mom_12m"])
        self.assertIsNotNone(rows[25]["vol_20d"])
        self.assertGreater(rows[25]["vol_20d"], 0.0)
        self.assertIsNotNone(rows[25]["adv_20d"])
        self.assertEqual(len(rows), 300)

    def test_cross_sectional_rank_and_displacement(self):
        tables = {}
        for i, sym in enumerate(("BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT")):
            tables[sym] = make_table(synth_daily(300, seed=20 + i, vol=0.005 * (i + 1)))
        day = R.bar_date(tables["BTCUSDT"]["bars"][260])
        R._cross_sectional_ranks(tables, day)
        ranks = [tables[s]["rows"][tables[s]["index"][day]]["cross_sectional_rank"]
                 for s in sorted(tables)]
        self.assertTrue(all(r is not None for r in ranks))
        self.assertTrue(all(0.0 <= r <= 1.0 for r in ranks))
        self.assertAlmostEqual(sum(ranks), 2.0, places=9)  # n/2 for n=4 percentile ranks
        self.assertEqual(sorted(ranks), [0.125, 0.375, 0.625, 0.875])
        # displacement: perfect score order vs reversed label order -> symmetric extreme losses
        disp = R.rank_displacement({"A": 4.0, "B": 3.0, "C": 2.0, "D": 1.0},
                                   {"A": 1.0, "B": 2.0, "C": 3.0, "D": 4.0})
        self.assertAlmostEqual(disp["A"], 0.75, places=9)
        self.assertAlmostEqual(disp["D"], 0.75, places=9)
        self.assertAlmostEqual(disp["B"], 0.25, places=9)

    def test_rank_ic_and_forward_excess_panel(self):
        self.assertIsNone(R.rank_ic({"A": 1.0}, {"A": 1.0}))
        ric = R.rank_ic({"A": 1.0, "B": 2.0, "C": 3.0, "D": 4.0},
                        {"A": 1.0, "B": 2.0, "C": 3.0, "D": 4.0})
        self.assertAlmostEqual(ric, 1.0, places=9)
        tables = {}
        for i, sym in enumerate(R.DEFAULT_INSTRUMENTS):
            tables[sym] = make_table(synth_daily(320, seed=31 + i))
        common = sorted({d for sym in tables for d in tables[sym]["index"]})
        fwd, bench = R.forward_excess_returns(tables, common, R.TAU)
        self.assertEqual(len(bench), len(common))
        for sym in tables:
            self.assertTrue(fwd[sym])
        self.assertTrue(all(isinstance(v, float) for v in fwd[R.DEFAULT_INSTRUMENTS[0]].values()))
        last = common[-1]
        dates_only = [d for d in common if d < last]
        self.assertTrue(all(d in bench for d in dates_only))


class TestEpisodeAccounting(unittest.TestCase):
    def test_cap_factor_scales_every_tranche(self):
        bars = mini_bars([100.0, 99.0, 98.0, 97.0, 96.0, 97.0])
        full = R.simulate_episode(bars, {"entry_idx": 0, "direction": "long", "exit_idx": 5},
                                  len(bars) - 1, len(bars) - 1, DCA, COST, [])
        capped = R.simulate_episode(bars, {"entry_idx": 0, "direction": "long", "exit_idx": 5,
                                           "cap_factor": R.KAPPA},
                                    len(bars) - 1, len(bars) - 1, DCA, COST, [])
        self.assertEqual(full["adds"], capped["adds"])
        self.assertAlmostEqual(capped["entry_notional"], full["entry_notional"] * R.KAPPA, places=6)
        self.assertAlmostEqual(capped["gross"], full["gross"] * R.KAPPA, places=6)
        self.assertEqual(capped["cap_factor"], R.KAPPA)

    def test_fill_ledger_decomposition_and_funding_sign(self):
        bars = mini_bars([100.0, 99.0, 98.0, 97.0, 90.0, 95.0, 96.0])
        ep = R.simulate_episode(bars, {"entry_idx": 0, "direction": "long", "exit_idx": 5},
                                len(bars) - 1, len(bars) - 1, DCA, COST, [])
        self.assertGreaterEqual(ep["fills"], 2)
        self.assertGreater(ep["adds"], 0)
        self.assertGreater(ep["max_active_tranches"], 1)
        self.assertLessEqual(ep["max_active_tranches"], R.ROUTINE_ACTIVE_TRANCHES_MAX)
        self.assertIn(ep["exit_reason"], ("tp", "invalidation", "family_exit", "window_end"))
        # funding sign flips for the short leg (official observation charged on both sides)
        step = 86400000
        flat = [{"open_time_ms": i * step, "close_time_ms": (i + 1) * step - 1, "open": 100.0,
                 "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1.0} for i in range(3)]
        funding = [{"t": 86400000, "rate": 0.001, "mark": 100.0, "cost_per_unit": 0.1}]
        wide = {"spacing_pct": 0.01, "size_multiplier": 1.0, "breakeven_tp_pct": 0.5,
                "invalidation_pct": 0.5}
        zero_slip = dict(COST, slippage_ticks=0.0)
        long_ep = R.simulate_episode(flat, {"entry_idx": 0, "direction": "long", "exit_idx": 2},
                                     len(flat) - 1, len(flat) - 1, wide, zero_slip, funding)
        short_ep = R.simulate_episode(flat, {"entry_idx": 0, "direction": "short", "exit_idx": 2},
                                      len(flat) - 1, len(flat) - 1, wide, zero_slip, funding)
        self.assertGreater(long_ep["funding_base"], 0.0)
        self.assertAlmostEqual(short_ep["funding_base"], -long_ep["funding_base"], places=9)
        self.assertEqual(long_ep["funding_events_charged"], 1)

    def test_episode_sequence_and_derived_tracks(self):
        bars = mini_bars([100.0, 99.0, 98.0, 97.0, 96.0, 95.0, 94.0, 93.0, 92.0, 91.0])
        events = [{"kind": "entry", "formation_idx": 0, "direction": "long", "cap_factor": 1.0},
                  {"kind": "exit", "formation_idx": 3},
                  {"kind": "entry", "formation_idx": 3, "direction": "short", "cap_factor": 1.0}]
        episodes, missing, oow = R.run_episode_sequence(bars, events, 0, len(bars) - 1,
                                                        len(bars) - 1, 0, DCA, COST, [])
        self.assertEqual(missing, 0)
        self.assertEqual(oow, 0)
        self.assertEqual(episodes[0]["direction"], "long")
        if len(episodes) > 1:
            self.assertEqual(episodes[1]["direction"], "short")
            self.assertGreaterEqual(episodes[1]["entry_idx"], episodes[0]["exit_idx"])
        derived = R.derive_episodes(episodes, 2.0, 0.0)
        self.assertAlmostEqual(derived[0]["fees"], episodes[0]["fee_base"] * 2.0, places=9)
        self.assertAlmostEqual(derived[0]["funding"], 0.0, places=9)
        flat = R.derive_flat_fee(episodes, 10.0)
        self.assertAlmostEqual(flat[0]["fees"], episodes[0]["turnover_usdt"] * 0.001, places=9)
        summary = R.summarize(derived, "2022-01-01", "2022-01-10")
        self.assertTrue(summary["decomposition_ok"])
        self.assertEqual(summary["episodes"], len(episodes))
        self.assertIn("capped_episodes", summary)

    def test_short_leg_mirrors_long_on_symmetric_prices(self):
        falling = mini_bars([100.0, 99.0, 98.0, 97.0, 90.0, 95.0, 96.0])
        rising = mini_bars([100.0, 101.0, 102.0, 103.0, 110.0, 105.0, 104.0])
        zero = {"fee_bps": 0.0, "funding_mult": 0.0, "entry_delay_bars": 0,
                "slippage_ticks": 0.0, "tick_size": 0.01}
        long_ep = R.simulate_episode(falling, {"entry_idx": 0, "direction": "long", "exit_idx": 5},
                                     len(falling) - 1, len(falling) - 1, DCA, zero, [])
        short_ep = R.simulate_episode(rising, {"entry_idx": 0, "direction": "short", "exit_idx": 5},
                                      len(rising) - 1, len(rising) - 1, DCA, zero, [])
        self.assertEqual(long_ep["adds"], short_ep["adds"])
        self.assertEqual(long_ep["exit_reason"], short_ep["exit_reason"])
        self.assertLess(long_ep["gross"], 0.0)
        self.assertLess(short_ep["gross"], 0.0)


class TestSelectorAndRecords(unittest.TestCase):
    def test_select_cohort_and_cull_reasons(self):
        empty = R.select_cohort([grid_row("historical", episodes=2)])
        self.assertEqual(empty[1], "insufficient_trades")
        losing = R.select_cohort([grid_row("historical", episodes=20, net_pnl=-5.0, sharpe=-0.5)])
        self.assertEqual(losing[1], "no_qualifying_candidate")
        winner, reason, candidates = R.select_cohort([
            grid_row("historical", episodes=20, net_pnl=100.0, sharpe=0.5),
            grid_row("historical", episodes=20, net_pnl=200.0, sharpe=0.9, spacing_pct=0.03)])
        self.assertIsNone(reason)
        self.assertEqual(winner["spacing_pct"], 0.03)
        self.assertEqual(len(candidates), 2)

    def test_neighbourhood_and_disposition_band(self):
        rows = []
        for sp in R.DCA_AXES["spacing_pct"]:
            rows.append(grid_row("historical", spacing_pct=sp, net_pnl=10.0, episodes=20))
        nb = R.neighbourhood(rows[1], rows)
        self.assertEqual(nb["neighbours"], 2)
        self.assertEqual(nb["same_sign_fraction"], 1.0)
        self.assertTrue(nb["passed"])
        self.assertEqual(R.disposition_band(0), "NO_SURVIVOR")
        self.assertEqual(R.disposition_band(1), "SURVIVOR_FOUND")
        self.assertEqual(R.disposition_band(3), "MULTIPLE_SURVIVORS")

    def test_cohort_record_matches_contract_10_8_schema(self):
        by_grid = {g: grid_row(g) for g in R.GRIDS}
        hist = by_grid["historical"]
        nb = {"neighbours": 4, "agreeing": 3, "same_sign_fraction": 0.75,
              "axes": ["strategy_case", "spacing_pct", "size_multiplier", "breakeven_tp_pct",
                       "invalidation_pct"], "passed": True}
        checks = {"historical_winner_found": True, "oos_economic": True, "full_economic": True,
                  "robustness_economic": True, "robustness_failed_grids": [],
                  "parameter_neighbourhood": True}
        record = R.cohort_record("BTCUSDT/1d", hist, hist, by_grid, [], checks, nb)
        self.assertEqual(record["outcome"], "SURVIVOR")
        self.assertEqual(record["winner"]["strategy_case"], 0)
        self.assertEqual(set(record["metrics"]), {"phases", "robustness", "neighbourhood"})
        self.assertEqual(set(record["metrics"]["phases"]), {"historical", "oos", "full"})
        self.assertEqual(set(record["metrics"]["robustness"]),
                         {"fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks"})
        self.assertEqual(record["cull_reasons"], [])
        culled = R.cohort_record("BTCUSDT/1d", hist, hist, by_grid, ["oos_economic"], checks, nb)
        self.assertEqual(culled["outcome"], "CULLED")


class TestEvaluationAndFalsificationWiring(unittest.TestCase):
    SYM = "BTCUSDT"

    def _daily_bars(self):
        days = [R.dt.date(2022, 1, 3), R.dt.date(2022, 1, 4),
                R.dt.date(2025, 10, 2), R.dt.date(2026, 1, 5)]
        bars = []
        for i, day in enumerate(days):
            t = int(R.dt.datetime(day.year, day.month, day.day,
                                  tzinfo=R.dt.timezone.utc).timestamp() * 1000)
            price = 100.0 + i
            bars.append({"open_time_ms": t, "close_time_ms": t + 86400000 - 1,
                         "open": price, "high": price * 1.01, "low": price * 0.99,
                         "close": price, "volume": 1.0})
        return bars

    def _meta(self):
        return {self.SYM: {"taker_fee": 0.0005, "price_increment": 0.1, "maker_fee": 0.0002,
                           "quote_currency": "USDT"}}

    def test_evaluate_all_grid_and_counts(self):
        bars = self._daily_bars()
        dates = [R.bar_date(b) for b in bars]
        rows_by_grid = R.evaluate_all(
            self._meta(), {R.TIMEFRAMES[0]: {self.SYM: bars}}, {self.SYM: dates},
            {self.SYM: {"events": []}}, {self.SYM: []})
        self.assertEqual(sorted(rows_by_grid), sorted(R.GRIDS))
        for grid, rows in rows_by_grid.items():
            self.assertEqual(len(rows), len(R.STRATEGY_CASES) * len(R.DCA_GRID))
            self.assertTrue(all(r["decomposition_ok"] for r in rows))

    def test_evaluate_falsification_battery_shape(self):
        rows = [grid_row("historical", symbol=self.SYM, net_pnl=100.0, sharpe=1.0, episodes=12)]
        bars = self._daily_bars()
        dates = [R.bar_date(b) for b in bars]
        panel_days = [{"day": "2025-10-02", "gate": 0.5, "coupling": 0.6,
                       "matured_rankic": 0.01, "good_day": 1, "ehat": {}, "score": {}},
                      {"day": "2025-10-03", "gate": 0.1, "coupling": 0.5,
                       "matured_rankic": -0.01, "good_day": 0, "ehat": {}, "score": {}}]
        audit = {str(lag): {"auroc_full": 0.7, "auroc_oos": 0.65, "days_full": 10, "days_oos": 2}
                 for lag in [R.TAU] + list(R.FALSIFICATION["embargo_audit_lags"])}
        result = R.evaluate_falsification(
            panel_days, audit, {"historical": rows}, {self.SYM: bars}, {self.SYM: dates},
            {self.SYM: {"events": []}}, {self.SYM: []}, self._meta(), [self.SYM])
        self.assertEqual(sorted(result["items"]), ["1", "2", "3", "4"])
        self.assertTrue(result["registered_battery_implemented"])
        self.assertIsInstance(result["case_rejected"], bool)
        self.assertEqual(result["items_evaluated"], 4)

    def _falsification_fixture(self):
        """8 full-window bars + one qualifying historical row; entry events make the pools non-empty."""
        days = [R.dt.date(2022, 1, 3) + R.dt.timedelta(days=i) for i in range(8)]
        prices = [100.0, 101.0, 99.0, 98.0, 97.0, 99.0, 100.0, 102.0]
        bars = []
        for day, price in zip(days, prices):
            t = int(R.dt.datetime(day.year, day.month, day.day,
                                  tzinfo=R.dt.timezone.utc).timestamp() * 1000)
            bars.append({"open_time_ms": t, "close_time_ms": t + 86400000 - 1,
                         "open": price, "high": price, "low": price, "close": price,
                         "volume": 1.0})
        dates = [R.bar_date(b) for b in bars]
        row = grid_row("historical", symbol=self.SYM, net_pnl=100.0, sharpe=1.0, episodes=12,
                       spacing_pct=0.01, breakeven_tp_pct=0.03, invalidation_pct=0.05)
        panel_days = [{"day": "2025-10-02", "gate": 0.5, "coupling": 0.6,
                       "matured_rankic": 0.01, "good_day": 1, "ehat": {}, "score": {}}]
        audit = {str(lag): {"auroc_full": 0.7, "auroc_oos": 0.65, "days_full": 10, "days_oos": 2}
                 for lag in [R.TAU] + list(R.FALSIFICATION["embargo_audit_lags"])}
        return bars, dates, row, panel_days, audit

    def _falsification_call(self, bars, dates, row, panel_days, audit, events):
        return R.evaluate_falsification(
            panel_days, audit, {"historical": [row]}, {self.SYM: bars}, {self.SYM: dates},
            {self.SYM: {"events": events}}, {self.SYM: []}, self._meta(), [self.SYM])

    def test_falsification_item3_pools_derived_episodes(self):
        """The cap/no-cap pool must consume derived cash records, not raise KeyError: 'net'."""
        bars, dates, row, panel_days, audit = self._falsification_fixture()
        events = [{"kind": "entry", "formation_idx": 0, "direction": "long",
                   "cap_factor": R.KAPPA}]
        result = self._falsification_call(bars, dates, row, panel_days, audit, events)
        item3 = result["items"]["3"]
        self.assertEqual(item3["key"], "cap_sharpe_superiority")
        self.assertIn(item3["status"], ("pass", "fail"))
        self.assertEqual(item3["cap_sharpe"], 0.0)      # one pooled episode -> Sharpe shortcut
        self.assertEqual(item3["nocap_sharpe"], 0.0)
        self.assertIsInstance(item3["cap_max_dd_pct"], float)
        self.assertIsInstance(item3["nocap_max_dd_pct"], float)

    def test_falsification_item3_matches_derived_pool_recomputation(self):
        """Item 3's pooled Sharpe/DD must equal the derive_episodes(...) composition exactly."""
        bars, dates, row, panel_days, audit = self._falsification_fixture()
        events = [{"kind": "entry", "formation_idx": 0, "direction": "long",
                   "cap_factor": R.KAPPA},
                  {"kind": "exit", "formation_idx": 3},
                  {"kind": "entry", "formation_idx": 4, "direction": "short",
                   "cap_factor": R.KAPPA}]
        result = self._falsification_call(bars, dates, row, panel_days, audit, events)
        item3 = result["items"]["3"]
        meta = self._meta()[self.SYM]
        dca = dict((k, row[k]) for k in R.DCA_AXES)
        dca["meta"] = meta
        base_cost = R.cost_for("historical", meta)
        start_idx, end_idx = R._window_indices(dates, "full")
        raw, _m, _o = R.run_episode_sequence(bars, events, start_idx, end_idx, len(bars) - 1,
                                             R.PATH_CONFIG["full"][1], dca, base_cost, [])
        nocap_events = [dict(e, cap_factor=1.0) if e["kind"] == "entry" else e for e in events]
        raw_nocap, _m2, _o2 = R.run_episode_sequence(bars, nocap_events, start_idx, end_idx,
                                                     len(bars) - 1, R.PATH_CONFIG["full_nocap"][1],
                                                     dca, base_cost, [])
        self.assertEqual(len(raw), 2)
        self.assertEqual(len(raw_nocap), 2)
        self.assertNotIn("net", raw[0])                 # raw simulate_episode records carry no net
        with self.assertRaises(KeyError):               # ... which is why they are not poolable
            R._pooled_sharpe(raw)
        cap_pool = R.derive_episodes(raw, 1.0, 1.0)
        nocap_pool = R.derive_episodes(raw_nocap, 1.0, 1.0)
        self.assertAlmostEqual(item3["cap_sharpe"], R._pooled_sharpe(cap_pool), places=9)
        self.assertAlmostEqual(item3["nocap_sharpe"], R._pooled_sharpe(nocap_pool), places=9)
        self.assertAlmostEqual(item3["cap_max_dd_pct"], R._pooled_max_dd(cap_pool), places=9)
        self.assertAlmostEqual(item3["nocap_max_dd_pct"], R._pooled_max_dd(nocap_pool), places=9)


class TestPipelineSmoke(unittest.TestCase):
    def test_pipeline_degrades_gracefully_before_warmup(self):
        symbols = list(R.DEFAULT_INSTRUMENTS)
        panels = {sym: synth_daily(400, seed=41 + i) for i, sym in enumerate(symbols)}
        dates = {sym: [R.bar_date(b) for b in panels[sym]] for sym in symbols}
        diagnostics = {}
        streams = R.build_family_streams(symbols, panels, dates, diagnostics)
        self.assertEqual(sorted(streams["events"]), sorted(symbols))
        for sym in symbols:
            self.assertEqual(streams["events"][sym], [])
        self.assertTrue(streams["assignments"])
        self.assertTrue(all(not a["active"] for a in streams["assignments"]))
        self.assertEqual(len(streams["panel_days"]), len(streams["days"]))
        self.assertIn("gate_audit_stats", streams)
        self.assertIn(str(R.TAU), streams["gate_audit_stats"])
        json.dumps(streams["gate_audit_stats"])  # serialisable


class TestRegistryInvariants(unittest.TestCase):
    def test_dca_grid_matches_the_frozen_fingerprint_input(self):
        fingerprint = R.FINGERPRINT_INPUT
        self.assertIn("spacing_pct=0.01,0.02,0.03,0.04", fingerprint)
        self.assertIn("size_multiplier=1.0,1.1", fingerprint)
        self.assertIn("breakeven_tp_pct=0.01,0.02,0.03", fingerprint)
        self.assertIn("invalidation_pct=0.05,0.10", fingerprint)
        self.assertEqual(len(R.DCA_GRID), 48)
        keys = {tuple(sorted(cell)) for cell in R.DCA_GRID}
        self.assertEqual(keys, {tuple(sorted(R.DCA_AXES))})

    def test_expected_coverage_counts(self):
        counts = R.expected_counts(["BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"])
        self.assertEqual(counts["cohorts"], 4)
        self.assertEqual(counts["strategy_cases_per_cohort"], 1)
        self.assertEqual(counts["dca_configs_per_cohort"], 48)
        self.assertEqual(counts["base_combinations_per_cohort"], 48)
        self.assertEqual(counts["case_evaluations_per_grid"], 192)
        self.assertEqual(counts["case_evaluations_total"], 1920)
        self.assertEqual(list(counts["grids"]), list(R.GRIDS))

    def test_registered_axes_provenance_and_mechanism_constants(self):
        self.assertEqual(R.STRATEGY_CASES, [0])
        self.assertEqual(R.PARAMETER_PROVENANCE["search_domain"]["axes"], list(R.DCA_AXES))
        self.assertEqual(R.PARAMETER_PROVENANCE["constants"]["base_quote"]["value"], R.BASE_QUOTE)
        self.assertEqual(R.EXECUTION_SEMANTICS["starting_equity_usdt"], 30000.0)
        self.assertEqual(R.EXECUTION_SEMANTICS["max_leverage"], 10.0)
        self.assertEqual(R.EXECUTION_SEMANTICS["routine_active_tranches_max"], 11)
        # the registered two-level uncertainty constants
        self.assertEqual(R.TAU, 20)
        self.assertEqual(R.EMBARGO_DAYS, 90)
        self.assertEqual(R.W_PIT, 60)
        self.assertEqual(R.GATE_THETA, 0.20)
        self.assertEqual(R.KAPPA, 0.70)
        self.assertEqual(R.P_TAIL_CAP, 0.85)
        self.assertEqual(R.K_TOP, 10)
        self.assertEqual(list(R.FEATURES), ["mom_1m", "mom_3m", "mom_12m", "vol_20d",
                                            "vol_60d", "adv_20d", "cross_sectional_rank"])
        self.assertEqual(sorted(R.PATH_KEYS),
                         sorted(["historical", "oos", "full", "full_delay1", "full_slip2",
                                 "full_nocap"]))

    def test_semantic_fingerprint_matches_the_candidate_input(self):
        expected = R.sha256(R.FINGERPRINT_INPUT.encode("utf-8"))
        self.assertTrue(expected.startswith("sha256:"))
        self.assertIn("two-level-uncertainty-cross-sectional-ranker-regime-trust-gate-tail-cap-2026-09-05|",
                      R.FINGERPRINT_INPUT)
        self.assertIn("selector=cohort-selector-v1;disposition=cohort-disposition-v1",
                      R.FINGERPRINT_INPUT)

    def test_frozen_blocks_are_json_serialisable(self):
        json.dumps(R.FALSIFICATION)
        json.dumps(R.EXECUTION_SEMANTICS)
        json.dumps(R.PARAMETER_PROVENANCE)
        json.dumps(R.STRATEGY_DOMAIN)
        json.dumps(R.GATES)
        self.assertEqual(R.FALSIFICATION["coupling_min_dates"], 500)
        self.assertEqual(R.FALSIFICATION["gate_auroc_min"], 0.55)
        self.assertEqual(R.FALSIFICATION["embargo_audit_lags"], [19, 15, 10])


if __name__ == "__main__":
    unittest.main()
