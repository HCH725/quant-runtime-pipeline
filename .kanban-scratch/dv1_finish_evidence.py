"""Regenerate the D evidence record and append its row to evidence/README.md (idempotent)."""
import json
import os
import subprocess

REPO = "/Users/hong/workspace/quant-runtime-pipeline"

# 1. regenerate the record (picks up the new verdict.json sha + the rewrite note)
print(subprocess.run(["/opt/homebrew/bin/python3", os.path.join(REPO, ".kanban-scratch",
                                                                "dv1_make_evidence.py")],
                     capture_output=True, text=True).stdout.strip())

row = (
    "| `strategy-d-v1-r1-run-20260915.json` | Strategy D（UTC clock-hour return seasonality panel，"
    "BTCUSDT/ETHUSDT/BNBUSDT/SOLUSDT 1h USD-M perpetual，三腿 clock 視窗 + window-bounded DCA rail）"
    "round r1 的執行記錄快照：preregistration 指紋（round-spec `sha256:f33b3cb0…` 於 u3 原封重用、"
    "run-spec `sha256:6097c4a1…`、引擎 `sha256:78b590a9…`）→ 三次 attempt（u1 因 winner-diagnostics "
    "跨 cohort cell lookup 以 `INCOMPLETE` 終結，修於 `1ac2542`；u2 為 `ARTIFACT_READY` 但自身斷言揭發兩個"
    "**會計／偵測器缺陷**——階梯直方圖被 diagnostics 重跑重複計入 29,155 個 episode、`funding_bar_out_of_hold` "
    "在 boundary-alternative 軌因「恰落 bar 邊界（0 ms jitter）」被半開映射誤報 768 次，修於 `3732230`（僅動直方圖"
    "累加與守衛讀取的 containment 映射，排程與金額不動）→ u3 為權威量測：12 grid × 1,344 cells = **16,128 / 16,128** "
    "case evaluations、**28/28** assertions 全真、`funding_bar_out_of_hold` 0、階梯 level 00 = 22,572,146（＝八個 "
    "full-window grid 的 episodes 和）、**12/12 grid CSV 與 u2 byte-identical**（修正為量測中性的實證）"
    "→ terminal readback（`DONE` sentinel 25 個 artifact checksum、`check` rc=0 `problems=[]`、reconcile `consumed` "
    "idempotent）→ `verdict.json` **REJECT**（0 survivor：四個 cohort 全數被 G8 cost-attrition 40 bps 刈除，"
    "BTCUSDT 另於 G6 fee_2x 與 G7 neighbourhood 0.40 < 0.60 失敗）、`performance_claimable=false`、yield "
    "`STOP_REJECT`（round 1/3）；family-level 兩項註冊證偽（timezone-fragility／window-instability）皆未觸發；"
    "**無 survivor bundle**（0 survivor，§10.8 要求 ≥1）。主機路徑已 redact |\n"
)

readme = os.path.join(REPO, "evidence", "README.md")
text = open(readme).read()
if "strategy-d-v1-r1-run-20260915.json" in text:
    print("README row already present - not appended")
else:
    with open(readme, "a") as fh:
        fh.write(row)
    print("README row appended (%d chars)" % len(row))
print("record:", json.load(open(os.path.join(REPO, "evidence",
                                             "strategy-d-v1-r1-run-20260915.json")))["verdict"][
    "verdict_json_sha256"])
