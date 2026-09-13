#!/usr/bin/env python3
"""Logic-layer checks for the v1.5.0 post-survivor lifecycle.

Covers `runtime/survivor_index.py` (file-only survivor index, contract 27.2), the forward
evidence ingestion in `runtime/survivor_leaderboard.py` (contract 27.3 / 27.4) and the
leaderboard (contract 27.5).  stdlib unittest only; no market data, no container, no Qlib and no
/results - every case builds a throwaway results root in a temp dir.

The checks pin the properties that make the layer honest:

  * the index is a deterministic rebuild of the frozen bundles, and a survivor_id pins family,
    round, run, bundle identity, cohort and the frozen strategy + DCA cell,
  * fail-closed: missing checksum, invalid bundle identity, a source that disagrees with the
    bundle (directory name, kanban_task_id, round-spec checksum), a param cell outside the
    registered axes, and a duplicate survivor_id all refuse to index,
  * forward evidence is strictly post-freeze, never overlaps or repeats a recorded slice, and
    never carries params other than the incumbent's (a retune is a challenger, not evidence),
  * a challenger's OOS start must be after its own preregistration cutoff, and a retune never
    rewrites the incumbent: both survivors coexist with their own params,
  * the leaderboard ordering is total and reproducible (forward evidence outranks the frozen
    fallback, NULLS LAST, lexical survivor_id last), the Top-10 is capped at 10, and a
    FROZEN_ONLY survivor reports no forward metrics at all (no invented evidence),
  * the frozen bundle and the index are never rewritten by ranking.
"""
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))  # runtime/tests -> runtime -> repo
RUNTIME = os.path.join(REPO, "runtime")
INDEX_CLI = os.path.join(RUNTIME, "survivor_index.py")
LEADERBOARD_CLI = os.path.join(RUNTIME, "survivor_leaderboard.py")
sys.path.insert(0, RUNTIME)

import survivor_index as si  # noqa: E402
import survivor_leaderboard as sl  # noqa: E402
from survivor_bundle import identity as bundle_identity  # noqa: E402

A = "BTCUSDT/1h"
B = "SOLUSDT/4h"

# The Strategy A v2 r1 frozen cell shape each cohort survivor carries.
DCA = {"spacing_pct": 0.01, "size_multiplier": 1.0, "breakeven_tp_pct": 0.01,
       "invalidation_pct": 0.1}


def survivor(cohort, window=20, discount=0.03, dca=None, oos=(1.0, 100.0, 40),
             full=(1.0, 200.0), robustness=(100.0, 90.0, 80.0, 70.0), same_sign=0.8):
    dca = dict(DCA) if dca is None else dca
    return {
        "cohort": cohort, "outcome": "SURVIVOR", "no_winner_reason": None, "cull_reasons": [],
        "winner": {"window": window, "discount": discount, **dca},
        "neighbourhood": {"neighbours": 6, "agreeing": 5, "same_sign_fraction": same_sign,
                          "passed": same_sign >= 0.6},
        "metrics": {
            "historical": {"net_pnl": 300.0, "sharpe": 1.2, "episodes": 100, "max_dd_pct": -0.1},
            "oos": {"net_pnl": oos[1], "sharpe": oos[0], "episodes": oos[2], "max_dd_pct": -0.08},
            "full": {"net_pnl": full[1], "sharpe": full[0], "episodes": 140, "max_dd_pct": -0.12},
            "robustness": {g: {"net_pnl": value, "sharpe": 0.5, "max_dd_pct": -0.15}
                           for g, value in zip(si.ROBUSTNESS_GRIDS, robustness)},
        },
    }


def a_v2_like_bundle():
    """The evidence shape of the real A v2 r1 bundle: 2 survivors, SOLUSDT/4h stronger."""
    return [
        survivor(A, window=20, discount=0.03, oos=(0.536464, 6016.344052, 34),
                 full=(0.588432, 14913.47311), robustness=(11353.7, 14701.4, 12210.2, 14876.0),
                 same_sign=0.833333),
        survivor(B, window=100, discount=0.03, dca=dict(DCA, size_multiplier=1.1),
                 oos=(2.17438, 8485.605171, 41), full=(4.396559, 60695.711357),
                 robustness=(53716.3, 60649.5, 17375.4, 62226.3), same_sign=0.857143),
    ]


def write_json(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(doc, fh, indent=2, sort_keys=False)
    return path


def make_family(root, family_id, survivors, round_id=None, run_id=None, data_end="2026-09-10",
                oos_start="2025-10-01", created="2026-09-13T00:44:48Z", challenger_of=None,
                task_id="t_test", mutate=None, name_mismatch=False):
    """One family + one round + one frozen survivor bundle, in throwaway-results-root shape."""
    round_id = round_id or family_id + "-r1"
    run_id = run_id or round_id + "-u1"
    fam_dir = os.path.join(root, family_id)
    round_dir = os.path.join(fam_dir, "rounds", round_id)
    spec_path = write_json(os.path.join(round_dir, "round-spec.json"), {
        "schema_version": 1, "family_id": family_id, "round_id": round_id,
        "contract": "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md v1.3.2",
        "data": {"data_start": "2022-01-01", "data_end": data_end,
                 "historical_end": "2025-09-30", "oos_start": oos_start, "oos_end": data_end},
    })
    write_json(os.path.join(fam_dir, "family.json"), {
        "schema_version": 1, "family_id": family_id,
        "kanban_task_id": "t_mismatched_family" if name_mismatch else task_id,
        "challenger_of": challenger_of, "created_at_utc": created,
    })
    bundle = {
        "schema_version": 1, "kind": "frozen_survivor_bundle",
        "contract": "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md v1.5.0",
        "contract_section": "7.3 / 10.8",
        "family_id": family_id, "round_id": round_id, "run_id": run_id,
        "kanban_task_id": task_id, "kanban_board": "quant-strategy-research",
        "source_attempt_dir": os.path.join(round_dir, "attempts", run_id),
        "survivor_count": len(survivors), "disposition_band": "SURVIVOR_FOUND" if len(survivors) == 1
        else "MULTIPLE_SURVIVORS",
        "verdict": "PASS" if survivors else "REJECT",
        "all_survivors_advance": True, "ranking": None,
        "source_verdict_not_rewritten": True,
        "survivors": survivors,
        "source_artifacts": {"round-spec.json": si.sha256_file(spec_path),
                             "DONE": "sha256:" + "d" * 64},
        "generator": {"path": "runtime/survivor_bundle.py", "sha256": "sha256:" + "e" * 64},
        "generated_at_utc": "2026-09-13T01:55:50Z",
    }
    bundle["bundle_identity_sha256"] = bundle_identity(bundle)
    if mutate:
        bundle = mutate(bundle)
    return write_json(os.path.join(round_dir, "survivor-bundle.json"), bundle)


def slice_doc(entry, data_start, data_end, episodes=50, net_pnl=100.0, sharpe=1.0,
              max_dd_pct=-0.02, return_pct=1.0):
    return {"survivor_id": entry["survivor_id"],
            "bundle_identity_sha256": entry["bundle_identity_sha256"],
            "params_sha256": entry["params_sha256"], "data_start": data_start,
            "data_end": data_end, "data_snapshot": "raw store snapshot 2026-10-05",
            "execution_semantics": "qlib container 20_strategy_a_run.py semantics",
            "cost_model": "taker 5bp, 1 tick baseline slippage, official funding",
            "episodes": episodes, "net_pnl": net_pnl, "return_pct": return_pct, "sharpe": sharpe,
            "max_dd_pct": max_dd_pct, "fees": 10.0, "funding": 1.0, "slippage_ticks": 1,
            "produced_at_utc": "2026-10-06T00:00:00Z"}


class Base(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="post-survivor-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def run_cli(self, script, *args):
        # the sub-command CLIs declare --results-root on the sub-parser, so it goes last
        return subprocess.run([sys.executable, script] + list(args)
                              + ["--results-root", self.root],
                              capture_output=True, text=True)

    def entries(self):
        index, problems = si.build(self.root)
        self.assertEqual(problems, [], problems)
        return index["survivors"]

    def entry_for(self, cohort, family_id=None):
        matches = [e for e in self.entries()
                   if e["cohort"] == cohort and (family_id is None or e["family_id"] == family_id)]
        self.assertEqual(len(matches), 1, matches)
        return matches[0]

    def write_slice(self, name, doc):
        return write_json(os.path.join(self.root, "slices", name), doc)


class TestSurvivorIndex(Base):

    def test_index_rebuild_is_deterministic_and_pins_the_identity_fields(self):
        make_family(self.root, "fam-a", a_v2_like_bundle())
        first, problems = si.build(self.root)
        self.assertEqual(problems, [])
        second, _ = si.build(self.root)
        self.assertEqual(si.measured(first), si.measured(second),
                         "a rebuild must reproduce the index byte for byte, clock aside")
        self.assertEqual(first["contract"], "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md "
                                            "v1.5.0")
        self.assertEqual(sorted(s["cohort"] for s in first["survivors"]), sorted([A, B]))
        self.assertEqual([s["survivor_id"] for s in first["survivors"]],
                         sorted(s["survivor_id"] for s in first["survivors"]),
                         "survivors are ordered by survivor_id, deterministically")
        self.assertEqual(first["survivor_count"], 2)
        entry = self.entry_for(B)
        for field in ("family_id", "round_id", "run_id", "bundle_identity_sha256", "cohort"):
            self.assertTrue(entry[field], field)
        self.assertEqual(entry["strategy_params"], {"window": 100, "discount": 0.03})
        self.assertEqual(entry["dca_params"], dict(DCA, size_multiplier=1.1))
        self.assertEqual(entry["params_sha256"], si.digest(
            {"strategy_params": {"window": 100, "discount": 0.03},
             "dca_params": dict(DCA, size_multiplier=1.1)}))
        # the survivor_id is exactly the digest of the pinned identity, not of the file path
        payload = si.identity_payload("fam-a", "fam-a-r1", "fam-a-r1-u1",
                                      entry["bundle_identity_sha256"], B,
                                      entry["strategy_params"], entry["dca_params"])
        self.assertEqual(entry["survivor_id"], si.survivor_id(payload))
        # the stress floor is the worst of the four reruns, and names its grid
        self.assertEqual(entry["robustness_stress_floor_net_pnl"], 17375.4)
        self.assertEqual(entry["robustness_stress_floor_grid"], "entry_delay_1_bar")

    def test_seed_shape_yields_exactly_the_two_frozen_survivors(self):
        make_family(self.root, "close-vs-sma-mean-reversion-long-flat-v2", a_v2_like_bundle())
        index, problems = si.build(self.root)
        self.assertEqual(problems, [])
        self.assertEqual(index["survivor_count"], 2)
        self.assertEqual(sorted(e["cohort"] for e in index["survivors"]), sorted([A, B]))
        self.assertEqual(len({e["survivor_id"] for e in index["survivors"]}), 2)
        self.assertEqual({e["research_data_cutoff"] for e in index["survivors"]}, {"2026-09-10"})
        self.assertEqual(index["skipped_bundles"], [])

    def test_duplicate_survivor_id_is_refused(self):
        path = make_family(self.root, "fam-a", a_v2_like_bundle())
        _, problems = si.build(self.root, bundle_list=[path, path])
        self.assertTrue(any("duplicate survivor_id" in p for p in problems), problems)

        other = tempfile.mkdtemp(prefix="dup-")
        try:
            doubled = make_family(other, "fam-b", [survivor(A), survivor(A)])
            _, problems = si.build(other, bundle_list=[doubled])
            self.assertTrue(any("duplicate survivor_id" in p for p in problems), problems)
        finally:
            shutil.rmtree(other, ignore_errors=True)

    def test_missing_checksum_refuses_to_index(self):
        make_family(self.root, "fam-a", a_v2_like_bundle(),
                    mutate=lambda b: dict(b, bundle_identity_sha256=None))
        index, problems = si.build(self.root)
        self.assertIsNone(index)
        self.assertTrue(any("missing checksum" in p for p in problems), problems)

    def test_invalid_bundle_identity_refuses_to_index(self):
        make_family(self.root, "fam-a", a_v2_like_bundle(),
                    mutate=lambda b: dict(b, verdict="REJECT"))  # stale published identity
        index, problems = si.build(self.root)
        self.assertIsNone(index)
        self.assertTrue(any("bundle is invalid" in p for p in problems), problems)

    def test_source_inconsistency_refuses_to_index(self):
        # (a) the directory a bundle sits in is part of its identity
        make_family(self.root, "fam-a", a_v2_like_bundle(),
                    mutate=lambda b: dict(b, family_id="somewhere-else"))
        _, problems = si.build(self.root)
        self.assertTrue(any("disagrees with its directory name" in p for p in problems), problems)

        # (b) the round-spec checksum the bundle froze no longer matches the file on disk
        root2 = tempfile.mkdtemp(prefix="drift-")
        try:
            path = make_family(root2, "fam-a", a_v2_like_bundle())
            spec_path = os.path.join(os.path.dirname(path), "round-spec.json")
            with open(spec_path) as fh:
                spec = json.load(fh)
            spec["data"]["data_end"] = "2026-09-30"
            write_json(spec_path, spec)
            _, problems = si.build(root2)
            self.assertTrue(any("does not hash to the checksum" in p for p in problems), problems)
        finally:
            shutil.rmtree(root2, ignore_errors=True)

        # (c) the bundle's kanban_task_id must agree with the family's ownership record
        root3 = tempfile.mkdtemp(prefix="taskid-")
        try:
            make_family(root3, "fam-a", a_v2_like_bundle(), name_mismatch=True)
            _, problems = si.build(root3)
            self.assertTrue(any("kanban_task_id mismatch" in p for p in problems), problems)
        finally:
            shutil.rmtree(root3, ignore_errors=True)

    def test_unknown_or_missing_param_axis_refuses_to_index(self):
        broken = survivor(A)
        del broken["winner"]["discount"]
        make_family(self.root, "fam-a", [broken])
        _, problems = si.build(self.root)
        self.assertTrue(any("missing registered param axis" in p for p in problems), problems)

        root2 = tempfile.mkdtemp(prefix="extra-axis-")
        try:
            widened = survivor(A)
            widened["winner"]["leverage"] = 3.0
            make_family(root2, "fam-a", [widened])
            _, problems = si.build(root2)
            self.assertTrue(any("outside the registered axes" in p for p in problems), problems)
        finally:
            shutil.rmtree(root2, ignore_errors=True)

    def test_index_cli_writes_checks_and_detects_drift(self):
        make_family(self.root, "fam-a", a_v2_like_bundle())
        written = self.run_cli(INDEX_CLI, "--json")
        self.assertEqual(written.returncode, 0, written.stderr)
        payload = json.loads(written.stdout)
        self.assertEqual(payload["result"], "written")
        self.assertEqual(payload["survivor_count"], 2)
        self.assertTrue(os.path.isfile(si.index_path(self.root)))
        again = self.run_cli(INDEX_CLI)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("result=unchanged", again.stdout)
        check = self.run_cli(INDEX_CLI, "--check")
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertIn("result=check_clean", check.stdout)

        # a new bundle the on-disk index does not know about is drift, not a silent re-rank
        make_family(self.root, "fam-b", [survivor("ETHUSDT/5m")], data_end="2026-09-20")
        drifted = self.run_cli(INDEX_CLI, "--check")
        self.assertEqual(drifted.returncode, 1)
        self.assertIn("derived artifact", drifted.stderr)

    def test_challenger_oos_start_must_follow_its_preregistration(self):
        make_family(self.root, "fam-challenger", [survivor(A)], challenger_of="sv-incumbent",
                    created="2026-09-20T00:00:00Z", oos_start="2025-10-01")
        _, problems = si.build(self.root)
        self.assertTrue(any("not after the challenger's preregistration cutoff" in p
                            for p in problems), problems)

        root2 = tempfile.mkdtemp(prefix="challenger-ok-")
        try:
            make_family(root2, "fam-challenger", [survivor(A)], challenger_of="sv-incumbent",
                        created="2026-09-20T00:00:00Z", oos_start="2026-10-01")
            index, problems = si.build(root2)
            self.assertEqual(problems, [])
            self.assertEqual(index["survivors"][0]["challenger_of"], "sv-incumbent")
        finally:
            shutil.rmtree(root2, ignore_errors=True)


class TestForwardEvidence(Base):

    def setUp(self):
        super().setUp()
        make_family(self.root, "fam-a", a_v2_like_bundle())
        self.entry = self.entry_for(B)

    def test_slice_must_be_strictly_post_freeze(self):
        doc = slice_doc(self.entry, "2026-09-10", "2026-09-20")
        self.assertTrue(any("not later than the survivor's research data cutoff" in p
                            for p in sl.slice_problems(doc, self.entry, [])))
        doc = slice_doc(self.entry, "2026-08-01", "2026-09-11")
        self.assertTrue(any("not later than" in p for p in sl.slice_problems(doc, self.entry, [])))
        doc = slice_doc(self.entry, "2026-09-11", "2026-09-20")
        self.assertEqual(sl.slice_problems(doc, self.entry, []), [])

    def test_overlapping_or_repeated_slice_is_refused(self):
        prior = slice_doc(self.entry, "2026-09-11", "2026-09-20")
        for start, end in (("2026-09-15", "2026-09-25"), ("2026-09-11", "2026-09-20"),
                           ("2026-09-01", "2026-09-12")):
            later = slice_doc(self.entry, start, end)
            self.assertTrue(any("overlaps the recorded slice" in p
                                for p in sl.slice_problems(later, self.entry, [prior])),
                            (start, end))
        clean = slice_doc(self.entry, "2026-09-21", "2026-09-30")
        self.assertEqual(sl.slice_problems(clean, self.entry, [prior]), [])

    def test_param_or_bundle_identity_mismatch_is_refused(self):
        retuned = slice_doc(self.entry, "2026-09-11", "2026-09-20")
        retuned["params_sha256"] = "sha256:" + "0" * 64
        self.assertTrue(any("may never be filed as evidence" in p
                            for p in sl.slice_problems(retuned, self.entry, [])))
        foreign = slice_doc(self.entry, "2026-09-11", "2026-09-20")
        foreign["bundle_identity_sha256"] = "sha256:" + "1" * 64
        self.assertTrue(any("is not the survivor's frozen bundle" in p
                            for p in sl.slice_problems(foreign, self.entry, [])))
        wrong_target = slice_doc(self.entry, "2026-09-11", "2026-09-20")
        wrong_target["survivor_id"] = "sv-someone-else"
        self.assertTrue(any("is not the target survivor" in p
                            for p in sl.slice_problems(wrong_target, self.entry, [])))

    def test_missing_slice_field_is_refused(self):
        for field in sorted(sl.SLICE_REQUIRED):
            doc = slice_doc(self.entry, "2026-09-11", "2026-09-20")
            del doc[field]
            self.assertTrue(sl.slice_problems(doc, self.entry, []), field)
        no_episodes = slice_doc(self.entry, "2026-09-11", "2026-09-20", episodes=0)
        self.assertTrue(any("positive integer" in p
                            for p in sl.slice_problems(no_episodes, self.entry, [])))

    def test_cli_appends_reads_back_and_refuses_a_second_identical_slice(self):
        first = slice_doc(self.entry, "2026-09-11", "2026-09-20")
        path = self.write_slice("one.json", first)
        proc = self.run_cli(LEADERBOARD_CLI, "forward", "--survivor-id",
                            self.entry["survivor_id"], "--slice", path, "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["result"], "appended")
        record = json.load(open(si.forward_path(self.root, self.entry["survivor_id"])))
        self.assertEqual({k: record[k] for k in first}, first,
                         "the slice is stored verbatim (only a slice schema version is added)")
        self.assertEqual(record["slice_schema_version"], 1)

        again = self.run_cli(LEADERBOARD_CLI, "forward", "--survivor-id",
                             self.entry["survivor_id"], "--slice", path)
        self.assertEqual(again.returncode, 1)
        self.assertIn("overlaps the recorded slice", again.stderr)

        second = slice_doc(self.entry, "2026-09-21", "2026-09-30", episodes=5, net_pnl=10.0)
        path2 = self.write_slice("two.json", second)
        proc = self.run_cli(LEADERBOARD_CLI, "forward", "--survivor-id",
                            self.entry["survivor_id"], "--slice", path2, "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["slice_count"], 2)
        with open(si.forward_path(self.root, self.entry["survivor_id"])) as fh:
            lines = [json.loads(line) for line in fh if line.strip()]
        self.assertEqual(len(lines), 2, "forward evidence is append-only")

    def test_unknown_survivor_id_is_refused(self):
        path = self.write_slice("one.json", slice_doc(self.entry, "2026-09-11", "2026-09-20"))
        proc = self.run_cli(LEADERBOARD_CLI, "forward", "--survivor-id", "sv-nope",
                            "--slice", path)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("matches 0 indexed survivors", proc.stderr)


class TestLeaderboard(Base):

    def forward(self, cohort, *slices):
        entry = self.entry_for(cohort)
        for index, doc in enumerate(slices):
            path = self.write_slice("%s-%d.json" % (cohort.replace("/", "-"), index),
                                    slice_doc(entry, *doc))
            proc = self.run_cli(LEADERBOARD_CLI, "forward", "--survivor-id",
                                entry["survivor_id"], "--slice", path)
            self.assertEqual(proc.returncode, 0, proc.stderr)
        return entry

    def board(self):
        doc, rows, problems = sl.build(self.root)
        self.assertEqual(problems, [], problems)
        return doc, rows

    def test_seed_is_frozen_only_and_reports_no_invented_forward_metrics(self):
        make_family(self.root, "close-vs-sma-mean-reversion-long-flat-v2", a_v2_like_bundle())
        doc, rows = self.board()
        self.assertEqual(doc["survivor_count"], 2)
        for row in rows:
            self.assertEqual(row["evidence_state"], "FROZEN_ONLY")
            self.assertFalse(row["forward"]["has_forward"])
            self.assertIsNone(row["forward"].get("sharpe"))
            self.assertIsNone(row["last_evidence_end"])
            self.assertFalse(row["champion_candidate"])
        self.assertEqual({row["evidence_state"] for row in rows}, {"FROZEN_ONLY"})

    def test_frozen_fallback_ranking_comes_from_evidence_not_from_names(self):
        make_family(self.root, "fam-a", a_v2_like_bundle())
        _, rows = self.board()
        self.assertEqual([row["cohort"] for row in rows], [B, A],
                         "without forward evidence the frozen OOS sharpe decides: SOLUSDT/4h "
                         "(2.17) ranks above BTCUSDT/1h (0.54)")
        self.assertEqual([row["rank"] for row in rows], [1, 2])

    def test_forward_evidence_outranks_the_frozen_fallback(self):
        make_family(self.root, "fam-a", a_v2_like_bundle())
        before = self.entry_for(B)
        self.forward(A, ("2026-09-11", "2026-09-20", 30, 500.0, 0.9, -0.01, 2.0))
        _, rows = self.board()
        self.assertEqual(rows[0]["cohort"], A,
                         "has_forward DESC puts genuine unseen evidence above the fallback")
        self.assertEqual(rows[0]["evidence_state"], "ACCUMULATING",
                         "30 forward episodes < the survivor's 34 frozen OOS episodes")
        self.assertIsNone([r for r in rows if r["cohort"] == B][0]["forward"].get("sharpe"))
        # ranking never touched the indexed record of the other survivor
        after = self.entry_for(B)
        self.assertEqual(si.measured(after), si.measured(before))

    def test_evidence_states_and_champion_candidate(self):
        make_family(self.root, "fam-a", a_v2_like_bundle())
        # 40 episodes == the frozen OOS episode count -> the sample is long enough to judge
        self.forward(A, ("2026-09-11", "2026-09-20", 40, 500.0, 0.9, -0.01, 2.0))
        _, rows = self.board()
        row = [r for r in rows if r["cohort"] == A][0]
        self.assertEqual(row["evidence_state"], "FORWARD_POSITIVE")
        self.assertTrue(row["champion_candidate"])
        self.assertTrue(row["in_top10"])

        make_family(self.root, "fam-b", [survivor("ETHUSDT/1h", oos=(0.4, 10.0, 40))],
                    data_end="2026-09-25")
        degraded = self.entry_for("ETHUSDT/1h")
        path = self.write_slice("degraded.json", slice_doc(
            degraded, "2026-09-26", "2026-09-30", episodes=60, net_pnl=-200.0, sharpe=-0.5))
        proc = self.run_cli(LEADERBOARD_CLI, "forward", "--survivor-id",
                            degraded["survivor_id"], "--slice", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        _, rows = self.board()
        row = [r for r in rows if r["cohort"] == "ETHUSDT/1h"][0]
        self.assertEqual(row["evidence_state"], "FORWARD_DEGRADED")
        self.assertFalse(row["champion_candidate"])
        self.assertEqual(row["rank"], 2, "degraded forward evidence still outranks no evidence")

    def test_aggregation_is_episode_weighted_and_documented(self):
        make_family(self.root, "fam-a", [survivor(A, oos=(0.5, 100.0, 10))])
        self.forward(A, ("2026-09-11", "2026-09-20", 10, 100.0, 1.0, -0.01, 1.0),
                     ("2026-09-21", "2026-09-30", 30, 200.0, 3.0, -0.05, 2.0))
        _, rows = self.board()
        forward = rows[0]["forward"]
        self.assertEqual(forward["episodes"], 40)
        self.assertEqual(forward["net_pnl"], 300.0)
        self.assertEqual(forward["return_pct"], 3.0)
        self.assertEqual(forward["sharpe"], (10 * 1.0 + 30 * 3.0) / 40)
        self.assertEqual(forward["max_dd_pct"], -0.05, "the worst drawdown is kept, not averaged")
        self.assertEqual(forward["first_data_start"], "2026-09-11")
        self.assertEqual(forward["last_data_end"], "2026-09-30")
        self.assertEqual(rows[0]["last_evidence_end"], "2026-09-30")

    def test_tie_break_is_lexical_survivor_id(self):
        # identical evidence: the ordering must still be total and reproducible
        same = dict(oos=(1.0, 100.0, 40), full=(1.0, 200.0), robustness=(10.0, 10.0, 10.0, 10.0),
                    same_sign=0.8)
        make_family(self.root, "fam-a", [survivor(A, **same), survivor(B, **same)])
        _, rows = self.board()
        self.assertEqual([r["survivor_id"] for r in rows],
                         sorted(r["survivor_id"] for r in rows))
        self.assertEqual([r["rank"] for r in rows], [1, 2])

    def test_top10_is_capped_and_the_csv_matches_the_json(self):
        make_family(self.root, "fam-a", [survivor("SYM%dUSDT/1h" % i, oos=(float(i), 100.0, 40))
                                         for i in range(11)])
        _, rows = self.board()
        self.assertEqual(len(rows), 11)
        self.assertEqual(sum(1 for row in rows if row["in_top10"]), 10)
        self.assertEqual(rows[0]["cohort"], "SYM10USDT/1h")
        self.assertEqual(rows[-1]["cohort"], "SYM0USDT/1h")

        written = self.run_cli(LEADERBOARD_CLI, "leaderboard", "--json")
        self.assertEqual(written.returncode, 0, written.stderr)
        payload = json.loads(written.stdout)
        self.assertEqual(payload["survivor_count"], 11)
        self.assertEqual(payload["top10_count"], 10)
        doc = json.load(open(os.path.join(self.root, "_survivors", "leaderboard.json")))
        self.assertEqual(len(doc["entries"]), 11)
        self.assertEqual(len(doc["top10"]), 10)
        self.assertEqual([r["rank"] for r in doc["top10"]], list(range(1, 11)))
        with open(os.path.join(self.root, "_survivors", "leaderboard.csv")) as fh:
            csv_text = fh.read()
        self.assertEqual(csv_text, sl.csv_text(rows))
        self.assertEqual(len(csv_text.strip().splitlines()), 12)  # header + 11

    def test_leaderboard_rebuild_is_deterministic_and_check_detects_drift(self):
        make_family(self.root, "fam-a", a_v2_like_bundle())
        first = self.run_cli(LEADERBOARD_CLI, "leaderboard")
        self.assertEqual(first.returncode, 0, first.stderr)
        with open(os.path.join(self.root, "_survivors", "leaderboard.csv")) as fh:
            csv_before = fh.read()
        check = self.run_cli(LEADERBOARD_CLI, "leaderboard", "--check")
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertIn("result=check_clean", check.stdout)

        doc, rows = self.board()
        second = self.run_cli(LEADERBOARD_CLI, "leaderboard")
        self.assertEqual(second.returncode, 0)
        self.assertEqual(si.measured(doc), si.measured(sl.build(self.root)[0]))
        with open(os.path.join(self.root, "_survivors", "leaderboard.csv")) as fh:
            self.assertEqual(fh.read(), csv_before, "the CSV carries no timestamp: it is stable")

        # hand-edited evidence is drift, and a corrupt jsonl line is refused outright
        with open(os.path.join(self.root, "_survivors", "leaderboard.json"), "w") as fh:
            json.dump({"kind": "tampered"}, fh)
        drifted = self.run_cli(LEADERBOARD_CLI, "leaderboard", "--check")
        self.assertEqual(drifted.returncode, 1)
        self.assertIn("is not the rebuild", drifted.stderr)

        entry = self.entry_for(A)
        os.makedirs(si.forward_dir(self.root), exist_ok=True)
        with open(si.forward_path(self.root, entry["survivor_id"]), "w") as fh:
            fh.write("{\"not\": \"a slice\"}\n")
        _, _, problems = sl.build(self.root)
        self.assertTrue(any("refusing to rank on unverified evidence" in p for p in problems),
                        problems)

    def test_retune_never_overwrites_the_incumbent(self):
        # a challenger family may appear with changed params, but the incumbent's frozen record
        # and its survivor_id stay exactly what they were
        make_family(self.root, "fam-a", [survivor(A, window=20, discount=0.03)])
        incumbent = self.entry_for(A, family_id="fam-a")
        make_family(self.root, "fam-challenger", [survivor(A, window=50, discount=0.03)],
                    challenger_of=incumbent["survivor_id"], created="2026-09-01T00:00:00Z",
                    oos_start="2026-09-05")
        incumbents = [e for e in self.entries() if e["family_id"] == "fam-a"]
        self.assertEqual(len(incumbents), 1)
        self.assertEqual(si.measured(incumbents[0]), si.measured(incumbent),
                         "adding a retuned family never rewrites the incumbent")
        challengers = [e for e in self.entries() if e["family_id"] == "fam-challenger"]
        self.assertEqual(len(challengers), 1)
        self.assertEqual(challengers[0]["challenger_of"], incumbent["survivor_id"])
        self.assertEqual(challengers[0]["strategy_params"], {"window": 50, "discount": 0.03})
        self.assertNotEqual(challengers[0]["survivor_id"], incumbent["survivor_id"])

        # and a retuned slice can never be filed as the incumbent's forward evidence
        retuned = slice_doc(incumbent, "2026-09-11", "2026-09-20")
        retuned["params_sha256"] = challengers[0]["params_sha256"]
        path = self.write_slice("retune.json", retuned)
        proc = self.run_cli(LEADERBOARD_CLI, "forward", "--survivor-id",
                            incumbent["survivor_id"], "--slice", path)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("new challenger family", proc.stderr)

    def test_frozen_bundle_and_index_are_not_rewritten_by_ranking(self):
        path = make_family(self.root, "fam-a", a_v2_like_bundle())
        before = si.sha256_file(path)
        self.run_cli(INDEX_CLI)
        self.run_cli(LEADERBOARD_CLI, "leaderboard")
        self.assertEqual(si.sha256_file(path), before)
        check = self.run_cli(INDEX_CLI, "--check")
        self.assertEqual(check.returncode, 0, check.stderr)
        bundle = json.load(open(path))
        self.assertEqual(bundle["ranking"], None)
        self.assertEqual(bundle["all_survivors_advance"], True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
