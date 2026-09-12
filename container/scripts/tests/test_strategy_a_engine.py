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
    def __init__(self, rows, tick=0.0, taf=0.0, funding=None, margin_maint=0.1):
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
