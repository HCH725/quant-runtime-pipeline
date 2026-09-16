#!/usr/bin/env python3
"""Prerequisite-gate read-back checker for the family

    crypto-quarter-hour-opening-order-imbalance-medium-horizon-2026-08-31

Card t_b3ac2c5d terminalises this family as TECHNICAL_INCOMPLETE because the
record's required data is not in the canonical raw:

  * the record's decision variable is the signed order imbalance measured over
    the FIRST 10 SECONDS of every quarter-hour opening, OI_t = signed_order_flow_t
    / total_volume_t, with the aggressor side taken from Binance's
    `isBuyerMaker` trade flag. The canonical raw holds klines, funding and
    instrument definitions for four USD-M perpetual contracts and nothing
    else: there is no trade/aggTrade/tick dataset of any kind, at any depth,
    in any row shape. The finest stored interval is 5m (300 s) and the only
    bars that even open on a quarter-hour boundary are 15m bars covering 900 s
    - i.e. 30x and 90x the registered measurement window - so the signal's own
    input window cannot be reconstructed;
  * the record's registered universe is six perpetual contracts (BTC, ETH,
    XRP, SOL, DOGE, ADA); locally only three of them exist (BTCUSDT, ETHUSDT,
    SOLUSDT) plus BNBUSDT, which the record does not register. Three of six
    registered instruments are absent, so the source's "every market" claim
    cannot be evaluated and the universe must not be shrunk to the local list;
  * the aggressor-side fields the record requires (millisecond trade
    timestamp, trade price, quantity, `isBuyerMaker` equivalent) are absent
    from every stored row shape - the store's own SCHEMA.md documents that
    the import kept OHLCV only, so `quote_volume`, trade count and taker-buy
    splits are deliberately not present;
  * the appendix mid-price anchor and the cross-exchange robustness leg have
    no substrate either: no bid/ask or book dataset exists anywhere and every
    local market is one venue (BINANCE). Bybit exists locally only as two
    cached web pages.

Running the local four-contract bar panel instead would change the record's
decision variable, its measurement window, its market sample and its
instruments. The card forbids that ("不得以近似資料、替代市場或改寫 hypothesis
硬跑" / "不得縮減 universe 以硬造可執行性"), so nothing was launched.

This script exists so an independent reader can re-derive that determination
from the live filesystem instead of trusting prose:

  * C1-C6 re-measure the canonical raw: the market/venue surface, the local
    instrument surface, the decisive absence of any trade-level dataset
    (entry-name probe over the whole tree at full depth + the stored row
    shapes + the store's own documented dataset families + its own documented
    coverage limit + its inventory), the resolution gate (10 s window vs the
    finest stored 300 s/900 s bars, with boundary alignment present), the
    universe gate (3 of 6 registered contracts absent), and the quote /
    second-venue surface (absent) beside the funding-settlement control
    (present);
  * C7-C12 re-read the round's immutable artifacts and assert they state
    exactly the contract-mandated terminal values (verdict, layer, class,
    yield decision, zero attempts, null run_id), that the registered
    requirement was NOT shrunk to the locally available instruments, that the
    house-wide scan is internally consistent against a live re-run (including
    the disclosed non-canonical artifacts and the excluded-token pass), that
    nothing was ever submitted (no attempt directory, no terminal sentinel),
    and that the DCA registration still carries the contract 7.2 v1.3.1
    provenance classes plus the complete 48-cell product;
  * C13 resolves every `*_verbatim` leaf of the persisted round-spec against
    its declared source (card / record / contract / footer) and re-checks the
    excerpt digest map - independent of the authoring run.

Read-only: it never writes inside the results tree or the raw tree. `--self-test`
builds tampered copies in fresh temp directories and asserts the checker refuses
them (non-vacuousness control). `--raw-fixture-control` builds a temp raw tree
carrying what this record would need (aggTrade/trade datasets with an
`isBuyerMaker` flag for all six registered contracts, 1m klines for the missing
instruments, a spot market and a second venue) and asserts the raw-side checks
flip to FAIL. `--host-scan` re-runs the house-wide search for a trade-level
dataset. Every temp tree is removed afterwards.

Usage:
    python3 runtime/crypto_quarter_hour_order_imbalance_prerequisite_check.py [--json]
    python3 runtime/crypto_quarter_hour_order_imbalance_prerequisite_check.py --measure-only
    python3 runtime/crypto_quarter_hour_order_imbalance_prerequisite_check.py --verify-verbatim
    python3 runtime/crypto_quarter_hour_order_imbalance_prerequisite_check.py --self-test
    python3 runtime/crypto_quarter_hour_order_imbalance_prerequisite_check.py --raw-fixture-control
    python3 runtime/crypto_quarter_hour_order_imbalance_prerequisite_check.py --host-scan

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
import xml.etree.ElementTree as ET
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

FAMILY = "crypto-quarter-hour-opening-order-imbalance-medium-horizon-2026-08-31"
ROUND = FAMILY + "-r1"
TASK = "t_b3ac2c5d"
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

# ---------------------------------------------------------------- registered requirement
# The record's own required-data list (## Required data, 9 items), registered by index so
# the card excerpt can be compared item by item.
RECORD_REQUIRED_ITEMS = 9
CARD_REQUIRED_EXCERPT_ITEMS = 7
# The record's falsification battery (## Falsification plan) and the card's transcription.
RECORD_FALSIFICATION_ITEMS = 8
CARD_FALSIFICATION_EXCERPT_ITEMS = 7
# The record's limitations list (## Limitations) and the card's transcription.
RECORD_LIMITATIONS_ITEMS = 7
CARD_LIMITATIONS_EXCERPT_ITEMS = 6
# The record's reported source sample ("from 2021-01-01 through 2024-10-31").
RECORD_SOURCE_SAMPLE = ("2021-01-01", "2024-10-31")
REGISTERED_INSTRUMENTS = ["ADA", "BTC", "DOGE", "ETH", "SOL", "XRP"]
REGISTERED_INSTRUMENTS_PRESENT_LOCAL = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
REGISTERED_INSTRUMENTS_ABSENT_LOCAL = ["ADAUSDT", "DOGEUSDT", "XRPUSDT"]
LOCAL_CONTRACTS_NOT_REGISTERED = ["BNBUSDT"]
# Source-supported signal parameters (record ## Signal).
MEASUREMENT_WINDOW_SECONDS = 10
QUARTER_HOUR_SECONDS = 900
REGISTERED_FORWARD_HORIZONS_H = (4, 8, 12)
# The store's documented interval ladder (SCHEMA.md: `1m 5m 15m 30m 1h 4h 1d 1w`).
DOCUMENTED_INTERVALS = ["1d", "1h", "1m", "1w", "30m", "4h", "5m", "15m"]
# Name tokens that would have to exist for this record's required dataset (individual /
# aggregate trades carrying an aggressor side, or quote/book data). One unified list is
# used by the raw entry-name probe, the house-wide scan and the checker's own
# re-measurement; the two documents can therefore never disagree about what the probe
# returns. Tokens that are substrings of ordinary words the host already uses are NOT
# probed and are listed - with a measurement - in PROBE_EXCLUSIONS below.
TRADE_TOKENS = ("aggtrade", "agg_trade", "aggtrades", "trades", "trade", "trade_ticks",
                "tick_data", "tickdata", "raw_trades", "trade_data", "isbuyermaker",
                "buyermaker", "buyer_maker", "aggressor", "trade_side", "signed_flow",
                "orderflow", "order_flow", "quotes", "bidask", "bid_ask", "bookticker",
                "book_ticker", "orderbook", "order_book", "level2", "l2book",
                "microsecond", "millisecond", "first_10_seconds", "quarter_hour",
                "quarterhour", "imbalance", "trade_tape", "xrpusdt", "dogeusdt",
                "adausdt", "bybit", "okx")
# Tokens deliberately NOT probed, with the measured reason. A token that is a substring
# of a word the host already uses can only ever return guaranteed false positives;
# excluding it is a probe-design decision, and every exclusion is demonstrated by a
# measurement (see `probe_exclusion_demonstration` / `excluded_token_scan`) - a generic
# token is not silently dropped.
PROBE_EXCLUSIONS = {
    "tick": "substring of the ordinary word 'sticker' (Telegram .tgs sticker exports "
            "under dr-profit-distillation/**/stickers/) and of the Hermes gateway socket "
            "names (gateway.loop-tick.<pid>.sock)",
    "ticks": "a strict substring of this pipeline's own registered stress-grid name "
             "`slippage_2ticks` (grid_slippage_2ticks.csv under "
             "/Volumes/ExpansionDrive/qlib-results/**)",
    "ticker": "substring of 'sticker' (.tgs exports) and of the Hermes cron heartbeat "
              "files ~/.hermes/cron/ticker_*",
    "quote": "substring of ordinary source identifiers and prose: Cargo build artefacts "
             "(`libquote-*.rlib`), test-module names (`test_..._quote.py`), the wiki "
             "record slug `...-finite-quotes-...`, and a daily quote-VOLUME csv",
    "bid": "substring of the ordinary words 'forbid'/'forbidden' and of bidirectional "
           "identifiers (`bidirectional`, `bidi.ts`, `libident`/`libidna` build artefacts)",
    "ask": "substring of 'task' (kanban task ids), 'masked', 'metamask' and of ordinary "
           "contributor e-mail addresses",
    "match": "substring of 'mismatch'/'unmatched'/'matcher' in the Hermes test suite "
             "(test_*_mismatch*.py) and of Cargo `libmatch*` build artefacts",
    "depth": "substring of `markdown-html-depth.ts` (an unrelated desktop-module name) "
             "and of a one-off xianxia image in ~/.hermes/tmp",
    "boundary": "substring of `error-boundary.tsx`, `prompt_cache_boundary.py` and this "
                "pipeline's own `grid_boundary_alt_*.csv` artifacts",
    "microstructure": "names US-equities research projects and a local copy of the `ml4t` "
                      "book source (case_studies/nasdaq100_microstructure), not a crypto "
                      "trade-data surface; the two data-bearing hit directories are "
                      "measured and disclosed as out-of-scope",
}
# Fields that would carry an aggressor side / taker split in a stored ROW.
TRADE_SHAPE_TOKENS = ("taker", "buyer", "maker", "aggressor", "side", "count", "trades",
                      "trade", "quote_volume")
# Narrow list for instrument definitions: `maker_fee`/`taker_fee` legitimately exist there,
# so only an actual trade-side field counts.
INSTRUMENT_TRADE_FIELDS = ("aggressor", "isbuyer", "buyermaker", "buyer_maker",
                           "trade_side", "taker_volume", "maker_volume", "signed",
                           "quote_volume")
DOCUMENTED_DATASET_FAMILIES = ["funding", "instruments",
                               "klines (Binance USD-M perpetual futures, UTC)"]
# the decisive registered data items and the exact status each must carry
DECISIVE_REQUIRED_STATUS = {
    "universe_six_registered_perpetual_contracts": "PARTIAL_3_OF_6",
    "instrument_xrp_perpetual": "ABSENT",
    "instrument_doge_perpetual": "ABSENT",
    "instrument_ada_perpetual": "ABSENT",
    "trade_level_data_with_aggressor_side": "ABSENT",
    "ten_second_quarter_hour_measurement_window": "NOT_CONSTRUCTIBLE",
    "required_fields_ms_timestamp_price_quantity_isbuyermaker": "ABSENT",
    "transaction_price_anchor": "NOT_CONSTRUCTIBLE",
    "bid_ask_mid_price_anchor_appendix": "ABSENT",
    "venue_bybit_cross_exchange_robustness": "ABSENT",
    "shifted_placebo_phase_grids_01_16_31_46": "NOT_CONSTRUCTIBLE",
}
MISSING_DATA_MATRIX_ITEMS = (
    "universe_six_registered_perpetual_contracts",
    "instrument_btc_perpetual", "instrument_eth_perpetual", "instrument_sol_perpetual",
    "instrument_xrp_perpetual", "instrument_doge_perpetual", "instrument_ada_perpetual",
    "venue_binance_usdm_perpetual", "venue_bybit_cross_exchange_robustness",
    "trade_level_data_with_aggressor_side",
    "ten_second_quarter_hour_measurement_window",
    "required_fields_ms_timestamp_price_quantity_isbuyermaker",
    "signed_order_flow_and_total_volume_derivation",
    "transaction_price_anchor", "bid_ask_mid_price_anchor_appendix",
    "binance_exchange_timestamp_convention_preserved",
    "point_in_time_signal_window_only",
    "missing_boundary_windows_excluded_not_imputed",
    "forward_return_horizons_4h_8h_12h", "funding_settlement_exclusion_control",
    "shifted_placebo_phase_grids_01_16_31_46",
    "contemporaneous_controls_oi_returns_vol_volume_bid_ask_bounce",
    "falsification_battery_8_items", "record_reported_source_sample_2021_2022",
    "strategy_rule_threshold_horizon_overlap_sizing", "transaction_cost_convention",
    "instrument_metadata_maker_taker_fees", "per_fill_cost_accounting_rail")
DECISIVE_MATRIX_ITEMS = tuple(DECISIVE_REQUIRED_STATUS)
# The raw's own window as registered on the card.
CARD_REGISTERED_RAW_WINDOW_START = "2022-01-01"
CARD_REGISTERED_RAW_WINDOW = "klines 2022-01-01\u21922026-09-11"
CARD_REGISTERED_FUNDING_WINDOW = "funding 2022-01-01T00:00Z\u21922026-09-12T08:00Z"
# The record's own words for the dataset it needs (used by both the registration and the
# checker so the two documents cannot drift).
TRADE_DATASET_REQUIRED_TEXT = ("Individual / aggregate trades carrying an aggressor side "
                               "(`isBuyerMaker`)")
# Non-canonical, non-raw data artefacts elsewhere on the host that the house-wide scan
# finds and discloses as measured-but-unused. Each marker is part of the measurement.
NON_CANONICAL_MARKERS = (
    "a1-1-phase9-pit-membership", "vision_um_daily_klines",
    "phase7-alpha-research/data/derived",
)
OUT_OF_SCOPE_MARKERS = ("/phase4-market-microstructure/", "phase4-market-microstructure",
                        "/ml4t-p1/", "nasdaq100_microstructure")
BUILD_ARTIFACT_MARKERS = ("/target/debug/deps/", "/target/release/deps/",
                          "/target/debug/build/", "/target/release/build/",
                          "/build-cache/", ".rlib", ".rmeta", ".rcgu.o")
STICKER_MARKERS = ("/stickers/", ".tgs", "sticker")
SOCKET_MARKERS = ("loop-tick", ".sock")
OWN_GRID_MARKERS = ("slippage_2ticks", "grid_boundary_alt")
HERMES_TMP_MARKERS = ("/.hermes/tmp/",)
FIXTURE_MARKERS = ("/fixtures/",)
# The retired engine stacks the store's own inventory lists. Their absence is measured
# (a migration result, not an assumption).
RETIRED_STORE_PATHS = (os.path.join(EXPANSION, "nautilus-system"),
                       os.path.join(EXPANSION, "lean-system"),
                       os.path.join(EXPANSION, "microsoft-quant-stack"))
INVARIANT_TOKENS = ("30,000", "numeraire", "leverage 10x", "12 tranches", "tranche #12",
                    "reduce-only", "flat/kill")
TMP_ROOTS = [os.path.join(HOME, "workspace"), EXPANSION, "/Volumes/ResearchData",
             os.path.join(HOME, ".hermes"),
             os.path.join(HOME, "workspace", "qlib-apple-container")]
SCAN_SKIP_DIRS = ("node_modules", "__pycache__", ".git", "venvs", "site-packages", ".venv",
                  "Photos Library.photoslibrary")
BACKUP_LIKE_DIRS = ("_archived", "archive", "backup", "backups", "old")
# Additional measured backup markers: an off-profile copy of a Hermes profile tree lives on
# the external drive under a `profile_backup_<date>` directory and carries cron heartbeat
# files whose names collide with the excluded `tick`/`ticker` tokens.
BACKUP_MARKERS = ("profile_backup", "_backup_", "-backup-")
# Classification of every house-wide hit. A hit is acceptable only if it is a document,
# code, session/log/cache artifact, our own provenance artifact, a build artifact, an
# out-of-scope research project, a non-canonical staging copy already disclosed below, or
# a directory with no data file beneath it - never a data store that the raw tree lacks.
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
                     "build_artifact_false_positive",
                     "telegram_sticker_export_false_positive",
                     "gateway_socket_false_positive",
                     "own_pipeline_grid_artifact_false_positive",
                     "hermes_tmp_artifact_false_positive",
                     "repo_fixture_false_positive",
                     "out_of_scope_market_research_false_positive",
                     "tool_output_log_false_positive",
                     "unclassified_data_candidate")
UNCLASSIFIED_CLASS = "unclassified_data_candidate"
INTEGRITY_MANIFEST_MARKERS = ("hash-manifest", "hashes", "rehash", "hash_compare",
                              "pre_hashes", "post_hash", "hash_three_states", "results_hash")
# this card's own evidence snapshot carries the family name in its filename
OWN_ARTIFACT_BASENAMES = (FAMILY + "-prerequisite-gate-20260917.json",)
WIKI_DIR_MARKER = os.path.join(".hermes", "wiki", "quant")
ROUND_DIR_PARTS = (FAMILY, "rounds", ROUND)
# memoised house scan (see host_scan); keyed nowhere because the scan takes no arguments
# that change its result within a process
_HOST_SCAN_CACHE = {}


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
         "index", "trades", "aggtrades", "aggTrades".lower(), "trade", "ticks", "book",
         "bookticker", "depth", "quotes")]
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
    m["klines_interval_set_by_symbol"] = {
        s: _dirs(os.path.join(kl, s)) for s in (m["klines_dataset_dirs"] or [])}
    secs = sorted(_interval_seconds(i) for i in (m["klines_interval_set"] or []))
    m["klines_interval_seconds"] = secs
    m["finest_stored_interval_seconds"] = secs[0] if secs else None
    m["finest_stored_interval_to_window_ratio"] = (
        round(secs[0] / MEASUREMENT_WINDOW_SECONDS, 1) if secs else None)
    m["quarter_hour_bar_seconds"] = QUARTER_HOUR_SECONDS
    m["quarter_hour_bar_to_window_ratio"] = QUARTER_HOUR_SECONDS / MEASUREMENT_WINDOW_SECONDS
    m["usdm_dataset_dirs"] = sorted(d for d in (_dirs(ud) or []))
    m["trades_dataset_dirs"] = [d for d in m["usdm_dataset_dirs"]
                                if any(t in d.lower() for t in
                                       ("trade", "tick", "agg", "book", "quote", "depth"))]

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
    # the boundary-alignment control: quarter-hour-aligned bars DO open exactly on the
    # boundary, so alignment is present while the trade substrate is not
    for iv, grid in (("15m", 900), ("5m", 300)):
        d = os.path.join(kl, "BTCUSDT", iv)
        fs = sorted(f for f in os.listdir(d) if f.endswith(".jsonl.gz")) \
            if os.path.isdir(d) else []
        if fs:
            rr = _rows(os.path.join(d, fs[-1]))
            m["klines_%s_grid_seconds" % iv] = grid
            m["klines_%s_first_open_utc" % iv] = _iso(rr[0]["open_time_ms"])
            m["klines_%s_last_open_utc" % iv] = _iso(rr[-1]["open_time_ms"])
            m["klines_%s_last3_open_mod_grid_seconds" % iv] = [
                r["open_time_ms"] % (grid * 1000) // 1000 for r in rr[-3:]]
    first, last = {}, {}
    for sym in (m["klines_dataset_dirs"] or []):
        d = os.path.join(kl, sym, "1h")
        if not os.path.isdir(d):
            continue
        fs = sorted(f for f in os.listdir(d) if f.endswith(".jsonl.gz"))
        if not fs:
            continue
        first[sym] = _rows(os.path.join(d, fs[0]))[0]["open_time_ms"]
        last[sym] = _rows(os.path.join(d, fs[-1]))[-1]["open_time_ms"]
    m["klines_1h_first_by_symbol"] = {k: _iso(v) for k, v in first.items()}
    m["klines_1h_last_by_symbol"] = {k: _iso(v) for k, v in last.items()}
    window = [_iso(min(first.values())), _iso(max(last.values()))] if first else None
    m["raw_window_utc"] = window
    m["card_registered_raw_window"] = CARD_REGISTERED_RAW_WINDOW
    m["card_registered_funding_window"] = CARD_REGISTERED_FUNDING_WINDOW
    m["raw_window_starts_at_or_after_card_registration"] = bool(
        window and window[0][:10] >= CARD_REGISTERED_RAW_WINDOW_START)
    if window:
        m["raw_history_days"] = _day_span_days(window[0], window[1])
    m["raw_window_covers_record_source_sample"] = bool(
        window and window[0][:10] <= RECORD_SOURCE_SAMPLE[0]
        and window[1][:10] >= RECORD_SOURCE_SAMPLE[1])
    m["record_source_sample"] = list(RECORD_SOURCE_SAMPLE)
    m["record_source_sample_missing_locally"] = (
        [RECORD_SOURCE_SAMPLE[0], CARD_REGISTERED_RAW_WINDOW_START]
        if window and window[0][:10] > RECORD_SOURCE_SAMPLE[0] else [])
    m["registered_instruments"] = list(REGISTERED_INSTRUMENTS)
    m["registered_instruments_present_local"] = list(REGISTERED_INSTRUMENTS_PRESENT_LOCAL)
    m["registered_instruments_absent_local"] = list(REGISTERED_INSTRUMENTS_ABSENT_LOCAL)
    m["local_contracts_not_in_record_universe"] = list(LOCAL_CONTRACTS_NOT_REGISTERED)
    m["registered_instrument_coverage"] = "%d/%d" % (
        len(REGISTERED_INSTRUMENTS_PRESENT_LOCAL), len(REGISTERED_INSTRUMENTS))
    m["forward_horizons_required_hours"] = list(REGISTERED_FORWARD_HORIZONS_H)

    fu = os.path.join(ud, "funding")
    m["funding_symbol_dirs"] = _dirs(fu)
    p = os.path.join(fu, "BTCUSDT", "BTCUSDT-funding.jsonl.gz")
    m["funding_has_official_truth_rows"] = (
        "official" in (m.get("funding_truth_status_values") or []))
    if os.path.exists(p):
        rs = _rows(p)
        m["funding_row_keys"] = sorted(rs[-1].keys())
        m["funding_truth_status_values"] = sorted({str(r.get("truth_status")) for r in rs})
        m["funding_has_official_truth_rows"] = (
            "official" in m["funding_truth_status_values"])
        m["funding_venues"] = sorted({str(r.get("venue")) for r in rs})
        m["funding_first_ms"] = _iso(rs[0]["funding_time_ms"])
        m["funding_last_ms"] = _iso(rs[-1]["funding_time_ms"])
    m["funding_row_has_trade_field"] = any(
        t in k.lower() for k in (m.get("funding_row_keys") or []) for t in TRADE_SHAPE_TOKENS)

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
    m["instrument_trade_field_hits"] = sorted(
        f for f in m["instrument_field_names"]
        if any(t in f.lower() for t in INSTRUMENT_TRADE_FIELDS))
    m["instrument_fee_fields_only"] = sorted(
        f for f in m["instrument_field_names"] if "fee" in f.lower())
    m["kline_row_has_trade_field"] = any(
        t in f.lower() for f in (m.get("klines_row_field_set") or []) for t in
        TRADE_SHAPE_TOKENS)

    schema_path = os.path.join(raw, "_meta", "SCHEMA.md")
    schema = _text(schema_path) if os.path.exists(schema_path) else ""
    flat = schema.replace("*", "")
    m["schema_sha256"] = _sha256_text(schema) if schema else None
    m["schema_dataset_sections"] = sorted(ln.split("## Dataset:", 1)[1].strip()
                                          for ln in schema.splitlines()
                                          if ln.startswith("## Dataset:"))
    m["schema_documents_trade_dataset"] = any(
        t in flat.lower() for t in ("aggtrade", "agg trade", "isbuyermaker", "bookticker",
                                    "book ticker", "order book", "level 2", "l2 book",
                                    "tick data", "trade data", "trade-level"))
    m["schema_documents_missing_splits"] = (
        "quote_volume" in flat and "trade count and taker-buy splits are" in flat)
    m["schema_documented_intervals"] = DOCUMENTED_INTERVALS
    m["schema_documented_finest_interval_seconds"] = min(
        _interval_seconds(i) for i in DOCUMENTED_INTERVALS)
    m["schema_documented_bar_store_only"] = (
        "the initial\nimport came from a bar store that kept only OHLCV" in schema
        or "bar store that kept only OHLCV" in flat)
    m["schema_single_venue"] = "Binance USD-M perpetual futures, UTC" in schema
    m["schema_no_silent_gapfill"] = "No gaps are silently filled" in schema
    m["schema_trade_word_lines"] = [ln.strip()[:90] for ln in schema.splitlines()
                                    if any(t in ln.lower()
                                           for t in ("trade", "taker", "aggressor",
                                                     "isbuyer", "tick", "quote"))]
    inv_path = os.path.join(raw, "_meta", "INVENTORY.md")
    inv = _text(inv_path) if os.path.exists(inv_path) else ""
    m["inventory_sha256"] = _sha256_text(inv) if inv else None
    m["inventory_declares_no_other_store_found"] = (
        "No other Binance/klines/tardis/aggTrade store was found on" in inv)
    m["inventory_declares_bars_only_migration"] = (
        "parquet **bars** (engine store)" in inv)
    m["inventory_lean_minute_note"] = next(
        (ln.strip()[:160] for ln in inv.splitlines()
         if "minute/" in ln and "30-minute trade bars" in ln), None)
    m["inventory_launchd_updater_line"] = next(
        (ln.strip()[:120] for ln in inv.splitlines()
         if "market_data_sync.py" in ln and "launchd" in ln), None)
    m["retired_store_paths_absent"] = {p: (not os.path.exists(p))
                                       for p in RETIRED_STORE_PATHS}
    m["all_retired_stores_absent"] = all(m["retired_store_paths_absent"].values())

    names = _all_entries(raw, PROBE_MAX_DEPTH)
    joined = "\n".join(names)
    m["raw_entry_name_count"] = len(names)
    m["probe_tokens_tested"] = list(TRADE_TOKENS)
    m["trade_token_hits_in_entry_names"] = sorted({t for t in TRADE_TOKENS if t in joined})
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
    m["note"] = ("single venue (BINANCE USD-M perpetual), four fixed contracts, three "
                 "dataset families: klines whose rows carry the seven-field OHLCV shape "
                 "only, that venue's funding, and its instrument definitions. No trade, "
                 "aggTrade, tick, quote or book dataset of any kind; the finest stored "
                 "interval is %s (%ss) and the only quarter-hour-aligned bars are 15m "
                 "bars covering %ss, against a registered measurement window of %ss. The "
                 "record's six-contract universe is present at 3/6 (BTCUSDT, ETHUSDT, "
                 "SOLUSDT); XRPUSDT, DOGEUSDT and ADAUSDT do not exist locally and "
                 "BNBUSDT is not in the record's universe."
                 % (",".join(str(s) for s in (m.get("klines_interval_seconds") or [])[:1]),
                    m.get("finest_stored_interval_seconds") or "?",
                    QUARTER_HOUR_SECONDS, MEASUREMENT_WINDOW_SECONDS))
    return m


def measure_non_canonical_artifacts():
    """Measure the non-canonical, non-raw data artefacts the house-wide scan discloses.

    These are NOT alternative data sources: each is measured here so the round-spec's
    disclosure is a measurement rather than prose, and so a later drift (a real
    trade-level dataset landing in one of them) cannot pass unnoticed.
    """
    out = {}
    a1 = os.path.join(HOME, "workspace", "a1-1-phase9-pit-membership-20260830", "raw")
    per_file, prefixes, symbols, agg_hits = {}, set(), set(), 0
    files = sorted(f for f in os.listdir(a1)
                   if f.startswith("vision_um_daily_klines")) if os.path.isdir(a1) else []
    ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
    for f in files:
        p = os.path.join(a1, f)
        try:
            root = ET.fromstring(_text(p))
        except ET.ParseError:
            per_file[f] = {"parse": "error"}
            continue
        keys = [e.text or "" for e in root.findall(".//s3:Contents/s3:Key", ns)]
        pfx = [e.text or "" for e in root.findall(".//s3:Prefix", ns)]
        text = _text(p)
        agg_hits += text.count("aggTrade")
        prefixes.update(pfx)
        symbols.add(f.replace("vision_um_daily_klines_", "").replace("_1d_index.raw", ""))
        per_file[f] = {"listing_keys": len(keys), "prefix": pfx[:1],
                       "first_key": keys[0] if keys else None,
                       "aggTrade_occurrences": text.count("aggTrade")}
    out["a1_1_binance_vision_s3_listings"] = {
        "dir": a1, "file_count": len(files), "symbols": sorted(symbols),
        "prefixes": sorted(prefixes), "aggTrade_occurrences_total": agg_hits,
        "daily_kline_prefix_only": all(p.endswith("/1d/") for p in prefixes) if prefixes else None,
        "per_file": per_file,
        "what_it_is": "data.binance.vision S3 ListBucketResult XML listings (1000 keys "
                      "each) for the `data/futures/um/daily/klines/<SYMBOL>/1d/` prefixes "
                      "- key listings, not market data rows, and daily klines only: no "
                      "aggTrade prefix and no 1m/10s surface appears in any listing.",
        "used": False,
    }
    p7 = os.path.join(HOME, "workspace", "phase7-alpha-research", "data", "derived",
                      "quote_volume_wide.csv")
    if os.path.exists(p7):
        with open(p7, encoding="utf-8", errors="replace") as f:
            head = [next(f).rstrip("\n") for _ in range(2)]
        out["phase7_daily_quote_volume_wide"] = {
            "path": p7, "bytes": os.path.getsize(p7), "header": head[0],
            "first_data_row": head[1][:120],
            "what_it_is": "a DAILY quote-volume matrix (one column per contract, one row "
                          "per UTC day) - daily aggregates, not quote, book or trade "
                          "data, and not the canonical raw.",
            "used": False,
        }
    p4 = os.path.join(HOME, "workspace", "phase4-market-microstructure", "data",
                      "merged_daily_ohlcv.csv")
    if os.path.exists(p4):
        with open(p4, encoding="utf-8", errors="replace") as f:
            head = [next(f).rstrip("\n") for _ in range(2)]
        out["phase4_merged_daily_ohlcv"] = {
            "path": p4, "bytes": os.path.getsize(p4), "header": head[0],
            "first_data_row": head[1][:120],
            "what_it_is": "a DAILY OHLCV matrix for BTC/ETH from a non-canonical "
                          "equities/microstructure research project (Nasdaq study); daily "
                          "bars only, unused.",
            "used": False,
        }
    ml4t = os.path.join(HOME, "workspace", "tmp", "ml4t-p1", "ml4t-src", "case_studies",
                        "nasdaq100_microstructure")
    if os.path.isdir(ml4t):
        bench = os.path.join(ml4t, "benchmark")
        out["ml4t_nasdaq100_case_study"] = {
            "dir": ml4t,
            "benchmark_files": sorted(os.listdir(bench)) if os.path.isdir(bench) else [],
            "what_it_is": "a local copy of the `ml4t` book source tree's NASDAQ-100 "
                          "case study (US equities, AlgoSeek archive): a different "
                          "market from the record's crypto perpetuals, non-canonical, "
                          "unused.",
            "used": False,
        }
    out["none_is_a_trade_level_source"] = (
        out["a1_1_binance_vision_s3_listings"]["aggTrade_occurrences_total"] == 0
        and out["a1_1_binance_vision_s3_listings"]["daily_kline_prefix_only"] is True)
    return out


def run_checks(results_root, raw_root, repo_root=None, card_body_path=None,
               record_path=None, board_db=None):
    checks = []
    add = lambda cid, ok, detail: checks.append({
        "id": cid, "status": "PASS" if ok else "FAIL", "detail": detail})
    raw = measure_raw(raw_root)
    noncanon = measure_non_canonical_artifacts()
    round_dir = os.path.join(results_root, FAMILY, "rounds", ROUND)

    # C1 - one market type for one venue, and no other market directory at all (no trade /
    #      aggTrade / tick / quote / book dataset directory, no spot market, no dated
    #      futures, no second venue).
    md = raw["binance_market_dirs"] or []
    add("C1", md == ["usdm"] and raw["venue"] == "BINANCE" and raw["market_type"] == "usdm_perp"
        and raw["paths_named_other_market"] == [] and raw["non_binance_paths"] == []
        and raw["usdm_dataset_dirs"] == ["funding", "instruments", "klines"]
        and raw["trades_dataset_dirs"] == [],
        "binance market dirs=%s venue=%s market_type=%s other_market_paths=%s "
        "non_binance_paths=%s usdm_dataset_dirs=%s trade_like_dirs=%s"
        % (md, raw["venue"], raw["market_type"], raw["paths_named_other_market"],
           raw["non_binance_paths"], raw["usdm_dataset_dirs"], raw["trades_dataset_dirs"]))

    # C2 - the four USD-M perpetual contracts are the only local instruments, and every one
    #      of them is quoted and settled in USDT.
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

    # C3 - THE DECISIVE ONE: the record's decision variable (signed order flow in the first
    #      10 seconds of a quarter-hour opening, aggressor side from `isBuyerMaker`) has no
    #      data substrate. No trade/aggTrade/tick/quote token appears in any entry name
    #      under the raw tree at full depth, no stored row shape carries an aggressor side
    #      or taker split (the store's own SCHEMA.md documents that the import kept OHLCV
    #      only, so quote_volume, trade count and taker-buy splits are not present), the
    #      store's documented dataset families are exactly three, and its own inventory
    #      records that no other Binance/klines/tardis/aggTrade store was found on the host.
    add("C3", raw["trade_token_hits_in_entry_names"] == []
        and raw["kline_row_has_trade_field"] is False
        and raw["funding_row_has_trade_field"] is False
        and raw["instrument_trade_field_hits"] == []
        and raw["schema_documents_trade_dataset"] is False
        and raw["schema_documents_missing_splits"] is True
        and raw["schema_dataset_sections"] == DOCUMENTED_DATASET_FAMILIES
        and raw["inventory_declares_no_other_store_found"] is True
        and raw["inventory_declares_bars_only_migration"] is True,
        "trade_token_hits=%s kline_row_has_trade_field=%s funding_row_has_trade_field=%s "
        "instrument_trade_fields=%s schema_documents_trade_dataset=%s "
        "schema_documents_missing_splits=%s documented_dataset_families=%s "
        "inventory_no_other_store=%s inventory_bars_only=%s (%d probe tokens over %d entry "
        "names, depth<=%d)"
        % (raw["trade_token_hits_in_entry_names"], raw["kline_row_has_trade_field"],
           raw["funding_row_has_trade_field"], raw["instrument_trade_field_hits"],
           raw["schema_documents_trade_dataset"], raw["schema_documents_missing_splits"],
           raw["schema_dataset_sections"], raw["inventory_declares_no_other_store_found"],
           raw["inventory_declares_bars_only_migration"], len(TRADE_TOKENS),
           raw["raw_entry_name_count"], PROBE_MAX_DEPTH))

    # C4 - the RESOLUTION gate: the trade-data absence is a property of the store's
    #      granularity, not of a field name. The finest stored interval is 5m (300 s) and
    #      the documented ladder bottoms out at 1m (60 s), both far coarser than the
    #      registered 10-second window; the only bars that open on a quarter-hour boundary
    #      are 15m bars covering the whole 900 s; kline rows are bar aggregates, so
    #      millisecond trade timestamps, trade prices, quantities and an aggressor side
    #      are not reconstructible at any interval. Boundary ALIGNMENT itself is present
    #      (aligned bars open exactly on :00/:15/:30/:45), so alignment is not the missing
    #      part - the trade substrate is.
    aligned_ok = (raw.get("klines_15m_last3_open_mod_grid_seconds") == [0, 0, 0]
                  and raw.get("klines_5m_last3_open_mod_grid_seconds") == [0, 0, 0])
    add("C4", raw["finest_stored_interval_seconds"] == 300
        and raw["finest_stored_interval_to_window_ratio"] >= 30
        and raw["quarter_hour_bar_to_window_ratio"] == 90
        and raw["schema_documented_finest_interval_seconds"] == 60
        and raw["schema_documented_bar_store_only"] is True
        and raw["trade_token_hits_in_entry_names"] == []
        and aligned_ok,
        "interval_seconds=%s finest=%ss window=%ss ratio=%sx quarter_hour_bar=%ss "
        "documented_finest=%ss bar_store_only=%s aligned_bars_open_mod_grid=%s "
        "trade_token_hits=%s"
        % (raw["klines_interval_seconds"], raw["finest_stored_interval_seconds"],
           MEASUREMENT_WINDOW_SECONDS, raw["finest_stored_interval_to_window_ratio"],
           QUARTER_HOUR_SECONDS, raw["schema_documented_finest_interval_seconds"],
           raw["schema_documented_bar_store_only"],
           {iv: raw.get("klines_%s_last3_open_mod_grid_seconds" % iv)
            for iv in ("15m", "5m")}, raw["trade_token_hits_in_entry_names"]))

    # C5 - the UNIVERSE gate: only three of the record's six registered perpetual contracts
    #      exist locally; XRPUSDT, DOGEUSDT and ADAUSDT are absent, and BNBUSDT is present
    #      although the record does not register it. The record's own universe is therefore
    #      not expressible without shrinking it, which the card forbids.
    add("C5", raw["klines_dataset_dirs"] == EXPECTED_SYMBOLS
        and set(REGISTERED_INSTRUMENTS_PRESENT_LOCAL) <= set(raw["klines_dataset_dirs"])
        and not any(s in (raw["klines_dataset_dirs"] or [])
                    for s in REGISTERED_INSTRUMENTS_ABSENT_LOCAL)
        and raw["registered_instrument_coverage"] == "3/6"
        and raw["local_contracts_not_in_record_universe"] == LOCAL_CONTRACTS_NOT_REGISTERED,
        "registered=%s present_local=%s absent_local=%s coverage=%s local_not_registered=%s "
        "local_klines=%s"
        % (raw["registered_instruments"], raw["registered_instruments_present_local"],
           raw["registered_instruments_absent_local"], raw["registered_instrument_coverage"],
           raw["local_contracts_not_in_record_universe"], raw["klines_dataset_dirs"]))

    # C6 - the AUXILIARY surfaces: the appendix mid-price anchor (bid/ask) and the
    #      cross-exchange robustness leg have no substrate either - no quote/book dataset
    #      exists and every local market is the single venue BINANCE - while the funding
    #      data the record's funding-settlement control needs IS present with an official
    #      truth_status. The stored shapes match what the store documents, so the absences
    #      above are properties of the store, not of this reader's field names.
    add("C6", raw["paths_named_other_market"] == []
        and raw["non_binance_paths"] == []
        and raw["funding_venues"] == ["BINANCE"]
        and raw["funding_has_official_truth_rows"] is True
        and raw["all_retired_stores_absent"] is True
        and raw["kline_row_has_trade_field"] is False
        and raw["schema_single_venue"] is True
        and raw["schema_no_silent_gapfill"] is True
        and raw["instrument_fee_fields_only"] == ["maker_fee", "taker_fee"]
        and all(v == 0 for v in (raw.get("klines_1d_first_open_mod_86400_seconds") or [1])),
        "other_market_paths=%s non_binance_paths=%s funding_venues=%s funding_truth=%s "
        "retired_stores_absent=%s kline_row_has_trade_field=%s schema_single_venue=%s "
        "schema_no_silent_gapfill=%s instrument_fee_fields=%s open_mod_86400=%s"
        % (raw["paths_named_other_market"], raw["non_binance_paths"], raw["funding_venues"],
           raw["funding_truth_status_values"], raw["all_retired_stores_absent"],
           raw["kline_row_has_trade_field"], raw["schema_single_venue"],
           raw["schema_no_silent_gapfill"], raw["instrument_fee_fields_only"],
           raw.get("klines_1d_first_open_mod_86400_seconds")))

    # ---- artifact side
    spec_path = os.path.join(round_dir, "round-spec.json")
    verdict_path = os.path.join(round_dir, "verdict.json")
    spec = _load_json(spec_path) if os.path.exists(spec_path) else {}
    verdict = _load_json(verdict_path) if os.path.exists(verdict_path) else {}

    # C7 - the recorded house-wide scan is internally consistent, its point-in-time hit set
    #      is explained (every hit carries an acceptable non-data classification, none is
    #      unclassified, the excluded-token pass is disclosed and produced no unclassified
    #      data candidate), the registered probe agrees with the live re-measurement, the
    #      disclosed non-canonical artifacts match a live re-measurement, and any path that
    #      appeared since the snapshot was written is one of this card's own artifacts -
    #      never a newly-visible data store.
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
    noncanon_stored = gate.get("disclosed_non_canonical_artifacts") or {}
    add("C7", house.get("unclassified_hits") == []
        and not cls_counts.get(UNCLASSIFIED_CLASS)
        and sum(v for k, v in cls_counts.items() if k != UNCLASSIFIED_CLASS)
        == house.get("hit_count")
        and house.get("hit_count") == len(house.get("hits") or [])
        + (house.get("hits_capped_by") or 0)
        and bool(house.get("note"))
        and bool(house.get("probe_exclusions"))
        and len(house.get("excluded_token_scan") or {}) == len(PROBE_EXCLUSIONS)
        and all(v.get("hit_count") == sum((v.get("classification_counts") or {}).values())
                for v in (house.get("excluded_token_scan") or {}).values())
        and not any(any(rt in p.lower() for rt in TRADE_TOKENS)
                    for v in (house.get("excluded_token_scan") or {}).values()
                    for p in (v.get("unclassified_data_candidates") or []))
        and all((d.get("house_collision_example") or d.get("house_hit_count") == 0)
                for d in (house.get("probe_exclusion_demonstration") or {}).values())
        and meas.get("trade_token_hits_in_entry_names") == []
        and meas.get("excluded_token_raw_tree_collisions")
        == raw["excluded_token_raw_tree_collisions"]
        and set(raw["excluded_token_raw_tree_collisions"]) <= set(PROBE_EXCLUSIONS)
        and raw["trade_token_hits_in_entry_names"] == []
        and noncanon_stored.get("a1_1_binance_vision_s3_listings", {}).get(
            "aggTrade_occurrences_total") == 0
        and _noncanon_matches(noncanon_stored, noncanon)[0] is True
        and drift_ok,
        "house_hits_stored=%s classification_counts=%s unclassified=%s hits_listed=%s "
        "capped_by=%s raw_name_probe_hits(artifact)=%s (live)=%s "
        "excluded_token_unclassified=%s excluded_token_raw_collisions=%s | "
        "live_house_hits=%s live_only=%s stored_only=%s drift_all_own_artifacts=%s | "
        "noncanonical_measured_match=%s a1_1_aggTrade=%s"
        % (house.get("hit_count"), cls_counts, house.get("unclassified_hits"),
           len(house.get("hits") or []), house.get("hits_capped_by"),
           meas.get("trade_token_hits_in_entry_names"),
           raw["trade_token_hits_in_entry_names"],
           sum(len(v.get("unclassified_data_candidates") or [])
               for v in (house.get("excluded_token_scan") or {}).values()),
           raw["excluded_token_raw_tree_collisions"],
           live_scan.get("hit_count"), live_only, stored_only, drift_ok,
           _noncanon_matches(noncanon_stored, noncanon)[0],
           noncanon_stored.get("a1_1_binance_vision_s3_listings", {}).get(
               "aggTrade_occurrences_total")))

    # C8 - the registration still states the record's own requirement (it was NOT shrunk to
    #      the locally available instruments) and records the missing surfaces item by item.
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
        and matrix["instrument_btc_perpetual"].get("status") == "PRESENT"
        and matrix["venue_binance_usdm_perpetual"].get("status") == "PRESENT"
        and matrix["forward_return_horizons_4h_8h_12h"].get("status") == "PRESENT_AS_LOCAL_PROXY"
        and matrix["funding_settlement_exclusion_control"].get("status") == "PRESENT"
        and univ.get("universe_shrunk_to_local_list") is False
        and univ.get("instrument_required") == REGISTERED_INSTRUMENTS
        and univ.get("instrument_required_size") == len(REGISTERED_INSTRUMENTS)
        and univ.get("instrument_present_local") == REGISTERED_INSTRUMENTS_PRESENT_LOCAL
        and univ.get("instrument_absent_local") == REGISTERED_INSTRUMENTS_ABSENT_LOCAL
        and univ.get("local_contracts_not_in_record_universe")
        == LOCAL_CONTRACTS_NOT_REGISTERED
        and univ.get("trade_dataset_required") == TRADE_DATASET_REQUIRED_TEXT
        and univ.get("trade_dataset_available") is False
        and univ.get("trade_name_probe_hits") == []
        and univ.get("probe_tokens_tested") == list(TRADE_TOKENS)
        and univ.get("probe_exclusions") == PROBE_EXCLUSIONS
        and univ.get("probe_excluded_token_raw_tree_collisions")
        == raw["excluded_token_raw_tree_collisions"]
        and univ.get("measurement_window_seconds") == MEASUREMENT_WINDOW_SECONDS
        and univ.get("finest_stored_interval_seconds") == 300
        and univ.get("quote_dataset_available") is False
        and univ.get("second_venue_available") is False
        and univ.get("record_required_items_registered") == RECORD_REQUIRED_ITEMS
        and univ.get("card_excerpt_items") == CARD_REQUIRED_EXCERPT_ITEMS
        and univ.get("restored_required_items")
        == RECORD_REQUIRED_ITEMS - CARD_REQUIRED_EXCERPT_ITEMS
        and (spec.get("falsification") or {}).get("battery_items")
        == RECORD_FALSIFICATION_ITEMS
        and (spec.get("falsification") or {}).get("card_excerpt_items")
        == CARD_FALSIFICATION_EXCERPT_ITEMS
        and (spec.get("falsification") or {}).get("limitations_items", {}).get("record")
        == RECORD_LIMITATIONS_ITEMS
        and (spec.get("falsification") or {}).get("limitations_items", {}).get("card_excerpt")
        == CARD_LIMITATIONS_EXCERPT_ITEMS
        and (spec.get("signal_semantics") or {}).get("decision_variable_available") is False
        and (spec.get("expected") or {}).get("status") == "not_computable"
        and (spec.get("strategy_domain") or {}).get("status") == "not_registered"
        and (spec.get("launch") or {}).get("attempts_launched") == 0
        and bool(spec.get("excerpt_source_map"))
    )
    add("C8", bool(ok8),
        "round-spec ids/gate=%s required_available=%s decisive=%s shrunk=%s "
        "trade_available=%s quote_available=%s second_venue=%s matrix_items=%d "
        "probe_tokens_equal=%s"
        % (gate.get("outcome"), gate.get("required_data_available"),
           {k: matrix.get(k, {}).get("status") for k in DECISIVE_MATRIX_ITEMS},
           univ.get("universe_shrunk_to_local_list"), univ.get("trade_dataset_available"),
           univ.get("quote_dataset_available"), univ.get("second_venue_available"),
           len(matrix), univ.get("probe_tokens_tested") == list(TRADE_TOKENS)))

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
        and (verdict.get("prerequisite") or {}).get("trade_dataset_available") is False
        and (verdict.get("prerequisite") or {}).get("registered_instrument_coverage") == "3/6"
    )
    add("C9", ok9, "verdict=%s run_id=%s layer=%s class=%s yield=%s run_ids=%s "
                   "trade_dataset_available=%s coverage=%s"
        % (verdict.get("verdict"), verdict.get("run_id"), fail.get("layer"), fail.get("class"),
           yld.get("yield_decision"), verdict.get("evidence_run_ids"),
           (verdict.get("prerequisite") or {}).get("trade_dataset_available"),
           (verdict.get("prerequisite") or {}).get("registered_instrument_coverage")))

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

    A growing listing directory legitimately changes the per-file map, so the compared
    fields are the aggregate ones the gate depends on: file count, symbol set, prefix set,
    the total aggTrade occurrences, the daily-kline-prefix flag, the two CSV headers, and
    the ml4t benchmark file list.
    """
    if not stored or not live:
        return False, {"reason": "missing stored or live measurement"}
    diffs = {}
    a_s = stored.get("a1_1_binance_vision_s3_listings") or {}
    a_l = live.get("a1_1_binance_vision_s3_listings") or {}
    for k in ("file_count", "symbols", "prefixes", "aggTrade_occurrences_total",
              "daily_kline_prefix_only"):
        if a_s.get(k) != a_l.get(k):
            diffs["a1_1.%s" % k] = {"stored": a_s.get(k), "live": a_l.get(k)}
    for key in ("phase7_daily_quote_volume_wide", "phase4_merged_daily_ohlcv"):
        if (stored.get(key) or {}).get("header") != (live.get(key) or {}).get("header"):
            diffs["%s.header" % key] = {"stored": (stored.get(key) or {}).get("header"),
                                        "live": (live.get(key) or {}).get("header")}
    if (stored.get("ml4t_nasdaq100_case_study") or {}).get("benchmark_files") != \
            (live.get("ml4t_nasdaq100_case_study") or {}).get("benchmark_files"):
        diffs["ml4t.benchmark_files"] = {
            "stored": (stored.get("ml4t_nasdaq100_case_study") or {}).get("benchmark_files"),
            "live": (live.get("ml4t_nasdaq100_case_study") or {}).get("benchmark_files")}
    if stored.get("none_is_a_trade_level_source") is not True:
        diffs["none_is_a_trade_level_source"] = stored.get("none_is_a_trade_level_source")
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
        "trade_dataset_matrix_item_claimed_present": lambda d: _tamper_spec(
            d, lambda s: _set_matrix_status(s, "trade_level_data_with_aggressor_side",
                                            "PRESENT")),
        "ten_second_window_claimed_constructible": lambda d: _tamper_spec(
            d, lambda s: _set_matrix_status(s, "ten_second_quarter_hour_measurement_window",
                                            "PRESENT")),
        "trade_dataset_claimed_available": lambda d: _tamper_spec(d, lambda s: s[
            "universe_registration"].update({
                "trade_dataset_available": True, "quote_dataset_available": True,
                "second_venue_available": True})),
        "universe_shrunk_to_the_local_four_contract_list": lambda d: _tamper_spec(d, lambda s: s[
            "universe_registration"].update({
                "universe_shrunk_to_local_list": True,
                "instrument_required": ["BTC", "ETH", "BNB", "SOL"],
                "instrument_absent_local": []})),
        "missing_instruments_claimed_present_locally": lambda d: _tamper_spec(
            d, lambda s: s["universe_registration"].update({"instrument_absent_local": []})),
        "coverage_claimed_six_of_six": lambda d: _tamper(d, lambda v: v["prerequisite"].update(
            {"registered_instrument_coverage": "6/6"})),
        "resolution_gate_relabelled": lambda d: _tamper_spec(d, lambda s: s[
            "universe_registration"].update({"finest_stored_interval_seconds": 10})),
        "probe_token_list_narrowed": lambda d: _tamper_spec(d, lambda s: s[
            "universe_registration"].update({"probe_tokens_tested": ["aggtrade", "trades"]})),
        "searched_axis_mislabelled_user_fixed": lambda d: _tamper_spec(
            d, lambda s: s["dca_domain"].update({"spacing_pct_status": "USER_FIXED"})),
        "dca_grid_truncated_to_47": lambda d: _tamper_spec(
            d, lambda s: s["dca_domain"]["grid"].pop()),
        "zero_attempt_claim_replaced_by_an_attempt_dir": lambda d: _fabricate_attempt(
            d, name="INCOMPLETE"),
        "verbatim_leaf_replaced_by_a_paraphrase": lambda d: _tamper_spec(
            d, lambda s: s["hypothesis"].update(
                {"identity_verbatim": "# Crypto quarter-hour order imbalance (paraphrased)"})),
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
        "required_excerpt_items_claimed_nine": lambda d: _tamper_spec(
            d, lambda s: s["universe_registration"].update({"card_excerpt_items": 9})),
    }
    results = []
    ok = True
    for name, mutate in variants.items():
        tmp = tempfile.mkdtemp(prefix="xq-qh-prereq-selftest-")
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

    Build a temp raw tree that carries what this record would need: aggregate trade
    datasets with an `isBuyerMaker` aggressor flag for all six registered contracts
    (including the three missing ones), 1m klines for a missing contract, a spot market and
    a second venue. The raw-side checks must flip to FAIL.
    """
    tmp = tempfile.mkdtemp(prefix="xq-qh-prereq-rawfix-")
    try:
        fixture = os.path.join(tmp, "raw")
        shutil.copytree(raw_root, fixture,
                        ignore=shutil.ignore_patterns(".DS_Store", "__pycache__"))
        # the record's required dataset: trade-level rows with an aggressor side
        for sym in ("BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT", "DOGEUSDT", "ADAUSDT"):
            d = os.path.join(fixture, "binance", "usdm", "aggTrades", sym)
            os.makedirs(d)
            _kw_gz(os.path.join(d, "%s-aggTrades-2025-01.jsonl.gz" % sym),
                   {"ts_ms": 1735689600123, "price": "93450.10", "quantity": "0.42",
                    "isBuyerMaker": False})
        # 1m klines for a registered contract that is absent locally, plus the
        # quarter-hour window itself
        for sym, iv in (("XRPUSDT", "1m"), ("ADAUSDT", "1m"), ("DOGEUSDT", "15m")):
            d = os.path.join(fixture, "binance", "usdm", "klines", sym, iv)
            os.makedirs(d)
            _kw_gz(os.path.join(d, "%s-%s-2025-01.jsonl.gz" % (sym, iv)),
                   {"open_time_ms": 1735689600000, "close_time_ms": 1735689659999,
                    "open": "2.10", "high": "2.11", "low": "2.09", "close": "2.105",
                    "volume": "1000.0"})
        # a spot market and a quote/book surface, and a second venue
        sp = os.path.join(fixture, "binance", "spot", "BTCUSDT", "1d")
        os.makedirs(sp)
        _kw_gz(os.path.join(sp, "BTCUSDT-1d-2025-01.jsonl.gz"),
               {"open_time_ms": 1735689600000, "close_time_ms": 1735775999999,
                "open": "93450.10", "high": "94000.00", "low": "93000.00",
                "close": "93800.00", "volume": "1200.0"})
        bd = os.path.join(fixture, "binance", "usdm", "bookTicker", "BTCUSDT")
        os.makedirs(bd)
        _kw_gz(os.path.join(bd, "BTCUSDT-bookTicker-2025-01.jsonl.gz"),
               {"ts_ms": 1735689600123, "bid_price": "93449.9", "ask_price": "93450.1",
                "bid_qty": "1.0", "ask_qty": "1.2"})
        os.makedirs(os.path.join(fixture, "bybit", "usdm", "klines", "BTCUSDT", "1m"))
        res = run_checks(results_root, fixture)
        failed = [c["id"] for c in res["checks"] if c["status"] == "FAIL"]
        # C1-C6 are pure raw-measurement checks. C7 and C8 additionally re-assert that the
        # persisted artifact agrees with the LIVE raw measurement (trade-name probe +
        # excluded-token collisions), so a fixture that plants trade datasets must flip
        # them too.
        expected = {"C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8"}
        return {"raw_fixture_control": {
            "failed_checks": sorted(failed), "expected": sorted(expected),
            "why": "the fixture plants what this record needs (aggTrade datasets with an "
                   "`isBuyerMaker` flag for all six registered contracts, 1m klines for "
                   "the missing instruments, a spot market, a bookTicker quote surface and "
                   "a second venue); C1-C6 re-measure the raw and C7-C8 re-assert the "
                   "artifact-to-live-raw agreement, so all eight must flip.",
            "detail": {c["id"]: c["detail"] for c in res["checks"] if c["id"] in expected},
            "overall": res["overall"]},
            "overall": "PASS" if expected <= set(failed) else "FAIL"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


DATA_EXTS = (".csv", ".parquet", ".jsonl", ".gz", ".bin", ".feather", ".h5", ".npy", ".arrow",
             ".db", ".sqlite", ".raw", ".hdf5", ".zst", ".zip", ".tar")
DOC_EXTS = (".md", ".txt", ".rst", ".mdx", ".ipynb")
LOG_EXTS = (".err", ".out", ".log")
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
    """A hit is acceptable only if it is a document, code, session/log, build artefact,
    out-of-scope research material, an artefact already disclosed as non-canonical, or a
    data-less stub. Never acceptable as-is: anything that could be a trade-level data store
    for the registered instruments. Those land in UNCLASSIFIED_CLASS and must be disclosed.
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
    if any(m in low for m in NON_CANONICAL_MARKERS):
        return "non_canonical_staging_copy_false_positive"
    if WIKI_DIR_MARKER in low or (low.endswith(".md") and "/wiki/" in low):
        return ("canonical_record_document" if FAMILY in low else "related_wiki_document")
    if any("/%s/" % d in low for d in BACKUP_LIKE_DIRS) or any(m in low
                                                              for m in BACKUP_MARKERS):
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
    if low.endswith(LOG_EXTS):
        return "tool_output_log_false_positive"
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


def host_scan(hits_cap=400, fresh=False):
    """House-wide search for a trade-level dataset (read-only).

    Two probes are run: the registered token list, and - separately and disclosed - the
    excluded tokens, so an exclusion can never hide a hit that the registered probe would
    have found.

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
    for p in _probe_hits(scanned, TRADE_TOKENS):
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
            "examples": tk[:5],
        }
    out = {"roots_scanned": scanned, "skipped_roots": skipped,
            "probe_tokens": list(TRADE_TOKENS),
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
                     "trade-level market-data store for the registered instruments. A "
                     "separate disclosed pass re-ran the %d excluded token(s) - excluded "
                     "because each is a substring of a word or name the host already uses "
                     "(an ordinary English word, a source identifier, a build artefact or "
                     "this pipeline's own grid name), so a hit could never distinguish a "
                     "trade dataset from those artefacts - and returned %s hit(s) in %d "
                     "classification(s), %s of them unclassified."
                     % (len(TRADE_TOKENS), len(scanned), len(records), len(counts),
                        len(unclassified), len(PROBE_EXCLUSIONS),
                        sum(v["hit_count"] for v in excluded_scan.values()),
                        len({c for v in excluded_scan.values()
                             for c in v["classification_counts"]}),
                        sum(len(v["unclassified_data_candidates"])
                            for v in excluded_scan.values())))}
    _HOST_SCAN_CACHE["result"] = out
    return out


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
