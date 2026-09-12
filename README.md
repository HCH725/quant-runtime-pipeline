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

- `QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md`：**v1.1.1，AUDITED PASS / FROZEN（auditor re-audit `t_83682069`，2026-09-13；audited content commit `18d6c3f`）**；
  前一個 AUDITED PASS / FROZEN 的版本是 **v1.0.1**（auditor re-audit `t_e35c39c0`）。
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
| `QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md` | canonical contract（v1.1.1 / AUDITED PASS / FROZEN，audited content commit `18d6c3f`，auditor re-audit `t_83682069`；前一個 FROZEN 版本 v1.0.1），全文三級標記 `[V]`/`[C]`/`[T]`，變更記錄見附錄 C |
| `container/Containerfile` | **唯一** image 定義：`python:3.12-slim` + Qlib `v0.9.7`（build 內 `rev-parse HEAD` 守衛，upstream 移動 tag 即 build 失敗） |
| `container/scripts/` | 只保留目前功能仍屬 canonical rebuild / verify / runtime 的腳本（v1.1.0 僅 `verify_final.sh` 兩處 Qlib 檢查改為 `/opt/venv/bin/python`，其餘與稽核當時逐位元相同；見 §6 Provenance）；一次性 phase/history 腳本已排除（分類與理由見 `evidence/README.md`） |
| `runtime/` | host 端最小 runtime readiness：`preflight.py`（P1–P10）、`reconcile.py`（no-agent 完成橋）、`terminal_evidence.py`（sentinel/checksum 產生器）、`tests/test_reconcile.py`（stdlib unittest，reconciler 的 consumed-first 與 fail-closed 狀態機檢查）、`tests/test_preflight_p10.py`（P9/P10 與 `script.sha256` 重算的邏輯層檢查）。純 stdlib、手動或 no_agent cron 觸發；不含任何常駐服務 |
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

## 7. Security / data exclusions（永不進入本 repo）

`.env`、任何 token / credential / auth header、Kanban DB（含 `-wal` / `-shm`）、
market-data raw 內容、`/results` 實際 run artifacts、`/qlib/work`、parquet、model / checkpoint、
大型 log（`build.log`、`phase*.log`、`housekeeping*.log`）、PID / heartbeat / state churn、
主機權限診斷輸出、raw 檔案清單、舊 LEAN / Nautilus runtime tree。

排除規則見 `.gitignore`；`evidence/` 只放人可讀、已 scrub 的精簡快照。

## 8. 刻意不做（避免過度工程）

- 不加 GitHub Actions / CI / Dependabot / CodeQL / Pages / release automation / submodule / LFS。
- 不新增 framework、service、daemon、registry、queue 或第二套 runtime。
- 不新增任何可自動觸發的排程；本 repo 只做版本控管。
- 本 repo 與 `HCH725/nautilus-quant-system` 無關，不觸及、不取代它。
