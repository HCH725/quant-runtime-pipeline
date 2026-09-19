#!/usr/bin/env python3
"""Host-side checks for the CMT survivor-replay driver (no numpy, no qlib, no engine import).

Run:  /opt/homebrew/bin/python3 container/scripts/tests/test_cmt_survivor_replay.py

The driver itself is pure stdlib; these cases pin the parts that decide *policy* — the §28.3
registered-grid enumeration, the write boundary, the frozen-row comparison typing and the
deterministic ledger reconciliation — against synthetic frozen artifacts built in a temp dir.
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
DRIVER = os.path.join(os.path.dirname(HERE), "141_continuous_macro_timing_survivor_replay.py")


def load_driver():
    spec = importlib.util.spec_from_file_location("cmt_replay_driver", DRIVER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


drv = load_driver()


def sentinel(manifest):
    return {"status": "DONE", "artifact_manifest": manifest}


def spec_with(expected=None):
    out = {}
    if expected is not None:
        out["expected_outputs"] = expected
    return out


class Enumeration(unittest.TestCase):
    """Contract 28.3: two frozen sources must agree, or the driver refuses."""

    def test_agreeing_sources_are_accepted(self):
        manifest = ["artifacts/grid_full.csv", "artifacts/grid_oos.csv"]
        got = drv.registered_grid_names(sentinel(manifest), spec_with(manifest[::-1]), {})
        self.assertEqual(got, ["full", "oos"])

    def test_run_spec_without_expected_outputs_is_refused(self):
        # the measured shape of every frozen CMT run-spec: the names live in the round-spec
        # pre-registration, not in the run-spec, so the published rule must refuse loudly
        with self.assertRaises(SystemExit) as ctx:
            drv.registered_grid_names(sentinel(["artifacts/grid_full.csv"]), spec_with(), {})
        self.assertIn("expected_outputs", str(ctx.exception))

    def test_disagreeing_sources_are_refused(self):
        with self.assertRaises(SystemExit):
            drv.registered_grid_names(sentinel(["artifacts/grid_full.csv"]),
                                      spec_with(["artifacts/grid_oos.csv"]), {})

    def test_empty_manifest_is_refused(self):
        with self.assertRaises(SystemExit):
            drv.registered_grid_names(sentinel([]), spec_with([]), {})

    def test_non_grid_manifest_entries_are_ignored(self):
        manifest = ["artifacts/grid_full.csv", "artifacts/input_manifest.json"]
        got = drv.registered_grid_names(sentinel(manifest),
                                       spec_with(["artifacts/grid_full.csv"]), {})
        self.assertEqual(got, ["full"])


class WriteBoundary(unittest.TestCase):
    def test_staging_outside_survivors_evidence_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNotNone(drv.evidence_boundary_problem(
                os.path.join(tmp, "family-2026", "rounds")))
            self.assertIsNotNone(drv.evidence_boundary_problem(tmp))
            self.assertIsNone(drv.evidence_boundary_problem(
                os.path.join(tmp, "_survivors", "evidence", ".staging-cmt-r2")))
            self.assertIsNone(drv.evidence_boundary_problem(
                os.path.join(tmp, "_survivors", "evidence")))


class GridPlan(unittest.TestCase):
    class Engine:
        STRESS = (("stress_q90", {"combo": "q90"}), ("slip_2", {"slip_ticks": 2}))
        COST_ATTRITION_STRESS = {"cost_attrition_bps": 40}
        COHORT_GRID_KINDS = ("historical", "oos", "full", "stress_q90", "slip_2",
                             "cost_attrition_40bps", "no_funding", "no_funding_full")

    SPEC = {"costs": {"baseline_slippage_ticks": 1},
            "data": {"start": "2022-01-01", "end": "2026-09-11",
                     "historical_start": "2022-01-01", "historical_end": "2025-09-30",
                     "oos_start": "2025-10-01", "oos_end": "2026-09-11"}}

    def test_registered_plan_matches_the_engine(self):
        got = {}
        for grid in self.Engine.COHORT_GRID_KINDS:
            window, stress, slip = drv.grid_plan(self.Engine, self.SPEC, grid)
            got[grid] = (window, stress, slip)
        self.assertEqual(got["full"][0], ("2022-01-01", "2026-09-11"))
        self.assertEqual(got["historical"][0], ("2022-01-01", "2025-09-30"))
        self.assertEqual(got["oos"][0], ("2025-10-01", "2026-09-11"))
        self.assertEqual(got["full"][1], {})
        self.assertEqual(got["stress_q90"], (("2022-01-01", "2026-09-11"), {"combo": "q90"}, 1))
        self.assertEqual(got["slip_2"][2], 2)
        self.assertEqual(got["cost_attrition_40bps"],
                         (("2022-01-01", "2026-09-11"), {"cost_attrition_bps": 40}, 1))
        self.assertEqual(got["no_funding"][0], ("2022-01-01", "2025-09-30"))
        self.assertEqual(got["no_funding_full"][1], {"no_funding": True})

    def test_unregistered_grid_is_refused(self):
        with self.assertRaises(SystemExit):
            drv.grid_plan(self.Engine, self.SPEC, "grid_made_up")


class FrozenRowComparison(unittest.TestCase):
    def test_typing(self):
        self.assertTrue(drv.values_equal(True, "True"))
        self.assertTrue(drv.values_equal(False, "False"))
        self.assertFalse(drv.values_equal(False, "True"))
        self.assertTrue(drv.values_equal(1011.6, "1011.6"))
        self.assertTrue(drv.values_equal(None, ""))
        self.assertFalse(drv.values_equal(None, "0"))
        self.assertTrue(drv.values_equal(7, "7"))

    def test_number_only_coordinates(self):
        self.assertTrue(drv.numbers_equal("1011.6", 1011.6))
        self.assertFalse(drv.numbers_equal("", 1011.6))
        self.assertFalse(drv.numbers_equal("TP", 0))


class Reconcile(unittest.TestCase):
    def ledger(self):
        """A flat equity stretch: zero returns, zero drawdown, zero Sharpe - exact zeros."""
        aggregates = {"episodes": 1, "tp_hits": 1, "stop_hits": 0, "margin_calls": 0,
                      "open_at_end": 0, "time_exits": 0, "gross_pnl": 12.5, "fees": 2.5,
                      "funding": 0.0, "net_pnl": 10.0, "days": 3,
                      "max_dd_usdt": 0.0, "max_dd_pct": 0.0, "sharpe": 0.0}
        episodes = [{"episode": 0, "exit_reason": "TP", "gross_pnl": 12.5, "fees": 2.5,
                     "funding": 0.0, "net_pnl": 10.0}]
        fills = [{"episode": 0, "event_type": "ENTRY"}, {"episode": 0, "event_type": "EXIT"}]
        equity = self.equity([100.0, 100.0, 100.0])
        return aggregates, episodes, fills, equity

    @staticmethod
    def equity(series):
        rows = []
        peak = -float("inf")
        for i, value in enumerate(series):
            peak = max(peak, value)
            rows.append({"day_index": i, "equity": value, "peak": peak,
                         "drawdown_usdt": value - peak,
                         "drawdown_pct": (value - peak) / peak if peak else 0.0,
                         "in_window": True})
        return rows

    def test_consistent_ledger_passes(self):
        aggregates, episodes, fills, equity = self.ledger()
        self.assertEqual(drv.reconcile(aggregates, fills, episodes, equity), [])

    def test_sharpe_that_contradicts_the_equity_column_is_caught(self):
        # 100 -> 95 -> 110 has a real daily-return Sharpe (~7.0111); an aggregate claiming 0
        # must be refused, exactly as runtime/survivor_evidence.py recomputes it host-side.
        aggregates, episodes, fills, _ = self.ledger()
        equity = self.equity([100.0, 95.0, 110.0])
        problems = drv.reconcile(aggregates, fills, episodes, equity)
        self.assertTrue(any("sharpe" in p for p in problems), problems)

    def test_drawdown_that_contradicts_the_equity_column_is_caught(self):
        aggregates, episodes, fills, _ = self.ledger()
        equity = self.equity([100.0, 95.0, 110.0])
        problems = drv.reconcile(aggregates, fills, episodes, equity)
        self.assertTrue(any("max_dd_usdt" in p for p in problems), problems)
        self.assertTrue(any("max_dd_pct" in p for p in problems), problems)

    def test_partition_mismatch_is_caught(self):
        aggregates, episodes, fills, equity = self.ledger()
        aggregates["tp_hits"] = 0
        aggregates["stop_hits"] = 1
        self.assertTrue(drv.reconcile(aggregates, fills, episodes, equity))

    def test_accounting_identity_is_caught(self):
        aggregates, episodes, fills, equity = self.ledger()
        episodes[0]["net_pnl"] = 99.0
        self.assertTrue(drv.reconcile(aggregates, fills, episodes, equity))

    def test_day_count_is_caught(self):
        aggregates, episodes, fills, equity = self.ledger()
        aggregates["days"] = 4
        self.assertTrue(drv.reconcile(aggregates, fills, episodes, equity))

    def test_stale_peak_column_is_caught(self):
        aggregates, episodes, fills, equity = self.ledger()
        equity[2]["peak"] = 95.0
        self.assertTrue(drv.reconcile(aggregates, fills, episodes, equity))

    def test_unregistered_event_type_is_caught(self):
        aggregates, episodes, fills, equity = self.ledger()
        fills[0]["event_type"] = "PARTIAL"
        self.assertTrue(drv.reconcile(aggregates, fills, episodes, equity))


class PathMapping(unittest.TestCase):
    def test_host_prefix_is_mapped_onto_the_runtime_root(self):
        index = {"results_root": "/Volumes/ExpansionDrive/qlib-results"}
        self.assertEqual(drv.to_local("/Volumes/ExpansionDrive/qlib-results/_survivors/x.json",
                                     index, "/results"),
                         "/results/_survivors/x.json")
        self.assertEqual(drv.to_local("/other/place/x.json", index, "/results"),
                         "/other/place/x.json")


if __name__ == "__main__":
    unittest.main(verbosity=2)
