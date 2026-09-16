#!/usr/bin/env python3
"""Prerequisite-gate read-back checker for the family

    bitcoin-onchain-rhodl-ratio-macro-cycle-2026-08-31

Card t_010232b7 terminalised this family as TECHNICAL_INCOMPLETE because the
record's required data is not in the canonical raw:

  * the record's registered universe is the *Bitcoin spot on-chain ledger* - a
    full archival Bitcoin node parsing UTXO creation dates, block heights,
    output values and spend timestamps. The canonical raw holds only BINANCE
    USD-M perpetual klines/funding/instruments for four contracts: there is no
    blockchain/UTXO dataset of any kind, so the realized-value sums
    RC_1w (age 1d..7d) and RC_1y-2y (age 365d..730d) that ARE the RHODL signal
    cannot be formed. The missing prerequisite is the signal's own independent
    variable, not an auxiliary filter;
  * the registered price reference is a "Daily BTC/USD volume-weighted spot
    index" aligned to daily 00:00 UTC block timestamps; locally the only BTC
    series is a USDT-quoted USD-M perpetual at one derivatives venue, and the
    realized-value definition needs P_creation(u) - the BTC/USD price on the day
    each UTXO was minted - which reaches back to 2009-01-03, long before the
    local window;
  * the point-in-time requirement (daily UTC closure snapshot of the confirmed
    UTXO set, mempool excluded) has no data substrate at all;
  * the record's evidence base and falsification battery span the 2011, 2013,
    2015, 2017-18 and 2021 cycles (the RHODL construction multiplies by
    "days since 2009-01-03"), while the raw's own daily history starts
    2022-01-01 - about 1.18 four-year cycles.

Running the local four-contract perpetual panel instead would change the
record's decision variable, its market source, its sample and its "market age"
multiplier. The card forbids that ("不得以近似資料、替代市場或改寫 hypothesis
硬跑" / "不得縮減 universe 以硬造可執行性"), so nothing was launched.

This script exists so an independent reader can re-derive that determination
from the live filesystem instead of trusting prose:

  * C1-C6 re-measure the canonical raw: the market-directory set, the instrument
    surface, the decisive absence of any on-chain/UTXO dataset (entry-name probe
    over the whole tree at full depth + the stored row shapes + the store's own
    documented dataset families + the store's own inventory of un-migrated
    stores), the absence of the registered BTC/USD spot price source, the
    non-constructibility of the RC bands / market-age series, and the raw's own
    daily window versus the record's required sample;
  * C7-C12 re-read the round's immutable artifacts and assert they state exactly
    the contract-mandated terminal values (verdict, layer, class, yield
    decision, zero attempts, null run_id), that the registered requirement was
    NOT shrunk to the locally available instruments, that nothing was ever
    submitted (no attempt directory, no terminal sentinel) and that the DCA
    registration still carries the contract 7.2 v1.3.1 provenance classes plus
    the complete 48-cell product;
  * C13 resolves every `*_verbatim` leaf of the persisted round-spec against its
    declared source (card / record / contract / footer) and re-checks the
    excerpt digest map - independent of the authoring run.

Read-only: it never writes inside the results tree or the raw tree. `--self-test`
builds tampered copies in fresh temp directories and asserts the checker refuses
them (non-vacuousness control). `--raw-fixture-control` builds a temp raw tree
carrying what this record would need (a Bitcoin UTXO/realized-cap age-band
dataset, a spot market with a BTC/USD daily series, a second venue, and a
pre-2022 daily bar) and asserts the raw-side checks flip to FAIL. `--host-scan`
re-runs the house-wide search for an on-chain dataset. Every temp tree is
removed afterwards.

Usage:
    python3 runtime/bitcoin_onchain_rhodl_ratio_prerequisite_check.py [--json]
    python3 runtime/bitcoin_onchain_rhodl_ratio_prerequisite_check.py --measure-only
    python3 runtime/bitcoin_onchain_rhodl_ratio_prerequisite_check.py --verify-verbatim
    python3 runtime/bitcoin_onchain_rhodl_ratio_prerequisite_check.py --self-test
    python3 runtime/bitcoin_onchain_rhodl_ratio_prerequisite_check.py --raw-fixture-control
    python3 runtime/bitcoin_onchain_rhodl_ratio_prerequisite_check.py --host-scan

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

# Provenance classes and the cartesian-product helper are reused from the
# existing registered validator rather than re-implemented here (contract 7.2 v1.3.1).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import strategy_a_v2_counts as _sav2
except ImportError:  # pragma: no cover - only if the sibling module is missing
    _sav2 = None
try:
    from production_handoff import LIFECYCLE_FOOTER as _LIFECYCLE_FOOTER
except ImportError:  # pragma: no cover - only if the sibling module is missing
    _LIFECYCLE_FOOTER = None

FAMILY = "bitcoin-onchain-rhodl-ratio-macro-cycle-2026-08-31"
ROUND = FAMILY + "-r1"
TASK = "t_010232b7"
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
# The store nests to six levels (binance/usdm/klines/<SYMBOL>/<interval>/<file>), so the
# entry-name probe walks deeper than the store can nest: a dataset cannot escape the
# probe by sitting deep in the tree.
PROBE_MAX_DEPTH = 8
# Name tokens that would have to exist for this record's required dataset (Bitcoin
# on-chain UTXO / realized-cap HODL-wave data). One unified list is used by the raw
# entry-name probe, the house-wide scan and the checker's own re-measurement; the two
# documents can therefore never disagree about what the probe returns.
ONCHAIN_TOKENS = ("rhodl", "hodl", "hodl_waves", "hodlwaves", "realized_cap", "realised_cap",
                  "realizedcap", "realized_value", "rc_1w", "rc_1y", "age_band", "ageband",
                  "coin_age", "coinage", "coindays", "coin_days", "utxo", "unspent",
                  "spent_output", "spentoutput", "onchain", "on-chain", "on_chain",
                  "blockchain", "mempool", "block_height", "blockheight", "genesis",
                  "sopr", "mvrv", "nupl", "thermocap", "puell", "reserve_risk",
                  "difficulty", "hashrate", "hash_rate", "mining", "block_reward",
                  "blockreward", "coinmetrics", "coin_metrics", "kaiko", "coinmarketcap",
                  "glassnode", "lookintobitcoin", "positivecrypto", "chainalysis", "dune",
                  "cryptoquant", "bitcoin_node", "bitcoind")
# Tokens deliberately NOT probed, with the reason. A token that is a substring of a word
# the host already uses can only ever return guaranteed false positives; excluding it is a
# probe-design decision, and every exclusion is demonstrated by a measurement (see
# `probe_exclusion_demonstration` / `excluded_token_scan` in the measurement output) - a
# generic token is not silently dropped.
PROBE_EXCLUSIONS = {
    "lth": "substring of the ordinary word 'health' (~/.hermes/hermes-agent/**/*health*.py "
           "and the archive copy of the same tree)",
    "sth": "substring of the ordinary word 'health' (same class as 'lth')",
    "miner": "substring of the contributor handle 'minervini' "
             "(contributors/emails/p.minervini@gmail.com)",
    "supply": "generic word: matches 'supply chain'/'power supply' prose and archive "
              "source trees, not an on-chain supply surface",
    "btcusd": "substring of the local contract name 'btcusdt' (and of every "
              "'btcusdt-<interval>-<month>.jsonl.gz' file)",
    "btc_usd": "same class as 'btcusd': every local BTC artefact is 'btcusdt'-named",
    "address": "generic word: matches mail/address-list artefacts across the host, not the "
               "UTXO address surface",
    "index": "generic word: matches web/JSON index files house-wide, not an on-chain index",
}
INSTRUMENT_ONCHAIN_FIELDS = ("utxo", "onchain", "on-chain", "realized", "hodl", "age_band",
                             "supply", "block", "chain")
DOCUMENTED_DATASET_FAMILIES = ["funding", "instruments",
                               "klines (Binance USD-M perpetual futures, UTC)"]
# the decisive registered data items and the exact status each must carry
DECISIVE_REQUIRED_STATUS = {
    "universe_bitcoin_spot_onchain_ledger": "ABSENT_AS_REGISTERED_SOURCE",
    "archival_bitcoin_node_utxo_parse": "ABSENT",
    "price_reference_btc_usd_volume_weighted_spot_index": "ABSENT_AS_REGISTERED_SOURCE",
    "derived_rc_band_and_market_age_series": "NOT_CONSTRUCTIBLE",
    "point_in_time_utxo_snapshot_no_mempool": "ABSENT",
    "rhodl_ratio_series_since_genesis": "NOT_CONSTRUCTIBLE",
}
MISSING_DATA_MATRIX_ITEMS = (
    "universe_bitcoin_spot_onchain_ledger", "archival_bitcoin_node_utxo_parse",
    "instrument_bitcoin", "timeframe_daily",
    "price_reference_btc_usd_volume_weighted_spot_index",
    "derived_rc_band_and_market_age_series", "point_in_time_utxo_snapshot_no_mempool",
    "rhodl_ratio_series_since_genesis", "rhodl_accumulation_episodes_threshold_350",
    "rhodl_distribution_episodes_threshold_15000",
    "falsification_item_1_accumulation_forward_returns_vs_buy_and_hold",
    "falsification_item_2_blowoff_top_above_historical_median",
    "falsification_item_3_random_age_band_replacement_control",
    "falsification_item_4_oos_cycle_band_divergence",
    "historical_cycle_evidence_base_2011_2022", "btc_usd_creation_price_since_2009",
    "baseline_buy_and_hold", "market_type_venue_execution_timing",
    "transaction_cost_convention")
DECISIVE_MATRIX_ITEMS = tuple(DECISIVE_REQUIRED_STATUS)
# The raw's own daily window as registered on the card.
CARD_REGISTERED_RAW_WINDOW_START = "2022-01-01"
CARD_REGISTERED_RAW_WINDOW = "klines 2022-01-01\u21922026-09-11"
CARD_REGISTERED_FUNDING_WINDOW = "funding 2022-01-01T00:00Z\u21922026-09-12T08:00Z"
# The record multiplies the ratio by "Market Age (days) = Date - 2009-01-03" and its
# evidence base spans the 2011/2013/2015/2017-18/2021 cycles.
GENESIS_DATE = "2009-01-03"
CYCLE_DAYS = 1461  # 4 years, the halving-cycle length the record's evidence is built on
OTHER_MARKET_NAMES = ("spot", "margin", "options", "inverse", "coinm", "delivery",
                      "futures", "quarter", "index")
INVARIANT_TOKENS = ("30,000", "numeraire", "leverage 10x", "12 tranches", "tranche #12",
                    "reduce-only", "flat/kill")
TMP_ROOTS = [os.path.join(HOME, "workspace"), EXPANSION, "/Volumes/ResearchData",
             os.path.join(HOME, ".hermes"),
             os.path.join(HOME, "workspace", "qlib-apple-container")]
SCAN_SKIP_DIRS = ("node_modules", "__pycache__", ".git", "venvs", "site-packages", ".venv",
                  "Photos Library.photoslibrary")
BACKUP_LIKE_DIRS = ("_archived", "archive", "backup", "backups", "old")
# Classification of every house-wide hit. A hit is acceptable only if it is a document,
# code, session/log/cache artefact, our own provenance artefact, or a directory with no
# data file beneath it - never a data store that the raw tree lacks.
HOUSE_HIT_CLASSES = ("canonical_record_document", "related_wiki_document",
                     "own_evidence_snapshot_false_positive", "own_round_artifact_false_positive",
                     "own_family_directory_false_positive", "own_checker_source_false_positive",
                     "repo_documentation_false_positive", "source_code_false_positive",
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
    m["klines_interval_set"] = _dirs(os.path.join(kl, "BTCUSDT"))
    d1 = os.path.join(kl, "BTCUSDT", "1d")
    files1 = sorted(f for f in os.listdir(d1) if f.endswith(".jsonl.gz")) \
        if os.path.isdir(d1) else []
    if files1:
        r_first = _rows(os.path.join(d1, files1[0]))
        r_last = _rows(os.path.join(d1, files1[-1]))
        m["klines_row_field_set"] = sorted(r_last[-1].keys())
        m["klines_1d_first_open_utc"] = _iso(r_first[0]["open_time_ms"])
        m["klines_1d_last_open_utc"] = _iso(r_last[-1]["open_time_ms"])
        m["klines_1d_first_open_mod_86400_seconds"] = [
            r["open_time_ms"] % 86400000 // 1000 for r in r_first[:3]]
        steps = set()
        for f in files1:
            rr = _rows(os.path.join(d1, f))
            for i in range(len(rr) - 1):
                steps.add((rr[i + 1]["open_time_ms"] - rr[i]["open_time_ms"]) // 1000)
        m["klines_1d_open_step_seconds"] = sorted(steps)
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
        m["cycles_covered_4y_equivalent"] = round(m["raw_1d_history_days"] / CYCLE_DAYS, 3)
    m["raw_1d_window_reaches_back_to_genesis"] = bool(
        window and window[0][:10] <= GENESIS_DATE)

    dw = os.path.join(kl, "BTCUSDT", "1w")
    fw = sorted(f for f in os.listdir(dw) if f.endswith(".jsonl.gz")) \
        if os.path.isdir(dw) else []
    if fw:
        wo = _rows(os.path.join(dw, fw[0]))[0]["open_time_ms"]
        m["klines_1w_first_open_utc"] = _iso(wo)
        m["klines_1w_first_open_weekday"] = datetime.fromtimestamp(
            wo / 1000, tz=timezone.utc).strftime("%a")

    fu = os.path.join(ud, "funding")
    m["funding_symbol_dirs"] = _dirs(fu)
    p = os.path.join(fu, "BTCUSDT", "BTCUSDT-funding.jsonl.gz")
    if os.path.exists(p):
        rs = _rows(p)
        m["funding_row_keys"] = sorted(rs[-1].keys())
        m["funding_venues"] = sorted({str(r.get("venue")) for r in rs})
        m["funding_first_ms"] = _iso(rs[0]["funding_time_ms"])
        m["funding_last_ms"] = _iso(rs[-1]["funding_time_ms"])
    m["funding_row_has_onchain_field"] = any(
        t in k.lower() for k in (m.get("funding_row_keys") or []) for t in ONCHAIN_TOKENS)

    inst_path = os.path.join(ud, "instruments", "usdm-perp-instruments.json")
    inst = _load_json(inst_path).get("instruments", []) if os.path.exists(inst_path) else []
    m["instrument_count"] = len(inst)
    m["instrument_types"] = sorted({i["fields"].get("type") for i in inst})
    m["instrument_quote_currencies"] = sorted({i["fields"].get("quote_currency")
                                               for i in inst
                                               if i["fields"].get("quote_currency")})
    m["instrument_settlement_currencies"] = sorted({i["fields"].get("settlement_currency")
                                                    for i in inst
                                                    if i["fields"].get("settlement_currency")})
    m["instrument_field_names"] = sorted({k for i in inst for k in i["fields"]})
    m["instrument_onchain_field_hits"] = sorted(
        f for f in m["instrument_field_names"]
        if any(t in f.lower() for t in INSTRUMENT_ONCHAIN_FIELDS))
    m["kline_row_has_onchain_field"] = any(
        t in f.lower() for f in (m.get("klines_row_field_set") or []) for t in ONCHAIN_TOKENS)

    schema_path = os.path.join(raw, "_meta", "SCHEMA.md")
    schema = open(schema_path, encoding="utf-8").read() if os.path.exists(schema_path) else ""
    flat = schema.replace("*", "")
    m["schema_sha256"] = _sha256_text(schema) if schema else None
    m["schema_dataset_sections"] = sorted(ln.split("## Dataset:", 1)[1].strip()
                                          for ln in schema.splitlines()
                                          if ln.startswith("## Dataset:"))
    m["schema_documents_onchain_dataset"] = any(
        t in flat.lower() for t in ("utxo", "on-chain", "onchain", "hodl", "realized cap",
                                    "realized-cap", "market age", "age band"))
    m["schema_single_venue"] = "Binance USD-M perpetual futures, UTC" in schema
    m["schema_documents_missing_fields"] = ("quote_volume" in flat
                                            and "trade count and taker-buy splits are" in flat)
    m["schema_no_silent_gapfill"] = "No gaps are silently filled" in schema
    m["schema_onchain_word_lines"] = [ln.strip()[:90] for ln in schema.splitlines()
                                      if any(t in ln.lower()
                                             for t in ("utxo", "on-chain", "onchain", "hodl",
                                                       "asset", "ledger", "blockchain"))]
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
    m["raw_entry_name_count"] = len(names)
    m["probe_tokens_tested"] = list(ONCHAIN_TOKENS)
    m["onchain_token_hits_in_entry_names"] = sorted({t for t in ONCHAIN_TOKENS if t in joined})
    m["excluded_token_raw_tree_collisions"] = {
        t: sorted({n for n in names if t in n})[:3] for t in PROBE_EXCLUSIONS
        if any(t in n for n in names)}
    m["raw_entry_names_sample"] = names[:40]
    m["probe_exclusions"] = dict(PROBE_EXCLUSIONS)
    m["meta_sha256"] = {rel: _sha256_file(os.path.join(raw, "_meta", rel))
                        for rel in ("CONFIG.json", "SCHEMA.md", "INVENTORY.md",
                                    "INSTRUMENTS_EXPORT.json", "TRANSCODE_MANIFEST.json",
                                    "FUNDING_EXPORT.json")
                        if os.path.exists(os.path.join(raw, "_meta", rel))}
    m["note"] = ("single venue (BINANCE USD-M perpetual), four fixed contracts: klines "
                 "whose rows carry the six-field OHLCV shape only, that venue's funding, "
                 "and its instrument definitions. No blockchain/UTXO dataset of any kind, "
                 "no realized-cap or HODL-age band surface, and no BTC/USD (spot dollars) "
                 "price source; every instrument is quoted and settled in USDT. The raw's "
                 "own daily history begins 2022-01-01, about 1.18 four-year cycles, while "
                 "the record multiplies by an age measured from 2009-01-03.")
    return m


def run_checks(results_root, raw_root, repo_root=None, card_body_path=None,
               record_path=None, board_db=None):
    checks = []
    add = lambda cid, ok, detail: checks.append({
        "id": cid, "status": "PASS" if ok else "FAIL", "detail": detail})
    raw = measure_raw(raw_root)
    round_dir = os.path.join(results_root, FAMILY, "rounds", ROUND)

    # C1 - one market type for one venue, and no other market directory at all
    #      (no spot market, no dated futures, no second venue).
    md = raw["binance_market_dirs"] or []
    add("C1", md == ["usdm"] and raw["venue"] == "BINANCE" and raw["market_type"] == "usdm_perp"
        and raw["paths_named_other_market"] == [] and raw["non_binance_paths"] == [],
        "binance market dirs=%s venue=%s market_type=%s other_market_paths=%s "
        "non_binance_paths=%s" % (md, raw["venue"], raw["market_type"],
                                  raw["paths_named_other_market"], raw["non_binance_paths"]))

    # C2 - the four USD-M perpetual contracts are the only local instruments, and every
    #      one of them is quoted and settled in USDT (there is no BTC/USD instrument and
    #      no spot market for the record's on-chain ledger).
    same = (raw["klines_dataset_dirs"] == EXPECTED_SYMBOLS
            and raw["funding_symbol_dirs"] == EXPECTED_SYMBOLS
            and raw["symbols"] == EXPECTED_SYMBOLS
            and raw["instrument_types"] == ["CryptoPerpetual"]
            and raw["instrument_count"] == len(EXPECTED_SYMBOLS)
            and raw["instrument_quote_currencies"] == ["USDT"]
            and raw["instrument_settlement_currencies"] == ["USDT"])
    add("C2", bool(same), "klines=%s funding=%s config=%s types=%s count=%s quote=%s settle=%s"
        % (raw["klines_dataset_dirs"], raw["funding_symbol_dirs"], raw["symbols"],
           raw["instrument_types"], raw["instrument_count"],
           raw["instrument_quote_currencies"], raw["instrument_settlement_currencies"]))

    # C3 - THE DECISIVE ONE: the record's own decision variables (the realized-cap HODL
    #      age bands RC_1w and RC_1y-2y over the Bitcoin UTXO set) are absent. No
    #      on-chain/UTXO token appears in any entry name under the raw tree at full depth,
    #      no stored row shape carries such a field, the store's own documented dataset
    #      families are exactly three (none on-chain), and the store's own inventory
    #      records that no other store was found on the host.
    add("C3", raw["onchain_token_hits_in_entry_names"] == []
        and raw["kline_row_has_onchain_field"] is False
        and raw["funding_row_has_onchain_field"] is False
        and raw["instrument_onchain_field_hits"] == []
        and raw["schema_documents_onchain_dataset"] is False
        and raw["schema_dataset_sections"] == DOCUMENTED_DATASET_FAMILIES
        and raw["inventory_declares_no_other_store_found"] is True,
        "onchain_token_hits=%s kline_row_has_onchain_field=%s funding_row_has_onchain_field=%s "
        "instrument_onchain_fields=%s schema_documents_onchain_dataset=%s "
        "documented_dataset_families=%s inventory_no_other_store=%s "
        "(%d probe tokens over %d entry names, depth<=%d)"
        % (raw["onchain_token_hits_in_entry_names"], raw["kline_row_has_onchain_field"],
           raw["funding_row_has_onchain_field"], raw["instrument_onchain_field_hits"],
           raw["schema_documents_onchain_dataset"], raw["schema_dataset_sections"],
           raw["inventory_declares_no_other_store_found"],
           len(ONCHAIN_TOKENS), raw["raw_entry_name_count"], PROBE_MAX_DEPTH))

    # C4 - the registered price source is absent AS REGISTERED: there is no BTC/USD
    #      volume-weighted spot index and no spot market; the only BTC series is the
    #      USDT-quoted USD-M perpetual. Daily BTC OHLCV does exist - which is why this is
    #      recorded as an absent source rather than an empty table - and its bars are
    #      exactly UTC-day aligned, so alignment is present while the source is not.
    add("C4", raw["paths_named_other_market"] == []
        and raw["non_binance_paths"] == []
        and raw["instrument_quote_currencies"] == ["USDT"]
        and raw["instrument_settlement_currencies"] == ["USDT"]
        and "BTCUSDT" in (raw["klines_dataset_dirs"] or [])
        and raw.get("klines_1d_open_step_seconds") == [86400]
        and all(v == 0 for v in (raw.get("klines_1d_first_open_mod_86400_seconds") or [1])),
        "other_market_paths=%s non_binance_paths=%s quote=%s settle=%s btc_series=%s "
        "step_seconds=%s open_mod_86400=%s"
        % (raw["paths_named_other_market"], raw["non_binance_paths"],
           raw["instrument_quote_currencies"], raw["instrument_settlement_currencies"],
           "BTCUSDT" in (raw["klines_dataset_dirs"] or []),
           raw.get("klines_1d_open_step_seconds"),
           raw.get("klines_1d_first_open_mod_86400_seconds")))

    # C5 - the record's derived series cannot be constructed and its required sample
    #      cannot be expressed: there is no UTXO/realized-value substrate at any age, the
    #      market-age multiplier is measured from 2009-01-03 while the raw's own daily
    #      history starts at the card's registered start (2022-01-01) and spans about 1.18
    #      four-year cycles, versus the 2011/2013/2015/2017-18/2021 cycles the record's
    #      evidence base and falsification battery are built on.
    add("C5", raw["raw_1d_window_starts_at_or_after_card_registration"] is True
        and raw["raw_1d_window_utc"] is not None
        and raw["cycles_covered_4y_equivalent"] < 2
        and raw["raw_1d_window_reaches_back_to_genesis"] is False
        and raw["onchain_token_hits_in_entry_names"] == []
        and raw["schema_documents_onchain_dataset"] is False,
        "raw_1d_window=%s history_days=%s cycles_4y_equivalent=%s "
        "card_registered_raw_window=%r reaches_back_to_genesis(%s)=%s onchain_token_hits=%s"
        % (raw["raw_1d_window_utc"], raw.get("raw_1d_history_days"),
           raw.get("cycles_covered_4y_equivalent"), CARD_REGISTERED_RAW_WINDOW,
           GENESIS_DATE, raw["raw_1d_window_reaches_back_to_genesis"],
           raw["onchain_token_hits_in_entry_names"]))

    # C6 - the stored shapes are exactly what the raw documents (six-field OHLCV klines
    #      plus the convenience close_time_ms, the venue's funding rows, its instrument
    #      definitions), so the absence above is a property of the store, not of this
    #      reader's field names.
    add("C6", raw.get("klines_row_field_set") == KLINE_ROW_FIELDS
        and raw["schema_documents_missing_fields"] is True
        and raw["schema_single_venue"] is True
        and raw["schema_no_silent_gapfill"] is True
        and 0 < len(raw.get("klines_1d_first_open_mod_86400_seconds") or []) <= 3
        and all(v == 0 for v in raw["klines_1d_first_open_mod_86400_seconds"]),
        "kline_row_field_set=%s schema_documents_missing_fields=%s schema_single_venue=%s "
        "schema_no_silent_gapfill=%s open_mod_86400=%s"
        % (raw.get("klines_row_field_set"), raw["schema_documents_missing_fields"],
           raw["schema_single_venue"], raw["schema_no_silent_gapfill"],
           raw.get("klines_1d_first_open_mod_86400_seconds")))

    # ---- artifact side
    spec_path = os.path.join(round_dir, "round-spec.json")
    verdict_path = os.path.join(round_dir, "verdict.json")
    spec = _load_json(spec_path) if os.path.exists(spec_path) else {}
    verdict = _load_json(verdict_path) if os.path.exists(verdict_path) else {}

    # C7 - the recorded house-wide scan is internally consistent and its point-in-time hit
    #      set is explained: every hit carries an acceptable (non-data) classification, no
    #      hit is unclassified, the raw name probe agrees with the live re-measurement, and
    #      any path that appeared since the snapshot was written is one of this card's own
    #      artifacts (the round dir / evidence snapshot), never a newly-visible data store.
    gate = spec.get("prerequisite_gate") or {}
    meas = gate.get("measured_available") or {}
    hs = gate.get("host_wide_scan") or {}
    house = hs.get("house_wide_probe") or {}
    cls_counts = house.get("classification_counts") or {}
    stored_paths = {h.get("path") for h in (house.get("hits") or [])}
    live_scan = host_scan()
    live_paths = {h.get("path") for h in (live_scan.get("hits") or [])}
    own_classes = {"own_evidence_snapshot_false_positive", "own_round_artifact_false_positive",
                   "own_family_directory_false_positive"}
    live_only = sorted(live_paths - stored_paths)
    stored_only = sorted(stored_paths - live_paths)
    drift_ok = all(_classify_house_hit(p) in own_classes for p in live_only)
    add("C7", house.get("unclassified_hits") == []
        and not cls_counts.get(UNCLASSIFIED_CLASS)
        and sum(v for k, v in cls_counts.items() if k != UNCLASSIFIED_CLASS) == house.get("hit_count")
        and house.get("hit_count") == len(house.get("hits") or []) + (house.get("hits_capped_by") or 0)
        and bool(house.get("note"))
        and bool(house.get("probe_exclusions"))
        and len(house.get("excluded_token_scan") or {}) == len(PROBE_EXCLUSIONS)
        and all(v.get("hit_count") == sum((v.get("classification_counts") or {}).values())
                for v in (house.get("excluded_token_scan") or {}).values())
        and not any(any(rt in p.lower() for rt in ONCHAIN_TOKENS)
                    for v in (house.get("excluded_token_scan") or {}).values()
                    for p in (v.get("unclassified_data_candidates") or []))
        and all((d.get("house_collision_example") or d.get("house_hit_count") == 0)
                for d in (house.get("probe_exclusion_demonstration") or {}).values())
        and meas.get("onchain_token_hits_in_entry_names") == []
        and meas.get("excluded_token_raw_tree_collisions")
        == raw["excluded_token_raw_tree_collisions"]
        and set(raw["excluded_token_raw_tree_collisions"]) <= set(PROBE_EXCLUSIONS)
        and all("btcusdt" in n
                for ex in raw["excluded_token_raw_tree_collisions"].values() for n in ex)
        and raw["onchain_token_hits_in_entry_names"] == []
        and drift_ok,
        "house_hits_stored=%s classification_counts=%s unclassified=%s hits_listed=%s "
        "capped_by=%s raw_name_probe_hits(artifact)=%s (live)=%s excluded_token_unclassified="
        "%s excluded_token_raw_collisions=%s | live_house_hits=%s live_only=%s "
        "stored_only=%s drift_all_own_artifacts=%s"
        % (house.get("hit_count"), cls_counts, house.get("unclassified_hits"),
           len(house.get("hits") or []), house.get("hits_capped_by"),
           meas.get("onchain_token_hits_in_entry_names"),
           raw["onchain_token_hits_in_entry_names"],
           sum(len(v.get("unclassified_data_candidates") or [])
               for v in (house.get("excluded_token_scan") or {}).values()),
           raw["excluded_token_raw_tree_collisions"],
           live_scan.get("hit_count"), live_only, stored_only, drift_ok))

    # C8 - the registration still states the record's own requirement (it was NOT shrunk
    #      to the locally available instruments) and records the missing surfaces.
    univ = spec.get("universe_registration") or {}
    matrix = {i.get("item"): i for i in (gate.get("required_data_matrix") or [])
              if isinstance(i, dict)}
    ok8 = (
        spec.get("family_id") == FAMILY and spec.get("round_id") == ROUND
        and spec.get("kanban_task_id") == TASK
        and gate.get("outcome") == "PREREQUISITE_MISSING"
        and gate.get("verdict") == "TECHNICAL_INCOMPLETE"
        and gate.get("required_data_available") is False
        and gate.get("attempts_launched") == 0
        and set(MISSING_DATA_MATRIX_ITEMS) <= set(matrix)
        and all(matrix[k].get("status") for k in MISSING_DATA_MATRIX_ITEMS)
        and all(matrix[k].get("status") == v
                for k, v in DECISIVE_REQUIRED_STATUS.items())
        and matrix["instrument_bitcoin"].get("status") == "ABSENT_AS_REGISTERED_SOURCE"
        and matrix["timeframe_daily"].get("status") == "PRESENT"
        and matrix["baseline_buy_and_hold"].get("status") == "PRESENT_AS_LOCAL_PROXY"
        and univ.get("universe_shrunk_to_local_list") is False
        and univ.get("instrument_required") == ["BTC"]
        and univ.get("onchain_dataset_required")
        == "Daily aggregate RC_1w / RC_1y-2y realized-cap HODL bands (Bitcoin UTXO set)"
        and univ.get("onchain_dataset_available") is False
        and univ.get("utxo_ledger_available") is False
        and univ.get("point_in_time_utxo_snapshot_available") is False
        and univ.get("price_source_required")
        == "Daily BTC/USD volume-weighted spot index (Coin Metrics / Kaiko / CoinMarketCap)"
        and univ.get("price_source_available_as_registered") is False
        and univ.get("onchain_name_probe_hits") == []
        and univ.get("probe_tokens_tested") == list(ONCHAIN_TOKENS)
        and univ.get("probe_exclusions") == PROBE_EXCLUSIONS
        and univ.get("probe_excluded_token_raw_tree_collisions")
        == raw["excluded_token_raw_tree_collisions"]
        and (spec.get("signal_semantics") or {}).get("decision_variable_available") is False
        and (spec.get("expected") or {}).get("status") == "not_computable"
        and (spec.get("strategy_domain") or {}).get("status") == "not_registered"
        and (spec.get("launch") or {}).get("attempts_launched") == 0
        and bool(spec.get("excerpt_source_map"))
    )
    add("C8", bool(ok8),
        "round-spec ids/gate=%s required_available=%s decisive=%s shrunk=%s "
        "onchain_available=%s price_source_available=%s matrix_items=%d probe_tokens_equal=%s"
        % (gate.get("outcome"), gate.get("required_data_available"),
           {k: matrix.get(k, {}).get("status") for k in DECISIVE_MATRIX_ITEMS},
           univ.get("universe_shrunk_to_local_list"),
           univ.get("onchain_dataset_available"),
           univ.get("price_source_available_as_registered"), len(matrix),
           univ.get("probe_tokens_tested") == list(ONCHAIN_TOKENS)))

    # C9 - the terminal verdict states the contract-mandated values.
    fail = verdict.get("failure") or {}
    yld = verdict.get("yield") or {}
    ok9 = (
        verdict.get("verdict") == "TECHNICAL_INCOMPLETE"
        and verdict.get("performance_claimable") is False
        and verdict.get("family_id") == FAMILY and verdict.get("round_id") == ROUND
        and verdict.get("kanban_task_id") == TASK
        and verdict.get("run_id") is None
        and fail.get("layer") == "card-local"
        and fail.get("class") == "data_window_invalid"
        and yld.get("yield_decision") == "STOP_TECHNICAL_INCOMPLETE"
        and (verdict.get("attempts") or {}).get("launched") == 0
        and not verdict.get("evidence_run_ids")
        and (verdict.get("prerequisite") or {}).get("onchain_dataset_available") is False
    )
    add("C9", ok9, "verdict=%s run_id=%s layer=%s class=%s yield=%s run_ids=%s "
                   "onchain_dataset_available=%s"
        % (verdict.get("verdict"), verdict.get("run_id"), fail.get("layer"), fail.get("class"),
           yld.get("yield_decision"), verdict.get("evidence_run_ids"),
           (verdict.get("prerequisite") or {}).get("onchain_dataset_available")))

    # C10 - nothing was ever submitted: no attempt directory, no terminal sentinel.
    attempts_dir = os.path.join(round_dir, "attempts")
    sentinels = []
    for dp, dn, fn in os.walk(round_dir):
        for f in fn:
            if f in ("DONE", "FAILED", "INCOMPLETE"):
                sentinels.append(os.path.join(dp, f))
    add("C10", not os.path.exists(attempts_dir) and sentinels == [],
        "attempts_dir_exists=%s terminal_sentinels=%s" % (os.path.exists(attempts_dir),
                                                          sentinels))

    # C11 - ownership, and the round directory holds only the two immutable artifacts.
    fam_path = os.path.join(results_root, FAMILY, "family.json")
    fam = _load_json(fam_path) if os.path.exists(fam_path) else {}
    files = sorted(f for f in os.listdir(round_dir)) if os.path.isdir(round_dir) else None
    add("C11", fam.get("kanban_task_id") == TASK
        and files == ["round-spec.json", "verdict.json"],
        "family.kanban_task_id=%s round_dir_files=%s" % (fam.get("kanban_task_id"), files))

    # C12 - the DCA registration keeps its contract 7.2 v1.3.1 provenance classes and its
    #       complete 48-cell product (a searched axis is never declared as a user-fixed
    #       invariant), and the card's USER_FIXED invariants are registered verbatim.
    probs = []
    dca = spec.get("dca_domain") or {}
    invariants = list(spec.get("user_fixed_invariants") or [])
    if _sav2 is None:
        probs.append("runtime/strategy_a_v2_counts.py is not importable")
    else:
        for axis in _sav2.DCA_AXES:
            values = list(dca.get(axis) or [])
            cls = _sav2.provenance_class(dca.get(axis + "_status"))
            if len(values) > 1 and cls != _sav2.PROJECT_SEARCH:
                probs.append("%s is searched over %r but classified %r" % (axis, values, cls))
        if _sav2.provenance_class(dca.get("base_quote_status")) != _sav2.PROJECT_CONSTANT:
            probs.append("base_quote_status=%r is not %s"
                         % (dca.get("base_quote_status"), _sav2.PROJECT_CONSTANT))
        cells = _sav2.product([list(dca.get(a) or []) for a in _sav2.DCA_AXES])
        declared = [tuple(g.get(a) for a in _sav2.DCA_AXES) for g in (dca.get("grid") or [])]
        if (len(cells) != 48 or len(declared) != len(cells)
                or sorted(set(declared)) != sorted(set(cells))
                or dca.get("config_count") != 48):
            probs.append("grid is not the complete 48-cell product (declared=%d product=%d "
                         "config_count=%r)" % (len(declared), len(cells),
                                               dca.get("config_count")))
        for key in _sav2.USER_FIXED_FORBIDDEN_KEYS:
            if any(str(s).lower().startswith(key.lower()) for s in invariants):
                probs.append("user_fixed_invariants declares the searched axis %r" % key)
    blob = " | ".join(str(s) for s in invariants).lower()
    for token in INVARIANT_TOKENS:
        if token not in blob:
            probs.append("registered invariant token missing: %s" % token)
    add("C12", not probs, "dca provenance/grid+bounded invariants: %s"
        % (probs if probs else "ok"))

    # C13 - every `*_verbatim` leaf of the persisted round-spec resolves against its
    #       declared source, and every excerpt digest is declared in the excerpt map.
    try:
        vb = verify_verbatim(spec_path, repo_root=repo_root, card_body_path=card_body_path,
                             record_path=record_path, board_db=board_db)
        ok13 = (vb["problems"] == [] and vb["misses"] == []
                and vb["unclassified_verbatim_paths"] == []
                and vb["missing_from_source_map"] == []
                and vb["orphan_map_entries"] == []
                and vb["digests_disagreeing_with_content"] == []
                and vb["verbatim_strings_on_disk"] > 0
                and vb["source_sha256_matches_declared"] is True)
        detail13 = ("verbatim_leaves=%d sources=%s misses=%s problems=%s digest_mismatches=%s "
                    "orphan_map_entries=%s source_sha_ok=%s"
                    % (vb["verbatim_strings_on_disk"], vb["declared_source_counts"],
                       vb["misses"], vb["problems"], vb["digests_disagreeing_with_content"],
                       vb["orphan_map_entries"], vb["source_sha256_matches_declared"]))
    except Exception as exc:  # fail closed: an unresolvable source is a FAIL, not a skip
        ok13, detail13 = False, "verbatim verification raised %s: %s" % (type(exc).__name__, exc)
    add("C13", bool(ok13), detail13)

    return {"family_id": FAMILY, "round_id": ROUND, "task_id": TASK,
            "results_root": results_root, "raw_root": raw_root,
            "measured_raw": raw, "checks": checks,
            "overall": "PASS" if all(c["status"] == "PASS" for c in checks) else "FAIL"}


# --------------------------------------------------------------------------- sources

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
            report["unclassified_verbatim_paths"].append({"path": path, "key": key, "source": src})
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
            {"verdict": "PASS"})),
        "layer_flipped_to_shared": lambda d: _tamper(d, lambda v: v["failure"].update(
            {"layer": "shared-layer"})),
        "run_id_fabricated": lambda d: _tamper(d, lambda v: v.update(
            {"run_id": ROUND + "-u1"})),
        "attempt_fabricated": lambda d: _fabricate_attempt(d),
        "required_data_available_flipped_true": lambda d: _tamper_spec(d, lambda s: s[
            "prerequisite_gate"].update({
                "required_data_available": True, "outcome": "PREREQUISITE_PRESENT",
                "missing": []})),
        "onchain_dataset_matrix_item_claimed_present": lambda d: _tamper_spec(
            d, lambda s: _set_matrix_status(s, "archival_bitcoin_node_utxo_parse",
                                            "PRESENT")),
        "onchain_dataset_claimed_available": lambda d: _tamper_spec(d, lambda s: s[
            "universe_registration"].update({
                "onchain_dataset_available": True,
                "utxo_ledger_available": True,
                "point_in_time_utxo_snapshot_available": True})),
        "universe_shrunk_to_the_local_four_contract_list": lambda d: _tamper_spec(d, lambda s: s[
            "universe_registration"].update({
                "universe_shrunk_to_local_list": True,
                "instrument_required": ["BTC", "ETH", "BNB", "SOL"]})),
        "btc_usd_price_source_claimed_present": lambda d: _tamper_spec(d, lambda s: s[
            "universe_registration"].update({
                "price_source_available_as_registered": True,
                "price_source_available_local": "Daily BTC/USD volume-weighted spot index"})),
        "probe_token_list_narrowed": lambda d: _tamper_spec(d, lambda s: s[
            "universe_registration"].update({"probe_tokens_tested": ["rhodl", "utxo"]})),
        "searched_axis_mislabelled_user_fixed": lambda d: _tamper_spec(
            d, lambda s: s["dca_domain"].update({"spacing_pct_status": "USER_FIXED"})),
        "dca_grid_truncated_to_47": lambda d: _tamper_spec(
            d, lambda s: s["dca_domain"]["grid"].pop()),
        "zero_attempt_claim_replaced_by_an_attempt_dir": lambda d: _fabricate_attempt(
            d, name="INCOMPLETE"),
        "verbatim_leaf_replaced_by_a_paraphrase": lambda d: _tamper_spec(
            d, lambda s: s["hypothesis"].update(
                {"identity_verbatim": "# Bitcoin RHODL ratio (paraphrased)"})),
        "verbatim_digest_removed_from_the_map": lambda d: _tamper_spec(
            d, lambda s: s["excerpt_source_map"].pop("identity_verbatim", None)),
        "verbatim_source_repointed": lambda d: _tamper_spec(
            d, lambda s: s["excerpt_source_map"]["identity_verbatim"].update(
                {"source": "card"})),
        "verbatim_chars_field_falsified": lambda d: _tamper_spec(
            d, lambda s: s["excerpt_source_map"]["identity_verbatim"].update(
                {"chars": 1})),
        "verbatim_leaf_relabelled_as_authored_prose": lambda d: _tamper_spec(
            d, lambda s: s["hypothesis"].update(
                {"identity_note": s["hypothesis"].pop("identity_verbatim")})),
    }
    results = []
    ok = True
    for name, mutate in variants.items():
        tmp = tempfile.mkdtemp(prefix="xq-rhodl-prereq-selftest-")
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

    Build a temp raw tree that carries what this record would need: a Bitcoin
    on-chain UTXO / realized-cap HODL age-band dataset, a spot market holding a BTC/USD
    daily series, a second venue, and a daily bar older than the card's registered raw
    start. The raw-side checks must flip to FAIL.
    """
    tmp = tempfile.mkdtemp(prefix="xq-rhodl-prereq-rawfix-")
    try:
        fixture = os.path.join(tmp, "raw")
        shutil.copytree(raw_root, fixture,
                        ignore=shutil.ignore_patterns(".DS_Store", "__pycache__"))
        # the record's required on-chain dataset: realized-cap HODL age bands per day
        d = os.path.join(fixture, "_ref", "bitcoin_onchain_utxo")
        os.makedirs(d)
        _kw_gz(os.path.join(d, "bitcoin-realized-cap-hodl-waves-daily.jsonl.gz"),
               {"date": "2022-01-01", "rc_1w_usd": "41000000000",
                "rc_1y_2y_usd": "210000000000", "market_age_days": "4746"})
        # a spot market with a BTC/USD daily series, and a second venue
        os.makedirs(os.path.join(fixture, "okx", "swap"))
        sp = os.path.join(fixture, "binance", "spot", "BTCUSD", "1d")
        os.makedirs(sp)
        _kw_gz(os.path.join(sp, "BTCUSD-1d-2016-01.jsonl.gz"),
               {"open_time_ms": 1451606400000, "close_time_ms": 1451692799999,
                "open": "430.0", "high": "435.0", "low": "428.0", "close": "432.0",
                "volume": "1200.0"})
        # a daily bar older than the card's registered raw window start
        old = os.path.join(fixture, "binance", "usdm", "klines", "BTCUSDT", "1d",
                           "BTCUSDT-1d-2016-01.jsonl.gz")
        _kw_gz(old, {"open_time_ms": 1451606400000, "close_time_ms": 1451692799999,
                     "open": "430.0", "high": "435.0", "low": "428.0", "close": "432.0",
                     "volume": "1200.0"})
        res = run_checks(results_root, fixture)
        failed = [c["id"] for c in res["checks"] if c["status"] == "FAIL"]
        # C1/C3/C4/C5 are pure raw-measurement checks. C7 and C8 additionally re-assert that
        # the persisted artifact agrees with the LIVE raw measurement (name probe + excluded
        # token collisions), so a fixture that plants an on-chain dataset must flip them too.
        # C6 is deliberately NOT expected to flip: the fixture adds datasets, it does not
        # change the stored kline row shape.
        expected = {"C1", "C3", "C4", "C5", "C7", "C8"}
        return {"raw_fixture_control": {
            "failed_checks": sorted(failed), "expected": sorted(expected),
            "why": "the fixture plants what this record needs (a Bitcoin UTXO realized-cap "
                   "age-band dataset, a spot BTC/USD daily series, a second venue, a "
                   "pre-2022 daily bar); C1-C5 re-measure the raw and C7-C8 re-assert the "
                   "artifact-to-live-raw agreement, so all six must flip. C6 (stored kline "
                   "row shape) is not expected to flip.",
            "detail": {c["id"]: c["detail"] for c in res["checks"]
                       if c["id"] in expected | {"C6"}},
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
    on-chain surface. Those land in UNCLASSIFIED_CLASS and must be disclosed.
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
    if base.endswith(".py") and ("prerequisite_check" in base or "prerequisite_gate" in base):
        return "own_checker_source_false_positive"
    if WIKI_DIR_MARKER in low or (low.endswith(".md") and "/wiki/" in low):
        return ("canonical_record_document" if FAMILY in low else "related_wiki_document")
    if any("/%s/" % d in low for d in BACKUP_LIKE_DIRS):
        return "non_canonical_staging_copy_false_positive"
    if os.path.join(HOME, ".hermes") in path and ("sessions" in low or "logs" in low
                                                 or "kanban" in low or "cron" in low):
        return "agent_session_or_log_false_positive"
    if "/cache/" in low:
        return "hermes_cache_false_positive"
    if "/contributors/" in low:
        return "contributor_metadata_false_positive"
    if "/optional-skills/" in low or "/plugin-catalog/" in low:
        return "hermes_skill_or_catalog_false_positive"
    if os.path.isdir(path):
        probe = _dir_data_probe(path)
        if probe["data_file_count"] == 0 and probe["other_file_count"] > 0:
            return "dataset_stub_directory_false_positive"
        return UNCLASSIFIED_CLASS
    if low.endswith(DOC_EXTS):
        return "repo_documentation_false_positive"
    if low.endswith(CODE_EXTS):
        return "source_code_false_positive"
    return UNCLASSIFIED_CLASS


def _probe_hits(roots, tokens):
    """Every path whose own name - or whose containing directory path - carries a token."""
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
    """House-wide search for a Bitcoin on-chain dataset (read-only).

    Two probes are run: the registered token list, and - separately and disclosed - the
    excluded tokens, so an exclusion can never hide a hit that the registered probe would
    have found.
    """
    scanned, skipped = [], []
    for r in TMP_ROOTS:
        (scanned if os.path.isdir(r) else skipped).append(r)

    records, counts = [], {}
    for p in _probe_hits(scanned, ONCHAIN_TOKENS):
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
            "btcusdt_named_hits": sum(1 for p in tk if "btcusdt" in os.path.basename(p).lower()),
            "examples": tk[:5],
        }
    return {"roots_scanned": scanned, "skipped_roots": skipped,
            "probe_tokens": list(ONCHAIN_TOKENS),
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
                     "tree was run over %d roots; it returned %d hit(s) in %d classification(s), "
                     "%d of them unclassified, and no hit class is a data store. A separate "
                     "disclosed pass re-ran the %d excluded token(s) - excluded because each is "
                     "a substring of a word or name the host already uses (an ordinary English "
                     "word, a contributor handle, or the local contract name), so a hit could "
                     "never distinguish an on-chain dataset from those artefacts - and returned "
                     "%s hit(s) in %d classification(s), %s of them unclassified, of which %s "
                     "carry the local contract name 'btcusdt' in the file name. The canonical "
                     "raw remains the only market-data source on the host and it holds no "
                     "on-chain dataset."
                     % (len(ONCHAIN_TOKENS), len(scanned), len(records), len(counts),
                        len(unclassified), len(PROBE_EXCLUSIONS),
                        sum(v["hit_count"] for v in excluded_scan.values()),
                        len({c for v in excluded_scan.values()
                             for c in v["classification_counts"]}),
                        sum(len(v["unclassified_data_candidates"])
                            for v in excluded_scan.values()),
                        sum(v["btcusdt_named_hits"] for v in excluded_scan.values())))}


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
