#!/usr/bin/env python3
"""Strategy K — pre-registration (round-spec r1) + run-spec authoring.

Family: `compact-rienet-volatility-drag-mitigation-leveraged-gmv-2026-09-02`, card t_54d4eaf1.
Round r1 opens the full backtest on the registered LOCAL eligible universe (the four canonical
USD-M perpetuals) under the system-owned lifecycle footer of contract section 14.4.

Pure stdlib.  Nothing is written until EVERY check passes:

  1. every verbatim excerpt is asserted as an exact substring of its declared source (the frozen
     card body read back from the board DB with the system-owned lifecycle footer kept separate,
     and the canonical wiki record);
  2. the canonical raw store is re-measured (the daily bar grid of all four symbols, funding
     settlements with their truth_status, instrument metadata) and the measurement is embedded;
  3. the registered pre-run feasibility probe (the fresh long-state event supply of every
     registered case, measured before the freeze with the same kernel) is embedded;
  4. the generic v1.8 parameter contract is generated FROM this document's own registered domains
     and validated (validate_contract / validate_round_spec_contract == 0 problems);
  5. the registered domain arithmetic is recomputed
     (4 cohorts x 12 strategy cases x 48 DCA configs x 10 phase grids = 23,040 evaluations);
  6. the engine bytes must be identical between the repo and the host deploy, and the engine's
     registered axes must match this document's (text-level pin of the frozen constants);
  7. both output files are published with O_CREAT|O_EXCL (exact once; a rerun is refused).

usage:  runtime/_author_strategy_k_template.py [--out-check <json>] [--dry-run]
"""
from __future__ import annotations

import argparse
import calendar
import glob
import gzip
import hashlib
import json
import os
import re
import sqlite3
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAMILY_ID = "compact-rienet-volatility-drag-mitigation-leveraged-gmv-2026-09-02"
ROUND_ID = FAMILY_ID + "-r1"
RUN_ID = ROUND_ID + "-u1"
TASK_ID = "t_54d4eaf1"
BOARD = "quant-strategy-research"
RESULTS_ROOT = "/Volumes/ExpansionDrive/qlib-results"
RAW_ROOT = "/Volumes/ExpansionDrive/market-data-raw/binance/usdm"
RECORD_PATH = os.path.expanduser(
    "~/.hermes/wiki/quant/compact-rienet-volatility-drag-mitigation-leveraged-gmv-2026-09-02.md")
ENGINE_REPO = os.path.join(REPO, "container", "scripts", "110_strategy_k_run.py")
ENGINE_DEPLOY = "/Users/hong/workspace/qlib-apple-container/scripts/110_strategy_k_run.py"
SELFCHECK_REPO = os.path.join(REPO, "container", "scripts", "tests", "test_strategy_k_engine.py")
SELFCHECK_DEPLOY = ("/Users/hong/workspace/qlib-apple-container/scripts/tests/"
                    "test_strategy_k_engine.py")
PROBE_JSON = os.path.join(REPO, ".kanban-scratch", "k_parts", "k_probe.json")
CONTRACT = os.path.join(REPO, "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md")
CONTRACT_VERSION = "v1.9.0"
FOOTER_MARK = "---\nLIFECYCLE FOOTER"
CONTAINER = "qlib-run"
IMAGE = "qlib:0.9.7-arm64"
SYMBOLS = ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT")
TIMEFRAMES = ({"raw_interval": "1d", "qlib_freq": "day"},)
START, HISTORICAL_END, OOS_START, END = "2022-01-01", "2025-09-30", "2025-10-01", "2026-09-11"
MS_PER_DAY = 86400000

# ---- registered strategy domain (must equal the engine's frozen constants) ------------------
CLEAN_ARMS = ("raw", "shrink_const", "mp_clip")
CLEAN_LABELS = ("raw", "shrink", "mp")
LOOKBACKS = (250, 500, 750, 1200)
LOOKBACK_LABELS = tuple(str(d) for d in LOOKBACKS)
CASE_FIELDS = ("cl_raw", "cl_shrink", "cl_mp", "lb_250", "lb_500", "lb_750", "lb_1200")
CASE_ORDER = tuple(
    tuple([1 if a == i else 0 for a in range(len(CLEAN_ARMS))]
          + [1 if b == j else 0 for b in range(len(LOOKBACKS))])
    for j in range(len(LOOKBACKS)) for i in range(len(CLEAN_ARMS)))
CASE_NAMES = tuple("%s__lb%s" % (CLEAN_LABELS[i], LOOKBACK_LABELS[j])
                   for j in range(len(LOOKBACKS)) for i in range(len(CLEAN_ARMS)))
THETA = (1.0, 0.5, 2.0, 1.0, 0.05)
SHRINK_DELTA = 0.5
EQUAL_WEIGHT_TILT = 0.25
SIGNAL_WARMUP_BARS = int(max(LOOKBACKS))
SIGNAL_CONSTANTS = {"theta": list(THETA), "theta_source": "project pre-registered (the record's "
                    "trained values are not published); theta1..theta5 of Module 1",
                    "shrink_delta": SHRINK_DELTA,
                    "shrink_delta_source": "project pre-registered constant-scalar-shrinkage "
                                           "weight (the record's ablation arm (b) value)",
                    "mp_edge_formula": "(1 + sqrt(n / dt_in))^2 (spectrum SPLIT only; the floor "
                                       "is the measured bulk mean)",
                    "equal_weight_tilt": EQUAL_WEIGHT_TILT,
                    "equal_weight_tilt_source": "project pre-registered entry state: LONG exactly "
                    "while the registered long-only weight is at least an equal share",
                    "signal_warmup_bars": SIGNAL_WARMUP_BARS,
                    "panel_size": 4}
DCA_AXES = {"spacing_pct": [0.01, 0.02, 0.03, 0.04], "size_multiplier": [1.0, 1.1],
            "breakeven_tp_pct": [0.01, 0.02, 0.03], "invalidation_pct": [0.05, 0.10]}
DCA_FIELDS = ("spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct")
BASE_QUOTE = 1000.0
PHASE_GRIDS = ("historical", "oos", "full", "fee_2x", "funding_2x", "entry_delay_1_bar",
               "slippage_2ticks", "no_funding", "no_funding_full", "cost_attrition_40bps")
SELECTOR_VERSION = "cohort-selector-v1"
DISPOSITION_VERSION = "cohort-disposition-v1"
MIN_EPISODES_IS = 10
MIN_EPISODES_OOS = 4
NEIGHBOURHOOD_MIN = 0.6
FREEZE_MARGIN_CALLS = 2
FREEZE_OOS_SHARPE = 0.40
FULL_WINDOW_NET_PNL_TOL = 1e-9
USER_FIXED_INVARIANTS = [
    "starting_equity = 30,000 USDT", "USDT is the only numeraire", "linear USD-M perpetual",
    "leverage 10x", "12 tranches",
    "geometric size multiplier 1.1 (historical progression value; inside the size_multiplier "
    "axis it is a search candidate)",
    "tranche #12 is reserved (routine active levels max 11)",
    "initial entry + adverse-price scale-ins", "reduce-only exits",
    "same-bar multi-level crossing uses the deterministic conservative ordering",
    "no add after FLAT/kill"]


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def sha256_text(text):
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def load_pc():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "parameter_contract", os.path.join(REPO, "runtime", "parameter_contract.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def read_card_source():
    """The frozen card body (footer split out) and the full body, read-only from the board DB."""
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
            if row is None:
                con.close()
                continue
            comments = con.execute(
                "select body from task_comments where task_id = ? order by id",
                (TASK_ID,)).fetchall()
            con.close()
        except sqlite3.Error:
            continue
        body = row[0]
        override = next((c[0] for c in comments if c[0] and "OPERATOR OVERRIDE" in c[0]), None)
        return body.split(FOOTER_MARK)[0].rstrip() + "\n", body, override, db
    raise SystemExit("card body for %s not found in any board DB (%s)" % (TASK_ID, cands[:4]))


def between(text, start_mark, end_mark=None):
    """The substring [start_mark, end_mark) - the anchor line is KEPT (it is part of the excerpt).

    An end mark of None means 'to the end of the text'.
    """
    i = text.index(start_mark)
    if end_mark is None:
        return text[i:].rstrip("\n")
    j = text.index(end_mark, i + len(start_mark))
    return text[i:j].rstrip("\n")


def bullets(block):
    """The bullet items of a block (lines starting with '- '); sub-bullets are not items."""
    return [ln for ln in block.split("\n") if ln.startswith("- ")]


def numbered(block):
    out = []
    for line in block.split("\n"):
        s = line.strip()
        if len(s) > 2 and s[0].isdigit() and s[1] == ".":
            out.append(line)
    return out


def utc_ms(date_str):
    return int(calendar.timegm(time.strptime(date_str + " 00:00:00", "%Y-%m-%d %H:%M:%S"))) * 1000


def iso(ms):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ms / 1000.0))


def measure_raw():
    """Re-measure exactly what the engine will load: the daily bars of the registered window."""
    out = {"symbols": {}, "funding": {}, "klines_root": os.path.join(RAW_ROOT, "klines")}
    total_bars = 0
    lo_ms, hi_ms = utc_ms(START), utc_ms(END) + MS_PER_DAY - 1
    for sym in SYMBOLS:
        d = os.path.join(RAW_ROOT, "klines", sym, "1d")
        files = sorted(glob.glob(os.path.join(d, "*.jsonl.gz")))
        if not files:
            raise SystemExit("no daily kline files for %s in %s" % (sym, d))
        rows = 0
        first = last = None
        off_grid = 0
        for path in files:
            month = os.path.basename(path)[len(sym) + 4:-len(".jsonl.gz")]
            if not (START[:7] <= month <= END[:7]):
                continue
            with gzip.open(path, "rt") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    ms = int(rec["open_time_ms"])
                    if ms < lo_ms or ms > hi_ms:
                        continue
                    if last is not None and ms - last != MS_PER_DAY:
                        off_grid += 1
                    if first is None:
                        first = ms
                    last = ms
                    rows += 1
        expected = int((last - first) // MS_PER_DAY) + 1 if first is not None else 0
        out["symbols"][sym] = {
            "month_files": len(files), "bars_in_window": rows,
            "first_bar_open_utc": iso(first), "last_bar_open_utc": iso(last),
            "first_bar_open_date": iso(first)[:10], "last_bar_open_date": iso(last)[:10],
            "off_grid_steps": off_grid, "expected_if_contiguous": expected,
            "contiguous": bool(rows == expected and off_grid == 0)}
        total_bars += rows
        fp = os.path.join(RAW_ROOT, "funding", sym, "%s-funding.jsonl.gz" % sym)
        ftimes, truth = [], {"official": 0, "modeled_funding": 0, "other": 0}
        with gzip.open(fp, "rt") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                ms = int(rec["funding_time_ms"])
                if ms < utc_ms(START) or ms > utc_ms(END) + MS_PER_DAY - 1:
                    continue
                ftimes.append(ms)
                st = rec.get("truth_status")
                truth[st if st in ("official", "modeled_funding") else "other"] += 1
        out["funding"][sym] = {"observations": len(ftimes),
                               "first": iso(ftimes[0]) if ftimes else None,
                               "last": iso(ftimes[-1]) if ftimes else None,
                               "truth_status_counts": truth,
                               "settlement_hours_utc": sorted(set(
                                   time.strftime("%H:%M", time.gmtime(t / 1000.0))
                                   for t in ftimes))}
    inst_path = os.path.join(RAW_ROOT, "instruments", "usdm-perp-instruments.json")
    with open(inst_path) as fh:
        inst_doc = json.load(fh)
    inst = {}
    for item in inst_doc["instruments"]:
        f = item["fields"]
        inst[f["raw_symbol"]] = {"id": f["id"], "type": f.get("type"),
                                 "settlement_currency": f.get("settlement_currency"),
                                 "price_increment": float(f["price_increment"]),
                                 "taker_fee": float(f["taker_fee"]),
                                 "maker_fee": float(f["maker_fee"]),
                                 "margin_init": float(f["margin_init"]),
                                 "margin_maint": float(f["margin_maint"])}
    out["instruments"] = {s: inst[s] for s in SYMBOLS if s in inst}
    missing = [s for s in SYMBOLS if s not in inst]
    if missing:
        raise SystemExit("instruments missing from the canonical metadata: %r" % missing)
    out["instruments_sha256"] = sha256_file(inst_path)
    out["market_dirs"] = sorted(os.listdir(os.path.join(RAW_ROOT, "..")))
    out["total_bars_in_window"] = total_bars
    return out


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


def load_probe():
    """The registered pre-run feasibility probe (measured with the same kernel, before the freeze)."""
    with open(PROBE_JSON) as fh:
        probe = json.load(fh)
    cases = {}
    for name, c in probe["cases"].items():
        cases[name] = {
            "arm": c["arm"], "lookback": c["lookback"], "defined_bars": c["defined_bars"],
            "degenerate_bars": c["degenerate_bars"],
            "weight_sum_deviation": {"max": c["max_weight_sum_dev"],
                                     "min": c["min_weight_sum_dev"]},
            "fresh_long_events": {s: c["per_symbol"][s]["events"] for s in probe["symbols"]},
            "long_bars": {s: c["per_symbol"][s]["long_bars"] for s in probe["symbols"]}}
    return {"source": os.path.relpath(PROBE_JSON, REPO),
            "measured_before_the_freeze": True,
            "kernel_identity": "k_parts/kernel.py, the same mathematics the engine inlines "
                               "(lag transformation -> sample correlation -> spectral cleaning -> "
                               "marginal-volatility rescaling -> exact long-only GMV)",
            "bars": probe["bars"], "first_ms": probe["first_ms"], "last_ms": probe["last_ms"],
            "cases": cases,
            "reading": "event supply only: no PnL, no DCA, no Sharpe was computed by the probe; "
                       "the numbers registered here are the structural feasibility measurement of "
                       "the registered entry state, not a performance measurement"}


def parameter_contract_block():
    """Generated entirely from this document's own registered domains (contract v1.8 rule)."""
    return {
        "parameter_contract_version": 1,
        "family_id": FAMILY_ID,
        "contract_ref": "v1.8 generic family parameter contract (runtime/parameter_contract.py); "
                        "generated from this document's registered parameter_domain / dca_domain",
        "research_axes_ordered": [
            {"name": "cleaning_arm", "kind": "composite",
             "members": ["cl_raw", "cl_shrink", "cl_mp"],
             "registered_values": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
             "row_fields": ["cl_raw", "cl_shrink", "cl_mp"]},
            {"name": "lookback_window", "kind": "composite",
             "members": ["lb_250", "lb_500", "lb_750", "lb_1200"],
             "registered_values": [[1 if k == j else 0 for k in range(4)] for j in range(4)],
             "row_fields": ["lb_250", "lb_500", "lb_750", "lb_1200"]},
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
        "row_fields": list(CASE_FIELDS) + list(DCA_FIELDS),
        "composite_map": {"cleaning_arm": ["cl_raw", "cl_shrink", "cl_mp"],
                          "lookback_window": ["lb_250", "lb_500", "lb_750", "lb_1200"]},
        "strategy_param_fields": list(CASE_FIELDS),
        "dca_param_fields": list(DCA_FIELDS),
        "canonical_recipe": {"sort_keys": True, "separators": [",", ":"], "ensure_ascii": False,
                             "numeric_rule": "JSON number finite, bool excluded"},
        "row_match_recipe": {"keys": ["symbol", "timeframe"] + list(CASE_FIELDS) + list(DCA_FIELDS),
                             "equality": "exact, numeric == float compare, rest bytewise"},
        "non_params": ["symbol", "timeframe", "diagnostics", "metrics"],
        "domain_cardinality": {"strategy": len(CASE_ORDER), "dca": len(dca_grid()),
                               "per_cohort": len(CASE_ORDER) * len(dca_grid())},
    }


def engine_text_pins():
    """Text-level pin of the frozen engine constants (the engine must not drift from the spec)."""
    with open(ENGINE_REPO) as fh:
        text = fh.read()
    pins = {
        "CLEAN_ARMS": 'CLEAN_ARMS = ("raw", "shrink_const", "mp_clip")',
        "LOOKBACKS": "LOOKBACKS = (250, 500, 750, 1200)",
        "THETA": "THETA = (1.0, 0.5, 2.0, 1.0, 0.05)",
        "SHRINK_DELTA": "SHRINK_DELTA = 0.5",
        "EQUAL_WEIGHT": "EQUAL_WEIGHT = 1.0 / PANEL_SIZE",
        "CASE_FIELDS": 'CASE_FIELDS = ("cl_raw", "cl_shrink", "cl_mp", "lb_250", "lb_500", '
                       '"lb_750", "lb_1200")',
        "SIGNAL_WARMUP": "SIGNAL_WARMUP_BARS = int(max(LOOKBACKS))",
        "GRIDS": '"cost_attrition_40bps")',
        "SELECTOR": 'SELECTOR_VERSION = "cohort-selector-v1"',
        "DISPOSITION": 'DISPOSITION_VERSION = "cohort-disposition-v1"',
    }
    bad = [k for k, v in pins.items() if v not in text]
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-check", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    pc = load_pc()
    problems = []
    card_body, card_full, override_text, card_db = read_card_source()
    card_sha = sha256_text(card_body)
    with open(RECORD_PATH) as fh:
        record_text = fh.read()
    record_sha = sha256_text(record_text)
    with open(CONTRACT) as fh:
        contract_text = fh.read()
    raw = measure_raw()
    probe = load_probe()
    engine_sha = sha256_file(ENGINE_REPO)
    engine_deploy_sha = sha256_file(ENGINE_DEPLOY)
    selfcheck_sha = sha256_file(SELFCHECK_REPO)
    selfcheck_deploy_sha = sha256_file(SELFCHECK_DEPLOY)
    if engine_sha != engine_deploy_sha:
        problems.append("engine sha differs between repo and host deploy: %s vs %s"
                        % (engine_sha, engine_deploy_sha))
    if selfcheck_sha != selfcheck_deploy_sha:
        problems.append("self-check sha differs between repo and host deploy")
    for bad in engine_text_pins():
        problems.append("engine text pin missing/mismatched: %s" % bad)
    if override_text is not None:
        problems.append("this card carries an OPERATOR OVERRIDE comment; the round-spec must "
                        "register it explicitly - re-author with the override block")
    with open(os.path.join(RESULTS_ROOT, FAMILY_ID, "family.json")) as fh:
        family = json.load(fh)
    if family.get("family_id") != FAMILY_ID:
        problems.append("family.json family_id mismatch")
    if family.get("kanban_task_id") != TASK_ID:
        problems.append("family.json kanban_task_id mismatch")
    if os.path.exists(os.path.join(RESULTS_ROOT, FAMILY_ID, "rounds")):
        problems.append("a rounds/ tree already exists for this family: this author script "
                        "publishes r1 exactly once")

    # ---------------- verbatim excerpts (asserted BEFORE any write) --------------------------
    excerpts = {}
    excerpts["card_source_of_truth"] = {"source": "card_body",
                                        "text": between(card_body, "SOURCE OF TRUTH",
                                                        "SCIENTIFIC HYPOTHESIS")}
    card_hyp = between(card_body, "SCIENTIFIC HYPOTHESIS", "ELIGIBLE UNIVERSE")
    excerpts["card_hypothesis_mechanism"] = {
        "source": "card_body",
        "text": between(card_hyp, "- economic mechanism（canonical record 原文節錄）：",
                        "- signal 語意與參數")}
    excerpts["card_signal_semantics"] = {
        "source": "card_body",
        "text": between(card_hyp, "- signal 語意與參數",
                        "- 核心 hypothesis 以本節錄為唯一註冊版本")}
    excerpts["card_core_hypothesis_rule"] = {
        "source": "card_body",
        "text": between(card_hyp, "- 核心 hypothesis 以本節錄為唯一註冊版本", "- 交易成本：")}
    excerpts["card_trade_cost"] = {"source": "card_body",
                                   "text": between(card_hyp, "- 交易成本：")}
    excerpts["card_eligible_universe"] = {"source": "card_body",
                                          "text": between(card_body, "ELIGIBLE UNIVERSE",
                                                          "DATA WINDOW / SPLIT")}
    excerpts["card_data_window_split"] = {"source": "card_body",
                                          "text": between(card_body, "DATA WINDOW / SPLIT",
                                                          "DCA PARAMETER DOMAIN")}
    excerpts["card_dca_domain"] = {"source": "card_body",
                                   "text": between(card_body, "DCA PARAMETER DOMAIN",
                                                   "COHORT SURVIVOR SEMANTICS")}
    excerpts["card_cohort_survivor_semantics"] = {
        "source": "card_body",
        "text": between(card_body, "COHORT SURVIVOR SEMANTICS", "ROBUSTNESS 與 FALSIFICATION")}
    excerpts["card_robustness_falsification"] = {
        "source": "card_body",
        "text": between(card_body, "ROBUSTNESS 與 FALSIFICATION", "FAILURE TAXONOMY")}
    excerpts["card_failure_taxonomy"] = {
        "source": "card_body",
        "text": between(card_body, "FAILURE TAXONOMY", "SURVIVOR BUNDLE 與 POST-SURVIVOR")}
    excerpts["card_survivor_bundle"] = {
        "source": "card_body",
        "text": between(card_body, "SURVIVOR BUNDLE 與 POST-SURVIVOR", "IMPLEMENTATION RULES")}
    excerpts["card_implementation_rules"] = {"source": "card_body",
                                             "text": between(card_body, "IMPLEMENTATION RULES",
                                                             "GIT / OPS")}
    excerpts["card_git_ops"] = {"source": "card_body",
                                "text": between(card_body, "GIT / OPS")}
    excerpts["card_lifecycle_footer"] = {
        "source": "card_full", "text": card_full[card_full.index(FOOTER_MARK):].rstrip("\n")}
    excerpts["record_mechanism"] = {"source": "canonical_record",
                                    "text": between(record_text, "### Source-reported",
                                                    "### Research interpretation")}
    rec_signal = between(record_text, "## Signal", "## Required data")
    rec_required = between(record_text, "## Required data", "## Execution assumptions")
    rec_exec = between(record_text, "## Execution assumptions", "## Evidence")
    rec_negative = between(record_text, "### Negative evidence", "## Falsification plan")
    rec_fals = between(record_text, "## Falsification plan", "## Crypto portability")
    rec_port = between(record_text, "## Crypto portability", "## Limitations")
    rec_limits = between(record_text, "## Limitations", "## Implementation status")
    rec_adopt = between(record_text, "## Adoption boundary", "## Related Wiki records")
    excerpts["record_signal_pipeline"] = {"source": "canonical_record", "text": rec_signal}
    excerpts["record_required_data"] = {"source": "canonical_record", "text": rec_required}
    excerpts["record_execution_assumptions"] = {"source": "canonical_record", "text": rec_exec}
    excerpts["record_negative_evidence"] = {"source": "canonical_record", "text": rec_negative}
    excerpts["record_falsification_plan"] = {"source": "canonical_record", "text": rec_fals}
    excerpts["record_crypto_portability"] = {"source": "canonical_record", "text": rec_port}
    excerpts["record_limitations"] = {"source": "canonical_record", "text": rec_limits}
    excerpts["record_adoption_boundary"] = {"source": "canonical_record", "text": rec_adopt}
    SOURCES = {"card_body": card_body, "card_full": card_full, "canonical_record": record_text,
               "operator_override": override_text}
    for name, ex in excerpts.items():
        src = SOURCES.get(ex["source"])
        if src is None:
            problems.append("excerpt %s declares unknown source %r" % (name, ex["source"]))
        elif ex["text"] not in src:
            problems.append("excerpt %s is not a verbatim substring of %s" % (name, ex["source"]))
    if len(numbered(rec_fals)) != 5:
        problems.append("record falsification items: %d (expected 5)" % len(numbered(rec_fals)))
    if len(bullets(rec_required)) != 5:
        problems.append("record required-data bullets: %d (expected 5)"
                        % len(bullets(rec_required)))
    if len(bullets(rec_limits)) != 4:
        problems.append("record limitations: %d (expected 4)" % len(bullets(rec_limits)))
    if len(bullets(rec_port)) != 3:
        problems.append("record crypto-portability bullets: %d (expected 3)"
                        % len(bullets(rec_port)))

    # ---------------- domain arithmetic ------------------------------------------------------
    cohorts = [{"symbol": s, "timeframe": "1d"} for s in SYMBOLS]
    grid_cases = [dict(zip(CASE_FIELDS, c)) for c in CASE_ORDER]
    dca = dca_grid()
    base_per_cohort = len(grid_cases) * len(dca)
    per_grid = base_per_cohort * len(cohorts)
    total_evals = per_grid * len(PHASE_GRIDS)
    if base_per_cohort != 12 * 48 or per_grid != 2304 or total_evals != 23040:
        problems.append("domain arithmetic mismatch: %d / %d / %d"
                        % (base_per_cohort, per_grid, total_evals))

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    case_index = {name: list(c) for name, c in zip(CASE_NAMES, CASE_ORDER)}

    round_spec = {
        "schema_version": 1,
        "document_kind": "round-spec (Strategy K v1 r1; full-backtest pre-registration for "
                         "family %s)" % FAMILY_ID,
        "family_id": FAMILY_ID,
        "family_title": "Compact-RIEnet: Neural Network-Driven Volatility Drag Mitigation and "
                        "Liquidation Delay under Aggressive Portfolio Leverage (local "
                        "eligible-universe execution)",
        "round_id": ROUND_ID,
        "kanban_task_id": TASK_ID,
        "kanban_board": BOARD,
        "created_at_utc": now,
        "authored_by": "Hermes default (Kanban card t_54d4eaf1)",
        "contract": "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md",
        "contract_sha256": sha256_text(contract_text),
        "contract_version": CONTRACT_VERSION,
        "contract_version_source": "the frozen contract in this repository at authoring time",
        "supersedes_round": None,
        "provenance": {
            "family_json": os.path.join(RESULTS_ROOT, FAMILY_ID, "family.json"),
            "family_json_sha256": sha256_file(os.path.join(RESULTS_ROOT, FAMILY_ID, "family.json")),
            "semantic_fingerprint": family.get("semantic_fingerprint"),
            "canonical_record": RECORD_PATH,
            "canonical_record_sha256": record_sha,
            "primary_source": "Christian Bongiorno, Efstratios Manolakis, and Rosario Nunzio "
                              "Mantegna, 'Neural Network-Driven Volatility Drag Mitigation under "
                              "Aggressive Leverage', arXiv:2607.23068v1 [q-fin.PM], July 26, 2026. "
                              "DOI: 10.48550/arXiv.2607.23068. ACM ICAIF DOI: "
                              "10.1145/3768292.3770370. Stable URL: "
                              "https://arxiv.org/abs/2607.23068. | Christian Bongiorno, "
                              "'Compact-RIEnet', https://github.com/bongiornoc/Compact-RIEnet, "
                              "commit 43234177d5830ba06203486c0b3abc98595e7eeb, July 2026.",
            "intake_decision": "PASS-WITH-CAVEAT (research-only knowledge; source-reported "
                               "results are not independently reproduced)",
            "card_body_sha256": card_sha,
            "card_body_source": card_db,
            "lifecycle_rule": "system-owned lifecycle footer (contract 14.4): the eligible "
                              "universe is the canonical local raw's complete available set on "
                              "which the core signal can be computed; the source market is "
                              "provenance and external-validity context only",
        },
        "hypothesis": {
            "thesis": "Compressing the levered portfolio's variance through a cleaned inverse "
                      "covariance matrix (lag-transformed returns -> denoised correlation "
                      "eigen-spectrum -> inverse covariance -> long-only global-minimum-variance "
                      "allocation) mitigates the quadratic volatility drag -1/2 l^2 sigma^2 and "
                      "keeps a levered book alive longer than an uncleaned covariance would.",
            "mechanism_verbatim": excerpts["record_mechanism"]["text"],
            "source_claim": "the record reports a first forced-liquidation leverage of 2.77 for "
                            "the compact network against 2.61-2.73 for eight benchmarks, an "
                            "incremental-efficiency ratio b/a = 1.08 and a Sharpe of 1.12 (36% "
                            "volatility, -77% max drawdown) at leverage 3.0 on a 2000-2024 US "
                            "equity panel",
            "disclosure": "the source-reported numbers are NOT reproduced here; this round tests "
                          "the mechanism on the local universe and reports its own numbers",
        },
        "signal_semantics": {
            "status": "record-faithful; every value the record leaves open is a project "
                      "pre-registered constant of this round",
            "signal_pipeline_verbatim": excerpts["record_signal_pipeline"]["text"],
            "registered_constants": SIGNAL_CONSTANTS,
            "record_math_verbatim": excerpts["card_signal_semantics"]["text"],
            "edge_cases": {
                "warmup": "a case with lookback dt_in has no defined value before bar dt_in; the "
                          "registered panel-wide floor is the largest lookback (%d) and a bar "
                          "below a case's own lookback carries target state 0"
                          % SIGNAL_WARMUP_BARS,
                "trained_modules_absent": "the record publishes no trained parameters (theta, the "
                                          "BiGRU weights gamma/omega, the per-asset MLP), so the "
                                          "registered cleaning operator is the parameter-free "
                                          "reading the record itself names (identity/raw, "
                                          "constant scalar shrinkage, Marchenko-Pastur noise-floor "
                                          "clipping) and the trained arm is carried as a "
                                          "`not_executed` reader; the marginal-volatility module is "
                                          "similarly read as the identity (sigma_NN^-1 = 1/sigma~)",
                "allocation_solver": "the long-only quadratic program is solved exactly by "
                                     "active-set enumeration over the 2^n - 1 supports (n = 4), "
                                     "which is exact for a positive-definite matrix and free of "
                                     "any solver dependency; each registered arm keeps the cleaned "
                                     "eigenvalues strictly positive by construction",
                "entry_state": "the registered target state is LONG exactly while the asset's "
                               "long-only weight is at least an equal share (w_i >= 1/n); the "
                               "literal reading (any strictly positive weight -> invested, true "
                               "on almost every bar of this universe) is disclosed and NOT "
                               "evaluated, because the pipeline's rail is event-driven",
                "no_short_leg": "the record's practical allocation is long-only (its own negative "
                                "evidence rejects the unconstrained long-short deployment); this "
                                "round registers LONG/FLAT only and adds no direction leg the "
                                "record does not register",
                "sizing": "the record's lot sizing (l w NLV / p, AUM and the leverage ladder) is "
                          "replaced by the card-mandated DCA rail, which owns the position size; "
                          "only the direction and the exit condition come from the record",
            },
        },
        "registered_universe": {
            "universe_status": "local eligible universe (system-owned lifecycle footer, contract "
                               "14.4)",
            "venue": "BINANCE USD-M perpetual",
            "instruments": list(SYMBOLS),
            "timeframes": ["1d"],
            "cohort_definition": "one cohort per (instrument x timeframe): 4 cohorts",
            "panel": "the four instruments form the registered panel the lag-transformed "
                     "correlation matrix and the long-only allocation are estimated on; a cohort "
                     "reads its own row of the panel's allocation vector",
            "source_market_note": "the record's US NYSE/NASDAQ equity panel (1990-2024, n = 1000 "
                                  "with fundamentals and FEDFUNDS) is absent from the canonical "
                                  "raw; it is provenance and external-validity context, not an "
                                  "execution prerequisite",
            "crypto_portability_verbatim": excerpts["record_crypto_portability"]["text"],
            "portability_status": "adapted / unproven (the record's own words); this round executes "
                                  "that extension and reports its own evidence",
        },
        "data": {
            "source": "/data/raw (host /Volumes/ExpansionDrive/market-data-raw), read-only",
            "start": START, "end": END,
            "historical_start": START, "historical_end": HISTORICAL_END,
            "oos_start": OOS_START, "oos_end": END,
            "split_immutability": "the split is fixed here before any computation and must not "
                                  "move afterwards; OOS is never used for any selection",
            "symbols": list(SYMBOLS),
            "timeframes": [dict(t) for t in TIMEFRAMES],
            "raw_measurement": raw,
            "funding_truth_status_windows": {
                "engine_rule": "the complete registered series is charged as a COST; the "
                               "truth_status split is disclosed and never re-labelled",
                "measured_per_symbol": {s: raw["funding"][s]["truth_status_counts"]
                                        for s in SYMBOLS}},
            "bar_labelling": {
                "raw_field": "open_time_ms",
                "status": "VERIFIED at pre-registration on the canonical raw store",
                "measured_at_pre_registration": {
                    s: {"bars_in_window": raw["symbols"][s]["bars_in_window"],
                        "first_bar_open_utc": raw["symbols"][s]["first_bar_open_utc"],
                        "last_bar_open_utc": raw["symbols"][s]["last_bar_open_utc"],
                        "off_grid_steps": raw["symbols"][s]["off_grid_steps"]} for s in SYMBOLS},
                "consequence": "every 1d grid must be exactly contiguous over the window, so "
                               "every window boundary is an exact bar index; the engine fails "
                               "closed on a non-contiguous grid (counter bar_grid_not_contiguous)"},
        },
        "parameter_domain": {
            "case_definition": "a strategy case is (cleaning arm, lookback dt_in): the spectral "
                               "cleaning operator applied to the sample correlation spectrum of "
                               "the lag-transformed window, and the length of that window in bars",
            "cleaning_arms": list(CLEAN_ARMS),
            "cleaning_arm_definition": {
                "raw": "lambda_{i,NN} = lambda_i (the record's ablation arm (a): identity, raw "
                       "sample eigenvalues)",
                "shrink_const": "lambda_{i,NN} = (1 - delta) lambda_i + delta lambda_bar, delta = "
                                "%g (the record's ablation arm (b): constant scalar shrinkage)"
                                % SHRINK_DELTA,
                "mp_clip": "lambda_{i,NN} = max(lambda_i, bulk_mean) where the Marchenko-Pastur "
                           "edge (1 + sqrt(n/dt_in))^2 only SPLITS the spectrum and the floor is "
                           "the measured bulk mean (RMT noise-floor clipping, the classical "
                           "shrinkage family the record names)",
                "not_evaluated": "the record's trained BiGRU arm itself (weights unpublished)"},
            "lookbacks_bars": list(LOOKBACKS),
            "lookback_note": "the record registers dt_in in [250, 1200] and calibrates 1200 in "
                             "its rolling backtest; the registered set is the project "
                             "pre-registered discretisation of that range",
            "case_fields": list(CASE_FIELDS),
            "case_order": [list(c) for c in CASE_ORDER],
            "case_names": list(CASE_NAMES),
            "case_index": case_index,
            "grid_cases": grid_cases,
            "provenance_class": "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN (the record names the "
                                "cleaning module and the lookback range; the case axis is their "
                                "registered product, frozen before the first run)",
            "feasibility_probe": probe,
        },
        "dca_domain": {
            "base_quote": BASE_QUOTE,
            "spacing_pct": list(DCA_AXES["spacing_pct"]),
            "size_multiplier": list(DCA_AXES["size_multiplier"]),
            "breakeven_tp_pct": list(DCA_AXES["breakeven_tp_pct"]),
            "invalidation_pct": list(DCA_AXES["invalidation_pct"]),
            "grid": dca,
            "card_verbatim": excerpts["card_dca_domain"]["text"],
            "provenance_class": {"searched_axes": "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN",
                                 "base_quote": "PROJECT_PRE_REGISTERED_CONSTANT"},
        },
        "dca_execution_semantics": {
            "entry": "tranche #1 at the NEXT bar's open after a fresh target-state change to LONG "
                     "(the record's next-day execution)",
            "scale_ins": "adverse-price ladder: level_k price = initial_entry_price x "
                         "(1 - spacing_pct x k), k = 1..10 (at most 11 routine active levels of "
                         "the 12-tranche rail)",
            "exits": "breakeven-anchored take profit at running_average_cost x (1 + tp); resting "
                     "invalidation at running_average_cost x (1 - invalidation); the record's own "
                     "exit (the registered long-only weight is no longer at least an equal share) "
                     "flattens every layer at the NEXT bar's open; the slice end flattens "
                     "reduce-only",
            "costs": "per-fill taker fee and per-settlement funding are charged at the instant of "
                     "the fill/settlement and reduce realised equity there (contract 7.2 v1.3.1); "
                     "gross PnL is produced by an independent price-PnL accumulator (v1.3.2)",
            "capital_semantics": "no new position is opened once realised equity reaches zero; "
                                 "re-entry requires a new position-change event",
        },
        "user_fixed_invariants": {
            "status": "INHERITED from the A/B rail registration, verbatim; this round must not "
                      "change them",
            "items": USER_FIXED_INVARIANTS,
            "card_verbatim": excerpts["card_dca_domain"]["text"],
        },
        "direction_disclosure": {
            "direction": "long_only (LONG / FLAT)",
            "note": "the record's practical production allocation is long-only (its own negative "
                    "evidence records that the unconstrained long-short deployment was "
                    "impractical); the card forbids adding a direction leg the record does not "
                    "register, so no short state is registered",
            "negative_evidence_verbatim": excerpts["record_negative_evidence"]["text"],
        },
        "selector_and_disposition": {
            "selector_version": SELECTOR_VERSION,
            "disposition_version": DISPOSITION_VERSION,
            "card_verbatim": excerpts["card_cohort_survivor_semantics"]["text"],
            "disposition_unit": "cohort (instrument x timeframe)",
            "thresholds": {"min_episodes_is": MIN_EPISODES_IS, "min_episodes_oos": MIN_EPISODES_OOS,
                           "neighborhood_min_same_sign_fraction": NEIGHBOURHOOD_MIN},
            "mapping": "0 survivors -> REJECT (performance_claimable=false); >=1 -> PASS with every "
                       "survivor retained; coverage/technical incompleteness -> "
                       "TECHNICAL_INCOMPLETE",
        },
        "robustness_plan": {
            "phase_grids": list(PHASE_GRIDS),
            "card_verbatim": excerpts["card_robustness_falsification"]["text"],
            "stress_semantics": {"fee_2x": "taker fee x2",
                                 "funding_2x": "funding rate x2",
                                 "entry_delay_1_bar": "one extra bar of execution delay",
                                 "slippage_2ticks": "2 ticks adverse instead of 1",
                                 "cost_attrition_40bps": "8x taker fee (40 bps per fill)",
                                 "no_funding": "funding as a cost switched off (historical slice)",
                                 "no_funding_full": "same, full slice"},
            "coverage_gate": "every registered phase grid must contain exactly cohorts x strategy "
                             "cases x 48 DCA cells",
        },
        "registered_family_level_falsification": {
            "landing": "a reader hit can only move a PASS to DEFERRED; readers never cull a cohort "
                       "and are never PASS-bearing",
            "record_battery_verbatim": excerpts["record_falsification_plan"]["text"],
            "readers": [
                {"id": "bigru_spectral_denoiser_ablation", "record_item": 2, "evaluated": True,
                 "definition": "per cohort, each registered cleaning arm's own best historical "
                               "cell (registered ordering, episodes floor applied) is carried to "
                               "the FULL window and the identity/raw arm's full-window net PnL is "
                               "compared with the best cleaning arm's",
                 "threshold": {"tolerance": FULL_WINDOW_NET_PNL_TOL},
                 "hit_when": "the identity/raw arm is at least as good as every cleaning arm in "
                             "the majority of the registered cohorts",
                 "disclosure": "the record's own threshold is a portfolio Sharpe at leverage 3.0; "
                               "this frame produces the registered rail's net PnL instead, and "
                               "the trained BiGRU arm itself is not evaluable (weights "
                               "unpublished) - the comparison is between the record's own two "
                               "ablation arms and the RMT arm, never against the trained model"},
                {"id": "market_impact_haircut_test", "record_item": 1, "evaluated": False,
                 "status": "not_executed",
                 "definition": "Almgren-Chriss square-root impact haircut calibrated at $10M / "
                               "$50M / $100M AUM, compared against the record's first "
                               "forced-liquidation leverage",
                 "reason": "the canonical store has no order-book / depth surface from which an "
                           "impact coefficient could be identified, and the registered rail runs "
                           "one fixed 10x notional rather than the record's leverage ladder; the "
                           "item is not executed rather than replaced by a weaker proxy"},
                {"id": "cross_asset_transferability_without_retraining", "record_item": 3,
                 "evaluated": False, "status": "not_executed",
                 "definition": "evaluate the pre-trained model on European (STOXX 600) and "
                               "Japanese (Nikkei 225) equity panels without fine-tuning",
                 "reason": "the local store holds one venue with four USD-M perpetual instruments "
                           "and no equity market; the pre-trained weights are absent as well"},
                {"id": "intraday_tick_level_margin_breach_audit", "record_item": 4,
                 "evaluated": False, "status": "not_executed",
                 "definition": "evaluate against actual tick-level intraday drawdown histories "
                               "rather than daily-low approximations",
                 "reason": "no tick/trade dataset exists in the canonical store (the finest kline "
                           "interval is measured at pre-registration); the daily-low "
                           "approximation cannot be replaced by the tick-level object"},
                {"id": "rejection_rule_freeze", "record_item": 5, "evaluated": True,
                 "definition": "the record's rejection rule as a disclosed local analogue: the "
                               "winner's margin-call count across every registered full-window "
                               "and robustness grid",
                 "threshold": {"margin_calls": FREEZE_MARGIN_CALLS,
                               "oos_sharpe_descriptive": FREEZE_OOS_SHARPE},
                 "hit_when": "any cohort winner records margin_calls >= 2 in any registered grid",
                 "disclosure": "the record's trailing-36-month rolling OOS Sharpe at leverage 2.0 "
                               "does not exist in this frame (the local OOS slice is 11.5 months "
                               "at the rail's fixed 10x notional); the winner's OOS Sharpe is "
                               "reported descriptively and is not part of the hit rule"},
            ],
        },
        "costs": {
            "baseline_slippage_ticks": 1,
            "robustness_slippage_ticks": 2,
            "slippage_direction": "adverse on every leg (entry, ladder add, TP, invalidation, "
                                  "slice end, margin call)",
            "taker_fee_source": "canonical instrument metadata (maker/taker), measured below",
            "measured_instruments": raw["instruments"],
            "funding_exposure_rule": "every settlement instant inside the hold is charged on the "
                                     "notional held at that instant; the exposure window is the "
                                     "closed interval [entry - 1s, exit + 1s] because the raw "
                                     "settlements carry ms-level jitter",
            "cost_attrition_multiplier": 8.0,
        },
        "gates": {"min_episodes_is": MIN_EPISODES_IS, "min_episodes_oos": MIN_EPISODES_OOS,
                  "neighborhood_min_same_sign_fraction": NEIGHBOURHOOD_MIN,
                  "require_positive_historical_net_and_sharpe": True,
                  "require_positive_oos_net_and_sharpe": True,
                  "require_positive_full_net": True,
                  "require_positive_every_robustness_grid": True,
                  "robustness_grids": ["fee_2x", "funding_2x", "entry_delay_1_bar",
                                       "slippage_2ticks"]},
        "strategy_domain": [
            {"case_index": i, "case_name": CASE_NAMES[i], "fields": dict(zip(CASE_FIELDS, c)),
             "cleaning_arm": CLEAN_ARMS[i % len(CLEAN_ARMS)],
             "lookback": int(LOOKBACK_LABELS[i // len(CLEAN_ARMS)])}
            for i, c in enumerate(CASE_ORDER)],
        "expected": {
            "cohorts": len(cohorts), "strategy_cases": len(grid_cases), "dca_configs": len(dca),
            "phase_grids": len(PHASE_GRIDS),
            "base_combinations_per_cohort": base_per_cohort,
            "case_evaluations_per_grid": per_grid,
            "expected_case_evaluations": total_evals,
            "grid_coverage_rule": "the measured cell set of every registered phase grid must equal "
                                  "this product exactly (G1); a mismatch is TECHNICAL_INCOMPLETE, "
                                  "never a scientific verdict",
        },
        "parameter_contract": parameter_contract_block(),
        "registration_excerpts": {k: v["text"] for k, v in excerpts.items() if v["text"]},
        "excerpt_source_map": {sha256_text(v["text"]): {"source": v["source"], "name": k}
                               for k, v in excerpts.items() if v["text"]},
        "non_goals": [
            "no second backtester, no Manager/Service/Factory/Registry/Orchestrator/daemon, no "
            "new audit profile",
            "no change to the record's core hypothesis, eligible-universe definition, DCA domain, "
            "phase grids or falsification battery",
            "no Paper/Live work; no forward-state or champion promotion in this round",
            "no second-round crypto/runnable suitability classification and no re-litigation of "
            "the intake decision (Wiki Brain is knowledge preservation, not an eligibility gate)",
        ],
        "artifact_authority": "the runner's disposition / verdict_recommendation is a "
                              "RECOMMENDATION; the final verdict.json is written by default under "
                              "contract section 10.7",
    }

    # ---------------- self-checks before writing ---------------------------------------------
    pc_problems = pc.validate_contract(round_spec["parameter_contract"])
    if pc_problems:
        problems.extend("parameter_contract: " + p for p in pc_problems)
    rs_problems = pc.validate_round_spec_contract(round_spec)
    if rs_problems:
        problems.extend("round_spec_contract: " + p for p in rs_problems)
    for k, v in round_spec["registration_excerpts"].items():
        dig = sha256_text(v)
        if dig not in round_spec["excerpt_source_map"]:
            problems.append("excerpt %s has no source-map entry" % k)
        else:
            src = SOURCES.get(round_spec["excerpt_source_map"][dig]["source"])
            if src is None or v not in src:
                problems.append("excerpt %s does not resolve against its declared source" % k)

    run_spec = {
        "schema_version": 1,
        "document_kind": "run-spec (Strategy K v1; instantiate to <attempt>/run-spec.json before "
                         "launch)",
        "family_id": FAMILY_ID, "round_id": ROUND_ID, "run_id": RUN_ID,
        "task_id": TASK_ID, "kanban_task_id": TASK_ID, "kanban_board": BOARD,
        "created_at_utc": now,
        "round_spec_path": "/results/%s/rounds/%s/round-spec.json" % (FAMILY_ID, ROUND_ID),
        "script": {"path": "/scripts/110_strategy_k_run.py", "sha256": engine_sha,
                   "deployed_from": "HCH725/quant-runtime-pipeline "
                                    "container/scripts/110_strategy_k_run.py (byte-identical, P10 "
                                    "recomputes it host side)"},
        "engine_selfcheck": {"script": "/scripts/tests/test_strategy_k_engine.py",
                             "sha256": selfcheck_sha, "must_run_before_launch": True},
        "container": {"name": CONTAINER, "image": IMAGE},
        "data": dict(round_spec["data"]),
        "parameter_domain": dict(round_spec["parameter_domain"]),
        "dca_domain": dict(round_spec["dca_domain"]),
        "costs": dict(round_spec["costs"]),
        "gates": dict(round_spec["gates"]),
        "expected": dict(round_spec["expected"]),
        "selector_version": SELECTOR_VERSION,
        "disposition_version": DISPOSITION_VERSION,
        "falsification": {
            "cross_asset_symbols": [],
            "registered_readers": [r["id"] for r in
                                   round_spec["registered_family_level_falsification"]["readers"]],
            "landing": "a reader hit can only move a PASS to DEFERRED",
        },
        "signal_constants": dict(SIGNAL_CONSTANTS),
        "round_spec_sha256_expected": None,
    }

    out_check = {"problems": problems, "engine_sha256": engine_sha,
                 "selfcheck_sha256": selfcheck_sha, "card_body_sha256": card_sha,
                 "record_sha256": record_sha, "expected_case_evaluations": total_evals,
                 "round_spec_path": None, "run_spec_path": None}
    if problems:
        print(json.dumps(out_check, indent=2, ensure_ascii=False))
        print("REFUSING TO WRITE: %d problem(s)" % len(problems))
        if args.out_check:
            with open(args.out_check, "w") as fh:
                json.dump(out_check, fh, indent=2, ensure_ascii=False)
        return 1
    if args.dry_run:
        out_check["dry_run"] = True
        out_check["round_spec_bytes"] = len(json.dumps(round_spec, indent=2, ensure_ascii=False))
        out_check["run_spec_bytes"] = len(json.dumps(run_spec, indent=2, ensure_ascii=False))
        print(json.dumps(out_check, indent=2, ensure_ascii=False))
        print("DRY RUN: every check passed, nothing written")
        if args.out_check:
            with open(args.out_check, "w") as fh:
                json.dump(out_check, fh, indent=2, ensure_ascii=False)
        return 0

    round_dir = os.path.join(RESULTS_ROOT, FAMILY_ID, "rounds", ROUND_ID)
    attempt_dir = os.path.join(round_dir, "attempts", RUN_ID)
    round_path = os.path.join(round_dir, "round-spec.json")
    run_path = os.path.join(attempt_dir, "run-spec.json")
    os.makedirs(attempt_dir, exist_ok=True)
    written = []
    for path, doc in ((round_path, round_spec), (run_path, run_spec)):
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            fd = os.open(path, flags, 0o644)
        except FileExistsError:
            raise SystemExit("refusing to overwrite %s (exact-once publication)" % path)
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
        written.append(path)
    out_check["round_spec_path"] = written[0]
    out_check["run_spec_path"] = written[1]
    out_check["round_spec_sha256"] = sha256_file(written[0])
    out_check["run_spec_sha256"] = sha256_file(written[1])
    if args.out_check:
        with open(args.out_check, "w") as fh:
            json.dump(out_check, fh, indent=2, ensure_ascii=False)
    print(json.dumps(out_check, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
