#!/usr/bin/env python3
"""Frozen survivor bundle writer (Contract v1.4.0, sections 7.3 / 10.8).

Contract v1.4.0 says: a strategy family passes the basic research gate as soon as it has
AT LEAST ONE cohort survivor, and EVERY survivor of the round is kept and advances.  The
family's survivors are therefore frozen as one bundle - not as a ranking, not as a
shortlist, and never as a single chosen cell.

This script is the only producer of that artifact.  It is a host-side, pure-stdlib,
deterministic reader of the attempt's immutable artifacts: it re-reads the survivors from
`artifacts/cohort_survivors.json`, re-checks them against `result.json` (count, order,
disposition band, coverage and assertions), and writes

    <round-dir>/survivor-bundle.json

atomically with the source checksums.  It never ranks, sorts, filters or drops a survivor,
never rewrites an existing bundle, never writes into an attempt directory, never runs
Qlib and is not a service (contract 1.2).

usage:
  python3 runtime/survivor_bundle.py --attempt-dir <attempt> [--out <path>] [--check] [--json]
exit: 0 = ok (written / already identical / check clean), 1 = refused or mismatch,
      2 = usage error
"""
import argparse
import hashlib
import json
import os
import sys
import time

SCHEMA_VERSION = 1
KIND = "frozen_survivor_bundle"
CONTRACT_VERSION = "v1.4.0"
TERMINAL_OK = "DONE"
# The v1.4.0 disposition bands (contract 7.3).  >=1 survivor passes the basic gate;
# the count selects the BAND, never the verdict.
BANDS = {0: "REJECT / NO_SURVIVOR", 1: "SURVIVOR_FOUND"}
# A round that ran under contract < v1.4.0 recorded FINALIST for >1 survivor plus
# performance_claimable=false (the gate v1.4.0 removed).  That pair - and only that pair - is
# accepted as legacy source semantics; the bundle still freezes the v1.4.0 verdict (PASS) and
# says so, because the source attempt is immutable and is never rewritten.
LEGACY_MULTI_VERDICT = "FINALIST"


def now_utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def band_for(count):
    return BANDS[count] if count < 2 else "MULTIPLE_SURVIVORS"


def verdict_for(count):
    return "REJECT" if count == 0 else "PASS"


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def identity(obj):
    """Reproducible identity of the bundle: everything except the generation timestamp."""
    body = {k: v for k, v in obj.items() if k != "generated_at_utc"}
    return "sha256:" + hashlib.sha256(canonical(body).encode()).hexdigest()


def load_json(path):
    with open(path) as fh:
        return json.load(fh)


def find_terminal(attempt_dir):
    found = [s for s in (TERMINAL_OK, "FAILED", "INCOMPLETE")
             if os.path.exists(os.path.join(attempt_dir, s))]
    return found


def build(attempt_dir, attempts_root=None):
    """Re-read the attempt and return (bundle, problems).  Pure function of the files."""
    problems = []
    paths = {
        "run-spec.json": os.path.join(attempt_dir, "run-spec.json"),
        "result.json": os.path.join(attempt_dir, "result.json"),
        "artifacts/cohort_survivors.json": os.path.join(attempt_dir, "artifacts", "cohort_survivors.json"),
        "artifacts/cohort_results.json": os.path.join(attempt_dir, "artifacts", "cohort_results.json"),
        "artifacts/assertions.json": os.path.join(attempt_dir, "artifacts", "assertions.json"),
    }
    for name, path in paths.items():
        if not os.path.isfile(path):
            problems.append("missing immutable source artifact: %s" % name)
    terminal = find_terminal(attempt_dir)
    if terminal != [TERMINAL_OK]:
        problems.append("attempt is not terminally DONE (found %r): a bundle is only frozen "
                        "from a terminal DONE attempt" % terminal)
    if problems:
        return None, problems

    run_spec = load_json(paths["run-spec.json"])
    result = load_json(paths["result.json"])
    survivors = load_json(paths["artifacts/cohort_survivors.json"])
    cohort_results = load_json(paths["artifacts/cohort_results.json"])
    assertions = load_json(paths["artifacts/assertions.json"])

    if not isinstance(survivors, list):
        problems.append("artifacts/cohort_survivors.json is not a list of survivor records")
        return None, problems

    identity_keys = ("family_id", "round_id", "run_id")
    for key in identity_keys:
        if run_spec.get(key) != result.get(key):
            problems.append("identity mismatch %s: run-spec %r != result.json %r"
                            % (key, run_spec.get(key), result.get(key)))

    labels = [s.get("cohort") for s in survivors]
    declared = result.get("cohort_survivors")
    if labels != declared:
        problems.append("survivors %r != result.json cohort_survivors %r "
                        "(the bundle must carry exactly the round's survivors, in the "
                        "order the run recorded them)" % (labels, declared))
    if result.get("cohort_survivor_count") != len(survivors):
        problems.append("survivor count mismatch: %r records vs result.json "
                        "cohort_survivor_count %r"
                        % (len(survivors), result.get("cohort_survivor_count")))
    if len(set(labels)) != len(labels):
        problems.append("duplicate cohort labels in the survivor set: %r" % labels)
    for rec in survivors:
        if rec.get("outcome") != "SURVIVOR":
            problems.append("cohort %r is in the survivor file with outcome %r"
                            % (rec.get("cohort"), rec.get("outcome")))
        if not rec.get("winner") or not rec.get("metrics"):
            problems.append("cohort %r carries no winner/metrics evidence" % rec.get("cohort"))
        if rec.get("cull_reasons"):
            problems.append("cohort %r is a survivor but carries cull_reasons %r"
                            % (rec.get("cohort"), rec.get("cull_reasons")))
    measured_survivors = [c.get("cohort") for c in cohort_results if c.get("outcome") == "SURVIVOR"]
    if measured_survivors != labels:
        problems.append("cohort_results.json reports survivors %r but the survivor file holds %r"
                        % (measured_survivors, labels))

    count = len(survivors)
    expected_band = band_for(count)
    if result.get("disposition") != expected_band:
        problems.append("disposition band mismatch: result.json %r, v1.4.0 mapping for %d "
                        "survivor(s) is %r" % (result.get("disposition"), count, expected_band))
    expected_verdict = verdict_for(count)
    legacy_source = (count > 1
                     and result.get("verdict_recommendation") == LEGACY_MULTI_VERDICT
                     and result.get("performance_claimable_recommendation") is False)
    if not legacy_source:
        if result.get("verdict_recommendation") != expected_verdict:
            problems.append("verdict recommendation mismatch: result.json %r, v1.4.0 mapping for "
                            "%d survivor(s) is %r"
                            % (result.get("verdict_recommendation"), count, expected_verdict))
        if count > 1 and result.get("performance_claimable_recommendation") is False:
            problems.append("v1.4.0: more than one survivor must not force "
                            "performance_claimable_recommendation false by itself")
    if result.get("coverage_complete") is not True:
        problems.append("coverage_complete is not true: an incomplete measurement is never frozen")
    false_assertions = sorted(k for k, v in (assertions or {}).items() if v is not True)
    if false_assertions:
        problems.append("assertions.json has non-true entries: %r" % false_assertions)
    if result.get("case_evaluations_total") != result.get("expected_case_evaluations"):
        problems.append("case evaluations %r != expected %r"
                        % (result.get("case_evaluations_total"),
                           result.get("expected_case_evaluations")))

    sources = dict(paths)
    round_dir = os.path.dirname(os.path.dirname(os.path.abspath(attempt_dir)))
    for name in ("round-spec.json", "verdict.json"):
        candidate = os.path.join(round_dir, name)
        if os.path.isfile(candidate):
            sources[name] = candidate
    sources["DONE"] = os.path.join(attempt_dir, "DONE")
    if problems:
        return None, problems

    bundle = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "contract": "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md %s" % CONTRACT_VERSION,
        "contract_section": "7.3 / 10.8",
        "family_id": run_spec["family_id"],
        "round_id": run_spec["round_id"],
        "run_id": run_spec["run_id"],
        "kanban_task_id": run_spec.get("task_id"),
        "kanban_board": run_spec.get("kanban_board"),
        "selector_version": result.get("selector_version") or run_spec.get("selector_version"),
        "disposition_version": result.get("disposition_version") or run_spec.get("disposition_version"),
        "source_attempt_dir": os.path.abspath(attempt_dir),
        "survivor_count": count,
        "disposition_band": expected_band,
        "verdict": expected_verdict,
        "all_survivors_advance": True,
        "ranking": None,
        "note": ("v1.4.0: >=1 cohort survivor passes the basic research gate, so every survivor "
                 "listed here passes it. All %d survivor(s) are frozen and advance; they are listed "
                 "in the order the run recorded them, which is NOT a ranking, and no survivor is "
                 "dropped, ranked or picked among. performance_claimable is decided by contract 9.6 "
                 "alone." % count),
        "source_attempt_semantics": (
            {"verdict_mapping": "pre-v1.4.0 (the attempt ran under contract < v1.4.0)",
             "verdict_recommendation_recorded_in_result_json": result.get("verdict_recommendation"),
             "performance_claimable_recommendation_recorded_in_result_json":
                 result.get("performance_claimable_recommendation"),
             "why_tolerated": ("the >1-survivor FINALIST mapping plus performance_claimable=false is "
                               "the semantics v1.4.0 replaces; the source attempt is immutable and is "
                               "never rewritten, so the bundle records the v1.4.0 verdict (PASS) "
                               "while disclosing the semantics the source artifacts were written under"),
             } if legacy_source else
            {"verdict_mapping": "v1.4.0",
             "verdict_recommendation_recorded_in_result_json": result.get("verdict_recommendation"),
             "performance_claimable_recommendation_recorded_in_result_json":
                 result.get("performance_claimable_recommendation")}),
        "source_verdict_not_rewritten": True,
        "survivors": survivors,
        "source_artifacts": {name: sha256_file(path) for name, path in sorted(sources.items())},
        "generator": {"path": "runtime/survivor_bundle.py",
                      "sha256": sha256_file(os.path.abspath(__file__))},
        "generated_at_utc": now_utc(),
    }
    bundle["bundle_identity_sha256"] = identity(bundle)
    return bundle, []


def write_bundle(bundle, out_path):
    """Idempotent atomic write: identical bundle -> no-op; different bytes -> refuse."""
    text = json.dumps(bundle, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    if os.path.exists(out_path):
        existing = load_json(out_path)
        if identity(existing) == identity(bundle):
            return "already_identical"
        return "refused_different_bytes"
    tmp = out_path + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, out_path)
    return "written"


def main():
    ap = argparse.ArgumentParser(description="Frozen survivor bundle writer (contract v1.4.0)")
    ap.add_argument("--attempt-dir", required=True, help="the terminally DONE attempt directory")
    ap.add_argument("--out", default=None,
                    help="bundle path (default: <round-dir>/survivor-bundle.json, itself derived "
                         "from the attempt path)")
    ap.add_argument("--check", action="store_true",
                    help="recompute and compare with the existing bundle; write nothing")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    attempt_dir = os.path.abspath(args.attempt_dir)
    if not os.path.isdir(attempt_dir):
        sys.stderr.write("usage error: not a directory: %s\n" % attempt_dir)
        return 2
    attempt_dir = attempt_dir.rstrip(os.sep)
    round_dir = os.path.dirname(os.path.dirname(attempt_dir))
    out_path = os.path.abspath(args.out) if args.out else os.path.join(round_dir, "survivor-bundle.json")

    bundle, problems = build(attempt_dir)
    if problems:
        for p in problems:
            sys.stderr.write("REFUSED: %s\n" % p)
        return 1

    if args.check:
        if not os.path.exists(out_path):
            sys.stderr.write("REFUSED: no bundle at %s\n" % out_path)
            return 1
        existing = load_json(out_path)
        if identity(existing) != identity(bundle):
            sys.stderr.write("REFUSED: bundle %s does not match the attempt's artifacts\n" % out_path)
            return 1
        result = "check_clean"
    else:
        result = write_bundle(bundle, out_path)
        if result == "refused_different_bytes":
            sys.stderr.write("REFUSED: %s already exists with different content; a frozen bundle "
                             "is never rewritten (contract 10.8)\n" % out_path)
            return 1

    out = {"ok": True, "result": result, "bundle_path": out_path, "survivor_count": bundle["survivor_count"],
           "disposition_band": bundle["disposition_band"], "verdict": bundle["verdict"],
           "survivors": [s["cohort"] for s in bundle["survivors"]],
           "bundle_identity_sha256": bundle["bundle_identity_sha256"]}
    if args.json:
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        print("bundle=%s result=%s survivors=%d band=%s verdict=%s identity=%s survivors=%s"
              % (out_path, result, out["survivor_count"], out["disposition_band"], out["verdict"],
                 out["bundle_identity_sha256"], ",".join(out["survivors"]) or "-"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
