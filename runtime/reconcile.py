#!/usr/bin/env python3
"""Completion bridge (no-agent reconciler) for the quant runtime pipeline.

Contract: .../QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md sections 9.4, 12.2, 12.3, 12.6, 11.
Only legal action: `scheduled -> ready` (kernel `unblock`), taken **only** after the contract
verification list passes for an *unconsumed* terminal sentinel. Anything conflicting is fail-closed:
no unblock, no block, an append-only incident line, and a card comment.

Second, narrower entry point (contract 9.4 v1.9.0): an authoritative attempt whose `state.json`
stage is compute-finished (`ARTIFACT_READY` / `FAILED_SCRIPT`) has no terminal sentinel yet - the
container's compute phase stopped, and the sentinel is published host-side afterwards by default
(§9.2 step 6). A parked card in that state would otherwise wait forever, so the same
`scheduled -> ready` unblock wakes default to dispose of it. That is still not a verdict and not a
terminal: this path writes no terminal file, no artifact, no incident of its own; default decides
DONE / retry / INCOMPLETE host-side.

Only the round's **authoritative current attempt** (contract 9.4 v1.7.1) may ever drive a Kanban
transition: the valid `run-spec.json` identity with the greatest (`created_at_utc`, `uN` ordinal).
An older attempt is *superseded* - its sentinel stays readable provenance, but it is a descriptive
no-op even when it is terminal, because a newer attempt of the same round exists. Ordering never
comes from the run_id string (v1.7.1: `u10` > `u9`). When the newest attempt of a round cannot be
ordered deterministically (missing/malformed/ambiguous run-spec identity, conflicting task
ownership) the round fails closed: incident, no action, and **no** fallback to an older terminal.

This is not a daemon: run it manually or from a no_agent cron / one-shot invocation.
Exit codes: 0 = no incident, 3 = incidents recorded (human needed), 2 = usage error.

Examples:
    python3 runtime/reconcile.py --dry-run --json          # read-only report over /results
    python3 runtime/reconcile.py                           # apply (unblock what verifies)
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from terminal_evidence import TERMINALS, host_boot_id, now_utc, sha256_file  # noqa: E402

DEFAULT_RESULTS = "/Volumes/ExpansionDrive/qlib-results"
INCIDENT_DIRNAME = "_incidents"
INCIDENT_FILE = "reconciliation_incident.jsonl"
# Compute-finished stages (contract 9.4 v1.9.0): the container runner writes state.json
# stage=ARTIFACT_READY - or FAILED_SCRIPT when the script itself raised - and exits. Neither is a
# verdict: the terminal sentinel is published host-side by default (§9.2 step 6).
COMPUTE_FINISHED_STAGES = ("ARTIFACT_READY", "FAILED_SCRIPT")


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


def terminal_identity(attempt):
    """(task_id, board) taken from the attempt's terminal evidence, else (None, None).

    Contract 9.4 scan scope: consumption is decided *before* anything is validated, so this must be
    cheap and unambiguous. Every terminal file must parse, carry a non-empty `task_id`, and they must
    all agree on it; anything else is undecidable here and stays fail-closed (validate() records it).
    """
    ids, boards = set(), set()
    for name in TERMINALS:
        path = attempt / name
        if not path.exists():
            continue
        try:
            doc = json.loads(path.read_text())
        except ValueError:
            return None, None
        if not doc.get("task_id"):
            return None, None
        ids.add(doc["task_id"])
        boards.add(doc.get("kanban_board") or "")
    if len(ids) != 1:
        return None, None
    return ids.pop(), (boards.pop() if len(boards) == 1 else None)


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


def discover_rounds(results_root):
    """<root>/<family_id>/rounds/<round_id>/attempts/<run_id>/ -> [(family_id, round_id, [dirs])]

    The round is the selection unit (contract 9.4 v1.7.1): attempts are only ever compared with
    attempts of the same family + round, never across rounds/families.
    """
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
            found = [a for a in sorted(attempts.iterdir()) if a.is_dir()]
            if found:
                out.append((family.name, rnd.name, found))
    return out


def discover_attempts(results_root):
    out = []
    for _family_id, _round_id, attempts in discover_rounds(results_root):
        out.extend(attempts)
    return out


ORDINAL = re.compile(r"u(\d+)$")


class Attempt(object):
    """One attempt dir plus the durable identity/ordering metadata used for round selection.

    `run-spec.json` is the ordering authority (it is published before compute, contract 9.2 step 1):
    `created_at_utc` is primary, the `uN` run ordinal only a deterministic tie-break, so the
    run_id string itself can never decide which attempt is current (`u10` > `u9`). Any missing or
    ambiguous piece lands in `problems` and makes the round undecidable -> fail closed.
    """

    def __init__(self, path, family_id, round_id, run_id):
        self.path = Path(path)
        self.family_id = family_id
        self.round_id = round_id
        self.run_id = run_id
        self.task_id = None
        self.kanban_board = None
        self.created_at = None
        self.ordinal = None
        self.problems = []


def parse_utc(value):
    """ISO-8601 UTC timestamp -> aware datetime; None when missing/unparsable."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        stamp = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=datetime.timezone.utc)
    return stamp.astimezone(datetime.timezone.utc)


def attempt_metadata(path, family_id, round_id, run_id):
    """Read one attempt's durable ordering identity; non-empty `problems` => undecidable."""
    rec = Attempt(path, family_id, round_id, run_id)
    spec = rec.path / "run-spec.json"
    if not spec.is_file():
        rec.problems.append("missing run-spec.json")
        return rec
    try:
        doc = json.loads(spec.read_text())
    except ValueError as exc:
        rec.problems.append("run-spec.json not valid JSON: %s" % exc)
        return rec
    for key, want in (("family_id", family_id), ("round_id", round_id), ("run_id", run_id)):
        if doc.get(key) != want:
            rec.problems.append("run-spec.json %s=%r != path %r" % (key, doc.get(key), want))
    task_id = doc.get("task_id")
    if isinstance(task_id, str) and task_id:
        rec.task_id = task_id
    else:
        rec.problems.append("run-spec.json task_id is not a non-empty string (%r)" % (task_id,))
    board = doc.get("kanban_board")
    if isinstance(board, str) and board:
        rec.kanban_board = board
    else:
        rec.problems.append("run-spec.json kanban_board is not a non-empty string (%r)" % (board,))
    rec.created_at = parse_utc(doc.get("created_at_utc"))
    if rec.created_at is None:
        rec.problems.append("run-spec.json created_at_utc missing/unparsable (%r)"
                            % (doc.get("created_at_utc"),))
    match = ORDINAL.search(run_id)
    rec.ordinal = int(match.group(1)) if match else None
    return rec


def select_authoritative(records):
    """(authoritative, superseded, problem) for one round's attempts.

    Contract 9.4 (v1.7.1): exactly one attempt per round is the authoritative *current* attempt;
    every older one is superseded (readable provenance, descriptive no-op). Undecidable ordering -
    broken identity, conflicting task ownership, equal timestamps without a deterministic uN
    tie-break - returns a problem instead of a guess, so the caller fails closed and never falls
    back to an older terminal.
    """
    if len(records) < 2:
        return records[0], [], None
    broken = [r for r in records if r.problems]
    if broken:
        return None, [], "; ".join("%s: %s" % (r.run_id, ", ".join(r.problems)) for r in broken)
    owners = sorted(set((r.task_id, r.kanban_board) for r in records))
    if len(owners) > 1:
        return None, [], "conflicting task ownership inside one round: %r" % (owners,)

    def order_key(rec):
        return (rec.created_at, -1 if rec.ordinal is None else rec.ordinal, rec.run_id)

    ordered = sorted(records, key=order_key)
    for older, newer in zip(ordered, ordered[1:]):
        if older.created_at != newer.created_at:
            continue
        if older.ordinal is None or newer.ordinal is None or older.ordinal == newer.ordinal:
            return None, [], ("attempts %s and %s share created_at_utc=%s without a deterministic "
                              "uN tie-break" % (older.run_id, newer.run_id,
                                                older.created_at.isoformat()))
    return ordered[-1], ordered[:-1], None


def superseded_result(rec, authoritative):
    res = Result(rec.path, rec.family_id, rec.round_id, rec.run_id)
    res.action = "superseded"
    res.reason = ("superseded by newer attempt %s (authoritative current attempt, created_at_utc=%s); "
                  "read-only no-op, no card mutation (contract 9.4 v1.7.1)"
                  % (authoritative.run_id, authoritative.created_at.isoformat()))
    res.task_id = rec.task_id
    res.detail["authoritative_run_id"] = authoritative.run_id
    res.detail["terminal_files"] = [t for t in TERMINALS if (rec.path / t).exists()]
    return res


def round_task_ids(records):
    """Distinct task ids visible for a round (run-specs + parsable terminal sentinels)."""
    ids = set(r.task_id for r in records if r.task_id)
    for rec in records:
        task_id, _board = terminal_identity(rec.path)
        if task_id:
            ids.add(task_id)
    return sorted(ids)


def handle_ambiguous_round(records, problem, results_root, board, dry_run, detector):
    """Fail closed: no attempt of an unorderable round may act (contract 12.6).

    Consumption still short-circuits incidents (contract 9.4 scan scope): a round whose card is no
    longer `scheduled` is a no-op, so a historical malformed round never accumulates incidents on
    every run. Everything else becomes an incident - never a release, never a fallback.
    """
    ids = round_task_ids(records)
    boards = [r.kanban_board for r in records if r.kanban_board]
    subject = next((r for r in records if r.problems), records[-1])
    res = Result(subject.path, subject.family_id, subject.round_id, subject.run_id)
    res.detail["round_attempts"] = [r.run_id for r in records]
    res.detail["ambiguity"] = problem
    res.task_id = ids[0] if len(ids) == 1 else None
    if len(ids) == 1:
        status, why = card_status(boards[0] if boards else board, ids[0])
        if status is not None:
            res.status_before = status
            if status != "scheduled":
                res.action = "consumed"
                res.reason = ("round is ambiguous (%s) but card status=%s is not scheduled -> "
                              "consumed, no incident" % (problem, status))
                return res
        else:
            res.detail["kanban_readback"] = why
    return fail(res, results_root, "attempt_selection_ambiguous", detector, dry_run,
                [str(r.path) for r in records], board)


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

    # mapping (contract 9.4 item 1, 10.1, 10.2, 10.6): path <-> sentinel <-> family.json <->
    # round-spec.json <-> run-spec.json must all carry the same identity - string equality, no
    # normalisation. family.json owns family_id, round-spec owns family_id/round_id, run-spec owns
    # the full family/round/run/task identity, and the container identity is cross-checked where the
    # existing schema carries it (live `container ls` identity stays preflight P5/P6's job).
    problems = []
    task_id = sentinel.get("task_id")
    if sentinel.get("family_id") != res.family_id:
        problems.append("family_id sentinel=%r path=%r" % (sentinel.get("family_id"), res.family_id))
    if sentinel.get("round_id") != res.round_id:
        problems.append("round_id sentinel=%r path=%r" % (sentinel.get("round_id"), res.round_id))
    if sentinel.get("run_id") != res.run_id:
        problems.append("run_id sentinel=%r path=%r" % (sentinel.get("run_id"), res.run_id))
    if not sentinel.get("container_id"):
        problems.append("container_id missing in sentinel (contract 9.4 item 4)")

    expected = {"family_id": res.family_id, "round_id": res.round_id, "run_id": res.run_id,
                "task_id": task_id, "kanban_task_id": task_id,
                "kanban_board": sentinel.get("kanban_board")}
    family_json = Path(results_root) / res.family_id / "family.json"
    round_spec = Path(results_root) / res.family_id / "rounds" / res.round_id / "round-spec.json"
    artifacts = (
        (family_json, "family.json", ("family_id", "kanban_task_id", "kanban_board")),
        (round_spec, "round-spec.json", ("family_id", "round_id", "kanban_task_id", "kanban_board")),
        (attempt / "run-spec.json", "run-spec.json",
         ("family_id", "round_id", "run_id", "task_id", "kanban_board")),
    )
    evidence = [str(attempt / name), str(family_json), str(round_spec), str(attempt / "run-spec.json")]
    for path, label, keys in artifacts:
        if not path.is_file():
            problems.append("missing %s" % path)
            continue
        try:
            doc = json.loads(path.read_text())
        except ValueError as exc:
            problems.append("%s not valid JSON: %s" % (label, exc))
            continue
        for key in keys:
            if doc.get(key) != expected[key]:
                problems.append("%s %s=%r != expected %r" % (label, key, doc.get(key), expected[key]))
        if label == "run-spec.json":
            for key in ("container_id", "image_id"):
                if key in doc and doc[key] != sentinel.get(key):
                    problems.append("run-spec.json %s=%r != sentinel %r" % (key, doc[key], sentinel.get(key)))
    if problems:
        res.detail["mapping_problems"] = problems
        fail(res, results_root, "mapping_mismatch", detector, dry_run, evidence, board)
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


def wake_default(res, spec_doc, results_root, board, dry_run, detector):
    """Compute-finished stage, no terminal sentinel -> wake default (contract 9.4 v1.9.0).

    `ARTIFACT_READY` / `FAILED_SCRIPT` only says the container's compute phase stopped; the terminal
    sentinel is still published host-side by default (§9.2 step 6), which then decides DONE / retry /
    INCOMPLETE (contract 12.2). So this path writes no terminal file, no artifact, no verdict and no
    incident of its own - the only legal action is the existing `scheduled -> ready` unblock.
    Anything it cannot verify stays fail-closed to the caller's descriptive orphan report: no
    non-empty run-spec `task_id`/`kanban_board`, unreadable card read-back, card not `scheduled`.
    An issued-but-unconfirmed board operation (unblock rc != 0, read-back not `ready`/`todo`) is the
    existing invariant_break incident, exactly as in the terminal path.
    """
    task_id = spec_doc.get("task_id")
    spec_board = spec_doc.get("kanban_board")
    if not (isinstance(task_id, str) and task_id and isinstance(spec_board, str) and spec_board):
        res.detail["wake"] = "run-spec task_id/kanban_board unreadable -> fail-closed, no wake"
        return None
    res.task_id = task_id
    board = spec_board or board
    status, why = card_status(board, task_id)
    if status is None:
        res.detail["kanban_readback"] = why
        res.detail["wake"] = "card read-back failed -> fail-closed, no wake"
        return None
    res.status_before = status
    if status != "scheduled":
        res.detail["wake"] = "card status=%s is not scheduled -> no wake" % status
        return None
    stage = res.detail.get("stage")
    if dry_run:
        res.action = "would_unblock"
        res.reason = ("compute finished (stage=%s, no terminal sentinel) and card is scheduled -> "
                      "would wake default for host-side disposition" % stage)
        return res

    rc, out, err = sh(["hermes", "kanban", "--board", board, "unblock", res.task_id,
                       "--reason", "reconciler: compute finished (stage=%s) for %s; default decides "
                                   "DONE/retry/INCOMPLETE host-side (contract 9.4 v1.9.0)"
                                   % (stage, res.run_id)])
    if rc != 0:
        res.detail["unblock_stderr"] = (err or out).strip()[:200]
        return fail(res, results_root, "invariant_break", detector, dry_run, [str(res.attempt_dir)], board)
    status_after, why2 = card_status(board, res.task_id)
    res.status_after = status_after
    if status_after not in ("ready", "todo"):
        res.detail["kanban_readback_after"] = why2
        return fail(res, results_root, "invariant_break", detector, dry_run, [str(res.attempt_dir)], board)
    res.action = "unblocked"
    res.reason = ("compute finished (stage=%s), no terminal sentinel; card %s -> %s (DB read-back); "
                  "default decides DONE/retry/INCOMPLETE host-side" % (stage, status, status_after))
    return res


def handle(res, results_root, board, dry_run, detector):
    attempt = Path(res.attempt_dir)
    terminals = [t for t in TERMINALS if (attempt / t).exists()]

    if not terminals:
        # Orphan candidate (contract 12.2 item 2/4): report only, default decides. One exception
        # (9.4 v1.9.0): a compute-finished stage means compute is over while the card is still
        # parked - the missing wake of the automatic completion loop - so default is woken instead;
        # every unverifiable case keeps the descriptive report below.
        state = attempt / "state.json"
        stage = None
        if state.is_file():
            try:
                stage = json.loads(state.read_text()).get("stage")
            except ValueError:
                stage = "unparsable"
        spec = attempt / "run-spec.json"
        spec_doc = {}
        if spec.is_file():
            try:
                spec_doc = json.loads(spec.read_text())
            except ValueError:
                spec_doc = {}
        res.detail["stage"] = stage
        res.detail["has_run_spec"] = spec.is_file()
        res.task_id = spec_doc.get("task_id")
        if stage in COMPUTE_FINISHED_STAGES:
            woke = wake_default(res, spec_doc, results_root, board, dry_run, detector)
            if woke is not None:
                return woke
        res.action = "orphan_candidate"
        res.reason = ("no terminal sentinel; stage=%s -> contract 12.2: host/default publishes "
                      "INCOMPLETE via runtime/terminal_evidence.py, then reconcile again" % stage)
        return res

    # Consumption FIRST (contract 9.4 scan scope): a sentinel whose card is no longer `scheduled` is
    # consumed -> no action, no incident, no comment. Validation/incident generation must not run for
    # it, otherwise every historical sentinel turns into a stale_sentinel incident after a host reboot
    # (and re-appends on every run). An undecidable task_id is not consumed here and stays fail-closed.
    task_id, sentinel_board = terminal_identity(attempt)
    if task_id:
        status, why = card_status(sentinel_board or board, task_id)
        res.task_id = task_id
        if status is not None:
            res.status_before = status
            if status != "scheduled":
                res.action = "consumed"
                res.reason = "sentinel already consumed (card status=%s); no action, idempotent" % status
                return res
        else:
            res.detail["kanban_readback"] = why

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
    for family_id, round_id, attempts in discover_rounds(args.results_root):
        records = [attempt_metadata(attempt, family_id, round_id, attempt.name)
                   for attempt in attempts]
        authoritative, superseded, problem = select_authoritative(records)
        if problem:
            results.append(handle_ambiguous_round(records, problem, args.results_root, args.board,
                                                  args.dry_run, args.detector))
            continue
        for rec in superseded:
            results.append(superseded_result(rec, authoritative))
        res = Result(authoritative.path, family_id, round_id, authoritative.run_id)
        res.detail["terminal_files"] = [t for t in TERMINALS if (authoritative.path / t).exists()]
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
        "superseded": [{"run_id": r.run_id, "superseded_by": r.detail.get("authoritative_run_id"),
                        "task_id": r.task_id} for r in results if r.action == "superseded"],
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
