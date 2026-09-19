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


class FakeCohort:
    """The cohort fields `simulate` / `_metrics` read, without any data file or container state."""

    def __init__(self, n=N_BARS, seed=11, symbol="BTCUSDT"):
        rng = np.random.default_rng(seed)
        close = 100.0 * np.cumprod(1.0 + rng.normal(0.0, 0.004, size=n))
        self.symbol = symbol
        self.close = close
        self.high = close * 1.01
        self.low = close * 0.99
        self.open = close * 0.999
        self.open_time_ms = np.arange(n, dtype=np.int64) * BAR_MS + BASE_MS
        self.bar_ms = BAR_MS
        self.price_increment = 0.1
        self.taker_fee = 0.0004
        self.leverage = 10.0
        self.margin_maint = 0.0


REGISTERED_DCA = {"base_quote": 1000, "spacing_pct": 0.01, "size_multiplier": 1.0,
                  "breakeven_tp_pct": 0.01, "invalidation_pct": 0.05}
EMPTY_FUNDING = {"obs_times": np.array([], dtype=np.int64), "obs_rates": np.array([]),
                 "settle_bar": np.array([], dtype=np.int64)}


class TestSliceDiagnosticsContract(unittest.TestCase):
    """The readers' diagnostics path: a flat slice is a flat book, not a missing measurement."""

    def _layer(self, cohort, n=N_BARS, side=0.5, flip_at=None, label="reader_side"):
        w = np.zeros((n, len(LABELS)))
        j = LABELS.index(cohort.symbol)
        w[:, j] = side
        if flip_at is not None:
            w[flip_at:, j] = -side
        return pt.SyntheticLayer(cohort, LABELS, w, w[:, j], label,
                                 "one reader-side weight path over the same rail")

    def test_no_episode_slice_publishes_the_full_diagnostics_block(self):
        """The kernel's no-trade exit must publish every diagnostics key the readers read.

        The registered readers re-derive a policy path over a slice; when the arm never traded
        in it the kernel returned BEFORE building its diagnostics, so the readers' own
        `diagnostics["daily_equity"]` read raised KeyError('daily_equity') after the whole grid
        had already been written - the r1-u3 terminal.  A flat slice must publish the same keys
        as the traded path, and the diagnostics flag must still short-circuit to {}.
        """
        cohort = FakeCohort()
        rail = pt.rail_for(REGISTERED_DCA)
        flat = self._layer(cohort, side=0.0, label="flat__never_enters")
        m = pt.simulate(cohort, {"arm": 0}, rail, (0, N_BARS), {}, 0, "test_no_episode",
                        EMPTY_FUNDING, diag=True, layer=flat)
        self.assertEqual(m["windows_seen"], 0)
        # the readers' own read (the r1-u3 terminal raised KeyError('daily_equity') here)
        _readers_read = m["diagnostics"]["daily_equity"]
        for key in ("pnl_by_year", "episode_stats", "signal_diagnostics", "signal_case",
                    "daily_equity", "daily_equity_days", "slice_first_day_utc",
                    "slice_last_day_utc"):
            self.assertIn(key, m["diagnostics"])
        self.assertEqual(_readers_read, m["diagnostics"]["daily_equity"])
        self.assertEqual(m["diagnostics"]["episode_stats"]["episodes"], 0)
        series = np.asarray(m["diagnostics"]["daily_equity"], dtype=np.float64)
        self.assertEqual(len(series), N_BARS)
        self.assertEqual(m["diagnostics"]["daily_equity_days"], len(series))
        self.assertEqual(float(series.min()), float(series.max()))       # a flat book
        self.assertEqual(m["diagnostics"]["signal_diagnostics"], {})
        self.assertTrue(m["diagnostics"]["slice_first_day_utc"].startswith("2025-01-01"))
        self.assertTrue(m["diagnostics"]["slice_last_day_utc"] > m["diagnostics"][
            "slice_first_day_utc"])
        off = pt.simulate(cohort, {"arm": 0}, rail, (0, N_BARS), {}, 0, "test_no_episode",
                          EMPTY_FUNDING, diag=False, layer=flat)
        self.assertEqual(off["diagnostics"], {})

    def test_reader_side_layer_without_panel_diagnostics_still_trades(self):
        """A seeded initialization's own weight path carries no panel diag and must not crash.

        `_seed_layer` hands `simulate` a reader-side layer built from one seed's weights.  The
        traded path read `layer.diag` directly, so any seeded path with an episode raised
        AttributeError in the reader phase - an independent instance of the same defect class.
        """
        cohort = FakeCohort()
        rail = pt.rail_for(REGISTERED_DCA)
        layer = self._layer(cohort, flip_at=N_BARS // 2, label="seed__own_path")
        self.assertFalse(hasattr(layer, "diag"))
        m = pt.simulate(cohort, {"arm": 0}, rail, (0, N_BARS), {"no_funding": True}, 0,
                        "test_reader_side_layer", {}, diag=True, layer=layer)
        self.assertGreaterEqual(m["windows_seen"], 1)
        self.assertEqual(m["diagnostics"]["signal_diagnostics"], {})
        self.assertEqual(m["diagnostics"]["signal_case"], "seed__own_path")
        self.assertEqual(m["diagnostics"]["daily_equity_days"],
                         len(m["diagnostics"]["daily_equity"]))
        self.assertIn("episodes", m["diagnostics"]["episode_stats"])

    def test_both_exits_build_the_block_through_one_helper(self):
        """Source shape: the traded exit and the no-trade exit share the diagnostics builder."""
        src = open(ENGINE).read()
        self.assertEqual(src.count("def _slice_diag_block("), 1)
        # one definition line + the two call sites (the traded exit and the no-trade exit)
        self.assertEqual(
            src.count("_slice_diag_block(layer, series_flat, cohort, i0, i1, diag"), 3)
        self.assertEqual(src.count("windows_entered, kind, {})"), 0)
        self.assertEqual(src.count('"signal_diagnostics": dict(layer.diag)'), 0)


    def test_stationary_bootstrap_reader_completes_on_a_registered_window(self):
        """The registered bootstrap must draw its blocks lazily enough to finish.

        The reader pre-drew `resamples * (n // block + 2)` geometric lengths - exactly the MEAN
        number of blocks one resample needs - so a resample whose blocks came out short ran past
        the end of its own array (the r1 short-window smoke: `index 310000 is out of bounds for
        axis 0 with size 310000`); on the registered window the allowance is even tighter
        (1715 // 21 + 2 = 83 against a mean need of ~82).  The registered procedure is unchanged:
        mean block 21, 10,000 resamples, one registered seed, uniform block starts.
        """
        rng = np.random.default_rng(5)
        a = rng.normal(0.0, 0.01, size=120)
        b = rng.normal(0.0, 0.01, size=120)
        p, stat = pt._stationary_bootstrap_p(a, b, block=21, resamples=10_000,
                                             seed=pt.BOOTSTRAP_SEED)
        self.assertIsNotNone(p)
        self.assertGreaterEqual(p, 0.0)
        self.assertLessEqual(p, 1.0)
        self.assertIsNotNone(stat)
        p2, _s2 = pt._stationary_bootstrap_p(a, b, block=21, resamples=10_000,
                                             seed=pt.BOOTSTRAP_SEED)
        self.assertEqual(p, p2)                       # deterministic for the registered seed
        src = open(ENGINE).read()
        self.assertEqual(src.count("blocks_per_resample"), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
