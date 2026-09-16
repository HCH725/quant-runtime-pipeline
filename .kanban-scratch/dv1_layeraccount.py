"""Test: is layers[0] - sum(episodes) explained by the winner-diagnostics re-runs? (card t_50c28da5)"""
import csv
import json
import os

U2 = ("/Volumes/ExpansionDrive/qlib-results/utc-clock-hour-seasonality-perp-panel-v1"
      "/rounds/utc-clock-hour-seasonality-perp-panel-v1-r1/attempts/"
      "utc-clock-hour-seasonality-perp-panel-v1-r1-u2")
A = os.path.join(U2, "artifacts")

res = json.load(open(os.path.join(U2, "result.json")))
cohorts = res["cohort_results"]
print("cohort winners:")
extra = 0
for c in cohorts:
    w = c["winner"]
    if w is None:
        print("  %-10s no winner (reason=%s)" % (c["cohort"], c.get("no_winner_reason")))
        continue
    print("  %-10s winner=%s case=%s dca=(%s,%s,%s,%s)" % (
        c["cohort"], w.get("case_name"), c.get("winner_case_label"),
        w["spacing_pct"], w["size_multiplier"], w["breakeven_tp_pct"], w["invalidation_pct"]))

# recompute the diagnostics inflation from grid_full.csv
cells = {}
with open(os.path.join(A, "grid_full.csv")) as fh:
    for row in csv.DictReader(fh):
        key = (row["symbol"], row["case_name"], row["spacing_pct"], row["size_multiplier"],
               row["breakeven_tp_pct"], row["invalidation_pct"])
        cells[key] = row

for c in cohorts:
    w = c["winner"]
    if w is None:
        continue
    sym = c["cohort"].split("/")[0]
    dca = (str(w["spacing_pct"]), str(w["size_multiplier"]),
           str(w["breakeven_tp_pct"]), str(w["invalidation_pct"]))
    cases = [c["winner_case_label"]] if "winner_case_label" in c else []
    # winner case label from the frozen row keys
    wcase = None
    for name in ("long_only", "short_only", "secondary_long_only", "long_short",
                 "long_secondary_long", "short_secondary_long", "all_legs"):
        k = (sym, name) + dca
        if k in cells and wcase is None:
            pass
    # the winner cell identity comes from the frozen winner dict itself
    wcase = None
    for name in ("long_only", "short_only", "secondary_long_only", "long_short",
                 "long_secondary_long", "short_secondary_long", "all_legs"):
        k = (sym, name) + dca
        if k not in cells:
            continue
        row = cells[k]
        ok = all(str(row[f]) == str(w[f]) for f in ("leg_long", "leg_short", "leg_secondary"))
        if ok:
            wcase = name
    print("  %-10s resolved winner case=%s" % (sym, wcase))
    run_cases = [wcase, "long_only", "short_only", "secondary_long_only"]
    sub = 0
    for name in run_cases:
        row = cells[(sym, name) + dca]
        sub += int(row["episodes"])
        print("      re-run %-20s episodes=%d" % (name, int(row["episodes"])))
    extra += sub
    print("      subtotal=%d" % sub)

print("diagnostics-inflation estimate =", extra)
print("observed layers0 - sum(episodes) =", 29155)
print("MATCH" if extra == 29155 else "NO MATCH")
