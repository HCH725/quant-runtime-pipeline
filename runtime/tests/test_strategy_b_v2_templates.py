#!/usr/bin/env python3
"""Structural + v1.4.0-semantics check for the Strategy B v2 preregistration templates.

B v2 is a PREREGISTRATION that has never been launched (card t_24cc6167): there is no B v2
family directory, no card, no engine and no run.  What must be true at this point is that the
registered documents are complete, internally consistent and written under v1.4.0 semantics:

  * 20 cohorts (4 symbols x 5 timeframes) with a complete strategy parameter domain
    (ema_pair x walk_forward = 120 cases per cohort),
  * a complete DCA parameter domain (4 axes, 48 configs) with the v1.3.1 provenance classes,
  * historical / OOS / robustness coverage declared as 10 phase grids with the exact expected
    arithmetic (20 x 120 x 48 x 10 = 1,152,000 case evaluations),
  * cohort survivor semantics (selector + five survivor requirements + the disposition bands)
    under the v1.4.0 mapping: 0 survivors -> REJECT, >=1 -> PASS, and the frozen survivor
    bundle requirement of contract 10.8,
  * no launch: both documents say so, and neither declares a delivered script sha256.

The checker below can contradict the documents (missing cohort, weakened domain, wrong count,
injected legacy FINALIST mapping, moved split, a 48-config grid that is not the cartesian
product), which is what the negative-control tests exercise.

stdlib unittest only; no market data, no container, no Qlib.
"""
import copy
import hashlib
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "runtime"))
import parameter_contract as pc  # noqa: E402
ROUND_SPEC = os.path.join(REPO, "runtime", "templates", "strategy_b_v2_round_spec.template.json")
RUN_SPEC = os.path.join(REPO, "runtime", "templates", "strategy_b_v2_run_spec.template.json")

DCA_AXES = ("spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct")
PHASE_GRIDS = ["historical", "oos", "full", "fee_2x", "funding_2x", "entry_delay_1_bar",
               "slippage_2ticks", "no_funding", "no_funding_full", "cost_attrition_40bps"]
PROJECT_CONSTANT = "PROJECT_PRE_REGISTERED_CONSTANT"
PROJECT_SEARCH = "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN"
USER_FIXED_FORBIDDEN_KEYS = ("base_quote", "base_quote_usdt", "size_multiplier", "spacing_pct",
                             "breakeven_tp_pct", "invalidation_pct")


def load(path):
    with open(path) as fh:
        return json.load(fh)


def product(axes):
    out = [()]
    for values in axes:
        out = [t + (v,) for t in out for v in values]
    return out


def recompute(spec):
    """Every registered count, recomputed from the declared axes."""
    universe = spec["eligible_universe"]
    cohorts = len(universe["symbols"]) * len(universe["timeframes"])
    params = spec["parameter_domain"]
    pairs = product([params["ema_fast"], params["ema_slow"]])
    legal_pairs = [p for p in pairs if p[0] < p[1]]
    declared_pairs = [(p["fast"], p["slow"]) for p in params["ema_pair"]]
    wf = [(c["train_days"], c["test_days"]) for c in params["walk_forward"]]
    strategy_cases = len(legal_pairs) * len(wf)
    dca = spec["dca_domain"]
    dca_cells = product([dca[a] for a in DCA_AXES])
    base = strategy_cases * len(dca_cells)
    per_grid = base * cohorts
    return {
        "cohorts": cohorts,
        "ema_pairs": legal_pairs,
        "declared_pairs": declared_pairs,
        "walk_forward": wf,
        "strategy_cases": strategy_cases,
        "dca_configs": len(dca_cells),
        "base_combinations_per_cohort": base,
        "case_evaluations_per_grid": per_grid,
        "phase_grid_count": len(PHASE_GRIDS),
        "expected_case_evaluations": per_grid * len(PHASE_GRIDS),
    }


def check(spec, run_spec=None):
    """Return (computed, problems).  Problems are what makes the preregistration invalid."""
    problems = []
    c = recompute(spec)

    if c["cohorts"] != 20:
        problems.append("eligible universe does not give 20 cohorts: %d" % c["cohorts"])
    if spec["eligible_universe"].get("cohort_count") != c["cohorts"]:
        problems.append("eligible_universe.cohort_count != the declared symbols x timeframes")
    if len(c["declared_pairs"]) != len(c["ema_pairs"]) or sorted(c["declared_pairs"]) != sorted(c["ema_pairs"]):
        problems.append("ema_pair is not the legal product of ema_fast x ema_slow (declared %d, "
                        "legal product %d)" % (len(c["declared_pairs"]), len(c["ema_pairs"])))
    if any(f >= s for f, s in c["declared_pairs"]):
        problems.append("ema_pair contains a cell with fast >= slow")
    if [p for p in c["walk_forward"] if p[0] != p[1]]:
        problems.append("walk_forward contains a non-diagonal cell the preregistration did not declare")
    if spec["parameter_domain"].get("legal_cases_per_cohort") != c["strategy_cases"]:
        problems.append("parameter_domain.legal_cases_per_cohort != ema_pair x walk_forward")

    dca = spec["dca_domain"]
    if len(dca.get("grid") or []) != c["dca_configs"] or dca.get("config_count") != c["dca_configs"]:
        problems.append("dca_domain.grid is not the four-axis cartesian product (%d)"
                        % c["dca_configs"])
    if not str(dca.get("base_quote_status") or "").startswith(PROJECT_CONSTANT):
        problems.append("dca_domain.base_quote_status must be %s" % PROJECT_CONSTANT)
    if len(dca.get("size_multiplier") or []) > 1 and \
            not str(dca.get("size_multiplier_status") or "").startswith(PROJECT_SEARCH):
        problems.append("dca_domain.size_multiplier_status must be %s (the axis is searched)"
                        % PROJECT_SEARCH)
    invariants = spec.get("authorization_invariants") or {}
    for key in USER_FIXED_FORBIDDEN_KEYS:
        if key in invariants:
            problems.append("authorization_invariants.%s contradicts the registered search domain"
                            % key)

    expected = spec["expected"]
    for key in ("cohorts", "strategy_cases_per_cohort", "dca_configs_per_cohort",
                "base_combinations_per_cohort", "case_evaluations_per_grid",
                "expected_case_evaluations"):
        want = {"cohorts": c["cohorts"], "strategy_cases_per_cohort": c["strategy_cases"],
                "dca_configs_per_cohort": c["dca_configs"],
                "base_combinations_per_cohort": c["base_combinations_per_cohort"],
                "case_evaluations_per_grid": c["case_evaluations_per_grid"],
                "expected_case_evaluations": c["expected_case_evaluations"]}[key]
        if expected.get(key) != want:
            problems.append("expected.%s = %r but the declared axes compute %r"
                            % (key, expected.get(key), want))
    if expected.get("cohort_grid_kinds") != PHASE_GRIDS:
        problems.append("expected.cohort_grid_kinds != the 10 registered phase grids")
    if expected.get("phase_grid_count") != len(PHASE_GRIDS):
        problems.append("expected.phase_grid_count != %d" % len(PHASE_GRIDS))
    for needed in ("historical", "oos", "full", "fee_2x", "funding_2x", "entry_delay_1_bar",
                   "slippage_2ticks"):
        if needed not in PHASE_GRIDS:
            problems.append("phase grids do not cover %s" % needed)

    data = spec["data"]
    if not (data.get("historical_start") == data.get("data_start")):
        problems.append("historical window does not start at data_start")
    if data.get("historical_end") != "2025-09-30" or data.get("oos_start") != "2025-10-01":
        problems.append("historical/OOS split is not the frozen 2025-09-30 | 2025-10-01 boundary")
    if data.get("oos_end") != data.get("data_end"):
        problems.append("OOS window does not end at data_end")
    if not (str(data.get("historical_end")) < str(data.get("oos_start"))):
        problems.append("historical window overlaps the OOS window")
    if "not a sealed holdout" not in str(data.get("split_disclosure", "")).lower():
        problems.append("the reused split is not disclosed as a non-sealed holdout")

    sel = spec["selector_and_disposition"]
    if sel.get("selector_version") != "cohort-selector-v1" or \
            sel.get("disposition_version") != "cohort-disposition-v1":
        problems.append("selector/disposition version strings are not the registered ones")
    if len(sel.get("cohort_survivor_requirements") or []) != 5:
        problems.append("the five cohort survivor requirements are not all registered")
    bands = sel.get("family_disposition") or {}
    if len(bands) != 4:
        problems.append("family_disposition must register the four bands (0 / 1 / >1 / technical)")
    if "REJECT" not in str(bands.get("0 cohort survivors")):
        problems.append("the 0-survivor band must be REJECT")
    for key in ("1 cohort survivor", ">1 cohort survivors"):
        if "PASS" not in str(bands.get(key)):
            problems.append("v1.4.0: the %r band must be verdict PASS" % key)
    if any("FINALIST" in str(v) for v in bands.values()):
        problems.append("v1.4.0: the legacy FINALIST mapping must not be registered as the "
                        "multi-survivor band")
    if "TECHNICAL_INCOMPLETE" not in " ".join(str(v) for v in bands.values()):
        problems.append("the TECHNICAL_INCOMPLETE band is not registered")
    if ">=1 -> PASS" not in str(spec["gates"].get("verdict_rule", "")):
        problems.append("gates.verdict_rule does not register the v1.4.0 mapping")
    claim = str(spec.get("performance_claimable_rule", ""))
    if "at least one cohort survivor" not in claim or "never" not in claim:
        problems.append("performance_claimable_rule must state the >=1-survivor rule and that the "
                        "survivor count never forces claimable false")
    for key, section in (("survivor_bundle", spec), ):
        bundle = section.get(key) or {}
        if "survivor-bundle.json" not in str(bundle.get("artifact", "")):
            problems.append("the frozen survivor bundle artifact is not registered")
        if "not a ranking" not in str(bundle.get("rule", "")):
            problems.append("the bundle rule must state that the survivor order is not a ranking")

    fp = spec["semantic_fingerprint"]
    declared = str(fp.get("semantic_fingerprint", ""))
    computed = "sha256:" + hashlib.sha256(str(fp.get("fingerprint_input", "")).encode()).hexdigest()
    if declared.startswith("TO_BE_RECOMPUTED"):
        pass  # correct for an unregistered preregistration
    elif declared != computed:
        problems.append("declared semantic_fingerprint != sha256(fingerprint_input)")
    for needed in ("selector=cohort-selector-v1", "disposition=cohort-disposition-v1",
                   "spacing_pct=", "size_multiplier=", "breakeven_tp_pct=", "invalidation_pct=",
                   "symbols=", "long/short", "ema_pair=", "walk_forward="):
        if needed not in str(fp.get("fingerprint_input", "")):
            problems.append("fingerprint_input does not contain %r" % needed)

    if "NOT LAUNCHED" not in str(spec["template_instantiation"].get("status", "")):
        problems.append("the round-spec template does not declare that it has not been launched")
    if "NOT IMPLEMENTED" not in str(spec.get("implementation_status", {}).get("engine", "")):
        problems.append("the round-spec template does not disclose that the B v2 engine does not "
                        "exist yet")

    run_spec_check = None
    if run_spec is not None:
        rs_params, rs_dca = run_spec["params"], run_spec["dca_domain"]
        domains_agree = ([(p["fast"], p["slow"]) for p in rs_params["ema_pair"]] == c["declared_pairs"]
                        and [(w["train_days"], w["test_days"]) for w in rs_params["walk_forward"]]
                        == c["walk_forward"]
                        and rs_params.get("grid_size") == c["strategy_cases"]
                        and [tuple(g[a] for a in DCA_AXES) for g in rs_dca["grid"]]
                        == [tuple(g[a] for a in DCA_AXES) for g in dca["grid"]]
                        and all(rs_dca.get(a) == dca.get(a) for a in DCA_AXES)
                        and rs_dca.get("base_quote") == dca.get("base_quote"))
        counts_agree = all(run_spec["expected"].get(k) == expected[k] for k in
                           ("cohorts", "strategy_cases_per_cohort", "dca_configs_per_cohort",
                            "base_combinations_per_cohort", "case_evaluations_per_grid",
                            "expected_case_evaluations"))
        versions_agree = (run_spec.get("selector_version") == sel["selector_version"]
                          and run_spec.get("disposition_version") == sel["disposition_version"])
        split_map = {"start": "data_start", "end": "data_end", "historical_start": "historical_start",
                     "historical_end": "historical_end", "oos_start": "oos_start", "oos_end": "oos_end"}
        split_agree = all(run_spec["data"].get(rs_key) == data.get(spec_key)
                          for rs_key, spec_key in split_map.items())
        script_placeholder = "{{" in str(run_spec["script"].get("sha256", ""))
        run_spec_check = {"domains_agree": domains_agree, "counts_agree": counts_agree,
                          "versions_agree": versions_agree, "split_agrees": split_agree,
                          "script_sha256_is_placeholder": script_placeholder,
                          "expected_outputs": len(run_spec["expected_outputs"])}
        for name, ok in (("domains", domains_agree), ("counts", counts_agree),
                         ("versions", versions_agree), ("split", split_agree)):
            if not ok:
                problems.append("run-spec %s disagree with the round-spec" % name)
        if "NOT LAUNCHED" not in str(run_spec["template_instantiation"].get("status", "")):
            problems.append("the run-spec template does not declare that it has not been launched")
        if "FINALIST" in json.dumps(run_spec):
            problems.append("v1.4.0: the run-spec template still carries the legacy FINALIST mapping")
        if not any("grid_cost_attrition_40bps.csv" in out for out in run_spec["expected_outputs"]):
            problems.append("the cost-attrition phase grid has no declared output")
        if run_spec.get("round_level_outputs", {}).get("written_by") != "default (host side), never by the container":
            problems.append("round-level outputs (verdict + survivor bundle) must be written host-side")

    return c, run_spec_check, problems


class TestBv2Preregistration(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.round_spec = load(ROUND_SPEC)
        cls.run_spec = load(RUN_SPEC)

    def test_preregistration_is_complete_and_internally_consistent(self):
        computed, rs, problems = check(self.round_spec, self.run_spec)
        self.assertEqual(problems, [])
        self.assertEqual(computed["cohorts"], 20)
        self.assertEqual(computed["strategy_cases"], 120)
        self.assertEqual(computed["dca_configs"], 48)
        self.assertEqual(computed["base_combinations_per_cohort"], 5760)
        self.assertEqual(computed["case_evaluations_per_grid"], 115200)
        self.assertEqual(computed["expected_case_evaluations"], 1152000)
        self.assertEqual(computed["expected_case_evaluations"], 20 * 120 * 48 * 10)
        self.assertTrue(all(rs[k] for k in ("domains_agree", "counts_agree", "versions_agree",
                                            "split_agrees", "script_sha256_is_placeholder")))

    def test_strategy_domain_is_the_declared_product(self):
        params = self.round_spec["parameter_domain"]
        self.assertEqual([tuple(p) for p in product([params["ema_fast"], params["ema_slow"]])
                          if p[0] < p[1]], sorted((p["fast"], p["slow"]) for p in params["ema_pair"]))
        self.assertEqual(params["ema_pair_count"], 30)
        self.assertEqual(params["walk_forward_cells"], 4)
        self.assertIn("BEFORE any computation", params["domain_shrinkage_disclosure"])

    def test_dca_provenance_classes_match_the_declared_search_domain(self):
        dca = self.round_spec["dca_domain"]
        self.assertTrue(dca["base_quote_status"].startswith(PROJECT_CONSTANT))
        self.assertTrue(dca["size_multiplier_status"].startswith(PROJECT_SEARCH))
        for key in USER_FIXED_FORBIDDEN_KEYS:
            self.assertNotIn(key, self.round_spec["authorization_invariants"])

    def test_narrowed_or_inconsistent_domain_is_rejected(self):
        spec = copy.deepcopy(self.round_spec)
        spec["dca_domain"]["grid"] = spec["dca_domain"]["grid"][:40]
        spec["dca_domain"]["config_count"] = 40
        _, _, problems = check(spec, None)
        self.assertTrue(any("cartesian product" in p for p in problems), problems)

        spec = copy.deepcopy(self.round_spec)
        spec["eligible_universe"]["timeframes"] = spec["eligible_universe"]["timeframes"][:4]
        _, _, problems = check(spec, None)
        self.assertTrue(any("20 cohorts" in p for p in problems), problems)

        spec = copy.deepcopy(self.round_spec)
        spec["expected"]["expected_case_evaluations"] = 115200
        _, _, problems = check(spec, None)
        self.assertTrue(any("expected_case_evaluations" in p for p in problems), problems)

    def test_legacy_multi_survivor_mapping_is_rejected(self):
        spec = copy.deepcopy(self.round_spec)
        spec["selector_and_disposition"]["family_disposition"][">1 cohort survivors"] = \
            "MULTIPLE_SURVIVORS (verdict FINALIST; performance_claimable false)"
        _, _, problems = check(spec, None)
        self.assertTrue(any("FINALIST" in p for p in problems), problems)
        self.assertTrue(any("must be verdict PASS" in p for p in problems), problems)

    def test_moved_split_and_sealed_holdout_claim_are_rejected(self):
        spec = copy.deepcopy(self.round_spec)
        spec["data"]["oos_start"] = "2026-01-01"
        _, _, problems = check(spec, None)
        self.assertTrue(any("split" in p for p in problems), problems)

        spec = copy.deepcopy(self.round_spec)
        spec["data"].pop("split_disclosure")
        _, _, problems = check(spec, None)
        self.assertTrue(any("non-sealed holdout" in p for p in problems), problems)

    def test_launch_claims_are_rejected(self):
        spec = copy.deepcopy(self.round_spec)
        spec["template_instantiation"]["status"] = "LAUNCHED"
        _, _, problems = check(spec, None)
        self.assertTrue(any("not been launched" in p for p in problems), problems)

        run_spec = copy.deepcopy(self.run_spec)
        run_spec["script"]["sha256"] = "0" * 64
        _, rs, problems = check(self.round_spec, run_spec)
        self.assertFalse(rs["script_sha256_is_placeholder"])

        run_spec = copy.deepcopy(self.run_spec)
        run_spec["expected"]["expected_case_evaluations"] = 103680
        _, _, problems = check(self.round_spec, run_spec)
        self.assertTrue(any("run-spec counts disagree" in p for p in problems), problems)

    def test_fingerprint_is_recomputable_and_deliberately_unregistered(self):
        fp = self.round_spec["semantic_fingerprint"]
        self.assertTrue(fp["semantic_fingerprint"].startswith("TO_BE_RECOMPUTED"))
        computed = "sha256:" + hashlib.sha256(fp["fingerprint_input"].encode()).hexdigest()
        self.assertEqual(len(computed), len("sha256:") + 64)
        spec = copy.deepcopy(self.round_spec)
        spec["semantic_fingerprint"]["semantic_fingerprint"] = "sha256:" + "1" * 64
        _, _, problems = check(spec, None)
        self.assertTrue(any("semantic_fingerprint" in p for p in problems), problems)

    # --- v1.8 / contract 26.1: the forward template must carry the generic parameter contract ---
    def test_round_spec_template_carries_the_generic_parameter_contract(self):
        contract = self.round_spec.get("parameter_contract")
        self.assertIsInstance(contract, dict)
        self.assertEqual(pc.validate_contract(contract), [])
        self.assertEqual(pc.validate_round_spec_contract(self.round_spec), [])
        self.assertEqual(contract["family_id"], self.round_spec["family_id"])
        self.assertEqual(contract["domain_cardinality"],
                         {"strategy": 120, "dca": 48, "per_cohort": 5760})
        self.assertEqual(contract["composite_map"],
                         {"ema_pair": ["ema_fast", "ema_slow"],
                          "walk_forward": ["wf_train_days", "wf_test_days"]})
        self.assertEqual(contract["strategy_param_fields"],
                         ["ema_fast", "ema_slow", "wf_train_days", "wf_test_days"])
        self.assertEqual(contract["dca_param_fields"],
                         ["spacing_pct", "size_multiplier", "breakeven_tp_pct",
                          "invalidation_pct"])
        # the composite axes must carry the registered values of the declared domains, in order:
        # a hand-edit that drifts from the registered axes fails here
        params = self.round_spec["parameter_domain"]
        self.assertEqual(contract["research_axes_ordered"][0]["registered_values"],
                         [[p["fast"], p["slow"]] for p in params["ema_pair"]])
        self.assertEqual(contract["research_axes_ordered"][1]["registered_values"],
                         [[c["train_days"], c["test_days"]] for c in params["walk_forward"]])
        self.assertEqual(contract["research_axes_ordered"][2:],
                         [{"name": axis, "kind": "atomic", "members": [axis],
                           "registered_values": list(self.round_spec["dca_domain"][axis]),
                           "row_fields": [axis]}
                          for axis in ("spacing_pct", "size_multiplier", "breakeven_tp_pct",
                                       "invalidation_pct")])

    def test_round_spec_template_without_the_contract_fails_closed(self):
        """Negative control: the pre-v1.8 template shape (no parameter_contract) is refused."""
        spec = copy.deepcopy(self.round_spec)
        spec.pop("parameter_contract")
        problems = pc.validate_round_spec_contract(spec)
        self.assertTrue(any("fail closed" in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main(verbosity=2)
