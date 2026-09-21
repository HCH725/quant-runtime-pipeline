# N8N Control Plane — SHADOW MODE（PROPOSED；未經審計）

狀態：**PROPOSED / SHADOW MODE**。本文件描述的是 **唯讀觀測層**，不是控制面。
`shadow-1` 不做任何 pipeline mutation：不改候選、不改 Kanban、不改 leaderboard、不改 `/Volumes/ExpansionDrive/qlib-results`、
不碰 private survivor repo、不 push GitHub、不動任何 cron。控制能力（launch / retry / resume / reorder / promote）**尚未實作**，
必須由另一個明確的 cutover 任務開啟（§10）。

本卡片只實作一個 n8n workflow：`Quant Control Plane — SHADOW (read-only)`（workflow id `shadowQuantCp1`），
所有 host 整合皆為 read-only；唯一的寫入是 n8n 部署自身檔案區的觀測快照（§6）。

---

## 1. 這個 shadow 是 / 不是什麼

| 是 | 不是 |
|---|---|
| 現有 pipeline 的**唯讀投影**：把既有狀態（dashboard projection、canonical intake state、prerequisite-gate evidence、parking mirror metadata）集中成一份 machine-readable 快照 | 第二套 pipeline／第二個 backtester／新的狀態儲存 |
| 階段計數的**對帳面**：讓 operator 一眼看出哪個階段有邏輯缺口 | 新的 gate：快照不參與任何 PASS/REJECT 判定 |
| 未來控制面的**前身**：拓撲與 vocabulary 已固定，控制節點尚未接上 | 控制面本體：沒有任何 mutating node、沒有 credentials |
| 沿用既有真值：`runtime/` 語意、`candidate_snapshot.py` 投影、intake state 皆**不重算、不改寫** | 真相來源：真值仍在原本的位置，快照只是投影 |

快照 artifact（host 路徑）：`/Users/hong/workspace/n8n/files/quant-control-plane-shadow.json`
（容器內 `/home/node/.n8n-files/quant-control-plane-shadow.json`，`schema = quant-control-plane-shadow/v1`）。

## 2. Topology：pipeline 階段 ↔ workflow 節點 ↔ 來源

單一 workflow，兩顆 trigger（manual validation + 排程觀測），5 個 read-only 來源節點，1 個 assembler Code node，1 個輸出節點：

```
Manual Trigger ─┐
                ├─→ Pool ─→ Intake Review ─→ Preflight Gate ─→ Candidate/Qlib/Leaderboard ─→ Parking ─→ Assemble ─→ Emit
Schedule (15m)  ┘
```

| # | workflow 節點（節點名即拓撲名） | 讀什麼（唯讀） | 對應 pipeline 階段 |
|---|---|---|---|
| 1 | `Pool — alpha-strategy-research pool (read-only)` | `/host/workspace-ro/alpha-strategy-research` 的 root `*.md`（canonical 規則：`len(parts)==1 and suffix==".md" and not startswith("README")`，即 `review_state.py:111`）＋ checkout HEAD sha | Strategy Research → GitHub alpha-strategy-research pool |
| 2 | `Intake Review — canonical intake state (read-only)` | `/host/workspace-ro/alpha-strategy-review-state.json`（current_snapshot buckets、pending_ingestion、deferred_delta、ingested_wiki_records、last_reviewed_*） | Intake Review → Wiki Brain |
| 3 | `Preflight Gate — prerequisite evidence (read-only)` | `<repo>/evidence/<current_family>-prerequisite-gate-*.json`（僅在與投影 current family **相符**時採用；不符即 unavailable） | Data / Preflight Gate |
| 4 | `Candidate→Qlib→Leaderboard — dashboard projection (read-only)` | `/host/quant-dashboard-data/dashboard.json`（`runtime/candidate_snapshot.py` 產出的既有投影：health／current／funnel／leaderboard） | Candidate Queue → Qlib Full Backtest → Result/Verdict → Survivor → Leaderboard |
| 5 | `Parking — private survivor repo metadata (read-only)` | `/host/workspace-ro/validated-survivor-research`：`survivors/` 目錄數、`leaderboard/leaderboard.json` 的 count／metadata／sha256、mirror HEAD sha（**只有 metadata，不讀 survivor 內容**） | Private Repo Parking |
| 6 | `Assemble shadow snapshot (read-only)` | 以上 5 個來源的 stdout（純解析；Code node 無 fs／無網路） | 全鏈 |
| 7 | `Emit snapshot (n8n shadow dir only)` | 寫入唯一輸出路徑（§6） | 觀測輸出 |

節點實作要點：來源節點 1／2／3／5 為 `executeCommand`（只做 `readdirSync`／`readFileSync`／`SHA-256` 投影，**唯讀、deterministic**），
節點 4 為 `cat`（6.6 KB，verbatim）。沒有 node 會執行 pipeline 腳本、Qlib、backtest 或任何寫入 host 狀態的指令。

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

Intake 分支語意（顯示用，不重判）：`PASS` + 真正完成 ingest 的 `PASS-WITH-CAVEAT` → Wiki Brain（＋ sibling candidate append）；
`REJECT` → 正常篩選終局；`REMEDIATE` → 尚未接受、**不是**系統錯誤；`Error` → 只保留給真正的系統／讀取／解析失敗，**不得**與 reject／remediate 混用。

---

## 4. Counts 與 reconciliation（快照的對帳面）

快照 `counts`（2026-09-21T14:36Z 實跑，逐項可回溯到 `sources[]` 的 path／sha256）：

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

## 5. Future resume policy（**已文件化，未啟用**）

> 若 family A 為 `WAITING_DATA`，後續 candidate 可以繼續跑。當 A 變成 `READY_TO_RESUME` 時，**不要**打斷正在跑的 family；
> 等目前 family 跑完後，`READY_TO_RESUME` 優先於尚未動過的 queue candidates。

Shadow-1 的行為：**只顯示、不執行**（`resume_policy.enabled = false`；`shadow_behaviour = visualise only`）。
`READY_TO_RESUME` 需要重跑該 family 的 prerequisite check 對 canonical raw（`/Volumes/ExpansionDrive/market-data-raw`，本卡刻意不掛載、不執行），
故 `ready_to_resume.value = null` 並附理由；本 workflow 沒有任何節點具備 reorder／delay／interrupt／resume 能力。

## 6. Trust boundaries

**掛載（`container inspect n8n` before/after diff 唯一差異）：**

| host path | container path | mode | 用途 |
|---|---|---|---|
| `/Users/hong/workspace/n8n/data` | `/home/node/.n8n` | rw（**原有**，未變） | n8n 自身持久資料（DB／config／storage） |
| `/Users/hong/workspace/n8n/files` | `/home/node/.n8n-files` | rw（**新增**） | 唯一輸出：shadow snapshot（n8n 自身檔案區，非任何 pipeline 狀態） |
| `/Users/hong/quant-dashboard/data` | `/host/quant-dashboard-data` | **ro** | dashboard projection |
| `/Users/hong/workspace` | `/host/workspace-ro` | **ro** | intake state、pool checkout、repo `evidence/`、parking mirror |

其他 host 狀態（`/Volumes/ExpansionDrive/qlib-results`、`market-data-raw`、Kanban DB、cron、private repo 的寫入面、GitHub）**完全沒有掛載**，
所以 shadow workflow 在結構上**不可能**寫到那些地方。唯讀性另有實測（§9）：`touch /host/...` → `Read-only file system`。

**n8n 設定變更（唯一一項，最小化）：** `NODES_EXCLUDE=["n8n-nodes-base.localFileTrigger"]`。
理由：n8n 2.x 預設停用 `executeCommand` 與 `localFileTrigger`；本 workflow 的 read-only 來源節點需要 `executeCommand` 做目錄列舉／投影，
故以最小集合覆寫預設值——**只**重新啟用 `executeCommand`，`localFileTrigger` 維持停用（實測 `export:nodes` 不含它）。
副作用與界線：`executeCommand` 只能在容器內執行 shell，而容器對 host 的掛載僅有一個 rw 目標（n8n 自身檔案區）；
`Read/Write Files from Disk` 節點另受 n8n 自身限制，只能存取 `~/.n8n-files`（實測：寫到 `/home/node/shadow/...` 被 n8n 拒絕）。回滾見 §7。
**沒有任何 credentials／token 進入 workflow 或 repo**。

---

## 7. n8n runtime contract 與 rollback

容器（Apple Container，image `docker.io/n8nio/n8n:latest` arm64，n8n `2.39.9`）：
host port `127.0.0.1:5678`、持久資料 `/Users/hong/workspace/n8n/data`（未變）、4 CPU／1 GiB、user `node`、`TZ=Asia/Taipei`。
`container inspect` before/after 比對：`image`／`cpus`／`mem`／`env`／`args`／`user`／`workdir`／`ports`／`networks`／`readOnly`／`runtimeHandler` **全部相同**，唯一差異是 §6 的掛載。

重建指令（等同當前狀態；`<...>` 內為 §6 的 mount 四行）：

```bash
container run -d --name n8n -c 4 -m 1024M -u node \
  -p 127.0.0.1:5678:5678 \
  -e TZ=Asia/Taipei -e GENERIC_TIMEZONE=Asia/Taipei -e N8N_LISTEN_ADDRESS=0.0.0.0 \
  -e N8N_RELEASE_TYPE=stable -e NPM_CONFIG_UPDATE_NOTIFIER=false -e NODE_PATH=/usr/local/lib/node_modules \
  -e 'NODES_EXCLUDE=["n8n-nodes-base.localFileTrigger"]' \
  --mount type=bind,source=/Users/hong/workspace/n8n/data,target=/home/node/.n8n \
  --mount type=bind,source=/Users/hong/workspace/n8n/files,target=/home/node/.n8n-files \
  --mount type=bind,source=/Users/hong/quant-dashboard/data,target=/host/quant-dashboard-data,readonly \
  --mount type=bind,source=/Users/hong/workspace,target=/host/workspace-ro,readonly \
  docker.io/n8nio/n8n:latest
```

匯入／執行（檔案自 repo 複製進容器；`N8N_RUNNERS_BROKER_PORT` 只為讓 CLI 與執行中的 server 並存）：

```bash
container copy n8n/quant-control-plane-shadow.workflow.json n8n:/tmp/shadow-wf.json
container exec n8n n8n import:workflow --input=/tmp/shadow-wf.json     # 匯入會把 workflow 設為未啟用
container exec n8n n8n publish:workflow --id=shadowQuantCp1            # 啟用（CLI 會提示需重啟才生效）
container exec n8n n8n list:workflow --active=true                     # 讀回
container exec n8n sh -c 'N8N_RUNNERS_BROKER_PORT=5699 n8n execute --id=shadowQuantCp1'   # 手動驗證執行
python3 n8n/shadow_check.py                                            # 驗證快照（見 §9）
```

**Rollback（任一步都可獨立回退，皆不影響 pipeline）：**

1. 停用觀測：`container exec n8n n8n unpublish:workflow --id=shadowQuantCp1`（或 UI 內把 workflow 關掉）。
2. 移除 workflow：UI 刪除，或重建容器前先 `container exec n8n n8n export:workflow` 備份後再處理；本 repo 只保留匯出檔。
3. 回復容器原狀：以上方指令移除 `NODES_EXCLUDE` 與後三行 mount，只留 `/Users/hong/workspace/n8n/data → /home/node/.n8n`，即為本卡前的原始狀態。
4. 刪除輸出：`/Users/hong/workspace/n8n/files/quant-control-plane-shadow.json`（n8n 自身檔案區，與 pipeline 狀態無關）。

## 8. 取樣節奏與儲存成本（實測）

卡片允許「60s for observation **if safe**」。實測結果如下，因此 shadow-1 採 **900s（15 分鐘）**：

| 設定 | 每筆 execution 儲存量（實測） | 每日 exec 數 | 每日 DB 增量 | 14 天保留窗 |
|---|---|---|---|---|
| 60s ＋ `saveDataSuccessExecution: none` | 1,252 B（但**永不 finalize**：`status` 永遠停在 `running`） | 1,440 | — | 執行列表被幽靈 running 洗版 → **不安全** |
| 60s ＋ 全量儲存（未修剪 payload） | 458,606 B | 1,440 | ~640 MB | **不安全** |
| **900s ＋ 全量儲存（payload 已投影修剪）** | **26,416 B** | 96 | **~2.5 MB** | **~35 MB** ✔ |

三項對策：(a) 來源節點只輸出**投影後**欄位（原本 `cat` 進 payload 的 intake state 210 KB／gate 86 KB／parking 107 KB 不再進入 execution data）；
(b) 開啟成功執行的資料儲存（`all`），使 execution 正確 finalize；
(c) 取樣 **15 分鐘**（`scheduleTrigger` 的 `rule.interval[0] = {field: "minutes", minutesInterval: 15}`，cron-backed），與 pipeline 既有的 15 分鐘 reconciler／watchdog 節奏一致（`candidate_snapshot.py` 本身是 hourly）。
**實測教訓**：本版 n8n（2.39.9）對 `field: "seconds", secondsInterval: 900` 實測仍**每 60 秒**觸發（DB 內已是 900 卻在 14:37:00／14:38:00 連續觸發），因此改用 minutes 單位；
改節奏只需改這一個欄位，但需先接受上表成本或設定 execution 修剪。
註：60s 實驗期間產生的少數 `running` 幽靈列（execution id 4–8）為驗證殘留，未以 SQL 手動改寫 n8n DB；可由 UI 刪除或隨保留策略淘汰。

## 9. 驗證記錄（實跑證據）

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
  （`dashboard.json` 自身由 hourly 既有 cron 重新產生，非本 workflow 所寫。）

## 10. Cutover gates（未來把控制面接上時的前置條件）

此 shadow **不得**在沒有下列明確授權前升級為控制面：

1. 獨立 cutover 卡（operator 明確要求），且不得與本觀測層的節點混用同一份執行路徑。
2. 明確列出「可 mutation 的動作集合」與其 fail-closed 條件（launch／retry／resume 各自的前置檢查）。
3. 寫入面必須落在既有真值的 owner 路徑（`_handoff/candidates.json`、Kanban、`/results`），且先有 atomic-write ＋ read-back ＋ incident 路徑，
   不得由 n8n 自建第二套 candidate／leaderboard／狀態儲存。
4. resume policy（§5）必須由 pipeline 端（reconciler／handoff）實作或明確委派，n8n 只呼叫既有機制，不自帶佇列語意。
5. 通過獨立審計（auditor）後才可啟用 mutating node；本文件與 workflow 目前狀態為 **PROPOSED / SHADOW**，未經審計。

## 11. 刻意不做（避免過度工程）

- 不加第二套 state store／queue／service／daemon；不新增 framework；不引入 credentials。
- 不重寫 `runtime/`、`candidate_snapshot.py`、reconciler、watchdog、handoff、Homepage、Qlib 或任何 backtest 語意。
- 不在 repo 內新增任何可自動觸發的排程（排程在 n8n，不在 repo；本 repo 只有 deterministic 腳本與版本控管）。
- 不 direct-read 需要寫入面的資料夾；`/Volumes/ExpansionDrive/*` 一律不掛載（gap 可接受，不為此建基礎設施）。
- 不調和 §4 第 4 項的 leaderboard／parking 數量差：只呈現缺口，交由 operator 判定。

