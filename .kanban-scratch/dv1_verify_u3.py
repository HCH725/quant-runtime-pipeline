"""Read back the Strategy D r1-u3 artifacts and prove the two fixes are measurement-neutral.

Comparisons:
  1. every assertion true, structural counters (nonzero only)
  2. layers[0] == sum(episodes) over the eight full-window grids (the u2 gap of 29,155 must be gone)
  3. all 12 grid CSVs identical to r1-u2 (sorted row compare + byte compare)
  4. the real-data funding mapping premise: the 2025-10-01T00:00Z settlement under both conventions
"""
import csv
import hashlib
import json
import os
import sys

ROOT = ("/Volumes/ExpansionDrive/qlib-results/utc-clock-hour-seasonality-perp-panel-v1/rounds/"
        "utc-clock-hour-seasonality-perp-panel-v1-r1/attempts")
U2 = os.path.join(ROOT, "utc-clock-hour-seasonality-perp-panel-v1-r1-u2")
U3 = os.path.join(ROOT, "utc-clock-hour-seasonality-perp-panel-v1-r1-u3")

FULL = ["full", "fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks",
        "no_funding_full", "cost_attrition_40bps", "boundary_alt_full"]
GRIDS = ["historical", "oos", "full", "fee_2x", "funding_2x", "entry_delay_1_bar",
         "slippage_2ticks", "no_funding", "no_funding_full", "cost_attrition_40bps",
         "boundary_alt_oos", "boundary_alt_full"]

res = json.load(open(os.path.join(U3, "result.json")))
problems = []

# ---- 1. assertions / counters
assertions = res["assertions"]
false_keys = [k for k, v in assertions.items() if not v]
print("assertions: %d total, false=%s" % (len(assertions), false_keys or "none"))
if false_keys:
    problems.append("false assertions: %s" % false_keys)
print("structural counters (nonzero grid/name only):")
for grid, counters in sorted(res["structural_counters"].items()):
    nz = {k: v for k, v in counters.items() if v}
    if nz:
        print("   %-20s %s" % (grid, nz))
        if "funding_bar_out_of_hold" in nz:
            problems.append("funding_bar_out_of_hold still nonzero in %s" % grid)

# ---- 2. ladder identity
hist = res["dca_layer_histogram"]
tot = 0
for g in FULL:
    with open(os.path.join(U3, "artifacts", "grid_%s.csv" % g)) as fh:
        for row in csv.DictReader(fh):
            tot += int(row["episodes"])
print("level_00 = %d ; sum(episodes over %d full-window grids) = %d ; equal=%s"
      % (hist["level_00"], len(FULL), tot, hist["level_00"] == tot))
print("u2 level_00 = 22601301 ; delta = %d (expected 29155 = the diagnostic episodes)"
      % (22601301 - hist["level_00"]))
if hist["level_00"] != tot:
    problems.append("ladder identity still broken")
if 22601301 - hist["level_00"] != 29155:
    problems.append("ladder delta is not the diagnostic episode count")

# ---- 3. grid CSV equality u2 vs u3
diff_grids = []
for g in GRIDS:
    a = os.path.join(U2, "artifacts", "grid_%s.csv" % g)
    b = os.path.join(U3, "artifacts", "grid_%s.csv" % g)
    ya, yb = open(a, "rb").read(), open(b, "rb").read()
    ha = hashlib.sha256(ya).hexdigest()
    hb = hashlib.sha256(yb).hexdigest()
    same_bytes = ya == yb
    if not same_bytes:
        la = sorted(ya.decode().splitlines())
        lb = sorted(yb.decode().splitlines())
        same_rows = la == lb
        diff_grids.append((g, "rows-equal" if same_rows else "DIFFERENT", ha[:12], hb[:12]))
    else:
        diff_grids.append((g, "identical", ha[:12], hb[:12]))
for g, verdict, ha, hb in diff_grids:
    print("   grid %-22s %-12s u2=%s u3=%s" % (g, verdict, ha, hb))
if any(v == "DIFFERENT" for _g, v, _a, _b in diff_grids):
    problems.append("grid CSVs differ beyond row order")

res2 = json.load(open(os.path.join(U2, "result.json")))
same_metrics = all(
    res["cohort_results"][i]["metrics"] == res2["cohort_results"][i]["metrics"]
    for i in range(len(res["cohort_results"])))
print("cohort_results metrics identical to u2: %s" % same_metrics)
print("panel_diagnostic identical: %s" % (res["panel_diagnostic"] == res2["panel_diagnostic"]))
print("timezone_fragility identical: %s" % (res["timezone_fragility"] == res2["timezone_fragility"]))
print("window_instability identical: %s" % (res["window_instability"] == res2["window_instability"]))

# ---- 4. coverage / disposition
print("coverage: %s" % {k: v for k, v in res["coverage"].items() if isinstance(v, int)})
for k in ("cohort_count", "case_evaluations_total", "cohort_survivor_count", "disposition",
          "verdict_recommendation", "verdict_recommendation_final",
          "registered_family_level_falsification_flags"):
    print("%s = %r" % (k, res.get(k)))
print("runtime_seconds = %s" % res.get("runtime_seconds"))

# ---- 5. real-data mapping premise (no container needed)
BASE = 1640995200000          # 2022-01-01T00:00:00Z
I0 = 32856                    # first OOS bar index (2025-10-01T00:00:00Z)
T = 1759276800000             # 2025-10-01T00:00:00Z settlement instant (measured 0 ms jitter)
idx = (T - BASE) // 3600000
print("settlement bar index under the slice: %d ; OOS slice starts at %d" % (idx, I0))
print("half-open mapping (scheduling/old guard) = %d (fires) ; containment mapping (new guard) = %d"
      % (idx - 1, idx))

print()
print("PROBLEMS: %s" % (problems or "none"))
sys.exit(1 if problems else 0)
