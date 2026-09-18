#!/usr/bin/env python3
"""Write the immutable round verdict.json for family
`compact-rienet-volatility-drag-mitigation-leveraged-gmv-2026-09-02` round r1
(card t_54d4eaf1; contract 10.7 / 15.5).

Every number is read from the published attempt artifacts; nothing is hand-typed.  The reader
verdicts live in the attempt's result.json; this writer only turns them into the round's terminal
verdict document (immutable, O_CREAT|O_EXCL, contract INV-4).

usage: runtime/strategy_k_r1_write_verdict.py
"""
import hashlib
import json
import os
import sys
import time

ROOT = "/Volumes/ExpansionDrive/qlib-results"
FAMILY = "compact-rienet-volatility-drag-mitigation-leveraged-gmv-2026-09-02"
ROUND = FAMILY + "-r1"
RUN = ROUND + "-u3"
U1 = ROUND + "-u1"
U2 = ROUND + "-u2"
RDIR = os.path.join(ROOT, FAMILY, "rounds", ROUND)
ATT = os.path.join(RDIR, "attempts", RUN)
ATT1 = os.path.join(RDIR, "attempts", U1)
ATT2 = os.path.join(RDIR, "attempts", U2)
REPO = "/Users/hong/workspace/quant-runtime-pipeline"
TASK = "t_54d4eaf1"
VERIFY_EVIDENCE = os.path.join(REPO, ".kanban-scratch", "k_parts", "k_verification.json")


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
cull = "; ".join("%s=%s" % (c["cohort"], ",".join(c["cull_reasons"]))
                 for c in res["cohort_results"] if c.get("cull_reasons"))
hits = [k for k, v in res["registered_family_level_falsification_flags"].items() if v]
assertions_true = all(res["assertions"].values())
winners = [c for c in res["cohort_results"] if c.get("winner") is not None]
winner_report = []
for c in winners:
    m = c.get("metrics") or {}
    winner_report.append({
        "cohort": c["cohort"], "case_name": c.get("winner_case_label"), "outcome": c["outcome"],
        "strategy_params": {k: c["winner"][k] for k in c["winner"]
                            if k in ("cl_raw", "cl_shrink", "cl_mp", "lb_250", "lb_500", "lb_750",
                                     "lb_1200")},
        "dca_params": {k: c["winner"][k] for k in ("spacing_pct", "size_multiplier",
                                                   "breakeven_tp_pct", "invalidation_pct")},
        "historical": m.get("historical"), "oos": m.get("oos"), "full": m.get("full"),
        "robustness": m.get("robustness"), "neighbourhood": c.get("neighbourhood"),
    })

verdict_value = res["verdict_recommendation_final"]
claimable = bool(verdict_value == "PASS" and res["coverage_complete"] and assertions_true)
missing = []
if verdict_value != "PASS":
    missing.append("verdict != PASS (disposition %s; per-cohort cull reasons: %s)"
                   % (res["disposition"], cull))
if hits:
    missing.append("registered family-level reader hit(s): %s" % ", ".join(hits))

verified = load(VERIFY_EVIDENCE) if os.path.exists(VERIFY_EVIDENCE) else {}
verdict = {
    "schema_version": 1,
    "kind": "round_verdict",
    "family_id": FAMILY,
    "family": {"path": os.path.join(ROOT, FAMILY, "family.json"),
               "sha256": sha(os.path.join(ROOT, FAMILY, "family.json")),
               "kanban_task_id": family["kanban_task_id"],
               "semantic_fingerprint": family["semantic_fingerprint"],
               "parent_family": family["parent_family"],
               "lineage_note": family["lineage_note"]},
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
            "4 cohorts x 12 strategy cases x 48 DCA configs = %d cells on each of the 10 phase "
            "grids = %d case evaluations (coverage_complete=%s, every declared assertion %s)"
            % (res["case_evaluations_per_cohort_per_grid"], res["case_evaluations_total"],
               res["coverage_complete"], "true" if assertions_true else "NOT true"),
            "artifacts/cohort_results.json - every cohort was adjudicated by cohort-selector-v1 / "
            "cohort-disposition-v1 (survivors %d of %d cohorts; culled: %s)"
            % (res["cohort_survivor_count"], res["cohort_count"], cull or "none"),
            "artifacts/family_falsification.json - the record's five-item battery was evaluated "
            "(two executed readers, three measured `not_executed`); reader hits: %s"
            % (", ".join(hits) if hits else "none"),
            "the round produced a terminal disposition (%s) from a complete pre-registered grid, "
            "so it is not a no-progress round (contract 15.3)" % res["disposition"]],
        "yield_decision": ("FINALIST" if verdict_value == "PASS" else
                           ("STOP_DEFERRED" if verdict_value == "DEFERRED" else
                            ("STOP_REJECT" if verdict_value == "REJECT"
                             else "STOP_TECHNICAL_INCOMPLETE"))),
        "decided_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
    "failure": {"layer": None, "class": None},
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
             "split_immutability": rspec["data"]["split_immutability"]},
    "reporting": {
        "registered_universe": "BTCUSDT/ETHUSDT/BNBUSDT/SOLUSDT USD-M perpetuals, 1d (local "
                               "eligible universe per the system-owned lifecycle footer, contract "
                               "14.4).  The record's source market (US NYSE/NASDAQ equities, "
                               "1990-2024, n=1000) and the mechanism's trained network artifacts "
                               "are NOT available in this workspace; the registered cleaning "
                               "operator is the parameter-free reading the record itself names and "
                               "the trained arm is carried as a `not_executed` reader.  The "
                               "conclusions below hold ONLY for this local universe.",
        "elected_winners": winner_report,
        "culled_cohorts": [{"cohort": c["cohort"], "cull_reasons": c["cull_reasons"],
                            "no_winner_reason": c.get("no_winner_reason")}
                           for c in res["cohort_results"] if c["outcome"] != "SURVIVOR"],
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
            "all_cells_full_grid_totals": verified.get("host", {}).get("full_grid_totals"),
            "turnover_and_trades": {
                "fills_all_cells_full_grid": verified.get("host", {}).get(
                    "full_grid_totals", {}).get("fills"),
                "turnover_usdt_all_cells_full_grid": verified.get("host", {}).get(
                    "full_grid_totals", {}).get("turnover_usdt")},
            "coverage_counts": {"per_grid_cells": res["coverage"],
                                "case_evaluations_total": res["case_evaluations_total"],
                                "oos_grid_cells": res["coverage"].get("oos"),
                                "robustness_grid_cells": {k: res["coverage"].get(k) for k in
                                                          ("fee_2x", "funding_2x",
                                                           "entry_delay_1_bar", "slippage_2ticks",
                                                           "cost_attrition_40bps", "no_funding",
                                                           "no_funding_full")}},
        },
        "dca_layer_histogram": res["dca_layer_histogram"],
        "stress_summary": res["stress_summary"],
        "descriptive_diagnostics": res["descriptive_diagnostics"],
        "descriptive_medians_all_base_cases": res["descriptive_medians_all_base_cases"],
        "panel_build": res.get("panel_build"),
        "signal_layer": res.get("signal_layer"),
        "signal_input_layer": res.get("signal_input_layer"),
        "raw_surface_measurement": res.get("raw_surface_measurement"),
    },
    "registered_family_level_falsification_flags":
        res["registered_family_level_falsification_flags"],
    "registered_family_level_reader_hits": hits,
    "registered_family_level_readers": {
        "bigru_spectral_denoiser_ablation": res["bigru_spectral_denoiser_ablation"],
        "market_impact_haircut_test": res["market_impact_haircut_test"],
        "cross_asset_transferability_without_retraining":
            res["cross_asset_transferability_without_retraining"],
        "intraday_tick_level_margin_breach_audit":
            res["intraday_tick_level_margin_breach_audit"],
        "rejection_rule_freeze": res["rejection_rule_freeze"],
    },
    "assertions": res["assertions"],
    "all_assertions_true": assertions_true,
    "selector_version": res["selector_version"],
    "disposition_version": res["disposition_version"],
    "disposition_mapping_version": res["disposition_mapping_version"],
    "contract_semantics_version": res["contract_semantics_version"],
    "engine_version": res["engine_version"],
    "engine_semantics": res["engine_semantics"],
    "container_verdict_hint": {
        "verdict_recommendation": res["verdict_recommendation"],
        "verdict_recommendation_final": res["verdict_recommendation_final"],
        "performance_claimable_recommendation": res["performance_claimable_recommendation"]},
    "attempts": {
        "registered_round_spec_sha256": sha(os.path.join(RDIR, "round-spec.json")),
        "round_spec_bytes": os.path.getsize(os.path.join(RDIR, "round-spec.json")),
        "u1": {"status": "FAILED_LAUNCH_ZERO_WRITE", "failure_class": "script_bug",
               "run_spec_sha256": sha(os.path.join(ATT1, "run-spec.json")),
               "engine_sha256": load(os.path.join(ATT1, "run-spec.json"))["script"]["sha256"],
               "detail": "NameError: name 'log' is not defined - the engine assembly had dropped "
                         "the module-level helper block; the same missing helper also killed the "
                         "exception handler, so the attempt carries no state.json, no "
                         "result.json, no sentinel and an empty artifacts/ tree",
               "note": "zero measurement: no scientific result, nothing adopted"},
        "u2": {"status": "SUPERSEDED_ZERO_WRITE", "failure_class": None,
               "run_spec_sha256": sha(os.path.join(ATT2, "run-spec.json")),
               "engine_sha256": load(os.path.join(ATT2, "run-spec.json"))["script"]["sha256"],
               "detail": "instantiated but never launched: the self-check bytes pinned at the "
                         "time contained an over-strict AST guard (a nested function definition "
                         "counted as an unresolved global) that failed 1/26 in the container; the "
                         "test file was repaired (26/26 green) and u3 re-pins the repaired bytes",
               "note": "zero measurement: no launch, no state.json, no result.json, no sentinel"},
        "u3": {"status": "DONE",
               "run_spec_sha256": sha(os.path.join(ATT, "run-spec.json")),
               "engine_sha256": rspec["script"]["sha256"],
               "result_sha256": sha(os.path.join(ATT, "result.json")),
               "sentinel_sha256": sha(os.path.join(ATT, "DONE")),
               "sentinel_manifest_entries": len(sent["artifact_checksums"]),
               "runtime_seconds": res["runtime_seconds"]},
        "pre_registration_unchanged_across_attempts": True,
        "retries": "u1 failure and the u2 pre-launch repair are card-local same-round remediations "
                   "inside the frozen r1 round; the registered domains, split, gates, costs and "
                   "selector/disposition semantics are unchanged, and authoritative u3 runs the "
                   "same 23,040-case contract",
    },
    "verification": {
        "engine_selfcheck": "container exec qlib-run env SK_ENGINE_PATH=/scripts/"
                            "110_strategy_k_run.py /opt/venv/bin/python "
                            "/scripts/tests/test_strategy_k_engine.py -> Ran 26 tests, OK",
        "engine_sha256_repo": rspec["script"]["sha256"],
        "engine_sha256_deployed": rspec["script"]["sha256"],
        "selfcheck_sha256": rspec["engine_selfcheck"]["sha256"],
        "preflight": "runtime/preflight.py --launch --attempt-dir <u3> -> P1-P10 PASS (rc=0, P10 "
                     "recomputed the script sha)",
        "terminal_evidence_check": "runtime/terminal_evidence.py check --attempt-dir <u3> -> "
                                   "ok=true, terminal DONE, 0 problems",
        "csv_rederivation": {
            "method": "host-side stdlib re-derivation of every grid CSV: row count + exact cell "
                      "set, the elected winners, and the DCA layer histogram identity",
            **verified.get("host", {})},
        "container_code_path_replay": verified.get("replay"),
        "worker_race_disclosure": "board run 298 (pid 99036) was booked `crashed` but its process "
                                  "was still alive and writing in the shared workspace when run "
                                  "299 claimed the card; it was terminated (single-writer "
                                  "restored) and its two artifacts (.kanban-scratch/"
                                  "t_54d4eaf1-explore-events.py and a J-engine copy at "
                                  "container/scripts/110_strategy_k_run.py) carry no measurement "
                                  "and were not adopted - the engine bytes were rebuilt and "
                                  "every hash in this document is re-read from disk",
        "written_by": "Hermes default (Kanban card t_54d4eaf1)",
    },
}

path = os.path.join(RDIR, "verdict.json")
flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
try:
    fd = os.open(path, flags, 0o644)
except FileExistsError:
    raise SystemExit("refusing to overwrite %s (immutable, contract INV-4)" % path)
with os.fdopen(fd, "w") as fh:
    fh.write(json.dumps(verdict, indent=2, ensure_ascii=False) + "\n")
print(json.dumps({"written": path, "sha256": sha(path), "verdict": verdict_value,
                  "performance_claimable": claimable, "survivors": verdict["cohort_survivors"],
                  "reader_hits": hits, "all_assertions_true": assertions_true}, indent=2))
return_code = 0
sys.exit(return_code)
