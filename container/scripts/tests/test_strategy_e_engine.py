#!/usr/bin/env python3
"""Executable check for the Strategy E pair execution engine (copula CMI pairs).

Runs inside the qlib container:
    container exec qlib-run env SE_ENGINE_PATH=/scripts/50_strategy_e_run.py \
        /opt/venv/bin/python /scripts/tests/test_strategy_e_engine.py
and on any host interpreter with numpy + scipy:
    SE_ENGINE_PATH=<repo>/container/scripts/50_strategy_e_run.py python3 <this file>

Drives the pure functions with synthetic hourly bars whose fills, fees, funding charges and
spread triggers are known by hand, so a regression in the copula densities / conditional CDFs,
the cointegration screen, the beta-hedged pair rail, the per-fill fee ledger, the per-leg
funding exposure or the independent gross accumulator fails loudly instead of silently
changing the science.  No market data, no container state, no network.
"""
import importlib.util
import math
import os
import unittest

import numpy as np

ENGINE = os.environ.get("SE_ENGINE_PATH", "/scripts/50_strategy_e_run.py")
_spec = importlib.util.spec_from_file_location("se_engine", ENGINE)
se = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(se)

BAR_MS = 3600000
BASE_MS = 1767225600000            # 2026-01-01T00:00:00Z (a clean UTC midnight)
NBARS = se.TRADING_BARS
RAIL = {"base_quote": 1000.0, "spacing_pct": 0.01, "size_multiplier": 1.0,
        "breakeven_tp_pct": 0.01, "invalidation_pct": 0.05}
CASE = {"t_o": 0.30, "t_c": 0.05, "copula_family": 0}
# A test-only case whose registered CMI reversion threshold is 0: |CMI| < 0 never holds, so a
# position stays open until the cycle-end flatten (used to expose the funding exposure window).
CASE_TC0 = {"t_o": 0.30, "t_c": 0.0, "copula_family": 0}
TICK = 0.01
FEE = 0.0005
LEV = 10.0


class FakePair:
    """Hourly bars of both legs on a clean UTC grid; bar i opens at BASE_MS + i * 1h."""

    def __init__(self, o1, h1, l1, c1, o2=None, h2=None, l2=None, c2=None, n=None,
                 tick1=TICK, tick2=TICK, fee=FEE, lev=LEV, mmaint=0.1, bar_ms=BAR_MS):
        n = n or len(c1)
        ones = [100.0] * n
        o2 = ones if o2 is None else o2
        h2 = ones if h2 is None else h2
        l2 = ones if l2 is None else l2
        c2 = ones if c2 is None else c2
        self.label = "SYNTH-SYNTH2"
        self.pair = ("SYNTH", "SYNTH2")
        self.timeframe = "1h"
        self.bar_ms = bar_ms
        self.open_time_ms = np.array([BASE_MS + i * bar_ms for i in range(n)], dtype=np.int64)
        self.day_id = self.open_time_ms // se.MS_PER_DAY
        self.day_end = np.flatnonzero(np.concatenate(
            [(self.open_time_ms[1:] // se.MS_PER_DAY) != (self.open_time_ms[:-1] // se.MS_PER_DAY),
             [True]]))
        self.legs = [{"symbol": self.pair[0], "open_time_ms": self.open_time_ms,
                      "fields": {"open": np.array(o1, dtype=float), "high": np.array(h1, dtype=float),
                                 "low": np.array(l1, dtype=float),
                                 "close": np.array(c1, dtype=float)}},
                     {"symbol": self.pair[1], "open_time_ms": self.open_time_ms,
                      "fields": {"open": np.array(o2, dtype=float), "high": np.array(h2, dtype=float),
                                 "low": np.array(l2, dtype=float),
                                 "close": np.array(c2, dtype=float)}}]
        self.meta = [{"price_increment": tick1, "taker_fee": fee, "margin_init": 1.0 / lev,
                      "margin_maint": mmaint},
                     {"price_increment": tick2, "taker_fee": fee, "margin_init": 1.0 / lev,
                      "margin_maint": mmaint}]
        self.n = n

    def slice(self, _a, _b):
        return 0, self.n

    def cycle_bounds(self):
        anchor = int(np.searchsorted(self.open_time_ms, se.utc_ms(se.CYCLE_ANCHOR), side="left"))
        out = []
        i = anchor
        while i + se.FORMATION_BARS + se.TRADING_BARS <= self.n:
            out.append({"index": len(out), "formation": (i, i + se.FORMATION_BARS),
                        "trading": (i + se.FORMATION_BARS,
                                    i + se.FORMATION_BARS + se.TRADING_BARS),
                        "formation_start_ms": int(self.open_time_ms[i]),
                        "trading_start_ms": int(self.open_time_ms[i + se.FORMATION_BARS])})
            i += se.TRADING_BARS
        return out


def flat(n, price=100.0):
    return [price] * n


def mk_signal(cmi, beta=1.0, entries=None, t0_abs=0, tradable=True, copula_family=0):
    s = se.CycleSignals()
    s.index = 0
    s.form = (0, 1)
    s.trade = (t0_abs, t0_abs + NBARS)
    s.beta = beta
    s.alpha = 0.0
    s.adf = -5.0
    s.kss = -4.0
    s.half_life_days = 5.0
    s.qualifies = tradable
    s.qualify_reason = "ok" if tradable else "cointegration_screen"
    s.copula = {}
    s.chosen_family = se.COPULA_FAMILIES[copula_family] if copula_family else "gaussian"
    s.aic = {}
    s.gof = {"pass": True, "ks_p": 0.5, "cvm_p": 0.5, "ks_stat": 0.01, "cvm_stat": 0.01,
             "bootstrap_B": se.GOF_BOOTSTRAP}
    s.tradable = tradable
    s.cmi = {copula_family: np.array(cmi, dtype=float)}
    s.entries = {}
    for oi, thr in enumerate(se.T_O_GRID):
        idx = np.flatnonzero(np.abs(np.array(cmi[:NBARS - 1])) > thr)
        s.entries[(copula_family, oi)] = ((int(idx[0]), 1 if cmi[int(idx[0])] < -thr else -1)
                                          if len(idx) else None)
    if entries is not None:
        s.entries.update(entries)
    s.saturation = {"u1_at_bounds": 0, "u2_at_bounds": 0, "trading_points": NBARS}
    return s


def mk_funding(pair, leg, settlements, rates=None):
    times = np.array([int(t) for t, _r in settlements], dtype=np.int64)
    rr = np.array([float(r) for _t, r in settlements], dtype=np.float64)
    containing = (np.searchsorted(pair.open_time_ms, times, side="left") - 1
                  if len(times) else np.array([], dtype=np.int64))
    closed = (np.searchsorted(pair.open_time_ms, times, side="right") - 1
              if len(times) else np.array([], dtype=np.int64))
    return {"label": "leg%d" % leg, "counts": {}, "obs_times": times, "obs_rates": rr,
            "settle_bar": containing.astype(np.int64),
            "settle_bar_closed": closed.astype(np.int64),
            "first_obs_ms": None, "last_obs_ms": None}


def run_cell(pair, sig, case=None, rail=None, stress=None, kind="full", series=None,
             diag=False, count_layers=True, slip=1):
    pack = se.pack_cycle(pair, {"trading": (sig.trade[0], sig.trade[0] + NBARS)}, sig.beta)
    ds = se.day_setup_for(pair, (0, pair.n))
    if series is None:
        series = [mk_funding(pair, 0, []), mk_funding(pair, 1, [])]
    return se.simulate_cell(pair, [sig], [pack], case or CASE, rail or RAIL, [0], stress or {},
                            slip, kind, series, ds, diag=diag, count_layers=count_layers)


class TestCopulaMath(unittest.TestCase):
    PARAMS = (("gaussian", {"rho": 0.5}), ("student_t", {"rho": 0.5, "nu": 5.0}),
              ("clayton", {"theta": 2.0}), ("gumbel", {"theta": 2.0}),
              ("frank", {"theta": 3.0}))

    def test_densities_integrate_to_one(self):
        g = (np.arange(512) + 0.5) / 512.0
        u1, u2 = np.meshgrid(g, g)
        for fam, th in self.PARAMS:
            dens = np.exp(se._log_cond_density(fam, th, u1.ravel(), u2.ravel()))
            integral = float(np.sum(dens)) / (512.0 * 512.0)
            self.assertAlmostEqual(integral, 1.0, delta=0.02, msg=fam)

    def test_density_is_the_u1_derivative_of_the_conditional_cdf(self):
        for fam, th in self.PARAMS:
            for u1 in (0.2, 0.5, 0.8):
                for u2 in (0.3, 0.7):
                    h = 1e-6
                    num = (se.copula_cond_cdf(fam, th, u1 + h, u2)
                           - se.copula_cond_cdf(fam, th, u1 - h, u2)) / (2.0 * h)
                    ana = math.exp(float(se._log_cond_density(fam, th, u1, u2)))
                    self.assertAlmostEqual(float(num) / ana, 1.0, delta=1e-3,
                                           msg="%s u1=%s u2=%s" % (fam, u1, u2))

    def test_conditional_cdf_boundaries_and_monotonicity(self):
        for fam, th in self.PARAMS:
            self.assertAlmostEqual(float(se.copula_cond_cdf(fam, th, 1.0, 0.4)), 1.0, delta=1e-6,
                                   msg=fam)
            self.assertLess(float(se.copula_cond_cdf(fam, th, 1e-7, 0.4)), 1e-3, msg=fam)
            xs = np.linspace(0.01, 0.99, 40)
            ys = se.copula_cond_cdf(fam, th, xs, 0.6)
            self.assertTrue(np.all(np.diff(ys) > 0.0), fam)

    def test_conditional_inverse_round_trips(self):
        for fam, th in self.PARAMS:
            u2 = np.array([0.15, 0.5, 0.85])
            w = np.array([0.1, 0.5, 0.9])
            u1 = se.copula_cond_inverse(fam, th, w, u2)
            back = se.copula_cond_cdf(fam, th, u1, u2)
            self.assertTrue(np.allclose(back, w, atol=1e-6), "%s %s" % (fam, back))

    def test_fit_recovers_the_simulated_parameters(self):
        rng = np.random.default_rng(7)
        u1, u2 = se.sample_copula("gaussian", {"rho": 0.6}, 4000, rng)
        th, _ll, k = se.fit_copula("gaussian", u1, u2)
        self.assertAlmostEqual(th["rho"], 0.6, delta=0.05)
        self.assertEqual(k, 1)
        u1, u2 = se.sample_copula("clayton", {"theta": 2.0}, 4000, rng)
        th, _ll, _k = se.fit_copula("clayton", u1, u2)
        self.assertAlmostEqual(th["theta"], 2.0, delta=0.35)
        u1, u2 = se.sample_copula("student_t", {"rho": 0.5, "nu": 6.0}, 4000, rng)
        th, _ll, k = se.fit_copula("student_t", u1, u2)
        self.assertAlmostEqual(th["rho"], 0.5, delta=0.08)
        self.assertAlmostEqual(th["nu"], 6.0, delta=3.0)
        self.assertEqual(k, 2)

    def test_aic_prefers_the_true_family_on_a_gaussian_sample(self):
        rng = np.random.default_rng(11)
        u1, u2 = se.sample_copula("gaussian", {"rho": 0.7}, 3000, rng)
        aics = {}
        for fam in se.COPULA_FAMILIES:
            _th, ll, k = se.fit_copula(fam, u1, u2)
            aics[fam] = 2.0 * k - 2.0 * ll
        # with a symmetric sample the elliptical families are near-equivalent; the registered
        # rule is the argmin, so the test asserts the true family is at least competitive
        self.assertLessEqual(aics["gaussian"] - min(aics.values()), 2.0)
        _th, ll, k = se.fit_copula("gaussian", u1, u2)
        direct = float(np.sum(se._log_cond_density("gaussian", _th, u1, u2)))
        self.assertAlmostEqual(ll, direct, places=6)
        self.assertEqual(k, 1)

    def test_gof_accepts_the_true_family_and_rejects_a_wrong_one(self):
        rng = np.random.default_rng(3)
        u1, u2 = se.sample_copula("clayton", {"theta": 2.0}, 1500, rng)
        th_true, _ll, _k = se.fit_copula("clayton", u1, u2)
        good = se.copula_gof("clayton", th_true, u1, u2, rng, B=30)
        self.assertTrue(good["pass"], good)
        th_g, _ll, _k = se.fit_copula("gaussian", u1, u2)
        bad = se.copula_gof("gaussian", th_g, u1, u2, rng, B=30)
        self.assertFalse(bad["pass"], bad)

    def test_marginal_cdf_is_the_registered_frozen_rule(self):
        ref = np.sort(np.array([1.0, 2.0, 3.0, 4.0]))
        got = se.apply_empirical_cdf(ref, np.array([0.5, 2.0, 4.0, 9.0]))
        self.assertTrue(np.allclose(got, [0.0, 0.5, 1.0, 1.0]))


class TestCointegrationScreen(unittest.TestCase):
    def _ar1(self, phi, n, seed):
        rng = np.random.default_rng(seed)
        e = rng.standard_normal(n)
        x = np.empty(n)
        x[0] = 0.0
        for i in range(1, n):
            x[i] = phi * x[i - 1] + e[i]
        return x

    def test_adf_rejects_a_stationary_series_and_not_a_random_walk(self):
        stationary = self._ar1(0.9, 2160, 1)
        self.assertLess(se.adf_tstat(stationary), se.ADF_CV_5PCT)
        rw = np.cumsum(np.random.default_rng(2).standard_normal(2160))
        self.assertGreater(se.adf_tstat(rw), se.ADF_CV_5PCT)

    def test_half_life_matches_the_ar1_formula(self):
        x = self._ar1(0.98, 6000, 3)
        hl = se.half_life_hours(x)
        # the fitted AR(1) coefficient is downward biased in a finite sample, so the recovered
        # half-life sits a few hours below the population value - the formula is what is under
        # test, not the estimator's bias
        self.assertAlmostEqual(hl, -math.log(2.0) / math.log(0.98), delta=6.0)
        self.assertLess(hl / 24.0, se.HALF_LIFE_MAX_DAYS)   # a fast-reverting residual qualifies

    def test_kss_statistic_is_finite_on_both_shapes(self):
        self.assertTrue(math.isfinite(se.kss_tstat(self._ar1(0.5, 1000, 4))))
        self.assertTrue(math.isfinite(se.kss_tstat(np.cumsum(
            np.random.default_rng(5).standard_normal(1000)))))

    def test_ols_beta_is_the_log_price_slope(self):
        rng = np.random.default_rng(6)
        l2 = np.cumsum(rng.standard_normal(500)) * 0.01 + 4.0
        l1 = 0.3 + 1.4 * l2 + rng.standard_normal(500) * 0.001
        beta, alpha = se.ols_beta(l1, l2)
        self.assertAlmostEqual(beta, 1.4, delta=0.02)
        self.assertAlmostEqual(alpha, 0.3, delta=0.05)


class TestPairExecution(unittest.TestCase):
    def test_no_entry_when_the_cmi_never_crosses(self):
        pair = FakePair(flat(NBARS), flat(NBARS), flat(NBARS), flat(NBARS))
        sig = mk_signal([0.0] * NBARS)
        m = run_cell(pair, sig)
        self.assertEqual(m["episodes"], 0)
        self.assertEqual(m["net_pnl"], 0.0)
        self.assertEqual(m["fees"], 0.0)
        self.assertEqual(m["fills"], 0)
        self.assertEqual(m["days"], 14)          # a 336-bar window spans 14 UTC days

    def test_cmi_entry_and_reversion_pay_exactly_the_registered_fees(self):
        pair = FakePair(flat(NBARS), flat(NBARS), flat(NBARS), flat(NBARS))
        cmi = [0.0] * NBARS
        cmi[0] = -0.4                       # -> long leg 1 / short leg 2, entry at bar 1 open
        sig = mk_signal(cmi)
        m = run_cell(pair, sig)
        self.assertEqual(m["episodes"], 1)
        self.assertEqual(m["cmi_exits"], 1)
        self.assertEqual(m["fills"], 4)
        self.assertAlmostEqual(m["gross_pnl"], 0.0, places=9)
        self.assertAlmostEqual(m["fees"], 20.0, places=9)
        self.assertAlmostEqual(m["net_pnl"], -20.0, places=9)
        self.assertAlmostEqual(m["ending_equity"], se.START_EQUITY - 20.0, places=9)
        m2 = run_cell(pair, sig, stress={"fee_mult": 2.0}, kind="fee_2x")
        self.assertAlmostEqual(m2["gross_pnl"], 0.0, places=9)
        self.assertAlmostEqual(m2["net_pnl"], -40.0, places=9)
        self.assertAlmostEqual(m2["fees"], 40.0, places=9)

    def test_the_entry_uses_the_next_bar_open_and_not_the_crossing_bar(self):
        o1 = flat(NBARS, 100.0)
        o1[1] = 110.0                        # the entry bar's open is the only odd price
        c1 = flat(NBARS, 100.0)
        o2 = flat(NBARS, 100.0)
        pair = FakePair(o1, flat(NBARS, 110.0), flat(NBARS, 100.0), c1, o2,
                        flat(NBARS, 100.0), flat(NBARS, 100.0), flat(NBARS, 100.0))
        cmi = [0.0] * NBARS
        cmi[0] = -0.4
        sig = mk_signal(cmi)
        m = run_cell(pair, sig)
        # leg 1 bought at 110.01 and sold at 100.01 -> -10 x 1000x10/110.01 price loss
        q1 = 1000.0 * LEV / 110.01
        expected_gross = q1 * 100.01 - q1 * 110.01
        self.assertAlmostEqual(m["gross_pnl"], expected_gross, places=6)
        self.assertLess(m["net_pnl"], expected_gross)

    def test_short_spread_direction_is_the_cmi_mirror(self):
        pair = FakePair(flat(NBARS), flat(NBARS), flat(NBARS), flat(NBARS))
        cmi = [0.0] * NBARS
        cmi[0] = 0.4                        # -> short leg 1 / long leg 2
        sig = mk_signal(cmi)
        m = run_cell(pair, sig)
        self.assertEqual(m["episodes"], 1)
        self.assertAlmostEqual(m["net_pnl"], -20.0, places=9)

    def test_hedge_ratio_sets_the_second_leg_notional(self):
        pair = FakePair(flat(NBARS), flat(NBARS), flat(NBARS), flat(NBARS))
        cmi = [0.0] * NBARS
        cmi[0] = -0.4
        sig = mk_signal(cmi, beta=2.0)
        m = run_cell(pair, sig)
        # leg 2 notional = 2 x leg 1 notional -> its fee is exactly twice leg 1's
        self.assertAlmostEqual(m["fees"], 30.0, places=9)
        self.assertEqual(se.counters_total("unhedged_entry_notional"), 0)

    def test_ladder_scales_in_and_the_pair_stop_closes_both_legs(self):
        h1 = flat(NBARS)
        l1 = flat(NBARS)
        l1[1] = 96.0                       # -4.08% spread against a long-spread position
        h2 = flat(NBARS)
        pair = FakePair(flat(NBARS), h1, l1, flat(NBARS), flat(NBARS), h2, flat(NBARS),
                        flat(NBARS))
        cmi = [0.0] * NBARS
        cmi[0] = -0.4
        sig = mk_signal(cmi)
        m = run_cell(pair, sig)
        self.assertEqual(m["episodes"], 1)
        self.assertEqual(m["sl_hits"], 1)
        self.assertEqual(m["leg_stop_hits"], 0)
        self.assertEqual(m["layers"][0], 1)
        for k in (1, 2, 3, 4):
            self.assertEqual(m["layers"][k], 1, "level %d" % k)
        for k in (5, 6, 7, 8, 9, 10):
            self.assertEqual(m["layers"][k], 0, "level %d" % k)
        self.assertEqual(m["layers"][11], 0)
        self.assertEqual(m["fills"], 2 * (1 + 4 + 1))
        q1_0 = 1000.0 * LEV / 100.01
        q2_0 = 1000.0 * LEV / 99.99
        q1_tot = q1_0 + 4 * (1000.0 * LEV / 96.01)
        q2_tot = q2_0 + 4 * (1000.0 * LEV / 99.99)
        gross = (q1_tot * 96.01 - (q1_0 * 100.01 + 4 * 1000.0 * LEV)) \
            + (q2_tot * 99.99 - (q2_0 * 99.99 + 4 * 1000.0 * LEV))
        self.assertAlmostEqual(m["gross_pnl"], gross, places=6)
        fees = (q1_0 * 100.01 + q2_0 * 99.99) * FEE + 4 * (1000.0 * LEV * 2.0) * FEE \
            + (q1_tot * 96.01 + q2_tot * 99.99) * FEE
        self.assertAlmostEqual(m["fees"], fees, places=6)
        self.assertAlmostEqual(m["net_pnl"], gross - fees, places=6)
        self.assertTrue(se.pnl_decomposition_ok(m))

    def test_the_reserve_tranche_is_never_deployed(self):
        l1 = flat(NBARS)
        l1[1] = 10.0                       # an extreme adverse bar: every routine level fires
        pair = FakePair(flat(NBARS), flat(NBARS), l1, flat(NBARS))
        cmi = [0.0] * NBARS
        cmi[0] = -0.4
        sig = mk_signal(cmi)
        m = run_cell(pair, sig)
        self.assertEqual(m["layers"][10], 1)
        self.assertEqual(m["layers"][11], 0)
        self.assertEqual(m["fills"], 2 * (1 + 10 + 1))

    def test_a_leg_take_profit_closes_both_legs_at_its_own_level(self):
        h1 = flat(NBARS)
        h1[1] = 120.0                      # leg 1 (long) reaches 1% above its entry
        pair = FakePair(flat(NBARS), h1, flat(NBARS), flat(NBARS))
        cmi = [0.0] * NBARS
        cmi[0] = -0.4
        sig = mk_signal(cmi)
        m = run_cell(pair, sig)
        self.assertEqual(m["episodes"], 1)
        self.assertEqual(m["leg_tp_hits"], 1)
        self.assertEqual(m["tp_hits"], 0)
        q1 = 1000.0 * LEV / 100.01
        # the registered level plus the leg's own adverse slippage
        self.assertAlmostEqual(m["gross_pnl"], q1 * (100.01 * 1.01 + 0.01 - 100.01), places=6)
        self.assertTrue(se.pnl_decomposition_ok(m))

    def test_a_favourable_gap_is_never_credited_but_a_gap_through_a_stop_is_never_flattered(self):
        # gap through the pair stop: bar 2 opens 10% below the entry spread
        o1 = flat(NBARS)
        o1[2] = 90.0
        l1 = flat(NBARS)
        l1[2] = 90.0
        pair = FakePair(o1, flat(NBARS), l1, flat(NBARS))
        cmi = [0.0] * NBARS
        cmi[0] = -0.4
        cmi[1] = -0.4                       # held through bar 1 so the bar-2 gap is the trigger
        sig = mk_signal(cmi)
        m = run_cell(pair, sig)
        self.assertEqual(m["sl_hits"], 1)
        q1_0 = 1000.0 * LEV / 100.01
        adds = 10 * 1000.0 * LEV / 90.01
        gross = (q1_0 + adds) * 90.01 - (q1_0 * 100.01 + 10 * 1000.0 * LEV)
        self.assertAlmostEqual(m["gross_pnl"], gross, places=6)
        self.assertEqual(m["layers"][10], 1)
        self.assertEqual(m["layers"][11], 0)

    def test_max_holding_is_the_registered_outer_bound_subsumed_by_the_cycle_end(self):
        pair = FakePair(flat(NBARS), flat(NBARS), flat(NBARS), flat(NBARS))
        cmi = [0.0] * NBARS
        cmi[0] = -0.4
        cmi[1] = -0.4                      # stays extreme: no CMI reversion
        cmi[2:] = [0.6] * (NBARS - 2)      # never reverts below t_c
        sig = mk_signal(cmi)
        sig.entries[(0, se.T_O_GRID.index(CASE["t_o"]))] = (0, 1)
        m = run_cell(pair, sig)
        self.assertEqual(m["episodes"], 1)
        # a 336-bar window with a next-bar entry can never reach the 336-bar holding bound, so
        # the registered cycle-end flatten binds first - both counters are reported separately
        self.assertEqual(m["max_holding_exits"], 0)
        self.assertEqual(m["window_end_exits"], 1)
        self.assertEqual(m["bars_in_market"], NBARS - 2)

    def test_the_holding_bound_is_evaluated_and_can_fire_when_the_window_is_longer(self):
        # same registered code path, a test-local bound: the rule is live, not decorative
        n = NBARS
        pair = FakePair(flat(n), flat(n), flat(n), flat(n))
        cmi = [0.0] * n
        cmi[0] = -0.4
        sig = mk_signal(cmi)
        sig.trade = (0, n)
        pack = se.pack_cycle(pair, {"trading": (0, n)}, sig.beta)
        ds = se.day_setup_for(pair, (0, pair.n))
        series = [mk_funding(pair, 0, []), mk_funding(pair, 1, [])]
        orig = se.MAX_HOLDING_BARS
        try:
            se.MAX_HOLDING_BARS = 5
            m = se.simulate_cell(pair, [sig], [pack], CASE_TC0, RAIL, [0], {}, 1, "full", series,
                                 ds)
        finally:
            se.MAX_HOLDING_BARS = orig
        self.assertEqual(m["max_holding_exits"], 1)
        self.assertEqual(m["episodes"], 1)

    def test_cycle_end_flattens_an_open_episode(self):
        pair = FakePair(flat(NBARS), flat(NBARS), flat(NBARS), flat(NBARS))
        cmi = [0.6] * NBARS
        cmi[0] = -0.4
        cmi[1] = -0.4
        sig = mk_signal(cmi)
        sig.entries[(0, se.T_O_GRID.index(CASE["t_o"]))] = (0, 1)
        m = run_cell(pair, sig)
        self.assertEqual(m["window_end_exits"], 1)
        self.assertEqual(m["episodes"], 1)
        self.assertTrue(se.pnl_decomposition_ok(m))

    def test_non_tradable_cycle_holds_no_position(self):
        pair = FakePair(flat(NBARS), flat(NBARS), flat(NBARS), flat(NBARS))
        cmi = [0.0] * NBARS
        cmi[0] = -0.4
        sig = mk_signal(cmi, tradable=False)
        m = run_cell(pair, sig)
        self.assertEqual(m["episodes"], 0)
        self.assertEqual(m["cycles_seen"], 1)
        self.assertEqual(m["cycles_tradable"], 0)


class TestFundingExposure(unittest.TestCase):
    def _pair_and_sig(self, cmi=None):
        pair = FakePair(flat(NBARS), flat(NBARS), flat(NBARS), flat(NBARS))
        cmi = cmi or [0.0] * NBARS
        if cmi[0] == 0.0:
            cmi[0] = -0.4
        return pair, mk_signal(list(cmi))

    def test_a_settlement_inside_the_hold_is_charged_on_the_leg_notional(self):
        pair, sig = self._pair_and_sig()
        # a settlement at the open of bar 4 (leg 1 only), rate 0.0001; the hold runs to the
        # cycle end because CASE_TC0's reversion threshold is 0
        st = mk_funding(pair, 0, [(int(pair.open_time_ms[4]), 0.0001)])
        series = [st, mk_funding(pair, 1, [])]
        m = run_cell(pair, sig, CASE_TC0, series=series)
        self.assertEqual(m["window_end_exits"], 1)
        q1 = 1000.0 * LEV / 100.01
        self.assertAlmostEqual(m["funding"], q1 * 100.0 * 0.0001, places=6)
        self.assertTrue(se.pnl_decomposition_ok(m))

    def test_a_short_leg_receives_a_positive_rate(self):
        pair, sig = self._pair_and_sig()
        st = mk_funding(pair, 1, [(int(pair.open_time_ms[4]), 0.0001)])
        series = [mk_funding(pair, 0, []), st]
        m = run_cell(pair, sig, CASE_TC0, series=series)
        q2 = 1000.0 * LEV / 99.99
        self.assertAlmostEqual(m["funding"], -q2 * 100.0 * 0.0001, places=6)

    def test_funding_2x_doubles_and_no_funding_zeroes_it(self):
        pair, sig = self._pair_and_sig()
        st = mk_funding(pair, 0, [(int(pair.open_time_ms[4]), 0.0001)])
        series = [st, mk_funding(pair, 1, [])]
        base = run_cell(pair, sig, CASE_TC0, series=series)
        doub = run_cell(pair, sig, CASE_TC0, series=series,
                        stress={"funding_mult": 2.0}, kind="funding_2x")
        zero = run_cell(pair, sig, CASE_TC0, series=series,
                        stress={"no_funding": True}, kind="no_funding")
        self.assertAlmostEqual(doub["funding"], 2.0 * base["funding"], places=6)
        self.assertEqual(zero["funding"], 0.0)
        self.assertNotEqual(zero["net_pnl"], base["net_pnl"])

    def test_a_settlement_outside_the_hold_is_not_charged(self):
        pair, sig = self._pair_and_sig()
        cmi = [0.0] * NBARS
        cmi[0] = -0.4
        cmi[1] = 0.0
        sig = mk_signal(cmi)
        # the episode ends at bar 1; a settlement at bar 200 is far outside the hold
        st = mk_funding(pair, 0, [(int(pair.open_time_ms[200]), 0.0001)])
        series = [st, mk_funding(pair, 1, [])]
        m = run_cell(pair, sig, series=series)
        self.assertEqual(m["cmi_exits"], 1)
        self.assertEqual(m["funding"], 0.0)

    def test_a_settlement_at_the_exit_instant_is_charged(self):
        pair, sig = self._pair_and_sig()
        cmi = [0.0] * NBARS
        cmi[0] = -0.4
        cmi[1] = 0.0
        sig = mk_signal(cmi)
        st = mk_funding(pair, 0, [(int(pair.open_time_ms[1]), 0.0001)])
        series = [st, mk_funding(pair, 1, [])]
        m = run_cell(pair, sig, series=series)
        q1 = 1000.0 * LEV / 100.01
        self.assertAlmostEqual(m["funding"], q1 * 100.0 * 0.0001, places=6)

    def test_a_settlement_before_the_entry_is_not_charged(self):
        pair, sig = self._pair_and_sig()
        st = mk_funding(pair, 0, [(int(pair.open_time_ms[0]), 0.0001)])
        series = [st, mk_funding(pair, 1, [])]
        m = run_cell(pair, sig, CASE_TC0, series=series)
        self.assertEqual(m["funding"], 0.0)


class TestLayerHistogramGating(unittest.TestCase):
    def test_only_grid_cells_feed_the_histogram(self):
        se.LAYER_TOTALS.clear()
        pair = FakePair(flat(NBARS), flat(NBARS), flat(NBARS), flat(NBARS))
        cmi = [0.0] * NBARS
        cmi[0] = -0.4
        sig = mk_signal(cmi)
        run_cell(pair, sig, kind="full", count_layers=True)
        after_grid = se.LAYER_TOTALS["full"][0]
        run_cell(pair, sig, kind="full", diag=True, count_layers=False)
        self.assertEqual(se.LAYER_TOTALS["full"][0], after_grid)
        run_cell(pair, sig, kind="historical", count_layers=True)
        self.assertEqual(se.LAYER_TOTALS["full"][0], after_grid)
        se.LAYER_TOTALS.clear()


class TestMetricsAndDaySeries(unittest.TestCase):
    def test_daily_series_is_slice_scoped(self):
        pair = FakePair(flat(NBARS), flat(NBARS), flat(NBARS), flat(NBARS))
        cmi = [0.0] * NBARS
        cmi[0] = -0.4
        sig = mk_signal(cmi)
        m = run_cell(pair, sig)
        self.assertEqual(m["days"], len(np.unique(pair.day_id)))
        self.assertEqual(m["days"], 14)

    def test_a_flat_run_has_zero_sharpe_and_no_drawdown(self):
        pair = FakePair(flat(NBARS), flat(NBARS), flat(NBARS), flat(NBARS))
        sig = mk_signal([0.0] * NBARS)
        m = run_cell(pair, sig)
        self.assertEqual(m["sharpe"], 0.0)
        self.assertEqual(m["max_dd_usdt"], 0.0)
        self.assertEqual(m["ending_equity"], se.START_EQUITY)

    def test_pnl_decomposition_detects_a_broken_ledger(self):
        pair = FakePair(flat(NBARS), flat(NBARS), flat(NBARS), flat(NBARS))
        cmi = [0.0] * NBARS
        cmi[0] = -0.4
        sig = mk_signal(cmi)
        m = run_cell(pair, sig)
        self.assertTrue(se.pnl_decomposition_ok(m))
        broken = dict(m)
        broken["fees"] = m["fees"] + 1.0
        self.assertFalse(se.pnl_decomposition_ok(broken))


class TestSelectorAndGates(unittest.TestCase):
    SPEC = {"gates": {"min_episodes_is": 3, "min_episodes_oos": 2,
                      "neighborhood_min_same_sign_fraction": 0.6, "min_oos_sharpe": 0.40,
                      "min_oos_annualized_return": 0.0},
            "dca_domain": {"spacing_pct": [0.01, 0.02], "size_multiplier": [1.0],
                           "breakeven_tp_pct": [0.01], "invalidation_pct": [0.05]}}

    def _row(self, t_o, t_c, copula=0, spacing=0.01, net=10.0, sharpe=0.5, episodes=5,
             kind="historical", m=None):
        row = {"symbol": "A-B", "timeframe": "1h", "window_kind": kind, "t_o": t_o, "t_c": t_c,
               "copula_family": copula, "spacing_pct": spacing, "size_multiplier": 1.0,
               "breakeven_tp_pct": 0.01, "invalidation_pct": 0.05,
               "net_pnl": net, "sharpe": sharpe, "episodes": episodes, "ending_equity": 30000.0,
               "fees": 0.0, "funding": 0.0, "max_dd_usdt": 0.0, "max_dd_pct": 0.0,
               "max_effective_leverage": 1.0, "capital_utilization": 0.0,
               "annualized_return": m if m is not None else 0.1, "cagr": 0.1}
        for k in se.ROW_FIELDS:
            row.setdefault(k, 0)
        return row

    def test_selector_refuses_oos_rows(self):
        rows = [self._row(0.30, 0.10, kind="oos")]
        with self.assertRaises(ValueError):
            se.select_cohort_winner(rows, self.SPEC)

    def test_insufficient_trades_culls_the_cohort(self):
        rows = [self._row(0.30, 0.10, episodes=1)]
        w, reason = se.select_cohort_winner(rows, self.SPEC)
        self.assertIsNone(w)
        self.assertEqual(reason, "insufficient_trades")

    def test_no_qualifying_candidate_when_every_case_loses(self):
        rows = [self._row(0.30, 0.10, net=-1.0)]
        w, reason = se.select_cohort_winner(rows, self.SPEC)
        self.assertIsNone(w)
        self.assertEqual(reason, "no_qualifying_candidate")

    def test_tie_break_is_the_registered_axis_index_order(self):
        rows = [self._row(0.25, 0.10), self._row(0.20, 0.10), self._row(0.20, 0.05)]
        w, _ = se.select_cohort_winner(rows, self.SPEC)
        self.assertEqual((w["t_o"], w["t_c"]), (0.20, 0.05))

    def test_neighbourhood_uses_registered_steps_and_historical_rows_only(self):
        rows = [self._row(0.25, 0.10, net=5.0, sharpe=0.9), self._row(0.20, 0.10, net=5.0),
                self._row(0.30, 0.10, net=-5.0), self._row(0.20, 0.05, net=5.0)]
        w, _ = se.select_cohort_winner(rows, self.SPEC)
        self.assertEqual((w["t_o"], w["t_c"]), (0.25, 0.10))
        nb = se.cohort_neighbourhood(rows, w, self.SPEC)
        self.assertEqual(nb["neighbours"], 2)          # t_o -1 and t_o +1
        self.assertEqual(nb["agreeing"], 1)
        self.assertFalse(nb["passed"])
        with self.assertRaises(ValueError):
            se.cohort_neighbourhood([dict(r, window_kind="oos") for r in rows], w, self.SPEC)

    def _neighbourhood_rows(self):
        """The winner cell and its full registered face-neighbourhood, all positive."""
        return [self._row(0.30, 0.10, net=5.0, sharpe=0.9),
                self._row(0.25, 0.10, net=5.0), self._row(0.35, 0.10, net=5.0),
                self._row(0.30, 0.05, net=5.0), self._row(0.30, 0.15, net=5.0),
                self._row(0.30, 0.10, spacing=0.02, net=5.0)]

    def test_gate_reasons_are_the_registered_vocabulary(self):
        spec = dict(self.SPEC)
        spec["gates"] = dict(self.SPEC["gates"])
        hist = self._neighbourhood_rows()
        oos = [dict(self._row(0.30, 0.10, kind="oos"), net_pnl=1.0, sharpe=0.9,
                    annualized_return=0.2, episodes=5)]
        full = [dict(self._row(0.30, 0.10, kind="full"), net_pnl=1.0)]
        stress = {s: [dict(self._row(0.30, 0.10, kind=s), net_pnl=1.0)]
                  for s, _ in se.STRESS}
        stress["fee_2x"][0]["net_pnl"] = -1.0
        rows = {"historical": hist, "oos": oos, "full": full, "cost_attrition_40bps":
                [dict(self._row(0.30, 0.10, kind="cost_attrition_40bps"), net_pnl=1.0)],
                "no_funding": [self._row(0.30, 0.10, kind="no_funding")],
                "no_funding_full": [self._row(0.30, 0.10, kind="no_funding_full")],
                "source_cost_0p12pct": [self._row(0.30, 0.10, kind="source_cost_0p12pct")]}
        rows.update(stress)
        out = se.evaluate_cohort(spec, "A-B/1h", rows)
        self.assertEqual(out["outcome"], "CULLED")
        self.assertIn("robustness_economic:fee_2x", out["cull_reasons"])
        rows["fee_2x"][0]["net_pnl"] = 1.0
        out = se.evaluate_cohort(spec, "A-B/1h", rows)
        self.assertEqual(out["outcome"], "SURVIVOR")
        self.assertEqual(out["cull_reasons"], [])
        self.assertTrue(out["neighbourhood"]["passed"])

    def test_oos_sharpe_floor_and_episode_floor_are_enforced(self):
        spec = dict(self.SPEC)
        spec["gates"] = dict(self.SPEC["gates"])
        hist = self._neighbourhood_rows()
        full = [dict(self._row(0.30, 0.10, kind="full"), net_pnl=1.0)]
        base = {"historical": hist, "full": full,
                "cost_attrition_40bps": [dict(self._row(0.30, 0.10), net_pnl=1.0)],
                "no_funding": [self._row(0.30, 0.10)], "no_funding_full": [self._row(0.30, 0.10)],
                "source_cost_0p12pct": [self._row(0.30, 0.10)]}
        for s, _ in se.STRESS:
            base[s] = [dict(self._row(0.30, 0.10, kind=s), net_pnl=1.0)]
        base["oos"] = [dict(self._row(0.30, 0.10, kind="oos"), net_pnl=1.0, sharpe=0.20,
                            annualized_return=0.2, episodes=5)]
        out = se.evaluate_cohort(spec, "A-B/1h", base)
        self.assertIn("oos_economic", out["cull_reasons"])
        base["oos"] = [dict(self._row(0.30, 0.10, kind="oos"), net_pnl=1.0, sharpe=0.9,
                            annualized_return=0.2, episodes=1)]
        out = se.evaluate_cohort(spec, "A-B/1h", base)
        self.assertIn("insufficient_trades", out["cull_reasons"])


class TestDispositionAndFamilyItems(unittest.TestCase):
    def test_band_mapping(self):
        self.assertEqual(se.family_disposition([], True)["verdict_recommendation"], "REJECT")
        self.assertEqual(se.family_disposition([], False)["verdict_recommendation"],
                         "TECHNICAL_INCOMPLETE")
        one = se.family_disposition(["c1"], True)
        self.assertEqual(one["verdict_recommendation"], "PASS")
        self.assertEqual(one["disposition"], "SURVIVOR_FOUND")
        many = se.family_disposition(["c1", "c2"], True)
        self.assertEqual(many["verdict_recommendation"], "PASS")
        self.assertEqual(many["disposition"], "MULTIPLE_SURVIVORS")

    def test_source_consistency_hits_on_a_negative_winner(self):
        results = [{"cohort": "A-B/1h", "winner": {"t_o": 0.3}, "outcome": "CULLED",
                    "metrics": {"full": {"net_pnl": -5.0}}},
                   {"cohort": "C-D/1h", "winner": {"t_o": 0.3}, "outcome": "CULLED",
                    "metrics": {"full": {"net_pnl": -1.0}}}]
        out = se.source_consistency_check({}, results)
        self.assertTrue(out["evaluated"])
        self.assertTrue(out["hit"])
        self.assertTrue(out["forbids_pass"])

    def test_cost_boundary_hits_on_a_sign_disagreement(self):
        results = [{"cohort": "A-B/1h", "winner": {"t_o": 0.3}, "outcome": "SURVIVOR",
                    "metrics": {"full": {"net_pnl": 5.0},
                                "source_cost_reference": {"net_pnl": -1.0}}}]
        out = se.cost_boundary_check({}, results)
        self.assertTrue(out["hit"])

    def test_pair_concentration_uses_the_oos_share(self):
        results = [{"cohort": "A-B/1h", "outcome": "SURVIVOR", "winner": {"t_o": 0.3},
                    "metrics": {"oos": {"net_pnl": 90.0}}},
                   {"cohort": "C-D/1h", "outcome": "SURVIVOR", "winner": {"t_o": 0.3},
                    "metrics": {"oos": {"net_pnl": 10.0}}}]
        out = se.pair_concentration_check({}, results)
        self.assertTrue(out["hit"])
        self.assertAlmostEqual(out["top_share"], 0.9, places=6)


class TestEngineConstants(unittest.TestCase):
    def test_registered_pairs_are_the_six_pairs_of_the_four_symbols(self):
        self.assertEqual(se.PAIRS, (("BTCUSDT", "ETHUSDT"), ("BTCUSDT", "BNBUSDT"),
                                    ("BTCUSDT", "SOLUSDT"), ("ETHUSDT", "BNBUSDT"),
                                    ("ETHUSDT", "SOLUSDT"), ("BNBUSDT", "SOLUSDT")))
        syms = set()
        for a, b in se.PAIRS:
            syms.update((a, b))
        self.assertEqual(sorted(syms), ["BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"])

    def test_strategy_cases_are_15_aic_plus_5_substitutions(self):
        self.assertEqual(len(se.STRATEGY_CASES), 20)
        aic = [c for c in se.STRATEGY_CASES if c["copula_family"] == 0]
        subs = [c for c in se.STRATEGY_CASES if c["copula_family"] != 0]
        self.assertEqual(len(aic), 15)
        self.assertEqual(len(subs), 5)
        self.assertEqual((aic[0]["t_o"], aic[0]["t_c"]), (0.20, 0.05))
        self.assertEqual((aic[-1]["t_o"], aic[-1]["t_c"]), (0.40, 0.15))
        for c in subs:
            self.assertEqual((c["t_o"], c["t_c"]), se.SUBSTITUTION_REFERENCE)

    def test_registered_grid_kinds_and_thresholds(self):
        self.assertEqual(len(se.COHORT_GRID_KINDS), 11)
        self.assertIn("source_cost_0p12pct", se.COHORT_GRID_KINDS)
        self.assertEqual(se.FORMATION_BARS, 2160)
        self.assertEqual(se.TRADING_BARS, 336)
        self.assertEqual(se.SPREAD_PT, 0.02)
        self.assertEqual(se.SPREAD_SL, 0.04)
        self.assertEqual(se.MAX_HOLDING_BARS, 336)
        self.assertLess(se.ADF_CV_5PCT, 0.0)
        self.assertLess(se.KSS_CV_5PCT, 0.0)
        self.assertAlmostEqual(se.SOURCE_FEE_PER_FILL * 4.0, 0.0012, places=9)

    def test_case_name_and_case_tuple_round_trip(self):
        for c in se.STRATEGY_CASES:
            self.assertEqual(se.case_index(se.case_tuple(c)),
                             se.STRATEGY_CASES.index(c))
            self.assertTrue(se.case_name(se.case_tuple(c)))

    def test_cycle_bounds_are_anchored_and_disjoint(self):
        pair = FakePair(flat(se.FORMATION_BARS + 2 * se.TRADING_BARS + 5),
                        flat(se.FORMATION_BARS + 2 * se.TRADING_BARS + 5),
                        flat(se.FORMATION_BARS + 2 * se.TRADING_BARS + 5),
                        flat(se.FORMATION_BARS + 2 * se.TRADING_BARS + 5))
        cycles = pair.cycle_bounds()
        self.assertEqual(len(cycles), 2)
        self.assertEqual(cycles[0]["formation"], (0, se.FORMATION_BARS))
        self.assertEqual(cycles[0]["trading"][1], cycles[1]["trading"][0])


class TestV101DetectorFixes(unittest.TestCase):
    """Regressions for the two detector defects the registered u1 attempt exposed.

    Card t_57ecd99e.  u1's own assertions flagged both classes: `cmi_within_registered_range`
    (2 raw conditional-CDF excursions, measured up to 2.812e-07 over 1.0 on ETHUSDT-BNBUSDT cycle
    70) and the three cost/funding no-op detectors, which read `median` over all registered cells
    and therefore saturate at exactly 0.0 whenever most cells never trade.
    """

    def test_cmi_series_clips_the_closed_form_tail_excursion(self):
        # the defect class, measured: frank's `expm1(-theta) + e1*e2` cancellation.  With
        # theta = 40, u1 = 0.995, u2 = 0.97 the unclipped primitive returns ~1.4e+283.
        u1, u2 = np.array([0.995]), np.array([0.97])
        raw = float(se.copula_cond_cdf("frank", {"theta": 40.0}, u1, u2)[0])
        self.assertGreater(raw, 1.0 + 1e-9)
        cmi_series = getattr(se, "cmi_series", None)
        self.assertIsNotNone(cmi_series, "engine exposes no cmi_series (the v1.0.1 clip)")
        cmi = float(cmi_series("frank", {"theta": 40.0}, u1, u2)[0])
        self.assertLessEqual(abs(cmi), 0.5 + 1e-12)
        # ... and the excursion stays disclosed instead of being silently swallowed
        self.assertGreaterEqual(se.CMI_RAW_EXCURSION["hits"], 1)
        self.assertGreater(se.CMI_RAW_EXCURSION["max_abs_overshoot"], 0.0)
        self.assertGreaterEqual(
            se.COUNTERS.get("signal", {}).get("cmi_raw_float_excursion", 0), 1)
        self.assertEqual(se.COUNTERS.get("signal", {}).get("cmi_out_of_range", 0), 0)

    def test_cmi_series_is_bit_identical_away_from_the_tail(self):
        for fam, th in TestCopulaMath.PARAMS:
            u1 = np.array([0.2, 0.5, 0.8, 0.95])
            u2 = np.array([0.3, 0.7, 0.55, 0.4])
            raw = se.copula_cond_cdf(fam, th, u1, u2) - 0.5
            self.assertTrue(np.array_equal(se.cmi_series(fam, th, u1, u2), raw), fam)

    def test_track_effectiveness_reads_the_aggregate_not_the_median(self):
        flat = {"episodes": 0, "fees": 0.0, "funding": 0.0, "net_pnl": 0.0}

        def grid(fee, fund, net):
            return [dict(flat)] * 7 + [{"episodes": 1, "fees": fee, "funding": fund,
                                        "net_pnl": net}] * 3

        rows = {"full": grid(10.0, 1.0, 5.0), "fee_2x": grid(20.0, 1.0, -5.0),
                "funding_2x": grid(10.0, 2.0, 2.0), "source_cost_0p12pct": grid(6.0, 1.0, 9.0)}
        med = lambda rs, k: sorted(r[k] for r in rs)[len(rs) // 2]      # noqa: E731
        # control (non-vacuous): the OLD median formulation is a no-op on this sparse grid
        self.assertEqual(med(rows["full"], "net_pnl"), 0.0)
        self.assertFalse(med(rows["fee_2x"], "fees") > med(rows["full"], "fees")
                         and med(rows["fee_2x"], "net_pnl") < med(rows["full"], "net_pnl"))
        got = se.track_effectiveness(rows)
        self.assertTrue(got["cost_pressure_effective"])
        self.assertTrue(got["funding_pressure_effective"])
        self.assertTrue(got["source_cost_effective"])

    def test_track_effectiveness_fails_closed_when_nothing_traded(self):
        zero = [{"episodes": 0, "fees": 0.0, "funding": 0.0, "net_pnl": 0.0}] * 4
        got = se.track_effectiveness({"full": zero, "fee_2x": list(zero),
                                      "funding_2x": list(zero),
                                      "source_cost_0p12pct": list(zero)})
        self.assertEqual(got, {"cost_pressure_effective": False,
                               "funding_pressure_effective": False,
                               "source_cost_effective": False})


if __name__ == "__main__":
    unittest.main(verbosity=1)
