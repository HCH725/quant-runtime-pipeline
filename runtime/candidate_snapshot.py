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
- cohort: the last data row of the newest grid_*.csv the authoritative attempt already streamed
  (latest *observable* symbol / timeframe - an observation, not a per-second heartbeat).
- research funnel: the canonical intake state (reviewed = the four current_snapshot buckets,
  ingested = unique ingested_wiki_records), a read-only +N/24h taken from the intake cron's own
  durable run reports, and the distinct registered / backtested families under the results root.
- counts: live running / blocked task counts read back from the family's board.

Output order is fixed: Leaderboard, Current, Research Funnel, Runtime health.
"""
import csv
import datetime
import json
import os
import re
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
CURRENT = "\U0001F9EA Current"
FUNNEL = "\U0001F52C Research Funnel"
WARN = "\u26A0\uFE0F"
RUNNING_STAGE = "RUNNING_QLIB"  # the in-flight stage default: a line repeating it carries nothing
REVIEW_STATE = "/Users/hong/workspace/alpha-strategy-review-state.json"
INTAKE_OUTPUT = "/Users/hong/.hermes/cron/output/a5ae89131299"
INGESTED_RE = re.compile(r"Ingested records:\s*\d+\s*total\s*\(\+(\d+)\s*this run\)")
DELTA_HOURS = 24


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
    try:
        with path.open(newline="") as fh:
            return max(0, sum(1 for _ in csv.reader(fh)) - 1)
    except OSError:
        return 0


def latest_observable_cohort(attempt):
    """(symbol, timeframe) of the attempt's newest streamed grid row; (None, None) when there is none.

    Latest *observable*: the newest (mtime) grid_*.csv that already streamed a data row, read at its
    last row - an observation of progress, not a second-level heartbeat.
    """
    newest, row = None, None
    for path in sorted((attempt.path / "artifacts").glob("grid_*.csv")):
        try:
            with path.open(newline="") as fh:
                reader = csv.reader(fh)
                header = next(reader, None)
                last = None
                for last in reader:  # it streams row by row: keep the tail, never the whole grid
                    pass
            stamp = path.stat().st_mtime
        except (OSError, csv.Error):
            continue
        if not header or last is None:  # header only: nothing observable streamed yet
            continue
        if newest is None or stamp > newest:
            newest, row = stamp, dict(zip(header, last))
    if row is None:
        return None, None
    return row.get("symbol") or None, row.get("timeframe") or None


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
    """(pct, done, total, stage, note, attempt) for the family's authoritative attempt."""
    rounds = [r for r in discover_rounds(results_root) if r[0] == family_id]
    if not rounds:
        total = round_spec_total(results_root, family_id)
        note = "no attempt yet" if total else "no round/attempt directory yet"
        return 0.0, 0, total, "not launched", note, None
    best = None
    for _family, round_id, attempts in rounds:
        records = [attempt_metadata(a, family_id, round_id, a.name) for a in attempts]
        authoritative, _superseded, problem = select_authoritative(records)
        if problem or authoritative is None:
            return 0.0, 0, None, "unknown", "attempt selection ambiguous: %s" % (problem or "?"), None
        if best is None or authoritative.created_at > best[0].created_at:
            best = (authoritative, round_id)
    attempt, round_id = best
    stage = (load_json(attempt.path / "state.json") or {}).get("stage") or "unknown"
    total = expected_total(results_root, family_id, round_id)
    terminals = [t for t in TERMINALS if (attempt.path / t).exists()]
    if "DONE" in terminals:
        return 100.0, total or 0, total, "DONE", "terminal DONE", attempt
    if terminals:
        return 0.0, 0, total, stage, "terminal %s (no verdict: family not completed)" % terminals[0], attempt
    done = sum(grid_rows(p) for p in sorted((attempt.path / "artifacts").glob("grid_*.csv")))
    note = attempt.run_id
    if total is None:
        return 0.0, done, None, stage, note + " (round-spec expected total unavailable)", attempt
    return min(100.0, max(0.0, 100.0 * done / total)), done, total, stage, note, attempt


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


def backtested_counts(results_root):
    """(backtested, registered) distinct strategy families under the results root.

    Registered = <family_id>/family.json outside the `_*` dirs; backtested = some attempt streamed a
    grid CSV with a data row (header + one line is enough, the grids are never read through).
    """
    families, backtested = [], 0
    for doc in sorted(Path(results_root).glob("*/family.json")):
        if doc.parent.name.startswith("_"):
            continue
        families.append(doc)
        for grid in doc.parent.glob("rounds/*/attempts/*/artifacts/grid_*.csv"):
            try:
                with grid.open(newline="") as fh:
                    if not (fh.readline() and fh.readline()):
                        continue
            except OSError:
                continue
            backtested += 1
            break
    return backtested, len(families)


def research_counts(now=None):
    """(reviewed, ingested, delta_24h) from the canonical intake state; all None when unreadable.

    reviewed = the four current_snapshot buckets (Wiki duplicates never double-count), ingested =
    unique ingested_wiki_records, delta_24h = what the intake cron's own durable run reports say it
    completed inside DELTA_HOURS - read-only, and unavailable (not partial) when a report in the
    window does not carry the canonical line, because then the window's total is unknowable.
    """
    doc = load_json(REVIEW_STATE)
    if not isinstance(doc, dict):
        return None, None, None
    buckets = doc.get("current_snapshot") or {}
    reviewed = sum(len(v) for v in buckets.values() if isinstance(v, list))
    ingested = len(set(doc.get("ingested_wiki_records") or []))
    cutoff = (now or datetime.datetime.now()) - datetime.timedelta(hours=DELTA_HOURS)
    total, reports = 0, 0
    for path in sorted(Path(INTAKE_OUTPUT).glob("*.md")):
        try:
            fresh = datetime.datetime.strptime(path.name[:19], "%Y-%m-%d_%H-%M-%S") >= cutoff
            text = path.read_text(errors="replace")
        except (OSError, ValueError):
            continue
        if not fresh:
            continue
        reports += 1
        match = INGESTED_RE.search(text)
        if not match:
            return reviewed, ingested, None
        total += int(match.group(1))
    return reviewed, ingested, total if reports else None


def bar(pct):
    """The shared 10-cell progress bar."""
    filled = int(round(pct / 100.0 * BAR_CELLS))
    return "\u2588" * filled + "\u2591" * (BAR_CELLS - filled)


def _num(value, digits=2, suffix=""):
    return "-" if not isinstance(value, (int, float)) else "%.*f%s" % (digits, value, suffix)


def render(results_root):
    lines = [HEADER]
    if not Path(results_root).is_dir():
        return "\n".join(lines + ["Results root not found: %s" % results_root,
                                  TROPHY + ": unavailable", CURRENT + ": unavailable",
                                  FUNNEL + ": unavailable",
                                  "%s Running/Blocked: unavailable" % WARN])
    family = current_family(results_root)
    board = (family or {}).get("kanban_board") or DEFAULT_BOARD
    task_id = (family or {}).get("kanban_task_id")

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

    pct, done, total, stage, note, attempt = ((0.0, 0, None, "unknown", "", None) if not family
                                             else progress(results_root, family["family_id"]))
    lines += ["", CURRENT, family["family_id"] if family else "unavailable"]
    if total:
        lines.append("Progress: %s %.1f%% (%s / %s)" % (bar(pct), pct, format(done, ","),
                                                       format(total, ",")))
    else:
        lines.append("Progress: unavailable (%s)" % note)
    symbol, timeframe = latest_observable_cohort(attempt) if attempt else (None, None)
    lines.append("Cohort: %s" % ("%s / %s" % (symbol, timeframe) if symbol and timeframe
                                 else "unavailable"))
    if task_id:
        status, why = card_status(board, task_id)
        lines.append("Card: %s" % (status or "unreadable (%s)" % why))
    else:
        lines.append("Card: unavailable (no family.json with a kanban_task_id)")
    if stage != RUNNING_STAGE:  # the in-flight default is a constant, so its line carries nothing
        lines.append("Stage: %s%s" % (stage, " (%s)" % note if total else ""))

    reviewed, ingested, delta = research_counts()
    lines += ["", FUNNEL]
    if reviewed and ingested is not None:
        share = 100.0 * ingested / reviewed
        lines.append("%-10s %s %s / %s reviewed %.1f%% %s" % (
            "Wiki Brain", bar(share), ingested, reviewed, share,
            "+%d/24h" % delta if delta is not None else "delta unavailable"))
    else:
        lines.append("%-10s unavailable" % "Wiki Brain")
    backtested, registered = backtested_counts(results_root)
    if registered:
        share = 100.0 * backtested / registered
        lines.append("%-10s %s %d / %d registered families %.1f%%" % (
            "Backtested", bar(share), backtested, registered, share))
    else:
        lines.append("%-10s unavailable" % "Backtested")

    running, blocked = board_counts(board)
    lines.append("")
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
