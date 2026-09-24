#!/opt/homebrew/bin/python3
"""On-demand, read-only runtime observation for the n8n control plane (Phase 2).

One stateless projection of the canonical quant runtime, printed as JSON on stdout and derived only
from artifacts the runtime itself already wrote. It answers exactly the two questions the n8n canvas
must not answer itself:

  * `current` - which family owes the pipeline runtime work right now, and in which lifecycle state:
    `preflight` (registered, direct-Hermes adaptation/preflight in flight, no attempt yet),
    `qlib_active` (the authoritative attempt is running: verbatim stage + progress + cohort),
    `disposition` (the attempt published a terminal sentinel, its own round verdict is still missing),
    `terminal` (the round/family verdict is terminal and that evidence is recent), or `idle`.
  * `counts` - the runtime-side counters (registered/backtested families, cumulative workload
    evaluations, survivors/leaderboard, candidate pool queued/consumed, last pipeline advance) plus
    the quant watchdog's own health pass-through.

Semantics are never re-derived here: `production_handoff.runtime_state` (contract 14.4 selection and
the per-round verdict release rule) decides in-flight/terminal, `candidate_snapshot`'s own helpers
provide progress / cohort / backtested / workload / leaderboard / top-N, and health is
`candidate_snapshot.health_payload` (the watchdog's verdict passed through). This module only selects,
projects and labels with provenance; anything it cannot prove stays null and is listed in `gaps`.

Read-only by construction: the results root is a module default / one CLI argument, no file is ever
opened for writing, no subprocess is spawned, and neither the board nor the Kanban DB is consulted -
a Kanban status can never drive `current`. Consumers get this as an on-demand response (the n8n host
bridge action `runtime_observe_once`); no snapshot file is written, so there is no second truth to go
stale.

Usage: python3 runtime/runtime_observation.py [--results-root PATH] [--json]
Exit: 0 = observation printed (unreadable inputs are reported as null + gaps, never invented).
"""
import argparse
import datetime
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import candidate_snapshot as cs  # noqa: E402
import production_handoff as ph  # noqa: E402
from terminal_evidence import TERMINALS  # noqa: E402

SCHEMA = "quant-runtime-observation/v1"
# The complete lifecycle vocabulary of `current.state`; anything else would be a lie, so an
# unprovable family reports null (the consumer routes it to attention instead of guessing).
STATES = ("preflight", "qlib_active", "disposition", "terminal", "idle")
DEFAULT_RESULTS = ph.DEFAULT_RESULTS  # one source for the production root
CURRENT_RULE = ("production_handoff.runtime_state (contract 14.4): the family whose newest attempt is "
                "real runtime work inside the %d-minute active window - no terminal sentinel yet, or "
                "its own round's verdict still missing - else a registered family inside the %d-minute "
                "launch grace" % (ph.ACTIVE_WINDOW_MINUTES, ph.LAUNCH_GRACE_MINUTES))


def _gap(gaps, field, reason):
    """One reported gap per field: a null is always explained, never silent."""
    if not any(item["field"] == field for item in gaps):
        gaps.append({"field": field, "reason": reason})


def family_last_write(results_root, family_id, state):
    """Newest observable write of one family's own runtime evidence, as epoch seconds (None if none).

    Family-level files (the immutable family.json, the frozen direct-Hermes prompt, the worker's
    agent.log), every round-level spec/verdict and every attempt artifact: mtime is the objective
    "when did this family's evidence last change" signal the watchdog and the handoff guard use.
    """
    stamps = []
    family = Path(results_root) / family_id
    if state.get("activity"):
        stamps.append(state["activity"])
    for path in (family / "family.json", family / "agent-task.md", family / ph.AGENT_LOG):
        try:
            stamps.append(path.stat().st_mtime)
        except OSError:
            pass
    for path in sorted(family.glob("rounds/*/*.json")):
        try:
            stamps.append(path.stat().st_mtime)
        except OSError:
            pass
    return max(stamps) if stamps else None


def agent_projection(results_root, family_id):
    """Observable direct-Hermes launch evidence. Liveness itself is the worker's flock lease.

    A read-only observation must not probe that lease: acquiring it would look like ownership to the
    next C3 tick, and a peer's lock is never touched by an observer. What is observable without
    touching it is the frozen prompt, the log file and when the log was last written.
    """
    family = Path(results_root) / family_id
    log = family / ph.AGENT_LOG
    prompt = family / "agent-task.md"
    size = None
    if log.is_file():
        try:
            size = log.stat().st_size
        except OSError:
            size = None
    return {"prompt_frozen": prompt.is_file(),
            "prompt_path": str(prompt) if prompt.is_file() else None,
            "agent_log": str(log) if log.is_file() else None,
            "agent_log_bytes": size,
            "agent_log_last_write_utc": cs.mtime_utc(log),
            "lease_note": ("the .agent.lock flock is the worker's lease; an observer never probes or "
                           "acquires it")}


def terminal_round(results_root, family_id, family_doc, token):
    """The round whose verdict carries `token` (newest first) via the canonical per-round check."""
    rounds = Path(results_root) / family_id / "rounds"
    if not rounds.is_dir():
        return None
    for round_dir in sorted((path for path in rounds.iterdir() if path.is_dir()), reverse=True):
        if ph.round_verdict_token(round_dir, family_id, family_doc) == token:
            return round_dir.name
    return None


def last_advance(results_root):
    """(record, reason) - newest real pipeline advance, from the handoff's own append-only ledger.

    Rule: the newest line whose `outcome` is `advanced` and whose `action` is `appended` (a real
    registration - a dry-run `would_append` is not an advance). That line is what the handoff itself
    writes when it advances.
    ponytail: the ledger is read whole (append-only, hundreds of lines); if it ever grows to where a
    whole read hurts the tick, take a bounded tail and keep the same record shape.
    """
    path = Path(results_root) / ph.HANDOFF_DIRNAME / ph.LOG_FILENAME
    if not path.is_file():
        return None, "handoff ledger not found: %s" % path
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError as exc:
        return None, "handoff ledger unreadable: %s" % exc
    for line in reversed(lines):
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("outcome") == "advanced" \
                and record.get("action") == "appended" and not record.get("dry_run"):
            return {"at_utc": record.get("ran_at_utc"), "family_id": record.get("family_id"),
                    "outcome": record.get("outcome")}, None
    return None, "no advance record in the handoff ledger"


def pool_projection(results_root):
    """(payload, reason) - candidate pool total / consumed / queued, from canonical artifacts only.

    Consumed = a pool entry whose family was registered as runtime evidence
    (`<results>/<family_id>/family.json`, the immutable artifact the handoff writes when it advances);
    queued = the rest. The rule travels with the numbers so it can be audited, and an unreadable pool
    stays null instead of being guessed.
    """
    path = Path(results_root) / ph.HANDOFF_DIRNAME / ph.POOL_FILENAME
    doc = cs.load_json(path)
    candidates = doc.get("candidates") if isinstance(doc, dict) else None
    if not isinstance(candidates, list):
        return None, "candidate pool unreadable or carries no candidate list: %s" % path
    ids = [c.get("family_id") for c in candidates if isinstance(c, dict)]
    ids = [item for item in ids if isinstance(item, str) and item]
    registered = set(ph.read_families(str(results_root)))
    consumed = sum(1 for item in ids if item in registered)
    return ({"available": True, "path": str(path), "updated_at_utc": doc.get("updated_at_utc"),
             "total": len(ids), "consumed": consumed, "queued": len(ids) - consumed,
             "rule": ("consumed = pool entry with a registered <results>/<family_id>/family.json; "
                      "queued = the remainder")}, None)


def unresolved_families(states):
    """Families that still owe a decision with nothing live to decide it (C4/disposition territory).

    Derived only from canonical `runtime_state` fields: an attempt exists, nothing is in flight inside
    the active window, and neither that attempt's own round verdict nor a family verdict is published.
    """
    return [family_id for family_id, _doc, state in states
            if state.get("attempt") and not state.get("in_flight")
            and not state.get("round_verdict") and not state.get("verdict")]


def _stage_of(attempt):
    """The attempt's own verbatim runtime stage ("RUNNING_QLIB" / "ARTIFACT_READY" / "FAILED_SCRIPT")."""
    doc = cs.load_json(attempt / "state.json")
    return doc.get("stage") if isinstance(doc, dict) else None


def _progress_projection(results_root, family_id, attempt):
    """(stage, progress, cohort, note) for the live family's authoritative attempt.

    Progress is `candidate_snapshot.progress` - the canonical projection the dashboard shows - and is
    only published when it selected *the same* attempt this observation classified as live; otherwise
    the numbers are reported unavailable instead of being borrowed from another attempt.
    """
    stage, note, cohort = _stage_of(attempt), None, None
    pct, done, total, pstage, pnote, selected, _round = cs.progress(str(results_root), family_id)
    selected_path = getattr(selected, "path", selected)
    if selected is None or str(selected_path) != str(attempt):
        return stage, {"available": False, "pct": None, "done": None, "total": None, "text": None}, cohort, \
            ("progress unavailable: the canonical authoritative attempt (%s) differs from the live "
             "attempt (%s)" % (selected_path, attempt))
    progress = {"available": total is not None,
                "pct": round(pct, 1) if total is not None else None,
                "done": done if total is not None else None,
                "total": total,
                "text": ("%.1f%% (%s / %s)" % (pct, format(done, ","), format(total, ","))
                         if total is not None else "unavailable (%s)" % pnote)}
    if stage is None:
        stage = pstage
    if stage == cs.RUNNING_STAGE:
        symbol, timeframe = cs.latest_observable_cohort(selected)
        cohort = "%s / %s" % (symbol, timeframe) if symbol and timeframe else None
    return stage, progress, cohort, note


def live_current(results_root, family_doc, family_id, state, now):
    """`preflight` / `qlib_active` / `disposition` (or null) for the family the runtime works on now."""
    attempt = Path(state["attempt"]) if state.get("attempt") else None
    round_id = attempt.parents[1].name if attempt else None
    stage, progress, cohort = None, {"available": False, "pct": None, "done": None, "total": None,
                                     "text": None}, None
    note = state.get("why")
    if family_doc.get("_unparsable"):
        classification = None
        note = "family.json unreadable: fail-closed, the runtime holds the pipeline until it is resolved"
    elif attempt is None:
        classification = "preflight"
        note = ("registered and inside the %d-minute launch grace: the direct Hermes worker is "
                "adapting/preflighting this family (no attempt directory yet)" % ph.LAUNCH_GRACE_MINUTES)
    else:
        terminals = [terminal for terminal in TERMINALS if (attempt / terminal).is_file()]
        stage, progress, cohort, extra = _progress_projection(results_root, family_id, attempt)
        if not terminals:
            classification = "qlib_active"
        else:
            classification = "disposition"
            note = "attempt published %s; its own round verdict is still missing" % terminals[0]
        if extra:
            note = ("%s; %s" % (note, extra)) if note else extra
    return {"state": classification, "family_id": family_id, "round_id": round_id,
            "attempt": Path(state["attempt"]).name if attempt else None,
            "attempt_path": state.get("attempt"), "stage": stage, "verdict": None,
            "progress": progress, "cohort": cohort, "last_activity_utc": cs.iso_utc(state.get("activity")),
            "age_minutes": (round((now - state["activity"]) / 60.0, 1)
                            if state.get("activity") else None),
            "agent": agent_projection(results_root, family_id), "why": note,
            "rule": CURRENT_RULE}


def settled_current(results_root, states, now):
    """`terminal` when the newest round/family verdict is recent evidence, otherwise `idle`."""
    evidence = []
    for family_id, family_doc, state in states:
        token = state.get("round_verdict") or state.get("verdict")
        if token:
            evidence.append((family_last_write(results_root, family_id, state), family_id,
                             family_doc, state, token))
    unresolved = unresolved_families(states)
    base = {"family_id": None, "round_id": None, "attempt": None, "attempt_path": None,
            "stage": None, "verdict": None, "progress": {"available": False, "pct": None, "done": None,
                                                         "total": None, "text": None},
            "cohort": None, "last_activity_utc": None, "age_minutes": None,
            "agent": None, "rule": CURRENT_RULE}
    if not evidence:
        base.update({"state": "idle", "why": "no family holds a live attempt and no terminal verdict "
                                             "exists; nothing owes the pipeline runtime work",
                     "unresolved_families": unresolved})
        return base
    last, family_id, family_doc, state, token = max(evidence, key=lambda item: (item[0] or 0.0, item[1]))
    age_minutes = round((now - last) / 60.0, 1) if last is not None else None
    inside = last is not None and (now - last) <= ph.ACTIVE_WINDOW_MINUTES * 60.0
    if not inside:
        base.update({"state": "idle",
                     "why": ("the newest terminal evidence (%s %s, %s min old) is outside the %d-minute "
                             "active window; no family owes the pipeline runtime work"
                             % (family_id, token, age_minutes, ph.ACTIVE_WINDOW_MINUTES)),
                     "unresolved_families": unresolved})
        return base
    attempt = Path(state["attempt"]) if state.get("attempt") else None
    base.update({"state": "terminal", "family_id": family_id,
                 "round_id": (attempt.parents[1].name if attempt
                              else terminal_round(results_root, family_id, family_doc, token)),
                 "attempt": attempt.name if attempt else None,
                 "attempt_path": state.get("attempt"), "verdict": token,
                 "stage": _stage_of(attempt) if attempt else None,
                 "last_activity_utc": cs.iso_utc(last), "age_minutes": age_minutes,
                 "agent": agent_projection(results_root, family_id),
                 "why": ("round/family verdict %s published %s min ago and no family holds live runtime "
                         "work" % (token, age_minutes)),
                 "unresolved_families": unresolved})
    return base


def observe(results_root=None, now=None):
    """The whole observation. Read-only; unreadable inputs become null + a gap, never a guess."""
    results_root = results_root or os.environ.get("QLIB_RESULTS_ROOT", DEFAULT_RESULTS)
    now = time.time() if now is None else now
    gaps = []
    root_present = Path(results_root).is_dir()
    if not root_present:
        _gap(gaps, "results_root", "results root not readable: %s" % results_root)
    families = ph.read_families(results_root) if root_present else {}
    states = [(family_id, doc, ph.runtime_state(results_root, family_id, doc, now=now))
              for family_id, doc in sorted(families.items())]
    in_flight = [item for item in states if item[2]["in_flight"]]
    if not root_present:
        # Fail-closed: an unreadable results root proves nothing, so no lifecycle state is claimed.
        current = {"state": None, "family_id": None, "round_id": None, "attempt": None,
                   "attempt_path": None, "stage": None, "verdict": None,
                   "progress": {"available": False, "pct": None, "done": None, "total": None,
                                "text": None},
                   "cohort": None, "last_activity_utc": None, "age_minutes": None, "agent": None,
                   "why": "results root not readable: %s" % results_root, "rule": CURRENT_RULE}
    elif in_flight:
        family_id, family_doc, state = max(in_flight,
                                           key=lambda item: (item[2]["activity"] or 0.0, item[0]))
        current = live_current(results_root, family_doc, family_id, state, now)
    else:
        current = settled_current(results_root, states, now)

    counts: dict = {"families_registered": None, "families_backtested": None,
                    "families_in_flight": None, "families_unresolved": None,
                    "workload_evaluations": None, "workload_grid_artifacts": None,
                    "survivors": None, "leaderboard_count": None, "leaderboard_shown": None,
                    "candidates_pool_total": None, "candidates_consumed": None,
                    "candidates_queued": None, "last_pipeline_advance_utc": None,
                    "last_pipeline_advance_family_id": None}
    pool, leaderboard_entries = None, None
    if root_present:
        backtested, registered = cs.backtested_counts(results_root)
        evaluations, artifacts = cs.cumulative_backtest_workload(results_root)
        entries = cs.leaderboard_entries(results_root)
        counts.update({"families_registered": registered, "families_backtested": backtested,
                       "families_in_flight": len(in_flight),
                       "families_unresolved": len(unresolved_families(states)),
                       "workload_evaluations": evaluations, "workload_grid_artifacts": artifacts,
                       "survivors": len(entries), "leaderboard_count": len(entries),
                       "leaderboard_shown": min(len(entries), cs.DASHBOARD_TOP_N)})
        if entries:
            leaderboard_entries = cs.top_entries(entries)
        pool, reason = pool_projection(results_root)
        if pool is None:
            _gap(gaps, "candidate_pool", reason)
        else:
            counts.update({"candidates_pool_total": pool["total"],
                           "candidates_consumed": pool["consumed"],
                           "candidates_queued": pool["queued"]})
        advance, reason = last_advance(results_root)
        if advance is None:
            _gap(gaps, "last_pipeline_advance", reason)
        else:
            counts.update({"last_pipeline_advance_utc": advance["at_utc"],
                           "last_pipeline_advance_family_id": advance["family_id"]})
    else:
        _gap(gaps, "counts", "results root not readable; runtime counts stay null")
    if current["state"] is None:
        _gap(gaps, "current.state", current["why"] or "current family is not provable")
    # The funnel (workload / wiki_brain / backtested) is `candidate_snapshot.funnel_view` verbatim -
    # the same computation the dashboard JSON carries, so the two can never disagree about it.
    funnel = cs.funnel_view(results_root, now=datetime.datetime.fromtimestamp(now))

    return {
        "schema": SCHEMA,
        "generated_at_utc": ph.now_utc(),
        "read_only": True,
        "scope_note": ("On-demand projection of the canonical quant runtime for the n8n control plane. "
                       "It starts, stops, retries and ranks nothing, writes nothing, and never reads a "
                       "Kanban status."),
        "results_root": results_root,
        "results_root_readable": root_present,
        "current": current,
        "funnel": funnel,
        "counts": counts,
        "pool": pool,
        "leaderboard": {"available": bool(leaderboard_entries), "count": counts["leaderboard_count"],
                        "shown": counts["leaderboard_shown"], "top_n": cs.DASHBOARD_TOP_N,
                        "entries": leaderboard_entries or []},
        "health": cs.health_payload(),
        "sources": [
            {"id": "runtime_results_root", "kind": "canonical runtime artifacts (read-only)",
             "path": results_root, "readable": root_present},
            {"id": "runtime_state_selection", "kind": "canonical selection semantics",
             "path": "runtime/production_handoff.py:runtime_state + runtime/candidate_snapshot.py",
             "readable": root_present, "note": CURRENT_RULE},
            {"id": "runtime_watchdog_state", "kind": "watchdog state pass-through (read-only)",
             "path": str(cs.WATCHDOG_STATE), "readable": None},
        ],
        "gaps": gaps,
    }


def main():
    parser = argparse.ArgumentParser(description="on-demand read-only quant runtime observation (Phase 2)")
    parser.add_argument("--results-root", default=os.environ.get("QLIB_RESULTS_ROOT", DEFAULT_RESULTS))
    parser.add_argument("--json", action="store_true", help="pretty-print instead of one compact line")
    args = parser.parse_args()
    payload = observe(results_root=args.results_root)
    print(json.dumps(payload, indent=2 if args.json else None, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
