#!/usr/bin/env python3
"""Immutable prerequisite-gate checker for the Bitcoin CVaR RaQL family.

The registered core state needs BTCUSDT daily returns *and* a daily Fear &
Greed Index (plus its seven-day momentum).  The frozen round below records the
historical prerequisite-missing decision; the canonical raw may later gain the
official FGI surface.  ``--live-recheck`` measures that current raw state
without substituting a proxy, shrinking the universe, or reopening artifacts.

The checker is intentionally read-only against live raw/results inputs.  The
--publish path creates immutable round-spec.json, verdict.json, and repository
evidence with O_EXCL.  --self-test mutates temporary artifact copies and
requires the checker to refuse each tampering variant.  --raw-fixture-control
plants a synthetic FGI surface in a temporary raw tree and requires the core
signal detector to flip to available.  No network, credentials, or secondary
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
from typing import Any, Callable

FAMILY = "crypto-bitcoin-cvar-risk-aware-q-learning-adaptive-controller-2026-09-02"
ROUND = FAMILY + "-r1"
TASK = "t_39351c26"
BOARD = "quant-strategy-research"
DEFAULT_RESULTS = "/Volumes/ExpansionDrive/qlib-results"
DEFAULT_RAW = "/Volumes/ExpansionDrive/market-data-raw"
DEFAULT_REPO = "/Users/hong/workspace/quant-runtime-pipeline"
DEFAULT_RECORD = os.path.expanduser("~/.hermes/wiki/quant/%s.md" % FAMILY)
DEFAULT_BOARD_DB = os.path.expanduser(
    "~/.hermes/kanban/boards/%s/kanban.db" % BOARD
)
DEFAULT_EVIDENCE = os.path.join(
    DEFAULT_REPO,
    "evidence",
    FAMILY + "-prerequisite-gate-20260921.json",
)

PHASE_GRIDS = [
    "historical",
    "oos",
    "full",
    "fee_2x",
    "funding_2x",
    "entry_delay_1_bar",
    "slippage_2ticks",
    "no_funding",
    "no_funding_full",
    "cost_attrition_40bps",
]
DCA_AXES = {
    "spacing_pct": [0.01, 0.02, 0.03, 0.04],
    "size_multiplier": [1.0, 1.1],
    "breakeven_tp_pct": [0.01, 0.02, 0.03],
    "invalidation_pct": [0.05, 0.10],
}
DCA_SEARCH_STATUS = "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN"
DCA_CONSTANT_STATUS = "PROJECT_PRE_REGISTERED_CONSTANT"
ACTION_WEIGHTS = [-0.5, -0.2, 0.0, 0.2, 0.5, 1.0]

FGI_PATTERN = re.compile(
    r"(?i)(?:\bfgi[_ .-]?daily\b|\bfgi\b|fear[_ .-]?greed|"
    r"fear[_ .-]?greed[_ .-]?index|alternative[._ -]?me|\bsentiment\b)"
)
FGI_SOURCE_URL = "https://api.alternative.me/fng/?limit=0"
DATA_EXTS = {
    ".csv",
    ".feather",
    ".gz",
    ".h5",
    ".json",
    ".jsonl",
    ".npy",
    ".parquet",
    ".sqlite",
    ".db",
    ".tsv",
}
HOST_ROOTS = [
    os.path.expanduser("~/workspace"),
    "/Volumes/ExpansionDrive",
    os.path.expanduser("~/.hermes/wiki/quant"),
]
HOST_SKIP_DIRS = {
    ".git",
    ".worktrees",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    "venv",
    ".venv",
    "site-packages",
    ".Trash",
    "Photos Library.photoslibrary",
}


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _sha256_text(text: str) -> str:
    return _sha256_bytes(text.encode("utf-8"))


def _sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _load_json(path: str | Path) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _write_json(path: str | Path, value: Any) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(value, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def _write_json_exclusive(path: str | Path, value: Any) -> None:
    data = (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
    except Exception:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        raise


def _now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _iso_ms(value: int | float | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc).isoformat()


def _walk_entries(root: str, max_depth: int = 16) -> list[tuple[str, str, bool]]:
    if not os.path.isdir(root):
        return []
    out: list[tuple[str, str, bool]] = []
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


def _read_bytes(path: str, full: bool = True, limit: int = 2 << 20) -> bytes:
    try:
        if path.endswith(".gz"):
            with gzip.open(path, "rb") as fh:
                return fh.read() if full else fh.read(limit)
        with open(path, "rb") as fh:
            return fh.read() if full else fh.read(limit)
    except (OSError, EOFError, gzip.BadGzipFile):
        return b""


def _read_text(path: str, full: bool = True) -> str:
    return _read_bytes(path, full=full).decode("utf-8", "replace")


def _first_json_row(path: str) -> dict[str, Any] | None:
    for line in _read_text(path, full=False).splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None
    return None


def _last_json_row(path: str) -> dict[str, Any] | None:
    last: dict[str, Any] | None = None
    for line in _read_text(path, full=True).splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            last = value
    return last


def _count_json_rows(path: str) -> int:
    count = 0
    for line in _read_text(path, full=True).splitlines():
        if line.strip():
            count += 1
    return count


def _surface(
    files: list[tuple[str, str]],
    time_key: str = "open_time_ms",
) -> dict[str, Any]:
    field_sets: set[tuple[str, ...]] = set()
    rows = 0
    first_time = None
    last_time = None
    bad_files: list[str] = []
    for rel, path in files:
        first = _first_json_row(path)
        last = _last_json_row(path)
        if first is None or last is None:
            bad_files.append(rel)
            continue
        field_sets.add(tuple(sorted(first)))
        rows += _count_json_rows(path)
        if first.get(time_key) is not None and (
            first_time is None or first[time_key] < first_time
        ):
            first_time = first[time_key]
        if last.get(time_key) is not None and (
            last_time is None or last[time_key] > last_time
        ):
            last_time = last[time_key]
    return {
        "file_count": len(files),
        "rows": rows,
        "first_time_utc": _iso_ms(first_time),
        "last_time_utc": _iso_ms(last_time),
        "field_sets": [list(x) for x in sorted(field_sets)],
        "bad_files": sorted(bad_files),
    }


def _strong_fgi_hits(raw_root: str, entries: list[tuple[str, str, bool]]) -> dict[str, Any]:
    name_hits: list[str] = []
    payload_hits: list[dict[str, Any]] = []
    metadata_hits: list[str] = []
    payload_files_scanned = 0
    for rel, path, is_file in entries:
        if FGI_PATTERN.search(rel):
            name_hits.append(rel)
        if not is_file:
            continue
        suffix = Path(path).suffix.lower()
        if suffix not in DATA_EXTS:
            continue
        if rel.startswith("_meta/") or rel == "_meta":
            metadata_text = _read_text(path, full=True)
            if FGI_PATTERN.search(metadata_text):
                metadata_hits.append(rel)
            continue
        payload_files_scanned += 1
        text = _read_bytes(path, full=False, limit=65536).decode("utf-8", "replace")
        match = FGI_PATTERN.search(text)
        if match:
            payload_hits.append(
                {
                    "relative": rel,
                    "matched_text": match.group(0),
                    "data_extension": suffix,
                    "json_row_keys": sorted((_first_json_row(path) or {}).keys()),
                }
            )
    return {
        "name_hits": sorted(set(name_hits)),
        "payload_hits": payload_hits,
        "metadata_token_hits": sorted(set(metadata_hits)),
        "payload_files_scanned": payload_files_scanned,
        "fgi_surface_present": bool(name_hits or payload_hits),
        "note": (
            "FGI detection uses explicit FGI/fear-greed/sentiment/Alternative.me "
            "tokens. Generic index-directory names are deliberately excluded."
        ),
    }


def _structural_fingerprint(measured: dict[str, Any]) -> str:
    selected = {
        "config": measured.get("config"),
        "spot_btcusdt_1d": measured.get("spot_btcusdt_1d"),
        "usdm_btcusdt_1d": measured.get("usdm_btcusdt_1d"),
        "funding_btcusdt": measured.get("funding_btcusdt"),
        "fgi_probe": measured.get("fgi_probe"),
        "schema_dataset_sections": measured.get("schema_dataset_sections"),
        "core_signal_inputs": measured.get("core_signal_inputs"),
    }
    return _sha256_text(
        json.dumps(selected, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    )


def measure_raw(raw_root: str = DEFAULT_RAW) -> dict[str, Any]:
    """Measure live canonical raw; never trust a prior round-spec measurement."""
    result: dict[str, Any] = {"raw_root": raw_root, "exists": os.path.isdir(raw_root)}
    if not result["exists"]:
        result["required_data_available"] = False
        result["core_signal_inputs"] = {"core_signal_available": False}
        return result

    entries = _walk_entries(raw_root)
    files = [(rel, path) for rel, path, is_file in entries if is_file]
    result["entry_count"] = len(entries)
    result["file_count"] = len(files)
    result["top_level"] = sorted(os.listdir(raw_root))

    config_path = os.path.join(raw_root, "_meta", "CONFIG.json")
    config = _load_json(config_path) if os.path.exists(config_path) else {}
    result["config"] = {
        "venue": config.get("venue"),
        "market_type": config.get("market_type"),
        "symbols": sorted(config.get("symbols") or []),
        "intervals": sorted(config.get("intervals") or []),
        "datasets": config.get("datasets") or {},
    }

    spot_files = [
        (rel, path)
        for rel, path in files
        if rel.startswith("binance/spot/klines/BTCUSDT/1d/") and rel.endswith(".gz")
    ]
    usdm_files = [
        (rel, path)
        for rel, path in files
        if rel.startswith("binance/usdm/klines/BTCUSDT/1d/") and rel.endswith(".gz")
    ]
    funding_files = [
        (rel, path)
        for rel, path in files
        if rel.startswith("binance/usdm/funding/BTCUSDT/") and rel.endswith(".gz")
    ]
    result["spot_btcusdt_1d"] = _surface(spot_files)
    result["usdm_btcusdt_1d"] = _surface(usdm_files)
    result["funding_btcusdt"] = _surface(funding_files, time_key="funding_time_ms")
    result["spot_btcusdt_1d"]["paths"] = [x[0] for x in spot_files]
    result["usdm_btcusdt_1d"]["paths"] = [x[0] for x in usdm_files]
    result["funding_btcusdt"]["paths"] = [x[0] for x in funding_files]

    schema_path = os.path.join(raw_root, "_meta", "SCHEMA.md")
    schema = _read_text(schema_path, full=True) if os.path.exists(schema_path) else ""
    result["schema_dataset_sections"] = [
        line.split("## Dataset:", 1)[1].strip()
        for line in schema.splitlines()
        if line.startswith("## Dataset:")
    ]
    result["schema_fgi_hits"] = sorted(
        set(m.group(0) for m in FGI_PATTERN.finditer(schema))
    )
    result["fgi_probe"] = _strong_fgi_hits(raw_root, entries)

    core = {
        "btcusdt_spot_daily_bars": bool(result["spot_btcusdt_1d"]["rows"]),
        "btcusdt_daily_return_derivable": bool(result["spot_btcusdt_1d"]["rows"]),
        "fgi_daily_values": result["fgi_probe"]["fgi_surface_present"],
        "fgi_7d_momentum_derivable": result["fgi_probe"]["fgi_surface_present"],
        "recent_return_ternary_feature": bool(result["spot_btcusdt_1d"]["rows"]),
        "negative_weight_perpetual_surface": bool(result["usdm_btcusdt_1d"]["rows"]),
        "actual_funding_surface": bool(result["funding_btcusdt"]["rows"]),
    }
    core["core_signal_available"] = all(
        core[key]
        for key in (
            "btcusdt_spot_daily_bars",
            "btcusdt_daily_return_derivable",
            "fgi_daily_values",
            "fgi_7d_momentum_derivable",
            "recent_return_ternary_feature",
        )
    )
    result["core_signal_inputs"] = core
    result["required_data_available"] = bool(core["core_signal_available"])
    result["structural_fingerprint"] = _structural_fingerprint(result)
    result["note"] = (
        "BTCUSDT spot daily bars, USD-M daily bars, and funding are readable. "
        "The frozen round's prerequisite gate records whether its historical "
        "measurement had a Fear & Greed surface; use --live-recheck for a current "
        "raw decision without rewriting that immutable round."
    )
    return result


def live_recheck(raw_root: str = DEFAULT_RAW) -> dict[str, Any]:
    """Re-check current raw without rewriting the frozen prerequisite round."""
    measured = measure_raw(raw_root)
    fgi_path = Path(raw_root) / "alternative_me" / "FGI_DAILY" / "fgi.jsonl.gz"
    rows: list[dict[str, Any]] = []
    malformed_rows = False
    try:
        with gzip.open(fgi_path, "rt", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    malformed_rows = True
                    continue
                rows.append(value)
    except (OSError, EOFError, gzip.BadGzipFile, json.JSONDecodeError):
        rows = []
        malformed_rows = True

    timestamps: list[int] = []
    timestamp_parse_error = False
    for row in rows:
        try:
            timestamps.append(int(row["timestamp"]))
        except (KeyError, TypeError, ValueError):
            timestamp_parse_error = True
    fgi_payload_ok = bool(rows) and not malformed_rows and not timestamp_parse_error and all(
        isinstance(row, dict)
        and isinstance(row.get("date"), str)
        and isinstance(row.get("timestamp"), int)
        and isinstance(row.get("open_time_ms"), int)
        and isinstance(row.get("value"), int)
        and 0 <= row["value"] <= 100
        and isinstance(row.get("value_classification"), str)
        and row.get("source") == "Alternative.me"
        and row.get("source_url") == FGI_SOURCE_URL
        and row.get("truth_status") == "official"
        and row["open_time_ms"] == row["timestamp"] * 1000
        and row["date"]
        == datetime.fromtimestamp(row["timestamp"], timezone.utc).date().isoformat()
        for row in rows
    )
    fgi_payload_ok = fgi_payload_ok and len(timestamps) >= 8
    fgi_payload_ok = fgi_payload_ok and timestamps == sorted(set(timestamps))
    fgi = {
        "status": "PRESENT" if fgi_payload_ok else "ABSENT_OR_INVALID",
        "path": str(fgi_path),
        "rows": len(rows),
        "earliest": rows[0].get("date") if rows else None,
        "latest": rows[-1].get("date") if rows else None,
        "source": "Alternative.me" if fgi_payload_ok else None,
        "source_url": FGI_SOURCE_URL if fgi_payload_ok else None,
        "truth_status": "official" if fgi_payload_ok else None,
        "monotonic_unique_timestamps": timestamps == sorted(set(timestamps)) if timestamps else False,
    }
    required_inputs = (
        "btcusdt_spot_daily_bars",
        "btcusdt_daily_return_derivable",
        "recent_return_ternary_feature",
        "negative_weight_perpetual_surface",
        "actual_funding_surface",
    )
    core_inputs = dict(measured.get("core_signal_inputs") or {})
    core_inputs["fgi_daily_values"] = fgi_payload_ok
    core_inputs["fgi_7d_momentum_derivable"] = fgi_payload_ok
    core_inputs["core_signal_available"] = fgi_payload_ok and all(
        bool(core_inputs.get(key)) for key in required_inputs
    )
    missing = [key for key, available in core_inputs.items() if key != "core_signal_available" and not available]
    return {
        "check_kind": "live_prerequisite_recheck",
        "raw_root": raw_root,
        "overall": "PASS" if core_inputs["core_signal_available"] else "FAIL",
        "fgi": fgi,
        "core_signal_inputs": core_inputs,
        "missing_inputs": missing,
        "frozen_round_artifacts_unchanged": True,
        "note": (
            "This is a read-only current-data recheck. The prior prerequisite-missing "
            "round remains immutable; newly available data requires a separate new-round "
            "decision and does not reopen or rewrite its verdict."
        ),
    }


def _host_path_has_fgi(path: str) -> bool:
    return bool(FGI_PATTERN.search(path))


def host_scan(
    roots: list[str] | None = None,
    max_files: int = 250_000,
) -> dict[str, Any]:
    """Disclosure-only scan; noncanonical files never become eligible raw."""
    roots = roots or HOST_ROOTS
    roots_scanned: list[str] = []
    roots_skipped: list[str] = []
    token_hits: list[dict[str, Any]] = []
    files_scanned = 0
    for root in roots:
        if not os.path.isdir(root):
            roots_skipped.append(root)
            continue
        roots_scanned.append(root)
        stop = False
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(
                d for d in dirnames if d not in HOST_SKIP_DIRS and not d.startswith(".")
            )
            for name in sorted(filenames):
                if files_scanned >= max_files:
                    stop = True
                    break
                files_scanned += 1
                path = os.path.join(dirpath, name)
                rel = os.path.relpath(path, root)
                path_hit = _host_path_has_fgi(path)
                suffix = Path(path).suffix.lower()
                content_hit = False
                row_keys: list[str] = []
                if suffix in DATA_EXTS:
                    text = _read_bytes(path, full=False, limit=65536).decode("utf-8", "replace")
                    content_hit = bool(FGI_PATTERN.search(text))
                    row = _first_json_row(path)
                    row_keys = sorted(row or {})
                if not path_hit and not content_hit:
                    continue
                lower_keys = {key.lower() for key in row_keys}
                has_time = bool(
                    lower_keys
                    & {"timestamp", "timestamp_ms", "time", "time_ms", "date", "event_time"}
                )
                has_market_feature = bool(
                    lower_keys
                    & {
                        "open",
                        "high",
                        "low",
                        "close",
                        "price",
                        "volume",
                        "value",
                        "fear_greed",
                        "fgi",
                    }
                )
                data_shaped = has_time and has_market_feature
                if os.path.abspath(path).startswith(os.path.abspath(DEFAULT_RAW)):
                    classification = "canonical_raw_already_measured"
                elif data_shaped:
                    classification = "noncanonical_data_surface_disclosed"
                else:
                    classification = "research_or_prose_false_positive"
                token_hits.append(
                    {
                        "root": root,
                        "relative": rel,
                        "path_token_hit": path_hit,
                        "content_token_hit": content_hit,
                        "suffix": suffix,
                        "row_keys": row_keys,
                        "data_shaped": data_shaped,
                        "classification": classification,
                    }
                )
            if stop:
                break
        if files_scanned >= max_files:
            break
    data_hits = [x for x in token_hits if x["data_shaped"]]
    unclassified = [x for x in token_hits if not x["classification"]]
    return {
        "roots_scanned": roots_scanned,
        "roots_skipped": roots_skipped,
        "files_scanned": files_scanned,
        "token_hit_count": len(token_hits),
        "data_shaped_hit_count": len(data_hits),
        "unclassified_hit_count": len(unclassified),
        "token_hits_sample": token_hits[:100],
        "noncanonical_data_hits": data_hits[:100],
        "unclassified_hits": unclassified[:100],
        "note": (
            "Host scan is disclosure-only. Research prose or a noncanonical data file "
            "cannot satisfy the canonical raw prerequisite."
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
    }


def _walk_terminal_sentinels(round_dir: str) -> list[str]:
    found: list[str] = []
    if not os.path.isdir(round_dir):
        return found
    for dirpath, _dirnames, filenames in os.walk(round_dir):
        for name in filenames:
            if name in {"DONE", "FAILED", "INCOMPLETE"}:
                found.append(os.path.join(dirpath, name))
    return sorted(found)


def _check(checks: list[dict[str, Any]], ident: str, passed: bool, detail: str) -> None:
    checks.append({"id": ident, "status": "PASS" if passed else "FAIL", "detail": detail})


def _expected_grid() -> list[dict[str, Any]]:
    return [
        dict(zip(DCA_AXES, values))
        for values in itertools.product(*(DCA_AXES[key] for key in DCA_AXES))
    ]


def _performance_claims(value: Any) -> list[str]:
    """Find actual metric-bearing output fields, not prose/falsification excerpts."""
    found: list[str] = []

    def visit(node: Any, path: tuple[str, ...] = ()) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                if "verbatim" in key.lower() or key in {"excerpt_source_map", "missing_conditions", "decisive_absences"}:
                    continue
                if key in {"sharpe", "cagr", "ending_equity", "net_pnl"} and child is not None:
                    found.append(".".join(path + (key,)))
                if key == "survivors" and child:
                    found.append(".".join(path + (key,)))
                visit(child, path + (key,))
        elif isinstance(node, list):
            for index, child in enumerate(node):
                visit(child, path + ("[%d]" % index,))

    visit(value)
    return sorted(set(found))


def run_checks(
    results_root: str = DEFAULT_RESULTS,
    raw_root: str = DEFAULT_RAW,
    include_host_scan: bool = False,
    raw_measurement: dict[str, Any] | None = None,
) -> dict[str, Any]:
    paths = _round_paths(results_root)
    checks: list[dict[str, Any]] = []
    raw = raw_measurement if raw_measurement is not None else measure_raw(raw_root)
    family = _load_json(paths["family_json"])
    spec = _load_json(paths["spec"])
    verdict = _load_json(paths["verdict"])
    gate = spec.get("prerequisite_gate") or {}
    universe = spec.get("universe_registration") or {}
    launch = spec.get("launch") or {}
    coverage = spec.get("coverage") or {}
    gate_matrix = gate.get("required_data_matrix") or []

    _check(
        checks,
        "C01",
        family.get("family_id") == FAMILY
        and family.get("kanban_task_id") == TASK
        and family.get("kanban_board") == BOARD
        and family.get("semantic_fingerprint")
        == _sha256_text(family.get("fingerprint_input", "")),
        "family identity/fingerprint is consistent",
    )
    _check(
        checks,
        "C02",
        spec.get("family_id") == FAMILY
        and spec.get("round_id") == ROUND
        and spec.get("kanban_task_id") == TASK
        and spec.get("kanban_board") == BOARD
        and spec.get("document_kind", "").startswith("prerequisite-gate"),
        "round-spec identity is pinned",
    )
    _check(
        checks,
        "C03",
        spec.get("contract_sha256") == _sha256_file(
            os.path.join(DEFAULT_REPO, "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md")
        ),
        "round-spec pins current contract bytes",
    )
    _check(
        checks,
        "C04",
        gate.get("required_data_available") is False
        and gate.get("outcome") == "PREREQUISITE_MISSING"
        and gate.get("core_signal_available") is False
        and len(gate_matrix) >= 3
        and gate_matrix[2].get("status") == "ABSENT_FROM_CANONICAL_RAW"
        and raw.get("required_data_available") is False,
        "live raw re-measurement and registered gate both deny the core prerequisite",
    )
    _check(
        checks,
        "C05",
        not raw.get("fgi_probe", {}).get("name_hits")
        and not raw.get("fgi_probe", {}).get("payload_hits")
        and not raw.get("schema_fgi_hits"),
        "canonical raw has no FGI name, payload, or schema hit",
    )
    _check(
        checks,
        "C06",
        bool(raw.get("spot_btcusdt_1d", {}).get("rows"))
        and bool(raw.get("usdm_btcusdt_1d", {}).get("rows"))
        and bool(raw.get("funding_btcusdt", {}).get("rows")),
        "available BTC price/funding surfaces were measured rather than treated as absent",
    )
    _check(
        checks,
        "C07",
        universe.get("universe_shrunk_to_local_list") is False
        and universe.get("substitute_market_used") is False
        and universe.get("local_universe_used_for_backtest") is False
        and universe.get("cohorts_realized") == 0,
        "no substitute market or post-hoc universe shrink was used",
    )
    dca = spec.get("dca_domain") or {}
    declared_grid = dca.get("grid") or []
    _check(
        checks,
        "C08",
        dca.get("base_quote") == 1000
        and dca.get("base_quote_status") == DCA_CONSTANT_STATUS
        and dca.get("axes_status") == {
            key: DCA_SEARCH_STATUS for key in DCA_AXES
        }
        and len(declared_grid) == 48
        and sorted(declared_grid, key=lambda x: json.dumps(x, sort_keys=True))
        == sorted(_expected_grid(), key=lambda x: json.dumps(x, sort_keys=True)),
        "full 48-cell DCA domain is retained",
    )
    _check(
        checks,
        "C09",
        coverage.get("phase_grids") == PHASE_GRIDS
        and coverage.get("cells_registered_total") == 0
        and coverage.get("cells_computed") == 0,
        "no computation is claimed before the prerequisite gate",
    )
    _check(
        checks,
        "C10",
        launch.get("launched") is False
        and launch.get("attempts") == 0
        and launch.get("run_specs") == 0
        and launch.get("terminal_sentinels") == 0
        and not os.path.exists(paths["attempts"])
        and not _walk_terminal_sentinels(paths["round_dir"]),
        "launch is exact-zero and has no fabricated terminal attempt",
    )
    round_files = sorted(os.listdir(paths["round_dir"]))
    _check(
        checks,
        "C11",
        round_files == ["round-spec.json", "verdict.json"],
        "round directory contains only immutable spec and verdict",
    )
    failure = verdict.get("failure") or {}
    _check(
        checks,
        "C12",
        verdict.get("family_id") == FAMILY
        and verdict.get("round_id") == ROUND
        and verdict.get("kanban_task_id") == TASK
        and verdict.get("verdict") == "TECHNICAL_INCOMPLETE"
        and verdict.get("performance_claimable") is False
        and verdict.get("run_id") is None
        and verdict.get("scientific_conclusion") == "NOT_EVALUATED"
        and failure.get("layer") == "card-local"
        and failure.get("class") == "data_window_invalid"
        and failure.get("last_run_id") is None
        and verdict.get("yield", {}).get("yield_decision")
        == "STOP_TECHNICAL_INCOMPLETE"
        and verdict.get("attempts", {}).get("launched") == 0
        and verdict.get("evidence_run_ids") == [],
        "verdict is the honest card-local technical terminal",
    )
    _check(
        checks,
        "C13",
        verdict.get("missing_conditions")
        and any("FGI" in str(x) or "Fear" in str(x) for x in verdict["missing_conditions"]),
        "verdict names the missing FGI conditions",
    )
    falsification = spec.get("falsification") or {}
    _check(
        checks,
        "C14",
        falsification.get("item_count") == 4
        and falsification.get("no_threshold_lowering") is True
        and falsification.get("no_item_removal") is True
        and str(falsification.get("falsification_status", "")).startswith("NOT_EXECUTED"),
        "falsification battery is preserved and explicitly not executed",
    )
    _check(
        checks,
        "C15",
        not _performance_claims(spec) and not _performance_claims(verdict),
        "no backtest performance claim or survivor result is present",
    )
    _check(
        checks,
        "C16",
        gate.get("measured_available", {}).get("structural_fingerprint")
        == raw.get("structural_fingerprint"),
        "registered raw measurement fingerprint matches live re-measurement",
    )
    host = host_scan() if include_host_scan else None
    result = {
        "family_id": FAMILY,
        "round_id": ROUND,
        "task_id": TASK,
        "results_root": results_root,
        "raw_root": raw_root,
        "measured_raw": raw,
        "host_scan": host,
        "checks": checks,
        "overall": "PASS" if all(x["status"] == "PASS" for x in checks) else "FAIL",
    }
    return result


def _copy_family(results_root: str, temp_root: str) -> str:
    source = os.path.join(results_root, FAMILY)
    target = os.path.join(temp_root, FAMILY)
    shutil.copytree(source, target)
    return target


def _mutate_json(path: str, mutate: Callable[[dict[str, Any]], None]) -> None:
    doc = _load_json(path)
    mutate(doc)
    _write_json(path, doc)


def _fabricate_attempt(family_dir: str) -> None:
    attempt = os.path.join(family_dir, "rounds", ROUND, "attempts", ROUND + "-u1")
    os.makedirs(attempt, exist_ok=True)
    Path(os.path.join(attempt, "INCOMPLETE")).write_text("{}\n", encoding="utf-8")


def self_test(results_root: str = DEFAULT_RESULTS, raw_root: str = DEFAULT_RAW) -> dict[str, Any]:
    variants: dict[str, Callable[[str], None]] = {
        "verdict_pass": lambda family_dir: _mutate_json(
            os.path.join(family_dir, "rounds", ROUND, "verdict.json"),
            lambda doc: doc.update({"verdict": "PASS"}),
        ),
        "required_data_claimed_present": lambda family_dir: _mutate_json(
            os.path.join(family_dir, "rounds", ROUND, "round-spec.json"),
            lambda doc: doc["prerequisite_gate"].update({"required_data_available": True}),
        ),
        "core_signal_claimed_present": lambda family_dir: _mutate_json(
            os.path.join(family_dir, "rounds", ROUND, "round-spec.json"),
            lambda doc: doc["prerequisite_gate"].update({"core_signal_available": True}),
        ),
        "matrix_fgi_claimed_present": lambda family_dir: _mutate_json(
            os.path.join(family_dir, "rounds", ROUND, "round-spec.json"),
            lambda doc: doc["prerequisite_gate"]["required_data_matrix"][2].update(
                {"status": "PRESENT"}
            ),
        ),
        "universe_shrunk": lambda family_dir: _mutate_json(
            os.path.join(family_dir, "rounds", ROUND, "round-spec.json"),
            lambda doc: doc["universe_registration"].update(
                {"universe_shrunk_to_local_list": True}
            ),
        ),
        "dca_grid_truncated": lambda family_dir: _mutate_json(
            os.path.join(family_dir, "rounds", ROUND, "round-spec.json"),
            lambda doc: doc["dca_domain"]["grid"].pop(),
        ),
        "attempt_fabricated": _fabricate_attempt,
        "falsification_item_removed": lambda family_dir: _mutate_json(
            os.path.join(family_dir, "rounds", ROUND, "round-spec.json"),
            lambda doc: doc["falsification"].update({"item_count": 3}),
        ),
        "performance_claim_added": lambda family_dir: _mutate_json(
            os.path.join(family_dir, "rounds", ROUND, "verdict.json"),
            lambda doc: doc.update({"sharpe": 0.1}),
        ),
    }
    raw_measurement = measure_raw(raw_root)
    results = []
    overall = True
    for name, mutate in variants.items():
        temp = tempfile.mkdtemp(prefix="xq-bitcoin-cvar-selftest-")
        try:
            family_dir = _copy_family(results_root, temp)
            mutate(family_dir)
            checked = run_checks(temp, raw_root, raw_measurement=raw_measurement)
            failed = [x["id"] for x in checked["checks"] if x["status"] == "FAIL"]
            refused = bool(failed)
            results.append({"variant": name, "refused": refused, "failed_checks": failed})
            overall = overall and refused
        finally:
            shutil.rmtree(temp, ignore_errors=True)
    return {"self_test": results, "overall": "PASS" if overall else "FAIL"}


def _write_gzip_jsonl(path: str, rows: list[dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with gzip.GzipFile(path, "wb", mtime=0) as gz:
        for row in rows:
            gz.write((json.dumps(row, separators=(",", ":")) + "\n").encode("utf-8"))


def raw_fixture_control(raw_root: str = DEFAULT_RAW) -> dict[str, Any]:
    """Create a small raw fixture whose FGI surface makes the gate flip."""
    temp = tempfile.mkdtemp(prefix="xq-bitcoin-cvar-rawfix-")
    try:
        fixture = os.path.join(temp, "raw")
        os.makedirs(os.path.join(fixture, "_meta"), exist_ok=True)
        _write_gzip_jsonl(
            os.path.join(
                fixture,
                "binance/spot/klines/BTCUSDT/1d/BTCUSDT-1d-2026-09.jsonl.gz",
            ),
            [
                {
                    "open_time_ms": 1,
                    "close_time_ms": 2,
                    "open": "1",
                    "high": "1",
                    "low": "1",
                    "close": "1",
                    "volume": "1",
                },
                {
                    "open_time_ms": 86400001,
                    "close_time_ms": 86400002,
                    "open": "1",
                    "high": "1",
                    "low": "1",
                    "close": "1",
                    "volume": "1",
                },
            ],
        )
        _write_gzip_jsonl(
            os.path.join(fixture, "alternative_me/FGI_DAILY/fgi.jsonl.gz"),
            [
                {"date": "1970-01-01", "value": 50},
                {"date": "1970-01-02", "value": 51},
            ],
        )
        measured = measure_raw(fixture)
        flipped = (
            measured["fgi_probe"]["fgi_surface_present"]
            and measured["core_signal_inputs"]["core_signal_available"]
        )
        return {
            "raw_fixture_control": {
                "fixture_raw": fixture,
                "fgi_probe": measured["fgi_probe"],
                "core_signal_inputs": measured["core_signal_inputs"],
                "flipped_to_present": flipped,
            },
            "overall": "PASS" if flipped else "FAIL",
        }
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def _resolve_sources(
    repo_root: str = DEFAULT_REPO,
    record_path: str = DEFAULT_RECORD,
    board_db: str = DEFAULT_BOARD_DB,
    task: str = TASK,
) -> dict[str, str]:
    record = Path(record_path).read_text(encoding="utf-8", errors="replace")
    contract_path = os.path.join(repo_root, "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md")
    contract = Path(contract_path).read_text(encoding="utf-8", errors="replace")
    con = sqlite3.connect(board_db)
    try:
        con.execute("PRAGMA query_only=ON")
        row = con.execute("select body from tasks where id=?", (task,)).fetchone()
    finally:
        con.close()
    if not row:
        raise RuntimeError("task not found: %s" % task)
    card_with_footer = row[0]
    marker = "\n---\nLIFECYCLE FOOTER"
    if marker not in card_with_footer:
        raise RuntimeError("task lifecycle footer not found")
    card, suffix = card_with_footer.split(marker, 1)
    footer = marker + suffix
    return {"card": card, "record": record, "contract": contract, "footer": footer}


def _slice(text: str, start: str, end: str | None = None) -> str:
    start_index = text.index(start)
    end_index = text.index(end, start_index) if end else len(text)
    return text[start_index:end_index].rstrip()


def _line_starting(text: str, prefix: str) -> str:
    for line in text.splitlines():
        if line.startswith(prefix):
            return line.rstrip()
    raise ValueError("line not found: %s" % prefix)


def _contract_line(text: str, needle: str) -> str:
    for line in text.splitlines():
        if needle in line:
            return line.rstrip()
    raise ValueError("contract line not found: %s" % needle)


def _excerpt_values(sources: dict[str, str]) -> dict[str, tuple[str, str]]:
    card = sources["card"]
    record = sources["record"]
    contract = sources["contract"]
    return {
        "hypothesis.card_record_title_verbatim": (_line_starting(card, "- record title"), "card"),
        "hypothesis.card_mechanism_excerpt_verbatim": (
            _slice(card, "- economic mechanism", "- signal 語意"),
            "card",
        ),
        "signal_semantics.card_signal_excerpt_verbatim": (
            _slice(card, "- signal 語意", "- 核心 hypothesis"),
            "card",
        ),
        "signal_semantics.record_signal_verbatim": (
            _slice(record, "## Signal", "## Required data"),
            "record",
        ),
        "registered_requirement.required_data_verbatim": (
            _slice(record, "## Required data", "## Execution assumptions"),
            "record",
        ),
        "registered_requirement.card_required_data_excerpt_verbatim": (
            _slice(card, "- record required data", "- crypto portability"),
            "card",
        ),
        "registered_requirement.record_portability_verbatim": (
            _slice(record, "## Crypto portability", "## Limitations"),
            "record",
        ),
        "registered_requirement.card_portability_prose_verbatim": (
            _slice(card, "- crypto portability", "- 本卡註冊"),
            "card",
        ),
        "data.card_window_verbatim": (
            _slice(card, "DATA WINDOW / SPLIT", "DCA PARAMETER DOMAIN"),
            "card",
        ),
        "dca_domain.card_dca_domain_verbatim": (
            _slice(card, "DCA PARAMETER DOMAIN", "COHORT SURVIVOR SEMANTICS"),
            "card",
        ),
        "user_fixed_invariants.card_user_fixed_verbatim": (
            _line_starting(card, "- USER_FIXED invariants"),
            "card",
        ),
        "selector_and_disposition.card_cohort_semantics_verbatim": (
            _slice(card, "COHORT SURVIVOR SEMANTICS", "ROBUSTNESS 與 FALSIFICATION"),
            "card",
        ),
        "falsification.record_falsification_verbatim": (
            _slice(record, "## Falsification plan", "## Crypto portability"),
            "record",
        ),
        "costs.record_execution_assumptions_verbatim": (
            _slice(record, "## Execution assumptions", "## Evidence"),
            "record",
        ),
        "failure_taxonomy.card_failure_taxonomy_verbatim": (
            _slice(card, "FAILURE TAXONOMY", "SURVIVOR BUNDLE"),
            "card",
        ),
        "survivor_bundle.card_survivor_bundle_verbatim": (
            _slice(card, "SURVIVOR BUNDLE", "IMPLEMENTATION RULES"),
            "card",
        ),
        "implementation.card_implementation_rules_verbatim": (
            _slice(card, "IMPLEMENTATION RULES", "GIT / OPS"),
            "card",
        ),
        "implementation.card_git_ops_verbatim": (
            _slice(card, "GIT / OPS"),
            "card",
        ),
        "prerequisite_gate.card_prerequisite_clause_verbatim": (
            _line_starting(card, "- prerequisite 條款"),
            "card",
        ),
        "prerequisite_gate.contract_technical_incomplete_verbatim": (
            _contract_line(contract, "TECHNICAL_INCOMPLETE` 必須附"),
            "contract",
        ),
        "prerequisite_gate.contract_local_universe_verbatim": (
            _contract_line(contract, "local eligible universe 與 prerequisite-missing"),
            "contract",
        ),
        "prerequisite_gate.contract_failure_class_verbatim": (
            _contract_line(contract, "| `data_window_invalid` |"),
            "contract",
        ),
        "lifecycle_footer_verbatim": (sources["footer"], "footer"),
    }


def _put_path(root: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node = root
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = value


def _walk_values(node: Any, path: tuple[str, ...] = ()) -> list[tuple[str, str, tuple[str, ...]]]:
    if isinstance(node, dict):
        out: list[tuple[str, str, tuple[str, ...]]] = []
        for key, value in node.items():
            out.extend(_walk_values(value, path + (str(key),)))
        return out
    if isinstance(node, list):
        out = []
        for index, value in enumerate(node):
            out.extend(_walk_values(value, path + ("[%d]" % index,)))
        return out
    if isinstance(node, str) and path and "verbatim" in path[-1].lower():
        return [(".".join(path), node, path)]
    return []


def _build_excerpt_map(spec: dict[str, Any], sources: dict[str, str]) -> dict[str, Any]:
    hints = _excerpt_values(sources)
    source_hashes = {name: _sha256_text(text) for name, text in sources.items()}
    source_map: dict[str, Any] = {}
    for path, value, _path_parts in _walk_values(spec):
        if path not in hints:
            raise RuntimeError("missing source hint for %s" % path)
        expected, source_name = hints[path]
        if value != expected:
            raise RuntimeError("excerpt value mismatch at %s" % path)
        source_map[path] = {
            "source": source_name,
            "sha256": _sha256_text(value),
            "source_sha256": source_hashes[source_name],
        }
    return source_map


def verify_verbatim(
    spec_path: str,
    repo_root: str = DEFAULT_REPO,
    record_path: str = DEFAULT_RECORD,
    board_db: str = DEFAULT_BOARD_DB,
) -> dict[str, Any]:
    spec = _load_json(spec_path)
    sources = _resolve_sources(repo_root, record_path, board_db, TASK)
    source_hashes = {name: _sha256_text(text) for name, text in sources.items()}
    source_map = spec.get("excerpt_source_map") or {}
    problems: list[dict[str, Any]] = []
    checked: list[str] = []
    for path, value, _path_parts in _walk_values(spec):
        entry = source_map.get(path)
        if not isinstance(entry, dict):
            problems.append({"path": path, "reason": "missing source-map entry"})
            continue
        source_name = entry.get("source")
        if source_name not in sources:
            problems.append({"path": path, "reason": "unknown source", "source": source_name})
            continue
        if entry.get("sha256") != _sha256_text(value):
            problems.append({"path": path, "reason": "excerpt digest mismatch"})
        if entry.get("source_sha256") != source_hashes[source_name]:
            problems.append({"path": path, "reason": "source digest mismatch", "source": source_name})
        if value not in sources[source_name]:
            problems.append({"path": path, "reason": "excerpt not found in source", "source": source_name})
        checked.append(path)
    stale = sorted(set(source_map) - set(checked))
    if stale:
        problems.append({"reason": "stale source-map entries", "paths": stale})
    return {
        "spec": spec_path,
        "checked_count": len(checked),
        "source_hashes": source_hashes,
        "problems": problems,
        "overall": "PASS" if not problems else "FAIL",
    }


def build_spec(
    results_root: str = DEFAULT_RESULTS,
    raw_root: str = DEFAULT_RAW,
    repo_root: str = DEFAULT_REPO,
    record_path: str = DEFAULT_RECORD,
    board_db: str = DEFAULT_BOARD_DB,
) -> tuple[dict[str, Any], dict[str, str]]:
    paths = _round_paths(results_root)
    family = _load_json(paths["family_json"])
    sources = _resolve_sources(repo_root, record_path, board_db, TASK)
    raw = measure_raw(raw_root)
    created = _now_utc()
    matrix = [
        {
            "id": "btc_spot_daily_bars",
            "requirement": "BTCUSDT spot daily bars and daily close",
            "status": "PRESENT",
            "measured": raw["spot_btcusdt_1d"],
        },
        {
            "id": "daily_simple_return",
            "requirement": "daily simple return r_t = P_t / P_{t-1} - 1",
            "status": "PRESENT",
            "measured": {"derivable_from": "btc_spot_daily_bars"},
        },
        {
            "id": "fgi_daily",
            "requirement": "Alternative.me Crypto Fear & Greed Index daily values 0-100",
            "status": "ABSENT_FROM_CANONICAL_RAW",
            "measured": raw["fgi_probe"],
        },
        {
            "id": "fgi_7d_momentum",
            "requirement": "seven-day FGI momentum Δ7d FGI_t = FGI_t - FGI_{t-7}",
            "status": "NOT_EXECUTABLE_AS_REGISTERED",
            "measured": {"blocked_by": "fgi_daily"},
        },
        {
            "id": "current_fgi_3_bins",
            "requirement": "current FGI three-bin state feature",
            "status": "NOT_EXECUTABLE_AS_REGISTERED",
            "measured": {"blocked_by": "fgi_daily"},
        },
        {
            "id": "momentum_3_bins",
            "requirement": "seven-day sentiment momentum three-bin state feature",
            "status": "NOT_EXECUTABLE_AS_REGISTERED",
            "measured": {"blocked_by": "fgi_7d_momentum"},
        },
        {
            "id": "recent_return_3_bins",
            "requirement": "recent daily return three-bin state feature",
            "status": "PRESENT",
            "measured": {"derivable_from": "btc_spot_daily_bars"},
        },
        {
            "id": "negative_weight_execution",
            "requirement": "perpetual execution surface for negative action weights",
            "status": "PRESENT",
            "measured": raw["usdm_btcusdt_1d"],
        },
        {
            "id": "actual_funding",
            "requirement": "actual historical Binance BTCUSDT funding for short exposure",
            "status": "PRESENT",
            "measured": raw["funding_btcusdt"],
        },
        {
            "id": "strict_calendar_alignment",
            "requirement": "strict calendar-date alignment across price and FGI",
            "status": "NOT_EXECUTABLE_AS_REGISTERED",
            "measured": {"blocked_by": "fgi_daily"},
        },
    ]
    dca_grid = _expected_grid()
    spec: dict[str, Any] = {
        "schema_version": 1,
        "document_kind": "prerequisite-gate pre-registration (contract 6.4 / 13 / 14.4); no launch",
        "family_id": FAMILY,
        "family_title": "Bitcoin CVaR Risk-Aware Q-Learning (RaQL) with Adaptive Finite-Budget Training Controller",
        "round_id": ROUND,
        "kanban_task_id": TASK,
        "kanban_board": BOARD,
        "created_at_utc": created,
        "authored_by": "Hermes default (Xiaoqian), card %s" % TASK,
        "contract": "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md",
        "contract_sha256": _sha256_file(
            os.path.join(repo_root, "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md")
        ),
        "contract_version": "v1.10.0",
        "provenance": {
            "record_path": record_path,
            "record_sha256": _sha256_text(sources["record"]),
            "card_sha256": _sha256_text(sources["card"]),
            "lifecycle_footer_sha256": _sha256_text(sources["footer"]),
            "family_json_path": paths["family_json"],
            "family_json_sha256": _sha256_file(paths["family_json"]),
            "family_semantic_fingerprint": family.get("semantic_fingerprint"),
            "primary_source": "arXiv:2608.04305v1 (as captured by the canonical research record)",
        },
        "hypothesis": {
            "record_title": "Bitcoin CVaR Risk-Aware Q-Learning (RaQL) with Adaptive Finite-Budget Training Controller",
            "mechanism_status": "registered_without_change; source-reported and research-interpretation text are preserved below",
            "card_record_title_verbatim": _excerpt_values(sources)["hypothesis.card_record_title_verbatim"][0],
            "card_mechanism_excerpt_verbatim": _excerpt_values(sources)["hypothesis.card_mechanism_excerpt_verbatim"][0],
            "record_source_period": ["2018-02-08", "2026-06-28"],
            "record_reported_observation_count": 3059,
        },
        "signal_semantics": {
            "state_count": 27,
            "action_count": 6,
            "action_weights": ACTION_WEIGHTS,
            "state_features": [
                "current Fear & Greed Index three bins",
                "seven-day FGI momentum three bins",
                "recent daily return three bins",
            ],
            "trade_timing": "daily close (00:00 UTC)",
            "record_signal_verbatim": _excerpt_values(sources)["signal_semantics.record_signal_verbatim"][0],
            "card_signal_excerpt_verbatim": _excerpt_values(sources)["signal_semantics.card_signal_excerpt_verbatim"][0],
        },
        "registered_requirement": {
            "portability_class": "direct",
            "source_market": "BTCUSDT spot (Binance)",
            "required_data_verbatim": _excerpt_values(sources)["registered_requirement.required_data_verbatim"][0],
            "card_required_data_excerpt_verbatim": _excerpt_values(sources)["registered_requirement.card_required_data_excerpt_verbatim"][0],
            "record_portability_verbatim": _excerpt_values(sources)["registered_requirement.record_portability_verbatim"][0],
            "card_portability_prose_verbatim": _excerpt_values(sources)["registered_requirement.card_portability_prose_verbatim"][0],
            "registration_note": "The source portability classification is direct; the system-owned lifecycle footer requires the complete legal local universe when the core signal is computable, and permits prerequisite-missing technical termination only when the necessary data type is wholly absent.",
        },
        "universe_registration": {
            "registered_source_instruments": ["BTCUSDT spot (Binance)"],
            "local_surfaces_measured": [
                "BINANCE spot BTCUSDT 1d",
                "BINANCE USD-M BTCUSDT 1d",
                "BINANCE USD-M BTCUSDT funding",
            ],
            "local_instruments_present": ["BTCUSDT"],
            "local_universe_used_for_backtest": False,
            "required_data_available_local": False,
            "core_signal_available": raw["core_signal_inputs"]["core_signal_available"],
            "universe_shrunk_to_local_list": False,
            "substitute_market_used": False,
            "cohorts_registered": 0,
            "cohorts_realized": 0,
            "reason_no_cohort": "FGI_DAILY is absent; no complete legal state vector can be computed even though BTC price and funding surfaces are readable.",
            "required_data_matrix": matrix,
        },
        "data": {
            "card_window_verbatim": _excerpt_values(sources)["data.card_window_verbatim"][0],
            "pre_registered_window_utc": ["2022-01-01T00:00:00Z", "2026-09-11T23:59:59Z"],
            "pre_registered_split": {
                "historical": ["2022-01-01", "2025-09-30"],
                "oos": ["2025-10-01", "2026-09-11"],
                "full": ["2022-01-01", "2026-09-11"],
            },
            "measured_raw_window_note": "Live raw was re-measured at gate time and extends beyond the card's frozen window; this growth is disclosed and not used to rewrite the pre-registered split.",
            "measured_spot_btcusdt_1d": raw["spot_btcusdt_1d"],
            "measured_usdm_btcusdt_1d": raw["usdm_btcusdt_1d"],
            "measured_funding_btcusdt": raw["funding_btcusdt"],
            "raw_root": raw_root,
            "raw_file_count": raw.get("file_count"),
            "raw_entry_count": raw.get("entry_count"),
        },
        "dca_domain": {
            "card_dca_domain_verbatim": _excerpt_values(sources)["dca_domain.card_dca_domain_verbatim"][0],
            "axes": DCA_AXES,
            "axes_status": {key: DCA_SEARCH_STATUS for key in DCA_AXES},
            "base_quote": 1000,
            "base_quote_status": DCA_CONSTANT_STATUS,
            "configs_per_cohort_per_grid": 48,
            "grid": dca_grid,
            "note": "The full joint domain is registered even though zero cells can execute before the prerequisite gate.",
        },
        "user_fixed_invariants": {
            "card_user_fixed_verbatim": _excerpt_values(sources)["user_fixed_invariants.card_user_fixed_verbatim"][0],
            "starting_equity_usdt": 30000,
            "numeraire": "USDT",
            "instrument_class": "linear USD-M perpetual for negative weights; spot is the source-required price surface",
            "leverage": 10,
            "tranches": 12,
            "entry_exit": "initial entry + adverse-price scale-ins; reduce-only exit",
        },
        "selector_and_disposition": {
            "selector": "cohort-selector-v1",
            "disposition": "cohort-disposition-v1",
            "cohort_slots_registered": 0,
            "cohorts_realized": 0,
            "survivors": [],
            "card_cohort_semantics_verbatim": _excerpt_values(sources)["selector_and_disposition.card_cohort_semantics_verbatim"][0],
        },
        "robustness_plan": {
            "phase_grids": PHASE_GRIDS,
            "cohort_slots_registered": 0,
            "strategy_cases_registered": 0,
            "configs_per_cohort_per_grid": 48,
            "cells_registered_total": 0,
            "cells_computed": 0,
            "coverage_gate": "unreachable until FGI_DAILY exists; no grid is silently dropped",
        },
        "falsification": {
            "item_count": 4,
            "record_falsification_verbatim": _excerpt_values(sources)["falsification.record_falsification_verbatim"][0],
            "falsification_status": "NOT_EXECUTED: prerequisite gate stopped before any historical/OOS/robustness computation",
            "no_threshold_lowering": True,
            "no_item_removal": True,
        },
        "costs": {
            "record_execution_assumptions_verbatim": _excerpt_values(sources)["costs.record_execution_assumptions_verbatim"][0],
            "baseline_fee_bps": 5,
            "funding_required_for_negative_weights": True,
            "slippage_baseline": "1 tick adverse; robustness 2 ticks adverse (research-defined)",
        },
        "failure_taxonomy": {
            "card_failure_taxonomy_verbatim": _excerpt_values(sources)["failure_taxonomy.card_failure_taxonomy_verbatim"][0],
            "layer": "card-local",
            "class": "data_window_invalid",
            "class_note": "The contract has no dedicated prerequisite-missing class; data_window_invalid is the closest registered card-local class and the mismatch is disclosed.",
        },
        "survivor_bundle": {
            "card_survivor_bundle_verbatim": _excerpt_values(sources)["survivor_bundle.card_survivor_bundle_verbatim"][0],
            "created": False,
            "reason": "No survivor bundle is legal without computed cohorts and complete coverage.",
        },
        "implementation": {
            "card_implementation_rules_verbatim": _excerpt_values(sources)["implementation.card_implementation_rules_verbatim"][0],
            "card_git_ops_verbatim": _excerpt_values(sources)["implementation.card_git_ops_verbatim"][0],
        },
        "prerequisite_gate": {
            "card_prerequisite_clause_verbatim": _excerpt_values(sources)["prerequisite_gate.card_prerequisite_clause_verbatim"][0],
            "contract_technical_incomplete_verbatim": _excerpt_values(sources)["prerequisite_gate.contract_technical_incomplete_verbatim"][0],
            "contract_local_universe_verbatim": _excerpt_values(sources)["prerequisite_gate.contract_local_universe_verbatim"][0],
            "contract_failure_class_verbatim": _excerpt_values(sources)["prerequisite_gate.contract_failure_class_verbatim"][0],
            "required_data_available": False,
            "core_signal_available": False,
            "outcome": "PREREQUISITE_MISSING",
            "verdict": "TECHNICAL_INCOMPLETE",
            "failure_layer": "card-local",
            "failure_class_used": "data_window_invalid",
            "last_run_id": None,
            "terminal_evidence": {
                "round_spec": "rounds/%s/round-spec.json" % ROUND,
                "verdict": "rounds/%s/verdict.json" % ROUND,
                "checker": "runtime/crypto_bitcoin_cvar_risk_aware_q_learning_prerequisite_check.py",
                "repo_evidence": "evidence/%s" % os.path.basename(DEFAULT_EVIDENCE),
            },
            "incomplete_reason": "FGI_DAILY is absent from canonical raw, so the registered 27-state core signal cannot be computed for any legal local cohort; zero attempts were launched and no backtest output is fabricated.",
            "required_data_matrix": matrix,
            "required_data_matrix_counts": {
                status: sum(1 for row in matrix if row["status"] == status)
                for status in sorted({row["status"] for row in matrix})
            },
            "decisive_absences": [
                "fgi_daily_absent: zero canonical raw path hits, zero non-metadata payload hits, and zero SCHEMA.md hits for FGI_DAILY/fear-greed/sentiment/Alternative.me tokens",
                "fgi_momentum_unavailable: seven-day momentum is a deterministic transform only after the daily FGI series exists",
                "state_vector_unavailable: current FGI and sentiment-momentum bins cannot be formed, so no legal 27-state sequence exists",
                "no_proxy_rule: BTC price, funding, or research prose cannot substitute for the registered sentiment feature",
            ],
            "measured_available": {
                "raw_root": raw_root,
                "raw_file_count": raw.get("file_count"),
                "raw_entry_count": raw.get("entry_count"),
                "spot_btcusdt_1d": raw["spot_btcusdt_1d"],
                "usdm_btcusdt_1d": raw["usdm_btcusdt_1d"],
                "funding_btcusdt": raw["funding_btcusdt"],
                "fgi_probe": raw["fgi_probe"],
                "structural_fingerprint": raw["structural_fingerprint"],
            },
        },
        "launch": {
            "launched": False,
            "attempts": 0,
            "run_specs": 0,
            "terminal_sentinels": 0,
            "attempt_dir": None,
            "why_not_launched": "prerequisite gate terminated before Qlib preflight/launch",
        },
        "coverage": {
            "phase_grids": PHASE_GRIDS,
            "cohort_slots_registered": 0,
            "strategy_cases_registered": 0,
            "cells_registered_per_grid": 0,
            "cells_registered_total": 0,
            "cells_computed": 0,
        },
        "non_goals": [
            "no proxy sentiment series",
            "no source-market substitution",
            "no universe shrink to price-only BTC data",
            "no parameter tuning or hypothesis rewrite",
            "no paper/testnet/live deployment",
        ],
        "lifecycle_footer_verbatim": _excerpt_values(sources)["lifecycle_footer_verbatim"][0],
        "excerpt_source_map": {},
    }
    spec["excerpt_source_map"] = _build_excerpt_map(spec, sources)
    return spec, sources


def build_verdict(spec: dict[str, Any], evidence_basename: str) -> dict[str, Any]:
    decided = spec["created_at_utc"]
    return {
        "schema_version": 1,
        "family_id": FAMILY,
        "round_id": ROUND,
        "run_id": None,
        "kanban_task_id": TASK,
        "kanban_board": BOARD,
        "verdict": "TECHNICAL_INCOMPLETE",
        "scientific_conclusion": "NOT_EVALUATED",
        "performance_claimable": False,
        "missing_conditions": [
            "FGI_DAILY / Alternative.me daily Fear & Greed values are absent from canonical raw",
            "seven-day sentiment momentum cannot be computed without FGI_DAILY",
            "the registered 27-state Markov sequence cannot be formed without current FGI and momentum bins",
            "no historical, OOS, full, or robustness cell was executable; no performance claim is permitted",
        ],
        "yield": {
            "rounds_used": 1,
            "max_rounds": 3,
            "no_progress_rounds": 1,
            "progress_evidence": [
                "round 1 completed the prerequisite measurement and immutable terminal evidence; no computational attempt was launched"
            ],
            "yield_decision": "STOP_TECHNICAL_INCOMPLETE",
            "note": "This is a measured technical termination, not a scientific REJECT. Re-entry requires a later tail card after FGI_DAILY is present; this round and its evidence remain immutable.",
        },
        "failure": {
            "layer": "card-local",
            "class": "data_window_invalid",
            "detail": "The registered core requires BTCUSDT daily returns plus a daily Fear & Greed Index and its seven-day momentum. BTC price and funding surfaces exist, but FGI_DAILY is wholly absent from canonical raw; the core mechanism is therefore not computable for any legal local cohort.",
            "failure_class_note": "The contract has no dedicated prerequisite-missing class; data_window_invalid is used as the closest registered card-local class and the mismatch is disclosed.",
            "last_run_id": None,
            "terminal_evidence": {
                "round_spec": "rounds/%s/round-spec.json" % ROUND,
                "verdict": "rounds/%s/verdict.json" % ROUND,
                "checker": "runtime/crypto_bitcoin_cvar_risk_aware_q_learning_prerequisite_check.py",
                "repo_evidence": "evidence/%s" % evidence_basename,
            },
            "incomplete_reason": "FGI_DAILY is absent from canonical raw, so the registered 27-state core signal cannot be computed for any legal local cohort; zero attempts were launched and no backtest output is fabricated.",
            "decisive_absences": [
                "canonical raw FGI path hits = 0",
                "canonical raw non-metadata FGI payload hits = 0",
                "canonical raw SCHEMA.md FGI hits = 0",
                "FGI seven-day momentum and sentiment state bins are consequently not executable",
            ],
        },
        "prerequisite": {
            "required_data_available": False,
            "core_signal_available": False,
            "outcome": "PREREQUISITE_MISSING",
            "required_data_matrix_path": "rounds/%s/round-spec.json#universe_registration.required_data_matrix" % ROUND,
        },
        "attempts": {
            "launched": 0,
            "run_specs": 0,
            "terminal_sentinels": 0,
            "run_spec": None,
            "terminal_sentinel": None,
            "attempt_dir": None,
        },
        "evidence_run_ids": [],
        "cohorts": {
            "registered_slots": 0,
            "realized": 0,
        },
        "survivors": [],
        "survivor_bundle": None,
        "coverage": {
            "cells_registered_per_grid": 0,
            "cells_registered_total": 0,
            "cells_computed": 0,
            "phase_grids": PHASE_GRIDS,
        },
        "decided_at_utc": decided,
    }


def _exclusive_probe(path: str) -> bool:
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        return True
    else:
        os.close(fd)
        os.unlink(path)
        return False


def publish(
    results_root: str = DEFAULT_RESULTS,
    raw_root: str = DEFAULT_RAW,
    repo_root: str = DEFAULT_REPO,
    record_path: str = DEFAULT_RECORD,
    board_db: str = DEFAULT_BOARD_DB,
    evidence_path: str = DEFAULT_EVIDENCE,
) -> dict[str, Any]:
    paths = _round_paths(results_root)
    if os.path.exists(paths["round_dir"]):
        raise FileExistsError("immutable round directory already exists: %s" % paths["round_dir"])
    if os.path.exists(evidence_path):
        raise FileExistsError("immutable evidence already exists: %s" % evidence_path)
    os.makedirs(os.path.dirname(paths["round_dir"]), exist_ok=True)
    os.mkdir(paths["round_dir"])
    spec, sources = build_spec(results_root, raw_root, repo_root, record_path, board_db)
    verdict = build_verdict(spec, os.path.basename(evidence_path))
    _write_json_exclusive(paths["spec"], spec)
    _write_json_exclusive(paths["verdict"], verdict)

    base_checks = run_checks(results_root, raw_root, include_host_scan=False)
    verbatim = verify_verbatim(paths["spec"], repo_root, record_path, board_db)
    fixture = raw_fixture_control(raw_root)
    selftest = self_test(results_root, raw_root)
    host = host_scan()
    o_excl = {
        "round_spec_refused_second_create": _exclusive_probe(paths["spec"]),
        "verdict_refused_second_create": _exclusive_probe(paths["verdict"]),
        "evidence_path_initially_absent": True,
    }
    evidence = {
        "schema_version": 1,
        "document_kind": "prerequisite-gate terminal evidence",
        "family_id": FAMILY,
        "round_id": ROUND,
        "kanban_task_id": TASK,
        "created_at_utc": _now_utc(),
        "checker": "runtime/crypto_bitcoin_cvar_risk_aware_q_learning_prerequisite_check.py",
        "raw_measurement": base_checks["measured_raw"],
        "host_scan": host,
        "base_checks": base_checks,
        "verbatim_check": verbatim,
        "raw_fixture_control": fixture,
        "self_test": selftest,
        "o_excl": o_excl,
        "artifacts": {
            "round_spec": paths["spec"],
            "round_spec_sha256": _sha256_file(paths["spec"]),
            "verdict": paths["verdict"],
            "verdict_sha256": _sha256_file(paths["verdict"]),
        },
        "conclusion": "PREREQUISITE_MISSING / TECHNICAL_INCOMPLETE; no backtest launch",
    }
    if base_checks["overall"] != "PASS":
        raise RuntimeError("base artifact checks failed before evidence publish")
    if verbatim["overall"] != "PASS":
        raise RuntimeError("verbatim source checks failed before evidence publish")
    if fixture["overall"] != "PASS":
        raise RuntimeError("raw fixture control failed before evidence publish")
    if selftest["overall"] != "PASS":
        raise RuntimeError("tamper self-tests failed before evidence publish")
    if o_excl["round_spec_refused_second_create"] is not True or o_excl["verdict_refused_second_create"] is not True:
        raise RuntimeError("O_EXCL immutability probe failed")
    _write_json_exclusive(evidence_path, evidence)
    return {
        "overall": "PASS",
        "artifacts": {
            "round_spec": paths["spec"],
            "verdict": paths["verdict"],
            "evidence": evidence_path,
        },
        "base_check_count": len(base_checks["checks"]),
        "verbatim_checked": verbatim["checked_count"],
        "self_test_variants": len(selftest["self_test"]),
        "host_files_scanned": host["files_scanned"],
        "raw_fixture_control": fixture["overall"],
    }


def publish_evidence(
    results_root: str = DEFAULT_RESULTS,
    raw_root: str = DEFAULT_RAW,
    repo_root: str = DEFAULT_REPO,
    record_path: str = DEFAULT_RECORD,
    board_db: str = DEFAULT_BOARD_DB,
    evidence_path: str = DEFAULT_EVIDENCE,
) -> dict[str, Any]:
    """Finish evidence publication after an interrupted/partial publish."""
    paths = _round_paths(results_root)
    if not os.path.exists(paths["spec"]) or not os.path.exists(paths["verdict"]):
        raise FileNotFoundError("immutable round artifacts are incomplete")
    if os.path.exists(evidence_path):
        raise FileExistsError("immutable evidence already exists: %s" % evidence_path)
    base_checks = run_checks(results_root, raw_root, include_host_scan=False)
    verbatim = verify_verbatim(paths["spec"], repo_root, record_path, board_db)
    fixture = raw_fixture_control(raw_root)
    selftest = self_test(results_root, raw_root)
    host = host_scan()
    o_excl = {
        "round_spec_refused_second_create": _exclusive_probe(paths["spec"]),
        "verdict_refused_second_create": _exclusive_probe(paths["verdict"]),
        "evidence_path_initially_absent": True,
    }
    evidence = {
        "schema_version": 1,
        "document_kind": "prerequisite-gate terminal evidence",
        "family_id": FAMILY,
        "round_id": ROUND,
        "kanban_task_id": TASK,
        "created_at_utc": _now_utc(),
        "checker": "runtime/crypto_bitcoin_cvar_risk_aware_q_learning_prerequisite_check.py",
        "raw_measurement": base_checks["measured_raw"],
        "host_scan": host,
        "base_checks": base_checks,
        "verbatim_check": verbatim,
        "raw_fixture_control": fixture,
        "self_test": selftest,
        "o_excl": o_excl,
        "artifacts": {
            "round_spec": paths["spec"],
            "round_spec_sha256": _sha256_file(paths["spec"]),
            "verdict": paths["verdict"],
            "verdict_sha256": _sha256_file(paths["verdict"]),
        },
        "conclusion": "PREREQUISITE_MISSING / TECHNICAL_INCOMPLETE; no backtest launch",
    }
    if base_checks["overall"] != "PASS":
        raise RuntimeError("base artifact checks failed before evidence publish")
    if verbatim["overall"] != "PASS":
        raise RuntimeError("verbatim source checks failed before evidence publish")
    if fixture["overall"] != "PASS":
        raise RuntimeError("raw fixture control failed before evidence publish")
    if selftest["overall"] != "PASS":
        raise RuntimeError("tamper self-tests failed before evidence publish")
    if o_excl["round_spec_refused_second_create"] is not True or o_excl["verdict_refused_second_create"] is not True:
        raise RuntimeError("O_EXCL immutability probe failed")
    _write_json_exclusive(evidence_path, evidence)
    return {
        "overall": "PASS",
        "artifacts": {
            "round_spec": paths["spec"],
            "verdict": paths["verdict"],
            "evidence": evidence_path,
        },
        "base_check_count": len(base_checks["checks"]),
        "verbatim_checked": verbatim["checked_count"],
        "self_test_variants": len(selftest["self_test"]),
        "host_files_scanned": host["files_scanned"],
        "host_data_shaped_hits": host["data_shaped_hit_count"],
        "raw_fixture_control": fixture["overall"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bitcoin CVaR RaQL prerequisite checker")
    parser.add_argument("--results-root", default=DEFAULT_RESULTS)
    parser.add_argument("--raw-root", default=DEFAULT_RAW)
    parser.add_argument("--repo-root", default=DEFAULT_REPO)
    parser.add_argument("--record-path", default=DEFAULT_RECORD)
    parser.add_argument("--board-db", default=DEFAULT_BOARD_DB)
    parser.add_argument("--evidence-path", default=DEFAULT_EVIDENCE)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--live-recheck", action="store_true")
    parser.add_argument("--measure-only", action="store_true")
    parser.add_argument("--host-scan", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--raw-fixture-control", action="store_true")
    parser.add_argument("--verify-verbatim", action="store_true")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--publish-evidence", action="store_true")
    args = parser.parse_args(argv)

    if args.publish:
        output = publish(
            args.results_root,
            args.raw_root,
            args.repo_root,
            args.record_path,
            args.board_db,
            args.evidence_path,
        )
    elif args.publish_evidence:
        output = publish_evidence(
            args.results_root,
            args.raw_root,
            args.repo_root,
            args.record_path,
            args.board_db,
            args.evidence_path,
        )
    elif args.live_recheck:
        output = live_recheck(args.raw_root)
    elif args.measure_only:
        output = measure_raw(args.raw_root)
    elif args.host_scan:
        output = host_scan()
    elif args.self_test:
        output = self_test(args.results_root, args.raw_root)
    elif args.raw_fixture_control:
        output = raw_fixture_control(args.raw_root)
    elif args.verify_verbatim:
        output = verify_verbatim(
            _round_paths(args.results_root)["spec"],
            args.repo_root,
            args.record_path,
            args.board_db,
        )
    else:
        output = run_checks(args.results_root, args.raw_root, include_host_scan=False)

    if args.json or any(
        (
            args.publish,
            args.publish_evidence,
            args.live_recheck,
            args.measure_only,
            args.host_scan,
            args.self_test,
            args.raw_fixture_control,
            args.verify_verbatim,
        )
    ):
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        for row in output["checks"]:
            print("%-4s %-4s %s" % (row["id"], row["status"], row["detail"]))
        print("overall:", output["overall"])
    if args.measure_only or args.live_recheck or args.host_scan:
        return 0
    return 0 if output.get("overall") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
