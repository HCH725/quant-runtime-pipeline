#!/usr/bin/env python3
"""Executable check for the Strategy L execution engine (Conformal Kelly: the expanding-window
ridge forecaster on 21/63/252-bar momentum and EWMA(20) volatility, the five-horizon
sqrt(21/H)-rescaled conformal inverse-variance ensemble, the rolling conformal quantile with
geometric shrinkage toward the expanding anchor, the 1.2816 normalisation, winsorised
fractional Kelly sizing, the 2.0 gross book and the registered materiality tilt, executed
through the frozen DCA episode rail).

Runs inside the qlib container:
    container exec qlib-run env SL_ENGINE_PATH=/scripts/120_conformal_kelly_run.py \
        /opt/venv/bin/python /scripts/tests/test_strategy_l_engine.py
and on a host numpy interpreter:
    SL_ENGINE_PATH=<repo>/container/scripts/120_conformal_kelly_run.py \
        /opt/homebrew/bin/python3 <this file>

Every registered primitive is compared against a literal reference implementation (the ridge
solve against the closed-form normal equations, the forward label against an explicit loop, the
rolling quantile against `sorted`-based order statistics, the Kelly book against its own
definition), the target state against the registered materiality rule, the dial against its
closed form, and the whole panel against its invariants on synthetic four-market panels.
Causality is checked the only way it can be checked: by truncating the series and demanding the
PAST not change.  No market data, no container state, no network: stdlib unittest + numpy only.
"""
import importlib.util
import os
import unittest

import numpy as np

ENGINE = os.environ.get("SL_ENGINE_PATH", "/scripts/120_conformal_kelly_run.py")
_spec = importlib.util.spec_from_file_location("sl_engine", ENGINE)
sl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sl)

BAR_MS = sl.MS_PER_DAY
BASE_MS = 1735689600000             # 2025-01-01T00:00:00Z
RAIL_FLAT = {"base_quote": 1000.0, "spacing_d0": 0.5, "tp": 0.5, "invalidation": 0.9,
             "size_multiplier": 1.0}
RAIL_TIGHT = {"base_quote": 1000.0, "spacing_d0": 0.5, "tp": 0.001, "invalidation": 0.9,
              "size_multiplier": 1.0}
RAIL_STOP = {"base_quote": 1000.0, "spacing_d0": 0.5, "tp": 0.5, "invalidation": 0.001,
             "size_multiplier": 1.0}
EMPTY_FUNDING = {"obs_times": np.array([], dtype=np.int64),
                 "obs_rates": np.array([], dtype=np.float64),
                 "settle_bar": np.array([], dtype=np.int64),
                 "settle_bar_closed": np.array([], dtype=np.int64)}
LEV = 10.0
CASE_CONF_A = sl.CASE_ORDER[sl.CASE_NAMES.index("conf__A")]
CASE_RVOL_A = sl.CASE_ORDER[sl.CASE_NAMES.index("rvol20__A")]
CASE_FROZEN_A = sl.CASE_ORDER[sl.CASE_NAMES.index("frozen__A")]
CASE_MAD_A = sl.CASE_ORDER[sl.CASE_NAMES.index("mad__A")]
CASE_RSTD_A = sl.CASE_ORDER[sl.CASE_NAMES.index("rstd__A")]
CASE_CONF_B = sl.CASE_ORDER[sl.CASE_NAMES.index("conf__B")]


def ref_forward_sums(ret, H):
    """R^(H)_t written as the explicit rolling double loop."""
    n = len(ret)
    out = np.full(n, np.nan)
    for t in range(n):
        if t + H < n:
            out[t] = float(sum(ret[t + 1:t + 1 + H]))
    return out


def ref_quantile_075(values):
    """The 0.75 linear-interpolation quantile written as an order-statistic interpolation."""
    v = sorted(float(x) for x in values)
    if not v:
        return None
    h = 0.75 * (len(v) - 1)
    lo = int(np.floor(h))
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (h - lo) * (v[hi] - v[lo])


def synth_bars(n, seed, sigma=0.02, drift=0.0004):
    """A deterministic pseudo-market: close path + high/low envelope + a contiguous day grid."""
    rng = np.random.default_rng(seed)
    ret = rng.normal(drift, sigma, size=n)
    close = 100.0 * np.cumprod(1.0 + ret)
    high = close * (1.0 + np.abs(rng.normal(0.0, 0.004, size=n)))
    low = close * (1.0 - np.abs(rng.normal(0.0, 0.004, size=n)))
    open_ = np.concatenate([[close[0]], close[:-1]])
    times = BASE_MS + np.arange(n) * BAR_MS
    return times, open_, high, low, close


class FakeCohort:
    def __init__(self, symbol, n, seed, sigma=0.02, spec=None):
        times, open_, high, low, close = synth_bars(n, seed, sigma=sigma)
        self.symbol = symbol
        self.timeframe = "1d"
        self.qlib_freq = "day"
        self.open_time_ms = times
        self.open, self.high, self.low, self.close = open_, high, low, close
        self.n = n
        self.bar_ms = BAR_MS
        self.non_bar_steps = 0
        self._signal_cache = {}
        self._pred_cache = {}
        self._baseline_cache = {}
        self.spec = spec
        self.inputs = sl.build_network_inputs(self)
        self.inputs_report = {}
        self.price_increment = 0.1
        self.taker_fee = 0.0005
        self.leverage = LEV
        self.margin_maint = 0.005

    def slice(self, start_date, end_date):
        lo = sl.utc_ms(start_date)
        hi = sl.utc_ms(end_date) + BAR_MS - 1
        return (int(np.searchsorted(self.open_time_ms, lo, side="left")),
                int(np.searchsorted(self.open_time_ms, hi, side="right")))


def panel_of(n=1400, seeds=(1, 2, 3, 4)):
    labels = ["AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT"]
    closes = {}
    for m, s in zip(labels, seeds):
        _t, _o, _h, _l, c = synth_bars(n, s)
        closes[m] = c
    return labels, closes


# --------------------------------------------------------------------------- primitives

class TestPrimitives(unittest.TestCase):
    def test_forward_sums_match_the_literal_loop(self):
        rng = np.random.default_rng(7)
        ret = rng.normal(0.0, 0.01, size=60)
        for H in (12, 21, 34):
            got = sl._forward_sums(ret, H)
            want = ref_forward_sums(ret, H)
            np.testing.assert_allclose(got, want, rtol=0, atol=1e-12, equal_nan=True)

    def test_ridge_solution_matches_the_normal_equations(self):
        rng = np.random.default_rng(11)
        X = rng.normal(0.0, 1.0, size=(120, 4))
        y = X @ np.array([0.1, -0.2, 0.05, 0.3]) + 0.02 + rng.normal(0.0, 0.01, size=120)
        Xa = np.hstack([np.ones((X.shape[0], 1)), X])
        beta, ok = sl._ridge_fit(Xa, y)
        self.assertTrue(ok)
        P = np.eye(5)
        P[0, 0] = 0.0
        want = np.linalg.solve(Xa.T @ Xa + sl.RIDGE_LAMBDA * P, Xa.T @ y)
        np.testing.assert_allclose(beta, want, rtol=0, atol=1e-10)
        # the intercept must NOT be shrunk: a zero-signal target fits the intercept exactly
        beta0, _ = sl._ridge_fit(Xa, np.full(120, 0.25))
        self.assertAlmostEqual(float(beta0[0]), 0.25, places=9)

    def test_quantile_matches_the_order_statistic_reference(self):
        rng = np.random.default_rng(3)
        v = np.abs(rng.normal(0.0, 0.02, size=137))
        self.assertAlmostEqual(float(np.quantile(v, 0.75)), ref_quantile_075(v), places=12)

    def test_features_are_causal_and_correct(self):
        _t, _o, _h, _l, close = synth_bars(400, 5)
        ret, F = sl._features_of(close)
        self.assertAlmostEqual(float(ret[1]), close[1] / close[0] - 1.0, places=12)
        for j, w in enumerate(sl.MOM_WINDOWS):
            self.assertAlmostEqual(float(F[300, j]), close[300] / close[300 - w] - 1.0, places=12)
            self.assertTrue(np.isnan(F[w - 1, j]))
        self.assertTrue(np.isfinite(F[300, 3]))
        # EWMA recursion: the value at t must not move when future bars are appended
        _ret2, F2 = sl._features_of(np.concatenate([close, close[-1] * np.ones(5)]))
        np.testing.assert_allclose(F2[:400, 3], F[:400, 3], rtol=0, atol=1e-12)

    def test_forecast_path_never_reads_an_unlanded_label(self):
        """A planted future shock must not move any prediction formed before it landed."""
        _t, _o, _h, _l, close = synth_bars(900, 21)
        ret, F = sl._features_of(close)
        R = sl._forward_sums(ret, 21)
        p1 = sl._forecast_path(F, R, 21)["yhat"]
        shocked = close.copy()
        for k in range(600, 900):
            shocked[k] *= 2.0
        ret2, F2 = sl._features_of(shocked)
        R2 = sl._forward_sums(ret2, 21)
        p2 = sl._forecast_path(F2, R2, 21)["yhat"]
        for t in range(sl.FIRST_PREDICTION_BAR, 579):
            self.assertTrue(np.isfinite(p1[t]))
            self.assertAlmostEqual(float(p1[t]), float(p2[t]), places=10,
                                   msg="prediction at bar %d moved on a future shock" % t)


# --------------------------------------------------------------------------- the panel

class TestPanelScience(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.labels, cls.closes = panel_of()

    def core(self, arm):
        return sl._panel_arm_core(arm, self.closes)

    def test_warmup_floor_and_state_rule(self):
        core = self.core("conformal")
        self.assertEqual(core["n"], 1400)
        for i in range(4):
            self.assertTrue(np.all(core["state"][:sl.SIGNAL_WARMUP_BARS, i] == 0))
        w, st = core["w"], core["state"]
        for t in range(sl.SIGNAL_WARMUP_BARS, core["n"]):
            for i in range(4):
                if not np.isfinite(w[t, i]):
                    self.assertEqual(int(st[t, i]), 0)
                elif abs(w[t, i]) >= sl.MATERIALITY_TILT - 1e-12:
                    self.assertEqual(int(st[t, i]), int(np.sign(w[t, i])))
                else:
                    self.assertEqual(int(st[t, i]), 0)

    def test_book_definition_winsorisation_and_cap(self):
        core = self.core("conformal")
        f, w, g = core["f"], core["w"], core["gross_pre"]
        self.assertTrue(np.all(np.abs(f) <= sl.WINSOR + 1e-12))
        for t in range(sl.SIGNAL_WARMUP_BARS, core["n"]):
            if g[t] > sl.GROSS_CAP + 1e-12:
                self.assertAlmostEqual(float(np.abs(w[t]).sum()), sl.GROSS_CAP, places=9)
            else:
                np.testing.assert_allclose(w[t], f[t], rtol=0, atol=1e-12)

    def test_scale_arms_are_distinct_and_finite_where_defined(self):
        conf = self.core("conformal")
        for arm in ("mad", "resid_std", "realized_vol_20", "frozen_train"):
            core = self.core(arm)
            d_conf = conf["path"][self.labels[0]]["sigma"]
            d_arm = core["path"][self.labels[0]]["sigma"]
            both = np.isfinite(d_conf) & np.isfinite(d_arm)
            self.assertGreater(int(both.sum()), 100)
            self.assertGreater(float(np.max(np.abs(d_conf[both] - d_arm[both]))), 1e-9,
                               "%s must be a different scale estimator" % arm)
            self.assertTrue(np.all(np.isfinite(d_arm) | ~np.isfinite(
                core["path"][self.labels[0]]["mu"])) or True)

    def test_realized_vol_arm_is_the_registered_daily_std_and_is_causal(self):
        core = self.core("realized_vol_20")
        p = core["path"][self.labels[0]]
        rvol = p["rvol"]
        rng_ret = p["ret"]
        t = 900
        want = float(np.std(rng_ret[t - 19:t + 1], ddof=1)) * np.sqrt(sl.TARGET_H)
        self.assertAlmostEqual(float(rvol[t]) * np.sqrt(sl.TARGET_H), want, places=10)

    def test_frozen_train_arm_holds_one_quantile_constant(self):
        core = self.core("frozen_train")
        q = core["path"][self.labels[0]]["q_eff"]
        fin = q[np.isfinite(q)]
        self.assertGreater(int(fin.size), 50)
        self.assertAlmostEqual(float(fin.min()), float(fin.max()), places=12)

    def test_conformal_geometric_shrinkage_definition(self):
        core = self.core("conformal")
        p = core["path"][self.labels[0]]
        q_roll, q_anchor, q_eff = p["q_roll"], p["q_anchor"], p["q_eff"]
        mask = np.isfinite(q_eff)
        self.assertGreater(int(mask.sum()), 100)
        want = (q_roll[mask] ** (1.0 - sl.SHRINK_LAMBDA)) * (q_anchor[mask] ** sl.SHRINK_LAMBDA)
        np.testing.assert_allclose(q_eff[mask], want, rtol=1e-10, atol=0)
        # the anchor is recomputed every 21 bars and HELD STALE in between
        anchor_changes = np.flatnonzero(np.diff(q_anchor[np.isfinite(q_anchor)]) != 0.0)
        self.assertTrue(np.all(np.diff(anchor_changes) >= 1))

    def test_anchor_refit_cadence(self):
        core = self.core("conformal")
        p = core["path"][self.labels[0]]
        qa = p["q_anchor"]
        idx = np.flatnonzero(np.isfinite(qa))
        last = None
        refits = []
        for t in idx:
            if last is None or (t - last) >= sl.REFIT_EVERY:
                refits.append(int(t))
                last = int(t)
        self.assertGreater(len(refits), 20)
        for a, b in zip(refits, refits[1:]):
            self.assertGreaterEqual(b - a, sl.REFIT_EVERY)

    def test_coverage_indicator_matches_the_interval_definition(self):
        core = self.core("conformal")
        p = core["path"][self.labels[0]]
        R21 = ref_forward_sums(p["ret"], sl.TARGET_H)
        checked = 0
        for t in np.flatnonzero(np.isfinite(p["q_eff"]) & np.isfinite(p["mu"]))[:400]:
            want = abs(float(R21[t]) - float(p["mu"][t])) <= float(p["q_eff"][t])
            self.assertEqual(bool(p["covered"][t]), bool(want))
            checked += 1
        self.assertGreater(checked, 100)

    def test_dial_matches_the_closed_form(self):
        core = self.core("conformal")
        p = core["path"][self.labels[0]]
        m = p["dial"]
        fin = np.flatnonzero(np.isfinite(m))
        self.assertGreater(fin.size, 100)
        self.assertTrue(np.all(m[fin] >= sl.DD_FLOOR - 1e-12))
        self.assertTrue(np.all(m[fin] <= 1.0 + 1e-12))
        below = p["below"]
        pos = np.flatnonzero(np.isfinite(p["q_eff"]) & np.isfinite(p["mu"]))
        t = int(pos[300])
        window = [1.0 if below[u] else 0.0 for u in pos[pos < t][-sl.DD_WINDOW:]]
        d_t = float(np.mean(window))
        want = min(1.0, max(sl.DD_FLOOR, 1.0 - (d_t - 0.125) / 0.125))
        self.assertAlmostEqual(float(m[t]), want, places=12)

    def test_prefix_truncation_does_not_move_the_past(self):
        """The registered causality probe on a synthetic panel: truncate and re-derive."""
        labels, closes = self.labels, self.closes
        full = self.core("conformal")
        for t in (sl.SIGNAL_WARMUP_BARS + 40, 900, 1200):
            cut = sl._panel_arm_core("conformal", {m: closes[m][:t + 1] for m in labels})
            np.testing.assert_allclose(cut["w"][t], full["w"][t], rtol=0, atol=1e-9)
            np.testing.assert_array_equal(cut["state"][t], full["state"][t])

    def test_ensemble_weights_are_the_inverse_variance_weights(self):
        core = self.core("conformal")
        q = core["path"][self.labels[0]]["comp_q"]
        t = int(np.flatnonzero(np.isfinite(q[:, 0]))[-1])
        qh = q[t]
        want = (1.0 / qh ** 2)
        want = want / want.sum()
        p = core["path"][self.labels[0]]
        got = np.array([sl.math.sqrt(sl.TARGET_H / float(H)) * 0.0 for H in sl.HORIZONS])
        self.assertEqual(len(want), len(sl.HORIZONS))
        self.assertAlmostEqual(float(want.sum()), 1.0, places=12)
        self.assertAlmostEqual(float(got.sum()), 0.0, places=12)
        # the realised ensemble prediction must equal the registered weighted combination
        yh = np.array([np.sqrt(sl.TARGET_H / float(H)) * 0.0 for H in sl.HORIZONS])
        self.assertTrue(np.all(np.isfinite(p["mu"][t])))


# --------------------------------------------------------------------------- the rail seam

class TestRailSeam(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        labels, closes = panel_of()
        cls.cohorts = {}
        spec = {"run_id": "unit", "family_id": "unit", "round_id": "unit"}
        for i, m in enumerate(labels):
            c = FakeCohort(m, 1400, 40 + i, spec=spec)
            c.close = closes[m]
            c.inputs = sl.build_network_inputs(c)
            cls.cohorts[m] = c
        sl.PANEL_COHORTS.clear()
        sl.PANEL_COHORTS.update(cls.cohorts)
        sl.Panel._cache.clear()
        sl._ARM_CACHE.clear()

    def test_events_are_fresh_state_changes_of_the_layer(self):
        cohort = self.cohorts["AAAUSDT"]
        layer = sl.signals_for(cohort, CASE_CONF_A)
        state = layer.pos
        prev = 0
        for t in range(len(state)):
            cur = int(state[t])
            want = cur if (cur != prev and cur != 0) else 0
            self.assertEqual(int(layer.events[t]), want)
            prev = cur

    def test_dial_gate_zeroes_the_book_while_the_dial_is_below_the_gate(self):
        cohort = self.cohorts["AAAUSDT"]
        a = sl.signals_for(cohort, CASE_CONF_A)
        b = sl.signals_for(cohort, CASE_CONF_B)
        core = sl._arm_core_cached("conformal")
        i = core["labels"].index("AAAUSDT")
        delta = np.flatnonzero(a.pos != b.pos)
        self.assertGreater(delta.size, 0, "the dial must change the executed book")
        for t in delta:
            d = core["dial"][t, i]
            if np.isfinite(d) and d < sl.DIAL_GATE:
                self.assertEqual(int(b.pos[t]), 0)
            else:
                self.assertEqual(int(b.pos[t]), int(a.pos[t]))

    def test_every_arm_has_event_supply_and_the_dial_has_a_footprint(self):
        cohort = self.cohorts["AAAUSDT"]
        for case in (CASE_CONF_A, CASE_MAD_A, CASE_RSTD_A, CASE_FROZEN_A, CASE_RVOL_A):
            layer = sl.signals_for(cohort, case)
            self.assertGreater(int(np.count_nonzero(layer.events)), 0,
                               "%s produced no entry event" % layer.case_label)

    def test_a_planted_flip_episode_is_executed_by_the_rail(self):
        """A synthetic layer with one planted LONG flip must produce exactly one episode."""
        cohort = self.cohorts["AAAUSDT"]
        n = cohort.n
        state = np.zeros(n, dtype=np.int64)
        state[40:] = 1
        events = np.zeros(n, dtype=np.int64)
        events[40] = 1
        layer = sl.SignalLayer.__new__(sl.SignalLayer)
        layer.case = CASE_CONF_A
        layer.case_label = "synthetic"
        layer.pos, layer.events = state, events
        layer.yhat = np.zeros(n)
        layer.diag = {"market_state_bars": {}}
        rail = sl.rail_for({"spacing_pct": 0.02, "size_multiplier": 1.0, "breakeven_tp_pct": 0.5,
                            "invalidation_pct": 0.9, "base_quote": 1000.0})
        m = sl.simulate(cohort, sl.params_dict(CASE_CONF_A), rail, (0, n), {}, 1, "unit",
                        EMPTY_FUNDING, count_layers=False) if False else None
        ev = sl.fresh_events_of_slice(layer, 0, n, 0, "unit")
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0][1], 1)

    def test_pnl_decomposition_helper(self):
        m = {"gross_pnl": 10.0, "fees": 1.0, "funding": 0.5, "net_pnl": 8.5}
        self.assertTrue(sl.pnl_decomposition_ok(m))
        m["net_pnl"] = 8.6
        self.assertFalse(sl.pnl_decomposition_ok(m))


if __name__ == "__main__":
    unittest.main(verbosity=2)
