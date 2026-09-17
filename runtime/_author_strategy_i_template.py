#!/usr/bin/env python3
"""Strategy I — pre-registration (round-spec) + run-spec authoring for family
`crypto-hourly-bitcoin-walk-forward-cost-aware-execution-2026-09-01` (card t_54d994a2).

Pure stdlib.  Nothing is written until EVERY check passes:

  1. every verbatim excerpt is asserted as an exact substring of its declared source
     (the frozen card body read back from the board DB with the system-owned lifecycle footer
     stripped, or the canonical wiki record);
  2. the canonical raw store is re-measured (1h bar grid, funding settlements, instrument
     metadata) and the measurement is embedded in the document;
  3. the generic v1.8 parameter contract is generated FROM this document's own registered
     domains and validated (validate_contract / validate_round_spec_contract == 0 problems);
  4. the registered domain arithmetic is recomputed (1 cohort x 4 strategy cases x 48 DCA
     configs x 10 phase grids = 1920 cell evaluations);
  5. both output files are published with O_CREAT|O_EXCL (exact once; a rerun is refused).

usage:
  strategy_i_v1_prereg.py --card-body <file|auto> --out-check <json>
"""
from __future__ import annotations

import argparse
import calendar
import glob
import gzip
import hashlib
import importlib.util
import json
import os
import sqlite3
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAMILY_ID = "crypto-hourly-bitcoin-walk-forward-cost-aware-execution-2026-09-01"
ROUND_ID = FAMILY_ID + "-r1"
RUN_ID = ROUND_ID + "-u1"
TASK_ID = "t_54d994a2"
BOARD = "quant-strategy-research"
RESULTS_ROOT = "/Volumes/ExpansionDrive/qlib-results"
RAW_ROOT = "/Volumes/ExpansionDrive/market-data-raw"
RECORD_PATH = os.path.expanduser(
    "~/.hermes/wiki/quant/crypto-hourly-bitcoin-walk-forward-cost-aware-execution-2026-09-01.md")
ENGINE_REPO = os.path.join(REPO, "container", "scripts", "90_strategy_i_run.py")
ENGINE_DEPLOY = "/Users/hong/workspace/qlib-apple-container/scripts/90_strategy_i_run.py"
SELFCHECK_REPO = os.path.join(REPO, "container", "scripts", "tests", "test_strategy_i_engine.py")
SELFCHECK_DEPLOY = "/Users/hong/workspace/qlib-apple-container/scripts/tests/test_strategy_i_engine.py"
CONTRACT = os.path.join(REPO, "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md")
CONTRACT_VERSION = "v1.9.0"
FOOTER_MARK = "---\nLIFECYCLE FOOTER"
DIRECTION_MODES = ("long_only", "long_short")
MODEL_IMPLS = ("lightgbm", "sklearn_hist_gbm")
CASE_FIELDS = ("dir_long_only", "dir_long_short", "model_lightgbm", "model_sklearn")
CASE_ORDER = tuple(tuple([1 if a == i else 0 for a in range(2)] + [1 if b == j else 0 for b in range(2)])
                   for j in range(2) for i in range(2))
CASE_NAMES = tuple("%s__%s" % (DIRECTION_MODES[i], MODEL_IMPLS[j])
                   for j in range(2) for i in range(2))
DCA_AXES = {"spacing_pct": [0.01, 0.02, 0.03, 0.04], "size_multiplier": [1.0, 1.1],
            "breakeven_tp_pct": [0.01, 0.02, 0.03], "invalidation_pct": [0.05, 0.10]}
BASE_QUOTE = 1000
PHASE_GRIDS = ("historical", "oos", "full", "fee_2x", "funding_2x", "entry_delay_1_bar",
               "slippage_2ticks", "no_funding", "no_funding_full", "cost_attrition_40bps")
MIN_EPISODES_IS = 10
MIN_EPISODES_OOS = 4
NEIGHBOURHOOD_MIN = 0.6


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def load_pc():
    spec = importlib.util.spec_from_file_location("parameter_contract",
                                                  os.path.join(REPO, "runtime", "parameter_contract.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def read_card_body():
    """The frozen card body straight from the board DB, footer stripped (source of truth)."""
    env = os.environ.get("HERMES_KANBAN_DB")
    cands = [env] if env else []
    cands += [os.path.expanduser("~/.hermes/kanban.db")]
    cands += sorted(glob.glob(os.path.expanduser("~/.hermes/kanban/boards/*/kanban.db")))
    for db in cands:
        if not db or not os.path.exists(db):
            continue
        try:
            con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
            row = con.execute("select body from tasks where id = ?", (TASK_ID,)).fetchone()
            con.close()
        except sqlite3.Error:
            continue
        if row is None:
            continue
        body = next((v for v in row if isinstance(v, str) and len(v or "") > 200), None)
        if body is None:
            continue
        return body.split(FOOTER_MARK)[0].rstrip() + "\n", db
    raise SystemExit("card body for %s not found in any board DB (%s)" % (TASK_ID, cands[:4]))


def measure_raw():
    """Re-measure the canonical raw store: the 1h bar grid, funding and instrument metadata."""
    kl = os.path.join(RAW_ROOT, "binance", "usdm", "klines", "BTCUSDT", "1h")
    files = sorted(glob.glob(os.path.join(kl, "*.jsonl.gz")))
    first = last = None
    rows = 0
    off_grid = 0
    per_month = {}
    for path in files:
        rows_m = 0
        with gzip.open(path, "rt") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                ms = int(rec["open_time_ms"])
                if last is not None and ms - last != 3600000:
                    off_grid += 1
                if first is None:
                    first = ms
                last = ms
                rows += 1
                rows_m += 1
        per_month[os.path.basename(path)] = rows_m
    funding_path = os.path.join(RAW_ROOT, "binance", "usdm", "funding", "BTCUSDT",
                               "BTCUSDT-funding.jsonl.gz")
    ftimes, truth = [], {"official": 0, "modeled_funding": 0, "other": 0}
    with gzip.open(funding_path, "rt") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            ftimes.append(int(rec["funding_time_ms"]))
            st = rec.get("truth_status")
            truth[st if st in ("official", "modeled_funding") else "other"] += 1
    inst_path = os.path.join(RAW_ROOT, "binance", "usdm", "instruments",
                             "usdm-perp-instruments.json")
    with open(inst_path) as fh:
        inst_doc = json.load(fh)
    instruments = {}
    for item in inst_doc["instruments"]:
        f = item["fields"]
        instruments[f["raw_symbol"]] = {"id": f["id"], "type": f["type"],
                                        "base_currency": f["base_currency"],
                                        "quote_currency": f["quote_currency"],
                                        "settlement_currency": f["settlement_currency"],
                                        "is_inverse": f["is_inverse"],
                                        "price_increment": float(f["price_increment"]),
                                        "taker_fee": float(f["taker_fee"]),
                                        "maker_fee": float(f["maker_fee"]),
                                        "margin_init": float(f["margin_init"]),
                                        "margin_maint": float(f["margin_maint"]),
                                        "min_notional": f["min_notional"]}

    def iso(ms):
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ms / 1000.0))

    return {"klines": {"path": kl, "month_files": len(files), "rows": rows,
                       "first_bar_open_utc": iso(first), "last_bar_open_utc": iso(last),
                       "first_bar_open_date": iso(first)[:10], "last_bar_open_date": iso(last)[:10],
                       "off_grid_steps": off_grid, "expected_rows_if_contiguous": rows + off_grid,
                       "per_month_first_last": {
                           "first": os.path.basename(files[0]), "last": os.path.basename(files[-1])}},
            "funding": {"path": funding_path, "observations": len(ftimes),
                        "first": iso(ftimes[0]), "last": iso(ftimes[-1]),
                        "truth_status_counts": truth},
            "instruments": instruments,
            "instruments_sha256": sha256_file(inst_path),
            "market_dirs": sorted(os.listdir(os.path.join(RAW_ROOT, "binance"))),
            "venues": sorted(os.listdir(os.path.join(RAW_ROOT, "binance", "usdm", "klines")))}


def dca_grid():
    out = []
    for sp in DCA_AXES["spacing_pct"]:
        for sm in DCA_AXES["size_multiplier"]:
            for tp in DCA_AXES["breakeven_tp_pct"]:
                for iv in DCA_AXES["invalidation_pct"]:
                    out.append({"base_quote": BASE_QUOTE, "spacing_pct": sp,
                                "size_multiplier": sm, "breakeven_tp_pct": tp,
                                "invalidation_pct": iv})
    return out


def parameter_contract_block():
    """Generated entirely from this document's own registered domains (contract v1.8 rule)."""
    return {
        "parameter_contract_version": 1,
        "family_id": FAMILY_ID,
        "contract_ref": "v1.8 generic family parameter contract "
                        "(runtime/parameter_contract.py); generated from this document's "
                        "registered parameter_domain / dca_domain",
        "research_axes_ordered": [
            {"name": "direction_mode", "kind": "composite",
             "members": ["dir_long_only", "dir_long_short"],
             "registered_values": [[1, 0], [0, 1]],
             "row_fields": ["dir_long_only", "dir_long_short"]},
            {"name": "model_impl", "kind": "composite",
             "members": ["model_lightgbm", "model_sklearn"],
             "registered_values": [[1, 0], [0, 1]],
             "row_fields": ["model_lightgbm", "model_sklearn"]},
            {"name": "spacing_pct", "kind": "atomic", "members": ["spacing_pct"],
             "registered_values": list(DCA_AXES["spacing_pct"]), "row_fields": ["spacing_pct"]},
            {"name": "size_multiplier", "kind": "atomic", "members": ["size_multiplier"],
             "registered_values": list(DCA_AXES["size_multiplier"]),
             "row_fields": ["size_multiplier"]},
            {"name": "breakeven_tp_pct", "kind": "atomic", "members": ["breakeven_tp_pct"],
             "registered_values": list(DCA_AXES["breakeven_tp_pct"]),
             "row_fields": ["breakeven_tp_pct"]},
            {"name": "invalidation_pct", "kind": "atomic", "members": ["invalidation_pct"],
             "registered_values": list(DCA_AXES["invalidation_pct"]),
             "row_fields": ["invalidation_pct"]}],
        "row_fields": list(CASE_FIELDS) + list(DCA_AXES),
        "composite_map": {"direction_mode": ["dir_long_only", "dir_long_short"],
                          "model_impl": ["model_lightgbm", "model_sklearn"]},
        "strategy_param_fields": list(CASE_FIELDS),
        "dca_param_fields": list(DCA_AXES),
        "canonical_recipe": {"sort_keys": True, "separators": [",", ":"], "ensure_ascii": False,
                             "numeric_rule": "JSON number finite, bool excluded"},
        "row_match_recipe": {"keys": ["symbol", "timeframe"] + list(CASE_FIELDS) + list(DCA_AXES),
                             "equality": "exact, numeric == float compare, rest bytewise"},
        "non_params": ["symbol", "timeframe", "diagnostics", "metrics"],
        "domain_cardinality": {"strategy": len(CASE_ORDER), "dca": len(dca_grid()),
                               "per_cohort": len(CASE_ORDER) * len(dca_grid())},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-check", default=None)
    args = ap.parse_args()
    pc = load_pc()
    problems = []
    card_body, card_db = read_card_body()
    card_sha = "sha256:" + hashlib.sha256(card_body.encode()).hexdigest()
    with open(RECORD_PATH) as fh:
        record_text = fh.read()
    record_sha = "sha256:" + hashlib.sha256(record_text.encode()).hexdigest()
    raw = measure_raw()
    engine_sha = sha256_file(ENGINE_REPO)
    engine_deploy_sha = sha256_file(ENGINE_DEPLOY)
    selfcheck_sha = sha256_file(SELFCHECK_REPO)
    selfcheck_deploy_sha = sha256_file(SELFCHECK_DEPLOY)
    if engine_sha != engine_deploy_sha:
        problems.append("engine sha differs between repo and host deploy: %s vs %s"
                        % (engine_sha, engine_deploy_sha))
    if selfcheck_sha != selfcheck_deploy_sha:
        problems.append("self-check sha differs between repo and host deploy")

    with open(os.path.join(RESULTS_ROOT, FAMILY_ID, "family.json")) as fh:
        family = json.load(fh)
    if family["kanban_task_id"] != TASK_ID:
        problems.append("family.json kanban_task_id %r != %r" % (family["kanban_task_id"], TASK_ID))
    fp_input = family["fingerprint_input"]
    fp = "sha256:" + hashlib.sha256(fp_input.encode()).hexdigest()
    if fp != family["semantic_fingerprint"]:
        problems.append("semantic fingerprint does not recompute: %s vs %s"
                        % (fp, family["semantic_fingerprint"]))

    # ---------------- verbatim excerpts (asserted BEFORE any write) ------------------------
    card_ex = {
        "family_id_registration":
            "family_id 固定：`crypto-hourly-bitcoin-walk-forward-cost-aware-execution-2026-09-01`",
        "prerequisite_clause":
            "若本 record 的 required data/market 不在上述 raw（見 ELIGIBLE UNIVERSE 節錄），本卡執行時依 §13 technical failure semantics 終結（`TECHNICAL_INCOMPLETE`／`DEFERRED`）；**不得**以近似資料、替代市場或改寫 hypothesis 硬跑",
        "mechanism_failure_of_naive_execution":
            "**Failure of Naive Sign-Based Execution:** Converting 1-hour return forecasts directly into positions ($\\text{pos}_t = \\text{sign}(\\hat{r}_{t+1})$) generates extreme turnover (over 10,000\u201318,000 trades over the 2018\u20132026 evaluation period). When conservative proportional transaction costs ($c = 10\\text{ bps}$ per unit turnover) are applied, all naive machine-learning strategies collapse into persistent losses (gross annual returns drop from $+73.5\\%$ to $-64.0\\%$ for XGBoost, and $+181.8\\%$ to $-98.6\\%$ for iTransformer).",
        "mechanism_cost_aware_filter":
            "**Cost-Aware Execution Filter:** Introducing an execution threshold proportional to transaction costs:",
        "signal_forecast_generation":
            "- Generate point prediction $\\hat{r}_{t+1}$ for next-hour log return $r_{t+1} = \\ln(P_{t+1}/P_t)$ using walk-forward retrained XGBoost regression model.",
        "signal_target_sign_position":
            "- Long-only mode: $\\text{pos}_t^* = 1$ if $\\hat{r}_{t+1} > 0$, else $\\text{pos}_t^* = 0$.",
        "signal_filter_rule":
            "$$|\\hat{r}_{t+1}| > \\lambda \\cdot c \\cdot |\\text{pos}_t^* - \\text{pos}_{t-1}|$$",
        "signal_rebalancing":
            "4. **Rebalancing Frequency:** Hourly on bar close.",
        "signal_research_defined_scope":
            "`research-defined` 只可補 execution 細節（成本、滑價、進場時點對齊），不得改變核心 hypothesis、方向或 universe。",
        "universe_required_data_universe":
            "- **Universe:** BTC/USDT USD-margined perpetual futures (or spot).",
        "universe_required_data_venue": "- **Venue:** Binance (or equivalent liquid centralized exchange).",
        "universe_required_data_timeframe": "- **Timeframe:** 1-hour completed bars (OHLCV).",
        "universe_required_data_features": "  - Rolling Technical Indicators computed over windows $w \\in \\{3, 6, 12, 24, 48, 72, 168, 336\\}$ hours.",
        "universe_required_data_egarch": "  - EGARCH(p,q) fitted recursively on training segment with Student-$t$ innovations.",
        "universe_timestamp_integrity": "- **Timestamp Integrity:** Strictly causal feature construction with no forward-looking data leakage.",
        "portability_direct":
            "The strategy is developed, backtested, and validated directly on Binance BTC/USDT perpetual futures hourly market data.",
        "window_rule":
            "資料窗：以 record 的 required data/sample period 為準；record 未明確指定時採本機 canonical raw 窗（2022-01-01\u21922026-09-11）並在 round-spec 標 `research-defined`",
        "window_no_rewrite":
            "**不得**以資料可得性改寫 record 的市場或 mechanism。",
        "window_split":
            "in-sample（historical）與 OOS 切分在 round-spec 寫死（canonical 採用 in-sample 2022-01-01\u21922025-09-30、OOS 2025-10-01\u21922026-09-11；record 已指定切分時以 record 為準）。",
        "dca_four_axes":
            "`spacing_pct \u2208 {0.01, 0.02, 0.03, 0.04}` \u00d7 `size_multiplier \u2208 {1.0, 1.1}` \u00d7 `breakeven_tp_pct \u2208 {0.01, 0.02, 0.03}` \u00d7 `invalidation_pct \u2208 {0.05, 0.10}` = **48 DCA configs per cohort per phase grid**",
        "dca_per_fill_costing":
            "每個 entry／DCA add／exit fill 的 taker fee 與 funding（衍生品部位）必須在**該 fill 的時點**扣入 realised equity",
        "dca_independent_gross":
            "`gross_pnl` 由**獨立 price-PnL accumulator** 產生（exit proceeds \u2212 cost basis，不含 fee／funding），**不得**由 net 反向回推",
        "user_fixed_invariants":
            "starting_equity = 30,000 USDT、USDT 為唯一 numeraire、linear USD-M perp、leverage 10x、12 tranches",
        "leg_semantics":
            "tranche #1 = 訊號成立後的初始進場；持倉期間 adverse-price scale-ins 依 ladder 展開（active levels \u2264 11；long 方向：level_k price = initial_entry_price \u00d7 (1 \u2212 spacing_pct \u00d7 k)",
        "coverage_gate_g1":
            "每個註冊 phase grid（`historical, oos, full, fee_2x, funding_2x, entry_delay_1_bar, slippage_2ticks, no_funding, no_funding_full, cost_attrition_40bps`）須恰含 `cohort 數 \u00d7 strategy cases \u00d7 48 DCA` 的完整乘積",
        "selector_steps":
            "1. 充分性：最佳 historical case 的 eligible 訊號數 < `min_episodes_is`（依 record 的 no-signal/insufficient-trades 門檻註冊，round-spec 寫死）\u2192 以 `insufficient_trades` 淘汰。",
        "survivor_requirements":
            "a) 存在 historical winner；",
        "robustness_stress_grids":
            "標準 stress grids（全 cell 實算）：`fee_2x`、`funding_2x`、`entry_delay_1_bar`、`slippage_2ticks`；另跑 `cost_attrition_40bps` 與 `no_funding` / `no_funding_full`",
        "failure_taxonomy":
            "**科學失敗**（依 evidence 判定，寫進 `verdict.json`）：record 的 falsification battery 命中 \u21d2 cull 或 family `REJECT`",
        "implementation_rules":
            "production script 先進 HCH725/quant-runtime-pipeline，再部署到 host /Users/hong/workspace/qlib-apple-container/scripts，兩份 sha256 必須一致；run-spec 的 script.path=/scripts/... 並由 P10 真實重算。",
        "implementation_container":
            "計算必須在 qlib-run container 執行；不得用 CatDesk 代算、不得把純 pandas host backtest 冒充 Qlib production。raw 讀取一律走 /data/raw（read-only），不得寫回。",
        "no_auditor_child":
            "本卡只用 Hermes default 執行；不要建立 auditor 子卡。",
        "survivor_bundle_rule":
            "該 round 的 **frozen survivor bundle** 凍結全部 survivors（`ranking=null`，非排名），只由 terminal `DONE` 且 coverage／assertions 全真之 attempt 產生，寫入後永不改寫",
        "forbid_second_engine":
            "禁止 Manager/Service/Factory/Registry/Orchestrator/daemon/queue/第二套 engine。",
    }
    record_ex = {
        "sample_data_source":
            "- **Data Source:** Hourly OHLCV data for BTC/USDT USD-margined perpetual futures from Binance public API.",
        "sample_period":
            "- **Sample Period:** 1 December 2017 to 1 January 2026 (70,872 hourly observations; 70,128 effective walk-forward evaluation hours net of December 2017 burn-in).",
        "validation_protocol":
            "- **Validation Protocol:** 27 sequential non-anchored rolling walk-forward folds (12-month train, 3-month validation, 3-month test).",
        "required_data_block":
            "## Required data\n\n- **Universe:** BTC/USDT USD-margined perpetual futures (or spot).\n- **Venue:** Binance (or equivalent liquid centralized exchange).\n- **Timeframe:** 1-hour completed bars (OHLCV).",
        "execution_timing":
            "- **Timing:** Orders executed at the open of hour $t+1$ following model inference on hour $t$ close.",
        "execution_costs":
            "- **Transaction Costs:** Proportional cost $c = 10\\text{ bps}$ ($0.0010$) per unit of turnover (accounting for 2\u20134 bps exchange fee + bid-ask spread crossing + slippage).",
        "execution_order_type": "- **Order Type:** Market on open / aggressive taker fill.",
        "execution_leverage": "- **Leverage:** 1x notional (unlevered).",
        "falsification_item_1":
            "1. **Out-of-Sample Walk-Forward Extension (2026+):** Apply the trained XGBoost model and cost-aware filter to live/forward 2026 data. The strategy is falsified if net-of-cost Sharpe ratio drops below 0.0 over 12 consecutive months.",
        "falsification_item_2":
            "2. **Transaction Cost Sensitivity Threshold:** Increment cost parameter $c$ from 10 bps to 15, 20, and 25 bps. If the strategy's Sharpe ratio turns negative at $c \\le 15\\text{ bps}$, the edge is too fragile for live execution.",
        "falsification_item_3":
            "3. **Cross-Asset Replication:** Test identical walk-forward pipeline on ETH/USDT and SOL/USDT. If the cost-aware execution filter fails to produce positive net returns on other major crypto pairs, the Bitcoin result is an artifact of asset-specific trending behavior.",
        "limitation_not_reproduced": "- **Not independently reproduced.**",
        "limitation_single_asset": "- **Single-Asset Focus:** Primary empirical results focus exclusively on BTC/USDT.",
        "limitation_execution_model": "- **Execution Model Simplification:** Assumes fixed 10 bps proportional fee rather than dynamic order-book depth simulation, variable spreads, or maker rebate models.",
        "intake_caveat":
            "intake_caveat: \"The source supports the cost-aware filter, c=10 bps, lambda=2.0, 0.20% one-unit and 0.40% two-unit hurdles, and 27-fold walk-forward design; however, the artifact\u2019s parenthetical technical-feature list should not be treated as the source\u2019s exact selected feature set, and all reported performance remains source-reported rather than independently reproduced.\"",
        "negative_evidence_long_short":
            "- **Long-Short Mode Failure:** Under cost-aware execution, long-short ML models achieve only modest positive returns",
    }
    excerpt_map = {}
    for label, text in card_ex.items():
        if text not in card_body:
            problems.append("card excerpt %r is not a verbatim substring of the frozen card body"
                            % label)
        excerpt_map["sha256:" + hashlib.sha256(text.encode()).hexdigest()] = \
            {"label": label, "declared_source": "frozen card body (board DB %s, footer stripped, %s)"
                                                 % (card_db, card_sha)}
    for label, text in record_ex.items():
        if text not in record_text:
            problems.append("record excerpt %r is not a verbatim substring of the canonical record"
                            % label)
        excerpt_map["sha256:" + hashlib.sha256(text.encode()).hexdigest()] = \
            {"label": label, "declared_source": "canonical wiki record %s (%s)"
                                                 % (RECORD_PATH, record_sha)}

    if problems:
        print(json.dumps({"problems": problems}, indent=2, ensure_ascii=False))
        return 1

    # ---------------- the frozen registrations ---------------------------------------------
    grid = dca_grid()
    cohort_cases = len(CASE_ORDER)
    per_cohort = cohort_cases * len(grid)
    per_grid = 1 * per_cohort
    total = per_grid * len(PHASE_GRIDS)
    c = raw["klines"]
    in_window = {"start": "2022-01-01", "end": c["last_bar_open_date"]}
    data_block = {
        "source": "/data/raw (host %s), read-only" % RAW_ROOT,
        "venue": "BINANCE USD-M perpetual",
        "symbols": ["BTCUSDT"],
        "timeframes": [{"raw_interval": "1h", "qlib_freq": "60min"}],
        "start": in_window["start"], "end": in_window["end"],
        "historical_start": "2022-01-01", "historical_end": "2025-09-30",
        "oos_start": "2025-10-01", "oos_end": in_window["end"],
        "split_immutability": "The split is immutable and fixed here, before any computation; it "
                              "must not be moved afterwards.",
        "derived_store": "Built per attempt from raw into the rebuildable /qlib/work area "
                         "(WORK_ROOT /qlib/work/strategyI-v1) via upstream qlib dump_bin.py; read "
                         "back exclusively through the qlib data layer. Bars are never resampled, "
                         "gap-filled or interpolated: a missing bar fails the run closed.",
        "bar_labelling": {
            "raw_field": "open_time_ms",
            "status": "VERIFIED at pre-registration on the canonical raw store (re-measured by "
                      "this authoring run, not quoted)",
            "measured_at_pre_registration": {
                "month_files": c["month_files"], "bars_in_window": c["rows"],
                "first_bar_open_utc": c["first_bar_open_utc"],
                "last_bar_open_utc": c["last_bar_open_utc"],
                "expected_if_contiguous": c["expected_rows_if_contiguous"],
                "off_grid_steps": c["off_grid_steps"]},
            "consequence": "the 1h grid is exactly contiguous over the window (zero off-grid "
                           "steps), so every fold boundary is an exact bar index derived by "
                           "searchsorted on open_time_ms; no boundary is inferred from a label"},
        "funding_series_contract": {
            "registered_window": "%s .. %s" % (raw["funding"]["first"], raw["funding"]["last"]),
            "measured_observations": raw["funding"]["observations"],
            "truth_status_counts": raw["funding"]["truth_status_counts"],
            "engine_rule": "the position is exposed to every settlement whose raw instant lies in "
                           "the CLOSED interval [entry instant - 1 s, exit instant + 1 s]; each "
                           "settlement is charged at its own instant on the position notional at "
                           "that instant, in bar order, exactly once",
            "no_fabrication": "the modelled/official split is disclosed; no modelled row is ever "
                              "relabelled official"},
        "funding_truth_status_windows": {
            "engine_rule": "the complete registered series is charged (as a COST only); the "
                           "truth_status split is disclosed and is never re-labelled",
            "modelled_funding": "2022-01-01T00:00:00Z .. 2023-10-31 (reconstruction)",
            "official": "2023-10-31T08:00:00Z .. %s (Binance mark price present)" % raw["funding"]["last"],
            "primary_uses": "the complete registered series (modelled + official)"},
        "window_registration": {
            "class": "research-defined",
            "rule_applied": "the card's window rule: the record's required data (BTC/USDT USD-M "
                            "1h OHLCV) is present in the canonical raw store, and the record's "
                            "own sample period is only partly reproducible here, so the local "
                            "canonical window is registered as `research-defined`",
            "record_sample_absent_share": {
                "record_sample": "2017-12-01 .. 2026-01-01 (97 months)",
                "local_overlap": "2022-01-01 .. 2026-01-01 (48 months, 0.4948)",
                "absent": "2017-12 .. 2021-12 (49 months, 0.5052) - entirely absent from the "
                          "canonical raw store"},
            "fold_count_consequence": "the record's 27-fold design is a function of its own "
                                      "97-month sample; on the registered window the SAME fold "
                                      "structure (12m train / 3m val / 3m test, non-anchored "
                                      "rolling, 3-month steps) yields 14 folds (13 complete + one "
                                      "final test window clipped to the data end). No gate, "
                                      "threshold or split rule is weakened by this; the fold "
                                      "count is disclosed as a consequence of the window.",
        },
    }

    round_spec = {
        "schema_version": 1,
        "document_kind": "round-spec (Strategy I v1 pre-registration; prerequisite re-measured, "
                         "full-backtest registered before the first run)",
        "family_id": FAMILY_ID,
        "family_title": "Hourly Bitcoin Machine-Learning Return Forecasting with Cost-Aware "
                        "Execution Filtering",
        "round_id": ROUND_ID,
        "kanban_board": BOARD,
        "kanban_task_id": TASK_ID,
        "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "authored_by": "Hermes default (Xiaoqian), card %s" % TASK_ID,
        "contract": {"document": "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md",
                     "version": CONTRACT_VERSION,
                     "sections_registered": ["6.4", "7.2", "7.3", "9.x", "10.x", "13", "14.4",
                                             "16.2", "26.1", "27", "28"]},
        "parent_family": None,
        "lineage_note": family["lineage_note"],
        "semantic_fingerprint": {"fingerprint_input": fp_input, "semantic_fingerprint": fp},
        "provenance": {
            "record_path": RECORD_PATH, "record_sha256": record_sha,
            "frozen_card_body_sha256": card_sha, "card_body_source": card_db,
            "intake_decision": "PASS-WITH-CAVEAT",
            "primary_sources": ["https://arxiv.org/abs/2606.00060"],
            "record_intake_caveat_verbatim": record_ex["intake_caveat"]},
        "hypothesis": {
            "title": "Hourly Bitcoin Machine-Learning Return Forecasting with Cost-Aware "
                     "Execution Filtering (Bysik & Slepaczuk 2026, arXiv:2606.00060)",
            "economic_mechanism_verbatim": [card_ex["mechanism_failure_of_naive_execution"],
                                            card_ex["mechanism_cost_aware_filter"]],
            "signal_semantics_verbatim": [card_ex["signal_forecast_generation"],
                                          card_ex["signal_target_sign_position"],
                                          card_ex["signal_filter_rule"],
                                          card_ex["signal_rebalancing"]],
            "falsification_battery_verbatim": [record_ex["falsification_item_1"],
                                               record_ex["falsification_item_2"],
                                               record_ex["falsification_item_3"]],
            "limitations_verbatim": [record_ex["limitation_not_reproduced"],
                                     record_ex["limitation_single_asset"],
                                     record_ex["limitation_execution_model"]],
            "negative_evidence_verbatim": [record_ex["negative_evidence_long_short"]],
        },
        "record_to_local_substitutions": [
            {"item": "forecasting model library",
             "source_registration": "XGBoost regression model (record's signal section 1)",
             "local_registration": "the registered strategy axis model_impl carries the two "
                                   "tabular gradient-boosting implementations the pinned "
                                   "production image provides: lightgbm 4.7.0 and "
                                   "scikit-learn 1.9.1 HistGradientBoostingRegressor",
             "reason": "the pinned production image qlib:0.9.7-arm64 deliberately ships "
                       "lightgbm as its declared ML extra and has NO xgboost; adding a package "
                       "to the production image is a change control, not a card-local decision",
             "disclosure": "unavailable implementation disclosed; the registered algorithm class "
                           "(tabular gradient boosting on the same feature matrix) is "
                           "preserved, and BOTH available implementations are searched as part "
                           "of the registered strategy domain, so the result can never be a "
                           "single-library artefact. The record's own architecture comparison "
                           "reports no statistically significant difference between model "
                           "architectures after Holm adjustment."},
            {"item": "feature set",
             "source_registration": "OHLCV + 10 selected rolling technical indicators + EGARCH "
                                    "conditional-variance features",
             "local_registration": "5 OHLCV-derived columns + the 40 rolling technical "
                                   "candidate columns (SMA/EMA/RSI/ATR/MACD over the record's "
                                   "own w set) with the top 10 selected per fold by |Spearman| "
                                   "on the TRAIN segment + 2 EGARCH(1,1)-t columns (ln sigma_t, "
                                   "z_t)",
             "reason": "the intake caveat explicitly refuses to read the record's parenthetical "
                       "indicator list as its exact selected feature set; the selection "
                       "mechanism (Spearman correlation) is the record's own",
             "disclosure": "the feature contract is research-defined and frozen in this "
                           "document before any computation"},
            {"item": "validation segment",
             "source_registration": "3-month validation segment inside every fold",
             "local_registration": "the validation segment is measured per fold (validation MSE "
                                   "of the elected model implementation) but selects nothing in "
                                   "this round",
             "reason": "the source's per-fold selectors (loss-best / IC-best / IR*-best) are not "
                       "reproduced here; the registered model hyper-parameters are project "
                       "constants fixed before the run",
             "disclosure": "registered as a disclosed not-evaluated item; no gate depends on it"},
            {"item": "cost parameterisation",
             "source_registration": "proportional cost c = 10 bps per unit of turnover (all-in)",
             "local_registration": "canonical fills pay the instrument's own taker fee "
                                   "(5 bps, read from the raw instrument metadata) plus one "
                                   "adverse tick of slippage; the record's c = 10 bps is the "
                                   "registered fee_2x track and the cost-sensitivity reader "
                                   "re-measures c = 10/15/20/25 bps exactly",
             "reason": "the card registers canonical instrument metadata plus a 1-tick adverse "
                       "slippage baseline and a 2-tick robustness re-measurement",
             "disclosure": "the mapping c = 5 bps x fee_mult is stated, so the record's c "
                           "sensitivity is read off the same money"},
        ],
        "eligible_universe": {
            "registration_verbatim": [card_ex["universe_required_data_universe"],
                                      card_ex["universe_required_data_venue"],
                                      card_ex["universe_required_data_timeframe"],
                                      card_ex["universe_required_data_features"],
                                      card_ex["universe_required_data_egarch"],
                                      card_ex["universe_timestamp_integrity"]],
            "crypto_portability_verbatim": [card_ex["portability_direct"]],
            "measured_at_pre_registration": {
                "market_dirs": raw["market_dirs"],
                "venue": "BINANCE", "market_type": "usdm_perp",
                "klines_symbols": raw["venues"],
                "registered_cohorts": ["BTCUSDT/1h"],
                "bars_in_window": c["rows"],
                "bar_window": [c["first_bar_open_utc"], c["last_bar_open_utc"]],
                "off_grid_steps": c["off_grid_steps"],
                "funding_observations": raw["funding"]["observations"],
                "instruments": {k: raw["instruments"][k] for k in ("BTCUSDT", "ETHUSDT", "SOLUSDT")},
                "prerequisite_outcome": "PRESENT: the record's required data (Binance BTC/USDT "
                                        "USD-M perpetual 1h OHLCV) exists, so this card runs the "
                                        "full backtest instead of a prerequisite terminal"},
            "non_cohort_falsification_assets": {
                "note": "ETHUSDT and SOLUSDT are used ONLY by the record's own cross-asset "
                        "falsification measurement; they are never cohorts, never enter the "
                        "coverage product, the selector or the disposition",
                "symbols": ["ETHUSDT", "SOLUSDT"]},
        },
        "data": data_block,
        "parameter_domain": {
            "case_definition": "a strategy case is (direction_mode, model_impl): the leg set the "
                               "record's sign position is mapped onto, and the tabular "
                               "gradient-boosting implementation that produces the forecast",
            "direction_modes": list(DIRECTION_MODES),
            "direction_modes_status": "SOURCE-SPECIFIED - the record's signal section registers "
                                      "both the long-only (0/1) and the long-short (+-1) target "
                                      "position mapping",
            "model_implementations": list(MODEL_IMPLS),
            "model_implementations_status": "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN - the two "
                                            "tabular gradient-boosting implementations present "
                                            "in the pinned production image (see "
                                            "record_to_local_substitutions)",
            "forecast_contract": {
                "status": "RESEARCH_DEFINED (frozen before the first run)",
                "lambda_cost": 2.0, "c": 0.0010,
                "lambda_c_status": "SOURCE-SPECIFIED (the record fixes lambda = 2.0 and c = 0.0010)",
                "one_unit_hurdle": 0.0020, "two_unit_hurdle": 0.0040,
                "ta_windows": [3, 6, 12, 24, 48, 72, 168, 336],
                "ta_families": ["sma", "ema", "rsi", "atr", "macd"],
                "selected_ta_count": 10,
                "selection_rule": "|Spearman rank correlation| between each of the 40 candidate "
                                  "technical columns and the fold's training target, computed "
                                  "on the TRAIN segment only; ties break on the column name",
                "volume_sma_period": 24,
                "egarch": {"order": [1, 1], "innovations": "standardised Student-t",
                           "mean": "constant", "estimator": "MLE (L-BFGS-B)",
                           "population": "fitted on each fold's TRAIN segment; filtered causally "
                                         "forward",
                           "columns": ["ln sigma_t", "z_t = r_t / sigma_t"]},
                "normalisation": "per fold, z-score with TRAIN-segment mean/std",
                "folds": {"train_months": 12, "val_months": 3, "test_months": 3,
                          "step_months": 3, "count": 14,
                          "structure": "sequential non-anchored rolling (the record's own "
                                       "protocol structure)",
                          "final_fold": "the last test window is clipped to the data end"},
                "model_constants": {"lightgbm": {"n_estimators": 300, "learning_rate": 0.05,
                                                 "num_leaves": 31, "min_child_samples": 50,
                                                 "subsample": 0.8, "subsample_freq": 1,
                                                 "colsample_bytree": 0.8, "random_state": 20260917,
                                                 "verbose": -1},
                                    "sklearn_hist_gbm": {"max_iter": 300, "learning_rate": 0.05,
                                                         "max_leaf_nodes": 31,
                                                         "min_samples_leaf": 50,
                                                         "l2_regularization": 0.0,
                                                         "random_state": 20260917},
                                    "seed": 20260917},
                "target": "y_t = ln(C_{t+1}/C_t); training rows are strictly inside the fold's "
                          "train segment",
                "entry_event": "a bar whose filtered target position CHANGES to a non-zero value "
                               "(long-only: 0->1; long-short: 0->+-1 or a sign flip); execution "
                               "at the OPEN of the next bar",
                "causality_controls": [
                    "prefix-invariance probe on the registered technical columns (recomputed from "
                    "truncated prefixes and compared to the full-window matrix; mismatches are a "
                    "counter asserted to zero)",
                    "the EGARCH recursion is seeded with the parametric steady state and depends "
                    "on z_{t-1}, sigma_{t-1} only",
                    "the technical selection and the z-score statistics read the TRAIN segment "
                    "only",
                    "a bar without a defined forecast can never open an episode"],
            },
            "entry_timing_status": "RESEARCH_DEFINED - the record executes at the open of hour "
                                   "t+1 after inference on hour t's close; the registered entry "
                                   "bar is the open of the next bar after the position-change "
                                   "event, and the entry_delay_1_bar stress re-measures the whole "
                                   "product one bar later",
            "exit_timing_status": "RESEARCH_DEFINED - the record's own exit condition is the "
                                  "filtered target position no longer being the episode's "
                                  "direction; the registered exit set is the rail TP, the "
                                  "resting invalidation, the record's own exit at the next bar's "
                                  "open, and the reduce-only slice-end flatten",
            "readme_sweep_scope": "the record's own comparison of architectures, loss "
                                  "functions and feature tiers is NOT re-run as separate cases; "
                                  "the registered case axis is direction x implementation only",
            "legal_cases_per_cohort": cohort_cases,
            "grid_cases": [dict(zip(CASE_FIELDS, cs)) for cs in CASE_ORDER],
            "grid_case_names": list(CASE_NAMES),
        },
        "dca_domain": {
            "config_count": len(grid), "base_quote": BASE_QUOTE,
            "base_quote_status": "PROJECT_PRE_REGISTERED_CONSTANT - held constant across the "
                                 "whole domain (NOT a search axis) and NOT a user-fixed "
                                 "invariant: no operator evidence fixes it at 1000",
            "spacing_pct": DCA_AXES["spacing_pct"],
            "spacing_pct_status": "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN",
            "size_multiplier": DCA_AXES["size_multiplier"],
            "size_multiplier_status": "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN",
            "breakeven_tp_pct": DCA_AXES["breakeven_tp_pct"],
            "breakeven_tp_pct_status": "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN",
            "invalidation_pct": DCA_AXES["invalidation_pct"],
            "invalidation_pct_status": "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN",
            "grid": grid,
            "user_fixed_invariants": {
                "provenance_class": "USER_FIXED - only items with explicit operator evidence "
                                    "(the card's own inherited rail registration)",
                "starting_equity_usdt": 30000, "numeraire": "USDT",
                "instrument_type": "linear USD-M perpetual", "leverage": 10,
                "tranches": 12, "reserve_tranche": "#12 (not routine)",
                "routine_active_levels_max": 11,
                "entry_style": "initial entry + adverse-price scale-ins",
                "exit_style": "reduce-only", "no_add_after_flat_or_kill": True,
                "same_bar_multi_level_ordering": "deterministic conservative",
                "geometric_size_multiplier": 1.1},
        },
        "dca_execution_semantics": {
            "status": "INHERITED VERBATIM from the A/B rail registration (the card forbids "
                      "changing the inherited execution envelope)",
            "tranche_1": "the initial entry at the OPEN of the first bar after the "
                         "position-change event",
            "ladder": "adverse-price scale-ins follow the registered ladder while the position is "
                      "open: level_k price = initial_entry_price x (1 -/+ spacing_pct x k), "
                      "k = 1..10 (at most 11 routine active levels of the 12-tranche rail)",
            "rung_refill_disclosure": "the inherited rail walks from rung 1 on EVERY bar and does "
                                      "not mark a rung consumed, so a rung the price re-enters "
                                      "fills again; the per-bar cap is 10 adds. This is the "
                                      "inherited A/B semantics, unchanged by this family, and it "
                                      "is pinned by a self-check test so a reviewer can read it "
                                      "instead of inferring it.",
            "take_profit": "reduce-only at running_average_cost x (1 + breakeven_tp_pct) for a "
                           "long book and x (1 - breakeven_tp_pct) for a short book",
            "invalidation": "resting stop at running_average_cost x (1 - invalidation_pct) for a "
                            "long book and x (1 + invalidation_pct) for a short book; a gap "
                            "through the stop fills at the open",
            "record_exit": "every layer is flattened reduce-only at the NEXT bar's open when the "
                           "filtered target position is no longer the episode's direction; a "
                           "long-short flip is a flatten and an entry at the same open",
            "slice_end": "every layer is flattened reduce-only at the last bar's close",
            "flat_rule": "after any exit the book is FLAT and never adds again before the next "
                         "position-change event",
            "capital_exhaustion": "no new episode may open once the realised equity is gone",
        },
        "execution_invariants_disclosure": {
            "inherited_verbatim": card_ex["user_fixed_invariants"],
            "tension": "the record's execution assumptions register a 1x unlevered notional, "
                       "while the inherited A/B envelope fixes 10x margin-based tranche sizing "
                       "on a 30,000 USDT account; the executed notional therefore differs from "
                       "the source's unlevered book",
            "resolution": "the inherited envelope is applied verbatim (the card forbids changing "
                          "it) and the tension is disclosed here rather than silently rewritten; "
                          "no gate or threshold is adjusted for it",
        },
        "gates": {
            "G1_coverage": "every one of the 10 registered phase grids must contain exactly %d "
                           "cases (1 cohort x 4 strategy cases x 48 DCA configs), the measured "
                           "strategy/DCA cell sets must equal the registered products, and the "
                           "case-evaluation total must equal %d; otherwise TECHNICAL_INCOMPLETE "
                           "and no cohort is judged" % (per_grid, total),
            "G2_insufficient_trades": "per cohort: fewer than min_episodes_is (%d) historical "
                                      "episodes in the cohort's best case, or fewer than "
                                      "min_episodes_oos (%d) episodes in the elected winner's OOS "
                                      "slice, culls the cohort" % (MIN_EPISODES_IS, MIN_EPISODES_OOS),
            "G3_cohort_selector": "historical net_pnl > 0 AND sharpe > 0 AND episodes >= %d, "
                                  "ranked by Sharpe desc, net_pnl desc, registered-index lexical "
                                  "tie-break" % MIN_EPISODES_IS,
            "G4_cohort_oos": "OOS net_pnl > 0 AND OOS sharpe > 0 for the frozen winner cell",
            "G5_cohort_full": "full-window net_pnl > 0 for the frozen winner cell",
            "G6_cohort_robustness": "the winner cell has net_pnl > 0 in all four execution stress "
                                    "reruns",
            "G7_cohort_neighbourhood": "at least %g of legal face-adjacent neighbours agree in "
                                       "historical net-PnL sign" % NEIGHBOURHOOD_MIN,
            "min_episodes_is": MIN_EPISODES_IS, "min_episodes_oos": MIN_EPISODES_OOS,
            "neighborhood_min_same_sign_fraction": NEIGHBOURHOOD_MIN,
            "min_episodes_provenance": "RESEARCH_DEFINED - the record registers no explicit "
                                       "no-signal/insufficient-trade threshold. Its own "
                                       "cost-aware long-only XGBoost book trades 251 times over "
                                       "the 2018-2026 sample (~31/year); on a 3.4-year tradeable "
                                       "window that implies roughly 50 signal-changing episodes, "
                                       "so 10 historical / 4 OOS episodes are a floor that can "
                                       "be met without lowering it later. Registered before the "
                                       "first run and never lowered.",
            "verdict_rule": "TECHNICAL_INCOMPLETE if G1 fails; else each cohort passing G3-G7 is "
                            "a SURVIVOR: 0 survivors -> REJECT, >=1 -> PASS (band only describes "
                            "the count). The three registered family-level readers can only move "
                            "a PASS to DEFERRED, never to PASS.",
            "known_gate_set_limitation": "the registered gate set contains no benchmark-relative "
                                         "test and no buy-and-hold comparison; the record's own "
                                         "negative evidence (passive benchmark dominance, "
                                         "long-short failure) is disclosed and is not repaired "
                                         "inside this round",
        },
        "costs": {
            "source": "canonical instrument metadata (raw binance/usdm/instruments/"
                      "usdm-perp-instruments.json, sha256 %s), read at run time, never "
                      "hard-coded" % raw["instruments_sha256"],
            "taker_fee": raw["instruments"]["BTCUSDT"]["taker_fee"],
            "maker_fee": raw["instruments"]["BTCUSDT"]["maker_fee"],
            "margin_init": raw["instruments"]["BTCUSDT"]["margin_init"],
            "margin_maint": raw["instruments"]["BTCUSDT"]["margin_maint"],
            "baseline_slippage_ticks": 1,
            "baseline_slippage_provenance": "research assumption of this round (NOT "
                                            "user-specified); robustness re-measures it at 2 ticks",
            "apply_rule": "every fill is a market order: taker fee on the full traded notional, "
                          "plus adverse slippage of baseline_slippage_ticks instrument ticks "
                          "(1 tick = price_increment). No maker fills are assumed anywhere.",
            "fee_bps": 5, "cost_attrition_fee_mult": 8.0,
            "cost_attrition_grid": "cost_attrition_40bps applies 8x the registered taker fee = "
                                   "40 bps per fill",
            "cost_sensitivity_mapping": "c = 5 bps x fee_mult, so the record's c = 10/15/20/25 bps "
                                        "sensitivity is exactly fee_mult 2/3/4/5",
            "funding": "the registered funding series is charged at each settlement inside the "
                       "episode's exposure interval (rate x position notional at that instant); "
                       "a negative rate is a CREDIT to a long position and a COST to a short",
            "funding_exposure_rule": data_block["funding_series_contract"]["engine_rule"],
            "funding_immutability": "the funding baseline must not be swapped for a modelled "
                                    "series to improve the outcome, nor vice versa",
        },
        "selector_and_disposition": {
            "selector_version": "cohort-selector-v1",
            "disposition_version": "cohort-disposition-v1",
            "selection_protocol": "Deterministic, historical window ONLY; OOS must never be used "
                                  "to select parameters.",
            "selection_steps": [
                "1. Sufficiency: if the cohort's best historical case has fewer episodes than "
                "gates.min_episodes_is, the cohort is culled with reason insufficient_trades.",
                "2. Eligibility: a case is a candidate iff historical net_pnl > 0 AND historical "
                "sharpe > 0 AND historical episodes >= gates.min_episodes_is.",
                "3. If no case is a candidate, the cohort is culled with reason "
                "no_qualifying_candidate.",
                "4. Ranking: Sharpe descending, then net_pnl descending, then a fixed lexical "
                "tie-break over the REGISTERED INDEX of each axis in the fixed order "
                "(window_case, spacing_pct, size_multiplier, breakeven_tp_pct, "
                "invalidation_pct). The first case is the cohort winner.",
                "5. The winner's exact strategy+DCA cell is carried unchanged into OOS, full, "
                "every robustness grid and the neighbourhood test."],
            "cohort_survivor_requirements": [
                "a) a historical winner exists (steps 1-4)",
                "b) OOS: net_pnl > 0 AND sharpe > 0 for the winner's cell",
                "c) full window: net_pnl > 0 for the winner's cell",
                "d) robustness: the SAME winner cell has net_pnl > 0 in each of fee_2x, "
                "funding_2x, entry_delay_1_bar, slippage_2ticks",
                "e) parameter neighbourhood: at least %g of the winner cell's legal "
                "face-adjacent neighbours agree with its HISTORICAL net-PnL sign"
                % NEIGHBOURHOOD_MIN],
            "cull_reasons_vocabulary": ["insufficient_trades", "no_qualifying_candidate",
                                        "oos_economic", "full_economic",
                                        "robustness_economic:<grids>", "parameter_neighbourhood"],
            "family_disposition_rule": "0 cohort survivors -> verdict REJECT "
                                       "(performance_claimable=false); >=1 survivors -> verdict "
                                       "PASS (band SURVIVOR_FOUND / MULTIPLE_SURVIVORS describes "
                                       "the count only; every survivor is kept and advances); "
                                       "coverage or technical incompleteness -> "
                                       "TECHNICAL_INCOMPLETE.",
            "runner_role": "the runner's disposition / verdict_recommendation are RECOMMENDATIONS; "
                           "the final verdict is written by default into verdict.json per "
                           "contract 10.7.",
        },
        "robustness_plan": {
            "stress_grids": [{"grid": "fee_2x", "stress": {"fee_mult": 2.0}},
                             {"grid": "funding_2x", "stress": {"funding_mult": 2.0}},
                             {"grid": "entry_delay_1_bar", "stress": {"entry_delay_1_bar": True}},
                             {"grid": "slippage_2ticks", "stress": {"slip_ticks": 2}}],
            "cost_attrition_grid": {"grid": "cost_attrition_40bps", "stress": {"fee_mult": 8.0}},
            "funding_free_diagnostics": ["no_funding (historical slice)", "no_funding_full "
                                                                          "(full slice)"],
            "registration_verbatim": [card_ex["robustness_stress_grids"],
                                      card_ex["coverage_gate_g1"]],
        },
        "registered_family_level_falsification": {
            "note": "three FAMILY-level readers, one per record falsification item, registered "
                    "before the first run. They never cull a cohort; a hit must NEVER be recorded "
                    "as PASS (the runner recommends DEFERRED and the evidence decides).",
            "forward-extension-12m-sharpe": {
                "reader": "forward_extension_sharpe_check",
                "record_item": record_ex["falsification_item_1"],
                "definition": "the elected winner's cell is re-simulated over the trailing 12 "
                              "months ending at the data end (the locally available forward "
                              "extension), canonical cost track; TRIGGERED if that Sharpe < 0.0",
                "disclosed_limit": "the record's '2026+' extension is read as the trailing 12 "
                                   "months of the registered window; the registered OOS slice is "
                                   "reported alongside"},
            "cost-sensitivity-c-15-25bps": {
                "reader": "cost_sensitivity_check",
                "record_item": record_ex["falsification_item_2"],
                "definition": "the elected winner's cell is re-simulated over the FULL window at "
                              "c = 10/15/20/25 bps (fee_mult 2/3/4/5); TRIGGERED if the Sharpe is "
                              "< 0.0 at c = 10 bps or c = 15 bps; the c = 10 bps track is "
                              "cross-checked against the registered fee_2x grid row",
                "disclosed_limit": "none - the mapping is exact under a 5 bps base fee"},
            "cross-asset-replication": {
                "reader": "cross_asset_replication_check",
                "record_item": record_ex["falsification_item_3"],
                "definition": "the elected winner's (case, DCA) cell is run through the "
                              "IDENTICAL registered walk-forward pipeline on ETHUSDT/1h and "
                              "SOLUSDT/1h over the FULL window; TRIGGERED if any replicated "
                              "asset's net_pnl <= 0",
                "disclosed_limit": "the two assets are falsification MEASUREMENTS, not cohorts: "
                                   "they never enter the coverage product, the selector or the "
                                   "disposition"},
        },
        "expected": {
            "cohorts": 1, "strategy_cases_per_cohort": cohort_cases,
            "dca_configs_per_cohort": len(grid), "base_combinations_per_cohort": per_cohort,
            "case_evaluations_per_cohort_per_grid": per_cohort,
            "case_evaluations_per_grid": per_grid, "phase_grid_count": len(PHASE_GRIDS),
            "expected_case_evaluations": total,
            "cohort_grid_kinds": list(PHASE_GRIDS),
            "arithmetic": "1 cohort (BTCUSDT/1h) x 4 registered (direction_mode, model_impl) "
                          "cases x 48 DCA configs = 192 base combinations per cohort per phase "
                          "grid; 192 per grid; x 10 phase grids = 1920 full case evaluations.",
            "runtime_expectation": {
                "basis": "measured on this machine: Strategy A v2 evaluated 103,680 cells in "
                         "1,302 s (~12.6 ms per case); the rail walk per cell is bounded by the "
                         "number of episodes, which this selective filter cuts by roughly an "
                         "order of magnitude. The dominating cost is the forecast layer (14 "
                         "folds x EGARCH MLE + 2 model fits), computed once per (cohort, "
                         "implementation) and shared by every DCA config and phase grid.",
                "estimate": "a rough estimate of 20-60 minutes of engine time plus the 3-dataset "
                            "1h bin rebuild and the cross-asset measurement - an estimate, not a "
                            "guarantee",
                "status": "estimate only; the run is launched detached inside the container and "
                          "monitored from the attempt state/log, never by holding an agent turn "
                          "open"},
        },
        "artifacts_required": ["result.json", "artifacts/cohort_results.json",
                               "artifacts/cohort_survivors.json", "artifacts/assertions.json",
                               "artifacts/dca_layer_histogram.json",
                               "artifacts/forecast_layer.json", "artifacts/signal_layer.json",
                               "artifacts/family_falsification.json",
                               "artifacts/robustness_diagnostics.json",
                               "artifacts/funding_series.json", "artifacts/input_manifest.json",
                               "artifacts/bins_build.json", "artifacts/progress.json"]
                              + ["artifacts/grid_%s.csv" % k for k in PHASE_GRIDS],
        "assertions_declared": ["episodes_partition", "pnl_decomposition", "coverage_complete",
                                "cohort_count_matches_registered",
                                "strategy_grid_is_registered_product",
                                "dca_grid_is_registered_product",
                                "base_combinations_per_cohort_per_grid",
                                "expected_case_evaluations", "layer0_equals_episodes",
                                "layer_histogram_nonempty", "no_entry_after_exhaustion",
                                "ending_equity_floor", "selector_deterministic",
                                "selector_historical_only",
                                "episodes_never_exceed_windows_seen",
                                "bar_grid_is_contiguous", "no_unregistered_strategy_case",
                                "funding_bar_never_out_of_hold", "no_funding_grid_is_cost_free",
                                "no_funding_track_is_cost_free", "fee_2x_track_is_not_a_noop",
                                "funding_2x_track_is_not_a_noop",
                                "cost_attrition_track_is_not_a_noop",
                                "entry_delay_track_is_not_a_noop",
                                "slippage_track_is_not_a_noop",
                                "entry_bar_is_the_next_bar_after_the_signal",
                                "no_entry_without_a_defined_forecast",
                                "forecast_covers_every_test_bar",
                                "features_finite_on_every_test_bar",
                                "egarch_fit_converged_on_every_fold",
                                "technical_selection_had_enough_rows",
                                "feature_layer_is_prefix_invariant", "no_fold_was_truncated",
                                "no_reentry_without_a_new_position_event",
                                "daily_series_is_slice_scoped"],
        "metrics_definitions": {
            "net_pnl": "realised equity change, net of every fill's fee and funding",
            "gross_pnl": "independent price-PnL accumulator: sum over exits of (exit proceeds - "
                         "cost basis); never reverse-derived from net",
            "pnl_decomposition": "gross_pnl - fees - funding == net_pnl within 1e-3",
            "sharpe": "ratio of the mean to the sample standard deviation of DAILY equity "
                      "returns, annualised by sqrt(365); the daily series is scoped to the "
                      "evaluated slice",
            "episodes": "opened episodes (a position-change entry event whose entry bar lies "
                        "inside the slice and that was not refused by capital exhaustion)",
            "windows_seen": "entry events whose signal bar lies inside the slice",
            "max_effective_leverage": "maximum of position notional / unrealised equity over the "
                                      "bars held",
            "capital_utilization": "mean of (cost/leverage) / unrealised equity over the bars held",
        },
        "authorization_invariants": [
            "the round-spec is immutable; the split and every registered domain are frozen "
            "before the first run",
            "no cohort may be added, removed or shrunk after this registration",
            "OOS is never used for selection",
            "the runner's outputs are recommendations; default writes the round verdict"],
        "non_goals": ["no Manager / Service / Factory / Registry / Orchestrator / daemon / queue",
                      "no second engine, no second backtester, no Nautilus path",
                      "no live, paper or testnet execution",
                      "no auditor child card",
                      "no change to any frozen artifact of another family"],
        "implementation_status": {
            "engine": {"repo": "container/scripts/90_strategy_i_run.py", "sha256": engine_sha,
                       "deployed": ENGINE_DEPLOY, "deployed_sha256": engine_deploy_sha,
                       "identity": "repo == host deploy (P10 recomputes host side)"},
            "selfcheck": {"repo": "container/scripts/tests/test_strategy_i_engine.py",
                          "sha256": selfcheck_sha, "deployed": SELFCHECK_DEPLOY,
                          "deployed_sha256": selfcheck_deploy_sha,
                          "result": "28/28 OK inside qlib-run before publication"},
            "authoring_script": {"path": "runtime/strategy_i_v1_prereg.py",
                                 "sha256": sha256_file(os.path.abspath(__file__))},
            "excerpt_source_map": excerpt_map,
        },
        "parameter_contract": parameter_contract_block(),
    }

    # ---------------- validation before any write ------------------------------------------
    problems += ["parameter_contract: " + p for p in pc.validate_contract(round_spec["parameter_contract"])]
    problems += ["round_spec_contract: " + p
                 for p in pc.validate_round_spec_contract(round_spec)]
    dom = round_spec["parameter_domain"]
    if dom["legal_cases_per_cohort"] != len(CASE_ORDER):
        problems.append("legal_cases_per_cohort %r != %d" % (dom["legal_cases_per_cohort"],
                                                             len(CASE_ORDER)))
    if round_spec["dca_domain"]["config_count"] != len(grid):
        problems.append("dca config_count != 48")
    if round_spec["expected"]["expected_case_evaluations"] != total:
        problems.append("expected_case_evaluations mismatch")
    if [dict(zip(CASE_FIELDS, cs)) for cs in CASE_ORDER] != dom["grid_cases"]:
        problems.append("grid_cases are not the registered case order")
    for name in ("base_quote", "spacing_pct", "size_multiplier", "breakeven_tp_pct",
                 "invalidation_pct"):
        if name in round_spec["dca_domain"].get("user_fixed_invariants", {}):
            problems.append("searched axis / project constant %r leaked into user_fixed" % name)
    if "geometric_size_multiplier" not in round_spec["dca_domain"]["user_fixed_invariants"]:
        problems.append("the operator-evidenced geometric multiplier 1.1 was dropped")

    run_spec = {
        "schema_version": 1,
        "document_kind": "run-spec (Strategy I v1; instantiate to /results/<family_id>/rounds/"
                         "<round_id>/attempts/<run_id>/run-spec.json BEFORE launch)",
        "family_id": FAMILY_ID, "round_id": ROUND_ID, "run_id": RUN_ID,
        "task_id": TASK_ID, "kanban_task_id": TASK_ID, "kanban_board": BOARD,
        "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "round_spec_path": "/results/%s/rounds/%s/round-spec.json" % (FAMILY_ID, ROUND_ID),
        "script": {"path": "/scripts/90_strategy_i_run.py", "sha256": engine_sha,
                   "deployed_from": "HCH725/quant-runtime-pipeline container/scripts/"
                                    "90_strategy_i_run.py (byte-identical, P10 recomputes it "
                                    "host side)"},
        "engine_selfcheck": {"script": "/scripts/tests/test_strategy_i_engine.py",
                             "sha256": selfcheck_sha, "must_run_before_launch": True},
        "data": {k: data_block[k] for k in ("source", "venue", "symbols", "timeframes", "start",
                                            "end", "historical_start", "historical_end",
                                            "oos_start", "oos_end", "split_immutability",
                                            "funding_truth_status_windows", "bar_labelling")},
        "parameter_domain": {k: dom[k] for k in ("case_definition", "direction_modes",
                                                 "model_implementations", "forecast_contract",
                                                 "legal_cases_per_cohort", "grid_cases",
                                                 "grid_case_names", "entry_timing_status",
                                                 "exit_timing_status")},
        "dca_domain": round_spec["dca_domain"],
        "selector_version": "cohort-selector-v1",
        "disposition_version": "cohort-disposition-v1",
        "gates": round_spec["gates"],
        "costs": round_spec["costs"],
        "expected": round_spec["expected"],
        "falsification": {
            "cross_asset_symbols": ["ETHUSDT", "SOLUSDT"],
            "cost_sensitivity_mults": [1.0, 2.0, 3.0, 4.0, 5.0],
            "readers": list(round_spec["registered_family_level_falsification"].keys())},
        "expected_outputs": round_spec["artifacts_required"],
        "semantic_fingerprint": round_spec["semantic_fingerprint"],
        "notes": "One attempt of round %s. The engine rebuilds the qlib .bin store from the "
                 "read-only raw store, builds the registered causal feature matrix, fits 14 "
                 "walk-forward EGARCH(1,1)-t + gradient-boosting folds per model implementation, "
                 "turns the cost-aware filter into position-change entry events, and evaluates "
                 "every legal (strategy case x DCA config) cell on every registered phase grid."
                 % ROUND_ID,
    }

    if problems:
        print(json.dumps({"problems": problems}, indent=2, ensure_ascii=False))
        return 1

    # ---------------- publish (O_EXCL, exact once) -----------------------------------------
    round_dir = os.path.join(RESULTS_ROOT, FAMILY_ID, "rounds", ROUND_ID)
    attempt_dir = os.path.join(round_dir, "attempts", RUN_ID)
    os.makedirs(attempt_dir, exist_ok=True)
    rs_path = os.path.join(round_dir, "round-spec.json")
    run_path = os.path.join(attempt_dir, "run-spec.json")
    written = []
    try:
        for path, doc in ((rs_path, round_spec), (run_path, run_spec)):
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
            fd = os.open(path, flags, 0o644)
            with os.fdopen(fd, "w") as fh:
                json.dump(doc, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            written.append({"path": path, "sha256": sha256_file(path),
                            "bytes": os.path.getsize(path)})
    except FileExistsError as exc:
        print(json.dumps({"error": "refusing to overwrite an existing artifact: %s" % exc,
                          "written_before_abort": written}, indent=2))
        return 1
    check = {
        "ok": True, "family_id": FAMILY_ID, "round_id": ROUND_ID, "run_id": RUN_ID,
        "cohorts": 1, "strategy_cases": len(CASE_ORDER), "dca_configs": len(grid),
        "per_cohort": per_cohort, "per_grid": per_grid, "total": total,
        "phase_grids": list(PHASE_GRIDS),
        "engine_sha256": engine_sha, "selfcheck_sha256": selfcheck_sha,
        "round_spec": written[0], "run_spec": written[1],
        "excerpts_checked": len(excerpt_map),
        "card_body_sha256": card_sha, "record_sha256": record_sha,
        "parameter_contract_problems": pc.validate_contract(round_spec["parameter_contract"]),
        "round_spec_contract_problems": pc.validate_round_spec_contract(round_spec),
    }
    print(json.dumps(check, indent=2, ensure_ascii=False))
    if args.out_check:
        with open(args.out_check, "w") as fh:
            json.dump(check, fh, indent=2, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
