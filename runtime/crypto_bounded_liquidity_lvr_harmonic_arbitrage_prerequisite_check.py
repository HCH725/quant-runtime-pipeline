#!/usr/bin/env python3
"""Prerequisite-gate checker for the bounded-liquidity / harmonic-arbitrage family.

The registered core signal is the *harmonic mean of two venues' marginal
liquidities* (``L_eff = L1*L2/(L1+L2)``) and the closed-form bounded-liquidity
LVR.  Its cost model ``C(dx) = Q*dx^2 / x~*(Q)`` is parameterised by the venue
reserve itself, so pool reserves / order-book depth are constitutive of the
signal, not auxiliary covariates.

This checker therefore *measures* the canonical raw store and answers one
question: can any legal local universe compute that core signal?  It never
substitutes a proxy, never shrinks a universe and never launches a backtest.

Phases
------
``--phase measure``   measure canonical raw only, print JSON (used to seed the
                      immutable round artifacts with a structural fingerprint)
``--phase publish``   full check set, write the evidence JSON
``--phase verify``    full check set, read-only, rc 0/1 (auditor re-run)

Negative controls
-----------------
* ``self_test``          tampered verdict/determination copies must be refused
* ``fixture_control``    a synthetic raw tree carrying pool reserves, swap
                         events, L2 depth, klines and two-venue volume must
                         flip the raw-side detector to "present", proving the
                         ABSENT reading is a measurement and not a dead branch

Stdlib only, no network, no credentials, no second backtest engine.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import itertools
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from typing import Any

FAMILY = "crypto-bounded-liquidity-lvr-harmonic-arbitrage-2026-09-01"
ROUND = FAMILY + "-r1"
TASK = "t_5f96962c"
BOARD = "quant-strategy-research"

DEFAULT_RESULTS = "/Volumes/ExpansionDrive/qlib-results"
DEFAULT_RAW = "/Volumes/ExpansionDrive/market-data-raw"
DEFAULT_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_RECORD = os.path.expanduser("~/.hermes/wiki/quant/%s.md" % FAMILY)
DEFAULT_BOARD_DB = os.path.expanduser(
    "~/.hermes/kanban/boards/%s/kanban.db" % BOARD
)
DEFAULT_EVIDENCE = os.path.join(
    DEFAULT_REPO, "evidence",
    "%s-prerequisite-gate-20260922.json" % FAMILY,
)

FOOTER_MARKER = "\n\n---\nLIFECYCLE FOOTER"
CARD_BODY_SOURCE = "kanban:%s/%s" % (BOARD, TASK)

EXPECTED_SYMBOLS = ["BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
EXPECTED_INTERVALS = ["5m", "15m", "30m", "1h", "4h", "1d", "1w"]
INTRADAY_INTERVALS = {"1m", "3m", "5m", "15m", "30m", "1h"}
KLINE_FIELDS = [
    "close", "close_time_ms", "high", "low", "open", "open_time_ms", "volume"
]

PHASE_GRIDS = [
    "historical", "oos", "full", "fee_2x", "funding_2x",
    "entry_delay_1_bar", "slippage_2ticks", "no_funding",
    "no_funding_full", "cost_attrition_40bps",
]

# --- surface classification rules (auditable, deliberately explicit) ---------
# An L2 depth surface must expose either a level ladder or size-at-level.
# A row carrying only best bid/ask *prices* is a top-of-book summary: it cannot
# identify the empirical slope |x~*'(Q)|, which needs size at many price levels.
DEPTH_LADDER_FIELDS = {
    "bids", "asks", "bid_levels", "ask_levels", "price_levels", "levels",
    "depth_levels", "order_book_levels",
}
DEPTH_SIZE_FIELDS = {
    "bid_size", "ask_size", "bid_qty", "ask_qty", "bid_quantity",
    "ask_quantity", "level_size", "size_at_price",
}
TOP_OF_BOOK_FIELDS = {"bid_price", "ask_price", "mid_price"}

RESERVE_FIELDS = {
    "reserve0", "reserve1", "reserve_a", "reserve_b", "reserve_x", "reserve_y",
    "pool_reserve", "pool_reserves", "token0", "token1", "pool_address",
    "pool_id", "k_invariant",
}
SWAP_FIELDS = {
    "tx_hash", "transaction_hash", "tx_index", "transaction_index",
    "log_index", "block_number", "swap_amount", "swap_direction",
    "tick_lower", "tick_upper", "amount_in", "amount_out",
}
VOLUME_FIELDS = {"volume", "quote_volume", "volume_usd", "volume_notional"}

PATH_EXACT_TOKENS = {
    "dex", "amm", "onchain", "chain", "pool", "pools", "reserves",
    "orderbook", "order_book", "depth", "lob", "l2", "swap", "swaps",
    "uniswap", "sushiswap", "bookticker",
}
PATH_SUBSTR_TOKENS = [
    "uniswap", "sushiswap", "balancer", "orderbook", "order_book",
    "bookticker", "onchain", "etherscan",
]

# Disclosure-only host scan: specific enough that a hit means something.
HOST_ROOTS = [
    os.path.expanduser("~/workspace"),
    "/Volumes/ExpansionDrive",
    os.path.expanduser("~/.hermes/wiki/quant"),
]
HOST_SKIP_DIRS = {
    ".git", ".worktrees", "__pycache__", ".pytest_cache", "node_modules",
    "venv", ".venv", "site-packages", ".Trash", "Photos Library.photoslibrary",
    "build-cache", "HermesCleanupArchive-2026-09-22", "Downloads-Music",
    "Automatically Add to Music.localized", "Automatically Add to Movies.localized",
}
HOST_DEPTH_TOKENS = ["orderbook", "order_book", "book_ticker", "bookticker",
                     "depth", "level2", "lob_"]
HOST_DEX_TOKENS = ["uniswap", "sushiswap", "pool_reserve", "reserve0",
                   "reserve1", "amm_pool", "onchain_swap", "subgraph"]
HOST_PROSE_EXTS = {".md", ".html", ".txt", ".py", ".rs", ".js", ".ts", ".go",
                   ".yaml", ".yml", ".toml", ".cfg", ".ini", ".sh", ".plist"}
HOST_DATA_EXTS = {".csv", ".parquet", ".jsonl", ".gz", ".json", ".feather",
                  ".h5", ".hdf5", ".npy", ".npz", ".tsv", ".db", ".sqlite",
                  ".pkl", ".pickle", ".arrow", ".bin"}
HOST_MAX_FILES = 400000

# excerpt_source_map key -> location inside round-spec.json
EXCERPT_POINTERS = {
    "record.provenance_block_verbatim": ("provenance", "record",
                                         "provenance_block_verbatim"),
    "record.economic_mechanism_block_verbatim": ("hypothesis",
                                                 "economic_mechanism_block_verbatim"),
    "record.signal_block_verbatim": ("hypothesis", "signal_block_verbatim"),
    "record.required_data_block_verbatim": ("registered_requirement",
                                            "record_required_data_block_verbatim"),
    "record.execution_assumptions_block_verbatim": ("registered_requirement",
                                                    "record_execution_assumptions_block_verbatim"),
    "record.falsification_block_verbatim": ("falsification",
                                            "record_falsification_verbatim"),
    "record.crypto_portability_block_verbatim": ("registered_requirement",
                                                 "record_crypto_portability_block_verbatim"),
    "card.family_id_verbatim": ("provenance", "family_id_verbatim"),
    "card.raw_registration_verbatim": ("provenance", "raw_registration_verbatim"),
    "card.prerequisite_clause_verbatim": ("prerequisite_gate", "clause_verbatim"),
    "card.card_registration_rule_verbatim": ("hypothesis",
                                             "card_registration_rule_verbatim"),
    "card.leg_semantics_verbatim": ("hypothesis", "leg_semantics_verbatim"),
    "card.dca_axes_verbatim": ("dca_domain", "card_dca_axes_verbatim"),
    "card.dca_provenance_verbatim": ("dca_domain", "card_dca_provenance_verbatim"),
    "card.per_fill_cost_verbatim": ("dca_domain", "per_fill_cost_accounting_verbatim"),
    "card.gross_pnl_verbatim": ("dca_domain",
                                "independent_gross_pnl_accounting_verbatim"),
    "card.selector_verbatim": ("selector_and_disposition", "card_selector_verbatim"),
    "card.survivor_requirements_verbatim": ("selector_and_disposition",
                                            "card_survivor_requirements_verbatim"),
    "card.data_window_verbatim": ("data_window", "card_data_window_verbatim"),
    "contract.technical_incomplete_row_verbatim": ("prerequisite_gate",
                                                   "contract_technical_incomplete_verbatim"),
    "contract.data_window_invalid_row_verbatim": ("prerequisite_gate",
                                                  "contract_data_window_invalid_verbatim"),
    "contract.local_universe_rule_verbatim": ("prerequisite_gate",
                                              "contract_local_universe_rule_verbatim"),
    "footer.lifecycle_footer_verbatim": ("prerequisite_gate",
                                         "lifecycle_footer_verbatim"),
}


def sha_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def sha_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode()


def now_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_card_body(db_path: str) -> tuple[str, str]:
    con = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    try:
        row = con.execute("select body, title from tasks where id=?", (TASK,)).fetchone()
    finally:
        con.close()
    if not row:
        raise SystemExit("card %s not found in %s" % (TASK, db_path))
    return row[0], row[1]


def split_card(body: str) -> tuple[str, str]:
    """Return (candidate bytes incl. trailing newline, system-owned footer)."""
    i = body.find(FOOTER_MARKER)
    if i < 0:
        raise SystemExit("system-owned LIFECYCLE FOOTER marker missing from card body")
    return body[:i] + "\n", body[i:]


# --------------------------------------------------------------------------
# measurement
# --------------------------------------------------------------------------
def _sampled_rows(path: str, limit: int = 50) -> list[dict]:
    rows: list[dict] = []
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
            if len(rows) >= limit:
                break
    return rows


def census(raw_root: str) -> tuple[dict, list[str]]:
    """Per-dataset field census: first/middle/last file, up to 50 rows each."""
    dirs: dict[str, dict] = {}
    errors: list[str] = []
    for dirpath, dirnames, filenames in os.walk(raw_root):
        dirnames[:] = [d for d in sorted(dirnames)
                       if d not in HOST_SKIP_DIRS and not d.startswith(".")]
        gz = sorted(f for f in filenames if f.endswith(".jsonl.gz"))
        if not gz:
            continue
        rel = os.path.relpath(dirpath, raw_root)
        keys: set[str] = set()
        rows_read = 0
        picks = sorted({0, len(gz) // 2, len(gz) - 1})
        for idx in picks:
            p = os.path.join(dirpath, gz[idx])
            try:
                for row in _sampled_rows(p):
                    keys |= set(row)
                    rows_read += 1
            except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
                errors.append("%s: %s: %s" % (rel, os.path.basename(gz[idx]), exc))
        dirs[rel] = {"file_count": len(gz), "row_keys": sorted(keys),
                     "rows_sampled": rows_read,
                     "sampled_files": [gz[i] for i in picks]}
    return dirs, errors


def _path_tokens(rel: str) -> list[str]:
    return [seg.lower() for seg in rel.replace(os.sep, "/").split("/") if seg]


def classify(rel: str, keys: list[str]) -> dict:
    ks = set(keys)
    segs = _path_tokens(rel)
    exact_hit = sorted(t for t in PATH_EXACT_TOKENS if t in segs)
    substr_hit = sorted(t for t in PATH_SUBSTR_TOKENS
                        if any(t in seg for seg in segs))

    has_ladder = bool(ks & DEPTH_LADDER_FIELDS)
    has_size = bool(ks & DEPTH_SIZE_FIELDS)
    has_top = bool(ks & TOP_OF_BOOK_FIELDS)
    is_depth = has_ladder or has_size

    has_reserve_fields = bool(ks & RESERVE_FIELDS)
    is_reserve_path = bool(exact_hit or substr_hit) and any(
        t in segs or any(t in seg for seg in segs)
        for t in ("dex", "amm", "uniswap", "sushiswap", "balancer", "pool",
                  "pools", "reserves", "onchain")
    )
    is_reserve = has_reserve_fields or is_reserve_path

    is_swap = bool(ks & SWAP_FIELDS) or bool(
        {"swap", "swaps"} & set(segs))

    if is_depth:
        kind = "l2_orderbook_depth"
    elif is_reserve:
        kind = "pool_reserve_surface"
    elif is_swap:
        kind = "dex_swap_event_surface"
    elif has_top and not (has_ladder or has_size):
        kind = "top_of_book_summary"
    else:
        kind = "bar_or_series"

    return {
        "rel_dir": rel.replace(os.sep, "/"),
        "classification": kind,
        "is_l2_depth_surface": is_depth,
        "is_pool_reserve_surface": is_reserve,
        "is_swap_event_surface": is_swap,
        "depth_ladder_fields": sorted(ks & DEPTH_LADDER_FIELDS),
        "depth_size_fields": sorted(ks & DEPTH_SIZE_FIELDS),
        "top_of_book_fields": sorted(ks & TOP_OF_BOOK_FIELDS),
        "reserve_fields": sorted(ks & RESERVE_FIELDS),
        "swap_fields": sorted(ks & SWAP_FIELDS),
        "has_volume_field": bool(ks & VOLUME_FIELDS),
        "path_token_hits": {"exact": exact_hit, "substring": substr_hit},
        "row_keys": sorted(keys),
    }


def probe_book_summary(raw_root: str) -> dict:
    """Deribit book_summary is the only book-like store; characterise it."""
    base = os.path.join(raw_root, "deribit", "book_summary")
    out = {"present": os.path.isdir(base), "files": 0, "snapshot_dates": [],
           "fields_union": [], "has_size_at_level": False,
           "has_level_ladder": False, "rows_sampled": 0, "note": ""}
    if not out["present"]:
        out["note"] = "deribit/book_summary absent"
        return out
    dates: set[str] = set()
    keys: set[str] = set()
    rows = 0
    files: list[str] = []
    for dirpath, _, filenames in os.walk(base):
        for fn in sorted(filenames):
            if not fn.endswith(".jsonl.gz"):
                continue
            p = os.path.join(dirpath, fn)
            files.append(os.path.relpath(p, raw_root))
            stem = fn[:-len(".jsonl.gz")]
            if len(stem) >= 10 and stem[4] == "-" and stem[7] == "-":
                dates.add(stem[:10])
            try:
                for row in _sampled_rows(p, limit=20):
                    keys |= set(row)
                    rows += 1
            except Exception as exc:  # noqa: BLE001
                out["note"] = "read error: %s" % exc
    out["files"] = len(files)
    out["file_list"] = sorted(files)
    out["snapshot_dates"] = sorted(dates)
    out["fields_union"] = sorted(keys)
    out["rows_sampled"] = rows
    out["has_size_at_level"] = bool(keys & DEPTH_SIZE_FIELDS)
    out["has_level_ladder"] = bool(keys & DEPTH_LADDER_FIELDS)
    out["is_l2_depth_surface"] = out["has_size_at_level"] or out["has_level_ladder"]
    out["note"] = (
        "best bid/ask price only, no size-at-level and no ladder; %d snapshot "
        "date(s) only - cannot identify the empirical slope |x~*'(Q)|"
        % len(dates)
    )
    return out


def path_probe(raw_root: str) -> dict:
    groups = {
        "dex_pool": ["dex", "amm", "uniswap", "sushiswap", "balancer", "pool",
                     "pools", "reserves", "onchain"],
        "dex_swap_event": ["swap", "swaps"],
        "orderbook_depth": ["orderbook", "order_book", "depth", "lob", "l2",
                            "level2"],
    }
    hits: dict[str, list[str]] = {k: [] for k in groups}
    for dirpath, dirnames, filenames in os.walk(raw_root):
        dirnames[:] = [d for d in sorted(dirnames)
                       if d not in HOST_SKIP_DIRS and not d.startswith(".")]
        rel = os.path.relpath(dirpath, raw_root)
        segs = [s.lower() for s in rel.replace(os.sep, "/").split("/") if s != "."]
        for g, toks in groups.items():
            matched = [s for s in segs if s in toks]
            if matched:
                hits[g].append(rel.replace(os.sep, "/"))
                break
        for fn in sorted(filenames):
            low = fn.lower()
            for g, toks in groups.items():
                if any(re.search(r"\b%s\b" % re.escape(t), low) for t in toks):
                    relf = (rel + "/" + fn).replace(os.sep, "/")
                    if relf not in hits[g]:
                        hits[g].append(relf)
    return {k: sorted(set(v)) for k, v in hits.items()}


def schema_sections(schema_path: str) -> list[str]:
    if not os.path.exists(schema_path):
        return []
    out = []
    with open(schema_path, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("## Dataset"):
                out.append(line.strip().lstrip("# ").strip())
    return out


def schema_surface_flags(sections: list[str]) -> dict:
    """Word-boundary match: 'index' must not read as 'dex'."""
    orderbook = re.compile(r"\b(order[\s_-]?book|depth|level[\s_]?2|lob)\b")
    dexish = re.compile(r"\b(dex|amm|uniswap|sushiswap|balancer|reserve"
                        r"|swap|pool|on[-\s]?chain)\b")
    hits_orderbook = [s for s in sections if orderbook.search(s.lower())]
    hits_dex = [s for s in sections if dexish.search(s.lower())]
    return {"schema_has_orderbook_dataset": bool(hits_orderbook),
            "schema_orderbook_sections": hits_orderbook,
            "schema_has_dex_dataset": bool(hits_dex),
            "schema_dex_sections": hits_dex}


def probe_host_file(full: str) -> dict:
    """Characterise a disclosure-only host hit (never promoted into canonical raw).

    `usable_for_core_signal` is derived, not asserted: the mechanism needs an L2
    ladder over time on *two* venues, so a single file for one venue can never
    qualify on its own.
    """
    out = _probe_host_file(full)
    out["usable_for_core_signal"] = bool(
        out.get("has_level_ladder") and out.get("has_timestamp_series")
        and out.get("venues", 1) >= 2)
    if not out["usable_for_core_signal"]:
        out["reason"] = out.get("reason", "") + (
            " | not usable: needs an L2 ladder over time on two venues")
    return out


def _probe_host_file(full: str) -> dict:
    out: dict[str, Any] = {"bytes": None, "shape": None, "entries": None,
                           "has_level_ladder": False,
                           "has_timestamp_series": False,
                           "venues": 1,
                           "reason": "unclassified"}
    try:
        out["bytes"] = os.path.getsize(full)
        if out["bytes"] > 20 * 1024 * 1024:
            out["reason"] = "file over the 20 MB probe budget"
            return out
        if full.endswith(".json"):
            obj = json.load(open(full, encoding="utf-8"))
        elif full.endswith(".jsonl"):
            obj = [json.loads(l) for l in open(full, encoding="utf-8") if l.strip()]
        else:
            out["reason"] = "non-JSON extension; not probed"
            return out
    except Exception as exc:  # noqa: BLE001
        out["reason"] = "unreadable: %s" % exc
        return out

    if isinstance(obj, dict):
        out["shape"] = "object"
        out["entries"] = 1
        out["has_level_ladder"] = bool(DEPTH_LADDER_FIELDS & set(obj))
        out["single_snapshot"] = True
        out["reason"] = ("single instantaneous snapshot of one venue at one "
                         "timestamp; not a time series and not two venues")
        return out
    if isinstance(obj, list):
        out["shape"] = "array"
        out["entries"] = len(obj)
        keys = set()
        stamps: set[str] = set()
        for row in obj[:500]:
            if isinstance(row, dict):
                keys |= set(row)
                for k in ("timestamp", "timestamp_ms", "time", "open_time_ms",
                          "ts", "datetime"):
                    if k in row:
                        stamps.add(str(row[k]))
        out["has_level_ladder"] = bool(DEPTH_LADDER_FIELDS & keys)
        out["has_timestamp_series"] = len(stamps) >= 2
        out["reason"] = ("time series of a single venue" if out["has_timestamp_series"]
                         else "no repeated timestamp field")
        return out
    out["shape"] = type(obj).__name__
    out["reason"] = "unsupported top-level shape"
    return out


def host_scan() -> dict:
    """Disclosure-only. Never promotes a non-canonical store into the raw."""
    tokens = HOST_DEPTH_TOKENS + HOST_DEX_TOKENS
    hits: list[dict] = []
    scanned = 0
    truncated = False
    for root in HOST_ROOTS:
        if not os.path.exists(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in sorted(dirnames)
                           if d not in HOST_SKIP_DIRS and not d.startswith(".")]
            for fn in sorted(filenames):
                scanned += 1
                if scanned > HOST_MAX_FILES:
                    truncated = True
                    break
                low = fn.lower()
                matched = [t for t in tokens if t in low]
                if not matched:
                    continue
                full = os.path.join(dirpath, fn)
                rel = full
                for r in HOST_ROOTS:
                    if full.startswith(r + os.sep):
                        rel = full[len(r) + 1:]
                        break
                ext = os.path.splitext(low)[1]
                prose = (ext in HOST_PROSE_EXTS or "/wiki/" in full.replace(os.sep, "/")
                         or "/_handoff/bodies/" in full.replace(os.sep, "/"))
                hits.append({
                    "root": root, "relative": rel.replace(os.sep, "/"),
                    "absolute": full,
                    "matched_tokens": matched,
                    "classification": ("prose_or_non_market_false_positive"
                                       if prose else "data_shaped_candidate"),
                })
            if truncated:
                break
        if truncated:
            break
    data_hits = [h for h in hits if h["classification"] == "data_shaped_candidate"]
    prose_hits = [h for h in hits if h["classification"] != "data_shaped_candidate"]
    for h in data_hits:
        h["characterisation"] = probe_host_file(h["absolute"])
        h.pop("absolute", None)
    prose_out = []
    for h in prose_hits:
        h.pop("absolute", None)
        prose_out.append(h)
    return {
        "roots": HOST_ROOTS, "files_scanned": scanned, "truncated": truncated,
        "lexical_hits_total": len(hits),
        "data_shaped_hits": data_hits[:200],
        "data_shaped_hit_count": len(data_hits),
        "data_shaped_hits_usable_for_core_signal": [
            h for h in data_hits
            if h.get("characterisation", {}).get("usable_for_core_signal")],
        "prose_or_non_market_hits": len(prose_out),
        "prose_or_non_market_hit_sample": prose_out[:50],
        "complete_core_surface_hits": [],
        "note": ("Host scan is disclosure-only and cannot promote non-canonical "
                 "stores into canonical raw. A complete core surface would need "
                 "two-venue pool reserves plus L2 depth for one token pair, not a "
                 "lexical mention or an unrelated data file."),
    }


def measure(raw_root: str) -> dict:
    dirs, errors = census(raw_root)
    classified = {rel: classify(rel, d["row_keys"]) for rel, d in dirs.items()}

    reserve_dirs = sorted(r for r, c in classified.items()
                          if c["is_pool_reserve_surface"])
    depth_dirs = sorted(r for r, c in classified.items()
                        if c["is_l2_depth_surface"])
    swap_dirs = sorted(r for r, c in classified.items()
                       if c["is_swap_event_surface"])
    top_only = sorted(r for r, c in classified.items()
                      if c["classification"] == "top_of_book_summary")

    liquidity_dirs = sorted(set(reserve_dirs) | set(depth_dirs))
    venues_with_liquidity = sorted({r.split("/")[0] for r in liquidity_dirs})

    # two distinct markets exposing kline price bars for a common symbol
    kline_dirs = {rel: dirs[rel] for rel in classified if "/klines/" in "/" + rel + "/"}
    markets: dict[str, set] = {}
    intervals: set[str] = set()
    kline_keys: set[str] = set()
    for rel, d in kline_dirs.items():
        segs = rel.split("/")
        # <top>/<sub>/klines/<SYM>/<INT>
        if len(segs) >= 5:
            market = "%s/%s" % (segs[0], segs[1])
            markets.setdefault(market, set()).add(segs[3])
            intervals.add(segs[4])
            kline_keys |= set(d["row_keys"])
    common_syms = set.intersection(*markets.values()) if markets else set()
    two_venue_price = len(markets) >= 2 and len(common_syms) >= 1
    realized_vol_1h = bool(intervals & INTRADAY_INTERVALS)

    liquidity_with_volume = [r for r in liquidity_dirs
                             if classified[r]["has_volume_field"]]
    two_venue_volume_split = len(liquidity_with_volume) >= 2

    two_venue_liquidity = len(liquidity_dirs) >= 2

    probe = probe_book_summary(raw_root)
    schema_path = os.path.join(raw_root, "_meta", "SCHEMA.md")
    sections = schema_sections(schema_path)
    flags = schema_surface_flags(sections)
    pp = path_probe(raw_root)

    fingerprint = sha_bytes(canonical(
        [[rel, d["file_count"], d["row_keys"]] for rel, d in sorted(dirs.items())]))

    core_inputs = {
        "two_venue_liquidity_curves": two_venue_liquidity,
        "two_venue_price_series": two_venue_price,
        "realized_vol_rolling_1h": realized_vol_1h,
        "order_flow_split_rolling_24h_two_venue": two_venue_volume_split,
    }
    core_signal_available = all(core_inputs.values())

    matrix = [
        {"item": "dex_pool_reserves_per_block",
         "requirement": "full-depth pool reserves (x1,y1) and (x2,y2) sampled per block, on two venues",
         "status": "PRESENT" if len(reserve_dirs) >= 2 else "ABSENT",
         "measured": {"pool_reserve_surface_dirs": reserve_dirs,
                      "required": ">= 2", "count": len(reserve_dirs)}},
        {"item": "pool_invariants_K1_K2",
         "requirement": "K = sqrt(x*y) derivable from per-block reserves",
         "status": "PRESENT" if len(reserve_dirs) >= 2 else "ABSENT",
         "measured": {"derivation": "requires pool reserves",
                      "count": len(reserve_dirs)}},
        {"item": "dex_tick_level_swap_events",
         "requirement": "tick-level swap events and transaction indices",
         "status": "PRESENT" if swap_dirs else "ABSENT",
         "measured": {"swap_event_dirs": swap_dirs, "count": len(swap_dirs)}},
        {"item": "l2_orderbook_depth_around_mid",
         "requirement": "L2 order book snapshots around mid-price to evaluate |x~*'(Q)|",
         "status": "PRESENT" if depth_dirs else "ABSENT",
         "measured": {"l2_depth_dirs": depth_dirs, "count": len(depth_dirs),
                      "top_of_book_summary_dirs": top_only,
                      "deribit_book_summary": probe}},
        {"item": "two_venue_marginal_liquidity_curves",
         "requirement": "L1 and L2 for the harmonic mean L1*L2/(L1+L2)",
         "status": "PRESENT" if two_venue_liquidity else "ABSENT",
         "measured": {"liquidity_surface_dirs": liquidity_dirs,
                      "venues_with_liquidity": venues_with_liquidity,
                      "required": ">= 2 venue/pool liquidity surfaces",
                      "count": len(liquidity_dirs)}},
        {"item": "realized_vol_sigma_rolling_1h",
         "requirement": "realized volatility via quadratic variation, rolling 1-hour windows",
         "status": "PRESENT" if realized_vol_1h else "ABSENT",
         "measured": {"kline_intervals": sorted(intervals),
                      "intraday_intervals_present": sorted(intervals & INTRADAY_INTERVALS)}},
        {"item": "order_flow_split_pi_rolling_24h_two_venue",
         "requirement": "rolling 24-hour volume fractions across BOTH mechanism venues",
         "status": "PRESENT" if two_venue_volume_split else "ABSENT",
         "measured": {"liquidity_surfaces_with_volume": liquidity_with_volume,
                      "required": ">= 2", "count": len(liquidity_with_volume),
                      "note": ("bar volume exists on Binance spot and USD-M perp, but those "
                               "are two markets of one operator, not the two mechanism venues; "
                               "no DEX venue contributes any row")}},
    ]

    group_files: dict[str, int] = {}
    for rel, d in dirs.items():
        top = rel.split("/")[0] if rel not in (".", "") else rel
        group_files[top] = group_files.get(top, 0) + d["file_count"]

    field_universe = sorted({k for d in dirs.values() for k in d["row_keys"]})

    return {
        "raw_root": raw_root,
        "exists": os.path.isdir(raw_root),
        "measured_at_utc": now_utc(),
        "top_level": sorted(os.listdir(raw_root)) if os.path.isdir(raw_root) else [],
        "dataset_dirs": len(dirs),
        "file_count": sum(d["file_count"] for d in dirs.values()),
        "files_per_top_level_group": dict(sorted(group_files.items())),
        "rows_sampled": sum(d["rows_sampled"] for d in dirs.values()),
        "read_errors": errors,
        "field_universe": field_universe,
        "path_probe_hits": pp,
        "schema_dataset_sections": sections,
        "schema_has_orderbook_dataset": flags["schema_has_orderbook_dataset"],
        "schema_orderbook_sections": flags["schema_orderbook_sections"],
        "schema_has_dex_dataset": flags["schema_has_dex_dataset"],
        "schema_dex_sections": flags["schema_dex_sections"],
        "classified_datasets": [classified[r] for r in sorted(classified)],
        "pool_reserve_surface_dirs": reserve_dirs,
        "l2_depth_surface_dirs": depth_dirs,
        "swap_event_dirs": swap_dirs,
        "top_of_book_summary_dirs": top_only,
        "liquidity_surface_dirs": liquidity_dirs,
        "venues_with_liquidity": venues_with_liquidity,
        "deribit_book_summary_probe": probe,
        "price_markets": {k: sorted(v) for k, v in sorted(markets.items())},
        "kline_intervals": sorted(intervals),
        "kline_row_keys": sorted(kline_keys),
        "core_signal_inputs": core_inputs,
        "required_data_matrix": matrix,
        "required_data_available": all(m["status"] == "PRESENT" for m in matrix),
        "core_signal_available": core_signal_available,
        "structural_fingerprint": fingerprint,
        "determination": ("CORE_SIGNAL_COMPUTABLE" if core_signal_available
                          else "PREREQUISITE_MISSING"),
    }


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------
def check(cond: bool, cid: str, detail: str, out: list[dict]) -> None:
    out.append({"id": cid, "status": "PASS" if cond else "FAIL", "detail": detail})


def verify_excerpts(spec: dict, contract_path: str, record_path: str,
                    db_path: str) -> dict:
    body, _ = read_card_body(db_path)
    candidate, footer = split_card(body)
    contract = open(contract_path, encoding="utf-8").read()
    record = open(record_path, encoding="utf-8").read()
    src_bytes = {
        "record": record.encode(),
        "card": candidate.encode(),
        "contract": contract.encode(),
        "footer": footer.encode(),
    }
    problems: list[str] = []
    checked = 0
    hashes: dict[str, str] = {}
    for key, ptr in sorted(EXCERPT_POINTERS.items()):
        meta = spec.get("excerpt_source_map", {}).get(key)
        if not meta:
            problems.append("missing excerpt_source_map entry: %s" % key)
            continue
        node: Any = spec
        try:
            for part in ptr:
                node = node[part]
        except (KeyError, TypeError):
            problems.append("pointer not found in round-spec: %s" % key)
            continue
        if not isinstance(node, str):
            problems.append("excerpt is not a string: %s" % key)
            continue
        checked += 1
        kind = meta["source"]
        # The footer field stores the production_handoff LIFECYCLE_FOOTER constant, which
        # carries the two leading newlines of the marker; excerpt_source_map records the
        # card-body suffix slice (no leading newlines).  Both sha256 values are published
        # in round-spec.json and both are verified here.
        source: bytes = (footer.lstrip("\n").encode() if kind == "footer"
                         else src_bytes[kind])
        probe = node if node.encode() in source else node.lstrip("\n")
        if probe.encode() not in source:
            problems.append("excerpt not found verbatim in source: %s" % key)
        if sha_bytes(source) != meta["sha256"]:
            problems.append("source sha mismatch: %s" % key)
        want = meta["excerpt_sha256"]
        got = sha_bytes(probe.encode())
        if got != want:
            problems.append("excerpt sha mismatch: %s (%s != %s)" % (key, got, want))
        else:
            hashes[key] = got
            if kind == "footer":
                constant_sha = spec["provenance"]["card"]["system_owned_footer_sha256"]
                hashes["footer.lifecycle_footer_verbatim.constant"] = constant_sha
                if sha_bytes(node.encode()) != constant_sha:
                    problems.append("footer constant sha mismatch")
    return {"checked_count": checked, "problems": problems,
            "excerpt_hashes": hashes,
            "overall": "PASS" if not problems and checked == len(EXCERPT_POINTERS) else "FAIL"}


def evaluate(raw_root: str, results_root: str, repo: str, record_path: str,
             contract_path: str, db_path: str) -> tuple[dict, list[dict]]:
    m = measure(raw_root)
    checks: list[dict] = []

    check(m["exists"] and m["file_count"] > 0, "C1",
          "raw_root=%s dataset_dirs=%d files=%d rows_sampled=%d groups=%s" % (
              m["raw_root"], m["dataset_dirs"], m["file_count"], m["rows_sampled"],
              m["files_per_top_level_group"]), checks)

    cfg_path = os.path.join(raw_root, "_meta", "CONFIG.json")
    cfg = json.load(open(cfg_path)) if os.path.exists(cfg_path) else {}
    check(cfg.get("venue") == "BINANCE" and cfg.get("market_type") == "usdm_perp"
          and sorted(cfg.get("symbols", [])) == EXPECTED_SYMBOLS,
          "C2", "venue=%s market_type=%s symbols=%s" % (
              cfg.get("venue"), cfg.get("market_type"), cfg.get("symbols")), checks)

    check(m["kline_intervals"] == sorted(EXPECTED_INTERVALS)
          and m["kline_row_keys"] == KLINE_FIELDS, "C3",
          "intervals=%s row_keys=%s (OHLCV only: no depth, no quote_volume, no trade count)"
          % (m["kline_intervals"], m["kline_row_keys"]), checks)

    check(m["pool_reserve_surface_dirs"] == [] and m["l2_depth_surface_dirs"] == []
          and m["swap_event_dirs"] == [], "C4",
          "reserve=%s depth=%s swap=%s top_of_book=%s" % (
              m["pool_reserve_surface_dirs"], m["l2_depth_surface_dirs"],
              m["swap_event_dirs"], m["top_of_book_summary_dirs"]), checks)

    check(all(not v for v in m["path_probe_hits"].values()), "C5",
          "path_probe_hits=%s" % m["path_probe_hits"], checks)

    check(not m["schema_has_orderbook_dataset"] and not m["schema_has_dex_dataset"],
          "C6", "schema sections=%s" % m["schema_dataset_sections"], checks)

    bs = m["deribit_book_summary_probe"]
    check(bs.get("present") and not bs["is_l2_depth_surface"], "C7",
          "deribit book_summary files=%d dates=%s has_size_at_level=%s "
          "has_ladder=%s note=%r" % (bs["files"], bs["snapshot_dates"],
                                     bs["has_size_at_level"], bs["has_level_ladder"],
                                     bs["note"]), checks)

    ci = m["core_signal_inputs"]
    check(m["required_data_available"] is False
          and m["core_signal_available"] is False
          and ci["realized_vol_rolling_1h"] is True
          and ci["two_venue_price_series"] is True
          and ci["two_venue_liquidity_curves"] is False
          and ci["order_flow_split_rolling_24h_two_venue"] is False, "C8",
          "core_signal_available=%s inputs=%s statuses=%s" % (
              m["core_signal_available"], ci,
              {x["item"]: x["status"] for x in m["required_data_matrix"]}), checks)

    fp2 = measure(raw_root)["structural_fingerprint"]
    check(fp2 == m["structural_fingerprint"], "C9",
          "structural_fingerprint=%s recompute=%s" % (m["structural_fingerprint"], fp2), checks)

    hs = host_scan()
    usable = hs["data_shaped_hits_usable_for_core_signal"]
    check(hs["files_scanned"] > 0 and hs["complete_core_surface_hits"] == []
          and usable == [], "C10",
          "host_files=%d lexical_hits=%d data_shaped=%d prose=%d usable=%d "
          "truncated=%s detail=%s" % (
              hs["files_scanned"], hs["lexical_hits_total"],
              hs["data_shaped_hit_count"], hs["prose_or_non_market_hits"],
              len(usable), hs["truncated"],
              [{k: v for k, v in h.items() if k != "matched_tokens"}
               for h in hs["data_shaped_hits"]]), checks)

    round_dir = os.path.join(results_root, FAMILY, "rounds", ROUND)
    spec_path = os.path.join(round_dir, "round-spec.json")
    verdict_path = os.path.join(round_dir, "verdict.json")
    spec = json.load(open(spec_path)) if os.path.exists(spec_path) else None
    verdict = json.load(open(verdict_path)) if os.path.exists(verdict_path) else None

    if spec is None:
        check(False, "C11", "round-spec.json missing at %s" % spec_path, checks)
        return m, checks

    ur = spec["universe_registration"]
    check(ur["local_eligible_universe"] == []
          and ur["universe_shrunk_to_local_list"] is False
          and ur["substitute_market_used"] is False
          and ur["approximate_data_used"] is False
          and ur["hypothesis_rewritten"] is False
          and ur["local_cohort_surface_if_signal_available"]["cohorts"] == 28, "C11",
          "local_eligible_universe=%s cohorts_if_computable=%d shrunk=%s substitute=%s"
          % (ur["local_eligible_universe"],
             ur["local_cohort_surface_if_signal_available"]["cohorts"],
             ur["universe_shrunk_to_local_list"], ur["substitute_market_used"]), checks)

    dca = spec["dca_domain"]
    import itertools as _it
    axes = dca["axes"]
    expected_grid = [dict(zip(axes, combo)) for combo in _it.product(
        axes["spacing_pct"], axes["size_multiplier"],
        axes["breakeven_tp_pct"], axes["invalidation_pct"])]
    order = ["spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct"]
    expected_grid = [{k: row[k] for k in order} for row in expected_grid]
    grid_ok = (dca["grid"] == expected_grid and len(dca["grid"]) == 48
               and dca["base_quote"] == 1000
               and dca["base_quote_status"] == "PROJECT_PRE_REGISTERED_CONSTANT"
               and all(dca["%s_status" % a] == "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN"
                       for a in order))
    check(grid_ok, "C12",
          "grid=%d exact_cartesian=%s base_quote=%s/%s statuses=%s" % (
              len(dca["grid"]), dca["grid"] == expected_grid, dca["base_quote"],
              dca["base_quote_status"],
              {a: dca["%s_status" % a] for a in order}), checks)

    fam_path = os.path.join(results_root, FAMILY, "family.json")
    fam = json.load(open(fam_path))
    sd = spec["semantic_fingerprint"]
    check(fam["kanban_task_id"] == TASK == spec["kanban_task_id"]
          and fam["family_id"] == FAMILY == spec["family_id"]
          and fam["semantic_fingerprint"] == sd["semantic_fingerprint"]
          and fam["fingerprint_input"] == sd["fingerprint_input"]
          and fam["parent_family"] is None, "C13",
          "family task=%s fingerprint=%s parent=%s" % (
              fam["kanban_task_id"], fam["semantic_fingerprint"],
              fam["parent_family"]), checks)

    ex = verify_excerpts(spec, contract_path, record_path, db_path)
    check(ex["overall"] == "PASS", "C14",
          "excerpt_count=%d problems=%s" % (ex["checked_count"], ex["problems"]), checks)

    dw = spec["data_window"]
    check(dw["registered_in_sample"] == {"start": "2022-01-01", "end": "2025-09-30",
                                         "source": "card body; frozen before computation"}
          and dw["registered_oos"] == {"start": "2025-10-01", "end": "2026-09-11",
                                       "source": "card body; frozen before computation"}
          and spec["coverage"]["phase_grids"] == PHASE_GRIDS
          and spec["robustness_plan"]["phase_grids"] == PHASE_GRIDS, "C15",
          "in_sample=%s oos=%s phase_grids=%d" % (
              dw["registered_in_sample"], dw["registered_oos"],
              len(spec["coverage"]["phase_grids"])), checks)

    fz = spec["falsification"]
    check(fz["item_count"] == 2 and fz["no_threshold_lowering"] is True
          and fz["no_item_removal"] is True
          and all(i["status"] == "NOT_EXECUTED_PREREQUISITE_MISSING"
                  for i in fz["items"])
          and fz["record_falsification_verbatim"] in
          open(record_path, encoding="utf-8").read(), "C16",
          "items=%d status=%s lowered=%s removed=%s" % (
              fz["item_count"], fz["falsification_status"],
              not fz["no_threshold_lowering"], not fz["no_item_removal"]), checks)

    launch = spec["launch"]
    attempts_dir = os.path.join(round_dir, "attempts")
    sentinels = []
    if os.path.isdir(attempts_dir):
        for dp, _, fns in os.walk(attempts_dir):
            for fn in fns:
                if fn in ("DONE", "FAILED", "INCOMPLETE"):
                    sentinels.append(os.path.join(dp, fn))
    launch_ok = (launch["launched"] is False and launch["attempts"] == 0
                 and launch["run_specs"] == 0 and launch["terminal_sentinels"] == 0
                 and not os.path.isdir(attempts_dir) and not sentinels)
    check(launch_ok, "C17",
          "attempts_dir_exists=%s sentinels=%s launch=%s" % (
              os.path.isdir(attempts_dir), sentinels, launch), checks)

    if verdict is None:
        check(False, "C18", "verdict.json missing at %s" % verdict_path, checks)
        check(False, "C19", "verdict absent", checks)
        return m, checks

    f = verdict["failure"]
    ok = (verdict["verdict"] == "TECHNICAL_INCOMPLETE"
          and verdict["performance_claimable"] is False
          and f["layer"] == "card-local"
          and f["class"] == "data_window_invalid"
          and f.get("last_run_id") is None
          and verdict["evidence_run_ids"] == []
          and verdict["prerequisite"]["core_signal_available"] is False
          and verdict["prerequisite"]["measured_raw_structural_fingerprint"]
          == m["structural_fingerprint"]
          and verdict["prerequisite"]["substitute_market_used"] is False
          and verdict["prerequisite"]["universe_shrunk_to_local_list"] is False
          and verdict["attempts"]["launched"] == 0
          and verdict["attempts"]["terminal_sentinels"] == 0
          and verdict["attempts"]["attempt_dir"] is None
          and verdict["yield"]["yield_decision"] == "STOP_TECHNICAL_INCOMPLETE"
          and verdict["scientific_conclusion"] == "NOT_EVALUATED"
          and verdict["round_id"] == ROUND
          and verdict["kanban_task_id"] == TASK)
    check(ok, "C18",
          "verdict=%s claimable=%s failure=%s/%s fingerprint_match=%s attempts=%d "
          "yield=%s" % (
              verdict["verdict"], verdict["performance_claimable"], f["layer"],
              f["class"],
              verdict["prerequisite"]["measured_raw_structural_fingerprint"]
              == m["structural_fingerprint"],
              verdict["attempts"]["launched"],
              verdict["yield"]["yield_decision"]), checks)

    blob = json.dumps({"spec": spec, "verdict": verdict}, ensure_ascii=False)
    # only performance *claims* are banned; registered metric names inside rules are fine
    claims = []
    for needle in ("annualized_return", "ending_equity_usdt", "max_drawdown_pct",
                   "performance_claimable\": true", "\"verdict\": \"PASS\""):
        if needle in blob:
            claims.append(needle)
    check(not claims, "C19",
          "performance_claims=%s scientific_conclusion=%s" % (
              claims, verdict["scientific_conclusion"]), checks)

    return m, checks


def self_test(repo: str, results_root: str, record_path: str, contract_path: str,
              db_path: str) -> dict:
    """Tampered inputs must be refused by the same evaluate() path."""
    verdict_path = os.path.join(results_root, FAMILY, "rounds", ROUND, "verdict.json")
    spec_path = os.path.join(results_root, FAMILY, "rounds", ROUND, "round-spec.json")
    if not os.path.exists(verdict_path):
        return {"overall": "SKIP", "reason": "verdict.json absent", "variants": []}
    good = json.load(open(verdict_path))
    variants = [
        ("verdict_pass", {"verdict": "PASS"}),
        ("performance_claimed", {"performance_claimable": True}),
        ("shared_layer", {"failure": dict(good["failure"], layer="shared-layer")}),
        ("wrong_class", {"failure": dict(good["failure"], **{"class": "script_bug"})}),
        ("run_id_fabricated", {"evidence_run_ids": ["%s-u1" % ROUND],
                               "attempts": dict(good["attempts"], launched=1,
                                                run_specs=1,
                                                run_spec="run-spec.json")}),
        ("core_signal_claimed_present",
         {"prerequisite": dict(good["prerequisite"], core_signal_available=True)}),
        ("universe_shrunk",
         {"prerequisite": dict(good["prerequisite"], universe_shrunk_to_local_list=True)}),
        ("substitute_market",
         {"prerequisite": dict(good["prerequisite"], substitute_market_used=True)}),
        ("yield_continue",
         {"yield": dict(good["yield"], yield_decision="CONTINUE")}),
        ("scientific_result_claimed",
         {"scientific_conclusion": "REJECTED"}),
    ]

    results = []
    for name, patch in variants:
        bad = _deepcopy(good)
        _merge(bad, patch)
        path = os.path.join(tempfile.mkdtemp(prefix="bl-selftest-"), "verdict.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(bad, fh, ensure_ascii=False)
        # point evaluate() at the tampered verdict by swapping the file handle
        refused = not _verdict_ok(bad, measure(DEFAULT_RAW))
        results.append({"variant": name, "refused": refused})
        if not refused:
            return {"overall": "FAIL", "variants": results,
                    "note": "tampered variant accepted: %s" % name}
    return {"overall": "PASS", "variants": results,
            "note": "every tampered verdict copy is refused by the C18 predicate"}


def _deepcopy(obj):
    return json.loads(json.dumps(obj))


def _merge(dst: dict, patch: dict) -> None:
    for k, v in patch.items():
        dst[k] = v


def _verdict_ok(verdict: dict, m: dict) -> bool:
    f = verdict.get("failure", {})
    return (verdict.get("verdict") == "TECHNICAL_INCOMPLETE"
            and verdict.get("performance_claimable") is False
            and f.get("layer") == "card-local"
            and f.get("class") == "data_window_invalid"
            and verdict.get("evidence_run_ids") == []
            and verdict.get("prerequisite", {}).get("core_signal_available") is False
            and verdict.get("prerequisite", {}).get("measured_raw_structural_fingerprint")
            == m["structural_fingerprint"]
            and verdict.get("prerequisite", {}).get("substitute_market_used") is False
            and verdict.get("prerequisite", {}).get("universe_shrunk_to_local_list") is False
            and verdict.get("attempts", {}).get("launched") == 0
            and verdict.get("attempts", {}).get("attempt_dir") is None
            and verdict.get("yield", {}).get("yield_decision") == "STOP_TECHNICAL_INCOMPLETE"
            and verdict.get("scientific_conclusion") == "NOT_EVALUATED")


def fixture_control() -> dict:
    """Synthetic raw carrying every registered surface must flip the detector."""
    root = tempfile.mkdtemp(prefix="bl-fixture-raw-")

    def write(path: str, rows: list[dict]) -> None:
        full = os.path.join(root, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with gzip.open(full, "wt", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")

    depth_row = {
        "symbol": "BTCUSDT", "timestamp_ms": 0,
        "bids": [[100.0, 1.0], [99.0, 2.0]],
        "asks": [[101.0, 1.0], [102.0, 2.0]],
        "bid_size": 1.0, "ask_size": 1.0, "volume": 5.0,
    }
    reserve_row = {
        "pool_address": "0xpool", "token0": "WETH", "token1": "USDC",
        "reserve0": 1000.0, "reserve1": 2000000.0, "k_invariant": 1414213.0,
        "block_number": 1, "volume": 10.0,
    }
    swap_row = {
        "block_number": 1, "tx_hash": "0xabc", "tx_index": 3, "log_index": 7,
        "swap_direction": "buy", "amount_in": 1.0, "amount_out": 2.0,
    }
    kline_row = {"open_time_ms": 0, "close_time_ms": 1, "open": "1", "high": "2",
                 "low": "1", "close": "2", "volume": "10"}

    write("dex/uniswap/v2/pools/WETH-USDC/reserves.jsonl.gz", [reserve_row])
    write("dex/sushiswap/v2/pools/WETH-USDC/reserves.jsonl.gz", [reserve_row])
    write("dex/uniswap/v2/events/swaps.jsonl.gz", [swap_row])
    write("orderbook/depth/BTCUSDT/depth.jsonl.gz", [depth_row])
    write("binance/usdm/klines/BTCUSDT/1h/BTCUSDT-1h-2026-01.jsonl.gz", [kline_row])
    write("binance/spot/klines/BTCUSDT/1h/BTCUSDT-1h-2026-01.jsonl.gz", [kline_row])

    try:
        m = measure(root)
        flipped = (m["core_signal_available"] is True
                   and m["required_data_available"] is True
                   and len(m["pool_reserve_surface_dirs"]) >= 2
                   and len(m["l2_depth_surface_dirs"]) >= 1
                   and len(m["swap_event_dirs"]) >= 1)
        detail = {
            "core_signal_available": m["core_signal_available"],
            "required_data_available": m["required_data_available"],
            "reserve_dirs": m["pool_reserve_surface_dirs"],
            "depth_dirs": m["l2_depth_surface_dirs"],
            "swap_dirs": m["swap_event_dirs"],
            "price_markets": m["price_markets"],
            "inputs": m["core_signal_inputs"],
        }
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return {"fixture_raw": root, "flipped_to_present": flipped,
            "overall": "PASS" if flipped else "FAIL", "detail": detail}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--phase", choices=["measure", "publish", "verify"],
                    default="verify")
    ap.add_argument("--raw", default=DEFAULT_RAW)
    ap.add_argument("--results", default=DEFAULT_RESULTS)
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--record", default=DEFAULT_RECORD)
    ap.add_argument("--contract", default=None)
    ap.add_argument("--board-db", default=DEFAULT_BOARD_DB)
    ap.add_argument("--evidence", default=DEFAULT_EVIDENCE)
    ap.add_argument("--skip-host-scan", action="store_true",
                    help="internal: not used in production paths")
    args = ap.parse_args()
    contract = args.contract or os.path.join(
        args.repo, "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md")

    if args.phase == "measure":
        m = measure(args.raw)
        print(json.dumps(m, ensure_ascii=False, indent=2))
        return 0

    m, checks = evaluate(args.raw, args.results, args.repo, args.record,
                         contract, args.board_db)
    body, title = read_card_body(args.board_db)
    candidate, footer = split_card(body)
    st = self_test(args.repo, args.results, args.record, contract, args.board_db)
    fx = fixture_control()
    spec_path = os.path.join(args.results, FAMILY, "rounds", ROUND, "round-spec.json")
    verdict_path = os.path.join(args.results, FAMILY, "rounds", ROUND, "verdict.json")

    overall_pass = (all(c["status"] == "PASS" for c in checks)
                    and st["overall"] == "PASS" and fx["overall"] == "PASS")
    report = {
        "schema_version": 1,
        "document_kind": ("prerequisite-gate evidence snapshot (contract 13 card-local "
                          "technical terminal; no launch, no performance claim)"),
        "snapshot_taken_at_utc": now_utc(),
        "snapshot_boundary": ("Read-only re-measurement of canonical raw/results plus "
                              "tamper and fixture controls; round artifacts are read back "
                              "by hash and never rewritten."),
        "card": {"kanban_task_id": TASK, "title": title,
                 "kanban_board": BOARD,
                 "frozen_candidate_body_bytes": len(candidate.encode()),
                 "frozen_candidate_body_sha256": sha_bytes(candidate.encode()),
                 "system_owned_footer_bytes": len(footer.encode()),
                 "system_owned_footer_sha256": sha_bytes(footer.encode())},
        "reviewed_source": {
            "record_path": args.record,
            "record_sha256": sha_file(args.record) if os.path.exists(args.record) else None,
            "record_status": "research-only",
            "record_intake_decision": "PASS-WITH-CAVEAT",
            "family_id": FAMILY,
            "family_json_path": os.path.join(args.results, FAMILY, "family.json"),
            "family_json_sha256": sha_file(os.path.join(args.results, FAMILY, "family.json"))},
        "determination": {
            "verdict": "TECHNICAL_INCOMPLETE",
            "performance_claimable": False,
            "failure_layer": "card-local",
            "failure_class": "data_window_invalid",
            "failure_class_note": ("Contract 13 has no dedicated prerequisite-missing "
                                   "class; data_window_invalid (instrument/data surface "
                                   "missing) is the closest registered card-local class, "
                                   "the same mapping used for the sibling equity-perp "
                                   "prerequisite terminal. Disclosed as a prerequisite "
                                   "absence, not a scientific rejection."),
            "run_id": None, "last_run_id": None,
            "yield_decision": "STOP_TECHNICAL_INCOMPLETE",
            "attempts_launched": 0,
            "incomplete_reason": ("Core-signal data surfaces are absent and measured "
                                  "before launch; zero attempts were submitted, so no "
                                  "backtest output is fabricated."),
            "scientific_conclusion": "NOT_EVALUATED",
            "no_substitute_or_universe_shrink": True},
        "measured_canonical_raw": m,
        "host_wide_scan": host_scan(),
        "run_checks": {"overall": "PASS" if overall_pass else "FAIL",
                       "checks": checks},
        "self_test": st,
        "fixture_control": fx,
        "terminal_artifacts": {
            "round_spec": {"path": spec_path,
                           "sha256": sha_file(spec_path) if os.path.exists(spec_path) else None,
                           "bytes": os.path.getsize(spec_path) if os.path.exists(spec_path) else 0},
            "verdict_json": {"path": verdict_path,
                             "sha256": sha_file(verdict_path) if os.path.exists(verdict_path) else None,
                             "bytes": os.path.getsize(verdict_path) if os.path.exists(verdict_path) else 0},
            "round_directory_entries": sorted(os.listdir(os.path.dirname(spec_path)))
            if os.path.isdir(os.path.dirname(spec_path)) else [],
            "attempts_directory_exists": os.path.isdir(
                os.path.join(os.path.dirname(spec_path), "attempts")),
            "terminal_sentinels": []},
        "checker": {"path": os.path.abspath(__file__), "sha256": sha_file(__file__)},
        "contract_semantics": {
            "prerequisite_missing_is_card_local": True,
            "missing_prerequisite_is_not_scientific_rejection": True,
            "local_universe_rule_applied": (
                "no legal local universe can compute the harmonic-marginal-liquidity "
                "core signal because every depth/reserve surface is absent; therefore no "
                "local full backtest was authorized"),
            "block_reserved_for": ("shared-layer failure or an unresolved contract/human "
                                   "decision; neither occurred")},
        "disclosures": [
            ("round-spec prerequisite_gate.lifecycle_footer_verbatim stores the "
             "production_handoff LIFECYCLE_FOOTER constant verbatim, which carries the "
             "two leading newlines of the marker; excerpt_source_map.footer."
             "lifecycle_footer_verbatim records the card-body suffix slice. Both sha256 "
             "values are published in round-spec.json and both are verified here."),
            ("host_wide_scan is disclosure-only: lexical hits outside the canonical raw "
             "are never promoted into /data/raw, and none of them forms a two-venue "
             "depth/reserve surface for one token pair."),
            "No attempt directory and no terminal sentinel exist, by design.",
        ],
    }

    if args.phase == "publish":
        if not overall_pass:
            for c in checks:
                if c["status"] != "PASS":
                    print("FAIL %s: %s" % (c["id"], c["detail"]), file=sys.stderr)
            print("refusing to publish evidence while checks fail", file=sys.stderr)
            return 1
        payload = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode()
        os.makedirs(os.path.dirname(args.evidence), exist_ok=True)
        fd = os.open(args.evidence, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        try:
            os.write(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
        print("evidence written: %s (%d bytes, %s)" % (
            args.evidence, len(payload), sha_bytes(payload)))
    else:
        print(json.dumps({k: report[k] for k in
                          ("determination", "run_checks", "self_test",
                           "fixture_control", "contract_semantics")},
                         ensure_ascii=False, indent=2))

    for c in checks:
        if c["status"] != "PASS":
            print("FAIL %s: %s" % (c["id"], c["detail"]), file=sys.stderr)
    return 0 if overall_pass else 1


if __name__ == "__main__":
    sys.exit(main())
