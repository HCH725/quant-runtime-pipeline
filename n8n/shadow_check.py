#!/usr/bin/env python3
"""Read-only verification of the n8n shadow control-plane snapshot.

Usage:
    python3 n8n/shadow_check.py [snapshot.json] [--max-age-seconds N] [--require-fresh]

Checks (never writes anything, never talks to n8n):
  * schema / mode identity and the full 11-stage topology, in order
  * required count keys present; null values are reported, never invented
  * reconciliation invariants that are expected to hold are actually true
  * snapshot age against --max-age-seconds (warn by default, fail with --require-fresh)

Exit code 0 = sound, 1 = failed. See N8N_CONTROL_PLANE.md.
"""

import json
import os
import sys
import time

DEFAULT_SNAPSHOT = "/Users/hong/workspace/n8n/files/quant-control-plane-shadow.json"
STAGES = [
    "strategy_research", "github_strategy_pool", "intake_review", "wiki_brain",
    "candidate_queue", "data_preflight_gate", "qlib_full_backtest", "result_verdict",
    "survivor", "leaderboard", "private_repo_parking",
]
COUNT_KEYS = [
    "pool_records_total", "intake_pass", "intake_pass_with_caveat", "intake_remediate",
    "intake_reject", "intake_pending_ingestion", "intake_ingested_wiki_records",
    "wiki_reviewed", "wiki_ingested", "families_registered", "families_backtested",
    "leaderboard_count", "current_stage", "parking_survivor_dirs",
]
MUST_HOLD = {
    "intake buckets total vs wiki reviewed",
    "ingested_wiki_records vs dashboard wiki ingested",
    "parking survivor dirs vs parking survivor_count",
}
VOCAB = [
    "WAITING_DATA", "READY_TO_RESUME", "BLOCKED", "TECHNICAL_INCOMPLETE", "REJECT",
    "PASS", "RUNNING_QLIB", "ARTIFACT_READY", "FAILED_SCRIPT",
]


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    path = args[0] if args else DEFAULT_SNAPSHOT
    max_age = 2400
    if "--max-age-seconds" in argv:
        max_age = int(argv[argv.index("--max-age-seconds") + 1])
    require_fresh = "--require-fresh" in argv

    failures, warnings = [], []
    if not os.path.isfile(path):
        print(f"FAIL snapshot not found: {path}")
        return 1
    with open(path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)

    def ok(label, condition, detail=""):
        print(f"{'PASS' if condition else 'FAIL'} {label}{(' — ' + detail) if detail else ''}")
        if not condition:
            failures.append(label)
        return condition

    ok("schema identity", doc.get("schema") == "quant-control-plane-shadow/v1", str(doc.get("schema")))
    ok("read-only mode", doc.get("mode") == "SHADOW_READ_ONLY", str(doc.get("mode")))
    cp = doc.get("control_plane") or {}
    ok("control plane disabled", cp.get("mutations_enabled") is False and not cp.get("mutating_nodes"))

    topology = doc.get("topology") or []
    ok("topology is the full 11-stage pipeline",
       [s.get("stage") for s in topology] == STAGES,
       f"{len(topology)} stages")

    states = {s.get("stage"): s.get("shadow_state") for s in topology}
    bad_token = {k: v for k, v in states.items() if v is not None and v not in VOCAB}
    ok("shadow states use only the documented vocabulary", not bad_token, str(bad_token))
    mapped = {k: v for k, v in states.items() if v is not None}
    print(f"INFO shadow states present: {json.dumps(mapped, ensure_ascii=False)}")

    counts = doc.get("counts") or {}
    missing = [k for k in COUNT_KEYS if k not in counts]
    ok("required count keys present", not missing, str(missing))
    nulls = sorted(k for k in COUNT_KEYS if counts.get(k) is None)
    if nulls:
        warnings.append(f"null counts (reported as unavailable, not guessed): {nulls}")
        print(f"WARN null counts -> {nulls}")

    recon = {r.get("check"): r for r in (doc.get("reconciliation") or [])}
    for name in sorted(MUST_HOLD):
        entry = recon.get(name)
        ok(f"reconciliation holds: {name}", bool(entry) and entry.get("ok") is True,
           "" if entry else "missing")
    for name, entry in recon.items():
        if name not in MUST_HOLD and entry.get("ok") is False:
            print(f"INFO outstanding gap reported by reconciliation: {name} "
                  f"(observed={entry.get('observed')} expected={entry.get('expected')})")

    policy = doc.get("resume_policy") or {}
    ok("future resume policy documented and not enabled",
       policy.get("documented") is True and policy.get("enabled") is False)

    age = time.time() - os.path.getmtime(path)
    fresh = age <= max_age
    print(f"{'PASS' if fresh else ('FAIL' if require_fresh else 'WARN')} snapshot freshness "
          f"({age:.0f}s old, threshold {max_age}s, generated_at_utc={doc.get('generated_at_utc')})")
    if not fresh:
        (failures if require_fresh else warnings).append("snapshot age")

    print(f"\nresult: {'FAIL' if failures else 'PASS'} — {path}")
    if failures:
        print("failures: " + "; ".join(failures))
    if warnings:
        print("warnings: " + "; ".join(warnings))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
