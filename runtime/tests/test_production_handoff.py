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
  * real runtime evidence holds the pipeline (no duplicate launch) - including a live follow-up round
    of a family whose earlier round already carries a terminal verdict, because verdict.json is per
    ROUND (contract 7.3/9.4); stale evidence and registered-but-never-launched families do not, and a
    terminal verdict releases only a family whose newest attempt is not live;
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


class FakeLaunch(object):
    """Record direct Hermes launches without invoking a model or touching a board."""

    def __init__(self, error=None, busy=False):
        self.error = error
        self.busy = busy
        self.calls = []

    def __call__(self, results_root, family_id, name, prompt, skills=(), workspace=h.DEFAULT_WORKSPACE):
        self.calls.append((results_root, family_id, name, prompt, skills, workspace))
        return None if self.error or self.busy else 12345, self.error, self.busy

    def launches(self):
        return self.calls

    def body(self):
        return self.calls[-1][3]


class Base(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="qrp-handoff-test-"))
        self.fake = FakeLaunch()
        self._real_launch = h.launch_agent
        self._real_sh = h.sh
        h.launch_agent = self.fake
        h.sh = lambda *_a, **_k: self.fail("production must not call any CLI/board reader")
        self._write_family(FAMILY_A, "t_A")
        self._write_pool([candidate()])

    def tearDown(self):
        h.sh = self._real_sh
        h.launch_agent = self._real_launch
        shutil.rmtree(self.root, ignore_errors=True)

    # --- fixture helpers -------------------------------------------------
    def _write_family(self, family, task_id="t_x", fingerprint_hex=None, created=None,
                      with_attempt=False, attempt_age_minutes=0, terminal=None, verdict=None,
                      attempt_round=None):
        d = Path(self.root) / family
        d.mkdir(parents=True, exist_ok=True)
        doc = {"schema_version": 1, "family_id": family, "kanban_task_id": task_id,
               "kanban_board": BOARD, "parent_family": None, "lineage_note": "x",
               "created_at_utc": created or "2026-09-13T00:00:00Z"}
        if fingerprint_hex:
            doc["semantic_fingerprint"] = fingerprint_hex
        (d / "family.json").write_text(json.dumps(doc))
        if with_attempt:
            self._write_attempt(family, age_minutes=attempt_age_minutes, terminal=terminal,
                                round_id=attempt_round)
        if verdict:
            self._write_verdict(family, verdict)
        return family

    def _write_attempt(self, family, run_id=None, age_minutes=0, terminal=None, round_id=None):
        """An attempt dir with a run-spec; `age_minutes` backdates every mtime (stale evidence)."""
        round_id = round_id or family + "-r1"
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

    def _write_verdict(self, family, verdict, doc=None, raw=None, round_id=None):
        round_id = round_id or family + "-r1"
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
        self.assertNotIn("kanban_task_id", doc)
        self.assertIsNone(res.task_id)
        self.assertEqual(doc["handoff"]["execution"], "direct_hermes")
        self.assertEqual(doc["handoff"]["decision_evidence"], "results_root_only")
        self.assertEqual(len(self.fake.launches()), 1)
        self.assertEqual(res.detail["semantic_fingerprint"], doc["semantic_fingerprint"])

    def test_kanban_is_never_read(self):
        # The point of contract 14.4 v-next: no list/show/status read exists, so no board state
        # (blocked, stale, unreadable) can participate in the advance decision.
        self.run_round()
        self.assertEqual([c[1] for c in self.fake.calls], [FAMILY_B])
        self.assertEqual(self.fake.calls[0][2], "agent-task.md")

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

    def test_terminal_verdict_cannot_release_a_live_attempt(self):
        # Was `test_terminal_verdict_releases_the_family` (round-1 review defect): verdict.json is PER
        # ROUND (contract 7.3/9.4), so a family whose newest attempt is still writing runtime evidence
        # (no terminal sentinel, write inside the window) holds the pipeline even when a round of that
        # family already carries a terminal verdict.
        self._write_family(FRESH, with_attempt=True, attempt_age_minutes=1,
                           verdict="TECHNICAL_INCOMPLETE")
        res = self.run_round()
        self.assertEqual((res.action, res.outcome), ("noop", "running"))
        self.assertEqual(res.detail["active_family"], FRESH)
        self.assertEqual(self.fake.calls, [])
        self.assertFalse((self.root / FAMILY_B).exists())

    def test_live_follow_up_round_holds_after_the_earlier_round_verdict(self):
        # The reported defect shape: r1 is decided (verdict PASS) while r2 is writing runtime evidence
        # right now - a multi-round follow-up is the normal live pattern, not a finished family.
        self._write_family(FRESH, with_attempt=True, attempt_age_minutes=1, verdict="PASS",
                           attempt_round=FRESH + "-r2")
        res = self.run_round()
        self.assertEqual((res.action, res.outcome), ("noop", "running"))
        self.assertEqual(self.fake.calls, [])

    def test_verdict_releases_a_family_whose_attempt_is_finished(self):
        # Closed = a terminal verdict AND a newest attempt that published its terminal sentinel: the
        # family owes the pipeline nothing, so the next candidate advances.
        self._write_family(FRESH, with_attempt=True, attempt_age_minutes=1, terminal="DONE",
                           verdict="TECHNICAL_INCOMPLETE")
        res = self.run_round()
        self.assertEqual(res.action, "appended", res.reason)
        self.assertEqual(res.family_id, FAMILY_B)

    def test_stale_attempt_with_a_verdict_advances(self):
        # A dead run never freezes the pipeline: outside the window the verdict closes the family.
        self._write_family(STALE, with_attempt=True, attempt_age_minutes=360,
                           verdict="TECHNICAL_INCOMPLETE")
        res = self.run_round()
        self.assertEqual(res.action, "appended", res.reason)

    def test_finished_attempt_without_a_round_verdict_still_holds(self):
        # The run finished but the round verdict is not written yet: the family still owes the round,
        # so the next candidate waits (bounded by the 90-minute window).
        self._write_family(FRESH, with_attempt=True, attempt_age_minutes=1, terminal="DONE")
        res = self.run_round()
        self.assertEqual((res.action, res.outcome), ("noop", "running"))
        self.assertEqual(self.fake.calls, [])

    def test_finished_follow_up_round_without_its_own_verdict_holds(self):
        # Round-2 review defect: the release decision must read the newest attempt's OWN round. r1 is
        # decided (PASS) and r2 already published DONE but has not been judged yet -> the family still
        # owes the pipeline r2, so the next candidate waits instead of racing the round verdict.
        self._write_family(FRESH, with_attempt=True, attempt_age_minutes=1, terminal="DONE",
                           verdict="PASS", attempt_round=FRESH + "-r2")
        res = self.run_round()
        self.assertEqual((res.action, res.outcome), ("noop", "running"))
        self.assertEqual(res.detail["active_family"], FRESH)
        self.assertIn("this round's verdict still missing", res.reason)
        self.assertEqual(self.fake.calls, [])
        self.assertFalse((self.root / FAMILY_B).exists())

    def test_follow_up_round_with_its_own_verdict_releases_the_family(self):
        # Same multi-round shape, but r2 carries its own terminal verdict: the family owes the
        # pipeline nothing and the next candidate advances.
        self._write_family(FRESH, with_attempt=True, attempt_age_minutes=1, terminal="DONE",
                           verdict="PASS", attempt_round=FRESH + "-r2")
        self._write_verdict(FRESH, "TECHNICAL_INCOMPLETE", round_id=FRESH + "-r2")
        res = self.run_round()
        self.assertEqual(res.action, "appended", res.reason)
        self.assertEqual(res.family_id, FAMILY_B)

    def test_foreign_round_verdict_never_releases_the_attempt(self):
        # Fail-closed ownership: a verdict file that is not *this* round's own - a foreign family_id, a
        # mismatched kanban_task_id, or a round_id naming another round - reads as missing, so the
        # family keeps holding instead of being closed by someone else's evidence.
        self._write_family(FRESH, with_attempt=True, attempt_age_minutes=1, terminal="DONE",
                           attempt_round=FRESH + "-r2")
        path = self._write_verdict(FRESH, "TECHNICAL_INCOMPLETE", round_id=FRESH + "-r2")
        for field, value in (("family_id", "fam-other-v1"), ("kanban_task_id", "t_other"),
                             ("round_id", FRESH + "-r1")):
            doc = {"family_id": FRESH, "kanban_task_id": "t_x", "round_id": FRESH + "-r2",
                   "verdict": "TECHNICAL_INCOMPLETE"}
            doc[field] = value
            path.write_text(json.dumps(doc))
            res = self.run_round()
            self.assertEqual((res.action, res.outcome), ("noop", "running"), (field, res.reason))
            self.assertEqual(self.fake.calls, [])
        self.assertFalse((self.root / FAMILY_B).exists())

    def test_non_terminal_verdict_token_cannot_bypass(self):
        # A non-terminal token is not a release: the family still holds the pipeline with its live run.
        self._write_family(FRESH, with_attempt=True, attempt_age_minutes=1,
                           verdict="NEEDS_MORE_EVIDENCE")
        res = self.run_round()
        self.assertEqual((res.action, res.outcome), ("noop", "running"))

    def test_registered_without_attempt_retries_same_family(self):
        self.run_round()
        self.assertEqual(len(self.fake.launches()), 1)
        again = self.run_round()
        self.assertEqual((again.family_id, again.outcome), (FAMILY_B, "advanced"))
        self.assertEqual([c[1] for c in self.fake.launches()], [FAMILY_B, FAMILY_B])

    def test_created_body_keeps_candidate_bytes_and_carries_the_lifecycle_footer(self):
        self.run_round()
        body = self.fake.body()
        self.assertIn(V13_BODY, body)
        self.assertIn(h.LIFECYCLE_FOOTER.strip(), body)
        self.assertIn("TECHNICAL_INCOMPLETE", body)

    def test_footer_carries_the_bounded_prerequisite_evidence_rule(self):
        self.assertIn("TECHNICAL_INCOMPLETE", h.LIFECYCLE_FOOTER)
        self.assertIn("prerequisite", h.LIFECYCLE_FOOTER)

    def test_launch_failure_stays_retryable_and_never_advances_next_candidate(self):
        self.fake.error = "simulated launcher failure"
        self._write_pool([candidate(), candidate(family="fam-next-v1",
                                               fingerprint_input="unique-next")])
        res = self.run_round()
        self.assertEqual((res.action, res.outcome, res.finding_key),
                         ("finding", "finding", "agent_launch_failed"))
        self.assertTrue((self.root / FAMILY_B / "family.json").is_file())
        self.assertEqual(self.run_round().family_id, FAMILY_B)
        self.assertFalse((self.root / "fam-next-v1").exists())
        self.fake.error = None
        self.assertEqual(self.run_round().family_id, FAMILY_B)
        self.assertFalse((self.root / "fam-next-v1").exists())
        self._write_verdict(FAMILY_B, "TECHNICAL_INCOMPLETE")
        self.assertEqual(self.run_round().family_id, "fam-next-v1")

    def test_registered_candidate_mutation_is_fail_closed(self):
        self.run_round()
        self._write_pool([candidate(body=V13_BODY + "modified")])
        res = self.run_round()
        self.assertEqual(res.finding_key, "registered_candidate_changed")
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
        keys = [c[1] for c in self.fake.launches()]
        self.assertEqual(keys, self.order[1:])
        self.assertNotIn(self.order[0], keys)

    def test_unfinished_direct_family_retries_itself_not_next(self):
        self.assertEqual(self.run_round().family_id, self.order[1])
        again = self.run_round()
        self.assertEqual((again.action, again.outcome, again.family_id),
                         ("appended", "advanced", self.order[1]))
        self.assertEqual([c[1] for c in self.fake.launches()], [self.order[1]] * 2)

    def test_consumed_b_v1_is_never_recreated(self):
        res = self.run_round()
        self.assertEqual(res.family_id, self.order[1])
        self.assertFalse((self.root / h.HANDOFF_DIRNAME / h.LOG_FILENAME).exists())
        doc = json.loads((self.root / self.order[0] / "family.json").read_text())
        self.assertEqual(doc["family_id"], self.order[0])
        self.assertEqual(doc["kanban_task_id"], "t_B1")

    def test_selected_workspace_is_preserved_future_invalid_workspace_does_not_gate(self):
        with tempfile.TemporaryDirectory(prefix="qrp-candidate-workspace-") as workspace:
            self._write_pool([
                candidate(family=self.order[1], fingerprint_input="workspace-a",
                          workspace_path=workspace),
                candidate(family=self.order[2], fingerprint_input="workspace-b",
                          workspace_path="/missing/future/candidate"),
            ])
            res = self.run_round()
            self.assertEqual((res.action, res.family_id), ("appended", self.order[1]))
            self.assertEqual(self.fake.launches()[0][5], workspace)
            self._write_verdict(self.order[1], "TECHNICAL_INCOMPLETE")
            next_tick = self.run_round()
            self.assertEqual(next_tick.finding_key, "unsupported_execution_target")
            self.assertFalse((self.root / self.order[2] / "family.json").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
