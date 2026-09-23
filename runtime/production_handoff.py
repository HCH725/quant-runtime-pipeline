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
no retry storm.  A freeze counts only for strategy cards owned by a canonical `family.json`, and a
hold-status (blocked/triage) family whose immutable round verdict is already contract-terminal is
terminal-equivalent for ordering, so the next candidate may proceed (Phase 2A).  Since v1.3.0 a
candidate whose card body does not register the DCA parameter
domain and the cohort survivor rules is also fail-closed (`candidate_body_not_v13`): a v1.2-era
body cannot express a v1.3 full backtest (contract 7.2/7.3/14.4).  The created body is the candidate
body verbatim plus a fixed system-owned lifecycle footer (contract 6.4: an honest prerequisite-missing
TECHNICAL_INCOMPLETE terminal satisfies the card goal) - the footer never rewrites candidate bytes,
never enters the fingerprint input, and changes no append gate.

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
# Hold statuses: a strategy card parked in one of these gates the tail append (contract 12.5/12.6)
# UNLESS its family already carries a contract-terminal round verdict (Phase 2A non-blocking handoff).
HOLD_STATUSES = ("blocked", "triage")
# Contract-terminal verdict tokens accepted from results/<family>/rounds/*/verdict.json.
TERMINAL_VERDICTS = ("PASS", "REJECT", "FINALIST", "DEFERRED", "TECHNICAL_INCOMPLETE")
_STRATEGY_CARD_FIELDS = ("id", "status", "created_at", "title")
# v1.3.0 candidate requirement (contract 14.4 + 7.2/7.3): a card appended by this automation
# must register the DCA parameter domain and the cohort survivor rules, otherwise it cannot
# express a v1.3 full backtest. Compared case-insensitively against the resolved card body.
CANDIDATE_BODY_MARKERS = ("DCA PARAMETER DOMAIN", "COHORT SURVIVOR")
# System-owned lifecycle footer appended to every card body this automation creates (contract 6.4).
# Fixed text, never derived from the candidate: the candidate bytes stay verbatim and its fingerprint
# input is untouched. It also prevents source-market identity or source-universe breadth from
# becoming a second suitability gate when the registered core signal is computable on canonical local data.
LIFECYCLE_FOOTER = (
    "\n\n---\n"
    "LIFECYCLE FOOTER（system-owned；append 時由 production_handoff 注入，非 candidate 規格的一部分；"
    "contract §6.4／§14.4）\n"
    "- full backtest 是本卡的目標。任何計算前，eligible universe 固定為 canonical local raw 中"
    "能讓本策略核心 signal/mechanism 合法計算的完整可用集合；原研究的 market／symbol 清單只作"
    "provenance 與外部效度參考，不要求與本機逐字一致。\n"
    "- 不得僅因原研究市場不同、named symbols 缺少或本機 universe 較小就判 TECHNICAL_INCOMPLETE。"
    "只要核心 signal 可在本機資料上計算，就必須用該 local eligible universe 完整跑"
    "symbols × timeframes × parameter domain × DCA execution × historical/OOS/robustness，"
    "並把結論範圍明確限制在該 local universe；不得事後挑幣或依結果縮減 universe。\n"
    "- 只有核心 signal 所必需的 data type／field 在本機完全不存在，導致任何合法 local universe"
    "都無法計算時，才可走 prerequisite-missing TECHNICAL_INCOMPLETE；其缺席須由 measured evidence"
    "證實，並依 contract 寫出 immutable `round-spec.json` ＋ `verdict.json`。\n"
    "- 若 candidate 文字中的 source-market exact-match／不得縮減 universe 條款與上述 local-universe"
    "規則衝突，以本 system-owned lifecycle rule 為執行準則；candidate bytes 與 fingerprint 仍不改寫。\n"
    "- `kanban_block` 仍只保留給 shared-layer failure（§12.5）或 contract 尚未決定、"
    "確實需要 human decision 的情況（§12.6）。\n"
    "- prerequisite 證據有界（Phase 2B）：唯一 canonical 來源是 Common Data Pack 的 "
    "`/data/raw/_meta/CONFIG.json` dataset IDs 與 `/data/raw/_meta/SCHEMA.md` field/layout 宣告，"
    "不建第二套 data registry；評估只讀這兩個 catalog/schema 檔，必要時再對 canonical raw 做"
    "小範圍直接 sample／path 讀取，不掃描無關 host 目錄或整個檔案系統。"
    "CONFIG／SCHEMA 明確表示核心 signal 所必需的 data type／field 不存在（clear-absence）時，"
    "記載 requirement-vs-available 實測事實的 immutable `round-spec.json` ＋ `verdict.json` "
    "即為 card-local TECHNICAL_INCOMPLETE 的充分 terminal evidence，不得 launch 任何 full backtest。\n"
    "- 明確禁止為證明顯然不存在的資料能力而新增 candidate-specific prerequisite checker"
    "（`runtime/*_prerequisite_check.py`）、repo-wide prerequisite evidence blob、host-wide 掃描、"
    "synthetic fixtures、tamper batteries 或 bespoke validation framework；既有 legacy checker／"
    "evidence 檔案屬不可變歷史證據，不移除、不改寫。CONFIG／SCHEMA 對既有 dataset 是否具備所需 "
    "field／capability 真正 ambiguous 時 fail closed，只對該 canonical dataset 做有界直接 read-back，"
    "不自動擴大 scope；human input 只留給未解 ambiguity，不用於 clear-absence。\n"
)


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


def fenced_context(reason):
    """A `hermes kanban` refusal caused by HERMES_DELEGATED_CHILD_CONTEXT is not a board problem.

    The contract 9.4 fence means "this invocation ran in a delegate_task/worker child context"
    (e.g. a manual `hermes cron run` from a worker session). Reporting it as `board_unreadable`
    would look like a broken board; keep it a distinct, self-describing kind.
    """
    return "delegate_task child contexts cannot mutate" in (reason or "")


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


def _safe_identity_component(value):
    return (isinstance(value, str) and bool(value) and value not in (".", "..")
            and "/" not in value and "\\" not in value and "\x00" not in value)


def _read_artifact_owner(path, owner_field, expected_identity):
    try:
        if not path.is_file():
            return None
        doc = json.loads(path.read_text())
    except (OSError, UnicodeError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None
    for key, value in expected_identity.items():
        if doc.get(key) != value:
            return None
    owner = doc.get(owner_field)
    if not isinstance(owner, str) or not owner.strip():
        return None
    return owner


def _recover_legacy_task_id(results_root, rec):
    """Recover ownership only from the row's three immutable execution artifacts."""
    identity = {key: rec.get(key) for key in ("family_id", "round_id", "run_id")}
    if not all(_safe_identity_component(value) for value in identity.values()):
        return None

    # ponytail: use existing immutable artifacts instead of adding a resolution registry.
    root = Path(results_root)
    round_root = root / identity["family_id"] / "rounds" / identity["round_id"]
    owners = (
        _read_artifact_owner(root / identity["family_id"] / "family.json", "kanban_task_id",
                             {"family_id": identity["family_id"]}),
        _read_artifact_owner(round_root / "round-spec.json", "task_id",
                             {"family_id": identity["family_id"], "round_id": identity["round_id"]}),
        _read_artifact_owner(round_root / "attempts" / identity["run_id"] / "run-spec.json", "task_id",
                             {"family_id": identity["family_id"], "round_id": identity["round_id"],
                              "run_id": identity["run_id"]}),
    )
    if any(owner is None for owner in owners) or len(set(owners)) != 1:
        return None
    return owners[0]


def family_has_terminal_verdict(results_root, family_id, task_id):
    """True when an existing immutable round verdict already terminates this strategy family.

    Reads `results/<family>/rounds/*/verdict.json` only - the artifacts are already there, so no
    registry, DB or state store. A verdict counts only if `family_id` AND `kanban_task_id` both
    match and the token is contract-terminal, so a malformed, foreign or partial verdict can never
    bypass the gate (fail-closed).
    """
    rounds = Path(results_root) / family_id / "rounds"
    if not rounds.is_dir():
        return False
    for path in sorted(rounds.glob("*/verdict.json")):
        try:
            doc = json.loads(path.read_text())
        except (OSError, UnicodeError, ValueError):
            continue
        if not isinstance(doc, dict):
            continue
        if (doc.get("family_id") == family_id and doc.get("kanban_task_id") == task_id
                and doc.get("verdict") in TERMINAL_VERDICTS):
            return True
    return False


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
        if not tid:
            tid = _recover_legacy_task_id(results_root, rec)
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


def body_with_footer(cand):
    """Candidate body verbatim + the fixed system-owned lifecycle footer (contract 6.4).

    The candidate bytes are never edited and the footer never enters `fingerprint_input`; it is
    appended so the created card states the prerequisite-missing terminal rule on its own body.
    """
    return (cand.get("_body") or cand.get("card_body") or "") + LIFECYCLE_FOOTER


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
           "--body", body_with_footer(cand),
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
        return res.finding("fenced_context" if fenced_context(why) else "board_unreadable", why)

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
        status = tasks[tid]["status"]
        strategy.append({"family_id": family_id, "id": tid, "status": status,
                         # A hold-status card whose immutable round verdict is already
                         # contract-terminal is terminal-equivalent for handoff ordering.
                         "terminal": status in TERMINAL_STATUSES or (
                             status in HOLD_STATUSES
                             and family_has_terminal_verdict(args.results_root, family_id, tid)),
                         "created_at": tasks[tid].get("created_at") or 0,
                         "semantic_fingerprint": doc.get("semantic_fingerprint")})
    res.detail["strategy_cards"] = len(strategy)

    active = [c for c in strategy if c["status"] in ACTIVE_STATUSES]
    if active:
        res.action = "noop"
        res.reason = "active strategy card present (%s=%s); tail append waits" % (
            active[0]["id"], active[0]["status"])
        return res

    # Only strategy-family cards (canonical family.json ownership) can freeze the handoff; an
    # unrelated blocked board task is somebody else's gate, not a pipeline freeze.
    held = sorted(c["id"] for c in strategy
                  if c["status"] in HOLD_STATUSES and not c["terminal"])
    if held:
        return res.finding("blocked_card_present",
                           "strategy card(s) %s are blocked/triage with no contract-terminal "
                           "verdict (freeze/human gate, contract 12.5/12.6)"
                           % ",".join(held))

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
    if not tail["terminal"]:
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
    body = (chosen["_body"] or "").upper()
    missing = [m for m in CANDIDATE_BODY_MARKERS if m not in body]
    if missing:
        return res.finding("candidate_body_not_v13",
                           "candidate %s card body is not a v1.3.0 card body: missing %s "
                           "(contract 14.4 requires the DCA parameter domain and the cohort "
                           "survivor rules in every appended card)" % (chosen["family_id"], missing),
                           pool_entry=chosen["family_id"], missing=missing)
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
    elif res.action == "appended":
        append_log(args.results_root, record, args.dry_run)

    if args.json:
        print(json.dumps(record, indent=2, ensure_ascii=False))
    elif emit:
        print("production handoff: %s — %s" % (res.action, res.reason))
    elif res.action == "noop":
        sys.stderr.write("production handoff: noop - %s\n" % res.reason)
    return 0


if __name__ == "__main__":
    sys.exit(main())
