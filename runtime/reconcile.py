#!/usr/bin/env python3
"""Completion bridge (no-agent reconciler) for the quant runtime pipeline.

Contract: .../QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md sections 9.4, 12.2, 12.3, 12.6, 11.
Only legal action: `scheduled -> ready` (kernel `unblock`), taken **only** after the contract
verification list passes for an *unconsumed* terminal sentinel. Anything conflicting is fail-closed:
no unblock, no block, an append-only incident line, and a card comment.

This is not a daemon: run it manually or from a no_agent cron / one-shot invocation.
Exit codes: 0 = no incident, 3 = incidents recorded (human needed), 2 = usage error.

Examples:
    python3 runtime/reconcile.py --dry-run --json          # read-only report over /results
    python3 runtime/reconcile.py                           # apply (unblock what verifies)
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from terminal_evidence import TERMINALS, host_boot_id, now_utc, sha256_file  # noqa: E402

DEFAULT_RESULTS = "/Volumes/ExpansionDrive/qlib-results"
INCIDENT_DIRNAME = "_incidents"
INCIDENT_FILE = "reconciliation_incident.jsonl"


def sh(cmd, timeout=120):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or ""), (p.stderr or "")
    except FileNotFoundError:
        return 127, "", "not found: %s" % cmd[0]
    except subprocess.TimeoutExpired:
        return 124, "", "timeout: %s" % " ".join(cmd)


def card_status(board, task_id):
    """DB read-back (truth = kernel DB, never the task's own narrative)."""
    rc, out, err = sh(["hermes", "kanban", "--board", board, "show", task_id, "--json"])
    if rc != 0:
        return None, "kanban show rc=%d: %s" % (rc, (err or out).strip()[:200])
    try:
        payload = json.loads(out[out.index("{"):])
    except (ValueError, IndexError) as exc:
        return None, "unparsable kanban show json: %s" % exc
    task = payload.get("task") or {}
    return task.get("status"), "kanban show ok (status=%s)" % task.get("status")


def append_incident(results_root, record, dry_run):
    if dry_run:
        return None
    path = os.path.join(results_root, INCIDENT_DIRNAME, INCIDENT_FILE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as fh:  # append-only, never rewrite existing lines
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return path


def incident_id(attempt_dir, kind):
    import hashlib
    h = hashlib.sha256(("%s|%s|%s" % (attempt_dir, kind, now_utc())).encode()).hexdigest()[:8]
    return "inc-%s-%s" % (time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()), h)


def discover_attempts(results_root):
    """<root>/<family_id>/rounds/<round_id>/attempts/<run_id>/"""
    root = Path(results_root)
    if not root.is_dir():
        return []
    out = []
    for family in sorted(root.iterdir()):
        if not family.is_dir() or family.name.startswith("_"):
            continue
        rounds = family / "rounds"
        if not rounds.is_dir():
            continue
        for rnd in sorted(rounds.iterdir()):
            attempts = rnd / "attempts"
            if not (rnd.is_dir() and attempts.is_dir()):
                continue
            for attempt in sorted(attempts.iterdir()):
                if attempt.is_dir():
                    out.append(attempt)
    return out


class Result(object):
    def __init__(self, attempt_dir, family_id, round_id, run_id):
        self.attempt_dir = str(attempt_dir)
        self.family_id = family_id
        self.round_id = round_id
        self.run_id = run_id
        self.action = None
        self.reason = None
        self.task_id = None
        self.status_before = None
        self.status_after = None
        self.incident = None
        self.detail = {}

    def as_dict(self):
        d = {"attempt_dir": self.attempt_dir, "family_id": self.family_id, "round_id": self.round_id,
             "run_id": self.run_id, "action": self.action, "reason": self.reason, "task_id": self.task_id,
             "status_before": self.status_before, "status_after": self.status_after}
        if self.incident:
            d["incident"] = self.incident
        if self.detail:
            d["detail"] = self.detail
        return d


def fail(res, results_root, kind, detector, dry_run, evidence, board):
    res.action = "incident"
    res.reason = kind
    res.incident = append_incident(results_root, {
        "schema_version": 1,
        "incident_id": incident_id(res.attempt_dir, kind),
        "detected_at_utc": now_utc(),
        "detector": detector,
        "kanban_task_id": res.task_id,
        "family_id": res.family_id,
        "round_id": res.round_id,
        "run_id": res.run_id,
        "observed_status": res.status_before,
        "kind": kind,
        "evidence_paths": evidence,
        "host_boot_id": host_boot_id(),
    }, dry_run)
    if res.task_id and not dry_run:
        sh(["hermes", "kanban", "--board", board, "comment", res.task_id,
            "incident: %s in %s (contract 12.6) - card left untouched, human needed; evidence=%s"
            % (kind, res.attempt_dir, res.incident or "(dry-run)")])
    return res


def validate(res, results_root, board, dry_run, detector):
    """Contract 9.4 verification list. Returns sentinel dict or None (incident already recorded)."""
    attempt = Path(res.attempt_dir)
    terminals = [t for t in TERMINALS if (attempt / t).exists()]

    if len(terminals) > 1:
        fail(res, results_root, "multiple_terminal", detector, dry_run,
             [str(attempt / t) for t in terminals], board)
        return None
    if not terminals:
        return None
    name = terminals[0]

    try:
        sentinel = json.loads((attempt / name).read_text())
    except ValueError as exc:
        res.detail["json_error"] = str(exc)
        fail(res, results_root, "sentinel_ambiguous", detector, dry_run, [str(attempt / name)], board)
        return None
    res.task_id = sentinel.get("task_id")
    # the sentinel carries the board it belongs to (contract 9.4 entry point)
    board = sentinel.get("kanban_board") or board
    if sentinel.get("status") != name:
        res.detail["sentinel_status"] = sentinel.get("status")
        fail(res, results_root, "sentinel_ambiguous", detector, dry_run, [str(attempt / name)], board)
        return None

    # mapping: sentinel <-> path <-> family.json <-> round-spec.json
    problems = []
    if sentinel.get("family_id") != res.family_id:
        problems.append("family_id sentinel=%r path=%r" % (sentinel.get("family_id"), res.family_id))
    if sentinel.get("round_id") != res.round_id:
        problems.append("round_id sentinel=%r path=%r" % (sentinel.get("round_id"), res.round_id))
    if sentinel.get("run_id") != res.run_id:
        problems.append("run_id sentinel=%r path=%r" % (sentinel.get("run_id"), res.run_id))
    family_json = Path(results_root) / res.family_id / "family.json"
    round_spec = Path(results_root) / res.family_id / "rounds" / res.round_id / "round-spec.json"
    for label, path in (("family.json", family_json), ("round-spec.json", round_spec)):
        if not path.is_file():
            problems.append("missing %s" % path)
            continue
        try:
            doc = json.loads(path.read_text())
        except ValueError as exc:
            problems.append("%s not valid JSON: %s" % (label, exc))
            continue
        if doc.get("kanban_task_id") != sentinel.get("task_id"):
            problems.append("%s kanban_task_id=%r != sentinel task_id=%r"
                            % (label, doc.get("kanban_task_id"), sentinel.get("task_id")))
    if problems:
        res.detail["mapping_problems"] = problems
        fail(res, results_root, "mapping_mismatch", detector, dry_run,
             [str(attempt / name), str(family_json), str(round_spec)], board)
        return None

    # checksums of required artifacts
    manifest = sentinel.get("artifact_manifest") or []
    checksums = sentinel.get("artifact_checksums") or {}
    bad = []
    for rel in manifest:
        full = attempt / rel
        if not full.is_file():
            bad.append("missing:%s" % rel)
            continue
        want = checksums.get(rel)
        if not want:
            bad.append("no_checksum:%s" % rel)
            continue
        got = sha256_file(str(full))
        if got != want:
            bad.append("mismatch:%s sentinel=%s recomputed=%s" % (rel, want, got))
    if bad:
        res.detail["bad_artifacts"] = bad
        fail(res, results_root, "checksum_mismatch", detector, dry_run, [str(attempt / name)], board)
        return None

    # stale sentinel: produced under a different boot
    if sentinel.get("host_boot_id") != host_boot_id():
        res.detail["sentinel_boot_id"] = sentinel.get("host_boot_id")
        res.detail["current_boot_id"] = host_boot_id()
        fail(res, results_root, "stale_sentinel", detector, dry_run, [str(attempt / name)], board)
        return None

    return sentinel


def handle(res, results_root, board, dry_run, detector):
    attempt = Path(res.attempt_dir)
    terminals = [t for t in TERMINALS if (attempt / t).exists()]

    if not terminals:
        # Orphan candidate (contract 12.2 item 2/4): report only, default decides.
        state = attempt / "state.json"
        stage = None
        if state.is_file():
            try:
                stage = json.loads(state.read_text()).get("stage")
            except ValueError:
                stage = "unparsable"
        spec = attempt / "run-spec.json"
        if spec.is_file():
            try:
                res.task_id = json.loads(spec.read_text()).get("task_id")
            except ValueError:
                pass
        res.detail["stage"] = stage
        res.detail["has_run_spec"] = spec.is_file()
        res.action = "orphan_candidate"
        res.reason = ("no terminal sentinel; stage=%s -> contract 12.2: host/default publishes "
                      "INCOMPLETE via runtime/terminal_evidence.py, then reconcile again" % stage)
        return res

    sentinel = validate(res, results_root, board, dry_run, detector)
    if sentinel is None:
        return res
    if not res.task_id:
        fail(res, results_root, "sentinel_ambiguous", detector, dry_run, [str(attempt)], board)
        return res

    board = sentinel.get("kanban_board") or board
    status, why = card_status(board, res.task_id)
    res.status_before = status
    if status is None:
        res.detail["kanban_readback"] = why
        fail(res, results_root, "invariant_break", detector, dry_run, [str(attempt)], board)
        return res
    if status != "scheduled":
        res.action = "consumed"
        res.reason = "sentinel already consumed (card status=%s); no action, idempotent" % status
        return res

    if dry_run:
        res.action = "would_unblock"
        res.reason = "all contract 9.4 checks pass; card is scheduled"
        return res

    rc, out, err = sh(["hermes", "kanban", "--board", board, "unblock", res.task_id,
                       "--reason", "reconciler: terminal %s verified for %s (contract 9.4)"
                       % (sorted(terminals)[0], res.run_id)])
    if rc != 0:
        res.detail["unblock_stderr"] = (err or out).strip()[:200]
        fail(res, results_root, "invariant_break", detector, dry_run, [str(attempt)], board)
        return res
    status_after, why2 = card_status(board, res.task_id)
    res.status_after = status_after
    if status_after not in ("ready", "todo"):
        res.detail["kanban_readback_after"] = why2
        fail(res, results_root, "invariant_break", detector, dry_run, [str(attempt)], board)
        return res
    res.action = "unblocked"
    res.reason = "terminal %s verified; card %s -> %s (DB read-back)" % (sorted(terminals)[0], status, status_after)
    return res


def main():
    ap = argparse.ArgumentParser(description="contract 9.4 no-agent completion bridge")
    ap.add_argument("--results-root", default=os.environ.get("QLIB_RESULTS_ROOT", DEFAULT_RESULTS))
    ap.add_argument("--board", default=os.environ.get("HERMES_KANBAN_BOARD", "quant-strategy-research"))
    ap.add_argument("--detector", default="reconciler", choices=["reconciler", "default", "operator"])
    ap.add_argument("--dry-run", action="store_true", help="read-only: no unblock, no incident, no comment")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not os.path.isdir(args.results_root):
        sys.stderr.write("usage error: results root not found: %s\n" % args.results_root)
        return 2

    results = []
    for attempt in discover_attempts(args.results_root):
        rel = attempt.relative_to(Path(args.results_root)).parts
        family_id, round_id, run_id = rel[0], rel[2], rel[4]
        res = Result(attempt, family_id, round_id, run_id)
        res.detail["terminal_files"] = [t for t in TERMINALS if (attempt / t).exists()]
        results.append(handle(res, args.results_root, args.board, args.dry_run, args.detector))

    incidents = [r for r in results if r.action == "incident"]
    report = {
        "schema_version": 1,
        "kind": "reconcile",
        "contract_section": "9.4",
        "ran_at_utc": now_utc(),
        "host_boot_id": host_boot_id(),
        "results_root": args.results_root,
        "board": args.board,
        "dry_run": args.dry_run,
        "attempts_scanned": len(results),
        "unblocked": [r.task_id for r in results if r.action == "unblocked"],
        "would_unblock": [r.task_id for r in results if r.action == "would_unblock"],
        "incidents": len(incidents),
        "results": [r.as_dict() for r in results],
    }
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print("scanned=%d unblocked=%s incidents=%d dry_run=%s"
              % (len(results), report["unblocked"], len(incidents), args.dry_run))
        for r in results:
            print("  %-16s %-14s %s" % (r.action, r.task_id or "-", r.reason))
    return 3 if incidents else 0


if __name__ == "__main__":
    sys.exit(main())
