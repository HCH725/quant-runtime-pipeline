#!/usr/bin/env python3
"""Checks for runtime/runtime_observation.py (Phase 2: the n8n runtime truth feed).

Simulation checks over a temp results root. Every lifecycle state the n8n canvas must distinguish is
built as real artifacts - registered direct-Hermes family with no attempt yet (preflight), a live
Qlib attempt (stage / progress / cohort), an attempt that published a terminal sentinel while its own
round verdict is missing (disposition), a recent round verdict (terminal) and no work at all (idle) -
and the runtime counts are asserted against the same temp root. The canonical intake source, the
watchdog state and the board read-back helpers are all replaced by temp paths / hard failures, so
neither a real source nor the board participates: a Kanban status can never drive `current`.

Run: python3 runtime/tests/test_runtime_observation.py     (stdlib unittest, no dependencies)
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Optional
from unittest import mock

RUNTIME = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME))
import candidate_snapshot as cs  # noqa: E402
import production_handoff as ph  # noqa: E402
import runtime_observation as ro  # noqa: E402

NOW = time.time()
GRID_HEADER = "symbol,timeframe,cohort,sharpe\n"


def iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def age(path, minutes):
    stamp = NOW - minutes * 60.0
    os.utime(path, (stamp, stamp))
    return path


def family(root, family_id, created_minutes_ago=1.0, kanban_task_id=None):
    doc = {"schema_version": 1, "schema": "quant-family/v2", "family_id": family_id,
           "handoff": {"execution": "direct_hermes", "mode": "direct"}}
    if kanban_task_id:
        doc["kanban_task_id"] = kanban_task_id
    if created_minutes_ago is not None:
        doc["created_at_utc"] = iso(NOW - created_minutes_ago * 60.0)
    path = write(Path(root) / family_id / "family.json", json.dumps(doc, indent=1) + "\n")
    age(path, created_minutes_ago)
    age(path.parent, created_minutes_ago)
    return path


def round_spec(root, family_id, round_id, expected: Optional[int] = 1000, age_minutes=240.0):
    """`expected=None` writes the scalar a prerequisite-gated round carries (no case denominator)."""
    spec = {"schema": "quant-round-spec/v1", "family_id": family_id, "round_id": round_id}
    spec["expected"] = ("not_computable" if expected is None
                        else {"expected_case_evaluations": expected})
    path = write(Path(root) / family_id / "rounds" / round_id / "round-spec.json",
                 json.dumps(spec, indent=1) + "\n")
    return age(path, age_minutes)


def attempt(root, family_id, round_id, run_id, stage, age_minutes, sentinel=None, rows=40,
            symbol="BTCUSDT", timeframe="1h", progress=None, state=True):
    base = Path(root) / family_id / "rounds" / round_id / "attempts" / run_id
    write(base / "run-spec.json",
          json.dumps({"schema": "quant-run-spec/v1", "family_id": family_id, "round_id": round_id,
                      "run_id": run_id,
                      "created_at_utc": iso(NOW - age_minutes * 60.0)}, indent=1) + "\n")
    if state:
        write(base / "state.json", json.dumps({"schema": "quant-attempt-state/v1",
                                               "family_id": family_id, "round_id": round_id,
                                               "run_id": run_id, "stage": stage}, indent=1) + "\n")
    write(base / "run.log", "fixture run log\n" * 3)
    write(base / "artifacts" / "grid_fixture.csv",
          GRID_HEADER + "".join("%s,%s,c%d,%d\n" % (symbol, timeframe, i, i) for i in range(rows)))
    if progress is not None:
        write(base / "artifacts" / "progress.json", json.dumps(progress) + "\n")
    if sentinel:
        write(base / sentinel, json.dumps({"status": sentinel, "family_id": family_id,
                                           "round_id": round_id, "run_id": run_id}) + "\n")
    for path in [base, base / "artifacts", base.parent, base.parent.parent,
                 base.parent.parent.parent] + sorted(base.rglob("*")):
        if path.is_file() or path.is_dir():
            age(path, age_minutes)
    return base


def verdict(root, family_id, round_id, token, age_minutes=1.0, kanban_task_id=None):
    doc = {"schema": "quant-round-verdict/v1", "family_id": family_id, "round_id": round_id,
           "verdict": token, "released_at_utc": iso(NOW - age_minutes * 60.0)}
    if kanban_task_id:
        doc["kanban_task_id"] = kanban_task_id
    path = write(Path(root) / family_id / "rounds" / round_id / "verdict.json",
                 json.dumps(doc, indent=1) + "\n")
    return age(path, age_minutes)


def pool(root, candidates):
    write(Path(root) / ph.HANDOFF_DIRNAME / ph.POOL_FILENAME,
          json.dumps({"schema": "quant-candidate-pool/v1", "updated_at_utc": "2026-09-24T00:00:00Z",
                      "candidates": [{"family_id": item} for item in candidates]}, indent=1) + "\n")


def ledger(root, lines):
    write(Path(root) / ph.HANDOFF_DIRNAME / ph.LOG_FILENAME,
          "".join(json.dumps(line) + "\n" for line in lines))


def leaderboard(root, entries):
    write(Path(root) / "_survivors" / "leaderboard.json",
          json.dumps({"schema": "quant-leaderboard/v1", "entries": entries}, indent=1) + "\n")


def tree(root):
    """(relative path, size, mtime) for everything under `root` - used to prove nothing was written."""
    root = Path(root)
    if not root.is_dir():
        return []
    items = []
    for path in sorted(root.rglob("*")):
        stat = path.stat()
        items.append((str(path.relative_to(root)), stat.st_size, round(stat.st_mtime, 6)))
    return items


class ObservationHarness(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="qrp-observation-root-")
        self.state = tempfile.mkdtemp(prefix="qrp-observation-state-")
        self._real = (cs.REVIEW_STATE, cs.INTAKE_OUTPUT, cs.WATCHDOG_STATE,
                      cs.card_status, cs.board_counts)
        cs.REVIEW_STATE = str(Path(self.state) / "review-state.json")
        cs.INTAKE_OUTPUT = str(Path(self.state) / "intake-output")
        cs.WATCHDOG_STATE = Path(self.state) / "quant_runtime_watchdog.json"
        write(cs.WATCHDOG_STATE, json.dumps({"schema_version": 1,
                                             "last_check_at_utc": "2026-09-24T00:00:00Z",
                                             "last_healthy_at_utc": "2026-09-24T00:00:00Z",
                                             "active_signatures": {}}) + "\n")
        # A board read-back must never happen while observing: fail loudly if anything tries.
        cs.card_status = self._forbidden("card_status")
        cs.board_counts = self._forbidden("board_counts")

    def tearDown(self):
        (cs.REVIEW_STATE, cs.INTAKE_OUTPUT, cs.WATCHDOG_STATE,
         cs.card_status, cs.board_counts) = self._real
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.state, ignore_errors=True)

    @staticmethod
    def _forbidden(name):
        def _raise(*_args, **_kwargs):
            raise AssertionError("the runtime observation must not call %s" % name)
        return _raise

    def observe(self, root=None):
        return ro.observe(results_root=root or self.root, now=NOW)


class LifecycleStates(ObservationHarness):
    def test_registered_direct_family_without_attempt_is_preflight(self):
        family(self.root, "fam-preflight", created_minutes_ago=2.0)
        write(Path(self.root) / "fam-preflight" / "agent-task.md", "frozen direct prompt\n")
        age(write(Path(self.root) / "fam-preflight" / "agent.log", "adapting\n"), 1.0)
        current = self.observe()["current"]
        self.assertEqual(current["state"], "preflight")
        self.assertEqual(current["family_id"], "fam-preflight")
        self.assertIsNone(current["attempt"])
        self.assertIsNone(current["stage"])
        self.assertIsNone(current["verdict"])
        self.assertFalse(current["progress"]["available"])
        self.assertTrue(current["agent"]["prompt_frozen"])
        self.assertTrue(current["agent"]["agent_log"].endswith("fam-preflight/agent.log"))
        self.assertIn("launch grace", current["why"])
        self.assertEqual(current["age_minutes"], None)

    def test_attempt_without_a_published_stage_is_still_preflight(self):
        """The live Phase-1 shape: the direct worker holds the attempt, Qlib has published no stage."""
        family(self.root, "fam-live", created_minutes_ago=240.0)
        round_spec(self.root, "fam-live", "fam-live-r1", expected=None)
        attempt(self.root, "fam-live", "fam-live-r1", "run1", None, 2.0, state=False)
        current = self.observe()["current"]
        self.assertEqual(current["state"], "preflight")
        self.assertEqual(current["family_id"], "fam-live")
        self.assertEqual(current["attempt"], "run1")
        self.assertIsNone(current["stage"])
        self.assertFalse(current["progress"]["available"])
        self.assertIn("no Qlib stage yet", current["why"])

    def test_live_attempt_is_qlib_active_with_stage_progress_and_cohort(self):
        family(self.root, "fam-live", created_minutes_ago=240.0)
        round_spec(self.root, "fam-live", "fam-live-r1")
        attempt(self.root, "fam-live", "fam-live-r1", "run1", cs.RUNNING_STAGE, 1.0)
        current = self.observe()["current"]
        self.assertEqual(current["state"], "qlib_active")
        self.assertEqual(current["family_id"], "fam-live")
        self.assertEqual(current["stage"], cs.RUNNING_STAGE)
        self.assertEqual(current["round_id"], "fam-live-r1")
        self.assertEqual(current["attempt"], "run1")
        self.assertEqual(current["cohort"], "BTCUSDT / 1h")
        # 40 streamed grid rows against the immutable round-spec denominator of 1000.
        self.assertEqual(current["progress"], {"available": True, "pct": 4.0, "done": 40,
                                               "total": 1000, "text": "4.0% (40 / 1,000)"})
        self.assertIsNone(current["verdict"])

    def test_live_attempt_uses_cohort_progress_without_a_case_denominator(self):
        family(self.root, "fam-live", created_minutes_ago=240.0)
        round_spec(self.root, "fam-live", "fam-live-r1", expected=None)
        attempt(self.root, "fam-live", "fam-live-r1", "run1", cs.RUNNING_STAGE, 1.0,
                progress={"cohorts_done": 4, "cohorts_total": 28, "case_evaluations": 9500})
        progress = self.observe()["current"]["progress"]
        self.assertEqual(progress, {"available": True, "mode": "cohort", "pct": 14.3,
                                    "done": 4, "total": 28,
                                    "text": "14.3% cohorts (4 / 28)"})

    def test_case_denominator_progress_still_precedes_cohort_fallback(self):
        family(self.root, "fam-live", created_minutes_ago=240.0)
        round_spec(self.root, "fam-live", "fam-live-r1", expected=1000)
        attempt(self.root, "fam-live", "fam-live-r1", "run1", cs.RUNNING_STAGE, 1.0,
                progress={"cohorts_done": 4, "cohorts_total": 28})
        progress = self.observe()["current"]["progress"]
        self.assertTrue(progress["available"])
        self.assertEqual(progress["total"], 1000)
        self.assertNotIn("mode", progress)
        self.assertNotIn("cohorts", progress["text"])

    def test_malformed_or_mismatched_cohort_progress_stays_unavailable(self):
        family(self.root, "fam-live", created_minutes_ago=240.0)
        round_spec(self.root, "fam-live", "fam-live-r1", expected=None)
        base = attempt(self.root, "fam-live", "fam-live-r1", "run1", cs.RUNNING_STAGE, 1.0,
                       progress={"cohorts_done": 4, "cohorts_total": 28})
        progress_path = base / "artifacts" / "progress.json"
        invalid_payloads = {
            "malformed JSON": "{not json\n",
            "cohort count exceeds total": json.dumps({"cohorts_done": 29, "cohorts_total": 28}),
            "another attempt identity": json.dumps({"cohorts_done": 4, "cohorts_total": 28,
                                                     "run_id": "run2"}),
        }
        for label, payload in invalid_payloads.items():
            with self.subTest(case=label):
                progress_path.write_text(payload)
                progress = self.observe()["current"]["progress"]
                self.assertFalse(progress["available"])
                self.assertIsNone(progress["pct"])
                self.assertIsNone(progress["done"])
                self.assertIsNone(progress["total"])

    def test_cohort_fallback_is_not_borrowed_from_a_different_selected_attempt(self):
        attempt_path = attempt(self.root, "fam-live", "fam-live-r1", "run1", cs.RUNNING_STAGE, 1.0,
                               progress={"cohorts_done": 4, "cohorts_total": 28})
        selected_elsewhere = mock.Mock()
        selected_elsewhere.path = attempt_path.parent / "run2"
        with mock.patch.object(cs, "progress", return_value=(0.0, 0, None, cs.RUNNING_STAGE,
                                                              "other attempt", selected_elsewhere,
                                                              "fam-live-r1")):
            _stage, progress, _cohort, note = ro._progress_projection(
                self.root, "fam-live", attempt_path)
        self.assertFalse(progress["available"])
        self.assertIsNotNone(note)
        self.assertIn("differs from the live attempt", note)

    def test_published_sentinel_without_its_round_verdict_is_disposition(self):
        family(self.root, "fam-disp", created_minutes_ago=240.0)
        round_spec(self.root, "fam-disp", "fam-disp-r1")
        attempt(self.root, "fam-disp", "fam-disp-r1", "run1", "ARTIFACT_READY", 5.0, sentinel="DONE")
        current = self.observe()["current"]
        self.assertEqual(current["state"], "disposition")
        self.assertEqual(current["family_id"], "fam-disp")
        self.assertEqual(current["stage"], "ARTIFACT_READY")
        self.assertIsNone(current["verdict"])
        self.assertIn("DONE", current["why"])

    def test_recent_round_verdict_is_terminal_and_reuses_the_verdict_token(self):
        family(self.root, "fam-term", created_minutes_ago=240.0)
        round_spec(self.root, "fam-term", "fam-term-r1")
        attempt(self.root, "fam-term", "fam-term-r1", "run1", "ARTIFACT_READY", 6.0,
                sentinel="INCOMPLETE")
        verdict(self.root, "fam-term", "fam-term-r1", "TECHNICAL_INCOMPLETE", age_minutes=5.0)
        current = self.observe()["current"]
        self.assertEqual(current["state"], "terminal")
        self.assertEqual(current["family_id"], "fam-term")
        self.assertEqual(current["round_id"], "fam-term-r1")
        self.assertEqual(current["verdict"], "TECHNICAL_INCOMPLETE")
        self.assertLessEqual(current["age_minutes"], ph.ACTIVE_WINDOW_MINUTES)

    def test_verdict_outside_the_active_window_is_idle(self):
        family(self.root, "fam-stale", created_minutes_ago=400.0)
        round_spec(self.root, "fam-stale", "fam-stale-r1")
        attempt(self.root, "fam-stale", "fam-stale-r1", "run1", cs.RUNNING_STAGE, 300.0)
        verdict(self.root, "fam-stale", "fam-stale-r1", "REJECT",
                age_minutes=ph.ACTIVE_WINDOW_MINUTES + 10)
        current = self.observe()["current"]
        self.assertEqual(current["state"], "idle")
        self.assertIsNone(current["family_id"])
        self.assertIn("outside the %d-minute active window" % ph.ACTIVE_WINDOW_MINUTES, current["why"])

    def test_empty_root_is_idle(self):
        current = self.observe()["current"]
        self.assertEqual(current["state"], "idle")
        self.assertEqual(current["verdict"], None)
        # An empty root carries no pool and no ledger, so those two stay null and are reported.
        self.assertEqual([gap["field"] for gap in self.observe()["gaps"]],
                         ["candidate_pool", "last_pipeline_advance"])

    def test_unreadable_family_fails_closed_without_a_state(self):
        write(Path(self.root) / "fam-broken" / "family.json", "{not json\n")
        observation = self.observe()
        self.assertIsNone(observation["current"]["state"])
        self.assertEqual(observation["current"]["family_id"], "fam-broken")
        self.assertIn("current.state", [gap["field"] for gap in observation["gaps"]])
        self.assertIn("fail-closed", observation["current"]["why"])

    def test_unreadable_results_root_claims_nothing(self):
        observation = self.observe(root=str(Path(self.root) / "does-not-exist"))
        self.assertFalse(observation["results_root_readable"])
        self.assertIsNone(observation["current"]["state"])
        self.assertTrue(all(value is None for value in observation["counts"].values()))
        self.assertEqual([gap["field"] for gap in observation["gaps"]],
                         ["results_root", "counts", "current.state"])

    def test_only_the_documented_lifecycle_vocabulary_is_ever_published(self):
        family(self.root, "fam-preflight", created_minutes_ago=2.0)
        self.assertIn(self.observe()["current"]["state"], ro.STATES)
        self.assertEqual(self.observe()["current"]["rule"], ro.CURRENT_RULE)


class Counts(ObservationHarness):
    def build(self):
        pool(self.root, ["fam-live", "fam-term", "fam-queued", "fam-queued-2"])
        ledger(self.root, [
            {"schema": "quant-handoff-log/v1", "ran_at_utc": "2026-09-24T01:00:00Z",
             "outcome": "advanced", "action": "appended", "dry_run": False, "family_id": "fam-term"},
            {"schema": "quant-handoff-log/v1", "ran_at_utc": "2026-09-24T01:10:00Z",
             "outcome": "advanced", "action": "would_append", "dry_run": True, "family_id": "fam-live"},
            {"schema": "quant-handoff-log/v1", "ran_at_utc": "2026-09-24T01:20:00Z",
             "outcome": "finding", "action": "found_unresolved", "dry_run": False,
             "family_id": "fam-term"},
        ])
        leaderboard(self.root, [{"rank": 1, "family_id": "fam-term", "cohort": "c1", "sharpe": 1.5,
                                 "annualized_return": 0.2, "max_dd_pct": 0.1,
                                 "evidence_state": "PAPER"}])
        family(self.root, "fam-live", created_minutes_ago=240.0)
        round_spec(self.root, "fam-live", "fam-live-r1")
        attempt(self.root, "fam-live", "fam-live-r1", "run1", cs.RUNNING_STAGE, 1.0)
        family(self.root, "fam-term", created_minutes_ago=240.0)
        round_spec(self.root, "fam-term", "fam-term-r1")
        attempt(self.root, "fam-term", "fam-term-r1", "run1", "ARTIFACT_READY", 6.0,
                sentinel="INCOMPLETE", rows=7)
        verdict(self.root, "fam-term", "fam-term-r1", "TECHNICAL_INCOMPLETE", age_minutes=5.0)

    def test_counts_match_the_canonical_temp_root_artifacts(self):
        self.build()
        observation = self.observe()
        counts = observation["counts"]
        self.assertEqual(counts["families_registered"], 2)
        self.assertEqual(counts["families_backtested"], 2)
        self.assertEqual(counts["families_in_flight"], 1)
        self.assertEqual(counts["families_unresolved"], 0)
        # workload: both attempts streamed a grid (40 + 7 rows) and neither published a result.json.
        self.assertEqual(counts["workload_evaluations"], 47)
        self.assertEqual(counts["workload_grid_artifacts"], 2)
        self.assertEqual(counts["survivors"], 1)
        self.assertEqual(counts["leaderboard_count"], 1)
        self.assertEqual(counts["leaderboard_shown"], 1)
        self.assertEqual(counts["candidates_pool_total"], 4)
        self.assertEqual(counts["candidates_consumed"], 2)
        self.assertEqual(counts["candidates_queued"], 2)
        # the newest real advance is the appended line, never the dry-run `would_append`.
        self.assertEqual(counts["last_pipeline_advance_utc"], "2026-09-24T01:00:00Z")
        self.assertEqual(counts["last_pipeline_advance_family_id"], "fam-term")
        payload, reason = ro.pool_projection(self.root)
        self.assertIsNone(reason)
        self.assertEqual(observation["pool"]["rule"], (payload or {}).get("rule"))
        self.assertEqual(observation["leaderboard"]["top_n"], cs.DASHBOARD_TOP_N)

    def test_counts_follow_the_runtime_state_selection(self):
        self.build()
        observation = self.observe()
        self.assertEqual(observation["funnel"]["backtested"],
                         {"available": True, "families": 2, "registered": 2, "share_pct": 100.0,
                          "summary": "2 / 2 registered families"})
        self.assertEqual(observation["funnel"]["workload"]["evaluations"], 47)
        self.assertEqual(observation["health"]["status"], "ok")

    def test_unreadable_pool_and_ledger_stay_null_with_gaps(self):
        family(self.root, "fam-live", created_minutes_ago=240.0)
        observation = self.observe()
        self.assertIsNone(observation["counts"]["candidates_queued"])
        self.assertIsNone(observation["counts"]["last_pipeline_advance_utc"])
        self.assertEqual(sorted(gap["field"] for gap in observation["gaps"]),
                         ["candidate_pool", "last_pipeline_advance"])


class CanonicalIncidents(ObservationHarness):
    def test_unresolved_incident_overlays_watchdog_health_without_rewriting_current(self):
        family(self.root, "fam-blocked", created_minutes_ago=400.0)
        round_spec(self.root, "fam-blocked", "fam-blocked-r1")
        attempt(self.root, "fam-blocked", "fam-blocked-r1", "run1", cs.RUNNING_STAGE, 360.0)
        write(Path(self.root) / ph.INCIDENT_DIRNAME / ph.INCIDENT_FILENAME,
              json.dumps({"schema_version": 1, "incident_id": "inc-1", "kind": "sentinel_ambiguous",
                          "family_id": "fam-blocked", "round_id": "fam-blocked-r1",
                          "run_id": "run1"}) + "\n")
        observation = self.observe()
        self.assertEqual(observation["current"]["state"], "idle")
        self.assertEqual(observation["health"]["status"], "attention")
        self.assertEqual(observation["health"]["active_count"], 1)
        incident = observation["health"]["active"][0]
        for field, value in (("incident_id", "inc-1"), ("kind", "sentinel_ambiguous"),
                             ("family_id", "fam-blocked")):
            self.assertEqual(incident[field], value)
        self.assertIn("no clean terminal", incident["why"])
        self.assertIn("inc-1", observation["health"]["summary"])

    def test_resolved_incident_leaves_existing_health_unchanged(self):
        family(self.root, "fam-done", created_minutes_ago=240.0)
        round_spec(self.root, "fam-done", "fam-done-r1")
        attempt(self.root, "fam-done", "fam-done-r1", "run1", "ARTIFACT_READY", 4.0,
                sentinel="INCOMPLETE")
        verdict(self.root, "fam-done", "fam-done-r1", "TECHNICAL_INCOMPLETE")
        write(Path(self.root) / ph.INCIDENT_DIRNAME / ph.INCIDENT_FILENAME,
              json.dumps({"schema_version": 1, "incident_id": "inc-old", "kind": "sentinel_ambiguous",
                          "family_id": "fam-done", "round_id": "fam-done-r1", "run_id": "run1"}) + "\n")
        observation = self.observe()
        self.assertEqual(observation["current"]["state"], "terminal")
        self.assertEqual(observation["health"], cs.health_payload())

    def test_canonical_incident_adds_to_existing_watchdog_alert(self):
        write(cs.WATCHDOG_STATE, json.dumps({"schema_version": 1,
                                             "active_signatures": {"fixture|terminal_pending": {}}}))
        write(Path(self.root) / ph.INCIDENT_DIRNAME / ph.INCIDENT_FILENAME,
              json.dumps({"schema_version": 1, "incident_id": "inc-no-family",
                          "kind": "mapping_mismatch"}) + "\n")
        health = self.observe()["health"]
        self.assertEqual(health["active_count"], 2)
        self.assertEqual(health["status"], "attention")
        self.assertEqual(health["active"][0]["signature"], "fixture|terminal_pending")
        self.assertEqual(health["active"][1]["incident_id"], "inc-no-family")
        self.assertIn("no family identity", health["active"][1]["why"])

    def test_unreadable_incident_ledger_never_reports_health_ok(self):
        with mock.patch.object(ph, "unresolved_incidents", side_effect=OSError("incident ledger unreadable")):
            observation = self.observe()
        self.assertEqual(observation["health"]["status"], "unknown")
        self.assertIn("canonical_incidents", [item["field"] for item in observation["gaps"]])

    def test_invalid_utf8_incident_ledger_is_an_explicit_gap(self):
        path = Path(self.root) / ph.INCIDENT_DIRNAME / ph.INCIDENT_FILENAME
        path.parent.mkdir(parents=True)
        path.write_bytes(b"\xff\n")
        observation = self.observe()
        self.assertEqual(observation["health"]["status"], "unknown")
        self.assertIn("canonical_incidents", [item["field"] for item in observation["gaps"]])


class ReadOnlyGuarantees(ObservationHarness):
    def test_observation_reads_no_kanban_status(self):
        """A card-bearing family is classified from artifacts alone, and no board call may happen."""
        family(self.root, "fam-term", created_minutes_ago=240.0, kanban_task_id="t_fixture")
        round_spec(self.root, "fam-term", "fam-term-r1")
        attempt(self.root, "fam-term", "fam-term-r1", "run1", "ARTIFACT_READY", 6.0,
                sentinel="INCOMPLETE")
        verdict(self.root, "fam-term", "fam-term-r1", "PASS", age_minutes=2.0,
                kanban_task_id="t_fixture")
        observation = self.observe()          # cs.card_status / cs.board_counts raise if called
        self.assertEqual(observation["current"]["state"], "terminal")
        self.assertEqual(observation["current"]["verdict"], "PASS")
        serialized = json.dumps(observation)
        for forbidden in ("card_status", "board_counts", "kanban_status", "card_id"):
            self.assertNotIn(forbidden, serialized)
        self.assertIn("never reads a Kanban status", observation["scope_note"])

    def test_observation_module_spawns_nothing(self):
        source = (RUNTIME / "runtime_observation.py").read_text()
        for forbidden in ("import subprocess", "subprocess.", "os.system", "popen", "check_output"):
            self.assertNotIn(forbidden, source)

    def test_observation_writes_nothing(self):
        family(self.root, "fam-live", created_minutes_ago=240.0)
        round_spec(self.root, "fam-live", "fam-live-r1")
        attempt(self.root, "fam-live", "fam-live-r1", "run1", cs.RUNNING_STAGE, 1.0)
        pool(self.root, ["fam-live"])
        ledger(self.root, [{"schema": "quant-handoff-log/v1", "ran_at_utc": "2026-09-24T01:00:00Z",
                            "outcome": "advanced", "action": "appended", "dry_run": False,
                            "family_id": "fam-live"}])
        before = tree(self.root)
        self.observe()
        self.observe()
        self.assertEqual(tree(self.root), before)


if __name__ == "__main__":
    unittest.main()
