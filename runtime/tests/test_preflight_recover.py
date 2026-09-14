#!/usr/bin/env python3
"""Logic checks for preflight --recover (reboot recovery gate, card t_7c11f5d0).

Covers the t_9060bcd4 boundary remainder: default preflight starts stopped-but-existing
containers (no system start, no auto-create), while recover_execution_plane() heals in
order (ExpansionDrive -> `container system start` -> `container start`) and fails closed
(expansion missing / system unstartable / container absent / container unstartable;
an absent container is never silently recreated).

Also covers:
  - Finding B regression: --recover fail-closed main-level (ok=False -> overall=FAIL, rc=1)
  - Finding C regression: wrapper rejects nonzero preflight rc (in test_reconcile_wrapper.py)

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

    # --- Finding A regression: default P5 starts stopped existing container ---
    def test_default_p4_p6_starts_stopped_container(self):
        """Finding A: default preflight starts a stopped-but-existing container.
        Does NOT start system or auto-create; only `container start`."""
        infos = [{"status": {"state": "stopped"}}, {"status": {"state": "running"}}]

        def info(name):
            return (infos.pop(0) if infos else {"status": {"state": "running"}}, None)
        preflight.container_info = info
        self.stub_run({("container", "system", "status"): (0, "status running\n", ""),
                       ("container", "start", "qlib-run"): (0, "started", "")})
        checks = []
        preflight.p4_p6(checks, "qlib-run", preflight.DEFAULT_IMAGE)
        by_id = {c["id"]: c for c in checks}
        self.assertEqual(by_id["P5"]["status"], "PASS")
        self.assertTrue(self.started("container", "start", "qlib-run"))
        # system start must NOT be called
        self.assertFalse(self.started("container", "system", "start"))

    def test_default_p4_p6_absent_container_fails(self):
        """Default P5: absent container → FAIL, no start attempted."""
        preflight.container_info = lambda name: (None, "not listed")
        self.stub_run({("container", "system", "status"): (0, "status running\n", "")})
        checks = []
        preflight.p4_p6(checks, "qlib-run", preflight.DEFAULT_IMAGE)
        by_id = {c["id"]: c for c in checks}
        self.assertEqual(by_id["P5"]["status"], "FAIL")
        self.assertFalse(self.started("container", "start", "qlib-run"))

    def test_default_p4_p6_already_running_no_start(self):
        """Default P5: already running → no start called."""
        preflight.container_info = lambda name: ({"status": {"state": "running"}}, None)
        self.stub_run({("container", "system", "status"): (0, "status running\n", "")})
        checks = []
        preflight.p4_p6(checks, "qlib-run", preflight.DEFAULT_IMAGE)
        by_id = {c["id"]: c for c in checks}
        self.assertEqual(by_id["P5"]["status"], "PASS")
        self.assertFalse(self.started("container", "start", "qlib-run"))


class RecoverHelpText(unittest.TestCase):
    """Minimal regression: --recover help must describe both sides of default mode
    (no system start / no auto-create AND will start existing stopped containers)
    to stay aligned with Contract §16.5."""

    def test_recover_help_contains_required_phrases(self):
        import io, contextlib
        buf = io.StringIO()
        old_argv = sys.argv
        sys.argv = ["preflight.py", "--help"]
        try:
            with contextlib.redirect_stdout(buf):
                with self.assertRaises(SystemExit):
                    preflight.main()
        finally:
            sys.argv = old_argv
        help_text = buf.getvalue()
        self.assertIn("does not start system or auto-create", help_text,
                       "--recover help must state default does not start system or auto-create")
        self.assertIn("will start existing stopped containers", help_text,
                       "--recover help must state default will start existing stopped containers")


# ══════════════════════════════════════════════════════════════════════════════
# Finding B regression: --recover fail-closed main-level
# ══════════════════════════════════════════════════════════════════════════════

class RecoverMainFailClosed(unittest.TestCase):
    """When --recover is used and recovery fails (ok=False), the main() function
    must return overall=FAIL and rc=1 WITHOUT running P1-P8."""

    def setUp(self):
        self.saved_run = preflight.run
        self.saved_container_info = preflight.container_info
        self.saved_system_status = preflight.system_status
        self.saved_wait = preflight._wait_until
        time.sleep = lambda s: None
        self.tmp = tempfile.mkdtemp(prefix="qrp-recover-main-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def tearDown(self):
        preflight.run = self.saved_run
        preflight.container_info = self.saved_container_info
        preflight.system_status = self.saved_system_status
        preflight._wait_until = self.saved_wait

    def test_recover_false_main_returns_fail_rc1(self):
        """Simulate expansion missing: recovery ok=False → main returns rc=1,
        overall=FAIL, no P1-P8 checks evaluated."""
        preflight.run = lambda cmd, timeout=60: (1, "", "")
        preflight.system_status = lambda: (0, "running")
        preflight.container_info = lambda name: ({"status": {"state": "running"}}, None)
        preflight._wait_until = lambda pred, timeout_s=60, interval_s=5: True
        # Patch recover_execution_plane to return ok=False
        orig = preflight.recover_execution_plane
        preflight.recover_execution_plane = lambda exp, name: {
            "attempted": True, "ok": False, "fail_reason": "expansion_missing",
            "actions": []
        }
        try:
            import io
            import contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                # Simulate argparse
                old_argv = sys.argv
                sys.argv = ["preflight.py", "--recover", "--json",
                            "--expansion", self.tmp]
                try:
                    rc = preflight.main()
                finally:
                    sys.argv = old_argv
            self.assertEqual(rc, 1, "main() must return rc=1 on recovery failure")
            import json
            report = json.loads(buf.getvalue())
            self.assertEqual(report["overall"], "FAIL")
            self.assertEqual(report["recovery"]["ok"], False)
            self.assertEqual(report["checks"], [],
                             "P1-P8 must NOT be evaluated when recovery fails")
        finally:
            preflight.recover_execution_plane = orig


# ══════════════════════════════════════════════════════════════════════════════
# Recovery Gate Contract (card t_225b2b0d)
#
# The preflight recovery gate (preflight.py --recover) is:
#   - OPT-IN: only activated with --recover flag; default starts stopped containers
#   - FAIL-CLOSED: if any step fails, reconcile aborts; no partial healing leaks
#   - ORDERED: ExpansionDrive → container system start → container start qlib-run
#   - NEVER AUTO-CREATES: absent container is fail-closed (operator recreates
#     per runbook, never unattended)
#   - DEDUPE/NO-SPAM: reconcile wrapper tracks gate_signature; repeated failures
#     on the same signature are silent; cleared when healthy again
#
# The reconcile wrapper (quant_runtime_reconcile.py) gates core reconcile:
#   - preflight --recover --json runs BEFORE core reconcile logic
#   - overall=PASS AND rc=0 → proceed; overall=FAIL OR rc!=0 → abort
#   - --no-recovery bypasses the gate (manual override)
#   - --dry-run still heals but does not persist gate/state files
# ══════════════════════════════════════════════════════════════════════════════


if __name__ == "__main__":
    unittest.main(verbosity=2)
