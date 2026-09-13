#!/usr/bin/env python3
"""Survivor evidence preservation (Contract v1.6.0 section 28).

Promotion rule (contract 28.1): a **leaderboard entry** is the evidence-preservation trigger -
not a Top-10 slot, not a PASS gate.  For every survivor that is a formal leaderboard entry this
tool materialises one complete execution evidence package holding the ledgers themselves -
episode / fill / equity records for each of the registered phase grids - under

    <results-root>/_survivors/evidence/<survivor_id>/
      manifest.json
      aggregate.csv                  # the 9 registered grids' matched winner rows
      grids/<grid>/episodes.csv      # frozen winner cell only
      grids/<grid>/fills.csv
      grids/<grid>/equity.csv
      grids/<grid>/summary.json

What is preserved is what the leaderboard's promotion made worth keeping.  The 103,680 cell
evaluations of the research sweep (and every non-promoted candidate / culled cohort) keep their
existing summary form - aggregates in `artifacts/grid_*.csv` - and are deliberately NOT given a
per-cell ledger: writing a ledger for every rejected cell is exactly the over-engineering this
layer exists to avoid (contract 28.1).

The ledgers are produced by `container/scripts/21_strategy_a_survivor_replay.py`, which re-runs
the SAME engine on the same frozen inputs (a deterministic replay materialisation, honestly
disclosed in the manifest as such - the original research run kept only aggregates), and which
fails closed unless every replayed winner row equals the frozen, terminal-`DONE`-sentinel-pinned
`artifacts/grid_<grid>.csv` row column by column.  This host-side tool re-verifies that claim
from the durable artifacts themselves: it re-reads each frozen grid CSV, re-checks the row
against the replayed aggregate, re-checks the sentinel checksums, and reconciles each grid's
ledgers back to its aggregate (Sigma episode gross/fees/funding/net, the episode partition, and
a pure-stdlib recomputation of Sharpe and max drawdown from the equity ledger).

Writing boundary (contract 28.5): narrower than section 27 - this layer writes only under
`<results-root>/_survivors/evidence/**`, and only ever publishes a package by an atomic rename
from a `.staging-*` directory; an existing package with a different identity is refused, never
overwritten.

usage:
  python3 runtime/survivor_evidence.py materialize --staging <dir> [--survivor-id <id>]
                                                   [--results-root <dir>] [--json]
  python3 runtime/survivor_evidence.py check [--survivor-id <id>] [--results-root <dir>] [--json]
  python3 runtime/survivor_evidence.py coverage [--results-root <dir>] [--json]
exit: 0 = ok, 1 = refused / mismatch / missing, 2 = usage error
"""
import argparse
import csv
import io
import json
import math
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import survivor_index as si  # noqa: E402

SCHEMA_VERSION = 1
KIND = "survivor_evidence_package"
CONTRACT_VERSION = "v1.6.0"
CONTRACT_SECTION = "28"
EVIDENCE_DIRNAME = "evidence"
STAGING_PREFIX = ".staging"
PUBLISH_PREFIX = ".staging-publish-"
MANIFEST_NAME = "manifest.json"
AGGREGATE_NAME = "aggregate.csv"
REPLAY_AGGREGATE_NAME = "replay_aggregate.csv"
LEADERBOARD_NAME = "leaderboard.json"
LEDGER_NAMES = ("fills.csv", "episodes.csv", "equity.csv")
# The two rows of the identity recipe that are excluded from the package identity: the identity
# field itself (it cannot hash itself) and the generation timestamp (not measured content).
IDENTITY_EXCLUDED = ("package_identity_sha256", "generated_at_utc")
# The replayed aggregate is emitted rounded to 6 decimals, so the ledger recomputation is
# compared against it at the rounding quantum of the aggregate itself.
TOL = 1e-6
GRID_AXES = ("window", "discount", "spacing_pct", "size_multiplier", "breakeven_tp_pct",
             "invalidation_pct")
EXIT_REASONS = (("TP", "tp_hits"), ("STOP", "stop_hits"), ("MARGIN_CALL", "margin_calls"),
                ("EOD_FLATTEN", "open_at_end"))


def sub(parent, *names):
    return os.path.join(parent, *names)


def evidence_root(results_root):
    return os.path.join(si.write_boundary(results_root), EVIDENCE_DIRNAME)


def package_dir(results_root, survivor_id):
    return os.path.join(evidence_root(results_root), survivor_id)


def manifest_path(results_root, survivor_id):
    return os.path.join(package_dir(results_root, survivor_id), MANIFEST_NAME)


def outside_evidence_boundary(results_root, path):
    """None when `path` lands inside `<results-root>/_survivors/evidence/**`, else why not.

    Contract 28.5 narrows the section 27 boundary (`_survivors/**`) to the evidence subtree for
    this layer: `evidence/` itself and its own reserved-root rules are checked first, so a
    symlinked `_survivors` root or a `..` escape can never land the write on a frozen round.
    """
    root_problem = si.reserved_root_problem(results_root)
    if root_problem:
        return root_problem
    boundary = os.path.realpath(evidence_root(results_root))
    target = os.path.realpath(os.path.abspath(path))
    if target == boundary or target.startswith(boundary + os.sep):
        return None
    return ("%s is outside the survivor evidence write boundary %s: contract 28.5 permits this "
            "layer to write only under _survivors/evidence/** - never into a <family_id>, round "
            "or attempt directory and never over a frozen bundle, verdict or result"
            % (target, boundary))


def read_csv(path):
    with open(path, newline="") as fh:
        reader = csv.reader(fh)
        rows = list(reader)
    if not rows:
        raise ValueError("empty CSV: %s" % path)
    header, body = rows[0], rows[1:]
    return header, [dict(zip(header, row)) for row in body]


def csv_text(columns, rows):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(columns), lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buf.getvalue()


def num(text):
    if text is None or text == "":
        return None
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def identity(manifest):
    """Contract 28.4: the package identity is the canonical sha of the manifest minus itself."""
    return si.digest({k: v for k, v in manifest.items() if k not in IDENTITY_EXCLUDED})


def grid_row_identity(row):
    """Deterministic identity of one frozen/replayed winner row (all columns, canonical)."""
    return si.digest({k: v for k, v in row.items()})


def cell_params(entry):
    """The winner cell's six registered coordinates (both param axes of an index entry)."""
    params = dict(entry.get("strategy_params") or {})
    params.update(entry.get("dca_params") or {})
    missing = [k for k in GRID_AXES if k not in params]
    if missing:
        raise ValueError("entry carries no value for registered axis/axes %r" % missing)
    return params


def winner_row(rows, symbol, timeframe, params):
    hits = [r for r in rows
            if r.get("symbol") == symbol and r.get("timeframe") == timeframe
            and all(num(r.get(k)) == float(params[k]) for k in GRID_AXES)]
    if len(hits) != 1:
        raise ValueError("expected exactly one frozen row for %s/%s %r, found %d"
                         % (symbol, timeframe, params, len(hits)))
    return hits[0]


# ---------------------------------------------------------------------------
# ledger self-verification (contract 28.3): the package proves its own arithmetic
# ---------------------------------------------------------------------------

def ledger_problems(aggregate_row, fills, episodes, equity_rows, label):
    problems = []
    expected_episodes = int(aggregate_row["episodes"])
    if len(episodes) != expected_episodes:
        problems.append("%s: episode ledger holds %d rows, aggregate says %d episodes"
                        % (label, len(episodes), expected_episodes))
    for reason, key in EXIT_REASONS:
        got = sum(1 for e in episodes if e["exit_reason"] == reason)
        want = int(aggregate_row[key])
        if got != want:
            problems.append("%s: episode partition %s=%d, aggregate %s=%d"
                            % (label, reason, got, key, want))
    for field, key in (("gross_pnl", "gross_pnl"), ("fees", "fees"), ("funding", "funding"),
                       ("net_pnl", "net_pnl")):
        total = sum(float(e[field]) for e in episodes)
        want = float(aggregate_row[key])
        if abs(total - want) > TOL * max(1.0, abs(want)):
            problems.append("%s: sum(episode %s)=%.9f != aggregate %s=%.9f"
                            % (label, field, total, key, want))
    episode_ids = {e["episode"] for e in episodes}
    orphan = sorted({f["episode"] for f in fills} - episode_ids)
    if orphan:
        problems.append("%s: fill ledger references episodes %r that no episode row closes"
                        % (label, orphan[:3]))
    for e in episodes:
        gross = float(e["gross_pnl"])
        fees = float(e["fees"])
        funding = float(e["funding"])
        net = float(e["net_pnl"])
        if abs((gross - fees - funding) - net) > TOL * max(1.0, abs(net)):
            problems.append("%s: episode %s PnL identity broken (gross %.9f - fees %.9f - "
                            "funding %.9f != net %.9f)"
                            % (label, e["episode"], gross, fees, funding, net))
    if len(equity_rows) != int(aggregate_row["days"]):
        problems.append("%s: equity ledger holds %d day rows, aggregate says days=%d"
                        % (label, len(equity_rows), int(aggregate_row["days"])))
        return problems

    series = [float(r["equity"]) for r in equity_rows]
    peak = -float("inf")
    worst_dd = 0.0
    worst_dd_pct = 0.0
    for i, value in enumerate(series):
        peak = max(peak, value)
        dd = value - peak
        if num(equity_rows[i]["peak"]) != peak or abs(num(equity_rows[i]["drawdown_usdt"]) - dd) > TOL:
            problems.append("%s: equity ledger row %d peak/drawdown does not match a running "
                            "maximum over its own equity column" % (label, i))
            break
        worst_dd = min(worst_dd, dd)
        worst_dd_pct = min(worst_dd_pct, dd / peak if peak else 0.0)
    if len(series) > 2:
        rets = [(series[i] - series[i - 1]) / series[i - 1] for i in range(1, len(series))]
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        sd = math.sqrt(var)
        sharpe = (mean / sd) * math.sqrt(365.0) if sd > 0 else 0.0
    else:
        sharpe = 0.0
    for got, key in ((sharpe, "sharpe"), (worst_dd, "max_dd_usdt"), (worst_dd_pct, "max_dd_pct")):
        want = float(aggregate_row[key])
        if abs(got - want) > TOL * max(1.0, abs(want)):
            problems.append("%s: equity ledger recomputes %s=%.9f, aggregate says %.9f"
                            % (label, key, got, want))
    return problems


# ---------------------------------------------------------------------------
# materialize
# ---------------------------------------------------------------------------

def staging_problem(results_root, staging):
    escape = outside_evidence_boundary(results_root, staging)
    if escape:
        return escape
    name = os.path.basename(os.path.abspath(staging))
    if not name.startswith(STAGING_PREFIX):
        return ("staging directory %s does not use the reserved %s* name: a replay staging "
                "area only ever lives beside the published packages it feeds"
                % (staging, STAGING_PREFIX))
    if not os.path.isdir(staging):
        return "staging directory does not exist: %s" % staging
    return None


def durable_entries(results_root):
    """The index entries that are formal leaderboard entries (contract 28.1 trigger)."""
    index, problems = si.build(results_root)
    if problems:
        return None, None, problems
    board_path = os.path.join(si.write_boundary(results_root), LEADERBOARD_NAME)
    if not os.path.isfile(board_path):
        return None, None, ["no leaderboard at %s: evidence preservation triggers on leaderboard "
                            "entries, so a missing leaderboard is a refusal, not an empty set"
                            % board_path]
    board = si.load_json(board_path)
    published = {e["survivor_id"] for e in board.get("entries", [])}
    return index["survivors"], published, []


def frozen_grids(results_root, entry):
    """(header, {grid: frozen row + pins}) read back from the frozen attempt itself."""
    bundle_path = entry["bundle_path"]
    attempt_dir = os.path.join(os.path.dirname(bundle_path), "attempts", entry["run_id"])
    spec_path = os.path.join(attempt_dir, "run-spec.json")
    sentinel_path = os.path.join(attempt_dir, "DONE")
    for path in (spec_path, sentinel_path):
        if not os.path.isfile(path):
            raise ValueError("frozen attempt artifact missing: %s" % path)
    spec = si.load_json(spec_path)
    sentinel = si.load_json(sentinel_path)
    if sentinel.get("status") != "DONE":
        raise ValueError("frozen attempt %s is not terminally DONE" % attempt_dir)
    for key, want in (("run_id", entry["run_id"]), ("family_id", entry["family_id"]),
                      ("task_id", entry["kanban_task_id"])):
        if sentinel.get(key) != want:
            raise ValueError("frozen attempt sentinel records %s %r, not %r"
                             % (key, sentinel.get(key), want))
    checksums = sentinel.get("artifact_checksums") or {}
    header = None
    out = {}
    for grid in registered_grid_names(sentinel, spec):
        rel = "artifacts/grid_%s.csv" % grid
        path = os.path.join(attempt_dir, rel)
        if not os.path.isfile(path):
            raise ValueError("frozen grid artifact missing: %s" % path)
        actual = si.sha256_file(path)
        pinned = checksums.get(rel)
        if pinned != actual:
            raise ValueError("%s hashes to %s, not the checksum the terminal DONE sentinel "
                             "recorded (%r)" % (path, actual, pinned))
        columns, rows = read_csv(path)
        if header is None:
            header = columns
        elif columns != header:
            raise ValueError("frozen grid CSVs disagree on their column set")
        row = winner_row(rows, entry["symbol"], entry["timeframe"], cell_params(entry))
        out[grid] = {"csv": path, "csv_sha256": actual, "sentinel_sha256": pinned, "row": row}
    return header, out, spec


def registered_grid_names(sentinel, spec):
    """The registered phase grids, read back from the frozen attempt's own terminal record.

    Two independent frozen sources must agree: the terminal `DONE` sentinel's `artifact_manifest`
    and the run-spec's `expected_outputs`.  The grid list is therefore never taken from the
    replay's own claim, and a frozen set that disagrees with itself is a refusal.
    """
    def names(entries):
        return sorted(rel[len("artifacts/grid_"):-len(".csv")] for rel in entries
                      if rel.startswith("artifacts/grid_") and rel.endswith(".csv"))

    from_sentinel = names(sentinel.get("artifact_manifest") or [])
    from_spec = names(spec.get("expected_outputs") or [])
    if not from_sentinel:
        raise ValueError("the frozen attempt's terminal DONE sentinel lists no "
                         "artifacts/grid_*.csv, so the registered grids cannot be enumerated")
    if from_sentinel != from_spec:
        raise ValueError("the frozen terminal sentinel's artifact manifest %r and the run-spec "
                         "expected_outputs %r disagree on the registered grids"
                         % (from_sentinel, from_spec))
    return from_sentinel


def build_package(results_root, staging, survivor_dir, entry, published, staging_root):
    """Assemble one package's files in memory + on disk, then publish it atomically."""
    replay = si.load_json(sub(survivor_dir, "replay.json"))
    problems = []
    for key, want in (("survivor_id", entry["survivor_id"]), ("family_id", entry["family_id"]),
                      ("round_id", entry["round_id"]), ("run_id", entry["run_id"]),
                      ("kanban_task_id", entry["kanban_task_id"]),
                      ("cohort", entry["cohort"]), ("params_sha256", entry["params_sha256"]),
                      ("bundle_sha256", entry["bundle_sha256"]),
                      ("bundle_identity_sha256", entry["bundle_identity_sha256"]),
                      ("research_data_cutoff", entry["research_data_cutoff"])):
        if replay.get(key) != want:
            problems.append("replay header %s=%r disagrees with the durable index %r"
                            % (key, replay.get(key), want))
    if problems:
        return None, problems

    header, frozen, spec = frozen_grids(results_root, entry)
    agg_path = sub(survivor_dir, REPLAY_AGGREGATE_NAME)
    if not os.path.isfile(agg_path):
        return None, ["replay produced no %s at %s" % (REPLAY_AGGREGATE_NAME, agg_path)]
    agg_columns, agg_rows = read_csv(agg_path)
    if agg_columns != ["grid"] + list(header):
        return None, ["%s columns %r are not 'grid' + the frozen grid columns" % (agg_path,
                                                                                 agg_columns)]
    by_grid = {r["grid"]: r for r in agg_rows}
    if sorted(by_grid) != sorted(frozen):
        return None, ["replayed grid set %r is not the frozen registered grid set %r"
                      % (sorted(by_grid), sorted(frozen))]

    grids_doc = []
    aggregate_rows = []
    for grid in sorted(frozen):
        label = "%s %s" % (entry["survivor_id"], grid)
        row = by_grid[grid]
        for key in header:
            if not same_value_type_aware(row[key], frozen[grid]["row"][key]):
                problems.append("%s: replayed column %s (%r) != frozen %r"
                                % (label, key, row[key], frozen[grid]["row"][key]))
        ledger_paths = {}
        for name in LEDGER_NAMES:
            path = sub(survivor_dir, "grids", grid, name)
            if not os.path.isfile(path):
                problems.append("%s: ledger %s is missing" % (label, name))
                break
            ledger_paths[name] = path
        if problems:
            continue
        _, fills = read_csv(ledger_paths["fills.csv"])
        _, episodes = read_csv(ledger_paths["episodes.csv"])
        _, equity_rows = read_csv(ledger_paths["equity.csv"])
        problems.extend(ledger_problems(row, fills, episodes, equity_rows, label))
        ledger_info = {}
        for name in LEDGER_NAMES:
            # The manifest pins the LEDGER FILES OF THE PACKAGE (not the staging copies they were
            # assembled from), so a later byte change to a published ledger fails `check`.
            ledger_info[name] = {
                "path": os.path.join(package_dir(results_root, entry["survivor_id"]), "grids",
                                     grid, name),
                "sha256": si.sha256_file(ledger_paths[name]),
                "rows": len(read_csv(ledger_paths[name])[1])}
        grids_doc.append({
            "grid": grid,
            "window_kind": grid,
            "frozen_csv_path": frozen[grid]["csv"],
            "frozen_csv_sha256": frozen[grid]["csv_sha256"],
            "frozen_csv_sentinel_sha256": frozen[grid]["sentinel_sha256"],
            "frozen_row_identity_sha256": grid_row_identity(frozen[grid]["row"]),
            "replay_row_identity_sha256": grid_row_identity(
                {k: row[k] for k in header}),
            "comparison": "MATCH",
            "ledgers": ledger_info,
        })
        aggregate_rows.append(dict({"grid": grid}, **{k: row[k] for k in header}))
    if problems:
        return None, problems

    round_dir = os.path.dirname(entry["bundle_path"])
    attempt_dir = os.path.join(round_dir, "attempts", entry["run_id"])
    files = {"grids": {}, "aggregate.csv": csv_text(["grid"] + list(header), aggregate_rows)}
    for grid in sorted(frozen):
        files["grids"][grid] = {}
        for name in LEDGER_NAMES:
            with open(sub(survivor_dir, "grids", grid, name), "rb") as fh:
                files["grids"][grid][name] = fh.read()
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "contract": "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md %s" % CONTRACT_VERSION,
        "contract_section": CONTRACT_SECTION,
        "survivor_id": entry["survivor_id"],
        "family_id": entry["family_id"],
        "round_id": entry["round_id"],
        "run_id": entry["run_id"],
        "kanban_task_id": entry["kanban_task_id"],
        "cohort": entry["cohort"],
        "symbol": entry["symbol"],
        "timeframe": entry["timeframe"],
        "strategy_params": entry["strategy_params"],
        "dca_params": entry["dca_params"],
        "params_sha256": entry["params_sha256"],
        "research_data_cutoff": entry["research_data_cutoff"],
        "bundle": {"path": entry["bundle_path"], "sha256": entry["bundle_sha256"],
                   "bundle_identity_sha256": entry["bundle_identity_sha256"]},
        "bundle_sha256": entry["bundle_sha256"],
        "bundle_identity_sha256": entry["bundle_identity_sha256"],
        "source_data": {
            "attempt_dir": attempt_dir,
            "input_manifest_path": os.path.join(attempt_dir, "artifacts", "input_manifest.json"),
            "input_manifest_sha256": replay["source_data"]["input_manifest_sha256"],
            "bins_build_path": os.path.join(attempt_dir, "artifacts", "bins_build.json"),
            "bins_build_sha256": replay["source_data"]["bins_build_sha256"],
            "data": replay["source_data"]["data"],
        },
        "source_runner": replay["source_runner"],
        "replay_runner": replay["replay_runner"],
        "grids": grids_doc,
        "aggregate": {"comparison": "MATCH", "grids_compared": len(grids_doc),
                      "columns_compared": len(header),
                      "cells_compared": len(grids_doc) * len(header)},
        "materialization": replay["materialization_disclosure"],
        "generated_at_utc": si.now_utc(),
    }
    manifest["package_identity_sha256"] = identity(manifest)
    files[MANIFEST_NAME] = json.dumps(manifest, indent=2, ensure_ascii=False,
                                      allow_nan=False) + "\n"
    files["grid_summaries"] = {doc["grid"]: doc for doc in grids_doc}
    return files, []


def same_value_type_aware(replayed_text, frozen_text):
    """The replay aggregate CSV cell vs the frozen grid CSV cell (same writer, exact compare)."""
    if replayed_text == frozen_text:
        return True
    a, b = num(replayed_text), num(frozen_text)
    return a is not None and b is not None and a == b


def publish(results_root, survivor_id, files):
    """Write the package through a staging directory and a single atomic rename."""
    final = package_dir(results_root, survivor_id)
    manifest_text = files[MANIFEST_NAME]
    manifest = json.loads(manifest_text)
    if os.path.exists(final):
        existing_path = os.path.join(final, MANIFEST_NAME)
        if not os.path.isfile(existing_path):
            return None, ("%s exists but holds no %s: refusing to overwrite an unrecognised "
                          "package directory" % (final, MANIFEST_NAME))
        existing = si.load_json(existing_path)
        if existing.get("package_identity_sha256") == manifest.get("package_identity_sha256"):
            if identity(existing) != existing.get("package_identity_sha256"):
                return None, ("the existing package %s does not satisfy its own identity recipe: "
                              "refusing to treat it as already_identical" % final)
            return "already_identical", None
        return None, ("a package already exists at %s with a DIFFERENT identity already "
                      "published (%s vs %s): an evidence package is never overwritten"
                      % (final, existing.get("package_identity_sha256"),
                         manifest.get("package_identity_sha256")))
    stage = os.path.join(evidence_root(results_root), "%s%s-%d" % (PUBLISH_PREFIX, survivor_id,
                                                                   os.getpid()))
    escape = outside_evidence_boundary(results_root, stage)
    if escape:
        return None, escape
    if os.path.isdir(stage):
        shutil.rmtree(stage)
    os.makedirs(sub(stage, "grids"), exist_ok=True)
    for grid, ledgers in files["grids"].items():
        os.makedirs(sub(stage, "grids", grid), exist_ok=True)
        for name, blob in ledgers.items():
            with open(sub(stage, "grids", grid, name), "wb") as fh:
                fh.write(blob)
    with open(sub(stage, AGGREGATE_NAME), "w") as fh:
        fh.write(files["aggregate.csv"])
    with open(sub(stage, MANIFEST_NAME), "w") as fh:
        fh.write(manifest_text)
    for grid_doc in [files["grid_summaries"][g] for g in sorted(files["grid_summaries"])]:
        summary = {"grid": grid_doc["grid"], "survivor_id": survivor_id,
                   "comparison": grid_doc["comparison"],
                   "frozen_csv_path": grid_doc["frozen_csv_path"],
                   "frozen_csv_sha256": grid_doc["frozen_csv_sha256"],
                   "frozen_row_identity_sha256": grid_doc["frozen_row_identity_sha256"],
                   "ledgers": grid_doc["ledgers"]}
        path = sub(stage, "grids", grid_doc["grid"], "summary.json")
        with open(path, "w") as fh:
            fh.write(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    os.rename(stage, final)
    return "published", None


def cmd_materialize(args, results_root):
    staging = os.path.abspath(args.staging)
    problem = staging_problem(results_root, staging)
    if problem:
        sys.stderr.write("REFUSED: %s\n" % problem)
        return 1
    entries, published, problems = durable_entries(results_root)
    if problems:
        for p in problems:
            sys.stderr.write("REFUSED: %s\n" % p)
        return 1
    by_id = {e["survivor_id"]: e for e in entries}

    wanted = sorted(d for d in os.listdir(staging)
                    if d.startswith("sv-") and os.path.isdir(sub(staging, d)))
    if args.survivor_id:
        wanted = [d for d in wanted if d == args.survivor_id]
    if not wanted:
        sys.stderr.write("REFUSED: staging directory %s holds no replayed survivor\n" % staging)
        return 1

    results = []
    for survivor_id in wanted:
        entry = by_id.get(survivor_id)
        if entry is None:
            sys.stderr.write("REFUSED: %s is not a survivor of the durable index: evidence is "
                             "preserved for promoted leaders only (contract 28.1)\n" % survivor_id)
            return 1
        if survivor_id not in published:
            sys.stderr.write("REFUSED: %s is not a leaderboard entry: evidence preservation is "
                             "triggered by formal leaderboard membership, not by a Top-10 slot "
                             "or a PASS gate (contract 28.1)\n" % survivor_id)
            return 1
        files, problems = build_package(results_root, staging, sub(staging, survivor_id), entry,
                                       published, staging)
        if problems:
            for p in problems:
                sys.stderr.write("REFUSED: %s\n" % p)
            return 1
        result, problem = publish(results_root, survivor_id, files)
        if problem:
            sys.stderr.write("REFUSED: %s\n" % problem)
            return 1
        manifest = json.loads(files[MANIFEST_NAME])
        results.append({"survivor_id": survivor_id, "cohort": entry["cohort"],
                        "result": result, "grids": len(manifest["grids"]),
                        "package": package_dir(results_root, survivor_id),
                        "package_identity_sha256": manifest["package_identity_sha256"],
                        "manifest_sha256": si.sha256_file(manifest_path(results_root,
                                                                        survivor_id))})
    payload = {"ok": True, "result": "materialized", "survivors": results}
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        for r in results:
            print("materialize %s %-12s grids=%d %s" % (r["survivor_id"], r["cohort"],
                                                        r["grids"], r["result"]))
    return 0


# ---------------------------------------------------------------------------
# check / coverage
# ---------------------------------------------------------------------------

def check_package(results_root, survivor_id, entry):
    """Re-verify one published package from disk; returns (manifest, problems)."""
    problems = []
    directory = package_dir(results_root, survivor_id)
    path = os.path.join(directory, MANIFEST_NAME)
    if not os.path.isfile(path):
        return None, ["no %s at %s" % (MANIFEST_NAME, path)]
    manifest = si.load_json(path)
    declared = manifest.get("package_identity_sha256")
    if not declared:
        return manifest, ["%s publishes no package_identity_sha256" % path]
    if identity(manifest) != declared:
        problems.append("%s: package_identity_sha256 %r is not the contract 28.4 recipe applied "
                        "to the manifest (recomputes %r)" % (path, declared, identity(manifest)))
    if entry is not None:
        for key in ("survivor_id", "family_id", "round_id", "run_id", "kanban_task_id",
                    "cohort", "params_sha256", "bundle_identity_sha256"):
            want = entry.get(key)
            if manifest.get(key) != want:
                problems.append("%s: manifest %s=%r disagrees with the durable index %r"
                                % (path, key, manifest.get(key), want))
    if not os.path.isfile(os.path.join(directory, AGGREGATE_NAME)):
        problems.append("%s: package file %s is missing" % (directory, AGGREGATE_NAME))
    grid_docs = manifest.get("grids")
    if not isinstance(grid_docs, list) or not grid_docs:
        return manifest, problems + ["%s: manifest carries no grid list" % path]
    for grid_doc in grid_docs:
        label = "%s %s" % (survivor_id, grid_doc.get("grid"))
        for name, info in (grid_doc.get("ledgers") or {}).items():
            target = info.get("path")
            if not os.path.isfile(target):
                problems.append("%s: ledger %s is missing at %s" % (label, name, target))
                continue
            actual = si.sha256_file(target)
            if actual != info.get("sha256"):
                problems.append("%s: ledger %s hashes to %s, not the recorded %s (tamper)"
                                % (label, name, actual, info.get("sha256")))
            elif info.get("rows") is not None and len(read_csv(target)[1]) != info["rows"]:
                problems.append("%s: ledger %s row count changed" % (label, name))
        frozen_csv = grid_doc.get("frozen_csv_path")
        if not os.path.isfile(frozen_csv):
            problems.append("%s: the frozen grid CSV %s is gone: the package can no longer be "
                            "re-verified against its source" % (label, frozen_csv))
            continue
        actual = si.sha256_file(frozen_csv)
        if actual != grid_doc.get("frozen_csv_sha256"):
            problems.append("%s: the frozen grid CSV %s changed (%s != recorded %s): refusing to "
                            "call this package verified" % (label, frozen_csv, actual,
                                                            grid_doc.get("frozen_csv_sha256")))
            continue
        columns, rows = read_csv(frozen_csv)
        row = winner_row(rows, manifest.get("symbol"), manifest.get("timeframe"),
                         cell_params(manifest))
        if grid_row_identity(row) != grid_doc.get("frozen_row_identity_sha256"):
            problems.append("%s: the frozen winner row no longer matches the identity recorded in "
                            "the package" % label)
            continue
        _, aggregate_rows = read_csv(os.path.join(directory, AGGREGATE_NAME))
        match = [r for r in aggregate_rows if r.get("grid") == grid_doc.get("grid")]
        if len(match) != 1:
            problems.append("%s: %s holds %d rows for this grid" % (label, AGGREGATE_NAME,
                                                                    len(match)))
            continue
        for key in columns:
            if not same_value_type_aware(match[0][key], row[key]):
                problems.append("%s: package aggregate column %s (%r) != the frozen row %r"
                                % (label, key, match[0][key], row[key]))
        _, fills = read_csv(grid_doc["ledgers"]["fills.csv"]["path"])
        _, episodes = read_csv(grid_doc["ledgers"]["episodes.csv"]["path"])
        _, equity_rows = read_csv(grid_doc["ledgers"]["equity.csv"]["path"])
        problems.extend(ledger_problems(match[0], fills, episodes, equity_rows, label))
    return manifest, problems


def package_ids(results_root):
    root = evidence_root(results_root)
    if not os.path.isdir(root):
        return []
    return sorted(d for d in os.listdir(root)
                  if not d.startswith(".") and os.path.isdir(os.path.join(root, d)))


def cmd_check(args, results_root):
    entries, published, problems = durable_entries(results_root)
    if problems:
        for p in problems:
            sys.stderr.write("REFUSED: %s\n" % p)
        return 1
    by_id = {e["survivor_id"]: e for e in entries}
    ids = package_ids(results_root)
    if args.survivor_id:
        ids = [i for i in ids if i == args.survivor_id]
        if not ids:
            sys.stderr.write("REFUSED: no evidence package for %s\n" % args.survivor_id)
            return 1
    if not ids:
        sys.stderr.write("REFUSED: no evidence package under %s\n" % evidence_root(results_root))
        return 1
    problems = []
    checked = []
    for survivor_id in ids:
        manifest, found = check_package(results_root, survivor_id, by_id.get(survivor_id))
        problems.extend(found)
        checked.append({"survivor_id": survivor_id, "grids": len((manifest or {}).get("grids") or []),
                        "package_identity_sha256": (manifest or {}).get("package_identity_sha256")})
    if problems:
        for p in problems:
            sys.stderr.write("REFUSED: %s\n" % p)
        return 1
    payload = {"ok": True, "result": "check_clean", "packages": checked}
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        for c in checked:
            print("check %s grids=%d %s" % (c["survivor_id"], c["grids"],
                                            c["package_identity_sha256"]))
    return 0


def pointer(results_root, entry):
    """Non-ranking evidence pointer for one leaderboard row (contract 28.6).

    PRESENT/ABSENT describes preservation coverage only: it never feeds the ordering tuple, the
    Top-10, `evidence_state`, `champion_candidate` or any verdict, and a missing package leaves
    the leaderboard fully valid (rc=0).
    """
    survivor_id = entry["survivor_id"]
    path = manifest_path(results_root, survivor_id)
    if not os.path.isfile(path):
        return {"evidence_package_status": "ABSENT", "evidence_manifest_path": None,
                "evidence_manifest_sha256": None}
    try:
        manifest = si.load_json(path)
    except (OSError, ValueError):
        return {"evidence_package_status": "ABSENT", "evidence_manifest_path": None,
                "evidence_manifest_sha256": None}
    if not isinstance(manifest, dict) or any(
            manifest.get(key) != entry.get(key)
            for key in ("survivor_id", "params_sha256", "bundle_identity_sha256")):
        return {"evidence_package_status": "ABSENT", "evidence_manifest_path": None,
                "evidence_manifest_sha256": None}
    return {"evidence_package_status": "PRESENT", "evidence_manifest_path": path,
            "evidence_manifest_sha256": si.sha256_file(path)}


def cmd_coverage(args, results_root):
    entries, published, problems = durable_entries(results_root)
    if problems:
        for p in problems:
            sys.stderr.write("REFUSED: %s\n" % p)
        return 1
    rows = []
    for entry in entries:
        point = pointer(results_root, entry)
        rows.append({"survivor_id": entry["survivor_id"], "cohort": entry["cohort"],
                     **point})
    missing = [r for r in rows if r["evidence_package_status"] != "PRESENT"]
    payload = {"ok": not missing, "result": "coverage", "leaderboard_entries": len(rows),
               "present": len(rows) - len(missing), "absent": len(missing),
               "entries": sorted(rows, key=lambda r: r["survivor_id"])}
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        for r in sorted(rows, key=lambda r: r["survivor_id"]):
            print("coverage %s %-12s %s" % (r["survivor_id"], r["cohort"],
                                            r["evidence_package_status"]))
        print("coverage %d/%d PRESENT" % (payload["present"], payload["leaderboard_entries"]))
    if missing:
        sys.stderr.write("REFUSED: %d of %d formal leaderboard entries have no evidence package: "
                         "%s\n" % (len(missing), len(rows),
                                   ", ".join(sorted(r["survivor_id"] for r in missing))))
        return 1
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="survivor evidence preservation (contract %s)"
                                             % CONTRACT_VERSION)
    subparsers = ap.add_subparsers(dest="command", required=True)
    for name in ("materialize", "check", "coverage"):
        parser = subparsers.add_parser(name)
        parser.add_argument("--results-root", default=si.DEFAULT_RESULTS_ROOT)
        parser.add_argument("--survivor-id", default=None)
        parser.add_argument("--json", action="store_true")
        if name == "materialize":
            parser.add_argument("--staging", required=True,
                                help="replay staging directory under _survivors/evidence/.staging*")
    args = ap.parse_args(argv)

    results_root = os.path.abspath(args.results_root)
    if not os.path.isdir(results_root):
        sys.stderr.write("usage error: results root does not exist: %s\n" % results_root)
        return 2
    if args.command == "materialize":
        return cmd_materialize(args, results_root)
    if args.command == "check":
        return cmd_check(args, results_root)
    return cmd_coverage(args, results_root)


if __name__ == "__main__":
    sys.exit(main())
