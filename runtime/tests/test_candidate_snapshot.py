#!/usr/bin/env python3
"""Checks for runtime/candidate_snapshot.py (monitoring-only hourly snapshot).

These are simulation checks: the board read-backs are injected (`candidate_snapshot.card_status` /
`candidate_snapshot.board_counts`), so no real board is touched, and the fixture is a temp results
root.  They assert the properties that matter for the operator's hourly line: superseded attempts
never drive progress (contract 9.4 v1.7.1 selection reused), 0% / 100% boundaries, the 0..100 cap,
unavailable-data fallbacks, and that a snapshot writes nothing under the results root.

Run: python3 runtime/tests/test_candidate_snapshot.py     (stdlib unittest, no dependencies)
"""
import hashlib
import json
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


class Harness(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="qrp-snapshot-test-")
        self._real_card_status = snap.card_status
        self._real_board_counts = snap.board_counts
        snap.card_status = lambda board, task_id: ("scheduled", "stub")
        snap.board_counts = lambda board: (1, 0)

    def tearDown(self):
        snap.card_status = self._real_card_status
        snap.board_counts = self._real_board_counts
        shutil.rmtree(self.root, ignore_errors=True)

    # --- fixture helpers -------------------------------------------------
    def write(self, rel, doc):
        path = Path(self.root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(doc if isinstance(doc, str) else json.dumps(doc))
        return path

    def family(self, family="fam-a", created="2026-09-13T00:00:00Z", round_id="fam-a-r1"):
        self.write("%s/family.json" % family, {"family_id": family, "kanban_task_id": TASK,
                                               "kanban_board": BOARD, "created_at_utc": created})
        self.write("%s/rounds/%s/round-spec.json" % (family, round_id),
                   {"expected": {"expected_case_evaluations": EXPECTED}})
        return round_id

    def attempt(self, round_id, run_id, rows=0, created="2026-09-14T00:00:00Z", terminal=None,
                stage="RUNNING_QLIB"):
        base = "fam-a/rounds/%s/attempts/%s" % (round_id, run_id)
        self.write(base + "/run-spec.json", {"family_id": "fam-a", "round_id": round_id,
                                             "run_id": run_id, "task_id": TASK,
                                             "kanban_board": BOARD, "created_at_utc": created})
        self.write(base + "/state.json", {"stage": stage})
        if rows:
            grid = "symbol,timeframe,net_pnl\n" + "".join("SYM,5m,%d\n" % i for i in range(rows))
            self.write(base + "/artifacts/grid_full.csv", grid)
        if terminal:
            self.write(base + "/" + terminal, {"task_id": TASK})
        return base

    def snapshot(self):
        return snap.render(self.root)

    # --- progress semantics ----------------------------------------------
    def test_running_attempt_streams_rows_over_round_spec_total(self):
        round_id = self.family()
        self.attempt(round_id, "fam-a-r1-u1", rows=250)
        out = self.snapshot()
        self.assertIn("Progress:", out)
        self.assertIn("25.0% (250 / 1,000)", out)
        self.assertIn("Stage: RUNNING_QLIB", out)
        self.assertIn("Card: scheduled", out)
        self.assertIn("Blocked: 0 | Running: 1", out)

    def test_superseded_attempt_never_drives_progress(self):
        round_id = self.family()
        self.attempt(round_id, "fam-a-r1-u1", rows=900, terminal="DONE")
        self.attempt(round_id, "fam-a-r1-u2", rows=100, created="2026-09-14T01:00:00Z")
        out = self.snapshot()
        self.assertIn("10.0% (100 / 1,000)", out)   # the newer attempt, not the older DONE one
        self.assertIn("fam-a-r1-u2", out)

    def test_no_attempt_yet_is_zero_percent(self):
        self.family()
        out = self.snapshot()
        self.assertIn("0.0% (0 / 1,000)", out)
        self.assertIn("Stage: not launched", out)

    def test_terminal_done_is_hundred_percent(self):
        round_id = self.family()
        self.attempt(round_id, "fam-a-r1-u1", rows=400, terminal="DONE")
        out = self.snapshot()
        self.assertIn("100.0%", out)
        self.assertIn("Stage: DONE", out)

    def test_terminal_failure_is_not_completion(self):
        round_id = self.family()
        self.attempt(round_id, "fam-a-r1-u1", rows=400, terminal="FAILED", stage="FAILED_SCRIPT")
        out = self.snapshot()
        self.assertIn("0.0% (0 / 1,000)", out)
        self.assertIn("Stage: FAILED_SCRIPT", out)

    def test_progress_is_capped_at_hundred(self):
        round_id = self.family()
        self.attempt(round_id, "fam-a-r1-u1", rows=EXPECTED + 500)
        self.assertIn("100.0% (1,500 / 1,000)", self.snapshot())

    def test_missing_round_spec_total_is_unavailable(self):
        round_id = self.family()
        self.attempt(round_id, "fam-a-r1-u1", rows=7)
        (Path(self.root) / ("fam-a/rounds/%s/round-spec.json" % round_id)).unlink()
        out = self.snapshot()
        self.assertIn("Progress: unavailable", out)
        self.assertIn("expected total unavailable", out)

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
        self.assertIn("Leaderboard: unavailable", out)
        self.assertIn("Current: unavailable", out)

    # --- read-only invariant ---------------------------------------------
    def test_snapshot_writes_nothing_under_results(self):
        round_id = self.family()
        self.attempt(round_id, "fam-a-r1-u1", rows=30)
        before = self.digest()
        self.snapshot()
        self.assertEqual(before, self.digest())

    def digest(self):
        out = {}
        for path in sorted(Path(self.root).rglob("*")):
            if path.is_file():
                out[str(path.relative_to(self.root))] = hashlib.sha256(path.read_bytes()).hexdigest()
        return out


if __name__ == "__main__":
    unittest.main(verbosity=2)
