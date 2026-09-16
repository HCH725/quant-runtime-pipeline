"""Dump the nested robustness block of one cohort (fixing the verdict builder's field mapping)."""
import json
import os

U3 = ("/Volumes/ExpansionDrive/qlib-results/utc-clock-hour-seasonality-perp-panel-v1/rounds/"
      "utc-clock-hour-seasonality-perp-panel-v1-r1/attempts/utc-clock-hour-seasonality-perp-panel-v1-r1-u3")
res = json.load(open(os.path.join(U3, "result.json")))
m = res["cohort_results"][0]["metrics"]
print("metrics keys:", sorted(m.keys()))
rb = m["robustness"]
print("robustness type:", type(rb).__name__, "keys:", sorted(rb.keys()) if isinstance(rb, dict) else rb)
print(json.dumps(rb, indent=1, sort_keys=True)[:1200])
print("\nBTC cull reasons:", res["cohort_results"][1]["cull_reasons"])
print("BNB neighbourhood:", json.dumps(res["cohort_results"][0]["neighbourhood"], indent=1))
print("\ncase_evaluations_per_cohort_per_grid:", res["case_evaluations_per_cohort_per_grid"])
print("cohort_grid_kinds:", res["cohort_grid_kinds"])
print("cohort_outcome_counts:", res["cohort_outcome_counts"])
print("descriptive_diagnostics keys:", sorted(res["descriptive_diagnostics"].keys())
      if isinstance(res["descriptive_diagnostics"], dict) else type(res["descriptive_diagnostics"]))
print("disposition_mapping_version:", res.get("disposition_mapping_version"))
print("performance_claimable_recommendation:", res.get("performance_claimable_recommendation"),
      res.get("performance_claimable_recommendation_final"))
