#!/usr/bin/env python3
"""Self-check for the Bitcoin rolling FPCA hourly-direction direct-family Qlib runner.

Run inside the production image (validate_spec reads /data/raw/_meta/CONFIG.json):

    container exec qlib-run /opt/venv/bin/python /scripts/tests/test_fpca_hourly_engine.py

Every check is a plain assert; any failure exits non-zero.  No production artifact is
written: panels are built in memory and the grid CSV goes to a temp directory.
"""
from __future__ import annotations

import importlib.util
import json
import math
import sys
import tempfile
from pathlib import Path

import numpy as np

ENGINE_PATH = Path(__file__).resolve().parents[1] / "310_fpca_hourly_run.py"
_spec = importlib.util.spec_from_file_location("fpca_hourly_engine", ENGINE_PATH)
assert _spec is not None and _spec.loader is not None, "cannot load %s" % ENGINE_PATH
eng = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(eng)

INST = {"tick": 0.10, "taker_fee": 0.0005}
DCA0 = dict(spacing_pct=0.02, size_multiplier=1.0,
            breakeven_tp_pct=0.02, invalidation_pct=0.05)
MS_HOUR = eng.MS_HOUR
EPOCH = eng.utc_ms("2024-01-01")
CHECKS = []


def ok(name):
    CHECKS.append(name)
    print("ok  %s" % name)


def synthetic_panel(n=420, seed=11, drift=0.0, constant=None):
    """Contiguous hourly OHLCV panel; optional constant price (round-trip arithmetic)."""
    rng = np.random.default_rng(seed)
    if constant is None:
        close = 100.0 * np.exp(np.cumsum(rng.normal(drift, 0.005, n)))
        open_ = np.r_[100.0, close[:-1]]
    else:
        close = np.full(n, float(constant))
        open_ = np.full(n, float(constant))
    high = np.maximum(open_, close) * 1.0005
    low = np.minimum(open_, close) * 0.9995
    return {"open_ms": EPOCH + np.arange(n, dtype=np.int64) * MS_HOUR,
            "open": open_.astype(np.float64), "high": high.astype(np.float64),
            "low": low.astype(np.float64), "close": close.astype(np.float64),
            "volume": np.ones(n)}


def all_rows(spec_rows):
    """Full registered grid: 9 cases x 48 DCA x 10 grids, with tunable economics."""
    rows = {g: [] for g in eng.GRIDS}
    for case in eng.CASES:
        for dca in eng.DCA_GRID:
            for grid in eng.GRIDS:
                m = eng.empty_metric(0, 0)
                m.update(episodes=50, gross_pnl=10.0, fees=2.0, funding=0.0,
                         net_pnl=spec_rows(grid, case, dca),
                         sharpe=spec_rows(grid, case, dca) / 100.0,
                         ending_equity=eng.START_EQUITY + spec_rows(grid, case, dca))
                rows[grid].append({
                    "symbol": eng.SYMBOLS[0], "timeframe": eng.TIMEFRAME,
                    "window_functions": case["window_functions"],
                    "fpca_dim_j": case["fpca_dim_j"], "case_label": case["label"],
                    **dca, "grid": grid, **eng.metric_block(m), "decomposition_ok": True})
    return rows


# --------------------------------------------------------------------------- #
def test_registered_domain():
    assert eng.expected_counts() == {
        "cohorts": 1, "strategy_cases_per_cohort": 9, "dca_configs_per_cohort": 48,
        "base_combinations_per_cohort": 432, "case_evaluations_per_grid": 432,
        "grid_count": 10, "case_evaluations_total": 4320}
    assert len(eng.CASES) == 9 and len(eng.DCA_GRID) == 48 and len(eng.GRIDS) == 10
    assert list(eng.STRATEGY_AXES) == ["window_functions", "fpca_dim_j"]
    assert [list(v) for v in eng.STRATEGY_AXES.values()] == [[90, 100, 110], [2, 4, 6]]
    assert [list(v) for v in eng.DCA_AXES.values()] == [
        [0.01, 0.02, 0.03, 0.04], [1.0, 1.1], [0.01, 0.02, 0.03], [0.05, 0.10]]
    assert eng.BASE_QUOTE == 1000.0 and eng.START_EQUITY == 30_000.0
    assert eng.MAX_ADD_LEVELS == 10 and eng.LADDER_LEVELS == 12
    keys = {eng.cell_key(r) for r in
            [{"symbol": "BTCUSDT", "timeframe": "1h", **c, **d}
             for c in eng.CASES for d in eng.DCA_GRID]}
    assert len(keys) == 432, len(keys)
    ok("registered_domain_9x48x10")


def test_spec_identity_and_direct_family():
    spec = eng.run_spec_template(created_at="2026-09-28T00:00:00Z")
    spec["round_id"] = eng.FAMILY_ID + "-r1"
    spec["run_id"] = spec["round_id"] + "-u1"
    for doc in (spec, eng.round_spec_template()):
        for k in ("task_id", "kanban_task_id", "kanban_board", "board_id", "card_id"):
            assert k not in doc, k
        assert doc["ownership_mode"] == "direct_hermes"
        assert eng.FAMILY_ID in json.dumps(doc)
    eng.validate_spec(spec)                 # also re-checks the canonical catalog
    assert spec["expected"] == eng.expected_counts()
    assert len(spec["falsification"]) == 7
    ok("spec_identity_direct_family_no_kanban")


def test_parameter_contract():
    c = eng.parameter_contract()
    assert c["parameter_contract_version"] == 1
    assert c["family_id"] == eng.FAMILY_ID
    assert c["row_fields"] == list(eng.STRATEGY_AXES) + list(eng.DCA_AXES)
    assert c["strategy_param_fields"] == list(eng.STRATEGY_AXES)
    assert c["dca_param_fields"] == list(eng.DCA_AXES)
    assert c["row_match_recipe"]["keys"] == ["symbol", "timeframe"] + c["row_fields"]
    assert c["domain_cardinality"] == {"strategy": 9, "dca": 48, "per_cohort": 432}
    assert len(c["research_axes_ordered"]) == 6
    assert {a["name"] for a in c["research_axes_ordered"]} == set(c["row_fields"])
    rs = eng.round_spec_template()
    assert rs["parameter_contract"] == c
    assert rs["dca_domain"]["base_quote_status"] == "PROJECT_PRE_REGISTERED_CONSTANT"
    for axis in eng.DCA_AXES:
        assert rs["dca_domain"][axis + "_status"] == "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN"
    ok("parameter_contract_v1_partition")


def test_falsification_registration():
    assert len(eng.FALSIFICATION) == 7
    assert len(eng.FALSIFICATION_REGISTRY) == 7
    assert len(set(eng.FALSIFICATION_REGISTRY)) == 7
    texts = "\n".join(eng.FALSIFICATION)
    for needle in ("<= 50%", "record item 1", "record item 2", "5% level",
                   "record item 3", "mean net return per position is <= 0",
                   "record item 5", "k = 1, 2, 4, 8", "record item 6",
                   "record item 7", "single venue"):
        assert needle in texts, needle
    assert eng.HORIZONS_DIAGNOSTIC == (1, 2, 4, 8)
    assert eng.REFERENCE_CASE == {"window_functions": 100, "fpca_dim_j": 4}
    assert (eng.FUNCTION_LENGTH, eng.HORIZON, eng.LASSO_LAMBDA, eng.CD_ITERS) == (24, 1, 0.01, 50)
    ok("falsification_7_items_registered")


def test_forecast_constant_series():
    M, W = 400, 40
    for sign in (1.0, -1.0):
        ret = np.full(M, 0.001 * sign)
        fc = eng.forecast_series(ret, W, js=(2, 4, 6))[4]
        assert np.isnan(fc).sum() == W + eng.HORIZON + eng.FUNCTION_LENGTH - 1
        first = int(np.flatnonzero(np.isfinite(fc))[0])
        assert first == W + eng.HORIZON + eng.FUNCTION_LENGTH - 1
        assert np.allclose(fc[first:], 0.001 * sign), "mean path must reproduce the level"
    ok("forecast_constant_series_level_and_bounds")


def test_forecast_point_in_time():
    rng = np.random.default_rng(20260903)
    ret = rng.normal(0.0, 0.004, 1200)
    W = 60
    fc = eng.forecast_series(ret, W, js=(3,))[3]
    first = int(np.flatnonzero(np.isfinite(fc))[0])
    assert first == W + eng.HORIZON + eng.FUNCTION_LENGTH - 1
    assert int(np.isfinite(fc).sum()) == len(ret) - W - eng.HORIZON - eng.FUNCTION_LENGTH + 1
    assert np.isfinite(fc[first:]).all() and np.isnan(fc[:first]).all()
    pit = eng.pit_sample_recheck(ret, W, 3, fc)
    assert pit["checked"] >= 3 and pit["failed_samples"] == 0, pit
    ok("forecast_point_in_time_suffix_recheck")


def test_forecast_scale_equivariance():
    rng = np.random.default_rng(5)
    ret = rng.normal(0.0, 0.004, 600)
    a = eng.forecast_series(ret, 50, js=(4,))[4]
    b = eng.forecast_series(ret * 2.0, 50, js=(4,))[4]
    m = np.isfinite(a) & np.isfinite(b)
    assert m.sum() > 0
    assert np.allclose(b[m], 2.0 * a[m], rtol=1e-6, atol=1e-12)
    ok("forecast_scale_equivariance")


def test_direction_rule():
    fc = np.array([np.nan, -0.001, 0.0, 0.002, -0.0])
    d = eng.direction_of(fc)
    assert list(d) == [0, -1, 0, 1, 0]
    assert d.dtype == np.int8
    ok("direction_rule_sign_and_flat")


def test_baselines_and_controls_no_lookahead():
    rng = np.random.default_rng(3)
    M, W = 500, 48
    ret = rng.normal(0.0, 0.004, M)
    hours = rng.integers(0, 24, M).astype(np.int8)
    base = eng.baseline_series(ret, W, hours)
    ctrl = eng.control_series(ret, W, hours)
    assert set(base) == {"unconditional_sign", "ar1_random_walk", "momentum_last_hour",
                         "reversal_last_hour", "utc_hour_seasonality"}
    assert set(ctrl) == {"raw_lagged_returns_ols", "named_controls_ols"}
    lo = W + eng.FUNCTION_LENGTH
    for series in list(base.values()) + list(ctrl.values()):
        assert series[:lo - 1].tolist() == [0] * (lo - 1)          # nothing decided early
        assert np.isin(series, (-1, 0, 1)).all()
        # suffix perturbation: the sign at n must not depend on ret[n:]
        for n in (lo, M // 2, M - 1):
            probe = ret.copy()
            probe[n:] = probe[n:] * -3.1 + 0.07
            alt = {k: v for k, v in
                   {**eng.baseline_series(probe, W, hours),
                    **eng.control_series(probe, W, hours)}.items()}
            assert alt[ident(series, base, ctrl)][n] == series[n], n
    assert np.array_equal(base["momentum_last_hour"], -base["reversal_last_hour"])
    ok("baselines_controls_point_in_time")


def ident(series, base, ctrl):
    for d in (base, ctrl):
        for k, v in d.items():
            if v is series:
                return k
    raise AssertionError("unknown series")


def test_simulate_adverse_tick_roundtrip():
    panel = synthetic_panel(n=60, constant=100.0)
    for direction_value in (1, -1):
        direction = np.full(len(panel["open"]), direction_value, dtype=np.int8)
        m = eng.simulate(panel, [[] for _ in panel["open_ms"]], direction, DCA0,
                         1, 1 + 40, {"slip_ticks": 1},
                         {"tick": 0.10, "taker_fee": 0.0})          # fees off: pure ticks
        qty = eng.BASE_QUOTE * eng.LEVERAGE / (100.0 + 0.10 * direction_value)
        expected = 40 * (-2.0 * 0.10 * abs(qty))
        assert m["episodes"] == 40
        assert math.isclose(m["net_pnl"], expected, rel_tol=1e-9), (m["net_pnl"], expected)
        assert m["fees"] == 0.0 and m["funding"] == 0.0
        assert m["decomposition_ok"] is True
        assert m["open_at_end"] == 0
    ok("simulate_adverse_tick_both_sides")


def test_simulate_episode_accounting():
    panel = synthetic_panel(n=300, seed=4)
    rng = np.random.default_rng(9)
    direction = rng.choice([-1, 0, 1], size=len(panel["open"]), p=[0.4, 0.2, 0.4]).astype(np.int8)
    events = [[] for _ in panel["open_ms"]]
    events[10] = [(int(panel["open_ms"][10]) + 8, 0.0001 * 100.0)]   # official stamp +8ms
    direction[9] = 1                              # guarantee a live book at the funding stamp
    m = eng.simulate(panel, events, direction, DCA0, 1, 200, {}, INST)
    assert m["layer_hist"][0] == m["episodes"]
    assert sum(m["layer_hist"][1:]) == m["episodes"]
    assert sum(m["layer_hist"]) == 2 * m["episodes"]
    assert m["open_at_end"] == 0
    assert m["horizon_exits"] + m["stop_hits"] + m["tp_hits"] + m["margin_calls"] \
        == m["episodes"], m
    assert m["fills"] == 2 * m["episodes"] + m["adds"], m
    assert math.isclose(m["net_pnl"], m["gross_pnl"] - m["fees"] - m["funding"],
                        abs_tol=1e-3)
    assert m["decomposition_ok"] is True
    assert m["funding"] != 0.0
    assert m["turnover_usdt"] > 0 and m["max_effective_leverage"] <= eng.LEVERAGE + 1e-9
    ok("simulate_episode_funding_and_decomposition")


def test_cost_stresses_change_net_not_gross():
    panel = synthetic_panel(n=240, seed=6)
    direction = np.where(np.arange(len(panel["open"])) % 7 == 0, 1, -1).astype(np.int8)
    direction[::5] = 0
    events = [[] for _ in panel["open_ms"]]
    for b in range(10, 200, 24):
        if direction[b - 1] != 0:                       # live book at the funding stamp
            events[b] = [(int(panel["open_ms"][b]) + 8, 0.0001 * float(panel["close"][b]))]
    assert any(events), "test panel must charge at least one official funding event"
    base = eng.simulate(panel, events, direction, DCA0, 1, 200, {}, INST)
    assert base["funding"] != 0.0
    fee2x = eng.simulate(panel, events, direction, DCA0, 1, 200,
                         eng.cost_for("fee_2x"), INST)
    assert math.isclose(fee2x["gross_pnl"], base["gross_pnl"], abs_tol=1e-9)
    assert fee2x["fees"] == 2 * base["fees"] and fee2x["fees"] > 0
    assert fee2x["net_pnl"] < base["net_pnl"]
    slip = eng.simulate(panel, events, direction, DCA0, 1, 200,
                        eng.cost_for("slippage_2ticks"), INST)
    assert slip["net_pnl"] < base["net_pnl"]
    assert not math.isclose(slip["gross_pnl"], base["gross_pnl"], abs_tol=1e-9)
    delay = eng.simulate(panel, events, direction, DCA0, 1, 200,
                         eng.cost_for("entry_delay_1_bar"), INST)
    assert not math.isclose(delay["net_pnl"], base["net_pnl"], abs_tol=1e-9)
    f2 = eng.simulate(panel, events, direction, DCA0, 1, 200,
                      eng.cost_for("funding_2x"), INST)
    assert math.isclose(f2["funding"], 2 * base["funding"], rel_tol=1e-9)
    assert math.isclose(f2["gross_pnl"], base["gross_pnl"], abs_tol=1e-9)
    # sign-agnostic: the extra funding leg must move net by exactly -funding
    assert math.isclose(f2["net_pnl"] - base["net_pnl"], -base["funding"], abs_tol=1e-9)
    assert not math.isclose(f2["net_pnl"], base["net_pnl"], abs_tol=1e-9)
    nof = eng.simulate(panel, events, direction, DCA0, 1, 200,
                       eng.cost_for("no_funding"), INST)
    assert nof["funding"] == 0.0
    assert math.isclose(nof["net_pnl"] - base["net_pnl"], base["funding"], abs_tol=1e-9)
    for m in (fee2x, slip, delay, f2, nof):
        assert m["decomposition_ok"] is True
    ok("cost_stress_effective_and_decomposition_not_tautological")


def test_ladder_scale_ins_and_reserve():
    """Every bar trades 3% against entry: exactly scale-ins 1..3 must deploy, no TP."""
    n = 60
    panel = synthetic_panel(n=n, constant=100.0)
    panel["high"] = np.full(n, 100.2)             # below breakeven TP of the running avg
    panel["low"] = np.full(n, 97.0)               # crosses spacing 1%,2%,3% but not 4%
    panel["close"] = np.full(n, 100.0)
    direction = np.ones(n, dtype=np.int8)
    dca = dict(DCA0, spacing_pct=0.01, invalidation_pct=0.10)
    m = eng.simulate(panel, [[] for _ in panel["open_ms"]], direction, dca, 1, n, {}, INST)
    episodes = n - 1
    assert m["episodes"] == episodes
    assert m["adds"] == 3 * episodes, m["adds"]          # levels 1,2,3 only
    assert m["layer_hist"][0] == episodes
    assert sum(m["layer_hist"][1:]) == episodes
    assert len(m["layer_hist"]) == eng.LADDER_LEVELS     # tranche #12 stays reserve
    assert m["adds"] <= eng.MAX_ADD_LEVELS * episodes
    assert m["tp_hits"] == 0 and m["stop_hits"] == 0
    assert m["open_at_end"] == 0
    ok("ladder_three_scale_ins_within_one_bar_reserve")


def test_selector_survivor_semantics():
    # (a) no positive historical cell -> no_qualifying_candidate
    rows = all_rows(lambda g, c, d: -5.0)
    sel, info = eng.select_cohort(rows)
    assert sel is None and info["cull_reasons"] == ["no_qualifying_candidate"]
    # (b) too few episodes -> insufficient_trades
    rows = all_rows(lambda g, c, d: 1.0)
    for g in eng.GRIDS:
        for r in rows[g]:
            r["episodes"] = 4
    sel, info = eng.select_cohort(rows)
    assert sel is None and info["cull_reasons"] == ["insufficient_trades"]
    # (c) healthy winner, OOS/full/stress positive, neighbourhood positive
    def net(g, c, d):
        return 20.0 if (g in eng.GRIDS and d["spacing_pct"] in (0.02, 0.03)) else -1.0
    rows = all_rows(net)
    for g in eng.GRIDS:
        for r in rows[g]:
            r["sharpe"] = 1.5 if r["net_pnl"] > 0 else -0.2
    sel, info = eng.select_cohort(rows)
    assert sel is not None, info
    assert info["winner_source_grid"] == "historical"
    assert info["neighbourhood"]["passed"] is True
    assert set(info["cull_reasons"]) == set()
    assert sorted(info["phases"]) == ["full", "historical", "oos"]
    # (d) OOS economic failure is recorded and blocks survivorship
    rows = all_rows(net)
    for r in rows["oos"]:
        if r["window_functions"] == sel["window_functions"] and r["fpca_dim_j"] == sel["fpca_dim_j"] \
                and all(r[k] == sel[k] for k in eng.DCA_AXES):
            r["net_pnl"] = -7.0
    sel2, info2 = eng.select_cohort(rows)
    assert sel2 is None and "oos_economic" in info2["cull_reasons"], info2
    # (e) selector never reads a non-historical row
    try:
        eng.select_cohort({**rows, "historical": [dict(rows["historical"][0], grid="oos")]})
        raise AssertionError("selector accepted a non-historical row")
    except ValueError:
        pass
    # (f) deterministic lexical tie-break on registered axis order
    rows = all_rows(lambda g, c, d: 5.0)
    for r in rows["historical"]:
        r["sharpe"], r["net_pnl"] = 1.0, 5.0
        if r["spacing_pct"] == 0.02:
            r["sharpe"], r["net_pnl"] = 1.2, 6.0
    sel3, info3 = eng.select_cohort(rows)
    assert sel3 is not None, info3
    assert sel3["spacing_pct"] == 0.02
    assert (sel3["window_functions"], sel3["fpca_dim_j"]) == (90, 2)
    assert (sel3["size_multiplier"], sel3["breakeven_tp_pct"],
            sel3["invalidation_pct"]) == (1.0, 0.01, 0.05)
    assert info3["cull_reasons"] == []
    ok("selector_historical_only_cull_reasons_tiebreak")


def test_neighbourhood_threshold():
    def net(g, c, d):
        return 3.0 if d["spacing_pct"] != 0.04 else -3.0
    rows = all_rows(net)
    winner = next(r for r in rows["historical"]
                  if r["spacing_pct"] == 0.03 and r["window_functions"] == 100
                  and r["fpca_dim_j"] == 4)
    res = eng.neighbourhood(winner, rows["historical"])
    assert res["neighbours"] > 0 and 0.0 <= res["same_sign_fraction"] <= 1.0
    assert res["passed"] == (res["same_sign_fraction"] >= eng.MIN_NEIGHBOUR)
    ok("neighbourhood_60pct_rule")


def test_grid_csv_and_coverage_keys():
    rows = all_rows(lambda g, c, d: 1.0)["historical"][:5]
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "grid_historical.csv"
        eng.write_grid(path, rows)
        header = path.read_text(encoding="utf-8").splitlines()[0].split(",")
        assert set(header) == {"symbol", "timeframe", "window_functions", "fpca_dim_j",
                               "case_label", *eng.DCA_AXES, "grid",
                               *eng.metric_block(eng.empty_metric(0, 0)).keys(),
                               "decomposition_ok"}
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 6
        for line in lines[1:]:
            parts = dict(zip(header, line.split(",")))
            assert parts["decomposition_ok"] in ("True", "False")
        try:
            eng.write_grid(path, rows)
            raise AssertionError("grid CSV must be write-once")
        except FileExistsError:
            pass
    ok("grid_csv_contract_write_once")


def test_trace_hook_inert():
    panel = synthetic_panel(n=120, seed=2)
    direction = np.ones(len(panel["open"]), dtype=np.int8)
    assert eng.TRACE is None
    m = eng.simulate(panel, [[] for _ in panel["open_ms"]], direction, DCA0, 1, 60, {}, INST)
    assert eng.TRACE is None, "section 28 trace hook must stay inert in grid search"
    assert not any("ledger" in k or "fill_rows" in k for k in m), sorted(m)
    assert set(eng.metric_block(m)) <= set(m)
    ok("trace_hook_inert_no_fill_ledger_in_rows")


def test_falsification_helpers():
    a = np.array([1, 1, 1, 0, -1, -1], dtype=np.int8)
    y = np.array([1, 1, 0, 0, -1, 1], dtype=np.int8)
    acc, n = eng.accuracy(a, y, 0, 6)
    assert n == 4 and math.isclose(acc, 0.75), (acc, n)
    acc0, n0 = eng.accuracy(a, y, 4, 4)
    assert acc0 is None and n0 == 0
    t = eng.mcnemar_test(np.array([True, True, False, False]),
                         np.array([True, False, False, False]))
    assert t["n01"] == 1 and t["n10"] == 0
    assert t["p_value"] == 1.0 and t["reject_equal_5pct"] is False
    strong = eng.mcnemar_test(np.array([True] * 40 + [False] * 5),
                              np.array([False] * 40 + [False] * 5))
    assert strong["reject_equal_5pct"] is True
    assert eng._status(True) == "FALSIFIED" and eng._status(False) == "NOT_FALSIFIED"
    assert eng.ret_slice((10, 20)) == (9, 19)
    assert eng.ret_slice((0, 5)) == (0, 4)
    ok("falsification_helpers_accuracy_mcnemar_ret_slice")


def test_expected_outputs_match_artifacts():
    assert tuple(eng.ARTIFACTS) == tuple(eng.run_spec_template()["expected_outputs"])
    assert "logs/run.log" in eng.ARTIFACTS
    assert sum(1 for a in eng.ARTIFACTS if a.startswith("artifacts/grid_")) == 10
    assert len(eng.ARTIFACTS) == len(set(eng.ARTIFACTS))
    assert eng.round_spec_template()["expected_outputs"] == list(eng.ARTIFACTS)
    ok("expected_outputs_registered")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    broken = []
    for fn in tests:
        try:
            fn()
        except Exception as exc:                       # noqa: BLE001 - report then fail
            broken.append(fn.__name__)
            print("FAIL %s: %s: %s" % (fn.__name__, type(exc).__name__, exc))
    print("%d/%d checks passed" % (len(tests) - len(broken), len(tests)))
    if broken:
        print("FAILED: " + ", ".join(broken))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
