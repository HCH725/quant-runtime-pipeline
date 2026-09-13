#!/usr/bin/env python3
"""Strategy A — survivor evidence replay driver (Contract v1.6.0 section 28.3/28.4).

Re-materialises the execution ledgers (fills / episodes / equity marks) of ALREADY FROZEN
survivor winner cells by re-running the **same engine** the production run used - this module
imports `20_strategy_a_run.py` as a module and calls its existing `Cohort`, data loaders,
`rail_for`, `simulate` and `record`; it never re-implements `simulate()` and never calls
`summarize()`.

Scope: only the cells that are already leaderboard entries.  For one survivor the driver
replays exactly

    len(engine.COHORT_GRID_KINDS)  (= 9 registered grids) winner-cell evaluations

never the 103,680-evaluation research sweep, and it fails closed on the first column of the
first grid whose replayed winner row differs from the frozen, DONE-sentinel-pinned
`artifacts/grid_<grid>.csv` row.

Honesty (contract 28.4): the ledgers this driver writes are a **deterministic replay
materialisation**, produced now, from the frozen inputs - they are NOT the bytes the original
research run kept (the original run kept only aggregates).  That disclosure is carried into
the package manifest; nothing here claims the original run stored a ledger.

Writes only into `--staging <dir>` (the host-side `runtime/survivor_evidence.py` owns the
final `_survivors/evidence/<survivor_id>/` package and its write boundary); this driver
refuses any `--staging` that is not inside a `_survivors/evidence/` subtree, and it never
writes into a `<family_id>`, round or attempt directory and never writes a verdict or bundle.

usage:
  /opt/venv/bin/python 21_strategy_a_survivor_replay.py --survivor-id <sv-...> \
      --staging <staging-dir> [--results-root /results] [--engine <20_strategy_a_run.py>] \
      [--no-verify-trace-inert] [--json]
exit: 0 = ok, 1 = refused/mismatch, 2 = usage error
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ENGINE = os.path.join(HERE, "20_strategy_a_run.py")
DEFAULT_RESULTS_ROOT = "/results"
SENTINEL_NAME = "DONE"
SURVIVORS_DIRNAME = "_survivors"
EVIDENCE_DIRNAME = "evidence"
CONTRACT_VERSION = "v1.6.0"

# The two "no funding" grids are literal in the engine's `run_cohort` (they are not in
# `engine.STRESS`); mapping them here keeps the grid -> (window, stress) plan in one readable
# place.  The engine's own `COHORT_GRID_KINDS` stays the authoritative registered name list.
NO_FUNDING_GRIDS = {"no_funding": ("historical", {"no_funding": True}),
                    "no_funding_full": ("full", {"no_funding": True})}

FILL_COLUMNS = ("episode", "event_type", "bar_index", "open_time_ms", "price", "qty",
                "dca_level", "trigger_price", "ref_price", "fee", "slip_ticks")
EPISODE_COLUMNS = ("episode", "entry_bar_index", "entry_time_ms", "entry_price",
                   "exit_bar_index", "exit_time_ms", "exit_price", "exit_reason",
                   "gross_pnl", "fees", "funding", "net_pnl", "holding_bars", "layers_used",
                   "mae_usdt", "mfe_usdt")
EQUITY_COLUMNS = ("day_index", "date", "equity", "peak", "drawdown_usdt", "drawdown_pct",
                  "in_window")
TOL = 1e-6


def log(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), msg)
    print(line, flush=True)
    return line


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def load_json(path):
    with open(path) as fh:
        return json.load(fh)


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    return "sha256:" + hashlib.sha256(canonical(value).encode()).hexdigest()


def load_engine(path):
    """Load the SAME engine module the production run uses (never a copy of its logic)."""
    spec = importlib.util.spec_from_file_location("strategy_a_engine_replay", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def utc_date(ms):
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000.0))


class Collector:
    """The trace sink handed to the engine; it only records, it never feeds anything back."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.fills = []
        self.episodes = []
        self.equity = None

    def __call__(self, event, fields):
        if event == "fill":
            self.fills.append(dict(fields))
        elif event == "episode":
            self.episodes.append(dict(fields))
        elif event == "equity_marks":
            self.equity = dict(fields)


def grid_plan(engine, spec, grid):
    """(window dates, stress, slip_ticks) for one registered grid - the engine's own plan."""
    slip = spec["costs"]["baseline_slippage_ticks"]
    data = spec["data"]
    windows = {"historical": (data["historical_start"], data["historical_end"]),
               "oos": (data["oos_start"], data["oos_end"]),
               "full": (data["start"], data["end"])}
    for name, stress in engine.STRESS:
        if name == grid:
            return windows["full"], dict(stress), stress.get("slip_ticks", slip)
    if grid in windows:
        return windows[grid], {}, slip
    if grid in NO_FUNDING_GRIDS:
        kind, stress = NO_FUNDING_GRIDS[grid]
        return windows[kind], dict(stress), slip
    raise SystemExit("REFUSED: %r is not a registered phase grid of this engine (%r)"
                     % (grid, list(engine.COHORT_GRID_KINDS)))


def read_csv_rows(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def build_bins(engine, spec, needed, log_fn):
    """Rebuild the Qlib .bin store for the datasets the replayed cohorts need.

    `/qlib/work` is a rebuildable derived area (INV-5).  Only the replayed (symbol, freq)
    datasets are built, and - unlike the production runner - the report is NOT written into
    the frozen attempt directory (a replay must never write into a frozen round).
    """
    if os.path.isdir(engine.WORK_ROOT):
        shutil.rmtree(engine.WORK_ROOT)
    freqs = sorted({freq for _s, _raw, freq in needed})
    for symbol, raw_interval, qlib_freq in needed:
        info = engine.write_csv(symbol, raw_interval, qlib_freq, spec["data"]["start"],
                                spec["data"]["end"])
        log_fn("csv %s/%s rows=%d bytes=%d" % (symbol, raw_interval, info["rows"],
                                               info["csv_bytes"]))
    for freq in freqs:
        cmd = [engine.VENV_PYTHON, engine.DUMP_BIN, "dump_all",
               "--data_path", os.path.join(engine.CSV_ROOT, freq),
               "--qlib_dir", engine.QLIB_DIR, "--freq", freq,
               "--include_fields", "open,close,high,low,volume",
               "--date_field_name", "date", "--max_workers", "1"]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=14400)
        if p.returncode != 0:
            raise SystemExit("REFUSED: dump_bin failed for freq %s rc=%d\n%s\n%s"
                             % (freq, p.returncode, p.stdout[-2000:], p.stderr[-2000:]))
        log_fn("dump_bin %s ok" % freq)
    import qlib
    qlib.init(provider_uri=engine.QLIB_DIR, region="cn", expression_cache=None,
              dataset_cache=None)


def day_dates(cohort):
    """UTC date of the first bar of every cohort day (same order as `day_index`)."""
    first = np.searchsorted(cohort.day_index, np.arange(cohort.n_days), side="left")
    return [utc_date(int(cohort.open_time_ms[int(i)])) for i in first]


def reconcile(aggregate_row, fills, episodes, equity_written):
    """Deterministic ledger self-check (contract 28.3) against the engine's aggregate row."""
    problems = []
    n = len(episodes)
    if n != aggregate_row["episodes"]:
        problems.append("episode ledger has %d episodes, aggregate says %d"
                        % (n, aggregate_row["episodes"]))
    for reason, key in (("TP", "tp_hits"), ("STOP", "stop_hits"),
                        ("MARGIN_CALL", "margin_calls"), ("EOD_FLATTEN", "open_at_end")):
        got = sum(1 for e in episodes if e["exit_reason"] == reason)
        if got != aggregate_row[key]:
            problems.append("episode partition %s=%d, aggregate %s=%d"
                            % (reason, got, key, aggregate_row[key]))
    for field, key in (("gross_pnl", "gross_pnl"), ("fees", "fees"), ("funding", "funding"),
                       ("net_pnl", "net_pnl")):
        total = sum(e[field] for e in episodes)
        if abs(total - aggregate_row[key]) > TOL * max(1.0, abs(aggregate_row[key])):
            problems.append("sum(episode %s)=%.9f != aggregate %s=%.9f"
                            % (field, total, key, aggregate_row[key]))
    for fill in fills:
        if not (fill["episode"] < n):
            problems.append("fill references episode %r but only %d episodes recorded"
                            % (fill["episode"], n))
            break
    if not equity_written:
        problems.append("no equity ledger recorded")
    return problems


def replay_cell(engine, cohort, window, p, dca, grid, stress, slip, collector, verify_inert):
    """One winner-cell evaluation, traced; returns (traced_row, plain_row_or_None)."""
    rail = engine.rail_for(dca)
    engine.TRACE = None
    plain = engine.simulate(cohort, window, rail, p, stress, slip, grid)
    collector.reset()
    engine.TRACE = collector
    traced = engine.simulate(cohort, window, rail, p, stress, slip, grid)
    engine.TRACE = None
    row_traced = engine.record(cohort, p, dca, grid, traced)
    if not verify_inert:
        return row_traced, None
    return row_traced, engine.record(cohort, p, dca, grid, plain)


def write_csv(path, columns, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(columns))
        w.writeheader()
        for row in rows:
            w.writerow(row)


def evidence_boundary_problem(staging):
    """Contract 28.5: the replay writes only inside a `_survivors/evidence/` subtree."""
    resolved = os.path.realpath(os.path.abspath(staging))
    marker = os.sep + SURVIVORS_DIRNAME + os.sep + EVIDENCE_DIRNAME + os.sep
    if marker not in resolved + os.sep:
        return ("staging directory %s is not inside a %s/%s/ subtree: the replay only writes "
                "under the reserved evidence namespace and never into a <family_id>, round or "
                "attempt directory" % (resolved, SURVIVORS_DIRNAME, EVIDENCE_DIRNAME))
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="survivor evidence replay driver (contract %s)"
                                             % CONTRACT_VERSION)
    ap.add_argument("--survivor-id", required=True)
    ap.add_argument("--staging", required=True)
    ap.add_argument("--results-root", default=DEFAULT_RESULTS_ROOT)
    ap.add_argument("--engine", default=DEFAULT_ENGINE)
    ap.add_argument("--deployed-runner", default="/scripts/20_strategy_a_run.py")
    ap.add_argument("--no-verify-trace-inert", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    staging = os.path.realpath(os.path.abspath(args.staging))
    problem = evidence_boundary_problem(staging)
    if problem:
        sys.stderr.write("REFUSED: %s\n" % problem)
        return 1
    results_root = os.path.realpath(os.path.abspath(args.results_root))
    engine_path = os.path.realpath(os.path.abspath(args.engine))
    if not os.path.isfile(engine_path):
        sys.stderr.write("usage error: engine module not found: %s\n" % engine_path)
        return 2

    engine = load_engine(engine_path)
    engine_sha = sha256_file(engine_path)
    deployed_sha = None
    if os.path.isfile(args.deployed_runner):
        deployed_sha = sha256_file(args.deployed_runner)

    # The survivor to replay is read back from the durable leaderboard/index artifacts - never
    # hard-coded, and never re-derived from the driver's own input.
    index_path = os.path.join(results_root, SURVIVORS_DIRNAME, "survivor-index.json")
    board_path = os.path.join(results_root, SURVIVORS_DIRNAME, "leaderboard.json")
    if not os.path.isfile(index_path):
        sys.stderr.write("REFUSED: no survivor index at %s\n" % index_path)
        return 1
    if not os.path.isfile(board_path):
        sys.stderr.write("REFUSED: no leaderboard at %s (evidence preservation triggers on "
                         "leaderboard entries only)\n" % board_path)
        return 1
    index = load_json(index_path)
    board = load_json(board_path)
    matches = [e for e in index["survivors"] if e["survivor_id"] == args.survivor_id]
    board_ids = [e["survivor_id"] for e in board["entries"]]
    if len(matches) != 1:
        sys.stderr.write("REFUSED: survivor_id %r matches %d indexed survivors\n"
                         % (args.survivor_id, len(matches)))
        return 1
    if args.survivor_id not in board_ids:
        sys.stderr.write("REFUSED: survivor_id %r is not a leaderboard entry: evidence is "
                         "preserved for promoted leaders only, never per candidate/culled "
                         "cell (contract 28.1)\n" % args.survivor_id)
        return 1
    entry = matches[0]

    bundle_path = to_local(entry["bundle_path"], index, results_root)
    bundle = load_json(bundle_path)
    if sha256_file(bundle_path) != entry["bundle_sha256"]:
        sys.stderr.write("REFUSED: frozen survivor bundle %s changed since the index was "
                         "built\n" % bundle_path)
        return 1
    records = [s for s in bundle["survivors"] if s["cohort"] == entry["cohort"]]
    if len(records) != 1:
        sys.stderr.write("REFUSED: bundle holds %d records for cohort %s\n"
                         % (len(records), entry["cohort"]))
        return 1
    winner = records[0]["winner"]
    if any(winner[k] != entry["strategy_params"].get(k) for k in ("window", "discount")):
        sys.stderr.write("REFUSED: bundle winner strategy params disagree with the index\n")
        return 1
    if any(winner[k] != entry["dca_params"].get(k)
           for k in ("spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct")):
        sys.stderr.write("REFUSED: bundle winner DCA params disagree with the index\n")
        return 1

    round_dir = os.path.dirname(bundle_path)
    attempt_dir = os.path.join(round_dir, "attempts", entry["run_id"])
    if not os.path.isdir(attempt_dir):
        sys.stderr.write("REFUSED: frozen attempt directory %s is missing\n" % attempt_dir)
        return 1
    spec = load_json(os.path.join(attempt_dir, "run-spec.json"))
    sentinel_path = os.path.join(attempt_dir, SENTINEL_NAME)
    if not os.path.isfile(sentinel_path):
        sys.stderr.write("REFUSED: frozen attempt %s has no terminal %s sentinel\n"
                         % (attempt_dir, SENTINEL_NAME))
        return 1
    sentinel = load_json(sentinel_path)
    if sentinel.get("status") != "DONE":
        sys.stderr.write("REFUSED: frozen attempt is not terminally DONE (%r)\n"
                         % sentinel.get("status"))
        return 1
    for key, want in (("run_id", entry["run_id"]), ("family_id", entry["family_id"]),
                      ("task_id", entry["kanban_task_id"])):
        if sentinel.get(key) != want:
            sys.stderr.write("REFUSED: sentinel records %s %r, not %r\n"
                             % (key, sentinel.get(key), want))
            return 1
    checksums = sentinel.get("artifact_checksums") or {}

    # per-registered-grid: pinned frozen row + the winner cell's coordinates
    header = None
    frozen = {}
    symbol, timeframe = entry["symbol"], entry["timeframe"]
    for grid in engine.COHORT_GRID_KINDS:
        rel = "artifacts/grid_%s.csv" % grid
        path = os.path.join(attempt_dir, rel)
        if not os.path.isfile(path):
            sys.stderr.write("REFUSED: frozen grid artifact %s is missing\n" % path)
            return 1
        actual = sha256_file(path)
        if checksums.get(rel) != actual:
            sys.stderr.write("REFUSED: %s hashes to %s, not the checksum the terminal sentinel "
                             "recorded (%r)\n" % (path, actual, checksums.get(rel)))
            return 1
        rows = read_csv_rows(path)
        if header is None:
            header = list(rows[0].keys())
        elif list(rows[0].keys()) != header:
            sys.stderr.write("REFUSED: frozen grid CSV column sets disagree\n")
            return 1
        axes = ("window", "discount", "spacing_pct", "size_multiplier", "breakeven_tp_pct",
                "invalidation_pct")
        hits = [r for r in rows if r["symbol"] == symbol and r["timeframe"] == timeframe
                and all(numbers_equal(r[k], winner[k]) for k in axes)]
        if len(hits) != 1:
            sys.stderr.write("REFUSED: %s holds %d rows for winner cell %r\n"
                             % (path, len(hits), winner))
            return 1
        frozen[grid] = {"csv": path, "csv_sha256": actual, "sentinel_sha256": checksums.get(rel),
                        "row": hits[0]}

    # The winner cell's registered coordinate values, typed for the engine.
    p = {"window": winner["window"], "discount": winner["discount"]}
    dca = {"base_quote": spec["dca_domain"]["base_quote"],
           "spacing_pct": winner["spacing_pct"], "size_multiplier": winner["size_multiplier"],
           "breakeven_tp_pct": winner["breakeven_tp_pct"],
           "invalidation_pct": winner["invalidation_pct"]}
    symbol, timeframe = entry["symbol"], entry["timeframe"]
    tf = [t for t in spec["data"]["timeframes"] if t["raw_interval"] == timeframe]
    if len(tf) != 1:
        sys.stderr.write("REFUSED: timeframe %r is not registered in the frozen run-spec\n"
                         % timeframe)
        return 1
    needed = [(symbol, timeframe, tf[0]["qlib_freq"])]

    log("replay survivor=%s cohort=%s params=%s" % (entry["survivor_id"], entry["cohort"],
                                                    canonical(p)))
    log("engine=%s (%s) deployed=%s" % (engine_path, engine_sha, deployed_sha))
    build_bins(engine, spec, needed, log)

    cohort = engine.Cohort(symbol, tf[0], spec["data"]["start"], spec["data"]["end"])
    instruments = load_json(os.path.join(engine.RAW_ROOT, "instruments",
                                         "usdm-perp-instruments.json"))
    meta = None
    for item in instruments["instruments"]:
        if item["fields"]["raw_symbol"] == symbol:
            meta = item["fields"]
    if meta is None:
        sys.stderr.write("REFUSED: no instrument metadata for %s\n" % symbol)
        return 1
    cohort.price_increment = float(meta["price_increment"])
    cohort.taker_fee = float(meta["taker_fee"])
    cohort.leverage = 1.0 / float(meta["margin_init"])
    cohort.margin_maint = float(meta["margin_maint"])
    ft, fr, _excluded = engine.load_funding(symbol, spec["data"]["start"], spec["data"]["end"])
    cohort.funding, applied = engine.funding_per_bar(cohort, ft, fr, 1.0)
    log("cohort %s/%s bars=%d days=%d funding_official=%d"
        % (symbol, timeframe, cohort.n, cohort.n_days, applied))

    dates = day_dates(cohort)
    input_manifest_path = os.path.join(attempt_dir, "artifacts", "input_manifest.json")
    bins_build_path = os.path.join(attempt_dir, "artifacts", "bins_build.json")
    source_data = {"attempt_dir": attempt_dir,
                   "input_manifest_path": input_manifest_path,
                   "input_manifest_sha256": sha256_file(input_manifest_path),
                   "bins_build_path": bins_build_path,
                   "bins_build_sha256": sha256_file(bins_build_path),
                   "data": spec["data"]}
    for rel, got in (("artifacts/input_manifest.json",
                      source_data["input_manifest_sha256"]),
                     ("artifacts/bins_build.json", source_data["bins_build_sha256"])):
        if checksums.get(rel) != got:
            sys.stderr.write("REFUSED: frozen %s hashes to %s, not the checksum the terminal "
                             "sentinel recorded (%r)\n" % (rel, got, checksums.get(rel)))
            return 1
    out_dir = os.path.join(staging, entry["survivor_id"])
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(os.path.join(out_dir, "grids"), exist_ok=True)

    collector = Collector()
    aggregate_rows = []
    grid_docs = []
    verify_inert = not args.no_verify_trace_inert
    for grid in engine.COHORT_GRID_KINDS:
        window_dates, stress, slip = grid_plan(engine, spec, grid)
        window = cohort.slice(*window_dates)
        row, plain = replay_cell(engine, cohort, window, p, dca, grid, stress, slip, collector,
                                 verify_inert)
        problems = []
        if plain is not None and plain != row:
            problems.append("trace off/on aggregate differ for grid %s" % grid)
        keys = set(row)
        if keys != set(header):
            problems.append("engine record keys %r != frozen grid CSV columns %r"
                            % (sorted(keys), sorted(header)))
        else:
            frozen_row = frozen[grid]["row"]
            for key in header:
                if not values_equal(row[key], frozen_row[key]):
                    problems.append("column %s: replayed %r != frozen %r"
                                    % (key, row[key], frozen_row[key]))
        equity_rows = []
        equity = collector.equity
        if equity is None:
            problems.append("no equity marks recorded for grid %s" % grid)
        else:
            series = [float(v) for v in equity["equity"]]
            peak = -float("inf")
            for i, value in enumerate(series):
                peak = max(peak, value)
                equity_rows.append({"day_index": equity["day_index"][i], "date": dates[i],
                                    "equity": value, "peak": peak,
                                    "drawdown_usdt": value - peak,
                                    "drawdown_pct": (value - peak) / peak if peak else 0.0,
                                    "in_window": bool(equity["in_window"][i])})
            problems.extend(reconcile(row, collector.fills, collector.episodes, True))
        if problems:
            for pr in problems:
                sys.stderr.write("REFUSED: grid %s %s: %s\n" % (grid, entry["survivor_id"], pr))
            return 1
        grid_dir = os.path.join(out_dir, "grids", grid)
        write_csv(os.path.join(grid_dir, "fills.csv"), FILL_COLUMNS, collector.fills)
        write_csv(os.path.join(grid_dir, "episodes.csv"), EPISODE_COLUMNS, collector.episodes)
        write_csv(os.path.join(grid_dir, "equity.csv"), EQUITY_COLUMNS, equity_rows)
        summary = {"grid": grid, "survivor_id": entry["survivor_id"], "cohort": entry["cohort"],
                   "window_kind": grid, "window_dates": list(window_dates), "stress": stress,
                   "window_bar_slice": list(window),
                   "slip_ticks": slip, "aggregate": engine._sanitize(row),
                   "frozen_csv": frozen[grid]["csv"],
                   "frozen_csv_sha256": frozen[grid]["csv_sha256"],
                   "frozen_row_identity_sha256": digest(frozen[grid]["row"]),
                   "fills": len(collector.fills), "episodes": len(collector.episodes),
                   "equity_rows": len(equity_rows)}
        with open(os.path.join(grid_dir, "summary.json"), "w") as fh:
            fh.write(json.dumps(engine._sanitize(summary), indent=2, ensure_ascii=False) + "\n")
        aggregate_rows.append(dict({"grid": grid}, **{k: row[k] for k in header}))
        grid_docs.append({"grid": grid, "window_kind": grid, "stress": stress,
                          "slip_ticks": slip,
                          "frozen_csv": frozen[grid]["csv"],
                          "frozen_csv_sha256": frozen[grid]["csv_sha256"],
                          "frozen_row_identity_sha256": digest(frozen[grid]["row"]),
                          "replay_row_identity_sha256": digest(row),
                          "trace_inert": plain is None or plain == row,
                          "fills": len(collector.fills),
                          "episodes": len(collector.episodes),
                          "equity_rows": len(equity_rows)})
        log("grid %-20s matches frozen row (fills=%d episodes=%d equity=%d)"
            % (grid, len(collector.fills), len(collector.episodes), len(equity_rows)))

    write_csv(os.path.join(out_dir, "replay_aggregate.csv"), ["grid"] + list(header),
              aggregate_rows)
    doc = {
        "schema_version": 1,
        "kind": "survivor_evidence_replay",
        "contract": "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md %s" % CONTRACT_VERSION,
        "contract_section": "28.3 / 28.4",
        "survivor_id": entry["survivor_id"],
        "cohort": entry["cohort"],
        "symbol": symbol,
        "timeframe": timeframe,
        "family_id": entry["family_id"],
        "round_id": entry["round_id"],
        "run_id": entry["run_id"],
        "kanban_task_id": entry["kanban_task_id"],
        "params": {"strategy_params": entry["strategy_params"], "dca_params": entry["dca_params"],
                   "base_quote": dca["base_quote"]},
        "params_sha256": entry["params_sha256"],
        "research_data_cutoff": entry["research_data_cutoff"],
        "bundle_path": bundle_path,
        "bundle_path_recorded_in_index": entry["bundle_path"],
        "bundle_sha256": entry["bundle_sha256"],
        "bundle_identity_sha256": entry["bundle_identity_sha256"],
        "frozen_attempt_dir": attempt_dir,
        "frozen_round_spec_path": os.path.join(round_dir, "round-spec.json"),
        "frozen_run_spec_path": os.path.join(attempt_dir, "run-spec.json"),
        "frozen_attempt_governance": {
            "registered_grids": len(grid_docs),
            "replayed_evaluations": len(grid_docs),
            "research_evaluations_not_replayed":
                spec["expected"]["expected_case_evaluations"] - len(grid_docs),
            "grid_csv_sha256_source": "the frozen attempt's terminal DONE sentinel "
                                      "artifact_checksums (not re-hashed by hand)",
            "frozen_csv_rows_each": 1 + spec["expected"]["case_evaluations_per_grid"]},
        "source_data": source_data,
        "source_runner": {"path": spec["script"]["path"],
                          "sha256": spec["script"]["sha256"],
                          "sha256_source": "frozen run-spec script.sha256",
                          "deployed_runner_path": args.deployed_runner,
                          "deployed_sha256_readback": deployed_sha},
        "replay_runner": {"path": engine_path, "sha256": engine_sha,
                          "note": "instrumented repo runner (adds the inert trace hook only; "
                                  "engine SHA differs from source_runner by that hook)"},
        "grids": grid_docs,
        "trace": {"emit_points": 11, "sink": "engine.TRACE (None in production)"},
        "materialization_disclosure": (
            "these ledgers are a DETERMINISTIC REPLAY materialisation produced by re-running "
            "the same engine on the same frozen inputs; the original research run kept only "
            "aggregates, so this is not a recovery of bytes the original run stored"),
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with open(os.path.join(out_dir, "replay.json"), "w") as fh:
        fh.write(json.dumps(engine._sanitize(doc), indent=2, ensure_ascii=False) + "\n")

    payload = {"ok": True, "survivor_id": entry["survivor_id"], "cohort": entry["cohort"],
               "staging": out_dir, "grids": len(grid_docs),
               "cells_replayed": len(grid_docs), "trace_inert_verified": verify_inert,
               "engine_sha256": engine_sha,
               "frozen_rows_matched": len(grid_docs)}
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print("replay ok survivor=%s cohort=%s grids=%d staging=%s"
              % (entry["survivor_id"], entry["cohort"], len(grid_docs), out_dir))
    return 0


def to_local(path, index, results_root):
    """Map a path recorded by the host-side index onto this runtime's results root.

    The index and the bundles store absolute HOST paths (e.g. `/Volumes/…/qlib-results/…`) while
    the container sees the very same tree at `/results`; the mapping is a prefix substitution of
    the index's own recorded `results_root`, and both the recorded and the local path are kept in
    the replay header so nothing is silently rewritten.
    """
    recorded = index.get("results_root")
    if recorded and (path == recorded or path.startswith(recorded + os.sep)):
        return results_root + path[len(recorded):]
    return path


def numbers_equal(text, value):
    """True iff a frozen CSV cell and a winner-cell coordinate are the same registered number."""
    try:
        return float(text) == float(value)
    except (TypeError, ValueError):
        return False


def values_equal(value, text):
    """Compare one engine record value against the frozen CSV cell, exactly."""
    if value is None:
        return text == ""
    if isinstance(value, bool):
        return text == str(value)
    if isinstance(value, float):
        try:
            return float(text) == value
        except ValueError:
            return False
    return text == str(value)


if __name__ == "__main__":
    sys.exit(main())
