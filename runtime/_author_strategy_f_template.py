#!/usr/bin/env python3
"""Author the frozen Strategy F v1 pre-registration templates (round-spec + run-spec).

The templates are the frozen scientific registration of the Stochastic-RSI (Renko) family; the
launch card instantiates them with three (round-spec) and seven (run-spec) registered
placeholders and nothing else.  Everything is generated from the registered constants in
`strategy_f_v1_counts.py` so the document and the validator can never drift apart, and the
result is validated by both the generic parameter contract (v1.8 launch gate) and the counts
module before it is written.

usage: python3 runtime/_author_strategy_f_template.py [--out-dir runtime/templates] [--json]
"""
import argparse
import calendar
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import strategy_f_v1_counts as counts  # noqa: E402
import parameter_contract as pc  # noqa: E402

RAW_INSTRUMENTS = "/Volumes/ExpansionDrive/market-data-raw/binance/usdm/instruments/" \
                  "usdm-perp-instruments.json"
ROUND_TEMPLATE_NAME = "strategy_f_v1_round_spec.template.json"
RUN_TEMPLATE_NAME = "strategy_f_v1_run_spec.template.json"
CONTRACT_VERSION = "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md v1.7.0"
FAMILY_FINGERPRINT_INPUT = (
    "stochastic-rsi-renko-2026-08-31|universe=portability=direct;window=record-faithful|"
    "dca=spacing_pct=0.01,0.02,0.03,0.04;size_multiplier=1.0,1.1;"
    "breakeven_tp_pct=0.01,0.02,0.03;invalidation_pct=0.05,0.10|"
    "selector=cohort-selector-v1;disposition=cohort-disposition-v1|"
    "source=stochastic-rsi-renko-2026-08-31.md")
LINEAGE_NOTE = (
    "Independent family canonicalized during the contract-14.4 backlog backfill (card "
    "t_86d04b09) from the already-reviewed wiki record quant/stochastic-rsi-renko-2026-08-31.md. "
    "Kanban parent edge is scheduling/order only; scientific lineage is null: parent_family=null "
    "- this family does not refine, inherit parameters from, or shrink the universe of any "
    "earlier family. Candidate eligibility was decided once, by the historical Research Intake "
    "Review decision that accepted this record; Wiki Brain is knowledge preservation, not a "
    "second eligibility gate.")
SOURCE_URL = ("https://github.com/fmzquant/strategies/blob/"
              "7853bb2bf262c4567ac238d3552d97f0e50cb801/"
              "K线Stochastic-RSI交易策略Renko-Stochastic-RSI-Trading-Strategy.md")
WIKI_RECORD = "quant/stochastic-rsi-renko-2026-08-31.md"


def measure_instruments():
    """The registered panel's instrument metadata, measured on this machine right now."""
    with open(RAW_INSTRUMENTS) as fh:
        doc = json.load(fh)
    out = {}
    for item in doc["instruments"]:
        f = item["fields"]
        if f["raw_symbol"] in counts.SYMBOLS:
            out[f["raw_symbol"]] = {"price_increment": float(f["price_increment"]),
                                    "taker_fee": float(f["taker_fee"]),
                                    "maker_fee": float(f["maker_fee"]),
                                    "margin_init": float(f["margin_init"]),
                                    "margin_maint": float(f["margin_maint"])}
    for sym in counts.SYMBOLS:
        if sym not in out:
            raise SystemExit("instrument metadata missing for %s" % sym)
    return out


def parameter_contract():
    axes = []
    for name, values in (("brick_pct", counts.BRICK_PCT_GRID),
                         ("rsi_period", counts.RSI_PERIOD_GRID)):
        axes.append({"name": name, "kind": "atomic", "members": [name],
                     "registered_values": list(values), "row_fields": [name]})
    for name in counts.DCA_AXES:
        axes.append({"name": name, "kind": "atomic", "members": [name],
                     "registered_values": list(counts.DCA_GRID[name]), "row_fields": [name]})
    return {
        "parameter_contract_version": 1,
        "family_id": counts.FAMILY_ID,
        "contract_ref": "v1.8 generic family parameter contract (runtime/parameter_contract.py); "
                        "generated from this document's registered parameter_domain / dca_domain",
        "research_axes_ordered": axes,
        "row_fields": list(counts.STRATEGY_FIELDS) + list(counts.DCA_AXES),
        "composite_map": {},
        "strategy_param_fields": list(counts.STRATEGY_FIELDS),
        "dca_param_fields": list(counts.DCA_AXES),
        "canonical_recipe": {"sort_keys": True, "separators": [",", ":"], "ensure_ascii": False,
                             "numeric_rule": "JSON number finite, bool excluded"},
        "row_match_recipe": {"keys": ["symbol", "timeframe"] + list(counts.STRATEGY_FIELDS)
                                     + list(counts.DCA_AXES),
                             "equality": "exact, numeric == float compare, rest bytewise"},
        "non_params": ["symbol", "timeframe", "window_kind", "case_name", "diagnostics",
                       "metrics"],
        "domain_cardinality": {"strategy": counts.CANONICAL["strategy_cases"],
                               "dca": counts.CANONICAL["dca_configs"],
                               "per_cohort": counts.CANONICAL["base_combinations_per_cohort"]},
    }


def round_spec_template(instruments):
    cases = [{"brick_pct": b, "rsi_period": r} for b, r in counts.STRATEGY_CASES]
    dca_grid = [{"spacing_pct": sp, "size_multiplier": m, "breakeven_tp_pct": tp,
                 "invalidation_pct": iv, "base_quote": 1000}
                for sp in counts.DCA_GRID["spacing_pct"]
                for m in counts.DCA_GRID["size_multiplier"]
                for tp in counts.DCA_GRID["breakeven_tp_pct"]
                for iv in counts.DCA_GRID["invalidation_pct"]]
    return {
        "schema_version": 1,
        "document_kind": "round-spec (frozen pre-registration TEMPLATE; every scientific field "
                         "is frozen before the first run and instantiation may substitute only "
                         "the three registered placeholders, the recomputed fingerprint and the "
                         "template-era implementation-status prose)",
        "template_instantiation": {
            "placeholders": ["{{round_id}}", "{{kanban_task_id}}", "{{created_at_utc}}"],
            "rule": "Every scientific field in this template is FROZEN before the first run. "
                    "Instantiation may only substitute the three placeholders, the "
                    "TO_BE_RECOMPUTED fingerprint and the template-era implementation-status "
                    "prose; it must not touch a domain, a split, a gate, a cost, a selector "
                    "version or the falsification battery.",
            "instantiated_to": "/results/<family_id>/rounds/<round_id>/round-spec.json",
            "validated_by": "python3 runtime/strategy_f_v1_counts.py --spec "
                            "runtime/templates/strategy_f_v1_round_spec.template.json",
            "status": "PREREGISTRATION ONLY / NOT LAUNCHED"},
        "family_id": counts.FAMILY_ID,
        "family_title": "Production Strategy F - Stochastic RSI (Renko) family representative, "
                        "BTC/ETH/BNB/SOL USD-M perpetual, five source grids",
        "round_id": "{{round_id}}",
        "kanban_task_id": "{{kanban_task_id}}",
        "kanban_board": "quant-strategy-research",
        "created_at_utc": "{{created_at_utc}}",
        "authored_by": "Hermes default (Xiaoqian) - Contract v1.7.0 production card "
                       "{{kanban_task_id}}",
        "contract": CONTRACT_VERSION + " (cohort survivor semantics of sections 7.2 / 7.3; the "
                                        "v1.8.0 launch gate additionally requires this "
                                        "document's generic parameter_contract, carried below)",
        "semantic_fingerprint": {
            "fingerprint_input": FAMILY_FINGERPRINT_INPUT,
            "normalisation": "The frozen handoff value (contract 14.2 step 7 / 14.4): family_id "
                             "| signal family and its registered mechanics | DCA domain (axes "
                             "alphabetically) | data window | timeframe | direction | symbols | "
                             "selector;disposition. It is copied verbatim from family.json and "
                             "never recomputed here.",
            "semantic_fingerprint": "TO_BE_RECOMPUTED",
            "registered_axis_vs_family_fingerprint": "Disclosure (no silent widening): the frozen "
                                                     "fingerprint_input records the DCA axes, the "
                                                     "split and the selector/disposition versions; "
                                                     "the brick_pct and rsi_period search grids are "
                                                     "registered in this round-spec (contract 7.2 "
                                                     "item 3) and are the round's strategy domain."},
        "parent_family": None,
        "lineage_note": LINEAGE_NOTE,
        "provenance": {
            "reviewed_source": WIKI_RECORD,
            "review_status": "research-only (intake reviewed; implementation_status="
                             "not-implemented, adoption=not-approved, approval_scope=research-only)",
            "intake_decision": "reviewed (canonical review backlog ingested_wiki_records; "
                               "candidateized exactly once by card t_86d04b09)",
            "primary_sources": [SOURCE_URL],
            "record_commit": "7853bb2bf262c4567ac238d3552d97f0e50cb801",
            "no_second_suitability_gate": "Contract 14.4 (doc-alignment, 2026-09-16): candidate "
                                          "eligibility was decided by the historical Intake "
                                          "Review decision; this card does not re-run a "
                                          "crypto/runnable suitability screen."},
        "hypothesis": {
            "statement": "A Renko-constructed price series filters noise well enough that a "
                         "Stochastic-RSI K/D crossover is worth trading on Binance USD-M "
                         "perpetuals after real fees, adverse slippage and funding.",
            "mechanism_source_reported": "This is a Stochastic RSI trading strategy designed for "
                                         "use on Renko charts. It generates buy and sell signals "
                                         "using the crossover and crossunder of Stochastic RSI K "
                                         "and D lines. The strategy is specialized for Renko "
                                         "charts and can effectively filter market noise and "
                                         "identify trends. / The trading signals are primarily "
                                         "based on the Stochastic RSI indicator ... First, the "
                                         "RSI value over a period is calculated, then Stochastic "
                                         "RSI is computed based on the RSI values. K line: moving "
                                         "average of RSI values over a period (fast line); D "
                                         "line: moving average of the K line (slow line). When K "
                                         "crosses above D a buy signal is generated; when K "
                                         "crosses below D a sell signal is generated.",
            "mechanism_research_interpretation": "Stochastic RSI (Renko) logic. Explicit Renko "
                                                 "Stochastic RSI strategy. Data dependency: "
                                                 "Renko charts.",
            "direction": "long AND short: the source defines both a buy signal (K crosses above "
                         "D) and a sell signal (K crosses below D), so the registered reading is "
                         "a two-sided flip book (long on the bullish crossing, short on the "
                         "bearish one). No direction leg is added that the record does not "
                         "register, and the mirror is the DCA rail's sym-directional mirror.",
            "source": "Reviewed wiki record %s (fmzquant/strategies, commit %s)."
                      % (WIKI_RECORD, "7853bb2bf262c4567ac238d3552d97f0e50cb801"),
            "declared_limitations_from_record": [
                "Renko construction contract is underspecified: brick size/method, source price, "
                "multiple-brick handling within one source bar, timestamp assignment, and causal "
                "fill convention must be fixed before implementation.",
                "underspecified parameter robustness",
                "not independently reproduced",
                "leakage/repainting risk: manual semantic review required for hidden repainting "
                "in original source code."],
            "how_this_round_closes_the_underspecification": "The record's first limitation is "
                                                            "closed by the frozen renko_contract "
                                                            "and indicator_contract in "
                                                            "parameter_domain below; the source "
                                                            "specifies no numeric parameters, so "
                                                            "the brick_pct / rsi_period grids and "
                                                            "the STOCH_PERIOD / K / D smoothing "
                                                            "are registered as "
                                                            "RESEARCH_DEFINED before the first "
                                                            "run.",
            "repainting_review": "The registered construction reads only the forming bar's own "
                                 "close and stamps every brick at that bar's close, so no brick "
                                 "can appear before the bar that produced it (the prefix-"
                                 "invariance test in the engine self-check is the executable "
                                 "control). No intrabar round trip is invented.",
            "falsification_plan_from_record": [
                "Construct an explicit tracked strategy-family implementation and evaluate it "
                "honouring `Renko charts` and the detailed signal rules - done by "
                "container/scripts/60_strategy_f_run.py under this round-spec.",
                "Test out-of-sample against structurally relevant assets - the registered OOS "
                "window 2025-10-01..2026-09-11 on the same four perpetuals.",
                "For hybrid candidates: isolate components via ablation - NOT APPLICABLE: this "
                "record is not hybrid (a single transparent indicator rule with a Renko bar "
                "construction); the registered equivalent is the parameter-neighbourhood gate "
                "G7 plus the cost/lag stress grids."]},
        "eligible_universe": {
            "symbols": list(counts.SYMBOLS),
            "timeframes": [dict(tf) for tf in counts.TIMEFRAMES],
            "cohort_count": counts.CANONICAL["cohorts"],
            "cohort_definition": "one cohort = one (symbol, source-grid) pair; the source grid is "
                                 "the bar series the Renko construction is computed from. It is "
                                 "the v1.3.0 disposition unit and all twenty cohorts are always "
                                 "reported separately; none may be dropped.",
            "symbol_source": "BINANCE USD-M perpetual, canonical raw store (/data/raw, read-only)",
            "universe_shrinkage_disclosure": "Universe shrinkage disclosure: the source names no "
                                             "symbols and no venue. The registered panel is the "
                                             "complete set of perpetuals this machine holds at "
                                             "all (BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT) over the "
                                             "five source grids {5m,15m,30m,1h,4h}; relative to "
                                             "the raw store's 4-symbol panel there is no "
                                             "shrinkage. 1d/1w are present in raw but excluded "
                                             "from this family's eligible universe: the Renko "
                                             "ladder and the registered DCA spacing are "
                                             "intraday-scale price structures and a daily/weekly "
                                             "execution grid cannot resolve them. The choice is "
                                             "registered before any computation and must not be "
                                             "narrowed or widened afterwards.",
            "excluded": ["1d", "1w (present in raw, outside this family's eligible universe)",
                         "every symbol absent from the canonical raw store"],
            "instruments_metadata_measured_at_pre_registration": instruments,
            "universe_immutability": "Registered here, before computation. Must not be narrowed or "
                                     "widened after seeing results (contract 7.2)."},
        "parameter_domain": {
            "kind": "strategy parameter domain (the registered signal cell set): the complete "
                    "brick_pct x rsi_period product",
            "case_definition": "a strategy case is (brick_pct, rsi_period): the Renko brick "
                               "threshold as a fraction of the current reference price, and the "
                               "Wilder RSI lookback in bricks",
            "grid_cases": cases,
            "case_names": ["brick%spct_rsi%d" % (str(b).replace(".", "p"), r)
                           for b, r in counts.STRATEGY_CASES],
            "legal_cases_per_cohort": counts.CANONICAL["strategy_cases"],
            "brick_pct_grid": list(counts.BRICK_PCT_GRID),
            "brick_pct_status": counts.SEARCH_STATUS + " - this axis IS searched over the "
                                                        "registered values. The axis is a "
                                                        "pre-registered search DOMAIN of this "
                                                        "round, not an immutable user-fixed "
                                                        "invariant (contract 7.2 v1.3.1).",
            "rsi_period_grid": list(counts.RSI_PERIOD_GRID),
            "rsi_period_status": counts.SEARCH_STATUS + " - this axis IS searched over the "
                                                        "registered values (the source names no "
                                                        "period).",
            "renko_contract": counts.RENKO_CONTRACT,
            "renko_contract_status": counts.RESEARCH_MARKER + " - the source requires Renko "
                                                              "charts but fixes no brick size, "
                                                              "source price, multi-brick rule or "
                                                              "stamp convention; this round "
                                                              "registers one contract and applies "
                                                              "it identically to all twenty "
                                                              "cohorts.",
            "indicator_contract": counts.INDICATOR_CONTRACT,
            "indicator_status": counts.RESEARCH_MARKER + " - the source describes the indicator "
                                                         "qualitatively (RSI, then the "
                                                         "stochastic of the RSI, K as the fast "
                                                         "line, D as the slow line) and fixes no "
                                                         "periods; STOCH_PERIOD=14, K=3, D=3 are "
                                                         "registered here.",
            "entry_timing_status": counts.RESEARCH_MARKER + " - a crossing is detected at the "
                                                            "forming bar's close, so the order "
                                                            "fills at the NEXT bar's open (the "
                                                            "contract's no-look-ahead rule); the "
                                                            "registered 1-bar delay stress is the "
                                                            "lag arm of the falsification battery.",
            "exit_timing_status": counts.RESEARCH_MARKER + " - an opposite crossing is known at "
                                                           "its forming bar's close and flattens "
                                                           "(and mirrors) at the next bar's open; "
                                                           "take profit and invalidation are "
                                                           "intrabar resting orders on the "
                                                           "running average cost.",
            "selection_rule": "The cohort winner is elected ONLY by the deterministic "
                              "HISTORICAL-window selector of contract 7.3 over this registered "
                              "domain; the OOS window selects nothing and is read only after the "
                              "winner is frozen.",
            "domain_immutability": "The domain is complete and bounded; no post-hoc parameter "
                                   "additions, no selection on OOS (contract 7.3)."},
        "dca_domain": {
            "kind": "DCA parameter domain (the execution rail), four axes - contract 7.2 item 4",
            "base_quote": 1000,
            "base_quote_status": counts.CONSTANT_STATUS + " - held constant across the whole "
                                                          "domain (it is NOT a search axis) and "
                                                          "NOT a user-fixed invariant: no "
                                                          "operator evidence fixes it at 1000. "
                                                          "It is a project pre-registration "
                                                          "choice, frozen here.",
            "spacing_pct": list(counts.DCA_GRID["spacing_pct"]),
            "spacing_pct_status": counts.SEARCH_STATUS + " - this axis IS searched over the "
                                                         "registered values. The axis is a "
                                                         "pre-registered search DOMAIN of this "
                                                         "round, not an immutable user-fixed "
                                                         "invariant (contract 7.2 v1.3.1).",
            "size_multiplier": list(counts.DCA_GRID["size_multiplier"]),
            "size_multiplier_status": counts.SEARCH_STATUS + " - this axis IS searched over the "
                                                            "registered values.",
            "breakeven_tp_pct": list(counts.DCA_GRID["breakeven_tp_pct"]),
            "breakeven_tp_pct_status": counts.SEARCH_STATUS + " - this axis IS searched over the "
                                                              "registered values.",
            "invalidation_pct": list(counts.DCA_GRID["invalidation_pct"]),
            "invalidation_pct_status": counts.SEARCH_STATUS + " - this axis IS searched over the "
                                                              "registered values.",
            "config_count": counts.CANONICAL["dca_configs"],
            "grid": dca_grid},
        "dca_execution_semantics": {
            "authorization": "Inherited verbatim from the Strategy A/B/C/D/E engine family "
                             "(per-fill fee accounting, breakeven-anchored take profit, resting "
                             "invalidation against the running average cost, reduce-only exits, "
                             "capital-exhaustion backstop).",
            "sizing": "level k notional = base_quote x size_multiplier^k x leverage at that "
                      "level's own fill price.",
            "ladder": "tranche #1 opens at the entry bar's open; adverse-price scale-ins fill at "
                      "level_k = initial_entry_price x (1 - spacing_pct x k) for a long and "
                      "x (1 + spacing_pct x k) for a short, k = 1..10, so at most 11 routine "
                      "active levels of the 12-tranche rail (tranche #12 is reserve).",
            "take_profit": "reduce-only at running_average_cost x (1 +/- breakeven_tp_pct); fills "
                           "at its own level, never better.",
            "invalidation": "a RESTING stop at running_average_cost x (1 -/+ invalidation_pct); it "
                            "is checked against the NEXT trigger on the way down (long) or up "
                            "(short), so it preempts a deeper ladder level rather than letting "
                            "the price walk through it.",
            "exit_order": "Registered order inside one bar: (1) the adverse ladder against the "
                          "resting stop, (2) the take profit, (3) the opposite-crossing flip at "
                          "the next bar's open, (4) the slice-end flatten.",
            "flip_rule": "An opposite crossing closes every layer reduce-only at the next bar's "
                         "open and opens the mirrored side at the same instant; a same-sign "
                         "crossing while positioned is ignored (the rail averages, it does not "
                         "re-enter).",
            "gap_rules": "An adverse gap through the stop fills at the bar's open (the first price "
                         "really available). A favourable gap is never credited: a take profit "
                         "fills at its own level.",
            "intrabar_ordering": "Deterministic and conservative: the ladder-walk order, then the "
                                 "stop, then the take profit, then the flip/slice-end.",
            "funding": "charged per settlement on the position notional, in the CLOSED holding "
                       "interval [entry instant - 1s, exit instant + 1s]; the engine never "
                       "assumes a fixed 8h grid.",
            "capital_exhaustion": "backstop only: forced full flatten at the bar close if "
                                  "unrealised equity <= margin_maint x notional; no new episode "
                                  "is opened once the realised equity is gone.",
            "concurrency": "one episode at a time; the book is FLAT between episodes and no layer "
                           "may be added after a FLAT/kill.",
            "slice_boundary": "Every slice (historical / oos / full / each stress grid) is "
                              "evaluated with a FLAT book at its first bar and is reduce-only "
                              "flattened at its last bar: no position crosses a slice edge."},
        "selector_and_disposition": {
            "selector_version": "cohort-selector-v1",
            "disposition_version": "cohort-disposition-v1",
            "selection_protocol": "Deterministic, HISTORICAL window ONLY; OOS must never be used "
                                  "to select parameters (contract 7.3).",
            "selection_steps": [
                "1. Sufficiency: if the cohort's best historical case has fewer than "
                "min_episodes_is (30) episodes, the cohort is culled with reason "
                "insufficient_trades.",
                "2. Candidate eligibility: historical net_pnl > 0 AND sharpe > 0 AND episodes >= "
                "min_episodes_is.",
                "3. No candidate -> the cohort is culled with reason no_qualifying_candidate.",
                "4. Ranking: Sharpe desc, net_pnl desc, then the fixed lexical tie-break on the "
                "registered index of the axis values in the registered axis order.",
                "5. The winner's strategy params + DCA params are carried UNCHANGED into OOS / "
                "full / robustness / neighbourhood; the cell is never re-picked after OOS is "
                "seen."],
            "tie_break_axis_order": list(counts.STRATEGY_FIELDS) + list(counts.DCA_AXES),
            "neighbourhood_axes": list(counts.STRATEGY_FIELDS) + list(counts.DCA_AXES),
            "cohort_survivor_requirements": [
                "a) a historical winner exists (steps 1-4)",
                "b) OOS: the same cell has net_pnl > 0 AND sharpe > 0",
                "c) full window: the same cell has net_pnl > 0",
                "d) robustness: the same cell has net_pnl > 0 in fee_2x, funding_2x, "
                "entry_delay_1_bar and slippage_2ticks",
                "e) parameter-neighbourhood: at least 60% of the winner cell's legal face-adjacent "
                "neighbours agree in historical net-PnL sign",
                "plus this round's registered G8: the same cell has net_pnl > 0 in "
                "cost_attrition_40bps (reason robustness_economic:cost_attrition_40bps)"],
            "cohort_outcomes": ["SURVIVOR", "CULLED"],
            "cull_reasons": ["insufficient_trades", "no_qualifying_candidate", "oos_economic",
                             "full_economic", "robustness_economic:<grids>",
                             "parameter_neighbourhood"],
            "family_disposition": {
                "0 cohort survivors": "REJECT / NO_SURVIVOR (verdict REJECT, "
                                      "performance_claimable false)",
                "1 cohort survivor": "SURVIVOR_FOUND (verdict PASS; performance_claimable still "
                                     "requires the full contract 9.6 condition set and no "
                                     "family-level falsification hit)",
                ">=1 cohort survivors": "PASS (band MULTIPLE_SURVIVORS above one); every survivor "
                                        "is frozen by contract 10.8 and advances, and the survivor "
                                        "count is never itself a reason for "
                                        "performance_claimable=false",
                "coverage or technical incompleteness": "TECHNICAL_INCOMPLETE (no cohort is "
                                                        "judged at all)"},
            "cross_cohort_medians": "never a gate (contract 7.2): any cross-cohort median reported "
                                    "in the artifacts is descriptive and marked non_gating",
            "no_signal_landing": "A cohort whose historical best case reaches fewer than 30 "
                                 "episodes is culled with insufficient_trades; a signal whose "
                                 "entry bar would fall outside a slice is counted "
                                 "(entry_clipped_at_slice_edge) and never traded."},
        "registered_family_level_falsification": {
            "note": "Three FAMILY-level readers, registered before the first run. They never cull "
                    "a cohort by themselves; a hit must NEVER be recorded as PASS (the runner "
                    "recommends DEFERRED and the evidence decides the final verdict).",
            "cost-boundary": {
                "definition": "for any elected winner, the full-window net-PnL sign under the "
                              "canonical cost track differs from the sign under "
                              "cost_attrition_40bps",
                "landing": "family-level: a hit must NEVER be recorded as PASS"},
            "symbol-concentration": {
                "definition": "among the survivors, the largest single symbol's share of the "
                              "total positive OOS net PnL exceeds 70%",
                "landing": "family-level robustness disclosure: a hit must NEVER be recorded as "
                           "PASS"},
            "oos-grid-instability": {
                "definition": "an elected winner's legal face-adjacent neighbours disagree with "
                              "the winner's OOS net-PnL sign on 40% or more of the neighbours",
                "landing": "family-level disclosure (the historical arm is gate G7); a hit must "
                           "NEVER be recorded as PASS"}},
        "gates": {
            "G1_coverage": "every one of the 10 registered phase grids must contain exactly %d "
                           "cases (20 cohorts x 9 strategy cases x 48 DCA configs), the measured "
                           "strategy/DCA cell sets must equal the registered products, and the "
                           "case-evaluation total must equal %d"
                           % (counts.CANONICAL["case_evaluations_per_grid"],
                              counts.CANONICAL["expected_case_evaluations"]),
            "G2_insufficient_trades": "per cohort: fewer than min_episodes_is (30) historical "
                                      "episodes in the cohort's best case, or fewer than "
                                      "min_episodes_oos (10) episodes in the elected winner's OOS "
                                      "slice, culls the cohort",
            "G3_cohort_selector": "historical net_pnl > 0 AND sharpe > 0 AND episodes >= 30, "
                                  "ranked by Sharpe desc, net_pnl desc, registered-index lexical "
                                  "tie-break",
            "G4_cohort_oos": "OOS net_pnl > 0 AND OOS sharpe > 0 for the frozen winner cell",
            "G5_cohort_full": "full-window net_pnl > 0 for the frozen winner cell",
            "G6_cohort_robustness": "the winner cell has net_pnl > 0 in all four execution stress "
                                    "reruns",
            "G7_cohort_neighbourhood": "at least 60% of legal face-adjacent neighbours agree in "
                                       "historical net-PnL sign",
            "G8_cohort_cost_attrition": "the winner cell has net_pnl > 0 in the "
                                        "cost_attrition_40bps grid",
            "verdict_rule": "TECHNICAL_INCOMPLETE if G1 fails; else each cohort passing G3-G8 is "
                            "a SURVIVOR: 0 survivors -> REJECT, >=1 survivors -> PASS (band "
                            "SURVIVOR_FOUND / MULTIPLE_SURVIVORS describes the count only). The "
                            "three registered family-level readers can only move a PASS to "
                            "DEFERRED, never to PASS.",
            "min_episodes_is": counts.GATES["min_episodes_is"],
            "min_episodes_is_provenance": "RESEARCH_DEFINED - the record registers no no-signal "
                                          "floor; this round registers 30 historical episodes for "
                                          "the selection window and 10 for the OOS slice before "
                                          "the first run, and never lowers them afterwards.",
            "min_episodes_oos": counts.GATES["min_episodes_oos"],
            "neighborhood_min_same_sign_fraction":
                counts.GATES["neighborhood_min_same_sign_fraction"],
            "known_gate_set_limitation": "The registered gate set contains no benchmark-relative "
                                         "test and no buy-and-hold comparison; a two-sided flip "
                                         "book can show positive PnL in a trending sample through "
                                         "posture alone. This limitation is disclosed and is not "
                                         "repaired inside this round."},
        "metrics_definitions": {
            "equity": "30000 + cumulative realised net PnL (USDT); unrealised marked at bar closes",
            "daily_series": "end-of-UTC-day equity marks of the EVALUATED slice, forward-filled "
                            "inside the slice only; never padded with pre-window history",
            "sharpe": "mean/std (ddof=1) of the slice's daily returns x sqrt(365); risk-free 0",
            "max_dd": "peak-to-trough of the daily equity series, in % and USDT",
            "cagr": "(ending_equity / 30000) ^ (1/years) - 1, years from the slice's own day count",
            "max_effective_leverage": "max of (notional / unrealised equity), sampled every "
                                      "in-market bar",
            "capital_utilization": "mean of (deployed margin / unrealised equity) over in-market "
                                   "bars",
            "turnover": "sum of |fill notional| over every entry / scale-in / exit fill of the cell",
            "dca_layer_histogram": "count of fills per ladder level, aggregated over every "
                                   "full-window-registered base case",
            "episodes": "the number of entries of a cell; each episode ends at a take profit, an "
                        "invalidation, an opposite-crossing flip or the slice-end flatten",
            "cohort_winner": "the historical-only selector output for that cohort",
            "cross_cohort": "descriptive (non_gating) cross-cohort table of outcomes and medians"},
        "robustness_plan": {
            "declared_before_first_run": True,
            "must_not_narrow_universe_or_lower_gates": True,
            "items": [
                {"id": "R1", "grid": "fee_2x", "method": "double the registered taker fee on "
                                                         "every fill, full window"},
                {"id": "R2", "grid": "funding_2x", "method": "double every funding settlement, "
                                                             "full window"},
                {"id": "R3", "grid": "entry_delay_1_bar", "method": "delay every entry (and every "
                                                                    "mirrored flip) by one bar"},
                {"id": "R4", "grid": "slippage_2ticks", "method": "two instrument ticks of adverse "
                                                                "slippage per fill"},
                {"id": "R5", "grid": "cost_attrition_40bps", "method": "8x the registered taker "
                                                                      "fee = 40 bps per fill"},
                {"id": "R6", "grid": "no_funding", "method": "funding zeroed on the historical "
                                                             "slice (descriptive reference)"},
                {"id": "R7", "grid": "no_funding_full", "method": "funding zeroed on the full "
                                                                 "window (descriptive reference)"},
                {"id": "R8", "grid": "historical", "method": "the selection window itself"},
                {"id": "R9", "grid": "oos", "method": "the frozen OOS window, read after selection"},
                {"id": "R10", "grid": "full", "method": "the whole registered window"}]},
        "data": {
            "source": "/data/raw (host /Volumes/ExpansionDrive/market-data-raw), read-only",
            "venue": "BINANCE USD-M perpetual",
            "data_start": counts.DATA["start"],
            "data_end": counts.DATA["end"],
            "historical_start": counts.DATA["historical_start"],
            "historical_end": counts.DATA["historical_end"],
            "oos_start": counts.DATA["oos_start"],
            "oos_end": counts.DATA["oos_end"],
            "split_immutability": "The split is fixed here, before any computation, and must not "
                                  "be moved afterwards.",
            "derived_store": "Built per attempt from raw into the rebuildable /qlib/work area via "
                             "upstream qlib dump_bin.py; read back exclusively through the qlib "
                             "data layer. Never written to /results. Bars are never resampled or "
                             "gap-filled.",
            "bar_labelling": {"raw_field": "open_time_ms",
                              "finding": "open_time_ms is the bar START in UTC (verified for the "
                                         "1h grid by the Strategy E pre-registration; this "
                                         "round re-measures it per timeframe at run time and "
                                         "publishes the readback)",
                              "consequence": "every signal is computed at a bar CLOSE and fills "
                                             "at the NEXT bar's open; no boundary is inferred "
                                             "from a label",
                              "grid_note": "the brick ladder is price-driven, so a missing bar "
                                           "is measured and disclosed (off_interval_grid_steps) "
                                           "rather than fatal; only the ordering is asserted"},
            "funding_series_contract": {
                "registered_window": "2022-01-01T00:00:00Z .. 2026-09-11T23:59:59Z",
                "engine_rule": "the position is exposed to every settlement whose raw instant "
                               "lies in the CLOSED interval [entry instant - 1s, exit instant + "
                               "1s]; each settlement is charged at its own instant on the "
                               "position notional at that instant",
                "no_fabrication": "the artifacts disclose the modelled/official split; no modelled "
                                  "row may be relabelled official"}},
        "costs": {
            "source": "canonical instrument metadata (raw binance/usdm/instruments/"
                      "usdm-perp-instruments.json), read at run time, never hard-coded",
            "maker_fee": counts.COSTS["maker_fee"],
            "taker_fee": counts.COSTS["taker_fee"],
            "margin_init": 0.1,
            "margin_maint": 0.1,
            "apply_rule": "every fill is a market order: taker fee on the full traded notional, "
                          "plus adverse slippage of baseline_slippage_ticks instrument ticks. No "
                          "maker fills are assumed anywhere.",
            "baseline_slippage_ticks": counts.COSTS["baseline_slippage_ticks"],
            "slippage_sign_rule": "ADVERSE in both directions: an entry or scale-in pays up for a "
                                  "buy and sells lower for a sell, and an exit is the mirror "
                                  "(the price always moves against the position).",
            "baseline_slippage_provenance": "research assumption of this round (NOT "
                                            "user-specified); robustness re-measures it at 2 "
                                            "ticks",
            "funding": "charged per settlement inside the holding interval (rate x position "
                       "notional); funding_2x doubles it and the no_funding grids zero it "
                       "(descriptive)",
            "funding_exposure_rule": "The position is exposed to every funding settlement whose "
                                     "raw instant lies in the CLOSED interval [entry instant - "
                                     "1s, exit instant + 1s].",
            "cost_attrition_grid": "cost_attrition_40bps applies 8x the registered taker fee = 40 "
                                   "bps per fill",
            "funding_immutability": "the funding baseline must not be swapped for a modelled "
                                    "series to improve the outcome, nor vice versa"},
        "authorization_invariants": {
            "provenance_class": "USER_FIXED - only items with explicit operator evidence. A "
                                "searched axis, a project constant, or a signal-mechanics field "
                                "must NOT be listed here (contract 7.2 v1.3.1).",
            "starting_equity_usdt": 30000,
            "numeraire": "USDT (sole)",
            "venue": "BINANCE USD-M linear perpetual",
            "leverage": "10x (1 / margin_init)",
            "tranches": 12,
            "tranche_12": "reserve/buffer, never routinely deployed",
            "routine_active_levels": 11,
            "initial_entry_and_scale_ins": "signal-triggered initial entry plus adverse-price "
                                           "scale-ins down the registered ladder",
            "reduce_only_exit": "every exit is reduce-only; the book is FLAT after it",
            "same_bar_multi_level_ordering": "deterministic and conservative - see "
                                             "dca_execution_semantics.exit_order",
            "no_add_after_flat_or_kill": True,
            "direction_legs": "long AND short, mirrored by the rail; no unregistered leg is added"},
        "expected": {
            "cohorts": counts.CANONICAL["cohorts"],
            "strategy_cases_per_cohort": counts.CANONICAL["strategy_cases"],
            "dca_configs_per_cohort": counts.CANONICAL["dca_configs"],
            "base_combinations_per_cohort": counts.CANONICAL["base_combinations_per_cohort"],
            "cohort_grid_kinds": list(counts.COHORT_GRID_KINDS),
            "phase_grid_count": counts.CANONICAL["phase_grid_count"],
            "case_evaluations_per_cohort_per_grid": counts.CANONICAL["base_combinations_per_cohort"],
            "case_evaluations_per_cohort_all_grids": (
                counts.CANONICAL["base_combinations_per_cohort"]
                * counts.CANONICAL["phase_grid_count"]),
            "case_evaluations_per_grid": counts.CANONICAL["case_evaluations_per_grid"],
            "expected_case_evaluations": counts.CANONICAL["expected_case_evaluations"],
            "arithmetic": "20 cohorts (BTCUSDT/ETHUSDT/BNBUSDT/SOLUSDT x {5m,15m,30m,1h,4h}) x 9 "
                          "registered (brick_pct, rsi_period) cases x 48 DCA configs = 8,640 base "
                          "combinations per phase grid; x 10 phase grids = 86,400 full case "
                          "evaluations.",
            "runtime_expectation": {
                "basis": "measured on this machine: Strategy A v2 (20 cohorts incl. 5m) evaluated "
                         "103,680 cells in 1,302 s; Strategy D (4 cohorts at 1h) 16,128 cells in "
                         "232 s",
                "estimate": "a detached run of roughly one to three hours (bins rebuild for five "
                            "grids + Renko/indicator precompute + 86,400 cell simulations) - an "
                            "estimate, not a guarantee",
                "status": "estimate only; the run is launched detached inside the container and "
                          "monitored from the attempt state/log, never by holding an agent turn "
                          "open"}},
        "non_goals": [
            "no parameter search beyond the registered strategy case set and the registered DCA "
            "domain",
            "no intraday-resampling or synthetic Renko feed outside the registered construction",
            "no second backtest engine, no Nautilus path, no live/paper execution",
            "no post-hoc widening or narrowing of the eligible universe",
            "no re-adjudication of the record's intake decision"],
        "artifacts_required": ["result.json", "artifacts/cohort_results.json",
                               "artifacts/cohort_survivors.json", "artifacts/assertions.json",
                               "artifacts/dca_layer_histogram.json",
                               "artifacts/grid_historical.csv", "artifacts/grid_oos.csv",
                               "artifacts/grid_full.csv", "artifacts/grid_fee_2x.csv",
                               "artifacts/grid_funding_2x.csv",
                               "artifacts/grid_entry_delay_1_bar.csv",
                               "artifacts/grid_slippage_2ticks.csv",
                               "artifacts/grid_no_funding.csv",
                               "artifacts/grid_no_funding_full.csv",
                               "artifacts/grid_cost_attrition_40bps.csv",
                               "artifacts/renko_signals.json", "artifacts/funding_series.json",
                               "artifacts/input_manifest.json", "artifacts/bins_build.json"],
        "assertions_declared": [
            "episodes_partition", "pnl_decomposition", "coverage_complete",
            "cohort_count_matches_registered", "strategy_grid_is_registered_product",
            "dca_grid_is_registered_product", "base_combinations_per_cohort_per_grid",
            "expected_case_evaluations", "layer0_equals_episodes", "layer_histogram_nonempty",
            "no_entry_after_exhaustion", "ending_equity_floor", "selector_deterministic",
            "selector_historical_only", "bar_grid_is_strictly_ascending",
            "no_unregistered_strategy_case", "brick_cap_never_reached",
            "funding_bar_never_out_of_hold", "no_funding_grid_is_cost_free",
            "fee_2x_track_is_not_a_noop", "funding_2x_track_is_not_a_noop",
            "cost_attrition_track_is_not_a_noop", "no_funding_track_is_cost_free",
            "entry_delay_track_is_not_a_noop", "slippage_track_is_not_a_noop",
            "daily_series_is_slice_scoped"],
        "parameter_contract": parameter_contract(),
    }


def run_spec_template():
    cases = [{"brick_pct": b, "rsi_period": r} for b, r in counts.STRATEGY_CASES]
    dca_grid = [{"spacing_pct": sp, "size_multiplier": m, "breakeven_tp_pct": tp,
                 "invalidation_pct": iv, "base_quote": 1000}
                for sp in counts.DCA_GRID["spacing_pct"]
                for m in counts.DCA_GRID["size_multiplier"]
                for tp in counts.DCA_GRID["breakeven_tp_pct"]
                for iv in counts.DCA_GRID["invalidation_pct"]]
    statuses = {
        "base_quote_status": counts.CONSTANT_STATUS + " - held constant across the whole domain "
                                                      "(it is NOT a search axis) and NOT a "
                                                      "user-fixed invariant.",
        "spacing_pct_status": counts.SEARCH_STATUS + " - this axis IS searched over the "
                                                     "registered values.",
        "size_multiplier_status": counts.SEARCH_STATUS + " - this axis IS searched over the "
                                                        "registered values.",
        "breakeven_tp_pct_status": counts.SEARCH_STATUS + " - this axis IS searched over the "
                                                          "registered values.",
        "invalidation_pct_status": counts.SEARCH_STATUS + " - this axis IS searched over the "
                                                          "registered values.",
        "brick_pct_status": counts.SEARCH_STATUS + " - this axis IS searched over the registered "
                                                   "values.",
        "rsi_period_status": counts.SEARCH_STATUS + " - this axis IS searched over the registered "
                                                    "values.",
        "entry_timing_status": counts.RESEARCH_MARKER + " - a crossing detected at a bar close "
                                                        "fills at the NEXT bar's open."}
    return {
        "schema_version": 1,
        "document_kind": "run-spec (frozen attempt contract TEMPLATE; instantiation may "
                         "substitute only the seven registered placeholders, the pinned script "
                         "sha256 values and the template-era status prose)",
        "template_instantiation": {
            "placeholders": ["{{round_id}}", "{{run_id}}", "{{task_id}}", "{{created_at_utc}}",
                             "{{script_sha256}}", "{{engine_selfcheck_sha256}}",
                             "{{round_spec_path}}"],
            "rule": "Domains, split, costs, gates and the selector/disposition versions are FROZEN "
                    "before the first run and must be copied from the instantiated "
                    "round-spec.json. Only the seven placeholders may be substituted. "
                    "script.sha256 pins the deployed runner and is recomputed host side by "
                    "preflight P10.",
            "validated_by": "python3 runtime/strategy_f_v1_counts.py --spec "
                            "runtime/templates/strategy_f_v1_round_spec.template.json --run-spec "
                            "runtime/templates/strategy_f_v1_run_spec.template.json",
            "status": "PREREGISTRATION ONLY / NOT LAUNCHED"},
        "family_id": counts.FAMILY_ID,
        "round_id": "{{round_id}}",
        "run_id": "{{run_id}}",
        "task_id": "{{task_id}}",
        "kanban_board": "quant-strategy-research",
        "created_at_utc": "{{created_at_utc}}",
        "round_spec_path": "{{round_spec_path}}",
        "selector_version": "cohort-selector-v1",
        "disposition_version": "cohort-disposition-v1",
        "data": {
            "source": "/data/raw",
            "read_only": True,
            "symbols": list(counts.SYMBOLS),
            "timeframes": [dict(tf) for tf in counts.TIMEFRAMES],
            "start": counts.DATA["start"],
            "end": counts.DATA["end"],
            "historical_start": counts.DATA["historical_start"],
            "historical_end": counts.DATA["historical_end"],
            "oos_start": counts.DATA["oos_start"],
            "oos_end": counts.DATA["oos_end"],
            "bar_labelling": "open_time_ms = bar START (UTC); the runner re-measures each "
                             "timeframe's grid and publishes the readback; the brick ladder is "
                             "price-driven so a missing bar is disclosed, not fatal",
            "funding_series_contract": {
                "engine_rule": "exposure to every settlement in the CLOSED interval [entry "
                               "instant - 1s, exit instant + 1s], charged on the position "
                               "notional at that instant",
                "no_fabrication": "the modelled/official split is disclosed in the artifacts"}},
        "parameter_domain": {
            "grid_cases": cases,
            "legal_cases_per_cohort": counts.CANONICAL["strategy_cases"],
            "brick_pct_grid": list(counts.BRICK_PCT_GRID),
            "rsi_period_grid": list(counts.RSI_PERIOD_GRID),
            "renko_contract": counts.RENKO_CONTRACT,
            "indicator_contract": counts.INDICATOR_CONTRACT,
            "entry_timing_status": statuses["entry_timing_status"],
            "brick_pct_status": statuses["brick_pct_status"],
            "rsi_period_status": statuses["rsi_period_status"]},
        "dca_domain": {
            "base_quote": 1000,
            "spacing_pct": list(counts.DCA_GRID["spacing_pct"]),
            "size_multiplier": list(counts.DCA_GRID["size_multiplier"]),
            "breakeven_tp_pct": list(counts.DCA_GRID["breakeven_tp_pct"]),
            "invalidation_pct": list(counts.DCA_GRID["invalidation_pct"]),
            "grid": dca_grid,
            **statuses},
        "provenance_mirror": {k: statuses[k] for k in statuses},
        "gates": dict(counts.GATES),
        "costs": {
            "source": "canonical instrument metadata, read at run time",
            "maker_fee": counts.COSTS["maker_fee"],
            "taker_fee": counts.COSTS["taker_fee"],
            "baseline_slippage_ticks": counts.COSTS["baseline_slippage_ticks"],
            "slippage_sign_rule": "ADVERSE for every fill in both directions",
            "cost_attrition_fee_mult": counts.COSTS["cost_attrition_fee_mult"],
            "funding": "charged per settlement inside the holding interval",
            "funding_exposure_rule": "CLOSED interval [entry - 1s, exit + 1s]",
            "fee_bps": 5.0,
            "slippage_bps": None},
        "expected": {
            "cohorts": counts.CANONICAL["cohorts"],
            "strategy_cases_per_cohort": counts.CANONICAL["strategy_cases"],
            "dca_configs_per_cohort": counts.CANONICAL["dca_configs"],
            "base_combinations_per_cohort": counts.CANONICAL["base_combinations_per_cohort"],
            "case_evaluations_per_grid": counts.CANONICAL["case_evaluations_per_grid"],
            "expected_case_evaluations": counts.CANONICAL["expected_case_evaluations"],
            "cohort_grid_kinds": list(counts.COHORT_GRID_KINDS)},
        "script": {"path": "/scripts/60_strategy_f_run.py", "sha256": "{{script_sha256}}"},
        "engine_selfcheck": {"script": "/scripts/tests/test_strategy_f_engine.py",
                             "sha256": "{{engine_selfcheck_sha256}}",
                             "must_run_before_launch": True},
        "expected_outputs": ["result.json", "artifacts/cohort_results.json",
                             "artifacts/cohort_survivors.json", "artifacts/assertions.json",
                             "artifacts/dca_layer_histogram.json"],
        "falsification": [
            "no signal / insufficient trades (G2 floor: fewer than 30 historical episodes in the "
            "cohort's best case, or fewer than 10 in the winner's OOS slice; culls the cohort)",
            "OOS economic failure of the frozen winner cell (G4: net_pnl <= 0 or sharpe <= 0)",
            "full-window economic failure of the frozen winner cell (G5: net_pnl <= 0)",
            "execution-cost failure (G6: any of fee_2x / funding_2x / entry_delay_1_bar / "
            "slippage_2ticks turns the frozen cell's net PnL non-positive)",
            "cost-attrition failure (G8: net_pnl <= 0 at 40 bps per fill)",
            "parameter-neighbourhood instability (G7: fewer than 60% of legal face-adjacent "
            "neighbours agree in historical net-PnL sign)",
            "family-level cost boundary: a winner's full-window net-PnL sign flips between the "
            "canonical track and cost_attrition_40bps (never PASS)",
            "family-level symbol concentration: one symbol carries more than 70% of the positive "
            "OOS net PnL among survivors (never PASS)",
            "family-level OOS grid instability: a winner's legal neighbours disagree in OOS "
            "net-PnL sign on 40% or more of the neighbours (never PASS)"],
        "notes": "The runner reads ONLY this file for parameters; it re-hashes itself against "
                 "script.sha256 before doing anything, and it writes only into the attempt "
                 "directory and /qlib/work."}


def main():
    ap = argparse.ArgumentParser(description="author the frozen Strategy F v1 templates")
    ap.add_argument("--out-dir", default=os.path.join(HERE, "templates"))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)
    instruments = measure_instruments()
    round_tpl = round_spec_template(instruments)
    run_tpl = run_spec_template()
    # a template is not an instance: it must satisfy the counts/provenance checks with the
    # placeholders still in place, otherwise the instantiation can never pass them
    probe = json.loads(json.dumps(round_tpl))
    probe["round_id"] = "stochastic-rsi-renko-2026-08-31-r1"
    probe["kanban_task_id"] = "t_PLACEHOLDER"
    probe["created_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    probe["semantic_fingerprint"]["semantic_fingerprint"] = counts.fingerprint(
        probe["semantic_fingerprint"]["fingerprint_input"])
    _, problems, _ = counts.check(probe, None)
    pc_problems = pc.validate_round_spec_contract(probe)
    # the run-spec is validated against the same probe, with the placeholders substituted
    rprobe = json.loads(json.dumps(run_tpl))
    for token, value in (("{{round_id}}", probe["round_id"]), ("{{run_id}}", probe["round_id"] + "-u1"),
                         ("{{task_id}}", probe["kanban_task_id"]),
                         ("{{created_at_utc}}", probe["created_at_utc"]),
                         ("{{script_sha256}}", "sha256:" + "0" * 64),
                         ("{{engine_selfcheck_sha256}}", "sha256:" + "1" * 64),
                         ("{{round_spec_path}}", "/results/%s/rounds/%s/round-spec.json"
                          % (counts.FAMILY_ID, probe["round_id"]))):
        rprobe = json.loads(json.dumps(rprobe).replace(token, value))
    _, run_problems, _ = counts.check(probe, rprobe)
    problems = problems + pc_problems + run_problems
    record = {"family_id": counts.FAMILY_ID, "instruments": instruments,
              "counts": counts.CANONICAL, "problems": problems,
              "round_template": os.path.join(args.out_dir, ROUND_TEMPLATE_NAME),
              "run_template": os.path.join(args.out_dir, RUN_TEMPLATE_NAME)}
    if problems:
        print(json.dumps(record, indent=2, ensure_ascii=False))
        print("REFUSING to write templates: %d problem(s)" % len(problems))
        return 1
    for path, doc in ((record["round_template"], round_tpl), (record["run_template"], run_tpl)):
        with open(path, "w") as fh:
            json.dump(doc, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
    # read back and re-validate the persisted bytes
    rb_round = json.load(open(record["round_template"]))
    rb_run = json.load(open(record["run_template"]))
    rb_probe = json.loads(json.dumps(rb_round))
    rb_probe["round_id"] = probe["round_id"]
    rb_probe["kanban_task_id"] = probe["kanban_task_id"]
    rb_probe["created_at_utc"] = probe["created_at_utc"]
    rb_probe["semantic_fingerprint"]["semantic_fingerprint"] = counts.fingerprint(
        rb_probe["semantic_fingerprint"]["fingerprint_input"])
    _, rb_problems, _ = counts.check(rb_probe, None)
    record["problems"] = rb_problems
    record["round_template_sha256"] = counts.fingerprint(open(record["round_template"]).read())
    record["run_template_sha256"] = counts.fingerprint(open(record["run_template"]).read())
    if args.json:
        print(json.dumps(record, indent=2, ensure_ascii=False))
    else:
        print("Strategy F v1 templates authored: %s (%d bytes), %s (%d bytes); read-back %d "
              "problem(s)" % (record["round_template"],
                              os.path.getsize(record["round_template"]),
                              record["run_template"], os.path.getsize(record["run_template"]),
                              len(rb_problems)))
    return 0 if not rb_problems else 1


if __name__ == "__main__":
    sys.exit(main())
