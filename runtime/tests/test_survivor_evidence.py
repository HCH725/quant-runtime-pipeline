#!/usr/bin/env python3
"""Host-side regression for survivor evidence preservation (contract v1.6.0 section 28).

Everything runs on a self-built fixture results root in a temp directory - the real `/results` is
never touched.  What is covered:

  * materialize publishes a package and `check` re-verifies it from the durable artifacts;
  * the package is idempotent (`already_identical`) and an existing package with a DIFFERENT
    identity is refused, never overwritten;
  * the evidence write boundary (`_survivors/evidence/**`, reserved-root/symlink rules) is
    enforced, and staging outside it is refused;
  * only formal leaderboard entries are preserved (a non-entry survivor never gets a package);
  * coverage is non-gating: a missing/removed package leaves the leaderboard valid (rc=0) with
    rank / Top-10 / evidence_state / champion_candidate and the CSV ordering columns unchanged;
  * tampering (a ledger byte, an aggregate column, an episode partition) is detected;
  * the manifest makes no fake original-run claim and no per-cell ledger exists under the
    research namespace.

usage:  python3 runtime/tests/test_survivor_evidence.py
"""
import contextlib
import csv
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME = os.path.dirname(HERE)
sys.path.insert(0, RUNTIME)

import survivor_evidence as se  # noqa: E402
import survivor_index as si  # noqa: E402
import survivor_leaderboard as sl  # noqa: E402
from survivor_bundle import identity as bundle_identity  # noqa: E402

FAMILY = "fixture-family-v1"
ROUND = "fixture-family-v1-r1"
RUN = "fixture-family-v1-r1-u1"
TASK = "t_fixture"
SYMBOL, TIMEFRAME = "BTCUSDT", "1h"
GRIDS = ("historical", "full")
WINNER = {"window": 20, "discount": 0.03, "spacing_pct": 0.01, "size_multiplier": 1.0,
          "breakeven_tp_pct": 0.01, "invalidation_pct": 0.1}
GRID_COLUMNS = ["symbol", "timeframe", "window_kind", "window", "discount", "spacing_pct",
                "size_multiplier", "breakeven_tp_pct", "invalidation_pct", "net_pnl", "fees",
                "funding", "gross_pnl", "ending_equity", "episodes", "tp_hits", "stop_hits",
                "open_at_end", "margin_calls", "halted", "sharpe", "max_dd_usdt", "max_dd_pct",
                "days", "total_return_pct"]
FILL_COLUMNS = ["episode", "event_type", "bar_index", "open_time_ms", "price", "qty", "dca_level",
                "trigger_price", "ref_price", "fee", "slip_ticks"]
EPISODE_COLUMNS = ["episode", "entry_bar_index", "entry_time_ms", "entry_price", "exit_bar_index",
                   "exit_time_ms", "exit_price", "exit_reason", "gross_pnl", "fees", "funding",
                   "net_pnl", "holding_bars", "layers_used", "mae_usdt", "mfe_usdt"]
EQUITY_COLUMNS = ["day_index", "date", "equity", "peak", "drawdown_usdt", "drawdown_pct",
                  "in_window"]


def dump(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        h.update(fh.read())
    return "sha256:" + h.hexdigest()


def write_csv(path, columns, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def bump_csv_cell(path, row_index, column, delta):
    """Deterministically change one numeric cell of a written CSV (tamper control)."""
    with open(path) as fh:
        rows = list(csv.reader(fh))
    col = rows[0].index(column)
    rows[row_index][col] = repr(float(rows[row_index][col]) + delta)
    with open(path, "w", newline="") as fh:
        csv.writer(fh, lineterminator="\n").writerows(rows)


def series_stats(series):
    peaks = []
    peak = -float("inf")
    dd = []
    for value in series:
        peak = max(peak, value)
        peaks.append(peak)
        dd.append(value - peak)
    ddp = [d / p if p else 0.0 for d, p in zip(dd, peaks)]
    if len(series) > 2:
        rets = [(series[i] - series[i - 1]) / series[i - 1] for i in range(1, len(series))]
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        sd = var ** 0.5
        sharpe = (mean / sd) * (365.0 ** 0.5) if sd > 0 else 0.0
    else:
        sharpe = 0.0
    return round(sharpe, 6), round(min(dd), 6), round(min(ddp), 6), peaks, dd, ddp


def episode(idx, reason, gross, fees, funding):
    return {"episode": idx, "entry_bar_index": 10 * idx, "entry_time_ms": 1700000000000 + idx,
            "entry_price": 100.0, "exit_bar_index": 10 * idx + 5,
            "exit_time_ms": 1700000000000 + idx + 5, "exit_price": 101.0, "exit_reason": reason,
            "gross_pnl": gross, "fees": fees, "funding": funding,
            "net_pnl": round(gross - fees - funding, 9), "holding_bars": 5, "layers_used": 2,
            "mae_usdt": -12.5, "mfe_usdt": 40.0}


def fills_for(idx, reason, fees, exit_event, exit_price):
    rows = [{"episode": idx, "event_type": "ENTRY", "bar_index": 10 * idx,
             "open_time_ms": 1700000000000 + idx, "price": 100.0, "qty": 10.0, "dca_level": 0,
             "trigger_price": "", "ref_price": 99.5, "fee": fees / 2.0, "slip_ticks": 1},
            {"episode": idx, "event_type": "DCA_ADD", "bar_index": 10 * idx + 1,
             "open_time_ms": 1700000000000 + idx + 1, "price": 99.0, "qty": 10.0, "dca_level": 1,
             "trigger_price": 99.0, "ref_price": 99.0, "fee": fees / 4.0, "slip_ticks": 1},
            {"episode": idx, "event_type": exit_event, "bar_index": 10 * idx + 5,
             "open_time_ms": 1700000000000 + idx + 5, "price": exit_price, "qty": 20.0,
             "dca_level": 2, "trigger_price": exit_price, "ref_price": exit_price,
             "fee": fees / 4.0, "slip_ticks": 1}]
    return rows


class Fixture(object):
    """A minimal but full-shape frozen round + index + leaderboard in a temp results root."""

    def __init__(self, root, leaderboard_entries=1):
        self.root = root
        self.family = os.path.join(root, FAMILY)
        self.round = os.path.join(self.family, "rounds", ROUND)
        self.attempt = os.path.join(self.round, "attempts", RUN)
        self.survivors = os.path.join(root, "_survivors")
        self.grid_rows = {}
        self.ledgers = {}
        self.build_round()
        self.build_staging()
        self.build_derived(leaderboard_entries)

    def build_round(self):
        dump(os.path.join(self.family, "family.json"),
             {"family_id": FAMILY, "kanban_task_id": TASK, "created_at_utc":
              "2026-09-13T00:00:00Z", "parent_family": None})
        dump(os.path.join(self.round, "round-spec.json"),
             {"family_id": FAMILY, "round_id": ROUND, "data": {"data_end": "2026-09-10"}})
        dump(os.path.join(self.round, "verdict.json"), {"verdict": "PASS"})
        dump(os.path.join(self.attempt, "run-spec.json"),
             {"family_id": FAMILY, "round_id": ROUND, "run_id": RUN,
              "script": {"path": "/scripts/20_strategy_a_run.py",
                         "sha256": "sha256:" + "c4" * 32},
              "data": {"symbols": [SYMBOL], "timeframes": [{"raw_interval": TIMEFRAME,
                                                            "qlib_freq": "60min"}],
                       "start": "2022-01-01", "end": "2026-09-10",
                       "historical_start": "2022-01-01", "historical_end": "2025-09-30",
                       "oos_start": "2025-10-01", "oos_end": "2026-09-10"},
              "dca_domain": {"base_quote": 1000},
              "expected": {"expected_case_evaluations": 576, "case_evaluations_per_grid": 576},
              "expected_outputs": ["result.json", "artifacts/bins_build.json",
                                   "artifacts/input_manifest.json"]
                                  + ["artifacts/grid_%s.csv" % g for g in GRIDS]})
        dump(os.path.join(self.attempt, "artifacts", "input_manifest.json"), {"raw_files": {}})
        dump(os.path.join(self.attempt, "artifacts", "bins_build.json"), {"qlib_dir_bytes": 1})
        for grid in GRIDS:
            self.grid_rows[grid] = self.build_grid(grid)
        checksums = {"result.json": "sha256:" + "11" * 32,
                     "artifacts/input_manifest.json":
                         sha(os.path.join(self.attempt, "artifacts", "input_manifest.json")),
                     "artifacts/bins_build.json":
                         sha(os.path.join(self.attempt, "artifacts", "bins_build.json"))}
        for grid in GRIDS:
            rel = "artifacts/grid_%s.csv" % grid
            checksums[rel] = sha(os.path.join(self.attempt, rel))
        dump(os.path.join(self.attempt, "DONE"),
             {"schema_version": 1, "status": "DONE", "family_id": FAMILY, "round_id": ROUND,
              "run_id": RUN, "task_id": TASK,
              "artifact_manifest": list(checksums), "artifact_checksums": checksums})

        sources = {"round-spec.json": sha(os.path.join(self.round, "round-spec.json")),
                   "run-spec.json": sha(os.path.join(self.attempt, "run-spec.json")),
                   "verdict.json": sha(os.path.join(self.round, "verdict.json")),
                   "result.json": checksums["result.json"]}
        bundle = {
            "schema_version": 1, "kind": "frozen_survivor_bundle",
            "contract": "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md v1.4.0",
            "family_id": FAMILY, "round_id": ROUND, "run_id": RUN, "kanban_task_id": TASK,
            "survivor_count": 1, "disposition_band": "SURVIVOR_FOUND", "verdict": "PASS",
            "ranking": None,
            "survivors": [{"cohort": "%s/%s" % (SYMBOL, TIMEFRAME), "outcome": "SURVIVOR",
                           "no_winner_reason": None, "cull_reasons": [], "winner": dict(WINNER),
                           "neighbourhood": {"neighbours": 6, "agreeing": 5,
                                             "same_sign_fraction": 0.833333, "passed": True},
                           "metrics": {"historical": {"net_pnl": 10.0, "sharpe": 1.0,
                                                      "episodes": 2, "max_dd_pct": -0.01},
                                       "oos": {"net_pnl": 20.0, "sharpe": 2.17438, "episodes": 3,
                                               "max_dd_pct": -0.02},
                                       "full": {"net_pnl": 30.0, "sharpe": 1.5, "episodes": 2,
                                                "max_dd_pct": -0.03},
                                       "robustness": {g: {"net_pnl": 5.0, "sharpe": 1.0,
                                                          "max_dd_pct": -0.04}
                                                      for g in ("fee_2x", "funding_2x",
                                                                "entry_delay_1_bar",
                                                                "slippage_2ticks")}}}],
            "source_artifacts": sources,
            "generator": {"path": "runtime/survivor_bundle.py",
                          "sha256": "sha256:" + "22" * 32},
            "generated_at_utc": "2026-09-13T01:00:00Z",
        }
        bundle["bundle_identity_sha256"] = bundle_identity(bundle)
        dump(os.path.join(self.round, "survivor-bundle.json"), bundle)

    def build_grid(self, grid):
        if grid == "historical":
            episodes = [episode(0, "TP", 100.0, 4.0, 1.0), episode(1, "STOP", -40.0, 4.0, 2.0)]
            equity = [30000.0, 30060.0, 30055.0]
            fill_fees = [4.0, 4.0]
            events, prices = ["EXIT"], [101.0]
        else:
            episodes = [episode(0, "EOD_FLATTEN", 250.0, 6.0, 3.0), episode(1, "TP", 90.0, 5.0, 1.5)]
            equity = [30000.0, 30100.0, 30338.5]
            fill_fees = [6.0, 5.0]
            events, prices = ["FLATTEN"], [100.5]
        sharpe, dd_usdt, dd_pct, peaks, dd, ddp = series_stats(equity)
        row = {"symbol": SYMBOL, "timeframe": TIMEFRAME, "window_kind": grid, "window": WINNER["window"],
               "discount": WINNER["discount"], "spacing_pct": WINNER["spacing_pct"],
               "size_multiplier": WINNER["size_multiplier"],
               "breakeven_tp_pct": WINNER["breakeven_tp_pct"],
               "invalidation_pct": WINNER["invalidation_pct"],
               "net_pnl": round(sum(e["net_pnl"] for e in episodes), 6),
               "fees": round(sum(e["fees"] for e in episodes), 6),
               "funding": round(sum(e["funding"] for e in episodes), 6),
               "gross_pnl": round(sum(e["gross_pnl"] for e in episodes), 6),
               "ending_equity": equity[-1], "episodes": len(episodes),
               "tp_hits": sum(1 for e in episodes if e["exit_reason"] == "TP"),
               "stop_hits": sum(1 for e in episodes if e["exit_reason"] == "STOP"),
               "open_at_end": sum(1 for e in episodes if e["exit_reason"] == "EOD_FLATTEN"),
               "margin_calls": 0, "halted": False, "sharpe": sharpe, "max_dd_usdt": dd_usdt,
               "max_dd_pct": dd_pct, "days": len(equity),
               "total_return_pct": round(equity[-1] / 30000.0 - 1.0, 6)}
        write_csv(os.path.join(self.attempt, "artifacts", "grid_%s.csv" % grid), GRID_COLUMNS, [row])
        fill_rows = []
        for i, ep in enumerate(episodes):
            fill_rows += fills_for(ep["episode"], ep["exit_reason"], fill_fees[i], events[0],
                                   prices[0])
        equity_rows = [{"day_index": i, "date": "2026-09-%02d" % (i + 1), "equity": equity[i],
                        "peak": peaks[i], "drawdown_usdt": dd[i], "drawdown_pct": ddp[i],
                        "in_window": True} for i in range(len(equity))]
        self.ledgers[grid] = {"fills.csv": (FILL_COLUMNS, fill_rows),
                              "episodes.csv": (EPISODE_COLUMNS, episodes),
                              "equity.csv": (EQUITY_COLUMNS, equity_rows)}
        return row

    def build_staging(self):
        self.staging = os.path.join(self.survivors, "evidence", ".staging-fixture")
        sid = None
        return sid

    def build_derived(self, leaderboard_entries):
        os.makedirs(self.survivors, exist_ok=True)
        index, problems = si.build(self.root)
        assert not problems, problems
        with open(os.path.join(self.survivors, "survivor-index.json"), "w") as fh:
            fh.write(json.dumps(index, indent=2, ensure_ascii=False) + "\n")
        self.entry = index["survivors"][0]
        doc, _rows, problems = sl.build(self.root)
        assert not problems, problems
        if not leaderboard_entries:
            doc["entries"] = []
            doc["top10"] = []
            doc["top10_count"] = 0
        with open(os.path.join(self.survivors, "leaderboard.json"), "w") as fh:
            fh.write(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
        with open(os.path.join(self.survivors, "leaderboard.csv"), "w") as fh:
            fh.write(sl.csv_text(_rows if leaderboard_entries else []))

    # -- the staging area a replay driver would have produced -------------------------------
    def stage_replay(self):
        sid = self.entry["survivor_id"]
        out = os.path.join(self.staging, sid)
        header = None
        rows = []
        for grid in GRIDS:
            grid_dir = os.path.join(out, "grids", grid)
            for name, (columns, body) in self.ledgers[grid].items():
                write_csv(os.path.join(grid_dir, name), columns, body)
            columns = list(self.grid_rows[grid].keys())
            header = header or columns
            rows.append(dict({"grid": grid}, **{k: self.grid_rows[grid][k] for k in columns}))
        write_csv(os.path.join(out, "replay_aggregate.csv"), ["grid"] + header, rows)
        dump(os.path.join(out, "replay.json"),
             {"schema_version": 1, "kind": "survivor_evidence_replay",
              "survivor_id": sid, "cohort": self.entry["cohort"], "symbol": SYMBOL,
              "timeframe": TIMEFRAME, "family_id": FAMILY, "round_id": ROUND, "run_id": RUN,
              "kanban_task_id": TASK, "params_sha256": self.entry["params_sha256"],
              "bundle_sha256": self.entry["bundle_sha256"],
              "bundle_identity_sha256": self.entry["bundle_identity_sha256"],
              "research_data_cutoff": self.entry["research_data_cutoff"],
              "source_data": {"attempt_dir": self.attempt,
                              "input_manifest_path": os.path.join(self.attempt, "artifacts",
                                                                  "input_manifest.json"),
                              "input_manifest_sha256":
                                  sha(os.path.join(self.attempt, "artifacts",
                                                   "input_manifest.json")),
                              "bins_build_path": os.path.join(self.attempt, "artifacts",
                                                              "bins_build.json"),
                              "bins_build_sha256": sha(os.path.join(self.attempt, "artifacts",
                                                                    "bins_build.json")),
                              "data": {"start": "2022-01-01", "end": "2026-09-10"}},
              "source_runner": {"path": "/scripts/20_strategy_a_run.py",
                                "sha256": "sha256:" + "c4" * 32},
              "replay_runner": {"path": "container/scripts/20_strategy_a_run.py",
                                "sha256": "sha256:" + "4a" * 32},
              "materialization_disclosure": "deterministic replay materialization, not the bytes "
                                            "the original run stored",
              "generated_at_utc": "2026-09-13T02:00:00Z"})
        return out

    def materialize(self, staging=None):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            rc = se.main(["materialize", "--results-root", self.root,
                          "--staging", staging or self.staging, "--json"])
        return rc, err.getvalue()

    def check(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            rc = se.main(["check", "--results-root", self.root, "--json"])
        return rc, err.getvalue()

    def coverage(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            rc = se.main(["coverage", "--results-root", self.root, "--json"])
        return rc, err.getvalue()

    def leaderboard_check(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            rc = sl.main(["leaderboard", "--results-root", self.root, "--check", "--json"])
        return rc, err.getvalue()


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v16-evidence-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def fixture(self, leaderboard_entries=1):
        return Fixture(self.tmp, leaderboard_entries)

    def test_materialize_publishes_and_check_passes(self):
        fx = self.fixture()
        fx.stage_replay()
        rc, err = fx.materialize()
        self.assertEqual(rc, 0, err)
        sid = fx.entry["survivor_id"]
        package = se.package_dir(self.tmp, sid)
        for name in ("manifest.json", "aggregate.csv"):
            self.assertTrue(os.path.isfile(os.path.join(package, name)), name)
        for grid in GRIDS:
            for name in ("fills.csv", "episodes.csv", "equity.csv", "summary.json"):
                self.assertTrue(os.path.isfile(os.path.join(package, "grids", grid, name)))
        rc, err = fx.check()
        self.assertEqual(rc, 0, err)
        rc, err = fx.coverage()
        self.assertEqual(rc, 0, err)

    def test_materialize_is_idempotent_and_refuses_a_different_identity(self):
        fx = self.fixture()
        fx.stage_replay()
        self.assertEqual(fx.materialize()[0], 0)
        rc, err = fx.materialize()
        self.assertEqual(rc, 0, err)  # re-publishing the same content is a no-op
        sid = fx.entry["survivor_id"]
        manifest_path = se.manifest_path(self.tmp, sid)
        with open(manifest_path) as fh:
            manifest = json.load(fh)
        first = manifest["package_identity_sha256"]
        manifest["probe"] = "different identity"
        manifest["package_identity_sha256"] = se.identity(manifest)
        self.assertNotEqual(manifest["package_identity_sha256"], first)
        files = {"grids": {}, "aggregate.csv": "",
                 MANIFEST_KEY: json.dumps(manifest, indent=2) + "\n", "grid_summaries": {}}
        with open(se.package_dir(self.tmp, sid) + "/" + se.MANIFEST_NAME, "w") as fh:
            fh.write(files[MANIFEST_KEY])
        rc, err = fx.materialize()
        self.assertEqual(rc, 1)
        self.assertIn("DIFFERENT identity", err)
        with open(manifest_path) as fh:
            self.assertEqual(json.load(fh)["probe"], "different identity")

    def test_ledger_tamper_is_detected(self):
        fx = self.fixture()
        fx.stage_replay()
        self.assertEqual(fx.materialize()[0], 0)
        sid = fx.entry["survivor_id"]
        target = os.path.join(se.package_dir(self.tmp, sid), "grids", "historical", "fills.csv")
        bump_csv_cell(target, 1, "price", 0.25)
        rc, err = fx.check()
        self.assertEqual(rc, 1)
        self.assertIn("tamper", err)

    def test_aggregate_tamper_is_detected(self):
        fx = self.fixture()
        fx.stage_replay()
        self.assertEqual(fx.materialize()[0], 0)
        sid = fx.entry["survivor_id"]
        target = os.path.join(se.package_dir(self.tmp, sid), "aggregate.csv")
        bump_csv_cell(target, 1, "net_pnl", 1.0)
        rc, err = fx.check()
        self.assertEqual(rc, 1)
        self.assertTrue("!=" in err or "recomputes" in err or "hash" in err, err)

    def test_ledger_values_that_do_not_reconcile_are_refused(self):
        fx = self.fixture()
        fx.stage_replay()
        sid = fx.entry["survivor_id"]
        episodes = os.path.join(fx.staging, sid, "grids", "historical", "episodes.csv")
        with open(episodes) as fh:
            text = fh.read()
        with open(episodes, "w") as fh:  # break the episode PnL identity on the last row only
            fh.write(text.replace("-40.0", "-45.0", 1))
        rc, err = fx.materialize()
        self.assertEqual(rc, 1)
        self.assertTrue("PnL identity" in err or "!=" in err, err)
        self.assertFalse(os.path.isdir(se.package_dir(self.tmp, sid)))

    def test_staging_outside_the_evidence_boundary_is_refused(self):
        fx = self.fixture()
        outside = os.path.join(self.tmp, "not-the-evidence-area", "sv-x")
        os.makedirs(outside)
        rc, err = fx.materialize(staging=os.path.join(self.tmp, "not-the-evidence-area"))
        self.assertEqual(rc, 1)
        self.assertIn("write boundary", err)

    def test_symlinked_reserved_root_is_refused(self):
        fx = self.fixture()
        fx.stage_replay()
        moved = os.path.join(self.tmp, "_survivors-real")
        shutil.move(fx.survivors, moved)
        os.symlink(moved, fx.survivors)
        rc, err = fx.materialize()
        self.assertEqual(rc, 1)
        self.assertTrue("symlink" in err or "resolved reserved" in err, err)
        self.assertFalse(os.path.isdir(os.path.join(moved, "evidence",
                                                    fx.entry["survivor_id"])))

    def test_only_leaderboard_entries_are_preserved(self):
        fx = self.fixture(leaderboard_entries=0)
        fx.stage_replay()
        rc, err = fx.materialize()
        self.assertEqual(rc, 1)
        self.assertIn("not a leaderboard entry", err)
        self.assertFalse(os.path.isdir(se.package_dir(self.tmp, fx.entry["survivor_id"])))

    def test_unknown_survivor_id_is_refused(self):
        fx = self.fixture()
        fx.stage_replay()
        os.makedirs(os.path.join(fx.staging, "sv-not-in-the-index"))
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            rc = se.main(["materialize", "--results-root", self.tmp, "--staging", fx.staging,
                          "--survivor-id", "sv-not-in-the-index", "--json"])
        self.assertEqual(rc, 1)
        self.assertIn("not a survivor of the durable index", err.getvalue())

    def test_coverage_is_non_gating_and_the_pointer_never_ranks(self):
        fx = self.fixture()
        with open(os.path.join(fx.survivors, "leaderboard.json")) as fh:
            before_json = json.load(fh)
        with open(os.path.join(fx.survivors, "leaderboard.csv")) as fh:
            before_csv = fh.read()
        rc, err = fx.coverage()
        self.assertEqual(rc, 1)  # no package yet: coverage reports the gap
        self.assertIn("no evidence package", err)
        rc, err = fx.leaderboard_check()
        self.assertEqual(rc, 0, err)  # ... and the leaderboard is still perfectly valid
        with open(os.path.join(fx.survivors, "leaderboard.csv")) as fh:
            self.assertEqual(fh.read(), before_csv)
        fx.stage_replay()
        self.assertEqual(fx.materialize()[0], 0)
        rc, err = fx.coverage()
        self.assertEqual(rc, 0, err)
        with open(os.path.join(fx.survivors, "leaderboard.json")) as fh:
            after = json.load(fh)
        for key in ("rank", "in_top10", "champion_candidate", "evidence_state", "survivor_id"):
            self.assertEqual([row[key] for row in before_json["entries"]],
                             [row[key] for row in after["entries"]], key)
        self.assertEqual(before_json["top10_count"], after["top10_count"])

    def test_manifest_makes_no_fake_original_run_claim(self):
        fx = self.fixture()
        fx.stage_replay()
        self.assertEqual(fx.materialize()[0], 0)
        sid = fx.entry["survivor_id"]
        package = se.package_dir(self.tmp, sid)
        with open(os.path.join(package, "manifest.json")) as fh:
            manifest = json.load(fh)
        disclosure = manifest["materialization"].lower()
        self.assertIn("replay", disclosure)
        self.assertIn("not the bytes", disclosure)
        self.assertEqual(manifest["contract"].split()[-1], "v1.6.0")
        self.assertEqual(manifest["aggregate"]["comparison"], "MATCH")
        self.assertEqual(manifest["aggregate"]["grids_compared"], len(GRIDS))
        self.assertEqual(manifest["source_runner"]["sha256"], "sha256:" + "c4" * 32)
        self.assertEqual(manifest["package_identity_sha256"], se.identity(manifest))
        # no copy of the frozen round, and no ledger under the research namespace
        self.assertFalse(os.path.exists(os.path.join(package, "attempts")))
        for grid in GRIDS:
            self.assertFalse(os.path.exists(os.path.join(package, "grids", grid,
                                                          "grid_%s.csv" % grid)))

    def test_frozen_grid_change_after_publication_is_detected(self):
        fx = self.fixture()
        fx.stage_replay()
        self.assertEqual(fx.materialize()[0], 0)
        grid_csv = os.path.join(fx.attempt, "artifacts", "grid_full.csv")
        bump_csv_cell(grid_csv, 1, "net_pnl", 1.0)
        rc, err = fx.check()
        self.assertEqual(rc, 1)
        self.assertTrue("frozen grid CSV" in err or "changed" in err, err)


MANIFEST_KEY = se.MANIFEST_NAME

if __name__ == "__main__":
    unittest.main(verbosity=2)
