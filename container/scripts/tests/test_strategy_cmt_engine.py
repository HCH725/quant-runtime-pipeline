#!/usr/bin/env python3
"""Executable check for the Strategy CMT execution engine (Continuous Macro Timing: the
expanding-z direction-normalised macro composite score, the softplus-gated stress / crowding
interactions, the tanh target tilt and the registered EWMA growth weight, executed through the
frozen DCA episode rail).

Runs inside the qlib container:
    container exec qlib-run env CMT_ENGINE_PATH=/scripts/140_continuous_macro_timing_run.py \\
        /opt/venv/bin/python /scripts/tests/test_strategy_cmt_engine.py
and on a host numpy interpreter:
    CMT_ENGINE_PATH=<repo>/container/scripts/140_continuous_macro_timing_run.py \\
        <numpy venv>/bin/python <this file>

Every registered primitive is compared against a literal reference implementation (the
expanding z-score against an explicit mean/var loop, the rolling percentile against a
`sum(... for u in window)` count, the drawdown and the relative momentum against explicit loops,
the EWMA against its own recursion, the whole score chain against a literal re-write of the
record's formulae), the target state against the registered long-only weight rule, and the panel
against its invariants on a synthetic four-market panel.  Causality is checked the only way it
can be checked: by truncating the source history and demanding the PAST not change.  The placebo
walks are checked against their own drift/scale definition.  No market data, no container state,
no network: stdlib unittest + numpy only.
"""
import importlib.util
import math
import os
import re
import unittest

import numpy as np

ENGINE = os.environ.get("CMT_ENGINE_PATH", "/scripts/140_continuous_macro_timing_run.py")
_spec = importlib.util.spec_from_file_location("cmt_engine", ENGINE)
cmt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cmt)

BAR_MS = cmt.MS_PER_DAY
BASE_MS = 1735689600000             # 2025-01-01T00:00:00Z
N_BARS = 1400
LABELS = ["BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
RAIL_FLAT = {"base_quote": 1000.0, "spacing_d0": 0.5, "tp": 0.5, "invalidation": 0.9,
             "size_multiplier": 1.0}
EMPTY_FUNDING = {"obs_times": np.array([], dtype=np.int64),
                 "obs_rates": np.array([], dtype=np.float64),
                 "settle_bar": np.array([], dtype=np.int64),
                 "settle_bar_closed": np.array([], dtype=np.int64)}
DCA = {"spacing_pct": 0.5, "size_multiplier": 1.0, "breakeven_tp_pct": 0.5,
       "invalidation_pct": 0.9, "base_quote": 1000.0}


# ---------------------------------------------------------------------------------------------
# literal reference implementations (deliberately naive: no shared code with the engine)
# ---------------------------------------------------------------------------------------------
def ref_softplus(x, tau=1.0):
    return tau * math.log1p(math.exp(x / tau))


def ref_expanding_z(x):
    out = np.full(len(x), np.nan)
    obs = []
    for i, v in enumerate(x):
        v = float(v)
        if not np.isfinite(v):
            continue
        obs.append(v)
        if len(obs) < cmt.Z_MIN_OBS:
            continue
        m = sum(obs) / len(obs)
        var = sum((o - m) ** 2 for o in obs) / (len(obs) - 1)
        out[i] = (v - m) / math.sqrt(var) if var > 0 else np.nan
    return out


def ref_percentile(x, window):
    out = np.full(len(x), np.nan)
    seen = []
    for i, v in enumerate(x):
        v = float(v)
        if not np.isfinite(v):
            continue
        seen.append(v)
        if len(seen) < window:
            continue
        w = seen[-window:]
        out[i] = sum(1 for u in w if u <= v) / float(window)
    return out


def ref_drawdown(x):
    out = np.full(len(x), np.nan)
    peak = None
    for i, v in enumerate(x):
        v = float(v)
        if not np.isfinite(v):
            continue
        peak = v if peak is None else max(peak, v)
        out[i] = v / peak - 1.0
    return out


def ref_momentum(g, d, window):
    out = np.full(len(g), np.nan)
    for i in range(window, len(g)):
        if all(np.isfinite([g[i], g[i - window], d[i], d[i - window]])):
            out[i] = (g[i] / g[i - window]) / (d[i] / d[i - window]) - 1.0
    return out


def ref_ewma(target, eta):
    out = np.full(len(target), np.nan)
    prev = cmt.W_G_INIT
    started = False
    for i, tv in enumerate(target):
        if not np.isfinite(tv):
            continue
        if not started:
            prev = cmt.W_G_INIT
            started = True
        prev = (1.0 - eta) * prev + eta * float(tv)
        out[i] = prev
    return out


def ref_score_chain(feats, variant, smoothing, alpha=None, lambda_s=None, tau_w=None, eta=None):
    """The record's composite score written out again from the documented formulae."""
    alpha = cmt.ALPHA_RATE if alpha is None else alpha
    lambda_s = cmt.LAMBDA_S if lambda_s is None else lambda_s
    tau_w = cmt.TAU_W if tau_w is None else tau_w
    eta = cmt.ETA_SMOOTH if eta is None else eta
    core = alpha * feats["rate"] + (1.0 - alpha) * feats["ddepth"]
    if variant == "full":
        raw = core + lambda_s * feats["stress"] - cmt.LAMBDA_C * feats["crowded"]
    elif variant == "core_only":
        raw = core
    else:
        raw = feats["rate"]
    score = ref_expanding_z(raw)
    target = 0.5 + cmt.MAX_TILT * np.tanh(score / tau_w)
    weight = target if smoothing == "none" else ref_ewma(target, eta)
    return {"score": score, "target": target, "weight": weight, "raw": raw}


# ---------------------------------------------------------------------------------------------
# synthetic market / bundle fixtures
# ---------------------------------------------------------------------------------------------
def synth_bundle(n=N_BARS, seed=11):
    dates = [cmt._day_str(BASE_MS + i * BAR_MS) for i in range(n)]
    rng = np.random.default_rng(seed)

    def walk(x0, mu, sd):
        return x0 * np.exp(np.cumsum(rng.normal(mu, sd, n)))

    btc = walk(40000.0, 0.0004, 0.03)
    dvol = np.clip(walk(60.0, 0.0, 0.05), 10.0, 200.0)
    fund = rng.normal(0.05, 0.3, n)
    g = walk(1.0, 0.0005, 0.04)
    d = 0.5 * (btc / btc[0]) + 0.5
    return {"dates": dates, "index": {x: i for i, x in enumerate(dates)}, "n": n,
            "btc": btc, "g": g, "d": d, "dvol": dvol, "fund": fund,
            "funding_counts": {}, "btc_first_ms": None, "dvol_first": dates[0],
            "fund_first": dates[0], "g_first": dates[0], "source": {}}


class FakeCohort:
    """The cohort surface the engine reads: the bar grid, the OHLC path and the fee metadata."""

    def __init__(self, symbol, n=N_BARS, seed=11, close=None):
        self.symbol = symbol
        self.timeframe = "1d"
        self.qlib_freq = "day"
        self.open_time_ms = np.array([BASE_MS + i * BAR_MS for i in range(n)], dtype=np.int64)
        if close is None:
            rng = np.random.default_rng(seed)
            close = 100.0 * np.cumprod(1.0 + rng.normal(0.0004, 0.02, n))
        self.open = self.high = self.low = self.close = np.asarray(close, dtype=np.float64)
        self.n = n
        self.bar_ms = BAR_MS
        self.non_bar_steps = 0
        self._signal_cache = {}
        self._pred_cache = {}
        self._baseline_cache = {}
        self.spec = None
        self.price_increment = 0.1
        self.taker_fee = 0.0005
        self.leverage = 10.0
        self.margin_maint = 0.005
        self.inputs = cmt.build_network_inputs(self)
        self.inputs_report = {}

    def slice(self, start_date, end_date):
        lo = cmt.utc_ms(start_date)
        hi = cmt.utc_ms(end_date) + BAR_MS - 1
        return (int(np.searchsorted(self.open_time_ms, lo, side="left")),
                int(np.searchsorted(self.open_time_ms, hi, side="right")))


def install(bundle=None, n=N_BARS):
    """Inject the synthetic bundle + the four registered cohorts into the engine."""
    cmt._BUNDLE = synth_bundle(n) if bundle is None else bundle
    cohorts = {s: FakeCohort(s, n=n) for s in LABELS}
    cmt.PANEL_COHORTS.clear()
    cmt.PANEL_COHORTS.update(cohorts)
    cmt._PANEL_CACHE.clear()
    cmt.Panel._cache.clear()
    return cohorts


def spec_stub(start="2025-01-01", end="2027-06-09", hist=("2025-01-01", "2026-06-09"),
              oos=("2026-06-10", "2027-06-09")):
    return {"data": {"start": start, "end": end, "historical_start": hist[0],
                     "historical_end": hist[1], "oos_start": oos[0], "oos_end": oos[1]},
            "dca_domain": {"base_quote": 1000.0}}


# ---------------------------------------------------------------------------------------------
class RegisteredAxisTests(unittest.TestCase):
    def test_case_axis_is_the_registered_six(self):
        self.assertEqual(len(cmt.CASE_ORDER), 6)
        self.assertEqual(list(cmt.CASE_NAMES),
                         ["full__sm", "core__sm", "rate__sm", "full__raw", "core__raw",
                          "rate__raw"])
        self.assertEqual(len(cmt.CASE_FIELDS), len(cmt.SCORE_VARIANTS) + len(cmt.SMOOTHINGS))
        for row in cmt.CASE_ORDER:
            self.assertEqual(sum(row), 2)

    def test_case_accessors_round_trip(self):
        for case in cmt.CASE_ORDER:
            v = cmt.case_variant(case)
            s = cmt.case_smoothing(case)
            self.assertEqual(cmt.VARIANT_LABELS[v],
                             cmt.CASE_NAMES[cmt.case_index(case)].split("__")[0])
            rebuilt = tuple([1 if i == v else 0 for i in range(len(cmt.SCORE_VARIANTS))]
                            + [1 if i == s else 0 for i in range(len(cmt.SMOOTHINGS))])
            self.assertEqual(rebuilt, tuple(case))
            self.assertEqual(cmt.case_name(case), cmt.CASE_NAMES[cmt.case_index(case)])

    def test_registered_constants_match_the_record(self):
        self.assertEqual(cmt.TAU_SOFTPLUS, 1.0)
        self.assertEqual(cmt.ALPHA_RATE, 0.5)
        self.assertEqual(cmt.LAMBDA_S, 0.5)
        self.assertEqual(cmt.LAMBDA_C, 0.05)
        self.assertEqual(cmt.MAX_TILT, 0.5)
        self.assertEqual(cmt.TAU_W, 0.75)
        self.assertEqual(cmt.ETA_SMOOTH, 0.05)
        self.assertEqual(cmt.DELTA_WINDOW, 21)
        self.assertEqual(cmt.DVOL_PCT_WINDOW, 756)
        self.assertEqual(cmt.MOMENTUM_WINDOW, 126)
        self.assertEqual(cmt.MATERIALITY_W, 0.5)
        self.assertEqual(cmt.MARKET_ROLE[cmt.DEFENSIVE_ASSET], "defensive")
        for s in cmt.GROWTH_BASKET:
            self.assertEqual(cmt.MARKET_ROLE[s], "growth")


class PrimitiveTests(unittest.TestCase):
    def test_softplus_matches_the_literal_definition(self):
        for x in (-4.0, -0.5, 0.0, 0.25, 3.0):
            self.assertAlmostEqual(float(cmt._softplus(np.array([x]))[0]), ref_softplus(x), 12)

    def test_expanding_z_matches_a_naive_loop(self):
        x = np.array([np.nan] + list(np.linspace(-2.0, 3.0, 80)) + [np.nan, 1.5])
        got = cmt._expanding_z(x)
        want = ref_expanding_z(x)
        for a, b in zip(got, want):
            if np.isnan(b):
                self.assertTrue(np.isnan(a))
            else:
                self.assertAlmostEqual(float(a), float(b), 10)
        self.assertTrue(np.isnan(got[cmt.Z_MIN_OBS - 1]))

    def test_rolling_percentile_matches_a_naive_window_count(self):
        x = np.array([0.1 * (i % 13) + 5.0 for i in range(40)])
        got = cmt._rolling_percentile(x, 10)
        want = ref_percentile(x, 10)
        for a, b in zip(got, want):
            if np.isnan(b):
                self.assertTrue(np.isnan(a))
            else:
                self.assertAlmostEqual(float(a), float(b), 12)
        self.assertTrue(np.isnan(got[8]))
        self.assertFalse(np.isnan(got[9]))

    def test_percentile_tie_with_running_max_scores_one(self):
        x = np.concatenate([np.arange(1.0, 11.0), [20.0]])
        got = cmt._rolling_percentile(x, 5)
        self.assertAlmostEqual(float(got[-1]), 1.0, 12)

    def test_drawdown_matches_a_naive_loop(self):
        x = np.array([1.0, 1.2, 0.9, 1.5, 1.4, 2.0])
        got = cmt._drawdown(x)
        want = ref_drawdown(x)
        for a, b in zip(got, want):
            self.assertAlmostEqual(float(a), float(b), 12)
        self.assertAlmostEqual(float(got[2]), 0.9 / 1.2 - 1.0, 12)

    def test_relative_momentum_matches_a_naive_loop(self):
        g = np.array([1.0, 1.1, 1.2, 1.5, 1.4, 1.9])
        d = np.array([2.0, 2.0, 2.1, 2.2, 2.2, 2.4])
        got = cmt._relative_momentum(g, d, 3)
        want = ref_momentum(g, d, 3)
        for a, b in zip(got, want):
            if np.isnan(b):
                self.assertTrue(np.isnan(a))
            else:
                self.assertAlmostEqual(float(a), float(b), 12)

    def test_ewma_weight_path_matches_its_own_recursion(self):
        rng = np.random.default_rng(3)
        target = rng.uniform(0.0, 1.0, 40)
        target[:5] = np.nan
        got = cmt._ewma_weight_path(target, cmt.ETA_SMOOTH)
        want = ref_ewma(target, cmt.ETA_SMOOTH)
        for a, b in zip(got, want):
            if np.isnan(b):
                self.assertTrue(np.isnan(a))
            else:
                self.assertAlmostEqual(float(a), float(b), 12)
        self.assertAlmostEqual(float(got[5]),
                               (1 - cmt.ETA_SMOOTH) * cmt.W_G_INIT + cmt.ETA_SMOOTH * target[5], 12)


class ScoreChainTests(unittest.TestCase):
    def setUp(self):
        install()

    def test_every_case_reproduces_the_literal_score_chain(self):
        feats = cmt._cmt_features(cmt.signal_bundle())
        defined = 0
        for case in cmt.CASE_ORDER:
            variant = cmt.SCORE_VARIANTS[cmt.case_variant(case)]
            smoothing = cmt.SMOOTHINGS[cmt.case_smoothing(case)]
            got = cmt.score_path(feats, variant, smoothing)
            want = ref_score_chain(feats, variant, smoothing)
            finite = np.isfinite(want["weight"])
            defined += int(finite.sum())
            self.assertTrue(bool(finite.any()), "case %s has no defined bar" % variant)
            for a, b in zip(got["weight"][finite], want["weight"][finite]):
                self.assertAlmostEqual(float(a), float(b), 10)
            for a, b in zip(got["score"][finite], want["score"][finite]):
                self.assertAlmostEqual(float(a), float(b), 10)
        self.assertGreater(defined, 0)

    def test_weight_stays_inside_the_registered_band(self):
        feats = cmt._cmt_features(cmt.signal_bundle())
        for case in cmt.CASE_ORDER:
            path = cmt.score_path(feats, cmt.SCORE_VARIANTS[cmt.case_variant(case)],
                                  cmt.SMOOTHINGS[cmt.case_smoothing(case)])
            fin = np.isfinite(path["weight"])
            self.assertTrue(bool(fin.any()))
            self.assertLessEqual(float(np.max(path["weight"][fin])),
                                 0.5 + cmt.MAX_TILT + 1e-12)
            self.assertGreaterEqual(float(np.min(path["weight"][fin])),
                                    0.5 - cmt.MAX_TILT - 1e-12)

    def test_no_smoothing_arm_is_the_target_itself(self):
        feats = cmt._cmt_features(cmt.signal_bundle())
        for variant in cmt.SCORE_VARIANTS:
            path = cmt.score_path(feats, variant, "none")
            fin = np.isfinite(path["weight"])
            self.assertTrue(bool(fin.any()))
            self.assertTrue(np.allclose(path["weight"][fin], path["target"][fin]))

    def test_unregistered_variant_fails_closed(self):
        feats = cmt._cmt_features(cmt.signal_bundle())
        with self.assertRaises(SystemExit):
            cmt.score_path(feats, "not_a_variant", "ewma")


class StateRuleTests(unittest.TestCase):
    def test_growth_and_defensive_read_opposite_sides_of_the_neutral_weight(self):
        w = np.array([np.nan, 0.2, 0.5, 0.8])
        st = cmt._states_from_weight(w, ["BTCUSDT", "ETHUSDT"])
        self.assertEqual(list(st[:, 0]), [0, 1, 0, 0])   # defensive: LONG while w < 0.5
        self.assertEqual(list(st[:, 1]), [0, 0, 0, 1])   # growth: LONG while w > 0.5

    def test_unknown_market_role_fails_closed(self):
        with self.assertRaises(SystemExit):
            cmt._states_from_weight(np.array([0.9]), ["BTCUSDT", "XYZUSDT"])


class PanelTests(unittest.TestCase):
    def setUp(self):
        self.cohorts = install()

    def grid(self):
        return {m: self.cohorts[m].open_time_ms for m in LABELS}

    def test_panel_invariants(self):
        for case in cmt.CASE_ORDER:
            core = cmt._panel_core(case, self.grid())
            self.assertEqual(core["labels"], LABELS)
            self.assertEqual(core["n"], N_BARS)
            self.assertTrue(set(np.unique(core["state"])).issubset({0, 1}))
            self.assertEqual(set(np.unique(core["events"])).issubset({0, 1}), True)
            for j, m in enumerate(LABELS):
                longs = int((core["state"][:, j] > 0).sum())
                self.assertEqual(longs, core["long_bars"][m])
                events = cmt._fresh_events(core["state"])[:, j]
                self.assertEqual(int((events != 0).sum()),
                                 core["score_diagnostics"]["events_per_market"][m])
            # the two roles never hold at the same time
            growth = np.max(core["state"][:, [LABELS.index(s) for s in cmt.GROWTH_BASKET]], axis=1)
            defl = core["state"][:, LABELS.index(cmt.DEFENSIVE_ASSET)]
            self.assertEqual(int(np.max(growth + defl)), 1)
            self.assertGreater(core["defined_bars"], 100)

    def test_every_bar_is_either_undefined_or_finite_and_causal(self):
        core = cmt._panel_core(cmt.CASE_ORDER[0], self.grid())
        w = core["weight"]
        self.assertEqual(int(np.count_nonzero(np.isnan(w))),
                         core["n"] - core["defined_bars"])
        # prefix truncation: a truncated history must reproduce the past exactly
        bundle = cmt.signal_bundle()
        probed = 0
        for t in range(core["warmup_bars"], core["n"] - 1,
                       max(1, (core["n"] - core["warmup_bars"]) // 5)):
            upto = bundle["index"][cmt._day_str(core["grid_ms"][t])]
            cut = cmt._panel_core(cmt.CASE_ORDER[0], self.grid(), upto=upto)
            fin = np.isfinite(cut["weight"][:t + 1]) & np.isfinite(w[:t + 1])
            self.assertTrue(np.array_equal(np.isfinite(cut["weight"][:t + 1]),
                                           np.isfinite(w[:t + 1])))
            if bool(fin.any()):
                self.assertLessEqual(float(np.max(np.abs(cut["weight"][:t + 1][fin]
                                                         - w[:t + 1][fin]))), 1e-9)
            self.assertTrue(np.array_equal(cut["state"][:t + 1], core["state"][:t + 1]))
            probed += 1
        self.assertGreater(probed, 0)

    def test_causality_probe_reports_no_mismatch(self):
        cohort = self.cohorts["ETHUSDT"]
        probe = cmt.causality_probe(cohort, cohort.inputs)
        self.assertGreater(probe["bars_probed"], 0)
        self.assertEqual(probe["mismatches"], 0)
        self.assertEqual(cmt.counters_total("causality_probe_mismatch"), 0)

    def test_signal_layer_matches_the_panel_view(self):
        cohort = self.cohorts["ETHUSDT"]
        case = cmt.CASE_ORDER[0]
        layer = cmt.signals_for(cohort, case)
        core = cmt._panel_core(case, self.grid())
        i = LABELS.index("ETHUSDT")
        self.assertTrue(np.array_equal(layer.pos, core["state"][:, i]))
        self.assertTrue(np.array_equal(layer.yhat, core["score"], equal_nan=True))

    def test_all_nan_inputs_fail_closed(self):
        n = 200
        dates = [cmt._day_str(BASE_MS + i * BAR_MS) for i in range(n)]
        nan = np.full(n, np.nan)
        bundle = {"dates": dates, "index": {d: i for i, d in enumerate(dates)}, "n": n,
                  "btc": nan.copy(), "g": nan.copy(), "d": nan.copy(), "dvol": nan.copy(),
                  "fund": nan.copy(), "funding_counts": {}, "btc_first_ms": None,
                  "dvol_first": None, "fund_first": None, "g_first": None, "source": {}}
        cohorts = install(bundle=bundle, n=n)
        core = cmt._panel_core(cmt.CASE_ORDER[0], {m: cohorts[m].open_time_ms for m in LABELS})
        self.assertEqual(core["defined_bars"], 0)
        self.assertEqual(int(np.count_nonzero(core["state"])), 0)
        self.assertEqual(core["warmup_bars"], n)

    def test_panel_needs_the_registered_size(self):
        with self.assertRaises(SystemExit):
            cmt._panel_core(cmt.CASE_ORDER[0], {LABELS[0]: self.cohorts[LABELS[0]].open_time_ms})


class PlaceboTests(unittest.TestCase):
    def setUp(self):
        install()

    def test_synthetic_walk_is_deterministic_and_drift_matched(self):
        bundle = cmt.signal_bundle()
        rng_a = np.random.default_rng(cmt.PLACEBO_SEED)
        rng_b = np.random.default_rng(cmt.PLACEBO_SEED)
        a = cmt._synthetic_walk(bundle["btc"], "log", rng_a)
        b = cmt._synthetic_walk(bundle["btc"], "log", rng_b)
        self.assertTrue(np.array_equal(a, b))
        src = np.log(bundle["btc"][1:] / bundle["btc"][:-1])
        got = np.log(a[1:] / a[:-1])
        # the walk is drift-MATCHED (same mean/sd parameters), so the realised sample
        # statistics agree only up to the sampling error of one registered draw
        se = float(np.std(src, ddof=1)) / math.sqrt(len(src))
        self.assertLessEqual(abs(float(np.mean(got)) - float(np.mean(src))), 5.0 * se)
        self.assertLessEqual(abs(float(np.std(got, ddof=1)) - float(np.std(src, ddof=1))),
                             0.2 * float(np.std(src, ddof=1)))

    def test_arith_walk_matches_the_level_drift(self):
        x = np.linspace(-0.5, 0.5, 60)
        rng = np.random.default_rng(1)
        y = cmt._synthetic_walk(x, "arith", rng)
        d_src = np.diff(x)
        d_got = np.diff(y)
        se = float(np.std(d_src, ddof=1)) / math.sqrt(len(d_src))
        self.assertLessEqual(abs(float(np.mean(d_got)) - float(np.mean(d_src))), 5.0 * se)
        self.assertLessEqual(abs(float(np.std(d_got, ddof=1)) - float(np.std(d_src, ddof=1))),
                             0.2 * float(np.std(d_src, ddof=1)))

    def test_synthetic_bundle_replaces_exactly_the_three_proxy_slots(self):
        bundle = cmt.signal_bundle()
        synth = cmt._synthetic_bundle(bundle)
        for key in ("dates", "index", "g", "d"):
            self.assertTrue(synth[key] is bundle[key] or np.array_equal(synth[key], bundle[key]))
        for key in ("fund", "dvol", "btc"):
            self.assertFalse(bool(np.allclose(synth[key], bundle[key])))

    def test_reader_shells_are_registered_and_never_pass_bearing(self):
        for fn, item in ((cmt.stationary_random_placebo, "stationary-random-placebo"),
                         (cmt.cost_stress_boundary, "cost-stress-boundary"),
                         (cmt.parameter_perturbation_grid, "parameter-perturbation-grid"),
                         (cmt.subperiod_rate_hike_drawdown, "subperiod-rate-hike-drawdown")):
            out = fn({}, [], {}, {})
            self.assertEqual(out["registered_item"], item)
            self.assertFalse(out["evaluated"])
            self.assertFalse(out["hit"])
            self.assertIn("NEVER be recorded as PASS", out["landing"])

    def test_cost_stress_reader_lands_on_the_registered_boundary(self):
        cohorts = self.cohorts = install()
        cohort = cohorts["ETHUSDT"]
        case = cmt.CASE_ORDER[0]
        fields = tuple(int(v) for v in case)
        row = {f: v for f, v in zip(cmt.CASE_FIELDS, case)}
        row.update({"spacing_pct": 0.5, "size_multiplier": 1.0, "breakeven_tp_pct": 0.5,
                    "invalidation_pct": 0.9, "cagr": -0.50, "net_pnl": -10.0, "fees": 5.0,
                    "sharpe": -0.4, "episodes": 4})
        full_row = dict(row, cagr=0.25, net_pnl=100.0, sharpe=0.9)
        rows_by_cohort = {"ETHUSDT": {"cost_attrition_40bps": [row], "full": [full_row]}}
        spec = spec_stub()
        spec["data"].update({"symbols": ["ETHUSDT"], "timeframes": [{"raw_interval": "1d"}]})
        winner = {f: v for f, v in zip(cmt.CASE_FIELDS, case)}
        winner.update({"spacing_pct": 0.5, "size_multiplier": 1.0, "breakeven_tp_pct": 0.5,
                       "invalidation_pct": 0.9})
        out = cmt.cost_stress_boundary(spec, [{"cohort": "ETHUSDT", "winner": winner}],
                                       {"ETHUSDT": (cohort, EMPTY_FUNDING)}, rows_by_cohort)
        self.assertTrue(out["evaluated"])
        self.assertTrue(out["cohorts"]["ETHUSDT"]["hit"])       # -50 % CAGR < the mix
        self.assertEqual(out["record_threshold_cagr"], 0.1712)
        self.assertIn("17.12", out["threshold_disclosure"])
        self.assertIn("provenance", out["threshold_disclosure"])


class RailSeamTests(unittest.TestCase):
    """The reader-side layer re-enters the SAME frozen rail (`layer=`), so the seam is checked
    end to end on a synthetic weight path."""

    def setUp(self):
        self.cohorts = install()

    def test_synthetic_layer_enters_on_the_next_bar_and_keeps_the_accounting_identity(self):
        cohort = self.cohorts["ETHUSDT"]
        n = cohort.n
        weight = np.full(n, np.nan)
        weight[100:200] = 0.9          # growth overweight -> LONG the growth instrument
        weight[200:260] = 0.1          # defensive overweight -> flat for ETHUSDT
        weight[260:400] = 0.8
        score = np.where(np.isfinite(weight), (weight - 0.5) / 0.5, np.nan)
        layer = cmt.SyntheticLayer(cohort, LABELS, weight, score, "seam_test", "test")
        expect = np.zeros(n, dtype=np.int64)
        expect[100:200] = 1
        expect[260:400] = 1
        self.assertTrue(np.array_equal(layer.pos, expect))
        self.assertEqual(int(layer.events[100]), 1)
        self.assertEqual(int(layer.events[200]), 0)
        m = cmt.simulate(cohort, {f: v for f, v in zip(cmt.CASE_FIELDS, cmt.CASE_ORDER[0])},
                         cmt.rail_for(DCA), (0, n), {}, 1, "seam", EMPTY_FUNDING,
                         layer=layer, count_layers=False)
        self.assertTrue(cmt.pnl_decomposition_ok(m))
        self.assertGreaterEqual(m["episodes"], 1)
        self.assertEqual(cmt.counters_snapshot()["seam"]["signal_undefined_on_event_bar"], 0)
        self.assertEqual(cmt.counters_snapshot()["seam"]["entry_before_a_defined_signal"], 0)

    def test_undefined_weight_never_enters(self):
        cohort = self.cohorts["ETHUSDT"]
        n = cohort.n
        weight = np.full(n, np.nan)
        layer = cmt.SyntheticLayer(cohort, LABELS, weight, weight, "seam_nan", "test")
        self.assertEqual(int(np.count_nonzero(layer.pos)), 0)
        self.assertEqual(int(np.count_nonzero(layer.events)), 0)


class EngineHygieneTests(unittest.TestCase):
    def test_source_carries_no_strategy_l_tokens(self):
        text = open(ENGINE).read()
        for token in ("SCALE_ARMS", "RIDGE_LAMBDA", "conformal", "kelly", "Kelly",
                      "case_fields_for", "SIGNAL_WARMUP_BARS", "strategyL"):
            self.assertNotIn(token, text, "strategy-L token %r survived" % token)

    def test_engine_identity_strings(self):
        self.assertEqual(cmt.ENGINE_VERSION, "cmt-v1-engine-1.0.0")
        self.assertIn("continuous macro-timing", cmt.ENGINE_SEMANTICS)
        self.assertEqual(cmt.PANEL_SIZE, 4)
        self.assertEqual(cmt.COHORT_GRID_KINDS[0], "historical")
        self.assertIn("cost_attrition_40bps", cmt.COHORT_GRID_KINDS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
