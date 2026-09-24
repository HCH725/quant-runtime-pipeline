#!/usr/bin/env python3
"""Regression test for Finding C: wrapper recovery_gate rejects nonzero preflight rc.

When the preflight process exits nonzero, the wrapper must treat it as a gate
failure even if stdout claims overall=PASS. Core reconcile must not run.

Includes a main-level regression that calls wrapper.main() and asserts core
reconcile (runpy.run_path) is never invoked.

Run: python3 runtime/tests/test_reconcile_wrapper.py   (stdlib unittest, no container)
"""
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Import the wrapper module directly
WRAPPER_PATH = Path.home() / ".hermes" / "scripts" / "quant_runtime_reconcile.py"
sys.path.insert(0, str(WRAPPER_PATH.parent))
import importlib.util
spec = importlib.util.spec_from_file_location("reconcile_wrapper", str(WRAPPER_PATH))
wrapper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wrapper)


class WrapperGateNonzeroRc(unittest.TestCase):
    """Finding C: wrapper must reject nonzero preflight rc as gate failure."""

    def _make_mock_process(self, rc, stdout, stderr=""):
        p = MagicMock()
        p.returncode = rc
        p.stdout = stdout
        p.stderr = stderr
        return p

    def test_nonzero_rc_with_pass_overall_is_gate_failure(self):
        """Even if stdout says overall=PASS, nonzero rc must fail closed.
        Signature preserves legacy format gate|<failed>|<reason>."""
        report = {"overall": "PASS", "checks": [], "recovery": {"ok": True}}
        p = self._make_mock_process(rc=1, stdout=json.dumps(report))
        with patch("subprocess.run", return_value=p):
            proceed, signature, line = wrapper.recovery_gate()
        self.assertFalse(proceed)
        # No failed checks → signature uses rc_N as placeholder in legacy format
        self.assertEqual(signature, "gate|rc_1|nonzero_rc")
        self.assertIn("rc=1", line)
        self.assertIn("no reconcile this tick", line)

    def test_nonzero_rc_with_fail_overall_is_gate_failure(self):
        """Nonzero rc + FAIL overall: also gate failure, legacy signature."""
        report = {"overall": "FAIL", "checks": [{"id": "P5", "status": "FAIL"}],
                  "recovery": {"ok": False, "fail_reason": "expansion_missing"}}
        p = self._make_mock_process(rc=1, stdout=json.dumps(report))
        with patch("subprocess.run", return_value=p):
            proceed, signature, line = wrapper.recovery_gate()
        self.assertFalse(proceed)
        # Legacy format: gate|P5|expansion_missing (no rc_ prefix)
        self.assertEqual(signature, "gate|P5|expansion_missing")

    def test_zero_rc_with_pass_overall_proceeds(self):
        """Normal healthy case: rc=0 + overall=PASS → proceed."""
        report = {"overall": "PASS", "checks": [], "recovery": {"ok": True}}
        p = self._make_mock_process(rc=0, stdout=json.dumps(report))
        with patch("subprocess.run", return_value=p):
            proceed, signature, line = wrapper.recovery_gate()
        self.assertTrue(proceed)
        self.assertIsNone(signature)

    def test_signature_stable_for_same_rc(self):
        """Signature for nonzero rc must be stable (for dedupe)."""
        report = {"overall": "PASS", "checks": [], "recovery": {"ok": True}}
        p1 = self._make_mock_process(rc=1, stdout=json.dumps(report))
        p2 = self._make_mock_process(rc=1, stdout=json.dumps(report))
        with patch("subprocess.run", return_value=p1):
            _, sig1, _ = wrapper.recovery_gate()
        with patch("subprocess.run", return_value=p2):
            _, sig2, _ = wrapper.recovery_gate()
        self.assertEqual(sig1, sig2)


class WrapperIncidentSchemaRegression(unittest.TestCase):
    """The core report has shipped incident as dict, path string, or omitted/None."""

    def test_incident_kind_accepts_dict_string_and_none(self):
        self.assertEqual(wrapper._incident_kind({
            "incident": {"kind": "checksum_mismatch"}, "reason": "fallback",
        }), "checksum_mismatch")
        self.assertEqual(wrapper._incident_kind({
            "incident": "/Volumes/ExpansionDrive/qlib-results/_incidents/x.jsonl",
            "reason": "checksum_mismatch",
        }), "checksum_mismatch")
        self.assertEqual(wrapper._incident_kind({
            "incident": None, "reason": "attempt_selection_ambiguous",
        }), "attempt_selection_ambiguous")


class WrapperMainLevelRegression(unittest.TestCase):
    """Main-level regression: wrapper.main() must not call core reconcile
    when preflight rc!=0 and stdout claims overall=PASS."""

    def _make_mock_process(self, rc, stdout, stderr=""):
        p = MagicMock()
        p.returncode = rc
        p.stdout = stdout
        p.stderr = stderr
        return p

    def test_main_rc1_stdout_pass_does_not_call_core(self):
        """wrapper.main() returns 0 and never calls runpy.run_path when
        preflight exits rc=1 with overall=PASS in stdout."""
        report = {"overall": "PASS", "checks": [], "recovery": {"ok": True}}
        p = self._make_mock_process(rc=1, stdout=json.dumps(report))
        with patch("subprocess.run", return_value=p), \
             patch("runpy.run_path") as mock_run_path:
            rc = wrapper.main()
        self.assertEqual(rc, 0)
        mock_run_path.assert_not_called()

    def test_failed_direct_wake_is_reported_as_an_incident(self):
        """BF-1: a disposition wake that cannot start must reach the operator, not retry silently."""
        report = {"ran_at_utc": "2026-09-24T12:00:00Z",
                  "results": [{"action": "incident",
                               "attempt_dir": "/r/fam-dead/rounds/fam-dead-r1/attempts/fam-dead-r1-u1",
                               "family_id": "fam-dead", "round_id": "fam-dead-r1",
                               "run_id": "fam-dead-r1-u1",
                               "reason": "disposition_launch_failed (direct Hermes launch failed: EAGAIN)",
                               "incident": "/r/_incidents/reconciliation_incident.jsonl"}],
                  "launched": [], "incidents": 1}
        argv = ["quant_runtime_reconcile.py", "--no-recovery", "--dry-run"]
        old_argv, old_stdout = sys.argv, sys.stdout
        captured = io.StringIO()
        try:
            sys.argv = argv
            sys.stdout = captured

            def fake_run_path(*args, **kwargs):
                print(json.dumps(report))

            with patch.object(wrapper.subprocess, "run") as gate, \
                    patch.object(wrapper.runpy, "run_path",
                                 side_effect=fake_run_path, return_value=3):
                rc = wrapper.main()
        finally:
            sys.argv, sys.stdout = old_argv, old_stdout
        self.assertEqual(rc, 0, captured.getvalue())
        gate.assert_not_called()
        out = captured.getvalue()
        self.assertIn("reconciler incident: disposition_launch_failed", out)
        self.assertIn("family=fam-dead", out)
        self.assertIn("run=fam-dead-r1-u1", out)
        self.assertNotIn("unblocked", out)
        self.assertNotIn("kanban", out.lower())
        self.assertNotIn("task=", out)

    def test_direct_launch_is_announced_without_kanban_language(self):
        """v2.0: a successful direct launch prints `launched`, never `unblocked`/`task=`."""
        report = {"ran_at_utc": "2026-09-24T12:00:00Z",
                  "results": [{"action": "launched", "attempt_dir": "/r/fam-a/rounds/fam-a-r1",
                               "family_id": "fam-a", "round_id": "fam-a-r1",
                               "run_id": "fam-a-r1-u1", "dry_run": False}],
                  "launched": ["fam-a-r1-u1"], "incidents": 0}
        argv = ["quant_runtime_reconcile.py", "--no-recovery", "--dry-run"]
        old_argv, old_stdout = sys.argv, sys.stdout
        captured = io.StringIO()
        try:
            sys.argv = argv
            sys.stdout = captured

            def fake_run_path(*args, **kwargs):
                print(json.dumps(report))

            with patch.object(wrapper.subprocess, "run") as gate, \
                    patch.object(wrapper.runpy, "run_path",
                                 side_effect=fake_run_path, return_value=None):
                rc = wrapper.main()
        finally:
            sys.argv, sys.stdout = old_argv, old_stdout
        self.assertEqual(rc, 0, captured.getvalue())
        gate.assert_not_called()  # --no-recovery: the gate is skipped, the core still runs
        out = captured.getvalue()
        self.assertIn("launched default disposition for fam-a-r1-u1", out)
        self.assertNotIn("unblocked", out)
        self.assertNotIn("kanban", out.lower())
        self.assertNotIn("task=", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
