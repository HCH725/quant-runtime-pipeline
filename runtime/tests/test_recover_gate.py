#!/usr/bin/env python3
"""Focused checks for the recover-gate healthy path and stopped-n8n DB gate.

Contract under test, in one line: an interval where the Apple Container system
is already running and the EXISTING qlib-run is already running must NOT invoke
`preflight.py --recover --json` (no P1-P8 sweep every 300 s) — it inspects state
only, logs qlib=already_running and exits 0.

Delegation to the canonical preflight happens only when something needs
recovering (qlib-run stopped, or the container system had to be recovered);
an absent qlib-run fails closed WITHOUT delegating and without ever being
`container run`/`container start`-ed. Stopped virtiofs n8n needs SQLite prep;
stopped named-volume n8n starts without touching host SQLite.

Run: python3 runtime/tests/test_recover_gate.py   (stdlib unittest, no container)
"""
import os
import shutil
import sqlite3
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
        self.saved = (rg.LOG, rg.LOCK, rg.N8N_DB, rg._prepare_n8n_db,
                      rg.qlib_preflight, rg._readiness_ok,
                      pf.run, pf.system_status, pf.container_info, pf._wait_until,
                      pf.host_boot_id, list(sys.argv))
        rg.LOG = os.path.join(self.tmp, "gate.log")
        rg.LOCK = os.path.join(self.tmp, "gate.lock")
        rg.N8N_DB = os.path.join(self.tmp, "database.sqlite")
        self.preflight_calls = []
        self.preflight_result = (0, "PASS(rc=0 actions=noop fail=- failed=-)")
        self.run_calls = []
        self.events = []
        self.prep_ok = True
        self.state = {"system": "running", "n8n": "running", "qlib-run": "running"}
        self.present = {"n8n": True, "qlib-run": True}
        self.mounts = [{"destination": "/home/node/.n8n", "type": {"virtiofs": {}}}]

        pf.host_boot_id = lambda: "boot-test"
        pf._wait_until = lambda pred, timeout_s=60, interval_s=5: bool(pred())
        pf.system_status = lambda: ((0, "running") if self.state["system"] == "running"
                                    else (1, ""))
        rg._readiness_ok = lambda: True
        pf.container_info = self._container_info
        pf.run = self._run
        rg.qlib_preflight = self._preflight
        rg._prepare_n8n_db = self._prepare_db

    def tearDown(self):
        (rg.LOG, rg.LOCK, rg.N8N_DB, rg._prepare_n8n_db,
         rg.qlib_preflight, rg._readiness_ok,
         pf.run, pf.system_status, pf.container_info, pf._wait_until,
         pf.host_boot_id) = self.saved[:11]
        sys.argv[:] = self.saved[11]

    # --- stubs ---
    def _container_info(self, name):
        if not self.present.get(name, False):
            return None, "container %r not listed" % name
        if name == "n8n":
            return {"status": {"state": self.state.get(name)},
                    "configuration": {"mounts": self.mounts}}, None
        return {"status": {"state": self.state.get(name)}}, None

    def _run(self, cmd, timeout=60):
        self.run_calls.append(tuple(cmd))
        cmd = tuple(cmd)
        if cmd[:3] == ("container", "system", "start"):
            self.state["system"] = "running"
            return 0, "started", ""
        if cmd[:2] == ("container", "start"):
            self.events.append(("container_start", cmd[2]))
            self.state[cmd[2]] = "running"
            return 0, "started", ""
        return 1, "", "unexpected: %s" % " ".join(cmd)

    def _prepare_db(self):
        self.events.append(("sqlite_prep",))
        return self.prep_ok

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
        self.assertEqual(self.events, [], "running n8n must not touch SQLite")

    def test_running_n8n_ignores_missing_mount_and_host_db(self):
        self.mounts = []
        rg._prepare_n8n_db = self.saved[3]  # would fail if called: no host DB exists
        self.assertEqual(self.run_gate(), 0, self.logged())
        self.assertEqual(self.run_calls, [])
        self.assertFalse(os.path.exists(rg.N8N_DB))

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
        self.assertEqual(self.events, [("sqlite_prep",), ("container_start", "n8n")])

    def test_stopped_named_volume_n8n_skips_host_sqlite_and_starts(self):
        self.state["n8n"] = "stopped"
        self.mounts = [{"destination": "/home/node/.n8n",
                        "type": {"volume": {"cache": {"on": {}}, "format": "ext4",
                                            "name": "n8n-data", "sync": {"fsync": {}}}}},
                       {"destination": "/home/node/.n8n-files", "type": {"virtiofs": {}}}]
        rg._prepare_n8n_db = self.saved[3]  # real prep would fail: no host DB exists
        self.assertEqual(self.run_gate(), 0, self.logged())
        self.assertIn("n8n=started", self.logged())
        self.assertEqual(self.events, [("container_start", "n8n")])
        self.assertFalse(os.path.exists(rg.N8N_DB))

    def test_stopped_n8n_unknown_missing_or_ambiguous_mount_fails_closed(self):
        self.state["n8n"] = "stopped"
        mount = {"destination": "/home/node/.n8n", "type": {"volume": {}}}
        for mounts in (None, [], [{"destination": "/other", "type": {"volume": {}}}],
                       [{"destination": "/home/node/.n8n", "type": {"bind": {}}}],
                       [mount, mount],
                       [{"destination": "/home/node/.n8n", "type": {"virtiofs": {}, "volume": {}}}],
                       [{"destination": "/home/node/.n8n", "type": {"volume": {}, "extra": {}}}],
                       [{"destination": "/home/node/.n8n", "type": {}}],
                       [{"destination": "/home/node/.n8n", "type": "named-volume"}],
                       [{"destination": "/home/node/.n8n", "type": ["volume"]}],
                       [{"destination": "/home/node/.n8n"}]):
            with self.subTest(mounts=mounts):
                self.mounts = mounts
                self.run_calls.clear()
                self.events.clear()
                self.assertEqual(self.run_gate(), 1, self.logged())
                self.assertIn("n8n=mount_fail_closed(", self.logged().splitlines()[-1])
                self.assertEqual(self.events, [])
                self.assertFalse(self.started("n8n"))

    def test_failed_sqlite_prep_prevents_n8n_start(self):
        self.state["n8n"] = "stopped"
        self.prep_ok = False
        self.assertEqual(self.run_gate(), 1)
        self.assertEqual(self.events, [("sqlite_prep",)])
        self.assertFalse(self.started("n8n"))
        self.assertIn("n8n=sqlite_prep_failed(", self.logged())

    def test_missing_db_is_not_created_or_started(self):
        self.state["n8n"] = "stopped"
        rg._prepare_n8n_db = self.saved[3]
        self.assertEqual(self.run_gate(), 1)
        self.assertFalse(os.path.exists(rg.N8N_DB))
        self.assertFalse(self.started("n8n"))
        self.assertIn("n8n=sqlite_prep_failed(", self.logged())

    def test_sqlite_prep_keeps_db_and_clears_sidecars(self):
        conn = sqlite3.connect(rg.N8N_DB)
        try:
            self.assertEqual(conn.execute("PRAGMA journal_mode=WAL").fetchone(), ("wal",))
            conn.execute("CREATE TABLE probe (value INTEGER)")
            conn.execute("INSERT INTO probe VALUES (1)")
            conn.commit()
        finally:
            conn.close()
        for suffix in ("-wal", "-shm"):
            with open(rg.N8N_DB + suffix, "wb"):
                pass
        self.assertTrue(self.saved[3]())
        self.assertTrue(os.path.isfile(rg.N8N_DB))
        self.assertFalse(any(os.path.exists(rg.N8N_DB + suffix) for suffix in ("-wal", "-shm")))
        conn = sqlite3.connect(rg.N8N_DB)
        try:
            self.assertEqual(conn.execute("SELECT value FROM probe").fetchall(), [(1,)])
        finally:
            conn.close()

    def test_container_name_overrides_only_affect_the_fail_closed_probe(self):
        """argv[2] = qlib name used by the recovery self-test; nothing is created."""
        rc = self.run_gate("n8n", "__definitely_absent__")
        self.assertEqual(rc, 1)
        self.assertIn("qlib=absent_fail_closed(container '__definitely_absent__' not listed)",
                      self.logged())
        self.assertEqual(self.preflight_calls, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
