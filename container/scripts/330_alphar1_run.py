#!/usr/bin/env python3
"""Qlib entry point for the source-faithful Alpha-R1 prerequisite gate.

The reviewed Alpha-R1 release has no public GRPO inference code or weights. This
runner therefore records bounded technical-incomplete evidence when frozen
prerequisites are absent; it never substitutes another model or claims returns.
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

FAMILY_ID = "alphar1-context-aware-alpha-screening-llm-reasoning-grpo-2026-09-03"
RAW_ROOT = Path("/data/raw")
CONFIG_PATH = RAW_ROOT / "_meta" / "CONFIG.json"
SCHEMA_PATH = RAW_ROOT / "_meta" / "SCHEMA.md"
MAX_CATALOG_BYTES = 256 * 1024
MODEL_RELEASE_STATUS = "roadmap_only_no_public_inference_code_or_weights"
SOURCE_REPO_COMMIT = "61feaa359bd57761f5ac58f75af46ddfed2d2d7b"
OWNERSHIP_KEYS = ("task_id", "kanban_task_id", "kanban_board")


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def read_bounded_catalog(path):
    """Read one regular catalog file without following a symlink or scanning raw data."""
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


def inspect_prerequisites(config, schema_text, source_release):
    """Compare frozen Alpha-R1 requirements with the bounded canonical catalogs."""
    datasets = config.get("datasets") if isinstance(config, dict) else None
    datasets = datasets if isinstance(datasets, dict) else {}
    venue = config.get("venue") if isinstance(config, dict) else None
    market_type = config.get("market_type") if isinstance(config, dict) else None
    symbols = config.get("symbols") if isinstance(config, dict) else None
    intervals = config.get("intervals") if isinstance(config, dict) else None
    intervals = intervals if isinstance(intervals, list) else []
    dataset_labels = {str(k): str(v) for k, v in datasets.items()}
    news_labels = sorted(k for k, v in dataset_labels.items()
                         if re.search(r"news|announcement|corporate", k + " " + v, re.I))
    schema_lower = schema_text.lower()
    constraint_markers = ("limit-up", "limit up", "limit-down", "limit down",
                          "ipo listing date", "initial listing date")
    constraint_fields = [marker for marker in constraint_markers if marker in schema_lower]
    source_assets_available = (
        isinstance(source_release, dict)
        and source_release.get("release_status") != MODEL_RELEASE_STATUS
        and bool(source_release.get("inference_code_available"))
        and bool(source_release.get("model_weights_available"))
    )
    equity_market = (venue in ("SSE", "SZSE", "CFFEX")
                     or market_type in ("china_a_shares", "equity")
                     or any("a-share" in (k + " " + v).lower()
                            for k, v in dataset_labels.items()))
    catalog = {
        "venue": venue,
        "market_type": market_type,
        "symbols": symbols if isinstance(symbols, list) else [],
        "intervals": sorted(set(str(x) for x in intervals)),
        "dataset_ids": sorted(dataset_labels),
        "news_or_announcement_dataset_ids": news_labels,
        "trading_constraint_schema_markers": constraint_fields,
    }
    checks = [
        {"requirement": "CSI 300 / CSI 1000 point-in-time Chinese A-share universe",
         "status": "present" if equity_market else "absent",
         "evidence": {"venue": venue, "market_type": market_type,
                      "configured_symbols": catalog["symbols"]}},
        {"requirement": "A-share daily OHLCV plus 1-minute 09:31-10:00 VWAP inputs",
         "status": "present" if equity_market and "1m" in catalog["intervals"] else "absent",
         "evidence": {"configured_intervals": catalog["intervals"],
                      "equity_market_present": equity_market}},
        {"requirement": "82 Alpha101 factors on point-in-time A-share data",
         "status": "present" if equity_market else "absent",
         "evidence": "No A-share source series are declared in the canonical CONFIG catalog; "
                     "the source's selected 82-factor implementation is not publicly released."},
        {"requirement": "Decision-time financial news, corporate announcements, and Chinese macro context",
         "status": "present" if news_labels else "absent",
         "evidence": {"matching_catalog_dataset_ids": news_labels,
                      "configured_macro_dataset_ids": [k for k in catalog["dataset_ids"]
                                                        if k in ("fred", "cftc", "cboe_family")] }},
        {"requirement": "Daily limit-up / limit-down and initial IPO listing-date constraints",
         "status": "present" if len(constraint_fields) == len(constraint_markers) else "absent",
         "evidence": {"declared_schema_markers": constraint_fields}},
        {"requirement": "Pinned Alpha-R1 Qwen3-8B GRPO inference code and aligned model weights",
         "status": "present" if source_assets_available else "absent_in_reviewed_source",
         "evidence": {"public_repo_commit": SOURCE_REPO_COMMIT,
                      "reviewed_release_status": (source_release or {}).get("release_status"),
                      "inference_code_available": (source_release or {}).get("inference_code_available"),
                      "model_weights_available": (source_release or {}).get("model_weights_available")}},
        {"requirement": "Exact Alpha-R1 preprocessing, news APIs, and prompt templates",
         "status": "underspecified_in_reviewed_source",
         "evidence": "The reviewed record identifies these items as not fully specified; no substitute is permitted."},
    ]
    return {"catalog_summary": catalog,
            "checks": checks,
            "missing_prerequisites": [item["requirement"] for item in checks
                                      if item["status"] in ("absent", "absent_in_reviewed_source",
                                                            "underspecified_in_reviewed_source")],
            "catalog_sha256": None}


def validate_identity(spec, round_spec, attempt_dir, runner_path):
    attempt = Path(attempt_dir).resolve(strict=True)
    if not attempt.is_dir():
        raise ValueError("attempt-dir is not a directory")
    family_id, round_id, run_id = (spec.get("family_id"), spec.get("round_id"), spec.get("run_id"))
    if family_id != FAMILY_ID or attempt.parents[3].name != family_id \
            or attempt.parents[1].name != round_id or attempt.name != run_id:
        raise ValueError("run-spec identity does not match the attempt path")
    if round_spec.get("family_id") != family_id or round_spec.get("round_id") != round_id:
        raise ValueError("round-spec identity mismatch")
    for document in (spec, round_spec):
        leaked = [key for key in OWNERSHIP_KEYS
                  if key in document and document[key] not in (None, "")]
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
    if script.get("path") != "/scripts/330_alphar1_run.py" \
            or script.get("sha256") != actual_script_sha:
        raise ValueError("run-spec script identity differs from the executing runner")
    if spec.get("source_release", {}).get("release_status") != MODEL_RELEASE_STATUS:
        raise ValueError("run-spec does not freeze the reviewed Alpha-R1 release status")
    if spec.get("source_release", {}).get("public_repo_commit") != SOURCE_REPO_COMMIT:
        raise ValueError("run-spec public repository commit differs from reviewed provenance")
    round_path = attempt.parents[1] / "round-spec.json"
    expected_round_sha = spec.get("round_spec_sha256")
    if expected_round_sha != sha256(round_path.read_bytes()):
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
    import qlib

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

    config_raw, schema_raw = None, None
    catalog_read = {}
    try:
        config_raw = read_bounded_catalog(CONFIG_PATH)
        config = json.loads(config_raw.decode("utf-8"))
        catalog_read["CONFIG.json"] = {"status": "read", "sha256": sha256(config_raw),
                                        "bytes": len(config_raw)}
    except (OSError, UnicodeError, ValueError) as exc:
        config = {}
        catalog_read["CONFIG.json"] = {"status": "unavailable", "reason": str(exc)[:300]}
    try:
        schema_raw = read_bounded_catalog(SCHEMA_PATH)
        schema_text = schema_raw.decode("utf-8")
        catalog_read["SCHEMA.md"] = {"status": "read", "sha256": sha256(schema_raw),
                                      "bytes": len(schema_raw)}
    except (OSError, UnicodeError, ValueError) as exc:
        schema_text = ""
        catalog_read["SCHEMA.md"] = {"status": "unavailable", "reason": str(exc)[:300]}

    evidence = inspect_prerequisites(config, schema_text, spec.get("source_release"))
    evidence["document_kind"] = "source_faithful_prerequisite_evidence"
    evidence["schema_version"] = 1
    evidence["family_id"] = spec["family_id"]
    evidence["round_id"] = spec["round_id"]
    evidence["run_id"] = spec["run_id"]
    evidence["measured_at_utc"] = utc_now()
    evidence["qlib_version"] = getattr(qlib, "__version__", "unknown")
    evidence["catalog_reads"] = catalog_read
    evidence["catalog_sha256"] = {
        name: rec.get("sha256") for name, rec in catalog_read.items() if rec.get("sha256")
    }
    # ponytail: do not instantiate a substitute model, data pipeline, or inference service;
    # the pinned source has no released aligned assets, so the faithful result is zero cells.
    result = {
        "schema_version": 1,
        "family_id": spec["family_id"],
        "round_id": spec["round_id"],
        "run_id": spec["run_id"],
        "engine": "alphar1-prerequisite-gate-v1",
        "status": "ARTIFACT_READY",
        "coverage_complete": False,
        "cells_computed": 0,
        "coverage": {"cells_computed": 0, "expected_cells": None,
                     "reason": "prerequisites unavailable before strategy computation"},
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
    immutable_write(attempt / "artifacts" / "prerequisite_evidence.json",
                    (json.dumps(evidence, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8"))
    immutable_write(attempt / "result.json",
                    (json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8"))
    atomic_state_write(attempt / "state.json", {
        "schema_version": 1,
        "family_id": spec["family_id"],
        "round_id": spec["round_id"],
        "run_id": spec["run_id"],
        "stage": "ARTIFACT_READY",
        "updated_at_utc": utc_now(),
        "note": "bounded prerequisite evidence only; host disposition owns terminal/verdict writing",
    })
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Alpha-R1 source-faithful Qlib prerequisite runner")
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
        sys.stderr.write("alphar1 runner failed: %s: %s\n" % (type(exc).__name__, exc))
        sys.exit(1)
