# QUANT RUNTIME PIPELINE — IMPLEMENTATION CONTRACT (SOP)

文件狀態：**AWAITING AUDIT**（v1.2.0；前一版 v1.1.1 = AUDITED PASS / FROZEN，audited content commit 18d6c3f，auditor re-audit t_83682069，2026-09-13；更前一版 v1.0.1，auditor re-audit t_e35c39c0，2026-09-12）
版本：v1.2.0（2026-09-13）
作者：Hermes default（小蒨），依 ChatGPT（GPT-5.6 Sol）卡片 t_5b5b38d6 定版；v1.0.1 remediation 依 t_bcedaf65（audit t_a3dc355d B1–B3）；v1.1.0 依 t_ec039d5f（Nautilus 語意校正、full-backtest 定義、P8 interpreter 修正、最小 runtime readiness 落地）；v1.1.1 依 t_4d6c5cd5（audit t_d7f48c7a 的 F1–F3 最小 remediation 與文件精度修正）；v1.2.0 依 t_0626a619（新增 §14.4 automatic production handoff trigger）
適用範圍：quant-strategy-research board 之 Qlib 研究 runtime 與 Kanban 交接
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
- `[C]` 現行 production 的計算面與效能真值來源只有 Qlib container 的 full-backtest（§7.2）。Nautilus（或任何第二套 backtester）為 **future / out-of-scope / non-blocking optional integration**（§17）：它不是已建立、必經、authoritative gate，也不是 performance claim gate；它不存在**不得**使現行 Qlib full-backtest 降級為 `research-only`，也不得成為第二套全量參數搜尋引擎。
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

## 7. 卡間狀態機（A→B→C ordering chain）

- `[C]` strategy cards 以 append-only ordering chain 序列化：A → B → C。新 family 只能接在 tail。
- `[C]` 實作方式：`kanban_create(..., parents=[<tail card id>])`。`[V]` 子卡在父卡 done 前停在 `todo`，父卡 done 後才 auto-promote `ready`。
- `[C]` **parent edge 只代表 scheduling/order**。不得描述為 scientific lineage、不得描述為 approval gate、不得描述為「B 是 A 的改良版」的證據。
- `[C]` scientific lineage 一律寫在 `/results/<family_id>/family.json`：`parent_family`（family_id 或 `null`）、`lineage_note`。這兩者與 Kanban parent edge 無關，可不同；卡片本身不承載 metadata。
- `[C]` 鏈上活動卡數：同一時刻整條研究鏈只應有 1 張 active strategy card（chain head）。其餘為 `todo`（等父卡）或 `done`。
- `[V]` 目前 `quant-strategy-research` board：`archived=66`、`running=1`、`todo=1`，即舊 LEAN/Nautilus 卡已全數 archive、active strategy baseline = 0（running/todo 為本 contract 卡與其 audit 子卡）。
- `[C]` 禁止 live rewiring：不得在鏈中間插卡、不得改既有 parent edge 以「優化順序」。要調整順序只能 tail append 新卡，並在其 `family.json` 的 `lineage_note` 說明。

### 7.1 chain head 判定（確定性）
- `[C]` chain head = 在 `quant-strategy-research` board 上、狀態 ∈ {`ready`,`running`,`scheduled`}，且被某個 `/results/<family_id>/family.json` 的 `kanban_task_id` 反查命中的卡（即 strategy card）。**不以卡片 metadata 判定**（`[V]` kernel 無 metadata 欄）。
- `[C]` 若同時存在 >1 張，屬 INV-3/§7 破壞 → 走 §12.6 incident：不自行挑一張跑，且不得對 `scheduled` 卡直接 `block` 或 `promote`。
- `[C]` 若某張 `ready` 卡尚無對應 `family.json`（tail append 同一輪進行中）：不得投遞 Qlib；claim 後必須先確認 `family.json` 已落地，否則走 §12.6 incident。
- `[T]` 上述判定尚未有腳本化查詢；落地前由 default 每輪以 DB 讀回 + `/results/*/family.json` 掃描人工確認。

### 7.2 全量回測（full-backtest）定義與 production 模式

- `[C]` **全量回測**：一張 strategy card 在其**預先定義的 eligible universe** 內完整覆蓋下列五個維度，才可宣稱完成一次 full-backtest：
  1. `symbols`：該卡事先註冊的合格 symbol 集合（不得事後擴張或挑選）。
  2. `timeframes`：事先註冊的全部頻率。
  3. `parameter domain`：事先註冊的完整參數域（§10.2 `params`），不是單點試算。
  4. `DCA execution`：以該卡註冊的 DCA（定期定額／分批進場）執行語意實算，不是事後推估。
  5. `historical / OOS / robustness`：in-sample 歷史段、明確 out-of-sample 段、robustness（參數鄰域／敏感度）三者皆完成。
- `[C]` eligible universe 的註冊位置是 `/results/<family_id>/rounds/<round_id>/round-spec.json`（immutable）；該卡的完成判定以 §15 yield policy 與該 round 的 `verdict.json` 為準。
- `[C]` 現行 production 計算面就是 Qlib container（§3/§9）；full-backtest 的完成與可宣稱性**不依賴** Nautilus 或任何下游 acceptance 階段（§9.6/§17）。
- `[C]` **production 序列是 sequential A→B→C**：A 卡完整結案（`done`，任何 verdict）後才進 B，B 完整結案後才進 C；同一時刻只有一張 active strategy card（§7）。
- `[C]` 多 family 一次全 universe 混跑**不是** production 模式，也不得作為 production 效能證據；若要探索，必須是獨立研究卡，並在該 family 的 `family.json` 的 `lineage_note` 載明其非 production 身分。

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

### 9.5 為何不用 HTTP / webhook / Redis / Celery / queue
- `[C]` 這些都需要常駐服務或網路信任面，會引入：新 daemon、新 failure mode、新 secret、新 port、新 restart 邏輯；而本 pipeline 的 completion 訊號本質是一個「至少一次、可重讀」的檔案事件。
- `[C]` 檔案 + 冪等 unblock 已滿足需求，且符合最小設計原則。任何以此為由的擴張提案都應被駁回。

### 9.6 performance_claimable 條件（全部成立才 true）
- `[C]` verdict = `PASS`；round 的 spec 事先註冊且未被事後修改；使用 canonical raw（非合成/非替代資料）；無 look-ahead（PIT 檢查通過）；樣本外或明確 out-of-sample 區間；成本/滑價假設已載明；且 §7.2 的 eligible universe 覆蓋（symbols × timeframes × parameter domain × DCA execution × historical/OOS/robustness）已依 round artifacts 完成。
- `[C]` 上列條件齊備即可 `performance_claimable=true`，**不以下游 authoritative acceptance（§17；Nautilus 或等價階段）為前提**：該階段目前 future/out-of-scope、non-blocking，未執行不影響現行 Qlib full-backtest 結論的可宣稱性，也不得據此把結論降級為 `research-only`。
- `[C]` 未同時滿足者，`performance_claimable=false`，並在該 round 的 `verdict.json` 的 `missing_conditions` 註明缺哪一項。`missing_conditions` 不得以「缺下游 acceptance」為理由。

## 10. Artifact schemas

### 10.1 目錄佈局（canonical）

```
/results/<family_id>/
  family.json                                  # immutable：ownership + lineage（見 §10.6）
  state.json                                  # atomic rewrite 唯一允許
  rounds/<round_id>/
    round-spec.json                           # immutable：預先註冊的門檻/參數域/falsification + kanban_task_id
    verdict.json                              # immutable：該 round 的 verdict / performance_claimable / yield 判定（見 §10.7）
    attempts/<run_id>/
      run-spec.json                           # immutable：本 attempt 的輸入契約
      result.json                             # immutable：計算結果摘要
      artifacts/…                             # immutable：tables/plots/中間彙總（可多檔）
      DONE | FAILED | INCOMPLETE              # immutable terminal sentinel（atomic publish，最後）
      logs/                                   # 允許非必要、可重跑產生的 log
      state.json                              # atomic rewrite：本 attempt 的進度 stage
```

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
  "params": {"…": "本 run 的參數域/單點"},
  "costs": {"fee_bps": null, "slippage_bps": null},
  "script": {"path": "/scripts/…", "sha256": "…"},
  "expected_outputs": ["result.json", "artifacts/summary.csv"],
  "falsification": ["…預先註冊的否證條件…"],
  "notes": "…"
}
```

- `[C]` `script.sha256` 必填：保證「同一 spec 指向同一份程式」。
- `[C]` `falsification` 必須在計算前寫定（預先註冊）；事後補寫視為無效。

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
1. `[C]` attempt 目錄有 terminal sentinel → 該 run 已終結；若卡片仍 `scheduled`，走 reconciler 放行（§9.4）。
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
  2. **寫 host-side incident artifact（append-only）**：`/results/_incidents/reconciliation_incident.jsonl`，每行一筆 JSON，至少含 `schema_version`、`incident_id`、`detected_at_utc`、`detector`（`reconciler|default|operator`）、`kanban_task_id`、`family_id`、`round_id`、`run_id`、`observed_status`、`kind`（`multiple_terminal|checksum_mismatch|sentinel_ambiguous|mapping_mismatch|stale_sentinel|invariant_break|duplicate_chain_head`）、`evidence_paths`、`host_boot_id`。不得改寫既有行；`/results/_incidents/` 為保留目錄，不得作為 family_id。
  3. **告警 + 要求人工介入**：在卡片留一則 comment（開頭 `incident:`）指向該 artifact；層級判定（card-local vs shared-layer）由 operator/default 事後決定。
- `[C]` 若人工判定確實需要把該卡轉為 `blocked`：必須由 operator/default 在**後續顯式流程**先 `unblock`（`scheduled`→`ready`/`todo`）再 `block`，兩步之間各自留下 DB 讀回證據。**此 two-step 不得包成 reconciler 自動動作**（避免競態與繞過 gate）。
- `[C]` incident 未結案前：該 family 不得投遞新 run，也不得 append 新 family。
- `[C]` 這不是新服務/daemon：incident artifact 只是檔案契約，偵測者是既有的 reconciler/default。

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

- `[C]` 判定層級的判準：**「同一動作在另一張卡上是否也會失敗？」** 會 → shared-layer；只在此卡 → card-local。
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

### 14.4 Automatic handoff trigger（v1.2.0）

- `[C]` **ownership**：automatic handoff 由 Hermes **default** 擁有，執行者是 host 端 deterministic 的**單一 no-agent script-only cron**（不是 daemon/service/watch，INV-8/§1.2 不變）。每一輪只做「檢查 → 必要時 append 1 張 → 結束」：不做長 turn wait、不跑回測、不判 verdict、不改 body、不建 auditor 卡。
- `[C]` **入口**：repo `runtime/production_handoff.py`（純 stdlib，支援 `--dry-run`、`--json`）；scheduler 端入口 `~/.hermes/scripts/quant_production_handoff.py` 只以 `runpy` 呼叫 repo 版本（repo 是唯一 source of truth，不複製邏輯）。stdout 語意：只有「真的 append」或「去重後的新 finding」才輸出，正常 no-op 完全靜默；exit 0 = ok、2 = usage error、其他 = 真正異常（由 cron 依其既有語意告警）。
- `[C]` **append 條件（全部成立才 append；順序即判定順序）**：1. `/results` 存在；2. board 可讀（DB 讀回）；3. **無任何 active strategy card**（`ready`/`running`/`scheduled` 的 chain head）；4. board 上**無 `blocked` 卡**（freeze / human gate，§12.5/§12.6）；5. `_incidents/reconciliation_incident.jsonl` **無未結案 incident**（其卡仍非 terminal，§12.6）；6. 存在 strategy card（由 `/results/*/family.json` 的 `kanban_task_id` 反查命中）且依 `created_at` 排序的 **tail 為 `done`/`archived`**（§14.1）；7. pool 內存在尚未出現於 `/results` 的候選（family 目錄不存在且 `semantic_fingerprint` 不在既有集合，§14.3）。
- `[C]` **候選來源 = reviewed pool**：`/results/_handoff/candidates.json`（保留目錄 `_handoff`，與 `_incidents` 同層）。每個 entry 至少含 `family_id`、`title`、`fingerprint_input`（正規化字串，供 fingerprint 重算）、`card_body` 或 `card_body_file`、`lineage_note`、`parent_family`、`provenance.reviewed_source`。**pool 是檔案契約、不是 Registry service**：消費狀態一律由掃描 `/results` 推導，handoff 不重寫 pool；同一 pool 內重複 `family_id`／fingerprint 即 `ambiguous_pool`（fail-closed）。候選必須來自**已 review** 的來源（Hermes wiki brain 已 review 紀錄 / 已 review 的 research intake / board archived research evidence）；未 review 的 intake 不得直接進 pool。pool 由 default 在 research/review 產出時追加；空或無可用候選 → 只留 finding，不建卡。
- `[C]` **append 動作**：`hermes kanban create`（`--parent <tail_id>`、`--assignee default`、`--priority 100`、`--workspace dir:<repo>`、`--completion-contract local-only`、`--goal`、`--idempotency-key <family_id>`），**同一輪最多 1 張**；建立後立即 `show` 讀回確認 `parents` 含 tail，再以 `O_EXCL` 寫 `/results/<family_id>/family.json`（§10.6；`semantic_fingerprint` 由 `fingerprint_input` 重算）並讀回驗證。`idempotency_key` 使 crash 後重跑收斂到同一張卡（不重複建卡）。
- `[C]` **失敗語意（fail-closed，不爆量重試）**：任何一步不成立一律**不建卡**，只在 `/results/_handoff/handoff_log.jsonl`（append-only）留一筆 finding；`finding_key` 未變則不再輸出（去重），kind 改變才再告警。kind 至少含 `results_root_missing`、`board_unreadable`、`family_card_missing`、`blocked_card_present`、`unresolved_incident`、`no_tail_card`、`tail_not_terminal`、`pool_missing`、`pool_invalid`、`ambiguous_pool`、`no_eligible_candidate`、`create_failed`、`readback_failed`、`family_json_failed`。卡已建立但 `family.json` 寫入失敗時**不刪卡**，由 finding 要求人工介入；該卡 claim 後仍必須先讀回 `family.json`，缺失即依 §7.1/§12.6 fail-closed。
- `[C]` **不改既有語意**：本節不新增 Manager/Service/Factory/Registry/Orchestrator、daemon、queue 或第二套 runtime；不改 §7/§9.4/§11/§12/§14.1–14.3/§15 的規則，也不改 §14.2 的 tail append 演算法、fingerprint 規則與 `family.json` rules。
- `[C]` **auditor 不是 production stage**：每張正式 strategy card 的流程只有 Hermes default（執行）+ §9.4 reconciler（放行）+ 本節 handoff（接續下一張）。**不新增 auditor 子卡、不把 auditor 列為任何 strategy card 的階段或 gate**；auditor 只在 operator/ChatGPT 另行開卡時獨立稽核。
- `[C]` **fence**：append 必須在無 `HERMES_DELEGATED_CHILD_CONTEXT` 的 host context 執行（§9.4；`hermes kanban` 對該標記一律拒絕 board 變更）。cron 由 gateway 直接執行腳本，故不需、也不得為此新增服務。
- `[V]` 2026-09-13 邏輯層與 dry-run 實測：`python3 runtime/tests/test_production_handoff.py` 19/19 OK（注入式 kernel 讀回／create，涵蓋唯一放行條件、dry-run 不變更、idempotency、以及全部 fail-closed 分支）；`python3 runtime/production_handoff.py --dry-run --json` → `would_append`（tail = `t_97208408`）。
- `[V]` 2026-09-13 第一次真實 handoff（A 已 `done(REJECT)`）：由 fence-free host context 執行 → append family `ema-crossover-walkforward-momentum-long-short-v1` 為卡 `t_3e696dce`（`parents=[t_97208408]`、`ready`、`idempotency_key` = family_id），`family.json` 同輪落地並讀回；再跑一輪 → `noop`（active strategy card），無重複卡；dispatcher 於同分鐘自動 claim（`ready` → `running`），即自動 append 的卡不需人工 promote 就進入 production 執行。
- `[V]` 2026-09-13 cron 掛載（default profile）：job `624d0be5b23c`「Quant production handoff (A→B→C)」、`5 * * * *`、`no-agent`（script only）、deliver `discord:1519163199117721650`；scheduler 端入口 `~/.hermes/scripts/quant_production_handoff.py`；gateway 行程環境無 `HERMES_DELEGATED_CHILD_CONTEXT`（實測，故 cron 端為 fence-free）。
- `[T]` 尚未在「tail 已 terminal 且 pool 有候選」的真實狀態下由 cron tick 自動 append 一次（首次 append 是 operator/default 手動觸發的 one-shot；B 目前 `running`，§14.1 不允許提前 append C）。B terminal 後的 cron tick 即為此路徑的真實驗收。

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
| P10 | run-spec 已 immutable publish | `run-spec.json` 存在且欄位合法；`script.sha256` 由 host 端**實際重算**後相符（`/scripts/<name>` 以既有 host scripts mapping 解析，見 §16.4）。無法解析或無法重算 → `FAIL`／NOT VERIFIED，**不得 PASS**（launch gate 不通過） | card-local |

- `[V]` P1–P10 已包成單一腳本 `runtime/preflight.py`（見 §16.4）。`[V]` 實測 2026-09-13：`python3 runtime/preflight.py` → P1–P8 全 PASS（P9/P10 在未給 `--attempt-dir` 時為 `N/A`）；`container exec qlib-run /opt/venv/bin/python -c "import qlib; print(qlib.__version__)"` → `0.9.7`；`container exec qlib-run /usr/local/bin/python -c "import qlib"` → `ModuleNotFoundError: No module named 'qlib'`（此即裸 `python` 誤判的來源）。`container ls` → `qlib-run  qlib:0.9.7-arm64  linux  arm64  running  6 CPU / 4096 MB`；`container --version` → `1.4.1`；`/Volumes/ExpansionDrive/{market-data-raw,qlib-results}` 皆存在。
- `[C]` P7 可機械修復：`/qlib/work` 可重建（INV-5），因此不屬於 shared-layer freeze 條件。
- `[V]` preflight 已包成單一腳本：`python3 runtime/preflight.py [--attempt-dir <dir>] [--launch] [--json]`。輸出每個 P# 的 PASS/FAIL/NA 與 `overall`，exit code 0 = 全數 evaluated 檢查 PASS，1 = 有 FAIL，2 = 使用錯誤。`--launch` 必須搭配 `--attempt-dir`（否則 P9/P10 無法評估，直接拒絕執行）。

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

## 17. FINALIST 之後：下游 authoritative acceptance（future / out-of-scope / non-blocking）

- `[C]` **本節不是現行 production 的必要階段。** 現行 production 的效能真值來源是 §7.2 的 Qlib full-backtest 及其 round `verdict.json`（§9.6）；本節描述的階段目前 **out-of-scope**，任何一張卡都不得因為它不存在而被降級、被標 `research-only`、或停在未終結狀態。
- `[C]` `FINALIST` 的語意：研究鏈的科學任務結束，survivor 被凍結。**下一個 family 立即放行**（不等待本節階段）。
- `[C]` frozen survivor 的定義：`PASS` 的參數集 + 其 round/run 的 immutable artifacts + `performance_claimable=true` 的研究結論，以 `frozen_at_utc` 與 checksum 釘死。
- `[C]` 未來若引入此階段（例如以 Nautilus 實作），它**只能**：
  1. 對**已凍結**的 survivor 做獨立真值驗證：獨立資料源/獨立 fill model/獨立成本模型。
  2. 回報 `ACCEPTED` / `REJECTED` / `DISPUTED` 與差異來源。
- `[C]` 該階段 **不得**：重新搜尋參數、改 hypothesis、擴參數域、以自身流程產生新候選、成為第二套全量 search engine；也不得成為 launch/終結/稽核的前置 gate。
- `[C]` 該階段的結論是獨立第二意見，**不得自動反向改寫** Qlib round 的 `verdict.json`（INV-4 immutable）。若出現衝突，屬語意層決策 → 走 §26 change control，由 operator 決定採信層級；本文件不預設「以未來階段為 authoritative」。
- `[C]` 未引入此階段完全不妨礙：full-backtest 完成、`performance_claimable`（§9.6）、family 終結、下一 family 放行、已凍結 survivor 的引用。
- `[V]` Nautilus 目前**未安裝**（本機零痕跡）；本節是 out-of-scope 備忘，不是現況描述，也不是待辦 blocker（附錄 B T9）。
- `[T]` 未來若要實作，屆時另立契約；本節不構成任何實作義務。

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
1. `[V]` R0：文件凍結 → audit → operator 核准（v1.0.1 = AUDITED PASS / FROZEN，auditor t_e35c39c0；v1.1.1 = AUDITED PASS / FROZEN，audited content commit 18d6c3f，auditor re-audit t_83682069，2026-09-13；前次 v1.1.0 = AUDITED FAIL @ audit t_d7f48c7a）。
2. `[V]` R1：preflight 腳本化（P1–P10），只讀，不投遞 → `runtime/preflight.py`（2026-09-13 實測）。
3. `[T]` R2：單一 smoke run（非策略）走完 `ready→running→scheduled→sentinel→unblock→ready`。**部分已驗證**：sentinel 產生/驗證、fail-closed 分支、以及 `scheduled→ready` 的判定邏輯已實測（fixture + `runtime/tests/test_reconcile.py`）；**真的放行一次**尚未執行，因為放行需要 `scheduled` 卡 + 無 fence 的 host context（§9.4），而本卡執行環境（kanban worker session）被 Hermes 拒絕 board 變更。`container exec` 投遞段的真實 Qlib smoke 計算同樣尚未執行（不在 t_ec039d5f 範圍）。
4. `[T]` R3：reconciler 腳本化（no_agent cron），以既有 sentinel 做 dry-run 對帳 → `runtime/reconcile.py --dry-run` 已可執行（2026-09-13 實測）；但 cron 的正式掛載（以及 apply 的第一次真實放行）仍待 operator 在**無 fence 的 host context**完成，見 §9.4 的執行環境限制。
5. `[T]` R4：第一張正式 strategy card（family A）全流程；觀察 yield policy 紀錄。
6. `[C]` 每階段完成後必須有 DB 讀回證據；階段未過不得前進。

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
- **A13** FINALIST 後下一 family 立即放行；下游 authoritative acceptance（Nautilus）明文為 future/out-of-scope/non-blocking，其不存在不得使現行 Qlib full-backtest 降級為 `research-only`。`[C]`
- **A14** 未新增 Manager/Service/Factory/Registry 類抽象；未新增 daemon/service/queue（`family.json`/`verdict.json` 為檔案契約，非 Registry service）。`[C]`
- **A15** 文件同時含：狀態機（卡內/卡間）、lifecycle、schemas、idempotency、reboot/orphan recovery、failure taxonomy、NEW_FAMILY、yield、preflight、durable state/comment conventions、observability/security、audit checkpoints、rollout/drills、acceptance、auditor checklist、worked examples、禁止事項、change control。`[C]`
- **A16** 文件不存在任何「以 task-level metadata 作 durable state」的要求；ownership/lineage/verdict 落點為 `/results` artifact（`family.json`/`round-spec.json`/`verdict.json`）。`[C]`
- **A17** 文件不存在對 `scheduled` 卡直接 `block` 的要求；衝突一律走 §12.6 incident（保持 `scheduled`、不 unblock、寫 incident artifact、人工介入）。`[C]`
- **A18** 單一 `/qlib/work` volume 故障一律 card-local（可重建、同 round 新 run_id、不 freeze）；只有系統性 shared-layer 故障才 freeze。`[C]`
- **A19** 「全量回測」有明確定義（單一 strategy card 的 eligible universe 覆蓋 symbols × timeframes × parameter domain × DCA execution × historical/OOS/robustness），且 production 模式明寫 sequential A→B→C、多 family 混跑不是 production。`[C]`
- **A20** production blocker 與 deferred hardening 已分級（附錄 B / §21.2）：D1–D13 與 T5–T8 等不得被當成第一張 strategy card 的前置 gate；只有 launch/終結/稽核不可缺的最小項才算 blocker。`[C]`

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
FINALIST → frozen_survivor.json (checksum 釘死) → 下一個 family B 立即 ready
                                        ↘ （future，可選）另開 acceptance 卡（assignee 依屆時契約）
```
- `[C]` 現行 production 在 FINALIST 之後**只需要**放行下一個 family；acceptance 階段不存在也不影響任何 verdict 或 claim。
- `[C]` 未來若真的開卡：acceptance 卡只能驗證 frozen survivor；若其結果 `DISPUTED`，不得回頭改 F-r* 的 artifacts（走 §26 / §17）。

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
- `[C]` 禁止 Nautilus（或任何下游階段）重新搜尋參數/改 hypothesis/成為第二套全量 search engine；亦禁止把它當成現行 production 的 gate（§17）。
- `[C]` 禁止新增 Manager / Service / Factory / Registry 類抽象。
- `[C]` 禁止以「缺下游 authoritative acceptance」為由把現行 Qlib full-backtest 降級為 `research-only`，或把它列為第一張 production card 的 gate。
- `[C]` 禁止把 comment 當唯一 state。
- `[C]` 禁止把 task-level metadata 當 state（`[V]` kernel 的 `tasks` schema / `Task` model 無 `metadata`；`task_runs.metadata` 屬 closing run，不是 card runtime state）。
- `[C]` 禁止對 `scheduled` 卡直接 `block`（`[V]` kernel 亦拒絕）；衝突一律走 §12.6 incident。
- `[C]` 禁止把單一 work volume（`/qlib/work`）故障當 shared-layer freeze（§12.5）。
- `[C]` 禁止把 `[T]` 條目當作已具備能力引用。
- `[C]` 禁止同一張卡自我審計。
- `[C]` 禁止在 comment/artifact 或任何卡片欄位寫入 secrets 或 PII。

## 26. Change control

- `[C]` 修改本文件需滿足其一：(a) operator 明確指示；(b) ChatGPT 規劃卡明示；(c) auditor 提出 blocking evidence 後的 default remediation（且需 re-audit）。
- `[C]` 版本遞增規則：條款新增/語意變更 → minor 版 ++；狀態機、trust boundary、completion bridge 變更 → major 版 ++ 並在文件頂端記錄 supersede 說明。
- `[C]` 每次變更必須在文件末尾的變更記錄留下：版本、日期、變更條號、理由、驗證方式。
- `[C]` 不得為了文采/語序順暢而重寫固定段落：本文件的前置固定段（§0–§5）視為凍結前綴；新增優先追加於對應段落尾端或新章節。
- `[C]` 變更後必須重跑 §22 的 A1–A18 自檢與 auditor checklist（§23）。

---

## 附錄 A：本文件引用的既有證據

| 項 | 來源 | 內容 |
|---|---|---|
| E1 | `container ls`（本機即時） | `qlib-run  qlib:0.9.7-arm64  linux  arm64  running  6 CPU / 4096 MB` |
| E2 | `container --version` | `1.4.1 (build: release, commit: 9a8917c)` |
| E3 | `/Volumes/ExpansionDrive/` | `market-data-raw`、`qlib-results` 皆存在 |
| E4 | `hermes_cli/kanban_db.py` | `VALID_STATUSES`、`promote_task`、`schedule_task`、`unblock_task` 語意 |
| E5 | `qlib-apple-container-audit-t_ec81af5e.md` | mount ro/rw、raw 唯讀拒絕寫、Qlib 0.9.7 arm64、volume 30 GiB |
| E6 | `quant-strategy-research` board DB（唯讀查詢） | `archived=66`, `running=1`, `todo=1`（active strategy baseline = 0） |
| E7 | `qlib-apple-container/Containerfile` | `QLIB_TAG=v0.9.7` + `QLIB_COMMIT=da920b7f…` + build 內 rev-parse 守衛 |

## 附錄 B：仍需實測（TO-BE-VALIDATED 總表）

`第一張 production card` 欄 = 開跑第一張正式 strategy card 前是否必須具備（`BLOCKER`）或可延後（`DEFERRED`／`OUT-OF-SCOPE`）；判準見 §21.2。

| # | 項目 | 卡在哪 | 第一張 production card | 驗證方式 |
|---|---|---|---|---|
| T1 | reconciler 腳本（no-agent） | **已落地（最小版）** `runtime/reconcile.py` | — | R3 dry-run 對既有 sentinel 對帳 + `runtime/tests/test_reconcile.py` 23/23（2026-09-13 實測；v1.1.1 追加 consumed-first 與 family/round/run/container identity 覆蓋） |
| T2 | preflight 腳本 P1–P10 | **已落地（最小版）** `runtime/preflight.py` | — | R1 全綠輸出可重現（2026-09-13 實測，P1–P8 PASS；P9/P10 與 P10 sha 重算之邏輯層檢查 `runtime/tests/test_preflight_p10.py` 10/10） |
| T3 | sentinel/checksum 產生器 | **已落地（最小版）** `runtime/terminal_evidence.py` | — | fixture 產出、`check` 重算相符、重複 publish 被拒（INV-15）（2026-09-13 實測） |
| T4 | artifact 目錄 schema 實際落地 | 尚無正式 family 目錄 | `BLOCKER`（隨第一張卡產生） | R4 第一張 strategy card |
| T5 | fingerprint 計算腳本 | 未實作 | `DEFERRED`（§14.3 允許手算 + auditor 重算） | `family.json` 的 `fingerprint_input` 由 auditor 唯讀重算比對 |
| T6 | yield 判定自動化 | 未實作 | `DEFERRED`（§15.4 明訂人工判定可稽核） | 以 round 數與 artifacts 人工判定後核對 |
| T7 | chain head 判定查詢 | 未實作 | `DEFERRED`（§7.1 明訂人工 DB 讀回） | DB 讀回人工確認 |
| T8 | 13 項 failure drills（D1–D13） | 未執行（reboot/resume 核心路徑已於 §12/§21.2 語意確認不被阻擋） | `DEFERRED`（不是第一張的 gate，§21.2） | §21.2 |
| T9 | Nautilus authoritative 階段 | 未安裝 | `OUT-OF-SCOPE`（非 blocker，§17） | 屆時另立契約 |
| T10 | 多筆 terminal evidence / checksum 衝突的仲裁 | 契約已定（§12.6）+ fail-closed 路徑已實作於 `runtime/reconcile.py`（含 incident JSONL 寫入）；未實戰 | `DEFERRED`（fail-closed 已可執行） | D3/D4/D5 變體 + D13；驗證 incident artifact 落地 |
| T11 | `/results/*/family.json` ownership/lineage 落地 | 尚無正式 family | `BLOCKER`（隨第一張卡產生） | R4 第一張 strategy card 時檔案落地，auditor 可唯讀重算 fingerprint |
| T12 | round `verdict.json` 與 incident artifact 寫入器 | incident 寫入器**已落地（最小版）**於 `runtime/reconcile.py`；`verdict.json` 仍由 default 人工寫入（維持最小設計） | `DEFERRED`（verdict.json 為 default 的判斷動作，不是自動化缺口） | 事後以檔案讀回驗證（欄位齊備、JSON 可解析） |

## 附錄 C：變更記錄

| 版本 | 日期 | 變更 | 理由 |
|---|---|---|---|
| v1.0 | 2026-09-12 | 初版定版（本卡 t_5b5b38d6） | ChatGPT 規劃；新增 Family Yield（§15）與 Execution Preflight（§16）兩條正式護欄 |
| v1.0.1 | 2026-09-12 | **B1**：廢除 task-level metadata 作為 durable state，ownership/lineage/verdict 改落 `/results`（新增 §10.6 `family.json`、§10.7 `verdict.json`；改寫 §9.4 reconciler 入口、§14、§18.1、INV-4/9/10/17）。**B2**：新增 §12.6 conflict/incident 流程（`scheduled` 不得直接 block），統一 §6.3/§7.1/§12.3/§12.4/§12.5。**B3**：`/qlib/work` 單一 volume 故障一律 card-local，統一 §12.5/§13/§16 | auditor t_a3dc355d 三個 blocking findings 的最小 remediation（本卡 t_bcedaf65） |
| v1.1.0 | 2026-09-13 | **A 語意校正**：§1.2 改寫（Nautilus 為 future/out-of-scope/non-blocking，非 authoritative gate）；新增 §7.2「全量回測定義與 production 模式」；§9.6 移除「未有下游 acceptance 只能 research-only」的降級條款；§17 全面改寫為 out-of-scope 備忘（不得反向改寫 verdict、不得當 gate）；§21.2 新增 blocker/deferred 分級；§22 新增 A19/A20、改寫 A13；§24.5 標 future；§25 新增兩條硬規則；附錄 B 新增「第一張 production card」分級欄；§16.2 P2 語意校正（raw 唯讀判定改以 **container 內寫入探針**為準：host 使用者擁有該 export，host 端 `test -w` 必然為真而會誤判，故明文禁用；此校正僅記載於本表與 `evidence/`）。**B 修正**：§16.2 P8 與等價檢查一律改用 `/opt/venv/bin/python`（`/usr/local/bin/python` 無 qlib；登入 shell 會還原 PATH）；`container/scripts/verify_final.sh` 兩處 `container exec … python` 同步改為 venv 絕對路徑。**C 最小 readiness**：新增 `runtime/preflight.py`（§16.4 的 P1–P10 單一腳本）、`runtime/reconcile.py`（§9.4 no-agent reconciler，含 §12.6 incident 寫入）、`runtime/terminal_evidence.py`（§10.3/§10.4 sentinel + checksum 產生器，host 端 orphan `INCOMPLETE` 補寫用）；§3/§9.4/§10.4/§21.1 的 `[T]` 對應轉為 `[V]` | ChatGPT（GPT-5.6 Sol）卡片 t_ec039d5f：修正契約語意與第一張正式 strategy card 前的最小 runtime 缺口的 remediation；不新增任何 Manager/Service/Factory/Registry/Orchestrator、daemon、queue 或第二套 runtime |
| v1.1.1 | 2026-09-13 | **F1（blocking）**：`runtime/reconcile.py` 的 consumed 判定移到 §9.4 驗證清單**之前**（sentinel 可解析出 `task_id` 且 DB 讀回非 `scheduled` → consumed/no-op：不寫 incident、不留 comment、重跑不累加；無法解析 `task_id` 才維持 fail-closed），對齊 §9.4 掃描範圍與 INV-16。**F2**：§9.4 item 1 補上 attempt `run-spec.json`，並補足 identity 對照（`family.json.family_id`、`round-spec.json.family_id/round_id`、`run-spec.json` 的 family/round/run/task、`container_id`（sentinel 必填、run-spec 有載明即須相符）、path/sentinel）；§9.4 item 4 載明現役容器 identity 比對屬 preflight P5/P6、不在 reconciler 內再引入 container 查詢。**F3**：§16.2 P10 改為「`script.sha256` 必須由 host 端**實際重算**相符」，不可讀／不可解析 → `FAIL`（NOT VERIFIED）且 launch gate 不通過；`runtime/preflight.py` 以既有 `/scripts` ro mount mapping 解析（`--host-scripts`，可用 `QLIB_HOST_SCRIPTS` 覆寫），並新增 `runtime/tests/test_preflight_p10.py`。**文件精度**：README／§21.1／附錄 C 的 audit 指針改為 `t_d7f48c7a`；附錄 C 補記 v1.1.0 的 §16.2-P2 語意校正；§9.4 的 fail-closed 分支計數修正為六項；README Provenance 措辭改為「除附錄 C 記載之變更外」。**語意不變**：Nautilus 仍為 future/out-of-scope/non-blocking；production 仍為 sequential A→B→C；§7.2 full-backtest 定義不變 | auditor t_d7f48c7a（v1.1.0，FAIL）的 F1 blocking、F2/F3 同批 conformance gap、M1–M4 文件精度，本卡 t_4d6c5cd5 之最小 remediation；不新增 Manager/Service/Factory/Registry/Orchestrator、daemon、queue、resolver 或第二套 runtime |

| v1.2.0 | 2026-09-13 | **新增 §14.4 automatic production handoff trigger**：ownership 為 default 的單一 no-agent script-only cron（每輪「檢查 → 必要時 append 1 張 → 結束」）；新增 repo `runtime/production_handoff.py`（deterministic one-round tail append：chain-head／blocked／incident／tail 狀態 gates、reviewed pool `/results/_handoff/candidates.json` 的候選選擇與 `/results/*/family.json` fingerprint 去重、`parents=[tail_id]` + `idempotency_key=<family_id>` 建卡、同輪 `family.json` 落地與讀回、全部歧義 fail-closed 為單一 finding 並去重）與 `runtime/tests/test_production_handoff.py`（19/19）；明文寫 auditor 不是每張 strategy card 的 production stage（不得新增 auditor 子卡）。**語意不變**：§7.2 sequential A→B→C、§14.2 tail append 演算法、fingerprint 規則、`family.json` rules、§12.5/§12.6、INV-8/§1.2（無新 Manager/Service/Factory/Registry/Orchestrator、daemon、queue、第二套 runtime）皆未改 | ChatGPT 卡片 t_0626a619（漢秦哥批准的 production hardening：把「單張自動執行」補成 terminal 後自動接下一張，維持 sequential 且不新增服務）；實作與第一次真實 handoff 證據見 §14.4 的 `[V]` 條目 |

驗證方式（v1.0.1）：全文 cross-reference 掃描（`metadata`/`scheduled`→`blocked`/`/qlib/work` shared-layer 三組字串逐條核對）+ §22 A1–A18 自檢；文件狀態：AUDITED PASS / FROZEN（auditor re-audit t_e35c39c0，2026-09-12）。

驗證方式（v1.1.0）：全文 cross-reference 掃描（Nautilus 相關句逐條核對是否仍暗示 mandatory / authoritative gate、`research-only` 降級條款是否已移除、裸 `python` 檢查是否已改為 `/opt/venv/bin/python`）+ §22 A1–A20 自檢 + `runtime/` 三支腳本實跑輸出（`evidence/runtime-readiness-20260913.json`）；文件狀態：AUDITED FAIL（audit t_d7f48c7a，2026-09-13）。

驗證方式（v1.1.1）：§26 change control 檢查（版本／日期／條號／理由／驗證方式）+ §22 A1–A20 自檢；§9.4 邏輯層 `python3 runtime/tests/test_reconcile.py` 23/23、§16.2 P9/P10 `python3 runtime/tests/test_preflight_p10.py` 10/10（stdlib unittest，真實檔案系統 + 注入 kernel 讀回）；`python3 runtime/preflight.py` → P1–P8 PASS、rc=0（未給 `--attempt-dir` 時 P9/P10 = `NA`）；`python3 runtime/reconcile.py --dry-run` → rc=0。另以**真實** board 讀回與**真實** host `/scripts` mapping 對抗性實測：consumed（card status=running）+ stale boot + checksum 衝突 + mapping 衝突 → 兩次執行皆 `consumed`、0 incident、0 comment、rc=0；identity 衝突且 DB 讀回不可判定 → `mapping_mismatch` incident（rc=3）；`script.sha256` 相符 → P10 PASS（recomputed 相符）、bogus sha → P10 FAIL + `--launch` rc=1。文件狀態：AUDITED PASS / FROZEN（auditor re-audit t_83682069，2026-09-13；audited content commit 18d6c3f；前次 FAIL audit t_d7f48c7a）。此狀態由 attestation-only metadata finalization 記錄（t_d99fbc51）：不改語意、不改 runtime 程式，亦不 bump 版本。

驗證方式（v1.2.0）：§26 change control 檢查（版本／日期／條號／理由／驗證方式）+ 全文 cross-reference（§14.1–14.3、§14.2 演算法、§10.6、§9.4、§12.5/§12.6、§15 未被改寫；無新 Manager/Service/Factory/Registry/Orchestrator、daemon、queue）；`python3 runtime/tests/test_production_handoff.py` 19/19 OK（stdlib unittest，注入式 kernel 讀回／create，涵蓋唯一放行條件、dry-run 不變更、idempotency 與全部 fail-closed 分支）；`python3 runtime/production_handoff.py --dry-run --json` → `would_append`（tail=`t_97208408`）；第一次真實 handoff（fence-free host context 執行）→ 卡 `t_3e696dce`（`parents=[t_97208408]`、`ready`、`idempotency_key`=family_id）+ `/results/ema-crossover-walkforward-momentum-long-short-v1/family.json` 落地並讀回；重跑 → `noop`、0 重複卡；cron job `624d0be5b23c`（`5 * * * *`、no-agent）已掛載並以 `hermes cron list` 讀回、`~/.hermes/scripts/quant_production_handoff.py` 由 foreign cwd 實跑通過。文件狀態：**AWAITING AUDIT**（v1.2.0）；前版 v1.1.1 = AUDITED PASS / FROZEN（auditor re-audit t_83682069）。
