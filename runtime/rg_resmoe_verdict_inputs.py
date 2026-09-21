#!/usr/bin/env python3
"""Read-only RG-ResMoE attempt verifier used before writing verdict.json."""
import argparse
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import rg_resmoe_counts as registered

FAMILY_ID = registered.FAMILY_ID
GRIDS = list(registered.GRID_KINDS)
DCA = list(registered.DCA_AXES)
COHORTS = [c["symbol"] + "/" + c["timeframe"] for c in registered.cohorts()]
EXPECTED_ROWS = registered.expected_counts()["case_evaluations_per_grid"]
EXPECTED_TOTAL = registered.expected_counts()["case_evaluations_total"]


def rows(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def cell(row):
    return (row["symbol"], row["timeframe"], int(row["model_code"]), *(float(row[k]) for k in DCA))


def expected_cells():
    return {
        (cohort["symbol"], cohort["timeframe"], int(strategy["model_code"]),
         *(float(config[key]) for key in DCA))
        for cohort in registered.cohorts()
        for strategy in registered.STRATEGY_CASES
        for config in registered.dca_cases()
    }


def derive(attempt_dir):
    problems, grids, aggregates = [], {}, {}
    expected = expected_cells()
    for grid in GRIDS:
        path = os.path.join(attempt_dir, "artifacts", "grid_%s.csv" % grid)
        if not os.path.isfile(path):
            problems.append("missing %s" % path)
            continue
        data = rows(path); grids[grid] = data
        if len(data) != EXPECTED_ROWS:
            problems.append("grid %s rows=%d expected=%d" % (grid, len(data), EXPECTED_ROWS))
        seen = set()
        for row in data:
            key = cell(row)
            if key in seen: problems.append("grid %s duplicate %r" % (grid, key))
            seen.add(key)
            if abs(float(row["gross_pnl"]) - float(row["fees"]) - float(row["funding"]) - float(row["net_pnl"])) > 1e-3:
                problems.append("grid %s decomposition failure %r" % (grid, key))
        if seen != expected:
            missing = sorted(expected - seen)
            extra = sorted(seen - expected)
            if missing:
                problems.append("grid %s missing %d registered cells" % (grid, len(missing)))
            if extra:
                problems.append("grid %s has %d unregistered cells" % (grid, len(extra)))
        aggregates[grid] = {"rows": len(data), "cells_with_episodes": sum(int(r["episodes"]) > 0 for r in data),
                            "sum_net_pnl": sum(float(r["net_pnl"]) for r in data), "sum_gross_pnl": sum(float(r["gross_pnl"]) for r in data),
                            "sum_fees": sum(float(r["fees"]) for r in data), "sum_funding": sum(float(r["funding"]) for r in data),
                            "sum_episodes": sum(int(r["episodes"]) for r in data), "sum_fills": sum(int(r["fills"]) for r in data),
                            "sum_turnover_usdt": sum(float(r["turnover_usdt"]) for r in data)}
    result = json.load(open(os.path.join(attempt_dir, "result.json"), encoding="utf-8"))
    cohorts = json.load(open(os.path.join(attempt_dir, "artifacts", "cohort_results.json"), encoding="utf-8"))
    survivors = json.load(open(os.path.join(attempt_dir, "artifacts", "cohort_survivors.json"), encoding="utf-8"))
    assertions = json.load(open(os.path.join(attempt_dir, "artifacts", "assertions.json"), encoding="utf-8"))
    if [r.get("cohort") for r in cohorts] != COHORTS: problems.append("cohort order/set mismatch")
    if result.get("cohort_survivors") != [r.get("cohort") for r in survivors]: problems.append("survivor labels disagree")
    if result.get("expected_case_evaluations") != EXPECTED_TOTAL: problems.append("result expected total mismatch")
    if result.get("case_evaluations_total") != EXPECTED_TOTAL: problems.append("result total mismatch")
    winners = []
    for record in cohorts:
        winner = record.get("winner")
        if not winner: continue
        symbol, timeframe = record["cohort"].split("/", 1)
        key = (symbol, timeframe, int(winner["model_code"]), *(float(winner[k]) for k in DCA))
        hit_map = {}
        for grid, data in grids.items():
            hit = [r for r in data if cell(r) == key]
            if len(hit) != 1: problems.append("winner %s hits=%d in %s" % (record["cohort"], len(hit), grid))
            else: hit_map[grid] = {"net_pnl": float(hit[0]["net_pnl"]), "sharpe": float(hit[0]["sharpe"]), "episodes": int(hit[0]["episodes"]), "ending_equity": float(hit[0]["ending_equity"]), "fees": float(hit[0]["fees"]), "funding": float(hit[0]["funding"]), "max_dd_pct": float(hit[0]["max_dd_pct"]), "max_effective_leverage": float(hit[0]["max_effective_leverage"]), "capital_utilization": float(hit[0]["capital_utilization"]), "turnover_usdt": float(hit[0]["turnover_usdt"]), "fills": int(hit[0]["fills"])}
        winners.append({"cohort": record["cohort"], "outcome": record["outcome"], "winner": winner, "metrics": record.get("metrics"), "cull_reasons": record.get("cull_reasons"), "grid_rows": hit_map})
    false_assertions = sorted(k for k, value in assertions.items() if value is not True)
    if false_assertions: problems.append("assertions false: %s" % ",".join(false_assertions))
    return {"ok": not problems, "problems": problems, "family_id": result.get("family_id"), "round_id": result.get("round_id"), "run_id": result.get("run_id"),
            "coverage": {"grids": GRIDS, "rows_per_grid": {g: len(grids.get(g, [])) for g in GRIDS}, "rows_total": sum(len(v) for v in grids.values()), "expected_total": len(GRIDS) * EXPECTED_ROWS},
            "aggregates": aggregates, "winners": winners, "survivor_count": len(survivors), "assertions": assertions, "result": result}


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("attempt_dir"); ap.add_argument("--json", action="store_true"); args = ap.parse_args(argv)
    out = derive(os.path.abspath(args.attempt_dir))
    print(json.dumps(out, indent=2, ensure_ascii=False) if args.json else "ok=%s rows=%d survivors=%d problems=%d" % (out["ok"], out["coverage"]["rows_total"], out["survivor_count"], len(out["problems"])))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
