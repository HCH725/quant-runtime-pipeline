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
5. **唯一例外：operator-stopped 的 exact-bytes archive。** `strategy-b-operator-stopped/runtime/`
   放的是**逐位元未修改**的 Strategy B runner 與其 engine test，屬 archive-only evidence
   （理由與 checksum 見該目錄的 `README.md` 與
   `strategy-b-operator-stop-record-20260913.json` 的 `archive_relocation`）。
   它們既不是 runtime 路徑、也不是可執行驗證，只是「當時被 operator 中止的那份實作」的證據；
   這是規則 1–4 之外**唯一**允許 non-snapshot bytes 存在的情況。

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
| `runtime-readiness-20260913.json` | `RUNTIME-2026-09-13`：Contract v1.1.0 的最小 runtime readiness 實跑證據（preflight P1–P8 PASS、reconciler 12/12 邏輯檢查、fixture 的 sentinel/checksum/incident 實測、`/results` 空掃描、以及 delegate-child fence 與 P2/P8 兩個發現）。主機路徑已 redact。**快照界線**：v1.1.1（audit `t_d7f48c7a` remediation）後測試檔已擴充（`test_reconcile.py` 23/23、新增 `test_preflight_p10.py`），本檔為當時輸出、不再重跑更新 |
| `strategy-b-operator-stopped/` | **archive-only**：被 operator 中止的 Strategy B 之 exact-bytes runner 與 engine test（`runtime/30_strategy_b_run.py`、`runtime/tests/test_strategy_b_engine.py`，checksums 見該目錄 `README.md`）。不是 runtime 路徑、不得執行；理由見 `strategy-b-operator-stop-record-20260913.json` 的 `archive_relocation`（audit `t_246c62d7` M1，卡片 `t_6c83c9fb`） |
| `v1.4.0-readiness-20260913.json` | v1.4.0 的實跑 readiness（engine 37/37、survivor bundle 13/13、B v2 preregistration 8/8、counts 未變）。主機路徑已 redact。**快照界線**：v1.4.2（重跑比對範圍）後測試檔已擴充（`test_survivor_bundle.py` 18 檢定），本檔為當時輸出、不再重跑更新 |
| `strategy-a-v2-survivor-bundle-20260913.json` | Strategy A v2 round r1 的 frozen survivor bundle 快照（2 survivors `BTCUSDT/1h`／`SOLUSDT/4h`、`ranking=null`、bundle identity `sha256:c051759f…`、來源 `verdict.json` checksum 未變、legacy 語意揭露）。主機路徑已 redact；bundle 本體仍是 `<EXPANSION>/qlib-results/**` 的 immutable artifact，本檔只是快照 |
| `v1.4.1-bundle-identity-remediation-20260913.json` | v1.4.1 對 auditor `t_0bd01630` 的 F1 最小 remediation 證據：§10.8 canonical identity recipe 的第三方純 stdlib 重算（移除 `generated_at_utc` 與 identity 欄位自身 → `sha256:c051759f…`；只移除 `generated_at_utc` → `sha256:810e1d6c…` 不等於公開值）、frozen bundle 的 `--check` rc=0 與 `identity_recipe_matches=true`、tamper 負向控制（複本上舊 digest／自洽 digest／非量測散文皆 rc=1）、以及 frozen artifacts 前後逐位元不變的 checksums。主機路徑已 redact。**狀態更正**：v1.4.1 已由 auditor `t_3edafbb9` 判為 AUDITED FAIL（唯一 blocking finding F2），F2 由 `v1.4.2-bundle-replay-scope-remediation-20260913.json` 修正 |
| `v1.4.2-bundle-replay-scope-remediation-20260913.json` | v1.4.2 對 auditor `t_3edafbb9` 的 F2 最小 remediation 證據：在 `/tmp` 複本上（只 rebase `source_attempt_dir` 並依 §10.8 重簽公開 identity）重跑 blocker reproducer——改 `generator.path` 且只重簽公開 identity 現在 `--check` rc=1／量測不符、writer rc=1／`already exists with different content`（v1.4.1 皆為 rc=0）；非 dict `generator` 亦 rc=1；對照控制「只改 `contract` ＋ `generator.sha256` 並重簽」仍 rc=0／`check_clean` 且 writer `already_identical`；`test_survivor_bundle.py` 18/18（該 regression 在 `5d14214` 的 writer 上實測 FAIL）；frozen artifacts 前後逐位元不變（`sha256:4638885f…`／`cb470adf…`／`012e6d1a…`／`74f250cf…`）且 bundle 公開 identity 仍為 `sha256:c051759f…`。主機路徑已 redact |
