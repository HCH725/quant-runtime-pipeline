#!/usr/bin/env python3
"""Deterministic preparation entrypoint (contract 14.4) - processes one backlog candidate per run.

<results>/_handoff/preparation_backlog.json is the ordered retry backlog of reviewed candidates that
are not yet execution-ready. This one-shot host runner owns the mechanical preparation loop that must
not sit in the C3 hot path, and it is the only thing that ever promotes a candidate into
<results>/_handoff/candidates.json:

  * nothing staged yet -> launch at most ONE bounded quant-preparation Hermes session (existing
    launcher: 15/20/40 turns, 1800 s run budget) behind its own family lease -> waiting;
  * a family-local preparation attempt that cannot launch, or that exits without a staged package or
    clear-absence outcome -> record the failure, keep the exact candidate, rotate it to the backlog
    tail, and let the next candidate proceed on the next cadence;
  * valid clear-absence outcome (canonical CONFIG/SCHEMA prove the core-required data absent) ->
    publish the existing no-compute TECHNICAL_INCOMPLETE terminal, append the exact candidate to the
    pool as consumed history, drop that candidate from the backlog;
  * staged prepared package (prepared-execution.json + immutable round/run specs + the family runner's
    focused test) -> bounded focused unittest, then the existing P1-P10 staged preflight; only a full
    PASS appends the exact candidate (+ absolute execution_file) and removes it from the backlog.

Promotion is agent-free: the Hermes session may only stage artifacts under _handoff/prepared/<family>/
or write the clear-absence outcome inside its preparing lease. Every mutation happens under the same
kernel lease C3 uses (_handoff/.advance.lock), so a promotion can never race the C3 pool read. Each step
is retry-idempotent: an already-consumed candidate is removed without rewriting the pool; identity,
backlog-shape, and pool-state inconsistencies still fail closed. Candidate-local transient preparation
failure is failure-isolated and must not monopolize unrelated candidates. Nothing here launches a
strategy runner or Qlib - the next C3 cadence dispatches the promoted family.

Outcome tokens (record/`--json`, and one stderr line per run): promoted, consumed, deferred, waiting,
idle, finding. Findings are reserved for integrity/system-state problems; a candidate-local transient
preparation failure is reported as deferred.

Exit codes: 0 = ok, 2 = usage error, 1 = unexpected.
"""
import argparse
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import production_handoff as h  # noqa: E402

BACKLOG_FILENAME = "preparation_backlog.json"
PREPARATION_STATUS = "preparation-status.json"
# Host source of the container's ro /scripts mount (the mapping preflight.py P10 resolves).
CONTAINER_SCRIPTS_HOST = "/Users/hong/workspace/qlib-apple-container/scripts"
FOCUSED_TEST_TIMEOUT_S = 300
BOUNDED_ENV = {"HOME": "/Users/hong", "PATH": "/opt/homebrew/bin:/usr/bin:/bin",
               "LANG": "en_US.UTF-8"}


def _write_json_atomic(path, doc):
    """Crash-safe full rewrite: same-directory temp file, fsync, atomic replace."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(doc, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _head_problem(head):
    """None when the backlog head is a promotable candidate object, else why not (fail-closed)."""
    if not isinstance(head, dict):
        return "preparation backlog head must be a candidate object"
    family_id = head.get("family_id")
    if not isinstance(family_id, str) or not h.FAMILY_ID.fullmatch(family_id):
        return "preparation backlog head has no valid family_id: %r" % (family_id,)
    fingerprint_input = head.get("fingerprint_input")
    if not isinstance(fingerprint_input, str) or not fingerprint_input.strip():
        return "preparation backlog head %s has no fingerprint_input" % family_id
    if not isinstance(head.get("title"), str) or not head["title"]:
        return "preparation backlog head %s has no title" % family_id
    return None


def _status_path(root, family_id):
    return (Path(root) / h.HANDOFF_DIRNAME / h.PREPARATION_DIRNAME / family_id /
            PREPARATION_STATUS)


def _load_preparation_status(root, family_id):
    path = _status_path(root, family_id)
    if not os.path.lexists(str(path)):
        return None, None
    doc = h._load_json(path)
    if not isinstance(doc, dict) or doc.get("schema_version") != 1 or \
            doc.get("family_id") != family_id or doc.get("state") not in ("running", "deferred"):
        return None, "invalid family-local preparation status: %s" % path
    return doc, None


def _write_preparation_status(root, family_id, state, reason, pid=None):
    path = _status_path(root, family_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"schema_version": 1, "family_id": family_id, "state": state,
           "updated_at_utc": h.now_utc(), "reason": reason}
    if pid is not None:
        doc["pid"] = pid
    _write_json_atomic(path, doc)
    return path


def _defer_head(res, root, backlog_path, backlog_doc, family_id, reason):
    """Preserve a transiently failed candidate and rotate it behind unrelated queued work."""
    try:
        status_path = _write_preparation_status(root, family_id, "deferred", reason)
    except OSError as exc:
        return res.finding("preparation_status_write_failed", str(exc), pool_entry=family_id)
    queue = list(backlog_doc["candidates"])
    rotated = queue[1:] + queue[:1]
    try:
        _write_json_atomic(backlog_path, dict(backlog_doc, candidates=rotated))
    except OSError as exc:
        return res.finding("backlog_write_failed", str(exc), pool_entry=family_id)
    readback = h._load_json(backlog_path)
    if not isinstance(readback, dict) or readback.get("candidates") != rotated:
        return res.finding("backlog_readback_failed",
                           "backlog read-back after preparation deferral does not match",
                           pool_entry=family_id)
    res.action, res.outcome = "deferred", "deferred"
    res.reason = reason
    res.detail["preparation_status"] = str(status_path)
    res.detail["deferred_family"] = family_id
    res.detail["backlog_depth"] = len(rotated)
    if rotated and isinstance(rotated[0], dict):
        res.detail["next_family"] = rotated[0].get("family_id")
    return res


def _run_focused_test(prepared):
    """Bounded focused unittest derived from the run-spec script: `/scripts/<name>.py` ->
    `<host scripts>/tests/test_<name>.py`; the file must be a regular non-symlink file and must run at
    least one test successfully. Returns None on PASS, else the failure reason."""
    stems = Path(prepared["script_path"]).name
    stem = stems[:-len(".py")] if stems.endswith(".py") else stems
    test_path = Path(CONTAINER_SCRIPTS_HOST) / "tests" / ("test_%s.py" % stem)
    try:
        info = os.lstat(str(test_path))
    except OSError as exc:
        return "focused test %s is missing: %s" % (test_path, exc)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        return "focused test %s must be a regular non-symlink file" % test_path
    cmd = [h.PREPARED_EXECUTION_PYTHON, "-m", "unittest", "discover", "-s", "tests",
           "-p", test_path.name]
    try:
        rc, out, err = h._run_bounded_process(cmd, CONTAINER_SCRIPTS_HOST, BOUNDED_ENV,
                                              FOCUSED_TEST_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "focused test run failed: %s" % exc
    ran = re.search(r"Ran (\d+) test", (err or "") + (out or ""))
    count = int(ran.group(1)) if ran else 0
    if rc != 0 or count < 1:
        return ("focused test %s did not pass (rc=%s, ran=%s tests): %s"
                % (test_path, rc, count, (err or out or "")[-1000:]))
    return None


def _promote(args, res, backlog_path, backlog_doc, pool_doc, pool_path, cand, execution_file, reason):
    """Agent-free promotion: append the exact candidate to the pool, then drop the backlog head.

    The pool write happens first so a crash between the two writes can never lose the candidate; the
    retry then finds it consumed and only drops the head.
    """
    family_id = cand["family_id"]
    entry = {key: value for key, value in cand.items() if key != "_body"}
    if execution_file is not None:
        entry["execution_file"] = execution_file
    clash = [c for c in pool_doc["candidates"]
             if isinstance(c, dict) and c.get("fingerprint_input") == entry.get("fingerprint_input")]
    if clash:
        return res.finding("promotion_identity_mismatch",
                           "fingerprint of %s is already used by pool candidate %s; appending would "
                           "make the pool ambiguous" % (family_id, clash[0].get("family_id")),
                           pool_entry=family_id)
    try:
        _write_json_atomic(pool_path, dict(pool_doc, candidates=list(pool_doc["candidates"]) + [entry]))
    except OSError as exc:
        return res.finding("pool_write_failed", str(exc), pool_entry=family_id)
    readback = h._load_json(pool_path)
    tail = readback.get("candidates") if isinstance(readback, dict) else None
    if not isinstance(tail, list) or not tail or tail[-1] != entry:
        return res.finding("promotion_readback_failed",
                           "pool read-back after append does not match the promoted entry",
                           pool_entry=family_id)
    try:
        _write_json_atomic(backlog_path, dict(backlog_doc, candidates=backlog_doc["candidates"][1:]))
    except OSError as exc:
        return res.finding("backlog_write_failed", str(exc), pool_entry=family_id)
    readback = h._load_json(backlog_path)
    if not isinstance(readback, dict) or readback.get("candidates") != backlog_doc["candidates"][1:]:
        return res.finding("promotion_readback_failed",
                           "backlog read-back after head removal does not match", pool_entry=family_id)
    res.detail["execution_file"] = execution_file
    res.action, res.outcome = "promoted", "promoted"
    res.reason = reason
    return res


def _consume(res, backlog_path, backlog_doc, family_id, reason):
    """Retry after a crash: the candidate is already in the pool - only drop the backlog head."""
    try:
        _write_json_atomic(backlog_path, dict(backlog_doc, candidates=backlog_doc["candidates"][1:]))
    except OSError as exc:
        return res.finding("backlog_write_failed", str(exc), pool_entry=family_id)
    readback = h._load_json(backlog_path)
    if not isinstance(readback, dict) or readback.get("candidates") != backlog_doc["candidates"][1:]:
        return res.finding("promotion_readback_failed",
                           "backlog read-back after head removal does not match", pool_entry=family_id)
    res.action, res.outcome = "consumed", "consumed"
    res.reason = reason
    return res


def _promote_clear_absence(args, res, root, backlog_path, backlog_doc, pool_doc, pool_path, cand,
                           outcome_path):
    """Validate the clear-absence outcome, publish the no-compute terminal, promote consumed history."""
    family_id = cand["family_id"]
    try:
        outcome = h._validate_preparation_outcome(outcome_path, cand)
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError) as exc:
        return res.finding("preparation_outcome_invalid", str(exc), pool_entry=family_id,
                           outcome=str(outcome_path))
    family_path = root / family_id
    if os.path.lexists(str(family_path)):
        # Crash retry after the no-compute terminal was already published: identity must still match.
        family_doc = h._load_json(family_path / "family.json")
        if not isinstance(family_doc, dict) or \
                family_doc.get("semantic_fingerprint") != h.fingerprint(cand["fingerprint_input"]):
            return res.finding("promotion_identity_mismatch",
                               "canonical family path %s exists with a different identity" % family_path,
                               pool_entry=family_id)
    else:
        try:
            h._publish_clear_absence_terminal(str(root), cand, outcome)
        except (OSError, ValueError) as exc:
            return res.finding("preparation_outcome_publish_failed", str(exc), pool_entry=family_id)
    res.detail["clear_absence"] = outcome["evidence"]
    return _promote(args, res, backlog_path, backlog_doc, pool_doc, pool_path, cand, None,
                    "clear-absence terminal in place for family %s; candidate appended as consumed "
                    "history" % family_id)


def _promote_prepared(args, res, root, backlog_path, backlog_doc, pool_doc, pool_path, cand,
                      manifest_path):
    """Validate the staged prepared package, run the focused test + P1-P10, then promote."""
    family_id = cand["family_id"]
    if os.path.lexists(str(root / family_id)):
        return res.finding("promotion_state_inconsistent",
                           "canonical family path exists for %s while the candidate is not in the pool; "
                           "refusing to promote" % family_id, pool_entry=family_id)
    staged = dict(cand, execution_file=str(manifest_path))
    prepared, problem = h._prepared_execution(staged, args)
    if problem:
        return res.finding(problem[0], problem[1], pool_entry=family_id,
                           execution_file=str(manifest_path))
    problem = _run_focused_test(prepared)
    if problem:
        return res.finding("focused_test_failed", problem, pool_entry=family_id,
                           execution_file=str(manifest_path))
    problem = h._run_staged_preflight(prepared, family_id)
    if problem:
        return res.finding("prepared_execution_preflight_failed", problem, pool_entry=family_id,
                           execution_file=str(manifest_path))
    current, problem = h._prepared_execution(staged, args)
    if problem or current.get("identity") != prepared.get("identity"):
        key = problem[0] if problem else "prepared_execution_mismatch"
        return res.finding(key,
                           "prepared artifacts changed during preflight%s"
                           % (": %s" % problem[1] if problem else ""),
                           pool_entry=family_id, execution_file=str(manifest_path))
    res.detail["manifest_sha256"] = prepared["manifest_sha256"]
    res.detail["script_path"] = prepared["script_path"]
    return _promote(args, res, backlog_path, backlog_doc, pool_doc, pool_path, cand,
                    str(manifest_path),
                    "prepared execution promoted for family %s (manifest %s)"
                    % (family_id, manifest_path))


def run_once(args):
    res = h.Round()
    root = Path(args.results_root)
    backlog_path = Path(args.backlog) if args.backlog else root / h.HANDOFF_DIRNAME / BACKLOG_FILENAME
    pool_path = Path(args.pool) if args.pool else root / h.HANDOFF_DIRNAME / h.POOL_FILENAME

    backlog_doc = h._load_json(backlog_path)
    if not isinstance(backlog_doc, dict) or not isinstance(backlog_doc.get("candidates"), list):
        return res.finding("backlog_invalid",
                           "preparation backlog has no 'candidates' list: %s" % backlog_path)
    queue = backlog_doc["candidates"]
    if not queue:
        return res.idle("preparation backlog is empty", backlog=str(backlog_path))
    head = queue[0]
    problem = _head_problem(head)
    if problem:
        return res.finding("backlog_invalid", problem)
    cand = dict(head)
    body = h.card_body(backlog_path, cand)
    if not isinstance(body, str) or not body:
        return res.finding("backlog_invalid",
                           "preparation backlog head %s has no readable card body" % cand["family_id"])
    cand["_body"] = body
    family_id = cand["family_id"]
    res.family_id = family_id
    res.detail["backlog_head"] = family_id
    res.detail["backlog_depth"] = len(queue)

    pool_doc = h._load_json(pool_path)
    if not isinstance(pool_doc, dict) or not isinstance(pool_doc.get("candidates"), list):
        return res.finding("pool_invalid", "production pool has no 'candidates' list: %s" % pool_path)

    consumed = [c for c in pool_doc["candidates"]
                if isinstance(c, dict) and c.get("family_id") == family_id]
    if consumed:
        if len(consumed) > 1 or consumed[0].get("fingerprint_input") != cand["fingerprint_input"]:
            return res.finding("promotion_identity_mismatch",
                               "pool already carries family %s with a different identity; refusing to "
                               "drop the backlog head" % family_id, pool_entry=family_id)
        if not consumed[0].get("execution_file") and not (root / family_id).is_dir():
            return res.finding("promotion_state_inconsistent",
                               "pool entry for %s carries neither an execution_file nor a canonical "
                               "family path; refusing to drop the backlog head" % family_id,
                               pool_entry=family_id)
        return _consume(res, backlog_path, backlog_doc, family_id,
                        "candidate %s is already consumed in the pool; backlog head removed" % family_id)

    manifest_path = root / h.HANDOFF_DIRNAME / "prepared" / family_id / h.PREPARED_MANIFEST_FILENAME
    outcome_path = (root / h.HANDOFF_DIRNAME / h.PREPARATION_DIRNAME / family_id /
                    h.PREPARATION_OUTCOME)
    staged = os.path.lexists(str(manifest_path))
    outcome_written = os.path.lexists(str(outcome_path))
    if staged and outcome_written:
        return res.finding("preparation_state_ambiguous",
                           "candidate %s carries both a staged prepared package and a clear-absence "
                           "outcome; refusing to promote" % family_id, pool_entry=family_id)
    if outcome_written:
        return _promote_clear_absence(args, res, root, backlog_path, backlog_doc, pool_doc, pool_path,
                                      cand, outcome_path)
    if staged:
        return _promote_prepared(args, res, root, backlog_path, backlog_doc, pool_doc, pool_path, cand,
                                 manifest_path)
    if os.path.lexists(str(root / family_id)):
        return res.finding("promotion_state_inconsistent",
                           "canonical family path exists for %s while the candidate is not in the pool; "
                           "refusing to launch preparation" % family_id, pool_entry=family_id)

    status, problem = _load_preparation_status(root, family_id)
    if problem:
        return res.finding("preparation_status_invalid", problem, pool_entry=family_id)

    lease_dir = root / h.HANDOFF_DIRNAME / h.PREPARATION_DIRNAME / family_id
    if status is not None and status["state"] == "running":
        try:
            fd = h._lock(lease_dir / h.AGENT_LOCK)
        except OSError as exc:
            return res.finding("preparation_lease_probe_failed", str(exc), pool_entry=family_id)
        if fd is None:
            return res.waiting("quant-preparation still owns candidate %s; preparation waits" % family_id,
                               preparation_source=h.PREPARATION_SOURCE)
        os.close(fd)
        return _defer_head(
            res, root, backlog_path, backlog_doc, family_id,
            "quant-preparation for candidate %s exited without a staged package or clear-absence "
            "outcome; candidate preserved and deferred for fair retry" % family_id)

    try:
        _write_preparation_status(root, family_id, "running",
                                  "candidate selected for quant-preparation retry")
    except OSError as exc:
        return res.finding("preparation_status_write_failed", str(exc), pool_entry=family_id)

    pid, why, busy = h.launch_preparation_agent(str(root), cand, str(backlog_path))
    if busy:
        return res.waiting("quant-preparation still owns candidate %s; preparation waits" % family_id,
                           preparation_source=h.PREPARATION_SOURCE)
    if why:
        return _defer_head(
            res, root, backlog_path, backlog_doc, family_id,
            "%s; candidate preserved and deferred for fair retry" % why)
    try:
        _write_preparation_status(root, family_id, "running",
                                  "quant-preparation launched; awaiting staged package or outcome",
                                  pid=pid)
    except OSError as exc:
        return res.finding("preparation_status_write_failed",
                           "session launched but status update failed: %s" % exc,
                           pool_entry=family_id, preparation_pid=pid)
    return res.waiting("quant-preparation launched for candidate %s (pid=%s); the next run validates "
                       "its staged package" % (family_id, pid),
                       preparation_required=True, preparation_pid=pid,
                       preparation_source=h.PREPARATION_SOURCE)


def run(args):
    """Serialize with C3 and other preparation runs on the same kernel lease."""
    root = Path(args.results_root)
    try:
        lock_dir = root / h.HANDOFF_DIRNAME
        lock_dir.mkdir(exist_ok=True)
        fd = h._lock(lock_dir / ".advance.lock")
    except OSError as exc:
        return h.Round().finding("advance_lock_failed", str(exc))
    if fd is None:
        return h.Round().waiting("another C3 advance/preparation run holds the handoff lease")
    try:
        return run_once(args)
    finally:
        os.close(fd)


def main():
    ap = argparse.ArgumentParser(
        description="contract 14.4 deterministic preparation entrypoint (one backlog head)")
    ap.add_argument("--results-root", default=os.environ.get("QLIB_RESULTS_ROOT", h.DEFAULT_RESULTS))
    ap.add_argument("--backlog", default=None,
                    help="preparation backlog JSON (default <results>/_handoff/%s)" % BACKLOG_FILENAME)
    ap.add_argument("--pool", default=None,
                    help="production candidate pool JSON (default <results>/_handoff/%s)"
                    % h.POOL_FILENAME)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not os.path.isdir(args.results_root):
        sys.stderr.write("usage error: results root not found: %s\n" % args.results_root)
        return 2
    res = run(args)
    record = {"schema_version": 1, "contract_section": "14.4", "ran_at_utc": h.now_utc(),
              "results_root": args.results_root, "decision_evidence": "results_root_only"}
    record.update(res.as_dict())
    if args.json:
        print(json.dumps(record, indent=2, ensure_ascii=False))
    elif res.action in ("promoted", "consumed", "deferred"):
        print("prepare candidate: %s — %s" % (res.action, res.reason))
    sys.stderr.write("prepare candidate: outcome=%s action=%s — %s\n"
                     % (res.outcome, res.action, res.reason))
    return 0


if __name__ == "__main__":
    sys.exit(main())
