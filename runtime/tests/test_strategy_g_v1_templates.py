#!/usr/bin/env python3
"""Strategy G v1 template / instantiation / provenance regression tests (pure stdlib).

Runs on the host:
    python3 runtime/tests/test_strategy_g_v1_templates.py

Pins the registration layer: the frozen templates must instantiate to a spec that passes the
counts + provenance validator with zero problems, and every tamper below must be REFUSED
(the checks are not tautologies).  No container, no market data, no network.
"""
import copy
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME = os.path.dirname(HERE)
sys.path.insert(0, RUNTIME)
import strategy_g_v1_counts as counts          # noqa: E402
import _author_strategy_g_template as author   # noqa: E402
import instantiate_strategy_g_v1 as inst       # noqa: E402


def templates():
    return (author.round_spec("TO_BE_RECOMPUTED", "TO_BE_RECOMPUTED"),
            author.run_spec("TO_BE_RECOMPUTED", "TO_BE_RECOMPUTED"))


class TestRegistration(unittest.TestCase):

    def setUp(self):
        self.round_t, self.run_t = templates()

    def test_frozen_templates_recompute_to_the_registered_counts(self):
        c = counts.recompute(self.round_t)
        self.assertEqual(c["cohorts"], 3)
        self.assertEqual(c["strategy_cases"], 12)
        self.assertEqual(c["dca_configs"], 48)
        self.assertEqual(c["base_combinations_per_cohort"], 576)
        self.assertEqual(c["case_evaluations_per_grid"], 1728)
        self.assertEqual(c["expected_case_evaluations"], 17280)
        self.assertEqual(c["cases"], [tuple(x) for x in counts.STRATEGY_CASES])

    def test_written_templates_match_the_authoring_source(self):
        # the fingerprint pair is deliberately sourced from the immutable /results family.json
        # at authoring time, so it is normalised out of this structural comparison
        for name, doc in (("strategy_g_v1_round_spec.template.json", self.round_t),
                          ("strategy_g_v1_run_spec.template.json", self.run_t)):
            path = os.path.join(RUNTIME, "templates", name)
            with open(path) as fh:
                on_disk = json.load(fh)
            a, b = copy.deepcopy(doc), copy.deepcopy(on_disk)
            for t in (a, b):
                sf = t.get("semantic_fingerprint")
                if isinstance(sf, dict):
                    sf.pop("fingerprint_input", None)
                    sf.pop("semantic_fingerprint", None)
            self.assertEqual(b, a, "%s drifted from the authoring source" % name)

    def test_instantiation_publishes_a_space_free_pair(self):
        round_spec, run_spec = inst.instantiate(self.round_t, self.run_t, "t_test", "fam-r1",
                                                "fam-r1-u1", "2026-01-01T00:00:00Z",
                                                "sha256:" + "a" * 64, "sha256:" + "b" * 64)
        self.assertEqual(inst.leftover_placeholders(round_spec), [])
        self.assertEqual(inst.leftover_placeholders(run_spec), [])
        problems = []
        c, probs, extra = counts.check(round_spec, run_spec)
        problems.extend(probs)
        self.assertEqual(problems, [], "instantiated pair must validate clean: %r" % (problems,))
        self.assertEqual(c["expected_case_evaluations"], 17280)
        self.assertEqual(run_spec["script"]["sha256"], "sha256:" + "a" * 64)
        self.assertEqual(run_spec["engine_selfcheck"]["sha256"], "sha256:" + "b" * 64)
        self.assertEqual(run_spec["data"]["funding_truth_status_windows"]["primary_uses"],
                         "the complete registered series (modelled + official), as a COST only")
        self.assertIn("funding_exposure_rule", run_spec["costs"])

    def test_contract_validation_of_the_instantiated_round_spec(self):
        import parameter_contract as pc
        round_spec, _run = inst.instantiate(self.round_t, self.run_t, "t_test", "fam-r1",
                                            "fam-r1-u1", "2026-01-01T00:00:00Z",
                                            "sha256:" + "a" * 64, "sha256:" + "b" * 64)
        self.assertEqual(pc.validate_round_spec_contract(round_spec), [])


class TestTampers(unittest.TestCase):
    """Every tamper below must be REFUSED by the frozen validator."""

    def setUp(self):
        self.round_spec, self.run_spec = inst.instantiate(
            author.round_spec("TO_BE_RECOMPUTED", "TO_BE_RECOMPUTED"),
            author.run_spec("TO_BE_RECOMPUTED", "TO_BE_RECOMPUTED"),
            "t_test", "fam-r1", "fam-r1-u1", "2026-01-01T00:00:00Z",
            "sha256:" + "a" * 64, "sha256:" + "b" * 64)

    def _problems(self, spec=None, run_spec=None):
        _c, probs, _e = counts.check(spec or self.round_spec, run_spec or self.run_spec)
        return probs

    def test_a_searched_axis_may_not_be_relabelled_user_fixed(self):
        bad = copy.deepcopy(self.round_spec)
        bad["authorization_invariants"]["conditions"] = bad["authorization_invariants"].pop(
            "initial_entry_and_scale_ins")
        bad["authorization_invariants"]["cond_high_vol"] = True
        probs = self._problems(spec=bad)
        self.assertTrue(any("must not list the searched axis" in p for p in probs), probs)

    def test_a_truncated_dca_grid_is_refused(self):
        bad = copy.deepcopy(self.round_spec)
        bad["dca_domain"]["grid"] = bad["dca_domain"]["grid"][:47]
        bad["dca_domain"]["config_count"] = 47
        probs = self._problems(spec=bad)
        self.assertTrue(any("complete 48-cell DCA product" in p for p in probs), probs)

    def test_a_rewritten_session_contract_is_refused(self):
        bad = copy.deepcopy(self.round_spec)
        bad["parameter_domain"]["session_contract"]["anchor_rule"] = (
            "anchor = the largest slot of the SAME day (look-ahead)")
        probs = self._problems(spec=bad)
        self.assertTrue(any("session_contract must be the frozen" in p for p in probs), probs)

    def test_a_moved_split_is_refused(self):
        bad = copy.deepcopy(self.round_spec)
        bad["data"]["oos_start"] = "2025-01-01"
        probs = self._problems(spec=bad)
        self.assertTrue(any("data.oos_start" in p for p in probs), probs)

    def test_a_strategy_case_outside_the_registered_order_is_refused(self):
        bad = copy.deepcopy(self.round_spec)
        bad["parameter_domain"]["grid_cases"] = list(
            reversed(bad["parameter_domain"]["grid_cases"]))
        probs = self._problems(spec=bad)
        self.assertTrue(any("registered order" in p for p in probs), probs)

    def test_a_run_spec_without_the_deployed_runner_pin_is_refused(self):
        bad = copy.deepcopy(self.run_spec)
        bad["script"]["sha256"] = "{{script_sha256}}"
        probs = self._problems(run_spec=bad)
        self.assertTrue(any("sha256:<hex>" in p for p in probs), probs)

    def test_an_unrecovered_detail_may_not_lose_its_research_defined_marker(self):
        bad = copy.deepcopy(self.round_spec)
        bad["parameter_domain"]["entry_timing_status"] = "SOURCE_SPECIFIED"
        probs = self._problems(spec=bad)
        self.assertTrue(any("entry_timing_status" in p for p in probs), probs)


if __name__ == "__main__":
    unittest.main(verbosity=2)
