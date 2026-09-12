# evidence/ — 這是快照，不是 runtime state

本目錄只放**支撐 Contract `[V]` 事實的精簡證據快照**。它具有以下性質：

1. **Snapshot, not runtime state.** 這些檔案是 2026-09-12 觀測當下的讀回結果，
   不是活的狀態來源。runtime 現在是否健康，一律以即時查詢（`container system status`、
   `container list`、mount / Qlib 讀回）為準，不以本目錄推論。本目錄不承載 heartbeat、
   PID、狀態機進度或任何會被定期改寫的檔案。
2. **Host-specific absolute paths are redacted.** 原始證據中的主機專屬路徑
   （例如使用者家目錄、外接 volume 掛載點、容器 app data root）一律以 `<PLACEHOLDER>` 取代；
   除該取代外，內容未經修改。placeholder 不是可移植性要求，只是把機器細節移出 repo。
3. **No secrets, no raw payload.** 不含 token / credential / `.env`、不含 market data 內容、
   不含 run artifacts、不含主機權限診斷輸出、不含 raw 檔案清單。
4. **Local-only raw evidence 不在此。** 完整 per-phase 執行輸出（`build.log`、`phase*.log`、
   `housekeeping*.log`、raw 檔案清單、主機權限診斷輸出、臨時 GitHub API helper）
   刻意不公開，僅存在於來源主機；本目錄是其可公開的精簡摘要。

## `container/scripts/` 的分類（ACTIVE_CANONICAL vs HISTORICAL_INSTALL_ONLY）

收錄判準是**目前的功能**，不是它當初在哪個 phase 被寫下。

**ACTIVE_CANONICAL（進 repo）**

| 檔案 | 為何是 canonical |
|---|---|
| `Containerfile` | 唯一的 image 定義；pinned tag + 內建 `rev-parse` 守衛，重建與稽核都以此為準 |
| `scripts/fetch_kernel.sh` | runtime 的 kernel pin（kata 3.32.0 arm64）與 digest 驗證；重建步驟 2 的必要動作 |
| `scripts/run_phase4.sh` | **runtime 定義本身**：建立 `qlib-run`（image、6 CPU / 4 GiB）並套用 Contract §3 mount 契約，同時驗證 ro/rw 語意。目前執行中的 container 就是它的產物，沒有第二份定義 |
| `scripts/00_env_baseline.py` | runtime import / 版本基線驗證（Qlib 0.9.7、python 3.12.14、arm64） |
| `scripts/03_qlib_smoke.py` | Qlib data layer 讀回驗證；被 `verify_final.sh` 直接呼叫 |
| `scripts/verify_final.sh` | 獨立讀回驗證（版本、mount 語意、data layer、raw 未受影響） |

**HISTORICAL_INSTALL_ONLY（刻意不收錄）**

| 排除項 | 理由 |
|---|---|
| `01_raw_to_csv.py`、`04_research_smoke.py` | 一次性的 smoke 視窗資料轉換／研究 smoke helper，屬研究腳本而非 runtime 定義 |
| `05_io_bench.py`、`06_datalayer_synthetic_check.py` | 一次性 I/O 基準與「mount 受阻時」的 synthetic 替代檢查 |
| `run_phase6.sh`、`run_phase7.sh`、`run_phase7b.sh`、`run_phase8.sh`、`run_phase9.sh`、`recover_phase5.sh`、`12_launchd_state.py` | 一次性 phase 執行 / 復原 / 主機狀態腳本 |
| `13_housekeeping_recheck.sh`、`10_inspect_summary.py`、`11_image_size.py` | 一次性 housekeeping 與檢視 helper |

把上述腳本整包搬進 repo 會是 scope creep：它們是一次性證據的產生器，不是 runtime 的可重建定義。
其結論已摘要於 `EVIDENCE.md`。

## 檔案

| 檔案 | 內容 |
|---|---|
| `EVIDENCE.md` | Phase 0–9 的可公開摘要、acceptance 表、揭露的偏差 |
| `container-qlib-run.json` | `qlib-run` 的讀回（mount 契約、資源、image digest；host 路徑已 redact） |
| `volume-qlib-work.json` | `qlib-work` 的讀回（driver / format / 30 GiB 上限；backing path 已 redact） |
| `env-baseline.json` | container 內 import / 版本基線 |
| `image-summary.json` | image `qlib:0.9.7-arm64` 的 digest / variant / size |
| `synthetic-datalayer-check.json` | synthetic bars 上的 data layer + 運算式引擎檢查（明確標示 synthetic） |
