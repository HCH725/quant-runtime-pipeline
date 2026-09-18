#!/usr/bin/env python3
"""Independent host-side verification of the Strategy L r1-u1 attempt (Conformal Kelly).

Reads ONLY the published attempt artifacts plus the canonical raw store, and re-derives:

  1. every grid CSV's row count and aggregate (net_pnl / fees / funding / gross_pnl / fills /
     turnover / episodes) - recomputed from the CSV text, never from result.json;
  2. the per-row accounting identities on every grid
     (episodes == tp_hits + stop_hits + time_exits + open_at_end + margin_calls and
      gross_pnl - fees - funding == net_pnl within 1e-3), counted independently;
  3. the registered ladder identity: level_00 of the full-window DCA histogram equals the sum of
     the episodes of every full-window grid row (and every level is non-negative);
  4. the elected winners: their cells re-read from grid_full.csv / grid_<stress>.csv and compared
     field-by-field with artifacts/cohort_results.json;
  5. the coverage-calibration reader: the registered conformal intervals are INDEPENDENTLY
     re-derived from the raw store through the engine kernel, and the marginal coverage plus the
     rolling 252-landed-bar extremes are recomputed and compared with artifacts/
     family_falsification.json;
  6. the survivor list: the per-cohort decision is recomputed from the grid CSVs with the
     registered selector logic.

Writes .kanban-scratch/l_parts/l_verification.json (host-side readback evidence).
"""
import csv
import importlib.util
import json
import os
import sys

import numpy as np

REPO = "/Users/hong/workspace/quant-runtime-pipeline"
ROOT = "/Volumes/ExpansionDrive/qlib-results"
FAMILY = "conformal-kelly-prediction-intervals-fractional-sizing-2026-09-02"
ROUND = FAMILY + "-r1"
RUN = ROUND + "-u1"
RDIR = os.path.join(ROOT, FAMILY, "rounds", ROUND)
ATT = os.path.join(RDIR, "attempts", RUN)
OUT = os.path.join(REPO, ".kanban-scratch", "l_parts", "l_verification.json")

sys.path.insert(0, os.path.join(REPO, "runtime"))
import _l_probe as probe  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "l_engine", os.path.join(REPO, "container", "scripts", "120_conformal_kelly_run.py"))
eng = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(eng)

GRIDS = ("historical", "oos", "full", "fee_2x", "funding_2x", "entry_delay_1_bar",
         "slippage_2ticks", "no_funding", "no_funding_full", "cost_attrition_40bps")
FULL_GRIDS = ("full", "fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks",
              "no_funding_full", "cost_attrition_40bps")
NUM = ("net_pnl", "fees", "funding", "gross_pnl", "ending_equity", "sharpe", "max_dd_usdt",
       "max_dd_pct", "max_effective_leverage", "capital_utilization", "turnover_usdt", "days")
INT = ("episodes", "tp_hits", "stop_hits", "time_exits", "open_at_end", "margin_calls", "fills",
       "windows_seen", "windows_entered", "bars_in_market")
CASE_FIELDS = eng.CASE_FIELDS
DCA_AXES = eng.DCA_AXES


def norm_row(r):
    out = dict(r)
    for f in CASE_FIELDS:
        out[f] = int(r[f])
    for a in DCA_AXES:
        out[a] = float(r[a])
    for k in NUM:
        out[k] = float(r[k]) if r[k] not in ("", "None") else 0.0
    for k in INT:
        out[k] = int(r[k])
    for k in ("cagr", "annualized_return"):
        out[k] = float(r[k]) if r[k] not in ("", "None") else None
    return out


def key_of(r):
    return (tuple(int(r[f]) for f in CASE_FIELDS),) + tuple(float(r[a]) for a in DCA_AXES)


res = json.load(open(os.path.join(ATT, "result.json")))
ff = json.load(open(os.path.join(ATT, "artifacts", "family_falsification.json")))
cr = json.load(open(os.path.join(ATT, "artifacts", "cohort_results.json")))
hist = json.load(open(os.path.join(ATT, "artifacts", "dca_layer_histogram.json")))
out = {"attempt_dir": ATT, "run_id": RUN,
       "result_json_sha256": eng.sha256_file(os.path.join(ATT, "result.json")),
       "grid_aggregates": {}, "identity_failures": {}, "ladder_identity": {},
       "winners": {}, "coverage_reader": {}, "selector": {}}

rows_by_grid = {}
for kind in GRIDS:
    path = os.path.join(ATT, "artifacts", "grid_%s.csv" % kind)
    with open(path, newline="") as fh:
        raw = list(csv.DictReader(fh))
    rows = [norm_row(r) for r in raw]
    rows_by_grid[kind] = rows
    agg = {k: round(sum(r[k] for r in rows), 6) for k in NUM}
    agg.update({k: sum(r[k] for r in rows) for k in INT})
    out["grid_aggregates"][kind] = {"rows": len(rows), "sha256": eng.sha256_file(path), **agg}
    bad_part = bad_ep = 0
    for r in rows:
        if abs(r["gross_pnl"] - r["fees"] - r["funding"] - r["net_pnl"]) > 1e-3:
            bad_part += 1
        if r["episodes"] != (r["tp_hits"] + r["stop_hits"] + r["time_exits"]
                             + r["open_at_end"] + r["margin_calls"]):
            bad_ep += 1
    out["identity_failures"][kind] = {"pnl_decomposition": bad_part, "episode_partition": bad_ep}

full_episodes = sum(r["episodes"] for k in FULL_GRIDS for r in rows_by_grid[k])
layer0 = int(hist["level_00"])
out["ladder_identity"] = {
    "layer_00": layer0, "sum_episodes_full_window_grids": full_episodes,
    "match": bool(layer0 == full_episodes and full_episodes > 0),
    "levels": {k: int(v) for k, v in sorted(hist.items())},
    "sum_all_levels": int(sum(int(v) for v in hist.values())),
    "all_levels_non_negative": bool(all(int(v) >= 0 for v in hist.values()))}

for c in cr:
    if c.get("winner") is None:
        continue
    sym, tf = c["cohort"].split("/")
    case = tuple(int(c["winner"][f]) for f in CASE_FIELDS)
    dca = {a: c["winner"][a] for a in DCA_AXES}
    entry = {"cohort": c["cohort"], "case_name": c.get("winner_case_label"), "grids": {}}
    for kind in ("historical", "oos", "full") + tuple(s for s, _ in eng.STRESS):
        want = (case,) + tuple(float(dca[a]) for a in DCA_AXES)
        hits = [r for r in rows_by_grid[kind]
                if r["symbol"] == sym and r["timeframe"] == tf and key_of(r) == want]
        entry["grids"][kind] = {"rows_found": len(hits)}
        if len(hits) != 1:
            continue
        r = hits[0]
        m = c["metrics"][kind] if kind in ("historical", "oos", "full") else c["metrics"][
            "robustness"][kind]
        diffs = {k: abs(float(r[k]) - float(m[k])) for k in m
                 if k in r and isinstance(m[k], (int, float)) and m[k] is not None}
        entry["grids"][kind].update({"max_abs_diff_vs_cohort_results":
                                     round(max(diffs.values()), 9) if diffs else None,
                                     "fields_compared": len(diffs)})
    out["winners"][c["cohort"]] = entry

times, closes = {}, {}
for s in probe.SYMBOLS:
    times[s], closes[s] = probe.load_closes(s)
core = eng._panel_arm_core("conformal", {s: closes[s] for s in probe.SYMBOLS})
rep = ff["coverage_calibration_stability"]["instruments"]
cov_out = {}
for label in core["labels"]:
    p = core["path"][label]
    mask = np.isfinite(p["q_eff"]) * np.isfinite(p["mu"])
    pos = np.flatnonzero(mask)
    vals = [1.0 if bool(p["covered"][i]) else 0.0 for i in pos]
    roll = [float(np.mean(vals[i:i + 252])) for i in range(0, len(vals) - 252 + 1)]
    cov_out[label] = {
        "recomputed_marginal": round(float(np.mean(vals)), 6),
        "recomputed_rolling_min": round(min(roll), 6),
        "recomputed_rolling_max": round(max(roll), 6),
        "recomputed_observations": len(vals),
        "atomic_marginal": rep[label]["marginal_coverage"],
        "atomic_rolling_min": rep[label]["rolling_min"],
        "atomic_rolling_max": rep[label]["rolling_max"],
        "atomic_observations": rep[label]["landed_observations"]}
    cov_out[label]["marginal_match"] = bool(
        abs(cov_out[label]["recomputed_marginal"] - rep[label]["marginal_coverage"]) < 1e-9)
    cov_out[label]["rolling_match"] = bool(
        abs(cov_out[label]["recomputed_rolling_min"] - rep[label]["rolling_min"]) < 1e-9
        and abs(cov_out[label]["recomputed_rolling_max"] - rep[label]["rolling_max"]) < 1e-9)
out["coverage_reader"] = cov_out
out["coverage_reader_all_match"] = bool(all(v["marginal_match"] and v["rolling_match"]
                                            for v in cov_out.values()))

spec_doc = json.load(open(os.path.join(ATT, "run-spec.json")))
sel = {}
for label in sorted({"%s/%s" % (r["symbol"], r["timeframe"]) for r in rows_by_grid["full"]}):
    hist_rows = [r for r in rows_by_grid["historical"]
                 if "%s/%s" % (r["symbol"], r["timeframe"]) == label]
    win, reason = eng.select_cohort_winner(hist_rows, spec_doc)
    got = next((c for c in cr if c["cohort"] == label), None)
    same = (win is None and got["winner"] is None) or (
        win is not None and got["winner"] is not None
        and key_of(win) == key_of({f: got["winner"][f] for f in CASE_FIELDS}
                                  | {a: got["winner"][a] for a in DCA_AXES}))
    sel[label] = {"recomputed_winner": None if win is None else got.get("winner_case_label"),
                  "recomputed_reason": reason,
                  "atomic_winner": got.get("winner_case_label") if got else None,
                  "atomic_outcome": got.get("outcome") if got else None,
                  "winner_match": bool(same)}
out["selector"] = sel
out["all_fields"] = {
    "coverage_complete": res["coverage_complete"],
    "case_evaluations_total": res["case_evaluations_total"],
    "assertions_all_true": bool(all(res["assertions"].values())),
    "cohort_survivors": res["cohort_survivors"],
    "disposition": res["disposition"],
    "verdict_recommendation_final": res["verdict_recommendation_final"],
    "reader_flags": res["registered_family_level_falsification_flags"]}
out["ok"] = bool(
    out["ladder_identity"]["match"]
    and all(v["pnl_decomposition"] == 0 and v["episode_partition"] == 0
            for v in out["identity_failures"].values())
    and all(g["rows_found"] == 1 for v in out["winners"].values() for g in v["grids"].values())
    and all(v["winner_match"] for v in sel.values())
    and out["coverage_reader_all_match"])

os.makedirs(os.path.dirname(OUT), exist_ok=True)
tmp = OUT + ".tmp"
with open(tmp, "w") as fh:
    fh.write(json.dumps(out, indent=2, sort_keys=True) + "\n")
os.rename(tmp, OUT)
print(json.dumps({k: out[k] for k in ("ok", "ladder_identity", "identity_failures",
                                      "coverage_reader_all_match", "all_fields")},
                 indent=2)[:2500])
print("wrote", OUT)
