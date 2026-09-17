#!/usr/bin/env python3
"""Prerequisite-gate read-back checker for the family

    crypto-low-price-anchor-cross-sectional-reversal-2026-09-01

Card t_7347811a terminalises this family as TECHNICAL_INCOMPLETE because the
record's required data is not in the canonical raw:

  * the record's object is a **cross-sectional portfolio sort over a
    point-in-time cryptocurrency universe** ("Universe: cryptocurrencies in a
    cross-sectional portfolio setting"), with the **lowest price inside each
    formation window** as the behavioural anchor. Its own Required-data list
    asks for point-in-time universe membership, market-capitalization and
    liquidity fields for universe controls, delisting/death histories to avoid
    survivorship bias, trading-volume and spread/cost data, and a 24/7
    timestamp convention - and, for a modern exchange-level replication,
    venue-level spot or perpetual OHLCV "with explicit listing/delisting
    timestamps";
  * the canonical raw holds **one venue (BINANCE) of USD-M perpetuals and four
    fixed contracts** (BTCUSDT/ETHUSDT/BNBUSDT/SOLUSDT), OHLCV-only klines on
    seven intervals, funding and instrument metadata. Prices are therefore
    sufficient to compute a formation-window minimum for *those four contracts*
    - and for nothing else. There is no universe-membership dataset, no
    listing/delisting field, no market-cap or liquidity dataset, no
    spread/order-book surface and no second venue;
  * the four local contracts are 2026-listed majors that exist over the whole
    window: a cross-section built from them is survivor-only. The card forbids
    exactly that move ("不得縮減 universe 以硬造可執行性"), and the record's
    falsification battery (items 1, 4, 8) is defined on the absent surfaces.

This script exists so an independent reader can re-derive that determination
from the live filesystem instead of trusting prose:

  * C1-C4 re-measure the canonical raw: the store identity (venue / market type
    / symbols / intervals as the store itself documents them), the instrument
    surface (ids, types, field names, fee/tick metadata, absence of
    listing/market-cap fields), the kline surface (symbol dirs, interval set,
    dataset count, row shape, 1d window and UTC grid) and the funding surface;
  * C5-C6 probe the whole tree at full depth by entry name and by payload
    content for the tokens a point-in-time-universe / market-cap / liquidity /
    spread / second-venue dataset would have to carry, and assert every hit is
    classified;
  * C7-C9 assert the record's registered decision surfaces (universe
    membership, delisting history, market-cap and liquidity controls,
    spread/cost, venue fragmentation) are absent or not series-shaped, and that
    the store's own schema documents no such dataset family;
  * C10 asserts the cross-section the raw can offer is exactly the four fixed
    contracts and that the published round did NOT shrink the registered
    universe to them;
  * C11-C15 re-read the round's immutable artifacts and assert they state
    exactly the contract-mandated terminal values (verdict, layer, class, yield
    decision, zero attempts, null run_id), that nothing was ever submitted (no
    run-spec, no attempt directory, no terminal sentinel), that the DCA
    registration still carries the contract 7.2 v1.3.1 provenance classes plus
    the complete 48-cell product, and that the coverage / survivor surface is
    empty by construction;
  * C16-C17 re-measure the host's non-canonical stores: the machine's own PIT
    evidence stores declare **zero** supported daily membership cells and zero
    point-in-time-supported assets (their own gates class them DATA_BLOCKED /
    BOUNDED), the widest host price panel is a 12-symbol *derived* daily panel
    whose own provenance document says the list is not a point-in-time universe
    and cannot claim survivorship-free results, and no measured store carries a
    market-cap, spread or second-venue series;
  * C18-C20 assert the round is bound to this card and family, that the failure
    taxonomy was kept separated (infrastructure, not science), and that the
    falsification battery was neither lowered nor trimmed;
  * `--verify-verbatim` resolves every `*_verbatim` leaf of the persisted
    round-spec against its declared source (card / record / contract / footer)
    and re-checks the excerpt digest map, independent of the authoring run.

Read-only: it never writes inside the results tree or the raw tree. `--self-test`
builds tampered copies in fresh temp directories and asserts the checker refuses
them (non-vacuousness control). `--raw-fixture-control` builds a temp raw tree
carrying what this record would need (a daily point-in-time universe-membership
table, a market-cap + liquidity panel, a delisting history, a spread dataset and
a second venue) and asserts the raw-side checks flip to FAIL. `--host-scan`
re-runs the house-wide search for such surfaces. Every temp tree is removed
afterwards.

Usage:
    python3 runtime/crypto_low_price_anchor_cross_sectional_reversal_prerequisite_check.py [--json]
    python3 runtime/crypto_low_price_anchor_cross_sectional_reversal_prerequisite_check.py --measure-only
    python3 runtime/crypto_low_price_anchor_cross_sectional_reversal_prerequisite_check.py --verify-verbatim
    python3 runtime/crypto_low_price_anchor_cross_sectional_reversal_prerequisite_check.py --self-test
    python3 runtime/crypto_low_price_anchor_cross_sectional_reversal_prerequisite_check.py --raw-fixture-control
    python3 runtime/crypto_low_price_anchor_cross_sectional_reversal_prerequisite_check.py --host-scan

Exit codes: 0 = all checks PASS, 1 = at least one FAIL, 2 = usage error.
"""
import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone

FAMILY = "crypto-low-price-anchor-cross-sectional-reversal-2026-09-01"
ROUND = FAMILY + "-r1"
TASK = "t_7347811a"
BOARD = "quant-strategy-research"
DEFAULT_RESULTS = "/Volumes/ExpansionDrive/qlib-results"
DEFAULT_RAW = "/Volumes/ExpansionDrive/market-data-raw"
DEFAULT_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_RECORD = os.path.join(os.path.expanduser("~"), ".hermes/wiki/quant", FAMILY + ".md")
PROBE_MAX_DEPTH = 8

# Tokens a point-in-time universe / market-cap / liquidity / cost / multi-venue
# surface would have to carry. Ticker and vocabulary tokens are matched with
# word boundaries everywhere: a bare substring scan would fire 'listing' on
# 'enlisting', 'spot' on 'Spotlight' and 'supply' on 'supplies'.
PROBE_PATTERNS = {
    # ---- point-in-time universe / membership / lifecycle
    "universe": r"\buniverse\b",
    "universe_membership": r"\buniverse[-_ ]membership\b",
    "membership": r"\bmembership\b",
    "survivorship": r"\bsurvivorship\b",
    "point_in_time": r"\bpoint[-_ ]in[-_ ]time\b",
    "pit": r"\bpit\b",
    "exchange_info": r"\bexchange[-_ ]?info(rmation)?\b",
    "listing": r"\blistings?\b",
    "delist": r"\bdelist(ing|ed|s)?\b",
    "onboard": r"\bonboard(ing|ed)?\b",
    # ---- universe controls: size and liquidity
    "market_cap": r"\bmarket[-_ ]?cap(italization)?\b",
    "mcap": r"\bmcap\b",
    "circulating_supply": r"\bcirculating[-_ ]supply\b",
    "supply": r"\bsupply\b",
    "liquidity": r"\bliquidity\b",
    "turnover": r"\bturnover\b",
    "quote_volume": r"\bquote[-_ ]volume\b",
    "num_trades": r"\bnum_trades\b|\bnumber[-_ ]of[-_ ]trades\b|\btrade[-_ ]count\b",
    # ---- execution realism: spread / book / costs
    "spread": r"\bspread\b",
    "bid_ask": r"\bbid[-_ ]?ask\b|\bbidprice\b",
    "orderbook": r"\border[-_ ]?book\b",
    "book_ticker": r"\bbook[-_ ]?ticker\b",
    # ---- venue fragmentation
    "spot": r"\bspot\b",
    "okx": r"\bokx\b", "bybit": r"\bbybit\b", "coinbase": r"\bcoinbase\b",
    "kraken": r"\bkraken\b", "bitfinex": r"\bbitfinex\b",
    # ---- corporate-action / token-migration and vendor surfaces
    "adjusted": r"\badjust(ed|ment)\b",
    "token_migration": r"\b(token[-_ ]migration|redenominat|contract[-_ ]conversion)\b",
    "coingecko": r"\bcoingecko\b", "coinmarketcap": r"\bcoinmarketcap\b",
    "binance_vision": r"\bbinance[ ._-]?vision\b|\bdata\.binance\.vision\b",
    "bars": r"\bbars?\b",
}
COMPILED = {k: re.compile(v, re.I) for k, v in PROBE_PATTERNS.items()}

# Tokens whose *presence as a series* in the canonical raw would mean the
# required surface exists. Every hit found by the probe must be classified
# (or the probe reports it as unexplained and the check fails).
PIT_TOKENS = ("universe", "universe_membership", "membership", "survivorship", "point_in_time",
              "pit", "exchange_info", "listing", "delist", "onboard")
SIZE_TOKENS = ("market_cap", "mcap", "circulating_supply", "supply")
LIQ_TOKENS = ("liquidity", "turnover", "quote_volume", "num_trades")
COST_TOKENS = ("spread", "bid_ask", "orderbook", "book_ticker")
VENUE_TOKENS = ("spot", "okx", "bybit", "coinbase", "kraken", "bitfinex")
ADJ_TOKENS = ("adjusted", "token_migration", "coingecko", "coinmarketcap")

# Classified probe hits: token -> {relative path (or path suffix): reason}. A hit
# on any other path is unexplained and fails C5/C6.
RAW_NAME_HIT_CLASSES = {}
RAW_PAYLOAD_HIT_CLASSES = {
    "membership": {
        "_meta/INVENTORY.md": "the store's migration inventory, listing *other* (non-migrated) stores by "
                              "path: 'a1-1-phase9-pit-membership/raw/** | S3 XML listings (not data) ...'",
    },
    "pit": {
        "_meta/INVENTORY.md": "same inventory row: the path token 'pit' inside 'a1-1-phase9-pit-membership'",
    },
    "exchange_info": {
        "_meta/INVENTORY.md": "same inventory row: 'old data.binance.vision probes' / store-path prose",
    },
    "listing": {
        "_meta/INVENTORY.md": "same inventory row: 'S3 XML listings (not data)' - bucket listings, not "
                              "instrument listing dates",
    },
    "book_ticker": {
        "_meta/INVENTORY.md": "a Spotlight/mdfind sweep token list in the inventory's method paragraph "
                              "('... aggTrade / bookTicker / openInterest ...'), i.e. a description of a "
                              "search, not an order-book series",
    },
    "bybit": {
        "_meta/INVENTORY.md": "the inventory's 'other stores (not migrated)' table naming LEAN sample "
                              "datasets ('... plus bitfinex/coinbase/bybit/dydx crypto')",
    },
    "coinbase": {
        "_meta/INVENTORY.md": "same not-migrated table row naming LEAN sample datasets",
    },
    "bitfinex": {
        "_meta/INVENTORY.md": "same not-migrated table row naming LEAN sample datasets",
    },
    "binance_vision": {
        "_meta/INVENTORY.md": "the same table row's provenance prose ('old data.binance.vision probes'); the "
                              "inventory states no Vision archive was ever retained locally",
    },
    "bars": {
        "_meta/SCHEMA.md": "the store's own schema prose describing bar partitioning ('Bar interval = "
                           "interval in the path')",
        "_meta/INVENTORY.md": "the store's migration inventory prose ('parquet bars (engine store)')",
        "_meta/TRANSCODE_MANIFEST.json": "the per-file provenance manifest naming the transcoded bar files",
        "_tools/README.md": "the updater's own README describing the bar store",
        "_tools/market_data_sync.py": "the standalone updater's own source code, which calls kline rows "
                                      "'bars' in its docstrings and assertions",
    },
    "quote_volume": {
        "_meta/SCHEMA.md": "the schema's Known coverage limit paragraph, which states that quote_volume, "
                           "trade count and taker-buy splits are **not** present",
    },
    "num_trades": {
        "_meta/SCHEMA.md": "the same Known coverage limit paragraph: 'trade count' is documented absent",
    },
}

# Host data-extension hits: every hit is an artifact of a *measured* research /
# PIT store, and each class states why it still does not satisfy the record's
# requirement. A hit matching no class fails C17.
HOST_DATA_HIT_CLASSES = [
    {"suffix": "a1-1-phase9-pit-membership-20260830/membership_cells.jsonl",
     "class": "pit_membership_cells_declared_unsupported",
     "why": "the machine's own PIT-membership attempt: 14,760 daily cells for 12 declared instruments, "
            "every one `membership_status=unknown`, `supported=false`; its gate_handoff.json classifies the "
            "phase DATA_BLOCKED with pit_supported_asset_count=0"},
    {"suffix": "a1-1-phase9-pit-membership-20260830/membership_observations.csv",
     "class": "pit_membership_observations_declared_unsupported",
     "why": "59 evidence rows behind the same attempt (present-state snapshots + official boundary notices); "
            "its own manifest reports daily_supported_rows=0"},
    {"suffix": "a1-usdm-pit-lifecycle-20260830/lifecycle_events.csv",
     "class": "binance_lifecycle_event_points_declared_bounded",
     "why": "772 official USD-M lifecycle notices (listing 736 / delisting 22 / conversion 11 / migration 2 / "
            "suspension 1) whose own gate classifies the evidence BOUNDED with pit_supported_asset_count=0 and "
            "states an event point is never projected into a daily valid interval; no prices exist for those "
            "symbols"},
    {"suffix": "a1-2-prospective-pit-foundation-20260830/capture_manifest.jsonl",
     "class": "present_state_exchangeinfo_snapshot",
     "why": "prospective capture manifest of /fapi/v1/exchangeInfo bytes; the store's own contract marks "
            "current-state snapshots cross-check only, never historical backfill"},
    {"suffix": "a1-2-prospective-pit-foundation-20260830/snapshot_observations.jsonl",
     "class": "present_state_exchangeinfo_snapshot",
     "why": "same present-state snapshot store (observation ledger)"},
    {"suffix": "a1-3-prospective-pit-cohort-20260830/cohort_manifest.jsonl",
     "class": "present_state_exchangeinfo_snapshot",
     "why": "cohort-level present-state snapshot manifest; explicit no-backfill policy"},
    {"suffix": "a1-3-prospective-pit-cohort-20260830/snapshot_observations.jsonl",
     "class": "present_state_exchangeinfo_snapshot",
     "why": "same cohort snapshot store (observation ledger)"},
    {"suffix": "phase7-alpha-research/data/derived/real_daily.csv",
     "class": "derived_research_price_panel_12_fixed_symbols_not_pit",
     "why": "a 12-symbol fixed daily panel (Binance USD-M 1d, 2022-01-01..2025-05-14, 14,760 rows, with "
            "quote_volume and trade count). Its own data_provenance.md states the current exchangeInfo list "
            "'不等於 point-in-time universe' and that results '不能宣稱無 survivorship bias'; it is 1d only, "
            "carries no market cap, and is not the canonical raw"},
    {"suffix": "phase7-alpha-research/data/derived/quote_volume_wide.csv",
     "class": "derived_research_price_panel_12_fixed_symbols_not_pit",
     "why": "the wide (date x symbol) quote-volume form of the same 12-symbol derived panel; same "
            "non-point-in-time provenance, 1d only, no market cap, not the canonical raw"},
    {"suffix": "phase10-pit-bitemporal/data/pit_events.sqlite",
     "class": "phase10_pit_contract_demonstration_spot_example",
     "why": "the Phase 10 bitemporal/PIT contract demonstration: 12 records, 19 lifecycle events, 6 "
            "snapshots for *spot* symbols (KGST, USDS, ...), with snapshot counts 'eligible 0-1 / unknown "
            "12'; a capability example, not a USD-M point-in-time universe panel and not price data"},
    {"suffix": "phase10-pit-bitemporal/data/real_lifecycle_evidence.csv",
     "class": "binance_spot_delisting_sample_prose_scale",
     "why": "8 hand-built Binance *spot* delisting notice records for the Phase 10 research contract; no "
            "USD-M price panel and no continuous membership interval"},
    {"suffix": "alpha-strategy-research/coverage_manifest.csv",
     "class": "research_corpus_index",
     "why": "an index of strategy markdown candidates; the tokens appear inside candidate file *titles* "
            "(liquidity / spread / supply / adjusted), not in any market series"},
]

TEXT_EXTS = (".csv", ".jsonl", ".gz", ".json", ".txt", ".md", ".tsv", ".raw",
             ".py", ".sh", ".yaml", ".yml")
DATA_EXTS = (".csv", ".jsonl", ".gz", ".parquet", ".bin", ".feather", ".h5",
             ".npy", ".arrow", ".db", ".sqlite", ".zst")

HOST_ROOTS = [
    "/Users/hong/workspace/a1-1-phase9-pit-membership-20260830",
    "/Users/hong/workspace/a1-usdm-pit-lifecycle-20260830",
    "/Users/hong/workspace/a1-2-prospective-pit-foundation-20260830",
    "/Users/hong/workspace/a1-3-prospective-pit-cohort-20260830",
    "/Users/hong/workspace/phase9-cross-sectional-factors",
    "/Users/hong/workspace/phase7-alpha-research",
    "/Users/hong/workspace/phase3-portfolio-risk",
    "/Users/hong/workspace/phase4-market-microstructure",
    "/Users/hong/workspace/phase5-crypto-derivatives",
    "/Users/hong/workspace/phase10-pit-bitemporal",
    "/Users/hong/workspace/phase12-l2-l3-execution-tca",
    "/Users/hong/workspace/alpha-strategy-research",
    "/Users/hong/workspace/btc-relative-entry-score",
    "/Users/hong/workspace/quant-runtime-pipeline",
    "/Users/hong/workspace/t_4b5afaa9-evidence",
    "/Volumes/ExpansionDrive/daily-crypto-brief",
    os.path.join(DEFAULT_RESULTS, "_handoff/bodies"),
    os.path.join(os.path.expanduser("~"), ".hermes/wiki/quant"),
]

# Host-side PIT evidence stores: their own gates, re-read rather than summarised.
PIT_GATE_SOURCES = {
    "membership_gate": "/Users/hong/workspace/a1-1-phase9-pit-membership-20260830/gate_handoff.json",
    "membership_cells": "/Users/hong/workspace/a1-1-phase9-pit-membership-20260830/membership_cells.jsonl",
    "lifecycle_gate": "/Users/hong/workspace/a1-usdm-pit-lifecycle-20260830/pit_handoff.json",
    "lifecycle_events": "/Users/hong/workspace/a1-usdm-pit-lifecycle-20260830/lifecycle_events.csv",
    "phase7_panel": "/Users/hong/workspace/phase7-alpha-research/data/derived/real_daily.csv",
    "phase7_provenance": "/Users/hong/workspace/phase7-alpha-research/data_provenance.md",
}
PHASE7_NON_PIT_STATEMENT = "不等於 point-in-time universe"


def _load_json(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return json.load(fh)


def _ls(path):
    if not os.path.isdir(path):
        return []
    return sorted(n for n in os.listdir(path) if not n.startswith("."))


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def _sha256_text(text):
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_text(path, chunk=None):
    if path.endswith(".gz"):
        with gzip.open(path, "rt", errors="replace") as fh:
            return fh.read() if chunk is None else fh.read(chunk)
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read() if chunk is None else fh.read(chunk)


def _iso(ms):
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _norm(text):
    """Normalise separator style before probe matching: a snake_case or kebab-case
    column name is still the series it names (`bid_ask_spread_bps` must match
    `spread`). Applied to entry names, payload text and field names alike."""
    return re.sub(r"[_\-]", " ", text)


def _iter_files(root, max_depth=PROBE_MAX_DEPTH, cap=60000):
    """Walk a host root for the store-wide scan. Depth and per-root file cap are
    disclosed in the evidence."""
    base = os.path.abspath(root).rstrip(os.sep).count(os.sep)
    n = 0
    for dirpath, dirnames, filenames in os.walk(root):
        if dirpath.count(os.sep) - base >= max_depth:
            dirnames[:] = []
        dirnames.sort()
        for name in sorted(filenames):
            if name == ".DS_Store":
                continue
            yield os.path.join(dirpath, name)
            n += 1
            if n >= cap:
                return


def _all_entries(root, max_depth=PROBE_MAX_DEPTH):
    base = os.path.abspath(root).rstrip(os.sep).count(os.sep)
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        if dirpath.count(os.sep) - base >= max_depth:
            dirnames[:] = []
        dirnames.sort()
        for n in sorted(dirnames):
            out.append(os.path.relpath(os.path.join(dirpath, n), root) + "/")
        for n in sorted(filenames):
            if n != ".DS_Store":
                out.append(os.path.relpath(os.path.join(dirpath, n), root))
    return out


def _classify_hits(hit_map, classes, unexplained_out, classified_out, mode):
    """Every probe hit must resolve to a declared class; otherwise it is unexplained."""
    for tok, lst in hit_map.items():
        allowed = classes.get(tok, {})
        for rel in lst:
            is_series = mode == "data"
            hit = {"token": tok, "entry": rel, "series_shaped": is_series}
            reason = allowed.get(rel)
            if reason is None:
                for k, v in allowed.items():
                    if rel.endswith(k):
                        reason = v
                        break
            if reason is None:
                unexplained_out.append(hit)
            else:
                classified_out.append({**hit, "why": reason})
    return None


def _token_field_scan(field_names):
    """Tokens that appear as an actual column/field name (series-shaped)."""
    return sorted({k for k, rx in COMPILED.items()
                    if any(rx.search(_norm(f)) for f in field_names)})


def measure_raw(raw, fresh=False):
    """Independent re-measurement of the canonical raw. No artifact is trusted."""
    rep = {"raw_root": raw}
    meta = os.path.join(raw, "_meta")
    rep["store_top_level"] = _ls(raw)
    rep["binance_subdirs"] = _ls(os.path.join(raw, "binance"))
    rep["usdm_subdirs"] = _ls(os.path.join(raw, "binance/usdm"))
    cfg = _load_json(os.path.join(meta, "CONFIG.json"))
    rep["documented_venue"] = cfg.get("venue")
    rep["documented_market_type"] = cfg.get("market_type")
    rep["documented_symbols"] = sorted(cfg.get("symbols", []))
    rep["documented_intervals"] = sorted(cfg.get("intervals", []))
    schema = _read_text(os.path.join(meta, "SCHEMA.md"))
    rep["schema_bytes"] = len(schema)
    rep["schema_dataset_sections"] = re.findall(r"^## Dataset: (.+)$", schema, re.M)
    rep["schema_documents_universe_or_mcap_dataset"] = any(
        re.search(r"(universe|membership|market[-_ ]?cap|liquidity|turnover|listing|delist|survivorship)",
                  s, re.I) for s in rep["schema_dataset_sections"])
    rep["schema_quote_volume_documented_absent"] = bool(
        re.search(r"quote_volume[^.]{0,220}\bnot\b[^.]{0,40}present", schema, re.I)
        or re.search(r"\bnot\b[^.]{0,40}present[^.]{0,220}quote_volume", schema, re.I))
    rep["schema_universe_mentions"] = sorted({m.group(0).lower()
                                              for m in re.finditer(r"\buniverse\b", schema, re.I)})
    rep["schema_listing_mentions"] = sorted({m.group(0).lower()
                                             for m in re.finditer(r"\blistings?\b", schema, re.I)})

    inst = _load_json(os.path.join(raw, "binance/usdm/instruments/usdm-perp-instruments.json"))
    inner = inst["instruments"]
    rows = inner if isinstance(inner, list) else list(inner.values())
    flds = [r["fields"] for r in rows]
    rep["instrument_count"] = len(rows)
    rep["instrument_ids"] = sorted(f["id"] for f in flds)
    rep["instrument_base_currencies"] = sorted({f["base_currency"] for f in flds})
    rep["instrument_types"] = sorted({f["type"] for f in flds})
    rep["instrument_field_names"] = sorted(flds[0].keys())
    rep["instrument_listing_fields"] = [f for f in sorted(flds[0].keys())
                                        if re.search(r"(listing|delist|onboard|expiry|delivery|"
                                                     r"valid_from|valid_to)", f, re.I)]
    rep["instrument_size_fields"] = [f for f in sorted(flds[0].keys())
                                     if re.search(r"(market[-_ ]?cap|mcap|supply|liquidity|"
                                                  r"turnover|float)", f, re.I)]
    rep["instrument_fee_fields"] = [f for f in ("maker_fee", "taker_fee")
                                    if f in flds[0]]
    rep["instrument_tick_sizes"] = {f["id"]: f.get("price_increment") for f in flds}

    kl = os.path.join(raw, "binance/usdm/klines")
    rep["klines_symbol_dirs"] = _ls(kl)
    rep["cross_section_size"] = len(rep["klines_symbol_dirs"])
    rep["klines_intervals"] = _ls(os.path.join(kl, rep["klines_symbol_dirs"][0])) if rep["klines_symbol_dirs"] else []
    rep["klines_dataset_dirs"] = len(rep["klines_symbol_dirs"]) * len(rep["klines_intervals"])
    rep["klines_interval_set_full"] = sorted({i for s in rep["klines_symbol_dirs"]
                                              for i in _ls(os.path.join(kl, s))})
    if rep["klines_symbol_dirs"]:
        d0 = os.path.join(kl, "BTCUSDT", "1d")
        fs = _ls(d0)
        rep["klines_1d_file_count"] = len(fs)
        rows_all = [json.loads(x) for f in fs for x in _read_text(os.path.join(d0, f)).splitlines()]
        rep["klines_1d_rows"] = len(rows_all)
        rep["klines_1d_window_utc"] = [_iso(rows_all[0]["open_time_ms"]), _iso(rows_all[-1]["open_time_ms"])]
        rep["klines_1d_open_step_seconds"] = ((rows_all[-1]["open_time_ms"] - rows_all[0]["open_time_ms"])
                                              / 1000.0 / (len(rows_all) - 1))
        rep["klines_1d_row_keys"] = sorted(rows_all[0].keys())
        rep["klines_columns_missing_vs_venue_12"] = [c for c in ("quote_volume", "num_trades",
                                                                 "taker_buy_base_volume")
                                                     if c not in rows_all[0]]
        d5 = os.path.join(kl, "BTCUSDT", "5m")
        rows5 = [json.loads(x) for x in _read_text(os.path.join(d5, _ls(d5)[0])).splitlines()]
        rep["klines_finest_step_seconds"] = ((rows5[1]["open_time_ms"] - rows5[0]["open_time_ms"]) / 1000.0)
        rep["klines_finest_row_keys"] = sorted(rows5[0].keys())

    fd = os.path.join(raw, "binance/usdm/funding")
    rep["funding_symbol_dirs"] = _ls(fd)
    if rep["funding_symbol_dirs"]:
        p = os.path.join(fd, "BTCUSDT", "BTCUSDT-funding.jsonl.gz")
        lines = _read_text(p).splitlines()
        first, last = json.loads(lines[0]), json.loads(lines[-1])
        rep["funding_row_keys"] = sorted(first.keys())
        rep["funding_venues"] = sorted({json.loads(x).get("venue") for x in lines[:50]})
        rep["funding_window_utc"] = [_iso(first["funding_time_ms"]), _iso(last["funding_time_ms"])]
        rep["funding_rows"] = len(lines)

    # ---- whole-tree probes (entry names, then payload content)
    entries = _all_entries(raw)
    rep["raw_entry_count"] = len(entries)
    name_hits = {}
    for e in entries:
        low = _norm(e).lower()
        for t in COMPILED:
            if t in low:
                name_hits.setdefault(t, []).append(e)
    rep["probe_tokens_tested"] = len(COMPILED)
    rep["probe_hit_counts_entry_names"] = {k: len(v) for k, v in name_hits.items()}
    rep["probe_hits_in_entry_names"] = {k: v[:8] for k, v in name_hits.items()}
    name_unexplained, name_classified = [], []
    _classify_hits(name_hits, RAW_NAME_HIT_CLASSES, name_unexplained, name_classified, "name")
    rep["probe_classified_name_hits"] = name_classified
    rep["probe_hits_unexplained_name"] = sorted({h["token"] for h in name_unexplained})

    files = [e for e in entries if not e.endswith("/")]
    tot_bytes = 0
    token_files = {}
    for e in files:
        p = os.path.join(raw, e)
        try:
            tot_bytes += os.path.getsize(p)
        except OSError:
            pass
        if os.path.splitext(p)[1].lower() not in TEXT_EXTS:
            continue
        try:
            txt = _read_text(p, 400000)
        except Exception:  # noqa: BLE001
            continue
        probe_txt = _norm(txt)
        for k, rx in COMPILED.items():
            if rx.search(probe_txt):
                token_files.setdefault(k, []).append(e)
    rep["payload_scan_files"] = len(files)
    rep["payload_scan_bytes"] = tot_bytes
    rep["payload_hit_counts"] = {k: len(v) for k, v in token_files.items()}
    rep["payload_token_files"] = {k: v[:6] for k, v in token_files.items()}
    pay_unexplained, pay_classified = [], []
    _classify_hits(token_files, RAW_PAYLOAD_HIT_CLASSES, pay_unexplained, pay_classified, "payload")
    rep["probe_classified_payload_hits"] = pay_classified
    rep["probe_hits_unexplained_payload"] = sorted({h["token"] for h in pay_unexplained})
    rep["payload_data_ext_token_files"] = {
        k: [e for e in v if os.path.splitext(e)[1].lower() in DATA_EXTS] for k, v in token_files.items()}
    rep["probe_payload_data_ext_hits"] = sum(len(v) for v in rep["payload_data_ext_token_files"].values())

    # a *series* is only present if the token appears in a data-shaped place:
    # a non-prose payload file, or a column/field name.
    series_tokens = {k for k, v in rep["payload_data_ext_token_files"].items() if v}
    column_tokens = set(_token_field_scan(rep.get("klines_1d_row_keys", [])
                                          + rep.get("klines_finest_row_keys", [])
                                          + rep.get("funding_row_keys", [])
                                          + rep.get("instrument_field_names", [])))
    rep["series_shaped_tokens"] = sorted(series_tokens | column_tokens)
    rep["universe_membership_series_present"] = bool(
        (series_tokens | column_tokens) & {"universe", "universe_membership", "membership", "survivorship",
                                           "point_in_time", "pit", "exchange_info"})
    rep["delisting_history_series_present"] = bool(
        (series_tokens | column_tokens) & {"listing", "delist", "onboard"}
        or rep["instrument_listing_fields"])
    rep["market_cap_series_present"] = bool((series_tokens | column_tokens) & {"market_cap", "mcap",
                                                                              "circulating_supply", "supply"}
                                            or rep["instrument_size_fields"])
    rep["liquidity_series_present"] = bool((series_tokens | column_tokens)
                                           & {"liquidity", "turnover", "quote_volume", "num_trades"})
    rep["spread_series_present"] = bool((series_tokens | column_tokens)
                                        & {"spread", "bid_ask", "orderbook", "book_ticker"})
    rep["second_venue_series_present"] = bool((series_tokens) & set(VENUE_TOKENS))
    rep["adjustment_series_present"] = bool((series_tokens | column_tokens) & set(ADJ_TOKENS))
    rep["venue_or_vendor_dirs"] = [d for d in rep["store_top_level"]
                                   if re.search(r"(okx|bybit|coinbase|kraken|bitfinex|coingecko|"
                                                r"coinmarketcap|tardis|kaiko)", d, re.I)]
    rep["wide_price_panel_present"] = bool(
        set(rep["klines_symbol_dirs"]) - {"BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"})
    # the one registered requirement the raw does cover: bars fine enough to
    # locate a formation window's exact minimum, for the four local contracts.
    rep["formation_window_prices_computable_local"] = bool(
        rep["klines_1d_rows"] > 1500 and rep["klines_finest_step_seconds"] == 300.0)
    rep["price_panel_symbols"] = rep["klines_symbol_dirs"]
    rep["required_data_available"] = False
    return rep


def _read_pit_gates(sources=None):
    """Re-read the host's own PIT evidence gates. Nothing here is trusted prose."""
    sources = sources or PIT_GATE_SOURCES
    out = {"sources": sources}
    g = _load_json(sources["membership_gate"])
    cov = g.get("coverage", {})
    out["membership_gate_classification"] = g.get("classification")
    out["membership_gate_pit_supported_asset_count"] = g.get("pit_supported_asset_count")
    out["membership_gate_supported_cells"] = cov.get("supported_cells")
    out["membership_gate_total_cells"] = cov.get("total_cells")
    out["membership_gate_unknown_cells"] = cov.get("unknown_cells")
    out["membership_gate_next_gate"] = g.get("next_gate")
    supported = total = 0
    with open(sources["membership_cells"], encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            total += 1
            if json.loads(line).get("supported") is True:
                supported += 1
    out["membership_cells_rows"] = total
    out["membership_cells_supported"] = supported
    lc = _load_json(sources["lifecycle_gate"])
    out["lifecycle_classification"] = lc.get("classification")
    out["lifecycle_pit_supported_asset_count"] = lc.get("pit_supported_asset_count")
    out["lifecycle_daily_membership_pit_supported_asset_count"] = lc.get(
        "daily_membership_pit_supported_asset_count")
    out["lifecycle_bounded_event_point_asset_count"] = lc.get("bounded_event_point_asset_count")
    out["lifecycle_bounded_event_point_event_count"] = lc.get("bounded_event_point_event_count")
    out["lifecycle_current_exchange_info_policy"] = lc.get("current_exchange_info_policy")
    ev = _load_json(sources["lifecycle_gate"])
    out["lifecycle_event_points_only"] = bool(
        isinstance(ev.get("bounded_event_point_asset_count"), int))
    import csv as _csv
    types = {}
    scopes = {}
    symbols = set()
    rows = 0
    with open(sources["lifecycle_events"], encoding="utf-8", errors="replace") as fh:
        for r in _csv.DictReader(fh):
            rows += 1
            types[r.get("event_type")] = types.get(r.get("event_type"), 0) + 1
            scopes[r.get("support_scope")] = scopes.get(r.get("support_scope"), 0) + 1
            symbols.add(r.get("venue_symbol"))
    out["lifecycle_event_rows"] = rows
    out["lifecycle_event_type_counts"] = types
    out["lifecycle_event_support_scopes"] = scopes
    out["lifecycle_event_symbols"] = len(symbols)
    panel_rows = 0
    panel_symbols = set()
    panel_dates = set()
    panel_cols = []
    with open(sources["phase7_panel"], encoding="utf-8", errors="replace") as fh:
        for r in _csv.DictReader(fh):
            panel_rows += 1
            panel_symbols.add(r.get("symbol"))
            panel_dates.add(r.get("date"))
            if not panel_cols:
                panel_cols = sorted(r.keys())
    out["phase7_panel_rows"] = panel_rows
    out["phase7_panel_symbol_count"] = len(panel_symbols)
    out["phase7_panel_symbols"] = sorted(panel_symbols)
    out["phase7_panel_window"] = [min(panel_dates), max(panel_dates)] if panel_dates else []
    out["phase7_panel_columns"] = panel_cols
    prov = _read_text(sources["phase7_provenance"])
    out["phase7_provenance_declares_not_pit"] = PHASE7_NON_PIT_STATEMENT in prov
    out["phase7_provenance_survivorship_statement"] = bool(
        re.search(r"不能宣稱無 survivorship bias", prov))
    return out


def measure_host_stores(roots=None, cap_per_root=60000):
    """Re-measure the host's non-canonical stores for the required surfaces."""
    roots = roots or HOST_ROOTS
    rep = {"roots": {}, "totals": {}}
    tot_files = tot_hits = 0
    data_hits, prose_hits = [], []
    for root in roots:
        if not os.path.isdir(root):
            rep["roots"][root] = {"exists": False}
            continue
        n = 0
        hits = 0
        per_tok = {}
        for p in _iter_files(root, cap=cap_per_root):
            n += 1
            rel = os.path.relpath(p, root)
            toks = {k for k, rx in COMPILED.items() if rx.search(_norm(rel))}
            if os.path.splitext(p)[1].lower() in TEXT_EXTS:
                try:
                    txt = _read_text(p, 200000)
                except Exception:  # noqa: BLE001
                    txt = ""
                probe_txt = _norm(txt)
                toks |= {k for k, rx in COMPILED.items() if rx.search(probe_txt)}
            if not toks:
                continue
            hits += 1
            for t in toks:
                per_tok[t] = per_tok.get(t, 0) + 1
            rec = {"root": root, "path": rel, "tokens": sorted(toks),
                   "ext": os.path.splitext(p)[1].lower()}
            if rec["ext"] in DATA_EXTS:
                data_hits.append(rec)
            else:
                prose_hits.append(rec)
        rep["roots"][root] = {"exists": True, "files_scanned": n, "token_files": hits,
                              "token_file_counts": per_tok,
                              "data_ext_hits": sum(1 for d in data_hits if d["root"] == root)}
        tot_files += n
        tot_hits += hits
    classified, unclassified = [], []
    for d in data_hits:
        full = os.path.join(d["root"], d["path"])
        cls = None
        for c in HOST_DATA_HIT_CLASSES:
            if full.endswith(c["suffix"]):
                cls = c
                break
        if cls is None:
            unclassified.append(d)
        else:
            classified.append({**d, "class": cls["class"], "why": cls["why"]})
    rep["totals"] = {"roots": len(roots), "files_scanned": tot_files, "token_files": tot_hits,
                     "data_extension_hits": len(data_hits),
                     "prose_extension_hits": len(prose_hits),
                     "classified_data_hits": len(classified),
                     "unclassified_data_hits": len(unclassified)}
    rep["data_candidates"] = data_hits[:20]
    rep["classified_data_hits"] = classified
    rep["unclassified_data_candidates"] = unclassified
    rep["prose_hits_sample"] = prose_hits[:10]
    rep["pid_gates"] = _read_pit_gates()
    rep["any_store_carries_pit_membership_panel"] = bool(
        (rep["pid_gates"].get("membership_cells_supported") or 0) > 0
        or (rep["pid_gates"].get("lifecycle_pit_supported_asset_count") or 0) > 0)
    rep["any_store_carries_market_cap_or_liquidity_series"] = any(
        {"market_cap", "mcap", "circulating_supply", "liquidity", "turnover"} & set(d["tokens"])
        for d in unclassified)
    rep["any_store_carries_spread_or_book_series"] = any(
        {"spread", "bid_ask", "orderbook", "book_ticker"} & set(d["tokens"])
        for d in unclassified)
    rep["widest_host_price_panel_symbol_count"] = rep["pid_gates"].get("phase7_panel_symbol_count")
    rep["widest_host_price_panel_is_declared_non_pit"] = bool(
        rep["pid_gates"].get("phase7_provenance_declares_not_pit"))
    return rep


def _check(cid, name, ok, detail):
    return {"id": cid, "name": name, "ok": bool(ok), "detail": detail}


def _family_surfaces(results_root, family, round_id):
    fam_dir = os.path.join(results_root, family)
    round_dir = os.path.join(fam_dir, "rounds", round_id)
    return {
        "family_dir": fam_dir,
        "round_dir": round_dir,
        "family_json": os.path.join(fam_dir, "family.json"),
        "spec": os.path.join(round_dir, "round-spec.json"),
        "verdict": os.path.join(round_dir, "verdict.json"),
        "attempts_dir": os.path.join(round_dir, "attempts"),
        "round_dir_listing": _ls(round_dir),
        "family_dir_listing": _ls(fam_dir),
    }


def run_checks(results_root, raw_root, repo_root=None, family=FAMILY, round_id=ROUND,
               task=TASK, raw=None, stores=None, record_path=None):
    repo_root = repo_root or DEFAULT_REPO
    raw = raw if raw is not None else measure_raw(raw_root)
    stores = stores if stores is not None else measure_host_stores()
    sf = _family_surfaces(results_root, family, round_id)
    spec = _load_json(sf["spec"])
    verdict = _load_json(sf["verdict"])
    fam = _load_json(sf["family_json"])
    checks = []
    add = checks.append

    # ---- C1 store identity
    add(_check("C1", "canonical raw identity is a single crypto-perp venue carrying the three dataset families",
               raw["documented_venue"] == "BINANCE" and raw["documented_market_type"] == "usdm_perp"
               and set(raw["usdm_subdirs"]) >= {"funding", "instruments", "klines"}
               and "binance" in raw["store_top_level"],
               {"venue": raw["documented_venue"], "market_type": raw["documented_market_type"],
                "usdm_subdirs": raw["usdm_subdirs"], "symbols": raw["documented_symbols"]}))
    # ---- C2 instrument surface (fee/tick metadata present; listing/size fields absent)
    add(_check("C2", "instrument surface carries four crypto perpetuals, fee/tick metadata and no "
                     "listing or market-cap field",
               raw["instrument_count"] == 4
               and raw["instrument_types"] == ["CryptoPerpetual"]
               and raw["instrument_listing_fields"] == []
               and raw["instrument_size_fields"] == []
               and raw["instrument_fee_fields"] == ["maker_fee", "taker_fee"]
               and set(raw["documented_symbols"]) == {"BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"},
               {"ids": raw["instrument_ids"], "types": raw["instrument_types"],
                "listing_fields": raw["instrument_listing_fields"],
                "size_fields": raw["instrument_size_fields"],
                "fee_fields": raw["instrument_fee_fields"],
                "tick_sizes": raw["instrument_tick_sizes"]}))
    # ---- C3 kline surface: OHLCV-only, seven intervals, 28 datasets
    add(_check("C3", "kline surface is OHLCV-only on seven intervals with no universe/market-cap column",
               raw["klines_symbol_dirs"] == ["BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
               and raw["klines_interval_set_full"] == ["15m", "1d", "1h", "1w", "30m", "4h", "5m"]
               and raw["klines_dataset_dirs"] == 28
               and raw["klines_1d_open_step_seconds"] == 86400.0
               and raw["klines_1d_rows"] > 1500
               and raw["klines_1d_row_keys"] == ["close", "close_time_ms", "high", "low", "open",
                                                 "open_time_ms", "volume"],
               {"symbols": raw["klines_symbol_dirs"], "intervals": raw["klines_interval_set_full"],
                "datasets": raw["klines_dataset_dirs"], "row_keys": raw["klines_1d_row_keys"],
                "1d_window": raw["klines_1d_window_utc"], "1d_rows": raw["klines_1d_rows"],
                "step_s": raw["klines_1d_open_step_seconds"],
                "finest_step_s": raw["klines_finest_step_seconds"]}))
    # ---- C4 funding surface
    add(_check("C4", "funding surface is perp funding only, with no universe/membership field",
               raw["funding_symbol_dirs"] == ["BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
               and raw["funding_venues"] == ["BINANCE"]
               and raw["funding_rows"] > 5000,
               {"symbols": raw["funding_symbol_dirs"], "keys": raw["funding_row_keys"],
                "venues": raw["funding_venues"], "window": raw["funding_window_utc"],
                "rows": raw["funding_rows"]}))
    # ---- C5 entry-name probe
    add(_check("C5", "entry-name probe over the whole raw tree finds no universe/market-cap/liquidity/"
                     "spread token",
               raw["probe_hits_unexplained_name"] == [] and raw["raw_entry_count"] > 1000,
               {"entries": raw["raw_entry_count"], "tokens": raw["probe_tokens_tested"],
                "hit_counts": raw["probe_hit_counts_entry_names"],
                "unexplained": raw["probe_hits_unexplained_name"]}))
    # ---- C6 payload probe
    add(_check("C6", "payload probe over every raw file finds only classified prose mentions and no "
                     "data-extension hit",
               raw["probe_hits_unexplained_payload"] == [] and raw["payload_scan_files"] > 1000
               and raw["probe_payload_data_ext_hits"] == 0,
               {"files": raw["payload_scan_files"], "bytes": raw["payload_scan_bytes"],
                "hit_counts": raw["payload_hit_counts"],
                "data_ext_token_files": raw["payload_data_ext_token_files"],
                "classified": raw["probe_classified_payload_hits"],
                "unexplained": raw["probe_hits_unexplained_payload"]}))
    # ---- C7 point-in-time universe membership absent (the record's first requirement)
    add(_check("C7", "point-in-time universe membership is absent from the canonical raw",
               raw["universe_membership_series_present"] is False
               and raw["delisting_history_series_present"] is False
               and raw["schema_documents_universe_or_mcap_dataset"] is False
               and raw["usdm_subdirs"] == ["funding", "instruments", "klines"],
               {"universe_membership_series": raw["universe_membership_series_present"],
                "delisting_history_series": raw["delisting_history_series_present"],
                "schema_dataset_sections": raw["schema_dataset_sections"],
                "schema_universe_mentions": raw["schema_universe_mentions"],
                "schema_listing_mentions": raw["schema_listing_mentions"],
                "series_shaped_tokens": raw["series_shaped_tokens"]}))
    # ---- C8 market-cap / liquidity controls absent
    add(_check("C8", "market-capitalization and liquidity/turnover controls are absent from the raw",
               raw["market_cap_series_present"] is False
               and raw["liquidity_series_present"] is False
               and raw["schema_quote_volume_documented_absent"] is True,
               {"market_cap_series": raw["market_cap_series_present"],
                "liquidity_series": raw["liquidity_series_present"],
                "columns_missing_vs_venue_12": raw["klines_columns_missing_vs_venue_12"],
                "quote_volume_documented_absent": raw["schema_quote_volume_documented_absent"],
                "size_fields": raw["instrument_size_fields"]}))
    # ---- C9 spread / cost surface and second venue absent
    add(_check("C9", "spread / order-book cost data and any second venue are absent",
               raw["spread_series_present"] is False
               and raw["second_venue_series_present"] is False
               and raw["adjustment_series_present"] is False
               and raw["binance_subdirs"] == ["usdm"]
               and raw["venue_or_vendor_dirs"] == [],
               {"spread_series": raw["spread_series_present"],
                "second_venue_series": raw["second_venue_series_present"],
                "adjustment_series": raw["adjustment_series_present"],
                "binance_subdirs": raw["binance_subdirs"],
                "venue_or_vendor_dirs": raw["venue_or_vendor_dirs"],
                "store_top_level": raw["store_top_level"]}))
    # ---- C10 cross-section size + no universe shrink in the published round
    ur = spec.get("universe_registration", {})
    add(_check("C10", "the raw can offer only four fixed survivor contracts; the published round did not "
                      "shrink the registered universe to them",
               raw["cross_section_size"] == 4
               and raw["wide_price_panel_present"] is False
               and ur.get("universe_shrunk_to_local_list") is False
               and ur.get("required_data_available_local") is False,
               {"cross_section_size": raw["cross_section_size"],
                "wide_price_panel_present": raw["wide_price_panel_present"],
                "universe_shrunk_to_local_list": ur.get("universe_shrunk_to_local_list"),
                "required_data_available_local": ur.get("required_data_available_local"),
                "price_panel_symbols": raw["price_panel_symbols"]}))
    # ---- C11 round-spec terminal values
    gate = spec.get("prerequisite_gate", {})
    launch = spec.get("launch", {})
    add(_check("C11", "round-spec states the contract-mandated terminal values",
               spec.get("family_id") == family and spec.get("round_id") == round_id
               and spec.get("kanban_task_id") == task
               and gate.get("verdict") == "TECHNICAL_INCOMPLETE"
               and gate.get("failure_layer") == "card-local"
               and gate.get("failure_class_used") == "data_window_invalid"
               and gate.get("last_run_id") is None
               and launch.get("launched") is False and launch.get("attempts") == 0,
               {"verdict": gate.get("verdict"), "layer": gate.get("failure_layer"),
                "class": gate.get("failure_class_used"), "last_run_id": gate.get("last_run_id"),
                "attempts": launch.get("attempts")}))
    # ---- C12 verdict.json terminal values
    add(_check("C12", "verdict.json states the same terminal values and an empty survivor/coverage surface",
               verdict.get("verdict") == "TECHNICAL_INCOMPLETE"
               and verdict.get("performance_claimable") is False
               and verdict.get("failure", {}).get("layer") == "card-local"
               and verdict.get("failure", {}).get("class") == "data_window_invalid"
               and verdict.get("failure", {}).get("last_run_id") is None
               and verdict.get("attempts", {}).get("launched") == 0
               and verdict.get("survivors") == [] and verdict.get("survivor_bundle") is None
               and verdict.get("coverage", {}).get("cells_computed") == 0
               and verdict.get("cohorts", {}).get("realized") == 0,
               {"verdict": verdict.get("verdict"),
                "performance_claimable": verdict.get("performance_claimable"),
                "attempts": verdict.get("attempts"), "survivors": verdict.get("survivors"),
                "coverage": verdict.get("coverage"), "cohorts": verdict.get("cohorts")}))
    # ---- C13 nothing submitted
    stray = []
    for dirpath, dirnames, filenames in os.walk(sf["family_dir"]):
        for name in filenames:
            if name in ("run-spec.json", "result.json", "state.json") or name.startswith("terminal"):
                stray.append(os.path.relpath(os.path.join(dirpath, name), sf["family_dir"]))
    add(_check("C13", "nothing was ever submitted: no run-spec, no attempt dir, no sentinel",
               not os.path.isdir(sf["attempts_dir"]) and stray == []
               and sorted(sf["round_dir_listing"]) == ["round-spec.json", "verdict.json"]
               and sorted(sf["family_dir_listing"]) == ["family.json", "rounds"],
               {"attempts_dir_exists": os.path.isdir(sf["attempts_dir"]),
                "round_dir_listing": sf["round_dir_listing"],
                "family_dir_listing": sf["family_dir_listing"], "stray": stray}))
    # ---- C14 DCA registration intact
    dd = spec.get("dca_domain", {})
    ufi = spec.get("user_fixed_invariants", {})
    axes = ["spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct"]
    dca_ok = (dd.get("configs_per_cohort_per_grid") == 48
              and dd.get("base_quote") == 1000
              and dd.get("base_quote_status") == "PROJECT_PRE_REGISTERED_CONSTANT"
              and dd.get("search_axes_status") == "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN"
              and sorted(dd.get("axes", {}).keys()) == sorted(axes)
              and not any(a in ufi for a in axes) and "base_quote" not in ufi)
    add(_check("C14", "DCA domain carries the 7.2 v1.3.1 provenance classes and the complete 48-cell product",
               dca_ok,
               {"dca_configs": dd.get("configs_per_cohort_per_grid"),
                "base_quote_status": dd.get("base_quote_status"),
                "search_axes_status": dd.get("search_axes_status"),
                "axes": sorted(dd.get("axes", {}).keys()), "dca_ok": dca_ok}))
    # ---- C15 coverage / survivor surface empty by construction
    cov = spec.get("robustness_plan", {})
    sel = spec.get("selector_and_disposition", {})
    add(_check("C15", "coverage is registered but empty; no survivor surface exists",
               cov.get("cells_registered_per_grid") == 48
               and cov.get("cells_computed") == 0
               and len(cov.get("registered_phase_grids", [])) == 10
               and all(v.get("computed") == 0 for v in cov.get("coverage_counts", {}).values())
               and sel.get("cohorts_realized") == 0 and sel.get("survivors") == []
               and sel.get("selector") == "cohort-selector-v1"
               and sel.get("disposition") == "cohort-disposition-v1",
               {"cells_registered_per_grid": cov.get("cells_registered_per_grid"),
                "cells_computed": cov.get("cells_computed"),
                "phase_grids": len(cov.get("registered_phase_grids", [])),
                "cohorts_realized": sel.get("cohorts_realized"),
                "selector": sel.get("selector"), "disposition": sel.get("disposition")}))
    # ---- C16 host-side PIT evidence gates declare zero supported membership
    pg = stores.get("pid_gates", {})
    add(_check("C16", "the machine's own PIT evidence stores declare zero supported daily membership",
               pg.get("membership_gate_classification") == "DATA_BLOCKED"
               and pg.get("membership_gate_pit_supported_asset_count") == 0
               and pg.get("membership_gate_supported_cells") == 0
               and (pg.get("membership_gate_unknown_cells") or 0) > 10000
               and pg.get("membership_cells_supported") == 0
               and pg.get("lifecycle_classification") == "BOUNDED"
               and pg.get("lifecycle_pit_supported_asset_count") == 0
               and pg.get("lifecycle_daily_membership_pit_supported_asset_count") == 0
               and stores.get("any_store_carries_pit_membership_panel") is False,
               {"membership_gate": {"classification": pg.get("membership_gate_classification"),
                                    "pit_supported_asset_count":
                                        pg.get("membership_gate_pit_supported_asset_count"),
                                    "supported_cells": pg.get("membership_gate_supported_cells"),
                                    "unknown_cells": pg.get("membership_gate_unknown_cells"),
                                    "total_cells": pg.get("membership_gate_total_cells")},
                "lifecycle_gate": {"classification": pg.get("lifecycle_classification"),
                                   "pit_supported_asset_count":
                                       pg.get("lifecycle_pit_supported_asset_count"),
                                   "daily_membership_pit_supported_asset_count":
                                       pg.get("lifecycle_daily_membership_pit_supported_asset_count"),
                                   "event_rows": pg.get("lifecycle_event_rows"),
                                   "event_types": pg.get("lifecycle_event_type_counts"),
                                   "support_scopes": pg.get("lifecycle_event_support_scopes")},
                "any_store_carries_pit_membership_panel":
                    stores.get("any_store_carries_pit_membership_panel")}))
    # ---- C17 host stores: all data hits classified; widest host panel is declared non-PIT
    add(_check("C17", "every host data-extension hit is classified and the widest host price panel is "
                      "declared non-point-in-time by its own provenance",
               stores.get("totals", {}).get("unclassified_data_hits") == 0
               and stores.get("totals", {}).get("files_scanned", 0) > 1000
               and pg.get("phase7_panel_symbol_count") == 12
               and pg.get("phase7_provenance_declares_not_pit") is True
               and stores.get("any_store_carries_spread_or_book_series") is False,
               dict(stores.get("totals", {}),
                    data_candidates=stores.get("classified_data_hits"),
                    unclassified=stores.get("unclassified_data_candidates"),
                    phase7_panel={"symbols": pg.get("phase7_panel_symbol_count"),
                                  "rows": pg.get("phase7_panel_rows"),
                                  "window": pg.get("phase7_panel_window"),
                                  "declared_non_pit": pg.get("phase7_provenance_declares_not_pit")})))
    # ---- C18 family.json identity
    add(_check("C18", "family.json binds this round's family to this card and fingerprint",
               fam.get("family_id") == family and fam.get("kanban_task_id") == task
               and isinstance(fam.get("semantic_fingerprint"), str)
               and spec.get("provenance", {}).get("semantic_fingerprint", {}).get(
                   "semantic_fingerprint") == fam.get("semantic_fingerprint"),
               {"family_id": fam.get("family_id"), "kanban_task_id": fam.get("kanban_task_id"),
                "semantic_fingerprint": fam.get("semantic_fingerprint")}))
    # ---- C19 taxonomy separation
    costs = spec.get("costs", {})
    add(_check("C19", "failure taxonomy kept separated: infrastructure terminal, not a scientific failure",
               "infrastructure/technical failure" in costs.get("note", "")
               and spec.get("expected") == "PREREQUISITE_ABSENT"
               and verdict.get("yield", {}).get("yield_decision") == "STOP_TECHNICAL_INCOMPLETE",
               {"expected": spec.get("expected"),
                "yield_decision": verdict.get("yield", {}).get("yield_decision"),
                "note": costs.get("note")}))
    # ---- C20 falsification battery neither lowered nor trimmed
    fal = spec.get("falsification", {})
    add(_check("C20", "the falsification battery is registered unchanged and recorded as not executed",
               fal.get("no_threshold_lowering") is True
               and fal.get("no_item_removal") is True
               and fal.get("falsification_status", "").startswith("NOT_EXECUTED")
               and "1. Reconstruct a survivorship-safe point-in-time crypto universe" in
               fal.get("record_falsification_verbatim", ""),
               {"status": fal.get("falsification_status"),
                "no_threshold_lowering": fal.get("no_threshold_lowering"),
                "no_item_removal": fal.get("no_item_removal")}))
    return {"checks": checks, "raw": raw, "stores": stores,
            "surfaces": {"family_dir": sf["family_dir"], "round_dir": sf["round_dir"]}}


def _result(checks, raw, stores):
    failed = [c for c in checks if not c["ok"]]
    return {"ok": not failed, "checks": checks, "failed": [c["id"] for c in failed],
            "raw_summary": {"entries": raw.get("raw_entry_count"),
                            "payload_files": raw.get("payload_scan_files"),
                            "payload_bytes": raw.get("payload_scan_bytes")},
            "stores_summary": stores.get("totals")}


def _resolve_source_texts(repo_root=None, card_body_path=None, record_path=None,
                          board_db=None, family=FAMILY, task=TASK):
    """Card / record / contract / footer texts a verbatim leaf may be checked against."""
    import sqlite3
    repo_root = repo_root or DEFAULT_REPO
    record_path = record_path or DEFAULT_RECORD
    with open(record_path, encoding="utf-8", errors="replace") as fh:
        record = fh.read()
    with open(os.path.join(repo_root, "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md"),
              encoding="utf-8", errors="replace") as fh:
        contract = fh.read()
    try:
        sys.path.insert(0, os.path.join(repo_root, "runtime"))
        from production_handoff import LIFECYCLE_FOOTER as footer
    except ImportError:  # pragma: no cover
        footer = ""
    card = None
    if card_body_path and os.path.exists(card_body_path):
        with open(card_body_path, encoding="utf-8", errors="replace") as fh:
            card = fh.read()
    if card is None:
        db = board_db or os.path.join(os.path.expanduser("~"), ".hermes/kanban/boards",
                                      BOARD, "kanban.db")
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
        try:
            row = con.execute("select body from tasks where id=?", (task,)).fetchone()
        finally:
            con.close()
        body = row[0] if row else ""
        if footer and body.endswith(footer):
            body = body[:-len(footer)]
        card = body
    return {"card": card, "record": record, "contract": contract, "footer": footer,
            "record_path": record_path}


def _walk_verbatim(node, path=()):
    if path and path[0] == "excerpt_source_map":
        return []
    out = []
    if isinstance(node, dict):
        for k, v in node.items():
            out.extend(_walk_verbatim(v, path + (str(k),)))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out.extend(_walk_verbatim(v, path + ("[%d]" % i,)))
    elif isinstance(node, str) and path and "verbatim" in path[-1].lower():
        out.append((".".join(path), node))
    return out


def verify_verbatim(spec_path, repo_root=None, card_body_path=None, record_path=None,
                    board_db=None, family=FAMILY, task=TASK):
    src = _resolve_source_texts(repo_root=repo_root, card_body_path=card_body_path,
                                record_path=record_path, board_db=board_db,
                                family=family, task=task)
    texts = {"card": src["card"], "record": src["record"], "contract": src["contract"],
             "footer": src["footer"]}
    spec = _load_json(spec_path)
    leaves = _walk_verbatim(spec)
    cmap = spec.get("excerpt_source_map", {})
    problems, misses, checked = [], [], 0
    seen = set()
    for key, value in leaves:
        entry = cmap.get(key)
        if entry is None:
            problems.append("%s: no excerpt_source_map entry" % key)
            continue
        seen.add(key)
        source = entry.get("source")
        if source not in texts:
            problems.append("%s: unknown source %r" % (key, source))
            continue
        if entry.get("chars") != len(value):
            problems.append("%s: chars %s != %d" % (key, entry.get("chars"), len(value)))
        if entry.get("sha256") != _sha256_text(value):
            problems.append("%s: sha256 mismatch in map" % key)
        if value not in texts[source]:
            misses.append("%s: not a verbatim substring of %s" % (key, source))
        checked += 1
    orphans = sorted(set(cmap) - seen)
    ok = not problems and not misses and not orphans and checked > 0
    return {"ok": ok, "checked": checked, "leaves": len(leaves), "problems": problems,
            "misses": misses, "orphans": orphans,
            "sources": {k: len(v) for k, v in texts.items()},
            "card_source": "pool_body_file" if card_body_path else "board_db"}


def _copy_family(src_results, dst_results, family=FAMILY, round_id=ROUND):
    """Fresh copy of the frozen family surface; any previous copy is dropped first
    so tamper variants cannot leak into one another."""
    dst = os.path.join(dst_results, family)
    if os.path.exists(dst):
        shutil.rmtree(dst)
    os.makedirs(os.path.join(dst, "rounds"), exist_ok=True)
    shutil.copy2(os.path.join(src_results, family, "family.json"),
                 os.path.join(dst, "family.json"))
    shutil.copytree(os.path.join(src_results, family, "rounds", round_id),
                    os.path.join(dst, "rounds", round_id))
    return dst


def _mutate(path, fn):
    doc = _load_json(path)
    fn(doc)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
        fh.write("\n")


def self_test(results_root, raw, stores, family=FAMILY, round_id=ROUND, task=TASK):
    """Non-vacuousness control: tampered copies must be refused by the named check."""
    tmp = tempfile.mkdtemp(prefix="t7347811a-selftest-")
    try:
        base = _copy_family(results_root, tmp, family, round_id)
        spec_p = os.path.join(base, "rounds", round_id, "round-spec.json")
        verd_p = os.path.join(base, "rounds", round_id, "verdict.json")
        fam_p = os.path.join(base, "family.json")
        results = []

        def v(name, expect, mutate):
            _copy_family(results_root, tmp, family, round_id)
            mutate(spec_p, verd_p, fam_p)
            res = run_checks(tmp, None, raw=raw, stores=stores, family=family,
                             round_id=round_id, task=task)
            failed = {c["id"] for c in res["checks"] if not c["ok"]}
            results.append({"variant": name, "expected": expect, "failed": sorted(failed),
                            "flipped": expect in failed, "ok": expect in failed})
            return res

        def m_spec(fn):
            return lambda s, vd, f: _mutate(s, fn)

        def m_verdict(fn):
            return lambda s, vd, f: _mutate(vd, fn)

        def m_family(fn):
            return lambda s, vd, f: _mutate(f, fn)

        v("verdict->PASS", "C12", m_verdict(lambda d: d.update({"verdict": "PASS"})))
        v("performance_claimable->true", "C12",
          m_verdict(lambda d: d.update({"performance_claimable": True})))
        v("failure.layer->shared-layer", "C12",
          m_verdict(lambda d: d["failure"].update({"layer": "shared-layer"})))
        v("failure.class->script_bug", "C12",
          m_verdict(lambda d: d["failure"].update({"class": "script_bug"})))
        v("failure.last_run_id->u1", "C12",
          m_verdict(lambda d: d["failure"].update({"last_run_id": round_id + "-u1"})))
        v("attempts.launched->1", "C12",
          m_verdict(lambda d: d["attempts"].update({"launched": 1})))
        v("survivors->[one]", "C12",
          m_verdict(lambda d: d.update({"survivors": [{"cohort": "BTCUSDT/1d"}]})))
        v("coverage.cells_computed->48", "C12",
          m_verdict(lambda d: d["coverage"].update({"cells_computed": 48})))
        v("yield_decision->CONTINUE", "C19",
          m_verdict(lambda d: d["yield"].update({"yield_decision": "CONTINUE"})))
        v("spec.launch.launched->true", "C11",
          m_spec(lambda d: d["launch"].update({"launched": True})))
        v("spec.gate.verdict->REJECT", "C11",
          m_spec(lambda d: d["prerequisite_gate"].update({"verdict": "REJECT"})))
        v("spec.gate.failure_layer->shared-layer", "C11",
          m_spec(lambda d: d["prerequisite_gate"].update({"failure_layer": "shared-layer"})))
        v("spec.gate.last_run_id->u1", "C11",
          m_spec(lambda d: d["prerequisite_gate"].update({"last_run_id": round_id + "-u1"})))
        v("spec.expected->RUN", "C19", m_spec(lambda d: d.update({"expected": "RUN"})))
        v("universe_shrunk_to_local_list->true", "C10",
          m_spec(lambda d: d["universe_registration"].update({"universe_shrunk_to_local_list": True})))
        v("required_data_available_local->true", "C10",
          m_spec(lambda d: d["universe_registration"].update({"required_data_available_local": True})))
        v("dca.configs->12", "C14",
          m_spec(lambda d: d["dca_domain"].update({"configs_per_cohort_per_grid": 12})))
        v("dca.base_quote_status->USER_FIXED", "C14",
          m_spec(lambda d: d["dca_domain"].update({"base_quote_status": "USER_FIXED"})))
        v("dca axes leak into user_fixed_invariants", "C14",
          m_spec(lambda d: d["user_fixed_invariants"].update({"spacing_pct": [0.01, 0.02]})))
        v("coverage.cells_computed->48 (spec)", "C15",
          m_spec(lambda d: d["robustness_plan"].update({"cells_computed": 48})))
        v("cohorts_realized->2", "C15",
          m_spec(lambda d: d["selector_and_disposition"].update({"cohorts_realized": 2})))
        v("selector version changed", "C15",
          m_spec(lambda d: d["selector_and_disposition"].update({"selector": "cohort-selector-v2"})))
        v("falsification item removed", "C20",
          m_spec(lambda d: d["falsification"].update({"no_item_removal": False})))
        v("falsification status -> EXECUTED", "C20",
          m_spec(lambda d: d["falsification"].update({"falsification_status": "EXECUTED"})))
        v("family.kanban_task_id->other", "C18",
          m_family(lambda d: d.update({"kanban_task_id": "t_00000000"})))
        v("family.semantic_fingerprint mismatch", "C18",
          m_family(lambda d: d.update({"semantic_fingerprint": "sha256:" + "0" * 64})))
        v("stray run-spec.json present", "C13",
          lambda s, vd, f: (os.makedirs(os.path.join(os.path.dirname(s), "attempts", round_id + "-u1"),
                                        exist_ok=True),
                            open(os.path.join(os.path.dirname(s), "attempts", round_id + "-u1",
                                              "run-spec.json"), "w").write("{}")))
        v("stray terminal sentinel present", "C13",
          lambda s, vd, f: open(os.path.join(os.path.dirname(s), "terminal-DONE"), "w").write("{}"))
        v("extra file in family dir", "C13",
          lambda s, vd, f, fam_dir=base: open(os.path.join(fam_dir, "extra.json"), "w").write("{}"))
        # must-not-flip: a mutation that touches nothing the checks read
        v("benign round-spec note", "NO_FLIP",
          m_spec(lambda d: d.update({"worker_note": "benign"})))
        results[-1]["ok"] = results[-1]["flipped"] is False
        results[-1]["flipped"] = results[-1]["failed"]
        return {"ok": all(r["ok"] for r in results), "variants": results}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def raw_fixture_control(results_root, raw, stores, family=FAMILY, round_id=ROUND, task=TASK):
    """Build a temp raw tree carrying what this record would need and assert the
    raw-side checks flip to FAIL (the raw checks are not vacuously green).

    The fixture keeps the live store's *shape* (one Binance USD-M venue, four
    symbols, seven intervals, 1,720 daily bars, 5,100 funding rows, four
    CryptoPerpetual instruments) so that only the checks about the added
    point-in-time-universe / market-cap / liquidity / spread / second-venue
    surfaces move: C1-C4 and C10 stay green, C5-C9 flip.
    """
    tmp = tempfile.mkdtemp(prefix="t7347811a-rawfix-")
    try:
        rawfix = os.path.join(tmp, "market-data-raw")
        symbols = ["BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
        intervals = ["15m", "1d", "1h", "1w", "30m", "4h", "5m"]
        for sub in (["_meta", "binance/usdm/instruments",
                     "binance/usdm/universe", "coingecko/market_cap", "costs/spread",
                     "token_migration/redenominations", "okx/spot/klines/BTCUSDT"]
                    + ["binance/usdm/klines/%s/%s" % (s, i)
                                                   for s in symbols for i in intervals]
                    + ["binance/usdm/funding/%s" % s for s in symbols]):
            os.makedirs(os.path.join(rawfix, sub), exist_ok=True)
        with open(os.path.join(rawfix, "_meta/CONFIG.json"), "w") as fh:
            json.dump({"schema": "market-data-raw/config/v1", "venue": "BINANCE",
                       "market_type": "usdm_perp", "symbols": symbols,
                       "intervals": intervals}, fh)
        with open(os.path.join(rawfix, "_meta/SCHEMA.md"), "w") as fh:
            fh.write("# fixture schema\n\n## Dataset: klines (Binance USD-M perpetual futures, UTC)\n\n"
                     "## Dataset: funding\n\n## Dataset: instruments\n\n"
                     "## Dataset: universe_membership (point-in-time cryptocurrency universe membership)\n\n"
                     "## Dataset: market_cap (point-in-time market capitalization and liquidity)\n\n"
                     "## Dataset: spread (bid-ask spread / order-book cost)\n\n"
                     "## Dataset: okx_spot (second venue)\n")
        with gzip.open(os.path.join(rawfix, "binance/usdm/universe/membership-2026-09.csv.gz"), "wt") as fh:
            fh.write("date,symbol,membership_status,listing_date,delisting_date\n"
                     "2026-09-01,BTCUSDT,member,2019-09-08,\n"
                     "2026-09-01,DOGEUSDT,member,2020-07-10,\n"
                     "2026-09-01,SCAMUSDT,delisted,2021-01-01,2025-12-31\n")
        with gzip.open(os.path.join(rawfix, "token_migration/redenominations/"
                                           "redenominations-2026-09.csv.gz"), "wt") as fh:
            fh.write("date,symbol,adjustment_type,redenominated_from,redenominated_to\n"
                     "2026-09-01,OLDUSDT,token_migration,OLDUSDT,NEWUSDT\n")
        with gzip.open(os.path.join(rawfix, "coingecko/market_cap/mcap-2026-09.csv.gz"), "wt") as fh:
            fh.write("date,symbol,market_cap,circulating_supply,quote_volume,turnover\n"
                     "2026-09-01,BTCUSDT,1200000000000,19900000,30000000000,0.025\n")
        with gzip.open(os.path.join(rawfix, "costs/spread/spread-2026-09.csv.gz"), "wt") as fh:
            fh.write("date,symbol,bid_ask_spread_bps,book_ticker_depth\n2026-09-01,BTCUSDT,1.2,500000\n")
        with gzip.open(os.path.join(rawfix, "okx/spot/klines/BTCUSDT/BTCUSDT-1d-2026-09.csv.gz"), "wt") as fh:
            fh.write("date,venue,market,open,high,low,close,volume\n"
                     "2026-09-01,okx,spot,1,1,1,1,1\n")
        for sym in symbols:
            # every dataset exists; only BTCUSDT/1d carries the measured 1,720-bar history
            for iv in intervals:
                path = os.path.join(rawfix, "binance/usdm/klines", sym, iv,
                                    "%s-%s-2022-01.jsonl.gz" % (sym, iv))
                n = (1720 if (sym == "BTCUSDT" and iv == "1d") else
                     288 if (sym == "BTCUSDT" and iv == "5m") else 2)
                with gzip.open(path, "wt") as fh:
                    for i in range(n):
                        fh.write(json.dumps({"open_time_ms": 1640995200000 + i * 86400000,
                                             "close_time_ms": 1641081599999 + i * 86400000,
                                             "open": "1", "high": "1", "low": "1", "close": "1",
                                             "volume": "1"}) + "\n")
            with gzip.open(os.path.join(rawfix, "binance/usdm/funding", sym,
                                        "%s-funding.jsonl.gz" % sym), "wt") as fh:
                n = 5100 if sym == "BTCUSDT" else 2
                for i in range(n):
                    fh.write(json.dumps({"symbol": sym, "venue": "BINANCE",
                                         "market_type": "usdm_perp",
                                         "funding_time_ms": 1640995200000 + i * 28800000,
                                         "funding_rate": "0", "mark_price": "1",
                                         "funding_price_source": "x", "rate_type": "y",
                                         "truth_status": "z"}) + "\n")
        with open(os.path.join(rawfix, "binance/usdm/instruments/usdm-perp-instruments.json"), "w") as fh:
            json.dump({"instruments": [{"fields": {"id": "%s-PERP.BINANCE" % s.replace("USDT", ""),
                                                   "base_currency": s.replace("USDT", ""),
                                                   "quote_currency": "USDT",
                                                   "settlement_currency": "USDT",
                                                   "type": "CryptoPerpetual",
                                                   "maker_fee": "0.0002", "taker_fee": "0.0005",
                                                   "price_increment": "0.10"},
                                        "python_type": "CryptoPerpetual"} for s in symbols]}, fh)
        fix_raw = measure_raw(rawfix)
        res = run_checks(results_root, rawfix, raw=fix_raw, stores=stores, family=family,
                         round_id=round_id, task=task)
        failed = {c["id"] for c in res["checks"] if not c["ok"]}
        expect_flip = ["C5", "C6", "C7", "C8", "C9"]
        must_not_flip = ["C1", "C2", "C3", "C4", "C10", "C11", "C12", "C13", "C14", "C15",
                         "C16", "C17", "C18", "C19", "C20"]
        return {"ok": all(c in failed for c in expect_flip)
                and not any(c in failed for c in must_not_flip),
                "expected_flip": expect_flip, "flipped": sorted(failed),
                "other_flips": sorted(failed - set(expect_flip)),
                "missing_flip": [c for c in expect_flip if c not in failed],
                "unexpected_flip": [c for c in must_not_flip if c in failed],
                "fixture_shape": {"symbols": symbols, "intervals": len(intervals),
                                  "btcusdt_1d_rows": 1720, "funding_rows_btcusdt": 5100,
                                  "instruments": len(symbols)},
                "fixture_flags": {"universe_membership_series": fix_raw["universe_membership_series_present"],
                                  "delisting_history_series": fix_raw["delisting_history_series_present"],
                                  "market_cap_series": fix_raw["market_cap_series_present"],
                                  "liquidity_series": fix_raw["liquidity_series_present"],
                                  "spread_series": fix_raw["spread_series_present"],
                                  "second_venue_series": fix_raw["second_venue_series_present"],
                                  "adjustment_series": fix_raw["adjustment_series_present"],
                                  "schema_sections": fix_raw["schema_dataset_sections"],
                                  "unexplained_name": fix_raw["probe_hits_unexplained_name"],
                                  "unexplained_payload": fix_raw["probe_hits_unexplained_payload"]}}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _print_checks(res):
    for c in res["checks"]:
        print("%-4s %-4s %s" % (c["id"], "PASS" if c["ok"] else "FAIL", c["name"]))
        if not c["ok"]:
            print("        detail: %s" % json.dumps(c["detail"], ensure_ascii=False)[:400])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--results-root", default=DEFAULT_RESULTS)
    ap.add_argument("--raw-root", default=DEFAULT_RAW)
    ap.add_argument("--repo-root", default=DEFAULT_REPO)
    ap.add_argument("--card-body", default=os.path.join(DEFAULT_RESULTS, "_handoff/bodies",
                                                        FAMILY + ".md"))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--measure-only", action="store_true")
    ap.add_argument("--verify-verbatim", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--raw-fixture-control", action="store_true")
    ap.add_argument("--host-scan", action="store_true")
    ap.add_argument("--other-stores", action="store_true")
    args = ap.parse_args(argv)

    if args.host_scan or args.other_stores:
        stores = measure_host_stores()
        print(json.dumps(stores["totals"], ensure_ascii=False, indent=1))
        print(json.dumps({"pid_gates": stores["pid_gates"],
                          "classified": stores["classified_data_hits"],
                          "unclassified": stores["unclassified_data_candidates"]},
                         ensure_ascii=False, indent=1)[:20000])
        return 0

    if args.measure_only:
        raw = measure_raw(args.raw_root)
        print(json.dumps(raw, ensure_ascii=False, indent=1, default=str)[:20000])
        return 0

    if args.verify_verbatim:
        sf = _family_surfaces(args.results_root, FAMILY, ROUND)
        out = verify_verbatim(sf["spec"], repo_root=args.repo_root,
                              card_body_path=args.card_body if os.path.exists(args.card_body) else None)
        print(json.dumps(out, ensure_ascii=False, indent=1)[:8000])
        return 0 if out["ok"] else 1

    raw = measure_raw(args.raw_root)
    stores = measure_host_stores()

    if args.self_test:
        out = self_test(args.results_root, raw, stores)
        print(json.dumps(out, ensure_ascii=False, indent=1)[:20000])
        return 0 if out["ok"] else 1

    if args.raw_fixture_control:
        out = raw_fixture_control(args.results_root, raw, stores)
        print(json.dumps(out, ensure_ascii=False, indent=1)[:8000])
        return 0 if out["ok"] else 1

    res = run_checks(args.results_root, args.raw_root, repo_root=args.repo_root,
                     raw=raw, stores=stores)
    out = _result(res["checks"], raw, stores)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1)[:40000])
    else:
        _print_checks(res)
        print("\nok=%s failed=%s" % (out["ok"], out["failed"]))
        print("raw: %s" % json.dumps(out["raw_summary"], ensure_ascii=False))
        print("stores: %s" % json.dumps(out["stores_summary"], ensure_ascii=False))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
