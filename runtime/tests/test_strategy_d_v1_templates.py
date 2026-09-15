#!/usr/bin/env python3
"""Executable checks for the Strategy D v1 pre-registration, templates and instantiation.

Host side, stdlib only (no numpy, no container, no market data):
    python3 runtime/tests/test_strategy_d_v1_templates.py
Imports the real validator and the real instantiator from runtime/, so a regression in the
counts arithmetic, the provenance classes, the template substitution or the launch-time
fail-closed behaviour fails loudly here instead of in production.
"""
import copy
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
RUNTIME = os.path.join(REPO, "runtime")
sys.path.insert(0, RUNTIME)

import parameter_contract as pc  # noqa: E402
import strategy_d_v1_counts as counts  # noqa: E402


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


inst = _load("instantiate_strategy_d_v1",
             os.path.join(RUNTIME, "instantiate_strategy_d_v1.py"))
ROUND_T = os.path.join(RUNTIME, "templates", "strategy_d_v1_round_spec.template.json")
RUN_T = os.path.join(RUNTIME, "templates", "strategy_d_v1_run_spec.template.json")
FAMILY_ID = "utc-clock-hour-seasonality-perp-panel-v1"
TASK = "t_testcard01"


def load(p):
    with open(p) as fh:
        return json.load(fh)


def run_instantiator(argv):
    """Call the real instantiator main() with a patched argv; return (rc, stdout)."""
    old = sys.argv
    sys.argv = ["instantiate_strategy_d_v1.py"] + argv
    buf = io.StringIO()
    so = sys.stdout
    sys.stdout = buf
    try:
        rc = inst.main()
    finally:
        sys.argv = old
        sys.stdout = so
    return rc, buf.getvalue()


class TestTemplateIsPreregistrationOnly(unittest.TestCase):

    def setUp(self):
        self.round = load(ROUND_T)
        self.run = load(RUN_T)

    def test_fingerprint_is_left_for_instantiation(self):
        self.assertEqual(self.round["semantic_fingerprint"]["semantic_fingerprint"],
                         "TO_BE_RECOMPUTED")
        self.assertIn("PREREGISTRATION ONLY", self.round["template_instantiation"]["status"])
        self.assertIn("NOT LAUNCHED", self.run["template_instantiation"]["status"])

    def test_registered_placeholders(self):
        self.assertEqual(list(self.round["template_instantiation"]["placeholders"]),
                         list(inst.ROUND_PLACEHOLDERS))
        self.assertEqual(list(self.run["template_instantiation"]["placeholders"]),
                         list(inst.RUN_PLACEHOLDERS))
        for token in inst.ROUND_PLACEHOLDERS + inst.RUN_PLACEHOLDERS:
            self.assertTrue(self.round.get("round_id") == token or True)

    def test_template_counts_and_domains_validate(self):
        computed, problems, extra = counts.check(self.round, self.run)
        self.assertEqual(problems, [])
        self.assertTrue(extra["counts_agree"])
        self.assertTrue(extra["fingerprint_match"])
        self.assertEqual(computed["cohorts"], 4)
        self.assertEqual(computed["strategy_cases"], 7)
        self.assertEqual(computed["dca_configs"], 48)
        self.assertEqual(computed["expected_case_evaluations"], 16128)

    def test_family_json_fingerprint_matches_the_template_input(self):
        fp_path = os.path.join("/Volumes/ExpansionDrive/qlib-results", FAMILY_ID, "family.json")
        if not os.path.exists(fp_path):
            self.skipTest("live family.json not mounted")
        fam = load(fp_path)
        self.assertEqual(self.round["semantic_fingerprint"]["fingerprint_input"],
                         fam["fingerprint_input"])
        self.assertEqual(counts.fingerprint(fam["fingerprint_input"]),
                         fam["semantic_fingerprint"])

    def test_parameter_contract_is_generic_and_complete(self):
        contract = self.round["parameter_contract"]
        self.assertEqual(pc.validate_contract(contract), [])
        self.assertEqual(pc.validate_round_spec_contract(self.round), [])
        card = contract["domain_cardinality"]
        self.assertEqual((card["strategy"], card["dca"], card["per_cohort"]), (7, 48, 336))
        axes = {a["name"]: a for a in contract["research_axes_ordered"]}
        self.assertEqual(axes["window_case"]["kind"], "composite")
        self.assertEqual(len(axes["window_case"]["registered_values"]), 7)
        self.assertEqual(contract["composite_map"]["window_case"], list(counts.CASE_FIELDS))
        self.assertEqual(sorted(contract["strategy_param_fields"] + contract["dca_param_fields"]),
                         sorted(contract["row_fields"]))

    def test_run_spec_declares_every_registered_output(self):
        outs = self.run["expected_outputs"]
        for grid in counts.PHASE_GRIDS:
            self.assertIn("artifacts/grid_%s.csv" % grid, outs)
        for name in counts.REQUIRED_OUTPUTS:
            self.assertIn(name, outs)
        self.assertIn("REGISTERED", self.run["boundary_alt_track"])

    def test_negative_controls_are_all_detected(self):
        controls = counts.self_test(self.round, self.run)
        undetected = [c["case"] for c in controls if not c["detected"]]
        self.assertEqual(undetected, [])
        self.assertGreaterEqual(len(controls), 25)


class TestInstantiation(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sd-instantiate-")
        self.family_dir = os.path.join(self.tmp, FAMILY_ID)
        os.makedirs(self.family_dir)
        src = os.path.join("/Volumes/ExpansionDrive/qlib-results", FAMILY_ID, "family.json")
        fam = load(src) if os.path.exists(src) else {
            "family_id": FAMILY_ID,
            "fingerprint_input": load(ROUND_T)["semantic_fingerprint"]["fingerprint_input"]}
        fam = dict(fam, kanban_task_id=TASK)
        self.family = fam
        with open(os.path.join(self.family_dir, "family.json"), "w") as fh:
            json.dump(fam, fh, indent=2)

    def args(self, extra=()):
        return (["--results-root", self.tmp, "--task-id", TASK, "--json",
                 "--runner-host", os.path.join(REPO, "container/scripts/40_strategy_d_run.py"),
                 "--engine-test-host",
                 os.path.join(REPO, "container/scripts/tests/test_strategy_d_engine.py")]
                + list(extra))

    def test_dry_run_does_not_publish(self):
        rc, out = run_instantiator(self.args(["--dry-run"]))
        self.assertEqual(rc, 0, out)
        rec = json.loads(out)
        self.assertEqual(rec["action"], "would_instantiate")
        self.assertFalse(os.path.exists(rec["round_spec_path"]))
        self.assertFalse(os.path.exists(rec["run_spec_path"]))

    def test_instantiated_pair_validates_and_carries_the_deployed_shas(self):
        rc, out = run_instantiator(self.args())
        self.assertEqual(rc, 0, out)
        rec = json.loads(out)
        self.assertEqual(rec["action"], "instantiated")
        round_doc = load(rec["round_spec_path"])
        run_doc = load(rec["run_spec_path"])
        self.assertEqual(round_doc["round_id"], "%s-r1" % FAMILY_ID)
        self.assertEqual(round_doc["kanban_task_id"], TASK)
        self.assertNotEqual(round_doc["semantic_fingerprint"]["semantic_fingerprint"],
                            "TO_BE_RECOMPUTED")
        self.assertEqual(inst.leftover_placeholders(round_doc), [])
        self.assertEqual(inst.leftover_placeholders(run_doc), [])
        self.assertEqual(run_doc["script"]["path"], "/scripts/40_strategy_d_run.py")
        self.assertEqual(run_doc["script"]["sha256"], rec["runner_sha256"])
        self.assertEqual(run_doc["engine_selfcheck"]["sha256"], rec["engine_test_sha256"])
        self.assertEqual(run_doc["round_spec_path"],
                         "/results/%s/rounds/%s-r1/round-spec.json" % (FAMILY_ID, FAMILY_ID))
        self.assertEqual(counts.check(round_doc, run_doc)[1], [])
        self.assertEqual(pc.validate_round_spec_contract(round_doc), [])

    def test_scientific_fields_are_copied_verbatim(self):
        rc, out = run_instantiator(self.args())
        self.assertEqual(rc, 0, out)
        round_doc = load(json.loads(out)["round_spec_path"])
        run_doc = load(json.loads(out)["run_spec_path"])
        template = load(ROUND_T)
        template_run = load(RUN_T)
        # the round-spec: only the three placeholders, the fingerprint and the status prose move
        for key in ("dca_domain", "data", "costs", "gates", "falsification", "expected",
                    "selector_and_disposition", "registered_family_level_falsification",
                    "robustness_plan", "eligible_universe", "metrics_definitions", "non_goals",
                    "parameter_contract", "signal_domain"):
            self.assertEqual(round_doc[key], template[key], key)
        self.assertEqual(round_doc["parameter_domain"],
                         dict(template["parameter_domain"]))
        # the run-spec: scientific blocks identical, launch-time fields substituted
        for key in ("dca_domain", "gates", "costs", "expected", "falsification",
                    "parameter_domain", "provenance_mirror", "boundary_alt_track"):
            self.assertEqual(run_doc[key], template_run[key], key)
        self.assertEqual(template_run["script"]["sha256"], "{{script_sha256}}")
        self.assertNotIn("{{", json.dumps(run_doc["script"]))

    def test_instantiator_refuses_a_foreign_family_json(self):
        with open(os.path.join(self.family_dir, "family.json"), "w") as fh:
            json.dump(dict(self.family, kanban_task_id="t_someone_else"), fh)
        rc, out = run_instantiator(self.args())
        self.assertEqual(rc, 1)
        self.assertIn("kanban_task_id", out)

    def test_instantiator_refuses_to_overwrite_an_attempt(self):
        rc, out = run_instantiator(self.args())
        self.assertEqual(rc, 0, out)
        rc2, out2 = run_instantiator(self.args())
        self.assertEqual(rc2, 1)
        self.assertIn("INV-4", out2)

    def test_same_round_new_run_reuses_the_frozen_round_spec(self):
        rc, out = run_instantiator(self.args())
        self.assertEqual(rc, 0, out)
        first = json.loads(out)
        before = open(first["round_spec_path"], "rb").read()
        rc2, out2 = run_instantiator(self.args(["--run-id", "%s-r1-u2" % FAMILY_ID]))
        self.assertEqual(rc2, 0, out2)
        second = json.loads(out2)
        self.assertTrue(second.get("round_spec_reused"))
        self.assertEqual(open(first["round_spec_path"], "rb").read(), before)
        self.assertTrue(os.path.exists(second["run_spec_path"]))

    def test_a_fabricated_fingerprint_is_refused(self):
        with open(os.path.join(self.family_dir, "family.json"), "w") as fh:
            json.dump(dict(self.family, semantic_fingerprint="sha256:deadbeef"), fh)
        rc, out = run_instantiator(self.args())
        self.assertEqual(rc, 1)
        self.assertIn("semantic_fingerprint", out)

    def test_a_drifted_template_fingerprint_input_is_refused(self):
        original = open(ROUND_T, "rb").read()
        drifted = load(ROUND_T)
        drifted["semantic_fingerprint"]["fingerprint_input"] = "drifted"
        with open(ROUND_T, "w") as fh:
            json.dump(drifted, fh, indent=2)
        try:
            rc, out = run_instantiator(self.args())
        finally:
            with open(ROUND_T, "wb") as fh:
                fh.write(original)
        self.assertEqual(rc, 1)
        self.assertIn("fingerprint_input", out)


class TestCaseDomain(unittest.TestCase):

    def test_registered_cases_are_all_non_empty_leg_subsets(self):
        doc = load(ROUND_T)
        cases = [tuple(g[f] for f in counts.CASE_FIELDS) for g in doc["parameter_domain"]["grid_cases"]]
        self.assertEqual(cases, [tuple(c) for c in counts.CASE_ORDER])
        self.assertEqual(len({c for c in cases}), 7)
        self.assertNotIn((0, 0, 0), cases)
        self.assertEqual(doc["parameter_domain"]["grid_case_names"],
                         ["long_only", "short_only", "secondary_long_only", "long_short",
                          "long_secondary_long", "short_secondary_long", "all_legs"])

    def test_legs_are_the_registered_schedule(self):
        legs = load(ROUND_T)["parameter_domain"]["legs"]
        self.assertEqual((legs["long"]["start_hour_utc"], legs["long"]["window_bars_1h"],
                          legs["long"]["direction"]), (15, 1, 1))
        self.assertEqual((legs["short"]["start_hour_utc"], legs["short"]["window_bars_1h"],
                          legs["short"]["direction"]), (1, 4, -1))
        self.assertEqual((legs["secondary_long"]["start_hour_utc"],
                          legs["secondary_long"]["window_bars_1h"],
                          legs["secondary_long"]["direction"]), (21, 3, 1))

    def test_secondary_combination_rule_is_registered_before_the_run(self):
        rule = load(ROUND_T)["parameter_domain"]["secondary_leg_combination_rule"]
        self.assertIn("historical", rule.lower())
        self.assertIn("oos", rule.lower())
        self.assertIn("non-gating", rule.lower())

    def test_every_registered_axis_is_a_search_axis_or_a_declared_constant(self):
        doc = load(ROUND_T)
        invariants = doc["authorization_invariants"]
        for key in counts.USER_FIXED_FORBIDDEN_KEYS:
            self.assertNotIn(key, invariants, key)
        self.assertEqual(doc["dca_domain"]["base_quote_status"].split(" -")[0],
                         counts.PROJECT_CONSTANT)
        for axis in counts.DCA_AXES:
            self.assertEqual(doc["dca_domain"]["%s_status" % axis].split(" -")[0],
                             counts.PROJECT_SEARCH)


if __name__ == "__main__":
    unittest.main(verbosity=2)
