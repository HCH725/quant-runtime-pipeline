#!/usr/bin/env python3
"""Automatic production handoff (contract sections 14.1 / 14.2 / 14.4) - ONE round per run.

Not a daemon, service, factory, queue or registry. Every input is either a read-only
`hermes kanban` DB read-back or a file contract under `/results`:
  * `/results/*/family.json`      = the existing family set (ownership + fingerprint, contract 10.6)
  * `/results/_handoff/candidates.json` = the reviewed candidate pool (contract 14.4)
  * `/results/_incidents/...`     = fail-closed incident ledger (contract 12.6)
  * `/results/_handoff/handoff_log.jsonl` = append-only record of append/finding decisions

Legal action: append at most ONE new family card at the chain tail
(`parents=[tail_id]`, `idempotency_key=<family_id>`) and land its `family.json` in the same round.
Duplicate / ambiguous / ineligible / incident / freeze -> fail-closed: no card, ONE finding line,
no retry storm.

Fence note (contract 9.4): the board mutation shells out to `hermes kanban create`, which Hermes
refuses from a delegate_task child context. Run this from a fence-free host shell - the cron entry
does exactly that.

Exit codes: 0 = ok (stdout empty when nothing needs reporting), 2 = usage error, 1 = unexpected.
stdout is the cron payload: emitted only for a real append or for a *new* distinct finding.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_RESULTS = "/Volumes/ExpansionDrive/qlib-results"
DEFAULT_WORKSPACE = "/Users/hong/workspace/quant-runtime-pipeline"
HANDOFF_DIRNAME = "_handoff"
POOL_FILENAME = "candidates.json"
LOG_FILENAME = "handoff_log.jsonl"
INCIDENT_DIRNAME = "_incidents"
INCIDENT_FILENAME = "reconciliation_incident.jsonl"
ACTIVE_STATUSES = ("ready", "running", "scheduled")
TERMINAL_STATUSES = ("done", "archived")
_STRATEGY_CARD_FIELDS = ("id", "status", "created_at", "title")


def sh(cmd, timeout=180):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or ""), (p.stderr or "")
    except FileNotFoundError:
        return 127, "", "not found: %s" % cmd[0]
    except subprocess.TimeoutExpired:
        return 124, "", "timeout: %s" % " ".join(cmd)


def now_utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def fingerprint(fingerprint_input):
    """Contract 14.3: sha256 of the normalised input string; auditor recomputes it from family.json."""
    return "sha256:" + hashlib.sha256(fingerprint_input.encode()).hexdigest()


def _first_json(text):
    for i, ch in enumerate(text or ""):
        if ch in "[{":
            try:
                return json.loads(text[i:])
            except ValueError:
                return None
    return None


def board_tasks(board):
    """(tasks_by_id, error). One read-only CLI call; truth is the kernel DB, not a card narrative."""
    rc, out, err = sh(["hermes", "kanban", "--board", board, "list", "--json", "--archived"])
    if rc != 0:
        return None, "kanban list rc=%d: %s" % (rc, (err or out).strip()[:200])
    doc = _first_json(out)
    rows = doc.get("tasks") if isinstance(doc, dict) else doc
    if not isinstance(rows, list):
        return None, "unparsable kanban list json"
    tasks = {}
    for row in rows:
        if isinstance(row, dict) and row.get("id"):
            tasks[row["id"]] = {k: row.get(k) for k in _STRATEGY_CARD_FIELDS}
    return tasks, None


def board_card(board, task_id):
    """(show_doc, error) - show_doc carries `task` and `parents`."""
    rc, out, err = sh(["hermes", "kanban", "--board", board, "show", task_id, "--json"])
    if rc != 0:
        return None, "kanban show rc=%d: %s" % (rc, (err or out).strip()[:200])
    doc = _first_json(out)
    if not isinstance(doc, dict) or not isinstance(doc.get("task"), dict):
        return None, "unparsable kanban show json"
    return doc, None


def read_families(results_root):
    """{family_id: family.json doc} - contract 10.6 file contract, never a registry service."""
    families = {}
    root = Path(results_root)
    for path in sorted(root.glob("*/family.json")):
        if path.parent.name.startswith("_"):
            continue
        try:
            families[path.parent.name] = json.loads(path.read_text())
        except ValueError:
            families[path.parent.name] = {"_unparsable": str(path)}
    return families


def unresolved_incidents(results_root, tasks):
    """Incident lines whose card is still on the board and not terminal (contract 12.6 gate)."""
    path = Path(results_root) / INCIDENT_DIRNAME / INCIDENT_FILENAME
    if not path.is_file():
        return []
    open_incidents = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            open_incidents.append({"kind": "unparsable_incident_line", "line": line[:120]})
            continue
        tid = rec.get("kanban_task_id") or rec.get("task_id")
        status = (tasks.get(tid) or {}).get("status") if tid else None
        if status is None or status not in TERMINAL_STATUSES:
            open_incidents.append({"incident_id": rec.get("incident_id"), "kind": rec.get("kind"),
                                   "kanban_task_id": tid, "observed_status": status})
    return open_incidents


def read_pool(path):
    """(candidates, error). Reviewed pool curated by default; never free-form intake."""
    if not path.is_file():
        return None, "pool file not found: %s" % path
    try:
        doc = json.loads(path.read_text())
    except ValueError as exc:
        return None, "pool not valid JSON: %s" % exc
    cands = doc.get("candidates") if isinstance(doc, dict) else None
    if not isinstance(cands, list):
        return None, "pool has no 'candidates' list"
    return cands, None


def card_body(pool_path, cand):
    """Card body text: inline `card_body`, or a markdown file beside the pool (`card_body_file`)."""
    if cand.get("card_body"):
        return cand["card_body"]
    rel = cand.get("card_body_file")
    if not rel:
        return None
    path = Path(pool_path).parent / rel
    try:
        return path.read_text() if path.is_file() else None
    except OSError:
        return None


class Round(object):
    def __init__(self):
        self.action = None
        self.reason = None
        self.finding_key = None
        self.family_id = None
        self.task_id = None
        self.tail_id = None
        self.detail = {}

    def finding(self, kind, reason, **detail):
        self.action = "finding"
        self.reason = "%s: %s" % (kind, reason)
        self.finding_key = kind
        self.detail.update(detail)
        return self

    def as_dict(self):
        d = {"action": self.action, "reason": self.reason, "family_id": self.family_id,
             "task_id": self.task_id, "tail_id": self.tail_id}
        if self.detail:
            d["detail"] = self.detail
        return d


def append_log(results_root, record, dry_run):
    if dry_run:
        return None
    path = Path(results_root) / HANDOFF_DIRNAME / LOG_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as fh:  # append-only, never rewrite existing lines
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return str(path)


def last_finding_key(results_root):
    path = Path(results_root) / HANDOFF_DIRNAME / LOG_FILENAME
    if not path.is_file():
        return None
    key = None
    for line in path.read_text().splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("action") == "finding":
            key = rec.get("finding_key")
    return key


def write_family_json(results_root, cand, task_id, board):
    """Contract 10.6 immutable ownership record, O_EXCL, fsynced, then read back and verified."""
    doc = {
        "schema_version": 1,
        "family_id": cand["family_id"],
        "kanban_task_id": task_id,
        "kanban_board": board,
        "semantic_fingerprint": fingerprint(cand["fingerprint_input"]),
        "fingerprint_input": cand["fingerprint_input"],
        "parent_family": cand.get("parent_family"),
        "lineage_note": cand.get("lineage_note") or "",
        "created_at_utc": now_utc(),
        "handoff": {"source": "production_handoff", "contract_section": "14.4",
                    "pool_fingerprint_source": cand.get("provenance", {}).get("reviewed_source")},
    }
    path = Path(results_root) / cand["family_id"] / "family.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    try:
        os.write(fd, (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    readback = json.loads(path.read_text())
    if readback.get("kanban_task_id") != task_id or readback.get("family_id") != cand["family_id"]:
        raise ValueError("family.json read-back mismatch at %s" % path)
    return doc, str(path)


def append_one(results_root, board, tail, cand, args, res):
    """Create the tail card, then land family.json in the same round (contract 14.2 steps 5-7)."""
    cmd = ["hermes", "kanban", "--board", board, "create",
           "--assignee", cand.get("assignee") or "default",
           "--parent", tail,
           "--priority", str(cand.get("priority", 100)),
           "--idempotency-key", cand["family_id"],
           "--workspace", "dir:" + (cand.get("workspace_path") or DEFAULT_WORKSPACE),
           "--completion-contract", "local-only",
           "--created-by", "production-handoff",
           "--body", cand.get("_body") or cand.get("card_body") or "",
           "--json"]
    if cand.get("goal_mode", True):
        cmd += ["--goal", "--goal-max-turns", str(cand.get("goal_max_turns", 20))]
    for skill in cand.get("skills") or []:
        cmd += ["--skill", skill]
    if args.dry_run:
        res.action = "would_append"
        res.reason = "all section 14 gates pass; would append family %s at tail %s" % (
            cand["family_id"], tail)
        res.family_id = cand["family_id"]
        res.tail_id = tail
        return res
    cmd.append(cand["title"])

    rc, out, err = sh(cmd, timeout=300)
    if rc != 0:
        res.finding("create_failed", "hermes kanban create rc=%d: %s" % (rc, (err or out).strip()[:300]),
                    attempted_family=cand["family_id"])
        return res
    doc = _first_json(out)
    task_id = doc.get("id") if isinstance(doc, dict) else None
    if not task_id:
        res.finding("create_failed", "kanban create returned no task id: %s" % (out or "")[:200])
        return res

    show, why = board_card(board, task_id)
    if show is None:
        res.finding("readback_failed", why, attempted_family=cand["family_id"], created_task=task_id)
        return res
    if tail not in list(show.get("parents") or []):
        res.finding("readback_failed", "new card parents=%r do not include tail %s"
                    % (show.get("parents"), tail), created_task=task_id)
        return res

    res.task_id = task_id
    res.family_id = cand["family_id"]
    res.tail_id = tail
    try:
        doc, path = write_family_json(results_root, cand, task_id, board)
    except (OSError, ValueError) as exc:
        res.finding("family_json_failed",
                    "%s (card %s exists and stays; contract 9.4/14.2 fail-closed)" % (exc, task_id),
                    created_task=task_id)
        return res
    res.action = "appended"
    res.reason = "family %s -> card %s (status=%s) parent=%s; family.json=%s" % (
        cand["family_id"], task_id, show["task"].get("status"), tail, path)
    res.detail["card_status"] = show["task"].get("status")
    res.detail["semantic_fingerprint"] = doc["semantic_fingerprint"]
    res.detail["family_json"] = path
    return res


def round_once(args):
    res = Round()
    root = Path(args.results_root)
    if not root.is_dir():
        return res.finding("results_root_missing", "results root not found: %s" % args.results_root)

    pool_path = Path(args.pool) if args.pool else root / HANDOFF_DIRNAME / POOL_FILENAME
    tasks, why = board_tasks(args.board)
    if tasks is None:
        return res.finding("board_unreadable", why)

    families = read_families(args.results_root)
    res.detail["families_scanned"] = len(families)
    strategy = []
    for family_id, doc in sorted(families.items()):
        tid = doc.get("kanban_task_id")
        if not tid:
            return res.finding("family_card_missing", "family %s has no kanban_task_id" % family_id,
                               family_id=family_id)
        if tid not in tasks:
            return res.finding("family_card_missing", "family %s card %s not on board %s"
                               % (family_id, tid, args.board), family_id=family_id)
        strategy.append({"family_id": family_id, "id": tid, "status": tasks[tid]["status"],
                         "created_at": tasks[tid].get("created_at") or 0,
                         "semantic_fingerprint": doc.get("semantic_fingerprint")})
    res.detail["strategy_cards"] = len(strategy)

    active = [c for c in strategy if c["status"] in ACTIVE_STATUSES]
    if active:
        res.action = "noop"
        res.reason = "active strategy card present (%s=%s); tail append waits" % (
            active[0]["id"], active[0]["status"])
        return res

    blocked = sorted(t["id"] for t in tasks.values() if t["status"] == "blocked")
    if blocked:
        return res.finding("blocked_card_present",
                           "board has blocked card(s) %s (freeze/human gate, contract 12.5/12.6)"
                           % ",".join(blocked))

    incidents = unresolved_incidents(args.results_root, tasks)
    if incidents:
        return res.finding("unresolved_incident",
                           "%d unresolved incident(s) (contract 12.6): %s"
                           % (len(incidents), json.dumps(incidents[:3], ensure_ascii=False)),
                           incidents=len(incidents))

    if not strategy:
        return res.finding("no_tail_card", "no strategy card on board %s with a family.json" % args.board)
    strategy.sort(key=lambda c: (c["created_at"], c["id"]))
    tail = strategy[-1]
    res.tail_id = tail["id"]
    if tail["status"] not in TERMINAL_STATUSES:
        return res.finding("tail_not_terminal", "tail %s status=%s (contract 14.1)"
                           % (tail["id"], tail["status"]), tail_id=tail["id"])

    cands, why = read_pool(pool_path)
    if cands is None:
        return res.finding("pool_missing", why, pool=str(pool_path))

    seen_ids = set(families)
    seen_fp = {c["semantic_fingerprint"] for c in strategy if c["semantic_fingerprint"]}
    pool_ids, pool_fps = set(), set()
    for cand in cands:
        fid, fin = cand.get("family_id"), cand.get("fingerprint_input")
        if not fid or not fin or not cand.get("title") or not card_body(pool_path, cand):
            return res.finding("pool_invalid", "candidate missing family_id/fingerprint_input/"
                               "card_body(_file)/title: %s" % json.dumps(cand.get("family_id")))
        if fid in pool_ids or fingerprint(fin) in pool_fps:
            return res.finding("ambiguous_pool", "pool repeats candidate %s / fingerprint" % fid)
        pool_ids.add(fid)
        pool_fps.add(fingerprint(fin))

    eligible = [c for c in cands
                if c["family_id"] not in seen_ids
                and not (root / c["family_id"]).exists()
                and fingerprint(c["fingerprint_input"]) not in seen_fp]
    if not eligible:
        return res.finding("no_eligible_candidate",
                           "pool %s has %d candidates, all already present in /results (contract 14.3)"
                           % (pool_path, len(cands)), pool=str(pool_path))
    chosen = dict(eligible[0])
    chosen["_body"] = card_body(pool_path, chosen)
    return append_one(args.results_root, args.board, tail["id"], chosen, args, res)


def main():
    ap = argparse.ArgumentParser(description="contract 14.4 automatic production handoff (one round)")
    ap.add_argument("--results-root", default=os.environ.get("QLIB_RESULTS_ROOT", DEFAULT_RESULTS))
    ap.add_argument("--board", default=os.environ.get("HERMES_KANBAN_BOARD", "quant-strategy-research"))
    ap.add_argument("--pool", default=None, help="candidate pool JSON (default <results>/_handoff/%s)"
                    % POOL_FILENAME)
    ap.add_argument("--detector", default="handoff", choices=["handoff", "default", "operator"])
    ap.add_argument("--dry-run", action="store_true", help="read-only: no card, no file, no log line")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet-noop", action="store_true", default=True,
                    help="print nothing for a normal no-op round (cron default)")
    args = ap.parse_args()

    if not os.path.isdir(args.results_root):
        sys.stderr.write("usage error: results root not found: %s\n" % args.results_root)
        return 2

    res = round_once(args)
    record = {
        "schema_version": 1,
        "detector": args.detector,
        "contract_section": "14.4",
        "ran_at_utc": now_utc(),
        "board": args.board,
        "results_root": args.results_root,
        "dry_run": args.dry_run,
    }
    record.update(res.as_dict())

    # Fail-closed findings are recorded once per distinct kind and reported once; a healthy no-op
    # round stays silent so an idle pipeline never turns into an hourly notification.
    emit = res.action in ("appended", "would_append")
    if res.action == "finding":
        previous = last_finding_key(args.results_root)
        append_log(args.results_root, record, args.dry_run)
        emit = args.dry_run or res.finding_key != previous

    if args.json:
        print(json.dumps(record, indent=2, ensure_ascii=False))
    elif emit:
        print("production handoff: %s — %s" % (res.action, res.reason))
    elif res.action == "noop":
        sys.stderr.write("production handoff: noop - %s\n" % res.reason)
    return 0


if __name__ == "__main__":
    sys.exit(main())
