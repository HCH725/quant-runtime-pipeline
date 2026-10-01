#!/usr/bin/env python3
"""Direct-family stale-attempt disposition and watchdog helper surface (auditor BF-1/BF-2).

A direct family whose attempt stopped writing outside the 90-minute stall window without a
terminal sentinel and without its own round verdict must never hold the pipeline silently: C4
wakes the same host-side disposition session it uses for a compute-finished stage, and a failed
wake is recorded as a fail-closed incident the wrapper reports. A fresh (live) attempt is never
touched. `attempt_metadata` stays the API the installed watchdog's W2/W3 lane calls.

Never touches the real results root, board, container or model: every fixture is a temp root and
every dispatch is either a stub or a harmless local process. `probe_stall.py` / `probe_watchdog.py`
in the auditor's scratch dir are the independent counterparts of these checks.

Run: python3 runtime/tests/test_direct_stall.py   (stdlib unittest, no container)
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

RUNTIME = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME))
import production_handoff as h  # noqa: E402
import reconcile as r  # noqa: E402

WATCHDOG = Path.home() / ".hermes" / "scripts" / "quant_runtime_watchdog.py"
STALE = time.time() - 2 * 3600  # 2h old: far outside the 90-minute window
BODY = "PROBE\nDCA PARAMETER DOMAIN\nwindow in [20,50]\nCOHORT SURVIVOR SEMANTICS\ntop sharpe\n"


class DirectStallDisposition(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="qrp-direct-stall-"))
        (self.root / "ws").mkdir()
        handoff = self.root / "_handoff"
        handoff.mkdir()
        (handoff / "candidates.json").write_text(json.dumps({"candidates": [
            {"family_id": fid, "title": "probe " + fid, "fingerprint_input": "probe|" + fid,
             "card_body": BODY, "skills": [], "workspace_path": str(self.root / "ws")}
            for fid in ("fam-dead", "fam-next")]}))
        self.calls = []

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def launch(self, root, family, name, prompt, skills=(), workspace=None, lease_fd=None):
        self.calls.append((root, family, name, prompt, skills))
        return 4242, None, False

    def build_attempt(self, stage=None, fresh=False, family="fam-dead"):
        """Direct family + one attempt; everything ages to STALE unless `fresh`."""
        rnd, run = family + "-r1", family + "-r1-u1"
        attempt = self.root / family / "rounds" / rnd / "attempts" / run
        attempt.mkdir(parents=True)
        stamp = time.time() if fresh else STALE
        iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stamp))
        (self.root / family / "family.json").write_text(json.dumps(
            {"schema_version": 1, "family_id": family, "created_at_utc": iso,
             "semantic_fingerprint": "sha256:" + "0" * 64,
             "handoff": {"execution": "direct_hermes"}}))
        (attempt.parents[1] / "round-spec.json").write_text(json.dumps(
            {"family_id": family, "round_id": rnd}))
        (attempt / "run-spec.json").write_text(json.dumps(
            {"schema_version": 1, "family_id": family, "round_id": rnd, "run_id": run,
             "created_at_utc": iso}))
        if stage is not None:
            (attempt / "state.json").write_text(json.dumps(
                {"family_id": family, "round_id": rnd, "run_id": run, "stage": stage}))
        for path in [self.root / family, attempt.parents[1], attempt] + list(attempt.iterdir()):
            os.utime(path, (stamp, stamp))
        os.utime(self.root / family / "family.json", (stamp, stamp))
        return attempt

    def run_root(self, dry_run=False):
        argv = ["reconcile.py", "--results-root", str(self.root), "--json"]
        if dry_run:
            argv.append("--dry-run")
        original, out = sys.argv, io.StringIO()
        try:
            sys.argv = argv
            with contextlib.redirect_stdout(out):
                code = r.main()
        finally:
            sys.argv = original
        return code, json.loads(out.getvalue())

    def run_c3(self):
        """One C3 tick; no dispatch can succeed here (Popen replaced), like the n8n cadence."""
        def record(cmd, **kwargs):
            raise OSError("test: dispatch would start here")

        args = types.SimpleNamespace(results_root=str(self.root), pool=None, dry_run=False,
                                     json=True, detector="handoff", quiet_noop=True, board=None)
        with patch.object(h.subprocess, "Popen", side_effect=record):
            return h.round_once(args)

    def incident_lines(self):
        path = self.root / r.INCIDENT_FILE
        return path.read_text().splitlines() if path.is_file() else []

    def test_stalled_attempt_without_state_wakes_disposition(self):
        """Agent died before Qlib ever wrote state.json: the round still gets decided."""
        attempt = self.build_attempt(stage=None)
        with patch.object(r, "launch_agent", side_effect=self.launch):
            code, report = self.run_root()
        self.assertEqual(code, 0)
        self.assertEqual(report["launched"], [attempt.name])
        self.assertEqual(report["results"][0]["action"], "launched")
        self.assertGreaterEqual(report["results"][0]["detail"]["stalled_minutes"], 90)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(
            self.calls[0][2],
            "disposition-v%d-%s-%s.md" % (r.DISPOSITION_PROMPT_VERSION,
                                          attempt.parents[1].name, attempt.name))
        self.assertIn("No Kanban", self.calls[0][3])
        self.assertEqual(self.incident_lines(), [])
        # the family is still undecided -> the next candidate must not be consumed
        c3 = self.run_c3()
        self.assertEqual(c3.outcome, "running")
        self.assertEqual(c3.detail.get("active_family"), "fam-dead")
        self.assertFalse((self.root / "fam-next").exists())

    def test_stalled_running_qlib_attempt_wakes_disposition(self):
        """Qlib died mid-run (stage=RUNNING_QLIB, nothing written for 2h): same disposal."""
        attempt = self.build_attempt(stage="RUNNING_QLIB")
        with patch.object(r, "launch_agent", side_effect=self.launch):
            code, report = self.run_root()
        self.assertEqual(code, 0)
        self.assertEqual(report["launched"], [attempt.name])
        self.assertIn("stalled RUNNING_QLIB", report["results"][0]["reason"])
        self.assertEqual(len(self.calls), 1)

    def test_fresh_undecided_attempt_is_never_woken(self):
        """Inside the window the attempt is live compute: report-only, no second worker."""
        self.build_attempt(stage="RUNNING_QLIB", fresh=True)
        with patch.object(r, "launch_agent", side_effect=self.launch):
            code, report = self.run_root()
        self.assertEqual(code, 0)
        self.assertEqual(report["results"][0]["action"], "orphan_candidate")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.incident_lines(), [])

    def test_stalled_wake_failure_records_incident_and_holds_next_candidate(self):
        """A wake that cannot start is an explicit fail-closed incident, not a silent hold."""
        self.build_attempt(stage=None)
        commands = []

        def refuse(cmd, **kwargs):
            commands.append(list(cmd))
            raise OSError("test: dispatch refused")

        with patch.object(h.subprocess, "Popen", side_effect=refuse):
            code, report = self.run_root()
        self.assertEqual(code, 3)
        self.assertEqual(report["results"][0]["action"], "incident")
        self.assertTrue(report["results"][0]["reason"].startswith("disposition_launch_failed"))
        self.assertEqual([json.loads(line)["kind"] for line in self.incident_lines()],
                         ["disposition_launch_failed"])
        self.assertTrue(commands and commands[0][0] == "hermes")
        self.assertFalse(any("kanban" in part for cmd in commands for part in cmd))
        # retryable for the SAME family, and the next candidate is never consumed
        for _ in range(2):
            c3 = self.run_c3()
            self.assertIn(c3.outcome, ("running", "incident", "finding"))
            self.assertNotEqual(c3.action, "appended")
            self.assertFalse((self.root / "fam-next").exists())
        with patch.object(h.subprocess, "Popen", side_effect=refuse):
            code, report = self.run_root()  # same cadence decision, one deduplicated incident
        self.assertEqual(code, 3)
        self.assertEqual(len(self.incident_lines()), 1)

    def test_stalled_attempt_dry_run_reports_without_launching(self):
        self.build_attempt(stage=None)
        with patch.object(r, "launch_agent", side_effect=self.launch):
            code, report = self.run_root(dry_run=True)
        self.assertEqual(code, 0)
        self.assertEqual(report["would_launch"], ["fam-dead-r1-u1"])
        self.assertEqual(self.calls, [])
        self.assertFalse((self.root / "_incidents").exists())

    def test_real_launch_freezes_prompt_and_lease_blocks_a_second_wake(self):
        """The stalled path really dispatches through the family lease (no duplicate worker)."""
        self.build_attempt(stage="RUNNING_QLIB")
        original = subprocess.Popen
        processes = []
        try:
            def harmless(cmd, **kwargs):
                proc = original([sys.executable, "-c", "import time; time.sleep(0.6)"], **kwargs)
                processes.append(proc)
                return proc

            with patch.object(h, "DEFAULT_WORKSPACE", str(self.root / "ws")), \
                 patch.object(h.subprocess, "Popen", side_effect=harmless):
                code, report = self.run_root()
                self.assertEqual(code, 0)
                self.assertEqual(report["results"][0]["action"], "launched")
                frozen = (self.root / "fam-dead" /
                          ("disposition-v%d-fam-dead-r1-fam-dead-r1-u1.md"
                           % r.DISPOSITION_PROMPT_VERSION))
                self.assertTrue(frozen.is_file())
                code, second = self.run_root()  # lease still held by the live session
                self.assertEqual(second["results"][0]["action"], "running")
                self.assertEqual(len(processes), 1)
        finally:
            for proc in processes:
                if proc.poll() is None:
                    proc.kill()
                proc.wait(timeout=3)


class WatchdogHelperSurface(unittest.TestCase):
    """BF-2: the installed watchdog imports these symbols from runtime/reconcile.py."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="qrp-watchdog-surface-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def fixture(self, family, direct, spec_extra=None):
        rnd, run = family + "-r1", family + "-r1-u1"
        attempt = self.root / family / "rounds" / rnd / "attempts" / run
        attempt.mkdir(parents=True)
        family_doc = {"family_id": family}
        family_doc.update({"handoff": {"execution": "direct_hermes"}} if direct
                          else {"kanban_task_id": "t_old"})
        (self.root / family / "family.json").write_text(json.dumps(family_doc))
        spec = {"family_id": family, "round_id": rnd, "run_id": run,
                "created_at_utc": "2026-09-13T00:00:00Z"}
        spec.update(spec_extra or {})
        (attempt / "run-spec.json").write_text(json.dumps(spec))
        return attempt

    def test_attempt_metadata_keeps_one_rule_per_family_kind(self):
        direct = self.fixture("fam-direct", direct=True)
        rec = r.attempt_metadata(str(direct), "fam-direct", "fam-direct-r1", "fam-direct-r1-u1")
        self.assertEqual(rec.problems, [])
        self.assertIsNotNone(rec.created_at)
        self.assertEqual((rec.path, rec.run_id, rec.ordinal), (direct, "fam-direct-r1-u1", 1))
        carded = self.fixture("fam-old", direct=False, spec_extra={"task_id": "t_old",
                                                                   "kanban_board": "default"})
        self.assertEqual(r.attempt_metadata(str(carded), "fam-old", "fam-old-r1",
                                            "fam-old-r1-u1").problems, [])
        owned = self.fixture("fam-direct-2", direct=True, spec_extra={"task_id": "t_leak"})
        self.assertIn("Kanban ownership",
                      "; ".join(r.attempt_metadata(str(owned), "fam-direct-2", "fam-direct-2-r1",
                                                   "fam-direct-2-u1").problems))
        unowned = self.fixture("fam-old-2", direct=False)
        self.assertIn("card ownership",
                      "; ".join(r.attempt_metadata(str(unowned), "fam-old-2", "fam-old-2-r1",
                                                   "fam-old-2-u1").problems))

    @unittest.skipUnless(WATCHDOG.is_file(), "installed watchdog not present")
    def test_installed_watchdog_finds_every_helper(self):
        spec = importlib.util.spec_from_file_location("qtw_probe", WATCHDOG)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        helpers = module.load_repo_helpers(str(RUNTIME))
        needed = ["discover_rounds", "attempt_metadata", "select_authoritative", "TERMINALS",
                  "host_boot_id"]
        self.assertEqual([name for name in needed if not hasattr(helpers, name)], [])
        attempt = self.fixture("fam-direct", direct=True)
        records = [helpers.attempt_metadata(a, "fam-direct", "fam-direct-r1", a.name)
                   for _f, _r, attempts in helpers.discover_rounds(str(self.root))
                   for a in attempts]
        authoritative, superseded, problem = helpers.select_authoritative(records)
        self.assertIsNone(problem)
        self.assertEqual((str(authoritative.path), superseded), (str(attempt), []))


if __name__ == "__main__":
    unittest.main(verbosity=2)
