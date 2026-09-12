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

## 2. Current status（2026-09-12）

- `QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md`：**v1.0.1，AUDITED PASS（auditor re-audit `t_e35c39c0`）→ FROZEN**。
- Runtime `[V]`：Apple Container **1.4.1**（client/server commit `9a8917ca…`）＋ Qlib **0.9.7** native linux/arm64
  image `qlib:0.9.7-arm64` 已建置；mount 契約（Contract §3）與 ro/rw 語意已實測。
- **未完成（Contract `[T]`，不得宣稱已實作）**：reconciler、execution preflight P1–P10、terminal sentinel / checksum
  產生器、fingerprint 自動化、failure drills D1–D13、Nautilus authoritative acceptance 階段。
  這些目前**都不在本 repo**，本 repo 也不得被引用為它們已存在。

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
- **Nautilus 的角色**：未來 **frozen survivor 的 authoritative acceptance**（Contract §17，`[T]`），
  **不是**第二套 parameter search；本 repo 不得引入第二套 backtester 或 Nautilus runtime tree。
- 完成橋只能是「durable sentinel + host deterministic no-agent reconciler + kanban unblock」；
  不得引入 HTTP server / webhook / Redis / Celery / queue manager。

## 4. 內容

| 路徑 | 內容 |
|---|---|
| `QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md` | audited canonical contract（v1.0.1 / FROZEN），全文三級標記 `[V]`/`[C]`/`[T]`，變更記錄見附錄 C |
| `container/Containerfile` | **唯一** image 定義：`python:3.12-slim` + Qlib `v0.9.7`（build 內 `rev-parse HEAD` 守衛，upstream 移動 tag 即 build 失敗） |
| `container/scripts/` | 只保留目前功能仍屬 canonical rebuild / verify / runtime 的腳本（與稽核當時逐位元相同）；一次性 phase/history 腳本已排除（分類與理由見 `evidence/README.md`） |
| `evidence/` | 支撐 `[V]` 的精簡證據**快照**（不是 runtime state；主機專屬絕對路徑已以 `<PLACEHOLDER>` 取代） |

## 5. 重建 runbook（最小步驟）

1. 安裝 **Apple Container 1.4.1**（官方 signed pkg；標準安裝位置需一次 admin/sudo 權限）。
2. `container/scripts/fetch_kernel.sh` — 取得 kata kernel 並對 `container` 自身 pin 的 digest 驗證。
3. `container build -t qlib:0.9.7-arm64 container/` — image 由 pinned commit 重建。
4. `container volume create --opt size=30g qlib-work` — derived work area（可重建，**永遠不是真值來源**）。
5. `container/scripts/run_phase4.sh` — 以容器名 `qlib-run`（6 CPU / 4 GiB）建立 runtime 並套用 mount 契約。
6. `container/scripts/verify_final.sh` — 獨立讀回：版本、mount 語意、Qlib data layer、raw 未受影響。

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
