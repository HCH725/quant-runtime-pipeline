#!/usr/bin/env python3
"""Executable check for the Strategy I execution engine (hourly BTC ML forecasting with the
cost-aware execution filter).

Runs inside the qlib container:
    container exec qlib-run env SI_ENGINE_PATH=/scripts/90_strategy_i_run.py \
        /opt/venv/bin/python /scripts/tests/test_strategy_i_engine.py
and on a host numpy interpreter:
    SI_ENGINE_PATH=<repo>/container/scripts/90_strategy_i_run.py python3 <this file>

Drives the pure functions with synthetic 1 h bars and synthetic forecast/position paths whose
fills, fees and funding charges are known by hand, so a regression in the cost-aware filter
(hurdles, sticky positions), the position-change entry events, the record's own exit rule, the
long-short flip, the window-bounded rail (ladder / breakeven TP / resting invalidation), the
per-fill fee ledger, the independent gross accumulator, the funding-exposure rule, the fold
geometry, the selector, the EGARCH recursion or the feature-layer causality probe fails loudly
instead of silently changing the science.  No market data, no container state, no network:
stdlib unittest + numpy only.
"""
import importlib.util
import os
import unittest

import numpy as np

ENGINE = os.environ.get("SI_ENGINE_PATH", "/scripts/90_strategy_i_run.py")
_spec = importlib.util.spec_from_file_location("si_engine", ENGINE)
si = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(si)

BAR_MS = 3600000                    # the registered 1h base grid
BASE_MS = 1735689600000             # 2025-01-01T00:00:00Z
P0 = 100.0
LEV = 10.0
CASE_LO_LGBM = si.case_fields(0, 0)
CASE_LS_LGBM = si.case_fields(1, 0)
CASE_LO_HGB = si.case_fields(0, 1)
CASE_LS_HGB = si.case_fields(1, 1)
P_LO_LGBM = dict(zip(si.CASE_FIELDS, CASE_LO_LGBM))
P_LS_LGBM = dict(zip(si.CASE_FIELDS, CASE_LS_LGBM))
# rails: FLAT closes only at the slice end (nothing else can trigger), TIGHT isolates the
# breakeven-anchored TP, LADDER isolates one scale-in, STOP isolates the resting invalidation.
RAIL_FLAT = {"base_quote": 1000.0, "spacing_d0": 0.5, "tp": 0.5, "invalidation": 0.9,
             "size_multiplier": 1.0}
RAIL_TIGHT = {"base_quote": 1000.0, "spacing_d0": 0.5, "tp": 0.01, "invalidation": 0.9,
              "size_multiplier": 1.0}
RAIL_LADDER = {"base_quote": 1000.0, "spacing_d0": 0.01, "tp": 0.5, "invalidation": 0.9,
               "size_multiplier": 1.0}
RAIL_STOP = {"base_quote": 1000.0, "spacing_d0": 0.5, "tp": 0.5, "invalidation": 0.01,
             "size_multiplier": 1.0}
EMPTY_FUNDING = {"obs_times": np.array([], dtype=np.int64),
                 "obs_rates": np.array([], dtype=np.float64),
                 "settle_bar": np.array([], dtype=np.int64),
                 "settle_bar_closed": np.array([], dtype=np.int64)}


class FakeCohort:
    """The engine's Cohort surface, driven by synthetic bars instead of the qlib data layer."""

    def __init__(self, closes, highs=None, lows=None, opens=None, volumes=None):
        c = np.array(closes, dtype=np.float64)
        n = len(c)
        self.close = c
        self.open = (np.array(opens, dtype=np.float64) if opens is not None else c.copy())
        self.high = (np.array(highs, dtype=np.float64) if highs is not None
                     else np.maximum(self.open, c))
        self.low = (np.array(lows, dtype=np.float64) if lows is not None
                    else np.minimum(self.open, c))
        self.volume = (np.array(volumes, dtype=np.float64) if volumes is not None
                       else np.full(n, 10.0))
        self.open_time_ms = BASE_MS + np.arange(n, dtype=np.int64) * BAR_MS
        self.n = n
        self.bar_ms = BAR_MS
        self.non_bar_steps = 0
        self.symbol = "BTCUSDT"
        self.timeframe = "1h"
        self.price_increment = 0.01
        self.taker_fee = 0.0005
        self.leverage = LEV
        self.margin_maint = 0.10
        self.spec = None
        self._signal_cache = {}
        self._pred_cache = {}
        self.features = None
        self.feature_report = {}

    def slice(self, start_date, end_date):
        lo = si.utc_ms(start_date)
        hi = si.utc_ms(end_date) + si.MS_PER_DAY - 1
        return (int(np.searchsorted(self.open_time_ms, lo, side="left")),
                int(np.searchsorted(self.open_time_ms, hi, side="right")))


class FakeLayer:
    """A registered signal layer: the filtered position path plus its entry events."""

    def __init__(self, pos, yhat=None, label="fake"):
        self.pos = np.array(pos, dtype=np.int64)
        self.events = np.zeros(len(pos), dtype=np.int64)
        prev = 0
        for t, v in enumerate(self.pos):
            if v != prev and v != 0:
                self.events[t] = int(v)
            prev = int(v)
        self.yhat = (np.array(yhat, dtype=np.float64) if yhat is not None
                     else np.full(len(pos), 0.01, dtype=np.float64))
        self.case_label = label
        self.diag = {"fake": True, "position_change_bars": int(np.count_nonzero(np.diff(self.pos)))}


def layer_for(cohort, case, pos, yhat=None):
    lay = FakeLayer(pos, yhat)
    cohort._signal_cache[tuple(case)] = lay
    return lay


def run(cohort, rail, pos, yhat=None, case=CASE_LO_LGBM, p=None, stress=None, slip=1,
        series=None, kind="test"):
    layer_for(cohort, case, pos, yhat)
    return si.simulate(cohort, p or P_LO_LGBM, rail, (0, cohort.n), stress or {}, slip, kind,
                       series or EMPTY_FUNDING)


class TestCostAwareFilter(unittest.TestCase):
    """The record's execution filter: hurdles, sticky positions, entry events."""

    def test_one_unit_hurdle_is_two_times_c(self):
        yhat = np.array([0.0025, 0.0015, -0.0005, 0.0021, -0.0019, -0.0021])
        path = si.build_position_path(yhat, "long_only")
        # 0.0025 > 0.0020 enters; 0.0019 (just below the hurdle) cannot exit; 0.0021 exits
        self.assertEqual(list(path["pos"]), [1, 1, 1, 1, 1, 0])
        self.assertEqual(list(path["events"]), [1, 0, 0, 0, 0, 0])
        self.assertEqual(path["diag"]["one_unit_hurdle"], 0.002)
        self.assertEqual(path["diag"]["two_unit_hurdle"], 0.004)

    def test_two_unit_hurdle_governs_the_long_short_flip(self):
        yhat = np.array([0.01, -0.0039, -0.0041])
        path = si.build_position_path(yhat, "long_short")
        # a short reversal needs |r_hat| > 0.0040: 0.0039 cannot flip, 0.0041 can
        self.assertEqual(list(path["pos"]), [1, 1, -1])
        self.assertEqual(list(path["events"]), [1, 0, -1])
        self.assertEqual(path["diag"]["sign_flips"], 1)

    def test_long_only_never_goes_short(self):
        yhat = np.array([-0.05, 0.05, -0.05])
        path = si.build_position_path(yhat, "long_only")
        self.assertEqual(list(path["pos"]), [0, 1, 0])

    def test_undefined_forecast_carries_the_position_forward(self):
        yhat = np.array([0.01, np.nan, -0.05])
        path = si.build_position_path(yhat, "long_only")
        self.assertEqual(list(path["pos"]), [1, 1, 0])

    def test_hurdle_is_strict(self):
        # |r_hat| == lambda*c*|delta| is NOT enough: the filter requires strictly greater
        path = si.build_position_path(np.array([0.002, 0.01]), "long_only")
        self.assertEqual(list(path["pos"]), [0, 1])


class TestEpisodeExecution(unittest.TestCase):
    """Entry at the next bar's open, the record's own exit, the DCA rail, per-fill accounting."""

    def test_entry_and_record_exit_prices(self):
        # pos: 0 1 1 0 0 -> entry event at bar 1 (entry at bar 2 open), exit when pos == 0 at
        # bar 3 (flatten at bar 4 open)
        closes = [P0] * 6
        opens = [P0] * 6
        co = FakeCohort(closes, opens=opens)
        m = run(co, RAIL_FLAT, [0, 1, 1, 0, 0, 0])
        self.assertEqual(m["episodes"], 1)
        self.assertEqual(m["time_exits"], 1)
        self.assertEqual(m["fills"], 2)
        # zero price move, so the whole net is the two adverse-tick fills plus their fees
        qty = 1000.0 * LEV / (P0 + 0.01)
        exp_fees = qty * (P0 + 0.01) * 0.0005 + qty * (P0 - 0.01) * 0.0005
        exp_gross = qty * (P0 - 0.01) - qty * (P0 + 0.01)
        self.assertAlmostEqual(m["fees"], exp_fees, places=6)
        self.assertAlmostEqual(m["gross_pnl"], exp_gross, places=6)
        self.assertAlmostEqual(m["net_pnl"], exp_gross - exp_fees, places=6)
        self.assertTrue(si.pnl_decomposition_ok(m))
        self.assertEqual(m["layers"][0], 1)

    def test_long_short_flip_flattens_and_enters_at_the_same_open(self):
        closes = [P0] * 6
        opens = [P0] * 6
        co = FakeCohort(closes, opens=opens)
        m = run(co, RAIL_FLAT, [0, 1, -1, -1, -1, -1], case=CASE_LS_LGBM, p=P_LS_LGBM)
        # episode 1 enters at bar 2 open and is flattened at bar 3 open (the flip bar's next
        # open), where episode 2 enters short at the same price; episode 2 ends at the slice end
        self.assertEqual(m["episodes"], 2)
        self.assertEqual(m["time_exits"], 1)
        self.assertEqual(m["open_at_end"], 1)
        self.assertEqual(m["fills"], 4)
        self.assertEqual(m["layers"][0], 2)

    def test_slice_end_flattens_every_layer(self):
        co = FakeCohort([P0] * 5)
        m = run(co, RAIL_FLAT, [0, 1, 1, 1, 1])
        self.assertEqual(m["open_at_end"], 1)
        self.assertEqual(m["episodes"], 1)

    def test_breakeven_anchored_take_profit(self):
        # entry at bar 2 open = 100.00 (plus one adverse tick); a +1% TP from the running
        # average cost fills inside bar 3
        closes = [P0, P0, P0, 102.0, 102.0]
        highs = [P0, P0, P0, 102.0, 102.0]
        lows = [P0, P0, P0, P0, P0]
        opens = [P0, P0, P0, P0, 102.0]
        co = FakeCohort(closes, highs=highs, lows=lows, opens=opens)
        m = run(co, RAIL_TIGHT, [0, 1, 1, 1, 1])
        self.assertEqual(m["tp_hits"], 1)
        self.assertEqual(m["episodes"], 1)
        avg = P0 + 0.01
        tpx = avg * 1.01
        qty = 1000.0 * LEV / avg
        exp_gross = qty * (tpx - 0.01) - qty * avg
        self.assertAlmostEqual(m["gross_pnl"], exp_gross, places=6)
        self.assertTrue(si.pnl_decomposition_ok(m))

    def test_ladder_rung_is_refilled_on_a_new_crossing_inherited_rail_semantics(self):
        # INHERITED A/B rail semantics, unchanged by this family: the walk restarts at rung 1 on
        # every bar, so a rung the price re-enters fills again (the per-bar cap of 10 adds is
        # what bounds a single bar).  Pinned deliberately, because it is a semantics a reviewer
        # must be able to read off the engine rather than infer.
        closes = [P0, P0, P0, 99.0, 99.0]
        lows = [P0, P0, P0, 98.5, 99.0]
        opens = [P0, P0, P0, P0, 99.0]
        co = FakeCohort(closes, lows=lows, opens=opens)
        m = run(co, RAIL_LADDER, [0, 1, 1, 1, 1])
        # entry + rung-1 fill at bar 3 + rung-1 REFILL at bar 4 (its low touches the rung again)
        # + the slice-end flatten
        self.assertEqual(m["fills"], 4)
        self.assertEqual(m["layers"][1], 2)
        self.assertEqual(m["layers"][0], 1)

    def test_ladder_does_not_refill_above_the_rung(self):
        closes = [P0, P0, P0, 99.0, 100.0]
        lows = [P0, P0, P0, 98.5, 100.0]
        opens = [P0, P0, P0, P0, 99.5]
        co = FakeCohort(closes, lows=lows, opens=opens)
        m = run(co, RAIL_LADDER, [0, 1, 1, 1, 1])
        self.assertEqual(m["fills"], 3)
        self.assertEqual(m["layers"][1], 1)

    def test_resting_invalidation_fills_at_the_stop(self):
        closes = [P0, P0, P0, 98.0, 98.0]
        lows = [P0, P0, P0, 98.0, 98.0]
        opens = [P0, P0, P0, P0, 98.0]
        co = FakeCohort(closes, lows=lows, opens=opens)
        m = run(co, RAIL_STOP, [0, 1, 1, 1, 1])
        self.assertEqual(m["stop_hits"], 1)
        avg = P0 + 0.01
        stop = avg * 0.99
        qty = 1000.0 * LEV / avg
        exp_gross = qty * (stop - 0.01) - qty * avg
        self.assertAlmostEqual(m["gross_pnl"], exp_gross, places=6)

    def test_fee_multiplier_moves_net_not_gross(self):
        closes = [P0, P0, P0, 102.0, 102.0]
        highs = [P0, P0, P0, 102.0, 102.0]
        lows = [P0, P0, P0, P0, P0]
        opens = [P0, P0, P0, P0, 102.0]
        co = FakeCohort(closes, highs=highs, lows=lows, opens=opens)
        base = run(co, RAIL_TIGHT, [0, 1, 1, 1, 1])
        layer_for(co, CASE_LO_LGBM, [0, 1, 1, 1, 1])
        dbl = si.simulate(co, P_LO_LGBM, RAIL_TIGHT, (0, co.n), {"fee_mult": 2.0}, 1, "test",
                          EMPTY_FUNDING)
        self.assertAlmostEqual(base["gross_pnl"], dbl["gross_pnl"], places=6)
        self.assertAlmostEqual(dbl["fees"], 2.0 * base["fees"], places=6)
        self.assertLess(dbl["net_pnl"], base["net_pnl"])

    def test_funding_is_charged_at_the_settlement_instant(self):
        co = FakeCohort([P0] * 6)
        # a settlement on the third bar (bar index 3) inside the hold
        times = np.array([int(co.open_time_ms[3])], dtype=np.int64)
        series = {"obs_times": times, "obs_rates": np.array([0.0001], dtype=np.float64),
                  "settle_bar": np.array([2], dtype=np.int64),
                  "settle_bar_closed": np.array([3], dtype=np.int64)}
        m = run(co, RAIL_FLAT, [0, 1, 1, 1, 1, 1], series=series)
        qty = 1000.0 * LEV / (P0 + 0.01)
        exp_fund = qty * P0 * 0.0001
        self.assertAlmostEqual(m["funding"], exp_fund, places=9)
        self.assertTrue(si.pnl_decomposition_ok(m))
        # a doubled funding rate doubles the charge and leaves gross untouched
        layer_for(co, CASE_LO_LGBM, [0, 1, 1, 1, 1, 1])
        dbl = si.simulate(co, P_LO_LGBM, RAIL_FLAT, (0, co.n), {"funding_mult": 2.0}, 1, "test",
                          series)
        self.assertAlmostEqual(dbl["funding"], 2.0 * exp_fund, places=9)
        self.assertAlmostEqual(dbl["gross_pnl"], m["gross_pnl"], places=6)

    def test_no_funding_stress_track_is_cost_free(self):
        co = FakeCohort([P0] * 6)
        times = np.array([int(co.open_time_ms[3])], dtype=np.int64)
        series = {"obs_times": times, "obs_rates": np.array([0.0001], dtype=np.float64),
                  "settle_bar": np.array([2], dtype=np.int64),
                  "settle_bar_closed": np.array([3], dtype=np.int64)}
        m = run(co, RAIL_FLAT, [0, 1, 1, 1, 1, 1], series=series, stress={"no_funding": True})
        self.assertEqual(m["funding"], 0.0)

    def test_short_episode_mirrors_the_ladder_and_stop(self):
        # short entry at bar 2 open, price rises into the ladder (adverse for a short); the
        # inherited rail refills rung 1 when the price re-enters it, exactly as for a long
        closes = [P0, P0, P0, 101.5, 101.5]
        highs = [P0, P0, P0, 101.5, 101.5]
        lows = [P0, P0, P0, P0, 101.5]
        opens = [P0, P0, P0, P0, 101.5]
        co = FakeCohort(closes, highs=highs, lows=lows, opens=opens)
        m = run(co, RAIL_LADDER, [0, -1, -1, -1, -1], case=CASE_LS_LGBM, p=P_LS_LGBM)
        self.assertEqual(m["fills"], 4)
        self.assertEqual(m["layers"][1], 2)
        self.assertEqual(m["layers"][0], 1)

    def test_unregistered_case_is_refused(self):
        co = FakeCohort([P0] * 4)
        bad = {k: 0 for k in si.CASE_FIELDS}
        layer_for(co, CASE_LO_LGBM, [0, 1, 1, 1])
        with self.assertRaises(SystemExit):
            si.simulate(co, bad, RAIL_FLAT, (0, co.n), {}, 1, "test", EMPTY_FUNDING)


class TestForecastLayer(unittest.TestCase):
    """Fold geometry, the EGARCH recursion's causality and the feature-layer prefix probe."""

    SPEC = {"data": {"start": "2022-01-01", "end": "2026-09-11",
                     "symbols": ["BTCUSDT"], "timeframes": [{"raw_interval": "1h",
                                                             "qlib_freq": "60min"}]}}

    def test_fold_geometry_matches_the_registered_design(self):
        folds = si.fold_bounds(self.SPEC)
        self.assertEqual(len(folds), si.MAX_FOLDS)
        f0 = folds[0]
        self.assertEqual(list(f0["train"]), ["2022-01-01", "2023-01-01"])
        self.assertEqual(list(f0["val"]), ["2023-01-01", "2023-04-01"])
        self.assertEqual(list(f0["test"]), ["2023-04-01", "2023-07-01"])
        # non-anchored rolling: the train window slides by 3 months per fold
        self.assertEqual(list(folds[1]["train"]), ["2022-04-01", "2023-04-01"])
        last = folds[-1]
        self.assertEqual(last["test"][1], "2026-09-11")   # clipped to the data end
        for fd in folds:
            self.assertEqual(fd["train"][1], fd["val"][0])
            self.assertEqual(fd["val"][1], fd["test"][0])

    def test_feature_columns_are_prefix_invariant(self):
        n = 900
        rng = np.random.default_rng(7)
        closes = P0 * np.exp(np.cumsum(rng.normal(0.0, 0.004, n)))
        co = FakeCohort(closes, highs=closes * 1.002, lows=closes * 0.998,
                        opens=np.concatenate([[closes[0]], closes[:-1]]),
                        volumes=np.abs(rng.normal(100.0, 10.0, n)))
        feats = si.build_feature_matrix(co)
        rep = si.causality_probe(co, feats, sample_bars=20)
        self.assertGreater(rep["bars_probed"], 0)
        self.assertEqual(rep["mismatches"], 0)

    def test_causality_probe_catches_a_planted_lookahead(self):
        n = 900
        rng = np.random.default_rng(11)
        closes = P0 * np.exp(np.cumsum(rng.normal(0.0, 0.004, n)))
        co = FakeCohort(closes, highs=closes * 1.002, lows=closes * 0.998,
                        opens=np.concatenate([[closes[0]], closes[:-1]]),
                        volumes=np.abs(rng.normal(100.0, 10.0, n)))
        feats = si.build_feature_matrix(co)
        col = feats["names"].index("rsi_24")
        full = closes[-1] / closes[0]
        feats["matrix"][:, col] = feats["matrix"][:, col] + full    # a future-dependent shift
        before = si.counters_total("causality_probe_mismatch")
        rep = si.causality_probe(co, feats, sample_bars=20)
        self.assertGreater(rep["mismatches"], 0)
        self.assertGreater(si.counters_total("causality_probe_mismatch"), before)

    def test_egarch_recursion_is_causal(self):
        rng = np.random.default_rng(3)
        r = rng.normal(0.0, 0.01, 600)
        theta = [0.0, 0.05, 0.10, -0.05, 0.90, 5.0]
        full = si.egarch_sigma(r, theta)
        prefix = si.egarch_sigma(r[:500], theta)
        np.testing.assert_allclose(full[:500], prefix, rtol=0.0, atol=0.0)

    def test_rank_data_averages_ties(self):
        r = si.rank_data(np.array([3.0, 1.0, 1.0, 2.0]))
        self.assertEqual(list(r), [4.0, 1.5, 1.5, 3.0])

    def test_model_matrix_accepts_a_short_sigma_prefix(self):
        """Regression (attempt u1 FAILED_SCRIPT): the EGARCH path only covers the fold's prefix,
        while the OHLCV columns span the whole window.  A matrix built from a short sigma must
        stay full length with NaNs beyond the prefix instead of raising a broadcast ValueError,
        and the rows inside the prefix must be finite."""
        n = 900
        rng = np.random.default_rng(5)
        closes = P0 * np.exp(np.cumsum(rng.normal(0.0, 0.004, n)))
        co = FakeCohort(closes, highs=closes * 1.002, lows=closes * 0.998,
                        opens=np.concatenate([[closes[0]], closes[:-1]]),
                        volumes=np.abs(rng.normal(100.0, 10.0, n)))
        feats = si.build_feature_matrix(co)
        selected = si.ta_column_names()[:si.SELECTED_TA_COUNT]
        ret1 = np.full(n, np.nan)
        ret1[1:] = np.log(closes[1:] / closes[:-1])
        m = 600
        sigma = si.egarch_sigma(ret1[:m], [0.0, 0.05, 0.10, -0.05, 0.90, 5.0])
        X, names = si._model_matrix(feats, selected, sigma, ret1)
        self.assertEqual(X.shape, (n, 5 + si.SELECTED_TA_COUNT + 2))
        self.assertEqual(len(names), X.shape[1])
        self.assertTrue(np.isfinite(X[m - 1, -2:]).all())
        self.assertTrue(np.isnan(X[m:, -2:]).all())

    def test_egarch_sigma_survives_a_leading_non_finite_return(self):
        """Regression (attempt u1): the registered return series starts with a non-finite element
        (bar 0 has no previous close).  An unguarded recursion turns every later sigma into NaN,
        which would silently empty every EGARCH column and every prediction."""
        r = np.array([np.nan, 0.004, -0.003, 0.001, 0.002])
        sigma = si.egarch_sigma(r, [0.0, 0.05, 0.10, -0.05, 0.90, 5.0])
        self.assertTrue(np.isfinite(sigma).all())
        self.assertAlmostEqual(sigma[1], sigma[0], places=12)   # no information -> carried
        self.assertTrue((sigma > 0.0).all())


class TestSelectorAndDomain(unittest.TestCase):
    SPEC = {"gates": {"min_episodes_is": 10, "min_episodes_oos": 3,
                      "neighborhood_min_same_sign_fraction": 0.6},
            "parameter_domain": {"grid_cases": [dict(zip(si.CASE_FIELDS, c))
                                                for c in si.CASE_ORDER]},
            "dca_domain": {"spacing_pct": [0.01, 0.02, 0.03, 0.04],
                           "size_multiplier": [1.0, 1.1],
                           "breakeven_tp_pct": [0.01, 0.02, 0.03],
                           "invalidation_pct": [0.05, 0.10], "base_quote": 1000.0}}

    def _row(self, case, dca, net, sharpe, eps, kind="historical"):
        row = {f: v for f, v in zip(si.CASE_FIELDS, case)}
        row.update(dca)
        row.update({"window_kind": kind, "net_pnl": net, "sharpe": sharpe, "episodes": eps,
                    "symbol": "BTCUSDT", "timeframe": "1h"})
        return row

    def test_case_order_and_helpers(self):
        self.assertEqual(len(si.CASE_ORDER), 4)
        self.assertEqual(si.case_name(CASE_LO_LGBM), "long_only__lightgbm")
        self.assertEqual(si.case_name(CASE_LS_HGB), "long_short__sklearn_hist_gbm")
        self.assertEqual(si.case_direction(CASE_LS_LGBM), 1)
        self.assertEqual(si.case_model(CASE_LO_HGB), 1)

    def test_selector_is_historical_only_and_deterministic(self):
        dca = {"spacing_pct": 0.01, "size_multiplier": 1.0, "breakeven_tp_pct": 0.01,
               "invalidation_pct": 0.05}
        rows = [self._row(CASE_LO_LGBM, dca, 100.0, 1.0, 20),
                self._row(CASE_LS_LGBM, dca, 500.0, 2.0, 20),
                self._row(CASE_LO_HGB, dca, 900.0, 0.5, 20)]
        w1, reason = si.select_cohort_winner(rows, self.SPEC)
        self.assertEqual(reason, "selected")
        self.assertEqual(si.case_tuple(w1), CASE_LS_LGBM)     # Sharpe desc wins
        shuffled = list(rows)
        np.random.default_rng(0).shuffle(shuffled)
        w2, _ = si.select_cohort_winner(shuffled, self.SPEC)
        self.assertEqual(si.cell_key(w1), si.cell_key(w2))

    def test_selector_refuses_oos_rows(self):
        dca = {"spacing_pct": 0.01, "size_multiplier": 1.0, "breakeven_tp_pct": 0.01,
               "invalidation_pct": 0.05}
        rows = [self._row(CASE_LO_LGBM, dca, 100.0, 1.0, 20, kind="oos")]
        with self.assertRaises(ValueError):
            si.select_cohort_winner(rows, self.SPEC)

    def test_insufficient_trades_culls_the_cohort(self):
        dca = {"spacing_pct": 0.01, "size_multiplier": 1.0, "breakeven_tp_pct": 0.01,
               "invalidation_pct": 0.05}
        rows = [self._row(CASE_LO_LGBM, dca, 100.0, 1.0, 3)]
        w, reason = si.select_cohort_winner(rows, self.SPEC)
        self.assertIsNone(w)
        self.assertEqual(reason, "insufficient_trades")

    def test_no_qualifying_candidate(self):
        dca = {"spacing_pct": 0.01, "size_multiplier": 1.0, "breakeven_tp_pct": 0.01,
               "invalidation_pct": 0.05}
        rows = [self._row(CASE_LO_LGBM, dca, -100.0, -1.0, 20)]
        w, reason = si.select_cohort_winner(rows, self.SPEC)
        self.assertIsNone(w)
        self.assertEqual(reason, "no_qualifying_candidate")

    def test_family_disposition_mapping(self):
        self.assertEqual(si.family_disposition([], True)["verdict_recommendation"], "REJECT")
        self.assertEqual(si.family_disposition([{"cohort": "x"}], True)["verdict_recommendation"],
                         "PASS")
        self.assertEqual(si.family_disposition([], False)["verdict_recommendation"],
                         "TECHNICAL_INCOMPLETE")


class TestFamilyReaders(unittest.TestCase):
    """The three registered family-level readers must run end to end.  They build a rail from the
    elected winner's cell, which needs the registered base_quote project constant - the defect
    that failed attempt u2 (KeyError: 'base_quote')."""

    SPEC = {
        "data": {"start": "2022-01-01", "end": "2026-07-01",
                 "historical_start": "2022-01-01", "historical_end": "2025-09-30",
                 "oos_start": "2025-10-01", "oos_end": "2026-07-01"},
        "costs": {"baseline_slippage_ticks": 1},
        "dca_domain": {"base_quote": 1000.0},
        "gates": {"min_episodes_is": 10, "min_episodes_oos": 3,
                  "neighborhood_min_same_sign_fraction": 0.6},
    }

    def _inputs(self):
        n = 40000
        co = FakeCohort([P0] * n)
        co.open_time_ms = si.utc_ms("2022-01-01") + np.arange(n, dtype=np.int64) * BAR_MS
        pos = np.zeros(n, dtype=np.int64)
        pos[100:400] = 1
        pos[30000:30600] = 1
        layer_for(co, CASE_LO_LGBM, pos)
        dca = {"spacing_pct": 0.01, "size_multiplier": 1.0, "breakeven_tp_pct": 0.01,
               "invalidation_pct": 0.05}
        winner = dict(zip(si.CASE_FIELDS, CASE_LO_LGBM))
        winner.update(dca)
        winner.update({"symbol": "BTCUSDT", "timeframe": "1h", "window_kind": "historical",
                       "net_pnl": 10.0, "sharpe": 1.0, "episodes": 5})
        row = dict(winner)
        results = [{"cohort": "BTCUSDT/1h", "winner": winner, "case_name": "long_only__lightgbm",
                    "metrics": {"oos": {"sharpe": 0.5}}}]
        return results, {"BTCUSDT/1h": (co, EMPTY_FUNDING)}, {"BTCUSDT/1h": {"fee_2x": [row]}}

    def test_readers_run_end_to_end(self):
        results, diag, rows = self._inputs()
        fwd = si.forward_extension_sharpe_check(self.SPEC, results, diag)
        sen = si.cost_sensitivity_check(self.SPEC, results, diag, rows)
        xa = si.cross_asset_replication_check(self.SPEC, results, diag, diag)
        for out in (fwd, sen, xa):
            self.assertTrue(out["evaluated"])
            self.assertIn("hit", out)
            self.assertIn("family-level", out["landing"])
        self.assertIn("c_10_bps", sen["c_bps_tracks"])
        self.assertIn("c_25_bps", sen["c_bps_tracks"])
        # the c == 10 bps cross-check is a REAL equality test, not a vacuous flag: with the
        # fixture's arbitrary grid row it must disagree, and once the grid row carries the
        # reader's own c = 10 bps value it must agree.
        self.assertFalse(sen["c_bps_tracks"]["cross_check_vs_fee_2x_grid"]["matches"])
        rows["BTCUSDT/1h"]["fee_2x"][0]["net_pnl"] = sen["c_bps_tracks"]["c_10_bps"]["net_pnl"]
        sen2 = si.cost_sensitivity_check(self.SPEC, results, diag, rows)
        self.assertTrue(sen2["c_bps_tracks"]["cross_check_vs_fee_2x_grid"]["matches"])
        self.assertIn("BTCUSDT/1h", xa["assets"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
