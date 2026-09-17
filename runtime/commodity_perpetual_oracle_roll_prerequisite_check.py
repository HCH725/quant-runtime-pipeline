#!/usr/bin/env python3
"""Prerequisite-gate read-back checker for the family

    commodity-perpetual-oracle-roll-funding-arbitrage-2026-09-01

Card t_a4ca5e52 terminalises this family as TECHNICAL_INCOMPLETE because the
record's required data is not in the canonical raw:

  * the record's traded objects are **crude-oil (WTI) perpetual swaps and CME
    front-month WTI futures**: "Instrument: Crude oil (WTI) perpetual swaps and
    CME front-month WTI futures." The canonical raw holds one venue's USD-M
    *crypto* perpetual klines/funding/instruments for four contracts
    (BTCUSDT/ETHUSDT/BNBUSDT/SOLUSDT). No commodity contract exists at any
    interval, depth or venue;
  * the record's venues are BitMEX (WTIUSDT), Hyperliquid (oil perps),
    Boros/Pendle Finance (funding-rate swaps) and CME (CL futures). The raw's
    only venue is BINANCE (config: venue=BINANCE, market_type=usdm_perp), so
    0 of the 4 registered venues are present;
  * the mechanism itself needs the futures curve: "Data needed: CME futures
    curve shape (front month vs next month) ... roll schedule dates;
    backwardation/contango status." A store with no futures contract, no
    contract month and no expiry calendar cannot express a curve, a roll
    window or a backwardation/contango state;
  * the record's own portability section rules out the obvious substitution:
    the mechanism "does not apply to crypto-native assets (BTC, ETH) because
    those perps track spot price baskets, not futures contracts." The four
    contracts that do exist locally are exactly those crypto-native perps, so
    running them would replace the record's mechanism, not test it;
  * the record's falsification battery needs "At least 2 years of quarterly
    roll windows for WTIUSDT on BitMEX (or comparable commodity perps)" — no
    such window exists locally, and the raw's own history (klines from
    2022-01-01) is irrelevant while the instrument is absent;
  * the on-chain funding-rate-swap expression (Boros implied APR) and the
    roll-schedule/oracle-index construction series have no local counterpart of
    any kind.

Running the local four-contract crypto perpetual panel instead would change the
record's instrument, venue, market type, mechanism and universe. The card
forbids that ("不得以近似資料、替代市場或改寫 hypothesis 硬跑" / "不得縮減 universe
以硬造可執行性"), so nothing was launched.

This script exists so an independent reader can re-derive that determination
from the live filesystem instead of trusting prose:

  * C1-C4 re-measure the canonical raw: the store identity (venue/market
    type/symbols/intervals as the store itself documents them), the instrument
    surface (ids, base/quote/settlement currencies, contract type, field
    names), the kline surface (symbol dirs, interval set, row shape, the 1d
    window and its UTC grid) and the funding surface (symbol dirs, row keys,
    venue column);
  * C5-C6 probe the whole tree at full depth by entry name and by payload
    content for the tokens a commodity/CME/roll/funding-swap dataset would have
    to carry, and assert every hit is classified;
  * C7-C9 assert the registered venue/instrument set is absent, that the six
    decisive derived objects (CME curve, roll schedule, backwardation/contango
    status, commodity funding, Boros implied APR, quarterly-roll sample) are
    not constructible from what is present, and that the store's own schema
    documents no such dataset family;
  * C10-C14 re-read the round's immutable artifacts and assert they state
    exactly the contract-mandated terminal values (verdict, layer, class, yield
    decision, zero attempts, null run_id), that nothing was ever submitted (no
    run-spec, no attempt directory, no terminal sentinel), that the registered
    universe was NOT shrunk to the locally available contracts, that the DCA
    registration still carries the contract 7.2 v1.3.1 provenance classes plus
    the complete 48-cell product, and that the coverage/survivor surface is
    empty by construction;
  * C15 re-measures the host's non-canonical market-data stores and asserts
    that none of them carries a commodity-perp / CME-curve / funding-swap
    surface that could stand in for the registered requirement (they are
    disclosed as measured-but-unused).
  * `--verify-verbatim` resolves every `*_verbatim` leaf of the persisted
    round-spec against its declared source (card / record / contract / footer)
    and re-checks the excerpt digest map, independent of the authoring run.

Read-only: it never writes inside the results tree or the raw tree. `--self-test`
builds tampered copies in fresh temp directories and asserts the checker refuses
them (non-vacuousness control). `--raw-fixture-control` builds a temp raw tree
carrying what this record would need (a BitMEX-style WTIUSDT commodity perpetual,
a CME crude-oil futures curve with front/next contract months, a Boros-style
implied-APR series and a roll schedule) and asserts the raw-side checks flip to
FAIL. `--host-scan` re-runs the house-wide search for such surfaces. Every temp
tree is removed afterwards.

Usage:
    python3 runtime/commodity_perpetual_oracle_roll_prerequisite_check.py [--json]
    python3 runtime/commodity_perpetual_oracle_roll_prerequisite_check.py --measure-only
    python3 runtime/commodity_perpetual_oracle_roll_prerequisite_check.py --verify-verbatim
    python3 runtime/commodity_perpetual_oracle_roll_prerequisite_check.py --self-test
    python3 runtime/commodity_perpetual_oracle_roll_prerequisite_check.py --raw-fixture-control
    python3 runtime/commodity_perpetual_oracle_roll_prerequisite_check.py --host-scan
    python3 runtime/commodity_perpetual_oracle_roll_prerequisite_check.py --other-stores

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

FAMILY = "commodity-perpetual-oracle-roll-funding-arbitrage-2026-09-01"
ROUND = FAMILY + "-r1"
TASK = "t_a4ca5e52"
FAMILY_TITLE = "Commodity Perpetual Oracle Roll Funding Arbitrage"
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
EXPECTED_INTERVALS = ["15m", "1d", "1h", "1w", "30m", "4h", "5m"]
KLINE_ROW_FIELDS = ["close", "close_time_ms", "high", "low", "open", "open_time_ms",
                    "volume"]
FUNDING_ROW_FIELDS = ["funding_price_source", "funding_rate", "funding_time_ms",
                      "mark_price", "market_type", "rate_type", "symbol",
                      "truth_status", "venue"]
# The record's registered venues and traded objects.
RECORD_VENUES = ["BitMEX", "Hyperliquid", "Boros/Pendle Finance", "CME"]
RECORD_VENUE_TOKENS = ["bitmex", "hyperliquid", "boros", "pendle", "cme", "coinbase",
                       "kraken", "okx", "bybit", "deribit"]
RECORD_INSTRUMENTS = ["WTIUSDT", "CL"]          # BitMEX oil perp / CME crude futures
COMMODITY_BASES = ["WTI", "CL", "BRENT", "USO", "XBR", "NG", "GAS", "XTI"]
LOCAL_BASES = ["BNB", "BTC", "ETH", "SOL"]
# The store nests to six levels (binance/usdm/klines/<SYMBOL>/<interval>/<file>). A planted
# dataset is probed to a depth the store cannot reach, so it cannot escape the probe.
PROBE_MAX_DEPTH = 8
# Token families. A dataset able to serve this record would have to carry a name from the
# FIRST group (commodity instrument / curve / roll / funding-swap vocabulary). The store's
# own four crypto contracts do not contain any of them; a planted commodity tree does.
COMMODITY_NAME_TOKENS = ("wti", "crude", "brent", "usoil", "ukousd", "xbrusd", "clfut",
                         "commodity", "energy", "gasoline", "natgas", "heating")
CURVE_NAME_TOKENS = ("front_month", "next_month", "contract_month", "futures_curve",
                     "term_structure", "backwardation", "contango", "rollover",
                     "roll_window", "roll_schedule", "expiry", "expiration")
INDEX_NAME_TOKENS = ("oracle", "index_price", "index_weights", "underlying_index")
SWAP_NAME_TOKENS = ("implied_apr", "funding_swap", "boros", "pendle", "yield_swap")
VENUE_NAME_TOKENS = ("bitmex", "hyperliquid")
PROBE_TOKENS = (COMMODITY_NAME_TOKENS + CURVE_NAME_TOKENS + INDEX_NAME_TOKENS
                + SWAP_NAME_TOKENS + VENUE_NAME_TOKENS)
# `roll` is deliberately NOT a bare token: the store's own provenance files legitimately
# carry the rollback/hash-manifest vocabulary ("rollback_sha256"), and a bare `roll`
# probe would flag integrity metadata as a market dataset. `rollback` is probed and
# classified instead.
ROLLBACK_TOKEN = "rollback"
PAYLOAD_SAMPLE_BYTES = 262144
PAYLOAD_SCAN_EXTS = (".json", ".jsonl", ".gz", ".md", ".txt", ".csv", ".tsv")

DECISIVE_REQUIRED_STATUS = {
    "required_data_1_instrument_wti_perp": "ABSENT",
    "required_data_1_instrument_cme_front_month_future": "ABSENT",
    "required_data_2_venue_bitmex_wtiusdt": "ABSENT",
    "required_data_2_venue_hyperliquid_oil_perp": "ABSENT",
    "required_data_2_venue_boros_pendle_funding_swap": "ABSENT",
    "required_data_2_venue_cme_cl_futures": "ABSENT",
    "required_data_3_market_type_commodity_perp": "ABSENT",
    "required_data_3_market_type_traditional_futures": "ABSENT",
    "required_data_3_market_type_onchain_funding_swap": "ABSENT",
    "required_data_5_cme_curve_front_vs_next": "NOT_CONSTRUCTIBLE",
    "required_data_5_bitmex_wti_funding_rates": "ABSENT",
    "required_data_5_boros_implied_apr": "ABSENT",
    "required_data_5_roll_schedule_dates": "NOT_CONSTRUCTIBLE",
    "required_data_5_backwardation_contango_status": "NOT_CONSTRUCTIBLE",
    "required_data_6_funding_settlement_timestamps_per_exchange": "PARTIAL_1_OF_4",
    "record_falsification_required_sample_quarterly_rolls": "NOT_CONSTRUCTIBLE",
}
# Statuses the record's own text fixes independently of any local measurement.
NON_MEASURED_STATUS = {
    "required_data_4_timeframe_daily_or_subdaily": "PRESENT_CRYPTO_ONLY",
    "required_data_7_source_raw_time_series": "DISCLOSURE_ONLY",
    "record_portability_exclusion_crypto_native": "DOCUMENTED",
    "record_oracle_index_construction_series": "ABSENT",
}
RECORD_EMPIRICAL_PERIOD = {
    "source_as_of": "2026-07-06",
    "cited_roll": "April 2026 (June roll)",
    "cited_period": "February-April 2026 (US-Iran war period)",
    "funding_trough_annualised_pct": -531,
    "convergence_trade_net_before_fees_usd": 170,
    "boros_implied_apr_moved_from_pct": -53.91,
    "boros_implied_apr_moved_to_pct": -15,
}
CARD_REGISTERED_RAW_WINDOW = "klines 2022-01-01\u21922026-09-11"
CARD_REGISTERED_FUNDING_WINDOW = "funding 2022-01-01T00:00Z\u21922026-09-12T08:00Z"
REGISTERED_PHASE_GRIDS = ["historical", "oos", "full", "fee_2x", "funding_2x",
                          "entry_delay_1_bar", "slippage_2ticks", "no_funding",
                          "no_funding_full", "cost_attrition_40bps"]
DCA_FOUR_AXES = ("spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct")
SEARCH_DOMAIN = "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN"
PROJECT_CONSTANT = "PROJECT_PRE_REGISTERED_CONSTANT"
USER_FIXED = "USER_FIXED"
DOCUMENTED_DATASET_SECTIONS = ("Dataset: klines", "Dataset: funding", "Dataset: instruments")
INSTRUMENT_CONTRACT_MONTH_TOKENS = ("expiry", "expiration", "contract_month", "maturity",
                                    "delivery", "front_month", "next_month", "roll")
FUNDING_SWAP_FIELD_TOKENS = ("implied", "apr", "swap", "fixed_rate", "floating",
                             "term_structure", "curve")
LIQUIDATION_FIELD_TOKENS = ("liquidation", "liq_price", "liq_", "bankruptcy",
                            "maintenance_margin", "margin_ratio")
OTHER_STORE_ROOTS = (
    "/Users/hong/workspace/phase7-alpha-research",
    "/Users/hong/workspace/phase3-portfolio-risk",
    "/Users/hong/workspace/phase4-market-microstructure",
    "/Users/hong/workspace/phase5-crypto-derivatives",
    "/Users/hong/workspace/phase9-cross-sectional-factors",
    "/Users/hong/workspace/phase12-l2-l3-execution-tca",
    "/Users/hong/workspace/phase10-pit-bitemporal",
    "/Users/hong/workspace/phase11-options-volatility",
    "/Users/hong/workspace/ml4t-real-evidence-remediation",
    "/Users/hong/workspace/a1-usdm-pit-lifecycle-20260830",
    "/Users/hong/workspace/a1-1-phase9-pit-membership-20260830",
    "/Users/hong/workspace/a1-2-prospective-pit-foundation-20260830",
    "/Users/hong/workspace/a1-3-prospective-pit-cohort-20260830",
    "/Users/hong/workspace/alpha-strategy-research",
    os.path.join(EXPANSION, "daily-crypto-brief"),
)
OTHER_STORE_PROBE_TOKENS = ("wti", "crude", "brent", "cme", "backwardation", "contango",
                            "boros", "pendle", "bitmex", "hyperliquid", "cl_futures",
                            "implied_apr")
DATA_EXTS = (".csv", ".parquet", ".jsonl", ".gz", ".bin", ".feather", ".h5", ".npy",
             ".arrow", ".tsv", ".zst", ".db", ".sqlite")
DOC_EXTS = (".md", ".txt", ".rst", ".mdx", ".ipynb")
CODE_EXTS = (".py", ".ts", ".tsx", ".js", ".jsx", ".sh", ".yaml", ".yml", ".json",
             ".toml", ".cfg", ".sql")
BACKUP_LIKE_DIRS = ("_archived", "archive", "backup", "backups", "old")
TMP_ROOTS = [os.path.join(HOME, "workspace"), EXPANSION, "/Volumes/ResearchData",
             os.path.join(HOME, ".hermes", "wiki", "quant")]
SCAN_SKIP_DIRS = ("node_modules", "__pycache__", ".git", "venvs", "site-packages",
                  ".venv", "venv", ".mypy_cache", ".pytest_cache", ".Trash",
                  "Photos Library.photoslibrary")
HOUSE_HIT_CLASSES = ("canonical_record_document", "related_wiki_document",
                     "own_evidence_snapshot", "own_round_artifact", "own_checker",
                     "handoff_body_pool", "integrity_or_rollback_metadata",
                     "results_prose", "code_reference", "other_data_store")
UNCLASSIFIED_CLASS = "unclassified_data_candidate"
OWN_ARTIFACT_BASENAMES = (FAMILY + "-prerequisite-gate-20260917.json",)
WIKI_DIR_MARKER = os.path.join(".hermes", "wiki")
ROUND_DIR_PARTS = (FAMILY, "rounds", ROUND)
HANDOFF_BODY_DIR = os.path.join("qlib-results", "_handoff", "bodies")
CLAIMED_MEASUREMENT_KEYS = (
    "klines_1d_window_utc", "raw_1d_history_days", "klines_1d_open_step_seconds",
    "klines_1d_open_grid_aligned_utc", "instrument_count", "instrument_symbols",
    "instrument_ids", "instrument_base_currencies", "instrument_quote_currencies",
    "instrument_settlement_currencies", "instrument_types", "instrument_is_inverse",
    "instrument_field_names", "instrument_contract_month_field_hits",
    "instrument_definition_files_outside_usdm",
    "instrument_definition_files_outside_usdm_explained",
    "instrument_definition_files_outside_usdm_describing_other_contracts",
    "klines_dataset_dirs",
    "klines_interval_set", "klines_row_field_set", "funding_symbol_dirs",
    "funding_row_keys", "funding_venues", "funding_row_has_mark_price",
    "raw_entry_name_count", "probe_tokens_tested", "probe_hits_in_entry_names",
    "probe_hits_unexplained", "payload_scan_files", "payload_scan_bytes",
    "payload_hits", "rollback_token_hits", "commodity_contract_present",
    "cme_curve_present", "roll_schedule_present", "boros_implied_apr_present",
    "second_venue_present", "registered_venue_overlap_count",
    "schema_dataset_sections", "schema_documents_commodity_dataset",
    "schema_sha256", "inventory_sha256", "meta_sha256", "venue", "market_type",
    "raw_markets", "binance_subdirs", "usdm_subdirs", "documented_venue",
    "documented_market_type", "documented_symbols", "documented_intervals",
    "funding_window_utc", "instrument_ids_normalized",
)


def _load_json(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return json.load(f)


def _dirs(path, skip_dotfiles=True):
    if not os.path.isdir(path):
        return []
    out = [d for d in sorted(os.listdir(path))
           if os.path.isdir(os.path.join(path, d))]
    if skip_dotfiles:
        out = [d for d in out if not d.startswith(".")]
    return out


def _all_entries(root, max_depth=PROBE_MAX_DEPTH):
    out = []
    root = os.path.abspath(root)
    base_depth = root.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in os.walk(root):
        depth = dirpath.count(os.sep) - base_depth
        if depth >= max_depth:
            dirnames[:] = []
        dirnames.sort()
        for name in sorted(dirnames):
            if name.startswith("."):
                continue
            out.append(("dir", os.path.relpath(os.path.join(dirpath, name), root),
                        os.path.join(dirpath, name)))
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            out.append(("file", os.path.relpath(os.path.join(dirpath, name), root),
                        os.path.join(dirpath, name)))
    return out


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def _sha256_text(text):
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _rows(path, limit=1):
    out = []
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
            if len(out) >= limit:
                break
    return out


def _iso(ms):
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _day_span_days(a_iso, b_iso):
    a = datetime.strptime(a_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    b = datetime.strptime(b_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return int((b - a).total_seconds() // 86400)


RAW_MEASURE_CACHE = {}
OTHER_STORES_CACHE = {}


def _probe_entry_names(root):
    entries = _all_entries(root)
    hits = {t: [] for t in PROBE_TOKENS}
    rollback = []
    for kind, rel, _full in entries:
        low = rel.lower()
        for t in PROBE_TOKENS:
            if t in low:
                hits[t].append("%s:%s" % (kind, rel))
        if ROLLBACK_TOKEN in low:
            rollback.append("%s:%s" % (kind, rel))
    return entries, hits, rollback


def _payload_scan(root, entries):
    """Bounded content scan of the store's own text payload.

    `_meta/**` is read whole (it is the store's own documentation and provenance);
    every kline/funding payload is read up to PAYLOAD_SAMPLE_BYTES, which is far
    beyond the row shape a hidden commodity series would have to appear in.
    """
    tokens = tuple(PROBE_TOKENS) + ("oil", ROLLBACK_TOKEN)
    hits = {}
    files = 0
    read_bytes = 0
    for kind, rel, full in entries:
        if kind != "file":
            continue
        ext = os.path.splitext(rel)[1].lower()
        if ext not in PAYLOAD_SCAN_EXTS:
            continue
        whole = rel.startswith("_meta" + os.sep) or "/_meta/" in full
        limit = None if whole else PAYLOAD_SAMPLE_BYTES
        try:
            if full.endswith(".gz"):
                with gzip.open(full, "rt", encoding="utf-8", errors="replace") as f:
                    txt = f.read() if limit is None else f.read(limit)
            else:
                with open(full, encoding="utf-8", errors="replace") as f:
                    txt = f.read() if limit is None else f.read(limit)
        except (OSError, EOFError, UnicodeDecodeError):
            continue
        files += 1
        read_bytes += len(txt)
        low = txt.lower()
        for t in tokens:
            if t in low:
                hits.setdefault(t, []).append(rel)
    return {"files": files, "bytes": read_bytes, "token_files": {k: sorted(set(v))
                                                                for k, v in hits.items()}}


def _norm_symbol(s):
    return "".join(ch for ch in str(s).upper() if ch.isalnum())


def measure_raw(raw, fresh=False):
    """Live measurement of the canonical raw store (read-only, cached)."""
    if not fresh and "raw" in RAW_MEASURE_CACHE:
        return RAW_MEASURE_CACHE["raw"]
    raw = os.path.abspath(raw)
    m = {"raw_root": raw}
    m["raw_markets"] = _dirs(raw)
    m["binance_subdirs"] = _dirs(os.path.join(raw, "binance"))
    m["usdm_subdirs"] = _dirs(os.path.join(raw, "binance", "usdm"))

    cfg = _load_json(os.path.join(raw, "_meta", "CONFIG.json"))
    m["documented_venue"] = cfg.get("venue")
    m["documented_market_type"] = cfg.get("market_type")
    m["documented_symbols"] = sorted(cfg.get("symbols") or [])
    m["documented_intervals"] = sorted(cfg.get("intervals") or [])
    m["documented_end_rules"] = [cfg.get("klines_end_rule"), cfg.get("funding_end_rule")]
    m["documented_updater"] = cfg.get("updater")
    m["venue"] = m["documented_venue"]
    m["market_type"] = m["documented_market_type"]

    inst_path = os.path.join(raw, "binance", "usdm", "instruments",
                             "usdm-perp-instruments.json")
    inst_doc = _load_json(inst_path)
    els = inst_doc["instruments"] if isinstance(inst_doc, dict) else inst_doc
    fields = [el.get("fields", {}) for el in els]
    m["instrument_count"] = len(fields)
    m["instrument_ids"] = sorted(str(f.get("id") or "") for f in fields)
    m["instrument_ids_normalized"] = sorted(_norm_symbol(f.get("id")) for f in fields)
    m["instrument_symbols"] = sorted(str(f.get("raw_symbol") or "") for f in fields)
    m["instrument_base_currencies"] = sorted(str(f.get("base_currency") or "") for f in fields)
    m["instrument_quote_currencies"] = sorted(set(str(f.get("quote_currency") or "")
                                                  for f in fields))
    m["instrument_settlement_currencies"] = sorted(set(
        str(f.get("settlement_currency") or "") for f in fields))
    m["instrument_types"] = sorted(set(str(f.get("type") or "") for f in fields))
    m["instrument_is_inverse"] = sorted(set(bool(f.get("is_inverse")) for f in fields))
    field_names = sorted(set(k for f in fields for k in f))
    m["instrument_field_names"] = field_names
    m["instrument_contract_month_field_hits"] = sorted(
        k for k in field_names
        if any(t in k.lower() for t in INSTRUMENT_CONTRACT_MONTH_TOKENS))
    m["instrument_liquidation_field_hits"] = sorted(
        k for k in field_names
        if any(t in k.lower() for t in LIQUIDATION_FIELD_TOKENS))
    m["instrument_definition_files_outside_usdm"] = sorted(
        rel for kind, rel, _full in _all_entries(raw)
        if kind == "file" and "instrument" in rel.lower()
        and not rel.startswith("binance/usdm/instruments"))
    # Which of those provably describe the same four crypto contracts (the store's own
    # provenance pointer, e.g. _meta/INSTRUMENTS_EXPORT.json), and which would describe a
    # contract this store does not hold (a second venue / a commodity contract)?
    explained, unexplained = [], []
    for rel in m["instrument_definition_files_outside_usdm"]:
        p = os.path.join(raw, rel)
        ids = set()
        try:
            doc = _load_json(p)
            if isinstance(doc, dict):
                ids |= {str(x) for x in (doc.get("instrument_ids") or [])}
                if isinstance(doc.get("instruments"), list):
                    for el in doc["instruments"]:
                        f = el.get("fields", el) if isinstance(el, dict) else {}
                        ids |= {str(f.get("id") or ""), str(f.get("raw_symbol") or "")}
                ids |= {str(doc.get(k) or "") for k in ("id", "raw_symbol", "symbol")}
        except (OSError, ValueError, TypeError):
            unexplained.append(rel)
            continue
        norm = {_norm_symbol(str(i).split("-")[0].split(".")[0]) for i in ids}
        norm.discard("")
        if norm and norm <= set(EXPECTED_SYMBOLS):
            explained.append(rel)
        else:
            unexplained.append(rel)
    m["instrument_definition_files_outside_usdm_explained"] = explained
    m["instrument_definition_files_outside_usdm_describing_other_contracts"] = unexplained
    m["instrument_commodity_bases"] = sorted(
        b for b in m["instrument_base_currencies"]
        if b.upper() in COMMODITY_BASES or _norm_symbol(b) in RECORD_INSTRUMENTS)

    kdir = os.path.join(raw, "binance", "usdm", "klines")
    m["klines_dataset_dirs"] = _dirs(kdir)
    intervals = set()
    for sym in m["klines_dataset_dirs"]:
        intervals |= set(_dirs(os.path.join(kdir, sym)))
    m["klines_interval_set"] = sorted(intervals)
    one_d = os.path.join(kdir, EXPECTED_SYMBOLS[1], "1d")
    day_files = sorted(f for f in os.listdir(one_d)) if os.path.isdir(one_d) else []
    m["klines_1d_files"] = day_files
    first = _rows(os.path.join(one_d, day_files[0]))[0] if day_files else {}
    m["klines_row_field_set"] = sorted(first)
    # The registered raw window's start: the store is partitioned by month, so the first
    # 1d row of the earliest month file is the store's own history start.
    start_ms = first.get("open_time_ms")
    last_files = sorted(f for sym in m["klines_dataset_dirs"]
                        for f in ([os.path.join(kdir, sym, "1d", x)
                                   for x in os.listdir(os.path.join(kdir, sym, "1d"))]
                                  if os.path.isdir(os.path.join(kdir, sym, "1d")) else []))
    rows = []
    end_ms = None
    if last_files:
        rows = _rows(last_files[-1], limit=10 ** 6)
        if rows:
            end_ms = rows[-1].get("close_time_ms")
    m["klines_1d_window_utc"] = [_iso(start_ms) if start_ms else None,
                                 _iso(end_ms) if end_ms else None]
    steps = sorted({r["open_time_ms"] - p["open_time_ms"]
                    for p, r in zip(rows[:-1], rows[1:])}) if len(rows) > 1 else []
    m["klines_1d_open_step_seconds"] = sorted({s // 1000 for s in steps})
    m["klines_1d_open_grid_aligned_utc"] = bool(
        rows and all(r["open_time_ms"] % 86400000 == 0 for r in rows))
    if m["klines_1d_window_utc"][0] and m["klines_1d_window_utc"][1]:
        m["raw_1d_history_days"] = _day_span_days(m["klines_1d_window_utc"][0],
                                                  m["klines_1d_window_utc"][1])

    fdir = os.path.join(raw, "binance", "usdm", "funding")
    m["funding_symbol_dirs"] = _dirs(fdir)
    f_sym = m["funding_symbol_dirs"][0] if m["funding_symbol_dirs"] else ""
    f_files = sorted(os.listdir(os.path.join(fdir, f_sym))) if f_sym else []
    f_path = os.path.join(fdir, f_sym, f_files[0]) if f_files else None
    f_rows = _rows(f_path, limit=10 ** 6) if f_path else []
    m["funding_row_keys"] = sorted(f_rows[0]) if f_rows else []
    m["funding_row_has_mark_price"] = bool(f_rows and "mark_price" in f_rows[0])
    m["funding_venues"] = sorted({str(r.get("venue")) for r in f_rows if "venue" in r})
    m["funding_market_types"] = sorted({str(r.get("market_type")) for r in f_rows
                                        if "market_type" in r})
    m["funding_rate_types"] = sorted({str(r.get("rate_type")) for r in f_rows
                                      if "rate_type" in r})
    m["funding_swap_field_hits"] = sorted(
        k for k in m["funding_row_keys"]
        if any(t in k.lower() for t in FUNDING_SWAP_FIELD_TOKENS))
    m["funding_window_utc"] = [
        _iso(f_rows[0]["funding_time_ms"]) if f_rows else None,
        _iso(f_rows[-1]["funding_time_ms"]) if f_rows else None]

    # ---- name probe over the whole tree, at full depth -------------------------
    entries, hits, rollback = _probe_entry_names(raw)
    m["raw_entry_name_count"] = len(entries)
    m["probe_tokens_tested"] = list(PROBE_TOKENS)
    m["probe_hits_in_entry_names"] = {t: v for t, v in hits.items() if v}
    m["rollback_token_hits"] = rollback
    m["probe_hits_unexplained"] = sorted(
        "%s:%s" % (t, p) for t, v in hits.items() if v for p in v)

    # ---- payload probe ---------------------------------------------------------
    payload = _payload_scan(raw, entries)
    m["payload_scan_files"] = payload["files"]
    m["payload_scan_bytes"] = payload["bytes"]
    m["payload_token_files"] = payload["token_files"]
    m["payload_hits"] = sorted(t for t in payload["token_files"] if t != ROLLBACK_TOKEN)
    m["payload_hits_unexplained"] = sorted(
        t for t in payload["token_files"] if t != ROLLBACK_TOKEN)

    # ---- derived objects the record needs --------------------------------------
    m["commodity_contract_present"] = bool(
        m["probe_hits_in_entry_names"].get("wti")
        or m["probe_hits_in_entry_names"].get("oil")
        or m["instrument_commodity_bases"]
        or any(_norm_symbol(s) in RECORD_INSTRUMENTS for s in m["klines_dataset_dirs"])
        or any(_norm_symbol(s) in RECORD_INSTRUMENTS for s in m["funding_symbol_dirs"])
        or [t for t in m["instrument_types"] if "Crypto" not in str(t)])
    m["cme_curve_present"] = bool(
        any(t in m["probe_hits_in_entry_names"] for t in CURVE_NAME_TOKENS)
        or m["instrument_contract_month_field_hits"]
        or "cme" in m["probe_hits_in_entry_names"]
        or _norm_symbol("cme") in m["instrument_ids_normalized"])
    m["roll_schedule_present"] = bool(
        [t for t in ("rollover", "roll_window", "roll_schedule")
         if t in m["probe_hits_in_entry_names"]])
    m["boros_implied_apr_present"] = bool(
        any(t in m["probe_hits_in_entry_names"] for t in SWAP_NAME_TOKENS)
        or m["funding_swap_field_hits"])
    m["second_venue_present"] = bool(
        [d for d in m["raw_markets"] if d not in ("_meta", "_tools", "binance")]
        or any(t in m["probe_hits_in_entry_names"] for t in VENUE_NAME_TOKENS))
    m["registered_venue_overlap_count"] = len(
        [v for v in RECORD_VENUES
         if v.lower().replace("/", " ").split()[0] in " ".join(m["raw_markets"]).lower()
         or v.lower().split("/")[0] in str(m["documented_venue"]).lower()])
    m["local_contracts_not_in_record_universe"] = [
        s for s in m["klines_dataset_dirs"]
        if _norm_symbol(s) not in RECORD_INSTRUMENTS]
    m["record_instruments_present_local"] = sorted(
        s for s in m["klines_dataset_dirs"] if _norm_symbol(s) in RECORD_INSTRUMENTS)

    # ---- store's own documentation ---------------------------------------------
    meta = os.path.join(raw, "_meta")
    schema = os.path.join(meta, "SCHEMA.md")
    inventory = os.path.join(meta, "INVENTORY.md")
    m["schema_sha256"] = _sha256_file(schema) if os.path.exists(schema) else None
    m["inventory_sha256"] = _sha256_file(inventory) if os.path.exists(inventory) else None
    m["meta_sha256"] = {f: _sha256_file(os.path.join(meta, f))
                        for f in sorted(os.listdir(meta)) if f.endswith(".json")} \
        if os.path.isdir(meta) else {}
    doc = ""
    if os.path.exists(schema):
        with open(schema, encoding="utf-8", errors="replace") as f:
            doc = f.read()
    m["schema_dataset_sections"] = [s for s in DOCUMENTED_DATASET_SECTIONS if s in doc]
    m["schema_documents_commodity_dataset"] = bool(
        any(t in doc.lower() for t in COMMODITY_NAME_TOKENS + CURVE_NAME_TOKENS
            + SWAP_NAME_TOKENS))
    m["documented_dataset_family_count"] = len(m["schema_dataset_sections"])

    # ---- required-data matrix ---------------------------------------------------
    present_commodity = m["commodity_contract_present"]
    matrix = []

    def add(key, requirement, status, basis):
        matrix.append({"item": key, "requirement": requirement, "status": status,
                       "basis": basis})

    add("required_data_1_instrument_wti_perp",
        "Instrument: crude oil (WTI) perpetual swaps.",
        "PRESENT" if present_commodity else DECISIVE_REQUIRED_STATUS[
            "required_data_1_instrument_wti_perp"],
        "name+payload probe of the whole raw tree, instrument base-currency set "
        "and contract-type set all measured live")
    add("required_data_1_instrument_cme_front_month_future",
        "Instrument: CME front-month WTI futures (CL).",
        "PRESENT" if m["cme_curve_present"] else DECISIVE_REQUIRED_STATUS[
            "required_data_1_instrument_cme_front_month_future"],
        "no futures instrument, no contract-month field, no expiry column measured")
    add("required_data_2_venue_bitmex_wtiusdt",
        "Venue: BitMEX (WTIUSDT).",
        "PRESENT" if ("bitmex" in m["probe_hits_in_entry_names"]
                      or m["second_venue_present"]) else DECISIVE_REQUIRED_STATUS[
            "required_data_2_venue_bitmex_wtiusdt"],
        "store identity is a single venue (%s) and the venue-name probe is measured live"
        % m["documented_venue"])
    add("required_data_2_venue_hyperliquid_oil_perp",
        "Venue: Hyperliquid (oil perps).",
        "PRESENT" if "hyperliquid" in m["probe_hits_in_entry_names"]
        else DECISIVE_REQUIRED_STATUS["required_data_2_venue_hyperliquid_oil_perp"],
        "venue-name probe measured live")
    add("required_data_2_venue_boros_pendle_funding_swap",
        "Venue: Boros / Pendle Finance (on-chain funding-rate swaps).",
        "PRESENT" if m["boros_implied_apr_present"] else DECISIVE_REQUIRED_STATUS[
            "required_data_2_venue_boros_pendle_funding_swap"],
        "swap-name probe and funding row field set measured live")
    add("required_data_2_venue_cme_cl_futures",
        "Venue: CME (CL futures).",
        "PRESENT" if m["cme_curve_present"] else DECISIVE_REQUIRED_STATUS[
            "required_data_2_venue_cme_cl_futures"],
        "no CME/futures surface in the store identity, names or payload")
    add("required_data_3_market_type_commodity_perp",
        "Market type: commodity perpetual futures.",
        "PRESENT" if present_commodity else DECISIVE_REQUIRED_STATUS[
            "required_data_3_market_type_commodity_perp"],
        "instrument type set and raw market-type paths measured live")
    add("required_data_3_market_type_traditional_futures",
        "Market type: traditional futures.",
        "PRESENT" if m["cme_curve_present"] else DECISIVE_REQUIRED_STATUS[
            "required_data_3_market_type_traditional_futures"],
        "raw carries no futures/dated-contract surface of any venue")
    add("required_data_3_market_type_onchain_funding_swap",
        "Market type: on-chain funding-rate swaps.",
        "PRESENT" if m["boros_implied_apr_present"] else DECISIVE_REQUIRED_STATUS[
            "required_data_3_market_type_onchain_funding_swap"],
        "no on-chain dataset of any kind in the store")
    add("required_data_4_timeframe_daily_or_subdaily",
        "Timeframe: daily or sub-daily bars.",
        NON_MEASURED_STATUS["required_data_4_timeframe_daily_or_subdaily"],
        "daily and sub-daily bars exist, but only for the four crypto-native USDT "
        "perpetuals the record's portability section excludes from this mechanism")
    add("required_data_4_roll_window_5_10_days",
        "Roll windows spanning 5-10 days on the futures calendar.",
        "PRESENT" if m["roll_schedule_present"] else "NOT_CONSTRUCTIBLE",
        "no dated futures contract exists, so no roll window can be dated")
    add("required_data_5_cme_curve_front_vs_next",
        "Data needed: CME futures curve shape (front month vs next month).",
        "PRESENT" if m["cme_curve_present"] else DECISIVE_REQUIRED_STATUS[
            "required_data_5_cme_curve_front_vs_next"],
        "no contract month, no expiry calendar, no dated contract in the store")
    add("required_data_5_bitmex_wti_funding_rates",
        "Data needed: BitMEX WTIUSDT funding rates.",
        "PRESENT" if present_commodity else DECISIVE_REQUIRED_STATUS[
            "required_data_5_bitmex_wti_funding_rates"],
        "funding symbol dirs measured live: %s" % m["funding_symbol_dirs"])
    add("required_data_5_boros_implied_apr",
        "Data needed: Boros implied APR.",
        "PRESENT" if m["boros_implied_apr_present"] else DECISIVE_REQUIRED_STATUS[
            "required_data_5_boros_implied_apr"],
        "funding row keys measured live carry no implied/fixed/floating rate field")
    add("required_data_5_roll_schedule_dates",
        "Data needed: roll schedule dates.",
        "PRESENT" if m["roll_schedule_present"] else DECISIVE_REQUIRED_STATUS[
            "required_data_5_roll_schedule_dates"],
        "no calendar/schedule dataset and no dated contract to schedule")
    add("required_data_5_backwardation_contango_status",
        "Data needed: backwardation/contango status (curve shape classification).",
        "PRESENT" if m["cme_curve_present"] else DECISIVE_REQUIRED_STATUS[
            "required_data_5_backwardation_contango_status"],
        "requires at least two dated contracts of the same underlying; none exist")
    add("required_data_6_funding_settlement_timestamps_per_exchange",
        "Timestamp requirements: funding settlement timestamps per exchange.",
        DECISIVE_REQUIRED_STATUS[
            "required_data_6_funding_settlement_timestamps_per_exchange"]
        if m["funding_venues"] != RECORD_VENUES else "PRESENT",
        "settlement timestamps are present for %s only (measured venue column), i.e. "
        "one of the four registered venues" % (m["funding_venues"] or "no venue"))
    add("required_data_7_source_raw_time_series",
        "Missing data: the source provides no raw time series, only summary figures "
        "and a worked example.",
        NON_MEASURED_STATUS["required_data_7_source_raw_time_series"],
        "a property of the record's source, not a local dataset requirement")
    add("record_falsification_required_sample_quarterly_rolls",
        "Falsification: at least 2 years of quarterly roll windows for WTIUSDT on "
        "BitMEX (or comparable commodity perps).",
        "PRESENT" if (present_commodity and m["roll_schedule_present"])
        else DECISIVE_REQUIRED_STATUS[
            "record_falsification_required_sample_quarterly_rolls"],
        "the required instrument does not exist locally, so no number of bars can "
        "cover the required sample")
    add("record_oracle_index_construction_series",
        "Mechanism input: the oracle index construction history (index price/roll "
        "weights of the commodity perpetual).",
        "PRESENT" if m["probe_hits_in_entry_names"].get("index_price")
        or m["probe_hits_in_entry_names"].get("oracle")
        else NON_MEASURED_STATUS["record_oracle_index_construction_series"],
        "no index/oracle series exists in the store; the crypto klines are trade bars")
    add("record_portability_exclusion_crypto_native",
        "Record's own portability rule: the mechanism does not apply to crypto-native "
        "assets (BTC, ETH), whose perps track spot baskets rather than futures.",
        NON_MEASURED_STATUS["record_portability_exclusion_crypto_native"],
        "the local contracts are exactly the excluded class, so substituting them "
        "would replace the mechanism rather than test it")

    m["required_data_matrix"] = matrix
    m["decisive_items"] = [i["item"] for i in matrix if i["item"] in DECISIVE_REQUIRED_STATUS]
    m["missing"] = [i["item"] for i in matrix if i["status"] != "PRESENT"]
    m["required_data_matrix_counts"] = {
        "items": len(matrix),
        "decisive": len(m["decisive_items"]),
        "absent": sum(1 for i in matrix if i["status"] == "ABSENT"),
        "not_constructible": sum(1 for i in matrix if i["status"] == "NOT_CONSTRUCTIBLE"),
        "not_evaluable": sum(1 for i in matrix if i["status"].startswith("PARTIAL")
                             or i["status"] == "NOT_EVALUABLE"),
        "present_or_partial": sum(1 for i in matrix if "PRESENT" in i["status"]),
    }
    m["required_data_available"] = all(i["status"] == "PRESENT" for i in matrix)
    RAW_MEASURE_CACHE["raw"] = m
    return m


def measure_other_stores(fresh=False):
    """Non-canonical stores on the host: measured, disclosed, never used."""
    if not fresh and "stores" in OTHER_STORES_CACHE:
        return OTHER_STORES_CACHE["stores"]
    out = {}
    for root in OTHER_STORE_ROOTS:
        entry: dict = {"exists": os.path.isdir(root)}
        if not entry["exists"]:
            out[root] = entry
            continue
        files = 0
        data_files = 0
        token_files: Counter = Counter()
        data_token_files: Counter = Counter()
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SCAN_SKIP_DIRS
                           and not d.startswith(".")]
            for name in filenames:
                if name.startswith("."):
                    continue
                files += 1
                p = os.path.join(dirpath, name)
                ext = os.path.splitext(name)[1].lower()
                try:
                    if ext == ".gz":
                        with gzip.open(p, "rt", errors="replace") as f:
                            txt = f.read(200000)
                    elif ext in (".csv", ".parquet", ".jsonl", ".json", ".txt", ".md",
                                 ".tsv", ".py", ".yaml", ".yml", ".zst"):
                        with open(p, encoding="utf-8", errors="replace") as f:
                            txt = f.read(200000)
                    else:
                        continue
                except (OSError, EOFError, UnicodeDecodeError):
                    continue
                low = txt.lower()
                for t in OTHER_STORE_PROBE_TOKENS:
                    if t in low:
                        token_files[t] += 1
                        if ext in DATA_EXTS:
                            data_token_files[t] += 1
            if files > 5000:
                break
        entry["files_scanned"] = files
        entry["token_file_counts"] = dict(token_files)
        entry["token_counts_in_data_files"] = dict(data_token_files)
        entry["carries_commodity_market_data"] = bool(data_token_files)
        out[root] = entry
    stores = {"stores": out,
              "any_store_carries_commodity_market_data": any(
                  v.get("carries_commodity_market_data") for v in out.values()),
              "note": ("measured but NOT used: the canonical raw is the only input the "
                       "card registers, so a non-canonical store could not substitute "
                       "for it without changing the record's data source")}
    OTHER_STORES_CACHE["stores"] = stores
    return stores
VERDICT_EXPECTED = "TECHNICAL_INCOMPLETE"
FAILURE_LAYER = "card-local"
FAILURE_CLASS = "data_window_invalid"
YIELD_DECISION = "STOP_TECHNICAL_INCOMPLETE"


def _check(cid, name, ok, detail):
    return {"id": cid, "name": name, "ok": bool(ok), "detail": detail}


def run_checks(results_root, raw_root, repo_root=None, card_body_path=None,
               record_path=None, board_db=None, raw=None, stores=None,
               spec_path=None, verdict_path=None, family=FAMILY, round_id=ROUND,
               task=TASK):
    raw = raw or measure_raw(raw_root)
    stores = stores if stores is not None else measure_other_stores()
    fam_dir = os.path.join(results_root, family)
    round_dir = os.path.join(fam_dir, "rounds", round_id)
    spec_path = spec_path or os.path.join(round_dir, "round-spec.json")
    verdict_path = verdict_path or os.path.join(round_dir, "verdict.json")
    checks = []

    # ---------------------------------------------------------------- C1 identity
    ok = (raw["raw_markets"] == ["_meta", "_tools", "binance"]
          and raw["binance_subdirs"] == ["usdm"]
          and raw["usdm_subdirs"] == ["funding", "instruments", "klines"]
          and raw["documented_venue"] == "BINANCE"
          and raw["documented_market_type"] == "usdm_perp"
          and raw["documented_symbols"] == EXPECTED_SYMBOLS
          and raw["documented_intervals"] == EXPECTED_INTERVALS
          and not raw["second_venue_present"])
    checks.append(_check(
        "C1", "canonical raw store identity (single venue, single market type)",
        ok, "markets=%s venue=%s market_type=%s symbols=%s intervals=%s "
            "second_venue_present=%s"
            % (raw["raw_markets"], raw["documented_venue"], raw["documented_market_type"],
               raw["documented_symbols"], raw["documented_intervals"],
               raw["second_venue_present"])))

    # ------------------------------------------------------------- C2 instruments
    ok = (raw["instrument_count"] == 4
          and raw["instrument_symbols"] == EXPECTED_SYMBOLS
          and raw["instrument_base_currencies"] == LOCAL_BASES
          and raw["instrument_types"] == ["CryptoPerpetual"]
          and raw["instrument_is_inverse"] == [False]
          and raw["instrument_quote_currencies"] == ["USDT"]
          and raw["instrument_settlement_currencies"] == ["USDT"]
          and not raw["instrument_contract_month_field_hits"]
          and not raw["instrument_commodity_bases"]
          and not raw["instrument_liquidation_field_hits"]
          and not raw["instrument_definition_files_outside_usdm_describing_other_contracts"])
    checks.append(_check(
        "C2", "instrument surface carries no commodity/futures contract",
        ok, "count=%s symbols=%s bases=%s types=%s quote=%s settle=%s inverse=%s "
            "contract_month_fields=%s commodity_bases=%s liquidation_fields=%s "
            "definition_files_outside_usdm={explained:%s, other_contracts:%s}"
            % (raw["instrument_count"], raw["instrument_symbols"],
               raw["instrument_base_currencies"], raw["instrument_types"],
               raw["instrument_quote_currencies"], raw["instrument_settlement_currencies"],
               raw["instrument_is_inverse"], raw["instrument_contract_month_field_hits"],
               raw["instrument_commodity_bases"], raw["instrument_liquidation_field_hits"],
               raw["instrument_definition_files_outside_usdm_explained"],
               raw["instrument_definition_files_outside_usdm_describing_other_contracts"])))

    # ----------------------------------------------------------------- C3 klines
    ok = (raw["klines_dataset_dirs"] == EXPECTED_SYMBOLS
          and raw["klines_interval_set"] == EXPECTED_INTERVALS
          and raw["klines_row_field_set"] == KLINE_ROW_FIELDS
          and raw["klines_1d_open_step_seconds"] == [86400]
          and raw["klines_1d_open_grid_aligned_utc"] is True
          and (raw["klines_1d_window_utc"][0] or "").startswith("2022-01-01")
          and raw["record_instruments_present_local"] == [])
    checks.append(_check(
        "C3", "kline surface is four crypto perpetual datasets and nothing dated",
        ok, "dataset_dirs=%s intervals=%s row_fields=%s 1d_window=%s step=%s "
            "grid_aligned=%s record_instruments_present=%s"
            % (raw["klines_dataset_dirs"], raw["klines_interval_set"],
               raw["klines_row_field_set"], raw["klines_1d_window_utc"],
               raw["klines_1d_open_step_seconds"], raw["klines_1d_open_grid_aligned_utc"],
               raw["record_instruments_present_local"])))

    # ---------------------------------------------------------------- C4 funding
    ok = (raw["funding_symbol_dirs"] == EXPECTED_SYMBOLS
          and raw["funding_row_keys"] == FUNDING_ROW_FIELDS
          and raw["funding_venues"] == ["BINANCE"]
          and raw["funding_row_has_mark_price"] is True
          and not raw["funding_swap_field_hits"]
          and (raw["funding_window_utc"][0] or "").startswith("2022-01-01"))
    checks.append(_check(
        "C4", "funding surface is venue funding for four crypto perps only",
        ok, "symbol_dirs=%s row_keys=%s venues=%s has_mark_price=%s swap_fields=%s "
            "window=%s"
            % (raw["funding_symbol_dirs"], raw["funding_row_keys"], raw["funding_venues"],
               raw["funding_row_has_mark_price"], raw["funding_swap_field_hits"],
               raw["funding_window_utc"])))

    # ------------------------------------------------------------ C5 name probe
    ok = (not raw["probe_hits_unexplained"] and not raw["rollback_token_hits"]
          and raw["raw_entry_name_count"] > 1500
          and len(raw["probe_tokens_tested"]) >= 30)
    checks.append(_check(
        "C5", "full-depth entry-name probe finds no commodity/curve/swap vocabulary",
        ok, "entries=%d tokens=%d unexplained_hits=%s rollback_hits=%s"
            % (raw["raw_entry_name_count"], len(raw["probe_tokens_tested"]),
               raw["probe_hits_unexplained"], raw["rollback_token_hits"])))

    # --------------------------------------------------------- C6 payload probe
    ok = (not raw["payload_hits_unexplained"]
          and raw["payload_scan_files"] > 1500
          and raw["payload_scan_bytes"] > 10 ** 7
          and raw["payload_token_files"].get(ROLLBACK_TOKEN) == ["_meta/FUNDING_EXPORT.json"])
    checks.append(_check(
        "C6", "payload probe finds no commodity/curve/swap series (rollback = integrity)",
        ok, "files=%d bytes=%d unexplained=%s rollback_hits=%s"
            % (raw["payload_scan_files"], raw["payload_scan_bytes"],
               raw["payload_hits_unexplained"],
               raw["payload_token_files"].get(ROLLBACK_TOKEN))))

    # ------------------------------------------- C7 registered venue/instrument
    ok = (raw["registered_venue_overlap_count"] == 0
          and raw["commodity_contract_present"] is False
          and raw["cme_curve_present"] is False
          and raw["roll_schedule_present"] is False
          and raw["boros_implied_apr_present"] is False
          and len(raw["local_contracts_not_in_record_universe"]) == 4)
    checks.append(_check(
        "C7", "0 of the record's registered venues/instruments exist locally",
        ok, "venue_overlap=%s commodity_contract=%s cme_curve=%s roll_schedule=%s "
            "boros_apr=%s local_contracts_not_in_record_universe=%s"
            % (raw["registered_venue_overlap_count"], raw["commodity_contract_present"],
               raw["cme_curve_present"], raw["roll_schedule_present"],
               raw["boros_implied_apr_present"],
               raw["local_contracts_not_in_record_universe"])))

    # ---------------------------------------------------- C8 constructibility
    by_item = {i["item"]: i["status"] for i in raw["required_data_matrix"]}
    pinned = [(k, by_item.get(k)) for k in DECISIVE_REQUIRED_STATUS]
    bad = [(k, got, want) for k, got, want in
           [(k, by_item.get(k), v) for k, v in DECISIVE_REQUIRED_STATUS.items()]
           if got != want]
    ok = not bad and raw["required_data_available"] is False
    checks.append(_check(
        "C8", "decisive required-data determinations match the measured statuses",
        ok, "matrix_items=%d decisive=%d mismatches=%s required_data_available=%s"
            % (len(raw["required_data_matrix"]), len(pinned), bad,
               raw["required_data_available"])))

    # ------------------------------------------------- C9 store documentation
    ok = (raw["schema_dataset_sections"] == list(DOCUMENTED_DATASET_SECTIONS)
          and raw["schema_documents_commodity_dataset"] is False
          and bool(raw["schema_sha256"]) and bool(raw["inventory_sha256"])
          and len(raw["meta_sha256"]) >= 4)
    checks.append(_check(
        "C9", "the store's own schema documents no commodity/curve/swap dataset",
        ok, "sections=%s schema_mentions_commodity=%s schema_sha256=%s meta_files=%d"
            % (raw["schema_dataset_sections"], raw["schema_documents_commodity_dataset"],
               raw["schema_sha256"], len(raw["meta_sha256"]))))

    # ------------------------------------------------- universe (measured) C10
    ok = (len(raw["local_contracts_not_in_record_universe"]) == 4
          and not raw["record_instruments_present_local"]
          and not raw["instrument_commodity_bases"])
    checks.append(_check(
        "C10", "local contract set is disjoint from the registered instrument set",
        ok, "local=%s registered_present=%s commodity_bases=%s"
            % (raw["local_contracts_not_in_record_universe"],
               raw["record_instruments_present_local"], raw["instrument_commodity_bases"])))

    # ============================ artifact-side checks (need the round files) ===
    spec = _load_json(spec_path) if os.path.exists(spec_path) else None
    verdict = _load_json(verdict_path) if os.path.exists(verdict_path) else None
    if spec is None or verdict is None:
        checks.append(_check(
            "C11", "terminal artifacts exist (round-spec.json + verdict.json)", False,
            "missing: %s" % [p for p in (spec_path, verdict_path)
                             if not os.path.exists(p)]))
        for cid, name in (("C12", "nothing was ever submitted"),
                          ("C13", "registered universe was not shrunk"),
                          ("C14", "DCA domain + provenance classes registered whole"),
                          ("C15", "coverage/survivor surface empty by construction"),
                          ("C16", "raw-side values claimed by the artifacts re-derive")):
            checks.append(_check(cid, name, False, "not evaluated: artifacts missing"))
        return _result(checks, raw, stores)

    # ------------------------------------------------- C11 terminal artifact values
    f = verdict.get("failure", {})
    y = verdict.get("yield", {})
    a = verdict.get("attempts", {})
    ok = (verdict.get("verdict") == VERDICT_EXPECTED
          and verdict.get("performance_claimable") is False
          and f.get("layer") == FAILURE_LAYER and f.get("class") == FAILURE_CLASS
          and y.get("yield_decision") == YIELD_DECISION
          and verdict.get("run_id", "MISSING") is None
          and a.get("launched") == 0 and a.get("run_specs") == 0
          and a.get("terminal_sentinels") == 0
          and verdict.get("evidence_run_ids") == []
          and spec.get("prerequisite_gate", {}).get("required_data_available") is False
          and spec.get("prerequisite_gate", {}).get("outcome") == "PREREQUISITE_ABSENT"
          and spec.get("launch", {}).get("launched") is False)
    checks.append(_check(
        "C11", "verdict.json states the contract-mandated technical terminal",
        ok, "verdict=%s claimable=%s failure=%s yield=%s run_id=%s attempts=%s "
            "evidence_run_ids=%s outcome=%s launched=%s"
            % (verdict.get("verdict"), verdict.get("performance_claimable"), f,
               y.get("yield_decision"), verdict.get("run_id"), a,
               verdict.get("evidence_run_ids"),
               spec.get("prerequisite_gate", {}).get("outcome"),
               spec.get("launch", {}).get("launched"))))

    # ------------------------------------------------------ C12 nothing submitted
    fam_listing = sorted(os.listdir(fam_dir)) if os.path.isdir(fam_dir) else []
    round_listing = sorted(os.listdir(round_dir)) if os.path.isdir(round_dir) else []
    stray = []
    for dirpath, dirnames, filenames in os.walk(fam_dir):
        for name in filenames:
            rel = os.path.relpath(os.path.join(dirpath, name), fam_dir)
            if rel not in ("family.json", os.path.join("rounds", round_id, "round-spec.json"),
                           os.path.join("rounds", round_id, "verdict.json")):
                stray.append(rel)
        for d in list(dirnames):
            if d in ("attempts", "runs", "work"):
                stray.append(os.path.relpath(os.path.join(dirpath, d), fam_dir) + "/")
    ok = (fam_listing == ["family.json", "rounds"]
          and round_listing == ["round-spec.json", "verdict.json"]
          and not stray
          and spec.get("launch", {}).get("run_spec") is None
          and spec.get("launch", {}).get("terminal_sentinel") is None
          and spec.get("launch", {}).get("attempt_dir") is None)
    checks.append(_check(
        "C12", "nothing was ever submitted: no run-spec, attempt dir or sentinel",
        ok, "family_listing=%s round_listing=%s stray=%s launch=%s"
            % (fam_listing, round_listing, stray, spec.get("launch"))))

    # ------------------------------------------------------ C13 universe discipline
    ur = spec.get("universe_registration", {})
    ok = (ur.get("universe_shrunk_to_local_list") is False
          and ur.get("registered_venue_overlap_count") == 0
          and ur.get("registered_instruments_present_local") == []
          and ur.get("declared_verdict_alternatives") == ["TECHNICAL_INCOMPLETE", "DEFERRED"]
          and int(ur.get("local_contracts_not_in_record_universe_count", -1)) == 4)
    checks.append(_check(
        "C13", "registered universe kept whole (not shrunk to the local contracts)",
        ok, "shrunk=%s venue_overlap=%s instruments_present=%s alternatives=%s "
            "local_outside_universe=%s"
            % (ur.get("universe_shrunk_to_local_list"),
               ur.get("registered_venue_overlap_count"),
               ur.get("registered_instruments_present_local"),
               ur.get("declared_verdict_alternatives"),
               ur.get("local_contracts_not_in_record_universe_count"))))

    # ------------------------------------------------------------- C14 DCA domain
    dca = spec.get("dca_domain", {})
    prod = 1
    for axis in DCA_FOUR_AXES:
        prod *= len(dca.get(axis, []))
    ok = (prod == 48 and dca.get("config_count") == 48
          and len(dca.get("grid", [])) == 48
          and dca.get("base_quote") == 1000
          and dca.get("base_quote_status") == PROJECT_CONSTANT
          and all(dca.get(a + "_status") == SEARCH_DOMAIN for a in DCA_FOUR_AXES)
          and dca.get("registration_status", {}).get("cells_computed") == 0
          and dca.get("registration_status", {}).get("cells_registered") == 48)
    checks.append(_check(
        "C14", "DCA 48-cell product + 7.2 v1.3.1 provenance classes registered whole",
        ok, "axes=%s product=%d config_count=%s grid=%d base_quote=%s/%s statuses=%s "
            "registration=%s"
            % ({a: len(dca.get(a, [])) for a in DCA_FOUR_AXES}, prod,
               dca.get("config_count"), len(dca.get("grid", [])), dca.get("base_quote"),
               dca.get("base_quote_status"),
               [dca.get(a + "_status") for a in DCA_FOUR_AXES],
               dca.get("registration_status"))))

    # --------------------------------------------- C15 coverage/survivor surface
    rb = spec.get("robustness_plan", {})
    surv = _family_survivor_surfaces(results_root, family, round_id)
    ok = (rb.get("registered_phase_grids") == REGISTERED_PHASE_GRIDS
          and rb.get("cells_computed") == 0
          and rb.get("cells_registered", 0) > 0
          and spec.get("selector_and_disposition", {}).get("survivors") == []
          and spec.get("selector_and_disposition", {}).get("cohorts_realized") == 0
          and not os.path.exists(surv["round_bundle"])
          and not os.path.exists(surv["family_survivors_dir"])
          and family not in (surv["leaderboard_entries"] or []))
    checks.append(_check(
        "C15", "coverage/survivor surface empty by construction, phase grid intact",
        ok, "phase_grids=%s cells_registered=%s cells_computed=%s survivors=%s "
            "cohorts=%s family_scoped_survivor_surfaces={bundle:%s,dir:%s,"
            "leaderboard_entries_for_family:%s}"
            % (rb.get("registered_phase_grids"), rb.get("cells_registered"),
               rb.get("cells_computed"),
               spec.get("selector_and_disposition", {}).get("survivors"),
               spec.get("selector_and_disposition", {}).get("cohorts_realized"),
               os.path.exists(surv["round_bundle"]),
               os.path.exists(surv["family_survivors_dir"]),
               [f for f in (surv["leaderboard_entries"] or []) if f == family])))

    # ------------------------------------------------- C16 claimed == re-measured
    pg = spec.get("prerequisite_gate", {})
    claimed = pg.get("measured_available", {})
    diffs = []
    for k in CLAIMED_MEASUREMENT_KEYS:
        if k not in claimed:
            diffs.append("%s: missing" % k)
        elif claimed[k] != raw.get(k):
            diffs.append("%s: claimed=%r live=%r" % (k, claimed[k], raw.get(k)))
    ok = not diffs
    checks.append(_check(
        "C16", "every raw-side value the artifacts claim equals a live re-measurement",
        ok, "keys=%d diffs=%s" % (len(CLAIMED_MEASUREMENT_KEYS), diffs[:6])))

    # ------------------------------------------------------- C17 other stores
    ok = (stores.get("any_store_carries_commodity_market_data") is False
          and bool(stores.get("stores")))
    checks.append(_check(
        "C17", "non-canonical stores carry no commodity/CME/funding-swap data",
        ok, "stores=%d any_carries=%s" % (len(stores.get("stores", {})),
                                          stores.get("any_store_carries_commodity_market_data"))))
    return _result(checks, raw, stores)


def _result(checks, raw, stores):
    return {"checks": checks,
            "pass": all(c["ok"] for c in checks),
            "failed": [c["id"] for c in checks if not c["ok"]],
            "measured": {k: raw.get(k) for k in CLAIMED_MEASUREMENT_KEYS},
            "required_data_matrix": raw["required_data_matrix"],
            "required_data_matrix_counts": raw["required_data_matrix_counts"],
            "other_stores": stores}


# --------------------------------------------------------------------------- #
# verbatim resolution
# --------------------------------------------------------------------------- #

def _resolve_source_texts(repo_root=None, card_body_path=None, record_path=None,
                          board_db=None, family=FAMILY, task=TASK):
    repo_root = repo_root or DEFAULT_REPO
    record_path = record_path or DEFAULT_RECORD
    board_db = board_db or DEFAULT_BOARD_DB
    with open(record_path, encoding="utf-8", errors="replace") as fh:
        record = fh.read()
    contract_path = os.path.join(repo_root, "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md")
    with open(contract_path, encoding="utf-8", errors="replace") as fh:
        contract = fh.read()
    footer = _LIFECYCLE_FOOTER or ""
    card = None
    if card_body_path and os.path.exists(card_body_path):
        with open(card_body_path, encoding="utf-8", errors="replace") as fh:
            card = fh.read()
    if card is None:
        con = sqlite3.connect("file:%s?mode=ro" % board_db, uri=True)
        try:
            row = con.execute("select body from tasks where id=?", (task,)).fetchone()
        finally:
            con.close()
        body = row[0] if row else ""
        if footer and body.endswith(footer):
            body = body[:-len(footer)]
        card = body
    return {"card": card, "record": record, "contract": contract, "footer": footer,
            "record_path": record_path, "contract_path": contract_path,
            "board_db": board_db}


def _walk_verbatim(node, path=(), flagged=False):
    """Collect `*_verbatim` leaves.

    The `excerpt_source_map` subtree is skipped by construction: it is the map itself
    (its entries carry source/chars/sha256/path fields keyed by verbatim leaf names), so
    walking it would flag the map's own fields as leaves.
    """
    if path and path[0] == "excerpt_source_map":
        return []
    out = []
    if isinstance(node, dict):
        for k, v in node.items():
            out.extend(_walk_verbatim(v, path + (str(k),),
                                      flagged or "verbatim" in str(k).lower()))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out.extend(_walk_verbatim(v, path + ("[%d]" % i,), flagged))
    elif isinstance(node, str) and flagged:
        out.append((".".join(path), node))
    return out


def verify_verbatim(spec_path, repo_root=None, card_body_path=None, record_path=None,
                    board_db=None, family=FAMILY, task=TASK):
    src = _resolve_source_texts(repo_root=repo_root, card_body_path=card_body_path,
                                record_path=record_path, board_db=board_db, family=family,
                                task=task)
    texts = {"card": src["card"], "record": src["record"], "contract": src["contract"],
             "footer": src["footer"]}
    with open(spec_path, encoding="utf-8", errors="replace") as fh:
        spec = json.load(fh)
    leaves = _walk_verbatim(spec)
    cmap = spec.get("excerpt_source_map", {})
    problems = []
    misses = []
    checked = 0
    for key, value in leaves:
        entry = cmap.get(key)
        if entry is None:
            problems.append("%s: no excerpt_source_map entry" % key)
            continue
        source = entry.get("source")
        if source not in texts:
            problems.append("%s: unknown source %r" % (key, source))
            continue
        if entry.get("chars") != len(value):
            problems.append("%s: chars %s != %d" % (key, entry.get("chars"), len(value)))
        if entry.get("sha256") != _sha256_text(value):
            problems.append("%s: sha256 mismatch in map" % key)
        checked += 1
        if source == "card":
            # the map's `path` may name a sub-document (e.g. the frozen card core); the
            # card text is the DB body minus the system-owned footer.
            pass
        if value not in texts[source]:
            misses.append("%s: not a substring of %s" % (key, source))
        # every multi-line excerpt is checked line by line as well, so a concatenated
        # excerpt cannot pass on a single matching line.
        if "\n" in value:
            for ln in [l for l in value.splitlines() if l.strip()]:
                if ln not in texts[source]:
                    misses.append("%s: line not in %s: %r" % (key, source, ln[:60]))
                    break
    orphans = [k for k in cmap if k not in {p for p, _v in leaves}]
    ok = not problems and not misses and not orphans and checked == len(cmap)
    return {"ok": ok, "leaves": len(leaves), "checked": checked, "misses": misses[:20],
            "orphans": orphans[:20], "problems": problems[:20],
            "sources": {k: {"chars": len(v), "sha256": _sha256_text(v)}
                        for k, v in texts.items() if v},
            "source_sha256": {"record": _sha256_file(src["record_path"]),
                              "contract": _sha256_file(src["contract_path"])}}
def _copy_tree(src, dst):
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst, symlinks=True)
    return dst


def _family_survivor_surfaces(results_root, family, round_id):
    """Survivor/leaderboard surfaces that belong to THIS family (family-scoped)."""
    out = {"family_dir": os.path.join(results_root, family),
           "family_survivors_dir": os.path.join(results_root, family, "_survivors"),
           "round_bundle": os.path.join(results_root, family, "rounds", round_id,
                                        "survivor-bundle.json"),
           "leaderboard": os.path.join(results_root, "_survivors", "leaderboard.json"),
           "leaderboard_entries": []}
    lb = out["leaderboard"]
    if os.path.exists(lb):
        try:
            doc = _load_json(lb)
            out["leaderboard_entries"] = [e.get("family_id") for e in
                                          doc.get("entries", []) or []]
        except (OSError, ValueError):
            out["leaderboard_entries"] = ["<unreadable>"]
    return out


def _fabricate_attempt(round_dir, name="DONE"):
    d = os.path.join(round_dir, "attempts", "run-001")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "run-spec.json"), "w", encoding="utf-8") as fh:
        fh.write("{}")
    with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
        fh.write("fabricated")


def self_test(results_root, raw_root, raw=None, stores=None, family=FAMILY,
              round_id=ROUND):
    """Tampered copies must be refused. Non-vacuousness control."""
    raw = raw or measure_raw(raw_root)
    stores = stores if stores is not None else measure_other_stores()
    fam_src = os.path.join(results_root, family)
    if not os.path.isdir(fam_src):
        return {"ok": False, "variants": 0, "accepted": [],
                "refused": [("", "family dir missing: %s" % fam_src)]}
    variants = []

    def v(name, expect, mutate):
        variants.append((name, expect, mutate))

    v("verdict: TECHNICAL_INCOMPLETE -> PASS", "C11",
      lambda s, ve, d: ve.update({"verdict": "PASS"}))
    v("verdict: TECHNICAL_INCOMPLETE -> DEFERRED", "C11",
      lambda s, ve, d: ve.update({"verdict": "DEFERRED"}))
    v("verdict: performance_claimable -> true", "C11",
      lambda s, ve, d: ve.update({"performance_claimable": True}))
    v("verdict: failure.layer -> shared-layer", "C11",
      lambda s, ve, d: ve["failure"].update({"layer": "shared-layer"}))
    v("verdict: failure.class -> script_bug", "C11",
      lambda s, ve, d: ve["failure"].update({"class": "script_bug"}))
    v("verdict: failure.class -> operator_stopped", "C11",
      lambda s, ve, d: ve["failure"].update({"class": "operator_stopped"}))
    v("verdict: yield_decision -> CONTINUE", "C11",
      lambda s, ve, d: ve["yield"].update({"yield_decision": "CONTINUE"}))
    v("verdict: run_id fabricated", "C11",
      lambda s, ve, d: ve.update({"run_id": "run-001"}))
    v("verdict: attempts.launched -> 1", "C11",
      lambda s, ve, d: ve["attempts"].update({"launched": 1}))
    v("verdict: attempts.run_specs -> 1", "C11",
      lambda s, ve, d: ve["attempts"].update({"run_specs": 1}))
    v("verdict: attempts.terminal_sentinels -> 1", "C11",
      lambda s, ve, d: ve["attempts"].update({"terminal_sentinels": 1}))
    v("verdict: evidence_run_ids fabricated", "C11",
      lambda s, ve, d: ve.update({"evidence_run_ids": ["run-001"]}))
    v("spec: prerequisite_gate.required_data_available -> true", "C11",
      lambda s, ve, d: s["prerequisite_gate"].update({"required_data_available": True}))
    v("spec: prerequisite_gate.outcome -> DATA_AVAILABLE", "C11",
      lambda s, ve, d: s["prerequisite_gate"].update({"outcome": "DATA_AVAILABLE"}))
    v("spec: launch.launched -> true", "C11",
      lambda s, ve, d: s["launch"].update({"launched": True}))
    v("spec: launch.run_spec fabricated", "C12",
      lambda s, ve, d: s["launch"].update({"run_spec": "run-spec.json"}))
    v("spec: launch.attempt_dir fabricated", "C12",
      lambda s, ve, d: s["launch"].update({"attempt_dir": "attempts/run-001"}))
    v("fabricated attempt directory + run-spec + DONE sentinel", "C12",
      lambda s, ve, d: _fabricate_attempt(d))
    v("spec: universe_shrunk_to_local_list -> true", "C13",
      lambda s, ve, d: s["universe_registration"].update(
          {"universe_shrunk_to_local_list": True}))
    v("spec: registered_venue_overlap_count -> 1", "C13",
      lambda s, ve, d: s["universe_registration"].update(
          {"registered_venue_overlap_count": 1}))
    v("spec: registered_instruments_present_local -> [WTIUSDT]", "C13",
      lambda s, ve, d: s["universe_registration"].update(
          {"registered_instruments_present_local": ["WTIUSDT"]}))
    v("spec: local_outside_universe count -> 3", "C13",
      lambda s, ve, d: s["universe_registration"].update(
          {"local_contracts_not_in_record_universe_count": 3}))
    v("spec: DCA grid truncated to 47 cells", "C14",
      lambda s, ve, d: s["dca_domain"].update(
          {"grid": s["dca_domain"]["grid"][:47]}))
    v("spec: DCA config_count -> 47", "C14",
      lambda s, ve, d: s["dca_domain"].update({"config_count": 47}))
    v("spec: searched axis mislabelled as USER_FIXED", "C14",
      lambda s, ve, d: s["dca_domain"].update({"spacing_pct_status": USER_FIXED}))
    v("spec: base_quote_status mislabelled as USER_FIXED", "C14",
      lambda s, ve, d: s["dca_domain"].update({"base_quote_status": USER_FIXED}))
    v("spec: DCA cells_computed claimed as 48", "C14",
      lambda s, ve, d: s["dca_domain"]["registration_status"].update(
          {"cells_computed": 48}))
    v("spec: phase grid loses one registered grid", "C15",
      lambda s, ve, d: s["robustness_plan"].update(
          {"registered_phase_grids": s["robustness_plan"]["registered_phase_grids"][:9]}))
    v("spec: coverage cells_computed claimed as non-zero", "C15",
      lambda s, ve, d: s["robustness_plan"].update({"cells_computed": 480}))
    v("spec: survivors fabricated", "C15",
      lambda s, ve, d: s["selector_and_disposition"].update({"survivors": ["x"]}))
    v("fabricated survivor-bundle.json", "C15",
      lambda s, ve, d: open(os.path.join(d, "survivor-bundle.json"), "w").write("{}"))
    v("spec: measured instrument_count claimed as 5", "C16",
      lambda s, ve, d: s["prerequisite_gate"]["measured_available"].update(
          {"instrument_count": 5}))
    v("spec: measured probe_hits_unexplained claims a hit", "C16",
      lambda s, ve, d: s["prerequisite_gate"]["measured_available"].update(
          {"probe_hits_unexplained": ["file:bitmex/wti.csv"]}))
    v("spec: measured schema_documents_commodity_dataset -> true", "C16",
      lambda s, ve, d: s["prerequisite_gate"]["measured_available"].update(
          {"schema_documents_commodity_dataset": True}))
    v("spec: measured instrument_base_currencies claims WTI", "C16",
      lambda s, ve, d: s["prerequisite_gate"]["measured_available"].update(
          {"instrument_base_currencies": ["BNB", "BTC", "ETH", "SOL", "WTI"]}))
    v("round-spec.json deleted", "C11",
      lambda s, ve, d: os.unlink(os.path.join(d, "round-spec.json")))
    v("verdict.json deleted", "C11",
      lambda s, ve, d: os.unlink(os.path.join(d, "verdict.json")))

    tmp = tempfile.mkdtemp(prefix="ck-selftest-")
    refused = []
    accepted = []
    try:
        for name, expect, mutate in variants:
            dest = os.path.join(tmp, "results")
            fam_dest = os.path.join(dest, family)
            _copy_tree(fam_src, fam_dest)
            round_dir = os.path.join(fam_dest, "rounds", round_id)
            spec_p = os.path.join(round_dir, "round-spec.json")
            verdict_p = os.path.join(round_dir, "verdict.json")
            spec = _load_json(spec_p) if os.path.exists(spec_p) else {}
            verdict = _load_json(verdict_p) if os.path.exists(verdict_p) else {}
            try:
                mutate(spec, verdict, round_dir)
            except (OSError, KeyError, TypeError) as exc:  # pragma: no cover
                refused.append([name, "mutation failed: %s" % exc])
                shutil.rmtree(dest, ignore_errors=True)
                continue
            if os.path.exists(spec_p):
                with open(spec_p, "w", encoding="utf-8") as fh:
                    json.dump(spec, fh, indent=1)
            if os.path.exists(verdict_p):
                with open(verdict_p, "w", encoding="utf-8") as fh:
                    json.dump(verdict, fh, indent=1)
            res = run_checks(dest, raw_root, raw=raw, stores=stores, family=family,
                             round_id=round_id)
            if expect in res["failed"]:
                refused.append([name, "refused (%s FAIL; failed=%s)"
                                % (expect, ",".join(res["failed"]))])
            else:
                accepted.append([name, "ACCEPTED by %s (failed=%s)"
                                 % (expect, ",".join(res["failed"]))])
            shutil.rmtree(dest, ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {"variants": len(variants), "refused": refused, "accepted": accepted,
            "ok": not accepted}


def raw_fixture_control(results_root, raw_root, family=FAMILY, round_id=ROUND,
                        raw=None, stores=None):
    """Plant what this record needs into a temp raw tree; raw-side checks must flip."""
    import io

    baseline_raw = raw or measure_raw(raw_root)
    stores = stores if stores is not None else measure_other_stores()
    tmp = tempfile.mkdtemp(prefix="ck-rawfixture-")
    tmp_raw = os.path.join(tmp, "market-data-raw")
    try:
        os.makedirs(tmp_raw)
        shutil.copytree(os.path.join(raw_root, "_meta"), os.path.join(tmp_raw, "_meta"))
        os.makedirs(os.path.join(tmp_raw, "_tools"))
        for sym in EXPECTED_SYMBOLS:
            for iv in EXPECTED_INTERVALS:
                d = os.path.join(tmp_raw, "binance", "usdm", "klines", sym, iv)
                os.makedirs(d, exist_ok=True)
                with gzip.open(os.path.join(d, "%s-%s-2022-01.jsonl.gz" % (sym, iv)),
                               "wt") as fh:
                    for i in range(3):
                        fh.write(json.dumps({
                            "open_time_ms": 1640995200000 + i * 86400000,
                            "close_time_ms": 1641081599999 + i * 86400000,
                            "open": "1.0", "high": "1.1", "low": "0.9", "close": "1.0",
                            "volume": "10.0"}) + "\n")
            fd = os.path.join(tmp_raw, "binance", "usdm", "funding", sym)
            os.makedirs(fd, exist_ok=True)
            with gzip.open(os.path.join(fd, "%s-funding.jsonl.gz" % sym), "wt") as fh:
                for i in range(3):
                    fh.write(json.dumps({
                        "symbol": sym, "venue": "BINANCE", "market_type": "usdm_perp",
                        "rate_type": "official", "truth_status": "official",
                        "funding_price_source": "mark", "mark_price": "1.0",
                        "funding_rate": "0.0001",
                        "funding_time_ms": 1640995200006 + i * 28800000}) + "\n")
        inst_dir = os.path.join(tmp_raw, "binance", "usdm", "instruments")
        os.makedirs(inst_dir, exist_ok=True)
        inst = _load_json(os.path.join(raw_root, "binance", "usdm", "instruments",
                                       "usdm-perp-instruments.json"))
        els = inst["instruments"]
        els.append({"python_type": "CommodityPerpetual", "fields": {
            "base_currency": "WTI", "id": "WTIUSDT-PERP.BITMEX", "is_inverse": False,
            "quote_currency": "USDT", "raw_symbol": "WTIUSDT", "settlement_currency": "USDT",
            "contract_month": "2026-06", "expiry": "2026-06-20", "type": "CommodityPerpetual"}})
        with open(os.path.join(inst_dir, "usdm-perp-instruments.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(inst, fh, indent=1)

        # planted commodity-perp venue, CME futures curve, roll schedule, Boros APR
        bd = os.path.join(tmp_raw, "bitmex", "usdm", "funding", "WTIUSDT")
        os.makedirs(bd, exist_ok=True)
        with gzip.open(os.path.join(bd, "WTIUSDT-funding.jsonl.gz"), "wt") as fh:
            for i in range(3):
                fh.write(json.dumps({"symbol": "WTIUSDT", "venue": "BITMEX",
                                     "funding_rate": "-0.05", "mark_price": "95.0",
                                     "funding_time_ms": 1775000000000 + i * 28800000}) + "\n")
        # ... and the same contract smuggled into the canonical store's own layout, so the
        # in-store surface checks (C3/C4) are exercised too, not only the venue-level ones.
        for iv in EXPECTED_INTERVALS:
            d = os.path.join(tmp_raw, "binance", "usdm", "klines", "WTIUSDT", iv)
            os.makedirs(d, exist_ok=True)
            with gzip.open(os.path.join(d, "WTIUSDT-%s-2026-04.jsonl.gz" % iv), "wt") as fh:
                for i in range(3):
                    fh.write(json.dumps({
                        "open_time_ms": 1775000000000 + i * 86400000,
                        "close_time_ms": 1775086399999 + i * 86400000,
                        "open": "95.14", "high": "95.20", "low": "94.90",
                        "close": "95.00", "volume": "100.0"}) + "\n")
        wfd = os.path.join(tmp_raw, "binance", "usdm", "funding", "WTIUSDT")
        os.makedirs(wfd, exist_ok=True)
        with gzip.open(os.path.join(wfd, "WTIUSDT-funding.jsonl.gz"), "wt") as fh:
            for i in range(3):
                fh.write(json.dumps({
                    "symbol": "WTIUSDT", "venue": "BINANCE", "market_type": "usdm_perp",
                    "rate_type": "official", "truth_status": "official",
                    "funding_price_source": "mark_price", "mark_price": "95.0",
                    "funding_rate": "-0.05",
                    "funding_time_ms": 1775000000006 + i * 28800000}) + "\n")
        cd = os.path.join(tmp_raw, "cme", "cl", "futures_curve")
        os.makedirs(cd, exist_ok=True)
        with open(os.path.join(cd, "CL-front_month-vs-next_month-2026-04.csv"), "w",
                  encoding="utf-8") as fh:
            fh.write("date,contract_month,front_month_price,next_month_price,"
                     "backwardation,contango\n2026-04-01,2026-06,95.14,93.10,1,0\n")
        with open(os.path.join(tmp_raw, "cme", "roll_schedule", "roll-schedule-2026.csv")
                  if False else os.path.join(tmp_raw, "cme", "roll_schedule.csv"), "w",
                  encoding="utf-8") as fh:
            fh.write("contract_month,roll_start,roll_end,weight_shift_per_day\n"
                     "2026-06,2026-04-01,2026-04-05,0.2\n")
        bd2 = os.path.join(tmp_raw, "boros", "implied_apr")
        os.makedirs(bd2, exist_ok=True)
        with open(os.path.join(bd2, "boros-implied-apr-oil.csv"), "w", encoding="utf-8") as fh:
            fh.write("date,implied_apr,underlying_index\n2026-04-01,-0.5391,WTI\n")
        with io.open(os.path.join(tmp_raw, "cme", "README.md"), "w", encoding="utf-8") as fh:
            fh.write("CME CL crude oil futures curve sample for the fixture control.\n")

        fx_raw = measure_raw(tmp_raw, fresh=True)
        res = run_checks(results_root, tmp_raw, raw=fx_raw, stores=stores, family=family,
                         round_id=round_id)
        flipped = set(res["failed"])
        expected = {"C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8", "C10"}
        must_not_flip = {"C11", "C12", "C13", "C14", "C15", "C17"}
        missing = sorted(expected - flipped)
        unexpected = sorted(must_not_flip & flipped)
        ok = not missing and not unexpected
        return {"ok": ok, "expected_flips": sorted(expected),
                "actual_flips": sorted(flipped), "missing_flips": missing,
                "unexpected_flips": unexpected,
                "fixture": {"commodity_contract_present":
                            fx_raw["commodity_contract_present"],
                            "cme_curve_present": fx_raw["cme_curve_present"],
                            "roll_schedule_present": fx_raw["roll_schedule_present"],
                            "boros_implied_apr_present": fx_raw["boros_implied_apr_present"],
                            "probe_hits": fx_raw["probe_hits_in_entry_names"],
                            "payload_hits": fx_raw["payload_hits"],
                            "required_data_available": fx_raw["required_data_available"]},
                "baseline_required_data_available": baseline_raw["required_data_available"]}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------- #
# house-wide scan
# --------------------------------------------------------------------------- #

HOUSE_SCAN_TOKENS = ("wti", "crude", "brent", "cme", "backwardation", "contango",
                     "boros", "pendle", "bitmex", "hyperliquid", "implied_apr",
                     "commodity_perpetual")


def _classify_house_hit(path):
    low = path.lower()
    base = os.path.basename(path)
    if WIKI_DIR_MARKER in path:
        return ("canonical_record_document" if base == FAMILY + ".md"
                else "related_wiki_document")
    if base in OWN_ARTIFACT_BASENAMES or "/evidence/" in path:
        return "own_evidence_snapshot"
    if "/" + "/".join(ROUND_DIR_PARTS[:1]) + "/" in path and "/rounds/" in path:
        return "own_round_artifact"
    if base.endswith("_prerequisite_check.py") or "prerequisite_check" in base:
        return "own_checker"
    if HANDOFF_BODY_DIR in path:
        return "handoff_body_pool"
    if "rollback" in base.lower() or "manifest" in base.lower() or "hash" in base.lower():
        return "integrity_or_rollback_metadata"
    ext = os.path.splitext(path)[1].lower()
    if ext in CODE_EXTS:
        return "code_reference"
    if ext in DATA_EXTS:
        return "other_data_store"
    if ext in DOC_EXTS or base.endswith(".html"):
        if "/workspace/" in path or "qlib-results" in path or "daily-crypto-brief" in path:
            return "results_prose"
        return "related_wiki_document"
    if any(d in low for d in BACKUP_LIKE_DIRS):
        return "integrity_or_rollback_metadata"
    return UNCLASSIFIED_CLASS


def host_scan(roots=None, tokens=None, max_files=400000, max_hits=2000):
    roots = roots or TMP_ROOTS
    tokens = tokens or HOUSE_SCAN_TOKENS
    hits = []
    counts = Counter()
    scanned = 0
    for root in roots:
        if not os.path.isdir(root):
            continue
        base_depth = os.path.abspath(root).rstrip(os.sep).count(os.sep)
        for dirpath, dirnames, filenames in os.walk(root):
            depth = dirpath.count(os.sep) - base_depth
            dirnames[:] = [d for d in dirnames
                           if d not in SCAN_SKIP_DIRS and not d.startswith(".")
                           and not (depth >= 8 and d in ("qlib-results", "market-data-raw"))]
            for name in filenames:
                if name.startswith("."):
                    continue
                scanned += 1
                if scanned > max_files or len(hits) >= max_hits:
                    break
                p = os.path.join(dirpath, name)
                ext = os.path.splitext(name)[1].lower()
                if ext not in (".md", ".txt", ".py", ".json", ".jsonl", ".csv", ".yaml",
                               ".yml", ".tsv", ".html", ".rst", ".gz"):
                    continue
                try:
                    if os.path.getsize(p) > 4 << 20:
                        continue
                    if ext == ".gz":
                        with gzip.open(p, "rt", errors="replace") as fh:
                            txt = fh.read(200000)
                    else:
                        with open(p, encoding="utf-8", errors="replace") as fh:
                            txt = fh.read(200000)
                except (OSError, EOFError, UnicodeDecodeError):
                    continue
                low = txt.lower()
                matched = [t for t in tokens if t in low]
                if matched:
                    cls = _classify_house_hit(p)
                    hits.append({"path": p, "class": cls, "tokens": matched})
                    counts[cls] += 1
            if scanned > max_files or len(hits) >= max_hits:
                break
    unclassified = [h for h in hits if h["class"] == UNCLASSIFIED_CLASS]
    return {"roots": list(roots), "tokens": list(tokens), "files_scanned": scanned,
            "hits": hits, "class_counts": dict(counts),
            "unclassified": unclassified, "ok": not unclassified}


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def _print_checks(res):
    for c in res["checks"]:
        print("%-4s %-6s %s" % (c["id"], "PASS" if c["ok"] else "FAIL", c["name"]))
        print("        %s" % c["detail"])
    print("\n%s (failed: %s)"
          % ("ALL CHECKS PASS" if res["pass"] else "CHECKS FAILED",
             ",".join(res["failed"]) or "none"))


def main(argv=None):
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    ap.add_argument("--measure-only", action="store_true")
    ap.add_argument("--verify-verbatim", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--raw-fixture-control", action="store_true")
    ap.add_argument("--host-scan", action="store_true")
    ap.add_argument("--other-stores", action="store_true")
    ap.add_argument("--results-root", default=DEFAULT_RESULTS)
    ap.add_argument("--raw-root", default=DEFAULT_RAW)
    ap.add_argument("--repo-root", default=DEFAULT_REPO)
    ap.add_argument("--record", default=DEFAULT_RECORD)
    ap.add_argument("--board-db", default=DEFAULT_BOARD_DB)
    args = ap.parse_args(argv)

    if args.measure_only:
        m = measure_raw(args.raw_root, fresh=True)
        out = {k: v for k, v in m.items() if k != "payload_token_files"}
        out["payload_token_files"] = m["payload_token_files"]
        print(json.dumps(out, indent=1, ensure_ascii=False) if args.json
              else "\n".join("%s: %s" % (k, json.dumps(m[k], ensure_ascii=False))
                             for k in sorted(m) if k != "required_data_matrix"))
        return 0
    if args.other_stores:
        st = measure_other_stores(fresh=True)
        print(json.dumps(st, indent=1, ensure_ascii=False) if args.json
              else "\n".join("%s: %s" % (k, json.dumps(v, ensure_ascii=False))
                             for k, v in sorted(st["stores"].items())))
        return 0 if not st["any_store_carries_commodity_market_data"] else 1
    if args.host_scan:
        scan = host_scan()
        print(json.dumps(scan, indent=1, ensure_ascii=False) if args.json
              else "files_scanned=%d hits=%d classes=%s unclassified=%d"
                   % (scan["files_scanned"], len(scan["hits"]),
                      json.dumps(scan["class_counts"], ensure_ascii=False),
                      len(scan["unclassified"])))
        return 0 if scan["ok"] else 1
    if args.verify_verbatim:
        out = verify_verbatim(os.path.join(args.results_root, FAMILY, "rounds", ROUND,
                                           "round-spec.json"),
                              repo_root=args.repo_root, record_path=args.record,
                              board_db=args.board_db)
        print(json.dumps(out, indent=1, ensure_ascii=False) if args.json
              else "leaves=%d checked=%d misses=%d orphans=%d problems=%d\n%s"
                   % (out["leaves"], out["checked"], len(out["misses"]),
                      len(out["orphans"]), len(out["problems"]),
                      json.dumps({"misses": out["misses"], "orphans": out["orphans"],
                                  "problems": out["problems"]}, ensure_ascii=False)))
        return 0 if out["ok"] else 1
    if args.self_test:
        raw = measure_raw(args.raw_root, fresh=True)
        stores = measure_other_stores(fresh=True)
        out = self_test(args.results_root, args.raw_root, raw=raw, stores=stores)
        print(json.dumps(out, indent=1, ensure_ascii=False))
        return 0 if out["ok"] else 1
    if args.raw_fixture_control:
        raw = measure_raw(args.raw_root, fresh=True)
        stores = measure_other_stores(fresh=True)
        out = raw_fixture_control(args.results_root, args.raw_root, raw=raw, stores=stores)
        print(json.dumps(out, indent=1, ensure_ascii=False))
        return 0 if out["ok"] else 1
    raw = measure_raw(args.raw_root)
    stores = measure_other_stores()
    res = run_checks(args.results_root, args.raw_root, repo_root=args.repo_root,
                     record_path=args.record, board_db=args.board_db, raw=raw, stores=stores)
    if args.json:
        print(json.dumps(res, indent=1, ensure_ascii=False))
    else:
        _print_checks(res)
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
