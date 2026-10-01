#!/usr/bin/env python3
"""C4 one-shot reconciler for direct Hermes quant families (no Kanban transport).

Only the newest valid attempt of a round may wake the default agent. Historical
Kanban-owned families remain immutable and are not operated by this path.

Completion/wake conditions are derived from results-root evidence only. A narrowly validated
`ARTIFACT_READY` zero-survivor REJECT is closed mechanically with the existing terminal writer plus
an exclusive round verdict; other compute-finished states retain the existing Hermes disposition path.
A validated terminal without its round verdict (including prior-boot recovery), and an attempt that stopped
writing outside the 90-minute stall window while still carrying no terminal sentinel and no
verdict of its own round (the agent died before Qlib started, or Qlib died mid-run). The stall
condition keeps a dead attempt from holding the pipeline silently: inside the window the
attempt is live compute and stays a descriptive `orphan_candidate`; outside it the same
disposition agent decides the round - terminate it or retry it - so the family is released
either way. A failed wake becomes a recorded fail-closed incident instead of retrying
invisibly; the family lease and the frozen prompt keep cadence after cadence duplicate-free.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from production_handoff import (ACTIVE_WINDOW_MINUTES, AGENT_LOCK, DISPOSITION_PROMPT_VERSION,  # noqa: E402
                                _lock, attempt_activity, direct_family, disposition_prompt, launch_agent,
                                round_verdict_token)
from terminal_evidence import TERMINALS, host_boot_id, now_utc, sha256_file  # noqa: E402

DEFAULT_RESULTS = "/Volumes/ExpansionDrive/qlib-results"
INCIDENT_FILE = "_incidents/reconciliation_incident.jsonl"
COMPUTE_FINISHED_STAGES = ("ARTIFACT_READY", "FAILED_SCRIPT")
ORDINAL = re.compile(r"u(\d+)$")
MECHANICAL_REJECT_DISPOSITIONS = (None, "NO_SURVIVOR", "REJECT / NO_SURVIVOR")


def load(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError, UnicodeError):
        return None


def discover_rounds(results_root):
    root = Path(results_root)
    for family in sorted(root.iterdir()):
        if not family.is_dir() or family.name.startswith("_"):
            continue
        for rnd in sorted((family / "rounds").glob("*")):
            if rnd.is_dir():
                attempts = sorted(a for a in (rnd / "attempts").glob("*") if a.is_dir())
                if attempts:
                    yield family.name, rnd.name, attempts


def discover_attempts(results_root):
    return [a for _f, _r, attempts in discover_rounds(results_root) for a in attempts]


def parse_utc(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        stamp = datetime.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=datetime.timezone.utc)
    return stamp.astimezone(datetime.timezone.utc)


class Attempt:
    def __init__(self, path, family_id, round_id, require_timestamp=True):
        self.path = Path(path)
        self.family_id = family_id
        self.round_id = round_id
        self.run_id = self.path.name
        self.created_at = None
        match = ORDINAL.search(self.run_id)
        self.ordinal = int(match.group(1)) if match else None
        self.problems = []
        doc = load(self.path / "run-spec.json")
        if not isinstance(doc, dict):
            self.problems.append("run-spec.json missing or unreadable")
            return
        for key, expected in (("family_id", family_id), ("round_id", round_id),
                              ("run_id", self.run_id)):
            if doc.get(key) != expected:
                self.problems.append("run-spec.json %s does not match path" % key)
        family = load(self.path.parents[3] / "family.json")
        if direct_family(family):
            if any(key in doc for key in ("task_id", "kanban_board", "kanban_task_id")):
                self.problems.append("direct run-spec.json has Kanban ownership")
        elif not isinstance(doc.get("task_id"), str) or not doc["task_id"] or \
                not isinstance(doc.get("kanban_board"), str) or not doc["kanban_board"]:
            self.problems.append("historical run-spec.json missing card ownership")
        self.created_at = parse_utc(doc.get("created_at_utc"))
        if require_timestamp and self.created_at is None:
            self.problems.append("run-spec.json created_at_utc missing/unparsable")


def attempt_metadata(path, family_id, round_id, run_id):
    """One attempt's durable ordering identity for the installed quant_runtime_watchdog.py.

    The watchdog's W2/W3 lane calls `discover_rounds` -> `attempt_metadata` -> `select_authoritative`
    and consumes `problems` / `created_at` / `ordinal` / `path` / `run_id`, so this adapter must keep
    that surface. Identity and ownership are judged by this module's own `Attempt` - one rule per
    family kind (a direct family's run-spec carries no Kanban ownership, a historical one carries
    both ids) - and non-empty `problems` still means the round is undecidable. No new state, no
    second registry.
    """
    rec = Attempt(path, family_id, round_id, require_timestamp=False)
    if rec.run_id != run_id:
        rec.problems.append("run_id %r does not match attempt dir %r" % (run_id, rec.run_id))
    return rec


def stalled_minutes(attempt):
    """Minutes since the newest write inside one attempt, or None when it has no readable stamp.

    The same objective liveness signal the handoff guard and the watchdog use: mtime only, nothing
    executed, no content parsed. `None` (nothing readable) is deliberately not treated as stalled -
    an attempt tree without readable evidence is the handoff retry path's business, so C4 stays
    descriptive and can never spawn a second worker from an unreadable tree.
    """
    stamp = attempt_activity(attempt)
    return None if stamp is None else (time.time() - stamp) / 60.0


def select_authoritative(records):
    broken = [r for r in records if r.problems]
    if broken:
        return None, [], "; ".join("%s: %s" % (r.run_id, ", ".join(r.problems)) for r in broken)
    if len(records) > 1:
        missing_order = [r for r in records if r.created_at is None]
        if missing_order:
            return None, [], "; ".join(
                "%s: run-spec.json created_at_utc missing/unparsable" % r.run_id
                for r in missing_order)
    oldest = datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
    ordered = sorted(records, key=lambda r: (r.created_at or oldest,
                                             -1 if r.ordinal is None else r.ordinal))
    for older, newer in zip(ordered, ordered[1:]):
        if older.created_at == newer.created_at and (older.ordinal is None or newer.ordinal is None or
                                                      older.ordinal == newer.ordinal):
            return None, [], "timestamp tie without distinct uN ordinals"
    return ordered[-1], ordered[:-1], None


class Result:
    def __init__(self, rec):
        self.attempt_dir = str(rec.path)
        self.family_id = rec.family_id
        self.round_id = rec.round_id
        self.run_id = rec.run_id
        self.action = None
        self.reason = None
        self.detail = {}
        self.incident = None

    def as_dict(self):
        result = {"attempt_dir": self.attempt_dir, "family_id": self.family_id,
                  "round_id": self.round_id, "run_id": self.run_id,
                  "action": self.action, "reason": self.reason}
        if self.detail:
            result["detail"] = self.detail
        if self.incident:
            result["incident"] = self.incident
        return result


def fail(res, results_root, kind, detector, dry_run, evidence):
    res.action, res.reason = "incident", kind
    if dry_run:
        return res
    path = Path(results_root) / INCIDENT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    # ponytail: scan one append-only ledger instead of introducing a second incident registry.
    # If this ever grows large, compact/report it offline; never make C4 a daemon.
    for line in path.read_text().splitlines() if path.exists() else ():
        try:
            old = json.loads(line)
            if (old.get("family_id"), old.get("round_id"), old.get("run_id"), old.get("kind")) == (
                    res.family_id, res.round_id, res.run_id, kind):
                res.incident = str(path)
                return res
        except ValueError:
            pass
    record = {"schema_version": 1,
              "incident_id": "inc-" + hashlib.sha256((res.attempt_dir + "|" + kind).encode()).hexdigest()[:16],
              "detected_at_utc": now_utc(), "detector": detector, "family_id": res.family_id,
              "round_id": res.round_id, "run_id": res.run_id, "kind": kind,
              "evidence_paths": evidence, "host_boot_id": host_boot_id()}
    with path.open("a") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    res.incident = str(path)
    return res


def mapping_problems(res, results_root, terminal=None):
    """Cross-check frozen family/round/run identity; never trust a sentinel alone."""
    root = Path(results_root) / res.family_id
    attempt = Path(res.attempt_dir)
    family, rnd, run = (load(root / "family.json"),
                        load(root / "rounds" / res.round_id / "round-spec.json"),
                        load(attempt / "run-spec.json"))
    problems = []
    for label, doc, expected in (("family.json", family, {"family_id": res.family_id}),
                                 ("round-spec.json", rnd, {"family_id": res.family_id,
                                                           "round_id": res.round_id}),
                                 ("run-spec.json", run, {"family_id": res.family_id,
                                                         "round_id": res.round_id,
                                                         "run_id": res.run_id})):
        if not isinstance(doc, dict):
            problems.append("missing/unreadable " + label)
            continue
        for key, want in expected.items():
            if doc.get(key) != want:
                problems.append("%s %s mismatch" % (label, key))
        if any(key in doc for key in ("task_id", "kanban_task_id", "kanban_board")):
            problems.append(label + " has Kanban ownership")
    if not direct_family(family):
        problems.append("family.json is not direct Hermes execution")
    if terminal:
        for key, want in (("family_id", res.family_id), ("round_id", res.round_id),
                          ("run_id", res.run_id)):
            if terminal.get(key) != want:
                problems.append("terminal %s mismatch" % key)
        if not terminal.get("container_id"):
            problems.append("terminal container_id missing")
        if isinstance(run, dict):
            for key in ("container_id", "image_id"):
                if key in run and run[key] != terminal.get(key):
                    problems.append("run-spec.json %s mismatch" % key)
        if any(key in terminal for key in ("task_id", "kanban_task_id", "kanban_board")):
            problems.append("terminal has Kanban ownership")
    return problems


def validate_terminal(res, root, dry_run, detector):
    attempt = Path(res.attempt_dir)
    names = [t for t in TERMINALS if (attempt / t).exists()]
    if len(names) != 1:
        return fail(res, root, "multiple_terminal", detector, dry_run,
                    [str(attempt / t) for t in names])
    name = names[0]
    sentinel = load(attempt / name)
    if not isinstance(sentinel, dict) or sentinel.get("status") != name:
        return fail(res, root, "sentinel_ambiguous", detector, dry_run, [str(attempt / name)])
    problems = mapping_problems(res, root, sentinel)
    if problems:
        res.detail["mapping_problems"] = problems
        return fail(res, root, "mapping_mismatch", detector, dry_run, [str(attempt / name)])
    manifest = sentinel.get("artifact_manifest")
    checksums = sentinel.get("artifact_checksums")
    if not isinstance(manifest, list) or not isinstance(checksums, dict):
        return fail(res, root, "checksum_mismatch", detector, dry_run, [str(attempt / name)])
    bad = []
    for rel in manifest:
        if not isinstance(rel, str) or not rel or Path(rel).is_absolute():
            bad.append("invalid manifest path")
            continue
        full = (attempt / rel).resolve()
        if not full.is_relative_to(attempt.resolve()) or not full.is_file():
            bad.append("missing/unsafe: %s" % rel)
        elif checksums.get(rel) != sha256_file(str(full)):
            bad.append("checksum mismatch: %s" % rel)
    if bad:
        res.detail["bad_artifacts"] = bad
        return fail(res, root, "checksum_mismatch", detector, dry_run, [str(attempt / name)])
    boot = sentinel.get("host_boot_id")
    current_boot = host_boot_id()
    boot_valid = isinstance(boot, str) and bool(boot.strip()) and boot != "boot-unknown"
    current_boot_valid = isinstance(current_boot, str) and bool(current_boot.strip()) and \
        current_boot != "boot-unknown"
    if not boot_valid or not current_boot_valid:
        return fail(res, root, "stale_sentinel", detector, dry_run, [str(attempt / name)])
    if boot != current_boot:
        fail(res, root, "stale_sentinel", detector, dry_run, [str(attempt / name)])
        # Identity, unique terminal and artifact checks passed: retain the incident provenance,
        # but let the existing disposition agent re-validate this prior-boot completion. C4 never
        # rewrites the sentinel, concludes the round, or releases C3's prepared one-shot hold.
        res.detail["boot_recovery"] = {"sentinel_host_boot_id": boot,
                                       "current_host_boot_id": current_boot}
    return None


def _mechanical_reject_payload(res, results_root):
    """Return validated deterministic REJECT evidence for one ARTIFACT_READY direct attempt.

    This path is intentionally narrow: it only accepts a completed zero-survivor result whose
    registered coverage and selector/disposition identities are internally consistent. Anything
    incomplete, PASS-like, technical, malformed or scientifically ambiguous stays on the existing
    Hermes disposition path.
    """
    attempt = Path(res.attempt_dir)
    root = Path(results_root)
    try:
        root_real = root.resolve(strict=True)
        relative_attempt = attempt.relative_to(root)
    except (OSError, ValueError):
        return None, "attempt path is outside/unresolvable from results root"
    current = root
    if current.is_symlink():
        return None, "results root must not be a symlink"
    for part in relative_attempt.parts:
        current = current / part
        if current.is_symlink():
            return None, "attempt path contains symlink: %s" % current
    try:
        attempt_real = attempt.resolve(strict=True)
        attempt_real.relative_to(root_real)
    except (OSError, ValueError):
        return None, "attempt path escapes results root"
    run_spec = load(attempt / "run-spec.json")
    result = load(attempt / "result.json")
    if not isinstance(run_spec, dict) or not isinstance(result, dict):
        return None, "run-spec/result missing or unreadable"
    for key, want in (("family_id", res.family_id), ("round_id", res.round_id),
                      ("run_id", res.run_id)):
        if run_spec.get(key) != want or result.get(key) != want:
            return None, "%s identity mismatch" % key
    if result.get("status") != "ARTIFACT_READY" or result.get("coverage_complete") is not True:
        return None, "result is not completed coverage"
    expected = result.get("expected_case_evaluations")
    actual = result.get("case_evaluations_total")
    registered = run_spec.get("expected") if isinstance(run_spec.get("expected"), dict) else {}
    registered_expected = registered.get("expected_case_evaluations")
    if registered_expected is None:
        registered_expected = registered.get("case_evaluations_total")
    if type(expected) is not int or expected <= 0 or actual != expected or registered_expected != expected:
        return None, "coverage count mismatch"
    if result.get("cohort_survivor_count") != 0 or result.get("cohort_survivors") != []:
        return None, "result is not zero-survivor"
    if result.get("verdict_recommendation") != "REJECT":
        return None, "verdict recommendation is not REJECT"
    if result.get("performance_claimable") is True:
        return None, "REJECT result cannot be performance-claimable"
    if result.get("assertions_all_true") is not True:
        return None, "assertions are not all true"
    if result.get("disposition") not in MECHANICAL_REJECT_DISPOSITIONS:
        return None, "disposition is not a recognized no-survivor form"
    for key in ("selector_version", "disposition_version"):
        value = result.get(key)
        if not isinstance(value, str) or not value or run_spec.get(key) != value:
            return None, "%s mismatch" % key
    if isinstance(result.get("coverage_cell_problems"), list) and result["coverage_cell_problems"]:
        return None, "coverage cell problems present"
    outputs = run_spec.get("expected_outputs")
    if not isinstance(outputs, list) or not outputs:
        return None, "run-spec expected_outputs missing"
    manifest = ["run-spec.json"]
    for rel in outputs:
        if not isinstance(rel, str) or not rel or os.path.isabs(rel):
            return None, "invalid expected output path"
        parts = Path(rel).parts
        if ".." in parts or rel in TERMINALS:
            return None, "unsafe expected output path"
        current = attempt
        for part in parts:
            current = current / part
            if current.is_symlink():
                return None, "expected output path contains symlink: %s" % rel
        full = attempt / rel
        if not full.is_file():
            return None, "required output missing/non-regular: %s" % rel
        try:
            full.resolve(strict=True).relative_to(attempt_real)
        except (OSError, ValueError):
            return None, "expected output escapes attempt tree: %s" % rel
        if rel not in manifest:
            manifest.append(rel)
    for required in ("state.json", "result.json", "artifacts/cohort_survivors.json",
                     "artifacts/cohort_results.json"):
        if required not in manifest:
            return None, "required output not registered: %s" % required
    survivors = load(attempt / "artifacts" / "cohort_survivors.json")
    cohort_results = load(attempt / "artifacts" / "cohort_results.json")
    if survivors != []:
        return None, "cohort_survivors artifact contradicts zero-survivor result"
    if not isinstance(cohort_results, list):
        return None, "cohort_results artifact is not a list"
    seen_cohorts = set()
    for index, row in enumerate(cohort_results):
        if not isinstance(row, dict):
            return None, "cohort_results row %d is not an object" % index
        cohort = row.get("cohort")
        if not isinstance(cohort, str) or not cohort.strip() or cohort in seen_cohorts:
            return None, "cohort_results row %d has invalid/duplicate cohort" % index
        seen_cohorts.add(cohort)
        outcome_keys = [key for key in ("outcome", "status") if key in row]
        if any(row.get(key) == "SURVIVOR" for key in outcome_keys):
            return None, "cohort_results artifact reports survivors: %s" % [cohort]
        if not outcome_keys or any(row.get(key) != "CULLED" for key in outcome_keys):
            return None, "cohort_results row %d is not explicitly CULLED" % index
    cohorts_evaluated = result.get("cohorts_evaluated")
    registered_cohorts = registered.get("cohorts")
    if type(cohorts_evaluated) is not int or cohorts_evaluated <= 0 or \
            len(cohort_results) != cohorts_evaluated or registered_cohorts != cohorts_evaluated:
        return None, "cohort coverage count mismatch"
    return {"result": result, "run_spec": run_spec, "manifest": manifest}, None


def _exclusive_json(path, doc):
    """Durably stage JSON, then atomically publish without clobbering an existing immutable path."""
    path = Path(path)
    data = (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode()
    fd, temporary = tempfile.mkstemp(prefix=".%s.tmp-" % path.name, dir=str(path.parent))
    try:
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, "wb") as stream:
            fd = -1
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, str(path))  # same filesystem; atomic and no-clobber
        dir_fd = os.open(str(path.parent), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if fd != -1:
            os.close(fd)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _published_terminal_after_race(res, results_root, detector):
    """Re-read a terminal that may have been published by an overlapping reconciler."""
    attempt = Path(res.attempt_dir)
    terminals = [name for name in TERMINALS if (attempt / name).exists()]
    if not terminals:
        return None
    if len(terminals) != 1:
        return fail(res, results_root, "mechanical_closeout_failed", detector, False,
                    [str(attempt / name) for name in terminals])
    checked = validate_terminal(res, results_root, False, detector)
    if checked:
        return checked
    family = load(Path(results_root) / res.family_id / "family.json")
    existing = load(attempt.parents[1] / "verdict.json")
    token = round_verdict_token(attempt.parents[1], res.family_id, family)
    if token == "REJECT" and isinstance(existing, dict) and existing.get("run_id") == res.run_id:
        res.action, res.reason = "finalized", "concurrent deterministic REJECT already published"
        res.detail["mechanical_closeout"] = True
    else:
        res.action, res.reason = "running", "terminal published concurrently; awaiting round verdict"
        res.detail["mechanical_closeout"] = True
    return res


def _finalize_mechanical_reject(res, results_root, payload, dry_run, detector):
    attempt = Path(res.attempt_dir)
    round_dir = attempt.parents[1]
    if dry_run:
        res.action = "would_finalize"
        res.reason = "ARTIFACT_READY deterministic zero-survivor REJECT"
        res.detail["mechanical_closeout"] = True
        return res
    try:
        lease_fd = _lock(Path(results_root) / res.family_id / AGENT_LOCK)
    except OSError as exc:
        res.detail["completion_lease_error"] = str(exc)
        return fail(res, results_root, "mechanical_closeout_failed", detector, False,
                    [str(Path(results_root) / res.family_id / AGENT_LOCK)])
    if lease_fd is None:
        res.action, res.reason = "running", "family completion/disposition lease already owned"
        res.detail["mechanical_closeout"] = True
        return res
    try:
        recovered = _published_terminal_after_race(res, results_root, detector)
        if recovered is not None:
            return recovered
        fresh_payload, recheck_problem = _mechanical_reject_payload(res, results_root)
        if fresh_payload is None:
            res.detail["mechanical_closeout_recheck_problem"] = recheck_problem
            return fail(res, results_root, "mechanical_closeout_failed", detector, False,
                        [str(attempt / "result.json")])
        payload = fresh_payload
        cmd = [sys.executable, str(Path(__file__).resolve().with_name("terminal_evidence.py")),
               "publish", "--attempt-dir", str(attempt), "--status", "DONE",
               "--family-id", res.family_id, "--round-id", res.round_id, "--run-id", res.run_id,
               "--verdict-hint", "CANDIDATE_REJECT"]
        for rel in payload["manifest"]:
            cmd.extend(["--manifest", rel])
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except (subprocess.TimeoutExpired, OSError) as exc:
            recovered = _published_terminal_after_race(res, results_root, detector)
            if recovered is not None:
                return recovered
            res.detail["terminal_publish_error"] = "%s: %s" % (type(exc).__name__, exc)
            return fail(res, results_root, "mechanical_closeout_failed", detector, False,
                        [str(attempt)])
        if proc.returncode != 0:
            recovered = _published_terminal_after_race(res, results_root, detector)
            if recovered is not None:
                return recovered
            res.detail["terminal_publish_stderr"] = (proc.stderr or "")[-1000:]
            return fail(res, results_root, "mechanical_closeout_failed", detector, False,
                        [str(attempt)])
        result = payload["result"]
        verdict = {
            "schema_version": 1,
            "kind": "round_verdict",
            "contract": "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md v3.0.0",
            "authored_by": "runtime/reconcile.py deterministic zero-survivor closeout",
            "ownership_mode": "direct_hermes",
            "family_id": res.family_id,
            "round_id": res.round_id,
            "run_id": res.run_id,
            "verdict": "REJECT",
            "disposition": result.get("disposition") or "NO_SURVIVOR",
            "selector_version": result.get("selector_version"),
            "disposition_version": result.get("disposition_version"),
            "performance_claimable": False,
            "cohort_survivor_count": 0,
            "cohort_survivors": [],
            "coverage_complete": True,
            "case_evaluations_total": result.get("case_evaluations_total"),
            "expected_case_evaluations": result.get("expected_case_evaluations"),
            "result_sha256": sha256_file(str(attempt / "result.json")),
            "terminal": {"status": "DONE", "path": str(attempt / "DONE")},
            "decided_at_utc": now_utc(),
        }
        try:
            _exclusive_json(round_dir / "verdict.json", verdict)
        except FileExistsError:
            existing = load(round_dir / "verdict.json")
            token = round_verdict_token(round_dir, res.family_id,
                                        load(Path(results_root) / res.family_id / "family.json"))
            if token != "REJECT" or not isinstance(existing, dict) or                     existing.get("run_id") != res.run_id:
                return fail(res, results_root, "mechanical_closeout_failed", detector, False,
                            [str(round_dir / "verdict.json")])
        except OSError as exc:
            res.detail["verdict_publish_error"] = str(exc)
            return fail(res, results_root, "mechanical_closeout_failed", detector, False,
                        [str(round_dir / "verdict.json")])
        res.action, res.reason = "finalized", "deterministic zero-survivor REJECT published"
        res.detail["mechanical_closeout"] = True
        return res
    finally:
        os.close(lease_fd)


def handle(res, results_root, dry_run, detector):
    """Dispose of one authoritative direct attempt: verify it, then wake default once (or stay still).

    Wake conditions: a validated terminal sentinel (same-boot or prior-boot recovery) still
    missing its own round verdict, a
    compute-finished stage (`ARTIFACT_READY` / `FAILED_SCRIPT`), or an attempt that stopped writing
    outside the 90-minute stall window while still carrying no terminal sentinel - the agent died
    before Qlib started, or Qlib died mid-run. Everything else, including a fresh (live) attempt,
    stays a descriptive `orphan_candidate`.
    """
    attempt = Path(res.attempt_dir)
    family = load(Path(results_root) / res.family_id / "family.json")
    verdict = round_verdict_token(attempt.parents[1], res.family_id, family)
    if verdict:
        res.action, res.reason = "consumed", "this round has terminal verdict %s" % verdict
        return res  # verdict is durable; ignore old boot/checksum noise on consumed terminals
    terminals = [name for name in TERMINALS if (attempt / name).exists()]
    state = None
    if terminals:
        problem = validate_terminal(res, results_root, dry_run, detector)
        if problem:
            return problem
        stage = "terminal %s" % terminals[0]
        if "boot_recovery" in res.detail:
            stage += " (prior boot; artifact re-validation required)"
    else:
        state = load(attempt / "state.json")
        stage = state.get("stage") if isinstance(state, dict) else None
        res.detail["stage"] = stage
        age = stalled_minutes(attempt)
        res.detail["stalled_minutes"] = None if age is None else round(age)
        if stage not in COMPUTE_FINISHED_STAGES and not (age and age > ACTIVE_WINDOW_MINUTES):
            res.action, res.reason = "orphan_candidate", "no terminal; stage=%s" % stage
            return res
        problems = mapping_problems(res, results_root)
        if isinstance(state, dict):
            for key, want in (("family_id", res.family_id), ("round_id", res.round_id),
                              ("run_id", res.run_id)):
                if state.get(key) != want:
                    problems.append("state.json %s mismatch" % key)
        if problems:
            res.detail["mapping_problems"] = problems
            return fail(res, results_root, "mapping_mismatch", detector, dry_run,
                        [str(attempt / "state.json")])
        if stage == "ARTIFACT_READY":
            payload, mechanical_problem = _mechanical_reject_payload(res, results_root)
            if payload is not None:
                return _finalize_mechanical_reject(res, results_root, payload, dry_run, detector)
            res.detail["mechanical_closeout_not_applicable"] = mechanical_problem
        if stage not in COMPUTE_FINISHED_STAGES:
            # Stalled and still undecided: nothing host-side can ever decide this round, so the same
            # disposition session that handles a compute-finished stage takes it over and terminates
            # or retries it - the family stops holding the pipeline silently. The branch above is
            # unreachable inside the window, so live compute is never disturbed.
            stage = "stalled %s (%.0f min without a write)" % (stage or "no-state", age)
    if dry_run:
        res.action, res.reason = "would_launch", "%s; would wake default for disposition" % stage
        return res
    pid, why, busy = launch_agent(
        results_root, res.family_id,
        "disposition-v%d-%s-%s.md" % (DISPOSITION_PROMPT_VERSION, res.round_id, res.run_id),
        disposition_prompt(res.family_id, res.round_id, res.run_id, results_root))
    if busy:
        res.action, res.reason = "running", "default worker already owns this family"
    elif why:
        # A failed wake must not retry invisibly: record the fail-closed incident (deduplicated per
        # attempt + kind, and resolved by the same terminal evidence every other incident needs) so
        # the wrapper reports it and the operator sees the pipeline cannot progress.
        res = fail(res, results_root, "disposition_launch_failed", detector, dry_run, [str(attempt)])
        res.reason = "%s (%s)" % (res.reason, why)
        return res
    else:
        res.action, res.reason = "launched", "%s; detached default pid=%s" % (stage, pid)
        res.detail["agent_pid"] = pid
    return res


def main():
    ap = argparse.ArgumentParser(description="direct quant C4: verify and wake default without a board")
    ap.add_argument("--results-root", default=os.environ.get("QLIB_RESULTS_ROOT", DEFAULT_RESULTS))
    ap.add_argument("--board", default=None, help="ignored historical wrapper argument")
    ap.add_argument("--detector", default="reconciler", choices=["reconciler", "default", "operator"])
    ap.add_argument("--dry-run", action="store_true", help="read-only: no launch or incident write")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if not os.path.isdir(args.results_root):
        sys.stderr.write("usage error: results root not found: %s\n" % args.results_root)
        return 2
    results = []
    for family_id, round_id, attempts in discover_rounds(args.results_root):
        if not direct_family(load(Path(args.results_root) / family_id / "family.json")):
            continue  # C4 never dispatches historical board-owned attempts
        records = [Attempt(a, family_id, round_id, require_timestamp=False) for a in attempts]
        current, older, problem = select_authoritative(records)
        if problem:
            res = Result(next((r for r in records if r.problems), records[-1]))
            res.detail["ambiguity"] = problem
            res.detail["round_attempts"] = [r.run_id for r in records]
            results.append(fail(res, args.results_root, "attempt_selection_ambiguous", args.detector,
                                args.dry_run, [str(r.path) for r in records]))
            continue
        for rec in older:
            res = Result(rec)
            res.action, res.reason = "superseded", "newest attempt is %s" % current.run_id
            results.append(res)
        results.append(handle(Result(current), args.results_root, args.dry_run, args.detector))
    # A wake that cannot start is recorded as an incident (scan above), never as its own action:
    # every failed disposition is surfaced, so rc=3 means exactly "incident written".
    incidents = [r for r in results if r.action == "incident"]
    report = {"schema_version": 1, "kind": "reconcile", "contract_section": "9.4",
              "ran_at_utc": now_utc(), "results_root": args.results_root, "dry_run": args.dry_run,
              "attempts_scanned": len(results),
              "launched": [r.run_id for r in results if r.action == "launched"],
              "would_launch": [r.run_id for r in results if r.action == "would_launch"],
              "finalized": [r.run_id for r in results if r.action == "finalized"],
              "would_finalize": [r.run_id for r in results if r.action == "would_finalize"],
              "incidents": len(incidents), "results": [r.as_dict() for r in results]}
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print("scanned=%d launched=%s incidents=%d dry_run=%s" % (
            len(results), report["launched"], len(incidents), args.dry_run))
    return 3 if incidents else 0


if __name__ == "__main__":
    sys.exit(main())
