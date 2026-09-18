#!/usr/bin/env python3
"""Prerequisite-gate read-back checker for the family

    continuous-macro-timing-growth-defensive-style-allocation-2026-09-02

Card t_20bec925 terminalises this family as TECHNICAL_INCOMPLETE because the
record's required data / market is not in the canonical raw:

  * the record trades a **growth-versus-defensive style spread** built from ten
    US-listed ETFs (growth: QQQ, XLK, VGT, SPYG, VUG; defensive: SCHD, VYM,
    VTV, FDVV, COWZ) on NYSE / NASDAQ / CBOE, and it conditions the allocation
    on four macro-market series: the 10-year Treasury yield (TNX), the CBOE
    VIX index, SPY drawdown depth and the Moody's Baa corporate yield
    (FRED: BAA10Y);
  * the record's own `Crypto portability` paragraph is `adapted / unproven` and
    prescribes the crypto mapping explicitly: the growth basket becomes
    high-beta L1/DeFi tokens (SOL, AVAX, NEAR, SUI), the defensive basket
    becomes **Bitcoin + USD stablecoins**, and the macro proxies become
    *annualized crypto perpetual funding rates* for TNX, **Deribit DVOL for
    VIX**, and BTC drawdown for SPY drawdown;
  * the canonical raw holds one venue (BINANCE) of USD-M perpetuals, four fixed
    contracts (BTCUSDT/ETHUSDT/BNBUSDT/SOLUSDT) and exactly three dataset
    families - OHLCV k-lines at 5m/15m/30m/1h/4h/1d/1w, funding rows and
    instrument metadata. There is **no implied-volatility series of any kind**
    (no DVOL, no options chain, no volatility index) and no US-equity venue, no
    ETF price panel and no Treasury/credit/Baa series;
  * the registered score is *not* computable without the volatility family: two
    of its five direction-normalized inputs (`vh_t = z(VIXPercentile_756)`,
    `vr_t = -z(dVIX_21)`) enter the softplus components (`HighVIX`, `VIXRelief`,
    `LowVIX`) and three of the four non-linear interaction terms
    (`i1 = r*vh`, `i2 = HighVIX*VIXRelief`, `i3/i4 = GrowthExt*LowVIX[*]`), so
    without an implied-vol series the StressScore and CrowdedScore collapse;
  * substituting a realized-volatility measure for the prescribed DVOL would
    replace the information set of the registered signal (and falsification
    item 1 operates on the *actual* TNX / VIX / SPYDrawdown series), i.e. it
    would be a new hypothesis - the card forbids an approximate dataset, a
    proxy market or a rewritten hypothesis, and forbids shrinking the universe
    to manufacture executability;
  * no legitimate local universe can supply the missing series either: DVOL is
    an external index, not a function of the traded universe, so `required_data`
    is not merely "a different market" - one required data-family is absent for
    every possible local universe. A missing prerequisite is not a rejection:
    it is a measured technical terminal.

This script exists so an independent reader can re-derive that determination
from the live filesystem instead of trusting prose:

  * C1-C6  re-measure the canonical raw structurally: store identity and
    dataset families, the instrument surface (and the fact that every local
    instrument is a perpetual with no maturity/option/implied-volatility
    field), the k-line surface (intervals, files, rows, row key set asserted on
    every file), the *finest resolved interval*, the funding surface and the
    measured windows;
  * C7-C11 probe the raw tree for this record's requirement vocabulary
    (implied-volatility / options market data, US-equity venues, the record's
    ETF and macro series, the portability alternates) by entry name AND payload
    content, assert the decisive groups are carried by NO raw row shape, and
    keep a positive control that the local store vocabulary IS present;
  * C12 audits the declared prose allowances: each declared occurrence must
    still be there, with the declared count, and the declarations must cover
    EVERY decisive-token occurrence in the raw tree (total hits == declared);
  * C13 re-measures the host's other stores: every series-shaped hit is
    classified by a declared rule and the measured substitutes are enumerated
    with numbers and `used=false`;
  * C14-C27 re-read the round's immutable artifacts and assert they state
    exactly the contract-mandated terminal values (verdict, layer, class, yield
    decision, zero attempts, null run_id), that nothing was ever submitted (no
    run-spec, no attempt directory, no terminal sentinel), that the decisive
    requirement matrix still carries the registered statuses item by item, that
    the claimed measurement block re-derives from a LIVE measurement, that the
    DCA registration keeps the contract 7.2 provenance classes plus the
    complete 48-cell product, that the falsification battery is restored to the
    record's full item count, and that no performance number was fabricated;
  * `--verify-verbatim` resolves every `*_verbatim` leaf of the persisted
    round-spec against its declared source (card / record / contract / footer)
    and re-checks the excerpt digest map, independent of the authoring run;
  * `--excluded-token-pass` re-probes the generic tokens the decisive
    vocabulary deliberately leaves out (`vol`, `iv`, `call`, `put`, `delta`,
    `vega`, `gamma`, `index`, `rate`, `macro`), so an exclusion cannot hide a
    hit;
  * `--self-test` builds tampered copies in fresh temp directories and asserts
    the named check refuses each variant (non-vacuousness control), including
    one benign control that must NOT flip;
  * `--raw-fixture-control` builds a temp raw tree that carries what this
    record would need (a Deribit DVOL implied-volatility series, an options
    chain with strikes/expiries/IV, a US-equity ETF panel and a Treasury/Baa
    series) and asserts the raw-side checks flip to FAIL;
  * `--host-scan` re-runs the house-wide search.

Read-only: it never writes inside the results tree or the raw tree.

Usage:
    python3 runtime/continuous_macro_timing_growth_defensive_style_allocation_prerequisite_check.py [--json]
    python3 runtime/continuous_macro_timing_growth_defensive_style_allocation_prerequisite_check.py --measure-only
    python3 runtime/continuous_macro_timing_growth_defensive_style_allocation_prerequisite_check.py --verify-verbatim
    python3 runtime/continuous_macro_timing_growth_defensive_style_allocation_prerequisite_check.py --self-test
    python3 runtime/continuous_macro_timing_growth_defensive_style_allocation_prerequisite_check.py --raw-fixture-control
    python3 runtime/continuous_macro_timing_growth_defensive_style_allocation_prerequisite_check.py --excluded-token-pass
    python3 runtime/continuous_macro_timing_growth_defensive_style_allocation_prerequisite_check.py --host-scan

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

FAMILY = "continuous-macro-timing-growth-defensive-style-allocation-2026-09-02"
ROUND = FAMILY + "-r1"
TASK = "t_20bec925"
BOARD = "quant-strategy-research"
DEFAULT_RAW = "/Volumes/ExpansionDrive/market-data-raw"
DEFAULT_RESULTS = "/Volumes/ExpansionDrive/qlib-results"
DEFAULT_REPO = "/Users/hong/workspace/quant-runtime-pipeline"
DEFAULT_RECORD = "/Users/hong/.hermes/wiki/quant/%s.md" % FAMILY
BOARD_DB = os.path.join(os.path.expanduser("~"), ".hermes/kanban/boards", BOARD,
                        "kanban.db")
PROBE_MAX_DEPTH = 8
# Payload probe budget per file: the k-line corpus is ~1,600 gzip files, so the
# head of each file is probed (declared) while every text file of the _meta /
# _tools / funding / instruments surface is read whole (they are all < 2 MB).
PROBE_READ_CAP = 262144
PROBE_READ_CAP_OTHER = 2097152

# Keys whose presence in a *number* position would be a performance claim.
PERFORMANCE_KEY_HINTS = ("pnl", "sharpe", "cagr", "max_dd", "max_drawdown",
                         "ending_equity", "annualized", "return_pct", "hit_rate",
                         "profit_factor", "sortino", "calmar")

# --- the record's requirement vocabulary -------------------------------------
# ONE unified token list: nothing is excluded from the probe. Generic tokens that
# cannot discriminate a market store are separately re-probed by
# `--excluded-token-pass` so the exclusion cannot hide a hit.
PATTERNS = {
    # the record's conditioning series: CBOE VIX, and the crypto replacement the
    # record's own portability paragraph prescribes for it (Deribit DVOL)
    "vix": r"\bvix\b",
    "dvol": r"\bdvol\b",
    "implied_vol": r"\bimplied[-_ ]?vol(?:atility)?\b",
    "volatility_index": r"\bvolatility[-_ ]?index(?:es)?\b",
    "deribit": r"\bderibit\b",
    "iv_surface": r"\biv[-_ ]?surface\b|\bvol(?:atility)?[-_ ]?surface\b",
    # options market data: any surface that could carry an implied volatility
    "option": r"\boptions?\b",
    "strike": r"\bstrikes?\b",
    "expiry": r"\bexpir(?:y|ies|ation|ations)\b",
    "open_interest": r"\bopen[-_ ]?interest\b",
    "greeks": r"\bgreeks?\b",
    "put_call": r"\bput[-_ ]?call\b",
    # the record's venue: US equity and index markets
    "nyse": r"\bnyse\b",
    "nasdaq": r"\bnasdaq\b",
    "cboe": r"\bcboe\b",
    "us_equity": r"\bus[-_ ]?equit(?:y|ies)\b",
    # the record's instruments (growth / defensive ETFs + SPY)
    "qqq": r"\bqqq\b",
    "xlk": r"\bxlk\b",
    "vgt": r"\bvgt\b",
    "spyg": r"\bspyg\b",
    "vug": r"\bvug\b",
    "schd": r"\bschd\b",
    "vym": r"\bvym\b",
    "vtv": r"\bvtv\b",
    "fdvv": r"\bfdvv\b",
    "cowz": r"\bcowz\b",
    "spy": r"\bspy\b",
    # the record's remaining macro series
    "tnx": r"\btnx\b",
    "treasury": r"\btreasur(?:y|ies)\b",
    "ten_year_yield": r"\b10[-_ ]?year[-_ ]?(?:treasury[-_ ]?)?yield\b",
    "baa": r"\bbaa\b",
    "moody": r"\bmoody(?:'s)?\b",
    "credit_spread": r"\bcredit[-_ ]?spreads?\b",
    # the record's portability alternates (Aave USDC supply APY) and the
    # portability baskets' named tokens
    "aave": r"\baave\b",
    "lending_apy": r"\blending[-_ ]?(?:rate|apy|yield)\b|\bsupply[-_ ]?apy\b",
    "avax": r"\bavax\b",
    "near_protocol": r"\bnear(?:usdt|[-_ ]?protocol)\b",
    "sui": r"\bsui\b",
    "stablecoin": r"\bstablecoins?\b|\busdc\b",
    # local store vocabulary (must remain present: these DO exist)
    "perpetual": r"\bperp(?:etual)?s?\b",
    "funding": r"\bfunding\b",
    "klines": r"\bklines?\b",
    "binance": r"\bbinance\b",
    "usdt": r"\busdt\b",
}

# Cheap literal pre-filter: a text that fails every literal cannot match any
# token, so the (expensive) boundary-aware regexes only run on candidates.
LITERALS = {
    "vix": ("vix",), "dvol": ("dvol",), "implied_vol": ("implied",),
    "volatility_index": ("volatility index", "volatility_index", "volatility-index"),
    "deribit": ("deribit",), "iv_surface": ("iv surface", "iv_surface", "vol surface",
                                            "volatility surface"),
    "option": ("option",), "strike": ("strike",), "expiry": ("expir",),
    "open_interest": ("open interest", "open_interest", "openinterest"),
    "greeks": ("greek",), "put_call": ("put call", "put_call", "put-call"),
    "nyse": ("nyse",), "nasdaq": ("nasdaq",), "cboe": ("cboe",),
    "us_equity": ("us equity", "us_equity", "us-equity"),
    "qqq": ("qqq",), "xlk": ("xlk",), "vgt": ("vgt",), "spyg": ("spyg",),
    "vug": ("vug",), "schd": ("schd",), "vym": ("vym",), "vtv": ("vtv",),
    "fdvv": ("fdvv",), "cowz": ("cowz",), "spy": ("spy",),
    "tnx": ("tnx",), "treasury": ("treasur",),
    "ten_year_yield": ("10 year", "10-year", "10_year"),
    "baa": ("baa",), "moody": ("moody",), "credit_spread": ("credit spread", "credit_spread",
                                                            "credit-spread"),
    "aave": ("aave",), "lending_apy": ("lending rate", "lending apy", "supply apy"),
    "avax": ("avax",), "near_protocol": ("nearusdt", "near protocol", "near_protocol"),
    "sui": ("sui",), "stablecoin": ("stablecoin", "usdc"),
    "perpetual": ("perp",), "funding": ("funding",), "klines": ("kline",),
    "binance": ("binance",), "usdt": ("usdt",),
}
ALL_LITERALS = tuple(sorted({lit for lits in LITERALS.values() for lit in lits}))

DECISIVE_GROUPS = {
    # THE decisive group: the implied-volatility series the registered score is
    # conditioned on (the record's VIX slot) and its prescribed crypto
    # replacement (Deribit DVOL)
    "volatility_index_series": ["vix", "dvol", "implied_vol", "volatility_index",
                                "deribit", "iv_surface"],
    # any options market surface that could carry an implied volatility
    "options_market_data": ["option", "strike", "expiry", "open_interest",
                            "greeks", "put_call"],
    # the record's venue
    "us_equity_venue": ["nyse", "nasdaq", "cboe", "us_equity"],
    # the record's traded instruments
    "record_instrument_set": ["qqq", "xlk", "vgt", "spyg", "vug", "schd", "vym",
                              "vtv", "fdvv", "cowz", "spy"],
    # the record's remaining macro series (TNX, BAA10Y)
    "record_macro_series": ["tnx", "treasury", "ten_year_yield", "baa", "moody",
                            "credit_spread"],
    # the portability paragraph's alternates and named basket tokens
    "portability_alternate_provider": ["aave", "lending_apy"],
    "portability_growth_basket_tokens": ["avax", "near_protocol", "sui"],
    "portability_defensive_store": ["stablecoin"],
}
DECISIVE_ZERO_TOKENS = sorted({t for toks in DECISIVE_GROUPS.values() for t in toks})
# Positive control: the local store's own vocabulary MUST be measured present,
# otherwise "everything is absent" would be an artifact of a blind probe.
PRESENT_TOKENS = ["perpetual", "funding", "klines", "binance", "usdt"]

# Decisive tokens with DECLARED, hand-inspected occurrences inside the store's
# own documents. Every one of these is a record of the ABSENCE (a search that
# found nothing), a dataset name that is explicitly NOT part of this store, or a
# CLI table header - never an implied-volatility / options / equity data
# surface. `count` is re-measured live: a declaration is refused when the count
# moves, and any occurrence NOT covered here is offending.
DECISIVE_PROSE_ALLOWED = {
    "option": {
        "_meta/INVENTORY.md": {
            "count": 1,
            "why": "canonical_store_inventory_prose: the LEAN source tree named in "
                   "section B ('data/{equity,future,option,forex,cfd,...}') and "
                   "explicitly NOT migrated; the directory itself no longer exists "
                   "on this host (measured)",
        },
        "_tools/README.md": {
            "count": 1,
            "why": "updater_readme_prose: the 'Options:' table header of the CLI flag "
                   "reference of the raw store's own updater",
        },
    },
    "open_interest": {
        "_meta/INVENTORY.md": {
            "count": 1,
            "why": "canonical_store_inventory_prose: the Phase-1 mdfind query token "
                   "list ('... / openInterest / tardis / market-data') whose recorded "
                   "result is that no such store was found",
        },
    },
}

# The exact sentence by which the store documents its own coverage limit.
COVERAGE_LIMIT_SENTENCE = ("the initial\nimport came from a bar store that kept only OHLCV")

# ------------------------------------------------------------------ host -------
# Roots re-scanned by --host-scan: every place an implied-volatility / options /
# US-equity store could plausibly live on this host.
HOST_ROOTS = [
    "/Users/hong/workspace/phase11-options-volatility",
    "/Users/hong/workspace/phase5-crypto-derivatives",
    "/Users/hong/workspace/phase2-market-data",
    "/Users/hong/workspace/phase7-alpha-research",
    "/Users/hong/workspace/phase10-pit-bitemporal",
    "/Users/hong/workspace/a1-1-phase9-pit-membership-20260830",
    "/Users/hong/workspace/a1-usdm-pit-lifecycle-20260830",
    "/Users/hong/workspace/alpha-strategy-research",
    "/Users/hong/workspace/quant-runtime-pipeline",
    "/Users/hong/workspace/qlib-apple-container",
    "/Users/hong/workspace/microsoft-quant-stack",
    "/Volumes/ExpansionDrive/daily-crypto-brief",
    "/Volumes/ExpansionDrive/qlib-results",
    "/Users/hong/.hermes/wiki/quant",
]
HOST_TEXT_EXTS = (".csv", ".jsonl", ".json", ".txt", ".md", ".tsv", ".py", ".sh",
                  ".yaml", ".yml", ".html", ".log")
HOST_ROW_EXTS = (".csv", ".tsv", ".jsonl", ".jsonl.gz", ".parquet", ".db", ".sqlite")
HOST_SKIP_DIRS = frozenset({".git", "__pycache__", "node_modules", ".venv", "venv",
                            ".pytest_cache", "__MACOSX", "site-packages",
                            ".mypy_cache", ".ruff_cache", ".kanban-scratch"})

# Every hit resolves through this ordered rule chain and the generic fallbacks at
# the end: a SERIES-shaped hit (a real row-oriented file) must be named by a
# specific rule, while prose/source hits fall back to the declared generic
# classes, which is truthful - a markdown corpus or a Python file cannot be a
# market-data store.
HOST_CLASS_RULES = [
    (r"^/Users/hong/workspace/phase11-options-volatility/source_cache/", "vendor_documentation"),
    (r"^/Users/hong/workspace/phase11-options-volatility/", "options_volatility_harness"),
    (r"^/Users/hong/workspace/phase5-crypto-derivatives/", "crypto_derivatives_harness"),
    (r"^/Users/hong/workspace/phase2-market-data/", "market_data_harness"),
    (r"^/Users/hong/workspace/phase7-alpha-research/source_cache/", "vendor_documentation"),
    (r"^/Users/hong/workspace/phase7-alpha-research/", "alpha_research_harness"),
    (r"^/Users/hong/workspace/phase10-pit-bitemporal/", "pit_bitemporal_store"),
    (r"^/Users/hong/workspace/a1-[0-9-]+[^|]*/", "pit_membership_harness"),
    (r"^/Users/hong/workspace/a1-usdm-pit-lifecycle[^|]*/", "pit_lifecycle_harness"),
    (r"^/Users/hong/workspace/alpha-strategy-research/", "alpha_research_harness"),
    (r"^/Users/hong/workspace/quant-runtime-pipeline/runtime/", "quant_pipeline_runtime_source"),
    (r"^/Users/hong/workspace/quant-runtime-pipeline/container/", "quant_pipeline_container_source"),
    (r"^/Users/hong/workspace/quant-runtime-pipeline/evidence/", "quant_pipeline_evidence"),
    (r"^/Users/hong/workspace/quant-runtime-pipeline/homepage/", "quant_pipeline_dashboard_source"),
    (r"^/Users/hong/workspace/quant-runtime-pipeline/", "quant_pipeline_repo_docs"),
    (r"^/Users/hong/workspace/qlib-apple-container/scripts/", "qlib_container_harness_source"),
    (r"^/Users/hong/workspace/qlib-apple-container/", "qlib_container_host_tree"),
    (r"^/Users/hong/workspace/microsoft-quant-stack/", "retired_qlib_china_a_share_stack"),
    (r"^/Volumes/ExpansionDrive/daily-crypto-brief/", "daily_brief_artifacts"),
    (r"^/Volumes/ExpansionDrive/qlib-results/_handoff/", "results_handoff_bodies"),
    (r"^/Volumes/ExpansionDrive/qlib-results/", "results_round_artifacts"),
    (r"^/Users/hong/\.hermes/wiki/quant/", "wiki_brain_quant_record"),
]
# Classes that WOULD satisfy the record's requirement (a stored implied-volatility
# or options surface, or a US-equity panel). Deliberately empty: no measured host
# class is such a store.
STORE_HOST_CLASSES = frozenset()
NON_CANONICAL_NOTE = (
    "the host scan enumerates every decisive-token hit by its own rule; the round "
    "used none of them, and no hit is an implied-volatility series, an options "
    "chain or a US-equity price panel"
)
# One house-wide scan per process: `--self-test` runs the full check suite once
# per tampered copy, so an unmemoised scan would turn a 25-variant control into
# an hour of walking.
_HOST_SCAN_CACHE = {}


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
    """Compile a word-boundary pattern so that separator-joined identifiers match.

    Python's ``\\b`` treats ``_`` as a word character, so ``\\bdvol\\b`` would still
    match inside ``btc_dvol_close`` only by luck and ``\\bcontract[-_ ]?month\\b``
    would not match a column named ``contract_month``. Replacing the leading /
    trailing ``\\b`` with lookarounds that treat ``_``, ``-`` and ``.`` as
    separators catches compound embeddings while still refusing ordinary English
    false positives.
    """
    alts = _split_alts(pat)
    if len(alts) > 1:
        return re.compile("|".join("(?:%s)" % _boundary_aware(a).pattern for a in alts),
                          re.I)
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
COMBINED = re.compile("|".join("(?P<g%d>%s)" % (i, COMPILED[k].pattern)
                               for i, k in enumerate(PATTERNS)), re.I)
TOKEN_ORDER = list(PATTERNS)


def _probe_text(text):
    """One pass over a text; returns {token: count} for every token with a hit."""
    low = text.lower()
    if not any(lit in low for lit in ALL_LITERALS):
        return {}
    out = {}
    for m in COMBINED.finditer(text):
        tok = TOKEN_ORDER[int(m.lastgroup[1:])]
        out[tok] = out.get(tok, 0) + 1
    return out


def _last_line(path):
    txt = _read_text(path)
    if not txt:
        return None
    for line in reversed(txt.splitlines()):
        if line.strip():
            return line
    return None


def _first_line(path, cap=16384):
    try:
        if path.endswith(".gz"):
            with gzip.open(path, "rb") as fh:
                data = fh.readline(cap)
        else:
            with open(path, "rb") as fh:
                data = fh.readline(cap)
    except Exception:
        return None
    return data.decode("utf-8", "replace").strip()


def _keys_of(line):
    try:
        return sorted(json.loads(line).keys())
    except Exception:
        return None


def _host_class(path):
    for rx, name in HOST_CLASS_RULES:
        if re.search(rx, path):
            return name
    return None


# ------------------------------------------------------------------ raw --------
def measure_raw(raw=DEFAULT_RAW, depth=PROBE_MAX_DEPTH):
    """Re-measure the canonical raw store. Read-only; bounded reads are declared."""
    if not os.path.isdir(raw):
        raise SystemExit("RAW ROOT MISSING: %s" % raw)
    out = {
        "raw_root": raw,
        "probe_max_depth": depth,
        "probe_read_cap_bytes": PROBE_READ_CAP,
        "probe_read_cap_bytes_other": PROBE_READ_CAP_OTHER,
        "probe_note": ("entry-NAME probe over the whole tree; payload probe on the "
                       "head of every k-line file and in full for the _meta / _tools / "
                       "funding / instruments surface"),
        "token_count": len(PATTERNS),
        "decisive_token_count": len(DECISIVE_ZERO_TOKENS),
    }

    files = 0
    payload_files = 0
    payload_bytes = 0
    name_hits = {t: [] for t in PATTERNS}
    payload_hits = {t: {} for t in PATTERNS}
    kline_row_keys = {}
    kline_files = []

    for dirpath, dirs, names in _walk(raw, depth):
        for fn in sorted(names):
            if fn.startswith("."):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, raw)
            files += 1
            for tok in PATTERNS:
                if COMPILED[tok].search(rel):
                    name_hits[tok].append(rel)
            if not fn.endswith((".json", ".jsonl", ".md", ".txt", ".py", ".sh",
                                ".log", ".csv", ".gz")):
                continue
            is_kline = "/klines/" in rel.replace(os.sep, "/")
            cap = PROBE_READ_CAP if is_kline else PROBE_READ_CAP_OTHER
            txt = _read_text(full, cap=cap)
            if txt is None:
                continue
            payload_files += 1
            payload_bytes += len(txt)
            if is_kline:
                kline_files.append(rel)
                keys = _keys_of(_first_line(full) or "")
                if keys is not None:
                    kline_row_keys[tuple(keys)] = kline_row_keys.get(tuple(keys), 0) + 1
            for tok, cnt in _probe_text(txt).items():
                payload_hits[tok][rel] = payload_hits[tok].get(rel, 0) + cnt

    out["files_walked"] = files
    out["payload_files_probed"] = payload_files
    out["payload_bytes_probed"] = payload_bytes
    out["kline_files_probed"] = len(kline_files)
    out["kline_row_key_sets"] = {"|".join(k): v for k, v in kline_row_keys.items()}

    # ---- store identity
    market_root = os.path.join(raw, "binance")
    markets = sorted(d for d in os.listdir(market_root)
                     if os.path.isdir(os.path.join(market_root, d)) and not d.startswith("."))
    out["market_dirs"] = markets
    usdm = os.path.join(market_root, "usdm")
    families = sorted(d for d in os.listdir(usdm)
                      if os.path.isdir(os.path.join(usdm, d)) and not d.startswith("."))
    out["dataset_families"] = families
    out["top_level_dirs"] = sorted(d for d in os.listdir(raw)
                                   if os.path.isdir(os.path.join(raw, d))
                                   and not d.startswith("."))

    # ---- instrument surface
    inst_path = os.path.join(usdm, "instruments", "usdm-perp-instruments.json")
    inst_doc = _load_json(inst_path) if os.path.isfile(inst_path) else {"instruments": []}
    fields = {}
    ids = []
    for entry in inst_doc["instruments"]:
        f = entry["fields"]
        ids.append(f["id"])
        for k in f:
            fields[k] = fields.get(k, 0) + 1
    out["instrument_surface"] = {
        "path": "binance/usdm/instruments/usdm-perp-instruments.json",
        "sha256": _sha256_file(inst_path),
        "count": len(ids),
        "instrument_ids": sorted(ids),
        "types": sorted({e["fields"]["type"] for e in inst_doc["instruments"]}),
        "is_inverse": sorted({e["fields"]["is_inverse"] for e in inst_doc["instruments"]}),
        "settlement_currencies": sorted({e["fields"]["settlement_currency"]
                                         for e in inst_doc["instruments"]}),
        "field_names": sorted(fields),
        "field_count": len(fields),
        "has_maturity_field": any(re.search(r"matur|expir|deliver|contract_month", k, re.I)
                                  for k in fields),
        "has_options_or_iv_field": any(
            re.search(r"option|implied|iv_|vol(?!ume)|strike|greek", k, re.I)
            for k in fields),
    }
    exp_path = os.path.join(raw, "_meta", "INSTRUMENTS_EXPORT.json")
    exp_doc = _load_json(exp_path) if os.path.isfile(exp_path) else {}
    out["instrument_export"] = {
        "path": "_meta/INSTRUMENTS_EXPORT.json",
        "sha256": _sha256_file(exp_path) if os.path.isfile(exp_path) else None,
        "count": exp_doc.get("count"),
        "instrument_ids": sorted(exp_doc.get("instrument_ids") or []),
    }

    # ---- kline surface
    klines_root = os.path.join(usdm, "klines")
    symbols = (sorted(d for d in os.listdir(klines_root)
                      if os.path.isdir(os.path.join(klines_root, d))
                      and not d.startswith(".")) if os.path.isdir(klines_root) else [])
    ks = {}
    for sym in symbols:
        sdir = os.path.join(klines_root, sym)
        ivs = sorted(d for d in os.listdir(sdir)
                     if os.path.isdir(os.path.join(sdir, d)) and not d.startswith("."))
        per_iv = {}
        for iv in ivs:
            idir = os.path.join(sdir, iv)
            fs = sorted(f for f in os.listdir(idir) if not f.startswith("."))
            rows = 0
            nbytes = 0
            for f in fs:
                full = os.path.join(idir, f)
                nbytes += os.path.getsize(full)
                if iv in ("1d", "1w"):
                    txt = _read_text(full)
                    if txt:
                        rows += sum(1 for line in txt.splitlines() if line.strip())
            per_iv[iv] = {"files": len(fs), "bytes": nbytes,
                          "rows": (rows if iv in ("1d", "1w") else None)}
        ks[sym] = {"intervals": ivs, "per_interval": per_iv}
    steps = sorted({_step_seconds(iv) for sym in ks for iv in ks[sym]["intervals"]})
    all_ivs = sorted({iv for sym in ks for iv in ks[sym]["intervals"]})
    out["kline_surface"] = {
        "symbols": ks,
        "intervals": all_ivs,
        "finest_resolved_interval": (min(all_ivs, key=lambda x: _step_seconds(x))
                                     if all_ivs else None),
        "finest_step_seconds": (min(steps) if steps else None),
        "rows_1d_total": sum(ks[s]["per_interval"].get("1d", {}).get("rows") or 0
                             for s in ks),
    }

    # ---- funding surface
    funding_root = os.path.join(usdm, "funding")
    fsym = (sorted(d for d in os.listdir(funding_root)
                   if os.path.isdir(os.path.join(funding_root, d))
                   and not d.startswith(".")) if os.path.isdir(funding_root) else [])
    fund = {}
    for sym in fsym:
        fdir = os.path.join(funding_root, sym)
        rows = []
        for f in sorted(os.listdir(fdir)):
            if f.startswith("."):
                continue
            txt = _read_text(os.path.join(fdir, f))
            if not txt:
                continue
            for line in txt.splitlines():
                if line.strip():
                    rows.append(json.loads(line))
        truth = {}
        hours = set()
        for r in rows:
            truth[r["truth_status"]] = truth.get(r["truth_status"], 0) + 1
            hours.add(_iso(r["funding_time_ms"])[11:16])
        fund[sym] = {
            "observations": len(rows),
            "first_utc": _iso(rows[0]["funding_time_ms"]) if rows else None,
            "last_utc": _iso(rows[-1]["funding_time_ms"]) if rows else None,
            "truth_status_counts": truth,
            "settlement_hours_utc": sorted(hours),
        }
    out["funding_surface"] = fund

    # ---- measured windows (1d contiguity per symbol, full read; 1w endpoints)
    windows = {}
    for sym in symbols:
        idir = os.path.join(klines_root, sym, "1d")
        opens = []
        for f in sorted(os.listdir(idir)):
            if f.startswith("."):
                continue
            txt = _read_text(os.path.join(idir, f))
            for line in (txt or "").splitlines():
                if line.strip():
                    opens.append(json.loads(line)["open_time_ms"])
        step = _step_seconds("1d") * 1000
        off = 0
        dupes = 0
        seen = set()
        for a, b in zip(opens, opens[1:]):
            if b - a != step:
                off += 1
            if b in seen:
                dupes += 1
            seen.add(a)
        windows[sym] = {
            "bars": len(opens),
            "first_bar_open_utc": _iso(opens[0]) if opens else None,
            "first_bar_open_date": _day(opens[0]) if opens else None,
            "last_bar_open_utc": _iso(opens[-1]) if opens else None,
            "last_bar_open_date": _day(opens[-1]) if opens else None,
            "off_grid_steps": off,
            "duplicate_opens": dupes,
            "expected_if_contiguous": ((opens[-1] - opens[0]) // step + 1) if opens else 0,
            "contiguous": off == 0 and dupes == 0 and len(opens) == (
                (opens[-1] - opens[0]) // step + 1 if opens else 0),
        }
    out["kline_1d_windows"] = windows

    cfg_p = os.path.join(raw, "_meta", "CONFIG.json")
    cfg = _load_json(cfg_p) if os.path.isfile(cfg_p) else {}
    out["config"] = cfg
    state_p = os.path.join(raw, "_meta", "STATE.json")
    state = _load_json(state_p) if os.path.isfile(state_p) else {}
    out["state"] = {"schema": state.get("schema"), "last_run_utc": state.get("last_run_utc")}

    schema_txt = _read_text(os.path.join(raw, "_meta", "SCHEMA.md")) or ""
    out["schema_documents_coverage_limit"] = COVERAGE_LIMIT_SENTENCE in schema_txt
    out["schema_coverage_limit_sentence"] = COVERAGE_LIMIT_SENTENCE
    out["schema_dataset_headers"] = re.findall(r"^## Dataset: (.+)$", schema_txt, re.M)

    # ---- probe results
    probe = {}
    for tok in PATTERNS:
        allowed = DECISIVE_PROSE_ALLOWED.get(tok) or {}
        name_hits_t = sorted(name_hits[tok])
        payload_t = dict(sorted(payload_hits[tok].items()))
        unexplained = [p for p in name_hits_t
                       if not any(p.startswith(a) for a in allowed)]
        unexplained += [p for p in payload_t
                        if not any(p.startswith(a) for a in allowed)]
        probe[tok] = {
            "name_hits": name_hits_t,
            "payload_hits": sum(payload_t.values()),
            "payload_files": [{"path": p, "count": c} for p, c in payload_t.items()],
            "declared_allowed": {k: v["count"] for k, v in allowed.items()},
            "unexplained": sorted(set(unexplained)),
        }
    out["probe"] = probe
    # Every decisive occurrence is either explained by a declared allowance or
    # offending; the two totals below are the gate's measured absence.
    out["decisive_hits_unexplained"] = {t: probe[t]["unexplained"] for t in DECISIVE_ZERO_TOKENS
                                        if probe[t]["unexplained"]}
    out["decisive_probe_hits_unexplained_total"] = sum(
        len(probe[t]["unexplained"]) for t in DECISIVE_ZERO_TOKENS)
    out["other_token_unexplained_total"] = sum(
        len(probe[t]["unexplained"]) for t in PATTERNS
        if t not in set(DECISIVE_ZERO_TOKENS))
    out["decisive_group_unexplained"] = {
        g: sum(len(probe[t]["unexplained"]) for t in toks)
        for g, toks in DECISIVE_GROUPS.items()}
    out["decisive_group_declared_hits"] = {
        g: sum(sum(probe[t]["declared_allowed"].values()) for t in toks)
        for g, toks in DECISIVE_GROUPS.items()}
    out["decisive_name_hits"] = {t: probe[t]["name_hits"] for t in DECISIVE_ZERO_TOKENS
                                 if probe[t]["name_hits"]}
    out["decisive_payload_hits"] = {t: probe[t]["payload_hits"]
                                    for t in DECISIVE_ZERO_TOKENS if probe[t]["payload_hits"]}
    out["present_token_positive_control"] = {
        t: {"name_hits": len(probe[t]["name_hits"]),
            "payload_hits": probe[t]["payload_hits"]} for t in PRESENT_TOKENS}
    out["decisive_carried_by_row_shape"] = sorted(
        k for k in (out["instrument_surface"]["field_names"] +
                    [x for v in out.get("kline_row_key_sets", {}) for x in v.split("|")])
        if any(COMPILED[t].search(k) for t in DECISIVE_ZERO_TOKENS))
    return out


# ----------------------------------------------------------------- host --------
def _probe_literals(text):
    """Literal (substring) token probe for the HOUSE scan.

    The host scan is a disclosure surface, not the decisive gate: it uses the
    same token list with a literal (substring) match, which is a SUPERSET of the
    boundary-aware matcher used on the canonical raw - so it can never hide a
    hit, only over-report one (and every hit is classified).
    """
    low = text.lower()
    return {t for t in DECISIVE_ZERO_TOKENS
            if any(lit in low for lit in LITERALS[t])}


def measure_host_stores(roots=None, read_cap=65536, progress=None):
    """Re-measure the host's other stores for the decisive vocabulary.

    Bounded by design: entry-name probe over every file, literal payload probe on
    the head of every text file (< `read_cap` bytes, declared) and a SKIP class
    for files larger than `skip_over` (declared, never silently dropped).
    """
    key = tuple(roots or HOST_ROOTS)
    if key in _HOST_SCAN_CACHE:
        return _HOST_SCAN_CACHE[key]
    skip_over = 64 * 1024 * 1024
    out = {"roots": [], "files_scanned": 0, "newest_mtime_utc": None,
           "payload_read_cap_bytes": read_cap, "payload_skip_over_bytes": skip_over,
           "payload_match_mode": "literal-substring superset of the boundary-aware raw probe",
           "files_payload_head_only": 0, "files_payload_skipped_too_large": 0}
    classes = {}
    series_hits = []
    prose_hits = []
    roots = roots or HOST_ROOTS
    for root in roots:
        if not os.path.isdir(root):
            out["roots"].append({"root": root, "exists": False})
            continue
        n = 0
        for dirpath, dirs, names in _walk(root, max_depth=64):
            for fn in names:
                if fn.startswith("."):
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, root)
                n += 1
                is_series = fn.endswith(HOST_ROW_EXTS)
                readable = fn.endswith(HOST_TEXT_EXTS + HOST_ROW_EXTS)
                try:
                    size = os.path.getsize(full)
                except OSError:
                    size = 0
                txt = None
                if readable and size:
                    if size > skip_over:
                        out["files_payload_skipped_too_large"] += 1
                    else:
                        if size > read_cap:
                            out["files_payload_head_only"] += 1
                        txt = _read_text(full, cap=read_cap)
                tokens = {t for t in DECISIVE_ZERO_TOKENS if COMPILED[t].search(rel)}
                by_payload = set()
                if txt:
                    by_payload = _probe_literals(txt)
                tokens |= by_payload
                if tokens:
                    entry = {"root": root, "rel": rel, "tokens": sorted(tokens),
                             "series_shaped": is_series,
                             "evidence": ("name+payload" if by_payload and
                                          COMPILED and tokens - by_payload else
                                          ("payload" if by_payload else "name")),
                             "bytes_read": len(txt) if txt else 0}
                    (series_hits if is_series else prose_hits).append(entry)
                mt = os.path.getmtime(full)
                if out["newest_mtime_utc"] is None or mt > out["newest_mtime_utc"][1]:
                    out["newest_mtime_utc"] = (_iso(mt * 1000), mt)
        out["roots"].append({"root": root, "exists": True, "files": n})
        out["files_scanned"] += n
        sys.stderr.write("scanned %s: %d files\n" % (root, n))
        sys.stderr.flush()
    if isinstance(out["newest_mtime_utc"], tuple):
        out["newest_mtime_utc"] = out["newest_mtime_utc"][0]
    for entry in series_hits:
        cls = _host_class(os.path.join(entry["root"], entry["rel"])) or "host_series_rows"
        entry["class"] = cls
        classes[cls] = classes.get(cls, 0) + 1
    for entry in prose_hits:
        cls = _host_class(os.path.join(entry["root"], entry["rel"])) or "host_prose_or_source_text"
        entry["class"] = cls
        classes[cls] = classes.get(cls, 0) + 1
    out["series_hits"] = series_hits
    out["prose_hits"] = prose_hits
    out["series_hit_classes"] = dict(sorted(classes.items()))
    # Measured substitutes: every series-shaped hit that carries something a
    # reader might mistake for the required data, with its measured size.
    out["measured_substitutes"] = [
        {"path": os.path.join(e["root"], e["rel"]), "tokens": e["tokens"],
         "class": e["class"], "used": False}
        for e in series_hits]
    out["unclassified"] = sorted({e["rel"] for e in series_hits
                                  if e["class"] == "host_series_rows"})
    out["note"] = NON_CANONICAL_NOTE
    if progress:
        progress(out)
    _HOST_SCAN_CACHE[key] = out
    return out


# ------------------------------------------------------------- checks ---------
OPTION_SNAPSHOT_DIR = "/Users/hong/workspace/phase11-options-volatility/raw"
DERIVED_PANEL_DIR = "/Users/hong/workspace/phase7-alpha-research/data/derived"


def measure_option_snapshot(d=OPTION_SNAPSHOT_DIR):
    """Re-measure the host's ONLY options/implied-volatility material (single instant)."""
    out = {"dir": d, "exists": os.path.isdir(d), "files": [], "single_instant": None,
           "instrument_count": None, "book_summary_count": None,
           "distinct_expiries": None, "distinct_strikes": None,
           "has_mark_iv": None, "mark_iv_sample": None, "series_shaped_files": []}
    if not out["exists"]:
        return out
    names = sorted(f for f in os.listdir(d) if not f.startswith("."))
    out["files"] = names
    out["series_shaped_files"] = [f for f in names if f.endswith(HOST_ROW_EXTS)]
    out["single_instant"] = len(names) == 2
    ins_p = os.path.join(d, "deribit_get_instruments_btc.json")
    bs_p = os.path.join(d, "deribit_get_book_summary_btc.json")
    if os.path.isfile(ins_p):
        ins = _load_json(ins_p).get("result") or []
        out["instrument_count"] = len(ins)
        out["distinct_expiries"] = len({r.get("expiration_timestamp") for r in ins})
        out["distinct_strikes"] = len({r.get("strike") for r in ins})
    if os.path.isfile(bs_p):
        bs = _load_json(bs_p).get("result") or []
        out["book_summary_count"] = len(bs)
        out["has_mark_iv"] = bool(bs) and "mark_iv" in bs[0]
        out["mark_iv_sample"] = bs[0].get("mark_iv") if bs else None
    return out


def measure_derived_panels(d=DERIVED_PANEL_DIR):
    """Re-measure the host's non-canonical derived crypto panel (no IV series)."""
    out = {"dir": d, "exists": os.path.isdir(d), "files": [],
           "symbols": None, "rows": None, "carries_implied_vol": None}
    if not out["exists"]:
        return out
    out["files"] = sorted(f for f in os.listdir(d) if not f.startswith("."))
    for f in out["files"]:
        if f.endswith(".csv") and "close_wide" in f:
            p = os.path.join(d, f)
            txt = _read_text(p, cap=4096)
            head = (txt or "").splitlines()
            if head:
                out["symbols"] = [c for c in head[0].split(",")[1:] if c]
            full = _read_text(p)
            out["rows"] = max(0, len(full.splitlines()) - 1) if full else None
            low = (full or "").lower()
            out["carries_implied_vol"] = any(lit in low for lit in ("dvol", "implied"))
            break
    return out


def _check(cid, name, ok, detail):
    return {"id": cid, "name": name, "ok": bool(ok), "detail": detail}


def _round_paths(results_root, family=FAMILY, round_id=ROUND):
    base = os.path.join(results_root, family, "rounds", round_id)
    return {"round_dir": base,
            "round_spec": os.path.join(base, "round-spec.json"),
            "verdict": os.path.join(base, "verdict.json"),
            "family_json": os.path.join(results_root, family, "family.json")}


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


ADVERSARIAL_PROBE_ROOTS = ("/Volumes/ExpansionDrive/lean-system", "/Volumes/ExpansionDrive/nautilus-system")


def run_checks(results_root=DEFAULT_RESULTS, raw_root=DEFAULT_RAW, repo_root=DEFAULT_REPO,
               record_path=DEFAULT_RECORD, card_body_path=None, host=None, raw=None,
               host_scan=None):
    """Re-derive the whole terminal determination from the live filesystem."""
    checks = []
    raw = raw or measure_raw(raw_root)
    paths = _round_paths(results_root)
    spec = _load_json(paths["round_spec"])
    verdict = _load_json(paths["verdict"])
    ur = spec["universe_registration"]
    gate = spec["prerequisite_gate"]
    dec = verdict["failure"]

    # C1 store identity
    ok = (raw["market_dirs"] == ["usdm"]
          and raw["dataset_families"] == ["funding", "instruments", "klines"]
          and raw["config"].get("venue") == "BINANCE"
          and raw["config"].get("market_type") == "usdm_perp")
    checks.append(_check("C1", "raw store identity (one venue, three dataset families)", ok,
                         {"market_dirs": raw["market_dirs"],
                          "dataset_families": raw["dataset_families"],
                          "config_venue": raw["config"].get("venue"),
                          "config_market_type": raw["config"].get("market_type")}))

    # C2 instrument surface: perpetuals only, no option/IV/maturity field
    inst = raw["instrument_surface"]
    ok = (inst["count"] == 4 and inst["types"] == ["CryptoPerpetual"]
          and inst["is_inverse"] == [False]
          and inst["settlement_currencies"] == ["USDT"]
          and not inst["has_maturity_field"] and not inst["has_options_or_iv_field"]
          and inst["instrument_ids"] == raw["instrument_export"]["instrument_ids"])
    checks.append(_check("C2", "instrument surface (four perpetuals, no option/IV/maturity field)",
                         ok, {"count": inst["count"], "types": inst["types"],
                              "field_count": inst["field_count"],
                              "has_maturity_field": inst["has_maturity_field"],
                              "has_options_or_iv_field": inst["has_options_or_iv_field"],
                              "instrument_ids": inst["instrument_ids"]}))

    # C3 k-line surface: 7 intervals everywhere, one uniform row key set
    ks = raw["kline_surface"]
    expected_ivs = ["15m", "1d", "1h", "1w", "30m", "4h", "5m"]
    ok = (ks["intervals"] == expected_ivs
          and all(ks["symbols"][s]["intervals"] == expected_ivs for s in ks["symbols"])
          and len(raw["kline_row_key_sets"]) == 1
          and set(list(raw["kline_row_key_sets"])[0].split("|")) ==
          {"close", "close_time_ms", "high", "low", "open", "open_time_ms", "volume"})
    checks.append(_check("C3", "k-line surface (7 intervals x 4 symbols, uniform row keys)", ok,
                         {"intervals": ks["intervals"],
                          "row_key_sets": raw["kline_row_key_sets"],
                          "kline_files_probed": raw["kline_files_probed"],
                          "payload_files_probed": raw["payload_files_probed"]}))

    # C4 finest resolved interval
    ok = ks["finest_resolved_interval"] == "5m" and ks["finest_step_seconds"] == 300
    checks.append(_check("C4", "finest resolved interval is a 5m bar grid (no sub-bar data)", ok,
                         {"finest_resolved_interval": ks["finest_resolved_interval"],
                          "finest_step_seconds": ks["finest_step_seconds"]}))

    # C5 funding surface
    fund = raw["funding_surface"]
    ok = (len(fund) == 4
          and all(v["observations"] > 0 for v in fund.values())
          and all(v["first_utc"] is not None for v in fund.values())
          and all(sum(v["truth_status_counts"].values()) == v["observations"]
                  for v in fund.values()))
    checks.append(_check("C5", "funding surface (four symbols, truth_status split measured)", ok,
                         {s: {"observations": v["observations"],
                              "truth_status_counts": v["truth_status_counts"]}
                          for s, v in fund.items()}))

    # C6 measured windows / bar labelling
    w = raw["kline_1d_windows"]
    ok = (len(w) == 4 and all(v["contiguous"] and v["off_grid_steps"] == 0
                              and v["duplicate_opens"] == 0 for v in w.values())
          and all(v["bars"] == v["expected_if_contiguous"] for v in w.values()))
    checks.append(_check("C6", "measured 1d windows (contiguous, no duplicate bar opens)", ok,
                         {s: {"bars": v["bars"], "first": v["first_bar_open_utc"],
                              "last": v["last_bar_open_utc"],
                              "expected_if_contiguous": v["expected_if_contiguous"]}
                          for s, v in w.items()}))

    # C7-C11 decisive-zero probes
    def zero_group(name, tokens, cid, label):
        bad = {}
        for t in tokens:
            p = raw["probe"][t]
            if p["unexplained"]:
                bad[t] = p["unexplained"]
        checks.append(_check(cid, label, not bad,
                             {"group": name, "tokens": tokens,
                              "offending": bad,
                              "declared_allowed": {t: raw["probe"][t]["declared_allowed"]
                                                   for t in tokens
                                                   if raw["probe"][t]["declared_allowed"]}}))

    zero_group("volatility_index_series", DECISIVE_GROUPS["volatility_index_series"], "C7",
               "implied-volatility / volatility-index vocabulary is absent (the decisive family)")
    zero_group("options_market_data", DECISIVE_GROUPS["options_market_data"], "C8",
               "no options-market-data vocabulary anywhere in the raw tree")
    zero_group("us_equity_venue", DECISIVE_GROUPS["us_equity_venue"], "C9",
               "no US-equity venue vocabulary anywhere in the raw tree")
    zero_group("record_instrument_set+record_macro_series",
               DECISIVE_GROUPS["record_instrument_set"] + DECISIVE_GROUPS["record_macro_series"],
               "C10", "no record instrument (ETF) or macro series (TNX/BAA10Y) vocabulary")
    zero_group("portability_alternates",
               DECISIVE_GROUPS["portability_alternate_provider"] +
               DECISIVE_GROUPS["portability_growth_basket_tokens"] +
               DECISIVE_GROUPS["portability_defensive_store"], "C11",
               "no portability-alternate / basket-token vocabulary (Aave, AVAX/NEAR/SUI, stablecoin)")

    # C12 declared allowances audit: live counts must equal the declarations and
    # the declarations must cover every decisive occurrence.
    decl_problems = []
    declared_total = 0
    measured_total = 0
    for tok in DECISIVE_ZERO_TOKENS:
        p = raw["probe"][tok]
        declared_total += sum(p["declared_allowed"].values())
        measured_total += p["payload_hits"] + len(p["name_hits"])
        for path, want in p["declared_allowed"].items():
            got = 0
            for e in p["payload_files"]:
                if e["path"] == path:
                    got += e["count"]
            if path in p["name_hits"]:
                got += 1
            if got != want:
                decl_problems.append({"token": tok, "path": path, "declared": want,
                                      "measured": got})
    ok = not decl_problems and declared_total == measured_total
    checks.append(_check("C12", "declared prose allowances cover every decisive occurrence", ok,
                         {"declared_total": declared_total, "measured_total": measured_total,
                          "problems": decl_problems}))

    # C13 host scan
    if not host:
        host = measure_host_stores()
    ok = (not host["unclassified"]
          and not (set(host["series_hit_classes"]) & set(STORE_HOST_CLASSES))
          and all(not e["used"] for e in host["measured_substitutes"]))
    checks.append(_check("C13", "host store scan (every hit classified, none usable)", ok,
                         {"files_scanned": host["files_scanned"],
                          "series_hits": len(host["series_hits"]),
                          "prose_hits": len(host["prose_hits"]),
                          "classes": host["series_hit_classes"],
                          "unclassified": host["unclassified"],
                          "measured_substitutes": host["measured_substitutes"][:12]}))

    # C14 nothing was ever submitted
    round_dir = paths["round_dir"]
    attempts_dir = os.path.join(round_dir, "attempts")
    family_dir = os.path.dirname(os.path.dirname(round_dir))
    stray = []
    for dirpath, dirs, names in _walk(family_dir, max_depth=6):
        for fn in dirs + names:
            if fn in ("run-spec.json", "DONE", "FAILED", "INCOMPLETE"):
                stray.append(os.path.relpath(os.path.join(dirpath, fn), results_root))
    ok = (not os.path.exists(attempts_dir) and not stray
          and sorted(os.listdir(round_dir)) == ["round-spec.json", "verdict.json"])
    checks.append(_check("C14", "no attempt, no run-spec, no terminal sentinel exists", ok,
                         {"attempts_dir_exists": os.path.exists(attempts_dir),
                          "round_dir_entries": sorted(os.listdir(round_dir)),
                          "stray_submission_files": stray}))

    # C15 verdict terminal values
    ok = (verdict["verdict"] == "TECHNICAL_INCOMPLETE"
          and verdict["performance_claimable"] is False
          and verdict["run_id"] is None
          and dec["layer"] == "card-local"
          and dec["class"] == "data_window_invalid"
          and dec["last_run_id"] is None
          and verdict["yield"]["yield_decision"] == "STOP_TECHNICAL_INCOMPLETE")
    checks.append(_check("C15", "verdict states the contract-mandated terminal values", ok,
                         {"verdict": verdict["verdict"],
                          "performance_claimable": verdict["performance_claimable"],
                          "run_id": verdict["run_id"], "layer": dec["layer"],
                          "class": dec["class"],
                          "yield_decision": verdict["yield"]["yield_decision"]}))

    # C16 verdict prerequisite block agrees with the spec, item by item
    vpre = verdict.get("prerequisite") or {}
    ok = (vpre.get("decisive_status_map") == gate.get("decisive_status_map")
          and vpre.get("decisive_matrix_item_count") == gate.get("decisive_matrix_item_count")
          and (verdict.get("attempts") or {}).get("launched") == 0
          and bool(dec.get("definitive_absences"))
          and bool(vpre.get("measured_available")))
    checks.append(_check("C16", "verdict.prerequisite mirrors the spec's decisive matrix", ok,
                         {"verdict_status_map_len": len(vpre.get("decisive_status_map") or {}),
                          "spec_status_map_len": len(gate.get("decisive_status_map") or {}),
                          "verdict_item_count": vpre.get("decisive_matrix_item_count"),
                          "spec_item_count": gate.get("decisive_matrix_item_count"),
                          "attempts_launched": (verdict.get("attempts") or {}).get("launched")}))

    # C17 gate block: terminal values, decisive map, claimed-measurement agreement
    live_decisive = {i["item"]: i["status"] for i in ur["required_data_matrix"]
                     if i["status"] in ur["decisive_status_vocabulary"]}
    claimed = gate.get("measured_available") or {}
    live = {
        "venue": raw["config"].get("venue"),
        "market_type": raw["config"].get("market_type"),
        "dataset_families": raw["dataset_families"],
        "instrument_ids": raw["instrument_surface"]["instrument_ids"],
        "kline_intervals": raw["kline_surface"]["intervals"],
        "kline_files_probed": raw["kline_files_probed"],
        "finest_resolved_interval": raw["kline_surface"]["finest_resolved_interval"],
        "kline_1d_bars": {s: v["bars"] for s, v in raw["kline_1d_windows"].items()},
        "funding_observations": {s: v["observations"] for s, v in raw["funding_surface"].items()},
        "decisive_group_unexplained": raw["decisive_group_unexplained"],
        "decisive_probe_hits_unexplained_total": raw["decisive_probe_hits_unexplained_total"],
        "present_token_positive_control": raw["present_token_positive_control"],
    }
    claim_diff = {k: {"claimed": claimed.get(k), "live": v}
                  for k, v in live.items() if claimed.get(k) != v}
    ok = (gate["verdict"] == "TECHNICAL_INCOMPLETE"
          and gate["failure_layer"] == "card-local"
          and gate["failure_class_used"] == "data_window_invalid"
          and gate["last_run_id"] is None
          and gate["attempts_launched"] == 0
          and gate["universe_shrunk"] is False
          and gate["substitute_market_used"] is False
          and gate["decisive_matrix_item_count"] == len(live_decisive)
          and gate["decisive_status_map"] == live_decisive
          and not claim_diff)
    checks.append(_check("C17", "gate block: terminal values, decisive map, live measurement agreement",
                         ok, {"gate_verdict": gate["verdict"],
                              "gate_item_count": gate["decisive_matrix_item_count"],
                              "live_decisive_count": len(live_decisive),
                              "status_map_equal": gate["decisive_status_map"] == live_decisive,
                              "claim_diff": claim_diff,
                              "universe_shrunk": gate["universe_shrunk"],
                              "substitute_market_used": gate["substitute_market_used"]}))

    # C18 universe registration: nothing was shrunk, matrix counts recompute
    counts = {}
    for i in ur["required_data_matrix"]:
        counts[i["status"]] = counts.get(i["status"], 0) + 1
    ok = (ur["required_data_available_local"] is False
          and ur["universe_shrunk_to_local_list"] is False
          and ur["local_instruments_present"] == raw["instrument_surface"]["instrument_ids"]
          and counts == ur["required_data_matrix_status_counts"]
          and len(ur["required_data_matrix"]) == sum(counts.values()))
    checks.append(_check("C18", "universe registration intact (unshrunk, matrix counts recompute)", ok,
                         {"required_data_available_local": ur["required_data_available_local"],
                          "universe_shrunk_to_local_list": ur["universe_shrunk_to_local_list"],
                          "status_counts_recomputed": counts,
                          "status_counts_declared": ur["required_data_matrix_status_counts"],
                          "local_instruments_present": ur["local_instruments_present"]}))

    # C19 DCA registration: full product kept, provenance classes kept
    dca = spec["dca_domain"]
    axes = dca.get("axes") or dca
    prod = 1
    for ax in ("spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct"):
        prod *= len(axes[ax])
    ok = (prod == 48 == dca["configs_per_cohort_per_grid"]
          and dca["base_quote"] == 1000.0
          and dca["provenance_class"]["base_quote"] == "PROJECT_PRE_REGISTERED_CONSTANT"
          and dca["provenance_class"]["searched_axes"] == "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN"
          and not any(re.match(r"^-?\s*(spacing_pct|size_multiplier|breakeven_tp_pct|"
                               r"invalidation_pct|base_quote)\b", it.strip(), re.I)
                      for it in spec["user_fixed_invariants"]["items"]))
    checks.append(_check("C19", "DCA registration (48-cell product, provenance classes)", ok,
                         {"product": prod, "declared": dca["configs_per_cohort_per_grid"],
                          "base_quote": dca["base_quote"],
                          "provenance_class": dca["provenance_class"],
                          "user_fixed_item_count": len(spec["user_fixed_invariants"]["items"]),
                          "user_fixed_items": [i[:60] for i in
                                               spec["user_fixed_invariants"]["items"]]}))

    # C20 falsification battery: record's full item count, no removal, restored item
    f = spec["falsification"]
    ok = (f["item_count"] == 4
          and f["card_excerpt_item_count"] == 3
          and f["no_item_removal"] is True
          and f["no_threshold_lowering"] is True
          and bool(f["blocked_by"])
          and "subperiod" in f["restored_items"][0]["item"].lower())
    checks.append(_check("C20", "falsification battery restored to the record's 4 items", ok,
                         {"item_count": f["item_count"],
                          "card_excerpt_item_count": f["card_excerpt_item_count"],
                          "restored_items": f["restored_items"],
                          "blocked_by": f["blocked_by"]}))

    # C21 launch block
    launch = spec["launch"]
    ok = (launch["launched"] is False and launch["attempts"] == 0
          and launch["run_specs"] == 0 and launch["terminal_sentinels"] == 0
          and launch["run_spec"] is None and launch["terminal_sentinel"] is None
          and launch["attempt_dir"] is None)
    checks.append(_check("C21", "launch block declares zero submissions", ok, launch))

    # C22 no fabricated performance numbers anywhere
    claims = _no_performance_claims(verdict, "verdict") + _no_performance_claims(spec, "spec")
    checks.append(_check("C22", "no fabricated performance number in either artifact",
                         not claims, {"claims": claims}))

    # C23 selector / disposition
    sd = spec["selector_and_disposition"]
    ok = (sd["cohorts_realized"] == 0
          and verdict["cohorts"]["realized"] == 0
          and verdict["survivors"] == []
          and verdict["survivor_bundle"] is None
          and sd["selector"] == "cohort-selector-v1"
          and sd["disposition"] == "cohort-disposition-v1")
    checks.append(_check("C23", "selector/disposition registered but zero cohorts realized", ok, sd))

    # C24 coverage
    cov = spec["coverage"]
    grids = ["historical", "oos", "full", "fee_2x", "funding_2x", "entry_delay_1_bar",
             "slippage_2ticks", "no_funding", "no_funding_full", "cost_attrition_40bps"]
    ok = (cov["cells_registered_per_grid"] == 0 and cov["cells_registered_total"] == 0
          and cov["cells_computed"] == 0 and cov["phase_grids"] == grids
          and verdict["coverage"]["phase_grids"] == grids)
    checks.append(_check("C24", "coverage block: zero cells, registered grid list intact", ok, cov))

    # C25 missing_conditions names the decisive absences
    mc = " ".join(verdict["missing_conditions"])
    needed = ["dvol", "vix", "etf", "treasury", "options", "implied"]
    ok = bool(verdict["missing_conditions"]) and all(n in mc.lower() for n in needed)
    checks.append(_check("C25", "missing_conditions names the decisive absences", ok,
                         {"count": len(verdict["missing_conditions"]),
                          "missing_tokens": [n for n in needed if n not in mc.lower()]}))

    # C26 verbatim digest map: every leaf is declared
    smap = spec.get("excerpt_source_map") or {}
    leaves = list(_walk_verbatim(spec))
    undeclared = [k for k, _ in leaves if k not in smap]
    bad_digest = [k for k, v in leaves
                  if k in smap and smap[k].get("sha256") != _sha256_text(v)]
    stale = [k for k in smap if k not in {kk for kk, _ in leaves}]
    ok = not undeclared and not bad_digest and not stale
    checks.append(_check("C26", "excerpt digest map covers every verbatim leaf", ok,
                         {"leaves": len(leaves), "map_entries": len(smap),
                          "undeclared": undeclared, "digest_mismatch": bad_digest,
                          "stale": stale}))

    # C27 verbatim resolution against the live sources
    res = verify_verbatim(spec_path=paths["round_spec"], repo_root=repo_root,
                          card_body_path=card_body_path, record_path=record_path,
                          results_root=results_root)
    checks.append(_check("C27", "every verbatim leaf resolves against its live source", res["ok"],
                         {"checked": res["verbatim_leaves_checked"],
                          "findings": res["findings"][:6]}))

    # C28 the host's only options material and derived panel are re-measured and
    # registered as used=false (the positive half of the absence claim)
    reg = (spec.get("other_local_stores") or {})
    reg_opt = reg.get("non_canonical_option_material") or {}
    live_opt = measure_option_snapshot()
    reg_pan = reg.get("non_canonical_derived_panels") or {}
    live_pan = measure_derived_panels()
    opt_diff = {}
    for k in ("exists", "files", "single_instant", "instrument_count",
              "book_summary_count", "distinct_expiries", "distinct_strikes",
              "has_mark_iv", "series_shaped_files"):
        if reg_opt.get(k) != live_opt.get(k):
            opt_diff[k] = {"registered": reg_opt.get(k), "live": live_opt.get(k)}
    pan_diff = {}
    for k in ("exists", "symbols", "rows", "carries_implied_vol"):
        if reg_pan.get(k) != live_pan.get(k):
            pan_diff[k] = {"registered": reg_pan.get(k), "live": live_pan.get(k)}
    ok = (not opt_diff and not pan_diff
          and reg_opt.get("used") is False and reg_pan.get("used") is False
          and live_opt.get("instrument_count") == 1012
          and live_opt.get("has_mark_iv") is True
          and live_opt.get("single_instant") is True
          and live_opt.get("series_shaped_files") == []
          and live_pan.get("carries_implied_vol") is False)
    checks.append(_check("C28", "non-canonical option snapshot + derived panel re-measured, used=false",
                         ok, {"option_diff": opt_diff, "panel_diff": pan_diff,
                              "option_used": reg_opt.get("used"),
                              "panel_used": reg_pan.get("used"),
                              "live_option": {k: live_opt.get(k) for k in
                                              ("single_instant", "instrument_count",
                                               "book_summary_count", "distinct_expiries",
                                               "distinct_strikes", "has_mark_iv",
                                               "series_shaped_files")},
                              "live_panel": {k: live_pan.get(k) for k in
                                             ("symbols", "rows", "carries_implied_vol")}}))
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
    return {"spec": spec_path, "verbatim_leaves_checked": checked,
            "source_map_entries": len(smap), "findings": findings, "ok": not findings}


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
    host = host or measure_host_stores()
    base = tempfile.mkdtemp(prefix="cmt-selftest-")

    def run(tmp_results):
        return run_checks(results_root=tmp_results, raw_root=DEFAULT_RAW, repo_root=repo_root,
                          record_path=record_path or DEFAULT_RECORD,
                          card_body_path=card_body_path, host=host or {}, raw=raw)

    cases = [
        ("verdict_not_technical_incomplete", ["C15"],
         lambda d: d.update({"verdict": "PASS"})),
        ("verdict_performance_claimable_true", ["C15"],
         lambda d: d.update({"performance_claimable": True})),
        ("verdict_failure_layer_shared", ["C15"],
         lambda d: d["failure"].update({"layer": "shared-layer"})),
        ("verdict_run_id_set", ["C15"],
         lambda d: d.update({"run_id": "r1-u1"})),
        ("verdict_yield_continue", ["C15"],
         lambda d: d["yield"].update({"yield_decision": "CONTINUE"})),
        ("verdict_missing_conditions_emptied", ["C25"],
         lambda d: d.update({"missing_conditions": ["x"]})),
        ("verdict_decisive_map_flipped", ["C16"],
         lambda d: d["prerequisite"].update({"decisive_status_map": {"x": "PRESENT"}})),
        ("verdict_cohorts_realized", ["C23"],
         lambda d: d.update({"cohorts": {"registered_slots": 0, "realized": 5}})),
        ("spec_gate_universe_shrunk", ["C17"],
         lambda d: d["prerequisite_gate"].update({"universe_shrunk": True})),
        ("spec_gate_substitute_market", ["C17"],
         lambda d: d["prerequisite_gate"].update({"substitute_market_used": True})),
        ("spec_matrix_decisive_count_off", ["C16", "C17"],
         lambda d: d["prerequisite_gate"].update({"decisive_matrix_item_count": 1})),
        ("spec_status_map_flipped", ["C16", "C17"],
         lambda d: d["prerequisite_gate"]["decisive_status_map"].update(
             {list(d["prerequisite_gate"]["decisive_status_map"])[0]: "PRESENT"})),
        ("spec_claimed_measurement_flipped", ["C17"],
         lambda d: d["prerequisite_gate"]["measured_available"].update(
             {"decisive_probe_hits_unexplained_total": 5})),
        ("spec_required_data_available_true", ["C18"],
         lambda d: d["universe_registration"].update({"required_data_available_local": True})),
        ("spec_matrix_status_count_off", ["C18"],
         lambda d: d["universe_registration"]["required_data_matrix_status_counts"].update(
             {"ABSENT": 999})),
        ("spec_dca_product_broken", ["C19"],
         lambda d: d["dca_domain"].update({"configs_per_cohort_per_grid": 47})),
        ("spec_dca_axis_marked_user_fixed", ["C19"],
         lambda d: d["dca_domain"]["provenance_class"].update(
             {"searched_axes": "USER_FIXED"})),
        ("spec_falsification_item_dropped", ["C20"],
         lambda d: d["falsification"].update({"item_count": 3})),
        ("spec_launch_claims_an_attempt", ["C21"],
         lambda d: d["launch"].update({"launched": True, "attempts": 1})),
        ("spec_fabricated_performance_number", ["C22"],
         lambda d: d["prerequisite_gate"].update({"net_pnl": 12345.67})),
        ("spec_coverage_cells_nonzero", ["C24"],
         lambda d: d["coverage"].update({"cells_computed": 7})),
        ("spec_verbatim_leaf_undeclared", ["C26", "C27"],
         lambda d: d["excerpt_source_map"].pop(
             sorted(d["excerpt_source_map"])[0], None)),
        ("spec_option_snapshot_marked_used", ["C28"],
         lambda d: d["other_local_stores"]["non_canonical_option_material"].update(
             {"used": True})),
        ("spec_option_snapshot_count_flipped", ["C28"],
         lambda d: d["other_local_stores"]["non_canonical_option_material"].update(
             {"instrument_count": 7})),
        ("attempt_dir_present", ["C14"],
         lambda d: os.makedirs(os.path.join(os.path.dirname(d["__path__"]), "attempts"),
                               exist_ok=True)),
        ("benign_non_goals_edit", None,
         lambda d: d.update({"non_goals": (d.get("non_goals") or []) + ["benign control"]})),
    ]
    for name, expect_fail, mutate in cases:
        tag = tempfile.mkdtemp(prefix="cmt-case-", dir=base)
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
    """Build a raw tree that DOES carry this record's needed data; raw checks must fail.

    Every required component is planted SEPARATELY (implied-volatility series,
    options chain with strikes/expiries/IV, US-equity ETF panel, Treasury/Baa
    macro series, stablecoin instrument) so each decisive group has its own
    control.
    """
    root = temp_root or tempfile.mkdtemp(prefix="cmt-rawfixture-")
    if temp_root is None:
        _write(os.path.join(root, "_meta/CONFIG.json"), json.dumps({
            "schema": "market-data-raw/config/v2",
            "venue": "DERIBIT+NYSE",
            "market_type": "options_and_equity",
            "symbols": ["BTC", "QQQ", "SPY", "SCHD"],
            "intervals": ["1d"],
            "klines_end_rule": "last completed session",
            "updater": "_tools/multi_venue_sync.py",
        }, indent=1))
        _write(os.path.join(root, "_meta/SCHEMA.md"),
               "# market-data-raw - schema\n\n## Dataset: dvol (implied volatility)\n\n"
               "* rows carry the Deribit DVOL implied volatility index close.\n")
        # 1) the implied-volatility index series the registered score needs
        rows = [json.dumps({"date": "2026-09-%02d" % d, "dvol_close": 50.0 + d,
                            "implied_volatility": 0.5 + d / 100.0}) for d in range(1, 21)]
        _write(os.path.join(root, "deribit/dvol/BTC-1d.jsonl"), "\n".join(rows) + "\n")
        # 2) an options chain with strikes / expiries / IV / open interest
        opt = [json.dumps({"expiry": "2026-12-25", "strike": 60000 + 5000 * k,
                           "option_type": "call", "implied_vol": 0.55,
                           "open_interest": 120 + k, "delta": 0.5 - k / 10})
               for k in range(4)]
        _write(os.path.join(root, "deribit/options/BTC-2026-12.jsonl"), "\n".join(opt) + "\n")
        # 3) the US-equity ETF panel on a US venue
        eq = [json.dumps({"date": "2026-09-%02d" % d, "close": 500.0 + d, "symbol": "QQQ",
                          "venue": "NASDAQ"}) for d in range(1, 21)]
        _write(os.path.join(root, "us_equity/etf/QQQ-1d.jsonl"), "\n".join(eq) + "\n")
        _write(os.path.join(root, "us_equity/etf/SPY-1d.jsonl"), "\n".join(eq) + "\n")
        # 4) the Treasury yield / Baa series the record conditions on
        _write(os.path.join(root, "fred/TNX-1d.csv"),
               "date,10-year treasury yield\n2026-09-01,4.12\n2026-09-02,4.10\n")
        _write(os.path.join(root, "fred/BAA10Y-1d.csv"),
               "date,moody baa yield\n2026-09-01,5.55\n")
        # 5) a stablecoin instrument (the portability defensive store)
        _write(os.path.join(root, "binance/usdm/instruments/usdm-perp-instruments.json"),
               json.dumps({"instruments": [{"fields": {
                   "id": "USDCUSDT-PERP.BINANCE", "type": "CryptoPerpetual",
                   "is_inverse": False, "settlement_currency": "USDT",
                   "base_currency": "USDC", "quote_currency": "USDT",
                   "price_increment": "0.0001", "taker_fee": "0.0005",
                   "maker_fee": "0.0002", "margin_init": "0.1", "margin_maint": "0.1"},
                   "python_type": "CryptoPerpetual"}]}))
        _write(os.path.join(root, "_meta/INSTRUMENTS_EXPORT.json"), json.dumps({
            "schema": "market-data-raw/instruments/usdm-perp/v1",
            "count": 1, "instrument_ids": ["USDCUSDT-PERP.BINANCE"]}, indent=1))
        _write(os.path.join(root, "_tools/multi_venue_sync.py"),
               "# Deribit DVOL and NYSE/NASDAQ ETF downloader\n")
    raw = measure_raw(root)
    # The host dimension is NOT what this control exercises (the live run does);
    # a declared empty stub keeps the control focused and fast.
    stub_host = {"roots": [], "files_scanned": 0, "series_hits": [], "prose_hits": [],
                 "series_hit_classes": {}, "measured_substitutes": [], "unclassified": [],
                 "note": "declared stub: the raw fixture control exercises the raw-side "
                         "checks only"}
    checks = run_checks(results_root=results_root or DEFAULT_RESULTS, raw_root=root,
                        repo_root=repo_root or DEFAULT_REPO, record_path=DEFAULT_RECORD,
                        host=stub_host, raw=raw)
    fails = _failing_ids(checks)
    expected = {"C1", "C2", "C3", "C7", "C8", "C9", "C10", "C11", "C12"}
    return {"fixture_root": root, "failing_checks": fails,
            "expected_failing": sorted(expected),
            "ok": expected <= set(fails),
            "decisive_hits_unexplained": raw.get("decisive_hits_unexplained"),
            "decisive_probe_hits_unexplained_total":
                raw.get("decisive_probe_hits_unexplained_total"),
            "decisive_group_unexplained": raw.get("decisive_group_unexplained"),
            "decisive_carried_by_row_shape": raw.get("decisive_carried_by_row_shape"),
            "decisive_name_hits": raw.get("decisive_name_hits"),
            "instrument_has_options_or_iv_field":
                raw["instrument_surface"]["has_options_or_iv_field"],
            "instrument_ids": raw["instrument_surface"]["instrument_ids"]}


# ------------------------------------------------ excluded token pass ---------
# Generic tokens deliberately NOT in the decisive vocabulary (they cannot
# discriminate a market store by themselves). A bare substring probe re-runs
# them so that their exclusion cannot hide an implied-volatility surface.
EXCLUDED_TOKENS = {
    "vol": r"vol",
    "iv": r"iv",
    "call": r"call",
    "put": r"put",
    "delta": r"delta",
    "vega": r"vega",
    "gamma": r"gamma",
    "index": r"index",
    "rate": r"rate",
    "macro": r"macro",
}


def excluded_token_pass(raw=DEFAULT_RAW, roots=None, read_cap=PROBE_READ_CAP):
    """Re-probe the generic tokens the decisive vocabulary leaves out."""
    compiled = {k: re.compile(v, re.I) for k, v in EXCLUDED_TOKENS.items()}
    out = {"tokens": {}, "unexplained": [], "ok": True}
    text_exts = (".json", ".jsonl", ".md", ".txt", ".py", ".sh", ".yaml", ".yml",
                 ".csv", ".tsv", ".html", ".log", ".gz")
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
                    if fn.endswith(text_exts):
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
                    host_series.setdefault(",".join(sorted(toks)), []).append(full)
    for tok in EXCLUDED_TOKENS:
        out["tokens"][tok] = {
            "raw_entry_name_hits": sorted(raw_name[tok])[:8],
            "raw_entry_name_hit_count": len(raw_name[tok]),
            "raw_payload_hits": raw_payload[tok],
            "raw_payload_files": sorted(raw_files[tok], key=lambda e: -e["count"])[:8],
        }
    out["host_series_name_hits"] = {k: sorted(set(v))[:12]
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
    ap.add_argument("--skip-host", action="store_true",
                    help="skip the (slow) live host scan; a cached host dict must be given")
    ap.add_argument("--host-json", default=None)
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
    if args.host_json and os.path.isfile(args.host_json):
        _HOST_SCAN_CACHE[tuple(HOST_ROOTS)] = _load_json(args.host_json)

    if args.measure_only:
        raw = cached_raw or measure_raw(args.raw_root)
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
        return 0 if not res["unclassified"] else 1

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
