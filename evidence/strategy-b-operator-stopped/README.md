# strategy-b-operator-stopped — archive-only，不是 runtime 路徑

這裡只放 **被 operator 中止的 Strategy B 之 exact bytes 證據**。它不是部署來源、不會掛進
`qlib-run`、也不得對現役 runtime 執行。

## 為什麼放在這裡

audit `t_246c62d7` 的 M1 發現：Strategy B 的 runner 與 engine test 雖然已 operator-stopped
（卡 `t_3e696dce` 保持 `blocked`、attempt 為 `INCOMPLETE/operator_stopped`、沒有 `verdict.json`），
但仍留在 **active runtime 路徑**（repo `container/scripts/` 與 host `/scripts` 部署目錄），
與 README 對 `container/scripts/` 的 canonical runtime 表述相衝，容易讓已停止的實作看起來
仍是在役 production implementation。卡片 `t_6c83c9fb` 依 Contract v1.3.1 §13 的
`operator_stopped` archive hygiene 把它們逐位元搬到這裡。

## 檔案（exact bytes，未修改）

| 檔案 | sha256 | 原路徑 |
|---|---|---|
| `runtime/30_strategy_b_run.py` | `f10a64cdca3daf8b409070f6e42d39015a5df65a30de6a610cedff2a1fa1b3d4` | `container/scripts/30_strategy_b_run.py`（原 host `/scripts/30_strategy_b_run.py`） |
| `runtime/tests/test_strategy_b_engine.py` | `574ce5c6e62d42f051dc00162ab0255c94be6e47080e23336cf95eb7182d040f` | `container/scripts/tests/test_strategy_b_engine.py`（原 host `/scripts/tests/test_strategy_b_engine.py`） |

runner 的那個 sha 正是 B round r1-u2 run-spec 所 pin 的值，所以留下來的檔案就是產出
被保留 artifacts 的那份腳本。

## 界線

- **不得執行**。存檔的 test 預設仍指向 `/scripts/30_strategy_b_run.py`，runner 也仍會寫
  `/results`；兩者都是「當時的位元」證據，不是可重跑的驗證。
- **不是可重建定義**。本目錄不參與 `Containerfile`、`container/scripts/run_phase4.sh`
  或 `runtime/preflight.py` 的任何路徑解析。
- **沒有科學 verdict**。B 沒有 PASS、也沒有 REJECT；這裡的任何數字都不得被引用為
  EMA-crossover 假說的績效陳述。
- **未動 `/results`**：`family.json`、`round-spec.json`、attempt artifacts 與
  `FAILED` / `INCOMPLETE` sentinel 全部逐位元不變。

stop record 的 `archive_relocation` 區塊記錄同一組事實：
`evidence/strategy-b-operator-stop-record-20260913.json`。
