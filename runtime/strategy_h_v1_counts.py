#!/usr/bin/env python3
"""Strategy H v1 pre-registration counts and provenance checks (pure stdlib).

Single job: recompute every registered count of the Markov-chain volume-price state family
(binance USD-M perpetual, 1h base grid with a causal 4h state mapping) from the declared axes,
fail closed on any mismatch with the frozen constants below, and enforce the contract's
provenance classification (section 7.2 v1.3.1): the three searched strategy axes and the four
searched DCA axes are PROJECT_PRE_REGISTERED_SEARCH_DOMAIN, the non-searched strategy
parameters and base_quote are PROJECT_PRE_REGISTERED_CONSTANT, the unrecovered execution
contract is RESEARCH_DEFINED, and only items with explicit operator evidence may sit in
USER_FIXED.  Used by the authoring/instantiation path (before anything is published) and by
the repo tests.
"""
import hashlib
import json

FAMILY_ID = "markov-chain-volume-price-state-2026-08-31"
EMPTY_MARKER = "{{"

# Registered eligible universe: the record's required data is "cryptocurrency OHLCV" tested on
# the 1h grid with the state transitions mapped from 4h resampled data, and its crypto
# portability is `direct`; the local canonical raw holds exactly these four USD-M perpetual
# contracts.  The 4h/5m/15m/30m/1d/1w grids exist in the raw but are OUTSIDE the registered
# eligible universe (the record registers 1h as its base grid), so they are excluded BEFORE
# any computation and never used to re-adjudicate suitability afterwards.
SYMBOLS = ["BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
TIMEFRAMES = [{"raw_interval": "1h", "qlib_freq": "60min"}]

# ---------------------------------------------------------------- searched strategy axes
PRICE_STATE_THRESHOLDS = [1.0, 1.5, 2.0]
SEQUENCE_LENGTHS = [1, 2, 3]
MIN_SIGNAL_PROBABILITIES = [0.5, 0.6, 0.7]
CASE_FIELDS = ["thr_1_0", "thr_1_5", "thr_2_0",
               "seq_len_1", "seq_len_2", "seq_len_3",
               "minp_0_5", "minp_0_6", "minp_0_7"]
STRATEGY_CASES = [tuple([1 if t == i else 0 for t in range(3)]
                        + [1 if l == j else 0 for l in range(3)]
                        + [1 if m == k else 0 for m in range(3)])
                  for i in range(3) for j in range(3) for k in range(3)]
CASE_NAMES = ["thr%g__L%d__p%g" % (PRICE_STATE_THRESHOLDS[i], SEQUENCE_LENGTHS[j],
                                   MIN_SIGNAL_PROBABILITIES[k])
              for i in range(3) for j in range(3) for k in range(3)]

# ---------------------------------------------------------------- DCA domain (card-fixed)
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

# The record registers no explicit no-signal / insufficient-trade threshold; the value is
# therefore a research-defined floor chosen BEFORE the first run (the record's own evidence
# reports 111 trades over five years, i.e. a very selective signal).
GATES = {"min_episodes_is": 30, "min_episodes_oos": 10,
         "neighborhood_min_same_sign_fraction": 0.6}
DATA = {"start": "2022-01-01", "end": "2026-09-11", "historical_start": "2022-01-01",
        "historical_end": "2025-09-30", "oos_start": "2025-10-01", "oos_end": "2026-09-11"}
COSTS = {"taker_fee": 0.0005, "maker_fee": 0.0002, "baseline_slippage_ticks": 1,
         "cost_attrition_fee_mult": 8.0, "cost_attrition_bps": 40}

SEARCH_STATUS = "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN"
CONSTANT_STATUS = "PROJECT_PRE_REGISTERED_CONSTANT"
RESEARCH_MARKER = "RESEARCH_DEFINED"

SEARCHED_STRATEGY_AXES = ("price_state_threshold", "sequence_length", "min_signal_probability")
CONSTANT_STRATEGY_PARAMS = ("atr_period", "volume_sma_period", "volume_high_multiplier",
                            "volume_low_multiplier", "min_row_observations")

# The record fixes the MECHANISM (12 states on price/volume, a transition matrix over state
# sequences, a probability threshold for the signal, a profitability-gated opposite-signal exit
# plus ATR-anchored stops) but none of its timing/alignment detail.  This block is the frozen
# RESEARCH_DEFINED adapted contract; every value is fixed BEFORE the first run.
MARKOV_CONTRACT = {
    "state_basis": "the 12-state discretisation is computed on COMPLETED 4h bins aligned to "
                   "00:00 UTC and built by resampling the cohort's own 1h bars (O = first "
                   "open, H = max high, L = min low, C = last close, V = sum volume); every "
                   "bin must contain exactly 4 base bars",
    "state_availability": "the state of a bin is visible only at or after that bin's close; a "
                          "base bar NEVER carries its own bin's state, it carries the most "
                          "recent COMPLETED bin state (the record's material source-code "
                          "look-ahead caveat, corrected)",
    "price_state": "r = (close - previous bin close) / atr; price_state = 0 (strong up) if "
                   "r >= price_state_threshold, 1 (mild up) if 0 <= r < threshold, 2 (mild "
                   "down) if -threshold < r < 0, 3 (strong down) if r <= -threshold",
    "atr": "Wilder ATR over atr_period = 14 completed bins: TR_i = max(high_i - low_i, "
           "|high_i - close_{i-1}|, |low_i - close_{i-1}|); atr_14 = mean(TR_1..TR_14); "
           "atr_i = (13 * atr_{i-1} + TR_i) / 14 for i > 14; a bin whose ATR is not positive "
           "or whose window is incomplete carries NO state",
    "volume_state": "vsma_i = mean(V_{i-23..i}) over the rolling 24 completed bins (the "
                    "current bin included); volume_state = 0 (low) if V_i < "
                    "volume_low_multiplier * vsma_i, 2 (high) if V_i > volume_high_multiplier "
                    "* vsma_i, 1 (normal) otherwise (equality is normal)",
    "state_index": "state = price_state * 3 + volume_state in 0..11; states 0..5 are the "
                   "BULLISH next-states (price_state 0 or 1, i.e. the next bin's close above "
                   "this one's) and states 6..11 are the BEARISH next-states",
    "sequence": "the decision sequence at base bar t is the last sequence_length COMPLETED bin "
                "states; the bin that contains t is excluded until it completes",
    "matrix": "expanding-window point-in-time counts of (sequence -> next state), grown only "
              "from transitions whose outcome bin has already completed at the decision bar; "
              "no future bar and no future bin can enter the row, which is the record's "
              "limitation 'transition-matrix training must remain strictly isolated from the "
              "evaluation window' implemented as strict causality",
    "min_row_observations": "a sequence whose row carries fewer than 5 observed transitions "
                            "yields NO signal (counted as untrained)",
    "signal": "p_bull = share of the row's transitions landing in states 0..5, p_bear = share "
              "landing in 6..11; LONG if p_bull > min_signal_probability and p_bull > p_bear, "
              "SHORT if p_bear > min_signal_probability and p_bear > p_bull, otherwise NONE "
              "(an exact tie is NONE)",
    "signal_event": "a FRESH signal (the signal at the decision bar differs from the signal at "
                    "the previous base bar) is the entry event; a persistent signal never "
                    "re-enters after a flatten",
    "entry": "the OPEN of the next base bar after the signal event (the first executable price "
             "after the signal is measurable)",
    "exit": "(1) rail take profit at running_average_cost x (1 +/- breakeven_tp_pct); (2) "
            "resting invalidation at running_average_cost x (1 -/+ invalidation_pct); (3) the "
            "record's own signal exit: an opposite signal while the position is profitable "
            "against its running average cost -> reduce-only flatten of every layer at the "
            "next base bar open; (4) slice end -> reduce-only flatten of every layer at the "
            "last bar close",
    "sizing": "the card-registered tranche rail replaces the source's equal-division sizing: "
              "tranche #1 = base_quote x leverage notional at the entry fill; adverse-price "
              "scale-ins sit at level_k = entry_fill_price x (1 -/+ spacing_pct x k) for "
              "k = 1..10, i.e. at most 11 routine active levels of the 12-tranche rail",
    "direction": "the record registers both a buy and a sell signal, so the family trades both "
                 "directions; a position is always opened by a fresh signal and is flattened "
                 "by one of the four registered exits",
    "point_in_time": "bin states, the transition matrix and the signal are all computed from "
                     "completed information only; a decision at bar t never reads a bar after t "
                     "and never reads a bin that closes after t",
    "lookahead_control": "the naive left-edge resample + backward as-of merge of the source "
                         "would attach a bin's own eventual state to the bars inside that bin; "
                         "the engine carries a registered structural counter that every "
                         "attached state was available before the bar it is attached to, and "
                         "the engine self-check red-proves the naive variant would be detected",
}

MARKOV_CONSTANTS = {"bin_hours": 4, "base_bars_per_bin": 4, "state_count": 12,
                    "bull_states": [0, 1, 2, 3, 4, 5], "bear_states": [6, 7, 8, 9, 10, 11],
                    "atr_period": 14, "volume_sma_period": 24, "volume_high_multiplier": 1.5,
                    "volume_low_multiplier": 0.5, "min_row_observations": 5,
                    "price_state_threshold_grid": PRICE_STATE_THRESHOLDS,
                    "sequence_length_grid": SEQUENCE_LENGTHS,
                    "min_signal_probability_grid": MIN_SIGNAL_PROBABILITIES}

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
    for axis in SEARCHED_STRATEGY_AXES:
        if _status_token(dom, axis + "_status") != SEARCH_STATUS:
            problems.append("parameter_domain.%s_status must start with %r (this axis IS "
                            "searched)" % (axis, SEARCH_STATUS))
    for name in CONSTANT_STRATEGY_PARAMS:
        if _status_token(dom, name + "_status") != CONSTANT_STATUS:
            problems.append("parameter_domain.%s_status must start with %r (the record names "
                            "the parameter but fixes no value; the frozen value is a project "
                            "pre-registration constant, not a searched axis)"
                            % (name, CONSTANT_STATUS))
    for field in ("markov_contract_status", "state_availability_status", "entry_timing_status",
                  "exit_timing_status", "matrix_training_status"):
        if not str(dom.get(field, "")).startswith(RESEARCH_MARKER):
            problems.append("parameter_domain.%s must declare the unrecovered execution detail "
                            "as %s" % (field, RESEARCH_MARKER))
    for name in list(DCA_AXES) + ["base_quote"] + list(CASE_FIELDS) \
            + list(SEARCHED_STRATEGY_AXES) + list(CONSTANT_STRATEGY_PARAMS):
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
        problems.append("eligible_universe.symbols must be the four USD-M perpetual contracts "
                        "of the canonical raw %r" % (SYMBOLS,))
    if uni.get("timeframes") != TIMEFRAMES:
        problems.append("eligible_universe.timeframes must register exactly the record's "
                        "tested 1h base grid")
    if "excluded" not in uni:
        problems.append("eligible_universe must register the excluded symbols/timeframes")
    dom = spec["parameter_domain"]
    if c["cases"] != [tuple(x) for x in STRATEGY_CASES]:
        problems.append("parameter_domain.grid_cases %r != the registered (threshold, sequence "
                        "length, min signal probability) product in the registered order"
                        % (c["cases"],))
    if dom.get("legal_cases_per_cohort") != c["strategy_cases"]:
        problems.append("parameter_domain.legal_cases_per_cohort != the registered case count")
    if list(dom.get("price_state_threshold_grid") or []) != PRICE_STATE_THRESHOLDS:
        problems.append("parameter_domain.price_state_threshold_grid must be %r"
                        % (PRICE_STATE_THRESHOLDS,))
    if list(dom.get("sequence_length_grid") or []) != SEQUENCE_LENGTHS:
        problems.append("parameter_domain.sequence_length_grid must be %r" % (SEQUENCE_LENGTHS,))
    if list(dom.get("min_signal_probability_grid") or []) != MIN_SIGNAL_PROBABILITIES:
        problems.append("parameter_domain.min_signal_probability_grid must be %r"
                        % (MIN_SIGNAL_PROBABILITIES,))
    if dom.get("markov_contract") != MARKOV_CONTRACT:
        problems.append("parameter_domain.markov_contract must be the frozen adapted markov "
                        "contract (state basis, availability, states, matrix, signal, entry, "
                        "exit, sizing)")
    if "historical" not in str(dom.get("selection_rule", "")).lower() \
            or "oos" not in str(dom.get("selection_rule", "")).lower():
        problems.append("parameter_domain.selection_rule must state that the winner is elected "
                        "on the historical window only and that OOS selects nothing")
    for name, want in MARKOV_CONSTANTS.items():
        got = dom.get("constants", {}).get(name)
        if got != want:
            problems.append("parameter_domain.constants.%s = %r, the registered value is %r"
                            % (name, got, want))
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
    if rdom.get("markov_contract") != MARKOV_CONTRACT:
        problems.append("run-spec.parameter_domain.markov_contract != the frozen round-spec "
                        "copy")
    if (rdom.get("constants") or {}) != (spec.get("parameter_domain") or {}).get("constants"):
        problems.append("run-spec.parameter_domain.constants != the round-spec constants")
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
    ap = argparse.ArgumentParser(description="Strategy H v1 preregistration counts")
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
        print("Strategy H v1 counts: %d cohorts x %d strategy cases x %d DCA = %d per grid; "
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
