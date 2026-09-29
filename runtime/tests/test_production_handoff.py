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
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

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
        self.prep_calls = []
        self.prep_result = (23456, None, False)
        self._real_launch = h.launch_agent
        self._real_prepare = h.launch_preparation_agent
        self._real_sh = h.sh
        h.launch_agent = self.fake
        def fake_prepare(results_root, cand, pool_path):
            self.prep_calls.append((results_root, dict(cand), pool_path))
            return self.prep_result
        h.launch_preparation_agent = fake_prepare
        h.sh = lambda *_a, **_k: self.fail("production must not call any CLI/board reader")
        self._write_family(FAMILY_A, "t_A")
        self._write_pool([candidate()])

    def tearDown(self):
        h.sh = self._real_sh
        h.launch_agent = self._real_launch
        h.launch_preparation_agent = self._real_prepare
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

    def _write_execution_blocker(self, family, **over):
        path = Path(self.root) / family / "execution-blocker.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        doc = {"schema_version": 1, "document_kind": "prelaunch_execution_blocker",
               "family_id": family, "status": "BLOCKED_BEFORE_ROUND_FREEZE",
               "required_human_input": "resolve the stated prerequisite question",
               "detected_at_utc": "2026-09-13T00:00:00Z",
               "round_id": None, "run_id": None,
               "attempt_launched": False, "qlib_launched": False}
        doc.update(over)
        path.write_text(json.dumps(doc) + "\n")
        return path

    def _write_blocker_verdict(self, family, round_id, **over):
        doc = {"schema_version": 1, "family_id": family, "round_id": round_id,
               "run_id": round_id + "-u1", "verdict": "TECHNICAL_INCOMPLETE"}
        doc.update(over)
        return self._write_verdict(family, doc["verdict"], doc=doc, round_id=round_id)

    def _write_no_compute_terminal(self, family, round_id=None, verdict_over=None,
                                   spec_over=None):
        round_id = round_id or family + "-r1"
        round_dir = Path(self.root) / family / "rounds" / round_id
        round_dir.mkdir(parents=True, exist_ok=True)
        spec = {"schema_version": 1, "family_id": family, "round_id": round_id,
                "created_at_utc": "2026-09-13T00:01:00Z",
                "attempts": {"launched": 0, "run_specs": 0, "terminal_sentinels": 0}}
        spec.update(spec_over or {})
        spec_path = round_dir / "round-spec.json"
        spec_path.write_text(json.dumps(spec) + "\n")
        verdict = {
            "schema_version": 1, "family_id": family, "round_id": round_id,
            "run_id": None, "verdict": "TECHNICAL_INCOMPLETE",
            "performance_claimable": False, "decided_at_utc": "2026-09-13T00:02:00Z",
            "attempts": {"launched": 0, "run_specs": 0, "terminal_sentinels": 0,
                         "run_spec": None, "terminal_sentinel": None, "attempt_dir": None},
            "failure": {"last_run_id": None}, "evidence_run_ids": [],
            "coverage": {"cells_computed": 0},
        }
        verdict.update(verdict_over or {})
        verdict_path = round_dir / "verdict.json"
        verdict_path.write_text(json.dumps(verdict) + "\n")
        return round_dir, spec_path, verdict_path

    def args(self, **over):
        # Historical behavior tests exercise rollback explicitly; prepared-mode tests opt in below.
        base = dict(results_root=str(self.root), board=BOARD, pool=None, detector="handoff",
                    dry_run=False, json=False, quiet_noop=True,
                    require_prepared_execution=False)
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
    def _pass_attempt(self, age_minutes=360):
        self._write_family("fam-passed-v1", with_attempt=True, attempt_age_minutes=age_minutes,
                           terminal="DONE", verdict="PASS")
        return Path(self.root) / "fam-passed-v1" / "rounds" / "fam-passed-v1-r1" / \
            "attempts" / "fam-passed-v1-r1-u1"

    def test_pass_finalizes_bundle_index_leaderboard_before_candidate_advance(self):
        attempt = self._pass_attempt()
        commands = []
        def fake_sh(cmd, **_kwargs):
            commands.append(cmd)
            if "--check" in cmd and "survivor-bundle.json" not in " ".join(cmd):
                if "survivor_leaderboard.py" in cmd and any(
                        "survivor_leaderboard.py" in prior and "--check" not in prior
                        for prior in commands[:-1]):
                    return 0, "", ""
                return 1, "", "stale"
            return 0, "", ""
        h.sh = fake_sh
        res = self.run_round()
        self.assertEqual(res.action, "appended", res.reason)
        self.assertEqual([Path(cmd[1]).name for cmd in commands if len(cmd) > 1],
                         ["survivor_bundle.py", "survivor_index.py", "survivor_index.py",
                          "survivor_leaderboard.py", "survivor_leaderboard.py"])
        self.assertEqual(commands[0][commands[0].index("--attempt-dir") + 1], str(attempt))
        self.assertIn("leaderboard", commands[-1])
        self.assertNotIn("--check", commands[-1])

    def test_pass_finalization_failure_finds_and_does_not_advance(self):
        self._pass_attempt()
        h.sh = lambda *_a, **_k: (1, "", "failed")
        res = self.run_round()
        self.assertEqual(res.action, "finding")
        self.assertEqual(res.finding_key, "post_survivor_finalize_failed")
        self.assertFalse((self.root / FAMILY_B).exists())

    def test_clean_post_survivor_checks_do_not_write(self):
        attempt = self._pass_attempt()
        (attempt.parents[1] / "survivor-bundle.json").write_text("{}")
        commands = []
        h.sh = lambda cmd, **_k: (commands.append(cmd) or (0, "", ""))
        res = self.run_round()
        self.assertEqual(res.action, "appended", res.reason)
        self.assertTrue(commands)
        self.assertTrue(all("--check" in cmd for cmd in commands))

    def test_dangling_survivor_bundle_symlink_fails_closed_without_replacement(self):
        attempt = self._pass_attempt()
        bundle_path = attempt.parents[1] / "survivor-bundle.json"
        bundle_path.symlink_to("missing-survivor-bundle-target.json")
        commands = []
        h.sh = lambda cmd, **_k: (commands.append(cmd) or (1, "", "missing or invalid bundle"))

        res = self.run_round()

        self.assertTrue(commands)
        self.assertIn("--check", commands[0])
        self.assertIn("survivor_bundle.py", commands[0][1])
        self.assertEqual(commands[0].count("--check"), 1)
        self.assertEqual(commands[0].count("--attempt-dir"), 1)
        self.assertEqual(res.action, "finding")
        self.assertEqual(res.finding_key, "post_survivor_finalize_failed")
        self.assertTrue(bundle_path.is_symlink())
        self.assertFalse(bundle_path.exists())
        self.assertEqual(os.readlink(str(bundle_path)), "missing-survivor-bundle-target.json")

    def _install_legacy_frozen_prompt(self, create_task=True):
        self.assertEqual(self.run_round().action, "appended")
        cand = candidate()
        cand["_body"] = V13_BODY
        current_footer = h.LIFECYCLE_FOOTER
        legacy_footer = "\n\n---\nLIFECYCLE FOOTER（system-owned；pre-change）\n- legacy rule.\n"
        try:
            h.LIFECYCLE_FOOTER = legacy_footer
            prompt = h.agent_prompt(cand, str(self.root))
        finally:
            h.LIFECYCLE_FOOTER = current_footer

        family_path = self.root / FAMILY_B / "family.json"
        doc = json.loads(family_path.read_text())
        doc["handoff"]["body_sha256"] = h.fingerprint(V13_BODY + legacy_footer)
        family_path.write_text(json.dumps(doc) + "\n")
        task_path = self.root / FAMILY_B / "agent-task.md"
        if create_task:
            task_path.write_bytes(prompt.encode("utf-8"))
        return prompt, task_path

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

    def test_old_round_late_terminal_write_cannot_hide_live_follow_up_round(self):
        # Live production race: r2 was materialized first, then host-side disposition closed r1.
        # The r1 terminal/verdict write is newer by mtime, but round ordinal is the scheduling truth:
        # r2 remains current and must keep the family from releasing the next candidate.
        self._write_family(FRESH)
        r2 = self._write_attempt(FRESH, round_id=FRESH + "-r2", age_minutes=5)
        r1 = self._write_attempt(FRESH, round_id=FRESH + "-r1", terminal="INCOMPLETE",
                                 age_minutes=1)
        self._write_verdict(FRESH, "TECHNICAL_INCOMPLETE", round_id=FRESH + "-r1")
        # Make the old r1 visibly newer on disk than r2 to reproduce the live bug.
        now = time.time()
        for path in [r1] + sorted(r1.rglob("*")):
            os.utime(str(path), (now, now))
        self.assertLess(h.attempt_activity(r2), h.attempt_activity(r1))

        res = self.run_round()
        self.assertEqual((res.action, res.outcome), ("noop", "running"))
        self.assertEqual(res.detail["active_family"], FRESH)
        self.assertIn(FRESH + "-r2-u1", res.reason)
        self.assertEqual(self.fake.calls, [])
        self.assertFalse((self.root / FAMILY_B).exists())

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
        first = self.run_round()
        self.assertEqual((first.action, first.outcome), ("appended", "advanced"))
        self.assertEqual(len(self.fake.launches()), 1)
        again = self.run_round()
        self.assertEqual((again.action, again.outcome, again.family_id),
                         ("retried", "running", FAMILY_B))
        self.assertEqual([c[1] for c in self.fake.launches()], [FAMILY_B, FAMILY_B])

    def test_dry_run_of_registered_family_is_not_a_pipeline_advance(self):
        self.run_round()
        retry = self.run_round(dry_run=True)
        self.assertEqual((retry.action, retry.outcome), ("would_retry", "running"))
        self.assertEqual(len(self.fake.launches()), 1)

    def test_created_body_keeps_candidate_bytes_and_carries_the_lifecycle_footer(self):
        body = (V13_BODY + "## Execution costs\n"
                "Funding 0.01% per 8h, fees 0.04% per side, and slippage 5 bps.\n")
        self._write_pool([candidate(body=body)])
        self.run_round()
        prompt = self.fake.body()
        marker = "--- REVIEWED CANDIDATE BODY (verbatim, followed by system lifecycle rules) ---\n"
        self.assertLess(prompt.index(marker), prompt.index(body))
        self.assertTrue(prompt.endswith(body + h.LIFECYCLE_FOOTER))

    def test_footer_carries_the_bounded_prerequisite_evidence_rule(self):
        self.assertIn("TECHNICAL_INCOMPLETE", h.LIFECYCLE_FOOTER)
        self.assertIn("prerequisite", h.LIFECYCLE_FOOTER)
        self.assertIn(
            "只有核心 signal 所必需的 data type／field 在本機完全不存在",
            h.LIFECYCLE_FOOTER,
        )
        self.assertIn(
            "funding、fee、slippage 若僅作 execution-cost／accounting inputs，而非 registered core "
            "signal/mechanism 的必要內容，則不是 core-signal prerequisites；"
            "execution fees／slippage 依 registered cost assumptions 及 canonical instrument metadata 處理；"
            "derivative funding cost 只使用 canonical official observations，缺少 official "
            "observation 時該 interval 的 funding "
            "cost 為 zero，並須揭露 observation coverage，絕不可用 modeled rows 替代。單獨的 "
            "execution-cost coverage gaps 不得作為 prelaunch TECHNICAL_INCOMPLETE 理由。反之，若 "
            "funding、fee、slippage 本身是 registered core signal/mechanism 的一部分，仍屬 core data，"
            "適用既有 core prerequisite 規則。",
            h.LIFECYCLE_FOOTER,
        )

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

    def test_pre_change_frozen_prompt_retries_after_footer_evolution(self):
        frozen_prompt, task_path = self._install_legacy_frozen_prompt()
        self.assertNotEqual(frozen_prompt, h.agent_prompt(candidate(), str(self.root)))
        frozen_bytes = task_path.read_bytes()
        family_path = self.root / FAMILY_B / "family.json"
        family_bytes = family_path.read_bytes()

        res = self.run_round()

        self.assertEqual((res.action, res.outcome), ("retried", "running"))
        self.assertEqual(self.fake.body(), frozen_prompt)
        self.assertEqual(task_path.read_bytes(), frozen_bytes)
        self.assertEqual(family_path.read_bytes(), family_bytes)

    def test_legacy_frozen_prompt_rejects_changed_semantic_fingerprint(self):
        self._install_legacy_frozen_prompt()
        changed = candidate(fingerprint_input="changed fingerprint input")
        self._write_pool([changed])

        res = self.run_round()

        self.assertEqual(res.finding_key, "registered_candidate_changed")
        self.assertEqual(len(self.fake.calls), 1)

    def test_legacy_frozen_prompt_rejects_changed_candidate_body(self):
        _, task_path = self._install_legacy_frozen_prompt()
        self._write_pool([candidate(body=V13_BODY + "changed")])

        res = self.run_round()

        self.assertEqual(res.finding_key, "registered_candidate_changed")
        self.assertEqual(len(self.fake.calls), 1)
        self.assertTrue(task_path.is_file())

    def test_legacy_frozen_prompt_rejects_hash_corruption(self):
        _, task_path = self._install_legacy_frozen_prompt()
        task_path.write_bytes(task_path.read_bytes() + b"corruption")

        res = self.run_round()

        self.assertEqual(res.finding_key, "registered_candidate_changed")
        self.assertEqual(len(self.fake.calls), 1)

    def test_missing_frozen_prompt_keeps_current_body_hash_check(self):
        _, task_path = self._install_legacy_frozen_prompt(create_task=False)

        res = self.run_round()

        self.assertEqual(res.finding_key, "registered_candidate_changed")
        self.assertEqual(len(self.fake.calls), 1)
        self.assertFalse(task_path.exists())


class TestFailClosed(Base):
    def _prepared(self, family=FAMILY_B, fingerprint_input="fam-b|w=2,4|1h|long/short",
                  manifest_over=None, round_over=None, run_over=None):
        family_dir = self.root / h.HANDOFF_DIRNAME / "prepared" / family
        round_id, run_id = family + "-r1", family + "-r1-u1"
        attempt = family_dir / "rounds" / round_id / "attempts" / run_id
        attempt.mkdir(parents=True)
        round_spec = {"schema_version": 1, "family_id": family, "round_id": round_id}
        round_spec.update(round_over or {})
        run_spec = {"schema_version": 1, "family_id": family, "round_id": round_id,
                    "run_id": run_id,
                    "script": {"path": "/scripts/strategy.py", "sha256": "sha256:" + "1" * 64}}
        run_spec.update(run_over or {})
        round_bytes = json.dumps(round_spec, sort_keys=True, separators=(",", ":")).encode()
        run_bytes = json.dumps(run_spec, sort_keys=True, separators=(",", ":")).encode()
        (family_dir / "rounds" / round_id / "round-spec.json").write_bytes(round_bytes)
        (attempt / "run-spec.json").write_bytes(run_bytes)
        manifest = {"schema_version": 1, "document_kind": h.PREPARED_EXECUTION_KIND,
                    "family_id": family,
                    "semantic_fingerprint": h.fingerprint(fingerprint_input),
                    "round_id": round_id, "run_id": run_id,
                    "round_spec_file": str(family_dir / "rounds" / round_id / "round-spec.json"),
                    "round_spec_sha256": h._sha256(round_bytes),
                    "run_spec_file": str(attempt / "run-spec.json"),
                    "run_spec_sha256": h._sha256(run_bytes)}
        manifest.update(manifest_over or {})
        execution_file = family_dir / "execution.json"
        execution_file.write_text(json.dumps(manifest, sort_keys=True))
        cand = candidate(family=family, fingerprint_input=fingerprint_input,
                         execution_file=str(execution_file))
        return cand, execution_file, manifest

    def _process_runner(self, family=FAMILY_B, stage="RUNNING_QLIB", container_rc=0,
                        state_doc=None, preflight_report=None, before_preflight=None):
        calls = []
        report = preflight_report or {"overall": "PASS", "launch_gate": "evaluated",
                                      "checks": [{"id": "P9", "status": "PASS"},
                                                 {"id": "P10", "status": "PASS"}]}
        round_id, run_id = family + "-r1", family + "-r1-u1"

        def run(cmd, cwd, env, timeout=h.PREPARED_EXECUTION_TIMEOUT_S):
            calls.append((list(cmd), cwd, dict(env), timeout))
            if cmd[0] == h.PREPARED_EXECUTION_PYTHON:
                if before_preflight is not None:
                    before_preflight(cmd, cwd, env)
                return (0 if report.get("overall") == "PASS" else 1,
                        json.dumps(report), "preflight fixture")
            if state_doc is not None or stage:
                attempt = self.root / family / "rounds" / round_id / "attempts" / run_id
                attempt.mkdir(parents=True, exist_ok=True)
                doc = state_doc or {"family_id": family, "round_id": round_id,
                                    "run_id": run_id, "stage": stage}
                (attempt / "state.json").write_text(json.dumps(doc))
            return container_rc, "container output", "container error"

        return calls, run

    def test_prepared_execution_mode_jit_prepares_legacy_candidate_without_family_or_qlib(self):
        res = self.run_round(require_prepared_execution=True)
        self.assertEqual((res.action, res.outcome), ("noop", "running"))
        self.assertEqual(res.family_id, FAMILY_B)
        self.assertTrue(res.detail["preparation_required"])
        self.assertEqual(res.detail["preparation_source"], h.PREPARATION_SOURCE)
        self.assertEqual(res.detail["preparation_pid"], 23456)
        self.assertEqual(len(self.prep_calls), 1)
        self.assertEqual(self.prep_calls[0][1]["family_id"], FAMILY_B)
        self.assertEqual(self.fake.calls, [])
        self.assertFalse((self.root / FAMILY_B).exists())

    def test_jit_preparation_busy_waits_without_duplicate_launch_or_family_mutation(self):
        self.prep_result = (None, None, True)
        res = self.run_round(require_prepared_execution=True)
        self.assertEqual((res.action, res.outcome), ("noop", "running"))
        self.assertIn("still owns candidate", res.reason)
        self.assertEqual(len(self.prep_calls), 1)
        self.assertFalse((self.root / FAMILY_B).exists())

    def test_jit_preparation_dry_run_launches_nothing(self):
        res = self.run_round(require_prepared_execution=True, dry_run=True)
        self.assertEqual((res.action, res.outcome), ("noop", "running"))
        self.assertTrue(res.detail["preparation_required"])
        self.assertEqual(self.prep_calls, [])
        self.assertFalse((self.root / FAMILY_B).exists())

    def test_preparation_prompt_forbids_compute_and_preserves_candidate_identity(self):
        cand = candidate(provenance={"reviewed_source": "legacy:source",
                                     "reviewed_wiki_path": "/wiki/quant/fam-b.md",
                                     "review_status": "PASS"})
        prompt = h.preparation_prompt(cand, str(self.root), str(self.root / h.HANDOFF_DIRNAME / h.POOL_FILENAME))
        self.assertIn("JIT PREPARATION ONLY", prompt)
        self.assertIn("do NOT run container exec qlib-run", prompt)
        self.assertIn("do NOT create %s/%s" % (self.root, FAMILY_B), prompt)
        self.assertIn("adding ONLY its validated absolute execution_file", prompt)
        self.assertIn(cand["fingerprint_input"], prompt)
        self.assertIn("Canonical reviewed research locator: /wiki/quant/fam-b.md", prompt)
        self.assertIn("provenance.reviewed_wiki_path when present", prompt)
        self.assertIn("source venue, quote currency, named symbols and source-universe breadth", prompt)
        self.assertIn("not an execution prerequisite", prompt)
        self.assertIn("complete legal local eligible universe", prompt)
        self.assertIn("core signal/model capability or required data type/field is absent", prompt)
        self.assertIn("dedicated current-family runner", prompt)
        self.assertIn("do not modify unrelated strategy runners or generic/shared engines", prompt)

    def test_prepared_execution_uses_fixed_container_command_and_freezes_identity(self):
        cand, _execution_file, _manifest = self._prepared()
        self._write_pool([cand])
        calls, runner = self._process_runner(
            before_preflight=lambda cmd, cwd, env: self.assertFalse(
                (self.root / FAMILY_B / "family.json").exists()))
        with patch.object(h, "_run_bounded_process", side_effect=runner):
            res = self.run_round(require_prepared_execution=True)

        self.assertEqual((res.action, res.outcome), ("appended", "advanced"))
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0][0], h.PREPARED_EXECUTION_PYTHON)
        self.assertEqual(calls[0][0][2:4], ["--launch", "--attempt-dir"])
        self.assertEqual(calls[0][2]["PATH"], "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin")
        attempt = "/results/%s/rounds/%s-r1/attempts/%s-r1-u1" % (FAMILY_B, FAMILY_B, FAMILY_B)
        self.assertEqual(calls[1][0], [
            "/usr/local/bin/container", "exec", "-d", "qlib-run", "/opt/venv/bin/python",
            "/scripts/strategy.py", "--run-spec", attempt + "/run-spec.json",
            "--attempt-dir", attempt])
        self.assertEqual(calls[1][2]["PATH"], "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin")
        self.assertEqual(self.fake.calls, [])
        family_doc = json.loads((self.root / FAMILY_B / "family.json").read_text())
        prepared, problem = h._prepared_execution(cand, self.args(require_prepared_execution=True))
        self.assertIsNone(problem)
        self.assertEqual(family_doc["handoff"]["prepared_execution"], prepared["identity"])
        self.assertEqual((self.root / FAMILY_B / "rounds" / (FAMILY_B + "-r1") /
                          "round-spec.json").read_bytes(), Path(prepared["round_spec_path"]).read_bytes())
        self.assertEqual((self.root / FAMILY_B / "rounds" / (FAMILY_B + "-r1") / "attempts" /
                          (FAMILY_B + "-r1-u1") / "run-spec.json").read_bytes(),
                         Path(prepared["run_spec_path"]).read_bytes())

    def test_prepared_execution_mismatch_fails_before_family_mutation(self):
        cand, execution_file, _manifest = self._prepared()
        doc = json.loads(execution_file.read_text())
        doc["semantic_fingerprint"] = "sha256:" + "0" * 64
        execution_file.write_text(json.dumps(doc))
        self._write_pool([cand])
        with patch.object(h, "_run_bounded_process") as direct:
            res = self.run_round(require_prepared_execution=True)
        self.assertEqual(res.finding_key, "prepared_execution_mismatch")
        direct.assert_not_called()
        self.assertFalse((self.root / FAMILY_B).exists())

    def test_manifest_rejects_argv_or_runner_extra_keys(self):
        for suffix, extra in (("argv", {"argv": ["/bin/sh", "-c", "true"]}),
                              ("runner", {"runner": "/tmp/runner.py"})):
            with self.subTest(extra=extra):
                family = "fam-extra-%s-v1" % suffix
                cand, execution_file, _manifest = self._prepared(family=family)
                doc = json.loads(execution_file.read_text())
                doc.update(extra)
                execution_file.write_text(json.dumps(doc))
                self._write_pool([cand])
                with patch.object(h, "_run_bounded_process") as direct:
                    res = self.run_round(require_prepared_execution=True)
                self.assertEqual(res.finding_key, "prepared_execution_invalid")
                direct.assert_not_called()
                self.assertFalse((self.root / family).exists())

    def test_spec_path_escape_and_symlink_are_rejected(self):
        cand, execution_file, _manifest = self._prepared()
        outside = self.root / "outside-round-spec.json"
        outside.write_text("{}")
        doc = json.loads(execution_file.read_text())
        doc["round_spec_file"] = str(outside)
        execution_file.write_text(json.dumps(doc))
        self._write_pool([cand])
        with patch.object(h, "_run_bounded_process") as direct:
            escaped = self.run_round(require_prepared_execution=True)
        self.assertEqual(escaped.finding_key, "prepared_execution_invalid")
        direct.assert_not_called()
        self.assertFalse((self.root / FAMILY_B).exists())

        family = "fam-symlink-v1"
        cand, execution_file, manifest = self._prepared(family=family)
        round_path = Path(manifest["round_spec_file"])
        real_path = round_path.with_suffix(".real")
        real_path.write_bytes(round_path.read_bytes())
        round_path.unlink()
        round_path.symlink_to(real_path)
        self._write_pool([cand])
        with patch.object(h, "_run_bounded_process") as direct:
            linked = self.run_round(require_prepared_execution=True)
        self.assertEqual(linked.finding_key, "prepared_execution_invalid")
        direct.assert_not_called()
        self.assertFalse((self.root / family).exists())

    def test_prepared_mode_ignores_legacy_worker_workspace_metadata(self):
        cand, _execution_file, _manifest = self._prepared()
        cand["workspace_path"] = str(self.root / "not-a-worker-directory.md")
        self._write_pool([cand])
        _calls, runner = self._process_runner()
        with patch.object(h, "_run_bounded_process", side_effect=runner):
            res = self.run_round(require_prepared_execution=True)
        self.assertEqual((res.action, res.outcome), ("appended", "advanced"))
        self.assertEqual(self.fake.calls, [])

    def test_empty_attempt_dir_or_missing_state_is_not_success(self):
        cand, _execution_file, _manifest = self._prepared()
        self._write_pool([cand])
        calls, runner = self._process_runner(stage="")
        with patch.object(h, "PREPARED_EVIDENCE_TIMEOUT_S", 0):
            with patch.object(h, "_run_bounded_process", side_effect=runner):
                res = self.run_round(require_prepared_execution=True)
        self.assertEqual(res.finding_key, "prepared_execution_no_evidence")
        attempt = self.root / FAMILY_B / "rounds" / (FAMILY_B + "-r1") / "attempts" / (FAMILY_B + "-r1-u1")
        self.assertTrue(attempt.is_dir())
        self.assertFalse((attempt / "state.json").exists())
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.fake.calls, [])

    def test_container_failure_materializes_attempt_and_blocks_next_candidate(self):
        cand, _execution_file, _manifest = self._prepared()
        next_cand = candidate(family="fam-next-v1", fingerprint_input="fam-next|w=3|1h|long")
        self._write_pool([cand, next_cand])
        calls, runner = self._process_runner(stage=None, container_rc=7)
        with patch.object(h, "_run_bounded_process", side_effect=runner):
            first = self.run_round(require_prepared_execution=True)
            attempt = (self.root / FAMILY_B / "rounds" / (FAMILY_B + "-r1") /
                       "attempts" / (FAMILY_B + "-r1-u1"))
            for path in (attempt, attempt / "run-spec.json"):
                os.utime(path, (1, 1))
            second = self.run_round(require_prepared_execution=True)
            self._write_verdict(FAMILY_B, "TECHNICAL_INCOMPLETE")
            after_c4 = self.run_round(require_prepared_execution=True)
        self.assertEqual(first.finding_key, "prepared_execution_failed")
        self.assertEqual((second.action, second.outcome), ("noop", "running"))
        self.assertEqual((after_c4.action, after_c4.outcome), ("noop", "running"))
        self.assertEqual(after_c4.family_id, "fam-next-v1")
        self.assertEqual(after_c4.detail["preparation_source"], h.PREPARATION_SOURCE)
        self.assertEqual(len(calls), 2)
        self.assertFalse((self.root / "fam-next-v1").exists())
        self.assertEqual(len(self.prep_calls), 1)
        self.assertEqual(self.prep_calls[0][1]["family_id"], "fam-next-v1")
        self.assertEqual(self.fake.calls, [])

    def test_hash_identity_and_ownership_mismatches_fail_before_mutation(self):
        cases = ("hash", "identity", "ownership")
        for suffix in cases:
            with self.subTest(case=suffix):
                family = "fam-mismatch-%s-v1" % suffix
                if suffix == "hash":
                    cand, _execution, _manifest = self._prepared(
                        family=family, manifest_over={"run_spec_sha256": "sha256:" + "0" * 64})
                elif suffix == "identity":
                    cand, _execution, _manifest = self._prepared(
                        family=family, run_over={"run_id": "foreign-run"})
                else:
                    cand, _execution, _manifest = self._prepared(
                        family=family, run_over={"kanban_task_id": "t_foreign"})
                self._write_pool([cand])
                with patch.object(h, "_run_bounded_process") as run:
                    res = self.run_round(require_prepared_execution=True)
                self.assertEqual(res.finding_key, "prepared_execution_invalid")
                run.assert_not_called()
                self.assertFalse((self.root / family).exists())

    def test_preflight_failure_is_before_family_write(self):
        cand, _execution, _manifest = self._prepared()
        self._write_pool([cand])
        report = {"overall": "FAIL", "launch_gate": "evaluated",
                  "checks": [{"id": "P9", "status": "PASS"},
                             {"id": "P10", "status": "FAIL"}]}
        calls, runner = self._process_runner(
            preflight_report=report,
            before_preflight=lambda _cmd, _cwd, _env: self.assertFalse(
                (self.root / FAMILY_B / "family.json").exists()))
        with patch.object(h, "_run_bounded_process", side_effect=runner):
            res = self.run_round(require_prepared_execution=True)
        self.assertEqual(res.finding_key, "prepared_execution_preflight_failed")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][2]["PATH"], "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin")
        self.assertFalse((self.root / FAMILY_B).exists())

    def test_malformed_or_foreign_state_is_not_success(self):
        cases = (
            ("stage", {"stage": "ALIEN"}),
            ("identity", {"family_id": "foreign-family"}),
        )
        for suffix, changed in cases:
            with self.subTest(case=suffix):
                family = "fam-state-%s-v1" % suffix
                cand, _execution, _manifest = self._prepared(family=family)
                self._write_pool([cand])
                state = {"family_id": family, "round_id": family + "-r1",
                         "run_id": family + "-r1-u1", "stage": "RUNNING_QLIB"}
                state.update(changed)
                calls, runner = self._process_runner(family=family, state_doc=state)
                with patch.object(h, "PREPARED_EVIDENCE_TIMEOUT_S", 0):
                    with patch.object(h, "_run_bounded_process", side_effect=runner):
                        res = self.run_round(require_prepared_execution=True)
                self.assertEqual(res.finding_key, "prepared_execution_no_evidence")
                self.assertEqual(len(calls), 2)
                shutil.rmtree(self.root / family)

    def test_artifact_ready_state_is_valid_launch_evidence(self):
        family = "fam-state-artifact-v1"
        cand, _execution, _manifest = self._prepared(family=family)
        self._write_pool([cand])
        _calls, runner = self._process_runner(family=family, stage="ARTIFACT_READY")
        with patch.object(h, "_run_bounded_process", side_effect=runner):
            res = self.run_round(require_prepared_execution=True)
        self.assertEqual((res.action, res.outcome), ("appended", "advanced"))

    def test_registered_retry_rejects_changed_manifest_and_spec_identity(self):
        for suffix, change in (("manifest", "manifest"), ("spec", "spec")):
            with self.subTest(change=change):
                family = "fam-retry-%s-v1" % suffix
                cand, execution_file, _manifest = self._prepared(family=family)
                prepared, problem = h._prepared_execution(
                    cand, self.args(require_prepared_execution=True))
                self.assertIsNone(problem)
                h.write_family_json(str(self.root), cand, prepared["identity"])
                doc = json.loads(execution_file.read_text())
                if change == "manifest":
                    execution_file.write_text(json.dumps(doc, indent=2))
                else:
                    run_path = Path(doc["run_spec_file"])
                    run_spec = json.loads(run_path.read_text())
                    run_spec["script"]["sha256"] = "sha256:" + "2" * 64
                    run_bytes = json.dumps(run_spec, sort_keys=True, separators=(",", ":")).encode()
                    run_path.write_bytes(run_bytes)
                    doc["run_spec_sha256"] = h._sha256(run_bytes)
                    execution_file.write_text(json.dumps(doc, sort_keys=True))
                self._write_pool([cand])
                with patch.object(h, "_run_bounded_process") as run:
                    res = self.run_round(require_prepared_execution=True)
                self.assertEqual(res.finding_key, "registered_candidate_changed")
                self.assertIn("prepared_execution_mismatch", res.reason or "")
                run.assert_not_called()
                self._write_verdict(family, "TECHNICAL_INCOMPLETE")

    def test_timeout_helper_kills_process_group(self):
        proc = Mock()
        proc.pid = 4321
        proc.returncode = -9
        proc.communicate.side_effect = [subprocess.TimeoutExpired(["fixed"], 1), (b"out", b"err")]
        with patch.object(h.subprocess, "Popen", return_value=proc) as popen:
            with patch.object(h.os, "killpg") as killpg:
                with patch.object(h.time, "sleep"):
                    with self.assertRaises(subprocess.TimeoutExpired):
                        h._run_bounded_process(["fixed"], "/", {}, timeout=1)
        self.assertEqual([call.args for call in killpg.call_args_list],
                         [(4321, h.signal.SIGTERM), (4321, h.signal.SIGKILL)])
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertNotIn("shell", popen.call_args.kwargs)

    def test_python_api_missing_prepared_flag_defaults_to_prepared(self):
        args = self.args()
        vars(args).pop("require_prepared_execution")
        res = h.round_once(args)
        self.assertEqual((res.action, res.outcome), ("noop", "running"))
        self.assertEqual(res.family_id, FAMILY_B)
        self.assertEqual(res.detail["preparation_source"], h.PREPARATION_SOURCE)
        self.assertEqual(len(self.prep_calls), 1)
        self.assertFalse((self.root / FAMILY_B).exists())
        self.assertEqual(self.fake.calls, [])

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

    def test_duplicate_incidents_reuse_family_verdict_within_one_call(self):
        terminal = "fam-terminal-duplicate-v1"
        unresolved = "fam-unresolved-duplicate-v1"
        distinct = "fam-distinct-unresolved-v1"
        self._write_family(terminal)
        self._write_verdict(terminal, "TECHNICAL_INCOMPLETE")
        self._write_family(unresolved)
        self._write_family(distinct)

        records = [
            ("inc-terminal-1", terminal, "run-terminal-1"),
            ("inc-unresolved-1", unresolved, "run-unresolved-1"),
            ("inc-terminal-2", terminal, "run-terminal-2"),
            ("inc-distinct-1", distinct, "run-distinct-1"),
            ("inc-unresolved-2", unresolved, "run-unresolved-2"),
        ]
        incident_path = self.root / h.INCIDENT_DIRNAME / h.INCIDENT_FILENAME
        incident_path.parent.mkdir(parents=True, exist_ok=True)
        incident_path.write_text("".join(json.dumps({
            "schema_version": 1, "incident_id": incident_id,
            "kind": "sentinel_ambiguous", "family_id": family_id,
            "round_id": family_id + "-r1", "run_id": run_id,
        }) + "\n" for incident_id, family_id, run_id in records))

        expected = [
            {"incident_id": "inc-unresolved-1", "kind": "sentinel_ambiguous",
             "family_id": unresolved,
             "why": "family has no terminal verdict and attempt run-unresolved-1 carries no clean terminal"},
            {"incident_id": "inc-distinct-1", "kind": "sentinel_ambiguous",
             "family_id": distinct,
             "why": "family has no terminal verdict and attempt run-distinct-1 carries no clean terminal"},
            {"incident_id": "inc-unresolved-2", "kind": "sentinel_ambiguous",
             "family_id": unresolved,
             "why": "family has no terminal verdict and attempt run-unresolved-2 carries no clean terminal"},
        ]
        real_token = h.family_verdict_token
        calls = []

        def spy(results_root, family_id, family_doc):
            calls.append(family_id)
            return real_token(results_root, family_id, family_doc)

        h.family_verdict_token = spy
        try:
            first = h.unresolved_incidents(str(self.root), h.read_families(str(self.root)))
            self.assertEqual(first, expected)
            self.assertEqual(calls, [terminal, unresolved, distinct])

            calls.clear()
            second = h.unresolved_incidents(str(self.root), h.read_families(str(self.root)))
            self.assertEqual(second, expected)
            self.assertEqual(calls, [terminal, unresolved, distinct])
        finally:
            h.family_verdict_token = real_token

    def test_active_prelaunch_blocker_is_normalized_and_stops_c3(self):
        self._write_family(BLOCKED, created="2026-09-13T00:00:00Z")
        retry_candidate = candidate(family=BLOCKED, fingerprint_input="blocked-family-v1")
        family_path = self.root / BLOCKED / "family.json"
        family_doc = json.loads(family_path.read_text())
        family_doc["semantic_fingerprint"] = h.fingerprint(retry_candidate["fingerprint_input"])
        family_doc["handoff"] = {
            "execution": h.DIRECT_MODE,
            "body_sha256": h.fingerprint(h.body_with_footer(retry_candidate)),
        }
        family_path.write_text(json.dumps(family_doc) + "\n")
        self._write_pool([retry_candidate])
        self._write_execution_blocker(BLOCKED)

        incidents = h.unresolved_incidents(str(self.root), h.read_families(str(self.root)))
        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0]["kind"], "prelaunch_execution_blocker")
        self.assertEqual(incidents[0]["family_id"], BLOCKED)
        self.assertIn("human input", incidents[0]["why"])

        res = self.run_round()
        self.assertEqual((res.action, res.outcome, res.finding_key),
                         ("finding", "incident", "unresolved_incident"))
        self.assertEqual(self.fake.calls, [])

    def test_invalid_or_nonmatching_prelaunch_blocker_is_not_an_incident(self):
        self._write_family(BLOCKED)
        self.assertEqual(h.unresolved_incidents(str(self.root), h.read_families(str(self.root))), [])
        blocker = self._write_execution_blocker(BLOCKED)

        invalid = [
            {"family_id": "fam-other-v1"},
            {"required_human_input": ""},
            {"round_id": "fam-blocked-r1"},
            {"run_id": "run1"},
            {"attempt_launched": True},
            {"qlib_launched": True},
            {"status": "RESOLVED"},
            {"document_kind": "other"},
        ]
        for changes in invalid:
            with self.subTest(changes=changes):
                blocker = self._write_execution_blocker(BLOCKED)
                doc = json.loads(blocker.read_text())
                doc.update(changes)
                blocker.write_text(json.dumps(doc) + "\n")
                self.assertEqual(h.unresolved_incidents(
                    str(self.root), h.read_families(str(self.root))), [])
        for missing_field in ("round_id", "run_id"):
            with self.subTest(missing_field=missing_field):
                blocker = self._write_execution_blocker(BLOCKED)
                doc = json.loads(blocker.read_text())
                del doc[missing_field]
                blocker.write_text(json.dumps(doc) + "\n")
                self.assertEqual(h.unresolved_incidents(
                    str(self.root), h.read_families(str(self.root))), [])
        blocker.write_text("{not json\n")
        self.assertEqual(h.unresolved_incidents(str(self.root), h.read_families(str(self.root))), [])

    def test_incomplete_verdict_identity_does_not_resolve_prelaunch_blocker(self):
        cases = (
            ("missing-round", ("round_id",), {}),
            ("missing-run", ("run_id",), {}),
            ("empty-run", (), {"run_id": ""}),
            ("missing-schema", ("schema_version",), {}),
            ("wrong-schema", (), {"schema_version": 2}),
            ("wrong-family", (), {"family_id": "fam-other-v1"}),
            ("wrong-round", (), {"round_id": "fam-other-r1"}),
            ("wrong-owner", (), {"kanban_task_id": "t_other"}),
        )
        for suffix, remove_keys, overrides in cases:
            with self.subTest(case=suffix):
                family = "fam-blocker-identity-" + suffix
                self._write_family(family)
                self._write_execution_blocker(family)
                round_id = family + "-r1"
                verdict = {"schema_version": 1, "family_id": family,
                           "round_id": round_id, "run_id": round_id + "-u1",
                           "verdict": "TECHNICAL_INCOMPLETE",
                           "decided_at_utc": "2026-09-14T00:00:00Z"}
                for key in remove_keys:
                    verdict.pop(key)
                verdict.update(overrides)
                self._write_verdict(family, verdict["verdict"], doc=verdict, round_id=round_id)
                incidents = h.unresolved_incidents(str(self.root), h.read_families(str(self.root)))
                self.assertIn(family, {item.get("family_id") for item in incidents})

    def test_verdict_mtime_change_alone_does_not_resolve_prelaunch_blocker(self):
        self._write_family(BLOCKED)
        round_id = BLOCKED + "-r1"
        verdict_path = self._write_blocker_verdict(
            BLOCKED, round_id, decided_at_utc="2026-09-12T23:00:00Z")
        blocker_path = self._write_execution_blocker(BLOCKED)
        before = verdict_path.read_bytes()
        later_mtime = max(blocker_path.stat().st_mtime, time.time()) + 60
        os.utime(str(verdict_path), (later_mtime, later_mtime))
        self.assertEqual(verdict_path.read_bytes(), before)

        incidents = h.unresolved_incidents(str(self.root), h.read_families(str(self.root)))
        self.assertIn(BLOCKED, {item.get("family_id") for item in incidents})

    def test_later_distinct_immutable_round_verdict_resolves_prelaunch_blocker(self):
        self._write_family(BLOCKED)
        self._write_blocker_verdict(
            BLOCKED, BLOCKED + "-r1", decided_at_utc="2026-09-12T23:00:00Z")
        later_round = BLOCKED + "-r2"
        verdict_path = self._write_blocker_verdict(
            BLOCKED, later_round, decided_at_utc="2026-09-14T00:00:00Z")
        verdict_bytes = verdict_path.read_bytes()
        blocker_path = self._write_execution_blocker(BLOCKED)
        self.assertEqual(verdict_path.read_bytes(), verdict_bytes)
        self.assertLess(verdict_path.stat().st_mtime, blocker_path.stat().st_mtime)

        self.assertEqual(h.unresolved_incidents(
            str(self.root), h.read_families(str(self.root))), [])

    def test_card_free_family_accepts_canonical_later_verdict_for_blocker_resolution(self):
        family = "fam-direct-blocked-v1"
        family_dir = self.root / family
        family_dir.mkdir(parents=True)
        (family_dir / "family.json").write_text(json.dumps({
            "schema_version": 1, "family_id": family,
            "handoff": {"execution": h.DIRECT_MODE},
        }) + "\n")
        self._write_execution_blocker(family)
        self._write_blocker_verdict(
            family, family + "-r2", decided_at_utc="2026-09-14T00:00:00Z")

        self.assertEqual(h.unresolved_incidents(
            str(self.root), h.read_families(str(self.root))), [])

    def test_no_compute_technical_incomplete_resolves_prelaunch_blocker(self):
        family = "fam-no-compute-blocked-v1"
        self._write_family(family)
        self._write_execution_blocker(family)
        round_dir, _, _ = self._write_no_compute_terminal(family)

        self.assertFalse((round_dir / "attempts").exists())
        self.assertEqual(h.unresolved_incidents(
            str(self.root), h.read_families(str(self.root))), [])

    def test_no_compute_terminal_preserves_direct_family_ownership_rules(self):
        family = "fam-direct-no-compute-blocked-v1"
        family_dir = self.root / family
        family_dir.mkdir(parents=True)
        (family_dir / "family.json").write_text(json.dumps({
            "schema_version": 1, "family_id": family,
            "handoff": {"execution": h.DIRECT_MODE},
        }) + "\n")
        self._write_execution_blocker(family)
        self._write_no_compute_terminal(family)

        self.assertEqual(h.unresolved_incidents(
            str(self.root), h.read_families(str(self.root))), [])

    def test_no_compute_terminal_negative_controls_fail_closed(self):
        missing = object()
        cases = (
            ("other-verdict", "verdict", "verdict", "PASS"),
            ("claimable-true", "verdict", "performance_claimable", True),
            ("claimable-missing", "verdict", "performance_claimable", missing),
            ("nonzero-launched", "verdict", "attempts.launched", 1),
            ("nonzero-run-specs", "verdict", "attempts.run_specs", 1),
            ("nonzero-sentinels", "verdict", "attempts.terminal_sentinels", 1),
            ("missing-launched", "verdict", "attempts.launched", missing),
            ("missing-run-specs", "verdict", "attempts.run_specs", missing),
            ("missing-sentinels", "verdict", "attempts.terminal_sentinels", missing),
            ("missing-run-spec", "verdict", "attempts.run_spec", missing),
            ("missing-terminal-sentinel", "verdict", "attempts.terminal_sentinel", missing),
            ("missing-attempt-dir", "verdict", "attempts.attempt_dir", missing),
            ("nonempty-evidence-ids", "verdict", "evidence_run_ids", ["run-1"]),
            ("failure-last-run-nonnull", "verdict", "failure.last_run_id", "run-1"),
            ("failure-last-run-missing", "verdict", "failure.last_run_id", missing),
            ("cells-computed-nonzero", "verdict", "coverage.cells_computed", 1),
            ("cells-computed-missing", "verdict", "coverage.cells_computed", missing),
            ("run-id-missing", "verdict", "run_id", missing),
            ("run-id-empty", "verdict", "run_id", ""),
            ("verdict-time-malformed", "verdict", "decided_at_utc", "not-a-time"),
            ("verdict-time-missing", "verdict", "decided_at_utc", missing),
            ("blocker-time-malformed", "blocker", "detected_at_utc", "not-a-time"),
            ("blocker-time-missing", "blocker", "detected_at_utc", missing),
            ("foreign-verdict-owner", "verdict", "kanban_task_id", "t_other"),
            ("round-spec-foreign", "round_spec", "family_id", "fam-other"),
            ("round-spec-wrong-round", "round_spec", "round_id", "other-r1"),
            ("round-spec-schema", "round_spec", "schema_version", 2),
            ("round-spec-time-before-blocker", "round_spec", "created_at_utc",
             "2026-09-12T23:59:59Z"),
            ("round-spec-time-after-verdict", "round_spec", "created_at_utc",
             "2026-09-13T00:02:01Z"),
            ("round-spec-missing-attempts", "round_spec", "attempts", missing),
            ("spec-nonzero-launched", "round_spec", "attempts.launched", 1),
            ("spec-nonzero-run-specs", "round_spec", "attempts.run_specs", 1),
            ("spec-nonzero-sentinels", "round_spec", "attempts.terminal_sentinels", 1),
            ("spec-missing-launched", "round_spec", "attempts.launched", missing),
            ("spec-missing-run-specs", "round_spec", "attempts.run_specs", missing),
            ("spec-missing-sentinels", "round_spec", "attempts.terminal_sentinels", missing),
            ("round-spec-missing", "remove_round_spec", None, None),
            ("round-spec-malformed", "malformed_round_spec", None, None),
            ("attempt-child-exists", "attempt_child", None, None),
        )
        for label, target, key, value in cases:
            with self.subTest(case=label):
                family = "fam-no-compute-negative-" + label
                self._write_family(family)
                blocker_path = self._write_execution_blocker(family)
                round_dir, spec_path, verdict_path = self._write_no_compute_terminal(family)
                if target == "attempt_child":
                    (round_dir / "attempts" / "attempt-u1").mkdir(parents=True)
                elif target in ("remove_round_spec", "malformed_round_spec"):
                    if target == "remove_round_spec":
                        spec_path.unlink()
                    else:
                        spec_path.write_text("{not json\n")
                else:
                    path = {"blocker": blocker_path, "round_spec": spec_path,
                            "verdict": verdict_path}[target]
                    doc = json.loads(path.read_text())
                    parts = (key or "").split(".")
                    parent = doc
                    for part in parts[:-1]:
                        parent = parent[part]
                    if value is missing:
                        parent.pop(parts[-1], None)
                    else:
                        parent[parts[-1]] = value
                    path.write_text(json.dumps(doc) + "\n")
                incidents = h.unresolved_incidents(
                    str(self.root), h.read_families(str(self.root)))
                self.assertIn(family, {item.get("family_id") for item in incidents})

    def test_missing_or_malformed_blocker_or_verdict_timestamps_fail_closed(self):
        cases = (
            ("missing-detected", "missing", "2026-09-14T00:00:00Z"),
            ("malformed-detected", "not-a-time", "2026-09-14T00:00:00Z"),
            ("missing-decided", "2026-09-13T00:00:00Z", None),
            ("malformed-decided", "2026-09-13T00:00:00Z", "not-a-time"),
            ("equal-times", "2026-09-13T00:00:00Z", "2026-09-13T00:00:00Z"),
        )
        for suffix, detected, decided in cases:
            with self.subTest(case=suffix):
                family = "fam-blocker-time-" + suffix
                self._write_family(family)
                blocker_path = self._write_execution_blocker(family)
                blocker_doc = json.loads(blocker_path.read_text())
                if detected == "missing":
                    blocker_doc.pop("detected_at_utc")
                else:
                    blocker_doc["detected_at_utc"] = detected
                blocker_path.write_text(json.dumps(blocker_doc) + "\n")
                round_id = family + "-r1"
                verdict = {"schema_version": 1, "family_id": family,
                           "round_id": round_id, "run_id": round_id + "-u1",
                           "verdict": "TECHNICAL_INCOMPLETE"}
                if decided is not None:
                    verdict["decided_at_utc"] = decided
                self._write_verdict(family, verdict["verdict"], doc=verdict, round_id=round_id)
                incidents = h.unresolved_incidents(str(self.root), h.read_families(str(self.root)))
                self.assertIn(family, {item.get("family_id") for item in incidents})

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

    def test_main_defaults_to_jit_preparation_and_never_launches_legacy_production_agent(self):
        rc, out, err = self.run_main("--json")
        self.assertEqual(rc, 0)
        record = json.loads(out)
        self.assertEqual((record["action"], record["outcome"]), ("noop", "running"))
        self.assertEqual(record["family_id"], FAMILY_B)
        self.assertEqual(record["detail"]["preparation_source"], h.PREPARATION_SOURCE)
        self.assertEqual(len(self.prep_calls), 1)
        self.assertEqual(self.fake.calls, [])
        self.assertFalse((self.root / FAMILY_B).exists())

    def test_append_is_logged_by_main(self):
        rc, out, err = self.run_main("--legacy-agent-dispatch", "--json")
        self.assertEqual(rc, 0)
        record = json.loads(out)
        self.assertEqual((record["action"], record["outcome"]), ("appended", "advanced"))
        self.assertIn("outcome=advanced", err)
        log = (self.root / h.HANDOFF_DIRNAME / h.LOG_FILENAME).read_text().strip().splitlines()
        self.assertEqual(json.loads(log[-1])["action"], "appended")

    def test_retry_is_logged_as_running_without_claiming_an_advance(self):
        self.run_main("--legacy-agent-dispatch", "--json")
        rc, out, err = self.run_main("--legacy-agent-dispatch", "--json")
        self.assertEqual(rc, 0)
        retry = json.loads(out)
        self.assertEqual((retry["action"], retry["outcome"]), ("retried", "running"))
        self.assertIn("outcome=running", err)
        log = [json.loads(line) for line in
               (self.root / h.HANDOFF_DIRNAME / h.LOG_FILENAME).read_text().splitlines()]
        self.assertEqual([row["action"] for row in log], ["appended", "retried"])

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
                         ("retried", "running", self.order[1]))
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
