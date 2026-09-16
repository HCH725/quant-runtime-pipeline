#!/usr/bin/env python3
"""Strategy H v1 template / instantiation / provenance regression tests (pure stdlib).

Runs on the host:
    python3 runtime/tests/test_strategy_h_v1_templates.py
    python3 -m unittest discover -s runtime/tests -p 'test_strategy_h_v1_templates.py' -v

Pins the registration layer of family markov-chain-volume-price-state-2026-08-31: the frozen
templates must instantiate to a spec that passes the counts + provenance validator AND the
generic parameter contract with zero problems, and every tamper below must be REFUSED (the
checks are not tautologies).  No container, no market data, no network.

The instantiation CLI test runs entirely inside a fresh mkdtemp scratch directory: nothing is
written under the results root and nothing is deleted afterwards.

Note on the fingerprint pair: at AUTHORING time the pair is copied verbatim from the immutable
/results family.json when that file exists (otherwise it keeps the literal TO_BE_RECOMPUTED
sentinel); the structural comparison below normalises the pair out, exactly like the G test.
"""
import copy
import hashlib
import io
import contextlib
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME = os.path.dirname(HERE)
sys.path.insert(0, RUNTIME)
import strategy_h_v1_counts as counts          # noqa: E402
import _author_strategy_h_template as author   # noqa: E402
import instantiate_strategy_h_v1 as inst       # noqa: E402
import parameter_contract as pc                # noqa: E402

ROUND_TEMPLATE_PATH = os.path.join(RUNTIME, "templates",
                                   "strategy_h_v1_round_spec.template.json")
RUN_TEMPLATE_PATH = os.path.join(RUNTIME, "templates", "strategy_h_v1_run_spec.template.json")
# design_spec.md section 2: result.json + 12 artifacts/*.json + the 10 grid CSVs
REGISTERED_ARTIFACTS = [
    "result.json", "artifacts/cohort_results.json", "artifacts/cohort_survivors.json",
    "artifacts/assertions.json", "artifacts/dca_layer_histogram.json",
    "artifacts/state_layer.json", "artifacts/signal_layer.json",
    "artifacts/family_falsification.json", "artifacts/robustness_diagnostics.json",
    "artifacts/funding_series.json", "artifacts/input_manifest.json",
    "artifacts/bins_build.json", "artifacts/progress.json",
] + ["artifacts/grid_%s.csv" % g for g in counts.COHORT_GRID_KINDS]
TEST_SHAS = ("sha256:" + "a" * 64, "sha256:" + "b" * 64)


def templates():
    return (author.round_spec("TO_BE_RECOMPUTED", "TO_BE_RECOMPUTED"),
            author.run_spec("TO_BE_RECOMPUTED", "TO_BE_RECOMPUTED"))


def instantiated():
    round_t, run_t = templates()
    return inst.instantiate(round_t, run_t, "t_test", "fam-r1", "fam-r1-u1",
                            "2026-01-01T00:00:00Z", TEST_SHAS[0], TEST_SHAS[1])


class TestRegistration(unittest.TestCase):

    def setUp(self):
        self.round_t, self.run_t = templates()

    def test_frozen_templates_recompute_to_the_registered_counts(self):
        c = counts.recompute(self.round_t)
        self.assertEqual(c["cohorts"], 4)
        self.assertEqual(c["strategy_cases"], 27)
        self.assertEqual(c["dca_configs"], 48)
        self.assertEqual(c["base_combinations_per_cohort"], 1296)
        self.assertEqual(c["case_evaluations_per_grid"], 5184)
        self.assertEqual(c["expected_case_evaluations"], 51840)
        self.assertEqual(c["cases"], [tuple(x) for x in counts.STRATEGY_CASES])

    def test_written_templates_match_the_authoring_source(self):
        # the fingerprint pair is deliberately sourced from the immutable /results family.json
        # at authoring time, so it is normalised out of this structural comparison
        for path, doc in ((ROUND_TEMPLATE_PATH, self.round_t), (RUN_TEMPLATE_PATH, self.run_t)):
            with open(path) as fh:
                on_disk = json.load(fh)
            a, b = copy.deepcopy(doc), copy.deepcopy(on_disk)
            for t in (a, b):
                sf = t.get("semantic_fingerprint")
                if isinstance(sf, dict):
                    sf.pop("fingerprint_input", None)
                    sf.pop("semantic_fingerprint", None)
            self.assertEqual(b, a, "%s drifted from the authoring source" % path)

    def test_the_only_unsubstituted_tokens_are_the_registered_placeholders(self):
        for doc, allowed in ((self.round_t, author.ROUND_PLACEHOLDERS),
                             (self.run_t, author.RUN_PLACEHOLDERS)):
            self.assertEqual(author.token_audit(doc, allowed), ([], []),
                             "templates must carry exactly the registered placeholders")

    def test_the_run_spec_registers_the_seven_placeholders_and_the_engine_paths(self):
        self.assertEqual(self.run_t["template_instantiation"]["placeholders"],
                         ["{{round_id}}", "{{run_id}}", "{{task_id}}", "{{created_at_utc}}",
                          "{{script_sha256}}", "{{engine_selfcheck_sha256}}",
                          "{{round_spec_path}}"])
        self.assertEqual(self.run_t["script"]["path"], "/scripts/80_strategy_h_run.py")
        self.assertEqual(self.run_t["engine_selfcheck"]["script"],
                         "/scripts/tests/test_strategy_h_engine.py")

    def test_the_run_spec_registers_the_23_engine_artifacts(self):
        self.assertEqual(len(REGISTERED_ARTIFACTS), 23)
        self.assertEqual(self.run_t["expected_outputs"], REGISTERED_ARTIFACTS)
        self.assertEqual(sorted(self.round_t["artifacts_required"]),
                         sorted(REGISTERED_ARTIFACTS))

    def test_instantiation_publishes_a_space_free_pair(self):
        round_spec, run_spec = instantiated()
        self.assertEqual(inst.leftover_placeholders(round_spec), [])
        self.assertEqual(inst.leftover_placeholders(run_spec), [])
        problems = []
        c, probs, extra = counts.check(round_spec, run_spec)
        problems.extend(probs)
        self.assertEqual(problems, [], "instantiated pair must validate clean: %r" % (problems,))
        self.assertEqual(c["expected_case_evaluations"], 51840)
        self.assertEqual(run_spec["script"]["sha256"], TEST_SHAS[0])
        self.assertEqual(run_spec["engine_selfcheck"]["sha256"], TEST_SHAS[1])
        self.assertEqual(run_spec["parameter_domain"]["markov_contract"],
                         counts.MARKOV_CONTRACT)
        self.assertEqual(round_spec["parameter_domain"]["constants"], counts.MARKOV_CONSTANTS)
        self.assertEqual(run_spec["data"]["funding_truth_status_windows"]["primary_uses"],
                         "the complete registered series (modelled + official), as a COST only")
        self.assertIn("funding_exposure_rule", run_spec["costs"])

    def test_contract_validation_of_the_instantiated_round_spec(self):
        round_spec, _run = instantiated()
        self.assertEqual(pc.validate_round_spec_contract(round_spec), [])
        self.assertEqual(pc.validate_contract(round_spec["parameter_contract"]), [])

    def test_the_parameter_contract_is_the_registered_product(self):
        round_spec, _run = instantiated()
        contract = round_spec["parameter_contract"]
        self.assertEqual(contract["row_fields"],
                         list(counts.CASE_FIELDS) + list(counts.DCA_AXES))
        self.assertEqual(contract["dca_param_fields"], list(counts.DCA_AXES))
        names = [ax["name"] for ax in contract["research_axes_ordered"]]
        self.assertEqual(names, ["window_case"] + list(counts.DCA_AXES))
        composite = contract["research_axes_ordered"][0]
        self.assertEqual(composite["kind"], "composite")
        self.assertEqual(composite["members"], list(counts.CASE_FIELDS))
        self.assertEqual(composite["registered_values"],
                         [list(case) for case in counts.STRATEGY_CASES])
        self.assertEqual(contract["composite_map"], {"window_case": list(counts.CASE_FIELDS)})
        self.assertEqual(contract["domain_cardinality"],
                         {"strategy": 27, "dca": 48, "per_cohort": 1296})
        self.assertEqual(contract["strategy_param_fields"], list(counts.CASE_FIELDS))
        rendered = pc.render_parameter_contract(contract)
        self.assertIn("cardinality: strategy=27 dca=48 per-cohort=1296", rendered)
        self.assertIn("composite: window_case->[thr_1_0", rendered)


class TestTampers(unittest.TestCase):
    """Every tamper below must be REFUSED by the frozen validator."""

    def setUp(self):
        self.round_spec, self.run_spec = instantiated()

    def _problems(self, spec=None, run_spec=None):
        _c, probs, _e = counts.check(spec or self.round_spec, run_spec or self.run_spec)
        return probs

    # ---- provenance classification (contract 7.2 v1.3.1)
    def test_a_searched_axis_may_not_be_relabelled_user_fixed(self):
        bad = copy.deepcopy(self.round_spec)
        inv = bad["authorization_invariants"]
        inv["min_signal_probability"] = inv.pop("initial_entry_and_scale_ins")
        probs = self._problems(spec=bad)
        self.assertTrue(any("must not list the searched axis" in p for p in probs), probs)

    def test_a_project_constant_may_not_be_relabelled_user_fixed(self):
        bad = copy.deepcopy(self.round_spec)
        bad["authorization_invariants"]["base_quote"] = 1000
        probs = self._problems(spec=bad)
        self.assertTrue(any("must not list the searched axis or project constant" in p
                            for p in probs), probs)

    def test_a_lost_user_fixed_invariant_is_refused(self):
        bad = copy.deepcopy(self.round_spec)
        bad["authorization_invariants"].pop("tranche_12")
        probs = self._problems(spec=bad)
        self.assertTrue(any("user-fixed invariant" in p for p in probs), probs)

    def test_an_unrecovered_detail_may_not_lose_its_research_defined_marker(self):
        bad = copy.deepcopy(self.round_spec)
        bad["parameter_domain"]["state_availability_status"] = "SOURCE_SPECIFIED"
        probs = self._problems(spec=bad)
        self.assertTrue(any("state_availability_status" in p for p in probs), probs)

    # ---- the registered science
    def test_a_rewritten_markov_contract_is_refused(self):
        bad = copy.deepcopy(self.round_spec)
        bad["parameter_domain"]["markov_contract"]["state_availability"] = (
            "the state of a bin is attached to every bar inside that bin (the source's "
            "left-edge resample, backward as-of merged) - the look-ahead defect")
        probs = self._problems(spec=bad)
        self.assertTrue(any("markov_contract must be the frozen adapted markov contract" in p
                            for p in probs), probs)

    def test_a_tampered_markov_constant_is_refused(self):
        bad = copy.deepcopy(self.round_spec)
        bad["parameter_domain"]["constants"]["min_row_observations"] = 3
        probs = self._problems(spec=bad)
        self.assertTrue(any("constants.min_row_observations" in p for p in probs), probs)

    def test_a_tampered_state_count_is_refused(self):
        bad = copy.deepcopy(self.round_spec)
        bad["parameter_domain"]["constants"]["state_count"] = 9
        probs = self._problems(spec=bad)
        self.assertTrue(any("constants.state_count" in p for p in probs), probs)

    def test_a_strategy_case_outside_the_registered_order_is_refused(self):
        bad = copy.deepcopy(self.round_spec)
        bad["parameter_domain"]["grid_cases"] = list(
            reversed(bad["parameter_domain"]["grid_cases"]))
        probs = self._problems(spec=bad)
        self.assertTrue(any("registered order" in p for p in probs), probs)

    def test_a_tampered_strategy_axis_grid_is_refused(self):
        bad = copy.deepcopy(self.round_spec)
        bad["parameter_domain"]["sequence_length_grid"] = [1, 2, 4]
        probs = self._problems(spec=bad)
        self.assertTrue(any("sequence_length_grid" in p for p in probs), probs)

    # ---- the counts
    def test_a_tampered_expected_count_is_refused(self):
        bad = copy.deepcopy(self.round_spec)
        bad["expected"]["expected_case_evaluations"] = 1296
        probs = self._problems(spec=bad)
        self.assertTrue(any("expected.expected_case_evaluations" in p for p in probs), probs)

    def test_a_tampered_base_combination_count_is_refused(self):
        bad = copy.deepcopy(self.round_spec)
        bad["expected"]["base_combinations_per_cohort"] = 48
        probs = self._problems(spec=bad)
        self.assertTrue(any("base_combinations_per_cohort" in p for p in probs), probs)

    def test_a_tampered_grid_count_is_refused(self):
        bad = copy.deepcopy(self.round_spec)
        bad["expected"]["cohort_grid_kinds"] = bad["expected"]["cohort_grid_kinds"][:9]
        probs = self._problems(spec=bad)
        self.assertTrue(any("ten registered phase grids" in p for p in probs), probs)

    def test_a_tampered_gate_is_refused(self):
        bad = copy.deepcopy(self.round_spec)
        bad["gates"]["min_episodes_oos"] = 25
        probs = self._problems(spec=bad)
        self.assertTrue(any("gates.min_episodes_oos" in p for p in probs), probs)

    # ---- the DCA domain
    def test_a_truncated_dca_grid_is_refused(self):
        bad = copy.deepcopy(self.round_spec)
        bad["dca_domain"]["grid"] = bad["dca_domain"]["grid"][:47]
        bad["dca_domain"]["config_count"] = 47
        probs = self._problems(spec=bad)
        self.assertTrue(any("complete 48-cell DCA product" in p for p in probs), probs)

    def test_a_tampered_dca_axis_value_is_refused(self):
        bad = copy.deepcopy(self.round_spec)
        bad["dca_domain"]["spacing_pct"] = [0.01, 0.02, 0.03, 0.05]
        probs = self._problems(spec=bad)
        self.assertTrue(any("dca_domain.spacing_pct" in p for p in probs), probs)

    def test_a_dca_cell_without_the_registered_base_quote_is_refused(self):
        bad = copy.deepcopy(self.round_spec)
        bad["dca_domain"]["grid"][0]["base_quote"] = 500
        probs = self._problems(spec=bad)
        self.assertTrue(any("base_quote=1000" in p for p in probs), probs)

    # ---- the data split and the universe
    def test_a_moved_split_is_refused(self):
        bad = copy.deepcopy(self.round_spec)
        bad["data"]["oos_start"] = "2025-01-01"
        probs = self._problems(spec=bad)
        self.assertTrue(any("data.oos_start" in p for p in probs), probs)

    def test_a_shrunk_universe_is_refused(self):
        bad = copy.deepcopy(self.round_spec)
        bad["eligible_universe"]["symbols"] = bad["eligible_universe"]["symbols"][:3]
        bad["eligible_universe"]["cohort_count"] = 3
        probs = self._problems(spec=bad)
        self.assertTrue(any("symbols must be the four USD-M perpetual contracts" in p
                            for p in probs), probs)

    def test_a_second_base_grid_is_refused(self):
        bad = copy.deepcopy(self.round_spec)
        bad["eligible_universe"]["timeframes"] = [{"raw_interval": "1h", "qlib_freq": "60min"},
                                                  {"raw_interval": "4h", "qlib_freq": "240min"}]
        probs = self._problems(spec=bad)
        self.assertTrue(any("timeframes must register exactly" in p for p in probs), probs)

    # ---- the run-spec mirror
    def test_a_run_spec_without_the_deployed_runner_pin_is_refused(self):
        bad = copy.deepcopy(self.run_spec)
        bad["script"]["sha256"] = "{{script_sha256}}"
        probs = self._problems(run_spec=bad)
        self.assertTrue(any("sha256:<hex>" in p for p in probs), probs)

    def test_a_run_spec_that_relabels_a_dca_axis_is_refused(self):
        bad = copy.deepcopy(self.run_spec)
        bad["dca_domain"]["spacing_pct_status"] = "USER_FIXED"
        probs = self._problems(run_spec=bad)
        self.assertTrue(any("round-spec classification" in p for p in probs), probs)

    def test_a_run_spec_with_reordered_cases_is_refused(self):
        bad = copy.deepcopy(self.run_spec)
        bad["parameter_domain"]["grid_cases"] = list(
            reversed(bad["parameter_domain"]["grid_cases"]))
        probs = self._problems(run_spec=bad)
        self.assertTrue(any("registered case product" in p for p in probs), probs)

    def test_a_run_spec_with_a_tampered_gate_is_refused(self):
        bad = copy.deepcopy(self.run_spec)
        bad["gates"]["min_episodes_is"] = 10
        probs = self._problems(run_spec=bad)
        self.assertTrue(any("run-spec.gates.min_episodes_is" in p for p in probs), probs)

    # ---- the generic parameter contract
    def test_a_tampered_parameter_contract_cardinality_is_refused(self):
        bad = copy.deepcopy(self.round_spec)
        bad["parameter_contract"]["domain_cardinality"]["strategy"] = 12
        self.assertTrue(pc.validate_contract(bad["parameter_contract"]))
        self.assertTrue(pc.validate_round_spec_contract(bad))

    def test_a_parameter_contract_without_the_composite_is_refused(self):
        bad = copy.deepcopy(self.round_spec)
        bad["parameter_contract"]["research_axes_ordered"][0]["kind"] = "atomic"
        self.assertTrue(pc.validate_contract(bad["parameter_contract"]))

    def test_a_parameter_contract_family_mismatch_is_refused(self):
        bad = copy.deepcopy(self.round_spec)
        bad["parameter_contract"]["family_id"] = "some-other-family"
        probs = self._problems(spec=bad)
        self.assertTrue(any("parameter_contract.family_id" in p for p in probs), probs)

    # ---- the placeholder discipline
    def test_an_unregistered_token_is_flagged_and_leaks_through_instantiation(self):
        round_t, run_t = templates()
        # the frozen templates carry exactly the registered placeholders ...
        self.assertEqual(author.token_audit(round_t, author.ROUND_PLACEHOLDERS), ([], []))
        self.assertEqual(author.token_audit(run_t, author.RUN_PLACEHOLDERS), ([], []))
        # ... and a rogue token is flagged by the audit and survives instantiation uncleaned
        bad = copy.deepcopy(round_t)
        bad["notes"] = "rogue {{rogue_token}} left in the document"
        unexpected, missing = author.token_audit(bad, author.ROUND_PLACEHOLDERS)
        self.assertEqual(unexpected, ["{{rogue_token}}"])
        self.assertEqual(missing, [])
        out, _run = inst.instantiate(bad, run_t, "t_test", "fam-r1", "fam-r1-u1",
                                     "2026-01-01T00:00:00Z", TEST_SHAS[0], TEST_SHAS[1])
        self.assertTrue(inst.leftover_placeholders(out))


class TestInstantiationCLI(unittest.TestCase):
    """End-to-end invocation of the instantiator inside a scratch results root."""

    TASK_ID = "t_test_cli"

    def setUp(self):
        self.scratch = tempfile.mkdtemp(prefix="h-inst-cli-")
        self.family_dir = os.path.join(self.scratch, counts.FAMILY_ID)
        os.makedirs(self.family_dir)
        # the fixture family.json carries the SAME fingerprint_input as the frozen template
        # (which was authored from the immutable /results family.json) and a recomputed
        # semantic_fingerprint, so the ownership readback is exercised for real
        with open(ROUND_TEMPLATE_PATH) as fh:
            template = json.load(fh)
        fp_input = template["semantic_fingerprint"]["fingerprint_input"]
        with open(os.path.join(self.family_dir, "family.json"), "w") as fh:
            json.dump({"schema_version": 1, "family_id": counts.FAMILY_ID,
                       "kanban_task_id": self.TASK_ID,
                       "fingerprint_input": fp_input,
                       "semantic_fingerprint": counts.fingerprint(fp_input)}, fh)
        self.scripts = os.path.join(self.scratch, "deployed")
        os.makedirs(os.path.join(self.scripts, "tests"))
        for rel in ("80_strategy_h_run.py", os.path.join("tests", "test_strategy_h_engine.py")):
            with open(os.path.join(self.scripts, rel), "w") as fh:
                fh.write("# scratch stand-in for the deployed %s\n" % rel)

    def _run_cli(self, *extra):
        argv = ["instantiate_strategy_h_v1.py", "--results-root", self.scratch,
                "--task-id", self.TASK_ID, "--host-scripts", self.scripts,
                "--created-at-utc", "2026-01-01T00:00:00Z"] + list(extra)
        old = sys.argv
        buf = io.StringIO()
        sys.argv = argv
        try:
            with contextlib.redirect_stdout(buf):
                rc = inst.main()
        finally:
            sys.argv = old
        return rc, buf.getvalue()

    def test_cli_publishes_reuses_and_refuses_overwrites(self):
        round_path = os.path.join(self.family_dir, "rounds", counts.FAMILY_ID + "-h1",
                                  "round-spec.json")
        run_path = os.path.join(self.family_dir, "rounds", counts.FAMILY_ID + "-h1", "attempts",
                                counts.FAMILY_ID + "-h1-u1", "run-spec.json")
        rc, out = self._run_cli("--round-id", counts.FAMILY_ID + "-h1",
                                "--run-id", counts.FAMILY_ID + "-h1-u1")
        self.assertEqual(rc, 0, out)
        self.assertIn("instantiated", out)
        self.assertTrue(os.path.exists(round_path), "round-spec not published")
        self.assertTrue(os.path.exists(run_path), "run-spec not published")
        with open(round_path) as fh:
            round_spec = json.load(fh)
        with open(run_path) as fh:
            run_spec = json.load(fh)
        self.assertEqual(round_spec["kanban_task_id"], self.TASK_ID)
        self.assertEqual(run_spec["round_id"], counts.FAMILY_ID + "-h1")
        self.assertEqual(run_spec["task_id"], self.TASK_ID)
        self.assertEqual(run_spec["script"]["path"], "/scripts/80_strategy_h_run.py")
        self.assertEqual(inst.leftover_placeholders(round_spec), [])
        self.assertEqual(inst.leftover_placeholders(run_spec), [])
        self.assertEqual(pc.validate_round_spec_contract(round_spec), [])
        _c, probs, _e = counts.check(round_spec, run_spec)
        self.assertEqual(probs, [], "published pair must validate clean: %r" % (probs,))
        with open(os.path.join(self.scripts, "80_strategy_h_run.py"), "rb") as fh:
            self.assertEqual(run_spec["script"]["sha256"],
                             "sha256:" + hashlib.sha256(fh.read()).hexdigest())
        # the same attempt may never be overwritten (INV-4)
        rc, out = self._run_cli("--round-id", counts.FAMILY_ID + "-h1",
                                "--run-id", counts.FAMILY_ID + "-h1-u1")
        self.assertEqual(rc, 1)
        self.assertIn("refusing to overwrite existing", out)
        # a technical retry keeps the round and publishes only the new attempt
        rc, out = self._run_cli("--round-id", counts.FAMILY_ID + "-h1",
                                "--run-id", counts.FAMILY_ID + "-h1-u2")
        self.assertEqual(rc, 0, out)
        self.assertTrue(os.path.exists(os.path.join(self.family_dir, "rounds",
                                                    counts.FAMILY_ID + "-h1", "attempts",
                                                    counts.FAMILY_ID + "-h1-u2",
                                                    "run-spec.json")))

    def test_cli_dry_run_writes_nothing(self):
        rc, out = self._run_cli("--round-id", counts.FAMILY_ID + "-d1",
                                "--run-id", counts.FAMILY_ID + "-d1-u1", "--dry-run")
        self.assertEqual(rc, 0, out)
        self.assertIn("would_instantiate", out)
        self.assertFalse(os.path.exists(os.path.join(self.family_dir, "rounds",
                                                    counts.FAMILY_ID + "-d1")))

    def test_cli_refuses_a_missing_family_json(self):
        os.unlink(os.path.join(self.family_dir, "family.json"))
        rc, out = self._run_cli("--round-id", counts.FAMILY_ID + "-x1",
                                "--run-id", counts.FAMILY_ID + "-x1-u1")
        self.assertEqual(rc, 1)
        self.assertIn("cannot read", out)

    def test_cli_refuses_a_missing_deployed_runner(self):
        rc, out = self._run_cli("--round-id", counts.FAMILY_ID + "-z1",
                                "--run-id", counts.FAMILY_ID + "-z1-u1",
                                "--runner-host", os.path.join(self.scratch, "not_deployed.py"))
        self.assertEqual(rc, 1)
        self.assertIn("cannot read the deployed host file", out)
        self.assertFalse(os.path.exists(os.path.join(self.family_dir, "rounds",
                                                     counts.FAMILY_ID + "-z1")))

    def test_cli_refuses_a_family_json_owned_by_another_card(self):
        path = os.path.join(self.family_dir, "family.json")
        with open(path) as fh:
            family = json.load(fh)
        family["kanban_task_id"] = "t_someone_else"
        with open(path, "w") as fh:
            json.dump(family, fh)
        rc, out = self._run_cli("--round-id", counts.FAMILY_ID + "-y1",
                                "--run-id", counts.FAMILY_ID + "-y1-u1")
        self.assertEqual(rc, 1)
        self.assertIn("kanban_task_id", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
