#!/usr/bin/env python3
"""Logic checks for preflight P10 (contract 16.2: `run-spec.json` + `script.sha256` 相符).

P10 is the launch gate for "this spec points at this program", so it must never report PASS without
an actual sha256 recomputation. These checks drive `preflight.p9_p10` on temp attempt dirs, with the
host scripts dir injected through the existing `/scripts` mount mapping (no container needed).

Run: python3 runtime/tests/test_preflight_p10.py     (stdlib unittest, no dependencies)
"""
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME))
import preflight  # noqa: E402

SPEC_KEYS = {"schema_version": 1, "family_id": "fam-a", "round_id": "fam-a-r1", "run_id": "fam-a-r1-u1",
             "task_id": "t_SMOKE", "kanban_board": "quant-strategy-research"}


class P10Case(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="qrp-p10-test-")
        self.attempt = Path(self.root) / "attempt"
        self.attempt.mkdir()
        self.host_scripts = Path(self.root) / "host-scripts"
        self.host_scripts.mkdir()
        self.script = self.host_scripts / "strategy.py"
        self.script.write_text("print('strategy')\n")
        self.sha = "sha256:" + hashlib.sha256(self.script.read_bytes()).hexdigest()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def run_p10(self, script=None, sha=None, drop_run_spec=False, terminals=()):
        if not drop_run_spec:
            spec = dict(SPEC_KEYS)
            spec["script"] = script if script is not None else {"path": "/scripts/strategy.py", "sha256": sha or self.sha}
            (self.attempt / "run-spec.json").write_text(json.dumps(spec))
        for term in terminals:
            (self.attempt / term).write_text("{}")
        checks = []
        preflight.p9_p10(checks, str(self.attempt), str(self.host_scripts))
        by_id = {c["id"]: c for c in checks}
        return by_id

    def assert_p10(self, expected, **kwargs):
        by_id = self.run_p10(**kwargs)
        self.assertEqual(by_id["P10"]["status"], expected, by_id["P10"]["detail"])
        return by_id

    # --- mapped / host-readable paths must actually be recomputed ---
    def test_container_scripts_path_resolved_via_host_mapping(self):
        by_id = self.assert_p10("PASS")
        self.assertIn(str(self.script), by_id["P10"]["detail"])
        self.assertIn("recomputed", by_id["P10"]["detail"])

    def test_absolute_host_path_is_recomputed(self):
        self.assert_p10("PASS", script={"path": str(self.script), "sha256": self.sha})

    def test_sha_mismatch_fails(self):
        self.assert_p10("FAIL", script={"path": "/scripts/strategy.py", "sha256": "sha256:" + "0" * 64})

    # --- F3 (audit v2): unverifiable sha256 must never PASS ---
    def test_unresolvable_path_is_not_verified(self):
        by_id = self.assert_p10("FAIL",
                                script={"path": "/scripts/missing.py", "sha256": "sha256:deadbeef" + "0" * 56})
        self.assertIn("NOT VERIFIED", by_id["P10"]["detail"])

    def test_path_outside_the_mapping_is_not_verified(self):
        by_id = self.assert_p10("FAIL", script={"path": "/opt/other/strategy.py", "sha256": self.sha})
        self.assertIn("NOT VERIFIED", by_id["P10"]["detail"])

    def test_missing_sha_is_fail(self):
        self.assert_p10("FAIL", script={"path": "/scripts/strategy.py"})

    def test_absent_script_block_is_fail(self):
        spec = dict(SPEC_KEYS)          # run-spec without a `script` key at all
        (self.attempt / "run-spec.json").write_text(json.dumps(spec))
        checks = []
        preflight.p9_p10(checks, str(self.attempt), str(self.host_scripts))
        by_id = {c["id"]: c for c in checks}
        self.assertEqual(by_id["P10"]["status"], "FAIL")
        # Finding D regression: `script` is a required key; absent → explicit diagnostic
        self.assertIn("missing keys", by_id["P10"]["detail"])
        self.assertIn("script", by_id["P10"]["detail"])

    def test_missing_run_spec_is_fail(self):
        self.assert_p10("FAIL", drop_run_spec=True)

    def test_terminal_sentinel_fails_the_launch_gate(self):
        by_id = self.run_p10(terminals=("DONE",))          # INV-15: terminal evidence forbids re-run
        self.assertEqual(by_id["P9"]["status"], "FAIL")
        evaluated = [c for c in by_id.values() if c["status"] != "NA"]
        self.assertTrue([c for c in evaluated if c["status"] != "PASS"])

    def test_no_attempt_dir_is_not_evaluated(self):
        checks = []
        preflight.p9_p10(checks, None)
        self.assertEqual([c["status"] for c in checks], ["NA", "NA"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
