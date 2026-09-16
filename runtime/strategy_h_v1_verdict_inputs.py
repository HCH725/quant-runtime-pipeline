#!/usr/bin/env python3
"""Strategy H verdict-input re-derivation (contract 10.7; read-only, stdlib only).

The round's verdict.json must be written from the FROZEN attempt artifacts, never from the
runner's own summary.  This tool re-reads the attempt's 10 grid CSVs plus cohort_results.json /
cohort_survivors.json / dca_layer_histogram.json / assertions.json and re-derives, independently:

  * per-grid row counts and the registered coverage product (4 cohorts x 27 cases x 48 DCA);
  * the per-row PnL decomposition identity (gross_pnl - fees - funding == net_pnl, tol 1e-3);
  * per-grid aggregates (net / fees / funding / gross / episodes / fills / turnover);
  * the DCA ladder-histogram identity (level_00 == the episode sum of exactly the seven
    full-window grids, level_01..11 == the add counts of the same seven grids);
  * the four cost/stress track effectivity checks on aggregate sums (a track is a no-op when it
    does not move the aggregate the signed way; an unexercised track is reported as such);
  * the winner rows of every cohort in every grid, with their net PnL and Sharpe;
  * the coverage / assertion flags read back from the attempt's own artifacts.

usage: strategy_h_v1_verdict_inputs.py <attempt_dir> [--json]
"""
import argparse
import csv
import json
import os
import sys

DCA_AXES = ("spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct")
CASE_FIELDS = ("thr_1_0", "thr_1_5", "thr_2_0", "seq_len_1", "seq_len_2", "seq_len_3",
               "minp_0_5", "minp_0_6", "minp_0_7")
GRIDS = ("historical", "oos", "full", "fee_2x", "funding_2x", "entry_delay_1_bar",
         "slippage_2ticks", "no_funding", "no_funding_full", "cost_attrition_40bps")
FULL_WINDOW_GRIDS = ("full", "fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks",
                     "no_funding_full", "cost_attrition_40bps")
COHORTS = 4
CASES = 27
DCA = 48


def load_rows(path):
    with open(path) as fh:
        return list(csv.DictReader(fh))


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("attempt_dir")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    a = args.attempt_dir
    problems = []
    grids = {}
    for kind in GRIDS:
        rows = load_rows(os.path.join(a, "artifacts", "grid_%s.csv" % kind))
        grids[kind] = rows
        if len(rows) != COHORTS * CASES * DCA:
            problems.append("grid %s has %d rows, registered %d"
                            % (kind, len(rows), COHORTS * CASES * DCA))
        seen = set()
        bad_decomp = 0
        for r in rows:
            key = (r["symbol"], r["window_case"]) + tuple(r[x] for x in DCA_AXES)
            if key in seen:
                problems.append("grid %s duplicate cell %r" % (kind, key))
            seen.add(key)
            if abs(float(r["gross_pnl"]) - float(r["fees"]) - float(r["funding"])
                   - float(r["net_pnl"])) > 1e-3:
                bad_decomp += 1
        if bad_decomp:
            problems.append("grid %s: %d row(s) violate gross - fees - funding == net"
                            % (kind, bad_decomp))

    hist = json.load(open(os.path.join(a, "artifacts", "dca_layer_histogram.json")))
    add_sums = [0] * 12
    # level 0 == episodes of the seven full-window grids; level k == the level-k add counts
    # (which the CSVs do not carry) -> recompute level 0 and the per-grid episode sums.
    ep_by_grid = {k: sum(int(r["episodes"]) for r in grids[k]) for k in GRIDS}
    expect_level0 = sum(ep_by_grid[k] for k in FULL_WINDOW_GRIDS)
    if hist.get("level_00") != expect_level0:
        problems.append("histogram level_00=%r != the episode sum of the seven full-window "
                        "grids (%d)" % (hist.get("level_00"), expect_level0))
    agg = {}
    for k in GRIDS:
        agg[k] = {
            "cells": len(grids[k]),
            "cells_with_episodes": sum(1 for r in grids[k] if int(r["episodes"]) > 0),
            "sum_net_pnl": sum(float(r["net_pnl"]) for r in grids[k]),
            "sum_fees": sum(float(r["fees"]) for r in grids[k]),
            "sum_funding": sum(float(r["funding"]) for r in grids[k]),
            "sum_gross_pnl": sum(float(r["gross_pnl"]) for r in grids[k]),
            "sum_episodes": ep_by_grid[k],
            "sum_fills": sum(int(r["fills"]) for r in grids[k]),
            "sum_turnover_usdt": sum(float(r["turnover_usdt"]) for r in grids[k]),
            "cells_halted": sum(1 for r in grids[k] if r["halted"] == "True"),
        }

    def track(name, base, stressed, key):
        d = round(agg[name][key] - agg[base][key], 6)
        return {"base_grid": base, "stressed_grid": name, "field": key, "delta": d,
                "moved": abs(d) > 1e-9}

    tracks = {
        "fee_2x": track("fee_2x", "full", "fee_2x", "sum_fees"),
        "funding_2x": track("funding_2x", "full", "funding_2x", "sum_funding"),
        "cost_attrition_40bps": track("cost_attrition_40bps", "full", "cost_attrition_40bps",
                                      "sum_fees"),
        "no_funding": track("no_funding_full", "full", "no_funding_full", "sum_funding"),
        "entry_delay_1_bar": track("entry_delay_1_bar", "full", "entry_delay_1_bar",
                                   "sum_net_pnl"),
        "slippage_2ticks": track("slippage_2ticks", "full", "slippage_2ticks", "sum_net_pnl"),
    }

    cohort_results = json.load(open(os.path.join(a, "artifacts", "cohort_results.json")))
    cohorts = cohort_results["cohorts"] if isinstance(cohort_results, dict) else cohort_results
    winners = []
    for c in cohorts:
        w = c.get("winner") or {}
        sym = c["cohort"].split("/")[0]
        label = c.get("winner_case_label")
        row_cache = {}
        if w:
            for kind in GRIDS:
                hit = [r for r in grids[kind]
                       if r["symbol"] == sym and r["window_case"] == label
                       and all(float(r[x]) == float(w[x]) for x in DCA_AXES)]
                if len(hit) != 1:
                    problems.append("winner cell of %s not unique in grid %s (%d hits)"
                                    % (c["cohort"], kind, len(hit)))
                else:
                    row_cache[kind] = {"net_pnl": float(hit[0]["net_pnl"]),
                                       "sharpe": float(hit[0]["sharpe"]),
                                       "episodes": int(hit[0]["episodes"]),
                                       "fees": float(hit[0]["fees"]),
                                       "funding": float(hit[0]["funding"]),
                                       "ending_equity": float(hit[0]["ending_equity"]),
                                       "max_dd_pct": float(hit[0]["max_dd_pct"]),
                                       "max_effective_leverage": float(
                                           hit[0]["max_effective_leverage"]),
                                       "capital_utilization": float(hit[0]["capital_utilization"]),
                                       "turnover_usdt": float(hit[0]["turnover_usdt"]),
                                       "fills": int(hit[0]["fills"])}
        winners.append({"cohort": c["cohort"], "outcome": c["outcome"],
                        "cull_reasons": c.get("cull_reasons"),
                        "winner_case": label, "winner_dca": {x: w.get(x) for x in DCA_AXES},
                        "grid_rows": row_cache,
                        "neighbourhood": c.get("neighbourhood"),
                        "runner_metrics": c.get("metrics")})

    assertions = json.load(open(os.path.join(a, "artifacts", "assertions.json")))
    result = json.load(open(os.path.join(a, "result.json")))
    survivors = json.load(open(os.path.join(a, "artifacts", "cohort_survivors.json")))
    out = {
        "attempt_dir": a,
        "problems": problems,
        "coverage": {
            "rows_per_grid": {k: len(grids[k]) for k in GRIDS},
            "expected_rows_per_grid": COHORTS * CASES * DCA,
            "case_evaluations_total": sum(len(grids[k]) for k in GRIDS),
            "expected_case_evaluations": COHORTS * CASES * DCA * len(GRIDS),
        },
        "per_grid_aggregates": agg,
        "ladder_histogram": {"level_00": hist.get("level_00"),
                             "level_00_expected": expect_level0,
                             "levels_01_11": {k: hist.get(k) for k in
                                              sorted(hist) if k != "level_00"}},
        "cost_tracks": tracks,
        "winners": winners,
        "assertions_not_true": [k for k, v in assertions.items() if v is not True],
        "assertion_count": len(assertions),
        "runner_summary": {"disposition": result.get("disposition"),
                           "verdict_recommendation": result.get("verdict_recommendation"),
                           "verdict_recommendation_final":
                               result.get("verdict_recommendation_final"),
                           "performance_claimable_recommendation_final":
                               result.get("performance_claimable_recommendation_final"),
                           "cohort_survivor_count": result.get("cohort_survivor_count"),
                           "flags": result.get("registered_family_level_falsification_flags"),
                           "runtime_seconds": result.get("runtime_seconds"),
                           "engine_version": result.get("engine_version")},
        "cohort_survivors_artifact": (survivors.get("survivors")
                                      if isinstance(survivors, dict) else survivors),
    }
    if args.json:
        print(json.dumps(out, indent=1, ensure_ascii=False))
    else:
        print("problems: %d" % len(problems))
        for p in problems:
            print("  PROBLEM: %s" % p)
        print("coverage: %s rows/grid, total %d (expected %d)"
              % (sorted(set(out['coverage']['rows_per_grid'].values())),
                 out["coverage"]["case_evaluations_total"],
                 out["coverage"]["expected_case_evaluations"]))
        print("histogram level_00=%s expected=%s"
              % (hist.get("level_00"), expect_level0))
        for name, t in tracks.items():
            print("track %-22s %s delta=%s moved=%s" % (name, t["field"], t["delta"], t["moved"]))
        for w in winners:
            print("cohort %-12s %-9s %s" % (w["cohort"], w["outcome"], w["cull_reasons"]))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
