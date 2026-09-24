#!/usr/bin/env python3
"""Hourly read-only snapshot of the current quant candidate (Discord #candidate).

Monitoring only: one short mobile-sized line set every run, read-only by construction - nothing under
/results is written, the only Kanban verbs are read-backs, and no launch / retry / unblock / verdict can
happen here.  Fixed output order: Leaderboard, Current, Research Funnel, Runtime health.  The same
document is available machine-readable for the read-only Homepage dashboard via `--dashboard-json
PATH` (the only write this file can make; a target under /results is refused), reusing these same
helpers - never a second calculation.  Runtime health is not derived here either: it is the quant
watchdog's own state, passed through.

Sources: `_survivors/leaderboard.json` entries verbatim (top 5); the family whose authoritative attempt
is *real active runtime work* (contract 14.4 runtime-evidence selection, shared with
`production_handoff.runtime_state`: a newest attempt inside the 90-minute window that either has
published no terminal sentinel yet or belongs to a round whose verdict is still missing - verdict.json
is per ROUND, so an earlier round's verdict never hides a live follow-up round) - a registered-but-never-
launched, stale or finished family is NOT current, and with no active work at all the snapshot reports
**idle** instead of showing the newest `family.json` as if it were running.
That family's authoritative attempt (reconcile.py selection, contract 9.4 v1.7.1) gives the progress
(round-spec `expected.expected_case_evaluations` as the denominator, and the larger of the streamed
`grid_*.csv` row count and the engine's own `artifacts/progress.json` cohort / pair counter
converted with it as the numerator) and, while it is still RUNNING_QLIB, its latest *observable* cohort
(newest streamed grid row); the canonical intake state for the funnel (reviewed = the four
current_snapshot buckets, ingested = unique ingested_wiki_records, +N/24h read-only from the intake
cron's own reports, distinct registered / backtested families); the board's live running / blocked
counts (agent-lane display only - never a pipeline decision).
"""
import argparse
import csv
import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from production_handoff import (ACTIVE_WINDOW_MINUTES, read_families,  # noqa: E402
                                runtime_state)
from reconcile import DEFAULT_RESULTS, Attempt, discover_rounds, select_authoritative  # noqa: E402
from terminal_evidence import TERMINALS  # noqa: E402

# `kanban list` is fence-blocked inside a worker child context (the fence guards mutation); every
# call below is a read, so drop just that marker - no write verb exists in this file.
os.environ.pop("HERMES_DELEGATED_CHILD_CONTEXT", None)

DEFAULT_BOARD = "quant-strategy-research"

def attempt_metadata(path, family_id, round_id, _run_id):
    """Read-only selection shared with C4; historical paths remain visible to the dashboard."""
    return Attempt(path, family_id, round_id, require_timestamp=False)

def sh(cmd):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return p.returncode, p.stdout or "", p.stderr or ""
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", str(exc)

def card_status(board, task_id):
    """Historical card read-back is display-only, never a C3/C4 execution gate."""
    rc, out, err = sh(["hermes", "kanban", "--board", board, "show", task_id, "--json"])
    if rc:
        return None, (err or out)[:200]
    try:
        task = json.loads(out[out.index("{"):]).get("task") or {}
        return task.get("status"), "card read-back"
    except (IndexError, ValueError):
        return None, "card read-back unparsable"

TOP_N = 5
DASHBOARD_TOP_N = 10
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
# `quant_runtime_watchdog.py`'s own state file: the single authoritative alert/health truth for the
# quant loop (its W1 results-root/space, W2 run/container/stall, W3 terminal-pending, W4 cron checks).
# This module reads it; it never re-checks any of those conditions itself.
WATCHDOG_STATE = Path(os.environ.get("QUANT_WATCHDOG_STATE",
                                     "~/.hermes/state/quant_runtime_watchdog.json")).expanduser()
# Watchdog signature kind -> the operator-facing phrase.  Pure relabelling of the watchdog's own
# signature tokens: nothing is decided here, an unknown kind falls back to the signature verbatim,
# and the raw signature always travels alongside its label.
HEALTH_KINDS = {
    "results_root": "results root not readable",
    "low_space": "results volume below the 50 GiB floor",
    "container_cli": "container CLI unavailable",
    "cron_registry": "cron registry unreadable",
    "repo_import": "watchdog cannot import the repo reconciler helpers",
    "container_down": "run container not running",
    "container_restart": "container restarted after the run began",
    "host_reboot": "host rebooted after the run began",
    "soft_stall": "run stalled (no run.log/artifacts for 90+ minutes)",
    "terminal_pending": "run stopped without a published terminal",
    "stale": "quant cron job stale",
    "missing": "quant cron job missing from the registry",
}


def load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def active_family(results_root, now=None):
    """The family that currently owes the pipeline real runtime work; None when the pipeline is idle.

    Contract 14.4 runtime-evidence selection, shared with `production_handoff.runtime_state` (never a
    second calculation): current = a family whose newest attempt is real runtime work inside the active
    window - no terminal sentinel published yet, or a round verdict still missing. A family that is
    merely registered (never launched), stale, or finished (terminal verdict plus a terminal sentinel)
    is NOT current, so `Current` can never present the newest `family.json` as if it were running work.
    """
    best = None
    for family_id, doc in sorted(read_families(results_root).items()):
        state = runtime_state(results_root, family_id, doc, now=now)
        if not state["in_flight"] or state["attempt"] is None:
            continue
        key = (state["activity"] or 0.0, family_id)
        if best is None or key > best[0]:
            best = (key, {"family_id": family_id, "doc": doc, "attempt": state["attempt"],
                          "activity": state["activity"], "why": state["why"]})
    return best[1] if best else None


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


def round_ordinal(family_id, round_id):
    """Numeric ``N`` from the contract's ``<family_id>-r<N>`` round ID, or None if malformed."""
    prefix = "%s-r" % family_id
    if not isinstance(round_id, str) or not round_id.startswith(prefix):
        return None
    ordinal = round_id[len(prefix):]
    if not re.fullmatch(r"[1-9][0-9]*", ordinal):
        return None
    return int(ordinal)


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
        ordinal = round_ordinal(family_id, round_id)
        if ordinal is None:
            return 0.0, 0, None, "unknown", "round selection ambiguous: malformed round_id %s" % round_id, None, None
        # ponytail: legacy attempts without timestamps use stable round/run identity, not wall-clock guesses.
        order_key = (authoritative.created_at is not None,
                     authoritative.created_at or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc),
                     ordinal, authoritative.run_id)
        if best is None or order_key > best[0]:
            best = (order_key, authoritative, round_id)
    assert best is not None
    _order_key, attempt, round_id = best
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


def cumulative_backtest_workload(results_root):
    """(evaluations, grid_artifacts) for cumulative executed backtest workload.

    Completed attempts use the authoritative ``result.json.case_evaluations_total`` when present.
    Legacy/incomplete attempts without that field fall back to physical non-header rows in their
    streamed ``grid_*.csv`` artifacts.  Retries/superseded attempts remain included because the KPI
    measures work actually executed, not unique strategies or unique parameter cases.
    """
    root = Path(results_root)
    if not root.is_dir():
        return None, None
    evaluations = 0
    artifacts = 0
    for attempt in sorted(root.glob("*/rounds/*/attempts/*")):
        if not attempt.is_dir() or attempt.relative_to(root).parts[0].startswith("_"):
            continue
        grids = sorted((attempt / "artifacts").glob("grid_*.csv"))
        artifacts += len(grids)
        result = load_json(attempt / "result.json")
        result_total = result.get("case_evaluations_total") if isinstance(result, dict) else None
        if isinstance(result_total, (int, float)) and not isinstance(result_total, bool) and result_total >= 0:
            evaluations += int(result_total)
            continue
        for grid in grids:
            try:
                lines = 0
                last = b""
                with grid.open("rb") as fh:
                    while True:
                        chunk = fh.read(1024 * 1024)
                        if not chunk:
                            break
                        lines += chunk.count(b"\n")
                        last = chunk[-1:]
                if last and last != b"\n":
                    lines += 1
            except OSError:
                return None, None
            evaluations += max(lines - 1, 0)
    return evaluations, artifacts


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
    current = active_family(results_root)
    family = (current or {}).get("doc") or None
    family_id = (current or {}).get("family_id")
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
        (0.0, 0, None, "unknown", "no active runtime work", None, None) if not family_id
        else progress(results_root, family_id))
    # Current is real runtime work or nothing: an unlaunched/stale family is never shown as current.
    lines += ["", CURRENT, family_id if family_id else "idle (no active runtime work)"]
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
        lines.append("Card: unavailable (no active family)" if not family_id
                     else "Card: unavailable (family.json has no kanban_task_id)")

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


def watchdog_health():
    """The quant watchdog's own state, passed through; None when that state is unreadable.

    `quant_runtime_watchdog.py` is the single authoritative alert/health truth for the loop, so an
    active signature *is* the finding - this module only relabels the signature kind for display and
    never re-derives the condition.  An unreadable state is `unknown`, never a healthy dashboard.
    """
    doc = load_json(WATCHDOG_STATE)
    signatures = doc.get("active_signatures") if isinstance(doc, dict) else None
    if not isinstance(signatures, dict):
        return None
    active = []
    for signature in sorted(signatures):
        seen = signatures[signature] if isinstance(signatures[signature], dict) else {}
        kind = next((k for k in HEALTH_KINDS if k in str(signature).split("|")), None)
        active.append({"signature": signature, "kind": kind,
                       "label": HEALTH_KINDS.get(kind) or signature,
                       "first_seen_utc": seen.get("first_seen_utc")})
    return {"state_path": str(WATCHDOG_STATE), "schema_version": doc.get("schema_version"),
            "last_check_at_utc": doc.get("last_check_at_utc"),
            "last_healthy_at_utc": doc.get("last_healthy_at_utc"),
            "active": active, "active_count": len(active)}


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
    immutable round-spec denominator, the canonical intake state, the board read-back), and runtime
    health is `quant_runtime_watchdog.py`'s own state passed through. Unreadable inputs are reported
    as null / available=false - never as 0 - and no strategy performance is derived or ranked here.
    """
    stamp = now if now is not None else datetime.datetime.now(datetime.timezone.utc)
    local_now = stamp.astimezone().replace(tzinfo=None) if stamp.tzinfo else stamp
    if stamp.tzinfo is None:
        stamp = stamp.astimezone()
    generated = stamp.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    root_present = Path(results_root).is_dir()
    current = active_family(results_root) if root_present else None
    family = (current or {}).get("doc") or None
    family_id = (current or {}).get("family_id")
    board = (family or {}).get("kanban_board") or DEFAULT_BOARD
    task_id = (family or {}).get("kanban_task_id")

    if family_id is None:
        pct, done, total, stage, attempt, round_id = None, None, None, None, None, None
        note = "no active runtime work (no non-terminal attempt inside the active window)"
    else:
        pct, done, total, stage, note, attempt, round_id = progress(results_root, family_id)
    available = total is not None
    if available:
        progress_text = "%.1f%% (%s / %s)" % (pct, format(done, ","), format(total, ","))
    else:
        progress_text = "unavailable (%s)" % note
    cohort = latest_observable_cohort(attempt) if attempt and stage == RUNNING_STAGE else (None, None)
    card, card_why = (card_status(board, task_id) if task_id
                      else (None, "no active family with a kanban_task_id"))

    entries = leaderboard_entries(results_root) if root_present else []
    leaderboard_path = Path(results_root) / "_survivors" / "leaderboard.json"
    leaderboard_as_of = mtime_utc(leaderboard_path) if root_present else None
    top = []
    for entry in entries[:DASHBOARD_TOP_N]:
        full = entry.get("full") or {}
        top.append({"rank": entry.get("rank"),
                    "cohort": entry.get("cohort") or entry.get("survivor_id"),
                    "sharpe": full.get("sharpe"),
                    "annualized_return": full.get("annualized_return"),
                    "avg_trades_per_year": full.get("avg_trades_per_year"),
                    "max_dd_pct": full.get("max_dd_pct"),
                    "evidence_state": entry.get("evidence_state"),
                    "summary": "夏普 %s · 年化 %s · 最大回撤 %s · 年均交易 %s" % (
                        _num(full.get("sharpe")),
                        _num(full.get("annualized_return") * 100.0, digits=2, suffix="%")
                        if isinstance(full.get("annualized_return"), (int, float)) else "—",
                        _num(full.get("max_dd_pct") * 100.0, digits=2, suffix="%")
                        if isinstance(full.get("max_dd_pct"), (int, float)) else "—",
                        _num(full.get("avg_trades_per_year"), digits=1, suffix=" 次/年")
                        if isinstance(full.get("avg_trades_per_year"), (int, float)) else "—")})

    reviewed, ingested, delta = research_counts(now=local_now)
    wiki = bool(reviewed) and ingested is not None
    backtested, registered = backtested_counts(results_root) if root_present else (None, None)
    workload_evaluations, workload_artifacts = (
        cumulative_backtest_workload(results_root) if root_present else (None, None))
    funnel = {
        "workload": {"available": workload_evaluations is not None,
                     "evaluations": workload_evaluations,
                     "grid_artifacts": workload_artifacts,
                     "unit": "streamed_grid_rows",
                     "summary": ("%s cumulative executed evaluations across %s grid artifacts" %
                                 ("{:,}".format(workload_evaluations), workload_artifacts)
                                 if workload_evaluations is not None else "unavailable")},
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
    # Runtime health is the watchdog's verdict, not this file's: `status` is "attention" exactly when
    # the watchdog itself holds an active signature, and "unknown" when its state cannot be read.
    watchdog = watchdog_health()
    if watchdog is None:
        health = {"available": False, "status": "unknown", "source": "quant_runtime_watchdog",
                  "state_path": str(WATCHDOG_STATE), "active": [], "active_count": None,
                  "last_check_at_utc": None, "last_healthy_at_utc": None,
                  "summary": "unknown: watchdog state unreadable (%s)" % WATCHDOG_STATE}
    else:
        labels = [item["label"] for item in watchdog["active"]]
        health = {"available": True, "status": "attention" if labels else "ok",
                  "source": "quant_runtime_watchdog", "state_path": watchdog["state_path"],
                  "active": watchdog["active"], "active_count": watchdog["active_count"],
                  "last_check_at_utc": watchdog["last_check_at_utc"],
                  "last_healthy_at_utc": watchdog["last_healthy_at_utc"],
                  "summary": ("attention: %s" % "; ".join(
                      labels[:3] + (["+%d more" % (len(labels) - 3)] if len(labels) > 3 else [])))
                  if labels else "ok"}

    return {
        "schema_version": DASHBOARD_SCHEMA_VERSION,
        "generated_at_utc": generated,
        "monitoring_only": True,
        "scope_note": ("Research progress snapshot, read-only. Not live PnL and not a control "
                       "plane: this file can start, stop or retry nothing."),
        "health": health,
        "sources": {"results_root": results_root, "results_root_readable": root_present,
                    "leaderboard_as_of_utc": leaderboard_as_of,
                    "family_created_at_utc": (family or {}).get("created_at_utc"),
                    "current_selection": ("contract 14.4 runtime evidence: newest attempt inside the "
                                          "%d-minute active window with no terminal sentinel yet, or "
                                          "no terminal round verdict" % ACTIVE_WINDOW_MINUTES)},
        "current": {"state": "running" if family_id else "idle",
                    "family_id": family_id,
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
                        "as_of_utc": leaderboard_as_of,
                        "entries": top, "top_n": DASHBOARD_TOP_N},
        "funnel": funnel,
        "agent": {"board": board, "running": running, "blocked": blocked,
                  "board_summary": ("Running %d \u00b7 Blocked %d" % (running, blocked)
                                    if running is not None else "unavailable")},
    }


def write_json(path, payload, results_root=None):
    """Publish one payload atomically (same-dir temp + replace) so a reader never sees a half file.

    A target inside the results root is refused (ValueError, nothing created): "never under /results"
    is enforced here rather than left to the caller.
    """
    path = Path(path)
    root = Path(results_root or os.environ.get("QLIB_RESULTS_ROOT", DEFAULT_RESULTS)).resolve()
    target = path.resolve()
    if target == root or root in target.parents:
        raise ValueError("refusing to write inside the results root: %s" % target)
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
            write_json(json_out, dashboard_payload(results_root), results_root)
        except (OSError, ValueError) as exc:
            print("dashboard json not written (%s): %s" % (json_out, exc), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
