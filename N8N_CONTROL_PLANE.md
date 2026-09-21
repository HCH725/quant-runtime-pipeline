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

註：本表是 **2026-09-21T14:36Z 當次實跑**的逐項值；快照是 live 投影，來源變動即反映——
15:45Z 拍點已見 `pool_records_total` / `pool_root_md_total` = **831 / 833**（checkout HEAD `2a34d59`），
差異來自 pipeline 自身的 `Quant Research Scout` cron（job `f5c0648122f3`，15:22Z 新增 1 筆 root `.md`），**不是** shadow 寫入。

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
   結論：這**不是**「SQLite 在 virtiofs 上結構性不可用」（容器內另以 WAL ＋ 4 條 reader ＋ 1,500 筆寫入實測通過），
   而是「不乾淨關機 → 髒 WAL／死 shm → 下一個行程持續 I/O 失敗」。

**修復程序（本卡實測有效；只動 n8n 自身資料區）：**

| # | 指令 | 判讀 |
|---|---|---|
| 1 | `container stop -t 60 n8n` | 給 n8n 完整 graceful 視窗（預設 5s 不夠） |
| 2 | `tail -6 "/Users/hong/Library/Application Support/com.apple.container/containers/n8n/stdio.log"` | 出現 `Shutdown timed out`／`Waiting for N active executions` = 此次停止**不乾淨** |
| 3 | `ls -l /Users/hong/workspace/n8n/data/database.sqlite*` | 仍見 `-wal`／`-shm` = 未 checkpoint |
| 4 | `sqlite3 /Users/hong/workspace/n8n/data/database.sqlite "PRAGMA wal_checkpoint(TRUNCATE); PRAGMA integrity_check;"` | 必須 `ok`；回報 `0\|0\|0` = 已無待 checkpoint 的 frame |
| 5 | `rm -f /Users/hong/workspace/n8n/data/database.sqlite-shm /Users/hong/workspace/n8n/data/database.sqlite-wal` | 丟掉死行程的 shm／空 WAL（**僅能在容器停止時做**） |
| 6 | `container start n8n` → `curl -s http://127.0.0.1:5678/healthz` | `{"status":"ok"}`，開機 log 無 I/O error |

**鐵律：**
- 只在容器**停止**時碰 DB；`rm` 只限步驟 5 那兩個檔案，永不刪 `database.sqlite`。修復前先備份 `database.sqlite*`（本卡留於 `/Users/hong/workspace/n8n/backups/`）。
- **容器在跑時，host 端不得直接開啟 live DB**（含 `sqlite3` 唯讀查詢）：那等於對同一顆 SQLite 引入第二個寫入端。要讀就先在容器內複製一組 `database.sqlite`／`-wal`／`-shm`，再讀**副本**——repo 內的唯讀探針即為此法：
  `container exec n8n node /host/workspace-ro/quant-runtime-pipeline-n8n/n8n/tick_probe.js`（複製到容器內 `diag/probe/` 後讀副本，永不寫 live 檔）。
- `import:workflow` 之後必須重新啟用並**重啟**：CLI 明示 `Changes will not take effect if n8n is running. Please restart n8n…`。重啟一律走本節步驟 1–6。

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
| **900s ＋ 全量儲存（payload 已投影修剪）** | **26,815 B**（exec 12–15 實測；最早一拍 exec 11 為 26,416 B） | 96 | **~2.5 MB** | **~35 MB** ✔ |

三項對策：(a) 來源節點只輸出**投影後**欄位（原本 `cat` 進 payload 的 intake state 210 KB／gate 86 KB／parking 107 KB 不再進入 execution data）；
(b) 開啟成功執行的資料儲存（`all`），使 execution 正確 finalize；
(c) 取樣 **15 分鐘**（`scheduleTrigger` 的 `rule.interval[0] = {field: "minutes", minutesInterval: 15}`，cron-backed），與 pipeline 既有的 15 分鐘 reconciler／watchdog 節奏一致（`dashboard.json` 投影本身由 cron `3d2e54e178ff` 每 5 分鐘重算）。
**實測教訓**：本版 n8n（2.39.9）對 `field: "seconds", secondsInterval: 900` 實測仍**每 60 秒**觸發（DB 內已是 900 卻在 14:37:00／14:38:00 連續觸發），因此改用 minutes 單位；
改節奏只需改這一個欄位，但需先接受上表成本或設定 execution 修剪。
註：60s 實驗期間產生的 `running` 幽靈列（execution id 3–8、10）為驗證殘留，**未以 SQL 手動改寫 n8n DB**，已由 n8n 自身的 pruning 清除。
execution 列的 id 區間**只在本文件 §9.B 的〈execution 現況〉寫一次**；本節先前另寫一份（12–15），與 §9.B（12–19）及 A2 表（exec 20）三方漂移，已收斂。

## 9. 驗證記錄（實跑證據）

> 第一輪（§9.A）＝部署與投影；第二輪（§9.B）＝2026-09-21T15:36Z 起的 DB 修復、節奏復活與 review round-1 要求項；第三輪（§9.C）＝review round-2 要求項（CLI 契約、文件漂移）。

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

截至 **2026-09-21T17:00:25Z**（第 6 拍、exec 21 落地後）以唯讀探針讀取：`total = 14`、`running = 0`、`max_id = 21`。

| status | n | execution id |
|---|---|---|
| `crashed` | 1 | 1（早期 CLI） |
| `error` | 1 | 2（早期 CLI） |
| `success` | 12 | 9・11（CLI 手動驗證）、12–13（60 s 節奏）、14–15（`minutes/15` 切換後首批 900 s）、16–21（修復後 900 s 排程拍點） |

此表隨每 15 分鐘拍點前進；取當下值一律用 §7.1 的唯讀探針
（`container exec n8n node /host/workspace-ro/quant-runtime-pipeline-n8n/n8n/tick_probe.js`），**不在 host 端開 live DB**。
§8 與本節 C2／C3 只指向本段，不各自複寫 id 區間（先前三方各寫一份而漂移，見 §8 註）。

**C1 — 節點名稱對齊 live／repo**：`n8n export:workflow --id=shadowQuantCp1`（重啟後）與 repo 匯出在 `id`／`name`／`nodes`／`connections`／`settings`／`active`
六個鍵上**逐鍵相同**（差異集為空）→ 節點名 `Schedule — 15m observation`，參數 `{field: minutes, minutesInterval: 15}`。

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

- `--selftest` **非恆真**：改寫過程中它當場抓到作者自己的 `argv` 索引錯誤（`['--require-fresh']` 被跳過）→ 修正後才轉 PASS。

**A2 — 文件漂移（已收斂為單一權威段落）**

- §8 與 §9.B C2／C3 不再各自複寫 exec id 區間，兩處都指向〈execution 現況〉；該段基準 = 2026-09-21T17:00:25Z 唯讀讀值（`total = 14`、`running = 0`、`max_id = 21`、`success = 12`），與 A2 表（exec 16–20）＋ exec 21 一致。
- 同輪順帶修掉同節內另一處自相矛盾：A2 標題「連續 4 拍」→「連續 5 拍」（表有 5 列、窗 60 分、該節內文本來就寫 5 拍）。

**本輪範圍**：只改 `n8n/shadow_check.py` 與 `N8N_CONTROL_PLANE.md` 兩個檔案；未重啟容器、未重新 import workflow、未觸發任何 n8n execution、未動 pipeline／cron／`qlib-run`／任何權威狀態。

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

