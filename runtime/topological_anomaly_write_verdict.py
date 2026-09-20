#!/usr/bin/env python3
"""Write the final topological-anomaly verdict from frozen attempt artifacts only."""
import argparse
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from topological_anomaly_verdict_inputs import derive  # noqa: E402

FAMILY_ID = "cross-sectional-topological-anomaly-score-intraday-equity-return-predictability-2026-09-02"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def atomic_json(path, value):
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(value, fh, indent=2, ensure_ascii=False, allow_nan=False)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("attempt_dir")
    ap.add_argument("--out", default=None)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    attempt = Path(args.attempt_dir).resolve()
    if not (attempt / "DONE").is_file():
        print("REFUSED: attempt is not terminally DONE", file=sys.stderr)
        return 1
    evidence = derive(str(attempt))
    if not evidence["ok"]:
        for problem in evidence["problems"]:
            print("REFUSED: " + problem, file=sys.stderr)
        return 1
    result = evidence["result"]
    if result.get("family_id") != FAMILY_ID:
        print("REFUSED: family mismatch", file=sys.stderr)
        return 1
    round_dir = attempt.parent.parent
    out = Path(args.out).resolve() if args.out else round_dir / "verdict.json"
    source_names = ["run-spec.json", "result.json", "artifacts/assertions.json",
                    "artifacts/cohort_results.json", "artifacts/cohort_survivors.json"]
    source_names += ["artifacts/grid_%s.csv" % g for g in evidence["coverage"]["grids"]]
    source = {name: sha256(attempt / name) for name in source_names}
    count = evidence["survivor_count"]
    verdict = "PASS" if count else "REJECT"
    body = {
        "schema_version": 1, "document_kind": "final_verdict", "family_id": FAMILY_ID,
        "round_id": result["round_id"], "run_id": result["run_id"],
        "task_id": result["task_id"], "kanban_board": result["kanban_board"],
        "verdict": verdict, "disposition": result["disposition"],
        "cohort_survivor_count": count, "cohort_survivors": result["cohort_survivors"],
        "performance_claimable": bool(result.get("performance_claimable_recommendation")),
        "coverage_complete": bool(evidence["coverage"]["rows_total"] == evidence["coverage"]["expected_total"]),
        "assertions": evidence["assertions"], "aggregates": evidence["aggregates"],
        "winner_evidence": evidence["winners"],
        "research_boundary": "PASS means the preregistered research gate only; this local crypto adaptation is unproven, not adopted, and not authorized for paper/testnet/live trading.",
        "source_attempt_dir": str(attempt), "source_artifacts": source,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if args.check:
        if not out.is_file():
            print("REFUSED: missing verdict %s" % out, file=sys.stderr)
            return 1
        existing = json.load(open(out, encoding="utf-8"))
        for key in ("family_id", "round_id", "run_id", "verdict", "disposition", "cohort_survivors", "source_artifacts"):
            if existing.get(key) != body.get(key):
                print("REFUSED: verdict field changed: %s" % key, file=sys.stderr)
                return 1
        action = "check_clean"
    elif out.exists():
        existing = json.load(open(out, encoding="utf-8"))
        same = all(existing.get(key) == body.get(key) for key in ("family_id", "round_id", "run_id", "verdict", "disposition", "cohort_survivors", "source_artifacts"))
        if not same:
            print("REFUSED: verdict exists with different frozen content", file=sys.stderr)
            return 1
        action = "already_identical"
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(out, body)
        action = "written"
    response = {"ok": True, "action": action, "verdict_path": str(out), "verdict": verdict,
                "survivor_count": count, "survivors": result["cohort_survivors"]}
    print(json.dumps(response, indent=2) if args.json else
          "verdict=%s survivors=%d path=%s action=%s" % (verdict, count, out, action))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
