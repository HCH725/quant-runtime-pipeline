#!/usr/bin/env python3
"""Authoring source of the Strategy G v1 templates (round-spec + run-spec).

Writes runtime/templates/strategy_g_v1_{round,run}_spec.template.json.  Every scientific field
comes from runtime/strategy_g_v1_counts.py, so the templates and the counts validator can never
drift apart; the two templates are generated together (one source of truth).  Only the three
registered round-spec placeholders ({{round_id}}, {{task_id}}, {{created_at_utc}}) and the
fingerprint text are ever substituted at instantiation.

usage: python3 runtime/_author_strategy_g_template.py [--family-json <path>] [--check]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import strategy_g_v1_counts as C  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATES = os.path.join(HERE, "templates")
FAMILY_ID = C.FAMILY_ID
GRID_CASES = [{f: int(v) for f, v in zip(C.CASE_FIELDS, case)} for case in C.STRATEGY_CASES]
DCA_CELLS = [dict(zip(C.DCA_AXES, cell), base_quote=1000)
             for cell in C.product([C.DCA_GRID[a] for a in C.DCA_AXES])]
INSTRUMENTS = {
    "BTCUSDT": {"id": "BTCUSDT-PERP.BINANCE", "type": "CryptoPerpetual",
                "base_currency": "BTC", "quote_currency": "USDT", "settlement_currency": "USDT",
                "is_inverse": False, "price_increment": 0.10, "size_increment": 0.001,
                "lot_size": "0.001", "maker_fee": 0.0002, "taker_fee": 0.0005,
                "margin_init": 0.1, "margin_maint": 0.1, "min_notional": "50.00000000 USDT",
                "max_quantity": "1000", "multiplier": "1"}
}
CASE_NAMES = list(C.CASE_NAMES)


def _chain():
    return ("the record's signal semantics (activity-defined session; first half-hour return "
            "forecasts the last half-hour return) are preserved verbatim; the session "
            "construction, the entry/exit timestamps and the conditioning window are the "
            "details the record itself lists as NOT recoverable and are therefore registered "
            "here as an explicitly RESEARCH_DEFINED adapted contract, chosen before any "
            "computation and never adjusted afterwards")


def round_spec(fp_input, fp_value):
    return {
        "schema_version": 1,
        "document_kind": "round-spec (instantiated production pre-registration; every "
                         "scientific field is copied verbatim from the frozen "
                         "strategy_g_v1_round_spec.template.json and only the three registered "
                         "placeholders, the TO_BE_RECOMPUTED fingerprint and the template-era "
                         "implementation-status prose were substituted)",
        "template_instantiation": {
            "placeholders": ["{{round_id}}", "{{kanban_task_id}}", "{{created_at_utc}}"],
            "rule": "Every scientific field in this template is FROZEN before the first run. "
                    "Instantiation may only substitute the three placeholders; it must not touch "
                    "the domains, the split, the selector/disposition versions, the gates, the "
                    "registered family-level falsification landings or the falsification list "
                    "(contract INV-4).",
            "instantiated_to": "/results/<family_id>/rounds/<round_id>/round-spec.json",
            "validated_by": "python3 runtime/strategy_g_v1_counts.py --spec "
                            "runtime/templates/strategy_g_v1_round_spec.template.json",
            "status": "PREREGISTRATION ONLY / NOT LAUNCHED until the production card "
                      "instantiates it"},
        "family_id": FAMILY_ID,
        "family_title": "Production candidate - Bitcoin intraday time-series momentum on "
                        "volume-anchored sessions (BTCUSDT USD-M perp; adapted portability)",
        "round_id": "{{round_id}}",
        "kanban_task_id": "{{kanban_task_id}}",
        "kanban_board": "quant-strategy-research",
        "created_at_utc": "{{created_at_utc}}",
        "authored_by": "Hermes default (Xiaoqian) - contract 14.4 production handoff card",
        "contract": "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md v1.9.0 (cohort survivor "
                    "semantics of sections 7.2 / 7.3; the v1.8 launch gate additionally requires "
                    "this document's generic parameter_contract, which is generated below from "
                    "the domains registered here)",
        "semantic_fingerprint": {
            "fingerprint_input": fp_input,
            "semantic_fingerprint": fp_value,
            "normalisation": "The frozen handoff value (contract 14.2 step 7 / 14.4): family_id "
                             "| universe portability token | DCA domain (axes alphabetically) | "
                             "window | selector;disposition version | source. It was written "
                             "into the immutable /results family.json by the automatic "
                             "production handoff and is copied here VERBATIM; this template "
                             "never rewrites it.",
        },
        "parent_family": None,
        "lineage_note": "Independent family canonicalized during the contract-14.4 backlog "
                        "backfill (card t_86d04b09) from the already-reviewed wiki record "
                        "quant/bitcoin-intraday-time-series-momentum-volume-session-2026-08-31.md. "
                        "Kanban parent edge is scheduling/order only; scientific lineage is null: "
                        "parent_family=null - this family does not refine, inherit parameters "
                        "from, or shrink the universe of any earlier family.",
        "provenance": {
            "record": "wiki brain quant/bitcoin-intraday-time-series-momentum-volume-session-2026-08-31.md",
            "record_status": "research-only / implementation_status=not-implemented / "
                             "adoption=not-approved / approval_scope=research-only",
            "primary_sources": ["https://doi.org/10.1111/fire.12290",
                                "https://research.birmingham.ac.uk/en/publications/bitcoin-intraday-time-series-momentum/"],
            "portability": "the record's own crypto portability line: Direct for Bitcoin spot if "
                           "the original source specification can be recovered; Adapted / "
                           "unproven for Bitcoin perpetual futures. The original source "
                           "specification (venue, sample, session construction) is NOT "
                           "recoverable from the accessible sources, so this round is the "
                           "registered ADAPTED port: a single liquid Bitcoin perpetual "
                           "(BINANCE USD-M BTCUSDT) with a pre-registered, explicitly disclosed "
                           "session contract.",
            "adapted_port_disclosure": _chain(),
        },
        "hypothesis": {
            "statement": "Within a 24/7 Bitcoin market the meaningful intraday session is the "
                         "one identified by TRADING ACTIVITY (volume as the proxy for market "
                         "trading time): the session's first half-hour return predicts its last "
                         "half-hour return in the same direction.",
            "mechanism": "The record reports the effect as an intraday persistence phenomenon "
                         "concentrated around elevated market participation, attributed mainly "
                         "to liquidity provision rather than late-informed trading, with "
                         "stronger economic gains during Bitcoin downturns.",
            "direction": "SIGN of the first-half-hour return: R_first > 0 -> LONG, R_first < 0 -> "
                         "SHORT; on a perpetual both legs are tradable, so the registered book "
                         "is two-sided exactly as the record's sign rule states. R_first == 0 -> "
                         "no episode.",
            "source_note": "the record's five normalized steps are preserved; what this round "
                           "adds is the operational session contract it declares unrecoverable",
            "test_statistic": "the single registered cohort set (BTCUSDT x {5m,15m,30m}): the "
                              "deterministic historical-only winner cell of each cohort is "
                              "carried unchanged into OOS, the full window, the four execution "
                              "stress grids and its historical parameter neighbourhood. The "
                              "family statistic is the COUNT of cohort survivors (0..3), never "
                              "a cross-cohort median.",
        },
        "eligible_universe": {
            "symbols": list(C.SYMBOLS),
            "symbol_source": "BINANCE USD-M perpetual, canonical raw store (/data/raw, "
                             "read-only)",
            "timeframes": [dict(tf) for tf in C.TIMEFRAMES],
            "cohort_count": C.CANONICAL["cohorts"],
            "cohort_definition": "one cohort = one (symbol, base grid) pair; the base grid is the "
                                 "bar series from which the half-hour slots, the anchor profile "
                                 "and the session boundaries are measured. It is the v1.3.0 "
                                 "disposition unit; all three cohorts are always reported "
                                 "separately.",
            "instruments_metadata_measured_at_pre_registration": INSTRUMENTS,
            "universe_shrinkage_disclosure": "No symbol shrinkage: the record studies Bitcoin "
                                             "and the machine holds one Bitcoin instrument. The "
                                             "timeframe set is {5m,15m,30m} - the base grids "
                                             "that resolve the half-hour slot exactly; 1h/4h/"
                                             "1d/1w are present in raw but excluded, because a "
                                             "grid coarser than 30 minutes cannot resolve the "
                                             "session's first and last half hour. The choice is "
                                             "registered before any computation and must not be "
                                             "narrowed or widened afterwards.",
            "excluded": ["1h, 4h, 1d, 1w (coarser than the 30 min session slot)",
                         "every symbol other than the record's Bitcoin instrument"],
            "universe_immutability": "Registered here, before computation. Must not be narrowed "
                                     "or widened after seeing results (contract 7.2).",
        },
        "parameter_domain": {
            "kind": "strategy parameter domain (the registered session-case set), a composite "
                    "axis over (session basis, entry rule, conditioning)",
            "case_definition": "a strategy case is (session_basis, entry_rule, conditioning): "
                               "the trailing window of the activity anchor, which of the two "
                               "registered entry timestamps is used, and whether the "
                               "unconditional session set or the high-volatility subset is "
                               "traded",
            "grid_cases": GRID_CASES,
            "grid_case_names": CASE_NAMES,
            "legal_cases_per_cohort": C.CANONICAL["strategy_cases"],
            "session_basis_grid": list(C.SESSION_BASES),
            "session_basis_status": C.SEARCH_STATUS + " - this axis IS searched over the "
                                                      "registered trailing windows (7 / 30 / 90 "
                                                      "days), the record's own timezone/session "
                                                      "sensitivity requirement.",
            "entry_rule_grid": list(C.ENTRY_RULES),
            "entry_rule_status": C.SEARCH_STATUS + " - this axis IS searched over the two "
                                                   "registered entry timestamps (the record "
                                                   "lists the exact entry timestamp as "
                                                   "unrecovered).",
            "conditioning_grid": list(C.CONDITIONINGS),
            "conditioning_status": C.SEARCH_STATUS + " - this axis IS searched over the "
                                                     "unconditional set and the pre-session "
                                                     "high-volatility subset (the record reports "
                                                     "the effect is strongest there and its "
                                                     "falsification battery requires the "
                                                     "subgroup to beat a predeclared "
                                                     "unconditional baseline).",
            "session_contract": C.SESSION_CONTRACT,
            "session_contract_status": C.RESEARCH_MARKER + " - the record requires an "
                                                            "activity-defined session and the "
                                                            "crypto adaptation to fix candle "
                                                            "boundaries and timezone rules "
                                                            "before evaluation, but fixes no "
                                                            "concrete construction; this "
                                                            "contract is registered once, "
                                                            "applied identically to all three "
                                                            "cohorts, and never adjusted after "
                                                            "seeing results.",
            "entry_timing_status": C.RESEARCH_MARKER + " - the signal is complete at the entry "
                                                       "instant (the entry bar opens exactly at "
                                                       "the registered boundary) and no path "
                                                       "reads a future bar; the "
                                                       "entry_delay_1_bar stress re-measures "
                                                       "the whole product one bar later.",
            "exit_timing_status": C.RESEARCH_MARKER + " - the exit is a hard session-end "
                                                      "boundary; a session whose window is not "
                                                      "fully inside the evaluated slice is "
                                                      "skipped and counted, never truncated "
                                                      "into a fake time exit.",
            "selection_rule": "The cohort winner is elected on the HISTORICAL window only "
                              "(deterministic selector, contract 7.3); OOS selects nothing and "
                              "is never read by the selector or the neighbourhood test.",
        },
        "dca_domain": {
            "kind": "DCA parameter domain (the execution rail), four axes - contract 7.2 item 4",
            "base_quote": 1000,
            "base_quote_status": C.CONSTANT_STATUS + " - held constant across the whole domain "
                                                     "(it is NOT a search axis) and NOT a "
                                                     "user-fixed invariant: no operator evidence "
                                                     "fixes it at 1000. It is a project "
                                                     "pre-registration choice, frozen here "
                                                     "before any computation (contract 7.2 "
                                                     "v1.3.1).",
            "spacing_pct": list(C.DCA_GRID["spacing_pct"]),
            "spacing_pct_status": C.SEARCH_STATUS + " - this axis IS searched over the registered "
                                                    "values; it is a pre-registered search "
                                                    "DOMAIN of this round, not an immutable "
                                                    "user-fixed invariant (contract 7.2 v1.3.1).",
            "size_multiplier": list(C.DCA_GRID["size_multiplier"]),
            "size_multiplier_status": C.SEARCH_STATUS + " - this axis IS searched over the "
                                                        "registered values (1.1 is the "
                                                        "historical progression value and a "
                                                        "search candidate here, not an "
                                                        "invariant).",
            "breakeven_tp_pct": list(C.DCA_GRID["breakeven_tp_pct"]),
            "breakeven_tp_pct_status": C.SEARCH_STATUS + " - this axis IS searched over the "
                                                         "registered values.",
            "invalidation_pct": list(C.DCA_GRID["invalidation_pct"]),
            "invalidation_pct_status": C.SEARCH_STATUS + " - this axis IS searched over the "
                                                         "registered values.",
            "config_count": C.CANONICAL["dca_configs"],
            "grid": DCA_CELLS,
            "grid_note": "the complete 4 x 2 x 3 x 2 = 48-cell product; every cell is evaluated "
                         "with true order/fill accounting on every registered phase grid",
        },
        "dca_execution_semantics": {
            "authorization": "Inherited verbatim from the Strategy A/B engine family (the ladder "
                             "geometry, breakeven-anchored TP and resting invalidation "
                             "semantics); this round parametrises the four axes and bounds the "
                             "rail by the session window - it does not re-interpret the "
                             "execution semantics.",
            "trigger_anchoring": "the LADDER is anchored on the INITIAL ENTRY FILL PRICE of the "
                                 "episode",
            "invalidation_anchoring": "the resting invalidation is anchored on the running "
                                      "AVERAGE COST, the same reference as the "
                                      "breakeven-anchored TP",
            "window_bounded": "tranche #1 is the initial entry at the registered entry bar; "
                              "adverse-price scale-ins follow the ladder inside the session "
                              "(level_k price = initial_entry_price x (1 -/+ spacing_pct x k), "
                              "k = 1..10, so at most 11 routine active levels of the 12-tranche "
                              "rail); the session end reduce-only flattens every layer, then the "
                              "book is FLAT and no layer may be added again before the next "
                              "qualifying session",
            "engine_semantics": {
                "entry": "the OPEN of the registered entry bar, taker fill with adverse "
                         "slippage",
                "sizing": "non-compounding fixed-quote basis on the pre-registered starting "
                          "equity: level k margin = base_quote x size_multiplier^k (k = 0..10); "
                          "level notional = margin x leverage",
                "take_profit": "reduce-only full flatten of the whole episode when the bar "
                               "high/low reaches running_average_cost x (1 +/- "
                               "breakeven_tp_pct)",
                "invalidation": "a RESTING stop at running_average_cost x (1 -/+ "
                                "invalidation_pct), anchored on the same reference as the TP",
                "intrabar_ordering": "deterministic and conservative: a long episode walks the "
                                     "bar LOW first and then the HIGH; a short episode mirrors "
                                     "it (HIGH first). A ladder level below (above) the resting "
                                     "stop of a long (short) can never fill, and a gap through "
                                     "the stop fills at the bar open.",
                "exit": "hard session-end flatten of every layer at the session's last bar "
                        "close, reduce-only",
                "capital_exhaustion": "no episode is opened once the realised equity is gone; "
                                      "the guard is re-evaluated after every fill and against "
                                      "the equity the entry fee itself will leave behind",
                "non_overlap": "a session whose entry bar falls inside an already open episode is "
                               "skipped and counted; the counter can never be negative",
            },
        },
        "selector_and_disposition": {
            "selector_version": "cohort-selector-v1",
            "disposition_version": "cohort-disposition-v1",
            "selection_protocol": "Deterministic, historical window ONLY; OOS must never be used "
                                  "to select parameters.",
            "selection_steps": [
                "1. Sufficiency: if the cohort's best historical case has fewer episodes than "
                "gates.min_episodes_is, the cohort is culled with reason insufficient_trades.",
                "2. Eligibility: a case is a candidate iff historical net_pnl > 0 AND historical "
                "sharpe > 0 AND historical episodes >= gates.min_episodes_is.",
                "3. If no case is a candidate, the cohort is culled with reason "
                "no_qualifying_candidate.",
                "4. Ranking: Sharpe descending, then net_pnl descending, then a fixed lexical "
                "tie-break over the REGISTERED INDEX of each axis in the fixed order "
                "(window_case, spacing_pct, size_multiplier, breakeven_tp_pct, invalidation_pct). "
                "The first case is the cohort winner.",
                "5. The winner's exact strategy+DCA cell is carried unchanged into OOS, full, "
                "every robustness grid and the neighbourhood test. No cell may be swapped after "
                "OOS has been seen."],
            "cohort_survivor_requirements": [
                "a) a historical winner exists (steps 1-4)",
                "b) OOS: net_pnl > 0 AND sharpe > 0 for the winner's cell",
                "c) full window: net_pnl > 0 for the winner's cell",
                "d) robustness: the SAME winner cell has net_pnl > 0 in each of fee_2x, "
                "funding_2x, entry_delay_1_bar, slippage_2ticks",
                "e) parameter neighbourhood: at least 60% of the winner cell's legal "
                "face-adjacent neighbours in the registered joint space agree with the winner's "
                "HISTORICAL net-PnL sign"],
            "cull_reasons_vocabulary": ["insufficient_trades", "no_qualifying_candidate",
                                        "oos_economic", "full_economic",
                                        "robustness_economic:<grids>",
                                        "parameter_neighbourhood"],
            "family_disposition_rule": "0 cohort survivors -> verdict REJECT "
                                       "(performance_claimable=false); >=1 survivors -> verdict "
                                       "PASS (band SURVIVOR_FOUND / MULTIPLE_SURVIVORS describes "
                                       "the count only; every survivor is kept and advances); "
                                       "coverage or technical incompleteness -> "
                                       "TECHNICAL_INCOMPLETE. The survivor count is never a "
                                       "reason for performance_claimable=false (contract 9.6 is "
                                       "the only reference).",
            "runner_role": "the runner's disposition / verdict_recommendation are RECOMMENDATIONS; "
                           "the final verdict is written by default into verdict.json per "
                           "contract 10.7.",
        },
        "registered_family_level_falsification": {
            "note": "Four FAMILY-level readers plus one explicitly non-evaluable record item, "
                    "registered before the first run. They never cull a cohort by themselves; a "
                    "reader hit must NEVER be recorded as PASS (the runner recommends DEFERRED "
                    "and the evidence decides the final verdict).",
            "session-definition-instability": {
                "definition": "for every elected winner, the OOS net-PnL signs over the THREE "
                              "registered session bases (entry rule, conditioning and DCA config "
                              "held at the winner's) do not agree",
                "record_item": "the record's falsification item 'results depend entirely on one "
                               "timezone/session definition selected after observing outcomes'",
                "landing": "family-level: a hit must NEVER be recorded as PASS"},
            "entry-rule-instability": {
                "definition": "for every elected winner, the OOS net-PnL signs of the two "
                              "registered entry rules (basis, conditioning and DCA config held "
                              "at the winner's) do not agree",
                "record_item": "the record lists the exact entry/exit timestamps as unrecovered, "
                               "so a sign that flips with the entry convention is a registered "
                               "fragility",
                "landing": "family-level: a hit must NEVER be recorded as PASS"},
            "cost-boundary": {
                "definition": "for every elected winner, the FULL-window net-PnL sign under the "
                              "canonical cost track differs from its sign under the registered "
                              "40 bps-per-fill attrition grid",
                "record_item": "the record's falsification item 'the effect disappears after "
                               "realistic fees, spread, and slippage'",
                "landing": "family-level: a hit must NEVER be recorded as PASS"},
            "conditioning-vs-unconditional-baseline": {
                "definition": "at every elected winner's session basis, entry rule and DCA "
                              "config, the OOS net PnL of the registered high_vol_only case "
                              "does NOT exceed the OOS net PnL of its all_sessions sibling",
                "record_item": "the record's falsification item 'the high-volume/high-volatility "
                               "subgroup does not outperform a predeclared unconditional "
                               "baseline'",
                "landing": "family-level: a hit must NEVER be recorded as PASS"},
            "venue-replication": {
                "definition": "the record's item 'the result is confined to one venue and fails "
                              "across major liquid Bitcoin venues'",
                "evaluated": False,
                "reason": "the canonical raw store holds BINANCE USD-M perpetuals only; no second "
                          "venue exists in this machine's raw, so venue replication is not "
                          "evaluable in this round and is disclosed as an untested limitation",
                "landing": "never PASS-bearing, never a cull"},
        },
        "gates": {
            "G1_coverage": "every one of the 10 registered phase grids must contain exactly "
                           "1728 cases (3 cohorts x 12 strategy cases x 48 DCA configs), the "
                           "measured strategy/DCA cell sets must equal the registered products, "
                           "and the case-evaluation total must equal 17280; otherwise "
                           "TECHNICAL_INCOMPLETE and no cohort is judged",
            "G2_insufficient_trades": "per cohort: fewer than min_episodes_is (100) historical "
                                      "episodes in the cohort's best case, or fewer than "
                                      "min_episodes_oos (25) episodes in the elected winner's "
                                      "OOS slice, culls the cohort (insufficient_trades)",
            "G3_cohort_selector": "historical net_pnl > 0 AND sharpe > 0 AND episodes >= 100, "
                                  "ranked by Sharpe desc, net_pnl desc, registered-index lexical "
                                  "tie-break",
            "G4_cohort_oos": "OOS net_pnl > 0 AND OOS sharpe > 0 for the frozen winner cell",
            "G5_cohort_full": "full-window net_pnl > 0 for the frozen winner cell",
            "G6_cohort_robustness": "the winner cell has net_pnl > 0 in all four execution stress "
                                    "reruns",
            "G7_cohort_neighbourhood": "at least 60% of legal face-adjacent neighbours agree in "
                                       "historical net-PnL sign",
            "verdict_rule": "TECHNICAL_INCOMPLETE if G1 fails; else each cohort passing G3-G7 is "
                            "a SURVIVOR: 0 survivors -> REJECT, >=1 survivors -> PASS (band "
                            "SURVIVOR_FOUND / MULTIPLE_SURVIVORS describes the count only). The "
                            "four registered family-level readers can only move a PASS to "
                            "DEFERRED, never to PASS.",
            "min_episodes_is": C.GATES["min_episodes_is"],
            "min_episodes_is_provenance": "RESEARCH_DEFINED - the record registers no no-signal "
                                          "floor; this round registers 100 historical episodes "
                                          "for the selection window and 25 for the OOS slice "
                                          "before the first run and never lowers them.",
            "min_episodes_oos": C.GATES["min_episodes_oos"],
            "neighborhood_min_same_sign_fraction": C.GATES["neighborhood_min_same_sign_fraction"],
            "cost_attrition_role": "the 40 bps cost_attrition_40bps grid is registered as a "
                                   "family-level reader (cost-boundary), NOT as an extra cohort "
                                   "cull: the card's registered survivor requirements are the "
                                   "four execution stress grids, and this round does not add a "
                                   "stricter cull that the registration did not ask for.",
            "known_gate_set_limitation": "the registered gate set contains no benchmark-relative "
                                         "test and no buy-and-hold comparison; a two-sided "
                                         "session book can show positive PnL in a trending "
                                         "sample through posture alone. This limitation is "
                                         "disclosed and is not repaired inside this round.",
        },
        "metrics_definitions": {
            "equity": "30000 + cumulative realised net PnL (USDT); unrealised marked at bar "
                      "closes",
            "daily_series": "end-of-UTC-day equity marks, forward-filled across flat stretches",
            "sharpe": "mean/std (ddof=1) of daily returns x sqrt(365); risk-free 0",
            "max_dd": "peak-to-trough of the daily equity series, reported in % and USDT",
            "cagr": "(ending_equity / 30000) ^ (1/years) - 1, years from the actual bar span",
            "max_effective_leverage": "max of (position notional / unrealised equity), sampled "
                                      "every in-market bar",
            "capital_utilization": "mean of (deployed margin / unrealised equity) over "
                                   "in-market bars",
            "turnover": "sum of |fill notional| over every entry / DCA add / exit fill of the "
                        "cell",
            "dca_layer_histogram": "count of fills per ladder level, aggregated over all "
                                   "full-window base cases; direct evidence of how much of the "
                                   "12-tranche rail is reachable under resting-stop sequencing",
            "cohort_winner": "the historical-only selector output for each (symbol, base grid) "
                             "cohort",
        },
        "robustness_plan": {
            "declared_before_first_run": True,
            "must_not_narrow_universe_or_lower_gates": True,
            "items": [
                {"id": "R1", "name": "parameter-neighbourhood robustness",
                 "method": "for each cohort winner, compare the winner's historical net-PnL sign "
                           "against its legal face-adjacent neighbours in the registered joint "
                           "space (window_case, spacing_pct, size_multiplier, breakeven_tp_pct, "
                           "invalidation_pct); require >= 60% sign agreement; historical rows "
                           "only"},
                {"id": "R2", "name": "execution stress reruns",
                 "method": "full-window evaluation of all registered cells under fee_2x, "
                           "funding_2x, entry_delay_1_bar, slippage_2ticks; the winner's cell "
                           "must stay net_pnl > 0 in all four"},
                {"id": "R3", "name": "funding baseline diagnostics",
                 "method": "no_funding (historical window) and no_funding_full (full window) "
                           "grids for all cells; descriptive only, never a gate"},
                {"id": "R4", "name": "cost attrition (registered reader)",
                 "method": "cost_attrition_40bps grid for all cells; the winner's full-window "
                           "net-PnL sign is compared with the canonical track as the "
                           "family-level cost-boundary reader"},
                {"id": "R5", "name": "session-definition and entry-rule stability",
                 "method": "the OOS net-PnL signs across the three registered session bases and "
                           "across the two registered entry rules at the winner's remaining "
                           "parameters (family-level readers; never PASS)"},
                {"id": "R6", "name": "conditioning-vs-unconditional baseline",
                 "method": "the OOS net PnL of the high_vol_only case must exceed its "
                           "all_sessions sibling at the winner's remaining parameters "
                           "(family-level reader; never PASS)"},
            ],
        },
        "data": {
            "source": "/data/raw (host <EXPANSION>/market-data-raw), read-only",
            "venue": "BINANCE USD-M perpetual",
            "data_start": C.DATA["start"],
            "data_end": C.DATA["end"],
            "historical_start": C.DATA["historical_start"],
            "historical_end": C.DATA["historical_end"],
            "oos_start": C.DATA["oos_start"],
            "oos_end": C.DATA["oos_end"],
            "split_immutability": "The split is immutable and fixed here, before any "
                                  "computation; it must not be moved afterwards.",
            "derived_store": "Built per attempt from raw into the rebuildable /qlib/work area via "
                             "upstream qlib dump_bin.py; read back exclusively through the qlib "
                             "data layer. Never written to /results. Bars are never resampled or "
                             "gap-filled: a missing bar stays missing and fails the run closed.",
            "bar_labelling": {
                "raw_field": "open_time_ms",
                "status": "VERIFIED at pre-registration on the canonical raw store",
                "measured_at_pre_registration": {
                    "symbol": "BTCUSDT",
                    "grids": {"5m": {"bars_in_window": 493920, "expected_if_contiguous": 493920,
                                     "off_grid_steps": 0, "missing_bars_total": 0,
                                     "close_time_delta_ms": 299999},
                              "15m": {"bars_in_window": 164640, "expected_if_contiguous": 164640,
                                      "off_grid_steps": 0, "missing_bars_total": 0,
                                      "close_time_delta_ms": 899999},
                              "30m": {"bars_in_window": 82320, "expected_if_contiguous": 82320,
                                      "off_grid_steps": 0, "missing_bars_total": 0,
                                      "close_time_delta_ms": 1799999}},
                    "first_bar_open_utc": "2022-01-01T00:00:00Z",
                    "last_bar_close_utc": "2026-09-11T23:59:59Z",
                    "finding": "open_time_ms is the bar START in UTC and every registered grid "
                               "is exactly contiguous over the window (zero gaps, zero "
                               "off-grid steps), so the slot/bar mapping is exact and the "
                               "engine asserts it at run time"},
                "consequence": "the anchor slot, the first half hour and the session end all map "
                               "to the bar that OPENS at the measured boundary; no boundary is "
                               "inferred from a label",
            },
            "funding_series_contract": {
                "registered_window": "2022-01-01T00:00:00Z .. 2026-09-11T23:59:59Z",
                "measured_at_pre_registration": {
                    "observations": 5145, "modeled_funding": 2005, "official": 3140,
                    "modelled_first": "2022-01-01T00:00:00Z",
                    "modelled_last": "2023-10-31T00:00:00Z",
                    "official_first": "2023-10-31T08:00:00Z",
                    "official_last": "2026-09-11T16:00:00Z",
                    "interval": "strict 8 h grid with ms-level timestamp jitter only"},
                "engine_rule": "the position is exposed to every settlement whose raw instant "
                               "lies in the CLOSED interval [entry instant - 1 s, exit instant "
                               "+ 1 s]; each settlement is charged at its own instant on the "
                               "position notional at that instant",
                "no_fabrication": "the artifacts disclose the modelled/official split; no "
                                  "modelled row may be relabelled official",
            },
        },
        "costs": {
            "source": "canonical instrument metadata (raw binance/usdm/instruments/"
                      "usdm-perp-instruments.json), read at run time, never hard-coded",
            "maker_fee": C.COSTS["maker_fee"],
            "taker_fee": C.COSTS["taker_fee"],
            "margin_init": 0.1,
            "margin_maint": 0.1,
            "apply_rule": "every fill is a market order: taker fee on the full traded notional, "
                          "plus adverse slippage of baseline_slippage_ticks instrument ticks "
                          "(1 tick = price_increment). No maker fills are assumed anywhere.",
            "baseline_slippage_ticks": C.COSTS["baseline_slippage_ticks"],
            "baseline_slippage_provenance": "research assumption of this round (NOT "
                                            "user-specified); robustness re-measures it at 2 "
                                            "ticks",
            "funding": "the registered funding series is charged at each settlement inside the "
                       "session's exposure interval (rate x position notional at that instant); "
                       "a negative rate is a CREDIT to a long position and a COST to a short. The "
                       "no_funding / no_funding_full grids re-measure the same cells with the "
                       "funding cost zeroed (descriptive), and funding_2x doubles it.",
            "funding_exposure_rule": "The position is exposed to every funding settlement whose "
                                     "raw instant lies in the CLOSED interval [entry instant - "
                                     "1 s, exit instant + 1 s]. The measured raw jitter of the "
                                     "settlement timestamps is 0..28 ms LATE and never early, so "
                                     "a settlement sitting on a session boundary is charged to "
                                     "the window that holds the position at that instant. A "
                                     "settlement is charged on the position notional at the "
                                     "moment of the charge: the close of the bar that contains "
                                     "it inside the session, or the episode's exit fill price "
                                     "when it lands at the closing boundary. The sign follows the "
                                     "position. Settlements outside the closed interval are "
                                     "never charged.",
            "cost_attrition_grid": "cost_attrition_40bps applies 8x the registered taker fee = 40 "
                                   "bps per fill, the registered attrition stress of the "
                                   "original falsification battery",
            "funding_immutability": "the funding baseline must not be swapped for a modelled "
                                    "series to improve the outcome, nor vice versa",
        },
        "authorization_invariants": {
            "provenance_class": "USER_FIXED - only items with explicit operator evidence (the "
                                "operator's stated rail definition, inherited verbatim from the "
                                "A/B registration). A searched axis, a project constant, or a "
                                "signal-mechanics field must NOT be listed here (contract 7.2 "
                                "v1.3.1).",
            "starting_equity_usdt": 30000,
            "numeraire": "USDT (sole)",
            "venue": "BINANCE USD-M linear perpetual",
            "leverage": "10x (1 / margin_init)",
            "tranches": 12,
            "tranche_12": "reserve/buffer, never routinely deployed",
            "routine_active_levels": 11,
            "initial_entry_and_scale_ins": "initial entry + adverse-price scale-ins only; no "
                                           "pre-signal adds",
            "reduce_only_exit": True,
            "same_bar_multi_level_ordering": "deterministic and conservative - see "
                                             "dca_execution_semantics.engine_semantics."
                                             "intrabar_ordering",
            "no_add_after_flat_or_kill": True,
        },
        "expected": {
            "cohorts": C.CANONICAL["cohorts"],
            "strategy_cases_per_cohort": C.CANONICAL["strategy_cases"],
            "dca_configs_per_cohort": C.CANONICAL["dca_configs"],
            "base_combinations_per_cohort": C.CANONICAL["base_combinations_per_cohort"],
            "cohort_grid_kinds": list(C.COHORT_GRID_KINDS),
            "phase_grid_count": C.CANONICAL["phase_grid_count"],
            "case_evaluations_per_cohort_per_grid": C.CANONICAL["base_combinations_per_cohort"],
            "case_evaluations_per_cohort_all_grids": (C.CANONICAL["base_combinations_per_cohort"]
                                                      * C.CANONICAL["phase_grid_count"]),
            "case_evaluations_per_grid": C.CANONICAL["case_evaluations_per_grid"],
            "expected_case_evaluations": C.CANONICAL["expected_case_evaluations"],
            "arithmetic": "3 cohorts (BTCUSDT x {5m,15m,30m}) x 12 registered (session basis, "
                          "entry rule, conditioning) cases x 48 DCA configs = 1728 base "
                          "combinations per phase grid; x 10 phase grids = 17280 full case "
                          "evaluations. A case evaluation = one engine simulation of one joint "
                          "parameter cell of the cohort on one phase grid.",
            "runtime_expectation": {
                "basis": "measured on this machine: Strategy A v2 evaluated 103,680 cells in "
                         "1,302 s (~12.6 ms per case); Strategy D 16,128 clock-window cells in "
                         "232 s; this family's episodes are up to 24 h long on a 5m base grid",
                "estimate": "roughly one to three hours of engine time plus the bin rebuild for "
                            "three base grids - an estimate, not a guarantee",
                "status": "estimate only; the run is launched detached inside the container and "
                          "monitored from the attempt state/log, never by holding an agent turn "
                          "open",
            },
        },
        "non_goals": [
            "no parameter search beyond the registered strategy grid and the registered DCA "
            "domain",
            "no OOS-based selection of any parameter",
            "no venue replication (the raw store holds a single venue; disclosed, not repaired "
            "inside this round)",
            "no stop-loss / take-profit beyond the registered rail",
            "no Paper or Live trading",
            "no second backtester or engine",
            "no new Manager/Service/Factory/Registry/Orchestrator/daemon/queue",
            "no legacy Nautilus runtime, gate or performance truth",
            "no narrowing or widening of the eligible universe after seeing results",
        ],
        "artifacts_required": [
            "result.json", "artifacts/cohort_results.json", "artifacts/cohort_survivors.json",
            "artifacts/assertions.json", "artifacts/dca_layer_histogram.json",
            "artifacts/session_schedule.json", "artifacts/family_falsification.json",
            "artifacts/robustness_diagnostics.json", "artifacts/funding_series.json",
            "artifacts/input_manifest.json", "artifacts/bins_build.json", "artifacts/progress.json",
        ] + ["artifacts/grid_%s.csv" % g for g in C.COHORT_GRID_KINDS],
        "assertions_declared": [
            "episodes_partition", "pnl_decomposition", "coverage_complete",
            "cohort_count_matches_registered", "strategy_grid_is_registered_product",
            "dca_grid_is_registered_product", "base_combinations_per_cohort_per_grid",
            "expected_case_evaluations", "layer0_equals_episodes", "layer_histogram_nonempty",
            "no_entry_after_exhaustion", "ending_equity_floor", "selector_deterministic",
            "selector_historical_only", "bar_grid_is_contiguous",
            "no_unregistered_strategy_case", "entry_bar_matches_registered_boundary",
            "exit_bar_matches_registered_window_end", "entry_never_before_signal_complete",
            "session_boundary_guards_aligned", "episodes_never_exceed_windows_seen",
            "overlap_skip_count_reported", "clipped_sessions_count_reported",
            "funding_bar_never_out_of_hold", "no_funding_grid_is_cost_free",
            "fee_2x_track_is_not_a_noop", "funding_2x_track_is_not_a_noop",
            "cost_attrition_track_is_not_a_noop", "no_funding_track_is_cost_free",
            "entry_delay_track_is_not_a_noop", "slippage_track_is_not_a_noop",
            "all_twelve_registered_cases_evaluated", "daily_series_is_slice_scoped",
        ],
        "parameter_contract": {
            "parameter_contract_version": 1,
            "family_id": FAMILY_ID,
            "contract_ref": "v1.8 generic family parameter contract "
                            "(runtime/parameter_contract.py); generated from this document's "
                            "registered parameter_domain / dca_domain",
            "research_axes_ordered": (
                [{"name": "window_case", "kind": "composite", "members": list(C.CASE_FIELDS),
                  "registered_values": [list(case) for case in C.STRATEGY_CASES],
                  "row_fields": list(C.CASE_FIELDS)}]
                + [{"name": ax, "kind": "atomic", "members": [ax],
                    "registered_values": list(C.DCA_GRID[ax]), "row_fields": [ax]}
                   for ax in C.DCA_AXES]),
            "row_fields": list(C.CASE_FIELDS) + list(C.DCA_AXES),
            "composite_map": {"window_case": list(C.CASE_FIELDS)},
            "strategy_param_fields": list(C.CASE_FIELDS),
            "dca_param_fields": list(C.DCA_AXES),
            "canonical_recipe": {"sort_keys": True, "separators": [",", ":"],
                                 "ensure_ascii": False,
                                 "numeric_rule": "JSON number finite, bool excluded"},
            "domain_cardinality": {"strategy": C.CANONICAL["strategy_cases"],
                                   "dca": C.CANONICAL["dca_configs"],
                                   "per_cohort": C.CANONICAL["base_combinations_per_cohort"]},
        },
    }


def run_spec(fp_input, fp_value):
    return {
        "schema_version": 1,
        "document_kind": "run-spec (Strategy G v1; instantiate to /results/<family_id>/rounds/"
                         "<round_id>/attempts/<run_id>/run-spec.json BEFORE launch)",
        "template_instantiation": {
            "placeholders": ["{{round_id}}", "{{run_id}}", "{{task_id}}", "{{created_at_utc}}",
                             "{{script_sha256}}", "{{engine_selfcheck_sha256}}",
                             "{{round_spec_path}}"],
            "rule": "Domains, split, costs, gates and the selector/disposition versions are "
                    "FROZEN before the first run and must be copied from the instantiated "
                    "round-spec.json. Only the seven placeholders may be substituted. "
                    "{{script_sha256}} must be the host-side recomputed sha256 of the deployed "
                    "/scripts/70_strategy_g_run.py; {{engine_selfcheck_sha256}} the same for "
                    "/scripts/tests/test_strategy_g_engine.py, which must run (rc=0) before "
                    "launch.",
        },
        "family_id": FAMILY_ID,
        "round_id": "{{round_id}}",
        "run_id": "{{run_id}}",
        "task_id": "{{task_id}}",
        "kanban_board": "quant-strategy-research",
        "created_at_utc": "{{created_at_utc}}",
        "round_spec_path": "{{round_spec_path}}",
        "selector_version": "cohort-selector-v1",
        "disposition_version": "cohort-disposition-v1",
        "data": {"source": "/data/raw", "read_only": True, "symbols": list(C.SYMBOLS),
                 "timeframes": [dict(tf) for tf in C.TIMEFRAMES],
                 "start": C.DATA["start"], "end": C.DATA["end"],
                 "historical_start": C.DATA["historical_start"],
                 "historical_end": C.DATA["historical_end"],
                 "oos_start": C.DATA["oos_start"], "oos_end": C.DATA["oos_end"],
                 "funding_truth_status_windows": {
                     "modeled_funding": "2022-01-01T00:00:00Z .. 2023-10-31 (reconstruction; "
                                       "mark_price null)",
                     "official": "2023-10-31T08:00:00Z .. 2026-09-11T23:59:59Z (Binance mark "
                                 "price present)",
                     "primary_uses": "the complete registered series (modelled + official), as a "
                                     "COST only",
                     "engine_rule": "The position is exposed to every funding settlement whose raw instant lies in the CLOSED interval [entry instant - 1 s, exit instant + 1 s]. The measured raw jitter of the settlement timestamps is 0..28 ms LATE and never early, so a settlement sitting on a session boundary is charged to the window that holds the position at that instant. A settlement is charged on the position notional at the moment of the charge: the close of the bar that contains it inside the session, or the episode's exit fill price when it lands at the closing boundary. The sign follows the position: a positive rate is a cost to a long and a credit to a short. Settlements outside the closed interval are never charged."},
                 "bar_labelling": "VERIFIED at pre-registration: open_time_ms is the bar START "
                                  "in UTC; 5m/15m/30m are exactly contiguous over the window "
                                  "(493,920 / 164,640 / 82,320 bars, 0 gaps, 0 off-grid steps), "
                                  "so the slot and session mapping is exact and the engine "
                                  "asserts it at run time."},
        "parameter_domain": {"grid_cases": GRID_CASES, "grid_case_names": CASE_NAMES,
                             "legal_cases_per_cohort": C.CANONICAL["strategy_cases"],
                             "session_basis_grid": list(C.SESSION_BASES),
                             "entry_rule_grid": list(C.ENTRY_RULES),
                             "conditioning_grid": list(C.CONDITIONINGS),
                             "session_contract": C.SESSION_CONTRACT,
                             "session_contract_status": C.RESEARCH_MARKER,
                             "entry_timing_status": C.RESEARCH_MARKER,
                             "exit_timing_status": C.RESEARCH_MARKER},
        "dca_domain": {"base_quote": 1000,
                       "base_quote_status": C.CONSTANT_STATUS,
                       "spacing_pct": list(C.DCA_GRID["spacing_pct"]),
                       "spacing_pct_status": C.SEARCH_STATUS,
                       "size_multiplier": list(C.DCA_GRID["size_multiplier"]),
                       "size_multiplier_status": C.SEARCH_STATUS,
                       "breakeven_tp_pct": list(C.DCA_GRID["breakeven_tp_pct"]),
                       "breakeven_tp_pct_status": C.SEARCH_STATUS,
                       "invalidation_pct": list(C.DCA_GRID["invalidation_pct"]),
                       "invalidation_pct_status": C.SEARCH_STATUS,
                       "config_count": C.CANONICAL["dca_configs"],
                       "grid": DCA_CELLS},
        "provenance_mirror": {"base_quote_status": C.CONSTANT_STATUS,
                              "spacing_pct_status": C.SEARCH_STATUS,
                              "size_multiplier_status": C.SEARCH_STATUS,
                              "breakeven_tp_pct_status": C.SEARCH_STATUS,
                              "invalidation_pct_status": C.SEARCH_STATUS},
        "gates": {"min_episodes_is": C.GATES["min_episodes_is"],
                  "min_episodes_oos": C.GATES["min_episodes_oos"],
                  "neighborhood_min_same_sign_fraction":
                      C.GATES["neighborhood_min_same_sign_fraction"]},
        "costs": {"source": "canonical instrument metadata, read at run time from /data/raw",
                  "maker_fee": C.COSTS["maker_fee"], "taker_fee": C.COSTS["taker_fee"],
                  "fee_bps": 5, "baseline_slippage_ticks": C.COSTS["baseline_slippage_ticks"],
                  "cost_attrition_fee_mult": C.COSTS["cost_attrition_fee_mult"],
                  "funding": "charged per settlement inside the session's closed exposure "
                             "interval; funding_2x doubles it and the no_funding grids zero it "
                             "(descriptive)",
                  "funding_exposure_rule": "The position is exposed to every funding settlement whose raw instant lies in the CLOSED interval [entry instant - 1 s, exit instant + 1 s]. The measured raw jitter of the settlement timestamps is 0..28 ms LATE and never early, so a settlement sitting on a session boundary is charged to the window that holds the position at that instant. A settlement is charged on the position notional at the moment of the charge: the close of the bar that contains it inside the session, or the episode's exit fill price when it lands at the closing boundary. The sign follows the position: a positive rate is a cost to a long and a credit to a short. Settlements outside the closed interval are never charged."},
        "expected": {"cohorts": C.CANONICAL["cohorts"],
                     "strategy_cases_per_cohort": C.CANONICAL["strategy_cases"],
                     "dca_configs_per_cohort": C.CANONICAL["dca_configs"],
                     "base_combinations_per_cohort": C.CANONICAL["base_combinations_per_cohort"],
                     "cohort_grid_kinds": list(C.COHORT_GRID_KINDS),
                     "phase_grid_count": C.CANONICAL["phase_grid_count"],
                     "case_evaluations_per_grid": C.CANONICAL["case_evaluations_per_grid"],
                     "expected_case_evaluations": C.CANONICAL["expected_case_evaluations"]},
        "script": {"path": "/scripts/70_strategy_g_run.py", "sha256": "{{script_sha256}}",
                   "deployed_from": "HCH725/quant-runtime-pipeline "
                                    "container/scripts/70_strategy_g_run.py (byte-identical, P10 "
                                    "recomputes it host side)"},
        "engine_selfcheck": {"script": "/scripts/tests/test_strategy_g_engine.py",
                             "sha256": "{{engine_selfcheck_sha256}}",
                             "must_run_before_launch": True},
        "expected_outputs": ["result.json", "artifacts/cohort_results.json",
                             "artifacts/cohort_survivors.json", "artifacts/assertions.json",
                             "artifacts/dca_layer_histogram.json",
                             "artifacts/session_schedule.json",
                             "artifacts/family_falsification.json",
                             "artifacts/robustness_diagnostics.json",
                             "artifacts/funding_series.json", "artifacts/input_manifest.json",
                             "artifacts/bins_build.json", "artifacts/progress.json"]
                            + ["artifacts/grid_%s.csv" % g for g in C.COHORT_GRID_KINDS],
        "falsification": [
            "no signal / insufficient sessions (G2)",
            "no qualifying historical candidate (G3)",
            "OOS economic rejection (G4)",
            "full-window economic rejection (G5)",
            "robustness economic failure (G6)",
            "parameter-neighbourhood fragility (G7)",
            "session-definition instability -> family-level, never PASS",
            "entry-rule instability -> family-level, never PASS",
            "cost boundary (40 bps attrition) -> family-level, never PASS",
            "conditioning does not beat the unconditional baseline -> family-level, never PASS",
            "coverage incomplete in any registered phase grid (G1)",
            "any false assertion in assertions.json",
        ],
        "notes": "One attempt of round {{round_id}}. The engine rebuilds the qlib .bin store from "
                 "the read-only raw store, evaluates 3 cohorts x 12 strategy cases x 48 DCA "
                 "configs on 10 registered phase grids, and writes the registered artifacts. The "
                 "runner's verdict_recommendation is a recommendation; the final verdict.json is "
                 "written by the Hermes default card after reading the artifacts back (contract "
                 "10.7).",
        "semantic_fingerprint": {"fingerprint_input": fp_input, "semantic_fingerprint": fp_value},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--family-json",
                    default="/Volumes/ExpansionDrive/qlib-results/%s/family.json" % FAMILY_ID)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    fp_input, fp_value = "TO_BE_RECOMPUTED", "TO_BE_RECOMPUTED"
    if os.path.exists(args.family_json):
        fam = C.load(args.family_json)
        fp_input = fam["fingerprint_input"]
        fp_value = fam["semantic_fingerprint"]
    round_t = round_spec(fp_input, fp_value)
    run_t = run_spec(fp_input, fp_value)
    if args.check:
        problems = []
        for doc, name in ((round_t, "round"), (run_t, "run")):
            if EMPTY := [k for k, v in doc.items() if v is None and k != "parent_family"]:
                problems.append("%s template has empty fields: %r" % (name, EMPTY))
        c, probs, _extra = C.check(round_t, run_t)
        problems.extend(probs)
        print("template check: %d problem(s)" % len(problems))
        for p in problems:
            print("PROBLEM:", p)
        return 1 if problems else 0
    os.makedirs(TEMPLATES, exist_ok=True)
    for name, doc in (("strategy_g_v1_round_spec.template.json", round_t),
                      ("strategy_g_v1_run_spec.template.json", run_t)):
        path = os.path.join(TEMPLATES, name)
        with open(path, "w") as fh:
            json.dump(doc, fh, indent=1, ensure_ascii=False, sort_keys=True)
            fh.write("\n")
        print("wrote %s (%d bytes)" % (path, os.path.getsize(path)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
