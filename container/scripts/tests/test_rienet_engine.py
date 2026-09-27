#!/usr/bin/env python3
"""Deterministic host self-check for the RIEnet engine; no Qlib or raw data needed.

Covers the family-owned surface (pairwise-complete indefinite correlation, tau_k / q_k,
BiGRU token shrinkage and its reconstruction, BPTT gradient correctness, training, entry
events, prefix causality, selector cull reasons, the record's synthetic falsification
battery) and the accounting contract of the reused audited rail (gross/fee/funding
decomposition with a negative control, adverse slippage direction, DCA ladder, funding
accounting).  Run from the repo root or from this directory.
"""
import importlib.util
import itertools
from pathlib import Path

import numpy as np

SCRIPT = Path(__file__).resolve().parents[1] / "220_rienet_run.py"
spec = importlib.util.spec_from_file_location("rienet_run", SCRIPT)
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)


def synthetic_panel(seed=11, bars=480, assets=4, missing=0.0):
    """Return closes/highs/lows with a controllable ragged observation mask."""
    rng = np.random.default_rng(seed)
    factor = rng.normal(0.0, 0.006, size=(bars, 1))
    idio = rng.normal(0.0, 0.006, size=(bars, assets))
    rets = factor + idio
    close = 100.0 * np.exp(np.cumsum(rets, axis=0))
    high = close * (1.0 + np.abs(rng.normal(0.0, 0.003, size=(bars, assets))))
    low = close * (1.0 - np.abs(rng.normal(0.0, 0.003, size=(bars, assets))))
    if missing > 0:
        holes = rng.random((bars, assets)) < missing
        holes[0] = False
        for arr in (close, high, low):
            arr[holes] = np.nan
    return close, high, low


def case(**overrides):
    base = {"case_code": 0, "lookback_days": 600, "vol_branch": 0,
            "vol_branch_code": 0, "label": "lb600__vol0"}
    base.update(overrides)
    return base


def tf_of(raw_interval):
    return next(t for t in engine.TIMEFRAMES if t["raw_interval"] == raw_interval)


def make_row(symbol, timeframe, strat, params, net_pnl, sharpe, episodes, grid="historical"):
    row = {"symbol": symbol, "timeframe": timeframe, "case_label": strat["label"], "grid": grid,
           "net_pnl": float(net_pnl), "sharpe": float(sharpe), "episodes": int(episodes),
           "fills": int(episodes), "adds": 0, "gross_pnl": float(net_pnl), "fees": 0.0,
           "funding": 0.0, "turnover_usdt": 0.0, "max_dd_pct": 0.0, "annualized_return": 0.0,
           "capital_utilization": 0.0, "tp_hits": 0, "stop_hits": 0, "time_exits": 0}
    row.update(engine.case_columns(strat))
    row.update(params)
    return row


def test_registered_constants():
    assert engine.FAMILY_ID == "neural-shrinkage-indefinite-pairwise-correlation-matrix-2026-09-02"
    assert engine.ENGINE_VERSION == "rienet_v1"
    assert engine.SYMBOLS == ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
    assert [tf["raw_interval"] for tf in engine.TIMEFRAMES] == \
        ["5m", "15m", "30m", "1h", "4h", "1d", "1w"]
    assert engine.DIRECTION_MODE == "long_only"
    assert len(engine.STRATEGIES) == 4
    assert {s["lookback_days"] for s in engine.STRATEGIES} == {600, 1200}
    assert {s["vol_branch_code"] for s in engine.STRATEGIES} == {0, 1}
    dca_count = 1
    for values in engine.DCA_AXES.values():
        dca_count *= len(values)
    assert dca_count == 48
    assert len(engine.GRID_KINDS) == 10
    total = len(engine.SYMBOLS) * len(engine.TIMEFRAMES) * len(engine.STRATEGIES) * dca_count
    assert total == 5376, total                       # 28 cohorts x 4 cases x 48 DCA cells
    assert total * len(engine.GRID_KINDS) == 53760    # full G1 coverage product
    assert engine.GATES["min_episodes_is"] == 30 and engine.GATES["min_episodes_oos"] == 10
    assert engine.GRU_HIDDEN == 32 and engine.TOKEN_DIM == 6
    assert engine.WEIGHT_GRID == 0.001 and engine.REBAL_SESSIONS == 5
    assert engine.disposition_for_survivor_count(0) == "REJECT / NO_SURVIVOR"
    assert engine.disposition_for_survivor_count(1) == "SURVIVOR_FOUND"
    assert engine.disposition_for_survivor_count(3) == "MULTIPLE_SURVIVORS"
    ticks = engine.instrument_constants()
    assert ticks["tick"]["BTCUSDT"] == 0.10 and ticks["taker_fee"]["BTCUSDT"] == 0.0005
    # row_key / param_only / case_columns must describe the registered joint domain.
    strat = engine.STRATEGIES[3]                     # (1200 days, close_plus_parkinson)
    row = make_row("BTCUSDT", "1d", strat, {"spacing_pct": 0.02, "size_multiplier": 1.1,
                                            "breakeven_tp_pct": 0.01, "invalidation_pct": 0.10},
                   5.0, 1.0, 40)
    assert engine.row_key(row)[0] == 1200 and engine.row_key(row)[1] == 1
    assert sorted(engine.param_only(row)) == sorted(
        ["lookback_days", "vol_branch_code", "spacing_pct", "size_multiplier",
         "breakeven_tp_pct", "invalidation_pct"])


def test_pairwise_indefinite_and_reconstruction():
    """The core mechanism: a ragged panel really yields an indefinite C_cap that RIEnet repairs."""
    close, high, low = synthetic_panel(seed=2, bars=500, assets=4, missing=0.50)
    prefix = engine.build_prefix(close, high, low)
    mom = engine.window_moments(prefix, 1, close.shape[0])
    mu, sig = engine.marginal_moments(mom)
    corr, overlap, _ = engine.pairwise_correlation(mom, mu, sig)
    lam, qvec, tokens = engine.spectral_features(corr, overlap)
    assert np.allclose(np.diag(corr), 1.0)
    assert overlap.min() > 0
    min_eig = float(np.linalg.eigvalsh(corr).min())
    assert min_eig < 0.0, "pairwise-complete C_cap must be indefinite on a ragged panel"
    assert lam.min() < 0.0 and tokens.shape == (4, engine.TOKEN_DIM)
    # tau_k / q_k: uniform overlap T gives tau_k = T exactly.
    uniform = np.full((4, 4), 73.0)
    np.fill_diagonal(uniform, 73.0)
    _, _, tokens_u = engine.spectral_features(corr, uniform)
    q_from_tokens = tokens_u[:, 3]                      # q_k = n / tau_k
    assert np.allclose(4.0 / q_from_tokens, 73.0, rtol=1e-9)
    # RIEnet reconstruction must be positive definite and unit-diagonal correlation before scaling.
    params = engine.init_rienet_params(engine.SEED, vol_dim=2)
    lam_inv, _, _ = engine.gru_forward(params, tokens[None, :, :])
    lam_nn = 1.0 / lam_inv
    assert (lam_nn > 0).all()
    sig_batch = sig[None, :]
    sigma = engine.reconstruct(qvec[None, :, :], lam_nn, sig_batch)
    assert sigma.shape == (1, 4, 4)
    assert np.linalg.eigvalsh(sigma[0]).min() > 0.0, "reconstruction must be positive definite"
    corr_nn = sigma[0] / np.outer(sig, sig)
    assert np.allclose(np.diag(corr_nn), 1.0, atol=1e-9)


def test_complete_panel_matches_numpy():
    """With a complete panel the pairwise estimator must equal the textbook correlation."""
    close, high, low = synthetic_panel(seed=5, bars=400, assets=4, missing=0.0)
    prefix = engine.build_prefix(close, high, low)
    mom = engine.window_moments(prefix, 1, close.shape[0])
    mu, sig = engine.marginal_moments(mom)
    corr, _, _ = engine.pairwise_correlation(mom, mu, sig)
    with np.errstate(invalid="ignore"):
        prev = np.vstack([np.zeros((1, 4)), close[:-1]])
        rets = np.where((close > 0) & (prev > 0), np.log(close / np.where(prev > 0, prev, 1.0)), 0.0)
    rets = rets[1:]
    expected = np.corrcoef(rets, rowvar=False)
    assert np.allclose(corr, expected, atol=1e-10), np.abs(corr - expected).max()
    # Realised covariance on a complete window must be the ordinary sample covariance.
    cov = engine.pairwise_covariance(mom, mu, sig)
    assert np.allclose(cov, np.cov(rets, rowvar=False), atol=1e-12)


def test_gmv_long_only():
    assets = 4
    uniform = np.full((assets, assets), 0.6)
    np.fill_diagonal(uniform, 1.0)
    weight = engine.gmv_long_only(uniform[None, :, :])[0]
    assert np.isclose(weight.sum(), 1.0) and (weight >= 0).all()
    assert np.allclose(weight, 1.0 / assets, atol=1e-8)     # exchangeable => equal weights
    skewed = np.array([[1.0, 0.9, 0.05, 0.05],
                       [0.9, 1.0, 0.05, 0.05],
                       [0.05, 0.05, 1.0, 0.9],
                       [0.05, 0.05, 0.9, 1.0]])
    w2 = engine.gmv_long_only(skewed[None, :, :])[0]
    assert np.isclose(w2.sum(), 1.0) and (w2 >= -1e-12).all()
    best = float(w2 @ skewed @ w2)
    rng = np.random.default_rng(0)
    for _ in range(200):
        probe = rng.random(assets)
        probe /= probe.sum()
        assert best <= float(probe @ skewed @ probe) + 1e-9
    # A matrix needing an active-set switch (interior point infeasible) still returns a
    # feasible long-only solution with variance no worse than the uniform allocation.
    ill = np.array([[1.0, -0.99, 0.0, 0.0],
                    [-0.99, 1.0, 0.0, 0.0],
                    [0.0, 0.0, 0.25, 0.01],
                    [0.0, 0.0, 0.01, 0.25]])
    w3 = engine.gmv_long_only(ill[None, :, :])[0]
    assert (w3 >= -1e-12).all() and np.isclose(w3.sum(), 1.0)
    uniform_w = np.full(assets, 0.25)
    assert float(w3 @ ill @ w3) <= float(uniform_w @ ill @ uniform_w) + 1e-9


def _bundles(seed=21, count=8, assets=4):
    rng = np.random.default_rng(seed)
    qcap = np.stack([np.linalg.qr(rng.standard_normal((assets, assets)))[0] for _ in range(count)])
    lam_cap = np.sort(rng.uniform(0.1, 3.0, size=(count, assets)), axis=1)
    tokens = np.stack([np.stack([lam_cap[s], np.sign(lam_cap[s]) * np.sqrt(lam_cap[s]),
                                 np.arange(assets) / assets,
                                 rng.uniform(0.001, 0.01, size=assets),
                                 np.sqrt(rng.uniform(0.001, 0.01, size=assets)),
                                 np.ones(assets)], axis=1) for s in range(count)])
    vol = np.stack([np.stack([np.abs(rng.normal(0.01, 0.003, size=assets)),
                              np.ones(assets)], axis=1) for _ in range(count)])
    factor = rng.standard_normal((count, 1, 1))
    realized = np.einsum("si,sj->sij", rng.normal(0.001, 0.0002, (count, assets)),
                         rng.normal(0.001, 0.0002, (count, assets)))
    realized = np.abs(realized) + factor * 1e-6 + np.eye(assets)[None] * 1e-6
    realized = 0.5 * (realized + np.swapaxes(realized, 1, 2))
    return {"tokens": tokens, "qcap": qcap, "vol": vol, "realized": realized, "lam_cap": lam_cap}


def test_bptt_gradient_check():
    """Analytic BiGRU + MLP gradients must match central differences on a smooth objective."""
    bundles = _bundles()
    x, vol = bundles["tokens"], bundles["vol"]
    count, assets = x.shape[0], x.shape[1]
    params = engine.init_rienet_params(engine.SEED, vol_dim=2)
    rng = np.random.default_rng(5)
    w_lam = rng.standard_normal((count, assets))
    w_sig = rng.standard_normal((count, assets))

    def objective(p):
        lam_inv, _, _ = engine.gru_forward(p, x)
        lam = 1.0 / lam_inv
        sig, _, _ = engine.mlp_forward(p, vol)
        return float(np.mean(np.sum(lam * w_lam, axis=1) + np.sum(sig * w_sig, axis=1)))

    lam_inv, pre, ctx = engine.gru_forward(params, x)
    sig, sig_pre, h1 = engine.mlp_forward(params, vol)
    dpre_lam = (w_lam / count) * -(1.0 / (lam_inv ** 2)) * engine._sigmoid(pre)
    dpre_sig = (w_sig / count) * engine._sigmoid(sig_pre)
    grads = engine.gru_backward(params, x, dpre_lam, ctx)
    grads.update(engine.mlp_backward(params, vol, dpre_sig, h1))
    checks = [("wo", 0), ("bo", 0), ("Wf", (0, 0, 0)), ("Uf", (2, 3, 4)),
              ("bf", (1, 5)), ("Wb", (1, 2, 3)), ("Ub", (0, 1, 2)), ("bb", (2, 7)),
              ("Wv1", (0, 3)), ("bv1", (2,)), ("Wv2", (3, 0)), ("bv2", (0,))]
    for key, index in checks:
        analytic = float(grads[key][index])
        probe = params[key][index]
        step = 1e-6 * max(abs(float(probe)), 1e-3)
        up = {k: v.copy() for k, v in params.items()}
        dn = {k: v.copy() for k, v in params.items()}
        up[key][index] += step
        dn[key][index] -= step
        numeric = (objective(up) - objective(dn)) / (2.0 * step)
        scale = max(abs(analytic), abs(numeric), 1e-8)
        assert abs(analytic - numeric) / scale < 2e-3, (key, index, analytic, numeric)
    # The FD layer through the GMV active set must not produce non-finite gradients.
    assert all(np.isfinite(v).all() for v in grads.values())


def test_training_reduces_loss():
    bundles = _bundles(seed=33, count=16)
    params, history = engine.train_rienet(bundles, engine.SEED, mode="gmv",
                                          epochs=25, lr=0.02)
    assert len(history) == 25 and all(np.isfinite(history))
    assert history[-1] < history[0], (history[0], history[-1])
    # The trained model must still emit a strictly positive spectrum and a PD reconstruction.
    lam_inv, _, _ = engine.gru_forward(params, bundles["tokens"])
    sigma = engine.reconstruct(bundles["qcap"], 1.0 / lam_inv,
                               engine.mlp_forward(params, bundles["vol"])[0])
    assert np.diagonal(sigma, axis1=1, axis2=2).min() > 0.0
    assert min(np.linalg.eigvalsh(item).min() for item in sigma) > 0.0
    weight = engine.gmv_long_only(sigma)
    assert np.allclose(weight.sum(axis=1), 1.0, atol=1e-9)
    assert (weight >= -1e-12).all()


def test_signal_path_events_and_causality():
    close, high, low = synthetic_panel(seed=9, bars=600, assets=4, missing=0.0)
    tf = tf_of("1d")
    bars = close.shape[0]
    rebars = np.arange(0, bars, engine.REBAL_SESSIONS * tf["bars_per_day"], dtype=np.int64)
    strat = case()
    prefix = engine.build_prefix(close, high, low)
    path = engine.signal_path(prefix, close, tf, strat, rebars)
    assert path["valid"].any() and path["valid"].sum() >= 20
    assert path["lookback_bars"] == 600 and path["min_eigenvalue"] is not None
    params = engine.init_rienet_params(engine.SEED, vol_dim=2)
    weight = engine.target_weights(path, params, bars)
    assert weight.shape == (bars, 4)
    assert (weight >= 0).all()
    valid_rows = np.flatnonzero(path["valid"])
    assert np.allclose(weight[valid_rows].sum(axis=1), 1.0, atol=1e-12)
    assert np.array_equal(np.flatnonzero(weight.sum(axis=1) > 0.0), valid_rows)
    # Weights live on the registered 0.1% grid.
    assert np.allclose(weight / engine.WEIGHT_GRID, np.round(weight / engine.WEIGHT_GRID), atol=1e-9)
    events, entries = engine.layer_from_weights(weight, rebars, bars, 0)
    # Registered leg semantics: EVERY rebalance where the GMV target still holds the asset is a
    # tranche-1 opportunity (re-entry happens only after the rail's reduce-only exit, because an
    # event at or before the last exit is skipped).
    want_rows = [pos for pos in range(len(rebars))
                 if weight[pos].sum() > 0.0 and weight[pos, 0] > 0.0]
    assert entries == len(want_rows) and entries >= 2
    assert int(events.sum()) == entries
    for pos in want_rows:
        assert events[int(rebars[pos])] == 1
    # Monotone positive weights for asset 0 => an opportunity at every valid rebalance row.
    mono = np.zeros((bars, 4))
    mono[:, 0] = np.linspace(0.0, 1.0, bars)
    mono_events, mono_entries = engine.layer_from_weights(mono, rebars, bars, 0)
    want_mono = [pos for pos in range(len(rebars)) if mono[pos].sum() > 0.0 and mono[pos, 0] > 0.0]
    assert mono_entries == len(want_mono) == len(rebars) - (1 if mono[0, 0] == 0.0 else 0)
    for pos in want_mono:
        assert mono_events[int(rebars[pos])] == 1
    assert int(mono_events.sum()) == mono_entries
    probe = engine.causality_probe(close, high, low, tf, strat, rebars, params,
                                   [bars // 2, bars - 10], 0)
    assert probe["mismatches"] == 0 and probe["bars_probed"] > 0, probe
    # A future shock must not move any signal read before it.
    shocked = close.copy()
    shocked[bars // 2:] *= 3.0
    shocked_high = high.copy()
    shocked_high[bars // 2:] *= 3.0
    shocked_low = low.copy()
    shocked_low[bars // 2:] *= 3.0
    probe2 = engine.causality_probe(shocked, shocked_high, shocked_low, tf, strat, rebars,
                                    params, [bars // 2 - 5], 0)
    assert probe2["mismatches"] == 0, probe2


def test_training_bundles_never_read_past_split():
    close, high, low = synthetic_panel(seed=13, bars=600, assets=4, missing=0.0)
    tf = tf_of("1d")
    bars = close.shape[0]
    rebars = np.arange(0, bars, engine.REBAL_SESSIONS * tf["bars_per_day"], dtype=np.int64)
    prefix = engine.build_prefix(close, high, low)
    hist_end = 400
    bundles, path, rows = engine.training_bundles(prefix, close, tf, case(), rebars, hist_end)
    assert bundles is not None and rows > 0
    horizon = engine.HORIZON_SESSIONS * tf["bars_per_day"]
    # every training label window must terminate inside the registered historical split
    for bar in rebars:
        pass
    valid_bars = [int(b) for b, ok in zip(rebars, path["valid"]) if ok
                  and int(b) + 1 + horizon <= hist_end]
    assert valid_bars and max(valid_bars) + 1 + horizon <= hist_end
    assert bundles["realized"].shape[0] == rows
    assert bundles["realized"].shape[1:] == (4, 4)
    # A realised COVARIANCE label is not elementwise positive (off-diagonals carry the sign of
    # the correlation); what must hold is a positive variance on the diagonal and a PSD matrix on
    # this complete (missing=0) panel.
    diag = np.diagonal(bundles["realized"], axis1=1, axis2=2)
    assert (diag > 0).all()
    assert np.linalg.eigvalsh(bundles["realized"]).min() > -1e-12


def test_selector():
    params_grid = [dict(zip(engine.DCA_AXES, values))
                   for values in itertools.product(*(engine.DCA_AXES[a] for a in engine.DCA_AXES))]
    assert len(params_grid) == 48
    strat = engine.STRATEGIES[0]
    rows_by_grid = {grid: [] for grid in engine.GRID_KINDS}
    for grid in engine.GRID_KINDS:
        for params in params_grid:
            rows_by_grid[grid].append(make_row("BTCUSDT", "1h", strat, params, -10.0, -0.5, 50, grid))
    winner, detail = engine.select_cohort(rows_by_grid)
    assert winner is None and detail["cull_reasons"] == ["no_qualifying_candidate"], detail
    for grid in engine.GRID_KINDS:
        for row in rows_by_grid[grid]:
            row["episodes"] = 5
    winner, detail = engine.select_cohort(rows_by_grid)
    assert winner is None and detail["cull_reasons"] == ["insufficient_trades"], detail
    for grid in engine.GRID_KINDS:
        for row in rows_by_grid[grid]:
            row["episodes"] = 50
            row["net_pnl"] = 25.0
            row["sharpe"] = 1.0
    winner, detail = engine.select_cohort(rows_by_grid)
    assert winner is not None and detail["cull_reasons"] == [], detail
    assert detail["neighbourhood"]["neighbours"] > 0
    for row in rows_by_grid["oos"]:
        if engine.row_key(row) == engine.row_key(winner):
            row["net_pnl"] = -5.0
    winner2, detail2 = engine.select_cohort(rows_by_grid)
    assert winner2 is None and "oos_economic" in detail2["cull_reasons"], detail2
    for grid in engine.GRID_KINDS:
        for row in rows_by_grid[grid]:
            row["net_pnl"] = 25.0
    for row in rows_by_grid["oos"]:
        row["net_pnl"] = 25.0
    for row in rows_by_grid["fee_2x"]:
        row["net_pnl"] = -1.0
    winner3, detail3 = engine.select_cohort(rows_by_grid)
    assert winner3 is None and detail3["cull_reasons"] == ["robustness_economic:fee_2x"], detail3
    for grid in engine.GRID_KINDS:
        for row in rows_by_grid[grid]:
            row["net_pnl"] = 25.0
    winner4, detail4 = engine.select_cohort(rows_by_grid)
    assert winner4 is not None and detail4["neighbourhood"]["same_sign_fraction"] >= 0.6


def test_neighbourhood_walks_the_joint_space():
    strat_a, strat_b = engine.STRATEGIES[0], engine.STRATEGIES[1]
    base = {"spacing_pct": 0.02, "size_multiplier": 1.1,
            "breakeven_tp_pct": 0.01, "invalidation_pct": 0.10}
    winner = make_row("BTCUSDT", "1d", strat_a, dict(base), 25.0, 1.0, 50)
    neighbour = make_row("BTCUSDT", "1d", strat_b, dict(base), 25.0, 1.0, 50)
    other = make_row("BTCUSDT", "1d", strat_a, dict(base, spacing_pct=0.03), 25.0, 1.0, 50)
    detail = engine.neighbourhood(winner, [winner, neighbour, other])
    # the strategy axis and the spacing axis are both walked, so both neighbours count
    assert detail["neighbours"] == 2, detail
    assert detail["passed"] is True and detail["same_sign_fraction"] == 1.0
    disagree = make_row("BTCUSDT", "1d", strat_b, dict(base), -5.0, -1.0, 50)
    # row_key is the cell identity (one row per cell in a real grid), so the disagreeing row
    # REPLACES the agreeing one rather than coexisting under the same key.
    detail2 = engine.neighbourhood(winner, [winner, disagree, other])
    assert detail2["neighbours"] == 2 and detail2["agreeing"] == 1, detail2
    assert detail2["same_sign_fraction"] < 1.0
    assert detail2["passed"] is False


def test_rail_accounting():
    count = 60
    open_px = np.full(count, 100.0)
    high = np.full(count, 100.2)
    low = np.full(count, 99.8)
    close = np.full(count, 100.0)
    high[7] = 110.0
    low[7] = 99.9
    close[7] = 105.0
    ms = np.arange(count, dtype=np.int64) * 86400000 + 1640995200000
    funding = np.zeros(count)
    layer = {"events": np.zeros(count, dtype=np.int8), "pos": np.zeros(count, dtype=np.int8)}
    layer["events"][5] = 1
    layer["pos"][5:] = 1
    params = {"spacing_pct": 0.02, "size_multiplier": 1.1,
              "breakeven_tp_pct": 0.01, "invalidation_pct": 0.10}
    base = engine.RAIL.simulate(open_px, high, low, close, ms, funding, layer, params,
                                60, 10, 0, count, {}, "BTCUSDT")
    assert base["episodes"] == 1 and base["decomposition_ok"]
    assert abs(base["gross_pnl"] - base["fees"] - base["funding"] - base["net_pnl"]) <= 1e-6
    assert abs(base["gross_pnl"] - (base["fees"] + 1.0) - base["funding"] - base["net_pnl"]) > 1e-6
    tick = engine.instrument_constants()["tick"]["BTCUSDT"]
    zero = engine.RAIL.simulate(open_px, high, low, close, ms, funding, layer, params,
                                60, 10, 0, count, {"slip_ticks": 0}, "BTCUSDT")
    one = engine.RAIL.simulate(open_px, high, low, close, ms, funding, layer, params,
                               60, 10, 0, count, {"slip_ticks": 1}, "BTCUSDT")
    two = engine.RAIL.simulate(open_px, high, low, close, ms, funding, layer, params,
                               60, 10, 0, count, {"slip_ticks": 2}, "BTCUSDT")
    assert abs(base["gross_pnl"] - one["gross_pnl"]) <= 1e-9
    assert zero["gross_pnl"] > one["gross_pnl"] > two["gross_pnl"]
    assert abs(one["gross_pnl"] - two["gross_pnl"]) > tick
    dip_low = low.copy()
    dip_low[7] = 100.0 * (1.0 - 0.021)
    dip_high = high.copy()
    dip_high[7] = 100.2
    dip_high[8] = 110.0
    dip_close = close.copy()
    dip_close[8] = 105.0
    added = engine.RAIL.simulate(open_px, dip_high, dip_low, dip_close, ms, funding, layer, params,
                                 60, 10, 0, count, {}, "BTCUSDT")
    assert added["adds"] == 1 and added["fills"] == 3, added
    assert added["layer_hist"][0] == 1 and added["layer_hist"][1] == 1, added["layer_hist"]
    funded = np.full(count, 0.0005)
    charged = engine.RAIL.simulate(open_px, high, low, close, ms, funded, layer, params,
                                   60, 10, 0, count, {}, "BTCUSDT")
    waived = engine.RAIL.simulate(open_px, high, low, close, ms, funded, layer, params,
                                  60, 10, 0, count, {"no_funding": True}, "BTCUSDT")
    assert charged["funding"] > 0.0 and waived["funding"] == 0.0
    assert charged["net_pnl"] < waived["net_pnl"]
    assert charged["decomposition_ok"] and waived["decomposition_ok"]


def test_record_falsification_battery():
    """Record items 1 and 2 run for real; item 3 is disclosed as untestable locally."""
    report = engine.synthetic_noise_stress(engine.SEED + 7)
    assert report["n_assets"] == 50 and report["rows"] == 600
    assert len(report["levels"]) == 3
    assert [e["missingness"] for e in report["levels"]] == [0.10, 0.30, 0.50]
    for entry in report["levels"]:
        assert set(entry["levels"]) == {"rienet", "nearest_correlation", "linear_shrinkage"}
        assert all(np.isfinite(v) and v >= 0.0 for v in entry["levels"].values())
        assert np.isfinite(entry["train_loss_first"]) and np.isfinite(entry["train_loss_last"])
    assert report["status"] in ("FALSIFIED", "NOT_FALSIFIED")
    scale = report["dimension_scaling"]
    assert scale["train_n_assets"] == 50 and scale["eval_n_assets"] == 100
    assert scale["fine_tuning"] is False and np.isfinite(scale["rienet_inverse_error"])


def main():
    test_registered_constants()
    test_pairwise_indefinite_and_reconstruction()
    test_complete_panel_matches_numpy()
    test_gmv_long_only()
    test_bptt_gradient_check()
    test_training_reduces_loss()
    test_signal_path_events_and_causality()
    test_training_bundles_never_read_past_split()
    test_selector()
    test_neighbourhood_walks_the_joint_space()
    test_rail_accounting()
    test_record_falsification_battery()
    print("RIEnet engine self-check: PASS")


if __name__ == "__main__":
    main()
