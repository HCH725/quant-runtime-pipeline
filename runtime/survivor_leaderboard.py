#!/usr/bin/env python3
"""Forward evidence ingestion + survivor leaderboard (Contract v1.5.0, sections 27.3 / 27.5).

The post-survivor lifecycle is: full backtest -> frozen survivor bundle -> forward evidence ->
survivor leaderboard -> champion candidate / challenger -> (future) paper/testnet/live.

Two file-based operations live here, and nothing else:

  `forward`      validate ONE pre-computed forward slice for one frozen survivor and append it,
                 verbatim, as one line of `<results-root>/_survivors/forward/<survivor_id>.jsonl`.
                 The slice must be post-freeze (its start is later than the round's registered
                 research data end), must not overlap or repeat an already recorded slice, and
                 must carry the survivor's own frozen params/bundle identity - so a retuned cell
                 can never be filed as evidence for its incumbent (contract 27.4).  The file is
                 append-only; every line is read back before the command reports success.

  `leaderboard`  rebuild the survivor index in memory, aggregate each survivor's forward
                 evidence, and write `<results-root>/_survivors/leaderboard.{json,csv}` with a
                 transparent deterministic ordering (no opaque weighted score) and a Top-10
                 shortlist.

Neither operation computes a backtest: the numbers in a slice come from a run of the existing
strategy/Qlib execution semantics, and the slice records which semantics and cost model produced
them.  Nothing here is a service, daemon, queue or registry, nothing here writes into a round or
attempt directory, and nothing here changes a verdict: the Top-10 is a ranking/selection aid, so
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

SCHEMA_VERSION = 1
KIND = "survivor_leaderboard"
CONTRACT_VERSION = "v1.5.0"
CONTRACT_SECTION = "27.3 / 27.5"
SLICE_SCHEMA_VERSION = 1
TOP_N = 10
EVIDENCE_STATES = ("FROZEN_ONLY", "ACCUMULATING", "FORWARD_POSITIVE", "FORWARD_DEGRADED")

# Contract 27.3: the fields a forward slice must record.  `fees`/`funding`/`slippage_ticks` are
# the realized cost numbers; `cost_model` and `execution_semantics` say which assumptions and
# which existing runner produced them.
SLICE_REQUIRED = {
    "survivor_id": "str", "bundle_identity_sha256": "str", "params_sha256": "str",
    "data_start": "date", "data_end": "date", "data_snapshot": "str",
    "execution_semantics": "str", "cost_model": "str", "episodes": "count",
    "net_pnl": "num", "return_pct": "num", "sharpe": "num", "max_dd_pct": "num",
    "fees": "num", "funding": "num", "slippage_ticks": "count", "produced_at_utc": "str",
}

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
]


def is_number(value):
    """True iff value is a real, finite JSON number (bools, strings and NaN are not)."""
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def is_date(value):
    return (isinstance(value, str) and len(value) == 10 and value[4] == "-" and value[7] == "-"
            and value.replace("-", "").isdigit())


def slice_problems(slice_doc, entry, existing):
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
    return problems


def read_slices(path, entry):
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
            problems = slice_problems(doc, entry, slices)
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
        slices, bad_line = read_slices(path, entry)
        if slices is None:
            return None, None, ["%s: recorded forward evidence at %s is not a valid post-freeze "
                                "slice for this survivor (line %d); refusing to rank on "
                                "unverified evidence" % (entry["survivor_id"], path, bad_line)]
        forward = aggregate(slices)
        row = dict(entry)
        row["forward"] = forward
        row["evidence_state"] = evidence_state(forward, entry)
        row["last_evidence_end"] = forward.get("last_data_end")
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


def csv_rows(rows):
    out = []
    for row in rows:
        out.append({
            "rank": row["rank"], "in_top10": row["in_top10"],
            "champion_candidate": row["champion_candidate"],
            "evidence_state": row["evidence_state"], "survivor_id": row["survivor_id"],
            "family_id": row["family_id"], "cohort": row["cohort"], "symbol": row["symbol"],
            "timeframe": row["timeframe"],
            "strategy_window": row["strategy_params"]["window"],
            "strategy_discount": row["strategy_params"]["discount"],
            "dca_spacing_pct": row["dca_params"]["spacing_pct"],
            "dca_size_multiplier": row["dca_params"]["size_multiplier"],
            "dca_breakeven_tp_pct": row["dca_params"]["breakeven_tp_pct"],
            "dca_invalidation_pct": row["dca_params"]["invalidation_pct"],
            "params_sha256": row["params_sha256"],
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
        })
    return out


def csv_text(rows):
    import io
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in csv_rows(rows):
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
    existing, bad_line = read_slices(path, entry)
    if existing is None:
        sys.stderr.write("REFUSED: the existing forward evidence at %s is not a valid slice for "
                         "this survivor (line %d); fix the artifact before appending\n"
                         % (path, bad_line))
        return 1
    problems = slice_problems(slice_doc, entry, existing)
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
    json_path = os.path.join(out_dir, "leaderboard.json")
    csv_path = os.path.join(out_dir, "leaderboard.csv")

    doc, rows, problems = build(results_root)
    if problems:
        for p in problems:
            sys.stderr.write("REFUSED: %s\n" % p)
        return 1
    assert doc is not None and rows is not None

    if args.check:
        if not (os.path.exists(json_path) and os.path.exists(csv_path)):
            sys.stderr.write("REFUSED: no leaderboard at %s\n" % out_dir)
            return 1
        existing = si.load_json(json_path)
        with open(csv_path) as fh:
            existing_csv = fh.read()
        drifted = si.measured(existing) != si.measured(doc)
        if drifted or existing_csv != csv_text(rows):
            sys.stderr.write("REFUSED: %s is not the rebuild of the index + forward evidence on "
                             "disk (leaderboard.json drift=%s, leaderboard.csv drift=%s)\n"
                             % (out_dir, drifted, existing_csv != csv_text(rows)))
            return 1
        result = "check_clean"
    else:
        write_atomic(json_path, json_text(doc))
        write_atomic(csv_path, csv_text(rows))
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
                                            "(contract v1.5.0)")
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
