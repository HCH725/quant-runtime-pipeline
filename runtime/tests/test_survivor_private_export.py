#!/usr/bin/env python3
"""Regression checks for the guarded validated-survivor private mirror."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME = os.path.dirname(HERE)
sys.path.insert(0, RUNTIME)
sys.path.insert(0, HERE)

import survivor_index as si  # noqa: E402
import survivor_leaderboard as sl  # noqa: E402
import survivor_private_export as spe  # noqa: E402
from test_post_survivor import make_family, survivor  # noqa: E402
from test_survivor_evidence import Fixture  # noqa: E402


def git(repo, *args, check=True):
    proc = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    if check and proc.returncode:
        raise AssertionError("git %s failed: %s" % (" ".join(args), proc.stderr))
    return proc


def init_repo(parent):
    repo = os.path.join(parent, "validated-survivor-research")
    remote = os.path.join(parent, "validated-survivor-research.git")
    os.makedirs(repo)
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    with open(os.path.join(repo, "README.md"), "w") as fh:
        fh.write("fixture\n")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "docs: init fixture")
    subprocess.run(["git", "init", "--bare", remote], check=True,
                   capture_output=True, text=True)
    git(repo, "remote", "add", "origin", remote)
    git(repo, "push", "-u", "origin", "main")
    return repo


def run_leaderboard(root, check=False):
    out, err = io.StringIO(), io.StringIO()
    argv = ["leaderboard", "--results-root", root, "--json"]
    if check:
        argv.append("--check")
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = sl.main(argv)
    return rc, out.getvalue(), err.getvalue()


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="private-export-v2-")
        self.root = os.path.join(self.tmp, "results")
        os.makedirs(self.root)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fixture(self, evidence=False):
        fx = Fixture(self.root)
        if evidence:
            fx.stage_replay()
            self.assertEqual(fx.materialize()[0], 0)
            self.assertEqual(run_leaderboard(self.root)[0], 0)
        return fx

    def test_temp_leaderboard_rebuild_never_calls_exporter(self):
        self.fixture()
        fake = types.SimpleNamespace(export=mock.Mock())
        with mock.patch.dict(sys.modules, {"survivor_private_export": fake}):
            rc, _out, err = run_leaderboard(self.root)
        self.assertEqual(rc, 0, err)
        fake.export.assert_not_called()

    def test_check_never_calls_exporter_even_when_root_is_marked_canonical(self):
        self.fixture()
        fake = types.SimpleNamespace(export=mock.Mock())
        with mock.patch.object(sl.si, "DEFAULT_RESULTS_ROOT", self.root), \
                mock.patch.dict(sys.modules, {"survivor_private_export": fake}):
            rc, _out, err = run_leaderboard(self.root, check=True)
        self.assertEqual(rc, 0, err)
        fake.export.assert_not_called()

    def test_direct_export_minimal_layout_and_non_top10_entry(self):
        # 11 formal entries prove sync is not a Top-10 gate.
        for index in range(11):
            make_family(self.root, "fam-%02d" % index,
                        [survivor("SYM%02dUSDT/1h" % index,
                                  oos=(float(index + 1), 100.0, 40))])
        self.assertEqual(run_leaderboard(self.root)[0], 0)
        with open(os.path.join(self.root, "_survivors", "leaderboard.json")) as fh:
            board = json.load(fh)
        outside = [entry for entry in board["entries"] if not entry["in_top10"]]
        self.assertEqual(len(outside), 1)
        repo = init_repo(self.tmp)
        result = spe.export(self.root, repo)
        self.assertTrue(result["ok"], result)
        self.assertTrue(os.path.isfile(os.path.join(repo, "leaderboard", "leaderboard.json")))
        rebuilt, problems = si.build(self.root)
        self.assertEqual(problems, [])
        by_id = {entry["survivor_id"]: entry for entry in rebuilt["survivors"]}
        for entry in board["entries"]:
            with open(os.path.join(repo, "survivors", entry["survivor_id"], "baseline.json")) as fh:
                self.assertEqual(json.load(fh), by_id[entry["survivor_id"]])
            self.assertTrue(os.path.isfile(os.path.join(
                repo, "survivors", entry["survivor_id"], "baseline.json")))
        self.assertTrue(os.path.isfile(os.path.join(
            repo, "survivors", outside[0]["survivor_id"], "baseline.json")))
        with open(os.path.join(repo, "README.md")) as fh:
            readme = fh.read()
        self.assertIn("**Promoted strategy families: 11**", readme)
        self.assertIn("Formal promoted survivors: **11**", readme)
        self.assertIn("(UTC+8)", readme)

    def test_readme_counts_unique_families_and_preserves_human_text(self):
        make_family(self.root, "same-family", [
            survivor("BTCUSDT/1h", oos=(1.0, 100.0, 40)),
            survivor("SOLUSDT/4h", oos=(2.0, 100.0, 40)),
        ])
        self.assertEqual(run_leaderboard(self.root)[0], 0)
        repo = init_repo(self.tmp)
        with open(os.path.join(repo, "README.md"), "w") as fh:
            fh.write("# Validated Survivor Research\n\nHuman-owned paragraph.\n")
        git(repo, "add", "README.md")
        git(repo, "commit", "-m", "docs: add human readme text")
        git(repo, "push", "origin", "main")

        result = spe.export(self.root, repo)

        self.assertTrue(result["ok"], result)
        with open(os.path.join(repo, "README.md")) as fh:
            readme = fh.read()
        self.assertIn("**Promoted strategy families: 1**", readme)
        self.assertIn("Formal promoted survivors: **2**", readme)
        self.assertIn("Human-owned paragraph.", readme)
        self.assertEqual(readme.count(spe.README_START), 1)
        self.assertEqual(readme.count(spe.README_END), 1)

    def test_second_export_is_idempotent(self):
        self.fixture()
        repo = init_repo(self.tmp)
        first = spe.export(self.root, repo)
        self.assertTrue(first["ok"], first)
        head = git(repo, "rev-parse", "HEAD").stdout.strip()
        second = spe.export(self.root, repo)
        self.assertTrue(second["ok"], second)
        self.assertEqual(second["result"], "unchanged")
        self.assertEqual(second["changed_paths"], [])
        self.assertEqual(git(repo, "rev-parse", "HEAD").stdout.strip(), head)

    def test_immutable_baseline_conflict_refuses_overwrite(self):
        fx = self.fixture()
        repo = init_repo(self.tmp)
        self.assertTrue(spe.export(self.root, repo)["ok"])
        path = os.path.join(repo, "survivors", fx.entry["survivor_id"], "baseline.json")
        with open(path, "w") as fh:
            fh.write('{"human_or_conflicting": true}\n')
        result = spe.export(self.root, repo)
        self.assertFalse(result["ok"])
        self.assertEqual(result["result"], "conflict")
        with open(path) as fh:
            self.assertEqual(json.load(fh), {"human_or_conflicting": True})

    def test_legacy_baseline_missing_avg_trades_is_compatible_noop(self):
        fx = self.fixture()
        repo = init_repo(self.tmp)
        self.assertTrue(spe.export(self.root, repo)["ok"])
        path = os.path.join(repo, "survivors", fx.entry["survivor_id"], "baseline.json")
        with open(path, "rb") as fh:
            baseline = json.load(fh)
        baseline["full"].pop("avg_trades_per_year")
        legacy_bytes = spe._json_bytes(baseline)
        with open(path, "wb") as fh:
            fh.write(legacy_bytes)

        result = spe.export(self.root, repo)

        self.assertTrue(result["ok"], result)
        self.assertNotEqual(result["result"], "conflict")
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), legacy_bytes)

    def test_immutable_evidence_conflict_refuses_overwrite(self):
        fx = self.fixture(evidence=True)
        repo = init_repo(self.tmp)
        self.assertTrue(spe.export(self.root, repo)["ok"])
        path = os.path.join(repo, "survivors", fx.entry["survivor_id"],
                            "evidence-manifest.json")
        human_bytes = b'{"human_or_conflicting": true}\n'
        with open(path, "wb") as fh:
            fh.write(human_bytes)
        head = git(repo, "rev-parse", "HEAD").stdout.strip()
        remote = git(repo, "ls-remote", "origin", "refs/heads/main").stdout.strip()

        result = spe.export(self.root, repo)

        self.assertFalse(result["ok"])
        self.assertEqual(result["result"], "conflict")
        self.assertTrue(any("evidence-manifest.json" in warning
                            for warning in result["warnings"]))
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), human_bytes)
        self.assertEqual(git(repo, "rev-parse", "HEAD").stdout.strip(), head)
        self.assertEqual(git(repo, "ls-remote", "origin", "refs/heads/main").stdout.strip(),
                         remote)

    def test_evidence_absent_and_present_layout(self):
        fx = self.fixture(evidence=False)
        repo = init_repo(self.tmp)
        self.assertTrue(spe.export(self.root, repo)["ok"])
        survivor_dir = os.path.join(repo, "survivors", fx.entry["survivor_id"])
        self.assertEqual(set(os.listdir(survivor_dir)), {"baseline.json"})

        # New isolated fixture with a valid §28 package: only compact package files are mirrored.
        other = os.path.join(self.tmp, "results-evidence")
        os.makedirs(other)
        fx2 = Fixture(other)
        fx2.stage_replay()
        self.assertEqual(fx2.materialize()[0], 0)
        self.assertEqual(run_leaderboard(other)[0], 0)
        repo2 = init_repo(os.path.join(self.tmp, "second"))
        result = spe.export(other, repo2)
        self.assertTrue(result["ok"], result)
        survivor_dir2 = os.path.join(repo2, "survivors", fx2.entry["survivor_id"])
        self.assertEqual(set(os.listdir(survivor_dir2)),
                         {"baseline.json", "evidence-manifest.json", "aggregate.csv"})
        self.assertFalse(os.path.exists(os.path.join(survivor_dir2, "grids")))
        self.assertFalse(os.path.exists(os.path.join(survivor_dir2, "research.md")))

    def test_unrelated_research_files_are_not_staged_or_committed(self):
        self.fixture()
        repo = init_repo(self.tmp)
        with open(os.path.join(repo, "research.md"), "w") as fh:
            fh.write("human notes\n")
        with open(os.path.join(repo, "unrelated.txt"), "w") as fh:
            fh.write("leave me alone\n")
        result = spe.export(self.root, repo)
        self.assertTrue(result["ok"], result)
        status = git(repo, "status", "--porcelain").stdout
        self.assertIn("?? research.md", status)
        self.assertIn("?? unrelated.txt", status)
        committed = git(repo, "show", "--pretty=", "--name-only", "HEAD").stdout
        self.assertNotIn("research.md", committed)
        self.assertNotIn("unrelated.txt", committed)

    def test_exporter_failure_is_nonblocking_to_canonical_hook(self):
        self.fixture()
        fake = types.SimpleNamespace(export=mock.Mock(side_effect=RuntimeError("offline")))
        with mock.patch.object(sl.si, "DEFAULT_RESULTS_ROOT", self.root), \
                mock.patch.dict(sys.modules, {"survivor_private_export": fake}):
            rc, _out, err = run_leaderboard(self.root)
        self.assertEqual(rc, 0, err)
        self.assertIn("WARNING: private survivor export failed", err)
        fake.export.assert_called_once()

    def test_missing_repo_is_warning_only_for_exporter(self):
        self.fixture()
        result = spe.export(self.root, os.path.join(self.tmp, "missing-repo"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["result"], "warning")
        self.assertTrue(any("missing" in w for w in result["warnings"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
