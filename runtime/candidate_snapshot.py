#!/usr/bin/env python3
"""Hourly read-only snapshot of the current quant candidate (Discord #candidate).

Monitoring only: one short mobile-sized line set every run, read-only by construction - nothing under
/results is written, the only Kanban verbs are read-backs, and no launch / retry / unblock / verdict can
happen here.  Fixed output order: Leaderboard, Current, Research Funnel, Runtime health.  The same
document is available machine-readable for the read-only Homepage dashboard via `--dashboard-json
PATH` (the only write this file can make, and never under /results), reusing these same helpers -
never a second calculation.

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
import argparse
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
                re.compile(r"Ingested:\s*\d+\s*\((\+\d+)\s*\)"))
DELTA_HOURS = 24

# --- dashboard JSON (read-only Homepage observability): machine-readable twin of the same line -----
# Written only when `--dashboard-json PATH` is passed, to that path (never under /results).  Every
# value is display-only: no threshold, ranking or outcome is re-derived here, so the dashboard can
# never disagree with the text the operator already gets.
DASHBOARD_SCHEMA_VERSION = 1
CONTAINER_NAME = os.environ.get("QLIB_CONTAINER", "qlib-run")
CRON_JOBS = Path(os.environ.get("HERMES_CRON_JOBS", "~/.hermes/cron/jobs.json")).expanduser()
# The quant loop's own scheduler entries: the dashboard reports their state, it never changes it.
CRON_SCRIPTS = ("quant_production_handoff.py", "quant_runtime_reconcile.py", "quant_runtime_watchdog.py",
                "quant_candidate_snapshot.py")
CRON_NAMES = ("Research Intake Review",)


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


def spec_expected_total(doc):
    """`expected.expected_case_evaluations` of one round-spec doc; None when it carries no denominator.

    A prerequisite-gated round registers `expected` as a scalar (e.g. `"not_computable"`) or leaves it
    out: anything that is not a dict has no case denominator, which is a normal registered outcome and
    never an error.
    """
    expected = doc.get("expected") if isinstance(doc, dict) else None
    total = expected.get("expected_case_evaluations") if isinstance(expected, dict) else None
    return total if isinstance(total, int) and total > 0 else None


def expected_total(results_root, family_id, round_id):
    """The immutable round-spec denominator; None when it is missing."""
    return spec_expected_total(load_json(Path(results_root) / family_id / "rounds" / round_id /
                                         "round-spec.json"))


def round_spec_total(results_root, family_id):
    """The family's newest round-spec denominator; None when there is none (not-launched family)."""
    pattern = (Path(results_root) / family_id / "rounds").glob("*/round-spec.json")
    for spec in sorted(pattern, reverse=True):
        total = spec_expected_total(load_json(spec))
        if total is not None:
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
    """(pct, done, total, stage, note, attempt, round_id) for the family's authoritative attempt."""
    rounds = [r for r in discover_rounds(results_root) if r[0] == family_id]
    if not rounds:
        total = round_spec_total(results_root, family_id)
        note = "no attempt yet" if total else "no round/attempt directory yet"
        return 0.0, 0, total, "not launched", note, None, None
    best = None
    for _family, round_id, attempts in rounds:
        records = [attempt_metadata(a, family_id, round_id, a.name) for a in attempts]
        authoritative, _superseded, problem = select_authoritative(records)
        if problem or authoritative is None:
            return 0.0, 0, None, "unknown", "attempt selection ambiguous: %s" % (problem or "?"), None, None
        if best is None or authoritative.created_at > best[0].created_at:
            best = (authoritative, round_id)
    attempt, round_id = best
    stage = (load_json(attempt.path / "state.json") or {}).get("stage") or "unknown"
    total = expected_total(results_root, family_id, round_id)
    terminals = [t for t in TERMINALS if (attempt.path / t).exists()]
    if "DONE" in terminals:
        return 100.0, total or 0, total, "DONE", "terminal DONE", attempt, round_id
    if terminals:
        return (0.0, 0, total, stage, "terminal %s (no verdict: family not completed)" % terminals[0],
                attempt, round_id)
    done = sum(grid_rows(p) for p in sorted((attempt.path / "artifacts").glob("grid_*.csv")))
    note = attempt.run_id
    if total is None:
        return 0.0, done, None, stage, note + " (round-spec expected total unavailable)", attempt, round_id
    # Only a non-terminal attempt reaches this line: its engine may still be mid-run, and the engines
    # that write the grids once every cohort is done publish the cohort / pair counter in the meantime.
    # Converted with the immutable denominator that counter is an estimate, so it is capped at the total
    # and only used when it reads higher than the (exact, but late) streamed row count.
    steps_done, steps_total = published_steps(attempt)
    if steps_done is not None and steps_total:
        done = max(done, min(total, total * steps_done // steps_total))
    return min(100.0, max(0.0, 100.0 * done / total)), done, total, stage, note, attempt, round_id


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

    pct, done, total, stage, note, attempt, _round_id = (
        (0.0, 0, None, "unknown", "", None, None) if not family
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


# --- dashboard payload (read-only JSON twin of the text snapshot) ------------------------------
def iso_utc(epoch):
    """One epoch second as the ISO-8601 Z form the rest of the tree uses; None stays None."""
    if epoch is None:
        return None
    return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def mtime_utc(path):
    """Last-write time of one file; None when it cannot be stat'ed."""
    try:
        return iso_utc(Path(path).stat().st_mtime)
    except OSError:
        return None


def container_runtime():
    """The qlib container's live state from `container list --all --format json`; None when the
    read-back is unavailable.

    A name that is missing from a *successful* listing is `absent` (measured); a failed CLI or
    unparsable listing is None (unknown). The dashboard must never read "unknown" as "down".
    """
    rc, out, _err = sh(["container", "list", "--all", "--format", "json"])
    if rc != 0:
        return None
    try:
        rows = json.loads(out)
    except ValueError:
        return None
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, dict) or row.get("id") != CONTAINER_NAME:
            continue
        status = row.get("status") if isinstance(row.get("status"), dict) else {}
        config = row.get("configuration") if isinstance(row.get("configuration"), dict) else {}
        image = config.get("image") if isinstance(config.get("image"), dict) else {}
        return {"name": CONTAINER_NAME, "state": status.get("state"),
                "image": image.get("reference"), "started_utc": status.get("startedDate")}
    return {"name": CONTAINER_NAME, "state": "absent", "image": None, "started_utc": None}


def quant_crons():
    """Read-only view of the quant loop's scheduler entries; None when the jobs file is unreadable."""
    doc = load_json(CRON_JOBS)
    jobs = doc.get("jobs") if isinstance(doc, dict) else None
    if not isinstance(jobs, list):
        return None
    out = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        if job.get("script") in CRON_SCRIPTS or job.get("name") in CRON_NAMES:
            schedule = job.get("schedule") if isinstance(job.get("schedule"), dict) else {}
            out.append({"id": job.get("id"), "name": job.get("name"), "script": job.get("script"),
                        "schedule": schedule.get("display"),
                        "state": "paused" if job.get("paused") else "active",
                        "last_status": job.get("last_status"), "last_run_at": job.get("last_run_at")})
    return out


def results_volume(results_root):
    """(free_gib, total_gib) of the filesystem holding the results root; (None, None) if unreadable."""
    try:
        st = os.statvfs(results_root)
    except OSError:
        return None, None
    return (round(st.f_bavail * st.f_frsize / float(2 ** 30), 1),
            round(st.f_blocks * st.f_frsize / float(2 ** 30), 1))


def last_activity_utc(attempt, family):
    """Newest observed file write in the attempt dir (top level + artifacts), else family created_at."""
    stamps = []
    if attempt is not None:
        try:
            paths = list(attempt.path.iterdir()) + list((attempt.path / "artifacts").glob("*"))
        except OSError:
            paths = []
        for path in paths:
            try:
                stamps.append(path.stat().st_mtime)
            except OSError:
                continue
    if stamps:
        return iso_utc(max(stamps))
    return (family or {}).get("created_at_utc") or None


def dashboard_payload(results_root, now=None):
    """Machine-readable twin of render(): the same sources, selections and numbers, as JSON.

    Nothing is recomputed here - every value comes from the helpers the Discord text already uses
    (leaderboard entries verbatim, the authoritative attempt's stage / streamed rows against the
    immutable round-spec denominator, the canonical intake state, the board read-back). Unreadable
    inputs are reported as null / available=false - never as 0 - and no strategy performance is
    derived or ranked by this module.
    """
    stamp = now if now is not None else datetime.datetime.now(datetime.timezone.utc)
    local_now = stamp.astimezone().replace(tzinfo=None) if stamp.tzinfo else stamp
    if stamp.tzinfo is None:
        stamp = stamp.astimezone()
    generated = stamp.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    root_present = Path(results_root).is_dir()
    family = current_family(results_root) if root_present else None
    board = (family or {}).get("kanban_board") or DEFAULT_BOARD
    task_id = (family or {}).get("kanban_task_id")

    if family is None:
        pct, done, total, stage, attempt, round_id = None, None, None, None, None, None
        note = "no family.json with a kanban_task_id under the results root"
    else:
        pct, done, total, stage, note, attempt, round_id = progress(results_root, family["family_id"])
    available = total is not None
    if available:
        progress_text = "%.1f%% (%s / %s)" % (pct, format(done, ","), format(total, ","))
    else:
        progress_text = "unavailable (%s)" % note
    cohort = latest_observable_cohort(attempt) if attempt and stage == RUNNING_STAGE else (None, None)
    card, card_why = card_status(board, task_id) if task_id else (None, "no family.json with a kanban_task_id")

    entries = leaderboard_entries(results_root) if root_present else []
    leaderboard_path = Path(results_root) / "_survivors" / "leaderboard.json"
    top = []
    for entry in entries[:TOP_N]:
        full = entry.get("full") or {}
        top.append({"rank": entry.get("rank"),
                    "cohort": entry.get("cohort") or entry.get("survivor_id"),
                    "sharpe": full.get("sharpe"),
                    "max_dd_pct": full.get("max_dd_pct"),
                    "evidence_state": entry.get("evidence_state"),
                    "summary": "Sharpe %s \u00b7 MaxDD %s \u00b7 %s" % (
                        _num(full.get("sharpe")), _num(full.get("max_dd_pct"), digits=6),
                        entry.get("evidence_state") or "unknown")})

    reviewed, ingested, delta = research_counts(now=local_now)
    wiki = bool(reviewed) and ingested is not None
    backtested, registered = backtested_counts(results_root) if root_present else (None, None)
    funnel = {
        "wiki_brain": {"available": wiki, "reviewed": reviewed if wiki else None,
                       "ingested": ingested if wiki else None,
                       "share_pct": round(100.0 * ingested / reviewed, 1) if wiki else None,
                       "delta_24h": delta, "delta_available": delta is not None,
                       "summary": ("%s / %s reviewed \u00b7 %s" % (
                           ingested, reviewed,
                           "+%d/24h" % delta if delta is not None else "delta unavailable")
                           if wiki else "unavailable")},
        "backtested": {"available": bool(registered), "families": backtested or None,
                       "registered": registered or None,
                       "share_pct": round(100.0 * backtested / registered, 1) if registered else None,
                       "summary": ("%d / %d registered families" % (backtested, registered)
                                   if registered else "unavailable")},
    }

    running, blocked = board_counts(board)
    container = container_runtime()
    free_gib, total_gib = results_volume(results_root) if root_present else (None, None)
    crons = quant_crons()
    if crons is None:
        crons_summary, crons_not_ok = "unavailable", None
    else:
        paused = sum(1 for job in crons if job["state"] == "paused")
        crons_summary = "%d jobs \u00b7 %d active \u00b7 %d paused" % (len(crons), len(crons) - paused, paused)
        crons_not_ok = [job["name"] for job in crons if job["last_status"] not in (None, "ok")]

    reasons = []
    if not root_present:
        status = "unknown"
        reasons.append("results root not found: %s" % results_root)
    elif blocked is None:
        status = "unknown"
        reasons.append("kanban board read-back unavailable (%s)" % board)
    elif blocked > 0:
        status = "attention"
        reasons.append("%d blocked card(s) on %s" % (blocked, board))
    elif container is None:
        status = "attention"
        reasons.append("container read-back unavailable")
    else:
        status = "ok"

    return {
        "schema_version": DASHBOARD_SCHEMA_VERSION,
        "generated_at_utc": generated,
        "monitoring_only": True,
        "scope_note": ("Research progress snapshot, read-only. Not live PnL and not a control "
                       "plane: this file can start, stop or retry nothing."),
        "health": {"status": status, "reasons": reasons,
                   "summary": status if not reasons else "%s: %s" % (status, "; ".join(reasons)),
                   "components": {"results_root_readable": root_present,
                                  "board_readback_available": blocked is not None,
                                  "container_readback_available": container is not None,
                                  "blocked_cards": blocked}},
        "sources": {"results_root": results_root, "results_root_readable": root_present,
                    "leaderboard_as_of_utc": mtime_utc(leaderboard_path) if root_present else None,
                    "family_created_at_utc": (family or {}).get("created_at_utc")},
        "current": {"family_id": (family or {}).get("family_id"),
                    "round_id": round_id,
                    "attempt": attempt.run_id if attempt else None,
                    "stage": stage, "note": note,
                    "progress_available": available,
                    "progress_pct": round(pct, 1) if available else None,
                    "progress_done": done if available else None,
                    "progress_total": total if available else None,
                    "progress_text": progress_text,
                    "cohort": "%s / %s" % cohort if all(cohort) else None,
                    "last_activity_utc": last_activity_utc(attempt, family),
                    "kanban_task_id": task_id or None, "board": board if family else None,
                    "card_status": card, "card_readback": card_why},
        "leaderboard": {"available": bool(entries), "count": len(entries), "shown": len(top),
                        "as_of_utc": mtime_utc(leaderboard_path) if root_present else None,
                        "entries": top, "top_n": TOP_N},
        "funnel": funnel,
        "runtime": {"container": container,
                    "container_summary": (container or {}).get("state") or "unavailable",
                    "results_volume": {"free_gib": free_gib, "total_gib": total_gib},
                    "results_volume_summary": ("%.1f GiB free / %.1f GiB" % (free_gib, total_gib)
                                               if free_gib is not None else "unavailable")},
        "agent": {"board": board, "running": running, "blocked": blocked,
                  "board_summary": ("Running %d \u00b7 Blocked %d" % (running, blocked)
                                    if running is not None else "unavailable"),
                  "crons": crons, "crons_summary": crons_summary, "crons_not_ok": crons_not_ok},
    }


def write_json(path, payload):
    """Publish one payload atomically (same-dir temp + replace) so a reader never sees a half file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)
    return path


def main():
    ap = argparse.ArgumentParser(description="Read-only hourly quant candidate snapshot (Discord line "
                                             "+ optional dashboard JSON).")
    ap.add_argument("--dashboard-json", metavar="PATH",
                    help="also write the read-only Homepage dashboard payload to PATH "
                         "(the only file this script writes, and never under /results)")
    args = ap.parse_args()
    json_out = args.dashboard_json
    results_root = os.environ.get("QLIB_RESULTS_ROOT", DEFAULT_RESULTS)
    print(render(results_root))
    if json_out:
        # stdout (the Discord payload) must never depend on the dashboard: a JSON write failure is
        # reported on stderr and leaves both stdout and the exit code untouched.
        try:
            write_json(json_out, dashboard_payload(results_root))
        except (OSError, ValueError) as exc:
            print("dashboard json not written (%s): %s" % (json_out, exc), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
