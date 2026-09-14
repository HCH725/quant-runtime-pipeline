#!/usr/bin/env python3
"""Logic checks for preflight P10 (contract 16.2: `run-spec.json` + `script.sha256` 相符).

P10 is the launch gate for "this spec points at this program", so it must never report PASS without
an actual sha256 recomputation.  Since v1.8 (contract 26.1) P10 additionally refuses an attempt
whose frozen round-spec carries no valid generic `parameter_contract`: such a family fails closed
in every post-survivor consumer, i.e. after the whole compute.  These checks drive
`preflight.p9_p10` on temp attempt dirs with the real results-tree shape
(`<root>/<family>/rounds/<round>/attempts/<run>/`), with the host scripts dir injected through the
existing `/scripts` mount mapping (no container needed).

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
import parameter_contract as pc  # noqa: E402

SPEC_KEYS = {"schema_version": 1, "family_id": "fam-a", "round_id": "fam-a-r1", "run_id": "fam-a-r1-u1",
             "task_id": "t_SMOKE", "kanban_board": "quant-strategy-research"}
B_V2_TEMPLATE = RUNTIME / "templates" / "strategy_b_v2_round_spec.template.json"


class P10Case(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="qrp-p10-test-")
        self.round = Path(self.root) / "family" / "rounds" / "fam-a-r1"
        self.attempt = self.round / "attempts" / "fam-a-r1-u1"
        self.attempt.mkdir(parents=True)
        self.host_scripts = Path(self.root) / "host-scripts"
        self.host_scripts.mkdir()
        self.script = self.host_scripts / "strategy.py"
        self.script.write_text("print('strategy')\n")
        self.sha = "sha256:" + hashlib.sha256(self.script.read_bytes()).hexdigest()
        # the legacy A v2 shape resolves through the in-code bridge (v1.8 backward compatibility)
        self.write_round_spec({"family_id": pc.LEGACY_A_FAMILY_ID})

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def write_round_spec(self, doc):
        (self.round / "round-spec.json").write_text(json.dumps(doc))

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

    # --- v1.8 / contract 26.1: the frozen round-spec must carry the parameter contract ---
    def test_round_spec_with_valid_parameter_contract_passes_the_launch_gate(self):
        """The real, forward-authored B v2 template round-spec is the launch-gate shape."""
        self.write_round_spec(json.loads(B_V2_TEMPLATE.read_text()))
        by_id = self.assert_p10("PASS")
        self.assertNotIn("parameter_contract:", by_id["P10"]["detail"])

    def test_round_spec_without_parameter_contract_fails_the_launch_gate(self):
        """The pre-migration r1 shape (non-legacy family, no schema) must be refused."""
        doc = json.loads(B_V2_TEMPLATE.read_text())
        doc.pop("parameter_contract")
        self.write_round_spec(doc)
        by_id = self.assert_p10("FAIL")
        self.assertIn("round-spec parameter_contract", by_id["P10"]["detail"])
        self.assertIn("fail closed", by_id["P10"]["detail"])

    def test_invalid_parameter_contract_fails_the_launch_gate(self):
        doc = json.loads(B_V2_TEMPLATE.read_text())
        doc["parameter_contract"]["domain_cardinality"] = {"strategy": 6, "dca": 48, "per_cohort": 288}
        self.write_round_spec(doc)
        by_id = self.assert_p10("FAIL")
        self.assertIn("parameter_contract invalid", by_id["P10"]["detail"])

    def test_schema_domain_mismatch_fails_the_launch_gate(self):
        """A contract that is internally valid but disagrees with the declared domain is refused."""
        doc = json.loads(B_V2_TEMPLATE.read_text())
        doc["parameter_domain"]["legal_cases_per_cohort"] = 121
        self.write_round_spec(doc)
        by_id = self.assert_p10("FAIL")
        self.assertIn("schema domain mismatch", by_id["P10"]["detail"])

    def test_missing_round_spec_fails_the_launch_gate(self):
        (self.round / "round-spec.json").unlink()
        by_id = self.assert_p10("FAIL")
        self.assertIn("missing", by_id["P10"]["detail"])

    def test_legacy_a_round_spec_still_passes_the_contract_gate(self):
        """Backward compatibility: the pre-schema A v2 bridge resolves without a contract."""
        self.assertEqual(preflight.round_spec_contract_problem(str(self.attempt)), None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
