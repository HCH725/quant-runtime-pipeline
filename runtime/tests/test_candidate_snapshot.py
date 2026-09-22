#!/usr/bin/env python3
"""Checks for runtime/candidate_snapshot.py (monitoring-only hourly snapshot).

These are simulation checks: the board read-backs are injected (`candidate_snapshot.card_status` /
`candidate_snapshot.board_counts`) and the funnel inputs (`candidate_snapshot.REVIEW_STATE` /
`candidate_snapshot.INTAKE_OUTPUT`) point at temp paths, so neither a real board nor the canonical
intake state is touched.  They assert the properties that matter for the operator's hourly line:
section order, superseded attempts never driving progress or cohort (contract 9.4 v1.7.1 selection
reused), latest-observable cohort semantics, the 0% / 100% boundaries, the 0..100 cap, the funnel
counts with their unavailable fallbacks, and that a snapshot writes nothing at all.

Run: python3 runtime/tests/test_candidate_snapshot.py     (stdlib unittest, no dependencies)
"""
import contextlib
import datetime
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME))
import candidate_snapshot as snap  # noqa: E402

TASK = "t_SMOKE"
BOARD = "quant-strategy-research"
EXPECTED = 1000
STATE_NAME = "alpha-strategy-review-state.json"
WATCHDOG_NAME = "quant_runtime_watchdog.json"


class Harness(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="qrp-snapshot-test-")
        self.state_dir = tempfile.mkdtemp(prefix="qrp-snapshot-state-")
        self.out_dir = tempfile.mkdtemp(prefix="qrp-snapshot-out-")
        self._real_card_status = snap.card_status
        self._real_board_counts = snap.board_counts
        self._real_review_state = snap.REVIEW_STATE
        self._real_intake_output = snap.INTAKE_OUTPUT
        self._real_watchdog_state = snap.WATCHDOG_STATE
        snap.card_status = lambda board, task_id: ("scheduled", "stub")
        snap.board_counts = lambda board: (1, 0)
        snap.REVIEW_STATE = str(Path(self.state_dir) / STATE_NAME)
        snap.INTAKE_OUTPUT = str(Path(self.state_dir) / "intake-output")
        snap.WATCHDOG_STATE = Path(self.state_dir) / WATCHDOG_NAME
        self.watchdog_state()   # the watchdog's own state, healthy unless a test says otherwise

    def tearDown(self):
        snap.card_status = self._real_card_status
        snap.board_counts = self._real_board_counts
        snap.REVIEW_STATE = self._real_review_state
        snap.INTAKE_OUTPUT = self._real_intake_output
        snap.WATCHDOG_STATE = self._real_watchdog_state
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.state_dir, ignore_errors=True)
        shutil.rmtree(self.out_dir, ignore_errors=True)

    # --- fixture helpers -------------------------------------------------
    def write(self, rel, doc):
        path = Path(self.root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(doc if isinstance(doc, str) else json.dumps(doc))
        return path

    def family(self, family="fam-a", created="2026-09-13T00:00:00Z", round_id="fam-a-r1",
               expected=EXPECTED):
        self.write("%s/family.json" % family, {"family_id": family, "kanban_task_id": TASK,
                                               "kanban_board": BOARD, "created_at_utc": created})
        self.write("%s/rounds/%s/round-spec.json" % (family, round_id),
                   {"expected": {"expected_case_evaluations": expected}})
        return round_id

    def attempt(self, round_id, run_id, rows=0, created="2026-09-14T00:00:00Z", terminal=None,
                stage="RUNNING_QLIB", family="fam-a", symbol="SYM", timeframe="5m", progress=None):
        base = "%s/rounds/%s/attempts/%s" % (family, round_id, run_id)
        self.write(base + "/run-spec.json", {"family_id": family, "round_id": round_id,
                                             "run_id": run_id, "task_id": TASK,
                                             "kanban_board": BOARD, "created_at_utc": created})
        self.write(base + "/state.json", {"stage": stage})
        if rows:
            grid = "symbol,timeframe,net_pnl\n" + "".join("%s,%s,%d\n" % (symbol, timeframe, i)
                                                          for i in range(rows))
            self.write(base + "/artifacts/grid_full.csv", grid)
        if progress is not None:
            self.write(base + "/artifacts/progress.json", progress)
        if terminal:
            self.write(base + "/" + terminal, {"task_id": TASK})
        return base

    def review_state(self, buckets=None, ingested=None):
        self.write_state(STATE_NAME, {"current_snapshot": buckets or {},
                                      "ingested_wiki_records": ingested or []})

    def watchdog_state(self, active=None, checked="2026-09-17T09:40:38Z", healthy=None):
        """One watchdog state file, in `quant_runtime_watchdog.py`'s own schema-1 shape."""
        self.write_state(WATCHDOG_NAME, {"schema_version": 1, "last_check_at_utc": checked,
                                         "updated_at_utc": checked,
                                         "last_healthy_at_utc": (checked if healthy is None else healthy),
                                         "active_signatures": active or {}})

    def write_state(self, rel, doc):
        path = Path(self.state_dir) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(doc if isinstance(doc, str) else json.dumps(doc))
        return path

    def intake_report(self, hours_ago, text):
        """One durable intake run report, named the way the cron names it (local run time)."""
        name = (datetime.datetime.now() - datetime.timedelta(hours=hours_ago)).strftime(
            "%Y-%m-%d_%H-%M-%S") + ".md"
        path = Path(snap.INTAKE_OUTPUT) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Cron Job: Research Intake Review\n## Response\n%s\n" % text)
        return path

    def snapshot(self):
        return snap.render(self.root)

    # --- output shape ----------------------------------------------------
    def test_section_order_is_fixed(self):
        round_id = self.family()
        self.attempt(round_id, "fam-a-r1-u1", rows=10)
        self.review_state({"pass": ["p"]}, ["p"])
        self.write("_survivors/leaderboard.json", {"entries": [
            {"rank": 1, "cohort": "SYM/5m", "evidence_state": "FROZEN_ONLY",
             "full": {"sharpe": 1.0}}]})
        out = self.snapshot()
        order = [out.index(snap.TROPHY), out.index(snap.CURRENT), out.index(snap.FUNNEL),
                 out.index("Blocked: 0 | Running: 1")]
        self.assertEqual(order, sorted(order))
        self.assertEqual(out.splitlines()[0], snap.HEADER)

    # --- progress semantics ----------------------------------------------
    def test_running_attempt_streams_rows_over_round_spec_total(self):
        round_id = self.family()
        self.attempt(round_id, "fam-a-r1-u1", rows=250, symbol="BTCUSDT", timeframe="1h")
        out = self.snapshot()
        self.assertIn("Progress:", out)
        self.assertIn("25.0% (250 / 1,000)", out)
        self.assertNotIn("Stage:", out)   # the cohort line replaces the near-constant stage line
        self.assertIn("Cohort: BTCUSDT / 1h", out)
        self.assertIn("Card: scheduled", out)
        self.assertIn("Blocked: 0 | Running: 1", out)

    def test_superseded_attempt_never_drives_progress(self):
        round_id = self.family()
        self.attempt(round_id, "fam-a-r1-u1", rows=900, terminal="DONE", symbol="AAAUSDT")
        self.attempt(round_id, "fam-a-r1-u2", rows=100, created="2026-09-14T01:00:00Z",
                     symbol="BBBUSDT")
        out = self.snapshot()
        self.assertIn("10.0% (100 / 1,000)", out)   # the newer attempt, not the older DONE one
        self.assertIn("Cohort: BBBUSDT / 5m", out)  # cohort follows the same selection
        self.assertNotIn("AAAUSDT", out)

    def test_missing_attempt_timestamps_use_round_order_for_progress(self):
        # Legacy attempts can lack a parseable created_at_utc.  Across rounds, the newest round
        # remains the current snapshot; comparing the raw None values used to crash with TypeError.
        first_round = self.family(round_id="fam-a-r1")
        self.attempt(first_round, "fam-a-r1-u1", rows=900, created="", symbol="OLDUSDT")
        second_round = self.family(round_id="fam-a-r2")
        self.attempt(second_round, "fam-a-r2-u1", rows=100, created="", symbol="NEWUSDT")
        out = self.snapshot()
        self.assertIn("10.0% (100 / 1,000)", out)
        self.assertIn("Cohort: NEWUSDT / 5m", out)
        self.assertNotIn("OLDUSDT", out)

    def test_missing_attempt_timestamps_order_rounds_by_numeric_ordinal(self):
        first_round = self.family(round_id="fam-a-r9")
        self.attempt(first_round, "fam-a-r9-u1", rows=900, created="", symbol="OLDUSDT")
        second_round = self.family(round_id="fam-a-r10")
        self.attempt(second_round, "fam-a-r10-u1", rows=100, created="", symbol="NEWUSDT")
        out = self.snapshot()
        self.assertIn("10.0% (100 / 1,000)", out)
        self.assertIn("Cohort: NEWUSDT / 5m", out)
        self.assertNotIn("OLDUSDT", out)

    def test_equal_attempt_timestamps_order_rounds_by_numeric_ordinal(self):
        stamp = "2026-09-14T00:00:00Z"
        first_round = self.family(round_id="fam-a-r9")
        self.attempt(first_round, "fam-a-r9-u1", rows=900, created=stamp, symbol="OLDUSDT")
        second_round = self.family(round_id="fam-a-r10")
        self.attempt(second_round, "fam-a-r10-u1", rows=100, created=stamp, symbol="NEWUSDT")
        out = self.snapshot()
        self.assertIn("10.0% (100 / 1,000)", out)
        self.assertIn("Cohort: NEWUSDT / 5m", out)
        self.assertNotIn("OLDUSDT", out)

    def test_no_attempt_yet_is_zero_percent(self):
        self.family()
        out = self.snapshot()
        self.assertIn("0.0% (0 / 1,000)", out)
        self.assertNotIn("Stage:", out)   # the stage line is gone: cohort carries the observation
        self.assertIn("Cohort: unavailable", out)

    def test_terminal_done_is_hundred_percent(self):
        round_id = self.family()
        self.attempt(round_id, "fam-a-r1-u1", rows=400, terminal="DONE")
        out = self.snapshot()
        self.assertIn("100.0%", out)
        # a finished attempt has no observable cohort: never show its last (stale) grid row
        self.assertIn("Cohort: unavailable", out)

    def test_terminal_failure_is_not_completion(self):
        round_id = self.family()
        self.attempt(round_id, "fam-a-r1-u1", rows=400, terminal="FAILED", stage="FAILED_SCRIPT")
        out = self.snapshot()
        self.assertIn("0.0% (0 / 1,000)", out)
        self.assertNotIn("Stage:", out)
        self.assertIn("Cohort: unavailable", out)

    def test_progress_is_capped_at_hundred(self):
        round_id = self.family()
        self.attempt(round_id, "fam-a-r1-u1", rows=EXPECTED + 500)
        self.assertIn("100.0% (1,500 / 1,000)", self.snapshot())

    def test_published_cohort_counter_advances_progress_before_any_grid_exists(self):
        # Strategy F writes its phase grids only once every cohort is done and publishes
        # cohorts_done/cohorts_total after each one: 1/20 of the immutable 86,400 must read ~5%, not 0.
        round_id = self.family(expected=86400)
        self.attempt(round_id, "fam-a-r1-u1", rows=0,
                     progress={"cohorts_done": 1, "cohorts_total": 20})
        out = self.snapshot()
        self.assertIn("5.0% (4,320 / 86,400)", out)
        self.assertIn("Cohort: unavailable", out)   # no grid row has streamed yet

    def test_published_pair_counter_is_supported(self):
        # Strategy E publishes pairs_done/pairs_total instead of the cohort keys.
        round_id = self.family()
        self.attempt(round_id, "fam-a-r1-u1", rows=0, progress={"pairs_done": 1, "pairs_total": 4})
        self.assertIn("25.0% (250 / 1,000)", self.snapshot())

    def test_unusable_published_counter_falls_back_to_the_grid_rows(self):
        # done > total is not a legal counter, and a truncated file is not JSON: both keep the fallback.
        round_id = self.family()
        base = self.attempt(round_id, "fam-a-r1-u1", rows=7,
                            progress={"cohorts_done": 5, "cohorts_total": 2})
        self.assertIn("0.7% (7 / 1,000)", self.snapshot())
        self.write(base + "/artifacts/progress.json", "{not json")
        self.assertIn("0.7% (7 / 1,000)", self.snapshot())

    def test_legal_but_lagging_counter_never_lowers_the_streamed_rows(self):
        round_id = self.family()
        self.attempt(round_id, "fam-a-r1-u1", rows=300,
                     progress={"cohorts_done": 0, "cohorts_total": 20})
        self.assertIn("30.0% (300 / 1,000)", self.snapshot())

    def test_terminal_attempt_never_takes_progress_from_a_counter(self):
        # a terminal attempt is not running: a complete-looking counter must not report progress.
        round_id = self.family()
        self.attempt(round_id, "fam-a-r1-u1", rows=0, terminal="FAILED", stage="FAILED_SCRIPT",
                     progress={"cohorts_done": 20, "cohorts_total": 20})
        self.assertIn("0.0% (0 / 1,000)", self.snapshot())

    def test_missing_round_spec_total_is_unavailable(self):
        round_id = self.family()
        self.attempt(round_id, "fam-a-r1-u1", rows=7)
        (Path(self.root) / ("fam-a/rounds/%s/round-spec.json" % round_id)).unlink()
        out = self.snapshot()
        self.assertIn("Progress: unavailable", out)
        self.assertIn("expected total unavailable", out)

    def test_string_expected_round_spec_without_an_attempt_is_unavailable(self):
        # A prerequisite-gated round is a legal terminal TECHNICAL_INCOMPLETE: its round-spec registers
        # `expected` as a scalar ("not_computable") and launches no attempt.  That family has no
        # denominator, so the line reads unavailable - never a fabricated 0/total, and never a crash.
        self.write("fam-a/family.json", {"family_id": "fam-a", "kanban_task_id": TASK,
                                         "kanban_board": BOARD, "created_at_utc": "2026-09-13T00:00:00Z"})
        self.write("fam-a/rounds/fam-a-r1/round-spec.json", {"expected": "not_computable"})
        self.write("fam-a/rounds/fam-a-r1/verdict.json", {"verdict": "TECHNICAL_INCOMPLETE"})
        out = self.snapshot()
        self.assertIn("Progress: unavailable", out)
        self.assertIn("no round/attempt directory yet", out)
        self.assertNotIn("Progress: 0.0%", out)

    def test_string_expected_round_spec_behind_an_attempt_is_unavailable(self):
        # Same scalar `expected`, but the round does carry an authoritative attempt: the denominator is
        # still absent, and the note has to say so instead of taking `.get` off a string.
        round_id = self.family()
        self.write("fam-a/rounds/%s/round-spec.json" % round_id, {"expected": "not_computable"})
        self.attempt(round_id, "fam-a-r1-u1", rows=7)
        out = self.snapshot()
        self.assertIn("Progress: unavailable", out)
        self.assertIn("expected total unavailable", out)

    # --- cohort (latest observable grid row) ------------------------------
    def test_cohort_is_the_last_row_of_the_newest_streamed_grid(self):
        round_id = self.family()
        base = self.attempt(round_id, "fam-a-r1-u1", rows=3, symbol="OLDUSDT", timeframe="5m")
        stale = Path(self.root) / base / "artifacts" / "grid_full.csv"
        fresh = self.write(base + "/artifacts/grid_aaa.csv",
                           "symbol,timeframe,net_pnl\nNEWUSDT,15m,1\nNEWUSDT,15m,2\n")
        os.utime(stale, (1000, 1000))
        os.utime(fresh, (2000, 2000))          # newest by mtime, though not last alphabetically
        self.assertIn("Cohort: NEWUSDT / 15m", self.snapshot())

    def test_newer_grid_without_data_row_is_not_observable(self):
        round_id = self.family()
        base = self.attempt(round_id, "fam-a-r1-u1", rows=2, symbol="DATAROW")
        header_only = self.write(base + "/artifacts/grid_zzz.csv", "symbol,timeframe,net_pnl\n")
        os.utime(header_only, (4000000000, 4000000000))   # newest by mtime, but never streamed a row
        self.assertIn("Cohort: DATAROW / 5m", self.snapshot())

    def test_cohort_unavailable_without_any_grid(self):
        round_id = self.family()
        self.attempt(round_id, "fam-a-r1-u1", rows=0)
        self.assertIn("Cohort: unavailable", self.snapshot())

    # --- research funnel ---------------------------------------------------
    def test_wiki_counts_and_24h_delta(self):
        self.review_state({"pass": ["p1", "p2"], "pass_with_caveat": ["c1"], "remediate": ["r1"],
                           "reject": []}, ["p1", "c1", "c1"])   # wiki duplicates never double-count
        self.intake_report(5, "- \u2705 Ingested records: 31 total (+5 this run)")
        # the older report carries the other canonical ingestion line: both formats must still sum
        self.intake_report(20, "**State:** Checkpoint `x` | Ingested: 26 (+4) | Buckets: pass 1")
        self.intake_report(30, "- Ingested records: 17 total (+9 this run)")   # outside the window
        out = self.snapshot()
        self.assertIn("2 / 4 reviewed 50.0%", out)   # 4 buckets, 2 unique Wiki records
        self.assertIn("+9/24h", out)

    def test_delta_unavailable_when_a_report_lacks_the_canonical_line(self):
        self.review_state({"pass": ["p1"]}, ["p1"])
        self.intake_report(5, "- Ingested records: 31 total (+5 this run)")
        self.intake_report(7, "**Ingestion:** All 4 Wiki records written (263 total ingested).")
        self.assertIn("delta unavailable", self.snapshot())

    def test_funnel_and_delta_unavailable_without_inputs(self):
        out = self.snapshot()
        self.assertIn("Wiki Brain unavailable", out)
        self.assertIn("Backtested unavailable", out)

    def test_cumulative_workload_counts_actual_grid_rows_across_attempts(self):
        self.family(family="fam-a", round_id="fam-a-r1")
        self.attempt("fam-a-r1", "fam-a-r1-u1", rows=2)
        self.attempt("fam-a-r1", "fam-a-r1-u2", rows=3, created="2026-09-14T02:00:00Z")
        self.write("fam-a/rounds/fam-a-r1/attempts/fam-a-r1-u2/artifacts/grid_oos.csv",
                   "symbol,timeframe,net_pnl\nSYM,5m,1\n")
        self.write("fam-a/rounds/fam-a-r1/attempts/fam-a-r1-u2/artifacts/grid_empty.csv",
                   "symbol,timeframe,net_pnl\n")
        evaluations, artifacts = snap.cumulative_backtest_workload(self.root)
        self.assertEqual(evaluations, 6)
        self.assertEqual(artifacts, 4)
        doc = self.dashboard()
        self.assertEqual(doc["funnel"]["workload"]["evaluations"], 6)
        self.assertEqual(doc["funnel"]["workload"]["grid_artifacts"], 4)
        self.assertEqual(doc["funnel"]["workload"]["unit"], "streamed_grid_rows")

    def test_cumulative_workload_prefers_authoritative_result_total(self):
        self.family(family="fam-a", round_id="fam-a-r1")
        base = self.attempt("fam-a-r1", "fam-a-r1-u1", rows=2)
        self.write(base + "/result.json", {"case_evaluations_total": 17})
        evaluations, artifacts = snap.cumulative_backtest_workload(self.root)
        self.assertEqual((evaluations, artifacts), (17, 1))

    def test_cumulative_workload_zero_is_real_not_unavailable(self):
        self.family()
        evaluations, artifacts = snap.cumulative_backtest_workload(self.root)
        self.assertEqual((evaluations, artifacts), (0, 0))
        doc = self.dashboard()
        self.assertTrue(doc["funnel"]["workload"]["available"])
        self.assertEqual(doc["funnel"]["workload"]["evaluations"], 0)

    def test_backtested_counts_distinct_families_not_inflated_by_attempts(self):
        self.family(family="fam-a", round_id="fam-a-r1")
        self.family(family="fam-b", round_id="fam-b-r1")
        self.family(family="fam-c", round_id="fam-c-r1")
        self.write("_selftest/family.json", {"family_id": "_selftest"})   # bookkeeping, not registered
        self.attempt("fam-a-r1", "fam-a-r1-u1", rows=2)
        self.attempt("fam-a-r1", "fam-a-r1-u2", rows=2, created="2026-09-14T02:00:00Z")
        self.write("fam-a/rounds/fam-a-r1/attempts/fam-a-r1-u1/artifacts/grid_oos.csv",
                   "symbol,timeframe,net_pnl\nSYM,5m,1\n")
        no_rows = self.attempt("fam-b-r1", "fam-b-r1-u1", rows=0, family="fam-b")
        self.write(no_rows + "/artifacts/grid_full.csv", "symbol,timeframe,net_pnl\n")
        out = self.snapshot()
        self.assertIn("1 / 3 registered families 33.3%", out)   # 2 attempts x 2 grids = still fam-a

    # --- leaderboard + fallbacks -----------------------------------------
    def test_leaderboard_top_five_in_file_order(self):
        entries = [{"rank": i + 1, "cohort": "SYM%dm" % i, "evidence_state": "FROZEN_ONLY",
                    "full": {"sharpe": 1.0 + i, "max_dd_pct": -0.01 * i}} for i in range(6)]
        self.write("_survivors/leaderboard.json", {"entries": entries})
        out = self.snapshot()
        self.assertIn("1. SYM0m Sharpe 1.00", out)
        self.assertIn("5. SYM4m", out)
        self.assertNotIn("SYM5m", out)

    def test_no_leaderboard_entries_is_unavailable(self):
        out = self.snapshot()
        self.assertIn(snap.TROPHY + ": unavailable (no entries)", out)
        self.assertIn(snap.CURRENT + "\nunavailable", out)
        self.assertIn("Cohort: unavailable", out)

    # --- dashboard payload (Homepage JSON twin) ---------------------------
    def payload(self, now=None):
        return snap.dashboard_payload(self.root, now=now)

    def dashboard(self):
        """The payload as a reader sees it: through json round-trip, nothing held by reference."""
        return json.loads(json.dumps(self.payload(), ensure_ascii=False))

    def test_payload_carries_the_required_sections_and_the_text_numbers(self):
        round_id = self.family()
        self.attempt(round_id, "fam-a-r1-u1", rows=250, symbol="BTCUSDT", timeframe="1h")
        self.review_state({"pass": ["p1"]}, ["p1"])
        self.intake_report(5, "- Ingested records: 31 total (+5 this run)")
        self.write("_survivors/leaderboard.json", {"entries": [
            {"rank": 1, "cohort": "SYM/5m", "evidence_state": "FROZEN_ONLY",
             "full": {"sharpe": 1.0, "annualized_return": 0.1234, "avg_trades_per_year": 21.25, "max_dd_pct": -0.01}}]})
        text = self.snapshot()
        doc = self.dashboard()
        self.assertEqual(doc["schema_version"], snap.DASHBOARD_SCHEMA_VERSION)
        for key in ("generated_at_utc", "monitoring_only", "health", "sources", "current",
                    "leaderboard", "funnel", "agent"):
            self.assertIn(key, doc)
        # container / results-volume / cron re-checks are gone: health is the watchdog's, the board is
        # the snapshot's own read-back, and nothing else is monitored from here.
        self.assertNotIn("runtime", doc)
        cur = doc["current"]
        self.assertEqual(cur["family_id"], "fam-a")
        self.assertEqual((cur["round_id"], cur["attempt"], cur["stage"]),
                         (round_id, "fam-a-r1-u1", "RUNNING_QLIB"))
        self.assertEqual((cur["progress_pct"], cur["progress_done"], cur["progress_total"]),
                         (25.0, 250, 1000))
        self.assertEqual(cur["cohort"], "BTCUSDT / 1h")
        self.assertEqual(cur["kanban_task_id"], TASK)
        self.assertEqual(cur["board"], BOARD)
        self.assertEqual(cur["card_status"], "scheduled")
        self.assertEqual(doc["sources"]["results_root"], self.root)
        self.assertTrue(doc["sources"]["results_root_readable"])
        # the payload is a twin, not a second calculation: its numbers are the text's numbers
        self.assertIn(cur["progress_text"], text)
        self.assertIn(str(doc["funnel"]["wiki_brain"]["ingested"]), text)
        self.assertEqual(doc["funnel"]["wiki_brain"]["delta_24h"], 5)
        self.assertEqual(doc["funnel"]["wiki_brain"]["share_pct"], 100.0)
        self.assertEqual(doc["leaderboard"]["entries"][0]["annualized_return"], 0.1234)
        self.assertEqual(doc["leaderboard"]["entries"][0]["avg_trades_per_year"], 21.25)
        self.assertEqual(doc["leaderboard"]["entries"][0]["summary"],
                         "夏普 1.00 · 年化 12.34% · 最大回撤 -1.00% · 年均交易 21.2 次/年")
        self.assertIn("Sharpe 1.00", text)
        self.assertEqual(doc["agent"]["blocked"], 0)
        self.assertEqual(doc["agent"]["running"], 1)
        self.assertEqual(doc["health"]["status"], "ok")     # the watchdog holds no active signature
        self.assertEqual(doc["health"]["active"], [])
        self.assertEqual(doc["health"]["active_count"], 0)

    def test_dashboard_top_ten_is_independent_from_discord_top_five(self):
        entries = [{"rank": i + 1, "cohort": "SYM%dm" % i, "evidence_state": "FROZEN_ONLY",
                    "full": {"sharpe": 1.0 + i}} for i in range(11)]
        self.write("_survivors/leaderboard.json", {"entries": entries})
        doc = self.dashboard()
        text = self.snapshot()
        self.assertEqual(doc["leaderboard"]["count"], 11)
        self.assertEqual(doc["leaderboard"]["shown"], 10)
        self.assertEqual(doc["leaderboard"]["top_n"], 10)
        self.assertEqual([e["cohort"] for e in doc["leaderboard"]["entries"]],
                         ["SYM%dm" % i for i in range(10)])
        self.assertIn("5. SYM4m", text)
        self.assertNotIn("6. SYM5m", text)

    def test_unknown_values_are_null_never_zero(self):
        # An empty results root measures nothing: every unavailable field has to read null (the text
        # line says "unavailable"), never a fabricated 0.
        doc = self.dashboard()
        self.assertIsNone(doc["current"]["family_id"])
        self.assertIsNone(doc["current"]["stage"])
        self.assertIsNone(doc["current"]["progress_pct"])
        self.assertIsNone(doc["current"]["progress_done"])
        self.assertIsNone(doc["current"]["progress_total"])
        self.assertFalse(doc["current"]["progress_available"])
        self.assertIsNone(doc["current"]["cohort"])
        self.assertIsNone(doc["current"]["card_status"])
        self.assertFalse(doc["leaderboard"]["available"])
        self.assertEqual(doc["leaderboard"]["entries"], [])
        self.assertIsNone(doc["leaderboard"]["as_of_utc"])
        self.assertIsNone(doc["funnel"]["wiki_brain"]["reviewed"])
        self.assertIsNone(doc["funnel"]["wiki_brain"]["delta_24h"])
        self.assertFalse(doc["funnel"]["wiki_brain"]["available"])
        self.assertIsNone(doc["funnel"]["backtested"]["registered"])
        self.assertIsNone(doc["funnel"]["backtested"]["share_pct"])
        self.assertIn("unavailable", self.snapshot())

    def test_health_is_the_watchdog_state_passed_through(self):
        # The watchdog is the single health truth: no active signature is "ok", and the timestamps in
        # the payload are the watchdog's own - nothing here derives a status from results/board/container.
        self.watchdog_state(checked="2026-09-17T09:40:38Z", healthy="2026-09-17T09:10:00Z")
        doc = self.dashboard()
        self.assertTrue(doc["health"]["available"])
        self.assertEqual(doc["health"]["source"], "quant_runtime_watchdog")
        self.assertEqual(doc["health"]["state_path"], str(snap.WATCHDOG_STATE))
        self.assertEqual(doc["health"]["status"], "ok")
        self.assertEqual(doc["health"]["active"], [])
        self.assertEqual(doc["health"]["active_count"], 0)
        self.assertEqual(doc["health"]["last_check_at_utc"], "2026-09-17T09:40:38Z")
        self.assertEqual(doc["health"]["last_healthy_at_utc"], "2026-09-17T09:10:00Z")
        self.assertEqual(doc["health"]["summary"], "ok")

    def test_active_watchdog_signature_is_attention_and_the_signature_travels(self):
        # An active signature *is* the finding: it is passed through verbatim (so a future watchdog
        # check can never be dropped by a stale label table) with its kind relabelled for display.
        self.watchdog_state(active={
            "attempt|/runs/x|soft_stall": {"first_seen_utc": "2026-09-17T09:00:00Z"},
            "cron|f6b9aa5e9034|stale": {"first_seen_utc": "2026-09-17T09:05:00Z"},
            "future|check|brand_new": {"first_seen_utc": "2026-09-17T09:06:00Z"}})
        doc = self.dashboard()
        self.assertEqual(doc["health"]["status"], "attention")
        self.assertEqual(doc["health"]["active_count"], 3)
        self.assertEqual([item["signature"] for item in doc["health"]["active"]],
                         ["attempt|/runs/x|soft_stall", "cron|f6b9aa5e9034|stale",
                          "future|check|brand_new"])
        self.assertEqual([item["kind"] for item in doc["health"]["active"]],
                         ["soft_stall", "stale", None])
        self.assertEqual(doc["health"]["active"][0]["first_seen_utc"], "2026-09-17T09:00:00Z")
        self.assertEqual(doc["health"]["active"][2]["label"], "future|check|brand_new")
        self.assertIn("run stalled", doc["health"]["summary"])
        self.assertIn("quant cron job stale", doc["health"]["summary"])

    def test_unreadable_watchdog_state_is_unknown_never_ok(self):
        Path(snap.WATCHDOG_STATE).unlink()
        doc = self.dashboard()
        self.assertEqual(doc["health"]["status"], "unknown")
        self.assertFalse(doc["health"]["available"])
        self.assertIsNone(doc["health"]["active_count"])    # never a fabricated 0
        self.assertIsNone(doc["health"]["last_check_at_utc"])
        self.assertIn("unreadable", doc["health"]["summary"])

    def test_board_readback_failure_is_null_not_zero_and_never_moves_health(self):
        snap.board_counts = lambda board: (None, None)
        doc = self.dashboard()
        self.assertIsNone(doc["agent"]["running"])
        self.assertIsNone(doc["agent"]["blocked"])
        self.assertEqual(doc["agent"]["board_summary"], "unavailable")
        self.assertEqual(doc["health"]["status"], "ok")     # health is the watchdog's, not the board's

    def test_payload_write_is_atomic_and_outside_results(self):
        round_id = self.family()
        self.attempt(round_id, "fam-a-r1-u1", rows=30)
        before = self.digest()
        target = Path(self.out_dir) / "dashboard" / "dashboard.json"
        snap.write_json(target, self.payload(), self.root)
        self.assertEqual(before, self.digest())                       # /results untouched
        self.assertEqual(json.loads(target.read_text())["schema_version"], snap.DASHBOARD_SCHEMA_VERSION)
        self.assertEqual(sorted(p.name for p in target.parent.iterdir()), ["dashboard.json"])  # no temp left

    def test_write_json_refuses_a_target_inside_the_results_root(self):
        # "never under /results" is a boundary of the writer, not a promise of its caller: the target
        # is refused before anything is created (no file, no directory, no temp).
        round_id = self.family()
        self.attempt(round_id, "fam-a-r1-u1", rows=5)
        before = self.digest()
        for target in (Path(self.root) / "dashboard.json",
                       Path(self.root) / "fam-a" / "nested" / "dashboard.json",
                       Path(self.root) / ".." / Path(self.root).name / "dashboard.json"):
            with self.assertRaises(ValueError):
                snap.write_json(target, self.payload(), self.root)
        self.assertEqual(before, self.digest())
        self.assertFalse((Path(self.root) / "fam-a" / "nested").exists())

    def test_cli_refuses_a_results_path_and_keeps_stdout_and_exit_code(self):
        # Same guard through the CLI: rc=0 and byte-identical stdout (the Discord line is untouched),
        # only a stderr note, and the results tree is bit-for-bit what it was.
        round_id = self.family()
        self.attempt(round_id, "fam-a-r1-u1", rows=250)
        target = Path(self.root) / "dashboard.json"
        before = self.digest()
        argv, env = sys.argv, os.environ.get("QLIB_RESULTS_ROOT")
        sys.argv = ["candidate_snapshot.py", "--dashboard-json", str(target)]
        os.environ["QLIB_RESULTS_ROOT"] = self.root
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = snap.main()
        finally:
            sys.argv = argv
            if env is None:
                os.environ.pop("QLIB_RESULTS_ROOT", None)
            else:
                os.environ["QLIB_RESULTS_ROOT"] = env
        self.assertEqual(rc, 0)
        self.assertEqual(out.getvalue(), self.snapshot() + "\n")
        self.assertIn("refusing to write inside the results root", err.getvalue())
        self.assertFalse(target.exists())
        self.assertEqual(before, self.digest())

    def test_cli_writes_the_dashboard_file_and_keeps_stdout_identical(self):
        round_id = self.family()
        self.attempt(round_id, "fam-a-r1-u1", rows=250)
        target = Path(self.state_dir) / "dashboard.json"
        argv, env = sys.argv, os.environ.get("QLIB_RESULTS_ROOT")
        sys.argv = ["candidate_snapshot.py", "--dashboard-json", str(target)]
        os.environ["QLIB_RESULTS_ROOT"] = self.root
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                rc = snap.main()
        finally:
            sys.argv = argv
            if env is None:
                os.environ.pop("QLIB_RESULTS_ROOT", None)
            else:
                os.environ["QLIB_RESULTS_ROOT"] = env
        self.assertEqual(rc, 0)
        self.assertEqual(out.getvalue(), self.snapshot() + "\n")      # the Discord payload is unchanged
        self.assertEqual(json.loads(target.read_text())["current"]["progress_pct"], 25.0)

    def test_cli_rejects_unknown_arguments(self):
        argv = sys.argv
        sys.argv = ["candidate_snapshot.py", "--nope"]
        try:
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                snap.main()
        finally:
            sys.argv = argv

    def test_snapshot_writes_nothing_under_results(self):
        round_id = self.family()
        self.attempt(round_id, "fam-a-r1-u1", rows=30)
        self.review_state({"pass": ["p1"]}, ["p1"])
        self.intake_report(5, "- Ingested records: 26 total (+4 this run)")
        before = self.digest()
        self.snapshot()
        self.payload()
        self.assertEqual(before, self.digest())

    def digest(self):
        out = {}
        for root in (self.root, self.state_dir):
            for path in sorted(Path(root).rglob("*")):
                if path.is_file():
                    out[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        return out


if __name__ == "__main__":
    unittest.main(verbosity=2)
