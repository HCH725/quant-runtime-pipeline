#!/usr/bin/env python3
"""Logic-level checks for runtime/production_handoff.py (contract 14.1 / 14.2 / 14.3 / 14.4).

Injected kernel: `production_handoff.sh` is replaced by FakeBoard, so the append decision is exercised
without touching a real board or a real /results tree. Asserts the properties that matter: a card is
created ONLY when every section-14 gate passed, `family.json` lands in the same round, the create is
idempotent by family_id, the created `--body` keeps the candidate bytes verbatim plus the system-owned
lifecycle footer (contract 6.4: a prerequisite-missing TECHNICAL_INCOMPLETE terminal satisfies the
card goal), and every ambiguous state is fail-closed with no card.

Run: python3 runtime/tests/test_production_handoff.py     (stdlib unittest, no dependencies)
"""
import argparse
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME))
import production_handoff as h  # noqa: E402

BOARD = "quant-strategy-research"
TAIL = "t_TAIL"
NEW = "t_NEW"
FAMILY_A = "fam-a-v1"
FAMILY_B = "fam-b-v1"


def candidate(family=FAMILY_B, fingerprint_input="fam-b|w=2,4|1h|long/short", body=None):
    return {
        "family_id": family,
        "title": "Production Strategy B",
        "fingerprint_input": fingerprint_input,
        "card_body": body if body is not None else V13_BODY,
        "provenance": {"reviewed_source": "wiki:quant/example.md", "review_status": "PASS"},
    }


# A v1.3.0-compliant card body: the automation refuses to append a card that does not register
# the DCA parameter domain and the cohort survivor rules (contract 14.4 / 7.2 / 7.3).
V13_BODY = ("## DCA PARAMETER DOMAIN\n"
            "spacing_pct {0.01,0.02,0.03,0.04} x size_multiplier {1.0,1.1} x "
            "breakeven_tp_pct {0.01,0.02,0.03} x invalidation_pct {0.05,0.10} = 48 configs\n"
            "## COHORT SURVIVOR SEMANTICS\n"
            "per (symbol, timeframe) cohort: historical-only selector, then OOS / full / "
            "robustness / neighbourhood evidence (contract 7.3)\n")


class FakeBoard(object):
    """Fake `hermes kanban ...` CLI: list / show / create over an in-memory task dict."""

    def __init__(self, tasks, create_rc=0):
        self.tasks = tasks
        self.create_rc = create_rc
        self.calls = []
        self.keys = {}

    def __call__(self, cmd, timeout=0):
        self.calls.append(cmd)
        action = cmd[4]
        if action == "list":
            return 0, json.dumps([dict(v, id=k) for k, v in self.tasks.items()]), ""
        if action == "show":
            tid = cmd[5]
            if tid not in self.tasks:
                return 1, "", "task not found"
            return 0, json.dumps({"task": dict(self.tasks[tid], id=tid),
                                  "parents": self.tasks[tid].get("parents", [])}), ""
        if action == "create":
            if self.create_rc != 0:
                return self.create_rc, "", "delegate_task child contexts cannot mutate Kanban tasks"
            key = cmd[cmd.index("--idempotency-key") + 1]
            parent = cmd[cmd.index("--parent") + 1]
            tid = self.keys.setdefault(key, NEW)
            self.tasks.setdefault(tid, {"id": tid, "status": "ready", "created_at": 9,
                                        "title": "Production Strategy B", "parents": [parent]})
            return 0, json.dumps(dict(self.tasks[tid], id=tid)), ""
        return 1, "", "unknown action"

    def actions(self):
        return [c[4] for c in self.calls]


class Base(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="qrp-handoff-test-"))
        self.tasks = {TAIL: {"status": "done", "created_at": 1, "title": "Strategy A"}}
        self.fake = FakeBoard(self.tasks)
        self._real_sh = h.sh
        h.sh = self.fake
        self._write_family(FAMILY_A, TAIL)
        self._write_pool([candidate()])

    def tearDown(self):
        h.sh = self._real_sh
        shutil.rmtree(self.root, ignore_errors=True)

    # --- fixture helpers -------------------------------------------------
    def _write_family(self, family, task_id, fingerprint_hex=None):
        d = Path(self.root) / family
        d.mkdir(parents=True, exist_ok=True)
        doc = {"schema_version": 1, "family_id": family, "kanban_task_id": task_id,
               "kanban_board": BOARD, "parent_family": None, "lineage_note": "x",
               "created_at_utc": "2026-09-13T00:00:00Z"}
        if fingerprint_hex:
            doc["semantic_fingerprint"] = fingerprint_hex
        (d / "family.json").write_text(json.dumps(doc))

    def _write_pool(self, cands):
        d = Path(self.root) / h.HANDOFF_DIRNAME
        d.mkdir(parents=True, exist_ok=True)
        (d / h.POOL_FILENAME).write_text(json.dumps({"schema_version": 1, "candidates": cands}))

    def _write_incident(self, task_id, kind="mapping_mismatch"):
        d = Path(self.root) / h.INCIDENT_DIRNAME
        d.mkdir(parents=True, exist_ok=True)
        (d / h.INCIDENT_FILENAME).write_text(json.dumps(
            {"schema_version": 1, "incident_id": "inc-1", "kind": kind,
             "kanban_task_id": task_id, "detected_at_utc": "2026-09-13T00:00:00Z"}) + "\n")

    def args(self, **over):
        base = dict(results_root=str(self.root), board=BOARD, pool=None, detector="handoff",
                    dry_run=False, json=False, quiet_noop=True)
        base.update(over)
        return argparse.Namespace(**base)

    def run_round(self, **over):
        return h.round_once(self.args(**over))


class TestAppend(Base):
    def test_appends_at_tail_and_lands_family_json(self):
        res = self.run_round()
        self.assertEqual(res.action, "appended", res.reason)
        self.assertEqual(res.task_id, NEW)
        self.assertEqual(res.tail_id, TAIL)
        self.assertIn("create", self.fake.actions())
        family_json = json.loads((Path(self.root) / FAMILY_B / "family.json").read_text())
        self.assertEqual(family_json["kanban_task_id"], NEW)
        self.assertEqual(family_json["family_id"], FAMILY_B)
        self.assertEqual(family_json["semantic_fingerprint"],
                         h.fingerprint(candidate()["fingerprint_input"]))
        create = [c for c in self.fake.calls if c[4] == "create"][0]
        self.assertEqual(create[create.index("--idempotency-key") + 1], FAMILY_B)
        self.assertEqual(create[create.index("--parent") + 1], TAIL)
        self.assertIn("--completion-contract", create)

    def test_one_round_then_noop_while_the_new_card_is_active(self):
        self.run_round()
        second = self.run_round()
        self.assertEqual(second.action, "noop", second.reason)
        self.assertEqual(len([c for c in self.fake.calls if c[4] == "create"]), 1)

    def test_create_is_idempotent_by_family_id(self):
        """A crashed round (card created, family.json lost, card later terminal) reuses the same card."""
        self.run_round()
        shutil.rmtree(str(Path(self.root) / FAMILY_B))
        self.fake.tasks[NEW]["status"] = "done"
        res = self.run_round()
        self.assertEqual(res.task_id, NEW)
        self.assertEqual(len(self.fake.keys), 1)

    def test_created_body_keeps_candidate_bytes_and_carries_the_lifecycle_footer(self):
        """The appended `--body` = candidate body verbatim + the fixed system-owned lifecycle footer.

        Contract 6.4: an honest prerequisite-missing `TECHNICAL_INCOMPLETE` terminal (with its immutable
        round-spec/verdict artifacts) satisfies the card's full-backtest goal, so a goal-mode judge must
        see that rule on the card instead of blocking for outputs that cannot exist.
        """
        res = self.run_round()
        self.assertEqual(res.action, "appended", res.reason)
        create = [c for c in self.fake.calls if c[4] == "create"][0]
        body = create[create.index("--body") + 1]
        self.assertTrue(body.startswith(V13_BODY), "candidate body must be preserved verbatim")
        self.assertIn("SATISFIES THIS CARD GOAL", body)
        self.assertIn("TECHNICAL_INCOMPLETE", body)
        self.assertIn("kanban_complete", body)
        self.assertIn("kanban_block", body)
        # The footer is system-owned chrome, never part of the candidate spec or its fingerprint.
        self.assertEqual(json.loads((Path(self.root) / FAMILY_B / "family.json").read_text())
                         ["semantic_fingerprint"], h.fingerprint(candidate()["fingerprint_input"]))


class TestFailClosed(Base):
    def test_ready_tail_is_a_silent_noop(self):
        """A chain head that is merely waiting (ready/running/scheduled) is not an anomaly."""
        self.tasks[TAIL]["status"] = "ready"
        res = self.run_round()
        self.assertEqual(res.action, "noop", res.reason)
        self.assertNotIn("create", self.fake.actions())

    def test_tail_not_terminal(self):
        self.tasks[TAIL]["status"] = "todo"
        res = self.run_round()
        self.assertEqual((res.action, res.finding_key), ("finding", "tail_not_terminal"))
        self.assertNotIn("create", self.fake.actions())

    def test_fenced_invocation_is_reported_as_its_own_kind(self):
        """`hermes kanban` refusing because of HERMES_DELEGATED_CHILD_CONTEXT is not a broken board."""
        h.sh = lambda cmd, timeout=0: (1, "", "kanban: delegate_task child contexts cannot mutate "
                                             "Kanban tasks or boards")
        res = self.run_round()
        self.assertEqual((res.action, res.finding_key), ("finding", "fenced_context"))

    def test_blocked_card_blocks_the_append(self):
        self.tasks[TAIL]["status"] = "done"
        self.tasks["t_BLOCKED"] = {"status": "blocked", "created_at": 2, "title": "freeze"}
        res = self.run_round()
        self.assertEqual((res.action, res.finding_key), ("finding", "blocked_card_present"))
        self.assertNotIn("create", self.fake.actions())

    def test_unresolved_incident_blocks_the_append(self):
        self.tasks["t_LIVE"] = {"status": "running", "created_at": 3, "title": "other"}
        self._write_incident("t_LIVE")
        res = self.run_round()
        self.assertEqual((res.action, res.finding_key), ("finding", "unresolved_incident"))
        self.assertNotIn("create", self.fake.actions())

    def test_resolved_incident_does_not_block(self):
        self._write_incident(TAIL)   # card terminal -> incident is moot
        self.assertEqual(self.run_round().action, "appended")

    def test_duplicate_fingerprint_is_not_re_appended(self):
        self._write_family("fam-used-v1", "t_USED", fingerprint_hex=h.fingerprint("fam-b|w=2,4|1h|long/short"))
        self.tasks["t_USED"] = {"status": "done", "created_at": 2, "title": "used"}
        res = self.run_round()
        self.assertEqual((res.action, res.finding_key), ("finding", "no_eligible_candidate"))
        self.assertNotIn("create", self.fake.actions())

    def test_missing_pool_is_a_finding(self):
        (Path(self.root) / h.HANDOFF_DIRNAME / h.POOL_FILENAME).unlink()
        res = self.run_round()
        self.assertEqual((res.action, res.finding_key), ("finding", "pool_missing"))

    def test_invalid_pool_entry_is_a_finding(self):
        self._write_pool([{"family_id": FAMILY_B}])
        res = self.run_round()
        self.assertEqual((res.action, res.finding_key), ("finding", "pool_invalid"))

    def test_v12_era_card_body_is_refused(self):
        """A pool entry whose body never registers the DCA domain / cohort survivor rules cannot
        express a v1.3 full backtest -> fail-closed, no card, no family.json (contract 14.4)."""
        self._write_pool([candidate(body="## DCA EXECUTION\nsingle PROVISIONAL rail, 2% spacing\n")])
        res = self.run_round()
        self.assertEqual((res.action, res.finding_key), ("finding", "candidate_body_not_v13"))
        self.assertNotIn("create", self.fake.actions())
        self.assertFalse((Path(self.root) / FAMILY_B).exists())

    def test_v13_card_body_is_appended(self):
        self._write_pool([candidate(body="## DCA PARAMETER DOMAIN\n48 configs\n"
                                         "## COHORT SURVIVOR SEMANTICS\nper cohort gate\n")])
        self.assertEqual(self.run_round().action, "appended")

    def test_repeated_pool_candidate_is_ambiguous(self):
        self._write_pool([candidate(), candidate(family="fam-c-v1", fingerprint_input="fam-b|w=2,4|1h|long/short")])
        res = self.run_round()
        self.assertEqual((res.action, res.finding_key), ("finding", "ambiguous_pool"))

    def test_family_without_card_on_board_is_a_finding(self):
        self._write_family("fam-orphan-v1", "t_GONE")
        res = self.run_round()
        self.assertEqual((res.action, res.finding_key), ("finding", "family_card_missing"))

    def test_missing_results_root_is_a_finding(self):
        res = self.run_round(results_root=str(self.root / "nope"))
        self.assertEqual((res.action, res.finding_key), ("finding", "results_root_missing"))

    def test_create_failure_leaves_no_family_json(self):
        self.fake.create_rc = 1
        res = self.run_round()
        self.assertEqual((res.action, res.finding_key), ("finding", "create_failed"))
        self.assertFalse((Path(self.root) / FAMILY_B).exists())

    def test_parent_readback_mismatch_is_a_finding(self):
        original = self.fake.__call__

        def broken(cmd, timeout=0):
            rc, out, err = original(cmd, timeout)
            if cmd[4] == "create":
                self.fake.tasks[NEW]["parents"] = []
            return rc, out, err

        h.sh = broken
        res = self.run_round()
        self.assertEqual((res.action, res.finding_key), ("finding", "readback_failed"))
        self.assertFalse((Path(self.root) / FAMILY_B).exists())


class TestDryRunAndReporting(Base):
    def test_append_is_logged_by_main(self):
        argv, out = sys.argv, sys.stdout
        sys.argv = ["production_handoff.py", "--results-root", str(self.root), "--board", BOARD, "--json"]
        try:
            import io
            sys.stdout = io.StringIO()
            rc = h.main()
            printed = sys.stdout.getvalue()
        finally:
            sys.argv, sys.stdout = argv, out
        self.assertEqual(rc, 0)
        record = json.loads(printed)
        self.assertEqual(record["action"], "appended")
        log = (Path(self.root) / h.HANDOFF_DIRNAME / h.LOG_FILENAME).read_text().strip().splitlines()
        self.assertEqual(json.loads(log[-1])["action"], "appended")

    def test_noop_round_stays_silent(self):
        self.run_round()
        argv, out = sys.argv, sys.stdout
        sys.argv = ["production_handoff.py", "--results-root", str(self.root), "--board", BOARD]
        try:
            import io
            sys.stdout = io.StringIO()
            rc = h.main()
            printed = sys.stdout.getvalue()
        finally:
            sys.argv, sys.stdout = argv, out
        self.assertEqual((rc, printed), (0, ""))

    def test_dry_run_mutates_nothing(self):
        res = self.run_round(dry_run=True)
        self.assertEqual(res.action, "would_append")
        self.assertNotIn("create", self.fake.actions())
        self.assertFalse((Path(self.root) / FAMILY_B).exists())
        self.assertFalse((Path(self.root) / h.HANDOFF_DIRNAME / h.LOG_FILENAME).exists())

    def test_findings_are_logged_and_reported_once(self):
        (Path(self.root) / h.HANDOFF_DIRNAME / h.POOL_FILENAME).unlink()
        first = self.run_round()
        h.append_log(str(self.root), dict(first.as_dict(), action="finding",
                                          finding_key=first.finding_key), False)
        self.assertEqual(h.last_finding_key(str(self.root)), "pool_missing")
        h.append_log(str(self.root), dict(first.as_dict(), action="finding",
                                          finding_key=first.finding_key), False)
        self.assertEqual(len((Path(self.root) / h.HANDOFF_DIRNAME / h.LOG_FILENAME)
                             .read_text().strip().splitlines()), 2)

    def test_append_is_logged(self):
        res = self.run_round()
        h.append_log(str(self.root), res.as_dict(), False)
        log = (Path(self.root) / h.HANDOFF_DIRNAME / h.LOG_FILENAME).read_text().strip().splitlines()
        self.assertEqual(json.loads(log[-1])["action"], "appended")


class SeqBoard(FakeBoard):
    """FakeBoard variant that hands out a DISTINCT task id per idempotency key, so a simulated
    round can mark the appended card terminal and the next round can advance the tail."""

    def __init__(self, tasks):
        super(SeqBoard, self).__init__(tasks)
        self.seq = 0

    def __call__(self, cmd, timeout=0):
        if cmd[4] != "create":
            return super(SeqBoard, self).__call__(cmd, timeout)
        self.calls.append(cmd)
        key = cmd[cmd.index("--idempotency-key") + 1]
        parent = cmd[cmd.index("--parent") + 1]
        if key not in self.keys:
            self.seq += 1
            self.keys[key] = "t_SEQ%d" % self.seq
        tid = self.keys[key]
        self.tasks.setdefault(tid, {"id": tid, "status": "ready", "created_at": 100 + self.seq,
                                    "title": key, "parents": [parent]})
        return 0, json.dumps(dict(self.tasks[tid], id=tid)), ""


class TestPoolOrdering(Base):
    """v1.7.0 (card t_15fed3f2): the reviewed pool's eligible order must be B v2 -> C -> D -> E.

    Hermetic mirror of the real pool shape: the consumed historical entry (B v1) stays first and
    must never be appended again; each round appends exactly the first unconsumed candidate.
    """

    def setUp(self):
        super(TestPoolOrdering, self).setUp()
        self.fake = SeqBoard(self.tasks)
        h.sh = self.fake
        self.order = ["fam-b-v1", "fam-b2-v1", "fam-c-v1", "fam-d-v1", "fam-e-v1"]
        self._write_pool([candidate(family=fid, fingerprint_input="%s|w=2,4|1h|long" % fid)
                          for fid in self.order])
        # B v1 is consumed: its family directory + a terminal (archived) card already exist
        self._write_family(self.order[0], "t_B1")
        self.tasks["t_B1"] = {"status": "archived", "created_at": 2, "title": "Strategy B v1"}

    def test_sequence_is_b2_then_c_then_d_then_e(self):
        appended = []
        for _ in range(len(self.order) - 1):        # B v2, C, D, E  (B v1 is already consumed)
            res = self.run_round()
            self.assertEqual(res.action, "appended", res.reason)
            appended.append(res.family_id)
            self.fake.tasks[res.task_id]["status"] = "done"   # card reaches terminal
        self.assertEqual(appended, self.order[1:])
        self.assertEqual(self.run_round().finding_key, "no_eligible_candidate")
        keys = [c[c.index("--idempotency-key") + 1]
                for c in self.fake.calls if c[4] == "create"]
        self.assertEqual(keys, self.order[1:])
        self.assertNotIn(self.order[0], keys)

    def test_consumed_b_v1_is_never_recreated(self):
        res = self.run_round()
        self.assertEqual(res.family_id, self.order[1])
        self.assertEqual(json.loads((Path(self.root) / self.order[0] / "family.json").read_text()),
                         {"schema_version": 1, "family_id": self.order[0],
                          "kanban_task_id": "t_B1", "kanban_board": BOARD,
                          "parent_family": None, "lineage_note": "x",
                          "created_at_utc": "2026-09-13T00:00:00Z"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
