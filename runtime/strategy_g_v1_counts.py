#!/usr/bin/env python3
"""Strategy G v1 pre-registration counts and provenance checks (pure stdlib).

Single job: recompute every registered count of the Bitcoin intraday time-series momentum
(volume-anchored session) family from the declared axes, fail closed on any mismatch with the
frozen constants below, and enforce the contract's provenance classification (section 7.2
v1.3.1): the three searched strategy axes and the four searched DCA axes are
PROJECT_PRE_REGISTERED_SEARCH_DOMAIN, base_quote is PROJECT_PRE_REGISTERED_CONSTANT, the
unrecovered execution contract is RESEARCH_DEFINED, and only items with explicit operator
evidence may sit in USER_FIXED.  Used by the authoring/instantiation path (before anything is
published) and by the repo tests.
"""
import hashlib
import json

FAMILY_ID = "bitcoin-intraday-time-series-momentum-volume-session-2026-08-31"
EMPTY_MARKER = "{{"

SYMBOLS = ["BTCUSDT"]
TIMEFRAMES = [{"raw_interval": "5m", "qlib_freq": "5min"},
              {"raw_interval": "15m", "qlib_freq": "15min"},
              {"raw_interval": "30m", "qlib_freq": "30min"}]
SESSION_BASES = ["trail7", "trail30", "trail90"]
ENTRY_RULES = ["hold_to_close", "last_half_hour"]
CONDITIONINGS = ["all_sessions", "high_vol_only"]
CASE_FIELDS = ["basis_trail7", "basis_trail30", "basis_trail90",
               "entry_hold_to_close", "entry_last_half_hour", "cond_high_vol"]
STRATEGY_CASES = [tuple([1 if b == i else 0 for b in range(3)]
                        + [1 if e == j else 0 for e in range(2)] + [c])
                  for i in range(3) for j in range(2) for c in range(2)]
CASE_NAMES = ["%s__%s__%s" % (SESSION_BASES[i], ENTRY_RULES[j], "all" if c == 0 else "highvol")
              for i in range(3) for j in range(2) for c in range(2)]
DCA_AXES = ("spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct")
DCA_GRID = {"spacing_pct": [0.01, 0.02, 0.03, 0.04],
            "size_multiplier": [1.0, 1.1],
            "breakeven_tp_pct": [0.01, 0.02, 0.03],
            "invalidation_pct": [0.05, 0.10]}
COHORT_GRID_KINDS = ("historical", "oos", "full", "fee_2x", "funding_2x", "entry_delay_1_bar",
                     "slippage_2ticks", "no_funding", "no_funding_full", "cost_attrition_40bps")
CANONICAL = {"cohorts": len(SYMBOLS) * len(TIMEFRAMES),
             "strategy_cases": len(STRATEGY_CASES),
             "dca_configs": 48,
             "base_combinations_per_cohort": len(STRATEGY_CASES) * 48,
             "phase_grid_count": len(COHORT_GRID_KINDS)}
CANONICAL["case_evaluations_per_grid"] = (CANONICAL["cohorts"]
                                          * CANONICAL["base_combinations_per_cohort"])
CANONICAL["expected_case_evaluations"] = (CANONICAL["case_evaluations_per_grid"]
                                          * CANONICAL["phase_grid_count"])
GATES = {"min_episodes_is": 100, "min_episodes_oos": 25,
         "neighborhood_min_same_sign_fraction": 0.6}
DATA = {"start": "2022-01-01", "end": "2026-09-11", "historical_start": "2022-01-01",
        "historical_end": "2025-09-30", "oos_start": "2025-10-01", "oos_end": "2026-09-11"}
COSTS = {"taker_fee": 0.0005, "maker_fee": 0.0002, "baseline_slippage_ticks": 1,
         "cost_attrition_fee_mult": 8.0, "cost_attrition_bps": 40}
SEARCH_STATUS = "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN"
CONSTANT_STATUS = "PROJECT_PRE_REGISTERED_CONSTANT"
RESEARCH_MARKER = "RESEARCH_DEFINED"
SESSION_CONTRACT = {
    "slot_grid": "the UTC day is sliced into 48 half-hour slots; every registered base grid "
                 "(5m/15m/30m) divides a slot exactly",
    "anchor_rule": "anchor(D) = argmax over the 48 slots of the MEAN slot volume over the "
                   "trailing `session_basis` days STRICTLY BEFORE the session's own day; ties "
                   "resolve to the smallest slot; the anchor plays the role of the market open",
    "session": "[day + anchor x 30 min, +24 h); first half hour = the anchor slot, last half "
               "hour = the session's final 30 minutes",
    "signal": "R_first = close(first half hour) / open(first half hour) - 1; R_first > 0 -> "
              "LONG episode, R_first < 0 -> SHORT episode, R_first == 0 -> no episode",
    "entry_rule": "hold_to_close: the OPEN of the first base bar that opens at or after "
                  "session_start + 30 min (the first executable price after the signal is "
                  "complete); last_half_hour: the OPEN of the first base bar that opens at or "
                  "after session_start + 23 h 30 min",
    "exit_rule": "reduce-only flatten at the CLOSE of the session's last base bar (a hard "
                 "boundary, never truncated into a fake time exit)",
    "conditioning": "all_sessions trades every session; high_vol_only trades only sessions "
                    "whose PRE-session realised volatility (population stdev of base-bar log "
                    "returns over the 24 h ending at the session start) is strictly above the "
                    "trailing median of that same statistic over the previous 30 sessions (PIT; "
                    "an incomplete volatility window cannot qualify)",
    "non_overlap": "a session whose entry bar falls inside an already open episode is skipped "
                   "(non-overlapping primary protocol) and counted",
    "point_in_time": "the anchor profile of day D reads only days < D, the signal is complete "
                     "at the entry instant, and no path reads a future bar"}
USER_FIXED_INVARIANTS = ("starting_equity_usdt", "numeraire", "venue", "leverage", "tranches",
                         "tranche_12", "routine_active_levels", "initial_entry_and_scale_ins",
                         "reduce_only_exit", "same_bar_multi_level_ordering",
                         "no_add_after_flat_or_kill")


def load(path):
    with open(path) as fh:
        return json.load(fh)


def product(axes):
    out = [()]
    for values in axes:
        out = [prev + (v,) for prev in out for v in values]
    return out


def fingerprint(fingerprint_input):
    return "sha256:" + hashlib.sha256(str(fingerprint_input).encode()).hexdigest()


def recompute(spec):
    """Every registered count, recomputed from the declared axes."""
    uni = spec["eligible_universe"]
    dom = spec["parameter_domain"]
    dca = spec["dca_domain"]
    cohorts = len(uni["symbols"]) * len(uni["timeframes"])
    cases = [tuple(g[f] for f in CASE_FIELDS) for g in dom["grid_cases"]]
    dca_cells = product([dca[a] for a in DCA_AXES])
    base = len(cases) * len(dca_cells)
    grids = list(spec["expected"]["cohort_grid_kinds"])
    per_grid = cohorts * base
    return {"cohorts": cohorts, "strategy_cases": len(cases), "dca_configs": len(dca_cells),
            "base_combinations_per_cohort": base, "phase_grid_count": len(grids),
            "case_evaluations_per_grid": per_grid,
            "expected_case_evaluations": per_grid * len(grids),
            "dca_cells": dca_cells, "cases": cases}


def _status_token(block, key):
    """The classification TOKEN of a provenance field (the prose after it is free)."""
    return str(block.get(key, "")).split(" ")[0].strip()


def _provenance_checks(spec, problems):
    """Contract 7.2 v1.3.1: searched axes and project constants are never user-fixed."""
    dca = spec["dca_domain"]
    dom = spec["parameter_domain"]
    inv = spec.get("authorization_invariants", {})
    if not str(inv.get("provenance_class", "")).startswith("USER_FIXED"):
        problems.append("authorization_invariants.provenance_class must be the registered "
                        "USER_FIXED scope statement")
    for name in USER_FIXED_INVARIANTS:
        if name not in inv:
            problems.append("authorization_invariants is missing the user-fixed invariant %r"
                            % name)
    if _status_token(dca, "base_quote_status") != CONSTANT_STATUS:
        problems.append("dca_domain.base_quote_status must start with %r (it is a project "
                        "pre-registration constant, not a user-fixed invariant)"
                        % CONSTANT_STATUS)
    for axis in DCA_AXES:
        if _status_token(dca, axis + "_status") != SEARCH_STATUS:
            problems.append("dca_domain.%s_status must start with %r (this axis IS searched)"
                            % (axis, SEARCH_STATUS))
    for axis in ("session_basis", "entry_rule", "conditioning"):
        if _status_token(dom, axis + "_status") != SEARCH_STATUS:
            problems.append("parameter_domain.%s_status must start with %r (this axis IS "
                            "searched)" % (axis, SEARCH_STATUS))
    for field in ("session_contract_status", "entry_timing_status", "exit_timing_status"):
        if not str(dom.get(field, "")).startswith(RESEARCH_MARKER):
            problems.append("parameter_domain.%s must declare the unrecovered execution detail "
                            "as %s" % (field, RESEARCH_MARKER))
    for name in list(DCA_AXES) + ["base_quote"] + list(CASE_FIELDS):
        if name in inv:
            problems.append("authorization_invariants must not list the searched axis or "
                            "project constant %r as user-fixed (contract 7.2 v1.3.1)" % name)


def check(spec, run_spec=None):
    """Return (computed, problems, extra)."""
    problems = []
    c = recompute(spec)
    for key in ("cohorts", "strategy_cases", "dca_configs", "base_combinations_per_cohort",
                "phase_grid_count", "case_evaluations_per_grid", "expected_case_evaluations"):
        if c[key] != CANONICAL[key]:
            problems.append("recomputed %s=%r != canonical %r" % (key, c[key], CANONICAL[key]))
    uni = spec["eligible_universe"]
    if uni.get("cohort_count") != c["cohorts"]:
        problems.append("eligible_universe.cohort_count != the registered symbol x timeframe "
                        "product")
    if uni.get("symbols") != SYMBOLS:
        problems.append("eligible_universe.symbols must be the record's Bitcoin instrument %r"
                        % (SYMBOLS,))
    if uni.get("timeframes") != TIMEFRAMES:
        problems.append("eligible_universe.timeframes must register exactly the three base "
                        "grids {5m,15m,30m} that divide the 30 min session slot")
    if "excluded" not in uni:
        problems.append("eligible_universe must register the excluded symbols/timeframes")
    dom = spec["parameter_domain"]
    if c["cases"] != [tuple(x) for x in STRATEGY_CASES]:
        problems.append("parameter_domain.grid_cases %r != the registered (basis, entry rule, "
                        "conditioning) product in the registered order" % (c["cases"],))
    if dom.get("legal_cases_per_cohort") != c["strategy_cases"]:
        problems.append("parameter_domain.legal_cases_per_cohort != the registered case count")
    if list(dom.get("session_basis_grid") or []) != SESSION_BASES:
        problems.append("parameter_domain.session_basis_grid must be %r" % (SESSION_BASES,))
    if list(dom.get("entry_rule_grid") or []) != ENTRY_RULES:
        problems.append("parameter_domain.entry_rule_grid must be %r" % (ENTRY_RULES,))
    if list(dom.get("conditioning_grid") or []) != CONDITIONINGS:
        problems.append("parameter_domain.conditioning_grid must be %r" % (CONDITIONINGS,))
    if dom.get("session_contract") != SESSION_CONTRACT:
        problems.append("parameter_domain.session_contract must be the frozen adapted session "
                        "contract (slot grid, anchor, signal, entry, exit, conditioning)")
    if "historical" not in str(dom.get("selection_rule", "")).lower() \
            or "oos" not in str(dom.get("selection_rule", "")).lower():
        problems.append("parameter_domain.selection_rule must state that the winner is elected "
                        "on the historical window only and that OOS selects nothing")
    dca = spec["dca_domain"]
    for axis, values in DCA_GRID.items():
        if [float(x) for x in (dca.get(axis) or [])] != [float(v) for v in values]:
            problems.append("dca_domain.%s = %r, the registered values are %r"
                            % (axis, dca.get(axis), values))
    if dca.get("base_quote") != 1000:
        problems.append("dca_domain.base_quote must be the registered 1000 USDT constant")
    if dca.get("config_count") != c["dca_configs"]:
        problems.append("dca_domain.config_count != the registered DCA product")
    grid = dca.get("grid") or []
    if len(grid) != c["dca_configs"]:
        problems.append("dca_domain.grid must carry the complete %d-cell DCA product, got %d"
                        % (c["dca_configs"], len(grid)))
    else:
        want = set(c["dca_cells"])
        got = set(tuple(cfg[a] for a in DCA_AXES) for cfg in grid)
        if got != want:
            problems.append("dca_domain.grid is not the registered four-axis product")
        if any(cfg.get("base_quote") != 1000 for cfg in grid):
            problems.append("dca_domain.grid entries must carry base_quote=1000")
    _provenance_checks(spec, problems)
    gates = spec.get("gates") or {}
    for key, want in GATES.items():
        if gates.get(key) != want:
            problems.append("gates.%s = %r, the registered value is %r"
                            % (key, gates.get(key), want))
    data = spec.get("data") or {}
    for key, want in DATA.items():
        got = data.get(key)
        if got is None and key in ("start", "end"):
            got = data.get("data_" + key)
        if got != want:
            problems.append("data.%s = %r, the registered value is %r" % (key, got, want))
    if "immutab" not in str(data.get("split_immutability", "")).lower():
        problems.append("data.split_immutability must state that the split is frozen here")
    expected = spec.get("expected") or {}
    for key, want in (("cohorts", CANONICAL["cohorts"]),
                      ("strategy_cases_per_cohort", CANONICAL["strategy_cases"]),
                      ("dca_configs_per_cohort", CANONICAL["dca_configs"]),
                      ("base_combinations_per_cohort", CANONICAL["base_combinations_per_cohort"]),
                      ("case_evaluations_per_grid", CANONICAL["case_evaluations_per_grid"]),
                      ("expected_case_evaluations", CANONICAL["expected_case_evaluations"])):
        if expected.get(key) != want:
            problems.append("expected.%s = %r, the registered value is %r"
                            % (key, expected.get(key), want))
    if list(expected.get("cohort_grid_kinds") or []) != list(COHORT_GRID_KINDS):
        problems.append("expected.cohort_grid_kinds must be the ten registered phase grids in "
                        "the registered order")
    if str(expected.get("phase_grid_count")) != str(CANONICAL["phase_grid_count"]):
        problems.append("expected.phase_grid_count must be %d" % CANONICAL["phase_grid_count"])
    fp = spec.get("semantic_fingerprint") or {}
    if fp.get("fingerprint_input") and fp.get("semantic_fingerprint"):
        if fingerprint(fp["fingerprint_input"]) != fp["semantic_fingerprint"]:
            problems.append("semantic_fingerprint does not recompute from fingerprint_input")
    pc = spec.get("parameter_contract") or {}
    if pc.get("family_id") != spec.get("family_id"):
        problems.append("parameter_contract.family_id must equal the round-spec family_id")
    extra = {}
    if run_spec is not None:
        extra = _run_spec_checks(spec, run_spec, c, problems)
    return c, problems, extra


def _run_spec_checks(spec, run_spec, c, problems):
    """The attempt's run-spec must repeat the registration verbatim, field by field."""
    extra = {"run_spec_mismatches": []}
    if run_spec.get("family_id") != spec.get("family_id"):
        problems.append("run-spec.family_id != round-spec.family_id")
    if run_spec.get("round_id") != spec.get("round_id"):
        problems.append("run-spec.round_id != round-spec.round_id")
    rdom = run_spec.get("parameter_domain") or {}
    if [tuple(g[f] for f in CASE_FIELDS) for g in (rdom.get("grid_cases") or [])] != c["cases"]:
        problems.append("run-spec.parameter_domain.grid_cases != the registered case product")
    if rdom.get("legal_cases_per_cohort") != c["strategy_cases"]:
        problems.append("run-spec.parameter_domain.legal_cases_per_cohort != the registered "
                        "count")
    if rdom.get("session_contract") != SESSION_CONTRACT:
        problems.append("run-spec.parameter_domain.session_contract != the frozen round-spec "
                        "copy")
    rdca = run_spec.get("dca_domain") or {}
    if len(rdca.get("grid") or []) != c["dca_configs"]:
        problems.append("run-spec.dca_domain.grid must carry the complete DCA product")
    for axis in DCA_AXES:
        if _status_token(rdca, axis + "_status") != _status_token(spec["dca_domain"],
                                                                  axis + "_status"):
            problems.append("run-spec.dca_domain.%s_status != the round-spec classification "
                            "(contract 7.2 v1.3.1 forbids a conflicting provenance reading)"
                            % axis)
    if _status_token(rdca, "base_quote_status") != _status_token(spec["dca_domain"],
                                                                 "base_quote_status"):
        problems.append("run-spec.dca_domain.base_quote_status != the round-spec classification")
    for key, want in GATES.items():
        if (run_spec.get("gates") or {}).get(key) != want:
            problems.append("run-spec.gates.%s must carry the registered %r" % (key, want))
    for key in ("start", "end", "historical_start", "historical_end", "oos_start", "oos_end"):
        if (run_spec.get("data") or {}).get(key) != DATA[key]:
            problems.append("run-spec.data.%s must be the registered %r" % (key, DATA[key]))
    rexp = run_spec.get("expected") or {}
    if rexp.get("expected_case_evaluations") != CANONICAL["expected_case_evaluations"]:
        problems.append("run-spec.expected.expected_case_evaluations != %d"
                        % CANONICAL["expected_case_evaluations"])
    if rexp.get("cohorts") != CANONICAL["cohorts"]:
        problems.append("run-spec.expected.cohorts != %d" % CANONICAL["cohorts"])
    script = run_spec.get("script") or {}
    if not str(script.get("path", "")).startswith("/scripts/"):
        problems.append("run-spec.script.path must be a /scripts/... container path")
    if not str(script.get("sha256", "")).startswith("sha256:"):
        problems.append("run-spec.script.sha256 must pin the deployed runner as sha256:<hex>")
    return extra


def _main(argv):
    import argparse
    ap = argparse.ArgumentParser(description="Strategy G v1 preregistration counts")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--run-spec", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    spec = load(args.spec)
    run_spec = load(args.run_spec) if args.run_spec else None
    c, problems, extra = check(spec, run_spec)
    out = {"family_id": spec.get("family_id"), "counts": c, "problems": problems, "extra": extra}
    if args.json:
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        print("Strategy G v1 counts: %d cohorts x %d strategy cases x %d DCA = %d per grid; "
              "%d grids = %d case evaluations (%d problem(s))"
              % (c["cohorts"], c["strategy_cases"], c["dca_configs"],
                 c["case_evaluations_per_grid"], c["phase_grid_count"],
                 c["expected_case_evaluations"], len(problems)))
        for p in problems:
            print("PROBLEM: %s" % p)
    return 0 if not problems else 1


if __name__ == "__main__":
    import sys
    sys.exit(_main(sys.argv[1:]))
