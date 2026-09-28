# QUANT RUNTIME PIPELINE — IMPLEMENTATION CONTRACT (SOP)

文件狀態：**v2.0.0 DIRECT-RUNTIME OVERRIDE — AUDITED PASS / LIVE**（2026-09-24 cutover；2026-09-25 operational read-back）。
依 §26，completion bridge／信任邊界變更升 major 版。§9.4 與 §14.4 的 direct-Hermes override 已完成獨立
auditor run 222 PASS，final audited implementation commit 為 `caeffea`，並已完成 live cutover。現行 production
由 n8n cadence／既有 fixed host bridge → Hermes `default` direct worker → Qlib／terminal evidence 推進；
**Kanban 不再是 production gate、queue、lock、scheduler、SoT 或派工 transport**，只保留工程協作與歷史 provenance。
下方 v1.11.0 及更早的 Kanban `create`／`unblock` 條文均為 audited historical baseline，不得當作 current
production authority。2026-09-25 observability close-out `824aecb` 另已 live：Current/counts 走 on-demand
canonical runtime observation，End-to-End display cadence 為 5 分鐘；詳見 `N8N_CONTROL_PLANE.md`。Phase 5 的
三次連續 real E2E transition acceptance 尚在觀察中，因此「整個五階段計畫完成」仍不得宣告。

歷史基線：**AUDITED PASS / LIVE AT v1.11.0 CUTOVER**（2026-09-23；既有 C3 implementation commit `68b338c` 已完成獨立 auditor PASS；C3.1 commits `9832b07`＋`fc44eb0` 另由 auditor run 445 PASS，並完成當時的 15 分鐘 scheduled cutover 驗證）。下列 v1.11 C3/Kanban 語意已由上方 v2.0 direct-runtime override 取代，不是 current production authority。
[v1.11 historical C3 state] n8n workflow `productionHandoffManualC2` 當時已完成 C3.1 live activation，cadence 為 `5,20,35,50 * * * *`（`:05/:20/:35/:50`），狀態 **AUDITED PASS / LIVE**。2026-09-23 23:05 Asia/Taipei execution 112 為 `mode=trigger`／`status=success`，下一分鐘 C4 execution 113 亦成功；Hermes cron `624d0be5b23c` 保持 **paused**、只作 rollback。此段「Kanban 仍參與 handoff」的歷史語意已由 v2.0 direct-runtime override 取代。
[v1.11 historical C3 fail-closed] Hermes／Kanban read-back unavailable 時的 HOLD 規則只描述 v1.11 歷史行為；**v2.0 current production 不讀、也不要求 Kanban read-back**。現行 fail-closed 依 canonical runtime artifacts、direct-family lease／active window、launch grace 與 unresolved incident semantics；仍不得新增 `PAUSED` state、health daemon、retry queue、watcher 或額外 preflight node。
[C3 maintenance SOP] HOLD transition → 乾淨停止 n8n／checkpoint／integrity check／known-good DB snapshot → reboot/update → login／既有 `ai.quant.recover-gate` 與 `ai.quant.n8n-host-bridge` LaunchAgent 復原 → readiness／Shadow／dry-run smoke → continue；不新增 recovery service。
[C4 current state] **AUDITED PASS / LIVE**：repo workflow `runtimeReconcilerC4`（`Quant Control Plane — Runtime Reconciler`）維持 `active=false`，live deployment 以 `6,21,36,51 * * * *` 觸發既有 fixed host bridge 的 `runtime_reconcile_once`；C4 automatic trigger 已於 2026-09-23 22:06 Asia/Taipei 成功觀測（execution 103、mode `trigger`、status `success`），前一拍為 C3 execution 102（22:05）。Hermes reconciler cron `f6b9aa5e9034` 保持 **paused**、只作 rollback；失敗即停用 C4、恢復 Hermes cron。watchdog `c5314d86cdfe` 保持 **active** 且獨立，Full Canvas `shadowQuantCp1` 保持 read-only。

文件狀態：**AUDITED PASS / FROZEN**（v1.10.0；semantic content commit `266390f1bc8179c503731cb1af2fda6195f7af42`；auditor `t_9073d65f`，2026-09-21；獨立唯讀 cross-repo audit **PASS**、`blocking_findings=[]`。受驗內容：三 repo refs／GitHub descriptions／docs diff scope、alpha EN/ZH + Intake sibling-output／eligible-universe 語意、Qlib-only／retired-engine boundary、§29 mirror contract 與 actual exporter semantics、validated seed bytes/readback；quant 隔離回歸 `test_survivor_private_export.py` **10/10**、`test_post_survivor.py` **44/44**、`test_survivor_evidence.py` **14/14** 全綠。此次 audit/attestation 未修改 runtime code、`/results`、candidate pool、cron 或 frozen artifacts）。
前一版 文件狀態：**AUDITED PASS / FROZEN**（v1.9.0；audited content commit `511b6e3`（= v1.9.0 審計當時的 remote main = 當時 HEAD），auditor `t_bde913b0`，2026-09-15；獨立唯讀審計卡 status=done、run 227 outcome=completed，PASS、無 scope expansion：`test_reconcile.py` **46/46**、runtime 全部 16 個測試檔 **320/320**；compute-finished wake 只放行 `scheduled` 的 authoritative `ARTIFACT_READY`／`FAILED_SCRIPT`、不寫 terminal／`verdict.json`／`/results`、fail-closed 邊界完整、`runtime/production_handoff.py` 未變、Contract v1.9.0 對齊。**v1.9.0 core audited at `511b6e3`；其後的變更**：`bdd2bae`（§14.4 **純文字對齊**；卡片 `t_86d04b09`），其後 `b48e449`（**已稽核的 lifecycle implementation／alignment**（三檔：`runtime/production_handoff.py` 於 append 時在 candidate body **之後**注入固定 lifecycle footer（candidate bytes 逐位元不改、`fingerprint_input` 不變）、`runtime/tests/test_production_handoff.py` 新增 regression、本文件 §6.4）——必要 prerequisite 客觀不存在且已由 measured evidence 證實時，`TECHNICAL_INCOMPLETE`（附 failure 層級／last run id／terminal evidence 路徑）即誠實且合法的終結、terminal evidence 齊備即 `done`，**不得**僅因 full-backtest outputs 不可能存在而形成 human gate；**不 bump semantic version、無新 gate／stage**；卡片 `t_2acfd339`，auditor `t_514e4827` PASS（27/27 target、351/351 runtime；pre-fix RED））。依 ChatGPT（GPT-5.6 Sol）卡片 `t_2b8c076c`，2026-09-15：§9.4 新增 **compute-finished wake**——authoritative current attempt 無 terminal sentinel、`run-spec.json` identity 可讀、卡片仍 `scheduled`，且 `state.json` stage ∈ {`ARTIFACT_READY`,`FAILED_SCRIPT`} 時，reconciler 以**既有** `unblock` 喚醒 default 做 host-side 處置（**不是** terminal verdict：不寫 sentinel／`verdict.json`／artifact、不建 incident、不啟動新 run）；`RUNNING_*`／無 `state`／stage 不可解析／superseded attempt／非 `scheduled` 卡一律行為不變；§14.4 補記 handoff cron 現況（`624d0be5b23c` **active**、`5,35 * * * *`）與 production loop 全貌，並以 v1.9.0 更正標註過期的「兩個 job 都 paused」現況文字。**audit PASS 後 reconciler cron `f6b9aa5e9034` 已 resume**（v1.9.0 交付時的「audit PASS 前不得 resume」條件已解除；2026-09-16 `hermes cron list --all` 讀回 handoff `624d0be5b23c`／reconciler `f6b9aa5e9034`／watchdog `c5314d86cdfe` 皆 active）。
前一版 文件狀態：**AWAITING AUDIT**（v1.8.0；依 ChatGPT（GPT-5.6 Sol）卡片 `t_67481d49`，2026-09-14：新增 **§26.1 一次性 additive schema migration 例外**——授權 family `ema-crossover-walkforward-momentum-long-short-v2` 的已凍結 round `…-r1` 之 `round-spec.json` **只新增一個** top-level `parameter_contract`（必須**完全由該 round-spec 自身已註冊的** `parameter_domain`／`dca_domain` 生成、`family_id` 一致、其餘所有 top-level／nested key／value canonical 逐位元不變；u1／u2 attempt 與其他所有 frozen artifact 不動），並**明文不放寬一般 INV-4**、不構成先例；同步要求 B／未來 generic family 的 round-spec template 內建該 schema、launch gate（§16.2 P10）與 instantiator 必須在 compute／publish **之前**呼叫 `parameter_contract.validate_round_spec_contract`。**本版變更尚未經獨立 auditor 稽核**（re-audit 卡於交付後建立；PASS 前不得 launch u3、不得 unblock `t_35b3e5da`））。
前一版 文件狀態：**AUDITED PASS / FROZEN**（v1.7.1；audited content commit ed07605（`ed076057eae09163cf608c61426c3229dce3e2ca` = v1.7.1 審計當時的 remote main = 當時 HEAD，audited bytes 未變），auditor `t_235ae131`，2026-09-14（獨立唯讀審計卡 `t_235ae131`（auditor lane、卡片 status=done、run 150 outcome=completed、metadata.verdict APPROVED／approved=true；default 未自審）：11/11 項 PASS——159/159 tests PASS（8 檔）、RED check（v1.7.0 bytes 上 11 FAIL＋3 ERROR）、70/70 fixture negative controls、production dry-run 對照（v1.7.0 = would_unblock=['t_35b3e5da']；v1.7.1 = []）、B v2 42 檔 snapshot 無硬變動（u2 仍 RUNNING_QLIB）、frozen SHA 14/14、cron `f6b9aa5e9034` 仍 paused／handoff `624d0be5b23c` 與交付時一致、board blocked=0、契約 v1.7.1 五點與實作逐點一致；未 resume 任一 cron、未 launch B v2、未改動 repo 或 `/results`；F1／F2 兩項 MINOR 文件精度 finding 經 operator 明確 **DEFER**、不開 v1.7.2、不改 runtime code／audited semantics；審計報告 sha256 `e6db775c…`）；依 ChatGPT（GPT-5.6 Sol）卡片 t_fd672413，2026-09-14：§9.4 reconciler 的 **authoritative current attempt** 修正——同一 round 內只有最新有效 attempt 可以改動 Kanban 狀態，較舊 attempt 的 terminal sentinel 一律 `superseded` descriptive no-op（仍可讀、可驗證、保留 provenance，但不得 unblock／complete／block）；較新 attempt 的 identity／ordering metadata 缺失、不可解析或歧義（含同一 timestamp 且無 `uN` tie-break、同 round task ownership 衝突）時整 round fail closed（incident，**不得**回退到較舊 terminal）；未新增 daemon／service／DB／current-pointer registry，未 resume 任一 cron，未 launch B v2、未寫入任何 `/results` attempt artifact）。前一版 **AUDITED PASS / FROZEN**（v1.7.0；audited content commit d21ec33（`d21ec336c7bd627908e6304866a7f209a8bea77f` = v1.7.0 審計當時的 remote main = 當時 HEAD，audited bytes 未變），auditor t_3219d6a0，2026-09-14（獨立唯讀審計卡 t_3219d6a0：verdict APPROVED、approved=true、144/144 tests PASS、static audit 42/42、隔離 pool verifier 全 true、序列 fixture B v2 → C → D → E（0 張真卡）、board blocked=0、cron f6b9aa5e9034 與 624d0be5b23c 皆仍 paused、handoff dry-run would_append B v2 at t_1f97bf6b、frozen A v2／_survivors／B v1 指定 SHA 全數相符；未 resume 任一 cron、未 launch B v2、未改動 repo 或 /results；審計報告 sha256 035d1b0e…）；依卡片 t_15fed3f2 的 production recovery automation：§14.4 candidate pool re-author（新增 B v2 完整 body、C/D/E 重寫為現行語意、fingerprint 重算）、board cleanup（`t_3e696dce`／`t_720406f2` 封存 archived、active blocked = 0）、新型 reconciler no-agent cron（job `f6b9aa5e9034`，建立即 **paused**、與 handoff cron 職責分離、不合併）；未 resume 任一 cron、未 launch B v2、未產生任何 C/D/E 真卡、未執行 Qlib 計算）。前一版 **AUDITED PASS / FROZEN**（v1.6.0；audited content commit 6e7d046，auditor t_e18a0f35，2026-09-13（獨立唯讀審計卡 t_e18a0f35：9/9 項 PASS、verdict APPROVED、無 blocking finding；非阻斷殘留 N1–N5 經 operator 明確 DEFER，不開 v1.6.1）；依 ChatGPT（GPT-5.6 Sol）卡片 t_68954a45 與唯讀研究卡 t_2382f20b（Operator 決策）：新增 §28 **Survivor Evidence Preservation**——「正式出現在 `leaderboard.json` entries 的 survivor」即為 evidence preservation 觸發點（不是 Top-10、不是 PASS gate），只有它升級成完整 execution evidence package（fills／episodes／equity 逐筆 ledger），而大量 rejected／candidate cell **明文不保留逐筆 execution**（維持 `artifacts/grid_*.csv` 摘要）；新增 `container/scripts/21_strategy_a_survivor_replay.py`（同一顆 engine 的 inert trace hook ＋ 只 replay promoted winner cells）與 `runtime/survivor_evidence.py`（host 純 stdlib：materialize／check／coverage）；寫入邊界再收窄為 `_survivors/evidence/**`，且 package 只能由 staging 原子 rename 發佈；leaderboard 增加**不參與排序**的 evidence drill-back 欄位；前一版 v1.5.2 = **AUDITED PASS / FROZEN**（audited content commit 0a361258，auditor t_3691bfb4，2026-09-13；依 ChatGPT（GPT-5.6 Sol）卡片 t_d19618e1（auditor t_346bcc04 對 v1.5.1 的 FAIL（F1/F2/F3）之最小 remediation）：reserved `_survivors` root **自身不得為 symlink**，且其 resolved 路徑必須恰為 resolved results root 之下的 **literal `_survivors` 子目錄**（root／ancestor escape 一律 rc=1、在任何寫入前即拒寫，`--out`／`--out-dir`／`forward` append 皆適用）；forward slice 的 `source_run.attempt_dir` 必須是 **absolute path**，realpath 後位於 results root 之**非** `_survivors` 的 attempt 目錄（relative 一律 fail-closed）；`kanban_task_id` 於 bundle 與 `family.json` **兩側皆須存在、皆為 non-empty string 且逐字相等**（int／bool／list／null／空字串一律 fail-closed，即使兩側同值）；前一版 v1.5.1 = **AUDITED FAIL**（auditor t_346bcc04，2026-09-13）：post-survivor 寫入邊界在工具層強制為 `_survivors/**`、forward evidence 必須可由 hash 釘住的真實 run artifact（terminal `DONE` ＋ sentinel 記錄的 `result.json` checksum ＋ 逐欄相等的 `forward_slice`）驗證、ownership id 缺漏一律 fail-closed；前一版 v1.5.0 = **AUDITED FAIL**（auditor t_57357d4c，2026-09-13；新增 Post-Survivor Lifecycle（§27）——frozen survivors 持續累積 post-freeze unseen evidence，形成可重算的 file-only survivor index 與 Top-10 leaderboard；唯一 blocking findings 為 F1（未受約束的 `--out`／`--out-dir` 可覆寫 frozen bundle／verdict）、F2（完全自述的 forward slice 可變成 `FORWARD_POSITIVE`／`champion_candidate`／rank 1）、F3（缺 `kanban_task_id` 的 bundle 放行））；更前一版 v1.4.2 = **AUDITED PASS / FROZEN**（audited content commit d699527，auditor t_dedbe003，2026-09-13；依 ChatGPT（GPT-5.6 Sol）卡片 t_670a86af（auditor t_3edafbb9 對 v1.4.1 的 F2 最小 remediation）；前一版 v1.4.1 = FAIL audit t_3edafbb9，2026-09-13；更前一版 v1.4.0 = FAIL audit t_0bd01630，2026-09-13；更前一版 v1.3.2 = AUDITED PASS / FROZEN，audited content commit 0363011，auditor t_3ffaeeb8，2026-09-13；更前一版 v1.3.1 = FAIL audit t_23f4c3ef，2026-09-13；更前一版 v1.3.0 = FAIL audit t_246c62d7，2026-09-13；更前一版 v1.2.0 = AUDITED PASS / FROZEN，audited content commit 068d6f7，auditor t_7b979fe8，2026-09-13；更前一版 v1.1.1 = AUDITED PASS / FROZEN，audited content commit 18d6c3f，auditor re-audit t_83682069，2026-09-13；更前一版 v1.0.1，auditor re-audit t_e35c39c0，2026-09-12））
版本：v1.11.0（2026-09-23）
作者：Hermes default（小蒨），依 ChatGPT（GPT-5.6 Sol）卡片 t_2fe87f35 實作；
版本：v1.10.0（2026-09-21）
作者：Hermes default（小蒨），依 ChatGPT（GPT-5.6 Sol）卡片 t_5b5b38d6 定版；v1.0.1 remediation 依 t_bcedaf65（audit t_a3dc355d B1–B3）；v1.1.0 依 t_ec039d5f（Nautilus 語意校正、full-backtest 定義、P8 interpreter 修正、最小 runtime readiness 落地）；v1.1.1 依 t_4d6c5cd5（audit t_d7f48c7a 的 F1–F3 最小 remediation 與文件精度修正）；v1.2.0 依 t_0626a619（新增 §14.4 automatic production handoff trigger）；v1.3.0 依 t_ad2e119e（DCA parameter domain 全量納入 full-backtest、cohort-level survivor disposition 取代跨 timeframe median gate、Strategy A 以新 family v2 重跑、Strategy B operator-stopped cleanup）；v1.3.1 依 t_6c83c9fb（audit t_246c62d7 的 F1/F2 最小 remediation：per-fill 費用會計、DCA provenance classification、M1 operator-stopped runner archive hygiene）；v1.3.2 依 t_33457313（audit t_23f4c3ef 對 v1.3.1 的 F3/F4 最小 remediation：獨立 gross/price-PnL accumulator、audit-only staging bytes）；v1.4.0 依 t_24cc6167（operator 決策：family 只要有 >=1 個 cohort survivor 即通過基本研究 gate、多個 survivors 不二選一而是全部保留並前進、新增 frozen survivor bundle、Strategy B v1 維持 operator-stopped 並備妥 B v2 preregistration）；v1.4.1 依 t_58166acc（audit t_0bd01630 對 v1.4.0 的 F1 最小 remediation：明確定義 frozen survivor bundle 的 canonical identity recipe，使公開的 `bundle_identity_sha256` 可由 auditor 以純 stdlib 自持久化檔案獨立重算）；v1.4.2 依 t_670a86af（audit t_3edafbb9 對 v1.4.1 的 F2 最小 remediation：重跑比對**逐鍵**只排除頂層 `contract` 與巢狀 `generator.sha256` 這兩個產生者身分欄位，`generator.path` 與其他所有欄位仍納入比對，非 dict 的 `generator` 不得被正規化掉）；v1.5.0 依 t_a7cfcdfd（operator 要求完善 Runtime SOP：新增 §27 post-survivor lifecycle——frozen survivors 持續累積 post-freeze unseen evidence、file-only survivor index、append-only forward evidence、可重算 Top-10 leaderboard、challenger rule 與 champion／live-candidate 邊界；不 launch B v2、不啟用 cron、不新增 service／daemon／queue／Registry／Orchestrator）；v1.5.1 依 t_171ba94f（audit t_57357d4c 對 v1.5.0 的 F1/F2/F3 最小 remediation：§27.1 的 `_survivors/**` 寫入邊界改為**工具層強制**（`--out`／`--out-dir` 兩側 realpath 邊界檢查，含 symlink 與 `..`，越界即 rc=1 且不寫入）、§27.3 新增 slice **`source_run` 出處契約**（必須指向結果樹內、非 `_survivors` 的 attempt 目錄；該目錄必須有 `terminal_evidence.py` 發佈的 terminal `DONE` sentinel、sentinel 記錄的 `result.json` checksum 必須等於 slice 宣告值與磁碟實際值、且 `result.json.forward_slice` 必須與 slice 逐欄相等；寫入與排名時都重新驗證，任一不符即 fail-closed）、§27.2 fail-closed 清單第 4 項與 A28 明定 `kanban_task_id` **缺漏**即來源不一致；語意不變：identity recipe、family gate、all-survivors mapping、B v2 preregistration 與未 launch、cron paused、不新增 service／daemon／queue／Registry／Orchestrator、不改任何既有 PASS/REJECT 或 frozen artifact）；v1.5.2 依 t_d19618e1（audit t_346bcc04 對 v1.5.1 的 F1/F2/F3 最小 remediation：§27.1 reserved root 自身不得為 symlink、resolved 後必須是 resolved results root 之下的 literal `_survivors`，root／ancestor escape 一律在任何寫入前拒寫（新增 `reserved_root_problem()`，並套用到 index `--out`、leaderboard `--out-dir` 與 `forward` append）；§27.3 `source_run.attempt_dir` 必須為 absolute path（relative 會以 reader cwd 解析）；§27.2 第 4 項 ownership id 必須兩側皆為 non-empty string 且逐字相等，任何非字串值即使兩側同值亦 fail-closed）；v1.7.1 依 t_fd672413（ChatGPT（GPT-5.6 Sol）：§9.4 reconciler 的 authoritative current attempt 最小修正——同一 round 內只有最新有效 attempt 可驅動 Kanban 轉換、較舊 terminal 一律 `superseded` descriptive no-op、unorderable round（identity／ordering metadata 缺失或歧義、同 round ownership 衝突）一律 fail closed 且不得回退較舊 terminal；ordering 以 run-spec `created_at_utc` 為主、`uN` 序數只作 deterministic tie-break）；v1.8.0 依 t_67481d49（ChatGPT（GPT-5.6 Sol）規劃卡，§26 (b)：新增 §26.1 **一次性 additive schema migration 例外**（僅授權 B v2 已凍結 r1 round-spec 只新增一個 top-level `parameter_contract`，由該 round-spec 自身已註冊 domains／axes 生成；明文不放寬一般 INV-4、不構成先例）＋ §16.2 P10 追加 schema 前置檢查 ＋ B／未來 generic family round-spec template 內建 `parameter_contract`、`runtime/preflight.py` 與 `runtime/instantiate_strategy_b_v2.py` 於 compute／publish 前呼叫 `validate_round_spec_contract`）；v1.9.0 依 t_2b8c076c（ChatGPT（GPT-5.6 Sol）規劃卡：§9.4 新增 **compute-finished wake**——authoritative current attempt 無 terminal sentinel 且 `state.json` stage ∈ {`ARTIFACT_READY`,`FAILED_SCRIPT`}、`run-spec.json` identity 可讀、卡片仍 `scheduled` 時，以**既有** `unblock` 喚醒 default 做 host-side 處置；不寫 terminal／verdict／artifact、不建 incident、不啟動新 run；§14.4 補記 handoff cron 現況與 production loop；未新增任何 service／daemon／cron／job／manager）；v1.10.0 依 operator 2026-09-21 明確指示與 ChatGPT（GPT-5.6 Sol）跨 repo 語意盤點：Qlib-only 現況、retired secondary-engine boundary、§29 validated-survivor mirror 與三 repo 文件對齊；doc-only，不改 runtime/code/data
作者補註（v1.6.0）：v1.6.0 依 t_68954a45（同一卡內含唯讀研究 t_2382f20b 的最小方案）：「survivor promotion → evidence preservation」入 §28；leaderboard entry 為唯一觸發點；ledger 只由同一顆 `simulate()` 的 inert 旁路產生（不建第二套 backtester、不重跑 103,680 次evaluation、只 replay 18 個 promoted winner cell）；provider 與 evidence 都不改寫任何既有 PASS／REJECT／ranking；B v2 未 launch、cron `624d0be5b23c` 仍 paused、Strategy B v1 仍 blocked。
作者補註（v1.7.0）：v1.7.0 依 t_15fed3f2：§14.4 candidate pool re-author（B v1 歷史 entry 逐字保留、新增 `ema-crossover-walkforward-momentum-long-short-v2`、C/D/E body 重寫為現行語意並重算 `fingerprint_input`）、board cleanup（B v1 operator-stopped 卡 `t_3e696dce` 與 DEFER flake 卡 `t_720406f2` 封存 archived、active blocked = 0）、reconciler no-agent cron 掛載（job `f6b9aa5e9034`、`every 15m`、wrapper `~/.hermes/scripts/quant_runtime_reconcile.py`）但**保持 paused**（與 handoff cron `624d0be5b23c` 職責分離，§9.4 v1.7.0 條）；未 resume 任一 cron、未 launch B v2、未產生 C/D/E 真卡、未新增 service／daemon／queue／Registry／Orchestrator、未做 checkpointing。
作者補註（v1.7.1）：v1.7.1 依 ChatGPT（GPT-5.6 Sol）卡片 t_fd672413，修 production 實際觀測到的 control-plane bug：`runtime/reconcile.py` 逐 attempt 掃描，於同一 round 已有較新 active attempt（B v2 `r1-u2` RUNNING_QLIB）時仍以較舊 attempt（`r1-u1` FAILED）的 terminal sentinel 放行 `t_35b3e5da`，造成 duplicate Hermes wake。修正：掃描單位改為 round，同 round 內**只有** authoritative current attempt（identity 合法、ordering＝`created_at_utc` 主序 ＋ `uN` 序數 tie-break）可驅動 Kanban 轉換；較舊 attempt 一律 `superseded` descriptive no-op；unorderable round 一律 fail closed（incident `attempt_selection_ambiguous`），不得回退較舊 terminal；consumed 判定仍先於驗證。**未**改 Strategy B engine／策略語意／candidate pool／handoff logic，**未**新增 daemon／service／DB／current-pointer registry／新 state machine，**未** resume 任一 cron（reconciler `f6b9aa5e9034` 仍 paused）、**未**碰 B v2 u2 的 process／run-spec／state／grid，**未**動 A／B v1 frozen science artifacts，**未**產生 Strategy C。
作者補註（v1.8.0）：v1.8.0 依 ChatGPT（GPT-5.6 Sol）卡片 `t_67481d49`（§26 change control (b)）：新增 **§26.1 一次性 additive schema migration 例外**——只授權 family `ema-crossover-walkforward-momentum-long-short-v2` 已凍結 round `…-r1` 的 `round-spec.json` **新增一個** top-level `parameter_contract`（由該 round-spec 自身已註冊的 `parameter_domain`／`dca_domain` 生成；其餘 key／value canonical 逐位元不變；u1／u2 與其他所有 frozen artifact 不動），並**明文不放寬一般 INV-4**、不構成先例；同時（同一變更的一部分）§16.2 P10 追加「launch gate 必須在 compute 前驗證 round-spec 的 `parameter_contract`」、`runtime/templates/strategy_b_v2_round_spec.template.json` 內建該 schema、`runtime/preflight.py` 與 `runtime/instantiate_strategy_b_v2.py`（含同 round retry reuse 路徑與 read-back）呼叫 `parameter_contract.validate_round_spec_contract`。**未**改 Strategy B engine／策略語意／domains／gates／selector／splits，**未**改本文件其他條文，**未**新增 daemon／service／DB／registry／queue／第二套 engine，**未** resume 任一 cron、**未** launch u3、**未** unblock `t_35b3e5da`；本版變更**尚未經獨立 auditor 稽核**（re-audit 卡於交付後建立）。
作者補註（v1.9.0）：v1.9.0 依 ChatGPT（GPT-5.6 Sol）卡片 `t_2b8c076c`（「Minimal fix — wake scheduled strategy on compute-finished stage + SOP align」；operator 明確要求切記過度工程、只做既有 completion bridge 的最小修補）：補上 full-auto completion 的 missing link——實測 Strategy D r1 u2 證明 container runner 會先落 `state.json` stage=`ARTIFACT_READY`（或腳本層失敗的 `FAILED_SCRIPT`）再退出，而 terminal sentinel 一律由 default 在 host 端發佈；若卡片已 park `scheduled`，過去沒有任何機制喚醒 default（reconciler 對無 sentinel 的 attempt 一律只回報 `orphan_candidate`），故在**既有** §9.4 驗證/放行語意上追加一條最小 wake：authoritative current attempt、無 terminal sentinel、`run-spec.json` 的 `task_id`/`kanban_board` 可讀、DB 讀回卡片仍 `scheduled`、`state.json.stage` ∈ {`ARTIFACT_READY`,`FAILED_SCRIPT`} → 執行**既有** `hermes kanban unblock`（`scheduled → ready`/`todo`）。`ARTIFACT_READY` **不得**自動等同 `DONE`（D u2 即為 `ARTIFACT_READY` 但斷言有 false 的反例）：本路徑不寫 `DONE`/`FAILED`/`INCOMPLETE`、不判 verdict、不改 `/results` 任何 artifact、不建 incident（除非既有 ownership／read-back 本身不成立）、不啟動新 run；`RUNNING_*`／無 `state.json`／stage 不可解析／superseded attempt／卡片非 `scheduled`／identity 取不到／card read-back 失敗一律維持描述性 `orphan_candidate` 且 fail-closed，authoritative current attempt selection 與 sentinel 驗證清單完全不動。`--dry-run` 只回 `would_unblock`。文件同步：§9.2 新增 compute-finished 非終結條、§9.4 新增 v1.9.0 `[C]`＋`[V]`、§12.2 交叉指針、§14.4 補記 handoff cron 現況（`624d0be5b23c` **active**、排程已由 ChatGPT 改為 `5,35 * * * *`）與 production loop 全貌、§22 A31、§23 item 19、§25 兩條硬規則，並以 v1.9.0 更正標註 §9.4/§14.4 中過期的「兩個 job 都 paused」**現況**文字（歷史交付事實逐字保留）。**未**新增任何 service／daemon／cron／job／manager、**未**改 `runtime/production_handoff.py`、**未**碰任何 strategy engine／parameter／DCA／backtest artifact、**未**操作任何 cron（reconciler `f6b9aa5e9034` 仍 paused，俟本版 audit PASS 後由 ChatGPT/operator resume）。
適用範圍：Qlib 研究 runtime、n8n fixed-bridge cadence 與 direct-Hermes production handoff；Kanban 僅屬工程協作／歷史 provenance，不是 production control plane
變更控制：見 §26；本文件為長期 implementation contract，不是高階摘要

---

## 標記慣例（三個等級，不得混用）

- `[V]` **VERIFIED CURRENT BEHAVIOR** — 本文件寫作時，於本機實測或讀回 kernel 原始碼確認的現況。
- `[C]` **CANONICAL DESIGN DECISION** — 已拍板的設計決策；實作必須遵守，但「已拍板」不等於「已實作」。
- `[T]` **TO-BE-VALIDATED** — 尚未實作或尚未在真實故障下驗證者。任何 `[T]` 條目都不得被引用為既有能力。

若一句話同時含事實與決策，拆成兩句分別標記。**禁止把 `[T]` 寫成 `[V]`。**

---

## 0. 本文件的權威性與從屬關係

- `[C]` 本文件是 Qlib 研究 runtime 的 implementation contract。當實作、卡片敘述、口頭指令與本文件衝突時，以本文件為準；若要偏離，走 §26 change control。
- `[C]` 本文件不重寫 Kanban kernel，也不假設可以改 kernel。所有狀態轉移都必須使用 kernel 既有合法操作（§6/§7）。
- `[V]` Kanban kernel 合法狀態集合為：`triage, todo, scheduled, ready, running, blocked, review, done, archived`（`hermes_cli/kanban_db.py: VALID_STATUSES`）。
- `[V]` `promote_task` **只**接受 `todo`/`blocked` → `ready`；對 `scheduled` 會回錯（`promote only applies to 'todo' or 'blocked'`）。因此 `scheduled` **不可能**用 promote 放行。
- `[V]` `schedule_task` 接受 `todo`, `ready`, `running`, `blocked` → `scheduled`，並清空 `claim_lock`/`claim_expires`/`worker_pid`，同時 end-or-synthesize 該 run（`outcome='scheduled'`）。
- `[V]` `unblock_task` 接受 `blocked`/`scheduled` → 依父卡完成度落 `ready` 或 `todo`（父卡未完成則重新 gate 回 `todo`），清 `current_run_id`、重設 `consecutive_failures`。
- `[V]` `block_kind`/`block_recurrences` 不會被 unblock 清除，只有 `complete_task` 清除（防止 unblock↔re-block 無限迴圈；這是 kernel 既有防護，本 contract 不得繞過）。

## 1. 目的與非目標

### 1.1 目的
- `[C]` 讓「研究決策（Hermes default）」與「數值計算（Qlib container）」以**最短控制面**接起來：卡片進、artifact 出、sentinel 落地、host 端確定性收斂。
- `[C]` 讓單一 strategy family 的科學迭代（round）可被序列化、可被誠實終結、且不會餓死後續 family。
- `[C]` 讓 Mac 重開機、container 重啟、mount 掉線、PID 遺失之後，系統能回到**可執行狀態**（不只是 Kanban 控制狀態正確）。
- `[C]` 保持最小設計：一個 family 一張卡、一個 round 一次科學迭代、一個 run 一次執行嘗試。

### 1.2 非目標（明文禁止）
- `[C]` 不建立 HTTP server / webhook / Redis / Celery / RabbitMQ / 任何 queue manager 作為完成橋。
- `[C]` 不新增 Manager / Service / Factory / Registry / Orchestrator 類抽象層；不新增 daemon。
- `[C]` 不讓 Qlib container 取得 Hermes credentials、Kanban DB 寫入能力或 host control socket。
- `[C]` 現行 production 的唯一計算面與效能真值來源是 Qlib container 的 full-backtest（§7.2）。Lean／Nautilus／PyBroker 已退役並 **non-participating**（§17）：它們不是 current/future production stage、authoritative gate 或 performance truth，也不得作為第二套全量參數搜尋／回測引擎。歷史版本、frozen artifacts 或 research records 中的 retired-engine references 只保留 provenance，不構成重新導入承諾；任何日後第二引擎重啟都必須由 operator 另走 §26。
- `[C]` 不把「done」當成「PASS」（§6.4）。

## 2. Roles / RACI

| 角色 | 實體 | Responsible | Accountable | Consulted | Informed |
|---|---|---|---|---|---|
| 研究方向與 owner 決策 | 漢秦哥（operator） | — | ✅ 全鏈 | — | — |
| 方法/架構/拆解/review | ChatGPT（GPT-5.6 Sol） | 卡片 spec 與驗收條文 | — | ✅ | ✅ |
| 研究決策與實作 | Hermes default（小蒨） | 產 spec、判 verdict、寫 `/results` durable ownership/lineage/verdict artifacts、執行 preflight、跑 reconciler | ✅ 研究鏈 | — | — |
| 數值計算 | Qlib container `qlib-run` | 只算、只寫 `/results` artifact | — | — | — |
| 獨立審計 | Hermes `auditor` profile | 唯讀審查 | — | — | ✅ |
| 完成橋 | deterministic no-agent reconciler | scheduled → ready | — | — | — |

- `[C]` RACI 邊界鐵律：**decision 與 computation 分離**。Hermes default 只做研究決策，不長 turn 等 Qlib；Qlib 只做計算，不做判斷、不改卡片。
- `[C]` auditor 只審計、不 remediation；FAIL 修復一律回 default，再交 auditor re-audit。
- `[C]` 同一張卡不得自我審計（self-review 與獨立 audit 必須是不同 profile）。

## 3. Trust boundaries

`[V]` 現況 mount 與信任邊界（實測，2026-09-12）：

| container 路徑 | host 來源 | 模式 | 可重建 | 用途 |
|---|---|---|---|---|
| `/data/raw` | `/Volumes/ExpansionDrive/market-data-raw` | **ro** virtiofs | 否（canonical raw） | 唯一原始資料來源 |
| `/results` | `/Volumes/ExpansionDrive/qlib-results` | **rw** virtiofs | 否 | durable authoritative research evidence |
| `/qlib/work` | named volume `qlib-work`（30 GiB ext4） | rw | **是** | derived / cache / work only |
| `/scripts` | `/Users/hong/workspace/qlib-apple-container/scripts` | **ro** | 是 | 進 container 的唯讀腳本 |

- `[V]` 除上表外，container 沒有掛載任何其他 host 路徑。
- `[V]` raw 唯讀已獨立複驗：`touch /data/raw/__probe__` → `Read-only file system`（exit 1）。
- `[C]` **container 永不持有**：Hermes credentials、`~/.hermes/**` 任何 secret、Kanban DB 寫入能力、host control socket（container runtime socket）、host SSH key。
- `[C]` container 對外唯一 communication surface 是 `/results` 檔案系統（單向、host-readable）。反方向（host → container）只用 `container exec`，由 host 主動發起。
- `[C]` 資料流單向：raw(ro) → 容器計算 → `/results`(rw) → host 讀回。任何需要 host 寫入 raw 的行為都不屬於本 pipeline。
- `[C]` reconciler 不信任 container 的任何「自述」（exit code、log line、comment）；只信任 `/results` 上的 durable terminal evidence 與自身對 Kanban DB 的讀回。
- `[V]` `/results` 的 checksum 產生與驗證機制已以最小版落地並實測（2026-09-13）：`runtime/terminal_evidence.py` 產生 manifest checksums 與 atomic sentinel，`runtime/reconcile.py` 對 manifest 逐項重算比對。缺失或不符合的 checksum 一律 fail-closed。

### 3.1 Common Data Pack contract

`[V]` 2026-09-21 read-back：`/data/raw` 的 canonical raw surface 由
`/Volumes/ExpansionDrive/market-data-raw/_tools/market_data_sync.py` 單一 updater 維護，
不引入第二套 backtester、資料庫或 provider-specific runtime。除了既有 Binance/CBOE VIX/
Deribit DVOL 外，common pack 現已包括：Alternative.me FGI、Coin Metrics Community
BTC/ETH daily metrics、expanded FRED macro/index pack、CBOE VIX9D/VIX3M/VVIX/SKEW、
CFTC legacy/TFF/disaggregated selected-market COT、Kenneth French US/developed
3F/5F/momentum daily/monthly factors，以及 Deribit perpetual funding 與 current
futures/options book-summary snapshots。

`[C]` Common Data Pack 是**高復用回測原料庫**，不是 data-governance platform。官方 API／
官方下載優先；來源透明、維護可靠、授權與原始資料可追溯的權威 GitHub 專案或成熟
adapter／distribution 也可作資料取得管道，但不因此成為 production framework、runtime
dependency 或第二套資料管線。

`[C]` provenance 依資料結構分級：homogeneous single-source dataset 只需 dataset／provider
層級 metadata 可追溯；row-level `source`／`truth_status` 只有在來源／真值語意會逐 observation
改變，或策略科學正確性確實需要時才是必要欄位。單純缺少重複 row-level metadata 屬
**non-blocking hygiene**，不得阻擋不相關策略、不得據此重寫已落地 raw、不得形成 human gate。
不得由 FGI/COT/factor/IV/OI/macro 欄位在 raw layer 偷算或補造不存在的觀測；CFTC 的 long/short/
spread 欄位保留於 `columns`；Fama-French `-99.99` 只轉成 JSON null；Coin Metrics
catalog-v2 404 時只能使用已驗證的 explicit timeseries endpoint；Deribit options
snapshot 不得宣稱完整歷史 backfill。

`[C]` Common Data Pack 的完成定義只有：①高復用通用資料已落地；②基本完整性、coverage、
可讀性與時間對齊合理；③掛入同一支 `_tools/market_data_sync.py`；④由同一個 launchd
`ai.marketdata.raw-sync` 每日統一補資料。達成即 DONE，不新增其他 gate。candidate 的
data readiness 只看該 candidate 核心 signal／mechanism 所需資料是否存在、真實且可合法對齊／
計算；其他 dataset 的 hygiene finding 不得 block candidate。Common Data Pack v2 到此 freeze；
之後只在出現明顯高復用的新共通資料時增補；tick／orderbook／full option-chain 等 heavy data
維持 lazy／on-demand。

`[V]` 初次匯入、第二次 up-to-date/incremental read-back、gzip deterministic write、
duplicate-key 檢查與 provider failure 的非零 exit code 均已由 raw updater 實跑驗證；
資料 layout/schema/coverage 以 raw root `_meta/SCHEMA.md` 與 `_meta/CONFIG.json` 為準。
`runtime/crypto_bitcoin_cvar_risk_aware_q_learning_prerequisite_check.py --live-recheck`
是針對既有 RaQL prerequisite-missing round 的唯讀 current-raw recheck：它驗證
Alternative.me FGI payload 的 official provenance、值域、單調唯一 timestamp 與 coverage，
但不重開或改寫既有 immutable round-spec/verdict；若 live core signal 可得，後續另由 operator
決定是否建立新 round。

## 4. 術語

| 術語 | 定義 |
|---|---|
| family | 一個 strategy/hypothesis family。**一張 strategy card = 一個 family**。 |
| round_id | **科學迭代**單位：同一 family 內一次假說變更/篩選階段的語意版本。 |
| run_id | **執行嘗試**單位：同一 round 的一次 execution attempt。technical retry 只換 run_id，round_id 不動。 |
| attempt | run_id 對應的一次實際執行；每個 attempt 必有 terminal evidence（含技術失敗）。 |
| verdict | 科學結論：`PASS` / `REJECT` / `FINALIST` / `DEFERRED` / `TECHNICAL_INCOMPLETE`。 |
| performance_claimable | 該結論是否可用於效能宣稱（僅 `PASS` 且滿足 §9.6 條件時為 true）。 |
| WAITING_QLIB | 卡片 runtime 語意：正在等 Qlib 計算，卡狀態為 `scheduled`。 |
| sentinel | `/results` 下的終態檔案（`DONE` / `FAILED` / `INCOMPLETE`），atomic publish。 |
| reconciler | host 端 deterministic **no-agent** 腳本，把 WAITING_QLIB 的 `scheduled` 卡按 sentinel 放行。 |
| shared-layer | 影響全鏈的共用層：mount、container runtime、image、`/results` 可寫性、Qlib import。 |
| card-local | 只影響單卡的失敗：該卡的參數/資料窗/腳本/預算問題。 |
| ordering chain | A→B→C 的 append-only 序列化，僅代表 scheduling/order。 |
| owner artifact | 記錄 runtime ownership 與 lineage 的 durable 檔案（`family.json`、`round-spec.json`、attempt `run-spec.json`）；它承載 Kanban card 不具備的 machine-readable state。 |
| unconsumed sentinel | attempt 目錄內存在 terminal sentinel，且其 `kanban_task_id` 指向的卡片仍是 `scheduled`（尚未放行）。consumed = 該卡已非 `scheduled`。 |
| parent_family | scientific lineage 欄位，記於 `/results/<family_id>/family.json`（不是 Kanban parent edge，也不是卡片 metadata）。 |

## 5. System invariants

- **INV-1 `[C]`** 一張 strategy card 恰對應一個 family；不得一卡一 experiment。
- **INV-2 `[C]`** round_id 與 run_id 分離；technical retry 不得遞增 round_id。
- **INV-3 `[C]`** 同一時刻，一個 family 最多一個 active round；同一 round 最多一個 active run。
- **INV-4 `[C]`** `/results` 的 `family.json`/spec/result/verdict/terminal evidence 一旦 publish 即 immutable；只有 `state.json` 可 atomic rewrite。
- **INV-5 `[C]`** `/qlib/work` 永不是真值來源；刪掉它不得損失任何研究結論。
- **INV-6 `[C]`** `/data/raw` 永不從 container 被寫入。
- **INV-7 `[C]`** 卡在等 Qlib 時狀態必須是 `scheduled`；不得用 `blocked` 代表正常等待。
- **INV-8 `[C]`** 完成橋只能是「durable sentinel + host deterministic no-agent reconciler + kanban unblock」；不得引入網路服務或 queue。
- **INV-9 `[C]`** Kanban parent edge 只代表 scheduling/order；科學血緣一律記於 `/results/<family_id>/family.json` 的 `parent_family`（卡片不承載 metadata）。
- **INV-10 `[C]`** `done != PASS`；verdict 與 performance_claimable 另記於該 round 的 `/results/…/verdict.json`（immutable）。
- **INV-11 `[C]`** NEW_FAMILY 永遠 append tail；不得插隊、不得 live rewiring。
- **INV-12 `[C]`** card-local failure 不得 freeze 全鏈；只有 shared-layer（跨卡、系統性）failure 可 freeze。單一 work volume（`/qlib/work`）失敗屬 card-local（§12.5/§13）。
- **INV-13 `[C]`** 同一 family 必須有客觀 stop/yield gate（§15），不得無限 REFINE。
- **INV-14 `[C]`** 每次 launch/retry Qlib 前必須通過 deterministic preflight（§16）。
- **INV-15 `[C]`** terminal evidence 已存在（DONE/FAILED/INCOMPLETE）時，**不得重跑**同一 run。
- **INV-16 `[C]`** duplicate reconciliation 必須無害（idempotent）；任何 reconciliation 結果都必須由 DB 讀回驗證，不得只採信腳本自述。
- **INV-17 `[C]`** comments 不是 state，卡片也不承載 machine-readable metadata（`[V]` kernel 無此欄）。Kanban card 只保存 lifecycle/status/ordering（DB 欄位 + events）；machine-readable runtime ownership、scientific lineage 與 verdict 只存在於 `/results` durable artifact。

## 6. 卡內狀態機（card-local）

### 6.1 Kanban 狀態
`[V]` kernel 既有狀態與 pipeline 語意對映：

| Kanban 狀態 | pipeline 語意 | 進入方式 |
|---|---|---|
| `todo` | 未排程/等待父卡 | 建立時；父卡未完成而 unblock/reopen |
| `ready` | 可被 dispatcher 撿起 | 父卡全 done；`unblock` 自 `scheduled` |
| `running` | worker 正在研究決策 | dispatcher claim |
| `scheduled` | **WAITING_QLIB**：已投遞 Qlib job、等 sentinel | worker 呼叫 `schedule_task` |
| `blocked` | 需要人或 shared-layer 介入 | worker `kanban_block` |
| `review` | 等人/reviewer 看（非 block） | `kanban_request_review` |
| `done` | 終結（含誠實負結論） | `kanban_complete` |
| `archived` | 封存 | operator |

`[V]` **正常 Qlib 等待路徑 = `running` → `scheduled` →（reconciler unblock）→ `ready` → `running`。**

- `[C]` 禁止把 `blocked` 寫成 Qlib 等待狀態。`blocked` 保留給：needs_input / dependency / capability / transient 真阻塞。
- `[C]` 禁止把 `promote` 寫成 release 機制；`[V]` promote 對 `scheduled` 直接拒絕。
- `[C]` `scheduled` 期間 worker 已合法退出：`[V]` `schedule_task` 會清 worker_pid/claim 並 end run（`outcome='scheduled'`），因此不佔用長 turn、不佔用 dispatcher slot。

### 6.2 研究階段（`stage` 記於 attempt `state.json`，非 Kanban 狀態）
`[C]` 每個 round 允許的階段序列（線性、不可回頭）：

```
PREREGISTER → LAUNCH_PENDING → RUNNING_QLIB → ARTIFACT_READY → VERDICT → (next round | terminal)
```

- `[C]` `stage` 只記錄在 `/results/…/attempts/<run_id>/state.json`（atomic rewrite）；它不改 Kanban 狀態，也不寫卡片 metadata。
- `[C]` 進入 `VERDICT` 前必須存在可讀回的 Artifact（§10）。
- `[C]` `RUNNING_QLIB` 對應的 Kanban 狀態必須是 `scheduled`；若觀測到 `RUNNING_QLIB` 而卡片不是 `scheduled`，視為 invariant 破壞，走 §12.4 reconciliation。

### 6.3 轉移表（含守衛條件）

| From | To | 觸發 | 守衛（全部必須成立） |
|---|---|---|---|
| `ready` | `running` | dispatcher claim | 父卡全 done（`[V]` claim 會重檢） |
| `running` | `scheduled` | 投遞 Qlib job | spec 已 immutable publish；preflight PASS（§16）；run_id 已配置 |
| `scheduled` | `ready` | reconciler unblock | sentinel 為 terminal（DONE/FAILED/INCOMPLETE）；task/run/round/family mapping 一致；required artifacts 存在且 checksum 相符 |
| `scheduled` | `todo` | reconciler unblock（父卡未完成） | 同上；`[V]` unblock 會自動落在 `todo` |
| `running` | `done` | `kanban_complete` | 有 terminal conclusion（§6.4）；durable artifacts（sentinel / `verdict.json`）齊備 |
| `running` | `review` | `kanban_request_review` | 有預先建立的 review/QA 子卡時**改用 complete**，不得同時 request review |
| `running` | `blocked` | 真阻塞 | `kind` ∈ {needs_input, dependency, capability, transient}；不得用於 Qlib 等待 |
| `blocked` | `ready`/`todo` | 人工 unblock | — |
| `scheduled` | `blocked` | 不允許（僅 operator/default 後續顯式 two-step） | `[C]` `[V]` kernel `block_task` 只接受 `running`/`ready`，對 `scheduled` 回 False，故「對 scheduled 卡直接 block」不可執行；衝突/歧義一律走 §12.6 incident（保持 `scheduled`、不 unblock、寫 incident artifact）。若確有需要轉 `blocked`，必須由 operator/default 在後續顯式流程先 `unblock`（→`ready`/`todo`）再 `block`；此 two-step 不得包成 reconciler 自動動作 |

### 6.4 終結語意：done != PASS

`[C]` 只要「誠實」就是合法 done。`verdict` 與 `performance_claimable` 分開記錄：

| verdict | 意義 | 可否 done | performance_claimable |
|---|---|---|---|
| `PASS` | 通過預先註冊的門檻 | ✅ | 僅當 §9.6 條件全成立 |
| `REJECT` | 明確被證據否證 | ✅ | false |
| `FINALIST` | 通過研究門檻、進入下游 | ✅ | false（研究階段） |
| `DEFERRED` | 有意不續（資源/優先序） | ✅ | false |
| `TECHNICAL_INCOMPLETE` | 技術上無法完成且已誠實終結 | ✅ | false |

- `[C]` `TECHNICAL_INCOMPLETE` 必須附：失敗層級（card-local vs shared-layer）、最後一次 run_id、terminal evidence 路徑、未完成原因；寫入該 round 的 `/results/…/verdict.json`，comment 只放人類摘要。
- `[C]` 禁止用 `done` 掩蓋失敗：沒有 terminal evidence 的失敗只能 `blocked` 或 `TECHNICAL_INCOMPLETE`。
- `[C]` **v1.4.0 family verdict 對映（cohort survivor disposition）**：0 個 cohort survivor → verdict `REJECT`；**>=1 個 → verdict `PASS`**（恰 1 個 = disposition band `SURVIVOR_FOUND`，>1 個 = band `MULTIPLE_SURVIVORS`；**band 只描述 survivor 數量，不是 verdict**）；coverage／技術不完整 → `TECHNICAL_INCOMPLETE`。`FINALIST` 仍在 verdict 列舉內（v1.3.x 已凍結的 artifacts 與其他終結原因可續用），但**v1.4.0 的 cohort-survivor disposition 永不產生 `FINALIST`**。`performance_claimable` 只由 §9.6 的客觀條件決定；survivor 數量 >1 **不**構成 false 的理由。
- `[C]` 上表是 verdict 的語意；band → verdict 的對映屬本 contract 的版本化條文（§7.3）。disposition **版本字串**（`cohort-disposition-v1`）版本化的是 cohort 判定語意（selector 步驟、五項 survivor 要求、band 集合），v1.4.0 未更動這些；因此 engine 在每筆輸出揭露 `contract_semantics_version`／`disposition_mapping_version`，讓 v1.3.x 已凍結的 artifacts 仍可被讀成它們原本的語意，而不被重新貼標。
- `[C]`（**operator correction，2026-09-18；不新增 gate／stage／append 條件**）**local eligible universe 與 prerequisite-missing 的終結語意**：每張卡在任何計算前，eligible universe 固定為 canonical local raw 中「能讓該策略核心 signal/mechanism 合法計算」的完整可用集合；原研究的 market／symbol 清單是 provenance／外部效度參考，**不是**必須逐字一致的執行 prerequisite。不得僅因 source market 不同、named symbols 缺少或本機 universe 較小就判 `TECHNICAL_INCOMPLETE`；只要核心 signal 可在本機資料上計算，就必須用該 local eligible universe 完整跑 `symbols × timeframes × parameter domain × DCA execution × historical/OOS/robustness`，並把結論範圍限制在該 local universe；eligible universe 一經 pre-register 後不得依結果挑幣、縮減或擴張。只有核心 signal 所必需的 data type／field 在本機完全不存在、使任何合法 local universe 都無法計算時，才屬 prerequisite-missing `card-local` failure，可依 measured evidence 終結為 `TECHNICAL_INCOMPLETE`（附 failure 層級、last run_id、terminal evidence 路徑、未完成原因）。`runtime/production_handoff.py` 的 system-owned lifecycle footer 對 candidate 內與此衝突的 source-market exact-match／不得縮減文字具有執行優先，但 candidate bytes、`fingerprint_input` 與 append 條件本身不改寫。`kanban_block` 仍只保留給 shared-layer failure（§12.5）或 contract 尚未決定、確實需要 human decision 的情況（§12.6）；卡片 terminal 後 §14.4 handoff 照既有條件 append 下一張。
- `[C]`（**Phase 2B prerequisite evidence 有界化；不新增 gate／stage／append 條件、不實作 WAITING_DATA／READY_TO_RESUME**）prerequisite 缺席判定的**唯一 canonical 證據來源**是既有 Common Data Pack catalog/schema（§3.1）：`/data/raw/_meta/CONFIG.json` 的 dataset IDs 與 `/data/raw/_meta/SCHEMA.md` 的 field/layout 宣告，**不得建立第二套 data registry／taxonomy**。評估必須有界：只讀這兩個 catalog/schema 檔，必要時再對 canonical raw 下的一個小 sample／path 做直接讀取，**不得掃描無關 host 目錄或整個檔案系統**。當 CONFIG／SCHEMA 明確表示核心 signal 所必需的 data type／field 不存在（clear-absence）時，記載 requirement-vs-available 實測事實的 immutable `round-spec.json` ＋ `verdict.json` 即為 card-local `TECHNICAL_INCOMPLETE` 的**充分** terminal evidence，**不得 launch 任何 full backtest**。明確禁止為證明顯然不存在的資料能力而新增 candidate-specific prerequisite checker（`runtime/*_prerequisite_check.py`）、repo-wide prerequisite evidence blob、host-wide 掃描、synthetic fixtures、tamper batteries 或 bespoke validation framework；既有 legacy checker／evidence 檔案屬不可變歷史證據，不移除、不改寫。若 CONFIG／SCHEMA 對既有 dataset 是否具備所需 field／capability 真正 ambiguous，fail closed，只對該 canonical dataset 做有界直接 read-back，**不自動擴大 scope**；human input 只留給未解的 ambiguity，不用於 clear-absence。本條不改 candidate selection ordering、不改 pool schema、不新增 service／watcher／cron／DB；可執行面只落在 `runtime/production_handoff.py` 的 system-owned lifecycle footer，candidate bytes、`fingerprint_input` 與 append 條件不改寫。

## 7. 卡間狀態機（A→B→C ordering chain）

- `[C]` strategy cards 以 append-only ordering chain 序列化：A → B → C。新 family 只能接在 tail。
- `[C]` 實作方式：`kanban_create(..., parents=[<tail card id>])`。`[V]` 子卡在父卡 done 前停在 `todo`，父卡 done 後才 auto-promote `ready`。
- `[C]` **parent edge 只代表 scheduling/order**。不得描述為 scientific lineage、不得描述為 approval gate、不得描述為「B 是 A 的改良版」的證據。
- `[C]` scientific lineage 一律寫在 `/results/<family_id>/family.json`：`parent_family`（family_id 或 `null`）、`lineage_note`。這兩者與 Kanban parent edge 無關，可不同；卡片本身不承載 metadata。
- `[C]` 鏈上活動卡數：同一時刻整條研究鏈只應有 1 張 active strategy card（chain head）。其餘為 `todo`（等父卡）或 `done`。
- `[V]` 2026-09-21 讀回：現行 production strategy active baseline = **0**；Lean／Nautilus／PyBroker 舊卡與舊 runtime 均不屬現行 production。文件／audit／維護卡的 control-plane 狀態不得被計入 active strategy family 數。
- `[C]` 禁止 live rewiring：不得在鏈中間插卡、不得改既有 parent edge 以「優化順序」。要調整順序只能 tail append 新卡，並在其 `family.json` 的 `lineage_note` 說明。

### 7.1 chain head 判定（確定性）
- `[C]` chain head = 在 `quant-strategy-research` board 上、狀態 ∈ {`ready`,`running`,`scheduled`}，且被某個 `/results/<family_id>/family.json` 的 `kanban_task_id` 反查命中的卡（即 strategy card）。**不以卡片 metadata 判定**（`[V]` kernel 無 metadata 欄）。
- `[C]` 若同時存在 >1 張，屬 INV-3/§7 破壞 → 走 §12.6 incident：不自行挑一張跑，且不得對 `scheduled` 卡直接 `block` 或 `promote`。
- `[C]` 若某張 `ready` 卡尚無對應 `family.json`（tail append 同一輪進行中）：不得投遞 Qlib；claim 後必須先確認 `family.json` 已落地，否則走 §12.6 incident。
- `[T]` 上述判定尚未有腳本化查詢；落地前由 default 每輪以 DB 讀回 + `/results/*/family.json` 掃描人工確認。

### 7.2 全量回測（full-backtest）定義與 production 模式

- `[C]` **全量回測（v1.3.0）**：一張 strategy card 在其**預先定義的 eligible universe** 內完整覆蓋下列六個維度，才可宣稱完成一次 full-backtest：
  1. `symbols`：該卡事先註冊的合格 symbol 集合（不得事後擴張或挑選）。
  2. `timeframes`：事先註冊的全部頻率；每一個 `(symbol, timeframe)` 對 = 一個 **cohort**，也是 v1.3.0 的 disposition 單位。
  3. `strategy parameter domain`：事先註冊的完整策略參數域（訊號層），不是單點試算。
  4. `DCA parameter domain`：事先註冊的完整 **DCA 參數域**（執行/加倉層：spacing、size multiplier、breakeven TP、invalidation…），並以真實逐筆成交會計**實算每一組 DCA 參數**。單一條固定 DCA rail 不再構成 full-backtest。
  5. `historical / OOS / robustness`：in-sample 歷史段、明確 out-of-sample 段、robustness（參數鄰域／敏感度／執行壓力）三者皆完成。
  6. **每一個 cohort 都跑完整的 `strategy domain × DCA domain` 乘積**（不得只抽樣、不得只跑部分 cohort 的子集）。
- `[C]` **DCA provenance classification（v1.3.1）**：round-spec 的 `dca_domain` 必須逐項聲明 provenance class：被**搜尋**的軸（本次為 `spacing_pct`／`size_multiplier`／`breakeven_tp_pct`／`invalidation_pct`）屬 `PROJECT_PRE_REGISTERED_SEARCH_DOMAIN`；被固定但**無 operator 明確固定證據**的常數（本次為 `base_quote`）屬 `PROJECT_PRE_REGISTERED_CONSTANT`；只有具**明確 operator 證據**的項目才可列為 `USER_FIXED`。被搜尋的軸或 project 常數**不得**被描述成 user-fixed invariant：搜尋域本身就是預先註冊的科學量，把可選參數標成不變量等於把「選出來的」偽裝成「固定的」。可執行強制：`runtime/strategy_a_v2_counts.py` 的 provenance 檢查（`base_quote_status` 必須是 `PROJECT_PRE_REGISTERED_CONSTANT`、被搜尋軸的 status 必須是 `PROJECT_PRE_REGISTERED_SEARCH_DOMAIN`、DCA 軸與 `base_quote*` 不得出現在 `user_fixed_invariants`、operator 證據項不得被刪除、round-spec 與 run-spec 的分類必須一致），負向控制見 `runtime/tests/test_strategy_a_v2_counts.py`。
- `[C]` **per-fill 成本會計（v1.3.1）**：DCA 的「真實逐筆成交會計」意謂每一個 entry／DCA add／exit fill 的 taker fee（以及 funding）必須在**該 fill 的時點**扣入 realised equity，不得只在 episode 結束時一次扣、也不得只累積成一個統計量。`net_pnl`／`ending_equity`／每日 equity marks／Sharpe／margin 與 leverage 判定必須全部是 **net-of-fee**；`fee_2x` 等成本壓力軌必須實質改變 net PnL／equity／robustness verdict（成本壓力軌成為 no-op 即視為未實作）。負向控制：`container/scripts/tests/test_strategy_a_engine.py` 的 free／costly／`fee_2x` 迴歸（單次 TP 的 entry+exit fee 精確算例、DCA add 逐層費用、intra-episode equity mark 隨費用變動）。
- `[C]` **獨立 gross PnL 會計（v1.3.2）**：`gross_pnl` 必須來自**獨立的 price-PnL accumulator**——在每一個 exit／flatten 時只累加該 episode 的**純價格損益**（exit proceeds − cost basis），**不含 fee、不含 funding**；entry／DCA add 只改 qty／cost basis，不增加 gross。`gross_pnl` **不得**由 net 反向回推（v1.3.1 的 `realized + fees_total + funding_paid` 已廢除：把 net 加回成本即為恆等式，費用或 funding 路徑一旦壞掉也驗不出來）。net realised equity 仍由 price PnL − 每個 fill 的 fee − funding 逐筆形成（§7.2 per-fill 成本會計）。`pnl_decomposition` 必須是**兩個獨立來源**的交叉比對（`gross_pnl − fees − funding` 對 `net_pnl`，容差 1e-3），且必須以負向控制證明它**非恆真**：`container/scripts/tests/test_strategy_a_engine.py` 的 `TestGrossPnlAccounting`（單次 TP 與 ladder stop 的獨立手算 gross/net、成本壓力軌只動 net 不動 gross、gross 路徑 monkeypatch、fee 只扣不入帳）在未修版本上必須失敗。
- `[C]` eligible universe 與兩個參數域的註冊位置是 `/results/<family_id>/rounds/<round_id>/round-spec.json`（immutable）；該卡的完成判定以 §7.3 的 cohort survivor disposition 與該 round 的 `verdict.json` 為準。
- `[C]` 現行 production 計算面就是 Qlib container（§3/§9）；full-backtest 的完成與可宣稱性只依本 contract 的 Qlib evidence，不依賴任何 retired secondary engine（§9.6/§17）。
- `[C]` **production 序列是 sequential A→B→C**：A 卡完整結案（`done`，任何 verdict）後才進 B，B 完整結案後才進 C；同一時刻只有一張 active strategy card（§7）。
- `[C]` 多 family 一次全 universe 混跑**不是** production 模式，也不得作為 production 效能證據；若要探索，必須是獨立研究卡，並在該 family 的 `family.json` 的 `lineage_note` 載明其非 production 身分。
- `[C]` **family gate（v1.3.0；v1.4.0 調整對映）＝ cohort survivor disposition**（細節 §7.3）：0 個 cohort survivor → `REJECT / NO_SURVIVOR`；**>=1 個即通過基本研究 gate**（恰 1 個 → `SURVIVOR_FOUND` band，>1 個 → `MULTIPLE_SURVIVORS` band；verdict 皆為 `PASS`，且**全部 survivors 都保留並進入下游，不做排序／淘汰／二選一**）；coverage/技術不完整 → `TECHNICAL_INCOMPLETE`（且不做任何 cohort 判定）。
- `[C]` **跨 20 個 cohort 的 median PnL / Sharpe 不得再作為 family 的 REJECT gate**：v1.2.0（含）以前的跨 timeframe median 門檻已廢除，只允許作為 descriptive diagnostic，並必須在 artifacts 中明確標示 `non_gating`。完整搜尋的意義是「每個 cohort 都被獨立判定」，不是「所有 timeframe 都要通過」。
- `[C]` `insufficient trades` / `no-signal` 只淘汰**該 cohort**，不影響其他 cohort，也不使 family 直接失敗。
- `[C]` survivor 證據必須逐 cohort 列出：symbol/timeframe、strategy params、DCA params、historical / OOS / full metrics、robustness（四個 stress grid）、parameter-neighbourhood 結果。落點為 attempt `artifacts/cohort_results.json` 與 `artifacts/cohort_survivors.json`，摘要寫入 `result.json`。

### 7.3 Cohort-level survivor semantics（v1.3.0；selector/disposition 版本化）

- `[C]` 每個 cohort 的判定必須由**預先註冊且版本化**的 selector 與 disposition 執行；版本字串為 `cohort-selector-v1` / `cohort-disposition-v1`，必須出現在 run-spec、`family.json` 的 `fingerprint_input` 與 round-spec 中。版本變更＝語意變更＝新 round（或新 family），不得原地改寫既有 artifacts。
- `[C]` **selector（deterministic，歷史段唯一）**：只允許使用 historical 視窗的實測值選參；OOS 不得用於選參。步驟固定為：
  1. 交易量充分性：若該 cohort 在 historical 的最佳 case episodes 數 < 註冊的 `min_episodes_is`，整個 cohort 以 `insufficient_trades` 淘汰。
  2. 候選資格：historical `net_pnl > 0` **且** `sharpe > 0` **且** `episodes >= min_episodes_is`。
  3. 無候選者 → 該 cohort 以 `no_qualifying_candidate` 淘汰。
  4. 排序：Sharpe 由大而小 → net_pnl 由大而小 → **固定 lexical tie-break**（在固定軸序 `window, discount, spacing_pct, size_multiplier, breakeven_tp_pct, invalidation_pct` 上取**註冊索引**的字典序）。第一名為該 cohort winner。
  5. winner 的 `strategy params + DCA params` **整組**帶去 OOS / full / robustness / 鄰域檢定；看過 OOS 之後不得換組。
- `[C]` **cohort SURVIVOR 最低要求（全部成立）**：
  a) 存在 historical winner；
  b) OOS：同一組參數 `net_pnl > 0` 且 `sharpe > 0`；
  c) full window：同一組參數 `net_pnl > 0`；
  d) robustness：同一組參數在 `fee_2x`、`funding_2x`、`entry_delay_1_bar`、`slippage_2ticks` 四個 grid 全部 `net_pnl > 0`；
  e) parameter-neighbourhood stability：在**完整 joint 空間**（strategy domain × DCA domain）中，winner cell 的 legal 面鄰居（單一軸 ±1 步，且仍在註冊域內）至少 **60%** 與 winner 的 historical net-PnL 同號；**鄰域判定只准使用 historical**，不得使用 OOS。
- `[C]` cohort 淘汰原因必須逐項記錄（`cull_reasons`），不得只寫「失敗」。
- `[C]` **family disposition 與 verdict 對映（v1.4.0）**：0 個 survivor → `REJECT`（`performance_claimable=false`）；**>=1 個 → verdict `PASS`**（恰 1 個 → band `SURVIVOR_FOUND`；>1 個 → band `MULTIPLE_SURVIVORS`；兩者同為 `PASS`，band 只描述數量）；coverage 或技術不完整 → `TECHNICAL_INCOMPLETE`。
- `[C]` **survivor 數量 >1 不是降級**：多個 survivor 之間**不得**做人工二選一、排序或淘汰；**全部** survivor 一律保留、一律進入下游，並由 §10.8 的 frozen survivor bundle 完整凍結。survivor 數量**不得**被當成 `performance_claimable=false` 的理由（§9.6 是唯一依據）。
- `[C]` **band → verdict 對映的版本邊界（v1.4.0）**：`cohort-disposition-v1` 版本化的是 cohort 判定語意——selector 五個步驟、survivor 五項要求（a–e）與四個 disposition band；v1.4.0 未更動其中任何一項，只更動由 band 導出 verdict／可宣稱性的那張對映表，而該表屬本 contract 的版本化條文。因此 v1.4.0 **不**改寫 `cohort-disposition-v1` 的字串，也**不**回寫任何既有 artifacts；engine 於每筆輸出揭露其套用的對映版本（`contract_semantics_version`／`disposition_mapping_version`），使 v1.3.x 的 artifact 可被讀成它原本的語意。新的 round-spec 必須逐字引用 v1.4.0 的對映（見 §10.2 與 templates）。
- `[C]` selector 的歷史段限定必須是**可執行**檢查（實作上對輸入列做 `window_kind == "historical"` 守衛）；不是僅靠敘述。runner 另以「同一組資料洗牌後重選必須得到同一 cell」自我檢查決定性。
- `[C]` runner 的輸出（`disposition` / `verdict_recommendation` / `performance_claimable_recommendation`，v1.4.0 另含 `disposition_mapping_version`）是**建議**；final verdict 仍由 default 依 §10.7 寫入 `verdict.json`（與 v1.2.0 一致）。
- `[V]` v1.3.0 的 cohort selector / survivor gate 已有實作與邏輯層測試（`container/scripts/tests/test_strategy_a_engine.py`）；其 `[T]`「尚未有正式 family 實跑」已於 2026-09-13 由 Strategy A v2（family `close-vs-sma-mean-reversion-long-flat-v2`，round `…-r1` / run `…-r1-u1`，卡片 `t_1f97bf6b`）解除：20/20 cohorts、`coverage_complete=true`、103,680 case evaluations、14/14 assertions true、**2 個 cohort survivor（`BTCUSDT/1h`、`SOLUSDT/4h`）**，`artifacts/cohort_results.json` 與 `artifacts/cohort_survivors.json` 已落地（證據見 `evidence/strategy-a-v2-*.json`）。該 round 的 `verdict.json` 於 v1.3.2 語意下寫為 `FINALIST`／`performance_claimable=false`，**保持 immutable、不回寫**；依 §10.8 由既有 `artifacts/cohort_survivors.json` 生成 survivor bundle，並在 bundle 內標註：在 v1.4.0 語意下**兩個 survivors 都通過基本 gate**。

## 8. Round / Run lifecycle

```
family F
 └─ round R1  (科學迭代 1)
      ├─ attempt/run R1/u1   [VERDICT: REJECT]
      ├─ attempt/run R1/u2   [technical retry → 同 round 新 run_id]
      └─ terminal: REJECT  (或進入 R2)
 └─ round R2 ...
```

- `[C]` round_id 格式：`<family_id>-r<N>`（N 從 1 起，單調遞增，不重複使用）。
- `[C]` run_id 格式：`<round_id>-u<M>`（M 從 1 起，單調遞增，同一 round 內不重複）。
- `[C]` 觸發 **新 round** 的情境：假說/參數域/篩選門檻/資料窗有語意變更。
- `[C]` 觸發 **新 run（同 round）** 的情境：純執行失敗、基礎設施重啟、mount 掉線、OOM、腳本 bug 修好後重跑。假說與參數域不變。
- `[C]` 每次 attempt 必有 terminal evidence；失敗 attempt 也必寫 `FAILED`/`INCOMPLETE`，**不得讓卡片永久停在 `scheduled`**。
- `[C]` 一個 round 的 verdict 只能由「該 round 最後一個成功 run 的 artifacts」決定；失敗 run 只貢獻 failure taxonomy。
- `[C]` **`/results` 已有該 run 的 terminal evidence 時禁止重跑**（INV-15）。要再算只能開新 run_id（同 round）或新 round_id。

## 9. Qlib launch / handoff / sentinel / reconciler contract

### 9.1 職責
- `[C]` Qlib container：只執行計算、只寫 `/results`。不判斷 verdict、不讀 Kanban、不連網（除 image build 期）。
- `[C]` Hermes default：產生 spec、判 verdict、寫 `/results` durable artifacts（`family.json`、`round-spec.json`、`verdict.json`）。不長 turn 等 Qlib，也不寫卡片 metadata。
- `[C]` reconciler：host 端 deterministic **no-agent** 腳本。只做 `scheduled → ready`。不判 verdict、不改 body、不寫 comments 當 state。

### 9.2 Handoff 步驟（default 的固定流程）
1. `[C]` 產生 run spec，atomic publish 到 `/results/<family_id>/rounds/<round_id>/attempts/<run_id>/`（§10.2）。
2. `[C]` 執行 preflight（§16）。FAIL 則修復或 `blocked`（shared-layer 才 freeze）。
3. `[C]` 以 `container exec qlib-run` 投遞計算（單次、非互動；`nohup`/背景非必要，但不得建立長時間 host 等待 turn）。
4. `[C]` 立刻 `kanban_comment` 記錄 `run_id`、artifact 路徑、投遞時間（comment 只是可讀紀錄，不是 state；machine-readable ownership 以 `/results/<family_id>/family.json` + attempt `run-spec.json` 為準）。
5. `[C]` 呼叫 `schedule_task` 把卡片 park 到 `scheduled`；worker 合法退出。
6. `[T]` 步驟 3（`container exec` 投遞計算）仍需由 default 手動執行，尚未有腳本化包裝；步驟 4–6 所需的 terminal evidence 寫入器與 no-agent reconciler 已於 v1.1.0 落地（`runtime/terminal_evidence.py`、`runtime/reconcile.py`）。ownership 證據留在 `/results` durable artifact（`family.json`/`run-spec.json`/sentinel），卡片只留可讀 comment。
- `[C]`（v1.9.0）**compute-finished ≠ 終結**：容器 runner 於計算結束時只寫 attempt `state.json` stage=`ARTIFACT_READY`（或腳本層失敗的 `FAILED_SCRIPT`）並退出；它**不**發佈 terminal sentinel，也**不**決定 verdict。真正的 terminal sentinel（`DONE`/`FAILED`/`INCOMPLETE`，§9.3/§10.3）仍一律由 default 在 host 端發佈；`ARTIFACT_READY` **不得**被自動等同 `DONE`（`[V]` Strategy D r1 u2 即為 `ARTIFACT_READY` 但自身 assertions 有 false 的反例：它沒有 sentinel、最終由 u3 取代）。compute-finished 階段唯一的自動動作是 §9.4 v1.9.0 的 **default review wake**（喚醒 default 做 host-side 處置），不是放行 verdict、不是終結卡片。

### 9.3 Sentinel 契約
- `[C]` sentinel 檔名固定為 `DONE` / `FAILED` / `INCOMPLETE`，置於 attempt 目錄。
- `[C]` terminal publish 必須 **最後** atomic 執行：寫 `*.tmp` → `close()` + `fsync()` → 同檔案系統 `rename()` 到終名。禁跨檔案系統 rename。
- `[C]` sentinel 內容為 JSON（§10.3），至少含：`schema_version`, `family_id`, `round_id`, `run_id`, `task_id`, `kanban_board`, `status`, `verdict_hint`, `created_at_utc`, `host_boot_id`, `container_id`, `artifact_manifest`, `artifact_checksums`。
- `[C]` `verdict_hint` 只是 hint：container 不得、也不能決定 verdict；final verdict 由 default 寫入該 round 的 `verdict.json`（`[V]` 卡片沒有 metadata 可寫）。
- `[C]` spec/result 檔案一旦 publish 即 immutable（INV-4）。重算只能寫新 run_id 目錄。
- `[C]` 缺 `DONE` 但存在 `FAILED`/`INCOMPLETE` 也是合法終態 → reconciler 一律放行。

### 9.4 Reconciler 契約
- `[C]` 觸發方式：host 端 cron（no_agent，script-only）或 operator 手動；**不得**是 daemon/service/watch（INV-8）。
- `[C]` **入口 = 未消費的 terminal sentinel**：掃描 `/results/<family_id>/rounds/<round_id>/attempts/<run_id>/` 下的 `DONE`/`FAILED`/`INCOMPLETE`，從 sentinel 讀 `family_id`/`round_id`/`run_id`/`task_id`，再以 `/results/<family_id>/family.json` 與 `round-spec.json` 交叉確認 ownership。**不查、也不要求任何 task-level metadata**（`[V]` kernel 無此欄）。
- `[C]` 掃描範圍：**只**處理「sentinel 未消費」且「DB 讀回的卡狀態為 `scheduled`」的卡。未消費的定義：sentinel 的 `kanban_task_id`（= sentinel `task_id`）指向的卡當前仍是 `scheduled`；若該卡已非 `scheduled`，此 sentinel 視為已消費（consumed），不動作。
- `[C]` 放行前必須逐項驗證（全部以檔案系統 + DB 讀回為準）：
  1. `kanban_task_id`（= sentinel `task_id`）/ `family_id` / `round_id` / `run_id` mapping 一致（`family.json` ↔ `round-spec.json` ↔ attempt 目錄路徑 ↔ attempt `run-spec.json` ↔ sentinel 內容）。
  2. sentinel 為合法終態且 JSON 可解析。
  3. `artifact_manifest` 中每個 required artifact 存在、可讀、checksum 相符。
  4. `host_boot_id` / `container_id` 可對照（stale sentinel 檢測，§12.3；sentinel `container_id` 為必填，且 attempt `run-spec.json` 有載明時必須相符。與**現役**容器 identity 的比對屬 preflight P5/P6，不在 reconciler 內再引入 container 查詢）。
  5. 該 attempt 目錄不存在多個 terminal 檔（§12.3）。
- `[C]` 驗證 PASS → `unblock`（→ `ready`，或父卡未完成時自動 `todo`）。
- `[C]` 驗證 FAIL 或證據衝突（multiple terminal / checksum 不符 / sentinel 歧義 / mapping 不一致）→ **絕對不 unblock，也絕對不對 `scheduled` 卡直接 `block`**；改走 §12.6 incident（保持 `scheduled`、寫 incident artifact、告警人工介入）。
- `[C]` duplicate reconciliation 必須無害：以 sentinel 為唯一判準，重複執行不會二次 unblock（`[V]` kernel 對非 `blocked`/`scheduled` 的 unblock 回 False，天然冪等）。
- `[C]` **comments 不得當唯一 state**：reconciler 只讀 DB 欄位 + 檔案系統；人工排除以 DB 讀回為準。
- `[V]` reconciler 已以最小版落地並實測（2026-09-13）：`runtime/reconcile.py`（純 stdlib、no-agent、支援 `--dry-run`）＝ 本節驗證清單的機械化；**掃描範圍判定（consumed → 不動作）先於驗證清單**（卡片非 `scheduled` 即 no-op，不產生 incident/comment）；偵測到 terminal sentinel 後才 `unblock`，衝突一律走 §12.6、不 unblock、不 block。
- `[V]` 邏輯層檢查：`python3 runtime/tests/test_reconcile.py`（stdlib unittest，注入 kernel 讀回與 `unblock` 回應）23/23 OK，涵蓋唯一放行條件、dry-run 不變更、**consumed 判定先於驗證清單**（consumed + stale boot / checksum 衝突 / mapping 衝突一律 no-op，重跑不累加 incident 或 comment；無法解析 `task_id` 才維持 fail-closed）、以及六項 fail-closed 分支（multiple_terminal / checksum_mismatch / sentinel_ambiguous / mapping_mismatch / stale_sentinel / unblock 後讀回未確認）。
- `[V]` **執行環境限制（2026-09-13 實測）**：`unblock`/`comment` 等 board 變更走 `hermes kanban` CLI，而 Hermes 對 `HERMES_DELEGATED_CHILD_CONTEXT=1` 的 context（delegate_task 子行程、kanban worker session，以及**由該 session 建立/觸發的 cron job**）一律拒絕 board 變更。因此 reconciler 的 apply 必須在**無此標記的 host context**（operator 的一般 shell，或由該 shell 建立/啟動的 cron/服務）執行；否則 sentinel 驗證會照常通過，但放行會被 CLI 擋下 → 卡片會停在 `scheduled`（§25 禁止事項）。dry-run 不受影響，可用來確認「只剩放行這一步」。
- `[C]` **v1.7.0 職責分離：handoff cron 與 reconciler cron 是兩個彼此獨立的 no-agent job，不得合併、不得互相代理。**
  1. **handoff cron**（job `624d0be5b23c`，`5 * * * *`；入口 `~/.hermes/scripts/quant_production_handoff.py` → repo `runtime/production_handoff.py`）＝ §14.4 的 tail append（family 卡）職責。
  2. **reconciler cron**（job `f6b9aa5e9034`，`every 15m`；入口 `~/.hermes/scripts/quant_runtime_reconcile.py` → repo `runtime/reconcile.py`）＝ 本節（§9.4）的 completion bridge 職責：未消費 terminal sentinel → 既有 ownership／checksum／fail-closed 驗證 → 唯一合法動作 `scheduled → ready`。
  3. reconciler cron **只**跑既有 `runtime/reconcile.py`（repo 是唯一邏輯來源；wrapper 只提供 scheduler 端入口與輸出靜默政策：正常 no-op 完全靜默、只有**真實 unblock** 或**新 incident signature** 才輸出，避免 Discord 重複噪音；wrapper 的 state 只存上次 incident signature，`--dry-run` 不寫 state）。
  4. reconciler cron **不得**：代 handoff 建卡、auto-restart container、auto-publish orphan `INCOMPLETE`、建立 recovery daemon、做 checkpointing；亦不得改變 §9.4 的驗證清單或 fail-closed 語意（`runtime/reconcile.py` 若需改動＝ contract code change，須依 §26 版本遞增＋測試＋audit）。
  5. 兩個 job 都必須在 operator 明確放行後才 resume；v1.7.0 交付狀態為**兩者皆 paused**。**（v1.9.0 更正：以上為 v1.7.0 交付時的事實，逐字保留；後續 current state 見下方 v1.9.0 cron 現況與 §14.4 v1.9.0 條——handoff `624d0be5b23c` 已由 ChatGPT 啟用並改排程為 `5,35 * * * *`，reconciler `f6b9aa5e9034` 仍 paused，俟本版 audit PASS 後由 ChatGPT/operator resume。）**
- `[V]` v1.7.0 掛載讀回（2026-09-14，卡片 t_15fed3f2）：reconciler cron `f6b9aa5e9034`（`every 15m`、`repeat=forever`、no-agent、deliver `discord:1519163199117721650`、`enabled=false`／`state=paused`、wrapper sha256 `66d913c6fe06666229e0608d63d262e14b03d56fe9b4ae7972e2f83f095a8d78`）；`hermes cron list --all` 讀回 handoff cron `624d0be5b23c` 仍為 **paused**。wrapper 以 fence-free host context 實跑 `--dry-run` → rc=0、**stdout 0 bytes**（no-op 靜默）、未寫 state 檔。**（v1.9.0 更正：此為 v1.7.0 交付時的讀回，逐字保留；handoff cron `624d0be5b23c` 其後已啟用且排程改為 `5,35 * * * *`，reconciler `f6b9aa5e9034` 當時起的 paused 狀態延續見下方 v1.9.0 cron 現況。）**
- `[C]` **v1.7.1 authoritative current attempt（production-discovered control-plane bug 的最小修正）**：reconcile 的掃描單位是 **round**（`<family_id>/rounds/<round_id>/attempts/**`），同一 round 內**只有一個** authoritative current attempt 可以驅動 Kanban 狀態轉換：
  1. **selection**：authoritative = 該 round 內 identity 合法（attempt `run-spec.json` 的 `family_id`／`round_id`／`run_id` 與路徑逐字相等、`task_id`／`kanban_board` 為 non-empty string）且 ordering metadata 可判定的 attempt 中 `(created_at_utc, uN ordinal)` 最大者。ordering **不得**只以 run_id 字串字典序決定：`created_at_utc` 為主序，`uN` 序數只作 deterministic tie-break（數值比較，`u10` > `u9`）。單一 attempt 的 round 不需 ordering（維持既有行為）。
  2. **superseded**：同一 round 內較舊的 attempt 一律 `superseded`——terminal sentinel 仍可讀、可驗證、保留 provenance，但必須是 descriptive no-op：**不得** unblock／complete／block 卡片，也不產生 incident／comment。
  3. **fail closed**：同一 round 有多個 attempt、但較新者的 `run-spec.json`／identity／ordering metadata 缺失、不可解析或歧義（含同一 `created_at_utc` 且無 `uN` tie-break 可判定、同一 round 內 task ownership 衝突）→ 整個 round fail closed：依 §12.6 記 incident（`kind=attempt_selection_ambiguous`）、不動作，**不得**回退到較舊 attempt 的 terminal。
  4. **consumption 仍優先**：authoritative attempt 的處理與 ambiguous round 的 incident 判定都先做 consumed 檢查（本節掃描範圍）：卡片非 `scheduled` 即 no-op，不累加 incident。
  5. 這不是新的 daemon／service／DB：selection 只是 discover 之後、handle 之前的小型 round grouping，**不得**引入 current-pointer registry 或新 state machine。
- `[V]` v1.7.1 邏輯層檢查（2026-09-14）：`python3 runtime/tests/test_reconcile.py` → **38/38 OK**（原 23 檢定 ＋ 15 項 round-level 檢定：較舊 terminal 被較新 attempt supersede → no-op、`u1 FAILED + u2 DONE` 只放行 u2、單一 terminal round 仍放行、較新 attempt 缺 identity／ordering metadata 或 ownership 衝突 → fail closed 且不 fallback、`u9`/`u10` 與 timestamp tie 的數值 tie-break、無 tie-break 的 timestamp tie → fail closed、不同 round／family 不互相 supersede、dry-run 與 real mode 選同一 attempt、ambiguous round 但卡片已消費 → no-op、superseded attempt 的 terminal 衝突 → no-op、authoritative attempt 的 checksum 衝突仍 fail closed）。同一批新檢定在 v1.7.0 的 `reconcile.py` 上 **14/15 失敗**（11 FAIL ＋ 3 ERROR；3 個 ERROR 為舊 report 無 `superseded` 欄的 `KeyError`）。
- `[V]` v1.7.1 production dry-run 讀回（2026-09-14，fence-free context、read-only）：`python3 runtime/reconcile.py --dry-run --json` 對現行 `/results` → `scanned=9 incidents=0 unblocked=[] would_unblock=[]`；B v2 round `r1-u1`（FAILED）為 `superseded`（`authoritative_run_id=…-r1-u2`）、`r1-u2` 為 `orphan_candidate`（`stage=RUNNING_QLIB`），**未**對 `t_35b3e5da` 提出 unblock／complete／block。修正前同一 dry-run 為 `would_unblock=['t_35b3e5da']`（即該 production bug 的 read-only 重現）。前後 B v2 attempt tree 34 檔逐位元比對：除 u2 進行中的 `artifacts/grid_fee_2x.csv`（live writer，size 333,076 → 734,210 bytes）外全部相同（含 u1 全部檔案、u2 `run-spec.json` `b20a8251…`、`state.json` `c8465714…`、`round-spec.json` `a3dd33a9…`、`family.json` `63ee6cb8…`）。
- `[C]` **v1.9.0 compute-finished wake（full-auto completion 的 missing link；最小修補）**：authoritative current attempt 在**沒有** terminal sentinel 時，既有行為一律是描述性 `orphan_candidate`（report-only，沒有任何卡被喚醒）。實測（Strategy D r1 u2）證明 container runner 會先落 attempt `state.json` stage=`ARTIFACT_READY`（或腳本層失敗的 `FAILED_SCRIPT`）再退出，sentinel 由 default host-side 發佈（§9.2 步驟 6）；若卡片已 park `scheduled`，reconciler 過去不會喚醒 default，full-auto completion 因此缺一環。修正＝同一條 `scheduled → ready` 路徑上的一個窄入口：
  1. **觸發條件（全部成立才動作）**：authoritative current attempt（v1.7.1）；attempt 目錄**無** terminal sentinel；attempt `run-spec.json` 可解析且 `task_id`／`kanban_board` 為 non-empty string；DB 讀回該卡 status == `scheduled`；`state.json` 可解析且 `stage` ∈ {`ARTIFACT_READY`, `FAILED_SCRIPT`}。
  2. **唯一合法動作**：**既有** `hermes kanban unblock`（`scheduled → ready`；父卡未完成時 kernel 落 `todo`）＝ 喚醒 default 做 host-side 處置。**不得**寫 `DONE`/`FAILED`/`INCOMPLETE`、不得判 verdict、不得寫或改 `/results` 任何 artifact（含 `verdict.json`／survivor bundle／attempt 內容）、不得建立 incident（除非既有 ownership／read-back 本身不成立）、不得啟動新 run、不得建卡或代 handoff 動作。
  3. **compute-finished ≠ terminal**：`ARTIFACT_READY` 只代表計算階段停止；default 醒來後仍自行決定 terminalize／retry／`INCOMPLETE`（§12.2），terminal sentinel 仍由 default host-side 發佈（§9.3）。`FAILED_SCRIPT` 同樣只喚醒，不是 terminal。
  4. **fail closed（行為不變）**：`RUNNING_*`、無 `state.json`、`stage` 不可解析、較舊（`superseded`）attempt、卡片非 `scheduled`、run-spec identity 取不到、card read-back 失敗 → 一律維持既有描述性 `orphan_candidate`（不 wake、不 incident、不改卡）。authoritative current attempt selection、§9.4 既有九項驗證清單、checksum／sentinel／fail-closed 路徑**全部不動**。
  5. `--dry-run` 對這條路徑只回 `would_unblock`：不改 board、不寫 incident、不寫 state。
- `[V]` v1.9.0 邏輯層檢查（2026-09-15）：`python3 runtime/tests/test_reconcile.py` → **46/46 OK**（原 38 ＋ 8 項：`ARTIFACT_READY`＋`scheduled` → dry-run `would_unblock` 與 real `unblock`（且證明不寫 terminal）、`FAILED_SCRIPT`＋`scheduled` → wake、`RUNNING_QLIB` 仍 `orphan_candidate`、`stage` 不可解析仍 report-only、非 `scheduled` completion-pending 不動作、superseded completion-pending 不喚醒、run-spec identity 缺失／card read-back 失敗 → fail-closed）。同一批新檢定對 **v1.8.0 的 `reconcile.py` bytes**（`git show <v1.8.0 commit>:runtime/reconcile.py`，sha256 `1e15d321…`）重跑 → **2 FAIL ＋ 3 ERROR**（5 項紅；其餘 3 項是「行為不變」的守衛檢定，依設計在兩版皆綠）。`runtime` 全部 16 個測試檔 → **320/320 OK**。
- `[V]` v1.9.0 production 讀回（2026-09-15，fence-free context、read-only）：`python3 runtime/reconcile.py --dry-run --json` 對現行 `/results` 在修正前後**逐列相同**（`attempts_scanned=16`、`incidents=0`、`unblocked=[]`、`would_unblock=[]`）——現行樹上每個 round 的 authoritative attempt 不是已有 terminal（consumed／superseded）就是 `RUNNING_QLIB`，故**無誤喚醒**；Strategy D `…-r1-u2`（`ARTIFACT_READY`、無 sentinel、卡片 `t_50c28da5` 已 `done`）仍為 `orphan_candidate`（`detail.wake = "card status=done is not scheduled -> no wake"`）。另以**真實** attempt bytes（該 u2 的 `state.json`／`run-spec.json` 複本 ＋ 注入式 board 讀回）驗證：status=`scheduled` → `would_unblock=['t_50c28da5']`（dry-run、0 次 `sh()`、0 incident、0 board 變更）；status=`done` → `orphan_candidate`。
- `[V]` v1.9.0 cron 現況（2026-09-15，`hermes cron list --all` 讀回）：handoff job `624d0be5b23c`（「Quant production handoff (A→B→C)」）＝ **active**、排程 **`5,35 * * * *`**（由 ChatGPT 改；v1.2.0 交付時為 `5 * * * *`）、no-agent、deliver `discord:1519163199117721650`、last run 2026-09-15T16:05 ok；reconciler job `f6b9aa5e9034`（「Quant runtime reconciler (completion bridge, paused until audit PASS)」）＝ **paused**（`every 15m`），俟本版 audit PASS 後**由 ChatGPT/operator resume**。**（2026-09-16 更正：v1.9.0 audit PASS 後本 job 已 resume；同日 `hermes cron list --all` 讀回 handoff `624d0be5b23c`／reconciler `f6b9aa5e9034`／watchdog `c5314d86cdfe` 皆 active。上列 paused 為 2026-09-15 交付當日讀回，逐字保留。）**
- `[C]`（v1.9.0）**reconciler cron 的復活時機與 worker 邊界**：`f6b9aa5e9034` 在本修補 audit PASS 前**不得** resume（本版只交付修補與證據，不啟停任何 cron）；worker 卡內**不得**操作 cron（不得 `hermes cron run`／`edit`／`enable`／`resume`），cron 的啟停一律由 ChatGPT/operator 決定。

- `[C]` **v2.0 direct C4 override（AUDITED PASS / LIVE）**：固定 n8n C4 action 仍呼叫
  `runtime/reconcile.py`；它只處理 `family.json.handoff.execution=direct_hermes`，歷史
  card-owned family 不改動。每 round 只讓 `(created_at_utc, uN)` 最新、identity 合法的
  attempt 驅動：較舊者 `superseded`；歧義、foreign identity、多 sentinel 或 checksum 衝突
  fail-closed 並記 canonical incident。`state.json.stage` 為 `ARTIFACT_READY`／
  `FAILED_SCRIPT` 且無 sentinel、有合法 terminal 但該 round 無 verdict、或 attempt 已超過
  90 分鐘 stall window（與 handoff／watchdog 同一窗口）未再寫入且仍無 sentinel 與該 round
  verdict（agent 未啟動 Qlib 即死，或 Qlib 中途死亡）時，僅以 family-scoped lease 啟動
  detached Hermes default 一次做 host-side disposition；它不自動判 PASS、不寫 sentinel/verdict、
  不建卡、不 `unblock`。stall window 內的（新鮮）attempt 一律維持描述性 `orphan_candidate`
  且不喚醒；喚醒失敗改記 fail-closed incident `disposition_launch_failed`（wrapper 以
  `family=`／`run=` 報出）而非靜默重試。有該 round verdict 時即 consumed。
  `--dry-run` 只回報 `would_launch`，不取得 lease、不寫檔、不啟動 agent。

### 9.5 為何不用 HTTP / webhook / Redis / Celery / queue
- `[C]` 這些都需要常駐服務或網路信任面，會引入：新 daemon、新 failure mode、新 secret、新 port、新 restart 邏輯；而本 pipeline 的 completion 訊號本質是一個「至少一次、可重讀」的檔案事件。
- `[C]` 檔案 + 冪等 unblock 已滿足需求，且符合最小設計原則。任何以此為由的擴張提案都應被駁回。

### 9.6 performance_claimable 條件（全部成立才 true）
- `[C]` verdict = `PASS`；round 的 spec 事先註冊且未被事後修改；使用 canonical raw（非合成/非替代資料）；無 look-ahead（PIT 檢查通過）；樣本外或明確 out-of-sample 區間；成本/滑價假設已載明；且 §7.2 的 eligible universe 覆蓋（symbols × timeframes × parameter domain × DCA execution × historical/OOS/robustness）已依 round artifacts 完成。
- `[C]` 上列條件齊備即可 `performance_claimable=true`。Lean／Nautilus／PyBroker 等 retired secondary engines（§17）不參與此判定；不得因未執行 retired engine validation 而把現行 Qlib full-backtest 結論降級為 `research-only`。
- `[C]` 未同時滿足者，`performance_claimable=false`，並在該 round 的 `verdict.json` 的 `missing_conditions` 註明缺哪一項。`missing_conditions` 不得以「缺下游 acceptance」為理由。
- `[C]` **本節是 `performance_claimable` 的唯一依據（v1.4.0 明文化）**：verdict == `PASS` 在 >=1 cohort survivor 時成立（§7.3），因此當 family 有 1 個、2 個或更多 survivor 時，剩下的 9.6 條件相同——都是資料／材料條件（canonical raw、無 look-ahead、明確 OOS、成本假設已載明、§7.2 覆蓋完整）。**survivor 數量本身既不是必要條件也不是拒絕理由**；`missing_conditions` 不得出現「survivor 多於一個」這種項目。

## 10. Artifact schemas

### 10.1 目錄佈局（canonical）

```
/results/<family_id>/
  family.json                                  # immutable：ownership + lineage（見 §10.6）
  state.json                                  # atomic rewrite 唯一允許
  rounds/<round_id>/
    round-spec.json                           # immutable：預先註冊的門檻/參數域/falsification + kanban_task_id
    verdict.json                              # immutable：該 round 的 verdict / performance_claimable / yield 判定（見 §10.7）
    survivor-bundle.json                      # immutable（v1.4.0）：該 round 全部 survivors 的凍結 bundle（見 §10.8）
    attempts/<run_id>/
      run-spec.json                           # immutable：本 attempt 的輸入契約
      result.json                             # immutable：計算結果摘要
      artifacts/…                             # immutable：tables/plots/中間彙總（可多檔）
      DONE | FAILED | INCOMPLETE              # immutable terminal sentinel（atomic publish，最後）
      logs/                                   # 允許非必要、可重跑產生的 log
      state.json                              # atomic rewrite：本 attempt 的進度 stage

/results/_survivors/                          # v1.5.0 post-survivor lifecycle（§27）：全部 derived/append-only
  survivor-index.json                         # derived/rebuildable：由 frozen bundles 重建（§27.2）
  leaderboard.json / leaderboard.csv          # derived/rebuildable：index + forward evidence（§27.5）
  forward/<survivor_id>.jsonl                 # append-only：post-freeze forward slices（§27.3）
```

- `[C]` `_survivors`（與既有 `_incidents`／`_handoff`）為保留命名空間，永不作為 `<family_id>`（§27.1）。

- `[C]` `<family_id>`、`<round_id>`、`<run_id>` 必須與 durable ownership artifacts（`family.json`、`round-spec.json`、attempt `run-spec.json`、sentinel）完全一致（字串相等），不做寬鬆比對、不做大小寫正規化。**卡片本身沒有、也不得要求 metadata。**
- `[C]` ownership key 統一語意為 `kanban_task_id`：在 `family.json` 與 `round-spec.json` 以此鍵名出現；在 `run-spec.json` / sentinel 沿用既有鍵名 `task_id`。**兩者為同一欄位**（同值，一律字串相等；不一致即 fail-closed）。
- `[C]` 命名對照：ChatGPT 卡片 t_bcedaf65 以 `spec.json` / `DONE.json` 簡寫指涉本節的 round `round-spec.json`、attempt `run-spec.json` 與各 terminal sentinel（無副檔名的 `DONE`/`FAILED`/`INCOMPLETE`）；**檔名以本節為準**，不採用兩套命名。
- `[C]` `state.json` 是**唯一**允許 atomic rewrite 的檔案；其餘一律 immutable（含 `family.json`、`round-spec.json`、`verdict.json`）。
- `[C]` `logs/` 不得承載唯一證據：任何結論都必須能從 immutable 檔重現。
- `[T]` 此佈局為拍板設計；尚未有實際 family 目錄落地（現存 `/results/qlib-smoke-20260912/…` 為 smoke 產物，屬舊命名，不作為 schema 範例）。

### 10.2 `run-spec.json`（最小欄位）

```json
{
  "schema_version": 1,
  "family_id": "close-vs-sma-mean-reversion-long-flat-v1",
  "round_id": "<family_id>-r1",
  "run_id": "<round_id>-u1",
  "task_id": "t_XXXXXXXX",
  "kanban_board": "quant-strategy-research",
  "created_at_utc": "2026-09-12T12:00:00Z",
  "data": {"source": "/data/raw", "read_only": true,
           "instruments": ["BTCUSDT"], "start": "2024-01-01", "end": "2024-04-01", "freq": "60min"},
  "params": {"grid_windows": [], "grid_discounts": [], "grid": ["本 run 的 strategy 參數域（完整乘積）"]},
  "dca_domain": {"base_quote": 1000, "spacing_pct": [], "size_multiplier": [],
                 "breakeven_tp_pct": [], "invalidation_pct": [],
                 "grid": ["本 run 的 DCA 參數域（完整乘積，逐組實算）"]},
  "selector_version": "cohort-selector-v1",
  "disposition_version": "cohort-disposition-v1",
  "expected": {"cohorts": 0, "strategy_cases_per_cohort": 0, "dca_configs_per_cohort": 0,
               "base_combinations_per_cohort": 0, "case_evaluations_per_grid": 0,
               "expected_case_evaluations": 0},
  "costs": {"fee_bps": null, "slippage_bps": null},
  "script": {"path": "/scripts/…", "sha256": "…"},
  "expected_outputs": ["result.json", "artifacts/cohort_results.json",
                       "artifacts/cohort_survivors.json", "artifacts/summary.csv"],
  "falsification": ["…預先註冊的否證條件…"],
  "notes": "…"
}
```

- `[C]` `script.sha256` 必填：保證「同一 spec 指向同一份程式」。
- `[C]` `falsification` 必須在計算前寫定（預先註冊）；事後補寫視為無效。
- `[C]` `dca_domain`（v1.3.0 必填）：四個 DCA 軸與其完整乘積 `grid`；`grid` 必須**逐項等於**四個軸的笛卡兒乘積（不得少跑、不得事後增刪），否則 coverage 不完整 → `TECHNICAL_INCOMPLETE`（§7.2）。
- `[C]` `dca_domain` 的 provenance status（v1.3.1 必填）：`base_quote_status` 與（當該軸被搜尋時）`size_multiplier_status` 必須逐字承載 round-spec 的分類字串開頭 token（`PROJECT_PRE_REGISTERED_CONSTANT` / `PROJECT_PRE_REGISTERED_SEARCH_DOMAIN`）；run-spec 與 round-spec 的分類不一致即為 pre-registration 口徑衝突（§7.2 v1.3.1 provenance classification）。
- `[C]` `selector_version` / `disposition_version`（v1.3.0 必填）：與 round-spec 及 `family.json` 的 `fingerprint_input` 完全一致；runner 只接受自己實作的那個版本，不一致即 fail-closed（不啟動計算）。
- `[C]` `expected`（v1.3.0 必填）：精確期望覆蓋數必須在 pre-registration 中算出（例：20 cohorts × 12 strategy × 48 DCA = 11,520 base combinations per phase grid；× 9 phase grids = 103,680 case evaluations）。runner 會以測得的 `coverage` 對照此欄位；不相等即視為 technical incomplete，且不得事後改寫期望值。

### 10.3 terminal sentinel（`DONE` / `FAILED` / `INCOMPLETE`）

```json
{
  "schema_version": 1,
  "status": "DONE",
  "family_id": "…", "round_id": "…", "run_id": "…",
  "task_id": "…", "kanban_board": "quant-strategy-research",
  "created_at_utc": "…",
  "host_boot_id": "…",
  "container_id": "qlib-run",
  "image_id": "qlib:0.9.7-arm64",
  "qlib_version": "0.9.7",
  "verdict_hint": "NONE|CANDIDATE_PASS|CANDIDATE_REJECT|INCOMPLETE",
  "failure": {"layer": "card-local|shared-layer|null", "class": "…", "detail": "…"},
  "artifact_manifest": ["result.json", "artifacts/summary.csv"],
  "artifact_checksums": {"result.json": "sha256:…", "artifacts/summary.csv": "sha256:…"},
  "runtime_seconds": 1234
}
```

- `[C]` `status` ∈ {`DONE`,`FAILED`,`INCOMPLETE`}，且必須與檔名一致。
- `[C]` `artifact_manifest` 只列 required artifacts（存在性是放行條件）；`logs/` 不列入。
- `[C]` `failure.layer` 只在 `FAILED`/`INCOMPLETE` 為非 null。
- `[C]` `verdict_hint` 只作提示，final verdict 由 default 寫入該 round 的 `verdict.json`（卡片無 metadata）。

### 10.4 Checksum / 完整性
- `[C]` checksum 一律 `sha256`，以 `sha256:<hex>` 表達。
- `[C]` reconciler 必須對 manifest 內每一項重算 checksum（`shasum -a 256`）。任一不符 → 不放行。
- `[C]` sentinel 檔案本身必須最後寫入；存在 manifest 但 sentinel 缺失 → 視為 **partial write**（§12.2）。
- `[V]` 自動 checksum 產生器已以最小版落地並實測（2026-09-13）：`runtime/terminal_evidence.py publish` 會對 `--manifest` 內每個檔案算 `sha256:` 後寫入 sentinel 的 `artifact_checksums`，`check` 子命令可獨立重算核對。

### 10.5 `state.json`（atomic rewrite）
```json
{"schema_version": 1, "family_id": "…", "round_id": "…", "run_id": "…",
 "stage": "RUNNING_QLIB", "updated_at_utc": "…",
 "reconcile": {"last_check_utc": "…", "result": "PASS|FAIL", "reason": null},
 "note": "…"}
```
- `[C]` 任何寫入都必須 tmp+fsync+rename；不得就地 append，不得部分覆寫。
- `[C]` `state.json` 遺失可重建（由 sentinel + DB 讀回重建），因此它不是真值來源。

### 10.6 `family.json`（ownership + lineage，immutable）

```json
{
  "schema_version": 1,
  "family_id": "close-vs-sma-mean-reversion-long-flat-v1",
  "kanban_task_id": "t_XXXXXXXX",
  "kanban_board": "quant-strategy-research",
  "semantic_fingerprint": "sha256:…",
  "fingerprint_input": "<正規化後的輸入字串>",
  "parent_family": null,
  "lineage_note": "…",
  "created_at_utc": "2026-09-12T12:00:00Z"
}
```

- `[C]` 這是 family 的 ownership 與 lineage **唯一 authoritative 記錄**（不是卡片 metadata，也不是 Registry service）。去重時掃描 `/results/*/family.json` 即為 fingerprint 集合。
- `[C]` `kanban_task_id` 必填，必須指向本 board 上該張 strategy 卡；最遲在該卡被 dispatcher claim（進入 `running`）前落地（§14.2 步驟 7）；未落地前不得投遞 Qlib。
- `[C]` 檔案 immutable：fingerprint 或 lineage 若要變更 → 開新 family（新卡、新 fingerprint），不得改寫既有 `family.json`。

### 10.7 `verdict.json`（round 判定，immutable）

```json
{
  "schema_version": 1,
  "family_id": "…", "round_id": "…", "run_id": "…",
  "kanban_task_id": "…",
  "verdict": "PASS|REJECT|FINALIST|DEFERRED|TECHNICAL_INCOMPLETE",
  "performance_claimable": false,
  "missing_conditions": ["…"],
  "yield": {"rounds_used": 2, "max_rounds": 3, "no_progress_rounds": 0,
            "progress_evidence": ["…"],
            "yield_decision": "CONTINUE|STOP_REJECT|STOP_DEFERRED|STOP_TECHNICAL_INCOMPLETE|FINALIST"},
  "failure": {"layer": "card-local|shared-layer|null", "class": "…"},
  "evidence_run_ids": ["…"],
  "decided_at_utc": "…"
}
```

- `[C]` 由 default 讀回 artifacts 後寫入，immutable（INV-4）。family 終結以**最後一個 round** 的 `verdict.json` 為準。
- `[C]` 這裡是 verdict / `performance_claimable` / yield 判定的唯一 machine-readable 記錄；卡片與 comment 只放人類摘要。

### 10.8 `survivor-bundle.json`（round 的 frozen survivor bundle，v1.4.0 起；identity recipe 於 v1.4.1 明確定義；重跑比對範圍於 v1.4.2 逐鍵收緊；writer 版本字串於 v1.5.0 更新，語意不變）

```json
{
  "schema_version": 1,
  "kind": "frozen_survivor_bundle",
  "contract": "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md v1.5.0",
  "contract_section": "7.3 / 10.8",
  "family_id": "…", "round_id": "…", "run_id": "…",
  "kanban_task_id": "…", "kanban_board": "…",
  "selector_version": "cohort-selector-v1", "disposition_version": "cohort-disposition-v1",
  "source_attempt_dir": "…",
  "survivor_count": 2,
  "disposition_band": "MULTIPLE_SURVIVORS",
  "verdict": "PASS",
  "all_survivors_advance": true,
  "ranking": null,
  "note": "…v1.4.0：>=1 cohort survivor 即通過基本研究 gate；全部 survivors 完整凍結並前進，順序為 run 記錄順序、不是排名…",
  "source_attempt_semantics": {"verdict_mapping": "v1.4.0 | pre-v1.4.0（legacy 揭露）", "…": "…"},
  "source_verdict_not_rewritten": true,
  "survivors": ["該 round 全部 survivor 記錄，逐筆照 run 原始內容與順序（含 winner／metrics／neighbourhood）"],
  "source_artifacts": {"artifacts/cohort_survivors.json": "sha256:…", "result.json": "sha256:…", "DONE": "sha256:…"},
  "generator": {"path": "runtime/survivor_bundle.py", "sha256": "…"},
  "bundle_identity_sha256": "sha256:…",
  "generated_at_utc": "…"
}
```

- `[C]` 落點：`rounds/<round_id>/survivor-bundle.json`，immutable。**唯一產生者**是 host 端、純 stdlib、deterministic 的 `runtime/survivor_bundle.py`（與 `terminal_evidence.py` 同層級的 artifact 產生器，**不是** service／daemon／queue，也不進 container）。container 不寫本檔。
- `[C]` **完整性**：bundle 必須包含**該 round 全部** survivor，逐筆照 `artifacts/cohort_survivors.json` 的內容與順序。**不得**排序、排名、篩選、淘汰或二選一；`ranking` 固定為 `null`、`all_survivors_advance` 固定為 `true`。survivor 數量／順序與 `result.json`、`artifacts/cohort_results.json` 不一致即 fail-closed 拒寫。
- `[C]` **產生條件**：只允許從 terminal `DONE` 的 attempt 產生，且該 attempt 的 `coverage_complete=true`、`assertions.json` 全為 true、`case_evaluations_total == expected_case_evaluations`；否則拒寫（coverage 不完整的量測不得被凍結）。
- `[C]` **冪等與不可改寫**：bundle 一旦寫入即不得改寫。重跑同一 attempt → 量測內容與 `bundle_identity_sha256` 相同時為 no-op；不同時拒寫（INV-4/§11）。
- `[C]` **canonical identity recipe（v1.4.1 明確定義；auditor 必須能不經本 repo 程式碼獨立重算）**：`bundle_identity_sha256` 的雜湊輸入是**移除 `generated_at_utc` 與 `bundle_identity_sha256` 這兩欄之後**的 bundle 物件，序列化為 canonical JSON（`json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`，UTF-8 編碼），輸出為 `"sha256:" + sha256(canonical).hexdigest()`。**identity 欄位本身永不得進入自己的雜湊輸入**（自我遞迴無解）；除上述兩欄外**不得**排除任何欄位，`generated_at_utc` 是產出時間、不是量測內容。同一 recipe 對「尚未帶 identity 欄位的重建物件」與「已持久化、已帶 identity 欄位的檔案」必然得到同一值，因此 auditor 可對持久化檔案獨立重算並與檔案內公開值逐位元比對（4 行純 stdlib，存檔後 `python3 <file> <bundle>` 執行即可，不需 import `runtime/survivor_bundle.py`、不需信任本 repo 的 writer 程式碼）：
  ```
  import hashlib, json, sys
  d = json.load(open(sys.argv[1]))
  b = {k: v for k, v in d.items() if k not in ("generated_at_utc", "bundle_identity_sha256")}
  print("sha256:" + hashlib.sha256(json.dumps(b, sort_keys=True, separators=(",", ":"),
                                             ensure_ascii=False).encode()).hexdigest())
  ```
- `[C]` **重跑比對的範圍（v1.4.1 引入；v1.4.2 逐鍵收緊措辭與實作）**：`--check` 與 writer 的「量測內容」比較，**只**額外排除兩個**產生者身分**欄位：頂層 `contract`（writer 所依的契約版本字串）與巢狀 `generator.sha256`（writer 自身 bytes）。排除**逐鍵**生效，**不是逐物件**：`generator` 內除 `sha256` 以外的所有欄位（含 `generator.path`、即 writer 的來源路徑）與其他所有頂層欄位一律納入比對；非 dict 的 `generator` 必須照原樣納入比對，**不得**被正規化掉或整包排除。理由是這兩個欄位在 writer 被修正時必然改變（修正 identity recipe 就會改變 writer 自身 hash），若納入比對，則任何 remediation 都必然讓既有 frozen bundle 的 `--check` 失敗，等於被迫改寫 immutable artifact。兩欄仍逐位元留在 frozen 檔案內，且仍被公開 `bundle_identity_sha256` 涵蓋（見上一條），產生當時的 provenance 不會被靜默改寫；其餘欄位（含 `note`、`generator.path` 與全部 `source_artifacts` checksum）一律納入比對——包含整個 `generator` 物件在內的任何更寬排除，都會讓「改 `generator.path` 並把公開 identity 重算成自洽值」這種 tamper 被 false accept（v1.4.1 的實作缺陷 F2，auditor t_3edafbb9）。
- `[C]` **legacy 揭露**：若來源 attempt 是在 v1.4.0 之前產生（>1 survivor 的 `verdict_recommendation=FINALIST` 且 `performance_claimable_recommendation=false`），bundle 必須在 `source_attempt_semantics` 逐字揭露該對映，並同時給出 v1.4.0 的 `verdict=PASS`；此揭露**不**改寫來源 artifact，也**不**改寫該 round 已凍結的 `verdict.json`。
- `[C]` **auditor 可重現**：`python3 runtime/survivor_bundle.py --attempt-dir <attempt> --check` 必須 rc=0，且必須同時成立兩件事：(1) 已持久化檔案公開的 `bundle_identity_sha256` 等於上一條 recipe 對**該檔案自身**的重算值（公開值與內容不一致 → 拒、rc=1），(2) 重讀該 attempt 的 immutable artifacts 得到同一量測內容（不符 → 拒、rc=1）。`--json` 輸出必須回報**檔案實際公開**的 `bundle_identity_sha256`、由檔案重算的 `identity_recomputed_from_persisted_file`，以及 `identity_recipe_matches`；`source_artifacts` 的每個 checksum 必須可由 auditor 以唯讀命令重算。

## 11. Idempotency

- `[C]` 每個寫入動作都必須可安全重複執行：
  - `[V]` Kanban `unblock` 對非 `blocked`/`scheduled` 的卡回 False（無副作用）→ 重複 reconcile 天然無害。
  - `[C]` artifact 寫入以「目標不存在才建立」為原則（`O_EXCL` 或等價檢查）；已存在且 checksum 相同 → no-op；已存在但 checksum 不同 → **不覆寫**，改立新 run_id。
- `[C]` 冪等鍵（semantic fingerprint）用於 NEW_FAMILY 去重（§14.3），不得用於覆寫既有 round/run。
- `[C]` 同一 run_id 只能有一個 worker/容器程序擁有它。以 sentinel 的存在性作為「已 terminal」判準，不以 PID 為準。
- `[C]` 「先檢查、後寫入」必須容忍競態：發現終名已存在 → 放棄本次寫入並採用既有檔（log 記 `idempotent_skip`）。

## 12. Reboot / orphan recovery

### 12.1 恢復兩個平面
- `[C]` 一次恢復必須同時處理：
  - **控制面**：Kanban 卡片狀態（是否 `scheduled` 卻已終結、是否有 dangling run）。
  - **執行面**：container/mount/image/volume 是否回到可執行狀態。
- `[C]` 只恢復控制面 = 未完成恢復。`[V]` Mac reboot、container restart、PID loss 後 PID 不可信；必須改用 sentinel / state.json / container identity / boot identity / heartbeat 作為最小可靠證據。
- `[C]` 恢復順序：環境識別（boot id）→ mount 檢查 → container 檢查 → orphan 判定 → 卡片對帳 → （必要時）preflight（§16）→ 才允許新 run。

### 12.2 Orphan 判定（權威順序）
1. `[C]` attempt 目錄有 terminal sentinel → 該 run 已終結；若卡片仍 `scheduled`，走 reconciler 放行（§9.4）。`[C]`（v1.9.0）attempt 目錄**無** sentinel 但 `state.json` stage ∈ {`ARTIFACT_READY`,`FAILED_SCRIPT`} 且卡片仍 `scheduled` → 這不是 orphan：compute 已結束、只缺 host-side sentinel，走 §9.4 v1.9.0 的 **default review wake**（喚醒 default 決定 terminalize／retry／`INCOMPLETE`）；本節其餘 orphan 判定（第 2–4 項）與處置完全不變。
2. `[C]` 無 sentinel、`state.json.stage=RUNNING_QLIB`、但 container 不存在或已重啟（container/boot identity 不符）→ run 為 **orphan**。
3. `[C]` orphan 處置：不得假裝成功。標 `INCOMPLETE` sentinel（由 default/host 以 `failure.class=orphaned_run` 補寫），再放行卡片；是否重跑由 default 決定 → 同 round 新 run_id。
4. `[C]` 無 sentinel、無 state、目錄只有 `run-spec.json` → 投遞未完成（`orphaned_submission`）→ 同 round 新 run_id 重新投遞。
5. `[C]` 已存在 terminal sentinel → **禁止重跑**（INV-15）。

### 12.3 Stale / 衝突 sentinel 檢測
- `[C]` sentinel 的 `family_id`/`round_id`/`run_id`/`task_id` 與 `/results` ownership artifacts（`family.json`、`round-spec.json`、run-spec）不一致 → stale：忽略、不得用來放行任何卡；若該卡仍是 `scheduled`，另走 §12.6 incident。
- `[C]` `host_boot_id` 與當前 boot 不符且 sentinel 是本次宣稱的產物 → 視為不可信，需 default 人工核對 artifact 內容後才放行。
- `[C]` 同一 attempt 出現**多個** terminal 檔（例如 `DONE` 與 `FAILED` 並存）→ fail-closed：不放行、不改卡狀態，保留雙檔為證據，走 §12.6 incident（**不得**因此對 `scheduled` 卡直接 `block`）。
- `[C]` checksum 不符、manifest 檔案缺失、sentinel JSON 不可解析 → 同樣 fail-closed 走 §12.6 incident：不 unblock、不 block、不猜測。

### 12.4 Invariant-break reconciliation
- `[C]` 觀測到下列任一情形即為 invariant 破壞：卡片 `scheduled` 但 `/results` 查無對應 attempt 目錄或無 `run-spec.json`（ownership artifact 缺失）；`stage=RUNNING_QLIB` 但卡片非 `scheduled`；`scheduled` 卡的 current_run 已 ended；同一卡出現兩個 active run。
- `[C]` 處置：不猜、不自動修，且**不得對 `scheduled` 卡直接 `block`**。先 `kanban_comment` 記錄完整證據（task_id、observed status、run_id、artifact 路徑、boot id），再走 §9.4 驗證清單；仍不確定 → §12.6 incident（fail-closed）。

### 12.5 Shared-layer freeze gate
- `[C]` shared-layer failure 定義（系統性、影響範圍**不只**單一 work volume）：`/Volumes/ExpansionDrive` 未掛載；`market-data-raw` 不可讀；`qlib-results` 不可寫；Apple Container runtime 不可用；`qlib-run` 無法安全啟動；image/runtime identity 不符；Qlib import/version 不符；或故障證據顯示 Apple Container runtime / shared filesystem subsystem / host storage layer 系統性異常（同一 storage 層上多個 volume/mount 同時異常）。
- `[C]` **單一 work volume 不得升級**：`/qlib/work` 是 rebuildable derived/cache/work area（INV-5）。單純 volume 缺失 / 不可寫 / 損毀 = card-local execution-plane failure：preflight（§16 P7）可安全重建/修復後**同 round 新 run_id** 續跑，**不得** freeze 任何無關 family。只有當故障證據顯示 shared filesystem / storage subsystem 系統性異常（影響範圍超出該單一 work volume）時，才升級 shared-layer freeze。
- `[C]` shared-layer failure → **freeze 全鏈**：把所有 `ready` 的 strategy 卡留在 `todo` 或 `blocked(kind=capability)`，並在 chain tail 留一張 `blocked` 的 shared-layer 卡記錄事件。不得讓 worker 反覆重試打爆 dispatcher。
- `[C]` freeze 時若 chain head 卡仍在 `scheduled`：不得直接 `block`（§6.3）；由 operator/default 依 §12.6 的顯式 two-step（先 `unblock` → 再 `block`）處理。
- `[C]` card-local failure（單卡參數/資料窗/腳本/work volume 問題）→ **不 freeze**：僅該卡 `TECHNICAL_INCOMPLETE` 或新 run 重試，後續 family 照常 append tail。
- `[C]` freeze 解除條件：shared-layer 失敗根因消除 + preflight（§16）全綠 + 獨立讀回驗證。解除後以 `unblock` 放行，不得用 promote（scheduled 不受理，且 promote 會繞過語意）。
- `[C]` freeze 期間**不得**新增 family（避免堆積無法執行的卡）；已存在的 tail append 之後在解除後自然放行。

### 12.6 Conflict / incident procedure（fail-closed，唯一合法路徑）

- `[C]` 適用情境：同一 attempt 多個 terminal evidence 並存、checksum 不符、sentinel 歧義（JSON 不可解析，或 `status` 與檔名不符）、ownership mapping 不一致、stale sentinel 指向仍為 `scheduled` 的卡、§12.4 invariant 破壞、§7.1 多張 chain head。
- `[C]` 固定動作（三步，順序不得變）：
  1. **保持卡片原狀**：該卡是 `scheduled` 就保持 `scheduled`；不 unblock、不 `promote`、不 `block`、不改 body。`[V]` kernel `block_task` 對 `scheduled` 回 False，所以「對 scheduled 卡直接 block」本來就不可執行。
  2. **寫 host-side incident artifact（append-only）**：`/results/_incidents/reconciliation_incident.jsonl`，每行一筆 JSON，至少含 `schema_version`、`incident_id`、`detected_at_utc`、`detector`（`reconciler|default|operator`）、`kanban_task_id`、`family_id`、`round_id`、`run_id`、`observed_status`、`kind`（`multiple_terminal|checksum_mismatch|sentinel_ambiguous|mapping_mismatch|stale_sentinel|invariant_break|duplicate_chain_head|attempt_selection_ambiguous`）、`evidence_paths`、`host_boot_id`。不得改寫既有行；`/results/_incidents/` 為保留目錄，不得作為 family_id。
  3. **告警 + 要求人工介入**：在卡片留一則 comment（開頭 `incident:`）指向該 artifact；層級判定（card-local vs shared-layer）由 operator/default 事後決定。
- `[C]` 若人工判定確實需要把該卡轉為 `blocked`：必須由 operator/default 在**後續顯式流程**先 `unblock`（`scheduled`→`ready`/`todo`）再 `block`，兩步之間各自留下 DB 讀回證據。**此 two-step 不得包成 reconciler 自動動作**（避免競態與繞過 gate）。
- `[C]` incident 未結案前：該 family 不得投遞新 run，也不得 append 新 family。
- `[C]` 這不是新服務/daemon：incident artifact 只是檔案契約，偵測者是既有的 reconciler/default。
- `[C]` 現行 prelaunch blocker 可由嚴格驗證的零計算 `TECHNICAL_INCOMPLETE` terminal（`run_id=null`，且 round/verdict artifacts 證明零 attempts）依既有語意解決；此例外不放寬一般 attempt-backed verdict identity。
- `[C]`（v1.7.1）`attempt_selection_ambiguous` 是 §9.4 的 round-level fail-closed kind：同一 round 內較新 attempt 的 identity／ordering metadata 缺失、不可解析或歧義（含同一 `created_at_utc` 且無 `uN` tie-break 可判定、同一 round task ownership 衝突），使 authoritative current attempt 無法判定時使用。處置同本節三步（保持卡片原狀、寫 append-only incident artifact、comment 指向 artifact），且**不得**以同 round 較舊 attempt 的 terminal 放行。

## 13. Failure taxonomy

| class | layer | 例 | 卡片處置 | 卡片 verdict |
|---|---|---|---|---|
| `submission_failed` | card-local | container exec 立刻失敗、腳本不存在 | 同 round 新 run_id 重試 | 未終結 |
| `script_bug` | card-local | Python exception、欄位名錯 | 修腳本 → 同 round 新 run_id | 未終結 |
| `data_window_invalid` | card-local | 區間內無 bar、instrument 缺失 | 新 round（若窗改）或新 run（若僅參數） | REJECT / DEFERRED |
| `oom` | card-local | container 記憶體上限（4 GB） | 縮窗/縮參數域 → 同 round 新 run_id | TECHNICAL_INCOMPLETE（若無法縮） |
| `timeout` | card-local | 超過研究預算時長 | 同 round 新 run_id（縮域） | TECHNICAL_INCOMPLETE（若重複） |
| `orphaned_run` | card-local | container 重啟、PID 遺失 | 補 `INCOMPLETE` → 新 run_id | TECHNICAL_INCOMPLETE（若不再重試） |
| `orphaned_submission` | card-local | 投遞後未開始 | 重新投遞（新 run_id） | 未終結 |
| `partial_write` | card-local | manifest 有檔但 sentinel 缺、或 sentinel 半寫 | 視為未終結 → 新 run_id | TECHNICAL_INCOMPLETE（若重複） |
| `checksum_mismatch` | card-local（單檔）／shared-layer（系統性） | manifest checksum 不符 | fail-closed：不放行、不改卡狀態，走 §12.6 incident 後由 default 判層級 | 待定 |
| `mount_missing` | shared-layer | ExpansionDrive 未掛載 | **freeze** | — |
| `raw_unreadable` | shared-layer | raw 不可讀/被改寫 | **freeze** + 立即上報 | — |
| `results_unwritable` | shared-layer | `/results` 不可寫 | **freeze** | — |
| `runtime_down` | shared-layer | Apple Container runtime 不可用、container 無法安全 start | **freeze** | — |
| `identity_mismatch` | shared-layer | image/runtime/qlib version 不符 | **freeze** | — |
| `work_volume_broken` | card-local（execution plane；可重建） | `/qlib/work` 缺失/不可寫/損毀 | 重建 volume（preflight P7）→ 同 round 新 run_id；**不 freeze**（除非證據顯示 storage subsystem 系統性異常，§12.5） | 未終結 |
| `operator_stopped` | card-local | operator 在任何 verdict 產生前中止該 round（例：語意升級後 superseded） | 保留全部 artifacts；以 host 端 `runtime/terminal_evidence.py` 對未終結的 open attempt 補發 `INCOMPLETE`（§12.2 同法），使該 attempt 永久 terminal（INV-15）；卡片保持 `blocked`/superseded，**不**產生科學 verdict | 無（superseded；不得回寫成 PASS/REJECT） |

- `[C]` 判定層級的判準：**「同一動作在另一張卡上是否也會失敗？」** 會 → shared-layer；只在此卡 → card-local。
- `[C]` **`operator_stopped` 的 archive hygiene（v1.3.1）**：被 operator 中止的 strategy 之 exact runner 與其測試**不得**留在 active runtime 路徑（`container/scripts/**`、host `/scripts` 部署目錄）。它們必須原封不動（exact bytes）移到明確標示為 archive-only 的 evidence 路徑（例：`evidence/strategy-b-operator-stopped/runtime/**`），並在 stop record 記錄原 SHA-256 與 archive path；host 部署副本同步移除。理由：`container/scripts/` 與 `/scripts` 是現行 canonical runtime 的表述，把已停止的 runner 留在那裡會讓 operator-stopped 的實作看起來仍是在役 production implementation。**不得**改動 `/results` 內既有的 immutable artifacts。
- `[C]` 分類必須寫進 sentinel `failure.class` 與該 round 的 `verdict.json` 的 `failure`，不得只寫在 comment（卡片不承載 metadata）。

## 14. NEW_FAMILY：intake / tail append algorithm

### 14.1 觸發
- `[C]` chain head 進入 terminal（`done`，任何 verdict）或 `FINALIST` 之後，才允許 append 下一個 family。
- `[C]` NEW_FAMILY **永遠 append tail**：以當前 tail card 作為唯一 parent。不得插隊、不得 live rewiring、不得改既有 parent edge（INV-11）。

### 14.2 Tail append 演算法（default 執行，deterministic）

```
1. 讀回 board：找出被 `/results/<family_id>/family.json` 的 `kanban_task_id` 反查命中的 strategy 卡，依 created_at 排序，取得 tail_id。
2. 驗證 tail 狀態 ∈ {done, archived}；否則停止（不 append）。
3. 計算 semantic fingerprint（§14.3）；掃描 `/results/*/family.json` 取得既有 fingerprint 集合（純檔案契約；不建立 Registry service、不讀卡片 metadata）。
4. fingerprint 已存在 → 不建立新卡；回報 duplicate 並要求 operator 決策。
5. 建立新卡：kanban_create(title=…, assignee="default", parents=[tail_id],
                            skills=[…], completion_contract="local-only")
6. 讀回新卡，確認 parents=[tail_id]、status=todo。
7. 以 `kanban_task_id` = 新卡 id 寫入 `/results/<family_id>/family.json`（immutable；含 `semantic_fingerprint`、`fingerprint_input`、`parent_family`、`lineage_note`）。`kanban_create` 可能讓卡立即變 `ready`，故本步必須在同一輪內立即完成；此檔最遲須在該卡被 claim 前落地。寫入失敗 → 走 §12.6 incident，不得投遞 Qlib。
8. 不得在同一輪同時 append 兩張卡。
```

- `[C]` 步驟 1/2/6 必須是 DB 讀回（`kanban_show`），不得只採信 create 回傳值；步驟 7 必須以檔案讀回確認 `family.json` 落地。
- `[C]` 若步驟 5 意外建立重複卡：不刪卡（刪卡會破壞審計）；待該卡為 `ready` 後以 `blocked(kind=needs_input, reason=duplicate family)` 上報。`[V]` kernel 的 `block_task` 對 `todo` 不受理，故卡停在 `todo` 時只能留 comment 交 operator 處理，不得用 promote 硬推。

### 14.3 Semantic fingerprint（防爆量）
- `[C]` fingerprint = `sha256` of 正規化後的：
  `family_signal_family | 參數域線性化（排序、去重、以 ',' 串接） | 資料區間 | 頻率 | 方向/持倉語意`。
- `[C]` 正規化規則：去除空白、統一大小寫、參數域排序；**不做語意同義詞合併**（保守：寧可判為不同 family 也不誤判相同）。
- `[C]` fingerprint 存於 `/results/<family_id>/family.json` 的 `semantic_fingerprint`（連同 `fingerprint_input`）；同一 board 內不得重複。查核方式為掃描 `/results/*/family.json`。
- `[C]` 同一 family 的不同 round 共用同一 fingerprint（round 不進 fingerprint）。
- `[T]` fingerprint 計算尚未腳本化；落地前由 default 手算，並把正規化後的輸入字串寫入 `family.json` 的 `fingerprint_input`，供 auditor 唯讀重算比對。
- `[C]` `family.json` 是檔案契約，**不是**新的 Registry service（§1.2 禁止抽象層）：去重靠掃描檔案，不靠常駐索引。
- `[C]` **v1.3.0 追加**：`fingerprint_input` 必須額外包含 (a) **DCA parameter domain**（四個 DCA 軸的值域，軸名字典序、值域以 `,` 串接）、(b) **eligible universe**（排序後的 symbol 清單）、(c) **selector/disposition 版本**（`selector=<cohort-selector-v1>;disposition=<cohort-disposition-v1>`）。理由：DCA 域或 disposition 語意一改就是另一個 family（§7.2/§7.3），舊 fingerprint 無法表達該差異。
- `[C]` 既有（v1.2.0 及以前）`family.json` 的 `fingerprint_input` 與 `semantic_fingerprint` 一律 **grandfather**：immutable、不重算、不回填（INV-4）。v1.3.0 只約束**新建立**的 family；同一 board 內不得出現兩個不同 `family_id` 卻相同 `semantic_fingerprint` 的紀錄。

### 14.3.1 C3 current cutover ownership（2026-09-23）
- `[C]` **current scheduler ownership**：C3.1 已完成獨立 audit 與 activation，n8n workflow `productionHandoffManualC2` 目前 live cadence 為 `5,20,35,50 * * * *`（`:05/:20/:35/:50`），狀態 **AUDITED PASS / LIVE**。workflow 只有 Manual Trigger、Schedule Trigger 與既有 host-bridge action 三個功能節點，兩條 trigger 都接同一固定 `production_handoff_once` request/response；2026-09-23 23:05 Asia/Taipei execution 112 已 `trigger/success`。
- `[C]` **runtime ownership 不變**：n8n 只負責 cadence 與既有 `ai.quant.n8n-host-bridge` 的編排；`runtime/production_handoff.py` 仍是 canonical 判定、append、`family.json` 與 finding source of truth，n8n 不取代 Hermes default、Kanban kernel、Qlib 或 reconciler/watchdog。
- `[C]` **rollback**：Hermes handoff cron `624d0be5b23c` 保持 **paused**，只作 rollback path；本 C3 implementation card 不啟動 n8n、不操作 cron。
- `[C]` **HOLD on unavailable**：Hermes／Kanban read-back unavailable 時，handoff 只回報 finding，**不建卡、不寫 family.json**；該 cadence 視為 **HOLD**，下一個 `:05`／`:20`／`:35`／`:50` tick 自然重試。不得為此新增 `PAUSED` state、health daemon、retry queue、watcher 或 preflight node；既有 active-family gate 防止重複。
- `[C]` **maintenance SOP**：HOLD transition → 乾淨停止 n8n／checkpoint／integrity check／known-good DB snapshot → reboot/update → login／既有 `ai.quant.recover-gate` 與 `ai.quant.n8n-host-bridge` LaunchAgent 復原 → readiness／Shadow／dry-run smoke → continue；不新增 recovery service。

### 14.3.2 C4 runtime reconciler cadence（2026-09-23）
- `[C]` **implementation state**：C4 已完成獨立 audit 並 **AUDITED PASS / LIVE**。repo workflow `runtimeReconcilerC4` 的 display name 為 `Quant Control Plane — Runtime Reconciler`，export `active=false`；功能節點恰為 Manual Trigger、Schedule Trigger 與一顆 `runtime_reconcile_once` host-bridge action。
- `[C]` **cadence**：目前 live cadence 為 `6,21,36,51 * * * *`，沿用既有 container timezone（Asia/Taipei），避開 C3 handoff 的目標 `:05`／`:20`／`:35`／`:50`；C4 automatic trigger 已於 2026-09-23 22:06 Asia/Taipei 成功觀測（execution 103、mode `trigger`、status `success`），前一拍為 C3 execution 102（22:05）。Manual 與 Schedule 都接同一 action。C4 不接入 `shadowQuantCp1` 或 Full Canvas mutating path。
- `[C]` **bridge contract**：bridge 維持既有固定 request／response mailbox、`quant-control-action/v1` 的恰三鍵 schema、atomic claim／response publish、request correlation、最小 `HOME`＋`PATH` 環境與 600 秒 process-group timeout；唯一新增的是固定 token `runtime_reconcile_once` → `[/opt/homebrew/bin/python3, /Users/hong/.hermes/scripts/quant_runtime_reconcile.py]`。`production_handoff_once` 的既有 command mapping 與 C3 workflow bytes 不變；未知第三 action 仍 fail-closed 且不產生 host action。
- `[C]` **ownership / no overengineering**：n8n 只提供 cadence 與既有 bridge 編排；`runtime/reconcile.py`、Hermes default、Kanban 與 Qlib 仍是 execution／decision owners。C4 不新增 mailbox、queue、DB、service、daemon、retry queue、state machine 或 runtime semantics；watchdog `c5314d86cdfe` 保持 active 且獨立，Full Canvas 保持 read-only。
- `[C]` **rollback**：C4 已完成 live cutover 且保持 active；Hermes reconciler cron `f6b9aa5e9034` 保持 **paused**，只作 rollback path。manual action 與 automatic execution 103（2026-09-23 22:06 Asia/Taipei、mode `trigger`、status `success`，前一拍 C3 execution 102 at 22:05）均已驗證；任何失敗均停用 C4 並恢復 Hermes cron。watchdog `c5314d86cdfe` 保持 active 且獨立。

### 14.4 Automatic handoff trigger（v1.2.0）

> **現行語意（2026-09-24，Kanban-free decision；卡片 `t_6c6a3286`；independent auditor run 222 PASS / LIVE）**：下方 v1.2.0 條文中凡涉及 **board 讀回／卡片狀態**者（append 條件 2／3／4／6、`fenced_context`／`board_unreadable`／`family_card_missing`／`blocked_card_present`／`no_tail_card`／`tail_not_terminal` 這幾個 finding kind、`--parent` 與建立後 `show` 讀回）**皆已由 Kanban-free 語意取代**：canonical advance 只由 `/results` artifacts 判定。逐條對照見本節末的 v-next 條目。

- `[C]` **ownership**：automatic handoff 由 Hermes **default** 擁有，執行者是 host 端 deterministic 的**單一 no-agent script-only cron**（不是 daemon/service/watch，INV-8/§1.2 不變）。每一輪只做「檢查 → 必要時 append 1 張 → 結束」：不做長 turn wait、不跑回測、不判 verdict、不改 body、不建 auditor 卡。
- `[C]` **入口**：repo `runtime/production_handoff.py`（純 stdlib，支援 `--dry-run`、`--json`）；scheduler 端入口 `~/.hermes/scripts/quant_production_handoff.py` 只以 `runpy` 呼叫 repo 版本（repo 是唯一 source of truth，不複製邏輯）。stdout 語意：只有「真的 append」或「去重後的新 finding」才輸出，正常 no-op 完全靜默；exit 0 = ok、2 = usage error、其他 = 真正異常（由 cron 依其既有語意告警）。
- `[C]` **append 條件（全部成立才 append；順序即判定順序）**：1. `/results` 存在；2. board 可讀（DB 讀回）；3. **無任何 active strategy card**（`ready`/`running`/`scheduled` 的 chain head）；4. board 上**無 `blocked` 卡**（freeze / human gate，§12.5/§12.6）；5. `_incidents/reconciliation_incident.jsonl` **無未結案 incident**（其卡仍非 terminal，§12.6）；6. 存在 strategy card（由 `/results/*/family.json` 的 `kanban_task_id` 反查命中）且依 `created_at` 排序的 **tail 為 `done`/`archived`**（§14.1）；7. pool 內存在尚未出現於 `/results` 的候選（family 目錄不存在且 `semantic_fingerprint` 不在既有集合，§14.3）。
- `[C]` **候選來源 = reviewed pool**：`/results/_handoff/candidates.json`（保留目錄 `_handoff`，與 `_incidents` 同層）。每個 entry 至少含 `family_id`、`title`、`fingerprint_input`（正規化字串，供 fingerprint 重算）、`card_body` 或 `card_body_file`、`lineage_note`、`parent_family`、`provenance.reviewed_source`。**pool 是檔案契約、不是 Registry service**：消費狀態一律由掃描 `/results` 推導，handoff 不重寫 pool；同一 pool 內重複 `family_id`／fingerprint 即 `ambiguous_pool`（fail-closed）。候選必須來自**已 review** 的來源（Hermes wiki brain 已 review 紀錄 / 已 review 的 research intake / board archived research evidence）；未 review 的 intake 不得直接進 pool。pool 由 default 在 research/review 產出時追加；空或無可用候選 → 只留 finding，不建卡。
- `[C]` **v1.3.0 candidate card requirements**：handoff append 的 card body 必須是**完整 v1.3.0 卡片規格**，至少含 (a) 明確的 `DCA PARAMETER DOMAIN`（四個 DCA 軸的值域與其完整乘積，逐組實算）、(b) 明確的 `COHORT SURVIVOR SEMANTICS`（cohort selector、survivor 五項要求、family disposition 對映，且載明 `selector/disposition` 版本）、(c) eligible universe 的來源與語意、(d) 資料窗與 historical/OOS 切分。執行時的 eligible universe 依 §6.4 固定為 canonical local raw 中可合法計算核心 signal 的完整可用集合，不沿用前一張卡結果，也不得事後依績效挑選；source market／source symbol breadth 不構成第二道 suitability gate。`runtime/production_handoff.py` 對**選中的**候選做兩個 marker 的 case-insensitive 檢查，缺少即 fail-closed 為 finding `candidate_body_not_v13`（不建卡、不寫 `family.json`）。理由是 §7.2/§7.3：一份 v1.2.0 語意的 body（單一 DCA rail 或跨 cohort median gate）無法表達 v1.3.0 的 full backtest。
- `[C]` 既有 pool（v1.3.0 之前寫入）的 body 一律視為**尚未通過 v1.3.0 candidate requirements**：`/results/_handoff/candidates.json` 中第一個未被消費的候選若仍是舊 body，handoff 會停在 `candidate_body_not_v13`，而**不會** append。pool 的重新 author 屬 default 的研究工作，必須在 append 前完成；不得為了讓 automation 動起來而放寬檢查。
- `[C]` **append 動作**：`hermes kanban create`（`--parent <tail_id>`、`--assignee default`、`--priority 100`、`--workspace dir:<repo>`、`--completion-contract local-only`、`--goal`、`--idempotency-key <family_id>`），**同一輪最多 1 張**；建立後立即 `show` 讀回確認 `parents` 含 tail，再以 `O_EXCL` 寫 `/results/<family_id>/family.json`（§10.6；`semantic_fingerprint` 由 `fingerprint_input` 重算）並讀回驗證。`idempotency_key` 使 crash 後重跑收斂到同一張卡（不重複建卡）。
- `[C]` **失敗語意（fail-closed，不爆量重試）**：任何一步不成立一律**不建卡**，只在 `/results/_handoff/handoff_log.jsonl`（append-only）留一筆 finding；`finding_key` 未變則不再輸出（去重），kind 改變才再告警。kind 至少含 `results_root_missing`、`fenced_context`、`board_unreadable`、`family_card_missing`、`blocked_card_present`、`unresolved_incident`、`no_tail_card`、`tail_not_terminal`、`pool_missing`、`pool_invalid`、`ambiguous_pool`、`no_eligible_candidate`、`candidate_body_not_v13`、`create_failed`、`readback_failed`、`family_json_failed`。卡已建立但 `family.json` 寫入失敗時**不刪卡**，由 finding 要求人工介入；該卡 claim 後仍必須先讀回 `family.json`，缺失即依 §7.1/§12.6 fail-closed。
- `[C]` **不改既有語意**：本節不新增 Manager/Service/Factory/Registry/Orchestrator、daemon、queue 或第二套 runtime；不改 §7/§9.4/§11/§12/§14.1–14.3/§15 的規則，也不改 §14.2 的 tail append 演算法、fingerprint 規則與 `family.json` rules。
- `[C]` **auditor 不是 production stage**：每張正式 strategy card 的流程只有 Hermes default（執行）+ §9.4 reconciler（放行）+ 本節 handoff（接續下一張）。**不新增 auditor 子卡、不把 auditor 列為任何 strategy card 的階段或 gate**；auditor 只在 operator/ChatGPT 另行開卡時獨立稽核。
- `[C]` **fence**：append 必須在無 `HERMES_DELEGATED_CHILD_CONTEXT` 的 host context 執行（§9.4；`hermes kanban` 對該標記一律拒絕 board 變更）。cron 由 gateway 直接執行腳本，故不需、也不得為此新增服務。若被誤在 fenced context 執行（例如從 kanban worker session 手動 `hermes cron run`），board 讀回會失敗，此時 finding kind 一律記為 **`fenced_context`**（不是 `board_unreadable`）：語意是「這次 invocation 跑在錯的 context」，不是 board 故障；不得把它當成 shared-layer 事件或 freeze 依據。
- `[V]` 2026-09-13 邏輯層與 dry-run 實測：`python3 runtime/tests/test_production_handoff.py` 22/22 OK（v1.2.0 當時）；v1.3.0 追加 candidate body 檢查後為 **24/24 OK**（同檔、同注入式 kernel 讀回／create，另含 `candidate_body_not_v13` 的 fail-closed 與 v1.3 body 放行）；`python3 runtime/production_handoff.py --dry-run --json` → `would_append`（tail = `t_97208408`，v1.2.0 當時）。
- `[V]` 2026-09-13 第一次真實 handoff（A 已 `done(REJECT)`）：由 fence-free host context 執行 → append family `ema-crossover-walkforward-momentum-long-short-v1` 為卡 `t_3e696dce`（`parents=[t_97208408]`、`ready`、`idempotency_key` = family_id），`family.json` 同輪落地並讀回；再跑一輪 → `noop`（active strategy card），無重複卡；dispatcher 於同分鐘自動 claim（`ready` → `running`），即自動 append 的卡不需人工 promote 就進入 production 執行。
- `[V]` 2026-09-13 cron 掛載（default profile）：job `624d0be5b23c`「Quant production handoff (A→B→C)」、`5 * * * *`、`no-agent`（script only）、deliver `discord:1519163199117721650`；scheduler 端入口 `~/.hermes/scripts/quant_production_handoff.py`。scheduler 端實跑一次（`hermes cron run`）→ `ok`／`execution completed`，證明 gateway 能執行該入口；但該次為 **in-process 手動觸發**（由 kanban worker session 發出），因此繼承了 session 的 `HERMES_DELEGATED_CHILD_CONTEXT`，board 讀回被拒 → 依上條記為 `fenced_context`（非 `board_unreadable`，非 board 故障），並在 `/results/_handoff/handoff_log.jsonl` 留下可稽核的一筆。fence 機制依 kernel 原始碼：`delegated_child_subprocess_env()` 只對「delegated child 或持有 `HERMES_KANBAN_TASK` 的行程」的子行程加標記，故 gateway tick（host 服務、兩者皆非）產生的腳本子行程為 fence-free。
- `[T]` 尚未在「tail 已 terminal 且 pool 有候選」的真實狀態下由 cron tick 自動 append 一次（首次 append 是 operator/default 手動觸發的 one-shot）。v1.3.0 現況：B（`t_3e696dce`）已被 operator 在**任何 verdict 產生前**中止並保持 `blocked`（§13 `operator_stopped`），因此 §14.4 的 append gate 一律停在 `blocked_card_present`；cron 亦依 operator 決定保持 **paused**（job `624d0be5b23c`）。**且**在 v1.3.0 之後，pool 內未被消費的候選若 body 仍是 v1.2.0 語意，會停在 `candidate_body_not_v13`——所以此路徑的真實驗收需要先完成 (a) pool 重新 author 為 v1.3.0 body、(b) B 卡的顯式 operator 處置（archive 或續留 blocked）、(c) cron 重新啟用。
- `[V]` **v1.7.0 pool / board 現況（2026-09-14，卡片 t_15fed3f2）**：(a) pool 已 re-author——B v1 歷史 entry **逐字保留**（consumed，永不重建；body 逐位元未改）、新增 `ema-crossover-walkforward-momentum-long-short-v2`（第一順位未消費候選，完整現行語意 body）、C/D/E body 重寫為現行語意並依 §14.3 重算 `fingerprint_input`（含 DCA 四軸、symbols、selector/disposition 版本）；pool 內無重複 `family_id`／fingerprint（前後 sha256 與重算值見 `evidence/v1.7.0-production-recovery-20260914.json`）；(b) operator 處置完成——B v1 卡 `t_3e696dce`（operator-stopped、無 verdict）與 DEFER flake 卡 `t_720406f2`（不記 done、不修）均已**封存為 `archived`**，active blocked count = 0，兩者於 archived board 可追溯；(c) fence-free `python3 runtime/production_handoff.py --dry-run --json` → `would_append` family `ema-crossover-walkforward-momentum-long-short-v2` at tail `t_1f97bf6b`（`families_scanned=3`、`strategy_cards=3`、**無** blocked-card finding；非 C、非 B v1）；(d) 隔離 fixture 序列（真實 pool ＋ 真實 body，temp results root ＋ 注入式 board，0 張真卡）實測 `B v2 → C → D → E → no_eligible_candidate`；(e) cron 重新啟用**尚未**執行——handoff `624d0be5b23c` 與新 reconciler `f6b9aa5e9034` 都維持 **paused**，俟 auditor PASS 後由 ChatGPT 決定。**（v1.9.0 更正：以上 (e) 為 v1.7.0 交付時的現況，逐字保留；後續 current state 見本節 v1.9.0 條——handoff `624d0be5b23c` 已由 ChatGPT 啟用且排程改為 `5,35 * * * *`（last run 2026-09-15T16:05 ok），reconciler `f6b9aa5e9034` 仍 paused、俟 §9.4 v1.9.0 修補 audit PASS 後由 ChatGPT/operator resume。）**
- `[V]` **v1.9.0 cron 現況（2026-09-15，`hermes cron list --all` 讀回；卡片 `t_2b8c076c`）**：handoff job `624d0be5b23c`（「Quant production handoff (A→B→C)」）＝ **active**、排程 **`5,35 * * * *`**（由 ChatGPT 改；v1.2.0 交付時為 `5 * * * *` 且 paused）、no-agent（script only）、deliver `discord:1519163199117721650`、last run 2026-09-15T16:05 ok；scheduler 端入口 `~/.hermes/scripts/quant_production_handoff.py`（`runpy` → repo `runtime/production_handoff.py`，repo 仍是唯一邏輯來源）。reconciler job `f6b9aa5e9034` 仍 **paused**（見 §9.4 v1.9.0）。**（2026-09-16 更正：v1.9.0 audit PASS 後本 job 已 resume；同日 `hermes cron list --all` 讀回本 job active、排程 `every 15m`，watchdog `c5314d86cdfe` 亦 active。上列 paused 為 2026-09-15 交付當日讀回，逐字保留。）**本節的 append 演算法、fingerprint 規則、pool 契約、fail-closed findings 與「同一輪最多 1 張」皆**未**變更。
- `[C]`（v1.9.0）**append 的觸發語意（白話摘要，不取代上方條件 1–7）**：handoff 只在「**上一張（tail）卡已是 terminal**（`done`／`archived`）**且** board 上**無** active strategy card（`ready`／`running`／`scheduled`）、**無** `blocked` 卡、`/results/_incidents/reconciliation_incident.jsonl` **無**未結案 incident、且 pool 內仍有未消費候選」時，才 append **下一張** family 卡；任一條件不成立即不建卡，只留去重 finding（正常 no-op 完全靜默）。`:05` 與 `:35` 兩個 tick 只是**更頻繁地檢查**——不是「每 tick 都 append」，也不是把 append 週期縮短一半。
- `[C]`（**v2.0.0，2026-09-24，卡片 `t_6c6a3286`；AUDITED PASS / LIVE**）**Kanban-free decision——production advance 不再依賴 Kanban；Hermes default 為 direct worker**。理由：live 事故中 `blocked` 的 `t_35111c45`（blocked 但從未 launch、且已有 terminal `verdict.json`）讓 §14.4 的 append gate 永久停在 `blocked_card_present`，production 因此實質凍結；「卡片狀態」不是 runtime 真值，卻成了 canonical production mutation 的必要條件。
  1. **決策證據 = `/results` artifacts only**：`*/family.json`（既有 family 集合與 fingerprint）、`rounds/*/verdict.json`（contract-terminal verdict）、`rounds/*/attempts/*`（terminal sentinel 與檔案 activity）、`_handoff/candidates.json`（reviewed pool）、`_incidents/reconciliation_incident.jsonl`、`_handoff/handoff_log.jsonl`。`runtime/production_handoff.py` **不再有任何 `hermes kanban list|show` 呼叫**；「board 可讀」不再是前置條件，`board_unreadable`／`fenced_context`／`family_card_missing`／`blocked_card_present`／`no_tail_card`／`tail_not_terminal` 六個 finding kind 隨之退場（歷史 log 行保留、可追溯）。卡片狀態（`blocked`／`stale`／讀不到）**零 gate 效力**，既不 freeze 也不改寫 advance。
  2. **advance 動作**：`hermes kanban create`（`--assignee default`、`--priority`、`--workspace dir:<repo>`、`--completion-contract local-only`、`--goal`、`--idempotency-key <family_id>`）**不再帶 `--parent`**（parent edge 會讓 blocked／stale 卡經 dispatcher gating 反過來凍結新卡；順序改由 runtime guard 保證），**不做建立後 `show` 讀回**（讀回不是決策證據）。`/results/<family_id>/family.json` 仍以 `O_EXCL` 寫入並讀回驗證（§10.6，欄位與 fingerprint 規則不變；`kanban_task_id` 為派送所得的 work-order id，派送失敗時為 `null`）。
  3. **runtime guard（客觀 artifact 證據，兩個窗都沿用 watchdog 既有的 90 分鐘 stall 窗，無新 state store／state machine）**：(a) family 的**最新 attempt 在 90 分鐘窗內且尚未發佈任何 terminal sentinel**（`DONE`／`FAILED`／`INCOMPLETE` 任一檔案）＝compute 真的在跑 → 本輪 `noop`（`outcome=running`），不重複 launch；(a-1) **`verdict.json` 是 per-round（§7.3／§9.4），verdict 不得短路 active attempt 守門**：最新 attempt 在窗內且尚無 terminal sentinel 時，**較早輪次的 terminal verdict 不得釋放該 family**（多輪 follow-up 是 live 常態）；最新 attempt 已發佈 terminal sentinel 但**該 attempt 自己所屬 round** 尚無 terminal verdict 時，family 仍欠 pipeline 這個 round → 同樣 `noop`。釋放判定讀取的是 `rounds/<round>/verdict.json`（`<round>` ＝該 attempt 的 `parents[1]`），並以與 family 級同一套 ownership 規則重新驗證：`family_id` 相符、`kanban_task_id` 兩側皆有值時須相符、`round_id` 若存在須等於該 round 目錄名、token 須為 contract-terminal；malformed／foreign（含 `round_id` 指向別的 round）／partial 一律視為**不存在**（fail-closed，該 round 視為未判定）；family 級 token 只保留給「最新 attempt 非 live」的收束路徑與 incident ledger；(b) family 已註冊但**尚無任何 runtime evidence 且註冊未滿 90 分鐘**＝launch in flight → 本輪等待（避免與剛派送的 worker 競速）；(c) 逾窗未動的 attempt（stale）或逾窗未 launch 的註冊＝不具凍結效力，pipeline 照常前進（不會永久卡在廢棄 attempt）；「family 已結案」＝最新 attempt **非 live**（已發佈 terminal sentinel，或 stale／不存在）**且**已有 terminal verdict，只有此時 terminal verdict 才收束 family（stale attempt ＋ terminal verdict 仍照常 advance）；(d) incident ledger 仍有**未解** incident（其 family 無 terminal verdict **且** 其 attempt 無 clean terminal sentinel；sentinel 解析失敗／status 不符／identity 不符皆視為**不乾淨**、fail-closed）→ finding `unresolved_incident`（`outcome=incident`）、不 advance。
  4. **outcome 詞彙（C3／C4 可區分 invocation 成功與 pipeline 結果，不需新基礎設施）**：每輪 stderr 固定一行 `production handoff: outcome=<advanced|running|idle|finding|incident> action=<…> — <reason>`；`--json` 的 record 亦帶 `outcome`。exit code 語意不變（0 = invocation 成功；2 = usage error），`advanced` 才寫 log、finding 仍去重。
  5. **pool／fingerprint／v1.3.0 candidate body／`_incidents` fail-closed／「同一輪最多 1 張」／不新增 Manager/Service/Factory/Registry/Orchestrator／不新增 daemon/queue**：全部不變。`family.json` 缺漏時的 §7.1／§12.6 fail-closed 亦不變。
  6. **`candidate_snapshot.py` 的 `Current` 同步改為同一套 runtime-evidence 選取**（共用 `production_handoff.runtime_state`，不是第二套計算）：只有「最新 attempt 在窗內且（尚未發佈 terminal sentinel **或**該 round 尚無 terminal verdict）」的 family 才算 current——因此 r1 已判定、r2 正在寫的 follow-up round 仍是 current；**未 launch／stale／已結案（terminal verdict ＋ terminal sentinel）的 family 一律不再被當成 current**，無 active work 時明確輸出 **idle**（`current.state=idle`、`family_id=null`），funnel counts 仍由 `/results` 推導。
- `[V]`（**v-next 驗證，2026-09-24，卡片 `t_6c6a3286`**）：`python3 -m unittest discover -s runtime/tests -t runtime/tests` → **518/518 OK**（`test_production_handoff.py` **39 檢定**：blocked 卡零效力、fresh attempt 擋重複 launch（含「較早輪次已有 terminal verdict 的 live follow-up round」）、stale attempt 不凍結、launch grace、terminal verdict 只在最新 attempt 非 live 時放行、incident fail-closed／terminal 證據解除、`--parent` 不存在、dispatch 失敗不回滾 advance、outcome token 五態；`test_candidate_snapshot.py` **47 檢定**：idle／running／stale／terminal／verdict 後 follow-up round 選取與 payload `current.state`）；live 唯讀 dry-run（真實 `/results`，53 families）→ `action=would_append`、`outcome=advanced`、`families_in_flight=0`、`family_id=crypto-dynamic-weight-amm-tfmm-dutch-reverse-auction-rebalancing-2026-09-01`（同一狀態在舊語意下停在 `blocked_card_present`）；incident ledger 329 行全部以 canonical 證據判為已解（0 open）；`candidate_snapshot.py` 同狀態輸出 `Current: idle (no active runtime work)`。**未**建任何真卡、**未**動 Kanban、**未**動 `/results`（dry-run 與唯讀列舉）、**未**改 cron（C3 cadence 仍 `:05/:20/:35/:50`）。
- `[V]`（**v-next 複驗（round-1 review 修復後），2026-09-24，卡片 `t_6c6a3286`**）：round-1 獨立複審指出單一 blocking defect——`runtime_state()` 在讀 attempt 之前先 `if verdict: return`，使「較早輪次已有 terminal verdict、最新輪次仍在寫」的 family 被誤判為已結案（live `/results` 中 17 個「有 verdict 且有 attempt」的 family 有 5 個屬此形狀）。修復＝改為 **attempt 先判**：`in_flight = 窗內 and（尚未發佈 terminal sentinel **或** 該 round 尚無 terminal verdict）`，terminal verdict 只收束「最新 attempt 非 live」的 family。複驗：runtime 全套 **518/518 OK**（`test_production_handoff.py` 35 → **39 檢定**、`test_candidate_snapshot.py` 46 → **47**）；temp-root ＋ 注入式 fake kanban 的 6 個 probe 全過（reviewer 的 defect fixture：r1 verdict `PASS` ＋ 窗內無 sentinel 的 r2 attempt → `noop`／`outcome=running`、0 張 create、無新 `family.json`；same-round verdict ＋ live attempt → `noop`；verdict ＋ 已發佈 `DONE` sentinel 的 attempt → `advance`；stale attempt ＋ verdict → `advance`；已發佈 sentinel 但無 round verdict → `noop`；blocked-card 形狀且無 runtime evidence → `advance`）；live 唯讀 dry-run（真實 `/results`，53 families）→ `action=would_append`、`outcome=advanced`、`families_in_flight=0`、`family_id=crypto-dynamic-weight-amm-tfmm-dutch-reverse-auction-rebalancing-2026-09-01`（與 round-1 複審獨立跑出者一致）；live 多輪 family `cross-sectional-volatility-regime-gated-residual-mixture-of-experts-2026-09-02`（r1 verdict `PASS` ＋ attempts 全部 stale）→ `in_flight=False`、reason 帶 `+ terminal verdict PASS`。**未**寫 `/results`（`handoff_log.jsonl` 361 行中 `decision_evidence` 欄位出現 0 次）、**未**建卡、**未**動 Kanban、**未**改 cron。**（本節為 v2.0.0 round-1 修復記錄；後續已納入 independent auditor run 222 PASS 的最終 direct-runtime bytes。）**
- `[V]`（**v-next 複驗（round-2 review 修復後），2026-09-24，卡片 `t_6c6a3286`**）：round-2 獨立複審指出同一 root cause family 的另一個 blocking defect——`runtime_state()` 拿 **family 級** verdict token 去判 **round 級** 規則：`family_verdict_token()` 掃描所有 round 並回傳第一個 terminal verdict，於是「r1 已 terminal、最新 r2 attempt 已發佈 terminal sentinel 但 r2 自己尚無 verdict」的 family 被誤放行（複審 probe：`action=appended`／`outcome=advanced`、1 張 create、下一張 `family.json` 落地、snapshot `current=None`），違反本節 3(a-1) 自己寫下的規範文字（該 round 尚無 terminal verdict → 仍 `noop`）。修復＝新增 `round_verdict_token()`：釋放判定只讀**最新 attempt 自己所屬 round** 的 `rounds/<round>/verdict.json`（`<round>` ＝該 attempt 的 `parents[1]`），並以同一套 ownership 規則重新驗證（`family_id` 相符／`kanban_task_id` 兩側皆有值時須相符／`round_id` 若存在須等於 round 目錄名／token 須為 contract-terminal；malformed／foreign／partial 一律視為**不存在**、fail-closed）；`in_flight = 窗內 and（尚未發佈 terminal sentinel **或** 該 round 尚無 terminal verdict）`，family 級 token 只保留給「最新 attempt 非 live」的收束路徑與 incident ledger。複驗：runtime 全套 **522/522 OK**（`test_production_handoff.py` 39 → **42 檢定**、`test_candidate_snapshot.py` 47 → **48**）；新增檢定在修復前為 RED（複審 defect fixture：r1 verdict `PASS` ＋ r2 已發佈 `DONE`、無 r2 verdict → 舊碼 `appended`／`advanced`；snapshot 同形狀 → 舊碼 `idle (no active runtime work)`）、修復後 GREEN（同形狀 → `noop`／`outcome=running`、0 張 create、無新 `family.json`；r2 有自己的 verdict → `advance`；foreign verdict（`family_id`／`kanban_task_id`／`round_id` 不符）→ 視為不存在、維持 HOLD）；live 唯讀 dry-run（真實 `/results`，53 families）→ `action=would_append`、`outcome=advanced`、`families_in_flight=0`、`family_id=crypto-dynamic-weight-amm-tfmm-dutch-reverse-auction-rebalancing-2026-09-01`；`candidate_snapshot.py` 同狀態輸出 `Current: idle (no active runtime work)`。**未**寫 `/results`、**未**建卡、**未**動 Kanban、**未**改 cron。**（本節為 v2.0.0 round-2 修復記錄；後續已納入 independent auditor run 222 PASS 的最終 direct-runtime bytes。）**
- `[C]`（v1.9.0）**production loop（全貌，逐段對應本文件各節；v1.9.0 明文化）**：

  ```
  Qlib compute（§9.2 步驟 3：`container exec -d`，detached）
    → attempt `state.json` stage=`ARTIFACT_READY`／`FAILED_SCRIPT`（§6.2；容器只寫 `/results`，不發 sentinel、不判 verdict）
    → reconciler（§9.4 v1.9.0；cron `f6b9aa5e9034`，`every 15m`）：無 terminal sentinel 但 compute-finished ＋ 卡片 `scheduled` → `unblock`（wake default）
    → default（host-side）：terminalize／retry／`INCOMPLETE`（§12.2）→ 發佈 terminal sentinel（§9.3）→ 寫 `verdict.json`（§10.7）／必要時 frozen survivor bundle（§10.8）
    → 卡片 `done`（§6.4；誠實的負結論也算 done）或由 operator `archived`
    → handoff（§14.4；cron `624d0be5b23c`、`:05`／`:35`）：tail 已 terminal 且無 active／blocked／未結案 incident → append 下一張 family 卡
    → dispatcher claim（`ready → running`）→ 回到第一步
  ```

  `[C]` 這條 loop 由三段**既有**機制拼成（container compute／no-agent reconciler／no-agent handoff），**不**新增任何 service／daemon／queue／manager；reconciler 不判 verdict、不改 `/results`、handoff 不建 auditor 卡、default 不長 turn 等 Qlib（§1.2/§25）。

- `[C]`（**文件對齊；卡片 `t_86d04b09`，2026-09-16；僅文字修正，無 runtime／gate／stage 變更**）**candidate 的 producer 就是 Research Intake Review 的同一決策**：每一個 `PASS`／`PASS-WITH-CAVEAT` 在同一次 review decision 產生兩個 **sibling outputs**——① Wiki Brain knowledge record（research-only 保存）；② 本節 production candidate pool 中的**恰一筆** candidate（`/results/_handoff/candidates.json`，並以 `/results/*/family.json` 推導消費狀態）。因此：Wiki Brain 是知識保存、**不是** candidate eligibility 的第二道 gate；`REMEDIATE`／`REJECT` 不進 pool；**不存在**「Wiki 之後再做 crypto/runnable suitability screening」的階段。candidate eligibility 就在 Intake Review 這一次決定，`PASS`／`PASS-WITH-CAVEAT` 必須足以 candidateize；body 的產生是 **format/canonicalization**（frozen GitHub artifact ＋ 該次 review 的正規化內容／crypto portability／caveat），仍須滿足上方 **v1.3.0 candidate card requirements**（含 `DCA PARAMETER DOMAIN` 與 `COHORT SURVIVOR SEMANTICS`），source 未明示的必要 execution 細節可標 `research-defined` 但不得改變核心 hypothesis；缺 prerequisite（本機無該資料／市場）**不是**拒絕 candidate 的新 gate——candidate 仍入 pool、body 忠實註冊 required data/market，未來的執行卡再依 §13 technical failure semantics 終結。append 為 idempotent（同 `reviewed_source`／`family_id`／fingerprint 已存在即 no-op）。本節其餘條文（append 演算法、fingerprint 規則、fail-closed findings、「同一輪最多 1 張」、auditor 非 production stage）與 §14.1–§14.3 皆**不變**；**不新增**任何 stage／service／daemon／queue／cron／manager。本條只作文字對齊，**未**變更 `runtime/production_handoff.py`、`runtime/reconcile.py` 或任何 production cron。

- `[C]` **v2.0 direct C3 override（AUDITED PASS / LIVE）**：同一個固定 host bridge 的
  `production_handoff_once` 一次只選 reviewed pool 的一個 family；runtime evidence、
  latest attempt 自己 round 的 terminal verdict 與 unresolved incident 仍是 advance gate，
  Kanban board/card/status/dispatcher **不是** gate，也不是 transport。選中候選的
  assignee 必須是 default、workspace 必須是存在的絕對目錄；不讓未輪到的候選 workspace
  擋住當前候選。以 `O_EXCL` 註冊 immutable `family.json`（`handoff.execution=direct_hermes`，
  無 `kanban_task_id`），在同一輪從 reviewed body＋固定 lifecycle footer 凍結 prompt，
  用 `hermes -p default --cli --accept-hooks chat --query-file <prompt> --in <workspace>`
  detached 啟動 default；可用既有 skills，無卡、無 `kanban create`。agent 根據 frozen
  body 適配策略、建立 round/run spec、P1–P10、`container exec -d qlib-run` 後退出；
  n8n bridge 不等 Qlib。家族 lease 隨 agent 生命週期釋放；失敗的已註冊 direct family
  優先以同一 body/fingerprint 重試，**不消費下一候選**；attempt 一旦出現，沿用 active
  attempt／per-round verdict guard 防重。歷史 family/round/run/task IDs 保持原封不動。
  `[T]` PASS 後的 post-survivor index/evidence 的 direct（card-free）provenance **以 §27.2／
  §27.3 的 v2.0 direct override 為準**（以 family/round/run 身分驗證、任一側洩漏非空 card
  keys 即 fail-closed；歷史 card-owned family 維持原本嚴格 `kanban_task_id` 雙側 non-empty
  string 檢查），本條不另立「direct family 不可索引」的限制；該 override 仍以獨立
  schema／negative-control 審計把關，不得憑 null task ID 假裝已可索引。
- `[C]`（**2026-09-28 prepared-execution cutover；PENDING AUDIT / C3 HOLD**）**normal C3 dispatch 不再以 Hermes default 作 launcher**。原 v2.0 direct C3 條文的 Hermes worker 路徑只保留為明確 `--legacy-agent-dispatch` rollback；production CLI 與 Python API 預設均要求 prepared execution。Research/Intake 須在既有 candidate 準備責任內產生 staged artifacts；不新增 stage／queue／daemon／service／Manager。live C3 維持 **HOLD pending audit**，本地改動未部署。
  1. **prepared contract**：`execution_file` 與 round/run specs 必須是 `<results>/_handoff/prepared/<family_id>/` 下 regular non-symlink files，分別不超過 64 KiB、1 MiB、512 KiB；manifest 恰含 `schema_version`、`document_kind`、`family_id`、`semantic_fingerprint`、`round_id`、`run_id`、`round_spec_file`／`round_spec_sha256`、`run_spec_file`／`run_spec_sha256`，不含 `argv` 或 runner path。Specs 必須鏡像 `rounds/<round_id>/round-spec.json` 與 `rounds/<round_id>/attempts/<run_id>/run-spec.json`；hash 與 identity 均重算驗證，direct specs 不得有非空 ownership 欄位。
  2. **preflight / fixed dispatch**：`runtime/production_handoff.py` 以 `/opt/homebrew/bin/python3 runtime/preflight.py --launch --attempt-dir <staged attempt> --json` 在 canonical mutation 前執行 P1–P10，僅接受 rc=0、JSON `overall=PASS`、`launch_gate=evaluated`。通過後才以 immutable write materialize canonical specs；再直接執行固定 `/usr/local/bin/container exec -d qlib-run /opt/venv/bin/python <script.path> --run-spec /results/<family>/rounds/<round>/attempts/<run>/run-spec.json --attempt-dir /results/<family>/rounds/<round>/attempts/<run>`，無 shell、無 Hermes、無 arbitrary argv。
  3. **freeze / retry / evidence**：首次 `family.json` 寫入前凍結 manifest resolved path/hash、兩份 spec hashes、round/run IDs 與 script path/hash。已登錄但尚無 canonical attempt 的 retry 必須與 frozen identity 完全相同；差異回 `registered_candidate_changed/prepared_execution_mismatch` 且不 launch。Container 非零／timeout 發生於 canonical attempt materialize 後，不由 C3 盲目重試；rc=0 仍須在有界時間內讀到 identity 一致且 stage 為 `RUNNING_QLIB`、`ARTIFACT_READY` 或 `FAILED_SCRIPT` 的 regular `state.json`，否則 `prepared_execution_no_evidence`，不得宣稱 advance。
  4. **unprepared candidate**：沒有 `execution_file` 時回報 non-terminal `candidate_preparation_required`；尚未註冊的 family 不寫 `family.json`、不產生 false `TECHNICAL_INCOMPLETE`、不啟動 Hermes。prepared mode 不要求 legacy `workspace_path`／default-worker target。
  5. **direct ownership compatibility**：`family.json.handoff.execution=direct_hermes` 僅是 P10/C4 沿用的 direct-family ownership token，prepared mode 下**不代表 Hermes process 實際啟動**；重新命名屬另一個 schema migration。
  6. **upstream remaining work / rollout**：Research/Intake 必須供應通過既有 parameter-contract/P10 驗證的 staged round/run specs，以及可由 `/scripts/<safe filename>.py` 映射並通過 P10 SHA-256 檢查的策略腳本；script 必須接受 `--run-spec` 與 `--attempt-dir`。完成獨立 audit 與明確 cutover 前，live C3 保持 HOLD，C4 維持既有 exception/reconciliation 職責。

## 15. Family Yield / Anti-Starvation Policy

### 15.1 為何需要
- `[C]` 沒有客觀停止條件時，單一 family 會無限 REFINE，B/C 永遠等不到 `ready`（starvation）。這不是資源問題，是排程公平性問題。

### 15.2 有界停止政策（bounded stop / yield）
- `[C]` 每個 family 的預算以「round 數」計，預設上限 **`max_rounds = 3`**（可在卡片 body 明示覆寫，但必須在 round 1 開始前寫定）。
- `[C]` 另設停滯上限 **`max_no_progress_rounds = 2`**（連續 2 個 round 無 progress 即停）。
- `[C]` `max_rounds` 與 `max_no_progress_rounds` 先到者生效。

### 15.3 no-progress 的可稽核判準
`[C]` 一個 round 算「有 progress」若且唯若至少一項成立（全部可由 artifacts 機械核對）：
1. **門檻推進**：該 round 的最佳目標指標相對上一 round 改善 ≥ 卡片預先註冊的 `min_improvement`（未註冊則視為 0，即任何改善皆算）。
2. **假說收斂**：該 round 明確淘汰了上一 round 的候選集合（淘汰集合非空且已寫入 artifacts）。
3. **否證成立**：該 round 觸發任一預先註冊的 falsification 條件（否證也是知識進展，計為 progress）。
4. **覆蓋擴張**：新增參數域/資料區間且已完成計算（僅「規劃」不算）。

`[C]` 以下**不算** progress：重跑、修基礎設施錯誤、換 run_id、只改註解、只延長預算、只換評估指標以製造改善。

- `[C]` 主觀延長一律被視為違規：判定只能用上述四項與 artifacts。若無法機械判定 → 記為 **no progress**（保守）。

### 15.4 達界線後的強制動作
- `[C]` 觸界線後，必須**誠實終結**為 `REJECT` 或 `DEFERRED`/`TECHNICAL_INCOMPLETE`，並立刻放行下一個 family（tail 已 append 的卡自動 `ready`）。
- `[C]` remediation / 新嘗試只能 **append tail**（新卡、新 fingerprint 或同 family 但 `parent_family` 註明再入理由），不得原地續命。
- `[C]` 例外只有一種：`FINALIST`（§17）——frozen survivor 另進下游 acceptance，不阻塞研究鏈。
- `[C]` 此政策**只是研究 stop policy**，不是新 scheduler/service/daemon（守住 INV-8 / §1.2）。
- `[T]` 尚未有自動倒數機制；落地前由 default 在每輪結束時對照 round 數與 artifacts 手動判定，並把判定結果寫入該 round 的 `/results/…/rounds/<round_id>/verdict.json`（`yield.yield_decision`）。

### 15.5 記錄欄位
落點：`/results/<family_id>/rounds/<round_id>/verdict.json` 的 `yield` 物件（immutable，§10.7）。

```json
{"rounds_used": 2, "max_rounds": 3, "no_progress_rounds": 0,
 "progress_evidence": ["round-r2-artifacts/cull.json"],
 "yield_decision": "CONTINUE|STOP_REJECT|STOP_DEFERRED|STOP_TECHNICAL_INCOMPLETE|FINALIST",
 "decided_at_utc": "…"}
```

## 16. Execution Preflight Contract

### 16.1 何時執行
- `[C]` **每次** launch 或 retry Qlib 前，必須執行 deterministic preflight。不得因為「上次剛跑過」而跳過。
- `[C]` preflight 由 host 端執行（default 或腳本），非 container 自檢。

### 16.2 Preflight 檢查清單（全部必查）

| # | 檢查 | 通過條件 | 失敗層級 |
|---|---|---|---|
| P1 | ExpansionDrive 已掛載 | `/Volumes/ExpansionDrive` 存在且可讀 | shared-layer |
| P2 | raw 存在且唯讀 | `market-data-raw` 存在且可讀；**在 container 內**寫入探針必須失敗（`touch /data/raw/__probe__` → `Read-only file system`）。**不可用 host 端 `test -w` 判定**：host 使用者擁有該 export，host 端永遠可寫，會誤判 FAIL（`[V]` 2026-09-13 實測） | shared-layer |
| P3 | results 存在且可寫 | `/Volumes/ExpansionDrive/qlib-results` 存在且可寫 | shared-layer |
| P4 | Apple Container runtime 存活 | `container system status` = running | shared-layer |
| P5 | `qlib-run` 存在 | `container ls` 可見；STATE=running；若 stopped 且可安全 start → 啟動並重查 | shared-layer |
| P6 | image/runtime identity 相符 | image = `qlib:0.9.7-arm64`；platform = linux/arm64；非 rosetta | shared-layer |
| P7 | `/qlib/work` 存在且可寫 | 容器內可寫；不存在/不可寫/損毀則重建 volume（INV-5） | card-local（可機械修復；**不 freeze**，§12.5） |
| P8 | Qlib import/version 相符 | `container exec qlib-run /opt/venv/bin/python -c "import qlib; print(qlib.__version__)"` = `0.9.7`。**必須用 venv 絕對路徑**：`/usr/local/bin/python` 沒有 qlib，且登入 shell（`sh -lc`）會把 PATH 還原成非 venv，用裸 `python` 會誤判 FAIL | shared-layer |
| P9 | 目標 attempt 未終結 | attempt 目錄無 terminal sentinel | — （存在即禁止重跑，INV-15） |
| P10 | run-spec 已 immutable publish | `run-spec.json` 存在且欄位合法；`script.sha256` 由 host 端**實際重算**後相符（`/scripts/<name>` 以既有 host scripts mapping 解析，見 §16.4）。無法解析或無法重算 → `FAIL`／NOT VERIFIED，**不得 PASS**（launch gate 不通過）。**（v1.8／§26.1 追加）** 同一 gate 亦驗證目標 attempt 的 round-spec 具備合法 generic `parameter_contract`（`validate_round_spec_contract` = 0 problems；pre-schema A v2 走 in-code bridge），缺失或不合法即 `FAIL`——此檢查必須在任何 compute 之前 | card-local |

- `[V]` P1–P10 已包成單一腳本 `runtime/preflight.py`（見 §16.4）。`[V]` 實測 2026-09-13：`python3 runtime/preflight.py` → P1–P8 全 PASS（P9/P10 在未給 `--attempt-dir` 時為 `N/A`）；`container exec qlib-run /opt/venv/bin/python -c "import qlib; print(qlib.__version__)"` → `0.9.7`；`container exec qlib-run /usr/local/bin/python -c "import qlib"` → `ModuleNotFoundError: No module named 'qlib'`（此即裸 `python` 誤判的來源）。`container ls` → `qlib-run  qlib:0.9.7-arm64  linux  arm64  running  6 CPU / 4096 MB`；`container --version` → `1.4.1`；`/Volumes/ExpansionDrive/{market-data-raw,qlib-results}` 皆存在。
- `[C]` P7 可機械修復：`/qlib/work` 可重建（INV-5），因此不屬於 shared-layer freeze 條件。
- `[V]` preflight 已包成單一腳本：`python3 runtime/preflight.py [--attempt-dir <dir>] [--launch] [--json]`。輸出每個 P# 的 PASS/FAIL/NA 與 `overall`，exit code 0 = 全數 evaluated 檢查 PASS，1 = 有 FAIL，2 = 使用錯誤。`--launch` 必須搭配 `--attempt-dir`（否則 P9/P10 無法評估，直接拒絕執行）。
- `[C]`（**v2.0 direct override（AUDITED PASS / LIVE）**）direct family（§9.4／§14.4 的
  `handoff.execution=direct_hermes`）的 P10 另驗 round-spec identity/ownership：
  `round-spec.json` 必須可讀、`family_id`／`round_id` 與 attempt 目錄路徑一致，且**不得**
  含 `task_id`／`kanban_task_id`／`kanban_board`（與 §9.4 v2.0 direct C4 的
  `mapping_problems` 同一規則）。不成立即 P10 `FAIL`，在任何 compute **之前**攔下——
  避免 Qlib 開跑後才被 C4 以 `mapping_mismatch` incident 攔下。歷史 card-owned
  family 的 P10 行為不變。

### 16.3 結果處置
- `[C]` 全綠 → 允許 launch（新 run_id 或首次 run）。
- `[C]` 有 card-local 且可機械修復 → 修復後**同 round 新 run_id** 重試。
- `[C]` 有 shared-layer → **freeze**（§12.5），卡片 `blocked(kind=capability)`，不得重試打爆 dispatcher。
- `[C]` 不可修復且屬 shared plane 才 freeze；不可修復但屬 card-local → `TECHNICAL_INCOMPLETE`。

### 16.4 Preflight 腳本（最小實作）

- `[V]` 位置：repo `runtime/preflight.py`（純 stdlib、Python 3.9+）。P1–P8 為環境面（mount / runtime / container / image / work volume / Qlib import），P9/P10 為 attempt 面（未終結、run-spec 合法且 `script.sha256` 相符）。
- `[V]` 環境面與 attempt 面可分開跑：不帶 `--attempt-dir` 只做環境檢查（`overall_env`）；帶 `--launch` 則強制要求 `--attempt-dir`，避免把「只驗環境」誤當成放行 gate。
- `[C]` 腳本的判定只依檔案系統與 `container` 查詢結果，不依賴卡片 metadata、不寫入任何狀態、不啟動 Qlib 計算。
- `[V]` P10 的 sha256 重算是**真的重算**（2026-09-13 實測）：`script.path` 為 `/scripts/<name>` 時以 host scripts directory 解析（`--host-scripts`；預設 host 上作為 container `/scripts` ro mount 來源的目錄，亦可用 `QLIB_HOST_SCRIPTS` 覆寫），其餘視為 host 絕對路徑；兩者皆不可讀 → P10 `FAIL`（`NOT VERIFIED`），launch gate 不通過。
- `[V]` P9/P10 邏輯層檢查：`python3 runtime/tests/test_preflight_p10.py`（stdlib unittest，真實檔案系統 + 注入 host scripts 目錄，不需 container）10/10 OK，涵蓋 mapped/絕對路徑重算相符、sha 不符、不可讀路徑 `NOT VERIFIED`、缺 `script.sha256`、缺 `run-spec.json`、terminal sentinel 使 launch gate FAIL、未給 `--attempt-dir` 的 `NA`。

### 16.5 Reboot recovery gate（opt-in，fail-closed）
- `[C]` `--recover` 為 opt-in flag：**預設 preflight 不啟動 container system、不自動建立容器**。預設模式下，已存在但 stopped 的 `qlib-run` 會嘗試 `container start` 並重查（P5 §16.2）。只有明確傳入 `--recover` 時才觸發完整的 `recover_execution_plane()`（含 `container system start`、fail-closed 恢復序列）。
- `[C]` `recover_execution_plane()` 為 ordered, fail-closed：
  1. ExpansionDrive 存在且可讀 → 否則立即返回 `fail_reason=expansion_missing`（不觸碰 container）。
  2. `container system status` ≠ running → `container system start`（timeout 180s）→ poll 直到 running（timeout 120s）或返回 `fail_reason=system_start_failed`。
  3. `container ls` 查詢 `qlib-run` → 不存在 → 返回 `fail_reason=container_absent`（**never auto-create**；缺失的 container 需由 operator 依 runbook 手動重建）。
  4. `qlib-run` state ≠ running → `container start qlib-run`（timeout 120s）→ poll 或返回 `fail_reason=container_start_failed`。
- `[C]` 恢復成功後才進入 P1–P8 檢查；恢復失敗時 preflight 立即返回 `overall=FAIL` 且 `rc=1`，**不進入 P1–P8**，report 內含 `recovery` 物件（`attempted=true, ok=false, fail_reason, actions`）。
- `[C]` Reconcile wrapper（`~/.hermes/scripts/quant_runtime_reconcile.py`）在每次 cron tick 時**預設**執行 `preflight --recover --json` 作為 recovery gate，結果在 core reconcile 之前評估：
  - gate `overall=PASS` **且** preflight `rc=0` → 進行 core reconcile。
  - gate `overall=FAIL` **或** preflight `rc!=0` → **fail-closed**：跳過 core reconcile（不 unblock、不 incident、不 state change）；一行輸出 gate failure，重複相同 signature 靜默（dedupe/no-spam，signature = `gate|<failed_checks>|<recovery_fail_reason>` 或 `gate|rc_<N>|<failed_checks>|<recovery_fail_reason>`）。
  - gate healthy 後自動清除 gate state（下次再 fail 時才重新告警）。
- `[C]` `--no-recovery` 為 escape hatch：跳過 recovery gate，直接執行 core reconcile（用於已知 healthy 或 operator 手動控制場景）。
- `[C]` `--dry-run` 仍執行 recovery gate（healing is the point），但不寫 gate-dedupe state 也不寫 reconciler state。
- `[V]` Recovery gate 測試：`python3 runtime/tests/test_preflight_recover.py`（stdlib unittest，mock all subprocess），涵蓋 fail-closed（expansion missing / system start failed / container absent / container start failed）、healing order（system down → start → ok / stopped container → start → ok）、default P5 starts stopped existing container、`--recover` fail-closed 主層級回歸（`ok=false` → `overall=FAIL` rc=1，不進入 P1-P8）。

## 17. Retired secondary-engine boundary（non-participating）

- `[C]` 現行及已規劃的 production workflow 只有 **Qlib**；Qlib full-backtest、其 durable artifacts 與 round `verdict.json` 才是本 contract 的 performance truth。
- `[C]` Lean、Nautilus、PyBroker 均已退役，**不是** current/future production stage、validation gate、performance-claim gate、candidate eligibility gate 或 second opinion requirement。它們的不存在不產生任何 blocker，也不會把 Qlib 結論降級為 `research-only`。
- `[C]` 歷史 changelog、舊 frozen artifacts、舊 strategy research records 中對 retired engine 的提及保留為 historical provenance；不得藉此推導「應重新安裝／應新增第二引擎驗證」的 current obligation。
- `[C]` survivor freeze、§27 leaderboard、§28 evidence、§29 private mirror 與下一 family handoff 都不等待任何 retired engine。
- `[C]` 禁止以任何 retired engine 重新搜尋參數、改 hypothesis、擴參數域、產生第二套全量 search/backtest path，或反向改寫 Qlib 的 immutable verdict/bundle。
- `[C]` 若 operator 未來明確決定重新導入任一第二引擎，必須另走 §26 change control，重新定義角色、truth boundary 與 non-gating/independence 規則；本節本身**不構成 future integration commitment**。
- `[V]` 目前現役量化 runtime/backtester 為 Qlib-only；本 repo 不含 Lean／Nautilus／PyBroker 的現役 runtime tree。

## 18. Durable state / comment conventions

### 18.1 Durable state 落點（Kanban card 不承載 machine-readable metadata）
- `[V]` 現行 Kanban kernel **沒有** task-level metadata 欄位：`tasks` schema 與 `Task` model 皆無 `metadata`；`metadata` 只存在於 `task_runs`，且 `complete_task` 把它寫入 **closing run**（run 層級、收尾才寫），不能當 card-level runtime state。
- `[C]` 因此：Kanban card 只保存 lifecycle/status/ordering（DB 欄位 + events）；machine-readable runtime ownership、scientific lineage 與 verdict 一律落在 `/results` durable artifact：

| 資料 | 落點（唯一 authoritative） | 卡片端 |
|---|---|---|
| ownership（`kanban_task_id`、board、family_id） | `/results/<family_id>/family.json`、`round-spec.json`、attempt `run-spec.json` | comment 摘要 |
| scientific lineage（`semantic_fingerprint`、`fingerprint_input`、`parent_family`、`lineage_note`） | `/results/<family_id>/family.json` | comment 摘要 |
| round 研究 spec（參數域/資料窗/falsification） | `/results/<family_id>/rounds/<round_id>/round-spec.json` | comment 摘要 |
| attempt 進度 `stage` | attempt `state.json`（atomic rewrite） | — |
| verdict / `performance_claimable` / yield 判定 | round `verdict.json` | comment 摘要 |
| failure layer/class | sentinel `failure` + round `verdict.json` | comment 摘要 |
| terminal evidence | attempt `DONE`/`FAILED`/`INCOMPLETE` + `result.json` | — |

- `[C]` comment 只做人類摘要，**不得**作為唯一 machine state（INV-17）。
- `[C]` 鍵名為固定契約（§10.6/§10.7）；鍵名變更屬 change control（§26）。
- `[C]` `family.json` 只是檔案契約，不構成 Registry service，也不得擴張為索引服務（§1.2/§14.3）。

### 18.2 Comment 慣例
- `[C]` 每輪至少一則 comment，開頭固定為 `round <round_id> / run <run_id> —`。
- `[C]` shared-layer 事件 comment 開頭固定為 `hotspot:` 或 `shared-layer:`，並附 P# 編號（§16.2）。
- `[C]` completion summary 必含：檔案 path、主要新增條款、仍需實測項目（`[T]` 清單）。
- `[C]` 禁止把 secrets、token、憑證、PII 寫入 comment 或 artifact（也不得寫入任何卡片欄位）。

## 19. Observability / security

### 19.1 Observability（最小）
- `[C]` 唯一 durable 觀測面：`/results` artifacts + Kanban events + run 的 `logs/`。
- `[C]` 每個 run 必須可回答：誰投遞（task_id）、何時、用什麼 spec（checksum）、算多久、結論 hint、是否終結。
- `[C]` 不得為了觀測引入新的常駐服務/port；`container exec` 查詢即足夠（INV-8）。

### 19.2 Security
- `[C]` container 不掛載 secrets、不掛載 host home、不掛載 `/var/run/docker.sock` 或 Apple Container 的 runtime socket。
- `[C]` container 不持有 Hermes credential；不以環境變數注入 token。
- `[C]` `/scripts` 唯讀：改腳本必須在 host 端改，改後重跑 preflight P10（script sha256 變更 → 必須新 run_id）。
- `[C]` image 必須以 pinned commit 建置（`[V]` Containerfile 已 pin `QLIB_COMMIT=da920b7f…` 並在 build 內 `test "$(rev-parse HEAD)" = "$QLIB_COMMIT"`）。
- `[C]` 任何「讓 container 直接寫 Kanban DB」的提案一律拒絕。

## 20. Audit checkpoints

- `[C]` **AC-1（run 投遞前）**：run-spec immutable、script.sha256 相符、preflight 全綠。
- `[C]` **AC-2（放行前）**：sentinel terminal + mapping 一致 + checksums 相符 + 無 multiple/stale sentinel；判定只依 `/results` artifacts + DB 狀態讀回，**不依賴 task metadata**。
- `[C]` **AC-3（round 終結）**：verdict 有 artifacts 支撐；falsification 是否預先註冊；`performance_claimable` 依據是否載明；上列齊備後寫入該 round 的 `verdict.json`（immutable）。
- `[C]` **AC-4（family 終結）**：yield policy 判定可稽核（rounds_used / no_progress_rounds / evidence）；未無限期延長；判定落點為最後一個 round 的 `verdict.json`。
- `[C]` **AC-5（shared-layer 事件）**：freeze 依據為 shared-layer（非 card-local 誤判），解除條件已驗證。
- `[C]` **AC-6（獨立性）**：implementer ≠ auditor；auditor 唯讀；FAIL 修復回 default。
- `[C]` 每個 checkpoint 的證據必須可由 auditor 以唯讀命令重現；不可重現者視為未通過。

## 21. Rollout / smoke tests / failure drills

### 21.1 Rollout 階段
1. `[V]` R0：文件凍結 → audit → operator 核准（v1.0.1 = AUDITED PASS / FROZEN，auditor t_e35c39c0；v1.1.1 = AUDITED PASS / FROZEN，audited content commit 18d6c3f，auditor re-audit t_83682069，2026-09-13；v1.2.0 = AUDITED PASS / FROZEN，audited content commit 068d6f7，auditor t_7b979fe8，2026-09-13；v1.3.0 = FAIL audit t_246c62d7；v1.3.1 = FAIL audit t_23f4c3ef；v1.3.2 = AUDITED PASS / FROZEN，audited content commit 0363011，auditor t_3ffaeeb8，2026-09-13；v1.4.0 = FAIL audit t_0bd01630；v1.4.1 = FAIL audit t_3edafbb9；v1.4.2 = AUDITED PASS / FROZEN**，audited content commit d699527，auditor t_dedbe003，2026-09-13；**v1.5.0 = AUDITED FAIL**（auditor t_57357d4c，2026-09-13；F1/F2/F3）；**v1.5.1 = AUDITED FAIL**（auditor t_346bcc04，2026-09-13；F1/F2/F3）；**v1.5.2 = AUDITED PASS / FROZEN**，audited content commit 0a361258，auditor t_3691bfb4，2026-09-13；依卡片 t_d19618e1；**v1.6.0 = AUDITED PASS / FROZEN**，audited content commit 6e7d046，auditor t_e18a0f35，2026-09-13；依卡片 t_68954a45；**v1.7.0 = AUDITED PASS / FROZEN**，audited content commit d21ec33，auditor t_3219d6a0，2026-09-14；依卡片 t_15fed3f2；**v1.7.1 = AUDITED PASS / FROZEN**，audited content commit ed07605，auditor t_235ae131，2026-09-14；依卡片 t_fd672413；前次 v1.1.0 = AUDITED FAIL @ audit t_d7f48c7a）。
2. `[V]` R1：preflight 腳本化（P1–P10），只讀，不投遞 → `runtime/preflight.py`（2026-09-13 實測）。
3. `[T]` R2：單一 smoke run（非策略）走完 `ready→running→scheduled→sentinel→unblock→ready`。**部分已驗證**：sentinel 產生/驗證、fail-closed 分支、以及 `scheduled→ready` 的判定邏輯已實測（fixture + `runtime/tests/test_reconcile.py`）；**真的放行一次**尚未執行，因為放行需要 `scheduled` 卡 + 無 fence 的 host context（§9.4），而本卡執行環境（kanban worker session）被 Hermes 拒絕 board 變更。`container exec` 投遞段的真實 Qlib smoke 計算同樣尚未執行（不在 t_ec039d5f 範圍）。
4. `[T]` R3：reconciler 腳本化（no_agent cron），以既有 sentinel 做 dry-run 對帳 → `runtime/reconcile.py --dry-run` 已可執行（2026-09-13 實測）；但 cron 的正式掛載（以及 apply 的第一次真實放行）仍待 operator 在**無 fence 的 host context**完成，見 §9.4 的執行環境限制。**v1.7.0 更正（2026-09-14）：cron 之掛載已完成——job `f6b9aa5e9034`、`every 15m`、no-agent，惟**保持 paused**；apply 的第一次真實放行仍待 operator 放行後在無 fence context 執行。**
5. `[T]` R4：第一張正式 strategy card（family A）全流程；觀察 yield policy 紀錄。
6. `[C]` 每階段完成後必須有 DB 讀回證據；階段未過不得前進。
7. `[V]` R5（v1.3.0）：**Strategy A v2 已完成** —— family `close-vs-sma-mean-reversion-long-flat-v2`，20 cohorts × 12 strategy × 48 DCA × 9 phase grids = 103,680 case evaluations，2026-09-13 以 `container exec --detach` exact-once 投遞、runtime 1302 s、terminal `DONE`（卡片 `t_1f97bf6b`）。結果：`coverage_complete=true`、14/14 assertions true、2 個 cohort survivor（`BTCUSDT/1h`、`SOLUSDT/4h`）。該 round 的 `verdict.json` 為 v1.3.2 語意下的 `FINALIST`（immutable、不回寫）；依 v1.4.0 §10.8 另生成 survivor bundle，bundle 內標註兩個 survivors 在 v1.4.0 語意下**都通過基本 gate**。
8. `[T]` R6（v1.4.0）：Strategy B v2 —— v1.4.0 已備妥 preregistration／templates（`runtime/templates/strategy_b_v2_{round,run}_spec.template.json`：20 cohorts × 120 strategy × 48 DCA × 10 phase grids = 1,152,000 case evaluations、cohort survivor 語意與 v1.4.0 對映），但 **B v2 engine 尚未實作**（v1.4.0 的 B runner 是 launch 前置條件；已 operator-stopped 的 B v1 runner 為 archive-only，不得重用）、**未 launch**、卡面未建立。Strategy B v1 卡維持 operator-stopped（`blocked`、無 verdict）；§14.4 的 append 與 handoff cron `624d0be5b23c` 一律保持 **paused**，直到 operator 明確放行。**（v1.9.0 更正：以上為 v1.4.0 當時的 rollout 現況；後續 current state 見 §14.4 v1.9.0 條——handoff cron 已由 ChatGPT 啟用且排程為 `5,35 * * * *`，reconciler `f6b9aa5e9034` 仍 paused。歷史敘述逐字保留。）**
9. `[V]` R7（v1.5.0；v1.5.1 收緊）：**post-survivor lifecycle 落地** —— `runtime/survivor_index.py`（file-only survivor index，§27.2；`--out` 受 `_survivors/**` realpath 寫入邊界強制）＋ `runtime/survivor_leaderboard.py`（forward evidence ingestion ＋ leaderboard，§27.3／§27.5；slice 需可驗證的 `source_run` 出處、`--out-dir` 同一邊界強制）＋ `runtime/tests/test_post_survivor.py`（**30 檢定**；含 F1／F2／F3 三組 v1.5.0 攻擊的 regression）；以既有 A v2 frozen bundle seed → index 恰含 2 survivors（`BTCUSDT/1h`、`SOLUSDT/4h`）、`leaderboard.json`／`leaderboard.csv` 兩者皆 `FROZEN_ONLY`、0 forward slices、0 `champion_candidate`（未執行任何 post-freeze 計算，未偽造任何 forward metric）；既有 immutable artifacts 逐位元未改。本階段**不** launch B v2、**不**啟用 cron、**不**新增 service／daemon／queue／Registry／Orchestrator。

### 21.2 必要 smoke / failure drills（每項都要有可重現證據）

| # | drill | 期望結果 |
|---|---|---|
| D1 | `ready → running → scheduled → DONE → unblock` 正常路徑 | 卡片回到 `ready`；sentinel 為 `DONE`；進行中不長 turn |
| D2 | 重複 reconcile（同一 sentinel 跑兩次） | 第二次無副作用；無重複 unblock／無狀態漂移 |
| D3 | partial sentinel（manifest 有檔、`DONE` 未 publish） | 不放行；判 `partial_write`；走 orphan/新 run_id |
| D4 | artifact missing（sentinel 存在但 manifest 檔案不存在） | 不放行；`checksum_mismatch`/`missing artifact` 路徑 |
| D5 | technical fail（腳本 exception → `FAILED` sentinel） | 卡片仍能放行；`TECHNICAL_INCOMPLETE` 或新 run_id；不永遠 `scheduled` |
| D6 | host reboot（mid-run） | 以 boot id/orphan 判定恢復；補 `INCOMPLETE`；不重跑已終結 run |
| D7 | container kill / restart | 同上；P5 能安全 start 則恢復；identity 不符則 freeze |
| D8 | orphan retry（無 sentinel、容器已重啟） | 判 `orphaned_run`；同 round 新 run_id |
| D9 | A `REJECT` → B 放行 | A `done(REJECT)` 後 B 卡自 `todo` → `ready`，B 不需人工 promote |
| D10 | A `FINALIST` → B 放行 + acceptance | B 立即 `ready`；acceptance 另開卡且不阻塞鏈 |
| D11 | NEW_FAMILY tail append | 新卡是最後一張、parent = 舊 tail、狀態 `todo` |
| D12 | shared-layer freeze（例如卸載 ExpansionDrive） | 全鏈凍結、無重試風暴；解除後 preflight 全綠才放行 |
| D13 | 多個 terminal evidence 並存（`DONE` + `FAILED`） | fail-closed：卡片保持 `scheduled`、不 unblock、不 block；寫 `reconciliation_incident.jsonl`；人工介入後才處理 |

- `[C]` **D1–D13 不是第一張正式 strategy card 的前置 gate**。第一張只需要「沒有它就無法可靠 launch / 終結 / 稽核」的最小集合（附錄 B 標 `BLOCKER` 者：preflight、terminal evidence + checksum、reconcile 放行）。其餘 drills 一律 `DEFERRED` hardening，不阻擋 Strategy A。
- `[C]` reboot / resume 核心路徑（D6/D7/D8 對應 §12.1–§12.2）在 contract 層**不被阻擋**：orphan attempt（無 sentinel）由 host/default 依 §12.2 以 `runtime/terminal_evidence.py` 補寫 `INCOMPLETE`，再由 reconciler 放行；此路徑不需要新服務、不需要 daemon。drill 本身（真的 reboot 一次）仍屬 `DEFERRED`。
- `[C]` drill 一律不得對 `/data/raw` 做任何寫入嘗試（唯讀前提下以「卸載/模擬路徑不可用」方式驗證）。
- `[C]` drill 若需要中斷真實計算，必須使用非策略 smoke family，不得動研究鏈上的正式卡。
- `[T]` 上述 13 項 drill 全部尚未執行；本節是驗收要求。

## 22. Objective acceptance criteria（本 contract 的可判定驗收）

- **A1** 文件存在於 `/Users/hong/workspace/QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md`，且每個事實/決策段落都有 `[V]`/`[C]`/`[T]` 標記。`[C]`
- **A2** 無任何段落把 `[T]` 敘述為已具備能力（auditor 逐段檢查）。`[C]`
- **A3** 正常 Qlib 等待路徑明寫為 `running→scheduled→(unblock)→ready`，且明寫 `promote` 不適用 `scheduled`。`[V]`+`[C]`
- **A4** completion bridge 明寫為 sentinel + no-agent reconciler + unblock，並明文排除 HTTP/webhook/Redis/Celery/queue。`[C]`
- **A5** round_id / run_id 分離，且 technical retry 只換 run_id。`[C]`
- **A6** parent edge 只代表 scheduling/order；scientific lineage 用 `/results/<family_id>/family.json` 的 `parent_family`（非卡片 metadata）。`[C]`
- **A7** `done != PASS` 表格完整且 verdict=5 種 + `performance_claimable` 分離。`[C]`
- **A8** sentinel schema 完整（含 atomic publish、checksums、terminal 必有、失敗 attempt 也有 terminal evidence）。`[C]`
- **A9** reconciler 契約含 mapping 驗證、partial/stale/multiple/duplicate 處理。`[C]`
- **A10** Family Yield 有客觀 stop gate（max_rounds / max_no_progress_rounds / 可稽核 no-progress 判準）且明寫不是新 scheduler。`[C]`
- **A11** Preflight P1–P10 完整，且 `/qlib/work` 標為可重建（card-local，非 shared-layer freeze 條件）、`/results` 為 durable evidence、raw 為 ro。`[V]`+`[C]`
- **A12** card-local 與 shared-layer freeze gate 分離，並說明各自處置；單一 work volume 故障一律 card-local。`[C]`
- **A13** PASS／既有歷史 FINALIST 終結後下一 family 依既有 handoff 規則放行；Lean／Nautilus／PyBroker retired/non-participating，不得成為 Qlib full-backtest、survivor lifecycle 或下一 family 的 current/future gate。`[C]`
- **A14** 未新增 Manager/Service/Factory/Registry 類抽象；未新增 daemon/service/queue（`family.json`/`verdict.json` 為檔案契約，非 Registry service）。`[C]`
- **A15** 文件同時含：狀態機（卡內/卡間）、lifecycle、schemas、idempotency、reboot/orphan recovery、failure taxonomy、NEW_FAMILY、yield、preflight、durable state/comment conventions、observability/security、audit checkpoints、rollout/drills、acceptance、auditor checklist、worked examples、禁止事項、change control。`[C]`
- **A16** 文件不存在任何「以 task-level metadata 作 durable state」的要求；ownership/lineage/verdict 落點為 `/results` artifact（`family.json`/`round-spec.json`/`verdict.json`）。`[C]`
- **A17** 文件不存在對 `scheduled` 卡直接 `block` 的要求；衝突一律走 §12.6 incident（保持 `scheduled`、不 unblock、寫 incident artifact、人工介入）。`[C]`
- **A18** 單一 `/qlib/work` volume 故障一律 card-local（可重建、同 round 新 run_id、不 freeze）；只有系統性 shared-layer 故障才 freeze。`[C]`
- **A19** 「全量回測」有明確定義（單一 strategy card 的 eligible universe 覆蓋 symbols × timeframes × strategy parameter domain × **DCA parameter domain** × historical/OOS/robustness，且每個 cohort 都跑完整乘積），且 production 模式明寫 sequential A→B→C、多 family 混跑不是 production。`[C]`
- **A21** family gate 明寫為 cohort survivor disposition（0/1/>1/TECHNICAL_INCOMPLETE），selector 為 deterministic 且**歷史段限定可執行**，survivor 五項要求（historical winner、OOS、full、四項 robustness、60% 歷史鄰域）齊備，且明寫跨 cohort median **不得**作為 family gate。`[C]`
- **A22** handoff candidate card requirements 明寫必須含 DCA parameter domain 與 cohort survivor rules，且 `runtime/production_handoff.py` 對缺少者 fail-closed 為 `candidate_body_not_v13`（不建卡）；`family.json` 的 `fingerprint_input` 必須含 DCA domain 與 selector/disposition 版本。`[C]`
- **A20** production blocker 與 deferred hardening 已分級（附錄 B / §21.2）：D1–D13 與 T5–T8 等不得被當成第一張 strategy card 的前置 gate；只有 launch/終結/稽核不可缺的最小項才算 blocker。`[C]`
- **A23** per-fill 成本會計有明確定義且可執行驗收：每個 entry／DCA add／exit fill 的 taker fee 在該 fill 時點扣入 realised equity，`net_pnl`／`ending_equity`／每日 equity marks／Sharpe／margin 判定全為 net-of-fee，且 `fee_2x` 等成本壓力軌必須實質改變 net PnL／equity／robustness verdict；engine 層有 free／costly／`fee_2x` 迴歸（`container/scripts/tests/test_strategy_a_engine.py`），且這些迴歸在未修版本上必須失敗。`[C]`
- **A24** DCA provenance 有分類要求（`PROJECT_PRE_REGISTERED_SEARCH_DOMAIN` / `PROJECT_PRE_REGISTERED_CONSTANT` / `USER_FIXED`）與可執行強制（`runtime/strategy_a_v2_counts.py` 的 provenance 檢查 + round-spec/run-spec 分類一致），且被搜尋的軸或無 operator 證據的常數**不得**標為 user-fixed。`[C]`
- **A26** v1.4.0 family gate 明寫「0 survivor → `REJECT`、**>=1 survivor → `PASS`**、coverage／技術不完整 → `TECHNICAL_INCOMPLETE`」，`MULTIPLE_SURVIVORS` 只是 disposition band 而非 verdict，且 survivor 數 >1 **不得**使 `performance_claimable=false`（唯一依據是 §9.6）；engine 的建議輸出與 §6.4/§7.3 一致，並有可執行迴歸（0／1／2+ 三個 band＋survivor 順序置換不變＋`disposition_mapping_version` 揭露）。`[C]`
- **A27** round 的 frozen survivor bundle（§10.8）具備：固定落點、**全部** survivor、不得排序／淘汰／二選一、不得改寫、只從 terminal `DONE` 且 `coverage_complete=true`／assertions 全 true 的 attempt 產生、**明確定義且非遞迴、可由 auditor 以純 stdlib 從持久化檔案獨立重算的 identity recipe（§10.8：移除 `generated_at_utc` 與 `bundle_identity_sha256` 兩欄後的正規化 JSON sha256，不得有其他隱含排除）**、**重跑比對只能逐鍵排除頂層 `contract` 與巢狀 `generator.sha256` 這兩個產生者身分欄位（v1.4.2；`generator.path` 與非 dict 的 `generator` 一律納入比對，自洽重簽的 `generator.path` tamper 必須被拒）**，以及可執行檢查（`runtime/survivor_bundle.py` ＋ `runtime/tests/test_survivor_bundle.py`，含 legacy 語意揭露、fail-closed 負向控制、公開 digest 的第三方重算、tamper 負向控制與 `generator.path` 自洽 tamper 的 F2 regression）；Strategy A v2 round r1 的既有 `verdict.json` 保持 immutable 且 bundle 由既有 `cohort_survivors.json` 生成、含兩個 survivors。`[C]`
- **A25** gross PnL 有獨立的 price-PnL accumulator（每個 exit／flatten 只累加 exit proceeds − cost basis，不含 fee／funding；entry／add 不動 gross），`result`/grid 的 `gross_pnl` 直接來自該 accumulator，且 `pnl_decomposition` 是兩個獨立來源的交叉比對並有**負向控制**證明非恆真（`TestGrossPnlAccounting` 在未修版本上失敗）。`[C]`
- **A28** post-survivor lifecycle（v1.5.0，§27）為**檔案層**且不改寫既有判定：①file-only survivor index（`/results/_survivors/survivor-index.json`）是 derived/rebuildable，**不是** source of truth、也**不是** Registry service；②`survivor_id` deterministic 且至少釘住 family_id／round_id／run_id／`bundle_identity_sha256`／cohort／strategy params／DCA params；③缺 checksum、bundle invalid、來源不一致（目錄名／`kanban_task_id`／`round-spec.json` checksum；v1.5.1 起 `kanban_task_id` 於 bundle 或 `family.json` **任一方缺漏**亦屬來源不一致，不得只在兩側皆 truthy 時才比對；v1.5.2 起該 id 於任一方為**非 non-empty string**（int／bool／list／null／空字串）亦屬來源不一致，即使兩側同值）、param cell 非註冊軸、duplicate survivor_id 一律 fail-closed；③之二**本層對 `/results` 的寫入邊界為工具層強制**（v1.5.1）：`survivor_index.py --out` 與 `survivor_leaderboard.py leaderboard --out-dir` 必須把候選路徑與 `<results-root>/_survivors` **兩側 realpath 解析後**比對前綴，界外即 rc=1 且不寫入任何檔案（symlink 與 `..` 段皆無法導向 frozen bundle／verdict／result），界內子路徑仍允許；④forward evidence 落點 `_survivors/forward/<survivor_id>.jsonl`、**append-only** 且寫入後讀回，`data_start` 必須嚴格晚於該 survivor 的 `research_data_cutoff`、不得與已記錄 slice 重疊、params／bundle identity 必須是 incumbent 的（改參數者是 challenger）；④之二**slice 必須有可重新驗證的 `source_run` 出處**（v1.5.1）：`attempt_dir`（結果樹內、非 `_survivors/**`、目錄名等於 `run_id`）＋ terminal `DONE` sentinel（sha256 相符、`status=DONE`、`run_id`／`task_id`／`family_id` 相符、非 frozen research run）＋ sentinel 記錄且與磁碟相符的 `result.json` checksum ＋ 與 slice 逐欄相等的 `result.json.forward_slice`；寫入與排名時都重新驗證，任一不符即拒收、拒排名；⑤無真實 post-freeze 計算時正確狀態是 zero-forward（`FROZEN_ONLY`），**不得**偽造任何 forward metric；⑥challenger 不得覆寫 incumbent，且其 OOS 起點必須晚於自身 preregistration cutoff；⑦leaderboard 用透明 deterministic ordering（`has_forward` → forward sharpe／return／max_dd_pct 絕對值 → oos_sharpe → robustness stress floor → neighbourhood → `survivor_id`），提供 Top-10，`evidence_state` ∈ {`FROZEN_ONLY`,`ACCUMULATING`,`FORWARD_POSITIVE`,`FORWARD_DEGRADED`} 僅為描述、**永不回寫 PASS**，掉出 Top-10 不等於 REJECT；⑧可執行檢查 `runtime/tests/test_post_survivor.py`（**30 檢定**：index deterministic／duplicate／invalid bundle／來源不一致／ownership id 缺漏／param 軸、`--out`／`--out-dir` 越界與 symlink／`..` 逃逸、forward post-freeze／overlap／params mismatch／readback／無 `source_run` 與偽造出處／數字與 pinned `result.json` 不符、retune 不覆寫、leaderboard deterministic／tie-break／forward 優先／fallback／Top-10 cap／A seed 恰兩人／no fake forward；其中 F1／F2／F3 三組 regression 在 v1.5.0 的 bytes 上實測 FAIL），並以既有 A v2 bundle seed 出恰 2 survivors、兩者 `FROZEN_ONLY`。`[C]`

- **A29** survivor evidence preservation（v1.6.0，§28）為**檔案層**且不改寫任何判定：①**觸發點是 leaderboard
  entry**（`leaderboard.json` 的 `entries` 成員；不是 Top-10、不是 PASS gate），且**大量 rejected／candidate
  cell 明文不保留逐筆 execution**（103,680 次 research evaluation 與所有未 promoted cell 維持
  `artifacts/grid_*.csv` 摘要）；②evidence package 存在與否**不得**回寫 verdict／`performance_claimable`／
  `evidence_state`／`champion_candidate`／ranking，`coverage` 可 rc=1 但 leaderboard 仍合法（package missing
  時 rc=0）；③落點唯一為 `/results/_survivors/evidence/<survivor_id>/{manifest.json,aggregate.csv,grids/<grid>/
  {episodes,fills,equity}.csv,summary.json}`，不複製整個 frozen round、不複製 103,680 列；④寫入邊界比 §27 更窄
  （只 `_survivors/evidence/**`，沿用 reserved-root／symlink／realpath fail-closed），final package 只能由
  `.staging-*` 原子 rename 產生，identity 相同 → `already_identical`、不同 → refuse overwrite；⑤instrumentation
  只對既有 `simulate()` 加 **inert trace hook**（`TRACE=None` ＋ `if TRACE is not None` 守衛），trace 關閉時
  aggregate／計算順序／語意不得改變（以 trace off／on 逐欄相等強制），tracing 值**永不**反向參與決策；
  ⑥只 replay **promoted winner cell**（9 grid × survivor；禁止重跑 103,680、禁止呼叫 `summarize()`、禁止寫
  verdict／bundle），且每個 replay aggregate 必須與 frozen terminal-`DONE`-sentinel-pinned
  `artifacts/grid_<grid>.csv` 的 winner row **逐欄相等**，任一欄不符即 fail-closed、不 materialize；⑦ledger
  自身驗證（Σepisode gross／fees／funding／net、episode partition、equity ledger 純 stdlib 重算 Sharpe／MaxDD）
  必須對回 aggregate；⑧`manifest.json` pin 全部引用檔 sha 並以 canonical sha（僅排除 identity 自身與
  `generated_at_utc`）為 `package_identity_sha256`（禁 hash-chain／Merkle），且**必須誠實揭露** ledger 是
  deterministic replay materialization 而非原始 run 保存的 bytes；⑨leaderboard 增加**不參與排序**的
  `evidence_package_status`／`evidence_manifest_path`／`evidence_manifest_sha256`；⑩可執行檢查
  `runtime/tests/test_survivor_evidence.py` 與 `container/scripts/tests/test_survivor_trace.py`（trace
  off／on 相等、ledger reconcile、boundary／symlink／refuse-overwrite、only-leaderboard-entries、coverage
  non-gating、pointer non-ranking、package tamper detection、no fake original-run claim）。`[C]`

- **A30** v1.7.1 reconciler authoritative current attempt（§9.4）：reconcile 的掃描單位為 round，同一 round 內**只有** authoritative current attempt（identity 合法；ordering = `run-spec.json.created_at_utc` 主序 ＋ `uN` 序數數值 tie-break，`u10` > `u9`）可驅動 Kanban 轉換；較舊 attempt 的 terminal sentinel 一律 `superseded` descriptive no-op（不得 unblock／complete／block、不產生 incident）；同一 round 內較新 attempt 的 identity／ordering metadata 缺失、不可解析或歧義（含 timestamp tie 無 `uN` tie-break、task ownership 衝突）一律 fail closed（`kind=attempt_selection_ambiguous` incident，**不得**回退較舊 terminal）；consumed 判定仍先於驗證；authoritative attempt 仍走既有 §9.4 九項檢查與 §12.6 fail-closed。可執行迴歸 `runtime/tests/test_reconcile.py`（**38 檢定**；含舊碼上會失敗的 15 項 round-level 檢定）。`[C]`

- **A31** v1.9.0 reconciler compute-finished wake（§9.2／§9.4／§12.2）：authoritative current attempt **無** terminal sentinel、其 `run-spec.json` 的 `task_id`／`kanban_board` 為 non-empty string、DB 讀回卡片 status == `scheduled`，且 `state.json` 可解析、`stage` ∈ {`ARTIFACT_READY`, `FAILED_SCRIPT`} 時，reconciler **只**執行既有 `scheduled → ready`（父卡未完成則 `todo`）的 `unblock`＝喚醒 default 做 host-side 處置；`ARTIFACT_READY` **不得**自動等同 `DONE`（`[V]` Strategy D r1 u2 為反例），該路徑不得寫 terminal sentinel／`verdict.json`／任何 `/results` artifact、不得判 verdict、不得建 incident（除既有 ownership／read-back 本身不成立）、不得啟動新 run、不得建卡或代 handoff；`RUNNING_*`、無 `state.json`、`stage` 不可解析、`superseded` attempt、非 `scheduled` 卡片、run-spec identity 取不到、card read-back 失敗一律維持既有描述性 `orphan_candidate`（fail-closed、行為不變）；authoritative current attempt selection 與 §9.4 既有九項驗證清單不變；`--dry-run` 只回 `would_unblock`。可執行迴歸 `runtime/tests/test_reconcile.py`（**46 檢定**；新增 8 項，其中 5 項在 v1.8.0 的 `reconcile.py` bytes 上 FAIL／ERROR）。`[C]`

## 23. Auditor checklist（唯讀，逐項打勾）

1. `[C]` 檔案 path 與 A1 相符；無其他檔案被本卡改動（`git status` 或 mtime 核對）。
2. `[C]` 標記三級制存在且未被混用；抽樣 10 段確認事實有出處。
3. `[C]` 對照 kernel 原始碼驗證：`VALID_STATUSES`、`promote_task` 拒絕 `scheduled`、`schedule_task` 清除 worker_pid/claim、`unblock_task` 自 `blocked`/`scheduled` 落 `ready`/`todo`。
4. `[C]` 對照本機實況驗證：`container ls`（qlib-run running、image、6 CPU/4096 MB）、`container --version` 1.4.1、mount 三件套、`/results` 可讀。
5. `[C]` 檢查 A3–A18 逐條是否在文件中可判定；任何缺漏即 FAIL 並指出段落。
6. `[C]` 檢查「禁止事項」與 §1.2 是否一致、有無自我矛盾（例如文件自己引入 queue）。
7. `[C]` 檢查 `[T]` 項目是否被其他段落當成前提使用（若被當前提 → FAIL）。
8. `[C]` 檢查是否有 secrets/PII 洩漏。
9. `[C]` 只給 PASS 或 FAIL；FAIL 必須列 blocking evidence 與具體段落；不做 remediation。
10. `[C]` 全文 cross-reference 檢查：不得殘留「以 task-level metadata 作 durable state」、「對 `scheduled` 卡直接 block」、「`/qlib/work` 必然 shared-layer freeze」三類舊敘述；有殘留即 FAIL 並指出行號。
11. `[C]` **engine 變更的 auditor 可執行驗證（v1.3.2）**：repo commit 的 runner/test bytes 必須同時存在於一個**明確非 production** 的 audit-only staging path（例：host `/Users/hong/workspace/qlib-apple-container/staging/<version>/**` ＋ container `/qlib/work/staging/<version>/**`，自身 `SHA256SUMS` 與 `README`），其 sha256 必須與 repo commit bytes 逐位元一致；auditor 在 `qlib-run` 內以 `/opt/venv/bin/python`（`SA_ENGINE_PATH` 指 staging runner）跑 v1.3.1+ engine tests 並全數通過。staging 不得新增 daemon/service、不得改寫 host `/scripts` 的 frozen A v1 部署副本、不得對 `/results` 產生 production artifacts（§7.2 v1.3.2、§25）。

12. `[C]` **v1.4.0 可執行驗證**：`python3 runtime/tests/test_survivor_bundle.py`（bundle 完整性／順序／冪等／legacy 揭露／fail-closed 負向控制）與 `python3 runtime/tests/test_strategy_b_v2_templates.py`（B v2 preregistration：20 cohorts × strategy domain × DCA domain × 10 phase grids × cohort survivor 語意 × v1.4.0 對映 × 未 launch 聲明）全綠；engine 變更以 audit-only staging（item 11 的同一機制，`staging/v1.4.0/**`，bytes 與 repo commit 逐位元一致）在 `qlib-run` 內以 `/opt/venv/bin/python` 實跑並全數通過；Strategy A v2 round 的 `verdict.json` checksum 必須仍為 `sha256:cb470adf56e9db19c3e6e6bc352177d634d7ecc800b4ceabaa41394c66ad0bf3`（immutable、未回寫），且 `rounds/<round_id>/survivor-bundle.json` 內含**兩個** survivors（`BTCUSDT/1h`、`SOLUSDT/4h`）並標註其在 v1.4.0 語意下皆通過基本 gate。`[C]`
13. `[C]` **v1.4.1 canonical identity recipe 可執行驗證**：auditor 必須**不經** `runtime/survivor_bundle.py` 而以純 stdlib 對 `rounds/<round_id>/survivor-bundle.json` 依 §10.8 的 recipe（移除 `generated_at_utc` 與 `bundle_identity_sha256` 兩欄後的正規化 JSON sha256）重算，且結果必須**逐位元等於**檔案內公開的 `bundle_identity_sha256`；只移除 `generated_at_utc`（v1.4.0 的措辭）必須**得不出**同一值。另必須確認：`python3 runtime/survivor_bundle.py --attempt-dir <attempt> --check --json` rc=0 且 `identity_recipe_matches=true`、`identity_recomputed_from_persisted_file` 等於公開值；`runtime/tests/test_survivor_bundle.py` 含第三方重算與 tamper 負向控制（改量測值但保留舊 digest、改量測值並重算成自洽 digest、改非量測散文）皆 rc=1，且 frozen 檔案的 sha256 與該 round 的 `verdict.json`／`result.json`／`cohort_survivors.json` checksum 在整段驗證前後不變（§10.8/§25）。`[C]`
14. `[C]` **v1.4.2 重跑比對範圍可執行驗證**：auditor 必須在 `/tmp` 複本上（只 rebase `source_attempt_dir` 並以 §10.8 recipe 重簽公開 identity 作為乾淨基線）確認：調換 `--check` 的比對範圍——把 `survivor-bundle.json` 的 `generator.path` 改成任何其他值、**只**用純 stdlib recipe 把公開 `bundle_identity_sha256` 重算成自洽值後，`python3 runtime/survivor_bundle.py --attempt-dir <attempt> --check --json` 必須 rc=1 並回報量測不符，且不帶 `--check` 的 writer 必須 rc=1／`refused_different_bytes`（不得是 `already_identical`）；把 `generator` 換成非 dict 值並重簽亦必須 rc=1（不得被正規化掉）。**對照控制必須仍成立**：只改 `contract` 與 `generator.sha256`（`generator.path` 不變）並重簽，`--check` 必須 rc=0／`check_clean`，writer 必須為 no-op——否則 remediation 會強迫改寫 immutable artifact。frozen 檔案的 sha256 與該 round 的 `verdict.json`／`result.json`／`cohort_survivors.json` checksum 在整段驗證前後必須不變（§10.8/§25）。`[C]`

15. `[C]` **v1.5.0 post-survivor lifecycle 可執行驗證（v1.5.1 收緊後重列）**（唯讀、可在 `/tmp` 複本或真實 `/results` 上重跑）：

- `python3 runtime/tests/test_post_survivor.py` → **33/33 OK**（index deterministic／duplicate／invalid bundle／來源不一致／ownership id 缺漏／param 軸／challenger cutoff、`--out`／`--out-dir`／forward append 越界與 symlink／`..` 逃逸，含 **v1.5.2** 的 `_survivors` 自身被 symlink 與 relative `source_run.attempt_dir` 兩組（同時確認界內子路徑仍可寫）、ownership id 缺漏**或非字串（即使兩側同值）**、forward post-freeze／overlap／params mismatch／欄位完整性／readback／無 `source_run` 或出處不可驗證／數字與 pinned `result.json` 不符、retune 不覆寫、leaderboard deterministic／tie-break／forward 優先／fallback／Top-10 cap／evidence_state／no fake forward、bundle 不被排名改寫）；同一份測試內以**兩名 survivor 的 fixture**（A v2 形狀）驗證 seed 恰兩人。
- `python3 runtime/survivor_index.py --check` → rc=0（index 是既有 frozen bundles 的重建）。auditor 必須自行以純 stdlib 覆核至少一項：`survivor_id` = `"sv-" + sha256(canonical({family_id, round_id, run_id, bundle_identity_sha256, cohort, strategy_params, dca_params}))[:16]`，並確認 `research_data_cutoff` 等於 `<round>/round-spec.json` 的 `data.data_end` **且**該檔 sha256 等於 bundle `source_artifacts["round-spec.json"]`。
- `python3 runtime/survivor_leaderboard.py leaderboard --check` → rc=0，且 `leaderboard.csv` 可逐位元重算（不含時間戳）；`leaderboard.json` 的 `entries`／`top10` 順序可依 §27.5 的 tuple 手算複現。
- **fail-closed 的負向控制**（在 `/tmp` 複本上執行）：拿掉 `bundle_identity_sha256`、或改量測值但保留舊 digest、或把 `family_id` 改成與目錄名不符、或改動 `round-spec.json` 使其 checksum 與 bundle 記錄不符、或對同一 bundle 掃描兩次（duplicate survivor_id）→ `survivor_index.py` 必須 rc=1 且 stderr 逐條給出原因；把一個 forward slice 的 `data_start` 設為 `≤ cutoff`、或與既有 slice 重疊、或改 `params_sha256` → `survivor_leaderboard.py forward` 必須 rc=1 且**不寫入**任何行。
- **v1.5.0 三個 blocking finding 的 regression（v1.5.1）**，可在 `/tmp` 複本或真實 `/results` 上重跑，且必須對 v1.5.0 的 bytes 失敗、對 v1.5.1 全數拒寫：**F1**`survivor_index.py --out <round>/verdict.json`、`--out <round>/survivor-bundle.json`、`--out /tmp/…json`、經 `_survivors` 內的 symlink 或 `..` 段的路徑，以及 `survivor_leaderboard.py leaderboard --out-dir <round>`／`--out-dir <root>` → 全部 rc=1、stderr 為 `outside the reserved post-survivor write boundary`、frozen `survivor-bundle.json`／`verdict.json` 的 sha256 不變；**F2** 一份無 `source_run` 的 slice（無出處、無 artifact/result hash）→ `forward` rc=1 且不寫入 jsonl、leaderboard 該列維持 `FROZEN_ONLY`／`champion_candidate=false`；另有出處但數字與 pinned `result.json.forward_slice` 不符、或出處 run 已被刪除／非 `DONE`／sentinel checksum 偽造／`result.json` 事後被改寫 → 一律 rc=1；**F3** 移除 bundle 的 `kanban_task_id` 並以 §10.8 recipe 重簽公開 identity → `survivor_index.py` rc=1／`source ownership is incomplete`（`family.json` 缺該欄時同樣 rc=1）；**對照控制**：`_survivors/**` 內的正常 index／leaderboard 寫入與 `--check` 必須仍 rc=0，且真人 run 形狀（terminal `DONE` ＋ `result.json.forward_slice`）的 slice 必須被收下。
- **既有 immutable 未改**：`survivor-bundle.json` `sha256:4638885f…`、`verdict.json` `sha256:cb470adf…`、`result.json` `sha256:012e6d1a…`、`artifacts/cohort_survivors.json` `sha256:74f250cf…`；bundle 公開 identity 仍為 `sha256:c051759f…`；`python3 runtime/survivor_bundle.py --attempt-dir <attempt> --check --json` rc=0／`identity_recipe_matches=true`（v1.5.0 的 `CONTRACT_VERSION` 更新屬 §10.8 允許排除的產生者身分欄位，不得使既有 bundle 的 `--check` 失敗）。
- **不得**宣稱有任何 forward evidence 或 `champion_candidate`：`/results/_survivors/forward/`（若存在）必須為空或不存在，`leaderboard.json` 內每一列的 forward metrics 必須為 `null` 且 `evidence_state=FROZEN_ONLY`。證據快照：`evidence/v1.5.0-post-survivor-lifecycle-20260913.json`（v1.5.0 seed）與 `evidence/v1.5.1-post-survivor-boundary-remediation-20260913.json`（v1.5.1 remediation 與 F1／F2／F3 regression 實跑）。**注意**：index／leaderboard 是 derived artifact，內含 `contract` 版本字串，因此換版後必須在 `_survivors/**` 內重建一次才會 `--check` clean（v1.5.1 已重建，`survivor-index.json`／`leaderboard.json` 的 sha256 因此與 v1.5.0 快照不同；`leaderboard.csv` 不含時間戳與版本，逐位元不變）。`[C]`

16. `[C]` **v1.5.2 trust-boundary 收緊可執行驗證**（唯讀、可在 `/tmp` 複本上重跑）：①把 `<root>/_survivors` 換成指向 frozen round 目錄的 symlink → `survivor_index.py --out <root>/_survivors/verdict.json`、`survivor_leaderboard.py leaderboard --out-dir <root>/_survivors`、`survivor_leaderboard.py forward` 三者皆 **rc=1**、stderr 含 `is a symlink` 與 `outside the reserved post-survivor write boundary`，且 frozen round 內**沒有**新增 `leaderboard.json`／`leaderboard.csv`／`forward/`／`survivor-index.json`、`verdict.json` 與 `survivor-bundle.json` 的 sha256 不變；②把 `source_run.attempt_dir` 改成 relative（相對於結果樹）並以結果樹為 cwd 執行 `forward` → **rc=1**／`is not an absolute path`、不寫任何 jsonl；以同一份 slice 的 absolute `attempt_dir` 執行則仍 rc=0（對照控制）；③把 `family.json` 與 bundle 的 `kanban_task_id` 同時設成同一個 JSON number（必要時以 §10.8 recipe 重簽公開 identity 使其自洽）→ `survivor_index.py` **rc=1**／`source ownership is incomplete - kanban_task_id is not a non-empty string on both sides`；空字串／bool／list／null 同理；兩側皆為 non-empty string 且相等時仍 rc=0。全部 7 個測試檔（**130 檢定**）必須全綠。
17. `[C]` **v1.6.0 survivor evidence preservation 可執行驗證**（唯讀；engine 變更沿用 item 11 的 audit-only
staging 機制，bytes 與 repo commit 逐位元一致，不得改寫 host `/scripts` 的 frozen A v1 部署副本、不得對
`/results` 產生非 `_survivors/evidence/**` 的 production artifacts）：
- `python3 runtime/tests/test_survivor_evidence.py` → 全綠（暫存 fixtures 自建，不動真實 `/results`）；
  `container exec qlib-run /opt/venv/bin/python /qlib/work/staging/v1.6.0/tests/test_survivor_trace.py` → 全綠。
- 真實 materialization 重跑：把 repo 的 `20_strategy_a_run.py` ＋ `21_strategy_a_survivor_replay.py` 複製到
  `_survivors/evidence/.staging-*/engine/`，於容器內對**每一個 leaderboard entry** 執行 replay →
  `grid <name> matches frozen row` ×9／survivor，且 `python3 runtime/survivor_evidence.py materialize
  --staging …` → `published`（重跑 → `already_identical`）、`survivor_evidence.py check` → rc=0、
  `coverage` → rc=0 且 PRESENT 數等於 leaderboard entry 數。
- **fail-closed 負向控制**（`/tmp` 複本或自建 fixture）：改動任一 frozen `artifacts/grid_<grid>.csv` 或使其
  與 sentinel checksum 不符 → replay 拒跑（不 materialize）；只把某個 grid 的 replay aggregate 改一欄 →
  `materialize` rc=1；改動已發佈 package 的 ledger 或 `aggregate.csv` → `check` rc=1；`staging` 指向
  `_survivors/evidence/` 之外 → rc=1；把 `_survivors` 換成 symlink → 所有寫入者 rc=1 且 frozen 檔 sha 不變；
  對已存在但 identity 不同的 package 再 materialize → refuse overwrite（`already_identical` 只在 identity
  相同時出現）。
- **non-gating 控制**：把兩個 package 移到 `_survivors/evidence/` 之外後 `leaderboard --check` 必須仍 rc=0 且
  `rank`／`top10`／`evidence_state`／`champion_candidate`／`leaderboard.csv` 的排序欄逐位元不變（只有
  `evidence_package_status` 由 PRESENT 變 ABSENT），而 `coverage` rc=1；移回後 `check` 與 `coverage` 皆 rc=0。
- **only-leaderboard-entries 控制**：對非 leaderboard entry 的 survivor_id（或對一個 candidate/culled cell）
  執行 replay／materialize → rc=1，且 `_survivors/evidence/**` 下**不得**產生任何新目錄；103,680 個 research
  cell 仍只在 `artifacts/grid_*.csv` 有 aggregate row（無 per-cell ledger）。
- **immutable 未改**：`survivor-bundle.json`、`verdict.json`、`result.json`、
  `artifacts/cohort_survivors.json`、`round-spec.json` 的 sha256 在整段驗證前後逐位元不變；`forward/` 仍不存在
  或為空、0 `champion_candidate`；`cron` job `624d0be5b23c` 仍 paused、Strategy B v1 仍 blocked。
- **揭露檢查**：manifest 的 `materialization` 文字必須明文說明「deterministic replay materialization，不是原始
  run 保存的 bytes」；repo 內不得出現任何宣稱原始 run 曾保存逐筆 ledger 的敘述。

18. `[C]` **v1.7.1 reconciler authoritative current attempt 可執行驗證**（唯讀；不得 resume 任何 cron、不得對
`/results` 寫入、不得動 container 中的 B v2 run）：
- `python3 runtime/tests/test_reconcile.py` → **38/38 OK**；並必須自行確認新增的 15 項 round-level 檢定對
  **v1.7.0 的 `reconcile.py` bytes**（`git show <v1.7.0 commit>:runtime/reconcile.py`）會失敗：把該檔與
  `terminal_evidence.py` 複製到自建 `/tmp` 樹、配上本版 test 檔重跑 → 至少 11 項 FAIL（不得全綠）。
- **原 bug 的獨立重現（負向控制）**：自建 `/tmp` fixture（同一 round：`u1` 帶 `FAILED` sentinel 且 checksum／
  boot 皆有效、`u2` 無 terminal 且 `state.json.stage=RUNNING_QLIB`、`u2.created_at_utc > u1`）→ v1.7.1 必須
  `u1=superseded`、`u2=orphan_candidate`、`unblock`／`comment`／incident 全為 0；同一 fixture 對 v1.7.0 bytes
  必須出現 `would_unblock`／`unblocked`（即 production 觀測到的 duplicate wake）。
- **fail-closed 負向控制**：`u1 DONE ＋ u2 run-spec 缺失／不可解析／`created_at_utc` 不合法`、`同 round 兩 attempt
  不同 task_id`、`同一 timestamp 且無 uN tie-break` → 必須 `attempt_selection_ambiguous` incident、
  **零** unblock、零 comment 之外的卡片動作（ownership 衝突時 `kanban_task_id` 無法決定，允許不 comment）。
- **tie-break 控制**：`u9 FAILED ＋ u10 RUNNING` 且同一 `created_at_utc` → `u10` authoritative（證明非字典序）；
  `u1`／`u2` 同 timestamp → 數值序數決定；不同 round／不同 family 的較新 attempt **不得** supersede 別 round 的
  terminal。
- **production 讀回（唯讀）**：fence-free context 執行 `python3 runtime/reconcile.py --dry-run --json` →
  `would_unblock=[]`、`unblocked=[]`、`incidents=0`；B v2 `…-r1-u1` 為 `superseded`（
  `authoritative_run_id=…-r1-u2`）、`…-r1-u2` 為 `orphan_candidate`；`t_35b3e5da` 未出現任何 unblock 動作。
  並以自建 snapshot 比對 B v2 attempt tree 前後 sha／size：除 u2 仍在寫入的 `artifacts/grid_*.csv` 外必須
  逐位元不變（`run-spec.json` `b20a8251…`、`state.json` `c8465714…`、`round-spec.json` `a3dd33a9…`、
  `family.json` `63ee6cb8…`、u1 全部檔案）。
- **邊界讀回**：`hermes cron list --all` → reconciler `f6b9aa5e9034` 仍 `paused`／`enabled=false`、
  handoff `624d0be5b23c` 狀態與 v1.7.1 交付時一致；board 上**無** Strategy C／D／E 真卡；未新增
  daemon／service／DB／queue／registry；`git status` 乾淨且 `origin/main` 等於受稽核 commit。

19. `[C]` **v1.9.0 compute-finished wake 可執行驗證**（唯讀；不得 resume 任何 cron、不得對 `/results` 寫入、不得動 container）：
- `python3 runtime/tests/test_reconcile.py` → **46/46 OK**；並自行確認新增的 8 項中至少 5 項對 **v1.8.0 的 `reconcile.py` bytes**（`git show <v1.8.0 commit>:runtime/reconcile.py`）會失敗（`2 FAIL ＋ 3 ERROR`，不得全綠）；另 3 項是「行為不變」守衛（`RUNNING_QLIB` 仍 report-only、`stage` 不可解析、superseded 不喚醒），依設計在兩版皆綠。
- **負向控制（自建 `/tmp` fixture ＋ 注入式 board／card 讀回）**：`ARTIFACT_READY`＋`scheduled` → dry-run `would_unblock`（0 次 board 動作）與 real `unblock`（read-back `ready`），且 attempt 目錄**不得**出現任何 terminal 檔；`FAILED_SCRIPT`＋`scheduled` 同上；卡片 `done`／其他非 `scheduled` → `orphan_candidate`（0 unblock、0 incident、0 comment）；無 `state.json`／`stage` 不可解析 → `orphan_candidate` 且 `detail` 無 wake 標記；`run-spec.json` 缺 `task_id`／`kanban_board` 或 card read-back 失敗 → `orphan_candidate` 且 `detail.wake` 明示 fail-closed；superseded 的 completion-pending attempt → `superseded`，**不得**喚醒。
- **production 讀回（唯讀）**：fence-free context `python3 runtime/reconcile.py --dry-run --json` → 與 v1.8.0 bytes 的同一 dry-run **逐列相同**（`attempts_scanned=16`、`incidents=0`、`unblocked=[]`、`would_unblock=[]`）；Strategy D `…-r1-u2`（`ARTIFACT_READY`、無 sentinel）仍為 `orphan_candidate`（其卡 `t_50c28da5` 已 `done`）。現行樹上沒有任何 attempt 該被喚醒——出現 `would_unblock` 非空即為誤喚醒，必須 FAIL。
- **邊界讀回**：`hermes cron list --all` → handoff `624d0be5b23c` = active／`5,35 * * * *`、reconciler `f6b9aa5e9034` = paused；worker 未執行任何 cron 動作；`git status` 乾淨且 `origin/main` 等於受稽核 commit；未新增 service／daemon／cron／job／manager、未改 `runtime/production_handoff.py`、未碰任何 strategy engine／parameter／DCA／backtest artifact。

## 24. Worked examples

### 24.1 例：A `REJECT` 之後放行 B
```
A 卡 (family-close-vs-sma-v1)  t_A
  r1/u1 → FAILED(script_bug)      → 修腳本
  r1/u2 → DONE (verdict_hint CANDIDATE_REJECT)
  r2/u1 → DONE (falsification 觸發)
  → yield: rounds_used=2 < max_rounds=3, progress=yes(否證成立)
  → verdict=REJECT, performance_claimable=false
  → kanban_complete → A done

B 卡 (parents=[t_A]) 本在 todo → 父卡 done → 自動 ready → dispatcher claim
```
- `[C]` 過程中沒有 promote、沒有 second run 重跑 r1/u2、沒有因 A 失敗而 freeze。

### 24.2 例：partial write
```
/results/F/rounds/F-r1/attempts/F-r1-u1/
  run-spec.json  (存在)
  result.json    (存在)
  artifacts/summary.csv (存在)
  DONE           (缺失)
  state.json     stage=RUNNING_QLIB
```
- `[C]` reconciler 不放行；判 `partial_write`。
- `[C]` default 決策：因無 terminal 且屬 card-local → 補 `INCOMPLETE`（記錄 partial_write）→ 同 round 新 run_id `F-r1-u2`。
- `[C]` 不得把 `result.json` 當成合格結論，因為 sentinel 未 terminal publish。

### 24.3 例：Mac reboot mid-run
```
reboot 後：container qlib-run 不存在（或新 container id）
attempts/F-r1-u1/ 無 sentinel、stage=RUNNING_QLIB
boot id 與 sentinel（無）不符
```
- `[C]` 判 orphan（`orphaned_run`）→ 補 `INCOMPLETE` → 卡片自 `scheduled` unblock 到 `ready`。
- `[C]` default 重跑 → **同 round 新 run_id** `F-r1-u2`（不遞增 round_id）。
- `[C]` 若 reboot 後 `qlib-run` 不存在，先走 preflight P4–P6；無法安全恢復才 freeze。

### 24.4 例：shared-layer freeze
```
/Volumes/ExpansionDrive 未掛載（P1 FAIL）
```
- `[C]` freeze：chain head 卡 `blocked(kind=capability)`；其餘 `ready` 卡回 `todo`；不新增 family。
- `[C]` 解除：掛載回來 → P1–P10 全綠 → `unblock` 放行；不得用 promote。

### 24.5 例：下游 authoritative acceptance（future／out-of-scope，非現行 production 路徑）
```
round verdict PASS（>=1 cohort survivor；v1.3.x 已凍結的 FINALIST round 保持原語意與 checksum）
  → rounds/<round_id>/survivor-bundle.json（§10.8：ALL survivors、identity sha256 釘死、ranking=null）
  → 下一個 family 立即 ready
                                        ↘ （future，可選）另開 acceptance 卡（assignee 依屆時契約；
                                           必須逐一處理 bundle 內全部 survivor，不得挑選其中一個）
```
- `[C]` 現行 production 在 FINALIST 之後**只需要**放行下一個 family；acceptance 階段不存在也不影響任何 verdict 或 claim。
- `[C]` 未來若真的開卡：acceptance 卡只能驗證 frozen survivor；若其結果 `DISPUTED`，不得回頭改 F-r* 的 artifacts（走 §26 / §17）。

### 24.6 例：v1.3.0 cohort survivor disposition（20 cohorts × 12 strategy × 48 DCA）

```
family close-vs-sma-mean-reversion-long-flat-v2   round r1 / run u1
  registered: 4 symbols x 5 timeframes = 20 cohorts
              strategy domain  window{20,50,100,200} x discount{0.01,0.02,0.03} = 12
              DCA domain       spacing{0.01,0.02,0.03,0.04} x size_mult{1.0,1.1}
                               x breakeven_tp{0.01,0.02,0.03} x invalidation{0.05,0.10} = 48
  per cohort per phase grid: 12 x 48 = 576 cases; 9 phase grids => 11,520 cases / grid
  expected case evaluations: 11520 x 9 = 103,680

  cohort ETHUSDT/4h
    selector (historical only)   -> winner (window 20, discount 0.02, spacing 0.02,
                                            size 1.1, tp 0.02, invalidation 0.05)
    OOS      net_pnl +   sharpe +    -> b) pass
    full     net_pnl +               -> c) pass
    fee_2x / funding_2x / entry_delay_1_bar / slippage_2ticks net_pnl all + -> d) pass
    neighbourhood 9 of 11 legal neighbours same sign = 0.818 >= 0.60 -> e) pass
    => cohort outcome SURVIVOR

  cohort BTCUSDT/5m
    selector -> winner; OOS net_pnl -  -> cull_reasons ["oos_economic"] => CULLED (only this cohort)

  family disposition (count of SURVIVOR cohorts)
    0 => REJECT / NO_SURVIVOR       (verdict REJECT, performance_claimable false)
    1 => SURVIVOR_FOUND             (verdict PASS; performance_claimable still needs all of 9.6)
    >1 => MULTIPLE_SURVIVORS        (verdict PASS as well: >=1 survivor passes the basic gate;
                                     EVERY survivor is frozen in the survivor bundle and advances;
                                     the count never forces performance_claimable false)
    coverage incomplete => TECHNICAL_INCOMPLETE (no cohort is judged at all)

  round level (v1.4.0) => rounds/<round_id>/survivor-bundle.json
    {survivor_count: 2, disposition_band: MULTIPLE_SURVIVORS, verdict: PASS,
     ranking: null, all_survivors_advance: true, survivors: [ALL of them, in recorded order],
     source_artifacts: {cohort_survivors.json: sha256:…, result.json: sha256:…, DONE: sha256:…}}
```
- `[C]` 過程中沒有「跨 20 cohort median 未過 → 整個 family REJECT」這條路徑；median 只出現在 `descriptive_diagnostics`（`non_gating: true`）。
- `[C]` 沒有 OOS 選參、沒有看過 OOS 後換組、沒有因為某個 timeframe 全滅而縮小 eligible universe。
- `[C]` `verdict.json` 由 default 依 §10.7 寫入；runner 的 `verdict_recommendation` 只是建議。
- `[C]` v1.4.0：survivor bundle 由 default 依 §10.8 從該 attempt 的既有 artifacts 生成（`runtime/survivor_bundle.py`），**不**改寫任何既有 immutable artifact；順序即 run 的記錄順序，不是排名。

## 25. 禁止事項（硬規則）

- `[C]` 禁止用 `blocked` 當 Qlib 等待狀態。
- `[C]` 禁止用 `promote` 放行 `scheduled` 卡（kernel 亦拒絕）。
- `[C]` 禁止在 Qlib 等待期間長 turn 佔用 worker。
- `[C]` 禁止引入 HTTP server / webhook / Redis / Celery / RabbitMQ / queue manager / 新 daemon。
- `[C]` 禁止讓 container 取得 Hermes credentials、Kanban DB 寫入能力、host control socket。
- `[C]` 禁止寫入 `/data/raw`。
- `[C]` 禁止把 `/qlib/work` 當真值來源或要求它不可重建。
- `[C]` 禁止重跑已有 terminal sentinel 的 run。
- `[C]` 禁止讓卡片永遠停在 `scheduled`（失敗 attempt 也必須有 terminal evidence）。
- `[C]` 禁止一卡一 experiment；禁止一卡多 family。
- `[C]` 禁止把 Kanban parent edge 當 scientific lineage 或 approval。
- `[C]` 禁止在鏈中插卡、禁止 live rewiring 既有 parent edge。
- `[C]` 禁止 card-local failure freeze 全鏈；禁止 shared-layer failure 只影響單卡。
- `[C]` 禁止無界 REFINE（必須適用 §15 停止政策）。
- `[C]` 禁止用「跨 cohort median PnL/Sharpe」作為 family 的 REJECT（或任何）gate；跨 cohort 統計只能是標示 `non_gating` 的 descriptive diagnostic（§7.2）。
- `[C]` 禁止以單一條固定 DCA rail 當成 full-backtest 的 DCA 維度；DCA parameter domain 必須完整註冊並逐組實算（§7.2）。
- `[C]` 禁止把被搜尋的 DCA 軸、或沒有明確 operator 固定證據的常數（如 `base_quote`）標成 user-fixed invariant；被搜尋的軸或 project 常數一律標 `PROJECT_PRE_REGISTERED_SEARCH_DOMAIN` / `PROJECT_PRE_REGISTERED_CONSTANT`（§7.2 v1.3.1）。
- `[C]` 禁止把 taker fee 只累積成統計量而延後到 episode 結束才扣（或根本不扣）realised equity；成本必須在每個 fill 時點扣入，成本壓力軌不得成為 no-op（§7.2 v1.3.1 per-fill 成本會計）。
- `[C]` 禁止把 `gross_pnl` 由 net 反向回推（`net + fees + funding` 型恆等式）或由任何成本統計量導出；gross 只能來自獨立的 price-PnL accumulator（§7.2 v1.3.2 獨立 gross PnL 會計）。
- `[C]` 禁止覆蓋 host `/scripts` 的 frozen A v1 部署副本（舊 A run-spec pin 它、不得 live rewire）；engine 變更的 auditor 可執行驗證一律走 audit-only staging path，不得對 active deploy 或 `/results` 產生任何 production 效果（§23）。
- `[C]` 禁止把已 operator-stopped 的 strategy runner/test 留在 active runtime 路徑（`container/scripts/**` 或 host `/scripts` 部署目錄）；必須 exact bytes 移到 archive-only evidence 路徑並保留 checksum（§13 operator_stopped archive hygiene）。
- `[C]` 禁止在 candidate card body 缺少 DCA parameter domain 或 cohort survivor rules 時 append 卡片（§14.4；`candidate_body_not_v13`）。
- `[C]` 禁止任何 retired／secondary engine 重新搜尋參數、改 hypothesis、成為第二套全量 search engine，或被重新解讀成現行 production gate（§17）。
- `[C]` 禁止新增 Manager / Service / Factory / Registry 類抽象。
- `[C]` 禁止以「缺下游 authoritative acceptance」為由把現行 Qlib full-backtest 降級為 `research-only`，或把它列為第一張 production card 的 gate。
- `[C]` 禁止把 comment 當唯一 state。
- `[C]` 禁止把 task-level metadata 當 state（`[V]` kernel 的 `tasks` schema / `Task` model 無 `metadata`；`task_runs.metadata` 屬 closing run，不是 card runtime state）。
- `[C]` 禁止對 `scheduled` 卡直接 `block`（`[V]` kernel 亦拒絕）；衝突一律走 §12.6 incident。
- `[C]` 禁止把單一 work volume（`/qlib/work`）故障當 shared-layer freeze（§12.5）。
- `[C]` 禁止把 `[T]` 條目當作已具備能力引用。
- `[C]` 禁止同一張卡自我審計。
- `[C]` 禁止把「survivor 數量 >1」當成 `performance_claimable=false`（或降級為 `research-only`）的理由（§7.3/§9.6）。
- `[C]` 禁止對 round 的 survivors 排序、排名、淘汰或二選一：全部 survivor 必須完整凍結於 `rounds/<round_id>/survivor-bundle.json` 並全部前進（§7.3/§10.8）。
- `[C]` 禁止改寫已凍結的 `survivor-bundle.json`，或由非 terminal `DONE`／`coverage_complete≠true`／assertions 含 false 的 attempt 產生 bundle（§10.8）。
- `[C]` 禁止以任何與 §10.8 canonical identity recipe 不同的方式定義或宣稱 `bundle_identity_sha256`——特別是**不得**把 `bundle_identity_sha256` 自身、`generated_at_utc` 以外的欄位排除在雜湊之外（隱含欄位排除使 auditor 無法獨立重算），也**不得**把它定義成無法由持久化檔案重算的自我包含 digest（§10.8）。
- `[C]` 禁止在重跑比對（`--check` 與 writer 的量測內容比較）中排除比 §10.8 所列更寬的範圍——特別是**不得**整包排除 `generator` 物件：產生者身分欄位只能逐鍵排除頂層 `contract` 與巢狀 `generator.sha256`，`generator.path` 與非 dict 的 `generator` 必須留在比對內，否則「改 `generator.path` 並把公開 identity 重算成自洽值」會被 false accept（§10.8/§10.8 重跑比對範圍；v1.4.1 的 F2 缺陷）。
- `[C]` 禁止把 v1.3.x 已凍結的 artifacts（含 Strategy A v2 round r1 的 `verdict.json`）回寫成 v1.4.0 語意；語意變更一律以新 round／新 family 落地，並在 bundle／verdict 內揭露來源語意（§7.3/§10.8/§26）。
- `[C]` 禁止為 post-survivor lifecycle 新增 Registry／Orchestrator／service／daemon／queue：survivor index、forward evidence 與 leaderboard 一律只以 `/results/_survivors/**` 的檔案 artifact 落地，且永遠是 derived／rebuildable（§27.1/§27.2）。
- `[C]` 禁止把 leaderboard 當成新的 PASS/REJECT gate，或把「掉出 Top-10」當成 REJECT、把 `evidence_state` 回寫成 verdict；`champion_candidate` 只是 research shortlist 標示，v1.5 不得據以發實盤訊號或配置資金（§27/§27.5/§27.6）。
- `[C]` 禁止在 forward evidence 中改動 survivor 的 strategy／DCA 參數、把 `data_start ≤ research_data_cutoff` 或與已記錄 slice 重疊的窗口當成 unseen evidence；改參數者一律走 challenger family（含 preregistration cutoff 檢查）並重走完整 full-backtest gate（§27.3/§27.4）。
- `[C]` 禁止偽造 forward evidence：沒有真實的 post-freeze 計算結果時，正確狀態是 zero-forward（`FROZEN_ONLY`）；不得寫入推估值、回填研究期資料、或以任何方式讓 leaderboard 顯示不存在的 forward 指標（§27.3/§27.5）。
- `[C]` 禁止讓本層（§27）把任何 artifact 寫到 `/results/_survivors/**` 之外——特別是**不得**以 `--out`／`--out-dir`（或 symlink、`..` 段）覆寫任何 `<family_id>`／round／attempt 目錄的檔案，含 frozen `survivor-bundle.json`、`verdict.json`、`result.json`；工具必須以**兩側 realpath 解析後的邊界檢查** fail-closed，越界即拒寫（§27.1；v1.5.0 的 F1 缺陷）。
- `[C]` 禁止以「slice 自己宣告的數字」當成 forward evidence：每個 slice 必須可由**真實 run artifact**重新驗證（結果樹內、非 `_survivors` 的 attempt 目錄 ＋ terminal `DONE` sentinel ＋ sentinel 記錄且與磁碟相符的 `result.json` checksum ＋ 與 slice 逐欄相等的 `result.json.forward_slice`），且該出處必須在**寫入時與讀取／排名時都**重新驗證；無法驗證者一律拒收、不得排名（§27.3/§27.5；v1.5.0 的 F2 缺陷）。
- `[C]` 禁止在 `kanban_task_id` 缺漏時放行：bundle 或 `family.json` 任一方缺 ownership id 即視為**來源不一致**並 fail-closed（不得只在兩側皆為 truthy 時才比對）（§27.2/§22 A28；v1.5.0 的 F3 缺陷）。
- `[C]` 禁止讓 forward computation 成為第二套 backtester／第二套 parameter search：它必須重用現行 strategy／Qlib execution semantics（§27.3）。
- `[C]`（v1.5.2）禁止在 reserved root 是 symlink（或其 resolved 路徑不等於 resolved results root 之下的 literal `_survivors`）時寫入：這不是慣例而是 fail-closed 前置條件，`--out`／`--out-dir`／`forward` append 都必須在任何寫入前回 rc=1（§27.1；v1.5.1 的 F1 殘留）。
- `[C]`（v1.5.2）禁止把 relative `source_run.attempt_dir` 當成可驗出處：出處必須是 absolute path，否則同一份 slice 的「可驗證性」取決於執行者的 cwd（§27.3；v1.5.1 的 F2 殘留）。
- `[C]`（v1.5.2）禁止以非字串的 `kanban_task_id` 建立 ownership：bundle 與 `family.json` 兩側皆必須是 non-empty string 且逐字相等，數字／布林／list／null／空字串即使兩側同值也一律 fail-closed（§27.2 第 4 項／§22 A28；v1.5.1 的 F3 殘留）。
- `[C]`（v1.6.0）禁止把 evidence preservation 的觸發點設成 Top-10 或 PASS gate：觸發點只能是
  `leaderboard.json` 的 `entries` 成員（§28.1）。
- `[C]`（v1.6.0）禁止為未 promoted 的 cell 產生逐筆 execution ledger：103,680 個 research evaluation 與所有
  candidate／culled cell 一律只保留 aggregate 摘要（§28）；per-cell ledger 的存在必須由「是否為 leaderboard
  entry」決定。
- `[C]`（v1.6.0）禁止讓 evidence package 的存在與否回寫 verdict、`performance_claimable`、`evidence_state`、
  `champion_candidate` 或 ranking，也禁止把 `evidence_package_status` 放進排序 tuple（§28.1／§28.5）。
- `[C]`（v1.6.0）禁止讓 instrumentation 改變既有執行語意：trace 關閉時 aggregate／計算順序／語意必須逐欄不變，
  且任何 traced 值不得反向參與策略決策或會計（§28.2）。
- `[C]`（v1.6.0）禁止在 replay 中複製 `simulate()` 邏輯、呼叫 `summarize()`、或重跑 103,680 次 evaluation；
  只准 replay promoted winner cell 並重用同一顆 engine（§28.3）。
- `[C]`（v1.6.0）禁止在任一欄與 frozen `artifacts/grid_*.csv` 不符時 materialize package，或宣稱原始 research
  run 曾保存逐筆 ledger（誠實揭露 deterministic replay materialization，§28.4）。
- `[C]`（v1.6.0）禁止在 `_survivors/evidence/**` 之外寫入，或就地覆寫一個 identity 不同的既有 package
  （§28.4 寫入邊界與 atomic publish）。
- `[C]` 禁止在 comment/artifact 或任何卡片欄位寫入 secrets 或 PII。
- `[C]`（v1.7.1）禁止以非 authoritative current attempt 的 terminal evidence 放行卡片：同一 round 內已被較新有效 attempt supersede 的較舊 attempt 一律 descriptive no-op，不得 unblock／complete／block，亦不得產生 incident／comment。
- `[C]`（v1.7.1）禁止以 run_id 字串字典序判斷 attempt 新舊（`u10` > `u9`；ordering 必須以 `run-spec.json.created_at_utc` 為主序、`uN` 序數為 tie-break）；亦禁止在 authoritative current attempt 無法判定時回退到較舊 attempt 的 terminal（一律走 §12.6 incident）。
- `[C]`（v1.9.0）禁止把 compute-finished stage（`state.json` stage=`ARTIFACT_READY`／`FAILED_SCRIPT`）當成 terminal 或等同 `DONE`：它不是 verdict，不能取代 terminal sentinel（`DONE`/`FAILED`/`INCOMPLETE`）；reconciler 對它的唯一合法動作是**既有** `scheduled → ready`（父卡未完成則 `todo`）的 `unblock`＝喚醒 default 做 host-side 處置，且不得寫 terminal sentinel／`verdict.json`／任何 `/results` artifact、不得判 verdict、不得建 incident（除既有 ownership／read-back 本身不成立）、不得啟動新 run、不得建卡或代 handoff（§9.2/§9.4 v1.9.0/§12.2）。
- `[C]`（v1.9.0）禁止放寬 compute-finished wake 的 fail-closed 條件：`RUNNING_*`、無 `state.json`、`stage` 不可解析、`superseded` attempt、卡片非 `scheduled`、run-spec identity 取不到、card read-back 失敗一律不得 wake；亦禁止在 worker 卡內操作 cron（`hermes cron run`／`edit`／`enable`／`resume`）或自行 resume reconciler `f6b9aa5e9034`（§9.4 v1.9.0；啟停一律由 ChatGPT/operator 決定）。

## 26. Change control

- `[C]` 修改本文件需滿足其一：(a) operator 明確指示；(b) ChatGPT 規劃卡明示；(c) auditor 提出 blocking evidence 後的 default remediation（且需 re-audit）。
- `[C]` 版本遞增規則：條款新增/語意變更 → minor 版 ++；狀態機、trust boundary、completion bridge 變更 → major 版 ++ 並在文件頂端記錄 supersede 說明。
- `[C]` 每次變更必須在文件末尾的變更記錄留下：版本、日期、變更條號、理由、驗證方式。
- `[C]` 不得為了文采/語序順暢而重寫固定段落：本文件的前置固定段（§0–§5）視為凍結前綴；新增優先追加於對應段落尾端或新章節。
- `[C]` 變更後必須重跑 §22 的 A1–A18 自檢與 auditor checklist（§23）。

### 26.1 一次性 additive schema migration 例外（v1.8.0；僅限本例外）

- `[C]` **授權來源**：ChatGPT（GPT-5.6 Sol）規劃卡 `t_67481d49`（§26 (b)），2026-09-14；operator 已指示繼續 Strategy B，其 production 卡 `t_35b3e5da` 因 r1-u3 launch gate fail-closed 而停住。
- `[C]` **為什麼需要本例外**：v1.8 把 family parameter contract 定義為 **frozen round-spec 的屬性**（`runtime/parameter_contract.py` 讀 `spec["parameter_contract"]`；只有 pre-schema 的 Strategy A v2 有 in-code `LEGACY_A_CONTRACT` bridge），因此任何 **pre-v1.8 已凍結**的 non-legacy round-spec 會在整個 compute 之後的 post-survivor 消費鏈上 fail closed（§27／§28 全部拒絕）。要讓該 round 的 u3 合法前進，只能對該檔做本例外允許的 migration。
- `[C]` **例外範圍（一次性、單檔、additive only）**：僅授權 family `ema-crossover-walkforward-momentum-long-short-v2`、round `ema-crossover-walkforward-momentum-long-short-v2-r1` 的 `round-spec.json` 進行**一次**migration，且**只新增一個 top-level key `parameter_contract`**：
  - 該 contract 必須**完全由該 round-spec 自身已註冊的 `parameter_domain`／`dca_domain` 生成**（composite 軸 `ema_pair`→`[ema_fast, ema_slow]`、`walk_forward`→`[wf_train_days, wf_test_days]`；DCA 四軸 `spacing_pct`／`size_multiplier`／`breakeven_tp_pct`／`invalidation_pct`；`domain_cardinality`={strategy:120, dca:48, per_cohort:5760}），且 `family_id` 與該 round-spec 一致；
  - 除該 key 外，所有既有 top-level／nested key／value 的 canonical JSON 必須**完全相同**；`hypothesis`／`eligible_universe`／data split／20 cohorts／120 strategy cases／48 DCA configs／10 phase grids／1,152,000 case evaluations 等科學欄位不得改動；u1／u2 attempt artifacts 與其他所有 frozen artifact 一律不動；
  - migration 前後 sha256、field-preservation proof（byte 級與 canonical 級）、`parameter_contract.validate_contract` 與 `parameter_contract.validate_round_spec_contract` = 0 problems、`strategy_b_v2_counts.py` 的 fingerprint MATCH，必須落在**獨立 evidence 檔**，並再由 **auditor profile 的獨立唯讀 re-audit** 驗證（default 不得自審）；**auditor PASS 之前不得 launch u3**。
- `[C]` **不放寬 INV-4**：本例外**只**涵蓋上述單檔、一次性、additive 的 migration。INV-4（`family.json`／spec／result／verdict／terminal evidence 一旦 publish 即 immutable）對其他**所有**檔案完全不變；本例外**不構成先例**——任何後續同類需求（其他 family／其他 round／任何非 additive 的改寫）都必須重新走 §26 並另行授權。
- `[C]` **未來 instantiation 的義務（同一變更的一部分）**：B（以及未來任何 generic family）的 round-spec template 必須**內建** `parameter_contract`；`runtime/preflight.py` 的 launch gate（§16.2 P10）與 `runtime/instantiate_strategy_b_v2.py` 必須在**任何 compute／publish 之前**呼叫 `parameter_contract.validate_round_spec_contract`，缺少 schema 的 non-legacy round-spec 一律 fail closed（A v2 走 in-code bridge，行為不變；P9／P10 的既有欄位檢查不變）。

---

## 27. Post-Survivor Lifecycle：forward evidence / survivor index / leaderboard（v1.5.0；v1.5.1 收緊：`_survivors/**` 寫入邊界改為工具層強制、slice 新增 `source_run` 出處契約、ownership id 缺漏 fail-closed）

- `[C]` **正式流程**：Full Backtest → frozen survivor bundle（§10.8）→ Forward Evidence（§27.3）→ Survivor Leaderboard（§27.5）→ Champion Candidate / Challenger（§27.4／§27.6）→ future Paper/Testnet → future Live Candidate Selection。本層完全位於 §7.3 的 family gate **之後**，且**不改變任何既有 PASS/REJECT**：verdict 仍只由 §7.3／§9.6 決定，已凍結的 `verdict.json` 與 `survivor-bundle.json` **永不**因排名或新 evidence 而改寫。
- `[C]` survivor 的 strategy params 與 DCA params 一律 frozen；forward evaluation **不得**改參數、**不得**做 parameter search、**不得**做 optimization。
- `[C]` 新資料只**增加** evidence（append-only）；既有 PASS／verdict／bundle 不因新資料改變。
- `[C]` Top-10 是 **ranking/selection aid**，不是新的 PASS/REJECT gate：掉出 Top-10 **不等於** REJECT，也不改變 `performance_claimable`。
- `[C]` 本層不新增 service／daemon／queue／Registry／Orchestrator：index、forward evidence、leaderboard 全部是**檔案 artifact**，由純 stdlib、可重跑的 CLI 產生（`runtime/survivor_index.py`、`runtime/survivor_leaderboard.py`），落點在 `/results` 之下，不進 container、不寫 Kanban。

### 27.1 落點與權威性

```
/results/_survivors/
  survivor-index.json                # derived/rebuildable：掃描既有 frozen bundles 而生成（§27.2）
  leaderboard.json / leaderboard.csv # derived/rebuildable：index + forward evidence 的排名（§27.5）
  forward/<survivor_id>.jsonl        # append-only：每個 survivor 的 forward slices（§27.3）
```

- `[C]` `_survivors`（以及既有 `_incidents`／`_handoff`）是**保留命名空間**，永不視為 `family_id`；掃描時 `_` 前綴目錄一律跳過。
- `[C]` **source of truth 仍是 frozen survivor bundle**（`rounds/<round_id>/survivor-bundle.json`，§10.8）。index 與 leaderboard 是 derived artifact：任何時候都必須能由 bundles ＋ forward jsonl 重建；`--check` 不一致即 fail-closed 要求重建（不得就地手改）。
- `[C]` 本層對 `/results` 的寫入**只限**上述 `_survivors/**`；不得寫入任何 `<family_id>`、round 或 attempt 目錄。
- `[C]` 這條邊界是**工具層強制**，不是慣例：`runtime/survivor_index.py --out` 與 `runtime/survivor_leaderboard.py leaderboard --out-dir` 都必須把候選路徑與 `<results-root>/_survivors` **兩側先做 realpath 解析**再比對前綴，不在界內即 rc=1 且**不寫入任何檔案**（symlink 與 `..` 段因此都無法導向 frozen `survivor-bundle.json`／`verdict.json`／`result.json`）。**界內的子路徑仍允許**（此檢查不是「一律拒絕」）。`[C]`（v1.5.2）**reserved root 自身也在檢查範圍內**：`<results-root>/_survivors` 本身必須**不是 symlink**，且其 resolved 路徑必須**恰為** resolved results root 之下的 **literal `_survivors` 子目錄**（`os.path.realpath(<results-root>)` 加 `/_survivors`）；任何 root／ancestor escape 一律 rc=1，且必須在**任何寫入動作之前**（含 `os.makedirs()`）即拒寫。此檢查適用於本層**所有**寫入者：`survivor_index.py --out`（含預設路徑）、`survivor_leaderboard.py leaderboard --out-dir`、以及 `survivor_leaderboard.py forward` 的 append（`_survivors/forward/<survivor_id>.jsonl`）。理由（v1.5.1 的 F1 殘留）：兩側 realpath 的對稱前綴檢查在 **boundary 本身被 re-point** 時失效——把 `_survivors` 換成指向 frozen round／attempt 目錄的 symlink，realpath 會把 boundary 解析成那個 frozen 目錄，於是`--out <root>/_survivors/verdict.json` 以 rc=0 覆寫 frozen verdict、`--out-dir <root>/_survivors` 在 frozen round 內寫出 leaderboard、`forward` 在 frozen round 內建立 `forward/`。`_survivors/**` 內的檔案（index／leaderboard／forward jsonl）是 derived artifact，可重建、可覆寫；界外的任何 artifact 永不由此層產生或改寫（v1.5.1 起；v1.5.0 的 F1 缺陷為未受約束的 `--out` 曾以 rc=0 覆寫 frozen bundle 與 verdict）。`[C]`

### 27.2 File-only survivor index（`survivor-index.json`）

- `[C]` 產生器：`runtime/survivor_index.py`（純 stdlib、deterministic、host 端；**不是** service／daemon／queue）。
- `[C]` **survivor_id 必須 deterministic**，且至少釘住 `family_id`、`round_id`、`run_id`、`bundle_identity_sha256`、`cohort`、strategy params、DCA params：

  ```
  survivor_id = "sv-" + sha256(canonical({family_id, round_id, run_id,
                                          bundle_identity_sha256, cohort,
                                          strategy_params, dca_params}))[:16]
  ```

  canonical 序列化與 §10.8 同一 recipe（`sort_keys=True, separators=(",", ":"), ensure_ascii=False`）。同一 frozen survivor 在任何主機、任何次重建都得到同一 id；改了參數的 cell（§27.4 challenger）**不可能**與其 incumbent 撞 id。
- `[C]` param cell 必須**恰好**是註冊軸：strategy = `window`／`discount`，DCA = `spacing_pct`／`size_multiplier`／`breakeven_tp_pct`／`invalidation_pct`（§7.3 的 winner cell）。缺軸或出現註冊軸以外的鍵一律 fail-closed（不得忽略、不得推測）。
- `[C]` `research_data_cutoff` 取自該 round 的 `round-spec.json` 的 `data.data_end`，且**只有在該檔仍等於 bundle `source_artifacts["round-spec.json"]` 所記 checksum 時才接受**；否則 fail-closed（cutoff 是「什麼算 unseen」的判準，不得來自未驗證來源）。
- `[C]` **fail-closed 清單**（任一成立即拒建索引，並在 stderr 逐條列出）：
  1. bundle 缺 `bundle_identity_sha256`（**缺 checksum**）；
  2. bundle 公開的 identity ≠ §10.8 recipe 對 bundle 自身的重算值（**bundle invalid**）；
  3. `source_artifacts` 缺漏或 checksum 格式不合法；
  4. bundle 的 `family_id`／`round_id` 與其所在目錄名不一致、`kanban_task_id` 與 `family.json` 不一致**或任一方缺漏**（缺漏即來源不一致：只在兩側皆為 truthy 時才比對會讓「移除 bundle 的 ownership id 並重簽 identity」被 false accept；**v1.5.2 再收緊**：兩側皆必須是 **non-empty string** 且逐字相等，int／bool／list／null／空字串一律 fail-closed，**即使兩側值相同**——同一個 JSON number 在 bundle 與 `family.json` 相等仍不是 ownership provenance，且因為 identity 可自洽重簽，checksum 檢查不會攔下它）、或缺 `family.json`（**來源不一致**）；
  5. `round-spec.json` 與 bundle 所記 checksum 不符（**來源不一致**）；
  6. param cell 缺軸或有註冊軸以外的鍵；
  7. 兩個項目得到同一個 `survivor_id`（**duplicate**：拒絕，不 merge、不覆寫）；
  8. `verdict=PASS` 但 bundle 內無 survivor。
- `[C]` 索引項至少記錄：`survivor_id`、family／round／run／`kanban_task_id`、`cohort`（`symbol`／`timeframe`）、`strategy_params`、`dca_params`、`params_sha256`、`research_data_cutoff`、bundle 路徑／檔案 checksum／公開 identity、`challenger_of`、以及 frozen evidence（historical／oos／full、四個 stress grid 與其 **stress floor** 及產生該 floor 的 grid 名、parameter-neighbourhood）。
- `[C]` 零 survivor 的 bundle（`verdict=REJECT`）不是錯誤：索引以 `skipped_bundles` 記錄「該 round 沒有 cohort survivor」，不產生任何項目。
- `[C]` 順序為 `survivor_id` 遞增（deterministic），**不是**排名，且 `--check` 以「移除 `generated_at_utc` 後逐欄相等」判定一致。
- `[C]`（**v2.0 direct override，AUDITED PASS / LIVE**）card-free direct family（`family.json.handoff.execution=direct_hermes`，§9.4 v2.0）沒有卡片，故**不以 `kanban_task_id` 驗證**：改以 **family/round/run 身分**驗證——上面第 1–3、5 點與目錄身分檢查照舊，另要求 bundle `run_id` 非空字串、`source_attempt_dir` 目錄名等於該 `run_id`（bundle 必須指名產生它的 terminally DONE attempt），且 family／bundle **任一方帶出非空 `kanban_task_id`／`kanban_board`／`task_id` 一律 fail-closed**（洩漏即 foreign owner）。歷史 card-owned family 維持上面第 4 點的嚴格雙側 non-empty-string 檢查；`survivor_bundle.py` 對 direct run 不寫入 card keys（歷史 run 的鍵與內容不變）。

### 27.3 Forward evidence（`forward/<survivor_id>.jsonl`）

- `[C]` **append-only**：一行一 slice，永不改寫既有行；同一個 survivor 只有一個檔案。
- `[C]` 每個 slice **必填**：`survivor_id`、`bundle_identity_sha256`、`params_sha256`、`data_start`、`data_end`、`data_snapshot`（raw snapshot／provenance）、`execution_semantics`（產生該數字的現行 runner 語意）、`cost_model`（fees／funding／slippage 假設）、`episodes`、`net_pnl`、`return_pct`、`sharpe`、`max_dd_pct`、`fees`、`funding`、`slippage_ticks`、`produced_at_utc`、**`source_run`**。缺欄、型別不符、非有限數值一律拒收。
- `[C]` **`source_run`（出處契約，v1.5.1）**：slice 的數字必須能由**真實 run artifact**重新驗證，物件至少含 `attempt_dir`（絕對路徑，必須落在本次 `--results-root` 之內，且**不得**位於 `_survivors/**`——消費 evidence 的層不得同時是它的來源；**v1.5.2 收緊**：必須是 **absolute path**，任何 relative `attempt_dir` 一律 fail-closed，因為 relative path 會以**讀取端 cwd** 解析，同一份 slice 會因此在一處可驗、另一處不可驗，甚至指向結果樹從未包含的目錄）、`run_id`、`kanban_task_id`、`sentinel_sha256`、`result_sha256`。驗證必須全部成立才算合規證據：①`attempt_dir` 的目錄名等於 `run_id`；②該目錄存在 `<attempt_dir>/DONE`（`runtime/terminal_evidence.py` 的 terminal sentinel）且其 sha256 等於 `sentinel_sha256`；③sentinel `status == "DONE"`、`run_id`／`task_id`／`family_id` 分別等於 `source_run.run_id`／`kanban_task_id`／該 survivor 的 `family_id`，且 `run_id` **不等於** frozen research run 的 `run_id`；④sentinel 的 `artifact_checksums["result.json"]` 等於 `result_sha256` **且**等於磁碟上 `<attempt_dir>/result.json` 的實際 sha256；⑤該 `result.json` 是物件、其 `family_id`／`run_id` 與上述一致，且其 **`forward_slice`** 區塊存在、並與 slice 的每個必填欄位**逐欄相等**。任一項不成立即拒收；`read_slices`／leaderboard 重建時**重新執行同一組驗證**（刪除或改寫出處 run 後，既有 slice 立刻變成不合規 → fail-closed 拒絕排名，而不是沿用先前的信任）。`[C]`
- `[C]` **post-freeze／unseen**：`data_start` 必須**嚴格晚於**該 survivor 的 `research_data_cutoff`；`data_start ≤ cutoff` 一律拒收（cutoff 前的資料在研究階段就可見，不得事後改稱 unseen evidence）。
- `[C]` **不得重疊**：新 slice 與既有已記錄 slice 的資料區間重疊（含完全重複）一律拒收；forward evidence 只覆蓋尚未記錄的窗口。
- `[C]` **params 必須是 incumbent 的**：`params_sha256` 或 `bundle_identity_sha256` 與 index 項不符即拒收（§27.4：改參數者是 challenger，不是 incumbent 的 evidence）。
- `[C]` **不得偽造**：本版不建立第二套 backtester、也不代跑任何計算；沒有真實的 post-freeze 計算結果時，正確狀態是 **zero-forward**（§27.5 的 `FROZEN_ONLY`），**不得**寫入任何推估、回填或以研究期資料重貼的 slice。**slice 自身的宣告不構成證據**：數字必須與 `source_run` 所指 run 的 `result.json.forward_slice` 相符，因此「憑空寫一份 JSON」不再是可接受的 forward evidence（v1.5.1 起；v1.5.0 的 F2 缺陷為無任何出處的 slice 曾以 rc=0 被收下，並在 leaderboard 上成為 `FORWARD_POSITIVE`／`champion_candidate`／rank 1）。
- `[C]` forward computation 必須重用現行 strategy／Qlib execution semantics；slice 以 `execution_semantics` 具名該語意，本版只建立 **artifact contract ＋ deterministic ingestion/aggregation path**。
- `[C]` ingestion CLI（`runtime/survivor_leaderboard.py forward`）寫入後必須**讀回**（read-back）最後一行並與寫入內容比對，不符即回報失敗；不存在的 survivor_id、或既有 jsonl 已有不合規 slice 時一律 fail-closed（先修檔案再 append）。
- `[C]` aggregation 必須是明文、可重算且 deterministic：`episodes` 相加、`net_pnl`／`return_pct` 相加（slices 為**互不重疊**的窗口，共用同一 base capital；未 compound）、`sharpe` 以 episode 數加權平均、`max_dd_pct` 取**最差**（最小）值、`first_data_start`／`last_data_end` 取極值。
- `[C]`（**v2.0 direct override，AUDITED PASS / LIVE**）index 項為 card-free（`kanban_task_id` 缺漏／null）時，其 `source_run` **不得**帶非空 `kanban_task_id`，DONE sentinel **不得**帶非空 `task_id`／`kanban_board`／`kanban_task_id`（任一非空即拒收），其餘出處驗證（目錄名、DONE、checksums、`forward_slice` 逐欄比對、post-freeze／不重疊／params 不變）全部照舊；歷史帶卡項目的 `kanban_task_id` non-empty-string 與 `sentinel.task_id` 比對維持不變。

### 27.4 Challenger rule

- `[C]` 任何因 forward evidence 而改動 strategy 或 DCA **任一**參數者，**不得**覆寫 incumbent survivor：必須新建 challenger family／version，並在該 family 的 `family.json` 記 `challenger_of=<survivor_id 或 family/cohort lineage>`。
- `[C]` 用來決定 retune 的資料視為 **consumed research data**；challenger 的新 OOS／forward 起點必須在 challenger 的 preregistration／data cutoff **之後**（本版以 `family.json.created_at_utc` 為 preregistration cutoff，以該 family round-spec 的 `data.oos_start` 為 OOS 起點；`oos_start ≤ created_at` 即 fail-closed）。**禁止**把已看過的資料重新當 OOS。
- `[C]` challenger 必須重新走完**完整 full-backtest survivor gate**（§7.3／§10.8）後，才能以新的 frozen bundle 加入 survivor index；challenger 與 incumbent 並存（各自有自己的 `survivor_id` 與 params），排名依 §27.5。
- `[C]` 本版不對 incumbent family 施加同一 preregistration 檢查：incumbent 已凍結、不會被重新判定；該規則只針對新 challenger。

### 27.5 Leaderboard v1（`leaderboard.json` ＋ `leaderboard.csv`）

- `[C]` `runtime/survivor_leaderboard.py leaderboard` 產生 `/results/_survivors/leaderboard.json` 與 `leaderboard.csv`，並提供 **Top-10** 陣列（json 的 `top10` 與 csv 的 `in_top10` 欄）。
- `[C]` **不做 opaque weighted score**：採透明 deterministic ordering，優先使用**真正的 post-freeze forward evidence**；尚無 forward evidence 者 fallback 到 frozen OOS evidence。排序 tuple（可測、可手算）：
  1. `has_forward` DESC（有 unseen evidence 者在前）；
  2. `forward_sharpe` DESC，NULLS LAST；
  3. `forward_return_pct` DESC，NULLS LAST；
  4. `forward_max_dd_pct` 絕對值 ASC，NULLS LAST（drawdown 較小者在前）；
  5. `oos_sharpe` DESC；
  6. robustness **stress floor**（四個 stress rerun 中**最差**的 net PnL）DESC；
  7. parameter-neighbourhood `same_sign_fraction` DESC；
  8. `survivor_id` 字典序 ASC（最後手段，使排序成為全序、可重現）。
- `[C]` 每一列必須列出：`rank`、family、`cohort`、params、`evidence_state`、forward metrics、OOS／full metrics、robustness（含 stress floor 與其 grid）、neighbourhood、`last_evidence_end`、bundle identity（含 bundle 檔案 checksum 與路徑）。
- `[C]` `evidence_state` 最小集合（**描述性**，永不回寫 PASS，也不 gate 任何事）：
  - `FROZEN_ONLY`：無 forward slice；
  - `ACCUMULATING`：有 forward slice，但累計 `episodes` 仍**少於**該 survivor frozen OOS 的 `episodes`（unseen 樣本仍比當初驗證它的 OOS 窗口短）；
  - `FORWARD_POSITIVE`：episodes 足夠且 forward `net_pnl > 0` 且 forward `sharpe > 0`；
  - `FORWARD_DEGRADED`：episodes 足夠且 forward `net_pnl ≤ 0` 或 forward `sharpe ≤ 0`。
- `[C]` Top-10 先是 research／live-candidate **shortlist**，**不等於**直接 live allocation；掉出 Top-10 不改變 verdict。
- `[C]` leaderboard 是 derived artifact：`--check` 必須能由 index ＋ forward jsonl 重算並與磁碟上的 json／csv 逐位元一致（csv 不含時間戳，故應逐位元穩定）；forward jsonl 若有不合規行、或 index 不一致，一律 fail-closed 拒絕排名（不得在未驗證 evidence 上排名）。

### 27.6 Champion／live-candidate 邊界

- `[C]` `champion_candidate` 可由「Top-10 ＋ `FORWARD_POSITIVE`」標示；但 v1.5 **不**自動發實盤訊號、**不**配置資金、**不**觸發任何下單或 paper/testnet 動作。
- `[C]` future live selector 必須**再**看 correlation、symbol exposure、timeframe exposure、family concentration／risk；**不得**只取 Rank #1。
- `[C]` Paper／Testnet／Live integration 仍是 downstream／future，**不得**成為 Qlib full-backtest 的 retroactive gate（§17）。

### 27.7 實作與可執行驗證

- `[V]` 工具：`runtime/survivor_index.py`（index 重建／`--check`；`--out` 受 `_survivors/**` realpath 邊界強制）、`runtime/survivor_leaderboard.py`（`forward` ingestion ＋ `leaderboard`／`--check`；`--out-dir` 同一邊界強制，slice 需通過 `source_run` 出處驗證）、`runtime/tests/test_post_survivor.py`（**33/33 OK**，含 F1／F2／F3 三組 v1.5.0 攻擊的 regression（v1.5.1 加入）與 v1.5.2 的三組 trust-boundary regression（symlinked reserved root／relative `source_run.attempt_dir`／non-string `kanban_task_id`）；六組在舊版 bytes 上實測 FAIL）。
- `[V]` **v1.5.2 trust-boundary 收緊（audit t_346bcc04 的 F1／F2／F3 殘留）**：reserved root 檢查（`reserved_root_problem()`）套用到本層全部寫入者；`source_run.attempt_dir` 必須為絕對路徑；ownership id 必須兩側皆為 non-empty string。三組 regression（`test_symlinked_survivors_root_cannot_escape`、`test_relative_attempt_dir_is_refused`、`test_non_string_ownership_id_fails_closed`）在 v1.5.1 的 bytes（`84f494c`）上實跑 `Ran 3 tests … FAILED (failures=3)`（F1 於 symlinked root 下 `result=written` 並覆寫複本 verdict、F2 收下 relative 出處的 slice、F3 索引照樣列出 `kanban_task_id: 12345` 的兩名 survivor），在 v1.5.2 全數 rc=1。證據快照：`evidence/v1.5.2-post-survivor-trust-boundary-20260913.json`。
- `[V]` **seed**：以既有 A v2 frozen bundle 自動重建 index／leaderboard，**恰為兩個 survivors**（`BTCUSDT/1h`、`SOLUSDT/4h`），兩者皆 `FROZEN_ONLY`（無任何 post-freeze 計算、未偽造 forward metrics）；fallback 順序由既有 evidence 客觀產生（`oos_sharpe` 2.17438 > 0.536464 → `SOLUSDT/4h` 第 1、`BTCUSDT/1h` 第 2），非硬寫名字。證據快照：`evidence/v1.5.0-post-survivor-lifecycle-20260913.json`。
- `[V]` **既有 immutable artifact 未改寫**：A v2 round r1 的 `survivor-bundle.json`（`sha256:4638885f…`）、`verdict.json`（`sha256:cb470adf…`）、`result.json`（`sha256:012e6d1a…`）、`artifacts/cohort_survivors.json`（`sha256:74f250cf…`）在 seed 前後逐位元相同；bundle 公開 identity 仍為 `sha256:c051759f…`，`survivor_bundle.py --check` 仍 rc=0／`check_clean`（`CONTRACT_VERSION` 於 v1.5.0 更新，屬 §10.8 允許排除的產生者身分欄位，故不影響任何 frozen bundle 的重跑比對）。
- `[T]` 本版**未**執行任何 post-freeze forward 計算：`/results/_survivors/forward/` 不存在、0 slices、0 `champion_candidate`。raw klines 覆蓋已越過 cutoff（最後一根 2026-09-13T00:00:00Z > cutoff 2026-09-10），因此 forward slices 在未來可由既有 engine 的一次真實 run 產生——那是一個帶 launch 的獨立任務，不是本版的一部分。**該 launch 的產出必須同時滿足 §27.3 的 `source_run` 契約**：一個 terminal `DONE` attempt（`runtime/terminal_evidence.py`），其 `result.json` 帶 `forward_slice` 區塊（欄位與 slice 逐欄相等），且該 attempt 位於結果樹內、`_survivors/**` 之外；v1.5.1 尚未實作任何 forward runner（附錄 B T18 維持未執行）。

## 28. Survivor Evidence Preservation：survivor promotion → execution evidence（v1.6.0）

- `[C]` **觸發語意**：正式出現在 `leaderboard.json` 的 `entries` **就是** evidence preservation 的觸發點
  （`entries` 成員 = §27.2 index 的 survivor 成員）。它**不是** Top-10 觸發、**不是** PASS gate；Top-10 之外的
  leaderboard entry 一樣要保存，不是 Top-10 的 leaderboard entry 也不會因此不被保存。
- `[C]` **大量 rejected cells 不保留逐筆 execution（明文）**：研究階段的 103,680 個 cell evaluation、以及每一個
  未進榜的 candidate／被 cull 的 cohort，一律維持既有摘要形式（`artifacts/grid_<grid>.csv` 的 aggregate row）。
  本層**禁止**為未 promoted 的 cell 產生 per-cell ledger、逐筆 fill、逐筆 equity 或任何 equivalent：那正是本節
  要避免的過度工程。per-cell 逐筆資料的存在與否，必須由「是否為 leaderboard entry」決定，不得由「是否跑過」決定。
- `[C]` **非 gate**：evidence package 是否存在**不得回寫** verdict、`performance_claimable`、`evidence_state`
  （forward）、`champion_candidate` 或 ranking。`evidence_package_status` 只描述 preservation coverage；package
  遺失時 leaderboard 仍必須 rc=0 且 ranking 逐欄不變，`coverage` 才可報缺件並 rc=1。
- `[C]` **落點（唯一允許）**：

```
<results-root>/_survivors/evidence/<survivor_id>/
  manifest.json
  aggregate.csv                # 9 個註冊 grid 的 frozen/replay matched winner rows
  grids/<grid>/episodes.csv
  grids/<grid>/fills.csv
  grids/<grid>/equity.csv
  grids/<grid>/summary.json    # optional compact header/aggregate
```

  不複製整個 frozen round、不複製 103,680 列 grid 資料、不新增 service／daemon／queue／Registry／UI／DB。
- `[C]` **寫入邊界比 §27 更窄**：本層只寫 `<results-root>/_survivors/evidence/**`（沿用 §27.1 的 reserved-root／
  symlink／realpath fail-closed，任何 root／ancestor escape 在任何寫入前即拒），且 final package 只能由
  `.staging-*` 目錄**原子 rename** 產生：final 不存在才建立；已存在且 identity 相同 → `already_identical`
  （no-op）；identity 不同 → **refuse overwrite**（憑證永不就地覆寫）。

### 28.1 觸發集合的來源（durable readback）

- `[C]` survivor_id 與其 params／cohort／bundle identity 一律由 durable 的 `survivor-index.json` ＋
  `leaderboard.json` **讀回**取得，不得由 prompt 或呼叫端硬編碼。
- `[C]` replay driver 在動工前必須確認該 survivor_id 同時（a）存在於 index 且（b）是 leaderboard entry；
  否則 rc=1（「evidence is preserved for promoted leaders only, never per candidate/culled cell」）。

### 28.2 A v2 同引擎 instrumentation（inert trace hook）

- `[C]` **不建立第二套 backtester**：對既有 `container/scripts/20_strategy_a_run.py` 的 `simulate()` 增加一個
  optional inert trace hook（module-level `TRACE = None` ＋ `_trace()`），emit 點一律位於
  `if TRACE is not None` 之後。**trace 關閉時既有的 aggregate return、計算順序與語意不得改變**，且必須以
  「同一 cell trace off／on 的 aggregate 逐欄相等」強制（driver 每次 replay 都跑，並且 engine 層有
  `container/scripts/tests/test_survivor_trace.py`）。
- `[C]` **tracing 只被觀察**：不得有任何 traced 值反向參與策略決策、會計或回傳值；MAE／MFE 取既有 `eq`／`ueq`
  的 min／max，不進 `record()` 的回傳 dict。
- `[C]` **不動既有部署**：不碰 `/Users/hong/workspace/qlib-apple-container/scripts` 的原始 `/scripts` runner、
  也不修它與 repo 的 disposition mapping drift；只記錄 provenance。`source_runner` pin 以 frozen run-spec
  `script.sha256`、terminal `DONE` sentinel 與**實際 `/scripts` readback** 為準，不硬信任何 prompt 文字；
  `replay_runner` pin 實際被 import 的 instrumented runner SHA，兩者在 manifest 內同時揭露。
- `[C]` **trace 至少保存**：
  - fills：`episode_id`、`event_type` ∈ ENTRY／DCA_ADD／EXIT／FLATTEN、`bar_index`／`open_time_ms`、`price`、
    `qty`、DCA level、trigger／ref price、`fee`、`slip_ticks`；
  - episodes：entry／exit timestamps、`exit_reason` ∈ TP／STOP／MARGIN_CALL／EOD_FLATTEN、`gross_pnl`、`fees`、
    `funding`、`net_pnl`、holding bars／hours、`layers_used`、MAE／MFE；
  - equity：`day_index`、`date`、`equity`、`peak`、`drawdown_usdt`、`drawdown_pct`、`in_window`；
  - aggregate／header：cohort、6 軸 winner params、grid、window／bar slice、stress／cost model、engine SHA。
- `[C]` **不保存**：逐 bar raw engine state、orderbook／depth、raw klines 複本、每 bar funding event
  （episode funding 總額已足夠）。

### 28.3 只 replay promoted winner cells

- `[C]` 每個 survivor 只重跑 **已註冊 grid 的 winner cell**（現況 9 個 grid × 2 survivors = **18** 次 winner-cell
  simulate）；**禁止**重跑 103,680 次 research evaluation，也禁止做參數搜尋。
- `[C]` 註冊 grid 名稱以 frozen terminal `DONE` sentinel 的 `artifact_manifest` 與 run-spec `expected_outputs`
  **兩者一致**的 `artifacts/grid_<grid>.csv` 清單為準（不一致即拒）。
- `[C]` 每個 replay aggregate 必須與 frozen `artifacts/grid_<grid>.csv` 中該 (symbol, timeframe, 6 軸 winner cell)
  row 的**全部欄位**逐欄相等（現況 32 欄）；frozen CSV 自身的 sha256 必須等於該 attempt terminal sentinel
  `artifact_checksums` 所記值。任一欄不符 → fail-closed，**不 materialize package**。
- `[C]` `container/scripts/21_strategy_a_survivor_replay.py` 必須 import／load **同一個** `20_strategy_a_run.py`
  module，只呼叫既有 `Cohort`／data loaders／`rail_for`／`simulate`／`record`；**禁止**複製 `simulate()` 邏輯、
  **禁止**呼叫 `summarize()`、**禁止**寫 verdict／bundle／任何 frozen 目錄（`/qlib/work` 為可重建區）。
- `[C]` **ledger 自身驗證**：Σepisode gross／fees／funding／net 對回 aggregate；episode partition
  （TP／STOP／MARGIN_CALL／EOD_FLATTEN）等於 aggregate 的 `tp_hits`／`stop_hits`／`margin_calls`／`open_at_end`；
  equity ledger 以**純 stdlib** 重算 Sharpe 與 MaxDD 對回 aggregate。日 equity 序列是 cohort 完整
  1714-day carry-forward（historical／oos／full 都是同一條日曆），故必須記 `day_index` ＋ `in_window` 才可重算。

### 28.4 Evidence package（保存 ledger 本體，不只引用）

- `[C]` `manifest.json` 至少 pin：`survivor_id`／family／round／run／cohort／`kanban_task_id`、bundle path＋sha＋
  `bundle_identity_sha256`、params＋`params_sha256`、research cutoff、source data（input manifest／bins build／
  data windows）、`source_runner` SHA、`replay_runner` SHA、contract 版本、每個 grid 的 frozen csv path＋sentinel
  sha＋row identity、ledger paths＋sha、aggregate comparison 結果、`generated_at_utc`。
- `[C]` `package_identity_sha256` = canonical JSON（僅排除 `package_identity_sha256` 自身與 `generated_at_utc`）
  的 sha256；**不得**使用 hash-chain／Merkle／任何隱含欄位排除。
- `[C]` **誠實揭露**：ledger 是 **deterministic replay materialization**——以同一顆 engine 在同一批 frozen 輸入上
  重跑而得，**不是**原始 research run 當時保存的 bytes（原 run 只保留 aggregate）。manifest 必須具名此揭露，
  任何文案或 artifact 都**不得**宣稱原始 run 存過逐筆 ledger。

### 28.5 Leaderboard drill-back（不參與排序）

- `[C]` `leaderboard.json` 的 derived entries 與 `leaderboard.csv` 增加 `evidence_package_status`
  （PRESENT／ABSENT）、`evidence_manifest_path`、`evidence_manifest_sha256`。
- `[C]` 這三欄**不進入** ORDERING_RULE／sort key：package 出現或消失不得移動 rank、不得改變 Top-10 成員、不得
  影響 `evidence_state`／`champion_candidate`／任何 verdict。
- `[C]` `leaderboard --check` 必須仍 deterministic；package missing 時 leaderboard 仍 rc=0；`coverage` 可 rc=1。
- `[C]` PRESENT 的判定至少要該 manifest 的 `survivor_id`／`params_sha256`／`bundle_identity_sha256` 與該 entry
  一致，否則視為 ABSENT（描述性覆蓋率，不是 gate）。

### 28.6 實作與可執行驗證

- `[V]` 工具：`container/scripts/21_strategy_a_survivor_replay.py`（容器內以 `/opt/venv/bin/python` 執行，
  import 同一顆 engine；只寫入 `--staging`，且要求該路徑位於 `_survivors/evidence/` 之下）、
  `runtime/survivor_evidence.py`（host 純 stdlib：`materialize`／`check`／`coverage`）、
  `container/scripts/tests/test_survivor_trace.py`、`runtime/tests/test_survivor_evidence.py`。
- `[V]` 任何新增檔皆為 stdlib／既有依賴（CSV／JSON／stdlib；容器內只用既有 numpy／qlib），**未**新增任何第三方
  依賴。
- `[V]` **現況 materialization**：由 durable index／leaderboard 讀回現有兩名 A v2 survivors
  （`sv-f762a1da8909a5bf` SOLUSDT/4h、`sv-904822905a811669` BTCUSDT/1h），各 replay 9 個註冊 grid，
  **18/18 winner cell 逐欄相符**、trace off／on 一致；`coverage` = **2/2 PRESENT**；leaderboard 仍
  `FROZEN_ONLY`、`champion_candidate=false`、`forward` 仍 0 slices、排名仍 SOLUSDT/4h #1、BTCUSDT/1h #2。
- `[V]` **實跑讀回（2026-09-13，commit `e4c6903`）**：`runtime/tests/test_survivor_evidence.py` **12/12 OK**（host、
  自建 temp fixture，真實 `/results` 未被觸碰）、`container/scripts/tests/test_survivor_trace.py` **7/7 OK**（host
  與 `qlib-run` 內 `/opt/venv/bin/python` 各實跑一次）；package identity `sv-904822905a811669` =
  `sha256:16338360…`、`sv-f762a1da8909a5bf` = `sha256:7088a5a2…`；兩個 package 的 `aggregate.csv` 皆
  `MATCH`（9 grids × 32 columns = 288 cells）；`source_runner` pin `sha256:c4f9a216…` 由 deployed
  `/scripts/20_strategy_a_run.py` **實際 readback** 相符（非採信 prompt）；frozen `survivor-bundle.json`
  （`4638885f…`）／`verdict.json`（`cb470adf…`）／`round-spec.json`（`e0b348bf…`）／`result.json`
  （`012e6d1a…`）／`artifacts/cohort_survivors.json`（`74f250cf…`）逐位元不變、`_survivors/forward/` 不存在。
  證據快照：`evidence/v1.6.0-survivor-evidence-20260913.json`。
- `[V]` **未變動項**：`contract 28` 不新增任何 service／daemon／queue／Registry／UI／DB；B v2 未 launch；
  cron `624d0be5b23c` 仍 paused；Strategy B v1 仍 blocked；§27 的 forward evidence 語意與 `forward/` 目錄狀態不變。
- `[V]` **文件狀態**：**AUDITED PASS / FROZEN**（v1.6.0；audited content commit `6e7d046`，auditor `t_e18a0f35`，2026-09-13；獨立唯讀審計卡 `t_e18a0f35`，9/9 項 PASS、verdict APPROVED、無 blocking finding——審計在 staging 4 檔 sha 與 repo 逐位元一致（`20_strategy_a_run.py` `4a910fc6…`／`21_strategy_a_survivor_replay.py` `34783c8c…`／`tests/test_strategy_a_engine.py` `208c46ac…`／`tests/test_survivor_trace.py` `b6254e0e…`）、deployed `/scripts/20_strategy_a_run.py` 仍 `c4f9a216…` 未改寫、容器對兩個 leaderboard entry 各重跑 9 grid 得 18/18 `matches frozen row`（auditor 另以自寫腳本獨立重算 sentinel `artifact_checksums` 18/18 相符、576 欄比對 0 不符、ledger 重算全對回 aggregate）、host 142 檢定全綠、負向控制 host 21 案＋容器 4 變體全 rc=1、frozen 五 artifact 與排名狀態逐位元不變的前提下 PASS；非阻斷殘留 N1–N5（文件措辭與既有計數：§23 item 17／§28 的「package 遺失仍 rc=0」須註明「（重建後）」、附錄 C 的「六條」實為 7 條、證據快照 `head_commit` 記為 `e4c6903` 而檔案於 `6e7d046` 入庫、`already_identical` 需以同一 `--engine` 路徑重現、附錄 B T17 既有的 30→33 計數漂移）經 operator 明確 **DEFER**，不開 v1.6.1）。

## 29. Validated Survivor Research Mirror（v1.10.0）

### 29.1 Role / truth boundary

- `[C]` 正式 Qlib survivor 的 canonical performance truth 永遠留在 `/Volumes/ExpansionDrive/qlib-results/_survivors/**`；`HCH725/validated-survivor-research` 是 **downstream compact research mirror**，不是第二套 source of truth，也不得 self-pass／self-reject／self-rank／self-promote。
- `[C]` mirror set = canonical `leaderboard.json` 的**全部 formal `entries`**，不是 Top-10。Top-10 仍只是 §27 ranking/selection aid；掉出 Top-10 不等於 REJECT，也不移除正式 survivor 的 mirror scope。
- `[C]` deep research 若產生 challenger、調參想法或任何新的 performance claim，必須回到 formal Qlib full-backtest；private repo 本身不能賦予 performance verdict。

### 29.2 Managed compact layout / large-data boundary

`runtime/survivor_private_export.py` 只管理：

```text
leaderboard/leaderboard.json
survivors/<survivor_id>/baseline.json
survivors/<survivor_id>/evidence-manifest.json   # only valid PRESENT §28 package
survivors/<survivor_id>/aggregate.csv            # same condition
```

- `[C]` `baseline.json` 必須直接 mirror canonical survivor-index entry；不得由 exporter 重新計算 performance/ranking。
- `[C]` §28 evidence 只有在 leaderboard entry 為 `PRESENT` 且 `survivor_evidence.check_package()` 驗證成功時，才可 mirror `manifest.json → evidence-manifest.json` 與 `aggregate.csv`。
- `[C]` raw market data、full sweeps、fills、episodes、equity ledgers 不進 private GitHub mirror。
- `[C]` `research.md` 是 human research file；exporter 不建立、不覆寫、不 stage。

### 29.3 Trigger / isolation / fail-closed

- `[C]` 自動 hook 只允許在 `survivor_leaderboard.py leaderboard` **成功寫入 canonical results root** 且不是 `--check` 時執行；`realpath(results_root)` 必須等於 `realpath(si.DEFAULT_RESULTS_ROOT)`。temp/custom results root 與 `--check` 一律不得 import/call exporter。
- `[C]` baseline 與已 mirror 的 compact evidence 對同一 identity 為 immutable：existing bytes 不同 → 在任何 write 前 `conflict` fail-closed，不 overwrite；same bytes → no-op。
- `[C]` private target 必須是 `main` 且 local `HEAD == origin/main`；不同步時只 warning/refuse，不自動 pull／merge／rebase／reset。
- `[C]` Git/repository/auth/network/conflict failure 對 Qlib pipeline **warning-only / non-blocking**：不得改 leaderboard 原本成功的 rc、不得改 PASS/REJECT、不得阻塞 handoff 或下一策略。
- `[C]` Git 只 stage 本次 managed changed paths；禁止 `git add .`，不得 stage unrelated user research files。
- `[C]` 本層不新增 daemon／service／queue／DB／GitHub Actions／webhook／cron／config framework；它直接 reuse canonical leaderboard write 的既有事件。

### 29.4 Verified current implementation / controlled seed

- `[V]` final exporter implementation：quant-runtime-pipeline commit `25d3e438093a7fbc1bf31cb7ecdd386796ac8d9e`；exporter regression **10/10 PASS**、post-survivor **44/44 PASS**、survivor-evidence **14/14 PASS**，immutable baseline/evidence conflict 皆在 write 前拒絕。
- `[V]` controlled initial seed（2026-09-21）：`HCH725/validated-survivor-research` commit `15eb017cfcb1da59f8c27977f6c5a6f63a7f072a`；canonical/private `leaderboard.json` **byte-for-byte identical**（sha256 `96f5b15fc8e6b00a8213468a3c9621a8261bc1df93f7c9b173c86828252d6e7f`），24/24 formal survivor IDs identical，24 baselines，2 組 `evidence-manifest.json + aggregate.csv`。
- `[V]` seed 後立即第二次 exporter run = `unchanged`，`changed_paths=[]`、無新 commit；private local `main == origin/main`。
- `[C]` 上述 24/2 是 **seed-time verified readback**，不是 contract 固定 cardinality；未來 formal leaderboard entries 依相同規則增量 mirror。

## 附錄 A：本文件引用的既有證據

| 項 | 來源 | 內容 |
|---|---|---|
| E1 | `container ls`（本機即時） | `qlib-run  qlib:0.9.7-arm64  linux  arm64  running  6 CPU / 4096 MB` |
| E2 | `container --version` | `1.4.1 (build: release, commit: 9a8917c)` |
| E3 | `/Volumes/ExpansionDrive/` | `market-data-raw`、`qlib-results` 皆存在 |
| E4 | `hermes_cli/kanban_db.py` | `VALID_STATUSES`、`promote_task`、`schedule_task`、`unblock_task` 語意 |
| E5 | `qlib-apple-container-audit-t_ec81af5e.md` | mount ro/rw、raw 唯讀拒絕寫、Qlib 0.9.7 arm64、volume 30 GiB |
| E6 | `quant-strategy-research` board DB（2026-09-21 唯讀查詢） | active **production strategy** baseline = 0；文件／audit／維護卡不計入 strategy family |
| E7 | `container/Containerfile`（本 repo） | `QLIB_TAG=v0.9.7` + `QLIB_COMMIT=da920b7f…` + build 內 rev-parse 守衛 |

## 附錄 B：仍需實測（TO-BE-VALIDATED 總表）

`第一張 production card` 欄 = 開跑第一張正式 strategy card 前是否必須具備（`BLOCKER`）或可延後（`DEFERRED`／`OUT-OF-SCOPE`）；判準見 §21.2。

| # | 項目 | 卡在哪 | 第一張 production card | 驗證方式 |
|---|---|---|---|---|
| T1 | reconciler 腳本（no-agent） | **已落地（最小版）** `runtime/reconcile.py` | — | R3 dry-run 對既有 sentinel 對帳 + `runtime/tests/test_reconcile.py` 23/23（2026-09-13 實測；v1.1.1 追加 consumed-first 與 family/round/run/container identity 覆蓋） |
| T2 | preflight 腳本 P1–P10 | **已落地（最小版）** `runtime/preflight.py` | — | R1 全綠輸出可重現（2026-09-13 實測，P1–P8 PASS；P9/P10 與 P10 sha 重算之邏輯層檢查 `runtime/tests/test_preflight_p10.py` 10/10） |
| T3 | sentinel/checksum 產生器 | **已落地（最小版）** `runtime/terminal_evidence.py` | — | fixture 產出、`check` 重算相符、重複 publish 被拒（INV-15）（2026-09-13 實測） |
| T4 | artifact 目錄 schema 實際落地 | **已完成**：2026-09-21 唯讀讀回 `/Volumes/ExpansionDrive/qlib-results` 有 45 個非保留 family directories | — | 現行 family/round/attempt artifacts + `survivor_index --check` / leaderboard readback |
| T5 | fingerprint 計算腳本 | 未實作 | `DEFERRED`（§14.3 允許手算 + auditor 重算） | `family.json` 的 `fingerprint_input` 由 auditor 唯讀重算比對 |
| T6 | yield 判定自動化 | 未實作 | `DEFERRED`（§15.4 明訂人工判定可稽核） | 以 round 數與 artifacts 人工判定後核對 |
| T7 | chain head 判定查詢 | 未實作 | `DEFERRED`（§7.1 明訂人工 DB 讀回） | DB 讀回人工確認 |
| T8 | 13 項 failure drills（D1–D13） | 未執行（reboot/resume 核心路徑已於 §12/§21.2 語意確認不被阻擋） | `DEFERRED`（不是第一張的 gate，§21.2） | §21.2 |
| T9 | Retired secondary engines（Lean／Nautilus／PyBroker） | non-participating；非現行/已規劃 stage | `RETIRED`（非 blocker，§17） | 只有 operator 未來明確重啟時才另走 §26 |
| T10 | 多筆 terminal evidence / checksum 衝突的仲裁 | 契約已定（§12.6）+ fail-closed 路徑已實作於 `runtime/reconcile.py`（含 incident JSONL 寫入）；未實戰 | `DEFERRED`（fail-closed 已可執行） | D3/D4/D5 變體 + D13；驗證 incident artifact 落地 |
| T11 | `/results/*/family.json` ownership/lineage 落地 | **已完成並長期使用**：多個正式 family 具 durable `family.json` ownership/lineage；現行 consumer 由 index/handoff/reconciler 讀回驗證 | — | 唯讀抽查正式 family `family.json` 與其 round/run ownership 對照 |
| T12 | round `verdict.json` 與 incident artifact 寫入器 | incident 寫入器**已落地（最小版）**於 `runtime/reconcile.py`；`verdict.json` 仍由 default 人工寫入（維持最小設計） | `DEFERRED`（verdict.json 為 default 的判斷動作，不是自動化缺口） | 事後以檔案讀回驗證（欄位齊備、JSON 可解析） |
| T13 | v1.3.0 cohort selector / survivor gate（`cohort-selector-v1` / `cohort-disposition-v1`） | **已落地（最小版）**：`container/scripts/20_strategy_a_run.py` 的 `select_cohort_winner` / `cohort_neighbourhood` / `evaluate_cohort` / `family_disposition`，含「historical-only」守衛與決定性自我檢查；邏輯層 `container/scripts/tests/test_strategy_a_engine.py` **35/35 OK**（host 端 numpy interpreter ＋ `qlib-run` 內 `/opt/venv/bin/python`，2026-09-13；v1.3.1 為 29、v1.3.2 新增 6 個獨立 gross PnL 檢定） | **已完成**（Strategy A v2 r1-u1 真實 artifacts：20/20 cohorts、`coverage_complete=true`、2 survivors `BTCUSDT/1h`＋`SOLUSDT/4h`；見 `evidence/strategy-a-v2-*.json`），v1.4.0 另生成 survivor bundle | Strategy A v2 attempt 的 `artifacts/cohort_results.json` + `cohort_survivors.json` + `result.json` 的 `disposition` |
| T14 | Strategy A v2 pre-registration 計數驗證 | **已落地（最小版）**：`runtime/strategy_a_v2_counts.py`（純 stdlib，`--out` 寫 evidence）＋ `runtime/tests/test_strategy_a_v2_counts.py` **14/14 OK**（v1.3.1 provenance 負向控制後；v1.3.0 為 9/9）；2026-09-13 實跑 → `cohorts=20 strategy=12 dca=48 base_per_cohort=576 per_grid=11520 total=103680`、fingerprint MATCH、`ok=True` | **已完成**（實例化的 r1 run-spec 已通過；counts 103,680 見 `evidence/strategy-a-v2-counts-instantiated-20260913.json`） | 重跑 `python3 runtime/strategy_a_v2_counts.py --run-spec <instantiated run-spec>` 為 rc 0 |

| T15 | v1.4.0 frozen survivor bundle（§10.8） | **已落地（最小版）** `runtime/survivor_bundle.py` ＋ `runtime/tests/test_survivor_bundle.py`（v1.4.2 起 18 檢定）；Strategy A v2 round r1 的真實 bundle 已生成（2 survivors、identity `sha256:c051759f…`，v1.4.1 起該值可由 auditor 以純 stdlib 從該檔案獨立重算） | — | `python3 runtime/survivor_bundle.py --attempt-dir <attempt> --check --json` rc=0 且 `identity_recipe_matches=true`；bundle 內含全部 survivors；改動量測值／公開 digest 的 tamper 負向控制 rc=1；v1.4.2 起改 `generator.path` 並只重簽公開 identity 亦必須 rc=1（`generator.sha256` 單獨變動仍為 no-op） |
| T16 | Strategy B v2 preregistration / execution | **已完成歷史生命週期**：family `ema-crossover-walkforward-momentum-long-short-v2` 已建立並執行；r1-u1/u2 technical FAILED、r1-u3 terminal DONE，round `verdict.json` = `REJECT`／`performance_claimable=false`。templates 保留為 provenance/rebuild input，不代表未 launch。 | — | 唯讀讀回該 family `family.json`、r1 attempts terminal 與 `verdict.json` |
| T17 | post-survivor lifecycle（§27：file-only survivor index／append-only forward evidence／Top-10 leaderboard） | **已落地並持續使用**：`survivor_index.py` + `survivor_leaderboard.py`；2026-09-21 canonical `--check` 皆 `check_clean`，formal survivors=24、Top-10=10；`_survivors/forward/` 仍不存在（0 slices）。`runtime/tests/test_post_survivor.py` 現行 44 檢定。 | — | `survivor_index.py --check`、`survivor_leaderboard.py leaderboard --check`、`test_post_survivor.py` |
| T18 | post-freeze forward evaluation（真實 slice 產生） | **未執行**（本版刻意不 launch 任何計算）：`/results/_survivors/forward/` 不存在、0 slices；raw klines 已覆蓋到 2026-09-13T00:00:00Z（> cutoff 2026-09-10），因此 slices 可由既有 engine 的一次真實 run 產生 | `DEFERRED`（需 operator 明確放行的 launch-bearing 任務） | 由既有 strategy／Qlib execution semantics 跑出一個 terminal `DONE` attempt（結果樹內、`_survivors/**` 之外），其 `result.json` 帶 `forward_slice` 區塊 → `survivor_leaderboard.py forward` 收件（`source_run` 出處驗證通過 ＋ readback 相符）→ `leaderboard --check` rc=0 且該列不再是 `FROZEN_ONLY` |
| T19 | survivor evidence preservation（§28：formal leaderboard-entry 觸發、evidence package＋manifest、非排序 drill-back） | **已落地並維持 optional coverage**：2026-09-21 formal leaderboard 有 24 survivors，其中 2 個既有 A v2 survivors 具 PRESENT §28 package；其餘 ABSENT/FROZEN_ONLY 不影響 rank/verdict。`runtime/tests/test_survivor_evidence.py` 現行 14 檢定。 | — | `survivor_evidence.py check/coverage`（對 PRESENT package）＋ leaderboard `--check`；package absence 不改排序/verdict |

## 附錄 C：變更記錄

| 版本 | 日期 | 變更 | 理由 |
|---|---|---|---|
| v2.0.0（AUDITED PASS / LIVE） | 2026-09-24 | §9.4、§14.4 direct-Hermes execution/completion bridge override；§16 P10／terminal host publication 容許無卡 direct family，historical card-owned 要求不變 | initial execution-transport audit finding 經 remediation `t_54a7af2e` 收斂；independent auditor run 222 PASS，final audited implementation `caeffea`；隔離 direct/legacy、故障同 family 重試、lease 防重、runtime/n8n regression 與 real-root read-only dry-run 通過，之後完成 live cutover。 |
| v1.0 | 2026-09-12 | 初版定版（本卡 t_5b5b38d6） | ChatGPT 規劃；新增 Family Yield（§15）與 Execution Preflight（§16）兩條正式護欄 |
| v1.0.1 | 2026-09-12 | **B1**：廢除 task-level metadata 作為 durable state，ownership/lineage/verdict 改落 `/results`（新增 §10.6 `family.json`、§10.7 `verdict.json`；改寫 §9.4 reconciler 入口、§14、§18.1、INV-4/9/10/17）。**B2**：新增 §12.6 conflict/incident 流程（`scheduled` 不得直接 block），統一 §6.3/§7.1/§12.3/§12.4/§12.5。**B3**：`/qlib/work` 單一 volume 故障一律 card-local，統一 §12.5/§13/§16 | auditor t_a3dc355d 三個 blocking findings 的最小 remediation（本卡 t_bcedaf65） |
| v1.1.0 | 2026-09-13 | **A 語意校正**：§1.2 改寫（Nautilus 為 future/out-of-scope/non-blocking，非 authoritative gate）；新增 §7.2「全量回測定義與 production 模式」；§9.6 移除「未有下游 acceptance 只能 research-only」的降級條款；§17 全面改寫為 out-of-scope 備忘（不得反向改寫 verdict、不得當 gate）；§21.2 新增 blocker/deferred 分級；§22 新增 A19/A20、改寫 A13；§24.5 標 future；§25 新增兩條硬規則；附錄 B 新增「第一張 production card」分級欄；§16.2 P2 語意校正（raw 唯讀判定改以 **container 內寫入探針**為準：host 使用者擁有該 export，host 端 `test -w` 必然為真而會誤判，故明文禁用；此校正僅記載於本表與 `evidence/`）。**B 修正**：§16.2 P8 與等價檢查一律改用 `/opt/venv/bin/python`（`/usr/local/bin/python` 無 qlib；登入 shell 會還原 PATH）；`container/scripts/verify_final.sh` 兩處 `container exec … python` 同步改為 venv 絕對路徑。**C 最小 readiness**：新增 `runtime/preflight.py`（§16.4 的 P1–P10 單一腳本）、`runtime/reconcile.py`（§9.4 no-agent reconciler，含 §12.6 incident 寫入）、`runtime/terminal_evidence.py`（§10.3/§10.4 sentinel + checksum 產生器，host 端 orphan `INCOMPLETE` 補寫用）；§3/§9.4/§10.4/§21.1 的 `[T]` 對應轉為 `[V]` | ChatGPT（GPT-5.6 Sol）卡片 t_ec039d5f：修正契約語意與第一張正式 strategy card 前的最小 runtime 缺口的 remediation；不新增任何 Manager/Service/Factory/Registry/Orchestrator、daemon、queue 或第二套 runtime |
| v1.1.1 | 2026-09-13 | **F1（blocking）**：`runtime/reconcile.py` 的 consumed 判定移到 §9.4 驗證清單**之前**（sentinel 可解析出 `task_id` 且 DB 讀回非 `scheduled` → consumed/no-op：不寫 incident、不留 comment、重跑不累加；無法解析 `task_id` 才維持 fail-closed），對齊 §9.4 掃描範圍與 INV-16。**F2**：§9.4 item 1 補上 attempt `run-spec.json`，並補足 identity 對照（`family.json.family_id`、`round-spec.json.family_id/round_id`、`run-spec.json` 的 family/round/run/task、`container_id`（sentinel 必填、run-spec 有載明即須相符）、path/sentinel）；§9.4 item 4 載明現役容器 identity 比對屬 preflight P5/P6、不在 reconciler 內再引入 container 查詢。**F3**：§16.2 P10 改為「`script.sha256` 必須由 host 端**實際重算**相符」，不可讀／不可解析 → `FAIL`（NOT VERIFIED）且 launch gate 不通過；`runtime/preflight.py` 以既有 `/scripts` ro mount mapping 解析（`--host-scripts`，可用 `QLIB_HOST_SCRIPTS` 覆寫），並新增 `runtime/tests/test_preflight_p10.py`。**文件精度**：README／§21.1／附錄 C 的 audit 指針改為 `t_d7f48c7a`；附錄 C 補記 v1.1.0 的 §16.2-P2 語意校正；§9.4 的 fail-closed 分支計數修正為六項；README Provenance 措辭改為「除附錄 C 記載之變更外」。**語意不變**：Nautilus 仍為 future/out-of-scope/non-blocking；production 仍為 sequential A→B→C；§7.2 full-backtest 定義不變 | auditor t_d7f48c7a（v1.1.0，FAIL）的 F1 blocking、F2/F3 同批 conformance gap、M1–M4 文件精度，本卡 t_4d6c5cd5 之最小 remediation；不新增 Manager/Service/Factory/Registry/Orchestrator、daemon、queue、resolver 或第二套 runtime |

| v1.2.0 | 2026-09-13 | **新增 §14.4 automatic production handoff trigger**：ownership 為 default 的單一 no-agent script-only cron（每輪「檢查 → 必要時 append 1 張 → 結束」）；新增 repo `runtime/production_handoff.py`（deterministic one-round tail append：chain-head／blocked／incident／tail 狀態 gates、reviewed pool `/results/_handoff/candidates.json` 的候選選擇與 `/results/*/family.json` fingerprint 去重、`parents=[tail_id]` + `idempotency_key=<family_id>` 建卡、同輪 `family.json` 落地與讀回、全部歧義 fail-closed 為單一 finding 並去重）與 `runtime/tests/test_production_handoff.py`（22/22）；明文寫 auditor 不是每張 strategy card 的 production stage（不得新增 auditor 子卡）。**語意不變**：§7.2 sequential A→B→C、§14.2 tail append 演算法、fingerprint 規則、`family.json` rules、§12.5/§12.6、INV-8/§1.2（無新 Manager/Service/Factory/Registry/Orchestrator、daemon、queue、第二套 runtime）皆未改 | ChatGPT 卡片 t_0626a619（漢秦哥批准的 production hardening：把「單張自動執行」補成 terminal 後自動接下一張，維持 sequential 且不新增服務）；實作與第一次真實 handoff 證據見 §14.4 的 `[V]` 條目 |
| v1.3.2 | 2026-09-13 | **F3 獨立 gross PnL 會計**：§7.2 新增 `[C]`（`gross_pnl` 只能來自獨立的 price-PnL accumulator：每個 exit／flatten 只累加 exit proceeds − cost basis，不含 fee／funding；entry／DCA add 只改 qty／cost；**不得**再由 net 反向回推；`pnl_decomposition` 必須是兩個獨立來源的交叉比對且有負向控制證明非恆真），§22 新增 A25，§25 新增對應禁令；`container/scripts/20_strategy_a_run.py` 新增 `exit_price_pnl()` / `pnl_decomposition_ok()`、`simulate()` 內 4 個 exit／flatten 路徑各自餵入 `gross_pnl` accumulator（v1.3.1 的 `"gross_pnl": realized + fees_total + funding_paid` 已刪除）、`summarize()` 的 `pnl_decomposition` 改呼叫同一函式；`container/scripts/tests/test_strategy_a_engine.py` 新增 `TestGrossPnlAccounting` 6 個檢定（29 → 35 tests：單次 TP 與 ladder stop 的獨立手算 gross/net、`fee_2x` 只動 net 不動 gross、gross 路徑 monkeypatch 負向控制、fee「只扣不入帳」負向控制、gross 不得反向回推的結構守衛）。**F4 audit-only staging**：§23 新增 checklist item 11、§25 新增禁令——engine 變更的 auditor 可執行驗證走**明確非 production** 的 staging path（host `qlib-apple-container/staging/v1.3.2/**` ＋ container `/qlib/work/staging/v1.3.2/**`，含自身 `SHA256SUMS`／`README`），bytes 與 repo commit 逐位元一致，auditor 在 `qlib-run` 內以 `/opt/venv/bin/python` 執行；staging 不新增 daemon/service、**不覆蓋 host `/scripts` 的 frozen A v1 部署副本**、不對 `/results` 產生任何 production artifact。**語意不變**：sequential A→B→C、§7.3 selector/disposition、§9.4 reconciler、§14.4 handoff、§16 preflight、§7.2 per-fill 成本會計、DCA provenance classification、INV-8/§1.2（未新增 Manager/Service/Factory/Registry/Orchestrator、daemon、queue、第二套 engine 或第二套 runtime）；Strategy A v2 仍未建立 family／card／results，handoff cron 仍 paused | ChatGPT 卡片 t_33457313（auditor t_23f4c3ef 對 v1.3.1 的 FAIL：F3 gross PnL 由 net 反向回推、F4 缺少 auditor 可在 `qlib-run` 執行且與 commit bytes 一致的 staging）；PM 補充見該卡 comment |
| v1.3.1 | 2026-09-13 | **F1 per-fill 成本會計**：§7.2 新增 `[C]`（每個 entry／DCA add／exit fill 於 fill 時點把 taker fee 扣入 realised equity；`net_pnl`／`ending_equity`／每日 equity marks／Sharpe／margin 判定全為 net-of-fee；成本壓力軌不得是 no-op），§25 新增對應禁令，§22 新增 A23；`container/scripts/20_strategy_a_run.py` 改以單一 `charge_fee()` 在每個 fill 扣款，`container/scripts/tests/test_strategy_a_engine.py` 新增 free／costly／`fee_2x` 迴歸（25 → 29 tests，該 4 個新檢定在未修版本上 FAIL）。**F2 DCA provenance**：§7.2 新增 provenance classification `[C]`（`PROJECT_PRE_REGISTERED_SEARCH_DOMAIN`／`PROJECT_PRE_REGISTERED_CONSTANT`／`USER_FIXED`）、§10.2 新增 run-spec provenance status 必填、§25 新增禁令、§22 新增 A24；`runtime/templates/strategy_a_v2_{round,run}_spec.template.json` 把 `base_quote=1000` 由 user-fixed 改標 project 常數、`size_multiplier={1.0,1.1}` 改標 pre-registered search domain（1.1 不再稱 invariant）、`user_fixed_invariants` 移除 `base_quote_usdt` 並保留有 operator 證據的項目；`runtime/strategy_a_v2_counts.py` 新增可執行 provenance 檢查（含 round-spec／run-spec 分類一致），`runtime/tests/test_strategy_a_v2_counts.py` 新增 5 個負向控制（9 → 14 tests）。**M1 archive hygiene**：§13 新增 `operator_stopped` archive hygiene `[C]`（已停止 strategy 的 exact runner/test 不得留在 active runtime 路徑，必須 exact bytes 移到 archive-only evidence 路徑並保留 checksum）；Strategy B 的 runner/test 由 `container/scripts/**` 移到 `evidence/strategy-b-operator-stopped/runtime/**`（sha 不變）、host `/scripts` 部署副本移除、stop record 與 `evidence/README.md` 更新。**語意不變**：sequential A→B→C、§7.3 selector/disposition、§9.4 reconciler、§14.4 handoff、§16 preflight、INV-8/§1.2（未新增 Manager/Service/Factory/Registry/Orchestrator、daemon、queue、第二套 engine 或第二套 runtime）；Strategy A v2 仍未建立 family／card／results，handoff cron 仍 paused | ChatGPT 卡片 t_6c83c9fb（auditor t_246c62d7 對 v1.3.0 的 FAIL：F1 A v2 費用未扣入 realised equity、F2 DCA provenance 誤標、M1 B archive hygiene） |
| v1.3.0 | 2026-09-13 | **DCA parameter domain 全量納入**：§7.2 全量回測改為六維（新增 `DCA parameter domain` 與「每個 cohort 都跑完整 strategy × DCA 乘積」），§10.2 run-spec 新增必填 `dca_domain`／`selector_version`／`disposition_version`／`expected`。**cohort-level survivor disposition**：新增 §7.3（deterministic、historical-only selector；survivor 五項要求；0/1/>1/coverage 的 disposition 與 verdict 對映；鄰域判定禁用 OOS），§7.2 明寫跨 cohort median 不得再作 family gate（只能 `non_gating` diagnostic）、insufficient trades 只淘汰該 cohort；§22 新增 A21/A22 並改寫 A19；§24.6 新增 worked example。**handoff candidate requirements**：§14.3 `fingerprint_input` 追加 DCA domain＋eligible universe＋selector/disposition 版本（既有 family 一律 grandfather、不回填），§14.4 新增 v1.3.0 candidate card requirements 與 fail-closed finding `candidate_body_not_v13`。**Strategy B cleanup**：§13 新增 `operator_stopped` 類別；§14.4/§21.1 載明 B(`t_3e696dce`) operator-stopped、handoff cron 保持 paused、R5/R6 前置條件。**語意不變**：sequential A→B→C、§9.4 reconciler、§11、§12、§15、§16、INV-8/§1.2（未新增 Manager/Service/Factory/Registry/Orchestrator、daemon、queue 或第二套 runtime） | ChatGPT 卡片 t_ad2e119e（漢秦哥決策：STOP Strategy B、修正 full-backtest 語意、以新 family 重啟 Strategy A） |

驗證方式（v1.0.1）：全文 cross-reference 掃描（`metadata`/`scheduled`→`blocked`/`/qlib/work` shared-layer 三組字串逐條核對）+ §22 A1–A18 自檢；文件狀態：AUDITED PASS / FROZEN（auditor re-audit t_e35c39c0，2026-09-12）。

驗證方式（v1.1.0）：全文 cross-reference 掃描（Nautilus 相關句逐條核對是否仍暗示 mandatory / authoritative gate、`research-only` 降級條款是否已移除、裸 `python` 檢查是否已改為 `/opt/venv/bin/python`）+ §22 A1–A20 自檢 + `runtime/` 三支腳本實跑輸出（`evidence/runtime-readiness-20260913.json`）；文件狀態：AUDITED FAIL（audit t_d7f48c7a，2026-09-13）。

驗證方式（v1.1.1）：§26 change control 檢查（版本／日期／條號／理由／驗證方式）+ §22 A1–A20 自檢；§9.4 邏輯層 `python3 runtime/tests/test_reconcile.py` 23/23、§16.2 P9/P10 `python3 runtime/tests/test_preflight_p10.py` 10/10（stdlib unittest，真實檔案系統 + 注入 kernel 讀回）；`python3 runtime/preflight.py` → P1–P8 PASS、rc=0（未給 `--attempt-dir` 時 P9/P10 = `NA`）；`python3 runtime/reconcile.py --dry-run` → rc=0。另以**真實** board 讀回與**真實** host `/scripts` mapping 對抗性實測：consumed（card status=running）+ stale boot + checksum 衝突 + mapping 衝突 → 兩次執行皆 `consumed`、0 incident、0 comment、rc=0；identity 衝突且 DB 讀回不可判定 → `mapping_mismatch` incident（rc=3）；`script.sha256` 相符 → P10 PASS（recomputed 相符）、bogus sha → P10 FAIL + `--launch` rc=1。文件狀態：AUDITED PASS / FROZEN（auditor re-audit t_83682069，2026-09-13；audited content commit 18d6c3f；前次 FAIL audit t_d7f48c7a）。此狀態由 attestation-only metadata finalization 記錄（t_d99fbc51）：不改語意、不改 runtime 程式，亦不 bump 版本。

驗證方式（v1.2.0）：§26 change control 檢查（版本／日期／條號／理由／驗證方式）+ 全文 cross-reference（§14.1–14.3、§14.2 演算法、§10.6、§9.4、§12.5/§12.6、§15 未被改寫；無新 Manager/Service/Factory/Registry/Orchestrator、daemon、queue）；`python3 runtime/tests/test_production_handoff.py` 22/22 OK（stdlib unittest，注入式 kernel 讀回／create，涵蓋唯一放行條件、dry-run 不變更、idempotency 與全部 fail-closed 分支）；`python3 runtime/production_handoff.py --dry-run --json` → `would_append`（tail=`t_97208408`）；第一次真實 handoff（fence-free host context 執行）→ 卡 `t_3e696dce`（`parents=[t_97208408]`、`ready`、`idempotency_key`=family_id）+ `/results/ema-crossover-walkforward-momentum-long-short-v1/family.json` 落地並讀回；重跑 → `noop`、0 重複卡；cron job `624d0be5b23c`（`5 * * * *`、no-agent）已掛載並以 `hermes cron list` 讀回、`~/.hermes/scripts/quant_production_handoff.py` 由 foreign cwd 實跑通過。文件狀態：**AUDITED PASS / FROZEN**（v1.2.0；audited content commit `068d6f7`，auditor `t_7b979fe8`，2026-09-13）；前版 v1.1.1 = AUDITED PASS / FROZEN（auditor re-audit t_83682069）。

驗證方式（v1.3.0）：§26 change control 檢查（版本／日期／條號／理由／驗證方式）+ 全文 cross-reference 掃描（舊語意殘留三組字串：task-level metadata 作 state、對 `scheduled` 卡直接 block、`/qlib/work` 必然 shared-layer freeze；另加「跨 cohort median 作 gate」「單一固定 DCA rail 當 full-backtest」兩組新增禁語）+ §22 A1–A22 自檢 + 實跑證據：
`python3 runtime/strategy_a_v2_counts.py --run-spec runtime/templates/strategy_a_v2_run_spec.template.json --out evidence/strategy-a-v2-counts-20260913.json` → `cohorts=20 strategy=12 dca=48 base_per_cohort=576 per_grid=11520 total=103680`、`fingerprint=MATCH`、`ok=True`、rc=0（20 個 cohort ×12 strategy ×48 DCA ×9 phase grids = 103,680 case evaluations，精確數字由腳本獨立算出並與卡片要求比對）；
`python3 runtime/tests/test_strategy_a_v2_counts.py` → 9/9 OK（含縮減 DCA grid、錯誤總數、fingerprint 缺 DCA 域、切分搬移、run-spec 域不一致五個負向控制）；
`SA_ENGINE_PATH=<repo>/container/scripts/20_strategy_a_run.py python <numpy interpreter> container/scripts/tests/test_strategy_a_engine.py` → **25/25 OK**（14 個 v1 engine 檢定全數保留 + 11 個 v1.3 cohort gate 檢定：selector 排序／決定性／historical-only 守衛／insufficient_trades 只淘汰該 cohort／cohort survivor 五項要求逐一反證／鄰域 60% 門檻／disposition 四bands／rail 四軸對映／DCA 軸確為 engine 輸入）；
`python3 runtime/tests/test_production_handoff.py` → **24/24 OK**（含新增 `candidate_body_not_v13` fail-closed 與 v1.3 body 放行）；
`python3 runtime/preflight.py`（P1–P8）+ `runtime/reconcile.py --dry-run` + `runtime/tests/test_reconcile.py` 23/23 + `runtime/tests/test_preflight_p10.py` 10/10 未受本次改動影響（同批重跑）；
py3.12（container image 版本）`py_compile` 通過；container 內 engine check 仍須於 launch 時在 `qlib-run` 執行（run-spec 的 `engine_selfcheck.must_run_before_launch=true`），本次未啟動任何 Qlib 計算。文件狀態：AUDITED FAIL（v1.3.0；auditor t_246c62d7，2026-09-13）。

驗證方式（v1.3.1）：§26 change control 檢查（版本／日期／條號／理由／驗證方式）+ §22 A1–A24 自檢 + 全文 cross-reference 掃描（新增禁語兩組：「被搜尋的 DCA 軸／無 operator 證據的常數標成 user-fixed」、「operator-stopped runner 留在 active runtime 路徑」；並確認 §7.3 selector/disposition、§9.4 reconciler、§14.4 handoff、§16 preflight、§1.2 非目標未被改寫）+ 實跑證據（全部在 repo `main`，無任何 Strategy A v2 launch、未寫 `/results`）：
**F1（per-fill 成本會計）**：於 `qlib-run` 內以 `SA_ENGINE_PATH=/qlib/work/verify-a/20_strategy_a_run.py /opt/venv/bin/python …/test_strategy_a_engine.py` → **29/29 OK**（既有 25 個檢定全數保留 + 4 個新增 fee 迴歸：單次 TP 的 entry+exit fee 精確算例 `120 − 5.00 − 5.06 = 109.94`、DCA add 逐層費用（entry+level1..4+stop exit 的獨立重算）、`fee_2x` 使 net_pnl／ending_equity 下降且 fees 恰好 2 倍、`capital_utilization` 隨費用變動（證明費用已進入 intra-episode equity mark，而非只在 episode 端點扣））。**同一組新檢定在未修版本（`b7a0e77` 的 bytes）上 4/4 FAIL**（`120.0 != 109.94`、`0.0 != 59.52`、`capital_utilization` 完全不變），確認迴歸真的測到缺陷而非恆真。
**F2（DCA provenance）**：`python3 runtime/strategy_a_v2_counts.py --run-spec runtime/templates/strategy_a_v2_run_spec.template.json --out evidence/strategy-a-v2-counts-20260913.json` → `cohorts=20 strategy=12 dca=48 base_per_cohort=576 per_grid=11520 total=103680`、`fingerprint=MATCH`、`ok=True`、rc=0，新增 `run_spec_check.provenance_agrees=true`（與 v1.3.0 的計數逐項相同）；`python3 runtime/tests/test_strategy_a_v2_counts.py` → **14/14 OK**（既有 9 個 + 5 個 provenance 負向控制：`base_quote` 標 user-fixed、被搜尋的 `size_multiplier` 標 user-fixed、刪除有 operator 證據的不變項、run-spec 與 round-spec 分類不一致，以及「正確分類必須零 problem」）。
**M1（archive hygiene）**：`git mv` 後 `shasum -a 256` 顯示 `evidence/strategy-b-operator-stopped/runtime/30_strategy_b_run.py` = `f10a64cdca3daf8b409070f6e42d39015a5df65a30de6a610cedff2a1fa1b3d4`、`…/tests/test_strategy_b_engine.py` = `574ce5c6e62d42f051dc00162ab0255c94be6e47080e23336cf95eb7182d040f`（與移動前逐位元相同，runner sha 仍等於 u2 run-spec 所 pin 的值）；`container exec qlib-run ls /scripts` 與 `ls /scripts/tests` 已不含 B 檔案（host 部署副本同步移除）；存檔的 B engine test 以 `SB_ENGINE_PATH=<archive path>` 在 `qlib-run` 內重跑 → **27/27 OK**（唯讀驗證，非從 active 路徑執行）。
**未變動項**：`python3 runtime/tests/test_production_handoff.py` 24/24、`python3 runtime/tests/test_reconcile.py` 23/23、`python3 runtime/tests/test_preflight_p10.py` 10/10、`python3 runtime/preflight.py` → P1–P8 PASS／rc=0（P9/P10 無 attempt 故 NA）、`python3 runtime/reconcile.py --dry-run` → `scanned=6 incidents=0`／rc=0；`hermes cron list --all` 讀回 job `624d0be5b23c` = **paused**；board 最新卡仍為本卡（`t_6c83c9fb`），無 Strategy A v2 卡，`/results` 無 `close-vs-sma-mean-reversion-long-flat-v2` 目錄，B 卡仍 `blocked` 且其 `/results` artifacts 未動。文件狀態：AUDITED FAIL（v1.3.1；auditor t_23f4c3ef，2026-09-13；來源為 auditor t_246c62d7 對 v1.3.0 的 FAIL）。

| v1.4.0 | 2026-09-13 | **family gate：all survivors advance**：§6.4 新增 band → verdict 對映 `[C]` 與 mapping 版本揭露；§7.2/§7.3 改為「0 survivor → `REJECT`、**>=1 survivor → `PASS`**（`SURVIVOR_FOUND`／`MULTIPLE_SURVIVORS` 僅為 band）」，`FINALIST` 不再由 cohort-survivor disposition 產生；survivor 數 >1 **不得**使 `performance_claimable=false`（§9.6 為唯一依據）；新增 **§10.8 frozen survivor bundle**（`rounds/<round_id>/survivor-bundle.json`：全部 survivors、`ranking=null`、不得改寫、只由 terminal `DONE` 且 coverage／assertions 全真之 attempt 產生、identity 可獨立重算）＋ §10.1 layout ＋ `runtime/survivor_bundle.py`；§17 改寫凍結對象為 bundle 內全部 survivor 並要求下游逐一處理；§21.1 R5 由 `[T]` 轉 `[V]`（A v2 已完成）並改寫 R6（B v2 preregistration 已備、engine 未實作、未 launch、cron paused）；§22 新增 A26/A27；§23 新增 checklist item 12；§25 新增四條禁令；§24.5/§24.6 對齊；附錄 B 更新 T13/T14 並新增 T15/T16。**engine**：`container/scripts/20_strategy_a_run.py` 的 `family_disposition` 改為 >=1 → `PASS`，新增 `CONTRACT_SEMANTICS_VERSION`、`mapping_version` 與 summary 的 `contract_semantics_version`／`disposition_mapping_version`；`container/scripts/tests/test_strategy_a_engine.py` 由 35 → **37** 檢定（multi-band、順序置換不變、mapping 版本揭露）。**Strategy A v2 round r1 的 `verdict.json` 不回寫**（checksum 仍為 `sha256:cb470adf…`）；survivor bundle 由既有 `artifacts/cohort_survivors.json` 生成並揭露 legacy 來源語意。**Strategy B v1 維持 operator-stopped**（無 verdict、archive-only runner）；新增 **B v2 preregistration**（20 cohorts × 120 strategy × 48 DCA × 10 phase grids = 1,152,000 case evaluations、cohort survivor 語意、v1.4.0 對映、未 launch）。**語意不變**：sequential A→B→C、§7.3 selector／survivor 五項要求、§9.4 reconciler、§14.4 handoff、§16 preflight、§7.2 per-fill 成本會計／獨立 gross PnL／DCA provenance、INV-8/§1.2（未新增 Manager/Service/Factory/Registry/Orchestrator、daemon、queue、第二套 engine 或第二套 runtime） | ChatGPT（GPT-5.6 Sol）卡片 t_24cc6167（operator 決策：>=1 cohort survivor 即通過基本 gate、多個 survivors 全部保留並前進、新增 frozen survivor bundle、B v2 preregistration 備妥但不 launch） |
| v1.4.1 | 2026-09-13 | **F1 canonical identity recipe 明確定義**（auditor t_0bd01630 對 v1.4.0 的唯一 blocking finding）：§10.8 新增 `[C]`「canonical identity recipe」——`bundle_identity_sha256` 的雜湊輸入是移除 `generated_at_utc` **與 `bundle_identity_sha256` 自身**兩欄後的正規化 JSON（`sort_keys=True, separators=(",", ":"), ensure_ascii=False`，UTF-8）之 sha256，輸出 `"sha256:" + hexdigest`，**不得**有其他隱含欄位排除，並附 4 行純 stdlib 獨立重算範例；另新增 `[C]` 明定 `--check` 的重跑比對範圍（除 identity recipe 的兩欄外只額外排除 `contract` 與 `generator.sha256` 這兩個產生者身分欄位，理由是其餘任何 writer 修正都會改變它們，納入比對等同強迫改寫 immutable artifact；兩欄仍留在檔案內且仍被公開 identity 涵蓋）；§10.8「auditor 可重現」改為必須同時成立「公開值＝該檔案自身的 recipe 重算值」與「量測內容＝重讀 attempt artifacts」、`--json` 必須回報 `identity_recomputed_from_persisted_file` 與 `identity_recipe_matches`；§22 A27 補上 identity recipe 要求；§23 新增 checklist item 13；§25 新增禁語一條（不得以非 §10.8 recipe 定義／宣稱 identity、不得把 identity 欄位以外的欄位隱含排除）。**runtime**：`runtime/survivor_bundle.py` 新增 `IDENTITY_EXCLUDED_KEYS`／`identity_problems()`／`content_identity()`，`identity()` 明示 recipe（值不變），`CONTRACT_VERSION` → `v1.4.1`，`--check` 同時驗證公開 digest 與量測內容，`--json` 改為回報**持久化檔案實際公開**的 identity；`runtime/tests/test_survivor_bundle.py` 13 → **17 檢定**（identity 自我排除、第三方純 stdlib 重算、tamper 三態負向控制、修正後的 writer 對既有 frozen 內容仍為 no-op）。**既有 immutable artifact 未改寫**：A v2 round r1 的 `survivor-bundle.json`（`sha256:4638885f…`）、`verdict.json`（`sha256:cb470adf…`）、`result.json`（`sha256:012e6d1a…`）、`artifacts/cohort_survivors.json`（`sha256:74f250cf…`）驗證前後逐位元相同，bundle 公開 identity 仍為 `sha256:c051759f…`，且自 v1.4.1 起可由 auditor 從該檔案獨立重算。**語意不變**：family gate（>=1 → `PASS`）、all-survivors mapping、B v2 preregistration 與未 launch、engine disposition mapping、cron paused、§9.4 reconciler、§14.4 handoff、§16 preflight、INV-8/§1.2（未新增 Manager/Service/Factory/Registry/Orchestrator、daemon、queue、第二套 engine 或第二套 runtime） | ChatGPT（GPT-5.6 Sol）卡片 t_58166acc（auditor t_0bd01630 對 v1.4.0 的 F1 最小 remediation：公開 identity 無法依 v1.4.0 §10.8 措辭重算） |
| v1.4.2 | 2026-09-13 | **F2 重跑比對逐鍵收緊**（auditor t_3edafbb9 對 v1.4.1 的唯一 blocking finding）：§10.8「重跑比對的範圍」`[C]` 收緊為**逐鍵**排除——只有頂層 `contract` 與巢狀 `generator.sha256` 可被忽略，`generator.path` 與其他所有巢狀／頂層欄位（含非 dict 的 `generator`）一律納入比對，任一更寬的排除（尤其整包排除 `generator`）都會讓「改 `generator.path` 並把公開 identity 重算成自洽值」被 false accept；§10.8 標題與範例 JSON 的 `contract` 欄位同步更新；§22 A27 補上逐鍵要求；§23 新增 checklist item 14（`/tmp` 複本上的 `generator.path` 自洽 tamper 必須 rc=1，且 `generator.sha256` 單獨變動仍必須 clean／no-op）；§25 新增禁語一條（不得在重跑比對中排除比 §10.8 所列更寬的範圍、不得整包排除 `generator`）；附錄 B T15 更新為 18 檢定。**runtime**：`runtime/survivor_bundle.py` 的 `content_identity()` 由 `PRODUCER_KEYS = ("contract", "generator")` 改為 `CONTENT_EXCLUDED_TOP_LEVEL_KEYS = ("contract",)` ＋ `CONTENT_EXCLUDED_GENERATOR_KEYS = ("sha256",)`（逐鍵排除；非 dict 的 `generator` 照原樣留在比對內），`CONTRACT_VERSION` → `v1.4.2`；`runtime/tests/test_survivor_bundle.py` 17 → **18 檢定**（新增 F2 regression：改 `generator.path` 並只以第三方純 stdlib recipe 重簽公開 identity → `--check` rc=1／量測不符、writer `refused_different_bytes` 且不覆寫；非 dict `generator` 納入比對；對照控制「只改 `contract`＋`generator.sha256` 並重簽」仍 `check_clean`／`already_identical`）。**既有 immutable artifact 未改寫**：A v2 round r1 的 `survivor-bundle.json`（`sha256:4638885f…`）、`verdict.json`（`sha256:cb470adf…`）、`result.json`（`sha256:012e6d1a…`）、`artifacts/cohort_survivors.json`（`sha256:74f250cf…`）驗證前後逐位元相同，bundle 公開 identity 仍為 `sha256:c051759f…`。**語意不變**：identity recipe（§10.8 兩欄排除）、family gate（>=1 → `PASS`）、all-survivors mapping、`ranking=null`、B v2 preregistration 與未 launch、engine disposition mapping、cron paused、§9.4 reconciler、§14.4 handoff、§16 preflight、INV-8/§1.2（未新增 Manager/Service/Factory/Registry/Orchestrator、daemon、queue、第二套 engine 或第二套 runtime） | ChatGPT（GPT-5.6 Sol）卡片 t_670a86af（auditor t_3edafbb9 對 v1.4.1 的 F2 最小 remediation：重跑比對多排除了 `generator.path`） |
| v1.5.0 | 2026-09-13 | **新增 §27 Post-Survivor Lifecycle**（在 §7.3 family gate **之後**，且**不改變任何既有 PASS/REJECT**）。①**file-only survivor index**（`/results/_survivors/survivor-index.json`，derived/rebuildable，**不是** Registry service）：掃描既有 frozen bundles，`survivor_id = "sv-" + sha256(canonical({family_id, round_id, run_id, bundle_identity_sha256, cohort, strategy_params, dca_params}))[:16]`，`research_data_cutoff` 只在 `round-spec.json` 仍等於 bundle 所記 checksum 時接受；缺 checksum／duplicate survivor_id／bundle invalid／來源不一致（目錄名、`kanban_task_id`、round-spec checksum）／param cell 非註冊軸一律 fail-closed。②**append-only forward evidence**（`_survivors/forward/<survivor_id>.jsonl`）：slice schema 必填、嚴格 post-freeze（`data_start > cutoff`）、不得與已記錄 slice 重疊、params 與 bundle identity 必須是 incumbent 的、寫入後 readback；無真實計算結果時正確狀態是 zero-forward，**不得**偽造。③**challenger rule**：改任一 strategy／DCA 參數不得覆寫 incumbent（新 family ＋ `family.json.challenger_of`），challenger 的 `oos_start` 必須晚於自身 preregistration cutoff，且必須重走完整 full-backtest gate 才能進入 index。④**leaderboard v1**（`leaderboard.json` ＋ `leaderboard.csv` ＋ Top-10）：透明 deterministic ordering（`has_forward` → forward sharpe → forward return → forward max_dd_pct 絕對值 → `oos_sharpe` → robustness stress floor → neighbourhood → `survivor_id`）；`evidence_state` ∈ {`FROZEN_ONLY`,`ACCUMULATING`,`FORWARD_POSITIVE`,`FORWARD_DEGRADED`} **只描述、永不回寫 PASS**；掉出 Top-10 **不等於** REJECT。⑤**champion／live-candidate 邊界**：`champion_candidate` = Top-10 ＋ `FORWARD_POSITIVE`，v1.5 不發實盤訊號、不配置資金；future live selector 必須另看 correlation／symbol／timeframe exposure／family concentration。**runtime**：新增 `runtime/survivor_index.py`、`runtime/survivor_leaderboard.py`、`runtime/tests/test_post_survivor.py`（**25 檢定**）；`runtime/survivor_bundle.py` 的 `CONTRACT_VERSION` → `v1.5.0`（§10.8 允許排除的產生者身分欄位，語意不變，既有 bundle 的 `--check` 仍 clean）。**seed**：既有 A v2 frozen bundle → index 恰 2 survivors（`BTCUSDT/1h`、`SOLUSDT/4h`）、leaderboard 兩列皆 `FROZEN_ONLY`、0 forward slices、0 `champion_candidate`。**文件**：§10.1 佈局追加 `_survivors/**`、§10.8 標題＋範例契約字串、§21.1 新增 R7、§22 新增 A28、§23 新增 checklist item 15、§25 新增五條禁令、附錄 B 新增 T17/T18。**語意不變**：family gate（>=1 → `PASS`）、all-survivors mapping、§10.8 identity recipe 與重跑比對範圍、B v2 preregistration 與未 launch、engine、cron paused、§9.4 reconciler、§14.4 handoff、§16 preflight、INV-8/§1.2（未新增 Manager/Service/Factory/Registry/Orchestrator、daemon、queue、第二套 engine、第二套 backtester） | ChatGPT（GPT-5.6 Sol）卡片 t_a7cfcdfd（operator 要求完善 Runtime SOP：讓所有通過 full-backtest 的 frozen survivors 持續累積 unseen evidence，形成可重算 Top-10 leaderboard，作為未來 Paper/Testnet/Live 候選來源） |
| v1.5.1 | 2026-09-13 | **F1／F2／F3 最小 remediation**（auditor t_57357d4c 對 v1.5.0 的三個 blocking findings）：①**§27.1 寫入邊界改為工具層強制**——`survivor_index.py --out` 與 `survivor_leaderboard.py leaderboard --out-dir` 必須把候選路徑與 `<results-root>/_survivors` **兩側 realpath 解析後**比對前綴，界外即 rc=1 且不寫入（symlink 與 `..` 段皆無效），界內子路徑仍允許；②**§27.3 新增 slice `source_run` 出處契約**——`attempt_dir`（結果樹內、非 `_survivors/**`、目錄名等於 `run_id`）＋ terminal `DONE` sentinel（sha256 相符、`status=DONE`、`run_id`／`task_id`／`family_id` 相符、非 frozen research run）＋ sentinel 記錄且與磁碟相符的 `result.json` checksum ＋ 與 slice 逐欄相等的 `result.json.forward_slice`；寫入與排名時**都**重新驗證，任一不符即拒收／拒排名（`SLICE_SCHEMA_VERSION` 1 → 2）；③**§27.2 fail-closed 清單第 4 項**明定 `kanban_task_id` 於 bundle 或 `family.json` **任一方缺漏**即來源不一致（原本只在兩側皆 truthy 時比對，導致「移除 bundle 的 ownership id 並自洽重簽 identity」被 false accept）；§25 新增三條禁語（越界寫入、以 slice 自身宣告當證據、缺 ownership id 放行）；§22 A28 新增③之二／④之二與 ⑧ 的 30 檢定；§23 item 15 補上三組 regression；附錄 B T17/T18 更新。**runtime**：`runtime/survivor_index.py`（`write_boundary()`／`outside_write_boundary()`、ownership id fail-closed、`CONTRACT_VERSION` → `v1.5.1`）、`runtime/survivor_leaderboard.py`（`source_run_problems()`、`SLICE_REQUIRED` 增 `source_run`、`slice_problems`／`read_slices` 需 `results_root`、`--out-dir` 邊界檢查、`CONTRACT_VERSION` → `v1.5.1`、`SLICE_SCHEMA_VERSION` → 2）；`runtime/tests/test_post_survivor.py` 25 → **30 檢定**（新增 F1 邊界兩組、F2 出處兩組、F3 ownership 一組；五組在 `ceeadb1` 的 bytes 上實測 4 FAIL（邊界 2／出處 1／ownership 1），修正後 30/30 OK）。**既有 immutable artifact 未改寫**：A v2 round r1 的 `survivor-bundle.json`（`sha256:4638885f…`）、`verdict.json`（`sha256:cb470adf…`）、`result.json`（`sha256:012e6d1a…`）、`artifacts/cohort_survivors.json`（`sha256:74f250cf…`）、`round-spec.json`（`sha256:e0b348bf…`）前後逐位元相同，bundle 公開 identity 仍為 `sha256:c051759f…` 且 `survivor_bundle.py --check` 仍 rc=0／`check_clean`（`runtime/survivor_bundle.py` 未改動）。**derived artifact 重建**：`_survivors/survivor-index.json` 與 `leaderboard.json` 因內含 `contract` 版本字串而在 `_survivors/**` 內重建一次（`leaderboard.csv` 逐位元不變）；re-seed 後恰 2 survivors、兩者 `FROZEN_ONLY`、0 forward slices、0 `champion_candidate`，`--check` 皆 rc=0。**語意不變**：identity recipe（§10.8 兩欄排除）、重跑比對逐鍵豁免、family gate（>=1 → `PASS`）、all-survivors mapping、`ranking=null`、challenger rule、B v2 preregistration 與未 launch、engine、cron paused、§9.4 reconciler、§14.4 handoff、§16 preflight、INV-8/§1.2（未新增 Manager/Service/Factory/Registry/Orchestrator、daemon、queue、第二套 engine 或第二套 runtime） | 卡片 t_171ba94f（auditor t_57357d4c 對 v1.5.0 的 F1／F2／F3 最小 remediation：未受約束的 `--out`／`--out-dir` 可覆寫 frozen artifact、無出處的 forward slice 可變成 `FORWARD_POSITIVE`／`champion_candidate`、缺 `kanban_task_id` 放行） |
| v1.5.2 | 2026-09-13 | **F1／F2／F3 最小 remediation**（auditor t_346bcc04 對 v1.5.1 的三個 blocking trust-boundary 殘留）：①**§27.1 reserved root 納入檢查**——`<results-root>/_survivors` 自身不得為 symlink，其 resolved 路徑必須恰為 resolved results root 之下的 literal `_survivors`；root／ancestor escape 一律在任何寫入前 rc=1，並套用到 index `--out`、leaderboard `--out-dir` 與 `forward` append；②**§27.3 `source_run.attempt_dir` 必須為 absolute path**（relative 會以 reader cwd 解析，fail-closed）；③**§27.2 第 4 項 ownership id 必須兩側皆為 non-empty string 且逐字相等**（int／bool／list／null／空字串即使兩側同值亦 fail-closed）。新增三組 regression（`test_post_survivor.py` 30 → **33**，7 檔合計 **130 檢定**全綠）；`_survivors/{survivor-index.json,leaderboard.json}` 因 `contract` 版本字串而在界內重建（`leaderboard.csv` 逐位元不變），frozen artifacts 與 bundle identity 逐位元不變；未改 leaderboard 排序、survivor／family gate、B v2 未 launch、cron `624d0be5b23c` 維持 paused、未新增 service／daemon／queue／Registry／Orchestrator。驗證方式見本附錄 v1.5.2 段與 §23 item 16。 |

驗證方式（v1.3.2）：§26 change control 檢查（版本／日期／條號／理由／驗證方式）+ §22 A1–A25 自檢 + 全文 cross-reference 掃描（新增禁語兩組：「把 `gross_pnl` 由 net 反向回推」、「覆蓋 host `/scripts` 的 frozen A v1 部署副本」；並確認 §7.2 per-fill 成本會計與 DCA provenance classification、§7.3 selector/disposition、§9.4 reconciler、§14.4 handoff、§16 preflight、§1.2 非目標未被改寫）+ 實跑證據（全部在 repo `main`；**未建立**任何 Strategy A v2 family／card／results，未寫 `/results`，未新增 daemon/service）：
**F3（獨立 gross PnL 會計）**：`container/scripts/20_strategy_a_run.py` 新增 `exit_price_pnl()`／`pnl_decomposition_ok()`，`simulate()` 的 **4 個** exit／flatten 路徑各自餵入 `gross_pnl` accumulator（`gross_pnl += exit_price_pnl(qty * xpx, cost)`），`"gross_pnl": realized + fees_total + funding_paid` 已刪除，`summarize()` 的 `pnl_decomposition` 改呼叫同一函式。於 `qlib-run` 內以 **staging bytes** 執行：
`container exec qlib-run env SA_ENGINE_PATH=/qlib/work/staging/v1.3.2/20_strategy_a_run.py /opt/venv/bin/python /qlib/work/staging/v1.3.2/tests/test_strategy_a_engine.py` → **`Ran 35 tests ... OK`**（v1.3.1 的 29 個全數保留 + `TestGrossPnlAccounting` 6 個：單次 TP 與 ladder stop 的獨立手算 gross/net、`fee_2x` 只動 net 不動 gross、gross 路徑 monkeypatch 負向控制、fee「只扣不入帳」負向控制、gross 不得反向回推的結構守衛）。**同一組新檢定在未修版本（`c04504c` 的 bytes，sha256 `dda304307051564f8c104b18ad4fc06887b17c4757c3ab73eb7168a1181831a0`）上 5/6 FAIL**（`FAILED (failures=1, errors=4)`）。
**恆真性證明（negative control 的核心）**：以同一 crafted 單次 TP fixture 跑「shipped」與「fee 被扣但未入帳」兩個變體（`/qlib/work/staging/v1.3.2/f3_vacuity_demo.py`）——
v1.3.1 bytes：`gross=109.9400 fees=0.0000 net=109.9400 gap=0.000000`（gross 隨 net 移動，任何 `gross − fees − funding − net` 形式的斷言**恆為 0、永遠不會 FAIL**）；
v1.3.2 bytes：`gross=120.0000 fees=0.0000 net=109.9400 gap=10.060000`（gross 不隨 tamper 移動，`pnl_decomposition_ok` = **False**）。host 端同檔以 numpy 2.0.2 interpreter 亦為 **35/35 OK**。
**F4（audit-only staging，不 launch）**：host `/Users/hong/workspace/qlib-apple-container/staging/v1.3.2/`（`20_strategy_a_run.py`、`tests/test_strategy_a_engine.py`、`SHA256SUMS`、`README.md`）＋ container `/qlib/work/staging/v1.3.2/`；`shasum -a 256 -c SHA256SUMS` → OK，engine sha256 `c4f9a216ce424a6c0c34379f29a74d0b348da9036818b0b590bfcaeba853b40f`（51,775 bytes）、test sha256 `d5ddb2475e047addf5a3fe9e4c4fdccb98526e29a79d90fa3b977a8a45097b5d`（33,984 bytes），container 內 `sha256sum` 逐位元相同；與 repo commit bytes 的一致性以 `git cat-file blob HEAD:<path> | shasum -a 256` 重算比對（見該 staging `README.md` 的指令）。**frozen A v1 部署副本未動**：host `qlib-apple-container/scripts/20_strategy_a_run.py` 仍為 sha256 `8f3ce89deebc14902f940d677cf5e3fef209e3f3a78d5dc10d0974964961df16`／38,208 bytes／mtime `2026-09-13 02:16:18`，container `sha256sum /scripts/20_strategy_a_run.py` 相同、`/scripts/tests` 內容不變；staging 不改 active `/scripts` mount、不寫 `/results`、不新增 cron/daemon/service。
**未變動項**：`python3 runtime/tests/test_production_handoff.py` 24/24、`runtime/tests/test_reconcile.py` 23/23、`runtime/tests/test_preflight_p10.py` 10/10、`runtime/tests/test_strategy_a_v2_counts.py` 14/14、`runtime/preflight.py` → P1–P8 PASS／rc=0（P9/P10 無 attempt 故 NA）、`runtime/reconcile.py --dry-run` → rc=0；`python3 runtime/strategy_a_v2_counts.py --run-spec runtime/templates/strategy_a_v2_run_spec.template.json` → `cohorts=20 strategy=12 dca=48 base_per_cohort=576 per_grid=11520 total=103680 fingerprint=MATCH ok=True`（**counts 仍為 103,680**）；`hermes cron list --all` 讀回 job `624d0be5b23c` = **paused**；`/Volumes/ExpansionDrive/qlib-results/` 無 `close-vs-sma-mean-reversion-long-flat-v2` 目錄，本 run 未呼叫 `kanban_create`（無 A v2 卡）；B 卡仍 `blocked` 且其 `/results` artifacts 未動。文件狀態：**AUDITED PASS / FROZEN**（v1.3.2；audited content commit `0363011`，auditor `t_3ffaeeb8`，2026-09-13；卡片 t_33457313 為 auditor t_23f4c3ef 對 v1.3.1 的 FAIL（F3/F4）之最小 remediation）。

驗證方式（v1.4.0）：§26 change control 檢查（版本／日期／條號／理由／驗證方式）＋ §22 A1–A27 自檢 ＋ 全文 cross-reference 掃描（新增禁語四組：「survivor 數 >1 作為 `performance_claimable=false` 的理由」、「對 survivors 排序／淘汰／二選一」、「改寫已凍結 `survivor-bundle.json`／由非 terminal attempt 產生 bundle」、「把 v1.3.x 已凍結 artifacts 回寫成 v1.4.0 語意」；並確認 §7.3 selector／survivor 五項要求、§9.4 reconciler、§14.4 handoff、§16 preflight、§1.2 非目標未被改寫）＋ 實跑證據（全部在 repo `main`；本次**未**建立任何新 family／卡／run；唯一寫入 `/results` 的是 A v2 round r1 的 `survivor-bundle.json`，未動任何既有 artifact）：
**engine（v1.4.0 mapping）**：`SA_ENGINE_PATH=<repo>/container/scripts/20_strategy_a_run.py python3 container/scripts/tests/test_strategy_a_engine.py` → **37/37 OK**（v1.3.2 的 35 個全數保留 ＋ 2 個新增：multi-survivor band 的 permutation 不變、`disposition_mapping_version` 揭露）。**audit-only staging**：host `qlib-apple-container/staging/v1.4.0/**` ＋ container `/qlib/work/staging/v1.4.0/**`（`SHA256SUMS`：engine `sha256 1c42d254…`、test `sha256 61389308…`，與 repo commit bytes 逐位元一致；`sha256sum -c` OK），在 `qlib-run` 內以 `SA_ENGINE_PATH=/qlib/work/staging/v1.4.0/20_strategy_a_run.py /opt/venv/bin/python …/tests/test_strategy_a_engine.py` 實跑 → **`Ran 37 tests ... OK`**。active `/scripts/20_strategy_a_run.py` 仍為 A v2 所 pin 的 v1.3.2（`sha256 c4f9a216…`），**未被覆蓋**。
**frozen survivor bundle**：`python3 runtime/tests/test_survivor_bundle.py` → **13/13 OK**（含 legacy 揭露、count／order 不一致拒寫、非 terminal `DONE` 拒寫、`coverage_complete`／assertions 負向控制、idempotent 不覆寫、identity 對 survivor 順序敏感）。以既有 artifacts 生成 A v2 round r1 的 bundle：`python3 runtime/survivor_bundle.py --attempt-dir …/attempts/close-vs-sma-mean-reversion-long-flat-v2-r1-u1` → `written`、`survivor_count=2`、`survivors=[BTCUSDT/1h, SOLUSDT/4h]`、bundle identity `sha256:c051759f…`；`--check` → `check_clean`、rc=0。**immutable 未動**：該 round `verdict.json` 仍為 `sha256:cb470adf…`（與 v1.3.2 記錄的 `verdict_sha256` 一致）、`result.json` 仍為 `sha256:012e6d1a…`；bundle 內 `source_attempt_semantics` 逐字揭露來源為 pre-v1.4.0 對映（`FINALIST` ＋ `performance_claimable=false`），同時給出 v1.4.0 的 `verdict=PASS`，`source_verdict_not_rewritten=true`。
**B v2 preregistration**：`python3 runtime/tests/test_strategy_b_v2_templates.py` → **8/8 OK**（20 cohorts、120 strategy × 48 DCA × 10 phase grids = 1,152,000、cohort survivor 語意、v1.4.0 對映、未 launch 聲明；注入 legacy `FINALIST`、縮域、錯誤計數、搬移 split、捏造 fingerprint 皆被拒）。
**未變動項**：`test_strategy_a_v2_counts.py` 14/14、`test_reconcile.py` 23/23、`test_preflight_p10.py` 10/10、`test_production_handoff.py` 24/24；`python3 runtime/strategy_a_v2_counts.py --run-spec runtime/templates/strategy_a_v2_run_spec.template.json` → `cohorts=20 strategy=12 dca=48 base_per_cohort=576 per_grid=11520 total=103680 fingerprint=MATCH ok=True`（counts 仍為 103,680）；`hermes cron list --all` 讀回 job `624d0be5b23c` = **paused**；Strategy B v1 卡仍 `blocked`、其 `/results` artifacts 未動。文件狀態：**AUDITED FAIL**（v1.4.0；auditor t_0bd01630 的唯一 blocking finding 為 F1：公開的 frozen bundle identity 無法依 v1.4.0 §10.8 措辭重算；已由 v1.4.1 最小 remediation 修正）。

驗證方式（v1.4.1）：§26 change control 檢查（版本／日期／條號／理由／驗證方式）＋ §22 A1–A27 自檢 ＋ 全文 cross-reference 掃描（新增禁語一組：「以非 §10.8 canonical identity recipe 定義／宣稱 `bundle_identity_sha256`、或把 identity 欄位以外的欄位隱含排除」；並確認 family gate「>=1 → `PASS`」、all-survivors mapping、`ranking=null`、B v2 preregistration 與未 launch、engine disposition mapping、cron paused、§9.4 reconciler、§14.4 handoff、§16 preflight、§1.2 非目標均未被改寫）＋ 實跑證據（全部在 repo `main`；**未建立**任何新 family／strategy 卡／run；**未寫入**任何 `/results` artifact；主機專屬路徑依 `evidence/README.md` redact，完整快照見 `evidence/v1.4.1-bundle-identity-remediation-20260913.json`）：
**F1（identity 可由第三方重算）**：對既有 frozen bundle（`<EXPANSION>/qlib-results/close-vs-sma-mean-reversion-long-flat-v2/rounds/close-vs-sma-mean-reversion-long-flat-v2-r1/survivor-bundle.json`）以純 stdlib（不 import `runtime/survivor_bundle.py`）重算 canonical JSON sha256 →
移除 `generated_at_utc` **與 `bundle_identity_sha256`** 兩欄 → `sha256:c051759fdf8291f66bce8f7249cb226b856c04de2ad78a8d055fdb4657543363`（**逐位元等於**檔案公開值）；
只移除 `generated_at_utc`（v1.4.0 措辭）→ `sha256:810e1d6cf85c05aa99ad396c5fd1ff8b7d3d819b59029a7cea7f7594063f3d6d`（≠ 公開值：v1.4.0 措辭把 identity 欄位算進自己的輸入，永遠無法重算，故 v1.4.1 的兩欄排除是必要的）。
`python3 runtime/survivor_bundle.py --attempt-dir …/attempts/close-vs-sma-mean-reversion-long-flat-v2-r1-u1 --check --json` → rc=0、`result=check_clean`、`survivor_count=2`、`survivors=[BTCUSDT/1h, SOLUSDT/4h]`、`bundle_identity_sha256` = `identity_recomputed_from_persisted_file` = `sha256:c051759f…`、`identity_recipe_matches=true`。
**tamper 負向控制（在 `/tmp` 的複本上執行，全程未碰 frozen artifact）**：複本基線 `--check` rc=0 → 把 `verdict` 改成 `REJECT` 且保留舊 digest → **rc=1**（`publishes bundle_identity_sha256 … but the contract 10.8 recipe (canonical JSON minus generated_at_utc and bundle_identity_sha256) recomputes …`）→ 再把 digest 重算成自洽 → **rc=1**（`the measurement … does not match the attempt's artifacts`）→ 還原複本 → rc=0。
**immutable 未動（驗證前後逐位元相同）**：`survivor-bundle.json` `sha256:4638885f5f3787ee947d860a1fe48627de9802c2e9adb1d13f62a6917240eb53`、`verdict.json` `sha256:cb470adf56e9db19c3e6e6bc352177d634d7ecc800b4ceabaa41394c66ad0bf3`（與 v1.3.2 記錄一致）、`result.json` `sha256:012e6d1a6eed4206e5035796ddeac67d0a3a90f76cee866edb98ed89bb9543e8`、`artifacts/cohort_survivors.json` `sha256:74f250cf165511ba80875af5dc22560ac07292d4445ea8007ccf7f7bcda71081`；bundle 公開 identity 仍為 `sha256:c051759f…`（未重新生成、未覆寫）。
**測試**：`python3 runtime/tests/test_survivor_bundle.py` → **17/17 OK**（v1.4.0 的 13 個全數保留 ＋ 4 個新增：identity 自我排除／公開值必須等於持久化檔案的 recipe 重算值且「只移除 `generated_at_utc`」必須得不出同值／tamper 三態負向控制（舊 digest、自洽 digest、非量測散文皆 rc=1）／修正後的 writer 對既有 frozen 內容仍判 `already_identical` 且 `--check` clean）。
**未變動項**：`python3 runtime/tests/test_strategy_b_v2_templates.py` 8/8、`test_strategy_a_v2_counts.py` 14/14、`test_reconcile.py` 23/23、`test_preflight_p10.py` 10/10、`test_production_handoff.py` 24/24；`python3 runtime/preflight.py` → P1–P8 PASS／rc=0（P9/P10 無 attempt 故 NA）；`python3 runtime/reconcile.py --dry-run` → `scanned=7 incidents=0`／rc=0；`hermes cron list --all` 讀回 job `624d0be5b23c` = **paused**（no-agent）；`qlib-run` = running（`qlib:0.9.7-arm64`，started 2026-09-12T09:29:02Z，未重建）；Strategy B v1 卡仍 `blocked`、其 `/results` artifacts 未動；`/qlib-results` 無 B v2 目錄（未 launch）；本卡只建立 1 張 fresh auditor re-audit 卡，未建立任何 family／strategy 卡。文件狀態：**AUDITED FAIL**（v1.4.1；auditor t_3edafbb9，2026-09-13；卡片 t_58166acc 為 auditor t_0bd01630 對 v1.4.0 的 F1 之最小 remediation，唯一 blocking finding F2 為重跑比對多排除了 `generator.path`）。**v1.4.2 更正**：v1.4.1 已由 auditor t_3edafbb9 判為 **AUDITED FAIL**（唯一 blocking finding F2：重跑比對多排除了 `generator.path`，自洽重簽的 `generator.path` tamper 被 false accept），已由 v1.4.2 最小 remediation 修正。

驗證方式（v1.4.2）：§26 change control 檢查（版本／日期／條號／理由／驗證方式）＋ §22 A1–A27 自檢 ＋ 全文 cross-reference 掃描（新增禁語一組：「在重跑比對中排除比 §10.8 所列更寬的範圍／整包排除 `generator`」；並確認 identity recipe 兩欄排除、family gate「>=1 → `PASS`」、all-survivors mapping、`ranking=null`、B v2 preregistration 與未 launch、engine disposition mapping、cron paused、§9.4 reconciler、§14.4 handoff、§16 preflight、§1.2 非目標均未被改寫）＋ 實跑證據（全部在 repo `main`；**未建立**任何新 family／strategy 卡／run；**未寫入**任何 `/results` artifact；主機專屬路徑依 `evidence/README.md` redact，完整快照見 `evidence/v1.4.2-bundle-replay-scope-remediation-20260913.json`）：
**F2（重跑比對保留 `generator.path`）**：在 `/tmp` 複本上（只把複本的 `source_attempt_dir` rebase 成複本 attempt path、並以 §10.8 canonical recipe 重簽複本公開 identity）建立乾淨基線 → `--check` **rc=0**；改 `survivor-bundle.json` 的 `generator.path = "tampered/other_writer.py"` 並**只**以純 stdlib recipe 把公開 `bundle_identity_sha256` 重算成自洽值後 →
`python3 runtime/survivor_bundle.py --attempt-dir <tmp-attempt> --check --json` → **rc=1**、`REFUSED: the measurement in … does not match the attempt's artifacts`（v1.4.1 為 rc=0／`check_clean`，即 F2 的 false acceptance）；
不帶 `--check` 的 writer → **rc=1**、`already exists with different content`（v1.4.1 為 rc=0／`already_identical`），且 tampered bytes 未被改寫；
把 `generator` 換成非 dict 值並重簽 → **rc=1**（不得被正規化掉）；還原複本 → rc=0。
**對照控制（§10.8 允許的豁免必須仍在）**：只改 `contract` 與 `generator.sha256`（`generator.path` 不變）並重簽 → `--check` **rc=0／`check_clean`**、`identity_recipe_matches=true`；`runtime/survivor_bundle.py` 的 `write_bundle` 回 **`already_identical`**（既有 frozen 內容不被強迫改寫）——若 remediation 改成「完全不排除」，此控制會失敗，故不能以粗暴方式修正。
**F1 未回歸**：純 stdlib（不 import writer）對 frozen `survivor-bundle.json` 依 §10.8 重算 → `sha256:c051759fdf8291f66bce8f7249cb226b856c04de2ad78a8d055fdb4657543363`（**逐位元等於**公開值）；只移除 `generated_at_utc` → `sha256:810e1d6cf85c05aa99ad396c5fd1ff8b7d3d819b59029a7cea7f7594063f3d6d`（≠ 公開值）。
**測試**：`python3 runtime/tests/test_survivor_bundle.py` → **18/18 OK**（v1.4.1 的 17 個檢定全數保留 ＋ 1 個 F2 regression：改 `generator.path` 並只重簽公開 identity → `--check` rc=1／量測不符、writer `refused_different_bytes`、非 dict `generator` 納入比對、對照控制仍 `clean`／no-op）；該 regression 已先對 `5d14214` 的 writer 實跑確認為 **FAIL**（非恆真）。
**immutable 未動（驗證前後逐位元相同）**：`survivor-bundle.json` `sha256:4638885f5f3787ee947d860a1fe48627de9802c2e9adb1d13f62a6917240eb53`、`verdict.json` `sha256:cb470adf56e9db19c3e6e6bc352177d634d7ecc800b4ceabaa41394c66ad0bf3`（與 v1.3.2 記錄一致）、`result.json` `sha256:012e6d1a6eed4206e5035796ddeac67d0a3a90f76cee866edb98ed89bb9543e8`、`artifacts/cohort_survivors.json` `sha256:74f250cf165511ba80875af5dc22560ac07292d4445ea8007ccf7f7bcda71081`；bundle 公開 identity 仍為 `sha256:c051759f…`（未重新生成、未覆寫），bundle 內 `generator.path` 仍為 `runtime/survivor_bundle.py`。
**未變動項**：`python3 runtime/tests/test_strategy_b_v2_templates.py` 8/8、`test_strategy_a_v2_counts.py` 14/14、`test_reconcile.py` 23/23、`test_preflight_p10.py` 10/10、`test_production_handoff.py` 24/24；`SA_ENGINE_PATH=<repo>/container/scripts/20_strategy_a_run.py python3 container/scripts/tests/test_strategy_a_engine.py` → **37/37 OK**（audit-only staging `staging/v1.4.0/**` 之 `SHA256SUMS` 亦 `-c` OK、同法 37/37；active `/scripts` 與 `/results` 未動）；`python3 runtime/strategy_a_v2_counts.py --run-spec runtime/templates/strategy_a_v2_run_spec.template.json` → `cohorts=20 strategy=12 dca=48 base_per_cohort=576 per_grid=11520 total=103680 fingerprint=MATCH ok=True`（counts 仍為 103,680）；`python3 runtime/preflight.py` → P1–P8 PASS／rc=0（P9/P10 無 attempt 故 NA；P3 的 hidden probe 已即時移除、`/Volumes/ExpansionDrive/qlib-results` 讀回無殘留）；`python3 runtime/reconcile.py --dry-run` → `scanned=7 incidents=0`／rc=0；`hermes cron list --all` 讀回 job `624d0be5b23c` = **paused**（no-agent）；`qlib-run` = running（`qlib:0.9.7-arm64`，started 2026-09-12T09:29:02Z，未重建）；Strategy B v1 卡仍 `blocked`、其 `/results` artifacts 未動；`/qlib-results` 無 B v2 目錄（未 launch）；本卡只建立 1 張 fresh auditor re-audit 卡，未建立任何 family／strategy 卡。文件狀態：**AUDITED PASS / FROZEN**（v1.4.2；audited content commit d699527，auditor t_dedbe003，2026-09-13；卡片 t_670a86af 為 auditor t_3edafbb9 對 v1.4.1 的 F2 之最小 remediation）。

驗證方式（v1.5.0）：§26 change control 檢查（版本／日期／條號／理由／驗證方式）＋ §22 A1–A28 自檢 ＋ 全文 cross-reference 掃描（新增禁語五組：「為 post-survivor lifecycle 新增 Registry／Orchestrator／service／daemon／queue」、「把 leaderboard 當成新的 PASS/REJECT gate 或把掉出 Top-10 當 REJECT」、「在 forward evidence 中改動 survivor 參數或把 `data_start ≤ cutoff`／重疊窗口當 unseen evidence」、「偽造 forward evidence」、「把 forward computation 當第二套 backtester／parameter search」；並確認 family gate「>=1 → `PASS`」、all-survivors mapping、§10.8 identity recipe 與重跑比對範圍、B v2 preregistration 與未 launch、engine、cron paused、§9.4 reconciler、§14.4 handoff、§16 preflight、§1.2 非目標均未被改寫）＋ 實跑證據（全部在 repo `main`；**未建立**任何新 family／strategy 卡／run；**未執行**任何 Qlib 計算；唯一寫入 `/results` 的是 `_survivors/{survivor-index.json,leaderboard.json,leaderboard.csv}`；主機專屬路徑依 `evidence/README.md` redact，完整快照見 `evidence/v1.5.0-post-survivor-lifecycle-20260913.json`）：
**survivor index（§27.2）**：`python3 runtime/survivor_index.py --json` → `written`、`bundle_count=1`、`survivor_count=2`；`--check` → rc=0／`check_clean`。兩名 survivor 的 `survivor_id` 為 `sv-904822905a811669`（`BTCUSDT/1h`，params `sha256:09343579…`）與 `sv-f762a1da8909a5bf`（`SOLUSDT/4h`，params `sha256:95ca1f16…`），兩者的 `research_data_cutoff` 皆為 `2026-09-10`，取自 `<round>/round-spec.json` 的 `data.data_end`——該檔 sha256 必須等於 bundle `source_artifacts["round-spec.json"]`（`sha256:e0b348bf…`）才會被接受。
**leaderboard（§27.5）**：`python3 runtime/survivor_leaderboard.py leaderboard --json` → `written`；`--check` → rc=0／`check_clean`。恰兩列、`top10_count=2`、兩者 `evidence_state=FROZEN_ONLY`、`champion_candidate=false`、forward metrics 全為 `null`（**零 forward evidence，未偽造任何指標**）。fallback 順序由既有 evidence 客觀產生：`#1 SOLUSDT/4h`（`oos_sharpe=2.17438`、`oos_net_pnl=8485.605171`、stress floor `17375.485091`（`entry_delay_1_bar`）、neighbourhood `0.857143`）、`#2 BTCUSDT/1h`（`oos_sharpe=0.536464`、`oos_net_pnl=6016.344052`、stress floor `11353.765787`（`fee_2x`）、neighbourhood `0.833333`）——非硬寫名字。`/results/_survivors/forward/` **不存在**、0 slices。
**negative controls（`runtime/tests/test_post_survivor.py`，25/25 OK）**：index 重建 deterministic（時戳以外逐欄相等）；缺 `bundle_identity_sha256` → rc=1「missing checksum」；量測值被改而保留舊 digest → rc=1「bundle is invalid」；`family_id` 與目錄名不符／`kanban_task_id` 與 `family.json` 不符／`round-spec.json` checksum 漂移 → rc=1「來源不一致」；param 軸缺漏或出現未註冊鍵 → rc=1；同一 bundle 被掃描兩次（或同一 cohort 於同一 bundle 重複）→ rc=1「duplicate survivor_id」；forward slice `data_start == cutoff` → rc=1；slice 與既有 slice 重疊或完全重複 → rc=1；`params_sha256`／bundle identity 非 incumbent → rc=1「new challenger family」；challenger `oos_start ≤ created_at_utc` → rc=1；leaderboard `--check` 對被手改的 json／不合規的 jsonl 行 → rc=1；Top-10 cap（11 名 survivor → 11 `entries`、10 `top10`）、ordering 與 csv 皆可重算。
**immutable 未動（seed 前後逐位元相同）**：`survivor-bundle.json` `sha256:4638885f5f3787ee947d860a1fe48627de9802c2e9adb1d13f62a6917240eb53`、`verdict.json` `sha256:cb470adf56e9db19c3e6e6bc352177d634d7ecc800b4ceabaa41394c66ad0bf3`、`result.json` `sha256:012e6d1a6eed4206e5035796ddeac67d0a3a90f76cee866edb98ed89bb9543e8`、`artifacts/cohort_survivors.json` `sha256:74f250cf165511ba80875af5dc22560ac07292d4445ea8007ccf7f7bcda71081`；bundle 公開 identity 仍為 `sha256:c051759f…`；`python3 runtime/survivor_bundle.py --attempt-dir <attempt> --check --json` → rc=0／`check_clean`／`identity_recipe_matches=true`（`CONTRACT_VERSION` 的 v1.5.0 更新屬 §10.8 允許排除的產生者身分欄位，故既有 frozen bundle 的重跑比對不受影響——這正是 v1.4.2 逐鍵豁免的對照控制）。
**未變動項**：`test_survivor_bundle.py` 18/18、`test_strategy_b_v2_templates.py` 8/8、`test_strategy_a_v2_counts.py` 14/14、`test_reconcile.py` 23/23、`test_preflight_p10.py` 10/10、`test_production_handoff.py` 24/24（7 個測試檔合計 **122 檢定**全綠，同一 Python 3.9.6 host interpreter）；`hermes cron list --all` 讀回 job `624d0be5b23c` = **paused**；`qlib-run` = running（`qlib:0.9.7-arm64`，started 2026-09-12T09:29:02Z，未重建）；`/qlib-results` 無 `strategy-b-v2` 目錄（未 launch）；Strategy B v1 卡仍 `blocked`、其 `/results` artifacts 未動；本卡未建立任何 family／strategy 卡。**post-freeze 資料現況**：raw klines 最後一根為 `2026-09-13T00:00:00Z`（`market-data-raw/_meta/STATE.json`），已晚於 cutoff `2026-09-10`，因此 forward slices 未來可由既有 engine 的一次真實 run 產生（附錄 B T18；那是需 operator 放行的 launch-bearing 任務，不在本版）。文件狀態：**AWAITING AUDIT**（v1.5.0；依卡片 t_a7cfcdfd）。**v1.5.1 更正**：v1.5.0 已由 auditor t_57357d4c 判為 **AUDITED FAIL**（三個 blocking findings：F1 未受約束的 `--out`／`--out-dir` 可覆寫 frozen bundle／verdict、F2 完全自述的 forward slice 可成為 `FORWARD_POSITIVE`／`champion_candidate`／rank 1、F3 缺 `kanban_task_id` 的 bundle 放行），已由 v1.5.1 最小 remediation 修正。

驗證方式（v1.5.1）：§26 change control 檢查（版本／日期／條號／理由／驗證方式）＋ §22 A1–A28 自檢 ＋ 全文 cross-reference 掃描（新增禁語三組：「本層把 artifact 寫到 `_survivors/**` 之外／以 `--out`／`--out-dir` 覆寫 frozen bundle／verdict／result」、「以 slice 自身宣告的數字當 forward evidence」、「`kanban_task_id` 缺漏時放行」；並確認 §10.8 identity recipe 與重跑比對逐鍵豁免、family gate「>=1 → `PASS`」、all-survivors mapping、`ranking=null`、challenger rule、B v2 preregistration 與未 launch、engine、cron paused、§9.4 reconciler、§14.4 handoff、§16 preflight、§1.2 非目標均未被改寫）＋ 實跑證據（全部在 repo `main`；**未建立**任何新 family／strategy 卡／run；**未執行**任何 Qlib 計算；唯一寫入 `/results` 的是 `_survivors/{survivor-index.json,leaderboard.json,leaderboard.csv}` 的重建；所有攻擊與 tamper 都在 `/tmp` 複本上執行；主機專屬路徑依 `evidence/README.md` redact，完整快照見 `evidence/v1.5.1-post-survivor-boundary-remediation-20260913.json`）：
**F1（寫入邊界）**：在 `/tmp` 複本（真實 A v2 family ＋ `_survivors`）上——`survivor_index.py --out <round>/verdict.json`、`--out <round>/survivor-bundle.json`、經 `_survivors/escape.json` symlink、經 `_survivors/../<family>/rounds/index.json`、`survivor_leaderboard.py leaderboard --out-dir <round>`／`--out-dir <root>` → 全部 **rc=1**、stderr 為 `REFUSED: … is outside the reserved post-survivor write boundary …/_survivors`，且 `survivor-bundle.json`／`verdict.json` 的 sha256 在探測前後不變（v1.5.0 的 bytes 上同兩組指令 rc=0 並實際改寫了 verdict／bundle）。**對照控制**：`_survivors/survivor-index.json`（預設路徑）與 `_survivors/scratch/leaderboard.json`（界內子路徑）皆 rc=0 且寫入成功，`--check` rc=0——此檢查不是「一律拒絕」。
**F2（出處契約）**：同一複本上以一份**無任何出處**的 slice（invented `episodes=999`／`net_pnl=987654.0`／`sharpe=999.0`）呼叫 `forward` → **rc=1**（`slice field 'source_run' is not an object`）、`_survivors/forward/` 未產生任何 jsonl，`leaderboard --json` 該列仍為 `FROZEN_ONLY`、`champion_candidate=false`、`forward_sharpe=null`（v1.5.0 的 bytes 上同一份 slice rc=0、`rank=1`、`FORWARD_POSITIVE`、`champion_candidate=true`）。另有出處但不可驗證的變體全部 rc=1：出處 run 目錄不存在／不是目錄、缺 terminal `DONE`、sentinel `status=FAILED`、`sentinel_sha256` 與磁碟不符、出處位於 `_survivors/**` 內、數字與 pinned `result.json.forward_slice` 不符、`result.json` 事後被改寫（sentinel 記錄的 checksum 不符）、出處屬其他 family。
**F3（ownership id）**：複本上移除 bundle 的 `kanban_task_id` 並以 §10.8 recipe **自洽重簽**公開 identity → `survivor_index.py` **rc=1**／`source ownership is incomplete - kanban_task_id is missing (family.json 't_1f97bf6b', bundle None)`（v1.5.0 的 bytes 上同一 tamper rc=0／`written`、索引照樣列出 2 survivors）；`family.json` 缺該欄的變體亦 rc=1；兩側皆存在且相等時仍 rc=0。
**測試**：`python3 runtime/tests/test_post_survivor.py` → **30/30 OK**（v1.5.0 的 25 個全數保留 ＋ 5 個新增：F1 邊界兩組（含 symlink／`..` 逃逸與界內正向控制）、F2 出處兩組（無出處／出處不可驗證、數字與 pinned result 不符）、F3 ownership 一組）。**非恆真證明**：把同一份 v1.5.1 測試檔放進 `ceeadb1` 的 worktree（`/tmp`）實跑 → `Ran 4 tests … FAILED (failures=4)`，失敗內容分別為 `0 != 1 : …/verdict.json`（F1）、`0 != 1 : …/fam-a-r1`（F1 leaderboard `--out-dir`）、`0 != 1`（F2 無出處 slice 被接受）、`… is not None`（F3 索引仍被建出、`kanban_task_id: None`）。
**re-seed 與 fail-closed**：`python3 runtime/survivor_index.py --json` → `written`、`bundle_count=1`、`survivor_count=2`（`sv-904822905a811669` BTCUSDT/1h、`sv-f762a1da8909a5bf` SOLUSDT/4h，id 與 v1.5.0 完全一致）、`index_sha256=sha256:f8ad34a1…`；`python3 runtime/survivor_leaderboard.py leaderboard --json` → `written`、2 列皆 `FROZEN_ONLY`、`top10_count=2`、forward metrics 全 `null`、`champion_candidate=false`；兩者 `--check` rc=0／`check_clean`。`/results/_survivors/forward/` **不存在**、0 slices。
**immutable 未動（前後逐位元相同）**：`survivor-bundle.json` `sha256:4638885f5f3787ee947d860a1fe48627de9802c2e9adb1d13f62a6917240eb53`、`verdict.json` `sha256:cb470adf56e9db19c3e6e6bc352177d634d7ecc800b4ceabaa41394c66ad0bf3`（與 v1.3.2 記錄一致）、`result.json` `sha256:012e6d1a6eed4206e5035796ddeac67d0a3a90f76cee866edb98ed89bb9543e8`、`artifacts/cohort_survivors.json` `sha256:74f250cf165511ba80875af5dc22560ac07292d4445ea8007ccf7f7bcda71081`、`round-spec.json` `sha256:e0b348bfdd0da706e55bf7fb1eacbcd47d34540934bebfe13aa9ab41d74c49e4`；bundle 公開 identity 仍為 `sha256:c051759f…`；`python3 runtime/survivor_bundle.py --attempt-dir <attempt> --check --json` → rc=0／`check_clean`／`identity_recipe_matches=true`（`runtime/survivor_bundle.py` 本版未改動，故 writer 身分欄位與 frozen bundle 的重跑比對完全不受影響）。**derived artifact 重建**：`_survivors/survivor-index.json`（`sha256:e724dda1…` → `sha256:68887ae3…`）與 `leaderboard.json`（`sha256:9769dba7…` → `sha256:517a61f5…`）因內含 `contract` 版本字串而重建；`leaderboard.csv` 逐位元不變（`sha256:c962bb5c97ff9dc26882ad23171df70ad10cbf380fd224c05d4595433976d9b8`，不含時間戳與版本）。
**未變動項**：`runtime/tests/test_survivor_bundle.py` 18/18、`test_strategy_b_v2_templates.py` 8/8、`test_strategy_a_v2_counts.py` 14/14、`test_reconcile.py` 23/23、`test_preflight_p10.py` 10/10、`test_production_handoff.py` 24/24（7 個測試檔合計 **127 檢定**全綠，同一 Python 3.9.6 host interpreter）；`hermes cron list --all` 讀回 job `624d0be5b23c` = **paused**（no-agent）；`qlib-run` 未重建；`/qlib-results` 無 strategy-b v2 目錄（未 launch）；Strategy B v1 卡 `t_3e696dce` 仍 `blocked`、其 `/results` artifacts 未動；本卡未建立任何 family／strategy 卡，亦未新增任何 service／daemon／queue／Registry／Orchestrator。文件狀態：**AUDITED FAIL**（v1.5.1；auditor t_346bcc04，2026-09-13；依卡片 t_171ba94f，為 auditor t_57357d4c 對 v1.5.0 的 F1/F2/F3 之最小 remediation）。**v1.5.2 更正**：該版已由 auditor t_346bcc04 判為 **AUDITED FAIL**，本段所述狀態已被下段取代。

**v1.5.2 更正**：v1.5.1 已由 auditor `t_346bcc04` 判為 **AUDITED FAIL**（三個 blocking trust-boundary 殘留：F1 `<results-root>/_survivors` 自身為 symlink 時可覆寫 frozen `verdict.json` 並在 frozen round 內建立 `forward/`；F2 relative `source_run.attempt_dir` 可被收下；F3 兩側同值的**數字** `kanban_task_id` 可被收下），已由 v1.5.2 最小 remediation 修正（§27.1／§27.3／§27.2 第 4 項）。

驗證方式（v1.5.2）：§26 change control 檢查（版本／日期／條號／理由／驗證方式）＋ §22 A1–A28 自檢 ＋ 全文 cross-reference 掃描（新增禁語三組：「reserved root 為 symlink 時仍寫入」、「把 relative `source_run.attempt_dir` 當成可驗出處」、「以非字串 `kanban_task_id` 建立 ownership」；並確認 §10.8 identity recipe 與重跑比對逐鍵豁免、family gate「>=1 → `PASS`」、all-survivors mapping、`ranking=null`、challenger rule、§27.4／§27.5 排序 tuple、B v2 preregistration 與未 launch、engine、cron paused、§9.4 reconciler、§14.4 handoff、§16 preflight、§1.2 非目標均未被改寫）＋ 實跑證據（唯一寫入 `/results` 的是 `_survivors/{survivor-index.json,leaderboard.json,leaderboard.csv}` 的重建；所有攻擊都在 `/tmp` worktree／複本上執行；未建立任何 family／strategy 卡；未執行任何 Qlib 計算）：
**F1（symlinked reserved root）**：在 `/tmp` 複本上把 `_survivors` 換成指向 frozen round 目錄的 symlink → `survivor_index.py --out <root>/_survivors/verdict.json`、`leaderboard --out-dir <root>/_survivors`、`forward` append 全部 **rc=1**（stderr `is a symlink` ＋ `outside the reserved post-survivor write boundary`），frozen round 內無新增檔案、`verdict.json`／`survivor-bundle.json` sha256 不變；把 `_survivors` 還原成真目錄後同一批指令回到 rc=0。v1.5.1 的 bytes 上同一測試 `ran 1 test … FAILED`，失敗訊息為 `result=written`（index 以 rc=0 寫進複本的 verdict 路徑）。
**F2（absolute attempt_dir）**：以結果樹為 cwd、`source_run.attempt_dir` 為 relative 的 slice 呼叫 `forward` → **rc=1**（`is not an absolute path`）、未寫任何 jsonl；同一份 slice 改成 absolute `attempt_dir` → rc=0／`appended`（對照控制）。v1.5.1 的 bytes 上同一 relative slice 於結果樹 cwd 執行 **rc=0／`appended`**（cwd 相依的 provenance 被收下）。
**F3（ownership 型別）**：`family.json` 與 bundle 的 `kanban_task_id` 同時設為同一個 JSON number （`12345`／`true`）、空字串、list、null → `survivor_index.py` 全部 **rc=1**／`source ownership is incomplete - kanban_task_id is not a non-empty string on both sides`；兩側皆為 `"t_1f97bf6b"` 時仍 rc=0（對照控制）。v1.5.1 的 bytes 上同一 number case **rc=0／索引照樣列出 2 survivors（`kanban_task_id: 12345`）**。
**測試**：`python3 runtime/tests/test_post_survivor.py` → **33/33 OK**（v1.5.1 的 30 個全數保留 ＋ 3 個新增）。**非恆真證明**：把同一份 v1.5.2 測試檔放進 `84f494c`（v1.5.1）的 worktree 實跑三個新增測試 → `Ran 3 tests … FAILED (failures=3)`。
**re-seed 與 fail-closed**：`python3 runtime/survivor_index.py --json` → `written`、`bundle_count=1`、`survivor_count=2`（`sv-904822905a811669` BTCUSDT/1h、`sv-f762a1da8909a5bf` SOLUSDT/4h，與 v1.5.0／v1.5.1 完全一致）、`index_sha256=sha256:23fd3970c50a2abd993651e38e12ca2e1d57ec88b07608f617b64f8d02f3260f`；`leaderboard --json` → `written`、2 列皆 `FROZEN_ONLY`、`top10_count=2`、forward metrics 全 `null`、`champion_candidate=false`；兩者 `--check` rc=0／`check_clean`；`/results/_survivors/forward/` **不存在**、0 slices。
**immutable 未動（前後逐位元相同）**：`survivor-bundle.json` `sha256:4638885f5f3787ee947d860a1fe48627de9802c2e9adb1d13f62a6917240eb53`、`verdict.json` `sha256:cb470adf56e9db19c3e6e6bc352177d634d7ecc800b4ceabaa41394c66ad0bf3`、`result.json` `sha256:012e6d1a6eed4206e5035796ddeac67d0a3a90f76cee866edb98ed89bb9543e8`、`artifacts/cohort_survivors.json` `sha256:74f250cf165511ba80875af5dc22560ac07292d4445ea8007ccf7f7bcda71081`、`round-spec.json` `sha256:e0b348bfdd0da706e55bf7fb1eacbcd47d34540934bebfe13aa9ab41d74c49e4`；bundle 公開 identity 仍為 `sha256:c051759fdf8291f66bce8f7249cb226b856c04de2ad78a8d055fdb4657543363`；`python3 runtime/survivor_bundle.py --attempt-dir <attempt> --check --json` → rc=0／`check_clean`／`identity_recipe_matches=true`（`runtime/survivor_bundle.py` 本版未改動）。**derived artifact 重建**：`_survivors/survivor-index.json` 與 `leaderboard.json` 因內含 `contract` 版本字串而重建（`leaderboard.csv` 不含時間戳與版本，逐位元不變）。
**未變動項**：`test_survivor_bundle.py` 18/18、`test_strategy_b_v2_templates.py` 8/8、`test_strategy_a_v2_counts.py` 14/14、`test_reconcile.py` 23/23、`test_preflight_p10.py` 10/10、`test_production_handoff.py` 24/24（7 個測試檔合計 **130 檢定**全綠，同一 Python 3.9.6 host interpreter）；`hermes cron list --all` 讀回 job `624d0be5b23c` = **paused**；`/qlib-results` 無 `strategy-b-v2` 目錄（未 launch）；Strategy B v1 卡 `t_3e696dce` 仍 `blocked`；本卡未建立任何 family／strategy 卡，亦未新增任何 service／daemon／queue／Registry／Orchestrator。文件狀態：**AUDITED PASS / FROZEN**（v1.5.2；audited content commit 0a361258，auditor t_3691bfb4，2026-09-13；卡片 t_d19618e1 為 auditor t_346bcc04 對 v1.5.1 的 F1/F2/F3 之最小 remediation；獨立 re-audit（t_3691bfb4，2026-09-13，verdict APPROVED）在 `/tmp` 隔離複本上重現 F1／F2／F3 全數在寫入前拒寫（F1 48 攻擊全 rc=1 且零殘留、F2 三種 cwd 皆拒且出處鏈 11/11 fail-closed、F3 五種非字串值全 rc=1）、130 檢定全綠、frozen 五 artifact 與 bundle identity 逐位元不變；另有 3 個非阻斷 minor 殘留（R1 reserved node 可被 `--out` 建成檔案、R2 `--out` 指向既有目錄時以 traceback 結束、R3 relocated copy 的 `--check` 依設計失敗），operator（卡片 t_cbf7344c）明確 **DEFER**，不開 v1.5.3）。**post-audit 觀察（finalization 期間，卡片 t_cbf7344c，2026-09-13）**：`runtime/tests/test_post_survivor.py::TestSurvivorIndex.test_index_cli_writes_checks_and_detects_drift` 為 intermittent（單一測試方法實測 5/60 失敗；rc 仍為 0、無 fail-closed 行為改變）：`survivor_index.write_index()` 以含 `generated_at_utc`（秒解析度）的**完整文字**比對決定 `unchanged`，因此第二次 CLI 跨秒時回報 `result=written`（兩份文件的 measured 內容完全相同，確定性重現時僅 `generated_at_utc` 一鍵不同）；`--check` 走 `measured()`（已排除時間戳）故不受影響，frozen artifacts 亦未動。此觀察**不在** auditor 的 R1–R3 之內、本 finalization 未對其作判定，已另立卡片 t_720406f2 交 default 追蹤（本版不改任何 runtime 檔、不開 v1.5.3）。

| v1.6.0 | 2026-09-13 | 新增 §28 Survivor Evidence Preservation（`[C]` 條文）：**survivor promotion → evidence
preservation**——正式出現在 `leaderboard.json` `entries` 的 **survivor** 才是觸發點（不是 Top-10、不是 PASS
gate），只有它升級為完整 execution evidence package（fills／episodes／equity 逐筆 ledger）；**大量 rejected／
candidate cell 明文不保留逐筆 execution**（103,680 次 research evaluation 與所有未 promoted cell 維持
`artifacts/grid_*.csv` 摘要）。落點唯一 `_survivors/evidence/<survivor_id>/`，寫入邊界比 §27 更窄（只
`_survivors/evidence/**`）且只能 staging→原子 rename 發佈（identity 相同 → `already_identical`，不同 →
refuse overwrite）。實作：①`container/scripts/20_strategy_a_run.py` 的既有 `simulate()` 加 **optional inert
trace hook**（`TRACE=None` ＋ `if TRACE is not None` 守衛，trace 關閉時 aggregate／計算順序／語意逐欄不變，以
trace off／on 相等強制）；②新增 `container/scripts/21_strategy_a_survivor_replay.py`（import 同一顆 engine，
只 replay `DONE`-sentinel-pinned 註冊 grid 的 winner cell，逐欄比對 frozen `artifacts/grid_*.csv`，不符即
fail-closed 不 materialize）；③新增 `runtime/survivor_evidence.py`（host 純 stdlib：`materialize`／`check`／
`coverage`；ledger 自身驗證＝Σepisode 恆等式＋episode partition＋equity 純 stdlib 重算 Sharpe／MaxDD）；
④`leaderboard` 增加**不參與排序**的 `evidence_package_status`／`evidence_manifest_path`／
`evidence_manifest_sha256`；⑤新增 §22 A29、§23 item 17、§25 六條 v1.6.0 硬規則、附錄 B T19。
**現況驗證**：以 durable index／leaderboard 讀回現有兩名 A v2 survivors（`sv-f762a1da8909a5bf` SOLUSDT/4h、
`sv-904822905a811669` BTCUSDT/1h），各 replay 9 個註冊 grid → **18/18 winner cell 逐欄相符**（frozen CSV 均
先與 terminal `DONE` sentinel `artifact_checksums` 比對）、trace off／on 一致；`coverage` = **2/2 PRESENT**；
materialize 重跑 → `already_identical`（refuse overwrite）；`survivor_evidence.py check` rc=0。**未變動**：
`forward/` 仍 0 slices、兩者仍 `FROZEN_ONLY`、`champion_candidate=false`、排名仍 SOLUSDT/4h #1、BTCUSDT/1h #2，
frozen `survivor-bundle.json`／`verdict.json`／`result.json`／`cohort_survivors.json`／`round-spec.json` sha256
逐位元不變；**未** launch B v2、**未**動 cron `624d0be5b23c`（仍 paused）、Strategy B v1 仍 blocked；
**未**新增 service／daemon／queue／Registry／UI／DB／第三方依賴。**明文 DEFER**：UI、DB、compression／retention、
Merkle／hash-chain、通用 family plugin／registry、forward ledger 合併、v1.5.2 `generated_at` flake、
`/scripts`↔repo disposition drift remediation。 | t_68954a45（ChatGPT GPT-5.6 Sol 卡片，依 Operator 決策與唯讀
研究卡 t_2382f20b 的最小方案）：把「survivor promotion → evidence preservation」寫成明確條文，並讓大量
rejected cells 只保留摘要，避免為未 promoted cell 建帳的過度工程；同時把 leaderboard 的 drill-back 指標做成
非排序欄位，使 evidence 的存否永不成為 gate | 見 §28.6／§22 A29／§23 item 17：`survivor_evidence.py check`
rc=0、`coverage` 2/2 PRESENT、18/18 winner cell 逐欄相符、`test_survivor_trace.py`（引擎層 trace off/on 與
ledger reconcile）與 `test_survivor_evidence.py`（host 層 boundary／tamper／non-gating／no-fake-claim）全綠，
負向控制（frozen CSV 被動／replay 欄位不符／package tamper／staging 越界／`_survivors` symlink／
non-leaderboard entry）皆 rc=1 |

| v1.7.0 | 2026-09-14 | **Production recovery automation（三件交付；不改科學語意、不改任何 frozen artifact）**：①**§14.4 candidate pool re-author**——B v1 entry **逐字保留**為歷史 provenance（consumed、永不重建、body 逐位元未改）；新增 `ema-crossover-walkforward-momentum-long-short-v2`（B v2 完整現行語意 body：eligible universe／data split／strategy domain（30 EMA 對 × 4 對角 walk-forward 格 = 120 cases/cohort）／**DCA PARAMETER DOMAIN**（四軸 4×2×3×2 = 48 組完整乘積、provenance class、long/short 鏡射 leg 語意）／**COHORT SURVIVOR SEMANTICS**（historical-only selector、同一 cell 帶到 OOS/full/stress/鄰域、0 survivor = REJECT、>=1 = PASS、全部 survivors 保留）／現行成本- funding- 滑價- robustness 語意／§10.8 survivor bundle＋§28 evidence preservation（leaderboard entry 為唯一觸發點、**非** PASS gate）／科學失敗 vs 基建-operator 失敗分離／durable family_id-fingerprint-provenance）；C／D／E body 重寫為現行語意（科學假說、provenance、eligible-universe 意義逐條保留；E 為 market-neutral，採 **leg-aware execution semantics**——每腿各自 ladder、leg 2 notional 由 OLS hedge ratio 決定、兩腿同時 reduce-only 平倉，**不**套用方向性單腿 DCA）；B v2／C／D／E 的 `fingerprint_input` 依 §14.3 重算（含 DCA 域、symbols、`selector=cohort-selector-v1;disposition=cohort-disposition-v1`）；pool ordering = B v1（consumed）→ B v2 → C → D → E，無重複 family_id／fingerprint。②**board cleanup**：`t_3e696dce`（B v1，operator-stopped、**無** PASS/REJECT）與 `t_720406f2`（`generated_at_utc` 冪等 flake，operator **DEFER**、不修、不記 done）封存為 `archived`（封存前各留 disposition comment），active blocked count = 0，兩者於 archived board 可追溯。③**新型 no-agent cron**：reconciler cron `f6b9aa5e9034`（`every 15m`、no-agent、deliver `discord:1519163199117721650`、**建立即 paused**；入口 `~/.hermes/scripts/quant_runtime_reconcile.py`＝純 `runpy` wrapper，repo `runtime/reconcile.py` 為唯一邏輯來源；wrapper 只做 no-op 靜默 + 新 incident signature 去重，`--dry-run` 不寫 state）——與 handoff cron `624d0be5b23c` **職責分離、不合併**（§9.4 v1.7.0 條）；**不得**代 handoff 建卡、auto-restart container、auto-publish orphan INCOMPLETE、建 recovery daemon 或 checkpointing。兩個 cron 交付時**皆 paused**。④**驗證**：fence-free `production_handoff.py --dry-run --json` → `would_append` B v2 at tail `t_1f97bf6b`（families_scanned=3、strategy_cards=3、無 blocked-card finding）；隔離 fixture 序列（真實 pool＋真實 body、temp results root＋注入式 board、**0 張真卡**）→ `B v2 → C → D → E → no_eligible_candidate`；8 檔測試 **144 檢定**全綠（`test_production_handoff.py` 24→**26**，新增 pool-ordering regression）；frozen A v2 artifacts／`_survivors` index／leaderboard／evidence packages 逐位元不變；未 launch B v2、未產生任何 C/D/E 真卡、未執行任何 Qlib 計算、未新增 service／daemon／queue／Registry／Orchestrator。 | ChatGPT（GPT-5.6 Sol）卡片 `t_15fed3f2`（operator 意圖：改善 Hermes 升級、gateway／session 中斷與一般控制面擾動後的快速恢復，且不 overengineer） |

驗證方式（v1.7.0）：§26 change control 檢查（版本／日期／條號／理由／驗證方式）＋ 全文 cross-reference 掃描（並確認 §7.2／§7.3／§9.4 驗證清單／§14.4 append 條件／§16 preflight／§27／§28 語意未被改寫；未新增 Manager／Service／Factory／Registry／Orchestrator、daemon、queue、第二套 engine）＋ 實跑證據（未建立任何 family／strategy 卡；未寫入 `/results` 既有 family／round／attempt 目錄；主機專屬路徑依 `evidence/README.md` redact，完整快照見 `evidence/v1.7.0-production-recovery-20260914.json`）：
**pool（§14.4）**：`python3 _handoff/verification/v1.7.0-pool-verify.py` → 全 true（`b_v1_entry_preserved_unchanged`、`b_v1_body_byte_identical`、`ordering`、`no_duplicate_family_id`、`no_duplicate_fingerprint`、`all_current_bodies_have_both_markers`、`b_v1_fingerprint_matches_recorded`（`sha256:46c22b91…`，與其 `/results` family.json 逐字相符）、`b_v2_fingerprint_input_equals_template`、`first_unconsumed_is_b_v2`）；前後 sha256：`candidates.json` `7ad6d0cf…` → `1105d826…`，B v1 body 不變（`558a01fb…`），B v2 body 新增（`704956aa…`），C/D/E body 重寫（`33d9a155…`→`2bd2404f…`、`304aa3fa…`→`c4200964…`、`7a9fbce0…`→`8a7c7f4a…`）；重算 fingerprint：B v2 `sha256:1a809ebc…`、C `sha256:ee24af42…`、D `sha256:b1b1dccb…`、E `sha256:33d1fa76…`（與既有 `/results` fingerprint 集合無交集、pool 內互異）。
**board cleanup**：`hermes kanban --board quant-strategy-research archive t_3e696dce t_720406f2`（fence-free host context，`HERMES_DELEGATED_CHILD_CONTEXT` 已 scrub）→ `Archived …` ×2、rc=0；DB 讀回 `t_3e696dce=archived`、`t_720406f2=archived`、`blocked_count=0`、`archived_count=70`；`kanban_show` 兩卡仍可讀（歷史與 comment 完整）。
**handoff dry-run（D）**：`env -u HERMES_DELEGATED_CHILD_CONTEXT … python3 runtime/production_handoff.py --dry-run --json` → `action=would_append`、`family_id=ema-crossover-walkforward-momentum-long-short-v2`、`tail_id=t_1f97bf6b`、`detail={families_scanned:3, strategy_cards:3}`、rc=0（無 `blocked_card_present`）；**未**寫 log、**未**建卡、**未**寫 `family.json`。
**隔離序列（D.3）**：`python3 _handoff/verification/v1.7.0-pool-sequence-verify.py` → `sequence_observed == expected == [B v2, C, D, E]`、`b_v1_never_reappears=true`、`create_calls=4`（全為注入式 fake board）、`real_cards_created=0`、第 5 輪 `no_eligible_candidate`。
**cron（C）**：`hermes cron list --all` 讀回 reconciler `f6b9aa5e9034`（`every 15m`、`repeat=forever`、`no-agent`、deliver `discord:1519163199117721650`、`enabled=false`、`state=paused`）與 handoff `624d0be5b23c`（仍 **paused**）；wrapper sha256 `66d913c6fe06666229e0608d63d262e14b03d56fe9b4ae7972e2f83f095a8d78`；wrapper `--dry-run` 自 foreign cwd（`/tmp`）實跑 → rc=0、stdout **0 bytes**、未寫 state（no-op 靜默）。
**測試**：`python3 runtime/tests/test_production_handoff.py` **26/26**（新增 `TestPoolOrdering` 兩檢定：序列 B v2→C→D→E、consumed B v1 永不重建）、`test_reconcile.py` 23/23、`test_preflight_p10.py` 10/10、`test_post_survivor.py` 33/33、`test_strategy_a_v2_counts.py` 14/14、`test_strategy_b_v2_templates.py` 8/8、`test_survivor_bundle.py` 18/18、`test_survivor_evidence.py` 12/12（8 檔合計 **144 檢定**全綠，同一 Python 3.9.6 host interpreter）。
**immutable 未動（前後逐位元相同）**：A v2 round r1 的 `survivor-bundle.json` `sha256:4638885f…`、`verdict.json` `sha256:cb470adf…`、`result.json` `sha256:012e6d1a…`、`artifacts/cohort_survivors.json` `sha256:74f250cf…`；`_survivors/survivor-index.json`、`leaderboard.json`、`leaderboard.csv`、`evidence/**` 皆未改寫；B v1 的 `/results/**` 未動。

| v1.7.1 | 2026-09-14 | **Reconciler authoritative current attempt（production-discovered control-plane bug 的最小修正）**：§9.4 新增 v1.7.1 `[C]`——reconcile 的掃描單位改為 **round**，同一 round 內**只有** authoritative current attempt（attempt `run-spec.json` identity 合法；ordering＝`created_at_utc` 主序 ＋ `uN` 序數**數值** tie-break，`u10` > `u9`）可驅動 Kanban 狀態轉換；較舊 attempt 的 terminal sentinel 一律 `superseded` descriptive no-op（可讀／可驗證／保留 provenance，但不得 unblock／complete／block，亦不產生 incident／comment）；同一 round 內較新 attempt 的 identity／ordering metadata 缺失、不可解析或歧義（含同一 timestamp 無 `uN` tie-break、同 round task ownership 衝突）→ 整 round **fail closed**（新 incident kind `attempt_selection_ambiguous`，**不得**回退較舊 terminal）；consumed 判定仍先於驗證；§9.4 既有九項驗證清單與 §12.6 三步語意不變。§12.6 kind 清單新增 `attempt_selection_ambiguous`、§22 新增 **A30**、§23 新增 **item 18**、§25 新增兩條 v1.7.1 硬規則。**未**新增 daemon／service／DB／current-pointer registry／新 state machine（selection 只是 discover 之後、handle 之前的小型 round grouping）；**未**改 Strategy B engine／策略語意／candidate pool／handoff logic。 | ChatGPT（GPT-5.6 Sol）卡片 `t_fd672413`（production 觀測：B v2 `…-r1-u1` FAILED 已被同 round `…-r1-u2` RUNNING_QLIB supersede，但 reconciler 仍以 u1 的 terminal 放行 `t_35b3e5da`，造成 duplicate Hermes wake；Qlib u2 本身仍健康） |
| v1.8.0 | 2026-09-14 | **§26.1 一次性 additive schema migration 例外（僅限該例外；不放寬 INV-4）＋ launch gate 的 schema 前置檢查**：①新增 §26.1 `[C]`——授權 family `ema-crossover-walkforward-momentum-long-short-v2` 已凍結 round `…-r1` 的 `round-spec.json` 僅新增**一個** top-level `parameter_contract`（必須**完全由該 round-spec 自身已註冊的** `parameter_domain`／`dca_domain` 生成、`family_id` 一致；其餘所有 key／value canonical 逐位元不變；u1／u2 attempt 與其他所有 frozen artifact 不動），並明定證據要求（before/after sha256＋field-preservation proof＋`validate_contract`／`validate_round_spec_contract` = 0 problems＋`strategy_b_v2_counts` fingerprint MATCH＋獨立 evidence 檔）與 auditor profile 的獨立唯讀 re-audit（default 不得自審；auditor PASS 前不得 launch u3）；②明文**不放寬一般 INV-4**、本例外不構成先例；③§16.2 **P10 追加**：launch gate 亦須在 compute 之前驗證目標 attempt 的 round-spec 具備合法 `parameter_contract`（pre-schema A v2 走 in-code bridge，行為不變）；④`runtime/templates/strategy_b_v2_round_spec.template.json` 內建 `parameter_contract`，`runtime/preflight.py` 與 `runtime/instantiate_strategy_b_v2.py`（含同 round retry 的 reuse 路徑與 read-back）於 publish／compute 前呼叫 `parameter_contract.validate_round_spec_contract`。**未**改 Strategy B engine／策略語意／domains／gates／selector／splits，**未**改本文件其他條文、**未**新增 service／daemon／registry／queue。 | ChatGPT（GPT-5.6 Sol）卡片 `t_67481d49`（§26 (b)）；blocker 證據 `evidence/strategy-b-v2-u3-launch-gate-blocker-20260914.json`（`bce61ca`）：pre-v1.8 已凍結的 r1 round-spec 缺 generic `parameter_contract`，v1.8 非 legacy family 的 post-survivor 消費鏈對它 fail closed——u3 不得帶著不合規的 round-spec launch |

驗證方式（v1.7.1）：§26 change control 檢查（版本／日期／條號／理由／驗證方式）＋ 全文 cross-reference 掃描（§9.4 九項驗證清單語意、§12.2 orphan、§12.6 三步、§14.4 handoff、§16 preflight、§27／§28 未被改寫；未新增 Manager／Service／Factory／Registry／Orchestrator、daemon、queue、第二套 engine）＋ 實跑證據（未建立任何 family／strategy 卡；未寫入 `/results` 任何 attempt artifact；主機專屬路徑依 `evidence/README.md` redact，完整快照見 `evidence/v1.7.1-reconciler-current-attempt-20260914.json`）：
**bug repro（read-only，修正前的 bytes）**：`python3 runtime/reconcile.py --dry-run --json` 對現行 `/results` → `would_unblock=['t_35b3e5da']`（B v2 `…-r1-u1` FAILED、同 round `…-r1-u2` RUNNING_QLIB）＝ production 觀測事件的無副作用重現。
**修正後同一 dry-run**：`scanned=9 dry_run=true incidents=0 unblocked=[] would_unblock=[]`；`…-r1-u1` = `superseded`（`authoritative_run_id=…-r1-u2`）、`…-r1-u2` = `orphan_candidate`（`stage=RUNNING_QLIB`）；A v2／A v1／B v1 歷史 round 全為 no-op（consumed／superseded），未對任何卡提出 unblock／complete／block。
**live 未動（前後逐位元比對 34 檔）**：除 u2 仍在寫入的 `artifacts/grid_fee_2x.csv`（333,076 → 734,210 bytes，live writer）外全部相同；`…-r1-u2/run-spec.json` `b20a8251…`、`state.json` `c8465714…`、`round-spec.json` `a3dd33a9…`、`family.json` `63ee6cb8…`、u1 全部檔案不變；未停／未重啟 u2、未新開 u3、未碰 A／B v1 frozen science artifacts。
**測試**：`python3 runtime/tests/test_reconcile.py` → **38/38 OK**（新增 15 項 round-level 檢定）；同一批新檢定對 **v1.7.0 的 `reconcile.py` bytes** 重跑 → **11 FAIL ＋ 3 ERROR**（3 個 ERROR 為舊 report 缺 `superseded` 欄的 `KeyError`），證明檢定確實偵測此 bug；8 檔合計 **159 檢定**全綠（`test_production_handoff.py` 26、`test_post_survivor.py` 33、`test_reconcile.py` 38、`test_preflight_p10.py` 10、`test_strategy_a_v2_counts.py` 14、`test_strategy_b_v2_templates.py` 8、`test_survivor_bundle.py` 18、`test_survivor_evidence.py` 12）。
**cron／board 讀回**：reconciler cron `f6b9aa5e9034` 仍 **paused**／`enabled=false`；handoff cron `624d0be5b23c` 與 v1.7.0 交付時狀態一致（未被本卡改動）；board 上無 Strategy C／D／E 真卡。
文件狀態：**AUDITED PASS / FROZEN**（v1.7.1；audited content commit `ed07605`（`ed076057eae09163cf608c61426c3229dce3e2ca` = v1.7.1 審計當時的 remote main = 當時 HEAD，audited bytes 未變），auditor `t_235ae131`，2026-09-14；獨立唯讀審計卡 `t_235ae131`（auditor lane、卡片 status=done、run 150 outcome=completed、metadata.verdict APPROVED／approved=true；default 未自審）：159/159 tests PASS（8 檔；`test_reconcile.py` 38/38）、RED check（同一批新檢定對 v1.7.0 `reconcile.py` bytes → 11 FAIL＋3 ERROR）、70/70 /tmp 負向控制、production dry-run 對照（v1.7.0 = `would_unblock=['t_35b3e5da']`；v1.7.1 = `[]`）、B v2 42 檔 snapshot 無硬變動（u2 仍 RUNNING_QLIB）、frozen SHA 14/14、cron `f6b9aa5e9034` 仍 paused／handoff `624d0be5b23c` 與交付時一致、board blocked=0；未 resume 任一 cron、未 launch B v2、未改動 repo 或 `/results`；F1／F2（MINOR 文件精度 findings）經 operator 明確 **DEFER**、不開 v1.7.2、不改 runtime code／audited semantics；審計報告 sha256 `e6db775c…`；依 ChatGPT（GPT-5.6 Sol）卡片 `t_fd672413`：reconciler authoritative current attempt 修正）。
| 前一版 文件狀態：**AUDITED PASS / FROZEN**（v1.7.0；audited content commit `d21ec33`（`d21ec336c7bd627908e6304866a7f209a8bea77f` = v1.7.0 審計當時的 remote main = 當時 HEAD，audited bytes 未變），auditor `t_3219d6a0`，2026-09-14；獨立唯讀審計卡 `t_3219d6a0`（auditor lane、卡片 status=done、run 145 outcome=completed、metadata.verdict APPROVED／approved=true；default 未自審）：144/144 tests PASS（8 檔）、static audit 42/42 PASS、隔離 pool verifier 全 true（B v1 consumed 逐位元未變、B v2 `fingerprint_input` 等於模板、ordering／無重複）、序列 fixture `B v2 → C → D → E`（0 張真卡）、board blocked=0（`t_3e696dce`／`t_720406f2` archived 可追溯、未記 done、未修）、cron `f6b9aa5e9034`／`624d0be5b23c` 皆仍 paused、foreign-cwd reconciler dry-run rc=0／stdout 0 bytes、handoff dry-run `would_append` B v2 at `t_1f97bf6b`、frozen A v2／`_survivors`／B v1 指定 SHA 全數相符；未 resume 任何 cron、未 launch B v2、未改動 repo 或 `/results`；審計報告 `v1.7.0-independent-audit-report.md` sha256 `035d1b0e…`；依 ChatGPT（GPT-5.6 Sol）卡片 `t_15fed3f2`）。

驗證方式（v1.8.0）：§26 change control 檢查（版本／日期／條號／理由／驗證方式）＋ 全文 cross-reference 掃描（§0–§5 凍結前綴未動；§16 只追加 P10 一句；§22／§23／§25／§27／§28 未被改寫；未新增 Manager／Service／Factory／Registry／Orchestrator、daemon、queue、第二套 engine）＋ 實跑證據：
**repo（v1.8.0 變更；先 commit／push main 才動 `/results`）**：`python3 -m unittest discover -s runtime/tests -t runtime/tests` → **250 檢定全綠**（v1.8.0 前 238；新增 12）：`test_preflight_p10.py` 10→16（launch gate 對「round-spec 缺 `parameter_contract`」／「contract 不合法」／「schema domain 不符」／「缺 round-spec」全 FAIL；合法 contract 與 pre-schema A v2 bridge 全 PASS）、`test_strategy_b_v2_templates.py` 8→10（template 內建 contract 且與註冊 axes 逐值一致；移除 contract 的 pre-v1.8 形狀 fail closed）、新增 `test_b_v2_instantiation_contract.py` 4（fresh 與 reuse 路徑放行合法 contract；pre-v1.8 形狀與無 contract 的 template 一律 refuse 且不 publish run-spec）。
**results migration（唯一一次 additive migration）**：`…-r1/round-spec.json` `sha256:a3dd33a9…` → `sha256:2c6d6f64…`（完整值與欄位級證明見 `evidence/strategy-b-v2-r1-round-spec-schema-migration-20260914.json` 的 `before_sha256`／`after_sha256`）；byte 級 splice proof（原文除末尾 `}` 外逐位元保留、`original_bytes_rewritten=0`、寫入後 read-back sha 相符）、canonical field-preservation proof（除新增之 `parameter_contract` 外所有 top-level／nested key／value 相同）、`parameter_contract.validate_contract` 與 `validate_round_spec_contract` = 0 problems、`strategy_b_v2_counts.py` fingerprint MATCH、u1／u2 的 `run-spec.json`／`FAILED`／`state.json` sha256 前後不變、`family.json` 不變；產生器 `evidence/strategy-b-v2-r1-round-spec-schema-migration-20260914/migrate_r1_round_spec.py`（`--dry-run` 可重跑、不寫檔）。
**未動**：Strategy B engine／策略語意／domains／gates／selector／splits、Strategy A 全部 artifacts、Strategy C、cron（reconciler `f6b9aa5e9034` 與 handoff `624d0be5b23c` 皆未 resume）、u3（未建立、未 launch）、`t_35b3e5da`（未 unblock）。
文件狀態：**AWAITING AUDIT**（v1.8.0；依 ChatGPT（GPT-5.6 Sol）卡片 `t_67481d49`；本版變更的獨立唯讀 re-audit 卡於交付後建立（auditor profile）；**auditor PASS 前不得 launch u3、不得 unblock `t_35b3e5da`**）。前一版 文件狀態：**AUDITED PASS / FROZEN**（v1.7.1；audited content commit `ed07605`，auditor `t_235ae131`，2026-09-14）。

| v1.9.0 | 2026-09-15 | **§9.4 compute-finished wake（full-auto completion 的 missing link；最小修補）＋ SOP 對齊**：新增 `[C]`——authoritative current attempt **無** terminal sentinel、其 `run-spec.json` 的 `task_id`／`kanban_board` 為 non-empty string、DB 讀回卡片 status == `scheduled`，且 `state.json` 可解析、`stage` ∈ {`ARTIFACT_READY`,`FAILED_SCRIPT`} 時，reconciler **只**執行既有 `scheduled → ready`（父卡未完成則 `todo`）的 `unblock`＝喚醒 default 做 host-side 處置；`ARTIFACT_READY` **不得**自動等同 `DONE`（`[V]` Strategy D r1 u2 反例），該路徑不得寫 terminal sentinel／`verdict.json`／任何 `/results` artifact、不得判 verdict、不得建 incident（除既有 ownership／read-back 本身不成立）、不得啟動新 run、不得建卡或代 handoff；`RUNNING_*`／無 `state.json`／`stage` 不可解析／`superseded` attempt／非 `scheduled` 卡片／identity 取不到／card read-back 失敗一律維持描述性 `orphan_candidate`（fail-closed、行為不變）；authoritative current attempt selection 與既有九項驗證清單不變；`--dry-run` 只回 `would_unblock`。§9.2 新增「compute-finished ≠ 終結」（true terminal sentinel 仍由 default host-side 發佈）；§12.2 第 1 項新增交叉指針；§14.4 新增 v1.9.0 cron 現況（handoff `624d0be5b23c` **active**、排程已由 ChatGPT 改為 `5,35 * * * *`；reconciler `f6b9aa5e9034` 仍 paused，俟本版 audit PASS 後由 ChatGPT/operator resume）＋ append 觸發語意白話摘要 ＋ **production loop 全貌明文化**；§9.4／§14.4 兩處過期的「兩個 job 都 paused」**現況**文字以 v1.9.0 更正標註（歷史交付事實逐字保留）；標題狀態／版本／作者與作者補註同步；§22 新增 **A31**、§23 新增 **item 19**、§25 新增兩條 v1.9.0 硬規則。**runtime**：`runtime/reconcile.py` 新增 `COMPUTE_FINISHED_STAGES` 與 `wake_default()`，`handle()` 的無 sentinel 分支在 compute-finished 時走該 wake（其他情況維持原 `orphan_candidate` 與既有 fail-closed 分支）；`runtime/tests/test_reconcile.py` 38 → **46 檢定**（新增 8 項；其中 5 項對 v1.8.0 的 `reconcile.py` bytes 為紅：2 FAIL ＋ 3 ERROR）。**未**新增 service／daemon／cron／job／manager、**未**改 `runtime/production_handoff.py`、**未**碰任何 strategy engine／parameter／DCA／backtest artifact、**未**操作任何 cron、**未**寫入 `/results` 任何檔案（唯一 `/results` 動作為 read-only dry-run）。 | ChatGPT（GPT-5.6 Sol）卡片 `t_2b8c076c`（operator 明確要求切記過度工程）：Strategy D（`t_50c28da5`）證實 container runner 先落 `ARTIFACT_READY` 再退出、sentinel 由 default host-side 發佈，而卡片 park `scheduled` 時 reconciler 對無 sentinel 的 attempt 只回報 `orphan_candidate`——full-auto completion 因此缺一環；只做既有 completion bridge 的最小修補與 SOP 對齊 |

| v1.10.0 | 2026-09-21 | **跨 repo 現行語意對齊 + §29 Validated Survivor Research Mirror**：依 operator 明確指示，現行 runtime/backtester 固定為 Qlib-only；Lean／Nautilus／PyBroker 改列 retired/non-participating（只保留歷史 provenance，不是 current/future gate）；新增 §29，將已落地 guarded compact mirror 的 truth boundary、formal-entry scope、compact layout、canonical-root/`--check` isolation、immutable conflict、Git non-blocking/idempotency 與 no-new-machinery 固定為 contract；README 與 alpha/validated sibling repos 同步。runtime implementation 仍為已驗證 commit `25d3e438…`，本版不改 runtime code、`/results`、candidate pool、cron 或 frozen artifacts。 | Operator 2026-09-21 要求三個量化 GitHub repo 分工/文件使用一致語意；ChatGPT（GPT-5.6 Sol）跨 repo 盤點後之最小 doc-only 對齊。 |

驗證方式（v1.10.0，已完成）：三 repo current-doc cross-reference；§29 逐條對照 `runtime/survivor_private_export.py`／`survivor_leaderboard.py` guard 與 tests；read-only canonical/private seed readback（24/24 IDs、leaderboard byte-identical、2 compact evidence pairs、second export unchanged）；`git diff --check`；只允許 README/contract 文件變更。獨立 auditor `t_9073d65f` 對 semantic content commit `266390f1bc8179c503731cb1af2fda6195f7af42` 判定 **PASS / 0 blocking findings**；本行為 audit 後的 attestation-only 狀態更新，不改任何 operational semantics。

驗證方式（v1.9.0）：§26 change control 檢查（版本／日期／條號／理由／驗證方式）＋ §22 A1–A31 自檢 ＋ 全文 cross-reference 掃描（§0–§5 凍結前綴未動；§7／§10／§11／§12（除 12.2 第 1 項的一句話交叉指針）／§13／§15／§16／§27／§28 未被改寫；新增禁語兩組：「把 `ARTIFACT_READY`／`FAILED_SCRIPT` 當 terminal 或等同 `DONE`」、「放寬 compute-finished wake 的 fail-closed 條件或由 worker 自行啟停 cron」；未新增 Manager／Service／Factory／Registry／Orchestrator、daemon、queue、第二套 engine、第二套 runtime）＋ 實跑證據（**未**新增或修改任何 `/results` 檔案、**未**執行 Qlib 計算、**未**啟停任何 cron；主機專屬路徑依 `evidence/README.md` redact）：
**測試（repo `main`，同一 host interpreter）**：`python3 runtime/tests/test_reconcile.py` → **46/46 OK**（原 38 ＋ 8）；`runtime` 全部 16 個測試檔 → **320/320 OK**（`test_parameter_contract.py` 53、`test_reconcile.py` 46、`test_post_survivor.py` 41、`test_production_handoff.py` 26、`test_strategy_d_v1_templates.py` 19、`test_candidate_snapshot.py` 18、`test_survivor_bundle.py` 18、`test_preflight_p10.py` 16、`test_strategy_c_v1_templates.py` 15、`test_strategy_a_v2_counts.py` 14、`test_survivor_evidence.py` 14、`test_preflight_recover.py` 11、`test_strategy_b_v2_templates.py` 10、`test_b_v2_r1_migration_scope.py` 8、`test_b_v2_instantiation_contract.py` 6、`test_reconcile_wrapper.py` 5）。
**RED（非恆真）**：對 v1.8.0 的 `reconcile.py` bytes（`git show 2c22fe0:runtime/reconcile.py`，sha256 `1e15d32114398ecef50b0d2092006b1d17881940d4f7adf0e866162e0cb7b667`）在 `/tmp` 複本上重跑同一批新檢定 → **failures=2、errors=3**（`ARTIFACT_READY`／`FAILED_SCRIPT` 不會 wake；三個新 `detail.wake` 斷言為 `KeyError`）；另 3 項為「行為不變」守衛，依設計兩版皆綠。修正後本樹 `runtime/reconcile.py` sha256 `640f17466283baa1f470665d4fe15a89af535ded9588dd161d9188f6b74eefcf`、`runtime/tests/test_reconcile.py` sha256 `4e014638b5dd429479a3275677a32ad7fe1722f07bf14bced08d814b0131b82b`。
**production dry-run 對照（唯讀）**：`python3 runtime/reconcile.py --dry-run --json` 對現行 `/results` → `attempts_scanned=16`、`incidents=0`、`unblocked=[]`、`would_unblock=[]`，與 v1.8.0 bytes 的同一 dry-run **逐列相同**（現行樹上沒有該被喚醒的 attempt：每個 round 的 authoritative attempt 不是已有 terminal 就是 `RUNNING_QLIB`；Strategy D `…-r1-u2` 已因同 round u3 而 `superseded`）；`/results/_incidents/reconciliation_incident.jsonl` 無新行。
**live-shape 對照（真實 attempt bytes ＋ 注入式 board 讀回，避免任何 board 變更）**：Strategy D `…-r1-u2`（實檔 `state.json` stage=`ARTIFACT_READY`、`run-spec.json` `task_id=t_50c28da5`、attempt 目錄無 terminal）→ 注入 status=`scheduled`：`would_unblock=['t_50c28da5']`、0 次 `sh()`、0 incident、0 board 變更；注入 status=`done`：`orphan_candidate`（`detail.wake` 明示 no wake）。
**邊界讀回**：`hermes cron list --all` → handoff `624d0be5b23c` = **active**／`5,35 * * * *`（last run 2026-09-15T16:05 ok）、reconciler `f6b9aa5e9034` = **paused**；本卡未執行任何 cron 動作；未新增任何 evidence 目錄／工具／腳本（診斷與 RED 腳本一律在 `/tmp`）。
文件狀態：**AUDITED PASS / FROZEN**（v1.9.0；audited content commit `511b6e3`（= v1.9.0 審計當時的 remote main = 當時 HEAD），auditor `t_bde913b0`，2026-09-15；獨立唯讀審計卡 status=done、run 227 outcome=completed，PASS、無 scope expansion：`test_reconcile.py` 46/46、runtime 16 檔 320/320、不寫 terminal／`verdict.json`／`/results`、fail-closed 完整、`runtime/production_handoff.py` 未變、Contract v1.9.0 對齊；audit PASS 後 reconciler cron `f6b9aa5e9034` 已 resume（2026-09-16 `hermes cron list --all` 讀回 handoff `624d0be5b23c`／reconciler `f6b9aa5e9034`／watchdog `c5314d86cdfe` 皆 active）；v1.9.0 core audited at `511b6e3`，其後 `bdd2bae`（§14.4 純文字對齊，卡片 `t_86d04b09`）與 `b48e449`（**已稽核的 lifecycle implementation／alignment**：`runtime/production_handoff.py` ＋ `runtime/tests/test_production_handoff.py` ＋ §6.4；卡片 `t_2acfd339`，auditor `t_514e4827` PASS；不 bump semantic version、無新 gate／stage）；依 ChatGPT（GPT-5.6 Sol）卡片 `t_2b8c076c`）。前一版 文件狀態：**AWAITING AUDIT**（v1.8.0；§26.1 一次性 additive schema migration 例外；該版交付後的稽核序列（auditor `t_2cb2910d` FAIL（F1/F2/F3）→ remediation `6b1cf77` → re-audit 卡 `t_92ef911d`）記於 board，本版不重新判定其結論；其「auditor PASS 前不得 launch u3、不得 unblock `t_35b3e5da`」的限制已由後續 production 流程處置，非本版範圍）。
