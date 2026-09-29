#!/usr/bin/env python3
"""Automatic production handoff (contract section 14.4) - ONE family per run.

Every decision input is a read-only file contract under the results root:
  * `/results/*/family.json`              = the existing family set (ownership + fingerprint, 10.6)
  * `/results/*/rounds/*/verdict.json`    = contract-terminal round verdicts
  * `/results/*/rounds/*/attempts/*`      = real runtime evidence (run-spec, sentinels, artifacts)
  * `/results/_handoff/candidates.json`   = the reviewed candidate pool (contract 14.4)
  * `/results/_incidents/...`             = fail-closed incident ledger (contract 12.6)
  * `/results/_handoff/handoff_log.jsonl` = append-only record of advance/retry/finding decisions

Normal C3 accepts the reviewed candidate schema directly. If the selected candidate has no
execution_file yet, C3 launches at most one upstream JIT `quant-preparation` Hermes session behind a
kernel lease under _handoff/preparing/<family>; that session may only build/test/freeze the deterministic
runner/spec/manifest and atomically add execution_file. It may not create canonical /results/<family>,
launch Qlib, or write a terminal verdict. On a later tick C3 validates the immutable prepared artifacts,
runs P1-P10 on the staged attempt, and invokes the fixed qlib-run container command directly. The old
Hermes production executor remains available only through the explicit legacy rollback flag. No Kanban
board, dispatcher, card status or task id participates in the hot path.

Legal action: register at most ONE new family per round. Prepared mode registers only after preflight;
once its canonical attempt is materialized, C3 never relaunches it and holds for C4 disposition.
Before an attempt exists, a registered prepared family may retry only with the exact frozen identity;
legacy worker retry remains available only through explicit rollback. Findings are deduplicated.

Runtime guards (all objective, all from results-root artifacts):
  * the family's NEWEST attempt holds the pipeline while it is inside ACTIVE_WINDOW_MINUTES and either
    has published no terminal sentinel (compute is really running) or its OWN round's verdict is still
    missing (the round is not decided). A verdict is per ROUND (contract 7.3/9.4), so the read is
    scoped to that attempt's own `rounds/<round>/verdict.json` and re-validated (family_id /
    kanban_task_id / round_id ownership plus a contract-terminal token); an earlier round's verdict -
    or a malformed / foreign / partial one - never releases a family whose current round still owes the
    pipeline runtime evidence, and counts as missing (fail-closed);
  * a family registered less than LAUNCH_GRACE_MINUTES ago that has produced no runtime evidence
    yet is a launch in flight -> wait;
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
import fcntl
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from terminal_evidence import TERMINALS  # noqa: E402

DEFAULT_RESULTS = "/Volumes/ExpansionDrive/qlib-results"
DEFAULT_WORKSPACE = "/Users/hong/workspace/quant-runtime-pipeline"
AGENT_LOG = "agent.log"
AGENT_LOCK = ".agent.lock"
DIRECT_MODE = "direct_hermes"
# Legacy family-ownership token consumed by P10/C4; it does not mean prepared C3 launched Hermes.
FAMILY_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,199}\Z")
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
PREPARED_EXECUTION_KIND = "prepared_execution"
PREPARATION_DIRNAME = "preparing"
PREPARATION_PROMPT = "prepare-task.md"
PREPARATION_LOG = "prepare.log"
PREPARATION_SOURCE = "quant-preparation"
PREPARED_EXECUTION_PYTHON = "/opt/homebrew/bin/python3"
PREPARED_MANIFEST_MAX_BYTES = 65536
PREPARED_ROUND_SPEC_MAX_BYTES = 1 << 20
PREPARED_RUN_SPEC_MAX_BYTES = 512 << 10
PREPARED_OUTPUT_MAX_BYTES = 65536
PREPARED_EXECUTION_TIMEOUT_S = 300
PREPARED_EVIDENCE_TIMEOUT_S = 12
PREPARED_EVIDENCE_POLL_S = 0.25
PREPARED_STATE_STAGES = ("RUNNING_QLIB", "ARTIFACT_READY", "FAILED_SCRIPT")
SAFE_SPEC_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}\Z")
SAFE_SCRIPT_PATH = re.compile(r"/scripts/([A-Za-z0-9][A-Za-z0-9_-]*\.py)\Z")
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
    "- funding、fee、slippage 若僅作 execution-cost／accounting inputs，而非 registered core "
    "signal/mechanism 的必要內容，則不是 core-signal prerequisites；"
    "execution fees／slippage 依 registered cost assumptions 及 canonical instrument metadata 處理；"
    "derivative funding cost 只使用 canonical official observations，缺少 official "
    "observation 時該 interval 的 funding "
    "cost 為 zero，並須揭露 observation coverage，絕不可用 modeled rows 替代。單獨的 "
    "execution-cost coverage gaps 不得作為 prelaunch TECHNICAL_INCOMPLETE 理由。反之，若 "
    "funding、fee、slippage 本身是 registered core signal/mechanism 的一部分，仍屬 core data，"
    "適用既有 core prerequisite 規則。\n"
    "- 若 candidate 文字中的 source-market exact-match／不得縮減 universe 條款與上述 local-universe"
    "規則衝突，以本 system-owned lifecycle rule 為執行準則；candidate bytes 與 fingerprint 仍不改寫。\n"
    "- shared-layer failure（§12.5）或確實需要 human decision（§12.6）時，"
    "在 canonical incident evidence 明確記載並停止；不得臆造 verdict。\n"
    "- prerequisite 證據有界（Phase 2B）：唯一 canonical 來源是 Common Data Pack 的 "
    "`/data/raw/_meta/CONFIG.json` dataset IDs 與 `/data/raw/_meta/SCHEMA.md` field/layout 宣告，"
    "不建第二套 data registry；評估只讀這兩個 catalog/schema 檔，必要時再對 canonical raw 做"
    "小範圍直接 sample／path 讀取，不掃描無關 host 目錄或整個檔案系統。"
    "CONFIG／SCHEMA 明確表示核心 signal 所必需的 data type／field 不存在（clear-absence）時，"
    "記載 requirement-vs-available 實測事實的 immutable `round-spec.json` ＋ `verdict.json` "
    "即為此 family 的 TECHNICAL_INCOMPLETE 充分 terminal evidence，不得 launch 任何 full backtest。\n"
    "- 明確禁止為證明顯然不存在的資料能力而新增 candidate-specific prerequisite checker"
    "（`runtime/*_prerequisite_check.py`）、repo-wide prerequisite evidence blob、host-wide 掃描、"
    "synthetic fixtures、tamper batteries 或 bespoke validation framework；既有 legacy checker／"
    "evidence 檔案屬不可變歷史證據，不移除、不改寫。CONFIG／SCHEMA 對既有 dataset 是否具備所需 "
    "field／capability 真正 ambiguous 時 fail closed，只對該 canonical dataset 做有界直接 read-back，"
    "不自動擴大 scope；human input 只留給未解 ambiguity，不用於 clear-absence。\n"
)
REVIEWED_CANDIDATE_MARKER = "--- REVIEWED CANDIDATE BODY (verbatim, followed by system lifecycle rules) ---\n"
LIFECYCLE_FOOTER_BOUNDARY = "\n\n---\nLIFECYCLE FOOTER（system-owned"


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


def _terminal_verdict_token(doc, family_id, owner):
    """One parsed `verdict.json` -> its contract-terminal token, else None (fail-closed).

    A verdict counts only when it is a dict whose `family_id` matches and, whenever both sides carry
    an ownership id, whose `kanban_task_id` agrees too: a malformed, foreign or partial verdict can
    never close anything.
    """
    if not isinstance(doc, dict) or doc.get("family_id") != family_id:
        return None
    verdict_owner = doc.get("kanban_task_id")
    if owner is None and any(key in doc for key in ("kanban_task_id", "kanban_board", "task_id")):
        return None  # a direct family cannot be closed by card-owned evidence
    if owner and verdict_owner and verdict_owner != owner:
        return None
    return doc.get("verdict") if doc.get("verdict") in TERMINAL_VERDICTS else None


def family_verdict_token(results_root, family_id, family_doc):
    """The family's contract-terminal round verdict token, else None (results artifacts only).

    Reads `results/<family>/rounds/*/verdict.json` - the artifacts are already there, so no
    registry, DB or state store. The ownership id is read from the *family.json artifact*
    (`kanban_task_id`), never from the board. This is the family-scoped token: it closes a family
    that has no live attempt left and feeds the incident ledger; it must never release a live
    attempt - that is `round_verdict_token` below, because verdict.json is per ROUND.
    """
    rounds = Path(results_root) / family_id / "rounds"
    if not rounds.is_dir():
        return None
    owner = family_doc.get("kanban_task_id") if isinstance(family_doc, dict) else None
    for path in sorted(rounds.glob("*/verdict.json")):
        token = _terminal_verdict_token(_load_json(path), family_id, owner)
        if token:
            return token
    return None


def round_verdict_token(round_dir, family_id, family_doc):
    """The verdict token of ONE round - the newest attempt's own round - else None (fail-closed).

    verdict.json is per ROUND (contract 7.3/9.4), so a release decision about a live attempt must
    read *that attempt's own* round, never an earlier round of the same family. Same ownership
    validation as `family_verdict_token` (`family_id` + `kanban_task_id`, token in
    TERMINAL_VERDICTS) plus the round identity: a verdict whose `round_id` names a different round is
    foreign to this one. A malformed, foreign or partial verdict is treated as *missing*, so the
    round stays undecided and the family keeps holding the pipeline.
    """
    round_dir = Path(round_dir)
    doc = _load_json(round_dir / "verdict.json")
    if isinstance(doc, dict) and doc.get("round_id") not in (None, round_dir.name):
        return None
    owner = family_doc.get("kanban_task_id") if isinstance(family_doc, dict) else None
    return _terminal_verdict_token(doc, family_id, owner)


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
    """One family's runtime state from artifacts only -> dict(family_id, verdict, round_verdict,
    attempt, activity, in_flight, why).

    `in_flight` is the pipeline guard: the family still owes the pipeline something. The newest
    attempt decides first - a terminal verdict is per ROUND (contract 7.3/9.4), so the release
    decision reads the verdict of *that attempt's own round* (`round_verdict_token`), never an
    earlier round's. Inside ACTIVE_WINDOW_MINUTES the family holds while its newest attempt has
    published no terminal sentinel (compute is really running) or while its own round verdict is
    still missing (the round is not decided). A family with no live attempt left is closed by its
    terminal verdict, and one that has produced no runtime evidence yet is a launch in flight for
    LAUNCH_GRACE_MINUTES. Anything outside those windows is stale/abandoned and no longer holds the
    pipeline (this is what keeps a blocked or dead card from freezing production).
    """
    now = time.time() if now is None else now
    state = {"family_id": family_id, "verdict": None, "round_verdict": None, "attempt": None,
             "activity": None, "in_flight": False, "why": ""}
    if not isinstance(family_doc, dict) or family_doc.get("_unparsable"):
        state["why"] = "family.json unreadable"
        state["in_flight"] = True  # fail-closed: an unreadable family is never skipped silently
        return state

    state["verdict"] = verdict = family_verdict_token(results_root, family_id, family_doc)

    attempts = family_attempts(results_root, family_id)
    activity = [(attempt_activity(a) or 0.0, a) for a in attempts]
    activity = [(stamp, a) for stamp, a in activity if stamp]
    if activity:
        stamp, attempt = max(activity, key=lambda item: (item[0], str(item[1])))
        state["attempt"] = str(attempt)
        state["activity"] = stamp
        age_minutes = (now - stamp) / 60.0
        inside = age_minutes <= ACTIVE_WINDOW_MINUTES
        # A published terminal sentinel (any of DONE/FAILED/INCOMPLETE) is the "run finished" signal
        # here - the same one the snapshot's progress uses; strict parsability belongs to the incident
        # gate (`clean_terminal`), not to this liveness heuristic.
        terminals = [t for t in TERMINALS if (attempt / t).is_file()]
        # results/<family>/rounds/<round>/attempts/<run>: parents[1] is this attempt's OWN round.
        # verdict.json is per ROUND, so only that round's verdict can release this attempt; a
        # malformed / foreign / partial verdict reads as missing and keeps the family holding.
        round_verdict = round_verdict_token(attempt.parents[1], family_id, family_doc)
        state["round_verdict"] = round_verdict
        state["in_flight"] = inside and (not terminals or not round_verdict)
        why = "attempt %s last write %.0f min ago" % (attempt.name, age_minutes)
        if not inside:
            why += " (stale: outside the %d min window)" % ACTIVE_WINDOW_MINUTES
            if verdict:
                why += " + terminal verdict %s" % verdict
        elif not terminals:
            why += " (no terminal sentinel: live compute)"
        elif round_verdict:
            why += (" (terminal %s published + this round's terminal verdict %s)"
                    % (terminals[0], round_verdict))
        else:
            why += (" (terminal %s published, this round's verdict still missing)" % terminals[0])
        state["why"] = why
        return state

    if verdict:
        state["why"] = "terminal verdict %s" % verdict
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
    """Canonical incidents plus active family-local prelaunch blockers (contract 12.6).

    Kanban-free replacement for the old "is the incident's card still non-terminal" read: an incident
    is resolved once its own subject - the family (contract-terminal verdict) or the referenced
    attempt (clean terminal sentinel) - has reached terminal evidence. A line without family identity,
    an unparsable line, or a subject that still owes terminal evidence stays open (fail-closed), so a
    real results/reconciliation safety incident still gates the pipeline.
    """
    path = Path(results_root) / INCIDENT_DIRNAME / INCIDENT_FILENAME
    open_incidents = []
    lines = path.read_text().splitlines() if path.is_file() else []
    family_verdicts = {}
    for line in lines:
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
        if isinstance(doc, dict):
            if family_id not in family_verdicts:
                family_verdicts[family_id] = family_verdict_token(results_root, family_id, doc)
            if family_verdicts[family_id]:
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

    def valid_no_compute_terminal(doc, round_dir, family_id, owner, detected_at):
        """Validate the prelaunch-only run_id=null terminal shape from immutable artifacts."""
        round_id = round_dir.name
        if not isinstance(doc, dict) or type(doc.get("schema_version")) is not int \
                or doc.get("schema_version") != 1 or doc.get("family_id") != family_id \
                or not isinstance(round_id, str) or not round_id.strip() \
                or doc.get("round_id") != round_id or "run_id" not in doc \
                or doc.get("run_id") is not None or doc.get("verdict") != "TECHNICAL_INCOMPLETE" \
                or doc.get("performance_claimable") is not False \
                or _terminal_verdict_token(doc, family_id, owner) != "TECHNICAL_INCOMPLETE":
            return False
        decided_at = parse_utc(doc.get("decided_at_utc"))
        if decided_at is None or decided_at <= detected_at:
            return False
        attempts = doc.get("attempts")
        counters = ("launched", "run_specs", "terminal_sentinels")
        null_fields = ("run_spec", "terminal_sentinel", "attempt_dir")
        if not isinstance(attempts, dict) or any(
                type(attempts.get(key)) is not int or attempts[key] != 0 for key in counters) \
                or any(key not in attempts or attempts[key] is not None for key in null_fields):
            return False
        failure = doc.get("failure")
        evidence_run_ids = doc.get("evidence_run_ids")
        if not isinstance(failure, dict) or "last_run_id" not in failure \
                or failure.get("last_run_id") is not None \
                or not isinstance(evidence_run_ids, list) or evidence_run_ids:
            return False
        coverage = doc.get("coverage")
        if not isinstance(coverage, dict) or type(coverage.get("cells_computed")) is not int \
                or coverage.get("cells_computed") != 0:
            return False

        round_spec = _load_json(round_dir / "round-spec.json")
        spec_attempts = round_spec.get("attempts") if isinstance(round_spec, dict) else None
        created_at = parse_utc(round_spec.get("created_at_utc")) \
            if isinstance(round_spec, dict) else None
        if not isinstance(round_spec, dict) \
                or type(round_spec.get("schema_version")) is not int \
                or round_spec.get("schema_version") != 1 \
                or round_spec.get("family_id") != family_id \
                or round_spec.get("round_id") != round_id \
                or created_at is None or created_at < detected_at or created_at > decided_at \
                or not isinstance(spec_attempts, dict) or any(
                    type(spec_attempts.get(key)) is not int or spec_attempts[key] != 0
                    for key in counters):
            return False

        attempts_dir = round_dir / "attempts"
        try:
            if os.path.lexists(str(attempts_dir)) and (
                    attempts_dir.is_symlink() or not attempts_dir.is_dir() or
                    next(attempts_dir.iterdir(), None) is not None):
                return False
        except OSError:
            return False
        return True

    root = Path(results_root)
    for family_id, family_doc in families.items():
        if not isinstance(family_id, str) or not family_id or not isinstance(family_doc, dict) \
                or family_doc.get("_unparsable") or family_doc.get("family_id") != family_id:
            continue
        family_dir = root / family_id
        blocker_path = family_dir / "execution-blocker.json"
        blocker = _load_json(blocker_path)
        if not isinstance(blocker, dict) or blocker.get("document_kind") != \
                "prelaunch_execution_blocker" or blocker.get("family_id") != family_id or \
                blocker.get("status") != "BLOCKED_BEFORE_ROUND_FREEZE" or \
                "round_id" not in blocker or blocker.get("round_id") is not None or \
                "run_id" not in blocker or blocker.get("run_id") is not None or \
                blocker.get("attempt_launched") is not False or blocker.get("qlib_launched") is not False:
            continue
        human_input = blocker.get("required_human_input")
        if not ((isinstance(human_input, str) and human_input.strip()) or
                (isinstance(human_input, (list, dict)) and human_input)):
            continue
        detected_at = parse_utc(blocker.get("detected_at_utc"))
        rounds = family_dir / "rounds"
        owner = family_doc.get("kanban_task_id")
        resolved = False
        if detected_at is not None and rounds.is_dir():
            for verdict_path in rounds.glob("*/verdict.json"):
                verdict = _load_json(verdict_path)
                round_id = verdict_path.parent.name
                run_id = verdict.get("run_id") if isinstance(verdict, dict) else None
                if not isinstance(verdict, dict) or type(verdict.get("schema_version")) is not int \
                        or verdict.get("schema_version") != 1 or verdict.get("family_id") != family_id \
                        or not isinstance(round_id, str) or not round_id.strip() \
                        or verdict.get("round_id") != round_id \
                        or (run_id is not None and (not isinstance(run_id, str) or not run_id.strip())):
                    continue
                if isinstance(run_id, str):
                    token = _terminal_verdict_token(verdict, family_id, owner)
                    decided_at = parse_utc(verdict.get("decided_at_utc"))
                    if token and decided_at is not None and decided_at > detected_at:
                        resolved = True
                        break
                elif "run_id" in verdict and valid_no_compute_terminal(
                        verdict, verdict_path.parent, family_id, owner, detected_at):
                    resolved = True
                    break
        if resolved:
            continue
        incident_id = blocker.get("incident_id")
        if not isinstance(incident_id, str) or not incident_id:
            incident_id = "prelaunch-blocker:%s" % family_id
        human_input_text = human_input.strip() if isinstance(human_input, str) else \
            json.dumps(human_input, ensure_ascii=False, sort_keys=True)
        open_incidents.append({
            "incident_id": incident_id,
            "kind": "prelaunch_execution_blocker",
            "family_id": family_id,
            "why": "prelaunch execution blocker remains BLOCKED_BEFORE_ROUND_FREEZE; "
                   "required human input: %s" % human_input_text[:240],
        })
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


def frozen_retry_prompt(task_path, cand, family_doc):
    """Return the registered frozen prompt only when its immutable candidate identity still matches."""
    try:
        prompt = Path(task_path).read_bytes()
    except OSError:
        return None
    marker = REVIEWED_CANDIDATE_MARKER.encode("utf-8")
    if prompt.count(marker) != 1:
        return None
    body = prompt.split(marker, 1)[1]
    handoff = family_doc.get("handoff")
    body_sha = "sha256:" + hashlib.sha256(body).hexdigest()
    candidate_body = (cand.get("_body") or cand.get("card_body") or "").encode("utf-8")
    if not isinstance(handoff, dict) or handoff.get("body_sha256") != body_sha or \
            not body.startswith(candidate_body + LIFECYCLE_FOOTER_BOUNDARY.encode("utf-8")):
        return None
    try:
        return prompt.decode("utf-8")
    except UnicodeError:
        return None


def direct_family(doc):
    return isinstance(doc, dict) and isinstance(doc.get("handoff"), dict) and \
        doc["handoff"].get("execution") == DIRECT_MODE

def _lock(path):
    """A kernel lease, not a queue/state store. None means another process still owns it."""
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd

def agent_prompt(cand, results_root):
    """Full reviewed specification, with an explicit non-Kanban execution/stop boundary."""
    return (
        "Quant production family %s — %s\n\n" % (cand["family_id"], cand["title"])
        + "Canonical results root: %s; family path: %s/%s. "
          % (results_root, results_root, cand["family_id"])
        + "You are the Hermes DEFAULT execution worker in the fixed repo workspace. There is NO "
          "Kanban task, board or dispatcher. Never invoke any kanban CLI subcommand or kanban_* "
          "tool, and do not invent a task id. Read the immutable family.json and this frozen "
          "reviewed body before acting. "
          "Adapt/implement the strategy only when necessary, preserve the registered hypothesis and "
          "candidate bytes, verify the local eligible universe and required data against the canonical "
          "raw catalog, and freeze the round/run specifications BEFORE compute. The existing strategy "
          "scripts are examples, not licenses to reuse historical task IDs or query the board DB. "
          "For a direct family omit kanban_task_id/kanban_board/task_id from new specs and evidence; "
          "use family_id+round_id+run_id for ownership. Run tests and P1–P10 preflight before "
          "launching Qlib. If required core data is objectively absent, write a legitimate "
          "TECHNICAL_INCOMPLETE round verdict with measured bounded evidence instead of a backtest. "
          "Otherwise launch Qlib with existing `container exec -d qlib-run` semantics and then EXIT "
          "this one-shot session; do not wait for the long computation. The existing n8n C4 cadence "
          "will resume host-side disposition after compute finishes. If you cannot safely proceed, "
          "record the exact gap; never manufacture a PASS or consume another family.\n\n"
        + REVIEWED_CANDIDATE_MARKER
        + body_with_footer(cand)
    )

def disposition_prompt(family_id, round_id, run_id, results_root):
    return (
        "Hermes DEFAULT host-side disposition for direct quant family %s, round %s, attempt %s. "
        "Canonical results root: %s. "
        "No Kanban task/board exists; NEVER use any kanban CLI subcommand or kanban_* tool. In the fixed repo, "
        "read family.json, frozen round/run specs, attempt state, engine artifacts and the frozen "
        "agent-task.md. Verify the authoritative attempt identity and actual compute completion. "
        "For ARTIFACT_READY/FAILED_SCRIPT, publish the host-side DONE/FAILED/INCOMPLETE sentinel "
        "with runtime/terminal_evidence.py only after the appropriate checks; do not equate "
        "ARTIFACT_READY with success. If terminal exists, verify its manifest and do not republish. "
        "Then write the contract-terminal verdict for THIS round (or safely retry within the same "
        "family/round if warranted). When the round passes with at least one cohort survivor, "
        "also freeze them with runtime/survivor_bundle.py (a direct family's bundle carries no "
        "card ids) so the post-survivor index can pick the result up. Never rerun a terminal "
        "attempt, never call the board, and "
        "never advance another candidate. If evidence is ambiguous, document it and stop. "
        "Once this round is terminal and verdict is durable, EXIT.\n"
        % (family_id, round_id, run_id, results_root)
    )

def _launch_hermes_session(lease_dir, name, prompt, skills=(), workspace=DEFAULT_WORKSPACE,
                           source="quant-production", log_name=AGENT_LOG):
    """Launch one detached Hermes session behind a kernel flock lease."""
    lease_dir = Path(lease_dir)
    lease_dir.mkdir(parents=True, exist_ok=True)
    fd = _lock(lease_dir / AGENT_LOCK)
    if fd is None:
        return None, None, True
    try:
        task = lease_dir / name
        data = prompt.encode("utf-8")
        try:
            out = os.open(str(task), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if task.read_bytes() != data:
                return None, "frozen agent prompt differs on retry: %s" % task, False
        else:
            try:
                os.write(out, data)
                os.fsync(out)
            finally:
                os.close(out)
        cmd = ["hermes", "-p", "default", "--cli", "--accept-hooks"]
        for skill in skills:
            cmd += ["--skills", skill]
        cmd += ["chat", "--query-file", str(task), "--in", workspace,
                "--max-turns", "500", "--run-budget", "7200", "--source", source]
        env = {"HOME": "/Users/hong", "PATH": "/Users/hong/.local/bin:/opt/homebrew/bin:/usr/bin:/bin",
               "LANG": "en_US.UTF-8"}
        with open(lease_dir / log_name, "ab") as log:
            proc = subprocess.Popen(cmd, cwd=workspace, env=env, stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=subprocess.STDOUT, pass_fds=(fd,),
                                    start_new_session=True)
        return proc.pid, None, False
    except (OSError, ValueError) as exc:
        return None, "Hermes launch failed: %s" % exc, False
    finally:
        os.close(fd)


def launch_agent(results_root, family_id, name, prompt, skills=(), workspace=DEFAULT_WORKSPACE):
    """Legacy production-agent launcher retained only for explicit rollback mode."""
    return _launch_hermes_session(Path(results_root) / family_id, name, prompt, skills, workspace,
                                  source="quant-production", log_name=AGENT_LOG)


def preparation_prompt(cand, results_root, pool_path):
    provenance = cand.get("provenance") if isinstance(cand.get("provenance"), dict) else {}
    reviewed_locator = provenance.get("reviewed_wiki_path") or provenance.get("reviewed_source") or ""
    return (
        "JIT PREPARATION ONLY for reviewed quant candidate %s — %s.\n\n" %
        (cand["family_id"], cand["title"])
        + "This session is upstream preparation owned by n8n C3. It is NOT a production/backtest "
          "executor. Candidate pool: %s. Canonical results root: %s. Workspace: %s.\n" %
          (pool_path, results_root, cand.get("workspace_path") or DEFAULT_WORKSPACE)
        + "Frozen candidate fingerprint_input: %s\n" % cand["fingerprint_input"]
        + "Canonical reviewed research locator: %s\n" % reviewed_locator
        + "Read the exact existing candidate object, its card body, provenance.reviewed_wiki_path when "
          "present (otherwise provenance.reviewed_source), and "
          "the deployed prepared-execution contract in runtime/production_handoff.py plus "
          "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md. Preserve family_id, fingerprint_input, "
          "candidate body, provenance, lineage and research decision exactly; do not re-review or "
          "re-rank the strategy.\n\n"
        + "Produce the smallest source-faithful deterministic execution package required for n8n C3: "
          "implement/reuse one strategy runner under container/scripts, add only focused tests, deploy "
          "a byte-identical copy under /Users/hong/workspace/qlib-apple-container/scripts, and stage "
          "immutable round-spec.json, run-spec.json and prepared-execution manifest under "
          "%s/_handoff/prepared/%s/. Run focused tests and P1-P10 preflight. If source-required "
          "data/model/assets are objectively absent, do not build new infrastructure or substitute "
          "another market/model; make the runner emit bounded prerequisite evidence downstream.\n\n"
          % (results_root, cand["family_id"])
        + "HARD STOP BOUNDARY: do NOT create %s/%s, do NOT run container exec qlib-run, do NOT "
          "write terminal sentinel/verdict/performance evidence, and do NOT launch any quant-production "
          "Hermes session. This preparation session ends after the staged package passes P1-P10 and "
          "the existing candidate object is atomically updated by adding ONLY its validated absolute "
          "execution_file. Same candidate order and every other field/byte must remain semantically "
          "unchanged. Re-read and validate after the write.\n\n"
          % (results_root, cand["family_id"])
        + "Use existing runner/spec/test conventions; do not add a scheduler, manager, registry, queue, "
          "state machine, generic compiler or second data registry. Stage/commit only files belonging "
          "to this candidate; never clean, reset or absorb unrelated workspace dirt. If safe preparation "
          "cannot be completed, record the exact blocker in this preparation log and EXIT without "
          "modifying execution_file.\n\n"
        + REVIEWED_CANDIDATE_MARKER + body_with_footer(cand)
    )


def launch_preparation_agent(results_root, cand, pool_path):
    """One JIT prepare-only Hermes session; its lease lives outside canonical /results/<family>."""
    lease_dir = Path(results_root) / HANDOFF_DIRNAME / PREPARATION_DIRNAME / cand["family_id"]
    return _launch_hermes_session(
        lease_dir, PREPARATION_PROMPT,
        preparation_prompt(cand, results_root, pool_path),
        cand.get("skills") or (), cand.get("workspace_path") or DEFAULT_WORKSPACE,
        source=PREPARATION_SOURCE, log_name=PREPARATION_LOG)


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


def write_family_json(results_root, cand, prepared_identity=None):
    """Immutable family identity; new families have no Kanban ownership fields."""
    doc = {
        "schema_version": 1,
        "family_id": cand["family_id"],
        "semantic_fingerprint": fingerprint(cand["fingerprint_input"]),
        "fingerprint_input": cand["fingerprint_input"],
        "parent_family": cand.get("parent_family"),
        "lineage_note": cand.get("lineage_note") or "",
        "created_at_utc": now_utc(),
        "handoff": {"source": "production_handoff", "contract_section": "14.4",
                    "execution": DIRECT_MODE, "body_sha256": fingerprint(body_with_footer(cand)),
                    "decision_evidence": "results_root_only",
                    "pool_fingerprint_source": cand.get("provenance", {}).get("reviewed_source")},
    }
    if prepared_identity is not None:
        doc["handoff"]["prepared_execution"] = dict(prepared_identity)
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


def _sha256(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _read_prepared_file(value, base, max_bytes, expected=None):
    """Read one bounded regular file without following a final-component symlink."""
    if not isinstance(value, str) or not value:
        raise ValueError("prepared file path must be a non-empty string")
    path = Path(value)
    if not path.is_absolute() or path.is_symlink():
        raise ValueError("prepared file path must be absolute and non-symlink")
    resolved = path.resolve(strict=True)
    base = Path(base).resolve(strict=True)
    try:
        relative = resolved.relative_to(base)
    except ValueError:
        raise ValueError("prepared file escapes %s" % base)
    if not relative.parts:
        raise ValueError("prepared file path must name a file under %s" % base)
    if expected is not None and resolved != Path(expected).resolve(strict=True):
        raise ValueError("prepared file does not match the required mirrored path")
    current = base
    for component in relative.parts[:-1]:
        current = current / component
        parent_info = os.lstat(str(current))
        if stat.S_ISLNK(parent_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode):
            raise ValueError("prepared file parents must be non-symlink directories")
    info = os.lstat(str(resolved))
    if stat.S_ISLNK(info.st_mode):
        raise ValueError("prepared file path must not contain symlinks")
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("prepared file must be a regular non-symlink file")
    if info.st_size > max_bytes:
        raise ValueError("prepared file exceeds %d bytes" % max_bytes)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(path), flags)
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or \
                (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError("prepared file changed to a non-regular file")
        with os.fdopen(fd, "rb") as stream:
            fd = -1
            raw = stream.read(max_bytes + 1)
    finally:
        if fd != -1:
            os.close(fd)
    if len(raw) > max_bytes:
        raise ValueError("prepared file exceeds %d bytes" % max_bytes)
    return resolved, raw


def _nonempty_ownership(doc):
    return [key for key in ("kanban_task_id", "kanban_board", "task_id")
            if key in doc and doc[key] is not None and doc[key] != ""]


def _prepared_identity(prepared):
    return {
        "manifest_path": prepared["manifest_path"],
        "manifest_sha256": prepared["manifest_sha256"],
        "round_spec_sha256": prepared["round_spec_sha256"],
        "run_spec_sha256": prepared["run_spec_sha256"],
        "round_id": prepared["round_id"],
        "run_id": prepared["run_id"],
        "script_path": prepared["script_path"],
        "script_sha256": prepared["script_sha256"],
    }


def _prepared_execution(cand, args):
    """Validate immutable staged specs and fixed Qlib script identity before canonical mutation."""
    execution_file = cand.get("execution_file")
    if not isinstance(execution_file, str) or not execution_file.strip():
        return None, ("candidate_preparation_required",
                      "candidate %s has no deterministic execution_file; Research/Intake must "
                      "prepare frozen execution artifacts first" % cand["family_id"])
    try:
        family_id = cand["family_id"]
        if not FAMILY_ID.fullmatch(family_id):
            raise ValueError("invalid family_id")
        results_root = Path(args.results_root).resolve(strict=True)
        handoff_root = results_root / HANDOFF_DIRNAME
        prepared_root = handoff_root / "prepared"
        for directory in (handoff_root, prepared_root):
            info = os.lstat(str(directory))
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise ValueError("prepared staging parents must be non-symlink directories")
        family_dir = prepared_root / family_id
        if family_dir.is_symlink() or not family_dir.is_dir():
            raise ValueError("prepared family directory must be a regular directory")
        family_dir = family_dir.resolve(strict=True)
        manifest_path, manifest_bytes = _read_prepared_file(
            execution_file, family_dir, PREPARED_MANIFEST_MAX_BYTES)
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        required = {"schema_version", "document_kind", "family_id", "semantic_fingerprint",
                    "round_id", "run_id", "round_spec_file", "round_spec_sha256",
                    "run_spec_file", "run_spec_sha256"}
        if not isinstance(manifest, dict) or set(manifest) != required:
            raise ValueError("manifest must contain exactly %s" % sorted(required))
        if type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1 or \
                manifest.get("document_kind") != PREPARED_EXECUTION_KIND:
            raise ValueError("manifest schema_version/document_kind mismatch")
        if manifest.get("family_id") != family_id or \
                manifest.get("semantic_fingerprint") != fingerprint(cand["fingerprint_input"]):
            raise ValueError("prepared_execution_mismatch: family/fingerprint mismatch")
        round_id, run_id = manifest.get("round_id"), manifest.get("run_id")
        if not isinstance(round_id, str) or not SAFE_SPEC_ID.fullmatch(round_id) or \
                round_id in (".", "..") or not isinstance(run_id, str) or \
                not SAFE_SPEC_ID.fullmatch(run_id) or run_id in (".", ".."):
            raise ValueError("round_id/run_id must be safe path components")
        round_path = family_dir / "rounds" / round_id / "round-spec.json"
        run_path = family_dir / "rounds" / round_id / "attempts" / run_id / "run-spec.json"
        round_file, round_bytes = _read_prepared_file(
            manifest.get("round_spec_file"), family_dir, PREPARED_ROUND_SPEC_MAX_BYTES,
            expected=round_path)
        run_file, run_bytes = _read_prepared_file(
            manifest.get("run_spec_file"), family_dir, PREPARED_RUN_SPEC_MAX_BYTES,
            expected=run_path)
        round_hash, run_hash = _sha256(round_bytes), _sha256(run_bytes)
        if manifest.get("round_spec_sha256") != round_hash or \
                manifest.get("run_spec_sha256") != run_hash:
            raise ValueError("prepared spec SHA256 mismatch")
        round_spec = json.loads(round_bytes.decode("utf-8"))
        run_spec = json.loads(run_bytes.decode("utf-8"))
        if not isinstance(round_spec, dict) or type(round_spec.get("schema_version")) is not int or \
                round_spec.get("schema_version") != 1 or round_spec.get("family_id") != family_id or \
                round_spec.get("round_id") != round_id:
            raise ValueError("round-spec family/round identity mismatch")
        if not isinstance(run_spec, dict) or type(run_spec.get("schema_version")) is not int or \
                run_spec.get("schema_version") != 1 or run_spec.get("family_id") != family_id or \
                run_spec.get("round_id") != round_id or run_spec.get("run_id") != run_id:
            raise ValueError("run-spec family/round/run identity mismatch")
        round_ownership, run_ownership = (_nonempty_ownership(round_spec),
                                          _nonempty_ownership(run_spec))
        if round_ownership or run_ownership:
            raise ValueError("prepared specs contain nonempty ownership fields: %s" %
                             sorted(set(round_ownership + run_ownership)))
        script = run_spec.get("script")
        if not isinstance(script, dict):
            raise ValueError("run-spec.script must contain path and sha256")
        script_path, script_sha256 = script.get("path"), script.get("sha256")
        if not isinstance(script_path, str) or not SAFE_SCRIPT_PATH.fullmatch(script_path):
            raise ValueError("script.path must be an absolute /scripts/<safe filename>.py path")
        if not isinstance(script_sha256, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", script_sha256):
            raise ValueError("script.sha256 must be sha256:<64 lowercase hex>")
        prepared = {
            "manifest_path": str(manifest_path),
            "manifest_sha256": _sha256(manifest_bytes),
            "round_spec_path": str(round_file),
            "round_spec_sha256": round_hash,
            "run_spec_path": str(run_file),
            "run_spec_sha256": run_hash,
            "round_id": round_id,
            "run_id": run_id,
            "script_path": script_path,
            "script_sha256": script_sha256,
            "round_spec_bytes": round_bytes,
            "run_spec_bytes": run_bytes,
            "prepared_family_dir": str(family_dir),
            "staged_attempt_dir": str(run_file.parent),
        }
        prepared["identity"] = _prepared_identity(prepared)
        return prepared, None
    except (OSError, UnicodeError, ValueError, RecursionError, TypeError) as exc:
        message = str(exc)
        kind = "prepared_execution_mismatch" if message.startswith("prepared_execution_mismatch:") else \
            "prepared_execution_invalid"
        return None, (kind, message)


def prepared_execution_hold(cand, args, res, existing=None):
    """Validate prepared execution or launch one JIT prepare-only session when it is absent."""
    if not getattr(args, "require_prepared_execution", True):
        return None
    if not isinstance(cand.get("execution_file"), str) or not cand.get("execution_file", "").strip():
        if existing is not None:
            return res.finding("registered_candidate_changed",
                               "registered direct family lost its execution_file",
                               pool_entry=cand["family_id"])
        pool_path = Path(args.pool) if args.pool else Path(args.results_root) / HANDOFF_DIRNAME / POOL_FILENAME
        res.family_id = cand["family_id"]
        if args.dry_run:
            return res.waiting("candidate %s requires JIT preparation; dry-run launches nothing" %
                               cand["family_id"], preparation_required=True,
                               preparation_source=PREPARATION_SOURCE)
        pid, why, busy = launch_preparation_agent(args.results_root, cand, str(pool_path))
        if busy:
            return res.waiting("JIT preparation still owns candidate %s; C3 waits" % cand["family_id"],
                               preparation_required=True, preparation_source=PREPARATION_SOURCE)
        if why:
            return res.finding("candidate_preparation_launch_failed",
                               "%s; candidate remains retryable" % why,
                               pool_entry=cand["family_id"])
        return res.waiting("JIT preparation launched for candidate %s (pid=%s); next C3 tick will "
                           "validate execution_file before Qlib" % (cand["family_id"], pid),
                           preparation_required=True, preparation_pid=pid,
                           preparation_source=PREPARATION_SOURCE)
    prepared, problem = _prepared_execution(cand, args)
    if problem:
        key, reason = problem
        if existing is not None:
            return res.finding("registered_candidate_changed",
                               "prepared_execution_mismatch: %s" % reason,
                               pool_entry=cand["family_id"],
                               execution_file=cand.get("execution_file"))
        return res.finding(key, reason, pool_entry=cand["family_id"],
                           execution_file=cand.get("execution_file"))
    cand["_prepared_execution"] = prepared
    return None


def _bounded_text(data, limit=PREPARED_OUTPUT_MAX_BYTES):
    if isinstance(data, str):
        data = data.encode("utf-8", "replace")
    data = data or b""
    return data[:limit].decode("utf-8", "replace")


def _run_bounded_process(cmd, cwd, env, timeout=PREPARED_EXECUTION_TIMEOUT_S):
    """Run a fixed argv in its own process group; timeout kills the whole group."""
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            start_new_session=True)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(proc.pid, sig)
            except ProcessLookupError:
                break
            if sig == signal.SIGTERM:
                time.sleep(0.2)
        try:
            out, err = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            out, err = proc.communicate()
        raise subprocess.TimeoutExpired(cmd, timeout, output=_bounded_text(out),
                                        stderr=_bounded_text(err)) from None
    return proc.returncode, _bounded_text(out), _bounded_text(err)


def _immutable_write(path, raw):
    """Create a file once, accepting only an exact-byte retry."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(str(path), flags, 0o644)
    except FileExistsError:
        info = os.lstat(str(path))
        if not stat.S_ISREG(info.st_mode) or path.is_symlink() or path.read_bytes() != raw:
            raise ValueError("immutable prepared artifact differs: %s" % path)
        return False
    try:
        with os.fdopen(fd, "wb") as stream:
            fd = -1
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if fd != -1:
            os.close(fd)
    return True


def _staged_family_marker(prepared, family_id):
    """Supply the existing P10 direct-family ownership marker only in the prepared staging tree."""
    marker = Path(prepared["prepared_family_dir"]) / "family.json"
    raw = (json.dumps({"schema_version": 1, "family_id": family_id,
                       "handoff": {"execution": DIRECT_MODE}}, sort_keys=True) + "\n").encode()
    if os.path.lexists(str(marker)):
        info = os.lstat(str(marker))
        if not stat.S_ISREG(info.st_mode) or marker.is_symlink():
            raise ValueError("staged family.json must be a regular non-symlink file")
        try:
            doc = json.loads(marker.read_text())
        except (OSError, UnicodeError, ValueError) as exc:
            raise ValueError("staged family.json is invalid: %s" % exc)
        if not isinstance(doc, dict) or doc.get("family_id") != family_id or \
                not isinstance(doc.get("handoff"), dict) or \
                doc["handoff"].get("execution") != DIRECT_MODE:
            raise ValueError("staged family.json does not identify this direct family")
        return
    _immutable_write(marker, raw)


def _run_staged_preflight(prepared, family_id):
    try:
        _staged_family_marker(prepared, family_id)
        cmd = [PREPARED_EXECUTION_PYTHON,
               str(Path(__file__).resolve().parent / "preflight.py"),
               "--launch", "--attempt-dir", prepared["staged_attempt_dir"], "--json"]
        env = {"HOME": "/Users/hong",
               "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin",
               "LANG": "en_US.UTF-8"}
        rc, out, err = _run_bounded_process(cmd, DEFAULT_WORKSPACE, env)
        report = json.loads(out)
        checks = {item.get("id"): item.get("status") for item in report.get("checks", [])
                  if isinstance(item, dict)} if isinstance(report, dict) else {}
        if rc != 0 or not isinstance(report, dict) or report.get("overall") != "PASS" or \
                report.get("launch_gate") != "evaluated" or \
                checks.get("P9") != "PASS" or checks.get("P10") != "PASS":
            return "P1-P10 preflight failed (rc=%s, overall=%s, launch_gate=%s, P9=%s, P10=%s): %s" % (
                rc, report.get("overall") if isinstance(report, dict) else "invalid-json",
                report.get("launch_gate") if isinstance(report, dict) else "unknown",
                checks.get("P9"), checks.get("P10"), err[-1000:])
        return None
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired) as exc:
        return "P1-P10 preflight failed: %s" % exc


def _materialize_canonical_specs(results_root, family_id, prepared):
    root = Path(results_root).resolve(strict=True)
    round_dir = root / family_id / "rounds" / prepared["round_id"]
    attempt_dir = round_dir / "attempts" / prepared["run_id"]
    for directory in (root / family_id, root / family_id / "rounds",
                      round_dir, round_dir / "attempts", attempt_dir):
        if os.path.lexists(str(directory)):
            info = os.lstat(str(directory))
            if not stat.S_ISDIR(info.st_mode) or directory.is_symlink():
                raise ValueError("canonical attempt path contains a non-directory: %s" % directory)
        else:
            directory.mkdir()
    _immutable_write(round_dir / "round-spec.json", prepared["round_spec_bytes"])
    _immutable_write(attempt_dir / "run-spec.json", prepared["run_spec_bytes"])
    return round_dir, attempt_dir


def _valid_prepared_state(attempt_dir, family_id, round_id, run_id):
    path = Path(attempt_dir) / "state.json"
    try:
        info = os.lstat(str(path))
        if not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_size > 65536:
            return False
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(str(path), flags)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return False
            with os.fdopen(fd, "rb") as stream:
                fd = -1
                doc = json.loads(stream.read(65537).decode("utf-8"))
        finally:
            if fd != -1:
                os.close(fd)
    except (OSError, UnicodeError, ValueError, RecursionError):
        return False
    return isinstance(doc, dict) and doc.get("family_id") == family_id and \
        doc.get("round_id") == round_id and doc.get("run_id") == run_id and \
        doc.get("stage") in PREPARED_STATE_STAGES


def _wait_for_prepared_state(attempt_dir, family_id, round_id, run_id):
    deadline = time.monotonic() + PREPARED_EVIDENCE_TIMEOUT_S
    while True:
        if _valid_prepared_state(attempt_dir, family_id, round_id, run_id):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(min(PREPARED_EVIDENCE_POLL_S,
                       max(0.0, deadline - time.monotonic())))


def _run_prepared_execution(cand, args, res, existing=None, family_path=None):
    prepared = cand.get("_prepared_execution")
    if not isinstance(prepared, dict):
        return res.finding("prepared_execution_invalid",
                           "validated prepared execution manifest/specs missing at dispatch",
                           attempted_family=cand["family_id"])
    identity = prepared["identity"]
    if existing:
        handoff = existing.get("handoff") if isinstance(existing, dict) else None
        frozen = handoff.get("prepared_execution") if isinstance(handoff, dict) else None
        if frozen != identity:
            return res.finding("registered_candidate_changed",
                               "prepared_execution_mismatch: manifest/spec/script identity differs "
                               "from family.json freeze", attempted_family=cand["family_id"])
        if handoff.get("body_sha256") != fingerprint(body_with_footer(cand)):
            return res.finding("registered_candidate_changed", "frozen candidate differs on retry",
                               attempted_family=cand["family_id"])
    preflight_problem = _run_staged_preflight(prepared, cand["family_id"])
    if preflight_problem:
        return res.finding("prepared_execution_preflight_failed", preflight_problem,
                           attempted_family=cand["family_id"],
                           execution_file=prepared["manifest_path"])
    current, problem = _prepared_execution(cand, args)
    if problem or current.get("identity") != identity:
        return res.finding("registered_candidate_changed" if existing else "prepared_execution_mismatch",
                           "prepared_execution_mismatch: prepared artifacts changed during preflight%s" %
                           (": %s" % problem[1] if problem else ""),
                           attempted_family=cand["family_id"])
    prepared = current
    if existing:
        doc, path = existing, family_path
    else:
        try:
            doc, path = write_family_json(args.results_root, cand, identity)
        except (OSError, ValueError) as exc:
            return res.finding("family_json_failed", str(exc), attempted_family=cand["family_id"])
    family_path = path
    try:
        _round_dir, attempt_dir = _materialize_canonical_specs(
            args.results_root, cand["family_id"], prepared)
    except (OSError, ValueError) as exc:
        return res.finding("prepared_execution_materialization_failed", str(exc),
                           attempted_family=cand["family_id"])
    container_attempt = str(Path("/results") / cand["family_id"] / "rounds" /
                            prepared["round_id"] / "attempts" / prepared["run_id"])
    container_run_spec = container_attempt + "/run-spec.json"
    cmd = ["/usr/local/bin/container", "exec", "-d", "qlib-run", "/opt/venv/bin/python",
           prepared["script_path"], "--run-spec", container_run_spec,
           "--attempt-dir", container_attempt]
    env = {"HOME": "/Users/hong",
           "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin",
           "LANG": "en_US.UTF-8"}
    try:
        rc, out, err = _run_bounded_process(cmd, DEFAULT_WORKSPACE, env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return res.finding("prepared_execution_failed",
                           "%s; canonical attempt materialized, C4 must resolve it" % exc,
                           attempted_family=cand["family_id"],
                           execution_file=prepared["manifest_path"])
    if rc != 0:
        return res.finding("prepared_execution_failed",
                           "container exec rc=%d stderr=%s; canonical attempt materialized, "
                           "C4 must resolve it" % (rc, err[-1000:]),
                           attempted_family=cand["family_id"],
                           execution_file=prepared["manifest_path"])
    if not _wait_for_prepared_state(attempt_dir, cand["family_id"],
                                    prepared["round_id"], prepared["run_id"]):
        return res.finding("prepared_execution_no_evidence",
                           "container exec rc=0 but no matching RUNNING_QLIB/ARTIFACT_READY/FAILED_SCRIPT "
                           "state.json appeared",
                           attempted_family=cand["family_id"],
                           execution_file=prepared["manifest_path"])
    res.detail["semantic_fingerprint"] = doc["semantic_fingerprint"]
    res.detail["family_json"] = family_path
    res.detail["execution_file"] = prepared["manifest_path"]
    if existing:
        res.action, res.outcome = "retried", "running"
        res.reason = "registered family %s retried and Qlib launch evidence verified" % cand["family_id"]
    else:
        res.action, res.outcome = "appended", "advanced"
        res.reason = "family %s registered (%s) and fixed Qlib command produced launch evidence" % (
            cand["family_id"], family_path)
    return res


def append_one(results_root, cand, args, res, existing=None):
    """Register once, then run only the frozen prepared launch or explicit legacy rollback."""
    if args.dry_run:
        res.action = "would_retry" if existing else "would_append"
        res.outcome = "running" if existing else "advanced"
        res.reason = "all runtime gates pass; would %s family %s" % (
            "retry" if existing else "advance", cand["family_id"])
        res.family_id = cand["family_id"]
        return res

    res.family_id = cand["family_id"]
    prepared_mode = getattr(args, "require_prepared_execution", True)
    if existing:
        doc = existing
        path = str(Path(results_root) / cand["family_id"] / "family.json")
        if doc.get("semantic_fingerprint") != fingerprint(cand["fingerprint_input"]):
            return res.finding("registered_candidate_changed", "frozen candidate differs on retry",
                               attempted_family=cand["family_id"])
        if prepared_mode:
            if (doc.get("handoff") or {}).get("body_sha256") != fingerprint(body_with_footer(cand)):
                return res.finding("registered_candidate_changed", "frozen candidate differs on retry",
                                   attempted_family=cand["family_id"])
            return _run_prepared_execution(cand, args, res, existing=doc, family_path=path)
        else:
            task_path = Path(results_root) / cand["family_id"] / "agent-task.md"
            if os.path.lexists(str(task_path)):
                prompt = frozen_retry_prompt(task_path, cand, doc)
                if prompt is None:
                    return res.finding("registered_candidate_changed", "frozen candidate differs on retry",
                                       attempted_family=cand["family_id"])
            else:
                if (doc.get("handoff") or {}).get("body_sha256") != fingerprint(body_with_footer(cand)):
                    return res.finding("registered_candidate_changed", "frozen candidate differs on retry",
                                       attempted_family=cand["family_id"])
                prompt = agent_prompt(cand, results_root)
    else:
        if prepared_mode:
            return _run_prepared_execution(cand, args, res)
        try:
            doc, path = write_family_json(results_root, cand)
        except (OSError, ValueError) as exc:
            return res.finding("family_json_failed", str(exc), attempted_family=cand["family_id"])
        prompt = None if prepared_mode else agent_prompt(cand, results_root)

    res.detail["semantic_fingerprint"] = doc["semantic_fingerprint"]
    res.detail["family_json"] = path
    pid, why, busy = launch_agent(results_root, cand["family_id"], "agent-task.md",
                                  prompt, cand.get("skills") or (),
                                  cand.get("workspace_path") or DEFAULT_WORKSPACE)
    if busy:
        return res.waiting("direct Hermes worker still owns family %s; next candidate waits"
                           % cand["family_id"], active_family=cand["family_id"])
    if why:
        return res.finding("agent_launch_failed", "%s; same family remains retryable" % why,
                           attempted_family=cand["family_id"])
    res.detail["agent_pid"] = pid
    if existing:
        res.action = "retried"
        res.outcome = "running"
        res.reason = "registered family %s retried; default Hermes worker launched (pid=%s)" \
            % (cand["family_id"], pid)
    else:
        res.action = "appended"
        res.outcome = "advanced"
        res.reason = ("family %s registered (%s) and default Hermes worker launched (pid=%s)"
                      % (cand["family_id"], path, pid))
    return res

def _round_once(args):
    res = Round()
    root = Path(args.results_root)
    prepared_mode = getattr(args, "require_prepared_execution", True)
    if not root.is_dir():
        return res.finding("results_root_missing", "results root not found: %s" % args.results_root)

    pool_path = Path(args.pool) if args.pool else root / HANDOFF_DIRNAME / POOL_FILENAME
    families = read_families(args.results_root)
    res.detail["families_scanned"] = len(families)
    states = [runtime_state(args.results_root, family_id, doc)
              for family_id, doc in sorted(families.items())]
    by_family = {s["family_id"]: s for s in states}
    res.detail["families_in_flight"] = sum(1 for s in states if s["in_flight"])
    # A prepared canonical attempt is a one-shot launch boundary: even if stale, C3 leaves its
    # disposition to C4 instead of duplicating Qlib. Legacy direct families keep the existing window.
    def prepared_attempt(state):
        doc = families[state["family_id"]]
        handoff = doc.get("handoff") if isinstance(doc, dict) else None
        return isinstance(handoff, dict) and isinstance(handoff.get("prepared_execution"), dict)

    active = [s for s in states if
              (s["in_flight"] or (s["attempt"] is not None and not s["round_verdict"] and
                                  prepared_attempt(s))) and
              (s["attempt"] is not None or not direct_family(families[s["family_id"]]))]
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
        if not isinstance(cand, dict):
            return res.finding("pool_invalid", "candidate must be an object")
        fid, fin = cand.get("family_id"), cand.get("fingerprint_input")
        if not isinstance(fid, str) or not FAMILY_ID.fullmatch(fid) or \
                not isinstance(fin, str) or not fin or not isinstance(cand.get("title"), str) or \
                not isinstance(card_body(pool_path, cand), str) or not card_body(pool_path, cand) or \
                not isinstance(cand.get("skills") or [], list) or \
                any(not isinstance(s, str) or not s for s in cand.get("skills") or []):
            return res.finding("pool_invalid", "candidate missing family_id/fingerprint_input/"
                               "card_body(_file)/title or unsupported execution target: %s"
                               % json.dumps(cand.get("family_id")))
        if fid in pool_ids or fingerprint(fin) in pool_fps:
            return res.finding("ambiguous_pool", "pool repeats candidate %s / fingerprint" % fid)
        pool_ids.add(fid)
        pool_fps.add(fingerprint(fin))

    pending = [(fid, doc) for fid, doc in sorted(families.items()) if direct_family(doc) and
               not (by_family[fid]["round_verdict"] if by_family[fid]["attempt"]
                    else by_family[fid]["verdict"])]
    if pending:
        fid, doc = pending[0]
        if by_family[fid]["attempt"]:
            return res.waiting("direct family %s has an undecided attempt; C4 must dispose of it "
                               "before next candidate" % fid, active_family=fid)
        cand = next((c for c in cands if c["family_id"] == fid), None)
        if cand is None:
            return res.finding("registered_candidate_missing", "direct family %s absent from pool" % fid)
        chosen = dict(cand)
        chosen["_body"] = card_body(pool_path, chosen)
        workspace = chosen.get("workspace_path") or DEFAULT_WORKSPACE
        if not prepared_mode and (chosen.get("assignee", "default") != "default" or
                                  not isinstance(workspace, str) or not os.path.isabs(workspace) or
                                  not os.path.isdir(workspace)):
            return res.finding("unsupported_execution_target", "registered candidate %s has no "
                               "usable default-worker workspace" % fid)
        if any(m not in chosen["_body"].upper() for m in CANDIDATE_BODY_MARKERS):
            return res.finding("candidate_body_not_v13", "registered candidate lost v1.3 body: %s" % fid)
        hold = prepared_execution_hold(chosen, args, res, existing=doc)
        if hold is not None:
            return hold
        return append_one(args.results_root, chosen, args, res, existing=doc)

    eligible = [c for c in cands
                if c["family_id"] not in seen_ids
                and not (root / c["family_id"]).exists()
                and fingerprint(c["fingerprint_input"]) not in seen_fp]
    if not eligible:
        return res.idle("pool %s has %d candidates, all already present in /results (contract 14.3)"
                        % (pool_path, len(cands)), pool=str(pool_path))
    chosen = dict(eligible[0])
    chosen["_body"] = card_body(pool_path, chosen)
    workspace = chosen.get("workspace_path") or DEFAULT_WORKSPACE
    if not prepared_mode and (chosen.get("assignee", "default") != "default" or
                              not isinstance(workspace, str) or not os.path.isabs(workspace) or
                              not os.path.isdir(workspace)):
        return res.finding("unsupported_execution_target", "candidate %s has no usable "
                           "default-worker workspace" % chosen["family_id"])
    body = (chosen["_body"] or "").upper()
    missing = [m for m in CANDIDATE_BODY_MARKERS if m not in body]
    if missing:
        return res.finding("candidate_body_not_v13",
                           "candidate %s card body is not a v1.3.0 card body: missing %s "
                           "(contract 14.4 requires the DCA parameter domain and the cohort "
                           "survivor rules in every appended card)" % (chosen["family_id"], missing),
                           pool_entry=chosen["family_id"], missing=missing)
    hold = prepared_execution_hold(chosen, args, res)
    if hold is not None:
        return hold
    return append_one(args.results_root, chosen, args, res)

def round_once(args):
    """Serialize concurrent C3 requests without holding the bridge during agent work."""
    root = Path(args.results_root)
    if args.dry_run or not root.is_dir():
        return _round_once(args)
    try:
        lock_dir = root / HANDOFF_DIRNAME
        lock_dir.mkdir(exist_ok=True)
        fd = _lock(lock_dir / ".advance.lock")
    except OSError as exc:
        return Round().finding("advance_lock_failed", str(exc))
    if fd is None:
        return Round().waiting("another C3 advance is in progress")
    try:
        return _round_once(args)
    finally:
        os.close(fd)


def main():
    ap = argparse.ArgumentParser(description="contract 14.4 automatic production handoff (one round)")
    ap.add_argument("--results-root", default=os.environ.get("QLIB_RESULTS_ROOT", DEFAULT_RESULTS))
    ap.add_argument("--board", default=None, help="ignored legacy argument (no board consulted)")
    ap.add_argument("--pool", default=None, help="candidate pool JSON (default <results>/_handoff/%s)"
                    % POOL_FILENAME)
    ap.add_argument("--detector", default="handoff", choices=["handoff", "default", "operator"])
    ap.add_argument("--dry-run", action="store_true", help="read-only: no agent, no file, no log line")
    ap.set_defaults(require_prepared_execution=True)
    ap.add_argument("--require-prepared-execution", dest="require_prepared_execution",
                    action="store_true",
                    help="require an upstream-prepared deterministic execution contract (default)")
    ap.add_argument("--legacy-agent-dispatch", dest="require_prepared_execution",
                    action="store_false",
                    help="rollback only: restore the historical Hermes worker dispatch path")
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
    elif res.action in ("appended", "retried"):
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
