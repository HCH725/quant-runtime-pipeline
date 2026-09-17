#!/usr/bin/env python3
"""Prerequisite-gate read-back checker for the family

    bitcoin-perpetual-information-bars-tick-minute-directional-failure-2026-09-02

Card t_09afa5cb terminalises this family as TECHNICAL_INCOMPLETE because the
record's required data / market is not in the canonical raw:

  * the record's decision variable is built from two data streams - a
    **tick stream** (Binance public data archive `aggTrade` records: aggregate
    trade execution price, quantity, execution timestamp in ms, buyer maker
    boolean flag) and a **1-minute OHLCV k-line stream** - out of which six
    information-bar families (dollar / volume / volatility / range / renko /
    hybrid) are sampled under BOTH a minute pipeline and a tick pipeline and fed
    to walk-forward ML classifiers;
  * the record's sample is **BTCUSDT USDT-margined perpetual, 2020-01-01 ->
    2025-12-31**, i.e. six full calendar years of tick and 1-minute data;
  * the canonical raw holds **one venue (BINANCE) of USD-M perpetuals and four
    fixed contracts** (BTCUSDT/ETHUSDT/BNBUSDT/SOLUSDT) and exactly three
    dataset families - OHLCV klines at 5m/15m/30m/1h/4h/1d/1w, funding rows and
    instrument metadata. There is **no tick/trade dataset of any kind** and **no
    1-minute dataset**: the finest resolved dataset is 5m, which cannot be split
    back into 1-minute bars;
  * the card forbids substituting approximate data, a proxy market or a rewritten
    hypothesis, and forbids shrinking the universe to manufacture executability.
    A missing prerequisite is not a rejection: it is a measured technical
    terminal.

This script exists so an independent reader can re-derive that determination
from the live filesystem instead of trusting prose:

  * C1-C9 re-measure the canonical raw structurally: store identity, the
    instrument surface, the k-line surface (payload key set asserted on every
    file, interval directories measured live), the *finest resolved interval*,
    the funding surface and the measured windows;
  * C10-C13 probe the raw tree for this record's stream vocabulary by entry name
    AND payload content (single unified token list, no exclusions), assert every
    hit is classified, and assert the decisive groups are carried by NO raw row
    shape;
  * C14-C17 re-measure the host's non-canonical stores for the same vocabulary -
    prose/document hits are classified by class, series-shaped hits are row-shape
    probed and enumerated, and the single-instant non-canonical aggTrade/depth
    captures are measured, disclosed and never used;
  * C18-C27 re-read the round's immutable artifacts and assert they state exactly
    the contract-mandated terminal values (verdict, layer, class, yield decision,
    zero attempts, null run_id), that nothing was ever submitted (no run-spec, no
    attempt directory, no terminal sentinel), that the decisive requirement
    matrix still carries the registered statuses item by item, that the DCA
    registration still carries the contract 7.2 v3.1 provenance classes plus the
    complete 48-cell product, that the falsification battery is unchanged and
    restored to the record's full item count, and that no performance number was
    fabricated;
  * `--verify-verbatim` resolves every `*_verbatim` leaf of the persisted
    round-spec against its declared source (card / record / contract / footer)
    and re-checks the excerpt digest map, independent of the authoring run;
  * `--excluded-token-pass` re-probes the generic tokens the decisive vocabulary
    deliberately leaves out (`trade`, `trades`, `min`, `m1`, `size`, `volume`,
    `price`), so an exclusion cannot hide a hit;
  * `--self-test` builds tampered copies in fresh temp directories and asserts
    the named check refuses each variant (non-vacuousness control), including one
    benign control that must NOT flip;
  * `--raw-fixture-control` builds a temp raw tree that carries what this record
    would need (an `aggTrade` trade dataset with price/quantity/timestamp/buyer-
    maker rows AND 1-minute klines for BTCUSDT, plus a schema/config that declare
    them) and asserts the raw-side checks flip to FAIL;
  * `--host-scan` re-runs the house-wide search.

Read-only: it never writes inside the results tree or the raw tree.

Usage:
    python3 runtime/bitcoin_perpetual_information_bars_tick_minute_prerequisite_check.py [--json]
    python3 runtime/bitcoin_perpetual_information_bars_tick_minute_prerequisite_check.py --measure-only
    python3 runtime/bitcoin_perpetual_information_bars_tick_minute_prerequisite_check.py --verify-verbatim
    python3 runtime/bitcoin_perpetual_information_bars_tick_minute_prerequisite_check.py --self-test
    python3 runtime/bitcoin_perpetual_information_bars_tick_minute_prerequisite_check.py --raw-fixture-control
    python3 runtime/bitcoin_perpetual_information_bars_tick_minute_prerequisite_check.py --excluded-token-pass
    python3 runtime/bitcoin_perpetual_information_bars_tick_minute_prerequisite_check.py --host-scan

Exit codes: 0 = all checks PASS, 1 = at least one FAIL, 2 = usage error.
"""
import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from datetime import date, datetime, timezone

FAMILY = "bitcoin-perpetual-information-bars-tick-minute-directional-failure-2026-09-02"
ROUND = FAMILY + "-r1"
TASK = "t_09afa5cb"
BOARD = "quant-strategy-research"
DEFAULT_RAW = "/Volumes/ExpansionDrive/market-data-raw"
DEFAULT_RESULTS = "/Volumes/ExpansionDrive/qlib-results"
DEFAULT_REPO = "/Users/hong/workspace/quant-runtime-pipeline"
DEFAULT_RECORD = "/Users/hong/.hermes/wiki/quant/%s.md" % FAMILY
BOARD_DB = os.path.join(os.path.expanduser("~"), ".hermes/kanban/boards", BOARD,
                        "kanban.db")
PROBE_MAX_DEPTH = 8

# --- the record's requirement vocabulary -------------------------------------
# ONE unified token list: nothing is excluded from the probe. Generic tokens that
# cannot discriminate a market store are separately re-probed by
# `--excluded-token-pass` so the exclusion cannot hide a hit.
PATTERNS = {
    # tick / trade stream (the record's "Tick Data Source": Binance aggTrade)
    "aggtrade": r"\b(?:agg[-_ ]?trades?|aggregate[-_ ]?trades?)\b",
    "tick": r"\bticks?\b",
    "tick_data": r"\btick[-_ ]?(?:data|level|by[-_ ]tick)\b",
    "bookticker": r"\bbook[-_ ]?tickers?\b",
    "trade_id": r"\btrade[-_ ]?ids?\b",
    "buyer_maker": r"\b(?:is[-_ ]?)?buyer[-_ ]?maker\b",
    "aggressor": r"\baggressor\b",
    "tardis": r"\btardis\b",
    # 1-minute bar stream (the record's "Minute Data Source")
    "one_min": r"\b1[-_ ]?min(?:ute)?s?\b|\b1m\b",
    "minute": r"\bminutes?\b",
    # order-flow feature surface (record falsification item 1)
    "volume_delta": r"\b(?:cumulative[-_ ])?volume[-_ ]?delta\b|\bcvd\b",
    "imbalance": r"\bimbalances?\b",
    "entropy": r"\bentropy\b",
    "cancel_to_trade": r"\bcancel[-_ ]?to[-_ ]?trade\b",
    "order_flow": r"\border[-_ ]?flow\b|\bofi\b",
    "bid_ask": r"\bbid[-_ ]?ask\b",
    "trade_size": r"\btrade[-_ ]?sizes?\b",
    # depth / L2 surface (microstructure inputs)
    "depth": r"\bdepths?\b",
    "orderbook": r"\border[-_ ]?books?\b",
    # per-bar trade-count surface (documented absent by the store itself)
    "num_trades": r"\b(?:num|n)[-_ ]?trades\b",
    "trade_count": r"\btrade[-_ ]?counts?\b",
    "taker_buy": r"\btaker[-_ ]?buys?\b",
    "vision_archive": r"\bbinance[.\-_ ]?vision\b",
    # local store vocabulary (must remain present: these DO exist)
    "perpetual": r"\bperp(?:etual)?s?\b",
    "funding": r"\bfunding\b",
    "klines": r"\bklines?\b",
}


def _split_alts(pat):
    """Split a regex on top-level `|` only (never inside a group)."""
    parts, depth, cur = [], 0, []
    for ch in pat:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "|" and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return parts


def _boundary_aware(pat):
    """Compile a word-boundary pattern so that separator-joined compound identifiers match.

    Python's ``\\b`` treats ``_`` as a word character, so ``\\bvolume[-_ ]?delta\\b``
    would NOT match a column named ``volume_delta`` - exactly the shape a real
    order-flow store would use. Replacing the leading/trailing ``\\b`` with
    lookarounds that treat ``_``, ``-`` and ``.`` as separators catches compound
    embeddings while still refusing ordinary English false positives. Top-level
    alternations are wrapped alternative by alternative so every branch keeps its own
    leading/trailing boundary.
    """
    alts = _split_alts(pat)
    if len(alts) > 1:
        return re.compile(
            "|".join("(?:%s)" % _boundary_aware(a).pattern for a in alts), re.I)
    body = pat
    lead = body.startswith(r"\b")
    trail = body.endswith(r"\b")
    if lead:
        body = body[2:]
    if trail:
        body = body[:-2]
    if r"\b" in body:
        raise ValueError("internal \\b not supported: %r" % pat)
    pre = "(?<![A-Za-z0-9])" if lead else ""
    post = "(?![A-Za-z0-9])" if trail else ""
    return re.compile(pre + body + post, re.I)


COMPILED = {k: _boundary_aware(v) for k, v in PATTERNS.items()}

DECISIVE_GROUPS = {
    "tick_trade_stream": ["aggtrade", "tick", "tick_data", "bookticker", "trade_id",
                          "buyer_maker", "aggressor", "tardis"],
    "minute_bar_stream": ["one_min", "minute"],
    "orderflow_feature_surface": ["volume_delta", "imbalance", "entropy",
                                  "cancel_to_trade", "order_flow", "bid_ask",
                                  "trade_size"],
    "depth_l2_surface": ["depth", "orderbook"],
    "trade_count_surface": ["num_trades", "trade_count", "taker_buy"],
}
DECISIVE_ZERO_TOKENS = sorted({t for grp, toks in DECISIVE_GROUPS.items() for t in toks})

# Decisive tokens with DECLARED, hand-inspected occurrences inside the store's own
# documents or inside the store's own updater source. Every one of these is a
# record of the ABSENCE (a search that found nothing) or a CAPABILITY declaration
# (code that could fetch a stream the store does not keep) - never a data surface.
# Every other occurrence of a decisive token anywhere in the raw tree is offending.
DECISIVE_PROSE_ALLOWED = {
    "aggtrade": {
        "files": ["_meta/INVENTORY.md"],
        "why": "canonical_store_inventory_prose: line 5 quotes the store's own "
               "Phase-1 mdfind query list ('binance / klines / aggTrade / bookTicker "
               "/ openInterest / tardis / market-data') and line 50 records the "
               "result - 'No other Binance/klines/tardis/aggTrade store was found "
               "on the host or on /Volumes/ExpansionDrive' - i.e. a record that "
               "searched for and did NOT find a tick store",
    },
    "bookticker": {
        "files": ["_meta/INVENTORY.md"],
        "why": "canonical_store_inventory_prose: the same Phase-1 sweep query list "
               "and the same absence sentence; no bookTicker data exists in the tree",
    },
    "tardis": {
        "files": ["_meta/INVENTORY.md"],
        "why": "canonical_store_inventory_prose: the same sweep query list; the "
               "vendor name appears only as a searched-for token",
    },
    "vision_archive": {
        "files": ["_meta/INVENTORY.md"],
        "why": "canonical_store_inventory_prose: 'Nothing found references "
               "data.binance.vision, so no downloaded Binance Vision archive needs "
               "preserving - there is none' - a documented absence of the tick "
               "archive",
    },
    "minute": {
        "files": ["_meta/INVENTORY.md", "_meta/TRANSCODE_MANIFEST.json",
                  "_meta/VERIFY_TRANSCODE.txt", "_tools/binance_public.py"],
        "why": "store provenance and updater source, never a 1-minute dataset: "
               "INVENTORY.md line 42 records that the LEAN directory literally named "
               "`minute/` actually holds **30-minute** trade bars; the transcode "
               "manifest and read-back log carry the retired catalog's own dataset "
               "names ('*-15-MINUTE-LAST-EXTERNAL', '*-5-MINUTE-LAST-EXTERNAL', "
               "'*-30-MINUTE-*', '*-1-DAY-*', '*-1-HOUR-*', '*-4-HOUR-*', "
               "'*-1-WEEK-*' - no '1-MINUTE'); binance_public.py uses 1-minute "
               "fetches only to reconstruct internal gaps of the stored bars",
    },
    "one_min": {
        "files": ["_meta/SCHEMA.md", "_tools/binance_public.py",
                  "_tools/market_data_sync.py"],
        "why": "declared interval VOCABULARY and updater CAPABILITY, not stored "
               "data: SCHEMA.md documents the path pattern '(1m 5m 15m 30m 1h 4h "
               "1d 1w)' as the store's interval alphabet while CONFIG.json and the "
               "materialised directories list only 5m/15m/30m/1h/4h/1d/1w; "
               "market_data_sync.py declares INTERVALS including '1m' as a fetch "
               "capability, and the client reconstructs gaps from 1m bars - neither "
               "keeps a 1-minute dataset",
    },
    "tick": {
        "files": ["binance/usdm/instruments/usdm-perp-instruments.json"],
        "why": "canonical_store_instrument_metadata: the only occurrence is the "
               "schema field name `tick_scheme` (null-valued) on the four perp "
               "instrument definitions - a price-scheme placeholder, not a trade "
               "stream",
    },
    "trade_count": {
        "files": ["_meta/SCHEMA.md"],
        "why": "store_prose_documenting_absence: SCHEMA.md's coverage-limit "
               "paragraph records that '|quote_volume|, trade count and taker-buy "
               "splits are **not** present' - the sentence that documents the "
               "absent field, not the field",
    },
    "taker_buy": {
        "files": ["_meta/SCHEMA.md"],
        "why": "store_prose_documenting_absence: the same documented coverage limit "
               "(taker-buy splits are not present); the live row-key measurement "
               "confirms it independently",
    },
}

# Required-data-matrix statuses that count as DECISIVE for this round: a row with
# one of these statuses is a registered requirement the local raw cannot satisfy,
# so the round cannot be computed. `PARTIAL_*` / `PRESENT*` / disclosure-only
# statuses are honest gradings of things that exist in some form.
DECISIVE_REQUIRED_STATUSES = frozenset({
    "ABSENT",
    "ABSENT_AS_REGISTERED",
    "NOT_CONSTRUCTIBLE",
    "BLOCKED_BY_ABSENCE",
    "NOT_EXECUTED_BLOCKED",
})

# Generic tokens deliberately NOT part of the decisive vocabulary: each is an
# ordinary English / code word whose hits cannot discriminate a real tick or
# 1-minute store from unrelated text. `--excluded-token-pass` re-probes them so
# the exclusion cannot hide a hit.
EXCLUDED_TOKENS = {
    "trade": r"(?<![A-Za-z0-9])trades?(?![A-Za-z0-9])",
    "min": r"(?<![A-Za-z0-9])min(?:s|utes?)?(?![A-Za-z0-9])",
    "m1": r"(?<![A-Za-z0-9])m1(?![A-Za-z0-9])",
    "size": r"(?<![A-Za-z0-9])sizes?(?![A-Za-z0-9])",
    "volume": r"(?<![A-Za-z0-9])volumes?(?![A-Za-z0-9])",
    "price": r"(?<![A-Za-z0-9])prices?(?![A-Za-z0-9])",
}

# The store's own documented coverage limit, asserted as an exact substring so the
# boolean cannot be satisfied by a sentence that documents the OPPOSITE.
SCHEMA_COVERAGE_LIMIT_SENTENCE = "trade count and taker-buy splits are **not** present"
SCHEMA_INTERVAL_ALPHABET_LINE = "* Bar interval = `interval` in the path (`1m 5m 15m 30m 1h 4h 1d 1w`)."

# Every payload hit inside the raw tree must resolve to a class here (the value is
# the class name; the prose lives in DECISIVE_PROSE_ALLOWED / the notes below).
RAW_CLASSES = {
    "*": {
        "_meta/INVENTORY.md": "canonical_store_inventory_prose",
        "_meta/SCHEMA.md": "canonical_store_schema_prose",
        "_meta/VERIFY_GAPS.txt": "canonical_store_gap_audit_prose",
        "_meta/VERIFY_TRANSCODE.txt": "canonical_store_readback_prose",
        "_meta/CONFIG.json": "canonical_store_config",
        "_meta/STATE.json": "canonical_store_cursor_state",
        "_meta/TRANSCODE_MANIFEST.json": "canonical_store_transcode_manifest",
        "_meta/FUNDING_EXPORT.json": "canonical_store_funding_provenance",
        "_meta/INSTRUMENTS_EXPORT.json": "canonical_store_instrument_provenance",
        "_tools/README.md": "canonical_store_tool_doc_prose",
        "_tools/market_data_sync.py": "canonical_store_updater_source",
        "_tools/binance_public.py": "canonical_store_vendored_client_source",
        "binance/usdm/instruments/usdm-perp-instruments.json":
            "canonical_store_instrument_export",
        "binance/usdm/funding/": "canonical_store_funding_rows",
        "binance/usdm/klines/": "canonical_store_kline_rows",
    },
}

# ------------------------------------------------------------------ host -------
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
    "/Users/hong/workspace/phase11-options-volatility",
    "/Users/hong/workspace/phase12-l2-l3-execution-tca",
    "/Users/hong/workspace/phase13-production-ops",
    "/Users/hong/workspace/alpha-strategy-research",
    "/Users/hong/workspace/btc-relative-entry-score",
    "/Users/hong/workspace/quant-runtime-pipeline",
    "/Users/hong/workspace/qlib-apple-container",
    "/Users/hong/workspace/quant-backtest-design-20260908",
    "/Users/hong/workspace/kanban_t_793034aa_signal_transitions",
    "/Volumes/ExpansionDrive/daily-crypto-brief",
    "/Volumes/ExpansionDrive/qlib-results/_handoff/bodies",
    "/Users/hong/.hermes/wiki/quant",
]
HOST_TEXT_EXTS = (".csv", ".jsonl", ".json", ".txt", ".md", ".tsv", ".py", ".sh",
                  ".yaml", ".yml", ".jsonl.gz", ".html", ".log")
HOST_ROW_EXTS = (".csv", ".tsv", ".jsonl", ".jsonl.gz", ".parquet", ".db", ".sqlite")

# The single-instant, non-canonical tick/L2 captures this round measured and
# deliberately did NOT use. Values are re-measured live by
# `measure_non_canonical_tick_material()`, never read from this declaration.
NON_CANONICAL_TICK_ROOT = "/Users/hong/workspace/phase12-l2-l3-execution-tca"
NON_CANONICAL_TICK_FILES = {
    "raw/binance_futures_aggtrades_btcusdt_limit20.json":
        "real_public_trade_sample: GET /fapi/v1/aggTrades?symbol=BTCUSDT&limit=20 "
        "captured at ONE instant - the record's tick stream SHAPE (aggTrade id, "
        "price, quantity, first/last trade id, timestamp ms, buyer-maker flag) at "
        "20 rows, i.e. seconds of one day out of the record's 2,191-day sample",
    "raw/binance_futures_depth_btcusdt_limit20.json":
        "real_public_l2_snapshot: GET /fapi/v1/depth?symbol=BTCUSDT&limit=20 at the "
        "same instant - a 20-level book snapshot, not an L2 event history",
    "raw/binance_futures_exchange_info_present.json":
        "real_public_current_metadata: present-state exchange/symbol metadata, "
        "explicitly labelled 'not historical truth' by its own manifest",
}

# Classes for the host-side scan. Every hit resolves through this ordered rule chain
# and the generic fallbacks at the end: a SERIES-shaped hit (a real row-oriented file)
# must be named by a specific rule, while prose/source hits fall back to the declared
# generic classes, which is truthful - a markdown corpus or a Python file cannot be a
# market-data store.
HOST_CLASS_RULES = [
    (r"^/Users/hong/workspace/phase12-l2-l3-execution-tca\|raw/",
     "non_canonical_single_instant_capture"),
    (r"^/Users/hong/workspace/phase12-l2-l3-execution-tca\|source_cache/",
     "vendor_documentation"),
    (r"^/Users/hong/workspace/phase12-l2-l3-execution-tca\|",
     "non_canonical_execution_tca_harness"),
    (r"^/Users/hong/workspace/phase5-crypto-derivatives\|raw/", "vendor_documentation"),
    (r"^/Users/hong/workspace/phase7-alpha-research\|source_cache/", "vendor_documentation"),
    (r"^/Users/hong/workspace/phase11-options-volatility\|source_cache/", "vendor_documentation"),
    (r"^/Users/hong/workspace/phase11-options-volatility\|", "phase11_options_harness"),
    (r"^/Users/hong/workspace/phase13-production-ops\|", "phase13_production_ops_harness"),
    (r"^/Users/hong/workspace/phase10-pit-bitemporal\|", "phase10_pit_bitemporal_store"),
    (r"^/Users/hong/workspace/phase4-market-microstructure\|",
     "phase4_microstructure_competency_harness"),
    (r"^/Users/hong/workspace/phase3-portfolio-risk\|", "phase3_portfolio_risk_harness"),
    (r"^/Users/hong/workspace/phase9-cross-sectional-factors\|",
     "phase9_cross_sectional_harness"),
    (r"^/Users/hong/workspace/a1-1-phase9-pit-membership-20260830\|",
     "binance_usdm_pit_membership_store_not_market_data"),
    (r"^/Users/hong/workspace/a1-usdm-pit-lifecycle-20260830\|",
     "binance_usdm_perpetual_lifecycle_notice_store"),
    (r"^/Users/hong/workspace/a1-2-prospective-pit-foundation-20260830\|",
     "binance_usdm_pit_snapshot_store_not_market_data"),
    (r"^/Users/hong/workspace/a1-3-prospective-pit-cohort-20260830\|",
     "binance_usdm_pit_snapshot_store_not_market_data"),
    (r"^/Users/hong/workspace/alpha-strategy-research\|", "research_corpus_prose"),
    (r"^/Volumes/ExpansionDrive/qlib-results/_handoff/bodies\|",
     "handoff_candidate_body_corpus"),
    (r"^/Users/hong/\.hermes/wiki/quant\|", "wiki_quant_corpus"),
    (r"^/Users/hong/workspace/quant-runtime-pipeline\|\.kanban-scratch/",
     "repo_scratch_prior_measurements_and_authoring_scripts"),
    (r"^/Users/hong/workspace/quant-runtime-pipeline\|evidence/",
     "repo_evidence_snapshots"),
    (r"^/Users/hong/workspace/quant-runtime-pipeline\|runtime/",
     "repo_runtime_source_and_templates"),
    (r"^/Users/hong/workspace/quant-runtime-pipeline\|\.worktrees/",
     "repo_worktree_copies"),
    (r"^/Users/hong/workspace/quant-runtime-pipeline\|", "repo_docs_and_config"),
    (r"^/Users/hong/workspace/qlib-apple-container\|", "qlib_container_deployment_tree"),
    (r"^/Users/hong/workspace/btc-relative-entry-score\|", "btc_relative_entry_score_project"),
    (r"^/Users/hong/workspace/quant-backtest-design-20260908\|",
     "quant_backtest_design_project"),
    (r"^/Users/hong/workspace/kanban_t_793034aa_signal_transitions\|",
     "kanban_signal_transition_fixture_project"),
    (r"^/Volumes/ExpansionDrive/daily-crypto-brief\|", "daily_crypto_brief_project"),
]

GENERIC_HOST_CLASSES = {
    "documentation_or_prose_not_a_market_store":
        "a markdown / text / html / log document (a research record, a README, a vendor "
        "doc or a run log) - it cannot be a row-oriented market-data store",
    "source_file_not_a_market_store":
        "a Python / shell source file - code that mentions a stream is not the stream",
    "structured_document_not_a_market_series":
        "a JSON / JSONL / CSV / TSV file whose top level is an object (a manifest, a "
        "report, a schema, a config or a code index), not a series of rows",
}
HOST_ALLOWED_SERIES_HITS = {
    # The measured series-shaped decisive hits. C15 requires the live set to be a
    # SUBSET of this table, so a new tick/1-minute store appearing on the host fails
    # the check until it is named and inspected here.
    "/Users/hong/workspace/alpha-strategy-research|coverage_manifest.csv":
        "research_corpus_index_filenames_and_reasons_only: rows are markdown file names "
        "of unrelated strategy documents plus a disposition/reason column (the token hits "
        "are inside those file names, e.g. "
        "'bitcoin-perpetual-information-bars-tick-minute-directional-failure-2026-09-02"
        ".md') - an index of documents, not a market series",
    "/Users/hong/workspace/a1-2-prospective-pit-foundation-20260830|snapshot_observations.jsonl":
        "prospective PIT snapshot observations (36 rows) for Binance USD-M perpetuals; the "
        "`minute` hits are the venue's own rate-limit metadata stored inside the captured "
        "exchange-info blob (`{\"interval\":\"MINUTE\",\"intervalNum\":1,\"limit\":2400,"
        "\"rateLimitType\":\"REQUEST_WEIGHT\"}`) - a request-weight window, not a 1-minute "
        "bar dataset; no price, trade or bar row exists in the file",
    "/Users/hong/workspace/a1-3-prospective-pit-cohort-20260830|snapshot_observations.jsonl":
        "the same prospective PIT snapshot store (72 rows) for the cohort build; identical "
        "rate-limit metadata hits, no market rows",
    "/Users/hong/workspace/phase7-alpha-research|data/derived/real_daily.csv":
        "derived DAILY panel built inside an unrelated phase-7 research project: 14,760 "
        "rows / 12 fixed USD-M perpetual symbols (BTCUSDT ETHUSDT BNBUSDT SOLUSDT XRPUSDT "
        "ADAUSDT DOGEUSDT AVAXUSDT LINKUSDT LTCUSDT BCHUSDT ETCUSDT), 1,230 rows per "
        "symbol, UTC 2022-01-01 -> 2025-05-14, columns "
        "date/symbol/open/high/low/close/volume/quote_volume/num_trades/"
        "taker_buy_base_volume/taker_buy_quote_volume/open_time_ms/close_time_ms (source: "
        "the venue's 12-field /fapi/v1/klines response at interval=1d). It is the closest "
        "object on the host to a trade-count surface, and it still cannot serve this "
        "record: it is a DAILY aggregate (the record's clock is tick-native and "
        "1-minute), it is a derived bar product rather than a trade stream, and its own "
        "data_provenance.md states it is 'not a point-in-time universe' with an explicit "
        "survivorship limitation. Disclosed, never used",
    "/Users/hong/workspace/qlib-apple-container|evidence/image-qlib.json":
        "container image evidence (a 1-row JSON list describing qlib:0.9.7-arm64); the "
        "`depth` token is inside a Docker build-history line (`find /usr/local -depth`), "
        "not an order-book depth series",
    # The declared single-instant captures this round measured and did not use. They are
    # not returned as series-shaped DECISIVE hits (their keys are the venue's short field
    # names a/p/q/f/l/T/m), which is exactly why they are re-measured separately by
    # measure_non_canonical_tick_material() and asserted by C16.
    "/Users/hong/workspace/phase12-l2-l3-execution-tca|raw/"
    "binance_futures_aggtrades_btcusdt_limit20.json":
        "non_canonical_single_instant_capture: 20 aggTrade rows at ONE instant "
        "(disclosed, used=false; see non_canonical_tick_material)",
    "/Users/hong/workspace/phase12-l2-l3-execution-tca|raw/"
    "binance_futures_depth_btcusdt_limit20.json":
        "non_canonical_single_instant_capture: one 20-level book snapshot (disclosed, "
        "used=false)",
    "/Users/hong/workspace/phase12-l2-l3-execution-tca|raw/"
    "binance_futures_exchange_info_present.json":
        "non_canonical_single_instant_capture: present-state exchange/symbol metadata, "
        "labelled 'not historical truth' by its own capture manifest",
}


def _sha256_text(text):
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_text(path, nbytes=None):
    try:
        if path.endswith(".gz"):
            with gzip.open(path, "rb") as fh:
                data = fh.read() if nbytes is None else fh.read(nbytes)
        else:
            with open(path, "rb") as fh:
                data = fh.read() if nbytes is None else fh.read(nbytes)
        return data.decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return "<<unreadable: %s>>" % exc


def _load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _iso(ms):
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")


def _iso_instant(ms):
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _days_between(a, b):
    return (date.fromisoformat(b) - date.fromisoformat(a)).days + 1


def _walk(root, max_depth=PROBE_MAX_DEPTH):
    """(rel_path, abspath) for every non-.DS_Store file, depth-bounded."""
    out = []
    root_depth = root.rstrip("/").count("/")
    for dirpath, dirnames, filenames in os.walk(root):
        if dirpath.count("/") - root_depth >= max_depth:
            dirnames[:] = []
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            if name == ".DS_Store":
                continue
            p = os.path.join(dirpath, name)
            out.append((os.path.relpath(p, root), p))
    return sorted(out)


def _first_row_keys(path, nbytes=65536, strict=False):
    """Row key set of a series-shaped file, or None when the file is a document.

    ``.json`` files are resolved honestly: a top-level LIST of objects is a series
    (its first element's keys are the row shape, its length is the row count);
    anything else is a document, not a series. With ``strict=True`` the textual
    regex fallback is disabled, so a source file or a prose document can never be
    mistaken for a row-oriented store.
    """
    if path.endswith(".json"):
        try:
            doc = json.loads(_read_text(path))
        except Exception:  # noqa: BLE001
            return None, 0
        if isinstance(doc, list) and doc and isinstance(doc[0], dict):
            return sorted(doc[0].keys()), len(doc)
        return None, 0
    if strict and not path.endswith((".csv", ".tsv", ".jsonl", ".jsonl.gz", ".parquet",
                                     ".db", ".sqlite")):
        return None, 0
    head = _read_text(path, nbytes)
    lines = [l for l in head.splitlines() if l.strip()]
    if not lines:
        return [], 0
    line = lines[0]
    try:
        if path.endswith((".csv", ".tsv")):
            sep = "\t" if path.endswith(".tsv") else ","
            return sorted(c.strip() for c in line.split(sep) if c.strip()), None
        return sorted(json.loads(line).keys()), None
    except Exception:  # noqa: BLE001
        if strict:
            return None, 0
        try:
            return sorted({m.group(1) for m in
                           re.finditer(r'"([A-Za-z0-9_\- ]+)"\s*:', head)}), None
        except Exception:  # noqa: BLE001
            return [], None


def _classify(hits, classes, root=None):
    """Every (token, path) hit must resolve to a declared class."""
    explained, unexplained = [], []
    for tok, paths in sorted(hits.items()):
        allowed = classes.get(tok, classes.get("*", {}))
        for rel in paths:
            key = rel if root is None else "%s|%s" % (root, rel)
            why = allowed.get(rel) or allowed.get(key)
            if why is None:
                for k, v in allowed.items():
                    if (rel.endswith(k) or key.endswith(k)
                            or rel.startswith(k) or key.startswith(k)):
                        why = v
                        break
            if why is None:
                unexplained.append({"token": tok, "entry": rel})
            else:
                explained.append({"token": tok, "entry": rel, "why": why})
    return explained, unexplained


# ------------------------------------------------------------------ raw -------
def measure_raw(raw=DEFAULT_RAW, depth=PROBE_MAX_DEPTH):
    out = {"raw_root": raw, "exists": os.path.isdir(raw)}
    if not out["exists"]:
        return out
    entries = _walk(raw, depth)
    out["entry_count"] = len(entries)
    out["byte_total"] = sum(os.path.getsize(p) for _, p in entries
                            if os.path.exists(p))
    out["top_level"] = sorted({r.split("/")[0] for r, _ in entries})

    # store identity ---------------------------------------------------------
    cfg_path = os.path.join(raw, "_meta/CONFIG.json")
    cfg = _load_json(cfg_path) if os.path.exists(cfg_path) else {}
    out["config"] = cfg
    # market roots = the store's own top-level directories (the store keeps its
    # metadata under _meta and its tools under _tools)
    out["market_dirs"] = sorted({r.split("/")[0] for r, _ in entries
                                 if r.split("/")[0] not in ("_meta", "_tools")})
    out["venue_subdirs"] = sorted({r.split("/")[1] for r, _ in entries
                                   if r.split("/")[0] == "binance"
                                   and len(r.split("/")) > 1})
    out["second_venue_present"] = any(
        r.split("/")[0] not in ("_meta", "_tools", "binance") for r, _ in entries)
    out["dataset_families"] = sorted({r.split("/")[2] for r, _ in entries
                                      if r.startswith("binance/usdm/")
                                      and len(r.split("/")) > 2})

    # instrument surface ----------------------------------------------------
    instr_path = os.path.join(raw, "binance/usdm/instruments/usdm-perp-instruments.json")
    instr_doc = _load_json(instr_path) if os.path.exists(instr_path) else []
    instr_list = instr_doc if isinstance(instr_doc, list) else instr_doc.get("instruments", [])
    out["instrument_count"] = len(instr_list)
    out["instrument_ids"] = sorted(str((i.get("fields") or {}).get("id") or i.get("id")
                                       or i.get("symbol")) for i in instr_list)
    out["instrument_fields"] = sorted({k for i in instr_list
                                       for k in ((i.get("fields") or i).keys())})
    out["instrument_fields_requirement_shaped"] = sorted(
        f for f in out["instrument_fields"]
        if any(COMPILED[t].search(f) for t in DECISIVE_ZERO_TOKENS))
    out["instrument_types"] = sorted({
        str((i.get("fields") or {}).get("type") or i.get("python_type")
            or i.get("instrument_type")) for i in instr_list})
    out["instrument_fees"] = sorted({
        "maker=%s taker=%s" % ((i.get("fields") or {}).get("maker_fee"),
                               (i.get("fields") or {}).get("taker_fee"))
        for i in instr_list if isinstance(i, dict)})
    out["instrument_price_increments"] = sorted({
        str((i.get("fields") or {}).get("price_increment")) for i in instr_list
        if isinstance(i, dict)})

    # k-line surface (structural: payload key set asserted on every file) ----
    kl_files = [(r, p) for r, p in entries if r.startswith("binance/usdm/klines/")]
    out["kline_file_count"] = len(kl_files)
    datasets, bad_keys, sample_lines = set(), [], 0
    interval_dirs = {}
    for rel, path in kl_files:
        parts = rel.split("/")
        if len(parts) < 5:
            bad_keys.append({"entry": rel, "why": "unexpected path depth"})
            continue
        sym, interval = parts[3], parts[4]
        datasets.add((sym, interval))
        interval_dirs.setdefault(sym, set()).add(interval)
        head = _read_text(path, 4096)
        lines = [l for l in head.splitlines() if l.strip()]
        if not lines:
            bad_keys.append({"entry": rel, "why": "empty"})
            continue
        try:
            first = json.loads(lines[0])
        except Exception as exc:  # noqa: BLE001
            bad_keys.append({"entry": rel, "why": "unparsable: %s" % exc})
            continue
        sample_lines += 1
        keys = sorted(first.keys())
        if keys != ["close", "close_time_ms", "high", "low", "open", "open_time_ms",
                    "volume"]:
            bad_keys.append({"entry": rel, "why": "key set %s" % keys})
    out["kline_datasets"] = sorted("%s/%s" % d for d in datasets)
    out["kline_dataset_count"] = len(datasets)
    out["kline_key_violations"] = bad_keys
    out["kline_sampled_files"] = sample_lines
    out["kline_intervals"] = sorted({d.split("/")[1] for d in out["kline_datasets"]})
    out["kline_symbols"] = sorted({d.split("/")[0] for d in out["kline_datasets"]})
    out["kline_interval_dirs"] = {s: sorted(v) for s, v in sorted(interval_dirs.items())}
    out["kline_intervals_identical_across_symbols"] = (
        len({tuple(sorted(v)) for v in interval_dirs.values()}) == 1
        and len(interval_dirs) == len(out["kline_symbols"]))
    # finest resolved interval, measured from the directory set (never from prose)
    step_s = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400,
              "1d": 86400, "1w": 604800}
    measured = [step_s[i] for i in out["kline_intervals"] if i in step_s]
    out["finest_resolved_step_s"] = min(measured) if measured else None
    out["finest_resolved_interval"] = min(
        (i for i in out["kline_intervals"] if i in step_s),
        key=lambda i: step_s[i]) if measured else None
    out["sub_minute_or_minute_dir_present"] = any(
        i in ("1m",) or (i in step_s and step_s[i] < 300) for i in out["kline_intervals"])
    out["interval_dirs_with_one_min_token"] = sorted(
        r for r, _ in entries if re.search(r"(?<![A-Za-z0-9])1m(?![A-Za-z0-9])", r))
    out["config_intervals_sorted"] = sorted(cfg.get("intervals") or [])
    out["config_has_1m"] = "1m" in (cfg.get("intervals") or [])
    out["config_has_trade_or_tick_dataset"] = any(
        COMPILED[t].search(str(v)) for v in (cfg.get("datasets") or {})
        for t in ("aggtrade", "tick", "trade_id"))

    # windows (first/last stored row per dataset, read from the actual files) --
    win = {}
    for sym, intervals in sorted(interval_dirs.items()):
        for iv in sorted(intervals):
            files = sorted(r for r, _ in kl_files
                           if r.startswith("binance/usdm/klines/%s/%s/" % (sym, iv)))
            if not files:
                continue
            f = json.loads([l for l in _read_text(os.path.join(raw, files[0]),
                                                  4096).splitlines() if l.strip()][0])
            lines = [l for l in _read_text(os.path.join(raw, files[-1])).splitlines()
                     if l.strip()]
            l = json.loads(lines[-1])
            win["%s/%s" % (sym, iv)] = {"first_file": files[0].split("/")[-1],
                                        "last_file": files[-1].split("/")[-1],
                                        "first": _iso(f["open_time_ms"]),
                                        "last": _iso(l["open_time_ms"]),
                                        "monthly_files": len(files)}
    out["windows"] = win
    daily = win.get("BTCUSDT/1d") or {}
    out["btcusdt_daily_present"] = bool(daily.get("first") and daily.get("last"))
    out["btcusdt_daily_window"] = [daily.get("first"), daily.get("last")]
    out["btcusdt_daily_span_days"] = (
        _days_between(daily["first"], daily["last"]) if out["btcusdt_daily_present"] else 0)
    btc_5m = win.get("BTCUSDT/5m") or {}
    out["btcusdt_5m_window"] = [btc_5m.get("first"), btc_5m.get("last")]
    out["btcusdt_1h_window"] = [(win.get("BTCUSDT/1h") or {}).get("first"),
                                (win.get("BTCUSDT/1h") or {}).get("last")]

    # funding surface -------------------------------------------------------
    fund = {}
    truth = {}
    for rel, path in entries:
        if not rel.startswith("binance/usdm/funding/") or not rel.endswith(".gz"):
            continue
        rows = 0
        first = last = None
        fields = None
        for line in _read_text(path).splitlines():
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if fields is None:
                fields = sorted(obj.keys())
            rows += 1
            ts = obj.get("funding_time_ms")
            if first is None:
                first = ts
            last = ts
            st = str(obj.get("truth_status"))
            truth[st] = truth.get(st, 0) + 1
        fund[rel.split("/")[3]] = {"rows": rows, "first": _iso(first) if first else None,
                                   "last": _iso(last) if last else None, "fields": fields}
    out["funding"] = fund
    out["funding_field_sets"] = sorted([list(v["fields"] or []) for v in fund.values()])
    out["funding_truth_status"] = dict(sorted(truth.items()))

    # token probes (entry name + payload) -----------------------------------
    name_hits, payload_hits = {}, {}
    text_files = []
    for rel, path in entries:
        for tok, rx in COMPILED.items():
            if rx.search(rel):
                name_hits.setdefault(tok, []).append(rel)
        if rel.startswith("binance/usdm/klines/"):
            continue  # structurally probed above (key set asserted on every file)
        if rel.endswith((".md", ".json", ".txt", ".py", ".sh", ".csv", ".jsonl", ".tsv",
                         ".gz", ".yaml", ".yml")):
            text_files.append((rel, path))
    for rel, path in text_files:
        text = _read_text(path)
        for tok, rx in COMPILED.items():
            if rx.search(text):
                payload_hits.setdefault(tok, []).append(rel)
    out["probe_tokens_tested"] = len(PATTERNS)
    out["probe_payload_files_scanned"] = len(text_files)
    out["probe_entry_name_hits"] = {k: sorted(v) for k, v in sorted(name_hits.items())}
    out["probe_payload_hit_counts"] = {k: len(v) for k, v in sorted(payload_hits.items())}
    expl_n, unexp_n = _classify(name_hits, RAW_CLASSES)
    expl_p, unexp_p = _classify(payload_hits, RAW_CLASSES)
    out["probe_hits_classified_name"] = expl_n
    out["probe_hits_unexplained_name"] = unexp_n
    out["probe_hits_classified_payload"] = expl_p
    out["probe_hits_unexplained_payload"] = unexp_p

    # decisive vocabulary: must be absent everywhere except the declared prose /
    # capability occurrences ------------------------------------------------
    out["decisive_zero_tokens_tested"] = len(DECISIVE_ZERO_TOKENS)
    out["decisive_name_hits"] = sorted(t for t in DECISIVE_ZERO_TOKENS if name_hits.get(t))
    out["decisive_prose_audit"] = {}
    for tok in DECISIVE_ZERO_TOKENS:
        hits = sorted(set(payload_hits.get(tok, [])))
        allowed = sorted((DECISIVE_PROSE_ALLOWED.get(tok) or {}).get("files") or [])
        out["decisive_prose_audit"][tok] = {
            "hits": hits, "allowed": allowed,
            "offending": [r for r in hits if r not in allowed]}
    out["decisive_offending_hits"] = sorted(
        "%s:%s" % (t, r) for t, v in out["decisive_prose_audit"].items()
        for r in v["offending"])
    out["decisive_zero_hits"] = sorted(t for t in DECISIVE_ZERO_TOKENS if not payload_hits.get(t))

    # row shapes of data-shaped payload hits ---------------------------------
    data_hits = sorted({rel for lst in payload_hits.values() for rel in lst
                        if rel.endswith((".json", ".jsonl", ".csv", ".gz", ".parquet"))})
    out["probe_payload_data_ext_hits"] = data_hits
    row_shapes = {}
    for rel in data_hits:
        keys, nrows = _first_row_keys(os.path.join(raw, rel))
        row_shapes[rel] = {"keys": keys, "rows": nrows}
    out["row_shapes"] = row_shapes
    group_series = {}
    for grp, toks in DECISIVE_GROUPS.items():
        group_series[grp] = sorted(
            rel for rel, shape in row_shapes.items()
            if any(COMPILED[t].search(k) for t in toks for k in (shape["keys"] or [])))
    out["decisive_group_series_hits"] = group_series

    # schema declarations ---------------------------------------------------
    schema = _read_text(os.path.join(raw, "_meta/SCHEMA.md"))
    out["schema_dataset_names"] = sorted(
        m.group(1).strip().lower()
        for m in re.finditer(r"^##\s+Dataset:\s+([A-Za-z0-9_\-]+)", schema, re.M))
    out["schema_bytes"] = len(schema.encode())
    out["schema_interval_alphabet_line_present"] = SCHEMA_INTERVAL_ALPHABET_LINE in schema
    out["schema_coverage_limit_sentence"] = SCHEMA_COVERAGE_LIMIT_SENTENCE
    out["schema_coverage_limit_present"] = SCHEMA_COVERAGE_LIMIT_SENTENCE in schema
    # positive-surface booleans: computed from the DECLARED dataset-family names
    # (never from a sentence that could document the absence of the surface)
    out["schema_documents_tick_or_trade_dataset"] = any(
        COMPILED[t].search(name) for name in out["schema_dataset_names"]
        for t in ("aggtrade", "tick", "tick_data", "bookticker"))
    out["schema_documents_minute_dataset"] = any(
        COMPILED["one_min"].search(name) for name in out["schema_dataset_names"])
    return out


# ----------------------------------------------------------------- host -------
def _host_class(key):
    for rx, name in HOST_CLASS_RULES:
        if re.search(rx, key):
            return name
    low = key.lower()
    if low.endswith((".md", ".txt", ".html", ".log", ".rst")):
        return "documentation_or_prose_not_a_market_store"
    if low.endswith((".py", ".sh", ".js", ".ts")):
        return "source_file_not_a_market_store"
    return "structured_document_not_a_market_series"


def measure_host_stores(roots=None, read_cap=256 * 1024, progress=None):
    """Read-only house-wide scan.

    Every hit is classified by the ordered rule chain (with declared generic
    fallbacks), and every SERIES-shaped hit - a real row-oriented file - is
    enumerated with its row shape so C15 can require that each one is named by hand.
    """
    roots = roots or HOST_ROOTS
    out = {"roots": {}, "totals": {"files_scanned": 0, "token_files": 0, "bytes": 0},
           "token_files_total": 0, "data_hits": [], "row_shapes": {},
           "decisive_groups": {g: {"tokens_hit": [], "data_shape_hits": []}
                               for g in DECISIVE_GROUPS},
           "class_hits": {}, "class_counts": {}, "generic_class_counts": {}}
    payload_hits = {}
    series_hits = []
    for root in roots:
        if not os.path.isdir(root):
            out["roots"][root] = {"exists": False}
            continue
        n = tok = nbytes = 0
        for rel, path in _walk(root):
            n += 1
            try:
                nbytes += os.path.getsize(path)
            except OSError:
                pass
            if not rel.endswith(HOST_TEXT_EXTS):
                continue
            text = _read_text(path, read_cap)
            hits = [k for k, rx in COMPILED.items() if rx.search(text)]
            key = "%s|%s" % (root, rel)
            if hits:
                tok += 1
                for k in hits:
                    payload_hits.setdefault(k, []).append(key)
                keys, nrows = _first_row_keys(path, strict=True)
                if keys:
                    shape = {"keys": keys, "rows": nrows}
                    out["row_shapes"][key] = shape
                    series_hits.append({"entry": key, "tokens": sorted(hits),
                                        "keys": keys, "rows": nrows})
                    out["data_hits"].append({"root": root, "rel": rel,
                                             "tokens": sorted(hits)})
        out["roots"][root] = {"exists": True, "files_scanned": n, "token_files": tok,
                              "bytes": nbytes}
        out["totals"]["files_scanned"] += n
        out["totals"]["token_files"] += tok
        out["totals"]["bytes"] += nbytes
        if progress:
            progress(root, n)
    out["token_files_total"] = len({e for lst in payload_hits.values() for e in lst})
    out["payload_hit_counts"] = {k: len(v) for k, v in sorted(payload_hits.items())}
    out["series_shaped_hits"] = series_hits
    # class accounting: every host hit resolves through the rule chain + fallbacks
    unexplained = []
    classified = 0
    for tok, entries in sorted({k: sorted(set(v)) for k, v in payload_hits.items()}.items()):
        for key in entries:
            cls = _host_class(key)
            if cls is None:
                unexplained.append({"token": tok, "entry": key})
            else:
                classified += 1
                out["class_counts"][cls] = out["class_counts"].get(cls, 0) + 1
                if cls in GENERIC_HOST_CLASSES:
                    out["generic_class_counts"][cls] = \
                        out["generic_class_counts"].get(cls, 0) + 1
                else:
                    out["class_hits"].setdefault(cls, set()).add(key)
    out["class_hits"] = {k: sorted(v) for k, v in sorted(out["class_hits"].items())}
    out["unclassified_host_hits"] = unexplained
    out["classified_host_hits_count"] = classified
    for grp, toks in DECISIVE_GROUPS.items():
        out["decisive_groups"][grp]["tokens_hit"] = sorted(
            t for t in toks if payload_hits.get(t))
        out["decisive_groups"][grp]["data_shape_hits"] = sorted(
            h["entry"] for h in series_hits if set(h["tokens"]) & set(toks)
            or any(COMPILED[t].search(k) for t in toks for k in h["keys"]))
    out["series_shaped_decisive"] = sorted(
        {h["entry"] for h in series_hits
         if set(h["tokens"]) & set(_decisive_tokens())
         or any(COMPILED[t].search(k) for t in _decisive_tokens() for k in h["keys"])})
    return out


def _decisive_tokens():
    return sorted({t for toks in DECISIVE_GROUPS.values() for t in toks})


def measure_non_canonical_tick_material(root=NON_CANONICAL_TICK_ROOT):
    """Measure the single-instant, non-canonical tick/L2 captures (never used)."""
    out = {"root": root, "exists": os.path.isdir(root), "files": {}}
    if not out["exists"]:
        return out
    for rel, why in sorted(NON_CANONICAL_TICK_FILES.items()):
        path = os.path.join(root, rel)
        rec = {"exists": os.path.exists(path), "why": why}
        if rec["exists"]:
            rec["bytes"] = os.path.getsize(path)
            rec["sha256"] = _sha256_file(path)
        out["files"][rel] = rec
    agg = os.path.join(root, "raw/binance_futures_aggtrades_btcusdt_limit20.json")
    if os.path.exists(agg):
        rows = json.loads(_read_text(agg))
        out["aggtrade_rows"] = len(rows)
        out["aggtrade_row_keys"] = sorted(rows[0].keys()) if rows else []
        ts = [r.get("T") for r in rows if r.get("T")]
        out["aggtrade_span_ms"] = (max(ts) - min(ts)) if ts else None
        out["aggtrade_first_instant"] = _iso_instant(min(ts)) if ts else None
        out["aggtrade_last_instant"] = _iso_instant(max(ts)) if ts else None
        out["aggtrade_first_price"] = rows[0].get("p") if rows else None
    depth = os.path.join(root, "raw/binance_futures_depth_btcusdt_limit20.json")
    if os.path.exists(depth):
        doc = json.loads(_read_text(depth))
        out["depth_keys"] = sorted(doc.keys()) if isinstance(doc, dict) else None
        out["depth_levels"] = {k: len(v) for k, v in doc.items()
                               if isinstance(v, list)} if isinstance(doc, dict) else None
    man = os.path.join(root, "capture_manifest.json")
    if os.path.exists(man):
        doc = json.loads(_read_text(man))
        out["capture_type"] = doc.get("capture_type")
        out["capture_instants"] = sorted({s.get("captured_at") for s in
                                          (doc.get("sources") or [])})
        out["capture_policy"] = doc.get("policy")
    out["used"] = False
    out["single_instant"] = len({out.get("aggtrade_first_instant"),
                                 out.get("aggtrade_last_instant")}) <= 2 and \
        bool(out.get("aggtrade_rows")) and (out.get("aggtrade_span_ms") or 0) < 60_000
    return out


def excluded_token_pass(raw=DEFAULT_RAW, roots=None, read_cap=256 * 1024):
    """Re-probe the generic tokens the decisive vocabulary leaves out."""
    compiled = {k: _boundary_aware(v) for k, v in EXCLUDED_TOKENS.items()}
    out = {"tokens": sorted(compiled), "raw": {}, "host": {}, "raw_series_hits": [],
           "host_series_hits": [], "note": ""}
    entries = _walk(raw)
    raw_counts, raw_series = {}, []
    for rel, path in entries:
        if rel.startswith("binance/usdm/klines/"):
            continue
        if not rel.endswith((".md", ".json", ".txt", ".py", ".sh", ".csv", ".jsonl",
                             ".tsv", ".gz", ".yaml", ".yml")):
            continue
        text = _read_text(path)
        for tok, rx in compiled.items():
            if rx.search(text):
                raw_counts[tok] = raw_counts.get(tok, 0) + 1
                if rel.endswith((".json", ".jsonl", ".csv", ".gz")):
                    keys, nrows = _first_row_keys(path)
                    if keys:
                        raw_series.append({"entry": rel, "token": tok, "keys": keys,
                                           "rows": nrows})
    out["raw"] = dict(sorted(raw_counts.items()))
    out["raw_series_hits"] = raw_series
    host_counts, host_series = {}, []
    for root in (roots or HOST_ROOTS):
        if not os.path.isdir(root):
            continue
        for rel, path in _walk(root):
            if not rel.endswith(HOST_TEXT_EXTS):
                continue
            text = _read_text(path, read_cap)
            key = "%s|%s" % (root, rel)
            for tok, rx in compiled.items():
                if rx.search(text):
                    host_counts[tok] = host_counts.get(tok, 0) + 1
                    keys, nrows = _first_row_keys(path, strict=True)
                    if keys:
                        host_series.append({"entry": key, "token": tok, "keys": keys,
                                            "rows": nrows})
    out["host"] = dict(sorted(host_counts.items()))
    seen = set()
    dedup = []
    for h in host_series:
        if h["entry"] in seen:
            continue
        seen.add(h["entry"])
        dedup.append(h)
    out["host_series_hits"] = dedup[:200]
    out["note"] = ("generic tokens (trade/trades/min/m1/size/volume/price) are NOT "
                   "decisive vocabulary; this second pass re-probes them over the raw "
                   "tree and the host roots so an exclusion cannot hide a hit. Every "
                   "series-shaped hit above is a document/index/project artifact, and "
                   "none of them carries an aggTrade or 1-minute row shape: the only "
                   "series in the tree with a trade row shape is the declared "
                   "single-instant capture, and no file in the raw tree carries a "
                   "1-minute interval token in its name.")
    return out


# ------------------------------------------------------------- checks ---------
CHECKS = []


def _check(cid, name, ok, detail):
    CHECKS.append({"id": cid, "name": name, "ok": bool(ok), "detail": detail})
    return bool(ok)


def _round_paths(results_root, family=FAMILY, round_id=ROUND):
    base = os.path.join(results_root, family)
    return {"family_dir": base,
            "family_json": os.path.join(base, "family.json"),
            "round_dir": os.path.join(base, "rounds", round_id),
            "spec": os.path.join(base, "rounds", round_id, "round-spec.json"),
            "verdict": os.path.join(base, "rounds", round_id, "verdict.json")}


PERFORMANCE_KEYS = ("sharpe", "cagr", "max_dd", "max_drawdown", "net_pnl", "gross_pnl",
                    "ending_equity", "annualized", "turnover", "win_rate", "profit_factor",
                    "fees_paid", "funding_paid")


def _no_performance_claims(doc):
    """No performance number may exist anywhere in the round's artifacts."""
    found = []

    def walk(node, path=""):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, "%s.%s" % (path, k))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, "%s[%d]" % (path, i))
        elif isinstance(node, (int, float)) and not isinstance(node, bool):
            leaf = path.split(".")[-1].lower()
            if any(k in leaf for k in PERFORMANCE_KEYS):
                found.append({"path": path, "value": node})

    walk(doc)
    return found


def run_checks(results_root=DEFAULT_RESULTS, raw_root=DEFAULT_RAW, repo_root=DEFAULT_REPO,
               record_path=None, card_body_path=None, host=None, raw=None, stores=None,
               material=None):
    del CHECKS[:]
    record_path = record_path or DEFAULT_RECORD
    p = _round_paths(results_root)
    raw = raw if raw is not None else measure_raw(raw_root)
    material = material if material is not None else measure_non_canonical_tick_material()

    # ---------------------------------------------------------------- raw ----
    _check("C1", "canonical raw exists and is the registered single-venue USD-M perp store",
           raw.get("exists") and raw.get("dataset_families") == ["funding", "instruments",
                                                                "klines"]
           and not raw.get("second_venue_present")
           and raw.get("config", {}).get("venue") == "BINANCE"
           and raw.get("config", {}).get("market_type") == "usdm_perp"
           and raw.get("market_dirs") == ["binance"]
           and raw.get("venue_subdirs") == ["usdm"],
           {"dataset_families": raw.get("dataset_families"),
            "market_dirs": raw.get("market_dirs"),
            "venue_subdirs": raw.get("venue_subdirs"),
            "second_venue_present": raw.get("second_venue_present"),
            "config": {k: raw.get("config", {}).get(k) for k in ("venue", "market_type")}})

    _check("C2", "instrument surface is four CryptoPerpetual contracts with the "
                 "registered symbol set",
           raw.get("instrument_count") == 4
           and raw.get("instrument_types") == ["CryptoPerpetual"]
           and raw.get("instrument_ids") == ["BNBUSDT-PERP.BINANCE", "BTCUSDT-PERP.BINANCE",
                                             "ETHUSDT-PERP.BINANCE", "SOLUSDT-PERP.BINANCE"],
           {"instrument_ids": raw.get("instrument_ids"),
            "instrument_types": raw.get("instrument_types")})

    _check("C3", "instrument metadata carries price_increment/maker/taker fees and "
                 "NO tick/trade-stream field beyond the declared null `tick_scheme`",
           raw.get("instrument_fields_requirement_shaped") == ["tick_scheme"]
           and raw.get("instrument_fees") == ["maker=0.0002 taker=0.0005"],
           {"requirement_shaped_fields": raw.get("instrument_fields_requirement_shaped"),
            "fees": raw.get("instrument_fees"),
            "price_increments": raw.get("instrument_price_increments")})

    _check("C4", "k-line surface: 28 datasets, 7 intervals per symbol, one key set "
                 "(OHLCV only) asserted on every one of the 1,596 files",
           raw.get("kline_dataset_count") == 28
           and raw.get("kline_file_count") == 1596
           and raw.get("kline_intervals") == ["15m", "1d", "1h", "1w", "30m", "4h", "5m"]
           and raw.get("kline_intervals_identical_across_symbols")
           and raw.get("kline_symbols") == ["BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
           and not raw.get("kline_key_violations")
           and raw.get("kline_sampled_files") == 1596,
           {"datasets": raw.get("kline_dataset_count"),
            "files": raw.get("kline_file_count"),
            "intervals": raw.get("kline_intervals"),
            "identical_across_symbols": raw.get("kline_intervals_identical_across_symbols"),
            "key_violations": raw.get("kline_key_violations"),
            "sampled_files": raw.get("kline_sampled_files")})

    _check("C4b", "no k-line row shape carries a trade-count / taker-buy / aggressor "
                  "field (the store's own documented coverage limit, re-measured live)",
           raw.get("decisive_group_series_hits", {}).get("trade_count_surface") == []
           and raw.get("decisive_group_series_hits", {}).get("tick_trade_stream") == []
           and raw.get("schema_coverage_limit_present") is True,
           {"trade_count_surface_series": raw.get("decisive_group_series_hits",
                                                  {}).get("trade_count_surface"),
            "tick_trade_stream_series": raw.get("decisive_group_series_hits",
                                                {}).get("tick_trade_stream"),
            "coverage_limit_sentence_present": raw.get("schema_coverage_limit_present")})

    _check("C5", "NO 1-minute dataset: not configured, not materialised, no entry name "
                 "carries a 1m token, finest resolved interval is 5m",
           raw.get("config_has_1m") is False
           and raw.get("sub_minute_or_minute_dir_present") is False
           and raw.get("interval_dirs_with_one_min_token") == []
           and raw.get("finest_resolved_interval") == "5m"
           and raw.get("finest_resolved_step_s") == 300,
           {"config_intervals": raw.get("config_intervals_sorted"),
            "config_has_1m": raw.get("config_has_1m"),
            "minute_dir_present": raw.get("sub_minute_or_minute_dir_present"),
            "entries_with_1m_token": raw.get("interval_dirs_with_one_min_token"),
            "finest_resolved_interval": raw.get("finest_resolved_interval")})

    _check("C6", "NO tick/trade dataset family: families are exactly klines/funding/"
                 "instruments and the store's schema declares no trade dataset",
           raw.get("dataset_families") == ["funding", "instruments", "klines"]
           and raw.get("schema_documents_tick_or_trade_dataset") is False
           and raw.get("schema_documents_minute_dataset") is False
           and raw.get("schema_dataset_names") == ["funding", "instruments", "klines"],
           {"dataset_families": raw.get("dataset_families"),
            "schema_dataset_names": raw.get("schema_dataset_names"),
            "schema_documents_tick_or_trade_dataset":
                raw.get("schema_documents_tick_or_trade_dataset"),
            "schema_documents_minute_dataset": raw.get("schema_documents_minute_dataset")})

    _check("C7", "funding surface present with exactly the nine funding fields on all four "
                 "symbols and both truth statuses recorded",
           sorted(raw.get("funding", {})) == ["BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
           and len(set(map(tuple, raw.get("funding_field_sets") or []))) == 1
           and (raw.get("funding_field_sets") or [[]])[0] == [
               "funding_price_source", "funding_rate", "funding_time_ms", "mark_price",
               "market_type", "rate_type", "symbol", "truth_status", "venue"]
           and set(raw.get("funding_truth_status") or {}) == {"official", "modeled_funding"},
           {"funding_field_sets": raw.get("funding_field_sets"),
            "truth_status": raw.get("funding_truth_status"),
            "rows": {k: v["rows"] for k, v in sorted((raw.get("funding") or {}).items())}})

    _check("C8", "measured window: BTCUSDT k-lines start 2022-01-01 (the record registers "
                 "2020-01-01) and extend past the record's 2025-12-31 end",
           (raw.get("btcusdt_5m_window") or [None])[0] == "2022-01-01"
           and (raw.get("btcusdt_daily_window") or [None])[0] == "2022-01-01",
           {"btcusdt_5m_window": raw.get("btcusdt_5m_window"),
            "btcusdt_1h_window": raw.get("btcusdt_1h_window"),
            "btcusdt_daily_window": [ (raw.get("windows", {}).get("BTCUSDT/1d") or {}
                                       ).get("first"),
                                      (raw.get("windows", {}).get("BTCUSDT/1d") or {}
                                       ).get("last")],
            "record_window": ["2020-01-01", "2025-12-31"]})

    _check("C9", "the registered price series exists only at 5m-and-coarser resolution; "
                 "the record's 1-minute and tick resolutions are not reachable by "
                 "aggregation",
           raw.get("finest_resolved_step_s") == 300 and raw.get("config_has_1m") is False,
           {"finest_resolved_step_s": raw.get("finest_resolved_step_s"),
            "note": "1-minute bars cannot be derived from 5-minute bars; the tick stream "
                    "cannot be derived from any bar at all"})

    # ------------------------------------------------------- token probes ----
    _check("C10", "no decisive token occurs in any raw ENTRY NAME",
           raw.get("decisive_name_hits") == [],
           {"decisive_tokens_tested": raw.get("decisive_zero_tokens_tested"),
            "decisive_name_hits": raw.get("decisive_name_hits")})

    _check("C11", "every decisive-token payload hit in the raw tree is the DECLARED "
                  "prose/capability occurrence; zero offending hits",
           raw.get("decisive_offending_hits") == []
           and raw.get("probe_hits_unexplained_payload") == []
           and raw.get("probe_hits_unexplained_name") == [],
           {"offending": raw.get("decisive_offending_hits"),
            "unexplained_payload": raw.get("probe_hits_unexplained_payload"),
            "unexplained_name": raw.get("probe_hits_unexplained_name"),
            "prose_audit": {k: {"hits": v["hits"], "allowed": v["allowed"]}
                            for k, v in (raw.get("decisive_prose_audit") or {}).items()
                            if v["hits"]}})

    _check("C12", "no raw row shape carries any decisive requirement group (all five "
                  "groups measured, each empty by construction — not vacuous)",
           sorted((raw.get("decisive_group_series_hits") or {}).keys())
           == sorted(DECISIVE_GROUPS.keys())
           and all(v == [] for v in (raw.get("decisive_group_series_hits") or {}).values())
           and bool(raw.get("row_shapes")),
           {"group_series_hits": raw.get("decisive_group_series_hits"),
            "groups_measured": sorted((raw.get("decisive_group_series_hits") or {}).keys()),
            "row_shapes_of_data_hits": raw.get("row_shapes")})

    _check("C13", "the store's own schema declares exactly the three dataset families and "
                  "records its coverage limit; the 1m token in its prose is a path "
                  "pattern, not a dataset",
           raw.get("schema_interval_alphabet_line_present") is True
           and raw.get("schema_coverage_limit_present") is True
           and raw.get("schema_documents_minute_dataset") is False
           and raw.get("config_has_1m") is False,
           {"schema_dataset_names": raw.get("schema_dataset_names"),
            "interval_alphabet_line_present": raw.get("schema_interval_alphabet_line_present"),
            "coverage_limit_present": raw.get("schema_coverage_limit_present")})

    # -------------------------------------------------------------- host -----
    if stores is not None:
        _check("C14", "host-wide scan: every host hit resolves to a declared class "
                      "(0 unclassified)",
               stores.get("unclassified_host_hits") == [],
               {"unclassified": stores.get("unclassified_host_hits"),
                "totals": stores.get("totals"),
                "class_hits": {k: len(v) for k, v in
                               (stores.get("class_hits") or {}).items()}})

        allowed = set(HOST_ALLOWED_SERIES_HITS)
        decisive_series = set(stores.get("series_shaped_decisive") or [])
        _check("C15", "every series-shaped decisive host hit is a declared, hand-inspected "
                      "artifact (none is a tick or 1-minute store)",
               decisive_series <= allowed,
               {"decisive_series_shaped": sorted(decisive_series),
                "declared": sorted(allowed),
                "undeclared": sorted(decisive_series - allowed)})

        _check("C16", "the only series-shaped object carrying a trade row shape on the "
                      "host is the declared single-instant capture",
               (material.get("aggtrade_rows") or 0) == 20
               and material.get("aggtrade_row_keys") == ["T", "a", "f", "l", "m", "nq",
                                                         "p", "q"]
               and material.get("single_instant") is True,
               {"aggtrade_rows": material.get("aggtrade_rows"),
                "aggtrade_row_keys": material.get("aggtrade_row_keys"),
                "span_ms": material.get("aggtrade_span_ms"),
                "single_instant": material.get("single_instant")})

    _check("C17", "the non-canonical tick/L2 captures are measured, disclosed and NOT used",
           material.get("exists") is True and material.get("used") is False
           and all(f.get("exists") for f in (material.get("files") or {}).values()
                   if f) and len(material.get("files") or {}) == 3,
           {"files": {k: {"exists": v.get("exists"), "bytes": v.get("bytes")}
                      for k, v in sorted((material.get("files") or {}).items())},
            "capture_instants": material.get("capture_instants"),
            "capture_type": material.get("capture_type"),
            "depth_levels": material.get("depth_levels"),
            "used": material.get("used")})

    # --------------------------------------------------------- artifacts -----
    spec_path, verdict_path = p["spec"], p["verdict"]
    if not (os.path.exists(spec_path) and os.path.exists(verdict_path)):
        _check("C18", "round-spec.json and verdict.json exist", False,
               {"spec": spec_path, "verdict": verdict_path})
        return _result(raw, stores, material)

    spec = _load_json(spec_path)
    verdict = _load_json(verdict_path)

    _check("C18", "verdict.json states the contract-mandated terminal values",
           verdict.get("verdict") == "TECHNICAL_INCOMPLETE"
           and verdict.get("performance_claimable") is False
           and (verdict.get("failure") or {}).get("layer") == "card-local"
           and (verdict.get("failure") or {}).get("class") == "data_window_invalid"
           and (verdict.get("failure") or {}).get("last_run_id") is None
           and verdict.get("run_id") is None
           and (verdict.get("attempts") or {}).get("launched") == 0
           and (verdict.get("yield") or {}).get("yield_decision") == "STOP_TECHNICAL_INCOMPLETE"
           and verdict.get("survivors") == []
           and verdict.get("survivor_bundle") is None,
           {"verdict": verdict.get("verdict"),
            "performance_claimable": verdict.get("performance_claimable"),
            "failure": verdict.get("failure"),
            "run_id": verdict.get("run_id"),
            "attempts": verdict.get("attempts"),
            "yield": verdict.get("yield")})

    _check("C18b", "round-spec.json states the pre-registration identity and the "
                   "not-registered/not-computable status",
           spec.get("family_id") == FAMILY and spec.get("round_id") == ROUND
           and spec.get("kanban_task_id") == TASK and spec.get("kanban_board") == BOARD
           and spec.get("strategy_domain_status") == "not_registered"
           and spec.get("expected_status") == "not_computable"
           and spec.get("expected") == "PREREQUISITE_ABSENT"
           and (spec.get("prerequisite_gate") or {}).get("universe_shrunk") is False
           and (spec.get("prerequisite_gate") or {}).get("substitute_market_used") is False,
           {"family_id": spec.get("family_id"), "round_id": spec.get("round_id"),
            "task_id": spec.get("kanban_task_id"),
            "strategy_domain_status": spec.get("strategy_domain_status"),
            "expected_status": spec.get("expected_status"),
            "expected": spec.get("expected")})

    claimed = _claimed_of(spec)
    live = {"dataset_families": raw.get("dataset_families"),
            "kline_dataset_count": raw.get("kline_dataset_count"),
            "kline_file_count": raw.get("kline_file_count"),
            "kline_intervals": raw.get("kline_intervals"),
            "kline_symbols": raw.get("kline_symbols"),
            "finest_resolved_interval": raw.get("finest_resolved_interval"),
            "config_intervals": raw.get("config_intervals_sorted"),
            "config_has_1m": raw.get("config_has_1m"),
            "instrument_count": raw.get("instrument_count"),
            "instrument_ids": raw.get("instrument_ids"),
            "funding_field_sets": raw.get("funding_field_sets"),
            "funding_truth_status": raw.get("funding_truth_status"),
            "schema_dataset_names": raw.get("schema_dataset_names"),
            "btcusdt_5m_window": raw.get("btcusdt_5m_window"),
            "decisive_offending_hits": raw.get("decisive_offending_hits"),
            "decisive_group_series_hits": raw.get("decisive_group_series_hits"),
            "probe_tokens_tested": raw.get("probe_tokens_tested"),
            "schema_coverage_limit_present": raw.get("schema_coverage_limit_present")}
    diffs = {k: {"claimed": claimed.get(k), "live": v} for k, v in live.items()
             if claimed.get(k) != v}
    _check("C18c", "the round-spec's claimed measurement block re-derives from the LIVE "
                   "raw measurement, key by key",
           bool(claimed) and not diffs,
           {"keys_claimed": sorted(claimed), "diffs": diffs})

    matrix = _matrix_of(spec)
    decisive_map = {r["item"]: r["status"] for r in matrix
                    if r["status"] in DECISIVE_REQUIRED_STATUSES}
    registered_map = (spec.get("prerequisite_gate") or {}).get("decisive_status_map") \
        or (spec.get("universe_registration") or {}).get("decisive_status_map") or {}
    _check("C19", "the FULL decisive requirement-status map is still the registered one "
                  "(item by item, not just a count)",
           bool(decisive_map) and decisive_map == registered_map,
           {"decisive_items": len(decisive_map),
            "mismatches": {k: {"registered": registered_map.get(k), "live": v}
                           for k, v in decisive_map.items()
                           if registered_map.get(k) != v},
            "missing_from_live": sorted(set(registered_map) - set(decisive_map))})

    fals = spec.get("falsification") or {}
    _check("C20", "the record's falsification battery is registered in FULL (4 items, "
                  "restored from the canonical record, none lowered, none removed) and "
                  "marked NOT_EXECUTED",
           fals.get("item_count") == 4
           and fals.get("no_threshold_lowering") is True
           and fals.get("no_item_removal") is True
           and str(fals.get("falsification_status", "")).startswith("NOT_EXECUTED")
           and "card_vs_record_disclosure" in fals
           and all(k in (spec.get("falsification") or {})
                   for k in ("record_falsification_verbatim",
                             "card_falsification_excerpt_verbatim")),
           {"item_count": fals.get("item_count"),
            "status": fals.get("falsification_status"),
            "no_threshold_lowering": fals.get("no_threshold_lowering"),
            "no_item_removal": fals.get("no_item_removal"),
            "disclosure_present": "card_vs_record_disclosure" in fals})

    _check("C21", "no performance number exists anywhere in either artifact",
           _no_performance_claims(spec) == [] and _no_performance_claims(verdict) == [],
           {"spec": _no_performance_claims(spec), "verdict": _no_performance_claims(verdict)})

    _check("C22", "the card's prerequisite clause and the system-owned lifecycle footer "
                  "are carried verbatim",
           bool(_clause(spec)) and bool(spec.get("lifecycle_footer_verbatim"))
           and "prerequisite" in _clause(spec),
           {"clause_chars": len(_clause(spec)),
            "footer_chars": len(spec.get("lifecycle_footer_verbatim", ""))})

    cov = spec.get("coverage") or {}
    grids = ["historical", "oos", "full", "fee_2x", "funding_2x", "entry_delay_1_bar",
             "slippage_2ticks", "no_funding", "no_funding_full", "cost_attrition_40bps"]
    _check("C23", "coverage/robustness registration is complete and computed nothing "
                  "(10 grids, 0 cells) and no result-based universe shrinking",
           cov.get("phase_grids") == grids and cov.get("cells_registered_total") == 0
           and cov.get("cells_computed") == 0
           and (spec.get("prerequisite_gate") or {}).get("universe_shrunk") is False
           and (spec.get("prerequisite_gate") or {}).get("substitute_market_used") is False,
           {"phase_grids": cov.get("phase_grids"),
            "cells_registered_total": cov.get("cells_registered_total"),
            "cells_computed": cov.get("cells_computed")})

    dca = spec.get("dca_domain") or {}
    axes = dca.get("axes") or {}
    _check("C24", "the DCA domain is still the complete four-axis product with the "
                  "registered provenance classes and the inherited USER_FIXED envelope",
           dca.get("configs_per_cohort_per_grid") == 48
           and axes.get("spacing_pct") == [0.01, 0.02, 0.03, 0.04]
           and axes.get("size_multiplier") == [1.0, 1.1]
           and axes.get("breakeven_tp_pct") == [0.01, 0.02, 0.03]
           and axes.get("invalidation_pct") == [0.05, 0.10]
           and dca.get("base_quote") == 1000
           and dca.get("base_quote_status") == "PROJECT_PRE_REGISTERED_CONSTANT"
           and set((dca.get("axes_status") or {}).values()) == {
               "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN"}
           and (spec.get("user_fixed_invariants") or {}).get("starting_equity_usdt") == 30000
           and (spec.get("user_fixed_invariants") or {}).get("leverage") == "10x"
           and (spec.get("user_fixed_invariants") or {}).get("tranches") == 12,
           {"configs": dca.get("configs_per_cohort_per_grid"), "axes": axes,
            "base_quote": dca.get("base_quote"),
            "base_quote_status": dca.get("base_quote_status")})

    # ------------------------------------------------------- round identity ---
    rd = p["round_dir"]
    contents = sorted(os.listdir(rd)) if os.path.isdir(rd) else []
    family_dir = p["family_dir"]
    stray = [n for n in (os.listdir(family_dir) if os.path.isdir(family_dir) else [])
             if n not in ("family.json", "rounds")]
    attempts_dir = os.path.join(family_dir, "rounds", ROUND, "attempts")
    _check("C25", "nothing was ever submitted: the round dir holds exactly the two "
                  "artifacts, no attempt dir, no run-spec, no sentinel, no stray files",
           contents == ["round-spec.json", "verdict.json"]
           and not os.path.exists(attempts_dir) and stray == []
           and not any(n.endswith(("-run-spec.json", "-DONE", "-INCOMPLETE", "-FAILED"))
                       for n in contents),
           {"round_dir_contents": contents, "attempts_dir_exists": os.path.exists(attempts_dir),
            "family_dir_stray": stray})

    fam = _load_json(p["family_json"]) if os.path.exists(p["family_json"]) else {}
    _check("C26", "family.json carries this family's identity and this card's id and the "
                  "round-spec agrees with its fingerprint",
           fam.get("family_id") == FAMILY and fam.get("kanban_task_id") == TASK
           and fam.get("kanban_board") == BOARD
           and spec.get("provenance", {}).get("semantic_fingerprint")
           == fam.get("semantic_fingerprint"),
           {"family_id": fam.get("family_id"), "task_id": fam.get("kanban_task_id"),
            "fingerprint_match": spec.get("provenance", {}).get("semantic_fingerprint")
            == fam.get("semantic_fingerprint")})

    _check("C27", "no fabrication: zero attempts / zero run-specs / zero sentinels / "
                  "null run_id are stated consistently in BOTH artifacts",
           (spec.get("launch") or {}).get("launched") is False
           and (spec.get("launch") or {}).get("attempts") == 0
           and (spec.get("launch") or {}).get("run_specs") == 0
           and (spec.get("launch") or {}).get("terminal_sentinels") == 0
           and (spec.get("launch") or {}).get("run_spec") is None
           and (verdict.get("failure") or {}).get("incomplete_reason", "").startswith(
               "prerequisite absent"),
           {"spec_launch": spec.get("launch"),
            "verdict_incomplete_reason": (verdict.get("failure") or {}).get(
                "incomplete_reason")})

    return _result(raw, stores, material)


def _result(raw, stores, material):
    failed = [c["id"] for c in CHECKS if not c["ok"]]
    res = {"checks": list(CHECKS), "ok": not failed, "failed": failed,
           "raw_summary": {
               "entries": raw.get("entry_count"),
               "kline_files": raw.get("kline_file_count"),
               "kline_datasets": raw.get("kline_dataset_count"),
               "intervals": raw.get("kline_intervals"),
               "finest_resolved_interval": raw.get("finest_resolved_interval"),
               "config_intervals": raw.get("config_intervals_sorted"),
               "config_has_1m": raw.get("config_has_1m"),
               "probe_payload_files": raw.get("probe_payload_files_scanned"),
               "decisive_tokens": raw.get("decisive_zero_tokens_tested"),
               "decisive_offending_hits": raw.get("decisive_offending_hits"),
           },
           "non_canonical_tick_material_summary": {
               "aggtrade_rows": material.get("aggtrade_rows"),
               "aggtrade_span_ms": material.get("aggtrade_span_ms"),
               "single_instant": material.get("single_instant"),
               "used": material.get("used"),
           }}
    if stores is not None:
        res["root_counts"] = {k: v.get("files_scanned")
                              for k, v in (stores.get("roots") or {}).items()}
        res["stores_summary"] = {
            "files_scanned": (stores.get("totals") or {}).get("files_scanned"),
            "token_files": (stores.get("totals") or {}).get("token_files"),
            "token_files_total": stores.get("token_files_total"),
            "class_counts": stores.get("class_counts"),
            "generic_class_counts": stores.get("generic_class_counts"),
            "series_shaped_hit_count": len(stores.get("series_shaped_hits") or []),
            "series_shaped_decisive": stores.get("series_shaped_decisive"),
            "unclassified_host_hits": stores.get("unclassified_host_hits"),
            "decisive_groups": stores.get("decisive_groups"),
            "row_shapes": stores.get("row_shapes"),
        }
    return res


# ------------------------------------------------------- verbatim ------------
def _resolve_source_texts(repo_root=None, card_body_path=None, record_path=None,
                          board_db=BOARD_DB):
    repo_root = repo_root or DEFAULT_REPO
    card = None
    if card_body_path and os.path.exists(card_body_path):
        card = open(card_body_path, encoding="utf-8", errors="replace").read()
    if card is None and os.path.exists(board_db):
        con = sqlite3.connect("file:%s?mode=ro" % board_db, uri=True)
        try:
            row = con.execute("select body from tasks where id=?", (TASK,)).fetchone()
            card = row[0] if row else ""
        finally:
            con.close()
    record = open(record_path or DEFAULT_RECORD, encoding="utf-8",
                  errors="replace").read()
    contract = open(os.path.join(repo_root,
                                 "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md"),
                    encoding="utf-8", errors="replace").read()
    sys.path.insert(0, os.path.join(repo_root, "runtime"))
    import production_handoff  # noqa: E402
    return {"card": card or "", "record": record, "contract": contract,
            "footer": production_handoff.LIFECYCLE_FOOTER}


def _walk_verbatim(node, path=()):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _walk_verbatim(v, path + (str(k),))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk_verbatim(v, path + ("[%d]" % i,))
    elif isinstance(node, str) and path and "verbatim" in path[-1].lower():
        yield ".".join(path), node


def verify_verbatim(spec_path, source_map=None, repo_root=None, card_body_path=None,
                    record_path=None, board_db=BOARD_DB):
    spec = _load_json(spec_path)
    src = _resolve_source_texts(repo_root=repo_root, card_body_path=card_body_path,
                               record_path=record_path, board_db=board_db)
    source_map = source_map if source_map is not None else spec.get("excerpt_source_map") or {}
    problems, checked, misses, orphans = [], 0, [], []
    leaves = list(_walk_verbatim(spec))
    for path, value in leaves:
        entry = source_map.get(path)
        if entry is None and "." in path:
            entry = source_map.get(path.split(".")[-1])
            if entry is not None:
                # nested duplicate claim: only legitimate when byte-identical
                peer = source_map.get(path.split(".")[-1])
                if peer and peer.get("sha256") != _sha256_text(value):
                    entry = None
        if entry is None:
            misses.append(path)
            continue
        checked += 1
        text = src.get(entry.get("source"))
        if text is None:
            problems.append({"path": path, "why": "unknown source %r" % entry.get("source")})
            continue
        if value not in text:
            problems.append({"path": path, "why": "not a substring of %s" % entry.get("source"),
                             "head": value[:80]})
        if entry.get("sha256") and entry["sha256"] != _sha256_text(value):
            problems.append({"path": path, "why": "digest mismatch"})
    # declared excerpts that never became a leaf are reported, not silently dropped
    used = {p.split(".")[-1] for p, _ in leaves}
    declared = {k for k in source_map if k not in used}
    for k in sorted(declared):
        if k not in {p for p, _ in leaves}:
            orphans.append(k)
    return {"ok": not problems and not misses, "leaves": len(leaves), "checked": checked,
            "problems": problems, "misses": misses, "declared_not_leaf": orphans,
            "card_source": "board_db" if not card_body_path else "card_body_file",
            "sources": {k: len(v) for k, v in src.items()}}


# ------------------------------------------------------- self-test -----------
def _copy_family(src_results, dst_results, family=FAMILY, round_id=ROUND):
    src = os.path.join(src_results, family)
    dst = os.path.join(dst_results, family)
    shutil.copytree(src, dst)
    return os.path.join(dst, "rounds", round_id)


def _mutate(path, fn):
    doc = _load_json(path)
    fn(doc)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
        fh.write("\n")


def self_test(results_root=DEFAULT_RESULTS, raw=None, stores=None, material=None,
              repo_root=DEFAULT_REPO):
    """Build tampered copies; each must be refused by the check it targets."""
    variants = [
        ("V1", "verdict value -> PASS",
         lambda d: d.update({"verdict": "PASS"}), "C18"),
        ("V2", "performance_claimable -> true",
         lambda d: d.update({"performance_claimable": True}), "C18"),
        ("V3", "failure.layer -> shared-layer",
         lambda d: d["failure"].update({"layer": "shared-layer"}), "C18"),
        ("V4", "failure.class -> script_bug",
         lambda d: d["failure"].update({"class": "script_bug"}), "C18"),
        ("V5", "failure.last_run_id -> 7",
         lambda d: d["failure"].update({"last_run_id": 7}), "C18"),
        ("V6", "yield decision -> STOP_REJECT",
         lambda d: d["yield"].update({"yield_decision": "STOP_REJECT"}), "C18"),
        ("V7", "attempts.launched -> 1",
         lambda d: d["attempts"].update({"launched": 1}), "C18"),
        ("V8", "a performance number is fabricated (net_pnl)",
         lambda d: d.update({"net_pnl": 1234.0}), "C21"),
        ("V9", "a Sharpe appears inside a nested block",
         lambda d: d["yield"].update({"sharpe": 1.5}), "C21"),
        ("V10", "strategy_domain_status -> registered",
         lambda d: d.update({"strategy_domain_status": "registered"}), "C18b"),
        ("V11", "expected -> PASS",
         lambda d: d.update({"expected": "PASS"}), "C18b"),
        ("V12", "family_id rewritten",
         lambda d: d.update({"family_id": "some-other-family"}), "C18b"),
        ("V13", "kanban_task_id rewritten",
         lambda d: d.update({"kanban_task_id": "t_deadbeef"}), "C18b"),
        ("V14", "universe_shrunk -> true",
         lambda d: d["prerequisite_gate"].update({"universe_shrunk": True}), "C18b"),
        ("V15", "DCA configs -> 47",
         lambda d: d["dca_domain"].update({"configs_per_cohort_per_grid": 47}), "C24"),
        ("V16", "a DCA axis value is dropped",
         lambda d: d["dca_domain"]["axes"].update({"spacing_pct": [0.01, 0.02, 0.03]}), "C24"),
        ("V17", "base_quote relabelled as a USER_FIXED invariant",
         lambda d: d["dca_domain"].update({"base_quote_status": "USER_FIXED"}), "C24"),
        ("V18", "a searched axis relabelled as USER_FIXED",
         lambda d: d["dca_domain"]["axes_status"].update({"spacing_pct": "USER_FIXED"}),
         "C24"),
        ("V19", "coverage cells_registered_total -> 1000",
         lambda d: d["coverage"].update({"cells_registered_total": 1000}), "C23"),
        ("V20", "a registered phase grid is dropped",
         lambda d: d["coverage"].update({"phase_grids": d["coverage"]["phase_grids"][:-1]}),
         "C23"),
        ("V21", "falsification item count -> 3 (an item is dropped)",
         lambda d: d["falsification"].update({"item_count": 3}), "C20"),
        ("V22", "falsification relabelled as executed",
         lambda d: d["falsification"].update({"falsification_status": "EXECUTED"}), "C20"),
        ("V23", "the card-vs-record falsification disclosure is removed",
         lambda d: d["falsification"].pop("card_vs_record_disclosure", None), "C20"),
        ("V24", "a decisive matrix row is upgraded to PRESENT",
         lambda d: _flip_first_decisive(d), "C19"),
        ("V25", "the registered decisive status map is emptied (both copies)",
         lambda d: (d["prerequisite_gate"].update({"decisive_status_map": {}}),
                    d["universe_registration"].update({"decisive_status_map": {}})), "C19"),
        ("V26", "a claimed measurement value drifts (interval set gains 1m)",
         lambda d: _claimed_block(d).update({"kline_intervals": ["1m", "5m"]}), "C18c"),
        ("V27", "a claimed measurement value drifts (dataset families)",
         lambda d: _claimed_block(d).update(
             {"dataset_families": ["funding", "instruments", "klines", "trades"]}), "C18c"),
        ("V28", "a claimed measurement value drifts (funding fields)",
         lambda d: _claimed_block(d).update(
             {"funding_field_sets": [["funding_time_ms"]]}), "C18c"),
        ("V29", "a claimed measurement value drifts (instrument count)",
         lambda d: _claimed_block(d).update({"instrument_count": 5}), "C18c"),
        ("V30", "the prerequisite clause is emptied (every copy removed)",
         lambda d: _drop_clause(d), "C22"),
        ("V31", "the lifecycle footer is removed",
         lambda d: d.pop("lifecycle_footer_verbatim", None), "C22"),
        ("V32", "launch relabelled as launched",
         lambda d: d["launch"].update({"launched": True, "attempts": 1}), "C27"),
        ("B1", "BENIGN: keys reordered, content identical",
         lambda d: None, None),
    ]
    raw = raw if raw is not None else measure_raw()
    material = material if material is not None else measure_non_canonical_tick_material()
    results = []
    for vid, desc, fn, target in variants:
        tmp = tempfile.mkdtemp(prefix="infobars-selftest-%s-" % vid)
        try:
            try:
                round_dir = _copy_family(results_root, tmp)
            except FileNotFoundError as exc:
                results.append({"variant": vid, "desc": desc, "target": target,
                                "error": "copy failed: %s" % exc})
                continue
            vpath = os.path.join(round_dir, "verdict.json")
            spath = os.path.join(round_dir, "round-spec.json")
            if vid == "B1":
                # rewrite with reordered keys only
                doc = _load_json(vpath)
                with open(vpath, "w", encoding="utf-8") as fh:
                    json.dump({k: doc[k] for k in sorted(doc)}, fh, ensure_ascii=False,
                              indent=1)
                    fh.write("\n")
            elif vid in ("V26", "V27", "V28", "V29", "V10", "V11", "V12", "V13", "V14",
                         "V15", "V16", "V17", "V18", "V19", "V20", "V21", "V22", "V23",
                         "V24", "V25", "V30", "V31", "V32"):
                _mutate(spath, fn)
            else:
                _mutate(vpath, fn)
            res = run_checks(results_root=tmp, raw=raw, repo_root=repo_root,
                             stores=stores, material=material)
            failed = {c["id"] for c in res["checks"] if not c["ok"]}
            if target is None:
                results.append({"variant": vid, "desc": desc, "target": None,
                                "failed_checks": sorted(failed),
                                "ok": not failed})
            else:
                results.append({"variant": vid, "desc": desc, "target": target,
                                "failed_checks": sorted(failed),
                                "ok": target in failed})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return {"variants": results,
            "ok": all(r.get("ok") for r in results),
            "flipped": [r["variant"] for r in results if r.get("ok") and r.get("target")],
            "not_flipped": [r["variant"] for r in results
                            if not r.get("ok") and r.get("target")],
            "benign_control_failed": [r["variant"] for r in results
                                      if r.get("target") is None and not r.get("ok")]}


def _matrix_of(spec):
    """The registered required-data matrix (authored under universe_registration)."""
    return ((spec.get("universe_registration") or {}).get("required_data_matrix")
            or (spec.get("prerequisite_gate") or {}).get("required_data_matrix") or [])


def _clause(spec):
    """The card's prerequisite clause, wherever the author placed it."""
    return ((spec.get("registered_requirement") or {}).get(
        "card_prerequisite_clause_verbatim")
        or (spec.get("prerequisite_gate") or {}).get("card_prerequisite_clause_verbatim")
        or spec.get("card_prerequisite_clause_verbatim") or "")


def _claimed_of(spec):
    return ((spec.get("universe_registration") or {}).get(
        "required_data_matrix_claimed_measurement")
        or spec.get("required_data_matrix_claimed_measurement") or {})


def _claimed_block(spec):
    """The mutable claimed-measurement block, wherever the author placed it
    (self-test variants drift a value inside it)."""
    if isinstance((spec.get("universe_registration") or {}).get(
            "required_data_matrix_claimed_measurement"), dict):
        return spec["universe_registration"]["required_data_matrix_claimed_measurement"]
    spec.setdefault("required_data_matrix_claimed_measurement", {})
    return spec["required_data_matrix_claimed_measurement"]


def _clause_block(spec):
    """The mutable home of the card's prerequisite clause (self-test variant V30)."""
    for holder in ((spec.get("registered_requirement") or {}),
                   (spec.get("prerequisite_gate") or {}), spec):
        if "card_prerequisite_clause_verbatim" in holder:
            return holder
    spec.setdefault("prerequisite_gate", {}).setdefault(
        "card_prerequisite_clause_verbatim", "")
    return spec["prerequisite_gate"]


def _drop_clause(spec):
    """Remove EVERY copy of the prerequisite clause (the honest 'it is gone' variant)."""
    for holder in ((spec.get("registered_requirement") or {}),
                   (spec.get("prerequisite_gate") or {}), spec):
        holder.pop("card_prerequisite_clause_verbatim", None)
    # leave an empty copy behind so the mutation cannot be mistaken for a missing key
    spec.setdefault("prerequisite_gate", {})["card_prerequisite_clause_verbatim"] = ""


def _flip_first_decisive(spec):
    matrix = _matrix_of(spec)
    for row in matrix:
        if row["status"] in DECISIVE_REQUIRED_STATUSES:
            row["status"] = "PRESENT"
            return
    raise SystemExit("no decisive matrix row to flip (fixture misconfigured)")


# --------------------------------------------------- raw fixture control ------
def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def raw_fixture_control(results_root=DEFAULT_RESULTS, repo_root=DEFAULT_REPO,
                        stores=None, material=None):
    """Build a temp raw tree carrying the record's streams; raw-side checks must FAIL."""
    tmp = tempfile.mkdtemp(prefix="infobars-fixture-")
    try:
        raw = os.path.join(tmp, "market-data-raw")
        # config declares 1m
        _write(os.path.join(raw, "_meta/CONFIG.json"), json.dumps(
            {"schema": "market-data-raw/config/v1", "venue": "BINANCE",
             "market_type": "usdm_perp",
             "symbols": ["BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"],
             "intervals": ["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"]}, indent=1))
        # schema declares an aggTrade dataset
        _write(os.path.join(raw, "_meta/SCHEMA.md"),
               "# fixture\n\n## Dataset: aggTrade trades (Binance USD-M)\n\n"
               "one row per aggregate trade\n\n## Dataset: klines (1m)\n\n"
               "one row per 1-minute bar\n")
        # a real aggTrade dataset, in BOTH the venue-native short-key shape and a
        # normalized named shape (so a schema/probe can see the tick stream)
        rows = [{"a": 1, "p": "78988.50", "q": "0.235", "f": 10, "l": 12,
                 "T": 1787926963174, "m": False},
                {"a": 2, "p": "78988.40", "q": "0.009", "f": 13, "l": 13,
                 "T": 1787926963246, "m": True}]
        _write(os.path.join(raw, "binance/usdm/trades/BTCUSDT/BTCUSDT-trades-2026-08.jsonl"),
               "\n".join(json.dumps(r) for r in rows) + "\n")
        norm = [{"agg_trade_id": 1, "price": "78988.50", "quantity": "0.235",
                 "timestamp_ms": 1787926963174, "is_buyer_maker": False,
                 "symbol": "BTCUSDT"},
                {"agg_trade_id": 2, "price": "78988.40", "quantity": "0.009",
                 "timestamp_ms": 1787926963246, "is_buyer_maker": True,
                 "symbol": "BTCUSDT"}]
        _write(os.path.join(raw, "binance/usdm/trades/BTCUSDT/"
                                 "BTCUSDT-trades-normalized-2026-08.jsonl"),
               "\n".join(json.dumps(r) for r in norm) + "\n")
        # 1-minute klines
        k = [{"open_time_ms": 1787926920000, "close_time_ms": 1787926979999,
              "open": "78980.0", "high": "78990.0", "low": "78975.0", "close": "78988.5",
              "volume": "12.5"}]
        _write(os.path.join(raw, "binance/usdm/klines/BTCUSDT/1m/BTCUSDT-1m-2026-08.jsonl"),
               "\n".join(json.dumps(r) for r in k) + "\n")
        # instrument export with a trade-shaped field
        _write(os.path.join(raw, "binance/usdm/instruments/usdm-perp-instruments.json"),
               json.dumps({"instruments": [
                   {"fields": {"id": "BTCUSDT-PERP.BINANCE", "type": "CryptoPerpetual",
                               "price_increment": "0.10", "maker_fee": "0.0002",
                               "taker_fee": "0.0005", "buyer_maker": True},
                    "python_type": "CryptoPerpetual"}]}, indent=1))
        res = run_checks(results_root=results_root, raw_root=raw, repo_root=repo_root,
                         stores=stores, material=material)
        failed = {c["id"] for c in res["checks"] if not c["ok"]}
        raw_side = {"C1", "C2", "C3", "C4", "C4b", "C5", "C6", "C7", "C8", "C9", "C10",
                    "C11", "C12", "C13"}
        expected = {"C3", "C4", "C4b", "C5", "C6", "C9", "C10", "C11", "C12", "C13"}
        # C18c measures the CLAIMED block against the live raw, so it must also fail on a
        # fixture raw; every other artifact-side check must stay green.
        artifact_side = {"C17", "C18", "C18b", "C19", "C20", "C21", "C22", "C23", "C24",
                         "C25", "C26", "C27"}
        violations = sorted((failed & artifact_side) | (failed - raw_side - {"C18c"}))
        return {"ok": expected <= failed and not violations,
                "flipped": sorted(failed & raw_side),
                "expected_subset": sorted(expected),
                "not_flipped": sorted(expected - failed),
                "artifact_side_failed": sorted(failed & artifact_side),
                "artifact_side_unaffected": violations,
                "C18c_flips_by_design": "C18c" in failed,
                "fixture_root": tmp}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ----------------------------------------------------------------- main ------
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--measure-only", action="store_true")
    ap.add_argument("--host-scan", action="store_true")
    ap.add_argument("--verify-verbatim", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--raw-fixture-control", action="store_true")
    ap.add_argument("--excluded-token-pass", action="store_true")
    ap.add_argument("--results-root", default=DEFAULT_RESULTS)
    ap.add_argument("--raw-root", default=DEFAULT_RAW)
    ap.add_argument("--repo-root", default=DEFAULT_REPO)
    ap.add_argument("--card-body", default=None)
    args = ap.parse_args(argv)

    if args.measure_only:
        print(json.dumps({"raw": measure_raw(args.raw_root),
                          "non_canonical_tick_material":
                              measure_non_canonical_tick_material(),
                          "host": measure_host_stores() if args.host_scan else "skipped"},
                         ensure_ascii=False, indent=1))
        return 0

    if args.verify_verbatim:
        p = _round_paths(args.results_root)
        print(json.dumps(verify_verbatim(p["spec"], repo_root=args.repo_root,
                                        card_body_path=args.card_body),
                         ensure_ascii=False, indent=1))
        return 0

    stores = measure_host_stores() if args.host_scan else None
    raw = measure_raw(args.raw_root)
    material = measure_non_canonical_tick_material()

    if args.self_test:
        print(json.dumps(self_test(results_root=args.results_root, raw=raw,
                                   stores=stores, material=material,
                                   repo_root=args.repo_root),
                         ensure_ascii=False, indent=1))
        return 0

    if args.raw_fixture_control:
        print(json.dumps(raw_fixture_control(results_root=args.results_root,
                                            repo_root=args.repo_root, stores=stores,
                                            material=material),
                         ensure_ascii=False, indent=1))
        return 0

    if args.excluded_token_pass:
        print(json.dumps(excluded_token_pass(args.raw_root), ensure_ascii=False, indent=1))
        return 0

    res = run_checks(results_root=args.results_root, raw_root=args.raw_root,
                     repo_root=args.repo_root, card_body_path=args.card_body,
                     raw=raw, stores=stores, material=material)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        for c in res["checks"]:
            print("%-5s %-4s %s" % (c["id"], "PASS" if c["ok"] else "FAIL", c["name"]))
            if not c["ok"]:
                print("      %s" % json.dumps(c["detail"], ensure_ascii=False)[:600])
        print("\n%d/%d checks PASS" % (sum(1 for c in res["checks"] if c["ok"]),
                                       len(res["checks"])))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
