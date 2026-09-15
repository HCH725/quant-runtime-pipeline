#!/usr/bin/env python3
"""Logical-layer checks for the Strategy C v1 preregistration (templates + counts + provenance).

No market data, no container, no network: stdlib only.  Covers
  * the frozen template pair validating with zero problems (counts + generic parameter contract),
  * the fingerprint_input being the frozen handoff value of the immutable family.json,
  * the instantiation path (placeholder substitution) producing a document pair that still
    validates and pins the deployed runner sha,
  * the registered negative controls (runtime/strategy_c_v1_counts.py --self-test) - each
    tampered registration must be rejected, so the validator is not vacuous.
"""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
import strategy_c_v1_counts as counts  # noqa: E402
import parameter_contract as pc  # noqa: E402
import instantiate_strategy_c_v1 as inst  # noqa: E402

FAMILY_ID = "funding-decile-extreme-contrarian-btc-v1"
FAMILY_JSON = "/Volumes/ExpansionDrive/qlib-results/%s/family.json" % FAMILY_ID


def load(path):
    with open(path) as fh:
        return json.load(fh)


class TestTemplates(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.round_spec = load(counts.DEFAULT_SPEC)
        cls.run_spec = load(counts.DEFAULT_RUN_SPEC)

    def test_template_pair_validates_clean(self):
        computed, problems, extra = counts.check(self.round_spec, self.run_spec)
        self.assertEqual(problems, [])
        self.assertEqual(computed["cohorts"], 1)
        self.assertEqual(computed["strategy_cases"], 3)
        self.assertEqual(computed["dca_configs"], 48)
        self.assertEqual(computed["expected_case_evaluations"], 1584)
        self.assertTrue(extra["domains_agree"])
        self.assertTrue(extra["counts_agree"])
        self.assertTrue(extra["versions_agree"])
        self.assertTrue(extra["split_agrees"])
        self.assertTrue(extra["provenance_agree"])
        self.assertTrue(extra["script_pinned"])
        self.assertTrue(extra["all_grid_outputs_declared"])

    def test_parameter_contract_is_generic_and_valid(self):
        contract = self.round_spec["parameter_contract"]
        self.assertEqual(contract["family_id"], FAMILY_ID)
        self.assertEqual(pc.validate_contract(contract), [])
        self.assertEqual(pc.validate_round_spec_contract(self.round_spec), [])
        self.assertEqual(contract["domain_cardinality"],
                         {"strategy": 3, "dca": 48, "per_cohort": 144})
        self.assertEqual(contract["composite_map"], {})
        self.assertEqual(contract["strategy_param_fields"], ["decile", "lookback_days"])

    def test_registered_grids_are_the_eleven_phase_grids(self):
        self.assertEqual(self.round_spec["expected"]["cohort_grid_kinds"], counts.PHASE_GRIDS)
        self.assertEqual(len(counts.PHASE_GRIDS), 11)
        self.assertIn("official_only", counts.PHASE_GRIDS)
        self.assertIn("cost_attrition_40bps", counts.PHASE_GRIDS)
        out = self.run_spec["expected_outputs"]
        for grid in counts.PHASE_GRIDS:
            self.assertIn("artifacts/grid_%s.csv" % grid, out)

    def test_primary_threshold_and_tracks_are_both_registered(self):
        dom = self.round_spec["parameter_domain"]
        self.assertEqual(dom["primary_threshold"], 0.10)
        self.assertEqual(dom["grid_deciles"], [0.05, 0.10, 0.20])
        self.assertIn("SOURCE_SPECIFIED_PRIMARY", dom["decile_status"])
        self.assertIn("SENSITIVITY_TRACKS", dom["decile_status"])
        self.assertIn("not to replace the primary", dom["decile_status"])
        self.assertEqual(self.round_spec["semantic_fingerprint"]["registered_axis_vs_family_fingerprint"],
                         self.round_spec["semantic_fingerprint"]["registered_axis_vs_family_fingerprint"])
        self.assertIn("delta is disclosed", self.round_spec["semantic_fingerprint"]["registered_axis_vs_family_fingerprint"])

    def test_split_and_official_only_window(self):
        data = self.round_spec["data"]
        self.assertEqual(data["data_end"], "2026-09-11")
        self.assertEqual(data["oos_end"], data["data_end"])
        self.assertEqual(data["historical_end"], "2025-09-30")
        self.assertEqual(data["official_only_start"], "2023-11-01")
        self.assertEqual(data["official_only_end"], "2025-09-30")
        self.assertLess(data["historical_end"], data["oos_start"])
        fsc = data["funding_series_contract"]
        self.assertIn("official_only_rerun", fsc)
        self.assertEqual(fsc["measured_at_pre_registration"]["modeled_funding"], 2005)

    def test_gates_are_the_registered_ones(self):
        gates = self.round_spec["gates"]
        for key, want in counts.GATES.items():
            self.assertEqual(gates[key], want)
        self.assertEqual(gates["min_episodes_is"], 20)
        self.assertEqual(gates["min_oos_sharpe"], 0.40)

    def test_family_level_falsification_forbids_pass(self):
        fl = self.round_spec["registered_family_level_falsification"]
        self.assertIn("never", fl["threshold-instability"]["landing"].lower())
        self.assertIn("NEVER PASS", fl["funding-provenance"]["landing"])
        self.assertIn("never PASS", "\n".join(self.round_spec["falsification"]))

    def test_frozen_fingerprint_is_the_handoff_value(self):
        """The immutable family.json is the ownership record; its fingerprint_input is copied
        verbatim and its digest must reproduce (the template never rewrites it)."""
        if not os.path.isfile(FAMILY_JSON):
            self.skipTest("family.json not mounted in this environment")
        family = load(FAMILY_JSON)
        self.assertEqual(family["kanban_task_id"], self.round_spec["kanban_task_id"].replace(
            "{{kanban_task_id}}", family["kanban_task_id"]))
        self.assertEqual(self.round_spec["semantic_fingerprint"]["fingerprint_input"],
                         family["fingerprint_input"])
        self.assertEqual(counts.fingerprint(family["fingerprint_input"]),
                         family["semantic_fingerprint"])


class TestInstantiation(unittest.TestCase):

    FAKE_SHA = "sha256:" + "a" * 64
    FAKE_TEST_SHA = "sha256:" + "b" * 64

    def test_substitution_leaves_no_placeholders_and_still_validates(self):
        round_template = load(counts.DEFAULT_SPEC)
        run_template = load(counts.DEFAULT_RUN_SPEC)
        rnd, run = inst.instantiate(round_template, run_template, "t_TESTCARD",
                                    "%s-r1" % FAMILY_ID, "%s-r1-u1" % FAMILY_ID,
                                    "2026-09-15T00:00:00Z", self.FAKE_SHA, self.FAKE_TEST_SHA)
        self.assertEqual(inst.leftover_placeholders(rnd), [])
        self.assertEqual(inst.leftover_placeholders(run), [])
        self.assertEqual(rnd["kanban_task_id"], "t_TESTCARD")
        self.assertEqual(run["task_id"], "t_TESTCARD")
        self.assertEqual(run["round_spec_path"],
                         "/results/%s/rounds/%s-r1/round-spec.json" % (FAMILY_ID, FAMILY_ID))
        self.assertEqual(rnd["semantic_fingerprint"]["semantic_fingerprint"],
                         counts.fingerprint(rnd["semantic_fingerprint"]["fingerprint_input"]))
        _, problems, _ = counts.check(rnd, run)
        self.assertEqual(problems, [])
        self.assertEqual(pc.validate_round_spec_contract(rnd), [])

    def test_instantiation_is_deterministic(self):
        args = (load(counts.DEFAULT_SPEC), load(counts.DEFAULT_RUN_SPEC), "t_TESTCARD",
                "%s-r1" % FAMILY_ID, "%s-r1-u1" % FAMILY_ID, "2026-09-15T00:00:00Z",
                self.FAKE_SHA, self.FAKE_TEST_SHA)
        a = inst.instantiate(*args)
        b = inst.instantiate(*args)
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))


class TestNegativeControls(unittest.TestCase):
    """The registered controls must all be detected: a validator that catches nothing is a
    validator that proves nothing."""

    def test_every_negative_control_is_detected(self):
        spec = load(counts.DEFAULT_SPEC)
        run = load(counts.DEFAULT_RUN_SPEC)
        cases = counts.self_test(spec, run)
        self.assertGreaterEqual(len(cases), 15)
        undetected = [c["case"] for c in cases if not c["detected"]]
        self.assertEqual(undetected, [])

    def test_a_shrunk_dca_domain_is_rejected(self):
        spec = load(counts.DEFAULT_SPEC)
        spec["dca_domain"]["grid"] = spec["dca_domain"]["grid"][:47]
        _, problems, _ = counts.check(spec, None)
        self.assertTrue(any("cartesian product" in p or "config_count" in p for p in problems))

    def test_a_missing_parameter_contract_is_rejected(self):
        spec = load(counts.DEFAULT_SPEC)
        spec.pop("parameter_contract")
        _, problems, _ = counts.check(spec, None)
        self.assertTrue(any("parameter_contract" in p for p in problems))

    def test_a_lowered_oos_floor_is_rejected(self):
        spec = load(counts.DEFAULT_SPEC)
        spec["gates"]["min_oos_sharpe"] = 0.10
        _, problems, _ = counts.check(spec, None)
        self.assertTrue(any("min_oos_sharpe" in p for p in problems))

    def test_a_run_spec_drifting_from_the_round_spec_is_rejected(self):
        spec = load(counts.DEFAULT_SPEC)
        run = load(counts.DEFAULT_RUN_SPEC)
        run["gates"]["min_episodes_is"] = 5
        _, problems, _ = counts.check(spec, run)
        self.assertTrue(any("run-spec gates.min_episodes_is" in p for p in problems))


if __name__ == "__main__":
    unittest.main(verbosity=2)
