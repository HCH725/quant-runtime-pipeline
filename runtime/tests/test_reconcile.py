#!/usr/bin/env python3
"""Logic-level checks for runtime/reconcile.py (contract 9.4 / 11 / 12.2 / 12.3 / 12.6).

These are *simulation* checks: the kernel read-back and the `unblock` call are injected
(`reconcile.card_status` / `reconcile.sh`), so the release decision state machine can be exercised
without mutating a real board. They assert the fail-closed property that matters: `unblock` is issued
**only** when every contract 9.4 check passed on an *unconsumed* terminal sentinel.

Run: python3 runtime/tests/test_reconcile.py     (stdlib unittest, no dependencies)
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME))
import reconcile  # noqa: E402

BOARD = "quant-strategy-research"
TASK = "t_SMOKE"


class Harness(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="qrp-reconcile-test-")
        self.calls = []
        self.fake_status = "scheduled"
        self.fake_unblock_rc = 0
        self._real_card_status = reconcile.card_status
        self._real_sh = reconcile.sh

    def tearDown(self):
        reconcile.card_status = self._real_card_status
        reconcile.sh = self._real_sh
        shutil.rmtree(self.root, ignore_errors=True)

    # --- fixture helpers -------------------------------------------------
    def fixture(self, family="fam-a", terminals=("DONE",), status_field=None, tamper=False,
                boot=None, task=TASK, sentinel_task=None):
        run_id = "%s-r1-u1" % family
        attempt = Path(self.root) / family / "rounds" / ("%s-r1" % family) / "attempts" / run_id
        attempt.mkdir(parents=True)
        sentinel_task = sentinel_task or task
        (Path(self.root) / family / "family.json").write_text(json.dumps(
            {"schema_version": 1, "family_id": family, "kanban_task_id": task, "kanban_board": BOARD}))
        (Path(self.root) / family / "rounds" / ("%s-r1" % family) / "round-spec.json").write_text(json.dumps(
            {"schema_version": 1, "family_id": family, "round_id": "%s-r1" % family, "kanban_task_id": task,
             "kanban_board": BOARD}))
        payload = (Path(self.root) / family / "family.json").read_text()
        (attempt / "result.json").write_text(payload)
        checksum = "sha256:" + hashlib.sha256(payload.encode()).hexdigest()
        if tamper:
            (attempt / "result.json").write_text(payload + "tampered")
        (attempt / "run-spec.json").write_text(json.dumps(
            {"schema_version": 1, "family_id": family, "round_id": "%s-r1" % family, "run_id": run_id,
             "task_id": task, "kanban_board": BOARD}))
        sentinel = {
            "schema_version": 1, "status": status_field or terminals[0], "family_id": family,
            "round_id": "%s-r1" % family, "run_id": run_id, "task_id": sentinel_task, "kanban_board": BOARD,
            "created_at_utc": "2026-09-13T00:00:00Z", "host_boot_id": boot or reconcile.host_boot_id(),
            "artifact_manifest": ["result.json"], "artifact_checksums": {"result.json": checksum},
            "verdict_hint": "CANDIDATE_PASS",
        }
        for term in terminals:
            (attempt / term).write_text(json.dumps(dict(sentinel, status=status_field or term)))
        return attempt, run_id

    def install_fakes(self):
        def fake_card_status(board, task_id):
            self.calls.append(("card_status", board, task_id))
            # before the unblock call the card is `scheduled`; after it the kernel read-back says `ready`
            if self.unblock_calls():
                return "ready", "fake read-back after unblock"
            return self.fake_status, "fake read-back"

        def fake_sh(cmd, timeout=120):
            self.calls.append(("sh", tuple(cmd)))
            if cmd[:3] == ["hermes", "kanban", "--board"] and len(cmd) > 4 and cmd[4] == "unblock":
                return self.fake_unblock_rc, "", "" if self.fake_unblock_rc == 0 else "kernel refused"
            return 0, "", ""

        reconcile.card_status = fake_card_status
        reconcile.sh = fake_sh

    def run_one(self, attempt, dry_run=False, action="reconcile"):
        rel = attempt.relative_to(Path(self.root)).parts
        res = reconcile.Result(attempt, rel[0], rel[2], rel[4])
        report = []

        def detector_trap(*a, **kw):
            report.append("incident")

        res = reconcile.handle(res, self.root, BOARD, dry_run, "reconciler")
        return res

    def unblock_calls(self):
        return [c for c in self.calls if c[0] == "sh" and "unblock" in c[1]]

    # --- cases -----------------------------------------------------------
    def test_verified_terminal_releases_scheduled_card(self):
        self.install_fakes()
        attempt, _ = self.fixture()
        res = self.run_one(attempt)
        self.assertEqual(res.action, "unblocked")
        self.assertEqual(res.status_before, "scheduled")
        self.assertEqual(res.status_after, "ready")
        self.assertEqual(len(self.unblock_calls()), 1)

    def test_dry_run_never_mutates(self):
        self.install_fakes()
        attempt, _ = self.fixture()
        res = self.run_one(attempt, dry_run=True)
        self.assertEqual(res.action, "would_unblock")
        self.assertEqual(self.unblock_calls(), [])

    def test_consumed_sentinel_is_noop(self):
        self.install_fakes()
        self.fake_status = "done"
        attempt, _ = self.fixture()
        res = self.run_one(attempt)
        self.assertEqual(res.action, "consumed")
        self.assertEqual(self.unblock_calls(), [])

    def test_multiple_terminal_is_fail_closed(self):
        self.install_fakes()
        attempt, _ = self.fixture(terminals=("DONE", "FAILED"))
        res = self.run_one(attempt)
        self.assertEqual(res.action, "incident")
        self.assertEqual(res.reason, "multiple_terminal")
        self.assertEqual(self.unblock_calls(), [])
        lines = (Path(self.root) / "_incidents" / "reconciliation_incident.jsonl").read_text().strip().splitlines()
        self.assertEqual(json.loads(lines[0])["kind"], "multiple_terminal")
        self.assertEqual(json.loads(lines[0])["detector"], "reconciler")

    def test_checksum_mismatch_is_fail_closed(self):
        self.install_fakes()
        attempt, _ = self.fixture(tamper=True)
        res = self.run_one(attempt)
        self.assertEqual(res.action, "incident")
        self.assertEqual(res.reason, "checksum_mismatch")
        self.assertEqual(self.unblock_calls(), [])

    def test_sentinel_status_mismatch_is_fail_closed(self):
        self.install_fakes()
        attempt, _ = self.fixture(terminals=("DONE",), status_field="FAILED")
        res = self.run_one(attempt)
        self.assertEqual(res.action, "incident")
        self.assertEqual(res.reason, "sentinel_ambiguous")
        self.assertEqual(self.unblock_calls(), [])

    def test_stale_boot_is_fail_closed(self):
        self.install_fakes()
        attempt, _ = self.fixture(boot="boot-0")
        res = self.run_one(attempt)
        self.assertEqual(res.action, "incident")
        self.assertEqual(res.reason, "stale_sentinel")
        self.assertEqual(self.unblock_calls(), [])

    def test_mapping_mismatch_is_fail_closed(self):
        self.install_fakes()
        attempt, _ = self.fixture(sentinel_task="t_OTHER")
        res = self.run_one(attempt)
        self.assertEqual(res.action, "incident")
        self.assertEqual(res.reason, "mapping_mismatch")
        self.assertEqual(self.unblock_calls(), [])

    def test_unblock_failure_is_reported(self):
        self.install_fakes()
        self.fake_unblock_rc = 1
        attempt, _ = self.fixture()
        res = self.run_one(attempt)
        self.assertEqual(res.action, "incident")
        self.assertEqual(res.reason, "invariant_break")

    def test_release_without_readback_confirmation_is_reported(self):
        self.install_fakes()
        attempt, _ = self.fixture()
        statuses = iter(["scheduled", "scheduled"])  # kernel did not move the card

        def fake_card_status(board, task_id):
            return next(statuses), "fake read-back"

        reconcile.card_status = fake_card_status
        res = self.run_one(attempt)
        self.assertEqual(res.action, "incident")
        self.assertEqual(res.reason, "invariant_break")

    def test_orphan_without_terminal_is_report_only(self):
        self.install_fakes()
        attempt, run_id = self.fixture(terminals=(), status_field="DONE")
        for term in ("DONE", "FAILED", "INCOMPLETE"):
            p = attempt / term
            if p.exists():
                p.unlink()
        (attempt / "state.json").write_text(json.dumps({"stage": "RUNNING_QLIB"}))
        res = self.run_one(attempt)
        self.assertEqual(res.action, "orphan_candidate")
        self.assertEqual(res.task_id, TASK)
        self.assertEqual(self.unblock_calls(), [])

    def test_scanner_ignores_incidents_dir_and_foreign_layout(self):
        self.install_fakes()
        os.makedirs(Path(self.root) / "_incidents")
        (Path(self.root) / "_incidents" / "reconciliation_incident.jsonl").write_text("{}\n")
        os.makedirs(Path(self.root) / "legacy-smoke-name")   # no rounds/attempts layout (old naming)
        found = reconcile.discover_attempts(self.root)
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
