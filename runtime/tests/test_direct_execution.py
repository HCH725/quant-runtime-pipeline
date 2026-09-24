#!/usr/bin/env python3
"""Direct worker lease, preflight and terminal identity (no real model/candidate)."""
import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

RUNTIME = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME))
import preflight  # noqa: E402
import production_handoff as h  # noqa: E402
import terminal_evidence as te  # noqa: E402


class DirectExecution(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="qrp-direct-worker-"))
        self.family = self.root / "fam-a"
        self.attempt = self.family / "rounds" / "fam-a-r1" / "attempts" / "fam-a-r1-u1"
        self.attempt.mkdir(parents=True)
        (self.family / "family.json").write_text(json.dumps(
            {"family_id": "fam-a", "handoff": {"execution": "direct_hermes"}}))

    def tearDown(self):
        shutil.rmtree(self.root)

    def test_launch_is_detached_and_lease_lives_through_child(self):
        original = subprocess.Popen
        processes, commands = [], []

        def harmless(cmd, **kwargs):
            commands.append(cmd)
            proc = original([sys.executable, "-c", "import time; time.sleep(0.5)"], **kwargs)
            processes.append(proc)
            return proc

        try:
            with patch.object(h, "DEFAULT_WORKSPACE", str(self.root)), \
                 patch.object(h.subprocess, "Popen", side_effect=harmless):
                pid, why, busy = h.launch_agent(self.root, "fam-a", "agent-task.md", "frozen body")
                self.assertEqual((pid, why, busy), (processes[0].pid, None, False))
                self.assertEqual(commands[0][:4], ["hermes", "-p", "default", "--cli"])
                self.assertIn("--query-file", commands[0])
                self.assertIn("--in", commands[0])
                self.assertEqual((self.family / "agent-task.md").read_text(), "frozen body")
                self.assertEqual(h.launch_agent(self.root, "fam-a", "agent-task.md", "frozen body"),
                                 (None, None, True))
                self.assertEqual(processes[0].wait(timeout=3), 0)
                fd = h._lock(self.family / h.AGENT_LOCK)
                self.assertIsNotNone(fd)
                os.close(fd)
                pid, why, busy = h.launch_agent(self.root, "fam-a", "agent-task.md", "DIFFERENT")
                self.assertIsNone(pid)
                self.assertIn("differs", why)
                self.assertFalse(busy)
        finally:
            for proc in processes:
                if proc.poll() is None:
                    proc.kill()
                proc.wait(timeout=3)

    def test_direct_p10_recomputes_script_without_a_card(self):
        scripts = self.root / "scripts"
        scripts.mkdir()
        script = scripts / "strategy.py"
        script.write_text("print('verified')\n")
        digest = "sha256:" + hashlib.sha256(script.read_bytes()).hexdigest()
        template = RUNTIME / "templates" / "strategy_b_v2_round_spec.template.json"
        (self.attempt.parents[1] / "round-spec.json").write_bytes(template.read_bytes())
        spec = {"schema_version": 1, "family_id": "fam-a", "round_id": "fam-a-r1",
                "run_id": "fam-a-r1-u1", "script": {"path": "/scripts/strategy.py", "sha256": digest}}
        (self.attempt / "run-spec.json").write_text(json.dumps(spec))
        checks = []
        preflight.p9_p10(checks, str(self.attempt), str(scripts))
        self.assertEqual({c["id"]: c["status"] for c in checks}, {"P9": "PASS", "P10": "PASS"})
        spec["task_id"] = "t_unwanted"
        (self.attempt / "run-spec.json").write_text(json.dumps(spec))
        checks = []
        preflight.p9_p10(checks, str(self.attempt), str(scripts))
        self.assertEqual(checks[-1]["status"], "FAIL")

    def publish(self, *extra):
        argv = ["terminal_evidence.py", "publish", "--attempt-dir", str(self.attempt),
                "--status", "DONE", "--family-id", "fam-a", "--round-id", "fam-a-r1",
                "--run-id", "fam-a-r1-u1", *extra]
        with patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()):
            return te.main()

    def test_direct_terminal_has_no_task_or_board_identity(self):
        self.assertEqual(self.publish(), 0)
        sentinel = json.loads((self.attempt / "DONE").read_text())
        self.assertTrue(all(k not in sentinel for k in ("task_id", "kanban_board", "kanban_task_id")))
        self.assertEqual(self.publish(), 1)  # immutable terminal

    def test_direct_terminal_refuses_injected_card_identity(self):
        self.assertEqual(self.publish("--task-id", "t_bad"), 1)
        self.assertFalse((self.attempt / "DONE").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
