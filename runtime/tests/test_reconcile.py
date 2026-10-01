#!/usr/bin/env python3
"""Hermetic direct C4 checks; never touch the real board, agent, or results root."""
import argparse
import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

RUNTIME = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME))
import reconcile as r  # noqa: E402
import production_handoff as h  # noqa: E402


class Reconcile(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="qrp-direct-c4-"))
        self.calls = []
        self._launch = r.launch_agent
        self._boot = r.host_boot_id
        r.launch_agent = self.launch
        r.host_boot_id = lambda: "boot-test-current"

    def tearDown(self):
        r.launch_agent = self._launch
        r.host_boot_id = self._boot
        shutil.rmtree(self.root)

    def launch(self, root, family, name, prompt, skills=()):
        self.calls.append((root, family, name, prompt, skills))
        return 1234, None, False

    def fixture(self, family="fam-a", round_id=None, run_id=None, stage=None, terminal=None,
                created="2026-09-13T00:00:00Z", direct=True):
        rnd = round_id or family + "-r1"
        run = run_id or rnd + "-u1"
        attempt = self.root / family / "rounds" / rnd / "attempts" / run
        attempt.mkdir(parents=True, exist_ok=True)
        owner = {"handoff": {"execution": "direct_hermes"}} if direct else {"kanban_task_id": "t_old"}
        (self.root / family / "family.json").write_text(json.dumps(dict(family_id=family, **owner)))
        (attempt.parents[1] / "round-spec.json").write_text(json.dumps(
            {"family_id": family, "round_id": rnd}))
        (attempt / "run-spec.json").write_text(json.dumps(
            {"family_id": family, "round_id": rnd, "run_id": run,
             "created_at_utc": created, "container_id": "qlib-run", "image_id": "img"}))
        if stage:
            (attempt / "state.json").write_text(json.dumps(
                {"family_id": family, "round_id": rnd, "run_id": run, "stage": stage}))
        if terminal:
            data = (attempt / "run-spec.json").read_bytes()
            (attempt / "result.json").write_bytes(data)
            (attempt / terminal).write_text(json.dumps({
                "status": terminal, "family_id": family, "round_id": rnd, "run_id": run,
                "container_id": "qlib-run", "image_id": "img", "host_boot_id": r.host_boot_id(),
                "artifact_manifest": ["result.json"],
                "artifact_checksums": {"result.json": "sha256:" + hashlib.sha256(data).hexdigest()}}))
        return attempt

    def deterministic_reject(self, attempt, disposition=None):
        spec = json.loads((attempt / "run-spec.json").read_text())
        spec.update({
            "selector_version": "cohort-selector-v1",
            "disposition_version": "cohort-disposition-v1",
            "expected": {"cohorts": 2, "expected_case_evaluations": 10},
            "expected_outputs": ["state.json", "result.json", "artifacts/cohort_survivors.json",
                                 "artifacts/cohort_results.json"],
        })
        (attempt / "run-spec.json").write_text(json.dumps(spec))
        (attempt / "artifacts").mkdir(exist_ok=True)
        (attempt / "artifacts" / "cohort_survivors.json").write_text("[]\n")
        (attempt / "artifacts" / "cohort_results.json").write_text(json.dumps([
            {"cohort": "BTCUSDT/1d", "outcome": "CULLED"},
            {"cohort": "ETHUSDT/1d", "status": "CULLED"},
        ]))
        result = {
            "schema_version": 1,
            "family_id": spec["family_id"], "round_id": spec["round_id"], "run_id": spec["run_id"],
            "status": "ARTIFACT_READY", "coverage_complete": True,
            "case_evaluations_total": 10, "expected_case_evaluations": 10,
            "cohorts_evaluated": 2, "cohort_survivor_count": 0, "cohort_survivors": [],
            "verdict_recommendation": "REJECT", "performance_claimable": False,
            "assertions_all_true": True, "selector_version": "cohort-selector-v1",
            "disposition_version": "cohort-disposition-v1",
        }
        if disposition is not None:
            result["disposition"] = disposition
        (attempt / "result.json").write_text(json.dumps(result))
        return attempt

    def publish_done_fixture(self, attempt):
        payload, problem = r._mechanical_reject_payload(r.Result(r.Attempt(
            attempt, "fam-a", "fam-a-r1", require_timestamp=False)), self.root)
        self.assertIsNone(problem)
        checksums = {rel: r.sha256_file(str(attempt / rel)) for rel in payload["manifest"]}
        (attempt / "DONE").write_text(json.dumps({
            "schema_version": 1, "status": "DONE", "family_id": "fam-a",
            "round_id": "fam-a-r1", "run_id": "fam-a-r1-u1",
            "host_boot_id": "boot-test-current", "container_id": "qlib-run",
            "image_id": "img", "verdict_hint": "CANDIDATE_REJECT",
            "artifact_manifest": payload["manifest"], "artifact_checksums": checksums,
        }))
        return payload

    def run_root(self, dry_run=False):
        argv = ["reconcile.py", "--results-root", str(self.root), "--board", "blocked-stale", "--json"]
        if dry_run:
            argv.append("--dry-run")
        original = sys.argv
        out = io.StringIO()
        try:
            sys.argv = argv
            with contextlib.redirect_stdout(out):
                code = r.main()
        finally:
            sys.argv = original
        return code, json.loads(out.getvalue())

    def test_deterministic_zero_survivor_reject_finalizes_without_agent(self):
        attempt = self.deterministic_reject(self.fixture(stage="ARTIFACT_READY"))
        code, report = self.run_root()
        self.assertEqual(code, 0)
        self.assertEqual(report["finalized"], [attempt.name])
        self.assertEqual(report["launched"], [])
        self.assertEqual(self.calls, [])
        self.assertTrue((attempt / "DONE").exists())
        verdict = json.loads((attempt.parents[1] / "verdict.json").read_text())
        self.assertEqual(verdict["verdict"], "REJECT")
        self.assertEqual(verdict["cohort_survivor_count"], 0)
        self.assertTrue(verdict["coverage_complete"])

    def test_deterministic_reject_dry_run_writes_nothing(self):
        attempt = self.deterministic_reject(self.fixture(stage="ARTIFACT_READY"), "NO_SURVIVOR")
        code, report = self.run_root(dry_run=True)
        self.assertEqual(code, 0)
        self.assertEqual(report["would_finalize"], [attempt.name])
        self.assertEqual(report["would_launch"], [])
        self.assertEqual(self.calls, [])
        self.assertFalse((attempt / "DONE").exists())
        self.assertFalse((attempt.parents[1] / "verdict.json").exists())

    def test_survivor_artifact_conflict_never_mechanically_rejects(self):
        attempt = self.deterministic_reject(self.fixture(stage="ARTIFACT_READY"))
        (attempt / "artifacts" / "cohort_survivors.json").write_text(json.dumps([
            {"cohort": "BTCUSDT/1d", "outcome": "SURVIVOR"}
        ]))
        code, report = self.run_root()
        self.assertEqual(code, 0)
        self.assertEqual(report["finalized"], [])
        self.assertEqual(report["launched"], [attempt.name])
        self.assertIn("cohort_survivors artifact contradicts",
                      report["results"][0]["detail"]["mechanical_closeout_not_applicable"])
        self.assertFalse((attempt / "DONE").exists())

    def test_cohort_results_survivor_conflict_never_mechanically_rejects(self):
        attempt = self.deterministic_reject(self.fixture(stage="ARTIFACT_READY"))
        rows = json.loads((attempt / "artifacts" / "cohort_results.json").read_text())
        rows[0]["outcome"] = "SURVIVOR"
        (attempt / "artifacts" / "cohort_results.json").write_text(json.dumps(rows))
        code, report = self.run_root()
        self.assertEqual(code, 0)
        self.assertEqual(report["finalized"], [])
        self.assertEqual(report["launched"], [attempt.name])
        self.assertIn("cohort_results artifact reports survivors",
                      report["results"][0]["detail"]["mechanical_closeout_not_applicable"])
        self.assertFalse((attempt / "DONE").exists())

    def test_malformed_cohort_result_row_never_mechanically_rejects(self):
        attempt = self.deterministic_reject(self.fixture(stage="ARTIFACT_READY"))
        rows = json.loads((attempt / "artifacts" / "cohort_results.json").read_text())
        rows[0] = {}
        (attempt / "artifacts" / "cohort_results.json").write_text(json.dumps(rows))
        code, report = self.run_root()
        self.assertEqual(code, 0)
        self.assertEqual(report["finalized"], [])
        self.assertEqual(report["launched"], [attempt.name])
        self.assertIn("invalid/duplicate cohort",
                      report["results"][0]["detail"]["mechanical_closeout_not_applicable"])
        self.assertFalse((attempt / "DONE").exists())

    def test_unknown_cohort_outcome_never_mechanically_rejects(self):
        attempt = self.deterministic_reject(self.fixture(stage="ARTIFACT_READY"))
        rows = json.loads((attempt / "artifacts" / "cohort_results.json").read_text())
        rows[0]["outcome"] = "UNKNOWN"
        (attempt / "artifacts" / "cohort_results.json").write_text(json.dumps(rows))
        code, report = self.run_root()
        self.assertEqual(code, 0)
        self.assertEqual(report["finalized"], [])
        self.assertEqual(report["launched"], [attempt.name])
        self.assertIn("not explicitly CULLED",
                      report["results"][0]["detail"]["mechanical_closeout_not_applicable"])
        self.assertFalse((attempt / "DONE").exists())

    def test_symlinked_family_attempt_tree_never_mechanically_closes(self):
        attempt = self.deterministic_reject(self.fixture(stage="ARTIFACT_READY"))
        outside = Path(tempfile.mkdtemp(prefix="qrp-family-outside-"))
        external_family = outside / "fam-a"
        try:
            shutil.move(str(self.root / "fam-a"), str(external_family))
            os.symlink(str(external_family), str(self.root / "fam-a"))
            external_attempt = external_family / "rounds" / "fam-a-r1" / "attempts" / attempt.name
            code, report = self.run_root()
            self.assertEqual(code, 0)
            self.assertEqual(report["finalized"], [])
            self.assertEqual(report["launched"], [attempt.name])
            self.assertIn("attempt path contains symlink",
                          report["results"][0]["detail"]["mechanical_closeout_not_applicable"])
            self.assertFalse((external_attempt / "DONE").exists())
            self.assertFalse((external_attempt.parents[1] / "verdict.json").exists())
        finally:
            if (self.root / "fam-a").is_symlink():
                (self.root / "fam-a").unlink()
            shutil.rmtree(outside)

    def test_parent_symlink_expected_output_never_mechanically_closes(self):
        attempt = self.deterministic_reject(self.fixture(stage="ARTIFACT_READY"))
        outside = Path(tempfile.mkdtemp(prefix="qrp-outside-"))
        try:
            shutil.rmtree(attempt / "artifacts")
            (outside / "cohort_survivors.json").write_text("[]\n")
            os.symlink(str(outside), str(attempt / "artifacts"))
            code, report = self.run_root()
            self.assertEqual(code, 0)
            self.assertEqual(report["finalized"], [])
            self.assertEqual(report["launched"], [attempt.name])
            self.assertIn("symlink", report["results"][0]["detail"]["mechanical_closeout_not_applicable"])
            self.assertFalse((attempt / "DONE").exists())
            self.assertFalse((attempt.parents[1] / "verdict.json").exists())
        finally:
            shutil.rmtree(outside)

    def test_exclusive_json_interrupted_stage_never_reserves_final_verdict_path(self):
        path = self.root / "verdict.json"
        with patch.object(r.os, "fsync", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                r._exclusive_json(path, {"verdict": "REJECT"})
        self.assertFalse(path.exists())
        self.assertEqual(list(self.root.glob(".verdict.json.tmp-*")), [])

    def test_exclusive_json_never_clobbers_existing_verdict(self):
        path = self.root / "verdict.json"
        path.write_text('{"verdict":"PASS"}\n')
        before = path.read_bytes()
        with self.assertRaises(FileExistsError):
            r._exclusive_json(path, {"verdict": "REJECT"})
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(self.root.glob(".verdict.json.tmp-*")), [])

    def test_terminal_publish_timeout_is_fail_closed_incident_not_traceback(self):
        attempt = self.deterministic_reject(self.fixture(stage="ARTIFACT_READY"))
        with patch.object(r.subprocess, "run", side_effect=r.subprocess.TimeoutExpired(
                ["terminal_evidence.py"], 60)):
            code, report = self.run_root()
        self.assertEqual(code, 3)
        self.assertEqual(report["incidents"], 1)
        self.assertEqual(report["results"][0]["action"], "incident")
        self.assertEqual(report["results"][0]["reason"], "mechanical_closeout_failed")
        self.assertIn("TimeoutExpired", report["results"][0]["detail"]["terminal_publish_error"])
        self.assertFalse((attempt / "DONE").exists())
        self.assertFalse((attempt.parents[1] / "verdict.json").exists())
        self.assertEqual(self.calls, [])

    def test_concurrent_terminal_publish_is_idempotent_not_false_incident(self):
        attempt = self.deterministic_reject(self.fixture(stage="ARTIFACT_READY"))

        def publish_elsewhere(*_args, **_kwargs):
            self.publish_done_fixture(attempt)
            return r.subprocess.CompletedProcess(["terminal_evidence.py"], 1, "", "already exists")

        with patch.object(r.subprocess, "run", side_effect=publish_elsewhere):
            code, report = self.run_root()
        self.assertEqual(code, 0)
        self.assertEqual(report["incidents"], 0)
        self.assertEqual(report["results"][0]["action"], "running")
        self.assertIn("published concurrently", report["results"][0]["reason"])
        self.assertTrue((attempt / "DONE").exists())
        self.assertFalse((attempt.parents[1] / "verdict.json").exists())
        self.assertEqual(self.calls, [])

    def test_mechanical_closeout_holds_family_lease_through_verdict_publish(self):
        attempt = self.deterministic_reject(self.fixture(stage="ARTIFACT_READY"))
        original_exclusive = r._exclusive_json
        lock_observations = []

        def exclusive_while_locked(path, doc):
            probe = h._lock(self.root / "fam-a" / h.AGENT_LOCK)
            lock_observations.append(probe)
            if probe is not None:
                os.close(probe)
            return original_exclusive(path, doc)

        with patch.object(r, "_exclusive_json", side_effect=exclusive_while_locked):
            code, report = self.run_root()
        self.assertEqual(code, 0)
        self.assertEqual(report["finalized"], [attempt.name])
        self.assertEqual(lock_observations, [None])
        self.assertTrue((attempt / "DONE").exists())
        self.assertTrue((attempt.parents[1] / "verdict.json").exists())

    def test_done_before_verdict_window_cannot_launch_agent_while_completion_lease_held(self):
        attempt = self.deterministic_reject(self.fixture(stage="ARTIFACT_READY"))
        self.publish_done_fixture(attempt)
        lease_fd = h._lock(self.root / "fam-a" / h.AGENT_LOCK)
        self.assertIsNotNone(lease_fd)
        previous_launch = r.launch_agent
        r.launch_agent = h.launch_agent
        try:
            code, report = self.run_root()
        finally:
            r.launch_agent = previous_launch
            os.close(lease_fd)
        self.assertEqual(code, 0)
        self.assertEqual(report["incidents"], 0)
        self.assertEqual(report["results"][0]["action"], "running")
        self.assertIn("already owns this family", report["results"][0]["reason"])
        self.assertFalse((attempt.parents[1] / "verdict.json").exists())
        self.assertEqual(self.calls, [])

    def test_artifact_ready_with_pass_recommendation_still_wakes_agent(self):
        attempt = self.deterministic_reject(self.fixture(stage="ARTIFACT_READY"))
        result = json.loads((attempt / "result.json").read_text())
        result.update({"verdict_recommendation": "PASS", "cohort_survivor_count": 1,
                       "cohort_survivors": ["BTCUSDT/1d"]})
        (attempt / "result.json").write_text(json.dumps(result))
        code, report = self.run_root()
        self.assertEqual(code, 0)
        self.assertEqual(report["launched"], [attempt.name])
        self.assertEqual(report["finalized"], [])
        self.assertEqual(len(self.calls), 1)
        self.assertFalse((attempt / "DONE").exists())

    def test_compute_finished_wakes_default_without_a_card(self):
        self.fixture(stage="ARTIFACT_READY")
        code, report = self.run_root()
        self.assertEqual(code, 0)
        self.assertEqual(report["launched"], ["fam-a-r1-u1"])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(
            self.calls[0][2],
            "disposition-v%d-fam-a-r1-fam-a-r1-u1.md" % r.DISPOSITION_PROMPT_VERSION)
        self.assertIn("No Kanban", self.calls[0][3])
        self.assertIn("agent-task.md is OPTIONAL provenance only", self.calls[0][3])
        self.assertIn("source venue/quote/named-symbol mismatch is NOT sufficient", self.calls[0][3])
        self.assertIn("NEXT round of the SAME family", self.calls[0][3])
        self.assertIn("edit only the current family runner", self.calls[0][3])
        self.assertIn("do NOT modify another strategy runner", self.calls[0][3])
        self.assertFalse(any("task_id" in row for row in report["results"]))

    def test_failed_script_wakes_host_disposition_not_a_fake_verdict(self):
        attempt = self.fixture(stage="FAILED_SCRIPT")
        self.assertEqual(self.run_root()[1]["launched"], [attempt.name])
        self.assertFalse((attempt / "FAILED").exists())
        self.assertFalse((attempt.parents[1] / "verdict.json").exists())

    def test_dry_run_does_not_launch_or_write(self):
        self.fixture(stage="ARTIFACT_READY")
        code, report = self.run_root(dry_run=True)
        self.assertEqual(code, 0)
        self.assertEqual(report["would_launch"], ["fam-a-r1-u1"])
        self.assertEqual(self.calls, [])
        self.assertFalse((self.root / "_incidents").exists())

    def test_running_stage_never_wakes(self):
        self.fixture(stage="RUNNING_QLIB")
        code, report = self.run_root()
        self.assertEqual(code, 0)
        self.assertEqual(report["results"][0]["action"], "orphan_candidate")
        self.assertEqual(self.calls, [])

    def test_terminal_wakes_only_after_manifest_validation(self):
        self.fixture(terminal="DONE")
        self.assertEqual(self.run_root()[1]["launched"], ["fam-a-r1-u1"])
        self.assertEqual(len(self.calls), 1)
        self.assertFalse((self.root / "_incidents").exists())

    def test_reboot_terminal_disposition_releases_prepared_one_shot_hold(self):
        with patch.object(r, "host_boot_id", return_value="boot-before-reboot"):
            attempt = self.fixture(terminal="DONE")
        family_path = self.root / "fam-a" / "family.json"
        family = json.loads(family_path.read_text())
        family["handoff"]["prepared_execution"] = {
            "round_id": "fam-a-r1", "run_id": attempt.name}
        family_path.write_text(json.dumps(family))
        pool = self.root / h.HANDOFF_DIRNAME / h.POOL_FILENAME
        pool.parent.mkdir()
        pool.write_text(json.dumps({"schema_version": 1, "candidates": []}))
        old = time.time() - (h.ACTIVE_WINDOW_MINUTES + 10) * 60
        for path in [attempt] + list(attempt.rglob("*")):
            os.utime(path, (old, old))
        frozen = {path: path.read_bytes() for path in attempt.rglob("*") if path.is_file()}
        args = argparse.Namespace(results_root=str(self.root), pool=str(pool),
                                  require_prepared_execution=True, dry_run=False)
        with patch.object(h, "sh", side_effect=AssertionError("no Qlib relaunch")), \
                patch.object(h, "launch_agent", side_effect=AssertionError("no C3 agent relaunch")), \
                patch.object(r, "host_boot_id", return_value="boot-after-reboot"):
            self.assertFalse(h.runtime_state(self.root, "fam-a", family)["in_flight"])
            held = h._round_once(args)
            self.assertEqual(held.detail["active_family"], "fam-a")
            self.assertIn("active runtime evidence", held.reason)
            code, report = self.run_root()
            self.assertEqual(code, 0)
            self.assertEqual(report["launched"], [attempt.name])
            self.assertEqual(len(self.calls), 1)
            self.assertEqual(report["results"][0]["detail"]["boot_recovery"], {
                "sentinel_host_boot_id": "boot-before-reboot",
                "current_host_boot_id": "boot-after-reboot"})
            self.assertIn("do not republish", self.calls[0][3])
            self.assertFalse((attempt.parents[1] / "verdict.json").exists())
            self.assertEqual(h._round_once(args).detail["active_family"], "fam-a")
            # The disposition agent, not C4, re-validates artifacts and concludes this round.
            sentinel = json.loads((attempt / "DONE").read_text())
            self.assertEqual(r.mapping_problems(r.Result(r.Attempt(attempt, "fam-a", "fam-a-r1")),
                                               self.root, sentinel), [])
            for rel in sentinel["artifact_manifest"]:
                self.assertEqual(r.sha256_file(str(attempt / rel)), sentinel["artifact_checksums"][rel])
            (attempt.parents[1] / "verdict.json").write_text(json.dumps(
                {"family_id": "fam-a", "round_id": "fam-a-r1", "verdict": "REJECT"}))
            self.assertEqual(self.run_root()[1]["results"][0]["action"], "consumed")
            self.assertEqual(len(self.calls), 1)
            released = h._round_once(args)
            self.assertTrue(released.reason.startswith("no_eligible_candidate:"), released.reason)
            self.assertEqual(h.unresolved_incidents(self.root, {"fam-a": family}), [])
        self.assertEqual({path: path.read_bytes() for path in frozen}, frozen)
        incidents = [json.loads(line) for line in (self.root / r.INCIDENT_FILE).read_text().splitlines()]
        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0]["kind"], "stale_sentinel")
        self.assertEqual(incidents[0]["host_boot_id"], "boot-after-reboot")
        self.assertEqual(incidents[0]["evidence_paths"], [str(attempt / "DONE")])

    def test_reboot_terminal_dry_run_and_repeated_wake_preserve_evidence(self):
        frozen = {}
        for terminal in r.TERMINALS:
            with self.subTest(terminal=terminal), \
                    patch.object(r, "host_boot_id", return_value="previous-boot"):
                attempt = self.fixture(family="fam-" + terminal.lower(), terminal=terminal)
                frozen[attempt / terminal] = (attempt / terminal).read_bytes()
            with patch.object(r, "host_boot_id", return_value="current-boot"):
                code, report = self.run_root(dry_run=True)
                self.assertEqual(code, 0)
                self.assertIn(attempt.name, report["would_launch"])
                self.assertEqual(self.calls, [])
                self.assertFalse((self.root / "_incidents").exists())
        with patch.object(r, "host_boot_id", return_value="current-boot"):
            for _ in range(2):
                self.assertEqual(self.run_root()[0], 0)
        self.assertEqual(len((self.root / r.INCIDENT_FILE).read_text().splitlines()), len(r.TERMINALS))
        self.assertEqual({path: path.read_bytes() for path in frozen}, frozen)

    def test_reboot_cannot_bypass_terminal_validation(self):
        for kind in ("checksum_mismatch", "mapping_mismatch", "multiple_terminal", "sentinel_ambiguous"):
            with self.subTest(kind=kind), patch.object(r, "host_boot_id", return_value="old-boot"):
                attempt = self.fixture(family="fam-" + kind.replace("_", "-"), terminal="DONE")
            if kind == "checksum_mismatch":
                (attempt / "result.json").write_text("tampered")
            elif kind == "multiple_terminal":
                (attempt / "FAILED").write_bytes((attempt / "DONE").read_bytes())
            else:
                sentinel = json.loads((attempt / "DONE").read_text())
                sentinel["run_id" if kind == "mapping_mismatch" else "status"] = "foreign"
                (attempt / "DONE").write_text(json.dumps(sentinel))
            with patch.object(r, "host_boot_id", return_value="new-boot"):
                rec = r.Attempt(attempt, attempt.parents[3].name, attempt.parents[1].name)
                result = r.handle(r.Result(rec), self.root, False, "reconciler")
                self.assertEqual((result.action, result.reason), ("incident", kind))
                self.assertNotIn("boot_recovery", result.detail)
        self.assertEqual(self.calls, [])

    def test_missing_or_invalid_boot_identity_is_not_reboot_recovery(self):
        for boot in (None, "", "   ", 123, [], "boot-unknown"):
            with self.subTest(boot=boot):
                attempt = self.fixture(terminal="DONE")
                sentinel = json.loads((attempt / "DONE").read_text())
                sentinel["host_boot_id"] = boot
                (attempt / "DONE").write_text(json.dumps(sentinel))
                code, report = self.run_root()
                self.assertEqual(code, 3)
                self.assertEqual(report["results"][0]["reason"], "stale_sentinel")
                self.assertNotIn("boot_recovery", report["results"][0].get("detail", {}))
        self.assertEqual(self.calls, [])

    def test_unavailable_current_boot_is_not_reboot_recovery(self):
        self.fixture(terminal="DONE")
        with patch.object(r, "host_boot_id", return_value="boot-unknown"):
            code, report = self.run_root()
        self.assertEqual(code, 3)
        self.assertEqual(report["results"][0]["reason"], "stale_sentinel")
        self.assertEqual(self.calls, [])

    def test_both_unavailable_boot_identities_fail_closed(self):
        with patch.object(r, "host_boot_id", return_value="boot-unknown"):
            self.fixture(terminal="DONE")
            code, report = self.run_root()
        self.assertEqual(code, 3)
        self.assertEqual(report["results"][0]["reason"], "stale_sentinel")
        self.assertNotIn("boot_recovery", report["results"][0].get("detail", {}))
        self.assertEqual(self.calls, [])

    def test_reboot_recovery_uses_existing_lease_and_launch_failure_incident(self):
        with patch.object(r, "host_boot_id", return_value="old-boot"):
            self.fixture(terminal="DONE")
        with patch.object(r, "host_boot_id", return_value="new-boot"):
            with patch.object(r, "launch_agent", return_value=(None, None, True)) as launch:
                code, report = self.run_root()
                self.assertEqual(code, 0)
                self.assertEqual(report["results"][0]["action"], "running")
                launch.assert_called_once()
            with patch.object(r, "launch_agent", return_value=(None, "launch failed", False)):
                code, report = self.run_root()
                self.assertEqual(code, 3)
                self.assertIn("disposition_launch_failed", report["results"][0]["reason"])
        incidents = [json.loads(line) for line in (self.root / r.INCIDENT_FILE).read_text().splitlines()]
        self.assertEqual([row["kind"] for row in incidents],
                         ["stale_sentinel", "disposition_launch_failed"])

    def test_tampered_terminal_fails_closed_once(self):
        attempt = self.fixture(terminal="DONE")
        (attempt / "result.json").write_text("tampered")
        for _ in range(2):
            code, report = self.run_root()
            self.assertEqual(code, 3)
            self.assertEqual(report["results"][0]["reason"], "checksum_mismatch")
        self.assertEqual(self.calls, [])
        self.assertEqual(len((self.root / r.INCIDENT_FILE).read_text().splitlines()), 1)

    def test_direct_attempt_refuses_card_identity(self):
        attempt = self.fixture(stage="ARTIFACT_READY")
        spec = json.loads((attempt / "run-spec.json").read_text())
        spec["task_id"] = "t_foreign"
        (attempt / "run-spec.json").write_text(json.dumps(spec))
        code, report = self.run_root()
        self.assertEqual(code, 3)
        self.assertEqual(report["results"][0]["reason"], "attempt_selection_ambiguous")
        self.assertEqual(self.calls, [])

    def test_wrong_state_identity_fails_closed(self):
        attempt = self.fixture(stage="ARTIFACT_READY")
        state = json.loads((attempt / "state.json").read_text())
        state["run_id"] = "foreign"
        (attempt / "state.json").write_text(json.dumps(state))
        code, report = self.run_root()
        self.assertEqual(code, 3)
        self.assertEqual(report["results"][0]["reason"], "mapping_mismatch")
        self.assertEqual(self.calls, [])

    def test_historical_family_is_inert_and_board_unconsulted(self):
        self.fixture(direct=False, stage="ARTIFACT_READY")
        code, report = self.run_root()
        self.assertEqual(code, 0)
        self.assertEqual(report["attempts_scanned"], 0)
        self.assertEqual(self.calls, [])

    def test_single_attempt_does_not_require_created_at_for_selection(self):
        self.fixture(stage="ARTIFACT_READY", created=None)
        code, report = self.run_root()
        self.assertEqual(code, 0)
        self.assertEqual(report["launched"], ["fam-a-r1-u1"])
        self.assertNotEqual(report["results"][0]["reason"], "attempt_selection_ambiguous")

    def test_multiple_attempts_still_require_ordering_timestamp(self):
        self.fixture(run_id="fam-a-r1-u1", terminal="DONE")
        self.fixture(run_id="fam-a-r1-u2", stage="ARTIFACT_READY", created=None)
        code, report = self.run_root()
        self.assertEqual(code, 3)
        self.assertEqual(report["results"][0]["reason"], "attempt_selection_ambiguous")
        self.assertIn("created_at_utc missing/unparsable", report["results"][0]["detail"]["ambiguity"])
        self.assertEqual(self.calls, [])

    def test_superseded_terminal_never_wakes_against_running_current(self):
        self.fixture(run_id="fam-a-r1-u1", terminal="DONE")
        self.fixture(run_id="fam-a-r1-u2", stage="RUNNING_QLIB",
                     created="2026-09-13T00:10:00Z")
        code, report = self.run_root()
        self.assertEqual(code, 0)
        self.assertEqual([x["action"] for x in report["results"]],
                         ["superseded", "orphan_candidate"])
        self.assertEqual(self.calls, [])

    def test_u10_beats_u9_on_timestamp_tie(self):
        self.fixture(run_id="fam-a-r1-u9", terminal="FAILED")
        self.fixture(run_id="fam-a-r1-u10", stage="ARTIFACT_READY")
        code, report = self.run_root()
        self.assertEqual(code, 0)
        self.assertEqual(report["launched"], ["fam-a-r1-u10"])

    def test_ambiguous_new_attempt_never_falls_back(self):
        self.fixture(terminal="DONE")
        second = self.fixture(run_id="fam-a-r1-u2", stage="ARTIFACT_READY")
        (second / "run-spec.json").unlink()
        code, report = self.run_root()
        self.assertEqual(code, 3)
        self.assertEqual(report["results"][0]["reason"], "attempt_selection_ambiguous")
        self.assertEqual(self.calls, [])

    def test_earlier_round_verdict_does_not_suppress_later_disposition(self):
        self.fixture(stage="ARTIFACT_READY", round_id="fam-a-r2", run_id="fam-a-r2-u1")
        rnd1 = self.root / "fam-a" / "rounds" / "fam-a-r1"
        rnd1.mkdir()
        (rnd1 / "verdict.json").write_text(json.dumps(
            {"family_id": "fam-a", "round_id": "fam-a-r1", "verdict": "PASS"}))
        self.assertEqual(self.run_root()[1]["launched"], ["fam-a-r2-u1"])

    def test_own_round_verdict_consumes_without_waking(self):
        attempt = self.fixture(terminal="DONE")
        (attempt.parents[1] / "verdict.json").write_text(json.dumps(
            {"family_id": "fam-a", "round_id": "fam-a-r1", "verdict": "REJECT"}))
        code, report = self.run_root()
        self.assertEqual(code, 0)
        self.assertEqual(report["results"][0]["action"], "consumed")
        self.assertEqual(self.calls, [])

    def test_foreign_round_verdict_cannot_suppress_wake(self):
        attempt = self.fixture(stage="ARTIFACT_READY")
        (attempt.parents[1] / "verdict.json").write_text(json.dumps(
            {"family_id": "fam-a", "round_id": "foreign-r1", "verdict": "PASS"}))
        self.assertEqual(self.run_root()[1]["launched"], ["fam-a-r1-u1"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
