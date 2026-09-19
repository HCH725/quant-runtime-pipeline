#!/usr/bin/env python3
"""Independent host-side verification of the published r2-u3 attempt artifacts (contract 11 /
verdict independent_verification block).  Reads ONLY the published attempt artifacts (the grid
CSVs + result.json + cohort_results.json) and re-derives, from their text:

  1. per-grid row counts and the registered product identity (4 cohorts x 6 cases x 48 DCA = 1152);
  2. the per-row accounting identity gross_pnl - fees - funding == net_pnl and
     ending_equity == 30000 + net_pnl;
  3. the winners' rows, re-read field by field out of the grid CSVs and compared with
     cohort_results.json (max abs diff per field, per grid);
  4. the cohort selector, recomputed from the CSV text under the registered rules
     (net_pnl > 0, sharpe > 0, episodes >= min_episodes_is; rank sharpe desc, net_pnl desc,
     registered lexical case order) and compared with the elected winners;
  5. the DCA ladder identity (dca_layer_histogram) against the CSV episode/fill sums;
  6. the critical structural counters.

Writes .kanban-scratch/cmt_r2/cmt_verification.json
"""
import csv
import json
import os
import sys

FAMILY = "continuous-macro-timing-growth-defensive-style-allocation-2026-09-02"
ROUND = FAMILY + "-r2"
RUN = ROUND + "-u3"
RDIR = os.path.join("/Volumes/ExpansionDrive/qlib-results", FAMILY, "rounds", ROUND)
ATT = os.path.join(RDIR, "attempts", RUN)
OUT = os.path.join("/Users/hong/workspace/quant-runtime-pipeline", ".kanban-scratch", "cmt_r2",
                   "cmt_verification.json")
GRIDS = ("historical", "oos", "full", "fee_2x", "funding_2x", "entry_delay_1_bar",
         "slippage_2ticks", "no_funding", "no_funding_full", "cost_attrition_40bps")
CASE_FIELDS = ("var_full", "var_core", "var_rate", "sm_ewma", "sm_none")
DCA_FIELDS = ("spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct")
CRITICAL_COUNTERS = ("causality_probe_mismatch", "state_not_derived_from_the_registered_rule",
                     "bar_grid_not_contiguous", "panel_grid_mismatch", "case_not_registered",
                     "entry_before_a_defined_signal", "signal_undefined_on_event_bar",
                     "funding_bar_out_of_hold", "signal_before_the_registered_warmup",
                     "expandable_stat_undefined", "expanding_stat_undefined", "score_nonfinite")

res = json.load(open(os.path.join(ATT, "result.json")))
cres = json.load(open(os.path.join(ATT, "artifacts", "cohort_results.json")))
rows_of = {}
for g in GRIDS:
    with open(os.path.join(ATT, "artifacts", "grid_%s.csv" % g), newline="") as fh:
        rows_of[g] = list(csv.DictReader(fh))

# 1. coverage / registered product
coverage = {}
dup_free = True
for g, rows in rows_of.items():
    coverage[g] = len(rows)
    keys = [(r["symbol"], r["timeframe"], r["window_case"], r["spacing_pct"],
             r["size_multiplier"], r["breakeven_tp_pct"], r["invalidation_pct"]) for r in rows]
    dup_free = dup_free and (len(set(keys)) == len(keys))
expected_per_grid = 4 * 6 * 48
product_ok = dup_free and all(v == expected_per_grid for v in coverage.values()) and \
    len({r["window_case"] for r in rows_of["full"]}) == 6 and \
    len({r["sm_ewma"] + r["sm_none"] for r in rows_of["full"]}) == 2

# 2. accounting identities
acct_failures = 0
equity_failures = 0
worst_acct = 0.0
worst_equity = 0.0
aggregates = {}
for g, rows in rows_of.items():
    agg = {"rows": len(rows), "fills": 0, "turnover_usdt": 0.0, "net_pnl": 0.0, "episodes": 0,
           "fees": 0.0, "funding": 0.0}
    for r in rows:
        net = float(r["net_pnl"]); fees = float(r["fees"]); fund = float(r["funding"])
        gross = float(r["gross_pnl"]); end = float(r["ending_equity"])
        d = abs(gross - fees - fund - net)
        worst_acct = max(worst_acct, d)
        if d > 1e-3:
            acct_failures += 1
        d2 = abs(end - (30000.0 + net))
        worst_equity = max(worst_equity, d2)
        if d2 > 1e-3:
            equity_failures += 1
        agg["fills"] += int(r["fills"]); agg["turnover_usdt"] += float(r["turnover_usdt"])
        agg["net_pnl"] += net; agg["episodes"] += int(r["episodes"])
        agg["fees"] += fees; agg["funding"] += fund
    for k in ("turnover_usdt", "net_pnl", "fees", "funding"):
        agg[k] = round(agg[k], 6)
    aggregates[g] = agg

# 3. winners re-read from the CSVs
def key_of(winner):
    sym = None
    return tuple(str(winner[f]) if f in ("spacing_pct", "size_multiplier", "breakeven_tp_pct",
                                         "invalidation_pct") else int(winner[f])
                 for f in CASE_FIELDS)

winner_rows = {}
for c in cres:
    label = c["cohort"]
    if c.get("winner") is None:
        winner_rows[label] = {"note": "no elected winner"}
        continue
    want = {f: int(c["winner"][f]) for f in CASE_FIELDS}
    want.update({a: float(c["winner"][a]) for a in DCA_FIELDS})
    sym, tf = label.split("/")
    entry = {"max_abs_diff_vs_cohort_results": {}, "grids": {}}
    for g in GRIDS:
        hits = [r for r in rows_of[g]
                if r["symbol"] == sym and r["timeframe"] == tf
                and all(int(r[f]) == want[f] for f in CASE_FIELDS)
                and all(float(r[a]) == want[a] for a in DCA_FIELDS)]
        if len(hits) != 1:
            entry["grids"][g] = {"rows_found": len(hits)}
            continue
        r = hits[0]
        metrics = c.get("metrics") or {}
        if g in ("fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks"):
            ref = (metrics.get("robustness") or {}).get(g) or {}
        elif g == "no_funding":
            ref = metrics.get("no_funding_reference") or {}
        elif g == "no_funding_full":
            ref = metrics.get("no_funding_full_reference") or {}
        else:
            ref = metrics.get(g) or {}
        diffs = {}
        for field, csv_key in (("net_pnl", "net_pnl"), ("sharpe", "sharpe"),
                               ("episodes", "episodes"), ("ending_equity", "ending_equity"),
                               ("fees", "fees"), ("funding", "funding"),
                               ("max_dd_pct", "max_dd_pct"), ("cagr", "cagr")):
            if field in ref:
                diffs[field] = abs(float(r[csv_key]) - float(ref[field]))
        entry["grids"][g] = {"rows_found": 1, "diffs": diffs,
                             "fills": int(r["fills"]), "turnover_usdt": round(float(r["turnover_usdt"]), 6),
                             "bars_in_market": int(r["bars_in_market"]),
                             "days": int(float(r["days"]))}
        entry["max_abs_diff_vs_cohort_results"][g] = max(diffs.values()) if diffs else None
    winner_rows[label] = entry

winner_diff_max = max((v for e in winner_rows.values()
                       if "max_abs_diff_vs_cohort_results" in e
                       for v in e["max_abs_diff_vs_cohort_results"].values() if v is not None),
                      default=None)

# 4. selector recomputation from the CSV text (historical grid only)
ORDER = ["full__sm", "core__sm", "rate__sm", "full__raw", "core__raw", "rate__raw"]
selector = {}
for c in cres:
    sym, tf = c["cohort"].split("/")
    mine = [r for r in rows_of["historical"] if r["symbol"] == sym and r["timeframe"] == tf]
    eligible = [r for r in mine
                if float(r["net_pnl"]) > 0 and float(r["sharpe"]) > 0 and int(r["episodes"]) >= 10]
    if not eligible:
        computed = None
    else:
        best = sorted(eligible, key=lambda r: (-float(r["sharpe"]), -float(r["net_pnl"]),
                                              ORDER.index(r["window_case"]),
                                              float(r["spacing_pct"]), float(r["size_multiplier"]),
                                              float(r["breakeven_tp_pct"]),
                                              float(r["invalidation_pct"])))[0]
        computed = {f: best[f] for f in ("window_case",) + CASE_FIELDS + DCA_FIELDS}
    elected_raw = None
    if c.get("winner") is not None:
        elected_raw = {"window_case": c.get("winner_case_label"),
                       **{f: c["winner"][f] for f in CASE_FIELDS + DCA_FIELDS}}

    def _norm(d):
        if d is None:
            return None
        out = {"window_case": d["window_case"]}
        for f in CASE_FIELDS:
            out[f] = int(d[f])
        for a in DCA_FIELDS:
            out[a] = float(d[a])
        return out

    computed_n, elected_n = _norm(computed), _norm(elected_raw)
    selector[c["cohort"]] = {
        "eligible_rows": len(eligible), "total_rows": len(mine),
        "computed": computed, "elected": elected_raw,
        "match": computed_n == elected_n}

# 5. DCA ladder identity
hist = res["dca_layer_histogram"]
FULL_WINDOW_GRIDS = ("full", "fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks",
                     "no_funding_full", "cost_attrition_40bps")
levels = [hist["level_%02d" % k] for k in range(12)]
sum_episodes_all = sum(a["episodes"] for a in aggregates.values())
sum_episodes_fullwin = sum(aggregates[g]["episodes"] for g in FULL_WINDOW_GRIDS)
sum_episodes_full = aggregates["full"]["episodes"]
sum_fills_all = sum(a["fills"] for a in aggregates.values())
sum_fills_fullwin = sum(aggregates[g]["fills"] for g in FULL_WINDOW_GRIDS)
ladder_candidates = {
    "level_00 == sum(episodes) over the seven full-window phase grids":
        hist["level_00"] == sum_episodes_fullwin,
    "level_00 == sum(episodes) over all ten phase grids": hist["level_00"] == sum_episodes_all,
    "level_00 == sum(episodes) over the full grid": hist["level_00"] == sum_episodes_full,
    "level_00 == sum(fills) over the seven full-window phase grids":
        hist["level_00"] == sum_fills_fullwin,
    "sum(levels) == sum(fills) over all ten phase grids": sum(levels) == sum_fills_all,
}
ladder_monotone = all(levels[k] >= levels[k + 1] for k in range(11))
ladder_reserve_unused = (hist["level_11"] == 0)
ladder_holding = [k for k, v in ladder_candidates.items() if v]

# 6. structural counters
counters = res.get("structural_counters") or {}
counter_violations = {}
for g, blob in counters.items():
    for k in CRITICAL_COUNTERS:
        v = int(blob.get(k, 0) or 0)
        if v:
            counter_violations["%s/%s" % (g, k)] = v

sel_ok = all(v["match"] for v in selector.values())
acct_ok = (acct_failures == 0 and equity_failures == 0)
winner_ok = winner_diff_max is not None and winner_diff_max == 0.0
coverage_ok = product_ok and all(res["coverage"][g] == coverage[g] for g in GRIDS)
ladder_ok = bool(ladder_holding) and ladder_monotone and ladder_reserve_unused
counters_ok = not counter_violations
ladder_identity = ("level_00 = %d = sum of episodes over the seven full-window phase grids"
                   % hist["level_00"] if ladder_candidates[
                       "level_00 == sum(episodes) over the seven full-window phase grids"]
                   else (ladder_holding[0] if ladder_holding else "NONE"))
out = {
    "schema_version": 1,
    "kind": "cmt_r2u3_independent_verification",
    "attempt_dir": ATT,
    "grids": list(GRIDS),
    "coverage": coverage,
    "registered_product_per_grid": expected_per_grid,
    "product_ok": product_ok,
    "grid_aggregates": aggregates,
    "accounting_identity_failures": acct_failures,
    "ending_equity_identity_failures": equity_failures,
    "worst_accounting_abs_diff": worst_acct,
    "worst_ending_equity_abs_diff": worst_equity,
    "winner_rows_re_read": winner_rows,
    "winner_max_abs_diff": winner_diff_max,
    "selector": selector,
    "selector_all_match": sel_ok,
    "ladder_candidates": ladder_candidates,
    "ladder_holding": ladder_holding,
    "ladder_identity": ladder_identity,
    "ladder_levels": hist,
    "ladder_monotone": ladder_monotone,
    "ladder_reserve_unused_level_11": ladder_reserve_unused,
    "ladder_episodes_fullwin": sum_episodes_fullwin,
    "ladder_episodes_all_grids": sum_episodes_all,
    "structural_counter_violations": counter_violations,
    "ok": bool(coverage_ok and acct_ok and winner_ok and sel_ok and ladder_ok and counters_ok),
    "checks": {"coverage_ok": coverage_ok, "accounting_ok": acct_ok, "winner_ok": winner_ok,
               "selector_ok": sel_ok, "ladder_ok": ladder_ok,
               "ladder_monotone": ladder_monotone, "ladder_reserve_unused": ladder_reserve_unused,
               "counters_ok": counters_ok},
}
with open(OUT, "w") as fh:
    json.dump(out, fh, indent=1, ensure_ascii=False)
    fh.write("\n")
print(json.dumps({k: out[k] for k in ("ok", "checks", "accounting_identity_failures",
                                      "ending_equity_identity_failures", "winner_max_abs_diff",
                                      "selector_all_match", "ladder_identity", "product_ok",
                                      "structural_counter_violations")},
                 indent=1, ensure_ascii=False))
print("selector:", json.dumps({k: {"match": v["match"], "eligible": v["eligible_rows"]}
                               for k, v in selector.items()}, ensure_ascii=False))
sys.exit(0 if out["ok"] else 1)
