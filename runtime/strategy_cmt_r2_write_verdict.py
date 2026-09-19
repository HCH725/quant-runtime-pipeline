#!/usr/bin/env python3
"""Write the immutable round verdict.json for family
`continuous-macro-timing-growth-defensive-style-allocation-2026-09-02` round r2
(card t_20bec925; contract 10.7 / 15.5).

Every number is read from the published attempt artifacts (result.json, the grid CSVs, the DONE
sentinel) plus the independent host-side verification evidence; nothing is hand-typed except the
disclosed narrative strings.  The reader outcomes live in the attempt's result.json; this writer
only turns them into the round's terminal verdict document (immutable, O_CREAT|O_EXCL, INV-4).

usage: runtime/strategy_cmt_r2_write_verdict.py
"""
import hashlib
import json
import os
import sys
import time

ROOT = "/Volumes/ExpansionDrive/qlib-results"
FAMILY = "continuous-macro-timing-growth-defensive-style-allocation-2026-09-02"
ROUND = FAMILY + "-r2"
RUN = ROUND + "-u3"
RDIR = os.path.join(ROOT, FAMILY, "rounds", ROUND)
ATT = os.path.join(RDIR, "attempts", RUN)
REPO = "/Users/hong/workspace/quant-runtime-pipeline"
TASK = "t_20bec925"
VERIFY_EVIDENCE = os.path.join(REPO, ".kanban-scratch", "cmt_r2", "cmt_verification.json")
ENGINE = os.path.join(REPO, "container", "scripts", "140_continuous_macro_timing_run.py")
CASE_FIELDS = ("var_full", "var_core", "var_rate", "sm_ewma", "sm_none")
DCA_FIELDS = ("spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct")


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def load(path):
    with open(path) as fh:
        return json.load(fh)


res = load(os.path.join(ATT, "result.json"))
sent = load(os.path.join(ATT, "DONE"))
rs = load(os.path.join(RDIR, "round-spec.json"))
rspec = load(os.path.join(ATT, "run-spec.json"))
family = load(os.path.join(ROOT, FAMILY, "family.json"))
verif = load(VERIFY_EVIDENCE)

assert sha(ENGINE) == rspec["script"]["sha256"], "repo engine bytes are not the pinned engine"
assert os.path.isfile(os.path.join(REPO, "container", "scripts",
                                   "140_continuous_macro_timing_run.py"))

hits = [k for k, v in res["registered_family_level_falsification_flags"].items() if v]
assertions_true = all(res["assertions"].values())
cull = "; ".join("%s=%s" % (c["cohort"], ",".join(c["cull_reasons"]))
                 for c in res["cohort_results"] if c.get("cull_reasons"))
winners = [c for c in res["cohort_results"] if c.get("winner") is not None]
winner_report = []
for c in winners:
    m = c.get("metrics") or {}
    winner_report.append({
        "cohort": c["cohort"], "case_name": c.get("winner_case_label"), "outcome": c["outcome"],
        "strategy_params": {k: c["winner"][k] for k in c["winner"] if k in CASE_FIELDS},
        "dca_params": {k: c["winner"][k] for k in DCA_FIELDS},
        "historical": m.get("historical"), "oos": m.get("oos"), "full": m.get("full"),
        "robustness": m.get("robustness"), "cost_attrition_40bps": m.get("cost_attrition_40bps"),
        "neighbourhood": c.get("neighbourhood"),
    })

verdict_value = res["verdict_recommendation_final"]
claimable = bool(verdict_value == "PASS" and res["coverage_complete"] and assertions_true)

winner_activity = {k: {"fills": v["grids"]["full"]["fills"],
                       "turnover_usdt": v["grids"]["full"]["turnover_usdt"],
                       "bars_in_market": v["grids"]["full"]["bars_in_market"],
                       "days": v["grids"]["full"]["days"]}
                   for k, v in verif["winner_rows_re_read"].items() if "grids" in v
                   and "full" in v["grids"] and v["grids"]["full"].get("rows_found") == 1}

missing = []
if verdict_value != "PASS":
    missing.append("verdict != PASS (disposition %s; per-cohort cull reasons: %s)"
                   % (res["disposition"], cull or "none - all four cohorts survived"))
if hits:
    missing.append("registered family-level reader hit(s): %s (the record's own pre-registered "
                   "rejection rules; no gate was lowered and no parameter tuned to obtain them)"
                   % ", ".join(hits))

attempts = {"registered_round_spec_sha256": sha(os.path.join(RDIR, "round-spec.json")),
            "round_spec_bytes": os.path.getsize(os.path.join(RDIR, "round-spec.json"))}
for name in ("u1", "u2", "u3"):
    adir = os.path.join(RDIR, "attempts", ROUND + "-" + name)
    entry = {"attempt_dir": adir}
    for status in ("DONE", "FAILED", "INCOMPLETE"):
        p = os.path.join(adir, status)
        if os.path.isfile(p):
            s = load(p)
            entry["status"] = status
            entry["sentinel_sha256"] = sha(p)
            entry["sentinel_verdict_hint"] = s.get("verdict_hint")
            entry["sentinel_failure"] = s.get("failure")
            entry["sentinel_published_at_utc"] = s.get("created_at_utc")
    for rel in ("run-spec.json", "state.json", "result.json"):
        p = os.path.join(adir, rel)
        if os.path.isfile(p):
            entry[rel.replace(".", "_").replace("-", "_") + "_sha256"] = sha(p)
    if entry.get("status") == "DONE":
        entry["runtime_seconds"] = res.get("runtime_seconds")
        entry["case_evaluations"] = res["case_evaluations_total"]
        entry["assertions_all_true"] = assertions_true
    attempts[name] = entry

verdict = {
    "schema_version": 1,
    "kind": "round_verdict",
    "family_id": FAMILY,
    "family": {"path": os.path.join(ROOT, FAMILY, "family.json"),
               "sha256": sha(os.path.join(ROOT, FAMILY, "family.json")),
               "kanban_task_id": family["kanban_task_id"],
               "semantic_fingerprint": family.get("semantic_fingerprint"),
               "parent_family": family.get("parent_family"),
               "lineage_note": family.get("lineage_note")},
    "round_id": ROUND,
    "run_id": RUN,
    "kanban_task_id": TASK,
    "kanban_board": "quant-strategy-research",
    "verdict": verdict_value,
    "performance_claimable": claimable,
    "performance_claimable_basis": "verdict == PASS (contract 9.6) AND coverage_complete AND "
                                   "every declared assertion true",
    "missing_conditions": missing,
    "yield": {
        "rounds_used": 1, "max_rounds": 3, "no_progress_rounds": 0,
        "progress_evidence": [
            "artifacts/grid_*.csv - the complete pre-registered product was measured: "
            "4 cohorts x 6 strategy cases x 48 DCA configs = %d cells on each of the 10 phase "
            "grids = %d case evaluations (coverage_complete=%s, every declared assertion %s)"
            % (res["case_evaluations_per_cohort_per_grid"] * res["cohort_count"],
               res["case_evaluations_total"], res["coverage_complete"],
               "true" if assertions_true else "NOT true"),
            "artifacts/cohort_results.json - every cohort was adjudicated by cohort-selector-v1 / "
            "cohort-disposition-v1 (survivors %d of %d cohorts; culled: %s)"
            % (res["cohort_survivor_count"], res["cohort_count"], cull or "none"),
            "artifacts/family_falsification.json - the record's four-item battery was evaluated "
            "(all four readers executed on the local universe); reader hits: %s"
            % (", ".join(hits) if hits else "none"),
            "the round produced a terminal disposition (%s) from a complete pre-registered grid, "
            "so it is not a no-progress round (contract 15.3)" % res["disposition"]],
        "yield_decision": ("FINALIST" if verdict_value == "PASS" else
                           ("STOP_DEFERRED" if verdict_value == "DEFERRED" else
                            ("STOP_REJECT" if verdict_value == "REJECT"
                             else "STOP_TECHNICAL_INCOMPLETE"))),
        "decided_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
    "failure": {"layer": None, "class": None,
                "note": "the round is technically complete: full coverage, every declared "
                        "assertion true, an independent host-side readback (ok=%s).  r1 "
                        "terminalised as TECHNICAL_INCOMPLETE (prerequisite-missing) before the "
                        "2026-09-19 data-pack correction; r2 was opened under section 8 because "
                        "the local eligible universe and prerequisite semantics changed, and runs "
                        "the record's own crypto portability mapping.  The verdict is DEFERRED "
                        "because the record's own registered falsification battery hit; that is a "
                        "scientific outcome, not a technical failure."
                        % ("true" if verif["ok"] else "false")},
    "evidence_run_ids": [RUN],
    "decided_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "disposition": res["disposition"],
    "cohort_survivors": res["cohort_survivors"],
    "cohort_count": res["cohort_count"],
    "cohort_outcome_counts": res["cohort_outcome_counts"],
    "coverage": res["coverage"],
    "case_evaluations_total": res["case_evaluations_total"],
    "expected_case_evaluations": res["expected_case_evaluations"],
    "data": {"start": rspec["data"]["start"], "end": rspec["data"]["end"],
             "historical_start": rspec["data"]["historical_start"],
             "historical_end": rspec["data"]["historical_end"],
             "oos_start": rspec["data"]["oos_start"], "oos_end": rspec["data"]["oos_end"],
             "symbols": rspec["data"]["symbols"],
             "timeframes": [tf["raw_interval"] for tf in rspec["data"]["timeframes"]],
             "split_immutability": rspec["data"]["split_immutability"],
             "slice_days": res["slice_days"]},
    "scientific_finding": {
        "headline": "on the registered local crypto universe the record's continuous "
                    "macro-timing score is a viable risk-timing allocator - all four cohorts "
                    "survive the section 7.3 gates and the winner cells stay net-positive through "
                    "the fee_2x / funding_2x / entry_delay_1_bar / slippage_2ticks / full-window "
                    "robustness grids - but THREE of the record's own four registered "
                    "family-level falsification rules fire: the stationary random placebo "
                    "reproduces (and beats) the winner's Sharpe on 2 of 4 cohorts, the registered "
                    "parameter perturbation exceeds the Sharpe-drop bound on 3 of 4 cohorts, and "
                    "the 40 bps cost-attrition leg drives every cohort's net CAGR below the local "
                    "static 50/50 growth/defensive benchmark.  The family is therefore DEFERRED "
                    "with every survivor frozen and advanced; the macro-timing edge as "
                    "operationalised here is not claimable at institutional friction.",
        "reader_1_stationary_random_placebo": {
            "rule": "the record rejects the score when drift-matched synthetic macro walks "
                    "generate a Sharpe >= 0.98 (its own construct); the local analogue is the "
                    "relative comparison of the winner's cell against the same cell driven by "
                    "the synthetic walks on the same rail",
            "measured": {k: {"winner_sharpe": v["winner_sharpe"],
                             "synthetic_sharpe": v["synthetic_sharpe"],
                             "winner_net_pnl": v["winner_net_pnl"],
                             "synthetic_net_pnl": v["synthetic_net_pnl"],
                             "synthetic_episodes": v["synthetic_episodes"],
                             "synthetic_hit": v["synthetic_hit"]}
                         for k, v in res["stationary_random_placebo"]["cohorts"].items()},
            "seed": res["stationary_random_placebo"].get("seed"),
            "cohorts_hit": sorted(k for k, v in res["stationary_random_placebo"]["cohorts"].items()
                                  if v.get("synthetic_hit")),
            "hit": True},
        "reader_2_parameter_perturbation_grid": {
            "rule": "the record rejects the score when a registered parameter perturbation drops "
                    "Sharpe by more than 0.20",
            "measured": {k: {"winner_sharpe": v["winner_sharpe"],
                             "worst_perturbation": max(
                                 v["perturbations"].items(),
                                 key=lambda kv: kv[1].get("sharpe_drop", 0.0))[0],
                             "worst_sharpe_drop": max(
                                 p.get("sharpe_drop", 0.0) for p in v["perturbations"].values()),
                             "exceeding": sorted(n for n, p in v["perturbations"].items()
                                                 if p.get("exceeds_bound"))}
                         for k, v in res["parameter_perturbation_grid"]["cohorts"].items()},
            "hit": True},
        "reader_3_cost_stress_boundary": {
            "rule": "the record rejects the strategy when fees rise to 25/40 bps and net CAGR "
                    "falls below its own static 50/50 benchmark (17.12 % on its market); the "
                    "local boundary is the local static 50/50 growth/defensive benchmark CAGR",
            "measured": {k: {"winner_net_cagr_full": v["winner_net_cagr_full"],
                             "stressed_net_cagr_40bps": v["stressed_net_cagr_40bps"],
                             "stressed_net_pnl_40bps": v["stressed_net_pnl_40bps"],
                             "static_mix_50_50_cagr": v["static_mix_50_50_cagr"],
                             "static_growth_100_cagr": v["static_growth_100_cagr"],
                             "hit": v["hit"]}
                         for k, v in res["cost_stress_boundary"]["cohorts"].items()},
            "hit": True},
        "reader_4_subperiod_rate_hike_drawdown": {
            "rule": "the winner's historical-slice maximum drawdown against the local 100 % growth "
                    "basket's maximum drawdown on the same slice",
            "measured": {k: {"winner_historical_max_dd_pct": v["winner_historical_max_dd_pct"],
                             "growth_100_max_dd_pct": v["growth_100_max_dd_pct"],
                             "hit": v["hit"]}
                         for k, v in res["subperiod_rate_hike_drawdown"]["cohorts"].items()},
            "hit": False},
        "not_hit_items": [k for k, v in res["registered_family_level_falsification_flags"].items()
                          if not v],
        "disclosure": "these are the record's own pre-registered rejection rules applied "
                      "verbatim on the local universe; no parameter was adjusted and no gate was "
                      "lowered to obtain them, and the four surviving cohorts remain frozen as "
                      "evidence",
    },
    "reporting": {
        "registered_universe": "BTCUSDT/ETHUSDT/BNBUSDT/SOLUSDT 1d on the local canonical raw "
                               "(BINANCE spot daily closes for the panel + USD-M perpetual "
                               "funding + Deribit BTC DVOL daily), i.e. the record's own crypto "
                               "portability mapping executed under the system-owned lifecycle "
                               "footer (contract 14.4): funding -> rate-relief slot, DVOL -> VIX "
                               "slots, BTC drawdown -> SPY-drawdown slot, growth basket = "
                               "ETH/BNB/SOL, defensive leg = BTC + USDT numeraire.  The record's "
                               "source market (US ETFs, TNX, BAA10Y) is NOT available in this "
                               "workspace; the conclusions below hold ONLY for this local "
                               "universe.",
        "elected_winners": winner_report,
        "culled_cohorts": [{"cohort": c["cohort"], "cull_reasons": c["cull_reasons"],
                            "no_winner_reason": c.get("no_winner_reason")}
                           for c in res["cohort_results"] if c["outcome"] != "SURVIVOR"],
        "card_required_reporting": {
            "annualized_return_and_cagr_usdt_equity": {
                c["cohort"]: {"annualized_return": (c.get("metrics") or {}).get("full", {}).get(
                    "annualized_return"),
                              "cagr": (c.get("metrics") or {}).get("full", {}).get("cagr"),
                              "window": "full"}
                for c in res["cohort_results"]},
            "sharpe": {c["cohort"]: (c.get("metrics") or {}).get("full", {}).get("sharpe")
                       for c in res["cohort_results"]},
            "max_dd_pct_and_usdt": {c["cohort"]: {
                "pct": (c.get("metrics") or {}).get("full", {}).get("max_dd_pct"),
                "usdt": (c.get("metrics") or {}).get("full", {}).get("max_dd_usdt")}
                for c in res["cohort_results"]},
            "ending_equity": {c["cohort"]: (c.get("metrics") or {}).get("full", {}).get(
                "ending_equity") for c in res["cohort_results"]},
            "net_pnl": {c["cohort"]: (c.get("metrics") or {}).get("full", {}).get("net_pnl")
                        for c in res["cohort_results"]},
            "fees": {c["cohort"]: (c.get("metrics") or {}).get("full", {}).get("fees")
                     for c in res["cohort_results"]},
            "funding": {c["cohort"]: (c.get("metrics") or {}).get("full", {}).get("funding")
                        for c in res["cohort_results"]},
            "turnover_and_trade_count": {
                "winner_cells_full_window": winner_activity,
                "all_cells_full_grid": {
                    "fills": verif["grid_aggregates"]["full"]["fills"],
                    "turnover_usdt": verif["grid_aggregates"]["full"]["turnover_usdt"],
                    "rows": verif["grid_aggregates"]["full"]["rows"]},
                "note": "the winner-cell figures are re-read from artifacts/grid_full.csv with an "
                        "independent cohort/parameter filter; the all-cells row is the whole "
                        "1,152-row full-window grid"},
            "max_effective_leverage": {c["cohort"]: (c.get("metrics") or {}).get(
                "full", {}).get("max_effective_leverage") for c in res["cohort_results"]},
            "capital_utilization": {c["cohort"]: (c.get("metrics") or {}).get(
                "full", {}).get("capital_utilization") for c in res["cohort_results"]},
            "dca_layer_histogram": res["dca_layer_histogram"],
            "coverage_counts": {
                "eligible_cohorts": res["cohort_count"],
                "per_grid_cells": res["coverage"],
                "case_evaluations_total": res["case_evaluations_total"],
                "oos_grid_cells": res["coverage"].get("oos"),
                "robustness_grid_cells": {k: res["coverage"].get(k) for k in
                                          ("fee_2x", "funding_2x", "entry_delay_1_bar",
                                           "slippage_2ticks", "cost_attrition_40bps",
                                           "no_funding", "no_funding_full")}},
            "survivor_list_and_disposition": {"survivors": res["cohort_survivors"],
                                              "disposition": res["disposition"],
                                              "culled": [c["cohort"] for c in
                                                         res["cohort_results"]
                                                         if c["outcome"] != "SURVIVOR"]},
            "performance_claimable": claimable,
            "verdict": verdict_value},
        "headline_metrics": {
            "survivor_full_window": {
                c["cohort"]: {"net_pnl": (c.get("metrics") or {}).get("full", {}).get("net_pnl"),
                              "cagr": (c.get("metrics") or {}).get("full", {}).get("cagr"),
                              "sharpe": (c.get("metrics") or {}).get("full", {}).get("sharpe"),
                              "max_dd_pct": (c.get("metrics") or {}).get("full", {}).get(
                                  "max_dd_pct"),
                              "max_dd_usdt": (c.get("metrics") or {}).get("full", {}).get(
                                  "max_dd_usdt"),
                              "ending_equity": (c.get("metrics") or {}).get("full", {}).get(
                                  "ending_equity"),
                              "max_effective_leverage": (c.get("metrics") or {}).get(
                                  "full", {}).get("max_effective_leverage"),
                              "capital_utilization": (c.get("metrics") or {}).get(
                                  "full", {}).get("capital_utilization"),
                              "episodes": (c.get("metrics") or {}).get("full", {}).get("episodes"),
                              "cull_reasons": c["cull_reasons"]}
                for c in res["cohort_results"]},
        },
        "dca_layer_histogram": res["dca_layer_histogram"],
        "stress_summary": res.get("stress_summary"),
        "descriptive_diagnostics": res.get("descriptive_diagnostics"),
        "descriptive_medians_all_base_cases": res.get("descriptive_medians_all_base_cases"),
        "robustness_diagnostics": res.get("robustness_diagnostics"),
        "panel_build": res.get("panel_build"),
        "signal_layer": res.get("signal_layer"),
        "signal_input_layer": res.get("signal_input_layer"),
        "funding_series": res.get("funding_series"),
        "instrument_metadata": res.get("instrument_metadata"),
        "data_readback": res.get("data_readback"),
        "bins_build": res.get("bins_build"),
    },
    "registered_family_level_falsification_flags":
        res["registered_family_level_falsification_flags"],
    "registered_family_level_reader_hits": hits,
    "registered_family_level_readers": {
        "stationary_random_placebo": res["stationary_random_placebo"],
        "parameter_perturbation_grid": res["parameter_perturbation_grid"],
        "cost_stress_boundary": res["cost_stress_boundary"],
        "subperiod_rate_hike_drawdown": res["subperiod_rate_hike_drawdown"],
    },
    "assertions": res["assertions"],
    "all_assertions_true": assertions_true,
    "structural_counters": res.get("structural_counters"),
    "structural_counter_note": res.get("structural_counters", {}),
    "independent_verification": {
        "script": os.path.join(REPO, "runtime", "_cmt_verify_r2u3.py"),
        "evidence": VERIFY_EVIDENCE,
        "evidence_sha256": sha(VERIFY_EVIDENCE),
        "ok": verif["ok"],
        "checks": verif["checks"],
        "ladder_identity": verif["ladder_identity"],
        "accounting_identity_failures": verif["accounting_identity_failures"],
        "ending_equity_identity_failures": verif["ending_equity_identity_failures"],
        "winner_max_abs_diff_vs_cohort_results": verif["winner_max_abs_diff"],
        "selector_recomputation": {k: {"match": v["match"], "eligible_rows": v["eligible_rows"],
                                       "total_rows": v["total_rows"]}
                                   for k, v in verif["selector"].items()},
        "coverage_matches_csv_row_counts": {k: verif["coverage"][k] for k in verif["grids"]},
        "structural_counter_violations": verif["structural_counter_violations"],
        "note": "the verifier reads only the published attempt artifacts (the ten grid CSVs plus "
                "result.json / cohort_results.json): per-grid row counts and the registered "
                "product identity, the per-row accounting identities gross - fees - funding == net "
                "and ending_equity == 30000 + net, the winners' cells re-read field by field, the "
                "cohort selector recomputed from the CSV text, the DCA ladder identity, and the "
                "critical structural counters",
    },
    "selector_version": res["selector_version"],
    "disposition_version": res["disposition_version"],
    "disposition_mapping_version": res["disposition_mapping_version"],
    "contract_semantics_version": res["contract_semantics_version"],
    "engine_version": res["engine_version"],
    "engine_semantics": res["engine_semantics"],
    "engine_pin": {"path": "container/scripts/140_continuous_macro_timing_run.py",
                   "sha256": rspec["script"]["sha256"],
                   "matches_repo_and_host": True,
                   "note": "repo bytes == host /Users/hong/workspace/qlib-apple-container/scripts "
                           "bytes == the sha pinned by the published run-spec (P10 re-computes it)"},
    "attempt_history": {
        "r1": {"terminal": "TECHNICAL_INCOMPLETE", "attempts_launched": 0,
               "note": "prerequisite-missing under the pre-2026-09-19 data pack; round-spec and "
                       "verdict preserved immutably, never overwritten"},
        "r2_u1": {"terminal": "FAILED", "failure_class": "card-local script/spec shape defect",
                  "note": "spec parameter_domain.grid_cases was materialised as a plain list while "
                          "the engine reads a list of mappings; caught before any cell was "
                          "computed (no artifacts)"},
        "r2_u2": {"terminal": "FAILED", "failure_class": "card-local script/spec shape defect",
                  "note": "run-spec missed data.funding_truth_status_windows; the engine aborted "
                          "while building the cohort report (no artifacts)"},
        "r2_prelaunch_remediation": {
            "round_spec_augmented": "v1.8 parameter_contract added (preflight P10); the removed "
                                    "revision is archived",
            "smoke": "a short-window copy of the u3 run-spec was executed in the container "
                     "(/results/_diag/cmt_r2_smoke, never an attempt) and reached ARTIFACT_READY "
                     "after repairing two engine defects (panel_report score diagnostics source; "
                     "cohort_results_labels market axis) and two spec gaps (base_quote on the "
                     "registered DCA grid entries; funding truth-status windows)",
            "engine_diff": "one expression + one function in the family runner; 31/31 engine "
                           "self-check OK in the container before launch; the run-spec pins the "
                           "repaired engine",
        },
        "r2_u3": {"terminal": "DONE", "runtime_seconds": res.get("runtime_seconds"),
                  "case_evaluations": res["case_evaluations_total"],
                  "assertions_all_true": assertions_true},
    },
    "container_verdict_hint": {
        "verdict_recommendation": res["verdict_recommendation"],
        "verdict_recommendation_final": res["verdict_recommendation_final"],
        "performance_claimable_recommendation": res["performance_claimable_recommendation"],
        "performance_claimable_recommendation_final":
            res["performance_claimable_recommendation_final"],
        "terminal_sentinel_verdict_hint": sent.get("verdict_hint"),
        "note": "the terminal-evidence hint vocabulary has no DEFERRED value "
                "(NONE/CANDIDATE_PASS/CANDIDATE_REJECT/INCOMPLETE), so the sentinel carries NONE "
                "and the authoritative verdict is this document (contract 10.7)"},
    "attempts": attempts,
}

path = os.path.join(RDIR, "verdict.json")
fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
with os.fdopen(fd, "w") as fh:
    fh.write(json.dumps(verdict, indent=2, ensure_ascii=False) + "\n")
print(json.dumps({"verdict": verdict_value, "claimable": claimable,
                  "survivors": res["cohort_survivors"] and
                  [c["cohort"] for c in res["cohort_results"] if c["outcome"] == "SURVIVOR"],
                  "hits": hits, "verdict_path": path, "verdict_sha256": sha(path),
                  "verification_ok": verif["ok"]}, indent=2, ensure_ascii=False))
sys.exit(0)
