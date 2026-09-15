#!/usr/bin/env python3
"""Strategy C v1 pre-registration counts / domain / provenance validator (pure stdlib).

One job: recompute every registered count from the declared domains and fail closed when the
pre-registration and the declared axes disagree.  Used by the launch card before any compute
(and by the instantiator after substitution, on the persisted bytes).

    python3 runtime/strategy_c_v1_counts.py --spec <round-spec> [--run-spec <run-spec>] [--json]
    python3 runtime/strategy_c_v1_counts.py --self-test

exit: 0 = ok, 1 = problems, 2 = usage error
"""
import argparse
import hashlib
import itertools
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import parameter_contract as pc  # noqa: E402  (same directory, pure stdlib)

DEFAULT_SPEC = os.path.join(HERE, "templates", "strategy_c_v1_round_spec.template.json")
DEFAULT_RUN_SPEC = os.path.join(HERE, "templates", "strategy_c_v1_run_spec.template.json")

CANONICAL = {
    "cohorts": 1,
    "strategy_cases_per_cohort": 3,
    "dca_configs_per_cohort": 48,
    "base_combinations_per_cohort": 144,
    "phase_grid_count": 11,
    "case_evaluations_per_grid": 144,
    "case_evaluations_per_cohort_all_grids": 1584,
    "expected_case_evaluations": 1584,
}
PHASE_GRIDS = ["historical", "oos", "full", "fee_2x", "funding_2x", "entry_delay_1_bar",
               "slippage_2ticks", "no_funding", "no_funding_full", "cost_attrition_40bps",
               "official_only"]
DECILES = [0.05, 0.10, 0.20]
LOOKBACKS = [180]
DCA_AXES = ("spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct")
PROJECT_CONSTANT = "PROJECT_PRE_REGISTERED_CONSTANT"
PROJECT_SEARCH = "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN"
PRIMARY_MARKER = "SOURCE_SPECIFIED_PRIMARY_AND_SOURCE_REPORTED_SENSITIVITY_TRACKS"
LOOKBACK_MARKER = "SOURCE_SPECIFIED"
# A searched axis, a project constant or a signal-mechanics field may never be presented as a
# user-fixed invariant (contract 7.2 v1.3.1).
USER_FIXED_FORBIDDEN_KEYS = ("base_quote", "base_quote_usdt", "size_multiplier", "spacing_pct",
                             "breakeven_tp_pct", "invalidation_pct", "decile", "lookback_days",
                             "lookback", "hold_hours", "settlement_frequency")
USER_FIXED_EXPECTED = {"starting_equity_usdt": 30000, "numeraire": "USDT (sole)",
                       "tranches": 12, "routine_active_levels": 11, "exit": "reduce-only",
                       "no_add_after_flat_or_kill": True}
SELECTOR_VERSION = "cohort-selector-v1"
DISPOSITION_VERSION = "cohort-disposition-v1"
SPLIT = {"data_start": "2022-01-01", "data_end": "2026-09-11",
         "historical_start": "2022-01-01", "historical_end": "2025-09-30",
         "oos_start": "2025-10-01", "oos_end": "2026-09-11",
         "official_only_start": "2023-11-01", "official_only_end": "2025-09-30"}
GATES = {"min_episodes_is": 20, "neighborhood_min_same_sign_fraction": 0.6,
         "min_oos_sharpe": 0.40, "min_oos_annualized_return": 0.0,
         "max_provenance_relative_difference": 0.5}
SCRIPT_PATH = "/scripts/40_strategy_c_run.py"
SELFCHECK_PATH = "/scripts/tests/test_strategy_c_engine.py"
RUNNER_SHA = "{{script_sha256}}"
SELFCHECK_SHA = "{{engine_selfcheck_sha256}}"


def portable(path):
    """Evidence files must not carry machine-specific absolute paths."""
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
    dom = spec["parameter_domain"]
    strategy_cells = product([dom["grid_deciles"], dom["grid_lookbacks"]])
    dca = spec["dca_domain"]
    dca_cells = product([dca[a] for a in DCA_AXES])
    base = len(strategy_cells) * len(dca_cells)
    per_grid = base * cohorts
    return {"cohorts": cohorts, "strategy_cases": len(strategy_cells),
            "dca_configs": len(dca_cells), "base_combinations_per_cohort": base,
            "case_evaluations_per_grid": per_grid,
            "case_evaluations_per_cohort_all_grids": base * len(PHASE_GRIDS),
            "phase_grid_count": len(PHASE_GRIDS),
            "expected_case_evaluations": per_grid * len(PHASE_GRIDS)}


def _status_checks(spec, prefix, statuses, problems, where):
    prov = spec[where]
    for key, want in statuses:
        got = prov.get(key)
        if not str(got or "").startswith(want):
            problems.append("%s.%s must start with %s, got %r" % (where, key, want, got))


def check(spec, run_spec=None):
    """Return (computed, problems, extra)."""
    problems = []
    c = recompute(spec)

    rename = {"strategy_cases_per_cohort": "strategy_cases",
              "dca_configs_per_cohort": "dca_configs"}
    for key, want in CANONICAL.items():
        got = c[rename.get(key, key)]
        if got != want:
            problems.append("recomputed %s=%r != canonical %r" % (key, got, want))
    if spec["eligible_universe"].get("cohort_count") != c["cohorts"]:
        problems.append("eligible_universe.cohort_count != declared symbols x timeframes")
    if spec["eligible_universe"].get("symbols") != ["BTCUSDT"]:
        problems.append("eligible_universe.symbols must be the source-specified single "
                        "instrument [BTCUSDT]")
    if len(spec["eligible_universe"].get("timeframes") or []) != 1:
        problems.append("eligible_universe.timeframes must register exactly the 15m price "
                        "reference of this family")

    dom = spec["parameter_domain"]
    if dom.get("grid_deciles") != DECILES:
        problems.append("parameter_domain.grid_deciles %r != the registered tracks %r"
                        % (dom.get("grid_deciles"), DECILES))
    if dom.get("grid_lookbacks") != LOOKBACKS:
        problems.append("parameter_domain.grid_lookbacks %r != %r"
                        % (dom.get("grid_lookbacks"), LOOKBACKS))
    if dom.get("legal_cases_per_cohort") != c["strategy_cases"]:
        problems.append("parameter_domain.legal_cases_per_cohort != decile x lookback product")
    if sorted(tuple(g[k] for k in ("decile", "lookback_days")) for g in (dom.get("grid") or [])) != \
            sorted(product([dom["grid_deciles"], dom["grid_lookbacks"]])):
        problems.append("parameter_domain.grid is not the registered decile x lookback product")
    if dom.get("primary_threshold") != 0.10:
        problems.append("parameter_domain.primary_threshold must stay the source-specified 0.10")
    _status_checks(spec, "parameter_domain", (("decile_status", PRIMARY_MARKER),
                                              ("lookback_status", LOOKBACK_MARKER)),
                   problems, "parameter_domain")

    dca = spec["dca_domain"]
    if dca.get("base_quote") != 1000:
        problems.append("dca_domain.base_quote must stay the registered project constant 1000")
    if dca.get("config_count") != c["dca_configs"]:
        problems.append("dca_domain.config_count %r != the four-axis product %r"
                        % (dca.get("config_count"), c["dca_configs"]))
    declared = [tuple(g[a] for a in DCA_AXES) for g in (dca.get("grid") or [])]
    legal = product([dca[a] for a in DCA_AXES])
    if sorted(declared) != sorted(legal):
        problems.append("dca_domain.grid is not the four-axis cartesian product (%d vs %d)"
                        % (len(declared), len(legal)))
    _status_checks(spec, "dca_domain", (("base_quote_status", PROJECT_CONSTANT),
                                        ("spacing_pct_status", PROJECT_SEARCH),
                                        ("size_multiplier_status", PROJECT_SEARCH),
                                        ("breakeven_tp_pct_status", PROJECT_SEARCH),
                                        ("invalidation_pct_status", PROJECT_SEARCH)),
                   problems, "dca_domain")
    invariants = spec.get("authorization_invariants") or {}
    for key in USER_FIXED_FORBIDDEN_KEYS:
        if key in invariants:
            problems.append("authorization_invariants.%s contradicts the registered search "
                            "domain / project constant" % key)
    for key, want in USER_FIXED_EXPECTED.items():
        if invariants.get(key) != want:
            problems.append("authorization_invariants.%s = %r, registered value is %r"
                            % (key, invariants.get(key), want))
    side = str(invariants.get("side", "")).lower()
    if "long-only" not in side or " and short" in side or "long and short" in side:
        problems.append("authorization_invariants.side must register the LONG-only rule and "
                        "must not enable a short leg (got %r)" % invariants.get("side"))

    expected = spec["expected"]
    rename = {"strategy_cases_per_cohort": "strategy_cases",
              "dca_configs_per_cohort": "dca_configs"}
    for key in ("cohorts", "strategy_cases_per_cohort", "dca_configs_per_cohort",
                "base_combinations_per_cohort", "case_evaluations_per_grid",
                "expected_case_evaluations", "phase_grid_count",
                "case_evaluations_per_cohort_all_grids"):
        want = c[rename.get(key, key)]
        if expected.get(key) != want:
            problems.append("expected.%s = %r but the declared axes compute %r"
                            % (key, expected.get(key), want))
    if expected.get("cohort_grid_kinds") != PHASE_GRIDS:
        problems.append("expected.cohort_grid_kinds != the 11 registered phase grids")

    data = spec["data"]
    for key, want in SPLIT.items():
        if data.get(key) != want:
            problems.append("data.%s = %r, the frozen split is %r" % (key, data.get(key), want))
    if not (str(data.get("historical_end")) < str(data.get("oos_start"))):
        problems.append("historical window overlaps the OOS window")
    fc = data.get("funding_series_contract") or {}
    if "modeled" not in json.dumps(fc).lower() or "official" not in json.dumps(fc).lower():
        problems.append("data.funding_series_contract must disclose both the modelled and the "
                        "official funding segments")
    if "official_only" not in json.dumps(data.get("funding_series_contract") or {}).lower():
        problems.append("data.funding_series_contract must register the official-only rerun")

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
        problems.append("family_disposition must register the four bands")
    if "REJECT" not in str(bands.get("0 cohort survivors")):
        problems.append("the 0-survivor band must be REJECT")
    if "PASS" not in str(bands.get("1 cohort survivor")):
        problems.append("the 1-survivor band must be PASS")

    gates = spec["gates"]
    for key, want in GATES.items():
        if gates.get(key) != want:
            problems.append("gates.%s = %r, the registered value is %r"
                            % (key, gates.get(key), want))
    # the gate set may only be widened, never weakened
    if gates.get("min_oos_sharpe", 0) < 0.40:
        problems.append("gates.min_oos_sharpe is below the registered 0.40 floor")
    if gates.get("min_episodes_is", 0) < 20:
        problems.append("gates.min_episodes_is is below the registered no-signal floor")
    if gates.get("neighborhood_min_same_sign_fraction", 0) < 0.6:
        problems.append("gates.neighborhood_min_same_sign_fraction is below the contract 0.60")

    fl = spec.get("registered_family_level_falsification") or {}
    for item in ("threshold-instability", "funding-provenance"):
        if item not in fl:
            problems.append("registered_family_level_falsification.%s is missing" % item)
        else:
            if "never" not in str(fl[item].get("landing", "")).lower():
                problems.append("registered_family_level_falsification.%s must forbid PASS"
                                % item)
    flist = [str(f) for f in spec.get("falsification") or []]
    for label, needle in (("G1 coverage", "coverage incomplete"),
                          ("G2 insufficient trades", "insufficient trades"),
                          ("G3 no qualifying candidate", "no qualifying historical candidate"),
                          ("G4 OOS economic", "oos economic rejection"),
                          ("G5 full economic", "full-window economic rejection"),
                          ("G6 robustness economic", "robustness economic failure"),
                          ("G7 neighbourhood", "parameter-neighbourhood fragility"),
                          ("G8 cost attrition", "cost attrition"),
                          ("funding provenance", "funding-provenance rejection"),
                          ("threshold instability", "threshold instability"),
                          ("DCA order-fill accounting", "dca execution incomplete")):
        if not any(needle in f.lower() for f in flist):
            problems.append("the registered falsification battery is missing the %s item" % label)
    if not any("never PASS" in f for f in flist):
        problems.append("the registered falsification list must state that the family-level "
                        "items can never be recorded as PASS")
    if len(flist) < 10:
        problems.append("the registered falsification battery has fewer than 10 items")

    problems += ["parameter_contract: %s" % p for p in pc.validate_round_spec_contract(spec)]
    fp_declared = (spec.get("semantic_fingerprint") or {}).get("semantic_fingerprint")
    fp_input = (spec.get("semantic_fingerprint") or {}).get("fingerprint_input")
    fp_computed = fingerprint(fp_input) if fp_input else None
    is_template = fp_declared == "TO_BE_RECOMPUTED"
    if not is_template and fp_declared != fp_computed:
        problems.append("semantic_fingerprint.semantic_fingerprint %r != recomputed %r"
                        % (fp_declared, fp_computed))
    if not fp_input:
        problems.append("semantic_fingerprint.fingerprint_input is missing")

    extra = {"is_template": is_template, "fingerprint_declared": fp_declared,
             "fingerprint_computed": fp_computed,
             "fingerprint_match": bool(is_template or fp_declared == fp_computed),
             "domains_agree": None, "counts_agree": None, "versions_agree": None,
             "split_agrees": None, "provenance_agree": None, "script_pinned": None,
             "all_grid_outputs_declared": None, "expected_outputs": 0}

    if run_spec is not None:
        if run_spec.get("family_id") != spec.get("family_id"):
            problems.append("run-spec family_id %r != round-spec %r"
                            % (run_spec.get("family_id"), spec.get("family_id")))
        if run_spec.get("round_id") != spec.get("round_id"):
            problems.append("run-spec round_id %r != round-spec %r"
                            % (run_spec.get("round_id"), spec.get("round_id")))
        # In TEMPLATE mode the two documents deliberately spell the same ids with different
        # placeholders ({{task_id}} vs {{kanban_task_id}}); the instantiator substitutes both
        # and the persisted pair is re-validated.
        template_mode = "{{" in str(run_spec.get("task_id", "")) and \
            "{{" in str(spec.get("kanban_task_id", ""))
        if not template_mode and run_spec.get("task_id") != spec.get("kanban_task_id"):
            problems.append("run-spec task_id %r != round-spec kanban_task_id %r"
                            % (run_spec.get("task_id"), spec.get("kanban_task_id")))
        if run_spec.get("kanban_board") != spec.get("kanban_board"):
            problems.append("run-spec kanban_board %r != round-spec %r"
                            % (run_spec.get("kanban_board"), spec.get("kanban_board")))
        rd, sd = run_spec["data"], spec["data"]
        # the run-spec spells the registered window as start/end (the engine's own keys); the
        # round-spec registers it as data_start/data_end.  Same values, two documented spellings.
        canonical_run = dict(rd)
        canonical_run["data_start"], canonical_run["data_end"] = rd.get("start"), rd.get("end")
        diff = [k for k in SPLIT if canonical_run.get(k) != sd.get(k)]
        universe = spec["eligible_universe"]
        if universe.get("symbols") != rd.get("symbols") or \
                universe.get("timeframes") != rd.get("timeframes"):
            diff.append("universe")
        extra["split_agrees"] = not diff
        if diff:
            problems.append("run-spec data %r does not match the round-spec registration" % diff)
        rdom, sdom = run_spec["parameter_domain"], spec["parameter_domain"]
        dom_same = (rdom.get("grid_deciles") == sdom["grid_deciles"]
                    and rdom.get("grid_lookbacks") == sdom["grid_lookbacks"]
                    and rdom.get("primary_threshold") == sdom["primary_threshold"])
        extra["domains_agree"] = bool(dom_same)
        if not dom_same:
            problems.append("run-spec parameter_domain does not match the round-spec registration")
        rdca, sdca = run_spec["dca_domain"], spec["dca_domain"]
        for key in ("base_quote",) + DCA_AXES + ("grid",):
            if rdca.get(key) != sdca.get(key):
                problems.append("run-spec dca_domain.%s does not match the round-spec" % key)
        mirror = run_spec.get("provenance_mirror") or {}
        pairs = [("base_quote_status", sdca.get("base_quote_status")),
                 ("spacing_pct_status", sdca.get("spacing_pct_status")),
                 ("size_multiplier_status", sdca.get("size_multiplier_status")),
                 ("breakeven_tp_pct_status", sdca.get("breakeven_tp_pct_status")),
                 ("invalidation_pct_status", sdca.get("invalidation_pct_status")),
                 ("decile_status", sdom.get("decile_status")),
                 ("lookback_status", sdom.get("lookback_status"))]
        bad = [k for k, v in pairs if mirror.get(k) != v]
        extra["provenance_agree"] = not bad
        if bad:
            problems.append("run-spec provenance_mirror disagrees with the round-spec for %r"
                            % bad)
        rver = (run_spec.get("selector_version"), run_spec.get("disposition_version"))
        sver = (SELECTOR_VERSION, DISPOSITION_VERSION)
        extra["versions_agree"] = rver == sver
        if rver != sver:
            problems.append("run-spec selector/disposition versions %r != %r" % (rver, sver))
        for key, want in GATES.items():
            if (run_spec.get("gates") or {}).get(key) != want:
                problems.append("run-spec gates.%s != the registered value" % key)
        for key in ("cohorts", "strategy_cases_per_cohort", "dca_configs_per_cohort",
                    "base_combinations_per_cohort", "case_evaluations_per_grid",
                    "expected_case_evaluations", "phase_grid_count"):
            if (run_spec.get("expected") or {}).get(key) != expected.get(key):
                problems.append("run-spec expected.%s != the round-spec registration" % key)
        costs = run_spec.get("costs") or {}
        if costs.get("fee_bps") != 5 or costs.get("baseline_slippage_ticks") != 1 or \
                costs.get("cost_attrition_fee_mult") != 8.0:
            problems.append("run-spec costs must register 5 bps taker, 1 baseline slippage tick "
                            "and the 8x cost-attrition stress")
        script = run_spec.get("script") or {}
        sha = script.get("sha256")

        def _pinned(value, placeholder):
            if value == placeholder:
                return True          # template mode: the placeholders are substituted at instantiation
            if not isinstance(value, str) or not value.startswith("sha256:"):
                return False
            body = value[len("sha256:"):]
            return len(body) == 64 and all(c in "0123456789abcdef" for c in body)

        pinned = script.get("path") == SCRIPT_PATH and _pinned(sha, RUNNER_SHA)
        extra["script_pinned"] = pinned
        if not pinned:
            problems.append("run-spec script must pin %s + %s (or the instantiated sha256)"
                            % (SCRIPT_PATH, RUNNER_SHA))
        selfcheck = run_spec.get("engine_selfcheck") or {}
        if selfcheck.get("script") != SELFCHECK_PATH or \
                not _pinned(selfcheck.get("sha256"), SELFCHECK_SHA):
            problems.append("run-spec engine_selfcheck must pin %s (+ %s or the instantiated "
                            "sha256)" % (SELFCHECK_PATH, SELFCHECK_SHA))
        outs = run_spec.get("expected_outputs") or []
        missing = [g for g in PHASE_GRIDS if ("artifacts/grid_%s.csv" % g) not in outs]
        for name in ("result.json", "artifacts/assertions.json", "artifacts/cohort_results.json",
                     "artifacts/cohort_survivors.json", "artifacts/funding_provenance.json",
                     "artifacts/robustness_diagnostics.json", "artifacts/funding_series.json"):
            if name not in outs:
                missing.append(name)
        extra["all_grid_outputs_declared"] = not missing
        extra["expected_outputs"] = len(outs)
        if missing:
            problems.append("run-spec expected_outputs is missing %r" % missing[:6])

    extra["counts_agree"] = all(c[rename.get(k, k)] == CANONICAL[k] for k in CANONICAL)
    return c, problems, extra


def self_test(spec, run_spec):
    """Negative controls: the validator must reject each tampered registration."""
    cases = []

    def mutate(name, fn):
        import copy
        rs, ss = copy.deepcopy(spec), copy.deepcopy(run_spec)
        target = fn(rs, ss)
        _, problems, _ = check(rs[0] if isinstance(rs, tuple) else rs,
                               None if ss is None else ss)
        cases.append({"case": name, "detected": bool(problems), "problems": problems[:2]})

    mutate("dca grid shrunk", lambda r, u: r["dca_domain"]["grid"].pop())
    mutate("split moved", lambda r, u: r["data"].__setitem__("oos_start", "2025-11-01"))
    mutate("official-only window dropped", lambda r, u: r["data"].pop("official_only_start"))
    mutate("decile tracks widened", lambda r, u: r["parameter_domain"]["grid_deciles"].append(0.15))
    mutate("primary threshold replaced",
           lambda r, u: r["parameter_domain"].__setitem__("primary_threshold", 0.05))
    mutate("base_quote relabelled user-fixed",
           lambda r, u: r["authorization_invariants"].__setitem__("base_quote", 1000))
    mutate("os shorpe floor lowered", lambda r, u: r["gates"].__setitem__("min_oos_sharpe", 0.2))
    mutate("no-signal floor lowered", lambda r, u: r["gates"].__setitem__("min_episodes_is", 5))
    mutate("provenance class flipped",
           lambda r, u: r["dca_domain"].__setitem__("size_multiplier_status", "USER_FIXED"))
    mutate("fingerprint fabricated",
           lambda r, u: r["semantic_fingerprint"].__setitem__("semantic_fingerprint", "sha256:0"))
    mutate("run-spec mirror drift",
           lambda r, u: u["provenance_mirror"].__setitem__("base_quote_status", "USER_FIXED"))
    mutate("run-spec fee drift", lambda r, u: u["costs"].__setitem__("fee_bps", 1))
    mutate("parameter contract removed", lambda r, u: r.pop("parameter_contract"))
    mutate("short leg smuggled in",
           lambda r, u: r["authorization_invariants"].__setitem__("side", "long and short"))
    mutate("long-only rule dropped",
           lambda r, u: r["authorization_invariants"].__setitem__("side", "both sides allowed"))
    mutate("official-only rerun unregistered",
           lambda r, u: r["data"]["funding_series_contract"].pop("official_only_rerun"))
    mutate("falsification item deleted", lambda r, u: r["falsification"].pop())
    mutate("user-fixed invariant deleted",
           lambda r, u: r["authorization_invariants"].pop("tranches"))
    return cases


def main():
    ap = argparse.ArgumentParser(description="validate the Strategy C v1 preregistration")
    ap.add_argument("--spec", default=DEFAULT_SPEC)
    ap.add_argument("--run-spec", default=None)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    try:
        spec = load(args.spec)
        run_spec = load(args.run_spec) if args.run_spec else None
    except (OSError, ValueError) as exc:
        sys.stderr.write("cannot read spec: %s\n" % exc)
        return 2
    if args.run_spec is None and os.path.basename(args.spec).startswith("strategy_c_v1_round"):
        run_spec = load(DEFAULT_RUN_SPEC)
    c, problems, extra = check(spec, run_spec)
    controls = self_test(spec, run_spec) if args.self_test else []
    out = {"spec": portable(args.spec), "run_spec": portable(args.run_spec),
           "computed": c, "problems": problems, "extra": extra,
           "self_test": controls,
           "self_test_failed": [x["case"] for x in controls if not x["detected"]],
           "ok": not problems and not [x for x in controls if not x["detected"]]}
    if args.json:
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        print("cohorts=%d strategy=%d dca=%d base_per_cohort=%d per_grid=%d total=%d "
              "fingerprint=%s ok=%s"
              % (c["cohorts"], c["strategy_cases"], c["dca_configs"],
                 c["base_combinations_per_cohort"], c["case_evaluations_per_grid"],
                 c["expected_case_evaluations"],
                 "MATCH" if extra["fingerprint_match"] else "MISMATCH", out["ok"]))
        for p in problems:
            print("PROBLEM: %s" % p)
        for x in controls:
            print("negative control %-32s detected=%s" % (x["case"], x["detected"]))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
