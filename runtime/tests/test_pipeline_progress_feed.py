#!/usr/bin/env python3
"""Checks for runtime/pipeline_progress_feed.py.

All fixtures live in temporary directories. Tests prove bootstrap silence, one-time milestone
delivery, chronological PREPARATION -> QLIB START -> COMPLETE ordering, canonical counts, and the
hard boundary that the dedupe cursor can never be written under the results root.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME))
import pipeline_progress_feed as feed  # noqa: E402


class Harness(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="qrp-progress-results-"))
        self.state_dir = Path(tempfile.mkdtemp(prefix="qrp-progress-state-"))
        self.state = self.state_dir / "progress.json"

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.state_dir, ignore_errors=True)

    def write(self, rel, doc):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(doc if isinstance(doc, str) else json.dumps(doc))
        return path

    def preparation(self, family="fam-a", state="running", updated="2026-10-02T01:00:00Z"):
        return self.write(
            "_handoff/preparing/%s/preparation-status.json" % family,
            {"schema_version": 1, "family_id": family, "state": state,
             "updated_at_utc": updated, "reason": "fixture"})

    def qlib(self, family="fam-a", round_id="fam-a-r1", run_id="fam-a-r1-u1",
             planned=86400, stage="RUNNING_QLIB"):
        self.write("%s/rounds/%s/round-spec.json" % (family, round_id),
                   {"family_id": family, "round_id": round_id,
                    "expected": {"expected_case_evaluations": planned}})
        self.write("%s/rounds/%s/attempts/%s/run-spec.json" % (family, round_id, run_id),
                   {"family_id": family, "round_id": round_id, "run_id": run_id,
                    "expected": {"expected_case_evaluations": planned}})
        return self.write("%s/rounds/%s/attempts/%s/state.json" % (family, round_id, run_id),
                          {"schema_version": 1, "family_id": family, "round_id": round_id,
                           "run_id": run_id, "stage": stage})

    def complete(self, family="fam-a", round_id="fam-a-r1", run_id="fam-a-r1-u1",
                 verdict="PASS", planned=86400, executed=86400, survivors=2):
        base = "%s/rounds/%s/attempts/%s" % (family, round_id, run_id)
        self.write(base + "/result.json",
                   {"family_id": family, "round_id": round_id, "run_id": run_id,
                    "expected_case_evaluations": planned, "case_evaluations_total": executed,
                    "cohort_survivor_count": survivors,
                    "cohort_survivors": ["SYM%d/1h" % i for i in range(survivors)]})
        self.write(base + "/artifacts/cohort_survivors.json",
                   [{"cohort": "SYM%d/1h" % i} for i in range(survivors)])
        return self.write("%s/rounds/%s/verdict.json" % (family, round_id),
                          {"schema_version": 1, "family_id": family, "round_id": round_id,
                           "run_id": run_id, "verdict": verdict,
                           "cohort_survivor_count": survivors,
                           "cohort_survivors": ["SYM%d/1h" % i for i in range(survivors)],
                           "decided_at_utc": "2026-10-02T03:00:00Z"})

    def bootstrap(self):
        self.assertEqual(feed.tick(self.root, self.state), [])
        self.assertTrue(self.state.is_file())

    def test_bootstrap_is_silent_and_baselines_existing_history(self):
        self.preparation()
        self.qlib()
        self.complete()
        self.bootstrap()
        state = json.loads(self.state.read_text())
        self.assertEqual(len(state["seen"]), 3)
        self.assertEqual(feed.tick(self.root, self.state), [])

    def test_preparation_emits_once_and_deferred_does_not_emit(self):
        self.bootstrap()
        self.preparation()
        events = feed.tick(self.root, self.state)
        self.assertEqual([event["kind"] for event in events], ["preparation"])
        self.assertIn("🟡 **PREPARATION**", feed.render(events[0]))
        self.assertEqual(feed.tick(self.root, self.state), [])

        self.preparation(family="fam-b", state="deferred")
        self.assertEqual(feed.tick(self.root, self.state), [])

    def test_qlib_start_carries_planned_evaluations_and_emits_once(self):
        self.bootstrap()
        self.qlib(planned=103680)
        events = feed.tick(self.root, self.state)
        self.assertEqual([event["kind"] for event in events], ["qlib"])
        self.assertEqual(events[0]["planned"], 103680)
        text = feed.render(events[0])
        self.assertIn("🔵 **QLIB START**", text)
        self.assertIn("103,680 evaluations", text)
        self.assertIn("r1/u1", text)
        self.assertEqual(feed.tick(self.root, self.state), [])

    def test_fast_run_missed_between_ticks_still_emits_all_three_in_order(self):
        self.bootstrap()
        self.preparation(updated="2026-10-02T01:00:00Z")
        state_path = self.qlib(planned=86400)
        os.utime(state_path, (1790906400, 1790906400))  # 2026-10-02T02:00:00Z
        self.complete(planned=86400, executed=86400, survivors=3)
        events = feed.tick(self.root, self.state)
        self.assertEqual([event["kind"] for event in events],
                         ["preparation", "qlib", "complete"])
        text = feed.render(events[-1])
        self.assertIn("**PASS**", text)
        self.assertIn("86,400 / 86,400", text)
        self.assertIn("Formal promoted survivors: **3**", text)

    def test_reject_completion_reports_zero_formal_promotions_even_with_cohort_survivors(self):
        self.bootstrap()
        self.qlib(planned=1920)
        feed.tick(self.root, self.state)
        self.complete(verdict="REJECT", planned=1920, executed=1920, survivors=3)
        events = feed.tick(self.root, self.state)
        self.assertEqual([event["kind"] for event in events], ["complete"])
        text = feed.render(events[0])
        self.assertIn("**REJECT**", text)
        self.assertIn("1,920 / 1,920", text)
        self.assertIn("Formal promoted survivors: **0**", text)

    def test_no_compute_terminal_is_completion_without_fake_survivor_count(self):
        self.bootstrap()
        family, round_id = "fam-data", "fam-data-r1"
        self.write("%s/rounds/%s/verdict.json" % (family, round_id),
                   {"schema_version": 1, "family_id": family, "round_id": round_id,
                    "run_id": None, "verdict": "TECHNICAL_INCOMPLETE",
                    "decided_at_utc": "2026-10-02T03:00:00Z",
                    "attempts": {"launched": 0}})
        events = feed.tick(self.root, self.state)
        self.assertEqual([event["kind"] for event in events], ["complete"])
        text = feed.render(events[0])
        self.assertIn("0 (no Qlib compute)", text)
        self.assertIn("Formal promoted survivors: **0**", text)

    def test_invalid_verdict_is_not_a_milestone(self):
        self.bootstrap()
        self.write("fam-a/rounds/fam-a-r1/verdict.json",
                   {"family_id": "fam-a", "round_id": "fam-a-r1", "verdict": "MAYBE"})
        self.assertEqual(feed.tick(self.root, self.state), [])

    def test_state_must_stay_outside_results_root(self):
        with self.assertRaisesRegex(ValueError, "outside the results root"):
            feed.tick(self.root, self.root / "_observer-state.json")

    def test_malformed_state_fails_closed_instead_of_replaying_history(self):
        self.preparation()
        self.state.write_text('{"schema_version": 999, "seen": []}')
        with self.assertRaisesRegex(ValueError, "invalid progress-feed state"):
            feed.tick(self.root, self.state)


if __name__ == "__main__":
    unittest.main(verbosity=2)
