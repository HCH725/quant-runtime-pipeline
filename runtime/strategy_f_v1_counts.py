#!/usr/bin/env python3
"""Strategy F v1 pre-registration counts and provenance checks (pure stdlib).

Single job: recompute every registered count of the Stochastic-RSI (Renko) family from the
declared axes and fail closed on any mismatch with the frozen constants below, and enforce the
contract's provenance classification (section 7.2 v1.3.1): the four searched DCA axes and the
two searched strategy axes are PROJECT_PRE_REGISTERED_SEARCH_DOMAIN, base_quote is
PROJECT_PRE_REGISTERED_CONSTANT, and only items with explicit operator evidence may sit in
USER_FIXED.  Used by the instantiator (before anything is published) and by the repo tests.
"""
import hashlib
import json
import os

FAMILY_ID = "stochastic-rsi-renko-2026-08-31"
EMPTY_MARKER = "{{"

SYMBOLS = ["BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
TIMEFRAMES = [{"raw_interval": "5m", "qlib_freq": "5min"},
              {"raw_interval": "15m", "qlib_freq": "15min"},
              {"raw_interval": "30m", "qlib_freq": "30min"},
              {"raw_interval": "1h", "qlib_freq": "60min"},
              {"raw_interval": "4h", "qlib_freq": "240min"}]
BRICK_PCT_GRID = [0.005, 0.01, 0.02]
RSI_PERIOD_GRID = [7, 14, 21]
STRATEGY_CASES = [(b, r) for b in BRICK_PCT_GRID for r in RSI_PERIOD_GRID]
DCA_AXES = ("spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct")
DCA_GRID = {"spacing_pct": [0.01, 0.02, 0.03, 0.04],
            "size_multiplier": [1.0, 1.1],
            "breakeven_tp_pct": [0.01, 0.02, 0.03],
            "invalidation_pct": [0.05, 0.10]}
STRATEGY_FIELDS = ("brick_pct", "rsi_period")
COHORT_GRID_KINDS = ("historical", "oos", "full", "fee_2x", "funding_2x", "entry_delay_1_bar",
                     "slippage_2ticks", "no_funding", "no_funding_full", "cost_attrition_40bps")
# canonical counts (contract 7.2: symbols x timeframes x strategy domain x DCA domain x grids)
CANONICAL = {"cohorts": len(SYMBOLS) * len(TIMEFRAMES),
             "strategy_cases": len(STRATEGY_CASES),
             "dca_configs": 48,
             "base_combinations_per_cohort": len(STRATEGY_CASES) * 48,
             "phase_grid_count": len(COHORT_GRID_KINDS)}
CANONICAL["case_evaluations_per_grid"] = CANONICAL["cohorts"] * CANONICAL["base_combinations_per_cohort"]
CANONICAL["expected_case_evaluations"] = (CANONICAL["case_evaluations_per_grid"]
                                          * CANONICAL["phase_grid_count"])
GATES = {"min_episodes_is": 30, "min_episodes_oos": 10,
         "neighborhood_min_same_sign_fraction": 0.6}
DATA = {"start": "2022-01-01", "end": "2026-09-11", "historical_start": "2022-01-01",
        "historical_end": "2025-09-30", "oos_start": "2025-10-01", "oos_end": "2026-09-11"}
COSTS = {"taker_fee": 0.0005, "maker_fee": 0.0002, "baseline_slippage_ticks": 1,
         "cost_attrition_fee_mult": 8.0, "cost_attrition_bps": 40}
SEARCH_STATUS = "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN"
CONSTANT_STATUS = "PROJECT_PRE_REGISTERED_CONSTANT"
RESEARCH_MARKER = "RESEARCH_DEFINED"
RENKO_CONTRACT = {"mechanism": "geometric bricks: the brick threshold is brick_pct of the "
                                "current reference price",
                  "source_price": "the source bar's CLOSE",
                  "initial_reference": "the first source bar's close",
                  "stamp": "the forming source bar's close (conservative: never earlier)",
                  "multiple_bricks_per_bar": "allowed, ladder moves monotonically toward the "
                                             "bar close",
                  "max_bricks_per_bar": 200}
INDICATOR_CONTRACT = {"rsi": "Wilder RSI on the brick close series over rsi_period bricks",
                      "stochastic": ("raw = (RSI - min(RSI, STOCH_PERIOD)) / "
                                     "(max(RSI, STOCH_PERIOD) - min(RSI, STOCH_PERIOD))"),
                      "stoch_period": 14, "k_smooth": 3, "d_smooth": 3,
                      "buy_signal": "K crosses above D (strict)",
                      "sell_signal": "K crosses below D (strict)",
                      "direction": "long AND short: the crossing flips the book"}
USER_FIXED_INVARIANTS = ("starting_equity_usdt", "numeraire", "venue", "leverage", "tranches",
                         "tranche_12", "routine_active_levels", "initial_entry_and_scale_ins",
                         "reduce_only_exit", "same_bar_multi_level_ordering",
                         "no_add_after_flat_or_kill")


def portable(path):
    return path.replace("\\", "/")


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
    cases = [tuple(g[f] for f in STRATEGY_FIELDS) for g in dom["grid_cases"]]
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


def _status_of(block, key):
    return str(block.get(key, ""))


def _provenance_checks(spec, problems):
    """Contract 7.2 v1.3.1: searched axes and project constants are never user-fixed."""
    dca = spec["dca_domain"]
    dom = spec["parameter_domain"]
    inv = spec.get("authorization_invariants", {})
    if inv.get("provenance_class") != "USER_FIXED - only items with explicit operator evidence. " \
                                     "A searched axis, a project constant, or a signal-mechanics " \
                                     "field must NOT be listed here (contract 7.2 v1.3.1).":
        problems.append("authorization_invariants.provenance_class must be the registered "
                        "USER_FIXED scope statement")
    for name in USER_FIXED_INVARIANTS:
        if name not in inv:
            problems.append("authorization_invariants is missing the user-fixed invariant %r" % name)
    if _status_token(dca, "base_quote_status") != CONSTANT_STATUS:
        problems.append("dca_domain.base_quote_status must start with %r (it is a project "
                        "pre-registration constant, not a user-fixed invariant)"
                        % CONSTANT_STATUS)
    for axis in DCA_AXES:
        if _status_token(dca, axis + "_status") != SEARCH_STATUS:
            problems.append("dca_domain.%s_status must start with %r (this axis IS searched)"
                            % (axis, SEARCH_STATUS))
    for axis in ("brick_pct", "rsi_period"):
        if _status_token(dom, axis + "_status") != SEARCH_STATUS:
            problems.append("parameter_domain.%s_status must start with %r (this axis IS "
                            "searched)" % (axis, SEARCH_STATUS))
    for field in ("entry_timing_status",):
        if not _status_of(dom, field).startswith(RESEARCH_MARKER):
            problems.append("parameter_domain.%s must declare the registered execution detail "
                            "as %s" % (field, RESEARCH_MARKER))
    for key in ("renko_contract_status", "indicator_status"):
        if not _status_of(dom, key).startswith(RESEARCH_MARKER):
            problems.append("parameter_domain.%s must declare the fixed construction/indicator "
                            "contract as %s" % (key, RESEARCH_MARKER))
    forbidden = list(DCA_AXES) + ["base_quote"] + list(STRATEGY_FIELDS)
    for name in forbidden:
        if name in inv:
            problems.append("authorization_invariants must not list the searched axis or project "
                            "constant %r as user-fixed (contract 7.2 v1.3.1)" % name)


def check(spec, run_spec=None):
    """Return (computed, problems, extra)."""
    problems = []
    c = recompute(spec)
    for key, want in (("cohorts", CANONICAL["cohorts"]),
                      ("strategy_cases", CANONICAL["strategy_cases"]),
                      ("dca_configs", CANONICAL["dca_configs"]),
                      ("base_combinations_per_cohort", CANONICAL["base_combinations_per_cohort"]),
                      ("phase_grid_count", CANONICAL["phase_grid_count"]),
                      ("case_evaluations_per_grid", CANONICAL["case_evaluations_per_grid"]),
                      ("expected_case_evaluations", CANONICAL["expected_case_evaluations"])):
        if c[key] != want:
            problems.append("recomputed %s=%r != canonical %r" % (key, c[key], want))

    uni = spec["eligible_universe"]
    if uni.get("cohort_count") != c["cohorts"]:
        problems.append("eligible_universe.cohort_count != the registered symbol x timeframe "
                        "product")
    if uni.get("symbols") != SYMBOLS:
        problems.append("eligible_universe.symbols must be the complete registered panel %r"
                        % (SYMBOLS,))
    if uni.get("timeframes") != TIMEFRAMES:
        problems.append("eligible_universe.timeframes must register exactly the five source "
                        "grids {5m,15m,30m,1h,4h}")
    if "shrink" not in json.dumps(uni.get("universe_shrinkage_disclosure", "")).lower():
        problems.append("eligible_universe.universe_shrinkage_disclosure must disclose what the "
                        "raw store does and does not contain")

    dom = spec["parameter_domain"]
    if c["cases"] != [tuple(x) for x in STRATEGY_CASES]:
        problems.append("parameter_domain.grid_cases %r != the registered brick_pct x "
                        "rsi_period product in the registered order" % (c["cases"],))
    if dom.get("legal_cases_per_cohort") != c["strategy_cases"]:
        problems.append("parameter_domain.legal_cases_per_cohort != the registered case count")
    if [float(x) for x in (dom.get("brick_pct_grid") or [])] != BRICK_PCT_GRID:
        problems.append("parameter_domain.brick_pct_grid must be the registered grid %r"
                        % (BRICK_PCT_GRID,))
    if [int(x) for x in (dom.get("rsi_period_grid") or [])] != RSI_PERIOD_GRID:
        problems.append("parameter_domain.rsi_period_grid must be the registered grid %r"
                        % (RSI_PERIOD_GRID,))
    if dom.get("renko_contract") != RENKO_CONTRACT:
        problems.append("parameter_domain.renko_contract must be the frozen construction "
                        "contract (brick size, source price, stamp, multi-brick handling)")
    if dom.get("indicator_contract") != INDICATOR_CONTRACT:
        problems.append("parameter_domain.indicator_contract must be the frozen Stochastic-RSI "
                        "contract (periods, smoothing, crossing rule, direction)")
    if "historical" not in str(dom.get("selection_rule", "")).lower() \
            or "oos" not in str(dom.get("selection_rule", "")).lower():
        problems.append("parameter_domain.selection_rule must state that the winner is elected "
                        "on the historical window only and that OOS selects nothing")

    dca = spec["dca_domain"]
    for axis, values in DCA_GRID.items():
        got = [float(x) for x in (dca.get(axis) or [])]
        if got != [float(v) for v in values]:
            problems.append("dca_domain.%s = %r, the registered values are %r"
                            % (axis, got, values))
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
        for cfg in grid:
            if cfg.get("base_quote") != 1000:
                problems.append("dca_domain.grid entries must carry base_quote=1000")
                break

    _provenance_checks(spec, problems)

    gates = spec.get("gates") or {}
    for key, want in GATES.items():
        if gates.get(key) != want:
            problems.append("gates.%s = %r, the registered value is %r" % (key, gates.get(key), want))
    data = spec.get("data") or {}
    for key, want in DATA.items():
        got = data.get(key)
        # the round-spec names the window data_start/data_end (Strategy A-E convention) while the
        # run-spec names it start/end; both must carry the registered value
        if got is None and key in ("start", "end"):
            got = data.get("data_" + key)
        if got != want:
            problems.append("data.%s = %r, the registered value is %r" % (key, got, want))
    if "must not be moved" not in str(data.get("split_immutability", "")).lower() \
            and "immutab" not in str(data.get("split_immutability", "")).lower():
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
    if [tuple(g[f] for f in STRATEGY_FIELDS) for g in (rdom.get("grid_cases") or [])] != c["cases"]:
        problems.append("run-spec.parameter_domain.grid_cases != the registered case product")
    if rdom.get("legal_cases_per_cohort") != c["strategy_cases"]:
        problems.append("run-spec.parameter_domain.legal_cases_per_cohort != the registered count")
    if rdom.get("renko_contract") != RENKO_CONTRACT:
        problems.append("run-spec.parameter_domain.renko_contract != the frozen round-spec copy")
    if rdom.get("indicator_contract") != INDICATOR_CONTRACT:
        problems.append("run-spec.parameter_domain.indicator_contract != the frozen round-spec copy")
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
    if run_spec.get("selector_version") != spec.get("selector_and_disposition", {}).get(
            "selector_version", run_spec.get("selector_version")):
        problems.append("run-spec.selector_version != round-spec selector version")
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
    ap = argparse.ArgumentParser(description="Strategy F v1 preregistration counts")
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
        print("Strategy F v1 counts: %d cohorts x %d strategy cases x %d DCA = %d per grid; "
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
