#!/usr/bin/env python3
"""Terminal evidence writer / checker for the quant runtime pipeline.

Contract: .../QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md sections 10.3, 10.4, 12.2.
- `publish` writes the terminal sentinel (DONE / FAILED / INCOMPLETE) for one attempt directory:
  manifest checksums are computed here, and the sentinel is published **last** and atomically
  (tmp -> fsync -> rename, same filesystem).
- `check` re-reads a published sentinel and recomputes every manifest checksum (read-only).
- Host side may use `publish --status INCOMPLETE` to record an orphaned run (contract 12.2 item 3).

Never rewrites an existing terminal sentinel (INV-15): refuse and exit 1.
Exit codes: 0 = ok/consistent, 1 = refused/inconsistent, 2 = usage error.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time

TERMINALS = ("DONE", "FAILED", "INCOMPLETE")
STATUSES = TERMINALS
HINTS = ("NONE", "CANDIDATE_PASS", "CANDIDATE_REJECT", "INCOMPLETE")
LAYERS = ("card-local", "shared-layer", "null")
SCHEMA_VERSION = 1


def run(cmd, timeout=60):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or ""), (p.stderr or "")
    except FileNotFoundError:
        return 127, "", "not found: %s" % cmd[0]
    except subprocess.TimeoutExpired:
        return 124, "", "timeout: %s" % " ".join(cmd)
    except OSError as exc:
        # Any other exec failure (EAGAIN/EACCES/injected Popen) is a failed run, never a traceback:
        # every caller branches on rc, and a host probe that raises would take the sentinel/incident
        # writer down with it.
        return 1, "", "exec failed: %s (%s)" % (cmd[0], exc)


def host_boot_id():
    """Stable per-boot identifier. Single source of truth shared with preflight/reconcile."""
    for executable in ("/usr/sbin/sysctl", "sysctl"):
        rc, out, _ = run([executable, "-n", "kern.boottime"])
        if rc == 0 and "sec" in out:
            return "boot-%s" % out.split("sec =")[1].split(",")[0].strip()
    return "boot-unknown"


def now_utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def existing_terminals(attempt_dir):
    return [t for t in TERMINALS if os.path.exists(os.path.join(attempt_dir, t))]


def atomic_write_json(path, payload):
    tmp = path + ".tmp"
    data = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    with open(tmp, "w") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.rename(tmp, path)  # same filesystem, atomic, publish last


def cmd_publish(args):
    attempt_dir = os.path.abspath(args.attempt_dir)
    if not os.path.isdir(attempt_dir):
        sys.stderr.write("usage error: not a directory: %s\n" % attempt_dir)
        return 2
    # New families have no card owner. Never silently publish a card-owned sentinel into one.
    family_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        attempt_dir)))), "family.json")
    try:
        with open(family_path) as fh:
            family = json.load(fh)
    except (OSError, ValueError):
        sys.stderr.write("refused: family.json missing or unreadable: %s\n" % family_path)
        return 1
    if not isinstance(family, dict):
        sys.stderr.write("refused: family.json is not an object\n")
        return 1
    direct = isinstance(family, dict) and isinstance(family.get("handoff"), dict) and \
        family["handoff"].get("execution") == "direct_hermes"
    if (family.get("family_id") != args.family_id or
            args.round_id != os.path.basename(os.path.dirname(os.path.dirname(attempt_dir))) or
            args.run_id != os.path.basename(attempt_dir) or
            (direct and (args.task_id or args.board)) or
            (not direct and not args.task_id)):
        sys.stderr.write("refused: family/run identity or execution ownership mismatch\n")
        return 1
    # Never overwrite terminal evidence (INV-15 / contract 8).
    present = existing_terminals(attempt_dir)
    if present:
        sys.stderr.write("refused: terminal evidence already exists in %s: %s (INV-15 forbids re-run)\n"
                         % (attempt_dir, present))
        return 1
    if args.status in ("FAILED", "INCOMPLETE") and args.failure_layer == "null":
        sys.stderr.write("usage error: FAILED/INCOMPLETE requires --failure-layer card-local|shared-layer\n")
        return 2

    manifest = []
    checksums = {}
    for rel in args.manifest:
        rel = rel.strip()
        if not rel:
            continue
        full = os.path.join(attempt_dir, rel)
        if not os.path.isfile(full):
            sys.stderr.write("refused: manifest entry missing: %s\n" % full)
            return 1
        manifest.append(rel)
        checksums[rel] = sha256_file(full)

    sentinel = {
        "schema_version": SCHEMA_VERSION,
        "status": args.status,
        "family_id": args.family_id,
        "round_id": args.round_id,
        "run_id": args.run_id,

        "created_at_utc": now_utc(),
        "host_boot_id": args.host_boot_id if args.host_boot_id is not None else host_boot_id(),
        "container_id": args.container_id,
        "image_id": args.image_id,
        "qlib_version": args.qlib_version,
        "verdict_hint": args.verdict_hint,
        "failure": {
            "layer": args.failure_layer if args.status != "DONE" else "null",
            "class": args.failure_class,
            "detail": args.failure_detail,
        },
        "artifact_manifest": manifest,
        "artifact_checksums": checksums,
        "runtime_seconds": args.runtime_seconds,
    }
    if args.task_id:
        # Historical Kanban-owned attempts keep their exact frozen identity schema.
        sentinel["task_id"] = args.task_id
        sentinel["kanban_board"] = args.board or "quant-strategy-research"
    elif args.board:
        sys.stderr.write("usage error: --board without --task-id is not a direct family\n")
        return 2
    target = os.path.join(attempt_dir, args.status)
    atomic_write_json(target, sentinel)
    print(json.dumps({"published": target, "sentinel": sentinel}, indent=2, ensure_ascii=False))
    return 0


def cmd_check(args):
    attempt_dir = os.path.abspath(args.attempt_dir)
    present = existing_terminals(attempt_dir)
    if len(present) != 1:
        print(json.dumps({"ok": False, "reason": "expected exactly one terminal file, found %s" % present,
                          "attempt_dir": attempt_dir}, indent=2))
        return 1
    name = present[0]
    try:
        with open(os.path.join(attempt_dir, name)) as fh:
            sentinel = json.load(fh)
    except ValueError as exc:
        print(json.dumps({"ok": False, "reason": "sentinel not valid JSON: %s" % exc, "attempt_dir": attempt_dir}))
        return 1

    problems = []
    if sentinel.get("status") != name:
        problems.append("status field %r != filename %r" % (sentinel.get("status"), name))
    if os.path.basename(attempt_dir) != sentinel.get("run_id"):
        problems.append("run_id %r != attempt dir name %r" % (sentinel.get("run_id"), os.path.basename(attempt_dir)))
    for rel, want in (sentinel.get("artifact_checksums") or {}).items():
        full = os.path.join(attempt_dir, rel)
        if not os.path.isfile(full):
            problems.append("missing artifact: %s" % rel)
            continue
        got = sha256_file(full)
        if got != want:
            problems.append("checksum mismatch %s: sentinel=%s recomputed=%s" % (rel, want, got))
    for rel in (sentinel.get("artifact_manifest") or []):
        if rel not in (sentinel.get("artifact_checksums") or {}):
            problems.append("manifest entry without checksum: %s" % rel)

    out = {
        "ok": not problems,
        "attempt_dir": attempt_dir,
        "terminal": name,
        "status": sentinel.get("status"),
        "family_id": sentinel.get("family_id"),
        "round_id": sentinel.get("round_id"),
        "run_id": sentinel.get("run_id"),
        "task_id": sentinel.get("task_id"),
        "verdict_hint": sentinel.get("verdict_hint"),
        "artifact_manifest": sentinel.get("artifact_manifest"),
        "host_boot_id": sentinel.get("host_boot_id"),
        "current_host_boot_id": host_boot_id(),
        "problems": problems,
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if not problems else 1


def main():
    ap = argparse.ArgumentParser(description="terminal evidence (DONE/FAILED/INCOMPLETE) writer+checker")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("publish", help="publish a terminal sentinel (atomic, last)")
    p.add_argument("--attempt-dir", required=True)
    p.add_argument("--status", required=True, choices=STATUSES)
    p.add_argument("--family-id", required=True)
    p.add_argument("--round-id", required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--task-id", default=None, help="historical Kanban attempts only; omit for direct")
    p.add_argument("--board", default=None, help="historical Kanban board only")
    p.add_argument("--manifest", action="append", default=[],
                   help="required artifact, path relative to --attempt-dir (repeatable, comma separated ok)")
    p.add_argument("--failure-layer", default="null", choices=LAYERS)
    p.add_argument("--failure-class", default=None)
    p.add_argument("--failure-detail", default=None)
    p.add_argument("--verdict-hint", default="NONE", choices=HINTS)
    p.add_argument("--host-boot-id", default=None,
                   help="validated host boot id supplied by a trusted host reconciler")
    p.add_argument("--container-id", default="qlib-run")
    p.add_argument("--image-id", default="qlib:0.9.7-arm64")
    p.add_argument("--qlib-version", default="0.9.7")
    p.add_argument("--runtime-seconds", type=int, default=None)
    p.set_defaults(func=cmd_publish)

    c = sub.add_parser("check", help="re-read + recompute checksums (read-only)")
    c.add_argument("--attempt-dir", required=True)
    c.set_defaults(func=cmd_check)

    args = ap.parse_args()
    if args.cmd == "publish":
        flat = []
        for item in args.manifest:
            flat.extend([x for x in item.split(",") if x.strip()])
        args.manifest = flat
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
