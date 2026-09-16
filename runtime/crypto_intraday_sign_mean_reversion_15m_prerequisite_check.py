#!/usr/bin/env python3
"""Prerequisite-gate read-back checker for the family

    crypto-intraday-sign-mean-reversion-15m-walk-forward-2026-09-01

Card t_a4f85d1b terminalises this family as TECHNICAL_INCOMPLETE because the
record's required market is not in the canonical raw:

  * the record's baseline required data opens with "crypto spot instruments"
    and "Binance USDT pairs for direct source replication"; its primary sample
    is 183 high-volume Binance USDT SPOT pairs at 15-minute resolution, with the
    universe screened on 2026-06-08 by 24h quote volume. The canonical raw holds
    one market type only - BINANCE USD-M PERPETUAL - with four fixed contracts
    (BNBUSDT/BTCUSDT/ETHUSDT/SOLUSDT) and no spot directory at any depth. No
    spot instrument exists anywhere on the host except a single non-canonical,
    single-asset, DAILY BTC spot cache (970 rows, 2024-01-01..2026-08-27) in an
    unrelated project tree, which is measured below and NOT used;
  * the record's required point-in-time, survivorship-free universe
    construction has no membership product either: the only machine-local
    point-in-time membership product (a non-canonical phase-9 project) is
    USD-M-perpetual-scoped, classifies DATA_BLOCKED with 0 of 14,760 cells
    supported and sets authorized_to_start_a2=false, and the canonical raw's
    instrument definitions carry no listing/delisting date field at all;
  * the record's mechanism/conditioning inputs (taker-buy base volume /
    aggressor-side classification, total volume sufficient to build a taker
    imbalance, order-book depth) have no substrate: no trade/aggTrade/tick/
    quote/book dataset exists in the raw tree at any depth (probe over 1,633
    entry names), every stored row shape is OHLCV-only, and the store's own
    SCHEMA.md documents that quote_volume, trade count and taker-buy splits are
    deliberately absent. The only host-local trade-level sample is a 20-row
    single-instant BTCUSDT USD-M futures aggTrade capture in a non-canonical
    project (measured, unused);
  * the record's falsification battery requires a second independent venue
    (Coinbase/OKX/Bybit) and a point-in-time non-survivor-biased universe over a
    fresh sample after 2026-08-08; no second venue exists locally and the
    membership product above is blocked.

Running the four-contract USD-M perpetual panel instead would substitute the
market (perp for spot), shrink the universe (4 fixed majors for a point-in-time
wide spot cross-section), invert the record's own registration (perps appear
there only as an OPTIONAL robustness control) - and the record's portability
paragraph states the perp port is "adapted rather than automatically direct".
The card forbids that ("不得以近似資料、替代市場或改寫 hypothesis 硬跑" /
"不得縮減 universe 以硬造可執行性"), so nothing was launched.

This script exists so an independent reader can re-derive that determination
from the live filesystem instead of trusting prose:

  * C1-C6 re-measure the canonical raw: the market/venue surface (one market
    type, no spot/margin/options/dated market, no trade-like dataset), the local
    instrument surface + stored row shapes, the DECISIVE absence of a spot
    market (path probe over the whole tree at full depth + instrument types +
    the store's own documented market type), the DECISIVE absence of any
    trade-level/aggressor substrate (entry-name probe + row shapes + the
    store's own documented dataset families and coverage limit + its
    inventory), the registered 15-minute/UTC/flat-bar items that ARE present
    (aligned 900 s bars, no silent gap fill) together with the measured window,
    and the host-local substitute surfaces (the single-asset daily spot cache,
    the 20-row trade sample, the 12-symbol USD-M daily panel, the DATA_BLOCKED
    membership product, the options quotes snapshot, the retired engine stacks);
  * C7-C12 re-read the round's immutable artifacts and assert they state
    exactly the contract-mandated terminal values (verdict, layer, class, yield
    decision, zero attempts, null run_id), that the registered requirement was
    NOT shrunk to the locally available four contracts and still states the
    record's own universe, that the house-wide scan is internally consistent
    against a live re-run, that nothing was ever submitted (no attempt
    directory, no terminal sentinel), and that the DCA registration still
    carries the contract 7.2 v1.3.1 provenance classes plus the complete
    48-cell product;
  * C13 resolves every `*_verbatim` leaf of the persisted round-spec against
    its declared source (card / record / contract / footer) and re-checks the
    excerpt digest map - independent of the authoring run.

Read-only: it never writes inside the results tree or the raw tree. `--self-test`
builds tampered copies in fresh temp directories and asserts the checker refuses
them (non-vacuousness control). `--raw-fixture-control` builds a temp raw tree
carrying what this record would need (a spot market, spot aggTrades with an
`isBuyerMaker` flag, a bookTicker quote surface, a second venue, extra
contracts and `_ref/` membership/listing references) and asserts the raw-side
checks flip to FAIL. `--host-scan` re-runs the house-wide search for the
record's data surfaces. Every temp tree is removed afterwards.

Usage:
    python3 runtime/crypto_intraday_sign_mean_reversion_15m_prerequisite_check.py [--json]
    python3 runtime/crypto_intraday_sign_mean_reversion_15m_prerequisite_check.py --measure-only
    python3 runtime/crypto_intraday_sign_mean_reversion_15m_prerequisite_check.py --verify-verbatim
    python3 runtime/crypto_intraday_sign_mean_reversion_15m_prerequisite_check.py --self-test
    python3 runtime/crypto_intraday_sign_mean_reversion_15m_prerequisite_check.py --raw-fixture-control
    python3 runtime/crypto_intraday_sign_mean_reversion_15m_prerequisite_check.py --host-scan

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

FAMILY = "crypto-intraday-sign-mean-reversion-15m-walk-forward-2026-09-01"
ROUND = FAMILY + "-r1"
TASK = "t_a4f85d1b"
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
# the four contracts the record does NOT register are simply absent; the local
# list is registered verbatim so "shrink the universe" can never be papered over
KLINE_ROW_FIELDS = ["close", "close_time_ms", "high", "low", "open", "open_time_ms",
                    "volume"]
FUNDING_ROW_FIELDS = ["funding_price_source", "funding_rate", "funding_time_ms",
                      "mark_price", "market_type", "rate_type", "symbol", "truth_status",
                      "venue"]
# The store nests to six levels (binance/usdm/klines/<SYMBOL>/<interval>/<file>), so the
# entry-name probe walks deeper than the store can nest: a dataset cannot escape the
# probe by sitting deep in the tree.
PROBE_MAX_DEPTH = 8

# ---------------------------------------------------------------- registered requirement
# The record's own required-data list (`## Required data`): 6 baseline items +
# 4 mechanism/conditioning items + 1 closing point-in-time-universe requirement.
RECORD_REQUIRED_ITEMS = 11
CARD_REQUIRED_EXCERPT_ITEMS = 11
# The record's falsification battery (`## Falsification plan`) has 10 items; the frozen
# card body quotes items 1-6 and marks the excerpt as truncated.
RECORD_FALSIFICATION_ITEMS = 10
CARD_FALSIFICATION_EXCERPT_ITEMS = 6
# The record's limitations list (8 items) and the card's transcription (8 items).
RECORD_LIMITATIONS_ITEMS = 8
CARD_LIMITATIONS_EXCERPT_ITEMS = 8
# The record's `Research interpretation` has 4 bullets; the card quotes 2.
RECORD_RESEARCH_INTERPRETATION_ITEMS = 4
CARD_RESEARCH_INTERPRETATION_EXCERPT_ITEMS = 2
# The record's `Execution assumptions` section has 10 bullets and never appears in the
# frozen card body; it is restored from the record in full.
RECORD_EXECUTION_ASSUMPTIONS_ITEMS = 10
# The record's reported samples.
RECORD_PRIMARY_SAMPLE = ("2025-01-01", "2026-02-11")
RECORD_FORWARD_HOLDOUT = ("2026-02-12", "2026-08-08")
RECORD_SOURCE_ASOF = "2026-08-08"
RECORD_SOURCE_SAMPLE_COUNT = 183          # high-volume Binance USDT SPOT pairs
RECORD_UNIVERSE_SCREEN_DATE = "2026-06-08"
RECORD_SIGNAL_LAG_COUNT = 12
RECORD_SOFTCLIP_SCALE = 150
RECORD_WALK_FORWARD_TRAIN_BARS = 5760
RECORD_WALK_FORWARD_TEST_BARS = 960
RECORD_GROSS_EDGE_BP = 1.3
RECORD_SPOT_ROUND_TRIP_BP = 5
# The raw's own window as registered on the card.
CARD_REGISTERED_RAW_WINDOW_START = "2022-01-01"
CARD_REGISTERED_RAW_WINDOW = "klines 2022-01-01\u21922026-09-11"
CARD_REGISTERED_FUNDING_WINDOW = "funding 2022-01-01T00:00Z\u21922026-09-12T08:00Z"
# The dataset families the store's own SCHEMA.md documents.
DOCUMENTED_DATASET_FAMILIES = ["funding", "instruments",
                              "klines (Binance USD-M perpetual futures, UTC)"]
DOCUMENTED_INTERVALS = ["1d", "1h", "1w", "30m", "4h", "5m", "15m"]
# One unified token list is used by the raw entry-name probe, the house-wide scan and
# every registered measurement, so the two documents can never disagree about what the
# probe returns. NO token is excluded: every collision of a generic token (e.g. `spot`
# inside `spotify`, `trade` inside `trader`) is classified and counted instead, so an
# exclusion can never hide a hit.
SPOT_TOKENS = ("spot", "spot_klines", "spotklines", "binance-spot", "binance_spot",
               "cryptospot", "crypto_spot", "spot_pairs", "spotpairs", "usdm_spot")
TRADE_TOKENS = ("aggtrade", "agg_trade", "aggtrades", "agg_trades", "isbuyermaker",
                "buyermaker", "buyer_maker", "aggressor", "taker_buy", "takerbuy",
                "taker-buy", "signed_flow", "orderflow", "order_flow", "trade_ticks",
                "tick_data", "tickdata", "trade_tape", "trade_level", "trades", "trade",
                "quotes", "bidask", "bid_ask", "bookticker", "book_ticker", "orderbook",
                "order_book", "level2", "l2book", "depth_snapshot", "orderbook_depth")
VENUE_TOKENS = ("bybit", "okx", "coinbase", "kraken")
UNIVERSE_TOKENS = ("universe_membership", "pit_membership", "membership_history",
                   "listing_dates", "listing_history", "delisting", "delistings",
                   "quote_volume", "volume_screen", "survivorship")
PROBE_TOKENS = tuple(SPOT_TOKENS + TRADE_TOKENS + VENUE_TOKENS + UNIVERSE_TOKENS)
PROBE_EXCLUSIONS = {}
# Name tokens that would have to exist for the record's required dataset.
TRADE_SHAPE_TOKENS = ("taker", "buyer", "maker", "aggressor", "side", "count", "trades",
                      "trade", "quote_volume")
# Narrow list for instrument definitions: `maker_fee`/`taker_fee` legitimately exist
# there, so only an actual trade-side or lifecycle field counts.
INSTRUMENT_TRADE_FIELDS = ("aggressor", "isbuyer", "buyermaker", "buyer_maker",
                           "trade_side", "taker_volume", "maker_volume", "signed",
                           "quote_volume")
INSTRUMENT_LIFECYCLE_FIELDS = ("listing", "listed", "onboard", "delisting", "delisted",
                               "launch", "expiry", "delivery", "activation",
                               "valid_from", "valid_to", "status")
# The decisive registered data items and the exact status each must carry.
DECISIVE_REQUIRED_STATUS = {
    "universe_binance_usdt_spot_pairs": "ABSENT",
    "universe_source_wide_cross_section_183_spot_pairs": "ABSENT",
    "universe_point_in_time_membership_history": "ABSENT",
    "instrument_listing_and_delisting_history": "ABSENT",
    "spot_ohlcv_15m_candles": "ABSENT",
    "taker_buy_base_volume_or_aggressor_classification": "ABSENT",
    "total_volume_for_taker_imbalance": "ABSENT",
    "order_book_depth_snapshots": "ABSENT",
    "second_venue_coinbase_okx_bybit": "ABSENT",
    "perpetual_basis_series_spot_leg": "NOT_CONSTRUCTIBLE",
}
NON_DECISIVE_REQUIRED_STATUS = {
    "klines_15m_utc_bucket_start": "PRESENT_FOR_FOUR_PERP_CONTRACTS",
    "missing_and_flat_bar_handling_convention": "PRESENT",
    "venue_binance_usdm_perpetual": "PRESENT",
    "instrument_btcusdt_perpetual": "PRESENT",
    "instrument_ethusdt_perpetual": "PRESENT",
    "instrument_bnbusdt_perpetual": "PRESENT",
    "instrument_solusdt_perpetual": "PRESENT",
    "perpetual_funding_history_with_mark_price": "PRESENT",
    "funding_settlement_exclusion_control_available": "PRESENT",
    "instrument_metadata_maker_taker_fee_and_tick": "PRESENT",
    "signal_soft_clipped_lagged_returns": "CONSTRUCTIBLE_ONLY_ON_UNREGISTERED_MARKET",
    "next_bar_sign_forecast_and_holding_rule":
        "CONSTRUCTIBLE_ONLY_ON_UNREGISTERED_MARKET",
    "parameter_free_sign_baselines_and_placebos":
        "CONSTRUCTIBLE_ONLY_ON_UNREGISTERED_MARKET",
    "walk_forward_geometry_5760_960": "METHOD_REGISTERED_NOT_DATA",
    "one_bar_information_gap_and_flat_bar_scoring": "METHOD_REGISTERED_NOT_DATA",
    "fresh_sample_after_2026_08_08": "WINDOW_PRESENT_FOR_FOUR_PERP_CONTRACTS_ONLY",
    "falsification_battery_10_items": "PARTIALLY_NOT_EXECUTABLE",
    "transaction_cost_convention_spot_round_trip_benchmark":
        "PARTIAL_LOCAL_INSTRUMENT_METADATA_ONLY",
    "per_fill_cost_accounting_rail": "PRESENT_IN_PIPELINE",
}
MISSING_DATA_MATRIX_ITEMS = tuple(list(DECISIVE_REQUIRED_STATUS)
                                 + list(NON_DECISIVE_REQUIRED_STATUS))
DECISIVE_MATRIX_ITEMS = tuple(DECISIVE_REQUIRED_STATUS)
# Local substitutes that exist on the host but are not the canonical raw and are not
# the record's required source; each is measured and recorded as unused.
SUBSTITUTE_MARKERS = {
    "phase5_spot_daily_cache": "phase5-crypto-derivatives",
    "phase12_trade_sample": "phase12-l2-l3-execution-tca",
    "phase7_usdm_daily_panel": "phase7-alpha-research",
    "phase9_pit_membership_product": "a1-1-phase9-pit-membership",
    "phase11_options_quotes": "phase11-options-volatility",
}
# Name markers for the house-wide classification. Order matters: the most specific
# class wins.
NON_CANONICAL_MARKERS = ("a1-1-phase9-pit-membership", "phase5-crypto-derivatives",
                         "phase7-alpha-research", "phase11-options-volatility",
                         "phase12-l2-l3-execution-tca", "phase10-pit-bitemporal",
                         "dr-profit-distillation", "audit-CJ-DEFI2",
                         "vision_um_daily_klines", "alpha-strategy-research")
OUT_OF_SCOPE_MARKERS = ("/ml4t-p1/", "/ml4t-delta-", "/tmp/ml4t", "nasdaq100_microstructure",
                        "/case_studies/")
BUILD_ARTIFACT_MARKERS = ("/target/debug/deps/", "/target/release/deps/",
                          "/target/debug/build/", "/target/release/build/",
                          "/build-cache/", ".rlib", ".rmeta", ".rcgu.o")
STICKER_MARKERS = ("/stickers/", ".tgs", "sticker")
SOCKET_MARKERS = ("loop-tick", ".sock")
OWN_GRID_MARKERS = ("slippage_2ticks", "grid_boundary_alt")
HERMES_TMP_MARKERS = ("/.hermes/tmp/",)
FIXTURE_MARKERS = ("/fixtures/",)
BACKUP_LIKE_DIRS = ("_archived", "archive", "backup", "backups", "old")
BACKUP_MARKERS = ("profile_backup", "_backup_", "-backup-")
# Collisions of the generic `spot` token with ordinary names the host already uses:
# the measured example path for each is recorded in the scan output.
UNRELATED_SPOT_MARKERS = ("spotify", "spotlight", "spotcheck", "hotspot", "gamespot",
                          "despotak", "spot_first", "spot-first", "spot-long",
                          "hedge-to-spot", "spot_candle", "spot-etf", "spot-perp",
                          "spot-perpetual", "spot-dynamic", "binance-spot",
                          "crypto-spot")
INTEGRITY_MANIFEST_MARKERS = ("hash-manifest", "hashes", "rehash", "hash_compare",
                              "pre_hashes", "post_hash", "hash_three_states",
                              "results_hash", "spotcheck_commits", "spotcheck_draft")
# this card's own evidence snapshot carries the family name in its filename
OWN_ARTIFACT_BASENAMES = (FAMILY + "-prerequisite-gate-20260917.json",)
WIKI_DIR_MARKER = os.path.join(".hermes", "wiki")
ROUND_DIR_PARTS = (FAMILY, "rounds", ROUND)
INVARIANT_TOKENS = ("30,000", "numeraire", "leverage 10x", "12 tranches", "tranche #12",
                    "reduce-only", "flat/kill")
# Classification vocabulary for house-wide hits. A hit is acceptable only if it is a
# document, code/markup, session/log/cache artifact, our own provenance artifact, a
# build artifact, an out-of-scope research project, an already-disclosed non-canonical
# substitute, a dataset-less stub directory, or an ordinary-name collision of a generic
# probe token - never a spot-market or trade-level data store for the registered
# instruments that is not already measured and disclosed.
HOUSE_HIT_CLASSES = (
    "own_evidence_snapshot_false_positive",
    "own_round_artifact_false_positive",
    "own_family_directory_false_positive",
    "own_checker_source_false_positive",
    "own_handoff_pool_false_positive",
    "canonical_record_document",
    "related_wiki_document",
    "repo_documentation_false_positive",
    "source_or_markup_false_positive",
    "agent_session_or_log_false_positive",
    "hermes_cache_false_positive",
    "hermes_skill_or_catalog_false_positive",
    "hermes_tmp_artifact_false_positive",
    "integrity_manifest_false_positive",
    "build_artifact_false_positive",
    "telegram_sticker_export_false_positive",
    "gateway_socket_false_positive",
    "own_pipeline_grid_artifact_false_positive",
    "repo_fixture_false_positive",
    "out_of_scope_market_research_false_positive",
    "non_canonical_staging_copy_false_positive",
    "disclosed_non_canonical_spot_cache_false_positive",
    "disclosed_non_canonical_trade_sample_false_positive",
    "disclosed_non_canonical_perp_panel_false_positive",
    "disclosed_non_canonical_membership_product_false_positive",
    "disclosed_non_canonical_options_quotes_false_positive",
    "unrelated_source_identifier_false_positive",
    "dataset_stub_directory_false_positive",
    "unclassified_data_candidate",
)
UNCLASSIFIED_CLASS = "unclassified_data_candidate"
# memoised house scan (see host_scan)
_HOST_SCAN_CACHE = {}
TMP_ROOTS = [os.path.join(HOME, "workspace"), EXPANSION, "/Volumes/ResearchData",
             os.path.join(HOME, ".hermes"),
             os.path.join(HOME, "workspace", "qlib-apple-container")]
SCAN_SKIP_DIRS = ("node_modules", "__pycache__", ".git", "venvs", "site-packages", ".venv",
                  "Photos Library.photoslibrary")
# The retired engine stacks the store's own inventory lists. Their absence is measured
# (a migration result, not an assumption).
RETIRED_STORE_PATHS = (os.path.join(EXPANSION, "nautilus-system"),
                       os.path.join(EXPANSION, "lean-system"),
                       os.path.join(EXPANSION, "microsoft-quant-stack"))


def _load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _text(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


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


def _interval_seconds(interval):
    unit, n = interval[-1], int(interval[:-1])
    return n * {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[unit]


def _kline_dir(raw, symbol, interval):
    return os.path.join(raw, "binance", "usdm", "klines", symbol, interval)


def _kline_files(raw, symbol, interval):
    d = _kline_dir(raw, symbol, interval)
    if not os.path.isdir(d):
        return []
    return sorted(f for f in os.listdir(d) if f.endswith(".jsonl.gz"))


def measure_raw(raw):
    """Independent re-measurement of the canonical raw. No artifact is trusted."""
    m = {}
    m["raw_root"] = raw
    m["top_level_dirs"] = _dirs(raw)
    m["binance_market_dirs"] = _dirs(os.path.join(raw, "binance"))
    m["binance_usdm_subdirs"] = _dirs(os.path.join(raw, "binance", "usdm"))
    m["raw_paths"] = _rel_dirs(raw, 3)
    m["paths_named_other_market"] = [
        p for p in m["raw_paths"] if os.path.basename(p).lower() in
        ("spot", "margin", "options", "inverse", "coinm", "delivery", "futures", "quarter",
         "index", "trades", "aggtrades", "trade", "ticks", "book", "bookticker", "depth",
         "quotes", "membership", "reference")]
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
    m["klines_interval_set_by_symbol"] = {
        s: _dirs(os.path.join(kl, s)) for s in (m["klines_dataset_dirs"] or [])}
    m["klines_interval_set"] = sorted(
        set(i for ivs in m["klines_interval_set_by_symbol"].values() for i in (ivs or [])))
    secs = sorted(_interval_seconds(i) for i in m["klines_interval_set"])
    m["klines_interval_seconds"] = secs
    m["finest_stored_interval_seconds"] = secs[0] if secs else None
    m["registered_interval_seconds"] = 900
    m["usdm_dataset_dirs"] = sorted(_dirs(ud) or [])
    m["trade_like_dataset_dirs"] = [
        d for d in m["usdm_dataset_dirs"]
        if any(t in d.lower() for t in ("trade", "tick", "agg", "book", "quote", "depth"))]
    m["reference_dirs_under_raw"] = sorted(
        d for d in m["raw_paths"] if any(t in d.lower() for t in
                                         ("_ref", "membership", "listing", "universe")))

    # row shapes + windows
    d1 = os.path.join(kl, "BTCUSDT", "1d")
    files1 = _kline_files(raw, "BTCUSDT", "1d")
    if files1:
        r_first, r_last = _rows(os.path.join(d1, files1[0])), _rows(os.path.join(d1, files1[-1]))
        m["klines_row_field_set"] = sorted(r_last[-1].keys())
        m["klines_row_field_count"] = len(m["klines_row_field_set"])
        m["klines_1d_first_open_utc"] = _iso(r_first[0]["open_time_ms"])
        m["klines_1d_last_open_utc"] = _iso(r_last[-1]["open_time_ms"])
        m["klines_1d_first_open_mod_86400_seconds"] = [
            r["open_time_ms"] % 86400000 // 1000 for r in r_first[:3]]
    first, last = {}, {}
    for sym in (m["klines_dataset_dirs"] or []):
        fs = _kline_files(raw, sym, "1h")
        if not fs:
            continue
        first[sym] = _rows(os.path.join(kl, sym, "1h", fs[0]))[0]["open_time_ms"]
        last[sym] = _rows(os.path.join(kl, sym, "1h", fs[-1]))[-1]["open_time_ms"]
    m["klines_1h_first_by_symbol"] = {k: _iso(v) for k, v in first.items()}
    m["klines_1h_last_by_symbol"] = {k: _iso(v) for k, v in last.items()}
    window = [_iso(min(first.values())), _iso(max(last.values()))] if first else None
    m["raw_window_utc"] = window
    m["card_registered_raw_window"] = CARD_REGISTERED_RAW_WINDOW
    m["card_registered_funding_window"] = CARD_REGISTERED_FUNDING_WINDOW
    m["raw_window_starts_at_or_after_card_registration"] = bool(
        window and window[0][:10] >= CARD_REGISTERED_RAW_WINDOW_START)
    m["raw_history_days"] = _day_span_days(window[0], window[1]) if window else None
    m["record_primary_sample"] = list(RECORD_PRIMARY_SAMPLE)
    m["record_forward_holdout"] = list(RECORD_FORWARD_HOLDOUT)
    m["record_source_sample_covered_in_time_by_raw_window"] = bool(
        window and window[0][:10] <= RECORD_PRIMARY_SAMPLE[0]
        and window[1][:10] >= RECORD_FORWARD_HOLDOUT[1])

    # 15-minute grid alignment + flat-bar preservation (the registered timeframe items)
    steps, flat, rows_seen, syms_15m = set(), 0, 0, []
    for sym in (m["klines_dataset_dirs"] or []):
        fs = _kline_files(raw, sym, "15m")
        syms_15m.append(sym)
        if not fs:
            continue
        rr_last = _rows(os.path.join(_kline_dir(raw, sym, "15m"), fs[-1]))
        m["klines_15m_last_open_mod_900_seconds_%s" % sym] = [
            r["open_time_ms"] % 900000 // 1000 for r in rr_last[-3:]]
        m["klines_15m_last_open_utc_%s" % sym] = _iso(rr_last[-1]["open_time_ms"])
    m["klines_15m_symbols"] = syms_15m
    for f in _kline_files(raw, "BTCUSDT", "15m"):
        rr = _rows(os.path.join(_kline_dir(raw, "BTCUSDT", "15m"), f))
        rows_seen += len(rr)
        flat += sum(1 for r in rr if r["high"] == r["low"])
        for i in range(len(rr) - 1):
            steps.add((rr[i + 1]["open_time_ms"] - rr[i]["open_time_ms"]) // 1000)
    m["klines_15m_btcusdt_rows_read"] = rows_seen
    m["klines_15m_btcusdt_open_step_seconds"] = sorted(steps)
    m["klines_15m_btcusdt_flat_bar_count"] = flat
    m["klines_15m_btcusdt_first_open_utc"] = (
        _iso(_rows(os.path.join(_kline_dir(raw, "BTCUSDT", "15m"),
                                _kline_files(raw, "BTCUSDT", "15m")[0]))[0]["open_time_ms"])
        if _kline_files(raw, "BTCUSDT", "15m") else None)

    # funding (the record's optional robustness control; present)
    fu = os.path.join(ud, "funding")
    m["funding_symbol_dirs"] = _dirs(fu)
    p = os.path.join(fu, "BTCUSDT", "BTCUSDT-funding.jsonl.gz")
    if os.path.exists(p):
        rs = _rows(p)
        m["funding_row_keys"] = sorted(rs[-1].keys())
        m["funding_truth_status_values"] = sorted({str(r.get("truth_status")) for r in rs})
        m["funding_has_official_truth_rows"] = "official" in m["funding_truth_status_values"]
        m["funding_venues"] = sorted({str(r.get("venue")) for r in rs})
        m["funding_mark_price_present"] = "mark_price" in m["funding_row_keys"]
        m["funding_first_utc"] = _iso(rs[0]["funding_time_ms"])
        m["funding_last_utc"] = _iso(rs[-1]["funding_time_ms"])
        m["funding_row_count"] = len(rs)
    m["funding_row_has_trade_field"] = any(
        t in k.lower() for k in (m.get("funding_row_keys") or []) for t in TRADE_SHAPE_TOKENS)

    inst_path = os.path.join(ud, "instruments", "usdm-perp-instruments.json")
    inst = _load_json(inst_path).get("instruments", []) if os.path.exists(inst_path) else []
    m["instrument_file"] = os.path.relpath(inst_path, raw) if os.path.exists(inst_path) else None
    m["instrument_count"] = len(inst)
    m["instrument_types"] = sorted({i["fields"].get("type") for i in inst})
    m["instrument_quote_currencies"] = sorted({i["fields"].get("quote_currency")
                                              for i in inst
                                              if i["fields"].get("quote_currency")})
    m["instrument_settlement_currencies"] = sorted(
        {i["fields"].get("settlement_currency") for i in inst
         if i["fields"].get("settlement_currency")})
    m["instrument_field_names"] = sorted({k for i in inst for k in i["fields"]})
    m["instrument_trade_field_hits"] = sorted(
        f for f in m["instrument_field_names"]
        if any(t in f.lower() for t in INSTRUMENT_TRADE_FIELDS))
    m["instrument_lifecycle_field_hits"] = sorted(
        f for f in m["instrument_field_names"]
        if any(t in f.lower() for t in INSTRUMENT_LIFECYCLE_FIELDS))
    m["instrument_fee_fields_only"] = sorted(
        f for f in m["instrument_field_names"] if "fee" in f.lower())
    m["kline_row_has_trade_field"] = any(
        t in f.lower() for f in (m.get("klines_row_field_set") or []) for t in
        TRADE_SHAPE_TOKENS)
    m["kline_row_has_quote_volume"] = any(
        t == "quote_volume" for t in (m.get("klines_row_field_set") or []))

    # the store's own documentation
    schema_path = os.path.join(raw, "_meta", "SCHEMA.md")
    schema = _text(schema_path) if os.path.exists(schema_path) else ""
    flat_s = schema.replace("*", "")
    m["schema_sha256"] = _sha256_text(schema) if schema else None
    m["schema_dataset_sections"] = sorted(ln.split("## Dataset:", 1)[1].strip()
                                          for ln in schema.splitlines()
                                          if ln.startswith("## Dataset:"))
    m["schema_documents_trade_dataset"] = any(
        t in flat_s.lower() for t in ("aggtrade", "agg trade", "isbuyermaker", "bookticker",
                                      "book ticker", "order book", "level 2", "l2 book",
                                      "tick data", "trade data", "trade-level"))
    m["schema_documents_missing_splits"] = (
        "quote_volume" in flat_s and "trade count and taker-buy splits are" in flat_s)
    m["schema_declares_perp_only_market"] = (
        "Binance USD-M perpetual futures, UTC" in schema)
    m["schema_declares_spot_market"] = ("Binance spot" in schema
                                        or "USD\u24c8-M spot" in schema
                                        or "## Dataset: spot" in schema)
    m["schema_no_silent_gapfill"] = "No gaps are silently filled" in schema
    m["schema_documented_intervals"] = DOCUMENTED_INTERVALS
    m["schema_trade_word_lines"] = [ln.strip()[:100] for ln in schema.splitlines()
                                    if any(t in ln.lower()
                                           for t in ("trade", "taker", "aggressor",
                                                     "isbuyer", "quote"))][:6]
    inv_path = os.path.join(raw, "_meta", "INVENTORY.md")
    inv = _text(inv_path) if os.path.exists(inv_path) else ""
    m["inventory_sha256"] = _sha256_text(inv) if inv else None
    m["inventory_declares_usdm_perp_only"] = (
        "BINANCE, USD-M perpetual futures" in inv)
    m["inventory_declares_bars_only_migration"] = ("parquet **bars** (engine store)" in inv)
    m["inventory_declares_no_other_store_found"] = (
        "No other Binance/klines/tardis/aggTrade store was found on" in inv)
    m["meta_sha256"] = {rel: _sha256_file(os.path.join(raw, "_meta", rel))
                        for rel in ("CONFIG.json", "SCHEMA.md", "INVENTORY.md",
                                    "INSTRUMENTS_EXPORT.json", "FUNDING_EXPORT.json")
                        if os.path.exists(os.path.join(raw, "_meta", rel))}
    m["retired_store_paths_absent"] = {p: (not os.path.exists(p))
                                       for p in RETIRED_STORE_PATHS}
    m["all_retired_stores_absent"] = all(m["retired_store_paths_absent"].values())

    # the entry-name probes (one token list, full depth)
    names = _all_entries(raw, PROBE_MAX_DEPTH)
    joined = "\n".join(names)
    m["raw_entry_name_count"] = len(names)
    m["probe_tokens_tested"] = list(PROBE_TOKENS)
    m["probe_exclusions"] = dict(PROBE_EXCLUSIONS)
    m["spot_token_hits_in_entry_names"] = sorted({t for t in SPOT_TOKENS if t in joined})
    m["trade_token_hits_in_entry_names"] = sorted({t for t in TRADE_TOKENS if t in joined})
    m["venue_token_hits_in_entry_names"] = sorted({t for t in VENUE_TOKENS if t in joined})
    m["universe_token_hits_in_entry_names"] = sorted(
        {t for t in UNIVERSE_TOKENS if t in joined})
    m["raw_entry_names_sample"] = names[:25]
    m["note"] = (
        "single venue (BINANCE USD-M perpetual), four fixed contracts, three dataset "
        "families: klines whose rows carry the seven-field OHLCV shape only, that venue's "
        "funding (with mark_price and an official truth_status), and its instrument "
        "definitions. No spot market, no trade/aggTrade/tick/quote/book dataset of any "
        "kind, no membership or listing-history reference: the raw entry-name probe over "
        "%d names at depth %d returns zero hits for all %d tokens. The registered "
        "15-minute UTC bars exist (900 s, aligned) but only for the four perpetual "
        "contracts." % (len(names), PROBE_MAX_DEPTH, len(PROBE_TOKENS)))
    return m


def _spot_cache_measurement():
    """The only spot-market data file on the host: a non-canonical single-asset cache."""
    p = os.path.join(HOME, "workspace", "phase5-crypto-derivatives", "data",
                     "klines_spot_btc_1d.json")
    if not os.path.exists(p):
        return {"path": p, "present": False, "used": False}
    rows = _load_json(p)
    return {
        "path": p, "present": True, "bytes": os.path.getsize(p), "rows": len(rows),
        "symbols": ["BTCUSDT"], "interval": "1d", "row_field_count": len(rows[0]),
        "first_open_utc": _iso(rows[0][0]), "last_open_utc": _iso(rows[-1][0]),
        "market": "spot (single asset, daily)",
        "what_it_is": "a single-asset BTC spot DAILY kline cache captured from the public "
                      "REST API by an unrelated phase-5 derivatives project; not the "
                      "canonical raw and not the record's 15-minute wide spot "
                      "cross-section (183 pairs, point-in-time membership).",
        "used": False,
    }


def _trade_sample_measurement():
    """The only trade-level sample on the host: a 20-row single-instant capture."""
    d = os.path.join(HOME, "workspace", "phase12-l2-l3-execution-tca", "raw")
    agg = os.path.join(d, "binance_futures_aggtrades_btcusdt_limit20.json")
    dep = os.path.join(d, "binance_futures_depth_btcusdt_limit20.json")
    out = {"dir": d, "used": False}
    if os.path.exists(agg):
        rows = _load_json(agg)
        out["aggTrades"] = {
            "path": agg, "bytes": os.path.getsize(agg), "rows": len(rows),
            "row_field_names": "Binance aggTrade short names (a,p,q,f,l,T,m)",
            "aggressor_flag_present": any("m" in r for r in rows),
            "symbols": ["BTCUSDT"], "market": "USD-M perpetual futures",
            "first_trade_ms_utc": _iso(min(r["T"] for r in rows)),
            "last_trade_ms_utc": _iso(max(r["T"] for r in rows)),
            "what_it_is": "a bounded 20-row present-state capture (limit=20) of one "
                          "contract at one instant - real aggressor-side data, but not a "
                          "time series of any usable length.",
        }
    if os.path.exists(dep):
        snap = _load_json(dep)
        out["depth_snapshot"] = {
            "path": dep, "bytes": os.path.getsize(dep),
            "bid_levels": len(snap.get("bids") or []), "ask_levels": len(snap.get("asks") or []),
            "update_id": snap.get("lastUpdateId"),
            "exchange_time_utc": _iso(snap["E"]) if snap.get("E") else None,
            "what_it_is": "a bounded present-state L2 depth snapshot (limit=20) of one "
                          "contract at one instant.",
        }
    return out


def _usdm_daily_panel_measurement():
    """A non-canonical 12-symbol USD-M DAILY panel with taker splits (unused)."""
    d = os.path.join(HOME, "workspace", "phase7-alpha-research", "data", "derived")
    out = {"dir": d, "used": False}
    real = os.path.join(d, "real_daily.csv")
    if os.path.exists(real):
        with open(real, encoding="utf-8", errors="replace") as f:
            header = f.readline().rstrip("\n")
            n = sum(1 for _ in f)
        cols = header.split(",")
        out["real_daily"] = {
            "path": real, "bytes": os.path.getsize(real), "data_rows": n,
            "columns": cols,
            "taker_split_columns_present": all(c in cols for c in
                                               ("taker_buy_base_volume",
                                                "num_trades", "quote_volume")),
            "symbols": sorted({ln.split(",")[1] for ln in
                               open(real, encoding="utf-8").read().splitlines()[1:6]}),
            "market": "BINANCE USD-M futures (README: /fapi/v1/klines), DAILY",
            "what_it_is": "a 12-symbol DAILY USD-M panel with taker-buy columns from an "
                          "unrelated phase-7 project, whose own README states the "
                          "exchangeInfo universe is present-state and explicitly not a "
                          "point-in-time universe; daily resolution, 12 survivor-selected "
                          "symbols - not the record's 15-minute wide spot cross-section.",
        }
    return out


def _pit_membership_measurement():
    """The only machine-local point-in-time membership product (perpetual-scoped,
    DATA_BLOCKED). Measured directly instead of cited."""
    base = os.path.join(HOME, "workspace", "a1-1-phase9-pit-membership-20260830")
    out = {"dir": base, "used": False}
    cells = os.path.join(base, "membership_cells.jsonl")
    census = os.path.join(base, "coverage_census.json")
    handoff = os.path.join(base, "gate_handoff.json")
    if os.path.exists(census):
        cc = _load_json(census)
        out["coverage_census"] = {
            "path": census, "classification": cc.get("classification"),
            "aggregate": cc.get("aggregate"), "window": cc.get("window"),
        }
    if os.path.exists(handoff):
        gh = _load_json(handoff)
        out["gate_handoff"] = {
            "path": handoff, "authorized_to_start_a2": gh.get("authorized_to_start_a2"),
            "evidence_eligible_for_a2": gh.get("evidence_eligible_for_a2"),
            "classification": gh.get("classification"),
        }
    if os.path.exists(cells):
        n, supported, first, last = 0, 0, None, None
        with open(cells, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                row = json.loads(line)
                n += 1
                supported += 1 if row.get("supported") else 0
                if first is None:
                    first = row.get("cell_id")
                last = row.get("cell_id")
        out["membership_cells"] = {"path": cells, "cells": n, "supported_cells": supported,
                                   "first_cell": first, "last_cell": last}
    out["what_it_is"] = ("a non-canonical phase-9 point-in-time membership product scoped "
                         "to USDT-margined PERPETUAL symbols (12 symbols x 1,230 daily "
                         "cells); its own census classifies DATA_BLOCKED with 0 supported "
                         "cells and its handoff sets authorized_to_start_a2=false. It is "
                         "not the record's spot membership history and it is not the "
                         "canonical raw.")
    return out


def _options_quotes_measurement():
    p = os.path.join(HOME, "workspace", "phase11-options-volatility", "data",
                     "real_valid_quotes.json")
    if not os.path.exists(p):
        return {"path": p, "present": False, "used": False}
    d = _load_json(p)
    return {"path": p, "present": True, "bytes": os.path.getsize(p),
            "top_level_keys": sorted(d) if isinstance(d, dict) else None,
            "rows": len(d.get("rows") or []) if isinstance(d, dict) else len(d),
            "what_it_is": "an options-quote snapshot (European-style, single timestamp) "
                          "captured by an unrelated options project; not spot or "
                          "perpetual market data and not the canonical raw.",
            "used": False}


def measure_non_canonical_artifacts():
    """Measure the non-canonical artefacts the house-wide scan discloses.

    None of these is an alternative data source: each is measured so the round-spec's
    disclosure is a measurement rather than prose, and so a later drift (a real
    spot/trade dataset landing in one of them) cannot pass unnoticed.
    """
    out = {
        "phase5_spot_daily_cache": _spot_cache_measurement(),
        "phase12_trade_sample": _trade_sample_measurement(),
        "phase7_usdm_daily_panel": _usdm_daily_panel_measurement(),
        "phase9_pit_membership_product": _pit_membership_measurement(),
        "phase11_options_quotes": _options_quotes_measurement(),
    }
    out["none_is_the_record_required_source"] = (
        out["phase5_spot_daily_cache"].get("present") is True
        and out["phase5_spot_daily_cache"].get("rows") == 970
        and out["phase12_trade_sample"].get("aggTrades", {}).get("rows") == 20
        and out["phase9_pit_membership_product"].get("coverage_census", {}).get(
            "classification") == "DATA_BLOCKED"
        and out["phase9_pit_membership_product"].get("membership_cells", {}).get(
            "supported_cells") == 0
        and all(out[k].get("used") is False for k in
                ("phase5_spot_daily_cache", "phase12_trade_sample",
                 "phase7_usdm_daily_panel", "phase9_pit_membership_product",
                 "phase11_options_quotes")))
    return out


def run_checks(results_root, raw_root, repo_root=None, card_body_path=None,
               record_path=None, board_db=None):
    checks = []
    add = lambda cid, ok, detail: checks.append({
        "id": cid, "status": "PASS" if ok else "FAIL", "detail": detail})
    raw = measure_raw(raw_root)
    noncanon = measure_non_canonical_artifacts()
    round_dir = os.path.join(results_root, FAMILY, "rounds", ROUND)

    # C1 - one market type for one venue, and no other market directory at all (no spot
    #      market, no dated futures, no trade/aggTrade/tick/quote/book dataset dir, no
    #      second venue, no reference/membership tree under the raw root).
    md = raw["binance_market_dirs"] or []
    add("C1", md == ["usdm"] and raw["venue"] == "BINANCE" and raw["market_type"] == "usdm_perp"
        and raw["paths_named_other_market"] == [] and raw["non_binance_paths"] == []
        and raw["usdm_dataset_dirs"] == ["funding", "instruments", "klines"]
        and raw["trade_like_dataset_dirs"] == []
        and raw["reference_dirs_under_raw"] == [],
        "binance market dirs=%s venue=%s market_type=%s other_market_paths=%s "
        "non_binance_paths=%s usdm_dataset_dirs=%s trade_like_dirs=%s reference_dirs=%s"
        % (md, raw["venue"], raw["market_type"], raw["paths_named_other_market"],
           raw["non_binance_paths"], raw["usdm_dataset_dirs"],
           raw["trade_like_dataset_dirs"], raw["reference_dirs_under_raw"]))

    # C2 - the four USD-M perpetual contracts are the only local instruments; every one
    #      is USDT-quoted and settled, carries all seven intervals, and every stored row
    #      shape is the documented OHLCV-only shape.
    same = (raw["klines_dataset_dirs"] == EXPECTED_SYMBOLS
            and raw["funding_symbol_dirs"] == EXPECTED_SYMBOLS
            and raw["symbols"] == EXPECTED_SYMBOLS
            and raw["instrument_types"] == ["CryptoPerpetual"]
            and raw["instrument_count"] == len(EXPECTED_SYMBOLS)
            and raw["instrument_quote_currencies"] == ["USDT"]
            and raw["instrument_settlement_currencies"] == ["USDT"]
            and all(sorted(ivs or []) == sorted(DOCUMENTED_INTERVALS)
                    for ivs in raw["klines_interval_set_by_symbol"].values()))
    add("C2", bool(same), "klines=%s funding=%s config=%s types=%s count=%s quote=%s "
                          "settle=%s intervals=%s row_fields=%s"
        % (raw["klines_dataset_dirs"], raw["funding_symbol_dirs"], raw["symbols"],
           raw["instrument_types"], raw["instrument_count"],
           raw["instrument_quote_currencies"], raw["instrument_settlement_currencies"],
           raw["klines_interval_seconds"], raw["klines_row_field_set"]))

    # C3 - THE DECISIVE ONE (a): the record's registered market is Binance USDT SPOT
    #      pairs and there is no spot market at all. No spot path in the raw tree, no
    #      spot token in any entry name, every instrument is a CryptoPerpetual, the
    #      store's own CONFIG/INVENTORY/SCHEMA declare the USD-M perpetual market only,
    #      no instrument carries a spot type, and the only spot data file on the whole
    #      host is the measured non-canonical single-asset DAILY cache (unused).
    spot_cache = noncanon["phase5_spot_daily_cache"]
    add("C3", raw["spot_token_hits_in_entry_names"] == []
        and raw["paths_named_other_market"] == []
        and raw["instrument_types"] == ["CryptoPerpetual"]
        and raw["market_type"] == "usdm_perp"
        and raw["inventory_declares_usdm_perp_only"] is True
        and raw["schema_declares_perp_only_market"] is True
        and raw["schema_declares_spot_market"] is False
        and spot_cache.get("present") is True and spot_cache.get("rows") == 970
        and spot_cache.get("symbols") == ["BTCUSDT"] and spot_cache.get("interval") == "1d"
        and spot_cache.get("used") is False,
        "spot_token_hits=%s spot_paths=%s instrument_types=%s market_type=%s "
        "inventory_usdm_only=%s schema_perp_only=%s schema_spot=%s | host spot data: "
        "present=%s rows=%s symbols=%s interval=%s window=%s..%s used=%s"
        % (raw["spot_token_hits_in_entry_names"], raw["paths_named_other_market"],
           raw["instrument_types"], raw["market_type"],
           raw["inventory_declares_usdm_perp_only"], raw["schema_declares_perp_only_market"],
           raw["schema_declares_spot_market"], spot_cache.get("present"),
           spot_cache.get("rows"), spot_cache.get("symbols"), spot_cache.get("interval"),
           spot_cache.get("first_open_utc"), spot_cache.get("last_open_utc"),
           spot_cache.get("used")))

    # C4 - THE DECISIVE ONE (b): no trade-level / aggressor substrate. No trade token in
    #      any raw entry name, no trade-side field in any stored row shape, the store's
    #      own SCHEMA.md documents the OHLCV-only coverage limit and no trade dataset,
    #      and its inventory records the bars-only migration plus that no other store was
    #      found on the host. The only host-local trade sample is the measured 20-row
    #      single-instant capture (unused).
    trade_sample = noncanon["phase12_trade_sample"]
    add("C4", raw["trade_token_hits_in_entry_names"] == []
        and raw["kline_row_has_trade_field"] is False
        and raw["kline_row_has_quote_volume"] is False
        and raw["funding_row_has_trade_field"] is False
        and raw["instrument_trade_field_hits"] == []
        and raw["schema_documents_trade_dataset"] is False
        and raw["schema_documents_missing_splits"] is True
        and raw["schema_dataset_sections"] == DOCUMENTED_DATASET_FAMILIES
        and raw["inventory_declares_no_other_store_found"] is True
        and raw["inventory_declares_bars_only_migration"] is True
        and trade_sample.get("aggTrades", {}).get("rows") == 20
        and trade_sample.get("aggTrades", {}).get("aggressor_flag_present") is True
        and trade_sample.get("used") is False,
        "trade_token_hits=%s kline_row_has_trade_field=%s quote_volume=%s "
        "funding_row_has_trade_field=%s instrument_trade_fields=%s "
        "schema_documents_trade_dataset=%s schema_documents_missing_splits=%s "
        "documented_dataset_families=%s inventory_no_other_store=%s inventory_bars_only=%s "
        "(1 unified token list of %d tokens over %d entry names, depth<=%d) | host trade "
        "sample: rows=%s aggressor_flag=%s used=%s"
        % (raw["trade_token_hits_in_entry_names"], raw["kline_row_has_trade_field"],
           raw["kline_row_has_quote_volume"], raw["funding_row_has_trade_field"],
           raw["instrument_trade_field_hits"], raw["schema_documents_trade_dataset"],
           raw["schema_documents_missing_splits"], raw["schema_dataset_sections"],
           raw["inventory_declares_no_other_store_found"],
           raw["inventory_declares_bars_only_migration"], len(PROBE_TOKENS),
           raw["raw_entry_name_count"], PROBE_MAX_DEPTH,
           trade_sample.get("aggTrades", {}).get("rows"),
           trade_sample.get("aggTrades", {}).get("aggressor_flag_present"),
           trade_sample.get("used")))

    # C5 - the registered timeframe items that ARE present, measured: the 15-minute
    #      interval exists for exactly the four perpetual contracts, bars open on the
    #      exact UTC bucket boundary (mod 900 s = 0), the bar grid is uniform (step 900 s)
    #      and the store documents that missing bars are absent rather than filled. The
    #      raw window covers the record's sample period in TIME - but only for the
    #      unregistered perpetual market.
    aligned = all(raw.get("klines_15m_last_open_mod_900_seconds_%s" % s) == [0, 0, 0]
                  for s in EXPECTED_SYMBOLS)
    add("C5", raw["klines_15m_symbols"] == EXPECTED_SYMBOLS
        and 900 in raw["klines_interval_seconds"]
        and aligned
        and raw["klines_15m_btcusdt_open_step_seconds"] == [900]
        and raw["schema_no_silent_gapfill"] is True
        and raw["raw_window_starts_at_or_after_card_registration"] is True
        and raw["record_source_sample_covered_in_time_by_raw_window"] is True,
        "15m symbols=%s intervals=%s aligned_open_mod_900=%s step_seconds=%s flat_bars=%s "
        "rows_read=%s window=%s..%s record_sample_in_time_covered=%s gapfill=%s"
        % (raw["klines_15m_symbols"], raw["klines_interval_seconds"],
           {s: raw.get("klines_15m_last_open_mod_900_seconds_%s" % s) for s in EXPECTED_SYMBOLS},
           raw["klines_15m_btcusdt_open_step_seconds"],
           raw["klines_15m_btcusdt_flat_bar_count"], raw["klines_15m_btcusdt_rows_read"],
           (raw["raw_window_utc"] or [None, None])[0], (raw["raw_window_utc"] or [None, None])[1],
           raw["record_source_sample_covered_in_time_by_raw_window"],
           raw["schema_no_silent_gapfill"]))

    # C6 - the AUXILIARY and SUBSTITUTE surfaces: the funding control the record lists
    #      optionally IS present (venue BINANCE, official truth_status, mark_price), while
    #      the second venue, the quote/book surface, the order-book depth snapshots and
    #      the instrument listing/delisting lifecycle fields are absent; the derivative
    #      basis series cannot be constructed without a spot leg; the machine-local
    #      point-in-time membership product is perp-scoped and DATA_BLOCKED (0 supported
    #      cells); the retired engine stacks are gone from disk; and every host-local
    #      substitute is measured and unused.
    mem = noncanon["phase9_pit_membership_product"]
    add("C6", raw["non_binance_paths"] == [] and raw["funding_venues"] == ["BINANCE"]
        and raw["funding_has_official_truth_rows"] is True
        and raw["funding_mark_price_present"] is True
        and raw["all_retired_stores_absent"] is True
        and raw["instrument_lifecycle_field_hits"] == []
        and raw["instrument_fee_fields_only"] == ["maker_fee", "taker_fee"]
        and raw["schema_no_silent_gapfill"] is True
        and mem.get("coverage_census", {}).get("classification") == "DATA_BLOCKED"
        and mem.get("membership_cells", {}).get("cells") == 14760
        and mem.get("membership_cells", {}).get("supported_cells") == 0
        and mem.get("gate_handoff", {}).get("authorized_to_start_a2") is False
        and mem.get("used") is False
        and noncanon["phase7_usdm_daily_panel"].get("used") is False
        and noncanon["phase11_options_quotes"].get("used") is False,
        "non_binance_paths=%s funding_venues=%s funding_truth=%s mark_price=%s "
        "retired_absent=%s instrument_lifecycle_fields=%s fee_fields=%s | membership "
        "product: cells=%s supported=%s classification=%s authorized_to_start_a2=%s used=%s"
        % (raw["non_binance_paths"], raw["funding_venues"],
           raw["funding_truth_status_values"], raw["funding_mark_price_present"],
           raw["all_retired_stores_absent"], raw["instrument_lifecycle_field_hits"],
           raw["instrument_fee_fields_only"],
           mem.get("membership_cells", {}).get("cells"),
           mem.get("membership_cells", {}).get("supported_cells"),
           mem.get("coverage_census", {}).get("classification"),
           mem.get("gate_handoff", {}).get("authorized_to_start_a2"), mem.get("used")))

    # ---- artifact side
    spec_path = os.path.join(round_dir, "round-spec.json")
    verdict_path = os.path.join(round_dir, "verdict.json")
    spec = _load_json(spec_path) if os.path.exists(spec_path) else {}
    verdict = _load_json(verdict_path) if os.path.exists(verdict_path) else {}

    # C7 - the recorded house-wide scan is internally consistent, every hit carries an
    #      acceptable non-data classification (none unclassified), the registered probe
    #      agrees with the live re-measurement, the disclosed non-canonical artefacts
    #      match a live re-measurement, and any path that appeared since the snapshot was
    #      written is one of this card's own artifacts - never a newly-visible data store.
    gate = spec.get("prerequisite_gate") or {}
    meas = gate.get("measured_available") or {}
    hs = gate.get("host_wide_scan") or {}
    house = hs.get("house_wide_probe") or {}
    cls_counts = house.get("classification_counts") or {}
    stored_paths = {h.get("path") for h in (house.get("hits") or [])}
    live_scan = host_scan()
    live_paths = {h.get("path") for h in (live_scan.get("hits") or [])}
    own_classes = {"own_evidence_snapshot_false_positive", "own_round_artifact_false_positive",
                   "own_family_directory_false_positive", "own_checker_source_false_positive",
                   "own_handoff_pool_false_positive"}
    live_only = sorted(live_paths - stored_paths)
    stored_only = sorted(stored_paths - live_paths)
    drift_ok = all(_classify_house_hit(p) in own_classes for p in live_only)
    noncanon_stored = gate.get("disclosed_non_canonical_artifacts") or {}
    add("C7", house.get("unclassified_hits") == []
        and not cls_counts.get(UNCLASSIFIED_CLASS)
        and sum(v for k, v in cls_counts.items() if k != UNCLASSIFIED_CLASS)
        == house.get("hit_count")
        and house.get("hit_count") == len(house.get("hits") or [])
        + (house.get("hits_capped_by") or 0)
        and bool(house.get("note"))
        and house.get("probe_exclusions") == PROBE_EXCLUSIONS
        and house.get("probe_tokens") == list(PROBE_TOKENS)
        and set(cls_counts) <= set(HOUSE_HIT_CLASSES)
        and meas.get("spot_token_hits_in_entry_names")
        == raw["spot_token_hits_in_entry_names"]
        and meas.get("trade_token_hits_in_entry_names")
        == raw["trade_token_hits_in_entry_names"]
        and meas.get("venue_token_hits_in_entry_names")
        == raw["venue_token_hits_in_entry_names"]
        and meas.get("universe_token_hits_in_entry_names")
        == raw["universe_token_hits_in_entry_names"]
        and meas.get("binance_market_dirs") == raw["binance_market_dirs"]
        and meas.get("usdm_dataset_dirs") == raw["usdm_dataset_dirs"]
        and meas.get("non_binance_paths") == raw["non_binance_paths"]
        and meas.get("symbols") == raw["symbols"]
        and meas.get("instrument_types") == raw["instrument_types"]
        and meas.get("instrument_field_names") == raw["instrument_field_names"]
        and meas.get("klines_row_field_set") == raw["klines_row_field_set"]
        and _noncanon_matches(noncanon_stored, noncanon)[0] is True
        and drift_ok,
        "house_hits_stored=%s classification_counts=%s unclassified=%s hits_listed=%s "
        "capped_by=%s raw_name_probe_hits(artifact spot/trade)=(%s/%s) (live)=(%s/%s) | "
        "artifact_vs_live_raw_agreement(market_dirs/datasets/non_binance/symbols/types/"
        "instrument_fields/kline_fields/venue+universe probes)=%s | "
        "live_house_hits=%s live_only=%s stored_only=%s drift_all_own_artifacts=%s | "
        "noncanonical_measured_match=%s"
        % (house.get("hit_count"), cls_counts, house.get("unclassified_hits"),
           len(house.get("hits") or []), house.get("hits_capped_by"),
           meas.get("spot_token_hits_in_entry_names"),
           meas.get("trade_token_hits_in_entry_names"),
           raw["spot_token_hits_in_entry_names"], raw["trade_token_hits_in_entry_names"],
           (meas.get("binance_market_dirs") == raw["binance_market_dirs"]
            and meas.get("usdm_dataset_dirs") == raw["usdm_dataset_dirs"]
            and meas.get("non_binance_paths") == raw["non_binance_paths"]
            and meas.get("symbols") == raw["symbols"]
            and meas.get("instrument_types") == raw["instrument_types"]
            and meas.get("instrument_field_names") == raw["instrument_field_names"]
            and meas.get("klines_row_field_set") == raw["klines_row_field_set"]
            and meas.get("venue_token_hits_in_entry_names")
            == raw["venue_token_hits_in_entry_names"]
            and meas.get("universe_token_hits_in_entry_names")
            == raw["universe_token_hits_in_entry_names"]),
           live_scan.get("hit_count"), live_only, stored_only, drift_ok,
           _noncanon_matches(noncanon_stored, noncanon)[0]))

    # C8 - the registration still states the record's own requirement (it was NOT shrunk
    #      to the locally available four perp contracts) and records the missing surfaces
    #      item by item.
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
        and all(matrix[k].get("status") == v
                for k, v in DECISIVE_REQUIRED_STATUS.items())
        and all(matrix[k].get("status") == v
                for k, v in NON_DECISIVE_REQUIRED_STATUS.items())
        and matrix["klines_15m_utc_bucket_start"].get("status")
        == "PRESENT_FOR_FOUR_PERP_CONTRACTS"
        and matrix["venue_binance_usdm_perpetual"].get("status") == "PRESENT"
        and univ.get("universe_shrunk_to_local_list") is False
        and univ.get("spot_dataset_available") is False
        and univ.get("spot_name_probe_hits") == raw["spot_token_hits_in_entry_names"]
        and univ.get("trade_dataset_available") is False
        and univ.get("trade_name_probe_hits") == raw["trade_token_hits_in_entry_names"]
        and univ.get("membership_history_available") is False
        and univ.get("second_venue_available") is False
        and univ.get("order_book_available") is False
        and univ.get("probe_tokens_tested") == list(PROBE_TOKENS)
        and univ.get("probe_exclusions") == PROBE_EXCLUSIONS
        and univ.get("local_perpetual_contracts") == EXPECTED_SYMBOLS
        and univ.get("source_spot_pair_count") == RECORD_SOURCE_SAMPLE_COUNT
        and univ.get("source_universe_screen_date") == RECORD_UNIVERSE_SCREEN_DATE
        and univ.get("record_required_items_registered") == RECORD_REQUIRED_ITEMS
        and univ.get("card_excerpt_items") == CARD_REQUIRED_EXCERPT_ITEMS
        and univ.get("restored_required_items")
        == RECORD_REQUIRED_ITEMS - CARD_REQUIRED_EXCERPT_ITEMS
        and (spec.get("falsification") or {}).get("battery_items")
        == RECORD_FALSIFICATION_ITEMS
        and (spec.get("falsification") or {}).get("card_excerpt_items")
        == CARD_FALSIFICATION_EXCERPT_ITEMS
        and (spec.get("falsification") or {}).get("restored_items")
        == RECORD_FALSIFICATION_ITEMS - CARD_FALSIFICATION_EXCERPT_ITEMS
        and (spec.get("falsification") or {}).get("limitations_items", {}).get("record")
        == RECORD_LIMITATIONS_ITEMS
        and (spec.get("falsification") or {}).get("limitations_items", {}).get("card_excerpt")
        == CARD_LIMITATIONS_EXCERPT_ITEMS
        and (spec.get("hypothesis") or {}).get("research_interpretation_items", {}).get(
            "record") == RECORD_RESEARCH_INTERPRETATION_ITEMS
        and (spec.get("hypothesis") or {}).get("research_interpretation_items", {}).get(
            "card_excerpt") == CARD_RESEARCH_INTERPRETATION_EXCERPT_ITEMS
        and (spec.get("signal_semantics") or {}).get("execution_assumptions_items")
        == RECORD_EXECUTION_ASSUMPTIONS_ITEMS
        and (spec.get("signal_semantics") or {}).get("decision_variable_available") is False
        and (spec.get("expected") or {}).get("status") == "not_computable"
        and (spec.get("strategy_domain") or {}).get("status") == "not_registered"
        and (spec.get("launch") or {}).get("attempts_launched") == 0
        and bool(spec.get("excerpt_source_map"))
    )
    add("C8", bool(ok8),
        "round-spec ids/gate=%s required_available=%s decisive=%s shrunk=%s "
        "spot_available=%s trade_available=%s membership_available=%s second_venue=%s "
        "matrix_items=%d probe_tokens_equal=%s"
        % (gate.get("outcome"), gate.get("required_data_available"),
           {k: matrix.get(k, {}).get("status") for k in DECISIVE_MATRIX_ITEMS},
           univ.get("universe_shrunk_to_local_list"), univ.get("spot_dataset_available"),
           univ.get("trade_dataset_available"), univ.get("membership_history_available"),
           univ.get("second_venue_available"), len(matrix),
           univ.get("probe_tokens_tested") == list(PROBE_TOKENS)))

    # C9 - the terminal verdict states the contract-mandated values.
    fail = verdict.get("failure") or {}
    yld = verdict.get("yield") or {}
    req = verdict.get("prerequisite") or {}
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
        and req.get("spot_dataset_available") is False
        and req.get("trade_dataset_available") is False
        and req.get("membership_history_available") is False
        and req.get("required_data_available") is False
    )
    add("C9", ok9, "verdict=%s run_id=%s layer=%s class=%s yield=%s run_ids=%s spot=%s "
                   "trade=%s membership=%s"
        % (verdict.get("verdict"), verdict.get("run_id"), fail.get("layer"), fail.get("class"),
           yld.get("yield_decision"), verdict.get("evidence_run_ids"),
           req.get("spot_dataset_available"), req.get("trade_dataset_available"),
           req.get("membership_history_available")))

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
            "measured_raw": raw, "measured_non_canonical": noncanon, "checks": checks,
            "overall": "PASS" if all(c["status"] == "PASS" for c in checks) else "FAIL"}


def _noncanon_matches(stored, live):
    """The stored disclosure must equal the live re-measurement on the decisive fields.

    Volatile bookkeeping (file sizes, capture ids) is compared only where it is the
    measurement itself: the spot cache's row count and window, the trade sample's row
    count and aggressor flag, the membership product's cell counters and classification,
    and every `used=false` flag.
    """
    if not stored or not live:
        return False, {"reason": "missing stored or live measurement"}
    diffs = {}
    s5, l5 = stored.get("phase5_spot_daily_cache") or {}, live.get("phase5_spot_daily_cache") or {}
    for k in ("present", "rows", "symbols", "interval", "first_open_utc", "last_open_utc",
              "used"):
        if s5.get(k) != l5.get(k):
            diffs["phase5.%s" % k] = {"stored": s5.get(k), "live": l5.get(k)}
    s12, l12 = stored.get("phase12_trade_sample") or {}, live.get("phase12_trade_sample") or {}
    for k in ("rows", "aggressor_flag_present", "symbols"):
        if (s12.get("aggTrades") or {}).get(k) != (l12.get("aggTrades") or {}).get(k):
            diffs["phase12.aggTrades.%s" % k] = {"stored": (s12.get("aggTrades") or {}).get(k),
                                                 "live": (l12.get("aggTrades") or {}).get(k)}
    if (s12.get("depth_snapshot") or {}).get("bid_levels") != \
            (l12.get("depth_snapshot") or {}).get("bid_levels"):
        diffs["phase12.depth.bid_levels"] = {"stored": (s12.get("depth_snapshot") or {}).get(
            "bid_levels"), "live": (l12.get("depth_snapshot") or {}).get("bid_levels")}
    if s12.get("used") != l12.get("used"):
        diffs["phase12.used"] = {"stored": s12.get("used"), "live": l12.get("used")}
    s9, l9 = (stored.get("phase9_pit_membership_product") or {}), \
             (live.get("phase9_pit_membership_product") or {})
    for k in ("cells", "supported_cells"):
        if (s9.get("membership_cells") or {}).get(k) != (l9.get("membership_cells") or {}).get(k):
            diffs["phase9.membership_cells.%s" % k] = {
                "stored": (s9.get("membership_cells") or {}).get(k),
                "live": (l9.get("membership_cells") or {}).get(k)}
    for k in ("classification", "authorized_to_start_a2"):
        src = "coverage_census" if k == "classification" else "gate_handoff"
        if (s9.get(src) or {}).get(k) != (l9.get(src) or {}).get(k):
            diffs["phase9.%s.%s" % (src, k)] = {"stored": (s9.get(src) or {}).get(k),
                                                "live": (l9.get(src) or {}).get(k)}
    s7, l7 = stored.get("phase7_usdm_daily_panel") or {}, live.get("phase7_usdm_daily_panel") or {}
    if (s7.get("real_daily") or {}).get("data_rows") != (l7.get("real_daily") or {}).get("data_rows"):
        diffs["phase7.real_daily.data_rows"] = {
            "stored": (s7.get("real_daily") or {}).get("data_rows"),
            "live": (l7.get("real_daily") or {}).get("data_rows")}
    if s7.get("used") is not False or l7.get("used") is not False:
        diffs["phase7.used"] = {"stored": s7.get("used"), "live": l7.get("used")}
    if stored.get("none_is_the_record_required_source") is not True:
        diffs["none_is_the_record_required_source"] = stored.get(
            "none_is_the_record_required_source")
    return (diffs == {}), diffs


# --------------------------------------------------------------------------- sources
def _resolve_source_texts(repo_root=None, card_body_path=None, record_path=None,
                          board_db=None, expected_card_sha=None):
    """The four declared verbatim sources, read from their live locations."""
    repo_root = repo_root or DEFAULT_REPO
    record_path = record_path or DEFAULT_RECORD
    board_db = board_db or DEFAULT_BOARD_DB
    contract_path = os.path.join(repo_root, "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md")
    texts = {}
    texts["contract"] = _text(contract_path)
    texts["record"] = _text(record_path)
    if _LIFECYCLE_FOOTER is None:
        raise RuntimeError("runtime/production_handoff.py LIFECYCLE_FOOTER not importable")
    texts["footer"] = _LIFECYCLE_FOOTER
    if card_body_path:
        texts["card"] = _text(card_body_path)
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

    A `*_verbatim` key may hold a single string, a list of strings, or a nested block;
    all of them are resolved, so the convention cannot be dodged by wrapping an excerpt
    in a list or an object. `excerpt_source_map` is the map itself, not content.
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


# ------------------------------------------------------------------ control machinery
def _copy_tree(results_root, tmp):
    dst = os.path.join(tmp, FAMILY)
    os.makedirs(os.path.join(dst, "rounds"))
    shutil.copy(os.path.join(results_root, FAMILY, "family.json"),
                os.path.join(dst, "family.json"))
    shutil.copytree(os.path.join(results_root, FAMILY, "rounds", ROUND),
                    os.path.join(dst, "rounds", ROUND))
    return os.path.join(dst, "rounds", ROUND)


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
        "attempt_fabricated_as_INCOMPLETE": lambda d: _fabricate_attempt(d, name="INCOMPLETE"),
        "required_data_available_flipped_true": lambda d: _tamper_spec(d, lambda s: s[
            "prerequisite_gate"].update({
                "required_data_available": True, "outcome": "PREREQUISITE_PRESENT",
                "missing": []})),
        "spot_dataset_claimed_available": lambda d: _tamper_spec(d, lambda s: s[
            "universe_registration"].update({"spot_dataset_available": True})),
        "trade_dataset_claimed_available": lambda d: _tamper_spec(d, lambda s: s[
            "universe_registration"].update({"trade_dataset_available": True,
                                             "membership_history_available": True,
                                             "second_venue_available": True})),
        "spot_matrix_item_claimed_present": lambda d: _tamper_spec(
            d, lambda s: _set_matrix_status(s, "spot_ohlcv_15m_candles", "PRESENT")),
        "taker_volume_matrix_item_claimed_present": lambda d: _tamper_spec(
            d, lambda s: _set_matrix_status(
                s, "taker_buy_base_volume_or_aggressor_classification", "PRESENT")),
        "membership_matrix_item_claimed_present": lambda d: _tamper_spec(
            d, lambda s: _set_matrix_status(s, "universe_point_in_time_membership_history",
                                            "PRESENT")),
        "universe_shrunk_to_the_local_four_contract_list": lambda d: _tamper_spec(d, lambda s: s[
            "universe_registration"].update({
                "universe_shrunk_to_local_list": True,
                "universe_registered_text": "BINANCE USD-M perpetual, 4 contracts"})),
        "spot_probe_hits_claimed_nonempty_while_live_probe_is_empty": lambda d: _tamper_spec(
            d, lambda s: s["universe_registration"].update(
                {"spot_name_probe_hits": ["spot"]})),
        "probe_token_list_narrowed": lambda d: _tamper_spec(d, lambda s: s[
            "universe_registration"].update({"probe_tokens_tested": ["spot", "trade"]})),
        "required_excerpt_items_claimed_twelve": lambda d: _tamper_spec(
            d, lambda s: s["universe_registration"].update({"card_excerpt_items": 12})),
        "falsification_battery_truncated_to_the_card_excerpt": lambda d: _tamper_spec(
            d, lambda s: s["falsification"].update({"battery_items": 6, "restored_items": 0})),
        "searched_axis_mislabelled_user_fixed": lambda d: _tamper_spec(
            d, lambda s: s["dca_domain"].update({"spacing_pct_status": "USER_FIXED"})),
        "dca_grid_truncated_to_47": lambda d: _tamper_spec(
            d, lambda s: s["dca_domain"]["grid"].pop()),
        "memberhsip_product_claim_replaced_by_an_attempt_dir": lambda d: _fabricate_attempt(
            d, name="DONE"),
        "verbatim_leaf_replaced_by_a_paraphrase": lambda d: _tamper_spec(
            d, lambda s: s["hypothesis"].update(
                {"identity_verbatim": "# Crypto intraday sign mean reversion (paraphrased)"})),
        "verbatim_digest_removed_from_the_map": lambda d: _tamper_spec(
            d, lambda s: s["excerpt_source_map"].pop("identity_verbatim", None)),
        "verbatim_source_repointed": lambda d: _tamper_spec(
            d, lambda s: s["excerpt_source_map"]["identity_verbatim"].update(
                {"source": "card"})),
        "verbatim_chars_field_falsified": lambda d: _tamper_spec(
            d, lambda s: s["excerpt_source_map"]["identity_verbatim"].update({"chars": 1})),
        "verbatim_leaf_relabelled_as_authored_prose": lambda d: _tamper_spec(
            d, lambda s: s["hypothesis"].update(
                {"identity_note": s["hypothesis"].pop("identity_verbatim")})),
    }
    results = []
    ok = True
    for name, mutate in variants.items():
        tmp = tempfile.mkdtemp(prefix="xq-sigmr-prereq-selftest-")
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

    Build a temp raw tree that carries what this record would need: a Binance SPOT
    market with 15-minute klines, spot aggTrades carrying an `isBuyerMaker` flag, a
    bookTicker quote surface, a second venue, extra contracts and `_ref/` membership
    and listing-history references. The raw-side checks must flip to FAIL.
    """
    tmp = tempfile.mkdtemp(prefix="xq-sigmr-prereq-rawfix-")
    try:
        fixture = os.path.join(tmp, "raw")
        shutil.copytree(raw_root, fixture,
                        ignore=shutil.ignore_patterns(".DS_Store", "__pycache__"))
        # the record's required market: a Binance USDT spot market with 15-minute candles
        for sym in ("BTCUSDT", "ETHUSDT", "XRPUSDT"):
            d = os.path.join(fixture, "binance", "spot", "klines", sym, "15m")
            os.makedirs(d)
            _kw_gz(os.path.join(d, "%s-15m-2025-01.jsonl.gz" % sym),
                   {"open_time_ms": 1735689600000, "close_time_ms": 1735690499999,
                    "open": "93450.10", "high": "93480.00", "low": "93440.00",
                    "close": "93470.00", "volume": "12.5"})
        # the record's conditioning substrate: trade-level rows with an aggressor side
        for sym in ("BTCUSDT", "ETHUSDT"):
            d = os.path.join(fixture, "binance", "spot", "aggTrades", sym)
            os.makedirs(d)
            _kw_gz(os.path.join(d, "%s-aggTrades-2025-01.jsonl.gz" % sym),
                   {"a": 1, "p": "93450.10", "q": "0.42", "T": 1735689600123, "m": False})
        # a quote surface and a second venue
        bd = os.path.join(fixture, "binance", "usdm", "bookTicker", "BTCUSDT")
        os.makedirs(bd)
        _kw_gz(os.path.join(bd, "BTCUSDT-bookTicker-2025-01.jsonl.gz"),
               {"ts_ms": 1735689600123, "bid_price": "93449.9", "ask_price": "93450.1",
                "bid_qty": "1.0", "ask_qty": "1.2"})
        os.makedirs(os.path.join(fixture, "bybit", "spot", "klines", "BTCUSDT", "15m"))
        # a contract the record does not register, at the registered interval
        d = os.path.join(fixture, "binance", "usdm", "klines", "XRPUSDT", "15m")
        os.makedirs(d)
        _kw_gz(os.path.join(d, "XRPUSDT-15m-2025-01.jsonl.gz"),
               {"open_time_ms": 1735689600000, "close_time_ms": 1735690499999,
                "open": "2.10", "high": "2.11", "low": "2.09", "close": "2.105",
                "volume": "1000.0"})
        # a point-in-time membership / listing-history reference tree
        ref = os.path.join(fixture, "_ref", "pit_membership")
        os.makedirs(ref)
        with open(os.path.join(ref, "membership_cells.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"cell_id": "BTCUSDT|2025-01-01", "supported": True,
                                "membership_status": "listed"}) + "\n")
        ref2 = os.path.join(fixture, "_ref", "listing_history")
        os.makedirs(ref2)
        with open(os.path.join(ref2, "listing_dates.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"symbol": "BTCUSDT", "listing_date": "2017-08-17"}) + "\n")
        res = run_checks(results_root, fixture)
        failed = [c["id"] for c in res["checks"] if c["status"] == "FAIL"]
        # C1-C6 are pure raw-measurement checks. C7 and C8 additionally re-assert that the
        # persisted artifact agrees with the LIVE raw measurement (spot/trade name probes,
        # non-canonical substitute measurements), so a fixture that plants the record's
        # required surfaces must flip them too.
        expected = {"C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8"}
        return {"raw_fixture_control": {
            "failed_checks": sorted(failed), "expected": sorted(expected),
            "why": "the fixture plants what this record needs (a Binance spot market with "
                   "15m klines, spot aggTrades with an `isBuyerMaker` flag, a bookTicker "
                   "quote surface, a second venue, an extra contract and `_ref/` "
                   "membership + listing-history references); C1-C6 re-measure the raw and "
                   "C7-C8 re-assert the artifact-to-live-raw agreement, so all eight must "
                   "flip.",
            "detail": {c["id"]: c["detail"] for c in res["checks"] if c["id"] in expected},
            "overall": res["overall"]},
            "overall": "PASS" if expected <= set(failed) else "FAIL"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------------ host scan
DATA_EXTS = (".csv", ".parquet", ".jsonl", ".gz", ".bin", ".feather", ".h5", ".npy", ".arrow",
             ".db", ".sqlite", ".raw", ".hdf5", ".zst", ".zip", ".tar")
DOC_EXTS = (".md", ".txt", ".rst", ".mdx", ".ipynb", ".pdf", ".tsv", ".xml")
LOG_EXTS = (".err", ".out", ".log", ".tmp")
CODE_EXTS = (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".sh", ".yaml",
             ".yml", ".json", ".toml", ".cfg", ".ini", ".html", ".css", ".sql", ".rs", ".go",
             ".c", ".h", ".cpp")


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
    """A hit is acceptable only if it is a document, code/markup, session/log/cache, our
    own provenance artifact, a build artifact, an out-of-scope research project, an
    already-disclosed non-canonical substitute, a dataset-less stub, or an ordinary-name
    collision of a generic probe token - never a spot-market or trade-level data store for
    the registered instruments that is not already measured and disclosed. Those land in
    UNCLASSIFIED_CLASS.
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
    if "/_handoff/" in low:
        return "own_handoff_pool_false_positive"
    if base.endswith(".py") and ("prerequisite_check" in base or "prerequisite_gate" in base):
        return "own_checker_source_false_positive"
    if any(m in low for m in BUILD_ARTIFACT_MARKERS):
        return "build_artifact_false_positive"
    if any(m in low for m in STICKER_MARKERS):
        return "telegram_sticker_export_false_positive"
    if any(m in low for m in SOCKET_MARKERS):
        return "gateway_socket_false_positive"
    if any(m in low for m in OWN_GRID_MARKERS):
        return "own_pipeline_grid_artifact_false_positive"
    if any(m in low for m in HERMES_TMP_MARKERS):
        return "hermes_tmp_artifact_false_positive"
    if any(m in low for m in FIXTURE_MARKERS):
        return "repo_fixture_false_positive"
    if any(m in low for m in OUT_OF_SCOPE_MARKERS):
        return "out_of_scope_market_research_false_positive"
    # the measured, disclosed, unused substitutes keep their own classes
    if "phase5-crypto-derivatives" in low and "klines_spot" in low:
        return "disclosed_non_canonical_spot_cache_false_positive"
    if "phase12-l2-l3-execution-tca" in low:
        return "disclosed_non_canonical_trade_sample_false_positive"
    if "phase7-alpha-research" in low and ("/data/" in low or ".csv" in low):
        return "disclosed_non_canonical_perp_panel_false_positive"
    if ("a1-1-phase9-pit-membership" in low or "phase10-pit-bitemporal" in low):
        return "disclosed_non_canonical_membership_product_false_positive"
    if "phase11-options-volatility" in low and base.endswith(".json"):
        return "disclosed_non_canonical_options_quotes_false_positive"
    if WIKI_DIR_MARKER in low or (low.endswith(".md") and "/wiki/" in low):
        return ("canonical_record_document" if FAMILY in low else "related_wiki_document")
    if any(m in low for m in NON_CANONICAL_MARKERS):
        return "non_canonical_staging_copy_false_positive"
    if any("/%s/" % d in low for d in BACKUP_LIKE_DIRS) or any(m in low
                                                              for m in BACKUP_MARKERS):
        return "non_canonical_staging_copy_false_positive"
    if os.path.join(HOME, ".hermes") in path and ("sessions" in low or "logs" in low
                                                 or "kanban" in low or "cron" in low
                                                 or "/archive/" in low
                                                 or "/profiles/" in low):
        return "agent_session_or_log_false_positive"
    if "/cache/" in low:
        return "hermes_cache_false_positive"
    if "/contributors/" in low:
        return "hermes_skill_or_catalog_false_positive"
    if "/skills/" in low or "/plugins/" in low or "/optional-skills/" in low or \
            "/plugin-catalog/" in low:
        return "hermes_skill_or_catalog_false_positive"
    if os.path.isdir(path):
        probe = _dir_data_probe(path)
        if probe["data_file_count"] == 0 and probe["other_file_count"] > 0:
            return "dataset_stub_directory_false_positive"
        return UNCLASSIFIED_CLASS
    if any(m in low for m in UNRELATED_SPOT_MARKERS):
        return "unrelated_source_identifier_false_positive"
    if low.endswith(DOC_EXTS):
        return "repo_documentation_false_positive"
    if low.endswith(LOG_EXTS):
        return "agent_session_or_log_false_positive"
    if low.endswith(CODE_EXTS):
        return "source_or_markup_false_positive"
    return UNCLASSIFIED_CLASS


def _probe_hits(roots, tokens, max_depth=6):
    """Every path whose own name - or whose containing directory path - carries a token."""
    hits = []
    for root in roots:
        for dp, dn, fn in os.walk(root):
            rel = os.path.relpath(dp, root)
            if rel.count(os.sep) >= max_depth:
                dn[:] = []
                continue
            dn[:] = [d for d in dn if not d.startswith(".") and d not in SCAN_SKIP_DIRS]
            if any(t in dp.lower() for t in tokens):
                hits.append(dp)
            for f in fn:
                if any(t in f.lower() for t in tokens):
                    hits.append(os.path.join(dp, f))
    return sorted(set(hits))


def host_scan(hits_cap=400, fresh=False):
    """House-wide search for the record's required data surfaces (read-only).

    One unified token list is used here and in the raw entry-name probe, and NO token is
    excluded: generic tokens such as `spot` (inside `spotify`/`spotlight`) and `trade`
    (inside `trader`/`trading`) are probed and every collision is classified instead, so
    an exclusion can never hide a hit.

    The result is memoised for the process: the self-test runs this checker once per
    tampered copy and the host tree is not what those controls vary. `fresh=True` (used by
    the `--host-scan` CLI path, which prints the measurement) recomputes it.
    """
    if not fresh and "result" in _HOST_SCAN_CACHE:
        return _HOST_SCAN_CACHE["result"]
    scanned, skipped = [], []
    for r in TMP_ROOTS:
        (scanned if os.path.isdir(r) else skipped).append(r)

    records, counts = [], {}
    for p in _probe_hits(scanned, PROBE_TOKENS):
        cls = _classify_house_hit(p)
        counts[cls] = counts.get(cls, 0) + 1
        records.append({"path": p, "classification": cls})
    unclassified = [r for r in records if r["classification"] == UNCLASSIFIED_CLASS]
    listed = records[:hits_cap]
    generic = {"spot": "spotify/spotlight/spotcheck/hotspot/GameSpot/despotak",
               "trade": "trader/trading/trades/tradersunion",
               "trades": "trades/aggregate-trades documentation"}
    out = {"roots_scanned": scanned, "skipped_roots": skipped,
           "probe_tokens": list(PROBE_TOKENS),
           "probe_exclusions": dict(PROBE_EXCLUSIONS),
           "generic_token_collision_notes": generic,
           "hit_count": len(records),
           "classification_counts": counts,
           "hits_listed": len(listed),
           "hits_capped_by": max(0, len(records) - len(listed)),
           "hits": listed,
           "unclassified_hits": unclassified,
           "note": ("a house-wide name probe with the same %d-token list used for the raw "
                    "tree was run over %d roots at depth<=6; it returned %d hit(s) in %d "
                    "classification(s), %d of them unclassified. No token was excluded: "
                    "the generic tokens %s collide with ordinary names the host already "
                    "uses and every such hit is classified rather than dropped. No hit "
                    "class is a spot-market or trade-level data store for the registered "
                    "instruments other than the five measured, disclosed, unused "
                    "non-canonical substitutes."
                    % (len(PROBE_TOKENS), len(scanned), len(records), len(counts),
                       len(unclassified), ", ".join(sorted(generic))))}
    _HOST_SCAN_CACHE["result"] = out
    return out


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
        print(json.dumps(host_scan(fresh=True), indent=2, ensure_ascii=False))
        return 0

    if not os.path.isdir(args.results_root) or not os.path.isdir(args.raw_root):
        print("usage error: results/raw root not mounted", file=sys.stderr)
        return 2

    if args.measure_only:
        print(json.dumps({"raw": measure_raw(args.raw_root),
                          "non_canonical": measure_non_canonical_artifacts()},
                         indent=2, ensure_ascii=False))
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
