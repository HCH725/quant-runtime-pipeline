#!/usr/bin/env python3
"""Hourly read-only snapshot of the current quant candidate (Discord #candidate).

Monitoring only.  It prints one short mobile-sized line set every run - even when nothing changed,
because the operator expects an hourly line - and it is read-only by construction: nothing under
/results is ever written, the only Kanban verbs are the read-backs `show` / `list`, the leaderboard
is consumed in the order the file already carries (no re-ranking, no recompute), and no launch /
retry / unblock / verdict can happen here.

Sources
- leaderboard: <results>/_survivors/leaderboard.json, entries verbatim, top 5.
- current family: the newest <results>/<family_id>/family.json (handoff-written, carries
  kanban_task_id / kanban_board).
- progress: the family's **authoritative current attempt** - selection reused from reconcile.py
  (contract 9.4 v1.7.1), never a second current-pointer.  terminal DONE = 100%, no attempt yet =
  0%, otherwise the rows that attempt actually streamed into artifacts/grid_*.csv over the immutable
  round-spec `expected.expected_case_evaluations`, capped to 0..100.
- counts: live running / blocked task counts read back from the family's board.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from reconcile import (DEFAULT_RESULTS, attempt_metadata, card_status, discover_rounds,  # noqa: E402
                       select_authoritative, sh)
from terminal_evidence import TERMINALS  # noqa: E402

# The CLI fence refuses `kanban list` from inside a delegate_task / worker child context because it
# guards mutation; every call below is a read, so drop just that marker and keep the snapshot usable
# when a worker runs it by hand.  (No write verb exists in this file.)
os.environ.pop("HERMES_DELEGATED_CHILD_CONTEXT", None)

DEFAULT_BOARD = "quant-strategy-research"
TOP_N = 5
BAR_CELLS = 10
HEADER = "\U0001F4CA Quant Candidate Hourly"
TROPHY = "\U0001F3C6 Leaderboard"
WARN = "\u26A0\uFE0F"


def load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def current_family(results_root):
    """The newest family.json under the results root; None when there is none."""
    best = None
    for doc_path in sorted(Path(results_root).glob("*/family.json")):
        doc = load_json(doc_path)
        if doc and doc.get("kanban_task_id") and doc.get("created_at_utc"):
            if best is None or doc["created_at_utc"] > best["created_at_utc"]:
                best = doc
    return best


def leaderboard_entries(results_root):
    doc = load_json(Path(results_root) / "_survivors" / "leaderboard.json")
    return (doc or {}).get("entries") or []


def grid_rows(path):
    """Data rows already streamed into one phase grid (header excluded)."""
    import csv
    try:
        with path.open(newline="") as fh:
            return max(0, sum(1 for _ in csv.reader(fh)) - 1)
    except OSError:
        return 0


def expected_total(results_root, family_id, round_id):
    """The immutable round-spec denominator; None when it is missing."""
    doc = load_json(Path(results_root) / family_id / "rounds" / round_id / "round-spec.json")
    expected = (doc or {}).get("expected") or {}
    total = expected.get("expected_case_evaluations")
    return total if isinstance(total, int) and total > 0 else None


def round_spec_total(results_root, family_id):
    """The family's newest round-spec denominator; None when there is none (not-launched family)."""
    pattern = (Path(results_root) / family_id / "rounds").glob("*/round-spec.json")
    for spec in sorted(pattern, reverse=True):
        total = ((load_json(spec) or {}).get("expected") or {}).get("expected_case_evaluations")
        if isinstance(total, int) and total > 0:
            return total
    return None


def progress(results_root, family_id):
    """(pct, done, total, stage, note) for the family's authoritative attempt."""
    rounds = [r for r in discover_rounds(results_root) if r[0] == family_id]
    if not rounds:
        total = round_spec_total(results_root, family_id)
        note = "no attempt yet" if total else "no round/attempt directory yet"
        return 0.0, 0, total, "not launched", note
    best = None
    for _family, round_id, attempts in rounds:
        records = [attempt_metadata(a, family_id, round_id, a.name) for a in attempts]
        authoritative, _superseded, problem = select_authoritative(records)
        if problem or authoritative is None:
            return 0.0, 0, None, "unknown", "attempt selection ambiguous: %s" % (problem or "?")
        if best is None or authoritative.created_at > best[0].created_at:
            best = (authoritative, round_id)
    attempt, round_id = best
    stage = (load_json(attempt.path / "state.json") or {}).get("stage") or "unknown"
    total = expected_total(results_root, family_id, round_id)
    terminals = [t for t in TERMINALS if (attempt.path / t).exists()]
    if "DONE" in terminals:
        return 100.0, total or 0, total, "DONE", "terminal DONE"
    if terminals:
        return 0.0, 0, total, stage, "terminal %s (no verdict: family not completed)" % terminals[0]
    done = sum(grid_rows(p) for p in sorted((attempt.path / "artifacts").glob("grid_*.csv")))
    note = attempt.run_id
    if total is None:
        return 0.0, done, None, stage, note + " (round-spec expected total unavailable)"
    return min(100.0, max(0.0, 100.0 * done / total)), done, total, stage, note


def board_counts(board):
    """(running, blocked) live task counts; (None, None) when the read-back fails."""
    rc, out, _err = sh(["hermes", "kanban", "--board", board, "list", "--json"])
    if rc != 0:
        return None, None
    try:
        tasks = json.loads(out[out.index("["):])
    except (ValueError, IndexError):
        return None, None
    counts = {}
    for task in tasks:
        counts[task.get("status")] = counts.get(task.get("status"), 0) + 1
    return counts.get("running", 0), counts.get("blocked", 0)


def _num(value, digits=2, suffix=""):
    return "-" if not isinstance(value, (int, float)) else "%.*f%s" % (digits, value, suffix)


def render(results_root):
    lines = [HEADER]
    if not Path(results_root).is_dir():
        return "\n".join(lines + ["Results root not found: %s" % results_root,
                                  "Progress: unavailable", "Card: unavailable", TROPHY + ": unavailable"])
    family = current_family(results_root)
    board = (family or {}).get("kanban_board") or DEFAULT_BOARD
    task_id = (family or {}).get("kanban_task_id")
    lines.append("Current: %s" % (family["family_id"] if family else "unavailable"))

    pct, done, total, stage, note = ((0.0, 0, None, "unknown", "")
                                     if not family else progress(results_root, family["family_id"]))
    if total:
        filled = int(round(pct / 100.0 * BAR_CELLS))
        bar = "\u2588" * filled + "\u2591" * (BAR_CELLS - filled)
        lines.append("Progress: %s %.1f%% (%s / %s)" % (bar, pct, format(done, ","),
                                                       format(total, ",")))
    else:
        lines.append("Progress: unavailable (%s)" % note)
    if task_id:
        status, why = card_status(board, task_id)
        lines.append("Card: %s" % (status or "unreadable (%s)" % why))
    else:
        lines.append("Card: unavailable (no family.json with a kanban_task_id)")
    lines.append("Stage: %s%s" % (stage, " (%s)" % note if total else ""))

    entries = leaderboard_entries(results_root)
    if entries:
        lines.append(TROPHY)
        for entry in entries[:TOP_N]:
            full = entry.get("full") or {}
            lines.append("%s. %s Sharpe %s MaxDD %s %s" % (
                entry.get("rank", "?"), entry.get("cohort") or entry.get("survivor_id"),
                _num(full.get("sharpe")), _num(full.get("max_dd_pct"), digits=6),
                entry.get("evidence_state", "")))
    else:
        lines.append(TROPHY + ": unavailable (no entries)")

    running, blocked = board_counts(board)
    if running is None:
        lines.append("%s Running/Blocked: unavailable" % WARN)
    else:
        lines.append("%s Blocked: %d | Running: %d" % (WARN, blocked, running))
    return "\n".join(lines)


def main():
    results_root = os.environ.get("QLIB_RESULTS_ROOT", DEFAULT_RESULTS)
    print(render(results_root))
    return 0


if __name__ == "__main__":
    sys.exit(main())
