#!/usr/bin/env python3
"""Self-check for 250_bayesian_ppp_run.py (Bayesian Parametric Portfolio Policies).

One assertion per non-trivial rule.  No qlib import and no write: the module under test
must stay importable without opening the result tree.
"""
import importlib.util
import json
import math
import os
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "250_bayesian_ppp_run.py")
_spec = importlib.util.spec_from_file_location("bayesian_ppp_run", SCRIPT)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

BAR_MS = 86_400_000          # one day per bar keeps the 335-day lookback affordable
N_BARS = 700
TICK = 0.01
TAKER = 0.0005


def synth_panel(seed=7, n=N_BARS, bar_ms=BAR_MS):
    rng = np.random.default_rng(seed)
    start = 1_640_995_200_000          # 2022-01-01T00:00:00Z
    out = {}
    for si, sym in enumerate(mod.SYMBOLS):
        drift = 0.0004 * (si - 1.5)
        vol = 0.020 * (1.0 + 0.30 * si)
        close = 100.0 * np.exp(np.cumsum(rng.normal(drift, vol, n)))
        open_ = np.concatenate([[close[0]], close[:-1]])
        high = np.maximum(open_, close) * (1.0 + np.abs(rng.normal(0.0, vol / 3.0, n)))
        low = np.minimum(open_, close) * (1.0 - np.abs(rng.normal(0.0, vol / 3.0, n)))
        volume = np.abs(rng.normal(1e6, 2e5, n)) + 1e5
        out[sym] = {"open": open_, "high": high, "low": low, "close": close,
                    "volume": volume, "open_ms": start + np.arange(n, dtype=np.int64) * bar_ms}
    return out


def test_coverage_product_is_the_registered_cartesian_grid():
    counts = mod.expected_counts()
    assert counts["cohorts"] == len(mod.SYMBOLS) * len(mod.TIMEFRAMES) == 28
    assert counts["strategy_cases_per_cohort"] == len(mod.STRATEGIES) == 4
    assert counts["dca_configs_per_cohort"] == len(mod.DCA_GRID) == 48
    assert counts["base_combinations_per_cohort"] == 192
    assert counts["grid_count"] == len(mod.GRIDS) == 10
    assert counts["case_evaluations_total"] == 28 * 4 * 48 * 10 == 53760
    # every phase grid carries the identical product (coverage gate G1): each of the 10
    # registered grids is exactly cohorts x strategy cases x 48 DCA configurations.
    assert counts["case_evaluations_per_grid"] == 28 * 4 * 48 == 5376
    assert counts["case_evaluations_total"] == counts["case_evaluations_per_grid"] * 10


def test_rebalance_clock_is_four_hours_of_each_own_bar_grid():
    assert dict(mod.REBALANCE_BARS) == {"5m": 48, "15m": 16, "30m": 8, "1h": 4,
                                        "4h": 1, "1d": 1, "1w": 1}


def test_characteristic_count_equals_cross_section_dimension():
    # K = N is the identification limit of the linear policy: theta enters only through X.
    assert mod.N_CHAR == len(mod.CHAR_SPECS) == len(mod.SYMBOLS) == 4
    assert mod.signal_constants()["cross_section_size"] == len(mod.SYMBOLS)


def test_characteristics_are_standardized_per_the_record():
    panels = synth_panel()
    z, good = mod.standardized_characteristics(panels, BAR_MS)
    assert z.shape == (N_BARS, 4, 4)
    idx = np.flatnonzero(good)
    assert len(idx) > 50, "warm-up must leave a usable characterized sample"
    assert np.all(idx[0] >= max(mod.bar_lookbacks(BAR_MS)))
    sub = z[idx]
    assert np.allclose(sub.mean(axis=1), 0.0, atol=1e-9), "sum_i x must be 0 at every t"
    assert np.allclose(sub.std(axis=1), 1.0, atol=1e-9), "(1/N) sum_i x^2 must be 1 at every t"
    # bars before the longest lookback stay at zero and never become decision bars
    assert np.all(z[: idx[0]] == 0.0)


def test_estimator_never_reads_a_futuristic_payoff():
    panels = synth_panel()
    z, _ = mod.standardized_characteristics(panels, BAR_MS)
    f, r_m = mod.characteristic_payoffs(z, panels)
    reb = mod.REBALANCE_BARS["1d"]
    est = 30
    first = int(math.ceil(mod.warmup_bars(BAR_MS, est) / float(reb))) * reb
    layer_a = mod.policy_layer(z, f, r_m, reb, est, 1.0, first)

    cut = N_BARS - 40                       # perturb everything strictly after ``cut``
    tampered = {s: dict(v) for s, v in panels.items()}
    rng = np.random.default_rng(11)
    for s in mod.SYMBOLS:
        close = tampered[s]["close"].copy()
        close[cut:] *= np.exp(rng.normal(0.0, 0.05, N_BARS - cut))
        tampered[s]["close"] = close
        tampered[s]["open"] = np.concatenate([[close[0]], close[:-1]])
        tampered[s]["high"] = np.maximum(tampered[s]["open"], close)
        tampered[s]["low"] = np.minimum(tampered[s]["open"], close)
    z2, _ = mod.standardized_characteristics(tampered, BAR_MS)
    f2, r_m2 = mod.characteristic_payoffs(z2, tampered)
    layer_b = mod.policy_layer(z2, f2, r_m2, reb, est, 1.0, first)

    assert np.allclose(layer_a["weights"][:cut], layer_b["weights"][:cut], atol=0.0), \
        "weights before the tampered region must be byte-identical"
    assert layer_a["diag"]["causal"] is True
    assert layer_a["diag"]["decisions"] == layer_b["diag"]["decisions"]


def test_bayesian_system_is_strictly_more_regularized_and_prior_monotone():
    panels = synth_panel()
    z, _ = mod.standardized_characteristics(panels, BAR_MS)
    f, r_m = mod.characteristic_payoffs(z, panels)
    reb = mod.REBALANCE_BARS["1d"]
    est = 90
    first = int(math.ceil(mod.warmup_bars(BAR_MS, est) / float(reb))) * reb
    d = int(mod.decision_bars(N_BARS, reb, first)[20])
    info = mod.estimate(f[d - est:d], r_m[d - est:d], 1.0)
    assert info is not None and info["observations"] == est

    # Var(theta | D) is a covariance: symmetric, PSD
    var = info["var_theta"]
    assert np.allclose(var, var.T, atol=1e-12)
    assert np.linalg.eigvalsh(var).min() >= -1e-12

    theta_b = mod.solve_policy(info, 1.0, mod.PRIOR_SCALE, "bayes")
    theta_p = mod.solve_policy(info, 1.0, mod.PRIOR_SCALE, "plugin")
    assert theta_b is not None and theta_p is not None
    assert np.allclose(info["sigma_f"] @ theta_p, info["b"], atol=1e-8), "plug-in normal eq."
    system = (info["sigma_f"]
              + np.eye(4) / (1.0 * mod.PRIOR_SCALE)
              + var)
    assert np.allclose(system @ theta_b, info["b"], atol=1e-8), "Bayesian normal eq."
    assert (np.linalg.eigvalsh(system).min()
            > np.linalg.eigvalsh(info["sigma_f"]).min()), "quadratic variance regularization"

    # (A + cI)^{-1} b with fixed A is strictly increasing in c's reciprocal: a weaker prior
    # (larger sigma_0^2) must release a strictly larger policy coefficient vector.
    norms = [float(np.linalg.norm(mod.solve_policy(info, 1.0, s, "bayes")))
             for s in (0.01, 0.1, 1.0, 10.0)]
    assert all(a < b for a, b in zip(norms, norms[1:])), norms
    assert norms[0] < float(np.linalg.norm(theta_p)), "strong prior must shrink below plug-in"


def test_plug_in_is_rejected_when_the_design_is_singular():
    # f block chosen so Sigma_f is exactly rank 1 -> condition number explodes.
    t = np.linspace(0.0, 1.0, 60)
    f_block = np.column_stack([t, 2 * t, 3 * t, 4 * t])
    m_block = np.sin(2 * np.pi * t) * 0.01
    info = mod.estimate(f_block, m_block, 1.0)
    assert info is not None
    assert info["sigma_condition"] > mod.SIGMA_CONDITION_LIMIT
    assert mod.solve_policy(info, 1.0, mod.PRIOR_SCALE, "plugin") is None
    assert mod.solve_policy(info, 1.0, mod.PRIOR_SCALE, "bayes") is not None, \
        "the registered Bayesian policy must stay well posed where the plug-in collapses"


def test_simulate_reconciles_gross_fees_funding_and_net():
    panels = synth_panel()
    n = N_BARS
    weights = np.zeros((n, len(mod.SYMBOLS)))
    decisions = np.arange(370, n, 1, dtype=np.int64)
    weights[370::7, :] = 0.05            # a qualified active tilt every seventh bar
    layer = {"weights": weights, "decisions": decisions, "diag": {}}
    dca = dict(mod.DCA_GRID[0])
    events = [[] for _ in range(n)]
    instrument = {"tick": TICK, "taker_fee": TAKER}
    symbol = mod.SYMBOLS[1]
    metric = mod.simulate(panels[symbol], events, layer, symbol, dca, 0, n, {}, instrument)

    assert metric["episodes"] >= 3, "the rail must actually trade in this fixture"
    assert metric["decomposition_ok"] is True
    residual = (metric["gross_pnl"] - metric["fees"] - metric["funding"]) - metric["net_pnl"]
    assert abs(residual) <= 1e-3, "gross - fees - funding must equal net, got %r" % residual
    hist = metric["layer_hist"]
    assert len(hist) == mod.LADDER_LEVELS, "the ladder histogram must span all 12 tranches"
    assert all(a >= b for a, b in zip(hist, hist[1:])), "layer histogram must be monotone"
    assert hist[0] <= metric["episodes"], "level-1 reaches cannot exceed episodes"
    assert sum(hist) <= metric["fills"], "layer reaches cannot exceed fills"
    assert abs(metric["ending_equity"] - mod.START_EQUITY - metric["net_pnl"]) <= 1e-3

    # negative control: double the taker fee must really move net PnL (cost rail is no-op-free)
    costly = mod.simulate(panels[symbol], events, layer, symbol, dca, 0, n,
                          {"fee_override": TAKER * 2}, instrument)
    assert costly["net_pnl"] < metric["net_pnl"], "fee_2x must reduce net PnL"

    # gross PnL is an independent accumulator, not net minus fees by construction
    assert metric["gross_pnl"] != metric["net_pnl"]
    gross_only = mod.simulate(panels[symbol], events, layer, symbol, dca, 0, n,
                              {"fee_override": 0.0}, instrument)
    assert abs(gross_only["gross_pnl"] - metric["gross_pnl"]) <= 1e-9, \
        "gross PnL must not depend on the fee rail"


def test_run_spec_round_trips_through_validate_spec():
    spec = mod.run_spec_template()
    spec["round_id"] = mod.FAMILY_ID + "-r1"
    spec["run_id"] = spec["round_id"] + "-u1"
    spec["created_at_utc"] = "2026-09-27T00:00:00Z"
    assert spec["falsification"] == mod.FALSIFICATION
    assert set(spec["falsification"]["record_tests"]) == {
        "oos_sharpe_and_turnover", "crisis_tail_risk_compression", "prior_sensitivity"}
    assert "kanban_task_id" not in spec and "task_id" not in spec and "kanban_board" not in spec

    fixture = {"market_type": "usdm_perp", "symbols": sorted(mod.SYMBOLS),
               "intervals": [tf["raw_interval"] for tf in mod.TIMEFRAMES],
               "datasets": {"klines": "k", "funding": "f"}}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(fixture, fh)
        meta_path = fh.name
    try:
        counts = mod.validate_spec(spec, meta_path=meta_path)
    finally:
        os.unlink(meta_path)
    assert counts == mod.expected_counts()

    # a direct family must refuse task/board ownership keys
    bad = dict(spec)
    bad["kanban_task_id"] = "t_phantom"
    try:
        mod.validate_spec(bad, meta_path=meta_path)
    except ValueError:
        pass
    else:
        raise AssertionError("run-spec with a kanban id must be rejected")
    finally:
        if os.path.exists(meta_path):
            os.unlink(meta_path)


def test_falsification_battery_is_frozen_before_compute():
    cfg = mod.FALSIFICATION
    assert cfg["record_tests"]["oos_sharpe_and_turnover"]["thresholds"] == {
        "min_sharpe_improvement": 0.15, "max_turnover_ratio": 0.75}
    assert cfg["record_tests"]["crisis_tail_risk_compression"]["thresholds"] == {
        "max_drawdown_ratio": 0.80}
    assert cfg["record_tests"]["prior_sensitivity"]["prior_grid"] == [0.01, 0.1, 1.0, 10.0]
    assert cfg["record_tests"]["prior_sensitivity"]["variation_threshold"] == 0.40
    assert cfg["required_stress_grids"] == list(mod.REQUIRED_STRESS)
    assert "never reworded into PASS" in cfg["failure_policy"]
    # sigma_0^2 must sit inside the record's own perturbation range
    assert 0.01 <= mod.PRIOR_SCALE <= 10.0


if __name__ == "__main__":
    failures = []
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS  %s" % name)
            except Exception as exc:                     # noqa: BLE001
                failures.append(name)
                print("FAIL  %s: %r" % (name, exc))
    if failures:
        raise SystemExit("%d self-check(s) failed: %s" % (len(failures), ", ".join(failures)))
    print("all self-checks passed")
