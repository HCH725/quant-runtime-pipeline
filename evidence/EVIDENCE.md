# Qlib on Apple Container — evidence snapshot

Status: **Phase 0–9 PASS**，於來源主機實測（2026-09-12）。本檔是**快照，不是 runtime state**；
主機專屬絕對路徑以 `<PLACEHOLDER>` 取代，其餘未修改。完整 per-phase 原始輸出為 local-only，不公開。

---

## Phase 0 — preflight / baseline（唯讀）

- `uname -m` → `arm64`；macOS 26.6.2（25G83）；10 CPUs；16 GiB RAM。
- 資料來源：canonical raw store（外接 volume）與結果目錄皆存在；raw store 於觀測期間
  **1614 files / 78,856,792 bytes**，逐檔 `stat` 清單 hash 見 Phase 4。
- 主機端 raw 同步 job 存在且 `LastExitStatus = 0`。
- container CLI：安裝前為 not installed（2026-09-11 舊 runtime 已退役）。

## Phase 1 — 官方 Apple Container 1.4.1

- Artifact：`container-1.4.1-installer-signed.pkg`（官方 release）
  * size 117,773,865 B；sha256 `c0d2716afefbb194c93fae662e9cae7cc186bcbcf746816608ec673dd648a6a4`
  * `pkgutil --check-signature` → **Apple 開發者憑證簽署、公證受信任**
  * `spctl -a -vvv --type install` → accepted，`source=Notarized Developer ID`，
    `origin=Developer ID Installer: Apple Inc. - Containerization (UPBK2H6LZM)`
- **Install root 偏差（已揭露）**：pkg 宣告 `install-location=/usr/local`（需 root）。
  本次以 `pkgutil --expand` 取出已簽章 payload，安裝於 **`$HOME/.local`**，完整鏡射 pkg 的
  `bin/ + libexec/` 佈局；未使用任何未驗證的 binary。標準佈局只需一次人工指令：
  `sudo installer -pkg container-1.4.1-installer-signed.pkg -target /`。
- Kernel：kata-static **3.32.0 arm64**，在 `container system kernel set` 前先對 **container 自身 pin 的 digest**
  sha256 `8736c054d9223974735394f822000823baef509e1c33405ec798240fa9b6e4b5` 驗證。
- `container system status` → `status running`，client & server **1.4.1**
  （commit `9a8917ca2da5cd6ba059b9ba5ca5a74892e9bb7d`），`host.architecture arm64`。
- **原生 arm64 smoke PASS**：`container run … alpine uname -a` → `Linux … aarch64`。全程無 Rosetta / amd64。

## Phase 2 — Qlib image（native linux/arm64，由 pinned upstream source 建置）

- base `python:3.12-slim`（index digest `sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea`，
  Python 3.12.14，debian trixie，arm64）。
- Qlib clone 於 tag **v0.9.7 == commit `da920b7f954f48ab1bb64117c976710de198373e`**；build 內以
  `rev-parse HEAD` 守衛，upstream 移動 / 重打 tag 即 build 失敗。
- PyPI `pyqlib 0.9.7` 只提供 linux-x86_64 wheel 且**無 sdist**，因此 arm64 必須自行 source build
  （`pip install --no-build-isolation`，2-stage build 丟棄 build-only deps）。
- Image **`qlib:0.9.7-arm64`**，OCI index digest
  `sha256:c1d6e9f6058f5016314a1966277541aa0043e48abc56ac897384d0da57f57b9f`，
  variant digest `sha256:1b021c27e67ca4910432e76c471a6b5ee5e0804b84c1181481c1fa040fd3b740`
  （linux/arm64，manifest 388,594,293 B），`PYTHON_VERSION=3.12.14`，`WorkingDir=/qlib/work`，16 history steps。
- 只含 upstream build/runtime deps ＋ `pyarrow` / `lightgbm` / `loguru` ＋ upstream 的 pinned
  `scripts/dump_bin.py`（置於 `/opt/qlib-tools/dump_bin.py`）。
  **未加入** Jupyter server、Redis、database、scheduler、API server、RD-Agent，亦未安裝
  Docker / Conda / LEAN / Nautilus。

## Phase 3 — storage

- `container volume create --opt size=30g qlib-work` → named volume、driver `local`、**format ext4**、
  `sizeInBytes 32,212,254,720`（30 GiB）；backing `volume.img` 位於官方預設 container app data root
  （主機內接 SSD，未客製路徑）。
- 結果目錄（外接 volume 上的 `qlib-results`）建立，兩側皆可寫。**不存在第二份 raw store。**

## Phase 4 — mounts / permissions PASS

Container **`qlib-run`**（image `qlib:0.9.7-arm64`，6 CPU / 4 GiB），mount 表與 Contract §3 一致：

| host（來源） | container | options |
|---|---|---|
| `<RAW_STORE_HOST_PATH>` | `/data/raw` | **ro**（virtiofs） |
| named volume `qlib-work`（ext4） | `/qlib/work` | rw |
| `<RESULTS_HOST_PATH>` | `/results` | rw（virtiofs） |
| `<RUNTIME_SCRIPTS_HOST_PATH>` | `/scripts` | **ro**（virtiofs） |

- `/data/raw` **可讀**；**不可寫**：`touch` / `mkdir` / `dd` 全部 → `Read-only file system`（exit 1），
  之後 `/data/raw` 未出現任何新檔案。
  *刻意的選擇*：modify / delete **未**在真實 raw payload 上嘗試（錯誤的 rw mount 會破壞它）；
  相同的 ro 語意改在自己的 `/scripts` ro virtiofs share 上完整驗證（`touch` / `chmod` / `rm` / append 全部被拒）。
- `/qlib/work` rw：寫入後讀回 OK。`/results` rw：寫入、讀回、刪除（`removed=YES`）。
- **host raw baseline 前後一致**：逐檔 `stat` 清單 hash
  `8ff261fde1df8c3871d95a288ef38412671a19e0c43dff002421197478919d9a`（1614 files）— **IDENTICAL**。
- 帶外接 mount 的 container 啟動約 **1 s**（`run_exit=0`）。

> 備註：本 phase 早期的 mount 受阻問題已由 operator 端的主機權限授予排除；
> 該權限診斷輸出本身不公開。

## Phase 5 — Qlib runtime import smoke PASS

container 內 fresh process：`qlib 0.9.7` / `numpy 2.5.3` / `pandas 3.0.5` / `pyarrow 25.0.1` /
`lightgbm 4.7.0` / `scipy 1.18.1` / `sklearn 1.9.1` / `mlflow 3.16.0` 全部 `import PASS`；
python 3.12.14、machine `aarch64`（`env-baseline.json`）。named volume 讀寫亦已驗證。

## Phase 6 — 最小 raw → Qlib smoke PASS

視窗：**BTCUSDT 1h, 2024-01-01 → 2024-03-31**（刻意小；未複製任何更大範圍）。

```
host-side raw row count (gunzip | wc -l, 唯讀)                     2184
raw → CSV（寫入 /qlib/work）      rows=2184  first=2024-01-01 00:00:00  last=2024-03-31 23:00:00
dump_bin.py dump_all → /qlib/work/qlib-data-btcusdt-2024Q1
    calendars/60min.txt, instruments/all.txt,
    features/btcusdt/{open,close,high,low,volume}.60min.bin        （合計 132 K）
qlib.init + qlib.data.D.features 讀回：
    calendar_len 2184, rows 2184, columns [$open,$high,$low,$close,$volume],
    index [instrument, datetime], monotonic_increasing true,
    calendar_duplicates 0, null_cells 0, instrument set ["BTCUSDT"]
```

獨立交叉核對（確認數值真的來自 canonical raw，而非 synthetic 或下載資料集）：

```
raw first bar 2024-01  close "42503.50"  ←→ qlib first_row close 42503.5
raw last  bar 2024-03  close "71363.00"  ←→ qlib last_row  close 71363.0
```

未下載官方 CN 範例資料集；raw store 未被複製進 `qlib-work`。Phase 6 後 raw baseline hash 仍 **IDENTICAL**。

## Phase 7 — 最小 research smoke PASS

資料層 → Qlib 運算式引擎（`Ref/Mean/Std`）→ signal + 次根報酬統計；輸出落於結果目錄的
`qlib-smoke-20260912/phase7-research-smoke/`：

```
LABEL  "SMOKE / RESEARCH-ONLY — not a performance claim, not an authoritative backtest"
rows_loaded 2184, rows_used 2159, python 3.12.14, machine aarch64
ma24_deviation  corr vs next-bar ret  -0.00450
zscore24        corr vs next-bar ret   0.00825
fwd_ret mean 0.000229, std 0.00595, min -0.04004, max 0.02950
```

**執行中發現並已修正的 bug（揭露）**：第一次嘗試把 *host* 路徑傳進 `container exec`，
摘要因此寫入 container 自身可寫層而非掛載的 `/results`。以 host 端讀回抓到後，
刪除該 stray 樹，改用 container 內路徑（`/results/…`）重跑；上述讀回來自修正後的執行。

## Phase 8 — I/O sanity check PASS

同一視窗、3 次計時：

| 路徑 | mount | rows | 3 次秒數 | best |
|---|---|---|---|---|
| A — raw JSON.gz（外接 volume VirtioFS bind） | `/data/raw` | 2184 | 0.0037 / 0.0050 / 0.0035 | **0.0035 s** |
| B — qlib `.bin` store（內接 SSD named volume） | `/qlib/work` | 2184 | 0.0067 / 0.0012 / 0.0025 | **0.0012 s** |

dataset size：raw gz 62,564 B vs derived store 132 K；peak RSS ≈ **231 MB**。
*artifact 內已註記的 caveat*：不同 parser、極小視窗，**不是**加速倍數宣稱；
唯一結論是熱的 derived working set 應放在內接 SSD（目前佈局即如此）。

## Phase 9 — capacity / cleanup baseline PASS

- 官方 container app root：約 **17 G**（containers 12 G、snapshots 5.0 G、content 557 M、kernels 29 M、volumes 6.5 M）。
- `qlib-work`：邏輯上限 **32,212,254,720 B（30 GiB）**，ext4；container 內已用 **436 K**。
- 結果目錄：**384 K**（evidence 324 K、phase7 20 K、phase8 4 K、scripts 32 K）。
- 清理：install staging（**2.4 G**）已移除；早期 mount 探測遺留的 anonymous `alpine` 容器已刪除。
  刻意保留：image `qlib:0.9.7-arm64`、volume `qlib-work`、container `qlib-run`、結果目錄、raw store（未觸碰）。
- 內接 SSD 可用：78 Gi（Phase 0）→ **69 Gi**（清理後）；外接 volume 1.7 Ti → 1.7 Ti（不變）。
- 容量政策（25 GB 警告 / 30 GB 硬上限）只存在於 volume 建立選項；**未**建立任何 daemon/service 來管理它。

### Negative checks（皆已驗證不存在）

`docker`、`podman`、`colima`、`rdagent`、`rd-agent`、`lean`、`nautilus`、`nautilus_trader`、`conda`、
`micromamba`、`miniconda` → 全部 absent；`/Applications/Docker.app`、`/Applications/OrbStack.app`、
`~/miniconda3`、`~/anaconda3`、`~/lean-system`、`~/nautilus-system`、`~/nautilus_trader`、
`~/microsoft-quant-stack`、`~/quant-archives`、`~/work/lean-absorption`、`~/.nautilus-tools` → 全部不存在。
未建立完整回測、未建立 runtime orchestration、未建立任何 Strategy Card A→B 工作。

### Raw-data safety（驗證，非宣稱）

raw store 的逐檔 `stat` 清單在 Phase 4 前與 Phase 9 後**逐位元相同**（1614 files，同 per-file size/mtime，
同清單 hash `8ff261fd…`）；raw 同步 job `LastExitStatus = 0`；不存在第二份 raw 資料。

---

## Acceptance criteria

| # | criterion | verdict | evidence |
|---|---|---|---|
| A | Container 1.4.1 running、原生 ARM64 smoke PASS | PASS | Phase 1 |
| B | Qlib 0.9.7 native arm64 source build / import、Python 3.12 | PASS | Phase 2 + 5 |
| C | raw ro mount PASS、host raw 未被修改 | PASS | Phase 4 + raw hashes |
| D | `qlib-work` named volume、官方內接 SSD data root、30 GB 上限 | PASS | Phase 3 + 9 |
| E | 結果目錄位於外接 volume 且可寫 | PASS | Phase 4 + 7 |
| F | BTCUSDT 1h 小樣本真的被 Qlib data layer 讀取 | PASS | Phase 6 |
| G | 最小 research smoke PASS（標示 research-only） | PASS | Phase 7 |
| H | I/O sanity 與磁碟用量基線已記錄 | PASS | Phase 8 + 9 |
| I | 未新增 Docker / Conda / RD-Agent / LEAN / Nautilus | PASS | Phase 9 negatives |
| J | 未建立完整回測、未建立 runtime orchestration | PASS | Phase 9 negatives |

## 揭露的偏差 / residual

1. `container` 安裝於 `$HOME/.local` 而非 `/usr/local`（`paths.installRoot` 反映此事）。
   功能正常，但 canonical 佈局仍需一次 admin 指令：
   `sudo installer -pkg container-1.4.1-installer-signed.pkg -target /`。
   若執行，應移除 `$HOME/.local` 的副本以免 split install。
2. helper 腳本本來只是 local-only 的 smoke / evidence 工具，不是 framework；
   本 repo 只把其中**功能仍屬 canonical rebuild / verify / runtime** 的子集納入版本控管
   （判準與排除清單見 `README.md`）。
3. derived work area（`/qlib/work`）仍留有早期 synthetic data-layer 檢查的產物
   （`csv-synthetic`、`qlib-data-synthetic`、`synthetic-datalayer-check.json`），刻意保留作為證據；
   它們極小且命名明確，稽核結束後可刪除。**它們不是真值來源**（INV-5）。

## 不公開的原始證據（local-only）

`build.log`、`phase*.log`、`housekeeping*.log`、raw 逐檔清單、主機權限診斷輸出、
臨時 GitHub API helper、以及所有 run artifacts 與市場資料，皆刻意不進本 repo。
