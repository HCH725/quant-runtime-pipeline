#!/usr/bin/env python3
"""CMT lineage — survivor evidence replay driver (Contract v1.8.0 sections 28.3/28.4).

Re-materialises the execution ledgers (fills / episodes / equity marks) of ALREADY FROZEN
survivor winner cells by re-running the **same engine** the production run used — this module
imports `140_continuous_macro_timing_run.py` as a module and calls its existing `Cohort`,
`build_bins`, `load_funding_series`, `build_funding_index`, `signals_for`, `rail_for`,
`simulate` and `record`; it never re-implements `simulate()` and never calls `summarize()`.

Scope: only the cells that are already leaderboard entries.  For one survivor the driver
replays exactly the registered phase grids read back from the frozen attempt's terminal `DONE`
sentinel (10 for this family) winner-cell evaluations, never the 11,520-evaluation research
sweep, and it fails closed on the first column of the first grid whose replayed winner row
differs from the frozen, DONE-sentinel-pinned `artifacts/grid_<grid>.csv` row.

Engine identity: unlike the J/K/L lineage, the CMT engine already carries the contract §28.2
inert trace hook in the FROZEN bytes, so the replay loads the engine the run-spec pins and
requires `sha256(engine) == spec["script"]["sha256"]` — the "same engine" claim is byte-exact,
not a copy that differs by a hook.  The deployed `/scripts` readback is recorded alongside.

Registered-grid enumeration (contract §28.3): the grid list is read back from two frozen
sources that must agree — the terminal `DONE` sentinel's `artifact_manifest` and the run-spec's
`expected_outputs` — and never from this driver's own claim.  This family's frozen run-specs
register NO `expected_outputs` (the names live in the round-spec pre-registration), so the
published rule refuses; the refusal names the measured gap and the pending decision instead of
inventing a third source.  (The staging-side proposal driver keeps the `expected_outputs`
branch byte-identical and accepts the lineage shape; see the staging evidence README.)

Ledger vocabulary (contract §28.2): `exit_reason` is TP / STOP / MARGIN_CALL / EOD_FLATTEN
(matching tp_hits / stop_hits / margin_calls / open_at_end), plus TIME_EXIT — this family's
registered target-state exit, reported by the aggregate as `time_exits`.

Honesty (contract §28.4): the ledgers this driver writes are a **deterministic replay
materialisation**, produced now, from the frozen inputs — they are NOT the bytes the original
research run kept (the original run kept only aggregates).  That disclosure is carried into
the package manifest; nothing here claims the original run stored a ledger.

Writes only into `--staging <dir>` (the host-side `runtime/survivor_evidence.py` owns the final
`_survivors/evidence/<survivor_id>/` package and its write boundary); this driver refuses any
`--staging` that is not inside a `_survivors/evidence/` subtree, and it never writes into a
`<family_id>`, round or attempt directory and never writes a verdict or bundle.

usage:
  /opt/venv/bin/python 141_continuous_macro_timing_survivor_replay.py --survivor-id <sv-...> \
      --staging <staging-dir> [--results-root /results] [--engine <path>] \
      [--deployed-runner <path>] [--no-verify-trace-inert] [--json]
exit: 0 = ok, 1 = refused/mismatch, 2 = usage error
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_RESULTS_ROOT = "/results"
SENTINEL_NAME = "DONE"
SURVIVORS_DIRNAME = "_survivors"
EVIDENCE_DIRNAME = "evidence"
CONTRACT_VERSION = "v1.8.0"

FILL_COLUMNS = ("episode", "event_type", "bar_index", "open_time_ms", "price", "qty",
                "dca_level", "trigger_price", "ref_price", "fee", "slip_ticks", "sign")
EPISODE_COLUMNS = ("episode", "entry_bar_index", "entry_time_ms", "entry_price",
                   "exit_bar_index", "exit_time_ms", "exit_price", "exit_reason",
                   "gross_pnl", "fees", "funding", "net_pnl", "holding_bars", "layers_used",
                   "mae_usdt", "mfe_usdt", "sign")
EQUITY_COLUMNS = ("day_index", "date", "equity", "peak", "drawdown_usdt", "drawdown_pct",
                  "in_window")
EVENT_TYPES = ("ENTRY", "DCA_ADD", "EXIT", "FLATTEN")
EXIT_PARTITION = (("TP", "tp_hits"), ("STOP", "stop_hits"), ("MARGIN_CALL", "margin_calls"),
                  ("EOD_FLATTEN", "open_at_end"), ("TIME_EXIT", "time_exits"))
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


def json_safe(obj):
    """Make a payload strictly JSON-safe: numpy scalars -> python, non-finite -> null."""
    if isinstance(obj, bool) or obj is None or isinstance(obj, (int, str)):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    item = getattr(obj, "item", None)
    if callable(item):
        try:
            return json_safe(item())
        except Exception:  # noqa: BLE001
            return str(obj)
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    return str(obj)


def csv_value(value):
    """A ledger cell as a plain python scalar (numpy scalars unwrapped; None stays empty)."""
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return item()
        except Exception:  # noqa: BLE001
            return value
    return value


def load_engine(path):
    """Load the SAME engine module the production run uses (never a copy of its logic)."""
    spec = importlib.util.spec_from_file_location("cmt_engine_replay", path)
    if spec is None or spec.loader is None:
        raise SystemExit("REFUSED: %s is not an importable python module" % path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def utc_date(ms):
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000.0))


def trace_supported(engine):
    return hasattr(engine, "TRACE") and callable(getattr(engine, "_trace", None))


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


# ---------------------------------------------------------------------------
# contract 28.3 registered-grid enumeration (the PUBLISHED rule)
# ---------------------------------------------------------------------------

def registered_grid_names(sentinel, spec, round_spec=None):
    """The registered phase grids, from two frozen sources that must agree (contract 28.3):

    the terminal `DONE` sentinel's `artifact_manifest` and the run-spec's `expected_outputs`.
    The grid list is therefore never taken from the replay's own claim.  `round_spec` is
    accepted (and unused here) so the staging-side proposal variant can be a one-function
    substitution; the published rule reads exactly the two frozen sources the contract names.
    """
    def names(entries):
        return sorted(rel[len("artifacts/grid_"):-len(".csv")] for rel in entries
                      if rel.startswith("artifacts/grid_") and rel.endswith(".csv"))

    from_sentinel = names(sentinel.get("artifact_manifest") or [])
    from_spec = names(spec.get("expected_outputs") or [])
    if not from_sentinel:
        raise SystemExit("REFUSED: the frozen attempt's terminal DONE sentinel lists no "
                         "artifacts/grid_*.csv, so the registered grids cannot be enumerated")
    if from_sentinel != from_spec:
        raise SystemExit("REFUSED: the frozen terminal sentinel's artifact manifest %r and the "
                         "run-spec expected_outputs %r disagree on the registered grids "
                         "(contract 28.3); this family's frozen run-spec registers no "
                         "expected_outputs at all, so the registered grid set cannot be read "
                         "back from the two sources the published rule names - the names live "
                         "in the round-spec pre-registration, and accepting that third shape is "
                         "a contract-level decision that has NOT been taken"
                         % (from_sentinel, from_spec))
    return from_sentinel


# ---------------------------------------------------------------------------
# per-grid plan: the engine's own `run_cohort` mapping, in one readable place
# ---------------------------------------------------------------------------

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
    if grid == "cost_attrition_40bps":
        return windows["full"], dict(engine.COST_ATTRITION_STRESS), slip
    if grid == "no_funding":
        return windows["historical"], {"no_funding": True}, slip
    if grid == "no_funding_full":
        return windows["full"], {"no_funding": True}, slip
    raise SystemExit("REFUSED: %r is not a registered phase grid of this engine (%r)"
                     % (grid, list(engine.COHORT_GRID_KINDS)))


def read_csv_rows(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def write_csv(path, columns, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(columns), lineterminator="\n")
        w.writeheader()
        for row in rows:
            w.writerow({k: csv_value(row.get(k)) for k in columns})


def evidence_boundary_problem(staging):
    """Contract 28.5: the replay writes only inside a `_survivors/evidence/` subtree."""
    resolved = os.path.realpath(os.path.abspath(staging))
    marker = os.sep + SURVIVORS_DIRNAME + os.sep + EVIDENCE_DIRNAME + os.sep
    if marker not in resolved + os.sep:
        return ("staging directory %s is not inside a %s/%s/ subtree: the replay only writes "
                "under the reserved evidence namespace and never into a <family_id>, round or "
                "attempt directory" % (resolved, SURVIVORS_DIRNAME, EVIDENCE_DIRNAME))
    return None


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


def reconcile(aggregate_row, fills, episodes, equity_rows):
    """Deterministic ledger self-check (contract 28.3) against the engine's aggregate row.

    Mirrors `runtime/survivor_evidence.py::ledger_problems` so a ledger that would be refused
    host-side is refused here, at replay time: the episode partition, the Σepisode accounting
    identities, the equity ledger's day count and a pure-stdlib Sharpe / max-drawdown
    recomputation from the equity column.
    """
    problems = []
    n = len(episodes)
    if n != aggregate_row["episodes"]:
        problems.append("episode ledger has %d episodes, aggregate says %d"
                        % (n, aggregate_row["episodes"]))
    for reason, key in EXIT_PARTITION:
        got = sum(1 for e in episodes if e["exit_reason"] == reason)
        if got != aggregate_row[key]:
            problems.append("episode partition %s=%d, aggregate %s=%d"
                            % (reason, got, key, aggregate_row[key]))
    for field, key in (("gross_pnl", "gross_pnl"), ("fees", "fees"), ("funding", "funding"),
                       ("net_pnl", "net_pnl")):
        total = sum(e[field] for e in episodes)
        want = aggregate_row[key]
        if abs(total - want) > TOL * max(1.0, abs(want)):
            problems.append("sum(episode %s)=%.9f != aggregate %s=%.9f" % (field, total, key,
                                                                          want))
    for e in episodes:
        net = e["gross_pnl"] - e["fees"] - e["funding"]
        if abs(net - e["net_pnl"]) > TOL * max(1.0, abs(e["net_pnl"])):
            problems.append("episode %s PnL identity broken (gross - fees - funding = %.9f, "
                            "ledger says %.9f)" % (e["episode"], net, e["net_pnl"]))
            break
    for fill in fills:
        if fill["event_type"] not in EVENT_TYPES:
            problems.append("fill carries unregistered event_type %r" % (fill["event_type"],))
            break
        if not (fill["episode"] < n):
            problems.append("fill references episode %r but only %d episodes recorded"
                            % (fill["episode"], n))
            break
    if len(equity_rows) != int(aggregate_row["days"]):
        problems.append("equity ledger has %d rows, aggregate says days=%d"
                        % (len(equity_rows), int(aggregate_row["days"])))
        return problems
    series = [float(r["equity"]) for r in equity_rows]
    peak = -float("inf")
    worst_dd = 0.0
    worst_dd_pct = 0.0
    for i, value in enumerate(series):
        peak = max(peak, value)
        dd = value - peak
        if csv_value(equity_rows[i]["peak"]) != peak or abs(
                float(equity_rows[i]["drawdown_usdt"]) - dd) > TOL:
            problems.append("equity ledger row %d peak/drawdown does not match a running "
                            "maximum over its own equity column" % i)
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
            problems.append("equity ledger recomputes %s=%.9f, aggregate says %.9f"
                            % (key, got, want))
    return problems


def replay_cell(engine, cohort, layer, series, window, p, dca, grid, stress, slip, collector,
                verify_inert):
    """One winner-cell evaluation, traced; returns (traced_row, plain_row_or_None).

    `count_layers=False` keeps the replay out of the full-window DCA ladder histogram: this is
    the same "diagnostic re-run of one already-counted cell" the engine's own winner diagnostics
    use, and it cannot move any returned value (verified by the trace off/on row equality).
    """
    rail = engine.rail_for(dca)
    engine.TRACE = None
    plain = engine.simulate(cohort, p, rail, window, stress, slip, grid, series,
                            count_layers=False, layer=layer)
    collector.reset()
    engine.TRACE = collector
    traced = engine.simulate(cohort, p, rail, window, stress, slip, grid, series,
                             count_layers=False, layer=layer)
    engine.TRACE = None
    row_traced = engine.record(cohort, p, dca, grid, traced)
    if not verify_inert:
        return row_traced, None
    return row_traced, engine.record(cohort, p, dca, grid, plain)


def build_cohorts(engine, spec, log_fn):
    """The replayed cohorts and the registered four-instrument panel they share a signal with.

    Mirrors the production runner's phase-1 cohort construction (contract 28.3: same engine,
    same frozen inputs): every registered symbol gets a cohort on the replayed timeframe, the
    registered instrument metadata, its full funding series and the panel registration.
    """
    symbols = list(spec["data"]["symbols"])
    tf_list = list(spec["data"]["timeframes"])
    if len(tf_list) != 1:
        raise SystemExit("REFUSED: this replay driver covers the registered single-timeframe "
                         "lineage; the frozen run-spec declares %d timeframes" % len(tf_list))
    tf = tf_list[0]
    instruments = load_json(os.path.join(engine.RAW_ROOT, "instruments",
                                         "usdm-perp-instruments.json"))
    meta = {}
    for item in instruments["instruments"]:
        meta[item["fields"]["raw_symbol"]] = item["fields"]
    start, end = spec["data"]["start"], spec["data"]["end"]
    cohorts = {}
    for symbol in symbols:
        if symbol not in meta:
            raise SystemExit("REFUSED: no instrument metadata for %s" % symbol)
        cohort = engine.Cohort(symbol, tf, start, end, spec)
        cohort.price_increment = float(meta[symbol]["price_increment"])
        cohort.taker_fee = float(meta[symbol]["taker_fee"])
        cohort.leverage = 1.0 / float(meta[symbol]["margin_init"])
        cohort.margin_maint = float(meta[symbol]["margin_maint"])
        ft, fr, counts = engine.load_funding_series(symbol, start, end)
        series = engine.build_funding_index(cohort, ft, fr, counts, "full")
        cohorts[symbol] = (cohort, series)
        log_fn("cohort %s/%s bars=%d funding_obs=%d official=%d modeled=%d"
               % (symbol, tf["raw_interval"], cohort.n, len(ft),
                  counts["official"], counts["modeled_funding"]))
    engine.PANEL_COHORTS.clear()
    engine.PANEL_COHORTS.update({s: c for s, (c, _series) in cohorts.items()})
    return cohorts


def main(argv=None):
    ap = argparse.ArgumentParser(description="CMT-lineage survivor evidence replay driver "
                                             "(contract %s)" % CONTRACT_VERSION)
    ap.add_argument("--survivor-id", required=True)
    ap.add_argument("--staging", required=True)
    ap.add_argument("--results-root", default=DEFAULT_RESULTS_ROOT)
    ap.add_argument("--engine", default=None,
                    help="the engine module named by the frozen run-spec (default: this repo's "
                         "runner of that name)")
    ap.add_argument("--deployed-runner", default=None)
    ap.add_argument("--no-verify-trace-inert", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    staging = os.path.realpath(os.path.abspath(args.staging))
    problem = evidence_boundary_problem(staging)
    if problem:
        sys.stderr.write("REFUSED: %s\n" % problem)
        return 1
    results_root = os.path.realpath(os.path.abspath(args.results_root))

    # The survivor to replay is read back from the durable leaderboard/index artifacts - never
    # hard-coded, and never re-derived from the driver's own input.
    index_path = os.path.join(results_root, SURVIVORS_DIRNAME, "survivor-index.json")
    board_path = os.path.join(results_root, SURVIVORS_DIRNAME, "leaderboard.json")
    for path in (index_path, board_path):
        if not os.path.isfile(path):
            sys.stderr.write("REFUSED: no %s at %s\n"
                             % (os.path.basename(path), path))
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
    if not os.path.isfile(bundle_path):
        sys.stderr.write("REFUSED: frozen survivor bundle %s is missing\n" % bundle_path)
        return 1
    if sha256_file(bundle_path) != entry["bundle_sha256"]:
        sys.stderr.write("REFUSED: frozen survivor bundle %s changed since the index was built\n"
                         % bundle_path)
        return 1
    bundle = load_json(bundle_path)
    records = [s for s in bundle["survivors"] if s["cohort"] == entry["cohort"]]
    if len(records) != 1:
        sys.stderr.write("REFUSED: bundle holds %d records for cohort %s\n"
                         % (len(records), entry["cohort"]))
        return 1
    winner = records[0]["winner"]

    # The registered parameter contract (round-spec copy) defines the winner cell's axes.
    round_dir = os.path.dirname(bundle_path)
    round_spec_path = os.path.join(round_dir, "round-spec.json")
    if not os.path.isfile(round_spec_path):
        sys.stderr.write("REFUSED: frozen round-spec %s is missing\n" % round_spec_path)
        return 1
    round_spec = load_json(round_spec_path)
    contract = round_spec.get("parameter_contract")
    if not isinstance(contract, dict):
        sys.stderr.write("REFUSED: round-spec %s carries no parameter_contract: the winner "
                         "cell axes cannot be read back (fail closed)\n" % round_spec_path)
        return 1
    strat_fields = list(contract.get("strategy_param_fields") or [])
    dca_fields = list(contract.get("dca_param_fields") or [])
    row_fields = list(contract.get("row_fields") or [])
    if not (strat_fields and dca_fields and row_fields):
        sys.stderr.write("REFUSED: parameter_contract is missing strategy/dca/row field lists\n")
        return 1
    for key in strat_fields:
        if key not in entry["strategy_params"] or key not in winner:
            sys.stderr.write("REFUSED: strategy axis %r missing from the index or the bundle "
                             "winner\n" % key)
            return 1
        if float(winner[key]) != float(entry["strategy_params"][key]):
            sys.stderr.write("REFUSED: bundle winner strategy %s=%r disagrees with the index "
                             "%r\n" % (key, winner[key], entry["strategy_params"][key]))
            return 1
    for key in dca_fields:
        if key not in entry["dca_params"] or key not in winner:
            sys.stderr.write("REFUSED: DCA axis %r missing from the index or the bundle "
                             "winner\n" % key)
            return 1
        if float(winner[key]) != float(entry["dca_params"][key]):
            sys.stderr.write("REFUSED: bundle winner DCA %s=%r disagrees with the index %r\n"
                             % (key, winner[key], entry["dca_params"][key]))
            return 1

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
    log("frozen gates ok: bundle_sha256=%s sentinel=%s(%s) contract_axes=%d/%d row_fields=%d"
        % (entry["bundle_sha256"][:24], sentinel.get("status"), entry["run_id"],
           len(strat_fields), len(dca_fields), len(row_fields)))
    registered = registered_grid_names(sentinel, spec, round_spec)

    # Engine identity: the frozen CMT engine already carries the inert trace hook, so the
    # replay must load the very bytes the run-spec pins.
    spec_engine_name = os.path.basename(spec["script"]["path"])
    engine_path = os.path.realpath(os.path.abspath(args.engine)) if args.engine else None
    if engine_path is None:
        engine_path = os.path.join(HERE, spec_engine_name)
    if os.path.basename(engine_path) != spec_engine_name:
        sys.stderr.write("REFUSED: --engine %s is not the frozen run-spec's registered runner "
                         "%r\n" % (engine_path, spec_engine_name))
        return 1
    if not os.path.isfile(engine_path):
        sys.stderr.write("usage error: engine module not found: %s\n" % engine_path)
        return 2
    engine = load_engine(engine_path)
    engine_sha = sha256_file(engine_path)
    if engine_sha != spec["script"]["sha256"]:
        sys.stderr.write("REFUSED: the loaded engine %s hashes to %s, not the sha256 the frozen "
                         "run-spec pins (%r): a §28 replay must re-run the same engine bytes\n"
                         % (engine_path, engine_sha, spec["script"]["sha256"]))
        return 1
    if not trace_supported(engine):
        sys.stderr.write("REFUSED: %s carries no inert trace hook (module-level TRACE / _trace, "
                         "contract 28.2)\n" % engine_path)
        return 1
    deployed_runner = args.deployed_runner or ("/scripts/" + spec_engine_name)
    deployed_sha = None
    if os.path.isfile(deployed_runner):
        deployed_sha = sha256_file(deployed_runner)

    if sorted(registered) != sorted(engine.COHORT_GRID_KINDS):
        sys.stderr.write("REFUSED: the frozen registered grid set %r is not this engine's "
                         "COHORT_GRID_KINDS %r\n"
                         % (sorted(registered), sorted(engine.COHORT_GRID_KINDS)))
        return 1
    if int(spec["expected"]["phase_grids"]) != len(registered):
        sys.stderr.write("REFUSED: run-spec expected.phase_grids=%r but the frozen attempt lists "
                         "%d registered grids\n"
                         % (spec["expected"]["phase_grids"], len(registered)))
        return 1

    # per-registered-grid: pinned frozen row + the winner cell's coordinates
    cell = dict(entry["strategy_params"])
    cell.update(entry["dca_params"])
    missing = [k for k in row_fields if k not in cell]
    if missing:
        sys.stderr.write("REFUSED: the index entry carries no value for registered row field(s) "
                         "%r\n" % missing)
        return 1
    header = None
    frozen = {}
    symbol, timeframe = entry["symbol"], entry["timeframe"]
    for grid in registered:
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
        hits = [r for r in rows if r["symbol"] == symbol and r["timeframe"] == timeframe
                and all(numbers_equal(r[k], cell[k]) for k in row_fields)]
        if len(hits) != 1:
            sys.stderr.write("REFUSED: %s holds %d rows for winner cell %r\n"
                             % (path, len(hits), cell))
            return 1
        frozen[grid] = {"csv": path, "csv_sha256": actual, "sentinel_sha256": checksums.get(rel),
                        "row": hits[0]}

    # The winner cell's registered coordinate values, typed for the engine.
    p = {k: entry["strategy_params"][k] for k in strat_fields}
    dca = {k: entry["dca_params"][k] for k in dca_fields}
    dca["base_quote"] = spec["dca_domain"]["base_quote"]
    case = engine.case_tuple(p)
    if case not in engine.CASE_ORDER:
        sys.stderr.write("REFUSED: the index's strategy params %r are not a registered case of "
                         "%s\n" % (p, engine_path))
        return 1
    tf = [t for t in spec["data"]["timeframes"] if t["raw_interval"] == timeframe]
    if len(tf) != 1:
        sys.stderr.write("REFUSED: timeframe %r is not registered in the frozen run-spec\n"
                         % timeframe)
        return 1

    input_manifest_path = os.path.join(attempt_dir, "artifacts", "input_manifest.json")
    bins_build_path = os.path.join(attempt_dir, "artifacts", "bins_build.json")
    source_data = {"attempt_dir": attempt_dir,
                   "input_manifest_path": input_manifest_path,
                   "input_manifest_sha256": sha256_file(input_manifest_path),
                   "bins_build_path": bins_build_path,
                   "bins_build_sha256": sha256_file(bins_build_path),
                   "data": spec["data"]}
    # A pinned checksum is verified fail-closed; an unpinned one is recorded as a plainly
    # labelled readback with its provenance named - never silently accepted, never dropped.
    for rel, key in (("artifacts/input_manifest.json", "input_manifest_sha256"),
                     ("artifacts/bins_build.json", "bins_build_sha256")):
        pinned = checksums.get(rel)
        if pinned is None:
            source_data[key + "_source"] = ("readback (the frozen terminal sentinel records no "
                                            "checksum for this path)")
            continue
        if pinned != source_data[key]:
            sys.stderr.write("REFUSED: frozen %s hashes to %s, not the checksum the terminal "
                             "sentinel recorded (%r)\n" % (rel, source_data[key], pinned))
            return 1
        source_data[key + "_source"] = "frozen terminal DONE sentinel artifact_checksums"

    log("replay survivor=%s cohort=%s case=%s params=%s"
        % (entry["survivor_id"], entry["cohort"], engine.case_label(case), canonical(p)))
    log("engine=%s (%s) deployed=%s pin=%s"
        % (engine_path, engine_sha, deployed_sha, spec["script"]["sha256"]))
    # Rebuild the Qlib store for the registered data (a rebuildable derived area); the report
    # is written to a scratch directory - never into the frozen attempt directory.
    bins_dir = tempfile.mkdtemp(prefix="cmt-replay-bins-")
    try:
        os.makedirs(os.path.join(bins_dir, "artifacts"), exist_ok=True)
        engine.build_bins(spec, bins_dir, log)
    finally:
        shutil.rmtree(bins_dir, ignore_errors=True)

    cohorts = build_cohorts(engine, spec, log)
    cohort, series = cohorts[symbol]
    for other, (other_cohort, _s) in cohorts.items():
        layer = engine.signals_for(other_cohort, case)
        log("signal layer %s %s events=%d defined=%d"
            % (other, layer.case_label, int((layer.events != 0).sum()),
               int((np_isfinite_count(layer.yhat)))))

    out_dir = os.path.join(staging, entry["survivor_id"])
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(os.path.join(out_dir, "grids"), exist_ok=True)

    layer = engine.signals_for(cohort, case)
    collector = Collector()
    aggregate_rows = []
    grid_docs = []
    verify_inert = not args.no_verify_trace_inert
    for grid in registered:
        window_dates, stress, slip = grid_plan(engine, spec, grid)
        window = cohort.slice(*window_dates)
        row, plain = replay_cell(engine, cohort, layer, series, window, p, dca, grid, stress,
                                 slip, collector, verify_inert)
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
            series_flat = [float(v) for v in equity["equity"]]
            peak = -float("inf")
            for i, value in enumerate(series_flat):
                peak = max(peak, value)
                equity_rows.append({"day_index": equity["day_index"][i],
                                    "date": utc_date(equity["day_start_ms"][i]),
                                    "equity": value, "peak": peak,
                                    "drawdown_usdt": value - peak,
                                    "drawdown_pct": (value - peak) / peak if peak else 0.0,
                                    "in_window": bool(equity["in_window"][i])})
            problems.extend(reconcile(row, collector.fills, collector.episodes, equity_rows))
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
                   "window_bar_slice": list(window), "slip_ticks": slip,
                   "aggregate": json_safe(row), "frozen_csv": frozen[grid]["csv"],
                   "frozen_csv_sha256": frozen[grid]["csv_sha256"],
                   "frozen_row_identity_sha256": digest(frozen[grid]["row"]),
                   "fills": len(collector.fills), "episodes": len(collector.episodes),
                   "equity_rows": len(equity_rows)}
        with open(os.path.join(grid_dir, "summary.json"), "w") as fh:
            fh.write(json.dumps(json_safe(summary), indent=2, ensure_ascii=False) + "\n")
        aggregate_rows.append(dict({"grid": grid}, **{k: row[k] for k in header}))
        grid_docs.append({"grid": grid, "window_kind": grid, "stress": stress,
                          "slip_ticks": slip, "frozen_csv": frozen[grid]["csv"],
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
    emit_points = open(engine_path).read().count("_trace(") - 1  # minus the def itself
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
        "frozen_round_spec_path": round_spec_path,
        "frozen_run_spec_path": os.path.join(attempt_dir, "run-spec.json"),
        "frozen_attempt_governance": {
            "registered_grids": len(grid_docs),
            "replayed_evaluations": len(grid_docs),
            "research_evaluations_not_replayed":
                spec["expected"]["expected_case_evaluations"] - len(grid_docs),
            "grid_csv_sha256_source": "the frozen attempt's terminal DONE sentinel "
                                      "artifact_checksums (not re-hashed by hand)",
            "registered_grids_source": "sentinel artifact_manifest ∩ run-spec "
                                       "expected_outputs (must agree, contract 28.3)",
            "frozen_csv_rows_each": 1 + spec["expected"]["case_evaluations_per_grid"]},
        "source_data": source_data,
        "source_runner": {"path": spec["script"]["path"],
                          "sha256": spec["script"]["sha256"],
                          "sha256_source": "frozen run-spec script.sha256",
                          "deployed_runner_path": deployed_runner,
                          "deployed_sha256_readback": deployed_sha,
                          "deployed_matches_frozen_pin": deployed_sha == spec["script"]["sha256"]},
        "replay_runner": {"path": engine_path, "sha256": engine_sha,
                          "note": "the frozen CMT engine already carries the inert trace hook, "
                                  "so the replay runs the pinned bytes themselves (sha256 == "
                                  "the frozen run-spec pin; no instrumented copy)"},
        "grids": grid_docs,
        "trace": {"emit_points": emit_points, "sink": "engine.TRACE (None in production)"},
        "materialization_disclosure": (
            "these ledgers are a DETERMINISTIC REPLAY materialisation produced by re-running "
            "the same engine on the same frozen inputs; the original research run kept only "
            "aggregates, so this is not a recovery of bytes the original run stored"),
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with open(os.path.join(out_dir, "replay.json"), "w") as fh:
        fh.write(json.dumps(json_safe(doc), indent=2, ensure_ascii=False, allow_nan=False)
                 + "\n")

    payload = {"ok": True, "survivor_id": entry["survivor_id"], "cohort": entry["cohort"],
               "staging": out_dir, "grids": len(grid_docs),
               "cells_replayed": len(grid_docs), "trace_inert_verified": verify_inert,
               "engine_sha256": engine_sha, "frozen_rows_matched": len(grid_docs)}
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print("replay ok survivor=%s cohort=%s grids=%d staging=%s"
              % (entry["survivor_id"], entry["cohort"], len(grid_docs), out_dir))
    return 0


def np_isfinite_count(values):
    """Count of finite entries of a score path, for the log line only (no policy reads it)."""
    try:
        import numpy as np
        return int(np.isfinite(np.asarray(values)).sum())
    except Exception:  # noqa: BLE001
        return -1


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


if __name__ == "__main__":
    sys.exit(main())
