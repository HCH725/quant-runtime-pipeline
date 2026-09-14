#!/usr/bin/env python3
"""Regression test for Finding C: wrapper recovery_gate rejects nonzero preflight rc.

When the preflight process exits nonzero, the wrapper must treat it as a gate
failure even if stdout claims overall=PASS. Core reconcile must not run.

Run: python3 runtime/tests/test_reconcile_wrapper.py   (stdlib unittest, no container)
"""
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
        """Even if stdout says overall=PASS, nonzero rc must fail closed."""
        report = {"overall": "PASS", "checks": [], "recovery": {"ok": True}}
        p = self._make_mock_process(rc=1, stdout=json.dumps(report))
        with patch("subprocess.run", return_value=p):
            proceed, signature, line = wrapper.recovery_gate()
        self.assertFalse(proceed)
        self.assertTrue(signature.startswith("gate|rc_1|"))
        self.assertIn("rc=1", line)
        self.assertIn("no reconcile this tick", line)

    def test_nonzero_rc_with_fail_overall_is_gate_failure(self):
        """Nonzero rc + FAIL overall: also gate failure (existing path)."""
        report = {"overall": "FAIL", "checks": [{"id": "P5", "status": "FAIL"}],
                  "recovery": {"ok": False, "fail_reason": "expansion_missing"}}
        p = self._make_mock_process(rc=1, stdout=json.dumps(report))
        with patch("subprocess.run", return_value=p):
            proceed, signature, line = wrapper.recovery_gate()
        self.assertFalse(proceed)
        self.assertIn("rc_1", signature)

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
