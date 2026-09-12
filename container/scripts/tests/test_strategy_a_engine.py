#!/usr/bin/env python3
"""Executable check for the Strategy A execution engine (DCA ladder accounting).

Runs inside the qlib container:  /opt/venv/bin/python /scripts/tests/test_strategy_a_engine.py
Drives `simulate()` with crafted bars whose fills are known by hand, so a regression in the
ladder walk / resting invalidation / TP / fee accounting fails loudly instead of silently
changing the science.  stdlib unittest only; no market data, no container state.
"""
import importlib.util
import json
import os
import unittest

import numpy as np

ENGINE = os.environ.get("SA_ENGINE_PATH", "/scripts/20_strategy_a_run.py")
_spec = importlib.util.spec_from_file_location("sa_engine", ENGINE)
sa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sa)

RAIL = {"base_quote": 1000.0, "size_multiplier": 1.1, "spacing_d0": 0.02,
        "tp": 0.012, "invalidation": 0.05}
PARAMS = {"window": 5, "discount": 0.01}
LEV = 10.0
P0 = 98.5                    # the entry close produced by the crafted crossing
FLAT = [(100.0, 100.0, 100.0, 100.0)] * 8   # SMA(5) warm-up, no crossing
ENTRY = (100.0, 100.0, 98.5, 98.5)          # bar 8: fresh oversold crossing -> entry at 98.5


class FakeCohort:
    def __init__(self, rows, tick=0.0, taf=0.0, funding=None, margin_maint=0.1,
                 symbol="SYNTH", timeframe="5m"):
        self.symbol = symbol
        self.timeframe = timeframe
        self.open = np.array([r[0] for r in rows], dtype=np.float64)
        self.high = np.array([r[1] for r in rows], dtype=np.float64)
        self.low = np.array([r[2] for r in rows], dtype=np.float64)
        self.close = np.array([r[3] for r in rows], dtype=np.float64)
        self.n = len(rows)
        self.open_time_ms = np.arange(self.n, dtype=np.int64) * 300000
        self.day_index = np.zeros(self.n, dtype=np.int64)
        self.n_days = 1
        self.day_end = {self.n - 1}
        self.funding = np.zeros(self.n) if funding is None else np.array(funding, dtype=np.float64)
        self.price_increment = tick
        self.taker_fee = taf
        self.leverage = LEV
        self.margin_maint = margin_maint


def run(cohort, slip=0.0, kind="historical"):
    return sa.simulate(cohort, (0, cohort.n), RAIL, PARAMS, {}, slip, kind)


def run_rail(cohort, rail):
    return sa.simulate(cohort, (0, cohort.n), rail, PARAMS, {}, 0.0, "historical")


def mk(tail, **kw):
    return FakeCohort(FLAT + [ENTRY] + tail, **kw)


def walk_expect(low, open_=P0):
    """Independent replay of the descending-price trigger walk (shallow -> deep)."""
    qty = 10000.0 / P0
    cost = 10000.0
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
        amt = RAIL["base_quote"] * (RAIL["size_multiplier"] ** k) * LEV
        qty += amt / trig
        cost += amt
        fills = k
        k += 1


class TestEngine(unittest.TestCase):

    def test_entry_then_take_profit(self):
        m = run(mk([(98.5, 100.0, 98.4, 99.5)]))
        self.assertEqual(m["episodes"], 1)
        self.assertEqual(m["tp_hits"], 1)
        self.assertEqual(m["stop_hits"], 0)
        self.assertEqual(m["layers"][0], 1)
        self.assertEqual(sum(m["layers"][1:]), 0)
        # 10x on a 1000 USDT margin = 10,000 notional; TP = +1.2% of the cost basis
        self.assertAlmostEqual(m["net_pnl"], 120.0, places=6)
        self.assertAlmostEqual(m["ending_equity"], 30120.0, places=6)

    def test_ladder_walk_then_resting_stop(self):
        # bar 9 trades down to 85: the ladder fills levels 1..4, then the resting invalidation
        # (average cost x 0.95) is reached before level 5
        fills, kill = walk_expect(85.0, 98.5)
        self.assertEqual(fills, 4)
        self.assertIsNotNone(kill)
        m = run(mk([(98.5, 93.0, 85.0, 86.0)]))
        self.assertEqual(m["episodes"], 1)
        self.assertEqual(m["stop_hits"], 1)
        self.assertEqual(m["tp_hits"], 0)
        self.assertEqual(m["layers"][:6], [1, 1, 1, 1, 1, 0])
        self.assertEqual(sum(m["layers"][6:]), 0)
        # hand-checked: only levels 0..4 can be deployed (fixed notional per level)
        cost = sum(1000.0 * 1.1 ** k * LEV for k in range(5))
        self.assertAlmostEqual(cost, 61051.0, places=6)
        qty = sum((1000.0 * 1.1 ** k * LEV) / (P0 * (1 - 0.02 * k)) for k in range(5))
        self.assertAlmostEqual(m["net_pnl"], qty * kill - cost, places=6)
        self.assertLess(m["net_pnl"], 0.0)

    def test_a_level_below_the_resting_stop_can_never_fill(self):
        # spacing 2% with a 5% invalidation means at most a handful of levels are reachable
        m = run(mk([(98.5, 93.0, 85.0, 86.0)]))
        self.assertLessEqual(sum(1 for x in m["layers"][1:] if x), 4)
        self.assertEqual(m["layers"][10], 0)
        self.assertEqual(m["layers"][11], 0)

    def test_gap_through_the_stop_fills_at_the_open(self):
        # a bar that OPENS below the resting stop cannot fill at the stop price
        fills, kill = walk_expect(79.0, open_=80.0)
        m = run(mk([(80.0, 81.0, 79.0, 80.0)]))
        self.assertEqual(m["stop_hits"], 1)
        self.assertEqual(kill, 80.0)
        cost = sum(1000.0 * 1.1 ** k * LEV for k in range(fills + 1))
        qty = sum((1000.0 * 1.1 ** k * LEV) / (P0 * (1 - 0.02 * k)) for k in range(fills + 1))
        self.assertAlmostEqual(m["net_pnl"], qty * 80.0 - cost, places=6)

    def test_take_profit_uses_the_same_bar_adds(self):
        # bar 9 low 96.0 fills level 1 (96.53); its high then takes profit on the larger book
        m = run(mk([(98.5, 100.0, 96.0, 99.5)]))
        self.assertEqual(m["layers"][0], 1)
        self.assertEqual(m["layers"][1], 1)
        self.assertEqual(m["layers"][2], 0)
        self.assertEqual(m["tp_hits"], 1)
        self.assertAlmostEqual(m["net_pnl"], 0.012 * 21000.0, places=6)

    def test_no_add_after_a_kill(self):
        m = run(mk([(98.5, 93.0, 85.0, 86.0), (86.0, 86.0, 80.0, 81.0), (81.0, 81.0, 70.0, 71.0)]))
        self.assertEqual(m["episodes"], 1)
        self.assertEqual(m["layers"][:6], [1, 1, 1, 1, 1, 0])
        self.assertEqual(sum(m["layers"][6:]), 0)

    def test_costs_and_funding_are_not_estimated(self):
        fund = [0.0] * 9 + [0.001]
        m = run(mk([(98.5, 100.0, 98.4, 99.5)], tick=0.1, taf=0.0005, funding=fund), slip=1.0)
        self.assertGreater(m["fees"], 0.0)
        self.assertGreater(m["funding"], 0.0)
        self.assertAlmostEqual(m["gross_pnl"] - m["fees"] - m["funding"], m["net_pnl"], places=6)
        self.assertEqual(m["episodes"], m["tp_hits"] + m["stop_hits"]
                         + m["open_at_end"] + m["margin_calls"])
        self.assertLess(m["net_pnl"], 120.0)

    def test_entry_is_edge_triggered(self):
        c = [100.0 - 0.5 * i for i in range(20)]
        rows = [(x, x + 0.05, x - 0.05, x) for x in c]
        m = run(FakeCohort(rows))
        self.assertEqual(m["episodes"], 1)
        self.assertEqual(m["layers"][0], 1)
        self.assertEqual(m["stop_hits"] + m["open_at_end"] + m["margin_calls"], 1)

    def test_episode_still_open_at_window_end(self):
        # neither the TP (99.682) nor the invalidation (93.575) is reached before the window ends
        m = run(mk([(98.5, 98.45, 98.30, 98.4)]))
        self.assertEqual(m["episodes"], 1)
        self.assertEqual(m["open_at_end"], 1)
        self.assertEqual(m["stop_hits"], 0)
        self.assertEqual(m["tp_hits"], 0)
        self.assertLess(m["net_pnl"], 0.0)

    def test_capital_exhaustion_backstop_is_reachable(self):
        # with a pathological maintenance ratio the backstop must fire and cut the episode
        m = run(mk([(98.5, 100.0, 98.4, 99.5)], margin_maint=5.0))
        self.assertEqual(m["episodes"], 1)
        self.assertEqual(m["margin_calls"], 1)
        self.assertEqual(m["tp_hits"], 0)

    def test_backstop_is_unreachable_in_the_calibrated_configuration(self):
        # 10x margin_init with margin_maint 0.1 leaves the resting invalidation in front of it
        for low in (90.0, 85.0, 80.0, 60.0, 20.0):
            m = run(mk([(98.5, 99.0, low, low + 1.0)]))
            self.assertEqual(m["margin_calls"], 0, "low=%s" % low)

    def test_no_new_episode_once_the_account_is_exhausted(self):
        # a rail whose ladder is far too large for the account: one kill wipes it, and the
        # later fresh crossing must be refused instead of trading an empty account
        big = dict(RAIL)
        big["base_quote"] = 24000.0
        rows = FLAT + [ENTRY,
                       (98.5, 93.0, 85.0, 86.0)] + [(120.0, 120.0, 120.0, 120.0)] * 6 + \
                       [(120.0, 120.0, 100.0, 100.0)]
        m = run_rail(FakeCohort(rows), big)
        self.assertEqual(m["episodes"], 1)
        self.assertTrue(m["halted"])
        self.assertLess(m["ending_equity"], 0.0)
        self.assertEqual(m["min_entry_equity"], 30000.0)  # the refused entry never happened
        self.assertEqual(m["layers"][0], 1)

    def test_json_payload_is_strictly_valid(self):
        # NaN / Infinity must never reach a durable artifact: jq and other strict parsers
        # reject those bare tokens, which would make result.json unreadable for an auditor
        out = sa._sanitize({"a": float("nan"), "b": float("inf"), "c": np.bool_(True),
                            "d": np.int64(3), "e": [float("-inf")], "f": np.float32(1.5)})
        self.assertIsNone(out["a"])
        self.assertIsNone(out["b"])
        self.assertIs(out["c"], True)
        self.assertEqual(out["d"], 3)
        self.assertEqual(out["e"], [None])
        self.assertAlmostEqual(out["f"], 1.5, places=5)
        json.dumps(out, allow_nan=False)

    def test_grid_runs_are_deterministic(self):
        a = run(mk([(98.5, 93.0, 85.0, 86.0)]))
        b = run(mk([(98.5, 93.0, 85.0, 86.0)]))
        self.assertEqual(a["net_pnl"], b["net_pnl"])
        self.assertEqual(a["layers"], b["layers"])


# ---------------------------------------------------------------------------
# v1.3.0: cohort selector / cohort survivor / disposition (contract 7.2 / 7.3)
# ---------------------------------------------------------------------------

def mini_spec(windows=(20, 50), discounts=(0.01, 0.02), spacing=(0.01, 0.02, 0.03),
              mult=(1.0,), tp=(0.01,), inval=(0.05,), min_episodes_is=20, neighbourhood=0.6):
    """A small but structurally complete v1.3.0 run-spec for the pure-logic checks."""
    return {
        "family_id": "synthetic", "round_id": "synthetic-r1", "run_id": "synthetic-r1-u1",
        "selector_version": sa.SELECTOR_VERSION, "disposition_version": sa.DISPOSITION_VERSION,
        "params": {"grid_windows": list(windows), "grid_discounts": list(discounts),
                   "grid": [{"window": w, "discount": d} for w in windows for d in discounts]},
        "dca_domain": {"base_quote": 1000, "spacing_pct": list(spacing),
                       "size_multiplier": list(mult), "breakeven_tp_pct": list(tp),
                       "invalidation_pct": list(inval),
                       "grid": [{"spacing_pct": s, "size_multiplier": m,
                                 "breakeven_tp_pct": t, "invalidation_pct": i, "base_quote": 1000}
                                for s in spacing for m in mult for t in tp for i in inval]},
        "gates": {"min_episodes_is": min_episodes_is,
                  "neighborhood_min_same_sign_fraction": neighbourhood},
    }


def build_cohort_rows(spec, label, value_fn):
    """Every cell of every registered phase grid for one cohort, with values from value_fn."""
    axes = sa.axis_values(spec)
    symbol, timeframe = label.split("/")
    out = {}
    for kind in sa.COHORT_GRID_KINDS:
        rows = []
        for w in axes["window"]:
            for d in axes["discount"]:
                for s in axes["spacing_pct"]:
                    for m in axes["size_multiplier"]:
                        for t in axes["breakeven_tp_pct"]:
                            for i in axes["invalidation_pct"]:
                                row = {"symbol": symbol, "timeframe": timeframe, "window_kind": kind,
                                       "window": w, "discount": d, "spacing_pct": s,
                                       "size_multiplier": m, "breakeven_tp_pct": t,
                                       "invalidation_pct": i}
                                pnl, sharpe, episodes = value_fn(kind, row)
                                row.update({"net_pnl": pnl, "sharpe": sharpe, "episodes": episodes,
                                            "ending_equity": 30000.0, "fees": 1.0, "funding": 0.0,
                                            "max_dd_usdt": -1.0, "max_dd_pct": -0.01,
                                            "max_effective_leverage": 1.0,
                                            "capital_utilization": 0.1})
                                rows.append(row)
        out[kind] = rows
    return out


def all_positive(kind, row):
    return 1.0, 0.5, 30


class TestV13CohortGate(unittest.TestCase):

    def test_rail_for_maps_the_four_dca_axes(self):
        dca = {"spacing_pct": 0.03, "size_multiplier": 1.0, "breakeven_tp_pct": 0.02,
               "invalidation_pct": 0.1, "base_quote": 1000}
        self.assertEqual(sa.rail_for(dca),
                         {"base_quote": 1000, "spacing_d0": 0.03, "size_multiplier": 1.0,
                          "tp": 0.02, "invalidation": 0.1})

    def test_dca_axis_reaches_the_engine(self):
        # a wide spacing keeps the ladder out of reach; a tight spacing fills it -- the DCA
        # axis is a real engine input, not a label on the row.
        wide = run_rail(mk([(98.5, 93.0, 85.0, 86.0)]),
                        sa.rail_for({"spacing_pct": 0.20, "size_multiplier": 1.0,
                                     "breakeven_tp_pct": 0.01, "invalidation_pct": 0.5,
                                     "base_quote": 1000}))
        tight = run_rail(mk([(98.5, 93.0, 85.0, 86.0)]),
                         sa.rail_for({"spacing_pct": 0.01, "size_multiplier": 1.0,
                                      "breakeven_tp_pct": 0.01, "invalidation_pct": 0.5,
                                      "base_quote": 1000}))
        self.assertEqual(sum(wide["layers"][1:]), 0)
        self.assertGreater(sum(tight["layers"][1:]), 0)

    def test_selector_refuses_non_historical_rows(self):
        spec = mini_spec()
        rows = build_cohort_rows(spec, "BTCUSDT/5m", all_positive)
        with self.assertRaises(ValueError):
            sa.select_cohort_winner(rows["oos"], spec)
        with self.assertRaises(ValueError):
            sa.cohort_neighbourhood(rows["full"], rows["full"][0], spec)

    def test_selector_ranking_net_pnl_then_lexical_tie_break(self):
        spec = mini_spec()
        rows = build_cohort_rows(spec, "BTCUSDT/5m",
                                 lambda kind, row: (1.0, 0.5, 30) if kind == "historical"
                                 else (1.0, 0.5, 30))
        # equal sharpe/pnl everywhere -> the registered-index lexical order decides:
        # window index 0, discount index 0, spacing index 0, ... = the first registered cell
        winner, reason = sa.select_cohort_winner(rows["historical"], spec)
        self.assertEqual(reason, "selected")
        self.assertEqual(sa.tie_break_key(winner, sa.axis_values(spec)), (0, 0, 0, 0, 0, 0))
        # a strictly better Sharpe wins regardless of lexical order
        boosted = [dict(r) for r in rows["historical"]]
        boosted[5]["sharpe"] = 9.0
        self.assertEqual(sa.cell_key(sa.select_cohort_winner(boosted, spec)[0]),
                         sa.cell_key(boosted[5]))
        # equal Sharpe -> the higher net_pnl wins; equal both -> lexical again
        tied = [dict(r) for r in rows["historical"]]
        tied[5]["sharpe"] = 9.0
        tied[7]["sharpe"] = 9.0
        tied[7]["net_pnl"] = 2.0
        self.assertEqual(sa.cell_key(sa.select_cohort_winner(tied, spec)[0]), sa.cell_key(tied[7]))

    def test_selector_determinism_under_shuffle(self):
        spec = mini_spec()
        rows = build_cohort_rows(spec, "SOLUSDT/4h", all_positive)
        first, _ = sa.select_cohort_winner(rows["historical"], spec)
        shuffled = list(rows["historical"])
        shuffled.reverse()
        again, _ = sa.select_cohort_winner(shuffled, spec)
        self.assertEqual(sa.cell_key(first), sa.cell_key(again))

    def test_selector_cull_reasons(self):
        spec = mini_spec()
        thin = build_cohort_rows(spec, "BTCUSDT/5m", lambda k, r: (1.0, 0.5, 3))
        self.assertEqual(sa.select_cohort_winner(thin["historical"], spec), (None, "insufficient_trades"))
        dark = build_cohort_rows(spec, "BTCUSDT/5m", lambda k, r: (-1.0, -0.5, 30))
        self.assertEqual(sa.select_cohort_winner(dark["historical"], spec),
                         (None, "no_qualifying_candidate"))

    def test_neighbourhood_is_face_adjacent_and_needs_60_percent(self):
        spec = mini_spec()
        rows = build_cohort_rows(spec, "BTCUSDT/5m", all_positive)
        winner, _ = sa.select_cohort_winner(rows["historical"], spec)
        idx = {sa.cell_key(r): r for r in rows["historical"]}
        nb = sa.cohort_neighbourhood(rows["historical"], winner, spec)
        # the winner is (20, 0.01, 0.01, 1.0, 0.01, 0.05): one legal neighbour per axis
        self.assertEqual(nb["neighbours"], 3)
        self.assertTrue(nb["passed"])
        # all three neighbours disagreeing = 0.0 < 0.6 -> the cohort fails the neighbourhood gate
        axes = sa.axis_values(spec)
        for axis in sa.AXES:
            vals = axes[axis]
            pos = vals.index(sa.cell_key(winner)[sa.AXES.index(axis)])
            npos = pos + 1
            if npos < len(vals):
                key = list(sa.cell_key(winner))
                key[sa.AXES.index(axis)] = vals[npos]
                idx[tuple(key)]["net_pnl"] = -1.0
        nb2 = sa.cohort_neighbourhood(rows["historical"], winner, spec)
        self.assertEqual(nb2["agreeing"], 0)
        self.assertFalse(nb2["passed"])

    def test_cohort_survivor_requires_all_five_requirements(self):
        spec = mini_spec()
        label = "ETHUSDT/1h"
        winner_fn = all_positive
        base = build_cohort_rows(spec, label, winner_fn)
        res = sa.evaluate_cohort(spec, label, base)
        self.assertEqual(res["outcome"], "SURVIVOR")
        self.assertEqual(res["cull_reasons"], [])
        self.assertIsNotNone(res["winner"])
        self.assertEqual(res["metrics"]["oos"]["net_pnl"], 1.0)
        self.assertEqual(res["metrics"]["full"]["net_pnl"], 1.0)
        self.assertEqual(sorted(res["metrics"]["robustness"]),
                         sorted([s for s, _ in sa.STRESS]))

        winner_key = sa.cell_key(sa.select_cohort_winner(base["historical"], spec)[0])

        def failing(kind_fail, value=-1.0):
            def fn(kind, row):
                pnl, sharpe, episodes = 1.0, 0.5, 30
                if kind == kind_fail and sa.cell_key(row) == winner_key:
                    return value, sharpe, episodes
                return pnl, sharpe, episodes
            return fn

        for kind_fail, expected in (("oos", "oos_economic"), ("full", "full_economic"),
                                    ("fee_2x", "robustness_economic:fee_2x"),
                                    ("slippage_2ticks", "robustness_economic:slippage_2ticks")):
            rows = build_cohort_rows(spec, label, failing(kind_fail))
            out = sa.evaluate_cohort(spec, label, rows)
            self.assertEqual(out["outcome"], "CULLED", kind_fail)
            self.assertIn(expected, out["cull_reasons"], kind_fail)

        def neighbourhood_fail(flipped):
            def fn(kind, row):
                if kind != "historical":
                    return 1.0, 0.5, 30
                if sa.cell_key(row) == winner_key or sa.cell_key(row) not in flipped:
                    return 1.0, 0.5, 30
                return -1.0, -0.5, 30
            return fn

        axes = sa.axis_values(spec)
        neighbour_keys = []
        for ai, axis in enumerate(sa.AXES):
            vals = axes[axis]
            pos = vals.index(winner_key[ai])
            for step in (-1, 1):
                npos = pos + step
                if 0 <= npos < len(vals):
                    key = list(winner_key)
                    key[ai] = vals[npos]
                    neighbour_keys.append(tuple(key))
        self.assertEqual(len(neighbour_keys), 3)

        # one of three neighbours disagreeing = 0.667 >= 0.60 -> still a survivor
        one = sa.evaluate_cohort(spec, label,
                                 build_cohort_rows(spec, label, neighbourhood_fail(neighbour_keys[:1])))
        self.assertEqual(one["outcome"], "SURVIVOR")
        self.assertAlmostEqual(one["neighbourhood"]["same_sign_fraction"], round(2.0 / 3.0, 6), places=6)
        # all three disagreeing = 0.0 < 0.60 -> parameter_neighbourhood cull
        none_ = sa.evaluate_cohort(spec, label,
                                   build_cohort_rows(spec, label, neighbourhood_fail(neighbour_keys)))
        self.assertEqual(none_["outcome"], "CULLED")
        self.assertIn("parameter_neighbourhood", none_["cull_reasons"])

    def test_insufficient_trades_culls_only_that_cohort(self):
        spec = mini_spec(min_episodes_is=20)
        good = build_cohort_rows(spec, "BTCUSDT/5m", all_positive)
        thin = build_cohort_rows(spec, "SOLUSDT/4h", lambda k, r: (5.0, 2.0, 4))
        self.assertEqual(sa.evaluate_cohort(spec, "BTCUSDT/5m", good)["outcome"], "SURVIVOR")
        res = sa.evaluate_cohort(spec, "SOLUSDT/4h", thin)
        self.assertEqual(res["outcome"], "CULLED")
        self.assertEqual(res["cull_reasons"], ["insufficient_trades"])
        self.assertIsNone(res["winner"])

    def test_family_disposition_bands(self):
        surv = {"cohort": "x", "outcome": "SURVIVOR"}
        self.assertEqual(sa.family_disposition([], True),
                         {"disposition": "REJECT / NO_SURVIVOR", "verdict_recommendation": "REJECT",
                          "performance_claimable_recommendation": False})
        self.assertEqual(sa.family_disposition([surv], True),
                         {"disposition": "SURVIVOR_FOUND", "verdict_recommendation": "PASS",
                          "performance_claimable_recommendation": True})
        two = sa.family_disposition([surv, dict(surv, cohort="y")], True)
        self.assertEqual(two["disposition"], "MULTIPLE_SURVIVORS")
        self.assertEqual(two["verdict_recommendation"], "FINALIST")
        self.assertFalse(two["performance_claimable_recommendation"])
        incomplete = sa.family_disposition([surv], False)
        self.assertEqual(incomplete["disposition"], "TECHNICAL_INCOMPLETE")
        self.assertEqual(incomplete["verdict_recommendation"], "TECHNICAL_INCOMPLETE")

    def test_record_carries_the_six_joint_axes(self):
        cohort = FakeCohort(FLAT + [ENTRY])
        p = {"window": 5, "discount": 0.01}
        dca = {"spacing_pct": 0.04, "size_multiplier": 1.1, "breakeven_tp_pct": 0.03,
               "invalidation_pct": 0.1, "base_quote": 1000}
        m = sa.simulate(cohort, (0, cohort.n), sa.rail_for(dca), p, {}, 0.0, "historical")
        row = sa.record(cohort, p, dca, "historical", m)
        self.assertEqual(sa.cell_key(row), (5, 0.01, 0.04, 1.1, 0.03, 0.1))
        self.assertEqual(row["window_kind"], "historical")


if __name__ == "__main__":
    unittest.main(verbosity=2)
