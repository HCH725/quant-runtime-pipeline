#!/usr/bin/env python3
"""Logic-layer check for the v1.4.0 frozen survivor bundle (runtime/survivor_bundle.py).

stdlib unittest only; no market data, no container, no Qlib, no /results.  The bundle is the
artifact that decides what "all survivors advance" means in practice, so these checks pin:

  * a bundle carries EVERY survivor of the round, in the recorded order, with no ranking and
    no dropped survivor (and the identity is order-sensitive, i.e. the writer does not sort),
  * the v1.4.0 disposition/verdict mapping (0 -> REJECT, >=1 -> PASS) and the disappearance
    of the old "more than one survivor forces performance_claimable false" gate,
  * the v1.4.1 identity recipe: the published `bundle_identity_sha256` is the canonical JSON
    digest of everything EXCEPT `generated_at_utc` and the identity column itself, recomputed
    from the persisted file by an independent stdlib implementation (the F1 regression pin),
  * the v1.4.2 replay-comparison scope pin: of the producer-identity fields, only the top-level
    `contract` and the nested `generator.sha256` may be ignored, so an edit to `generator.path`
    that recomputes the public identity to look self-consistent is still refused by `--check`
    and by the writer, and a non-dict `generator` is compared rather than normalised away (the
    F2 regression pin),
  * fail-closed behaviour: a non-terminal attempt, a mismatched survivor count/order, false
    assertions, incomplete coverage, an attempt that records no survivor at all, or a
    disagreeing disposition band all refuse to freeze,
  * tamper control: a frozen bundle whose measurement, or whose published identity, no longer
    matches the attempt's artifacts is refused by `--check` (and never rewritten), while a
    corrected writer with unchanged measurement still recognises its own frozen content,
  * idempotency: an identical bundle is a no-op, a different one is never overwritten.
"""
import copy
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))  # runtime/tests -> runtime -> repo
BUNDLE = os.path.join(REPO, "runtime", "survivor_bundle.py")

_spec = importlib.util.spec_from_file_location("survivor_bundle", BUNDLE)
sb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sb)

A = "BTCUSDT/1h"
B = "SOLUSDT/4h"

# Independent, stdlib-only reimplementation of the contract 10.8 identity recipe.  It is
# deliberately NOT sb.identity(): the point of the check is that a third party reproduces the
# published value from the persisted file without using this repo's writer code.
IDENTITY_EXCLUDED = ("generated_at_utc", "bundle_identity_sha256")


def digest(obj):
    canonical = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def recipe_identity(bundle):
    return digest({k: v for k, v in bundle.items() if k not in IDENTITY_EXCLUDED})


def survivor(label):
    return {"cohort": label, "outcome": "SURVIVOR", "no_winner_reason": None, "cull_reasons": [],
            "winner": {"window": 20, "discount": 0.03, "spacing_pct": 0.01, "size_multiplier": 1.0,
                       "breakeven_tp_pct": 0.01, "invalidation_pct": 0.1},
            "neighbourhood": {"passed": True, "same_sign_fraction": 0.75},
            "metrics": {"historical": {"net_pnl": 1.0, "sharpe": 1.0, "episodes": 100},
                        "oos": {"net_pnl": 1.0, "sharpe": 1.0, "episodes": 30},
                        "full": {"net_pnl": 1.0, "sharpe": 1.0, "episodes": 130},
                        "robustness": {s: {"net_pnl": 1.0} for s in
                                       ("fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks")}}}


def make_attempt(root, survivors, count=None, coverage_complete=True, disposition=None,
                 verdict_recommendation=None, claimable=None, sentinel="DONE",
                 assertions=None, extra_cohorts=None, direct=False):
    attempt = os.path.join(root, "rounds", "fam-r1", "attempts", "fam-r1-u1")
    os.makedirs(os.path.join(attempt, "artifacts"), exist_ok=True)
    n = len(survivors) if count is None else count
    labels = [s["cohort"] for s in survivors]
    run_spec = {"schema_version": 1, "family_id": "fam", "round_id": "fam-r1", "run_id": "fam-r1-u1",
                "selector_version": "cohort-selector-v1", "disposition_version": "cohort-disposition-v1"}
    if not direct:  # a card-free direct run-spec carries no card fields at all (v2.0)
        run_spec["task_id"] = "t_test"
        run_spec["kanban_board"] = "quant-strategy-research"
    result = {"schema_version": 1, "family_id": "fam", "round_id": "fam-r1", "run_id": "fam-r1-u1",
              "selector_version": "cohort-selector-v1", "disposition_version": "cohort-disposition-v1",
              "cohort_survivors": labels, "cohort_survivor_count": n,
              "disposition": sb.band_for(n) if disposition is None else disposition,
              "verdict_recommendation": (sb.verdict_for(n) if verdict_recommendation is None
                                         else verdict_recommendation),
              "performance_claimable_recommendation": ((n > 0) if claimable is None else claimable),
              "coverage_complete": coverage_complete,
              "case_evaluations_total": 103680, "expected_case_evaluations": 103680}
    cohort_results = [{"cohort": c, "outcome": "SURVIVOR"} for c in labels]
    cohort_results += [{"cohort": c, "outcome": "CULLED"} for c in (extra_cohorts or [])]
    assertions = {"coverage_complete": True, "selector_deterministic": True} if assertions is None else assertions
    for name, doc in (("run-spec.json", run_spec), ("result.json", result)):
        with open(os.path.join(attempt, name), "w") as fh:
            json.dump(doc, fh)
    for name, doc in (("artifacts/cohort_survivors.json", survivors),
                      ("artifacts/cohort_results.json", cohort_results),
                      ("artifacts/assertions.json", assertions)):
        with open(os.path.join(attempt, name), "w") as fh:
            json.dump(doc, fh)
    with open(os.path.join(attempt, sentinel), "w") as fh:
        fh.write("{}\n")
    return attempt


class TestSurvivorBundle(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="survivor-bundle-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_two_survivors_are_both_frozen_in_recorded_order(self):
        attempt = make_attempt(self.root, [survivor(A), survivor(B)])
        bundle, problems = sb.build(attempt)
        self.assertEqual(problems, [])
        self.assertEqual(bundle["survivor_count"], 2)
        self.assertEqual([s["cohort"] for s in bundle["survivors"]], [A, B])
        self.assertEqual(bundle["disposition_band"], "MULTIPLE_SURVIVORS")
        self.assertEqual(bundle["verdict"], "PASS")
        self.assertTrue(bundle["all_survivors_advance"])
        self.assertIsNone(bundle["ranking"])
        self.assertEqual(bundle["contract_section"], "7.3 / 10.8")
        self.assertIn("v1.5.0", bundle["contract"])

    def test_one_survivor_band_and_verdict(self):
        bundle, problems = sb.build(make_attempt(self.root, [survivor(A)]))
        self.assertEqual(problems, [])
        self.assertEqual(bundle["disposition_band"], "SURVIVOR_FOUND")
        self.assertEqual(bundle["verdict"], "PASS")

    def test_direct_run_freezes_a_bundle_without_any_card_keys(self):
        # v2.0 direct (contract 27.2 v-next): a card-free run-spec freezes a bundle that carries
        # NO card keys at all - not null - so the index can validate it by family/round/run
        # identity while a leaked non-null card id would still fail closed there.
        bundle, problems = sb.build(make_attempt(self.root, [survivor(A)], direct=True))
        self.assertEqual(problems, [], problems)
        self.assertNotIn("kanban_task_id", bundle)
        self.assertNotIn("kanban_board", bundle)
        self.assertEqual(bundle["run_id"], "fam-r1-u1")

    def test_carded_run_still_publishes_both_card_keys(self):
        # Historical pin: the direct branch must not leak into card-owned runs.
        bundle, problems = sb.build(make_attempt(self.root, [survivor(A)]))
        self.assertEqual(problems, [], problems)
        self.assertEqual(bundle["kanban_task_id"], "t_test")
        self.assertEqual(bundle["kanban_board"], "quant-strategy-research")

    def test_zero_survivor_attempt_is_refused_and_freezes_nothing(self):
        # Card t_e86b05a8: a round with no cohort survivor has no frozen survivor bundle.  The
        # refusal must name the survivor count itself; the disposition-band check is NOT the
        # reason (a band-correct 0-survivor attempt used to be written as an empty bundle).
        attempt = make_attempt(self.root, [], extra_cohorts=["ETHUSDT/5m"])
        bundle, problems = sb.build(attempt)
        self.assertIsNone(bundle)
        self.assertTrue(any(p.startswith("0 survivors") for p in problems), problems)
        self.assertEqual([p for p in problems if "disposition band mismatch" in p], [])
        proc = subprocess.run([sys.executable, BUNDLE, "--attempt-dir", attempt, "--json"],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("0 survivors", proc.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.root, "rounds", "fam-r1",
                                                     "survivor-bundle.json")),
                         "a 0-survivor attempt must freeze nothing")

    def test_bundle_identity_is_order_sensitive_so_nothing_is_sorted_or_ranked(self):
        attempt = make_attempt(self.root, [survivor(A), survivor(B)])
        first, _ = sb.build(attempt)
        second, _ = sb.build(attempt)
        self.assertEqual(first["bundle_identity_sha256"], second["bundle_identity_sha256"],
                         "the identity must be reproducible, independent of the clock")
        flipped = os.path.join(self.root, "flipped")
        attempt2 = make_attempt(flipped, [survivor(B), survivor(A)])
        other, _ = sb.build(attempt2)
        self.assertNotEqual(first["bundle_identity_sha256"], other["bundle_identity_sha256"],
                            "a permuted survivor list must not collapse to the same bundle: "
                            "the writer must not sort (no ranking)")
        self.assertEqual([s["cohort"] for s in other["survivors"]], [B, A])

    def test_identity_excludes_its_own_column_so_the_recipe_is_not_recursive(self):
        bundle, problems = sb.build(make_attempt(self.root, [survivor(A), survivor(B)]))
        self.assertEqual(problems, [])
        without_column = {k: v for k, v in bundle.items() if k != "bundle_identity_sha256"}
        self.assertEqual(sb.identity(bundle), sb.identity(without_column),
                         "the published identity must not depend on its own value")
        self.assertEqual(bundle["bundle_identity_sha256"], sb.identity(bundle))
        self.assertEqual(sb.identity_problems(bundle, "bundle"), [])
        forged = dict(bundle, bundle_identity_sha256="sha256:" + "0" * 64)
        self.assertTrue(sb.identity_problems(forged, "forged"), "a stale published identity must fail")

    def test_published_identity_equals_the_recipe_recomputed_from_the_persisted_file(self):
        # F1 regression pin (auditor t_0bd01630): the recipe must name BOTH exclusions.  A
        # reader who removes only generated_at_utc - the wording Contract v1.4.0 shipped, and
        # what the writer's own docstring implied - hashes the identity column into its own
        # input and can never reproduce the published digest.
        attempt = make_attempt(self.root, [survivor(A), survivor(B)])
        proc = subprocess.run([sys.executable, BUNDLE, "--attempt-dir", attempt, "--json"],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        with open(payload["bundle_path"]) as fh:
            persisted = json.load(fh)

        self.assertEqual(persisted["bundle_identity_sha256"], recipe_identity(persisted))
        self.assertEqual(payload["bundle_identity_sha256"], persisted["bundle_identity_sha256"])
        self.assertEqual(payload["identity_recomputed_from_persisted_file"],
                         persisted["bundle_identity_sha256"])
        self.assertTrue(payload["identity_recipe_matches"])
        only_timestamp_removed = digest({k: v for k, v in persisted.items()
                                         if k != "generated_at_utc"})
        self.assertNotEqual(only_timestamp_removed, persisted["bundle_identity_sha256"],
                            "the v1.4.0 wording (drop generated_at_utc only) is not the recipe: "
                            "it hashes the identity column into its own input")

    def test_check_refuses_a_tampered_frozen_bundle(self):
        attempt = make_attempt(self.root, [survivor(A), survivor(B)])
        proc = subprocess.run([sys.executable, BUNDLE, "--attempt-dir", attempt, "--json"],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)["bundle_path"]
        with open(out) as fh:
            pristine = json.load(fh)

        def check():
            return subprocess.run([sys.executable, BUNDLE, "--attempt-dir", attempt, "--check"],
                                  capture_output=True, text=True)

        def rewrite(doc):
            with open(out, "w") as fh:
                json.dump(doc, fh)

        # (a) a measurement edited while the published identity is left stale
        stale = copy.deepcopy(pristine)
        stale["verdict"] = "REJECT"
        rewrite(stale)
        proc = check()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("bundle_identity_sha256", proc.stderr)

        # (b) the same edit with the identity column recomputed to look self-consistent: still
        # refused, because the measurement no longer matches the attempt's artifacts
        forged = copy.deepcopy(stale)
        forged["bundle_identity_sha256"] = recipe_identity(forged)
        rewrite(forged)
        proc = check()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("does not match the attempt", proc.stderr)

        # (c) an edit to non-measurement prose is still caught by the published identity
        prose = copy.deepcopy(pristine)
        prose["note"] = "one survivor was silently dropped"
        rewrite(prose)
        self.assertEqual(check().returncode, 1)

        # (d) the untouched frozen bundle passes, and reports its own published identity
        rewrite(pristine)
        proc = subprocess.run([sys.executable, BUNDLE, "--attempt-dir", attempt, "--check",
                               "--json"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["result"], "check_clean")
        self.assertEqual(payload["bundle_identity_sha256"], pristine["bundle_identity_sha256"])
        self.assertTrue(payload["identity_recipe_matches"])

    def test_a_corrected_writer_still_recognises_its_own_frozen_content(self):
        # The remediation scenario itself: the measurement is untouched, but the writer changed
        # (so the contract string and the writer's own hash in the frozen bundle no longer match
        # what the corrected writer would emit).  Re-running must be a no-op and --check clean -
        # otherwise the fix could only be shipped by rewriting an immutable artifact.
        attempt = make_attempt(self.root, [survivor(A), survivor(B)])
        bundle, problems = sb.build(attempt)
        self.assertEqual(problems, [])
        out = os.path.join(os.path.dirname(os.path.dirname(attempt)), "survivor-bundle.json")
        self.assertEqual(sb.write_bundle(copy.deepcopy(bundle), out), "written")

        newer = copy.deepcopy(bundle)
        newer["contract"] = "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md v1.4.99"
        newer["generator"] = {"path": "runtime/survivor_bundle.py", "sha256": "sha256:" + "1" * 64}
        newer["bundle_identity_sha256"] = sb.identity(newer)
        self.assertNotEqual(newer["bundle_identity_sha256"], bundle["bundle_identity_sha256"])
        self.assertEqual(sb.write_bundle(newer, out), "already_identical")
        with open(out) as fh:
            on_disk = json.load(fh)
        self.assertEqual(on_disk["bundle_identity_sha256"], bundle["bundle_identity_sha256"],
                         "the frozen file must keep its own provenance")
        self.assertEqual(on_disk["contract"], bundle["contract"])

        proc = subprocess.run([sys.executable, BUNDLE, "--attempt-dir", attempt, "--check", "--json"],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["bundle_identity_sha256"], bundle["bundle_identity_sha256"])
        self.assertTrue(payload["identity_recipe_matches"])

    def test_check_refuses_a_self_consistent_generator_path_tamper(self):
        # F2 regression pin (auditor t_3edafbb9): contract 10.8 lets the replay comparison ignore
        # ONLY two producer-identity keys - top-level `contract` and the nested
        # `generator.sha256`.  `generator.path` is provenance of the measurement and stays inside
        # the comparison, so an edit that recomputes the public identity to look self-consistent
        # must still be refused by both `--check` and the writer.
        attempt = make_attempt(self.root, [survivor(A), survivor(B)])
        bundle, problems = sb.build(attempt)
        self.assertEqual(problems, [])
        out = os.path.join(os.path.dirname(os.path.dirname(attempt)), "survivor-bundle.json")
        self.assertEqual(sb.write_bundle(copy.deepcopy(bundle), out), "written")

        with open(out) as fh:
            tampered = json.load(fh)
        self.assertEqual(tampered["generator"]["path"], "runtime/survivor_bundle.py")
        tampered["generator"]["path"] = "tampered/other_writer.py"
        # only the PUBLIC identity is recomputed, with the independent 10.8 recipe: this is the
        # attacker who hides the edit from the "does the file agree with itself?" check
        tampered["bundle_identity_sha256"] = recipe_identity(tampered)
        with open(out, "w") as fh:
            json.dump(tampered, fh)

        proc = subprocess.run([sys.executable, BUNDLE, "--attempt-dir", attempt, "--check", "--json"],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("does not match the attempt", proc.stderr)

        # ... and the writer must refuse to treat it as its own frozen content
        self.assertEqual(sb.write_bundle(sb.build(attempt)[0], out), "refused_different_bytes")
        proc = subprocess.run([sys.executable, BUNDLE, "--attempt-dir", attempt],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("already exists with different content", proc.stderr)
        with open(out) as fh:
            self.assertEqual(json.load(fh)["generator"]["path"], "tampered/other_writer.py",
                             "a refused bundle is never rewritten")

        # a non-dict generator is compared as it stands, not normalised away
        self.assertNotEqual(sb.content_identity({"generator": "runtime/survivor_bundle.py"}),
                            sb.content_identity({"generator": "tampered/other_writer.py"}))

        # control: the OTHER 10.8 exception still behaves - with the measurement untouched, a
        # corrected writer (new contract string, new own-hash, same path) is a no-op and clean
        control_attempt = make_attempt(os.path.join(self.root, "control"), [survivor(A), survivor(B)])
        control_bundle, problems = sb.build(control_attempt)
        self.assertEqual(problems, [])
        control_out = os.path.join(os.path.dirname(os.path.dirname(control_attempt)),
                                   "survivor-bundle.json")
        self.assertEqual(sb.write_bundle(copy.deepcopy(control_bundle), control_out), "written")
        newer = copy.deepcopy(control_bundle)
        newer["contract"] = "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md v1.4.99"
        newer["generator"] = {"path": "runtime/survivor_bundle.py", "sha256": "sha256:" + "3" * 64}
        newer["bundle_identity_sha256"] = sb.identity(newer)
        self.assertEqual(sb.write_bundle(newer, control_out), "already_identical")
        proc = subprocess.run([sys.executable, BUNDLE, "--attempt-dir", control_attempt, "--check",
                               "--json"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["result"], "check_clean")
        self.assertTrue(payload["identity_recipe_matches"])

    def test_multi_survivor_must_not_force_performance_claimable_false(self):
        # the negative control for the v1.3.x gate that v1.4.0 removed
        attempt = make_attempt(self.root, [survivor(A), survivor(B)], claimable=False)
        bundle, problems = sb.build(attempt)
        self.assertIsNone(bundle)
        self.assertTrue(any("must not force" in p for p in problems), problems)

    def test_count_or_order_mismatch_refuses_to_freeze(self):
        attempt = make_attempt(self.root, [survivor(A), survivor(B)], count=1)
        _, problems = sb.build(attempt)
        self.assertTrue(any("cohort_survivor_count" in p for p in problems), problems)

        reversed_result = make_attempt(os.path.join(self.root, "rev"), [survivor(A), survivor(B)])
        path = os.path.join(reversed_result, "result.json")
        with open(path) as fh:
            doc = json.load(fh)
        doc["cohort_survivors"] = [B, A]
        with open(path, "w") as fh:
            json.dump(doc, fh)
        _, problems = sb.build(reversed_result)
        self.assertTrue(any("cohort_survivors" in p for p in problems), problems)

    def test_disposition_band_disagreement_refuses_to_freeze(self):
        attempt = make_attempt(self.root, [survivor(A), survivor(B)],
                               disposition="REJECT / NO_SURVIVOR", verdict_recommendation="FINALIST")
        _, problems = sb.build(attempt)
        self.assertTrue(any("disposition band mismatch" in p for p in problems), problems)
        self.assertTrue(any("verdict recommendation mismatch" in p for p in problems), problems)

    def test_non_terminal_or_incomplete_attempt_refuses_to_freeze(self):
        attempt = make_attempt(os.path.join(self.root, "failed"), [survivor(A)], sentinel="FAILED")
        _, problems = sb.build(attempt)
        self.assertTrue(any("terminally DONE" in p for p in problems), problems)

        partial = make_attempt(os.path.join(self.root, "partial"), [survivor(A)],
                               coverage_complete=False)
        _, problems = sb.build(partial)
        self.assertTrue(any("coverage_complete" in p for p in problems), problems)

        falsey = make_attempt(os.path.join(self.root, "assertions"), [survivor(A)],
                              assertions={"coverage_complete": True, "ending_equity_floor": False})
        _, problems = sb.build(falsey)
        self.assertTrue(any("non-true entries" in p for p in problems), problems)

    def test_missing_source_artifact_refuses_to_freeze(self):
        attempt = make_attempt(self.root, [survivor(A)])
        os.remove(os.path.join(attempt, "artifacts", "cohort_survivors.json"))
        _, problems = sb.build(attempt)
        self.assertTrue(any("missing immutable source artifact" in p for p in problems), problems)

    def test_legacy_multi_survivor_attempt_is_frozen_with_disclosure(self):
        # the real Strategy A v2 attempt ran under contract < v1.4.0 and therefore recorded
        # verdict_recommendation=FINALIST with performance_claimable=false.  That source is
        # immutable: the bundle freezes the v1.4.0 verdict (PASS) and discloses the legacy pair
        # instead of refusing or pretending the source already said PASS.
        attempt = make_attempt(self.root, [survivor(A), survivor(B)],
                               verdict_recommendation="FINALIST", claimable=False)
        bundle, problems = sb.build(attempt)
        self.assertEqual(problems, [])
        self.assertEqual(bundle["verdict"], "PASS")
        self.assertEqual(bundle["disposition_band"], "MULTIPLE_SURVIVORS")
        semantics = bundle["source_attempt_semantics"]
        self.assertIn("pre-v1.4.0", semantics["verdict_mapping"])
        self.assertEqual(semantics["verdict_recommendation_recorded_in_result_json"], "FINALIST")
        self.assertTrue(bundle["source_verdict_not_rewritten"])
        self.assertEqual([s["cohort"] for s in bundle["survivors"]], [A, B])

    def test_new_semantics_attempt_carries_no_legacy_disclosure(self):
        bundle, problems = sb.build(make_attempt(self.root, [survivor(A), survivor(B)]))
        self.assertEqual(problems, [])
        self.assertEqual(bundle["source_attempt_semantics"]["verdict_mapping"], "v1.4.0")

    def test_write_is_idempotent_and_never_overwrites(self):
        attempt = make_attempt(self.root, [survivor(A), survivor(B)])
        bundle, problems = sb.build(attempt)
        self.assertEqual(problems, [])
        out = os.path.join(os.path.dirname(os.path.dirname(attempt)), "survivor-bundle.json")
        self.assertEqual(sb.write_bundle(copy.deepcopy(bundle), out), "written")
        self.assertTrue(os.path.isfile(out))
        again = sb.build(attempt)[0]
        self.assertEqual(sb.write_bundle(again, out), "already_identical")
        with open(out) as fh:
            before = fh.read()
        self.assertEqual(sb.write_bundle(again, out), "already_identical")
        with open(out) as fh:
            self.assertEqual(fh.read(), before, "an existing frozen bundle must not be rewritten")
        tampered = json.loads(before)
        tampered["survivors"] = tampered["survivors"][:1]
        self.assertEqual(sb.write_bundle(tampered, out), "refused_different_bytes")

    def test_cli_writes_at_the_round_level_and_check_reads_it_back(self):
        attempt = make_attempt(self.root, [survivor(A), survivor(B)])
        proc = subprocess.run([sys.executable, BUNDLE, "--attempt-dir", attempt, "--json"],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["result"], "written")
        self.assertEqual(payload["survivors"], [A, B])
        self.assertTrue(payload["bundle_path"].endswith(
            os.path.join("fam-r1", "survivor-bundle.json")), payload["bundle_path"])
        check = subprocess.run([sys.executable, BUNDLE, "--attempt-dir", attempt, "--check", "--json"],
                               capture_output=True, text=True)
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertEqual(json.loads(check.stdout)["result"], "check_clean")

        broken = make_attempt(os.path.join(self.root, "broken"), [survivor(A)],
                              disposition="MULTIPLE_SURVIVORS")
        refused = subprocess.run([sys.executable, BUNDLE, "--attempt-dir", broken],
                                 capture_output=True, text=True)
        self.assertEqual(refused.returncode, 1)
        self.assertIn("REFUSED", refused.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.root, "broken", "rounds", "fam-r1",
                                                     "survivor-bundle.json")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
