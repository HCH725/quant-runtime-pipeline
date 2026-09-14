#!/usr/bin/env python3
"""Logic checks for preflight --recover (reboot recovery gate, card t_7c11f5d0).

Covers the t_9060bcd4 boundary remainder: default preflight stays read-only (never
auto-starts), while recover_execution_plane() heals in order (ExpansionDrive ->
`container system start` -> `container start`) and fails closed (expansion missing /
system unstartable / container absent / container unstartable; an absent container is
never silently recreated).

Run: python3 runtime/tests/test_preflight_recover.py   (stdlib unittest, no container)
"""
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME))
import preflight  # noqa: E402


class RecoverCase(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.saved = (preflight.run, preflight.container_info,
                      preflight.system_status, preflight._wait_until, time.sleep)
        time.sleep = lambda s: None  # polling must not wait in tests
        self.tmp = tempfile.mkdtemp(prefix="qrp-recover-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def tearDown(self):
        (preflight.run, preflight.container_info,
         preflight.system_status, preflight._wait_until) = self.saved[:4]
        time.sleep = self.saved[4]

    # --- stubs ---
    def stub_run(self, table):
        """table: {cmd_prefix_tuple: (rc, out, err)}; records every call."""
        def fake(cmd, timeout=60):
            self.calls.append(tuple(cmd))
            for key, val in table.items():
                if tuple(cmd[:len(key)]) == key:
                    return val() if callable(val) else val
            return (1, "", "unexpected: %s" % " ".join(cmd))
        preflight.run = fake

    def oneshot_wait(self):
        """Fail-closed paths: single probe, no 120s busy loop (sleep is stubbed)."""
        preflight._wait_until = lambda pred, timeout_s=60, interval_s=5: bool(pred())

    def started(self, *tokens):
        return tuple(tokens) in self.calls

    # --- fail-closed ---
    def test_expansion_missing_never_starts(self):
        self.stub_run({})
        preflight.system_status = lambda: (0, "running")
        preflight.container_info = lambda name: ({"status": {"state": "running"}}, None)
        res = preflight.recover_execution_plane("/nonexistent-expansion-xyz", "qlib-run")
        self.assertFalse(res["ok"])
        self.assertEqual(res["fail_reason"], "expansion_missing")
        self.assertEqual(self.calls, [])

    def test_system_start_failed_fail_closed(self):
        self.oneshot_wait()
        preflight.system_status = lambda: (1, "")
        self.stub_run({("container", "system", "start"): (1, "", "boom")})
        preflight.container_info = lambda name: ({"status": {"state": "running"}}, None)
        res = preflight.recover_execution_plane(self.tmp, "qlib-run")
        self.assertFalse(res["ok"])
        self.assertEqual(res["fail_reason"], "system_start_failed")

    def test_container_absent_fail_closed_no_recreate(self):
        preflight.system_status = lambda: (0, "running")
        preflight.container_info = lambda name: (None, "not listed")
        self.stub_run({})
        res = preflight.recover_execution_plane(self.tmp, "qlib-run")
        self.assertFalse(res["ok"])
        self.assertEqual(res["fail_reason"], "container_absent")
        self.assertFalse(self.started("container", "start", "qlib-run"))
        self.assertFalse(any(c[:2] == ("container", "run") for c in self.calls))

    def test_container_start_failed_fail_closed(self):
        self.oneshot_wait()
        preflight.system_status = lambda: (0, "running")
        preflight.container_info = lambda name: ({"status": {"state": "stopped"}}, None)
        self.stub_run({("container", "start", "qlib-run"): (1, "", "boom")})
        res = preflight.recover_execution_plane(self.tmp, "qlib-run")
        self.assertFalse(res["ok"])
        self.assertEqual(res["fail_reason"], "container_start_failed")

    # --- healing order ---
    def test_system_down_started_then_ok(self):
        n = {"calls": 0}

        def status():
            n["calls"] += 1
            return (0, "running") if n["calls"] > 1 else (1, "")
        preflight.system_status = status
        self.stub_run({("container", "system", "start"): (0, "started", "")})
        preflight.container_info = lambda name: ({"status": {"state": "running"}}, None)
        res = preflight.recover_execution_plane(self.tmp, "qlib-run")
        self.assertTrue(res["ok"], res)
        self.assertEqual([a["step"] for a in res["actions"]], ["system_start"])
        self.assertFalse(self.started("container", "start", "qlib-run"))

    def test_stopped_container_started_then_ok(self):
        preflight.system_status = lambda: (0, "running")
        infos = [{"status": {"state": "stopped"}}, {"status": {"state": "running"}}]

        def info(name):
            return (infos.pop(0) if infos else {"status": {"state": "running"}}, None)
        preflight.container_info = info
        self.stub_run({("container", "start", "qlib-run"): (0, "started", "")})
        res = preflight.recover_execution_plane(self.tmp, "qlib-run")
        self.assertTrue(res["ok"], res)
        self.assertEqual([a["step"] for a in res["actions"]], ["container_start"])

    # --- default preflight stays read-only ---
    def test_default_p4_p6_never_starts(self):
        def fake_run(cmd, timeout=60):
            self.calls.append(tuple(cmd))
            if cmd[:3] == ["container", "system", "status"]:
                return (0, "status running\n", "")
            return (1, "", "unexpected: %s" % " ".join(cmd))
        preflight.run = fake_run
        preflight.container_info = lambda name: ({"status": {"state": "stopped"}}, None)
        checks = []
        preflight.p4_p6(checks, "qlib-run", preflight.DEFAULT_IMAGE)
        by_id = {c["id"]: c for c in checks}
        self.assertEqual(by_id["P5"]["status"], "FAIL")
        self.assertFalse(any(tok == "start" for c in self.calls for tok in c), self.calls)


if __name__ == "__main__":
    unittest.main(verbosity=2)
