"""Digest of the drafted verdict for review before publishing."""
import json

v = json.load(open("/tmp/d_v1_verdict.json"))
print("family=%s round=%s run=%s" % (v["family_id"], v["round_id"], v["run_id"]))
print("verdict=%s performance_claimable=%s band=%s survivors=%s"
      % (v["verdict"], v["performance_claimable"], v["disposition_band"], v["cohort_survivors"]))
print("yield=%s rounds_used=%s" % (v["yield"]["yield_decision"], v["yield"]["rounds_used"]))
print("\ncohorts:")
for d in v["cohort_decisions"]:
    print("  %-11s %-8s case=%-20s culls=%s" % (d["cohort"], d["outcome"],
                                                d["winner_case_label"], d["cull_reasons"]))
    for k in ("historical", "oos", "full"):
        m = d[k]
        print("      %-10s net_pnl=%12.2f sharpe=%7.3f episodes=%5d maxdd_pct=%7.4f lev=%6.3f"
              % (k, m["net_pnl"], m["sharpe"], m["episodes"], m["max_dd_pct"],
                 m["max_effective_leverage"]))
    print("      robustness: %s" % {g: round(m["net_pnl"], 1) for g, m in d["robustness"].items()})
    print("      cost_attrition_40bps net_pnl=%s ; neighbourhood passed=%s (%.2f)"
          % (round(d["cost_attrition_40bps"]["net_pnl"], 1), d["neighbourhood"]["passed"],
             d["neighbourhood"]["same_sign_fraction"]))
print("\ncoverage: %s total=%s complete=%s" % (v["coverage"]["case_evaluations_per_grid"],
                                               v["coverage"]["case_evaluations_total"],
                                               v["coverage"]["coverage_complete"]))
print("family flags: %s" % v["registered_family_level_falsification"]["flags"])
print("counters nonzero:", {g: {k: n for k, n in c.items() if n}
                            for g, c in v["assertions"]["structural_counters"].items()
                            if any(c.values())})
print("assertions all true:", v["assertions"]["all_true"])
print("level_00=%s level_01=%s level_11=%s" % (v["dca_layer_histogram"]["level_00"],
                                               v["dca_layer_histogram"]["level_01"],
                                               v["dca_layer_histogram"]["level_11"]))
print("shas: sentinel=%s result=%s run_spec=%s engine=%s"
      % (v["terminal_sentinel_sha256"][:24], v["result_json_sha256"][:24],
         v["run_spec_sha256"][:24], v["pinned_engine_sha256"][:24]))
print("panel:", json.dumps(v["panel_diagnostic_equal_notional"], ensure_ascii=False)[:400])
print("turnover keys:", list(v["turnover_and_leverage_full_window_winner_cell"]))
print("leg decomposition cohorts:", list(v["leg_decomposition_and_robustness_diagnostics"]["cohorts"]))
print("notes:", [n["item"] for n in v["registered_semantics_notes"]])
print("missing_conditions chars:", len(v["missing_conditions"][0]))
