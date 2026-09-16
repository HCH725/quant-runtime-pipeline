#!/usr/bin/env python3
"""Prerequisite-gate read-back checker for the family

    bitcoin-hash-ribbon-miner-capitulation-2026-08-31

Card t_60876182 terminalised this family as TECHNICAL_INCOMPLETE because the
record's required data is not in the canonical raw:

  * the record's decision variable is "Daily Bitcoin Network Hash Rate" - a
    30-day/60-day SMA crossover of the hash rate IS the signal (capitulation
    when the 30d crosses below the 60d; buy when it crosses back above). The
    canonical raw holds only BINANCE USD-M perpetual klines/funding/instruments
    for four contracts: there is no network/on-chain dataset of any kind, no
    hash rate, no mining difficulty, no block or miner series;
  * the record's registered market data is "Daily BTC/USD OHLCV"; locally the
    only BTC series is a USDT-quoted USD-M perpetual (BTCUSDT) at one venue;
  * the record requires point-in-time historical hash-rate data without
    lookahead bias - with no hash-rate series at all, neither variant exists;
  * the record's falsification battery starts from "Backtest over all available
    Bitcoin history, separating price-driven capitulations from external shock
    capitulations", and its ablation isolates the network data against a simple
    price trend. Both need the network leg, so neither is expressible.

Running the local four-contract perpetual panel instead would change the
record's decision variable, its market source and its falsification sample. The
card forbids that ("不得以近似資料、替代市場或改寫 hypothesis 硬跑" /
"不得縮減 universe 以硬造可執行性"), so nothing was launched.

This script exists so an independent reader can re-derive that determination from
the live filesystem instead of trusting prose:

  * C1-C6 re-measure the canonical raw: the market-directory set, the instrument
    surface, the decisive absence of any hash-rate/network dataset (name probe
    over the whole tree plus the stored row shapes plus the documented dataset
    families), the absence of the registered BTC/USD source, and the raw's own
    daily window;
  * C7-C11 re-read the round's immutable artifacts and assert they state exactly
    the contract-mandated terminal values (verdict, layer, class, yield decision,
    zero attempts, null run_id), that the registered requirement was NOT shrunk
    to the locally available instruments, and that nothing was ever submitted
    (no attempt directory, no terminal sentinel);
  * C12 asserts the DCA registration still carries the contract 7.2 v1.3.1
    provenance classes plus the complete 48-cell product, reusing the existing
    registered validator instead of re-implementing a second one.

Read-only: it never writes inside the results tree or the raw tree. `--self-test`
builds tampered copies in fresh temp directories and asserts the checker refuses
them (non-vacuousness control). `--raw-fixture-control` builds a temp raw tree
carrying what this record would need (a Bitcoin network hash-rate dataset, a spot
market with a BTC/USD daily series, a second venue, and a pre-2022 daily bar) and
asserts the raw-side checks flip to FAIL. `--host-scan` re-runs the house-wide
search for a network dataset. Every temp tree is removed afterwards.

Usage:
    python3 runtime/bitcoin_hash_ribbon_miner_capitulation_prerequisite_check.py [--json]
    python3 runtime/bitcoin_hash_ribbon_miner_capitulation_prerequisite_check.py --self-test
    python3 runtime/bitcoin_hash_ribbon_miner_capitulation_prerequisite_check.py --raw-fixture-control
    python3 runtime/bitcoin_hash_ribbon_miner_capitulation_prerequisite_check.py --host-scan

Exit codes: 0 = all checks PASS, 1 = at least one FAIL, 2 = usage error.
"""
import argparse
import gzip
import hashlib
import json
import os
import shutil
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

FAMILY = "bitcoin-hash-ribbon-miner-capitulation-2026-08-31"
ROUND = FAMILY + "-r1"
TASK = "t_60876182"
DEFAULT_RESULTS = "/Volumes/ExpansionDrive/qlib-results"
DEFAULT_RAW = "/Volumes/ExpansionDrive/market-data-raw"
EXPANSION = "/Volumes/ExpansionDrive"
HOME = os.path.expanduser("~")
EXPECTED_SYMBOLS = ["BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
# The record's registered required data (canonical record "Required data" section).
RECORD_REQUIRED_DATA_BULLETS = 5
RECORD_EXPECTED_HOLDING = "- **Holding Period:** Underspecified."
RECORD_EXPECTED_NETWORK_ITEM = "- **Network Data:** Daily Bitcoin Network Hash Rate."
RECORD_EXPECTED_MARKET_ITEM = ("- **Market Data:** Daily BTC/USD OHLCV "
                               "(if using price confirmation filters).")
RECORD_EXPECTED_PIT_ITEM = ("- **Point-in-time:** True historical hash rate data without "
                            "lookahead bias.")
# The exact kline row shape the raw stores: six OHLCV fields plus the convenience
# close_time_ms. No quote volume, no trade count, no taker-buy splits, no network field.
KLINE_ROW_FIELDS = ["close", "close_time_ms", "high", "low", "open", "open_time_ms",
                    "volume"]
# Name tokens that would have to exist for this record's required dataset.
HASH_TOKENS = ("hashrate", "hash_rate", "hash-rate", "network_hash", "net_hash",
               "network_difficulty", "difficulty", "onchain", "on-chain", "mempool",
               "block_height", "blockheight", "miner", "mining", "blockchain", "thermocap",
               "stock_to_flow", "stock-to-flow", "sopr", "puell", "mvrv", "coins_issued",
               "block_reward", "blockreward", "glassnode", "lookintobitcoin", "coinmetrics",
               "blockchain_com", "hashribbon", "hash_ribbon")
# instrument-export field names that would identify a network/mining surface
INSTRUMENT_NETWORK_FIELDS = ("hash", "network", "difficulty", "mining")
DOCUMENTED_DATASET_FAMILIES = ["funding", "instruments",
                               "klines (Binance USD-M perpetual futures, UTC)"]
# the decisive registered data items and the exact status each must carry
DECISIVE_REQUIRED_STATUS = {
    "network_data_bitcoin_hash_rate_daily": "ABSENT",
    "point_in_time_hash_rate_no_lookahead": "ABSENT",
    "hash_rate_regime_history_30_60_sma": "NOT_CONSTRUCTIBLE",
}
OTHER_MARKET_NAMES = ("spot", "margin", "options", "inverse", "coinm", "delivery",
                      "futures", "quarter", "index")
INVARIANT_TOKENS = ("30,000", "numeraire", "leverage 10x", "12 tranches", "tranche #12",
                    "reduce-only", "flat/kill")
MISSING_DATA_MATRIX_ITEMS = (
    "instrument_bitcoin", "timeframe_daily", "network_data_bitcoin_hash_rate_daily",
    "market_data_btc_usd_daily_ohlcv", "point_in_time_hash_rate_no_lookahead",
    "hash_rate_regime_history_30_60_sma", "falsification_sample_all_bitcoin_history",
    "price_momentum_confirmation_inputs", "baseline_buy_and_hold", "baseline_trend_200d_sma",
    "ablation_network_data_vs_price_trend", "market_type_venue_execution_timing",
    "transaction_cost_convention")
DECISIVE_MATRIX_ITEMS = ("network_data_bitcoin_hash_rate_daily",
                         "point_in_time_hash_rate_no_lookahead",
                         "hash_rate_regime_history_30_60_sma")
DECISIVE_STATUS_ITEMS = tuple(DECISIVE_REQUIRED_STATUS)
# The raw's own daily window as registered on the card.
CARD_REGISTERED_RAW_WINDOW_START = "2022-01-01"
TMP_ROOTS = [os.path.join(HOME, "workspace"), EXPANSION, "/Volumes/ResearchData",
             os.path.join(HOME, ".hermes"),
             os.path.join(HOME, "workspace", "qlib-apple-container")]
SCAN_EXTS = (".csv", ".parquet", ".jsonl", ".gz", ".bin", ".feather", ".h5", ".npy",
             ".json", ".tsv", ".txt", ".db", ".sqlite")
SCAN_SKIP_DIRS = ("node_modules", "__pycache__", ".git", "venvs", "site-packages", ".venv",
                  "Photos Library.photoslibrary")
INTEGRITY_MANIFEST_MARKERS = ("hash-manifest", "hashes", "rehash", "hash_compare",
                              "pre_hashes", "post_hash", "hash_three_states", "results_hash")
# this card's own evidence snapshot carries the family name in its filename
OWN_ARTIFACT_BASENAMES = ("bitcoin-hash-ribbon-miner-capitulation-prerequisite-gate-"
                          "20260917.json",)
FALSE_POSITIVE_CLASSES = ("integrity_manifest_false_positive",
                          "own_evidence_snapshot_false_positive")


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


def _all_entries(root, max_depth=8):
    """Every file/directory basename token under the raw root (for name probes).

    The depth bound is deliberately deeper than the store's own nesting (<= 6 levels)
    so that a dataset hidden under klines/<SYMBOL>/<interval>/ cannot escape the probe
    simply by being deep.
    """
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


def _rows(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


def _iso(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def measure_raw(raw):
    """Independent re-measurement of the canonical raw. No artifact is trusted."""
    m = {}
    m["raw_root"] = raw
    m["top_level_dirs"] = _dirs(raw)
    m["binance_market_dirs"] = _dirs(os.path.join(raw, "binance"))
    m["binance_usdm_subdirs"] = _dirs(os.path.join(raw, "binance", "usdm"))
    m["raw_paths"] = _rel_dirs(raw, 3)
    m["paths_named_other_market"] = [
        p for p in m["raw_paths"]
        if os.path.basename(p).lower() in OTHER_MARKET_NAMES]
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
    if first:
        window = [_iso(min(first.values())), _iso(max(last.values()))]
    else:
        window = None
    m["raw_1d_window_utc"] = window
    m["raw_1d_window_starts_at_or_after_card_registration"] = bool(
        window and window[0] >= CARD_REGISTERED_RAW_WINDOW_START)

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
    m["funding_row_has_network_field"] = any(
        t in k.lower() for k in (m.get("funding_row_keys") or []) for t in HASH_TOKENS)

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
    m["instrument_network_field_hits"] = sorted(
        f for f in m["instrument_field_names"]
        if any(t in f.lower() for t in INSTRUMENT_NETWORK_FIELDS))
    m["kline_row_has_network_field"] = any(
        t in f.lower() for f in (m.get("klines_row_field_set") or []) for t in HASH_TOKENS)

    schema_path = os.path.join(raw, "_meta", "SCHEMA.md")
    schema = open(schema_path, encoding="utf-8").read() if os.path.exists(schema_path) else ""
    flat = schema.replace("*", "")
    m["schema_dataset_sections"] = sorted(ln.split("## Dataset:", 1)[1].strip()
                                          for ln in schema.splitlines()
                                          if ln.startswith("## Dataset:"))
    m["schema_documents_network_dataset"] = any(
        t in flat.lower() for t in ("hash rate", "hashrate", "network difficulty",
                                    "network hash"))
    m["schema_single_venue"] = "Binance USD-M perpetual futures, UTC" in schema
    m["schema_documents_missing_fields"] = ("quote_volume" in flat
                                            and "trade count and taker-buy splits are" in flat)
    m["schema_no_silent_gapfill"] = "No gaps are silently filled" in schema
    m["schema_hash_word_lines"] = [ln.strip()[:80] for ln in schema.splitlines()
                                   if "hash" in ln.lower()]

    names = _all_entries(raw, 8)
    joined = "\n".join(names)
    m["raw_entry_name_count"] = len(names)
    m["name_probe_tokens_tested"] = list(HASH_TOKENS)
    m["hash_token_hits_in_entry_names"] = sorted({t for t in HASH_TOKENS if t in joined})
    m["meta_sha256"] = {rel: _sha256_file(os.path.join(raw, "_meta", rel))
                        for rel in ("CONFIG.json", "SCHEMA.md", "INVENTORY.md",
                                    "INSTRUMENTS_EXPORT.json", "TRANSCODE_MANIFEST.json")
                        if os.path.exists(os.path.join(raw, "_meta", rel))}
    m["note"] = ("single venue (BINANCE USD-M perpetual), four fixed contracts: klines "
                 "whose rows carry the six-field OHLCV shape only, that venue's funding, and "
                 "its instrument definitions. No network/on-chain dataset of any kind and no "
                 "BTC/USD (spot dollars) price source; every instrument is quoted and settled "
                 "in USDT. The raw's own daily history begins 2022-01-01.")
    return m


def run_checks(results_root, raw_root):
    checks = []
    add = lambda cid, ok, detail: checks.append({
        "id": cid, "status": "PASS" if ok else "FAIL", "detail": detail})
    raw = measure_raw(raw_root)
    round_dir = os.path.join(results_root, FAMILY, "rounds", ROUND)

    # C1 - one market type for one venue, and no other market directory at all
    #      (no spot, no dated futures, no second venue).
    md = raw["binance_market_dirs"] or []
    add("C1", md == ["usdm"] and raw["venue"] == "BINANCE" and raw["market_type"] == "usdm_perp"
        and raw["paths_named_other_market"] == [] and raw["non_binance_paths"] == [],
        "binance market dirs=%s venue=%s market_type=%s other_market_paths=%s "
        "non_binance_paths=%s" % (md, raw["venue"], raw["market_type"],
                                  raw["paths_named_other_market"], raw["non_binance_paths"]))

    # C2 - the four USD-M perpetual contracts are the only local instruments, and every
    #      one of them is quoted and settled in USDT (there is no BTC/USD instrument).
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

    # C3 - THE DECISIVE ONE: the record's decision variable (Daily Bitcoin Network Hash
    #      Rate) is absent. No hash-rate/network token appears in any entry name under the
    #      raw tree, no stored row shape carries such a field, and the schema documents
    #      exactly three dataset families - none of them a network dataset.
    add("C3", raw["hash_token_hits_in_entry_names"] == []
        and raw["kline_row_has_network_field"] is False
        and raw["funding_row_has_network_field"] is False
        and raw["instrument_network_field_hits"] == []
        and raw["schema_documents_network_dataset"] is False
        and raw["schema_dataset_sections"] == DOCUMENTED_DATASET_FAMILIES,
        "hash_token_hits=%s kline_row_has_network_field=%s funding_row_has_network_field=%s "
        "instrument_network_fields=%s schema_documents_network_dataset=%s "
        "documented_dataset_families=%s (%s probe tokens over %d entry names)"
        % (raw["hash_token_hits_in_entry_names"], raw["kline_row_has_network_field"],
           raw["funding_row_has_network_field"], raw["instrument_network_field_hits"],
           raw["schema_documents_network_dataset"], raw["schema_dataset_sections"],
           len(HASH_TOKENS), raw["raw_entry_name_count"]))

    # C4 - the registered market source is absent AS REGISTERED: there is no BTC/USD
    #      series anywhere and no spot market; the only BTC series is the USDT-quoted
    #      USD-M perpetual. Daily BTC OHLCV does exist, which is why this is recorded as
    #      an absent source rather than an empty table.
    add("C4", raw["paths_named_other_market"] == []
        and raw["non_binance_paths"] == []
        and raw["instrument_quote_currencies"] == ["USDT"]
        and raw["instrument_settlement_currencies"] == ["USDT"]
        and "BTCUSDT" in (raw["klines_dataset_dirs"] or [])
        and raw.get("klines_1d_open_step_seconds") == [86400],
        "other_market_paths=%s non_binance_paths=%s quote=%s settle=%s btc_series=%s "
        "step_seconds=%s" % (raw["paths_named_other_market"], raw["non_binance_paths"],
                             raw["instrument_quote_currencies"],
                             raw["instrument_settlement_currencies"],
                             "BTCUSDT" in (raw["klines_dataset_dirs"] or []),
                             raw.get("klines_1d_open_step_seconds")))

    # C5 - the falsification sample the record registers ('all available Bitcoin history')
    #      cannot be expressed: the raw's own daily history starts exactly at the card's
    #      registered start (2022-01-01) and carries no network series, so the required
    #      price-driven-versus-external-shock separation has no data.
    add("C5", raw["raw_1d_window_starts_at_or_after_card_registration"] is True
        and raw["raw_1d_window_utc"] is not None
        and raw["hash_token_hits_in_entry_names"] == []
        and raw["schema_documents_network_dataset"] is False,
        "raw_1d_window=%s card_registered_start=%s card_registered_raw_window="
        "'klines 2022-01-01→2026-09-11' hash_token_hits=%s"
        % (raw["raw_1d_window_utc"], CARD_REGISTERED_RAW_WINDOW_START,
           raw["hash_token_hits_in_entry_names"]))

    # C6 - the stored shapes are exactly what the raw says they are (six-field OHLCV
    #      klines, the venue's funding rows, its instrument definitions), so the absence
    #      above is a property of the store, not of this reader's field names.
    add("C6", raw.get("klines_row_field_set") == KLINE_ROW_FIELDS
        and raw["schema_documents_missing_fields"] is True
        and raw["schema_single_venue"] is True
        and raw["schema_no_silent_gapfill"] is True
        and 0 < len(raw.get("klines_1d_first_open_mod_86400_seconds") or []) <= 3
        and all(v == 0 for v in raw["klines_1d_first_open_mod_86400_seconds"]),
        "kline_row_field_set=%s schema_documents_missing_fields=%s schema_single_venue=%s "
        "open_mod_86400=%s" % (raw.get("klines_row_field_set"),
                               raw["schema_documents_missing_fields"],
                               raw["schema_single_venue"],
                               raw.get("klines_1d_first_open_mod_86400_seconds")))

    # ---- artifact side
    spec_path = os.path.join(round_dir, "round-spec.json")
    verdict_path = os.path.join(round_dir, "verdict.json")
    spec = _load_json(spec_path) if os.path.exists(spec_path) else {}
    verdict = _load_json(verdict_path) if os.path.exists(verdict_path) else {}

    # C7 - the recorded house-wide scan is internally consistent: the precise probe is
    #      empty and every loose 'hash' filename hit is an integrity manifest, with zero
    #      unclassified hits.
    gate = spec.get("prerequisite_gate") or {}
    meas = gate.get("measured_available") or {}
    hs = gate.get("host_wide_scan") or {}
    add("C7", hs.get("unclassified_hits") == []
        and hs.get("loose_probe_unclassified") == []
        and bool(hs.get("loose_hash_substring_files"))
        and all(r.get("classification") in FALSE_POSITIVE_CLASSES
                for r in (hs.get("hashrate_like_files") or []))
        and all(r.get("classification") in FALSE_POSITIVE_CLASSES
                for r in (hs.get("loose_hash_substring_files") or []))
        and meas.get("hash_token_hits_in_entry_names") == [],
        "precise_hits=%s loose_hits=%s unclassified=%s raw_name_probe_hits=%s"
        % (len(hs.get("hashrate_like_files") or []),
           len(hs.get("loose_hash_substring_files") or []),
           hs.get("unclassified_hits"), meas.get("hash_token_hits_in_entry_names")))

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
        and matrix["market_data_btc_usd_daily_ohlcv"].get("status") == "ABSENT_AS_REGISTERED_SOURCE"
        and matrix["instrument_bitcoin"].get("status") == "ABSENT_AS_REGISTERED_SOURCE"
        and univ.get("universe_shrunk_to_local_list") is False
        and univ.get("instrument_required") == ["BTC"]
        and univ.get("network_dataset_required") == "Daily Bitcoin Network Hash Rate"
        and univ.get("network_dataset_available") is False
        and univ.get("point_in_time_hash_rate_available") is False
        and univ.get("price_source_required") == "Daily BTC/USD OHLCV"
        and univ.get("price_source_available_as_registered") is False
        and univ.get("hash_rate_name_probe_hits") == []
        and (spec.get("signal_semantics") or {}).get("decision_variable_available") is False
        and (spec.get("expected") or {}).get("status") == "not_computable"
        and (spec.get("strategy_domain") or {}).get("status") == "not_registered"
        and (spec.get("launch") or {}).get("attempts_launched") == 0
        and bool(spec.get("excerpt_source_map"))
    )
    add("C8", bool(ok8),
        "round-spec ids/gate=%s required_available=%s decisive=%s shrunk=%s "
        "network_available=%s price_source_available=%s matrix_items=%d"
        % (gate.get("outcome"), gate.get("required_data_available"),
           {k: matrix.get(k, {}).get("status") for k in DECISIVE_MATRIX_ITEMS},
           univ.get("universe_shrunk_to_local_list"),
           univ.get("network_dataset_available"),
           univ.get("price_source_available_as_registered"), len(matrix)))

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
        and (verdict.get("prerequisite") or {}).get("network_dataset_available") is False
    )
    add("C9", ok9, "verdict=%s run_id=%s layer=%s class=%s yield=%s run_ids=%s "
                   "network_dataset_available=%s"
        % (verdict.get("verdict"), verdict.get("run_id"), fail.get("layer"), fail.get("class"),
           yld.get("yield_decision"), verdict.get("evidence_run_ids"),
           (verdict.get("prerequisite") or {}).get("network_dataset_available")))

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
    #       invariant).
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

    return {"family_id": FAMILY, "round_id": ROUND, "task_id": TASK,
            "results_root": results_root, "raw_root": raw_root,
            "measured_raw": raw, "checks": checks,
            "overall": "PASS" if all(c["status"] == "PASS" for c in checks) else "FAIL"}


def _copy_tree(results_root, tmp):
    dst = os.path.join(tmp, FAMILY)
    os.makedirs(os.path.join(dst, "rounds"))
    shutil.copy(os.path.join(results_root, FAMILY, "family.json"),
                os.path.join(dst, "family.json"))
    shutil.copytree(os.path.join(results_root, FAMILY, "rounds", ROUND),
                    os.path.join(dst, "rounds", ROUND))
    return os.path.join(dst, "rounds", ROUND)


def self_test(results_root, raw_root):
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
        "network_dataset_matrix_item_claimed_present": lambda d: _tamper_spec(
            d, lambda s: _set_matrix_status(s, "network_data_bitcoin_hash_rate_daily",
                                            "PRESENT")),
        "network_dataset_claimed_available": lambda d: _tamper_spec(d, lambda s: s[
            "universe_registration"].update({
                "network_dataset_available": True,
                "point_in_time_hash_rate_available": True})),
        "universe_shrunk_to_the_local_four_contract_list": lambda d: _tamper_spec(d, lambda s: s[
            "universe_registration"].update({
                "universe_shrunk_to_local_list": True,
                "instrument_required": ["BTC", "ETH", "BNB", "SOL"]})),
        "btc_usd_source_claimed_present": lambda d: _tamper_spec(d, lambda s: s[
            "universe_registration"].update({
                "price_source_available_as_registered": True,
                "price_source_available_local": "Daily BTC/USD OHLCV"})),
        "searched_axis_mislabelled_user_fixed": lambda d: _tamper_spec(
            d, lambda s: s["dca_domain"].update({"spacing_pct_status": "USER_FIXED"})),
        "dca_grid_truncated_to_47": lambda d: _tamper_spec(
            d, lambda s: s["dca_domain"]["grid"].pop()),
        "zero_attempt_claim_replaced_by_an_attempt_dir": lambda d: _fabricate_attempt(
            d, name="INCOMPLETE"),
    }
    results = []
    ok = True
    for name, mutate in variants.items():
        tmp = tempfile.mkdtemp(prefix="xq-hashribbon-prereq-selftest-")
        try:
            rdir = _copy_tree(results_root, tmp)
            mutate(rdir)
            res = run_checks(tmp, raw_root)
            refused = [c["id"] for c in res["checks"] if c["status"] == "FAIL"]
            results.append({"variant": name, "refused": bool(refused),
                            "failed_checks": refused})
            ok = ok and bool(refused)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return {"self_test": results, "overall": "PASS" if ok else "FAIL"}


def _kw_gz(path, row):
    with gzip.GzipFile(path, "wb", mtime=0) as gz:
        gz.write(json.dumps(row).encode() + b"\n")


def raw_fixture_control(results_root, raw_root):
    """Measurement-side non-vacuousness control.

    Build a temp raw tree that carries what this record would need: a Bitcoin network
    hash-rate dataset, a spot market on the same venue holding a BTC/USD daily series,
    a second venue, and a daily bar older than the card's registered raw start. The
    raw-side checks must flip to FAIL.
    """
    tmp = tempfile.mkdtemp(prefix="xq-hashribbon-prereq-rawfix-")
    try:
        fixture = os.path.join(tmp, "raw")
        shutil.copytree(raw_root, fixture,
                        ignore=shutil.ignore_patterns(".DS_Store", "__pycache__"))
        # the record's required network dataset
        d = os.path.join(fixture, "_ref", "bitcoin_network_hashrate")
        os.makedirs(d)
        _kw_gz(os.path.join(d, "bitcoin-network-hashrate-daily.jsonl.gz"),
               {"date": "2022-01-01", "network_hashrate_ths": "182000000"})
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
        expected = {"C1", "C3", "C4", "C5"}
        return {"raw_fixture_control": {
            "failed_checks": sorted(failed), "expected": sorted(expected),
            "detail": {c["id"]: c["detail"] for c in res["checks"] if c["id"] in expected},
            "overall": res["overall"]},
            "overall": "PASS" if expected <= set(failed) else "FAIL"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def host_scan():
    """House-wide search for a Bitcoin network dataset (read-only)."""
    scanned, skipped = [], []
    for r in TMP_ROOTS:
        (scanned if os.path.isdir(r) else skipped).append(r)
    hard_hits, loose_hits = [], []
    for root in scanned:
        for dp, dn, fn in os.walk(root):
            rel = os.path.relpath(dp, root)
            if rel.count(os.sep) >= 5:
                dn[:] = []
                continue
            dn[:] = [d for d in dn if not d.startswith(".") and d not in SCAN_SKIP_DIRS]
            low = dp.lower()
            for f in fn:
                fl = f.lower()
                if not fl.endswith(SCAN_EXTS):
                    continue
                if any(t in fl for t in HASH_TOKENS):
                    hard_hits.append(os.path.join(dp, f))
                elif any(t in low for t in ("hashrate", "onchain", "hash_rate")):
                    hard_hits.append(os.path.join(dp, f))
                if "hash" in fl:
                    loose_hits.append(os.path.join(dp, f))

    def classify(paths):
        out = []
        for p in sorted(set(paths)):
            b = os.path.basename(p).lower()
            if any(k in b for k in INTEGRITY_MANIFEST_MARKERS):
                cls = "integrity_manifest_false_positive"
            elif b in OWN_ARTIFACT_BASENAMES:
                cls = "own_evidence_snapshot_false_positive"
            else:
                cls = "unclassified"
            out.append({"path": p, "classification": cls})
        return out

    hard_records, loose_records = classify(hard_hits), classify(loose_hits)
    return {"roots_scanned": scanned, "skipped_roots": skipped,
            "probe_tokens": list(HASH_TOKENS),
            "hashrate_like_files": hard_records,
            "loose_hash_substring_files": loose_records,
            "unclassified_hits": [r for r in hard_records
                                  if r["classification"] == "unclassified"],
            "loose_probe_unclassified": [r for r in loose_records
                                         if r["classification"] == "unclassified"],
            "note": ("the canonical raw is the only market-data source; a precise network-"
                     "dataset name probe over the whole house returns no file, and the loose "
                     "'hash' substring probe returns only sha256 integrity manifests plus this "
                     "card's own evidence snapshot")}


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
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--raw-fixture-control", action="store_true")
    ap.add_argument("--host-scan", action="store_true")
    args = ap.parse_args(argv)

    if args.host_scan:
        out = host_scan()
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0

    if not os.path.isdir(args.results_root) or not os.path.isdir(args.raw_root):
        print("usage error: results/raw root not mounted", file=sys.stderr)
        return 2

    if args.self_test:
        out = self_test(args.results_root, args.raw_root)
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0 if out["overall"] == "PASS" else 1

    if args.raw_fixture_control:
        out = raw_fixture_control(args.results_root, args.raw_root)
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0 if out["overall"] == "PASS" else 1

    out = run_checks(args.results_root, args.raw_root)
    if args.json:
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        for c in out["checks"]:
            print("%-4s %-4s %s" % (c["id"], c["status"], c["detail"]))
        print("overall:", out["overall"])
    return 0 if out["overall"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
