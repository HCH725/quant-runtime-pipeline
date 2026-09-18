#!/usr/bin/env python3
"""Executable check for the Strategy K execution engine (Compact-RIEnet: the five-parameter
hyperbolic lag transformation, the spectral cleaning of the sample correlation matrix of the
transformed window, the marginal-volatility rescaling and the exact long-only global-minimum-
variance allocation, executed through the frozen DCA episode rail).

Runs inside the qlib container:
    container exec qlib-run env SK_ENGINE_PATH=/scripts/110_strategy_k_run.py \
        /opt/venv/bin/python /scripts/tests/test_strategy_k_engine.py
and on a host numpy interpreter:
    SK_ENGINE_PATH=<repo>/container/scripts/110_strategy_k_run.py python3 <this file>

The lag transformation is compared against a literal loop reference, each cleaning arm against its
own definition, the long-only allocation against a projected-gradient solve of the same quadratic
program AND against the unconstrained analytic solution when that happens to be interior, and the
whole panel pipeline (window -> transform -> correlation -> cleaned spectrum -> assembly ->
allocation -> target state -> entry events) against its own invariants on synthetic four-market
panels.  Causality is checked the only way it can be checked: by truncating the series and
demanding the past not change.  No market data, no container state, no network: stdlib unittest +
numpy only.
"""
import importlib.util
import os
import random
import unittest

import numpy as np

ENGINE = os.environ.get("SK_ENGINE_PATH", "/scripts/110_strategy_k_run.py")
_spec = importlib.util.spec_from_file_location("sk_engine", ENGINE)
sk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sk)

BAR_MS = sk.MS_PER_DAY
BASE_MS = 1735689600000             # 2025-01-01T00:00:00Z
LEV = 10.0
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
DCA_ONE = {"spacing_pct": 0.02, "size_multiplier": 1.0, "breakeven_tp_pct": 0.01,
           "invalidation_pct": 0.05}
CASE_RAW_250 = sk.CASE_ORDER[sk.CASE_NAMES.index("raw__lb250")]
CASE_MP_250 = sk.CASE_ORDER[sk.CASE_NAMES.index("mp__lb250")]
CASE_SHRINK_250 = sk.CASE_ORDER[sk.CASE_NAMES.index("shrink__lb250")]


# --------------------------------------------------------------- reference implementations

def ref_lag_transform(window, lb):
    """The registered transformation written as a literal per-lag double loop."""
    out = np.zeros_like(window, dtype=np.float64)
    th1, th2, th3, th4, th5 = sk.THETA
    for row, lag in enumerate(range(lb, 0, -1)):
        alpha = th1 * lag ** (-th2)
        beta = th3 - th4 * np.exp(-th5 * lag)
        for i in range(window.shape[1]):
            out[row, i] = (alpha / beta) * np.tanh(beta * window[row, i])
    return out


def ref_long_only_gmv(Sigma, steps=400000, step0=0.05):
    """Projected-gradient reference for min w'Sw s.t. w >= 0, sum w = 1 (independent of the
    active-set enumeration the engine uses)."""
    n = Sigma.shape[0]
    w = np.full(n, 1.0 / n)
    best = w.copy()
    best_val = float(w @ Sigma @ w)
    step = step0
    for it in range(steps):
        grad = 2.0 * (Sigma @ w)
        w = w - step * grad
        w = np.maximum(w, 0.0)
        s = w.sum()
        if s <= 0.0:
            w = np.full(n, 1.0 / n)
        else:
            w = w / s
        val = float(w @ Sigma @ w)
        if val < best_val - 1e-15:
            best_val, best = val, w.copy()
        if it % 20000 == 19999:
            step *= 0.5
    return best, best_val


# --------------------------------------------------------------- synthetic cohort surface

class FakeCohort:
    """The engine's Cohort surface, driven by synthetic daily bars."""

    def __init__(self, symbol, seed, n=560, vol=0.02, drift=0.0):
        rng = random.Random(seed)
        close = [100.0]
        for _ in range(n - 1):
            close.append(close[-1] * (1.0 + drift + rng.gauss(0, vol)))
        c = np.array(close, dtype=np.float64)
        self.symbol = symbol
        self.timeframe = "1d"
        self.close = c
        self.open = np.concatenate([[c[0]], c[:-1]])
        self.high = np.maximum(self.open, c) * 1.001
        self.low = np.minimum(self.open, c) * 0.999
        self.volume = np.full(n, 10.0)
        self.open_time_ms = BASE_MS + np.arange(n, dtype=np.int64) * BAR_MS
        self.n = n
        self.bar_ms = BAR_MS
        self.non_bar_steps = 0
        self.price_increment = 0.1
        self.taker_fee = 0.0005
        self.leverage = LEV
        self.margin_maint = 0.10
        self.spec = None
        self._signal_cache = {}
        self.inputs = sk.build_network_inputs(self)
        self.inputs_report = {}

    def slice(self, start_date, end_date):
        lo = sk.utc_ms(start_date)
        hi = sk.utc_ms(end_date) + sk.MS_PER_DAY - 1
        return (int(np.searchsorted(self.open_time_ms, lo, side="left")),
                int(np.searchsorted(self.open_time_ms, hi, side="right")))


def four_market_cohorts(seed=100, n=560):
    syms = ["AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT"]
    return {s: FakeCohort(s, seed + i, n=n) for i, s in enumerate(syms)}


class PanelFixture:
    """Registers the four synthetic cohorts as the panel for the duration of one test."""

    def __init__(self, cohorts):
        self.cohorts = cohorts

    def __enter__(self):
        self.saved = dict(sk.PANEL_COHORTS)
        sk.PANEL_COHORTS.clear()
        sk.PANEL_COHORTS.update(self.cohorts)
        sk.Panel._cache.clear()
        return self.cohorts

    def __exit__(self, *exc):
        sk.PANEL_COHORTS.clear()
        sk.PANEL_COHORTS.update(self.saved)
        sk.Panel._cache.clear()
        return False


class FakeLayer:
    """A hand-built signal layer: the target-state path plus its fresh entry events."""

    def __init__(self, pos, yhat=None, label="fake"):
        self.pos = np.array(pos, dtype=np.int64)
        self.events = np.zeros(len(pos), dtype=np.int64)
        prev = 0
        for t, v in enumerate(self.pos):
            if v != prev and v != 0:
                self.events[t] = int(v)
            prev = int(v)
        self.yhat = (np.array(yhat, dtype=np.float64) if yhat is not None
                     else np.full(len(pos), 0.01, dtype=np.float64))
        self.case_label = label
        self.diag = {"fake": True}


def params_of(case, dca=None):
    p = dict(zip(sk.CASE_FIELDS, case))
    p.update(dca or DCA_ONE)
    p["base_quote"] = 1000.0
    return p


def run_sim(cohort, rail, pos, case, stress=None, slip=1, series=None, kind="test", layer=None):
    layer = layer or FakeLayer(pos)
    prev = cohort._signal_cache.get(tuple(case))
    cohort._signal_cache[tuple(case)] = layer
    try:
        return sk.simulate(cohort, params_of(case), rail, (0, cohort.n), stress or {}, slip, kind,
                           series or EMPTY_FUNDING)
    finally:
        if prev is None:
            cohort._signal_cache.pop(tuple(case), None)
        else:
            cohort._signal_cache[tuple(case)] = prev


def make_row(case, dca, kind, net, sharpe, episodes, margin_calls=0):
    r = {"symbol": "AAAUSDT", "timeframe": "1d", "window_kind": kind}
    r.update(dict(zip(sk.CASE_FIELDS, case)))
    r.update(dca)
    r.update({"net_pnl": net, "sharpe": sharpe, "episodes": episodes,
              "margin_calls": margin_calls})
    return r


def fake_spec():
    return {"gates": {"min_episodes_is": 10, "min_episodes_oos": 4},
            "parameter_domain": {"grid_cases": [dict(zip(sk.CASE_FIELDS, c))
                                                for c in sk.CASE_ORDER]},
            "dca_domain": {"spacing_pct": [0.02], "size_multiplier": [1.0],
                           "breakeven_tp_pct": [0.01], "invalidation_pct": [0.05],
                           "base_quote": 1000.0}}


# --------------------------------------------------------------- tests

class TestRegisteredAxes(unittest.TestCase):
    def test_case_axes_are_the_registered_product(self):
        self.assertEqual(len(sk.CASE_ORDER), 12)
        self.assertEqual(len(set(sk.CASE_ORDER)), 12)
        self.assertEqual(len(sk.CASE_NAMES), 12)
        self.assertEqual(len(sk.CASE_FIELDS), 7)
        self.assertEqual(sk.CLEAN_ARMS, ("raw", "shrink_const", "mp_clip"))
        self.assertEqual(sk.LOOKBACKS, (250, 500, 750, 1200))
        for case in sk.CASE_ORDER:
            self.assertEqual(len(case), len(sk.CASE_FIELDS))
            self.assertEqual(sum(case[:3]), 1)
            self.assertEqual(sum(case[3:]), 1)

    def test_case_helpers_round_trip(self):
        for case in sk.CASE_ORDER:
            name = sk.case_name(case)
            arm = sk.CLEAN_ARMS[sk.case_clean(case)]
            lb = sk.LOOKBACKS[sk.case_lookback(case)]
            self.assertEqual(name, "%s__lb%d" % (sk.CLEAN_LABELS[sk.case_clean(case)], lb))
            self.assertIn(name.split("__")[0], sk.CLEAN_LABELS)
            self.assertEqual(sk.case_index(case), sk.CASE_ORDER.index(case))
            self.assertEqual(sk.case_tuple(dict(zip(sk.CASE_FIELDS, case))), tuple(case))
            self.assertIn(arm, sk.CLEAN_ARMS)

    def test_registered_constants_match_the_record(self):
        self.assertEqual(len(sk.THETA), 5)
        self.assertTrue(all(t > 0.0 for t in sk.THETA))
        self.assertTrue(0.0 < sk.SHRINK_DELTA < 1.0)
        self.assertEqual(sk.PANEL_SIZE, 4)
        self.assertAlmostEqual(sk.EQUAL_WEIGHT, 0.25)
        self.assertEqual(sk.SIGNAL_WARMUP_BARS, 1200)
        self.assertEqual(set(sk.COUNTER_NAMES), set(sk.COUNTER_NAMES))
        self.assertIn("allocation_weight_sum_violation", sk.COUNTER_NAMES)
        self.assertIn("state_not_derived_from_the_registered_rule", sk.COUNTER_NAMES)


class TestKernel(unittest.TestCase):
    def test_lag_transform_matches_the_literal_reference(self):
        rng = np.random.default_rng(7)
        for lb in (5, 40, 250):
            w = rng.normal(0.0, 0.03, size=(lb, 4))
            self.assertTrue(np.allclose(sk.lag_transform(w, lb), ref_lag_transform(w, lb),
                                        atol=1e-15))

    def test_cleaning_arms_match_their_definitions(self):
        lam = np.array([3.4, 0.9, 0.5, 0.2])
        raw = sk.clean_eigenvalues_q(lam, "raw", 250)
        self.assertTrue(np.allclose(raw, lam))
        shr = sk.clean_eigenvalues_q(lam, "shrink_const", 250)
        self.assertTrue(np.allclose(shr, (1.0 - sk.SHRINK_DELTA) * lam
                                    + sk.SHRINK_DELTA * lam.mean()))
        self.assertTrue(np.all(shr > 0.0))
        self.assertLess(shr.std(), lam.std())
        mp = sk.clean_eigenvalues_q(lam, "mp_clip", 250)
        edge = (1.0 + np.sqrt(4.0 / 250.0)) ** 2
        self.assertTrue(np.all(mp >= lam - 1e-15))
        self.assertTrue(np.allclose(mp[lam > edge], lam[lam > edge]))
        floor = float(np.mean(lam[lam <= edge]))
        self.assertTrue(np.allclose(mp[lam <= edge], np.maximum(lam[lam <= edge], floor)))
        self.assertTrue(np.allclose(mp[lam > edge], lam[lam > edge]))

    def test_long_only_gmv_is_feasible_and_minimal(self):
        rng = np.random.default_rng(11)
        for _ in range(12):
            A = rng.normal(0.0, 1.0, size=(4, 4))
            Sigma = A @ A.T + np.eye(4) * 0.3
            w, feasible = sk.long_only_gmv(Sigma)
            self.assertGreaterEqual(feasible, 1)
            self.assertAlmostEqual(float(w.sum()), 1.0, places=12)
            self.assertTrue(np.all(w >= 0.0))
            val = float(w @ Sigma @ w)
            for mask in range(1, 16):                       # every other feasible support
                idx = [i for i in range(4) if (mask >> i) & 1]
                sub = Sigma[np.ix_(idx, idx)]
                ones = np.ones(len(idx))
                z = np.linalg.solve(sub, ones)
                ws = z / float(ones @ z)
                if np.any(ws <= 0.0):
                    continue
                full = np.zeros(4)
                for j, i in enumerate(idx):
                    full[i] = ws[j]
                self.assertLessEqual(val, float(full @ Sigma @ full) + 1e-12)

    def test_long_only_gmv_matches_a_projected_gradient_solve(self):
        rng = np.random.default_rng(13)
        A = rng.normal(0.0, 1.0, size=(4, 4))
        Sigma = A @ A.T + np.eye(4) * 0.1
        w, _ = sk.long_only_gmv(Sigma)
        wr, val = ref_long_only_gmv(Sigma)
        self.assertLessEqual(float(w @ Sigma @ w), val + 1e-9)

    def test_long_only_gmv_equals_the_unconstrained_solution_when_interior(self):
        Sigma = np.array([[1.0, 0.05, 0.02, 0.01],
                          [0.05, 1.1, 0.03, 0.02],
                          [0.02, 0.03, 1.2, 0.04],
                          [0.01, 0.02, 0.04, 1.3]])
        inv = np.linalg.inv(Sigma)
        ones = np.ones(4)
        w_unc = inv @ ones / float(ones @ inv @ ones)
        self.assertTrue(np.all(w_unc > 0.0))
        w, _ = sk.long_only_gmv(Sigma)
        self.assertTrue(np.allclose(w, w_unc, atol=1e-10))

    def test_panel_paths_are_deterministic(self):
        rng = np.random.default_rng(17)
        ret = rng.normal(0.0, 0.02, size=(320, 4))
        a = sk.panel_paths(ret, 250, "raw")
        b = sk.panel_paths(ret, 250, "raw")
        self.assertTrue(np.array_equal(a["weights"], b["weights"]))
        self.assertTrue(np.array_equal(a["states"], b["states"]))
        self.assertTrue(np.array_equal(a["events"], b["events"]))

    def test_state_is_the_registered_tilt_rule_and_events_are_fresh(self):
        rng = np.random.default_rng(19)
        ret = rng.normal(0.0, 0.02, size=(340, 4))
        for arm in sk.CLEAN_ARMS:
            p = sk.panel_paths(ret, 250, arm)
            defined = p["weights"].sum(axis=1) > 0.0
            self.assertTrue(defined[:250].sum() == 0)
            rule = np.where(p["weights"] >= sk.EQUAL_WEIGHT - sk.LAG_TOL, 1, 0)
            self.assertTrue(np.array_equal(p["states"], rule))
            for t, i in np.argwhere(p["events"] != 0):
                self.assertEqual(int(p["states"][t, i]), int(p["events"][t, i]))
                self.assertNotEqual(int(p["states"][t - 1, i]), int(p["events"][t, i]))
            for t in np.flatnonzero(defined):
                self.assertAlmostEqual(float(p["weights"][t].sum()), 1.0, places=12)
                self.assertTrue(np.all(p["weights"][t] >= 0.0))

    def test_low_volatility_asset_takes_the_largest_weight(self):
        rng = np.random.default_rng(23)
        common = rng.normal(0.0, 0.03, size=400)
        ret = np.zeros((400, 4))
        for i in range(4):
            ret[:, i] = common + rng.normal(0.0, 0.004 * (i + 1), size=400)
        ret[:, 0] = ret[:, 0] * 0.4        # asset 0 carries the same shock at 40% scale
        p = sk.panel_paths(ret, 250, "raw")
        t = 399
        self.assertLess(abs(ret[:, 0].std() - 0.4 * common.std()), 0.01)
        self.assertEqual(int(np.argmax(p["weights"][t])), 0)

    def test_weights_state_of_window_equals_the_panel_path(self):
        cohorts = four_market_cohorts(seed=29, n=340)
        with PanelFixture(cohorts):
            panel = sk.Panel(CASE_RAW_250, cohorts)
            for t in (250, 300, 339):
                w, s = sk._weights_state_of_window(panel.R[:t + 1, :], panel.lookback, panel.arm)
                self.assertTrue(np.allclose(w, panel.weights[t], atol=1e-12))
                self.assertTrue(np.array_equal(s, panel.state[t]))


class TestPanelPipeline(unittest.TestCase):
    def test_panel_builds_every_registered_case(self):
        cohorts = four_market_cohorts(seed=31, n=1260)
        with PanelFixture(cohorts):
            for case in sk.CASE_ORDER:
                p = sk.Panel(case, cohorts)
                self.assertEqual(p.n, 1260)
                self.assertEqual(p.markets, ["AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT"])
                defined = p.weights.sum(axis=1) > 0.0
                self.assertEqual(int(defined.sum()), 1260 - p.lookback)
                self.assertTrue(np.all(p.state[:p.lookback] == 0))
                self.assertTrue(np.allclose(p.weights[defined].sum(axis=1), 1.0, atol=1e-12))

    def test_causality_probe_passes_on_a_clean_panel(self):
        cohorts = four_market_cohorts(seed=37, n=1300)
        with PanelFixture(cohorts):
            sk.panel_for(SK_PROBE_CASE)
            c = cohorts["AAAUSDT"]
            out = sk.causality_probe(c, c.inputs, sample_bars=8)
            self.assertGreater(out["bars_probed"], 0)
            self.assertEqual(out["mismatches"], 0)

    def test_causality_probe_catches_a_planted_lookahead(self):
        cohorts = four_market_cohorts(seed=41, n=1300)
        with PanelFixture(cohorts):
            panel = sk.panel_for(SK_PROBE_CASE)
            t = panel.lookback
            saved = float(panel.weights[t, 0])
            panel.weights[t, 0] = saved * 0.5 + 0.5      # cannot be a feasible allocation
            try:
                c = cohorts["AAAUSDT"]
                out = sk.causality_probe(c, c.inputs, sample_bars=8)
                self.assertGreaterEqual(out["mismatches"], 1)
            finally:
                panel.weights[t, 0] = saved

    def test_panel_diagnostics_are_consistent(self):
        cohorts = four_market_cohorts(seed=43, n=600)
        with PanelFixture(cohorts):
            p = sk.Panel(CASE_MP_250, cohorts)
            d = p.report()
            self.assertEqual(d["cleaning_arm"], "mp_clip")
            self.assertEqual(d["lookback"], 250)
            self.assertEqual(d["degenerate_bars"], 0)
            self.assertAlmostEqual(d["max_weight_sum"], 1.0, places=9)
            self.assertLessEqual(d["max_weight"], 1.0)
            ev = sum(v["events"] for v in d["market_state_bars"].values())
            self.assertEqual(ev, int(np.count_nonzero(p.events)))


class TestEpisodeExecution(unittest.TestCase):
    """The seam between a real panel layer and the frozen DCA episode engine."""

    def test_entry_uses_the_next_bar_open_and_the_record_exit_flattens(self):
        cohorts = four_market_cohorts(seed=700, n=400)
        with PanelFixture(cohorts):
            c = cohorts["AAAUSDT"]
            net = sk.signals_for(c, CASE_RAW_250)
            m = run_sim(c, RAIL_FLAT, net.pos, CASE_RAW_250, layer=net)
            self.assertEqual(m["episodes"], m["tp_hits"] + m["stop_hits"] + m["time_exits"]
                             + m["open_at_end"] + m["margin_calls"])
            self.assertTrue(sk.pnl_decomposition_ok(m))
            self.assertGreater(m["windows_seen"], 0)
            self.assertLessEqual(m["episodes"], m["windows_seen"])

    def test_tight_take_profit_and_stop_isolate_their_fill_paths(self):
        cohorts = four_market_cohorts(seed=800, n=300)
        with PanelFixture(cohorts):
            c = cohorts["BBBUSDT"]
            pos = np.zeros(60, dtype=np.int64)
            pos[5:] = 1
            pos = np.concatenate([pos, np.zeros(c.n - 60, dtype=np.int64)])
            m_tp = run_sim(c, RAIL_TIGHT, pos, CASE_RAW_250)
            m_stop = run_sim(c, RAIL_STOP, pos, CASE_RAW_250)
            self.assertGreaterEqual(m_tp["tp_hits"], 1)
            self.assertGreaterEqual(m_stop["stop_hits"], 1)

    def test_no_entry_before_a_defined_signal_and_no_reentry_without_a_new_event(self):
        cohorts = four_market_cohorts(seed=900, n=400)
        with PanelFixture(cohorts):
            c = cohorts["CCCUSDT"]
            net = sk.signals_for(c, CASE_SHRINK_250)
            run_sim(c, RAIL_FLAT, net.pos, CASE_SHRINK_250, layer=net)
            self.assertEqual(sk.counters_total("entry_before_a_defined_signal"), 0)
            self.assertEqual(sk.counters_total("entry_bar_not_the_next_bar_after_the_signal"), 0)

    def test_funding_and_fee_multipliers_move_net_not_gross(self):
        cohorts = four_market_cohorts(seed=1000, n=300)
        with PanelFixture(cohorts):
            c = cohorts["DDDUSDT"]
            pos = np.zeros(120, dtype=np.int64)
            pos[10:90] = 1
            pos = np.concatenate([pos, np.zeros(c.n - 120, dtype=np.int64)])
            f = {"obs_times": np.array([sk.utc_ms("2025-02-01") + 8 * 3600000], dtype=np.int64),
                 "obs_rates": np.array([0.0001]), "settle_bar": np.array([31], dtype=np.int64),
                 "settle_bar_closed": np.array([31], dtype=np.int64)}
            m0 = run_sim(c, RAIL_FLAT, pos, CASE_RAW_250, series=f)
            m2 = run_sim(c, RAIL_FLAT, pos, CASE_RAW_250, series=f, stress={"fee_mult": 2.0})
            self.assertEqual(m0["gross_pnl"], m2["gross_pnl"])
            self.assertGreater(m2["fees"], m0["fees"])
            self.assertLess(m2["net_pnl"], m0["net_pnl"])


class TestFamilyReaders(unittest.TestCase):
    def setUp(self):
        sk._RAW_SURFACES.clear()

    def test_impact_transferability_and_tick_readers_are_not_executed(self):
        for fn in (sk.market_impact_haircut_reader, sk.cross_asset_transferability_reader,
                   sk.intraday_tick_margin_audit_reader):
            out = fn(None, [], {})
            self.assertFalse(out["evaluated"])
            self.assertFalse(out["hit"])
            self.assertEqual(out["status"], "not_executed")
            self.assertIn("measured_available", out)

    def test_ablation_reader_runs_end_to_end(self):
        spec = fake_spec()
        hist = []
        full = []
        for case in sk.CASE_ORDER:
            arm = sk.CLEAN_ARMS[sk.case_clean(case)]
            sharpe = {"raw": 0.5, "shrink_const": 0.9, "mp_clip": 0.7}[arm]
            net = {"raw": 100.0, "shrink_const": 300.0, "mp_clip": 200.0}[arm]
            hist.append(make_row(case, DCA_ONE, "historical", net, sharpe, 20))
            full.append(make_row(case, DCA_ONE, "full", net * 2.0, sharpe, 30))
        rows = {"AAAUSDT/1d": {"historical": hist, "full": full}}
        out = sk.bigru_spectral_ablation_reader(spec, [{"cohort": "AAAUSDT/1d"}], {}, rows)
        self.assertTrue(out["evaluated"])
        self.assertEqual(out["cells"], 1)
        self.assertFalse(out["hit"])          # shrink beats raw -> no trigger
        self.assertFalse(out["cohorts"]["AAAUSDT/1d"]["identity_at_least_as_good"])
        self.assertEqual(out["cohorts"]["AAAUSDT/1d"]["arms"]["shrink_const"]["case_name"],
                         "shrink__lb250")

    def test_ablation_reader_triggers_on_a_majority(self):
        spec = fake_spec()
        rows = {}
        for label in ("AAAUSDT/1d", "BBBUSDT/1d", "CCCUSDT/1d"):
            hist, full = [], []
            for case in sk.CASE_ORDER:
                arm = sk.CLEAN_ARMS[sk.case_clean(case)]
                net = {"raw": 500.0, "shrink_const": 100.0, "mp_clip": 50.0}[arm]
                hist.append(make_row(case, DCA_ONE, "historical", net, 0.9, 20))
                full.append(make_row(case, DCA_ONE, "full", net, 0.9, 30))
            rows[label] = {"historical": hist, "full": full}
        cr = [{"cohort": k} for k in rows]
        out = sk.bigru_spectral_ablation_reader(spec, cr, {}, rows)
        self.assertTrue(out["hit"])
        self.assertEqual(out["cells"], 3)
        self.assertEqual(out["cells_identity_at_least_as_good"], 3)

    def test_freeze_reader_runs_and_flags_the_registered_margin_leg(self):
        spec = fake_spec()
        winner = dict(zip(sk.CASE_FIELDS, CASE_RAW_250))
        winner.update(DCA_ONE)
        rows = {}
        for label, calls in (("AAAUSDT/1d", 0), ("BBBUSDT/1d", 2)):
            rows[label] = {k: [make_row(CASE_RAW_250, DCA_ONE, k, 10.0, 1.0, 12,
                                        margin_calls=calls)]
                           for k in ("historical", "oos", "full", "fee_2x", "funding_2x",
                                     "entry_delay_1_bar", "slippage_2ticks", "no_funding_full",
                                     "cost_attrition_40bps")}
        cr = [{"cohort": label, "winner": winner, "metrics": {"oos": {"sharpe": 1.0}}}
              for label in rows]
        out = sk.freeze_rule_reader(spec, cr, {}, rows)
        self.assertTrue(out["evaluated"])
        self.assertTrue(out["hit"])
        self.assertEqual(out["cohorts_triggering"], ["BBBUSDT/1d"])
        self.assertFalse(out["cohorts"]["AAAUSDT/1d"]["freeze_margin_leg"])

    def test_no_function_shadows_a_module_level_helper_it_calls(self):
        """Static guard: a local assignment must never reuse the name of a module-level
        function/constant that the same function body also references."""
        import ast

        def bound_names(target):
            if isinstance(target, ast.Name):
                return [target.id]
            if isinstance(target, (ast.Tuple, ast.List)):
                out = []
                for el in target.elts:
                    out.extend(bound_names(el))
                return out
            if isinstance(target, ast.Starred):
                return bound_names(target.value)
            return []

        with open(ENGINE) as fh:
            tree = ast.parse(fh.read())
        module_names = {n.name for n in tree.body
                        if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
        module_names |= {t.id for n in tree.body if isinstance(n, ast.Assign)
                         for t in n.targets if isinstance(t, ast.Name)}
        offenders = []
        for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
            locals_bound = set()
            for node in ast.walk(fn):
                if isinstance(node, ast.Assign):
                    for t in node.targets:
                        locals_bound |= set(bound_names(t))
                elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                    if node.target is not None:
                        locals_bound |= set(bound_names(node.target))
                elif isinstance(node, (ast.For, ast.comprehension)):
                    if isinstance(node.target, ast.Name):
                        locals_bound.add(node.target.id)
            used = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)
                    and isinstance(n.ctx, ast.Load)}
            bad = (locals_bound & used) & module_names
            bad.discard(fn.name)
            if bad:
                offenders.append((fn.name, sorted(bad)))
        self.assertEqual(offenders, [])


    def test_every_referenced_module_helper_is_defined(self):
        """Static guard for the r1-u1 failure class: a function whose body loads a name that
        neither the function nor the module defines dies with NameError only on the production
        path (the execution engine's `main()` was never exercised by the unit tests)."""
        import ast
        import builtins

        with open(ENGINE) as fh:
            tree = ast.parse(fh.read())
        module_names = set(dir(builtins))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                module_names.add(node.name)
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        module_names.add(t.id)
            elif isinstance(node, ast.Import):
                for a in node.names:
                    module_names.add((a.asname or a.name).split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                for a in node.names:
                    module_names.add(a.asname or a.name)
        unresolved = []
        for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
            local, loads = set(), set()
            for node in ast.walk(fn):
                if isinstance(node, ast.Name):
                    (local if isinstance(node.ctx, ast.Store) else loads).add(node.id)
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    for a in node.names:
                        local.add((a.asname or a.name).split(".")[0])
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    if node is not fn:
                        local.add(node.name)
                elif isinstance(node, ast.arg):
                    local.add(node.arg)
                elif isinstance(node, ast.ExceptHandler) and node.name:
                    local.add(node.name)
            missing = sorted(n for n in (loads - local - module_names)
                             if n not in ("self", "cls", "__file__"))
            if missing:
                unresolved.append((fn.name, missing))
        self.assertEqual(unresolved, [])


SK_PROBE_CASE = sk.CASE_ORDER[sk.CASE_NAMES.index("raw__lb%d" % sk.SIGNAL_WARMUP_BARS)]

if __name__ == "__main__":
    unittest.main(verbosity=2)
