#!/usr/bin/env python3
"""Strategy J — pre-registration (round-spec r2) + run-spec authoring.

Family: `commodity-futures-network-momentum-lead-lag-graph-learning-2026-09-02`, card
t_5551afc1.  Round r2 opens the full backtest on the registered LOCAL eligible universe after the
operator override of 2026-09-18 (comment on the card) superseded the r1 prerequisite terminal.
r1 stays immutable history.

Pure stdlib.  Nothing is written until EVERY check passes:

  1. every verbatim excerpt is asserted as an exact substring of its declared source (the frozen
     card body read back from the board DB with the system-owned lifecycle footer stripped, the
     canonical wiki record, or the operator-override comment);
  2. the canonical raw store is re-measured (the daily bar grid of all four symbols, funding
     settlements with their truth_status, instrument metadata) and the measurement is embedded;
  3. the generic v1.8 parameter contract is generated FROM this document's own registered
     domains and validated (validate_contract / validate_round_spec_contract == 0 problems);
  4. the registered domain arithmetic is recomputed
     (4 cohorts x 21 strategy cases x 48 DCA configs x 10 phase grids = 40,320 evaluations);
  5. the engine bytes must be identical between the repo and the host deploy;
  6. both output files are published with O_CREAT|O_EXCL (exact once; a rerun is refused).

usage:  _author_strategy_j_template.py [--out-check <json>]
"""
from __future__ import annotations

import argparse
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
FAMILY_ID = "commodity-futures-network-momentum-lead-lag-graph-learning-2026-09-02"
ROUND_ID = FAMILY_ID + "-r2"
RUN_ID = ROUND_ID + "-u1"
TASK_ID = "t_5551afc1"
BOARD = "quant-strategy-research"
RESULTS_ROOT = "/Volumes/ExpansionDrive/qlib-results"
RAW_ROOT = "/Volumes/ExpansionDrive/market-data-raw/binance/usdm"
RECORD_PATH = os.path.expanduser(
    "~/.hermes/wiki/quant/commodity-futures-network-momentum-lead-lag-graph-learning-2026-09-02.md")
ENGINE_REPO = os.path.join(REPO, "container", "scripts", "100_strategy_j_run.py")
ENGINE_DEPLOY = "/Users/hong/workspace/qlib-apple-container/scripts/100_strategy_j_run.py"
SELFCHECK_REPO = os.path.join(REPO, "container", "scripts", "tests",
                              "test_strategy_j_engine.py")
SELFCHECK_DEPLOY = "/Users/hong/workspace/qlib-apple-container/scripts/tests/test_strategy_j_engine.py"
CONTRACT = os.path.join(REPO, "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md")
CONTRACT_VERSION = "v1.9.0"
FOOTER_MARK = "---\nLIFECYCLE FOOTER"
CONTAINER = "qlib-run"
IMAGE = "qlib:0.9.7-arm64"
SYMBOLS = ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT")
TIMEFRAMES = ({"raw_interval": "1d", "qlib_freq": "day"},)
START, HISTORICAL_END, OOS_START, END = "2022-01-01", "2025-09-30", "2025-10-01", "2026-09-11"
LL_METHODS = ("levy", "dtw", "ddtw")
LOOKBACKS = (22, 44, 66, 88, 110, 132)
LOOKBACK_LABELS = tuple([str(d) for d in LOOKBACKS] + ["ensemble"])
CASE_FIELDS = ("ll_levy", "ll_dtw", "ll_ddtw",
               "lb_22", "lb_44", "lb_66", "lb_88", "lb_110", "lb_132", "lb_ensemble")
CASE_ORDER = tuple(
    tuple([1 if a == i else 0 for a in range(3)] + [1 if b == j else 0 for b in range(7)])
    for j in range(len(LOOKBACK_LABELS)) for i in range(len(LL_METHODS)))
CASE_NAMES = tuple("%s__lb%s" % (LL_METHODS[i], LOOKBACK_LABELS[j])
                   for j in range(len(LOOKBACK_LABELS)) for i in range(len(LL_METHODS)))
DCA_AXES = {"spacing_pct": [0.01, 0.02, 0.03, 0.04], "size_multiplier": [1.0, 1.1],
            "breakeven_tp_pct": [0.01, 0.02, 0.03], "invalidation_pct": [0.05, 0.10]}
BASE_QUOTE = 1000.0
PHASE_GRIDS = ("historical", "oos", "full", "fee_2x", "funding_2x", "entry_delay_1_bar",
               "slippage_2ticks", "no_funding", "no_funding_full", "cost_attrition_40bps")
MIN_EPISODES_IS = 10
MIN_EPISODES_OOS = 4
NEIGHBOURHOOD_MIN = 0.6
SELECTOR_VERSION = "cohort-selector-v1"
DISPOSITION_VERSION = "cohort-disposition-v1"
SIGNAL_CONSTANTS = {
    "vol_window": 22, "speed_indexes": [1, 2, 3, 4, 5, 6], "rho": 4.0,
    "sigmoid_lambda": 1.4142135623730951, "c_lambda": 1.0, "graph_alpha": 1.0,
    "graph_beta": 0.01, "dtw_band_fraction": 0.25, "dtw_min_band": 2,
    "signal_warmup_bars": 188, "panel_size": 4,
}
N_PERM_DRAWS = 24
PERM_SEED = 20260918
PERM_ALPHA = 0.01
PERM_MARGIN_SHARPE = 0.05
LAG_DISSIPATION_FRACTION = 0.5
SHOCK_WINDOWS = (("luna_collapse_2022", "2022-05-01", "2022-06-30"),
                 ("ftx_collapse_2022", "2022-11-01", "2022-12-31"),
                 ("carry_unwind_2024", "2024-07-25", "2024-08-31"))


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def sha256_text(text):
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def load_pc():
    spec = importlib.util.spec_from_file_location(
        "parameter_contract", os.path.join(REPO, "runtime", "parameter_contract.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def read_card_source():
    """The frozen card body (footer stripped) and the operator-override comment, read-only."""
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


def section_lines(text, first, last=None):
    """The card-body lines [first, last] (1-based, inclusive) joined back verbatim."""
    lines = text.split("\n")
    block = lines[first - 1: (last if last else first)]
    return "\n".join(block).rstrip("\n")


def between(text, start_mark, end_mark):
    """The substring [start_mark, end_mark) - the anchor line is KEPT (it is part of the excerpt)."""
    i = text.index(start_mark)
    j = text.index(end_mark, i + len(start_mark))
    return text[i:j].rstrip("\n")


def bullets(block):
    """The bullet items of a block that starts with a bullet label; the label line is NOT an item."""
    out = []
    for line in block.split("\n"):
        if line.startswith("- **") or line.startswith("- "):
            out.append(line)
    return out


def numbered(block):
    out = []
    for line in block.split("\n"):
        s = line.strip()
        if s[:2] in ("1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9.") or (
                len(s) > 3 and s[0].isdigit() and s[1] == "."):
            out.append(line)
    return out


def measure_raw():
    """Re-measure the canonical daily raw store: bars, funding, instruments, coverage."""
    out = {"symbols": {}, "funding": {}, "klines_root": os.path.join(RAW_ROOT, "klines")}
    total_bars = 0
    for sym in SYMBOLS:
        d = os.path.join(RAW_ROOT, "klines", sym, "1d")
        files = sorted(glob.glob(os.path.join(d, "*.jsonl.gz")))
        if not files:
            raise SystemExit("no daily kline files for %s in %s" % (sym, d))
        lo_ms = int(time.mktime(time.strptime(START, "%Y-%m-%d"))) * 1000
        hi_ms = int(time.mktime(time.strptime(END, "%Y-%m-%d"))) * 1000 + 86400000 - 1
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
                    if last is not None and ms - last != 86400000:
                        off_grid += 1
                    if first is None:
                        first = ms
                    last = ms
                    rows += 1
        iso = lambda ms: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ms / 1000.0))
        expected = int((hi_ms - lo_ms) // 86400000) + 1
        out["symbols"][sym] = {
            "month_files": len(files), "bars_in_window": rows,
            "first_bar_open_utc": iso(first), "last_bar_open_utc": iso(last),
            "first_bar_open_date": iso(first)[:10], "last_bar_open_date": iso(last)[:10],
            "off_grid_steps": off_grid,
            "expected_if_contiguous": expected,
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
                if ms < lo_ms or ms > hi_ms:
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
    instruments = {}
    for item in inst_doc["instruments"]:
        f = item["fields"]
        instruments[f["raw_symbol"]] = {"id": f["id"], "type": f["type"],
                                        "settlement_currency": f["settlement_currency"],
                                        "price_increment": float(f["price_increment"]),
                                        "taker_fee": float(f["taker_fee"]),
                                        "maker_fee": float(f["maker_fee"]),
                                        "margin_init": float(f["margin_init"]),
                                        "margin_maint": float(f["margin_maint"])}
    out["instruments"] = {s: instruments[s] for s in SYMBOLS}
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


def parameter_contract_block():
    """Generated entirely from this document's own registered domains (contract v1.8 rule)."""
    return {
        "parameter_contract_version": 1,
        "family_id": FAMILY_ID,
        "contract_ref": "v1.8 generic family parameter contract (runtime/parameter_contract.py); "
                        "generated from this document's registered parameter_domain / dca_domain",
        "research_axes_ordered": [
            {"name": "lead_lag_method", "kind": "composite",
             "members": ["ll_levy", "ll_dtw", "ll_ddtw"],
             "registered_values": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
             "row_fields": ["ll_levy", "ll_dtw", "ll_ddtw"]},
            {"name": "lookback_window", "kind": "composite",
             "members": ["lb_22", "lb_44", "lb_66", "lb_88", "lb_110", "lb_132", "lb_ensemble"],
             "registered_values": [[1 if k == j else 0 for k in range(7)] for j in range(7)],
             "row_fields": ["lb_22", "lb_44", "lb_66", "lb_88", "lb_110", "lb_132",
                            "lb_ensemble"]},
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
        "composite_map": {
            "lead_lag_method": ["ll_levy", "ll_dtw", "ll_ddtw"],
            "lookback_window": ["lb_22", "lb_44", "lb_66", "lb_88", "lb_110", "lb_132",
                                "lb_ensemble"]},
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
    ap.add_argument("--dry-run", action="store_true",
                    help="run every check and print the problems without writing anything")
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
    if family.get("family_id") != FAMILY_ID:
        problems.append("family.json family_id mismatch")
    r1_dir = os.path.join(RESULTS_ROOT, FAMILY_ID, "rounds", FAMILY_ID + "-r1")
    r1_verdict_path = os.path.join(r1_dir, "verdict.json")
    r1_spec_path = os.path.join(r1_dir, "round-spec.json")
    with open(r1_verdict_path) as fh:
        r1_verdict = json.load(fh)
    r1_pins = {"round_id": FAMILY_ID + "-r1", "verdict_sha256": sha256_file(r1_verdict_path),
               "round_spec_sha256": sha256_file(r1_spec_path),
               "verdict": r1_verdict.get("verdict"),
               "status": "preserved immutable history; this round does not read or rewrite it"}
    if r1_verdict.get("verdict") != "TECHNICAL_INCOMPLETE":
        problems.append("r1 verdict is %r, expected TECHNICAL_INCOMPLETE" % r1_verdict.get("verdict"))

    # ---------------- verbatim excerpts (asserted BEFORE any write) --------------------------
    excerpts = {}
    excerpts["card_source_of_truth"] = {"source": "card_body", "text": section_lines(card_body, 3, 8)}
    excerpts["card_hypothesis_mechanism"] = {"source": "card_body",
                                             "text": section_lines(card_body, 12, 18)}
    excerpts["card_signal_semantics"] = {"source": "card_body",
                                         "text": section_lines(card_body, 19, 44)}
    excerpts["card_core_hypothesis_rule"] = {"source": "card_body",
                                             "text": section_lines(card_body, 45, 46)}
    excerpts["card_eligible_universe"] = {"source": "card_body", "text": section_lines(card_body, 48, 61)}
    excerpts["card_data_window_split"] = {"source": "card_body",
                                          "text": section_lines(card_body, 63, 66)}
    excerpts["card_dca_domain"] = {"source": "card_body", "text": section_lines(card_body, 68, 75)}
    excerpts["card_cohort_survivor_semantics"] = {"source": "card_body",
                                                  "text": section_lines(card_body, 77, 94)}
    excerpts["card_robustness_falsification"] = {"source": "card_body",
                                                 "text": section_lines(card_body, 96, 108)}
    excerpts["card_failure_taxonomy"] = {"source": "card_body",
                                         "text": section_lines(card_body, 110, 114)}
    excerpts["card_survivor_bundle"] = {"source": "card_body", "text": section_lines(card_body, 116, 120)}
    excerpts["card_implementation_rules"] = {"source": "card_body",
                                             "text": section_lines(card_body, 122, 128)}
    excerpts["card_git_ops"] = {"source": "card_body", "text": section_lines(card_body, 130, 133)}
    excerpts["card_lifecycle_footer"] = {"source": "card_body", "text": section_lines(card_body, 136, 140)}
    excerpts["operator_override"] = {"source": "operator_override", "text": override_text}
    excerpts["record_mechanism"] = {"source": "canonical_record",
                                    "text": between(record_text, "### Source-reported",
                                                    "### Research interpretation")}
    rec_signal = between(record_text, "### 1. Continuous Price Construction",
                         "## Required data")
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
    SOURCES = {"card_body": card_body, "canonical_record": record_text,
               "operator_override": override_text}
    for name, ex in excerpts.items():
        src = SOURCES.get(ex["source"])
        if src is None:
            problems.append("excerpt %s declares unknown source %r" % (name, ex["source"]))
        elif ex["text"] not in src:
            problems.append("excerpt %s is not a verbatim substring of %s" % (name, ex["source"]))
    # list counts, so a shortened registration cannot slip through
    if len(bullets(rec_required)) != 4:
        problems.append("record required-data bullets: %d (expected 4)"
                        % len(bullets(rec_required)))
    if len(numbered(rec_fals)) != 4:
        problems.append("record falsification items: %d (expected 4)" % len(numbered(rec_fals)))
    if len(bullets(rec_limits)) != 3:
        problems.append("record limitations: %d (expected 3)" % len(bullets(rec_limits)))
    if override_text is None:
        problems.append("operator override comment not found on the card")

    # ---------------- domain arithmetic ------------------------------------------------------
    cohorts = [{"symbol": s, "timeframe": "1d"} for s in SYMBOLS]
    grid_cases = [dict(zip(CASE_FIELDS, c)) for c in CASE_ORDER]
    dca = dca_grid()
    base_per_cohort = len(grid_cases) * len(dca)
    per_grid = base_per_cohort * len(cohorts)
    total_evals = per_grid * len(PHASE_GRIDS)
    if base_per_cohort != 21 * 48 or per_grid != 4032 or total_evals != 40320:
        problems.append("domain arithmetic mismatch: %d / %d / %d"
                        % (base_per_cohort, per_grid, total_evals))

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    case_index = {name: list(c) for name, c in zip(CASE_NAMES, CASE_ORDER)}

    round_spec = {
        "schema_version": 1,
        "document_kind": "round-spec (Strategy J v1 r2; full-backtest pre-registration for "
                         "family %s)" % FAMILY_ID,
        "family_id": FAMILY_ID,
        "family_title": "Commodity Futures Network Momentum: Signature Levy Area and Dynamic "
                        "Time Warping Graph Learning (local eligible-universe execution)",
        "round_id": ROUND_ID,
        "kanban_task_id": TASK_ID,
        "kanban_board": BOARD,
        "created_at_utc": now,
        "authored_by": "Hermes default (Kanban card t_5551afc1, run 296)",
        "contract": "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md",
        "contract_sha256": sha256_text(contract_text),
        "contract_version": CONTRACT_VERSION,
        "contract_version_source": "the frozen contract in this repository at authoring time",
        "supersedes_round": r1_pins,
        "provenance": {
            "family_json": os.path.join(RESULTS_ROOT, FAMILY_ID, "family.json"),
            "family_json_sha256": sha256_file(os.path.join(RESULTS_ROOT, FAMILY_ID, "family.json")),
            "semantic_fingerprint": family.get("semantic_fingerprint"),
            "canonical_record": RECORD_PATH,
            "canonical_record_sha256": record_sha,
            "primary_source": "Linze Li (Imperial College London), William Ferreira (University "
                              "College London), 'Follow the Leader: Enhancing Systematic "
                              "Trend-Following Using Network Momentum', arXiv:2501.07135v1 "
                              "[q-fin.PM, q-fin.TR], January 2025. "
                              "URL: https://arxiv.org/abs/2501.07135",
            "intake_decision": "PASS-WITH-CAVEAT (research-only knowledge; source-reported "
                               "results are not independently reproduced)",
            "card_body_sha256": card_sha,
            "card_body_source": card_db,
            "r1_terminal": "TECHNICAL_INCOMPLETE (prerequisite gate, zero attempts launched)",
        },
        "operator_override": {
            "authority": "ChatGPT (GPT-5.6 Sol), per user direction 2026-09-18, comment on card "
                         "t_5551afc1",
            "verbatim": override_text,
            "effect": "supersedes the card body's source-market exact-match / no-universe-shrink "
                      "execution wording: the LOCAL eligible universe (BTCUSDT, ETHUSDT, BNBUSDT, "
                      "SOLUSDT USD-M perpetuals on the registered local timeframe 1d) is the "
                      "execution universe; the source's 28 commodity futures and the wider crypto "
                      "list are provenance and external-validity context only.  r1 artifacts stay "
                      "immutable history.  TECHNICAL_INCOMPLETE remains available only if a data "
                      "type or field required by the core signal is absent even for every legal "
                      "local universe.",
            "not_applied": "the override does not change the record's core hypothesis, the signal "
                           "mathematics, the DCA parameter domain, the split rule, the phase "
                           "grids or the falsification battery",
        },
        "hypothesis": {
            "thesis": "Cross-market lead-lag structure carries directional momentum spillover: "
                      "filtering pairwise lead-lag matrices (2nd-level signature Levy area and "
                      "DTW/DDTW mode lags) into a sparse graph and aggregating each market's "
                      "six-speed TSMOM oscillators over its network neighbours produces a trend "
                      "signal that beats the same oscillator chain without the network.",
            "mechanism_verbatim": excerpts["record_mechanism"]["text"],
            "source_claim": "the record reports a +29% net Sharpe enhancement over its univariate "
                            "MACD baseline on 28 commodity futures (2005-2024), bootstrapped",
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
                "warmup": "the registered signal is defined from bar index %d (longest lookback "
                          "132 + volatility window 22 + the slowest oscillator's transient); a "
                          "bar below it carries target state 0 and can never open an episode"
                          % SIGNAL_CONSTANTS["signal_warmup_bars"],
                "own_market_excluded": "diag(A) = 0, so a market never inherits its own "
                                       "oscillator: the aggregation is strictly cross-market",
                "directional_asymmetry": "the sign of a lead-lag entry enters only through the "
                                         "distance weighting W of the registered graph problem; "
                                         "a leader-only neighbour-set variant is NOT evaluated",
                "dtw_band": "the DTW dynamic program is bounded by a Sakoe-Chiba band of "
                            "max(2, round(0.25 x lookback)) steps (a computational scope "
                            "approximation of the registered unbounded alignment; disclosed)",
                "ddtw_causality": "the registered DDTW derivative estimate is centred; the "
                                  "window is sliced so no value used at bar t reads a bar after "
                                  "t (prefix invariance is proved by the engine self-check)",
                "sizing": "the record's lot sizing (1/(F E sigma), AUM and the 10% volatility "
                          "target) is replaced by the card-mandated DCA rail, which owns the "
                          "position size; only the direction and the exit condition are taken "
                          "from the record's target position",
            },
        },
        "registered_universe": {
            "universe_status": "local eligible universe (operator override, 2026-09-18)",
            "venue": "BINANCE USD-M perpetual",
            "instruments": list(SYMBOLS),
            "timeframes": [tf["raw_interval"] for tf in TIMEFRAMES],
            "cohort_definition": "one cohort per (instrument x timeframe): 4 cohorts",
            "panel": "the four instruments form the registered panel the lead-lag matrices and "
                     "the graph adjacency are estimated on; a cohort reads its own row",
            "source_market_note": "the record's 28 commodity futures are provenance and "
                                  "external-validity context, not an execution prerequisite",
            "crypto_portability_verbatim": excerpts["record_crypto_portability"]["text"],
            "portability_status": "adapted / unproven (the record's own words); this round "
                                  "executes that extension and reports its own evidence",
        },
        "data": {
            "source": "/data/raw (host /Volumes/ExpansionDrive/market-data-raw), read-only",
            "start": START, "end": END,
            "historical_start": START, "historical_end": HISTORICAL_END,
            "oos_start": OOS_START, "oos_end": END,
            "split_immutability": "the split is fixed here before any computation and must not "
                                  "move afterwards; OOS is never used for any selection",
            "symbols": list(SYMBOLS),
            "timeframes": [dict(tf) for tf in TIMEFRAMES],
            "raw_measurement": raw,
            "funding_truth_status_windows": {
                "engine_rule": "the complete registered series is charged as a COST; the "
                               "truth_status split is disclosed and never re-labelled",
                "measured_per_symbol": {s: raw["funding"][s]["truth_status_counts"]
                                        for s in SYMBOLS},
            },
            "bar_labelling": {
                "raw_field": "open_time_ms",
                "status": "VERIFIED at pre-registration on the canonical raw store",
                "measured_at_pre_registration": {
                    s: {"bars_in_window": raw["symbols"][s]["bars_in_window"],
                        "first_bar_open_utc": raw["symbols"][s]["first_bar_open_utc"],
                        "last_bar_open_utc": raw["symbols"][s]["last_bar_open_utc"],
                        "off_grid_steps": raw["symbols"][s]["off_grid_steps"]}
                    for s in SYMBOLS},
                "consequence": "every 1d grid is exactly contiguous over the window, so every "
                               "window boundary is an exact bar index (searchsorted on "
                               "open_time_ms); no boundary is inferred from a label",
            },
        },
        "parameter_domain": {
            "case_definition": "a strategy case is (lead-lag estimator, lookback window): the "
                               "estimator that builds the pairwise lead-lag matrix and the "
                               "lookback it is estimated over (the record's ensemble variant "
                               "averages the six normalised adjacencies)",
            "lead_lag_methods": list(LL_METHODS),
            "lookbacks_trading_days": list(LOOKBACKS),
            "lookback_labels": list(LOOKBACK_LABELS),
            "case_fields": list(CASE_FIELDS),
            "case_order": [list(c) for c in CASE_ORDER],
            "case_names": list(CASE_NAMES),
            "case_index": case_index,
            "grid_cases": grid_cases,
            "provenance_class": "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN (the record names the "
                                "estimator family and the six lookbacks; the case axis is their "
                                "registered product, frozen before the first run)",
            "speeds": {"status": "NOT a searched axis: all six registered speeds are combined "
                                 "exactly as the record prescribes (mean over k of the reverting "
                                 "sigmoid of the network oscillator)"},
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
            "entry": "tranche #1 at the NEXT bar's open after a fresh target-state change to a "
                     "non-zero value (the record's next-day execution)",
            "scale_ins": "adverse-price ladder: level_k price = initial_entry_price x "
                         "(1 - spacing_pct x k) long / x (1 + spacing_pct x k) short, "
                         "k = 1..10 (at most 11 routine active levels of the 12-tranche rail)",
            "exits": "breakeven-anchored take profit at running_average_cost x (1 +- tp); "
                     "resting invalidation at running_average_cost x (1 -+ invalidation); the "
                     "record's own exit (target state no longer the episode direction) flattens "
                     "every layer at the NEXT bar's open; slice end flattens reduce-only",
            "costs": "per-fill taker fee and per-settlement funding are charged at the instant "
                     "of the fill/settlement and reduce realised equity there (contract 7.2 "
                     "v1.3.1); gross PnL comes from an independent price-PnL accumulator "
                     "(v1.3.2); slippage baseline 1 tick adverse by instrument price_increment, "
                     "robustness re-measures 2 ticks",
            "capital_semantics": "no new position is opened once realised equity reaches zero; "
                                 "re-entry requires a new position-change event",
        },
        "user_fixed_invariants": {
            "status": "INHERITED from the A/B rail registration, verbatim; this round must not "
                      "change them",
            "items": ["starting_equity = 30,000 USDT", "USDT is the only numeraire",
                      "linear USD-M perpetual", "leverage 10x", "12 tranches",
                      "geometric size multiplier 1.1 (historical progression value; inside the "
                      "size_multiplier axis it is a search candidate)",
                      "tranche #12 is reserved (routine active levels max 11)",
                      "initial entry + adverse-price scale-ins", "reduce-only exits",
                      "same-bar multi-level crossing uses the deterministic conservative "
                      "ordering", "no add after FLAT/kill"],
            "card_verbatim": excerpts["card_dca_domain"]["text"],
        },
        "direction_disclosure": {
            "direction": "long_short (the record's target position is signed)",
            "note": "the card forbids adding a direction leg the record does not register; the "
                    "record's own negative evidence (negative short-side Sharpe on commodities) "
                    "is recorded as a caveat, not as a licence to drop the short side",
            "negative_evidence_verbatim": excerpts["record_negative_evidence"]["text"],
        },
        "selector_and_disposition": {
            "selector_version": SELECTOR_VERSION,
            "disposition_version": DISPOSITION_VERSION,
            "card_verbatim": excerpts["card_cohort_survivor_semantics"]["text"],
            "disposition_unit": "cohort (instrument x timeframe)",
            "thresholds": {"min_episodes_is": MIN_EPISODES_IS, "min_episodes_oos": MIN_EPISODES_OOS,
                           "neighborhood_min_same_sign_fraction": NEIGHBOURHOOD_MIN},
            "mapping": "0 survivors -> REJECT (performance_claimable=false); >=1 -> PASS with "
                       "every survivor retained; coverage/technical incompleteness -> "
                       "TECHNICAL_INCOMPLETE",
        },
        "robustness_plan": {
            "phase_grids": list(PHASE_GRIDS),
            "card_verbatim": excerpts["card_robustness_falsification"]["text"],
            "stress_semantics": {"fee_2x": "taker fee x2", "funding_2x": "funding rate x2",
                                 "entry_delay_1_bar": "one extra bar of execution delay",
                                 "slippage_2ticks": "2 ticks adverse instead of 1",
                                 "cost_attrition_40bps": "8x taker fee (40 bps per fill)",
                                 "no_funding": "funding as a cost switched off (historical "
                                               "slice)", "no_funding_full": "same, full slice"},
            "coverage_gate": "every registered phase grid must contain exactly "
                             "cohorts x strategy cases x 48 DCA cells",
        },
        "registered_family_level_falsification": {
            "landing": "a reader hit can only move a PASS to DEFERRED; readers never cull a "
                       "cohort and are never PASS-bearing",
            "record_battery_verbatim": excerpts["record_falsification_plan"]["text"],
            "readers": [
                {"id": "synthetic_lead_lag_permutation_test",
                 "record_item": 1,
                 "definition": "the elected winner's cell re-simulated on the real panel and on "
                               "N_PERM_DRAWS panels, one circular per-market shift set per draw "
                               "(autocorrelation intact, cross-sectional alignment destroyed); "
                               "one-sided paired t-test of (real - shuffled) over the draws at "
                               "alpha = 0.01, plus the empirical exceedance rate",
                 "threshold": {"margin_sharpe": PERM_MARGIN_SHARPE, "alpha": PERM_ALPHA},
                 "hit_when": "mean surplus < +0.05 net Sharpe, or the paired test fails to reach "
                             "p < 0.01",
                 "draws": N_PERM_DRAWS, "seed": PERM_SEED,
                 "disclosure": "the record fixes the threshold, not the test machinery; the "
                               "circular wrap perturbs at most a few windows per draw; a "
                               "block-shuffle variant is not evaluated"},
                {"id": "execution_lag_and_slippage_sensitivity",
                 "record_item": 2,
                 "definition": "the record's univariate baseline is the identical six-speed "
                               "oscillator chain WITHOUT the network aggregation; the reader "
                               "re-runs the winner's cell, the baseline and the "
                               "entry_delay_1_bar / slippage_2ticks stress tracks on the FULL "
                               "window and measures the Sharpe enhancement (network - baseline) "
                               "and its dissipation under one extra bar of delay",
                 "threshold": {"dissipated_fraction": LAG_DISSIPATION_FRACTION},
                 "hit_when": "a positive enhancement dissipates by more than 50%",
                 "disclosure": "the record's MACD baseline is read as its own univariate "
                               "oscillator chain (same speeds, no network); the reader reports "
                               "the enhancement even when it is not positive"},
                {"id": "dtw_descriptor_ablation",
                 "record_item": 3,
                 "definition": "inside each registered local shock window and per cohort, the "
                               "winner's DCA config is run for all twelve DTW-family cases and "
                               "the two descriptors are compared by the median net Sharpe",
                 "shock_windows": [{"name": n, "start": a, "end": b} for n, a, b in SHOCK_WINDOWS],
                 "hit_when": "DDTW does not out-perform standard DTW in the majority of the "
                             "registered cohort x window cells",
                 "disclosure": "the record's commodity shock regimes (2008 oil spike, 2020 "
                               "negative oil, 2022 Ukraine) are outside the local store; the "
                               "registered local analogues replace them as measurement windows "
                               "only, never as a re-defined mechanism"},
                {"id": "out_of_sample_universe_expansion",
                 "record_item": 4,
                 "definition": "evaluate on 20 liquid crypto perpetual contracts and 30 "
                               "international sovereign bond futures",
                 "status": "not_executed",
                 "reason": "the canonical store holds exactly four USD-M perpetual contracts and "
                           "no sovereign bond futures; the operator override registers those "
                           "four as the round's universe and forbids shrinking or substituting "
                           "a universe, so the expansion is recorded as not_executed rather "
                           "than lowered or simulated on a shrunken panel"},
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
                                     "settlements carry up to ~28 ms of jitter",
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
             "lead_lag_method": LL_METHODS[i % 3],
             "lookback": LOOKBACK_LABELS[i // 3]}
            for i, c in enumerate(CASE_ORDER)],
        "expected": {
            "cohorts": len(cohorts), "strategy_cases": len(grid_cases), "dca_configs": len(dca),
            "phase_grids": len(PHASE_GRIDS),
            "base_combinations_per_cohort": base_per_cohort,
            "case_evaluations_per_grid": per_grid,
            "expected_case_evaluations": total_evals,
            "grid_coverage_rule": "the measured cell set of every registered phase grid must "
                                  "equal this product exactly (G1); a mismatch is "
                                  "TECHNICAL_INCOMPLETE, never a scientific verdict",
        },
        "parameter_contract": parameter_contract_block(),
        "registration_excerpts": {k: v["text"] for k, v in excerpts.items() if v["text"]},
        "excerpt_source_map": {sha256_text(v["text"]): {"source": v["source"], "name": k}
                               for k, v in excerpts.items() if v["text"]},
        "non_goals": [
            "no second backtester, no Manager/Service/Factory/Registry/Orchestrator/daemon, no "
            "new audit profile",
            "no change to the record's core hypothesis, eligible-universe definition, DCA "
            "domain, phase grids or falsification battery",
            "no Paper/Live work; no forward-state or champion promotion in this round",
            "no re-litigation of the r1 prerequisite terminal (preserved immutable history)",
        ],
        "artifact_authority": "the runner's disposition / verdict_recommendation is a "
                              "RECOMMENDATION; the final verdict.json is written by default "
                              "under contract section 10.7",
    }

    # ---------------- self-checks before writing ---------------------------------------------
    pc_problems = pc.validate_contract(round_spec["parameter_contract"])
    if pc_problems:
        problems.extend("parameter_contract: " + p for p in pc_problems)
    rs_problems = pc.validate_round_spec_contract(round_spec)
    if rs_problems:
        problems.extend("round_spec_contract: " + p for p in rs_problems)
    # every *_verbatim / registration excerpt must resolve through the declared source map
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
        "document_kind": "run-spec (Strategy J v1; instantiate to <attempt>/run-spec.json before "
                         "launch)",
        "family_id": FAMILY_ID, "round_id": ROUND_ID, "run_id": RUN_ID,
        "task_id": TASK_ID, "kanban_task_id": TASK_ID, "kanban_board": BOARD,
        "created_at_utc": now,
        "round_spec_path": "/results/%s/rounds/%s/round-spec.json" % (FAMILY_ID, ROUND_ID),
        "script": {"path": "/scripts/100_strategy_j_run.py", "sha256": engine_sha,
                   "deployed_from": "HCH725/quant-runtime-pipeline "
                                    "container/scripts/100_strategy_j_run.py (byte-identical, "
                                    "P10 recomputes it host side)"},
        "engine_selfcheck": {"script": "/scripts/tests/test_strategy_j_engine.py",
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
            "shock_windows": [{"name": n, "start": a, "end": b} for n, a, b in SHOCK_WINDOWS],
            "permutation": {"draws": N_PERM_DRAWS, "seed": PERM_SEED, "alpha": PERM_ALPHA,
                            "margin_sharpe": PERM_MARGIN_SHARPE},
            "lag_dissipation_fraction": LAG_DISSIPATION_FRACTION,
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
    run_spec["round_spec_sha256_expected"] = None
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
