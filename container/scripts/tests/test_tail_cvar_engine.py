#!/usr/bin/env python3
"""Self-check for the tail-CVaR direct-family Qlib runner (contract 16 / body preflight).

Run inside the production image (the spec checks read /data/raw/_meta/CONFIG.json):

    container exec qlib-run /opt/venv/bin/python /scripts/tests/test_tail_cvar_engine.py

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

ENGINE_PATH = Path(__file__).resolve().parents[1] / "280_tail_cvar_run.py"
_spec = importlib.util.spec_from_file_location("tail_cvar_engine", ENGINE_PATH)
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
        if (i // step) % 2:
            d[i] = -1
    return d


def positive_funding(panel):
    return [[(int(t) + 8 * 3600 * 1000, 0.0005 * float(c))]
            for t, c in zip(panel["open_ms"], panel["close"])]


def build_spec():
    spec = eng.run_spec_template()
    spec["round_id"] = eng.FAMILY_ID + "-r1"
    spec["run_id"] = spec["round_id"] + "-u1"
    spec["created_at_utc"] = "2026-09-28T00:00:00Z"
    return spec


def base_row(symbol, case, dca, grid, net, sharpe, episodes):
    row = {"symbol": symbol, "timeframe": eng.TIMEFRAME,
           "cvar_alpha": case["cvar_alpha"], "short_budget": case["short_budget"],
           "case_label": case["label"], **dca, "grid": grid,
           **eng.metric_block(eng.empty_metric(0, 0)), "decomposition_ok": True}
    row.update(net_pnl=net, sharpe=sharpe, episodes=episodes,
               ending_equity=eng.START_EQUITY + net)
    return row


def rows_from(fn):
    rows = {g: [] for g in eng.GRIDS}
    for case in eng.CASES:
        for dca in eng.DCA_GRID:
            for grid in eng.GRIDS:
                net, sharpe, episodes = fn(grid, case, dca)
                rows[grid].append(base_row("BTCUSDT", case, dca, grid, net, sharpe, episodes))
    return rows


def is_winner(case, dca):
    return case["cvar_alpha"] == 0.05 and case["short_budget"] == 0.0 \
        and all(dca[k] == v for k, v in WINNER_CELL.items())


def gjr_series(n, seed, omega=1.8e-5, alpha=0.08, gamma=0.10, beta=0.85, nu=5.0):
    rng = np.random.default_rng(seed)
    s2 = omega / (1.0 - alpha - beta - 0.5 * gamma)
    out = np.empty(n)
    e_prev = 0.0
    for i in range(n):
        if i:
            s2 = omega + (alpha + (gamma if e_prev < 0 else 0.0)) * e_prev ** 2 + beta * s2
        z = float(rng.standard_t(nu)) / math.sqrt(nu / (nu - 2.0))
        e = math.sqrt(s2) * z
        out[i] = e
        e_prev = e
    return out


# --------------------------------------------------------------------------------------
def test_frozen_domain_registration():
    spec = eng.run_spec_template()
    assert [p["cvar_alpha"] for p in spec["params"]] == [0.05, 0.05, 0.01, 0.01], spec["params"]
    assert [p["short_budget"] for p in spec["params"]] == [0.0, 0.3, 0.0, 0.3], spec["params"]
    assert [p["label"] for p in spec["params"]] == ["a05_lo", "a05_ls", "a01_lo", "a01_ls"]
    assert spec["params"] == [dict(c) for c in eng.CASES]
    dca = spec["dca_domain"]
    assert list(dca["spacing_pct"]) == [0.01, 0.02, 0.03, 0.04]
    assert list(dca["size_multiplier"]) == [1.0, 1.1]
    assert list(dca["breakeven_tp_pct"]) == [0.01, 0.02, 0.03]
    assert list(dca["invalidation_pct"]) == [0.05, 0.10]
    assert len(dca["grid"]) == 48 and len(set(json.dumps(x, sort_keys=True) for x in dca["grid"])) == 48
    assert dca["base_quote"] == 1000.0
    assert dca["base_quote_status"] == "PROJECT_PRE_REGISTERED_CONSTANT"
    for axis in ("spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct"):
        assert dca[axis + "_status"] == "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN", axis
    assert spec["expected"] == {"cohorts": 4, "strategy_cases_per_cohort": 4,
                                "dca_configs_per_cohort": 48, "base_combinations_per_cohort": 192,
                                "case_evaluations_per_grid": 768, "grid_count": 10,
                                "case_evaluations_total": 7680}
    assert list(eng.GRIDS) == ["historical", "oos", "full", "fee_2x", "funding_2x",
                               "entry_delay_1_bar", "slippage_2ticks", "no_funding",
                               "no_funding_full", "cost_attrition_40bps"]
    assert spec["gates"] == {"min_episodes_is": 10, "min_episodes_oos": 3,
                             "min_neighbour_same_sign_fraction": 0.6}
    assert spec["selector_version"] == "cohort-selector-v1"
    assert spec["disposition_version"] == "cohort-disposition-v1"
    assert not any(k in spec for k in ("task_id", "kanban_task_id", "kanban_board"))
    assert eng.expected_counts()["case_evaluations_total"] == 7680
    assert eng.SIGNAL_PARAMS["cvar_alphas"] == [0.05, 0.01]
    assert eng.SIGNAL_PARAMS["short_budgets"] == [0.0, 0.30]
    assert eng.SIGNAL_PARAMS["weight_deadband"] == 0.02
    assert eng.SIGNAL_PARAMS["est_window"] == 252 and eng.SIGNAL_PARAMS["refit_every"] == 21


def test_spec_validation_and_rejections():
    if not Path(eng.RAW_ROOT + "/_meta/CONFIG.json").is_file():
        print("  [skip] /data/raw/_meta/CONFIG.json not present (host run?)")
        return
    ok = build_spec()
    assert eng.validate_spec(ok)["case_evaluations_total"] == 7680

    def rejects(mutate, why):
        spec = json.loads(json.dumps(build_spec()))
        mutate(spec)
        try:
            eng.validate_spec(spec)
        except ValueError as exc:
            print("  reject[%s]: %s" % (why, exc))
            return
        raise AssertionError("validate_spec accepted a tampered spec: " + why)

    rejects(lambda s: s.update(task_id="t_phantom"), "kanban ownership key")
    rejects(lambda s: s["params"].append({"case_code": 4, "cvar_alpha": 0.10,
                                          "short_budget": 0.5, "label": "a10_xx"}),
            "extra strategy case")
    rejects(lambda s: s["dca_domain"]["spacing_pct"].append(0.05), "DCA axis widened")
    rejects(lambda s: s["dca_domain"].update(base_quote_status="USER_FIXED"),
            "provenance class rewrite")
    rejects(lambda s: s["expected"].update(case_evaluations_total=7679), "coverage rewrite")
    rejects(lambda s: s["signal_constants"].update(weight_deadband=0.05), "registered constant rewrite")
    rejects(lambda s: s["script"].update(sha256="sha256:" + "0" * 64), "script pin mismatch")
    rejects(lambda s: s.update(selector_version="cohort-selector-v2"), "selector rewrite")
    rejects(lambda s: s.update(round_id="some-other-family-r1"), "round identity")
    rejects(lambda s: s["dca_domain"].update(grid=s["dca_domain"]["grid"][:47]), "grid shrink")


def test_record_constants_registration_and_falsification_battery():
    assert eng.SIGNAL_PARAMS["cvar_alphas"] == [0.05, 0.01]
    assert eng.SIGNAL_PARAMS["short_budgets"] == [0.0, 0.30]
    assert eng.PIT_STATUS == "PASS_NO_LOOKAHEAD"
    assert eng.STRATEGY_AXES == {"cvar_alpha": (0.05, 0.01), "short_budget": (0.0, 0.30)}
    assert len(eng.FALSIFICATION) == 4
    assert sorted(eng.FALSIFICATION_REGISTRY) == [
        "concentration_removal", "gaussian_tail_control", "subsample_regime_inversion",
        "turnover_cost_stress"]
    assert eng.CONCENTRATION_SYMBOLS == ("BTCUSDT", "ETHUSDT")
    assert eng.COST_STRESS_BPS == (0.0010, 0.0025)
    assert eng.DOWN_CYCLE == ("2022-01-01", "2022-12-31")
    assert len(eng.ARTIFACTS) == len(set(eng.ARTIFACTS))
    for name in ("artifacts/falsification.json", "artifacts/econometrics.json",
                 "artifacts/portfolio_diagnostics.json", "artifacts/risk_free_coverage.json"):
        assert name in eng.ARTIFACTS, name
    assert eng._aggregate({}) == "INDETERMINATE"
    assert eng._aggregate({"a": {"falsified": True}, "b": {"falsified": False}}) == "FALSIFIED"
    assert eng._aggregate({"a": {"falsified": False}}) == "NOT_FALSIFIED"


def test_gjr_garch_t_fit_and_prefix_forecast():
    theta = (1.8e-5, 0.08, 0.10, 0.85, 5.0)
    series = gjr_series(600, seed=11)
    nll, forecast, path = eng._garch_path(theta, series)
    assert math.isfinite(nll) and nll < 1e11, "likelihood sentinel (fit invalid region): %s" % nll
    assert forecast > 0 and len(path) == 600
    # Prefix consistency == point-in-time: a forecast made after k observations must not
    # move when more observations are appended.
    for k in (100, 250, 400):
        head = eng._garch_path(theta, series[:k])[1]
        assert abs(head - path[k]) <= 1e-12 * max(1.0, path[k]), (k, head, path[k])
    # Bad parameters are rejected instead of silently producing a finite likelihood.
    assert eng._garch_path((1.8e-5, 0.9, 0.4, 0.9, 5.0), series)[0] >= 1e11
    assert eng._garch_path((1.8e-5, 0.08, 0.10, 0.85, 1.5), series)[0] >= 1e11

    fitted = eng.fit_gjr_garch_t(series[:252])
    assert 0 < fitted["persistence"] < 1, fitted
    assert fitted["nu"] > 2.0 and fitted["omega"] > 0 and fitted["nll"] < 1e6
    assert abs(fitted["persistence"] - (theta[1] + theta[3] + 0.5 * theta[2])) < 0.30, fitted
    assert 0 <= fitted["alpha"] <= 1 and 0 <= fitted["beta"] <= 1
    # The record's asymmetry parameter must be identifiable on leverage data.
    assert -0.5 <= fitted["gamma"] <= 1.0


def test_hill_and_long_memory_estimators():
    rng = np.random.default_rng(5)
    losses = np.abs(rng.pareto(3.0, 400)) + 1e-6
    out = eng.hill_tail_index(list(losses))
    assert out is not None and out["k"] == int(math.floor(math.sqrt(400))) == 20
    ordered = np.sort(losses)[::-1]
    k = out["k"]
    manual = 1.0 / float(np.mean(np.log(ordered[:k]) - math.log(ordered[k])))
    assert abs(out["alpha_hat"] - manual) < 1e-12, (out["alpha_hat"], manual)
    assert out["alpha_hat"] > 0
    # degenerate inputs stay None instead of producing a fake estimate
    assert eng.hill_tail_index([0.0, 0.0, 0.0]) is None

    squared = (gjr_series(1024, seed=13) ** 2)
    d_gph = eng.gph_d(squared)
    d_lw = eng.local_whittle_d(squared)
    assert d_gph is not None and -0.5 < d_gph < 1.0, d_gph
    assert d_lw is not None and -0.5 < d_lw < 1.0, d_lw
    # a persistent (long-memory-like) series must estimate d above a white-noise level
    sq_long = gjr_series(4096, seed=17) ** 2
    ar = np.empty(4096)
    ar[0] = sq_long[0]
    for i in range(1, len(ar)):
        ar[i] = 0.12 * sq_long[i] + 0.88 * ar[i - 1]
    assert eng.gph_d(ar) is not None


def test_cvar_lp_mvp_feasibility_and_direction_mapping():
    rng = np.random.default_rng(99)
    n = 400
    common = rng.normal(0, 0.02, n)
    R = np.vstack([0.0005 + 0.9 * common + rng.normal(0, 0.01, n),
                   0.0004 + 1.1 * common + rng.normal(0, 0.012, n),
                   -0.0002 + 0.6 * common + rng.normal(0, 0.008, n),
                   0.0001 + 0.8 * common + rng.normal(0, 0.01, n)]).T
    cov = np.cov(R, rowvar=False)
    for case in eng.CASES:
        budget = case["short_budget"]
        w = eng.solve_cvar(R, case["cvar_alpha"], budget)
        assert abs(w.sum() - 1.0) <= 1e-6, w
        assert w.min() >= -budget - 1e-6 and w.max() <= 1 + budget + 1e-6, w
        if budget == 0.0:
            assert (w >= -1e-9).all(), "the LO regime must never short"
        mv = eng.solve_mvp(cov, budget)
        assert abs(mv.sum() - 1.0) <= 1e-6
        assert mv.min() >= -budget - 1e-6 and mv.max() <= 1 + budget + 1e-6
    # deadband mapping: tiny weights stay flat, decisively signed weights take the leg
    d = eng.direction_from_weight(np.array([0.5, -0.5, 0.01, -0.01, 0.0]))
    assert list(d) == [1, -1, 0, 0, 0]
    # the deadband boundary itself stays flat (|w| > deadband is required to take a leg)
    assert eng.direction_from_weight(np.array([0.02, -0.02, 0.021, -0.021])).tolist() \
        == [0, 0, 1, -1]
    # LP determinism: identical inputs give the identical allocation
    a = eng.solve_cvar(R, 0.05, 0.30)
    b = eng.solve_cvar(R, 0.05, 0.30)
    assert np.allclose(a, b)


def test_allocation_layer_is_point_in_time():
    """Perturbing data strictly after day k must not move any decision at or before k."""
    n, k = 300, 274
    panel = make_universe(n)
    layer_a = eng.build_signal_layer(panel, report={})
    touched = {s: {key: (val.copy() if isinstance(val, np.ndarray) else val)
                   for key, val in panel[s].items()} for s in eng.SYMBOLS}
    rng = np.random.default_rng(31337)
    for s in eng.SYMBOLS:
        drift = rng.normal(0.05, 0.05, n)
        touched[s]["close"][k + 1:] *= np.exp(np.cumsum(drift[k + 1:]))
        touched[s]["high"] = np.maximum(touched[s]["high"], touched[s]["close"])
        touched[s]["low"] = np.minimum(touched[s]["low"], touched[s]["close"])
    layer_b = eng.build_signal_layer(touched, report={})
    assert not np.allclose(np.vstack([panel[s]["close"][k + 1:] for s in eng.SYMBOLS]),
                           np.vstack([touched[s]["close"][k + 1:] for s in eng.SYMBOLS])), \
        "the perturbation must actually change the price path after day k"
    responded = 0.0
    for case in eng.CASES:
        code = case["case_code"]
        for s in eng.SYMBOLS:
            da, db = layer_a["directions"][code][s], layer_b["directions"][code][s]
            assert np.array_equal(da[:k + 1], db[:k + 1]), (case["label"], s)
            assert np.all(da[:eng.EST_WINDOW - 1] == 0), "signal must stay flat before warm-up"
            responded = max(responded, float(np.max(np.abs(
                layer_a["weights"][code][k + 1:] - layer_b["weights"][code][k + 1:]))))
        assert np.count_nonzero(layer_a["defined"]) > 0
    assert responded > 1e-4, \
        "the allocation layer never reacted to a changed price path (max shift %.3g)" % responded
    rep = layer_a["report"]
    assert rep["max_data_index_used"] == n - 2, rep
    assert rep["signal_days"] == n - eng.EST_WINDOW, rep
    assert rep["worst_sum_w_deviation"] <= 1e-6 and rep["worst_box_violation"] <= 1e-6, rep
    assert rep["garch"] and all(v["fits"] > 0 for v in rep["garch"].values()), rep
    # at least one leg in both directions exists somewhere in the universe, and the
    # LO cases never produce a short leg
    for case in eng.CASES:
        if case["short_budget"] == 0.0:
            for s in eng.SYMBOLS:
                assert (layer_a["directions"][case["case_code"]][s] >= 0).all(), \
                    "LO regime produced a short leg"
    # falsification probes were measured on refit days only
    assert all(len(layer_a["gauss_div"][c["case_code"]]) > 0 for c in eng.CASES)
    assert all(len(layer_a["reduced_div"][c["case_code"]]) > 0 for c in eng.CASES)


def test_ladder_accounting_is_independent_and_net_of_cost():
    panel = make_panel(300)
    direction = flipping_direction(300)
    funding = positive_funding(panel)
    case = eng.CASES[0]
    m = eng.simulate(panel, funding, direction, case, DCA, 50, 250, {}, INST)
    assert m["decomposition_ok"] is True
    assert abs(m["gross_pnl"] - m["fees"] - m["funding"] - m["net_pnl"]) < 1e-3
    assert abs(m["ending_equity"] - (eng.START_EQUITY + m["net_pnl"])) < 1e-6
    assert len(m["daily_equity"]) == 200
    assert m["episodes"] >= 1 and m["fills"] >= m["episodes"]
    assert m["turnover_usdt"] > 0 and m["fees"] > 0 and abs(m["funding"]) > 0
    assert m["open_at_end"] == 0
    assert m["max_effective_leverage"] <= eng.LEVERAGE + 1e-9
    assert m["capital_utilization"] <= 1.0 + 1e-9
    hist = m["layer_hist"]
    assert len(hist) == eng.LADDER_LEVELS == 12
    assert hist[0] == m["episodes"], "every entry must close exactly once"
    assert sum(hist) == 2 * m["episodes"]
    assert all(v == 0 for v in hist[12:]), "tranche 12 is reserve only"

    # Negative control: gross_pnl comes from an independent price-PnL accumulator.
    free = eng.simulate(panel, funding, direction, case, DCA, 50, 250, {"fee_mult": 0.0}, INST)
    assert abs(free["fees"]) < 1e-9
    assert abs(free["gross_pnl"] - m["gross_pnl"]) < 1e-6, "gross must not be back-solved from net"
    assert abs((free["net_pnl"] - m["net_pnl"]) - m["fees"]) < 1e-3, \
        "removing every fee must lift net by exactly the fees charged"
    assert free["net_pnl"] > m["net_pnl"], "taker fees must actually bite"
    assert abs(m["gross_pnl"] - (m["fees"] + 1.0) - m["funding"] - m["net_pnl"]) >= 1e-3

    # Signal semantics: a flat signal flattens reduce-only and never re-enters by itself.
    flat = np.zeros(300, dtype=np.int8)
    m0 = eng.simulate(panel, funding, flat, case, DCA, 50, 250, {}, INST)
    assert m0["episodes"] == 0 and m0["net_pnl"] == 0.0
    assert m0["decomposition_ok"]


def test_registered_stress_grids_all_move_net():
    panel = make_panel(300)
    direction = flipping_direction(300)
    funding = positive_funding(panel)
    case = eng.CASES[1]
    base = eng.simulate(panel, funding, direction, case, DCA, 50, 250, {}, INST)
    for grid, cost in {"fee_2x": {"fee_mult": 2.0}, "slippage_2ticks": {"slip_ticks": 2},
                       "cost_attrition_40bps": {"fee_override": 0.004}}.items():
        m = eng.simulate(panel, funding, direction, case, DCA, 50, 250, cost, INST)
        assert m["net_pnl"] < base["net_pnl"], "%s must reduce net" % grid
        assert abs(m["net_pnl"] - base["net_pnl"]) > 1e-9
    # fee-only stresses move net without touching the price-PnL accumulator; slippage is
    # an execution price effect and therefore must move BOTH gross and net
    for grid, cost in {"fee_2x": {"fee_mult": 2.0},
                       "cost_attrition_40bps": {"fee_override": 0.004}}.items():
        m = eng.simulate(panel, funding, direction, case, DCA, 50, 250, cost, INST)
        assert abs(m["gross_pnl"] - base["gross_pnl"]) < 1e-6, \
            "%s must touch net only, never gross" % grid
    slipped = eng.simulate(panel, funding, direction, case, DCA, 50, 250,
                           {"slip_ticks": 2}, INST)
    assert slipped["gross_pnl"] < base["gross_pnl"], \
        "adverse slippage must reduce gross (fill-price effect)"
    assert abs(slipped["net_pnl"] - slipped["gross_pnl"]
               + slipped["fees"] + slipped["funding"]) < 1e-3
    for grid, cost in {"entry_delay_1_bar": {"entry_delay": 1},
                       "funding_2x": {"funding_mult": 2.0},
                       "no_funding": {"no_funding": True}}.items():
        m = eng.simulate(panel, funding, direction, case, DCA, 50, 250, cost, INST)
        assert abs(m["net_pnl"] - base["net_pnl"]) > 1e-9, "%s was a no-op" % grid
    assert eng.cost_for("fee_2x") == {"fee_mult": 2.0}
    assert eng.cost_for("cost_attrition_40bps") == {"fee_override": 0.004}
    assert eng.cost_for("historical") == {}
    # a short leg must be mirrored: direction -1 flips the barrier logic and still closes
    short = -np.ones(300, dtype=np.int8)
    ms = eng.simulate(panel, funding, short, eng.CASES[3], DCA, 50, 250, {}, INST)
    assert ms["decomposition_ok"] and ms["episodes"] >= 1


def test_selector_disposition_and_neighbourhood():
    winner = WINNER_CELL

    def survivor_fn(grid, case, dca):
        win = is_winner(case, dca)
        if grid == "historical":
            return (100.0 if win else 10.0), (2.0 if win else 0.5), 50
        return (50.0 if win else 5.0), (1.0 if win else 0.3), 20

    rows = rows_from(survivor_fn)
    selected, info = eng.select_cohort(rows)
    assert selected is not None, info
    assert info["winner"] == {**{k: selected[k] for k in ("cvar_alpha", "short_budget",
                                                          *eng.DCA_AXES)}}
    assert set(info["winner"]) == {"cvar_alpha", "short_budget", "spacing_pct", "size_multiplier",
                                   "breakeven_tp_pct", "invalidation_pct"}
    assert info["winner_source_grid"] == "historical"
    assert info["cull_reasons"] == []
    # face neighbours of the lexicographically first cell: 2 strategy + 4 DCA axes
    assert info["neighbourhood"]["neighbours"] == 6, info["neighbourhood"]
    assert info["neighbourhood"]["passed"] is True
    assert info["neighbourhood"]["agreeing"] == 6
    assert set(info["metrics"]) >= {"historical", "oos", "full", "robustness", "neighbourhood"}
    assert set(info["metrics"]["robustness"]) == {"fee_2x", "funding_2x",
                                                  "entry_delay_1_bar", "slippage_2ticks"}
    assert info["metrics"]["oos"]["sharpe"] > 0 and info["metrics"]["full"]["net_pnl"] > 0

    # tie-break must follow REGISTERED INDEX order, not raw numeric value order:
    # cvar_alpha 0.05 (index 0) beats 0.01 (index 1) even though 0.01 < 0.05.
    def tied_fn(grid, case, dca):
        if grid == "historical":
            return 10.0, 1.0, 50
        return 10.0, 1.0, 20

    tied, _ = eng.select_cohort(rows_from(tied_fn))
    assert tied is not None
    assert (tied["cvar_alpha"], tied["short_budget"], tied["spacing_pct"],
            tied["size_multiplier"], tied["breakeven_tp_pct"], tied["invalidation_pct"]) == \
           (0.05, 0.0, 0.01, 1.0, 0.01, 0.05), tied

    def oos_fail(grid, case, dca):
        win = is_winner(case, dca)
        if grid == "historical":
            return (100.0 if win else 10.0), (2.0 if win else 0.5), 50
        if grid == "oos":
            return (-5.0 if win else 5.0), (0.5 if win else 0.3), 20
        return (50.0 if win else 5.0), (1.0 if win else 0.3), 20

    _, info = eng.select_cohort(rows_from(oos_fail))
    assert info["cull_reasons"] == ["oos_economic"], info["cull_reasons"]

    def robust_fail(grid, case, dca):
        win = is_winner(case, dca)
        if grid == "historical":
            return (100.0 if win else 10.0), (2.0 if win else 0.5), 50
        if grid == "fee_2x":
            return (-7.0 if win else 5.0), (-0.4 if win else 0.3), 20
        return (50.0 if win else 5.0), (1.0 if win else 0.3), 20

    _, info = eng.select_cohort(rows_from(robust_fail))
    assert info["cull_reasons"] == ["robustness_economic:fee_2x"], info["cull_reasons"]

    def neigh_fail(grid, case, dca):
        win = is_winner(case, dca)
        if grid == "historical":
            return (100.0 if win else -1.0), (2.0 if win else -0.5), 50
        return (50.0 if win else -1.0), (1.0 if win else -0.2), 20

    _, info = eng.select_cohort(rows_from(neigh_fail))
    assert info["cull_reasons"] == ["parameter_neighbourhood"], info["cull_reasons"]
    _, info = eng.select_cohort(rows_from(lambda g, c, d: (1.0, 0.1, 9)))
    assert info["cull_reasons"] == ["insufficient_trades"], info["cull_reasons"]
    _, info = eng.select_cohort(rows_from(lambda g, c, d: (-1.0, -0.1, 50)))
    assert info["cull_reasons"] == ["no_qualifying_candidate"], info["cull_reasons"]
    bad = rows_from(survivor_fn)
    bad["historical"][0]["grid"] = "oos"
    try:
        eng.select_cohort(bad)
    except ValueError:
        pass
    else:
        raise AssertionError("selector accepted a non-historical row")


def test_grid_csv_contract_and_portfolio_helpers():
    rows = rows_from(lambda g, c, d: (1.0, 0.1, 5))
    tmp = Path(tempfile.mkdtemp(prefix="tail-cvar-test-"))
    try:
        path = tmp / "grid_historical.csv"
        eng.write_grid(path, rows["historical"])
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            header = reader.fieldnames
            body = list(reader)
        expected = ["symbol", "timeframe", "cvar_alpha", "short_budget", "case_label",
                    *eng.DCA_AXES, "grid", *eng.metric_block(eng.empty_metric(0, 0)),
                    "decomposition_ok"]
        assert header == expected, (header, expected)
        assert len(body) == 192 == 4 * 48
        assert body[0]["symbol"] == "BTCUSDT" and body[0]["timeframe"] == "1d"
        assert float(body[0]["net_pnl"]) == 1.0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # coherent metrics: a fat-right-skewed draw must produce a finite Rachev ratio
    rng = np.random.default_rng(7)
    excess = rng.normal(0.0005, 0.01, 1500)
    metrics = eng.coherent_metrics(excess, np.full(1500, 1e-4))
    assert metrics["rachev_95"] > 0 and metrics["starr_95"] is not None
    assert metrics["sortino_annualized"] is not None and metrics["sharpe_annualized"] is not None
    assert eng.coherent_metrics(np.array([0.0]), np.array([0.0])) is None
    # portfolio turnover accounting: a rebalanced book pays, a held book does not
    rets = rng.normal(0.0004, 0.01, (4, 200))
    static = np.zeros((4, 200))
    static[:, :] = 0.25
    r0, turn0 = eng.portfolio_returns(static, rets, 0.0)
    r1, turn1 = eng.portfolio_returns(static, rets, 0.001)
    assert np.allclose(turn0, turn0[0]), "a constant weight book has no turnover after day 0"
    assert np.allclose(r0 - r1, 0.001 * turn1)
    bh = eng.buy_and_held_weights(rets)
    assert bh.shape == rets.shape and np.allclose(bh[:, 0], 0.25)
    assert not np.allclose(bh[:, 1], bh[:, 0]), "buy-and-hold weights must drift"
    assert eng.max_drawdown_pct(np.array([100.0, 120.0, 90.0, 130.0])) > 0


def test_round_spec_parameter_contract_when_present():
    """Cross-check of the frozen round-spec against this engine's registered domain;
    skipped when the round-spec has not been written yet."""
    path = Path("/results") / eng.FAMILY_ID / "rounds" / (eng.FAMILY_ID + "-r1") / "round-spec.json"
    if not path.is_file():
        print("  [skip] %s not present yet" % path)
        return
    spec = json.loads(path.read_text(encoding="utf-8"))
    assert spec["family_id"] == eng.FAMILY_ID
    assert spec["round_id"] == eng.FAMILY_ID + "-r1"
    assert not any(k in spec for k in ("task_id", "kanban_task_id", "kanban_board"))
    contract = spec["parameter_contract"]
    assert contract["family_id"] == eng.FAMILY_ID
    assert contract["parameter_contract_version"] == 1
    axes = {a["name"]: a for a in contract["research_axes_ordered"]}
    assert list(axes) == ["cvar_alpha", "short_budget", "spacing_pct", "size_multiplier",
                          "breakeven_tp_pct", "invalidation_pct"]
    assert axes["cvar_alpha"]["registered_values"] == [0.05, 0.01]
    assert axes["short_budget"]["registered_values"] == [0.0, 0.3]
    for name, domain in eng.DCA_AXES.items():
        assert axes[name]["registered_values"] == list(domain), name
    assert contract["row_fields"] == ["cvar_alpha", "short_budget", "spacing_pct",
                                      "size_multiplier", "breakeven_tp_pct", "invalidation_pct"]
    assert contract["strategy_param_fields"] == ["cvar_alpha", "short_budget"]
    assert contract["dca_param_fields"] == list(eng.DCA_AXES)
    assert contract["domain_cardinality"] == {"strategy": 4, "dca": 48, "per_cohort": 192}
    assert contract["composite_map"] == {}
    assert spec["expected"] == eng.expected_counts()
    assert spec["params"] == [dict(c) for c in eng.CASES]
    assert spec["dca_domain"]["config_count"] == 48
    print("  round-spec parameter_contract cross-check: OK")


def test_risk_free_series_boundary_and_alignment():
    """The registered window opens on 2022-01-01 (a Saturday): the official 3M T-bill
    series has no observation that day, so the carry-forward must be seeded from a
    bounded lookback instead of failing or inventing a value."""
    if not Path(eng.RISK_FREE_PATH).is_file():
        print("  [skip] canonical DGS3MO not present")
        return
    ms = np.array([eng.utc_ms("2021-12-25") + i * eng.MS_DAY for i in range(40)], dtype=np.int64)
    rf, rep = eng.load_risk_free(ms)
    assert len(rf) == 40 and np.isfinite(rf).all() and (rf > 0).all(), rep
    assert rep["rows_in_window"] > 0 and rep["truth_status_official_only"] is True
    assert rep["carry_forward_seeded_from"] is not None, rep
    assert rep["carry_forward_seeded_from"] < "2021-12-25", rep
    # 2022-01-03 carries the official 0.08 percent print; conversion is percent/100/365
    i = [int(t) for t in ms].index(eng.utc_ms("2022-01-03"))
    assert abs(rf[i] - 0.08 / 100.0 / 365.0) < 1e-12, (rf[i], rep)
    # Saturday and Sunday must both hold the previous official observation
    assert rf[0] == rf[1], "weekend carry-forward broke"


TESTS = [test_frozen_domain_registration,
         test_spec_validation_and_rejections,
         test_record_constants_registration_and_falsification_battery,
         test_gjr_garch_t_fit_and_prefix_forecast,
         test_hill_and_long_memory_estimators,
         test_cvar_lp_mvp_feasibility_and_direction_mapping,
         test_allocation_layer_is_point_in_time,
         test_ladder_accounting_is_independent_and_net_of_cost,
         test_registered_stress_grids_all_move_net,
         test_selector_disposition_and_neighbourhood,
         test_grid_csv_contract_and_portfolio_helpers,
         test_risk_free_series_boundary_and_alignment,
         test_round_spec_parameter_contract_when_present]


def main(argv):
    only = argv[1] if len(argv) > 1 else None
    failures = []
    for fn in TESTS:
        if only and only not in fn.__name__:
            continue
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - report and keep going
            failures.append((fn.__name__, "%s: %s" % (type(exc).__name__, exc)))
            print("FAIL %s: %s: %s" % (fn.__name__, type(exc).__name__, exc))
        else:
            print("PASS %s" % fn.__name__)
    total = len([t for t in TESTS if not only or only in t.__name__])
    print("%d/%d passed" % (total - len(failures), total))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
