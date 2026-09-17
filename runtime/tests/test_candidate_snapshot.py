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
import datetime
import hashlib
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


class Harness(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="qrp-snapshot-test-")
        self.state_dir = tempfile.mkdtemp(prefix="qrp-snapshot-state-")
        self._real_card_status = snap.card_status
        self._real_board_counts = snap.board_counts
        self._real_review_state = snap.REVIEW_STATE
        self._real_intake_output = snap.INTAKE_OUTPUT
        snap.card_status = lambda board, task_id: ("scheduled", "stub")
        snap.board_counts = lambda board: (1, 0)
        snap.REVIEW_STATE = str(Path(self.state_dir) / STATE_NAME)
        snap.INTAKE_OUTPUT = str(Path(self.state_dir) / "intake-output")

    def tearDown(self):
        snap.card_status = self._real_card_status
        snap.board_counts = self._real_board_counts
        snap.REVIEW_STATE = self._real_review_state
        snap.INTAKE_OUTPUT = self._real_intake_output
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.state_dir, ignore_errors=True)

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

    # --- read-only invariant ---------------------------------------------
    def test_snapshot_writes_nothing_under_results(self):
        round_id = self.family()
        self.attempt(round_id, "fam-a-r1-u1", rows=30)
        self.review_state({"pass": ["p1"]}, ["p1"])
        self.intake_report(5, "- Ingested records: 26 total (+4 this run)")
        before = self.digest()
        self.snapshot()
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
