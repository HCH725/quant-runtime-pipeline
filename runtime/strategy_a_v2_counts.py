#!/usr/bin/env python3
"""Strategy A v2 (Contract v1.3.0) pre-registration counter / validator - pure stdlib, read-only.

Contract v1.3.0 makes the full backtest

    symbols x timeframes x strategy parameter domain x DCA parameter domain
    x (historical / OOS / robustness)

and requires the exact expected case count to be computed and checked IN THE
PRE-REGISTRATION, before any computation, and never to be resized afterwards.

This script does exactly that and nothing else: it reads the round-spec (and
optionally the run-spec) template, recomputes every count and every registered
request from the declared axes, and compares them against the canonical
v1.3.0 numbers and against the values the templates declare.  Any mismatch is a
problem (exit 1).  It never writes a card, never touches /results, never runs
Qlib, and it is not a service.

usage:
  python3 runtime/strategy_a_v2_counts.py [--spec <round-spec.json>]
                                          [--run-spec <run-spec.json>]
                                          [--out <evidence.json>] [--json]
exit: 0 = ok, 1 = problems, 2 = usage error
"""
import argparse
import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
DEFAULT_SPEC = os.path.join(HERE, "templates", "strategy_a_v2_round_spec.template.json")
DEFAULT_RUN_SPEC = os.path.join(HERE, "templates", "strategy_a_v2_run_spec.template.json")


def portable(path):
    """Evidence files must not carry host-specific absolute paths (evidence/README rule 2)."""
    if path and path.startswith(REPO_ROOT + os.sep):
        return "<REPO>/" + os.path.relpath(path, REPO_ROOT)
    return path

# Canonical v1.3.0 arithmetic (contract 7.2 / 7.3, card t_ad2e119e).  Hard-coded on
# purpose: the script must be able to contradict the document, not merely echo it.
CANONICAL = {
    "cohorts": 20,
    "strategy_cases_per_cohort": 12,
    "dca_configs_per_cohort": 48,
    "base_combinations_per_cohort": 576,
    "phase_grid_count": 9,
    "case_evaluations_per_grid": 11520,
    "case_evaluations_per_cohort_all_grids": 5184,
    "expected_case_evaluations": 103680,
}
PHASE_GRIDS = ["historical", "oos", "full", "fee_2x", "funding_2x", "entry_delay_1_bar",
               "slippage_2ticks", "no_funding", "no_funding_full"]
DCA_AXES = ("spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct")


def now_utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def product(axes):
    """Cartesian product of a list of value lists, in a deterministic order."""
    out = [()]
    for values in axes:
        out = [t + (v,) for t in out for v in values]
    return out


def fingerprint(fingerprint_input):
    return "sha256:" + hashlib.sha256(fingerprint_input.encode()).hexdigest()


def validate(spec, run_spec=None):
    """Recompute every registered v1.3.0 count.  Returns (computed, problems)."""
    problems = []
    universe = spec.get("eligible_universe") or {}
    symbols = list(universe.get("symbols") or [])
    timeframes = list(universe.get("timeframes") or [])
    cohorts = len(symbols) * len(timeframes)
    if len(set(symbols)) != len(symbols) or len(set(symbols)) == 0:
        problems.append("eligible_universe.symbols is empty or has duplicates")
    if len({tf.get("raw_interval") for tf in timeframes}) != len(timeframes) or not timeframes:
        problems.append("eligible_universe.timeframes is empty or has duplicates")

    params = spec.get("parameter_domain") or {}
    windows = list(params.get("window") or [])
    discounts = list(params.get("discount") or [])
    strategy_cells = product([windows, discounts])
    declared_strategy = [(g.get("window"), g.get("discount")) for g in (params.get("grid") or [])]
    if len(set(strategy_cells)) != len(strategy_cells):
        problems.append("strategy domain has duplicate axis values")
    if sorted(set(strategy_cells)) != sorted(set(declared_strategy)) or len(declared_strategy) != len(strategy_cells):
        problems.append("parameter_domain.grid is not the cartesian product of window x discount "
                        "(declared %d entries, product %d)" % (len(declared_strategy), len(strategy_cells)))
    if params.get("grid_windows") != windows or params.get("grid_discounts") != discounts:
        problems.append("parameter_domain.grid_windows/grid_discounts disagree with window/discount")

    dca = spec.get("dca_domain") or {}
    axes = [list(dca.get(a) or []) for a in DCA_AXES]
    for name, values in zip(DCA_AXES, axes):
        if not values:
            problems.append("dca_domain.%s is missing or empty" % name)
    dca_cells = product(axes)
    declared_dca = [tuple(g.get(a) for a in DCA_AXES) for g in (dca.get("grid") or [])]
    if len(set(dca_cells)) != len(dca_cells):
        problems.append("dca domain has duplicate axis values")
    if sorted(set(dca_cells)) != sorted(set(declared_dca)) or len(declared_dca) != len(dca_cells):
        problems.append("dca_domain.grid is not the cartesian product of the four DCA axes "
                        "(declared %d entries, product %d)" % (len(declared_dca), len(dca_cells)))
    base_quotes = {g.get("base_quote") for g in (dca.get("grid") or [])}
    if base_quotes != {dca.get("base_quote")}:
        problems.append("dca_domain.grid base_quote is not the registered constant %r"
                        % dca.get("base_quote"))

    strategy_cases = len(strategy_cells)
    dca_configs = len(dca_cells)
    base_combos = strategy_cases * dca_configs
    per_grid = base_combos * cohorts
    total = per_grid * len(PHASE_GRIDS)
    computed = {
        "symbols": symbols,
        "timeframes": [tf.get("raw_interval") for tf in timeframes],
        "cohorts": cohorts,
        "strategy_cases_per_cohort": strategy_cases,
        "dca_configs_per_cohort": dca_configs,
        "base_combinations_per_cohort": base_combos,
        "phase_grids": list(PHASE_GRIDS),
        "phase_grid_count": len(PHASE_GRIDS),
        "case_evaluations_per_grid": per_grid,
        "case_evaluations_per_cohort_all_grids": base_combos * len(PHASE_GRIDS),
        "expected_case_evaluations": total,
    }
    for key, want in CANONICAL.items():
        got = computed.get(key)
        if got != want:
            problems.append("canonical mismatch %s: computed %r, contract v1.3.0 requires %r"
                            % (key, got, want))

    expected = spec.get("expected") or {}
    for key in ("cohorts", "strategy_cases_per_cohort", "dca_configs_per_cohort",
                "base_combinations_per_cohort", "case_evaluations_per_grid",
                "case_evaluations_per_cohort_all_grids", "expected_case_evaluations"):
        if expected.get(key) != computed[key]:
            problems.append("round-spec expected.%s = %r but the declared axes compute %r"
                            % (key, expected.get(key), computed[key]))
    if expected.get("cohort_grid_kinds") != PHASE_GRIDS:
        problems.append("round-spec expected.cohort_grid_kinds != the engine's registered grids")
    if params.get("legal_cases_per_cohort") not in (None, strategy_cases):
        problems.append("parameter_domain.legal_cases_per_cohort != %d" % strategy_cases)
    if dca.get("config_count") not in (None, dca_configs):
        problems.append("dca_domain.config_count != %d" % dca_configs)

    selector = spec.get("selector_and_disposition") or {}
    if not selector.get("selector_version") or not selector.get("disposition_version"):
        problems.append("selector_and_disposition versions missing (they belong in the fingerprint)")
    if not isinstance(selector.get("family_disposition"), dict):
        problems.append("selector_and_disposition.family_disposition missing "
                        "(0/1/>1 survivor + TECHNICAL_INCOMPLETE mapping must be pre-registered)")
    bands = selector.get("family_disposition") or {}
    if not any("TECHNICAL_INCOMPLETE" in str(v) for v in bands.values()):
        problems.append("family_disposition does not register the TECHNICAL_INCOMPLETE band")

    data = spec.get("data") or {}
    if not (data.get("historical_start") == data.get("data_start") == "2022-01-01"):
        problems.append("historical window does not start at the registered 2022-01-01")
    if data.get("historical_end") != "2025-09-30" or data.get("oos_start") != "2025-10-01":
        problems.append("historical/OOS split is not the frozen 2025-09-30 | 2025-10-01 boundary")
    if data.get("oos_end") != data.get("data_end") == "2026-09-10":
        problems.append("OOS window does not end at the registered 2026-09-10")
    if not (str(data.get("historical_end")) < str(data.get("oos_start"))):
        problems.append("historical window overlaps the OOS window")

    fp = spec.get("semantic_fingerprint") or {}
    fp_input = fp.get("fingerprint_input")
    recomputed = fingerprint(fp_input) if fp_input else None
    declared_fp = fp.get("semantic_fingerprint")
    fp_status = "MISSING"
    if recomputed:
        if declared_fp == recomputed:
            fp_status = "MATCH"
        elif declared_fp and "TO_BE_RECOMPUTED" in str(declared_fp):
            fp_status = "PLACEHOLDER_RECOMPUTED"
        else:
            fp_status = "MISMATCH"
            problems.append("semantic_fingerprint %r != sha256(fingerprint_input) %r"
                            % (declared_fp, recomputed))
    else:
        problems.append("semantic_fingerprint.fingerprint_input missing "
                        "(the fingerprint must carry strategy domain + DCA domain + universe + "
                        "direction + data window + selector/disposition version, contract 14.3)")
    if fp_input:
        for needed in ("selector=cohort-selector-v1", "disposition=cohort-disposition-v1",
                       "spacing_pct=", "size_multiplier=", "breakeven_tp_pct=",
                       "invalidation_pct=", "symbols=", "long/flat"):
            if needed not in fp_input:
                problems.append("fingerprint_input does not contain %r (contract 14.3 / 7.2)" % needed)

    run_spec_check = None
    if run_spec is not None:
        rs_params = run_spec.get("params") or {}
        rs_dca = run_spec.get("dca_domain") or {}
        same = (rs_params.get("grid") == params.get("grid")
                and rs_params.get("grid_windows") == windows
                and rs_params.get("grid_discounts") == discounts
                and rs_dca.get("grid") == dca.get("grid")
                and all(rs_dca.get(a) == dca.get(a) for a in DCA_AXES)
                and rs_dca.get("base_quote") == dca.get("base_quote"))
        rs_expected = run_spec.get("expected") or {}
        rs_counts_ok = all(rs_expected.get(k) == computed[k] for k in
                           ("cohorts", "strategy_cases_per_cohort", "dca_configs_per_cohort",
                            "base_combinations_per_cohort", "case_evaluations_per_grid",
                            "expected_case_evaluations"))
        rs_data = run_spec.get("data") or {}
        split_map = {"start": "data_start", "end": "data_end",
                     "historical_start": "historical_start", "historical_end": "historical_end",
                     "oos_start": "oos_start", "oos_end": "oos_end"}
        rs_split_ok = all(rs_data.get(rs_key) == data.get(spec_key)
                          for rs_key, spec_key in split_map.items())
        rs_versions_ok = (run_spec.get("selector_version") == selector.get("selector_version")
                          and run_spec.get("disposition_version") == selector.get("disposition_version"))
        rs_script = (run_spec.get("script") or {}).get("sha256")
        run_spec_check = {
            "domains_agree": bool(same),
            "counts_agree": bool(rs_counts_ok),
            "split_agrees": bool(rs_split_ok),
            "versions_agree": bool(rs_versions_ok),
            "script_sha256_is_placeholder": bool(rs_script and "{{" in str(rs_script)),
            "expected_outputs": len(run_spec.get("expected_outputs") or []),
        }
        if not same:
            problems.append("run-spec domains disagree with the round-spec domains")
        if not rs_counts_ok:
            problems.append("run-spec expected counts disagree with the computed counts")
        if not rs_split_ok:
            problems.append("run-spec data split disagrees with the round-spec split")
        if not rs_versions_ok:
            problems.append("run-spec selector/disposition versions disagree with the round-spec")

    return {
        "computed": computed,
        "canonical": CANONICAL,
        "fingerprint": {"input": fp_input, "recomputed": recomputed, "declared": declared_fp,
                        "status": fp_status},
        "run_spec_check": run_spec_check,
        "problems": problems,
    }


def main():
    ap = argparse.ArgumentParser(description="Strategy A v2 pre-registration counter (contract v1.3.0)")
    ap.add_argument("--spec", default=DEFAULT_SPEC, help="round-spec (default: the repo template)")
    ap.add_argument("--run-spec", default=None, help="optional run-spec to cross-check")
    ap.add_argument("--out", default=None, help="write the result JSON here (evidence snapshot)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    for path in [p for p in (args.spec, args.run_spec) if p]:
        if not os.path.isfile(path):
            sys.stderr.write("usage error: not a file: %s\n" % path)
            return 2
    with open(args.spec) as fh:
        spec = json.load(fh)
    run_spec = None
    if args.run_spec:
        with open(args.run_spec) as fh:
            run_spec = json.load(fh)

    result = validate(spec, run_spec)
    out = {
        "schema_version": 1,
        "kind": "strategy_a_v2_pre_registration_counts",
        "contract_section": "7.2 / 7.3 (Contract v1.3.0)",
        "checked_at_utc": now_utc(),
        "round_spec": portable(os.path.abspath(args.spec)),
        "run_spec": portable(os.path.abspath(args.run_spec)) if args.run_spec else None,
        "family_id": spec.get("family_id"),
        "ok": not result["problems"],
    }
    out.update(result)
    text = json.dumps(out, indent=2, ensure_ascii=False)
    if args.out:
        tmp = args.out + ".tmp"
        with open(tmp, "w") as fh:
            fh.write(text + "\n")
        os.replace(tmp, args.out)
    if args.json:
        print(text)
    else:
        c = result["computed"]
        print("cohorts=%d strategy=%d dca=%d base_per_cohort=%d per_grid=%d total=%d "
              "fingerprint=%s ok=%s"
              % (c["cohorts"], c["strategy_cases_per_cohort"], c["dca_configs_per_cohort"],
                 c["base_combinations_per_cohort"], c["case_evaluations_per_grid"],
                 c["expected_case_evaluations"], result["fingerprint"]["status"], out["ok"]))
        for p in result["problems"]:
            print("PROBLEM: %s" % p)
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
