#!/usr/bin/env python3
"""Prerequisite-gate read-back checker for the family

    bitcoin-option-rnd-low-volatility-high-vrp-regime-2026-09-01

Card t_05ef4352 terminalises this family as TECHNICAL_INCOMPLETE because the
record's required data / market is not in the canonical raw:

  * the record's object is the **option-implied risk-neutral density (RND) of
    Bitcoin** - daily fixed-maturity RNDs estimated from **Deribit BTC option
    transaction/quote data** (timestamp, call/put type, strike, maturity,
    implied volatility / option price, underlying price), clustered by
    functional-data hierarchical clustering into HV/LV regimes, with the
    Bitcoin variance risk premium (BVRP) defined from **risk-neutral variance
    relative to physical realized variance** over a 27-day horizon, using the
    9-, 27- and 45-day implied-volatility curves;
  * the record's own required-data list additionally names a risk-free rate
    proxy, fixed-maturity interpolation, realized Bitcoin variance aligned to
    the same horizon, mandatory point-in-time option availability/timestamps
    and explicit 24/7 calendar / UTC settlement handling;
  * the canonical raw holds **one venue (BINANCE) of USD-M perpetuals and four
    fixed contracts** (BTCUSDT/ETHUSDT/BNBUSDT/SOLUSDT), OHLCV-only klines,
    funding and instrument metadata - no option row of any kind, no strike, no
    maturity/expiry, no implied volatility, no option venue, no risk-neutral
    density or variance, no variance risk premium, no risk-free/discount curve
    and no point-in-time option availability surface;
  * the card forbids substituting a proxy market, approximate data or a
    rewritten hypothesis, and forbids shrinking the universe to the four local
    contracts. The record's Crypto-portability label is `direct` because the
    source is *already* Bitcoin-options research on Deribit - which is exactly
    the surface that does not exist locally.

This script exists so an independent reader can re-derive that determination
from the live filesystem instead of trusting prose:

  * C1-C4 re-measure the canonical raw: store identity, the instrument surface,
    the kline surface (structurally, by payload key set) and the funding
    surface;
  * C5-C6 probe the raw tree for this record's requirement vocabulary by entry
    name and by payload content, and assert every hit is classified;
  * C7-C13 assert the record's decision surfaces item by item: no option market
    surface, no option/venue surface, no risk-neutral-density / variance-risk-
    premium state surface, no risk-free/discount surface, the registered price
    series present only as a USD-M perpetual analogue, and a store schema that
    declares exactly klines/funding/instruments;
  * C14-C16 re-measure the host's non-canonical stores for the same vocabulary
    - prose mentions are classified by class, row-shape probed, and the decisive
    requirement groups must have no series-shaped surface anywhere;
  * C17-C26 re-read the round's immutable artifacts and assert they state
    exactly the contract-mandated terminal values (verdict, layer, class, yield
    decision, zero attempts, null run_id), that nothing was ever submitted (no
    run-spec, no attempt directory, no terminal sentinel), that the DCA
    registration still carries the contract 7.2 v1.3.1 provenance classes plus
    the complete 48-cell product, that the falsification battery is unchanged,
    and that no performance number was fabricated;
  * `--verify-verbatim` resolves every `*_verbatim` leaf of the persisted
    round-spec against its declared source (card / record / contract / footer)
    and re-checks the excerpt digest map, independent of the authoring run;
  * `--excluded-token-pass` re-probes the generic tokens that the decisive
    vocabulary deliberately excludes (`call`, `put`, `delta`, `gamma`, `vega`,
    `spot`, `index`, `quote`, `bid`, `ask`), so an exclusion cannot hide a hit.

Read-only: it never writes inside the results tree or the raw tree. `--self-test`
builds tampered copies in fresh temp directories and asserts the checker refuses
them (non-vacuousness control). `--raw-fixture-control` builds a temp raw tree
carrying what this record would need (a Deribit BTC option chain with strikes /
expiries / implied vols / call-put flags, fixed-maturity 9/27/45-day IV curves,
a risk-free curve, a risk-neutral-density panel with risk-neutral variance and a
variance-risk-premium series, and a BTC spot index series) and asserts the
raw-side checks flip to FAIL. `--host-scan` re-runs the house-wide search.

Usage:
    python3 runtime/bitcoin_option_rnd_low_volatility_high_vrp_regime_prerequisite_check.py [--json]
    python3 runtime/bitcoin_option_rnd_low_volatility_high_vrp_regime_prerequisite_check.py --measure-only
    python3 runtime/bitcoin_option_rnd_low_volatility_high_vrp_regime_prerequisite_check.py --verify-verbatim
    python3 runtime/bitcoin_option_rnd_low_volatility_high_vrp_regime_prerequisite_check.py --self-test
    python3 runtime/bitcoin_option_rnd_low_volatility_high_vrp_regime_prerequisite_check.py --raw-fixture-control
    python3 runtime/bitcoin_option_rnd_low_volatility_high_vrp_regime_prerequisite_check.py --excluded-token-pass
    python3 runtime/bitcoin_option_rnd_low_volatility_high_vrp_regime_prerequisite_check.py --host-scan

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

FAMILY = "bitcoin-option-rnd-low-volatility-high-vrp-regime-2026-09-01"
ROUND = FAMILY + "-r1"
TASK = "t_05ef4352"
BOARD = "quant-strategy-research"
DEFAULT_RAW = "/Volumes/ExpansionDrive/market-data-raw"
DEFAULT_RESULTS = "/Volumes/ExpansionDrive/qlib-results"
DEFAULT_REPO = "/Users/hong/workspace/quant-runtime-pipeline"
DEFAULT_RECORD = "/Users/hong/.hermes/wiki/quant/%s.md" % FAMILY
BOARD_DB = os.path.join(os.path.expanduser("~"), ".hermes/kanban/boards", BOARD,
                        "kanban.db")
PROBE_MAX_DEPTH = 8

# --- the record's requirement vocabulary -------------------------------------
PATTERNS = {
    # option market surface (the record's decision variable is built from this)
    "option": r"\boptions?\b",
    "strike": r"\bstrikes?\b",
    "maturity": r"\bmaturit(?:y|ies)\b",
    "expiry": r"\bexpir(?:y|ation|ies|ing)\b",
    "implied_vol": r"\bimplied[-_ ]?vol(?:atility)?\b",
    "iv_surface": r"\biv[-_ ]?(?:surface|curve|smile|skew)\b",
    "option_chain": r"\boption[-_ ]?chains?\b",
    "open_interest": r"\bopen[-_ ]?interest\b",
    "put_call_parity": r"\bput[-_ ]call[-_ ](?:parity|ratio)\b",
    "greeks": r"\bgreeks?\b",
    "straddle": r"\bstraddles?\b",
    "variance_swap": r"\bvariance[-_ ]swaps?\b",
    "delta_hedge": r"\bdelta[-_ ]?hedg(?:e|es|ing|ed)\b",
    "fixed_maturity": r"\bfixed[-_ ]maturity\b",
    # option venue / reference surface
    "deribit": r"\bderibit\b",
    "dvol": r"\bdvol\b",
    "bvix": r"\bbvix\b",
    # risk-neutral state surface (the record's regime state variable)
    "risk_neutral_density": r"\brisk[-_ ]neutral[-_ ](?:density|densities|distribution)\b",
    "risk_neutral_variance": r"\brisk[-_ ]neutral[-_ ](?:variance|vol(?:atility)?)\b",
    "variance_risk_premium": r"\bvariance[-_ ]risk[-_ ]premium\b",
    "vrp": r"\bvrp\b",
    # risk-free / discount surface (required data item 3)
    "risk_free": r"\brisk[-_ ]free\b",
    "sofr": r"\bsofr\b",
    "treasury": r"\btreasur(?:y|ies)\b",
    "yield_curve": r"\byield[-_ ]curves?\b",
    "discount_factor": r"\bdiscount[-_ ](?:factor|rate|curve)\b",
    "libor": r"\blibor\b",
    # realized-variance / price surface (partially derivable locally)
    "realized_variance": r"\breali[sz]ed[-_ ](?:variance|vol(?:atility)?)\b",
    "spot_index": r"\bspot[-_ ]?(?:index|price)\b",
    "index_price": r"\bindex[-_ ]price\b",
    "btc_index": r"\b(?:btc|bitcoin)[-_ ]index\b",
    # local store vocabulary (must remain present: these DO exist)
    "perpetual": r"\bperp(?:etual)?s?\b",
    "funding": r"\bfunding\b",
    "klines": r"\bklines?\b",
}


def _boundary_aware(pat):
    """Compile a word-boundary pattern so that separator-joined compound identifiers match.

    Python's ``\\b`` treats ``_`` as a word character, so ``\\bstrike[-_ ]price\\b`` would NOT
    match a column named ``strike_price`` - exactly the shape a real option chain would use.
    Replacing the leading/trailing ``\\b`` with lookarounds that treat ``_``, ``-`` and ``.``
    as separators catches compound embeddings while still refusing ordinary English false
    positives (``callback`` does not match ``call``: a trailing word character is seen).
    """
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
    "option_market_surface": ["option", "strike", "maturity", "expiry", "implied_vol",
                              "iv_surface", "option_chain", "put_call_parity", "greeks",
                              "straddle", "variance_swap", "delta_hedge", "fixed_maturity"],
    "option_venue_surface": ["deribit", "dvol", "bvix"],
    "rnd_state_surface": ["risk_neutral_density", "risk_neutral_variance",
                          "variance_risk_premium", "vrp"],
    "rate_discount_surface": ["risk_free", "sofr", "treasury", "yield_curve",
                              "discount_factor", "libor"],
}
# tokens that must have ZERO occurrences anywhere in the raw tree
DECISIVE_ZERO_TOKENS = sorted({t for grp, toks in DECISIVE_GROUPS.items() for t in toks})
# Two decisive tokens have declared, hand-inspected PROSE-ONLY occurrences inside the
# store's own documents - prose that names the missing surface in order to record its
# absence, never a data surface. Every other occurrence is an offending hit:
#   * `open_interest`: INVENTORY.md quotes the store's own mdfind query string
#     ('openInterest') while recording what was searched for and NOT found;
#   * `option`: INVENTORY.md lists the non-migrated LEAN sample dirs
#     (`equity,future,option,indexoption,futureoption`) in its 'NOT migrated' table,
#     and _tools/README.md carries the CLI usage header 'Options:'.
DECISIVE_PROSE_ALLOWED = {
    "open_interest": ["_meta/INVENTORY.md"],
    "option": ["_meta/INVENTORY.md", "_tools/README.md"],
}

# Required-data-matrix statuses that count as DECISIVE for this round: a row with one
# of these statuses is a registered requirement the local raw cannot satisfy, so the
# round cannot be computed. `PARTIAL_*` / `PRESENT_*` / `DERIVABLE_*` / disclosure-only
# statuses are honest gradings of things that exist in some form and are not decisive.
DECISIVE_REQUIRED_STATUSES = frozenset({
    "ABSENT",
    "ABSENT_AS_REGISTERED",
    "ABSENT_FOR_OPTIONS",
    "NOT_CONSTRUCTIBLE",
    "BLOCKED_BY_ABSENCE",
    "NOT_EXECUTED_BLOCKED",
})

# Generic tokens deliberately NOT part of the decisive vocabulary: each is an
# ordinary English / code word whose hits cannot discriminate a real option
# surface from unrelated text. `--excluded-token-pass` re-probes them so the
# exclusion cannot hide a hit (see EXCLUDED_TOKENS).
EXCLUDED_TOKENS = {
    "call": r"(?<![A-Za-z0-9])calls?(?![A-Za-z0-9])",
    "put": r"(?<![A-Za-z0-9])puts?(?![A-Za-z0-9])",
    "delta": r"(?<![A-Za-z0-9])delta(?![A-Za-z0-9])",
    "gamma": r"(?<![A-Za-z0-9])gamma(?![A-Za-z0-9])",
    "vega": r"(?<![A-Za-z0-9])vega(?![A-Za-z0-9])",
    "spot": r"(?<![A-Za-z0-9])spot(?![A-Za-z0-9])",
    "index": r"(?<![A-Za-z0-9])index(?:es)?(?![A-Za-z0-9])",
    "quote": r"(?<![A-Za-z0-9])quotes?(?![A-Za-z0-9])",
    "bid": r"(?<![A-Za-z0-9])bids?(?![A-Za-z0-9])",
    "ask": r"(?<![A-Za-z0-9])asks?(?![A-Za-z0-9])",
}

# Every payload hit inside the raw tree must resolve to a class here.
RAW_CLASSES = {
    "*": {
        "_meta/INVENTORY.md": "canonical_store_inventory_prose: the store's own Phase-1 "
                              "inventory; it names the non-migrated LEAN equity/future/"
                              "option/indexoption/futureoption sample dirs and the "
                              "margin_interest CSVs in its 'NOT migrated' table, i.e. a "
                              "record of what is absent from this root - and its mdfind "
                              "query list quotes the search strings (klines, aggTrade, "
                              "bookTicker, openInterest, tardis, market-data)",
        "_meta/SCHEMA.md": "canonical_store_schema_prose: declares exactly the klines / "
                           "funding / instruments dataset families, including the "
                           "deliberate coverage limit that quote_volume, trade count and "
                           "taker-buy splits are NOT present",
        "_meta/VERIFY_GAPS.txt": "canonical_store_gap_audit_prose: per-dataset row/"
                                 "duplicate audit lines for the 28 kline datasets",
        "_meta/VERIFY_TRANSCODE.txt": "canonical_store_readback_prose: read-back compare log",
        "_meta/CONFIG.json": "canonical_store_config: venue=BINANCE, market_type=usdm_perp, "
                             "four symbols, seven intervals",
        "_meta/STATE.json": "canonical_store_cursor_state: per-stream cursors of the updater",
        "_meta/TRANSCODE_MANIFEST.json": "canonical_store_transcode_manifest: per-file bytes/"
                                         "rows/sha256 of the initial import",
        "_meta/FUNDING_EXPORT.json": "canonical_store_funding_provenance",
        "_meta/INSTRUMENTS_EXPORT.json": "canonical_store_instrument_provenance",
        "_tools/README.md": "canonical_store_tool_doc_prose",
        "_tools/market_data_sync.py": "canonical_store_updater_source: Binance USD-M only; "
                                      "no option, rate or Deribit endpoint is referenced",
        "_tools/binance_public.py": "canonical_store_vendored_client_source: Binance public "
                                    "klines/funding/exchangeInfo only",
        "binance/usdm/instruments/usdm-perp-instruments.json": "canonical_store_instrument_export: "
                                                               "four USD-M perpetual definitions",
        "binance/usdm/funding/": "canonical_store_funding_rows: fundingTime/fundingRate/"
                                 "symbol/markPrice fields only",
        "binance/usdm/klines/": "canonical_store_kline_rows: open_time_ms/close_time_ms/open/"
                                "high/low/close/volume fields only",
    },
}

# Every store-shaped hit on the host must resolve to a class here, keyed by
# "<root>|<relpath>". All measured store-shaped hits are metadata/prose/index
# stores, not option or rate market data: five are the a1-* point-in-time
# membership/lifecycle stores for Binance USD-M perpetuals (their only token hit
# is the word 'perpetual' inside an instrument identifier or a contract-type
# cell), one is a research-corpus index CSV whose token hits are markdown file
# names and reason prose, and the Phase-11 option snapshot files are declared and
# measured separately as `non_canonical_option_material` (single instant).
HOST_DATA_CLASSES = {
    "/Users/hong/workspace/a1-1-phase9-pit-membership-20260830|membership_cells.jsonl":
        "binance_usdm_pit_membership_cells_not_market_data: point-in-time membership cells "
        "(cell_id 'BTCUSDT|2022-01-01', membership_status 'unknown', supported=false, "
        "evidence_tier null); the only token hit is 'perpetual' inside the instrument id - no "
        "price, quote, rate, metric or option field",
    "/Users/hong/workspace/a1-1-phase9-pit-membership-20260830|membership_observations.csv":
        "binance_usdm_pit_membership_observations_not_market_data: the same store's observation "
        "rows for USD-M perpetual membership; only token 'perpetual' in the instrument id",
    "/Users/hong/workspace/a1-usdm-pit-lifecycle-20260830|lifecycle_events.csv":
        "binance_usdm_perpetual_lifecycle_notice_store: listing/delisting event rows for Binance "
        "USD-M perpetuals; the token hits are vendor prose inside the stored notice text and the "
        "venue column value 'USD-M Futures' - not an option, rate or risk-neutral surface",
    "/Users/hong/workspace/a1-2-prospective-pit-foundation-20260830|snapshot_observations.jsonl":
        "binance_usdm_pit_snapshot_observations_not_market_data: prospective PIT snapshot "
        "observations for USD-M perpetuals; only token 'perpetual' (contractType 'PERPETUAL')",
    "/Users/hong/workspace/a1-3-prospective-pit-cohort-20260830|snapshot_observations.jsonl":
        "binance_usdm_pit_snapshot_observations_not_market_data: prospective PIT cohort snapshot "
        "observations for USD-M perpetuals; only token 'perpetual'",
    "/Users/hong/workspace/alpha-strategy-research|coverage_manifest.csv":
        "research_corpus_index_filenames_and_reasons_only: rows are markdown file names of "
        "unrelated strategy documents plus a disposition/family/reason column; the token hits "
        "('deribit', 'option', 'strike', 'open_interest', 'funding') are substrings of those "
        "file names and of the reason prose (e.g. "
        "'基于Stochastic指标的周期性期权交易策略Stochastic-Weekly-Options-Trading-Strategy.md', "
        "'Quantitative-Multi-Factor-Dynamic-Options-Trading-Strategy.md', "
        "\"Failed strict evidence rule: Lexical collision risk with 'options'\") - an index of "
        "documents, not a market series",
}

# The decisive-group data-shaped hits the host scan is allowed to return: files
# whose row shapes carry a decisive token *without* being an option or rate
# surface. Every entry here is also declared in HOST_DATA_CLASSES and inspected
# by hand; C16 asserts the measured set is a subset of this declaration.
HOST_ALLOWED_DATA_HITS = set()


def _classify_host(hits, classes):
    """Every (token, 'root|rel') host hit must resolve to a declared class."""
    explained, unexplained = [], []
    for tok, keys in sorted(hits.items()):
        for key in keys:
            why = classes.get(key)
            if why is None:
                for k, v in classes.items():
                    if key.endswith(k) or key.startswith(k):
                        why = v
                        break
            record = {"token": tok, "entry": key}
            if why is None:
                unexplained.append(record)
            else:
                explained.append({**record, "why": why})
    return explained, unexplained


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


def _first_row_keys(path, nbytes=4096):
    head = _read_text(path, nbytes)
    lines = [l for l in head.splitlines() if l.strip()]
    if not lines:
        return []
    line = lines[0]
    try:
        if path.endswith(".csv") or line.count(",") >= 2:
            return sorted(c.strip() for c in line.split(",") if c.strip())
        return sorted(json.loads(line).keys())
    except Exception:  # noqa: BLE001
        try:
            return sorted({m.group(1) for m in re.finditer(r'"([A-Za-z0-9_\- ]+)"\s*:', head)})
        except Exception:  # noqa: BLE001
            return []


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
    out["venue_dirs"] = sorted({r.split("/")[0] for r, _ in entries
                                if len(r.split("/")) >= 2 and r.split("/")[1] not in
                                ("_meta", "_tools")})
    out["second_venue_present"] = any(
        r.split("/")[0] not in ("_meta", "_tools", "binance") for r, _ in entries)
    out["spot_market_present"] = any("/spot" in r or r.startswith("binance/spot")
                                     for r, _ in entries)
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

    # kline surface (structural: payload key set) ---------------------------
    kl_files = [(r, p) for r, p in entries if r.startswith("binance/usdm/klines/")]
    out["kline_file_count"] = len(kl_files)
    out["kline_byte_total"] = sum(os.path.getsize(p) for _, p in kl_files)
    datasets, bad_keys, sample_lines = set(), [], 0
    per_symbol_daily = {}
    for rel, path in kl_files:
        parts = rel.split("/")
        if len(parts) < 5:
            bad_keys.append({"entry": rel, "why": "unexpected path depth"})
            continue
        sym, interval = parts[3], parts[4]
        datasets.add((sym, interval))
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
        if interval == "1d":
            per_symbol_daily.setdefault(sym, {"first": None, "last": None, "files": 0})
            per_symbol_daily[sym]["files"] += 1
            if per_symbol_daily[sym]["first"] is None:
                per_symbol_daily[sym]["first"] = first["open_time_ms"]
    # last 1d bar per symbol: read the last monthly file's last line
    for sym in sorted(per_symbol_daily):
        files = sorted(r for r, _ in kl_files
                       if r.startswith("binance/usdm/klines/%s/1d/" % sym))
        if not files:
            continue
        path = os.path.join(raw, files[-1])
        text = _read_text(path)
        lines = [l for l in text.splitlines() if l.strip()]
        if lines:
            per_symbol_daily[sym]["last"] = json.loads(lines[-1])["open_time_ms"]
    out["kline_datasets"] = sorted("%s/%s" % d for d in datasets)
    out["kline_dataset_count"] = len(datasets)
    out["kline_key_violations"] = bad_keys
    out["kline_sampled_files"] = sample_lines
    out["daily_windows"] = {
        s: {"first": _iso(v["first"]) if v["first"] else None,
            "last": _iso(v["last"]) if v["last"] else None,
            "monthly_files": v["files"]}
        for s, v in sorted(per_symbol_daily.items())}
    out["kline_intervals"] = sorted({d.split("/")[1] for d in out["kline_datasets"]})
    out["kline_symbols"] = sorted({d.split("/")[0] for d in out["kline_datasets"]})
    daily = out["daily_windows"].get("BTCUSDT") or {}
    out["btcusdt_daily_present"] = bool(daily.get("first") and daily.get("last"))
    out["btcusdt_daily_span_days"] = (
        _days_between(daily["first"], daily["last"]) if out["btcusdt_daily_present"] else 0)

    # funding surface -------------------------------------------------------
    fund = {}
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
        fund[rel.split("/")[3]] = {"rows": rows, "first": _iso(first) if first else None,
                                   "last": _iso(last) if last else None, "fields": fields}
    out["funding"] = fund
    out["funding_field_sets"] = sorted([list(v["fields"] or []) for v in fund.values()])
    out["funding_truth_status"] = sorted({
        str(json.loads(l).get("truth_status"))
        for rel, path in entries
        if rel.startswith("binance/usdm/funding/") and rel.endswith(".gz")
        for l in _read_text(path).splitlines() if l.strip()})

    # token probes (entry name + payload) -----------------------------------
    name_hits, payload_hits = {}, {}
    text_files = []
    for rel, path in entries:
        for tok, rx in COMPILED.items():
            if rx.search(rel):
                name_hits.setdefault(tok, []).append(rel)
        if rel.startswith("binance/usdm/klines/"):
            # structurally probed above (key set asserted on every file)
            continue
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
    out["probe_entry_name_hits"] = sorted(name_hits)
    out["probe_payload_hit_counts"] = {k: len(v) for k, v in sorted(payload_hits.items())}
    expl_n, unexp_n = _classify(name_hits, RAW_CLASSES)
    expl_p, unexp_p = _classify(payload_hits, RAW_CLASSES)
    out["probe_hits_classified_name"] = expl_n
    out["probe_hits_unexplained_name"] = unexp_n
    out["probe_hits_classified_payload"] = expl_p
    out["probe_hits_unexplained_payload"] = unexp_p

    # decisive vocabulary: must be absent everywhere in the raw tree --------
    out["decisive_zero_tokens_tested"] = len(DECISIVE_ZERO_TOKENS)
    out["decisive_name_hits"] = sorted(t for t in DECISIVE_ZERO_TOKENS if name_hits.get(t))
    out["decisive_zero_hits"] = sorted(
        t for t in DECISIVE_ZERO_TOKENS
        if t not in DECISIVE_PROSE_ALLOWED and payload_hits.get(t))
    # the decisive tokens with declared prose-only occurrences: audit every hit
    out["decisive_prose_audit"] = {}
    for tok, allowed in sorted(DECISIVE_PROSE_ALLOWED.items()):
        hits = sorted(set(payload_hits.get(tok, [])))
        out["decisive_prose_audit"][tok] = {
            "hits": hits, "allowed": list(allowed),
            "offending": [r for r in hits if r not in allowed]}
    out["decisive_offending_hits"] = sorted(
        "%s:%s" % (t, r) for t, v in out["decisive_prose_audit"].items()
        for r in v["offending"])

    # store-shaped hits & row shapes ---------------------------------------
    data_hits = sorted({rel for lst in payload_hits.values() for rel in lst
                        if rel.endswith((".json", ".jsonl", ".csv", ".gz"))})
    out["probe_payload_data_ext_hits"] = data_hits
    row_shapes = {}
    for rel in data_hits:
        row_shapes[rel] = _first_row_keys(os.path.join(raw, rel))
    out["row_shapes"] = row_shapes
    out["series_shaped_tokens"] = sorted(
        {tok for rel, keys in row_shapes.items() for tok in COMPILED
         if any(COMPILED[tok].search(k) for k in keys)})

    # decisive groups: any series-shaped (row-key) presence in the raw? ------
    group_series = {}
    for grp, toks in DECISIVE_GROUPS.items():
        group_series[grp] = sorted(
            rel for rel, keys in row_shapes.items()
            if any(COMPILED[t].search(k) for t in toks for k in keys))
    out["decisive_group_series_hits"] = group_series
    out["decisive_group_prose_hits"] = {
        grp: sorted({rel for t in toks for rel in payload_hits.get(t, [])
                     if not rel.endswith((".json", ".jsonl", ".csv", ".gz"))})
        for grp, toks in DECISIVE_GROUPS.items()}
    out["decisive_group_data_shape_hits"] = {
        grp: sorted({rel for t in toks for rel in payload_hits.get(t, [])
                     if rel.endswith((".json", ".jsonl", ".csv", ".gz"))
                     and not rel.endswith("TRANSCODE_MANIFEST.json")})
        for grp, toks in DECISIVE_GROUPS.items()}

    # schema declarations ---------------------------------------------------
    schema = _read_text(os.path.join(raw, "_meta/SCHEMA.md"))
    out["schema_dataset_sections"] = re.findall(r"^## .*$", schema, re.M)
    out["schema_dataset_names"] = sorted(
        m.group(1).strip().lower()
        for m in re.finditer(r"^##\s+Dataset:\s+([A-Za-z0-9_\-]+)", schema, re.M))
    out["schema_bytes"] = len(schema.encode())
    for key, tok in (("documents_option_dataset", "option"),
                     ("documents_rate_dataset", "risk_free"),
                     ("documents_rnd_dataset", "risk_neutral_density")):
        out[key] = any(COMPILED[tok].search(name) for name in out["schema_dataset_names"])
    return out


# ----------------------------------------------------------------- host -------
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
    "/Users/hong/workspace/alpha-strategy-research",
    "/Users/hong/workspace/btc-relative-entry-score",
    "/Users/hong/workspace/quant-runtime-pipeline",
    "/Users/hong/workspace/qlib-apple-container",
    "/Volumes/ExpansionDrive/daily-crypto-brief",
    "/Volumes/ExpansionDrive/qlib-results/_handoff/bodies",
    "/Users/hong/.hermes/wiki/quant",
]
HOST_TEXT_EXTS = (".csv", ".jsonl", ".json", ".txt", ".md", ".tsv", ".py", ".sh",
                  ".yaml", ".yml", ".jsonl.gz")
HOST_DATA_EXTS = (".csv", ".jsonl", ".jsonl.gz", ".parquet", ".db", ".sqlite")


def measure_host_stores(roots=None, read_cap=512 * 1024):
    roots = roots or HOST_ROOTS
    out = {"roots": {}, "totals": {"files_scanned": 0, "token_files": 0, "bytes": 0},
           "data_hits": [], "row_shapes": {},
           "decisive_groups": {g: {"tokens_hit": [], "data_shape_hits": []}
                               for g in DECISIVE_GROUPS}}
    payload_hits = {}
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
            if hits:
                tok += 1
                for k in hits:
                    payload_hits.setdefault(k, []).append((root, rel))
            if rel.endswith(HOST_DATA_EXTS):
                keys = _first_row_keys(path)
                if keys and any(rx.search(k) for rx in COMPILED.values() for k in keys):
                    out["row_shapes"]["%s|%s" % (root, rel)] = keys
                if hits:
                    out["data_hits"].append({"root": root, "rel": rel, "tokens": sorted(hits)})
        out["roots"][root] = {"exists": True, "files_scanned": n, "token_files": tok,
                              "bytes": nbytes}
        out["totals"]["files_scanned"] += n
        out["totals"]["token_files"] += tok
        out["totals"]["bytes"] += nbytes
    expl, unexp = _classify_host(
        {k: ["%s|%s" % (r, rel) for r, rel in v] for k, v in payload_hits.items()},
        HOST_DATA_CLASSES)
    data_keys = {"%s|%s" % (d["root"], d["rel"]) for d in out["data_hits"]}
    out["classified_data_hits"] = [h for h in expl if h["entry"] in data_keys]
    out["unclassified_data_hits"] = [h for h in unexp if h["entry"] in data_keys]
    for grp, toks in DECISIVE_GROUPS.items():
        out["decisive_groups"][grp]["tokens_hit"] = sorted(
            t for t in toks if payload_hits.get(t))
        out["decisive_groups"][grp]["data_shape_hits"] = sorted(
            "%s|%s" % (d["root"], d["rel"]) for d in out["data_hits"]
            if set(d["tokens"]) & set(toks))
    out["payload_hit_counts"] = {k: len(v) for k, v in sorted(payload_hits.items())}
    out["series_shaped_decisive"] = sorted(
        {k for k, keys in out["row_shapes"].items()
         if any(COMPILED[t].search(kk) for t in _decisive_tokens() for kk in keys)})
    out["decisive_series_shaped_hits"] = sorted(
        {k for k, keys in out["row_shapes"].items()
         for grp, toks in DECISIVE_GROUPS.items()
         if any(COMPILED[t].search(kk) for t in toks for kk in keys)})
    return out


def _decisive_tokens():
    return sorted({t for toks in DECISIVE_GROUPS.values() for t in toks})


# --------------------------------------- non-canonical option material --------
# The host DOES carry Deribit BTC option material - not in the canonical raw, but in
# an unrelated Phase-11 competency harness. It is measured here, disclosed in the
# round-spec, and never used: it is a SINGLE-INSTANT snapshot (the project's own
# lineage forbids 'latest quote as historical'), while the record's decision variable
# is a daily option-implied risk-neutral-density panel.
NON_CANONICAL_OPTION_ROOT = "/Users/hong/workspace/phase11-options-volatility"
NON_CANONICAL_OPTION_FILES = {
    "raw/deribit_get_book_summary_btc.json":
        "venue book-summary snapshot: bid/ask/mark_price/mark_iv/open_interest per "
        "instrument, one collection instant",
    "raw/deribit_get_instruments_btc.json":
        "venue instrument catalogue snapshot: kind=option, strike/expiry encoded in "
        "instrument_name, expiration_timestamp",
    "data/real_valid_quotes.json":
        "joined, mechanically valid quote rows (strike, expiry, option_type, iv, "
        "risk_free_rate, discount_factor, forward, greeks) at one available_at instant",
    "data/real_snapshot_summary.json":
        "the project's own snapshot summary and quality/exclusion counts",
    "data/synthetic_surface.json":
        "fixed-seed SYNTHETIC surface (not market data)",
    "data/variance_report.json":
        "model-free variance / RND witness computed from that single snapshot",
    "data/decision_time_witness.json":
        "decision-time witness for the same single snapshot",
}
# series-shaped decisive rows this host scan is allowed to return: the single-instant
# option snapshot files above (their row keys literally carry strike/expiry/option_type/
# risk_free_rate) plus the classified research-corpus index. C15/C15b assert the
# measured set is exactly this declaration and that the snapshot is one instant.
HOST_ALLOWED_DATA_HITS = {
    "%s|%s" % (NON_CANONICAL_OPTION_ROOT, rel)
    for rel in NON_CANONICAL_OPTION_FILES
    if rel.startswith(("raw/", "data/"))
} | {"/Users/hong/workspace/alpha-strategy-research|coverage_manifest.csv"}


def measure_non_canonical_option_material(root=NON_CANONICAL_OPTION_ROOT):
    """Read-only measurement of the host's single-instant Deribit BTC option snapshot."""
    out = {"root": root, "exists": os.path.isdir(root), "files": {},
           "used": False}
    if not out["exists"]:
        return out
    for rel in sorted(NON_CANONICAL_OPTION_FILES):
        path = os.path.join(root, rel)
        out["files"][rel] = {"exists": os.path.exists(path),
                             "bytes": os.path.getsize(path) if os.path.exists(path) else None}
    quotes_path = os.path.join(root, "data/real_valid_quotes.json")
    if os.path.exists(quotes_path):
        rows = _load_json(quotes_path)
        rows = rows if isinstance(rows, list) else rows.get("rows", [])
        out["quote_rows"] = len(rows)
        out["quote_row_keys"] = sorted(rows[0].keys()) if rows else []
        out["distinct_available_at"] = sorted({str(r.get("available_at")) for r in rows})
        out["distinct_expiries"] = sorted({str(r.get("expiry")) for r in rows})
        out["distinct_strikes"] = len({r.get("strike") for r in rows})
        out["venues"] = sorted({str(r.get("venue")) for r in rows})
        out["single_instant"] = len(out["distinct_available_at"]) == 1
        out["snapshot_instant"] = (out["distinct_available_at"][0]
                                   if len(out["distinct_available_at"]) == 1 else None)
    instr_path = os.path.join(root, "raw/deribit_get_instruments_btc.json")
    if os.path.exists(instr_path):
        doc = _load_json(instr_path)
        res = doc.get("result", []) if isinstance(doc, dict) else doc
        out["instrument_count"] = len(res)
        out["kind_values"] = sorted({str(i.get("kind")) for i in res})
    book_path = os.path.join(root, "raw/deribit_get_book_summary_btc.json")
    if os.path.exists(book_path):
        doc = _load_json(book_path)
        res = doc.get("result", []) if isinstance(doc, dict) else doc
        out["book_summary_count"] = len(res)
    readme_path = os.path.join(root, "README.md")
    readme = _read_text(readme_path)
    out["project_boundary_statement_present"] = (
        "Deribit public read-only BTC option snapshot" in readme)
    lineage_path = os.path.join(root, "data_lineage.json")
    if os.path.exists(lineage_path):
        lineage = _load_json(lineage_path)
        shortcuts = lineage.get("forbidden_shortcuts", [])
        out["forbidden_shortcuts"] = shortcuts
        out["latest_quote_as_historical_forbidden"] = (
            "latest quote as historical" in shortcuts)
    return out


def excluded_token_pass(raw=DEFAULT_RAW, roots=None, read_cap=512 * 1024):
    """Second, disclosed pass over the tokens the decisive vocabulary excludes.

    The exclusions are for generic English/code words; this pass re-probes them
    over the raw tree and the host roots and reports every hit, so an exclusion
    cannot silently hide a real option surface. A hit is 'data-shaped' when the
    containing file carries a data extension AND a token appears in its first
    row's keys.
    """
    rxs = {k: re.compile(v, re.I) for k, v in EXCLUDED_TOKENS.items()}
    out = {"tokens": sorted(rxs), "token_count": len(rxs), "raw": {}, "host": {},
           "raw_data_shaped": [], "host_data_shaped": [], "unclassified_host_hits": []}
    # raw tree
    raw_hits, raw_data_shaped = {}, []
    for rel, path in _walk(raw):
        text = _read_text(path)
        hits = [k for k, rx in rxs.items() if rx.search(text)]
        if not hits:
            continue
        for k in hits:
            raw_hits.setdefault(k, []).append(rel)
        if rel.endswith((".json", ".jsonl", ".csv", ".gz")):
            keys = _first_row_keys(path)
            if any(rxs[k].search(kk) for k in hits for kk in keys):
                raw_data_shaped.append({"entry": rel, "tokens": sorted(hits)})
    out["raw"] = {k: len(v) for k, v in sorted(raw_hits.items())}
    out["raw_hits"] = {k: sorted(set(v))[:20] for k, v in sorted(raw_hits.items())}
    out["raw_data_shaped"] = raw_data_shaped
    # host roots
    host_hits, host_data_shaped = {}, []
    for root in (roots or HOST_ROOTS):
        if not os.path.isdir(root):
            continue
        for rel, path in _walk(root):
            if not rel.endswith(HOST_TEXT_EXTS):
                continue
            text = _read_text(path, read_cap)
            hits = [k for k, rx in rxs.items() if rx.search(text)]
            if not hits:
                continue
            for k in hits:
                host_hits.setdefault(k, []).append("%s|%s" % (root, rel))
            if rel.endswith(HOST_DATA_EXTS):
                keys = _first_row_keys(path)
                if any(rxs[k].search(kk) for k in hits for kk in keys):
                    host_data_shaped.append({"entry": "%s|%s" % (root, rel),
                                             "tokens": sorted(hits), "row_keys_sample":
                                             keys[:8]})
    out["host"] = {k: len(v) for k, v in sorted(host_hits.items())}
    out["host_data_shaped"] = host_data_shaped
    out["host_hits_sample"] = {k: sorted(set(v))[:5] for k, v in sorted(host_hits.items())}
    return out


# --------------------------------------------------------------- checks -------
def _check(cid, name, ok, detail):
    return {"id": cid, "name": name, "ok": bool(ok), "detail": detail}


def _round_paths(results_root, family=FAMILY, round_id=ROUND):
    base = os.path.join(results_root, family)
    rnd = os.path.join(base, "rounds", round_id)
    return {"family": base, "round": rnd,
            "family_json": os.path.join(base, "family.json"),
            "spec": os.path.join(rnd, "round-spec.json"),
            "verdict": os.path.join(rnd, "verdict.json"),
            "attempts": os.path.join(rnd, "attempts"),
            "bundle": os.path.join(rnd, "survivor-bundle.json")}


def run_checks(results_root=DEFAULT_RESULTS, raw_root=DEFAULT_RAW, repo_root=DEFAULT_REPO,
               family=FAMILY, round_id=ROUND, task=TASK, raw=None, stores=None,
               host_scan=False, material=None):
    raw = raw if raw is not None else measure_raw(raw_root)
    stores = stores if stores is not None else (
        measure_host_stores() if host_scan else {"skipped": True})
    material = material if material is not None else measure_non_canonical_option_material()
    p = _round_paths(results_root, family, round_id)
    checks = []

    # ---- raw: identity / surfaces
    checks.append(_check(
        "C1", "canonical raw store identity",
        raw.get("config", {}).get("venue") == "BINANCE"
        and raw.get("config", {}).get("market_type") == "usdm_perp"
        and not raw.get("second_venue_present")
        and not raw.get("spot_market_present")
        and raw.get("dataset_families") == ["funding", "instruments", "klines"],
        "venue=%s market_type=%s venue_dirs=%s datasets=%s second_venue=%s spot=%s"
        % (raw.get("config", {}).get("venue"), raw.get("config", {}).get("market_type"),
           raw.get("venue_dirs"), raw.get("dataset_families"),
           raw.get("second_venue_present"), raw.get("spot_market_present"))))

    checks.append(_check(
        "C2", "instrument surface is four USD-M perpetuals, no option-shaped instrument field",
        raw.get("instrument_count") == 4
        and raw.get("instrument_ids") == ["BNBUSDT-PERP.BINANCE", "BTCUSDT-PERP.BINANCE",
                                          "ETHUSDT-PERP.BINANCE", "SOLUSDT-PERP.BINANCE"]
        and not raw.get("instrument_fields_requirement_shaped")
        and raw.get("instrument_types") == ["CryptoPerpetual"]
        and all("PERP" in i for i in raw.get("instrument_ids", [])),
        "count=%s ids=%s types=%s requirement_shaped_fields=%s"
        % (raw.get("instrument_count"), raw.get("instrument_ids"),
           raw.get("instrument_types"), raw.get("instrument_fields_requirement_shaped"))))

    checks.append(_check(
        "C3", "kline surface: 28 OHLCV datasets, no option/rate field anywhere",
        raw.get("kline_dataset_count") == 28
        and not raw.get("kline_key_violations")
        and raw.get("kline_sampled_files") == raw.get("kline_file_count")
        and sorted(raw.get("kline_symbols", [])) == ["BNBUSDT", "BTCUSDT", "ETHUSDT",
                                                     "SOLUSDT"]
        and len(raw.get("kline_intervals", [])) == 7,
        "datasets=%s files=%s sampled=%s key_violations=%s intervals=%s"
        % (raw.get("kline_dataset_count"), raw.get("kline_file_count"),
           raw.get("kline_sampled_files"), raw.get("kline_key_violations"),
           raw.get("kline_intervals"))))

    checks.append(_check(
        "C4", "funding surface: four symbols, funding_rate/funding_time_ms rows only, "
              "windows non-empty",
        sorted(raw.get("funding", {})) == ["BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
        and all(v["rows"] > 0 and v["first"] and v["last"]
                for v in raw.get("funding", {}).values())
        and raw.get("funding_field_sets", [[]])[0] == [
            "funding_price_source", "funding_rate", "funding_time_ms", "mark_price",
            "market_type", "rate_type", "symbol", "truth_status", "venue"],
        "symbols=%s rows=%s windows=%s fields=%s"
        % (sorted(raw.get("funding", {})),
           {k: v["rows"] for k, v in sorted(raw.get("funding", {}).items())},
           {k: [v["first"], v["last"]] for k, v in sorted(raw.get("funding", {}).items())},
           raw.get("funding_field_sets"))))

    # ---- raw: probe classification
    checks.append(_check(
        "C5", "every entry-name probe hit is classified",
        raw.get("probe_tokens_tested", 0) > 25 and not raw.get("probe_hits_unexplained_name"),
        "tokens_tested=%s hits=%s unexplained=%s"
        % (raw.get("probe_tokens_tested"), len(raw.get("probe_hits_classified_name", [])),
           raw.get("probe_hits_unexplained_name"))))

    checks.append(_check(
        "C6", "every payload probe hit is classified",
        not raw.get("probe_hits_unexplained_payload")
        and raw.get("probe_payload_files_scanned", 0) > 0,
        "files_scanned=%s hits=%s unexplained=%s"
        % (raw.get("probe_payload_files_scanned"),
           len(raw.get("probe_hits_classified_payload", [])),
           raw.get("probe_hits_unexplained_payload"))))

    # ---- raw: the record's decision surfaces, item by item
    def group_series_empty(grp):
        return not raw.get("decisive_group_series_hits", {}).get(grp)

    def decisive_present_payload():
        """Decisive tokens actually present in the raw as data (prose-allowed tokens count
        only when they occur outside their declared store documents)."""
        present = set(raw.get("decisive_zero_hits", []))
        present |= {t for t, v in raw.get("decisive_prose_audit", {}).items()
                    if v.get("offending")}
        return present

    def group_absent_everywhere(grp):
        toks = DECISIVE_GROUPS[grp]
        return (not raw.get("decisive_group_data_shape_hits", {}).get(grp)
                and not (set(toks) & decisive_present_payload())
                and not any(t in raw.get("decisive_name_hits", []) for t in toks))

    checks.append(_check(
        "C7", "no decisive requirement group is carried by a store-shaped row shape in the "
              "raw; the two declared prose occurrences are audited hit by hit",
        all(not v for v in raw.get("decisive_group_series_hits", {}).values())
        and not raw.get("decisive_zero_hits")
        and not raw.get("decisive_name_hits")
        and not raw.get("decisive_offending_hits"),
        "series_hits=%s decisive_zero_hits=%s decisive_name_hits=%s prose_audit=%s "
        "offending=%s"
        % (raw.get("decisive_group_series_hits"), raw.get("decisive_zero_hits"),
           raw.get("decisive_name_hits"), raw.get("decisive_prose_audit"),
           raw.get("decisive_offending_hits"))))

    checks.append(_check(
        "C8", "no option market surface (option rows, strike, maturity/expiry, implied "
              "volatility, IV curve, option chain, put-call ratio, greeks, straddle, "
              "variance swap, delta hedge, fixed maturity)",
        group_series_empty("option_market_surface")
        and group_absent_everywhere("option_market_surface"),
        "group_series=%s group_data_shape=%s tokens_present=%s"
        % (raw.get("decisive_group_series_hits", {}).get("option_market_surface"),
           raw.get("decisive_group_data_shape_hits", {}).get("option_market_surface"),
           sorted(set(DECISIVE_GROUPS["option_market_surface"]) & decisive_present_payload()))))

    checks.append(_check(
        "C9", "no option venue / venue-reference surface (Deribit, DVOL, BVIX)",
        group_series_empty("option_venue_surface")
        and group_absent_everywhere("option_venue_surface"),
        "group_series=%s group_data_shape=%s tokens_present=%s"
        % (raw.get("decisive_group_series_hits", {}).get("option_venue_surface"),
           raw.get("decisive_group_data_shape_hits", {}).get("option_venue_surface"),
           sorted(set(DECISIVE_GROUPS["option_venue_surface"]) & decisive_present_payload()))))

    checks.append(_check(
        "C10", "no risk-neutral state surface (risk-neutral density/variance, variance risk "
               "premium, VRP)",
        group_series_empty("rnd_state_surface") and group_absent_everywhere("rnd_state_surface"),
        "group_series=%s group_data_shape=%s"
        % (raw.get("decisive_group_series_hits", {}).get("rnd_state_surface"),
           raw.get("decisive_group_data_shape_hits", {}).get("rnd_state_surface"))))

    checks.append(_check(
        "C11", "no risk-free / discount surface (risk-free rate, SOFR, treasury, yield "
               "curve, discount factor, LIBOR)",
        group_series_empty("rate_discount_surface")
        and group_absent_everywhere("rate_discount_surface"),
        "group_series=%s group_data_shape=%s"
        % (raw.get("decisive_group_series_hits", {}).get("rate_discount_surface"),
           raw.get("decisive_group_data_shape_hits", {}).get("rate_discount_surface"))))

    checks.append(_check(
        "C12", "the record's registered price series IS present, but only as a USD-M "
               "perpetual (local analogue)",
        raw.get("btcusdt_daily_present") is True
        and raw.get("btcusdt_daily_span_days", 0) > 1000
        and "1d" in raw.get("kline_intervals", [])
        and "BTCUSDT/1d" in raw.get("kline_datasets", [])
        and raw.get("config", {}).get("market_type") == "usdm_perp"
        and not raw.get("spot_market_present"),
        "btcusdt_daily=%s span_days=%s intervals=%s window=%s market_type=%s spot=%s"
        % (raw.get("btcusdt_daily_present"), raw.get("btcusdt_daily_span_days"),
           raw.get("kline_intervals"), raw.get("daily_windows", {}).get("BTCUSDT"),
           raw.get("config", {}).get("market_type"), raw.get("spot_market_present"))))

    checks.append(_check(
        "C13", "store schema declares exactly the klines/funding/instruments datasets",
        raw.get("schema_dataset_names") == ["funding", "instruments", "klines"]
        and not any(raw.get(k) for k in ("documents_option_dataset",
                                         "documents_rate_dataset",
                                         "documents_rnd_dataset")),
        "schema_datasets=%s sections=%s"
        % (raw.get("schema_dataset_names"), raw.get("schema_dataset_sections"))))

    # ---- host: same vocabulary, no series-shaped decisive surface
    if host_scan:
        checks.append(_check(
            "C14", "host scan: store-shaped hits classified by class, row-shape probed",
            stores.get("totals", {}).get("files_scanned", 0) > 1000
            and not stores.get("unclassified_data_hits"),
            "totals=%s data_hits=%s unclassified=%s"
            % (stores.get("totals"), [d["rel"] for d in stores.get("data_hits", [])],
               stores.get("unclassified_data_hits"))))
        material_surfaces = []
        if any(COMPILED[t].search(k) for t in _decisive_tokens()
               for k in material.get("quote_row_keys", [])):
            material_surfaces = ["%s|%s" % (material.get("root"), rel)
                                 for rel in sorted(NON_CANONICAL_OPTION_FILES)]
        combined_surfaces = set(stores.get("series_shaped_decisive", [])) | set(material_surfaces)
        checks.append(_check(
            "C15", "host scan: the ONLY row surface on the host carrying decisive option/rate "
                   "tokens is the declared single-instant option snapshot (plus the classified "
                   "corpus index) - no option/rate time series exists",
            combined_surfaces <= HOST_ALLOWED_DATA_HITS
            and bool(material_surfaces)
            and set(stores.get("decisive_series_shaped_hits", [])) <= HOST_ALLOWED_DATA_HITS,
            "series_shaped_decisive=%s material_row_surfaces=%d combined=%d allowed=%d"
            % (stores.get("series_shaped_decisive"), len(material_surfaces),
               len(combined_surfaces), len(HOST_ALLOWED_DATA_HITS))))
        declared_material = _load_json(p["spec"]).get("non_canonical_option_material") \
            if os.path.exists(p["spec"]) else None
        declared_material = declared_material or {}
        mkeys = ("exists", "quote_rows", "instrument_count", "book_summary_count",
                 "distinct_available_at", "distinct_expiries", "distinct_strikes",
                 "venues", "kind_values", "single_instant", "snapshot_instant",
                 "project_boundary_statement_present",
                 "latest_quote_as_historical_forbidden", "used")
        mmism = sorted(k for k in mkeys if declared_material.get(k) != material.get(k))
        decisive_keys = sorted(k for k in material.get("quote_row_keys", [])
                               if any(COMPILED[t].search(k) for t in _decisive_tokens()))
        checks.append(_check(
            "C15b", "the host's only option material is a declared single-instant snapshot "
                    "whose row keys really carry the option vocabulary and which cannot express "
                    "the record's daily RND panel",
            bool(declared_material) and not mmism
            and material.get("single_instant") is True
            and material.get("used") is False
            and declared_material.get("used") is False
            and len(material.get("distinct_expiries", [])) >= 2
            and len(decisive_keys) >= 4
            and len(declared_material.get("cannot_satisfy_record_because", [])) >= 3,
            "declared_keys=%d mismatched=%s instants=%s used=%s expiries=%d "
            "decisive_row_keys=%s"
            % (len(declared_material), mmism, material.get("distinct_available_at"),
               declared_material.get("used"), len(material.get("distinct_expiries", [])),
               decisive_keys)))
        allowed = HOST_ALLOWED_DATA_HITS
        checks.append(_check(
            "C16", "host scan: decisive-group data-shaped hits are limited to the declared, "
                   "classified non-option stores",
            all(set(stores["decisive_groups"][g]["data_shape_hits"]) <= allowed
                for g in ("option_market_surface", "option_venue_surface",
                          "rnd_state_surface", "rate_discount_surface")),
            "option_market=%s option_venue=%s rnd_state=%s rate_discount=%s"
            % (stores["decisive_groups"]["option_market_surface"]["data_shape_hits"],
               stores["decisive_groups"]["option_venue_surface"]["data_shape_hits"],
               stores["decisive_groups"]["rnd_state_surface"]["data_shape_hits"],
               stores["decisive_groups"]["rate_discount_surface"]["data_shape_hits"])))

    # ---- round artifacts
    spec = _load_json(p["spec"]) if os.path.exists(p["spec"]) else {}
    verdict = _load_json(p["verdict"]) if os.path.exists(p["verdict"]) else {}
    fam = _load_json(p["family_json"]) if os.path.exists(p["family_json"]) else {}

    checks.append(_check(
        "C17", "round-spec exists, is bound to this family/card and registers "
               "PREREQUISITE_ABSENT",
        spec.get("family_id") == family and spec.get("round_id") == round_id
        and spec.get("kanban_task_id") == task and spec.get("expected") == "PREREQUISITE_ABSENT",
        "family_id=%s round_id=%s task=%s expected=%s"
        % (spec.get("family_id"), spec.get("round_id"), spec.get("kanban_task_id"),
           spec.get("expected"))))

    checks.append(_check(
        "C18", "round-spec: universe kept whole, no substitute market, no proxy panel",
        spec.get("universe_registration", {}).get("universe_shrunk_to_local_list") is False
        and spec.get("prerequisite_gate", {}).get("universe_shrunk") is False
        and spec.get("prerequisite_gate", {}).get("substitute_market_used") is False
        and spec.get("universe_registration", {}).get("required_data_available_local") is False,
        "universe_shrunk=%s substitute=%s required_data_available=%s"
        % (spec.get("universe_registration", {}).get("universe_shrunk_to_local_list"),
           spec.get("prerequisite_gate", {}).get("substitute_market_used"),
           spec.get("universe_registration", {}).get("required_data_available_local"))))

    # C18b: the required-data matrix, its recomputed status counts and the decisive set
    matrix = spec.get("universe_registration", {}).get("required_data_matrix", [])
    counts = {}
    for row in matrix:
        counts[row.get("status")] = counts.get(row.get("status"), 0) + 1
    decisive_rows = [r for r in matrix if r.get("status") in DECISIVE_REQUIRED_STATUSES]
    declared_absences = spec.get("prerequisite_gate", {}).get("decisive_absences", [])
    checks.append(_check(
        "C18b", "required-data matrix: complete, status counts recompute, decisive rows match "
                "the declared absences",
        len(matrix) >= 25
        and counts == spec.get("universe_registration", {}).get(
            "required_data_matrix_status_counts")
        and len(decisive_rows)
        == spec.get("universe_registration", {}).get("decisive_matrix_item_count")
        and sorted(r["item"] for r in decisive_rows) == sorted(declared_absences),
        "matrix=%d counts=%s decisive=%d declared_absences=%d"
        % (len(matrix), counts, len(decisive_rows), len(declared_absences))))

    # C18c: every claimed measurement re-derives from the live raw tree
    claimed = spec.get("universe_registration", {}).get("measured_available", {})
    live = {
        "entry_count": raw.get("entry_count"),
        "kline_file_count": raw.get("kline_file_count"),
        "kline_dataset_count": raw.get("kline_dataset_count"),
        "kline_symbols": raw.get("kline_symbols"),
        "kline_intervals": raw.get("kline_intervals"),
        "funding_rows": {k: v["rows"] for k, v in sorted(raw.get("funding", {}).items())},
        "funding_field_sets": raw.get("funding_field_sets"),
        "instrument_ids": raw.get("instrument_ids"),
        "instrument_types": raw.get("instrument_types"),
        "dataset_families": raw.get("dataset_families"),
        "second_venue_present": raw.get("second_venue_present"),
        "spot_market_present": raw.get("spot_market_present"),
        "probe_tokens_tested": raw.get("probe_tokens_tested"),
        "probe_payload_files_scanned": raw.get("probe_payload_files_scanned"),
        "decisive_zero_tokens_tested": raw.get("decisive_zero_tokens_tested"),
        "probe_entry_name_hits": raw.get("probe_entry_name_hits"),
        "probe_payload_hit_counts": raw.get("probe_payload_hit_counts"),
        "probe_hits_unexplained_payload": raw.get("probe_hits_unexplained_payload"),
        "decisive_zero_hits": raw.get("decisive_zero_hits"),
        "decisive_name_hits": raw.get("decisive_name_hits"),
        "decisive_offending_hits": raw.get("decisive_offending_hits"),
        "decisive_group_series_hits": raw.get("decisive_group_series_hits"),
        "schema_dataset_names": raw.get("schema_dataset_names"),
        "btcusdt_daily_window": [raw.get("daily_windows", {}).get("BTCUSDT", {}).get("first"),
                                 raw.get("daily_windows", {}).get("BTCUSDT", {}).get("last")],
        "btcusdt_daily_span_days": raw.get("btcusdt_daily_span_days"),
    }
    mismatched = sorted(k for k, v in claimed.items() if live.get(k) != v)
    missing = sorted(set(live) - set(claimed))
    checks.append(_check(
        "C18c", "round-spec: the claimed live measurement re-derives from the raw tree",
        bool(claimed) and not mismatched and not missing,
        "claimed_keys=%d mismatched=%s missing=%s" % (len(claimed), mismatched, missing)))

    checks.append(_check(
        "C19", "round-spec: nothing was launched (0 attempts, 0 run-specs, 0 sentinels)",
        spec.get("launch", {}).get("launched") is False
        and spec.get("launch", {}).get("attempts") == 0
        and spec.get("launch", {}).get("run_specs") == 0
        and spec.get("launch", {}).get("terminal_sentinels") == 0
        and not os.path.exists(p["attempts"])
        and not os.path.exists(p["bundle"]),
        "launch=%s attempts_dir=%s bundle=%s"
        % (spec.get("launch"), os.path.exists(p["attempts"]), os.path.exists(p["bundle"]))))

    checks.append(_check(
        "C20", "verdict.json states the contract-mandated terminal values",
        verdict.get("family_id") == family and verdict.get("round_id") == round_id
        and verdict.get("kanban_task_id") == task
        and verdict.get("verdict") == "TECHNICAL_INCOMPLETE"
        and verdict.get("performance_claimable") is False
        and verdict.get("failure", {}).get("layer") == "card-local"
        and verdict.get("failure", {}).get("class") == "data_window_invalid"
        and verdict.get("failure", {}).get("last_run_id") is None
        and verdict.get("run_id") is None,
        "verdict=%s claimable=%s failure=%s run_id=%s"
        % (verdict.get("verdict"), verdict.get("performance_claimable"),
           verdict.get("failure"), verdict.get("run_id"))))

    checks.append(_check(
        "C21", "verdict.json: failure is separated (infrastructure, not science) and empty "
               "by construction",
        verdict.get("attempts", {}).get("launched") == 0
        and verdict.get("attempts", {}).get("terminal_sentinels") == 0
        and verdict.get("attempts", {}).get("run_spec") is None
        and verdict.get("survivors") == []
        and verdict.get("survivor_bundle") is None
        and verdict.get("cohorts", {}).get("realized") == 0
        and verdict.get("evidence_run_ids") == []
        and verdict.get("yield", {}).get("yield_decision") == "STOP_TECHNICAL_INCOMPLETE",
        "attempts=%s cohorts=%s survivors=%s yield=%s"
        % (verdict.get("attempts"), verdict.get("cohorts"), verdict.get("survivors"),
           verdict.get("yield", {}).get("yield_decision"))))

    checks.append(_check(
        "C22", "round-spec: DCA domain is the complete 48-cell product with contract "
               "provenance classes",
        spec.get("dca_domain", {}).get("configs_per_cohort_per_grid") == 48
        and spec.get("dca_domain", {}).get("axes_status", {}).get("spacing_pct")
        == "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN"
        and spec.get("dca_domain", {}).get("base_quote") == 1000
        and spec.get("dca_domain", {}).get("base_quote_status")
        == "PROJECT_PRE_REGISTERED_CONSTANT"
        and spec.get("dca_domain", {}).get("axes", {}).get("spacing_pct")
        == [0.01, 0.02, 0.03, 0.04]
        and spec.get("dca_domain", {}).get("axes", {}).get("size_multiplier") == [1.0, 1.1]
        and spec.get("dca_domain", {}).get("axes", {}).get("breakeven_tp_pct")
        == [0.01, 0.02, 0.03]
        and spec.get("dca_domain", {}).get("axes", {}).get("invalidation_pct") == [0.05, 0.10],
        "configs=%s axes=%s base_quote=%s/%s"
        % (spec.get("dca_domain", {}).get("configs_per_cohort_per_grid"),
           spec.get("dca_domain", {}).get("axes"),
           spec.get("dca_domain", {}).get("base_quote"),
           spec.get("dca_domain", {}).get("base_quote_status"))))

    checks.append(_check(
        "C23", "round-spec: coverage + selector/disposition surfaces are empty by "
               "construction",
        spec.get("coverage", {}).get("cells_computed") == 0
        and spec.get("coverage", {}).get("cells_registered_total") == 0
        and len(spec.get("coverage", {}).get("phase_grids", [])) == 10
        and spec.get("selector_and_disposition", {}).get("cohorts_realized") == 0
        and spec.get("selector_and_disposition", {}).get("survivors") == []
        and spec.get("selector_and_disposition", {}).get("selector") == "cohort-selector-v1"
        and spec.get("selector_and_disposition", {}).get("disposition")
        == "cohort-disposition-v1",
        "coverage=%s cohorts=%s survivors=%s"
        % ({k: spec.get("coverage", {}).get(k) for k in
            ("cells_registered_total", "cells_per_grid", "cells_computed")},
           spec.get("selector_and_disposition", {}).get("cohorts_realized"),
           spec.get("selector_and_disposition", {}).get("survivors"))))

    checks.append(_check(
        "C24", "family.json is bound to this card/family and its fingerprint recomputes",
        fam.get("family_id") == family
        and fam.get("kanban_task_id") == task
        and fam.get("kanban_board") == BOARD
        and fam.get("semantic_fingerprint")
        == _sha256_text(fam.get("fingerprint_input") or ""),
        "family_id=%s task=%s fingerprint=%s"
        % (fam.get("family_id"), fam.get("kanban_task_id"), fam.get("semantic_fingerprint"))))

    checks.append(_check(
        "C25", "falsification battery registered unchanged (7 items, none lowered, none "
               "removed)",
        spec.get("falsification", {}).get("item_count") == 7
        and spec.get("falsification", {}).get("no_threshold_lowering") is True
        and spec.get("falsification", {}).get("no_item_removal") is True
        and spec.get("falsification", {}).get("falsification_status", "").startswith(
            "NOT_EXECUTED")
        and all(re.search(r"(?m)^%d\.[ \t]" % i,
                          spec.get("falsification", {}).get("record_falsification_verbatim", ""))
                for i in (1, 2, 3, 4, 5, 6, 7)),
        "items=%s status=%s"
        % (spec.get("falsification", {}).get("item_count"),
           spec.get("falsification", {}).get("falsification_status"))))

    checks.append(_check(
        "C26", "no performance number was fabricated anywhere in the round",
        _no_performance_claims(spec) and _no_performance_claims(verdict),
        "spec_claims=%s verdict_claims=%s"
        % (_no_performance_claims(spec), _no_performance_claims(verdict))))

    failed = [c for c in checks if not c["ok"]]
    return {"ok": not failed, "checks": checks, "failed": [c["id"] for c in failed],
            "raw_summary": {"entries": raw.get("entry_count"),
                            "kline_files": raw.get("kline_file_count"),
                            "probe_files": raw.get("probe_payload_files_scanned")},
            "stores_summary": stores.get("totals")}


PERFORMANCE_KEYS = ("sharpe", "cagr", "max_dd", "max_drawdown", "net_pnl", "gross_pnl",
                    "ending_equity", "annualized_return", "total_return", "profit_factor")

def _no_performance_claims(doc):
    """True when no realized (non-null, non-empty) performance metric is present."""
    bad = []

    def walk(node, path=()):
        if isinstance(node, dict):
            for k, v in node.items():
                if str(k).lower() in PERFORMANCE_KEYS and v not in (None, [], {}, 0, False, ""):
                    bad.append("%s.%s" % (".".join(path), k))
                walk(v, path + (str(k),))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, path + ("[%d]" % i,))

    walk(doc)
    return not bad


# ------------------------------------------------------- verbatim machinery ----
def _resolve_source_texts(repo_root=None, card_body_path=None, record_path=None,
                          board_db=None, task=TASK):
    repo_root = repo_root or DEFAULT_REPO
    record_path = record_path or DEFAULT_RECORD
    with open(record_path, encoding="utf-8", errors="replace") as fh:
        record = fh.read()
    with open(os.path.join(repo_root, "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md"),
              encoding="utf-8", errors="replace") as fh:
        contract = fh.read()
    try:
        sys.path.insert(0, os.path.join(repo_root, "runtime"))
        from production_handoff import LIFECYCLE_FOOTER as footer  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        footer = ""
    card = None
    if card_body_path and os.path.exists(card_body_path):
        with open(card_body_path, encoding="utf-8", errors="replace") as fh:
            card = fh.read()
    if card is None:
        db = board_db or BOARD_DB
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
                    board_db=None, task=TASK):
    src = _resolve_source_texts(repo_root=repo_root, card_body_path=card_body_path,
                                record_path=record_path, board_db=board_db, task=task)
    texts = {k: src[k] for k in ("card", "record", "contract", "footer")}
    spec = _load_json(spec_path)
    leaves = _walk_verbatim(spec)
    cmap = spec.get("excerpt_source_map", {})
    problems, misses, checked, seen = [], [], 0, set()
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


# ------------------------------------------------------------- self test -------
def _copy_family(src_results, dst_results, family=FAMILY, round_id=ROUND):
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


def _matrix_row(doc, item):
    """The required-data-matrix row whose `item` equals `item` (tamper helper)."""
    for row in doc["universe_registration"]["required_data_matrix"]:
        if row.get("item") == item:
            return row
    raise KeyError("no matrix row named %r" % item)


def self_test(results_root=DEFAULT_RESULTS, raw=None, stores=None, family=FAMILY,
              round_id=ROUND, task=TASK, material=None):
    """Non-vacuousness control: tampered copies must be refused by the named check."""
    tmp = tempfile.mkdtemp(prefix="t05ef4352-selftest-")
    try:
        base = _copy_family(results_root, tmp, family, round_id)
        spec_p = os.path.join(base, "rounds", round_id, "round-spec.json")
        verd_p = os.path.join(base, "rounds", round_id, "verdict.json")
        fam_p = os.path.join(base, "family.json")
        results = []

        def run():
            return run_checks(tmp, None, raw=raw, stores=stores, family=family,
                              round_id=round_id, task=task, material=material, host_scan=True)

        def v(name, expect, mutate):
            _copy_family(results_root, tmp, family, round_id)
            mutate(spec_p, verd_p, fam_p)
            res = run()
            failed = {c["id"] for c in res["checks"] if not c["ok"]}
            results.append({"variant": name, "expected": expect, "failed": sorted(failed),
                            "flipped": expect in failed})
            return res

        def m_spec(fn):
            return lambda s, vd, f: _mutate(s, fn)

        def m_verdict(fn):
            return lambda s, vd, f: _mutate(vd, fn)

        def m_family(fn):
            return lambda s, vd, f: _mutate(f, fn)

        v("verdict->PASS", "C20", m_verdict(lambda d: d.update({"verdict": "PASS"})))
        v("performance_claimable->true", "C20",
          m_verdict(lambda d: d.update({"performance_claimable": True})))
        v("failure.layer->shared-layer", "C20",
          m_verdict(lambda d: d["failure"].update({"layer": "shared-layer"})))
        v("failure.class->operator_stopped", "C20",
          m_verdict(lambda d: d["failure"].update({"class": "operator_stopped"})))
        v("run_id->fabricated", "C20", m_verdict(lambda d: d.update({"run_id": "r-fake"})))
        v("attempts.launched->1", "C21",
          m_verdict(lambda d: d["attempts"].update({"launched": 1})))
        v("survivors->one", "C21", m_verdict(lambda d: d.update({"survivors": ["x"]})))
        v("yield->CONTINUE", "C21",
          m_verdict(lambda d: d["yield"].update({"yield_decision": "CONTINUE"})))
        v("verdict.claimable->true via coverage", "C20",
          m_verdict(lambda d: d.update({"performance_claimable": True, "verdict":
                                        "TECHNICAL_INCOMPLETE"})))
        v("spec.expected->PREREQUISITE_PRESENT", "C17",
          m_spec(lambda d: d.update({"expected": "PREREQUISITE_PRESENT"})))
        v("spec.task->other card", "C17",
          m_spec(lambda d: d.update({"kanban_task_id": "t_00000000"})))
        v("universe_shrunk->true", "C18",
          m_spec(lambda d: d["universe_registration"].update(
              {"universe_shrunk_to_local_list": True})))
        v("substitute_market->true", "C18",
          m_spec(lambda d: d["prerequisite_gate"].update({"substitute_market_used": True})))
        v("required_data_available_local->true", "C18",
          m_spec(lambda d: d["universe_registration"].update(
              {"required_data_available_local": True})))
        v("matrix decisive row -> PRESENT (option chain present)", "C18b",
          m_spec(lambda d: _matrix_row(d, "record data: Deribit BTC option transaction/quote "
                                          "rows (venue Deribit)").update({"status": "PRESENT"})))
        v("matrix row dropped", "C18b",
          m_spec(lambda d: d["universe_registration"]["required_data_matrix"].pop(0)))
        v("matrix status_counts tampered", "C18b",
          m_spec(lambda d: d["universe_registration"]["required_data_matrix_status_counts"]
                .update({"ABSENT": 0})))
        v("claimed measurement tampered (entry_count)", "C18c",
          m_spec(lambda d: d["universe_registration"]["measured_available"].update(
              {"entry_count": 1})))
        v("claimed measurement key dropped", "C18c",
          m_spec(lambda d: d["universe_registration"]["measured_available"].pop(
              "decisive_zero_hits")))
        v("non-canonical snapshot declared as used", "C15b",
          m_spec(lambda d: d["non_canonical_option_material"].update({"used": True})))
        v("non-canonical snapshot instant count tampered", "C15b",
          m_spec(lambda d: d["non_canonical_option_material"].update(
              {"distinct_available_at": ["2026-08-28T12:19:42Z", "2026-08-29T12:19:42Z"]})))
        v("non-canonical sufficiency reasons stripped", "C15b",
          m_spec(lambda d: d["non_canonical_option_material"].update(
              {"cannot_satisfy_record_because": []})))
        v("launch.launched->true", "C19",
          m_spec(lambda d: d["launch"].update({"launched": True, "attempts": 1})))
        v("coverage.cells_computed->48", "C23",
          m_spec(lambda d: d["coverage"].update({"cells_computed": 48,
                                                 "cells_registered_total": 48})))
        v("selector.selector->v0", "C23",
          m_spec(lambda d: d["selector_and_disposition"].update(
              {"selector": "cohort-selector-v0"})))
        v("dca.axes.spacing_pct->trimmed", "C22",
          m_spec(lambda d: d["dca_domain"]["axes"].update({"spacing_pct": [0.01]})))
        v("dca.base_quote->500", "C22",
          m_spec(lambda d: d["dca_domain"].update({"base_quote": 500})))
        v("dca.provenance->user-fixed", "C22",
          m_spec(lambda d: d["dca_domain"]["axes_status"].update(
              {"spacing_pct": "USER_FIXED"})))
        v("falsification.item_count->6", "C25",
          m_spec(lambda d: d["falsification"].update({"item_count": 6})))
        v("falsification.status->EXECUTED", "C25",
          m_spec(lambda d: d["falsification"].update({"falsification_status": "EXECUTED"})))
        v("falsification.item7 stripped from verbatim", "C25",
          m_spec(lambda d: d["falsification"].update(
              {"record_falsification_verbatim":
               d["falsification"]["record_falsification_verbatim"].split(
                   "7. Test whether the full density classifier")[0]})))
        v("fabricated sharpe in spec", "C26",
          m_spec(lambda d: d.update({"measured": {"sharpe": 1.9}})))
        v("fabricated net_pnl in verdict", "C26",
          m_verdict(lambda d: d.update({"realized": {"net_pnl": 4210.0}})))
        v("family.fingerprint->wrong", "C24",
          m_family(lambda d: d.update({"semantic_fingerprint": "sha256:" + "0" * 64})))
        v("family.task->other card", "C24",
          m_family(lambda d: d.update({"kanban_task_id": "t_00000000"})))
        # benign variant: an extra non-asserted field must NOT flip anything
        _copy_family(results_root, tmp, family, round_id)
        _mutate(spec_p, lambda d: d.update({"operator_note": "benign addition"}))
        benign = run()
        results.append({"variant": "benign: extra note field", "expected": None,
                        "failed": benign["failed"], "flipped": False})
        ok = all(r["flipped"] for r in results if r["expected"]) and not benign["failed"]
        return {"ok": ok, "variants": results,
                "flipped": sum(1 for r in results if r["flipped"])}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------ raw fixture control ----
def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def raw_fixture_control(results_root=DEFAULT_RESULTS, raw=None, stores=None,
                        family=FAMILY, round_id=ROUND, task=TASK, material=None):
    """Plant what this record needs into a temp raw tree; the raw-side checks must flip."""
    tmp = tempfile.mkdtemp(prefix="t05ef4352-fixture-")
    try:
        fixture = os.path.join(tmp, "raw")
        _write(os.path.join(fixture, "_meta/CONFIG.json"), json.dumps(
            {"venue": "BINANCE", "market_type": "usdm_perp", "symbols": ["BTCUSDT"]}))
        _write(os.path.join(fixture, "_meta/SCHEMA.md"),
               "# schema\n\n## Dataset: klines\n\n## Dataset: option_chain\n\n"
               "## Dataset: risk_free_curve\n\n## Dataset: risk_neutral_density\n")
        # the record's decision variable: a Deribit option chain with strikes,
        # expiries, implied vols and call/put flags
        _write(os.path.join(fixture, "deribit/options/btc_option_chain_2022-01.jsonl"),
               json.dumps({"timestamp": 1640995200000, "venue": "deribit",
                           "instrument": "BTC-27JAN22-50000-C", "option_type": "call",
                           "strike": 50000, "maturity_days": 27, "implied_vol": 0.71,
                           "option_price": 0.0412, "underlying_price": 47310.5,
                           "open_interest": 812.5, "bid": 0.0409, "ask": 0.0415}) + "\n")
        # fixed-maturity 9/27/45-day implied-volatility curves
        _write(os.path.join(fixture, "deribit/iv_curves/fixed_maturity_iv.csv"),
               "date,maturity_9d_iv,maturity_27d_iv,maturity_45d_iv,fixed_maturity_note\n"
               "2022-01-02,0.63,0.71,0.74,interpolated\n")
        # the risk-free rate proxy the record requires
        _write(os.path.join(fixture, "rates/risk_free_curve.csv"),
               "date,sofr,treasury_1m_yield,discount_factor,risk_free_note\n"
               "2022-01-02,0.0005,0.0006,0.99996,proxy\n")
        # risk-neutral density panel + variance risk premium state series
        _write(os.path.join(fixture, "deribit/rnd/rnd_panel.jsonl"),
               json.dumps({"date": "2022-01-02", "risk_neutral_density": [0.1, 0.3, 0.4],
                           "risk_neutral_variance": 0.46, "variance_risk_premium": 0.17,
                           "regime": "LV"}) + "\n")
        # a BTC spot index series (the record's first required-data item)
        _write(os.path.join(fixture, "index/btc_spot_index.csv"),
               "date,spot_index,index_price\n2022-01-02,47310.5,47310.5\n")
        res = run_checks(results_root, fixture, raw=None, stores=stores, family=family,
                         round_id=round_id, task=task, material=material)
        flipped = sorted(res["failed"])
        expected_subset = {"C1", "C2", "C5", "C6", "C7", "C8", "C9", "C10", "C11", "C13",
                           "C18c"}
        return {"ok": expected_subset.issubset(set(flipped)), "flipped": flipped,
                "expected_subset": sorted(expected_subset),
                "not_flipped": sorted(expected_subset - set(flipped)),
                "fixture_root": fixture}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------- main ----
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--measure-only", action="store_true")
    ap.add_argument("--verify-verbatim", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--raw-fixture-control", action="store_true")
    ap.add_argument("--excluded-token-pass", action="store_true")
    ap.add_argument("--host-scan", action="store_true")
    ap.add_argument("--results-root", default=DEFAULT_RESULTS)
    ap.add_argument("--raw-root", default=DEFAULT_RAW)
    ap.add_argument("--repo-root", default=DEFAULT_REPO)
    ap.add_argument("--card-body")
    args = ap.parse_args(argv)

    if args.measure_only:
        print(json.dumps({"raw": measure_raw(args.raw_root),
                          "host": measure_host_stores() if args.host_scan else "skipped"},
                         ensure_ascii=False, indent=1))
        return 0

    if args.excluded_token_pass:
        res = excluded_token_pass(args.raw_root)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0

    if args.verify_verbatim:
        p = _round_paths(args.results_root)
        res = verify_verbatim(p["spec"], repo_root=args.repo_root,
                              card_body_path=args.card_body)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0 if res["ok"] else 1

    if args.self_test or args.raw_fixture_control:
        raw = measure_raw(args.raw_root)
        material = measure_non_canonical_option_material()
        # the self-test tamper set includes host-scan checks (C15/C15b/C16), so the host
        # scan must be measured once and reused across every variant
        stores = measure_host_stores() if (args.host_scan or args.self_test) else {"skipped": True}
        if args.self_test:
            res = self_test(args.results_root, raw=raw, stores=stores, material=material)
        else:
            res = raw_fixture_control(args.results_root, raw=raw, stores=stores,
                                      material=material)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0 if res["ok"] else 1

    res = run_checks(args.results_root, args.raw_root, args.repo_root,
                     raw=measure_raw(args.raw_root),
                     stores=measure_host_stores() if args.host_scan else {"skipped": True},
                     host_scan=args.host_scan,
                     material=measure_non_canonical_option_material())
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        for c in res["checks"]:
            print("%-4s %-6s %s" % (c["id"], "PASS" if c["ok"] else "FAIL", c["name"]))
            if not c["ok"]:
                print("      detail: %s" % json.dumps(c["detail"], ensure_ascii=False)[:400])
        print("overall: %s failed=%s" % ("PASS" if res["ok"] else "FAIL", res["failed"]))
        print("raw: %s" % json.dumps(res["raw_summary"], ensure_ascii=False))
        print("stores: %s" % json.dumps(res["stores_summary"], ensure_ascii=False))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
