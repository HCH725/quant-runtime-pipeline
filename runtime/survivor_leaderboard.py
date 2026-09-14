#!/usr/bin/env python3
"""Forward evidence ingestion + survivor leaderboard (Contract v1.8.0, sections 27.3 / 27.5).

The post-survivor lifecycle is: full backtest -> frozen survivor bundle -> forward evidence ->
survivor leaderboard -> champion candidate / challenger -> (future) paper/testnet/live.

Two file-based operations live here, and nothing else:

  `forward`      validate ONE pre-computed forward slice for one frozen survivor and append it,
                 verbatim, as one line of `<results-root>/_survivors/forward/<survivor_id>.jsonl`.
                 The slice must be post-freeze (its start is later than the round's registered
                 research data end), must not overlap or repeat an already recorded slice, and
                 must carry the survivor's own frozen params/bundle identity - so a retuned cell
                 can never be filed as evidence for its incumbent (contract 27.4).  It must also
                 name the run that produced it (`source_run`: attempt dir + terminal `DONE`
                 sentinel + the sentinel's recorded `result.json` checksum), and its numbers must
                 equal that pinned `result.json`'s `forward_slice` block field by field: a
                 self-declared slice is not evidence (contract 27.3; v1.5.0 audit finding F2).  The
                 file is append-only; every line is read back before the command reports success.

  `leaderboard`  rebuild the survivor index in memory, aggregate each survivor's forward
                 evidence, and write `<results-root>/_survivors/leaderboard.{json,csv}` with a
                 transparent deterministic ordering (no opaque weighted score) and a Top-10
                 shortlist.

Neither operation computes a backtest: the numbers in a slice come from a run of the existing
strategy/Qlib execution semantics, which the slice's `source_run` points at - by an absolute
attempt dir inside the results tree (v1.5.2) - and which this tool re-verifies from disk (sentinel
+ result checksums) before ranking on it.  Nothing here is a service, daemon, queue or registry,
nor into a round or attempt directory, and since v1.6.0 each row also carries the non-ranking
evidence drill-back pointer (`evidence_package_status` / `evidence_manifest_path` /
`evidence_manifest_sha256`, contract 28.6), which never enters the ordering tuple and never gates
anything.
nothing here writes into a round or attempt directory (the only writable subtree is `_survivors/**`,
contract 27.1: `--out-dir` and the forward append are both checked against it, the reserved root
must not be a symlink, and the target must realpath into it), and nothing here changes a verdict:
the Top-10 is a ranking/selection aid, so
falling out of it is not a rejection, and a `champion_candidate` is a research shortlist entry -
v1.5 never trades and never allocates capital.

usage:
  python3 runtime/survivor_leaderboard.py leaderboard [--results-root <dir>] [--out-dir <dir>]
                                            [--check] [--json]
  python3 runtime/survivor_leaderboard.py forward --survivor-id <id> --slice <file>
                                            [--results-root <dir>] [--json]
exit: 0 = ok, 1 = refused/mismatch, 2 = usage error
"""
import argparse
import csv
import hashlib
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import survivor_index as si  # noqa: E402
import survivor_evidence as se  # noqa: E402
import parameter_contract as pc  # noqa: E402

SCHEMA_VERSION = 1
KIND = "survivor_leaderboard"
CONTRACT_VERSION = "v1.8.0"
CONTRACT_SECTION = "27.3 / 27.5"
SLICE_SCHEMA_VERSION = 2
TOP_N = 10
EVIDENCE_STATES = ("FROZEN_ONLY", "ACCUMULATING", "FORWARD_POSITIVE", "FORWARD_DEGRADED")

# Contract 27.3: the fields a forward slice must record.  `fees`/`funding`/`slippage_ticks` are
# the realized cost numbers; `cost_model` and `execution_semantics` say which assumptions and
# which existing runner produced them; `source_run` says WHERE those numbers can be re-read from
# (see SOURCE_RUN_REQUIRED) so the slice is traceable instead of merely self-declared.
SLICE_REQUIRED = {
    "survivor_id": "str", "bundle_identity_sha256": "str", "params_sha256": "str",
    "data_start": "date", "data_end": "date", "data_snapshot": "str",
    "execution_semantics": "str", "cost_model": "str", "episodes": "count",
    "net_pnl": "num", "return_pct": "num", "sharpe": "num", "max_dd_pct": "num",
    "fees": "num", "funding": "num", "slippage_ticks": "count", "produced_at_utc": "str",
    "source_run": "object",
}

# Contract 27.3: the provenance of a slice.  `attempt_dir` is the (absolute) directory of the run
# that produced the numbers, `<attempt_dir>/DONE` is that run's terminal sentinel published by
# `runtime/terminal_evidence.py`, and `result_sha256` must be the checksum the sentinel recorded
# for `<attempt_dir>/result.json`.  The run's `result.json` must carry a `forward_slice` block that
# equals the slice field by field.  A slice without this cannot be checked against any real
# computation, so it is refused instead of being ranked as evidence.
SOURCE_RUN_REQUIRED = ("attempt_dir", "run_id", "kanban_task_id", "sentinel_sha256", "result_sha256")
SOURCE_RUN_SENTINEL_NAME = "DONE"
SOURCE_RUN_RESULT_NAME = "result.json"
SOURCE_RUN_BLOCK = "forward_slice"

# Contract 27.5 ordering: transparent, deterministic, recomputable by hand.  FORWARD evidence
# always sorts above the frozen fallback; within a group the tuple below applies, NULLS LAST.
ORDERING_RULE = [
    "has_forward DESC (post-freeze evidence outranks the frozen OOS fallback)",
    "forward_sharpe DESC NULLS LAST",
    "forward_return_pct DESC NULLS LAST",
    "forward_max_dd_pct abs() ASC NULLS LAST (a shallower drawdown ranks higher)",
    "oos_sharpe DESC",
    "robustness_stress_floor_net_pnl DESC (worst of the four stress reruns; same base capital)",
    "neighbourhood same_sign_fraction DESC",
    "survivor_id lexical ASC (last resort, so the order is total and reproducible)",
]
EVIDENCE_STATE_RULE = {
    "FROZEN_ONLY": "no forward slice recorded: frozen OOS/full/robustness evidence only",
    "ACCUMULATING": ("forward slices exist but their total episodes are below the survivor's "
                     "frozen OOS episode count, i.e. the unseen sample is still shorter than "
                     "the window the survivor was validated on"),
    "FORWARD_POSITIVE": ("enough forward episodes and forward net_pnl > 0 AND forward sharpe > 0"),
    "FORWARD_DEGRADED": ("enough forward episodes and forward net_pnl <= 0 OR forward sharpe <= 0"),
}
EVIDENCE_STATE_NOTE = ("the state is descriptive only: it never rewrites a PASS/REJECT and "
                       "never gates anything")

CSV_COLUMNS = [
    "rank", "in_top10", "champion_candidate", "evidence_state", "survivor_id", "family_id",
    "cohort", "symbol", "timeframe", "strategy_window", "strategy_discount",
    "dca_spacing_pct", "dca_size_multiplier", "dca_breakeven_tp_pct", "dca_invalidation_pct",
    "params_sha256", "forward_slices", "forward_episodes", "forward_net_pnl",
    "forward_return_pct", "forward_sharpe", "forward_max_dd_pct", "last_evidence_end",
    "oos_sharpe", "oos_net_pnl", "full_sharpe", "full_net_pnl",
    "robustness_stress_floor_net_pnl", "robustness_stress_floor_grid",
    "neighbourhood_same_sign_fraction", "research_data_cutoff", "bundle_identity_sha256",
    "bundle_sha256", "bundle_path",
    # Contract 28.6: preservation coverage drill-back.  These three describe WHERE the execution
    # evidence package for this row is, or that it is not preserved yet.  They are deliberately
    # NOT part of ORDERING_RULE / sort_key: a package appearing or disappearing must never move a
    # rank, change a Top-10 membership, or feed `evidence_state`/`champion_candidate`.
    "evidence_package_status", "evidence_manifest_path", "evidence_manifest_sha256",
]

# Legacy A v2 CSV columns (the family-specific per-param columns).
LEGACY_CSV_PARAM_COLUMNS = [
    "strategy_window", "strategy_discount",
    "dca_spacing_pct", "dca_size_multiplier", "dca_breakeven_tp_pct", "dca_invalidation_pct",
]

# Generic CSV columns (replaces per-family param columns for non-A families).
GENERIC_CSV_PARAM_COLUMNS = [
    "strategy_params_canonical_json", "strategy_params_sha256",
    "dca_params_canonical_json", "row_fields_json",
]


def csv_columns(contract=None):
    """The CSV column set: legacy A v2 keeps its per-param columns; generic families use the
    four generic columns.  The rest of the header is identical."""
    base = [c for c in CSV_COLUMNS
            if c not in ("strategy_window", "strategy_discount",
                         "dca_spacing_pct", "dca_size_multiplier",
                         "dca_breakeven_tp_pct", "dca_invalidation_pct")]
    # F1 remediation: None sentinel must NOT default to legacy A.  Only an explicit
    # LEGACY_A_FAMILY_ID match uses the legacy per-param columns; everything else
    # (including None) uses the generic canonical JSON columns.
    fam = contract.get("family_id") if isinstance(contract, dict) else None
    if fam == pc.LEGACY_A_FAMILY_ID:
        # ponytail: insert legacy param columns at the same position as the original
        return (base[:9] + LEGACY_CSV_PARAM_COLUMNS + base[9:])
    return (base[:9] + GENERIC_CSV_PARAM_COLUMNS + base[9:])


def is_number(value):
    """True iff value is a real, finite JSON number (bools, strings and NaN are not)."""
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def is_date(value):
    return (isinstance(value, str) and len(value) == 10 and value[4] == "-" and value[7] == "-"
            and value.replace("-", "").isdigit())


def is_checksum(value):
    return (isinstance(value, str) and len(value) == 71 and value.startswith("sha256:")
            and all(c in "0123456789abcdef" for c in value[7:]))


def inside(path, parent):
    """True when the (already realpath-resolved) `path` sits inside `parent`."""
    return path == parent or path.startswith(parent + os.sep)


def source_run_problems(slice_doc, entry, results_root):
    """Contract 27.3: a slice only counts as evidence when it can be re-read from a real run.

    v1.5.0 accepted any self-declared JSON document, so a slice with invented episodes/PnL/Sharpe
    and no reference at all became `FORWARD_POSITIVE`, `champion_candidate=true` and rank 1 (audit
    finding F2).  The numbers must therefore come from a run the pipeline itself recorded:

        slice.source_run  ->  <attempt_dir>/DONE      (terminal sentinel, hash-pinned)
                          ->  the sentinel's recorded checksum for <attempt_dir>/result.json
                          ->  result.json's `forward_slice` block, equal to the slice field by field

    That makes a bare JSON blob insufficient (the numbers have to exist in a hash-pinned run
    artifact), keeps the evidence re-verifiable at read time (deleting or editing the run after the
    fact fails closed instead of ranking on a claim), and reuses the existing sentinel machinery
    instead of inventing a second runner.
    """
    source = slice_doc.get("source_run")
    if not isinstance(source, dict):
        return ["slice carries no source_run object: forward evidence must name the run that "
                "produced its numbers (attempt dir, terminal DONE sentinel, result checksum) and "
                "match that run's result.json - a self-declared slice is never rankable evidence "
                "(contract 27.3)"]
    problems = []
    for key in ("attempt_dir", "run_id", "kanban_task_id"):
        value = source.get(key)
        if not (isinstance(value, str) and value.strip()):
            problems.append("source_run.%s is missing or not a non-empty string (%r)" % (key, value))
    for key in ("sentinel_sha256", "result_sha256"):
        if not is_checksum(source.get(key)):
            problems.append("source_run.%s is not a sha256:<64 hex> checksum (%r)"
                            % (key, source.get(key)))
    if problems:
        return problems

    if not os.path.isabs(source["attempt_dir"]):
        # v1.5.2 / audit finding F2: `realpath()` resolves a relative attempt_dir against the
        # READER's cwd, so the very same slice was verifiable from one directory and not from
        # another (and could name a directory the results tree never contained).  Provenance that
        # depends on where the tool happens to be run from is not provenance.
        problems.append("source_run.attempt_dir %r is not an absolute path: a relative attempt "
                        "dir resolves against the reader's working directory, so the same slice "
                        "would be verifiable from one cwd and unverifiable from another (contract "
                        "27.3)" % source["attempt_dir"])
        return problems

    attempt = os.path.realpath(source["attempt_dir"])
    root = os.path.realpath(os.path.abspath(results_root))
    survivors_root = si.write_boundary(root)
    if not inside(attempt, root):
        problems.append("source_run.attempt_dir %s is outside the results root %s: forward evidence "
                        "may only be read from a run inside the pipeline's own results tree"
                        % (attempt, root))
    elif inside(attempt, survivors_root):
        problems.append("source_run.attempt_dir %s sits inside the post-survivor namespace %s: the "
                        "layer that consumes the evidence may not also be its source (contract "
                        "27.1/27.3)" % (attempt, survivors_root))
    if not os.path.isdir(attempt):
        problems.append("source_run.attempt_dir %s is not a directory: the producing run's "
                        "artifacts are gone, so the numbers cannot be re-read" % attempt)
        return problems
    if os.path.basename(attempt) != source["run_id"]:
        problems.append("source_run.run_id %r is not the attempt directory name %r (the terminal "
                        "sentinel is published under the run id)"
                        % (source["run_id"], os.path.basename(attempt)))

    sentinel_path = os.path.join(attempt, SOURCE_RUN_SENTINEL_NAME)
    if not os.path.isfile(sentinel_path):
        problems.append("the source run has no terminal %s sentinel at %s: only a terminally DONE "
                        "run (runtime/terminal_evidence.py) can be the source of forward evidence"
                        % (SOURCE_RUN_SENTINEL_NAME, sentinel_path))
        return problems
    actual_sentinel = si.sha256_file(sentinel_path)
    if actual_sentinel != source["sentinel_sha256"]:
        problems.append("source_run.sentinel_sha256 %s is not the sentinel on disk %s"
                        % (source["sentinel_sha256"], actual_sentinel))
    try:
        sentinel = si.load_json(sentinel_path)
    except (OSError, ValueError) as exc:
        problems.append("the source run's sentinel is unreadable/unparsable (%s)" % exc)
        return problems
    if not isinstance(sentinel, dict):
        problems.append("the source run's sentinel is not an object")
        return problems
    if sentinel.get("status") != SOURCE_RUN_SENTINEL_NAME:
        problems.append("the source run is not terminally DONE (status %r): a FAILED/INCOMPLETE "
                        "run never produces evidence" % sentinel.get("status"))
    for key, want in (("run_id", source["run_id"]), ("task_id", source["kanban_task_id"]),
                      ("family_id", entry["family_id"])):
        if sentinel.get(key) != want:
            problems.append("the source run's sentinel records %s %r, not %r"
                            % (key, sentinel.get(key), want))
    if sentinel.get("run_id") == entry["run_id"]:
        problems.append("the source run is the frozen research run %r itself: forward evidence "
                        "must come from a later, distinct run" % entry["run_id"])
    recorded = (sentinel.get("artifact_checksums") or {}).get(SOURCE_RUN_RESULT_NAME)
    if recorded != source["result_sha256"]:
        problems.append("source_run.result_sha256 %s is not the checksum the source run's terminal "
                        "sentinel recorded for %s (%r)"
                        % (source["result_sha256"], SOURCE_RUN_RESULT_NAME, recorded))

    result_path = os.path.join(attempt, SOURCE_RUN_RESULT_NAME)
    if not os.path.isfile(result_path):
        problems.append("the source run has no %s at %s" % (SOURCE_RUN_RESULT_NAME, result_path))
        return problems
    actual_result = si.sha256_file(result_path)
    if actual_result != source["result_sha256"]:
        problems.append("the source run's %s hashes to %s, not the declared %s: the run artifact "
                        "changed after the slice was written (contract 27.3)"
                        % (SOURCE_RUN_RESULT_NAME, actual_result, source["result_sha256"]))
        return problems
    try:
        result = si.load_json(result_path)
    except (OSError, ValueError) as exc:
        problems.append("the source run's %s is unreadable/unparsable (%s)"
                        % (SOURCE_RUN_RESULT_NAME, exc))
        return problems
    if not isinstance(result, dict):
        problems.append("the source run's %s is not an object" % SOURCE_RUN_RESULT_NAME)
        return problems
    for key, want in (("family_id", entry["family_id"]), ("run_id", source["run_id"])):
        if result.get(key) != want:
            problems.append("the source run's %s records %s %r, not %r"
                            % (SOURCE_RUN_RESULT_NAME, key, result.get(key), want))
    block = result.get(SOURCE_RUN_BLOCK)
    if not isinstance(block, dict):
        problems.append("the source run's %s carries no %s block: the slice numbers must be read "
                        "off the run's own result artifact, not declared by the slice itself "
                        "(contract 27.3)"
                        % (SOURCE_RUN_RESULT_NAME, SOURCE_RUN_BLOCK))
        return problems
    for key in SLICE_REQUIRED:
        if key == "source_run":
            continue
        if block.get(key) != slice_doc.get(key):
            problems.append("slice field %r (%r) does not match the source run's %s.%s (%r): the "
                            "numbers must come from the pinned run result (contract 27.3)"
                            % (key, slice_doc.get(key), SOURCE_RUN_RESULT_NAME, SOURCE_RUN_BLOCK,
                               block.get(key)))
    return problems


def slice_problems(slice_doc, entry, existing, results_root):
    """Contract 27.3: refuse anything that would not be genuine, post-freeze, unseen evidence."""
    problems = []
    if not isinstance(slice_doc, dict):
        return ["slice is not a JSON object"]
    for key, kind in SLICE_REQUIRED.items():
        value = slice_doc.get(key)
        if kind == "str" and not (isinstance(value, str) and value.strip()):
            problems.append("slice is missing required non-empty string %r" % key)
        elif kind == "date" and not is_date(value):
            problems.append("slice field %r is not a YYYY-MM-DD date (%r)" % (key, value))
        elif kind == "count" and not (isinstance(value, int) and not isinstance(value, bool)
                                      and value > 0):
            problems.append("slice field %r is not a positive integer (%r)" % (key, value))
        elif kind == "num" and not is_number(value):
            problems.append("slice field %r is not a finite number (%r)" % (key, value))
        elif kind == "object" and not isinstance(value, dict):
            problems.append("slice field %r is not an object (%r)" % (key, value))
    if problems:
        return problems

    if slice_doc["data_start"] > slice_doc["data_end"]:
        problems.append("slice data_start %s is after data_end %s"
                        % (slice_doc["data_start"], slice_doc["data_end"]))
    if slice_doc["survivor_id"] != entry["survivor_id"]:
        problems.append("slice survivor_id %r is not the target survivor %r"
                        % (slice_doc["survivor_id"], entry["survivor_id"]))
    # The retune guard (contract 27.4): evidence for an incumbent may only carry the incumbent's
    # own frozen params and bundle identity.  Anything else is a different (challenger) cell and
    # must go through the full backtest gate as a new family before it can be ranked at all.
    if slice_doc["params_sha256"] != entry["params_sha256"]:
        problems.append("slice params_sha256 %r is not the incumbent's frozen params %r: changed "
                        "strategy/DCA params may never be filed as evidence for a survivor - "
                        "they require a new challenger family (contract 27.4)"
                        % (slice_doc["params_sha256"], entry["params_sha256"]))
    if slice_doc["bundle_identity_sha256"] != entry["bundle_identity_sha256"]:
        problems.append("slice source bundle identity %r is not the survivor's frozen bundle %r"
                        % (slice_doc["bundle_identity_sha256"], entry["bundle_identity_sha256"]))
    cutoff = entry.get("research_data_cutoff")
    if not cutoff:
        problems.append("the survivor has no verified research data cutoff, so no slice can be "
                        "accepted as post-freeze")
    elif slice_doc["data_start"] <= cutoff:
        problems.append("slice data_start %s is not later than the survivor's research data "
                        "cutoff %s: data at or before the cutoff was available to the research "
                        "run and is never unseen forward evidence (contract 27.3)"
                        % (slice_doc["data_start"], cutoff))
    for prior in existing:
        if not (slice_doc["data_start"] > prior["data_end"] or
                slice_doc["data_end"] < prior["data_start"]):
            problems.append("slice %s..%s overlaps the recorded slice %s..%s: forward evidence "
                            "is append-only and never re-covers a window"
                            % (slice_doc["data_start"], slice_doc["data_end"],
                               prior["data_start"], prior["data_end"]))
    problems.extend(source_run_problems(slice_doc, entry, results_root))
    return problems


def read_slices(path, entry, results_root):
    """Read one survivor's append-only jsonl, re-checking its integrity (never trust the file)."""
    if not os.path.exists(path):
        return [], 0
    slices = []
    with open(path) as fh:
        for lineno, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                doc = json.loads(line)
            except ValueError as exc:
                return None, lineno  # unparsable evidence: fail closed at aggregation time
            if not isinstance(doc, dict):
                return None, lineno
            problems = slice_problems(doc, entry, slices, results_root)
            if problems:
                return None, lineno
            slices.append(doc)
    slices.sort(key=lambda s: s["data_start"])
    return slices, 0


def aggregate(slices):
    """Deterministic aggregation of a survivor's forward slices (documented, not compounded)."""
    if not slices:
        return {"has_forward": False, "slices": 0}
    episodes = sum(s["episodes"] for s in slices)
    return {
        "has_forward": True,
        "slices": len(slices),
        "episodes": episodes,
        "net_pnl": sum(s["net_pnl"] for s in slices),
        # ponytail: slice returns are summed, not compounded - the slices are disjoint windows
        # measured on a shared base capital, so a chain-linked return would invent equity marks
        # the pipeline never produced.  Chain-link when the forward runner reports equity paths.
        "return_pct": sum(s["return_pct"] for s in slices),
        "sharpe": sum(s["sharpe"] * s["episodes"] for s in slices) / episodes,
        "max_dd_pct": min(s["max_dd_pct"] for s in slices),
        "first_data_start": min(s["data_start"] for s in slices),
        "last_data_end": max(s["data_end"] for s in slices),
    }


def evidence_state(forward, entry):
    if not forward["has_forward"]:
        return "FROZEN_ONLY"
    required = entry.get("oos", {}).get("episodes")
    if is_number(required) and forward["episodes"] < required:
        return "ACCUMULATING"
    if forward["net_pnl"] > 0 and forward["sharpe"] > 0:
        return "FORWARD_POSITIVE"
    return "FORWARD_DEGRADED"


def desc(value):
    """DESC with NULLS LAST, expressed as an ascending key."""
    return (0, -value) if is_number(value) else (1, 0)


def asc_abs(value):
    return (0, abs(value)) if is_number(value) else (1, 0)


def sort_key(row):
    return (0 if row["forward"]["has_forward"] else 1,
            desc(row["forward"].get("sharpe")),
            desc(row["forward"].get("return_pct")),
            asc_abs(row["forward"].get("max_dd_pct")),
            desc(row["oos"]["sharpe"]),
            desc(row["robustness_stress_floor_net_pnl"]),
            desc(row["neighbourhood"]["same_sign_fraction"]),
            row["survivor_id"])


def build(results_root):
    """Rebuild index + leaderboard rows from disk.  Returns (doc, rows, problems)."""
    results_root = os.path.abspath(results_root)
    index, problems = si.build(results_root)
    if problems:
        return None, None, problems
    assert index is not None

    rows = []
    for entry in index["survivors"]:
        path = si.forward_path(results_root, entry["survivor_id"])
        slices, bad_line = read_slices(path, entry, results_root)
        if slices is None:
            return None, None, ["%s: recorded forward evidence at %s is not a valid post-freeze "
                                "slice for this survivor (line %d); refusing to rank on "
                                "unverified evidence" % (entry["survivor_id"], path, bad_line)]
        forward = aggregate(slices)
        row = dict(entry)
        row["forward"] = forward
        row["evidence_state"] = evidence_state(forward, entry)
        row["last_evidence_end"] = forward.get("last_data_end")
        # Contract 28.6: a non-ranking drill-back pointer only.  A missing package leaves this
        # row, the ranking and the Top-10 exactly as they were (the leaderboard stays rc=0).
        row.update(se.pointer(results_root, entry))
        rows.append(row)

    rows.sort(key=sort_key)
    for position, row in enumerate(rows, 1):
        row["rank"] = position
        row["in_top10"] = position <= TOP_N
        # Contract 27.6: a champion candidate is a research shortlist entry only - Top-10 plus
        # positive unseen evidence.  v1.5 issues no live signal and allocates no capital.
        row["champion_candidate"] = bool(row["in_top10"]
                                         and row["evidence_state"] == "FORWARD_POSITIVE")

    doc = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "contract": "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md %s" % CONTRACT_VERSION,
        "contract_section": CONTRACT_SECTION,
        "results_root": results_root,
        "derived": True,
        "source_of_truth": index["source_of_truth"],
        "ordering_rule": ORDERING_RULE,
        "evidence_state_rule": EVIDENCE_STATE_RULE,
        "notes": ("ranking/selection aid only.  It is NOT a PASS/REJECT gate: falling out of the "
                  "Top-10 is not a rejection, and a champion_candidate is a research shortlist "
                  "entry, not an allocation.  A future live selector must additionally consider "
                  "correlation, symbol/timeframe exposure and family concentration (contract "
                  "27.6); v1.5 issues no signal and no capital."),
        "survivor_count": len(rows),
        "top10_count": sum(1 for row in rows if row["in_top10"]),
        "entries": rows,
        "top10": [row for row in rows if row["in_top10"]],
        si.GENERATED_KEY: si.now_utc(),
    }
    return doc, rows, []


def csv_rows(rows, contract=None):
    """Generate CSV row dicts.  Legacy A v2 uses per-param columns; generic families use the
    canonical JSON + sha256 columns."""
    # F1 remediation: None sentinel must NOT default to legacy A (same as csv_columns).
    fam = contract.get("family_id") if isinstance(contract, dict) else None
    is_legacy = (fam == pc.LEGACY_A_FAMILY_ID)
    out = []
    for row in rows:
        base = {
            "rank": row["rank"], "in_top10": row["in_top10"],
            "champion_candidate": row["champion_candidate"],
            "evidence_state": row["evidence_state"], "survivor_id": row["survivor_id"],
            "family_id": row["family_id"], "cohort": row["cohort"],
            "symbol": row["symbol"], "timeframe": row["timeframe"],
        }
        if is_legacy:
            base["strategy_window"] = row["strategy_params"]["window"]
            base["strategy_discount"] = row["strategy_params"]["discount"]
            base["dca_spacing_pct"] = row["dca_params"]["spacing_pct"]
            base["dca_size_multiplier"] = row["dca_params"]["size_multiplier"]
            base["dca_breakeven_tp_pct"] = row["dca_params"]["breakeven_tp_pct"]
            base["dca_invalidation_pct"] = row["dca_params"]["invalidation_pct"]
        else:
            base["strategy_params_canonical_json"] = pc.canonical(row["strategy_params"])
            base["strategy_params_sha256"] = pc.digest(row["strategy_params"])
            base["dca_params_canonical_json"] = pc.canonical(row["dca_params"])
            base["row_fields_json"] = pc.canonical(
                sorted(set(row["strategy_params"]) | set(row["dca_params"])))
        base["params_sha256"] = row["params_sha256"]
        base.update({
            "forward_slices": row["forward"].get("slices"),
            "forward_episodes": row["forward"].get("episodes"),
            "forward_net_pnl": row["forward"].get("net_pnl"),
            "forward_return_pct": row["forward"].get("return_pct"),
            "forward_sharpe": row["forward"].get("sharpe"),
            "forward_max_dd_pct": row["forward"].get("max_dd_pct"),
            "last_evidence_end": row["last_evidence_end"],
            "oos_sharpe": row["oos"]["sharpe"], "oos_net_pnl": row["oos"]["net_pnl"],
            "full_sharpe": row["full"]["sharpe"], "full_net_pnl": row["full"]["net_pnl"],
            "robustness_stress_floor_net_pnl": row["robustness_stress_floor_net_pnl"],
            "robustness_stress_floor_grid": row["robustness_stress_floor_grid"],
            "neighbourhood_same_sign_fraction": row["neighbourhood"]["same_sign_fraction"],
            "research_data_cutoff": row["research_data_cutoff"],
            "bundle_identity_sha256": row["bundle_identity_sha256"],
            "bundle_sha256": row["bundle_sha256"], "bundle_path": row["bundle_path"],
            "evidence_package_status": row["evidence_package_status"],
            "evidence_manifest_path": row["evidence_manifest_path"],
            "evidence_manifest_sha256": row["evidence_manifest_sha256"],
        })
        out.append(base)
    return out


def csv_contract_for(rows):
    """The contract whose columns the CSV uses, or None for the generic column set.

    R1 remediation (v1.8 re-audit residual): the legacy per-param columns are only valid when
    EVERY row is legacy A v2.  Selecting them from ``rows[0]`` routed the non-A rows of a mixed
    root into the legacy branch, where ``row["strategy_params"]["window"]`` raised KeyError -
    after ``leaderboard.json`` had already been written.
    """
    if rows and all(row["family_id"] == pc.LEGACY_A_FAMILY_ID for row in rows):
        contract, _problems, _is_legacy = pc.load_contract_from_round_spec(
            {"family_id": pc.LEGACY_A_FAMILY_ID, "parameter_contract": pc.LEGACY_A_CONTRACT})
        return contract
    return None  # generic canonical-JSON columns


def csv_text(rows, contract=None):
    import io
    columns = csv_columns(contract=contract)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    for row in csv_rows(rows, contract=contract):
        writer.writerow(row)
    return buf.getvalue()


def write_atomic(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def json_text(doc):
    return json.dumps(doc, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def cmd_forward(args, results_root):
    index, problems = si.build(results_root)
    if problems:
        for p in problems:
            sys.stderr.write("REFUSED: %s\n" % p)
        return 1
    assert index is not None
    matches = [e for e in index["survivors"] if e["survivor_id"] == args.survivor_id]
    if len(matches) != 1:
        sys.stderr.write("REFUSED: survivor_id %r matches %d indexed survivors; forward evidence "
                         "may only be filed against exactly one frozen survivor\n"
                         % (args.survivor_id, len(matches)))
        return 1
    entry = matches[0]
    try:
        with open(args.slice) as fh:
            slice_doc = json.load(fh)
    except (OSError, ValueError) as exc:
        sys.stderr.write("REFUSED: slice is unreadable/unparsable (%s)\n" % exc)
        return 1

    path = si.forward_path(results_root, entry["survivor_id"])
    # Contract 27.1/27.3: appending forward evidence is a write into `<results-root>/_survivors/**`
    # and must pass the same boundary check as `--out`/`--out-dir` - including the v1.5.2 reserved
    # root check, so a `_survivors` symlink cannot turn the append into a write inside a frozen
    # round/attempt directory.
    escape = si.outside_write_boundary(results_root, path)
    if escape:
        sys.stderr.write("REFUSED: %s\n" % escape)
        return 1
    existing, bad_line = read_slices(path, entry, results_root)
    if existing is None:
        sys.stderr.write("REFUSED: the existing forward evidence at %s is not a valid slice for "
                         "this survivor (line %d); fix the artifact before appending\n"
                         % (path, bad_line))
        return 1
    problems = slice_problems(slice_doc, entry, existing, results_root)
    if problems:
        for p in problems:
            sys.stderr.write("REFUSED: %s\n" % p)
        return 1

    record = dict(slice_doc)
    record["slice_schema_version"] = SLICE_SCHEMA_VERSION
    line = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())
    with open(path) as fh:
        written = fh.read().splitlines()
    if written[-1] != line.rstrip("\n"):
        sys.stderr.write("REFUSED: readback of %s does not match what was appended\n" % path)
        return 1

    payload = {"ok": True, "result": "appended", "survivor_id": entry["survivor_id"],
               "cohort": entry["cohort"], "forward_path": path,
               "slice_count": len(existing) + 1,
               "slice": {"data_start": record["data_start"], "data_end": record["data_end"],
                         "episodes": record["episodes"], "net_pnl": record["net_pnl"],
                         "sharpe": record["sharpe"]},
               "line_sha256": "sha256:" + hashlib.sha256(line.rstrip("\n").encode()).hexdigest()}
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print("appended survivor=%s cohort=%s %s..%s slices=%d"
              % (payload["survivor_id"], payload["cohort"], record["data_start"],
                 record["data_end"], payload["slice_count"]))
    return 0


def cmd_leaderboard(args, results_root):
    out_dir = os.path.abspath(args.out_dir) if args.out_dir else os.path.join(
        results_root, si.SURVIVORS_DIRNAME)
    escape = si.outside_write_boundary(results_root, out_dir)
    if escape:
        sys.stderr.write("REFUSED: %s\n" % escape)
        return 1
    json_path = os.path.join(out_dir, "leaderboard.json")
    csv_path = os.path.join(out_dir, "leaderboard.csv")

    doc, rows, problems = build(results_root)
    if problems:
        for p in problems:
            sys.stderr.write("REFUSED: %s\n" % p)
        return 1
    assert doc is not None and rows is not None

    # Determine the contract for CSV column generation.  The legacy per-param columns are only
    # valid when EVERY row is legacy A v2 (R1 remediation); any mixed or non-A root uses the
    # generic canonical-JSON columns for all of its rows.
    csv_contract = csv_contract_for(rows)

    if args.check:
        if not (os.path.exists(json_path) and os.path.exists(csv_path)):
            sys.stderr.write("REFUSED: no leaderboard at %s\n" % out_dir)
            return 1
        existing = si.load_json(json_path)
        with open(csv_path) as fh:
            existing_csv = fh.read()
        drifted = si.measured(existing) != si.measured(doc)
        if drifted or existing_csv != csv_text(rows, contract=csv_contract):
            sys.stderr.write("REFUSED: %s is not the rebuild of the index + forward evidence on "
                             "disk (leaderboard.json drift=%s, leaderboard.csv drift=%s)\n"
                             % (out_dir, drifted,
                                existing_csv != csv_text(rows, contract=csv_contract)))
            return 1
        result = "check_clean"
    else:
        # Render both payloads before the first write, so a failure cannot publish one half of
        # the pair (R1: the mixed root used to write leaderboard.json and then die in the CSV).
        doc_text = json_text(doc)
        csv_body = csv_text(rows, contract=csv_contract)
        write_atomic(json_path, doc_text)
        write_atomic(csv_path, csv_body)
        result = "written"

    payload = {"ok": True, "result": result, "leaderboard_json": json_path,
               "leaderboard_csv": csv_path, "survivor_count": doc["survivor_count"],
               "top10_count": doc["top10_count"],
               "rows": [{"rank": r["rank"], "survivor_id": r["survivor_id"], "cohort": r["cohort"],
                         "evidence_state": r["evidence_state"],
                         "champion_candidate": r["champion_candidate"],
                         "forward_sharpe": r["forward"].get("sharpe"),
                         "oos_sharpe": r["oos"]["sharpe"]} for r in rows]}
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print("leaderboard=%s result=%s survivors=%d top10=%d"
              % (json_path, result, doc["survivor_count"], doc["top10_count"]))
        for r in rows:
            print("  #%-2d %-14s %-18s %s"
                  % (r["rank"], r["cohort"], r["evidence_state"], r["survivor_id"]))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="forward evidence + survivor leaderboard "
                                            "(contract v1.5.2)")
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("forward", "leaderboard"):
        parser = sub.add_parser(name)
        parser.add_argument("--results-root", default=si.DEFAULT_RESULTS_ROOT)
        parser.add_argument("--json", action="store_true")
        if name == "forward":
            parser.add_argument("--survivor-id", required=True)
            parser.add_argument("--slice", required=True, help="path to one slice JSON document")
        else:
            parser.add_argument("--out-dir", default=None,
                                help="default: <results-root>/_survivors")
            parser.add_argument("--check", action="store_true",
                                help="rebuild and compare with the files on disk; write nothing")
    args = ap.parse_args(argv)

    results_root = os.path.abspath(args.results_root)
    if not os.path.isdir(results_root):
        sys.stderr.write("usage error: results root does not exist: %s\n" % results_root)
        return 2
    if args.command == "forward":
        return cmd_forward(args, results_root)
    return cmd_leaderboard(args, results_root)


if __name__ == "__main__":
    sys.exit(main())
