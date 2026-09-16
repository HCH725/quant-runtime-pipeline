"""Structural dump of the u3 cohort_results (to map the verdict fields exactly)."""
import json
import os

U3 = ("/Volumes/ExpansionDrive/qlib-results/utc-clock-hour-seasonality-perp-panel-v1/rounds/"
      "utc-clock-hour-seasonality-perp-panel-v1-r1/attempts/utc-clock-hour-seasonality-perp-panel-v1-r1-u3")

res = json.load(open(os.path.join(U3, "result.json")))
print("top-level keys:", sorted(res.keys()))
c0 = res["cohort_results"][0]
print("\ncohort_results[0] keys:", sorted(c0.keys()))
for k, v in c0.items():
    if isinstance(v, dict):
        print("  %-24s dict keys: %s" % (k, sorted(v.keys())))
    elif isinstance(v, list):
        print("  %-24s list[%d] first=%r" % (k, len(v), v[0] if v else None))
    else:
        print("  %-24s %r" % (k, v))
m = c0["metrics"]
print("\nmetrics keys:", sorted(m.keys()))
for grp in ("historical", "oos", "full"):
    if grp in m:
        print("  %s: %s" % (grp, json.dumps(m[grp], sort_keys=True)[:400]))
print("\nrobustness_diagnostics keys:", sorted(res["robustness_diagnostics"].keys()))
print("cohort diag keys:",
      sorted(res["robustness_diagnostics"]["cohorts"][list(res["robustness_diagnostics"]["cohorts"])[0]].keys()))
print("\npanel_diagnostic keys:", sorted(res["panel_diagnostic"].keys()) if isinstance(res["panel_diagnostic"], dict) else type(res["panel_diagnostic"]))
print("timezone_fragility keys:", sorted(res["timezone_fragility"].keys()))
print("window_instability keys:", sorted(res["window_instability"].keys()))
print("survivor_evidence keys:", sorted(res["survivor_evidence"].keys()) if isinstance(res["survivor_evidence"], dict) else res["survivor_evidence"])
print("\nregistered_family_level_falsification_flags:", res["registered_family_level_falsification_flags"])
print("expected:", json.dumps(res.get("expected"), indent=1)[:600])
print("slice_days:", res.get("slice_days"))
