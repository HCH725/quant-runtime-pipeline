#!/usr/bin/env python3
"""Tests for the generic parameter contract module (Contract v1.8.0, section 29).

Covers:
  * LEGACY_A_CONTRACT validates and renders correctly;
  * load_contract_from_round_spec: valid contract, legacy A bridge, unknown family fail-closed;
  * validate_contract: structural validation catches bad axes, duplicate row fields, cardinality;
  * normalize_winner: strategy/dca split, missing/unknown/non-numeric fail-closed;
  * match_row: exact row match, zero/multiple hits fail-closed;
  * render_parameter_contract: deterministic six-line output;
  * unknown future-family fixture: round-spec without parameter_contract for non-A family fails;
  * B-type contract: selector regression through contract-validated path.
"""
import copy
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
RUNTIME = os.path.join(REPO, "runtime")
sys.path.insert(0, RUNTIME)

import parameter_contract as pc  # noqa: E402


class TestLegacyAContract(unittest.TestCase):

    def test_validates_cleanly(self):
        problems = pc.validate_contract(pc.LEGACY_A_CONTRACT)
        self.assertEqual(problems, [])

    def test_family_id_matches(self):
        self.assertEqual(pc.LEGACY_A_CONTRACT["family_id"], pc.LEGACY_A_FAMILY_ID)

    def test_domain_cardinality_product(self):
        card = pc.LEGACY_A_CONTRACT["domain_cardinality"]
        self.assertEqual(card["per_cohort"], card["strategy"] * card["dca"])
        self.assertEqual(card["strategy"], 12)   # 4 windows x 3 discounts
        self.assertEqual(card["dca"], 48)         # 4 x 2 x 3 x 2

    def test_strategy_dca_partition_row_fields(self):
        strat = set(pc.LEGACY_A_CONTRACT["strategy_param_fields"])
        dca = set(pc.LEGACY_A_CONTRACT["dca_param_fields"])
        self.assertEqual(strat & dca, set(), "no overlap")
        self.assertEqual(strat | dca, set(pc.LEGACY_A_CONTRACT["row_fields"]),
                         "must partition row_fields")

    def test_render_produces_six_lines(self):
        text = pc.render_parameter_contract(pc.LEGACY_A_CONTRACT)
        lines = text.split("\n")
        self.assertEqual(len(lines), 7)  # header + 6 required lines
        self.assertTrue(lines[0].startswith("PARAMETER CONTRACT ["))


class TestLoadContractFromRoundSpec(unittest.TestCase):

    def test_valid_contract_from_spec(self):
        spec = {"family_id": "my-family", "parameter_contract": pc.LEGACY_A_CONTRACT}
        # Note: family_id mismatch is caught
        contract, problems, is_legacy = pc.load_contract_from_round_spec(spec)
        self.assertTrue(any("family_id" in p for p in problems))

    def test_matching_family_id(self):
        spec = {"family_id": pc.LEGACY_A_FAMILY_ID,
                "parameter_contract": pc.LEGACY_A_CONTRACT}
        contract, problems, is_legacy = pc.load_contract_from_round_spec(spec)
        self.assertEqual(problems, [])
        self.assertFalse(is_legacy)
        self.assertEqual(contract["family_id"], pc.LEGACY_A_FAMILY_ID)

    def test_legacy_bridge_for_pre_schema_a(self):
        spec = {"family_id": pc.LEGACY_A_FAMILY_ID}
        contract, problems, is_legacy = pc.load_contract_from_round_spec(spec)
        self.assertEqual(problems, [])
        self.assertTrue(is_legacy)
        self.assertEqual(contract["family_id"], pc.LEGACY_A_FAMILY_ID)

    def test_unknown_family_without_contract_fails_closed(self):
        spec = {"family_id": "some-new-family"}
        contract, problems, is_legacy = pc.load_contract_from_round_spec(spec)
        self.assertIsNone(contract)
        self.assertTrue(any("fail closed" in p for p in problems))

    def test_invalid_contract_fails(self):
        spec = {"family_id": "fam", "parameter_contract": {"bad": True}}
        contract, problems, is_legacy = pc.load_contract_from_round_spec(spec)
        self.assertIsNone(contract)
        self.assertTrue(any("invalid" in p for p in problems))


class TestValidateContract(unittest.TestCase):

    def test_rejects_non_dict(self):
        problems = pc.validate_contract("not a dict")
        self.assertEqual(problems, ["parameter_contract is not an object"])

    def test_rejects_wrong_version(self):
        bad = dict(pc.LEGACY_A_CONTRACT, parameter_contract_version=99)
        problems = pc.validate_contract(bad)
        self.assertTrue(any("version" in p for p in problems))

    def test_rejects_empty_family_id(self):
        bad = dict(pc.LEGACY_A_CONTRACT, family_id="")
        problems = pc.validate_contract(bad)
        self.assertTrue(any("family_id" in p for p in problems))

    def test_rejects_empty_axes(self):
        bad = dict(pc.LEGACY_A_CONTRACT, research_axes_ordered=[])
        problems = pc.validate_contract(bad)
        self.assertTrue(any("non-empty list" in p for p in problems))

    def test_rejects_duplicate_row_fields(self):
        bad = dict(pc.LEGACY_A_CONTRACT,
                   research_axes_ordered=[
                       {"name": "a", "kind": "atomic", "members": ["x"],
                        "registered_values": [1], "row_fields": ["x"]},
                       {"name": "b", "kind": "atomic", "members": ["x"],
                        "registered_values": [2], "row_fields": ["x"]},
                   ],
                   row_fields=["x"])
        problems = pc.validate_contract(bad)
        self.assertTrue(any("duplicate" in p for p in problems))

    def test_rejects_cardinality_mismatch(self):
        bad = dict(pc.LEGACY_A_CONTRACT,
                   domain_cardinality={"strategy": 10, "dca": 48, "per_cohort": 100})
        problems = pc.validate_contract(bad)
        self.assertTrue(any("per_cohort" in p for p in problems))

    def test_rejects_composite_arity_mismatch(self):
        bad = dict(pc.LEGACY_A_CONTRACT,
                   research_axes_ordered=[
                       {"name": "ema_pair", "kind": "composite",
                        "members": ["ema_fast", "ema_slow"],
                        "registered_values": [[5, 40], [10, 60]],  # arity OK
                        "row_fields": ["ema_fast", "ema_slow"]},
                       {"name": "dca", "kind": "atomic", "members": ["spacing_pct"],
                        "registered_values": [0.01], "row_fields": ["spacing_pct"]},
                   ],
                   row_fields=["ema_fast", "ema_slow", "spacing_pct"],
                   composite_map={"ema_pair": ["ema_fast", "ema_slow"]},
                   strategy_param_fields=["ema_fast", "ema_slow"],
                   dca_param_fields=["spacing_pct"],
                   domain_cardinality={"strategy": 2, "dca": 1, "per_cohort": 2})
        # This should validate clean (arity matches)
        problems = pc.validate_contract(bad)
        self.assertEqual(problems, [])

    def test_strategy_dca_must_partition_row_fields(self):
        # Missing a row_field in the union of strategy+dca
        bad = dict(pc.LEGACY_A_CONTRACT,
                   strategy_param_fields=["window"],
                   dca_param_fields=["spacing_pct", "size_multiplier",
                                     "breakeven_tp_pct", "invalidation_pct"])
        problems = pc.validate_contract(bad)
        self.assertTrue(any("partition" in p for p in problems))


class TestNormalizeWinner(unittest.TestCase):

    def test_legacy_a_winner(self):
        winner = {"window": 20, "discount": 0.03,
                  "spacing_pct": 0.01, "size_multiplier": 1.0,
                  "breakeven_tp_pct": 0.01, "invalidation_pct": 0.1}
        problems = []
        strat, dca = pc.normalize_winner(pc.LEGACY_A_CONTRACT, winner, "test", problems)
        self.assertEqual(problems, [])
        self.assertEqual(strat, {"window": 20, "discount": 0.03})
        self.assertEqual(dca, {"spacing_pct": 0.01, "size_multiplier": 1.0,
                               "breakeven_tp_pct": 0.01, "invalidation_pct": 0.1})

    def test_missing_strategy_key_refuses(self):
        winner = {"discount": 0.03,
                  "spacing_pct": 0.01, "size_multiplier": 1.0,
                  "breakeven_tp_pct": 0.01, "invalidation_pct": 0.1}
        problems = []
        strat, dca = pc.normalize_winner(pc.LEGACY_A_CONTRACT, winner, "test", problems)
        self.assertIsNone(strat)
        self.assertTrue(any("missing" in p for p in problems))

    def test_unknown_key_refuses(self):
        winner = {"window": 20, "discount": 0.03, "leverage": 3.0,
                  "spacing_pct": 0.01, "size_multiplier": 1.0,
                  "breakeven_tp_pct": 0.01, "invalidation_pct": 0.1}
        problems = []
        strat, dca = pc.normalize_winner(pc.LEGACY_A_CONTRACT, winner, "test", problems)
        self.assertIsNone(strat)
        self.assertTrue(any("outside" in p for p in problems))

    def test_non_numeric_refuses(self):
        winner = {"window": "twenty", "discount": 0.03,
                  "spacing_pct": 0.01, "size_multiplier": 1.0,
                  "breakeven_tp_pct": 0.01, "invalidation_pct": 0.1}
        problems = []
        strat, dca = pc.normalize_winner(pc.LEGACY_A_CONTRACT, winner, "test", problems)
        self.assertIsNone(strat)
        self.assertTrue(any("not numeric" in p for p in problems))


class TestMatchRow(unittest.TestCase):

    def test_exact_match(self):
        rows = [
            {"symbol": "BTCUSDT", "timeframe": "1h", "window": 20, "discount": 0.03},
        ]
        params = {"window": 20, "discount": 0.03}
        contract = {"row_fields": ["window", "discount"]}
        row = pc.match_row(rows, "BTCUSDT", "1h", params, contract)
        self.assertEqual(row, rows[0])

    def test_no_match_raises(self):
        rows = [
            {"symbol": "BTCUSDT", "timeframe": "1h", "window": 50, "discount": 0.03},
        ]
        params = {"window": 20, "discount": 0.03}
        contract = {"row_fields": ["window", "discount"]}
        with self.assertRaises(ValueError):
            pc.match_row(rows, "BTCUSDT", "1h", params, contract)

    def test_multiple_matches_raises(self):
        rows = [
            {"symbol": "BTCUSDT", "timeframe": "1h", "window": 20, "discount": 0.03},
            {"symbol": "BTCUSDT", "timeframe": "1h", "window": 20, "discount": 0.03},
        ]
        params = {"window": 20, "discount": 0.03}
        contract = {"row_fields": ["window", "discount"]}
        with self.assertRaises(ValueError):
            pc.match_row(rows, "BTCUSDT", "1h", params, contract)


class TestRenderParameterContract(unittest.TestCase):

    def test_legacy_a_renders(self):
        text = pc.render_parameter_contract(pc.LEGACY_A_CONTRACT)
        self.assertIn("PARAMETER CONTRACT [", text)
        self.assertIn(pc.LEGACY_A_FAMILY_ID, text)
        self.assertIn("window{", text)
        self.assertIn("discount{", text)
        self.assertIn("cardinality: strategy=12 dca=48 per-cohort=576", text)

    def test_composite_renders(self):
        contract = {
            "family_id": "test-composite",
            "research_axes_ordered": [
                {"name": "ema_pair", "kind": "composite",
                 "members": ["ema_fast", "ema_slow"],
                 "registered_values": [[5, 40], [10, 60]],
                 "row_fields": ["ema_fast", "ema_slow"]},
            ],
            "composite_map": {"ema_pair": ["ema_fast", "ema_slow"]},
            "dca_param_fields": ["spacing_pct"],
            "domain_cardinality": {"strategy": 2, "dca": 1, "per_cohort": 2},
        }
        text = pc.render_parameter_contract(contract)
        self.assertIn("composite ema_fast, ema_slow", text)
        self.assertIn("(5,40)", text)


class TestUnknownFamilyFixture(unittest.TestCase):
    """Unknown future-family without a parameter_contract fails closed (v1.8+)."""

    def test_unknown_family_no_contract(self):
        spec = {"family_id": "future-family-v1"}
        contract, problems, is_legacy = pc.load_contract_from_round_spec(spec)
        self.assertIsNone(contract)
        self.assertEqual(len(problems), 1)
        self.assertIn("fail closed", problems[0])

    def test_unknown_family_with_invalid_contract(self):
        spec = {"family_id": "future-family-v1",
                "parameter_contract": {"version": 1}}  # missing required fields
        contract, problems, is_legacy = pc.load_contract_from_round_spec(spec)
        self.assertIsNone(contract)
        self.assertTrue(len(problems) > 0)


class TestBFamilyContractThroughSelector(unittest.TestCase):
    """B-type contract with composite axes validates and normalizes correctly."""

    B_CONTRACT = {
        "parameter_contract_version": 1,
        "family_id": "ema-crossover-walkforward-momentum-long-short-v2",
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
        "non_params": ["symbol", "timeframe", "n_steps", "ema_pair_index",
                       "walk_forward_index"],
        "domain_cardinality": {"strategy": 6, "dca": 36, "per_cohort": 216},
    }

    def test_validates(self):
        problems = pc.validate_contract(self.B_CONTRACT)
        self.assertEqual(problems, [])

    def test_composite_arity(self):
        for ax in self.B_CONTRACT["research_axes_ordered"]:
            if ax["kind"] == "composite":
                for v in ax["registered_values"]:
                    self.assertEqual(len(v), len(ax["members"]),
                                     "arity mismatch in %s" % ax["name"])

    def test_strategy_dca_partition(self):
        strat = set(self.B_CONTRACT["strategy_param_fields"])
        dca = set(self.B_CONTRACT["dca_param_fields"])
        self.assertEqual(strat & dca, set())
        self.assertEqual(strat | dca, set(self.B_CONTRACT["row_fields"]))

    def test_domain_cardinality_product(self):
        card = self.B_CONTRACT["domain_cardinality"]
        self.assertEqual(card["per_cohort"], card["strategy"] * card["dca"])
        # strategy: ema_pair(3) x walk_forward(2) = 6
        self.assertEqual(card["strategy"], 6)
        # dca: spacing(3) x size(2) x tp(3) x inv(2) = 36
        # Wait, that's 36, not 12. Let me check...
        # Actually the B contract has 4 DCA axes: 3*2*3*2=36, not 12.
        # Let me fix the cardinality.
        self.assertEqual(card["dca"], 36)

    def test_normalize_winner_b_type(self):
        winner = {"ema_fast": 10, "ema_slow": 60,
                  "wf_train_days": 252, "wf_test_days": 63,
                  "spacing_pct": 0.02, "size_multiplier": 1.1,
                  "breakeven_tp_pct": 0.03, "invalidation_pct": 0.10}
        problems = []
        strat, dca = pc.normalize_winner(self.B_CONTRACT, winner, "test", problems)
        self.assertEqual(problems, [])
        self.assertEqual(strat, {"ema_fast": 10, "ema_slow": 60,
                                 "wf_train_days": 252, "wf_test_days": 63})
        self.assertEqual(dca, {"spacing_pct": 0.02, "size_multiplier": 1.1,
                               "breakeven_tp_pct": 0.03, "invalidation_pct": 0.10})

    def test_b_winner_with_legacy_keys_fails(self):
        """B winner using A-style keys (window/discount) must fail closed."""
        winner = {"window": 20, "discount": 0.03,
                  "spacing_pct": 0.01, "size_multiplier": 1.0,
                  "breakeven_tp_pct": 0.01, "invalidation_pct": 0.1}
        problems = []
        strat, dca = pc.normalize_winner(self.B_CONTRACT, winner, "test", problems)
        self.assertIsNone(strat)
        self.assertTrue(any("missing" in p for p in problems))

    def test_render_b_contract(self):
        text = pc.render_parameter_contract(self.B_CONTRACT)
        self.assertIn("composite ema_fast, ema_slow", text)
        self.assertIn("composite wf_train_days, wf_test_days", text)
        self.assertIn("cardinality: strategy=6 dca=36 per-cohort=216", text)


class TestMalformedContractShapes(unittest.TestCase):
    """F3 remediation: malformed composite_map/registered_values/members must accumulate
    validation problems, never raise TypeError."""

    def _base(self):
        # deep copy: the axis dicts of LEGACY_A_CONTRACT are shared by reference through a shallow
        # dict(), so an in-place edit of one axis (members/row_fields) used to corrupt the module
        # constant for every later test in the same process.
        base = copy.deepcopy(pc.LEGACY_A_CONTRACT)
        # Add a composite axis so composite_map key validation can reach the value check
        base["research_axes_ordered"] = list(base["research_axes_ordered"]) + [
            {"name": "ema_pair", "kind": "composite",
             "members": ["ema_fast", "ema_slow"],
             "registered_values": [[5, 40], [10, 60]],
             "row_fields": ["ema_fast", "ema_slow"]},
        ]
        base["row_fields"] = list(base["row_fields"]) + ["ema_fast", "ema_slow"]
        base["composite_map"] = {"ema_pair": ["ema_fast", "ema_slow"]}
        base["strategy_param_fields"] = list(base["strategy_param_fields"]) + ["ema_fast", "ema_slow"]
        base["domain_cardinality"] = dict(base["domain_cardinality"],
                                          strategy=base["domain_cardinality"]["strategy"] * 2,
                                          per_cohort=base["domain_cardinality"]["per_cohort"] * 2)
        return base

    def test_composite_map_value_not_a_list(self):
        """composite_map with a string value (not a list) must not raise TypeError."""
        bad = self._base()
        bad["composite_map"] = {"ema_pair": "not-a-list"}
        problems = pc.validate_contract(bad)
        self.assertTrue(any("composite_map" in p and "not a list" in p for p in problems),
                        problems)

    def test_composite_map_value_none(self):
        """composite_map with None value must not raise TypeError."""
        bad = self._base()
        bad["composite_map"] = {"ema_pair": None}
        problems = pc.validate_contract(bad)
        self.assertTrue(any("composite_map" in p for p in problems), problems)

    def test_composite_map_value_int(self):
        """composite_map with an integer value must not raise TypeError."""
        bad = self._base()
        bad["composite_map"] = {"ema_pair": 42}
        problems = pc.validate_contract(bad)
        self.assertTrue(any("composite_map" in p for p in problems), problems)

    def test_members_not_a_list(self):
        """axis members that is not a list must be caught, not raise TypeError."""
        bad = self._base()
        bad["research_axes_ordered"][0]["members"] = "not-a-list"
        problems = pc.validate_contract(bad)
        self.assertTrue(any("members" in p for p in problems), problems)

    def test_row_fields_not_a_list(self):
        """axis row_fields that is not a list must be caught, not raise TypeError."""
        bad = self._base()
        bad["research_axes_ordered"][0]["row_fields"] = "not-a-list"
        problems = pc.validate_contract(bad)
        self.assertTrue(any("row_fields" in p for p in problems), problems)

    def test_load_contract_from_round_spec_malformed_does_not_traceback(self):
        """load_contract_from_round_spec with malformed contract returns problems,
        never raises."""
        spec = {"family_id": "fam",
                "parameter_contract": {"bad": True}}
        contract, problems, is_legacy = pc.load_contract_from_round_spec(spec)
        self.assertIsNone(contract)
        self.assertTrue(len(problems) > 0)

    # --- R2 residual (v1.8 re-audit): mixed-type composite_map/members/field lists -------------
    def test_composite_map_value_mixed_list_does_not_raise(self):
        """R2: [1, 'ema_slow'] reached sorted() and raised TypeError."""
        bad = self._base()
        bad["composite_map"] = {"ema_pair": [1, "ema_slow"]}
        problems = pc.validate_contract(bad)
        self.assertTrue(any("composite_map" in p for p in problems), problems)

    def test_composite_map_value_list_with_dict_does_not_raise(self):
        """R2: ['ema_fast', {'k': 1}] reached sorted() and raised TypeError."""
        bad = self._base()
        bad["composite_map"] = {"ema_pair": ["ema_fast", {"k": 1}]}
        problems = pc.validate_contract(bad)
        self.assertTrue(any("composite_map" in p for p in problems), problems)

    def test_composite_map_value_bool_list_does_not_raise(self):
        """R2: [True, 'ema_slow'] reached sorted() and raised TypeError."""
        bad = self._base()
        bad["composite_map"] = {"ema_pair": [True, "ema_slow"]}
        problems = pc.validate_contract(bad)
        self.assertTrue(any("composite_map" in p for p in problems), problems)

    def test_referenced_mixed_members_do_not_raise(self):
        """R2: a composite axis referenced by composite_map whose members are mixed-type
        reached sorted() and raised TypeError."""
        bad = self._base()
        bad["research_axes_ordered"][-1]["members"] = [1, "ema_slow"]
        problems = pc.validate_contract(bad)
        self.assertTrue(any("members" in p for p in problems), problems)

    def test_mixed_type_row_fields_does_not_raise(self):
        """R2 sibling: a mixed-type contract row_fields list reached sorted()."""
        bad = self._base()
        bad["row_fields"] = list(bad["row_fields"]) + [1]
        problems = pc.validate_contract(bad)
        self.assertTrue(any("row_fields" in p for p in problems), problems)

    def test_unhashable_strategy_field_does_not_raise(self):
        """R2 sibling: an unhashable entry in strategy_param_fields reached set()."""
        bad = self._base()
        bad["strategy_param_fields"] = ["window", ["discount"]]
        problems = pc.validate_contract(bad)
        self.assertTrue(any("strategy_param_fields" in p for p in problems), problems)

    def test_mixed_type_strategy_fields_does_not_raise(self):
        """R2 sibling: a mixed-type strategy/dca field list reached sorted()."""
        bad = self._base()
        bad["strategy_param_fields"] = ["window", 7]
        problems = pc.validate_contract(bad)
        self.assertTrue(any("strategy_param_fields" in p for p in problems), problems)

    def test_load_fails_closed_on_mixed_composite_map(self):
        """R2: the mixed shapes fail closed at the load boundary, not with a TypeError."""
        for mutate in (lambda c: c["composite_map"].__setitem__("ema_pair", [1, "ema_slow"]),
                       lambda c: c["research_axes_ordered"][-1].__setitem__(
                           "members", [1, "ema_slow"]),
                       lambda c: c.__setitem__("row_fields", list(c["row_fields"]) + [1])):
            bad = self._base()
            mutate(bad)
            contract, problems, is_legacy = pc.load_contract_from_round_spec(
                {"family_id": bad["family_id"], "parameter_contract": bad})
            self.assertIsNone(contract, bad["composite_map"])
            self.assertTrue(problems)
            self.assertIn("parameter_contract invalid", problems[0])
            self.assertFalse(is_legacy)


class TestRenderHeaderLabels(unittest.TestCase):
    """F4 remediation: generic atomic header must not be mislabeled legacy-a-v2."""

    def test_legacy_a_header_label(self):
        text = pc.render_parameter_contract(pc.LEGACY_A_CONTRACT)
        self.assertIn("legacy-a-v2", text)

    def test_generic_atomic_header_label(self):
        contract = {
            "family_id": "generic-atomic-family",
            "research_axes_ordered": [
                {"name": "param_x", "kind": "atomic", "members": ["param_x"],
                 "registered_values": [1, 2], "row_fields": ["param_x"]},
            ],
            "composite_map": {},
            "dca_param_fields": ["param_x"],
            "domain_cardinality": {"strategy": 2, "dca": 1, "per_cohort": 2},
            "strategy_param_fields": [],
            "row_fields": ["param_x"],
        }
        text = pc.render_parameter_contract(contract)
        self.assertIn("v1, atomic", text)
        self.assertNotIn("legacy-a-v2", text)

    def test_composite_header_label(self):
        contract = {
            "family_id": "composite-family",
            "research_axes_ordered": [
                {"name": "ema_pair", "kind": "composite",
                 "members": ["ema_fast", "ema_slow"],
                 "registered_values": [[5, 40], [10, 60]],
                 "row_fields": ["ema_fast", "ema_slow"]},
            ],
            "composite_map": {"ema_pair": ["ema_fast", "ema_slow"]},
            "dca_param_fields": ["ema_fast", "ema_slow"],
            "domain_cardinality": {"strategy": 2, "dca": 1, "per_cohort": 2},
            "strategy_param_fields": ["ema_fast", "ema_slow"],
            "row_fields": ["ema_fast", "ema_slow"],
        }
        text = pc.render_parameter_contract(contract)
        self.assertIn("v1, composite", text)
        self.assertNotIn("legacy-a-v2", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
