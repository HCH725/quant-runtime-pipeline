#!/usr/bin/env python3
"""Automatic production handoff (contract section 14.4) - ONE round per run, Kanban-free decision.

Every decision input is a read-only file contract under the results root:
  * `/results/*/family.json`              = the existing family set (ownership + fingerprint, 10.6)
  * `/results/*/rounds/*/verdict.json`    = contract-terminal round verdicts
  * `/results/*/rounds/*/attempts/*`      = real runtime evidence (run-spec, sentinels, artifacts)
  * `/results/_handoff/candidates.json`   = the reviewed candidate pool (contract 14.4)
  * `/results/_incidents/...`             = fail-closed incident ledger (contract 12.6)
  * `/results/_handoff/handoff_log.jsonl` = append-only record of advance/finding decisions

Hermes/Kanban is neither consulted nor required for any advance/freeze decision: this file contains
no board read (`list`/`show`/status) and no board read-back. A blocked, stale, missing or unreadable
card therefore has zero power over the pipeline (contract 14.4 runtime-evidence gate). The created
work-order card is only the existing execution vehicle for the agent lane
(dispatcher -> default worker -> `qlib-run`); its state is never an input here.

Legal action: advance at most ONE new family per round - land its `family.json` and dispatch its
work-order card in the same round. Duplicate / ambiguous / ineligible / incident / active-runtime
-> fail-closed: no card, ONE finding line, no retry storm.

Runtime guards (all objective, all from results-root artifacts):
  * an ACTIVE attempt (non-terminal runtime evidence whose newest write is inside
    ACTIVE_WINDOW_MINUTES) holds the pipeline: the next candidate is not launched while compute is
    really running;
  * a family registered less than LAUNCH_GRACE_MINUTES ago that has produced no runtime evidence
    yet is a launch in flight (the worker still has to publish its round/run specs) -> wait;
  * an unresolved canonical incident (its family/attempt carries no terminal evidence) still gates.
Both windows reuse the 90-minute stall window the quant runtime watchdog already applies to a run
that stopped writing `run.log`/artifacts - no new state store, no new state machine.

Exit codes: 0 = ok (stdout empty when nothing needs reporting), 2 = usage error, 1 = unexpected.
stdout is the cron payload: emitted only for a real advance or for a *new* distinct finding. The
pipeline outcome token (`advanced` / `running` / `idle` / `finding` / `incident`) is always on the
JSON record (`--json`) and on one stderr line, so C3 can tell invocation success from outcome.
"""
import argparse
import datetime
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from terminal_evidence import TERMINALS  # noqa: E402

DEFAULT_RESULTS = "/Volumes/ExpansionDrive/qlib-results"
DEFAULT_WORKSPACE = "/Users/hong/workspace/quant-runtime-pipeline"
HANDOFF_DIRNAME = "_handoff"
POOL_FILENAME = "candidates.json"
LOG_FILENAME = "handoff_log.jsonl"
INCIDENT_DIRNAME = "_incidents"
INCIDENT_FILENAME = "reconciliation_incident.jsonl"
# Contract-terminal verdict tokens accepted from results/<family>/rounds/*/verdict.json.
TERMINAL_VERDICTS = ("PASS", "REJECT", "FINALIST", "DEFERRED", "TECHNICAL_INCOMPLETE")
# Runtime-evidence windows, in minutes (results-root artifacts only; never a board read).
ACTIVE_WINDOW_MINUTES = 90
LAUNCH_GRACE_MINUTES = 90
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


def _load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, UnicodeError, ValueError):
        return None


def read_families(results_root):
    """{family_id: family.json doc} - contract 10.6 file contract, never a registry service."""
    families = {}
    root = Path(results_root)
    for path in sorted(root.glob("*/family.json")):
        if path.parent.name.startswith("_"):
            continue
        try:
            families[path.parent.name] = json.loads(path.read_text())
        except (OSError, UnicodeError, ValueError):
            families[path.parent.name] = {"_unparsable": str(path)}
    return families


def parse_utc(value):
    """ISO-8601 UTC timestamp -> epoch seconds; None when missing/unparsable."""
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
    return stamp.timestamp()


def family_verdict_token(results_root, family_id, family_doc):
    """The family's contract-terminal round verdict token, else None (results artifacts only).

    Reads `results/<family>/rounds/*/verdict.json` - the artifacts are already there, so no
    registry, DB or state store. A verdict counts only when `family_id` matches and, whenever both
    sides carry an ownership id, `kanban_task_id` agrees too: a malformed, foreign or partial
    verdict can never close a family (fail-closed). The ownership id is read from the *family.json
    artifact* (`kanban_task_id`), never from the board.
    """
    rounds = Path(results_root) / family_id / "rounds"
    if not rounds.is_dir():
        return None
    owner = family_doc.get("kanban_task_id") if isinstance(family_doc, dict) else None
    for path in sorted(rounds.glob("*/verdict.json")):
        doc = _load_json(path)
        if not isinstance(doc, dict):
            continue
        if doc.get("family_id") != family_id:
            continue
        verdict_owner = doc.get("kanban_task_id")
        if owner and verdict_owner and verdict_owner != owner:
            continue
        if doc.get("verdict") in TERMINAL_VERDICTS:
            return doc["verdict"]
    return None


def family_attempts(results_root, family_id):
    """Every attempt directory of the family (results/<family>/rounds/*/attempts/*), sorted."""
    rounds = Path(results_root) / family_id / "rounds"
    if not rounds.is_dir():
        return []
    found = []
    for rnd in sorted(rounds.iterdir()):
        attempts = rnd / "attempts"
        if rnd.is_dir() and attempts.is_dir():
            found.extend(a for a in sorted(attempts.iterdir()) if a.is_dir())
    return found


def attempt_activity(attempt_dir):
    """Newest observed file write inside one attempt dir, as epoch seconds; None when unreadable.

    Runtime evidence means files the engines/runner actually wrote: the attempt dir itself (run-spec,
    sentinels, run.log) plus its `artifacts/` children. Nothing is executed and no content is parsed
    here - mtime is the objective "is this still running" signal the watchdog already uses.
    """
    stamps = []
    try:
        paths = [attempt_dir] + list(attempt_dir.iterdir())
        artifacts = attempt_dir / "artifacts"
        if artifacts.is_dir():
            paths.append(artifacts)
            paths.extend(artifacts.iterdir())
    except OSError:
        return None
    for path in paths:
        try:
            stamps.append(path.stat().st_mtime)
        except OSError:
            continue
    return max(stamps) if stamps else None


def clean_terminal(attempt_dir):
    """True when the attempt carries exactly one parsable, identity-consistent terminal sentinel."""
    present = [t for t in TERMINALS if (attempt_dir / t).is_file()]
    if len(present) != 1:
        return False
    name = present[0]
    doc = _load_json(attempt_dir / name)
    if not isinstance(doc, dict) or doc.get("status") != name:
        return False
    # results/<family>/rounds/<round>/attempts/<run> -> parents[3] / parents[1] / self
    for key, want in (("family_id", attempt_dir.parents[3].name),
                      ("round_id", attempt_dir.parents[1].name),
                      ("run_id", attempt_dir.name)):
        if key in doc and doc[key] != want:
            return False
    return True


def runtime_state(results_root, family_id, family_doc, now=None):
    """One family's runtime state from artifacts only -> dict(family_id, verdict, attempt, activity,
    in_flight, why).

    `in_flight` is the pipeline guard: the family still owes the pipeline something. Terminal
    verdict -> done. Otherwise the newest attempt decides: fresh non-terminal runtime evidence means
    compute is really running; a family that has produced no runtime evidence yet is a launch in
    flight for LAUNCH_GRACE_MINUTES. Anything older than the windows is stale/abandoned and no longer
    holds the pipeline (this is what keeps a blocked or dead card from freezing production).
    """
    now = time.time() if now is None else now
    state = {"family_id": family_id, "verdict": None, "attempt": None, "activity": None,
             "in_flight": False, "why": ""}
    if not isinstance(family_doc, dict) or family_doc.get("_unparsable"):
        state["why"] = "family.json unreadable"
        state["in_flight"] = True  # fail-closed: an unreadable family is never skipped silently
        return state

    verdict = family_verdict_token(results_root, family_id, family_doc)
    state["verdict"] = verdict
    if verdict:
        state["why"] = "terminal verdict %s" % verdict
        return state

    attempts = family_attempts(results_root, family_id)
    activity = [(attempt_activity(a) or 0.0, a) for a in attempts]
    activity = [(stamp, a) for stamp, a in activity if stamp]
    if activity:
        stamp, attempt = max(activity, key=lambda item: (item[0], str(item[1])))
        state["attempt"] = str(attempt)
        state["activity"] = stamp
        age_minutes = (now - stamp) / 60.0
        state["in_flight"] = age_minutes <= ACTIVE_WINDOW_MINUTES
        state["why"] = ("attempt %s last write %.0f min ago%s"
                        % (attempt.name, age_minutes,
                           "" if state["in_flight"] else " (stale: outside the %d min window)"
                           % ACTIVE_WINDOW_MINUTES))
        return state

    registered = parse_utc(family_doc.get("created_at_utc"))
    if registered is None:
        state["why"] = "no attempt and no parsable created_at_utc (fail-closed)"
        state["in_flight"] = True
        return state
    age_minutes = (now - registered) / 60.0
    state["in_flight"] = age_minutes <= LAUNCH_GRACE_MINUTES
    state["why"] = ("registered %.0f min ago, no runtime evidence yet (%s the %d min launch grace)"
                    % (age_minutes, "inside" if state["in_flight"] else "outside",
                       LAUNCH_GRACE_MINUTES))
    return state


def unresolved_incidents(results_root, families, now=None):
    """Incident lines whose canonical subject still carries no terminal evidence (contract 12.6).

    Kanban-free replacement for the old "is the incident's card still non-terminal" read: an incident
    is resolved once its own subject - the family (contract-terminal verdict) or the referenced
    attempt (clean terminal sentinel) - has reached terminal evidence. A line without family identity,
    an unparsable line, or a subject that still owes terminal evidence stays open (fail-closed), so a
    real results/reconciliation safety incident still gates the pipeline.
    """
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
        if not isinstance(rec, dict):
            open_incidents.append({"kind": "unparsable_incident_line", "line": line[:120]})
            continue
        family_id = rec.get("family_id")
        subject = {"incident_id": rec.get("incident_id"), "kind": rec.get("kind"),
                   "family_id": family_id}
        if not (isinstance(family_id, str) and family_id):
            subject["why"] = "no family identity in incident line"
            open_incidents.append(subject)
            continue
        doc = families.get(family_id)
        if doc is None:
            doc = _load_json(Path(results_root) / family_id / "family.json")
        if isinstance(doc, dict) and family_verdict_token(results_root, family_id, doc):
            continue  # family reached a contract-terminal verdict -> incident is a diagnostic
        round_id, run_id = rec.get("round_id"), rec.get("run_id")
        attempt = None
        if isinstance(round_id, str) and isinstance(run_id, str):
            candidate = Path(results_root) / family_id / "rounds" / round_id / "attempts" / run_id
            attempt = candidate if candidate.is_dir() else None
        if attempt is not None and clean_terminal(attempt):
            continue  # the referenced attempt published a clean terminal -> resolved
        subject["why"] = ("family has no terminal verdict and attempt %s carries no clean terminal"
                          % (run_id or "(unidentified)"))
        open_incidents.append(subject)
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
        self.outcome = None
        self.reason = None
        self.finding_key = None
        self.family_id = None
        self.task_id = None
        self.detail = {}

    def finding(self, kind, reason, **detail):
        self.action = "finding"
        self.outcome = "finding"
        self.reason = "%s: %s" % (kind, reason)
        self.finding_key = kind
        self.detail.update(detail)
        return self

    def idle(self, reason, **detail):
        """Nothing to advance and nothing wrong: the pool is exhausted (a normal pipeline state)."""
        self.action = "noop"
        self.outcome = "idle"
        self.reason = "no_eligible_candidate: %s" % reason
        self.finding_key = "no_eligible_candidate"
        self.detail.update(detail)
        return self

    def waiting(self, reason, **detail):
        """Real runtime work is in flight: advancing would duplicate it."""
        self.action = "noop"
        self.outcome = "running"
        self.reason = reason
        self.detail.update(detail)
        return self

    def as_dict(self):
        d = {"action": self.action, "outcome": self.outcome, "reason": self.reason,
             "family_id": self.family_id, "task_id": self.task_id}
        if self.finding_key:
            d["finding_key"] = self.finding_key
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
    """Contract 10.6 immutable ownership record, O_EXCL, fsynced, then read back and verified.

    `kanban_task_id` stays for artifact compatibility (reconcile/validate and the survivor tools read
    it); it is the work-order id of the dispatch below, never an input to any decision here.
    """
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
                    "decision_evidence": "results_root_only",
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
    if readback.get("family_id") != cand["family_id"]:
        raise ValueError("family.json read-back mismatch at %s" % path)
    return doc, str(path)


def dispatch_work_order(board, cand, args):
    """(task_id, error). The existing agent-lane vehicle: one `hermes kanban create`, no read-back.

    Best effort by design: the canonical advance is the `family.json` registration above, so a
    dispatch failure is reported as a finding and never freezes or rewinds the pipeline. No
    `--parent` edge is used: a parent edge would let a blocked/stale card gate the dispatcher, which
    is exactly the Kanban dependency contract 14.4 removes; sequencing is enforced by the runtime
    guards instead. `--idempotency-key <family_id>` still makes a re-run converge on one card.
    """
    cmd = ["hermes", "kanban", "--board", board, "create",
           "--assignee", cand.get("assignee") or "default",
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
    cmd.append(cand["title"])
    rc, out, err = sh(cmd, timeout=300)
    if rc != 0:
        return None, "hermes kanban create rc=%d: %s" % (rc, (err or out).strip()[:300])
    doc = _first_json(out)
    task_id = doc.get("id") if isinstance(doc, dict) else None
    if not task_id:
        return None, "kanban create returned no task id: %s" % (out or "")[:200]
    return task_id, None


def append_one(results_root, board, cand, args, res):
    """Advance one family: dispatch the work-order card, then land family.json (contract 14.2).

    The dispatch is best effort and the canonical advance is the registration below, so a Kanban
    failure can never freeze or rewind the pipeline: `family.json` lands either way and the work-order
    id is recorded whenever it is known. `hermes kanban create` is idempotent by
    `--idempotency-key <family_id>`, so a later round converges on the same card.
    """
    if args.dry_run:
        res.action = "would_append"
        res.outcome = "advanced"
        res.reason = "all runtime gates pass; would advance family %s" % cand["family_id"]
        res.family_id = cand["family_id"]
        return res

    res.family_id = cand["family_id"]
    task_id, why = dispatch_work_order(board, cand, args)
    res.task_id = task_id
    try:
        doc, path = write_family_json(results_root, cand, task_id, board)
    except (OSError, ValueError) as exc:
        res.finding("family_json_failed",
                    "%s (candidate not consumed; the work order is idempotent by family_id, so the "
                    "next round converges on it)" % exc, attempted_family=cand["family_id"])
        return res

    res.detail["semantic_fingerprint"] = doc["semantic_fingerprint"]
    res.detail["family_json"] = path
    if why:
        res.action = "finding"
        res.outcome = "finding"
        res.finding_key = "work_order_failed"
        res.reason = ("work_order_failed: family %s is registered (%s) but the work-order card was "
                      "not created: %s - operator action needed" % (cand["family_id"], path, why))
        return res
    res.action = "appended"
    res.outcome = "advanced"
    res.reason = ("family %s advanced (family.json=%s) and work order %s dispatched"
                  % (cand["family_id"], path, task_id))
    return res


def round_once(args):
    res = Round()
    root = Path(args.results_root)
    if not root.is_dir():
        return res.finding("results_root_missing", "results root not found: %s" % args.results_root)

    pool_path = Path(args.pool) if args.pool else root / HANDOFF_DIRNAME / POOL_FILENAME
    families = read_families(args.results_root)
    res.detail["families_scanned"] = len(families)
    states = [runtime_state(args.results_root, family_id, doc)
              for family_id, doc in sorted(families.items())]
    res.detail["families_in_flight"] = sum(1 for s in states if s["in_flight"])
    active = [s for s in states if s["in_flight"]]
    if active:
        return res.waiting(
            "active runtime evidence (%s: %s); next candidate waits"
            % (active[0]["family_id"], active[0]["why"]),
            active_family=active[0]["family_id"], active_families=[s["family_id"] for s in active])

    incidents = unresolved_incidents(args.results_root, families)
    if incidents:
        res.finding("unresolved_incident",
                    "%d unresolved incident(s) (contract 12.6): %s"
                    % (len(incidents), json.dumps(incidents[:3], ensure_ascii=False)),
                    incidents=len(incidents))
        res.outcome = "incident"
        return res

    cands, why = read_pool(pool_path)
    if cands is None:
        return res.finding("pool_missing", why, pool=str(pool_path))

    seen_ids = set(families)
    seen_fp = {c["semantic_fingerprint"] for c in families.values()
               if isinstance(c, dict) and c.get("semantic_fingerprint")}
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
        return res.idle("pool %s has %d candidates, all already present in /results (contract 14.3)"
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
    return append_one(args.results_root, args.board, chosen, args, res)


def main():
    ap = argparse.ArgumentParser(description="contract 14.4 automatic production handoff (one round)")
    ap.add_argument("--results-root", default=os.environ.get("QLIB_RESULTS_ROOT", DEFAULT_RESULTS))
    ap.add_argument("--board", default=os.environ.get("HERMES_KANBAN_BOARD", "quant-strategy-research"),
                    help="board the work-order card is dispatched to (never read back)")
    ap.add_argument("--pool", default=None, help="candidate pool JSON (default <results>/_handoff/%s)"
                    % POOL_FILENAME)
    ap.add_argument("--detector", default="handoff", choices=["handoff", "default", "operator"])
    ap.add_argument("--dry-run", action="store_true", help="read-only: no card, no file, no log line")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet-noop", action="store_true", default=True,
                    help="print nothing on stdout for a normal no-op round (cron default)")
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
        "decision_evidence": "results_root_only",
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
    # Always one bounded outcome line on stderr: C3/C4 can tell invocation success (rc) from the
    # pipeline outcome (advanced / running / idle / finding / incident) without new infrastructure.
    sys.stderr.write("production handoff: outcome=%s action=%s — %s\n"
                     % (res.outcome, res.action, res.reason))
    return 0


if __name__ == "__main__":
    sys.exit(main())
