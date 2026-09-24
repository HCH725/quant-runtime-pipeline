# quant-runtime-pipeline

## Pending local change (independent audit and cutover required)

The worktree's C3/C4 runtime now proposes direct Hermes default sessions instead of Kanban as its
production work-order transport. Existing n8n schedules and the fixed host bridge remain unchanged.
C3 registers an immutable direct family and launches `hermes -p default --cli --accept-hooks chat
--query-file <frozen task> --in <candidate workspace>` detached; a family-scoped live lease and
durable results evidence retry a failed launch on the same family, not the next candidate. The agent
adapts the reviewed strategy, checks preflight, and starts detached Qlib compute. C4 reads the latest
attempt's stage/terminal evidence and, when ready for host disposition, starts another detached
default session. It does not call or read the production Kanban board. Historical task IDs and the
read-only dashboard's card projection are retained. This branch is not deployed or audited; the
historical live status below describes the pre-cutover installation. Direct PASS post-survivor
index ownership (direct families are indexed by family/round/run identity with no card ids;
historical card-owned families keep the strict checks) and the installed C4 wrapper's `launched`
notification are both addressed on this branch and pending independent audit.

**Phase 2（卡片 `t_35951c0c`；待獨立審計，未 deploy）**：n8n Full Canvas 的 runtime 真值（Current／runtime counts）
改由**既有 host bridge 的一個固定唯讀 action** `runtime_observe_once` → `runtime/runtime_observation.py`
（on-demand 讀 canonical `/Volumes/ExpansionDrive/qlib-results`）提供，取代原本 15 分鐘取樣、5 分鐘產生的
`dashboard.json` 投影鏈；無新掛載、無 snapshot 檔、無 daemon、無新 DB／queue，且 `current` 完全不讀 Kanban。
細節與實跑證據見 `N8N_CONTROL_PLANE.md` §9.G。

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

## 2. Current status（2026-09-23）

- **Full Canvas Completion（AUDITED PASS / LIVE）**：stable workflow `shadowQuantCp1` 已由原本 source-only shadow 展開成同一張 end-to-end canvas；SOURCE / METRICS REFRESH lane 保留既有唯讀來源鏈，CURRENT LIFECYCLE lane 使用 deterministic derived view、Current Stage Router、Pipeline Counts Summary 與 17 個 lifecycle indicators。implementation commits `46093ba`、`ca13752` 與 remediation `b29612f` 已由同一卡 `t_fd62293f` 的獨立 auditor re-audit PASS，且已 live import／publish。真實 execution 已驗證 snapshot writer、exact-one current-stage routing 與 `Attention / Unresolved` fail-closed；Historical/OOS、Robustness、WAITING_DATA/READY_TO_RESUME 等不會在沒有 authoritative token 時被猜測。production workflow `productionHandoffManualC2`、bridge、Qlib、Hermes/Kanban 與 cron ownership **完全未改**。
- **Phase 2C3 production handoff cadence（AUDITED PASS / LIVE）**：workflow `productionHandoffManualC2` 保持 stable ID，保留 Manual Trigger，Schedule Trigger live exact cron 為 `5,20,35,50 * * * *`（`:05/:20/:35/:50`）；兩者都只接同一個已稽核的 host-bridge request/response action。C3.1 commits `9832b07`＋`fc44eb0` 已由 auditor run 445 PASS 並完成 live deployment；2026-09-23 23:05 Asia/Taipei execution 112 為 `mode=trigger`／`status=success`，下一分鐘 C4 execution 113 亦成功。Hermes handoff cron `624d0be5b23c` 保持 **paused**，只作 rollback path。
- C3 **不取代** Hermes default、Kanban、Qlib 或 `runtime/production_handoff.py`：n8n 只負責 cadence 與既有 `ai.quant.n8n-host-bridge` 的最小編排，canonical handoff 判定與 fail-closed 語意仍在 repo runtime。
- **（2026-09-24，卡片 `t_6c6a3286`；待獨立審計）** `production_handoff` 的 canonical 判定只讀 `/results` artifacts，**不讀、也不要求 Hermes／Kanban**：Hermes／Kanban read-back 不再是前置條件，卡片狀態（blocked／stale／讀不到）零 gate 效力；真正的 runtime guard 是 90 分鐘 active attempt 窗（最新 attempt 在窗內且尚未發佈 terminal sentinel，**或**該 attempt **自己所屬 round** 尚無 terminal verdict——`verdict.json` 是 per-round，較早輪次的 verdict 不得釋放仍在寫的 follow-up round）、90 分鐘 launch grace 與 canonical incident fail-closed，每輪在 stderr 輸出 `outcome=advanced|running|idle|finding|incident`。不新增 `PAUSED` state、health daemon、retry queue、watcher 或 preflight node；等待中的輪次由下一個 `:05`／`:20`／`:35`／`:50` cadence 自然重試。（v1.2.0 語意為「Hermes／Kanban read-back unavailable → finding／operation HOLD」，已由上述 Kanban-free 決策取代。）
- 維護 SOP 沿用既有元件：先做 HOLD transition，乾淨停止 n8n、checkpoint／integrity check 並保留 known-good DB snapshot；完成 reboot/update 與 login 後，由既有 `ai.quant.recover-gate` 及 `ai.quant.n8n-host-bridge` LaunchAgent 復原，再做 readiness、Shadow、dry-run smoke，確認後繼續。**不新增 recovery service**。
- **Phase 2C4 runtime reconciler cadence（AUDITED PASS / LIVE）**：stable workflow `runtimeReconcilerC4` 以 Manual Trigger／Schedule Trigger／單一 fixed host-bridge action 維持目前 live cadence `6,21,36,51 * * * *`（約 15 分鐘，避開 C3 `:05`／`:20`／`:35`／`:50`）；bridge 僅使用固定 `runtime_reconcile_once` allowlist mapping 到既有 `~/.hermes/scripts/quant_runtime_reconcile.py`，不新增 mailbox、queue、service、daemon、state machine 或 runtime semantics。C4 automatic trigger 已於 2026-09-23 22:06 Asia/Taipei 成功觀測（execution 103、mode `trigger`、status `success`），前一拍為 C3 execution 102（22:05）。
- C4 live cutover 已由 operator 完成：Hermes reconciler cron `f6b9aa5e9034` 保持 **paused**，只作 rollback path；C4 automatic trigger 已於 2026-09-23 22:06 Asia/Taipei 成功讀回（execution 103、mode `trigger`、status `success`），緊接 C3 execution 102（22:05）。失敗 rollback：停用 C4，再恢復 Hermes cron；watchdog `c5314d86cdfe` 保持 **active** 且獨立，Full Canvas `shadowQuantCp1` 維持 read-only，production control 仍在獨立 production workflows。

- `QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md`：**v1.11.0，AUDITED PASS / LIVE**（2026-09-23）；C3 cutover ownership、fail-closed HOLD 與 maintenance SOP 已落地，implementation commit `68b338c` 已完成獨立 auditor PASS，live scheduled cutover 也已驗證。
- `QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md`：**v1.10.0，AUDITED PASS / FROZEN**（2026-09-21；operator-directed cross-repo semantic alignment）：現行 production/runtime 為 **Qlib-only**；Lean／Nautilus／PyBroker 皆為 retired／non-participating historical systems，不是 current/future production gate 或 performance truth。新增 §29 **Validated Survivor Research Mirror**，把已落地的 guarded exporter 正式納入 SOP：final runtime implementation `25d3e438093a7fbc1bf31cb7ecdd386796ac8d9e`；private controlled seed `15eb017cfcb1da59f8c27977f6c5a6f63a7f072a`；canonical/private leaderboard byte-for-byte identical、24/24 survivor IDs 相同、24 baselines、2 組 compact §28 evidence、second export=`unchanged`。本次只同步文件語意，**未**修改 runtime code、canonical `/results`、candidate pool、cron 或任何 backtest artifact；v1.10 semantic content commit `266390f1bc8179c503731cb1af2fda6195f7af42` 已由 auditor `t_9073d65f` 於 2026-09-21 完成獨立唯讀 cross-repo audit：**PASS / 0 blocking findings**（exporter/post-survivor/survivor-evidence 隔離回歸 **10/10、44/44、14/14** 全綠）。
- 前一版 `QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md`：**v1.9.0，AUDITED PASS / FROZEN**（auditor `t_bde913b0`，2026-09-15；audited content commit `511b6e3`（= v1.9.0 審計當時的 remote main = 當時 HEAD）；依 ChatGPT（GPT-5.6 Sol）卡片 `t_2b8c076c`；獨立唯讀審計卡 status=done、run 227 outcome=completed）：**§9.4 compute-finished wake（full-auto completion 的 missing link；最小修補）**——authoritative current attempt **無** terminal sentinel、`run-spec.json` 的 `task_id`／`kanban_board` 可讀、DB 讀回卡片仍 `scheduled`，且 `state.json` stage ∈ {`ARTIFACT_READY`,`FAILED_SCRIPT`} 時，reconciler **只**執行**既有** `unblock`（`scheduled → ready`／父卡未完成則 `todo`）＝喚醒 default 做 host-side 處置；`ARTIFACT_READY` **不得**自動等同 `DONE`（`[V]` Strategy D r1 u2 反例），該路徑不寫 terminal sentinel／`verdict.json`／任何 `/results` artifact、不判 verdict、不建 incident、不啟動新 run；`RUNNING_*`／無 `state.json`／stage 不可解析／`superseded` attempt／非 `scheduled` 卡片／identity 取不到／card read-back 失敗一律維持描述性 `orphan_candidate`（fail-closed）。**審計實證（auditor `t_bde913b0`，2026-09-15，PASS、無 scope expansion）**：`test_reconcile.py` **46/46**、runtime 全部 16 個測試檔 **320/320**；`runtime/production_handoff.py` 未變。**v1.9.0 core audited at `511b6e3`；其後的變更**：`bdd2bae`（§14.4 對齊 Research Intake Review sibling-output candidate producer——**純文字對齊**，卡片 `t_86d04b09`），其後 `b48e449`（**已稽核的 lifecycle implementation／alignment**，共三檔：`runtime/production_handoff.py` 於 append 時在 candidate body **之後**注入固定 lifecycle footer（candidate bytes 逐位元不改、`fingerprint_input` 不變）、`runtime/tests/test_production_handoff.py` 新增 regression、契約 §6.4 prerequisite-missing 終結語意——必要 prerequisite 客觀不存在且已由 measured evidence 證實時，`TECHNICAL_INCOMPLETE` 即誠實且合法的終結（**不是** scientific `REJECT`），terminal evidence 齊備即 `done`、不得僅因 full-backtest outputs 不可能存在而形成 human gate；**不 bump semantic version、無新 gate／stage**；卡片 `t_2acfd339`，auditor `t_514e4827` PASS，27/27 target＋351/351 runtime）。**現況（2026-09-16 `hermes cron list --all` 讀回）**：production loop **active**——handoff `624d0be5b23c`（`5,35 * * * *`、no-agent、last run 2026-09-16T23:05 ok）／reconciler `f6b9aa5e9034`（`every 15m`、no-agent；v1.9.0 audit PASS 後已 resume、last run 2026-09-16T23:13 ok）／watchdog `c5314d86cdfe`（`every 15m`、no-agent、alert-only、last run 2026-09-16T23:21 ok）皆 active。
- 前一版 `QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md`：**v1.8.0，交付時 AWAITING AUDIT**（依 ChatGPT（GPT-5.6 Sol）規劃卡 `t_67481d49`，2026-09-14，§26 change control (b)）：**§26.1 一次性 additive schema migration 例外**——只授權 family `ema-crossover-walkforward-momentum-long-short-v2` 已凍結 round `…-r1` 的 `round-spec.json` **新增一個** top-level `parameter_contract`（必須**完全由該 round-spec 自身已註冊的** `parameter_domain`／`dca_domain` 生成、`family_id` 一致；其餘所有 top-level／nested key／value canonical 逐位元不變；u1／u2 attempt 與其他所有 frozen artifact 不動），**明文不放寬一般 INV-4**、不構成先例；同一變更補上未來 instantiation 的最小 wiring：`runtime/templates/strategy_b_v2_round_spec.template.json` 內建 `parameter_contract`、§16.2 **P10** 的 launch gate 與 `runtime/instantiate_strategy_b_v2.py`（含同 round retry 的 reuse 路徑與 read-back）在 compute／publish **之前**呼叫 `parameter_contract.validate_round_spec_contract`（pre-schema A v2 走 in-code bridge，行為不變）。**驗證**：`python3 -m unittest discover -s runtime/tests -t runtime/tests` → **250/250 全綠**（238 → 250；`test_preflight_p10.py` 10→16、`test_strategy_b_v2_templates.py` 8→10、新增 `test_b_v2_instantiation_contract.py` 4）；results migration `…-r1/round-spec.json` `sha256:a3dd33a9…` → `sha256:2c6d6f64…`（byte 級 splice proof：原文除末尾 `}` 外逐位元保留、`original_bytes_rewritten=0`；canonical field-preservation proof：除新增 key 外所有 key／value 相同；`validate_contract`／`validate_round_spec_contract` = 0 problems；counts fingerprint MATCH；u1／u2 `run-spec.json`／`FAILED`／`state.json` 與 `family.json` 前後不變），證據快照 `evidence/strategy-b-v2-r1-round-spec-schema-migration-20260914.json`（產生器 `…-schema-migration-20260914/migrate_r1_round_spec.py`，`--dry-run` 可重跑）。**未** launch u3、**未** unblock `t_35b3e5da`、**未** resume 任一 cron、**未**動 Strategy A／B v1／C 任何 artifact、**未**新增 service／daemon／registry／第二套 engine。**本版變更尚未經獨立 auditor 稽核**（re-audit 卡於交付後建立；auditor PASS 前不得 launch u3、不得 unblock `t_35b3e5da`）。**（v1.8.0 交付後的稽核序列，記於 board：auditor `t_2cb2910d` FAIL（F1／F2／F3）→ remediation `6b1cf77` → re-audit 卡 `t_92ef911d`；其「auditor PASS 前不得 launch u3、不得 unblock `t_35b3e5da`」的限制已由後續 production 流程處置。）**
- 前一版 `QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md`：**v1.7.1，AUDITED PASS / FROZEN**（auditor `t_235ae131`，2026-09-14；audited content commit `ed07605`（= remote main）；依 ChatGPT（GPT-5.6 Sol）卡片 `t_fd672413`）：**reconciler authoritative current attempt**——修 production 實測到的 control-plane bug（B v2 `r1-u1` FAILED 已在同 round `r1-u2` RUNNING_QLIB 之下被 supersede，reconciler 仍以 u1 的 terminal 放行 `t_35b3e5da` → duplicate Hermes wake）。修正：reconcile 掃描單位改為 round，同 round 內**只有** authoritative current attempt（`run-spec.json` identity 合法；ordering＝`created_at_utc` 主序 ＋ `uN` 序數**數值** tie-break、`u10` > `u9`）可驅動 Kanban 轉換；較舊 attempt 一律 `superseded` descriptive no-op（不得 unblock／complete／block，亦不產生 incident／comment）；較新 attempt 的 identity／ordering metadata 缺失、不可解析或歧義（含 timestamp tie 無 tie-break、同 round ownership 衝突）→ 整 round fail closed（新 incident kind `attempt_selection_ambiguous`，不得回退較舊 terminal）；consumed 判定仍先於驗證。**未**新增 daemon／service／DB／current-pointer registry／state machine，**未**改 Strategy B engine／策略語意／candidate pool／handoff logic，**未** resume 任一 cron（reconciler `f6b9aa5e9034` 仍 paused）、**未**碰正在跑的 B v2 u2（run-spec／state／grid sha 前後不變）、**未**產生 Strategy C。**驗證**：修正前 dry-run `would_unblock=['t_35b3e5da']`（bug 重現）→ 修正後 `would_unblock=[]／incidents=0`、`r1-u1=superseded`、`r1-u2=orphan_candidate`；`test_reconcile.py` **38/38**（新增 15 項 round-level 檢定；同一批檢定對 v1.7.0 bytes → 11 FAIL ＋ 3 ERROR）、8 檔合計 **159 檢定**全綠。證據快照 `evidence/v1.7.1-reconciler-current-attempt-20260914.json`。
- 前一版 `QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md`：**v1.7.0，AUDITED PASS / FROZEN**（auditor `t_3219d6a0`，2026-09-14；audited content commit `d21ec33`（v1.7.0 審計當時的 remote main）；卡片 `t_15fed3f2`，ChatGPT（GPT-5.6 Sol）；**前一版 v1.6.0，AUDITED PASS / FROZEN**）：**production recovery automation** —— ①§14.4 candidate pool re-author（B v1 歷史 entry 逐字保留為 provenance、新增 `ema-crossover-walkforward-momentum-long-short-v2` 完整現行語意 body、C/D/E body 重寫為現行語意並依 §14.3 重算 `fingerprint_input`），②board cleanup（`t_3e696dce` operator-stopped 卡與 `t_720406f2` DEFER flake 卡封存 `archived`、active blocked = 0、兩者於 archived board 可追溯），③新型 **reconciler no-agent cron** `f6b9aa5e9034`（`every 15m`、wrapper `~/.hermes/scripts/quant_runtime_reconcile.py` → repo `runtime/reconcile.py` 為唯一邏輯來源、**建立即 paused**、與 handoff cron 職責分離不合併）。**驗證**：fence-free handoff dry-run → `would_append` B v2 at tail `t_1f97bf6b`（無 blocked-card gate）；隔離 fixture 序列（真實 pool＋真實 body、temp root、0 張真卡）→ `B v2 → C → D → E`；8 檔測試 **144 檢定**全綠（`test_production_handoff.py` 24→26）；frozen A v2 artifacts 與 `_survivors/**` 逐位元不變；**未** resume 任一 cron（handoff `624d0be5b23c` 與 reconciler `f6b9aa5e9034` 皆 paused）、**未** launch B v2、**未**產生 C/D/E 真卡。證據快照 `evidence/v1.7.0-production-recovery-20260914.json`。前一版描述如下：
- `QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md`：**v1.6.0，AUDITED PASS / FROZEN**（auditor `t_e18a0f35`，2026-09-13；audited content commit `6e7d046`；卡片 `t_68954a45`（ChatGPT GPT-5.6 Sol，依 Operator 決策與唯讀研究卡 `t_2382f20b`）：新增 §28 Survivor Evidence Preservation——survivor promotion → execution evidence、`leaderboard.json` entry 為唯一觸發點、大量 rejected／candidate cell 明文不保留逐筆 execution、寫入邊界收窄為 `_survivors/evidence/**` 且只能 staging→原子 rename 發佈、leaderboard 新增不參與排序的 evidence drill-back 欄位；前一版 **v1.5.2 = AUDITED PASS / FROZEN**（auditor `t_3691bfb4`，2026-09-13；audited content commit `0a361258`；remediation 卡片 `t_d19618e1`，auditor `t_346bcc04` 對 v1.5.1 的 F1／F2／F3 最小 remediation——reserved `_survivors` root 自身不得為 symlink 且須為 resolved results root 之下的 literal `_survivors`、forward slice `source_run.attempt_dir` 必須為 absolute path、ownership id 兩側皆須為 non-empty string）；前一版 **v1.5.1 = AUDITED FAIL**（auditor `t_346bcc04`，2026-09-13；F1 symlinked `_survivors` root 可覆寫 frozen verdict／在 frozen round 內建立 `forward/`、F2 relative `source_run.attempt_dir` 被收下、F3 兩側同值的數字 `kanban_task_id` 被收下）；前一版 **v1.5.0 = AUDITED FAIL**（auditor `t_57357d4c`，2026-09-13；F1 未受約束的 `--out`／`--out-dir` 可覆寫 frozen bundle／verdict、F2 完全自述的 forward slice 可變成 `FORWARD_POSITIVE`／`champion_candidate`／rank 1、F3 缺 `kanban_task_id` 的 bundle 放行）；再前一版 **v1.4.2，AUDITED PASS / FROZEN**（auditor `t_dedbe003`，2026-09-13；audited content commit `d699527`；remediation 卡片 `t_670a86af`，依 auditor `t_3edafbb9` 對 v1.4.1 的 F2 最小 remediation；前一版 **v1.4.1 = FAIL audit `t_3edafbb9`**，唯一 blocking finding F2：重跑比對多排除了 `generator.path`；再前一版 **v1.4.0 = FAIL audit `t_0bd01630`**，唯一 blocking finding F1：公開的 frozen bundle identity 無法依 v1.4.0 §10.8 措辭重算；再前一版 **v1.3.2 = AUDITED PASS / FROZEN**，auditor `t_3ffaeeb8`，2026-09-13，audited content commit `0363011`，remediation 卡片 `t_33457313` 依 auditor `t_23f4c3ef` 對 v1.3.1 的 FAIL）；
  上一個 AUDITED PASS / FROZEN 的版本是 **v1.5.2**（auditor `t_3691bfb4`，2026-09-13；audited content commit `0a361258`），
  更前為 **v1.1.1**（auditor re-audit `t_83682069`，2026-09-13；audited content commit `18d6c3f`）與 **v1.0.1**（auditor re-audit `t_e35c39c0`）。
- **v1.6.0（2026-09-13，AUDITED PASS / FROZEN；auditor `t_e18a0f35`，audited content commit `6e7d046`；卡片 `t_68954a45`，依 Operator 決策與唯讀研究卡 `t_2382f20b`）**：**Survivor Evidence Preservation（§28）**。
  只為「正式出現在 `leaderboard.json` `entries` 的 survivor」保存完整 execution evidence package（fills／episodes／equity 逐筆 ledger）；103,680 次 research evaluation 與所有未 promoted cell 維持 `artifacts/grid_*.csv` 摘要（**明文不建帳**）。
  ①**§28.1 觸發點**：leaderboard entry 是唯一觸發點（不是 Top-10、不是 PASS gate）；replay／materialize 皆先驗 index 成員再驗 leaderboard 成員，非 entry 一律 rc=1，且 evidence 的存在與否不回寫 verdict／`performance_claimable`／forward state／champion／ranking。
  ②**§28.2 同引擎 inert hook**：`container/scripts/20_strategy_a_run.py` 只加 `TRACE=None` ＋ `_trace()` ＋ 8 個 `if TRACE is not None:` 守衛（11 個 emit 點全在守衛內），trace 關閉時 aggregate／計算順序／語意逐欄不變。
  ③**§28.3 只 replay promoted winner cell**：`container/scripts/21_strategy_a_survivor_replay.py` import 同一顆 engine，只 replay 2 survivors × 9 註冊 grid = 18 個 winner cell，逐欄比對 terminal `DONE` sentinel-pinned frozen `artifacts/grid_*.csv`，不符即 fail-closed 不 materialize。
  ④**§28.4 package**：`runtime/survivor_evidence.py`（host 純 stdlib：`materialize`／`check`／`coverage`）落點唯一 `_survivors/evidence/<survivor_id>/`，只能 staging→原子 rename 發佈（identity 相同 → `already_identical`、不同 → refuse overwrite）；ledger 自驗（Σepisode 恆等式＋episode partition＋equity 純 stdlib 重算 Sharpe／MaxDD）。
  ⑤**§28.5 非排序 drill-back**：leaderboard 新增 `evidence_package_status`／`evidence_manifest_path`／`evidence_manifest_sha256`，不進排序 tuple；package 遺失時**重建後** `--check` 仍 rc=0 且 `rank`／`in_top10`／`evidence_state`／`champion_candidate` 與 `leaderboard.csv` 排序欄逐位元不變。
  **實跑**：18/18 winner cell 逐欄相符（frozen CSV 先與 sentinel `artifact_checksums` 相符）、`coverage` 2/2 PRESENT、`test_survivor_evidence.py` 12/12、`test_survivor_trace.py` 7/7（host ＋ 容器）、engine 37/37、既有 8 檔測試 **142 檢定**全綠。
  **獨立審計（auditor `t_e18a0f35`，2026-09-13，verdict APPROVED，9/9 項 PASS，無 blocking finding）**：staging 4 檔 sha 與 repo 逐位元一致、deployed `/scripts/20_strategy_a_run.py` 仍 `c4f9a216…`（未改寫）、容器對兩個 leaderboard entry 各重跑 9 grid → 18/18 `matches frozen row`（auditor 另以自寫腳本獨立重算 sentinel checksums 18/18、576 欄 0 不符、ledger 全對回 aggregate）、負向控制 host 21 案＋容器 4 變體全 rc=1 且不產生 package、frozen 五 artifact 與排名狀態逐位元不變；非阻斷殘留 **N1–N5**（文件措辭與既有計數：需在 §23 item 17／§28 補「（重建後）」、附錄 C 的「六條」實為 7 條、證據快照 `head_commit` 記為 `e4c6903` 而檔案於 `6e7d046` 入庫、`already_identical` 需以同一 `--engine` 路徑重現、附錄 B T17 既有的 30→33 計數漂移）經 operator **DEFER**，不開 v1.6.1。
  證據快照 `evidence/v1.6.0-survivor-evidence-20260913.json`；詳見契約 §28、§22 A29、§23 item 17、§25、附錄 B T19、附錄 C。
- **v1.7.0（2026-09-14，AUDITED PASS / FROZEN；auditor `t_3219d6a0`，audited content commit `d21ec33`；卡片 `t_15fed3f2`，ChatGPT（GPT-5.6 Sol））**：**production recovery automation**——改善 Hermes 升級／gateway-session 中斷／一般控制面擾動後的快速恢復，且**不** overengineer。
  ①**candidate pool re-author（§14.4）**：B v1 entry 逐字保留為歷史 provenance（consumed、永不重建、body 逐位元未改）；新增 `ema-crossover-walkforward-momentum-long-short-v2` 完整現行語意 body（eligible universe／data split／strategy domain 120 cases-cohort／**DCA PARAMETER DOMAIN** 四軸 48 組完整乘積／**COHORT SURVIVOR SEMANTICS**（historical-only selector、同一 cell 帶到 OOS-full-stress-鄰域、0 survivor=REJECT、>=1=PASS、全部 survivors 保留）／成本-funding-滑價-robustness／§10.8 bundle＋§28 evidence preservation（非 PASS gate）／科學失敗 vs 基建-operator 失敗分離）；C／D／E body 重寫為現行語意（科學假說- provenance- universe 意義不變；E 為 market-neutral，採 leg-aware execution semantics：每腿各自 ladder、leg 2 notional 由 OLS hedge ratio 決定、兩腿同時 reduce-only 平倉——**不**套用方向性單腿 DCA）；B v2／C／D／E `fingerprint_input` 依 §14.3 重算（含 DCA 域、symbols、selector/disposition 版本）。
  ②**board cleanup**：`t_3e696dce`（B v1，operator-stopped、無 PASS/REJECT）與 `t_720406f2`（`generated_at_utc` 冪等 flake，operator DEFER、不修、不記 done）封存為 `archived`（封存前各留 disposition comment）→ DB 讀回 `blocked_count=0`、兩卡於 archived board 可追溯。
  ③**reconciler no-agent cron（建立即 paused）**：job `f6b9aa5e9034`、`every 15m`、deliver `discord:1519163199117721650`、入口 `~/.hermes/scripts/quant_runtime_reconcile.py`（純 `runpy` wrapper；repo `runtime/reconcile.py` 為唯一邏輯來源；正常 no-op 靜默、只輸出真實 unblock 或**新** incident signature；`--dry-run` 不寫 state）。與 handoff cron `624d0be5b23c` **職責分離、不合併**（§9.4 v1.7.0 條）；**不得**代 handoff 建卡、auto-restart container、auto-publish orphan INCOMPLETE、建 recovery daemon、checkpointing。
  ④**驗證**：fence-free `production_handoff.py --dry-run --json` → `would_append` B v2 at tail `t_1f97bf6b`；隔離 fixture 序列（真實 pool＋真實 body、temp root、0 張真卡）→ `B v2 → C → D → E → no_eligible_candidate`；8 檔測試 **144 檢定**全綠（`test_production_handoff.py` 24→**26**，新增 pool-ordering regression）；frozen A v2 artifacts／`_survivors/{survivor-index.json,leaderboard.json,leaderboard.csv}`／`evidence/**` 逐位元不變；**未** resume 任一 cron、**未** launch B v2、**未**產生 C/D/E 真卡、**未**執行 Qlib 計算、**未**新增 service／daemon／queue／Registry／Orchestrator。
  **獨立審計（auditor `t_3219d6a0`，2026-09-14，verdict APPROVED，approved=true；audited content commit `d21ec33` = remote main，worktree clean）**：remote blob 3 檔與隔離 snapshot 相符、static audit **42/42 PASS**、隔離 pool verifier 全 true、序列 fixture `B v2 → C → D → E`（0 張真卡）、8 檔測試 **144/144 PASS**（post-survivor flake 本次未命中、未重試）、board blocked=0（`t_3e696dce`／`t_720406f2` archived、trace 可讀）、`f6b9aa5e9034` 與 `624d0be5b23c` 皆仍 paused、foreign-cwd reconciler dry-run rc=0／stdout 0 bytes／state 未變、handoff dry-run `would_append` B v2 at `t_1f97bf6b`、frozen A v2／`_survivors`／B v1 指定 SHA 全數相符；未 resume cron、未 launch B v2、未改動 repo 或 `/results`（報告 sha256 `035d1b0e…`）。
  證據快照 `evidence/v1.7.0-production-recovery-20260914.json`；pool 前後 bytes 快照 `_handoff/verification/v1.7.0-pool-before/**`；驗證腳本 `_handoff/verification/v1.7.0-pool-{verify,sequence-verify}.py`；詳見契約 §9.4（v1.7.0 條）、§14.4、§21.1 R0／R3。
- **v1.7.1（2026-09-14，AUDITED PASS / FROZEN；auditor `t_235ae131`，audited content commit `ed07605`；依卡片 `t_fd672413`，ChatGPT（GPT-5.6 Sol））**：**reconciler authoritative current attempt**——修 production 觀測到的 control-plane correctness bug，**不**改科學語意、**不**碰任何 frozen artifact。
  ①**bug**：`runtime/reconcile.py` 逐 attempt 掃描，對同一 round 內已被較新 active attempt supersede 的較舊 terminal 仍會放行卡片——B v2 round `…-r1-u1`（FAILED）在 `…-r1-u2`（RUNNING_QLIB）之下仍被用來 unblock `t_35b3e5da`，造成 duplicate Hermes wake（Qlib u2 本身健康）。
  ②**修正（§9.4 v1.7.1 `[C]`）**：掃描單位改為 round；同 round 內**只有** authoritative current attempt（attempt `run-spec.json` identity 合法；ordering＝`created_at_utc` 主序 ＋ `uN` 序數**數值** tie-break，`u10` > `u9`）可驅動 Kanban 狀態轉換；較舊 attempt 的 terminal sentinel 一律 `superseded` descriptive no-op（可讀／可驗證／保留 provenance，但不得 unblock／complete／block、不產生 incident／comment）；較新 attempt 的 identity／ordering metadata 缺失、不可解析或歧義（含同一 `created_at_utc` 無 tie-break、同 round task ownership 衝突）→ 整 round **fail closed**（新 incident kind `attempt_selection_ambiguous`，**不得**回退較舊 terminal）；consumed 判定仍先於驗證；§9.4 既有九項驗證清單與 §12.6 三步語意不變。
  ③**不做的事**：不新增 daemon／service／DB／current-pointer registry／state machine（只是在 discover 之後、handle 之前的小型 round grouping）；不改 Strategy B engine／策略語意／candidate pool／handoff logic；未 resume 任一 cron（reconciler `f6b9aa5e9034` 仍 paused）；未停／未重啟 B v2 u2、未開 u3；未產生 Strategy C／D／E 真卡。
  ④**驗證**：修正前同一 dry-run → `would_unblock=['t_35b3e5da']`（bug 無副作用重現）；修正後 → `scanned=9 incidents=0 unblocked=[] would_unblock=[]`、`…-r1-u1=superseded`（authoritative＝`…-r1-u2`）、`…-r1-u2=orphan_candidate`（`stage=RUNNING_QLIB`）；B v2 attempt tree 34 檔前後逐位元比對除 u2 進行中的 `artifacts/grid_fee_2x.csv`（live writer）外全部相同；`test_reconcile.py` **38/38**（新增 15 項 round-level 檢定；同一批檢定對 v1.7.0 的 `reconcile.py` bytes → **11 FAIL ＋ 3 ERROR**）、8 檔合計 **159 檢定**全綠。
  **獨立審計（auditor `t_235ae131`，2026-09-14，verdict APPROVED，approved=true；audited content commit `ed07605` = remote main，worktree clean）**：5/5 audited files blob-sha 相符（GitHub API 讀回）、`test_reconcile.py` 38/38、8 檔測試 **159/159 PASS**、RED check（同一批新檢定對 v1.7.0 `reconcile.py` bytes → **11 FAIL ＋ 3 ERROR**）、70/70 /tmp 負向控制、production dry-run 對照（v1.7.0 = `would_unblock=['t_35b3e5da']`；v1.7.1 = `[]`）、B v2 42 檔 snapshot 無硬變動（u2 仍 RUNNING_QLIB）、frozen SHA 14/14、cron `f6b9aa5e9034` 仍 paused、board blocked=0、契約 v1.7.1 五點與實作逐點一致；未 resume cron、未 launch B v2、未改動 repo 或 `/results`；F1／F2（MINOR 文件精度）經 operator 明確 **DEFER**、不開 v1.7.2（報告 sha256 `e6db775c…`）。
  契約新增：§9.4 v1.7.1 `[C]`（含 5 點語意）與兩條 `[V]`、§12.6 `attempt_selection_ambiguous`、§22 **A30**、§23 **item 18**、§25 兩條硬規則、附錄 C v1.7.1 row；證據快照 `evidence/v1.7.1-reconciler-current-attempt-20260914.json`。
- **v1.5.2（2026-09-13，AUDITED PASS / FROZEN；auditor `t_3691bfb4`，audited content commit `0a361258`；卡片 `t_d19618e1`，auditor `t_346bcc04` 對 v1.5.1 的 F1／F2／F3 最小 remediation）**：**post-survivor trust-boundary 殘留收尾**。
  ①**§27.1 reserved root 納入檢查**：`<results-root>/_survivors` 本身必須不是 symlink，且 resolved 後必須恰為 resolved results root 之下的 **literal `_survivors`**；任何 root／ancestor escape 一律在**任何寫入前** rc=1，並套用到 `survivor_index.py --out`、`survivor_leaderboard.py leaderboard --out-dir` 與 `survivor_leaderboard.py forward` 的 append——v1.5.1 的兩側 realpath 對稱檢查在 boundary 被 re-point 時會把 frozen round 當成界內（F1）。
  ②**§27.3 `source_run.attempt_dir` 必須為 absolute path**：relative 會以讀取端 cwd 解析，同一份 slice 因此在一處可驗、另一處不可驗；absolute 且 realpath 後位於 results root 之非 `_survivors` attempt 目錄才收（F2）。
  ③**§27.2 第 4 項 ownership 型別收緊**：`kanban_task_id` 於 bundle 與 `family.json` 兩側皆須存在、皆為 non-empty string 且逐字相等；int／bool／list／null／空字串一律 fail-closed，**即使兩側同值**（F3）。
  **實跑**：三組 v1.5.2 regression 在 `84f494c`（v1.5.1）的 worktree 上 `Ran 3 tests … FAILED (failures=3)`（非恆真），在 v1.5.2 全數 rc=1。`runtime/tests/test_post_survivor.py` 30 → **33/33**（7 檔合計 **130 檢定**全綠）。
  **既有 immutable artifacts 逐位元未改**（bundle `4638885f…`／verdict `cb470adf…`／result `012e6d1a…`／survivors `74f250cf…`／round-spec `e0b348bf…`，bundle identity 仍 `c051759f…`；`runtime/survivor_bundle.py` 未改動）；`_survivors/{survivor-index.json,leaderboard.json}` 因內含 `contract` 版本字串而在界內重建一次（`leaderboard.csv` 逐位元不變），re-seed 後仍恰 2 survivors、兩者 `FROZEN_ONLY`、0 slices、0 `champion_candidate`，`--check` rc=0。leaderboard 排序、survivor／family gate、cron 狀態與 B v2 未 launch 均未變動。
  證據快照 `evidence/v1.5.2-post-survivor-trust-boundary-20260913.json`；**獨立 re-audit（auditor `t_3691bfb4`，2026-09-13，verdict APPROVED）**：在 `/tmp` 隔離複本上重現 F1／F2／F3 全數在寫入前拒寫（F1 48 攻擊全 rc=1、frozen round 零新增檔案零殘留；F2 於結果樹 cwd／repo cwd／`/` 三處皆拒、出處鏈 11/11 fail-closed；F3 五種非字串值全 rc=1 且非 checksum 攔截），130 檢定全綠、v1.5.1 bytes 上三組 regression `FAILED (failures=3)`（非恆真）、frozen 五 artifact 與 bundle identity 逐位元不變、derived `_survivors/**` 重建經 mtime 鑑識僅限界內三檔且 `--check` clean；另有 3 個非阻斷 minor 殘留（R1 `--out` 指向（不存在）reserved node 會建立檔案、R2 `--out` 指向既有目錄時以 traceback 結束、R3 relocated copy 的 `--check` 依設計失敗），operator 卡片 `t_cbf7344c` 明確 **DEFER**、不開 v1.5.3。**post-audit 觀察（finalization 期間，2026-09-13）**：`runtime/tests/test_post_survivor.py::TestSurvivorIndex.test_index_cli_writes_checks_and_detects_drift` 為 intermittent（單一測試方法實測 **5/60** 失敗、rc 仍為 0）：`survivor_index.write_index()` 以含 `generated_at_utc`（秒解析度）的完整文字比對決定 `unchanged`，故第二次 CLI 跨秒時回報 `result=written`（measured 內容完全相同、僅時間戳不同；同秒內則 `unchanged`）；`--check`、fail-closed 行為與 frozen artifacts 均不受影響。此觀察不在 auditor 的 R1–R3 之內、本 finalization 未作判定，已另立卡片 `t_720406f2` 交 default 追蹤。詳見契約 §27.1／§27.2／§27.3、§22 A28、§23 item 16、§25、附錄 C。
- **v1.5.1（2026-09-13，AUDITED FAIL；auditor `t_346bcc04`；卡片 `t_171ba94f`，為 auditor `t_57357d4c` 對 v1.5.0 的 F1／F2／F3 最小 remediation；三個 trust-boundary 殘留已由 v1.5.2 修正）**：**post-survivor 寫入邊界與 forward evidence 出處契約**。
  ①**§27.1 邊界改為工具層強制**：`survivor_index.py --out` 與 `survivor_leaderboard.py leaderboard --out-dir` 必須把候選路徑與 `<results-root>/_survivors` **兩側 realpath 解析後**比對前綴，界外即 rc=1 且不寫入任何檔案（symlink 與 `..` 段皆無效），界內子路徑仍允許——v1.5.0 的 `--out` 曾以 rc=0 覆寫 frozen `survivor-bundle.json`／`verdict.json`（F1）。
  ②**§27.3 新增 slice `source_run` 出處契約**：`attempt_dir`（結果樹內、非 `_survivors/**`、目錄名等於 `run_id`）＋ terminal `DONE` sentinel（sha256 相符、`status=DONE`、`run_id`／`task_id`／`family_id` 相符、非 frozen research run）＋ sentinel 記錄且與磁碟相符的 `result.json` checksum ＋ 與 slice **逐欄相等**的 `result.json.forward_slice`；寫入與 leaderboard 重建時都重新驗證，任一不符即拒收／拒排名——v1.5.0 收下過一份完全自述的 slice 並讓它成為 `FORWARD_POSITIVE`／`champion_candidate`／rank 1（F2）。
  ③**§27.2 第 4 項 fail-closed 收緊**：`kanban_task_id` 於 bundle 或 `family.json` **任一方缺漏**即來源不一致（原本只在兩側皆 truthy 時比對，導致「移除 bundle 的 ownership id 並自洽重簽 identity」被 false accept）（F3）。
  **實跑**：三組 v1.5.0 攻擊在 `/tmp` 複本上全部被拒（rc=1）且 frozen `survivor-bundle.json`／`verdict.json` sha256 不變；同一份 regression 放進 `ceeadb1` 的 worktree 實跑為 4 FAIL（非恆真）。`runtime/tests/test_post_survivor.py` 25 → **30/30**（7 檔合計 **127 檢定**全綠）。
  **既有 immutable artifacts 逐位元未改**（bundle `4638885f…`／verdict `cb470adf…`／result `012e6d1a…`／survivors `74f250cf…`／round-spec `e0b348bf…`，bundle identity 仍 `c051759f…`；`runtime/survivor_bundle.py` 未改動）；`_survivors/{survivor-index.json,leaderboard.json}` 因內含 `contract` 版本字串而在界內重建一次（`leaderboard.csv` 逐位元不變），re-seed 後仍恰 2 survivors、兩者 `FROZEN_ONLY`、0 slices、0 `champion_candidate`，`--check` rc=0。
  證據快照 `evidence/v1.5.1-post-survivor-boundary-remediation-20260913.json`；詳見契約 §27.1／§27.2／§27.3、§22 A28、§23 item 15、§25、附錄 B T17/T18、附錄 C。
- **v1.5.0（2026-09-13，AUDITED FAIL；auditor `t_57357d4c`，2026-09-13；依 ChatGPT（GPT-5.6 Sol）卡片 `t_a7cfcdfd`；F1／F2／F3 已由 v1.5.1 最小 remediation 修正）**：**Post-Survivor Lifecycle（§27）**。
  frozen survivor 之後的生命週期正式化：full backtest → frozen survivor bundle → **forward evidence** → **survivor leaderboard** → champion candidate／challenger → future paper/testnet → future live candidate selection。
  ①**file-only survivor index**（`/results/_survivors/survivor-index.json`，derived/rebuildable，**不是** Registry service）：掃描既有 frozen bundles，`survivor_id = "sv-" + sha256(canonical({family_id, round_id, run_id, bundle_identity_sha256, cohort, strategy_params, dca_params}))[:16]`；
  缺 checksum／duplicate survivor_id／bundle invalid／來源不一致（目錄名、`kanban_task_id`、`round-spec.json` checksum）／param cell 非註冊軸一律 fail-closed。
  ②**append-only forward evidence**（`_survivors/forward/<survivor_id>.jsonl`）：嚴格 post-freeze（`data_start > cutoff`，cutoff 取自 checksum 驗證過的 `round-spec.json`）、不得重疊、params／bundle identity 必須是 incumbent 的、寫入後 readback；沒有真實計算結果時正確狀態是 **zero-forward**，**不得**偽造。
  ③**challenger rule**：改任一 strategy／DCA 參數不得覆寫 incumbent（新 family ＋ `challenger_of`），challenger 的 OOS 起點必須晚於自身 preregistration cutoff，且必須重走完整 full-backtest gate。
  ④**leaderboard v1**（`leaderboard.json` ＋ `leaderboard.csv` ＋ Top-10）：透明 deterministic ordering（`has_forward` → forward sharpe／return／max_dd_pct 絕對值 → `oos_sharpe` → robustness stress floor → neighbourhood → `survivor_id`）；`evidence_state` ∈ {`FROZEN_ONLY`,`ACCUMULATING`,`FORWARD_POSITIVE`,`FORWARD_DEGRADED`} **只描述、永不回寫 PASS**；掉出 Top-10 **不等於** REJECT；`champion_candidate` 只是 research shortlist，v1.5 不發實盤訊號、不配置資金。
  **seed 實跑**：既有 A v2 frozen bundle → index 恰 2 survivors（`BTCUSDT/1h`、`SOLUSDT/4h`）、leaderboard 兩列皆 `FROZEN_ONLY`、0 forward slices、0 `champion_candidate`；排名由既有 evidence 客觀產生（`SOLUSDT/4h` `oos_sharpe` 2.17438 > `BTCUSDT/1h` 0.536464），非硬寫名字。
  **既有 immutable artifacts 逐位元未改**（bundle `4638885f…`／verdict `cb470adf…`／result `012e6d1a…`／survivors `74f250cf…`，bundle identity 仍 `c051759f…`）；`runtime/tests/test_post_survivor.py` **25/25**（7 檔合計 122 檢定全綠）。
  **v1.5.1 更正**：本版已由 auditor `t_57357d4c` 判為 **AUDITED FAIL**（F1 unsandboxed `--out`／`--out-dir` 可覆寫 frozen bundle／verdict；F2 全域自述的 forward slice 可變成 `FORWARD_POSITIVE`／`champion_candidate`／rank 1；F3 缺 `kanban_task_id` 放行），已由 v1.5.1 修正（見上）。 **v1.5.2 更正**：v1.5.1 亦已由 auditor `t_346bcc04` 判為 **AUDITED FAIL**（F1 symlinked `_survivors` root、F2 relative `source_run.attempt_dir`、F3 非字串 `kanban_task_id`），已由 v1.5.2 修正（見上）。
  證據快照 `evidence/v1.5.0-post-survivor-lifecycle-20260913.json`；詳見契約 §27、§22 A28、§23 item 15、§25、附錄 B T17/T18。
- **v1.4.2（2026-09-13，AUDITED PASS / FROZEN；auditor `t_dedbe003`，audited content commit `d699527`；卡片 `t_670a86af`，auditor `t_3edafbb9` 對 v1.4.1 的 F2 最小 remediation）**：**重跑比對逐鍵收緊**。
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
- **仍屬 `[T]`／`DEFERRED` 的非阻斷項**：fingerprint 自動化、yield 判定自動化、chain-head 專用查詢、failure drills D1–D13、`container exec` 投遞的額外腳本化包裝。正式 family 目錄與 ownership/lineage artifacts 已長期落地，不再列為缺口；上述 deferred 項也不是現行 Qlib production 的 gate。
- **Retired secondary engines（非待辦）**：Lean／Nautilus／PyBroker 已退役且不參與現行或已規劃的 production workflow。現行 production 的唯一計算面與效能真值來源是 Qlib full-backtest；歷史文件中的 retired-engine references 只保留為 provenance，不構成 gate、validation stage 或 future integration commitment（Contract §17）。

### Common Data Pack（`/Volumes/ExpansionDrive/market-data-raw`）

本 repo 的 runtime 只讀 engine-neutral raw data；資料同步已由同一支
`_tools/market_data_sync.py` 維護，不另設第二套 backtester 或資料管線。
截至 **2026-09-21**，pack 已實際匯入並 read-back 驗證：

- Binance USD-M／spot、CBOE VIX + VIX9D/VIX3M/VVIX/SKEW、Deribit DVOL。
- Alternative.me FGI（2018-02-01 → 2026-09-21）。
- Coin Metrics Community BTC/ETH daily asset metrics（provider-native 起始日 → 2026-09-20）。
- FRED rates/liquidity/inflation/labour/credit/index bounded pack（series-native coverage）。
- CFTC legacy/TFF/disaggregated selected-market COT（1986/2006/2017 → 2026-09-15）。
- Kenneth French US/developed 3F/5F/momentum daily + monthly tables（1926/1990 → 2026-07-31）。
- Deribit BTC/ETH perpetual funding rolling public window，及每日 futures/options book-summary snapshot。

Common Data Pack 的目的，是把多策略會反覆使用的**高復用回測原料**一次放進 canonical raw；
不是建立 data-governance platform。官方 API／官方下載優先；來源透明、維護可靠、授權與原始
資料可追溯的權威 GitHub 專案或成熟 adapter／distribution 也可作資料取得管道，但不因此成為
production framework 或 runtime dependency。homogeneous single-source dataset 只需在
dataset／provider 層級保留可追溯來源；重複的 row-level `source`／`truth_status` 缺漏屬
**non-blocking hygiene**，除非來源／真值語意會逐 observation 改變，或策略科學正確性確實要求。

Common Data Pack 的完成定義只有四項：高復用資料已落地、基本完整性／coverage／可讀性／時間對齊合理、
掛入同一支 `_tools/market_data_sync.py`，並由同一個 `ai.marketdata.raw-sync` 每日統一補資料。
達成即 DONE；不再為 metadata hygiene 新增 gate。tick／orderbook／full option-chain 等 heavy data
維持 lazy／on-demand；v2 到此 freeze，後續只在出現明顯高復用的新共通資料時增補。

RaQL 的既有 prerequisite-missing round 保持 immutable；以
`python3 runtime/crypto_bitcoin_cvar_risk_aware_q_learning_prerequisite_check.py --live-recheck --json`
做唯讀 current-raw recheck。2026-09-22 fresh read-back 為 `PASS`：FGI `PRESENT`（3,151 rows，
2018-02-01 → 2026-09-21）、`missing_inputs=[]`、核心 signal 可得，且 frozen round artifacts 未改寫。

## 3. Architecture boundary

```
n8n + Hermes / Kanban (control plane)
        │  卡片進 → 執行 → sentinel 落地
        ▼
Apple Container / Qlib  (compute plane, 唯一計算面)
        │  單向：raw(ro) → 計算 → /results(rw)
        ▼
durable /results  (authoritative research evidence)
        │
        ├──→ host reconciler (deterministic, no-agent) → Kanban unblock → Hermes
        │
        └──→ formal survivor leaderboard → guarded compact mirror
             → HCH725/validated-survivor-research (downstream research only)

Runtime truth/read-back (results + watchdog state + Kanban)
        │  read-only projection
        ▼
candidate_snapshot.py → dashboard.json
        ▼
Homepage + Detail → Cloudflare Tunnel + Access → operator
(Observability / Display Plane; non-blocking, non-control-plane)
```

- **Observability / Display Plane**：`homepage/` + `runtime/candidate_snapshot.py --dashboard-json` + `runtime/dashboard_serve.py` 只做唯讀投影；它不啟動、停止、重試、unblock 或回測，不自行重算 health／ranking／績效。Display Plane 掛掉不得影響 Qlib、reconciler、handoff 或任何 authoritative evidence。Production UI 為 `https://quant.vicchong1983.trade`（Cloudflare Access 保護）；現行快照 producer 每 300 秒更新一次，Homepage widget 每 60 秒讀取一次。詳細部署與驗收契約見 `homepage/README.md`。
- container 對外的唯一 communication surface 是 `/results` 檔案系統；反方向只用 host 主動發起的 `container exec`。
- **Secondary-engine boundary**：Lean／Nautilus／PyBroker 均已退役、non-participating。現行及已規劃的 production workflow 只有 Qlib；不得把 retired engine 當成 current/future gate、performance truth 或第二套 parameter-search/backtest path。任何日後重新導入第二引擎都必須由 operator 另走 Contract §26，不能從歷史文字自動復活。
- 完成橋只能是「durable sentinel + host deterministic no-agent reconciler + kanban unblock」；
  不得引入 HTTP server / webhook / Redis / Celery / queue manager。

## 4. 內容

| 路徑 | 內容 |
|---|---|
| `QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md` | canonical contract（現行 **v1.11.0 / AUDITED PASS / LIVE**；C3 cadence／HOLD／maintenance semantics 已完成獨立 audit 與 live scheduled cutover；前一版 **v1.10.0 / AUDITED PASS / FROZEN** 的 semantic content commit `266390f1bc8179c503731cb1af2fda6195f7af42` 已由 auditor `t_9073d65f` 於 2026-09-21 獨立唯讀審計 PASS（0 blocking findings）。前一版 v1.9.0 的 audited content commit 為 `511b6e3`、auditor `t_bde913b0`、2026-09-15，依卡片 `t_2b8c076c`（ChatGPT（GPT-5.6 Sol）：§9.4 compute-finished wake——authoritative current attempt 無 terminal sentinel、`run-spec.json` identity 可讀、DB 讀回卡片仍 `scheduled`、`state.json` stage ∈ {`ARTIFACT_READY`,`FAILED_SCRIPT`} 時，reconciler 只執行**既有** `unblock` 喚醒 default 做 host-side 處置；`ARTIFACT_READY` 不得等同 `DONE`、不寫 terminal／`verdict.json`／`/results`；其餘 fail-closed 行為不變）；其後 `bdd2bae`（§14.4 純文字對齊，卡片 `t_86d04b09`）、其後 `b48e449`（已稽核的 lifecycle implementation／alignment：`runtime/production_handoff.py` ＋ `runtime/tests/test_production_handoff.py` ＋ §6.4，auditor `t_514e4827` PASS；不 bump 版本、無新 gate／stage）；前一版 **v1.8.0**（§26.1 一次性 additive schema migration 例外；交付時 AWAITING AUDIT，其後稽核序列記於 board）；前一版 **v1.7.1 / AUDITED PASS / FROZEN**，audited content commit `ed07605`，auditor `t_235ae131`，2026-09-14，依卡片 `t_fd672413`（ChatGPT（GPT-5.6 Sol）：reconciler authoritative current attempt——同 round 內只有最新有效 attempt 可改動 Kanban 狀態、較舊 terminal 一律 `superseded` no-op、unorderable round fail closed 且不得回退；ordering＝`run-spec.json.created_at_utc` 主序＋`uN` 序數 tie-break）；前一版 **v1.7.0 / AUDITED PASS / FROZEN**，audited content commit `d21ec33`，auditor `t_3219d6a0`，2026-09-14，依卡片 `t_15fed3f2`（ChatGPT（GPT-5.6 Sol）：production recovery automation——①§14.4 candidate pool re-author（B v1 歷史 entry 逐字保留為 provenance、新增 B v2 完整現行語意 body、C/D/E 重寫為現行語意並依 §14.3 重算 `fingerprint_input`），②board cleanup（`t_3e696dce`／`t_720406f2` 封存 archived、active blocked = 0），③reconciler no-agent cron `f6b9aa5e9034`（`every 15m`、no-agent、建立即 paused、與 handoff cron `624d0be5b23c` 職責分離）；驗證：fence-free handoff dry-run → `would_append` B v2 at tail `t_1f97bf6b`、隔離 fixture 序列 `B v2 → C → D → E`、8 檔測試 **144 檢定**全綠；未 resume 任一 cron、未 launch B v2）；前一版 **v1.6.0 / AUDITED PASS / FROZEN**，audited content commit `6e7d046`，auditor `t_e18a0f35`，2026-09-13，依卡片 `t_68954a45`（依 Operator 決策與唯讀研究卡 `t_2382f20b`：新增 §28 Survivor Evidence Preservation——leaderboard entry 為唯一觸發點、只 replay promoted winner cell（2 survivors × 9 註冊 grid = 18 次，未重跑 103,680 次 evaluation）、同一顆 engine 的 inert trace hook、`_survivors/evidence/**` ＋ staging→原子 rename、非排序 evidence drill-back；另新增 §22 A29、§23 item 17、§25 v1.6.0 硬規則、附錄 B T19）；前一版 **v1.5.2 / AUDITED PASS / FROZEN**，audited content commit `0a361258`，auditor `t_3691bfb4`，2026-09-13，依卡片 `t_d19618e1`（auditor `t_346bcc04` 對 v1.5.1 的 F1/F2/F3 最小 remediation：§27.1 `_survivors/**` 寫入邊界改為工具層 realpath 強制、§27.3 slice `source_run` 出處契約（terminal `DONE` ＋ sentinel 記錄的 `result.json` checksum ＋ 逐欄相等的 `forward_slice`，寫入與排名時都重新驗證）、§27.2 第 4 項 `kanban_task_id` 缺漏即來源不一致）；（v1.5.2：§27.1 reserved root 自身不得為 symlink 且須為 resolved results root 之下的 literal `_survivors`；§27.3 `source_run.attempt_dir` 必須為 absolute path；§27.2 第 4 項 `kanban_task_id` 兩側皆須為非空字串且逐字相等。前一版 **v1.5.1 = AUDITED FAIL**（auditor `t_346bcc04`，2026-09-13）；再前一版 **v1.5.0 / AUDITED FAIL**（auditor `t_57357d4c`，2026-09-13；依卡片 `t_a7cfcdfd` 新增 §27 post-survivor lifecycle：file-only survivor index、append-only forward evidence、Top-10 leaderboard、challenger rule、champion／live-candidate 邊界；blocking findings F1/F2/F3）；再前一版 **v1.4.2 / AUDITED PASS / FROZEN**，audited content commit `d699527`，auditor `t_dedbe003`，2026-09-13，依卡片 `t_670a86af`（auditor `t_3edafbb9` 對 v1.4.1 的 F2 最小 remediation）：§10.8 重跑比對**逐鍵**只排除頂層 `contract` 與巢狀 `generator.sha256`，`generator.path` 仍納入比對；前版 **v1.4.1 = FAIL audit `t_3edafbb9`**（F2）；**v1.4.0 = FAIL audit `t_0bd01630`**（F1）；更前 FROZEN 版本 **v1.3.2 / AUDITED PASS / FROZEN**，audited content commit `0363011`，auditor `t_3ffaeeb8`，依 `t_33457313`（audit `t_23f4c3ef` 對 v1.3.1 的 F3/F4 最小 remediation）；更前 **v1.3.1** = FAIL audit `t_23f4c3ef`；**v1.3.0** = FAIL audit `t_246c62d7`；更前 FROZEN 版本 **v1.2.0 / AUDITED PASS / FROZEN**，audited content commit `068d6f7`，auditor `t_7b979fe8`，含 §14.4 automatic handoff；再前為 **v1.1.1 / AUDITED PASS / FROZEN**，audited content commit `18d6c3f`，auditor re-audit `t_83682069`），全文三級標記 `[V]`/`[C]`/`[T]`，變更記錄見附錄 C |
| `container/Containerfile` | **唯一** image 定義：`python:3.12-slim` + Qlib `v0.9.7`（build 內 `rev-parse HEAD` 守衛，upstream 移動 tag 即 build 失敗） |
| `container/scripts/` | **現行** runtime 的 canonical rebuild / verify / engine 定義：`20_strategy_a_run.py`（v1.3.0/v1.3.1/v1.3.2 Strategy A 全量回測 engine；**v1.6.0** 起只加 inert trace hook（§28.2；trace 關閉時語意逐欄不變））、`21_strategy_a_survivor_replay.py`（**v1.6.0** §28.3 survivor replay 驅動：import 同一顆 engine，只 replay 已 promoted 的 winner cell 並逐欄比對 frozen `artifacts/grid_*.csv`）、`00_env_baseline.py`、`03_qlib_smoke.py`、`verify_final.sh`、`run_phase4.sh`、`fetch_kernel.sh`、`tests/test_strategy_a_engine.py`（37 檢定）、`tests/test_survivor_trace.py`（**v1.6.0**，7 檢定）。v1.1.0 僅 `verify_final.sh` 兩處 Qlib 檢查改為 `/opt/venv/bin/python`，其餘與稽核當時逐位元相同（見 §6 Provenance）；一次性 phase/history 腳本已排除（分類與理由見 `evidence/README.md`）。**已 operator-stopped 的 Strategy B runner 與其 test 不在這裡**：它們逐位元存檔於 `evidence/strategy-b-operator-stopped/runtime/`（archive-only，不得執行），host `/scripts` 部署副本亦已移除（Contract §13 archive hygiene，卡 `t_6c83c9fb`） |
| `runtime/` | host 端最小 runtime readiness：`survivor_index.py`（**v1.5.2** §27.2 file-only survivor index：掃描 `<root>/*/rounds/*/survivor-bundle.json`、以 §10.8 canonical 序列化釘出 deterministic `survivor_id`、`research_data_cutoff` 必須來自 checksum 相符的 `round-spec.json`、`kanban_task_id` 缺漏即來源不一致、`--out` 受 `_survivors/**` realpath 寫入邊界強制（v1.5.2 起 reserved root 自身不得為 symlink、resolved 後須為 literal `_survivors`）；fail-closed；純 stdlib、非服務）、`survivor_leaderboard.py`（**v1.5.2** §27.3／§27.5：`forward` 收件 append-only forward slice（必填 `source_run` 出處：**absolute path**、結果樹內、非 `_survivors` 的 terminal `DONE` attempt ＋ sentinel 記錄且與磁碟相符的 `result.json` checksum ＋ 逐欄相等的 `result.json.forward_slice`，寫入與排名時都重新驗證）＋ `leaderboard` 產生 `leaderboard.json`／`leaderboard.csv`／Top-10，透明 deterministic ordering、`--out-dir` 與 `forward` append 同一寫入邊界（v1.5.2 起 reserved root 不得為 symlink）；純 stdlib、非服務）、`tests/test_post_survivor.py`（現行 **44 檢定**，含 F1／F2／F3 regression 與 v1.5.2 trust-boundary regression）、`survivor_bundle.py`（**v1.5.0** §10.8 frozen survivor bundle 產生器：全部 survivors、`ranking=null`、不得改寫、只由 terminal `DONE` 且 coverage／assertions 全真之 attempt 產生、公開 `bundle_identity_sha256` 依 §10.8 canonical recipe（移除 `generated_at_utc` 與 identity 欄位自身）可由 auditor 以純 stdlib 獨立重算、重跑比對**逐鍵**只排除頂層 `contract` 與巢狀 `generator.sha256`（`generator.path` 仍納入比對）；純 stdlib、非服務；v1.5.1 未改動）、`survivor_private_export.py`（**v1.10.0 / implementation already audited before doc alignment**：formal leaderboard entries 的 guarded compact private mirror；canonical-root write only、`--check`/temp/custom root 不觸發、baseline/evidence immutable conflict fail-closed、Git failure non-blocking、無新 cron/service；純 stdlib）、`tests/test_survivor_private_export.py`（現行 **10 檢定**）、`survivor_evidence.py`（**v1.6.0** §28.4 host 端 survivor evidence：`materialize`／`check`／`coverage`；觸發點只認 leaderboard entry、落點唯一 `_survivors/evidence/<survivor_id>/`、只能 staging→原子 rename 發佈（identity 相同 → `already_identical`、不同 → refuse overwrite）、ledger 自驗（Σepisode 恆等式＋episode partition＋equity 純 stdlib 重算 Sharpe／MaxDD）；純 stdlib、非服務）、`tests/test_survivor_evidence.py`（§28 現行 **14 檢定**）、`templates/strategy_b_v2_{round,run}_spec.template.json`（B v2 歷史 preregistration/rebuild input；該 family 後續已實際執行並 terminal REJECT）、`preflight.py`（P1–P10）、`reconcile.py`（no-agent 完成橋；**v1.7.1** §9.4 round-level authoritative current attempt：同 round 內只有最新有效 attempt 可改動 Kanban 狀態、較舊 terminal 一律 `superseded` descriptive no-op、較新 attempt metadata 缺失／歧義或同 round ownership 衝突即 fail closed（incident `attempt_selection_ambiguous`）且不得回退）、`terminal_evidence.py`（sentinel/checksum 產生器）、`production_handoff.py`（§14.4 automatic handoff：每輪檢查並最多 append 1 張 family 卡，v1.3.0 起另檢 candidate body 的 DCA domain／cohort survivor 標記；**2026-09-24 Kanban-free decision**（卡片 `t_6c6a3286`）：決策只讀 `/results` artifacts、**不讀也不要求 Hermes／Kanban**，卡片狀態（blocked／stale／讀不到）零 gate 效力，advance ＝ `family.json` 落地 ＋ 冪等 work-order 派送（無 `--parent`、無 read-back），runtime guard 為 90 分鐘 active attempt 窗（最新 attempt 在窗內且尚未發佈 terminal sentinel，或該 round 尚無 terminal verdict；`verdict.json` 為 per-round、不得短路 active attempt 守門）／90 分鐘 launch grace／canonical incident fail-closed，每輪輸出 `outcome=advanced|running|idle|finding|incident`；`tests/test_production_handoff.py` 現行 **39 檢定**）、`strategy_a_v2_counts.py`（v1.3.0 pre-registration 計數器／驗證器）、`templates/strategy_a_v2_{round,run}_spec.template.json`、`tests/test_reconcile.py`（現行 **46 檢定**；含 v1.7.1 round-level 與 v1.9 compute-finished wake regression）、`tests/test_preflight_p10.py`、`tests/test_production_handoff.py`、`tests/test_strategy_a_v2_counts.py`、`tests/test_survivor_bundle.py`（18 檢定）、`tests/test_strategy_b_v2_templates.py`（8 檢定）（皆 stdlib unittest）。純 stdlib、手動或 no_agent cron 觸發；不含任何常駐服務 |
| `homepage/` | **Observability / Display Plane** 的 Homepage v2.4.0 設定與 production runbook；唯讀、non-blocking、non-control-plane，正式入口 `https://quant.vicchong1983.trade`，詳細契約見 `homepage/README.md` |
| `N8N_CONTROL_PLANE.md`＋`n8n/` | **END-TO-END CANVAS + C3 HANDOFF — AUDITED PASS / LIVE；C4 RUNTIME RECONCILER — IMPLEMENTED / AWAITING INDEPENDENT AUDIT / NOT LIVE**：stable `shadowQuantCp1` 已展開 SOURCE / METRICS REFRESH 與 CURRENT LIFECYCLE 兩個清楚 lane，完整呈現 Research→Pool→Intake→Wiki→Queue/Parking→Preflight→Qlib Full Backtest→Historical/OOS→Robustness→Failure Analysis→Verdict→Reject/Survivor→Leaderboard→Private Repo，並以 exact-one current-stage routing＋counts summary 避免把 source-read 綠燈誤認為 lifecycle activity；Full Canvas 已完成獨立 re-audit、live import/publish 與真實 execution 驗證。C3 workflow `productionHandoffManualC2` 維持既有 AUDITED PASS / LIVE，只以 Manual／Schedule（`:05`／`:35`）觸發既有 host bridge action，deploy 前後 export hash 相同。C4 workflow `runtimeReconcilerC4` 維持 `active=false`，以 `6,21,36,51 * * * *` 觸發同一 fixed bridge 的 `runtime_reconcile_once`，尚未 live import／publish；watchdog 仍獨立、Full Canvas 仍 read-only。**不改動**任何 Qlib／data／candidate／leaderboard／reconciler/watchdog 語意 |
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
8. `runtime/recover_gate.py` ＋ `runtime/ai.quant.recover-gate.plist` — 可選的 host 重啟復原 gate（n8n＋qlib-run，
   `RunAtLoad`＋`StartInterval 300`，語意與實測證據見 `N8N_CONTROL_PLANE.md` §7.2）。
9. `runtime/n8n_host_action_bridge.py` ＋ `runtime/ai.quant.n8n-host-bridge.plist` — Phase 2C1 最小 n8n→host
   動作橋（固定 request/response 路徑＋單一 allowlist 動作＋WatchPaths 喚醒，fail-closed；見 `N8N_CONTROL_PLANE.md` §7.3）。

**執行環境限制（Contract §9.4；本 worktree 的 direct 改造見本檔頂端〈Pending local change〉）**：
歷史（pre-cutover）`runtime/reconcile.py` 的唯一變更動作是 `hermes kanban unblock`，而 Hermes 會拒絕來自
`HERMES_DELEGATED_CHILD_CONTEXT=1` context（delegate_task 子行程、kanban worker session，以及**由該 session
建立／觸發的 cron job**）的 board 變更，故當時 apply 必須在無此標記的 host context 執行。**本分支的 C4
已不再有任何 board 變更**：direct family 只以 detached `hermes -p default ... chat --query-file` 喚醒 default
做 host-side disposition，board/fence 對新執行路徑零效力（歷史 card-owned family 不由本路徑操作）；
`--dry-run` 仍全程唯讀。

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
- 不新增任何**在 repo 內**可自動觸發的排程（無 CI、無 hook）；本 repo 只提供 deterministic 腳本與版本控管。C3 production automatic handoff 的 live cadence 由 n8n workflow `productionHandoffManualC2`（`5,20,35,50 * * * *`，`:05/:20/:35/:50`）擁有；C3.1 已 **AUDITED PASS / LIVE**，Hermes cron `624d0be5b23c` 保持 paused、只作 rollback，不因此新增服務或狀態儲存。
- 同為 **Hermes cron**（非 repo 內排程）的 **hourly quant candidate snapshot**：`runtime/candidate_snapshot.py`（repo 唯一邏輯來源）＋ `~/.hermes/scripts/quant_candidate_snapshot.py`（純 `runpy` wrapper），default profile job `0090473eae7b`、`20 * * * *`、no-agent、deliver `discord:1523786664613511299`。純唯讀監控：只讀 `_survivors/leaderboard.json`（Top-5 逐字，不重排）、current family（**2026-09-24 起**＝共用 `production_handoff.runtime_state` 的 runtime-evidence 選取：只有「最新 attempt 在 90 分鐘窗內且（尚未發佈 terminal sentinel **或** 該 round 尚無 terminal verdict）」的 family 才算 current——`verdict.json` 是 per-round，較早輪次已判定、follow-up round 仍在寫的 family 仍是 current；未 launch／stale／已結案（terminal verdict ＋ terminal sentinel）的 family 不再被當成 current，無 active work 時明確輸出 `idle`／`current.state=idle`／`family_id=null`）的 authoritative attempt 進度（round-spec `expected.expected_case_evaluations` 為分母；分子取兩者較高並 cap 至總數：`grid_*.csv` 已串流資料列、該 attempt 自身 `artifacts/progress.json` 的 cohort／pair 計數換算值（F 型只在全部 cohort 完成後才一次寫出 grid，中途全靠此計數；檔案缺失或非法時退回串流列數），selection 重用 `reconcile.py` §9.4 v1.7.1）與 board 讀回；輸出順序固定 **Leaderboard → Current → Research Funnel → Runtime health**，Current 另示 cohort（該 attempt 仍 RUNNING_QLIB 時，取其已串流、mtime 最新的 `grid_*.csv` 最後一列之 symbol／timeframe，否則 unavailable；已取代舊 Stage 行）、Research Funnel 示 canonical intake state 的 Wiki ingested／reviewed 與 `family.json` 去重的 registered／backtested families（`+N/24h` 只讀既有 intake cron 報告，無法解析即 unavailable）；不寫 `/results`、不改 Kanban、不 launch／retry／unblock、不 bump contract 版本。
- 本 repo 與 `HCH725/nautilus-quant-system` 無關，不觸及、不取代它。
- n8n **SHADOW** 觀測層（`N8N_CONTROL_PLANE.md`、`n8n/`）仍是 read-only、非 pipeline state owner；C3 handoff workflow 是獨立的最小 cadence 編排，透過既有 bridge 呼叫 canonical `production_handoff`，不取代 Hermes/Qlib execution。C3.1 已依 §10 cutover gates 完成獨立 audit 與 live activation，live cadence 為 `:05/:20/:35/:50`；legacy Hermes handoff cron 保持 paused 作 rollback。C4 runtime reconciler 已 live，Hermes reconciler cron `f6b9aa5e9034` 保持 paused 作 rollback，watchdog `c5314d86cdfe` 維持 active。
