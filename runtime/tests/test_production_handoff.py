#!/usr/bin/env python3
"""Logic-level checks for runtime/production_handoff.py (contract 14.1 / 14.2 / 14.3 / 14.4).

Injected kernel: `production_handoff.sh` is replaced by FakeKanban, so the round decision is exercised
without touching a real board or a real /results tree. Asserts the properties that matter:

  * the decision reads ONLY results-root artifacts - no `hermes kanban list|show` call exists at all,
    so a blocked / stale / unreadable board can never freeze or reorder the pipeline;
  * `family.json` (the canonical advance) lands in the same round as the dispatch, and the dispatch is
    one idempotent-by-family_id `create` carrying the candidate bytes verbatim plus the system-owned
    lifecycle footer (contract 6.4: a prerequisite-missing TECHNICAL_INCOMPLETE terminal satisfies the
    card goal) and no `--parent` edge;
  * real runtime evidence holds the pipeline (no duplicate launch); stale evidence and registered-but-
    never-launched families do not; a terminal verdict releases the family;
  * every ambiguous state is fail-closed with no dispatch, and a dispatch failure never rewinds the
    canonical advance.

Run: python3 runtime/tests/test_production_handoff.py     (stdlib unittest, no dependencies)
"""
import argparse
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME))
import production_handoff as h  # noqa: E402

BOARD = "quant-strategy-research"
FAMILY_A = "fam-a-v1"
FAMILY_B = "fam-b-v1"
BLOCKED = "fam-blocked-v1"
STALE = "fam-stale-v1"
FRESH = "fam-fresh-v1"


def candidate(family=FAMILY_B, fingerprint_input="fam-b|w=2,4|1h|long/short", body=None, **over):
    cand = {
        "family_id": family,
        "title": "Production Strategy B",
        "fingerprint_input": fingerprint_input,
        "card_body": body if body is not None else V13_BODY,
        "provenance": {"reviewed_source": "wiki:quant/example.md", "review_status": "PASS"},
    }
    cand.update(over)
    return cand


# A v1.3.0-compliant card body: the automation refuses to append a card that does not register
# the DCA parameter domain and the cohort survivor rules (contract 14.4 / 7.2 / 7.3).
V13_BODY = ("## DCA PARAMETER DOMAIN\n"
            "spacing_pct {0.01,0.02,0.03,0.04} x size_multiplier {1.0,1.1} x "
            "breakeven_tp_pct {0.01,0.02,0.03} x invalidation_pct {0.05,0.10} = 48 configs\n"
            "## COHORT SURVIVOR SEMANTICS\n"
            "per (symbol, timeframe) cohort: historical-only selector, then OOS / full / "
            "robustness / neighbourhood evidence (contract 7.3)\n")


class FakeKanban(object):
    """Fake `hermes kanban create`: records every call so the suite can prove no read ever happens."""

    def __init__(self, create_rc=0, create_out=None):
        self.create_rc = create_rc
        self.create_out = create_out
        self.calls = []
        self.keys = {}

    def __call__(self, cmd, timeout=0):
        self.calls.append(cmd)
        if cmd[4] != "create":
            raise AssertionError("production handoff must never call `hermes kanban %s`" % cmd[4])
        if self.create_rc != 0:
            return self.create_rc, "", "delegate_task child contexts cannot mutate Kanban tasks"
        if self.create_out is not None:
            return 0, self.create_out, ""
        key = cmd[cmd.index("--idempotency-key") + 1]
        tid = self.keys.setdefault(key, "t_" + key[:12])
        return 0, json.dumps({"id": tid, "status": "ready"}), ""

    def creates(self):
        return [c for c in self.calls if c[4] == "create"]

    def body(self):
        cmd = self.creates()[-1]
        return cmd[cmd.index("--body") + 1]


class Base(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="qrp-handoff-test-"))
        self.fake = FakeKanban()
        self._real_sh = h.sh
        h.sh = self.fake
        self._write_family(FAMILY_A, "t_A")
        self._write_pool([candidate()])

    def tearDown(self):
        h.sh = self._real_sh
        shutil.rmtree(self.root, ignore_errors=True)

    # --- fixture helpers -------------------------------------------------
    def _write_family(self, family, task_id="t_x", fingerprint_hex=None, created=None,
                      with_attempt=False, attempt_age_minutes=0, terminal=None, verdict=None):
        d = Path(self.root) / family
        d.mkdir(parents=True, exist_ok=True)
        doc = {"schema_version": 1, "family_id": family, "kanban_task_id": task_id,
               "kanban_board": BOARD, "parent_family": None, "lineage_note": "x",
               "created_at_utc": created or "2026-09-13T00:00:00Z"}
        if fingerprint_hex:
            doc["semantic_fingerprint"] = fingerprint_hex
        (d / "family.json").write_text(json.dumps(doc))
        if with_attempt:
            self._write_attempt(family, age_minutes=attempt_age_minutes, terminal=terminal)
        if verdict:
            self._write_verdict(family, verdict)
        return family

    def _write_attempt(self, family, run_id=None, age_minutes=0, terminal=None):
        """An attempt dir with a run-spec; `age_minutes` backdates every mtime (stale evidence)."""
        round_id = family + "-r1"
        run_id = run_id or round_id + "-u1"
        base = Path(self.root) / family / "rounds" / round_id / "attempts" / run_id
        base.mkdir(parents=True, exist_ok=True)
        (base / "run-spec.json").write_text(json.dumps(
            {"family_id": family, "round_id": round_id, "run_id": run_id}))
        if terminal:
            (base / terminal).write_text(json.dumps(
                {"family_id": family, "round_id": round_id, "run_id": run_id, "status": terminal}))
        if age_minutes:
            old = time.time() - age_minutes * 60
            for path in [base] + sorted(base.rglob("*")):
                os.utime(str(path), (old, old))
        return base

    def _write_verdict(self, family, verdict, doc=None, raw=None):
        round_id = family + "-r1"
        path = Path(self.root) / family / "rounds" / round_id / "verdict.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        if raw is None:
            if doc is None:
                doc = {"schema_version": 1, "family_id": family, "round_id": round_id,
                       "verdict": verdict}
            raw = json.dumps(doc)
        path.write_text(raw)
        return path

    def _write_pool(self, cands):
        d = Path(self.root) / h.HANDOFF_DIRNAME
        d.mkdir(parents=True, exist_ok=True)
        (d / h.POOL_FILENAME).write_text(json.dumps({"schema_version": 1, "candidates": cands}))

    def _write_incident(self, rec):
        d = Path(self.root) / h.INCIDENT_DIRNAME
        d.mkdir(parents=True, exist_ok=True)
        (d / h.INCIDENT_FILENAME).write_text(json.dumps(rec) + "\n")

    def args(self, **over):
        base = dict(results_root=str(self.root), board=BOARD, pool=None, detector="handoff",
                    dry_run=False, json=False, quiet_noop=True)
        base.update(over)
        return argparse.Namespace(**base)

    def run_round(self, **over):
        return h.round_once(self.args(**over))

    def run_main(self, *argv):
        """h.main() with captured stdout/stderr; returns (rc, stdout, stderr)."""
        saved = sys.argv, sys.stdout, sys.stderr
        out, err = io.StringIO(), io.StringIO()
        sys.argv = ["production_handoff.py", "--results-root", str(self.root), "--board", BOARD] \
            + list(argv)
        sys.stdout, sys.stderr = out, err
        try:
            rc = h.main()
        finally:
            sys.argv, sys.stdout, sys.stderr = saved
        return rc, out.getvalue(), err.getvalue()


class TestAdvance(Base):
    def test_advance_lands_family_json_and_dispatches_one_work_order(self):
        res = self.run_round()
        self.assertEqual(res.action, "appended", res.reason)
        self.assertEqual(res.outcome, "advanced")
        self.assertEqual(res.family_id, FAMILY_B)
        doc = json.loads((self.root / FAMILY_B / "family.json").read_text())
        self.assertEqual(doc["family_id"], FAMILY_B)
        self.assertEqual(doc["kanban_task_id"], res.task_id)
        self.assertEqual(doc["handoff"]["decision_evidence"], "results_root_only")
        self.assertEqual(len(self.fake.creates()), 1)
        self.assertEqual(res.detail["semantic_fingerprint"], doc["semantic_fingerprint"])

    def test_kanban_is_never_read(self):
        # The point of contract 14.4 v-next: no list/show/status read exists, so no board state
        # (blocked, stale, unreadable) can participate in the advance decision.
        self.run_round()
        self.assertEqual([c[4] for c in self.fake.calls], ["create"])
        cmd = self.fake.creates()[0]
        self.assertNotIn("--parent", cmd)
        self.assertEqual(cmd[cmd.index("--idempotency-key") + 1], FAMILY_B)

    def test_blocked_board_card_cannot_freeze_the_advance(self):
        # Reproduction of the production freeze: a family whose card sits in `blocked` with no verdict
        # used to gate the tail forever. It is now inert - the pipeline still advances.
        self._write_family(BLOCKED, "t_BLOCKED", created="2026-09-13T00:00:00Z")
        res = self.run_round()
        self.assertEqual(res.action, "appended", res.reason)
        self.assertEqual(res.family_id, FAMILY_B)

    def test_stale_unterminated_family_does_not_freeze(self):
        # An attempt that stopped writing 6h ago is not work in flight: the pipeline moves on.
        self._write_family(STALE, with_attempt=True, attempt_age_minutes=360)
        res = self.run_round()
        self.assertEqual(res.action, "appended", res.reason)

    def test_fresh_attempt_holds_the_pipeline(self):
        # Real, non-terminal runtime evidence: advancing now would duplicate a live run.
        self._write_family(FRESH, with_attempt=True, attempt_age_minutes=1)
        res = self.run_round()
        self.assertEqual((res.action, res.outcome), ("noop", "running"))
        self.assertEqual(res.detail["active_family"], FRESH)
        self.assertEqual(self.fake.calls, [])
        self.assertFalse((self.root / FAMILY_B).exists())

    def test_just_registered_family_holds_the_pipeline(self):
        # A registration younger than the launch grace is a launch in flight: its worker has not
        # published round/run specs yet, so the next candidate waits instead of racing it.
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self._write_family(FRESH, created=now)
        res = self.run_round()
        self.assertEqual((res.action, res.outcome), ("noop", "running"))
        self.assertIn("launch grace", res.reason)
        self.assertEqual(self.fake.calls, [])

    def test_terminal_verdict_releases_the_family(self):
        # A contract-terminal verdict means the family owes the pipeline nothing - even with a fresh
        # attempt directory lying around.
        self._write_family(FRESH, with_attempt=True, attempt_age_minutes=1,
                           verdict="TECHNICAL_INCOMPLETE")
        res = self.run_round()
        self.assertEqual(res.action, "appended", res.reason)

    def test_non_terminal_verdict_token_cannot_bypass(self):
        # A non-terminal token is not a release: the family still holds the pipeline with its live run.
        self._write_family(FRESH, with_attempt=True, attempt_age_minutes=1,
                           verdict="NEEDS_MORE_EVIDENCE")
        res = self.run_round()
        self.assertEqual((res.action, res.outcome), ("noop", "running"))

    def test_create_is_idempotent_by_family_id(self):
        self.run_round()
        self.assertEqual(len(self.fake.creates()), 1)
        # a second dispatch of the same family converges on the same card
        task_id, why = h.dispatch_work_order(BOARD, candidate(), self.args())
        self.assertIsNone(why)
        self.assertEqual(task_id, "t_" + FAMILY_B[:12])
        self.assertEqual(len(self.fake.keys), 1)

    def test_created_body_keeps_candidate_bytes_and_carries_the_lifecycle_footer(self):
        self.run_round()
        body = self.fake.body()
        self.assertTrue(body.startswith(V13_BODY), body[:200])
        self.assertIn(h.LIFECYCLE_FOOTER.strip(), body)
        self.assertIn("TECHNICAL_INCOMPLETE", body)

    def test_footer_carries_the_bounded_prerequisite_evidence_rule(self):
        self.assertIn("TECHNICAL_INCOMPLETE", h.LIFECYCLE_FOOTER)
        self.assertIn("prerequisite", h.LIFECYCLE_FOOTER)

    def test_work_order_failure_is_reported_but_does_not_rewind_the_advance(self):
        # The advance is the family.json registration; the card is the agent-lane vehicle. A dispatch
        # failure is a finding for the operator, never a freeze and never a silent rollback.
        h.sh = FakeKanban(create_rc=1)
        res = self.run_round()
        self.assertEqual((res.action, res.outcome, res.finding_key),
                         ("finding", "finding", "work_order_failed"))
        self.assertTrue((self.root / FAMILY_B / "family.json").is_file())
        self.assertIn("operator action needed", res.reason)

    def test_unparsable_create_output_is_a_finding(self):
        h.sh = FakeKanban(create_out="not json")
        res = self.run_round()
        self.assertEqual(res.finding_key, "work_order_failed")
        self.assertTrue((self.root / FAMILY_B / "family.json").is_file())


class TestFailClosed(Base):
    def test_missing_results_root_is_a_finding(self):
        res = self.run_round(results_root=str(self.root / "nope"))
        self.assertEqual(res.finding_key, "results_root_missing")
        self.assertEqual(self.fake.calls, [])

    def test_missing_pool_is_a_finding(self):
        (self.root / h.HANDOFF_DIRNAME / h.POOL_FILENAME).unlink()
        res = self.run_round()
        self.assertEqual(res.finding_key, "pool_missing")
        self.assertEqual(self.fake.calls, [])

    def test_invalid_pool_entry_is_a_finding(self):
        self._write_pool([candidate(), {"family_id": "fam-x"}])
        res = self.run_round()
        self.assertEqual(res.finding_key, "pool_invalid")
        self.assertEqual(self.fake.calls, [])

    def test_repeated_pool_candidate_is_ambiguous(self):
        self._write_pool([candidate(), candidate()])
        res = self.run_round()
        self.assertEqual(res.finding_key, "ambiguous_pool")

    def test_duplicate_fingerprint_is_not_re_appended(self):
        self._write_family(FAMILY_B, "t_B", fingerprint_hex=h.fingerprint(
            candidate()["fingerprint_input"]))
        res = self.run_round()
        self.assertEqual((res.action, res.outcome), ("noop", "idle"))
        self.assertEqual(self.fake.calls, [])

    def test_v12_era_card_body_is_refused(self):
        self._write_pool([candidate(body="## COHORT SURVIVOR SEMANTICS\nv1.2 body\n")])
        res = self.run_round()
        self.assertEqual(res.finding_key, "candidate_body_not_v13")
        self.assertEqual(self.fake.calls, [])

    def test_v13_card_body_is_appended(self):
        self._write_pool([candidate(body=V13_BODY)])
        res = self.run_round()
        self.assertEqual(res.action, "appended", res.reason)

    def test_unresolved_incident_blocks_the_advance(self):
        # Fail-closed on canonical evidence: the incident's family has no terminal verdict and its
        # attempt carries no clean terminal, so the pipeline does not advance.
        self._write_family(BLOCKED, with_attempt=True, attempt_age_minutes=360)
        self._write_incident({"schema_version": 1, "incident_id": "inc-1",
                              "kind": "sentinel_ambiguous", "family_id": BLOCKED,
                              "round_id": BLOCKED + "-r1",
                              "detected_at_utc": "2026-09-13T00:00:00Z"})
        res = self.run_round()
        self.assertEqual((res.action, res.outcome, res.finding_key),
                         ("finding", "incident", "unresolved_incident"))
        self.assertEqual(self.fake.calls, [])

    def test_incident_resolves_on_terminal_evidence(self):
        self._write_family(BLOCKED, with_attempt=True, attempt_age_minutes=360,
                           terminal="INCOMPLETE", verdict="TECHNICAL_INCOMPLETE")
        self._write_incident({"schema_version": 1, "incident_id": "inc-1",
                              "kind": "sentinel_ambiguous", "family_id": BLOCKED,
                              "round_id": BLOCKED + "-r1",
                              "detected_at_utc": "2026-09-13T00:00:00Z"})
        res = self.run_round()
        self.assertEqual(res.action, "appended", res.reason)

    def test_incident_without_family_identity_fails_closed(self):
        self._write_incident({"schema_version": 1, "incident_id": "inc-2", "kind": "mapping_mismatch"})
        res = self.run_round()
        self.assertEqual(res.finding_key, "unresolved_incident")

    def test_unparsable_incident_line_fails_closed(self):
        d = Path(self.root) / h.INCIDENT_DIRNAME
        d.mkdir(parents=True, exist_ok=True)
        (d / h.INCIDENT_FILENAME).write_text("{not json\n")
        res = self.run_round()
        self.assertEqual(res.finding_key, "unresolved_incident")

    def test_ambiguous_sentinel_is_not_a_clean_terminal(self):
        # A malformed/mismatched terminal keeps the incident open (fail-closed), never a silent pass.
        self._write_family(BLOCKED, with_attempt=True, attempt_age_minutes=360)
        attempt = (self.root / BLOCKED / "rounds" / (BLOCKED + "-r1") / "attempts"
                   / (BLOCKED + "-r1-u1"))
        (attempt / "INCOMPLETE").write_text(json.dumps({"status": "SOMETHING_ELSE"}))
        old = time.time() - 360 * 60
        for path in [attempt] + sorted(attempt.rglob("*")):
            os.utime(str(path), (old, old))          # the sentinel itself is old evidence
        self._write_incident({"schema_version": 1, "incident_id": "inc-3",
                              "kind": "sentinel_ambiguous", "family_id": BLOCKED,
                              "detected_at_utc": "2026-09-13T00:00:00Z"})
        res = self.run_round()
        self.assertEqual(res.finding_key, "unresolved_incident")


class TestDryRunAndReporting(Base):
    def test_dry_run_mutates_nothing(self):
        res = self.run_round(dry_run=True)
        self.assertEqual((res.action, res.outcome), ("would_append", "advanced"))
        self.assertEqual(self.fake.calls, [])
        self.assertFalse((self.root / FAMILY_B).exists())
        self.assertFalse((self.root / h.HANDOFF_DIRNAME / h.LOG_FILENAME).exists())

    def test_append_is_logged_by_main(self):
        rc, out, err = self.run_main("--json")
        self.assertEqual(rc, 0)
        record = json.loads(out)
        self.assertEqual((record["action"], record["outcome"]), ("appended", "advanced"))
        self.assertIn("outcome=advanced", err)
        log = (self.root / h.HANDOFF_DIRNAME / h.LOG_FILENAME).read_text().strip().splitlines()
        self.assertEqual(json.loads(log[-1])["action"], "appended")

    def test_noop_round_stays_silent_on_stdout_but_reports_its_outcome(self):
        self._write_family(FRESH, with_attempt=True, attempt_age_minutes=1)
        rc, out, err = self.run_main()
        self.assertEqual((rc, out), (0, ""))
        self.assertIn("outcome=running", err)
        self.assertFalse((self.root / h.HANDOFF_DIRNAME / h.LOG_FILENAME).exists())

    def test_idle_outcome_is_reported_without_a_log_line(self):
        self._write_pool([])
        rc, out, err = self.run_main()
        self.assertEqual((rc, out), (0, ""))
        self.assertIn("outcome=idle", err)

    def test_findings_are_logged_and_reported_once(self):
        (self.root / h.HANDOFF_DIRNAME / h.POOL_FILENAME).unlink()
        first = self.run_round()
        h.append_log(str(self.root), dict(first.as_dict(), action="finding",
                                          finding_key=first.finding_key), False)
        self.assertEqual(h.last_finding_key(str(self.root)), "pool_missing")
        h.append_log(str(self.root), dict(first.as_dict(), action="finding",
                                          finding_key=first.finding_key), False)
        self.assertEqual(len((self.root / h.HANDOFF_DIRNAME / h.LOG_FILENAME)
                             .read_text().strip().splitlines()), 2)

    def test_repeated_finding_is_silent_on_the_second_tick(self):
        (self.root / h.HANDOFF_DIRNAME / h.POOL_FILENAME).unlink()
        rc, out, err = self.run_main()
        self.assertEqual(rc, 0)                      # invocation succeeded; the outcome is the token
        self.assertIn("outcome=finding", err)
        self.assertIn("pool_missing", out)
        rc, out, err = self.run_main()
        self.assertEqual((rc, out), (0, ""))         # the same finding is not repeated
        self.assertIn("outcome=finding", err)

    def test_incident_outcome_is_its_own_token(self):
        self._write_incident({"schema_version": 1, "incident_id": "inc-4", "kind": "mapping_mismatch"})
        rc, out, err = self.run_main("--json")
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["outcome"], "incident")
        self.assertIn("outcome=incident", err)


class TestPoolOrdering(Base):
    """The reviewed pool's eligible order must be B v2 -> C -> D -> E (contract 14.3).

    Hermetic mirror of the real pool shape: the consumed historical entry (B v1) stays first and must
    never be dispatched again; each round advances exactly the first unconsumed candidate, and the next
    advance happens only once the previous family really reached terminal evidence.
    """

    def setUp(self):
        super(TestPoolOrdering, self).setUp()
        self.order = ["fam-b-v1", "fam-b2-v1", "fam-c-v1", "fam-d-v1", "fam-e-v1"]
        self._write_pool([candidate(family=fid, fingerprint_input="%s|w=2,4|1h|long" % fid)
                          for fid in self.order])
        # B v1 is consumed: its family dir exists (with a terminal verdict) and is never re-advanced.
        self._write_family(self.order[0], "t_B1", verdict="TECHNICAL_INCOMPLETE")

    def test_sequence_is_b2_then_c_then_d_then_e(self):
        advanced = []
        for expected in self.order[1:]:
            res = self.run_round()
            self.assertEqual(res.action, "appended", res.reason)
            self.assertEqual(res.family_id, expected)
            advanced.append(res.family_id)
            # the worker finishes the family; only then may the pipeline advance again
            self._write_verdict(expected, "TECHNICAL_INCOMPLETE")
        self.assertEqual(advanced, self.order[1:])
        last = self.run_round()
        self.assertEqual((last.action, last.outcome), ("noop", "idle"))
        keys = [c[c.index("--idempotency-key") + 1] for c in self.fake.creates()]
        self.assertEqual(keys, self.order[1:])
        self.assertNotIn(self.order[0], keys)

    def test_no_advance_while_the_new_family_is_unfinished(self):
        self.assertEqual(self.run_round().family_id, self.order[1])
        again = self.run_round()
        self.assertEqual((again.action, again.outcome), ("noop", "running"))
        self.assertEqual(len(self.fake.creates()), 1)

    def test_consumed_b_v1_is_never_recreated(self):
        res = self.run_round()
        self.assertEqual(res.family_id, self.order[1])
        self.assertFalse((self.root / h.HANDOFF_DIRNAME / h.LOG_FILENAME).exists())
        doc = json.loads((self.root / self.order[0] / "family.json").read_text())
        self.assertEqual(doc["family_id"], self.order[0])
        self.assertEqual(doc["kanban_task_id"], "t_B1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
