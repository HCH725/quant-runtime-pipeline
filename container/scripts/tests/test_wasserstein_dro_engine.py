#!/usr/bin/env python3
"""Self-check for the Wasserstein-hyperplane DRO direct-family Qlib runner (contract 16 / body preflight).

Run inside the production image (the spec checks read /data/raw/_meta/CONFIG.json):

    container exec qlib-run /opt/venv/bin/python /scripts/tests/test_wasserstein_dro_engine.py

Every test is a plain assert; any failure exits non-zero.  No production artifact is
written: scratch panels are built in memory and grid CSVs go to a temp directory.
"""
from __future__ import annotations

import csv
import importlib.util
import json
import math
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np

ENGINE_PATH = Path(__file__).resolve().parents[1] / "300_wasserstein_dro_run.py"
_spec = importlib.util.spec_from_file_location("wasserstein_dro_engine", ENGINE_PATH)
assert _spec is not None and _spec.loader is not None, "cannot load %s" % ENGINE_PATH
eng = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(eng)

INST = {"tick": 0.01, "taker_fee": 0.0005}
DCA = {"spacing_pct": 0.02, "size_multiplier": 1.1,
       "breakeven_tp_pct": 0.02, "invalidation_pct": 0.10}
WINNER_CELL = {"spacing_pct": 0.01, "size_multiplier": 1.0,
               "breakeven_tp_pct": 0.01, "invalidation_pct": 0.05}


def make_panel(n=300, start="2024-01-01", kind="varied"):
    """Deterministic synthetic daily panel: OHLCV + open_ms, no NaN, no gaps."""
    ms = np.array([eng.utc_ms(start) + i * eng.MS_DAY for i in range(n)], dtype=np.int64)
    if kind == "flat":
        close = np.full(n, 100.0)
    else:
        rng = np.random.default_rng(20260902)
        close = 100.0 * np.exp(np.cumsum(rng.normal(0.0004, 0.02, n)))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * 1.01
    low = np.minimum(open_, close) * 0.99
    return {"open_ms": ms, "open": open_, "high": high, "low": low,
            "close": close, "volume": np.full(n, 1_000_000.0)}


def make_universe(n=300, start="2024-01-01", seed=4242):
    """Correlated synthetic four-symbol universe with heavy tails and a leverage effect."""
    rng = np.random.default_rng(seed)
    common = rng.standard_t(5, n) / math.sqrt(5.0 / 3.0)
    out = {}
    for i, sym in enumerate(eng.SYMBOLS):
        load = 0.5 + 0.15 * i
        idio = rng.standard_t(5, n) / math.sqrt(5.0 / 3.0)
        shock = 0.016 * (load * common + 0.7 * idio)
        ret = 0.0003 + shock
        close = 100.0 * (i + 1.0) * np.exp(np.cumsum(ret))
        open_ = np.r_[close[0], close[:-1]]
        high = np.maximum(open_, close) * (1.0 + 0.004 + 0.02 * (shock < 0))
        low = np.minimum(open_, close) * (1.0 - 0.004 - 0.02 * (shock > 0))
        out[sym] = {"open_ms": np.array([eng.utc_ms(start) + k * eng.MS_DAY for k in range(n)],
                                        dtype=np.int64),
                    "open": open_, "high": high, "low": low, "close": close,
                    "volume": np.full(n, 1_000_000.0)}
    return out


def flipping_direction(n, step=9):
    d = np.ones(n, dtype=np.int8)
    for i in range(n):
        if (i // step) % 2 == 0 and i % step == step - 1:
            d[i] = 0
    return d


def flat_direction(n):
    return np.ones(n, dtype=np.int8)


def no_funding_events(n):
    return [[] for _ in range(n)]


def base_cost(**over):
    c = {"fee_mult": 1.0, "funding_mult": 1.0, "entry_delay": 0, "slip_ticks": 1,
         "no_funding": False}
    c.update(over)
    return c


def synthetic_daily(n=300):
    uni = make_universe(n)
    carry = {s: np.zeros(n) for s in eng.SYMBOLS}
    return uni, carry, eng.scenario_returns(uni, carry)


def build_case_layer(eps=None, lookback=63, n=300, report=None):
    daily, carry, scen = synthetic_daily(n)
    if eps is None:
        eps = eng.CASES[0]["wasserstein_epsilon"]
    return eng.build_layer(daily, scen, eps, lookback, report=report)


def expect_raises(fn, exc=Exception):
    try:
        fn()
    except exc:
        return
    raise AssertionError("expected %s" % exc.__name__)


# --------------------------------------------------------------------------------------
def test_frozen_domain_registration():
    assert eng.STRATEGY_AXES["wasserstein_epsilon"] == (1.0, 1e-2, 1e-4)
    assert eng.STRATEGY_AXES["lookback_n"] == (63, 252)
    assert [20, 252][0] <= min(eng.STRATEGY_AXES["lookback_n"]) \
        <= max(eng.STRATEGY_AXES["lookback_n"]) <= 252
    assert len(eng.CASES) == 6 and len(eng.DCA_GRID) == 48 and len(eng.GRIDS) == 10
    counts = eng.expected_counts()
    assert counts == {"cohorts": 4, "strategy_cases_per_cohort": 6,
                      "dca_configs_per_cohort": 48, "base_combinations_per_cohort": 288,
                      "case_evaluations_per_grid": 1152, "grid_count": 10,
                      "case_evaluations_total": 11520}
    assert eng.PHASES["historical"] == ("2022-01-01", "2025-09-30")
    assert eng.PHASES["oos"] == ("2025-10-01", "2026-09-11")
    assert eng.PHASES["full"] == ("2022-01-01", "2026-09-11")
    assert (eng.MIN_EPISODES_IS, eng.MIN_EPISODES_OOS, eng.MIN_NEIGHBOUR) == (10, 3, 0.60)
    assert eng.START_EQUITY == 30_000.0 and eng.BASE_QUOTE == 1_000.0
    assert eng.LEVERAGE == 10.0 and eng.LADDER_LEVELS == 12 and eng.MAX_ADD_LEVELS == 10
    assert eng.ETA == 1e-3 and eng.X_MIN_CLIP == -0.99 and eng.REBALANCE_EVERY == 7
    assert eng.SIGNAL_PARAMS["wasserstein_epsilon"] == [1.0, 1e-2, 1e-4]
    assert eng.SIGNAL_PARAMS["lookback_n"] == [63, 252]
    assert [c["label"] for c in eng.CASES] == [
        "%s_n%d" % (eng._eps_label(e), n)
        for e, n in __import__("itertools").product(*eng.STRATEGY_AXES.values())]
    # direct family: no kanban ownership keys in any emitted spec document
    for doc in (eng.run_spec_template(created_at="2026-09-28T00:00:00Z"),
                eng.round_spec_template(source_record_sha256="sha256:00")):
        assert not any(k in doc for k in ("task_id", "kanban_task_id", "kanban_board"))
        assert doc["family_id"] == eng.FAMILY_ID
    print("PASS test_frozen_domain_registration")


def test_spec_validation_and_rejections():
    spec = eng.run_spec_template(created_at="2026-09-28T00:00:00Z")
    spec["round_id"] = eng.FAMILY_ID + "-r1"
    spec["run_id"] = spec["round_id"] + "-u1"
    try:
        eng.validate_spec(spec, script_path=ENGINE_PATH, test_path=Path(__file__))
    except FileNotFoundError:
        print("  (skipped CONFIG part: /data/raw/_meta/CONFIG.json absent on host)")
    # tamper battery: each mutation must be rejected
    def bad(mutate):
        s = json.loads(json.dumps(spec))
        mutate(s)
        expect_raises(lambda: eng.validate_spec(s, script_path=ENGINE_PATH,
                                                test_path=Path(__file__)), ValueError)
    bad(lambda s: s.update(family_id="not-the-family"))
    bad(lambda s: s.update(task_id="t_x"))
    bad(lambda s: s.update(kanban_task_id="t_x"))
    bad(lambda s: s.update(kanban_board="b"))
    bad(lambda s: s["params"].__setitem__(0, {**s["params"][0], "wasserstein_epsilon": 0.5}))
    bad(lambda s: s["params"].pop())
    bad(lambda s: s["dca_domain"].update(spacing_pct=[0.01, 0.02, 0.03, 0.04, 0.05]))
    bad(lambda s: s["dca_domain"].update(base_quote=500.0))
    bad(lambda s: s["dca_domain"].update(base_quote_status="USER_FIXED"))
    bad(lambda s: s["dca_domain"].update(spacing_pct_status="USER_FIXED"))
    bad(lambda s: s["expected"].update(case_evaluations_total=1))
    bad(lambda s: s["signal_constants"].update(rebalance_every_days=1))
    bad(lambda s: s["script"].update(path="/scripts/other.py"))
    bad(lambda s: s["script"].update(sha256="sha256:" + "0" * 64))
    bad(lambda s: s["engine"].update(self_check_sha256="sha256:" + "0" * 64))
    bad(lambda s: s.update(round_id=eng.FAMILY_ID + "-r0"))
    bad(lambda s: s.update(run_id=eng.FAMILY_ID + "-r1-u0"))
    # kernel: an untampered spec validates (inside the container where CONFIG exists)
    try:
        eng.validate_spec(spec, script_path=ENGINE_PATH, test_path=Path(__file__))
    except FileNotFoundError:
        pass
    print("PASS test_spec_validation_and_rejections")


def test_record_constants_and_falsification_battery():
    assert set(eng.FALSIFICATION_REGISTRY) == {
        "radius_sweep", "support_boundary_misspecification",
        "turnover_slippage_hurdle", "rejection_threshold"}
    assert len(eng.FALSIFICATION) == 4
    assert eng.SWEEP_EPS == (1e-5, 1e-4, 1e-3, 5e-3, 1e-2, 5e-2, 1e-1, 1e0, 1e1)
    assert eng.INTERMEDIATE_EPS == (5e-3, 5e-2)
    assert eng.SAA_EPS == 0.0
    assert (eng.ITEM3_TURNOVER_THRESHOLD, eng.ITEM3_SHARPE_THRESHOLD) == (0.40, 0.6)
    assert (eng.ITEM4_SHARPE_THRESHOLD, eng.ITEM4_MAXDD_THRESHOLD) == (0.5, 30.0)
    assert eng._aggregate({}) == "INDETERMINATE"
    assert eng._aggregate({"a": {"falsified": True}, "b": {"falsified": False}}) == "FALSIFIED"
    assert eng._aggregate({"a": {"falsified": False}}) == "NOT_FALSIFIED"
    assert eng._aggregate({"a": {"falsified": None}}) == "INDETERMINATE"
    print("PASS test_record_constants_and_falsification_battery")


def test_hyperplane_certificate_and_mesh():
    for lo, hi in ((-0.05, 0.05), (-0.30, 0.25), (-0.45, 0.40), (-0.50, 0.40), (0.0, 0.0)):
        mesh = eng.hyperplane_mesh(lo, hi)
        assert mesh["m"] >= 2
        if hi > lo:
            assert mesh["dy"] <= math.sqrt(8 * eng.ETA / mesh["l_f"]) + 1e-12
        dense = np.linspace(lo, hi, 1025) if hi > lo else np.array([lo])
        gaps = eng.majorant_gap(dense, mesh)
        assert float(np.max(gaps)) <= eng.ETA + 1e-12
        assert float(np.min(gaps)) >= -1e-12  # majorant is an upper bound everywhere
    # fail closed: support violating x_min > -1, empty range, and the hyperplane cap
    expect_raises(lambda: eng.hyperplane_mesh(-1.0, 0.1), RuntimeError)
    expect_raises(lambda: eng.hyperplane_mesh(0.2, 0.1), RuntimeError)
    expect_raises(lambda: eng.hyperplane_mesh(-0.99, 0.10), RuntimeError)  # M > cap 128
    # the certificate is not vacuous: interior gap is strictly positive somewhere
    mesh = eng.hyperplane_mesh(-0.3, 0.25)
    interior = np.linspace(-0.28, 0.23, 211)
    assert float(np.max(eng.majorant_gap(interior, mesh))) > 1e-6
    print("PASS test_hyperplane_certificate_and_mesh")


def test_wasserstein_lp_anchors():
    rng = np.random.default_rng(7)
    n_obs = 60
    mu = np.array([0.001, 0.0005, -0.0002, 0.0015])
    X = rng.normal(0.0, 0.02, size=(n_obs, 4)) + mu
    xmin = np.maximum(X.min(axis=0), eng.X_MIN_CLIP)
    eps_bar = float((X.max(axis=0) - xmin).sum())
    # feasibility + determinism
    s1 = eng.solve_wasserstein(X, 1e-2)
    s2 = eng.solve_wasserstein(X, 1e-2)
    assert np.allclose(s1["w"], s2["w"], atol=1e-9)
    assert abs(s1["w"].sum() - 1.0) <= 1e-7 and s1["w"].min() >= -1e-9 and s1["lam"] >= 0
    # eps = 0 is exactly the SAA epigraph through the same surrogate
    saa = eng.solve_saa_epigraph(X)
    lp0 = eng.solve_wasserstein(X, 0.0)
    assert abs(lp0["objective"] - saa["objective"]) <= 1e-6, (lp0["objective"], saa["objective"])
    # eps >= eps_bar collapses to the maximin closed form (record Theorem 3.13)
    big = eng.solve_wasserstein(X, 1.5 * eps_bar)
    closed, mask = eng.maximin_weights(xmin)
    assert float(big["w"][mask].sum()) >= 1.0 - 1e-6
    if int(mask.sum()) == 1:
        assert np.allclose(big["w"], closed, atol=1e-6)
    # worst-case value is nonincreasing in eps (larger ambiguity, weaker guarantee)
    vals = [eng.solve_wasserstein(X, e)["objective"]
            for e in (0.0, 1e-4, 1e-2, 1e-1, 1.0, eps_bar)]
    assert all(vals[i] >= vals[i + 1] - 1e-9 for i in range(len(vals) - 1)), vals
    # box containment fails closed (a box raised above the data excludes scenarios)
    expect_raises(lambda: eng.solve_wasserstein(X, 1e-2, xmin=X.min(axis=0) + 0.5),
                  RuntimeError)
    # non-finite / degenerate scenarios fail closed
    bad = X.copy()
    bad[0, 0] = np.nan
    expect_raises(lambda: eng.solve_wasserstein(bad, 1e-2), RuntimeError)
    expect_raises(lambda: eng.solve_wasserstein(np.zeros((1, 4)), 1e-2), RuntimeError)
    print("PASS test_wasserstein_lp_anchors")


def test_signal_layer_is_point_in_time():
    n = 300
    daily, carry, scen = synthetic_daily(n)
    rep = {}
    layer = eng.build_layer(daily, scen, 1e-2, 63, report=rep)
    epochs = layer["epochs"]
    assert epochs, "no epochs on a 300-bar panel"
    assert all(t % eng.REBALANCE_EVERY == 0 for t in epochs)
    assert all(63 <= t <= n - 2 for t in epochs)
    assert rep["max_data_index_used"] == epochs[-1] <= n - 2
    assert rep["epochs"] == len(epochs) and rep["worst_sum_w_deviation"] <= 1e-7
    for s in eng.SYMBOLS:
        d = layer["directions"][s]
        assert d.dtype == np.int8 and set(np.unique(d)).issubset({0, 1})
        assert int(d[:epochs[0]].max(initial=0)) == 0  # warm-up stays flat
    # suffix perturbation: decisions at or before the cut must not move
    k = 274
    daily2, carry2, scen2 = synthetic_daily(n)
    for s in eng.SYMBOLS:
        daily2[s]["close"][k + 1:] *= 1.7
        daily2[s]["high"][k + 1:] *= 1.7
        daily2[s]["low"][k + 1:] *= 0.5
    scen2 = eng.scenario_returns(daily2, carry2)
    layer2 = eng.build_layer(daily2, scen2, 1e-2, 63)
    for s in eng.SYMBOLS:
        assert np.array_equal(layer["directions"][s][:k + 1], layer2["directions"][s][:k + 1]), s
    # scenario construction: price return minus carry, no look-ahead in the matrix itself
    raw = daily["BTCUSDT"]["close"]
    expected0 = raw[5] / raw[4] - 1.0 - carry["BTCUSDT"][5]
    assert abs(scen["BTCUSDT"][5] - expected0) < 1e-12
    print("PASS test_signal_layer_is_point_in_time")


def test_ladder_accounting_gross_net_and_controls():
    panel = make_panel(300)
    n = len(panel["close"])
    d = flipping_direction(n)
    free = eng.simulate(panel, no_funding_events(n), d, DCA, 0, n,
                        base_cost(fee_override=0.0, slip_ticks=0), INST)
    costly = eng.simulate(panel, no_funding_events(n), d, DCA, 0, n, base_cost(), INST)
    for m in (free, costly):
        assert m["decomposition_ok"] is True
        assert abs(m["gross_pnl"] - m["fees"] - m["funding"] - m["net_pnl"]) < 1e-3
        assert abs(m["net_pnl"] - (m["ending_equity"] - eng.START_EQUITY)) < 1e-3
        assert m["episodes"] >= 10 and m["layer_hist"][0] == m["episodes"]
        assert m["max_effective_leverage"] > 1.0 and m["capital_utilization"] > 0.0
    assert costly["net_pnl"] < free["net_pnl"]  # fees/slippage must bind
    assert costly["fees"] > free["fees"] == 0.0
    flat = eng.simulate(panel, no_funding_events(n), np.ones(n, dtype=np.int8) * 0,
                        DCA, 0, n, base_cost(), INST)
    assert flat["episodes"] == 0 and flat["net_pnl"] == 0.0
    # funding: official settlement while held changes net but never gross decomposition
    funded_events = [[] for _ in range(n)]
    for i in range(5, n):
        funded_events[i] = [(int(panel["open_ms"][i]) + 3_600_000, 0.0005)]
    with_f = eng.simulate(panel, funded_events, d, DCA, 0, n, base_cost(), INST)
    without_f = eng.simulate(panel, funded_events, d, DCA, 0, n,
                             base_cost(no_funding=True), INST)
    assert with_f["funding"] > 0 and with_f["decomposition_ok"] and without_f["funding"] == 0.0
    assert abs(with_f["net_pnl"] - without_f["net_pnl"]) > 1e-9
    print("PASS test_ladder_accounting_gross_net_and_controls")


def test_registered_stress_grids_all_move_net():
    panel = make_panel(300)
    n = len(panel["close"])
    d = flipping_direction(n)
    events = [[] for _ in range(n)]
    for i in range(5, n):
        events[i] = [(int(panel["open_ms"][i]) + 3_600_000, 0.0006)]
    base = eng.simulate(panel, events, d, DCA, 0, n, eng.cost_for("historical"), INST)
    assert base["episodes"] > 0
    for grid in ("fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks",
                 "cost_attrition_40bps", "no_funding"):
        got = eng.simulate(panel, events, d, DCA, 0, n, eng.cost_for(grid), INST)
        assert abs(got["net_pnl"] - base["net_pnl"]) > 1e-9, grid
    fee2 = eng.simulate(panel, events, d, DCA, 0, n, eng.cost_for("fee_2x"), INST)
    assert fee2["fees"] > base["fees"] and fee2["net_pnl"] < base["net_pnl"]
    no_f = eng.simulate(panel, events, d, DCA, 0, n, eng.cost_for("no_funding"), INST)
    assert no_f["funding"] == 0.0 and no_f["net_pnl"] > base["net_pnl"]
    print("PASS test_registered_stress_grids_all_move_net")


def make_row(symbol, case, dca, grid, net_pnl, sharpe, episodes, **over):
    m = eng.metric_block(eng.empty_metric(0, 100))
    m.update(net_pnl=net_pnl, sharpe=sharpe, episodes=episodes,
             ending_equity=eng.START_EQUITY + net_pnl)
    row = {"symbol": symbol, "timeframe": eng.TIMEFRAME,
           "wasserstein_epsilon": case["wasserstein_epsilon"],
           "lookback_n": case["lookback_n"], "case_label": case["label"],
           **dca, "grid": grid, **m, "decomposition_ok": True}
    row.update(over)
    return row


def all_rows(symbol="BTCUSDT", tune=lambda row: None):
    rows = {g: [] for g in eng.GRIDS}
    for case in eng.CASES:
        for dca in eng.DCA_GRID:
            for grid in eng.GRIDS:
                r = make_row(symbol, case, dca, grid, net_pnl=100.0, sharpe=1.0,
                             episodes=40)
                tune(r)
                rows[grid].append(r)
    return rows


def test_selector_disposition_and_neighbourhood():
    # 1. clean survivor: historical winner passes all five survivor requirements
    rows = all_rows()
    winner, info = eng.select_cohort(rows)
    assert winner is not None and info["cull_reasons"] == []
    assert info["winner_source_grid"] == "historical"
    assert info["neighbourhood"]["neighbours"] == 6
    assert info["neighbourhood"]["passed"] and info["neighbourhood"]["same_sign_fraction"] == 1.0
    # 2. tie-break follows the registered index order (eps 1.0 index 0 wins ties)
    def zero(r):
        r.update(net_pnl=1.0, sharpe=1.0)
    winner, _ = eng.select_cohort(all_rows(tune=zero))
    assert winner["wasserstein_epsilon"] == 1.0 and winner["lookback_n"] == 63
    assert winner["spacing_pct"] == 0.01 and winner["invalidation_pct"] == 0.05
    # 3. insufficient_trades
    _, info = eng.select_cohort(all_rows(tune=lambda r: r.update(episodes=3)))
    assert info["cull_reasons"] == ["insufficient_trades"]
    # 4. no_qualifying_candidate
    _, info = eng.select_cohort(all_rows(tune=lambda r: r.update(net_pnl=-1.0)))
    assert info["cull_reasons"] == ["no_qualifying_candidate"]
    # 5. oos_economic / full_economic / robustness / neighbourhood on the winner cell
    def oos_bad(r):
        if r["grid"] == "oos":
            r.update(net_pnl=-5.0, sharpe=-0.1)
    _, info = eng.select_cohort(all_rows(tune=oos_bad))
    assert info["cull_reasons"] == ["oos_economic"]

    def full_bad(r):
        if r["grid"] == "full":
            r.update(net_pnl=-5.0)
    _, info = eng.select_cohort(all_rows(tune=full_bad))
    assert info["cull_reasons"] == ["full_economic"]

    def robust_bad(r):
        if r["grid"] == "fee_2x":
            r.update(net_pnl=-5.0)
        if r["grid"] == "slippage_2ticks":
            r.update(net_pnl=-7.0)
    _, info = eng.select_cohort(all_rows(tune=robust_bad))
    assert info["cull_reasons"] == ["robustness_economic:fee_2x,slippage_2ticks"]

    def neigh_bad(r):
        if r["grid"] == "historical" and (r["wasserstein_epsilon"], r["lookback_n"],
                                           r["spacing_pct"], r["size_multiplier"],
                                           r["breakeven_tp_pct"], r["invalidation_pct"]) \
                != (1.0, 63, 0.01, 1.0, 0.01, 0.05):
            r.update(net_pnl=-1.0, sharpe=1.0)
    winner, info = eng.select_cohort(all_rows(tune=neigh_bad))
    assert winner is None and info["cull_reasons"] == ["parameter_neighbourhood"]
    # 6. the selector refuses non-historical rows and the neighbourhood guard
    mixed = all_rows()
    mixed["historical"] = mixed["historical"][:-1] + [mixed["oos"][0]]  # oos row leaks into hist
    expect_raises(lambda: eng.select_cohort(mixed), ValueError)
    winner_rows = all_rows()
    expect_raises(lambda: eng.neighbourhood(winner_rows["historical"][0],
                                             winner_rows["oos"]), ValueError)
    print("PASS test_selector_disposition_and_neighbourhood")


def test_grid_csv_contract_and_portfolio_helpers():
    cols = ["symbol", "timeframe", "wasserstein_epsilon", "lookback_n", "case_label",
            *eng.DCA_AXES, "grid", *eng.metric_block(eng.empty_metric(0, 0)),
            "decomposition_ok"]
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "grid_historical.csv"
        eng.write_grid(p, all_rows()["historical"])
        with open(p, newline="", encoding="utf-8") as fh:
            reader = csv.reader(fh)
            header = next(reader)
            body = list(reader)
        assert header == cols
        assert len(body) == 6 * 48
        assert len(set(map(tuple, body))) == len(body)  # unique registered cells
    # portfolio helpers: held weights are point-in-time, cost bites, EW bookkeeps
    daily, carry, scen = synthetic_daily(300)
    layer = eng.build_layer(daily, scen, 1e-2, 63)
    n = 300
    W = eng.held_weights(layer, n)
    assert np.allclose(W.sum(axis=1)[layer["epochs"][0] + 1:], 1.0, atol=1e-9)
    assert np.allclose(W[:layer["epochs"][0]], 0.0)          # nothing before the first fill
    first = layer["epochs"][0]
    assert np.allclose(W[first + 1], layer["weights"][first])  # epoch decision trades next bar
    net_rets = eng.scenario_returns(daily, carry)
    mat = np.vstack([net_rets[s] for s in eng.SYMBOLS])
    free, turn = eng.layer_portfolio_returns(layer, mat, 0.0, start=first)
    paid, _ = eng.layer_portfolio_returns(layer, mat, 0.001, start=first)
    assert float(np.sum(free - paid)) > 0.0                   # turnover cost is not a no-op
    assert turn.shape == free.shape
    ew = eng.buy_and_hold_ew(mat, start=first)
    assert abs(float(np.sum(ew)) - float(ew.sum())) < 1e-12
    assert eng.sharpe_from_returns(np.array([0.01, -0.005, 0.002])) != 0.0
    eq = np.array([100.0, 110.0, 99.0, 120.0])
    assert eng.max_drawdown_pct(eq) > 0.0
    print("PASS test_grid_csv_contract_and_portfolio_helpers")


def test_falsification_battery_end_to_end():
    n = 294
    daily, carry, scen = synthetic_daily(n)
    layers = {}
    for lookback in eng.STRATEGY_AXES["lookback_n"]:
        for eps in (eng.SAA_EPS,) + eng.SWEEP_EPS:
            layers[(eps, lookback)] = eng.build_layer(daily, scen, eps, lookback)
    mat = np.vstack([eng.scenario_returns(daily, carry)[s] for s in eng.SYMBOLS])
    windows = {"historical": (0, 200), "oos": (200, n), "full": (0, n)}
    report = eng.falsification_report(layers, layers, daily, mat, windows,
                                      {s: no_funding_events(n) for s in eng.SYMBOLS})
    assert set(report["items"]) == set(eng.FALSIFICATION_REGISTRY)
    assert report["battery_status"] in ("FAIL", "NOT_FALSIFIED",
                                        "NOT_FALSIFIED_WITH_INDETERMINATE")
    assert sorted(report["falsification_hits"]) == sorted(
        k for k, v in report["items"].items() if v["status"] == "FALSIFIED")
    for item, body in report["items"].items():
        assert body["registered_rule"] == eng.FALSIFICATION_REGISTRY[item]
        assert body["status"] in ("FALSIFIED", "NOT_FALSIFIED", "INDETERMINATE")
    # radius sweep records every registered epsilon for both lookbacks
    for key, case in report["items"]["radius_sweep"]["cases"].items():
        assert set(case["oos_sweep"]) == {"%g" % e for e in (0.0,) + eng.SWEEP_EPS}
    print("PASS test_falsification_battery_end_to_end")


def test_funding_official_only_filter():
    td = Path(tempfile.mkdtemp())
    try:
        root = td
        for sym in eng.SYMBOLS:
            d = root / "binance/usdm/funding" / sym
            d.mkdir(parents=True, exist_ok=True)
            rows = [
                {"symbol": sym, "funding_time_ms": eng.utc_ms("2024-03-01") + 8 * 3600 * 1000,
                 "funding_rate": 0.0001, "mark_price": 100.0, "truth_status": "official"},
                {"symbol": sym, "funding_time_ms": eng.utc_ms("2024-03-01") + 16 * 3600 * 1000,
                 "funding_rate": 0.0002, "mark_price": 100.0, "truth_status": "official"},
                {"symbol": sym, "funding_time_ms": eng.utc_ms("2024-03-02"),
                 "funding_rate": 0.9, "mark_price": 100.0, "truth_status": "modeled"},
            ]
            import gzip as _gz
            with _gz.open(d / ("%s-funding.jsonl.gz" % sym), "wt", encoding="utf-8") as fh:
                for r in rows:
                    fh.write(json.dumps(r) + "\n")
        open_ms = np.array([eng.utc_ms("2024-03-01") + i * eng.MS_DAY for i in range(5)],
                           dtype=np.int64)
        events, rep = eng.load_funding("BTCUSDT", open_ms, open_ms[-1], root=str(root))
        assert rep["official"] == 2 and rep["modeled_ignored"] == 1
        assert rep["charged_events"] == rep["official"]
        assert rep["missing_intervals_are_zero_not_modeled"] is True
        carry = eng.load_funding_carry("BTCUSDT", open_ms, open_ms[-1], root=str(root))
        assert abs(carry[0] - 0.0003) < 1e-12 and np.all(carry[1:] == 0.0)
        assert (carry[0] != 0.0) and bool(events[0])
        assert not events[1] and carry[1] == 0.0
    finally:
        shutil.rmtree(td, ignore_errors=True)
    print("PASS test_funding_official_only_filter")


def test_round_spec_parameter_contract_when_present():
    pc = eng.parameter_contract()
    assert [a["name"] for a in pc["research_axes_ordered"]] == \
        ["wasserstein_epsilon", "lookback_n", *eng.DCA_AXES]
    assert pc["row_fields"] == ["wasserstein_epsilon", "lookback_n", *eng.DCA_AXES]
    assert pc["strategy_param_fields"] == ["wasserstein_epsilon", "lookback_n"]
    assert pc["dca_param_fields"] == list(eng.DCA_AXES)
    assert pc["domain_cardinality"] == {"strategy": 6, "dca": 48, "per_cohort": 288}
    assert pc["canonical_recipe"]["sort_keys"] is True
    for axis_ in pc["research_axes_ordered"]:
        assert axis_["kind"] == "atomic" and len(axis_["registered_values"]) >= 2
        assert axis_["row_fields"] == [axis_["name"]]
    # once the immutable round-spec is frozen on disk, verify it matches this kernel
    path = Path("/results") / eng.FAMILY_ID / "rounds" / (eng.FAMILY_ID + "-r1") / "round-spec.json"
    if path.is_file():
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert doc["parameter_contract"] == pc, "frozen round-spec contract drifted"
        assert doc["family_id"] == eng.FAMILY_ID
        assert not any(k in doc for k in ("task_id", "kanban_task_id", "kanban_board"))
        print("  (frozen round-spec present: parameter_contract verified)")
    else:
        print("  (round-spec not frozen yet: kernel-only check)")
    print("PASS test_round_spec_parameter_contract_when_present")


TESTS = [
    test_frozen_domain_registration,
    test_spec_validation_and_rejections,
    test_record_constants_and_falsification_battery,
    test_hyperplane_certificate_and_mesh,
    test_wasserstein_lp_anchors,
    test_signal_layer_is_point_in_time,
    test_ladder_accounting_gross_net_and_controls,
    test_registered_stress_grids_all_move_net,
    test_selector_disposition_and_neighbourhood,
    test_grid_csv_contract_and_portfolio_helpers,
    test_falsification_battery_end_to_end,
    test_funding_official_only_filter,
    test_round_spec_parameter_contract_when_present,
]


def main():
    print("engine:", ENGINE_PATH)
    failures = []
    for i, test in enumerate(TESTS, 1):
        try:
            test()
        except Exception as exc:  # noqa: BLE001
            failures.append((test.__name__, "%s: %s" % (type(exc).__name__, exc)))
            print("FAIL [%d/%d] %s: %s: %s" % (i, len(TESTS), test.__name__,
                                                type(exc).__name__, exc))
    print("passed %d/%d" % (len(TESTS) - len(failures), len(TESTS)))
    if failures:
        for name, msg in failures:
            print("  - %s: %s" % (name, msg))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
