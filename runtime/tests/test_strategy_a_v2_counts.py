#!/usr/bin/env python3
"""Logic-layer check for the Strategy A v2 pre-registration counter (runtime/strategy_a_v2_counts.py).

stdlib unittest only; no market data, no container, no Qlib.  Guards the two things the
v1.3.0 pre-registration must never get wrong:
  * the exact expected case arithmetic (20 cohorts x 12 strategy cases x 48 DCA configs
    x 9 phase grids = 103,680 case evaluations), and
  * the fail-closed behaviour of the counter itself (a weakened domain, a wrong declared
    count, a fingerprint that no longer carries the DCA domain, a split that moved, or a
    run-spec that disagrees with the round-spec must all be reported as problems).
"""
import copy
import importlib.util
import json
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))  # runtime/tests -> runtime -> repo
COUNTS = os.path.join(REPO, "runtime", "strategy_a_v2_counts.py")
ROUND_SPEC = os.path.join(REPO, "runtime", "templates", "strategy_a_v2_round_spec.template.json")
RUN_SPEC = os.path.join(REPO, "runtime", "templates", "strategy_a_v2_run_spec.template.json")

_spec = importlib.util.spec_from_file_location("sa_v2_counts", COUNTS)
sa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sa)


def load(path):
    with open(path) as fh:
        return json.load(fh)


class TestCounts(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.round_spec = load(ROUND_SPEC)
        cls.run_spec = load(RUN_SPEC)

    def test_registered_arithmetic_is_exactly_20x12x48x9(self):
        res = sa.validate(self.round_spec, self.run_spec)
        self.assertEqual(res["problems"], [])
        c = res["computed"]
        self.assertEqual(c["cohorts"], 20)
        self.assertEqual(c["strategy_cases_per_cohort"], 12)
        self.assertEqual(c["dca_configs_per_cohort"], 48)
        self.assertEqual(c["base_combinations_per_cohort"], 576)
        self.assertEqual(c["phase_grid_count"], 9)
        self.assertEqual(c["case_evaluations_per_grid"], 11520)
        self.assertEqual(c["case_evaluations_per_cohort_all_grids"], 5184)
        self.assertEqual(c["expected_case_evaluations"], 103680)
        self.assertEqual(c["expected_case_evaluations"], 20 * 12 * 48 * 9)

    def test_registered_dca_domain_is_the_card_domain(self):
        dca = self.round_spec["dca_domain"]
        self.assertEqual(dca["spacing_pct"], [0.01, 0.02, 0.03, 0.04])
        self.assertEqual(dca["size_multiplier"], [1.0, 1.1])
        self.assertEqual(dca["breakeven_tp_pct"], [0.01, 0.02, 0.03])
        self.assertEqual(dca["invalidation_pct"], [0.05, 0.10])
        self.assertEqual(dca["base_quote"], 1000)
        self.assertEqual([tuple(g[a] for a in sa.DCA_AXES) for g in dca["grid"]],
                         sorted(sa.product([dca[a] for a in sa.DCA_AXES])))

    def test_fingerprint_carries_dca_domain_and_versions(self):
        res = sa.validate(self.round_spec, self.run_spec)
        self.assertEqual(res["fingerprint"]["status"], "MATCH")
        fp = res["fingerprint"]["input"]
        for token in ("selector=cohort-selector-v1", "disposition=cohort-disposition-v1",
                      "spacing_pct=", "size_multiplier=", "breakeven_tp_pct=",
                      "invalidation_pct=", "symbols=", "long/flat", "2022-01-01..2026-09-10"):
            self.assertIn(token, fp)

    def test_narrowing_the_dca_grid_is_rejected(self):
        spec = copy.deepcopy(self.round_spec)
        spec["dca_domain"]["grid"] = spec["dca_domain"]["grid"][:40]
        spec["expected"]["dca_configs_per_cohort"] = 40
        res = sa.validate(spec, None)
        self.assertTrue(res["problems"])
        self.assertTrue(any("cartesian product" in p for p in res["problems"]))

    def test_wrong_declared_total_is_rejected(self):
        spec = copy.deepcopy(self.round_spec)
        spec["expected"]["expected_case_evaluations"] = 2160  # the v1 count: must not fit v2
        res = sa.validate(spec, None)
        self.assertTrue(any("expected_case_evaluations" in p for p in res["problems"]))

    def test_tampered_fingerprint_input_is_rejected(self):
        spec = copy.deepcopy(self.round_spec)
        spec["semantic_fingerprint"]["fingerprint_input"] = \
            spec["semantic_fingerprint"]["fingerprint_input"].replace("spacing_pct=", "ignored=")
        res = sa.validate(spec, None)
        self.assertTrue(any("fingerprint_input does not contain" in p for p in res["problems"]))

    def test_moved_split_is_rejected(self):
        spec = copy.deepcopy(self.round_spec)
        spec["data"]["oos_start"] = "2026-01-01"
        res = sa.validate(spec, None)
        self.assertTrue(any("split" in p or "overlap" in p for p in res["problems"]))

    def test_run_spec_domain_disagreement_is_rejected(self):
        run_spec = copy.deepcopy(self.run_spec)
        run_spec["dca_domain"]["spacing_pct"] = [0.01, 0.02]
        res = sa.validate(self.round_spec, run_spec)
        self.assertTrue(any("run-spec domains disagree" in p for p in res["problems"]))

    def test_uninstantiated_placeholders_are_not_counted_as_science(self):
        # the run-spec template must still declare the real counts even before instantiation
        self.assertTrue("{{script_sha256}}" in self.run_spec["script"]["sha256"])
        self.assertEqual(self.run_spec["expected"]["expected_case_evaluations"], 103680)

    # ------------------------------------------------------------------
    # audit F2: DCA provenance labels must match the declared search domain
    # ------------------------------------------------------------------

    def test_dca_provenance_classes_match_the_declared_search_domain(self):
        dca = self.round_spec["dca_domain"]
        self.assertTrue(dca["base_quote_status"].startswith(sa.PROJECT_CONSTANT))
        self.assertTrue(dca["size_multiplier_status"].startswith(sa.PROJECT_SEARCH))
        invariants = self.round_spec["dca_execution_semantics"]["user_fixed_invariants"]
        for key in sa.USER_FIXED_FORBIDDEN_KEYS:
            self.assertNotIn(key, invariants, "a searched axis/project constant is not user-fixed")
        for key in sa.USER_FIXED_REQUIRED_KEYS:
            self.assertIn(key, invariants, "operator-evidenced invariants must stay registered")
        # the two registered documents must carry the same classification
        res = sa.validate(self.round_spec, self.run_spec)
        self.assertEqual(res["problems"], [])
        self.assertTrue(res["run_spec_check"]["provenance_agrees"])

    def test_base_quote_labelled_user_fixed_is_rejected(self):
        spec = copy.deepcopy(self.round_spec)
        spec["dca_domain"]["base_quote_status"] = \
            "user-fixed invariant, held constant across the whole domain (it is not searched)"
        spec["dca_execution_semantics"]["user_fixed_invariants"]["base_quote_usdt"] = 1000
        res = sa.validate(spec, None)
        self.assertTrue(any("base_quote_status" in p for p in res["problems"]), res["problems"])
        self.assertTrue(any("user_fixed_invariants.base_quote_usdt" in p
                            for p in res["problems"]), res["problems"])

    def test_searchable_multiplier_labelled_user_fixed_is_rejected(self):
        spec = copy.deepcopy(self.round_spec)
        spec["dca_domain"]["size_multiplier_status"] = \
            "user-fixed invariant for this production rail (1.0 for flat sizing)"
        spec["dca_execution_semantics"]["user_fixed_invariants"]["size_multiplier"] = 1.1
        res = sa.validate(spec, None)
        self.assertTrue(any("size_multiplier_status" in p for p in res["problems"]),
                        res["problems"])
        self.assertTrue(any("user_fixed_invariants.size_multiplier" in p
                            for p in res["problems"]), res["problems"])

    def test_stripping_the_operator_evidenced_invariants_is_rejected(self):
        spec = copy.deepcopy(self.round_spec)
        for key in ("leverage", "tranches", "no_add_after_flat_or_kill"):
            spec["dca_execution_semantics"]["user_fixed_invariants"].pop(key)
        res = sa.validate(spec, None)
        self.assertEqual(len([p for p in res["problems"] if "user_fixed_invariants" in p]), 3)

    def test_run_spec_provenance_disagreement_is_rejected(self):
        run_spec = copy.deepcopy(self.run_spec)
        run_spec["dca_domain"]["base_quote_status"] = "user-fixed invariant (not searched)"
        res = sa.validate(self.round_spec, run_spec)
        self.assertFalse(res["run_spec_check"]["provenance_agrees"])
        self.assertTrue(any("provenance classes disagree" in p for p in res["problems"]),
                        res["problems"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
