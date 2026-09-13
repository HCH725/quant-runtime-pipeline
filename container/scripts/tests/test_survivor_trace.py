#!/usr/bin/env python3
"""Engine-layer regression for the v1.6.0 inert trace hook (contract v1.6.0 sections 28.2/28.3).

Runs against a synthetic cohort (no Qlib, no container, no /results): the two things this file
exists to prove are

  * tracing is INERT - the same cell with `TRACE = None` and with a live sink returns a byte-equal
    `record()` row (so the aggregate, the calculation order and the semantics are untouched), and
  * the ledgers the hook emits are self-consistent - they reconcile back to the aggregate the
    engine returned, which is the check `runtime/survivor_evidence.py` repeats on the real
    frozen/replayed 18 cells.

Non-vacuity: `test_negative_control_dropped_episode_breaks_reconciliation` shows the same
reconciliation fails once a ledger row is removed, so a green run cannot be an artefact of the
check never being able to fail.

usage:  python3 container/scripts/tests/test_survivor_trace.py     (numpy interpreter)
        SA_ENGINE_PATH=<instrumented runner> python3 …             (audit-only staging override)
"""
import importlib.util
import os
import sys
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ENGINE = os.path.join(os.path.dirname(HERE), "20_strategy_a_run.py")
ENGINE_PATH = os.environ.get("SA_ENGINE_PATH", DEFAULT_ENGINE)
TOL = 1e-9


def load_engine(path):
    spec = importlib.util.spec_from_file_location("strategy_a_engine_trace_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Collector(object):
    """The same sink shape the replay driver hands to the engine."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.fills = []
        self.episodes = []
        self.equity = None

    def __call__(self, event, fields):
        if event == "fill":
            self.fills.append(dict(fields))
        elif event == "episode":
            self.episodes.append(dict(fields))
        elif event == "equity_marks":
            self.equity = dict(fields)


class FakeCohort(object):
    """The subset of `Cohort` that `simulate()` reads - deterministic, Qlib-free."""

    def __init__(self, bars=600, bars_per_day=20, seed=20260913):
        rng = np.random.RandomState(seed)
        i = np.arange(bars, dtype=np.float64)
        close = 100.0 + 6.0 * np.sin(i / 7.0) + rng.normal(0.0, 0.35, bars)
        self.symbol = "SYNTHUSDT"
        self.timeframe = "1h"
        self.qlib_freq = "60min"
        self.open_time_ms = (i * 3600000).astype(np.int64)
        self.open = close + rng.normal(0.0, 0.05, bars)
        self.close = close
        self.high = np.maximum(self.open, close) + 0.4
        self.low = np.minimum(self.open, close) - 0.4
        self.volume = np.ones(bars)
        self.n = bars
        day_id = (i // bars_per_day).astype(np.int64)
        self.day_index = day_id
        self.n_days = int(day_id[-1]) + 1
        self.day_end = set(int(k) for k in np.flatnonzero(
            np.concatenate([day_id[1:] != day_id[:-1], [True]])))
        self.price_increment = 0.1
        self.taker_fee = 0.0005
        self.leverage = 10.0
        self.margin_maint = 0.005
        self.funding = np.zeros(bars, dtype=np.float64)


class TestTraceHook(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = load_engine(ENGINE_PATH)
        cls.cohort = FakeCohort()
        cls.p = {"window": 20, "discount": 0.03}
        cls.dca = {"base_quote": 1000, "spacing_pct": 0.01, "size_multiplier": 1.0,
                   "breakeven_tp_pct": 0.01, "invalidation_pct": 0.1}
        cls.rail = cls.engine.rail_for(cls.dca)
        cls.window = (0, cls.cohort.n)
        cls.cases = [("plain", {}, 1), ("fee_2x", {"fee_mult": 2.0}, 1),
                     ("funding_2x", {"funding_mult": 2.0}, 1),
                     ("entry_delay_1_bar", {"entry_delay_1_bar": True}, 1),
                     ("slippage_2ticks", {}, 2), ("no_funding", {"no_funding": True}, 1)]

    def run_case(self, stress, slip):
        engine, cohort = self.engine, self.cohort
        engine.TRACE = None
        plain = engine.simulate(cohort, self.window, self.rail, self.p, stress, slip, "full")
        collector = Collector()
        engine.TRACE = collector
        traced = engine.simulate(cohort, self.window, self.rail, self.p, stress, slip, "full")
        engine.TRACE = None
        row = engine.record(cohort, self.p, self.dca, "full", traced)
        return row, collector

    def test_trace_off_on_aggregate_is_identical(self):
        for name, stress, slip in self.cases:
            engine, cohort = self.engine, self.cohort
            engine.TRACE = None
            plain = engine.record(cohort, self.p, self.dca, "full",
                                  engine.simulate(cohort, self.window, self.rail, self.p,
                                                  stress, slip, "full"))
            row, _collector = self.run_case(stress, slip)
            self.assertEqual(plain, row,
                             "trace off/on aggregate differs for case %s" % name)
            self.assertTrue(row["episodes"] > 0, "case %s produced no episode" % name)

    def test_trace_none_emits_nothing(self):
        engine, cohort = self.engine, self.cohort
        collector = Collector()
        engine.TRACE = None
        engine.simulate(cohort, self.window, self.rail, self.p, {}, 1, "full")
        self.assertEqual(collector.fills, [])
        self.assertEqual(collector.episodes, [])
        self.assertIsNone(collector.equity)

    def test_episode_ledger_reconciles_to_aggregate(self):
        row, collector = self.run_case({}, 1)
        # `record()` emits the aggregate rounded to 6 decimals, so the ledger sum is compared at
        # the rounding quantum - exactly the rule `runtime/survivor_evidence.py` applies to the
        # real frozen/replayed cells.
        for field, key in (("gross_pnl", "gross_pnl"), ("fees", "fees"),
                           ("funding", "funding"), ("net_pnl", "net_pnl")):
            total = sum(e[field] for e in collector.episodes)
            self.assertAlmostEqual(total, row[key], delta=TOL * max(1.0, abs(row[key])),
                                   msg="sum(episode %s) != aggregate %s" % (field, key))
        self.assertEqual(len(collector.episodes), row["episodes"])

    def test_episode_partition_matches_aggregate(self):
        row, collector = self.run_case({}, 1)
        for reason, key in (("TP", "tp_hits"), ("STOP", "stop_hits"),
                            ("MARGIN_CALL", "margin_calls"), ("EOD_FLATTEN", "open_at_end")):
            self.assertEqual(sum(1 for e in collector.episodes if e["exit_reason"] == reason),
                             row[key], "episode partition %s != aggregate %s" % (reason, key))
        for episode in collector.episodes:
            self.assertAlmostEqual(episode["gross_pnl"] - episode["fees"] - episode["funding"],
                                   episode["net_pnl"], delta=TOL)
            self.assertTrue(episode["layers_used"] >= 1)

    def test_fill_ledger_closes_every_episode(self):
        _row, collector = self.run_case({}, 1)
        closed = {e["episode"] for e in collector.episodes}
        self.assertEqual(sorted({f["episode"] for f in collector.fills}), sorted(closed))
        for fill in collector.fills:
            self.assertIn(fill["event_type"], ("ENTRY", "DCA_ADD", "EXIT", "FLATTEN"))
            self.assertIsNotNone(fill["open_time_ms"])
        firsts = {}
        for fill in collector.fills:
            firsts.setdefault(fill["episode"], fill["event_type"])
        self.assertTrue(all(kind == "ENTRY" for kind in firsts.values()),
                        "every episode must open with its ENTRY fill")

    def test_equity_ledger_recomputes_sharpe_and_drawdown(self):
        row, collector = self.run_case({}, 1)
        equity = collector.equity
        self.assertIsNotNone(equity)
        series = np.array(equity["equity"], dtype=np.float64)
        self.assertEqual(len(series), self.cohort.n_days)
        self.assertEqual(len(equity["in_window"]), self.cohort.n_days)
        self.assertTrue(any(equity["in_window"]))
        rets = np.diff(series) / series[:-1]
        sd = float(np.std(rets, ddof=1))
        sharpe = float(np.mean(rets) / sd * np.sqrt(365.0)) if sd > 0 else 0.0
        peak = np.maximum.accumulate(series)
        dd = series - peak
        self.assertAlmostEqual(round(sharpe, 6), row["sharpe"], places=6)
        self.assertAlmostEqual(round(float(dd.min()), 6), row["max_dd_usdt"], places=6)
        self.assertAlmostEqual(round(float((dd / peak).min()), 6), row["max_dd_pct"], places=6)

    def test_negative_control_dropped_episode_breaks_reconciliation(self):
        row, collector = self.run_case({}, 1)
        trimmed = collector.episodes[:-1]
        total = sum(e["net_pnl"] for e in trimmed)
        self.assertGreater(abs(total - row["net_pnl"]), TOL,
                           "dropping an episode must break the reconciliation (the check must "
                           "not be vacuous)")
        self.assertNotEqual(len(trimmed), row["episodes"])


if __name__ == "__main__":
    print("engine: %s" % ENGINE_PATH)
    unittest.main(verbosity=2)
