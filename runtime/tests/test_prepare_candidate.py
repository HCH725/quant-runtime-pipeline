#!/usr/bin/env python3
"""Logic-level checks for runtime/prepare_candidate.py (contract 14.4 deterministic preparation).

Each test owns a temp results root and a temp host-scripts mirror; the Hermes launcher and the staged
P1-P10 preflight are the only substituted calls, so promotion decisions run against real files, a real
kernel lease, a real atomic-rewrite path, and a real bounded unittest invocation:

  * the backlog head only: one head per run, FIFO order preserved, head never skipped;
  * nothing staged -> exactly one bounded quant-preparation session, pool and backlog untouched;
  * a busy preparation lease or a held handoff lease -> waiting, never a duplicate launch;
  * promotion is agent-free and exact: the candidate bytes (+ execution_file) land in the pool, the
    backlog head is removed, and both writes are read back;
  * every invalid/intermediate state fails closed with the pool and the backlog untouched (invalid
    manifest, missing/failing focused test, P1-P10 failure, tampered outcome, identity mismatch);
  * retries are idempotent: an already-consumed candidate only drops the backlog head, and a published
    clear-absence terminal is reused instead of republished;
  * a valid clear-absence outcome publishes the no-compute terminal, appends consumed history to the
    pool and drops the head.

Run: python3 runtime/tests/test_prepare_candidate.py     (stdlib unittest, no dependencies)
"""
import argparse
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

RUNTIME = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME))
import production_handoff as h  # noqa: E402
import prepare_candidate as p  # noqa: E402

FAMILY_A = "fam-prep-a-v1"
FAMILY_B = "fam-prep-b-v1"
SCRIPT_NAME = "360_probe_run.py"
SCRIPT_STEM = SCRIPT_NAME[:-len(".py")]


def backlog_candidate(family, fingerprint_input=None, **over):
    cand = {
        "family_id": family,
        "title": "Production Candidate — %s" % family,
        "fingerprint_input": (fingerprint_input if fingerprint_input is not None
                              else "%s|w=2,4|1h|long" % family),
        "card_body_file": "bodies/%s.md" % family,
        "workspace_path": h.DEFAULT_WORKSPACE,
        "assignee": "default",
        "priority": 100,
        "goal_mode": True,
        "goal_max_turns": 20,
        "parent_family": None,
        "lineage_note": "canonical intake backfill",
        "provenance": {"reviewed_source": "/Users/hong/.hermes/wiki/quant/%s.md" % family,
                       "review_status": "research-only"},
    }
    cand.update(over)
    return cand


class Base(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="qrp-prepare-test-"))
        self.scripts = Path(tempfile.mkdtemp(prefix="qrp-prepare-scripts-"))
        (self.scripts / "tests").mkdir()
        self.handoff = Path(self.root) / h.HANDOFF_DIRNAME
        self.launch_calls = []
        self.launch_result = (31337, None, False)
        self.preflight_calls = []
        self.preflight_problem = None
        self._real_prepare = h.launch_preparation_agent
        self._real_preflight = h._run_staged_preflight
        h.launch_preparation_agent = self._fake_launch
        h._run_staged_preflight = self._fake_preflight
        self._scripts_patch = patch.object(p, "CONTAINER_SCRIPTS_HOST", str(self.scripts))
        self._scripts_patch.start()

    def tearDown(self):
        self._scripts_patch.stop()
        h.launch_preparation_agent = self._real_prepare
        h._run_staged_preflight = self._real_preflight
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.scripts, ignore_errors=True)

    def _fake_launch(self, results_root, cand, pool_path):
        self.launch_calls.append((results_root, dict(cand), pool_path))
        return self.launch_result

    def _fake_preflight(self, prepared, family_id):
        self.preflight_calls.append(dict(prepared, identity=None))
        return self.preflight_problem

    # --- fixtures ---------------------------------------------------------
    def _write_backlog(self, cands, doc_over=None):
        self.handoff.mkdir(parents=True, exist_ok=True)
        for cand in cands:
            rel = cand.get("card_body_file") if isinstance(cand, dict) else None
            if rel:
                path = self.handoff / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("# %s\nDCA PARAMETER DOMAIN\nCOHORT SURVIVOR SEMANTICS\n"
                                % cand["family_id"])
        doc = {"schema_version": 1, "contract_section": "14.4", "note": "backlog",
               "updated_at_utc": "2026-09-30T11:37:46Z", "candidates": cands}
        doc.update(doc_over or {})
        path = self.handoff / p.BACKLOG_FILENAME
        path.write_text(json.dumps(doc, indent=2) + "\n")
        return path

    def _write_pool(self, cands, doc_over=None):
        self.handoff.mkdir(parents=True, exist_ok=True)
        doc = {"schema_version": 1, "contract_section": "14.4", "note": "pool",
               "updated_at_utc": "2026-09-30T11:37:46Z", "candidates": cands}
        doc.update(doc_over or {})
        path = self.handoff / h.POOL_FILENAME
        path.write_text(json.dumps(doc, indent=2) + "\n")
        return path

    def _backlog(self):
        return json.loads((self.handoff / p.BACKLOG_FILENAME).read_text())

    def _pool(self):
        return json.loads((self.handoff / h.POOL_FILENAME).read_text())

    def _body_text(self, family):
        """The exact card body `_write_backlog` stages for this family."""
        return "# %s\nDCA PARAMETER DOMAIN\nCOHORT SURVIVOR SEMANTICS\n" % family

    def _prepared_package(self, cand, manifest_over=None, run_over=None, test_body=None,
                          test_file=True, manifest_name=None):
        """A staged prepared package plus its focused test inside the temp host-scripts mirror."""
        family = cand["family_id"]
        prepared_dir = self.handoff / "prepared" / family
        round_id, run_id = family + "-r1", family + "-r1-u1"
        attempt = prepared_dir / "rounds" / round_id / "attempts" / run_id
        attempt.mkdir(parents=True, exist_ok=True)
        round_spec = {"schema_version": 1, "family_id": family, "round_id": round_id}
        run_spec = {"schema_version": 1, "family_id": family, "round_id": round_id, "run_id": run_id,
                    "script": {"path": "/scripts/%s" % SCRIPT_NAME,
                               "sha256": "sha256:" + "1" * 64}}
        run_spec.update(run_over or {})
        round_bytes = json.dumps(round_spec, sort_keys=True, separators=(",", ":")).encode()
        run_bytes = json.dumps(run_spec, sort_keys=True, separators=(",", ":")).encode()
        (prepared_dir / "rounds" / round_id / "round-spec.json").write_bytes(round_bytes)
        (attempt / "run-spec.json").write_bytes(run_bytes)
        manifest = {"schema_version": 1, "document_kind": h.PREPARED_EXECUTION_KIND,
                    "family_id": family,
                    "semantic_fingerprint": h.fingerprint(cand["fingerprint_input"]),
                    "round_id": round_id, "run_id": run_id,
                    "round_spec_file": str(prepared_dir / "rounds" / round_id / "round-spec.json"),
                    "round_spec_sha256": h._sha256(round_bytes),
                    "run_spec_file": str(attempt / "run-spec.json"),
                    "run_spec_sha256": h._sha256(run_bytes)}
        manifest.update(manifest_over or {})
        manifest_path = prepared_dir / (manifest_name or h.PREPARED_MANIFEST_FILENAME)
        manifest_path.write_text(json.dumps(manifest, sort_keys=True))
        if test_file:
            test_path = Path(self.scripts) / "tests" / ("test_%s.py" % SCRIPT_STEM)
            test_path.write_text(test_body or (
                "import unittest\n\n\nclass Probe(unittest.TestCase):\n"
                "    def test_ok(self):\n        self.assertTrue(True)\n"))
        return prepared_dir, manifest_path

    def _clear_absence_outcome(self, cand, tamper=None):
        config = self.root / "canonical-CONFIG.json"
        schema = self.root / "canonical-SCHEMA.md"
        config.write_bytes(b'{"datasets":[]}\n')
        schema.write_bytes(b"# canonical schema\n")
        body = self._body_text(cand["family_id"])
        outcome = {
            "schema_version": 1, "document_kind": "jit_preparation_clear_absence",
            "family_id": cand["family_id"],
            "semantic_fingerprint": h.fingerprint(cand["fingerprint_input"]),
            "body_sha256": h.fingerprint(body + h.LIFECYCLE_FOOTER),
            "status": "TECHNICAL_INCOMPLETE", "detected_at_utc": "2026-09-29T00:00:00Z",
            "failure": {"layer": "card-local", "class": "data_window_invalid",
                        "last_run_id": None, "detail": "Required data absent."},
            "attempts": {"launched": 0, "run_specs": 0, "terminal_sentinels": 0},
            "coverage": {"cells_computed": 0},
            "evidence": {"config_sha256": h._sha256(config.read_bytes()),
                         "schema_sha256": h._sha256(schema.read_bytes()), "summary": "absent"},
        }
        if tamper == "hash":
            outcome["evidence"]["config_sha256"] = "sha256:" + "0" * 64
        path = (self.handoff / h.PREPARATION_DIRNAME / cand["family_id"] / h.PREPARATION_OUTCOME)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(outcome) + "\n")
        return path, config, schema

    # --- runner helpers ---------------------------------------------------
    def args(self, **over):
        base = dict(results_root=str(self.root), backlog=None, pool=None, json=False)
        base.update(over)
        return argparse.Namespace(**base)

    def run_prepare(self, **over):
        return p.run(self.args(**over))

    def run_main(self, *argv):
        saved = sys.argv, sys.stdout, sys.stderr
        out, err = io.StringIO(), io.StringIO()
        sys.argv = ["prepare_candidate.py", "--results-root", str(self.root)] + list(argv)
        sys.stdout, sys.stderr = out, err
        try:
            rc = p.main()
        finally:
            sys.argv, sys.stdout, sys.stderr = saved
        return rc, out.getvalue(), err.getvalue()


class TestBacklogIntake(Base):
    def test_missing_results_root_is_a_usage_error(self):
        rc, out, err = self.run_main("--results-root", str(self.root / "nope"))
        self.assertEqual(rc, 2)
        self.assertIn("usage error", err)

    def test_empty_backlog_is_idle_and_launches_nothing(self):
        self._write_backlog([])
        self._write_pool([])
        res = self.run_prepare()
        self.assertEqual((res.action, res.outcome), ("noop", "idle"))
        self.assertEqual(self.launch_calls, [])
        self.assertEqual(self._pool()["candidates"], [])

    def test_invalid_backlog_shapes_fail_closed(self):
        cases = ("no_file", "no_list", "head_not_object", "bad_family", "bad_fingerprint", "no_body")
        for case in cases:
            with self.subTest(case=case):
                self.launch_calls.clear()
                shutil.rmtree(self.handoff, ignore_errors=True)
                good = backlog_candidate(FAMILY_A)
                if case == "no_file":
                    (self.root / h.HANDOFF_DIRNAME).mkdir(parents=True, exist_ok=True)
                    self._write_pool([])
                elif case == "no_list":
                    (self.root / h.HANDOFF_DIRNAME).mkdir(parents=True, exist_ok=True)
                    (self.handoff / p.BACKLOG_FILENAME).write_text(json.dumps({"schema_version": 1}))
                    self._write_pool([])
                elif case == "head_not_object":
                    self._write_backlog(["not-a-candidate"])
                    self._write_pool([])
                elif case == "bad_family":
                    self._write_backlog([backlog_candidate("BAD FAMILY")])
                    self._write_pool([])
                elif case == "bad_fingerprint":
                    self._write_backlog([backlog_candidate(FAMILY_A, fingerprint_input="")])
                    self._write_pool([])
                else:
                    cand = backlog_candidate(FAMILY_A)
                    self._write_backlog([cand])
                    (self.handoff / "bodies" / ("%s.md" % FAMILY_A)).unlink()
                    self._write_pool([])
                res = self.run_prepare()
                self.assertEqual(res.finding_key, "backlog_invalid", case)
                self.assertEqual(self.launch_calls, [])
                self.assertEqual(self._pool()["candidates"], [])

    def test_missing_pool_is_a_finding(self):
        self._write_backlog([backlog_candidate(FAMILY_A)])
        (self.root / h.HANDOFF_DIRNAME).mkdir(parents=True, exist_ok=True)
        res = self.run_prepare()
        self.assertEqual(res.finding_key, "pool_invalid")
        self.assertEqual(self.launch_calls, [])


class TestPreparationLaunch(Base):
    def test_nothing_staged_launches_one_bounded_session_and_returns_waiting(self):
        cand = backlog_candidate(FAMILY_A)
        backlog_path = self._write_backlog([cand])
        self._write_pool([])
        backlog_bytes = backlog_path.read_bytes()
        res = self.run_prepare()
        self.assertEqual((res.action, res.outcome), ("noop", "running"))
        self.assertEqual(res.family_id, FAMILY_A)
        self.assertTrue(res.detail["preparation_required"])
        self.assertEqual(res.detail["preparation_pid"], 31337)
        self.assertEqual(res.detail["preparation_source"], h.PREPARATION_SOURCE)
        self.assertEqual(len(self.launch_calls), 1)
        results_root, launched, source_backlog_path = self.launch_calls[0]
        self.assertEqual(results_root, str(self.root))
        self.assertEqual(source_backlog_path, str(self.handoff / "preparation_backlog.json"))
        self.assertEqual(launched["family_id"], FAMILY_A)
        self.assertEqual(launched["fingerprint_input"], cand["fingerprint_input"])
        self.assertEqual(launched["_body"], "# %s\nDCA PARAMETER DOMAIN\nCOHORT SURVIVOR SEMANTICS\n"
                         % FAMILY_A)
        self.assertEqual(backlog_path.read_bytes(), backlog_bytes)
        self.assertEqual(self._pool()["candidates"], [])
        self.assertFalse((self.root / FAMILY_A).exists())

    def test_busy_preparation_lease_waits_without_duplicate_launch(self):
        self._write_backlog([backlog_candidate(FAMILY_A)])
        self._write_pool([])
        self.launch_result = (None, None, True)
        res = self.run_prepare()
        self.assertEqual((res.action, res.outcome), ("noop", "running"))
        self.assertIn("still owns candidate", res.reason)
        self.assertEqual(len(self.launch_calls), 1)
        self.assertEqual(len(self._backlog()["candidates"]), 1)
        self.assertEqual(self._pool()["candidates"], [])

    def test_launch_failure_is_a_finding_and_the_head_stays(self):
        self._write_backlog([backlog_candidate(FAMILY_A)])
        self._write_pool([])
        self.launch_result = (None, "simulated launcher failure", False)
        res = self.run_prepare()
        self.assertEqual((res.action, res.outcome), ("finding", "finding"))
        self.assertEqual(res.finding_key, "preparation_launch_failed")
        self.assertEqual(self._backlog()["candidates"][0]["family_id"], FAMILY_A)
        self.assertEqual(self._pool()["candidates"], [])

    def test_concurrent_run_is_serialized_by_the_handoff_lease(self):
        self._write_backlog([backlog_candidate(FAMILY_A)])
        self._write_pool([])
        self.handoff.mkdir(parents=True, exist_ok=True)
        fd = h._lock(self.handoff / ".advance.lock")
        self.assertIsNotNone(fd)
        try:
            res = self.run_prepare()
        finally:
            os.close(fd)
        self.assertEqual((res.action, res.outcome), ("noop", "running"))
        self.assertIn("handoff lease", res.reason)
        self.assertEqual(self.launch_calls, [])
        again = self.run_prepare()
        self.assertEqual(again.outcome, "running")
        self.assertEqual(len(self.launch_calls), 1)

    def test_existing_family_path_without_pool_entry_blocks_the_launch(self):
        self._write_backlog([backlog_candidate(FAMILY_A)])
        self._write_pool([])
        family_dir = self.root / FAMILY_A
        family_dir.mkdir(parents=True)
        (family_dir / "family.json").write_text(json.dumps({"family_id": FAMILY_A}) + "\n")
        res = self.run_prepare()
        self.assertEqual(res.finding_key, "promotion_state_inconsistent")
        self.assertEqual(self.launch_calls, [])
        self.assertEqual(len(self._backlog()["candidates"]), 1)


class TestPreparedPromotion(Base):
    def _promote_fixture(self, backlog=None):
        cand_a = backlog_candidate(FAMILY_A)
        cand_b = backlog_candidate(FAMILY_B)
        self._write_backlog(backlog if backlog is not None else [cand_a, cand_b])
        self._write_pool([])
        prepared_dir, manifest_path = self._prepared_package(cand_a)
        return cand_a, cand_b, prepared_dir, manifest_path

    def test_valid_package_promotes_fifo_with_exact_identity_and_order(self):
        cand_a, cand_b, _prepared_dir, manifest_path = self._promote_fixture()
        calls = []
        real_bounded = h._run_bounded_process

        def spy(cmd, cwd, env, timeout=h.PREPARED_EXECUTION_TIMEOUT_S):
            calls.append(list(cmd))
            self.assertNotEqual(cmd[0], "/usr/local/bin/container")
            return real_bounded(cmd, cwd, env, timeout)

        with patch.object(h, "_run_bounded_process", side_effect=spy):
            res = self.run_prepare()

        self.assertEqual((res.action, res.outcome), ("promoted", "promoted"), res.reason)
        self.assertEqual(res.family_id, FAMILY_A)
        self.assertEqual(res.detail["execution_file"], str(manifest_path))
        pool = self._pool()
        entry = pool["candidates"][-1]
        expected = dict(cand_a, execution_file=str(manifest_path))
        self.assertEqual(entry, expected)
        self.assertEqual(list(entry), list(cand_a) + ["execution_file"])
        self.assertEqual(pool["candidates"][:-1], [])
        self.assertEqual(pool["updated_at_utc"], "2026-09-30T11:37:46Z")
        self.assertEqual(pool["note"], "pool")
        backlog = self._backlog()
        self.assertEqual(backlog["candidates"], [cand_b])
        self.assertEqual(backlog["updated_at_utc"], "2026-09-30T11:37:46Z")
        self.assertFalse((self.root / FAMILY_A).exists())
        self.assertFalse((self.handoff / h.LOG_FILENAME).exists())
        self.assertEqual(self.launch_calls, [])
        self.assertEqual(len(self.preflight_calls), 1)
        self.assertEqual(self.preflight_calls[0]["script_path"], "/scripts/%s" % SCRIPT_NAME)
        self.assertTrue(calls)
        self.assertTrue(all(cmd[0] == h.PREPARED_EXECUTION_PYTHON for cmd in calls))
        self.assertTrue(all("unittest" in cmd for cmd in calls))

        # FIFO: the next head is B and it is only launched for preparation, never skipped.
        second = self.run_prepare()
        self.assertEqual(second.outcome, "running")
        self.assertEqual(len(self.launch_calls), 1)
        self.assertEqual(self.launch_calls[0][1]["family_id"], FAMILY_B)
        self.assertEqual(self._backlog()["candidates"][0]["family_id"], FAMILY_B)
        self.assertEqual(self._pool()["candidates"][-1], expected)

    def test_promotion_transport_preserves_challenger_lineage(self):
        # Preparation must append the exact candidate bytes: challenger_of is lineage, not a
        # preparation detail, so it must reach the pool untouched (contract 27.4).
        cand = backlog_candidate(FAMILY_A, challenger_of="sv-incumbent")
        self._write_backlog([cand])
        self._write_pool([])
        _prepared_dir, manifest_path = self._prepared_package(cand)
        res = self.run_prepare()
        self.assertEqual((res.action, res.outcome), ("promoted", "promoted"), res.reason)
        entry = self._pool()["candidates"][-1]
        self.assertEqual(entry, dict(cand, execution_file=str(manifest_path)))
        self.assertEqual(entry["challenger_of"], "sv-incumbent")

    def test_idempotent_retry_only_drops_the_head_when_already_consumed(self):
        cand_a = backlog_candidate(FAMILY_A)
        self._write_backlog([cand_a, backlog_candidate(FAMILY_B)])
        pool_path = self._write_pool([dict(cand_a, execution_file="/staged/prepared-execution.json")])
        pool_bytes = pool_path.read_bytes()
        res = self.run_prepare()
        self.assertEqual((res.action, res.outcome), ("consumed", "consumed"))
        self.assertEqual(res.family_id, FAMILY_A)
        self.assertEqual(pool_path.read_bytes(), pool_bytes)
        self.assertEqual([c["family_id"] for c in self._backlog()["candidates"]], [FAMILY_B])
        self.assertEqual(self.launch_calls, [])

    def test_pool_identity_mismatch_fails_closed(self):
        cand_a = backlog_candidate(FAMILY_A)
        self._write_backlog([cand_a])
        pool_path = self._write_pool([backlog_candidate(FAMILY_A, fingerprint_input="other|fp")])
        pool_bytes = pool_path.read_bytes()
        res = self.run_prepare()
        self.assertEqual(res.finding_key, "promotion_identity_mismatch")
        self.assertEqual(pool_path.read_bytes(), pool_bytes)
        self.assertEqual(len(self._backlog()["candidates"]), 1)
        self.assertEqual(self.launch_calls, [])

    def test_pool_entry_without_any_execution_evidence_fails_closed(self):
        cand_a = backlog_candidate(FAMILY_A)
        self._write_backlog([cand_a])
        self._write_pool([dict(cand_a)])
        res = self.run_prepare()
        self.assertEqual(res.finding_key, "promotion_state_inconsistent")
        self.assertEqual(len(self._backlog()["candidates"]), 1)
        self.assertEqual(self.launch_calls, [])

    def test_invalid_package_never_promotes(self):
        cand_a = backlog_candidate(FAMILY_A)
        self._write_backlog([cand_a])
        self._write_pool([])
        self._prepared_package(cand_a, manifest_over={"semantic_fingerprint": "sha256:" + "0" * 64})
        with patch.object(h, "_run_bounded_process") as bounded:
            res = self.run_prepare()
        self.assertEqual(res.finding_key, "prepared_execution_mismatch")
        bounded.assert_not_called()
        self.assertEqual(self._pool()["candidates"], [])
        self.assertEqual(len(self._backlog()["candidates"]), 1)
        self.assertEqual(self.preflight_calls, [])
        self.assertEqual(self.launch_calls, [])

    def test_missing_focused_test_never_promotes(self):
        cand_a = backlog_candidate(FAMILY_A)
        self._write_backlog([cand_a])
        self._write_pool([])
        self._prepared_package(cand_a, test_file=False)
        res = self.run_prepare()
        self.assertEqual(res.finding_key, "focused_test_failed")
        self.assertIn("missing", res.reason)
        self.assertEqual(self._pool()["candidates"], [])
        self.assertEqual(self.preflight_calls, [])
        self.assertEqual(self.launch_calls, [])

    def test_focused_test_failure_never_promotes(self):
        cand_a = backlog_candidate(FAMILY_A)
        self._write_backlog([cand_a])
        self._write_pool([])
        self._prepared_package(cand_a, test_body=(
            "import unittest\n\n\nclass Probe(unittest.TestCase):\n"
            "    def test_bad(self):\n        self.assertTrue(False)\n"))
        res = self.run_prepare()
        self.assertEqual(res.finding_key, "focused_test_failed")
        self.assertIn("did not pass", res.reason)
        self.assertIn("Ran 1 test", res.reason)
        self.assertEqual(self._pool()["candidates"], [])
        self.assertEqual(self.preflight_calls, [])
        self.assertEqual(self.launch_calls, [])

    def test_preflight_failure_never_promotes(self):
        cand_a = backlog_candidate(FAMILY_A)
        self._write_backlog([cand_a])
        self._write_pool([])
        self._prepared_package(cand_a)
        self.preflight_problem = "P1-P10 preflight failed (rc=1, overall=FAIL, P10=FAIL)"
        res = self.run_prepare()
        self.assertEqual(res.finding_key, "prepared_execution_preflight_failed")
        self.assertEqual(len(self.preflight_calls), 1)
        self.assertEqual(self._pool()["candidates"], [])
        self.assertEqual(len(self._backlog()["candidates"]), 1)
        self.assertEqual(self.launch_calls, [])

    def test_identity_drift_during_preflight_never_promotes(self):
        cand_a = backlog_candidate(FAMILY_A)
        self._write_backlog([cand_a])
        self._write_pool([])
        prepared_dir, _manifest = self._prepared_package(cand_a)
        run_spec = prepared_dir / "rounds" / (FAMILY_A + "-r1") / "attempts" / (FAMILY_A + "-r1-u1") \
            / "run-spec.json"

        def drift(prepared, family_id):
            run_spec.write_bytes(run_spec.read_bytes() + b"\n")
            return None

        h._run_staged_preflight = drift
        try:
            res = self.run_prepare()
        finally:
            h._run_staged_preflight = self._fake_preflight
        self.assertIn(res.finding_key, ("prepared_execution_invalid", "prepared_execution_mismatch"))
        self.assertEqual(self._pool()["candidates"], [])
        self.assertEqual(len(self._backlog()["candidates"]), 1)
        self.assertEqual(self.launch_calls, [])

    def test_package_and_outcome_together_is_fail_closed(self):
        cand_a = backlog_candidate(FAMILY_A)
        self._write_backlog([cand_a])
        self._write_pool([])
        self._prepared_package(cand_a)
        outcome_path, config, schema = self._clear_absence_outcome(cand_a)
        with patch.object(h, "CANONICAL_CONFIG", str(config)), \
                patch.object(h, "CANONICAL_SCHEMA", str(schema)):
            res = self.run_prepare()
        self.assertEqual(res.finding_key, "preparation_state_ambiguous")
        self.assertEqual(self._pool()["candidates"], [])
        self.assertEqual(len(self._backlog()["candidates"]), 1)
        self.assertFalse((self.root / FAMILY_A).exists())
        self.assertTrue(outcome_path.is_file())
        self.assertEqual(self.launch_calls, [])


class TestClearAbsence(Base):
    def _fixture(self):
        cand_a = backlog_candidate(FAMILY_A)
        self._write_backlog([cand_a, backlog_candidate(FAMILY_B)])
        self._write_pool([])
        outcome_path, config, schema = self._clear_absence_outcome(cand_a)
        return cand_a, outcome_path, config, schema

    def test_valid_outcome_publishes_terminal_appends_consumed_history_and_drops_head(self):
        cand_a, _outcome, config, schema = self._fixture()
        with patch.object(h, "CANONICAL_CONFIG", str(config)), \
                patch.object(h, "CANONICAL_SCHEMA", str(schema)):
            res = self.run_prepare()
        self.assertEqual((res.action, res.outcome), ("promoted", "promoted"), res.reason)
        self.assertEqual(res.family_id, FAMILY_A)
        family_dir = self.root / FAMILY_A
        family_doc = json.loads((family_dir / "family.json").read_text())
        self.assertEqual(family_doc["family_id"], FAMILY_A)
        self.assertEqual(family_doc["semantic_fingerprint"], h.fingerprint(cand_a["fingerprint_input"]))
        round_dir = family_dir / "rounds" / (FAMILY_A + "-r1")
        verdict = json.loads((round_dir / "verdict.json").read_text())
        self.assertEqual(verdict["verdict"], "TECHNICAL_INCOMPLETE")
        self.assertIsNone(verdict["run_id"])
        self.assertFalse((round_dir / "attempts").exists())
        self.assertEqual(h.unresolved_incidents(str(self.root), h.read_families(str(self.root))), [])
        pool = self._pool()
        self.assertEqual(pool["candidates"][-1], cand_a)
        self.assertNotIn("execution_file", pool["candidates"][-1])
        self.assertEqual([c["family_id"] for c in self._backlog()["candidates"]], [FAMILY_B])
        self.assertEqual(self.launch_calls, [])
        self.assertEqual(self.preflight_calls, [])

    def test_tampered_outcome_fails_closed_without_promotion(self):
        cand_a, _outcome, config, schema = self._fixture()
        path = (self.handoff / h.PREPARATION_DIRNAME / FAMILY_A / h.PREPARATION_OUTCOME)
        outcome = json.loads(path.read_text())
        outcome["attempts"]["launched"] = 1
        path.write_text(json.dumps(outcome))
        with patch.object(h, "CANONICAL_CONFIG", str(config)), \
                patch.object(h, "CANONICAL_SCHEMA", str(schema)):
            res = self.run_prepare()
        self.assertEqual(res.finding_key, "preparation_outcome_invalid")
        self.assertFalse((self.root / FAMILY_A).exists())
        self.assertEqual(self._pool()["candidates"], [])
        self.assertEqual(len(self._backlog()["candidates"]), 2)
        self.assertEqual(self.launch_calls, [])

    def test_crash_retry_reuses_the_published_terminal(self):
        cand_a, _outcome, config, schema = self._fixture()
        staged = dict(cand_a, _body=self._body_text(FAMILY_A))
        with patch.object(h, "CANONICAL_CONFIG", str(config)), \
                patch.object(h, "CANONICAL_SCHEMA", str(schema)):
            validated = h._validate_preparation_outcome(
                self.handoff / h.PREPARATION_DIRNAME / FAMILY_A / h.PREPARATION_OUTCOME, staged)
            h._publish_clear_absence_terminal(str(self.root), staged, validated)
            verdict_path = (self.root / FAMILY_A / "rounds" / (FAMILY_A + "-r1") / "verdict.json")
            verdict_bytes = verdict_path.read_bytes()
            res = self.run_prepare()
        self.assertEqual((res.action, res.outcome), ("promoted", "promoted"), res.reason)
        self.assertEqual(verdict_path.read_bytes(), verdict_bytes)
        self.assertEqual(self._pool()["candidates"][-1], cand_a)
        self.assertEqual([c["family_id"] for c in self._backlog()["candidates"]], [FAMILY_B])


class TestReporting(Base):
    def test_main_reports_promotion_on_stdout_and_outcome_on_stderr(self):
        cand_a = backlog_candidate(FAMILY_A)
        self._write_backlog([cand_a])
        self._write_pool([])
        self._prepared_package(cand_a)
        rc, out, err = self.run_main("--json")
        self.assertEqual(rc, 0)
        record = json.loads(out)
        self.assertEqual((record["action"], record["outcome"]), ("promoted", "promoted"))
        self.assertEqual(record["family_id"], FAMILY_A)
        self.assertEqual(record["contract_section"], "14.4")
        self.assertIn("outcome=promoted", err)

    def test_main_reports_a_finding_without_writing_the_handoff_log(self):
        self._write_backlog([backlog_candidate(FAMILY_A)])
        self._write_pool([])
        rc, out, err = self.run_main("--json")
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["outcome"], "running")
        self.assertIn("outcome=running", err)
        self.assertFalse((self.handoff / h.LOG_FILENAME).exists())

    def test_custom_backlog_and_pool_paths_are_honoured(self):
        other = Path(tempfile.mkdtemp(prefix="qrp-prepare-alt-"))
        try:
            # The card body must resolve beside the *custom* backlog file: use the inline form here.
            cand_a = backlog_candidate(FAMILY_A, card_body=self._body_text(FAMILY_A))
            self.handoff.mkdir(parents=True, exist_ok=True)
            backlog_path = other / "custom-backlog.json"
            backlog_path.write_text(json.dumps({"schema_version": 1, "candidates": [cand_a]}))
            pool_path = other / "custom-pool.json"
            pool_path.write_text(json.dumps({"schema_version": 1, "candidates": []}))
            res = self.run_prepare(backlog=str(backlog_path), pool=str(pool_path))
            self.assertEqual(res.outcome, "running")
            self.assertEqual(len(self.launch_calls), 1)
            self.assertEqual(self.launch_calls[0][2], str(backlog_path))
            self.assertEqual(len(json.loads(backlog_path.read_text())["candidates"]), 1)
        finally:
            shutil.rmtree(other, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
