#!/usr/bin/env python3
"""Executable check for the Strategy PT execution engine (End-to-End Parametric Portfolio
Policies: the Portfolio Transformer / LSTM policy on the cross-sectional daily return state,
the signed-softmax unit-gross weight layer, the differentiable Sharpe objective with the
cost-aware lambda = 0.0002 variant, and the record's own simple rules and two-step baseline
through the frozen DCA episode rail).

Runs inside the qlib container:
    container exec qlib-run env PT_ENGINE_PATH=/scripts/160_end_to_end_portfolio_policy_run.py \
        /opt/venv/bin/python /scripts/tests/test_strategy_pt_engine.py
and on a host numpy interpreter:
    PT_ENGINE_PATH=<repo>/container/scripts/160_end_to_end_portfolio_policy_run.py \
        <numpy venv>/bin/python <this file>

Every registered primitive is compared against a literal reference implementation (the
signed-softmax layer against the record's own formula, its reverse-mode gradients against
finite differences, the causal mask against its definition, the three simple rules against
explicit loops or numpy expressions, the target-state band against a literal threshold rule,
the Sharpe objective against a plain numpy rewrite), the registered constants against the
record's numbers, and the panel/state tensor against its invariants on a synthetic four-market
panel.  Causality is checked the only way it can be checked: by truncating the source history
and demanding the PAST not change.  The reader windows are checked against the record's own
numbers and the reader shells against "never PASS-bearing".  No market data, no container
state, no network: stdlib unittest + numpy only.
"""
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
import unittest

import numpy as np

ENGINE = os.environ.get("PT_ENGINE_PATH",
                        "/scripts/160_end_to_end_portfolio_policy_run.py")
_spec = importlib.util.spec_from_file_location("pt_engine", ENGINE)
pt = importlib.util.module_from_spec(_spec)
sys.modules["pt_engine"] = pt
_spec.loader.exec_module(pt)

BAR_MS = 86_400_000
BASE_MS = 1735689600000                     # 2025-01-01T00:00:00Z
N_BARS = 900
LABELS = ["BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]


def synth_panel(n=N_BARS, seed=7):
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.0, 0.03, size=(n, len(LABELS)))
    for t in range(30, n):
        rets[t] += 0.12 * rets[t - 1] + 0.05 * rets[t - 5]
    px = 100.0 * np.cumprod(1.0 + rets, axis=0)
    ms = np.arange(n, dtype=np.int64) * BAR_MS + BASE_MS
    return {m: np.stack([ms.astype(np.float64), px[:, i]], axis=1)
            for i, m in enumerate(LABELS)}


class TestRegistrations(unittest.TestCase):
    def test_policy_arms_are_the_registered_eight(self):
        self.assertEqual(pt.POLICY_ARMS, ("tf", "tf_net", "tfx", "lstm", "mvo", "tsmom", "rp",
                                          "ew"))
        self.assertEqual(pt.TRAINED_ARMS, ("tf", "tf_net", "tfx", "lstm"))

    def test_case_axis_is_the_registered_one_tuple_axis(self):
        self.assertEqual(len(pt.CASE_FIELDS), 1)
        self.assertEqual(pt.CASE_ORDER, tuple((i,) for i in range(len(pt.POLICY_ARMS))))
        for i, arm in enumerate(pt.POLICY_ARMS):
            case = (i,)
            self.assertEqual(pt.case_tuple({"arm": i}), case)
            self.assertEqual(pt.case_index(case), i)
            self.assertEqual(pt.case_name(case), arm)

    def test_registered_constants_match_the_record(self):
        self.assertEqual((pt.ENC_LAYERS, pt.DEC_LAYERS), (4, 4))
        self.assertEqual(pt.TURNOVER_LAMBDA, 0.0002)
        self.assertEqual(pt.GRAD_CLIP_NORM, 1.0)
        self.assertEqual(len(pt.ENSEMBLE_SEEDS), 3)
        self.assertEqual(len(set(pt.ENSEMBLE_SEEDS)), 3)
        self.assertEqual(pt.FEATURE_Z_WINDOW, 252)
        self.assertEqual(pt.FEATURE_ROC_WINDOWS, (1, 5, 20, 60))
        self.assertEqual(pt.FEATURE_VOL_WINDOW, 20)
        self.assertEqual((pt.FEATURE_SKEW_WINDOW, pt.FEATURE_KURT_WINDOW), (20, 20))
        self.assertGreaterEqual(pt.T2V_PERIODIC, 1)
        self.assertEqual(pt.TARGET_GROSS, 1.0)
        self.assertGreater(pt.MATERIALITY_W, 0.0)
        self.assertEqual(pt.SELECTOR_VERSION, "cohort-selector-v1")
        self.assertEqual(pt.DISPOSITION_VERSION, "cohort-disposition-v1")

    def test_registered_reader_windows_match_the_record(self):
        self.assertEqual(pt.COST_ESCALATION_BPS, (5.0, 10.0, 15.0))
        self.assertEqual(pt.COST_ESCALATION_SHARPE_FLOOR, 0.30)
        self.assertEqual(pt.COST_ESCALATION_EW_GAP, 0.10)
        self.assertEqual(pt.BOOTSTRAP_BLOCK, 21)
        self.assertEqual(pt.BOOTSTRAP_RESAMPLES, 10000)

    def test_engine_identity_strings(self):
        self.assertEqual(pt.ENGINE_VERSION, "pt-v1-engine-1.0.0")
        for token in ("signed-softmax", "Portfolio Transformer", "LSTM", "differentiable",
                      "lambda = 0.0002"):
            self.assertIn(token, pt.ENGINE_SEMANTICS)

    def test_panel_members_are_the_registered_four(self):
        self.assertEqual(pt.PANEL_SIZE, 4)
        self.assertEqual(tuple(sorted(pt.PANEL_MEMBERS)),
                         ("BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"))


class TestSignedSoftmax(unittest.TestCase):
    def test_matches_the_records_literal_formula(self):
        rng = np.random.default_rng(11)
        s = rng.normal(0.0, 2.5, size=(17, 4))
        got = pt.signed_softmax_raw(s)
        want = np.sign(s) * np.exp(np.abs(s)) / np.exp(np.abs(s)).sum(axis=-1, keepdims=True)
        np.testing.assert_allclose(got, want, rtol=0, atol=1e-12)
        np.testing.assert_allclose(np.abs(got).sum(axis=-1), 1.0, rtol=0, atol=1e-12)

    def test_extremes_stay_finite_and_unit_gross(self):
        s = np.array([[1000.0, -1000.0, 0.0, 1e-9]])
        got = pt.signed_softmax_raw(s)
        self.assertTrue(np.isfinite(got).all())
        self.assertAlmostEqual(float(np.abs(got).sum()), 1.0, places=12)

    def test_autodiff_gradients_match_finite_differences(self):
        rng = np.random.default_rng(3)
        raw = rng.normal(0.0, 1.0, size=(5, 4))
        pt.reset_tape()
        leaf = pt.param(raw)
        node = pt.signed_softmax(leaf)
        loss = pt.sum_last(pt.mul(node, pt.const(np.arange(4.0).reshape(1, 4) + 1.0)))
        pt.backward(loss)
        ana = leaf.g
        num = np.zeros_like(raw)
        eps = 1e-6
        for i in range(raw.shape[0]):
            for j in range(raw.shape[1]):
                up = raw.copy()
                up[i, j] += eps
                dn = raw.copy()
                dn[i, j] -= eps
                wu = pt.signed_softmax_raw(up)
                wd = pt.signed_softmax_raw(dn)
                fu = float((wu * (np.arange(4.0) + 1.0)).sum())
                fd = float((wd * (np.arange(4.0) + 1.0)).sum())
                num[i, j] = (fu - fd) / (2 * eps)
        err = float(np.max(np.abs(ana - num)))
        self.assertLess(err, 1e-6, "signed-softmax gradient error %.3e" % err)


class TestCausalityStructure(unittest.TestCase):
    def test_causal_mask_is_strictly_lower_triangular(self):
        m = pt.causal_mask(4, 3)
        self.assertEqual(m.shape, (1, 12, 12))
        for i in range(12):
            for j in range(12):
                want = 0.0 if (j // 3) <= (i // 3) else pt.MASK_FILL
                self.assertEqual(float(m[0, i, j]), want)

    def test_inference_path_is_prefix_causal(self):
        panel = synth_panel()
        labels, grid, close, ret = pt._panel_matrix(panel)
        state = pt._state_tensor("tf", close, ret)
        _lo, _hi = pt._fit_bounds(state, 0, len(grid))
        rng = np.random.default_rng(5)
        params = pt.TransformerParams(rng, len(labels))
        full = pt.infer_weights("tf", params, state)
        for cut in (200, 400, 700):
            part = pt.infer_weights("tf", params, state[:cut + 1])
            np.testing.assert_array_equal(np.isfinite(part), np.isfinite(full[:cut + 1]))
            fin = np.isfinite(part) & np.isfinite(full[:cut + 1])
            if fin.any():
                dev = float(np.max(np.abs(part[fin] - full[:cut + 1][fin])))
                self.assertLess(dev, 1e-9, "prefix truncation moved the past by %.3e" % dev)


class TestStateAndRules(unittest.TestCase):
    def test_target_state_band_rule(self):
        w = np.array([[0.0, 1.0, -1.0, np.nan],
                      [0.25, -0.25, 0.2499, 1e-12]])
        st = pt._states_from_weight(w, LABELS)
        self.assertEqual(st.tolist(), [[0, 1, -1, 0], [1, -1, 0, 0]])

    def test_weight_matrix_of_the_wrong_shape_fails_closed(self):
        with self.assertRaises(SystemExit):
            pt._states_from_weight(np.zeros((3, 5)), LABELS)
        with self.assertRaises(SystemExit):
            pt._states_from_weight(np.zeros((3, 3)), LABELS)
        with self.assertRaises(SystemExit):
            pt._states_from_weight(np.zeros(4), LABELS)

    def test_fresh_events_mark_only_changes_into_a_non_zero_state(self):
        state = np.array([[0, 0], [1, 0], [1, -1], [1, 0], [0, 0]])
        ev = pt._fresh_events(state)
        # leaving a position (state -> 0) is an EXIT and is not a fresh entry event
        self.assertEqual(ev.tolist(), [[0, 0], [1, 0], [0, -1], [0, 0], [0, 0]])

    def test_equal_weight_arm_is_one_over_n(self):
        panel = synth_panel()
        _l, _g, close, ret = pt._panel_matrix(panel)
        w = pt._rule_weights("ew", close, ret)
        np.testing.assert_allclose(w, 1.0 / len(LABELS))

    def test_tsmom_arm_matches_an_explicit_loop(self):
        panel = synth_panel()
        _l, _g, close, ret = pt._panel_matrix(panel)
        w = pt._rule_weights("tsmom", close, ret)
        n = len(LABELS)
        for t in (pt.TSMOM_WINDOW - 1, pt.TSMOM_WINDOW + 5, ret.shape[0] - 1):
            blk = ret[t - pt.TSMOM_WINDOW + 1:t + 1]
            want = np.sign(blk.mean(axis=0)) / float(n)
            np.testing.assert_allclose(w[t], want, rtol=0, atol=1e-12)
        self.assertTrue(np.isnan(w[:pt.TSMOM_WINDOW - 1]).all())

    def test_risk_parity_arm_matches_inverse_volatility(self):
        panel = synth_panel()
        _l, _g, close, ret = pt._panel_matrix(panel)
        w = pt._rule_weights("rp", close, ret)
        t = ret.shape[0] - 1
        blk = ret[t - pt.RP_VOL_WINDOW + 1:t + 1]
        inv = 1.0 / np.maximum(blk.std(axis=0), 1e-6)
        want = inv / inv.sum()
        np.testing.assert_allclose(w[t], want, rtol=1e-9, atol=1e-12)
        np.testing.assert_allclose(np.abs(w[t]).sum(), 1.0, rtol=0, atol=1e-9)

    def test_sharpe_loss_matches_a_plain_numpy_rewrite(self):
        rng = np.random.default_rng(13)
        w_cur = pt.signed_softmax_raw(rng.normal(0, 1, size=(9, 4)))
        w_prev = pt.signed_softmax_raw(rng.normal(0, 1, size=(9, 4)))
        r_next = rng.normal(0, 0.02, size=(9, 4))
        pt.reset_tape()
        node = pt.sharpe_loss(pt.const(w_cur), pt.const(w_prev), r_next, pt.TURNOVER_LAMBDA)
        pt.backward(node)
        gross = (w_cur * r_next).sum(axis=1)
        net = gross - pt.TURNOVER_LAMBDA * np.abs(w_cur - w_prev).sum(axis=1)
        want = -float(net.mean() / np.sqrt(net.var() + 1e-12))
        self.assertAlmostEqual(float(np.asarray(node.v).ravel()[0]), want, places=10)

    def test_panel_and_state_tensor_invariants(self):
        panel = synth_panel()
        labels, grid, close, ret = pt._panel_matrix(panel)
        self.assertEqual(labels, LABELS)
        self.assertEqual(close.shape, (N_BARS, 4))
        self.assertTrue(np.isnan(ret[0]).all())
        self.assertTrue(np.isfinite(ret[1:]).all())
        state = pt._state_tensor("tf", close, ret)
        self.assertEqual(state.shape, (N_BARS, pt.LOOKBACK, 4, 4))
        self.assertTrue(np.isnan(state[:pt.LOOKBACK - 1]).all())
        fx = pt._state_tensor("tfx", close, ret)
        self.assertEqual(fx.shape[3], 4 + fx.shape[3] - 4)
        self.assertGreater(fx.shape[3], state.shape[3])

    def test_panel_needs_the_registered_four_markets(self):
        with self.assertRaises(SystemExit):
            pt._panel_matrix({LABELS[0]: synth_panel()[LABELS[0]]})
        with self.assertRaises(SystemExit):
            pt._panel_matrix({m: synth_panel()[m] for m in LABELS[:3]})


class TestReadersAndFailClosed(unittest.TestCase):
    def test_reader_shell_is_never_pass_bearing(self):
        shell = pt._reader_shell("record-item", "definition")
        self.assertFalse(shell["evaluated"])
        self.assertFalse(shell["hit"])
        self.assertIn("NEVER", shell["landing"])

    def test_grid_cases_that_are_not_the_registered_order_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            spec = {
                "family_id": "t", "round_id": "t-r1", "run_id": "t-r1-u1",
                "task_id": "t", "kanban_board": "b",
                "script": {"sha256": pt.sha256_file(os.path.abspath(ENGINE))},
                "parameter_domain": {"grid_cases": [[0], [1]]},
                "dca_domain": {"grid": []}, "expected": {}, "gates": {}, "costs": {},
                "falsification": {},
                "selector_version": pt.SELECTOR_VERSION,
                "disposition_version": pt.DISPOSITION_VERSION,
            }
            path = os.path.join(td, "run-spec.json")
            with open(path, "w") as fh:
                import json
                json.dump(spec, fh)
            p = subprocess.run([sys.executable, os.path.abspath(ENGINE), path],
                               capture_output=True, text=True, timeout=300)
            self.assertNotEqual(p.returncode, 0)
            self.assertIn("grid_cases", p.stderr + p.stdout)

    def test_panel_report_reads_variant_safely_for_rule_arms(self):
        """Diagnostics path: the panel report must not require transformer-only keys.

        The record's own rule arms (mvo/tsmom/rp/ew) carry neither `variant` nor `smoothing`
        in their panel diag, so the report has to read them safely and label the variant with
        the registered arm name.  This is the exact defect class that killed the u2 run after
        the fits and before any artifact.
        """
        for i, arm in enumerate(pt.POLICY_ARMS):
            case = (i,)
            diag = {"warmup_bars": 1, "defined_bars": 2}
            row = {"variant": diag.get("variant", pt.POLICY_ARMS[pt.case_index(case)]),
                   "smoothing": diag.get("smoothing")}
            self.assertEqual(row["variant"], arm)
            self.assertIsNone(row["smoothing"])
        src = open(ENGINE).read()
        self.assertEqual(src.count('p.diag["variant"]'), 0)
        self.assertEqual(src.count('p.diag["smoothing"]'), 0)
        self.assertEqual(src.count('p.diag.get("variant"'), 1)
        self.assertEqual(src.count('p.diag.get("smoothing")'), 1)

    def test_source_carries_no_foreign_family_tokens(self):
        src = open(ENGINE).read()
        forbidden = ["cmt", "CMT", "growth_defensive", "DVOL_ROOT", "SPOT_ROOT",
                     "growth_basket", "stationary_random_placebo", "rate_hike"]
        bad = [t for t in forbidden if re.search(r"\b%s\b" % re.escape(t), src)]
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
