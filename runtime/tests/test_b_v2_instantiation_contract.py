#!/usr/bin/env python3
"""Contract 26.1 wiring checks for the B v2 launcher (runtime/instantiate_strategy_b_v2.py).

The one-time migration card t_67481d49 requires the launcher to validate the generic
parameter_contract BEFORE anything is published or computed: the forward template must carry it,
a reused (same-round retry) round-spec must carry it, and the pre-v1.8 shape must be refused
instead of publishing a run-spec that would fail closed ~11 h later in the post-survivor chain.

Run: python3 runtime/tests/test_b_v2_instantiation_contract.py   (stdlib unittest, no container)
"""
import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME))
import instantiate_strategy_b_v2 as inst  # noqa: E402
import parameter_contract as pc  # noqa: E402
import strategy_b_v2_counts as counts  # noqa: E402

TASK = "t_TESTSMOKE"
FAMILY = inst.FAMILY_ID
ROUND_ID = "%s-r1" % FAMILY


class InstantiationContractCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="qrp-instantiate-test-"))
        self.results = self.root / "results"
        (self.results / FAMILY).mkdir(parents=True)
        self.runner = self.root / "30_strategy_b_run.py"
        self.runner.write_text("# runner\n")
        self.engine_test = self.root / "test_strategy_b_engine.py"
        self.engine_test.write_text("# engine test\n")
        template = json.loads((RUNTIME / "templates" /
                               "strategy_b_v2_round_spec.template.json").read_text())
        fp_input = template["semantic_fingerprint"]["fingerprint_input"]
        (self.results / FAMILY / "family.json").write_text(json.dumps({
            "schema_version": 1, "family_id": FAMILY, "kanban_task_id": TASK,
            "semantic_fingerprint": counts.fingerprint(fp_input), "fingerprint_input": fp_input}))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    @property
    def round_spec_path(self):
        return self.results / FAMILY / "rounds" / ROUND_ID / "round-spec.json"

    def run_spec_path(self, suffix):
        return self.results / FAMILY / "rounds" / ROUND_ID / "attempts" / ("%s-%s" % (ROUND_ID, suffix)) / "run-spec.json"

    def run_cli(self, suffix="u1"):
        argv = ["instantiate", "--results-root", str(self.results), "--task-id", TASK,
                "--round-id", ROUND_ID, "--run-id", "%s-%s" % (ROUND_ID, suffix),
                "--runner-host", str(self.runner), "--engine-test-host", str(self.engine_test),
                "--json"]
        saved = sys.argv
        sys.argv = argv
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                rc = inst.main()
        finally:
            sys.argv = saved
        try:
            record = json.loads(out.getvalue())
        except ValueError:
            record = {"stdout": out.getvalue()}
        return rc, record

    # --- fresh instantiation of the forward template ---
    def test_fresh_instantiation_publishes_a_contract_carrying_round_spec(self):
        rc, record = self.run_cli("u1")
        self.assertEqual(rc, 0, record)
        self.assertEqual(record.get("action"), "instantiated", record)
        spec = json.loads(self.round_spec_path.read_text())
        self.assertIn("parameter_contract", spec)
        self.assertEqual(pc.validate_round_spec_contract(spec), [])
        self.assertEqual(pc.validate_contract(spec["parameter_contract"]), [])
        self.assertTrue(self.run_spec_path("u1").is_file())

    # --- same-round technical retry (the u3 path) ---
    def test_same_round_retry_reuses_a_contract_carrying_round_spec(self):
        self.assertEqual(self.run_cli("u1")[0], 0)
        before = self.round_spec_path.read_bytes()
        rc, record = self.run_cli("u2")
        self.assertEqual(rc, 0, record)
        self.assertTrue(record.get("round_spec_reused"), record)
        self.assertEqual(self.round_spec_path.read_bytes(), before)
        self.assertTrue(self.run_spec_path("u2").is_file())

    def test_same_round_retry_refuses_the_pre_v1_8_round_spec(self):
        """The pre-migration r1 shape must fail closed, not publish a run-spec."""
        self.assertEqual(self.run_cli("u1")[0], 0)
        spec = json.loads(self.round_spec_path.read_text())
        spec.pop("parameter_contract")
        self.round_spec_path.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n")
        rc, record = self.run_cli("u2")
        self.assertEqual(rc, 1, record)
        self.assertFalse(self.run_spec_path("u2").exists())
        problems = " ".join(record.get("problems") or [])
        self.assertIn("parameter_contract", problems)
        self.assertIn("fail closed", problems)

    # --- forward template without the contract must never be publishable ---
    def test_fresh_instantiation_refuses_a_template_without_the_contract(self):
        template = json.loads((RUNTIME / "templates" /
                               "strategy_b_v2_round_spec.template.json").read_text())
        template.pop("parameter_contract")
        stripped = self.root / "stripped_round_spec.template.json"
        stripped.write_text(json.dumps(template, indent=2, ensure_ascii=False) + "\n")
        saved = inst.ROUND_TEMPLATE
        inst.ROUND_TEMPLATE = str(stripped)
        try:
            rc, record = self.run_cli("u1")
        finally:
            inst.ROUND_TEMPLATE = saved
        self.assertEqual(rc, 1, record)
        self.assertFalse(self.round_spec_path.exists())
        self.assertFalse(self.run_spec_path("u1").exists())
        self.assertIn("parameter_contract", " ".join(record.get("problems") or []))


if __name__ == "__main__":
    unittest.main(verbosity=2)
