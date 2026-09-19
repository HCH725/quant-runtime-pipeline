#!/usr/bin/env python3
"""Instantiate the r2-u2 run-spec for family
`commodity-futures-network-momentum-lead-lag-graph-learning-2026-09-02` (contract 13 same-round
card-local script-bug remediation; card t_5551afc1).

r2-u1 measured all ten registered phase grids and then failed in the terminal summarize step
(`dtw_ablation_check` shadowed its own module helper name -> UnboundLocalError), so the attempt
carries no result.json and no terminal sentinel.  u2 re-runs the SAME frozen round-spec with the
fixed engine bytes.  The science must be identical: every registered domain, the split, the
costs, the gates and the reader registrations are asserted canonical-JSON-equal to u1's run-spec
BEFORE anything is written, and only the run identity + engine pins may differ.

usage: _author_strategy_j_u2_run_spec.py [--dry-run]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_ROOT = "/Volumes/ExpansionDrive/qlib-results"
FAMILY_ID = "commodity-futures-network-momentum-lead-lag-graph-learning-2026-09-02"
ROUND_ID = FAMILY_ID + "-r2"
U1 = ROUND_ID + "-u1"
U2 = ROUND_ID + "-u2"
ENGINE_REPO = os.path.join(REPO, "container", "scripts", "100_strategy_j_run.py")
ENGINE_DEPLOY = "/Users/hong/workspace/qlib-apple-container/scripts/100_strategy_j_run.py"
SELFCHECK_REPO = os.path.join(REPO, "container", "scripts", "tests",
                              "test_strategy_j_engine.py")
SELFCHECK_DEPLOY = "/Users/hong/workspace/qlib-apple-container/scripts/tests/test_strategy_j_engine.py"
# fields that describe the science: they may not change between attempts of the same round
FROZEN_FIELDS = ("family_id", "round_id", "task_id", "kanban_task_id", "kanban_board",
                 "container", "data", "parameter_domain", "dca_domain", "costs", "gates",
                 "expected", "selector_version", "disposition_version", "falsification",
                 "signal_constants")


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
    ap.add_argument("--source-run", default=ROUND_ID + "-u1",
                    help="attempt whose frozen fields are inherited")
    ap.add_argument("--target-run", default=ROUND_ID + "-u2", help="attempt to instantiate")
    ap.add_argument("--expect-source-stage", default="FAILED_SCRIPT")
    ap.add_argument("--reason", default="terminal summarize step failed (script bug)")
    args = ap.parse_args()
    U1 = args.source_run
    U2 = args.target_run
    round_path = os.path.join(RESULTS_ROOT, FAMILY_ID, "rounds", ROUND_ID, "round-spec.json")
    u1_dir = os.path.join(RESULTS_ROOT, FAMILY_ID, "rounds", ROUND_ID, "attempts", U1)
    u2_dir = os.path.join(RESULTS_ROOT, FAMILY_ID, "rounds", ROUND_ID, "attempts", U2)
    with open(round_path) as fh:
        round_spec = json.load(fh)
    with open(os.path.join(u1_dir, "run-spec.json")) as fh:
        u1 = json.load(fh)
    with open(os.path.join(u1_dir, "state.json")) as fh:
        u1_state = json.load(fh)
    problems = []
    if u1_state.get("stage") != "FAILED_SCRIPT":
        problems.append("u1 state is %r, expected FAILED_SCRIPT" % u1_state.get("stage"))
    if os.path.exists(os.path.join(u1_dir, "result.json")):
        problems.append("u1 published a result.json: it is not a failed attempt")
    for name in ("DONE", "FAILED", "INCOMPLETE"):
        if os.path.exists(os.path.join(u1_dir, name)):
            problems.append("u1 already has a terminal sentinel %s" % name)
    engine_sha = sha256_file(ENGINE_REPO)
    engine_deploy_sha = sha256_file(ENGINE_DEPLOY)
    selfcheck_sha = sha256_file(SELFCHECK_REPO)
    selfcheck_deploy_sha = sha256_file(SELFCHECK_DEPLOY)
    if engine_sha != engine_deploy_sha:
        problems.append("engine sha differs between repo and host deploy")
    if selfcheck_sha != selfcheck_deploy_sha:
        problems.append("self-check sha differs between repo and host deploy")
    if engine_sha == u1.get("script", {}).get("sha256"):
        problems.append("engine sha is unchanged from u1: this is not the fixed bytes")

    import time
    u2 = json.loads(json.dumps(u1))
    u2["run_id"] = U2
    u2["created_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    u2["script"] = dict(u1["script"], sha256=engine_sha)
    u2["engine_selfcheck"] = dict(u1["engine_selfcheck"], sha256=selfcheck_sha)
    u2["round_spec_sha256"] = sha256_file(round_path)
    u2["round_spec_sha256_expected"] = u2["round_spec_sha256"]
    u2["supersedes_attempt"] = {
        "run_id": U1,
        "status": "FAILED_SCRIPT at the terminal summarize step (dtw_ablation_check shadowed "
                  "the module helper `_winner_case`); no result.json, no sentinel",
        "run_spec_sha256": sha256_file(os.path.join(u1_dir, "run-spec.json")),
        "state_sha256": sha256_file(os.path.join(u1_dir, "state.json")),
        "measurement_neutrality": "the fix only touches the reader function; u2's ten grid CSVs "
                                  "must be byte-identical to u1's (asserted after the run)",
        "engine_sha256_at_u1": u1["script"]["sha256"],
    }
    for f in FROZEN_FIELDS:
        if canon(u2.get(f)) != canon(u1.get(f)):
            problems.append("field %r changed between u1 and u2" % f)
    if canon(round_spec["data"]) != canon(u2["data"]):
        problems.append("u2 data block differs from the frozen round-spec")
    if canon(round_spec["parameter_domain"]["grid_cases"]) != canon(
            u2["parameter_domain"]["grid_cases"]):
        problems.append("u2 grid_cases differ from the frozen round-spec")
    if canon(round_spec["expected"]) != canon(u2["expected"]):
        problems.append("u2 expected counts differ from the frozen round-spec")

    out = {"problems": problems, "engine_sha256": engine_sha, "selfcheck_sha256": selfcheck_sha,
           "u1_engine_sha256": u1["script"]["sha256"],
           "run_spec_path": None, "run_spec_sha256": None}
    if problems:
        print(json.dumps(out, indent=2, ensure_ascii=False))
        print("REFUSING TO WRITE: %d problem(s)" % len(problems))
        return 1
    if args.dry_run:
        out["dry_run"] = True
        print(json.dumps(out, indent=2, ensure_ascii=False))
        print("DRY RUN: nothing written")
        return 0
    os.makedirs(os.path.join(u2_dir, "logs"), exist_ok=True)
    os.makedirs(os.path.join(u2_dir, "artifacts"), exist_ok=True)
    path = os.path.join(u2_dir, "run-spec.json")
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps(u2, indent=2, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    out["run_spec_path"] = path
    out["run_spec_sha256"] = sha256_file(path)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
