#!/usr/bin/env python3
"""Read back a Strategy D attempt result.json summary (card t_50c28da5)."""
import json
import sys

p = sys.argv[1]
d = json.load(open(p))
keys = ("family_id", "run_id", "engine_version", "coverage_complete", "case_evaluations_total",
        "expected_case_evaluations", "case_evaluations_per_grid", "cohorts", "cohort_count",
        "disposition", "verdict_recommendation", "verdict_recommendation_final",
        "performance_claimable_recommendation", "cohort_survivor_count", "runtime_seconds",
        "structural_counters", "slice_days", "generated_at_utc")
for k in keys:
    print("%-42s %s" % (k, json.dumps(d.get(k), ensure_ascii=False)[:300]))
print("--- coverage:", json.dumps(d.get("coverage"), ensure_ascii=False))
print("--- assertions false:", [k for k, v in (d.get("assertions") or {}).items() if v is not True])
print("--- assertions count:", len(d.get("assertions") or {}))
print("--- cohort outcomes:", json.dumps(
    [{c["cohort"]: c["outcome"]} for c in (d.get("cohort_results") or [])], ensure_ascii=False))
print("--- falsification flags:", json.dumps(
    d.get("registered_family_level_falsification_flags"), ensure_ascii=False))
if len(sys.argv) > 2:
    print(json.dumps(d, indent=1, ensure_ascii=False)[: int(sys.argv[2])])
