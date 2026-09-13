#!/usr/bin/env python3
"""Strategy B v2 (Contract v1.4.0+ semantics) pre-registration counter / validator.

Pure stdlib, read-only, no service.  The B v2 preregistration ships no case-count validator
of its own (template `validated_by`: "B has no pre-registered case-count validator of its
own, deliberately: its counts are validated by this test alone until the B v2 launch card
registers them").  This is that registration: the launch card runs this tool against the
instantiated round-spec / run-spec before launch, and an auditor can re-run it afterwards.

It recomputes every count and every registered request from the declared axes and compares
them against the canonical B v2 numbers AND against the values the documents declare, so it
can contradict the documents instead of merely echoing them.  `--self-test` ships the
negative controls (each mutation must be flagged).

usage:
  python3 runtime/strategy_b_v2_counts.py [--spec <round-spec.json>]
                                          [--run-spec <run-spec.json>]
                                          [--out <evidence.json>] [--json] [--self-test]
exit: 0 = ok, 1 = problems, 2 = usage error
"""
import argparse
import copy
import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
DEFAULT_SPEC = os.path.join(HERE, "templates", "strategy_b_v2_round_spec.template.json")
DEFAULT_RUN_SPEC = os.path.join(HERE, "templates", "strategy_b_v2_run_spec.template.json")

# Canonical B v2 arithmetic (round-spec `expected` / contract 7.2 / 7.3).  Hard-coded on
# purpose: the tool must be able to contradict the document, not merely echo it.
CANONICAL = {
    "cohorts": 20,
    "ema_pairs": 30,
    "walk_forward_cells": 4,
    "strategy_cases_per_cohort": 120,
    "dca_configs_per_cohort": 48,
    "base_combinations_per_cohort": 5760,
    "phase_grid_count": 10,
    "case_evaluations_per_grid": 115200,
    "case_evaluations_per_cohort_all_grids": 57600,
    "expected_case_evaluations": 1152000,
}
PHASE_GRIDS = ["historical", "oos", "full", "fee_2x", "funding_2x", "entry_delay_1_bar",
               "slippage_2ticks", "no_funding", "no_funding_full", "cost_attrition_40bps"]
DCA_AXES = ("spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct")
PROJECT_CONSTANT = "PROJECT_PRE_REGISTERED_CONSTANT"
PROJECT_SEARCH = "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN"
USER_FIXED_FORBIDDEN_KEYS = ("base_quote", "base_quote_usdt", "size_multiplier", "spacing_pct",
                             "breakeven_tp_pct", "invalidation_pct")
# The registered USER_FIXED invariants (only items with explicit operator evidence).
USER_FIXED_EXPECTED = {"starting_equity_usdt": 30000, "numeraire": "USDT (sole)",
                       "tranches": 12, "routine_active_levels": 11, "exit": "reduce-only",
                       "no_add_after_flat_or_kill": True}
SELECTOR_VERSION = "cohort-selector-v1"
DISPOSITION_VERSION = "cohort-disposition-v1"
SPLIT = {"data_start": "2022-01-01", "data_end": "2026-09-10",
         "historical_start": "2022-01-01", "historical_end": "2025-09-30",
         "oos_start": "2025-10-01", "oos_end": "2026-09-10"}


def portable(path):
    """Evidence files must not carry host-specific absolute paths (evidence/README rule 2)."""
    if path and path.startswith(REPO_ROOT + os.sep):
        return "<REPO>/" + os.path.relpath(path, REPO_ROOT)
    return path


def load(path):
    with open(path) as fh:
        return json.load(fh)


def product(axes):
    out = [()]
    for values in axes:
        out = [t + (v,) for t in out for v in values]
    return out


def fingerprint(fingerprint_input):
    return "sha256:" + hashlib.sha256(str(fingerprint_input).encode()).hexdigest()


def recompute(spec):
    """Every registered count, recomputed from the declared axes."""
    universe = spec["eligible_universe"]
    cohorts = len(universe["symbols"]) * len(universe["timeframes"])
    params = spec["parameter_domain"]
    pairs = [p for p in product([params["ema_fast"], params["ema_slow"]]) if p[0] < p[1]]
    declared_pairs = [(p["fast"], p["slow"]) for p in params["ema_pair"]]
    wf = [(c["train_days"], c["test_days"]) for c in params["walk_forward"]]
    strategy_cases = len(pairs) * len(wf)
    dca = spec["dca_domain"]
    dca_cells = product([dca[a] for a in DCA_AXES])
    base = strategy_cases * len(dca_cells)
    per_grid = base * cohorts
    return {"cohorts": cohorts, "ema_pairs": pairs, "declared_pairs": declared_pairs,
            "walk_forward": wf, "strategy_cases": strategy_cases,
            "dca_configs": len(dca_cells), "base_combinations_per_cohort": base,
            "case_evaluations_per_grid": per_grid,
            "case_evaluations_per_cohort_all_grids": base * len(PHASE_GRIDS),
            "phase_grid_count": len(PHASE_GRIDS),
            "expected_case_evaluations": per_grid * len(PHASE_GRIDS)}


def check(spec, run_spec=None):
    """Return (computed, problems, extra).  Problems are what makes the registration invalid."""
    problems = []
    c = recompute(spec)

    for key, want in (("cohorts", CANONICAL["cohorts"]),
                      ("strategy_cases", CANONICAL["strategy_cases_per_cohort"]),
                      ("dca_configs", CANONICAL["dca_configs_per_cohort"]),
                      ("base_combinations_per_cohort", CANONICAL["base_combinations_per_cohort"]),
                      ("case_evaluations_per_grid", CANONICAL["case_evaluations_per_grid"]),
                      ("case_evaluations_per_cohort_all_grids",
                       CANONICAL["case_evaluations_per_cohort_all_grids"]),
                      ("phase_grid_count", CANONICAL["phase_grid_count"]),
                      ("expected_case_evaluations", CANONICAL["expected_case_evaluations"])):
        if c[key] != want:
            problems.append("recomputed %s=%r != canonical %r" % (key, c[key], want))
    if len(c["ema_pairs"]) != CANONICAL["ema_pairs"]:
        problems.append("legal EMA pair product is %d, canonical is %d"
                        % (len(c["ema_pairs"]), CANONICAL["ema_pairs"]))
    if len(c["walk_forward"]) != CANONICAL["walk_forward_cells"]:
        problems.append("walk-forward cells = %d, canonical is %d"
                        % (len(c["walk_forward"]), CANONICAL["walk_forward_cells"]))
    if spec["eligible_universe"].get("cohort_count") != c["cohorts"]:
        problems.append("eligible_universe.cohort_count != the declared symbols x timeframes")
    if sorted(c["declared_pairs"]) != sorted(c["ema_pairs"]):
        problems.append("ema_pair is not the legal product of ema_fast x ema_slow "
                        "(declared %d, legal %d)" % (len(c["declared_pairs"]), len(c["ema_pairs"])))
    if any(f >= s for f, s in c["declared_pairs"]):
        problems.append("ema_pair contains a cell with fast >= slow")
    if [w for w in c["walk_forward"] if w[0] != w[1]]:
        problems.append("walk_forward contains a non-diagonal cell the preregistration "
                        "did not declare")
    if spec["parameter_domain"].get("legal_cases_per_cohort") != c["strategy_cases"]:
        problems.append("parameter_domain.legal_cases_per_cohort != ema_pair x walk_forward")

    dca = spec["dca_domain"]
    if len(dca.get("grid") or []) != c["dca_configs"] or dca.get("config_count") != c["dca_configs"]:
        problems.append("dca_domain.grid is not the four-axis cartesian product (%d)"
                        % c["dca_configs"])
    if len(dca.get("grid") or []) == c["dca_configs"]:
        declared = [tuple(g[a] for a in DCA_AXES) for g in dca["grid"]]
        legal = product([dca[a] for a in DCA_AXES])
        if sorted(declared) != sorted(legal):
            problems.append("dca_domain.grid values are not the four-axis cartesian product")
    # provenance classes (contract 7.2 v1.3.1)
    if not str(dca.get("base_quote_status") or "").startswith(PROJECT_CONSTANT):
        problems.append("dca_domain.base_quote_status must start with %s" % PROJECT_CONSTANT)
    if len(dca.get("size_multiplier") or []) > 1 and \
            not str(dca.get("size_multiplier_status") or "").startswith(PROJECT_SEARCH):
        problems.append("dca_domain.size_multiplier_status must start with %s (axis is searched)"
                        % PROJECT_SEARCH)
    invariants = spec.get("authorization_invariants") or {}
    for key in USER_FIXED_FORBIDDEN_KEYS:
        if key in invariants:
            problems.append("authorization_invariants.%s contradicts the registered search "
                            "domain" % key)
    for key, want in USER_FIXED_EXPECTED.items():
        if invariants.get(key) != want:
            problems.append("authorization_invariants.%s = %r, registered value is %r"
                            % (key, invariants.get(key), want))

    expected = spec["expected"]
    computed_key = {"strategy_cases_per_cohort": "strategy_cases",
                    "dca_configs_per_cohort": "dca_configs"}
    for key in ("cohorts", "strategy_cases_per_cohort", "dca_configs_per_cohort",
                "base_combinations_per_cohort", "case_evaluations_per_grid",
                "expected_case_evaluations", "phase_grid_count"):
        want = c[computed_key.get(key, key)]
        if expected.get(key) != want:
            problems.append("expected.%s = %r but the declared axes compute %r"
                            % (key, expected.get(key), want))
    if expected.get("cohort_grid_kinds") != PHASE_GRIDS:
        problems.append("expected.cohort_grid_kinds != the 10 registered phase grids")
    if expected.get("case_evaluations_per_cohort_all_grids") != \
            c["case_evaluations_per_cohort_all_grids"]:
        problems.append("expected.case_evaluations_per_cohort_all_grids = %r but the declared "
                        "axes compute %r" % (expected.get("case_evaluations_per_cohort_all_grids"),
                                             c["case_evaluations_per_cohort_all_grids"]))

    data = spec["data"]
    for key, want in SPLIT.items():
        if data.get(key) != want:
            problems.append("data.%s = %r, the frozen split is %r" % (key, data.get(key), want))
    if not (str(data.get("historical_end")) < str(data.get("oos_start"))):
        problems.append("historical window overlaps the OOS window")
    if "not a sealed holdout" not in str(data.get("split_disclosure", "")).lower():
        problems.append("the reused split is not disclosed as a non-sealed holdout")

    sel = spec["selector_and_disposition"]
    if sel.get("selector_version") != SELECTOR_VERSION or \
            sel.get("disposition_version") != DISPOSITION_VERSION:
        problems.append("selector/disposition version strings are not the registered ones")
    if len(sel.get("cohort_survivor_requirements") or []) != 5:
        problems.append("the five cohort survivor requirements are not all registered")
    if sorted(sel.get("cull_reasons") or []) != sorted(
            ["insufficient_trades", "no_qualifying_candidate", "oos_economic", "full_economic",
             "robustness_economic:<grids>", "parameter_neighbourhood"]):
        problems.append("the registered cull_reasons set is not the contract 7.3 set")
    bands = sel.get("family_disposition") or {}
    if len(bands) != 4:
        problems.append("family_disposition must register the four bands (0 / 1 / >1 / technical)")
    if "REJECT" not in str(bands.get("0 cohort survivors")):
        problems.append("the 0-survivor band must be REJECT")
    for key in ("1 cohort survivor", ">1 cohort survivors"):
        if "PASS" not in str(bands.get(key)):
            problems.append("v1.4.0: the %r band must be verdict PASS" % key)
    if any("FINALIST" in str(v) for v in bands.values()):
        problems.append("v1.4.0: the legacy FINALIST mapping must not be registered")
    if "TECHNICAL_INCOMPLETE" not in " ".join(str(v) for v in bands.values()):
        problems.append("the TECHNICAL_INCOMPLETE band is not registered")
    if ">=1 -> PASS" not in str(spec["gates"].get("verdict_rule", "")):
        problems.append("gates.verdict_rule does not register the v1.4.0 mapping")
    if spec["gates"].get("min_episodes_is") != 20:
        problems.append("gates.min_episodes_is != 20")
    if abs(float(spec["gates"].get("neighborhood_min_same_sign_fraction", 0)) - 0.6) > 1e-12:
        problems.append("gates.neighborhood_min_same_sign_fraction != 0.6")
    claim = str(spec.get("performance_claimable_rule", ""))
    if "at least one cohort survivor" not in claim or "never" not in claim:
        problems.append("performance_claimable_rule must state the >=1-survivor rule and that "
                        "the survivor count never forces claimable false")
    bundle = spec.get("survivor_bundle") or {}
    if "survivor-bundle.json" not in str(bundle.get("artifact", "")):
        problems.append("the frozen survivor bundle artifact is not registered")
    if "not a ranking" not in str(bundle.get("rule", "")):
        problems.append("the bundle rule must state that the survivor order is not a ranking")

    fp = spec["semantic_fingerprint"]
    status = str(spec["template_instantiation"].get("status", ""))
    # a template's status STARTS with PREREGISTRATION ONLY; an instantiated launch document
    # starts with INSTANTIATED (it may still mention the frozen template's status later on)
    is_template = status.startswith("PREREGISTRATION ONLY")
    declared_fp = str(fp.get("semantic_fingerprint", ""))
    computed_fp = fingerprint(fp.get("fingerprint_input", ""))
    if declared_fp.startswith("TO_BE_RECOMPUTED"):
        if not is_template:
            problems.append("a spec whose fingerprint is TO_BE_RECOMPUTED is not an "
                            "instantiated launch document")
    elif declared_fp != computed_fp:
        problems.append("declared semantic_fingerprint != sha256(fingerprint_input)")
    for needed in ("selector=cohort-selector-v1", "disposition=cohort-disposition-v1",
                   "spacing_pct=", "size_multiplier=", "breakeven_tp_pct=", "invalidation_pct=",
                   "symbols=", "long/short", "ema_pair=", "walk_forward=",
                   "ema-crossover-walkforward-momentum-long-short-v2"):
        if needed not in str(fp.get("fingerprint_input", "")):
            problems.append("fingerprint_input does not contain %r" % needed)

    extra = {"is_template": is_template, "fingerprint_declared": declared_fp,
             "fingerprint_computed": computed_fp}
    if is_template:
        if "NOT IMPLEMENTED" not in str(spec.get("implementation_status", {}).get("engine", "")):
            problems.append("the round-spec template does not disclose that the B v2 engine "
                            "does not exist yet")
    else:
        engine_status = str(spec.get("implementation_status", {}).get("engine", ""))
        if "IMPLEMENTED" not in engine_status:
            problems.append("an instantiated round-spec must disclose the deployed engine")

    if run_spec is not None:
        rs_params, rs_dca = run_spec["params"], run_spec["dca_domain"]
        domains_agree = (
            [(p["fast"], p["slow"]) for p in rs_params["ema_pair"]] == c["declared_pairs"]
            and [(w["train_days"], w["test_days"]) for w in rs_params["walk_forward"]] == c["walk_forward"]
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
        split_map = {"start": "data_start", "end": "data_end",
                     "historical_start": "historical_start", "historical_end": "historical_end",
                     "oos_start": "oos_start", "oos_end": "oos_end"}
        split_agree = all(run_spec["data"].get(rs_key) == data.get(spec_key)
                          for rs_key, spec_key in split_map.items())
        provenance_agree = (
            str(rs_dca.get("base_quote_status", "")).startswith(PROJECT_CONSTANT)
            and str(rs_dca.get("size_multiplier_status", "")).startswith(PROJECT_SEARCH))
        sha = str(run_spec["script"].get("sha256", ""))
        script_pinned = sha.startswith("sha256:") and len(sha) == len("sha256:") + 64
        outputs_ok = all(("grid_%s.csv" % k) in " ".join(run_spec["expected_outputs"])
                         for k in PHASE_GRIDS)
        rs_fp = "FINALIST" not in json.dumps(run_spec)
        extra.update({"domains_agree": domains_agree, "counts_agree": counts_agree,
                      "versions_agree": versions_agree, "split_agrees": split_agree,
                      "provenance_agree": provenance_agree, "script_pinned": script_pinned,
                      "all_grid_outputs_declared": outputs_ok, "no_legacy_finalist": rs_fp,
                      "expected_outputs": len(run_spec["expected_outputs"])})
        for name, ok in (("domains", domains_agree), ("counts", counts_agree),
                         ("versions", versions_agree), ("split", split_agree)):
            if not ok:
                problems.append("run-spec %s disagree with the round-spec" % name)
        if not provenance_agree:
            problems.append("run-spec dca provenance classes disagree with the round-spec")
        if not script_pinned and not extra["is_template"]:
            problems.append("run-spec script.sha256 is not a pinned sha256 (launch document)")
        if not outputs_ok:
            problems.append("run-spec expected_outputs do not declare every phase grid")
        if not rs_fp:
            problems.append("v1.4.0: the run-spec still carries the legacy FINALIST mapping")
    return c, problems, extra


def self_test(round_spec, run_spec):
    """Negative controls: every mutation below MUST be flagged (otherwise the tool is vacuous)."""
    cases = []

    def mutate(name, fn, expect_substr, which="spec", spec_mutator=None):
        spec = copy.deepcopy(round_spec if which == "spec" else run_spec)
        rs = copy.deepcopy(round_spec)
        if spec_mutator is not None:
            spec_mutator(rs)
        fn(spec)
        _, problems, _ = check(spec if which == "spec" else rs,
                               None if which == "spec" else spec)
        ok = any(expect_substr in p for p in problems)
        cases.append({"case": name, "flagged": ok, "problems": problems[:3]})

    def as_launched(doc):
        doc["template_instantiation"]["status"] = "INSTANTIATED for the production launch card"
        doc["implementation_status"] = {"engine": "status not disclosed"}

    mutate("dca_grid_truncated", lambda s: (s["dca_domain"].__setitem__("grid", s["dca_domain"]["grid"][:40]),
                                            s["dca_domain"].__setitem__("config_count", 40)),
           "cartesian product")
    mutate("universe_shrunk", lambda s: s["eligible_universe"].__setitem__(
        "timeframes", s["eligible_universe"]["timeframes"][:4]), "canonical")
    mutate("wrong_expected_total", lambda s: s["expected"].__setitem__(
        "expected_case_evaluations", 103680), "expected.expected_case_evaluations")
    mutate("legacy_finalist", lambda s: s["selector_and_disposition"]["family_disposition"].__setitem__(
        ">1 cohort survivors", "MULTIPLE_SURVIVORS (verdict FINALIST)"), "FINALIST")
    mutate("moved_split", lambda s: s["data"].__setitem__("oos_start", "2026-01-01"), "frozen split")
    mutate("searched_axis_as_user_fixed", lambda s: s["authorization_invariants"].__setitem__(
        "spacing_pct", 0.02), "contradicts the registered search domain")
    mutate("fingerprint_corrupt", lambda s: s["semantic_fingerprint"].__setitem__(
        "semantic_fingerprint", "sha256:" + "1" * 64), "semantic_fingerprint")
    mutate("invariant_deleted", lambda s: s["authorization_invariants"].__setitem__(
        "starting_equity_usdt", 1000), "registered value")
    mutate("run_spec_placeholder_sha", lambda s: s["script"].__setitem__(
        "sha256", "{{script_sha256}}"), "pinned sha256", which="run-spec",
        spec_mutator=as_launched)
    mutate("run_spec_count_drift", lambda s: s["expected"].__setitem__(
        "expected_case_evaluations", 115200), "run-spec counts disagree", which="run-spec")
    mutate("launched_without_engine_disclosure", as_launched, "must disclose the deployed engine")
    return cases


def main():
    ap = argparse.ArgumentParser(description="Strategy B v2 pre-registration counter/validator")
    ap.add_argument("--spec", default=DEFAULT_SPEC)
    ap.add_argument("--run-spec", default=DEFAULT_RUN_SPEC)
    ap.add_argument("--out", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    try:
        round_spec = load(args.spec)
    except (OSError, ValueError) as exc:
        sys.stderr.write("usage error: cannot read %s: %s\n" % (args.spec, exc))
        return 2
    run_spec = None
    if args.run_spec and os.path.isfile(args.run_spec):
        run_spec = load(args.run_spec)

    computed, problems, extra = check(round_spec, run_spec)
    record = {"schema_version": 1, "kind": "strategy_b_v2_counts",
              "contract_semantics": "v1.4.0 band mapping (unchanged by v1.7.0)",
              "ran_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "spec": portable(args.spec), "run_spec": portable(args.run_spec),
              "canonical": CANONICAL, "computed": {k: computed[k] for k in
                                                   ("cohorts", "strategy_cases", "dca_configs",
                                                    "base_combinations_per_cohort",
                                                    "case_evaluations_per_grid",
                                                    "case_evaluations_per_cohort_all_grids",
                                                    "phase_grid_count",
                                                    "expected_case_evaluations")},
              "extra": extra, "problems": problems, "ok": not problems}
    if args.self_test:
        record["self_test"] = self_test(round_spec, run_spec)
        record["self_test_ok"] = all(c["flagged"] for c in record["self_test"])
        if not record["self_test_ok"]:
            record["problems"].append("self-test controls were not all flagged")
            record["ok"] = False
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    if args.json:
        print(json.dumps(record, indent=2, ensure_ascii=False))
    else:
        c = record["computed"]
        print("cohorts=%d strategy=%d dca=%d base_per_cohort=%d per_grid=%d total=%d "
              "fingerprint=%s ok=%s"
              % (c["cohorts"], c["strategy_cases"], c["dca_configs"],
                 c["base_combinations_per_cohort"], c["case_evaluations_per_grid"],
                 c["expected_case_evaluations"],
                 "MATCH" if extra["fingerprint_declared"] == extra["fingerprint_computed"]
                 or extra["is_template"] else "MISMATCH", record["ok"]))
        for p in problems:
            print("PROBLEM: %s" % p)
    return 0 if record["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
