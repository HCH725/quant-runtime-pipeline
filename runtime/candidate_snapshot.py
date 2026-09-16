#!/usr/bin/env python3
"""Hourly read-only snapshot of the current quant candidate (Discord #candidate).

Monitoring only: one short mobile-sized line set every run, read-only by construction - nothing under
/results is written, the only Kanban verbs are read-backs, and no launch / retry / unblock / verdict can
happen here.  Fixed output order: Leaderboard, Current, Research Funnel, Runtime health.

Sources: `_survivors/leaderboard.json` entries verbatim (top 5); the current family's newest
`<family_id>/family.json`; its authoritative attempt (reconcile.py selection, contract 9.4 v1.7.1) for
the progress (round-spec `expected.expected_case_evaluations` as the denominator, and the larger of the
streamed `grid_*.csv` row count and the engine's own `artifacts/progress.json` cohort / pair counter
converted with it as the numerator) and, while it is still RUNNING_QLIB, its latest *observable* cohort
(newest streamed grid row); the canonical intake state for the funnel (reviewed = the four
current_snapshot buckets, ingested = unique ingested_wiki_records, +N/24h read-only from the intake
cron's own reports, distinct registered / backtested families); the board's live running / blocked
counts.
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

# `kanban list` is fence-blocked inside a worker child context (the fence guards mutation); every
# call below is a read, so drop just that marker - no write verb exists in this file.
os.environ.pop("HERMES_DELEGATED_CHILD_CONTEXT", None)

DEFAULT_BOARD = "quant-strategy-research"
TOP_N = 5
BAR_CELLS = 10
HEADER = "\U0001F4CA Quant Candidate Hourly"
TROPHY = "\U0001F3C6 Leaderboard"
CURRENT = "\U0001F9EA Current"
FUNNEL = "\U0001F52C Research Funnel"
WARN = "\u26A0\uFE0F"
RUNNING_STAGE = "RUNNING_QLIB"  # the in-flight stage default: only then is a cohort observable
REVIEW_STATE = "/Users/hong/workspace/alpha-strategy-review-state.json"
INTAKE_OUTPUT = "/Users/hong/.hermes/cron/output/a5ae89131299"
# the only two ingestion lines the durable intake reports have carried lately (newest format first)
INGESTED_RES = (re.compile(r"Ingested records:\s*\d+\s*total\s*\(\+(\d+)\s*this run\)"),
                re.compile(r"Ingested:\s*\d+\s*\(\+(\d+)\s*\)"))
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
    """(symbol, timeframe) of the attempt's newest already-streamed grid row; (None, None) if none.

    Newest grid first (mtime): the first grid_*.csv with a data row wins, read at its last row.
    """
    stamped = []
    for path in (attempt.path / "artifacts").glob("grid_*.csv"):
        try:
            stamped.append((path.stat().st_mtime, path))
        except OSError:
            continue
    for _stamp, path in sorted(stamped, reverse=True):
        try:
            with path.open(newline="") as fh:
                reader = csv.reader(fh)
                header = next(reader, None)
                last = None
                for last in reader:  # it streams row by row: keep the tail, never the whole grid
                    pass
        except (OSError, csv.Error):
            continue
        if header and last is not None:
            row = dict(zip(header, last))
            return row.get("symbol") or None, row.get("timeframe") or None
    return None, None


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


def published_steps(attempt):
    """(done, total) work units the attempt's own `artifacts/progress.json` reports; (None, None) if unusable.

    The engines that write their phase grids only once every cohort is done (Strategy F) still publish
    their running cohort counter after each one (Strategy E: `pairs_done` / `pairs_total`), so a
    mid-run snapshot has a numerator while no grid has streamed a row yet.
    """
    doc = load_json(attempt.path / "artifacts" / "progress.json")
    if not isinstance(doc, dict):
        return None, None
    for done_key, total_key in (("cohorts_done", "cohorts_total"), ("pairs_done", "pairs_total")):
        done, total = doc.get(done_key), doc.get(total_key)
        if type(done) is int and type(total) is int and total > 0 and 0 <= done <= total:
            return done, total
    return None, None


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
    # Only a non-terminal attempt reaches this line: its engine may still be mid-run, and the engines
    # that write the grids once every cohort is done publish the cohort / pair counter in the meantime.
    # Converted with the immutable denominator that counter is an estimate, so it is capped at the total
    # and only used when it reads higher than the (exact, but late) streamed row count.
    steps_done, steps_total = published_steps(attempt)
    if steps_done is not None and steps_total:
        done = max(done, min(total, total * steps_done // steps_total))
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

    Registered = <family_id>/family.json outside `_*`; backtested = some attempt streamed a grid CSV
    with a data row (two read lines are enough, the grids are never read through).
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
    """(reviewed, ingested, delta_24h) from the canonical intake state; delta_24h None = unavailable.

    reviewed = the four current_snapshot buckets, ingested = unique ingested_wiki_records; delta_24h is
    read-only from the intake cron's own durable reports, and unavailable (never partial) when one in
    the window carries neither ingestion line.
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
        match = next((m for m in (p.search(text) for p in INGESTED_RES) if m), None)
        if match is None:
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
    # cohort replaces the stage line; only a live attempt has an observable one
    cohort = latest_observable_cohort(attempt) if attempt and stage == RUNNING_STAGE else (None, None)
    lines.append("Cohort: %s" % ("%s / %s" % cohort if all(cohort) else "unavailable"))
    if task_id:
        status, why = card_status(board, task_id)
        lines.append("Card: %s" % (status or "unreadable (%s)" % why))
    else:
        lines.append("Card: unavailable (no family.json with a kanban_task_id)")

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
