#!/usr/bin/env python3
"""Prerequisite-gate read-back checker for the family

    ethereum-exchange-net-inflow-bearish-drift-1h-6h-2026-09-01

Card t_729dd9ad terminalised this family as TECHNICAL_INCOMPLETE because the
record's required data is not in the canonical raw:

  * the record's decision variable is *ETH exchange net inflow*, formed from
    on-chain ETH transfers (`exchange inflow minus exchange outflow`) whose
    endpoints are classified by *point-in-time labelled centralised-exchange
    addresses*, at hourly or finer resolution. The canonical raw holds one venue's
    USD-M perpetual klines/funding/instruments for four contracts: it carries no
    blockchain transfer surface, no exchange-address label surface, and therefore
    no inflow/outflow series. The missing prerequisite is the signal's own
    independent variable, not an auxiliary filter;
  * the registered price reference is "ETH spot or index price aligned to the same
    clock"; locally the only ETH series is a USDT-quoted USD-M perpetual at one
    derivatives venue (a different market, a different clock convention and a
    different settlement asset), and the store documents that its kline rows carry
    the OHLCV shape only - it holds no trade-level or taker-side data either;
  * the registered 1h/2h/3h/4h/6h forward returns are functions of that same
    clock-aligned price series *conditioned on flow events*, so they are not
    constructible without the flow input;
  * the label-vintage requirement (address labels as they were observable at `t`,
    with explicit vintage controls) has no data substrate at all: a label vintage
    column cannot be synthesised from price bars;
  * the source's reported return-forecasting sample is 2017-12-16 -> 2023-01-20
    (1861 days). The raw's own daily history starts 2022-01-01, so even if a flow
    series existed locally, only 384 of the source's 1861 sample days (20.6%) are
    inside the raw window, and the record's falsification items 1-2 (reconstruct
    the flows, reproduce the 1/2/3/4/6h regressions over the source sample) are
    not coverable from this machine's data.

Running the local four-contract perpetual panel instead would change the
record's decision variable, its market source, its sample and its clock. The card
forbids that ("不得以近似資料、替代市場或改寫 hypothesis 硬跑" / "不得縮減 universe
以硬造可執行性"), so nothing was launched.

This script exists so an independent reader can re-derive that determination
from the live filesystem instead of trusting prose:

  * C1-C7 re-measure the canonical raw: the market-directory set, the instrument
    surface, the stored row shapes and the store's own documented dataset
    families, the decisive absence of any ETH on-chain transfer / exchange-flow /
    address-label dataset (entry-name probe over the whole tree at full depth),
    the absence of the registered ETH spot-or-index price source, the
    non-constructibility of the net-inflow series and of the flow-conditioned
    forward returns, and the raw's own daily window versus the record's sample;
  * C8-C12 re-read the round's immutable artifacts and assert they state exactly
    the contract-mandated terminal values (verdict, layer, class, yield decision,
    zero attempts, null run_id), that nothing was ever submitted (no attempt
    directory, no terminal sentinel), that the registered requirement was NOT
    shrunk to the locally available instruments, that the DCA registration still
    carries the contract 7.2 v1.3.1 provenance classes plus the complete 48-cell
    product, and that every raw-side value the artifact claims still equals a live
    re-measurement (C12);
  * C13 resolves every `*_verbatim` leaf of the persisted round-spec against its
    declared source (card / record / contract / footer) and re-checks the excerpt
    digest map - independent of the authoring run;
  * C14 re-asserts that the coverage/survivor surface is empty by construction and
    that the registered phase grid is intact.

Read-only: it never writes inside the results tree or the raw tree. `--self-test`
builds tampered copies in fresh temp directories and asserts the checker refuses
them (non-vacuousness control). `--raw-fixture-control` builds a temp raw tree
carrying what this record would need (an hourly ETH exchange net-inflow series
with labelled exchange addresses, a spot market with an ETH/USD series, a second
venue, and an older daily bar) and asserts the raw-side checks flip to FAIL.
`--host-scan` re-runs the house-wide search for such a dataset. Every temp tree
is removed afterwards.

Usage:
    python3 runtime/ethereum_exchange_net_inflow_prerequisite_check.py [--json]
    python3 runtime/ethereum_exchange_net_inflow_prerequisite_check.py --measure-only
    python3 runtime/ethereum_exchange_net_inflow_prerequisite_check.py --verify-verbatim
    python3 runtime/ethereum_exchange_net_inflow_prerequisite_check.py --self-test
    python3 runtime/ethereum_exchange_net_inflow_prerequisite_check.py --raw-fixture-control
    python3 runtime/ethereum_exchange_net_inflow_prerequisite_check.py --host-scan

Exit codes: 0 = all checks PASS, 1 = at least one FAIL, 2 = usage error.
"""
import argparse
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone

# Provenance classes are reused from the existing registered validator rather than
# re-implemented here (contract 7.2 v1.3.1).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import strategy_a_v2_counts as _sav2
except ImportError:  # pragma: no cover - only if the sibling module is missing
    _sav2 = None
try:
    from production_handoff import LIFECYCLE_FOOTER as _LIFECYCLE_FOOTER
except ImportError:  # pragma: no cover - only if the sibling module is missing
    _LIFECYCLE_FOOTER = None

FAMILY = "ethereum-exchange-net-inflow-bearish-drift-1h-6h-2026-09-01"
ROUND = FAMILY + "-r1"
TASK = "t_729dd9ad"
FAMILY_TITLE = "Ethereum Exchange Net-Inflow Bearish Drift at 1-6 Hour Horizons"
DEFAULT_RESULTS = "/Volumes/ExpansionDrive/qlib-results"
DEFAULT_RAW = "/Volumes/ExpansionDrive/market-data-raw"
DEFAULT_BOARD_DB = os.path.join(os.path.expanduser("~"), ".hermes", "kanban", "boards",
                                "quant-strategy-research", "kanban.db")
DEFAULT_RECORD = os.path.join(os.path.expanduser("~"), ".hermes", "wiki", "quant",
                              FAMILY + ".md")
DEFAULT_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPANSION = "/Volumes/ExpansionDrive"
HOME = os.path.expanduser("~")
EXPECTED_SYMBOLS = ["BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
KLINE_ROW_FIELDS = ["close", "close_time_ms", "high", "low", "open", "open_time_ms",
                    "volume"]
# The store nests to six levels (binance/usdm/klines/<SYMBOL>/<interval>/<file>). A planted
# dataset is probed to a depth the store cannot reach, so it cannot escape the probe.
PROBE_MAX_DEPTH = 8
# Name tokens that would have to exist for this record's required dataset (ETH on-chain
# transfers classified by point-in-time labelled exchange addresses, and the derived
# inflow/outflow/net-inflow series). One unified list is used by the raw entry-name probe,
# the house-wide scan and the checker's own re-measurement; the two documents can
# therefore never disagree about what the probe returns.
EXCHANGE_FLOW_TOKENS = (
    "exchange_net_inflow", "exchange_netflow", "exchange_inflow", "exchange_outflow",
    "net_inflow", "netinflow", "net_flow", "netflow", "inflow", "outflow",
    "exchange_flow", "cex_flow", "cexflow", "onchain_flow", "deposit_addr",
    "exchange_address", "address_label", "addresslabel", "labelled_address",
    "labeled_address", "wallet_label", "exchange_label", "label_vintage",
    "cex_wallet", "glassnode", "cryptoquant", "nansen", "arkham", "santiment",
    "intotheblock", "chainalysis", "etherscan", "onchain", "on-chain", "on_chain",
    "erc20", "tx_hash", "txhash", "ethereum_transfer", "eth_flow", "vintage")
# Tokens deliberately NOT probed, with the reason. A token that is a substring of a word the
# host already uses can only ever return guaranteed false positives; excluding it is a
# probe-design decision, and every exclusion is demonstrated by a measurement (see
# `probe_exclusion_demonstration` / `excluded_token_scan` in the measurement output). No
# exclusion removes a specific surface: the compound tokens stay in the probe list.
PROBE_EXCLUSIONS = {
    "eth": "substring of ordinary English words ('method', 'whether', 'withdraw'), of the "
           "word 'ethereum' itself and of the local contract names ('ETHUSDT' and every "
           "'ETHUSDT-<interval>-<month>.jsonl.gz' file), so a hit could never distinguish "
           "an on-chain ETH transfer dataset from those artefacts",
    "flow": "substring of the ordinary words 'workflow', 'overflow', 'flowchart'; the "
            "compound tokens 'exchange_flow', 'net_flow', 'eth_flow', 'onchain_flow' stay "
            "in the probe list, so the actual surface remains covered",
    "exchange": "generic word: matches the venue metadata ('BINANCE'), the repo/wiki prose "
                "about exchanges and web-cache files across the host; the compound flow "
                "tokens stay in the probe list",
    "label": "substring of UI/form label code and of the word 'labeled'/'labelling' across "
             "the host; the compound tokens 'address_label', 'wallet_label', "
             "'exchange_label' stay in the probe list",
    "address": "generic word: matches mail/address-list and memory-address artefacts "
               "across the host (the same class the earlier on-chain gate excluded)",
    "transfer": "substring of generic file-transfer code and prose ('transfer' in scripts, "
                "rsync logs and docs); 'ethereum_transfer' stays in the probe list",
    "wallet": "substring of wallet-app and custody prose unrelated to labelled exchange "
              "addresses; 'cex_wallet'/'wallet_label' stay in the probe list",
}
INSTRUMENT_FLOW_FIELDS = ("inflow", "outflow", "netflow", "net_flow", "flow", "onchain",
                         "on-chain", "address", "label", "exchange")
DOCUMENTED_DATASET_FAMILIES = ["funding", "instruments",
                               "klines (Binance USD-M perpetual futures, UTC)"]
# The store's own schema documents that its kline rows carry the OHLCV shape only; this is
# the sentence that owns the "no trade-level / taker-side surface" claim.
SCHEMA_MISSING_FIELDS_SENTENCE = "trade count and taker-buy splits are"
# the decisive registered data items and the exact status each must carry
DECISIVE_REQUIRED_STATUS = {
    "universe_eth_onchain_transfer_data": "ABSENT",
    "point_in_time_exchange_address_labels": "ABSENT",
    "exchange_inflow_outflow_series_hourly_or_finer": "ABSENT",
    "derived_net_inflow_inflow_minus_outflow": "NOT_CONSTRUCTIBLE",
    "price_reference_eth_spot_or_index_same_clock": "ABSENT_AS_REGISTERED_SOURCE",
    "forward_returns_1h_2h_3h_4h_6h_from_flow_events": "NOT_CONSTRUCTIBLE",
    "address_label_vintage_metadata": "ABSENT",
    "exchange_maintenance_wallet_migration_bridge_metadata": "ABSENT",
}
MISSING_DATA_MATRIX_ITEMS = (
    "universe_eth_onchain_transfer_data", "point_in_time_exchange_address_labels",
    "exchange_inflow_outflow_series_hourly_or_finer",
    "derived_net_inflow_inflow_minus_outflow",
    "price_reference_eth_spot_or_index_same_clock",
    "forward_returns_1h_2h_3h_4h_6h_from_flow_events",
    "current_lagged_eth_returns_source_style_controls", "address_label_vintage_metadata",
    "exchange_maintenance_wallet_migration_bridge_metadata", "instrument_eth",
    "timeframe_hourly_or_finer", "venue_and_timezone_normalization_utc",
    "perpetual_bid_ask_fees_slippage_depth", "perpetual_funding_mark_index_basis",
    "falsification_item_1_reconstruct_flows_point_in_time",
    "falsification_item_2_forward_return_regressions_source_sample",
    "falsification_item_3_strict_oos_from_2023_01_21",
    "falsification_item_4_normalization_variants",
    "falsification_item_5_incremental_information_controls",
    "falsification_item_6_top_pct_event_studies_and_placebos",
    "falsification_item_7_remove_wallet_migration_custody_events",
    "falsification_item_8_second_vendor_or_independent_label_map",
    "falsification_item_9_spot_and_perp_execution_net_of_cost",
    "baseline_zero_flow_placebo", "source_sample_2017_12_16_to_2023_01_20")
DECISIVE_MATRIX_ITEMS = tuple(DECISIVE_REQUIRED_STATUS)
# The record's own reported return-forecasting sample (Provenance / Evidence sections).
RECORD_SAMPLE_START = "2017-12-16"
RECORD_SAMPLE_END = "2023-01-20"
RECORD_SAMPLE_DAYS = 1861
# The raw's own daily window as registered on the card.
CARD_REGISTERED_RAW_WINDOW_START = "2022-01-01"
CARD_REGISTERED_RAW_WINDOW = "klines 2022-01-01\u21922026-09-11"
CARD_REGISTERED_FUNDING_WINDOW = "funding 2022-01-01T00:00Z\u21922026-09-12T08:00Z"
REGISTERED_PHASE_GRIDS = ["historical", "oos", "full", "fee_2x", "funding_2x",
                          "entry_delay_1_bar", "slippage_2ticks", "no_funding",
                          "no_funding_full", "cost_attrition_40bps"]
DCA_FOUR_AXES = ("spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct")
SEARCH_DOMAIN = "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN"
PROJECT_CONSTANT = "PROJECT_PRE_REGISTERED_CONSTANT"
USER_FIXED = "USER_FIXED"
OTHER_MARKET_NAMES = ("spot", "margin", "options", "inverse", "coinm", "delivery",
                      "futures", "quarter", "index", "onchain", "on-chain")
INVARIANT_TOKENS = ("30,000", "numeraire", "leverage 10x", "12 tranches", "tranche #12",
                    "reduce-only", "flat/kill")
TMP_ROOTS = [os.path.join(HOME, "workspace"), EXPANSION, "/Volumes/ResearchData",
             os.path.join(HOME, ".hermes"),
             os.path.join(HOME, "workspace", "qlib-apple-container")]
SCAN_SKIP_DIRS = ("node_modules", "__pycache__", ".git", "venvs", "site-packages", ".venv",
                  "Photos Library.photoslibrary")
# Classification of every house-wide hit. A hit is acceptable only if it is a document,
# code, session/log/cache artefact, our own provenance artefact, or a directory with no
# data file beneath it - never a data store that the raw tree lacks.
HOUSE_HIT_CLASSES = ("canonical_record_document", "related_wiki_document",
                     "own_evidence_snapshot_false_positive", "own_round_artifact_false_positive",
                     "own_family_directory_false_positive", "own_checker_source_false_positive",
                     "repo_documentation_false_positive", "source_code_false_positive",
                     "research_document_false_positive",
                     "agent_session_or_log_false_positive", "hermes_cache_false_positive",
                     "contributor_metadata_false_positive",
                     "hermes_skill_or_catalog_false_positive",
                     "integrity_manifest_false_positive",
                     "non_canonical_staging_copy_false_positive",
                     "dataset_stub_directory_false_positive",
                     "unclassified_data_candidate")
UNCLASSIFIED_CLASS = "unclassified_data_candidate"
INTEGRITY_MANIFEST_MARKERS = ("hash-manifest", "hashes", "rehash", "hash_compare",
                              "pre_hashes", "post_hash", "hash_three_states", "results_hash")
# this card's own evidence snapshot carries the family name in its filename
OWN_ARTIFACT_BASENAMES = (FAMILY + "-prerequisite-gate-20260917.json",)
WIKI_DIR_MARKER = os.path.join(".hermes", "wiki", "quant")
ROUND_DIR_PARTS = (FAMILY, "rounds", ROUND)
# the raw-side values the persisted artifact claims and that C12 re-derives live
CLAIMED_MEASUREMENT_KEYS = (
    "raw_1d_window_utc", "raw_1d_history_days", "instrument_count",
    "instrument_symbols", "instrument_ids", "instrument_base_currencies",
    "instrument_quote_currencies", "instrument_settlement_currencies",
    "instrument_types", "instrument_is_inverse", "instrument_field_names",
    "instrument_flow_field_hits", "instrument_definition_files_outside_usdm",
    "klines_dataset_dirs", "klines_interval_set", "klines_row_field_set",
    "klines_1h_open_step_seconds", "funding_symbol_dirs", "funding_row_keys",
    "funding_venues", "raw_entry_name_count", "exchange_flow_token_hits_in_entry_names",
    "ref_or_onchain_subtrees", "exchange_flow_dataset_present",
    "exchange_address_label_dataset_present", "label_vintage_metadata_present",
    "eth_onchain_transfer_dataset_present", "net_inflow_series_constructible",
    "forward_return_regression_from_flow_constructible", "record_reported_sample",
    "record_sample_days_inside_raw_window", "record_sample_coverage_fraction",
    "schema_dataset_sections", "schema_documents_exchange_flow_dataset",
    "schema_sha256", "inventory_sha256", "meta_sha256",
    "venue", "market_type", "binance_market_dirs", "paths_named_other_market",
    "non_binance_paths", "probe_tokens_tested", "probe_exclusions",
)


def _load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _dirs(path, skip_dotfiles=True):
    if not os.path.isdir(path):
        return None
    out = sorted(os.listdir(path))
    if skip_dotfiles:
        out = [x for x in out if not x.startswith(".")]
    return out


def _rel_dirs(root, max_depth=3):
    out = []
    for dp, dn, fn in os.walk(root):
        rel = os.path.relpath(dp, root)
        if rel.count(os.sep) >= max_depth:
            dn[:] = []
            continue
        out.append(rel)
    return sorted(p for p in out if p != ".")


def _all_entries(root, max_depth=PROBE_MAX_DEPTH):
    """Every file/directory basename token under the raw root (for name probes)."""
    names = []
    for dp, dn, fn in os.walk(root):
        rel = os.path.relpath(dp, root)
        if rel.count(os.sep) >= max_depth:
            dn[:] = []
            continue
        dn[:] = [d for d in dn if not d.startswith(".")]
        names.extend(dn)
        names.extend(f for f in fn if not f.startswith("."))
    return sorted({n.lower() for n in names})


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def _sha256_text(text):
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _rows(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


def _iso(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _day_span_days(a_iso, b_iso):
    a = datetime.fromisoformat(a_iso)
    b = datetime.fromisoformat(b_iso)
    return (b - a).days


def measure_raw(raw):
    """Independent re-measurement of the canonical raw. No artifact is trusted."""
    m = {}
    m["raw_root"] = raw
    m["top_level_dirs"] = _dirs(raw)
    m["binance_market_dirs"] = _dirs(os.path.join(raw, "binance"))
    m["binance_usdm_subdirs"] = _dirs(os.path.join(raw, "binance", "usdm"))
    m["raw_paths"] = _rel_dirs(raw, 3)
    m["paths_named_other_market"] = [
        p for p in m["raw_paths"] if os.path.basename(p).lower() in OTHER_MARKET_NAMES]
    m["non_binance_paths"] = [p for p in m["raw_paths"]
                              if not p.startswith("binance") and not p.startswith("_")]
    cfg_path = os.path.join(raw, "_meta", "CONFIG.json")
    cfg = _load_json(cfg_path) if os.path.exists(cfg_path) else {}
    m["venue"] = cfg.get("venue")
    m["market_type"] = cfg.get("market_type")
    m["symbols"] = sorted(cfg.get("symbols") or [])
    m["config_intervals"] = sorted(cfg.get("intervals") or [])
    m["klines_end_rule"] = cfg.get("klines_end_rule")

    kl = os.path.join(raw, "binance", "usdm", "klines")
    ud = os.path.join(raw, "binance", "usdm")
    m["klines_dataset_dirs"] = _dirs(kl)
    m["klines_interval_set"] = _dirs(os.path.join(kl, "ETHUSDT"))
    eth1h = os.path.join(kl, "ETHUSDT", "1h")
    files1h = sorted(f for f in os.listdir(eth1h) if f.endswith(".jsonl.gz")) \
        if os.path.isdir(eth1h) else []
    if files1h:
        r_first = _rows(os.path.join(eth1h, files1h[0]))
        r_last = _rows(os.path.join(eth1h, files1h[-1]))
        m["klines_row_field_set"] = sorted(r_last[-1].keys())
        m["klines_1h_first_open_utc"] = _iso(r_first[0]["open_time_ms"])
        m["klines_1h_last_open_utc"] = _iso(r_last[-1]["open_time_ms"])
        m["klines_1h_open_step_seconds_sample"] = [
            (r_first[i + 1]["open_time_ms"] - r_first[i]["open_time_ms"]) // 1000
            for i in range(min(3, len(r_first) - 1))]
        steps = set()
        for f in files1h:
            rr = _rows(os.path.join(eth1h, f))
            for i in range(len(rr) - 1):
                steps.add((rr[i + 1]["open_time_ms"] - rr[i]["open_time_ms"]) // 1000)
        m["klines_1h_open_step_seconds"] = sorted(steps)
    d1 = os.path.join(kl, "ETHUSDT", "1d")
    files1 = sorted(f for f in os.listdir(d1) if f.endswith(".jsonl.gz")) \
        if os.path.isdir(d1) else []
    first, last = {}, {}
    for sym in (m["klines_dataset_dirs"] or []):
        d = os.path.join(kl, sym, "1d")
        if not os.path.isdir(d):
            continue
        fs = sorted(f for f in os.listdir(d) if f.endswith(".jsonl.gz"))
        if not fs:
            continue
        first[sym] = _rows(os.path.join(d, fs[0]))[0]["open_time_ms"]
        last[sym] = _rows(os.path.join(d, fs[-1]))[-1]["open_time_ms"]
    m["klines_1d_first_by_symbol"] = {k: _iso(v) for k, v in first.items()}
    m["klines_1d_last_by_symbol"] = {k: _iso(v) for k, v in last.items()}
    window = [_iso(min(first.values())), _iso(max(last.values()))] if first else None
    m["raw_1d_window_utc"] = window
    m["card_registered_raw_window"] = CARD_REGISTERED_RAW_WINDOW
    m["card_registered_funding_window"] = CARD_REGISTERED_FUNDING_WINDOW
    m["raw_1d_window_starts_at_or_after_card_registration"] = bool(
        window and window[0][:10] >= CARD_REGISTERED_RAW_WINDOW_START)
    if window:
        m["raw_1d_history_days"] = _day_span_days(window[0], window[1])
    # the record's own reported sample, and how much of it the raw's window can cover
    m["record_reported_sample"] = {"start": RECORD_SAMPLE_START, "end": RECORD_SAMPLE_END,
                                   "days": RECORD_SAMPLE_DAYS}
    if window:
        a = max(RECORD_SAMPLE_START, window[0][:10])
        b = min(RECORD_SAMPLE_END, window[1][:10])
        overlap = max(0, _day_span_days(a + "T00:00:00+00:00", b + "T00:00:00+00:00"))
        m["record_sample_days_inside_raw_window"] = overlap
        m["record_sample_coverage_fraction"] = round(overlap / RECORD_SAMPLE_DAYS, 4)
        m["raw_window_covers_oos_extension_from_2023_01_21"] = window[1][:10] >= "2023-01-21"

    fu = os.path.join(ud, "funding")
    m["funding_symbol_dirs"] = _dirs(fu)
    p = os.path.join(fu, "ETHUSDT", "ETHUSDT-funding.jsonl.gz")
    if os.path.exists(p):
        rs = _rows(p)
        m["funding_row_keys"] = sorted(rs[-1].keys())
        m["funding_venues"] = sorted({str(r.get("venue")) for r in rs})
        m["funding_first_ms"] = _iso(rs[0]["funding_time_ms"])
        m["funding_last_ms"] = _iso(rs[-1]["funding_time_ms"])
    m["funding_row_has_flow_field"] = any(
        t in k.lower() for k in (m.get("funding_row_keys") or [])
        for t in EXCHANGE_FLOW_TOKENS)

    inst_path = os.path.join(ud, "instruments", "usdm-perp-instruments.json")
    inst = _load_json(inst_path).get("instruments", []) if os.path.exists(inst_path) else []
    m["instrument_count"] = len(inst)
    m["instrument_symbols"] = sorted(i["fields"].get("raw_symbol") for i in inst
                                     if i["fields"].get("raw_symbol"))
    m["instrument_ids"] = sorted(i["fields"].get("id") for i in inst
                                 if i["fields"].get("id"))
    m["instrument_base_currencies"] = sorted(i["fields"].get("base_currency") for i in inst
                                             if i["fields"].get("base_currency"))
    m["instrument_is_inverse"] = sorted({bool(i["fields"].get("is_inverse")) for i in inst})
    m["instrument_types"] = sorted({i["fields"].get("type") for i in inst})
    m["instrument_quote_currencies"] = sorted({i["fields"].get("quote_currency")
                                               for i in inst
                                               if i["fields"].get("quote_currency")})
    m["instrument_settlement_currencies"] = sorted({i["fields"].get("settlement_currency")
                                                    for i in inst
                                                    if i["fields"].get("settlement_currency")})
    m["instrument_field_names"] = sorted({k for i in inst for k in i["fields"]})
    m["instrument_flow_field_hits"] = sorted(
        f for f in m["instrument_field_names"]
        if any(t in f.lower() for t in INSTRUMENT_FLOW_FIELDS))
    m["kline_row_has_flow_field"] = any(
        t in f.lower() for f in (m.get("klines_row_field_set") or [])
        for t in EXCHANGE_FLOW_TOKENS)

    schema_path = os.path.join(raw, "_meta", "SCHEMA.md")
    schema = open(schema_path, encoding="utf-8").read() if os.path.exists(schema_path) else ""
    flat = schema.replace("*", "")
    m["schema_sha256"] = _sha256_text(schema) if schema else None
    m["schema_dataset_sections"] = sorted(ln.split("## Dataset:", 1)[1].strip()
                                          for ln in schema.splitlines()
                                          if ln.startswith("## Dataset:"))
    m["schema_documents_exchange_flow_dataset"] = any(
        t in flat.lower() for t in ("net inflow", "net-inflow", "inflow", "outflow",
                                    "exchange flow", "address label", "labelled address",
                                    "on-chain transfer", "onchain transfer"))
    m["schema_single_venue"] = "Binance USD-M perpetual futures, UTC" in schema
    m["schema_documents_missing_fields"] = ("quote_volume" in flat
                                            and SCHEMA_MISSING_FIELDS_SENTENCE in flat)
    m["schema_no_silent_gapfill"] = "No gaps are silently filled" in schema
    m["schema_flow_word_lines"] = [ln.strip()[:90] for ln in schema.splitlines()
                                   if any(t in ln.lower()
                                          for t in ("inflow", "outflow", "flow", "address",
                                                    "label", "on-chain", "onchain",
                                                    "asset", "ledger"))]
    inv_path = os.path.join(raw, "_meta", "INVENTORY.md")
    inv = open(inv_path, encoding="utf-8").read() if os.path.exists(inv_path) else ""
    m["inventory_sha256"] = _sha256_text(inv) if inv else None
    m["inventory_declares_no_other_store_found"] = (
        "No other Binance/klines/tardis/aggTrade store was found on" in inv)
    m["inventory_launchd_updater_line"] = next(
        (ln.strip()[:120] for ln in inv.splitlines()
         if "market_data_sync.py" in ln and "launchd" in ln), None)

    names = _all_entries(raw, PROBE_MAX_DEPTH)
    joined = "\n".join(names)
    # A dataset name may use hyphens where a spec uses underscores ('address-labels' vs
    # 'address_label'), so every token is matched against the raw names AND against a
    # separator-normalised copy. Normalising can only add hits, never hide one.
    joined_norm = joined.replace("-", "_").replace(" ", "_")
    m["raw_entry_name_count"] = len(names)
    m["probe_tokens_tested"] = list(EXCHANGE_FLOW_TOKENS)
    m["exchange_flow_token_hits_in_entry_names"] = sorted(
        {t for t in EXCHANGE_FLOW_TOKENS if t in joined or t.replace("-", "_") in joined_norm})
    m["flow_token_name_examples"] = {
        t: sorted({n for n in names if t in n
                   or t.replace("-", "_") in n.lower().replace("-", "_").replace(" ", "_")})[:3]
        for t in EXCHANGE_FLOW_TOKENS if any(
            t in n or t.replace("-", "_") in n.lower().replace("-", "_").replace(" ", "_")
            for n in names)}
    m["excluded_token_raw_tree_collisions"] = {
        t: sorted({n for n in names if t in n})[:3] for t in PROBE_EXCLUSIONS
        if any(t in n for n in names)}
    m["raw_entry_names_sample"] = names[:40]
    m["probe_exclusions"] = dict(PROBE_EXCLUSIONS)
    # reference subtrees: a planted on-chain/flow store normally lands in one of these
    m["ref_or_onchain_subtrees"] = sorted(
        p for p in m["raw_paths"]
        if p.split(os.sep)[0].lower() in ("_ref", "ref", "reference", "onchain", "on-chain",
                                          "flows", "flow", "labels", "entities", "labels_raw"))
    # any second instrument-definition surface (a spot/inverse/other-venue definition file);
    # `_meta/**` holds the store's own provenance manifests for the same usdm instruments,
    # so it is not a second surface.
    m["instrument_definition_files_outside_usdm"] = sorted(
        os.path.join(dp, f) for dp, dn, fn in os.walk(raw)
        for f in fn if "instrument" in f.lower()
        and os.path.relpath(os.path.join(dp, f), raw).split(os.sep)[:2] != ["binance", "usdm"]
        and os.path.relpath(os.path.join(dp, f), raw).split(os.sep)[0] != "_meta")
    m["meta_sha256"] = {rel: _sha256_file(os.path.join(raw, "_meta", rel))
                        for rel in ("CONFIG.json", "SCHEMA.md", "INVENTORY.md",
                                    "INSTRUMENTS_EXPORT.json", "TRANSCODE_MANIFEST.json",
                                    "FUNDING_EXPORT.json")
                        if os.path.exists(os.path.join(raw, "_meta", rel))}

    # ---- the record's decision-variable surface, measured item by item -------------
    hay = joined + "\n" + joined_norm
    m["exchange_flow_dataset_present"] = bool(m["exchange_flow_token_hits_in_entry_names"])
    m["exchange_address_label_dataset_present"] = any(
        t in hay for t in ("address_label", "addresslabel", "labelled_address",
                           "labeled_address", "wallet_label", "exchange_label",
                           "exchange_address", "cex_wallet"))
    m["label_vintage_metadata_present"] = "vintage" in hay
    m["eth_onchain_transfer_dataset_present"] = any(
        t in hay for t in ("tx_hash", "txhash", "ethereum_transfer", "erc20",
                           "onchain", "on-chain", "on_chain"))
    m["net_inflow_series_constructible"] = bool(
        m["exchange_flow_dataset_present"] and m["exchange_address_label_dataset_present"])
    m["forward_return_regression_from_flow_constructible"] = bool(
        m["net_inflow_series_constructible"] and m["label_vintage_metadata_present"])
    m["note"] = ("single venue (BINANCE USD-M perpetual), four fixed contracts: klines "
                 "whose rows carry the seven-field OHLCV shape only, that venue's funding, "
                 "and its instrument definitions. No blockchain transfer surface, no "
                 "exchange-address label surface, no inflow/outflow series, and no ETH/USD "
                 "(spot dollars) price source; every instrument is quoted and settled in "
                 "USDT. The raw's own daily history begins 2022-01-01, so only 384 of the "
                 "record's reported 1861 sample days (20.6%) fall inside it.")
    return m


def run_checks(results_root, raw_root, repo_root=None, card_body_path=None,
               record_path=None, board_db=None):
    checks = []
    add = lambda cid, ok, detail: checks.append({
        "id": cid, "status": "PASS" if ok else "FAIL", "detail": detail})
    raw = measure_raw(raw_root)
    round_dir = os.path.join(results_root, FAMILY, "rounds", ROUND)
    spec_path = os.path.join(round_dir, "round-spec.json")
    verdict_path = os.path.join(round_dir, "verdict.json")
    spec = _load_json(spec_path) if os.path.exists(spec_path) else {}
    verdict = _load_json(verdict_path) if os.path.exists(verdict_path) else {}
    pg = spec.get("prerequisite_gate") or {}
    ur = spec.get("universe_registration") or {}

    # C1 - one market type for one venue, and no other market directory at all
    #      (no spot market, no dated futures, no second venue, no reference subtree).
    md = raw["binance_market_dirs"] or []
    add("C1", md == ["usdm"] and raw["venue"] == "BINANCE" and raw["market_type"] == "usdm_perp"
        and raw["paths_named_other_market"] == [] and raw["non_binance_paths"] == []
        and raw["ref_or_onchain_subtrees"] == [],
        "binance market dirs=%s venue=%s market_type=%s other_market_paths=%s "
        "non_binance_paths=%s ref/onchain subtrees=%s"
        % (md, raw["venue"], raw["market_type"], raw["paths_named_other_market"],
           raw["non_binance_paths"], raw["ref_or_onchain_subtrees"]))

    # C2 - the four USD-M perpetual contracts are the only local instruments, all
    #      USDT-quoted and USDT-settled, and no instrument definition carries a
    #      flow/address/label field (the registered universe is an ETH on-chain surface,
    #      not a contract list).
    add("C2", raw["instrument_count"] == 4
        and raw["instrument_symbols"] == EXPECTED_SYMBOLS
        and raw["instrument_types"] == ["CryptoPerpetual"]
        and raw["instrument_quote_currencies"] == ["USDT"]
        and raw["instrument_settlement_currencies"] == ["USDT"]
        and raw["instrument_is_inverse"] == [False]
        and raw["instrument_flow_field_hits"] == []
        and raw["instrument_definition_files_outside_usdm"] == [],
        "instruments=%d symbols=%s types=%s quote=%s settlement=%s is_inverse=%s "
        "flow_fields=%s instrument files outside binance/usdm=%s"
        % (raw["instrument_count"], raw["instrument_symbols"], raw["instrument_types"],
           raw["instrument_quote_currencies"], raw["instrument_settlement_currencies"],
           raw["instrument_is_inverse"], raw["instrument_flow_field_hits"],
           raw["instrument_definition_files_outside_usdm"]))

    # C3 - the stored row shapes carry no flow surface and the store documents its own
    #      dataset families (klines/funding/instruments) plus the fields it does NOT hold.
    add("C3", raw["klines_row_field_set"] == KLINE_ROW_FIELDS
        and raw["kline_row_has_flow_field"] is False
        and raw["funding_row_has_flow_field"] is False
        and raw["schema_dataset_sections"] == DOCUMENTED_DATASET_FAMILIES
        and raw["schema_documents_exchange_flow_dataset"] is False
        and raw["schema_documents_missing_fields"] is True
        and raw["inventory_declares_no_other_store_found"] is True,
        "kline row fields=%s kline flow field=%s funding flow field=%s documented dataset "
        "families=%s schema documents a flow dataset=%s schema documents missing fields=%s "
        "inventory declares no other store=%s"
        % (raw["klines_row_field_set"], raw["kline_row_has_flow_field"],
           raw["funding_row_has_flow_field"], raw["schema_dataset_sections"],
           raw["schema_documents_exchange_flow_dataset"],
           raw["schema_documents_missing_fields"],
           raw["inventory_declares_no_other_store_found"]))

    # C4 - the decisive absence: the whole-tree entry-name probe returns no ETH
    #      on-chain transfer / exchange-flow / address-label dataset.
    add("C4", raw["exchange_flow_token_hits_in_entry_names"] == []
        and raw["exchange_flow_dataset_present"] is False
        and raw["exchange_address_label_dataset_present"] is False
        and raw["label_vintage_metadata_present"] is False
        and raw["eth_onchain_transfer_dataset_present"] is False,
        "token hits in entry names=%s dataset present=%s address-label dataset present=%s "
        "label-vintage metadata present=%s on-chain transfer dataset present=%s "
        "(probe: %d tokens, %d entry names, depth %d)"
        % (raw["exchange_flow_token_hits_in_entry_names"],
           raw["exchange_flow_dataset_present"],
           raw["exchange_address_label_dataset_present"],
           raw["label_vintage_metadata_present"],
           raw["eth_onchain_transfer_dataset_present"], len(EXCHANGE_FLOW_TOKENS),
           raw["raw_entry_name_count"], PROBE_MAX_DEPTH))

    # C5 - the registered price reference (ETH spot or index price on the same clock) is
    #      absent: there is no spot/index market path anywhere in the tree and the only local
    #      ETH series is a USDT-quoted USD-M perpetual.
    add("C5", raw["klines_dataset_dirs"] == EXPECTED_SYMBOLS
        and raw["instrument_settlement_currencies"] == ["USDT"]
        and raw["paths_named_other_market"] == []
        and raw["instrument_definition_files_outside_usdm"] == []
        and "1h" in (raw["klines_interval_set"] or []),
        "klines datasets=%s ETHUSDT intervals=%s quote/settlement=%s/%s no other-market path="
        "%s no instrument surface outside binance/usdm=%s - a USDT-quoted USD-M perpetual is "
        "not the registered 'ETH spot or index price aligned to the same clock' source"
        % (raw["klines_dataset_dirs"], raw["klines_interval_set"],
           raw["instrument_quote_currencies"], raw["instrument_settlement_currencies"],
           raw["paths_named_other_market"] == [],
           raw["instrument_definition_files_outside_usdm"] == []))

    # C6 - the record's own decision variable and its flow-conditioned forward returns are
    #      not constructible from anything the store holds.
    add("C6", raw["net_inflow_series_constructible"] is False
        and raw["forward_return_regression_from_flow_constructible"] is False
        and raw["eth_onchain_transfer_dataset_present"] is False,
        "net-inflow series constructible=%s flow-conditioned 1/2/3/4/6h forward-return "
        "surface constructible=%s on-chain transfer dataset present=%s"
        % (raw["net_inflow_series_constructible"],
           raw["forward_return_regression_from_flow_constructible"],
           raw["eth_onchain_transfer_dataset_present"]))

    # C7 - the raw's own daily window vs the record's reported sample: the window starts at
    #      2022-01-01, so it covers only the measured fraction of the record's sample.
    add("C7", raw["raw_1d_window_starts_at_or_after_card_registration"] is True
        and raw["raw_1d_window_utc"] is not None
        and raw["raw_1d_window_utc"][0][:10] == CARD_REGISTERED_RAW_WINDOW_START
        and raw["record_sample_days_inside_raw_window"] == 384
        and raw["record_sample_coverage_fraction"] == 0.2063,
        "raw 1d window=%s record sample=%s..%s (%d days) days inside window=%s "
        "coverage=%s oos extension from 2023-01-21 covered=%s"
        % (raw["raw_1d_window_utc"], RECORD_SAMPLE_START, RECORD_SAMPLE_END,
           RECORD_SAMPLE_DAYS, raw["record_sample_days_inside_raw_window"],
           raw["record_sample_coverage_fraction"],
           raw["raw_window_covers_oos_extension_from_2023_01_21"]))

    # C8 - the persisted verdict states exactly the contract-mandated terminal values.
    att = verdict.get("attempts") or {}
    add("C8", verdict.get("verdict") == "TECHNICAL_INCOMPLETE"
        and verdict.get("performance_claimable") is False
        and (verdict.get("failure") or {}).get("layer") == "card-local"
        and (verdict.get("failure") or {}).get("class") == "data_window_invalid"
        and (verdict.get("yield") or {}).get("yield_decision") == "STOP_TECHNICAL_INCOMPLETE"
        and verdict.get("run_id") is None
        and att.get("launched") == 0 and att.get("run_specs") == 0
        and att.get("terminal_sentinels") == 0
        and verdict.get("evidence_run_ids") == []
        and verdict.get("family_id") == FAMILY and verdict.get("round_id") == ROUND
        and verdict.get("kanban_task_id") == TASK,
        "verdict=%s performance_claimable=%s layer=%s class=%s yield=%s run_id=%s "
        "attempts=%s evidence_run_ids=%s"
        % (verdict.get("verdict"), verdict.get("performance_claimable"),
           (verdict.get("failure") or {}).get("layer"),
           (verdict.get("failure") or {}).get("class"),
           (verdict.get("yield") or {}).get("yield_decision"), verdict.get("run_id"),
           att, verdict.get("evidence_run_ids")))

    # C9 - nothing was ever submitted: the round directory holds exactly the two immutable
    #      artifacts, no attempt directory, no terminal sentinel.
    present = sorted(os.listdir(round_dir)) if os.path.isdir(round_dir) else []
    attempts_dir = os.path.join(round_dir, "attempts")
    add("C9", present == ["round-spec.json", "verdict.json"]
        and not os.path.exists(attempts_dir)
        and pg.get("attempts_launched") == 0
        and (verdict.get("terminal_evidence") or {}).get("attempt_dir") is None
        and (verdict.get("terminal_evidence") or {}).get("terminal_sentinel") is None
        and (verdict.get("terminal_evidence") or {}).get("run_spec") is None,
        "round dir entries=%s attempts dir exists=%s attempts_launched=%s terminal_evidence="
        "attempt_dir/run_spec/sentinel=%s"
        % (present, os.path.exists(attempts_dir), pg.get("attempts_launched"),
           [(verdict.get("terminal_evidence") or {}).get(k)
            for k in ("attempt_dir", "run_spec", "terminal_sentinel")]))

    # C10 - the registered requirement was NOT shrunk to the locally available instruments.
    add("C10", ur.get("universe_shrunk_to_local_list") is False
        and ur.get("instrument_required") == ["ETH"]
        and sorted(ur.get("local_contracts_not_in_record_universe") or []) == EXPECTED_SYMBOLS
        and (ur.get("onchain_dataset_available") is False)
        and (ur.get("exchange_address_label_dataset_available") is False)
        and (ur.get("price_source_available_as_registered") is False),
        "universe_shrunk_to_local_list=%s instrument_required=%s local contracts outside "
        "the record universe=%s on-chain dataset available=%s address-label dataset "
        "available=%s registered price source available=%s"
        % (ur.get("universe_shrunk_to_local_list"), ur.get("instrument_required"),
           ur.get("local_contracts_not_in_record_universe"),
           ur.get("onchain_dataset_available"),
           ur.get("exchange_address_label_dataset_available"),
           ur.get("price_source_available_as_registered")))

    # C11 - the DCA registration still carries the contract 7.2 v1.3.1 provenance classes
    #       and the complete 48-cell product.
    dd = spec.get("dca_domain") or {}
    statuses = [dd.get(a + "_status") for a in DCA_FOUR_AXES]
    grid = dd.get("grid") or []
    add("C11", dd.get("base_quote_status") == PROJECT_CONSTANT
        and statuses == [SEARCH_DOMAIN] * 4
        and dd.get("config_count") == 48 and len(grid) == 48
        and dd.get("base_quote") == 1000
        and _sav2 is not None
        and set(DCA_FOUR_AXES) <= set(getattr(_sav2, "USER_FIXED_FORBIDDEN_KEYS", ()))
        and dd.get("spacing_pct") == [0.01, 0.02, 0.03, 0.04]
        and dd.get("size_multiplier") == [1.0, 1.1]
        and dd.get("breakeven_tp_pct") == [0.01, 0.02, 0.03]
        and dd.get("invalidation_pct") == [0.05, 0.10],
        "searched axes statuses=%s base_quote=%s base_quote_status=%s config_count=%s grid "
        "len=%s axes=%s" % (statuses, dd.get("base_quote"), dd.get("base_quote_status"),
                            dd.get("config_count"), len(grid),
                            {a: dd.get(a) for a in DCA_FOUR_AXES}))

    # C12 - artifact-to-live-raw agreement: every raw-side value the persisted artifact
    #       claims still equals a live re-measurement (so a planted dataset cannot pass).
    claims = pg.get("measured_available") or {}
    disagreements = [k for k in CLAIMED_MEASUREMENT_KEYS
                     if k in claims and claims[k] != raw.get(k)]
    add("C12", claims and not disagreements
        and set(CLAIMED_MEASUREMENT_KEYS) <= set(claims),
        "claimed keys=%d covered=%d disagreements vs live raw=%s missing keys=%s"
        % (len(claims), len(set(CLAIMED_MEASUREMENT_KEYS) & set(claims)), disagreements,
           sorted(set(CLAIMED_MEASUREMENT_KEYS) - set(claims))))

    # C13 - every persisted `*_verbatim` leaf resolves against its declared source.
    vv = verify_verbatim(spec_path, repo_root=repo_root, card_body_path=card_body_path,
                         record_path=record_path, board_db=board_db)
    add("C13", vv["misses"] == [] and vv["problems"] == []
        and vv["unclassified_verbatim_paths"] == [] and vv["missing_from_source_map"] == []
        and vv["orphan_map_entries"] == [] and vv["duplicate_map_keys"] == []
        and vv["digests_disagreeing_with_content"] == []
        and vv["source_sha256_matches_declared"] is True,
        "%d verbatim leaves resolved (%s), misses=%d problems=%s unclassified=%d "
        "missing_from_map=%d orphan_map=%d"
        % (vv["verbatim_strings_on_disk"], vv["declared_source_counts"],
           len(vv["misses"]), vv["problems"], len(vv["unclassified_verbatim_paths"]),
           len(vv["missing_from_source_map"]), len(vv["orphan_map_entries"])))

    # C14 - coverage and survivor surface is empty by construction, the registered phase grid
    #       is intact, and every decisive requirement row still carries its registered status
    #       (no decisive item may be flipped to PRESENT).
    cov = verdict.get("coverage") or {}
    sur = verdict.get("survivors") or {}
    matrix = pg.get("required_data_matrix") or []
    row_status = {row.get("item"): row.get("status") for row in matrix}
    bad_status = {k: row_status.get(k) for k, v in DECISIVE_REQUIRED_STATUS.items()
                  if row_status.get(k) != v}
    decisive_status = {k: row_status.get(k) for k in DECISIVE_REQUIRED_STATUS}
    add("C14", cov.get("cohorts_realized") == 0 and cov.get("case_evaluations") == 0
        and cov.get("registered_phase_grids") == REGISTERED_PHASE_GRIDS
        and cov.get("coverage_complete") is False
        and sur.get("count") == 0 and sur.get("bundle") is None and sur.get("ranking") is None
        and pg.get("required_data_available") is False
        and pg.get("outcome") == "PREREQUISITE_ABSENT"
        and sorted(pg.get("decisive_items") or []) == sorted(DECISIVE_MATRIX_ITEMS)
        and decisive_status == DECISIVE_REQUIRED_STATUS
        and [row.get("item") for row in matrix] == list(MISSING_DATA_MATRIX_ITEMS),
        "cohorts=%s cases=%s phase grids=%d complete=%s survivors=%s required_data_available="
        "%s outcome=%s decisive_items=%d matrix rows=%d wrong_statuses=%s"
        % (cov.get("cohorts_realized"), cov.get("case_evaluations"),
           len(cov.get("registered_phase_grids") or []), cov.get("coverage_complete"),
           sur.get("count"), pg.get("required_data_available"), pg.get("outcome"),
           len(pg.get("decisive_items") or []), len(matrix), bad_status))

    overall = "PASS" if all(c["status"] == "PASS" for c in checks) else "FAIL"
    return {"family_id": FAMILY, "round_id": ROUND, "kanban_task_id": TASK,
            "checks": checks, "overall": overall,
            "raw_measurement_note": raw["note"]}


def _resolve_source_texts(repo_root=None, card_body_path=None, record_path=None,
                          board_db=None, expected_card_sha=None):
    """The four declared verbatim sources, read from their live locations."""
    repo_root = repo_root or DEFAULT_REPO
    record_path = record_path or DEFAULT_RECORD
    board_db = board_db or DEFAULT_BOARD_DB
    contract_path = os.path.join(repo_root, "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md")
    texts = {}
    texts["contract"] = open(contract_path, encoding="utf-8").read()
    texts["record"] = open(record_path, encoding="utf-8").read()
    if _LIFECYCLE_FOOTER is None:
        raise RuntimeError("runtime/production_handoff.py LIFECYCLE_FOOTER not importable")
    texts["footer"] = _LIFECYCLE_FOOTER
    if card_body_path:
        texts["card"] = open(card_body_path, encoding="utf-8").read()
    else:
        if not os.path.exists(board_db):
            raise RuntimeError("board db not readable: %s (pass --card-body-file)" % board_db)
        con = sqlite3.connect("file:%s?mode=ro" % board_db, uri=True)
        try:
            row = con.execute("select body from tasks where id=?", (TASK,)).fetchone()
        finally:
            con.close()
        if not row or not row[0]:
            raise RuntimeError("card %s body not found in %s" % (TASK, board_db))
        body = row[0]
        if not body.endswith(_LIFECYCLE_FOOTER):
            raise RuntimeError("card body does not end with the system-owned lifecycle footer")
        texts["card"] = body[:-len(_LIFECYCLE_FOOTER)]
    texts["__sha256__"] = {k: _sha256_text(v) for k, v in texts.items()}
    if expected_card_sha and texts["__sha256__"]["card"] != expected_card_sha:
        raise RuntimeError("card body sha256 %s != declared %s"
                           % (texts["__sha256__"]["card"], expected_card_sha))
    return texts


def _walk_verbatim(node, path=(), flagged=False):
    """Every scalar leaf under a key (or under any ancestor key) containing 'verbatim'.

    A `*_verbatim` key may hold a single string, a list of strings (one excerpt split into
    lines), or a nested block; all of them are resolved, so the convention cannot be dodged
    by wrapping an excerpt in a list or an object. `excerpt_source_map` is the map itself,
    not content, and is never walked.
    """
    out = []
    if isinstance(node, dict):
        for k, v in node.items():
            if str(k) == "excerpt_source_map":
                continue
            out.extend(_walk_verbatim(v, path + (str(k),),
                                      flagged or "verbatim" in str(k).lower()))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out.extend(_walk_verbatim(v, path + (str(i),), flagged))
    elif flagged:
        key = next((seg for seg in reversed(path) if "verbatim" in seg.lower()),
                   path[-1] if path else "")
        out.append((".".join(path), key, node))
    return out


def verify_verbatim(spec_path, repo_root=None, card_body_path=None, record_path=None,
                    board_db=None):
    """Resolve every persisted `*_verbatim` leaf against its declared source."""
    spec = _load_json(spec_path)
    src_map = spec.get("excerpt_source_map") or {}
    expected_card_sha = ((spec.get("provenance") or {}).get("card") or {}).get(
        "frozen_candidate_body_sha256")
    texts = _resolve_source_texts(repo_root=repo_root, card_body_path=card_body_path,
                                 record_path=record_path, board_db=board_db,
                                 expected_card_sha=expected_card_sha)
    leaves = _walk_verbatim(spec)
    report = {"verbatim_strings_on_disk": len(leaves), "strings_with_declared_source": 0,
              "misses": [], "unclassified_verbatim_paths": [], "declared_source_counts": {},
              "missing_from_source_map": [], "orphan_map_entries": [], "problems": [],
              "duplicate_map_keys": [], "digests_disagreeing_with_content": [],
              "source_sha256_matches_declared": True,
              "source_sha256": {k: v for k, v in texts["__sha256__"].items()
                                if not k.startswith("__")}}
    keys = [k for _, k, _ in leaves]
    dupes = [k for k, n in Counter(keys).items() if n > 1]
    if dupes:
        report["duplicate_map_keys"] = dupes
        report["problems"].append("duplicate verbatim key names: %s" % dupes)
    for path, key, value in leaves:
        entry = src_map.get(key)
        if not entry:
            report["missing_from_source_map"].append({"path": path, "key": key})
            continue
        src = entry.get("source")
        if src not in ("card", "record", "contract", "footer"):
            report["unclassified_verbatim_paths"].append({"path": path, "key": key,
                                                          "source": src})
            continue
        report["strings_with_declared_source"] += 1
        report["declared_source_counts"][src] = report["declared_source_counts"].get(src, 0) + 1
        if value not in texts[src]:
            report["misses"].append({"path": path, "key": key, "source": src,
                                     "chars": len(value), "head": value[:70]})
        if _sha256_text(value) != entry.get("sha256"):
            report["digests_disagreeing_with_content"].append(
                {"path": path, "key": key, "declared": entry.get("sha256"),
                 "computed": _sha256_text(value)})
        if entry.get("chars") != len(value):
            report["digests_disagreeing_with_content"].append(
                {"path": path, "key": key, "declared_chars": entry.get("chars"),
                 "computed_chars": len(value)})
    used = {k for _, k, _ in leaves}
    report["orphan_map_entries"] = sorted(set(src_map) - used)
    return report


def _copy_tree(results_root, tmp):
    dst = os.path.join(tmp, FAMILY)
    os.makedirs(os.path.join(dst, "rounds"))
    shutil.copy(os.path.join(results_root, FAMILY, "family.json"),
                os.path.join(dst, "family.json"))
    shutil.copytree(os.path.join(results_root, FAMILY, "rounds", ROUND),
                    os.path.join(dst, "rounds", ROUND))
    return os.path.join(dst, "rounds", ROUND)


def self_test(results_root, raw_root, **kw):
    """Non-vacuousness control: the checker must refuse every tampered copy."""
    variants = {
        "verdict_tampered_to_PASS": lambda d: _tamper(d, lambda v: v.update(
            {"verdict": "PASS", "performance_claimable": True})),
        "layer_flipped_to_shared": lambda d: _tamper(d, lambda v: v["failure"].update(
            {"layer": "shared-layer"})),
        "failure_class_flipped_to_a_research_result": lambda d: _tamper(
            d, lambda v: v["failure"].update({"class": "research_rejected"})),
        "yield_decision_flipped_to_continue": lambda d: _tamper(
            d, lambda v: v["yield"].update({"yield_decision": "CONTINUE"})),
        "run_id_fabricated": lambda d: _tamper(d, lambda v: v.update(
            {"run_id": ROUND + "-u1"})),
        "attempt_fabricated": lambda d: _fabricate_attempt(d),
        "required_data_available_flipped_true": lambda d: _tamper_spec(d, lambda s: s[
            "prerequisite_gate"].update({
                "required_data_available": True, "outcome": "PREREQUISITE_PRESENT",
                "missing": []})),
        "exchange_flow_matrix_item_claimed_present": lambda d: _tamper_spec(
            d, lambda s: _set_matrix_status(s, "exchange_inflow_outflow_series_hourly_or_finer",
                                            "PRESENT")),
        "onchain_dataset_claimed_available": lambda d: _tamper_spec(d, lambda s: s[
            "universe_registration"].update({
                "onchain_dataset_available": True,
                "eth_onchain_transfer_dataset_available": True})),
        "address_label_dataset_claimed_available": lambda d: _tamper_spec(d, lambda s: s[
            "universe_registration"].update({
                "exchange_address_label_dataset_available": True,
                "label_vintage_metadata_available": True})),
        "universe_shrunk_to_the_local_four_contract_list": lambda d: _tamper_spec(d, lambda s: s[
            "universe_registration"].update({
                "universe_shrunk_to_local_list": True,
                "instrument_required": ["BTC", "ETH", "BNB", "SOL"]})),
        "eth_spot_price_source_claimed_present": lambda d: _tamper_spec(d, lambda s: s[
            "universe_registration"].update({
                "price_source_available_as_registered": True,
                "price_source_available_local": "ETH/USD spot index"})),
        "probe_token_list_narrowed": lambda d: _tamper_spec(d, lambda s: s[
            "prerequisite_gate"]["measured_available"].update(
                {"probe_tokens_tested": ["inflow", "outflow"]})),
        "claimed_raw_window_edited": lambda d: _tamper_spec(d, lambda s: s[
            "prerequisite_gate"]["measured_available"].update(
                {"raw_1d_window_utc": ["2017-08-17T00:00:00+00:00", "2026-09-15T00:00:00+00:00"]})),
        "searched_axis_mislabelled_user_fixed": lambda d: _tamper_spec(
            d, lambda s: s["dca_domain"].update({"spacing_pct_status": USER_FIXED})),
        "project_constant_relabelled_user_fixed": lambda d: _tamper_spec(
            d, lambda s: s["dca_domain"].update({"base_quote_status": USER_FIXED})),
        "dca_grid_truncated_to_47": lambda d: _tamper_spec(
            d, lambda s: s["dca_domain"]["grid"].pop()),
        "coverage_claimed_complete_with_1_cohort": lambda d: _tamper(d, lambda v: v[
            "coverage"].update({"cohorts_realized": 1, "case_evaluations": 48,
                                "coverage_complete": True})),
        "survivor_bundle_fabricated": lambda d: _tamper(d, lambda v: v["survivors"].update(
            {"count": 1, "bundle": ROUND + "-survivors"})),
        "zero_attempt_claim_replaced_by_an_attempt_dir": lambda d: _fabricate_attempt(
            d, name="INCOMPLETE"),
        "verbatim_leaf_replaced_by_a_paraphrase": lambda d: _tamper_spec(
            d, lambda s: s["provenance"]["record_excerpts"].update(
                {"identity_verbatim": "# Ethereum exchange-flow drift (paraphrased)"})),
        "verbatim_digest_removed_from_the_map": lambda d: _tamper_spec(
            d, lambda s: s["excerpt_source_map"].pop("identity_verbatim", None)),
        "verbatim_source_repointed": lambda d: _tamper_spec(
            d, lambda s: s["excerpt_source_map"]["identity_verbatim"].update(
                {"source": "card"})),
        "verbatim_chars_field_falsified": lambda d: _tamper_spec(
            d, lambda s: s["excerpt_source_map"]["identity_verbatim"].update({"chars": 1})),
        "verbatim_leaf_relabelled_as_authored_prose": lambda d: _tamper_spec(
            d, lambda s: s["provenance"]["record_excerpts"].update(
                {"identity_note": s["provenance"]["record_excerpts"].pop(
                    "identity_verbatim")})),
        "verbatim_leaf_nested_in_a_list_is_still_resolved": lambda d: _tamper_spec(
            d, lambda s: s["provenance"]["record_excerpts"].update(
                {"identity_verbatim": [s["provenance"]["record_excerpts"]["identity_verbatim"],
                                       "an extra appended line that is not in the record"]})),
    }
    results = []
    ok = True
    for name, mutate in variants.items():
        tmp = tempfile.mkdtemp(prefix="xq-ethflow-prereq-selftest-")
        try:
            rdir = _copy_tree(results_root, tmp)
            mutate(rdir)
            res = run_checks(tmp, raw_root, **kw)
            refused = [c["id"] for c in res["checks"] if c["status"] == "FAIL"]
            results.append({"variant": name, "refused": bool(refused),
                            "failed_checks": refused})
            ok = ok and bool(refused)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return {"self_test": results, "variants": len(variants),
            "refused": sum(1 for r in results if r["refused"]),
            "overall": "PASS" if ok else "FAIL"}


def _kw_gz(path, row):
    with gzip.GzipFile(path, "wb", mtime=0) as gz:
        gz.write(json.dumps(row).encode() + b"\n")


def raw_fixture_control(results_root, raw_root):
    """Measurement-side non-vacuousness control.

    Build a temp raw tree that carries what this record would need: an hourly ETH
    exchange net-inflow series derived from point-in-time labelled exchange addresses
    (with a label-vintage column), a spot market holding an ETH/USD series, a second
    venue, and a daily bar older than the card's registered raw start. The raw-side
    checks must flip to FAIL.
    """
    tmp = tempfile.mkdtemp(prefix="xq-ethflow-prereq-rawfix-")
    try:
        fixture = os.path.join(tmp, "raw")
        shutil.copytree(raw_root, fixture,
                        ignore=shutil.ignore_patterns(".DS_Store", "__pycache__"))
        # the record's required dataset: hourly exchange inflow/outflow with labelled
        # exchange addresses and label-vintage metadata
        d = os.path.join(fixture, "_ref", "ethereum_exchange_flows")
        os.makedirs(d)
        _kw_gz(os.path.join(d, "eth-exchange-net-inflow-hourly.jsonl.gz"),
               {"hour_utc": "2022-01-01T00:00:00Z", "exchange_inflow_eth": "41200.5",
                "exchange_outflow_eth": "39110.2", "net_inflow_eth": "2090.3",
                "exchange_address": "0x28c6c06298d514db089934071355e5743bf21d60",
                "address_label": "Binance 14", "label_vintage": "2022-01-05"})
        # the point-in-time address-label surface is its own dataset in practice, so the
        # fixture plants it separately: without it no net-inflow series is constructible.
        _kw_gz(os.path.join(d, "eth-exchange-address-labels-with-vintage.jsonl.gz"),
               {"exchange_address": "0x28c6c06298d514db089934071355e5743bf21d60",
                "address_label": "Binance 14", "label_vintage": "2022-01-05",
                "first_seen_utc": "2018-03-11", "source": "provider-label-map"})
        # a spot market with an ETH/USD series, and a second venue
        os.makedirs(os.path.join(fixture, "okx", "swap"))
        sp = os.path.join(fixture, "binance", "spot", "ETHUSD", "1h")
        os.makedirs(sp)
        _kw_gz(os.path.join(sp, "ETHUSD-1h-2017-12.jsonl.gz"),
               {"open_time_ms": 1513296000000, "close_time_ms": 1513299599999,
                "open": "742.0", "high": "748.0", "low": "739.0", "close": "745.0",
                "volume": "15200.0"})
        # a spot instrument definition, so the instrument surface is no longer perp-only
        inst = os.path.join(fixture, "binance", "spot", "instruments")
        os.makedirs(inst)
        with open(os.path.join(inst, "spot-instruments.json"), "w", encoding="utf-8") as f:
            json.dump({"instruments": [{"fields": {"symbol": "ETHUSD", "type": "CryptoSpot",
                                                   "quote_currency": "USD",
                                                   "settlement_currency": "ETH"}}]}, f)
        # a daily bar older than the card's registered raw window start
        old = os.path.join(fixture, "binance", "usdm", "klines", "ETHUSDT", "1d",
                           "ETHUSDT-1d-2017-12.jsonl.gz")
        _kw_gz(old, {"open_time_ms": 1513296000000, "close_time_ms": 1513382399999,
                     "open": "742.0", "high": "748.0", "low": "739.0", "close": "745.0",
                     "volume": "15200.0"})
        res = run_checks(results_root, fixture)
        failed = [c["id"] for c in res["checks"] if c["status"] == "FAIL"]
        # C1/C2/C4/C5/C6/C7 are pure raw-measurement checks; C12 additionally re-asserts
        # that the persisted artifact agrees with the LIVE raw measurement (name probe,
        # window, probe token list), so a fixture that plants an exchange-flow dataset must
        # flip it too. C3 (documented row shapes) and C8-C11/C13/C14 (artifact-side) are
        # not expected to flip: the fixture adds datasets, it does not edit the store's
        # SCHEMA.md, the stored row shapes or the immutable round artifacts.
        expected = {"C1", "C2", "C4", "C5", "C6", "C7", "C12"}
        return {"raw_fixture_control": {
            "failed_checks": sorted(failed), "expected": sorted(expected),
            "why": "the fixture plants what this record needs (an hourly ETH exchange "
                   "net-inflow series with labelled exchange addresses and label-vintage "
                   "metadata, a spot ETH/USD market, a spot instrument definition, an OKX "
                   "subtree, and a 2017-12 daily bar); C1/C2/C4/C5/C6/C7 re-measure the raw "
                   "and C12 re-asserts artifact-to-live-raw agreement, so all seven must "
                   "flip. C3 (documented dataset families / stored row shapes) is not "
                   "expected to flip: the fixture does not edit the store's own SCHEMA.md.",
            "detail": {c["id"]: c["detail"] for c in res["checks"]
                       if c["id"] in expected | {"C3"}},
            "overall": res["overall"]},
            "overall": "PASS" if expected <= set(failed) else "FAIL"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


DATA_EXTS = (".csv", ".parquet", ".jsonl", ".gz", ".bin", ".feather", ".h5", ".npy", ".arrow",
             ".db", ".sqlite", ".raw", ".hdf5", ".zst", ".zip", ".tar")
DOC_EXTS = (".md", ".txt", ".rst", ".mdx", ".ipynb")
CODE_EXTS = (".py", ".ts", ".tsx", ".js", ".jsx", ".sh", ".yaml", ".yml", ".json", ".toml",
             ".cfg", ".ini", ".html", ".css", ".sql", ".rs", ".go", ".c", ".h", ".cpp")


def _dir_data_probe(path, max_depth=3, cap=4000):
    """What a hit directory actually holds. A directory with no data file beneath it is a
    stub (a downloader/README folder), not a dataset."""
    data, other, seen, truncated = [], [], 0, False
    for dp, dn, fn in os.walk(path):
        rel = os.path.relpath(dp, path)
        if rel.count(os.sep) >= max_depth:
            dn[:] = []
            continue
        dn[:] = [d for d in dn if not d.startswith(".") and d not in SCAN_SKIP_DIRS]
        for f in fn:
            seen += 1
            if seen > cap:
                truncated = True
                break
            fl = f.lower()
            (data if fl.endswith(DATA_EXTS) else other).append(os.path.join(dp, f))
        if truncated:
            break
    return {"files_seen": seen, "data_files": data[:20], "data_file_count": len(data),
            "other_file_count": len(other), "truncated": truncated}


def _classify_house_hit(path):
    """A hit is acceptable only if it is a document, code, session/log or a data-less stub.

    Never acceptable as-is: anything that could be a data store carrying the missing
    on-chain-flow surface. Those land in UNCLASSIFIED_CLASS and must be disclosed.
    """
    low = path.lower()
    base = os.path.basename(low)
    if any(k in base for k in INTEGRITY_MANIFEST_MARKERS):
        return "integrity_manifest_false_positive"
    if base in OWN_ARTIFACT_BASENAMES:
        return "own_evidence_snapshot_false_positive"
    if "/rounds/" in low and FAMILY in low:
        return "own_round_artifact_false_positive"
    if FAMILY in low and "qlib-results" in low:
        return "own_family_directory_false_positive"
    if base.endswith(CODE_EXTS) and os.path.basename(os.path.dirname(low)) == "runtime":
        return "own_checker_source_false_positive"
    if WIKI_DIR_MARKER in low and base.endswith(".md"):
        if FAMILY in low:
            return "canonical_record_document"
        return "related_wiki_document"
    if low.startswith(os.path.join(HOME, "workspace", "quant-runtime-pipeline").lower()):
        if base.endswith(DOC_EXTS):
            return "repo_documentation_false_positive"
        return "source_code_false_positive"
    if low.startswith(os.path.join(HOME, ".hermes", "sessions").lower()) \
            or low.startswith(os.path.join(HOME, ".hermes", "logs").lower()) \
            or "/transcripts/" in low or base.endswith(".log"):
        return "agent_session_or_log_false_positive"
    if low.startswith(os.path.join(HOME, ".hermes").lower()) and (
            "/cache/" in low or "/.cache/" in low or "/tmp/" in low):
        return "hermes_cache_false_positive"
    if low.startswith(os.path.join(HOME, ".hermes", "skills").lower()) \
            or low.startswith(os.path.join(HOME, ".hermes", "wiki", "_meta").lower()):
        return "hermes_skill_or_catalog_false_positive"
    if "contributors" in low or "author" in base:
        return "contributor_metadata_false_positive"
    if any(part in low for part in BACKUP_LIKE_DIRS):
        return "non_canonical_staging_copy_false_positive"
    if os.path.isdir(path):
        probe = _dir_data_probe(path)
        if probe["data_file_count"] == 0:
            return "dataset_stub_directory_false_positive"
    # A hit that is a document or a source file cannot be a market-data store, so it is
    # classified as a false positive with its own class rather than left unclassified; the
    # unclassified bucket therefore holds only hits that are neither documents, code, own
    # artifacts, caches, staging copies, nor data-less stubs.
    if base.endswith(DOC_EXTS) or base.endswith(".ipynb"):
        return "research_document_false_positive"
    if base.endswith(CODE_EXTS):
        return "source_code_false_positive"
    return UNCLASSIFIED_CLASS


BACKUP_LIKE_DIRS = ("_archived", "archive", "backup", "backups", "old")


def _probe_hits(roots, tokens):
    hits = []
    for root in roots:
        for dp, dn, fn in os.walk(root):
            rel = os.path.relpath(dp, root)
            if rel.count(os.sep) >= 6:
                dn[:] = []
                continue
            dn[:] = [d for d in dn if not d.startswith(".") and d not in SCAN_SKIP_DIRS]
            if any(t in dp.lower() for t in tokens):
                hits.append(dp)
            for f in fn:
                if any(t in f.lower() for t in tokens):
                    hits.append(os.path.join(dp, f))
    return sorted(set(hits))


def host_scan(hits_cap=400):
    """House-wide search for an ETH exchange-flow / on-chain transfer dataset (read-only).

    Two probes are run: the registered token list, and - separately and disclosed - the
    excluded tokens, so an exclusion can never hide a hit that the registered probe would
    have found.
    """
    scanned, skipped = [], []
    for r in TMP_ROOTS:
        (scanned if os.path.isdir(r) else skipped).append(r)

    records, counts = [], {}
    for p in _probe_hits(scanned, EXCHANGE_FLOW_TOKENS):
        cls = _classify_house_hit(p)
        counts[cls] = counts.get(cls, 0) + 1
        records.append({"path": p, "classification": cls})
    unclassified = [r for r in records if r["classification"] == UNCLASSIFIED_CLASS]
    listed = records[:hits_cap]

    excluded_scan = {}
    for tok in PROBE_EXCLUSIONS:
        tk = _probe_hits(scanned, [tok])
        ex_counts = {}
        for p in tk:
            cls = _classify_house_hit(p)
            ex_counts[cls] = ex_counts.get(cls, 0) + 1
        excluded_scan[tok] = {
            "hit_count": len(tk),
            "classification_counts": ex_counts,
            "unclassified_data_candidates": [p for p in tk
                                             if _classify_house_hit(p) == UNCLASSIFIED_CLASS],
            "ethusdt_named_hits": sum(1 for p in tk if "ethusdt" in os.path.basename(p).lower()),
            "examples": tk[:5],
        }
    return {"roots_scanned": scanned, "skipped_roots": skipped,
            "probe_tokens": list(EXCHANGE_FLOW_TOKENS),
            "probe_exclusions": dict(PROBE_EXCLUSIONS),
            "probe_exclusion_demonstration": {
                tok: {"house_collision_example": (excluded_scan[tok]["examples"] or [None])[0],
                      "house_hit_count": excluded_scan[tok]["hit_count"],
                      "reason": PROBE_EXCLUSIONS[tok]} for tok in PROBE_EXCLUSIONS},
            "excluded_token_scan": excluded_scan,
            "hit_count": len(records),
            "classification_counts": counts,
            "hits_listed": len(listed),
            "hits_capped_by": max(0, len(records) - len(listed)),
            "hits": listed,
            "unclassified_hits": unclassified,
            "note": ("a house-wide name probe with the same %d-token list used for the raw "
                     "tree was run over %d roots; it returned %d hit(s) in %d "
                     "classification(s), %d of them unclassified, and no hit class is a "
                     "data store. A separate disclosed pass re-ran the %d excluded token(s) "
                     "- excluded because each is a substring of a word or name the host "
                     "already uses (an ordinary English word, the venue metadata, or the "
                     "local contract name) - and returned %s hit(s) in %d classification(s), "
                     "%s of them unclassified, of which %s carry the local contract name "
                     "'ethusdt' in the file name. The canonical raw remains the only "
                     "market-data source on the host and it holds no on-chain transfer, "
                     "exchange-flow or address-label dataset."
                     % (len(EXCHANGE_FLOW_TOKENS), len(scanned), len(records), len(counts),
                        len(unclassified), len(PROBE_EXCLUSIONS),
                        sum(v["hit_count"] for v in excluded_scan.values()),
                        len({c for v in excluded_scan.values()
                             for c in v["classification_counts"]}),
                        sum(len(v["unclassified_data_candidates"])
                            for v in excluded_scan.values()),
                        sum(v["ethusdt_named_hits"] for v in excluded_scan.values())))}


def _tamper(round_dir, mutate):
    p = os.path.join(round_dir, "verdict.json")
    doc = _load_json(p)
    mutate(doc)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)


def _tamper_spec(round_dir, mutate):
    p = os.path.join(round_dir, "round-spec.json")
    doc = _load_json(p)
    mutate(doc)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)


def _set_matrix_status(spec, item, status):
    for row in spec["prerequisite_gate"]["required_data_matrix"]:
        if row.get("item") == item:
            row["status"] = status


def _fabricate_attempt(round_dir, name="DONE"):
    adir = os.path.join(round_dir, "attempts", ROUND + "-u1")
    os.makedirs(adir, exist_ok=True)
    with open(os.path.join(adir, name), "w", encoding="utf-8") as f:
        f.write("{}\n")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="prerequisite-gate read-back checker (%s)" % FAMILY)
    ap.add_argument("--results-root", default=DEFAULT_RESULTS)
    ap.add_argument("--raw-root", default=DEFAULT_RAW)
    ap.add_argument("--repo-root", default=DEFAULT_REPO)
    ap.add_argument("--card-body-file", default=None)
    ap.add_argument("--record-path", default=DEFAULT_RECORD)
    ap.add_argument("--board-db", default=DEFAULT_BOARD_DB)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--measure-only", action="store_true")
    ap.add_argument("--verify-verbatim", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--raw-fixture-control", action="store_true")
    ap.add_argument("--host-scan", action="store_true")
    args = ap.parse_args(argv)

    kw = dict(repo_root=args.repo_root, card_body_path=args.card_body_file,
              record_path=args.record_path, board_db=args.board_db)

    if args.host_scan:
        print(json.dumps(host_scan(), indent=2, ensure_ascii=False))
        return 0

    if not os.path.isdir(args.results_root) or not os.path.isdir(args.raw_root):
        print("usage error: results/raw root not mounted", file=sys.stderr)
        return 2

    if args.measure_only:
        print(json.dumps(measure_raw(args.raw_root), indent=2, ensure_ascii=False))
        return 0

    if args.verify_verbatim:
        spec_path = os.path.join(args.results_root, FAMILY, "rounds", ROUND, "round-spec.json")
        out = verify_verbatim(spec_path, **kw)
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0 if (out["misses"] == [] and out["problems"] == []
                     and out["unclassified_verbatim_paths"] == []
                     and out["missing_from_source_map"] == []
                     and out["orphan_map_entries"] == []) else 1

    if args.self_test:
        out = self_test(args.results_root, args.raw_root, **kw)
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0 if out["overall"] == "PASS" else 1

    if args.raw_fixture_control:
        out = raw_fixture_control(args.results_root, args.raw_root)
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0 if out["overall"] == "PASS" else 1

    out = run_checks(args.results_root, args.raw_root, **kw)
    if args.json:
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        for c in out["checks"]:
            print("%-4s %-4s %s" % (c["id"], c["status"], c["detail"]))
        print("overall:", out["overall"])
    return 0 if out["overall"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
