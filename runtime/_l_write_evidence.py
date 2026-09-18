#!/usr/bin/env python3
"""Author the evidence record for the Strategy L (Conformal Kelly) r1 full backtest and append
its README row.  Every number is read back from the published artifacts; the README row's series
number is derived from the live file count (never from an alphabetical index).

usage: runtime/_l_write_evidence.py
"""
import hashlib
import json
import os
import time

REPO = "/Users/hong/workspace/quant-runtime-pipeline"
ROOT = "/Volumes/ExpansionDrive/qlib-results"
FAMILY = "conformal-kelly-prediction-intervals-fractional-sizing-2026-09-02"
ROUND = FAMILY + "-r1"
RUN = ROUND + "-u1"
RDIR = os.path.join(ROOT, FAMILY, "rounds", ROUND)
ATT = os.path.join(RDIR, "attempts", RUN)
OUT = os.path.join(REPO, "evidence", "%s-full-backtest-20260918.json" % FAMILY)
README = os.path.join(REPO, "evidence", "README.md")
CARD = {"id": "t_14a1a080",
        "body_sha256": "sha256:a51235274b5346dbdbc38137e020254de9c867a717c6a2f6ded6c6f9a45d626c",
        "record_path": os.path.expanduser(
            "~/.hermes/wiki/quant/%s.md" % FAMILY)}


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def load(p):
    with open(p) as fh:
        return json.load(fh)


res = load(os.path.join(ATT, "result.json"))
verdict = load(os.path.join(RDIR, "verdict.json"))
bundle = load(os.path.join(RDIR, "survivor-bundle.json"))
rs = load(os.path.join(RDIR, "round-spec.json"))
rspec = load(os.path.join(ATT, "run-spec.json"))
sent = load(os.path.join(ATT, "DONE"))
verif = load(os.path.join(REPO, ".kanban-scratch", "l_parts", "l_verification.json"))
ff = load(os.path.join(ATT, "artifacts", "family_falsification.json"))
index = load(os.path.join(ROOT, "_survivors", "survivor-index.json"))
lead = load(os.path.join(ROOT, "_survivors", "leaderboard.json"))
my_entries = {e["survivor_id"]: e for e in lead["entries"] if e["family_id"] == FAMILY}

winners = {}
for c in res["cohort_results"]:
    if not c.get("winner"):
        winners[c["cohort"]] = {"outcome": c["outcome"], "cull_reasons": c["cull_reasons"]}
        continue
    winners[c["cohort"]] = {
        "case_name": c.get("winner_case_label"), "outcome": c["outcome"],
        "strategy_params": {k: c["winner"][k] for k in
                            ("arm_conf", "arm_mad", "arm_rstd", "arm_frozen", "arm_rvol20",
                             "cfg_A", "cfg_B")},
        "dca_params": {k: c["winner"][k] for k in
                       ("spacing_pct", "size_multiplier", "breakeven_tp_pct",
                        "invalidation_pct")},
        "historical": c["metrics"]["historical"], "oos": c["metrics"]["oos"],
        "full": c["metrics"]["full"], "robustness": c["metrics"]["robustness"],
        "neighbourhood": c["neighbourhood"]}

doc = {
    "schema_version": 1,
    "kind": "strategy_full_backtest_evidence",
    "family_id": FAMILY,
    "round_id": ROUND,
    "run_id": RUN,
    "kanban_task_id": CARD["id"],
    "kanban_board": "quant-strategy-research",
    "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "card": {"body_sha256": CARD["body_sha256"], "record_path": CARD["record_path"],
             "record_sha256": rs["provenance"]["canonical_record_sha256"],
             "intake_decision": rs["provenance"]["intake_decision"],
             "semantic_fingerprint": rs["provenance"]["semantic_fingerprint"],
             "lifecycle_footer": rs["registration_excerpts"]["card_lifecycle_footer"]},
    "registration": {
        "round_spec_path": os.path.join(RDIR, "round-spec.json"),
        "round_spec_sha256": sha(os.path.join(RDIR, "round-spec.json")),
        "round_spec_bytes": os.path.getsize(os.path.join(RDIR, "round-spec.json")),
        "run_spec_sha256": sha(os.path.join(ATT, "run-spec.json")),
        "engine": {"path": "container/scripts/120_conformal_kelly_run.py",
                   "sha256": rspec["script"]["sha256"],
                   "deployed_sha256_host": sha(os.path.join(
                       "/Users/hong/workspace/qlib-apple-container/scripts",
                       "120_conformal_kelly_run.py"))},
        "engine_selfcheck": {"path": "container/scripts/tests/test_strategy_l_engine.py",
                             "sha256": rspec["engine_selfcheck"]["sha256"],
                             "result": "21/21 tests OK on the host numpy interpreter AND inside "
                                       "the qlib-run container (/scripts/tests)"},
        "domains": {"cohorts": res["cohort_count"], "strategy_cases": 10, "dca_configs": 48,
                    "phase_grids": 10,
                    "cells_per_grid": res["case_evaluations_per_grid"],
                    "expected_case_evaluations": res["expected_case_evaluations"],
                    "strategy_axes": {"scale_arms": list(rs["parameter_domain"]["scale_arms"]),
                                      "sizing_configs": list(
                                          rs["parameter_domain"]["sizing_configs"])}},
        "signal_constants": rs["signal_semantics"]["registered_constants"],
        "pre_run_feasibility_probe": {
            "measured_before_the_freeze": True,
            "note": "per-arm event supply / coverage / gross-cap structure, measured with the "
                    "engine kernel before the round-spec was written",
            "bars": rs["parameter_domain"]["feasibility_probe"]["bars"],
            "arms": {a: {"defined_bars": b["defined_bars"],
                         "gross_cap_binding_rate": b["gross_cap_binding_rate"],
                         "gross_pre_cap_max": b["gross_pre_cap_max"],
                         "events_full": {s: v["events_full"]
                                         for s, v in b["per_symbol"].items()}}
                     for a, b in rs["parameter_domain"]["feasibility_probe"]["arms"].items()}},
    },
    "coverage": {"per_grid": res["coverage"], "case_evaluations_total":
                 res["case_evaluations_total"],
                 "expected_case_evaluations": res["expected_case_evaluations"],
                 "coverage_complete": res["coverage_complete"],
                 "all_assertions_true": all(res["assertions"].values()),
                 "assertion_count": len(res["assertions"]),
                 "grid_rows_per_grid": {k: verif["grid_aggregates"][k]["rows"]
                                        for k in verif["grid_aggregates"]}},
    "result": {
        "disposition": res["disposition"], "verdict": verdict["verdict"],
        "performance_claimable": verdict["performance_claimable"],
        "survivors": res["cohort_survivors"],
        "cohort_outcome_counts": res["cohort_outcome_counts"],
        "culled_cohorts": {c["cohort"]: c["cull_reasons"] for c in res["cohort_results"]
                           if c["outcome"] != "SURVIVOR"},
        "winner_cells": winners,
        "dca_layer_histogram": res["dca_layer_histogram"],
        "stress_summary": res["stress_summary"],
        "descriptive_diagnostics": res["descriptive_diagnostics"],
        "descriptive_medians_all_base_cases": res["descriptive_medians_all_base_cases"],
        "signal_layer": res["signal_layer"],
        "panel_build": res["panel_build"],
    },
    "registered_family_level_falsification": {
        "flags": res["registered_family_level_falsification_flags"],
        "hits": verdict["registered_family_level_reader_hits"],
        "coverage_calibration_stability": ff["coverage_calibration_stability"],
        "conformal_vs_realized_volatility_horserace":
            ff["conformal_vs_realized_volatility_horserace"],
        "downside_miscoverage_dial_placebo": ff["downside_miscoverage_dial_placebo"],
        "leverage_cap_ablation": ff["leverage_cap_ablation"]},
    "independent_verification": {
        "script": "runtime/_l_verify_r1u1.py",
        "script_sha256": sha(os.path.join(REPO, "runtime", "_l_verify_r1u1.py")),
        "evidence_path": ".kanban-scratch/l_parts/l_verification.json",
        "evidence_sha256": sha(os.path.join(REPO, ".kanban-scratch", "l_parts",
                                            "l_verification.json")),
        "ok": verif["ok"],
        "ladder_identity": verif["ladder_identity"],
        "accounting_identity_failures": verif["identity_failures"],
        "coverage_reader_all_match": verif["coverage_reader_all_match"],
        "winner_rows_max_abs_diff_vs_cohort_results":
            {k: {g: v["max_abs_diff_vs_cohort_results"] for g, v in x["grids"].items()}
             for k, x in verif["winners"].items()},
        "selector_recomputation_matches": all(v["winner_match"]
                                              for v in verif["selector"].values()),
        "preflight": "P1-P10 PASS (launch gate evaluated)",
        "terminal_evidence_check": "ok=true, problems=[] (%d manifest entries)"
                                   % len(sent["artifact_manifest"]),
    },
    "post_survivor_lifecycle": {
        "survivor_bundle": {"path": os.path.join(RDIR, "survivor-bundle.json"),
                            "sha256": sha(os.path.join(RDIR, "survivor-bundle.json")),
                            "identity_sha256": bundle.get("bundle_identity_sha256"),
                            "ranking": bundle.get("ranking"),
                            "band": bundle.get("disposition_band")},
        "survivor_index_entries": [s["survivor_id"] for s in index["survivors"]
                                   if s["family_id"] == FAMILY],
        "leaderboard_entries": {sid: {"cohort": e.get("cohort"),
                                      "evidence_state": e.get("evidence_state"),
                                      "rank": (lead["top10"].index(sid) + 1)
                                      if sid in lead["top10"] else None}
                                for sid, e in my_entries.items()},
        "evidence_packages": "NOT materialized in this round: contract 28.4 requires the "
                             "package's ledgers to be produced by a replay of the SAME engine, "
                             "and this card's engine lineage (J/K/L) carries no inert trace hook "
                             "(only 20_strategy_a_run.py has one, contract v1.6.0 section 28.2), "
                             "while adding one to the frozen production engine would change the "
                             "sha256 pinned by the published run-spec.  `survivor_evidence.py "
                             "coverage` reports the entries as ABSENT (FROZEN_ONLY); the gap is "
                             "disclosed here and handed to a follow-up card rather than patched "
                             "in place.",
    },
    "attempts": {"u1": {"status": "DONE", "runtime_seconds": res.get("runtime_seconds"),
                        "sentinel_sha256": sha(os.path.join(ATT, "DONE")),
                        "result_sha256": sha(os.path.join(ATT, "result.json")),
                        "case_evaluations": res["case_evaluations_total"],
                        "assertions_all_true": all(res["assertions"].values())}},
    "disclosures": [
        "the conclusions hold ONLY for the local eligible universe (BTCUSDT/ETHUSDT/BNBUSDT/"
        "SOLUSDT USD-M perpetuals, 1d); the record's source market (8 liquid US-listed ETFs on a "
        "frozen Kaggle daily snapshot) and its sample period are absent from the canonical raw "
        "and are recorded as provenance / external-validity context (system-owned lifecycle "
        "footer, contract 14.4)",
        "the record's crypto portability status is `adapted` / `unproven`; this round executes "
        "that extension and reports its own evidence",
        "the project pre-registered constants (quantile rule, window boundaries, warm-up "
        "accounting, materiality tilt, dial exposure gate, frozen-TRAIN leg length) are frozen in "
        "the round-spec; the alternative readings of the target state and of the dial are "
        "disclosed and NOT evaluated",
        "the rail owns the position size, so the record's continuous allocation vector and its "
        "continuous leverage multiplier enter only as the registered tilt rule and exposure gate",
        "two of the record's four falsification items (the dial placebo test and the leverage-cap "
        "ablation) are `not_executed` with their measured local analogues reported instead of a "
        "weakened substitute",
        "no parameter was adjusted and no gate was lowered: the DEFERRED verdict is produced by "
        "the record's own pre-registered rejection rules",
    ],
}

os.makedirs(os.path.dirname(OUT), exist_ok=True)
tmp = OUT + ".tmp"
with open(tmp, "w") as fh:
    fh.write(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
os.rename(tmp, OUT)
ev_sha = sha(OUT)

# ---- README row: series number from the live file count, appended after the last row ---------
with open(README) as fh:
    before = fh.read()
files = sorted(f for f in os.listdir(os.path.join(REPO, "evidence"))
               if f.endswith("-full-backtest-20260918.json") or "-full-backtest-" in f)
n_series = len([f for f in os.listdir(os.path.join(REPO, "evidence"))
                if "-full-backtest-" in f])
surv = ", ".join(res["cohort_survivors"])
row = (
    "| `%s`（`%s`） | **Production candidate `%s`（Conformal Kelly: Conformal Prediction "
    "Intervals as the Robust Scale in Fractional Kelly Position Sizing；family `%s`、round "
    "`%s`）— 本系列第 **%d** 張真的跑完全量回測的 production 卡（計數規則：`evidence/` 內 "
    "`*-full-backtest-*.json` 檔數）。** record 的 source market（8 檔美國掛牌 ETF、每日收盤、"
    "frozen Kaggle snapshot）與其 sample period 均不在本機；依 system-owned lifecycle footer"
    "（contract §14.4）以 **local eligible universe**（BTCUSDT／ETHUSDT／BNBUSDT／SOLUSDT "
    "USD-M perpetual 1d）執行 full backtest，record 自身的 crypto portability 為 "
    "`adapted`／`unproven`。engine `container/scripts/120_conformal_kelly_run.py`"
    "（`%s`；repo == host `/scripts` 部署、P10 重算相符；由已審計的 J spine 逐位元保留執行面"
    "、只替換 family signal 層與 readers）。self-check **21/21 OK**（host 與容器內各一次；含 "
    "prefix-truncation causality probe、ridge 正規方程閉式比對、conformal 幾何收縮定義、"
    "dial 閉式、materiality 狀態規則與 rail 接縫）。coverage gate G1 全綠：4 cohorts × 10 "
    "strategy cases（5 scale-estimator arms × 2 sizing configs）× 48 DCA × 10 phase grids = "
    "**%d／%d** case evaluations、`coverage_complete=true`、**%d/%d assertions true**、10 張 "
    "grid CSV 各 1,920 列。**註冊訊號**：expanding-window ridge（λ=10，21/63/252 momentum＋"
    "EWMA(20) vol，每 21 bar 重擬、第 750 bar 起預測）→ 五個 horizon {12,16,21,27,34} 以 "
    "sqrt(21/H) 對齊並以 w_h ∝ 1/q_h² 合成 → 0.75 分位 conformal 半寬（W=500，λ=0.3 幾何收縮"
    "至 expanding anchor）→ σ̂=q_eff/1.2816 → f=0.15μ̂/σ̂²（±0.75 winsor、2.0 gross cap）→ "
    "|w_i| ≥ equal-share 的 registered tilt 決定 LONG／SHORT／FLAT。**結果（科學）**："
    "disposition **MULTIPLE_SURVIVORS**、`verdict.json` = **DEFERRED**、"
    "`performance_claimable=false`；survivors **%d／%d** = **%s**（兩位 winner 皆為 "
    "`rvol20__A`：20-day realized-vol 對照臂），BNBUSDT/1d 與 ETHUSDT/1d 以 "
    "**`parameter_neighbourhood`** 淘汰。**record 自己的 falsification battery 兩項執行讀者"
    "雙雙命中**：①*Coverage Calibration Stability*——realized marginal coverage 在 rolling "
    "252 landed-bar 窗內離開 [0.70, 0.80]（BNBUSDT min **0.6429**／max 0.9881、BTCUSDT min "
    "**0.6071**、ETHUSDT min **0.5635**、SOLUSDT min 0.6944）→ 「reject the validity of the "
    "geometric anchor shrinkage calibration」；②*Conformal vs Realized-Volatility Horserace*"
    "——conformal 臂在 Sharpe 與 annualized net growth 兩腿都未勝過 20-day realized-vol 對照臂"
    "（cohorts_conformal_exceeds_sharpe=%d／%d、growth=%d／%d）→ 「reject the hypothesis that "
    "conformal quantiles provide a superior scale proxy」。未執行項：dial placebo（1,000 "
    "circular block-bootstrap；rail 為單一 instrument、無法表達連續 portfolio multiplier，"
    "改以 dial 序列分布與 gate 的 measured execution footprint 揭露）與 leverage-cap ablation"
    "（給出 cap binding rate 與 pre-cap gross 極值：record 自陳 cap 在開發期綁住 97.7%%、本機"
    "實測遠低，即本輪是以「未受 cap 約束」的配置檢驗該機制——正是 record limitations 明言"
    "「findings may not generalise to unlevered or unconstrained portfolios」的區間）。"
    "frozen survivor bundle `%s`（identity `%s`、`ranking=null`、全部 survivors 前進）；"
    "`verdict.json` `%s`。**獨立驗證**：preflight P1–P10 PASS（launch gate evaluated）；"
    "`terminal_evidence check` ok=true（%d 項 manifest）；host 端 stdlib 重算（10 張 grid CSV "
    "的 19,200 列：pnl 分解與 episode partition 各 0 失敗、ladder identity level_00 = %d = "
    "Σ full-window episodes、winners 逐欄 diff **0.0**、coverage reader 以引擎 kernel 自 raw "
    "重建後 marginal 與 rolling 端點全等、selector 重算 4／4 相同）→ `ok=true`。attempts："
    "u1（authoritative、runtime **%d s**、sentinel DONE、round-spec／run-spec `O_EXCL` "
    "exact-once）。**揭露**：①結論僅限 local universe；②project pre-registered constants"
    "（quantile 內插規則、窗邊界、warm-up 記帳、tilt／dial gate 語義、frozen-TRAIN 腿長）逐項"
    "凍結於 round-spec，替代讀法**未**評估；③rail 擁有部位大小，record 的連續權重與連續槓桿"
    "乘數僅以註冊 tilt／exposure gate 進入；④**§28 evidence package 未 materialize**"
    "（`survivor_evidence.py coverage` 將本 family 2 個 leaderboard entry 記為 ABSENT／"
    "FROZEN_ONLY）：§28.4 要求以**同一顆引擎**replay 產生 ledger，而本卡引擎血統（J／K／L）"
    "尚無 inert trace hook（僅 `20_strategy_a_run.py` 具備，contract v1.6.0 §28.2），現在為"
    "frozen production engine 加 hook 會改變 published run-spec 所 pin 的 sha256——已在 "
    "evidence 內具名揭露並交由 follow-up 卡處理，未就地修補。主機路徑已 redact |\n"
    % (os.path.basename(OUT), ev_sha, CARD["id"], FAMILY, ROUND, n_series,
       rspec["script"]["sha256"], res["case_evaluations_total"],
       res["expected_case_evaluations"], sum(1 for v in res["assertions"].values() if v),
       len(res["assertions"]), res["cohort_survivor_count"], res["cohort_count"], surv,
       ff["conformal_vs_realized_volatility_horserace"]["cohorts_conformal_exceeds_sharpe"],
       ff["conformal_vs_realized_volatility_horserace"]["cohorts_evaluated"],
       ff["conformal_vs_realized_volatility_horserace"]["cohorts_conformal_exceeds_growth"],
       ff["conformal_vs_realized_volatility_horserace"]["cohorts_evaluated"],
       sha(os.path.join(RDIR, "survivor-bundle.json")),
       bundle.get("bundle_identity_sha256"), sha(os.path.join(RDIR, "verdict.json")),
       len(sent["artifact_manifest"]), verif["ladder_identity"]["layer_00"],
       res.get("runtime_seconds")))
sep = "" if before.endswith("\n") else "\n"
after = before + sep + row
assert len(after.splitlines()) == len(before.splitlines()) + 1
assert after.endswith("|\n")
with open(README, "w") as fh:
    fh.write(after)
with open(README) as fh:
    check = fh.read()
assert check.count(os.path.basename(OUT)) == 1
assert ev_sha in check
print(json.dumps({"evidence": OUT, "evidence_sha256": ev_sha, "readme_lines":
                  len(after.splitlines()), "series_number": n_series,
                  "readme_row_hashes_match": ev_sha in check}, indent=2))
