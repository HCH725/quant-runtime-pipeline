#!/usr/bin/env python3
"""Instantiate the r1-u2 run-spec for family
`compact-rienet-volatility-drag-mitigation-leveraged-gmv-2026-09-02` (contract 13 same-round
card-local script-bug remediation; card t_54d4eaf1).

r1-u1 launched with engine bytes sha256:92e98f01... and died immediately in `main()` with
`NameError: name 'log' is not defined` (the Strategy J engine fork had dropped the
log/sha256_file/_sanitize/atomic_write_json helper block); the exception handler failed on the
same missing helper, so the attempt carries NO state.json, NO result.json, NO terminal sentinel
and an empty artifacts/ tree - zero measurement.  u2 re-runs the SAME frozen round-spec with the
repaired engine bytes.  The science must be identical: every frozen field is asserted
canonical-JSON-equal to u1's run-spec BEFORE anything is written, and only the run identity and
the engine/self-check pins may differ.

usage: runtime/_author_strategy_k_u2_run_spec.py [--dry-run]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_ROOT = "/Volumes/ExpansionDrive/qlib-results"
FAMILY_ID = "compact-rienet-volatility-drag-mitigation-leveraged-gmv-2026-09-02"
ROUND_ID = FAMILY_ID + "-r1"
ENGINE_REPO = os.path.join(REPO, "container", "scripts", "110_strategy_k_run.py")
ENGINE_DEPLOY = "/Users/hong/workspace/qlib-apple-container/scripts/110_strategy_k_run.py"
SELFCHECK_REPO = os.path.join(REPO, "container", "scripts", "tests", "test_strategy_k_engine.py")
SELFCHECK_DEPLOY = ("/Users/hong/workspace/qlib-apple-container/scripts/tests/"
                    "test_strategy_k_engine.py")
FROZEN_FIELDS = ("family_id", "round_id", "task_id", "kanban_task_id", "kanban_board",
                 "container", "data", "parameter_domain", "dca_domain", "costs", "gates",
                 "expected", "selector_version", "disposition_version", "falsification",
                 "signal_constants")
U1_ENGINE_SHA = "sha256:92e98f01b019ac7637ceca988137dfbb7a0be7cc45352e41c116353de25df607"


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--source-run", default=ROUND_ID + "-u1")
    ap.add_argument("--target-run", default=ROUND_ID + "-u2")
    ap.add_argument("--expect-engine-sha", default=U1_ENGINE_SHA,
                    help="engine sha the source attempt was pinned to")
    ap.add_argument("--superseded-reason", default=None)
    args = ap.parse_args()
    u1_dir = os.path.join(RESULTS_ROOT, FAMILY_ID, "rounds", ROUND_ID, "attempts", args.source_run)
    u2_dir = os.path.join(RESULTS_ROOT, FAMILY_ID, "rounds", ROUND_ID, "attempts", args.target_run)
    with open(os.path.join(u1_dir, "run-spec.json")) as fh:
        u1 = json.load(fh)
    problems = []
    if u1["script"]["sha256"] != args.expect_engine_sha:
        problems.append("source run-spec engine pin is %r, expected %r"
                        % (u1["script"]["sha256"], args.expect_engine_sha))
    for name in ("state.json", "result.json", "DONE", "FAILED", "INCOMPLETE"):
        if os.path.exists(os.path.join(u1_dir, name)):
            problems.append("u1 carries %s: this instantiator expects the zero-write failure shape"
                            % name)
    log_path = os.path.join(u1_dir, "logs", "run.log")
    if os.path.exists(log_path):
        with open(log_path) as fh:
            log_text = fh.read()
        if "NameError: name 'log' is not defined" not in log_text:
            problems.append("source run.log does not carry the expected NameError")
        if "strategy_k_run.py" not in log_text:
            problems.append("source run.log does not name the engine")
        if engine_sha == args.expect_engine_sha:
            problems.append("the engine bytes are unchanged: this retry would reproduce the "
                            "source attempt's failure")
    else:
        # a zero-write instantiation that was never launched is a legitimate superseded source
        if not args.superseded_reason:
            problems.append("source attempt has no run.log and no --superseded-reason was given")
    art = os.path.join(u1_dir, "artifacts")
    if os.path.isdir(art) and os.listdir(art):
        problems.append("u1 artifacts/ is not empty: it produced measurements")
    engine_sha = sha256_file(ENGINE_REPO)
    engine_deploy_sha = sha256_file(ENGINE_DEPLOY)
    selfcheck_sha = sha256_file(SELFCHECK_REPO)
    selfcheck_deploy_sha = sha256_file(SELFCHECK_DEPLOY)
    if engine_sha != engine_deploy_sha:
        problems.append("engine sha differs between repo and host deploy")
    if selfcheck_sha != selfcheck_deploy_sha:
        problems.append("self-check sha differs between repo and host deploy")

    out = json.loads(json.dumps(u1))            # deep copy of the frozen attempt document
    out["run_id"] = args.target_run
    out["created_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    out["script"]["sha256"] = engine_sha
    out["engine_selfcheck"]["sha256"] = selfcheck_sha
    out["supersedes_attempt"] = {
        "run_id": args.source_run,
        "engine_sha256": U1_ENGINE_SHA,
        "failure": args.superseded_reason or (
            "NameError: name 'log' is not defined (module-level helper block dropped by the "
            "engine assembly); the same missing helper also killed the exception handler, so no "
            "state.json/result.json/sentinel was written and artifacts/ is empty - zero "
            "measurement"),
        "status": "superseded; same frozen round-spec and identical registered domains",
    }
    for f in FROZEN_FIELDS:
        if canon(out.get(f)) != canon(u1.get(f)):
            problems.append("frozen field %s changed between u1 and u2" % f)
    changed = sorted(k for k in set(out) | set(u1) if canon(out.get(k)) != canon(u1.get(k)))
    allowed = {"run_id", "created_at_utc", "script", "engine_selfcheck", "supersedes_attempt"}
    extra = [k for k in changed if k not in allowed]
    if extra:
        problems.append("unregistered field differences: %r" % extra)

    if problems:
        print(json.dumps({"problems": problems}, indent=2))
        print("REFUSING TO WRITE: %d problem(s)" % len(problems))
        return 1
    if args.dry_run:
        print(json.dumps({"problems": [], "dry_run": True, "run_id": out["run_id"],
                          "engine_sha256": engine_sha, "selfcheck_sha256": selfcheck_sha,
                          "changed_fields": changed}, indent=2))
        return 0
    os.makedirs(os.path.join(u2_dir, "logs"), exist_ok=True)
    os.makedirs(os.path.join(u2_dir, "artifacts"), exist_ok=True)
    path = os.path.join(u2_dir, "run-spec.json")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(path, flags, 0o644)
    except FileExistsError:
        raise SystemExit("refusing to overwrite %s (exact-once publication)" % path)
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"problems": [], "run_spec_path": path,
                      "run_spec_sha256": sha256_file(path), "engine_sha256": engine_sha,
                      "selfcheck_sha256": selfcheck_sha}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
