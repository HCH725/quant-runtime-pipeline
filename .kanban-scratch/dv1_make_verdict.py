"""Build rounds/<round>/verdict.json for Strategy D r1 (contract 10.7) from the u3 artifacts.

Refuses to overwrite an existing verdict.json (INV-4). Writes a temp file first for review.
"""
import csv
import hashlib
import json
import os
import sys
import time

RESULTS = "/Volumes/ExpansionDrive/qlib-results"
FAMILY = "utc-clock-hour-seasonality-perp-panel-v1"
ROUND = FAMILY + "-r1"
RUN = ROUND + "-u3"
ROUND_DIR = os.path.join(RESULTS, FAMILY, "rounds", ROUND)
U3 = os.path.join(ROUND_DIR, "attempts", RUN)
VERDICT = os.path.join(ROUND_DIR, "verdict.json")
OUT = "/tmp/d_v1_verdict_full.json"

res = json.load(open(os.path.join(U3, "result.json")))
assertions = res["assertions"]

METRIC_KEYS = ("net_pnl", "sharpe", "episodes", "ending_equity", "fees", "funding",
               "max_dd_usdt", "max_dd_pct", "max_effective_leverage", "capital_utilization",
               "annualized_return", "cagr")


def pick(d, keys=METRIC_KEYS):
    return {k: d[k] for k in keys if k in d}


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def fmt(x, nd=4):
    return None if x is None else round(float(x), nd)


decisions = []
for c in res["cohort_results"]:
    m = c["metrics"]
    rob = {g: pick(m["robustness"][g]) for g in ("fee_2x", "funding_2x", "entry_delay_1_bar",
                                                 "slippage_2ticks") if g in m["robustness"]}
    decisions.append({
        "cohort": c["cohort"],
        "outcome": c["outcome"],
        "cull_reasons": c["cull_reasons"],
        "no_winner_reason": c["no_winner_reason"],
        "winner_case_label": c["winner_case_label"],
        "winner_cell": c["winner"],
        "neighbourhood": c["neighbourhood"],
        "historical": pick(m["historical"]),
        "oos": pick(m["oos"]),
        "full": pick(m["full"]),
        "robustness": rob,
        "cost_attrition_40bps": pick(m["cost_attrition_40bps"]),
        "no_funding_reference_historical": pick(m["no_funding_reference"]),
        "no_funding_full_reference": pick(m["no_funding_full_reference"]),
        "boundary_alt_oos_reference": pick(m["boundary_alt_oos_reference"]),
        "boundary_alt_full_reference": pick(m["boundary_alt_full_reference"]),
    })

diag = res["robustness_diagnostics"]
leg_decomposition = {}
for label, d in diag["cohorts"].items():
    leg_decomposition[label] = {
        "winner_cell": d["winner_cell"],
        "pnl_by_year": d["pnl_by_year"],
        "pnl_by_entry_window": d["pnl_by_window"],
        "singleton_leg_pnl_by_year": d["singleton_leg_pnl_by_year"],
        "winner_diagnostics_match_full_row": d["winner_diagnostics_match_full_row"],
        "daily_series_days": d["daily_series_days"],
    }

turnover = {}
rows_full = {}
with open(os.path.join(U3, "artifacts", "grid_full.csv")) as fh:
    for row in csv.DictReader(fh):
        rows_full[(row["symbol"], row["window_case"], row["spacing_pct"], row["size_multiplier"],
                   row["breakeven_tp_pct"], row["invalidation_pct"])] = row
for c in res["cohort_results"]:
    f = c["metrics"]["full"]
    w = c["winner"]
    sym = c["cohort"].split("/")[0]
    cell = rows_full[(sym, c["winner_case_label"], str(w["spacing_pct"]), str(w["size_multiplier"]),
                      str(w["breakeven_tp_pct"]), str(w["invalidation_pct"]))]
    turnover[c["cohort"]] = {
        "fills": int(cell["fills"]), "turnover_usdt": float(cell["turnover_usdt"]),
        "episodes": int(cell["episodes"]), "windows_seen": int(cell["windows_seen"]),
        "max_effective_leverage": f["max_effective_leverage"],
        "capital_utilization": f["capital_utilization"],
        "annualized_return": f["annualized_return"], "cagr": f["cagr"],
        "max_dd_pct": f["max_dd_pct"], "max_dd_usdt": f["max_dd_usdt"],
        "ending_equity": f["ending_equity"], "net_pnl": f["net_pnl"], "sharpe": f["sharpe"],
        "fees": f["fees"], "funding": f["funding"],
    }

culls = "\n".join("  - %s: %s" % (d["cohort"], ", ".join(d["cull_reasons"])) for d in decisions)
missing = (
    "verdict != PASS: the family disposition is %s (0 of %d cohort survivors). Each cohort "
    "produced a qualifying historical winner under cohort-selector-v1, and every one of them was "
    "culled by the pre-registered falsification battery:\n%s\nContract 9.6 requires verdict == "
    "PASS for performance_claimable=true. Nothing else is missing: canonical read-only /data/raw, "
    "no look-ahead (28/28 assertions incl. the two clock-window structural guards and the "
    "funding/coverage guards), an explicit OOS split written into the immutable round-spec before "
    "any computation, declared cost/funding/slippage assumptions, and full section 7.2 coverage "
    "(4 cohorts x 7 registered strategy cases x 48 DCA configs x 12 phase grids = 16,128 case "
    "evaluations) are all satisfied and recorded in the u3 attempt artifacts."
    % (res["disposition"], res["cohort_count"], culls))

verdict = {
    "schema_version": 1,
    "family_id": FAMILY,
    "round_id": ROUND,
    "run_id": RUN,
    "kanban_task_id": "t_50c28da5",
    "kanban_board": "quant-strategy-research",
    "verdict": "REJECT",
    "performance_claimable": False,
    "missing_conditions": [missing],
    "yield": {
        "rounds_used": 1,
        "max_rounds": 3,
        "no_progress_rounds": 0,
        "progress_evidence": [
            "artifacts/cohort_results.json - all four cohorts adjudicated by cohort-selector-v1 / "
            "cohort-disposition-v1 with every cull reason recorded",
            "contract 15.3 item 3: a triggered pre-registered falsification counts as progress, so "
            "this round is not a no-progress round (STOP_REJECT at round 1 of 3)",
        ],
        "yield_decision": "STOP_REJECT",
        "decided_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    },
    "failure": {"layer": None, "class": None},
    "evidence_run_ids": [RUN],
    "decided_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "disposition": res["disposition"].split(" / ")[0],
    "disposition_band": res["disposition"].split(" / ")[1],
    "cohort_survivors": res["cohort_survivors"],
    "cohort_count": res["cohort_count"],
    "cohort_outcome_counts": res["cohort_outcome_counts"],
    "coverage": {
        "coverage_complete": res["coverage_complete"],
        "case_evaluations_total": res["case_evaluations_total"],
        "expected_case_evaluations": res["expected_case_evaluations"],
        "case_evaluations_per_grid": res["case_evaluations_per_grid"],
        "case_evaluations_per_cohort_per_grid": res["case_evaluations_per_cohort_per_grid"],
        "coverage_per_grid": res["coverage"],
        "cohort_count": res["cohort_count"],
        "slice_days": res["slice_days"],
    },
    "cohort_decisions": decisions,
    "registered_family_level_falsification": {
        "timezone_fragility": res["timezone_fragility"],
        "window_instability": res["window_instability"],
        "flags": res["registered_family_level_falsification_flags"],
    },
    "leg_decomposition_and_robustness_diagnostics": {
        "registered_item": diag["registered_item"],
        "note": diag["note"],
        "non_gating": diag["non_gating"],
        "cohorts": leg_decomposition,
    },
    "panel_diagnostic_equal_notional": res["panel_diagnostic"],
    "stress_summary_medians_non_gating": res["stress_summary"],
    "turnover_and_leverage_full_window_winner_cell": turnover,
    "dca_layer_histogram": res["dca_layer_histogram"],
    "assertions": {
        "all_true": all(assertions.values()),
        "entries": assertions,
        "structural_counters": res["structural_counters"],
        "structural_counter_tolerance_ms": res["structural_counter_tolerance_ms"],
        "note": "the only nonzero structural counter is window_clipped_at_slice_edge=768 on the "
                "registered entry_delay_1_bar track: the delay track deliberately moves the "
                "schedule, so its last window falls past the slice edge and its clip count is "
                "REPORTED rather than asserted (contract 7.2); every unshifted grid is 0 and the "
                "funding_bar_out_of_hold guard is 0 everywhere.",
    },
    "selector_version": res["selector_version"],
    "disposition_version": res["disposition_version"],
    "disposition_mapping_version": res["disposition_mapping_version"],
    "engine_version": res["engine_version"],
    "engine_semantics": res["engine_semantics"],
    "contract_semantics_version": res["contract_semantics_version"],
    "container_verdict_hint": "CANDIDATE_REJECT (hint only, contract 9.3); the runner's own "
                              "verdict_recommendation_final is %s" % res[
                                  "verdict_recommendation_final"],
    "technical_retries": [
        {"run_id": ROUND + "-u1", "terminal": "INCOMPLETE", "failure_layer": "card-local",
         "failure_class": "script_bug",
         "detail": "summarize() re-read the elected winner's frozen full-window row from the "
                   "global cross-cohort accumulation, so same_cell refused with 'expected exactly "
                   "one row, found 4'; fixed in 1ac2542. No measurement artifact released."},
        {"run_id": ROUND + "-u2", "terminal": None, "failure_layer": "card-local",
         "failure_class": "script_bug (two accounting/detector defects, fixed in 3732230)",
         "detail": "ARTIFACT_READY with 16,128/16,128 case evaluations but two false structural "
                   "assertions: layer0_equals_episodes (the ladder histogram double-counted the "
                   "winner/singleton-leg diagnostics by exactly 29,155 episodes) and "
                   "funding_bar_never_out_of_hold (768 events on the boundary-alternative OOS "
                   "track, caused by the half-open bar mapping of an exactly-on-boundary, 0 ms "
                   "jitter settlement). No terminal sentinel was ever published for u2; its 12 "
                   "grid CSVs are byte-identical to u3's, so it carries no independent "
                   "measurement and u3 is the authoritative attempt of this round."},
    ],
    "registered_semantics_notes": [
        {"item": "slice-scoped daily equity series",
         "disclosed_in": "round-spec before launch (registered measurement deviation from the "
                         "sealed C engine)",
         "detail": "the daily equity series of a grid is scoped to the evaluated slice; a "
                   "cohort-wide series padded with pre-window days would dilute Sharpe by "
                   "sqrt(slice/cohort) and silently weaken the G4 floor."},
        {"item": "entry_delay_1_bar shifts the whole episode",
         "disclosed_in": "round-spec before launch (registered measurement deviation)",
         "detail": "delaying only the entry inside a fixed window would delete every 1-bar leg; the "
                   "registered stress moves the episode and keeps the registered holding length. "
                   "Its 768 clipped slice-edge windows are reported, not asserted."},
        {"item": "funding mark for a settlement landing exactly ON an interior bar open",
         "disclosed_in": "verdict (documentation note; NOT one of the two assertion findings, no "
                         "remediation in this round)",
         "detail": "the engine charges the close of the bar ENDING at the settlement instant (the "
                   "mark at the instant). For an instant exactly on an INTERIOR bar open the "
                   "registered wording ('the close of the bar that contains it inside the window') "
                   "can also be read as the bar STARTING there, i.e. one bar later. The readings "
                   "are identical for entry- and exit-boundary settlements (the only boundary "
                   "instants the 0/8/16h grid puts inside a hold). They differ only for SOLUSDT's "
                   "early-2022 2h/4h-era on-boundary settlements at 02:00/04:00/22:00 (9 raw "
                   "observations, all Nov 2022). Upper bound on the whole effect, computed from "
                   "raw closes at the full 11-level ladder notional: 20.43 USDT over the 4.5-year "
                   "window (0.07% of the 30,000 USDT starting equity); the realised figure is "
                   "smaller. Changing the convention would move money, so it is a candidate for a "
                   "future registration, not for this round."},
    ],
    "evidence_paths": [
        os.path.join(U3, "DONE") + " (terminal sentinel)",
        os.path.join(U3, "result.json"),
        os.path.join(U3, "artifacts", "assertions.json"),
        os.path.join(U3, "artifacts", "cohort_results.json"),
        os.path.join(U3, "artifacts", "cohort_survivors.json"),
        os.path.join(U3, "artifacts", "panel_diagnostic.json"),
        os.path.join(U3, "artifacts", "robustness_diagnostics.json"),
        os.path.join(U3, "artifacts", "family_falsification.json"),
        os.path.join(U3, "artifacts", "dca_layer_histogram.json"),
        os.path.join(ROUND_DIR, "round-spec.json"),
        os.path.join(U3, "run-spec.json"),
    ],
    "terminal_sentinel_sha256": sha(os.path.join(U3, "DONE")),
    "result_json_sha256": sha(os.path.join(U3, "result.json")),
    "round_spec_sha256": sha(os.path.join(ROUND_DIR, "round-spec.json")),
    "run_spec_sha256": sha(os.path.join(U3, "run-spec.json")),
    "pinned_engine_sha256": sha("/Users/hong/workspace/qlib-apple-container/scripts/"
                                "40_strategy_d_run.py"),
    "survivor_bundle": "not written (contract 10.8 requires >=1 survivor; 0 survivors -> no bundle)",
}

if os.path.exists(OUT):
    sys.exit("refusing to overwrite existing %s" % OUT)
with open(OUT, "w") as fh:
    json.dump(verdict, fh, indent=2, ensure_ascii=False)
    fh.write("\n")
print("wrote %s (%d bytes)" % (OUT, os.path.getsize(OUT)))
print("verdict=%s claimable=%s survivors=%s band=%s"
      % (verdict["verdict"], verdict["performance_claimable"], verdict["cohort_survivor_count"]
         if "cohort_survivor_count" in verdict else verdict["cohort_outcome_counts"],
         verdict["disposition_band"]))
print("assertions all true:", verdict["assertions"]["all_true"])
print("level_00:", verdict["dca_layer_histogram"]["level_00"])
