# quant-runtime-pipeline

Private，versioned **runtime source-of-truth**：Apple Container → Qlib → Quant Runtime 的可重建定義、
audited implementation contract 與其變化歷史。

**這不是新的執行平台，也不是 backtest data store。** clone 本 repo 本身不產生任何副作用：
沒有任何排程、執行、回測、背景服務或狀態寫入；`container/scripts/` 只在有人手動執行時才動作。

---

## 1. 這個 repo 是 / 不是什麼

| 是 | 不是 |
|---|---|
| audited Runtime Contract 的唯一版本控管來源（含變更記錄） | 第二套 backtester / parameter search |
| Apple Container + Qlib runtime 的可重建定義（image、kernel pin、mount 契約、驗證腳本） | 資料倉：raw market data 與 run artifacts 都不在此 |
| 支撐 Contract `[V]` 事實的精簡證據快照 | runtime state store（沒有 PID / heartbeat / state churn） |
| 給 auditor 唯讀稽核的最小可公開材料 | scheduler / queue / service / framework |

## 2. Current status（2026-09-13）

- `QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md`：**v1.4.2，AWAITING AUDIT**（卡片 `t_670a86af`；前一版 **v1.4.1 = FAIL audit `t_3edafbb9`**，唯一 blocking finding F2：重跑比對多排除了 `generator.path`；再前一版 **v1.4.0 = FAIL audit `t_0bd01630`**，唯一 blocking finding F1：公開的 frozen bundle identity 無法依 v1.4.0 §10.8 措辭重算；再前一版 **v1.3.2 = AUDITED PASS / FROZEN**，auditor `t_3ffaeeb8`，2026-09-13，audited content commit `0363011`，remediation 卡片 `t_33457313` 依 auditor `t_23f4c3ef` 對 v1.3.1 的 FAIL）；
  上一個 AUDITED PASS / FROZEN 的版本是 **v1.2.0**（auditor `t_7b979fe8`，2026-09-13；audited content commit `068d6f7`），
  更前為 **v1.1.1**（auditor re-audit `t_83682069`，2026-09-13；audited content commit `18d6c3f`）與 **v1.0.1**（auditor re-audit `t_e35c39c0`）。
- **v1.4.2（2026-09-13，AWAITING AUDIT；卡片 `t_670a86af`，auditor `t_3edafbb9` 對 v1.4.1 的 F2 最小 remediation）**：**重跑比對逐鍵收緊**。
  v1.4.1 的 `--check` 把**整個** `generator` 物件排除在「量測內容」之外，因此 `generator.path` 被改成任何值、只要公開 identity 依 §10.8 重算成自洽值，`--check` 與 writer 都仍接受（自洽 tamper 的 false acceptance）。
  v1.4.2 改為**逐鍵**排除 §10.8 允許的兩個產生者身分欄位——頂層 `contract` 與巢狀 `generator.sha256`——`generator.path` 與其他所有巢狀／頂層欄位一律納入比對；
  非 dict 的 `generator` 不再被正規化掉而是照原樣納入比對（`CONTENT_EXCLUDED_TOP_LEVEL_KEYS`／`CONTENT_EXCLUDED_GENERATOR_KEYS`）。
  `runtime/tests/test_survivor_bundle.py` 17 → **18/18**（新增 F2 regression：改 `generator.path` 並只重簽公開 identity → `--check` rc=1／measurement mismatch、writer `refused_different_bytes`；非 dict `generator` 納入比對；對照控制「只改 `generator.sha256`＋`contract` 並重簽」仍 `check_clean`／no-op）。
  **既有 immutable artifacts 未改寫**：A v2 round r1 的 `survivor-bundle.json`（`sha256:4638885f…`）、`verdict.json`（`sha256:cb470adf…`）、`result.json`（`sha256:012e6d1a…`）、`cohort_survivors.json`（`sha256:74f250cf…`）驗證前後逐位元相同，bundle 公開 identity 仍為 `sha256:c051759f…`。
  **語意不變**：family gate（>=1 → `PASS`）、all-survivors mapping、identity recipe（仍是 §10.8 兩欄排除）、B v2 preregistration 與未 launch、engine、cron paused、§9.4 reconciler、§14.4 handoff、§16 preflight。
  詳見契約 §10.8（重跑比對範圍）、§22 A27、§23 item 13、§25 與附錄 C；證據快照 `evidence/v1.4.2-bundle-replay-scope-remediation-20260913.json`。
- **v1.4.1（2026-09-13，AUDITED FAIL：auditor `t_3edafbb9` 唯一 blocking finding F2「重跑比對多排除了 `generator.path`」；卡片 `t_58166acc`，auditor `t_0bd01630` 對 v1.4.0 的 F1 最小 remediation；F2 已由 **v1.4.2** 修正）**：**canonical identity recipe 明確定義**。
  frozen survivor bundle 的公開 `bundle_identity_sha256` 現在由 §10.8 逐字定義：移除 `generated_at_utc` **與 `bundle_identity_sha256` 自身**兩欄後的 canonical JSON
  （`sort_keys=True, separators=(",", ":"), ensure_ascii=False`，UTF-8）sha256，**不得**有其他隱含排除——identity 欄位不得進入自己的雜湊輸入（自我遞迴無解），
  因此 auditor 可用 4 行純 stdlib 從持久化檔案獨立重算並與公開值逐位元比對（v1.4.0 的措辭只移除 `generated_at_utc`，永遠算不出公開值）。
  `--check` 除比對量測內容外，另行驗證「檔案公開值＝該檔案自身的 recipe 重算值」，`--json` 回報 `identity_recomputed_from_persisted_file` 與 `identity_recipe_matches`；
  `runtime/tests/test_survivor_bundle.py` 13 → **17/17**（第三方重算、identity 自我排除、tamper 三態負向控制、修正後 writer 對既有 frozen 內容仍為 no-op）。
  **既有 immutable artifacts 未改寫**：A v2 round r1 的 `survivor-bundle.json`（`sha256:4638885f…`）、`verdict.json`（`sha256:cb470adf…`）、
  `result.json`（`sha256:012e6d1a…`）、`cohort_survivors.json`（`sha256:74f250cf…`）驗證前後逐位元相同，bundle 公開 identity 仍為 `sha256:c051759f…`。
  **語意不變**：family gate（>=1 → `PASS`）、all-survivors mapping、B v2 preregistration 與未 launch、engine、cron paused、§9.4 reconciler、§14.4 handoff、§16 preflight。
  詳見契約 §10.8（recipe 與 4 行重算範例）、§22 A27、§23 item 13、§25 與附錄 C；證據快照 `evidence/v1.4.1-bundle-identity-remediation-20260913.json`。
- **v1.4.0（2026-09-13，AUDITED FAIL：auditor `t_0bd01630` 唯一 blocking finding F1；卡片 `t_24cc6167`，operator 決策：>=1 cohort survivor 即通過基本研究 gate）**：**all survivors advance**。
  family gate 由「恰 1 個 survivor 才 PASS」改為「**>=1 個即 PASS**」：0 個 → `REJECT`、>=1 個 → `PASS`，而 `SURVIVOR_FOUND` / `MULTIPLE_SURVIVORS` 只是 **disposition band**（描述數量，不是 verdict）；`FINALIST` 不再由 cohort-survivor disposition 產生，**survivor 數 >1 也不得**使 `performance_claimable=false`（唯一依據是 §9.6）。
  新增 **§10.8 frozen survivor bundle**（`rounds/<round_id>/survivor-bundle.json`，`runtime/survivor_bundle.py` 為唯一產生者）：完整凍結該 round **全部** survivors、`ranking=null`、不得排序/淘汰/二選一、不得改寫，且只由 terminal `DONE` 且 `coverage_complete=true`／assertions 全 true 的 attempt 產生（13/13 測試，含 legacy 語意揭露與 fail-closed 負向控制）。
  engine `family_disposition` 改為 >=1 → `PASS` 並揭露 `contract_semantics_version`／`disposition_mapping_version`（`test_strategy_a_engine.py` 35 → **37/37**）；Strategy A v2 round r1 的既有 `verdict.json` **不回寫**（仍為 `sha256:cb470adf…`），另由既有 `cohort_survivors.json` 生成 bundle（**2 survivors：`BTCUSDT/1h`、`SOLUSDT/4h`，兩者在 v1.4.0 語意下皆通過基本 gate**）。**語意不變**：sequential A→B→C、§9.4 reconciler、§14.4 handoff、§16 preflight、per-fill 成本會計／獨立 gross PnL／DCA provenance，無新服務。詳見契約 §6.4/§7.2/§7.3/§9.6/§10.8/§17/§22 A26–A27/§25 與附錄 C。
- **v1.3.2（2026-09-13，AUDITED PASS / FROZEN；auditor `t_3ffaeeb8`，audited content commit `0363011`；audit `t_23f4c3ef` 對 v1.3.1 的 F3/F4 最小 remediation）**：兩項。
  **F3 獨立 gross PnL 會計**——`gross_pnl` 不再由 net 反向回推（`realized + fees_total + funding_paid` 已刪除），
  改由**獨立的 price-PnL accumulator**：`simulate()` 的 4 個 exit／flatten 路徑各自只累加該 episode 的
  `exit proceeds − cost basis`（不含 fee、不含 funding），`summarize()` 的 `pnl_decomposition` 改以
  `pnl_decomposition_ok()` 比對兩個獨立來源；engine 新增 `TestGrossPnlAccounting` 6 個檢定（`test_strategy_a_engine.py` 由 29 → **35/35**，
  含單次 TP 與 ladder stop 的獨立手算 gross/net、`fee_2x` 只動 net 不動 gross、gross 路徑 monkeypatch 與
  fee「只扣不入帳」兩個負向控制）。實測：同一組檢定在 v1.3.1 bytes 上 5/6 失敗；同一 fee-tamper 下 v1.3.1 的
  `gross − fees − funding − net` gap 恆為 0（斷言恆真），v1.3.2 為 10.06（斷言 FAIL）——證明檢查非恆真。
  **F4 audit-only staging**——契約 §23 新增 checklist item 11、§25 新增禁令；v1.3.2 engine bytes 以
  host `qlib-apple-container/staging/v1.3.2/**` ＋ container `/qlib/work/staging/v1.3.2/**`（自身 `SHA256SUMS`／`README`）
  供 auditor 在 `qlib-run` 內以 `/opt/venv/bin/python` 執行，sha256 與 repo commit bytes 逐位元一致。
  staging 只服務稽核：不新增 daemon/service、**不覆蓋 host `/scripts` 的 frozen A v1 部署副本**、不對 `/results` 產生任何 production artifact，
  亦未建立任何 Strategy A v2 family／card／result（counts 仍 103,680、handoff cron 仍 paused）。詳見契約 §7.2/§22 A25/§23/§25 與附錄 C。
- **v1.3.1（2026-09-13，FAIL audit `t_23f4c3ef`；audit `t_246c62d7` 的最小 remediation）**：三項。
  **F1 per-fill 成本會計**——`container/scripts/20_strategy_a_run.py` 的每個 entry／DCA add／exit fill 現在於 fill 時點
  把 taker fee 扣入 realised equity（單一 `charge_fee()` choke point），`net_pnl`/`ending_equity`/每日 equity marks/Sharpe/margin 判定
  全為 net-of-fee，`fee_2x` 不再是 no-op；engine 新增 free／costly／`fee_2x` 迴歸（`test_strategy_a_engine.py` 由 25 → **29/29**）。
  **F2 DCA provenance**——`base_quote=1000` 改標 `PROJECT_PRE_REGISTERED_CONSTANT`（不再冒充 user-fixed），
  被搜尋的 `size_multiplier` 軸改標 `PROJECT_PRE_REGISTERED_SEARCH_DOMAIN`（1.1 是 pre-registered search candidate，不是不變量），
  round-spec／run-spec 口徑一致，並由 `runtime/strategy_a_v2_counts.py` 的 provenance 檢查與 5 個負向控制強制（`test_strategy_a_v2_counts.py` 由 9 → **14/14**）。
  **M1 archive hygiene**——operator-stopped 的 Strategy B runner 與其 engine test 已逐位元移到 archive-only 路徑
  `evidence/strategy-b-operator-stopped/runtime/`，host `/scripts` 部署副本同步移除（`/results` 未動）。詳見契約 §7.2/§10.2/§13 與附錄 C。
- **v1.3.0（2026-09-13，FAIL audit `t_246c62d7`）**：**DCA parameter domain 全量納入 full-backtest**——full-backtest 改為
  `symbols × timeframes × strategy parameter domain × DCA parameter domain × historical/OOS/robustness`，且每個
  `(symbol, timeframe)` cohort 都必須跑完整的 `strategy × DCA` 乘積（單一固定 DCA rail 不再合格，§7.2）；
  **cohort-level survivor disposition**（新增 §7.3）——每個 cohort 以 deterministic、**historical-only** 的 selector 選出唯一
  winner，再要求 OOS / full / 四個 execution stress / 歷史參數鄰域（≥60%）全部成立；family 判定改為「0 個 survivor → REJECT、
  恰 1 個 → PASS、>1 個 → FINALIST、coverage 不完整 → TECHNICAL_INCOMPLETE」，**跨 cohort median 不得再作 family gate**
  （只能 `non_gating` diagnostic）。§14.4 handoff 新增 candidate body 必須帶 DCA domain 與 cohort survivor rules
  （`candidate_body_not_v13` fail-closed）；§13 新增 `operator_stopped`。**語意不變**：production 仍 sequential A→B→C、
  §9.4 reconciler、§11/§12/§15/§16、無新服務、auditor 不是每張 strategy card 的 stage。詳見契約 §7.2/§7.3/§14.4 與附錄 C。
- **Strategy A v2（2026-09-13 production run，terminal 已完成；round verdict = FINALIST）**：family `close-vs-sma-mean-reversion-long-flat-v2`
  （舊 `...-v1` 與其 REJECT artifacts 保持 immutable），round/run = `close-vs-sma-mean-reversion-long-flat-v2-r1` /
  `...-r1-u1`，production 卡 `t_1f97bf6b`，runner sha256 `c4f9a216…`（v1.3.2，已部署到 active `/scripts`）。
  preflight P1–P10 全綠後於 2026-09-13T00:45:53Z 以 `container exec --detach` exact-once 投遞；
  20 cohorts × 12 strategy × 48 DCA = 576 / cohort / grid × 9 phase grids = **103,680 case evaluations**。
  啟動前提已滿足：Contract v1.3.2 = AUDITED PASS / FROZEN（audited content commit `0363011`，attestation commit `84b8728`，
  auditor `t_3ffaeeb8`）。
  **terminal 結果（2026-09-13T01:07:36Z，runtime 1302 s）**：20/20 cohorts、`coverage_complete=true`、14/14 assertions true、
  9 個 phase grid 各 11,520 筆；**2 個 cohort survivor（`BTCUSDT/1h`、`SOLUSDT/4h`）→ disposition `MULTIPLE_SURVIVORS` →
  round `verdict.json` = FINALIST、`performance_claimable=false`**（v1.3.2 語意；該檔 **immutable、不回寫**）。
  **v1.4.0 補充**：依 §10.8，該 round 已由既有 `artifacts/cohort_survivors.json` 生成凍結 survivor bundle
  （`rounds/…-r1/survivor-bundle.json`，identity `sha256:c051759f…`，`ranking=null`、`all_survivors_advance=true`），
  bundle 內**明確標註**：在 v1.4.0 語意下 `BTCUSDT/1h` 與 `SOLUSDT/4h` **兩者都通過基本 gate**（≥1 survivor 即 PASS），
  不二選一、不排序；bundle 亦逐字揭露來源 attempt 是 pre-v1.4.0 對映（`FINALIST`／`claimable=false`），而來源 `verdict.json` 的 checksum 未變。terminal sentinel `DONE` 由 host 端 `runtime/terminal_evidence.py publish` 最後原子寫入，
  `... check` ok（17 個 manifest checksum 全數重算相符，problems 空）；`runtime/reconcile.py --dry-run` → 本 attempt
  `consumed`（卡片非 `scheduled`，no-op）、incidents 0。
  證據：`evidence/strategy-a-v2-{preflight,counts-instantiated,launch-record,terminal,round-verdict,verdict-crosscheck}-20260913.json`
  （`verdict-crosscheck` 直接由 `artifacts/grid_<phase>.csv` 重導兩個 survivor 的 G4/G5/G6 與
  `gross_pnl - fees - funding == net_pnl`，不經 summary artifacts）。
- **Strategy B（operator-stopped）**：卡 `t_3e696dce` 在任何 verdict 產生前被 operator 中止並保持 `blocked`；B 的
  `/results` artifacts 全部保留，未終結的 attempt 已於 host 端補發 `INCOMPLETE`（`failure.class=operator_stopped`）。
  **B 沒有 PASS/REJECT**。B 的 exact runner 與 engine test 已不再位於 active runtime 路徑：逐位元存檔於
  `evidence/strategy-b-operator-stopped/runtime/`（archive-only，不得執行），host `/scripts` 部署副本已移除。
  handoff cron `624d0be5b23c` 依 operator 決定**保持 paused**（2026-09-13 `hermes cron list --all` 讀回 `[paused]`）。
  **v1.4.0 補充（B v2 preregistration，未 launch）**：已備妥 `runtime/templates/strategy_b_v2_{round,run}_spec.template.json`
  （20 cohorts × 120 strategy × 48 DCA × 10 phase grids = **1,152,000** case evaluations、cohort survivor 語意、v1.4.0 對映、
  DCA domain 沿用 A 的已註冊域、walk-forward 由 16 格縮為 4 格且預先揭露）與其檢查 `runtime/tests/test_strategy_b_v2_templates.py`（8/8）；
  **B v2 engine／卡／run 皆不存在**（v1.4.0 的 B runner 是 launch 前置條件；已 operator-stopped 的 B v1 runner 為 archive-only 不得重用），
  本輪**未** launch B、**未**建立卡、**未**重啟 cron。
- **v1.2.0（2026-09-13，AUDITED PASS / FROZEN）**：新增 Contract **§14.4 automatic production handoff trigger**——正式 strategy card terminal 後，由 default 的單一 no-agent cron 自動 append 下一張 family（`runtime/production_handoff.py`，候選來自已 review 的 pool `/results/_handoff/candidates.json`）。第一次真實 handoff 已 append Strategy B（卡 `t_3e696dce`，`parents=[t_97208408]`），並由 dispatcher 自動 claim；該 family 隨後被 operator 依 v1.3.0 決策中止（見上）。詳見契約 §14.4 與附錄 C。
- **v1.1.1（2026-09-13）**：audit `t_d7f48c7a` 的最小 remediation——`reconcile.py` 先判 consumed（非 `scheduled` 即 no-op，不寫 incident／不留 comment）、mapping 補足 family/round/run/container identity、preflight P10 必須由 host 端實際重算 `script.sha256`（不可讀即 `FAIL`／NOT VERIFIED），**語意不變**（Nautilus 仍 out-of-scope、production 仍 sequential A→B→C）。變更記錄見契約附錄 C。
- Runtime `[V]`：Apple Container **1.4.1**（client/server commit `9a8917ca…`）＋ Qlib **0.9.7** native linux/arm64
  image `qlib:0.9.7-arm64` 已建置；mount 契約（Contract §3）與 ro/rw 語意已實測。
- **已落地的 runtime readiness（最小版）**：`runtime/preflight.py`（Contract §16 的 P1–P10 單一腳本）、
  `runtime/reconcile.py`（§9.4 no-agent reconciler，含 §12.6 fail-closed incident 寫入）、
  `runtime/terminal_evidence.py`（§10.3/§10.4 terminal sentinel 與 checksum 產生器）。實跑證據見
  `evidence/runtime-readiness-20260913.json`。這三支是**最小版**：沒有 Manager/Service/Factory/Registry/Orchestrator、
  沒有 daemon、沒有 queue、沒有第二套 runtime。
- **仍未完成（Contract `[T]`／`DEFERRED`，不得宣稱已實作）**：fingerprint 自動化、yield 判定自動化、chain head 查詢、
  failure drills D1–D13（**不是**第一張正式 strategy card 的 gate）、`/results` 正式 family 目錄（T4/T11，隨第一張卡產生）、
  `container exec` 投遞的腳本化包裝。這些不在本 repo，本 repo 也不得被引用為它們已存在。
- **Out-of-scope（不是待辦 blocker）**：下游 authoritative acceptance（Nautilus）階段。現行 production 的效能真值來源是
  Qlib full-backtest；Nautilus 不存在不影響任何 verdict、`performance_claimable` 或 family 放行（Contract §17）。

## 3. Architecture boundary

```
Hermes / Kanban (control plane)
        │  卡片進 → 執行 → sentinel 落地
        ▼
Apple Container / Qlib  (compute plane, 唯一計算面)
        │  單向：raw(ro) → 計算 → /results(rw)
        ▼
durable /results  (authoritative research evidence)
        │
        ▼
host reconciler (deterministic, no-agent)  →  Kanban unblock  →  Hermes
```

- container 對外的唯一 communication surface 是 `/results` 檔案系統；反方向只用 host 主動發起的 `container exec`。
- **Nautilus 的角色**：**future / out-of-scope / non-blocking optional integration**（Contract §17）。它不是已建立、
  必經的 authoritative gate，也不是 performance claim gate；現行 Qlib full-backtest **不會**因為它不存在而被降級為
  `research-only`。本 repo 不得引入第二套 backtester 或 Nautilus runtime tree，也不得把它列為第一張 production card 的 gate。
- 完成橋只能是「durable sentinel + host deterministic no-agent reconciler + kanban unblock」；
  不得引入 HTTP server / webhook / Redis / Celery / queue manager。

## 4. 內容

| 路徑 | 內容 |
|---|---|
| `QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md` | canonical contract（現行 **v1.4.2 / AWAITING AUDIT**，卡片 `t_670a86af`，依 auditor `t_3edafbb9` 對 v1.4.1 的 F2 最小 remediation：§10.8 重跑比對**逐鍵**只排除頂層 `contract` 與巢狀 `generator.sha256`，`generator.path` 仍納入比對；前版 **v1.4.1 = FAIL audit `t_3edafbb9`**（F2）；**v1.4.0 = FAIL audit `t_0bd01630`**（F1）；上一個 FROZEN 版本 **v1.3.2 / AUDITED PASS / FROZEN**，audited content commit `0363011`，auditor `t_3ffaeeb8`，依 `t_33457313`（audit `t_23f4c3ef` 對 v1.3.1 的 F3/F4 最小 remediation）；更前 **v1.3.1** = FAIL audit `t_23f4c3ef`；**v1.3.0** = FAIL audit `t_246c62d7`；更前 FROZEN 版本 **v1.2.0 / AUDITED PASS / FROZEN**，audited content commit `068d6f7`，auditor `t_7b979fe8`，含 §14.4 automatic handoff；再前為 **v1.1.1 / AUDITED PASS / FROZEN**，audited content commit `18d6c3f`，auditor re-audit `t_83682069`），全文三級標記 `[V]`/`[C]`/`[T]`，變更記錄見附錄 C |
| `container/Containerfile` | **唯一** image 定義：`python:3.12-slim` + Qlib `v0.9.7`（build 內 `rev-parse HEAD` 守衛，upstream 移動 tag 即 build 失敗） |
| `container/scripts/` | **現行** runtime 的 canonical rebuild / verify / engine 定義：`20_strategy_a_run.py`（v1.3.0/v1.3.1/v1.3.2 Strategy A 全量回測 engine）、`00_env_baseline.py`、`03_qlib_smoke.py`、`verify_final.sh`、`run_phase4.sh`、`fetch_kernel.sh`、`tests/test_strategy_a_engine.py`。v1.1.0 僅 `verify_final.sh` 兩處 Qlib 檢查改為 `/opt/venv/bin/python`，其餘與稽核當時逐位元相同（見 §6 Provenance）；一次性 phase/history 腳本已排除（分類與理由見 `evidence/README.md`）。**已 operator-stopped 的 Strategy B runner 與其 test 不在這裡**：它們逐位元存檔於 `evidence/strategy-b-operator-stopped/runtime/`（archive-only，不得執行），host `/scripts` 部署副本亦已移除（Contract §13 archive hygiene，卡 `t_6c83c9fb`） |
| `runtime/` | host 端最小 runtime readiness：`survivor_bundle.py`（**v1.4.2** §10.8 frozen survivor bundle 產生器：全部 survivors、`ranking=null`、不得改寫、只由 terminal `DONE` 且 coverage／assertions 全真之 attempt 產生、公開 `bundle_identity_sha256` 依 §10.8 canonical recipe（移除 `generated_at_utc` 與 identity 欄位自身）可由 auditor 以純 stdlib 獨立重算、重跑比對**逐鍵**只排除頂層 `contract` 與巢狀 `generator.sha256`（`generator.path` 仍納入比對）；純 stdlib、非服務）、`templates/strategy_b_v2_{round,run}_spec.template.json`（**v1.4.0** B v2 preregistration，未 launch）、`preflight.py`（P1–P10）、`reconcile.py`（no-agent 完成橋）、`terminal_evidence.py`（sentinel/checksum 產生器）、`production_handoff.py`（§14.4 automatic handoff：每輪檢查並最多 append 1 張 family 卡，v1.3.0 起另檢 candidate body 的 DCA domain／cohort survivor 標記）、`strategy_a_v2_counts.py`（v1.3.0 pre-registration 計數器／驗證器）、`templates/strategy_a_v2_{round,run}_spec.template.json`、`tests/test_reconcile.py`、`tests/test_preflight_p10.py`、`tests/test_production_handoff.py`、`tests/test_strategy_a_v2_counts.py`、`tests/test_survivor_bundle.py`（18 檢定）、`tests/test_strategy_b_v2_templates.py`（8 檢定）（皆 stdlib unittest）。純 stdlib、手動或 no_agent cron 觸發；不含任何常駐服務 |
| `evidence/` | 支撐 `[V]` 的精簡證據**快照**（不是 runtime state；主機專屬絕對路徑已以 `<PLACEHOLDER>` 取代） |

## 5. 重建 runbook（最小步驟）

1. 安裝 **Apple Container 1.4.1**（官方 signed pkg；標準安裝位置需一次 admin/sudo 權限）。
2. `container/scripts/fetch_kernel.sh` — 取得 kata kernel 並對 `container` 自身 pin 的 digest 驗證。
3. `container build -t qlib:0.9.7-arm64 container/` — image 由 pinned commit 重建。
4. `container volume create --opt size=30g qlib-work` — derived work area（可重建，**永遠不是真值來源**）。
5. `container/scripts/run_phase4.sh` — 以容器名 `qlib-run`（6 CPU / 4 GiB）建立 runtime 並套用 mount 契約。
6. `container/scripts/verify_final.sh` — 獨立讀回：版本、mount 語意、Qlib data layer、raw 未受影響。
7. `python3 runtime/preflight.py` — Contract §16 的 P1–P10 deterministic preflight（只讀）；正式 launch 前加上
   `--attempt-dir <attempt> --launch`（P10 的 `script.sha256` 以 host 上 `/scripts` 的來源目錄重算，必要時用
   `--host-scripts <dir>` 或 `QLIB_HOST_SCRIPTS` 指定）。此步驟是可選的獨立檢查，不會被 repo clone 或任何排程自動觸發。

**執行環境限制（Contract §9.4，2026-09-13 實測）**：`runtime/reconcile.py` 的唯一變更動作是 `hermes kanban unblock`，
而 Hermes 會拒絕來自 `HERMES_DELEGATED_CHILD_CONTEXT=1` context（delegate_task 子行程、kanban worker session，
以及**由該 session 建立／觸發的 cron job**）的 board 變更。所以 apply 必須在無此標記的 host context 執行
（operator 的一般 shell／由該 shell 建立的 cron 或服務）；`--dry-run` 不受限制，可用來確認「只剩放行這一步」。

mount 契約（Contract §3，`[V]`）：

| container | 模式 | 性質 |
|---|---|---|
| `/data/raw` | **ro** | canonical raw，唯一原始資料來源，container 永不寫入 |
| `/results` | **rw** | durable authoritative research evidence |
| `/qlib/work` | rw | derived / cache / work，**刪掉不得損失任何研究結論** |
| `/scripts` | **ro** | 進 container 的唯讀腳本；改腳本要在 host 端改 |

## 6. Path portability（重要）

Contract §3 的 `[V]` 表與 `container/scripts/` 會出現來源主機的主機專屬絕對路徑
（家目錄下的 workspace、外接 volume 的掛載點、容器 app data root）。這些都是**來源環境的參考
（reference）**，**不是** clone 後必須成立的 canonical path，也不是可移植性要求。

- 可移植性要求只有一個：Container **§3 的 mount 契約**（container 內路徑與 ro/rw 語意）＋ §16 preflight。
- 在主機端重跑腳本前，請先把這些路徑以環境變數 / 參數代入（例如先自行 `export` 對應變數，
  或直接編輯腳本開頭的路徑常數）；腳本不會、也不應該自動猜測你的佈局。
- 本 repo **不**為了美觀而改寫已稽核的契約文字與 runtime 腳本：保留原樣才能讓 auditor
  把 repo 內容與稽核當時的 artifact 逐位元對上。

**Provenance（已稽核腳本的唯一例外）**：`container/scripts/verify_final.sh` 的兩處 `container exec qlib-run python …`
已改為 `/opt/venv/bin/python`（Contract v1.1.0 附錄 C 的 B 修正）：容器內 `/usr/local/bin/python` **沒有** qlib，
且登入 shell 會把 PATH 還原成非 venv，裸 `python` 會讓 Qlib import 檢查誤判 FAIL。除附錄 C 記載之 v1.1.0／v1.1.1
變更外，未改動其他已稽核腳本或既有契約段落；契約段落之變更一律追加於對應段落尾端／新章節，並以附錄 C 為準
（v1.1.1 另含 audit `t_d7f48c7a` 的 F1–F3 最小 remediation）。同一個 venv 路徑修正也已套用到 host 上
`qlib-apple-container/scripts/verify_final.sh`（container 內 `/scripts` 的來源），避免部署副本與 repo 漂移。

**v1.3.0 的 runner 演進與部署狀態（重要）**：`container/scripts/20_strategy_a_run.py` 已由 v1.2.0 的「單一 DCA rail」
版本演進為 v1.3.0 的「DCA parameter domain + cohort survivor」版本（見契約附錄 C v1.3.0）。舊 v1 版本完整保存在 git 歷史
（commit `4859051`），而 **host 部署目錄 `qlib-apple-container/scripts/20_strategy_a_run.py` 刻意保持 v1 不變**
（sha256 `8f3ce89deebc14902f940d677cf5e3fef209e3f3a78d5dc10d0974964961df16`）：那正是 Strategy A v1 attempt run-spec 所 pin
的腳本，保持不動才能讓已稽核的 A v1 artifacts 持續可被 P10 逐位元重算驗證。因此 repo 與 host 在這一支檔案上**暫時不同步**，
v1.3.x 的 runner 只在 Strategy A v2 啟動時才部署到 host（屆時 `runtime/preflight.py --launch` 的 P10 會以新版 run-spec 的
`script.sha256` 重新計算並要求逐位元相符，不符即 launch gate 直接 FAIL）。

**v1.3.1 的 runner 修正（重要）**：`container/scripts/20_strategy_a_run.py` 的費用會計已依 audit `t_246c62d7` F1 修正——
每個 entry／DCA add／exit fill 的 taker fee 於 fill 時點扣入 realised equity（`charge_fee()`），所有 net 指標與
equity path 皆為 net-of-fee，`fee_2x` 不再是 no-op。此修正**尚未部署到 host**：host 部署目錄
`qlib-apple-container/scripts/20_strategy_a_run.py` 仍刻意為 Strategy A v1 保持 v1 版本不變（見上段），
v1.3.x 的 runner 只在 Strategy A v2 啟動時才部署，屆時 P10 會逐位元重算新 run-spec 的 `script.sha256`。

**v1.3.2 的 runner 修正與 audit-only staging（重要）**：`container/scripts/20_strategy_a_run.py` 的 gross 會計已依 audit
`t_23f4c3ef` F3 修正——`gross_pnl` 由獨立的 price-PnL accumulator（每個 exit／flatten 只累加 `exit proceeds − cost basis`）
供給，不再由 net 反向回推，`pnl_decomposition` 因此成為兩個獨立來源的交叉比對（負向控制見 `test_strategy_a_engine.py`
的 `TestGrossPnlAccounting`）。此修正**同樣尚未部署到 host**：host `qlib-apple-container/scripts/20_strategy_a_run.py`
仍為 Strategy A v1（sha256 `8f3ce89d…`）。為了讓 auditor 不必覆蓋該 frozen 部署副本就能實際執行 v1.3.2 engine，
新增**只服務稽核**的 staging 副本：host `qlib-apple-container/staging/v1.3.2/**`（`20_strategy_a_run.py`、`tests/`、
`SHA256SUMS`、`README.md`）與 container `/qlib/work/staging/v1.3.2/**`，其 bytes 與 repo commit 逐位元一致；
auditor 在 `qlib-run` 內以 `SA_ENGINE_PATH=<staging runner> /opt/venv/bin/python <staging test>` 執行即可（35/35 OK）。
staging 不新增 daemon/service、不改動 active `/scripts` mount、不寫 `/results`、不產生任何 A v2 結果。

**2026-09-13 v1.4.0 的 repo 與部署狀態（重要）**：repo `container/scripts/20_strategy_a_run.py` 已演進為 **v1.4.0**（>=1 survivor → `PASS` 的 disposition 對映），
但 **active host/container `/scripts` 刻意維持 Strategy A v2 所 pin 的 v1.3.2**（sha256 `c4f9a216…`）：那是 A v2 run-spec 所 pin、也是產出已凍結 A v2 artifacts 的 bytes，
不動它才能讓已稽核的 A v2 artifacts 持續被 P10 逐位元重算驗證。v1.4.0 的 engine bytes 以 **audit-only staging** 提供：
host `qlib-apple-container/staging/v1.4.0/**`（`20_strategy_a_run.py` sha256 `1c42d254…`、`tests/test_strategy_a_engine.py` sha256 `61389308…`、`SHA256SUMS`、`README.md`）
＋ container `/qlib/work/staging/v1.4.0/**`，auditor 在 `qlib-run` 內以 `SA_ENGINE_PATH=<staging runner> /opt/venv/bin/python <staging test>` 執行（37/37 OK）。
staging 不新增 daemon/service、不覆蓋 active `/scripts`、不寫 `/results`。

**2026-09-13 A v2 launch 的部署（更新上述三段狀態）**：attestation commit `84b8728` 之後，v1.3.2 runner 已部署到 active path：
host `qlib-apple-container/scripts/20_strategy_a_run.py`（sha256 `c4f9a216…`、51,775 bytes）與
`scripts/tests/test_strategy_a_engine.py`（sha256 `d5ddb247…`），container `/scripts`（ro mount）讀回逐位元相同，
preflight P10 由 host 端**實際重算**相符。此後 active `/scripts` 的 `20_strategy_a_run.py` 為 v1.3.2，不再等於 frozen A v1
（sha256 `8f3ce89d…`）；A v1 artifacts 的 `script.sha256` 仍可由 git 歷史（commit `4859051` 的 bytes）重算。

**host `/scripts` 的 B 清理（v1.3.1 M1）**：`qlib-apple-container/scripts/30_strategy_b_run.py` 與
`qlib-apple-container/scripts/tests/test_strategy_b_engine.py` 已從 host 部署目錄移除（container 內 `/scripts` 同步消失）；
exact bytes 存檔於 `evidence/strategy-b-operator-stopped/runtime/`，checksum 與理由見該目錄 `README.md` 與
`evidence/strategy-b-operator-stop-record-20260913.json`。`/results` 的 B artifacts 未受影響。

## 7. Security / data exclusions（永不進入本 repo）

`.env`、任何 token / credential / auth header、Kanban DB（含 `-wal` / `-shm`）、
market-data raw 內容、`/results` 實際 run artifacts、`/qlib/work`、parquet、model / checkpoint、
大型 log（`build.log`、`phase*.log`、`housekeeping*.log`）、PID / heartbeat / state churn、
主機權限診斷輸出、raw 檔案清單、舊 LEAN / Nautilus runtime tree。

排除規則見 `.gitignore`；`evidence/` 只放人可讀、已 scrub 的精簡快照。

## 8. 刻意不做（避免過度工程）

- 不加 GitHub Actions / CI / Dependabot / CodeQL / Pages / release automation / submodule / LFS。
- 不新增 framework、service、daemon、registry、queue 或第二套 runtime。
- 不新增任何**在 repo 內**可自動觸發的排程（無 CI、無 hook）；本 repo 只提供 deterministic 腳本與版本控管。production 的 automatic handoff 觸發是 **Hermes cron**（Contract §14.4，default profile job `624d0be5b23c`，no-agent script-only），不是 repo 內的排程，也不因此新增服務或狀態儲存。
- 本 repo 與 `HCH725/nautilus-quant-system` 無關，不觸及、不取代它。
