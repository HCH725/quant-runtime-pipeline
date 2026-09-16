"""Read-only inspection of the Strategy D r1-u2 attempt artifacts (card t_50c28da5)."""
import csv
import json
import os

U2 = ("/Volumes/ExpansionDrive/qlib-results/utc-clock-hour-seasonality-perp-panel-v1"
      "/rounds/utc-clock-hour-seasonality-perp-panel-v1-r1/attempts/"
      "utc-clock-hour-seasonality-perp-panel-v1-r1-u2")

FULL = ["full", "fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks",
        "no_funding_full", "cost_attrition_40bps", "boundary_alt_full"]

layers = json.load(open(os.path.join(U2, "artifacts", "dca_layer_histogram.json")))
print("level_00 (layers[0]) =", layers["level_00"])

tot = 0
per_grid = {}
seen = {}
for g in FULL:
    p = os.path.join(U2, "artifacts", "grid_%s.csv" % g)
    n = 0
    s = 0
    rows = 0
    with open(p) as fh:
        for row in csv.DictReader(fh):
            rows += 1
            n += int(row["episodes"])
            s += int(row["windows_seen"])
    per_grid[g] = n
    seen[g] = s
    tot += n
    print("%-22s rows=%5d episodes_sum=%9d windows_seen_sum=%9d" % (g, rows, n, s))

print("sum(episodes) over FULL_WINDOW_GRID_KINDS =", tot)
print("layers0 - sum =", layers["level_00"] - tot)

# per-grid breakdown of the two red counters + assertion source values
res = json.load(open(os.path.join(U2, "result.json")))
print("structural_counters:")
print(json.dumps(res["structural_counters"], indent=2))
print("coverage cells per grid:", {k: v for k, v in res["coverage"].items() if isinstance(v, int)})
for key in ("cohort_count", "case_evaluations_total", "cohort_survivor_count",
            "disposition", "verdict_recommendation", "verdict_recommendation_final"):
    if key in res:
        print("%s = %r" % (key, res[key]))
