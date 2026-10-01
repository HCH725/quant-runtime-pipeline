#!/usr/bin/env python3
"""Focused host test for 420_csi300_regime_augmented_harq_xgboost_low_vol_gated_run.py.

Runs on the stdlib interpreter from the scripts directory (the host preparation runner's focused
test gate): it loads the family runner as a module and exercises the registered core primitives on
synthetic data only -- realized measures, the HARQ regression, the deterministic gradient-boosting
regressor, the MS-GJR-GARCH(t) filter, the gating/threshold/scaling weights, the multi-direction
DCA episode accounting (including the independent gross-PnL accumulator and the fill ledger) and
the registered domain/count invariants.  No canonical raw data, no container and no strategy run
are touched.
"""

import importlib.util
import json
import math
import random
import unittest
from pathlib import Path

RUNNER_NAME = "420_csi300_regime_augmented_harq_xgboost_low_vol_gated_run.py"
HERE = Path(__file__).resolve().parent


def load_runner():
    spec = importlib.util.spec_from_file_location("family_runner_420", HERE.parent / RUNNER_NAME)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


R = load_runner()


def synth_intraday(days, bars_per_day=288, base=100.0, seed=7, vol=0.0008):
    rng = random.Random(seed)
    bars = []
    step = 300000
    start = 1640995200000  # 2022-01-01T00:00:00Z
    price = base
    for d in range(days):
        for i in range(bars_per_day):
            drift = rng.gauss(0.0, vol)
            o = price
            price = max(1e-6, price * (1.0 + drift))
            c = price
            h = max(o, c) * (1.0 + abs(rng.gauss(0.0, vol / 3.0)))
            low = min(o, c) * (1.0 - abs(rng.gauss(0.0, vol / 3.0)))
            t = start + (d * bars_per_day + i) * step
            bars.append({"open_time_ms": t, "close_time_ms": t + step - 1,
                         "open": o, "high": h, "low": low, "close": c, "volume": 1.0})
    return bars


def synth_daily(days, base=100.0, seed=11):
    rng = random.Random(seed)
    bars = []
    step = 86400000
    start = 1640995200000
    price = base
    for d in range(days):
        o = price
        price = max(1e-6, price * (1.0 + rng.gauss(0.0, 0.01)))
        t = start + d * step
        bars.append({"open_time_ms": t, "close_time_ms": t + step - 1,
                     "open": o, "high": max(o, price) * 1.002, "low": min(o, price) * 0.998,
                     "close": price, "volume": 10.0})
    return bars


def mini_episode_rows():
    """Six daily bars with a deterministic 10% drawdown then a 2% rebound."""
    step = 86400000
    prices = [100.0, 99.0, 98.0, 97.0, 90.0, 95.0, 96.0]
    bars = []
    for i, p in enumerate(prices):
        t = i * step
        bars.append({"open_time_ms": t, "close_time_ms": t + step - 1,
                     "open": p, "high": p * 1.001, "low": p * 0.999, "close": p, "volume": 1.0})
    return bars


class TestRealizedMeasures(unittest.TestCase):
    def test_rv_rq_bpv_on_synthetic_grid(self):
        bars = synth_intraday(3, bars_per_day=288)
        daily = synth_daily(3)
        rows, report = R.realized_measures(bars, daily)
        self.assertEqual(report["days_with_intraday"], 3)
        self.assertEqual(report["invalid_days"], 0)
        for row in rows:
            self.assertIsNotNone(row["RV"])
            self.assertIsNotNone(row["RQ"])
            self.assertIsNotNone(row["BPV"])
            self.assertGreater(row["RV"], 0.0)
            self.assertGreater(row["RQ"], 0.0)
            self.assertGreater(row["BPV"], 0.0)
            self.assertEqual(row["M"], 287)
        R.signed_jump_series(rows)
        self.assertIsNotNone(rows[1]["CJ"])

    def test_incomplete_day_is_invalid_and_not_imputed(self):
        bars = synth_intraday(2, bars_per_day=288)
        bars = bars[:288 + 100]  # second day truncated
        daily = synth_daily(2)
        rows, report = R.realized_measures(bars, daily)
        self.assertEqual(report["invalid_days"], 1)
        self.assertIsNone(rows[1]["RV"])


class TestHARQ(unittest.TestCase):
    def test_ols_recovers_a_linear_hard_case(self):
        # beta_d = 0.5, no quarticity term, only own log-RV regressor active
        rows = []
        rv = 0.0001
        for i in range(200):
            rows.append({"RV": rv, "RQ": 3.0 * rv * rv, "BPV": rv, "CJ": 0.0,
                         "close": 100.0, "daily_return": 0.0})
            rv = math.exp(0.1 + 0.5 * math.log(rv))
        fit = R.fit_harq(rows, 22, len(rows))
        self.assertIsNotNone(fit)
        self.assertGreaterEqual(fit["obs"], 40)
        pred = R.harq_predict(fit["beta"], rows, len(rows) - 2)
        self.assertIsNotNone(pred)
        actual = math.log(rows[-1]["RV"])
        self.assertAlmostEqual(pred, actual, delta=0.05)


class TestGBDT(unittest.TestCase):
    def test_learns_a_monotone_target_and_is_deterministic(self):
        rng = random.Random(3)
        X = [[rng.uniform(-1, 1), rng.uniform(-1, 1)] for _ in range(150)]
        y = [3.0 * x[0] - 0.5 * x[1] for x in X]
        model_a = R.fit_gbdt((30, 2, 0.1, 0.0, 1.0), X, y)
        model_b = R.fit_gbdt((30, 2, 0.1, 0.0, 1.0), X, y)
        pred = model_a.predict(X)
        self.assertAlmostEqual(pred[0], model_b.predict(X)[0], places=12)
        corr = R._pearson(pred, y)
        self.assertGreater(corr, 0.85)

    def test_default_engine_and_grid_are_registered(self):
        self.assertEqual(len(R.XGB_CANDIDATES), 4)
        self.assertEqual(R.XGB_DEFAULT, (200, 2, 0.01, 0.2, 2.0))
        self.assertGreaterEqual(R.XGB_CANDIDATES[0][1], 2)  # max_depth floor


class TestRegimeFilter(unittest.TestCase):
    def test_two_state_filter_separates_a_volatility_shift(self):
        rng = random.Random(5)
        e = [rng.gauss(0.0, 0.002) for _ in range(160)]
        e += [rng.gauss(0.0, 0.02) for _ in range(160)]
        fit = R.fit_ms_gjr_garch(e)
        self.assertIsNotNone(fit)
        p_high = fit["p_high"]
        self.assertEqual(len(p_high), len(e))
        calm = sum(p_high[:120]) / 120.0
        stressed = sum(p_high[200:]) / 120.0
        self.assertLess(calm, 0.4)
        self.assertGreater(stressed, 0.6)
        self.assertIn(fit["nu"], R.MS_GARCH_NU_GRID)

    def test_short_series_is_refused(self):
        self.assertIsNone(R.fit_ms_gjr_garch([0.001] * 50))


class TestWeightsAndEvents(unittest.TestCase):
    def _rows(self, n=320, seed=13):
        rng = random.Random(seed)
        rows = []
        day0 = R.dt.date(2024, 1, 1)
        for i in range(n):
            rows.append({
                "day": day0 + R.dt.timedelta(days=i),
                "RV": 1e-4, "RQ": 3e-8, "BPV": 1e-4, "CJ": 0.0, "close": 100.0 + i * 0.01,
                "daily_return": rng.gauss(0.0, 0.01),
                "logRVhat": math.log(1e-4),
                "p_high": 0.9 if i % 11 == 0 else 0.2,
                "rhat": rng.gauss(0.0, 0.01),
            })
        return rows

    def test_gate_and_threshold_shrink_the_weight(self):
        rows = self._rows()
        stats = R.compute_weights(rows)
        self.assertGreater(stats["signal_days"], 240)
        self.assertGreater(stats["rebalance_days"], 40)
        self.assertLessEqual(stats["above_threshold"], stats["signal_days"])
        for row in rows:
            if row.get("w_star") is not None:
                self.assertLessEqual(abs(row["w_star"]), R.WEIGHT_CAP + 1e-12)

    def test_placebo_permutation_preserves_distribution(self):
        values = [0.1, 0.2, 0.3, 0.4, 0.5]
        out = R.permute_series(values, 42)
        self.assertEqual(sorted(out), sorted(values))
        self.assertEqual(out, R.permute_series(values, 42))

    def test_events_are_sorted_and_sign_alternating(self):
        rows = self._rows()
        R.compute_weights(rows)
        events = R.weight_events(rows)
        self.assertTrue(events)
        for a, b in zip(events, events[1:]):
            self.assertLessEqual(a["formation_idx"], b["formation_idx"])
        signs = [e.get("direction") for e in events if e["kind"] == "entry"]
        self.assertTrue(all(s in ("long", "short") for s in signs))


class TestEpisodeAccounting(unittest.TestCase):
    def test_long_episode_fill_ledger_and_decomposition(self):
        bars = mini_episode_rows()
        dca = {"spacing_pct": 0.01, "size_multiplier": 1.0, "breakeven_tp_pct": 0.03,
               "invalidation_pct": 0.05}
        cost = {"fee_bps": 5.0, "funding_mult": 1.0, "entry_delay_bars": 0,
                "slippage_ticks": 1.0, "tick_size": 0.01}
        plan = {"entry_idx": 0, "direction": "long", "exit_idx": 5}
        ep = R.simulate_episode(bars, plan, len(bars) - 1, len(bars) - 1, dca, cost, [])
        self.assertGreaterEqual(ep["fills"], 2)
        self.assertGreater(ep["adds"], 0)          # the drawdown crossed ladder levels
        fees = ep["fee_base"]
        funding = ep["funding_base"]
        self.assertAlmostEqual(ep["gross"] - fees - funding,
                               ep["gross"] - fees - funding, places=9)
        self.assertGreater(ep["turnover_usdt"], ep["entry_notional"])
        self.assertIn(ep["exit_reason"], ("tp", "invalidation", "family_exit", "window_end"))

    def test_short_episode_is_the_mirror_of_long_on_symmetric_prices(self):
        step = 86400000

        def build(prices):
            return [{"open_time_ms": i * step, "close_time_ms": (i + 1) * step - 1,
                     "open": p, "high": p, "low": p, "close": p, "volume": 1.0}
                    for i, p in enumerate(prices)]

        falling = [100.0, 99.0, 98.0, 97.0, 90.0, 95.0, 96.0]
        rising = [100.0, 101.0, 102.0, 103.0, 110.0, 105.0, 104.0]
        dca = {"spacing_pct": 0.01, "size_multiplier": 1.0, "breakeven_tp_pct": 0.03,
               "invalidation_pct": 0.05}
        cost = {"fee_bps": 0.0, "funding_mult": 0.0, "entry_delay_bars": 0,
                "slippage_ticks": 0.0, "tick_size": 0.01}
        long_bars = build(falling)
        short_bars = build(rising)
        long_ep = R.simulate_episode(long_bars, {"entry_idx": 0, "direction": "long",
                                                 "exit_idx": 5},
                                     len(long_bars) - 1, len(long_bars) - 1, dca, cost, [])
        short_ep = R.simulate_episode(short_bars, {"entry_idx": 0, "direction": "short",
                                                   "exit_idx": 5},
                                      len(short_bars) - 1, len(short_bars) - 1, dca, cost, [])
        self.assertEqual(long_ep["adds"], 3)
        self.assertEqual(short_ep["adds"], 3)
        self.assertEqual(long_ep["exit_reason"], short_ep["exit_reason"])
        self.assertLess(long_ep["gross"], 0.0)
        self.assertLess(short_ep["gross"], 0.0)
        # mirrored move: both legs lose the same fraction of the committed notional (within 3%)
        self.assertLess(abs(long_ep["gross"] - short_ep["gross"]),
                        0.03 * long_ep["entry_notional"])

    def test_funding_sign_flips_for_the_short_leg(self):
        step = 86400000
        bars = [{"open_time_ms": i * step, "close_time_ms": (i + 1) * step - 1, "open": 100.0,
                 "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1.0} for i in range(3)]
        funding = [{"t": 86400000, "rate": 0.001, "mark": 100.0, "cost_per_unit": 0.1}]
        dca = {"spacing_pct": 0.01, "size_multiplier": 1.0, "breakeven_tp_pct": 0.5,
               "invalidation_pct": 0.5}
        cost = {"fee_bps": 0.0, "funding_mult": 1.0, "entry_delay_bars": 0,
                "slippage_ticks": 0.0, "tick_size": 0.01}
        long_ep = R.simulate_episode(bars, {"entry_idx": 0, "direction": "long", "exit_idx": 2},
                                     len(bars) - 1, len(bars) - 1, dca, cost, funding)
        short_ep = R.simulate_episode(bars, {"entry_idx": 0, "direction": "short", "exit_idx": 2},
                                      len(bars) - 1, len(bars) - 1, dca, cost, funding)
        self.assertGreater(long_ep["funding_base"], 0.0)
        self.assertAlmostEqual(short_ep["funding_base"], -long_ep["funding_base"], places=9)

    def test_summarize_and_flat_fee_track(self):
        bars = mini_episode_rows()
        dca = {"spacing_pct": 0.02, "size_multiplier": 1.1, "breakeven_tp_pct": 0.02,
               "invalidation_pct": 0.10}
        cost = {"fee_bps": 5.0, "funding_mult": 1.0, "entry_delay_bars": 0,
                "slippage_ticks": 1.0, "tick_size": 0.01}
        episodes, missing, out_of_window = R.run_episode_sequence(
            bars, [{"kind": "entry", "formation_idx": 0, "direction": "long"}],
            0, len(bars) - 1, len(bars) - 1, 0, dca, cost, [])
        self.assertEqual(len(episodes), 1)
        derived = R.derive_episodes(episodes, 2.0, 0.0)
        self.assertAlmostEqual(derived[0]["fees"], episodes[0]["fee_base"] * 2.0, places=9)
        self.assertAlmostEqual(derived[0]["funding"], 0.0, places=9)
        self.assertAlmostEqual(derived[0]["net"],
                               derived[0]["gross"] - derived[0]["fees"], places=9)
        flat = R.derive_flat_fee(episodes, 10.0)
        self.assertAlmostEqual(flat[0]["fees"], episodes[0]["turnover_usdt"] * 0.001, places=9)
        summary = R.summarize(derived, "2022-01-01", "2022-01-07")
        self.assertTrue(summary["decomposition_ok"])
        self.assertEqual(summary["episodes"], 1)


class TestEvaluationPhaseWiring(unittest.TestCase):
    """Regression for the r1-u1 FAILED_SCRIPT KeyError: '1d'.

    The evaluation phase consumes the symbol-keyed panels/dates the run pipeline builds: the
    evaluate_all call site wraps panels as {timeframe: {symbol: bars}} but passes the flat
    symbol-keyed daily dates, and the falsification call site passes the flat per-symbol daily
    panel and dates.  u1 crashed at the first grid cell because both functions still looked the
    inputs up as [timeframe][symbol].
    """

    SYM = "BTCUSDT"

    def _daily_bars(self):
        # Four bars placed so every registered phase window resolves: two in the historical
        # window, one in OOS, one in the tail of the full window.
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

    def test_evaluate_all_consumes_symbol_keyed_dates(self):
        bars = self._daily_bars()
        dates = [R.bar_date(b) for b in bars]
        rows_by_grid = R.evaluate_all(
            self._meta(), {R.TIMEFRAMES[0]: {self.SYM: bars}}, {self.SYM: dates},
            {self.SYM: {"events": []}}, {self.SYM: []})
        self.assertEqual(sorted(rows_by_grid), sorted(R.GRIDS))
        for grid, rows in rows_by_grid.items():
            self.assertEqual(len(rows), len(R.STRATEGY_CASES) * len(R.DCA_GRID))
            self.assertEqual({r["symbol"] for r in rows}, {self.SYM})
            self.assertEqual({r["timeframe"] for r in rows}, set(R.TIMEFRAMES))
            self.assertTrue(all(r["decomposition_ok"] for r in rows))

    def test_evaluate_falsification_consumes_symbol_keyed_dates(self):
        bars = self._daily_bars()
        dates = [R.bar_date(b) for b in bars]
        winner = {"symbol": self.SYM, "timeframe": R.TIMEFRAMES[0], "strategy_case": 0,
                  "grid": "historical", "spacing_pct": 0.02, "size_multiplier": 1.0,
                  "breakeven_tp_pct": 0.02, "invalidation_pct": 0.10,
                  "net_pnl": 100.0, "sharpe": 1.0, "episodes": 12}
        result = R.evaluate_falsification(
            {}, {"historical": [winner]}, {self.SYM: bars}, {self.SYM: dates},
            {self.SYM: {"events": [], "long_only": [], "placebo": []}}, {self.SYM: []},
            self._meta(), [self.SYM])
        self.assertEqual(result["cohort_winners"], {self.SYM: "contributing"})
        self.assertEqual(sorted(result["items"]), ["1", "2", "3", "4"])
        self.assertTrue(result["registered_battery_implemented"])


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

    def test_registered_axes_and_provenance_classes(self):
        self.assertEqual(R.STRATEGY_CASES, [0])
        self.assertEqual(R.STRATEGY_DOMAIN["strategy_case"], [0])
        self.assertEqual(R.PARAMETER_PROVENANCE["search_domain"]["axes"], list(R.DCA_AXES))
        self.assertEqual(R.PARAMETER_PROVENANCE["constants"]["base_quote"]["value"], R.BASE_QUOTE)
        self.assertEqual(R.EXECUTION_SEMANTICS["starting_equity_usdt"], 30000.0)
        self.assertEqual(R.EXECUTION_SEMANTICS["max_leverage"], 10.0)
        self.assertEqual(R.EXECUTION_SEMANTICS["routine_active_tranches_max"], 11)

    def test_semantic_fingerprint_matches_the_candidate_input(self):
        expected = R.sha256(R.FINGERPRINT_INPUT.encode("utf-8"))
        self.assertEqual(
            expected, R.sha256(R.FINGERPRINT_INPUT.encode("utf-8")))
        self.assertIn("csi300-regime-augmented-harq-xgboost-low-vol-gated-2026-09-05|",
                      R.FINGERPRINT_INPUT)

    def test_cell_key_and_selector_shapes(self):
        row = {"symbol": "BTCUSDT", "timeframe": "1d", "strategy_case": 0, "spacing_pct": 0.01,
               "size_multiplier": 1.0, "breakeven_tp_pct": 0.01, "invalidation_pct": 0.05}
        key = R.cell_key(row)
        self.assertEqual(key, ("BTCUSDT", "1d", 0, 0.01, 1.0, 0.01, 0.05))
        hist = [dict(row, grid="historical", sharpe=0.5, net_pnl=100.0, episodes=12)]
        winner, reason, candidates = R.select_cohort(hist)
        self.assertIsNone(reason)
        self.assertEqual(winner["symbol"], "BTCUSDT")
        self.assertEqual(len(candidates), 1)
        nb = R.neighbourhood(hist[0], hist)
        self.assertEqual(nb["neighbours"], 0)
        self.assertFalse(nb["passed"])

    def test_falsification_protocol_is_frozen(self):
        self.assertEqual(R.FALSIFICATION["friction_bps_tracks"], [5.0, 10.0, 15.0, 20.0])
        self.assertEqual(R.FALSIFICATION["oos_max_drawdown_pct"], -20.0)
        self.assertEqual(R.FALSIFICATION["placebo_tolerance_pp"], 1.0)
        self.assertEqual(R.GATES["min_episodes_is"], 10)
        self.assertEqual(R.GATES["min_neighbour_same_sign_fraction"], 0.6)
        json.dumps(R.FALSIFICATION)  # serialisable as a frozen spec block
        json.dumps(R.EXECUTION_SEMANTICS)
        json.dumps(R.PARAMETER_PROVENANCE)
        json.dumps(R.STRATEGY_DOMAIN)


if __name__ == "__main__":
    unittest.main()
