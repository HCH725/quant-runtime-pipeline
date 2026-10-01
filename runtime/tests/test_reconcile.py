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
        r.launch_agent = self.launch

    def tearDown(self):
        r.launch_agent = self._launch
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
