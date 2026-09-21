#!/usr/bin/env python3
"""Independent prerequisite-gate checker for the 24/7 equity-perpetual oracle family.

The registered core signal needs (a) 24/7 crypto-listed equity perpetual marks and
indices, (b) CME E-mini ES/NQ after-hours prices, (c) primary-exchange official
cash closes, and (d) the registered 1m/5m depth/mark surfaces.  The canonical raw
store is a Binance crypto-native USD-M store plus auxiliary crypto/macro series;
it has no equity perpetual or CME E-mini surface.  This checker therefore proves
the honest terminal ``TECHNICAL_INCOMPLETE`` without shrinking the universe or
inventing a proxy backtest.

This module is read-only against canonical raw/results inputs.  ``--self-test``
creates tampered result copies and requires the checker to reject them.  The
fixture control creates a small synthetic raw tree containing the missing surfaces
and requires the raw-side detector to change.  No network, credentials, or second
backtest engine is used.
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FAMILY = "crypto-24-7-equity-perpetual-oracle-closed-window-basis-2026-09-01"
ROUND = FAMILY + "-r1"
TASK = "t_2e5ce8f3"
BOARD = "quant-strategy-research"
DEFAULT_RESULTS = "/Volumes/ExpansionDrive/qlib-results"
DEFAULT_RAW = "/Volumes/ExpansionDrive/market-data-raw"
DEFAULT_REPO = "/Users/hong/workspace/quant-runtime-pipeline"
DEFAULT_RECORD = os.path.expanduser("~/.hermes/wiki/quant/%s.md" % FAMILY)
DEFAULT_BOARD_DB = os.path.expanduser(
    "~/.hermes/kanban/boards/%s/kanban.db" % BOARD
)
DEFAULT_EVIDENCE = os.path.join(
    DEFAULT_REPO, "evidence",
    "crypto-24-7-equity-perpetual-oracle-closed-window-basis-2026-09-01-"
    "prerequisite-gate-20260921.json",
)

EXPECTED_SYMBOLS = ["BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
EXPECTED_INTERVALS = ["15m", "1d", "1h", "1w", "30m", "4h", "5m"]
EXPECTED_INSTRUMENT_IDS = [
    "BNBUSDT-PERP.BINANCE",
    "BTCUSDT-PERP.BINANCE",
    "ETHUSDT-PERP.BINANCE",
    "SOLUSDT-PERP.BINANCE",
]
KLINE_FIELDS = [
    "close", "close_time_ms", "high", "low", "open", "open_time_ms", "volume"
]
FUNDING_FIELDS = [
    "funding_price_source", "funding_rate", "funding_time_ms", "mark_price",
    "market_type", "rate_type", "symbol", "truth_status", "venue"
]
PHASE_GRIDS = [
    "historical", "oos", "full", "fee_2x", "funding_2x",
    "entry_delay_1_bar", "slippage_2ticks", "no_funding",
    "no_funding_full", "cost_attrition_40bps",
]
EQUITY_SYMBOLS = ["nvda", "aapl", "tsla", "spy"]
CME_SYMBOLS = ["es", "nq"]
PATH_PROBE_GROUPS = {
    "equity_symbols": EQUITY_SYMBOLS,
    "cme_venue": ["cme", "globex", "e_mini", "emini"],
    "cme_contracts": CME_SYMBOLS,
    "official_cash_close": ["official_close", "exchange_close", "cash_close", "primary_close"],
    "orderbook_depth": ["orderbook", "order_book", "order-depth", "order_depth", "depth", "bid_ask"],
}

DCA_AXES = {
    "spacing_pct": [0.01, 0.02, 0.03, 0.04],
    "size_multiplier": [1.0, 1.1],
    "breakeven_tp_pct": [0.01, 0.02, 0.03],
    "invalidation_pct": [0.05, 0.10],
}
DCA_SEARCH_STATUS = "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN"
DCA_CONSTANT_STATUS = "PROJECT_PRE_REGISTERED_CONSTANT"
USER_FIXED = "USER_FIXED"

# A bounded, disclosure-oriented host scan.  These are deliberately specific:
# generic words such as "close", "mark", or "es" would make the measurement
# mostly lexical collisions rather than a market-data absence test.
HOST_ROOTS = [
    os.path.expanduser("~/workspace"),
    "/Volumes/ExpansionDrive",
    os.path.expanduser("~/.hermes/wiki/quant"),
]
HOST_SKIP_DIRS = {
    ".git", ".worktrees", "__pycache__", ".pytest_cache", "node_modules",
    "venv", ".venv", "site-packages", ".Trash", "Photos Library.photoslibrary",
}
HOST_DATA_EXTS = {
    ".csv", ".parquet", ".jsonl", ".gz", ".feather", ".h5", ".npy",
    ".tsv", ".db", ".sqlite",
}


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def _sha256_text(text: str) -> str:
    return _sha256_bytes(text.encode("utf-8"))


def _load_json(path: str | Path) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _write_json(path: str | Path, value: Any) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(value, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def _now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _top_level(root: str) -> list[str]:
    if not os.path.isdir(root):
        return []
    return sorted(p.name for p in Path(root).iterdir() if not p.name.startswith("."))


def _walk_entries(root: str, max_depth: int = 12) -> list[tuple[str, str, bool]]:
    """Return (relative path, absolute path, is_file), skipping dotfiles."""
    out: list[tuple[str, str, bool]] = []
    if not os.path.isdir(root):
        return out
    base_depth = os.path.abspath(root).rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in os.walk(root):
        depth = os.path.abspath(dirpath).count(os.sep) - base_depth
        dirnames[:] = sorted(
            d for d in dirnames if not d.startswith(".") and d not in {"__pycache__"}
        )
        if depth >= max_depth:
            dirnames[:] = []
        rel_dir = os.path.relpath(dirpath, root)
        if rel_dir != ".":
            out.append((rel_dir, dirpath, False))
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            path = os.path.join(dirpath, name)
            out.append((os.path.relpath(path, root), path, True))
    return sorted(out)


def _read_text(path: str, limit: int | None = None) -> str:
    try:
        if path.endswith(".gz"):
            with gzip.open(path, "rb") as fh:
                data = fh.read() if limit is None else fh.read(limit)
        else:
            with open(path, "rb") as fh:
                data = fh.read() if limit is None else fh.read(limit)
        return data.decode("utf-8", "replace")
    except Exception as exc:  # pragma: no cover - diagnostic path
        return "<<unreadable: %s>>" % exc


def _first_json_row(path: str) -> dict[str, Any] | None:
    text = _read_text(path, 8192)
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None
    return None


def _last_json_row(path: str) -> dict[str, Any] | None:
    last = None
    try:
        if path.endswith(".gz"):
            fh = gzip.open(path, "rt", encoding="utf-8", errors="replace")
        else:
            fh = open(path, "r", encoding="utf-8", errors="replace")
        with fh:
            for line in fh:
                if line.strip():
                    try:
                        last = json.loads(line)
                    except Exception:
                        last = None
        return last if isinstance(last, dict) else None
    except Exception:
        return None


def _count_json_rows(path: str) -> int:
    count = 0
    try:
        if path.endswith(".gz"):
            fh = gzip.open(path, "rt", encoding="utf-8", errors="replace")
        else:
            fh = open(path, "r", encoding="utf-8", errors="replace")
        with fh:
            for line in fh:
                if line.strip():
                    count += 1
    except Exception:
        return -1
    return count


def _normal_segments(rel: str) -> list[str]:
    return [x for x in re.split(r"[/_.\-]+", rel.lower()) if x]


def _path_has_token(rel: str, token: str) -> bool:
    low = rel.lower()
    if token in {"official_close", "exchange_close", "cash_close", "primary_close", "order_book", "order-depth", "order_depth", "bid_ask", "e_mini", "emini"}:
        return token.replace("-", "_") in low.replace("-", "_")
    return token in _normal_segments(rel)


def _path_probe(entries: list[tuple[str, str, bool]]) -> dict[str, list[str]]:
    files_and_dirs = [rel for rel, _path, _is_file in entries]
    result: dict[str, list[str]] = {}
    for group, tokens in PATH_PROBE_GROUPS.items():
        hits = []
        for rel in files_and_dirs:
            if any(_path_has_token(rel, token) for token in tokens):
                hits.append(rel)
        result[group] = sorted(set(hits))
    return result


def _dataset_sections(schema: str) -> list[str]:
    out = []
    for line in schema.splitlines():
        if line.startswith("## Dataset:"):
            out.append(line.split("## Dataset:", 1)[1].strip())
    return sorted(out)


def _structural_fingerprint(measured: dict[str, Any]) -> str:
    selected = {
        "config_venue": measured.get("config_venue"),
        "config_market_type": measured.get("config_market_type"),
        "config_symbols": measured.get("config_symbols"),
        "config_intervals": measured.get("config_intervals"),
        "instrument_ids": measured.get("instrument_ids"),
        "instrument_types": measured.get("instrument_types"),
        "instrument_fields": measured.get("instrument_fields"),
        "usd_perp_kline_symbols": measured.get("usd_perp_kline_symbols"),
        "usd_perp_kline_intervals": measured.get("usd_perp_kline_intervals"),
        "kline_field_sets": measured.get("kline_field_sets"),
        "funding_field_sets": measured.get("funding_field_sets"),
        "auxiliary_surfaces": measured.get("auxiliary_surfaces"),
        "path_probe_hits": measured.get("path_probe_hits"),
        "schema_dataset_sections": measured.get("schema_dataset_sections"),
        "core_signal_inputs": measured.get("core_signal_inputs"),
    }
    return _sha256_text(json.dumps(selected, sort_keys=True, separators=(",", ":"), ensure_ascii=False))


def measure_raw(raw_root: str = DEFAULT_RAW) -> dict[str, Any]:
    """Re-measure the live canonical raw; never trust the round-spec for this."""
    out: dict[str, Any] = {"raw_root": raw_root, "exists": os.path.isdir(raw_root)}
    if not out["exists"]:
        return out
    entries = _walk_entries(raw_root)
    files = [(r, p) for r, p, is_file in entries if is_file]
    out["top_level"] = _top_level(raw_root)
    out["entry_count"] = len(entries)
    out["file_count"] = len(files)
    out["path_probe_hits"] = _path_probe(entries)

    config_path = os.path.join(raw_root, "_meta", "CONFIG.json")
    cfg = _load_json(config_path) if os.path.exists(config_path) else {}
    out["config_venue"] = cfg.get("venue")
    out["config_market_type"] = cfg.get("market_type")
    out["config_symbols"] = sorted(cfg.get("symbols") or [])
    out["config_intervals"] = sorted(cfg.get("intervals") or [])
    out["config_datasets"] = cfg.get("datasets") or {}

    instrument_path = os.path.join(
        raw_root, "binance/usdm/instruments/usdm-perp-instruments.json"
    )
    instrument_doc = _load_json(instrument_path) if os.path.exists(instrument_path) else {}
    instrument_rows = instrument_doc.get("instruments", []) if isinstance(instrument_doc, dict) else []
    fields_rows = [row.get("fields", row) for row in instrument_rows if isinstance(row, dict)]
    out["instrument_count"] = len(fields_rows)
    out["instrument_ids"] = sorted(str(row.get("id")) for row in fields_rows if row.get("id"))
    out["instrument_types"] = sorted({str(row.get("type")) for row in fields_rows if row.get("type")})
    out["instrument_fields"] = sorted({str(k) for row in fields_rows for k in row})
    out["instrument_equity_hits"] = sorted(
        i for i in out["instrument_ids"] if any(s.upper() in i.upper() for s in EQUITY_SYMBOLS)
    )
    out["instrument_cme_hits"] = sorted(
        i for i in out["instrument_ids"] if any(s.upper() in i.upper() for s in CME_SYMBOLS)
    )

    usd_prefix = "binance/usdm/klines/"
    mark_prefix = "binance/usdm/mark/"
    index_prefix = "binance/usdm/index/"
    premium_prefix = "binance/usdm/premium/"
    spot_prefix = "binance/spot/klines/"
    usd_kline_files = [(r, p) for r, p in files if r.startswith(usd_prefix) and r.endswith(".gz")]
    mark_files = [(r, p) for r, p in files if r.startswith(mark_prefix) and r.endswith(".gz")]
    index_files = [(r, p) for r, p in files if r.startswith(index_prefix) and r.endswith(".gz")]
    premium_files = [(r, p) for r, p in files if r.startswith(premium_prefix) and r.endswith(".gz")]
    spot_files = [(r, p) for r, p in files if r.startswith(spot_prefix) and r.endswith(".gz")]

    def surface(files_for_surface: list[tuple[str, str]]) -> dict[str, Any]:
        symbols, intervals, key_sets = set(), set(), set()
        bad = []
        for rel, path in files_for_surface:
            parts = rel.split("/")
            if len(parts) >= 5:
                symbols.add(parts[3])
                intervals.add(parts[4])
            row = _first_json_row(path)
            if row is None:
                bad.append(rel)
            else:
                key_sets.add(tuple(sorted(row)))
        return {
            "file_count": len(files_for_surface),
            "symbols": sorted(symbols),
            "intervals": sorted(intervals),
            "field_sets": [list(x) for x in sorted(key_sets)],
            "bad_files": sorted(bad),
        }

    out["usd_perp_klines"] = surface(usd_kline_files)
    out["mark_surface"] = surface(mark_files)
    out["index_surface"] = surface(index_files)
    out["premium_surface"] = surface(premium_files)
    out["spot_surface"] = surface(spot_files)
    out["usd_perp_kline_symbols"] = out["usd_perp_klines"]["symbols"]
    out["usd_perp_kline_intervals"] = out["usd_perp_klines"]["intervals"]
    out["kline_field_sets"] = out["usd_perp_klines"]["field_sets"]

    funding_files = [
        (r, p) for r, p in files
        if r.startswith("binance/usdm/funding/") and r.endswith(".gz")
    ]
    funding = {}
    funding_field_sets = set()
    for rel, path in funding_files:
        first, last = _first_json_row(path), _last_json_row(path)
        symbol = rel.split("/")[3] if len(rel.split("/")) >= 4 else rel
        fields = sorted(first) if first else []
        funding_field_sets.add(tuple(fields))
        funding[symbol] = {
            "rows": _count_json_rows(path),
            "fields": fields,
            "first_time_ms": first.get("funding_time_ms") if first else None,
            "last_time_ms": last.get("funding_time_ms") if last else None,
            "truth_statuses": sorted({
                str(x.get("truth_status"))
                for x in (first, last) if isinstance(x, dict) and x.get("truth_status") is not None
            }),
        }
    out["funding"] = funding
    out["funding_field_sets"] = [list(x) for x in sorted(funding_field_sets)]
    out["funding_symbols"] = sorted(funding)

    schema_path = os.path.join(raw_root, "_meta/SCHEMA.md")
    schema = Path(schema_path).read_text(encoding="utf-8", errors="replace") if os.path.exists(schema_path) else ""
    out["schema_dataset_sections"] = _dataset_sections(schema)
    out["schema_has_orderbook_dataset"] = bool(re.search(r"order[-_ ]?book|depth", schema, re.I))
    out["schema_has_equity_perp_dataset"] = bool(re.search(r"equity[-_ ]perpetual|single[-_ ]stock", schema, re.I))
    out["schema_has_cme_dataset"] = bool(re.search(r"CME|Globex|E-mini", schema, re.I))
    out["schema_has_official_close_dataset"] = bool(re.search(r"official.*clos|cash.*clos", schema, re.I))

    aux = []
    for prefix, label in (
        ("binance/spot/", "binance_spot"),
        ("cboe/vix/", "cboe_vix"),
        ("deribit/dvol/", "deribit_dvol"),
        ("fred/macro/", "fred_macro"),
    ):
        matching = [r for r, _p in files if r.startswith(prefix)]
        if matching:
            aux.append({"name": label, "file_count": len(matching), "sample": sorted(matching)[:5]})
    out["auxiliary_surfaces"] = aux

    core = {
        "equity_perpetual_mark_price": bool(
            out["path_probe_hits"]["equity_symbols"]
            and any("mark" in r.lower() for r in out["path_probe_hits"]["equity_symbols"])
        ),
        "equity_perpetual_index_price": bool(
            out["path_probe_hits"]["equity_symbols"]
            and any("index" in r.lower() for r in out["path_probe_hits"]["equity_symbols"])
        ),
        "cme_e_mini_es_nq_prices": bool(
            out["path_probe_hits"]["cme_venue"] and out["path_probe_hits"]["cme_contracts"]
        ),
        "primary_exchange_official_close": bool(out["path_probe_hits"]["official_cash_close"]),
        "order_book_depth": bool(out["path_probe_hits"]["orderbook_depth"]),
        "crypto_perpetual_mark_price": bool(out["mark_surface"]["file_count"]),
        "crypto_perpetual_index_price": bool(out["index_surface"]["file_count"]),
        "crypto_perpetual_funding": bool(out["funding_files"] if "funding_files" in out else funding_files),
    }
    core["core_signal_available"] = all(
        core[k] for k in (
            "equity_perpetual_mark_price",
            "equity_perpetual_index_price",
            "cme_e_mini_es_nq_prices",
            "primary_exchange_official_close",
        )
    )
    out["core_signal_inputs"] = core
    out["required_data_available"] = False
    out["local_universe"] = sorted(set(out["usd_perp_kline_symbols"]))
    out["structural_fingerprint"] = _structural_fingerprint(out)
    out["note"] = (
        "The canonical raw contains Binance USD-M crypto-native perpetual klines, "
        "mark/index/premium klines and funding for BTCUSDT/ETHUSDT/BNBUSDT/SOLUSDT, "
        "plus Binance spot, CBOE VIX, Deribit DVOL and FRED macro auxiliaries. It "
        "contains no NVDA/AAPL/TSLA/SPY equity-perpetual mark/index surface, no CME "
        "Globex ES/NQ surface, no primary-exchange official equity close surface and "
        "no order-book depth surface. The local crypto fields cannot express the "
        "registered equity-oracle mechanism."
    )
    return out


def _json_row_shape(path: str) -> list[str]:
    row = _first_json_row(path)
    return sorted(row) if row else []


def _host_file_is_data(path: str) -> bool:
    return Path(path).suffix.lower() in HOST_DATA_EXTS


def _host_path_token(rel_or_abs: str) -> list[str]:
    low = rel_or_abs.lower()
    hits = []
    for group, tokens in PATH_PROBE_GROUPS.items():
        if any(_path_has_token(low, token) for token in tokens):
            hits.append(group)
    return hits


def host_scan(roots: list[str] | None = None, max_files: int = 250000) -> dict[str, Any]:
    """Scan names plus the first row of data-shaped hits; prose is disclosed."""
    roots = roots or HOST_ROOTS
    scanned_roots, skipped_roots = [], []
    hits = []
    files_scanned = 0
    for root in roots:
        if not os.path.isdir(root):
            skipped_roots.append(root)
            continue
        scanned_roots.append(root)
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in HOST_SKIP_DIRS and not d.startswith("."))
            for name in sorted(filenames):
                if files_scanned >= max_files:
                    break
                files_scanned += 1
                path = os.path.join(dirpath, name)
                groups = _host_path_token(path)
                if not groups:
                    continue
                rel = os.path.relpath(path, root)
                record: dict[str, Any] = {
                    "root": root,
                    "path": path,
                    "relative": rel,
                    "groups": groups,
                    "suffix": Path(path).suffix.lower(),
                    "data_shaped": False,
                    "classification": None,
                    "row_keys": [],
                }
                if _host_file_is_data(path):
                    keys = _json_row_shape(path)
                    record["row_keys"] = keys
                    low_keys = " ".join(keys).lower()
                    record["data_shaped"] = bool(
                        keys and any(k in low_keys for k in (
                            "open", "high", "low", "close", "bid", "ask", "price",
                            "timestamp", "time", "volume", "funding", "mark",
                        ))
                    )
                if not record["data_shaped"]:
                    record["classification"] = "prose_or_non_market_false_positive"
                elif os.path.abspath(path).startswith(os.path.abspath(DEFAULT_RAW)):
                    record["classification"] = "canonical_raw_already_measured"
                elif "evidence" in path or "wiki" in path or path.endswith(".md"):
                    record["classification"] = "research_or_evidence_prose_surface"
                else:
                    record["classification"] = "noncanonical_data_surface_disclosed"
                hits.append(record)
            if files_scanned >= max_files:
                break
    data_hits = [x for x in hits if x["data_shaped"]]
    unclassified = [x for x in hits if not x["classification"]]
    complete_core_hits = [
        x for x in data_hits
        if set(x["groups"]) >= {
            "equity_symbols", "cme_venue", "cme_contracts",
        }
    ]
    return {
        "roots_scanned": scanned_roots,
        "roots_skipped": skipped_roots,
        "files_scanned": files_scanned,
        "probe_groups": {k: list(v) for k, v in PATH_PROBE_GROUPS.items()},
        "hits": hits,
        "data_shaped_hits": data_hits,
        "unclassified_hits": unclassified,
        "complete_core_surface_hits": complete_core_hits,
        "note": (
            "Host scan is disclosure-only and cannot promote non-canonical stores into "
            "the canonical raw. Every lexical hit is classified; a complete local core "
            "surface would need the equity-perpetual and CME groups together, not merely "
            "a prose mention or an unrelated data file."
        ),
    }


def _round_paths(results_root: str) -> dict[str, str]:
    round_dir = os.path.join(results_root, FAMILY, "rounds", ROUND)
    return {
        "family_dir": os.path.join(results_root, FAMILY),
        "family_json": os.path.join(results_root, FAMILY, "family.json"),
        "round_dir": round_dir,
        "spec": os.path.join(round_dir, "round-spec.json"),
        "verdict": os.path.join(round_dir, "verdict.json"),
        "attempts": os.path.join(round_dir, "attempts"),
        "bundle": os.path.join(round_dir, "survivor-bundle.json"),
    }


def _walk_terminal_sentinels(round_dir: str) -> list[str]:
    found = []
    if not os.path.isdir(round_dir):
        return found
    for dp, _dn, fn in os.walk(round_dir):
        for name in fn:
            if name in {"DONE", "FAILED", "INCOMPLETE"}:
                found.append(os.path.join(dp, name))
    return sorted(found)


def _walk_values(node: Any, path: tuple[str, ...] = ()) -> list[tuple[str, Any]]:
    if isinstance(node, dict):
        out = []
        for key, value in node.items():
            out.extend(_walk_values(value, path + (str(key),)))
        return out
    if isinstance(node, list):
        out = []
        for i, value in enumerate(node):
            out.extend(_walk_values(value, path + ("[%d]" % i,)))
        return out
    return [(".".join(path), node)]


PERFORMANCE_KEYS = {
    "sharpe", "cagr", "max_dd", "max_drawdown", "net_pnl", "gross_pnl",
    "ending_equity", "annualized_return", "total_return", "profit_factor",
}


def _performance_claims(doc: Any) -> list[str]:
    bad = []
    for path, value in _walk_values(doc):
        if path.rsplit(".", 1)[-1].lower() in PERFORMANCE_KEYS and value not in (None, "", [], {}, 0, False):
            bad.append(path)
    return bad


def _expected_matrix() -> dict[str, str]:
    return {
        "equity_perpetual_mark_price": "ABSENT",
        "equity_perpetual_index_price": "ABSENT",
        "cme_e_mini_es_nq_prices": "ABSENT",
        "primary_exchange_official_close": "ABSENT",
        "order_book_depth": "ABSENT",
        "equity_perpetual_funding": "ABSENT",
        "closed_window_session_calendar": "NOT_CONSTRUCTIBLE",
        "synthetic_fundamental_proxy": "NOT_CONSTRUCTIBLE",
        "closed_window_basis_zscore": "NOT_CONSTRUCTIBLE",
        "cash_reopen_reference": "NOT_CONSTRUCTIBLE",
        "falsification_reopen_events": "BLOCKED_BY_ABSENCE",
    }


def _check(checks: list[dict[str, Any]], cid: str, ok: bool, detail: str) -> None:
    checks.append({"id": cid, "status": "PASS" if ok else "FAIL", "detail": detail})


def run_checks(
    results_root: str = DEFAULT_RESULTS,
    raw_root: str = DEFAULT_RAW,
    include_host_scan: bool = False,
) -> dict[str, Any]:
    raw = measure_raw(raw_root)
    paths = _round_paths(results_root)
    checks: list[dict[str, Any]] = []
    spec = _load_json(paths["spec"]) if os.path.exists(paths["spec"]) else {}
    verdict = _load_json(paths["verdict"]) if os.path.exists(paths["verdict"]) else {}
    family = _load_json(paths["family_json"]) if os.path.exists(paths["family_json"]) else {}

    _check(
        checks, "C1",
        raw.get("exists") is True
        and raw.get("config_venue") == "BINANCE"
        and raw.get("config_market_type") == "usdm_perp"
        and raw.get("config_symbols") == EXPECTED_SYMBOLS
        and raw.get("usd_perp_kline_symbols") == EXPECTED_SYMBOLS
        and sorted(raw.get("usd_perp_kline_intervals", [])) == sorted(EXPECTED_INTERVALS),
        "venue=%s market_type=%s config_symbols=%s kline_symbols=%s intervals=%s"
        % (
            raw.get("config_venue"), raw.get("config_market_type"),
            raw.get("config_symbols"), raw.get("usd_perp_kline_symbols"),
            raw.get("usd_perp_kline_intervals"),
        ),
    )
    _check(
        checks, "C2",
        raw.get("instrument_ids") == EXPECTED_INSTRUMENT_IDS
        and raw.get("instrument_types") == ["CryptoPerpetual"]
        and raw.get("instrument_equity_hits") == []
        and raw.get("instrument_cme_hits") == [],
        "instrument_count=%s ids=%s types=%s equity_hits=%s cme_hits=%s"
        % (
            raw.get("instrument_count"), raw.get("instrument_ids"),
            raw.get("instrument_types"), raw.get("instrument_equity_hits"),
            raw.get("instrument_cme_hits"),
        ),
    )
    _check(
        checks, "C3",
        raw.get("usd_perp_klines", {}).get("file_count", 0) > 0
        and raw.get("kline_field_sets") == [KLINE_FIELDS]
        and not raw.get("usd_perp_klines", {}).get("bad_files"),
        "usd_perp_kline_files=%s field_sets=%s bad_files=%s"
        % (
            raw.get("usd_perp_klines", {}).get("file_count"),
            raw.get("kline_field_sets"),
            raw.get("usd_perp_klines", {}).get("bad_files"),
        ),
    )
    _check(
        checks, "C4",
        raw.get("mark_surface", {}).get("file_count", 0) > 0
        and raw.get("index_surface", {}).get("file_count", 0) > 0
        and raw.get("premium_surface", {}).get("file_count", 0) > 0
        and raw.get("funding_symbols") == EXPECTED_SYMBOLS
        and raw.get("funding_field_sets") == [FUNDING_FIELDS]
        and raw.get("path_probe_hits", {}).get("orderbook_depth") == [],
        "mark/index/premium=%s/%s/%s funding=%s orderbook_path_hits=%s"
        % (
            raw.get("mark_surface", {}).get("file_count"),
            raw.get("index_surface", {}).get("file_count"),
            raw.get("premium_surface", {}).get("file_count"),
            raw.get("funding_symbols"),
            raw.get("path_probe_hits", {}).get("orderbook_depth"),
        ),
    )
    _check(
        checks, "C5",
        all(not raw.get("path_probe_hits", {}).get(group) for group in PATH_PROBE_GROUPS),
        "required path probe hits=%s" % raw.get("path_probe_hits"),
    )
    _check(
        checks, "C6",
        raw.get("schema_has_orderbook_dataset") is False
        and raw.get("schema_has_equity_perp_dataset") is False
        and raw.get("schema_has_cme_dataset") is False
        and raw.get("schema_has_official_close_dataset") is False
        and "klines (Binance USD-M perpetual futures, UTC)" in raw.get("schema_dataset_sections", [])
        and "funding" in raw.get("schema_dataset_sections", [])
        and "instruments" in raw.get("schema_dataset_sections", []),
        "schema_sections=%s equity=%s cme=%s official_close=%s orderbook=%s"
        % (
            raw.get("schema_dataset_sections"),
            raw.get("schema_has_equity_perp_dataset"),
            raw.get("schema_has_cme_dataset"),
            raw.get("schema_has_official_close_dataset"),
            raw.get("schema_has_orderbook_dataset"),
        ),
    )
    core = raw.get("core_signal_inputs", {})
    _check(
        checks, "C7",
        core.get("equity_perpetual_mark_price") is False
        and core.get("equity_perpetual_index_price") is False
        and core.get("cme_e_mini_es_nq_prices") is False
        and core.get("primary_exchange_official_close") is False
        and core.get("core_signal_available") is False
        and core.get("crypto_perpetual_funding") is True,
        "core_signal_inputs=%s" % core,
    )

    host = host_scan() if include_host_scan else None
    if host is not None:
        _check(
            checks, "C8",
            host.get("files_scanned", 0) > 0
            and not host.get("unclassified_hits")
            and not host.get("complete_core_surface_hits"),
            "host_files=%s data_hits=%s unclassified=%s complete_core_hits=%s"
            % (
                host.get("files_scanned"), len(host.get("data_shaped_hits", [])),
                host.get("unclassified_hits"), host.get("complete_core_surface_hits"),
            ),
        )

    gate = spec.get("prerequisite_gate") or {}
    matrix = {row.get("item"): row for row in gate.get("required_data_matrix", []) if isinstance(row, dict)}
    expected_matrix = _expected_matrix()
    _check(
        checks, "C9",
        spec.get("family_id") == FAMILY
        and spec.get("round_id") == ROUND
        and spec.get("kanban_task_id") == TASK
        and gate.get("outcome") == "PREREQUISITE_MISSING"
        and gate.get("required_data_available") is False
        and spec.get("signal_semantics", {}).get("core_signal_available") is False,
        "spec ids=%s/%s/%s outcome=%s required=%s core_signal=%s"
        % (
            spec.get("family_id"), spec.get("round_id"), spec.get("kanban_task_id"),
            gate.get("outcome"), gate.get("required_data_available"),
            spec.get("signal_semantics", {}).get("core_signal_available"),
        ),
    )
    _check(
        checks, "C10",
        set(matrix) == set(expected_matrix)
        and all(matrix[item].get("status") == status for item, status in expected_matrix.items())
        and len(matrix) == len(expected_matrix),
        "matrix_count=%s statuses=%s" % (len(matrix), {k: matrix.get(k, {}).get("status") for k in expected_matrix}),
    )
    universe = spec.get("universe_registration") or {}
    _check(
        checks, "C11",
        universe.get("universe_shrunk_to_local_list") is False
        and universe.get("substitute_market_used") is False
        and universe.get("local_universe_used_for_backtest") is False
        and universe.get("required_data_available_local") is False
        and len(universe.get("registered_instruments", [])) >= 2,
        "registered_instruments=%s local_universe=%s shrunk=%s substitute=%s"
        % (
            universe.get("registered_instruments"), universe.get("local_instruments_present"),
            universe.get("universe_shrunk_to_local_list"), universe.get("substitute_market_used"),
        ),
    )
    launch = spec.get("launch") or {}
    sentinels = _walk_terminal_sentinels(paths["round_dir"])
    round_files = sorted(os.listdir(paths["round_dir"])) if os.path.isdir(paths["round_dir"]) else []
    _check(
        checks, "C12",
        launch.get("launched") is False
        and launch.get("attempts") == 0
        and launch.get("run_specs") == 0
        and launch.get("terminal_sentinels") == 0
        and not os.path.exists(paths["attempts"])
        and not os.path.exists(paths["bundle"])
        and sentinels == []
        and round_files == ["round-spec.json", "verdict.json"],
        "launch=%s attempts_dir=%s sentinels=%s round_files=%s"
        % (launch, os.path.exists(paths["attempts"]), sentinels, round_files),
    )
    failure = verdict.get("failure") or {}
    _check(
        checks, "C13",
        verdict.get("family_id") == FAMILY
        and verdict.get("round_id") == ROUND
        and verdict.get("kanban_task_id") == TASK
        and verdict.get("verdict") == "TECHNICAL_INCOMPLETE"
        and verdict.get("performance_claimable") is False
        and verdict.get("run_id") is None
        and failure.get("layer") == "card-local"
        and failure.get("class") == "data_window_invalid"
        and failure.get("last_run_id") is None
        and verdict.get("yield", {}).get("yield_decision") == "STOP_TECHNICAL_INCOMPLETE"
        and verdict.get("attempts", {}).get("launched") == 0
        and verdict.get("evidence_run_ids") == [],
        "verdict=%s claimable=%s failure=%s run_id=%s attempts=%s"
        % (
            verdict.get("verdict"), verdict.get("performance_claimable"), failure,
            verdict.get("run_id"), verdict.get("attempts"),
        ),
    )
    dca = spec.get("dca_domain") or {}
    declared_grid = [
        tuple(row.get(axis) for axis in DCA_AXES)
        for row in dca.get("grid", []) if isinstance(row, dict)
    ]
    expected_grid = list(itertools.product(*[DCA_AXES[a] for a in DCA_AXES]))
    invariants = " ".join(str(x).lower() for x in spec.get("user_fixed_invariants", []))
    _check(
        checks, "C14",
        dca.get("base_quote") == 1000
        and dca.get("base_quote_status") == DCA_CONSTANT_STATUS
        and all(dca.get("axes_status", {}).get(axis) == DCA_SEARCH_STATUS for axis in DCA_AXES)
        and dca.get("configs_per_cohort_per_grid") == 48
        and sorted(declared_grid) == sorted(expected_grid)
        and not any(axis.lower() in invariants for axis in DCA_AXES),
        "base_quote=%s/%s configs=%s grid=%s expected=%s axes_status=%s"
        % (
            dca.get("base_quote"), dca.get("base_quote_status"),
            dca.get("configs_per_cohort_per_grid"), len(declared_grid), len(expected_grid),
            dca.get("axes_status"),
        ),
    )
    coverage = spec.get("coverage") or {}
    sel = spec.get("selector_and_disposition") or {}
    _check(
        checks, "C15",
        coverage.get("phase_grids") == PHASE_GRIDS
        and coverage.get("cells_registered_total") == 0
        and coverage.get("cells_computed") == 0
        and sel.get("cohorts_realized") == 0
        and sel.get("survivors") == []
        and sel.get("selector") == "cohort-selector-v1"
        and sel.get("disposition") == "cohort-disposition-v1",
        "coverage=%s selector=%s"
        % ({k: coverage.get(k) for k in ("phase_grids", "cells_registered_total", "cells_computed")}, sel),
    )
    falsification = spec.get("falsification") or {}
    _check(
        checks, "C16",
        falsification.get("item_count") == 3
        and falsification.get("no_threshold_lowering") is True
        and falsification.get("no_item_removal") is True
        and str(falsification.get("falsification_status", "")).startswith("NOT_EXECUTED")
        and all(re.search(r"(?m)^%d\. \*\*" % i, falsification.get("record_falsification_verbatim", "")) for i in (1, 2, 3)),
        "item_count=%s status=%s" % (falsification.get("item_count"), falsification.get("falsification_status")),
    )
    _check(
        checks, "C17",
        family.get("family_id") == FAMILY
        and family.get("kanban_task_id") == TASK
        and family.get("kanban_board") == BOARD
        and family.get("semantic_fingerprint") == _sha256_text(family.get("fingerprint_input", "")),
        "family_id=%s task=%s fingerprint=%s"
        % (family.get("family_id"), family.get("kanban_task_id"), family.get("semantic_fingerprint")),
    )
    _check(
        checks, "C18",
        not _performance_claims(spec) and not _performance_claims(verdict)
        and verdict.get("scientific_conclusion") == "NOT_EVALUATED",
        "performance_claims spec=%s verdict=%s scientific_conclusion=%s"
        % (_performance_claims(spec), _performance_claims(verdict), verdict.get("scientific_conclusion")),
    )
    live_fp = raw.get("structural_fingerprint")
    _check(
        checks, "C19",
        gate.get("measured_available", {}).get("structural_fingerprint") == live_fp,
        "registered_measurement_fp=%s live_measurement_fp=%s"
        % (gate.get("measured_available", {}).get("structural_fingerprint"), live_fp),
    )

    result = {
        "family_id": FAMILY,
        "round_id": ROUND,
        "task_id": TASK,
        "results_root": results_root,
        "raw_root": raw_root,
        "measured_raw": raw,
        "host_scan": host,
        "checks": checks,
        "overall": "PASS" if all(c["status"] == "PASS" for c in checks) else "FAIL",
    }
    return result


def _copy_family(results_root: str, temp_root: str) -> str:
    src = os.path.join(results_root, FAMILY)
    dst = os.path.join(temp_root, FAMILY)
    shutil.copytree(src, dst)
    return dst


def _mutate_json(path: str, fn) -> None:
    doc = _load_json(path)
    fn(doc)
    _write_json(path, doc)


def _fabricate_attempt(family_dir: str) -> None:
    attempt = os.path.join(family_dir, "rounds", ROUND, "attempts", ROUND + "-u1")
    os.makedirs(attempt, exist_ok=True)
    Path(os.path.join(attempt, "INCOMPLETE")).write_text("{}\n", encoding="utf-8")


def self_test(results_root: str = DEFAULT_RESULTS, raw_root: str = DEFAULT_RAW) -> dict[str, Any]:
    variants = {
        "verdict_pass": lambda family_dir: _mutate_json(
            os.path.join(family_dir, "rounds", ROUND, "verdict.json"),
            lambda d: d.update({"verdict": "PASS"}),
        ),
        "failure_shared_layer": lambda family_dir: _mutate_json(
            os.path.join(family_dir, "rounds", ROUND, "verdict.json"),
            lambda d: d["failure"].update({"layer": "shared-layer"}),
        ),
        "run_id_fabricated": lambda family_dir: _mutate_json(
            os.path.join(family_dir, "rounds", ROUND, "verdict.json"),
            lambda d: d.update({"run_id": ROUND + "-u1"}),
        ),
        "required_data_claimed_present": lambda family_dir: _mutate_json(
            os.path.join(family_dir, "rounds", ROUND, "round-spec.json"),
            lambda d: d["prerequisite_gate"].update({"required_data_available": True}),
        ),
        "matrix_item_claimed_present": lambda family_dir: _mutate_json(
            os.path.join(family_dir, "rounds", ROUND, "round-spec.json"),
            lambda d: d["prerequisite_gate"]["required_data_matrix"][0].update({"status": "PRESENT"}),
        ),
        "universe_shrunk": lambda family_dir: _mutate_json(
            os.path.join(family_dir, "rounds", ROUND, "round-spec.json"),
            lambda d: d["universe_registration"].update({"universe_shrunk_to_local_list": True}),
        ),
        "dca_grid_truncated": lambda family_dir: _mutate_json(
            os.path.join(family_dir, "rounds", ROUND, "round-spec.json"),
            lambda d: d["dca_domain"]["grid"].pop(),
        ),
        "attempt_fabricated": _fabricate_attempt,
        "falsification_item_removed": lambda family_dir: _mutate_json(
            os.path.join(family_dir, "rounds", ROUND, "round-spec.json"),
            lambda d: d["falsification"].update({"item_count": 2}),
        ),
    }
    results = []
    overall = True
    for name, mutate in variants.items():
        tmp = tempfile.mkdtemp(prefix="xq-equity-oracle-selftest-")
        try:
            family_dir = _copy_family(results_root, tmp)
            mutate(family_dir)
            checked = run_checks(tmp, raw_root)
            failed = [c["id"] for c in checked["checks"] if c["status"] == "FAIL"]
            refused = bool(failed)
            results.append({"variant": name, "refused": refused, "failed_checks": failed})
            overall = overall and refused
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return {"self_test": results, "overall": "PASS" if overall else "FAIL"}


def _write_gzip_jsonl(path: str, rows: list[dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with gzip.GzipFile(path, "wb", mtime=0) as gz:
        for row in rows:
            gz.write((json.dumps(row, separators=(",", ":")) + "\n").encode("utf-8"))


def raw_fixture_control(raw_root: str = DEFAULT_RAW) -> dict[str, Any]:
    """Plant the missing surfaces in a temporary raw tree and require a flip."""
    tmp = tempfile.mkdtemp(prefix="xq-equity-oracle-rawfix-")
    try:
        fixture = os.path.join(tmp, "raw")
        os.makedirs(os.path.join(fixture, "_meta"), exist_ok=True)
        _write_gzip_jsonl(
            os.path.join(fixture, "binance/usdm/klines/NVDA-PERP/5m/NVDA-PERP-5m-2026-09.jsonl.gz"),
            [{"open_time_ms": 1, "close_time_ms": 2, "open": "1", "high": "1", "low": "1", "close": "1", "volume": "1"}],
        )
        _write_gzip_jsonl(
            os.path.join(fixture, "hyperliquid/equity_perp/mark/NVDA-PERP/5m/NVDA-PERP-5m-2026-09.jsonl.gz"),
            [{"open_time_ms": 1, "close_time_ms": 2, "open": "1", "high": "1", "low": "1", "close": "1", "volume": "1"}],
        )
        _write_gzip_jsonl(
            os.path.join(fixture, "hyperliquid/equity_perp/index/NVDA-PERP/5m/NVDA-PERP-5m-2026-09.jsonl.gz"),
            [{"open_time_ms": 1, "close_time_ms": 2, "open": "1", "high": "1", "low": "1", "close": "1", "volume": "1"}],
        )
        _write_gzip_jsonl(
            os.path.join(fixture, "cme/globex/ES/5m/ES-5m-2026-09.jsonl.gz"),
            [{"open_time_ms": 1, "close_time_ms": 2, "open": "1", "high": "1", "low": "1", "close": "1", "volume": "1"}],
        )
        _write_gzip_jsonl(
            os.path.join(fixture, "cme/globex/NQ/5m/NQ-5m-2026-09.jsonl.gz"),
            [{"open_time_ms": 1, "close_time_ms": 2, "open": "1", "high": "1", "low": "1", "close": "1", "volume": "1"}],
        )
        _write_gzip_jsonl(
            os.path.join(fixture, "nyse/official_close/NVDA/1d/NVDA-official-close-2026-09.jsonl.gz"),
            [{"date": "2026-09-18", "close": "1"}],
        )
        _write_gzip_jsonl(
            os.path.join(fixture, "hyperliquid/orderbook/NVDA-PERP/depth.jsonl.gz"),
            [{"ts": 1, "bid": "1", "ask": "1", "depth": 10}],
        )
        measured = measure_raw(fixture)
        core = measured.get("core_signal_inputs", {})
        flipped = (
            bool(measured.get("path_probe_hits", {}).get("equity_symbols"))
            and bool(measured.get("path_probe_hits", {}).get("cme_venue"))
            and bool(measured.get("path_probe_hits", {}).get("cme_contracts"))
            and bool(measured.get("path_probe_hits", {}).get("official_cash_close"))
            and bool(measured.get("path_probe_hits", {}).get("orderbook_depth"))
            and core.get("core_signal_available") is True
        )
        return {
            "raw_fixture_control": {
                "fixture_raw": fixture,
                "path_probe_hits": measured.get("path_probe_hits"),
                "core_signal_inputs": core,
                "flipped_to_present": flipped,
            },
            "overall": "PASS" if flipped else "FAIL",
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _resolve_sources(
    repo_root: str = DEFAULT_REPO,
    record_path: str = DEFAULT_RECORD,
    board_db: str = DEFAULT_BOARD_DB,
    task: str = TASK,
) -> dict[str, str]:
    record = Path(record_path).read_text(encoding="utf-8", errors="replace")
    contract = Path(os.path.join(repo_root, "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md")).read_text(
        encoding="utf-8", errors="replace"
    )
    con = sqlite3.connect("file:%s?mode=ro" % board_db, uri=True)
    try:
        row = con.execute("select body from tasks where id=?", (task,)).fetchone()
    finally:
        con.close()
    card_with_footer = row[0] if row else ""
    card = card_with_footer.split("\n---\nLIFECYCLE FOOTER", 1)[0]
    footer = card_with_footer[len(card):]
    return {"card": card, "record": record, "contract": contract, "footer": footer}


def _walk_verbatim(node: Any, path: tuple[str, ...] = ()) -> list[tuple[str, str]]:
    if path and path[0] == "excerpt_source_map":
        return []
    if isinstance(node, dict):
        out = []
        for key, value in node.items():
            out.extend(_walk_verbatim(value, path + (str(key),)))
        return out
    if isinstance(node, list):
        out = []
        for i, value in enumerate(node):
            out.extend(_walk_verbatim(value, path + ("[%d]" % i,)))
        return out
    if isinstance(node, str) and path and "verbatim" in path[-1].lower():
        return [(".".join(path), node)]
    return []


def verify_verbatim(
    spec_path: str,
    repo_root: str = DEFAULT_REPO,
    record_path: str = DEFAULT_RECORD,
    board_db: str = DEFAULT_BOARD_DB,
    task: str = TASK,
) -> dict[str, Any]:
    spec = _load_json(spec_path)
    sources = _resolve_sources(repo_root, record_path, board_db, task)
    source_hashes = {name: _sha256_text(text) for name, text in sources.items()}
    source_map = spec.get("excerpt_source_map", {})
    problems = []
    checked = []
    for path, value in _walk_verbatim(spec):
        entry = source_map.get(path)
        if not isinstance(entry, dict):
            problems.append({"path": path, "reason": "missing source-map entry"})
            continue
        source_name = entry.get("source")
        if source_name not in sources:
            problems.append({"path": path, "reason": "unknown source", "source": source_name})
            continue
        digest = _sha256_text(value)
        if digest != entry.get("sha256"):
            problems.append({"path": path, "reason": "excerpt digest mismatch"})
        if entry.get("source_sha256") != source_hashes[source_name]:
            problems.append({"path": path, "reason": "source digest mismatch", "source": source_name})
        if value not in sources[source_name]:
            problems.append({"path": path, "reason": "excerpt not found in source", "source": source_name})
        checked.append(path)
    stale_map = sorted(set(source_map) - set(checked))
    if stale_map:
        problems.append({"reason": "stale source-map entries", "paths": stale_map})
    return {
        "spec": spec_path,
        "checked_count": len(checked),
        "problems": problems,
        "source_hashes": source_hashes,
        "overall": "PASS" if not problems else "FAIL",
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="24/7 equity-perpetual oracle prerequisite checker")
    ap.add_argument("--results-root", default=DEFAULT_RESULTS)
    ap.add_argument("--raw-root", default=DEFAULT_RAW)
    ap.add_argument("--repo-root", default=DEFAULT_REPO)
    ap.add_argument("--record-path", default=DEFAULT_RECORD)
    ap.add_argument("--board-db", default=DEFAULT_BOARD_DB)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--measure-only", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--raw-fixture-control", action="store_true")
    ap.add_argument("--host-scan", action="store_true")
    ap.add_argument("--verify-verbatim", action="store_true")
    args = ap.parse_args(argv)

    if args.measure_only:
        out = measure_raw(args.raw_root)
    elif args.self_test:
        out = self_test(args.results_root, args.raw_root)
    elif args.raw_fixture_control:
        out = raw_fixture_control(args.raw_root)
    elif args.host_scan:
        out = host_scan()
    elif args.verify_verbatim:
        spec_path = _round_paths(args.results_root)["spec"]
        out = verify_verbatim(spec_path, args.repo_root, args.record_path, args.board_db, TASK)
    else:
        out = run_checks(args.results_root, args.raw_root, include_host_scan=False)

    if args.json or args.measure_only or args.self_test or args.raw_fixture_control or args.host_scan or args.verify_verbatim:
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        for row in out["checks"]:
            print("%-4s %-4s %s" % (row["id"], row["status"], row["detail"]))
        print("overall:", out["overall"])
    if args.measure_only or args.host_scan:
        return 0
    return 0 if out.get("overall") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
