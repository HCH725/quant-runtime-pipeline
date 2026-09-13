#!/usr/bin/env python3
"""Instantiate the frozen Strategy B v2 templates into /results for a production launch.

Host side, pure stdlib, deterministic given (task id, clock, deployed script bytes).  This is
the launch card's step 1 (contract 9.2 / 10.1 / 10.2 / 10.6): the templates under
`runtime/templates/` stay frozen and untouched; the instantiated copies land as the immutable
round-spec / run-spec of one attempt:

    <results>/<family_id>/rounds/<round_id>/round-spec.json
    <results>/<family_id>/rounds/<round_id>/attempts/<run_id>/run-spec.json

What may change at instantiation (the rule of the templates, and the precedent of the
Strategy A v2 instantiation): the registered placeholders, the fingerprint value that the
template deliberately leaves as TO_BE_RECOMPUTED, and the template-era implementation-status
prose, which must describe the launch-time truth instead of the preregistration-era truth.
Every scientific field (domains, split, gates, selector/disposition versions, falsification,
costs, provenance classes) is copied verbatim and is re-validated with
`runtime/strategy_b_v2_counts.py` after substitution.

Fail-closed: a missing/mismatching family.json, a leftover placeholder token, a counts
mismatch or an existing target file aborts before anything is published.

usage:
  python3 runtime/instantiate_strategy_b_v2.py --results-root <dir> --task-id t_XXXXXXXX
        [--round-id <id>] [--run-id <id>] [--created-at-utc <ts>]
        [--runner-host <path>] [--engine-test-host <path>] [--dry-run] [--json]
exit: 0 = ok, 1 = refused/failed, 2 = usage error
"""
import argparse
import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import strategy_b_v2_counts as counts  # noqa: E402  (same directory, pure stdlib)

FAMILY_ID = "ema-crossover-walkforward-momentum-long-short-v2"
ROUND_TEMPLATE = os.path.join(HERE, "templates", "strategy_b_v2_round_spec.template.json")
RUN_TEMPLATE = os.path.join(HERE, "templates", "strategy_b_v2_run_spec.template.json")
DEFAULT_RESULTS = "/Volumes/ExpansionDrive/qlib-results"
DEFAULT_RUNNER_HOST = "/Users/hong/workspace/qlib-apple-container/scripts/30_strategy_b_run.py"
DEFAULT_ENGINE_TEST_HOST = "/Users/hong/workspace/qlib-apple-container/scripts/tests/test_strategy_b_engine.py"
ROUND_PLACEHOLDERS = ("{{round_id}}", "{{kanban_task_id}}", "{{created_at_utc}}")
RUN_PLACEHOLDERS = ("{{round_id}}", "{{run_id}}", "{{task_id}}", "{{created_at_utc}}",
                    "{{script_sha256}}", "{{engine_selfcheck_sha256}}", "{{round_spec_path}}")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def load(path):
    with open(path) as fh:
        return json.load(fh)


def walk_substitute(obj, mapping):
    """Recursively substitute placeholder tokens inside every string of a JSON document."""
    if isinstance(obj, str):
        out = obj
        for token, value in mapping.items():
            out = out.replace(token, value)
        return out
    if isinstance(obj, list):
        return [walk_substitute(v, mapping) for v in obj]
    if isinstance(obj, dict):
        return {k: walk_substitute(v, mapping) for k, v in obj.items()}
    return obj


def leftover_placeholders(obj, path="$"):
    found = []
    if isinstance(obj, str):
        if "{{" in obj:
            found.append(path)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found += leftover_placeholders(v, "%s[%d]" % (path, i))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            found += leftover_placeholders(v, "%s.%s" % (path, k))
    return found


def atomic_write_new(path, payload):
    """O_EXCL write (the artifact is immutable: never overwrite an existing attempt file)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    try:
        with open(tmp) as src:
            os.write(fd, src.read().encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    os.unlink(tmp)


def main():
    ap = argparse.ArgumentParser(description="instantiate the frozen B v2 templates")
    ap.add_argument("--results-root", default=os.environ.get("QLIB_RESULTS_ROOT", DEFAULT_RESULTS))
    ap.add_argument("--task-id", required=True)
    ap.add_argument("--round-id", default=None)
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--created-at-utc", default=None)
    ap.add_argument("--runner-host", default=DEFAULT_RUNNER_HOST)
    ap.add_argument("--engine-test-host", default=DEFAULT_ENGINE_TEST_HOST)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    round_id = args.round_id or "%s-r1" % FAMILY_ID
    run_id = args.run_id or "%s-u1" % round_id
    created_at = args.created_at_utc or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    record = {"schema_version": 1, "kind": "b_v2_spec_instantiation", "family_id": FAMILY_ID,
              "task_id": args.task_id, "round_id": round_id, "run_id": run_id,
              "created_at_utc": created_at, "dry_run": args.dry_run, "problems": []}

    # ---- ownership readback: the family.json must already exist and point at THIS card
    family_path = os.path.join(args.results_root, FAMILY_ID, "family.json")
    try:
        family = load(family_path)
    except (OSError, ValueError) as exc:
        record["problems"].append("cannot read %s: %s" % (family_path, exc))
        return finish(record, args)
    if family.get("family_id") != FAMILY_ID:
        record["problems"].append("family.json family_id mismatch")
    if family.get("kanban_task_id") != args.task_id:
        record["problems"].append("family.json kanban_task_id %r != %r"
                                  % (family.get("kanban_task_id"), args.task_id))
    fp_declared = family.get("semantic_fingerprint")
    fp_computed = counts.fingerprint(family.get("fingerprint_input"))
    if fp_declared != fp_computed:
        record["problems"].append("family.json semantic_fingerprint %r != recomputed %r"
                                  % (fp_declared, fp_computed))

    # ---- template -> instantiated documents
    round_spec = load(ROUND_TEMPLATE)
    run_spec = load(RUN_TEMPLATE)
    if round_spec["semantic_fingerprint"]["fingerprint_input"] != family.get("fingerprint_input"):
        record["problems"].append("round-spec template fingerprint_input != family.json "
                                  "fingerprint_input")
    runner_sha = sha256_file(args.runner_host)
    engine_test_sha = sha256_file(args.engine_test_host)
    round_spec = walk_substitute(round_spec, {"{{round_id}}": round_id,
                                              "{{kanban_task_id}}": args.task_id,
                                              "{{created_at_utc}}": created_at})
    round_spec["document_kind"] = (
        "round-spec (instantiated production pre-registration; every scientific field is copied "
        "verbatim from the frozen strategy_b_v2_round_spec.template.json and only the three "
        "registered placeholders, the TO_BE_RECOMPUTED fingerprint and the template-era "
        "implementation-status prose were substituted)")
    round_spec["semantic_fingerprint"]["semantic_fingerprint"] = fp_computed
    round_spec["template_instantiation"]["status"] = (
        "INSTANTIATED for production launch card %s at %s; the frozen template keeps its "
        "PREREGISTRATION ONLY / NOT LAUNCHED status" % (args.task_id, created_at))
    round_spec["implementation_status"] = {
        "engine": ("IMPLEMENTED and deployed: /scripts/30_strategy_b_run.py sha256:%s; the "
                   "v1.4.0-semantics B v2 engine is a new script (the archived, "
                   "operator-stopped B v1 runner is archive-only and was not reused)."
                   % runner_sha.replace("sha256:", "")),
        "engine_selfcheck": ("/scripts/tests/test_strategy_b_engine.py sha256:%s; run in "
                             "qlib-run before launch" % engine_test_sha.replace("sha256:", "")),
        "no_launch_in_this_card": ("The preregistration template was authored without launching "
                                   "anything; the launch is performed by card %s and lands in "
                                   "this instantiated copy." % args.task_id)}
    run_spec = walk_substitute(run_spec, {
        "{{round_id}}": round_id, "{{run_id}}": run_id, "{{task_id}}": args.task_id,
        "{{created_at_utc}}": created_at, "{{script_sha256}}": runner_sha,
        "{{engine_selfcheck_sha256}}": engine_test_sha,
        "{{round_spec_path}}": "/results/%s/rounds/%s/round-spec.json" % (FAMILY_ID, round_id)})
    run_spec["document_kind"] = (
        "run-spec (instantiated production attempt contract; every scientific field is copied "
        "verbatim from the frozen strategy_b_v2_run_spec.template.json and only the seven "
        "registered placeholders plus the template-era status prose were substituted)")
    run_spec["template_instantiation"]["status"] = (
        "INSTANTIATED for production launch card %s at %s (attempt %s)" % (args.task_id,
                                                                          created_at, run_id))
    run_spec["script"] = {"path": "/scripts/30_strategy_b_run.py",
                          "sha256": runner_sha,
                          "deployed_from": "HCH725/quant-runtime-pipeline "
                                           "container/scripts/30_strategy_b_run.py "
                                           "(byte-identical, P10 recomputes it host side)"}
    run_spec["engine_selfcheck"] = {"script": "/scripts/tests/test_strategy_b_engine.py",
                                    "sha256": engine_test_sha, "must_run_before_launch": True}

    for doc, name in ((round_spec, "round-spec"), (run_spec, "run-spec")):
        left = leftover_placeholders(doc)
        if left:
            record["problems"].append("%s has unsubstituted placeholders: %s" % (name, left[:5]))

    computed, problems, extra = counts.check(round_spec, run_spec)
    record["problems"] += ["counts/%s: %s" % (name, p) for p in problems]
    record["counts"] = {k: computed[k] for k in ("cohorts", "strategy_cases", "dca_configs",
                                                 "base_combinations_per_cohort",
                                                 "case_evaluations_per_grid",
                                                 "expected_case_evaluations")}
    record["counts_extra"] = extra
    record["runner_sha256"] = runner_sha
    record["engine_test_sha256"] = engine_test_sha
    round_path = os.path.join(args.results_root, FAMILY_ID, "rounds", round_id, "round-spec.json")
    run_path = os.path.join(args.results_root, FAMILY_ID, "rounds", round_id, "attempts", run_id,
                            "run-spec.json")
    record["round_spec_path"] = round_path
    record["run_spec_path"] = run_path
    if record["problems"]:
        return finish(record, args)
    if args.dry_run:
        record["action"] = "would_instantiate"
        return finish(record, args)
    round_written = True
    if os.path.exists(round_path):
        # same round, new run (contract 8: a technical retry keeps the round and its
        # immutable pre-registration): reuse the existing round-spec verbatim after
        # re-validating it against this card, and publish only the new attempt's run-spec.
        existing = load(round_path)
        _, ex_problems, _ = counts.check(existing, None)
        if ex_problems or existing.get("kanban_task_id") != args.task_id:
            record["problems"].append("existing round-spec is not valid for this card: %s"
                                      % (ex_problems[:2] or "kanban_task_id mismatch"))
        else:
            round_spec = existing
            round_written = False
            record["round_spec_reused"] = True
            record["round_spec_sha256"] = sha256_file(round_path)
    if os.path.exists(run_path):
        record["problems"].append("refusing to overwrite existing %s (INV-4)" % run_path)
    if record["problems"]:
        return finish(record, args)
    try:
        if round_written:
            atomic_write_new(round_path, round_spec)
        atomic_write_new(run_path, run_spec)
    except OSError as exc:
        record["problems"].append("publish failed: %s" % exc)
        return finish(record, args)
    # read back and re-validate the persisted bytes (never trust the in-memory copy)
    rb_round, rb_run = load(round_path), load(run_path)
    _, rb_problems, _ = counts.check(rb_round, rb_run)
    if rb_problems:
        record["problems"] += ["read-back: %s" % p for p in rb_problems]
    record["action"] = "instantiated"
    record["run_spec_sha256"] = sha256_file(run_path)
    return finish(record, args)


def finish(record, args):
    if args.json:
        print(json.dumps(record, indent=2, ensure_ascii=False))
    else:
        print("B v2 spec instantiation: %s%s" % (record.get("action", "refused"),
                                                 "" if not record["problems"] else
                                                 " (%d problem(s))" % len(record["problems"])))
        for p in record["problems"]:
            print("PROBLEM: %s" % p)
    return 0 if not record["problems"] else 1


if __name__ == "__main__":
    sys.exit(main())
