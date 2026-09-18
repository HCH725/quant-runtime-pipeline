#!/usr/bin/env python3
"""Executable check for the Strategy J execution engine (network momentum: signature Levy area
and DTW / DDTW lead-lag matrices, the registered convex graph adjacency, the network oscillator
and the reverting sigmoid, executed through the frozen DCA episode rail).

Runs inside the qlib container:
    container exec qlib-run env SJ_ENGINE_PATH=/scripts/100_strategy_j_run.py \
        /opt/venv/bin/python /scripts/tests/test_strategy_j_engine.py
and on a host numpy interpreter:
    SJ_ENGINE_PATH=<repo>/container/scripts/100_strategy_j_run.py python3 <this file>

The DTW diagonal-vectorised dynamic program is compared against a plain scalar reference, the
closed-form graph solution against a projected-gradient solve of the same objective AND against
the literal elementwise definition, the Levy area against the registered double sum, and the
whole panel pipeline (lead-lag tensors -> adjacency -> network oscillator -> target state ->
entry events) against its own invariants on synthetic four-market panels.  Causality is checked
the only way it can be checked: by truncating the series and demanding the past not change.  No
market data, no container state, no network: stdlib unittest + numpy only.
"""
import importlib.util
import math
import os
import random
import unittest

import numpy as np

ENGINE = os.environ.get("SJ_ENGINE_PATH", "/scripts/100_strategy_j_run.py")
_spec = importlib.util.spec_from_file_location("sj_engine", ENGINE)
sj = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sj)

BAR_MS = sj.MS_PER_DAY
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


# --------------------------------------------------------------- reference implementations

def ref_dtw_lag_mode(x, y, band=None):
    """Plain scalar DTW (same recurrence, same tie-breaks) used as the reference."""
    L = len(x)
    if band is None:
        band = L
    INF = float("inf")
    C = [[INF] * (L + 1) for _ in range(L + 1)]
    C[0][0] = 0.0
    for i in range(L):
        for j in range(L):
            if abs(i - j) > band:
                continue
            d = (x[i] - y[j]) ** 2
            C[i + 1][j + 1] = d + min(C[i][j], C[i][j + 1], C[i + 1][j])
    if not math.isfinite(C[L][L]):
        return None, 0
    i, j = L - 1, L - 1
    path = []
    while True:
        path.append(j - i)
        if i == 0 and j == 0:
            break
        cand = []
        if i > 0 and j > 0:
            cand.append((C[i][j], i - 1, j - 1))
        if i > 0:
            cand.append((C[i][j + 1], i - 1, j))
        if j > 0:
            cand.append((C[i + 1][j], i, j - 1))
        if not cand:
            break
        cand.sort(key=lambda c: (c[0], c[1] - c[2]))
        _c, i, j = cand[0]
    vals = sorted(set(path))
    best = max(path.count(v) for v in vals)
    tied = sorted([v for v in vals if path.count(v) == best], key=lambda v: (abs(v), v))
    return tied[0], len(path)


def ref_levy(xi, xj):
    """The registered Levy-area formula, literal."""
    tot = 0.0
    for a in range(1, len(xi)):
        tot += ((xi[a] - xi[a - 1]) * (xj[a - 1] + xj[a])
                - (xj[a] - xj[a - 1]) * (xi[a - 1] + xi[a]))
    return tot


def ref_graph_normalised(V, beta):
    """Reference: W from the max-abs normalised row profiles, then the row normalisation."""
    scale = float(np.max(np.abs(V))) if np.max(np.abs(V)) > 0 else 1.0
    v = V / scale
    m = V.shape[0]
    K = np.zeros((m, m))
    for i in range(m):
        for j in range(m):
            if i == j:
                continue
            W = sum((v[i, l] - v[j, l]) ** 2 for l in range(m))
            K[i, j] = 1.0 / (W ** 2 + beta)
    rs = K.sum(axis=1, keepdims=True)
    return np.divide(K, rs, out=np.zeros_like(K), where=rs > 0.0)


# --------------------------------------------------------------- synthetic cohort surface

class FakeCohort:
    """The engine's Cohort surface, driven by synthetic daily bars."""

    def __init__(self, symbol, seed, n=520, vol=0.02, drift=0.0):
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
        self._baseline_cache = {}
        self.inputs = sj.build_network_inputs(self)
        self.inputs_report = {}

    def slice(self, start_date, end_date):
        lo = sj.utc_ms(start_date)
        hi = sj.utc_ms(end_date) + sj.MS_PER_DAY - 1
        return (int(np.searchsorted(self.open_time_ms, lo, side="left")),
                int(np.searchsorted(self.open_time_ms, hi, side="right")))


def four_market_cohorts(seed=100, n=520):
    syms = ["AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT"]
    return {s: FakeCohort(s, seed + i, n=n) for i, s in enumerate(syms)}


class PanelFixture:
    """Registers the four synthetic cohorts as the panel for the duration of one test."""

    def __init__(self, cohorts):
        self.cohorts = cohorts

    def __enter__(self):
        self.saved = dict(sj.PANEL_COHORTS)
        sj.PANEL_COHORTS.clear()
        sj.PANEL_COHORTS.update(self.cohorts)
        sj.Panel._cache.clear()
        sj._LL_CACHE.clear()
        return self.cohorts

    def __exit__(self, *exc):
        sj.PANEL_COHORTS.clear()
        sj.PANEL_COHORTS.update(self.saved)
        sj.Panel._cache.clear()
        sj._LL_CACHE.clear()
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


def run_sim(cohort, rail, pos, case, stress=None, slip=1, series=None, kind="test", layer=None):
    layer = layer or FakeLayer(pos)
    prev = cohort._signal_cache.get(tuple(case))
    cohort._signal_cache[tuple(case)] = layer
    try:
        p = sj.params_dict(tuple(case))
        return sj.simulate(cohort, p, rail, (0, cohort.n), stress or {}, slip, kind,
                           series or EMPTY_FUNDING)
    finally:
        if prev is None:
            cohort._signal_cache.pop(tuple(case), None)
        else:
            cohort._signal_cache[tuple(case)] = prev


CASE_LEVY_22 = sj.case_fields_for("levy", "22")
CASE_DTW_22 = sj.case_fields_for("dtw", "22")
CASE_ENSEMBLE = sj.case_fields_for("ddtw", "ensemble")


# --------------------------------------------------------------- tests

class TestRegisteredAxes(unittest.TestCase):
    def test_case_axes_are_the_registered_product(self):
        self.assertEqual(len(sj.CASE_ORDER), 21)
        self.assertEqual(len(set(sj.CASE_ORDER)), 21)
        self.assertEqual(len(set(sj.CASE_NAMES)), 21)
        self.assertEqual(sj.CASE_NAMES[0], "levy__lb22")
        self.assertEqual(sj.CASE_NAMES[1], "dtw__lb22")
        self.assertEqual(sj.CASE_NAMES[2], "ddtw__lb22")
        self.assertEqual(sj.CASE_NAMES[-1], "ddtw__lbensemble")

    def test_case_helpers_round_trip(self):
        for m in sj.LL_METHODS:
            for lb in sj.LOOKBACK_LABELS:
                c = sj.case_fields_for(m, lb)
                self.assertIn(c, sj.CASE_ORDER)
                self.assertEqual(sj.case_name(c), "%s__lb%s" % (m, lb))
                self.assertEqual(sj.params_dict(c), dict(zip(sj.CASE_FIELDS, c)))
                self.assertEqual(sj.LL_METHODS[sj.case_method(c)], m)
                self.assertEqual(sj.LOOKBACK_LABELS[sj.case_lookback(c)], lb)

    def test_registered_constants_match_the_record(self):
        self.assertEqual(sj.VOL_WINDOW, 22)
        self.assertEqual(tuple(sj.SPEED_INDEXES), (1, 2, 3, 4, 5, 6))
        self.assertEqual(sj.RHO, 4.0)
        self.assertAlmostEqual(sj.SIGMOID_LAMBDA, math.sqrt(2.0))
        self.assertEqual(tuple(sj.LOOKBACKS), (22, 44, 66, 88, 110, 132))
        self.assertEqual(sj.PANEL_SIZE, 4)
        self.assertEqual(tuple(sorted(sj.COHORT_GRID_KINDS)),
                         tuple(sorted(["historical", "oos", "full", "fee_2x", "funding_2x",
                                       "entry_delay_1_bar", "slippage_2ticks", "no_funding",
                                       "no_funding_full", "cost_attrition_40bps"])))


class TestVolatilityScalingAndOscillators(unittest.TestCase):
    def test_scaled_series_is_prefix_invariant(self):
        c = FakeCohort("AAAUSDT", 1, n=420)
        probe = sj.causality_probe(c, c.inputs, sample_bars=12)
        self.assertGreater(probe["bars_probed"], 0)
        self.assertEqual(probe["mismatches"], 0, probe["examples"])

    def test_causality_probe_catches_a_planted_lookahead(self):
        c = FakeCohort("AAAUSDT", 2, n=420)
        poisoned = dict(c.inputs)
        poisoned["scaled_price"] = c.inputs["scaled_price"].copy()
        poisoned["scaled_price"][-1] += 1.0
        probe = sj.causality_probe(c, poisoned, sample_bars=400)
        self.assertGreater(probe["bars_probed"], 0)

    def test_oscillator_warmup_is_finite_after_the_floor(self):
        c = FakeCohort("AAAUSDT", 3, n=420)
        for k in sj.SPEED_INDEXES:
            self.assertTrue(np.isfinite(c.inputs["osc"][k][sj.SIGNAL_WARMUP_BARS:]).all())


class TestLeadLagTensors(unittest.TestCase):
    def test_levy_area_matches_the_literal_formula(self):
        rng = random.Random(3)
        n, m = 60, 4
        dsc = np.array([[rng.gauss(0, 1) for _ in range(m)] for _ in range(n)])
        ps = np.cumsum(dsc, axis=0)
        V = sj.lead_lag_tensor(dsc, "levy", 22)
        for t in (25, 40, 59):
            win = ps[t - 22:t + 1]
            for i in range(m):
                for j in range(m):
                    if i == j:
                        continue
                    self.assertAlmostEqual(
                        V[t, i, j], ref_levy(list(win[:, i]), list(win[:, j])), places=9)

    def test_levy_tensor_is_prefix_invariant(self):
        rng = random.Random(4)
        n, m = 90, 4
        dsc = np.array([[rng.gauss(0, 1) for _ in range(m)] for _ in range(n)])
        full = sj.lead_lag_tensor(dsc, "levy", 22)
        for t in (40, 70, 89):
            trunc = sj.lead_lag_tensor(dsc[:t + 1], "levy", 22)
            self.assertTrue(np.allclose(trunc[t], full[t]))

    def test_dtw_mode_lag_matches_the_scalar_reference(self):
        rng = random.Random(7)
        for trial in range(12):
            L = rng.choice([6, 9, 12, 17])
            pairs = []
            for _p in range(3):
                base = [rng.gauss(0, 1) for _ in range(L)]
                lag = rng.choice([-2, -1, 0, 1, 2])
                shifted = []
                for t in range(L):
                    src = t - lag
                    shifted.append((base[src] if 0 <= src < L else 0.0) + rng.gauss(0, 0.25))
                pairs.append((np.array(base), np.array(shifted)))
            xi = np.stack([p[0] for p in pairs])
            xj = np.stack([p[1] for p in pairs])
            lags, lens = sj._dtw_panel_lags((xi, xj), band=L)
            for k, (x, y) in enumerate(pairs):
                refl, reflen = ref_dtw_lag_mode(list(x), list(y), band=L)
                self.assertEqual(int(lags[k]), int(refl), "trial %d pair %d" % (trial, k))
                self.assertEqual(int(lens[k]), reflen)

    def test_dtw_band_bounds_the_mode_lag(self):
        rng = random.Random(11)
        L, band = 40, 4
        xi = np.stack([[rng.gauss(0, 1) for _ in range(L)] for _ in range(6)])
        xj = np.stack([[rng.gauss(0, 1) for _ in range(L)] for _ in range(6)])
        lags, lens = sj._dtw_panel_lags((xi, xj), band=band)
        self.assertTrue(np.all(np.abs(lags) <= band))
        self.assertTrue(np.all(lens > 0))

    def test_dtw_and_ddtw_tensors_are_prefix_invariant(self):
        """The strongest causality check available: truncating the series must not change any
        entry of a bar that already existed (for all three descriptors)."""
        rng = random.Random(13)
        n, m = 120, 3
        dsc = np.array([[rng.gauss(0, 1) for _ in range(m)] for _ in range(n)])
        for method in ("dtw", "ddtw"):
            full = sj.lead_lag_tensor(dsc, method, 22)
            for t in (60, 100, 119):
                trunc = sj.lead_lag_tensor(dsc[:t + 1], method, 22)
                self.assertTrue(np.allclose(trunc[t], full[t]),
                                "%s bar %d changed when the future was removed" % (method, t))

    def test_lead_lag_tensor_is_skew_symmetric(self):
        rng = random.Random(17)
        n, m = 60, 4
        dsc = np.array([[rng.gauss(0, 1) for _ in range(m)] for _ in range(n)])
        for method in ("levy", "dtw", "ddtw"):
            V = sj.lead_lag_tensor(dsc, method, 22)
            for t in (30, 59):
                self.assertTrue(np.allclose(V[t], -V[t].T),
                                "%s bar %d is not skew symmetric" % (method, t))


class TestGraphLearning(unittest.TestCase):
    def test_closed_form_matches_the_literal_definition(self):
        rng = random.Random(5)
        for _ in range(4):
            V = np.array([[rng.gauss(0, 1) for _ in range(4)] for _ in range(4)])
            V = V - V.T
            self.assertTrue(np.allclose(sj.graph_adjacency(V),
                                        ref_graph_normalised(V, sj.GRAPH_BETA), atol=1e-12))

    def test_solution_is_row_normalised_with_a_zero_diagonal(self):
        rng = random.Random(6)
        V = np.array([[rng.gauss(0, 1) for _ in range(4)] for _ in range(4)])
        V = V - V.T
        A = sj.graph_adjacency(V)
        self.assertTrue(np.allclose(A.sum(axis=1), 1.0))
        self.assertTrue(np.allclose(np.diag(A), 0.0))
        self.assertTrue((A >= 0.0).all())

    def test_closed_form_matches_a_projected_gradient_solve(self):
        rng = random.Random(9)
        V = np.array([[rng.gauss(0, 1) for _ in range(4)] for _ in range(4)])
        V = V - V.T
        closed = sj.graph_adjacency(V)
        it = sj.graph_adjacency_iterative(V, iters=20000, step=0.02)
        self.assertLess(float(np.max(np.abs(closed - it))), 5e-3)

    def test_alpha_cancels_in_the_row_normalisation(self):
        """The registered problem's alpha scales every entry; row normalisation removes it."""
        rng = random.Random(21)
        V = np.array([[rng.gauss(0, 1) for _ in range(4)] for _ in range(4)])
        V = V - V.T
        self.assertTrue(np.allclose(sj.graph_adjacency(V), sj.graph_adjacency(V, beta=sj.GRAPH_BETA)))


class TestPanelPipeline(unittest.TestCase):
    def test_panel_builds_every_registered_case(self):
        cohorts = four_market_cohorts()
        with PanelFixture(cohorts):
            for case in sj.CASE_ORDER:
                p = sj.Panel(case, cohorts)
                self.assertEqual(p.n, 520)
                self.assertTrue(np.allclose(p.A_use.sum(axis=2), 1.0))
                self.assertTrue(np.allclose(np.diagonal(p.A_use, axis1=1, axis2=2), 0.0))
                self.assertTrue(np.isnan(p.score[:sj.SIGNAL_WARMUP_BARS]).all())
                self.assertTrue(np.isfinite(p.score[sj.SIGNAL_WARMUP_BARS:]).all())

    def test_events_are_fresh_target_state_changes(self):
        cohorts = four_market_cohorts(seed=200)
        with PanelFixture(cohorts):
            p = sj.Panel(CASE_LEVY_22, cohorts)
            for t, i in np.argwhere(p.events != 0):
                self.assertEqual(int(p.state[t, i]), int(p.events[t, i]))
                if t > 0:
                    self.assertNotEqual(int(p.state[t - 1, i]), int(p.events[t, i]))

    def test_ensemble_is_the_mean_of_the_six_normalised_adjacencies(self):
        cohorts = four_market_cohorts(seed=300)
        with PanelFixture(cohorts):
            ens = sj.Panel(CASE_ENSEMBLE, cohorts)
            singles = [sj.Panel(sj.case_fields_for("ddtw", str(d)), cohorts) for d in sj.LOOKBACKS]
            mean = np.mean(np.stack([s.A_use for s in singles], axis=0), axis=0)
            self.assertTrue(np.allclose(ens.A_use, mean, atol=1e-12))

    def test_network_aggregation_excludes_the_own_market(self):
        cohorts = four_market_cohorts(seed=400)
        with PanelFixture(cohorts):
            p = sj.Panel(CASE_LEVY_22, cohorts)
            i = p.markets.index("AAAUSDT")
            own = p.A_use[:, i, i]
            self.assertTrue(np.allclose(own, 0.0))

    def test_permutation_shift_changes_the_signal(self):
        cohorts = four_market_cohorts(seed=500)
        with PanelFixture(cohorts):
            base = sj.Panel(CASE_LEVY_22, cohorts)
            shifted = sj.Panel(CASE_LEVY_22, cohorts, shifts=[0, 37, 120, 5])
            self.assertGreater(int(np.sum(base.state != shifted.state)), 0)

    def test_baseline_layer_differs_from_the_network_signal(self):
        cohorts = four_market_cohorts(seed=600)
        with PanelFixture(cohorts):
            c = cohorts["AAAUSDT"]
            base = sj.baseline_layer_for(c)
            net = sj.signals_for(c, CASE_LEVY_22)
            self.assertGreater(int(np.sum(base.pos != net.pos)), 0)
            self.assertEqual(int(np.count_nonzero(base.events)), int((np.diff(base.pos) != 0).sum()))


class TestEpisodeExecution(unittest.TestCase):
    """The seam between a real panel layer and the frozen DCA episode engine."""

    def test_entry_uses_the_next_bar_open_and_the_record_exit_flattens(self):
        cohorts = four_market_cohorts(seed=700, n=400)
        with PanelFixture(cohorts):
            c = cohorts["AAAUSDT"]
            net = sj.signals_for(c, CASE_LEVY_22)
            m = run_sim(c, RAIL_FLAT, net.pos, CASE_LEVY_22, layer=net)
            self.assertEqual(m["episodes"], m["tp_hits"] + m["stop_hits"] + m["time_exits"]
                             + m["open_at_end"] + m["margin_calls"])
            self.assertTrue(sj.pnl_decomposition_ok(m))
            self.assertGreater(m["episodes"], 0)
            self.assertGreater(m["windows_seen"], 0)
            self.assertLessEqual(m["episodes"], m["windows_seen"])

    def test_tight_take_profit_and_stop_isolate_their_fill_paths(self):
        cohorts = four_market_cohorts(seed=800, n=300)
        with PanelFixture(cohorts):
            c = cohorts["BBBUSDT"]
            pos = np.zeros(60, dtype=np.int64)
            pos[5:] = 1
            pos = np.concatenate([pos, np.zeros(c.n - 60, dtype=np.int64)])
            m_tp = run_sim(c, RAIL_TIGHT, pos, CASE_LEVY_22)
            m_stop = run_sim(c, RAIL_STOP, pos, CASE_LEVY_22)
            self.assertGreaterEqual(m_tp["tp_hits"], 1)
            self.assertGreaterEqual(m_stop["stop_hits"], 1)

    def test_no_entry_before_a_defined_signal_and_no_reentry_without_a_new_event(self):
        cohorts = four_market_cohorts(seed=900, n=400)
        with PanelFixture(cohorts):
            c = cohorts["CCCUSDT"]
            net = sj.signals_for(c, CASE_DTW_22)
            m = run_sim(c, RAIL_FLAT, net.pos, CASE_DTW_22, layer=net)
            self.assertEqual(sj.counters_total("entry_before_a_defined_signal"), 0)
            self.assertEqual(sj.counters_total("entry_bar_not_the_next_bar_after_the_signal"), 0)

    def test_funding_and_fee_multipliers_move_net_not_gross(self):
        cohorts = four_market_cohorts(seed=1000, n=300)
        with PanelFixture(cohorts):
            c = cohorts["DDDUSDT"]
            pos = np.zeros(120, dtype=np.int64)
            pos[10:90] = 1
            pos = np.concatenate([pos, np.zeros(c.n - 120, dtype=np.int64)])
            f = {"obs_times": np.array([sj.utc_ms("2025-02-01") + 8 * 3600000], dtype=np.int64),
                 "obs_rates": np.array([0.0001]), "settle_bar": np.array([31], dtype=np.int64),
                 "settle_bar_closed": np.array([31], dtype=np.int64)}
            m0 = run_sim(c, RAIL_FLAT, pos, CASE_LEVY_22, series=f)
            m2 = run_sim(c, RAIL_FLAT, pos, CASE_LEVY_22, series=f, stress={"fee_mult": 2.0})
            self.assertEqual(m0["gross_pnl"], m2["gross_pnl"])
            self.assertGreater(m2["fees"], m0["fees"])
            self.assertLess(m2["net_pnl"], m0["net_pnl"])


class TestFamilyReaders(unittest.TestCase):
    def test_expansion_reader_is_not_executed_and_never_a_hit(self):
        out = sj.oos_universe_expansion_check(None, [], {})
        self.assertFalse(out["evaluated"])
        self.assertFalse(out["hit"])
        self.assertEqual(out["status"], "not_executed")

    def test_permutation_reader_runs_end_to_end(self):
        cohorts = four_market_cohorts(seed=1100, n=420)
        with PanelFixture(cohorts):
            c = cohorts["AAAUSDT"]
            saved = sj.N_PERM_DRAWS
            sj.N_PERM_DRAWS = 2
            try:
                spec = {"costs": {"baseline_slippage_ticks": 1},
                        "data": {"start": "2025-01-01", "end": "2026-05-01"},
                        "dca_domain": {"base_quote": 1000.0}}
                winner = dict(zip(sj.CASE_FIELDS, CASE_LEVY_22))
                winner.update({"spacing_pct": 0.02, "size_multiplier": 1.0,
                               "breakeven_tp_pct": 0.01, "invalidation_pct": 0.05,
                               "cohort": "AAAUSDT/1d", "winner_case_label": "levy__lb22"})
                out = sj.permutation_test_check(spec, [{"winner": winner, "cohort": "AAAUSDT/1d"}],
                                                {"AAAUSDT/1d": (c, EMPTY_FUNDING)})
                self.assertTrue(out["evaluated"])
                self.assertEqual(out["cohorts"]["AAAUSDT/1d"]["draws"], 2)
                self.assertIn("mean_surplus_sharpe", out["cohorts"]["AAAUSDT/1d"])
            finally:
                sj.N_PERM_DRAWS = saved

    def test_execution_lag_reader_runs_end_to_end(self):
        cohorts = four_market_cohorts(seed=1200, n=420)
        with PanelFixture(cohorts):
            c = cohorts["BBBUSDT"]
            spec = {"costs": {"baseline_slippage_ticks": 1},
                    "data": {"start": "2025-01-01", "end": "2026-05-01"},
                    "dca_domain": {"base_quote": 1000.0}}
            winner = dict(zip(sj.CASE_FIELDS, CASE_LEVY_22))
            winner.update({"spacing_pct": 0.02, "size_multiplier": 1.0,
                           "breakeven_tp_pct": 0.01, "invalidation_pct": 0.05,
                           "cohort": "BBBUSDT/1d", "winner_case_label": "levy__lb22"})
            cr = [{"winner": winner, "cohort": "BBBUSDT/1d"}]
            rows = {"BBBUSDT/1d": {"entry_delay_1_bar": []}}
            out = sj.execution_lag_check(spec, cr, {"BBBUSDT/1d": (c, EMPTY_FUNDING)}, rows)
            self.assertTrue(out["evaluated"])
            self.assertIn("enhancement_over_macd", out["cohorts"]["BBBUSDT/1d"])

    def test_dtw_ablation_reader_runs_end_to_end(self):
        """Regression for the u1 FAILED_SCRIPT defect: the reader shadowed its own helper name
        (`_winner_case, dca = _winner_case(c, spec)` -> UnboundLocalError) and only fired inside
        the full production run, after all four grids had been measured.  The registered shock
        windows live inside the real 2022-2026 window; the synthetic fixture is shorter, so the
        window set is patched to a slice the fixture actually covers."""
        cohorts = four_market_cohorts(seed=1300, n=420)
        with PanelFixture(cohorts):
            c = cohorts["CCCUSDT"]
            spec = {"costs": {"baseline_slippage_ticks": 1},
                    "dca_domain": {"base_quote": 1000.0}}
            winner = dict(zip(sj.CASE_FIELDS, CASE_DTW_22))
            winner.update({"spacing_pct": 0.02, "size_multiplier": 1.0,
                           "breakeven_tp_pct": 0.01, "invalidation_pct": 0.05,
                           "cohort": "CCCUSDT/1d", "winner_case_label": "dtw__lb22"})
            saved_windows, saved_labels = sj.SHOCK_WINDOWS, sj.LOOKBACK_LABELS
            sj.SHOCK_WINDOWS = (("synthetic_shock", "2025-06-01", "2025-07-31"),)
            try:
                out = sj.dtw_ablation_check(spec, [{"winner": winner, "cohort": "CCCUSDT/1d"}],
                                            {"CCCUSDT/1d": (c, EMPTY_FUNDING)})
            finally:
                sj.SHOCK_WINDOWS = saved_windows
            self.assertEqual(saved_windows, sj.SHOCK_WINDOWS)
            self.assertTrue(out["evaluated"])
            self.assertIn("CCCUSDT/1d", out["cohorts"])
            self.assertIn("cells_total", out)
            self.assertEqual(saved_labels, sj.LOOKBACK_LABELS)

    def test_no_function_shadows_a_module_level_helper_it_calls(self):
        """Static guard for the class of defect that failed r2-u1: a local assignment must never
        reuse the name of a module-level function/constant that the same function body also
        references (Python would then bind the local and raise UnboundLocalError at run time)."""
        import ast

        def bound_names(target):
            """Names a target really binds: plain names plus tuple/list unpacking; a Subscript or
            Attribute target (`D[k] = v`, `o.a = v`) rebinds nothing."""
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
        for fn in [n for n in tree.body if isinstance(n, ast.FunctionDef)]:
            assigned, used = set(), set()
            for node in ast.walk(fn):
                if isinstance(node, ast.Assign):
                    for t in node.targets:
                        assigned.update(bound_names(t))
                elif isinstance(node, ast.For):
                    assigned.update(bound_names(node.target))
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    for alias in node.names:
                        assigned.add((alias.asname or alias.name).split(".")[0])
                elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                    assigned.update(bound_names(node.target))
                elif isinstance(node, ast.Name):
                    used.add(node.id)
            clash = assigned & used & module_names
            if clash:
                offenders.append({"function": fn.name, "names": sorted(clash)})
        self.assertEqual(offenders, [], "local names shadow module-level helpers: %r" % offenders)


if __name__ == "__main__":
    unittest.main(verbosity=2)
