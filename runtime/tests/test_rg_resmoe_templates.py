#!/usr/bin/env python3
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "runtime"))
import parameter_contract as pc
import rg_resmoe_counts as counts


class RGResMoETemplateTests(unittest.TestCase):
    def test_registered_counts(self):
        self.assertEqual(counts.expected_counts(), {"cohorts": 28, "strategy_cases_per_cohort": 1, "dca_configs_per_cohort": 48, "cases_per_cohort": 48, "case_evaluations_per_grid": 1344, "grid_count": 10, "case_evaluations_total": 13440})

    def test_templates_validate(self):
        round_spec = json.loads((ROOT / "runtime/templates/rg_resmoe_round_spec.template.json").read_text())
        run_spec = json.loads((ROOT / "runtime/templates/rg_resmoe_run_spec.template.json").read_text())
        self.assertFalse(pc.validate_contract(round_spec["parameter_contract"]))
        self.assertFalse(pc.validate_round_spec_contract(round_spec))
        self.assertFalse(pc.validate_contract(run_spec["parameter_contract"]))
        self.assertEqual(round_spec["family_id"], counts.FAMILY_ID)
        self.assertEqual(run_spec["expected"]["case_evaluations_total"], 13440)

    def test_instantiator_dry_run(self):
        proc = subprocess.run([sys.executable, str(ROOT / "runtime/instantiate_rg_resmoe.py"), "--dry-run", "--json"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        doc = json.loads(proc.stdout)
        self.assertTrue(doc["ok"])
        self.assertEqual(doc["expected_case_evaluations"], 13440)


if __name__ == "__main__":
    unittest.main()
