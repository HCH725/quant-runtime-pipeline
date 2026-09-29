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
    bundle (directory name, a missing / empty / mismatched / non-string `kanban_task_id`,
    round-spec checksum), a param cell outside the registered axes, and a duplicate survivor_id
    all refuse to index,
  * the layer only ever writes under `_survivors/**`: `--out` / `--out-dir` / the forward append
    cannot be pointed at a frozen bundle, verdict or result (not by an absolute path, not by a `..`
    segment, not by a symlink, and not by re-pointing `_survivors` itself with a symlink), and a
    path inside the boundary is still accepted,
  * forward evidence is strictly post-freeze, never overlaps or repeats a recorded slice, never
    carries params other than the incumbent's (a retune is a challenger, not evidence), and is
    only admissible when its `source_run` resolves to a terminal DONE run - by an ABSOLUTE attempt
    dir inside the results tree - whose sentinel-pinned `result.json` carries the same numbers (a
    self-declared or cwd-dependent slice is refused, not ranked),
  * a challenger's OOS start must be after its own preregistration cutoff, and a retune never
    rewrites the incumbent: both survivors coexist with their own params,
  * the leaderboard ordering is total and reproducible (forward evidence outranks the frozen
    fallback, NULLS LAST, lexical survivor_id last), the Top-10 is capped at 10, and a
    FROZEN_ONLY survivor reports no forward metrics at all (no invented evidence),
  * the frozen bundle and the index are never rewritten by ranking.
"""
import datetime
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
import parameter_contract as pc  # noqa: E402
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
                task_id="t_test", mutate=None, name_mismatch=False,
                parameter_contract=None, cutoff_key="data_end", direct=False):
    """One family + one round + one frozen survivor bundle, in throwaway-results-root shape."""
    round_id = round_id or family_id + "-r1"
    run_id = run_id or round_id + "-u1"
    fam_dir = os.path.join(root, family_id)
    round_dir = os.path.join(fam_dir, "rounds", round_id)
    data = {"data_start": "2022-01-01", cutoff_key: data_end,
            "historical_end": "2025-09-30", "oos_start": oos_start, "oos_end": data_end}
    spec = {
        "schema_version": 1, "family_id": family_id, "round_id": round_id,
        "contract": "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md v1.3.2",
        "data": data,
    }
    # Include a parameter_contract in the round-spec.  Non-A families without one fail closed
    # under v1.8+, so test helpers must always provide one.
    if parameter_contract is not None:
        spec["parameter_contract"] = parameter_contract
    elif family_id == "close-vs-sma-mean-reversion-long-flat-v2":
        # Legacy A v2: no parameter_contract needed (uses in-code bridge)
        pass
    else:
        # Non-A test families: include a valid parameter_contract with matching family_id
        # so the fail-closed path for "unknown family without schema" is tested separately.
        spec["parameter_contract"] = {
            "parameter_contract_version": 1,
            "family_id": family_id,
            "contract_ref": "test fixture for non-A families",
            "research_axes_ordered": [
                {"name": "window", "kind": "atomic", "members": ["window"],
                 "registered_values": [20, 50, 100, 200], "row_fields": ["window"]},
                {"name": "discount", "kind": "atomic", "members": ["discount"],
                 "registered_values": [0.01, 0.02, 0.03], "row_fields": ["discount"]},
                {"name": "spacing_pct", "kind": "atomic", "members": ["spacing_pct"],
                 "registered_values": [0.01, 0.02, 0.03, 0.04], "row_fields": ["spacing_pct"]},
                {"name": "size_multiplier", "kind": "atomic", "members": ["size_multiplier"],
                 "registered_values": [1.0, 1.1], "row_fields": ["size_multiplier"]},
                {"name": "breakeven_tp_pct", "kind": "atomic", "members": ["breakeven_tp_pct"],
                 "registered_values": [0.01, 0.02, 0.03], "row_fields": ["breakeven_tp_pct"]},
                {"name": "invalidation_pct", "kind": "atomic", "members": ["invalidation_pct"],
                 "registered_values": [0.05, 0.10], "row_fields": ["invalidation_pct"]},
            ],
            "row_fields": ["window", "discount", "spacing_pct", "size_multiplier",
                           "breakeven_tp_pct", "invalidation_pct"],
            "composite_map": {},
            "strategy_param_fields": ["window", "discount"],
            "dca_param_fields": ["spacing_pct", "size_multiplier", "breakeven_tp_pct",
                                 "invalidation_pct"],
            "canonical_recipe": {"sort_keys": True, "separators": (",", ":"),
                                 "ensure_ascii": False,
                                 "numeric_rule": "JSON number finite, bool excluded"},
            "row_match_recipe": {"keys": ["symbol", "timeframe", "window", "discount",
                                          "spacing_pct", "size_multiplier",
                                          "breakeven_tp_pct", "invalidation_pct"],
                                 "equality": "exact, numeric == float compare, rest bytewise"},
            "non_params": ["symbol", "timeframe", "ema_pair_index", "walk_forward_index",
                           "n_steps", "indices", "diagnostics", "metrics"],
            "domain_cardinality": {"strategy": 12, "dca": 48, "per_cohort": 576},
        }
    spec_path = write_json(os.path.join(round_dir, "round-spec.json"), spec)
    family_doc = {
        "schema_version": 1, "family_id": family_id,
        "challenger_of": challenger_of, "created_at_utc": created,
    }
    if direct:
        family_doc["handoff"] = {"execution": "direct_hermes"}
    else:
        family_doc["kanban_task_id"] = "t_mismatched_family" if name_mismatch else task_id
    write_json(os.path.join(fam_dir, "family.json"), family_doc)
    bundle = {
        "schema_version": 1, "kind": "frozen_survivor_bundle",
        "contract": "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md v1.5.0",
        "contract_section": "7.3 / 10.8",
        "family_id": family_id, "round_id": round_id, "run_id": run_id,
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
    if not direct:
        bundle["kanban_task_id"] = task_id
        bundle["kanban_board"] = "quant-strategy-research"
    bundle["bundle_identity_sha256"] = bundle_identity(bundle)
    if mutate:
        bundle = mutate(bundle)
    return write_json(os.path.join(round_dir, "survivor-bundle.json"), bundle)


def bare_slice(entry, data_start, data_end, episodes=50, net_pnl=100.0, sharpe=1.0,
               max_dd_pct=-0.02, return_pct=1.0):
    """The measured fields of a slice, without provenance (`source_run`)."""
    return {"survivor_id": entry["survivor_id"],
            "bundle_identity_sha256": entry["bundle_identity_sha256"],
            "params_sha256": entry["params_sha256"], "data_start": data_start,
            "data_end": data_end, "data_snapshot": "raw store snapshot 2026-10-05",
            "execution_semantics": "qlib container 20_strategy_a_run.py semantics",
            "cost_model": "taker 5bp, 1 tick baseline slippage, official funding",
            "episodes": episodes, "net_pnl": net_pnl, "return_pct": return_pct, "sharpe": sharpe,
            "max_dd_pct": max_dd_pct, "fees": 10.0, "funding": 1.0, "slippage_ticks": 1,
            "produced_at_utc": "2026-10-06T00:00:00Z"}


def make_forward_run(root, entry, slice_fields, run_id="fw-r1-u1", family_id=None,
                     task_id="t_forward_launch", status="DONE", result_family_id=None,
                     result_run_id=None, drop_sentinel=False, extra_result=None,
                     mutate_result=None, mutate_sentinel=None, direct=False):
    """A terminal run whose `result.json` declares the slice: the only admissible source_run.

    Mirrors what a real forward launch writes: `<attempt_dir>/result.json` plus the terminal
    sentinel published by `runtime/terminal_evidence.py`, which records the result's checksum.
    """
    family_id = family_id or entry["family_id"]
    attempt = os.path.join(root, family_id, "rounds", family_id + "-fw-r1", "attempts", run_id)
    result = {"schema_version": 1, "family_id": result_family_id or family_id,
              "round_id": family_id + "-fw-r1", "run_id": result_run_id or run_id,
              "coverage_complete": True, "forward_slice": dict(slice_fields)}
    if not direct:
        result["task_id"] = task_id
    result.update(extra_result or {})
    if mutate_result:
        result = mutate_result(result)
    result_path = write_json(os.path.join(attempt, "result.json"), result)
    sentinel = {"schema_version": 1, "status": status, "family_id": family_id,
                "round_id": family_id + "-fw-r1", "run_id": run_id,
                "created_at_utc": "2026-11-02T00:00:00Z",
                "artifact_manifest": ["result.json"],
                "artifact_checksums": {"result.json": si.sha256_file(result_path)}}
    if not direct:
        sentinel["task_id"] = task_id
        sentinel["kanban_board"] = "quant-strategy-research"
    if mutate_sentinel:
        sentinel = mutate_sentinel(sentinel)
    sentinel_path = write_json(os.path.join(attempt, "DONE"), sentinel)
    source = {"attempt_dir": attempt, "run_id": run_id,
              "sentinel_sha256": si.sha256_file(sentinel_path),
              "result_sha256": si.sha256_file(result_path)}
    if not direct:
        source["kanban_task_id"] = task_id
    return source


def resign(bundle):
    """Give a mutated bundle a self-consistent published identity (the tamper the audit used)."""
    return dict(bundle, bundle_identity_sha256=bundle_identity(bundle))


def drop_bundle_task_id(bundle):
    """A bundle that lost its owning card id, re-signed so only the ownership check can catch it."""
    return resign({k: v for k, v in bundle.items() if k != "kanban_task_id"})


class Base(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="post-survivor-")
        # every slice fixture gets its own produced-run directory, so the provenance of one slice
        # can never be satisfied by another slice's run
        self._forward_runs = 0

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

    def slice_doc(self, entry, data_start, data_end, episodes=50, net_pnl=100.0, sharpe=1.0,
                  max_dd_pct=-0.02, return_pct=1.0):
        """A slice carrying the provenance contract 27.3 requires.

        The numbers are written into a real (terminal DONE) run inside this test's results root,
        exactly as a forward launch would, so the happy-path cases exercise the provenance check
        instead of bypassing it.
        """
        self._forward_runs += 1
        doc = bare_slice(entry, data_start, data_end, episodes=episodes, net_pnl=net_pnl,
                         sharpe=sharpe, max_dd_pct=max_dd_pct, return_pct=return_pct)
        doc["source_run"] = make_forward_run(self.root, entry, doc,
                                             run_id="fw-r1-u%d" % self._forward_runs)
        return doc


class TestSurvivorIndex(Base):

    def test_index_rebuild_is_deterministic_and_pins_the_identity_fields(self):
        make_family(self.root, "fam-a", a_v2_like_bundle())
        first, problems = si.build(self.root)
        self.assertEqual(problems, [])
        second, _ = si.build(self.root)
        self.assertEqual(si.measured(first), si.measured(second),
                         "a rebuild must reproduce the index byte for byte, clock aside")
        self.assertEqual(first["contract"], "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md "
                                            "v1.8.0")
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

    def test_current_round_spec_end_key_is_accepted_as_research_cutoff(self):
        make_family(self.root, "fam-current", [survivor("ETHUSDT/1d")],
                    data_end="2026-09-11", cutoff_key="end")
        index, problems = si.build(self.root)
        self.assertEqual(problems, [])
        self.assertIsNotNone(index)
        self.assertEqual(index["survivor_count"], 1)
        self.assertEqual(index["survivors"][0]["research_data_cutoff"], "2026-09-11")

    def test_nested_phase_metrics_are_accepted_and_flat_metrics_take_precedence(self):
        rec = survivor(A)
        rec["metrics"]["phases"] = {"historical": rec["metrics"]["historical"],
                                    "oos": {"sharpe": 2.0, "net_pnl": 20},
                                    "full": {"sharpe": 3.0, "net_pnl": 30}}
        rec["metrics"]["oos"] = {"sharpe": 4.0, "net_pnl": 40}
        del rec["metrics"]["full"]
        make_family(self.root, "fam-nested-metrics", [rec])
        index, problems = si.build(self.root)
        self.assertEqual(problems, [])
        self.assertIsNotNone(index)
        assert index is not None
        entry = next(row for row in index["survivors"] if row["family_id"] == "fam-nested-metrics")
        self.assertEqual(entry["oos"]["sharpe"], 4.0)
        self.assertEqual(entry["full"]["sharpe"], 3.0)

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

    def test_malformed_source_artifacts_refuses_without_crashing(self):
        def malformed(bundle):
            return resign(dict(bundle, source_artifacts=["legacy-artifact.json"]))

        make_family(self.root, "fam-a", a_v2_like_bundle(), mutate=malformed)
        index, problems = si.build(self.root)
        self.assertIsNone(index)
        self.assertTrue(any("no source_artifacts checksum map" in p for p in problems), problems)

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

    def test_missing_ownership_id_fails_closed(self):
        # The v1.5.0 attack (audit finding F3): the comparison only ran when BOTH ids were
        # truthy, so a bundle that lost its kanban_task_id - re-signed, so the identity check
        # cannot catch it either - was indexed anyway.
        make_family(self.root, "fam-a", a_v2_like_bundle(), mutate=drop_bundle_task_id)
        index, problems = si.build(self.root)
        self.assertIsNone(index)
        self.assertTrue(any("source ownership is incomplete" in p for p in problems), problems)

        # and the same when the family record is the side that lost the id
        root2 = tempfile.mkdtemp(prefix="family-taskid-")
        try:
            make_family(root2, "fam-a", a_v2_like_bundle())
            family_path = os.path.join(root2, "fam-a", "family.json")
            with open(family_path) as fh:
                family = json.load(fh)
            del family["kanban_task_id"]
            write_json(family_path, family)
            _, problems = si.build(root2)
            self.assertTrue(any("source ownership is incomplete" in p for p in problems), problems)
        finally:
            shutil.rmtree(root2, ignore_errors=True)

        # the control: the id present on both sides is still indexed
        root3 = tempfile.mkdtemp(prefix="ownership-ok-")
        try:
            make_family(root3, "fam-a", a_v2_like_bundle())
            index, problems = si.build(root3)
            self.assertEqual(problems, [])
            self.assertEqual(index["survivor_count"], 2)
        finally:
            shutil.rmtree(root3, ignore_errors=True)

    def test_non_string_ownership_id_fails_closed(self):
        # The v1.5.1 residual (audit t_346bcc04 finding F3): the check was truthful/equal only, so
        # the very same JSON *number* on both sides - self-consistent, hence not caught by the
        # identity check either - was accepted as provenance.
        for value in (12345, True, "", ["t_1f97bf6b"], None):
            root = tempfile.mkdtemp(prefix="ownership-type-")
            try:
                make_family(root, "fam-a", a_v2_like_bundle(), task_id=value)
                index, problems = si.build(root)
                self.assertIsNone(index, value)
                self.assertTrue(any("source ownership is incomplete" in p for p in problems),
                                (value, problems))
                self.assertTrue(any("not a non-empty string" in p for p in problems),
                                (value, problems))
            finally:
                shutil.rmtree(root, ignore_errors=True)

        # the control: a non-empty string id on both sides is still indexed
        root = tempfile.mkdtemp(prefix="ownership-string-")
        try:
            make_family(root, "fam-a", a_v2_like_bundle(), task_id="t_1f97bf6b")
            index, problems = si.build(root)
            self.assertEqual(problems, [])
            self.assertEqual(index["survivor_count"], 2)
        finally:
            shutil.rmtree(root, ignore_errors=True)

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

    def test_rebuild_in_a_later_second_is_still_unchanged(self):
        """`unchanged` may only mean: the measured content on disk IS the rebuild.

        The generation timestamp is the writer's clock, not content a rebuild must reproduce, so
        pinning it to a later second must not turn a no-op rebuild into a rewrite - the property
        test_index_cli_writes_checks_and_detects_drift relies on, and the rule `--check` already
        uses (`measured()`).
        """
        make_family(self.root, "fam-a", a_v2_like_bundle())
        out = si.index_path(self.root)
        real_now_utc = si.now_utc
        try:
            si.now_utc = lambda: "2026-09-13T00:00:00Z"
            first, problems = si.build(self.root)
            self.assertEqual(problems, [])
            self.assertEqual(si.write_index(first, out), "written")
            written_sha = si.sha256_file(out)
            si.now_utc = lambda: "2026-09-13T00:00:07Z"  # the same bundles, seven seconds later
            second, _ = si.build(self.root)
            self.assertNotEqual(first[si.GENERATED_KEY], second[si.GENERATED_KEY])
            self.assertEqual(si.measured(first), si.measured(second))
            self.assertEqual(si.write_index(second, out), "unchanged")
            self.assertEqual(si.sha256_file(out), written_sha,
                             "a no-op rebuild must not rewrite the file or move its timestamp")
        finally:
            si.now_utc = real_now_utc

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
        doc = self.slice_doc(self.entry, "2026-09-10", "2026-09-20")
        self.assertTrue(any("not later than the survivor's research data cutoff" in p
                            for p in sl.slice_problems(doc, self.entry, [], self.root)))
        doc = self.slice_doc(self.entry, "2026-08-01", "2026-09-11")
        self.assertTrue(any("not later than" in p
                            for p in sl.slice_problems(doc, self.entry, [], self.root)))
        doc = self.slice_doc(self.entry, "2026-09-11", "2026-09-20")
        self.assertEqual(sl.slice_problems(doc, self.entry, [], self.root), [])

    def test_overlapping_or_repeated_slice_is_refused(self):
        prior = self.slice_doc(self.entry, "2026-09-11", "2026-09-20")
        for start, end in (("2026-09-15", "2026-09-25"), ("2026-09-11", "2026-09-20"),
                           ("2026-09-01", "2026-09-12")):
            later = self.slice_doc(self.entry, start, end)
            self.assertTrue(any("overlaps the recorded slice" in p
                                for p in sl.slice_problems(later, self.entry, [prior], self.root)),
                            (start, end))
        clean = self.slice_doc(self.entry, "2026-09-21", "2026-09-30")
        self.assertEqual(sl.slice_problems(clean, self.entry, [prior], self.root), [])

    def test_param_or_bundle_identity_mismatch_is_refused(self):
        retuned = self.slice_doc(self.entry, "2026-09-11", "2026-09-20")
        retuned["params_sha256"] = "sha256:" + "0" * 64
        self.assertTrue(any("may never be filed as evidence" in p
                            for p in sl.slice_problems(retuned, self.entry, [], self.root)))
        foreign = self.slice_doc(self.entry, "2026-09-11", "2026-09-20")
        foreign["bundle_identity_sha256"] = "sha256:" + "1" * 64
        self.assertTrue(any("is not the survivor's frozen bundle" in p
                            for p in sl.slice_problems(foreign, self.entry, [], self.root)))
        wrong_target = self.slice_doc(self.entry, "2026-09-11", "2026-09-20")
        wrong_target["survivor_id"] = "sv-someone-else"
        self.assertTrue(any("is not the target survivor" in p
                            for p in sl.slice_problems(wrong_target, self.entry, [], self.root)))

    def test_missing_slice_field_is_refused(self):
        for field in sorted(sl.SLICE_REQUIRED):
            doc = self.slice_doc(self.entry, "2026-09-11", "2026-09-20")
            del doc[field]
            self.assertTrue(sl.slice_problems(doc, self.entry, [], self.root), field)
        no_episodes = self.slice_doc(self.entry, "2026-09-11", "2026-09-20", episodes=0)
        self.assertTrue(any("positive integer" in p
                            for p in sl.slice_problems(no_episodes, self.entry, [], self.root)))

    def test_cli_appends_reads_back_and_refuses_a_second_identical_slice(self):
        first = self.slice_doc(self.entry, "2026-09-11", "2026-09-20")
        path = self.write_slice("one.json", first)
        proc = self.run_cli(LEADERBOARD_CLI, "forward", "--survivor-id",
                            self.entry["survivor_id"], "--slice", path, "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["result"], "appended")
        record = json.load(open(si.forward_path(self.root, self.entry["survivor_id"])))
        self.assertEqual({k: record[k] for k in first}, first,
                         "the slice is stored verbatim (only a slice schema version is added)")
        self.assertEqual(record["slice_schema_version"], 2)

        again = self.run_cli(LEADERBOARD_CLI, "forward", "--survivor-id",
                             self.entry["survivor_id"], "--slice", path)
        self.assertEqual(again.returncode, 1)
        self.assertIn("overlaps the recorded slice", again.stderr)

        second = self.slice_doc(self.entry, "2026-09-21", "2026-09-30", episodes=5, net_pnl=10.0)
        path2 = self.write_slice("two.json", second)
        proc = self.run_cli(LEADERBOARD_CLI, "forward", "--survivor-id",
                            self.entry["survivor_id"], "--slice", path2, "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["slice_count"], 2)
        with open(si.forward_path(self.root, self.entry["survivor_id"])) as fh:
            lines = [json.loads(line) for line in fh if line.strip()]
        self.assertEqual(len(lines), 2, "forward evidence is append-only")

    def test_unknown_survivor_id_is_refused(self):
        path = self.write_slice("one.json",
                                self.slice_doc(self.entry, "2026-09-11", "2026-09-20"))
        proc = self.run_cli(LEADERBOARD_CLI, "forward", "--survivor-id", "sv-nope",
                            "--slice", path)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("matches 0 indexed survivors", proc.stderr)

    def test_self_declared_slice_without_a_verifiable_source_run_is_refused(self):
        # The v1.5.0 attack (audit finding F2): invented episodes/PnL/Sharpe with no reference at
        # all was accepted, ranking rank 1 / FORWARD_POSITIVE / champion_candidate.
        bare = bare_slice(self.entry, "2026-09-11", "2026-09-30", episodes=999,
                          net_pnl=987654.0, sharpe=999.0, return_pct=12345.0)
        path = self.write_slice("invented.json", bare)
        proc = self.run_cli(LEADERBOARD_CLI, "forward", "--survivor-id",
                            self.entry["survivor_id"], "--slice", path)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("source_run", proc.stderr)
        self.assertFalse(os.path.exists(si.forward_path(self.root, self.entry["survivor_id"])),
                         "nothing is written when the provenance does not check out")

        valid = self.slice_doc(self.entry, "2026-09-11", "2026-09-30")
        cases = {
            "the run directory does not exist":
                dict(valid, source_run=dict(valid["source_run"],
                                            attempt_dir=os.path.join(self.root, "gone"))),
            "the run is not a directory":
                dict(valid, source_run=dict(valid["source_run"],
                                            attempt_dir=os.path.join(self.root, "fam-a"))),
            "the sentinel was never published": self.slice_without_sentinel(valid),
            "the run did not finish": self.slice_with_failed_sentinel(valid),
            "the sentinel checksum was forged": dict(valid, source_run=dict(
                valid["source_run"], sentinel_sha256="sha256:" + "0" * 64)),
            "the source lives in the post-survivor namespace":
                self.slice_inside_survivors_namespace(valid),
        }
        for position, (label, doc) in enumerate(sorted(cases.items())):
            path = self.write_slice("bad-%d.json" % position, doc)
            proc = self.run_cli(LEADERBOARD_CLI, "forward", "--survivor-id",
                                self.entry["survivor_id"], "--slice", path)
            self.assertEqual(proc.returncode, 1, label)
            self.assertFalse(os.path.exists(si.forward_path(self.root, self.entry["survivor_id"])),
                             label)
        self.assertFalse(os.path.exists(si.forward_path(self.root, self.entry["survivor_id"])))

    def slice_without_sentinel(self, valid):
        doc = copy.deepcopy(valid)
        os.unlink(os.path.join(doc["source_run"]["attempt_dir"], "DONE"))
        return doc

    def slice_with_failed_sentinel(self, valid):
        doc = copy.deepcopy(valid)
        write_json(os.path.join(doc["source_run"]["attempt_dir"], "DONE"),
                   {"schema_version": 1, "status": "FAILED", "run_id": doc["source_run"]["run_id"],
                    "task_id": doc["source_run"]["kanban_task_id"], "family_id": self.entry["family_id"],
                    "artifact_checksums": {}})
        return doc

    def slice_inside_survivors_namespace(self, valid):
        doc = copy.deepcopy(valid)
        source = make_forward_run(os.path.join(self.root, "_survivors"), self.entry,
                                  {k: v for k, v in valid.items() if k != "source_run"},
                                  run_id="fw-inside-u1")
        doc["source_run"] = source
        return doc

    def test_relative_attempt_dir_is_refused(self):
        # The v1.5.1 residual (audit t_346bcc04 finding F2): a relative attempt_dir was resolved
        # against the READER's cwd, so the identical slice was accepted when the tool was run from
        # the results root and refused when it was run from the repository.
        valid = self.slice_doc(self.entry, "2026-09-11", "2026-09-30")
        relative = dict(valid, source_run=dict(
            valid["source_run"],
            attempt_dir=os.path.relpath(valid["source_run"]["attempt_dir"], self.root)))
        rel_path = self.write_slice("relative-attempt.json", relative)
        proc = subprocess.run([sys.executable, LEADERBOARD_CLI, "forward", "--survivor-id",
                               self.entry["survivor_id"], "--slice", rel_path,
                               "--results-root", self.root],
                              capture_output=True, text=True, cwd=self.root)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("is not an absolute path", proc.stderr)
        self.assertFalse(os.path.exists(si.forward_path(self.root, self.entry["survivor_id"])),
                         "a cwd-dependent provenance never appends evidence")

        # the control: the identical slice with an absolute attempt_dir is still accepted
        abs_path = self.write_slice("absolute-attempt.json", valid)
        ok = self.run_cli(LEADERBOARD_CLI, "forward", "--survivor-id", self.entry["survivor_id"],
                          "--slice", abs_path, "--json")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(json.loads(ok.stdout)["result"], "appended")

    def test_slice_numbers_must_reconcile_with_the_pinned_run_result(self):
        valid = self.slice_doc(self.entry, "2026-09-11", "2026-09-30", episodes=40,
                               net_pnl=500.0, sharpe=0.9, return_pct=2.0)
        # (a) the numbers are edited after the run that supposedly produced them published its
        #     result - the run artifact still holds the real values
        invented = dict(valid, episodes=999, net_pnl=987654.0, sharpe=999.0, return_pct=12345.0)
        path = self.write_slice("invented-metrics.json", invented)
        proc = self.run_cli(LEADERBOARD_CLI, "forward", "--survivor-id",
                            self.entry["survivor_id"], "--slice", path)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("does not match the source run's result.json.forward_slice", proc.stderr)

        # (b) the run's result is rewritten after the fact: the sentinel's recorded checksum no
        #     longer matches the file, so the evidence is unverifiable
        rewritten = copy.deepcopy(valid)
        result_path = os.path.join(rewritten["source_run"]["attempt_dir"], "result.json")
        with open(result_path) as fh:
            result = json.load(fh)
        result["forward_slice"]["net_pnl"] = -1.0
        write_json(result_path, result)
        path = self.write_slice("rewritten-result.json", rewritten)
        proc = self.run_cli(LEADERBOARD_CLI, "forward", "--survivor-id",
                            self.entry["survivor_id"], "--slice", path)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("hashes to", proc.stderr)

        # (c) the run belongs to another family: evidence for this survivor cannot come from it
        foreign = copy.deepcopy(valid)
        foreign["source_run"] = make_forward_run(self.root, self.entry,
                                                 {k: v for k, v in valid.items()
                                                  if k != "source_run"},
                                                 run_id="fw-foreign-u1", family_id="fam-other")
        path = self.write_slice("foreign-run.json", foreign)
        proc = self.run_cli(LEADERBOARD_CLI, "forward", "--survivor-id",
                            self.entry["survivor_id"], "--slice", path)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("sentinel records family_id", proc.stderr)
        self.assertFalse(os.path.exists(si.forward_path(self.root, self.entry["survivor_id"])),
                         "no unverifiable evidence is ever appended")


class TestLeaderboard(Base):

    def forward(self, cohort, *slices):
        entry = self.entry_for(cohort)
        for index, doc in enumerate(slices):
            path = self.write_slice("%s-%d.json" % (cohort.replace("/", "-"), index),
                                    self.slice_doc(entry, *doc))
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

    def test_avg_trades_per_year_uses_registered_inclusive_window(self):
        rec = survivor(A)
        rec["metrics"]["full"]["episodes"] = 140
        make_family(self.root, "fam-a", [rec], data_end="2026-09-10")
        _, rows = self.board()
        years = ((datetime.date(2026, 9, 10) - datetime.date(2022, 1, 1)).days + 1) / 365.25
        self.assertAlmostEqual(rows[0]["full"]["avg_trades_per_year"], 140 / years, places=10)

    def test_full_annualized_return_is_preserved_as_frozen_evidence(self):
        rec = survivor(A)
        rec["metrics"]["full"]["annualized_return"] = 0.1234
        make_family(self.root, "fam-a", [rec])
        _, rows = self.board()
        self.assertEqual(rows[0]["full"]["annualized_return"], 0.1234)

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
        path = self.write_slice("degraded.json", self.slice_doc(
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
        retuned = self.slice_doc(incumbent, "2026-09-11", "2026-09-20")
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


class TestWriteBoundary(Base):
    """Contract 27.1: this layer may only write under `_survivors/**` (audit finding F1)."""

    def test_index_out_cannot_escape_the_survivors_boundary(self):
        path = make_family(self.root, "fam-a", a_v2_like_bundle())
        round_dir = os.path.dirname(path)
        verdict = write_json(os.path.join(round_dir, "verdict.json"),
                             {"kind": "round_verdict", "verdict": "PASS"})
        before = {"bundle": si.sha256_file(path), "verdict": si.sha256_file(verdict)}
        for target in (verdict, path, os.path.join(self.root, "outside.json"),
                       os.path.join(self.root, "fam-a", "leaderboard.json"),
                       os.path.join(self.root, "_handoff", "survivor-index.json")):
            proc = self.run_cli(INDEX_CLI, "--out", target)
            self.assertEqual(proc.returncode, 1, target)
            self.assertIn("outside the reserved post-survivor write boundary", proc.stderr)
        self.assertEqual(si.sha256_file(path), before["bundle"],
                         "the frozen bundle is never rewritten by an escaped --out")
        self.assertEqual(si.sha256_file(verdict), before["verdict"],
                         "the frozen verdict is never rewritten by an escaped --out")

        # a symlink inside _survivors is resolved rather than trusted
        os.makedirs(os.path.join(self.root, "_survivors"), exist_ok=True)
        link = os.path.join(self.root, "_survivors", "escape.json")
        os.symlink(verdict, link)
        proc = self.run_cli(INDEX_CLI, "--out", link)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("outside the reserved post-survivor write boundary", proc.stderr)
        self.assertEqual(si.sha256_file(verdict), before["verdict"])

        # a `..` segment cannot reach out of the boundary either
        proc = self.run_cli(INDEX_CLI, "--out", os.path.join(self.root, "_survivors", "..", "fam-a",
                                                             "rounds", "index.json"))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("outside the reserved post-survivor write boundary", proc.stderr)

        # the boundary is not "refuse everything": the canonical path inside it still works
        ok = self.run_cli(INDEX_CLI, "--json")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertTrue(os.path.isfile(si.index_path(self.root)))
        inside = os.path.join(self.root, "_survivors", "scratch", "index.json")
        ok = self.run_cli(INDEX_CLI, "--out", inside, "--json")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertTrue(os.path.isfile(inside))

    def test_leaderboard_out_dir_cannot_escape_the_survivors_boundary(self):
        path = make_family(self.root, "fam-a", a_v2_like_bundle())
        round_dir = os.path.dirname(path)
        before = si.sha256_file(path)
        for target in (round_dir, self.root, os.path.join(self.root, "elsewhere")):
            proc = self.run_cli(LEADERBOARD_CLI, "leaderboard", "--out-dir", target)
            self.assertEqual(proc.returncode, 1, target)
            self.assertIn("outside the reserved post-survivor write boundary", proc.stderr)
        self.assertFalse(os.path.exists(os.path.join(round_dir, "leaderboard.json")))
        self.assertFalse(os.path.exists(os.path.join(round_dir, "leaderboard.csv")))
        self.assertFalse(os.path.exists(os.path.join(self.root, "elsewhere", "leaderboard.json")))
        self.assertEqual(si.sha256_file(path), before)

        inside = os.path.join(self.root, "_survivors", "scratch")
        ok = self.run_cli(LEADERBOARD_CLI, "leaderboard", "--out-dir", inside, "--json")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertTrue(os.path.isfile(os.path.join(inside, "leaderboard.json")))

    def test_symlinked_survivors_root_cannot_escape(self):
        # The v1.5.1 residual (audit t_346bcc04 finding F1): `_survivors` ITSELF was a symlink onto
        # the frozen round, so realpath resolved the boundary onto that round and every
        # "in-boundary" write landed on frozen artifacts with rc=0.
        path = make_family(self.root, "fam-a", a_v2_like_bundle())
        round_dir = os.path.dirname(path)
        verdict = write_json(os.path.join(round_dir, "verdict.json"),
                             {"kind": "round_verdict", "verdict": "PASS"})
        before = {"bundle": si.sha256_file(path), "verdict": si.sha256_file(verdict)}
        reserved = os.path.join(self.root, si.SURVIVORS_DIRNAME)
        os.symlink(round_dir, reserved)

        proc = self.run_cli(INDEX_CLI, "--out", os.path.join(reserved, "verdict.json"))
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("is a symlink", proc.stderr)
        self.assertIn("outside the reserved post-survivor write boundary", proc.stderr)

        proc = self.run_cli(LEADERBOARD_CLI, "leaderboard", "--out-dir", reserved)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("is a symlink", proc.stderr)

        # the append path is a writer too: it may not create forward/ inside the frozen round
        entry = self.entry_for(B)
        slice_path = self.write_slice("escape-root.json",
                                      self.slice_doc(entry, "2026-09-11", "2026-09-30"))
        proc = self.run_cli(LEADERBOARD_CLI, "forward", "--survivor-id", entry["survivor_id"],
                            "--slice", slice_path)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("is a symlink", proc.stderr)

        self.assertFalse(os.path.exists(os.path.join(round_dir, "forward")))
        self.assertFalse(os.path.exists(os.path.join(round_dir, "leaderboard.json")))
        self.assertFalse(os.path.exists(os.path.join(round_dir, "leaderboard.csv")))
        self.assertFalse(os.path.exists(os.path.join(round_dir, "survivor-index.json")))
        self.assertEqual(si.sha256_file(path), before["bundle"],
                         "the frozen bundle is never rewritten through a symlinked _survivors root")
        self.assertEqual(si.sha256_file(verdict), before["verdict"],
                         "the frozen verdict is never rewritten through a symlinked _survivors root")

        # the control: a real `_survivors` directory (same root, same bundles) still writes
        os.unlink(reserved)
        os.makedirs(reserved)
        ok = self.run_cli(INDEX_CLI, "--json")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertTrue(os.path.isfile(si.index_path(self.root)))


class TestGenericFamilyLeaderboard(Base):
    """F1 remediation: true B-shaped rows must use generic canonical JSON CSV columns,
    never legacy A window/discount columns.  These tests exercise the full
    index -> leaderboard -> CSV pipeline with a non-A family fixture."""

    B_FAMILY = "ema-crossover-walkforward-momentum-long-short-v2"

    def b_contract(self):
        return {
            "parameter_contract_version": 1,
            "family_id": self.B_FAMILY,
            "contract_ref": "B v2 generic contract",
            "research_axes_ordered": [
                {"name": "ema_pair", "kind": "composite",
                 "members": ["ema_fast", "ema_slow"],
                 "registered_values": [[5, 40], [10, 60], [15, 80]],
                 "row_fields": ["ema_fast", "ema_slow"]},
                {"name": "walk_forward", "kind": "composite",
                 "members": ["wf_train_days", "wf_test_days"],
                 "registered_values": [[252, 63], [126, 31]],
                 "row_fields": ["wf_train_days", "wf_test_days"]},
                {"name": "spacing_pct", "kind": "atomic", "members": ["spacing_pct"],
                 "registered_values": [0.01, 0.02, 0.03], "row_fields": ["spacing_pct"]},
                {"name": "size_multiplier", "kind": "atomic", "members": ["size_multiplier"],
                 "registered_values": [1.0, 1.1], "row_fields": ["size_multiplier"]},
                {"name": "breakeven_tp_pct", "kind": "atomic", "members": ["breakeven_tp_pct"],
                 "registered_values": [0.01, 0.02, 0.03], "row_fields": ["breakeven_tp_pct"]},
                {"name": "invalidation_pct", "kind": "atomic", "members": ["invalidation_pct"],
                 "registered_values": [0.05, 0.10], "row_fields": ["invalidation_pct"]},
            ],
            "row_fields": ["ema_fast", "ema_slow", "wf_train_days", "wf_test_days",
                           "spacing_pct", "size_multiplier", "breakeven_tp_pct",
                           "invalidation_pct"],
            "composite_map": {"ema_pair": ["ema_fast", "ema_slow"],
                              "walk_forward": ["wf_train_days", "wf_test_days"]},
            "strategy_param_fields": ["ema_fast", "ema_slow", "wf_train_days", "wf_test_days"],
            "dca_param_fields": ["spacing_pct", "size_multiplier", "breakeven_tp_pct",
                                 "invalidation_pct"],
            "canonical_recipe": {"sort_keys": True, "separators": (",", ":"),
                                 "ensure_ascii": False,
                                 "numeric_rule": "JSON number finite, bool excluded"},
            "row_match_recipe": {"keys": ["symbol", "timeframe", "ema_fast", "ema_slow",
                                          "wf_train_days", "wf_test_days",
                                          "spacing_pct", "size_multiplier",
                                          "breakeven_tp_pct", "invalidation_pct"],
                                 "equality": "exact"},
            "non_params": ["symbol", "timeframe"],
            "domain_cardinality": {"strategy": 6, "dca": 36, "per_cohort": 216},
        }

    def b_survivor(self, cohort, ema_fast=10, ema_slow=60, wf_train=252, wf_test=63,
                   dca=None):
        dca = dca or {"spacing_pct": 0.02, "size_multiplier": 1.1,
                      "breakeven_tp_pct": 0.03, "invalidation_pct": 0.10}
        return {
            "cohort": cohort, "outcome": "SURVIVOR", "no_winner_reason": None,
            "cull_reasons": [],
            "winner": {"ema_fast": ema_fast, "ema_slow": ema_slow,
                       "wf_train_days": wf_train, "wf_test_days": wf_test, **dca},
            "neighbourhood": {"neighbours": 6, "agreeing": 5,
                              "same_sign_fraction": 0.85, "passed": True},
            "metrics": {
                "historical": {"net_pnl": 500.0, "sharpe": 1.5, "episodes": 80,
                               "max_dd_pct": -0.08},
                "oos": {"net_pnl": 200.0, "sharpe": 1.8, "episodes": 40,
                        "max_dd_pct": -0.05},
                "full": {"net_pnl": 700.0, "sharpe": 2.0, "episodes": 120,
                         "max_dd_pct": -0.10},
                "robustness": {g: {"net_pnl": 50.0, "sharpe": 0.8, "max_dd_pct": -0.12}
                               for g in ("fee_2x", "funding_2x",
                                         "entry_delay_1_bar", "slippage_2ticks")},
            },
        }

    def test_b_index_builds_with_generic_contract(self):
        """B family round-spec includes parameter_contract; index loads it."""
        make_family(self.root, self.B_FAMILY,
                    [self.b_survivor("BTCUSDT/1h"), self.b_survivor("SOLUSDT/4h",
                     ema_fast=15, ema_slow=80)],
                    parameter_contract=self.b_contract())
        index, problems = si.build(self.root)
        self.assertEqual(problems, [], problems)
        self.assertEqual(index["survivor_count"], 2)
        entry = [e for e in index["survivors"]
                 if e["cohort"] == "BTCUSDT/1h"][0]
        # B-shaped strategy_params: ema_fast/ema_slow + wf_train/wf_test, NOT window/discount
        self.assertEqual(entry["strategy_params"],
                         {"ema_fast": 10, "ema_slow": 60,
                          "wf_train_days": 252, "wf_test_days": 63})
        self.assertNotIn("window", entry["strategy_params"])
        self.assertNotIn("discount", entry["strategy_params"])

    def test_b_leaderboard_csv_uses_generic_columns(self):
        """Leaderboard CSV for B family uses canonical JSON columns, not legacy A columns."""
        make_family(self.root, self.B_FAMILY,
                    [self.b_survivor("BTCUSDT/1h")],
                    parameter_contract=self.b_contract())
        doc, rows, problems = sl.build(self.root)
        self.assertEqual(problems, [], problems)
        self.assertEqual(len(rows), 1)
        # csv_columns with a non-A contract must return generic columns
        contract, _cp, _il = pc.load_contract_from_round_spec(
            {"family_id": self.B_FAMILY, "parameter_contract": self.b_contract()})
        cols = sl.csv_columns(contract=contract)
        self.assertIn("strategy_params_canonical_json", cols)
        self.assertIn("strategy_params_sha256", cols)
        self.assertNotIn("strategy_window", cols)
        self.assertNotIn("strategy_discount", cols)
        # csv_rows must produce generic output without KeyError
        csv_rows = sl.csv_rows(rows, contract=contract)
        self.assertEqual(len(csv_rows), 1)
        self.assertIn("strategy_params_canonical_json", csv_rows[0])
        self.assertNotIn("strategy_window", csv_rows[0])

    def test_b_leaderboard_csv_with_none_contract_uses_generic(self):
        """F1: csv_columns(None) must NOT default to legacy A columns."""
        cols_none = sl.csv_columns(contract=None)
        self.assertIn("strategy_params_canonical_json", cols_none)
        self.assertNotIn("strategy_window", cols_none)

    def test_b_leaderboard_e2e_write_check(self):
        """Full B-shaped leaderboard write + check roundtrip."""
        make_family(self.root, self.B_FAMILY,
                    [self.b_survivor("BTCUSDT/1h"), self.b_survivor("SOLUSDT/4h",
                     ema_fast=15, ema_slow=80)],
                    parameter_contract=self.b_contract())
        proc = self.run_cli(LEADERBOARD_CLI, "leaderboard", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["survivor_count"], 2)
        # check roundtrip
        check = self.run_cli(LEADERBOARD_CLI, "leaderboard", "--check")
        self.assertEqual(check.returncode, 0, check.stderr)
        # CSV must have generic columns
        with open(os.path.join(self.root, "_survivors", "leaderboard.csv")) as fh:
            header = fh.readline().strip()
        self.assertIn("strategy_params_canonical_json", header)
        self.assertNotIn("strategy_window", header)

    def test_csv_contract_needs_every_row_to_be_legacy_a(self):
        """R1 residual (v1.8 re-audit): one legacy A row is not enough - a mixed root must not
        take the legacy per-param columns."""
        a_row = {"family_id": pc.LEGACY_A_FAMILY_ID}
        b_row = {"family_id": self.B_FAMILY}
        self.assertEqual(sl.csv_contract_for([a_row]), pc.LEGACY_A_CONTRACT)
        self.assertIsNone(sl.csv_contract_for([]))
        self.assertIsNone(sl.csv_contract_for([b_row]))
        self.assertIsNone(sl.csv_contract_for([a_row, b_row]))
        self.assertIsNone(sl.csv_contract_for([b_row, a_row]))

    def test_mixed_a_b_root_uses_generic_columns_for_every_row(self):
        """R1 residual (v1.8 re-audit): with a legacy-A row ranked FIRST the old rows[0] check
        took the legacy CSV branch and the B row raised KeyError 'window' - leaderboard.json was
        already written, leaderboard.csv never was."""
        make_family(self.root, pc.LEGACY_A_FAMILY_ID,
                    [survivor("BTCUSDT/1h", oos=(3.0, 100.0, 40))])
        make_family(self.root, self.B_FAMILY, [self.b_survivor("SOLUSDT/4h")],
                    parameter_contract=self.b_contract())
        proc = self.run_cli(LEADERBOARD_CLI, "leaderboard", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["survivor_count"], 2)
        # A's OOS sharpe 3.0 outranks B's 1.8, so A really is rows[0] (the failing shape)
        with open(os.path.join(self.root, "_survivors", "leaderboard.json")) as fh:
            doc = json.load(fh)
        self.assertEqual(doc["entries"][0]["family_id"], pc.LEGACY_A_FAMILY_ID)
        self.assertEqual(doc["entries"][1]["family_id"], self.B_FAMILY)
        # both halves of the pair are produced, with generic columns for all rows
        csv_path = os.path.join(self.root, "_survivors", "leaderboard.csv")
        with open(csv_path) as fh:
            header = fh.readline().strip()
            body = [line for line in fh.read().splitlines() if line]
        self.assertIn("strategy_params_canonical_json", header)
        self.assertNotIn("strategy_window", header)
        self.assertEqual(len(body), 2)
        check = self.run_cli(LEADERBOARD_CLI, "leaderboard", "--check")
        self.assertEqual(check.returncode, 0, check.stderr)

    def test_legacy_a_only_root_keeps_its_per_param_columns(self):
        """A compatibility: the R1 fix must not push an all-legacy-A root onto generic columns."""
        make_family(self.root, pc.LEGACY_A_FAMILY_ID, a_v2_like_bundle())
        proc = self.run_cli(LEADERBOARD_CLI, "leaderboard", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        with open(os.path.join(self.root, "_survivors", "leaderboard.csv")) as fh:
            header = fh.readline().strip()
        self.assertIn("strategy_window", header)
        self.assertIn("strategy_discount", header)
        self.assertNotIn("strategy_params_canonical_json", header)
        check = self.run_cli(LEADERBOARD_CLI, "leaderboard", "--check")
        self.assertEqual(check.returncode, 0, check.stderr)

    def test_malformed_composite_map_contract_refuses_instead_of_traceback(self):
        """R2 residual (v1.8 re-audit): a mixed-type composite_map value must reach the caller
        as accumulated problems (index refused), never as a TypeError out of sorted()."""
        contract = self.b_contract()
        contract["composite_map"] = {"ema_pair": [1, "ema_slow"],
                                     "walk_forward": ["wf_train_days", "wf_test_days"]}
        make_family(self.root, self.B_FAMILY, [self.b_survivor("BTCUSDT/1h")],
                    parameter_contract=contract)
        index, problems = si.build(self.root)
        self.assertIsNone(index)
        self.assertTrue(any("parameter_contract invalid" in p for p in problems), problems)
        self.assertTrue(any("composite_map" in p for p in problems), problems)
        proc = self.run_cli(INDEX_CLI, "--json")
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("REFUSED", proc.stderr)


class TestDirectOwnership(Base):
    """v2.0 direct (card-free) ownership: family/round/run identity instead of a Kanban id.

    A direct family has no card, so neither its bundle nor its forward source run may carry one;
    historical card-owned families keep the strict two-sided `kanban_task_id` checks above.
    """

    def test_direct_family_indexes_and_takes_forward_evidence_without_card_ids(self):
        make_family(self.root, "direct-fam-1", a_v2_like_bundle(), direct=True)
        index, problems = si.build(self.root)
        self.assertEqual(problems, [], problems)
        self.assertEqual(index["survivor_count"], 2)
        entries = index["survivors"]
        for entry in entries:
            self.assertIsNone(entry["kanban_task_id"])
            with open(entry["bundle_path"]) as fh:
                published = json.load(fh)
            for key in ("kanban_task_id", "kanban_board", "task_id"):
                self.assertFalse(published.get(key), key)
        # forward evidence produced by a card-free run appends, and the leaderboard ranks normally
        entry = [e for e in entries if e["cohort"] == B][0]
        doc = bare_slice(entry, "2026-09-11", "2026-09-30")
        doc["source_run"] = make_forward_run(self.root, entry, doc,
                                             run_id="fw-direct-u1", direct=True)
        path = self.write_slice("direct-forward.json", doc)
        proc = self.run_cli(LEADERBOARD_CLI, "forward", "--survivor-id",
                            entry["survivor_id"], "--slice", path, "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["result"], "appended")
        board, rows, problems = sl.build(self.root)
        self.assertEqual(problems, [], problems)
        self.assertEqual(board["survivor_count"], 2)
        proc = self.run_cli(LEADERBOARD_CLI, "leaderboard", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        with open(os.path.join(self.root, "_survivors", "leaderboard.json")) as fh:
            doc = json.load(fh)
        self.assertEqual(doc["entries"][0]["family_id"], "direct-fam-1")

    def test_direct_bundle_with_card_identity_fails_closed(self):
        make_family(self.root, "direct-fam-2", a_v2_like_bundle(), direct=True,
                    mutate=lambda b: resign(dict(b, kanban_task_id="t_leaked")))
        index, problems = si.build(self.root)
        self.assertIsNone(index)
        self.assertTrue(any("carries card ownership" in p for p in problems), problems)

    def test_direct_source_run_with_card_identity_is_refused(self):
        make_family(self.root, "direct-fam-3", a_v2_like_bundle(), direct=True)
        entry = [e for e in self.entries() if e["cohort"] == B][0]
        doc = bare_slice(entry, "2026-09-11", "2026-09-30")
        doc["source_run"] = make_forward_run(self.root, entry, doc,
                                             run_id="fw-direct-bad-u1", direct=True)
        doc["source_run"]["kanban_task_id"] = "t_sneaky"
        path = self.write_slice("direct-carded.json", doc)
        proc = self.run_cli(LEADERBOARD_CLI, "forward", "--survivor-id",
                            entry["survivor_id"], "--slice", path)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("card-free direct evidence", proc.stderr)
        self.assertFalse(os.path.exists(si.forward_path(self.root, entry["survivor_id"])))


if __name__ == "__main__":
    unittest.main(verbosity=2)
