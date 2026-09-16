#!/usr/bin/env python3
"""Instantiate the frozen Strategy H v1 templates into a production attempt.

Contract refs: INV-4 (the pre-registration is immutable once the first run starts), section
14.2 step 7 (family.json is written by the handoff and read back here), section 26.1 (the
generic parameter contract is a launch gate) and section 9.4 (the run-spec pins the deployed
script's sha256).

Only the registered placeholders are substituted; the domains, split, costs, gates and the
selector/disposition versions are copied verbatim from the frozen templates.

usage:
  python3 runtime/instantiate_strategy_h_v1.py --task-id <kanban card> [--results-root <dir>]
          [--round-id <id>] [--run-id <id>] [--created-at-utc <iso>]
          [--host-scripts <deployed scripts dir>] [--runner-host <path>]
          [--engine-test-host <path>] [--dry-run] [--json]

--host-scripts <dir> is a convenience for the deployed container scripts directory: it supplies
both --runner-host (<dir>/80_strategy_h_run.py) and --engine-test-host
(<dir>/tests/test_strategy_h_engine.py) unless those are given explicitly.
"""
import argparse
import hashlib
import json
import os
import sys
import time
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import strategy_h_v1_counts as counts  # noqa: E402  (same directory, pure stdlib)
import parameter_contract as pc  # noqa: E402  (same directory, pure stdlib)

FAMILY_ID = counts.FAMILY_ID
ROUND_TEMPLATE = os.path.join(HERE, "templates", "strategy_h_v1_round_spec.template.json")
RUN_TEMPLATE = os.path.join(HERE, "templates", "strategy_h_v1_run_spec.template.json")
DEFAULT_RESULTS = "/Volumes/ExpansionDrive/qlib-results"
DEFAULT_SCRIPTS_HOST = "/Users/hong/workspace/qlib-apple-container/scripts"
DEFAULT_RUNNER_HOST = os.path.join(DEFAULT_SCRIPTS_HOST, "80_strategy_h_run.py")
DEFAULT_ENGINE_TEST_HOST = os.path.join(DEFAULT_SCRIPTS_HOST, "tests",
                                        "test_strategy_h_engine.py")
SCRIPT_PATH = "/scripts/80_strategy_h_run.py"
SELFCHECK_PATH = "/scripts/tests/test_strategy_h_engine.py"


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def load(path):
    with open(path) as fh:
        return json.load(fh)


def walk_substitute(obj: Any, mapping: dict) -> Any:
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
    data = (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
    except Exception:
        os.unlink(path)
        raise


def instantiate(round_spec, run_spec, task_id, round_id, run_id, created_at, runner_sha,
                engine_test_sha):
    """Pure substitution of the registered placeholders + the launch-time prose."""
    round_spec = walk_substitute(round_spec, {"{{round_id}}": round_id,
                                             "{{kanban_task_id}}": task_id,
                                             "{{created_at_utc}}": created_at})
    round_spec["document_kind"] = (
        "round-spec (instantiated production pre-registration; every scientific field is copied "
        "verbatim from the frozen strategy_h_v1_round_spec.template.json and only the three "
        "registered placeholders, the TO_BE_RECOMPUTED fingerprint and the template-era "
        "implementation-status prose were substituted)")
    round_spec["semantic_fingerprint"]["semantic_fingerprint"] = counts.fingerprint(
        round_spec["semantic_fingerprint"]["fingerprint_input"])
    round_spec["template_instantiation"]["status"] = (
        "INSTANTIATED for production launch card %s at %s; the frozen template keeps its "
        "PREREGISTRATION ONLY / NOT LAUNCHED status" % (task_id, created_at))
    round_spec["implementation_status"] = {
        "engine": ("IMPLEMENTED and deployed: %s %s; the H v1 engine is a new script written "
                   "for this family (no A/B/C/D/E/F/G runner is reused). It builds the causal "
                   "4h state layer by resampling each cohort's OWN 1h bars (exactly 4 base bars "
                   "per bin, states visible only at/after the bin close), discretises the "
                   "12-state price/volume layer, trains the transition matrix with "
                   "expanding-window point-in-time counts, emits LONG/SHORT/NONE signals, and "
                   "runs the DCA rail as an episode state machine with per-fill fees, adverse "
                   "tick slippage, per-settlement funding and the record's profitability-gated "
                   "opposite-signal exit."
                   % (SCRIPT_PATH, runner_sha)),
        "engine_selfcheck": ("%s %s; run in qlib-run before launch"
                             % (SELFCHECK_PATH, engine_test_sha)),
        "no_launch_in_this_card": ("The preregistration template was authored without launching "
                                   "anything; the launch is performed by card %s and lands in "
                                   "this instantiated copy." % task_id),
    }
    run_spec = walk_substitute(run_spec, {
        "{{round_id}}": round_id, "{{run_id}}": run_id, "{{task_id}}": task_id,
        "{{created_at_utc}}": created_at, "{{script_sha256}}": runner_sha,
        "{{engine_selfcheck_sha256}}": engine_test_sha,
        "{{round_spec_path}}": "/results/%s/rounds/%s/round-spec.json" % (FAMILY_ID, round_id)})
    run_spec["document_kind"] = (
        "run-spec (instantiated production attempt contract; every scientific field is copied "
        "verbatim from the frozen strategy_h_v1_run_spec.template.json and only the seven "
        "registered placeholders plus the template-era status prose were substituted)")
    run_spec["template_instantiation"]["status"] = (
        "INSTANTIATED for production launch card %s at %s (attempt %s)"
        % (task_id, created_at, run_id))
    run_spec["script"] = {"path": SCRIPT_PATH, "sha256": runner_sha,
                          "deployed_from": "HCH725/quant-runtime-pipeline "
                                           "container/scripts/80_strategy_h_run.py "
                                           "(byte-identical, P10 recomputes it host side)"}
    run_spec["engine_selfcheck"] = {"script": SELFCHECK_PATH, "sha256": engine_test_sha,
                                    "must_run_before_launch": True}
    return round_spec, run_spec


def main():
    ap = argparse.ArgumentParser(description="instantiate the frozen H v1 templates")
    ap.add_argument("--results-root", default=os.environ.get("QLIB_RESULTS_ROOT", DEFAULT_RESULTS))
    ap.add_argument("--task-id", required=True)
    ap.add_argument("--round-id", default=None)
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--created-at-utc", default=None)
    ap.add_argument("--host-scripts", default=None,
                    help="deployed container scripts dir: supplies the runner and engine-test "
                         "host paths unless they are given explicitly")
    ap.add_argument("--runner-host", default=None)
    ap.add_argument("--engine-test-host", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    host_scripts = args.host_scripts or DEFAULT_SCRIPTS_HOST
    runner_host = args.runner_host or os.path.join(host_scripts, "80_strategy_h_run.py")
    engine_test_host = (args.engine_test_host
                        or os.path.join(host_scripts, "tests", "test_strategy_h_engine.py"))
    round_id = args.round_id or "%s-r1" % FAMILY_ID
    run_id = args.run_id or "%s-u1" % round_id
    created_at = args.created_at_utc or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    record = {"schema_version": 1, "kind": "h_v1_spec_instantiation", "family_id": FAMILY_ID,
              "task_id": args.task_id, "round_id": round_id, "run_id": run_id,
              "created_at_utc": created_at, "dry_run": args.dry_run, "problems": []}

    # ---- ownership readback: family.json must already exist and point at THIS card
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
    round_template = load(ROUND_TEMPLATE)
    run_template = load(RUN_TEMPLATE)
    if round_template["semantic_fingerprint"]["fingerprint_input"] != family.get("fingerprint_input"):
        record["problems"].append("round-spec template fingerprint_input != family.json "
                                  "fingerprint_input (the frozen handoff value must be copied "
                                  "verbatim)")
    runner_sha = None
    engine_test_sha = None
    for path, key in ((runner_host, "runner_sha256"), (engine_test_host, "engine_test_sha256")):
        try:
            sha = sha256_file(path)
        except OSError as exc:
            record["problems"].append("cannot read the deployed host file %s: %s" % (path, exc))
            sha = None
        record[key] = sha
        if key == "runner_sha256":
            runner_sha = sha
        else:
            engine_test_sha = sha
    if record["problems"]:
        return finish(record, args)
    round_spec, run_spec = instantiate(round_template, run_template, args.task_id, round_id,
                                       run_id, created_at, runner_sha, engine_test_sha)

    for doc, name in ((round_spec, "round-spec"), (run_spec, "run-spec")):
        left = leftover_placeholders(doc)
        if left:
            record["problems"].append("%s has unsubstituted placeholders: %s" % (name, left[:5]))

    # v1.8 launch gate (contract 26.1): no compute and no publish without a valid generic
    # parameter_contract - the validation MUST precede counts.check.
    record["problems"] += ["round-spec contract: %s" % p
                           for p in pc.validate_round_spec_contract(round_spec)]
    computed, problems, extra = counts.check(round_spec, run_spec)
    record["problems"] += ["counts/round-spec: %s" % p for p in problems]
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
        # same round, new run (contract 8: a technical retry keeps the round and its immutable
        # pre-registration): reuse the existing round-spec verbatim after re-validating it
        # against this card, and publish only the new attempt's run-spec.
        existing = load(round_path)
        ex_contract = pc.validate_round_spec_contract(existing)   # contract first, then compute
        _, ex_problems, _ = counts.check(existing, None)
        if ex_problems or ex_contract or existing.get("kanban_task_id") != args.task_id:
            record["problems"].append("existing round-spec is not valid for this card: %s"
                                      % (ex_problems[:2] or ex_contract[:2]
                                         or "kanban_task_id mismatch"))
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
    record["problems"] += ["read-back round-spec contract: %s" % p
                           for p in pc.validate_round_spec_contract(rb_round)]
    _, rb_problems, _ = counts.check(rb_round, rb_run)
    if rb_problems:
        record["problems"] += ["read-back: %s" % p for p in rb_problems]
    record["action"] = "instantiated"
    record["run_spec_sha256"] = sha256_file(run_path)
    record["round_spec_sha256"] = sha256_file(round_path)
    return finish(record, args)


def finish(record, args):
    if args.json:
        print(json.dumps(record, indent=2, ensure_ascii=False))
    else:
        print("Strategy H v1 spec instantiation: %s%s"
              % (record.get("action", "refused"),
                 "" if not record["problems"] else " (%d problem(s))" % len(record["problems"])))
        for p in record["problems"]:
            print("PROBLEM: %s" % p)
    return 0 if not record["problems"] else 1


if __name__ == "__main__":
    sys.exit(main())
