#!/usr/bin/env python3
"""Independent host-side re-derivation of topological-anomaly run evidence.

This reader never executes Qlib and never chooses a different winner.  It checks exact registered
coverage, parameter-cell uniqueness, PnL decomposition, and that every recorded winner resolves to
exactly one row in every grid before the final verdict writer is allowed to publish verdict.json.
"""
import argparse
import csv
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAMILY_ID = "cross-sectional-topological-anomaly-score-intraday-equity-return-predictability-2026-09-02"
GRIDS = ["historical", "oos", "full", "fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks", "no_funding", "no_funding_full", "cost_attrition_40bps"]
DCA = ["spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct"]
COHORTS = [f"{s}/{tf}" for tf in ("5m", "15m", "30m", "1h") for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT")]
EXPECTED_ROWS = 2304


def read_rows(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def f(row, key):
    return float(row[key])


def cell_key(row):
    return (row["symbol"], row["timeframe"], int(row["method_code"]),
            *(float(row[k]) for k in DCA))


def winner_key(cohort, winner):
    symbol, timeframe = cohort.split("/", 1)
    return (symbol, timeframe, int(winner["method_code"]),
            *(float(winner[k]) for k in DCA))


def derive(attempt_dir):
    problems = []
    grids = {}
    seen_all = {}
    aggregates = {}
    for grid in GRIDS:
        path = os.path.join(attempt_dir, "artifacts", "grid_%s.csv" % grid)
        if not os.path.isfile(path):
            problems.append("missing %s" % path)
            continue
        rows = read_rows(path)
        grids[grid] = rows
        if len(rows) != EXPECTED_ROWS:
            problems.append("grid %s rows=%d expected=%d" % (grid, len(rows), EXPECTED_ROWS))
        seen = set()
        bad_decomp = 0
        for row in rows:
            key = cell_key(row)
            if key in seen:
                problems.append("grid %s duplicate cell %r" % (grid, key))
            seen.add(key)
            if abs(f(row, "gross_pnl") - f(row, "fees") - f(row, "funding") - f(row, "net_pnl")) > 1e-3:
                bad_decomp += 1
            seen_all.setdefault(key, set()).add(grid)
        if bad_decomp:
            problems.append("grid %s decomposition failures=%d" % (grid, bad_decomp))
        aggregates[grid] = {
            "rows": len(rows), "cells_with_episodes": sum(1 for r in rows if int(r["episodes"]) > 0),
            "sum_net_pnl": sum(f(r, "net_pnl") for r in rows),
            "sum_gross_pnl": sum(f(r, "gross_pnl") for r in rows),
            "sum_fees": sum(f(r, "fees") for r in rows),
            "sum_funding": sum(f(r, "funding") for r in rows),
            "sum_episodes": sum(int(r["episodes"]) for r in rows),
            "sum_fills": sum(int(r["fills"]) for r in rows),
            "sum_turnover_usdt": sum(f(r, "turnover_usdt") for r in rows),
        }
    for key, grids_seen in seen_all.items():
        if grids_seen != set(GRIDS):
            problems.append("cell %r missing grids %r" % (key, sorted(set(GRIDS) - grids_seen)))
    result_path = os.path.join(attempt_dir, "result.json")
    cohort_path = os.path.join(attempt_dir, "artifacts", "cohort_results.json")
    survivor_path = os.path.join(attempt_dir, "artifacts", "cohort_survivors.json")
    assertions_path = os.path.join(attempt_dir, "artifacts", "assertions.json")
    result = json.load(open(result_path, encoding="utf-8"))
    cohorts = json.load(open(cohort_path, encoding="utf-8"))
    survivors = json.load(open(survivor_path, encoding="utf-8"))
    assertions = json.load(open(assertions_path, encoding="utf-8"))
    if [c.get("cohort") for c in cohorts] != COHORTS:
        problems.append("cohort order/set mismatch")
    labels = [s.get("cohort") for s in survivors]
    if labels != result.get("cohort_survivors"):
        problems.append("survivor labels disagree with result.json")
    if result.get("case_evaluations_total") != len(GRIDS) * EXPECTED_ROWS:
        problems.append("result case_evaluations_total mismatch")
    if result.get("cohort_survivor_count") != len(survivors):
        problems.append("result survivor count mismatch")
    winners = []
    for record in cohorts:
        winner = record.get("winner")
        if not winner:
            continue
        key = winner_key(record["cohort"], winner)
        hits = []
        for grid, rows in grids.items():
            matching = [r for r in rows if cell_key(r) == key]
            if len(matching) != 1:
                problems.append("winner %s hits=%d in grid %s" % (record["cohort"], len(matching), grid))
            else:
                hits.append((grid, matching[0]))
        winners.append({"cohort": record["cohort"], "outcome": record["outcome"],
                        "winner": winner, "winner_case_label": record.get("winner_case_label"),
                        "metrics": record.get("metrics"),
                        "grid_net_pnl": {grid: f(row, "net_pnl") for grid, row in hits}})
    false_assertions = sorted(k for k, value in assertions.items() if value is not True)
    if false_assertions:
        problems.append("assertions false: %s" % ",".join(false_assertions))
    return {"ok": not problems, "problems": problems, "family_id": result.get("family_id"),
            "round_id": result.get("round_id"), "run_id": result.get("run_id"),
            "coverage": {"grids": GRIDS, "rows_per_grid": {g: len(grids.get(g, [])) for g in GRIDS},
                         "rows_total": sum(len(v) for v in grids.values()), "expected_total": len(GRIDS) * EXPECTED_ROWS},
            "aggregates": aggregates, "winners": winners, "survivor_count": len(survivors),
            "assertions": assertions, "result": result}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("attempt_dir")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    out = derive(os.path.abspath(args.attempt_dir))
    if args.json:
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        print("ok=%s rows=%d survivors=%d problems=%d" %
              (out["ok"], out["coverage"]["rows_total"], out["survivor_count"], len(out["problems"])))
        for problem in out["problems"]:
            print("FAIL: " + problem)
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
