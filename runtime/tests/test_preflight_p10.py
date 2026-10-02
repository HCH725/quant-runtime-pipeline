#!/usr/bin/env python3
"""Logic checks for preflight P10 (contract 16.2: `run-spec.json` + `script.sha256` 相符).

P10 is the launch gate for "this spec points at this program", so it must never report PASS without
an actual sha256 recomputation.  Since v1.8 (contract 26.1) P10 additionally refuses an attempt
whose frozen round-spec carries no valid generic `parameter_contract`: such a family fails closed
in every post-survivor consumer, i.e. after the whole compute.  Since the 2026-10-03 terminal
authority boundary it also refuses a new attempt in a round whose own `verdict.json` already
carries a contract-terminal verdict (a DECIDED round is closed; a same-round technical retry is
legal only BEFORE that verdict exists).  These checks drive
`preflight.p9_p10` on temp attempt dirs with the real results-tree shape
(`<root>/<family>/rounds/<round>/attempts/<run>/`), with the host scripts dir injected through the
existing `/scripts` mount mapping (no container needed).

Run: python3 runtime/tests/test_preflight_p10.py     (stdlib unittest, no dependencies)
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
import preflight  # noqa: E402
import parameter_contract as pc  # noqa: E402

SPEC_KEYS = {"schema_version": 1, "family_id": "fam-a", "round_id": "fam-a-r1", "run_id": "fam-a-r1-u1",
             "task_id": "t_SMOKE", "kanban_board": "quant-strategy-research"}
FAMILY = "fam-a"
B_V2_TEMPLATE = RUNTIME / "templates" / "strategy_b_v2_round_spec.template.json"


class P10Fixture(unittest.TestCase):
    """Real results-tree shape + helpers.  No test methods of its own: subclasses add the checks,
    so a new check class never re-runs another class's checks."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="qrp-p10-test-")
        self.round = Path(self.root) / FAMILY / "rounds" / "fam-a-r1"
        self.attempt = self.round / "attempts" / "fam-a-r1-u1"
        self.attempt.mkdir(parents=True)
        self.host_scripts = Path(self.root) / "host-scripts"
        self.host_scripts.mkdir()
        self.script = self.host_scripts / "strategy.py"
        self.script.write_text("print('strategy')\n")
        self.sha = "sha256:" + hashlib.sha256(self.script.read_bytes()).hexdigest()
        # the legacy A v2 shape resolves through the in-code bridge (v1.8 backward compatibility)
        self.write_round_spec({"family_id": pc.LEGACY_A_FAMILY_ID})

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def write_round_spec(self, doc, round_dir=None):
        round_dir = round_dir or self.round
        round_dir.mkdir(parents=True, exist_ok=True)
        (round_dir / "round-spec.json").write_text(json.dumps(doc))

    def write_verdict(self, doc, round_dir=None):
        round_dir = round_dir or self.round
        round_dir.mkdir(parents=True, exist_ok=True)
        (round_dir / "verdict.json").write_text(json.dumps(doc))

    def write_family(self, doc):
        (Path(self.root) / FAMILY / "family.json").write_text(json.dumps(doc))

    def run_p10(self, script=None, sha=None, drop_run_spec=False, terminals=()):
        if not drop_run_spec:
            spec = dict(SPEC_KEYS)
            spec["script"] = script if script is not None else {"path": "/scripts/strategy.py", "sha256": sha or self.sha}
            (self.attempt / "run-spec.json").write_text(json.dumps(spec))
        for term in terminals:
            (self.attempt / term).write_text("{}")
        checks = []
        preflight.p9_p10(checks, str(self.attempt), str(self.host_scripts))
        by_id = {c["id"]: c for c in checks}
        return by_id

    def assert_p10(self, expected, **kwargs):
        by_id = self.run_p10(**kwargs)
        self.assertEqual(by_id["P10"]["status"], expected, by_id["P10"]["detail"])
        return by_id

class P10Case(P10Fixture):

    # --- mapped / host-readable paths must actually be recomputed ---
    def test_container_scripts_path_resolved_via_host_mapping(self):
        by_id = self.assert_p10("PASS")
        self.assertIn(str(self.script), by_id["P10"]["detail"])
        self.assertIn("recomputed", by_id["P10"]["detail"])

    def test_absolute_host_path_is_recomputed(self):
        self.assert_p10("PASS", script={"path": str(self.script), "sha256": self.sha})

    def test_sha_mismatch_fails(self):
        self.assert_p10("FAIL", script={"path": "/scripts/strategy.py", "sha256": "sha256:" + "0" * 64})

    # --- F3 (audit v2): unverifiable sha256 must never PASS ---
    def test_unresolvable_path_is_not_verified(self):
        by_id = self.assert_p10("FAIL",
                                script={"path": "/scripts/missing.py", "sha256": "sha256:deadbeef" + "0" * 56})
        self.assertIn("NOT VERIFIED", by_id["P10"]["detail"])

    def test_path_outside_the_mapping_is_not_verified(self):
        by_id = self.assert_p10("FAIL", script={"path": "/opt/other/strategy.py", "sha256": self.sha})
        self.assertIn("NOT VERIFIED", by_id["P10"]["detail"])

    def test_missing_sha_is_fail(self):
        self.assert_p10("FAIL", script={"path": "/scripts/strategy.py"})

    def test_absent_script_block_is_fail(self):
        spec = dict(SPEC_KEYS)          # run-spec without a `script` key at all
        (self.attempt / "run-spec.json").write_text(json.dumps(spec))
        checks = []
        preflight.p9_p10(checks, str(self.attempt), str(self.host_scripts))
        by_id = {c["id"]: c for c in checks}
        self.assertEqual(by_id["P10"]["status"], "FAIL")
        # Finding D regression: `script` is a required key; absent → explicit diagnostic
        self.assertIn("missing keys", by_id["P10"]["detail"])
        self.assertIn("script", by_id["P10"]["detail"])

    def test_missing_run_spec_is_fail(self):
        self.assert_p10("FAIL", drop_run_spec=True)

    def test_terminal_sentinel_fails_the_launch_gate(self):
        by_id = self.run_p10(terminals=("DONE",))          # INV-15: terminal evidence forbids re-run
        self.assertEqual(by_id["P9"]["status"], "FAIL")
        evaluated = [c for c in by_id.values() if c["status"] != "NA"]
        self.assertTrue([c for c in evaluated if c["status"] != "PASS"])

    def test_no_attempt_dir_is_not_evaluated(self):
        checks = []
        preflight.p9_p10(checks, None)
        self.assertEqual([c["status"] for c in checks], ["NA", "NA"])

    # --- v1.8 / contract 26.1: the frozen round-spec must carry the parameter contract ---
    def test_round_spec_with_valid_parameter_contract_passes_the_launch_gate(self):
        """The real, forward-authored B v2 template round-spec is the launch-gate shape."""
        self.write_round_spec(json.loads(B_V2_TEMPLATE.read_text()))
        by_id = self.assert_p10("PASS")
        self.assertNotIn("parameter_contract:", by_id["P10"]["detail"])

    def test_round_spec_without_parameter_contract_fails_the_launch_gate(self):
        """The pre-migration r1 shape (non-legacy family, no schema) must be refused."""
        doc = json.loads(B_V2_TEMPLATE.read_text())
        doc.pop("parameter_contract")
        self.write_round_spec(doc)
        by_id = self.assert_p10("FAIL")
        self.assertIn("round-spec parameter_contract", by_id["P10"]["detail"])
        self.assertIn("fail closed", by_id["P10"]["detail"])

    def test_invalid_parameter_contract_fails_the_launch_gate(self):
        doc = json.loads(B_V2_TEMPLATE.read_text())
        doc["parameter_contract"]["domain_cardinality"] = {"strategy": 6, "dca": 48, "per_cohort": 288}
        self.write_round_spec(doc)
        by_id = self.assert_p10("FAIL")
        self.assertIn("parameter_contract invalid", by_id["P10"]["detail"])

    def test_schema_domain_mismatch_fails_the_launch_gate(self):
        """A contract that is internally valid but disagrees with the declared domain is refused."""
        doc = json.loads(B_V2_TEMPLATE.read_text())
        doc["parameter_domain"]["legal_cases_per_cohort"] = 121
        self.write_round_spec(doc)
        by_id = self.assert_p10("FAIL")
        self.assertIn("schema domain mismatch", by_id["P10"]["detail"])

    def test_missing_round_spec_fails_the_launch_gate(self):
        (self.round / "round-spec.json").unlink()
        by_id = self.assert_p10("FAIL")
        self.assertIn("missing", by_id["P10"]["detail"])

    def test_legacy_a_round_spec_still_passes_the_contract_gate(self):
        """Backward compatibility: the pre-schema A v2 bridge resolves without a contract."""
        self.assertEqual(preflight.round_spec_contract_problem(str(self.attempt)), None)


class TerminalAuthorityCase(P10Fixture):
    """Terminal authority boundary (2026-10-03): a DECIDED round is not launchable.

    P10 is the sanctioned pre-launch gate every disposition/remediation session runs (contract 16),
    so it is where "the round is already closed" is enforced: a new same-round attempt is refused
    once the attempt's OWN `rounds/<round>/verdict.json` carries a contract-terminal verdict, while
    a legitimate same-round technical retry BEFORE that verdict (contract 8 / 13 `script_bug`) is
    untouched.  The live incident this pins: a corrected serialization retry (u2) was materialised
    and launched in a round that had already published PASS, so nothing could ever consume u2.
    """

    def test_own_round_terminal_verdict_refuses_a_new_same_round_attempt(self):
        self.write_verdict({"schema_version": 1, "family_id": FAMILY, "round_id": "fam-a-r1",
                            "verdict": "PASS"})
        by_id = self.assert_p10("FAIL")
        self.assertEqual(by_id["P9"]["status"], "PASS")  # the refusal is the round's, not INV-15
        self.assertIn("terminal authority boundary", by_id["P10"]["detail"])
        self.assertIn("contract-terminal verdict PASS", by_id["P10"]["detail"])
        self.assertIn("new round_id", by_id["P10"]["detail"])

    def test_every_contract_terminal_token_closes_the_round(self):
        for token in ("PASS", "REJECT", "FINALIST", "DEFERRED", "TECHNICAL_INCOMPLETE"):
            self.write_verdict({"family_id": FAMILY, "round_id": "fam-a-r1", "verdict": token})
            by_id = self.assert_p10("FAIL")
            self.assertIn("contract-terminal verdict %s" % token, by_id["P10"]["detail"])

    def test_undecided_round_still_launches_a_same_round_technical_retry(self):
        # control: no verdict.json at all -> contract 8 / 13 same-round retry with a new run_id
        by_id = self.assert_p10("PASS")
        self.assertNotIn("terminal authority boundary", by_id["P10"]["detail"])

    def test_verdict_that_is_not_terminal_authority_refuses_nothing(self):
        # exactly the shapes production_handoff.round_verdict_token reads as MISSING (malformed,
        # unknown token, foreign round, foreign family): they are not terminal authority, so a
        # same-round retry stays legal - the gate must not invent a closure.
        (self.round / "verdict.json").write_text("{not json")
        self.assert_p10("PASS")
        for doc in ({"family_id": FAMILY, "round_id": "fam-a-r1", "verdict": "CANDIDATE_PASS"},
                    {"family_id": FAMILY, "round_id": "fam-a-r2", "verdict": "PASS"},
                    {"family_id": "someone-else", "round_id": "fam-a-r1", "verdict": "PASS"}):
            self.write_verdict(doc)
            self.assert_p10("PASS")

    def test_ownership_mismatch_is_not_terminal_authority(self):
        self.write_family({"schema_version": 1, "family_id": FAMILY, "kanban_task_id": "t_x",
                           "kanban_board": "quant-strategy-research"})
        self.write_verdict({"family_id": FAMILY, "round_id": "fam-a-r1", "verdict": "PASS",
                            "kanban_task_id": "t_other"})
        self.assert_p10("PASS")
        self.write_verdict({"family_id": FAMILY, "round_id": "fam-a-r1", "verdict": "PASS",
                            "kanban_task_id": "t_x"})
        by_id = self.assert_p10("FAIL")
        self.assertIn("terminal authority boundary", by_id["P10"]["detail"])

    def test_an_earlier_rounds_verdict_does_not_close_a_new_round(self):
        # verdict.json is per ROUND: r1's PASS decides r1, never a fresh r2 attempt
        self.write_verdict({"family_id": FAMILY, "round_id": "fam-a-r1", "verdict": "PASS"})
        r2_round = Path(self.root) / FAMILY / "rounds" / "fam-a-r2"
        r2_attempt = r2_round / "attempts" / "fam-a-r2-u1"
        r2_attempt.mkdir(parents=True)
        self.write_round_spec({"family_id": pc.LEGACY_A_FAMILY_ID}, round_dir=r2_round)
        (r2_attempt / "run-spec.json").write_text(json.dumps(dict(
            SPEC_KEYS, round_id="fam-a-r2", run_id="fam-a-r2-u1",
            script={"path": "/scripts/strategy.py", "sha256": self.sha})))
        checks = []
        preflight.p9_p10(checks, str(r2_attempt), str(self.host_scripts))
        by_id = {c["id"]: c for c in checks}
        self.assertEqual(by_id["P10"]["status"], "PASS", by_id["P10"]["detail"])

    def test_the_helper_reads_the_own_round_and_its_ownership(self):
        # direct unit pin of the token rule the gate delegates to
        self.assertIsNone(preflight.round_terminal_verdict(str(self.attempt), {}))
        self.write_verdict({"family_id": FAMILY, "round_id": "fam-a-r1", "verdict": "PASS"})
        self.assertEqual(preflight.round_terminal_verdict(str(self.attempt), {}), "PASS")


if __name__ == "__main__":
    unittest.main(verbosity=2)
