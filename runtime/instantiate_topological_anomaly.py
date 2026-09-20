#!/usr/bin/env python3
"""Instantiate the registered topological-anomaly round and run spec.

Only placeholders are filled here; the template remains the frozen registration source.  The
script refuses a family mismatch and validates the generic parameter contract before writing.
"""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))
import parameter_contract as pc  # noqa: E402

FAMILY_ID = "cross-sectional-topological-anomaly-score-intraday-equity-return-predictability-2026-09-02"


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(value, fh, indent=2, ensure_ascii=False, sort_keys=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--family-dir", default="/Volumes/ExpansionDrive/qlib-results/" + FAMILY_ID)
    ap.add_argument("--round-id", default="round-2026-09-20-topological-anomaly-v3")
    ap.add_argument("--run-id", default="run-2026-09-20-topological-anomaly-v3-001")
    ap.add_argument("--out-root", default=None, help="optional results root; defaults to family-dir")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    family_dir = Path(args.family_dir).resolve()
    family_path = family_dir / "family.json"
    if not family_path.is_file():
        raise SystemExit("missing family.json: %s" % family_path)
    family = load(family_path)
    if family.get("family_id") != FAMILY_ID:
        raise SystemExit("family mismatch: %r" % family.get("family_id"))
    if family.get("kanban_task_id") != "t_0a6aba33":
        raise SystemExit("task mismatch: %r" % family.get("kanban_task_id"))
    if args.out_root:
        family_dir = Path(args.out_root).resolve()
        family_dir.mkdir(parents=True, exist_ok=True)
        atomic_json(family_dir / "family.json", family)
    template_dir = ROOT / "runtime" / "templates"
    round_obj = load(template_dir / "topological_anomaly_round_spec.template.json")
    run_obj = load(template_dir / "topological_anomaly_run_spec.template.json")
    for obj, label in ((round_obj, "round"), (run_obj, "run")):
        if obj.get("family_id") != FAMILY_ID:
            raise SystemExit("%s template family mismatch" % label)
        obj["round_id"] = args.round_id
        if label == "run":
            obj["run_id"] = args.run_id
        problems = pc.validate_contract(obj.get("parameter_contract"))
        if problems:
            raise SystemExit("%s parameter contract invalid: %s" % (label, "; ".join(problems)))
        if pc.validate_round_spec_contract(obj):
            raise SystemExit("%s parameter contract/domain mismatch: %s" %
                             (label, "; ".join(pc.validate_round_spec_contract(obj))))
    round_dir = family_dir / "rounds" / args.round_id
    attempt_dir = round_dir / "attempts" / args.run_id
    round_path = round_dir / "round-spec.json"
    if round_path.exists():
        if load(round_path) != round_obj:
            raise SystemExit("refusing to overwrite immutable round spec: %s" % round_path)
    else:
        atomic_json(round_path, round_obj)
    if attempt_dir.exists():
        raise SystemExit("refusing to overwrite existing attempt: %s" % attempt_dir)
    atomic_json(attempt_dir / "run-spec.json", run_obj)
    out = {"ok": True, "family_dir": str(family_dir), "round_dir": str(round_dir),
           "attempt_dir": str(attempt_dir), "round_id": args.round_id, "run_id": args.run_id,
           "expected_case_evaluations": run_obj["expected"]["case_evaluations_total"]}
    print(json.dumps(out, indent=2) if args.json else
          "round=%s run=%s attempt=%s expected=%d" %
          (args.round_id, args.run_id, attempt_dir, out["expected_case_evaluations"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
