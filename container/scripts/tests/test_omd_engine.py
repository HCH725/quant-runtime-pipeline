#!/usr/bin/env python3
"""Focused host-side OMD arithmetic / causality / accounting checks; no /results writes.

Run:  python3 container/scripts/tests/test_omd_engine.py
The live Qlib 0.9.7 readback path (raw -> CSV -> dump_bin -> qlib.data.D) is
exercised at production launch, not by this host-side test.
"""
import copy
import csv
import gzip
import importlib.util
import itertools
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

ENGINE = Path(__file__).resolve().parents[1] / "230_omd_run.py"
_spec = importlib.util.spec_from_file_location("omd_engine", ENGINE)
omd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(omd)


def miniature(n=12):
    ms = np.arange(n, dtype=np.int64) * omd.MS_DAY
    panel = {"open_ms": ms, "open": np.full(n, 100.), "close": np.full(n, 100.),
             "high": np.full(n, 100.5), "low": np.full(n, 99.5)}
    layer = np.zeros((n, 4))
    dca = dict(omd.DCA_GRID[0])
    tick_fee = {"tick": 0.1, "taker_fee": 0.0005}
    return panel, [[] for _ in range(n)], layer, dca, tick_fee


def filled_spec():
    """run_spec_template() + only the identity/timestamp fields the caller owns."""
    spec = omd.run_spec_template()
    spec["round_id"] = omd.FAMILY_ID + "-r1"
    spec["run_id"] = spec["round_id"] + "-u1"
    spec["created_at_utc"] = "2026-09-27T00:00:00Z"
    return spec


def with_config(root):
    meta = Path(root) / "_meta"
    meta.mkdir(parents=True, exist_ok=True)
    (meta / "CONFIG.json").write_text(json.dumps({
        "market_type": "usdm_perp", "symbols": list(omd.SYMBOLS),
        "intervals": ["1d"], "datasets": {"klines": "present", "funding": "present"}}))


class TestObservedThreeMatrices(unittest.TestCase):
    def test_bins_four_symbols_map_deterministically_into_five_and_ten(self):
        values = np.array([3., 1., 4., 2.])
        self.assertEqual(omd.quantile_states(values, 5).tolist(), [4, 1, 5, 2])
        self.assertEqual(omd.quantile_states(values, 10).tolist(), [7, 1, 10, 4])
        self.assertEqual(omd.quantile_states(np.ones(4), 10).tolist(), [1, 4, 7, 10])

    def test_only_empirical_transition_rows_no_fabricated_pseudocount(self):
        # A transition exists only between two consecutive observed decisions; there is
        # no smoothing term anywhere. Occupied rows are stochastic, unoccupied rows stay
        # exactly zero so an empty bin can never borrow a neighbour's mass.
        states = [np.array([1, 2]), np.array([2, 3]), np.array([1, 2])]
        t, totals = omd.transition_matrix(states, 3)
        np.testing.assert_array_equal(totals, [1, 2, 1])          # observed-row occupancy
        np.testing.assert_allclose(t, [[0, 1, 0], [.5, 0, .5], [0, 1, 0]])
        np.testing.assert_allclose(t[totals > 0].sum(axis=1), 1)  # every used row sums to 1
        wide, wide_totals = omd.transition_matrix(states, 5)
        self.assertEqual(wide_totals[3], 0)                        # state 4 never observed
        np.testing.assert_allclose(wide[3], np.zeros(5))           # stays exactly zero
        self.assertEqual(wide_totals.tolist(), [1, 2, 1, 0, 0])
        self.assertTrue((wide_totals[[0, 1, 2]] > 0).all())

    def test_residual_pca_d_measured_but_two_asset_optimum_is_provably_5050(self):
        rng = np.random.default_rng(19)
        factor = rng.normal(0, 0.02, (252, 1))
        window = factor @ np.array([[1., .7, -.2, 1.4]]) + rng.normal(0, .01, (252, 4))
        d, residual = omd.distance_matrices(window)
        self.assertEqual(d.shape, (4, 4)); self.assertEqual(residual.shape, (4, 4))
        self.assertGreater(np.max(abs(d - residual)), .05)  # PCA removal really changes D
        self.assertGreater(residual[0, 1], 0)
        for different_d in (.05, .25, .9):
            w, objective = omd.lo_optimizer_two(different_d)
            np.testing.assert_array_equal(w, [.5, .5])
            self.assertAlmostEqual(objective, different_d / 2)
            self.assertGreaterEqual(
                objective,
                max(2 * different_d * x * (1 - x) for x in np.linspace(0, 1, 1001)) - 1e-12)

    def test_principal_defect_d_cannot_be_faked_into_the_weights(self):
        """Fails if a later edit makes LO weights respond to distance (or hides it)."""
        r = np.array([4., 3., 2., 1.])
        score = np.array([4., 3., 2., 1.])
        def compose(value):
            mat = np.full((4, 4), value)
            np.fill_diagonal(mat, 0)
            return omd.compose_weights(r, score, mat, 0.5)
        base = compose(.1)
        moved = compose(.9)
        wt, ls, lo, objective, selected = base
        moved_wt, moved_ls, moved_lo, moved_objective, moved_selected = moved
        np.testing.assert_array_equal(ls, moved_ls)        # LS sleeve ignores D
        np.testing.assert_array_equal(lo, moved_lo)        # LO sleeve ignores D (it is degenerate)
        np.testing.assert_array_equal(selected, moved_selected)
        np.testing.assert_array_equal(wt, moved_wt)        # the traded vector never moves with D
        self.assertAlmostEqual(ls.sum(), 0.)               # dollar-neutral, net gross 0
        self.assertAlmostEqual(ls[0] + ls[3], 0.)
        self.assertAlmostEqual(lo.sum(), 1.)
        np.testing.assert_array_equal(lo[:2], [.5, .5])    # the analytically degenerate optimum
        self.assertAlmostEqual(objective, .1 / 2)
        self.assertAlmostEqual(moved_objective, .9 / 2)    # only the measured objective moves
        self.assertNotEqual(objective, moved_objective)
        np.testing.assert_allclose(wt, .5 * ls + .5 * lo)   # blend is what actually trades
        layer = {"lo_weights": np.array([[.5, .5, 0, 0]]),
                 "diag": {"decision_indices": [0]}}
        self.assertTrue(omd.lo_optimum_is_exactly_5050(layer))
        layer["lo_weights"] = np.array([[.6, .4, 0, 0]])   # fabricated D sensitivity
        self.assertFalse(omd.lo_optimum_is_exactly_5050(layer))

    def test_ls_dollar_neutral_short_and_blend_are_both_signed(self):
        dr = np.full((4, 4), 0.5)
        np.fill_diagonal(dr, 0)
        wt, ls, lo, _, selected = omd.compose_weights(
            np.array([4., 3., 2., 1.]), np.array([4., 3., 2., 1.]), dr, .5)
        np.testing.assert_array_equal(ls, [.5, 0, 0, -.5])
        np.testing.assert_array_equal(lo, [.5, .5, 0, 0])
        np.testing.assert_allclose(wt, .5 * ls + .5 * lo)
        self.assertLess(wt[-1], 0)          # the signed short survives the blend
        self.assertAlmostEqual(ls.sum(), 0) # market neutral sleeve
        self.assertAlmostEqual(lo.sum(), 1)
        self.assertEqual(selected, (0, 1))

    def test_signal_prefix_invariance_and_null_replacement_parity(self):
        rng = np.random.default_rng(44)
        returns = rng.normal(0, .009, (335, 4)) + np.array([.0002, .0003, .0004, .0001])
        closes = 100 * np.exp(np.cumsum(returns, axis=0))
        ms = omd.utc_ms("2022-01-01") + np.arange(335) * omd.MS_DAY
        case = omd.STRATEGIES[0]
        full = omd.signal_layer(closes, ms, case, end_index=315)
        mutated = closes.copy()
        mutated[316:] *= [7., .4, 20., .01]          # post-window bytes cannot leak back
        changed = omd.signal_layer(mutated, ms, case, end_index=315)
        np.testing.assert_array_equal(full["weights"], changed["weights"])
        self.assertGreater(full["diag"]["decisions"], 0)
        self.assertEqual(full["diag"]["lo_5050_degenerate_decisions"], full["diag"]["decisions"])
        self.assertEqual(full["diag"]["decision_attempts"],
                         full["diag"]["decisions"] + full["diag"]["skipped_warmup"] +
                         full["diag"]["skipped_occupied_row_without_transitions"])
        self.assertTrue(omd.lo_optimum_is_exactly_5050(full))
        tr = np.random.default_rng(6).dirichlet(np.ones(case["bins"]), size=case["bins"])
        tv = np.random.default_rng(7).dirichlet(np.ones(case["bins"]), size=case["bins"])
        cached = omd.randomized_layer(full, case, (tr, tv))
        fresh = omd.signal_layer(closes, ms, case, end_index=315,
                                 random_transition=(tr, tv))
        np.testing.assert_array_equal(cached["weights"], fresh["weights"])
        np.testing.assert_array_equal(cached["ls_weights"], fresh["ls_weights"])

    def test_funding_adjustment_only_asof_official_and_no_future(self):
        close_ms = omd.utc_ms("2022-01-02") + omd.MS_DAY - 1
        times = np.array([close_ms - 20_000, close_ms + 1], dtype=np.int64)
        rates = np.array([-.0001, +.05])
        # only observations at or before the close are visible, and never >24h stale
        self.assertEqual(omd.score_adjustment(close_ms, times, rates), (1.0, True))
        self.assertEqual(omd.score_adjustment(close_ms - omd.MS_DAY - 1, times, rates), (0., False))
        self.assertEqual(omd.score_adjustment(close_ms + omd.MS_DAY + 20_001, times, rates), (0., False))

    def test_official_funding_only_and_exact_event_mark(self):
        panel, _, _, _, _ = miniature()
        with tempfile.TemporaryDirectory() as root:
            dest = Path(root) / "binance/usdm/funding/BNBUSDT"
            dest.mkdir(parents=True)
            rows = [{"funding_time_ms": omd.MS_DAY + 1000, "funding_rate": ".001",
                     "mark_price": "101", "truth_status": "modeled"},
                    {"funding_time_ms": 2 * omd.MS_DAY + 2000, "funding_rate": ".0001",
                     "mark_price": "102", "truth_status": "official"}]
            with gzip.open(dest / "BNBUSDT-funding.jsonl.gz", "wt") as stream:
                for row in rows:
                    stream.write(json.dumps(row) + "\n")
            events, times, rates, coverage = omd.load_funding("BNBUSDT", panel["open_ms"], root)
        self.assertEqual(coverage["modeled_ignored"], 1)   # modeled rows are counted, never charged
        self.assertEqual(coverage["official"], 1)
        self.assertEqual(sum(len(e) for e in events), coverage["official"])
        self.assertEqual(events[1], [])
        self.assertEqual(events[2][0][2], 102)             # official mark price, not bar close
        self.assertEqual(times.tolist(), [2 * omd.MS_DAY + 2000])
        self.assertEqual(rates.tolist(), [0.0001])

    def test_missing_funding_file_is_zero_cost_with_coverage_not_an_error(self):
        with tempfile.TemporaryDirectory() as root:
            events, times, rates, coverage = omd.load_funding("NOSUCH", np.arange(3), root)
        self.assertEqual(coverage["file_missing"], True)
        self.assertEqual(coverage["official"], 0)
        self.assertEqual(coverage["missing_funding_intervals_are_zero_not_modeled"], True)
        self.assertEqual(events, [[], [], []])
        self.assertEqual(times.size, 0)


class TestQlibCsvStage(unittest.TestCase):
    """raw archive -> dump_bin CSV. dump_bin + qlib.data.D (0.9.7) only exist inside
    the qlib-run container, so this host test pins this side of that boundary."""

    HOST_RAW = "/Volumes/ExpansionDrive/market-data-raw"

    @unittest.skipUnless(Path(HOST_RAW + "/binance/usdm/klines").is_dir(),
                         "canonical raw archive not mounted on this host")
    def test_registered_window_becomes_a_contiguous_finite_daily_csv(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.object(omd, "RAW_ROOT", self.HOST_RAW), \
                    patch.object(omd, "CSV_ROOT", root):
                summary = omd.write_qlib_csv("BNBUSDT", *omd.PHASES["full"])
                lines = (Path(root) / "BNBUSDT.csv").read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], "date," + ",".join(omd.FIELDS))
        self.assertEqual(len(lines) - 1, 1715)                    # 2022-01-01..2026-09-11
        self.assertEqual(summary["rows"], 1715)
        self.assertEqual(summary["last"], omd.utc_ms("2026-09-11"))
        self.assertEqual(len(summary["raw_files"]), 57)           # every month in window
        self.assertEqual(lines[1].split(",")[0], "2022-01-01")
        self.assertEqual(lines[-1].split(",")[0], "2026-09-11")
        for line in (lines[1], lines[900], lines[-1]):
            date, open_, high, low, close, volume = line.split(",")
            self.assertEqual(len(date), 10)
            self.assertTrue(all(float(x) > 0 for x in (open_, high, low, close, volume)))


class TestSignedExecution(unittest.TestCase):
    def run_book(self, signed_weight, events=None, panel=None, cost=None, dca=None, n=12):
        p, ev, layer, rail, instrument = miniature(n)
        if panel is not None:
            p.update(panel)
        if events is not None:
            ev = events
        if dca is not None:
            rail.update(dca)
        layer[0, 0] = signed_weight
        return omd.simulate(p, ev, layer, "BNBUSDT", rail, 0, len(p["open"]),
                            cost or {}, instrument)

    def test_one_tick_adverse_both_directions_and_fee_each_fill(self):
        for sign in (1, -1):
            m = self.run_book(sign * .5)
            entry = 100 + sign * .1
            exit_price = 100 - sign * .1
            qty = 5000 / entry
            gross = sign * qty * (exit_price - entry)
            fees = 5000 * .0005 + qty * exit_price * .0005
            self.assertEqual(m["episodes"], 1)
            self.assertEqual(m["fills"], 2)
            self.assertAlmostEqual(m["gross_pnl"], gross)
            self.assertAlmostEqual(m["fees"], fees)
            self.assertAlmostEqual(m["net_pnl"], gross - fees)
            self.assertAlmostEqual(m["ending_equity"], 30000 + m["net_pnl"])
            self.assertTrue(m["decomposition_ok"])
            self.assertLess(m["gross_pnl"], 0)   # one adverse tick is never a credit

    def test_official_mark_funding_positive_long_charge_short_credit(self):
        p, ev, _, _, _ = miniature()
        ev[2] = [(2 * omd.MS_DAY + 1000, .0001, 102.)]
        long = self.run_book(.5, events=ev)
        short = self.run_book(-.5, events=ev)
        self.assertAlmostEqual(long["funding"], (5000 / 100.1) * 102 * .0001)
        self.assertAlmostEqual(short["funding"], -(5000 / 99.9) * 102 * .0001)
        self.assertAlmostEqual(
            self.run_book(.5, events=ev, cost={"funding_mult": 2})["funding"],
            2 * long["funding"])
        self.assertEqual(self.run_book(.5, events=ev, cost={"no_funding": True})["funding"], 0)
        self.assertAlmostEqual(long["net_pnl"],
                               long["gross_pnl"] - long["fees"] - long["funding"])

    def test_ladder_fills_in_both_directions_with_a_fee_on_every_add(self):
        for sign in (1, -1):
            p, _, _, _, _ = miniature()
            bars = {"low": p["low"].copy(), "high": p["high"].copy()}
            bars["low" if sign > 0 else "high"][1] = 98.9 if sign > 0 else 101.1
            m = self.run_book(sign * .5, panel=bars)
            self.assertGreaterEqual(m["adds"], 1)
            self.assertEqual(m["fills"], m["adds"] + m["episodes"] * 2)
            self.assertEqual(m["layer_hist"][0], m["episodes"])
            self.assertTrue(m["decomposition_ok"])
            self.assertLessEqual(m["adds"], omd.MAX_ADD_LEVELS)

    def test_registered_12th_tranche_remains_a_reserve(self):
        """tranche #1 = entry, #2..#11 = active adds; #12 stays reserved."""
        n = 13
        p, _, _, _, _ = miniature(n)
        # Monotone adverse path: one ladder rung per bar, no TP (H=L<avg) and the
        # 10% invalidation stop stays below active rungs; the 12th
        # tranche could be reached by OHLC but is not a routine fill.
        path = np.array([100.1 * (1 - 0.01 * k) for k in range(n)])
        panel = {"open": np.full(n, 100.), "close": path, "high": path.copy(),
                 "low": path.copy()}
        dca = {"spacing_pct": .01, "size_multiplier": 1.0,
               "breakeven_tp_pct": .03, "invalidation_pct": .10}
        m = self.run_book(.5, panel=panel, dca=dca, n=n)
        self.assertEqual(m["adds"], omd.MAX_ADD_LEVELS)
        self.assertEqual(len(m["layer_hist"]), omd.LADDER_LEVELS)
        self.assertEqual(m["layer_hist"][-1], 0)  # reserve not deployed
        self.assertEqual(m["stop_hits"], 0)
        self.assertEqual(m["tp_hits"], 0)
        self.assertTrue(m["decomposition_ok"])

    def test_same_bar_adverse_add_precedes_tp_and_old_stop_survives_add(self):
        p, _, _, _, _ = miniature()
        low, high = p["low"].copy(), p["high"].copy()
        low[1], high[1] = 98.9, 102.5
        both = self.run_book(.5, panel={"low": low, "high": high})
        self.assertGreaterEqual(both["adds"], 1)
        self.assertEqual(both["tp_hits"], 1)
        low[1] = 94.8  # crosses the original resting stop and earlier add rungs
        stopped = self.run_book(.5, panel={"low": low, "high": high})
        self.assertGreaterEqual(stopped["adds"], 1)
        self.assertEqual(stopped["stop_hits"], 1)
        self.assertEqual(stopped["tp_hits"], 0)
        self.assertTrue(stopped["decomposition_ok"])

    def test_resting_stop_gapped_at_open_cannot_add_or_collect_later_funding(self):
        p, ev, _, _, _ = miniature()
        opened, low = p["open"].copy(), p["low"].copy()
        opened[2] = low[2] = 90.
        ev[2] = [(2 * omd.MS_DAY + 1000, .001, 90.)]
        stopped = self.run_book(.5, events=ev, panel={"open": opened, "low": low})
        self.assertEqual(stopped["stop_hits"], 1)
        self.assertEqual(stopped["adds"], 0)
        self.assertEqual(stopped["funding"], 0.)
        self.assertLess(stopped["gross_pnl"], -450.)

    def test_adverse_cost_stress_changes_realized_equity_and_gross_independent(self):
        baseline = self.run_book(.5)
        fee2 = self.run_book(.5, cost={"fee_mult": 2})
        slip2 = self.run_book(.5, cost={"slip_ticks": 2})
        self.assertAlmostEqual(fee2["gross_pnl"], baseline["gross_pnl"])
        self.assertAlmostEqual(fee2["fees"], baseline["fees"] * 2)
        self.assertLess(fee2["net_pnl"], baseline["net_pnl"])
        self.assertLess(slip2["net_pnl"], baseline["net_pnl"])
        self.assertLess(slip2["ending_equity"], baseline["ending_equity"])
        self.assertLess(self.run_book(.5, cost={"fee_override": .004})["net_pnl"],
                        baseline["net_pnl"])

    def test_gross_net_independence_has_negative_control(self):
        ok = self.run_book(.5)
        self.assertTrue(ok["decomposition_ok"])
        with patch.object(omd, "gross_price_pnl",
                          side_effect=lambda s, q, x, b: s * (q * x - b) + 1):
            bad = self.run_book(.5)
        self.assertAlmostEqual(bad["gross_pnl"] - ok["gross_pnl"], 1)
        self.assertAlmostEqual(bad["net_pnl"], ok["net_pnl"])
        self.assertFalse(bad["decomposition_ok"])

    def test_daily_equity_marks_net_fee_on_entry_before_exit(self):
        cost = self.run_book(.5, cost={"fee_mult": 2})
        free = self.run_book(.5, cost={"fee_override": 0})
        self.assertLess(cost["daily_equity"][1], free["daily_equity"][1])
        self.assertAlmostEqual(cost["daily_equity"][-1], cost["ending_equity"])

    def test_signal_is_next_bar_and_zero_weight_opens_no_position(self):
        p, ev, layer, dca, inst = miniature(4)
        layer[0, 0] = .5
        m = omd.simulate(p, ev, layer, "BNBUSDT", dca, 0, 4, {}, inst)
        self.assertEqual(m["daily_equity"][0], 30000.)   # close-of-signal bar is flat
        self.assertLess(m["daily_equity"][1], 30000.)
        shifted = omd.simulate(p, ev, layer, "BNBUSDT", dca, 0, 4,
                               {"entry_delay": 1}, inst)
        self.assertEqual(shifted["daily_equity"][1], 30000.)
        p2, ev2, layer2, dca2, inst2 = miniature(4)
        layer2[0, 0] = 0.0
        flat = omd.simulate(p2, ev2, layer2, "BNBUSDT", dca2, 0, 4, {}, inst2)
        self.assertEqual(flat["episodes"], 0)
        self.assertEqual(flat["net_pnl"], 0.0)


class TestGuardsSelectorAndFalsification(unittest.TestCase):
    def test_full_registered_multiplication_and_exact_output_list(self):
        self.assertEqual(len(omd.STRATEGIES), 8)          # M x lookback x gamma
        self.assertEqual(len(omd.DCA_GRID), 48)
        self.assertEqual(len(omd.GRIDS), 10)
        self.assertEqual(omd.expected_counts()["case_evaluations_total"], 15360)
        self.assertEqual(omd.expected_counts()["base_combinations_per_cohort"], 384)
        self.assertEqual(len(omd.ARTIFACTS), len(set(omd.ARTIFACTS)))
        self.assertIn("artifacts/family_falsification.json", omd.ARTIFACTS)
        self.assertIn("artifacts/funding_coverage.json", omd.ARTIFACTS)
        self.assertEqual(len(omd.FALSIFICATION["record_tests"]), 3)

    def test_run_spec_template_is_the_valid_spec(self):
        with tempfile.TemporaryDirectory() as root:
            with_config(root)
            with patch.object(omd, "RAW_ROOT", root):
                counts = omd.validate_spec(filled_spec(), ENGINE)
        self.assertEqual(counts["case_evaluations_total"], 15360)

    def test_spec_identity_coverage_and_lookahead_guard_fail_closed(self):
        with tempfile.TemporaryDirectory() as root:
            with_config(root)
            with patch.object(omd, "RAW_ROOT", root):
                good = filled_spec()
                mutations = [
                    lambda x: x["script"].update(sha256="sha256:bad"),
                    lambda x: x["engine"].update(self_check_sha256="sha256:bad"),
                    lambda x: x["expected"].update(case_evaluations_per_grid=1535),
                    lambda x: x["expected_outputs"].remove("artifacts/funding_coverage.json"),
                    lambda x: x["dca_domain"]["grid"].pop(),
                    lambda x: x["params"][0].update(bins=4),
                    lambda x: x["falsification"]["record_tests"].pop("cost_stress_turnover"),
                    lambda x: x["falsification"]["record_tests"]["cost_stress_turnover"
                              ].update(min_sharpe_at_15bps=0.1),
                    lambda x: x["gates"].update(min_episodes_is=1),
                    lambda x: x["signal_constants"].update(k=2),
                    lambda x: x["grids"].pop(),
                    lambda x: x["split"].update(oos_end="2026-09-12"),
                    lambda x: x["data"]["timeframes"][0].update(qlib_freq="week"),
                    lambda x: x["data"].update(symbols=["BTCUSDT"]),
                    lambda x: x.update(task_id="t_fake"),
                    lambda x: x.update(kanban_board="quant-strategy-research"),
                    lambda x: x.update(run_id=omd.FAMILY_ID + "-r1-ufoo"),
                ]
                for mutate in mutations:
                    candidate = copy.deepcopy(good)
                    mutate(candidate)
                    with self.assertRaises((ValueError, KeyError), msg=str(mutate)):
                        omd.validate_spec(candidate, ENGINE)

    def test_historical_only_selector_refuses_oos_rows_and_neighbours(self):
        row = {"symbol": "BNBUSDT", "timeframe": "1d", "grid": "oos", "episodes": 12,
               "net_pnl": 10., "sharpe": 1., **omd.STRATEGIES[0], **omd.DCA_GRID[0]}
        with self.assertRaises(ValueError):
            omd.select_cohort({"historical": [row]})
        with self.assertRaises(ValueError):
            omd.neighbourhood(row, [row])

    def test_selector_winner_cannot_move_when_oos_is_mutated(self):
        template = {"symbol": "BNBUSDT", "timeframe": "1d", **omd.STRATEGIES[0],
                    **omd.DCA_GRID[0], "net_pnl": 100., "sharpe": 2., "episodes": 20,
                    "gross_pnl": 130., "fees": 20., "funding": 10.,
                    "ending_equity": 30100., "fills": 40, "adds": 0,
                    "turnover_usdt": 1000., "max_dd_pct": .1, "max_dd_usdt": 10.,
                    "annualized_return": .01, "max_effective_leverage": 1.,
                    "capital_utilization": .1, "tp_hits": 1, "stop_hits": 0,
                    "margin_calls": 0, "rebalance_exits": 1, "open_at_end": 0}
        axes = {**omd.STRATEGY_AXES, **omd.DCA_AXES}
        neighbours = []
        for name, domain in axes.items():
            index = domain.index(template[name])
            if index + 1 < len(domain):
                row = dict(template)
                row[name] = domain[index + 1]
                row["sharpe"] = 1.0
                neighbours.append(row)
        rows = {g: [{**r, "grid": g} for r in [template, *neighbours]] for g in omd.GRIDS}
        winner, detail = omd.select_cohort(rows)
        self.assertEqual(winner["sharpe"], 2.)
        self.assertEqual(winner["grid"], "historical")
        self.assertTrue(detail["neighbourhood"]["passed"])
        altered = copy.deepcopy(rows)
        for row in altered["oos"]:
            row["sharpe"] = 500
        for row in altered["full"]:
            row["net_pnl"] = -999
        chosen, altered_detail = omd.select_cohort(altered)
        # Selection (the historical ranking) is OOS-blind: same cell wins either way...
        self.assertEqual(altered_detail["winner"], detail["winner"])
        # ...while OOS/full still decide survival, and a negative full grid culls it.
        self.assertIsNone(chosen)
        self.assertIn("full_economic", altered_detail["cull_reasons"])

    def test_shuffling_battery_fails_when_true_equals_random(self):
        """A null that ties with the true strategy must be reported FAIL, not PASS."""
        n = 1715
        p, ev, _, dca, instrument = miniature(n)
        ms = omd.utc_ms("2022-01-01") + np.arange(n) * omd.MS_DAY
        p["open_ms"] = ms
        rng = np.random.default_rng(3)
        close = 100 * np.exp(np.cumsum(rng.normal(0, .01, (n, 4)), axis=0))
        case = omd.STRATEGIES[0]
        base = omd.signal_layer(close, ms, case)
        self.assertGreater(base["diag"]["decisions"], 50)
        with patch.object(omd, "randomized_layer", return_value=base):
            actual = omd.falsification(base, "BNBUSDT", case, dca,
                                       *omd.window_indices(ms, *omd.PHASES["oos"]),
                                       {s: p for s in omd.SYMBOLS},
                                       {s: ev for s in omd.SYMBOLS},
                                       {s: instrument for s in omd.SYMBOLS},
                                       shuffled=9)
        self.assertEqual(actual["markov_shuffle"]["status"], "FAIL")
        self.assertEqual(actual["markov_shuffle"]["p_one_sided_margin_0_30"], 1.0)
        self.assertEqual(set(actual["cost_turnover"]["oos_fee_bps"]), {"5", "15", "30", "50"})
        for name, key in (("synthetic_markov_shuffling", "markov_shuffle"),
                          ("cost_stress_turnover", "cost_turnover"),
                          ("cross_sectional_subperiod_walkforward", "subperiod_walkforward")):
            self.assertEqual(actual[key]["failure_rule"],
                             omd.FALSIFICATION["record_tests"][name]["failure_rule"])
        self.assertEqual(actual["markov_shuffle"]["null_definition"],
                         omd.FALSIFICATION["record_tests"]["synthetic_markov_shuffling"]["null"])
        self.assertIn(actual["subperiod_walkforward"]["status"],
                      ("FAIL", "PASS", "INDETERMINATE"))
        self.assertIn("all four LS legs", actual["subperiod_walkforward"]["scope"])

    def test_ls_walkforward_aggregates_both_sides_not_one_cohort(self):
        n = 1715  # entire registered window; no shorter synthetic window may be extrapolated
        ms = omd.utc_ms("2022-01-01") + np.arange(n) * omd.MS_DAY
        p, events, layer, dca, instrument = miniature(n)
        p["open_ms"] = ms
        layer[::7, 0] = .5
        layer[::7, 3] = -.5
        result = omd.cross_sectional_walkforward(
            {s: p for s in omd.SYMBOLS}, {s: events for s in omd.SYMBOLS},
            {"ls_weights": layer}, dca, {s: instrument for s in omd.SYMBOLS})
        self.assertTrue(result["windows"])
        self.assertTrue(any(m["eligible"] for m in result["windows"]))
        self.assertTrue(all(m["ls_net_alpha_vs_cash_usdt"] < 0 for m in result["windows"]))


class TestEndToEndDryRun(unittest.TestCase):
    """run() orchestration outside /results: spec gate -> panels -> grids -> selector
    -> falsification wiring -> assertions -> every registered artifact. The Qlib build
    and the 999-draw battery are replaced by stubs here; both are covered separately."""

    @staticmethod
    def panel(n=1715):   # full registered window, so every phase window is nonempty
        ms = omd.utc_ms("2022-01-01") + np.arange(n) * omd.MS_DAY
        rng = np.random.default_rng(7)
        out = {}
        for i, sym in enumerate(omd.SYMBOLS):
            close = 100 * np.exp(np.cumsum(rng.normal(0.0015 - 0.001 * i, .006, n)))
            opened = close * (1 + rng.normal(0, .002, n))
            out[sym] = {"open_ms": ms, "open": opened,
                        "high": np.maximum(opened, close) * 1.004,
                        "low": np.minimum(opened, close) * .996,
                        "close": close, "volume": np.full(n, 1e6)}
        return out

    @staticmethod
    def fake_build(start, end):
        return TestEndToEndDryRun.panel(), {
            "qlib_version": "0.9.7", "read_path": "qlib.data.D.features",
            "source": "/data/raw", "qlib_rows_identical_to_raw": True,
            "datasets": {s: {"rows": 1715} for s in omd.SYMBOLS}}

    @staticmethod
    def fake_falsification(layer, symbol, case, dca, o0, o1, panels, all_events,
                           instruments, shuffled=omd.SHUFFLES):
        # Exact production signature: a drifted call site fails here instead of passing.
        assert 0 <= o0 < o1 <= len(panels[symbol]["open_ms"]), "battery window must be in-panel"
        assert set(panels) == set(all_events) == set(instruments) == set(omd.SYMBOLS)
        reg = omd.FALSIFICATION["record_tests"]
        return {"markov_shuffle": {"status": "PASS", "p_one_sided_margin_0_30": .001,
                                   "failure_rule": reg["synthetic_markov_shuffling"]["failure_rule"]},
                "cost_turnover": {"status": "FAIL", "oos_fee_bps": {"15": {"sharpe": 0.0}},
                                  "failure_rule": reg["cost_stress_turnover"]["failure_rule"]},
                "subperiod_walkforward": {"status": "INDETERMINATE", "negative_fraction": None,
                                          "failure_rule": reg["cross_sectional_subperiod_walkforward"]["failure_rule"]}}

    def test_registered_artifacts_coverage_and_identity(self):
        strat_axes = {"bins": (5, 10), "lookback_days": (7,), "gamma": (.3,)}
        dca_axes = {"spacing_pct": (.01, .04), "size_multiplier": (1., 1.1),
                    "breakeven_tp_pct": (.01, .03), "invalidation_pct": (.05, .10)}
        strategies = tuple(dict(zip(strat_axes, v)) for v in itertools.product(*strat_axes.values()))
        dca_grid = tuple(dict(zip(dca_axes, v)) for v in itertools.product(*dca_axes.values()))
        cells_per_grid = len(omd.SYMBOLS) * len(strategies) * len(dca_grid)
        with tempfile.TemporaryDirectory() as root:
            results_root = Path(root) / "results"
            attempt = (results_root / omd.FAMILY_ID / "rounds" / (omd.FAMILY_ID + "-r1") /
                       "attempts" / (omd.FAMILY_ID + "-r1-u1"))
            instruments = {s: {"tick": .1, "taker_fee": .0005, "source": "test",
                               "source_sha256": "sha256:test"} for s in omd.SYMBOLS}
            with_config(str(Path(root) / "raw"))   # canonical catalog the spec gate reads
            with patch.object(omd, "RESULTS_ROOT", str(results_root)), \
                    patch.object(omd, "RAW_ROOT", str(Path(root) / "raw")), \
                    patch.object(omd, "STRATEGY_AXES", strat_axes), \
                    patch.object(omd, "STRATEGIES", strategies), \
                    patch.object(omd, "DCA_AXES", dca_axes), patch.object(omd, "DCA_GRID", dca_grid), \
                    patch.object(omd, "instrument_metadata", return_value=instruments), \
                    patch.object(omd, "build_qlib", side_effect=self.fake_build), \
                    patch.object(omd, "falsification", side_effect=self.fake_falsification):
                before = {str(q) for q in Path(root).rglob("*") if q.is_file()}
                spec = filled_spec()
                counts = omd.validate_spec(spec, ENGINE)
                result = omd.run(spec, str(attempt))
                after = {str(q) for q in Path(root).rglob("*") if q.is_file()}
            self.assertEqual(counts["case_evaluations_total"], cells_per_grid * len(omd.GRIDS))
            self.assertEqual(result["case_evaluations_total"], counts["case_evaluations_total"])
            self.assertTrue(result["coverage_complete"])
            self.assertTrue(all(result["coverage_by_grid"].values()))
            self.assertEqual(result["assertion_failures"],
                             [k for k in result["assertion_failures"] if k in
                              {"cost_stress_effective", "funding_2x_effective"}])
            self.assertEqual(result["script_sha256"], omd.sha256_file(ENGINE))
            self.assertEqual(json.loads((attempt / "state.json").read_text())["stage"], "ARTIFACT_READY")
            for rel in omd.ARTIFACTS:  # every registered artifact really exists
                self.assertTrue((attempt / rel).is_file(), "missing " + rel)
            self.assertTrue((attempt / "logs/run.log").read_text().strip())
            records = json.loads((attempt / "artifacts/cohort_results.json").read_text())
            self.assertEqual(len(records), len(omd.SYMBOLS))
            self.assertEqual(len(result["falsification_cohorts_evaluated"]),
                             sum(1 for r in records if "winner" in r))
            for grid in omd.GRIDS:
                with open(attempt / "artifacts" / ("grid_%s.csv" % grid), newline="") as stream:
                    self.assertEqual(len(list(csv.DictReader(stream))), cells_per_grid)
            strays = sorted(q for q in after - before
                            if not q.startswith(str(attempt)))
            self.assertEqual(strays, [], "run() must write only inside its attempt dir")


if __name__ == "__main__":
    unittest.main()
