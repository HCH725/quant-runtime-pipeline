#!/usr/bin/env python3
"""Logic-level checks for runtime/reconcile.py (contract 9.4 / 11 / 12.2 / 12.3 / 12.6).

These are *simulation* checks: the kernel read-back and the `unblock` call are injected
(`reconcile.card_status` / `reconcile.sh`), so the release decision state machine can be exercised
without mutating a real board. They assert the fail-closed property that matters: `unblock` is issued
**only** when every contract 9.4 check passed on an *unconsumed* terminal sentinel of the round's
authoritative current attempt (v1.7.1: an older attempt whose terminal was superseded by a newer
valid attempt of the same round is a descriptive no-op, and an unorderable round fails closed).

Run: python3 runtime/tests/test_reconcile.py     (stdlib unittest, no dependencies)
"""
import contextlib
import hashlib
import io
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
                boot=None, task=TASK, sentinel_task=None, container="qlib-run",
                family_over=None, round_over=None, run_over=None, sentinel_over=None):
        run_id = "%s-r1-u1" % family
        round_id = "%s-r1" % family
        attempt = Path(self.root) / family / "rounds" / round_id / "attempts" / run_id
        attempt.mkdir(parents=True)
        sentinel_task = sentinel_task or task
        family_doc = {"schema_version": 1, "family_id": family, "kanban_task_id": task, "kanban_board": BOARD}
        round_doc = {"schema_version": 1, "family_id": family, "round_id": round_id,
                     "kanban_task_id": task, "kanban_board": BOARD}
        run_doc = {"schema_version": 1, "family_id": family, "round_id": round_id, "run_id": run_id,
                   "task_id": task, "kanban_board": BOARD, "container_id": container,
                   "image_id": "qlib:0.9.7-arm64"}
        for doc, over in ((family_doc, family_over), (round_doc, round_over), (run_doc, run_over)):
            doc.update(over or {})
        (Path(self.root) / family / "family.json").write_text(json.dumps(family_doc))
        (Path(self.root) / family / "rounds" / round_id / "round-spec.json").write_text(json.dumps(round_doc))
        payload = (Path(self.root) / family / "family.json").read_text()
        (attempt / "result.json").write_text(payload)
        checksum = "sha256:" + hashlib.sha256(payload.encode()).hexdigest()
        if tamper:
            (attempt / "result.json").write_text(payload + "tampered")
        (attempt / "run-spec.json").write_text(json.dumps(run_doc))
        sentinel = {
            "schema_version": 1, "status": status_field or terminals[0], "family_id": family,
            "round_id": round_id, "run_id": run_id, "task_id": sentinel_task, "kanban_board": BOARD,
            "created_at_utc": "2026-09-13T00:00:00Z", "host_boot_id": boot or reconcile.host_boot_id(),
            "container_id": container, "image_id": "qlib:0.9.7-arm64",
            "artifact_manifest": ["result.json"], "artifact_checksums": {"result.json": checksum},
            "verdict_hint": "CANDIDATE_PASS",
        }
        sentinel.update(sentinel_over or {})
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

    def incident_lines(self):
        path = Path(self.root) / "_incidents" / "reconciliation_incident.jsonl"
        return path.read_text().strip().splitlines() if path.exists() else []

    def assert_no_incident_no_comment(self):
        self.assertEqual(self.incident_lines(), [], "consumed sentinel must not write an incident")
        comments = [c for c in self.calls if c[0] == "sh" and "comment" in c[1]]
        self.assertEqual(comments, [], "consumed sentinel must not comment on the card")

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
        # consumption read + pre-unblock read + post-unblock read: kernel never moves the card
        statuses = iter(["scheduled"] * 3)

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

    # --- F1 (audit v2): consumption is decided before validation/incident ---
    def test_consumed_with_stale_boot_is_noop(self):
        self.install_fakes()
        self.fake_status = "done"
        attempt, _ = self.fixture(boot="boot-0")     # would be stale_sentinel if validated first
        res = self.run_one(attempt)
        self.assertEqual(res.action, "consumed")
        self.assertEqual(res.status_before, "done")
        self.assert_no_incident_no_comment()

    def test_consumed_with_checksum_conflict_is_noop(self):
        self.install_fakes()
        self.fake_status = "done"
        attempt, _ = self.fixture(tamper=True)
        res = self.run_one(attempt)
        self.assertEqual(res.action, "consumed")
        self.assert_no_incident_no_comment()

    def test_consumed_with_mapping_conflict_is_noop(self):
        self.install_fakes()
        self.fake_status = "done"
        attempt, _ = self.fixture(sentinel_task="t_OTHER", container="other-container")
        res = self.run_one(attempt)
        self.assertEqual(res.action, "consumed")
        self.assert_no_incident_no_comment()

    def test_repeated_consumed_runs_never_accumulate_incidents(self):
        self.install_fakes()
        self.fake_status = "done"
        attempt, _ = self.fixture(tamper=True, boot="boot-0", sentinel_task="t_OTHER")
        first = self.run_one(attempt)
        second = self.run_one(attempt)
        self.assertEqual((first.action, second.action), ("consumed", "consumed"))
        self.assert_no_incident_no_comment()
        self.assertEqual(self.unblock_calls(), [])

    def test_unparsable_sentinel_stays_fail_closed(self):
        self.install_fakes()
        self.fake_status = "done"
        attempt, _ = self.fixture()
        (attempt / "DONE").write_text("{not json")
        res = self.run_one(attempt)
        self.assertEqual(res.action, "incident")
        self.assertEqual(res.reason, "sentinel_ambiguous")
        self.assertEqual(len(self.incident_lines()), 1)

    # --- F2 (audit v2): full identity mapping, not just task_id ---
    def assert_mapping_fail_closed(self, attempt):
        res = self.run_one(attempt)
        self.assertEqual(res.action, "incident")
        self.assertEqual(res.reason, "mapping_mismatch")
        self.assertTrue(res.detail.get("mapping_problems"))
        self.assertEqual(self.unblock_calls(), [])
        return res

    def test_family_json_family_id_mismatch_is_fail_closed(self):
        self.install_fakes()
        attempt, _ = self.fixture(family_over={"family_id": "fam-other"})
        res = self.assert_mapping_fail_closed(attempt)
        self.assertTrue(any("family.json" in p for p in res.detail["mapping_problems"]))

    def test_round_spec_family_and_round_id_mismatch_is_fail_closed(self):
        self.install_fakes()
        attempt, _ = self.fixture(round_over={"family_id": "fam-other", "round_id": "fam-a-r9"})
        res = self.assert_mapping_fail_closed(attempt)
        self.assertTrue(any("round-spec.json" in p for p in res.detail["mapping_problems"]))

    def test_run_spec_identity_mismatch_is_fail_closed(self):
        self.install_fakes()
        attempt, _ = self.fixture(run_over={"run_id": "fam-a-r1-u9", "task_id": "t_OTHER"})
        res = self.assert_mapping_fail_closed(attempt)
        self.assertTrue(any("run-spec.json" in p for p in res.detail["mapping_problems"]))

    def test_container_identity_mismatch_is_fail_closed(self):
        self.install_fakes()
        attempt, _ = self.fixture(container="qlib-run", run_over={"container_id": "other-container"})
        res = self.assert_mapping_fail_closed(attempt)
        self.assertTrue(any("container_id" in p for p in res.detail["mapping_problems"]))

    def test_missing_container_id_is_fail_closed(self):
        self.install_fakes()
        attempt, _ = self.fixture(sentinel_over={"container_id": ""})
        res = self.assert_mapping_fail_closed(attempt)
        self.assertTrue(any("container_id" in p for p in res.detail["mapping_problems"]))

    def test_missing_run_spec_is_fail_closed(self):
        self.install_fakes()
        attempt, _ = self.fixture()
        (attempt / "run-spec.json").unlink()
        self.assert_mapping_fail_closed(attempt)

    # --- v1.7.1: round-level authoritative current attempt -----------------
    # The production bug: a round with a superseded FAILED u1 and a RUNNING u2 was released on u1's
    # terminal sentinel (duplicate Hermes wake). A round now has exactly one attempt that may drive
    # a board transition: the newest valid run-spec identity. Everything older is provenance.
    def round_setup(self, family="fam-a", round_id="fam-a-r1", task=TASK):
        base = Path(self.root) / family
        rnd = base / "rounds" / round_id
        (rnd / "attempts").mkdir(parents=True, exist_ok=True)
        (base / "family.json").write_text(json.dumps(
            {"schema_version": 1, "family_id": family, "kanban_task_id": task,
             "kanban_board": BOARD}))
        (rnd / "round-spec.json").write_text(json.dumps(
            {"schema_version": 1, "family_id": family, "round_id": round_id,
             "kanban_task_id": task, "kanban_board": BOARD}))
        return rnd

    def add_attempt(self, rnd, run_id, terminals=(), created="2026-09-13T00:00:00Z", task=TASK,
                    spec=True, spec_over=None, status_field=None, stage=None, boot=None,
                    tamper=False):
        family, round_id = rnd.parent.parent.name, rnd.name
        attempt = rnd / "attempts" / run_id
        attempt.mkdir(parents=True)
        if spec:
            run_doc = {"schema_version": 1, "family_id": family, "round_id": round_id,
                       "run_id": run_id, "task_id": task, "kanban_board": BOARD,
                       "created_at_utc": created, "container_id": "qlib-run",
                       "image_id": "qlib:0.9.7-arm64"}
            run_doc.update(spec_over or {})
            (attempt / "run-spec.json").write_text(json.dumps(run_doc))
        if stage is not None:
            (attempt / "state.json").write_text(json.dumps(
                {"schema_version": 1, "family_id": family, "round_id": round_id, "run_id": run_id,
                 "stage": stage}))
        payload = (Path(self.root) / family / "family.json").read_text()
        (attempt / "result.json").write_text(payload)
        checksum = "sha256:" + hashlib.sha256(payload.encode()).hexdigest()
        if tamper:
            (attempt / "result.json").write_text(payload + "tampered")
        sentinel = {
            "schema_version": 1, "family_id": family, "round_id": round_id, "run_id": run_id,
            "task_id": task, "kanban_board": BOARD, "created_at_utc": created,
            "host_boot_id": boot or reconcile.host_boot_id(), "container_id": "qlib-run",
            "image_id": "qlib:0.9.7-arm64", "artifact_manifest": ["result.json"],
            "artifact_checksums": {"result.json": checksum}, "verdict_hint": "CANDIDATE_PASS",
        }
        for term in terminals:
            (attempt / term).write_text(json.dumps(dict(sentinel, status=status_field or term)))
        return attempt

    def run_root(self, dry_run=False):
        """Full traversal (the real main() loop) over the temp root with injected kernel I/O."""
        argv = ["reconcile.py", "--results-root", self.root, "--board", BOARD, "--json"]
        if dry_run:
            argv.append("--dry-run")
        buf = io.StringIO()
        real_argv = sys.argv
        try:
            sys.argv = argv
            with contextlib.redirect_stdout(buf):
                rc = reconcile.main()
        finally:
            sys.argv = real_argv
        return rc, json.loads(buf.getvalue())

    def row(self, report, run_id):
        rows = [r for r in report["results"] if r["run_id"] == run_id]
        self.assertEqual(len(rows), 1, "expected exactly one row for %s, got %d" % (run_id, len(rows)))
        return rows[0]

    def assert_no_release(self, report):
        self.assertEqual(report["unblocked"], [])
        self.assertEqual(report["would_unblock"], [])
        self.assertEqual(self.unblock_calls(), [])

    def test_older_terminal_superseded_by_newer_running_attempt(self):
        self.install_fakes()
        rnd = self.round_setup()
        self.add_attempt(rnd, "fam-a-r1-u1", terminals=("FAILED",), created="2026-09-13T00:00:00Z")
        self.add_attempt(rnd, "fam-a-r1-u2", created="2026-09-13T00:10:00Z", stage="RUNNING_QLIB")
        rc, report = self.run_root()
        self.assertEqual(rc, 0)
        self.assert_no_release(report)
        self.assertEqual(report["incidents"], 0)
        superseded = self.row(report, "fam-a-r1-u1")
        self.assertEqual(superseded["action"], "superseded")
        self.assertEqual(superseded["detail"]["authoritative_run_id"], "fam-a-r1-u2")
        self.assertEqual(superseded["detail"]["terminal_files"], ["FAILED"])
        self.assertEqual(self.row(report, "fam-a-r1-u2")["action"], "orphan_candidate")
        self.assert_no_incident_no_comment()

    def test_only_the_newest_terminal_is_actionable(self):
        self.install_fakes()
        rnd = self.round_setup()
        self.add_attempt(rnd, "fam-a-r1-u1", terminals=("FAILED",), created="2026-09-13T00:00:00Z")
        self.add_attempt(rnd, "fam-a-r1-u2", terminals=("DONE",), created="2026-09-13T00:10:00Z")
        rc, report = self.run_root()
        self.assertEqual(rc, 0)
        self.assertEqual(report["unblocked"], [TASK])
        self.assertEqual(self.row(report, "fam-a-r1-u1")["action"], "superseded")
        self.assertEqual(self.row(report, "fam-a-r1-u2")["action"], "unblocked")
        self.assertEqual(len(self.unblock_calls()), 1)

    def test_single_terminal_attempt_still_releases(self):
        self.install_fakes()
        rnd = self.round_setup()
        self.add_attempt(rnd, "fam-a-r1-u1", terminals=("FAILED",), created="2026-09-13T00:00:00Z")
        rc, report = self.run_root()
        self.assertEqual(rc, 0)
        self.assertEqual(report["unblocked"], [TASK])
        self.assertEqual(report["superseded"], [])

    def assert_round_fails_closed(self, report, run_ids):
        self.assertEqual(len(report["results"]), 1)
        row = report["results"][0]
        self.assertEqual(row["action"], "incident")
        self.assertEqual(row["reason"], "attempt_selection_ambiguous")
        self.assertEqual(row["detail"]["round_attempts"], list(run_ids))
        self.assert_no_release(report)
        lines = self.incident_lines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["kind"], "attempt_selection_ambiguous")
        comments = [c for c in self.calls if c[0] == "sh" and "comment" in c[1]]
        self.assertEqual(len(comments), 1)

    def test_newest_attempt_without_identity_fails_closed(self):
        self.install_fakes()
        rnd = self.round_setup()
        self.add_attempt(rnd, "fam-a-r1-u1", terminals=("DONE",), created="2026-09-13T00:00:00Z")
        self.add_attempt(rnd, "fam-a-r1-u2", spec=False, created="2026-09-13T00:10:00Z")
        rc, report = self.run_root()
        self.assertEqual(rc, 3)
        self.assert_round_fails_closed(report, ["fam-a-r1-u1", "fam-a-r1-u2"])
        self.assertEqual(report["results"][0]["run_id"], "fam-a-r1-u2")

    def test_newest_attempt_with_malformed_ordering_metadata_fails_closed(self):
        self.install_fakes()
        rnd = self.round_setup()
        self.add_attempt(rnd, "fam-a-r1-u1", terminals=("DONE",), created="2026-09-13T00:00:00Z")
        self.add_attempt(rnd, "fam-a-r1-u2", spec_over={"created_at_utc": "not-a-timestamp"},
                         created="not-a-timestamp")
        rc, report = self.run_root()
        self.assertEqual(rc, 3)
        self.assert_round_fails_closed(report, ["fam-a-r1-u1", "fam-a-r1-u2"])

    def test_conflicting_task_ownership_in_one_round_fails_closed(self):
        self.install_fakes()
        rnd = self.round_setup()
        self.add_attempt(rnd, "fam-a-r1-u1", terminals=("DONE",), created="2026-09-13T00:00:00Z")
        self.add_attempt(rnd, "fam-a-r1-u2", terminals=("DONE",), created="2026-09-13T00:10:00Z",
                         task="t_OTHER")
        rc, report = self.run_root()
        self.assertEqual(rc, 3)
        self.assertEqual(len(report["results"]), 1)
        self.assert_no_release(report)
        self.assertIn("conflicting task ownership", report["results"][0]["detail"]["ambiguity"])

    def test_u10_beats_u9_on_a_timestamp_tie(self):
        self.install_fakes()
        rnd = self.round_setup()
        tie = "2026-09-13T00:00:00Z"
        self.add_attempt(rnd, "fam-a-r1-u9", terminals=("FAILED",), created=tie)
        self.add_attempt(rnd, "fam-a-r1-u10", created=tie, stage="RUNNING_QLIB")
        rc, report = self.run_root()
        self.assertEqual(rc, 0)
        self.assert_no_release(report)
        self.assertEqual(self.row(report, "fam-a-r1-u9")["action"], "superseded")
        self.assertEqual(self.row(report, "fam-a-r1-u9")["detail"]["authoritative_run_id"],
                         "fam-a-r1-u10")
        self.assertEqual(self.row(report, "fam-a-r1-u10")["action"], "orphan_candidate")

    def test_timestamp_tie_resolved_by_numeric_ordinal(self):
        self.install_fakes()
        rnd = self.round_setup()
        tie = "2026-09-13T00:00:00Z"
        self.add_attempt(rnd, "fam-a-r1-u1", terminals=("FAILED",), created=tie)
        self.add_attempt(rnd, "fam-a-r1-u2", terminals=("DONE",), created=tie)
        rc, report = self.run_root()
        self.assertEqual(rc, 0)
        self.assertEqual(report["unblocked"], [TASK])
        self.assertEqual(self.row(report, "fam-a-r1-u1")["action"], "superseded")

    def test_timestamp_tie_without_ordinal_tiebreak_fails_closed(self):
        self.install_fakes()
        rnd = self.round_setup()
        tie = "2026-09-13T00:00:00Z"
        self.add_attempt(rnd, "fam-a-r1-alpha", terminals=("DONE",), created=tie)
        self.add_attempt(rnd, "fam-a-r1-beta", created=tie, stage="RUNNING_QLIB")
        rc, report = self.run_root()
        self.assertEqual(rc, 3)
        self.assert_round_fails_closed(report, ["fam-a-r1-alpha", "fam-a-r1-beta"])

    def test_different_rounds_do_not_supersede(self):
        self.install_fakes()
        rnd1 = self.round_setup(round_id="fam-a-r1")
        self.add_attempt(rnd1, "fam-a-r1-u1", terminals=("DONE",), created="2026-09-13T00:00:00Z")
        rnd2 = self.round_setup(round_id="fam-a-r2")
        self.add_attempt(rnd2, "fam-a-r2-u1", created="2026-09-13T00:10:00Z", stage="RUNNING_QLIB")
        rc, report = self.run_root()
        self.assertEqual(rc, 0)
        self.assertEqual(report["unblocked"], [TASK])
        self.assertEqual(self.row(report, "fam-a-r1-u1")["action"], "unblocked")
        self.assertEqual(self.row(report, "fam-a-r2-u1")["action"], "orphan_candidate")
        self.assertEqual(report["superseded"], [])

    def test_different_families_do_not_supersede(self):
        self.install_fakes()
        rnd_a = self.round_setup(family="fam-a", round_id="fam-a-r1")
        self.add_attempt(rnd_a, "fam-a-r1-u1", terminals=("DONE",), created="2026-09-13T00:00:00Z")
        rnd_b = self.round_setup(family="fam-b", round_id="fam-b-r1")
        self.add_attempt(rnd_b, "fam-b-r1-u1", created="2026-09-13T00:10:00Z", stage="RUNNING_QLIB")
        rc, report = self.run_root()
        self.assertEqual(rc, 0)
        self.assertEqual(report["unblocked"], [TASK])
        self.assertEqual(self.row(report, "fam-a-r1-u1")["action"], "unblocked")
        self.assertEqual(report["superseded"], [])

    def test_dry_run_selects_the_same_attempt_and_never_mutates(self):
        self.install_fakes()
        rnd = self.round_setup()
        self.add_attempt(rnd, "fam-a-r1-u1", terminals=("FAILED",), created="2026-09-13T00:00:00Z")
        self.add_attempt(rnd, "fam-a-r1-u2", terminals=("DONE",), created="2026-09-13T00:10:00Z")
        rc, report = self.run_root(dry_run=True)
        self.assertEqual(rc, 0)
        self.assertEqual(report["would_unblock"], [TASK])
        self.assertEqual(self.row(report, "fam-a-r1-u1")["action"], "superseded")
        self.assertEqual(self.row(report, "fam-a-r1-u2")["action"], "would_unblock")
        self.assertEqual(self.unblock_calls(), [])
        self.assert_no_incident_no_comment()

    def test_ambiguous_round_with_consumed_card_is_noop(self):
        self.install_fakes()
        self.fake_status = "done"
        rnd = self.round_setup()
        self.add_attempt(rnd, "fam-a-r1-u1", terminals=("DONE",), created="2026-09-13T00:00:00Z")
        self.add_attempt(rnd, "fam-a-r1-u2", spec=False, created="2026-09-13T00:10:00Z")
        rc, report = self.run_root()
        self.assertEqual(rc, 0)
        self.assertEqual(report["incidents"], 0)
        self.assertEqual(report["results"][0]["action"], "consumed")
        self.assert_no_release(report)
        self.assert_no_incident_no_comment()

    def test_superseded_attempt_with_conflicting_terminals_is_noop(self):
        self.install_fakes()
        rnd = self.round_setup()
        self.add_attempt(rnd, "fam-a-r1-u1", terminals=("DONE", "FAILED"),
                         created="2026-09-13T00:00:00Z")
        self.add_attempt(rnd, "fam-a-r1-u2", terminals=("DONE",), created="2026-09-13T00:10:00Z")
        rc, report = self.run_root()
        self.assertEqual(rc, 0)
        self.assertEqual(report["unblocked"], [TASK])
        self.assertEqual(self.row(report, "fam-a-r1-u1")["action"], "superseded")
        self.assert_no_incident_no_comment()

    def test_authoritative_attempt_checks_still_fail_closed(self):
        self.install_fakes()
        rnd = self.round_setup()
        self.add_attempt(rnd, "fam-a-r1-u1", terminals=("DONE",), created="2026-09-13T00:00:00Z")
        self.add_attempt(rnd, "fam-a-r1-u2", terminals=("DONE",), created="2026-09-13T00:10:00Z",
                         tamper=True)
        rc, report = self.run_root()
        self.assertEqual(rc, 3)
        self.assert_no_release(report)
        self.assertEqual(self.row(report, "fam-a-r1-u1")["action"], "superseded")
        self.assertEqual(self.row(report, "fam-a-r1-u2")["action"], "incident")
        self.assertEqual(self.row(report, "fam-a-r1-u2")["reason"], "checksum_mismatch")

    # --- v1.9.0: compute-finished stage wakes default (no terminal sentinel yet) -----------
    # The container runner writes state.json stage=ARTIFACT_READY (or FAILED_SCRIPT) and exits; the
    # sentinel is published host-side by default afterwards. A parked card in that state used to stay
    # `orphan_candidate` forever (Strategy D r1-u2 was exactly this), so the reconciler now wakes
    # default with the same `scheduled -> ready` unblock - never a verdict, never a terminal file,
    # never an incident. Everything unverifiable keeps the descriptive orphan report.
    def terminal_files(self, rnd, run_id):
        attempt = rnd / "attempts" / run_id
        return [t for t in ("DONE", "FAILED", "INCOMPLETE") if (attempt / t).exists()]

    def test_compute_finished_stage_wakes_scheduled_card(self):
        self.install_fakes()
        rnd = self.round_setup()
        self.add_attempt(rnd, "fam-a-r1-u1", created="2026-09-13T00:00:00Z", stage="ARTIFACT_READY")

        rc, report = self.run_root(dry_run=True)
        self.assertEqual(rc, 0)
        self.assertEqual(report["would_unblock"], [TASK])
        self.assertEqual(report["unblocked"], [])
        self.assertEqual(self.unblock_calls(), [])
        row = self.row(report, "fam-a-r1-u1")
        self.assertEqual(row["action"], "would_unblock")
        self.assertEqual(row["detail"]["stage"], "ARTIFACT_READY")
        self.assert_no_incident_no_comment()

        rc, report = self.run_root()
        self.assertEqual(rc, 0)
        self.assertEqual(report["unblocked"], [TASK])
        self.assertEqual(report["would_unblock"], [])
        self.assertEqual(len(self.unblock_calls()), 1)
        row = self.row(report, "fam-a-r1-u1")
        self.assertEqual(row["action"], "unblocked")
        self.assertEqual((row["status_before"], row["status_after"]), ("scheduled", "ready"))
        self.assertEqual(self.terminal_files(rnd, "fam-a-r1-u1"), [],
                         "the wake must never write a terminal sentinel")
        self.assert_no_incident_no_comment()

    def test_failed_script_stage_wakes_scheduled_card(self):
        self.install_fakes()
        rnd = self.round_setup()
        self.add_attempt(rnd, "fam-a-r1-u1", created="2026-09-13T00:00:00Z", stage="FAILED_SCRIPT")
        rc, report = self.run_root()
        self.assertEqual(rc, 0)
        self.assertEqual(report["unblocked"], [TASK])
        self.assertEqual(self.row(report, "fam-a-r1-u1")["detail"]["stage"], "FAILED_SCRIPT")
        self.assertEqual(self.terminal_files(rnd, "fam-a-r1-u1"), [])
        self.assert_no_incident_no_comment()

    def test_running_qlib_stage_is_still_report_only(self):
        self.install_fakes()
        rnd = self.round_setup()
        self.add_attempt(rnd, "fam-a-r1-u1", created="2026-09-13T00:00:00Z", stage="RUNNING_QLIB")
        rc, report = self.run_root()
        self.assertEqual(rc, 0)
        self.assert_no_release(report)
        row = self.row(report, "fam-a-r1-u1")
        self.assertEqual(row["action"], "orphan_candidate")
        self.assertEqual(row["detail"]["stage"], "RUNNING_QLIB")
        self.assertNotIn("wake", row["detail"])
        self.assert_no_incident_no_comment()

    def test_compute_finished_wake_requires_a_parsable_stage(self):
        self.install_fakes()
        rnd = self.round_setup()
        attempt = self.add_attempt(rnd, "fam-a-r1-u1", created="2026-09-13T00:00:00Z")
        (attempt / "state.json").write_text("{not json")
        rc, report = self.run_root()
        self.assertEqual(rc, 0)
        self.assert_no_release(report)
        row = self.row(report, "fam-a-r1-u1")
        self.assertEqual(row["action"], "orphan_candidate")
        self.assertEqual(row["detail"]["stage"], "unparsable")
        self.assertNotIn("wake", row["detail"])
        self.assert_no_incident_no_comment()

    def test_compute_finished_on_non_scheduled_card_is_noop(self):
        self.install_fakes()
        self.fake_status = "done"
        rnd = self.round_setup()
        self.add_attempt(rnd, "fam-a-r1-u1", created="2026-09-13T00:00:00Z", stage="ARTIFACT_READY")
        rc, report = self.run_root()
        self.assertEqual(rc, 0)
        self.assert_no_release(report)
        row = self.row(report, "fam-a-r1-u1")
        self.assertEqual(row["action"], "orphan_candidate")
        self.assertIn("is not scheduled", row["detail"]["wake"])
        self.assert_no_incident_no_comment()

    def test_superseded_compute_finished_attempt_never_wakes(self):
        self.install_fakes()
        rnd = self.round_setup()
        self.add_attempt(rnd, "fam-a-r1-u1", created="2026-09-13T00:00:00Z", stage="ARTIFACT_READY")
        self.add_attempt(rnd, "fam-a-r1-u2", created="2026-09-13T00:10:00Z", stage="RUNNING_QLIB")
        rc, report = self.run_root()
        self.assertEqual(rc, 0)
        self.assert_no_release(report)
        self.assertEqual(self.row(report, "fam-a-r1-u1")["action"], "superseded")
        self.assertEqual(self.row(report, "fam-a-r1-u2")["action"], "orphan_candidate")
        self.assert_no_incident_no_comment()

    def test_compute_finished_without_run_spec_identity_is_fail_closed(self):
        self.install_fakes()
        rnd = self.round_setup()
        self.add_attempt(rnd, "fam-a-r1-u1", created="2026-09-13T00:00:00Z", stage="ARTIFACT_READY",
                         spec_over={"task_id": "", "kanban_board": ""})
        rc, report = self.run_root()
        self.assertEqual(rc, 0)
        self.assert_no_release(report)
        row = self.row(report, "fam-a-r1-u1")
        self.assertEqual(row["action"], "orphan_candidate")
        self.assertIn("fail-closed", row["detail"]["wake"])
        self.assert_no_incident_no_comment()

    def test_compute_finished_with_unreadable_card_readback_is_fail_closed(self):
        self.install_fakes()
        rnd = self.round_setup()
        self.add_attempt(rnd, "fam-a-r1-u1", created="2026-09-13T00:00:00Z", stage="ARTIFACT_READY")

        def dead_card_status(board, task_id):
            return None, "kanban show rc=1: boom"

        reconcile.card_status = dead_card_status
        rc, report = self.run_root()
        self.assertEqual(rc, 0)
        self.assert_no_release(report)
        row = self.row(report, "fam-a-r1-u1")
        self.assertEqual(row["action"], "orphan_candidate")
        self.assertIn("fail-closed", row["detail"]["wake"])
        self.assert_no_incident_no_comment()


if __name__ == "__main__":
    unittest.main(verbosity=2)
