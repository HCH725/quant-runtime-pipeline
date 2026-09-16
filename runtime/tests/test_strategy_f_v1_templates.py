#!/usr/bin/env python3
"""Strategy F v1 pre-registration templates: positive validation + negative controls.

The point of this file is that the frozen Strategy F templates (round-spec + run-spec) and the
counts/provenance validator agree, and that each provenance or arithmetic corruption is actually
CAUGHT - a validator that only ever says "ok" is not a check.  Every negative control asserts
the specific problem text it expects, so a silent pass-through fails the suite.
"""
import ast
import copy
import json
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME = os.path.dirname(HERE)
REPO = os.path.dirname(RUNTIME)
sys.path.insert(0, RUNTIME)
import strategy_f_v1_counts as counts  # noqa: E402
import parameter_contract as pc  # noqa: E402
import instantiate_strategy_f_v1 as inst  # noqa: E402

RUNNER = os.path.join(REPO, "container", "scripts", "60_strategy_f_run.py")
ROUND_TEMPLATE = os.path.join(RUNTIME, "templates", "strategy_f_v1_round_spec.template.json")
RUN_TEMPLATE = os.path.join(RUNTIME, "templates", "strategy_f_v1_run_spec.template.json")


def load(path):
    with open(path) as fh:
        return json.load(fh)


def probe_docs():
    """A fully substituted (round, run) pair, produced by the production instantiator."""
    round_tpl = load(ROUND_TEMPLATE)
    run_tpl = load(RUN_TEMPLATE)
    return inst.instantiate(round_tpl, run_tpl, "t_PROBE", counts.FAMILY_ID + "-r1",
                            counts.FAMILY_ID + "-r1-u1", "2026-09-16T00:00:00Z",
                            "sha256:" + "a" * 64, "sha256:" + "b" * 64)


class TestTemplatesValidate(unittest.TestCase):
    def test_round_spec_counts_and_contract_are_clean(self):
        round_spec, run_spec = probe_docs()
        computed, problems, _extra = counts.check(round_spec, run_spec)
        self.assertEqual(problems, [])
        self.assertEqual(pc.validate_round_spec_contract(round_spec), [])
        self.assertEqual(computed["cohorts"], 20)
        self.assertEqual(computed["strategy_cases"], 9)
        self.assertEqual(computed["dca_configs"], 48)
        self.assertEqual(computed["case_evaluations_per_grid"], 8640)
        self.assertEqual(computed["expected_case_evaluations"], 86400)

    def test_templates_carry_no_substituted_identity(self):
        for path in (ROUND_TEMPLATE, RUN_TEMPLATE):
            doc = load(path)
            self.assertIn("{{", json.dumps(doc))

    def test_fingerprint_input_is_the_frozen_handoff_value(self):
        round_spec, _run = probe_docs()
        self.assertEqual(round_spec["semantic_fingerprint"]["semantic_fingerprint"],
                         counts.fingerprint(round_spec["semantic_fingerprint"]["fingerprint_input"]))
        self.assertIn("stochastic-rsi-renko-2026-08-31", round_spec["semantic_fingerprint"]["fingerprint_input"])
        self.assertIn("selector=cohort-selector-v1;disposition=cohort-disposition-v1",
                      round_spec["semantic_fingerprint"]["fingerprint_input"])

    def test_parameter_contract_matches_the_registered_axes(self):
        round_spec, _run = probe_docs()
        contract = round_spec["parameter_contract"]
        self.assertEqual(contract["domain_cardinality"],
                         {"strategy": 9, "dca": 48, "per_cohort": 432})
        self.assertEqual(contract["strategy_param_fields"], ["brick_pct", "rsi_period"])
        self.assertEqual(contract["dca_param_fields"], list(counts.DCA_AXES))
        self.assertEqual(contract["composite_map"], {})


class TestNegativeControls(unittest.TestCase):
    def _problems(self, round_spec, run_spec=None):
        _computed, problems, _extra = counts.check(round_spec, run_spec)
        return problems

    def test_dca_grid_cell_removed_is_caught(self):
        round_spec, _run = probe_docs()
        tampered = copy.deepcopy(round_spec)
        tampered["dca_domain"]["grid"] = tampered["dca_domain"]["grid"][:-1]
        self.assertTrue(any("must carry the complete 48-cell DCA product" in p
                            for p in self._problems(tampered)))

    def test_dca_grid_duplicate_cell_is_caught(self):
        round_spec, _run = probe_docs()
        tampered = copy.deepcopy(round_spec)
        grid = tampered["dca_domain"]["grid"]
        grid[-1] = copy.deepcopy(grid[0])          # same length, one cell duplicated
        self.assertTrue(any("not the registered four-axis product" in p
                            for p in self._problems(tampered)))

    def test_searched_axis_marked_user_fixed_is_caught(self):
        round_spec, _run = probe_docs()
        tampered = copy.deepcopy(round_spec)
        tampered["authorization_invariants"]["spacing_pct"] = 0.01
        self.assertTrue(any("must not list the searched axis" in p
                            for p in self._problems(tampered)))

    def test_base_quote_status_relabelled_is_caught(self):
        round_spec, _run = probe_docs()
        tampered = copy.deepcopy(round_spec)
        tampered["dca_domain"]["base_quote_status"] = "USER_FIXED - operator evidence"
        self.assertTrue(any("base_quote_status must start with" in p
                            for p in self._problems(tampered)))

    def test_axis_status_relabelled_is_caught(self):
        round_spec, _run = probe_docs()
        tampered = copy.deepcopy(round_spec)
        tampered["dca_domain"]["invalidation_pct_status"] = "USER_FIXED - operator evidence"
        self.assertTrue(any("invalidation_pct_status must start with" in p
                            for p in self._problems(tampered)))

    def test_renko_contract_drift_is_caught(self):
        round_spec, _run = probe_docs()
        tampered = copy.deepcopy(round_spec)
        tampered["parameter_domain"]["renko_contract"]["source_price"] = "the source bar's HIGH"
        self.assertTrue(any("renko_contract must be the frozen construction contract" in p
                            for p in self._problems(tampered)))

    def test_gate_lowered_is_caught(self):
        round_spec, _run = probe_docs()
        tampered = copy.deepcopy(round_spec)
        tampered["gates"]["min_episodes_is"] = 5
        self.assertTrue(any("gates.min_episodes_is" in p for p in self._problems(tampered)))

    def test_split_moved_is_caught(self):
        round_spec, _run = probe_docs()
        tampered = copy.deepcopy(round_spec)
        tampered["data"]["oos_start"] = "2025-01-01"
        self.assertTrue(any("data.oos_start" in p for p in self._problems(tampered)))

    def test_symbol_dropped_is_caught(self):
        round_spec, _run = probe_docs()
        tampered = copy.deepcopy(round_spec)
        tampered["eligible_universe"]["symbols"] = ["BTCUSDT"]
        problems = self._problems(tampered)
        self.assertTrue(any("symbols must be the complete registered panel" in p for p in problems))
        self.assertTrue(any("cohort_count != the registered symbol x timeframe product" in p
                            for p in problems))

    def test_case_order_swapped_is_caught(self):
        round_spec, _run = probe_docs()
        tampered = copy.deepcopy(round_spec)
        cases = tampered["parameter_domain"]["grid_cases"]
        cases[0], cases[1] = cases[1], cases[0]
        self.assertTrue(any("the registered brick_pct x rsi_period product" in p
                            for p in self._problems(tampered)))

    def test_run_spec_provenance_conflict_is_caught(self):
        round_spec, run_spec = probe_docs()
        tampered = copy.deepcopy(run_spec)
        tampered["dca_domain"]["base_quote_status"] = "USER_FIXED - operator evidence"
        self.assertTrue(any("run-spec.dca_domain.base_quote_status" in p
                            for p in self._problems(round_spec, tampered)))

    def test_run_spec_script_path_must_be_container_path(self):
        round_spec, run_spec = probe_docs()
        tampered = copy.deepcopy(run_spec)
        tampered["script"]["path"] = "/Users/hong/workspace/60_strategy_f_run.py"
        self.assertTrue(any("run-spec.script.path" in p
                            for p in self._problems(round_spec, tampered)))

    def test_expected_total_cannot_drift(self):
        round_spec, run_spec = probe_docs()
        tampered = copy.deepcopy(run_spec)
        tampered["expected"]["expected_case_evaluations"] = 1000
        self.assertTrue(any("run-spec.expected.expected_case_evaluations" in p
                            for p in self._problems(round_spec, tampered)))


class TestEngineAgreesWithTheRegistration(unittest.TestCase):
    """The runner's own registered constants must equal the pre-registration's."""

    def setUp(self):
        with open(RUNNER) as fh:
            self.src = fh.read()

    def test_case_order_matches(self):
        node = None
        tree = ast.parse(self.src)
        for item in tree.body:
            if isinstance(item, ast.Assign) and getattr(item.targets[0], "id", None) == "CASE_ORDER":
                node = item
        self.assertIsNotNone(node, "the runner must declare CASE_ORDER")
        pairs = ast.literal_eval(node.value)
        self.assertEqual([tuple(p) for p in pairs], [tuple(c) for c in counts.STRATEGY_CASES])

    def test_row_fields_carry_the_contract_axes(self):
        tree = ast.parse(self.src)
        row_fields = None
        for item in tree.body:
            if isinstance(item, ast.Assign) and getattr(item.targets[0], "id", None) == "ROW_FIELDS":
                row_fields = ast.literal_eval(item.value)
        self.assertIsNotNone(row_fields)
        round_spec, _run = probe_docs()
        for field in round_spec["parameter_contract"]["row_fields"]:
            self.assertIn(field, row_fields)

    def test_phase_grid_kinds_match(self):
        tree = ast.parse(self.src)
        kinds = None
        for item in tree.body:
            if isinstance(item, ast.Assign) and getattr(item.targets[0], "id", None) == "COHORT_GRID_KINDS":
                kinds = ast.literal_eval(item.value)
        self.assertIsNotNone(kinds)
        self.assertEqual(list(kinds), list(counts.COHORT_GRID_KINDS))

    def test_engine_version_and_selector_versions_are_declared(self):
        self.assertIn('ENGINE_VERSION = "f-v1-engine-1.0.0"', self.src)
        self.assertIn('SELECTOR_VERSION = "cohort-selector-v1"', self.src)
        self.assertIn('DISPOSITION_VERSION = "cohort-disposition-v1"', self.src)
        self.assertIn('CONTRACT_SEMANTICS_VERSION = "v1.4.0"', self.src)

    def test_adverse_slippage_rule_is_the_registered_one(self):
        # entries/scale-ins pay for a buy and sell lower for a sell; exits are the mirror
        self.assertRegex(self.src, r"px = O\[entry\] \+ sign \* slip_ticks \* tick")
        self.assertRegex(self.src, r"xpx = base_price - sign \* slip_ticks \* tick")


if __name__ == "__main__":
    unittest.main(verbosity=2)
