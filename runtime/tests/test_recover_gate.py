#!/usr/bin/env python3
"""Focused checks for the recover-gate healthy-path no-op (card t_58ef1b17).

Contract under test, in one line: an interval where the Apple Container system
is already running and the EXISTING qlib-run is already running must NOT invoke
`preflight.py --recover --json` (no P1-P8 sweep every 300 s) — it inspects state
only, logs qlib=already_running and exits 0.

Delegation to the canonical preflight happens only when something needs
recovering (qlib-run stopped, or the container system had to be recovered);
an absent qlib-run fails closed WITHOUT delegating and without ever being
`container run`/`container start`-ed.  n8n keeps its existing fail-closed /
restart behaviour.

Run: python3 runtime/tests/test_recover_gate.py   (stdlib unittest, no container)
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME))
import recover_gate as rg  # noqa: E402
pf = rg.pf  # preflight module (shared helpers under test)


class GateCase(unittest.TestCase):
    """Every test drives rg.main() against a fully stubbed container world."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="qrp-gate-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.saved = (rg.LOG, rg.LOCK, rg.qlib_preflight, rg._readiness_ok,
                      pf.run, pf.system_status, pf.container_info, pf._wait_until,
                      pf.host_boot_id, list(sys.argv))
        rg.LOG = os.path.join(self.tmp, "gate.log")
        rg.LOCK = os.path.join(self.tmp, "gate.lock")
        self.preflight_calls = []
        self.preflight_result = (0, "PASS(rc=0 actions=noop fail=- failed=-)")
        self.run_calls = []
        self.state = {"system": "running", "n8n": "running", "qlib-run": "running"}
        self.present = {"n8n": True, "qlib-run": True}

        pf.host_boot_id = lambda: "boot-test"
        pf._wait_until = lambda pred, timeout_s=60, interval_s=5: bool(pred())
        pf.system_status = lambda: ((0, "running") if self.state["system"] == "running"
                                    else (1, ""))
        rg._readiness_ok = lambda: True
        pf.container_info = self._container_info
        pf.run = self._run
        rg.qlib_preflight = self._preflight

    def tearDown(self):
        (rg.LOG, rg.LOCK, rg.qlib_preflight, rg._readiness_ok,
         pf.run, pf.system_status, pf.container_info, pf._wait_until,
         pf.host_boot_id) = self.saved[:9]
        sys.argv[:] = self.saved[9]

    # --- stubs ---
    def _container_info(self, name):
        if not self.present.get(name, False):
            return None, "container %r not listed" % name
        return {"status": {"state": self.state.get(name)}}, None

    def _run(self, cmd, timeout=60):
        self.run_calls.append(tuple(cmd))
        cmd = tuple(cmd)
        if cmd[:3] == ("container", "system", "start"):
            self.state["system"] = "running"
            return 0, "started", ""
        if cmd[:2] == ("container", "start"):
            self.state[cmd[2]] = "running"
            return 0, "started", ""
        return 1, "", "unexpected: %s" % " ".join(cmd)

    def _preflight(self):
        self.preflight_calls.append(True)
        return self.preflight_result

    def run_gate(self, *argv):
        sys.argv[:] = ["recover_gate.py"] + list(argv)
        return rg.main()

    def logged(self):
        with open(rg.LOG) as fh:
            return fh.read()

    def started(self, name):
        return ("container", "start", name) in self.run_calls

    # --- issue 1: healthy interval is a true cheap no-op ---
    def test_healthy_interval_skips_preflight_entirely(self):
        rc = self.run_gate()
        self.assertEqual(rc, 0, self.logged())
        self.assertEqual(self.preflight_calls, [],
                         "healthy interval must NOT run preflight P1-P8")
        self.assertIn("system=running n8n=already_running qlib=already_running rc=0 problems=-",
                      self.logged())
        # nothing was started either: inspection only
        self.assertEqual(self.run_calls, [])

    def test_qlib_stopped_delegates_to_canonical_preflight(self):
        self.state["qlib-run"] = "stopped"
        rc = self.run_gate()
        self.assertEqual(rc, 0, self.logged())
        self.assertEqual(len(self.preflight_calls), 1,
                         "stopped qlib-run must delegate to preflight --recover")
        self.assertIn("qlib=preflight:PASS(rc=0 actions=noop", self.logged())
        # the gate itself must not start qlib-run - that is preflight's job
        self.assertFalse(self.started("qlib-run"))

    def test_recovered_container_system_delegates_even_if_qlib_running(self):
        self.state["system"] = "stopped"
        rc = self.run_gate()
        self.assertEqual(rc, 0, self.logged())
        self.assertIn(("container", "system", "start"), self.run_calls)
        self.assertEqual(len(self.preflight_calls), 1,
                         "a recovered container system must re-validate via preflight")
        self.assertIn("system=started", self.logged())

    def test_preflight_failure_is_a_gate_failure(self):
        self.state["qlib-run"] = "stopped"
        self.preflight_result = (1, "FAIL(rc=1 actions=noop fail=container_absent failed=P5)")
        rc = self.run_gate()
        self.assertEqual(rc, 1)
        self.assertIn("qlib=preflight:FAIL(rc=1", self.logged())

    # --- fail closed: missing containers are never created ---
    def test_missing_qlib_fails_closed_without_preflight_or_recreate(self):
        self.present["qlib-run"] = False
        rc = self.run_gate()
        self.assertEqual(rc, 1, self.logged())
        self.assertEqual(self.preflight_calls, [],
                         "an absent qlib-run must fail closed without delegating")
        self.assertIn("qlib=absent_fail_closed(container 'qlib-run' not listed)", self.logged())
        self.assertFalse(any(c[:2] == ("container", "run") for c in self.run_calls))
        self.assertFalse(self.started("qlib-run"))

    def test_missing_n8n_fails_closed_and_qlib_still_reported(self):
        self.present["n8n"] = False
        rc = self.run_gate()
        self.assertEqual(rc, 1, self.logged())
        self.assertIn("n8n=missing_fail_closed(container 'n8n' not listed)", self.logged())
        self.assertIn("qlib=already_running", self.logged())
        self.assertEqual(self.preflight_calls, [])
        self.assertFalse(self.started("n8n"))
        self.assertFalse(any(c[:2] == ("container", "run") for c in self.run_calls))

    def test_stopped_n8n_is_restarted(self):
        self.state["n8n"] = "stopped"
        rc = self.run_gate()
        self.assertEqual(rc, 0, self.logged())
        self.assertIn("n8n=started", self.logged())
        self.assertTrue(self.started("n8n"))

    def test_container_name_overrides_only_affect_the_fail_closed_probe(self):
        """argv[2] = qlib name used by the recovery self-test; nothing is created."""
        rc = self.run_gate("n8n", "__definitely_absent__")
        self.assertEqual(rc, 1)
        self.assertIn("qlib=absent_fail_closed(container '__definitely_absent__' not listed)",
                      self.logged())
        self.assertEqual(self.preflight_calls, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
