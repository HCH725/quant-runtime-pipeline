#!/usr/bin/env python3
"""Prerequisite-gate read-back checker for the family

    commodity-futures-hierarchical-graph-learning-calendar-spread-2026-09-02

Card t_35e7bbbc terminalises this family as TECHNICAL_INCOMPLETE because the
record's required data / market is not in the canonical raw:

  * the record's object is a **CME Group commodity-futures calendar-spread
    strategy**: it trades long/short legs across different MATURITIES of the
    same commodity (energy: crude oil / natural gas / heating oil / gasoline;
    metals: gold / silver / copper / platinum; agriculture: corn / soybeans /
    wheat / soybean oil / soybean meal; livestock) on CME, CBOT, NYMEX, COMEX
    and eCBOT, and its input is a **term structure of dated contracts** -
    daily settlement prices ($F_{tcd}$) and an explicit contract
    maturity/expiration schedule ($T_{cd}$) with time-to-maturity up to
    tau_max = 52 weeks;
  * the record's own portability paragraph is explicit that this is only
    *adapted / unproven* for crypto, and that an adapted application would need
    **dated delivery futures** (perpetual + 3M/6M delivery curves) or a dated
    term-structure venue - the local store has neither;
  * the canonical raw holds **one venue (BINANCE) of USD-M perpetuals and four
    fixed contracts** (BTCUSDT/ETHUSDT/BNBUSDT/SOLUSDT) and exactly three
    dataset families - OHLCV k-lines at 5m/15m/30m/1h/4h/1d/1w, funding rows
    and instrument metadata. There is no dated-futures curve, no contract
    maturity/expiration schedule, no exchange settlement price and no second
    venue: the four instruments are perpetuals with no maturity at all, so a
    calendar spread (a spread ACROSS maturities of one commodity) cannot even
    be indexed;
  * the card forbids substituting an approximate dataset, a proxy market or a
    rewritten hypothesis, and forbids shrinking the universe to manufacture
    executability. A missing prerequisite is not a rejection: it is a measured
    technical terminal.

This script exists so an independent reader can re-derive that determination
from the live filesystem instead of trusting prose:

  * C1-C9 re-measure the canonical raw structurally: store identity, the
    instrument surface (and the fact that every local instrument is a
    perpetual), the k-line surface (payload key set asserted on every file,
    interval directories measured live, gaps/duplicates), the *finest resolved
    interval*, the funding surface and the measured windows;
  * C10-C14 probe the raw tree for this record's requirement vocabulary
    (CME venues, commodity underlyings, dated-curve surface) by entry name AND
    payload content, assert every hit is classified, assert the decisive
    groups are carried by NO raw row shape (k-line row keys, funding row keys,
    instrument field names), and audit the declared prose occurrences;
  * C15-C17 re-measure the host's other stores for the same vocabulary -
    every series-shaped hit is named by a rule and enumerated, prose/source
    hits fall back to declared generic classes;
  * C18-C27 re-read the round's immutable artifacts and assert they state
    exactly the contract-mandated terminal values (verdict, layer, class, yield
    decision, zero attempts, null run_id), that nothing was ever submitted
    (no run-spec, no attempt directory, no terminal sentinel), that the
    decisive requirement matrix still carries the registered statuses item by
    item, that the DCA registration keeps the contract 7.2 v3.1 provenance
    classes plus the complete 48-cell product, that the falsification battery
    is unchanged and restored to the record's full item count, and that no
    performance number was fabricated;
  * `--verify-verbatim` resolves every `*_verbatim` leaf of the persisted
    round-spec against its declared source (card / record / contract / footer)
    and re-checks the excerpt digest map, independent of the authoring run;
  * `--excluded-token-pass` re-probes the generic tokens the decisive
    vocabulary deliberately leaves out (`roll`, `contract`, `futures`, `price`,
    `volume`, `month`, `open`), so an exclusion cannot hide a hit;
  * `--self-test` builds tampered copies in fresh temp directories and asserts
    the named check refuses each variant (non-vacuousness control), including
    one benign control that must NOT flip;
  * `--raw-fixture-control` builds a temp raw tree that carries what this
    record would need (a CME commodity daily settlement file with an explicit
    contract month / maturity schedule and several dated expiries per
    commodity, plus a config that declares that venue) and asserts the
    raw-side checks flip to FAIL;
  * `--host-scan` re-runs the house-wide search.

Read-only: it never writes inside the results tree or the raw tree.

Usage:
    python3 runtime/commodity_futures_hierarchical_graph_learning_calendar_spread_prerequisite_check.py [--json]
    python3 runtime/commodity_futures_hierarchical_graph_learning_calendar_spread_prerequisite_check.py --measure-only
    python3 runtime/commodity_futures_hierarchical_graph_learning_calendar_spread_prerequisite_check.py --verify-verbatim
    python3 runtime/commodity_futures_hierarchical_graph_learning_calendar_spread_prerequisite_check.py --self-test
    python3 runtime/commodity_futures_hierarchical_graph_learning_calendar_spread_prerequisite_check.py --raw-fixture-control
    python3 runtime/commodity_futures_hierarchical_graph_learning_calendar_spread_prerequisite_check.py --excluded-token-pass
    python3 runtime/commodity_futures_hierarchical_graph_learning_calendar_spread_prerequisite_check.py --host-scan

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
from datetime import datetime, timezone

FAMILY = "commodity-futures-hierarchical-graph-learning-calendar-spread-2026-09-02"
ROUND = FAMILY + "-r1"
TASK = "t_35e7bbbc"
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
    # CME Group venues (the record's Universe: "CME, CBOT, NYMEX, COMEX, eCBOT")
    "cme": r"\bcme\b",
    "cbot": r"\bcbot\b",
    "nymex": r"\bnymex\b",
    "comex": r"\bcomex\b",
    "ecbot": r"\becbot\b",
    "globex": r"\bglobex\b",
    # commodity underlyings the record trades: energy
    "crude": r"\bcrude\b|\bwti\b",
    "natural_gas": r"\bnatural[-_ ]?gas\b",
    "heating_oil": r"\bheating[-_ ]?oil\b",
    "gasoline": r"\bgasoline\b|\brbob\b",
    # metals
    "gold": r"\bgold\b",
    "silver": r"\bsilver\b",
    "copper": r"\bcopper\b",
    "platinum": r"\bplatinum\b",
    # agriculture
    "corn": r"\bcorn\b",
    "soybean": r"\bsoybeans?\b|\bsoy[-_ ]?bean",
    "wheat": r"\bwheat\b",
    "soy_oil": r"\bsoy(?:bean)?[-_ ]?oil\b",
    "soy_meal": r"\bsoy(?:bean)?[-_ ]?meal\b",
    # livestock
    "livestock": r"\blivestock\b|\bcattle\b|\bhogs?\b",
    # dated-futures / term-structure surface (the record's object is a spread
    # across maturities of ONE commodity; without dated contracts there is no
    # curve to spread)
    "settlement": r"\bsettlements?\b",
    "expiration": r"\bexpir(?:ation|y|ies)\b",
    "maturity": r"\bmaturit(?:y|ies)\b",
    "delivery": r"\bdeliver(?:y|ies|able)\b",
    "contract_month": r"\bcontract[-_ ]?months?\b",
    "front_month": r"\bfront[-_ ]?months?\b",
    "back_month": r"\bback[-_ ]?months?\b",
    "futures_curve": r"\bfutures?[-_ ]?curves?\b",
    "calendar_spread": r"\bcalendar[-_ ]?spreads?\b",
    "convenience_yield": r"\bconvenience[-_ ]?yield\b",
    "storage_cost": r"\bstorage[-_ ]?costs?\b",
    "open_interest": r"\bopen[-_ ]?interest\b",
    "dated_futures": r"\bdated[-_ ]?futures?\b|\bdelivery[-_ ]?futures?\b",
    "quarterly_futures": r"\bquarterly[-_ ]?futures?\b",
    "tenor": r"\btenors?\b",
    "coin_m": r"\bcoin[-_ ]?m\b",
    "deribit": r"\bderibit\b",
    # vendor terminals the source paper's data came from
    "lseg": r"\blseg\b",
    "datastream": r"\bdatastream\b",
    "wrds": r"\bwrds\b",
    # generic-but-decisive-in-context: an exchange-listed FUTURES market always
    # says so somewhere in its own metadata
    "futures": r"\bfutures?\b",
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

    Python's ``\\b`` treats ``_`` as a word character, so ``\\bcontract[-_ ]?month\\b``
    would NOT match a column named ``contract_month`` - exactly the shape a real
    term-structure store would use. Replacing the leading/trailing ``\\b`` with
    lookarounds that treat ``_``, ``-`` and ``.`` as separators catches compound
    embeddings while still refusing ordinary English false positives.
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
# A single union pattern used ONLY as a fast pre-filter: a text that fails this
# search cannot match any token, so the per-token exact counts are skipped for it.
# Files that pass it are still counted token by token (exact, not union-based).
ANY_TOKEN = re.compile("|".join(COMPILED[k].pattern for k in COMPILED), re.I)

DECISIVE_GROUPS = {
    # an exchange GROUP the record trades on: if any of these venues were stored
    # locally the record would be runnable, so their absence is decisive
    "cme_group_venue": ["cme", "cbot", "nymex", "comex", "ecbot", "globex"],
    # the commodity underlyings the record trades (energy / metals / agri / livestock)
    "commodity_underlying": ["crude", "natural_gas", "heating_oil", "gasoline",
                             "gold", "silver", "copper", "platinum", "corn",
                             "soybean", "wheat", "soy_oil", "soy_meal", "livestock"],
    # the dated-curve surface the record's object is defined on
    "dated_curve_surface": ["settlement", "expiration", "maturity", "delivery",
                            "contract_month", "front_month", "back_month",
                            "futures_curve", "calendar_spread", "convenience_yield",
                            "storage_cost", "open_interest", "dated_futures",
                            "quarterly_futures", "tenor", "futures"],
    # alternative dated venues named by the record's portability paragraph
    "crypto_dated_venue": ["coin_m", "deribit"],
    # the vendor terminals the source paper's daily settlement panel came from
    "vendor_terminal": ["lseg", "datastream", "wrds"],
}
DECISIVE_ZERO_TOKENS = sorted({t for grp, toks in DECISIVE_GROUPS.items() for t in toks})

# Decisive tokens with DECLARED, hand-inspected occurrences inside the store's own
# documents or inside the store's own updater source. Every one of these is a
# record of the ABSENCE (a search that found nothing), a CAPABILITY declaration
# (code that could fetch something the store does not keep) or an unrelated
# same-word field of the perpetual store - never a dated-futures data surface.
# Every other occurrence of a decisive token anywhere in the raw tree is offending.
DECISIVE_PROSE_ALLOWED = {
    "open_interest": {
        "files": ["_meta/INVENTORY.md"],
        "why": "canonical_store_inventory_prose: line 5 quotes the store's own "
               "Phase-1 mdfind query list ('binance / klines / aggTrade / bookTicker "
               "/ openInterest / tardis / market-data') and line 50 records the "
               "result - 'No other Binance/klines/tardis/aggTrade store was found on "
               "the host or on /Volumes/ExpansionDrive' - i.e. a record that searched "
               "for and did NOT find a tick store; open interest is named only as a "
               "searched-for token",
    },
    "settlement": {
        "files": ["binance/usdm/instruments/usdm-perp-instruments.json"],
        "why": "canonical_store_instrument_metadata: the four occurrences are the "
               "instrument field `settlement_currency` (value \"USDT\") on the four "
               "PERPETUAL definitions - the currency a perpetual's PnL settles in, "
               "not an exchange daily settlement PRICE of a dated contract",
    },
    "futures": {
        "files": ["_meta/INVENTORY.md", "_meta/SCHEMA.md",
                  "_meta/CONFIG.json", "_tools/market_data_sync.py",
                  "_tools/binance_public.py", "_tools/README.md",
                  "_meta/INSTRUMENTS_EXPORT.json", "_meta/FUNDING_EXPORT.json",
                  "_meta/TRANSCODE_MANIFEST.json", "_meta/VERIFY_GAPS.txt",
                  "_meta/VERIFY_TRANSCODE.txt", "_meta/STATE.json",
                  "binance/usdm/instruments/usdm-perp-instruments.json"],
        "why": "store_provenance_prose_and_source: the word occurs only in the "
               "store's own description of itself and in its updater source, always "
               "as 'USD-M perpetual futures' / the venue's futures market type - the "
               "local market IS a futures market (perpetual futures), so the word "
               "cannot discriminate a dated curve; no dated contract, curve, expiry "
               "or settlement panel is named anywhere in these files",
    },
}
DECISIVE_PROSE_ALLOWED_FILES = {
    t: set(spec["files"]) for t, spec in DECISIVE_PROSE_ALLOWED.items()
}

# Decisive tokens that also appear as a DECLARED row-field NAME of the perpetual
# store. A row shape carrying one of these pairs is disclosed, not offending; any
# OTHER decisive token inside a row shape is offending and fails the check.
DECISIVE_ROW_FIELD_ALLOWED = {
    "settlement": {
        "settlement_currency":
            "canonical_store_instrument_field: the four PERPETUAL definitions' "
            "`settlement_currency` (value \"USDT\") names the currency a perpetual's PnL "
            "settles in - not an exchange daily settlement PRICE of a dated contract",
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
# ordinary English / code word whose hits cannot discriminate a dated-futures
# store from unrelated text. `--excluded-token-pass` re-probes them so the
# exclusion cannot hide a hit.
EXCLUDED_TOKENS = {
    "roll": r"(?<![A-Za-z0-9])roll(?:s|ing|over|ed)?(?![A-Za-z0-9])",
    "contract": r"(?<![A-Za-z0-9])contracts?(?![A-Za-z0-9])",
    "curve": r"(?<![A-Za-z0-9])curves?(?![A-Za-z0-9])",
    "spread": r"(?<![A-Za-z0-9])spreads?(?![A-Za-z0-9])",
    "price": r"(?<![A-Za-z0-9])prices?(?![A-Za-z0-9])",
    "volume": r"(?<![A-Za-z0-9])volumes?(?![A-Za-z0-9])",
    "month": r"(?<![A-Za-z0-9])months?(?![A-Za-z0-9])",
    "oil": r"(?<![A-Za-z0-9])oils?(?![A-Za-z0-9])",
    "gas": r"(?<![A-Za-z0-9])gas(?:es)?(?![A-Za-z0-9])",
    "metal": r"(?<![A-Za-z0-9])metals?(?![A-Za-z0-9])",
    "venue": r"(?<![A-Za-z0-9])venues?(?![A-Za-z0-9])",
    "exchange": r"(?<![A-Za-z0-9])exchanges?(?![A-Za-z0-9])",
    "energy": r"(?<![A-Za-z0-9])energy(?![A-Za-z0-9])",
}

# The store's own documented coverage limit, asserted as an exact substring so the
# boolean cannot be satisfied by a sentence that documents the OPPOSITE.
SCHEMA_COVERAGE_LIMIT_SENTENCE = "trade count and taker-buy splits are **not** present"
SCHEMA_INTERVAL_ALPHABET_LINE = "* Bar interval = `interval` in the path (`1m 5m 15m 30m 1h 4h 1d 1w`)."
SCHEMA_FUTURES_LINE = "## Dataset: klines (Binance USD-M perpetual futures, UTC)"

# Every payload/entry hit inside the raw tree must resolve to a class here (the value
# is the class name; the prose lives in DECISIVE_PROSE_ALLOWED / the notes below).
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

# Classes that describe a ROW-ORIENTED data surface (a real store) as opposed to
# prose, source code or configuration. A decisive token landing in one of these is
# offending: it would mean the raw tree carries the record's market after all.
SERIES_CLASSES = frozenset({
    "canonical_store_kline_rows",
    "canonical_store_funding_rows",
    "canonical_store_instrument_export",
    "host_series_rows",
})

# ------------------------------------------------------------------ host -------
# Roots re-scanned by --host-scan: every place a dated-futures/commodity store
# could plausibly live on this host (the retired engine stacks, the research
# phase directories, the results/handoff tree and the wiki records).
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
    "/Users/hong/workspace/microsoft-quant-stack",
    "/Volumes/ExpansionDrive/daily-crypto-brief",
    "/Volumes/ExpansionDrive/qlib-results",
    "/Users/hong/.hermes/wiki/quant",
]
HOST_TEXT_EXTS = (".csv", ".jsonl", ".json", ".txt", ".md", ".tsv", ".py", ".sh",
                  ".yaml", ".yml", ".jsonl.gz", ".html", ".log")
HOST_ROW_EXTS = (".csv", ".tsv", ".jsonl", ".jsonl.gz", ".parquet", ".db", ".sqlite")
HOST_SKIP_DIRS = frozenset({".git", "__pycache__", "node_modules", ".venv", "venv",
                            ".pytest_cache", "__MACOSX", "site-packages",
                            ".mypy_cache", ".ruff_cache", ".kanban-scratch"})

# Classes for the host-side scan. Every hit resolves through this ordered rule chain
# and the generic fallbacks at the end: a SERIES-shaped hit (a real row-oriented file)
# must be named by a specific rule, while prose/source hits fall back to the declared
# generic classes, which is truthful - a markdown corpus or a Python file cannot be a
# market-data store.
HOST_CLASS_RULES = [
    (r"^/Users/hong/workspace/microsoft-quant-stack/", "retired_qlib_china_a_share_stack"),
    (r"^/Users/hong/workspace/qlib-apple-container/scripts/", "qlib_container_harness_source"),
    (r"^/Users/hong/workspace/qlib-apple-container/", "qlib_container_host_tree"),
    (r"^/Users/hong/workspace/quant-runtime-pipeline/runtime/", "quant_pipeline_runtime_source"),
    (r"^/Users/hong/workspace/quant-runtime-pipeline/container/", "quant_pipeline_container_source"),
    (r"^/Users/hong/workspace/quant-runtime-pipeline/evidence/", "quant_pipeline_evidence"),
    (r"^/Users/hong/workspace/quant-runtime-pipeline/homepage/", "quant_pipeline_dashboard_source"),
    (r"^/Users/hong/workspace/quant-runtime-pipeline/", "quant_pipeline_repo_docs"),
    (r"^/Volumes/ExpansionDrive/qlib-results/_handoff/", "results_handoff_bodies"),
    (r"^/Volumes/ExpansionDrive/qlib-results/_incidents/", "results_incidents"),
    (r"^/Volumes/ExpansionDrive/qlib-results/", "results_round_artifacts"),
    (r"^/Users/hong/\.hermes/wiki/quant/", "wiki_brain_quant_record"),
    (r"^/Volumes/ExpansionDrive/daily-crypto-brief/", "daily_brief_artifacts"),
    (r"^/Users/hong/workspace/phase12-l2-l3-execution-tca/raw/",
     "non_canonical_single_instant_capture"),
    (r"^/Users/hong/workspace/phase12-l2-l3-execution-tca/source_cache/",
     "vendor_documentation"),
    (r"^/Users/hong/workspace/phase12-l2-l3-execution-tca/", "execution_tca_harness"),
    (r"^/Users/hong/workspace/phase5-crypto-derivatives/raw/", "vendor_documentation"),
    (r"^/Users/hong/workspace/phase7-alpha-research/source_cache/", "vendor_documentation"),
    (r"^/Users/hong/workspace/phase11-options-volatility/source_cache/", "vendor_documentation"),
    (r"^/Users/hong/workspace/phase11-options-volatility/", "options_volatility_harness"),
    (r"^/Users/hong/workspace/phase13-production-ops/", "production_ops_harness"),
    (r"^/Users/hong/workspace/phase10-pit-bitemporal/", "pit_bitemporal_store"),
    (r"^/Users/hong/workspace/phase4-market-microstructure/", "market_microstructure_harness"),
    (r"^/Users/hong/workspace/phase3-portfolio-risk/", "portfolio_risk_harness"),
    (r"^/Users/hong/workspace/phase9-cross-sectional-factors/", "cross_sectional_factor_harness"),
    (r"^/Users/hong/workspace/a1-[0-9-]+[^|]*/", "pit_membership_harness"),
    (r"^/Users/hong/workspace/a1-usdm-pit-lifecycle[^|]*/", "pit_lifecycle_harness"),
    (r"^/Users/hong/workspace/alpha-strategy-research/", "alpha_research_harness"),
    (r"^/Users/hong/workspace/btc-relative-entry-score/", "btc_relative_score_harness"),
    (r"^/Users/hong/workspace/quant-backtest-design-20260908/", "backtest_design_docs"),
    (r"^/Users/hong/workspace/kanban_t_793034aa_signal_transitions/", "signal_transition_harness"),
]

# Every one of the hits below is a single-instant or prose artifact that this round
# measured and deliberately did NOT use. Values are re-measured live, never trusted
# from this declaration.
NON_CANONICAL_NOTE = (
    "the host scan enumerates every series-shaped hit by its own rule; the round "
    "used none of them, and no hit is a CME / commodity-futures dated-curve store"
)


# --------------------------------------------------------------- helpers -------
def _sha256_text(text):
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def _read_text(path, cap=None):
    """Decode a text-or-gzip file; returns None when it cannot be decoded."""
    try:
        if path.endswith(".gz"):
            with gzip.open(path, "rb") as fh:
                data = fh.read(cap) if cap else fh.read()
        else:
            with open(path, "rb") as fh:
                data = fh.read(cap) if cap else fh.read()
    except Exception:
        return None
    return data.decode("utf-8", "replace")


def _load_json(path):
    with open(path, "rb") as fh:
        return json.loads(fh.read().decode("utf-8"))


def _iso(ms):
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _day(ms):
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def _step_seconds(interval):
    m = re.fullmatch(r"(\d+)([mhdw])", interval)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    return n * {"m": 60, "h": 3600, "d": 86400, "w": 604800}[unit]


def _walk(root, max_depth=PROBE_MAX_DEPTH):
    for dirpath, dirs, names in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        if depth >= max_depth:
            dirs[:] = []
        dirs[:] = [d for d in dirs if d not in HOST_SKIP_DIRS and not d.startswith(".")]
        yield dirpath, dirs, names


def _last_line(path):
    txt = _read_text(path)
    if not txt:
        return None
    for line in reversed(txt.splitlines()):
        if line.strip():
            return line
    return None


def _classify(hits, classes, root=None):
    """Resolve every hit path to a class; unresolved paths are returned."""
    unexplained = []
    for path in hits:
        rel = os.path.relpath(path, root) if root else path
        cls = None
        for key, name in classes.items():
            if key.endswith("/"):
                if rel.startswith(key):
                    cls = name
                    break
            elif rel == key:
                cls = name
                break
        if cls is None and "*" in classes:
            for key, name in classes["*"].items():
                if key == rel:
                    cls = name
                    break
                if key.endswith("/") and rel.startswith(key):
                    cls = name
                    break
        if cls is None:
            unexplained.append(rel)
    return unexplained


# ------------------------------------------------------------------ raw --------
def measure_raw(raw=DEFAULT_RAW, depth=PROBE_MAX_DEPTH):
    """Re-measure the canonical raw store structurally (read-only)."""
    out = {"raw_root": raw, "exists": os.path.isdir(raw)}
    if not out["exists"]:
        return out

    entries = sorted(x for x in os.listdir(raw) if not x.startswith("."))
    out["entry_count"] = len(entries)
    out["top_level"] = entries

    # ---- layout surface (a tree that does not carry the declared layout is still
    #      measurable: the probe below runs over whatever is there)
    kl_root = os.path.join(raw, "binance/usdm/klines")
    fu_root = os.path.join(raw, "binance/usdm/funding")
    ins_path = os.path.join(raw, "binance/usdm/instruments/usdm-perp-instruments.json")
    cfg_path = os.path.join(raw, "_meta/CONFIG.json")
    usdm_root = os.path.join(raw, "binance/usdm")

    out["declared_layout_present"] = os.path.isdir(usdm_root)
    out["config"] = _load_json(cfg_path) if os.path.isfile(cfg_path) else {}
    out["config_intervals_sorted"] = sorted(out["config"].get("intervals") or [])
    out["dataset_families"] = sorted(
        x for x in os.listdir(usdm_root) if not x.startswith(".")) \
        if os.path.isdir(usdm_root) else []
    out["second_venue_present"] = any(
        x != "usdm" for x in os.listdir(os.path.join(raw, "binance"))
        if not x.startswith(".")) if os.path.isdir(os.path.join(raw, "binance")) else False

    # ---- instruments
    if os.path.isfile(ins_path):
        inst = _load_json(ins_path)
        items = inst.get("instruments") or []
    else:
        items = []
    fields = [it.get("fields", {}) for it in items]
    out["instrument_count"] = len(items)
    out["instrument_ids"] = sorted(f.get("id") for f in fields)
    out["instrument_types"] = sorted({f.get("type") for f in fields})
    out["instrument_fees"] = {f.get("raw_symbol"): {"maker": f.get("maker_fee"),
                                                    "taker": f.get("taker_fee")}
                              for f in fields}
    out["instrument_price_increments"] = {f.get("raw_symbol"): f.get("price_increment")
                                          for f in fields}
    out["instrument_settlement_currencies"] = sorted(
        {f.get("settlement_currency") for f in fields})
    out["instrument_field_keys"] = sorted({k for f in fields for k in f})
    out["instrument_is_inverse"] = sorted({bool(f.get("is_inverse")) for f in fields})
    out["instrument_has_maturity_field"] = sorted(
        k for k in out["instrument_field_keys"]
        if re.search(r"maturit|expir|deliver|contract[-_ ]?month|tenor", k, re.I))

    # ---- klines
    syms = sorted(x for x in os.listdir(kl_root) if not x.startswith(".")) \
        if os.path.isdir(kl_root) else []
    out["kline_symbols"] = syms
    intervals_seen = set()
    interval_dirs = {}
    dataset_count = 0
    file_count = 0
    row_keys_union = set()
    row_keys_by_ext = {}
    row_keys_uniform = True
    windows = {}
    row_totals = {}
    contiguity = {}
    for sym in syms:
        sp = os.path.join(kl_root, sym)
        if not os.path.isdir(sp):
            continue
        ivs = sorted(x for x in os.listdir(sp) if not x.startswith("."))
        interval_dirs[sym] = ivs
        for iv in ivs:
            intervals_seen.add(iv)
            ip = os.path.join(sp, iv)
            if not os.path.isdir(ip):
                continue
            files = sorted(x for x in os.listdir(ip) if not x.startswith("."))
            dataset_count += 1
            file_count += len(files)
            rows = 0
            keys_of_dataset = set()
            first_ms = last_ms = None
            for k, fn in enumerate(files):
                fp = os.path.join(ip, fn)
                txt = _read_text(fp)
                if txt is None:
                    continue
                for line in txt.splitlines():
                    if not line.strip():
                        continue
                    rec = json.loads(line)
                    rows += 1
                    keys = frozenset(rec)
                    keys_of_dataset.add(keys)
                    row_keys_union |= keys
                    if first_ms is None:
                        first_ms = rec.get("open_time_ms")
                    last_ms = rec.get("open_time_ms")
                ext = ".jsonl.gz" if fn.endswith(".gz") else os.path.splitext(fn)[1]
                row_keys_by_ext.setdefault(ext, set()).add(
                    frozenset(json.loads(txt.splitlines()[0])))
            if len(keys_of_dataset) != 1:
                row_keys_uniform = False
            key = "%s/%s" % (sym, iv)
            row_totals[key] = rows
            windows[key] = {"first_ms": first_ms, "last_ms": last_ms,
                            "first": _iso(first_ms), "last": _iso(last_ms),
                            "first_day": _day(first_ms), "last_day": _day(last_ms)}
            step = _step_seconds(iv)
            if first_ms is not None and step:
                expected = (last_ms - first_ms) // (step * 1000) + 1
                contiguity[key] = {"rows": rows, "expected": expected,
                                   "equal": rows == expected}
    out["kline_intervals"] = sorted(intervals_seen)
    out["kline_interval_dirs"] = interval_dirs
    out["kline_dataset_count"] = dataset_count
    out["kline_file_count"] = file_count
    out["kline_row_keys_union"] = sorted(row_keys_union)
    out["kline_row_keys_by_ext"] = {k: sorted(map(sorted, v))
                                    for k, v in row_keys_by_ext.items()}
    out["kline_row_keys_uniform"] = row_keys_uniform
    out["kline_row_totals"] = row_totals
    out["kline_windows"] = windows
    out["kline_contiguity_all_equal"] = all(v["equal"] for v in contiguity.values())
    out["kline_contiguity_failures"] = sorted(k for k, v in contiguity.items()
                                              if not v["equal"])
    steps = [(iv, _step_seconds(iv)) for iv in out["kline_intervals"]]
    steps = [(iv, s) for iv, s in steps if s]
    if steps:
        finest = min(steps, key=lambda t: t[1])
        out["finest_resolved_interval"] = finest[0]
        out["finest_resolved_step_s"] = finest[1]
    else:
        out["finest_resolved_interval"] = None
        out["finest_resolved_step_s"] = None
    out["btcusdt_5m_window"] = [windows.get("BTCUSDT/5m", {}).get("first_day"),
                                windows.get("BTCUSDT/5m", {}).get("last_day")]
    out["btcusdt_daily_window"] = [windows.get("BTCUSDT/1d", {}).get("first_day"),
                                   windows.get("BTCUSDT/1d", {}).get("last_day")]
    if windows.get("BTCUSDT/1d", {}).get("first_ms") is not None:
        d0 = windows["BTCUSDT/1d"]["first_ms"]
        d1 = windows["BTCUSDT/1d"]["last_ms"]
        out["btcusdt_daily_span_days"] = (d1 - d0) // 86400000 + 1
    else:
        out["btcusdt_daily_span_days"] = None

    # ---- funding
    funding = {}
    truth = {}
    fund_keys = set()
    fu_syms = sorted(x for x in os.listdir(fu_root) if not x.startswith(".")) \
        if os.path.isdir(fu_root) else []
    for sym in fu_syms:
        p = os.path.join(fu_root, sym)
        if not os.path.isdir(p):
            continue
        files = sorted(x for x in os.listdir(p) if not x.startswith("."))
        rows = 0
        first_ms = last_ms = None
        for fn in files:
            txt = _read_text(os.path.join(p, fn))
            if txt is None:
                continue
            for line in txt.splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                rows += 1
                fund_keys |= set(rec)
                t = rec.get("truth_status")
                truth[t] = truth.get(t, 0) + 1
                if first_ms is None:
                    first_ms = rec.get("funding_time_ms")
                last_ms = rec.get("funding_time_ms")
        funding[sym] = {"files": len(files), "rows": rows,
                        "first_ms": first_ms, "last_ms": last_ms,
                        "first": _iso(first_ms), "last": _iso(last_ms)}
    out["funding"] = funding
    out["funding_rows_total"] = sum(v["rows"] for v in funding.values())
    out["funding_truth_status"] = truth
    out["funding_field_sets"] = sorted(fund_keys)

    # ---- schema / inventory prose
    schema = _read_text(os.path.join(raw, "_meta/SCHEMA.md")) or ""
    inventory = _read_text(os.path.join(raw, "_meta/INVENTORY.md")) or ""
    out["schema_dataset_names"] = sorted(set(re.findall(r"^## Dataset: (\S+)", schema, re.M)))
    out["schema_coverage_limit_present"] = SCHEMA_COVERAGE_LIMIT_SENTENCE in schema
    out["schema_interval_alphabet_line_present"] = SCHEMA_INTERVAL_ALPHABET_LINE in schema
    out["schema_futures_line_present"] = SCHEMA_FUTURES_LINE in schema
    out["inventory_absence_sentence_present"] = (
        "No other Binance/klines/tardis/aggTrade store was found on the host or on" in inventory)

    # ---- vocabulary probe (entry names + payload content)
    name_hits = {t: [] for t in PATTERNS}
    payload_hits = {t: 0 for t in PATTERNS}
    payload_files = {t: [] for t in PATTERNS}
    payload_scanned = 0
    text_exts = (".json", ".jsonl", ".md", ".txt", ".py", ".sh", ".yaml", ".yml",
                 ".csv", ".tsv", ".html", ".log")
    for dirpath, dirs, names in _walk(raw):
        for fn in names:
            if fn.startswith("."):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, raw)
            for tok, rx in COMPILED.items():
                if rx.search(rel):
                    name_hits[tok].append(rel)
            if fn.endswith(text_exts) or fn.endswith(".gz"):
                txt = _read_text(full, cap=4 * 1024 * 1024)
                if txt is None:
                    continue
                payload_scanned += 1
                if not ANY_TOKEN.search(txt):
                    continue
                for tok, rx in COMPILED.items():
                    c = len(rx.findall(txt))
                    if c:
                        payload_hits[tok] += c
                        payload_files[tok].append({"path": rel, "count": c})

    def _raw_class(rel):
        table = RAW_CLASSES["*"]
        if rel in table:
            return table[rel]
        for key, name in table.items():
            if key.endswith("/") and rel.startswith(key):
                return name
        return None

    name_unexplained = []
    name_hits_by_class = {}
    for tok, rels in sorted(name_hits.items()):
        for rel in rels:
            cls = _raw_class(rel)
            if cls is None:
                name_unexplained.append({"token": tok, "path": rel})
            else:
                name_hits_by_class.setdefault(cls, []).append({"token": tok, "path": rel})
    payload_unexplained = []
    payload_hits_by_class = {}
    decisive_offending = []
    decisive_in_unclassified = []
    decisive_prose_audit = {}
    for tok, entries in sorted(payload_files.items()):
        allowed = DECISIVE_PROSE_ALLOWED_FILES.get(tok)
        for e in entries:
            cls = _raw_class(e["path"])
            if cls is None:
                payload_unexplained.append({"token": tok, "path": e["path"]})
                if tok in DECISIVE_ZERO_TOKENS:
                    decisive_in_unclassified.append({"token": tok, "path": e["path"],
                                                     "count": e["count"]})
                continue
            payload_hits_by_class.setdefault(cls, []).append(
                {"token": tok, "path": e["path"], "count": e["count"]})
            if tok in DECISIVE_ZERO_TOKENS:
                if allowed and e["path"] in allowed:
                    decisive_prose_audit.setdefault(tok, []).append(e["path"])
                else:
                    decisive_offending.append(
                        {"token": tok, "path": e["path"], "class": cls,
                         "why": "decisive token outside its declared prose allowlist"})
    out["probe_tokens_tested"] = len(PATTERNS)
    out["probe_payload_files_scanned"] = payload_scanned
    out["entry_count_all"] = sum(1 for _ in _walk(raw) for _n in [_])
    out["probe_entry_name_hits"] = {t: sorted(v) for t, v in name_hits.items() if v}
    out["probe_payload_hit_counts"] = {t: c for t, c in payload_hits.items() if c}
    out["probe_hits_by_class"] = {k: v for k, v in payload_hits_by_class.items()}
    out["probe_hits_unexplained_name"] = name_unexplained
    out["probe_hits_unexplained_payload"] = payload_unexplained
    out["decisive_zero_tokens_tested"] = len(DECISIVE_ZERO_TOKENS)
    out["decisive_zero_hits"] = sorted(t for t in DECISIVE_ZERO_TOKENS
                                       if not payload_hits.get(t) and not name_hits.get(t))
    out["decisive_name_hits"] = sorted(
        t for t in DECISIVE_ZERO_TOKENS if name_hits.get(t))
    out["decisive_prose_audit"] = {t: sorted(v) for t, v in decisive_prose_audit.items()}
    out["decisive_prose_audit_why"] = {t: DECISIVE_PROSE_ALLOWED[t]["why"]
                                       for t in sorted(decisive_prose_audit)}
    out["decisive_offending_hits"] = decisive_offending
    out["decisive_hits_in_unclassified"] = decisive_in_unclassified

    # ---- is any decisive token carried by a raw ROW SHAPE?
    row_shape_keys = set(out["kline_row_keys_union"]) | set(out["funding_field_sets"]) \
        | set(out["instrument_field_keys"])
    carried = []
    declared_carried = []
    for tok in DECISIVE_ZERO_TOKENS:
        rx = COMPILED[tok]
        allowed_fields = DECISIVE_ROW_FIELD_ALLOWED.get(tok) or {}
        for key in sorted(row_shape_keys):
            if rx.search(key):
                if key in allowed_fields:
                    declared_carried.append({"token": tok, "row_field": key,
                                             "why": allowed_fields[key]})
                else:
                    carried.append({"token": tok, "row_field": key})
    out["decisive_carried_by_row_shape"] = carried
    out["decisive_carried_by_row_shape_declared"] = declared_carried
    out["row_shape_keys_tested"] = sorted(row_shape_keys)
    out["decisive_group_series_hits"] = {
        grp: sorted(t for t in toks if payload_hits.get(t) or name_hits.get(t))
        for grp, toks in sorted(DECISIVE_GROUPS.items())}
    return out


# ----------------------------------------------------------------- host --------
def _host_class(path):
    """Resolve one host path to a class through the ordered rule chain.

    The rule table is keyed on the ABSOLUTE path, so a rule names a subtree of the
    filesystem directly and never depends on which scan root reached it.
    """
    p = path.replace(os.sep, "/")
    for pat, name in HOST_CLASS_RULES:
        if re.match(pat, p):
            return name
    return None


def measure_host_stores(roots=None, read_cap=2 * 1024 * 1024, progress=None):
    """Re-measure the host's other stores for this record's vocabulary (read-only).

    Every hit resolves to a class; a SERIES-shaped hit (a row-oriented data file)
    MUST be named by one of the specific rules, otherwise it is reported as
    unclassified and the check fails closed.
    """
    roots = list(roots or HOST_ROOTS)
    out = {
        "scan_roots": roots,
        "missing_roots": [],
        "files_scanned": 0,
        "text_files_scanned": 0,
        "row_files_scanned": 0,
        "hits_by_token": {},
        "hits_by_class": {},
        "unclassified": [],
        "series_hits": [],
        "series_hits_by_class": {},
        "series_hits_unclassified": [],
        "decisive_series_hits": [],
        "decisive_series_hits_by_class": {},
        "decisive_hits_by_token": {},
        "truncated_reads": 0,
    }
    for root in roots:
        if not os.path.isdir(root):
            out["missing_roots"].append(root)
            continue
        for dirpath, dirs, names in _walk(root, max_depth=64):
            for fn in names:
                if fn.startswith("."):
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, root)
                out["files_scanned"] += 1
                is_row = fn.endswith(HOST_ROW_EXTS)
                if is_row:
                    out["row_files_scanned"] += 1
                if not (fn.endswith(HOST_TEXT_EXTS) or fn.endswith(".gz")):
                    if is_row:
                        # a binary row file still counts: its NAME is probed
                        pass
                    else:
                        continue
                name_hits = [t for t, rx in COMPILED.items() if rx.search(fn)]
                body_hits = {}
                txt = None
                if fn.endswith(HOST_TEXT_EXTS) or fn.endswith(".gz"):
                    txt = _read_text(full, cap=read_cap)
                    if txt is not None and len(txt) >= read_cap - 1:
                        out["truncated_reads"] += 1
                    out["text_files_scanned"] += 1
                    if txt:
                        for tok, rx in COMPILED.items():
                            c = len(rx.findall(txt))
                            if c:
                                body_hits[tok] = c
                tokens = sorted(set(name_hits) | set(body_hits))
                if not tokens:
                    continue
                cls = _host_class(full)
                entry = {"path": full, "class": cls, "name_hits": name_hits,
                         "body_hits": body_hits, "row_shaped": is_row}
                for tok in tokens:
                    out["hits_by_token"].setdefault(tok, []).append(full)
                if cls is None:
                    if not is_row:
                        # declared generic fallback: prose/source/configuration text
                        # cannot be a market-data store, and the row-shaped hits (the
                        # only ones that could be) must be named by a specific rule
                        cls = "host_prose_or_source_text"
                    else:
                        out["unclassified"].append(entry)
                        cls = "UNCLASSIFIED"
                out["hits_by_class"].setdefault(cls, 0)
                out["hits_by_class"][cls] += 1
                decisive = [t for t in tokens if t in DECISIVE_ZERO_TOKENS]
                if decisive:
                    out["decisive_hits_by_token"].setdefault(tuple(decisive), []).append(full)
                if is_row:
                    out["series_hits"].append(entry)
                    out["series_hits_by_class"].setdefault(cls, []).append(full)
                    if cls == "UNCLASSIFIED":
                        out["series_hits_unclassified"].append(entry)
                    if decisive:
                        out["decisive_series_hits"].append(
                            {"path": full, "class": cls, "tokens": decisive,
                             "name_hits": name_hits, "body_hits": body_hits})
                        out["decisive_series_hits_by_class"].setdefault(cls, []).append(full)
            if progress:
                progress(dirpath)
    out["hits_by_token"] = {t: sorted(set(v)) for t, v in sorted(out["hits_by_token"].items())}
    out["series_hits_by_class"] = {k: sorted(set(v))
                                   for k, v in sorted(out["series_hits_by_class"].items())}
    out["decisive_series_hits_by_class"] = {
        k: sorted(set(v)) for k, v in sorted(out["decisive_series_hits_by_class"].items())}
    return out


# ------------------------------------------------------------- checks ---------
def _check(cid, name, ok, detail):
    return {"id": cid, "name": name, "ok": bool(ok), "detail": detail}


def _round_paths(results_root, family=FAMILY, round_id=ROUND):
    rd = os.path.join(results_root, family, "rounds", round_id)
    return {
        "family_dir": os.path.join(results_root, family),
        "round_dir": rd,
        "round_spec": os.path.join(rd, "round-spec.json"),
        "verdict": os.path.join(rd, "verdict.json"),
        "attempts_dir": os.path.join(rd, "attempts"),
        "survivor_bundle": os.path.join(rd, "survivor-bundle.json"),
    }


PERFORMANCE_KEY_HINTS = ("cagr", "annualized", "annualised", "sharpe", "drawdown",
                         "max_dd", "ending_equity", "net_pnl", "gross_pnl", "turnover",
                         "profit_factor", "win_rate", "expectancy", "sortino", "calmar")


def _no_performance_claims(doc, path=""):
    """Walk a document; return every key that claims a PERFORMANCE result."""
    found = []

    def walk(node, where):
        if isinstance(node, dict):
            for k, v in node.items():
                if not isinstance(v, (dict, list)) and isinstance(v, (int, float)) \
                        and not isinstance(v, bool) and v is not None:
                    kl = str(k).lower()
                    if any(h in kl for h in PERFORMANCE_KEY_HINTS):
                        found.append({"where": "%s.%s" % (where, k), "value": v})
                walk(v, "%s.%s" % (where, k))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, "%s[%d]" % (where, i))

    walk(doc, path)
    return found


NON_STORE_HOST_CLASSES = frozenset({
    "retired_qlib_china_a_share_stack",
    "qlib_container_harness_source",
    "qlib_container_host_tree",
    "quant_pipeline_runtime_source",
    "quant_pipeline_container_source",
    "quant_pipeline_evidence",
    "quant_pipeline_dashboard_source",
    "quant_pipeline_repo_docs",
    "results_handoff_bodies",
    "results_incidents",
    "results_round_artifacts",
    "wiki_brain_quant_record",
    "daily_brief_artifacts",
    "non_canonical_single_instant_capture",
    "vendor_documentation",
    "execution_tca_harness",
    "options_volatility_harness",
    "production_ops_harness",
    "pit_bitemporal_store",
    "market_microstructure_harness",
    "portfolio_risk_harness",
    "cross_sectional_factor_harness",
    "pit_membership_harness",
    "pit_lifecycle_harness",
    "alpha_research_harness",
    "btc_relative_score_harness",
    "backtest_design_docs",
    "signal_transition_harness",
    "host_series_rows",
    "host_prose_or_source_text",
    "UNCLASSIFIED",
})
# Classes that WOULD satisfy the record's requirement (a CME/commodity dated-curve
# store). Deliberately empty: no measured host class is a dated-curve price store.
STORE_HOST_CLASSES = frozenset()


def _section(text, start, end=None):
    i = text.find(start)
    if i < 0:
        raise SystemExit("SECTION MISS: %r" % start)
    j = len(text) if end is None else text.find(end, i + len(start))
    if j < 0:
        raise SystemExit("SECTION END MISS: %r" % end)
    return text[i:j]


def run_checks(results_root=DEFAULT_RESULTS, raw_root=DEFAULT_RAW, repo_root=DEFAULT_REPO,
               record_path=DEFAULT_RECORD, card_body_path=None, host=None, raw=None,
               skip_host=False):
    """Re-derive the whole terminal determination from the live filesystem."""
    checks = []
    raw = raw or measure_raw(raw_root)
    paths = _round_paths(results_root)
    rd = paths["round_dir"]
    rec_exists = os.path.isfile(record_path)
    rec = open(record_path, encoding="utf-8", errors="replace").read() if rec_exists else ""

    # ---- C1: the canonical raw is present, non-empty and single-rooted
    checks.append(_check(
        "C1", "canonical raw root present with the declared top-level entries",
        raw.get("exists") and raw.get("entry_count") == 3
        and sorted(raw.get("top_level") or []) == ["_meta", "_tools", "binance"],
        {"exists": raw.get("exists"), "entry_count": raw.get("entry_count"),
         "top_level": raw.get("top_level"), "expected": ["_meta", "_tools", "binance"]}))

    # ---- C2: store identity
    cfg = raw.get("config") or {}
    checks.append(_check(
        "C2", "store identity is one venue / one market type / four fixed symbols",
        cfg.get("venue") == "BINANCE" and cfg.get("market_type") == "usdm_perp"
        and cfg.get("symbols") == ["BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"],
        {"venue": cfg.get("venue"), "market_type": cfg.get("market_type"),
         "symbols": cfg.get("symbols")}))

    # ---- C3: k-line surface integrity
    checks.append(_check(
        "C3", "k-line surface: 28 datasets, 1596 files, uniform row shape, no gaps",
        raw.get("kline_dataset_count") == 28 and raw.get("kline_file_count") == 1596
        and raw.get("kline_row_keys_uniform") is True
        and raw.get("kline_contiguity_all_equal") is True
        and raw.get("kline_row_keys_union") == ["close", "close_time_ms", "high", "low",
                                                "open", "open_time_ms", "volume"],
        {"datasets": raw.get("kline_dataset_count"), "files": raw.get("kline_file_count"),
         "row_keys": raw.get("kline_row_keys_union"),
         "row_keys_uniform": raw.get("kline_row_keys_uniform"),
         "contiguity_all_equal": raw.get("kline_contiguity_all_equal"),
         "contiguity_failures": raw.get("kline_contiguity_failures")}))

    # ---- C4: the interval surface - no 1-minute, finest resolved 5m
    checks.append(_check(
        "C4", "interval surface carries 5m-and-coarser only (finest resolved 5m)",
        raw.get("finest_resolved_interval") == "5m"
        and raw.get("finest_resolved_step_s") == 300
        and raw.get("kline_intervals") == ["15m", "1d", "1h", "1w", "30m", "4h", "5m"]
        and "1m" not in (raw.get("config_intervals_sorted") or []),
        {"finest": raw.get("finest_resolved_interval"),
         "step_s": raw.get("finest_resolved_step_s"),
         "interval_dirs": raw.get("kline_intervals"),
         "config_intervals": raw.get("config_intervals_sorted")}))

    # ---- C5: funding surface
    truth = raw.get("funding_truth_status") or {}
    checks.append(_check(
        "C5", "funding surface: 4 streams, 20k+ rows, truth_status split disclosed",
        len(raw.get("funding") or {}) == 4
        and (raw.get("funding_rows_total") or 0) >= 20000
        and set(truth.keys()) <= {"official", "modeled_funding"}
        and truth.get("official", 0) > 0,
        {"streams": sorted((raw.get("funding") or {}).keys()),
         "rows_total": raw.get("funding_rows_total"),
         "truth_status": truth,
         "note": "the store's own schema documents that `truth_status` separates exchange "
                 "rows (`official`) from reconstructed ones (`modeled_funding`); consumers "
                 "that need exchange truth must filter on `official` - disclosed, and this "
                 "round applied no funding figure at all"}))

    # ---- C6: instrument surface - every local instrument is a PERPETUAL
    checks.append(_check(
        "C6", "instrument surface: four linear USDT perpetuals, no maturity field",
        raw.get("instrument_count") == 4
        and raw.get("instrument_types") == ["CryptoPerpetual"]
        and raw.get("instrument_is_inverse") == [False]
        and raw.get("instrument_settlement_currencies") == ["USDT"]
        and raw.get("instrument_has_maturity_field") == [],
        {"count": raw.get("instrument_count"), "types": raw.get("instrument_types"),
         "is_inverse": raw.get("instrument_is_inverse"),
         "settlement_currencies": raw.get("instrument_settlement_currencies"),
         "maturity_like_fields": raw.get("instrument_has_maturity_field")}))

    # ---- C7: one venue, three dataset families
    checks.append(_check(
        "C7", "single venue / three dataset families: klines + funding + instruments",
        raw.get("dataset_families") == ["funding", "instruments", "klines"]
        and raw.get("second_venue_present") is False,
        {"dataset_families": raw.get("dataset_families"),
         "second_venue_present": raw.get("second_venue_present")}))

    # ---- C8: measured windows and the record's own window
    win = raw.get("btcusdt_daily_window") or [None, None]
    checks.append(_check(
        "C8", "measured local windows cover the declared raw window and the record's era end",
        win[0] == "2022-01-01" and win[1] is not None and win[1] >= "2026-09-10"
        and (raw.get("btcusdt_daily_span_days") or 0) >= 1714
        and (raw.get("btcusdt_5m_window") or [None])[0] == "2022-01-01",
        {"daily_window": win, "daily_span_days": raw.get("btcusdt_daily_span_days"),
         "five_minute_window": raw.get("btcusdt_5m_window"),
         "record_window": ["1977-08-01", "2025-12-31"],
         "note": "the store is appended monthly, so the measured end advances; the check "
                 "therefore asserts the START and the declared minimum coverage rather than "
                 "a frozen end date. The local window is far shorter than the record's panel "
                 "and covers a different market - disclosed, not used to explain the gate "
                 "away"}))

    # ---- C9: every probe hit is classified, and no decisive token survives
    unexplained = (raw.get("probe_hits_unexplained_name") or []) \
        + (raw.get("probe_hits_unexplained_payload") or [])
    checks.append(_check(
        "C9", "every raw-tree probe hit resolves to a declared class",
        not unexplained,
        {"probe_tokens_tested": raw.get("probe_tokens_tested"),
         "payload_files_scanned": raw.get("probe_payload_files_scanned"),
         "unexplained": unexplained[:20]}))

    # ---- C10: the decisive vocabulary is carried by NO raw row shape
    declared_carried = raw.get("decisive_carried_by_row_shape_declared") or []
    checks.append(_check(
        "C10", "no decisive market token is carried by name, payload or row shape "
               "(declared metadata field names excepted, item by item)",
        not (raw.get("decisive_offending_hits") or [])
        and not (raw.get("decisive_carried_by_row_shape") or [])
        and not (raw.get("decisive_name_hits") or [])
        and not (raw.get("decisive_hits_in_unclassified") or [])
        and sorted(d["row_field"] for d in declared_carried) == ["settlement_currency"],
        {"offending": raw.get("decisive_offending_hits"),
         "carried_by_row_shape": raw.get("decisive_carried_by_row_shape"),
         "carried_by_row_shape_declared": declared_carried,
         "decisive_hits_in_unclassified": raw.get("decisive_hits_in_unclassified"),
         "decisive_name_hits": raw.get("decisive_name_hits"),
         "decisive_tokens_tested": raw.get("decisive_zero_tokens_tested"),
         "decisive_tokens_with_zero_hits": len(raw.get("decisive_zero_hits") or []),
         "declared_prose_audit": raw.get("decisive_prose_audit"),
         "row_shape_keys_tested": len(raw.get("row_shape_keys_tested") or [])}))

    # ---- C11: the probe is non-vacuous - the local control tokens ARE present
    checks.append(_check(
        "C11", "local control tokens are present (the probe can see the live store)",
        (raw.get("probe_payload_hit_counts") or {}).get("klines", 0) > 0
        and (raw.get("probe_payload_hit_counts") or {}).get("perpetual", 0) > 0
        and (raw.get("probe_payload_hit_counts") or {}).get("funding", 0) > 0,
        {t: (raw.get("probe_payload_hit_counts") or {}).get(t)
         for t in ("klines", "perpetual", "funding")}))

    # ---- C12: host-side store scan
    if host is None and not skip_host:
        host = measure_host_stores()
    if host is None:
        checks.append(_check("C12", "host store scan", False,
                             {"error": "host scan not provided"}))
    else:
        decisive_series = host.get("decisive_series_hits") or []
        unclassified = host.get("series_hits_unclassified") or []
        store_class_hits = [h for h in decisive_series
                            if h.get("class") in STORE_HOST_CLASSES]
        checks.append(_check(
            "C12", "host scan: every series-shaped market hit is named and none is a "
                   "CME/commodity dated-curve price store",
            not unclassified and not store_class_hits and not (host.get("unclassified") or []),
            {"roots": host.get("scan_roots"), "missing_roots": host.get("missing_roots"),
             "files_scanned": host.get("files_scanned"),
             "text_files_scanned": host.get("text_files_scanned"),
             "row_files_scanned": host.get("row_files_scanned"),
             "series_hits_unclassified": unclassified[:20],
             "decisive_series_hits": decisive_series,
             "decisive_series_hits_by_class": host.get("decisive_series_hits_by_class"),
             "unclassified_text_hits": (host.get("unclassified") or [])[:20]}))

    # ---- C13: the round directory state - nothing was ever submitted
    rd_exists = os.path.isdir(rd)
    checks.append(_check(
        "C13", "round dir holds exactly the two terminal artifacts and no attempt",
        rd_exists and os.path.isfile(paths["round_spec"]) and os.path.isfile(paths["verdict"])
        and not os.path.exists(paths["attempts_dir"])
        and not os.path.exists(paths["survivor_bundle"]),
        {"round_dir": rd, "round_spec": os.path.isfile(paths["round_spec"]),
         "verdict": os.path.isfile(paths["verdict"]),
         "attempts_dir": os.path.exists(paths["attempts_dir"]),
         "survivor_bundle": os.path.exists(paths["survivor_bundle"]),
         "round_dir_entries": sorted(os.listdir(rd)) if rd_exists else None}))

    if not (rd_exists and os.path.isfile(paths["round_spec"])
            and os.path.isfile(paths["verdict"])):
        return checks
    spec = _load_json(paths["round_spec"])
    verdict = _load_json(paths["verdict"])

    # ---- C14: verdict.json carries the contract-mandated terminal values
    v_fail = verdict.get("failure") or {}
    v_yield = verdict.get("yield") or {}
    checks.append(_check(
        "C14", "verdict.json = TECHNICAL_INCOMPLETE with layer/class/run_id/yield",
        verdict.get("verdict") == "TECHNICAL_INCOMPLETE"
        and verdict.get("performance_claimable") is False
        and verdict.get("run_id") is None
        and v_fail.get("layer") == "card-local"
        and v_fail.get("class") == "data_window_invalid"
        and v_fail.get("last_run_id") is None
        and v_yield.get("yield_decision") == "STOP_TECHNICAL_INCOMPLETE"
        and v_yield.get("no_progress_rounds") == 1
        and bool(verdict.get("missing_conditions"))
        and verdict.get("evidence_run_ids") == []
        and verdict.get("kanban_task_id") == TASK
        and verdict.get("family_id") == FAMILY and verdict.get("round_id") == ROUND
        and bool(verdict.get("decided_at_utc")),
        {"verdict": verdict.get("verdict"),
         "performance_claimable": verdict.get("performance_claimable"),
         "run_id": verdict.get("run_id"), "layer": v_fail.get("layer"),
         "class": v_fail.get("class"), "yield_decision": v_yield.get("yield_decision"),
         "missing_conditions": len(verdict.get("missing_conditions") or []),
         "evidence_run_ids": verdict.get("evidence_run_ids")}))

    # ---- C15: identity + live-source hashes
    prov = spec.get("provenance") or {}
    live_record_sha = _sha256_file(record_path) if rec_exists else None
    card_ok = True
    card_sha = None
    if card_body_path and os.path.isfile(card_body_path):
        card_sha = _sha256_text(open(card_body_path, encoding="utf-8").read())
        card_ok = card_sha == prov.get("card_body_sha256_footer_stripped")
    contract_path = os.path.join(repo_root, "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md")
    fam_path = os.path.join(paths["family_dir"], "family.json")
    fam = _load_json(fam_path) if os.path.isfile(fam_path) else {}
    checks.append(_check(
        "C15", "round-spec identity matches the family, the record and the contract",
        spec.get("family_id") == FAMILY and spec.get("round_id") == ROUND
        and spec.get("kanban_task_id") == TASK and spec.get("kanban_board") == BOARD
        and prov.get("record_sha256") == live_record_sha
        and spec.get("contract_sha256") == _sha256_file(contract_path)
        and prov.get("semantic_fingerprint") == fam.get("semantic_fingerprint")
        and card_ok,
        {"record_sha256": prov.get("record_sha256"), "live_record_sha256": live_record_sha,
         "contract_sha256": spec.get("contract_sha256"),
         "live_contract_sha256": _sha256_file(contract_path), "card_ok": card_ok,
         "card_sha": card_sha,
         "spec_card_sha": prov.get("card_body_sha256_footer_stripped"),
         "semantic_fingerprint": prov.get("semantic_fingerprint"),
         "family_fingerprint": fam.get("semantic_fingerprint")}))

    # ---- C16: prerequisite_gate block agrees with the verdict
    gate = spec.get("prerequisite_gate") or {}
    checks.append(_check(
        "C16", "round-spec prerequisite_gate states the same terminal as verdict.json",
        gate.get("verdict") == verdict.get("verdict")
        and gate.get("failure_layer") == v_fail.get("layer")
        and gate.get("failure_class_used") == v_fail.get("class")
        and gate.get("last_run_id") is None
        and gate.get("attempts_launched") == 0
        and gate.get("universe_shrunk") is False
        and gate.get("substitute_market_used") is False
        and bool(gate.get("measured_absence")) and bool(gate.get("decisive_absences"))
        and sorted(gate.get("declared_verdict_alternatives") or [])
        == ["DEFERRED", "TECHNICAL_INCOMPLETE"],
        {"gate_verdict": gate.get("verdict"), "layer": gate.get("failure_layer"),
         "class": gate.get("failure_class_used"), "attempts": gate.get("attempts_launched"),
         "measured_absence_items": len(gate.get("measured_absence") or []),
         "decisive_absences": len(gate.get("decisive_absences") or [])}))

    # ---- C17: registered requirement matrix
    uni = spec.get("universe_registration") or {}
    matrix = uni.get("required_data_matrix") or []
    statuses = {r.get("status") for r in matrix}
    decisive = [r for r in matrix if r.get("status") in DECISIVE_REQUIRED_STATUSES]
    checks.append(_check(
        "C17", "required-data matrix: registered statuses, counts and decisive set",
        len(matrix) >= 20
        and int(uni.get("decisive_matrix_item_count") or -1) == len(decisive)
        and uni.get("decisive_status_map") == {r["item"]: r["status"] for r in decisive}
        and all(r.get("status") in DECISIVE_REQUIRED_STATUSES for r in decisive)
        and (uni.get("required_data_matrix_status_counts") or {})
        == {s: sum(1 for r in matrix if r.get("status") == s) for s in statuses}
        and uni.get("required_data_available_local") is False
        and uni.get("universe_shrunk_to_local_list") is False,
        {"matrix_items": len(matrix),
         "status_counts": uni.get("required_data_matrix_status_counts"),
         "decisive_items": uni.get("decisive_matrix_item_count"),
         "decisive_recomputed": len(decisive),
         "available_local": uni.get("required_data_available_local"),
         "universe_shrunk": uni.get("universe_shrunk_to_local_list")}))

    # ---- C18: DCA registration keeps the contract 7.2 v3.1 provenance classes
    dca = spec.get("dca_domain") or {}
    axes = dca.get("axes") or {}
    product = 1
    for v in axes.values():
        product *= len(v)
    ufi = spec.get("user_fixed_invariants") or {}
    checks.append(_check(
        "C18", "DCA registration: complete 48-cell product and 7.2 v3.1 provenance classes",
        dca.get("configs_per_cohort_per_grid") == 48 and product == 48
        and sorted(axes) == ["breakeven_tp_pct", "invalidation_pct", "size_multiplier",
                             "spacing_pct"]
        and set((dca.get("axes_status") or {}).values())
        == {"PROJECT_PRE_REGISTERED_SEARCH_DOMAIN"}
        and dca.get("base_quote") == 1000
        and dca.get("base_quote_status") == "PROJECT_PRE_REGISTERED_CONSTANT"
        and not (set(axes) & set(ufi))
        and "base_quote" not in ufi and "base_quote_status" not in ufi,
        {"configs_per_cohort_per_grid": dca.get("configs_per_cohort_per_grid"),
         "axes": {k: len(v) for k, v in axes.items()}, "product": product,
         "axes_status": dca.get("axes_status"),
         "base_quote_status": dca.get("base_quote_status"),
         "user_fixed_keys": sorted(ufi)}))

    # ---- C19: the falsification battery is the record's, unchanged
    fals = spec.get("falsification") or {}
    rec_fals_block = _section(rec, "## Falsification plan", "\n## Crypto portability")
    rec_fals = [l for l in rec_fals_block.splitlines() if l.split(".")[0].isdigit()]
    checks.append(_check(
        "C19", "falsification battery: record-faithful, no lowering, no removal",
        fals.get("item_count") == len(rec_fals) == 4
        and fals.get("card_excerpt_item_count") == 4
        and fals.get("no_threshold_lowering") is True
        and fals.get("no_item_removal") is True
        and bool(fals.get("blocked_by")),
        {"item_count": fals.get("item_count"), "record_items": len(rec_fals),
         "card_excerpt_items": fals.get("card_excerpt_item_count"),
         "no_threshold_lowering": fals.get("no_threshold_lowering"),
         "no_item_removal": fals.get("no_item_removal"),
         "blocked_by": fals.get("blocked_by")}))

    # ---- C20: nothing was ever launched
    launch = spec.get("launch") or {}
    checks.append(_check(
        "C20", "launch block: zero attempts, no run-spec, no sentinel",
        launch.get("launched") is False and launch.get("attempts") == 0
        and launch.get("run_specs") == 0 and launch.get("terminal_sentinels") == 0
        and launch.get("run_spec") is None and launch.get("terminal_sentinel") is None
        and launch.get("attempt_dir") is None,
        {"launch": launch}))

    # ---- C21: coverage product is the registered product (vacuously, zero cohorts)
    cov = spec.get("coverage") or {}
    rp = spec.get("robustness_plan") or {}
    checks.append(_check(
        "C21", "coverage: registered product == computed product (0 = 0) and 10 grids",
        cov.get("phase_grids") == rp.get("registered_phase_grids")
        and len(cov.get("phase_grids") or []) == 10
        and cov.get("cells_registered_per_grid") == 0
        and cov.get("cells_registered_total") == 0
        and cov.get("cells_computed") == 0
        and rp.get("no_result_based_universe_shrinking") is True,
        {"grids": len(cov.get("phase_grids") or []),
         "registered_total": cov.get("cells_registered_total"),
         "computed": cov.get("cells_computed")}))

    # ---- C22: no fabricated performance number anywhere in the artifacts
    perf = _no_performance_claims(spec, "round-spec") + _no_performance_claims(verdict, "verdict")
    checks.append(_check(
        "C22", "no fabricated performance metric in the round artifacts",
        not perf,
        {"offending_keys": perf[:20]}))

    # ---- C23: decisive booleans are all false and the spec declares the gate
    sel = spec.get("selector_and_disposition") or {}
    checks.append(_check(
        "C23", "decisive terminal booleans are false and the spec declares the gate",
        spec.get("expected") == "PREREQUISITE_ABSENT"
        and sel.get("cohorts_realized") == 0
        and sel.get("survivors") == []
        and bool(spec.get("non_goals")),
        {"expected": spec.get("expected"),
         "strategy_domain_status": spec.get("strategy_domain_status"),
         "expected_status": spec.get("expected_status"),
         "cohorts_realized": sel.get("cohorts_realized"),
         "non_goals": len(spec.get("non_goals") or [])}))

    # ---- C24: the registered requirement is the record's own required data
    req = spec.get("registered_requirement") or {}
    rec_req_block = _section(rec, "## Required data", "## Execution assumptions")
    rec_req_items = [l for l in rec_req_block.splitlines() if l.startswith("- **")]
    checks.append(_check(
        "C24", "registered requirement equals the record's own required-data block",
        len(rec_req_items) == 5
        and (uni.get("registered_data_needed") or []) == rec_req_items
        and "CME Group commodity futures" in (req.get("record_required_data_verbatim") or "")
        and "Adapted / unproven" in (req.get("record_portability_verbatim") or ""),
        {"record_items": len(rec_req_items),
         "registered_items": len(uni.get("registered_data_needed") or []),
         "portability_class": req.get("portability_class")}))

    # ---- C25: the verdict's missing conditions name the record's requirements
    missing = verdict.get("missing_conditions") or []
    joined = " ".join(missing)
    checks.append(_check(
        "C25", "missing_conditions name the absent market and the absent data fields",
        len(missing) >= 8 and "CME" in joined
        and "settlement" in joined and "maturity" in joined
        and ("dated" in joined or "term-structure" in joined or "term structure" in joined),
        {"count": len(missing), "heads": [m[:70] for m in missing[:6]]}))
    return checks


# ------------------------------------------------------- verbatim ------------
def _card_body(task=TASK, board_db=BOARD_DB):
    """Read the card body (verbatim, footer included) from the board DB, read-only."""
    con = sqlite3.connect("file:%s?mode=ro" % board_db, uri=True)
    try:
        row = con.execute("select body from tasks where id=?", (task,)).fetchone()
    finally:
        con.close()
    return row[0] if row else None


def _resolve_source_texts(repo_root=None, card_body_path=None, record_path=None):
    repo_root = repo_root or DEFAULT_REPO
    sources = {}
    if card_body_path and os.path.isfile(card_body_path):
        sources["card"] = open(card_body_path, encoding="utf-8", errors="replace").read()
    else:
        body = _card_body()
        if body is not None:
            sources["card"] = body
    record_path = record_path or DEFAULT_RECORD
    if os.path.isfile(record_path):
        sources["record"] = open(record_path, encoding="utf-8", errors="replace").read()
    contract = os.path.join(repo_root, "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md")
    if os.path.isfile(contract):
        sources["contract"] = open(contract, encoding="utf-8", errors="replace").read()
    try:
        sys.path.insert(0, os.path.join(repo_root, "runtime"))
        import production_handoff
        sources["footer"] = production_handoff.LIFECYCLE_FOOTER
    except Exception:
        pass
    return sources


def _walk_verbatim(node, path=()):
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, str) and "verbatim" in str(k).lower():
                yield ".".join(path + (str(k),)), v
            else:
                for item in _walk_verbatim(v, path + (str(k),)):
                    yield item
    elif isinstance(node, list):
        for i, v in enumerate(node):
            for item in _walk_verbatim(v, path + (str(i),)):
                yield item


def verify_verbatim(spec_path=None, repo_root=None, card_body_path=None,
                    record_path=None, results_root=None):
    """Resolve every *_verbatim leaf of the persisted round-spec against its source."""
    paths = _round_paths(results_root or DEFAULT_RESULTS)
    spec_path = spec_path or paths["round_spec"]
    spec = _load_json(spec_path)
    sources = _resolve_source_texts(repo_root, card_body_path, record_path)
    smap = spec.get("excerpt_source_map") or {}
    findings = []
    checked = 0
    seen = set()
    for key, value in _walk_verbatim(spec):
        # every leaf found by the walker must be declared in the source map
        if key in seen:
            continue
        seen.add(key)
        entry = smap.get(key)
        if entry is None:
            findings.append({"key": key, "problem": "leaf not declared in excerpt_source_map"})
            continue
        src_name = entry.get("source")
        src = sources.get(src_name)
        if src is None:
            findings.append({"key": key, "problem": "unknown source %r" % src_name})
            continue
        checked += 1
        if value not in src:
            findings.append({"key": key, "problem": "not a substring of %r" % src_name,
                             "chars": len(value)})
            continue
        if entry.get("chars") != len(value):
            findings.append({"key": key, "problem": "chars %s != %d" % (entry.get("chars"),
                                                                       len(value))})
        if entry.get("sha256") != _sha256_text(value):
            findings.append({"key": key, "problem": "sha256 mismatch"})
    # declared map entries that the walker never saw (stale declarations)
    for key in smap:
        if key not in seen and key.split(".")[-1] in ("",):
            findings.append({"key": key, "problem": "declared but not a verbatim leaf"})
    return {"spec": spec_path, "verbatim_leaves_checked": checked,
            "source_map_entries": len(smap), "findings": findings,
            "ok": not findings}


# ------------------------------------------------------- self-test ------------
def _copy_round(src_results, dst_results, family=FAMILY, round_id=ROUND):
    src = os.path.join(src_results, family, "rounds", round_id)
    dst = os.path.join(dst_results, family, "rounds", round_id)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copytree(src, dst)
    fam = os.path.join(src_results, family, "family.json")
    if os.path.isfile(fam):
        shutil.copy2(fam, os.path.join(dst_results, family, "family.json"))
    return dst


def _mutate(path, fn):
    doc = _load_json(path)
    fn(doc)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, ensure_ascii=False)


def _failing_ids(checks):
    return [c["id"] for c in checks if not c["ok"]]


def self_test(results_root=None, raw=None, host=None, repo_root=None, record_path=None,
              card_body_path=None):
    """Tamper the immutable artifacts in temp copies; the named check must refuse."""
    results_root = results_root or DEFAULT_RESULTS
    repo_root = repo_root or DEFAULT_REPO
    out = {"variants": [], "ok": True}
    raw = raw or measure_raw()
    base = tempfile.mkdtemp(prefix="cfhgl-selftest-")

    def run(tmp_results):
        return run_checks(results_root=tmp_results, raw_root=DEFAULT_RAW, repo_root=repo_root,
                          record_path=record_path or DEFAULT_RECORD,
                          card_body_path=card_body_path, host=host or {}, raw=raw)

    cases = [
        ("verdict_not_technical_incomplete", ["C14", "C16"],
         lambda d: d.update({"verdict": "PASS"})),
        ("verdict_performance_claimable_true", ["C14"],
         lambda d: d.update({"performance_claimable": True})),
        ("verdict_failure_layer_shared", ["C14", "C16"],
         lambda d: d["failure"].update({"layer": "shared-layer"})),
        ("verdict_run_id_set", ["C14"], lambda d: d.update({"run_id": "r1-u1"})),
        ("verdict_yield_continue", ["C14"],
         lambda d: d["yield"].update({"yield_decision": "CONTINUE"})),
        ("verdict_missing_conditions_emptied", ["C25"],
         lambda d: d.update({"missing_conditions": ["x"]})),
        ("spec_gate_universe_shrunk", ["C16"],
         lambda d: d["prerequisite_gate"].update({"universe_shrunk": True})),
        ("spec_matrix_decisive_count_off", ["C17"],
         lambda d: d["universe_registration"].update(
             {"decisive_matrix_item_count": 1})),
        ("spec_required_data_available_true", ["C17"],
         lambda d: d["universe_registration"].update(
             {"required_data_available_local": True})),
        ("spec_dca_product_broken", ["C18"],
         lambda d: d["dca_domain"].update({"configs_per_cohort_per_grid": 47})),
        ("spec_dca_axis_marked_user_fixed", ["C18"],
         lambda d: d["dca_domain"]["axes_status"].update(
             {"spacing_pct": "USER_FIXED"})),
        ("spec_falsification_item_dropped", ["C19"],
         lambda d: d["falsification"].update({"item_count": 3})),
        ("spec_launch_claims_an_attempt", ["C20"],
         lambda d: d["launch"].update({"launched": True, "attempts": 1})),
        ("spec_fabricated_performance_number", ["C22"],
         lambda d: d["prerequisite_gate"].update({"net_pnl": 12345.67})),
        ("spec_cohorts_realized", ["C23"],
         lambda d: d["selector_and_disposition"].update({"cohorts_realized": 5})),
        ("attempt_dir_present", ["C13"],
         lambda d: os.makedirs(os.path.join(os.path.dirname(d["__path__"]), "attempts"),
                               exist_ok=True)),
        ("benign_non_goals_edit", None,
         lambda d: d.update({"non_goals": (d.get("non_goals") or []) + ["benign control"]})),
    ]
    for name, expect_fail, mutate in cases:
        tag = tempfile.mkdtemp(prefix="cfhgl-case-", dir=base)
        rd = _copy_round(results_root, tag)
        spec_path = os.path.join(rd, "round-spec.json")
        verdict_path = os.path.join(rd, "verdict.json")
        if name.startswith("verdict"):
            _mutate(verdict_path, mutate)
        else:
            if name == "attempt_dir_present":
                mutate({"__path__": spec_path})
            else:
                _mutate(spec_path, mutate)
        checks = run(tag)
        fails = _failing_ids(checks)
        if expect_fail is None:
            # the benign edit must not flip anything that was green before
            before = _failing_ids(run(results_root))
            ok = set(fails) == set(before)
        else:
            ok = set(fails) == set(expect_fail)
        out["variants"].append({"variant": name, "expected_fail": expect_fail,
                                "failing_checks": fails, "ok": ok})
        out["ok"] = out["ok"] and ok
    shutil.rmtree(base, ignore_errors=True)
    return out


# --------------------------------------------------- raw fixture control ------
def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    mode = "wb" if isinstance(text, bytes) else "w"
    with open(path, mode) as fh:
        fh.write(text)


def raw_fixture_control(temp_root=None, repo_root=None, results_root=None):
    """Build a raw tree that DOES carry this record's market; the raw checks must fail."""
    root = temp_root or tempfile.mkdtemp(prefix="cfhgl-rawfixture-")
    if temp_root is None:
        _write(os.path.join(root, "_meta/CONFIG.json"), json.dumps({
            "schema": "market-data-raw/config/v1",
            "venue": "CME",
            "market_type": "futures",
            "symbols": ["CL", "GC", "ZC", "LE"],
            "intervals": ["1d"],
            "klines_end_rule": "last completed session",
            "updater": "_tools/cme_sync.py",
        }, indent=1))
        _write(os.path.join(root, "_meta/SCHEMA.md"),
               "# market-data-raw — schema\n\n## Dataset: settlements (CME commodity futures)\n\n"
               "* rows carry settlement price, volume, contract month and maturity.\n")
        _write(os.path.join(root, "_meta/INVENTORY.md"), "# inventory\n")
        rows = []
        for cm, settle in (("2026-03", 71.25), ("2026-06", 70.9), ("2026-12", 70.1)):
            rows.append(json.dumps({"commodity": "CL", "contract_month": cm,
                                    "settlement": settle, "volume": 120000,
                                    "maturity_ms": 1774051200000,
                                    "expiration": "2026-03-20"}))
        _write(os.path.join(root, "cme/commodity/settlements/CL-1d-2026-03.jsonl"),
               "\n".join(rows) + "\n")
        _write(os.path.join(root, "cme/commodity/instruments/cme-instruments.json"),
               json.dumps({"instruments": [{"fields": {
                   "raw_symbol": "CL", "type": "FuturesContract",
                   "settlement_currency": "USD", "maturity": "2026-03-20",
                   "contract_month": "2026-03"}}]}))
        _write(os.path.join(root, "_tools/cme_sync.py"), "# CME daily settlement downloader\n")
    raw = measure_raw(root)
    checks = run_checks(results_root=results_root or DEFAULT_RESULTS, raw_root=root,
                        repo_root=repo_root or DEFAULT_REPO, record_path=DEFAULT_RECORD,
                        host={}, raw=raw)
    fails = _failing_ids(checks)
    expected = {"C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9", "C10", "C11"}
    return {"fixture_root": root, "failing_checks": fails,
            "expected_failing": sorted(expected),
            "ok": expected <= set(fails),
            "decisive_offending_hits": raw.get("decisive_offending_hits"),
            "decisive_hits_in_unclassified": raw.get("decisive_hits_in_unclassified"),
            "decisive_carried_by_row_shape": raw.get("decisive_carried_by_row_shape"),
            "decisive_name_hits": raw.get("decisive_name_hits"),
            "probe_hits_unexplained_payload": raw.get("probe_hits_unexplained_payload"),
            "instrument_has_maturity_field": raw.get("instrument_has_maturity_field")}


# ------------------------------------------------ excluded token pass ---------
def excluded_token_pass(raw=DEFAULT_RAW, roots=None, read_cap=2 * 1024 * 1024):
    """Re-probe the generic tokens the decisive vocabulary leaves out."""
    compiled = {k: re.compile(v, re.I) for k, v in EXCLUDED_TOKENS.items()}
    out = {"tokens": {}, "unexplained": [], "ok": True}
    text_exts = (".json", ".jsonl", ".md", ".txt", ".py", ".sh", ".yaml", ".yml",
                 ".csv", ".tsv", ".html", ".log")
    raw_name = {t: [] for t in EXCLUDED_TOKENS}
    raw_payload = {t: 0 for t in EXCLUDED_TOKENS}
    raw_files = {t: [] for t in EXCLUDED_TOKENS}
    if os.path.isdir(raw):
        for dirpath, dirs, names in _walk(raw, max_depth=PROBE_MAX_DEPTH):
            for fn in names:
                if fn.startswith("."):
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, raw)
                for tok, rx in compiled.items():
                    if rx.search(rel):
                        raw_name[tok].append(rel)
                        continue
                    if fn.endswith(text_exts) or fn.endswith(".gz"):
                        txt = _read_text(full, cap=read_cap)
                        if txt:
                            c = len(rx.findall(txt))
                            if c:
                                raw_payload[tok] += c
                                raw_files[tok].append({"path": rel, "count": c})
    host_series = {}
    for root in (roots or HOST_ROOTS):
        if not os.path.isdir(root):
            continue
        for dirpath, dirs, names in _walk(root, max_depth=64):
            for fn in names:
                if fn.startswith(".") or not fn.endswith(HOST_ROW_EXTS):
                    continue
                full = os.path.join(dirpath, fn)
                toks = [t for t, rx in compiled.items() if rx.search(fn)]
                if toks:
                    host_series.setdefault(tuple(toks), []).append(full)
    for tok in EXCLUDED_TOKENS:
        out["tokens"][tok] = {
            "raw_entry_name_hits": sorted(raw_name[tok]),
            "raw_payload_hits": raw_payload[tok],
            "raw_payload_files": sorted(raw_files[tok], key=lambda e: -e["count"])[:8],
        }
    out["host_series_name_hits"] = {",".join(k): sorted(set(v))[:12]
                                    for k, v in sorted(host_series.items())}
    return out


# ----------------------------------------------------------------- main -------
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--measure-only", action="store_true")
    ap.add_argument("--verify-verbatim", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--raw-fixture-control", action="store_true")
    ap.add_argument("--excluded-token-pass", action="store_true")
    ap.add_argument("--host-scan", action="store_true")
    ap.add_argument("--results-root", default=DEFAULT_RESULTS)
    ap.add_argument("--raw-root", default=DEFAULT_RAW)
    ap.add_argument("--repo-root", default=DEFAULT_REPO)
    ap.add_argument("--record-path", default=DEFAULT_RECORD)
    ap.add_argument("--card-body-path", default=None)
    ap.add_argument("--raw-json", default=None,
                    help="reuse a previously measured raw dict (default: measure live)")
    args = ap.parse_args(argv)
    cached_raw = None
    if args.raw_json and os.path.isfile(args.raw_json):
        cached_raw = _load_json(args.raw_json)

    if args.measure_only:
        raw = measure_raw(args.raw_root)
        print(json.dumps(raw, indent=1, ensure_ascii=False, default=str))
        return 0
    if args.verify_verbatim:
        res = verify_verbatim(repo_root=args.repo_root, record_path=args.record_path,
                              card_body_path=args.card_body_path,
                              results_root=args.results_root)
        print(json.dumps(res, indent=1, ensure_ascii=False))
        return 0 if res["ok"] else 1
    if args.self_test:
        res = self_test(results_root=args.results_root, repo_root=args.repo_root,
                        record_path=args.record_path, card_body_path=args.card_body_path,
                        raw=cached_raw)
        print(json.dumps(res, indent=1, ensure_ascii=False))
        return 0 if res["ok"] else 1
    if args.raw_fixture_control:
        res = raw_fixture_control(repo_root=args.repo_root, results_root=args.results_root)
        print(json.dumps(res, indent=1, ensure_ascii=False))
        return 0 if res["ok"] else 1
    if args.excluded_token_pass:
        res = excluded_token_pass(args.raw_root)
        print(json.dumps(res, indent=1, ensure_ascii=False))
        return 0 if res["ok"] else 1
    if args.host_scan:
        res = measure_host_stores()
        print(json.dumps(res, indent=1, ensure_ascii=False, default=str))
        return 0 if not res["unclassified"] and not res["series_hits_unclassified"] else 1

    checks = run_checks(results_root=args.results_root, raw_root=args.raw_root,
                        repo_root=args.repo_root, record_path=args.record_path,
                        card_body_path=args.card_body_path, raw=cached_raw)
    failed = [c for c in checks if not c["ok"]]
    if args.json:
        print(json.dumps({"checks": checks, "failed": [c["id"] for c in failed],
                          "ok": not failed}, indent=1, ensure_ascii=False, default=str))
    else:
        for c in checks:
            print("%-4s %-5s %s" % (c["id"], "PASS" if c["ok"] else "FAIL", c["name"]))
        print("\n%d/%d checks PASS" % (len(checks) - len(failed), len(checks)))
        for c in failed:
            print("FAIL %s: %s" % (c["id"], json.dumps(c["detail"], ensure_ascii=False)[:400]))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
