#!/usr/bin/env python3
"""Deterministic Discord progress feed for the quant pipeline.

This is observability only. It reads canonical preparation / Qlib / verdict artifacts and emits only
three milestone classes:

  PREPARATION -> QLIB START -> COMPLETE

It never evaluates health, never launches / retries / reorders anything, and never writes under the
results root. The only write is a small dedupe cursor outside the results tree. Missing state means
"bootstrap": remember all already-existing milestones and emit nothing, so deployment cannot replay
historical families into Discord.

Hermes no-agent cron is expected to deliver stdout directly to Discord. No stdout means no message.
"""

import argparse
import datetime
import json
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from production_handoff import (DEFAULT_RESULTS, HANDOFF_DIRNAME, PREPARATION_DIRNAME,  # noqa: E402
                                TERMINAL_VERDICTS)

STATE_SCHEMA_VERSION = 1
DEFAULT_STATE = Path(os.environ.get(
    "QUANT_PIPELINE_PROGRESS_STATE",
    "~/.hermes/state/quant_pipeline_progress_feed.json")).expanduser()
PREPARATION_STATUS = "preparation-status.json"
QLIB_STAGES = ("RUNNING_QLIB", "ARTIFACT_READY", "FAILED_SCRIPT")
KIND_ORDER = {"preparation": 0, "qlib": 1, "complete": 2}
MAX_JSON_BYTES = 8 * 1024 * 1024


def load_json(path):
    path = Path(path)
    try:
        info = path.lstat()
        if not path.is_file() or path.is_symlink() or info.st_size > MAX_JSON_BYTES:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, RecursionError):
        return None


def _timestamp(path, explicit=None):
    if isinstance(explicit, str) and explicit:
        try:
            return datetime.datetime.fromisoformat(explicit.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    try:
        return Path(path).stat().st_mtime
    except OSError:
        return 0.0


def _positive_int(value):
    return value if type(value) is int and value >= 0 else None


def _expected_evaluations(round_dir, attempt_dir=None):
    candidates = []
    if attempt_dir is not None:
        candidates.append(Path(attempt_dir) / "run-spec.json")
    candidates.append(Path(round_dir) / "round-spec.json")
    for path in candidates:
        doc = load_json(path)
        if not isinstance(doc, dict):
            continue
        expected = doc.get("expected")
        if isinstance(expected, dict):
            value = _positive_int(expected.get("expected_case_evaluations"))
            if value is not None:
                return value
        value = _positive_int(doc.get("expected_case_evaluations"))
        if value is not None:
            return value
    return None


def _short_generation(round_id, run_id=None):
    r = re.search(r"-r([1-9][0-9]*)$", round_id or "")
    u = re.search(r"-u([1-9][0-9]*)$", run_id or "")
    parts = []
    if r:
        parts.append("r%s" % r.group(1))
    if u:
        parts.append("u%s" % u.group(1))
    return "/".join(parts) if parts else (run_id or round_id or "-")


def _attempt_metrics(round_dir, verdict):
    run_id = verdict.get("run_id") if isinstance(verdict.get("run_id"), str) else None
    attempt = Path(round_dir) / "attempts" / run_id if run_id else None
    result = load_json(attempt / "result.json") if attempt is not None else None

    executed = None
    planned = _expected_evaluations(round_dir, attempt)
    survivors = None
    no_compute = False

    if isinstance(result, dict):
        executed = _positive_int(result.get("case_evaluations_total"))
        result_expected = _positive_int(result.get("expected_case_evaluations"))
        if planned is None:
            planned = result_expected
        survivors = _positive_int(result.get("cohort_survivor_count"))
        if survivors is None and isinstance(result.get("cohort_survivors"), list):
            survivors = len(result["cohort_survivors"])

    verdict_survivors = _positive_int(verdict.get("cohort_survivor_count"))
    if verdict_survivors is not None:
        survivors = verdict_survivors
    elif survivors is None and isinstance(verdict.get("cohort_survivors"), list):
        survivors = len(verdict["cohort_survivors"])

    attempts = verdict.get("attempts")
    if isinstance(attempts, dict) and _positive_int(attempts.get("launched")) == 0:
        no_compute = True
        executed = 0

    if survivors is None and attempt is not None:
        records = load_json(attempt / "artifacts" / "cohort_survivors.json")
        if isinstance(records, list):
            survivors = len(records)

    formal_promoted_survivors = survivors if verdict.get("verdict") == "PASS" else 0

    return {
        "run_id": run_id,
        "planned": planned,
        "executed": executed,
        "formal_promoted_survivors": formal_promoted_survivors,
        "no_compute": no_compute,
    }


def collect_events(results_root):
    root = Path(results_root)
    events = []

    preparation_root = root / HANDOFF_DIRNAME / PREPARATION_DIRNAME
    if preparation_root.is_dir():
        for status_path in sorted(preparation_root.glob("*/" + PREPARATION_STATUS)):
            doc = load_json(status_path)
            family_id = status_path.parent.name
            if (not isinstance(doc, dict) or doc.get("state") != "running" or
                    doc.get("family_id") != family_id):
                continue
            events.append({
                "key": "preparation|%s" % family_id,
                "kind": "preparation",
                "family_id": family_id,
                "sort_ts": _timestamp(status_path, doc.get("updated_at_utc")),
            })

    for family_dir in sorted(root.iterdir()) if root.is_dir() else []:
        if not family_dir.is_dir() or family_dir.name.startswith("_"):
            continue
        family_id = family_dir.name
        rounds = family_dir / "rounds"
        if not rounds.is_dir():
            continue

        for round_dir in sorted(path for path in rounds.iterdir() if path.is_dir()):
            round_id = round_dir.name
            attempts_dir = round_dir / "attempts"
            if attempts_dir.is_dir():
                for attempt_dir in sorted(path for path in attempts_dir.iterdir() if path.is_dir()):
                    run_id = attempt_dir.name
                    state_path = attempt_dir / "state.json"
                    state = load_json(state_path)
                    if not isinstance(state, dict) or state.get("stage") not in QLIB_STAGES:
                        continue
                    if (state.get("family_id") not in (None, family_id) or
                            state.get("round_id") not in (None, round_id) or
                            state.get("run_id") not in (None, run_id)):
                        continue
                    events.append({
                        "key": "qlib|%s|%s|%s" % (family_id, round_id, run_id),
                        "kind": "qlib",
                        "family_id": family_id,
                        "round_id": round_id,
                        "run_id": run_id,
                        "stage": state.get("stage"),
                        "planned": _expected_evaluations(round_dir, attempt_dir),
                        "sort_ts": _timestamp(state_path),
                    })

            verdict_path = round_dir / "verdict.json"
            verdict = load_json(verdict_path)
            if not isinstance(verdict, dict) or verdict.get("verdict") not in TERMINAL_VERDICTS:
                continue
            if verdict.get("family_id") != family_id or verdict.get("round_id") != round_id:
                continue
            metrics = _attempt_metrics(round_dir, verdict)
            events.append({
                "key": "complete|%s|%s" % (family_id, round_id),
                "kind": "complete",
                "family_id": family_id,
                "round_id": round_id,
                "verdict": verdict["verdict"],
                "sort_ts": _timestamp(verdict_path, verdict.get("decided_at_utc")),
                **metrics,
            })

    events.sort(key=lambda event: (event["sort_ts"], KIND_ORDER[event["kind"]], event["key"]))
    return events


def _state_inside_results(results_root, state_path):
    root = Path(results_root).expanduser().resolve()
    target = Path(state_path).expanduser().resolve(strict=False)
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return False


def load_state(path):
    path = Path(path)
    if not path.exists():
        return None
    doc = load_json(path)
    if (not isinstance(doc, dict) or doc.get("schema_version") != STATE_SCHEMA_VERSION or
            not isinstance(doc.get("seen"), list) or
            any(not isinstance(item, str) for item in doc["seen"])):
        raise ValueError("invalid progress-feed state: %s" % path)
    return doc


def write_state(path, seen):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "schema_version": STATE_SCHEMA_VERSION,
        "seen": sorted(set(seen)),
        "updated_at_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    fd, tmp = tempfile.mkstemp(prefix=".%s." % path.name, suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(doc, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def tick(results_root, state_path):
    if _state_inside_results(results_root, state_path):
        raise ValueError("progress-feed state must stay outside the results root")
    events = collect_events(results_root)
    state = load_state(state_path)
    if state is None:
        write_state(state_path, [event["key"] for event in events])
        return []

    seen = set(state["seen"])
    new_events = [event for event in events if event["key"] not in seen]
    if new_events:
        seen.update(event["key"] for event in new_events)
        write_state(state_path, seen)
    return new_events


def render(event):
    family_id = event["family_id"]
    if event["kind"] == "preparation":
        return "\n".join([
            "🟡 **PREPARATION**",
            "`%s`" % family_id,
            "Preparation started",
        ])

    if event["kind"] == "qlib":
        generation = _short_generation(event.get("round_id"), event.get("run_id"))
        planned = event.get("planned")
        planned_line = ("Planned: **%s evaluations**" % f"{planned:,}"
                        if planned is not None else "Planned: unavailable")
        return "\n".join([
            "🔵 **QLIB START**",
            "`%s` · %s" % (family_id, generation),
            planned_line,
        ])

    generation = _short_generation(event.get("round_id"), event.get("run_id"))
    executed, planned = event.get("executed"), event.get("planned")
    if event.get("no_compute"):
        execution_line = "Executed: **0 (no Qlib compute)**"
    elif executed is not None and planned is not None:
        execution_line = "Executed: **%s / %s**" % (f"{executed:,}", f"{planned:,}")
    elif executed is not None:
        execution_line = "Executed: **%s**" % f"{executed:,}"
    else:
        execution_line = "Executed: unavailable"
    promoted_survivors = event.get("formal_promoted_survivors")
    survivor_line = ("Formal promoted survivors: **%s**" % f"{promoted_survivors:,}"
                     if promoted_survivors is not None else "Formal promoted survivors: n/a")
    return "\n".join([
        "🏁 **COMPLETE**",
        "`%s` · %s · **%s**" % (family_id, generation, event.get("verdict")),
        execution_line,
        survivor_line,
    ])


def main():
    parser = argparse.ArgumentParser(description="read-only quant pipeline milestone feed")
    parser.add_argument("--results-root", default=os.environ.get("QLIB_RESULTS_ROOT", DEFAULT_RESULTS))
    parser.add_argument("--state", default=str(DEFAULT_STATE))
    args = parser.parse_args()

    if not Path(args.results_root).is_dir():
        sys.stderr.write("progress feed: results root not found: %s\n" % args.results_root)
        return 2
    try:
        events = tick(args.results_root, args.state)
    except (OSError, ValueError) as exc:
        sys.stderr.write("progress feed: %s\n" % exc)
        return 2
    if events:
        print("\n\n".join(render(event) for event in events))
    return 0


if __name__ == "__main__":
    sys.exit(main())
