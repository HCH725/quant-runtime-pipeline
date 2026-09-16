"""Write evidence/strategy-d-v1-r1-run-20260915.json from the u3 artifacts + verdict.json."""
import csv
import hashlib
import json
import os

REPO = "/Users/hong/workspace/quant-runtime-pipeline"
RESULTS = "/Volumes/ExpansionDrive/qlib-results"
FAMILY = "utc-clock-hour-seasonality-perp-panel-v1"
ROUND = FAMILY + "-r1"
RUN = ROUND + "-u3"
ROUND_DIR = os.path.join(RESULTS, FAMILY, "rounds", ROUND)
U3 = os.path.join(ROUND_DIR, "attempts", RUN)
OUT = os.path.join(REPO, "evidence", "strategy-d-v1-r1-run-20260915.json")


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


res = json.load(open(os.path.join(U3, "result.json")))
verdict = json.load(open(os.path.join(ROUND_DIR, "verdict.json")))
family_json = json.load(open(os.path.join(RESULTS, FAMILY, "family.json")))
assertions = res["assertions"]

cohorts_out = []
rows_full = {}
with open(os.path.join(U3, "artifacts", "grid_full.csv")) as fh:
    for row in csv.DictReader(fh):
        rows_full[(row["symbol"], row["window_case"], row["spacing_pct"], row["size_multiplier"],
                   row["breakeven_tp_pct"], row["invalidation_pct"])] = row
for c in res["cohort_results"]:
    m = c["metrics"]
    w = c["winner"]
    sym = c["cohort"].split("/")[0]
    cell = rows_full[(sym, c["winner_case_label"], str(w["spacing_pct"]),
                      str(w["size_multiplier"]), str(w["breakeven_tp_pct"]),
                      str(w["invalidation_pct"]))]
    cohorts_out.append({
        "cohort": c["cohort"], "outcome": c["outcome"], "cull_reasons": c["cull_reasons"],
        "winner_case_label": c["winner_case_label"], "winner_cell": c["winner"],
        "neighbourhood": {k: c["neighbourhood"][k] for k in
                          ("neighbours", "agreeing", "same_sign_fraction", "threshold",
                           "winner_net_pnl_positive", "passed")},
        "historical": {k: m["historical"][k] for k in
                       ("net_pnl", "sharpe", "episodes", "ending_equity", "fees", "funding",
                        "max_dd_usdt", "max_dd_pct", "max_effective_leverage",
                        "capital_utilization", "annualized_return", "cagr")},
        "oos": {k: m["oos"][k] for k in
                ("net_pnl", "sharpe", "episodes", "ending_equity", "fees", "funding",
                 "max_dd_usdt", "max_dd_pct", "max_effective_leverage", "capital_utilization",
                 "annualized_return", "cagr")},
        "full": {k: m["full"][k] for k in
                 ("net_pnl", "sharpe", "episodes", "ending_equity", "fees", "funding",
                  "max_dd_usdt", "max_dd_pct", "max_effective_leverage", "capital_utilization",
                  "annualized_return", "cagr")},
        "full_winner_cell_turnover": {k: cell[k] for k in ("fills", "turnover_usdt",
                                                           "windows_seen")},
        "robustness": {g: {k: m["robustness"][g][k] for k in ("net_pnl", "sharpe", "episodes")}
                       for g in ("fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks")},
        "cost_attrition_40bps": {k: m["cost_attrition_40bps"][k] for k in
                                 ("net_pnl", "sharpe", "episodes", "ending_equity")},
        "no_funding_full_reference": {k: m["no_funding_full_reference"][k] for k in
                                      ("net_pnl", "sharpe", "episodes")},
        "boundary_alt_oos_reference": {k: m["boundary_alt_oos_reference"][k] for k in
                                       ("net_pnl", "sharpe", "episodes")},
        "boundary_alt_full_reference": {k: m["boundary_alt_full_reference"][k] for k in
                                        ("net_pnl", "sharpe", "episodes")},
    })

diag = res["robustness_diagnostics"]
leg_notes = {}
for label, d in diag["cohorts"].items():
    legs = {}
    for leg, slot in d["singleton_leg_pnl_by_year"].items():
        legs[leg] = {"net_pnl_by_year": {y: v["net_pnl"] for y, v in slot.items()},
                     "net_pnl_total": round(sum(v["net_pnl"] for v in slot.values()), 4)}
    leg_notes[label] = {"winner_cell": d["winner_cell"], "singleton_leg_totals": legs,
                        "winner_diagnostics_match_full_row": d["winner_diagnostics_match_full_row"]}

record = {
    "snapshot_kind": "strategy-d-v1 round 1 execution record (launch -> terminal -> verdict)",
    "snapshot_taken_at_utc": "2026-09-15T08:30:00Z",
    "snapshot_boundary": ("Read-back of the immutable /results tree on the run host at the time of "
                          "writing. Not runtime state: whether the container, mounts or cron are "
                          "healthy now must be queried live, never inferred from this file."),
    "host_paths_redacted": "<EXPANSION> = the external results volume mount; <HOME> = the user home",
    "card": {"kanban_task_id": "t_50c28da5", "board": "quant-strategy-research",
             "family_id": FAMILY, "round_id": ROUND},
    "family": {
        "title": "UTC clock-hour return seasonality panel (BTCUSDT/ETHUSDT/BNBUSDT/SOLUSDT 1h "
                 "USD-M perpetuals)",
        "hypothesis": "Clock-time seasonality in 24/7 crypto: the UTC hour of day carries return "
                      "differences because activity/liquidity/settlement overlap with fiat market "
                      "hours - 01:00-05:00 UTC weak, 15:00-16:00 UTC strong, the last three hours "
                      "of the day positive. The signal depends on clock time only (no price "
                      "history, no trend or mean-reversion estimate), so it is information-set "
                      "orthogonal to Strategy A and B.",
        "registered_legs": {
            "long": "15:00-16:00 UTC, +1 unit",
            "short": "01:00-05:00 UTC, -1 unit (sym-directionally mirrored DCA rail)",
            "secondary_long": "21:00-24:00 UTC, +1 unit (evaluated separately; only advances with "
                              "the primary if it adds OOS value under a pre-written rule)",
        },
        "registered_grids": 12,
        "registered_grid_names": res["cohort_grid_kinds"],
        "cohort_count": 4,
        "case_evaluations_per_grid": res["case_evaluations_per_grid"],
        "case_evaluations_per_cohort_per_grid": res["case_evaluations_per_cohort_per_grid"],
        "case_evaluations_total": res["case_evaluations_total"],
        "expected_case_evaluations": res["expected_case_evaluations"],
        "provenance": "wiki brain quant/crypto-intraday-utc-return-seasonality-tea-time-2026-09-03.md "
                      "(research-only intake); the source's 38-venue pair evidence does not cover "
                      "perpetual futures and this card IS the registered portability test; scope "
                      "limited to Binance USD-M perp, 4 symbols, 2022-01-01..2026-09-11",
        "selector_disposition": "selector=cohort-selector-v1; disposition=cohort-disposition-v1",
    },
    "preregistration": {
        "family_json_sha256": sha(os.path.join(RESULTS, FAMILY, "family.json")),
        "family_json_kanban_task_id": family_json.get("kanban_task_id"),
        "semantic_fingerprint": family_json.get("semantic_fingerprint"),
        "round_spec_sha256": sha(os.path.join(ROUND_DIR, "round-spec.json")),
        "round_spec_reused_verbatim": True,
        "run_spec_sha256": sha(os.path.join(U3, "run-spec.json")),
        "engine_script": "/scripts/40_strategy_d_run.py",
        "engine_sha256": sha("/Users/hong/workspace/qlib-apple-container/scripts/"
                             "40_strategy_d_run.py"),
        "engine_selfcheck_sha256": sha("/Users/hong/workspace/qlib-apple-container/scripts/tests/"
                                       "test_strategy_d_engine.py"),
        "engine_version": res["engine_version"],
        "engine_semantics": res["engine_semantics"],
        "split": {"historical": "2022-01-01..2025-09-30 (1369 days)",
                  "oos": "2025-10-01..2026-09-11 (346 days)",
                  "full": "2022-01-01..2026-09-11 (1715 days)"},
    },
    "attempts": [
        {"run_id": ROUND + "-u1", "terminal": "INCOMPLETE", "failure_layer": "card-local",
         "failure_class": "script_bug",
         "detail": "summarize() re-read the elected winner's frozen full-window row from the "
                   "global cross-cohort accumulation, so same_cell refused ('expected exactly one "
                   "row for cell ..., found 4'). The 3-month smoke run could not see it because "
                   "every smoke cohort was culled at selection, so the winner-diagnostics loop was "
                   "skipped - a production-only path. Fixed in 1ac2542; no measurement artifact "
                   "released."},
        {"run_id": ROUND + "-u2", "terminal": None, "failure_layer": "card-local",
         "failure_class": "script_bug (accounting + detector; fixed in 3732230)",
         "detail": "ARTIFACT_READY with 16,128/16,128 case evaluations and coverage complete, but "
                   "layer0_equals_episodes=false (the full-window ladder histogram double-counted "
                   "the winner/singleton-leg diagnostics by exactly 29,155 episodes) and "
                   "funding_bar_never_out_of_hold=false (768 events, all on the boundary-"
                   "alternative OOS track: its shifted short window opens on the OOS slice's first "
                   "bar, where the raw 2025-10-01T00:00Z settlement sits exactly on the boundary "
                   "with 0 ms jitter). No terminal sentinel was ever published for u2; its 12 grid "
                   "CSVs are byte-identical to u3's, so it carries no independent measurement."},
        {"run_id": RUN, "terminal": "DONE", "state": "ARTIFACT_READY",
         "container_id": "qlib-run", "image_id": "qlib:0.9.7-arm64", "qlib_version": "0.9.7",
         "verdict_hint": "CANDIDATE_REJECT", "runtime_seconds": res["runtime_seconds"],
         "terminal_sentinel_sha256": sha(os.path.join(U3, "DONE")),
         "result_json_sha256": sha(os.path.join(U3, "result.json")),
         "sentinel_artifacts_verified": 25,
         "terminal_evidence_check": {"rc": 0, "problems": []},
         "reconcile_readback": {"dry_run_rc": 0, "action": "consumed (sentinel already consumed "
                                                          "(card status=running); idempotent)",
                                "incidents": []}},
    ],
    "script_fixes_this_round": [
        {"commit": "1ac2542",
         "detail": "the winner-diagnostics cell lookup stays inside the cohort's own rows "
                   "(fixes the u1 failure)"},
        {"commit": "3732230",
         "detail": "two accounting/detector defects, both found by u2 asserting on itself: "
                   "(1) simulate(..., count_layers=True) with the two winner/singleton-leg "
                   "diagnostic call sites passing count_layers=False, so the reported full-window "
                   "ladder histogram counts GRID cells only and the registered identity "
                   "(level 00 == sum of episodes over the eight full-window grids) holds; "
                   "(2) build_funding_index additionally returns settle_bar_closed (the bar that "
                   "CONTAINS the instant, half-open) which ONLY the out-of-hold guard reads, so an "
                   "exactly-on-boundary settlement is not pushed one bar out of the hold while the "
                   "scheduling array settle_bar - and therefore every charge - stays untouched. "
                   "Regression tests 49/49 in qlib-run (was 45); RED-proved against a /tmp copy "
                   "with the two production lines reverted (1 != 0 and [2,0,...] != [1,0,...]); "
                   "the u3 grid CSVs are byte-identical to u2's, which is the measurement-"
                   "neutrality proof."},
    ],
    "measurement_neutrality_proof": {
        "claim": "the two fixes change no charge, gate or grid row",
        "method": "sha256 of each of the 12 grid CSVs, u2 vs u3",
        "result": "12/12 byte-identical",
        "corroboration": ["cohort_results metrics identical to u2",
                          "panel_diagnostic identical to u2",
                          "timezone_fragility identical to u2",
                          "window_instability identical to u2",
                          "the only intended numeric delta anywhere is the corrected "
                          "dca_layer_histogram: level_00 22,601,301 -> 22,572,146 (=-29,155)"],
    },
    "measurement": {
        "coverage_complete": res["coverage_complete"],
        "coverage_per_grid": res["coverage"],
        "assertions_all_true": all(assertions.values()),
        "assertions": assertions,
        "structural_counters": res["structural_counters"],
        "structural_counter_tolerance_ms": res["structural_counter_tolerance_ms"],
        "dca_layer_histogram": res["dca_layer_histogram"],
        "cohort_outcome_counts": res["cohort_outcome_counts"],
        "cohort_survivor_count": res["cohort_survivor_count"],
        "disposition": res["disposition"],
        "family_level_falsification": {
            "flags": res["registered_family_level_falsification_flags"],
            "timezone_fragility": {k: res["timezone_fragility"][k] for k in
                                   ("hit", "evaluated", "landing", "non_gating_band")},
            "window_instability": {k: res["window_instability"][k] for k in
                                   ("hit", "evaluated", "landing", "non_gating_band")},
        },
        "panel_diagnostic_equal_notional": {
            "non_gating": res["panel_diagnostic"]["non_gating"],
            "all_members_ending_equity": res["panel_diagnostic"]["all_members"]["ending_equity"],
            "all_members_net_pnl": res["panel_diagnostic"]["all_members"]["net_pnl"],
        },
        "cohorts": cohorts_out,
        "leg_decomposition": leg_notes,
        "stress_summary_medians_non_gating": res["stress_summary"],
        "funding_series_readback": {k: {"observations": v["observations"],
                                        "truth_status_counts": v["truth_status_counts"],
                                        "first": v["first"], "last": v["last"]}
                                    for k, v in res["funding_series"].items()},
    },
    "verdict": {
        "verdict": verdict["verdict"],
        "performance_claimable": verdict["performance_claimable"],
        "disposition": verdict["disposition"],
        "disposition_band": verdict["disposition_band"],
        "yield": verdict["yield"],
        "missing_conditions": verdict["missing_conditions"],
        "failed_gates": ["robustness_economic (cost_attrition_40bps) for all four cohorts",
                         "robustness_economic (fee_2x) for BTCUSDT/1h",
                         "parameter_neighbourhood for BTCUSDT/1h (0.40 < 0.60)"],
        "verdict_json_sha256": sha(os.path.join(ROUND_DIR, "verdict.json")),
        "verdict_json_rewrite": {
            "reason": "the first write of this attempt's verdict.json carried null fills/turnover "
                      "in turnover_and_leverage_full_window_winner_cell (the cohort metrics block "
                      "does not carry them). It was replaced minutes later - before any board "
                      "transition, with the single-file diff reviewed (only the turnover blocks and "
                      "the regenerated decided_at_utc timestamps differ) - by the values read from "
                      "artifacts/grid_full.csv for each winner cell.",
            "old_sha256": "sha256:6663da6fff61cb1debc246cabc411cf5c616cfeac1208a56e215b986ab89c1ab",
            "authoritative_sha256": "sha256:d6992d7012d2562c0142441eb9f0d606387befd2f137a6829215ca3fc559037d",
        },
        "survivor_bundle": "not written (0 survivors; contract 10.8 requires >=1)",
        "contract_9_6_note": "performance_claimable stays false because verdict != PASS even "
                             "though every other 9.6 condition (canonical data, no look-ahead, "
                             "explicit OOS window, declared costs/funding/slippage, section 7.2 "
                             "coverage) is satisfied",
        "registered_semantics_notes": verdict["registered_semantics_notes"],
    },
    "not_done_by_this_card": [
        "no survivor bundle, no post-survivor index/leaderboard entry, no evidence package "
        "(this round has 0 survivors, so no leaderboard entry exists to trigger contract 28)",
        "no forward slice, no champion selection, no operationalisation, no Paper/Live",
        "no Strategy A/B artifacts touched (all immutable)",
        "the next family is NOT appended by this card: the section 14.4 automatic handoff appends "
        "it after this card reaches done",
    ],
}

with open(OUT, "w") as fh:
    json.dump(record, fh, indent=2, ensure_ascii=False)
    fh.write("\n")
back = json.load(open(OUT))
print("wrote %s (%d bytes)" % (OUT, os.path.getsize(OUT)))
print("verdict=%s claimable=%s assertions_all_true=%s cohorts=%d coverage_total=%s"
      % (back["verdict"]["verdict"], back["verdict"]["performance_claimable"],
         back["measurement"]["assertions_all_true"], len(back["measurement"]["cohorts"]),
         back["family"]["case_evaluations_total"]))
