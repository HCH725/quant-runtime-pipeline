# N8N Control Plane — END-TO-END CANVAS + C3 PRODUCTION HANDOFF

## Host preparation + prepared direct dispatch — C3 LIVE

The current preparation boundary is outside n8n C3. Research Intake is the sole eligibility gate and appends each exact,
eligible candidate idempotently to `/Volumes/ExpansionDrive/qlib-results/_handoff/preparation_backlog.json`. Default-profile
no-agent Hermes cron `219d541661d5` runs `/Users/hong/.hermes/scripts/quant_prepare_candidate.py` at `:00/:15/:30/:45`, offset five
minutes from C3's existing `:05/:20/:35/:50` cadence. The deterministic host runner processes one ordered backlog candidate
per run and may start one bounded `quant-preparation` session under the existing handoff lease. A live family waits; a
candidate-local launch failure or completed attempt with no staged package/outcome is recorded and the exact candidate is
rotated to the backlog tail so unrelated candidates continue. That agent only stages runner/test/spec/manifest
artifacts or a permitted clear-absence outcome; it must not write the backlog or production pool, run Qlib/strategy
execution, or publish promotion. The host runner performs focused tests and P1–P10 validation and alone promotes a valid
execution-ready candidate into `candidates.json`.

Live C3 remains **ACTIVE** and unchanged. The bridge invokes `runtime/production_handoff.py` on its existing cadence and
consumes only execution-ready production-pool entries. It never starts `quant-preparation`. Prepared dispatch validates
the exact manifest and mirrored staged round/run specs, runs P1–P10, materializes identical canonical specs, then invokes
the fixed `/usr/local/bin/container exec -d qlib-run ...` command. That actual Qlib dispatch uses no Hermes production
executor and no shell. The explicit `--legacy-agent-dispatch` path is rollback-only.

The stable n8n workflow IDs, fixed request schema, allowlisted host bridge, and cadence are unchanged: no new n8n node,
queue, DB, daemon, or service was introduced. Historical direct-Hermes C3 text below must not be used as current instructions.

Current C4: the same fixed `runtime_reconcile_once` bridge invokes the repo reconciler. It ignores
historical card-owned families and selects only the newest valid attempt in each direct round. A
verified `ARTIFACT_READY`/`FAILED_SCRIPT` stage, a valid terminal sentinel still missing its own
round verdict, or an attempt that wrote nothing for longer than the 90-minute stall window without a
terminal (agent died before Qlib started, or Qlib died mid-run) starts another detached default CLI
disposition session, not a Kanban unblock. That agent checks artifacts and publishes host-side
terminal/verdict as appropriate - terminating the round or retrying it, so a dead attempt can never
hold the pipeline silently; `ARTIFACT_READY` alone never means PASS and a fresh (live) attempt is
never disturbed. A wake that cannot start is recorded as the fail-closed incident
`disposition_launch_failed`, which the wrapper reports by `family=`/`run=`, instead of retrying
invisibly. Missing/conflicting evidence remains fail-closed. `--dry-run` launches nothing.
The installed wrapper now announces a successful direct `launched` action
(`reconciler: launched default disposition for ...`) and reports incidents by `family=`/`run=`
instead of `task=`; a legacy `unblocked` line remains only for a rolled-back board core. The compute-finished
disposition path intentionally launches Hermes `quant-production` sessions in current C4 as the family-level research/disposition boundary; it is not C3 mechanical execution and must not be described as deterministic/no-agent today.

Scope boundary: archived family/sentinel task IDs remain readable; historical records are not
rewritten. The post-survivor index/evidence tooling now accepts direct PASS bundles by
family/round/run ownership (no card ids; a leaked card id fails closed) while historical
card-owned families keep the strict `kanban_task_id` checks. This direct/card-free provenance boundary is covered by the later v2.0 independent auditor run 222 PASS and the v1.10 post-survivor audit evidence; it is not a current pending gate.

狀態：Full Canvas 的既有 live 狀態維持；本次 backlog-preparation migration 尚未經獨立 audit。C3 production handoff 仍為 **LIVE**；2026-09-30 起 Research Intake 將 exact reviewed candidate append 至 preparation backlog，由 no-agent host preparation cron `219d541661d5` 執行 preparation/validation/promotion；C3 只消費已準備好的 production-pool entry，既有 cadence 不變。
Full Canvas implementation commits `46093ba`、`ca13752` 與 remediation `b29612f` 已完成同一卡 `t_fd62293f` 的獨立 auditor re-audit PASS，並已由 canonical `main` live import／publish。stable workflow `shadowQuantCp1` 保留原本 source/metrics refresh lane，新增 derived lifecycle view、Current Stage Router、Pipeline Counts Summary 與 17 個 lifecycle indicators；不改 Hermes default、Kanban、Qlib、`production_handoff.py`、既有 live C3 ownership 或任何 production mutation path。C3.1 commits `9832b07`＋`fc44eb0` 已由 auditor run 445 PASS，並完成 live import／publish；2026-09-23 23:05 Asia/Taipei C3 execution 112 `mode=trigger`／`status=success`，下一分鐘 C4 execution 113 亦 `success`。

C3 handoff workflow `productionHandoffManualC2` 維持 `:05/:20/:35/:50` cadence 並已 live；no-agent host preparation cron `219d541661d5` 使用 `:00/:15/:30/:45`，與 C3 錯開五分鐘。Hermes handoff cron `624d0be5b23c` 保持 paused，僅作明確 rollback。C3 僅對已 host-prepared candidate 執行 direct Qlib dispatch；preparation 不在 C3 hot path。n8n workflow topology/cadence 未變。

`shadow-1` 仍不做任何 pipeline mutation：不改候選、不改 Kanban、不改 leaderboard、不改 `/Volumes/ExpansionDrive/qlib-results`、
不碰 private survivor repo、不 push GitHub、不動任何 cron。Shadow 的控制能力（launch / retry / resume / reorder / promote）仍未實作。

## C3 current state（production handoff）

- **Live C3: ACTIVE.** n8n workflow `productionHandoffManualC2` 維持既有 cadence；repo export 的 `active=false` 只代表 export 檔本身不作 deployment source-of-truth，live instance 已 publish/activate。
- normal C3 reads the production pool and accepts only execution-ready candidates. The host preparation runner consumes the FIFO backlog outside C3, stages/validates the package and performs host-only promotion into `candidates.json`; its bounded agent cannot write either file. C3 validates the promoted immutable package, executes P1–P10, then starts Qlib through the fixed container command. C3 never starts `quant-preparation`, and Intake does not build runners, run P1–P10/Qlib, or launch preparation.
- Container rc 非零／timeout 後 canonical attempt 已存在，C3 不盲目重跑；既有 C3/C4 artifact guard 與 C4 exception/remediation 負責後續處置。`family.json.handoff.execution=direct_hermes` 僅為 P10/C4 沿用的 direct-family compatibility token，不表示 prepared C3 啟動 Hermes。
- C4 保持 runtime reconciliation **與 family-level LLM research/disposition** 職責。這個 `quant-production` session 是刻意的 family boundary：同一 family 的 full-backtest／failure analysis／必要 technical retry／round verdict 完成後，C3 才可放下一個 candidate；它不是 C3 的 mechanical production executor。2026-09-29 disposition v2 只修 prepared/direct authority、local-universe precedence 與 immutable-round remediation，不新增 n8n node／queue／service，也不改 C3 cadence。

## C4 current state（runtime reconciler cadence）

- **AUDITED PASS / LIVE**：stable workflow `runtimeReconcilerC4`（`Quant Control Plane — Runtime Reconciler`）的 repo export 維持 `active=false`，live deployment 以 Manual Trigger、Schedule Trigger 與單一 `runtime_reconcile_once` host-bridge action 運作；目前 live cadence 為 `6,21,36,51 * * * *`，沿用既有 container timezone（Asia/Taipei）。C4 automatic trigger 已於 2026-09-23 22:06 Asia/Taipei 成功觀測（execution 103、mode `trigger`、status `success`），前一拍為 C3 execution 102（22:05）。
- bridge 仍只讀同一固定 request path `/Users/hong/workspace/n8n/files/control/production_handoff.request.json`、寫同一固定 response path `production_handoff.response.json`；新增的第二個固定 action 只映射到既有 `~/.hermes/scripts/quant_runtime_reconcile.py`，request schema、atomic claim／response、correlation、bounded timeout 與最小環境不變。不新增 mailbox、queue、DB、service、daemon、retry queue 或 runtime state machine。
- C4 live import／publish 與 automatic trigger 驗證已完成；compute-finished direct family 由 C4 以 lease-protected Hermes `quant-production` 做 family research/disposition。prepared/direct family 不要求 legacy `agent-task.md`；authority 依 current contract → prepared identity/specs → hashed candidate/research → attempt evidence。若錯誤 exact-source prerequisite 已凍進 immutable round，C4 保持同一 family，先啟動 corrected next round 並確認 runtime state，再收舊 round，避免 C3 race。Hermes reconciler cron `f6b9aa5e9034` 保持 **paused**，只作 rollback path；watchdog `c5314d86cdfe` 保持 active 且獨立。
- watchdog `c5314d86cdfe` 保持 active 且獨立；Full Canvas `shadowQuantCp1` 保持 read-only，production control 仍在獨立 production workflows，C4 不接入 shadow／Full Canvas mutating path。

既有 C3 handoff workflow 沒有 webhook、AI node、credentials、host path 或 request 內任意 action；它只保留 C2 已稽核的固定 bridge request/response semantics。

---

## Full Canvas Completion current state（AUDITED PASS / LIVE）

`shadowQuantCp1` 的 repo source 現在以**同一張 canvas**分成兩個語意不同的區域：

- **SOURCE / METRICS REFRESH**：沿用既有 read-only source chain；repo 排程已由 15 分鐘改為 5 分鐘。這一列節點變綠只代表「來源成功讀取」，**不代表 lifecycle 正在該站執行**。
- **CURRENT LIFECYCLE**：由已組好的 shadow snapshot 做 deterministic derived view，再經 `Current Stage Router` 每拍只送往 **1 個** lifecycle indicator；若 current-family evidence 無法安全定位，就只亮 `Attention / Unresolved`，不得猜測。

Lifecycle canvas 固定呈現：Strategy Research → GitHub Pool → Intake → Wiki Brain → Candidate Queue → WAITING_DATA / READY_TO_RESUME Parking → Data / Preflight → Qlib Full Backtest（symbols × timeframes × parameter domain × DCA）→ Historical/OOS → Robustness → Failure Analysis / Result Validation → Result/Verdict → REJECT 或 Survivor/PASS → Leaderboard → Private Survivor Repo，另有 `Attention / Unresolved`。

`Historical/OOS`、`Robustness` 等細站**只是既定 lifecycle 的可視節點，不是新增 runtime state**；目前 authoritative runtime 沒提供可安全細分時，router 不會把粗粒度 `RUNNING_QLIB` 假裝成其中任一細站。`WAITING_DATA` / `READY_TO_RESUME` 也只有在 snapshot 真正帶 exact token 時才會亮，現階段不可得就維持 unavailable。

動態 counts 不透過 workflow self-mutation 寫進 node 名稱。既有 snapshot counts 由 `Pipeline Counts Summary (derived, read-only)` 集中輸出；current indicator 同時收到 current family/card/stage/progress/provenance。這保留 n8n native canvas 的可讀性，也避免另造 registry 或 workflow rewriter。

Full Canvas 已完成 live import／publish 與安全重啟驗證：published `shadowQuantCp1` 為 `Quant Control Plane — End-to-End`、active、32 nodes／17 lifecycle indicators，live `nodes + positions + parameters + connections + settings` 與 canonical repo 逐鍵語意相等。一次真實手動 execution 成功，canonical snapshot 寫出成功，且依當下 current-family 的 blocked／not-launched／gate-unmatched 證據只路由至 `Lifecycle 17 — Attention / Unresolved`；`shadow_check.py --require-fresh` PASS。production workflow `productionHandoffManualC2` 的 deploy 前後 export hash 完全相同，未受本輪變更影響。

2026-09-25 live cutover 已完成：`824aecb` fast-forward 到 `main` 後完成 canonical workflow import／publish 與 stopped-only SQLite backup＋WAL checkpoint／integrity check 的 safe restart；healthz=`ok`、readiness=200、三個 production workflows 皆 active。CatDesk browser 直接觀測 End-to-End 在 **00:55:25／01:00:25／01:05:25 Asia/Taipei** 連續三個 5 分鐘自動拍點皆 `Success`；live export 讀回 `Schedule — 5m observation`／`minutesInterval=5`，故 ≤5 分鐘展示 freshness 已由 live evidence 驗證。

---

## 1. 這個 shadow 是 / 不是什麼

| 是 | 不是 |
|---|---|
| 現有 pipeline 的**唯讀投影**：把既有狀態（Hermes Scout cron state、dashboard projection、canonical intake state、prerequisite-gate evidence、parking mirror metadata）集中成一份 machine-readable 快照 | 第二套 pipeline／第二個 backtester／新的狀態儲存 |
| 階段計數的**對帳面**：讓 operator 一眼看出哪個階段有邏輯缺口 | 新的 gate：快照不參與任何 PASS/REJECT 判定 |
| 未來控制面的**前身**：拓撲與 vocabulary 已固定，控制節點尚未接上 | 控制面本體：沒有任何 mutating node、沒有 credentials |
| 沿用既有真值：`runtime/` 語意、`candidate_snapshot.py` 投影、intake state 皆**不重算、不改寫** | 真相來源：真值仍在原本的位置，快照只是投影 |

快照 artifact（host 路徑）：`/Users/hong/workspace/n8n/files/quant-control-plane-shadow.json`
（容器內 `/home/node/.n8n-files/quant-control-plane-shadow.json`，`schema = quant-control-plane-shadow/v1`）。

## 1.A End-to-end canvas（`shadowQuantCp1`）

`shadowQuantCp1` 是唯一的 Shadow workflow，stable ID 不變；display name 現為 **Quant Control Plane — End-to-End**。它把同一份已組裝的 `quant-control-plane-shadow/v1` 快照分成兩條清楚的視覺語意：

- **SOURCE / METRICS REFRESH**：Manual + 5m observation、6 個既有唯讀來源、assembler 與唯一 shadow snapshot write。這條線顯示綠色，只代表來源讀取／快照寫出成功，**不代表 lifecycle 活動**。
- **CURRENT LIFECYCLE**：`Build lifecycle view (derived, read-only)` 只從 assembler 輸入建立衍生 view；`Current Stage Router` 依 **runtime observation 的 lifecycle state**（`preflight`／`qlib_active`／`disposition`／`terminal`／`idle`；Phase 2，§9.G）與其逐字 token，只把本次 refresh 送到**一個** stage indicator；canonical `production_handoff.unresolved_incidents` 若有未結案事件，則展示優先導向既有 `Attention / Unresolved`，不覆寫 `current.state`；prerequisite-gate record 只作**佐證**（agree／disagree 都顯示，**不路由**），另有 `Pipeline Counts Summary (derived, read-only)`。它不讀新來源、不寫狀態、不啟動／重排／續跑任何 runtime，**也不讀任何 Kanban 卡片狀態**（`current` 不因卡片狀態改變）。

Lifecycle indicator 的固定順序為：
`Strategy Research / Hermes Scout` → `GitHub Strategy Pool` → `Intake Review` → `Wiki Brain` → `Candidate Queue` → `WAITING_DATA / READY_TO_RESUME Parking` → `Data Readiness / Preflight` → `Qlib Full Backtest (symbols x timeframes x parameter domain x DCA)` → `Historical / OOS` → `Robustness` → `Failure Analysis / Result Validation` → `Result / Verdict` → `REJECT`／`Survivor / PASS` → `Leaderboard` → `Private Survivor Repo Parking`，另有 `Attention / Unresolved`。`REJECT` 與 `Survivor / PASS` 是展示分支；`Leaderboard` → `Private Survivor Repo Parking` 是 promoted display path，不是第二套執行狀態機。

`Pipeline Counts Summary` 沿用 snapshot 既有 counts；WAITING_DATA、READY_TO_RESUME 與 backtest-layer REJECT 沒有 authoritative 值時維持 `null`／reason，不從 leaderboard、intake 或其他全域數字推導。**Phase 2 起** runtime 相關欄位（`current_*`、families registered／backtested／in-flight／unresolved、cumulative workload、survivors／leaderboard、candidate pool total／consumed／queued、last pipeline advance）改由 on-demand runtime observation 提供（§9.G）；Pool／Intake／Wiki／Parking 仍走各自既有來源。n8n 原生 canvas 不會在每次 refresh 自動改寫 node name／Sticky Note 顯示動態數字，因此即時 counts 留在 node output 與專用 summary node；workflow 不做 self-mutation。

## 2. Topology：pipeline 階段 ↔ workflow 節點 ↔ 來源

單一 workflow（stable ID `shadowQuantCp1`），兩顆 trigger（manual validation + 排程觀測），6 個 read-only 來源節點，1 個 assembler Code node，1 個 derived lifecycle builder，1 個 current-stage Switch router，1 個 counts summary，1 個 shadow 輸出節點，17 個 lifecycle indicators，及 2 個 lane-label Sticky Notes。source lane 與 lifecycle lane 共用同一份 snapshot，不各自建立 state。

```
Manual Trigger ─┐
                ├─→ Scout cron ─→ Pool ─→ Intake Review ─→ Preflight Gate ─→ Runtime Observation ─→ Parking ─→ Assemble ─┬→ Build lifecycle view ─→ Counts Summary
Schedule (5m)   ┘                                                                                                               ├→ Current Stage Router ─→ exactly one indicator
                                                                                                                                  └→ Emit snapshot
```

| # | workflow 節點（節點名即拓撲名） | 讀什麼（唯讀） | 對應 pipeline 階段 |
|---|---|---|---|
| 1 | `Strategy Research — Hermes Scout cron state (read-only)` | `/host/hermes-cron-ro/jobs.json`（＝ `~/.hermes/cron/jobs.json` 的 **ro** 掛載）：只投影 job `f5c0648122f3` 的**契約白名單**（10 個固定鍵：job_id／name／enabled／state／schedule_display／last_run_at／last_status／last_error／failure_streak／next_run_at，一律存在、來源無值時為 `null`）＋ optional `last_dispatch`（只在來源真有 dispatch 記錄時出現，且只含 scheduled_at／dispatched_at／lateness_seconds／kind 四個子鍵） | Strategy Research → Hermes cron `f5c0648122f3`（`Quant Research Scout`，上游研究產生者） |
| 2 | `Pool — alpha-strategy-research pool (read-only)` | `/host/workspace-ro/alpha-strategy-research` 的 root `*.md`（canonical 規則：`len(parts)==1 and suffix==".md" and not startswith("README")`，即 `review_state.py:111`）＋ checkout HEAD sha | Strategy Research → GitHub alpha-strategy-research pool |
| 3 | `Intake Review — canonical intake state (read-only)` | `/host/workspace-ro/alpha-strategy-review-state.json`（current_snapshot buckets、pending_ingestion、deferred_delta、ingested_wiki_records、last_reviewed_*） | Intake Review → Wiki Brain |
| 4 | `Preflight Gate — prerequisite evidence (read-only)` | `<repo>/evidence/*-prerequisite-gate-*.json`：投影**每個 family 最新一筆**（family／round／conclusion／verdict／sha256／bytes），再由 assembler 取用**observation 指名的那個 family** 的 record；沒有就 `null`＋`gaps`，**不臆測**（不再需要投影檔提供 family 提示） | Data / Preflight Gate |
| 5 | `Runtime Observation — canonical runtime evidence (read-only)` | **不再讀任何投影檔**：對既有 host bridge 發一次固定唯讀 action `runtime_observe_once`（request／response 皆固定路徑，`request_id` 對帳），取回 `runtime/runtime_observation.py` 對 canonical `/Volumes/ExpansionDrive/qlib-results` 的**當下**投影：`current`（lifecycle state＋family／round／attempt／stage／progress／cohort／verdict）、runtime counts、candidate pool、leaderboard、watchdog health 與 canonical unresolved incidents | Candidate Queue → Qlib Full Backtest → Result/Verdict → Survivor → Leaderboard |
| 6 | `Parking — private survivor repo metadata (read-only)` | `/host/workspace-ro/validated-survivor-research`：`survivors/` 目錄數、`leaderboard/leaderboard.json` 的 count／metadata／sha256、mirror HEAD sha（**只有 metadata，不讀 survivor 內容**） | Private Repo Parking |
| 7 | `Assemble shadow snapshot (read-only)` | 以上 6 個來源的 stdout（純解析；Code node 無 fs／無網路） | 全鏈 |
| 8 | `Emit snapshot (n8n shadow dir only)` | 寫入唯一輸出路徑（§6） | 觀測輸出 |

節點實作要點：來源節點 1／2／3／4／6 為 `executeCommand`（只做 `readdirSync`／`readFileSync`／`SHA-256` 投影，**唯讀、deterministic**），
節點 5 亦為 `executeCommand`，但只**發布**一個固定 request 並**讀回**對應 response（不改任何 host 檔案；`action`／路徑皆為硬編碼常數，request 內只有 `request_id`）。沒有 node 會執行 pipeline 腳本、Qlib、backtest 或任何寫入 host 狀態的指令。
節點 1 的讀取面只有 `jobs.json` 一個檔案；repo 版對 5 分鐘拍點做一次投影，讀不到時輸出 `available: false` ＋ 理由（**不會**讓 execution 失敗，也不以預設值代替）。
節點 1 投影出的欄位就是 §3 的 stage 1 白名單本身；來源可讀性與檔案時間戳留在 `sources[]`（`hermes_scout_cron_state`），**不進** stage 1。

## 3. State vocabulary（SHADOW 顯示語意）

Vocabulary 只**顯示**，不驅動任何動作。可驗證來源者以來源 token 逐字呈現（`state_provenance = verbatim…`）；
需要推論者標明 `shadow mapping`；不可得者一律 `null` ＋ 進 `gaps`，**不以 0 或預設值代替**。

| token | 語意 | shadow-1 是否可得 |
|---|---|---|
| `WAITING_DATA` | candidate prerequisite/data 缺失但可能可恢復 | 需重跑 family prerequisite check（canonical raw 未掛載）→ **不可得**，列 gap |
| `READY_TO_RESUME` | 先前 waiting 的 candidate 現在 prerequisite 已滿足 | 同上 → **不可得**（`resume_policy.ready_to_resume.value = null`） |
| `BLOCKED` | 真正異常、需要介入（**不是**「資料缺失」的同義詞） | 由 watchdog `attention` ← 現行投影顯示為 `attention` + 1 筆 active incident（原始 incident signature 一併顯示） |
| `TECHNICAL_INCOMPLETE` | terminal：客觀 prerequisite 無法滿足，不自動續跑 | **可得**：gate record 逐字 token（目前 `PREREQUISITE_MISSING / TECHNICAL_INCOMPLETE; no backtest launch`） |
| `REJECT` | terminal 科學／策略否決 | intake reject 計數（30）為終局常態，非錯誤；backtest 層 REJECT 不在投影中 → 該層為 null |
| `PASS` | promoted survivor | 由 leaderboard projection count 推得（contract §28.1：leaderboard entry 即 promotion 觸發點），標示為 shadow mapping |
| `RUNNING_QLIB` / `ARTIFACT_READY` / `FAILED_SCRIPT` | 既有 runtime stage，照實顯示 | 直接取投影 `current.stage` 逐字（落在這三者之一才給 token，否則 `null`） |

**Phase 2：`current` 的 lifecycle state（runtime observation，§9.G）。** 這五個值由 canonical runtime artifacts 判定，**沒有任何一個來自 Kanban**：

| state | 判定依據（canonical artifacts） | canvas 顯示的 current stage |
|---|---|---|
| `preflight` | canonical family 已註冊且在 90 分鐘 launch grace 內、尚無 attempt 目錄；**或** attempt 目錄已存在但 Qlib 尚未發佈 `state.json`。這描述 prepared-direct dispatch 的 canonical materialization／Qlib startup transition；backlog preparation 由 C3 之外的 no-agent host cron 執行，發生在 `_handoff/preparing/<family_id>/`、早於 canonical family 註冊，因此不屬於此 runtime state。 | `Data Readiness / Preflight` |
| `qlib_active` | 最新 attempt 在 90 分鐘窗內、無 terminal sentinel、且已發佈 runtime stage | stage 為 `RUNNING_QLIB` → `Qlib Full Backtest`；其他 stage token 不臆測 → `Attention / Unresolved` |
| `disposition` | 最新 attempt 已發佈 terminal sentinel，但**該 attempt 自己所屬 round** 尚無 verdict | `ARTIFACT_READY` → `Result / Verdict`；`FAILED_SCRIPT` → `Failure Analysis / Result Validation` |
| `terminal` | 該 round／family 的 terminal verdict 已發佈且是最新證據（90 分鐘窗內） | `PASS` → `Survivor / PASS`；`REJECT` → `REJECT`；其他 terminal token → `Result / Verdict` |
| `idle` | 無任何 family 持有 live attempt，且最新 terminal 證據在窗外 | `Candidate Queue`（不是 attention alarm） |

state 不可證明時 `current.state = null`＋`gaps`，canvas 走 `Attention / Unresolved`（不猜）。

Intake 分支語意（顯示用，不重判）：`PASS` + 真正完成 ingest 的 `PASS-WITH-CAVEAT` → Wiki Brain（＋ sibling candidate append）；
`REJECT` → 正常篩選終局；`REMEDIATE` → 尚未接受、**不是**系統錯誤；`Error` → 只保留給真正的系統／讀取／解析失敗，**不得**與 reject／remediate 混用。

Stage 1 `strategy_research` 不是 vocabulary token，而是 Hermes cron job `f5c0648122f3` 的**逐字欄位投影**（§2 節點 1）：
`shadow_state` 維持 `null`，observation **只**含契約白名單——10 個固定鍵（job_id／name／enabled／state／schedule_display／last_run_at／last_status／last_error／failure_streak／next_run_at）
一律存在（來源無值時為 `null`），`last_dispatch` 為 **optional**（來源真有 dispatch 記錄時才出現，且只含 scheduled_at／dispatched_at／lateness_seconds／kind）；
來源可讀性／檔案時間戳／說明等診斷 metadata 一律放 `sources[]`（`hermes_scout_cron_state`），**不進** stage 1。
來源不可讀或 job 不存在時 observation 為 `null` 並記入 `gaps`（`field = strategy_research_scout_cron`）、**不以預設值或推論值代替**；`shadow_check.py` 同時驗「欄位齊全」「沒有白名單外鍵」「`last_dispatch` 有值即完整」三件事（§9.F F4）。

---

## 4. Counts 與 reconciliation（快照的對帳面）

快照 `counts`（2026-09-21T14:36Z 實跑，逐項可回溯到 `sources[]` 的 path／sha256）：

> **Phase 2（§9.G）後的來源變更**：下表 `wiki_reviewed`／`wiki_ingested`、`families_*`、`workload_evaluations`、`leaderboard_*`、`current_*`、`runtime_health_*` 改由 **on-demand runtime observation** 提供（同一組 `candidate_snapshot` helper，數值語意不變；`current_*` 欄位改為 `current_state`／`current_stage`／`current_round_id`／`current_attempt`／`current_verdict`／`current_why`，**不再有** `current_card_status`／`current_kanban_task_id`）。下表逐字保留 2026-09-21 當次實跑值作為歷史證據。

| 欄位 | 值 | 來源 |
|---|---|---|
| `pool_records_total` / `pool_root_md_total` | 830 / 832 | pool checkout（canonical 規則剔除 `README*`；HEAD `4b606674…`） |
| `intake_pass` / `pass_with_caveat` / `remediate` / `reject` | 42 / 285 / 191 / 30 | canonical intake state（sha256 `4363e3b6…`） |
| `intake_pending_ingestion` / `intake_deferred_delta` | 0 / 277 | 同上 |
| `intake_ingested_wiki_records` | 327 | 同上 |
| `wiki_reviewed` / `wiki_ingested` | 548 / 327 | dashboard projection |
| `families_registered` / `families_backtested` | 47 / 17 | dashboard projection（`family.json` 去重後） |
| `workload_evaluations` | 2,371,832 | dashboard projection（cumulative grid rows，386 artifacts） |
| `leaderboard_count` / `leaderboard_shown` | 29 / 10 | dashboard projection（Top-10 逐字，不重排） |
| `current_family_id` / `current_stage` / `current_card_status` | `crypto-bitcoin-cvar-risk-aware-q-learning-adaptive-controller-2026-09-02` / `not launched` / `blocked` | dashboard projection（card read-back `t_39351c26`） |
| `runtime_health_status` / `runtime_health_active_count` | `attention` / 1 | dashboard projection（watchdog；incident `terminal_pending` / `FAILED_SCRIPT`，first seen 2026-09-21T01:06:41Z） |
| `gate_record_count` / `gate_record_matched_current_family` | 29 / true | repo `evidence/`（gate record sha256 `7d92061c…`，85,841 bytes） |
| `parking_survivor_dirs` / `parking_survivor_count` / `parking_top10_count` | 24 / 24 / 10 | parking mirror（leaderboard.json sha256 `96f5b15f…`；mirror HEAD `30b6f8b9…`；generated 2026-09-20T10:03:35Z） |

`reconciliation[]` 每次執行都輸出，`ok` 為 `true` / `false` / `null`（不可判定時**不假裝**判定）：

1. `intake buckets total (548) vs wiki reviewed (548)` → **ok**（canonical state 與投影一致）
2. `ingested_wiki_records (327) vs dashboard wiki ingested (327)` → **ok**
3. `parking survivor dirs (24) vs parking survivor_count (24)` → **ok**
4. `canonical leaderboard count (29) vs parking survivor_count (24)` → **false**：這是**呈現出來的缺口**，不是被修掉的缺口；
   parking mirror 是另一次生成的子集（見其 `generated_at_utc` 與 `mirror_head_sha`），本卡不調和、不重生成。
5. `pool records (830) vs intake buckets + deferred_delta (825)` → `ok = null`（informational）：pool checkout HEAD 領先 last reviewed snapshot，差異屬預期，**不是**錯誤。
6. `current family gate record matched (true)` → **ok**；無對應 gate record 時為 false，且**不會**捏造 gate 狀態。

不可得者一律 `null` ＋ 記入 `gaps`（目前：candidate pool file 未讀、results root 未掛載、Kanban 僅讀投影 read-back 欄位）。

註：本表是 **2026-09-21T14:36Z 當次實跑**的逐項值；快照是 live 投影，來源變動即反映——
15:45Z 拍點已見 `pool_records_total` / `pool_root_md_total` = **831 / 833**（checkout HEAD `2a34d59`），
差異來自 pipeline 自身的 `Quant Research Scout` cron（job `f5c0648122f3`，15:22Z 新增 1 筆 root `.md`），**不是** shadow 寫入。
2026-09-22 起 stage 1 直接逐字顯示同一個 job 的 live 欄位（本輪新增的唯一來源）；該輪的 count 讀值與逐項證據見 §9.E；欄位集合已於 §9.F 收緊為白名單（見 §3）。

## 5. Future resume policy（**已文件化，未啟用**）

> 若 family A 為 `WAITING_DATA`，後續 candidate 可以繼續跑。當 A 變成 `READY_TO_RESUME` 時，**不要**打斷正在跑的 family；
> 等目前 family 跑完後，`READY_TO_RESUME` 優先於尚未動過的 queue candidates。

Shadow-1 的行為：**只顯示、不執行**（`resume_policy.enabled = false`；`shadow_behaviour = visualise only`）。
`READY_TO_RESUME` 需要重跑該 family 的 prerequisite check 對 canonical raw（`/Volumes/ExpansionDrive/market-data-raw`，本卡刻意不掛載、不執行），
故 `ready_to_resume.value = null` 並附理由；本 workflow 沒有任何節點具備 reorder／delay／interrupt／resume 能力。

## 6. Trust boundaries

**現行掛載（2026-09-27 storage cutover 後）：**

| source | container path | mode | 用途 |
|---|---|---|---|
| Apple Container named volume `n8n-data`（5 GiB、ext4） | `/home/node/.n8n` | rw | n8n 自身持久資料（DB／config／storage）；SQLite 不再放在 host virtiofs bind mount |
| `/Users/hong/workspace/n8n/files` | `/home/node/.n8n-files` | rw（**新增**） | 唯一輸出：shadow snapshot（n8n 自身檔案區，非任何 pipeline 狀態） |
| `/Users/hong/quant-dashboard/data` | `/host/quant-dashboard-data` | **ro** | dashboard projection |
| `/Users/hong/workspace` | `/host/workspace-ro` | **ro** | intake state、pool checkout、repo `evidence/`、parking mirror |
| `/Users/hong/.hermes/cron` | `/host/hermes-cron-ro` | **ro** | Hermes cron state：只讀 `jobs.json`，且只投影 job `f5c0648122f3` 的固定欄位（prompt／`executions.db`／`output/`／`usage_audit.jsonl` 皆不讀、不進快照） |

其他 host 狀態（`/Volumes/ExpansionDrive/qlib-results`、`market-data-raw`、Kanban DB、private repo 的寫入面、GitHub）**完全沒有掛載**；
cron 只有 `~/.hermes/cron` 以 **ro** 掛入——Apple `container` 不支援單檔 bind mount（實測 `Error: path '…/jobs.json' is not a directory`），
故以「最小目錄」為掛載單位，而節點只讀其中一個檔案、只投影其中一個 job 的固定欄位。
所以 shadow workflow 在結構上**不可能**寫到那些地方。唯讀性另有實測（§9）：`touch /host/...` → `Read-only file system`。

**Phase 2（§9.G）沒有新增任何掛載。** runtime 真值改由**既有** host bridge 的一個固定唯讀 action 取得（§7.3）：容器內仍然沒有 `/Volumes/ExpansionDrive/*`（results root 未掛載），observation 在 **host 端**讀 canonical artifacts 後只回傳投影 JSON（一次約 7 KB）。因此上表與「結構上不可能寫到那些地方」的結論完全不變。

**n8n 設定變更（唯一一項，最小化）：** `NODES_EXCLUDE=["n8n-nodes-base.localFileTrigger"]`。
理由：n8n 2.x 預設停用 `executeCommand` 與 `localFileTrigger`；本 workflow 的 read-only 來源節點需要 `executeCommand` 做目錄列舉／投影，
故以最小集合覆寫預設值——**只**重新啟用 `executeCommand`，`localFileTrigger` 維持停用（實測 `export:nodes` 不含它）。
副作用與界線：`executeCommand` 只能在容器內執行 shell，而容器對 host 的掛載僅有一個 rw 目標（n8n 自身檔案區）；
`Read/Write Files from Disk` 節點另受 n8n 自身限制，只能存取 `~/.n8n-files`（實測：寫到 `/home/node/shadow/...` 被 n8n 拒絕）。回滾見 §7。
**沒有任何 credentials／token 進入 workflow 或 repo**。

---

## 7. n8n runtime contract 與 rollback

容器（Apple Container，image `docker.io/n8nio/n8n:latest` arm64，n8n `2.39.9`）：
host port `127.0.0.1:5678`、持久資料為 named volume `n8n-data`（5 GiB、ext4）掛載至 `/home/node/.n8n`、4 CPU／1 GiB、user `node`、`TZ=Asia/Taipei`。
2026-09-27 storage cutover 僅把 n8n 自身持久資料由 host virtiofs bind mount 移入 named volume；`image`／`cpus`／`mem`／`env`／`args`／`user`／`workdir`／`ports`／`networks` 與其餘 host bridge mounts 均維持原設定。

重建指令（等同當前狀態；`<...>` 內為 §6 的 mount 四行）：

```bash
container run -d --name n8n -c 4 -m 1024M -u node \
  -p 127.0.0.1:5678:5678 \
  -e TZ=Asia/Taipei -e GENERIC_TIMEZONE=Asia/Taipei -e N8N_LISTEN_ADDRESS=0.0.0.0 \
  -e N8N_RELEASE_TYPE=stable -e NPM_CONFIG_UPDATE_NOTIFIER=false -e NODE_PATH=/usr/local/lib/node_modules \
  -e 'NODES_EXCLUDE=["n8n-nodes-base.localFileTrigger"]' \
  --mount type=volume,source=n8n-data,target=/home/node/.n8n \
  --mount type=bind,source=/Users/hong/workspace/n8n/files,target=/home/node/.n8n-files \
  --mount type=bind,source=/Users/hong/quant-dashboard/data,target=/host/quant-dashboard-data,readonly \
  --mount type=bind,source=/Users/hong/workspace,target=/host/workspace-ro,readonly \
  --mount type=bind,source=/Users/hong/.hermes/cron,target=/host/hermes-cron-ro,readonly \
  docker.io/n8nio/n8n:latest
```

匯入／執行（workflow 檔經 §6 的唯讀掛載 `/host/workspace-ro` 讀入；`N8N_RUNNERS_BROKER_PORT` 只為讓 CLI 與執行中的 server 並存）：

```bash
container exec n8n n8n import:workflow --input=/host/workspace-ro/quant-runtime-pipeline-n8n/n8n/quant-control-plane-shadow.workflow.json   # 匯入會把 workflow 設為未啟用
container exec n8n n8n update:workflow --id=shadowQuantCp1 --active=true   # 啟用（CLI 明示需重啟才生效 → 見 §7.1）
container exec n8n n8n list:workflow --active=true                     # 讀回
container exec n8n sh -c 'N8N_RUNNERS_BROKER_PORT=5699 n8n execute --id=shadowQuantCp1'   # 手動驗證執行
python3 n8n/shadow_check.py                                            # 驗證快照（見 §9）
```

`n8n/quant-control-plane-shadow.workflow.json` 的 `active` 欄位與**部署狀態一致**（`true`：排程開啟；控制面 mutation 仍為關閉，見 §3／§10）。
`import:workflow` 會把 workflow 設為未啟用，所以匯入後必須重新啟用**並重啟**（§7.1），否則排程不會跑。

### 7.1 安全重啟程序（2026-09-21 實測事故與修復；**必須遵守**）

**事故**：以預設寬限重啟 n8n 之後，DB 層持續失敗——`SQLITE_IOERR: disk I/O error`、`SQLITE_CORRUPT: database disk image is malformed`、
`Failed to query executions parked on a sub-execution`，以及 1 Hz 重試的 `Failed to hard-delete executions`（×388）；
排程拍點觸發了但 execution 無法落地，快照因此停滯。實測根因鏈：

1. n8n 2.39.9 的 shutdown 會卡住：`Waiting for 2 active executions to finish...` → `Shutdown timed out after 30 seconds` →
   `Start.exitWithCrash` 結束行程，**SQLite 連線從未正常關閉**。（`container stop` 預設 `-t 5`，連 n8n 自己的 30 秒自救視窗都不到。）
2. 因此 WAL 沒有 checkpoint、`-shm` 以死行程狀態留在 virtiofs 綁定上（停止後仍見 `database.sqlite-wal` 1,355,512 B ＋ `database.sqlite-shm` 32,768 B）。
3. 下一個 n8n 行程對同一顆 DB 的讀寫即持續失敗（拍點寫入失敗、wait-tracker／pruning 讀取失敗）。
4. **磁碟上的位元組沒有損壞**：host 與容器兩側對 `database.sqlite`／`-wal`／`-shm` 的 sha256 **完全相同**，`PRAGMA integrity_check` = `ok`
   （容器內對副本讀亦然），且新行程讀「同一組 WAL＋shm 副本」正常 → 壞的是**未關閉的執行期狀態**，不是檔案本身。
   當時結論是「不乾淨關機 → 髒 WAL／死 shm → 下一個行程持續 I/O 失敗」，且短期壓測未能重現 virtiofs 本身的穩定性問題。

**2026-09-27 後續證據（supersedes 上述 storage 結論）**：在 stopped-state checkpoint／`quick_check`／`integrity_check` 均通過並清除 stale `-wal/-shm` 後，n8n 重新啟動仍幾乎立即復發 `SQLITE_IOERR`；同一容器的 `/home/node/.n8n` 當時為 host **virtiofs** bind mount。故現行 canonical deployment 已把**只有** `/home/node/.n8n` 移至 `n8n-data` named ext4 volume，其餘 host bridge mounts 保持 virtiofs。cutover 後 n8n 持續排程寫入正常、`tick_probe.js` `integrity_check=ok`，且新容器 stdio 無新的 `SQLITE_IOERR`／`SQLITE_CORRUPT`／`SQLITE_NOTADB`。因此不得再把 `/home/node/.n8n` 重建成 host virtiofs bind mount。

**歷史 virtiofs 修復程序（只適用於舊 bind-mount deployment；現行 named-volume deployment 不再直接操作 host SQLite）：**

| # | 指令 | 判讀 |
|---|---|---|
| 1 | `container stop -t 60 n8n` | 給 n8n 完整 graceful 視窗（預設 5s 不夠） |
| 2 | `tail -6 "/Users/hong/Library/Application Support/com.apple.container/containers/n8n/stdio.log"` | 出現 `Shutdown timed out`／`Waiting for N active executions` = 此次停止**不乾淨** |
| 3 | `ls -l /Users/hong/workspace/n8n/data/database.sqlite*` | 仍見 `-wal`／`-shm` = 未 checkpoint |
| 4 | `sqlite3 /Users/hong/workspace/n8n/data/database.sqlite "PRAGMA wal_checkpoint(TRUNCATE); PRAGMA integrity_check;"` | 必須 `ok`；回報 `0\|0\|0` = 已無待 checkpoint 的 frame |
| 5 | `rm -f /Users/hong/workspace/n8n/data/database.sqlite-shm /Users/hong/workspace/n8n/data/database.sqlite-wal` | 丟掉死行程的 shm／空 WAL（**僅能在容器停止時做**） |
| 6 | `container start n8n` → `curl -s http://127.0.0.1:5678/healthz` | `{"status":"ok"}`，開機 log 無 I/O error |

**鐵律：**
- **現行 named-volume deployment**：host 不再直接持有 live SQLite；停止／啟動交由 container lifecycle 管理，`recover_gate.py` 偵測到 named volume 時不再對舊 host DB 做 checkpoint。若未來暫時回到 virtiofs fallback，才套用上表 stopped-state checkpoint／integrity／sidecar 清理規則。
- **容器在跑時不得直接開啟 live DB 做外部診斷**。要讀就先在容器內複製一組 `database.sqlite`／`-wal`／`-shm`，再讀**副本**——repo 內的唯讀探針即為此法：
  `container exec n8n node /host/workspace-ro/quant-runtime-pipeline-n8n/n8n/tick_probe.js`（複製到容器內 `diag/probe/` 後讀副本，永不寫 live 檔）。
- `import:workflow` 之後必須重新啟用並**重啟**：CLI 明示 `Changes will not take effect if n8n is running. Please restart n8n…`。重啟一律走本節步驟 1–6。

**Rollback（任一步都可獨立回退，皆不影響 pipeline）：**

1. 停用觀測：`container exec n8n n8n unpublish:workflow --id=shadowQuantCp1`（或 UI 內把 workflow 關掉）。
2. 移除 workflow：UI 刪除，或重建容器前先 `container exec n8n n8n export:workflow` 備份後再處理；本 repo 只保留匯出檔。
3. 若要回退 shadow/control-plane 顯示功能，只移除對應 workflow／額外 host bridge mounts；**不得**把 `/home/node/.n8n` 回退成 `/Users/hong/workspace/n8n/data` virtiofs bind mount。n8n persistent state 的 canonical storage 維持 `n8n-data` named ext4 volume。
4. 刪除輸出：`/Users/hong/workspace/n8n/files/quant-control-plane-shadow.json`（n8n 自身檔案區，與 pipeline 狀態無關）。

### 7.2 Host 復原 gate：`ai.quant.recover-gate`（2026-09-22 實測）

Phase 2 control cutover 前的最小 host 級重啟／復原：只補「重啟後兩個既有 Apple Container 服務自己回健康」，
**不**新增 daemon／service／DB／cron／n8n workflow，**不**動 container 定義與 mount、Qlib 計算語意、candidate 邏輯、
Shadow workflow、Homepage、Research Scout。

| 檔 | 角色 |
|---|---|
| `runtime/recover_gate.py` | 短命腳本，每次四步後退出：① 確保 Apple Container system running（bounded wait 120 s）→ ② 確保**既有** n8n container running（缺失＝fail closed，**絕不自動建立／重建**）；若 n8n 已 running，完全不碰 SQLite；若 stopped，先判讀 `/home/node/.n8n` mount type：現行 named volume 直接由 container lifecycle 啟動，舊 virtiofs fallback 才執行 stopped-state checkpoint／`quick_check`／`integrity_check`／sidecar cleanup，mount 缺失／模糊／未知一律 fail closed；啟動後驗 `http://127.0.0.1:5678/healthz/readiness` = 200 → ③ **只讀既有 qlib-run 狀態**（`container ls --all`，不開子程序）：system 本就 running 且 qlib-run running → 記 `qlib=already_running`、**不跑 preflight、不做 P1–P8**；只有 **qlib-run stopped** 或 **① 需要復原 container system** 才委派既有 `runtime/preflight.py --recover --json`；qlib-run 缺失即 fail closed 且**絕不委派**、絕不建立 → ④ append 一行 log 後退出（rc 0＝健康、1＝任一 gate 失敗）。健康時為 no-op。 |
| `runtime/ai.quant.recover-gate.plist` | launchd：`RunAtLoad` ＋ `StartInterval 300`（**無常駐程序**）、絕對 PATH、WorkingDirectory 指本 repo；`plutil -lint` 通過後才複製到 `~/Library/LaunchAgents/ai.quant.recover-gate.plist`。 |

- **Log**：`~/quant-dashboard/logs/recover_gate.log`，每次一行 `boot= system= n8n= qlib= rc= problems=`；`qlib=` 的值為 `already_running`（健康 no-op，未跑 preflight）、`preflight:PASS(…)`／`preflight:FAIL(…)`（委派既有 preflight --recover）或 `absent_fail_closed(…)`（fail closed，絕不建立）；超過 1 MiB 整檔截斷（刻意簡化，不做 rotation）。launchd stdout/stderr → `recover_gate.err.log`（平時 0 B）。
- **非重疊**：單一非阻塞 lock 檔，interval 撞上前一輪時只記 `skipped=previous_run_still_active`。
- **路徑注意**：本卡不 push origin（遠端 `origin/main` 動都不動），只把本地 canonical `main` fast-forward 到本 branch；plist 已改指 canonical repo `/Users/hong/workspace/quant-runtime-pipeline`（LaunchAgent 不再依賴暫存 feature worktree）。**殘留依賴（不在本卡範圍）**：n8n container 的 ro mount source 是整個 `/Users/hong/workspace` → `/host/workspace-ro`（兩份 repo 都看得到，並非 worktree 專屬）；真正還寫死 worktree 的是**已匯入 n8n 的 shadow workflow**：`SHADOW_GATE_DIR=/host/workspace-ro/quant-runtime-pipeline-n8n/evidence`，另有 `tick_probe.js` 使用註解與本文件的命令範例。改該路徑＝改 workflow（本卡明文禁止），故 worktree 與 feature branch 在稽核前都要留著。

**驗證（2026-09-22 12:51–13:02 CST 實測；停機前兩次確認無 active Qlib backtest：qlib-run 內只有 PID 1 `sleep infinity`、唯一 `stage=RUNNING_QLIB` 的 `state.json` 是 9/14 已帶 `FAILED` sentinel 的舊 attempt、當日無任何 `run.log` 寫入）：**

1. **受控 stop → RunAtLoad 復原**：`container stop -t 60 n8n` → stdio.log `Received SIGTERM. Shutting down...`／`Deregistered all crons`、`Shutdown timed out` **0** 筆 → host `PRAGMA wal_checkpoint(TRUNCATE)` = `0|0|0`、`PRAGMA integrity_check` = `ok` → **僅在容器停止時** `rm -f` `-wal`/`-shm` → `container stop -t 60 qlib-run`（12:52:52 兩者 `stopped`）→ 複製 plist ＋ `launchctl bootstrap gui/501/…`（RunAtLoad）→ log `04:53:10Z system=running n8n=started qlib_preflight=PASS(rc=0 actions=container_start …)`；讀回 readiness `200`、`/healthz` `{"status":"ok"}`、`list:workflow --active=true` → `shadowQuantCp1`。
2. **container-system-down 復原（實測一次，安全）**：同 §7.1 乾淨停 n8n（`0|0|0`＋`ok`、`rm -wal/-shm`）→ 停 qlib-run → `container system stop`（12:56:03：`apiserver is not running`、`container ls` XPC 失敗）→ `launchctl kickstart gui/501/ai.quant.recover-gate` → **19 秒後** log `04:56:22Z system=started n8n=started qlib_preflight=PASS(rc=0 actions=container_start)`；讀回 `containers.running 2/2`、n8n `startedDate 04:56:12Z`、qlib-run `04:56:20Z`、readiness `200`、重啟後 stdio.log `SQLITE_IOERR|SQLITE_CORRUPT|disk I/O error|malformed|Failed to hard-delete` = **0** 筆。
3. **interval 自動跑 ＋ 健康 no-op**：`StartInterval` 自動觸發 `05:01:23Z system=running n8n=already_running qlib_preflight=PASS(rc=0 actions=noop …) rc=0`；`launchctl print` → `runs = 3`、`last exit code = 0`；`recover_gate.err.log` = 0 B。
4. **recovery 後一個新鮮排程 tick**：exec **66**（`mode=trigger`、`status=success`、`startedAt 2026-09-22 05:00:25.038Z`，前一拍 65 = `04:45:25Z` 即停機前基線）；`tick_probe.js` → `integrity_check = ok`、`max_id = 66`；`python3 n8n/shadow_check.py --require-fresh --max-age-seconds 1800` → **PASS**（age 82 s）。
5. **qlib 執行面**：獨立 `python3 runtime/preflight.py --recover --json` → `overall PASS`（P4/P5/P6 PASS、P8 `/opt/venv/bin/python … version=0.9.7`）。
6. **fail-closed 負向實測**：以不存在的 container 名跑同一支腳本 → `rc=1`、log `n8n=missing_fail_closed(container '__definitely_absent__' not listed)`，前後 `container ls` 皆 `['n8n', 'qlib-run']`（**沒有**被自動建立）。

**本卡健康 no-op 與正典化部署實測（2026-09-22 13:24–13:47 CST，commit `15b3f78`；上列 12:51–13:02 六項是 `qlib_preflight=` 舊欄位、每次都委派 preflight 的舊行為，僅供歷史對照）：**

1. **健康 interval／RunAtLoad 不再跑 P1–P8**：`launchctl bootstrap` 的 RunAtLoad `05:24:34Z system=running n8n=already_running qlib=already_running rc=0 problems=-`；`StartInterval` 自動跑 `05:31:29Z`、`05:36:29Z`、`05:41:30Z` 三行同樣 `qlib=already_running rc=0` 且**不含** `preflight:` 字樣 → `launchctl print` `runs = 4`（觀察時刻）、`last exit code = 0`、`arguments` 與 `working directory` 皆為 canonical `/Users/hong/workspace/quant-runtime-pipeline`、`recover_gate.err.log` = 0 B。
2. **受控 qlib-run stop → 仍委派 canonical preflight**：停機前再確認無 active Qlib backtest（qlib-run 內只有 PID 1 `sleep` 與探針本身、兩個 `stage=RUNNING_QLIB` 的 `state.json` 皆 9/14 舊檔、當日 `run.log` **0** 筆）→ `container stop -t 60 qlib-run`（`05:28:14Z` 讀回 `stopped`）→ gate `05:29:20Z … n8n=already_running qlib=preflight:PASS(rc=0 actions=container_start fail=- failed=-) rc=0 problems=-`、讀回 `running`。
3. **受控 n8n stop → 恢復且不觸發 preflight**：安全停機（`PRAGMA wal_checkpoint(TRUNCATE)` = `0|0|0`、`PRAGMA integrity_check` = `ok`、**僅在停止時** `rm -f` `-wal`/`-shm`）→ gate `05:30:37Z … n8n=started qlib=already_running rc=0 problems=-` → readiness/health `200`、`n8n list:workflow --active=true` → `shadowQuantCp1`、stdio.log `Shutdown timed out` 與 `SQLITE_IOERR|SQLITE_CORRUPT|disk I/O error|malformed|Failed to hard-delete` 各 **0** 筆；恢復後 shadow 仍按 900 s 節拍出新 tick exec **69**（`mode=trigger`、`status=success`、`startedAt 2026-09-22 05:45:25.549Z`，前一拍 68 = `05:30:25Z` 即停機前基線）、`tick_probe.js` → `integrity_check = ok`、`max_id = 69`、`shadow_check.py --require-fresh --max-age-seconds 1800` → **PASS**。
4. **fail-closed 負向（兩個名稱都實測）**：`recover_gate.py __definitely_absent__` → `rc=1`、log `n8n=missing_fail_closed(container '__definitely_absent__' not listed)`；`recover_gate.py n8n __definitely_absent__` → `rc=1`、log `qlib=absent_fail_closed(container '__definitely_absent__' not listed)`；前後 `container ls` 皆 `['n8n', 'qlib-run']`（**沒有**被自動建立，也**沒有**委派 preflight）。
5. **明確呼叫 canonical preflight 仍 PASS**：`python3 runtime/preflight.py --recover --json` → `overall PASS`、P1–P8 全 `PASS`、P9/P10 `NA`、`recovery.actions = []`、`rc = 0`。
6. **部署正典化**：本地 canonical `main` `46e0e90` → `15b3f78` fast-forward（**未 push**：`origin/main` 仍 `46e0e90`、`main…origin/main [ahead 9]`；feature branch 保留）；canonical 既有 4 個 dirty 檔（`runtime/candidate_snapshot.py`、`runtime/tests/test_candidate_snapshot.py`、`runtime/tests/test_reconcile_wrapper.py`、`homepage/custom.js`）的 sha256、`git diff` 內容與 untracked 清單前後逐位元相同（備份與前後快照在 `~/.hermes/cache/scratch/t_58ef1b17-ff-backup/`）；repo 與 installed `ai.quant.recover-gate.plist` byte-identical（sha256 `f554414479e3e8ca02c686d1bc006c727f7a6ee9f7c2a73612979b3412c942bb`、`plutil -lint` OK、檔內無 `quant-runtime-pipeline-n8n` 字樣）。
7. **焦點測試**：`python3 runtime/tests/test_recover_gate.py`（8 checks）、`test_preflight_recover.py`（11）、`test_preflight_p10.py`（16）在 feature worktree 與 canonical 兩邊皆 `OK`（stdlib unittest、不碰 container）。

**Rollback（任一步都可獨立回退）**：`launchctl bootout gui/501/ai.quant.recover-gate` ＋ 刪除
`~/Library/LaunchAgents/ai.quant.recover-gate.plist`（與 repo 內兩檔）；不影響任何容器與 pipeline 狀態。

### 7.3 Host action bridge：`ai.quant.n8n-host-bridge`（Phase 2C1；2026-09-23 實測）

n8n 控制面接管前的**最小 host 動作橋**：一條固定 request 路徑 → **三個固定 allowlist 動作** → 既有 scheduler wrapper（第三個為 Phase 2 的**唯讀** observation，§9.G）。
只有這些固定元件（one script ＋ one plist ＋ focused tests），**不是** queue／service／daemon；C3／C4 workflow 都只透過同一個固定 host bridge，不新增另一套 runtime。

| 檔 | 角色 |
|---|---|
| `runtime/n8n_host_action_bridge.py` | 短命腳本：只讀固定 `…/n8n/files/control/production_handoff.request.json`、只寫同目錄固定 `production_handoff.response.json`（**路徑永不由 request 資料決定**）。claim 用**同目錄 per-PID `os.rename`**（同一 request 的多個 wake 恰一個贏，其餘 ENOENT no-op；per-PID 讓在途 run 的 claim 不會被下一個 request 摺掉）。request schema 只收 `quant-control-action/v1` ＋ `action ∈ {production_handoff_once, runtime_reconcile_once, runtime_observe_once}` ＋非空 bounded `request_id`（≤128 字元、≤4096 bytes、**恰三鍵**；request_id 必須是合法 Unicode scalar）；malformed／non-object／oversize／多餘鍵／錯 schema／未知 action／壞 request_id 全部 fail-closed：寫 rejection response、**絕不**呼叫 host action；非空 directory request 會在 claim 前拒絕，絕不遞迴刪除其內容。三個放行命令皆硬編碼：`[/opt/homebrew/bin/python3, ~/.hermes/scripts/quant_production_handoff.py]`、`[/opt/homebrew/bin/python3, ~/.hermes/scripts/quant_runtime_reconcile.py]` 或 `[/opt/homebrew/bin/python3, ~/.hermes/scripts/quant_runtime_observe.py]`（thin wrappers → 各自 runpy canonical runtime module，不複製邏輯），bounded timeout 600s（獨立 session/process group；timeout 會終止整個 group）、stdout/stderr **per-action cap**（兩個 mutating action 維持已稽核的 4096 bytes；唯讀 observation 64 KiB，因其回傳一份 JSON 文件）、子環境**新造**只含 `HOME`+`PATH`（不繼承、零 Hermes secret／fence var）。response 為 **非權威 read-back**（同目錄 exclusive/nofollow temp + rename 原子寫，固定 response 最終必為 regular file）：`schema/request_id/action/started_at_utc/finished_at_utc/exit_code/status/stdout/stderr`；claim 只在 response 寫出後清掉（寫不出 → 留存當證據）。exit：0＝no-op 或 action rc 0、1＝任何 rejection／action 失敗／timeout（fail closed）。 |
| `runtime/ai.quant.n8n-host-bridge.plist` | launchd：**`WatchPaths` 指向精確 request 檔**、`RunAtLoad=false`、**無 `StartInterval`、無 `KeepAlive`**（事件喚醒、跑完即退）、`/opt/homebrew/bin/python3` 啟動；`plutil -lint` 通過後複製到 `~/Library/LaunchAgents/`（tests 全綠後才安裝）。stdout/stderr → `~/quant-dashboard/logs/n8n_host_action_bridge.err.log`。 |
| `runtime/tests/test_n8n_host_action_bridge.py` | **32 checks**：exact wrapper command（stub runner，真實 wrapper 從未被測試執行）、minimal env、malformed/non-object/oversize/未知 action／錯 schema／多餘鍵／壞 request_id fail-closed、atomic claim（同目錄 per-PID、二度 claim no-op、他 PID orphan 不重讀）、重複 wake 不 double-run、pending request 同次 invocation drain、bounded output、temp+rename 原子性 spy／預置 symlink 防護、固定 response regular-file、request symlink/FIFO/non-regular 防護（非空 directory 在 claim 前拒絕且不遞迴刪除）、深巢 JSON／invalid UTF-8、timeout descendant group termination、request 竊路徑被拒、no-request no-op、action rc≠0／timeout 記錄、plist 契約（WatchPaths 精確路徑／RunAtLoad false／無 StartInterval／無 KeepAlive）。 |

**Live validation（2026-09-23 10:46–10:48 CST；**4 個 invalid request create/delete 週期**，全程零真實 handoff——board 空、有效 action 可能 append，故只用 invalid action 證喚醒）：**

1. **WatchPaths 可靠喚醒**：4 週期（wrong action／malformed JSON／wrong schema／wrong action）各於 **0.7s、0.7s、9.9s、0.49s** 內落地 response；每週期 request 檔被 claim 刪除後**下一個 create 仍可靠喚醒**（第 3 週期 9.9s 為與前一 claim 刪除事件引發的 no-op run 併發時的事件 coalescing，非丟失——settled 後第 4 週期回到 0.49s）；`launchctl print` `runs` 6、全部 rejection `last exit code = 1`（fail-closed）；no-op 無 request 直跑 `rc=0` 且 response 未動；`err.log` = 0 B。
2. **零 mutation（前後逐項同值）**：board 257 卡（archived 160／done 96／running 1）、`candidates.json` sha256 `005c1c5d…` 不變、handoff cron `624d0be5b23c` 仍 **paused**、`container ls` 兩容器與 startedDate 不動、`control/` 最終只剩一個 `production_handoff.response.json`（request／claim／temp 全清）。
3. **測試**：focused 20/20 OK ＋ runtime 全 **25 檔 507/507 OK**（同一 `/opt/homebrew/bin/python3` 3.14.6）＋ `git diff --check` 乾淨。

**Remediation revalidation（commit `b13c53f`）**：focused **27/27 OK**、runtime **514/514 OK**、`plutil -lint` 與 `git diff --check` 均通過；獨立 invalid request `audit-remediation-b13c53f` 經已安裝的 `WatchPaths` 於 **0.277 s** 落地 `rejected_unknown_action`，request／claim／temp 全清，Kanban DB 與 `_handoff/candidates.json` SHA-256 前後不變，launchd 該次 `last exit code = 1`。另直接 no-request 執行 `rc=0` 且 response SHA-256 不變；全程未執行 valid production handoff。

**部署正典化（2026-09-23）**：本卡獨立 auditor PASS 後，tracked plist 與已安裝 LaunchAgent 的 script／WorkingDirectory 已 re-point 到 canonical repo `/Users/hong/workspace/quant-runtime-pipeline`；feature worktree 不再是 production dependency。正典化只替換部署路徑，不改 bridge 邏輯或 trust boundary。

**刻意不做**：無 queue／DB／socket server／HTTP daemon／SSH／credential store／新 cron／n8n DB 存取／request 內任意命令執行；未改 n8n workflow、Qlib、data、Scout、Intake、Homepage、reconciler/watchdog、candidate pool、contract 語意與 immutable artifacts。

**Rollback（任一步都可獨立回退）**：`launchctl bootout gui/501/ai.quant.n8n-host-bridge` ＋ 刪除 `~/Library/LaunchAgents/ai.quant.n8n-host-bridge.plist`（與 repo 內三檔）＋ 可選 `rm /Users/hong/workspace/n8n/files/control/`；不影響任何容器、cron 與 pipeline 狀態。

## 8. 取樣節奏與儲存成本（實測）

本節下表保留 **shadow-1 初始 900s（15 分鐘）** 的儲存成本實測，作為歷史 sizing evidence；**現行 live End-to-End observation 已於 2026-09-25 改為 5 分鐘**。原始實測如下：

| 設定 | 每筆 execution 儲存量（實測） | 每日 exec 數 | 每日 DB 增量 | 14 天保留窗 |
|---|---|---|---|---|
| 60s ＋ `saveDataSuccessExecution: none` | 1,252 B（但**永不 finalize**：`status` 永遠停在 `running`） | 1,440 | — | 執行列表被幽靈 running 洗版 → **不安全** |
| 60s ＋ 全量儲存（未修剪 payload） | 458,606 B | 1,440 | ~640 MB | **不安全** |
| 900s ＋ 全量儲存（payload 已投影修剪）— 2026-09-21 版 | **26,815 B**（exec 12–15 實測；最早一拍 exec 11 為 26,416 B） | 96 | ~2.5 MB | ~35 MB ✔ |
| 同上 ＋ Scout cron 來源（2026-09-22 起） | **28,425 B**（§9.E 實測；同節亦載變更前基線 26,796–26,797 B） | 96 | **~2.7 MB** | **~38 MB** ✔ |
| 同上 ＋ stage 1 白名單收緊（§9.F，2026-09-21T23:47Z 起） | **28,794 B**（排程拍點 exec 51 實測；CLI exec 50 = 28,396 B；同輪基線：排程 exec 49 = 28,823 B、CLI exec 47 = 28,425 B） | 96 | **~2.7 MB** | **~38 MB** ✔ |

三項對策：(a) 來源節點只輸出**投影後**欄位（原本 `cat` 進 payload 的 intake state 210 KB／gate 86 KB／parking 107 KB 不再進入 execution data）；
(b) 開啟成功執行的資料儲存（`all`），使 execution 正確 finalize；
(c) 歷史版本取樣為 **15 分鐘**；現行 live `scheduleTrigger` 為 `rule.interval[0] = {field: "minutes", minutesInterval: 5}`。Runtime truth 已不再依賴 `dashboard.json` 投影，而是每拍透過既有 host bridge 做 on-demand canonical runtime observation；C3/C4 cadence 仍維持各自既有排程，未因觀測頻率變更而改動。
**實測教訓**：本版 n8n（2.39.9）對 `field: "seconds", secondsInterval: 900` 實測仍**每 60 秒**觸發（DB 內已是 900 卻在 14:37:00／14:38:00 連續觸發），因此改用 minutes 單位；
改節奏只需改這一個欄位，但需先接受上表成本或設定 execution 修剪。
註：60s 實驗期間產生的 `running` 幽靈列（execution id 3–8、10）為驗證殘留，**未以 SQL 手動改寫 n8n DB**，已由 n8n 自身的 pruning 清除。
**Phase 2（§9.G）追加／2026-09-25 live close-out**：runtime 真值不再來自 5 分鐘投影檔，而是每次 refresh 由既有 host bridge 執行一次 on-demand observation。先前「來源即時即可保證 ≤5 分鐘展示」的主張有誤，因為舊 canvas 仍每 15 分鐘寫快照；`824aecb` 已把 repo 與 live 排程改為 `minutesInterval: 5`，且不加 daemon／watcher。部署後 CatDesk browser 親眼確認 **00:55:25 → 01:00:25 → 01:05:25** 三個連續自動拍點皆成功，故目前正確表述為 **authoritative live read + ≤5-minute display freshness**。原先 15 分鐘節奏的儲存實測值保留於上表作歷史 sizing evidence；原本節點 5 `cat` 的 dashboard.json 為 6.6 KB，現在同一位置換成 observation envelope（real root 57 families 實測 **7.4 KB**），皆在 n8n execution data 內。

execution 列的 id 區間**只在本文件 §9.B 的〈execution 現況〉寫一次**；本節先前另寫一份（12–15），與 §9.B（12–19）及 A2 表（exec 20）三方漂移，已收斂。

## 9. 驗證記錄（實跑證據）

> 第一輪（§9.A）＝部署與投影；第二輪（§9.B）＝2026-09-21T15:36Z 起的 DB 修復、節奏復活與 review round-1 要求項；第三輪（§9.C）＝review round-2 要求項（CLI 契約、文件漂移）；第四輪（§9.D）＝review round-3 要求項；第五輪（§9.E）＝2026-09-21T23:19Z–23:31Z 新增唯一一個 read-only 來源（Hermes Scout cron state）＋對應的 ro 掛載與容器重建；第六輪（§9.F）＝2026-09-21T23:44Z–2026-09-22T00:01Z 的 review（t_5fbebce6 changes_requested）要求項：stage 1 收緊為契約白名單、`shadow_check.py` 加 presence 斷言＋回歸 fixture、live／repo 等價敘述與時間戳更正（純本機 shadow 層，未動 pipeline）。

### 9.A 第一輪：部署與投影

- **健康**：容器重建後 `curl -s http://127.0.0.1:5678/healthz` → `{"status":"ok"}`（重建後、重啟後各一次）。
- **匯入／清單**：`import:workflow` → `Successfully imported 1 workflow.`；`list:workflow` → `shadowQuantCp1|Quant Control Plane — SHADOW (read-only)`（單一 workflow）。
- **手動執行**：`n8n execute --id=shadowQuantCp1` → `"status": "success"`（CLI，Manual Trigger），並在 host 產生快照檔。
- **讀回**：`/Users/hong/workspace/n8n/files/quant-control-plane-shadow.json`（16,029 B）內容與來源逐項相符（§4 全表）；
  `python3 n8n/shadow_check.py` 檢查 schema／mode／topology 11 階／`null` 語意／reconciliation／新鮮度 → PASS。
- **排程生效**：`container logs n8n` → `Activated workflow "Quant Control Plane — SHADOW (read-only)" (ID: shadowQuantCp1)`；
  重啟後排程實際觸發：60s 版本於 14:31／14:32／14:33 連續三拍（execution 4–6）；改為 `minutes/15` 後 14:39–14:44 **無**每分鐘觸發，
  下一拍 14:45:25（execution 14）與其後一拍 15:00:25（execution 15）相隔 **恰好 900 s**＝15 分鐘，兩者皆 `success`、各儲存 26,815 B，快照 `generated_at_utc` 隨之更新。
- **唯讀實證**：容器內 `touch /host/quant-dashboard-data/NOPE`、`touch /host/workspace-ro/NOPE`、`touch /host/workspace-ro/alpha-strategy-research/NOPE`
  → 三者皆 `Read-only file system`。
- **零寫入權威狀態**：本卡未寫 `/Volumes/ExpansionDrive/qlib-results`、`market-data-raw`、Kanban DB、candidate state、leaderboard、private survivor repo、GitHub；
  未暫停／修改任何 cron；未 launch／retry／resume 任何 family；未動 `qlib-run` 容器。
  （`dashboard.json` 自身由既有 cron `3d2e54e178ff`（`*/5`；`quant_candidate_snapshot.py` → `runtime/candidate_snapshot.py`）重新產生，非本 workflow 所寫。）

### 9.B 第二輪：DB 修復、節奏復活與 review round-1 要求項（2026-09-21T15:36Z–16:31Z）

**A1 — DB 修復（根因鏈見 §7.1）**

- `15:36:56Z`：`container stop -t 60 n8n` 耗時 32 s；`stdio.log` 顯示該次 shutdown 卡在 `Waiting for 2 active executions to finish...` → `Shutdown timed out after 30 seconds`
  → 行程以 crash 路徑結束，**SQLite 連線未關閉**（＝不乾淨）。
- 停止後 `data/` 仍見 `database.sqlite-wal`（1,355,512 B）與 `database.sqlite-shm`（32,768 B）。
- host 端 drain：`PRAGMA wal_checkpoint(TRUNCATE)` → `0|0|0`、`PRAGMA integrity_check` → `ok`；移除死亡 `-shm`／已截斷的 `-wal` 後，
  `database.sqlite` sha256 = `7c924e8b…`（**與 drain 前逐位元相同 → 無資料遺失**）。備份：`/Users/hong/workspace/n8n/backups/pre-repair-20260921T153540Z/`。
- `15:37:55Z` `container start n8n` → n8n 自報 `Last session crashed`（＝前次不乾淨，與上面證據一致）；其後 log（28 行）**0 筆**
  `SQLITE_IOERR`／`SQLITE_CORRUPT`／`Failed to hard-delete executions`／`Failed to query executions parked`／`disk I/O error`／`malformed`；
  `curl -s http://127.0.0.1:5678/healthz` → `{"status":"ok"}`。
- `15:39:24Z` 依 §7.1 再做一次 stop(60 s 寬限)→drain→start：shutdown log 為 `Received SIGTERM. Shutting down...`、`Deregistered all crons`；
  `list:workflow --active=true` → `shadowQuantCp1|Quant Control Plane — SHADOW (read-only)`。

**A2 — 節奏復活：連續 5 拍 900 s、全 `success`、快照每拍前進**

| 拍 | execution id | startedAt (UTC) | stoppedAt (UTC) | status | 快照 mtime（CST） | 與前拍間隔 |
|---|---|---|---|---|---|---|
| 1 | 16 | 15:45:25.047 | 15:45:25.206 | `success` | 23:45:25 | — |
| 2 | 17 | 16:00:25.099 | 16:00:25.295 | `success` | 00:00:25 | 900.05 s |
| 3 | 18 | 16:15:25.058 | 16:15:25.249 | `success` | 00:15:25 | 899.96 s |
| 4 | 19 | 16:30:25.098 | 16:30:25.255 | `success` | 00:30:25 | 900.04 s |
| 5 | 20 | 16:45:25.071 | 16:45:25.228 | `success` | 00:45:25 | 899.97 s |

- 觀察窗 15:45:25Z → 16:45:25Z = **60 分 00 秒**、連續 5 拍（要求 ≥3 拍／≥45 分鐘）；每拍同時檢查 `container logs n8n` 的 I/O 錯誤計數皆為 **0**。
- `python3 n8n/shadow_check.py --require-fresh` → **PASS**（最終檢查當下快照 age 44 s；`generated_at_utc = 2026-09-21T16:45:25.221Z`）；
  快照 sha256 `285b397e…`、16,027 B（tick 4 當下為 `69b5fb1b…`／16,029 B），`mode = SHADOW_READ_ONLY`、`mutations_enabled = false`、topology 11 階、state vocabulary 9 token。
- 計數對帳：15:45Z 拍點 `pool_records_total` / `pool_root_md_total` = **831 / 833** → 16:45Z 拍點 **832 / 834**（checkout HEAD `2a34d59`；來源為 pipeline 自身 scout cron，見 §4 註），
  其餘 count 與 §4 表逐項相同；唯一 `ok = false` 仍是 leaderboard 29 vs parking 24（刻意呈現，不調和）。

**execution 現況（本文件唯一權威敘述；point-in-time 讀值，不是常數）**

截至 **2026-09-21T23:30:33Z**（本輪 §9.E 變更後的排程拍點、exec 48 落地後）以唯讀探針讀取：`total = 41`、`running = 0`、`max_id = 48`。

| status | n | execution id |
|---|---|---|
| `crashed` | 1 | 1（早期 CLI） |
| `error` | 1 | 2（早期 CLI） |
| `success` | 39 | 9・11（CLI 手動驗證）、12–13（60 s 節奏）、14–15（`minutes/15` 切換後首批 900 s）、16–46（修復後 900 s 排程拍點）、47（§9.E CLI 驗證）、48（§9.E 排程拍點） |

此表是 2026-09-21 的 **15 分鐘歷史 execution evidence**；現行 live 已改為 5 分鐘。取當下值一律用 §7.1 的唯讀探針
（`container exec n8n node /host/workspace-ro/quant-runtime-pipeline-n8n/n8n/tick_probe.js`），**不在 host 端開 live DB**。
§8 與本節 C2／C3 只指向本段，不各自複寫 id 區間（先前三方各寫一份而漂移，見 §8 註）。

**C1 — 節點名稱對齊 live／repo**：`n8n export:workflow --id=shadowQuantCp1`（重啟後）與 repo 匯出在 `id`（`shadowQuantCp1`）／`name`／`active`（`true`）／`connections` 上**逐鍵相同**；
`nodes` 與 `settings` **不同**（皆為 n8n import／儲存時的正規化，非部署漂移）——**語意等價**才是可重現的敘述：10 個節點的 `type` 全同，除正規化鍵外每個節點的參數逐位元相同
（5 個來源／投影指令與 Code `jsCode` 的 sha256 兩側一致）。完整差異清單、取證指令與實跑值見 **§9.D**。**現行 2026-09-25 live export** 的節點名為 `Schedule — 5m observation`，參數 `{field: minutes, minutesInterval: 5}`；本段其餘數值仍是 2026-09-21 的歷史取證。

**C2／C3**：§8 的 26,416 B 已改為 **26,815 B**（並註明 exec 11 為 26,416 B）；幽靈 `running` 列敘述更新為「已由 n8n 自身 pruning 清除、`running = 0`」，
id 區間以本節〈execution 現況〉**單一權威段落**為準（此處先前寫 12–19、§8 寫 12–15、A2 表寫到 exec 20 → review round-2 A2 指出的三方漂移）。

**A5 — 邊界不變（實測 before／after）**

| 對象 | 證據 |
|---|---|
| `alpha-strategy-review-state.json` | sha256 `4363e3b6…`（與部署前相同）、mtime 14:55（早於本輪） |
| parking mirror `leaderboard/leaderboard.json` | sha256 `96f5b15f…`；`survivors/` 24 dirs、repo 30 files、HEAD `30b6f8b9…`、`git status` clean |
| `/Volumes/ExpansionDrive/qlib-results/_survivors/leaderboard.json` | sha256 `6b287c70…`、mtime 10:01（未動） |
| `dashboard.json` | 由自身 5 分鐘 cron（`3d2e54e178ff`）更新，**非** shadow 寫入（15:48 與 16:30 兩次取樣之間僅此檔變動） |
| Kanban／candidate／private repo／GitHub | n8n 容器沒有這些掛載（§6）＝結構上不可寫；本輪未 push、未 merge main |
| 3 支 quant cron | `624d0be5b23c`／`f6b9aa5e9034`／`c5314d86cdfe` 皆 enabled、last run `ok`；`0090473eae7b` 自 05:43 起 paused（早於本卡，非本輪所為） |
| `qlib-run` | 未動（started 2026-09-20T21:07:42Z） |
| 唯讀掛載 | `touch /host/quant-dashboard-data/NOPE2`、`touch /host/workspace-ro/NOPE2` → 皆 `Read-only file system` |

**唯讀探針**：本輪新增 `n8n/tick_probe.js`（read-only；在容器內複製 DB trio 後讀**副本**，host 全程不開 live DB）；上表所有 execution 計數皆由它產出。

### 9.C 第三輪：review round-2 要求項（2026-09-22T01:05 CST）

**A1 — `n8n/shadow_check.py` 值型旗標解析（已修，實跑）**

- 根因：`args = [a for a in argv[1:] if not a.startswith("--")]` 把旗標值 `1800` 當成位置參數（→ `FAIL snapshot not found: 1800`）；同一行也讓等號形 `--max-age-seconds=1` 從未被解析，靜默落回預設 **2400**。
- 修法：新增 `parse_args()`（維持手寫解析、零新依賴），同時支援 `--max-age-seconds N` 與 `--max-age-seconds=N`；未知旗標／缺值／多餘位置參數一律 `rc=1` 明示失敗，不再靜默忽略（與原缺陷同一類）。`--selftest` 為同檔內的契約檢查（無新檔、無框架）。
- 實跑（`/opt/homebrew/bin/python3 n8n/shadow_check.py …`；exit code 逐一擷取、未經管道）：

| 呼叫 | 結果 | rc |
|---|---|---|
| `--selftest` | `PASS parser self-test (6 accepted forms, 3 rejected)` | 0 |
| （無參數） | PASS，threshold 2400s（行為不變） | 0 |
| `--require-fresh` | PASS，threshold 2400s（行為不變） | 0 |
| `--max-age-seconds 1800 --require-fresh` | PASS，threshold **1800s**（修正前 rc=1 `FAIL snapshot not found: 1800`） | 0 |
| `--max-age-seconds=1800 --require-fresh` | PASS，threshold **1800s**（修正前靜默落回 2400s） | 0 |
| `--max-age-seconds 1 --require-fresh` | FAIL，threshold **1s**＝負向對照，證明門檻真的被採用 | 1 |
| `--max-age-seconds=1 --require-fresh` | FAIL，threshold **1s**＝同上 | 1 |
| `<snapshot 路徑>`（位置參數） | PASS，無回歸 | 0 |
| `--nope`／`--max-age-seconds`（缺值） | `unknown option: --nope`／`--max-age-seconds needs a value` | 1 |

> 註（2026-09-22T01:29 CST）：上表 `--selftest` 那行是 round-2 當下的原文輸出；§9.D 為非整數值增列 2 個拒絕形後，現行輸出為 `PASS parser self-test (6 accepted forms, 5 rejected)`。

- `--selftest` **非恆真**：改寫過程中它當場抓到作者自己的 `argv` 索引錯誤（`['--require-fresh']` 被跳過）→ 修正後才轉 PASS。

**A2 — 文件漂移（已收斂為單一權威段落）**

- §8 與 §9.B C2／C3 不再各自複寫 exec id 區間，兩處都指向〈execution 現況〉；該段基準 = 2026-09-21T17:00:25Z 唯讀讀值（`total = 14`、`running = 0`、`max_id = 21`、`success = 12`），與 A2 表（exec 16–20）＋ exec 21 一致。
- 同輪順帶修掉同節內另一處自相矛盾：A2 標題「連續 4 拍」→「連續 5 拍」（表有 5 列、窗 60 分、該節內文本來就寫 5 拍）。

**本輪範圍**：只改 `n8n/shadow_check.py` 與 `N8N_CONTROL_PLANE.md` 兩個檔案；未重啟容器、未重新 import workflow、未觸發任何 n8n execution、未動 pipeline／cron／`qlib-run`／任何權威狀態。

### 9.D 第四輪：review round-3 要求項（2026-09-21T17:26Z–17:31Z 實跑）

**A1 — §9.B C1 的 live／repo 等價敘述改為可重現版本（已修）**

review round-3 判決「六鍵逐鍵相同（差異集為空）」不成立；本輪以同一道指令獨立重跑，**複驗該判決成立**（並以實測修正其中兩處細節：position 差異是 **8 個**節點、live 側連節點層也沒有 `executeOnce`）。§9.B C1 已改為「相同鍵」＋「已知正規化差異」＋「語意等價（指令／`jsCode` 逐位元相同）」的版本。

取證指令（唯讀；唯一寫入是容器的 `/tmp`，host 端不留檔、不開 live DB。**以下兩段可直接貼上重跑**）：

```
python3 - <<'PY'
import hashlib, json, subprocess

def container(*args):
    return subprocess.run(["container", "exec", "n8n", *args], capture_output=True, text=True, check=True)

container("n8n", "export:workflow", "--id=shadowQuantCp1", "--output=/tmp/r4-live-export.json")
raw = container("cat", "/tmp/r4-live-export.json").stdout
print("live export bytes:", len(raw.encode()), "sha256:", hashlib.sha256(raw.encode()).hexdigest()[:8])

canon = lambda o: json.dumps(o, sort_keys=True, separators=(",", ":"))
live = json.loads(raw)[0]                                     # n8n 匯出是單元素陣列
repo = json.load(open("n8n/quant-control-plane-shadow.workflow.json"))
print("diff keys:", [k for k in sorted(set(live) | set(repo))
                     if canon(live.get(k, "<absent>")) != canon(repo.get(k, "<absent>"))])
PY
```

```
python3 - <<'PY'
import hashlib, json, subprocess

def container(*args):
    return subprocess.run(["container", "exec", "n8n", *args], capture_output=True, text=True, check=True)

canon = lambda o: json.dumps(o, sort_keys=True, separators=(",", ":"))
sha = lambda s: hashlib.sha256(s.encode()).hexdigest()
live = json.loads(container("cat", "/tmp/r4-live-export.json").stdout)[0]
repo = json.load(open("n8n/quant-control-plane-shadow.workflow.json"))
NORM = {"executeOnce", "mode", "language", "dataPropertyName"}   # 只有這 4 個鍵被 n8n 正規化
for l, r in zip(live["nodes"], repo["nodes"]):
    lp = {k: v for k, v in l.get("parameters", {}).items() if k not in NORM}
    rp = {k: v for k, v in r.get("parameters", {}).items() if k not in NORM}
    payload = r.get("parameters", {}).get("command") or r.get("parameters", {}).get("jsCode")
    print(f"  {r['name'][:46]:48s} name={l['name'] == r['name']} type={l['type'] == r['type']} "
          f"params_same={canon(lp) == canon(rp)} payload_sha256={sha(payload)[:8] if payload else '-'}")
PY
```

實跑值（2026-09-21T17:26Z）：live 匯出 **28,914 B**、sha256 `ec619a85…`（`cat` 過容器邊界後 sha256 不變）；repo 檔 sha256 `312a46b9…`（本輪未動）。

| 比較面 | 結果（實測） |
|---|---|
| **相同** | `id`（`shadowQuantCp1`）／`name`／`active`（`true`）／`connections`／`meta`（`{"instanceId":"shadow-1-local"}`）／`pinData`／`tags`（`[]`）。10 個節點的 `id`／`name`／`type`／`typeVersion` 全同；`settings` 的 `executionOrder`／`saveDataSuccessExecution`／`saveDataErrorExecution`／`saveManualExecutions` 逐值相同 |
| **不同 —— `nodes`：n8n 正規化（4 類）** | ① 5 個 `executeCommand` 節點：repo 的 `parameters.executeOnce: true` 在 live **不存在**（live 側連**節點層**都沒有這個鍵——n8n 未持久化寫在 `parameters` 裡的旗標）。本拓樸是單線鏈（`Manual Trigger`／`Schedule` → `Pool` → … → `Emit`），每拍每個來源節點只收 1 個 item，故有無此旗標不改變行為。<br>② Code 節點：repo 有 `parameters.mode="runOnceForAllItems"`、`parameters.language="javaScript"`，live 沒有（型別預設值不回寫）。<br>③ `readWriteFile`：repo 有 `parameters.dataPropertyName="data"`，live 沒有（同上）。<br>④ `position`：**8 個**節點不同（5 個來源節點 `y 420→432`；`Assemble`／`Emit` `y 140→144`；sticky note `[-260,400]→[-16,64]`）＝ canvas 16px 格點吸附 |
| **不同 —— `settings`** | live 多一個 `binaryMode="separate"`（儲存時補上的預設） |
| **不同 —— top-level instance 鍵（live 匯出多出、repo 檔沒有）** | `activeVersionId`／`createdAt`／`description`／`isArchived`／`nodeGroups`／`shared`／`sourceWorkflowId`／`triggerCount`／`updatedAt`／`versionCounter`／`versionMetadata`；另 `staticData`（live 是排程 recurrence 記號 `{"node:Schedule — 15m observation": …}`、repo 為 `null`）與 `versionId`（live `322345a2…` vs repo `117ec9f7…`）本質為 instance／版本控管值 |

**語意等價（本輪實測、可重跑）**：除上表 4 類正規化鍵外，每個節點的參數**逐位元相同**（上面第二段取證指令逐節點印 `params_same=True`）——

- 5 個來源／投影指令 sha256：`329eb9b9…`／`18b5df35…`／`46959354…`／`c1275683…`／`92208ab0…`（live 與 repo 一致）；Code 節點 `jsCode` sha256 `38871aa9…`（兩側一致）。
- 結論：repo 匯出檔是**可重現的來源**，live 部署是**同一份拓樸**多一層 n8n 自身的正規化與 instance 書籤；「六鍵逐字相同」不是驗收條件。本輪**未**為此改 workflow 檔、**未** re-import、**未**重啟容器。

**C1（round-3 §C 的可選項）— `--max-age-seconds` 非整數值的失敗輸出（已做；新增同檔 `_int()` ＋ 2 個 selftest 拒絕形）**

- 修正前：`--max-age-seconds=abc` 與 `--max-age-seconds abc` 皆為未捕捉的 `ValueError` traceback（`rc=1`，屬明示失敗但輸出難讀）。
- 修法：`parse_args()` 的整數轉換統一走同檔 `_int(flag, raw)` → `SystemExit("--max-age-seconds needs an integer, got: 'abc'")`；`--selftest` 增列這 2 個拒絕形（`--nope`／缺值／多餘位置參數的行為不變）。零新依賴、零新檔案。

實跑（`/opt/homebrew/bin/python3 n8n/shadow_check.py …`；exit code 逐一擷取、未經管道；同一次執行內 snapshot age 815 s、`generated_at_utc=2026-09-21T17:15:25.163Z`）：

| 呼叫 | 結果 | rc |
|---|---|---|
| `--selftest` | `PASS parser self-test (6 accepted forms, 5 rejected)` | 0 |
| （無參數） | PASS，threshold 2400s | 0 |
| `--require-fresh` | PASS，threshold 2400s | 0 |
| `--max-age-seconds 1800 --require-fresh` | PASS，threshold 1800s | 0 |
| `--max-age-seconds=1800 --require-fresh` | PASS，threshold 1800s | 0 |
| `--max-age-seconds 1 --require-fresh` | FAIL，threshold 1s（負向對照：門檻真被採用） | 1 |
| `--max-age-seconds=1 --require-fresh` | FAIL，threshold 1s | 1 |
| `<snapshot 路徑>`（位置參數） | PASS，無回歸 | 0 |
| `--nope` | `unknown option: --nope` | 1 |
| `--max-age-seconds`（缺值） | `--max-age-seconds needs a value` | 1 |
| `--max-age-seconds=abc` | `--max-age-seconds needs an integer, got: 'abc'` | 1 |
| `--max-age-seconds abc` | `--max-age-seconds needs an integer, got: 'abc'` | 1 |
| `a.json b.json`（多餘位置參數） | `unexpected extra argument: b.json` | 1 |

**活體檢查（本輪，未重啟容器、未觸發任何 execution）**

- exec **23** 於 `17:30:25.041Z` → `17:30:25.166Z` **`success`**（與 exec 22 相隔 **900.00 s**，即連續第 8 拍 16–23）；快照 `generated_at_utc = 2026-09-21T17:30:25.156Z`、`--max-age-seconds 1800 --require-fresh` → **PASS**（age 36 s）。
- 唯讀探針（`n8n/tick_probe.js`；`2026-09-21T17:31:01Z`）讀值：`integrity_check = ok`、`total = 16`、`max_id = 23`、`success = 14`、`crashed = 1`、`error = 1`；`/healthz` 與 `/healthz/readiness` 皆 `200`。

**本輪範圍**：只改 `n8n/shadow_check.py`（新增 `_int()` 硬化 ＋ 2 個 selftest 拒絕形）與 `N8N_CONTROL_PLANE.md`；未重啟容器、未重新 import workflow、未觸發任何 n8n execution、未動 pipeline／cron／`qlib-run`／任何權威狀態。

### 9.E 第五輪：唯一新增來源 — Hermes Scout cron state（2026-09-21T23:19Z–23:31Z 實跑；＝ CST 09-22 07:19–07:31）

**本輪範圍**：新增 **一個** read-only 來源節點（`Strategy Research — Hermes Scout cron state (read-only)`，插在 Pool 之前）＋ n8n 多一個 **ro** 掛載 ＋ `shadow_check.py` 一條斷言；
未動 Scout cron（prompt／schedule／model／狀態）、未動 pipeline／Qlib／Kanban／GitHub，`mutations_enabled` 仍 `false`、resume 仍 `false`。

**E1 — 節點與拓撲（repo 匯出檔 → live）**

- repo 檔 sha256 `6fc50e0f…`（34,411 B；`+28 / −3` 行，單檔）：節點 11 個（新增 `executeCommand` 節點，插在 Pool 之前），connections 改為 兩顆 trigger → **Scout** → Pool → …；
- `import:workflow` → `Successfully imported 1 workflow.`；`update:workflow --id=shadowQuantCp1 --active=true` → CLI 提示需重啟；重啟後 stdio.log → `Activated workflow "Quant Control Plane — SHADOW (read-only)" (ID: shadowQuantCp1)`；`list:workflow --active=true` → `shadowQuantCp1|Quant Control Plane — SHADOW (read-only)`；
- 容器內 `export:workflow` 讀回：`active`、**11 nodes**，順序為 Trigger／Schedule／**Scout**／Pool／Intake／Gate／Dashboard／Parking／Assemble／Emit／Sticky。

**E2 — 掛載與安全重啟（§7.1 全程遵守）**

- **單檔 bind mount 不可行**（Apple `container` 實測回 `Error: path '…/jobs.json' is not a directory`），故掛目錄層最小單位：`/Users/hong/.hermes/cron → /host/hermes-cron-ro`（**ro**）；節點只讀其中 `jobs.json` 一個檔、只投影一個 job 的固定欄位。
- `container inspect` before／after：**新增的唯一 mount 就是這一個**，其他 4 個逐項不變；image／cpus（4）／mem（1024 MB）／env／ports（`127.0.0.1:5678`）／user（`node`）／workdir 全部相同。
- 兩次重啟（重建前、啟用後）都走 §7.1：`container stop -t 60` → stdio.log 顯示 `Received SIGTERM. Shutting down...`／`Stopping n8n...`（**無** `Shutdown timed out`）→ `PRAGMA wal_checkpoint(TRUNCATE)` = `0|0|0`、`PRAGMA integrity_check` = `ok` → 僅在停止時移除 `-wal`／`-shm` → `container start`；
  健康 `curl /healthz` → `{"status":"ok"}`、readiness `200`；開機後 log 中 `SQLITE_IOERR|SQLITE_CORRUPT|disk I/O error|malformed|Failed to hard-delete` 行數 = **0**。備份：`/Users/hong/workspace/n8n/backups/pre-scout-20260921T232112Z/`（`database.sqlite`＋`-wal`＋`-shm`）。

**E3 — 一次成功執行＋ stage 1 的 live 欄位**

- `container exec n8n sh -c 'N8N_RUNNERS_BROKER_PORT=5699 n8n execute --id=shadowQuantCp1'` → `"status": "success"`（exec **47**、mode `cli`、`2026-09-21T23:22:23.915Z → 23:22:24.723Z`）。
- 快照：**17,029 B**（原 16,013 B）、sha256 `6c3b851b…`、`generated_at_utc = 2026-09-21T23:22:24.709Z`；`mode = SHADOW_READ_ONLY`、`mutations_enabled = false`、topology 仍 11 階同序。
- stage 1 `observation`（逐字）與 host 端**同時**讀值比對：**11／11 欄位相同**（`job_id`／`name`／`enabled`／`state`／`schedule_display`／`last_run_at`／`last_status`／`last_error`／`failure_streak`／`next_run_at`／`last_dispatch`，另 `source_readable`／`state_file_updated_at`）：

| 欄位 | 值（live 逐字） |
|---|---|
| `job_id` / `name` | `f5c0648122f3` / `Quant Research Scout` |
| `enabled` / `state` | `true` / `scheduled` |
| `schedule_display` | `15 * * * *` |
| `last_run_at` / `last_status` / `last_error` | `2026-09-22T07:21:35.352109+08:00` / `ok` / `null` |
| `failure_streak` / `next_run_at` | `0` / `2026-09-22T08:15:00+08:00` |
| `last_dispatch` | `scheduled_at 2026-09-22T07:15:00+08:00`、`dispatched_at …07:15:49.146058+08:00`、`lateness_seconds 49.1`、`kind on_time` |
| `source_readable` / `state_file_updated_at` | `true` / `2026-09-22T07:21:35.352458+08:00` |

- `sources[0]`（`hermes_scout_cron_state`）：`path /host/hermes-cron-ro/jobs.json`、`readable true`、`bytes 56,603`、`sha256 aa993d81…`。
- **沒有外洩鍵**：stage 1 的 observation 逐鍵比對後，白名單外 = **0 個鍵**（Scout 的 prompt／其他 15 個 job／`executions.db`／`output/`／`usage_audit.jsonl` 都不在快照中）；`shadow_check.py` 的 `SCOUT_OBS_KEYS` 斷言會在未來任何多餘欄位出現時 FAIL。
- reader 強健性（實測，非推論）：以 throwaway 容器掛同一顆 scratch 目錄，host 端用 **tmp＋rename** 原子替換被掛載檔案後，容器端第一次 `cat` 會短暫 `ENOENT`（≤5 s 後恢復；in-place 改寫則立即可見）。Hermes ticker 正是以 tmp＋rename 改寫 `jobs.json`，故節點在同拍內 **重試一次**（300 ms）；兩次都讀不到時輸出 `available: false` ＋ 理由並 `exit 0`（**不會**讓 execution 失敗，也不會以預設值代替）。
- **變更後第一個排程拍點也成功**：exec **48**（mode `trigger`、`2026-09-21T23:30:25.039Z → 23:30:25.211Z`、`success`），與前一拍（exec 46，`23:15:25.333Z`）相隔 **900.1 s**＝15 分鐘節奏未被重建／重啟破壞；該拍快照 `generated_at_utc = 2026-09-21T23:30:25.203Z`（17,029 B、sha256 `77354946…`），`shadow_check.py --max-age-seconds 300 --require-fresh` → **PASS**（rc 0）。

> **歷史註（2026-09-21T23:47Z 修訂）**：本節 E1–E4 是**當輪**的逐項記錄，其中 stage 1 的欄位集合（`source_readable`／`state_file_updated_at`／`note`）與 E5 的 live／repo 敘述已由 §9.F 的 review 要求項修正：
> stage 1 現行契約是**只含白名單**（§3），診斷 metadata 移到 `sources[]`；E5 的「canon 相等」claim 已由審查者複驗證偽並改寫。E3 的欄位表保留為當輪證據（當時快照確實長那樣），**不得**當成現行契約引用。

**E4 — `shadow_check.py`（+1 斷言，附負向對照）**

| 呼叫 | 結果 | rc |
|---|---|---|
| `--selftest` | `PASS parser self-test (6 accepted forms, 5 rejected)` | 0 |
| 變更**前**快照（複本，stage 1 無 Scout 投影且無 gap） | `FAIL strategy research stage is never silently empty` | 1 |
| 變更後快照 `--max-age-seconds 600 --require-fresh` | **PASS**，含 `PASS strategy research stage carries the live Scout cron projection — job_id=f5c0648122f3 enabled=True state=scheduled next_run_at=2026-09-22T08:15:00+08:00` | 0 |

**E5 — live／repo 等價（§9.D 兩段取證原封重跑；2026-09-21T23:46Z 由本輪獨立複驗，claim 已更正）**

- **相同**：`id`（`shadowQuantCp1`）／`name`／`active`（`true`）／`connections`／`meta`／`pinData`／`tags`；11 個節點的 `id`／`name`／`type`／`typeVersion`，以及 `settings` 的 `executionOrder`／`saveDataSuccessExecution`／`saveDataErrorExecution`／`saveManualExecutions` 逐值相同。
- **不同 —— `nodes`／`settings`：n8n 自身的正規化，不是等價**：`nodes` 的差異只有 4 類正規化鍵（6 個 `executeCommand` 的 `parameters.executeOnce`、Code 的 `mode`／`language`、`readWriteFile` 的 `dataPropertyName`：repo 有、live 無）＋ **9 個節點的 `position` 格點吸附**（6 個來源節點 `y 420→432`、`Assemble`／`Emit` `y 140→144`、sticky `[-260,400]→[-112,32]`）；`settings` live 多一個 `binaryMode="separate"`。→ **不是 canon 相等**（§9.D 已記錄同一類差異，本輪實測再次成立）。
- **不同 —— top-level instance／版本鍵 13 個**：`activeVersionId`／`createdAt`／`description`／`isArchived`／`nodeGroups`／`shared`／`sourceWorkflowId`／`staticData`／`triggerCount`／`updatedAt`／`versionCounter`／`versionId`／`versionMetadata`。
- **語意等價（可重跑）**：除上列正規化鍵外，每個節點的參數**逐位元相同**（§9.D 第二段逐節點印 `params_same=True`）；payload sha256：
  Scout（新）`c60bc406…`、Pool `329eb9b9…`、Intake `18b5df35…`、Gate `46959354…`、Dashboard `c1275683…`、Parking `92208ab0…`、Code `c6f6b689…`；本輪另比對 live／repo 的 `Assemble` `jsCode` sha256 兩側同為 `783720e0…`（§9.F F5）。
- live 匯出 32,897 B／sha256 `058c8589…`；repo 檔（當輪）34,411 B／sha256 `6fc50e0f…`（兩段取證指令見 §9.D，未改）。
- 結論：repo 匯出檔是**可重現的來源**，live 部署是**同一份拓樸**多一層 n8n 自身的正規化與 instance 書籤；驗收條件是「**語意等價 ＋ 列明已知正規化差異**」，**不是**逐鍵 canon 相等。本節 E5 記錄的是 **re-import 前**的 live 狀態；§9.F 的 re-import 之後，live 匯出的 `nodes`／`settings` 已與 repo 檔逐鍵相同（`position` 差異 0 個）。

**E6 — 邊界（before／after 實測）**

| 對象 | 證據 |
|---|---|
| Hermes cron | 16 jobs → 16 jobs、id 集合相同（**未新增任何 cron**）；Scout `f5c0648122f3` 的 `id`／`name`／`enabled`／`state`／`schedule`／`model`／`provider`／**prompt sha256 `0699cf5a…`**／`skills`／`deliver`／`workdir` 逐項不變。`jobs.json` 本身的 sha 每拍由 Hermes ticker 改寫（`last_run_at`／`fire_claim`），非本卡寫入；該目錄對容器為 **ro**（`touch` → `Read-only file system`）。 |
| `alpha-strategy-review-state.json` | sha256 `8ac9030d…`、mtime `2026-09-21T18:48:57Z`（before ＝ after） |
| parking mirror `leaderboard/leaderboard.json` | sha256 `96f5b15f…`（before ＝ after） |
| `qlib-run` 容器 | `startedDate` 不變（`2026-09-20T21:07:42Z`） |
| Kanban／GitHub／results root／`/Volumes/*` | 未掛載（§6）＝結構上不可寫；本輪未 push／未 merge |
| 唯讀掛載 | 容器內 `touch /host/hermes-cron-ro/NOPE`／`/host/quant-dashboard-data/NOPE3`／`/host/workspace-ro/NOPE3` → 三者皆 `Read-only file system` |

**E7 — 成本（§8 追加）**：變更前拍點（exec 44–46）每拍儲存 26,796–26,797 B；變更後 exec 47 = **28,425 B**（+6%），快照本體 16,013 → 17,029 B。
換算：96 拍／日 ≈ 2.7 MB／日、14 天保留窗 ≈ 38 MB（原估 35 MB），仍在同一量級。

### 9.F 第六輪：review（`changes_requested`）要求項 — stage 1 收緊為契約白名單（2026-09-21T23:44Z–2026-09-22T00:01Z 實跑）

**來源**：kanban `t_5fbebce6` 的 auditor review（`changes_requested`）三項要求：① stage 1 observation 只留卡片指定的 live 欄位、來源／診斷 metadata 移到 `sources[]` 或 gap；② `shadow_check.py` 補 required-key／presence 斷言＋一個會失敗的回歸 fixture；③ §9.E E5 的 live／repo「canon 相等」claim 與 §9.E 標題時間戳更正。

**本輪範圍**：只改 repo 內 3 個既有檔（`n8n/quant-control-plane-shadow.workflow.json`、`n8n/shadow_check.py`、`N8N_CONTROL_PLANE.md`）＋一次 re-import／publish／§7.1 重啟；
未動 Scout cron（prompt／schedule／model／狀態）、未動 pipeline／Qlib／Kanban／GitHub／results root，未新增 workflow／service／DB／daemon／watchdog／cron，`mutations_enabled` 仍 `false`、resume 仍 `false`。

**F1 — 改動內容（repo 檔）**

| 檔案 | 變更 | after |
|---|---|---|
| `n8n/quant-control-plane-shadow.workflow.json` | assembler：stage 1 observation 由 13 鍵（含 `source_readable`／`state_file_updated_at`／`note`）收緊為 **10 個固定鍵 ＋ optional `last_dispatch`**；`sources[0]` 加 `note`（診斷 metadata 落點）；節點數／順序／connections 與其他 10 個節點未動 | 34,461 B／sha256 `528d2b1b…` |
| `n8n/shadow_check.py` | stage 1 斷言改為 3 條（欄位齊全／沒有白名單外鍵／`last_dispatch` 有值即完整）；斷言抽成 `check_snapshot()` 讓 `--selftest` 用 fixture 驅動；selftest 由「parser 6 接受／5 拒絕」擴為再加 **8 個 snapshot fixture** | 13,624 B（見 commit） |
| `N8N_CONTROL_PLANE.md` | §2 節點 1、§3 stage 1、§8 成本、§9 前言、§9.E 標題／歷史註／E5 更正，＋本節 | — |

**F2 — 部署（§7.1 全程遵守）**

- 備份：`/Users/hong/workspace/n8n/backups/pre-whitelist-20260921T234647Z/`（`database.sqlite`＋`-wal`＋`-shm`）。
- `import:workflow --input=/host/workspace-ro/…/quant-control-plane-shadow.workflow.json` → `Successfully imported 1 workflow.`；本版 CLI 對 `update:workflow --active=true` 回 `Please use: publish:workflow --id=shadowQuantCp1`，故補跑 `publish:workflow`；`list:workflow --active=true` 讀回 `shadowQuantCp1|Quant Control Plane — SHADOW (read-only)`。
- 重啟：`container stop -t 60 n8n`（`23:47:01Z`）→ stdio.log 出現 `Received SIGTERM. Shutting down...`／`Stopping n8n...`、**無** `Shutdown timed out`／`Waiting for N active executions` → `PRAGMA wal_checkpoint(TRUNCATE)` = `0|0|0`、`PRAGMA integrity_check` = `ok` → 停止時 `rm -f` 只刪 `-wal`／`-shm` → `container start n8n`（`23:47:15Z`）→ `/healthz` `{"status":"ok"}`、readiness `200`；開機 log 中 `SQLITE_IOERR|SQLITE_CORRUPT|disk I/O error|malformed|Failed to hard-delete` = **0** 行。
- `container inspect` before／after 逐鍵比對（含 `mounts`）：**全部相同**——本輪只 stop／start，未重建容器、未新增掛載。

**F3 — 變更後執行（一次即可；本卡明示不需等下一小時）**

- `n8n execute --id=shadowQuantCp1` → `"status": "success"`（exec **50**、mode `cli`、`2026-09-21T23:47:35.185Z → 23:47:35.796Z`）。
- 快照：**16,947 B**、sha256 `c4e74fd1…`、`generated_at_utc = 2026-09-21T23:47:35.782Z`；topology 11 階同序、`mode = SHADOW_READ_ONLY`、`mutations_enabled = false`、resume `false`。
- **重啟後的第一個排程拍點也成功**：exec **51**（mode `trigger`、`2026-09-22T00:00:25.264Z → 00:00:25.445Z`、`success`），與改動前最後一拍（exec 49、`23:45:25.115Z`）相隔 **900.1 s**＝15 分鐘節奏未被 re-import／重啟破壞；快照 `16,947 B`、sha256 `005d28f6…`、`generated_at_utc = 2026-09-22T00:00:25.436Z`，`shadow_check.py --max-age-seconds 300 --require-fresh` → **PASS**（rc 0），stage 1 仍為白名單 11 鍵、host 10／10 相同、`last_dispatch` 相同。

**F4 — stage 1 白名單（live 逐字）**

| 面 | 值／結果 |
|---|---|
| observation 鍵集合 | 恰為 11 鍵（10 個固定鍵 ＋ `last_dispatch`）；**白名單外 = 0 個鍵** |
| host（同時讀 `~/.hermes/cron/jobs.json`）vs 快照 | **10／10 相同**：`job_id f5c0648122f3`／`name Quant Research Scout`／`enabled true`／`state scheduled`／`schedule_display 15 * * * *`／`last_run_at 2026-09-22T07:21:35.352109+08:00`／`last_status ok`／`last_error null`／`failure_streak 0`／`next_run_at 2026-09-22T08:15:00+08:00`；`last_dispatch` 亦逐字相同（`scheduled_at 2026-09-22T07:15:00+08:00`／`dispatched_at …07:15:49.146058+08:00`／`lateness_seconds 49.1`／`kind on_time`） |
| 診斷 metadata 落點 | `source_readable`／`state_file_updated_at`／`note` **不在** observation；對應值在 `sources[0]`（`readable true`、`as_of_utc 2026-09-22T07:46:59.982968+08:00`、`path /host/hermes-cron-ro/jobs.json`、`bytes 56,603`、`sha256 f6602a5e…`、`note`） |

**F5 — live／repo 等價（re-import 後；§9.D 兩段取證原封重跑）**

- live export **32,948 B**／sha256 `5546af3f…`（改動前：`32,729 B`／`5c8099d3…`）；repo 檔 34,461 B／`528d2b1b…`。
- **`nodes` 與 `settings` 逐鍵相同**：`canon(live.nodes) == canon(repo.nodes)` = True、11 個節點 `param_diff=[]`、`position` 差異 = 0 個、`settings` 逐值相同。
  對照 §9.E E5（re-import **前**：9 個 `position` 差異、4 類參數正規化鍵、`settings.binaryMode`）：那些差異來自 **n8n server 自身的儲存路徑**，而 CLI `import:workflow` 會把 repo 檔寫成 current version 並原封保存，故 re-import 後 live 匯出即回到 repo 形式。
- 差異鍵只剩 13 個 instance／版本鍵（`activeVersionId`／`createdAt`／`description`／`isArchived`／`nodeGroups`／`shared`／`sourceWorkflowId`／`staticData`／`triggerCount`／`updatedAt`／`versionCounter`／`versionId`／`versionMetadata`）；`id`／`name`／`active`／`connections`／`meta`／`pinData`／`tags` 相同。
- 語意等價證據：`Assemble` 的 `jsCode` sha256 live／repo 兩側同為 `783720e0…`；6 個來源節點 payload sha256 與 §9.E 相同（`c60bc406…`／`329eb9b9…`／`18b5df35…`／`46959354…`／`c1275683…`／`92208ab0…`，本輪未動）。
- 註：live 形式日後若經 UI／server 儲存，可能再被正規化；驗收條件仍是「**語意等價 ＋ 列明已知正規化差異**」，不是逐鍵相等。

**F6 — 斷言的活體與負向對照**

| 呼叫 | 結果 | rc |
|---|---|---|
| `shadow_check.py --selftest` | `PASS parser self-test (6 accepted forms, 5 rejected)` ＋ `PASS snapshot self-test (8 fixtures: 3 sound, 5 each with exactly the expected failure)` | 0 |
| review 重現：複製 live 快照後刪 `last_status`，`--max-age-seconds 999999 --require-fresh` | **FAIL** — `stage 1 carries every required cron key` | 1 |
| 改動**前**的 live 快照（observation 含 `source_readable` 等 3 鍵） | **FAIL** — `stage 1 carries only the whitelisted cron keys` | 1 |
| 改動後 live 快照 `--max-age-seconds 300 --require-fresh` | **PASS**（含 `stage 1 carries every required cron key — missing=[]`、`… only the whitelisted cron keys — unexpected=[]`、`… last_dispatch is optional and complete when present`） | 0 |

**F7 — 邊界（before／after 實測）**

| 對象 | 證據 |
|---|---|
| Hermes cron | 16 jobs → 16 jobs、id 集合相同（**未新增任何 cron**）；Scout `f5c0648122f3` 的 `id`／`name`／`enabled`／`state`（`scheduled`）／`schedule`（`cron 15 * * * *`）／`model`（`mimo-v2.5`）／`provider`（`opencode-go`）／**prompt sha256 `0699cf5a…`（6,223 B）**／`skills`／`deliver`（`local`）／`workdir` 逐項不變（與 §9.E E6 同值）。 |
| `alpha-strategy-review-state.json` | sha256 `8ac9030d…`、mtime `2026-09-21T18:48:57Z`（與 §9.E E6 同值，早於本輪任何寫入） |
| parking mirror `leaderboard/leaderboard.json` | sha256 `96f5b15f…`、mtime `2026-09-20T20:20:53Z`（同上） |
| `qlib-run` 容器 | `container list` 的 STARTED 仍 `2026-09-20T21:07:42Z`（未動） |
| Kanban／GitHub／results root／`/Volumes/*` | 未掛載（§6）＝結構上不可寫；本輪未 push／未 merge |
| 唯讀掛載 | 容器內 `touch /host/hermes-cron-ro/NOPE5`／`/host/quant-dashboard-data/NOPE5`／`/host/workspace-ro/NOPE5` → 三者皆 `Read-only file system`，且無殘留檔 |

**F8 — 成本（§8 追加）**：同模式對照 exec 49（改動前排程拍點）= 28,823 B → exec 51（改動後排程拍點）= 28,794 B；CLI 對照 exec 47（改動前）= 28,425 B → exec 50（改動後）= 28,396 B（量測方式：容器內對 DB 複本讀 `length(execution_data.data)`，不碰 live 檔）。96 拍／日 ≈ 2.7 MB、14 天 ≈ 38 MB，量級不變。

### 9.G 第七輪：Phase 2 — authoritative runtime Current／counts（卡片 `t_35951c0c`；2026-09-24 實跑；**2026-09-25 live close-out verified**）

**問題（已驗證）**：runtime 相關欄位（Current、families／workload／leaderboard／candidate pool）來自 `/host/quant-dashboard-data/dashboard.json`（cron `3d2e54e178ff` 每 5 分鐘產生），canvas 每 15 分鐘取樣 → 間接且可能落後。Phase-1 cutover 後的具體錯配：direct family 已註冊、direct Hermes worker 正在跑，但 dashboard Current 仍顯示 idle（該 family 尚無 Qlib attempt）。

**選定的最小架構**：**既有 host bridge 上的第三個固定唯讀 action** `runtime_observe_once` → 固定 wrapper `~/.hermes/scripts/quant_runtime_observe.py` → canonical `runtime/runtime_observation.py`（on-demand、**無 snapshot 檔、無 daemon、無新掛載、無新 DB／queue**）。**不採用**「加一個 ro `/Volumes/ExpansionDrive/qlib-results` 掛載」：掛載只搬 bytes，`current` 的選擇（`production_handoff.runtime_state`，contract 14.4）、per-round verdict release、90 分鐘 active／launch window、progress 分母與 streamed rows、cohort、launch grace 都得在 n8n JS 重寫一遍＝第二套 runtime 真值；走既有 bridge 則直接重用 canonical Python 語意，兩者不可能不一致。

| 檔 | 變更 |
|---|---|
| `runtime/runtime_observation.py`（新） | `quant-runtime-observation/v1`。`current`＝§3 的五態＋family／round／attempt／stage／progress／cohort／direct-agent 證據／verdict／why；`counts`＝runtime 側計數；`funnel`／`health`／`leaderboard` **直接呼叫 `candidate_snapshot` 既有 helper**（同一計算，不可能分歧）；讀不到的一律 `null`＋`gaps`。不寫檔、不 spawn subprocess、不讀 Kanban。 |
| `runtime/candidate_snapshot.py` | 只抽出三個共用計算（`top_entries`／`health_payload`／`funnel_view`）；dashboard payload 在 7 個 fixture root 上與 HEAD **byte-identical**。 |
| `runtime/n8n_host_action_bridge.py` | 第三個 allowlist action ＋ **per-action output cap**（唯讀 64 KiB；兩個 mutating action 維持已稽核的 4096 B）。 |
| `n8n/quant-control-plane-shadow.workflow.json` | 節點 5 → `Runtime Observation — canonical runtime evidence (read-only)`；節點 4 投影每 family 最新 gate record；assembler 只從 observation 取 runtime counts／current；lifecycle view 改由 observation state 路由（`idle` → `Candidate Queue`）。workflow id／節點數（32）／router 17 輸出／canvas 不變。 |
| `~/.hermes/scripts/quant_runtime_observe.py`（新，host 非 repo） | thin `runpy` wrapper → canonical repo 模組。**尚未生效**：live bridge allowlist 尚無此 action、canonical main 尚無該模組（現在呼叫只會 fail-closed，見下）。 |
| 測試 | `runtime/tests/test_runtime_observation.py`（新 16）、`runtime/tests/test_n8n_host_action_bridge.py`（31）、`n8n/test_shadow_workflow.py`（重寫 19）、`n8n/shadow_check.py`（MUST_HOLD 名稱同步）。 |

**實跑證據（全程唯讀；未動 live 容器／workflow／mounts／main）**：

1. **Real root 對照（`/Volumes/ExpansionDrive/qlib-results`，2026-09-24T02:4xZ）**：observation 與獨立讀取逐項相同——`families_registered` **57**（`*/family.json` 去重）、`leaderboard_count` **29**、candidate pool **341／55／286**、last real advance `2026-09-24T02:20:03Z`（`crypto-microstructure-alpha-hierarchical-cross-asset-transfer-2026-09-01`）、in-flight **1**、workload **2,371,832**（386 grid artifacts）、`gaps` 空。**`current` = `preflight`**（該 family 的 attempt `…-r1-u1` 只有 `run-spec.json`、尚無 `state.json`／terminal sentinel；`agent.log` 最後寫入 7 分鐘前）——正是本卡要修的錯配：舊來源在此只會說 idle。
2. **Bridge 端到端（scratch mailbox，live `control/` 未動）**：real bridge → 固定命令 → wrapper → canonical module → response：`status=ok`／`exit_code=0`／`request_id` 對帳成功／stdout **7,375 B**（< 64 KiB cap）／stderr 0 B／response 為 regular file／claim 清除。同一 response 內 `status`（invocation outcome）與 `body.current.state`（pipeline outcome）分屬兩個欄位。**Fail-closed 反證**：wrapper 指向的 canonical main 尚無該模組時，bridge 回 `action_failed`／`exit_code=1`／`FileNotFoundError`，**不**假裝成功。
3. **測試**：runtime `discover -s runtime/tests -t runtime/tests` → **528/528 OK**（含新 16）；`n8n/test_shadow_workflow.py` **19/19**；`n8n/test_production_handoff_workflow.py` **4/4**；`n8n/test_runtime_reconciler_workflow.py` **4/4**；`shadow_check.py --selftest` PASS。
4. **唯讀性**：模組無寫入／無 subprocess（測試斷言）；fixture root 前後 tree 逐項不變；`candidate_snapshot.card_status`／`board_counts` 被替換為硬失敗後 observation 仍完成（Kanban 不參與）；靜態掃描全 workflow 節點無 `dashboard.json`／`quant-dashboard-data`／`dashboard_meta`。

**Live close-out／殘留注意事項**：Phase 2 已 deploy；bridge／wrapper／workflow 與 `main` 已對齊，`824aecb` 的 5m observation 與 canonical incident projection 已完成 independent audit、import／publish、safe restart 與 CatDesk browser 連續拍點驗證。`preflight` 涵蓋「attempt 目錄已存在但尚無 `state.json`」是依實測（real root 9 個無 `state.json` 的 attempt ＋ live family）反推，非新語意；stage 非三者之一時 canvas 仍走 `Attention / Unresolved`（不臆測）；prerequisite-gated round 的 progress 分母仍是 `unavailable`（canonical 語意未改）。目前沒有 active production incident，因此 incident→`Attention / Unresolved` 的 **live positive path** 不人工造 incident 驗證；該路徑已有 RED→GREEN regression 與 independent auditor PASS，留待下一次自然 incident 做 regression confirmation。

## 10. Cutover gates（未來把控制面接上時的前置條件）

此 shadow **不得**在沒有下列明確授權前升級為控制面：

1. 獨立 cutover 卡（operator 明確要求），且不得與本觀測層的節點混用同一份執行路徑。
2. 明確列出「可 mutation 的動作集合」與其 fail-closed 條件（launch／retry／resume 各自的前置檢查）。
3. 寫入面必須落在既有真值的 owner 路徑（`_handoff/candidates.json`、Kanban、`/results`），且先有 atomic-write ＋ read-back ＋ incident 路徑，
   不得由 n8n 自建第二套 candidate／leaderboard／狀態儲存。
4. resume policy（§5）必須由 pipeline 端（reconciler／handoff）實作或明確委派，n8n 只呼叫既有機制，不自帶佇列語意。
5. 任何新增 mutating path 都必須先通過獨立審計（auditor）。既有 C1 host bridge、C2 manual handoff、C3 cadence trigger／HOLD 語意與本次 C3.1 15 分鐘 cadence 均已完成獨立審計；C3.1 已 live，後續新增 mutating path 仍須先 audit，不得以本次通過作為一般放寬。
6. **Phase 2 runtime truth feed（卡片 `t_35951c0c`，§9.G）**：獨立 auditor PASS **之後**才可 (a) 把 Phase 2 合併到 main（`~/.hermes/scripts/quant_runtime_observe.py` 指向的 canonical 模組才存在）、(b) 以 repo export 重新 import `shadowQuantCp1`、(c) 以同一 LaunchAgent 驗證一次排程拍點。三者皆不得在 audit 前做；observation 本身唯讀，不構成 mutating path。

## 11. 刻意不做（避免過度工程）

- 不加第二套 state store／queue／service／daemon；不新增 framework；不引入 credentials。
- 不重寫 `runtime/`、`candidate_snapshot.py`、reconciler、watchdog、handoff、Homepage、Qlib 或任何 backtest 語意。
- 不在 repo 內新增任何可自動觸發的排程（排程在 n8n，不在 repo；本 repo 只有 deterministic 腳本與版本控管）。
- 不 direct-read 需要寫入面的資料夾；`/Volumes/ExpansionDrive/*` 一律不掛載（gap 可接受，不為此建基礎設施）。
- 不動 Hermes cron：Scout `f5c0648122f3` 的 prompt／schedule／model／狀態一律未改，也未新增任何 cron；n8n 對 cron 只有 **ro** 讀取面（無 cron 寫入節點、無新 service／DB／daemon／watchdog）。
- 不調和 §4 第 4 項的 leaderboard／parking 數量差：只呈現缺口，交由 operator 判定。

