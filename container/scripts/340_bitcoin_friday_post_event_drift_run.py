#!/usr/bin/env python3
"""Qlib entry point for the source-faithful Bitcoin Friday drift prerequisite gate.

The reviewed record requires Kraken BTC/USD spot hourly data, including its
2016-2021 source sample and strictly later out-of-sample data. This runner fails
closed when those exact inputs are absent or their coverage is unverified; it
never substitutes Binance spot/perpetual data or claims strategy performance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

FAMILY_ID = "bitcoin-friday-3pm-est-post-event-drift-2026-09-03"
RUNNER_NAME = "340_bitcoin_friday_post_event_drift_run.py"
RAW_ROOT = Path("/data/raw")
CONFIG_PATH = RAW_ROOT / "_meta" / "CONFIG.json"
SCHEMA_PATH = RAW_ROOT / "_meta" / "SCHEMA.md"
MAX_CATALOG_BYTES = 256 * 1024
OWNERSHIP_KEYS = ("task_id", "kanban_task_id", "kanban_board")
SOURCE_SAMPLE = {"start": "2016-01-01", "end": "2021-12-31"}
POST_SOURCE_START = "2022-01-01"


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def read_bounded_catalog(path):
    """Read a small regular catalog without following a symlink."""
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ValueError("catalog must be a regular non-symlink file: %s" % path)
    if info.st_size > MAX_CATALOG_BYTES:
        raise ValueError("catalog exceeds %d bytes: %s" % (MAX_CATALOG_BYTES, path))
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError("catalog changed while opening: %s" % path)
        with os.fdopen(fd, "rb") as stream:
            fd = -1
            raw = stream.read(MAX_CATALOG_BYTES + 1)
    finally:
        if fd != -1:
            os.close(fd)
    if len(raw) > MAX_CATALOG_BYTES:
        raise ValueError("catalog exceeds %d bytes: %s" % (MAX_CATALOG_BYTES, path))
    return raw


def inspect_prerequisites(config, schema_text):
    """Compare the frozen Kraken spot requirements with the bounded raw catalog.

    ponytail: inspect only CONFIG/SCHEMA, not millions of OHLCV rows. A cataloged
    Kraken series remains coverage_unverified until its exact date coverage is
    represented in a canonical catalog; Binance data is never treated as a proxy.
    """
    config = config if isinstance(config, dict) else {}
    datasets = config.get("datasets")
    datasets = datasets if isinstance(datasets, dict) else {}
    dataset_labels = {str(key): str(value) for key, value in datasets.items()}
    intervals = config.get("intervals")
    intervals = sorted(set(str(value) for value in intervals)) if isinstance(intervals, list) else []
    symbols_value = config.get("symbols")
    symbols = list(symbols_value) if isinstance(symbols_value, list) else []
    venue = config.get("venue")
    market_type = config.get("market_type")

    kraken_dataset_ids = sorted(
        key for key, label in dataset_labels.items()
        if "kraken" in (key + " " + label).lower()
    )
    exact_market_records = []
    for key in kraken_dataset_ids:
        label = key + " " + dataset_labels[key]
        if "spot" not in label.lower():
            continue
        if not re.search(r"\bBTC\s*(?:/|-)?\s*USD\b", label, re.I):
            continue
        exact_market_records.append(key)
    venue_is_kraken = str(venue or "").strip().lower() == "kraken" or bool(kraken_dataset_ids)
    spot_is_declared = str(market_type or "").strip().lower() == "spot" or any(
        "spot" in (key + " " + value).lower() for key, value in dataset_labels.items()
    )
    pair_is_declared = any(str(symbol).strip().upper().replace("/", "") == "BTCUSD" for symbol in symbols)
    hourly_is_declared = "1h" in intervals or any(
        re.search(r"\bhourly\b|\b1h\b", value, re.I) for value in dataset_labels.values()
    )
    exact_market_declared = venue_is_kraken and spot_is_declared and (pair_is_declared or bool(exact_market_records)) and hourly_is_declared

    if exact_market_declared:
        market_status = "coverage_unverified"
        market_evidence = {
            "matching_dataset_ids": exact_market_records,
            "configured_venue": venue,
            "configured_market_type": market_type,
            "configured_symbols": symbols,
            "configured_intervals": intervals,
            "reason": "CONFIG declares a Kraken BTC/USD hourly spot series but does not prove the frozen sample and OOS date coverage.",
        }
    else:
        market_status = "absent"
        market_evidence = {
            "matching_dataset_ids": exact_market_records,
            "configured_venue": venue,
            "configured_market_type": market_type,
            "configured_symbols": symbols,
            "configured_intervals": intervals,
            "reason": "CONFIG does not declare the exact Kraken BTC/USD spot hourly source market; other venues/quotes are not substitutes.",
        }

    execution_dataset_ids = sorted(
        key for key, label in dataset_labels.items()
        if "kraken" in (key + " " + label).lower()
        and re.search(r"trade|quote|book|spread|fee", key + " " + label, re.I)
    )
    execution_status = "coverage_unverified" if execution_dataset_ids else "absent"
    checks = [
        {
            "requirement": "Kraken BTC/USD spot hourly closes for exact source reproduction",
            "status": market_status,
            "required_period": SOURCE_SAMPLE,
            "evidence": market_evidence,
        },
        {
            "requirement": "Strictly post-source Kraken BTC/USD spot hourly out-of-sample data",
            "status": market_status,
            "required_start": POST_SOURCE_START,
            "evidence": market_evidence,
        },
        {
            "requirement": "Kraken executable bid/ask, fees, and missing/stale-print evidence for net-performance testing",
            "status": execution_status,
            "evidence": {
                "matching_dataset_ids": execution_dataset_ids,
                "reason": "No matching Kraken execution-cost dataset is declared in CONFIG." if not execution_dataset_ids
                          else "Kraken execution data is declared but exact coverage is not proven by CONFIG.",
            },
        },
    ]
    catalog = {
        "venue": venue,
        "market_type": market_type,
        "symbols": symbols,
        "intervals": intervals,
        "dataset_ids": sorted(dataset_labels),
        "kraken_dataset_ids": kraken_dataset_ids,
        "binance_spot_dataset_ids": sorted(
            key for key, label in dataset_labels.items()
            if "binance" in (key + " " + label).lower() and "spot" in (key + " " + label).lower()
        ),
        "schema_mentions_kraken": "kraken" in schema_text.lower(),
    }
    return {
        "catalog_summary": catalog,
        "checks": checks,
        "missing_prerequisites": [
            item["requirement"] for item in checks
            if item["status"] in ("absent", "coverage_unverified")
        ],
    }


def validate_identity(spec, round_spec, attempt_dir, runner_path):
    attempt = Path(attempt_dir).resolve(strict=True)
    if not attempt.is_dir():
        raise ValueError("attempt-dir is not a directory")
    family_id, round_id, run_id = spec.get("family_id"), spec.get("round_id"), spec.get("run_id")
    if family_id != FAMILY_ID or attempt.parents[3].name != family_id \
            or attempt.parents[1].name != round_id or attempt.name != run_id:
        raise ValueError("run-spec identity does not match the attempt path")
    if round_spec.get("family_id") != family_id or round_spec.get("round_id") != round_id:
        raise ValueError("round-spec identity mismatch")
    for document in (spec, round_spec):
        leaked = [key for key in OWNERSHIP_KEYS if key in document and document[key] not in (None, "")]
        if leaked:
            raise ValueError("direct execution spec contains ownership fields: %s" % leaked)
    if spec.get("semantic_fingerprint") != round_spec.get("semantic_fingerprint"):
        raise ValueError("semantic fingerprint differs between specs")
    expected_fingerprint = "sha256:" + hashlib.sha256(
        spec.get("fingerprint_input", "").encode("utf-8")).hexdigest()
    if expected_fingerprint != spec.get("semantic_fingerprint"):
        raise ValueError("fingerprint_input does not match semantic_fingerprint")
    script = spec.get("script") or {}
    actual_script_sha = sha256(Path(runner_path).read_bytes())
    if script.get("path") != "/scripts/" + RUNNER_NAME or script.get("sha256") != actual_script_sha:
        raise ValueError("run-spec script identity differs from the executing runner")
    round_path = attempt.parents[1] / "round-spec.json"
    if spec.get("round_spec_sha256") != sha256(round_path.read_bytes()):
        raise ValueError("run-spec round_spec_sha256 does not match the adjacent frozen round-spec")
    return attempt


def immutable_write(path, raw):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
    except FileExistsError:
        if path.is_symlink() or not path.is_file() or path.read_bytes() != raw:
            raise ValueError("immutable output differs: %s" % path)
        return
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def atomic_state_write(path, payload):
    path = Path(path)
    temp = path.with_name(path.name + ".tmp")
    raw = (json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    with open(temp, "xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def run(spec_path, attempt_dir, runner_path=__file__):
    import qlib  # type: ignore[reportMissingImports]

    attempt = Path(attempt_dir).resolve(strict=True)
    if any((attempt / terminal).exists() for terminal in ("DONE", "FAILED", "INCOMPLETE")):
        raise ValueError("terminal sentinel exists; INV-15 forbids rerun")
    spec_file = Path(spec_path).resolve(strict=True)
    if spec_file != attempt / "run-spec.json":
        raise ValueError("run-spec must be the attempt's run-spec.json")
    spec = json.loads(spec_file.read_text(encoding="utf-8"))
    round_path = attempt.parents[1] / "round-spec.json"
    round_spec = json.loads(round_path.read_text(encoding="utf-8"))
    attempt = validate_identity(spec, round_spec, attempt, runner_path)

    catalog_reads = {}
    try:
        config_raw = read_bounded_catalog(CONFIG_PATH)
        config = json.loads(config_raw.decode("utf-8"))
        catalog_reads["CONFIG.json"] = {"status": "read", "sha256": sha256(config_raw), "bytes": len(config_raw)}
    except (OSError, UnicodeError, ValueError) as exc:
        config = {}
        catalog_reads["CONFIG.json"] = {"status": "unavailable", "reason": str(exc)[:300]}
    try:
        schema_raw = read_bounded_catalog(SCHEMA_PATH)
        schema_text = schema_raw.decode("utf-8")
        catalog_reads["SCHEMA.md"] = {"status": "read", "sha256": sha256(schema_raw), "bytes": len(schema_raw)}
    except (OSError, UnicodeError, ValueError) as exc:
        schema_raw = b""
        schema_text = ""
        catalog_reads["SCHEMA.md"] = {"status": "unavailable", "reason": str(exc)[:300]}

    evidence = inspect_prerequisites(config, schema_text)
    evidence.update({
        "document_kind": "source_faithful_prerequisite_evidence",
        "schema_version": 1,
        "family_id": spec["family_id"],
        "round_id": spec["round_id"],
        "run_id": spec["run_id"],
        "measured_at_utc": utc_now(),
        "qlib_version": getattr(qlib, "__version__", "unknown"),
        "catalog_reads": catalog_reads,
        "catalog_sha256": {
            name: item.get("sha256") for name, item in catalog_reads.items() if item.get("sha256")
        },
        "schema_sha256": sha256(schema_raw) if schema_raw else None,
    })
    result = {
        "schema_version": 1,
        "family_id": spec["family_id"],
        "round_id": spec["round_id"],
        "run_id": spec["run_id"],
        "engine": "bitcoin-friday-post-event-drift-prerequisite-gate-v1",
        "status": "ARTIFACT_READY",
        "coverage_complete": False,
        "cells_computed": 0,
        "coverage": {
            "cells_computed": 0,
            "expected_cells": (spec.get("expected") or {}).get("expected_case_evaluations"),
            "reason": "exact Kraken BTC/USD spot source and out-of-sample prerequisites are absent or coverage-unverified",
        },
        "disposition": "TECHNICAL_INCOMPLETE",
        "verdict_recommendation": "TECHNICAL_INCOMPLETE",
        "performance_claimable_recommendation": False,
        "failure": {"layer": "card-local", "class": "required_source_prerequisite_unavailable"},
        "missing_prerequisites": evidence["missing_prerequisites"],
        "evidence_artifact": "artifacts/prerequisite_evidence.json",
        "final_verdict_written": False,
        "qlib_version": evidence["qlib_version"],
        "script_sha256": sha256(Path(runner_path).read_bytes()),
        "generated_at_utc": utc_now(),
    }
    immutable_write(
        attempt / "artifacts" / "prerequisite_evidence.json",
        (json.dumps(evidence, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8"),
    )
    immutable_write(
        attempt / "result.json",
        (json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8"),
    )
    atomic_state_write(attempt / "state.json", {
        "schema_version": 1,
        "family_id": spec["family_id"],
        "round_id": spec["round_id"],
        "run_id": spec["run_id"],
        "stage": "ARTIFACT_READY",
        "updated_at_utc": utc_now(),
        "note": "bounded source-market prerequisite evidence only; host disposition owns terminal/verdict writing",
    })
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Bitcoin Friday post-event drift Qlib prerequisite runner")
    parser.add_argument("--run-spec", required=True)
    parser.add_argument("--attempt-dir", required=True)
    args = parser.parse_args(argv)
    result = run(args.run_spec, args.attempt_dir)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # bounded attempt evidence; no final terminal or verdict is written here
        sys.stderr.write("bitcoin Friday runner failed: %s: %s\n" % (type(exc).__name__, exc))
        sys.exit(1)
