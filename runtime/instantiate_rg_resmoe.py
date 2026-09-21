#!/usr/bin/env python3
"""Instantiate the frozen RG-ResMoE round/run specs; only identity and deployment pins vary."""
import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import parameter_contract as pc
import rg_resmoe_counts as counts

FAMILY_ID = counts.FAMILY_ID
DEFAULT_RESULTS = "/Volumes/ExpansionDrive/qlib-results"
DEFAULT_SCRIPTS = "/Users/hong/workspace/qlib-apple-container/scripts"
SCRIPT_PATH = "/scripts/180_rg_resmoe_run.py"
SELFCHECK_PATH = "/scripts/tests/test_rg_resmoe_engine.py"
ROUND_TEMPLATE = HERE / "templates" / "rg_resmoe_round_spec.template.json"
RUN_TEMPLATE = HERE / "templates" / "rg_resmoe_run_spec.template.json"


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return "sha256:" + h.hexdigest()


def substitute(value, mapping):
    if isinstance(value, str):
        for key, replacement in mapping.items():
            value = value.replace(key, replacement)
        return value
    if isinstance(value, list):
        return [substitute(v, mapping) for v in value]
    if isinstance(value, dict):
        return {k: substitute(v, mapping) for k, v in value.items()}
    return value


def atomic_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-root", default=DEFAULT_RESULTS)
    ap.add_argument("--task-id", default=counts.TASK_ID)
    ap.add_argument("--round-id", default=FAMILY_ID + "-r1")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--host-scripts", default=DEFAULT_SCRIPTS)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    run_id = args.run_id or args.round_id + "-u1"
    runner = Path(args.host_scripts) / "180_rg_resmoe_run.py"
    test = Path(args.host_scripts) / "tests" / "test_rg_resmoe_engine.py"
    problems = []
    family_dir = Path(args.results_root) / FAMILY_ID
    try:
        family = load(family_dir / "family.json")
    except (OSError, ValueError) as exc:
        problems.append("family.json unreadable: %s" % exc)
        family = {}
    if family.get("family_id") != FAMILY_ID:
        problems.append("family_id mismatch")
    if family.get("kanban_task_id") != args.task_id:
        problems.append("family.json task mismatch: %r" % family.get("kanban_task_id"))
    if family.get("semantic_fingerprint") != counts.fingerprint(family.get("fingerprint_input")):
        problems.append("family semantic fingerprint mismatch")
    if not runner.is_file():
        problems.append("runner missing: %s" % runner)
    if not test.is_file():
        problems.append("self-check missing: %s" % test)
    if problems:
        out = {"ok": False, "problems": problems, "family_id": FAMILY_ID, "round_id": args.round_id, "run_id": run_id}
        print(json.dumps(out, indent=2) if args.json else "REFUSED: " + "; ".join(problems))
        return 1
    rsha, tsha = sha256(runner), sha256(test)
    created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    mapping = {"{{round_id}}": args.round_id, "{{run_id}}": run_id, "{{task_id}}": args.task_id,
               "{{script_sha256}}": rsha, "{{engine_selfcheck_sha256}}": tsha}
    round_spec = substitute(load(ROUND_TEMPLATE), mapping)
    run_spec = substitute(load(RUN_TEMPLATE), mapping)
    for label, obj in (("round", round_spec), ("run", run_spec)):
        if obj.get("family_id") != FAMILY_ID:
            problems.append("%s family mismatch" % label)
        if pc.validate_contract(obj.get("parameter_contract")):
            problems.extend("%s: %s" % (label, p) for p in pc.validate_contract(obj.get("parameter_contract")))
        if label == "round" and pc.validate_round_spec_contract(obj):
            problems.extend("round: %s" % p for p in pc.validate_round_spec_contract(obj))
    if problems:
        print(json.dumps({"ok": False, "problems": problems}, indent=2))
        return 1
    round_dir = family_dir / "rounds" / args.round_id
    attempt_dir = round_dir / "attempts" / run_id
    if not args.dry_run:
        round_path = round_dir / "round-spec.json"
        if round_path.exists() and load(round_path) != round_spec:
            raise SystemExit("refusing to overwrite immutable round spec: %s" % round_path)
        if not round_path.exists():
            atomic_new(round_path, round_spec)
        if attempt_dir.exists():
            raise SystemExit("refusing to overwrite existing attempt: %s" % attempt_dir)
        atomic_new(attempt_dir / "run-spec.json", run_spec)
    out = {"ok": True, "dry_run": args.dry_run, "family_id": FAMILY_ID, "round_id": args.round_id,
           "run_id": run_id, "round_dir": str(round_dir), "attempt_dir": str(attempt_dir),
           "runner_sha256": rsha, "selfcheck_sha256": tsha,
           "expected_case_evaluations": counts.expected_counts()["case_evaluations_total"]}
    print(json.dumps(out, indent=2) if args.json else
          "round=%s run=%s attempt=%s expected=%d" % (args.round_id, run_id, attempt_dir, out["expected_case_evaluations"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
