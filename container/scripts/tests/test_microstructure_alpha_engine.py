#!/usr/bin/env python3
"""Deterministic host self-check for the microstructure-alpha engine; no Qlib or raw data needed.

Covers the family-owned surface (feature causality, purged folds, stability selection, entry
events, selector cull reasons) and the accounting contract of the reused audited rail
(gross/fee/funding decomposition with a negative control, adverse slippage direction, DCA ladder,
funding accounting).  Run from the repo root or from this directory.
"""
import importlib.util
from pathlib import Path

import numpy as np

SCRIPT = Path(__file__).resolve().parents[1] / "190_microstructure_alpha_run.py"
spec = importlib.util.spec_from_file_location("microstructure_alpha_run", SCRIPT)
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)


def synthetic_panel(seed=11, count=600):
    rng = np.random.default_rng(seed)
    returns = rng.normal(0.0001, 0.01, size=count)
    close = 100.0 * np.exp(np.cumsum(returns))
    high = close * (1.0 + np.abs(rng.normal(0.0, 0.004, size=count)))
    low = close * (1.0 - np.abs(rng.normal(0.0, 0.004, size=count)))
    open_px = np.concatenate(([close[0]], close[:-1]))
    volume = np.abs(rng.normal(1000.0, 200.0, size=count)) + 10.0
    return open_px, high, low, close, volume


class StubModel:
    """Deterministic least-squares stand-in for the registered gradient boosters (host self-check)."""

    def __init__(self, x, y):
        design = np.column_stack([np.ones(len(x)), x])
        coef, *_ = np.linalg.lstsq(design, y, rcond=None)
        self.coef = coef

    def predict(self, x):
        design = np.column_stack([np.ones(len(x)), x])
        return design @ self.coef


def stub_fitter(x_train, y_train, seed):
    return StubModel(x_train, y_train)


def make_row(symbol, timeframe, case, params, net_pnl, sharpe, episodes, grid="historical"):
    row = {"symbol": symbol, "timeframe": timeframe, "case_label": case["label"], "grid": grid,
           "net_pnl": float(net_pnl), "sharpe": float(sharpe), "episodes": int(episodes),
           "fills": int(episodes), "adds": 0, "gross_pnl": float(net_pnl), "fees": 0.0,
           "funding": 0.0, "turnover_usdt": 0.0, "max_dd_pct": 0.0, "annualized_return": 0.0,
           "capital_utilization": 0.0, "tp_hits": 0, "stop_hits": 0, "time_exits": 0}
    row.update(engine.case_columns(case))
    row.update(params)
    return row


def test_features_and_folds():
    open_px, high, low, close, volume = synthetic_panel()
    panel, target = engine.feature_panel(open_px, high, low, close, volume, 24)
    assert panel.shape == (len(close), len(engine.FEATURE_NAMES))
    assert target.shape == (len(close),)
    assert np.isnan(target[-1])
    # Causal guard: a future shock cannot change any earlier feature value.
    altered = close.copy()
    altered[400:] *= 2.5
    panel2, _ = engine.feature_panel(open_px, high, low, altered, volume, 24)
    assert np.allclose(panel[:390], panel2[:390], equal_nan=True)
    probe = engine.causality_probe((open_px, high, low, close, volume), 24, sample_bars=8)
    assert probe["bars_probed"] >= 4 and probe["mismatches"] == 0, probe
    months = engine.month_ordinals(np.arange(len(close), dtype=np.int64) * 86400000 + 1640995200000)
    folds = engine.fold_specs(months)
    assert folds and all(f["train_months"][1] <= f["test_months"][0] for f in folds)
    engine.MODEL_FITTERS["stub"] = stub_fitter
    prediction = engine.walk_forward_predictions(panel, target, months, "stub", engine.SEED)
    fitted = [f for f in prediction["folds"] if f["status"] == "fitted"]
    assert fitted, prediction["folds"][:2]
    assert np.isfinite(prediction["yhat"]).sum() > 0
    assert all(f["selected_features"] for f in fitted)
    assert all(len(f["selected_features"]) <= engine.STABILITY["top_k"] for f in fitted)
    z = prediction["yhat"] / np.maximum(prediction["sigma"], 1e-12)
    long_path = engine.position_path(z, "long_only")
    short_path = engine.position_path(z, "long_short")
    assert not np.any(long_path["events"] == -1)
    assert np.count_nonzero(short_path["events"] == -1) >= np.count_nonzero(long_path["events"] == -1)
    events = long_path["events"]
    finite = np.isfinite(z)
    # Every entry event is a fresh non-zero change above the registered entry threshold.
    for index in np.flatnonzero(events == 1):
        assert finite[index] and z[index] >= engine.ENTRY_Z
        prior = events[index - 1] if index > 0 else 0
        assert prior != 1
    selection = engine.stability_select(
        engine.rank_columns(panel[np.isfinite(panel).all(axis=1)][:200]),
        engine.average_ranks(target[np.isfinite(panel).all(axis=1)][:200]),
        engine.FEATURE_NAMES, engine.SEED)
    assert selection["selected_names"] and set(selection["selected_names"]) <= set(engine.FEATURE_NAMES)


def test_rail_accounting():
    count = 60
    open_px = np.full(count, 100.0)
    high = np.full(count, 100.2)
    low = np.full(count, 99.8)
    close = np.full(count, 100.0)
    open_px[6] = 100.0
    high[7] = 110.0            # take-profit reachable
    low[7] = 99.9
    close[7] = 105.0
    ms = np.arange(count, dtype=np.int64) * 86400000 + 1640995200000
    funding = np.zeros(count)
    layer = {"events": np.zeros(count, dtype=np.int64), "pos": np.zeros(count, dtype=np.int64)}
    layer["events"][5] = 1
    layer["pos"][5:] = 1
    params = {"spacing_pct": 0.02, "size_multiplier": 1.1,
              "breakeven_tp_pct": 0.01, "invalidation_pct": 0.10}
    base = engine.RAIL.simulate(open_px, high, low, close, ms, funding, layer, params,
                                60, 10, 0, count, {}, "BTCUSDT")
    assert base["episodes"] == 1 and base["decomposition_ok"]
    assert abs(base["gross_pnl"] - base["fees"] - base["funding"] - base["net_pnl"]) <= 1e-6
    # Negative control: the identity is a real cross-check, not a tautology.
    assert abs(base["gross_pnl"] - (base["fees"] + 1.0) - base["funding"] - base["net_pnl"]) > 1e-6
    tick = engine.instrument_constants()["tick"]["BTCUSDT"]
    zero = engine.RAIL.simulate(open_px, high, low, close, ms, funding, layer, params,
                                60, 10, 0, count, {"slip_ticks": 0}, "BTCUSDT")
    one = engine.RAIL.simulate(open_px, high, low, close, ms, funding, layer, params,
                               60, 10, 0, count, {"slip_ticks": 1}, "BTCUSDT")
    two = engine.RAIL.simulate(open_px, high, low, close, ms, funding, layer, params,
                               60, 10, 0, count, {"slip_ticks": 2}, "BTCUSDT")
    # The registered baseline is one tick adverse on both legs: more ticks, strictly less gross.
    assert abs(base["gross_pnl"] - one["gross_pnl"]) <= 1e-9
    assert zero["gross_pnl"] > one["gross_pnl"] > two["gross_pnl"]
    assert abs(one["gross_pnl"] - two["gross_pnl"]) > tick
    # DCA ladder: a 2% adverse move must add exactly one level at spacing 0.02, and the
    # take-profit re-anchors to the running average cost of both layers.
    dip_low = low.copy()
    dip_low[7] = 100.0 * (1.0 - 0.021)
    dip_high = high.copy()
    dip_high[7] = 100.2
    dip_high[8] = 110.0
    dip_close = close.copy()
    dip_close[8] = 105.0
    added = engine.RAIL.simulate(open_px, dip_high, dip_low, dip_close, ms, funding, layer, params,
                                 60, 10, 0, count, {}, "BTCUSDT")
    assert added["adds"] == 1, added
    assert added["fills"] == 3, added
    assert added["layer_hist"][0] == 1 and added["layer_hist"][1] == 1, added["layer_hist"]
    # No add when the same dip happens one level deeper than the registered spacing.
    flat_low = low.copy()
    flat_low[7] = 100.0 * (1.0 - 0.019)
    no_add = engine.RAIL.simulate(open_px, dip_high, flat_low, dip_close, ms, funding, layer, params,
                                  60, 10, 0, count, {}, "BTCUSDT")
    assert no_add["adds"] == 0 and no_add["fills"] == 2, no_add
    # Funding is charged per bar the position is open and disappears under the no_funding stress.
    funded = np.full(count, 0.0005)
    charged = engine.RAIL.simulate(open_px, high, low, close, ms, funded, layer, params,
                                   60, 10, 0, count, {}, "BTCUSDT")
    waived = engine.RAIL.simulate(open_px, high, low, close, ms, funded, layer, params,
                                  60, 10, 0, count, {"no_funding": True}, "BTCUSDT")
    assert charged["funding"] > 0.0
    assert waived["funding"] == 0.0
    assert abs(charged["net_pnl"] - (charged["gross_pnl"] - charged["fees"] - charged["funding"])) <= 1e-6
    assert charged["net_pnl"] < waived["net_pnl"]
    assert charged["decomposition_ok"] and waived["decomposition_ok"]


def test_selector():
    params_grid = [dict(zip(engine.DCA_AXES, values))
                   for values in __import__("itertools").product(*(engine.DCA_AXES[a] for a in engine.DCA_AXES))]
    case = engine.STRATEGIES[0]
    rows_by_grid = {grid: [] for grid in engine.GRID_KINDS}
    for grid in engine.GRID_KINDS:
        for params in params_grid:
            rows_by_grid[grid].append(make_row("BTCUSDT", "1h", case, params, -10.0, -0.5, 50, grid))
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
    # OOS economic failure must cull the historical winner with the registered reason.
    for row in rows_by_grid["oos"]:
        if engine.row_key(row) == engine.row_key(winner):
            row["net_pnl"] = -5.0
    winner2, detail2 = engine.select_cohort(rows_by_grid)
    assert winner2 is None and "oos_economic" in detail2["cull_reasons"], detail2
    # Robustness failure must be reported grid by grid.
    for grid in engine.GRID_KINDS:
        for row in rows_by_grid[grid]:
            row["net_pnl"] = 25.0
    for row in rows_by_grid["oos"]:
        row["net_pnl"] = 25.0
    for row in rows_by_grid["fee_2x"]:
        row["net_pnl"] = -1.0
    winner3, detail3 = engine.select_cohort(rows_by_grid)
    assert winner3 is None and detail3["cull_reasons"] == ["robustness_economic:fee_2x"], detail3


def test_registered_constants():
    assert engine.FAMILY_ID == "crypto-microstructure-alpha-hierarchical-cross-asset-transfer-2026-09-01"
    assert engine.SYMBOLS == ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
    assert [tf["raw_interval"] for tf in engine.TIMEFRAMES] == ["5m", "15m", "30m", "1h", "4h", "1d", "1w"]
    assert [tf["bars_per_day"] for tf in engine.TIMEFRAMES] == [288, 96, 48, 24, 6, 1, 1]
    assert len(engine.STRATEGIES) == 4 and len(engine.DCA_AXES) == 4
    dca_count = 1
    for values in engine.DCA_AXES.values():
        dca_count *= len(values)
    assert dca_count == 48
    assert len(engine.GRID_KINDS) == 10
    assert len(engine.SYMBOLS) * len(engine.TIMEFRAMES) * len(engine.STRATEGIES) * dca_count * len(engine.GRID_KINDS) == 53760
    assert engine.GATES["min_episodes_is"] == 30 and engine.GATES["min_episodes_oos"] == 10
    assert engine.ENTRY_Z == 0.5 and engine.LEVERAGE == 10.0 and engine.START_EQUITY == 30000.0
    assert engine.disposition_for_survivor_count(0) == "REJECT / NO_SURVIVOR"
    assert engine.disposition_for_survivor_count(1) == "SURVIVOR_FOUND"
    assert engine.disposition_for_survivor_count(3) == "MULTIPLE_SURVIVORS"
    ticks = engine.instrument_constants()
    assert ticks["tick"]["BTCUSDT"] == 0.10 and ticks["taker_fee"]["BTCUSDT"] == 0.0005


def main():
    test_registered_constants()
    test_features_and_folds()
    test_rail_accounting()
    test_selector()
    print("microstructure-alpha engine self-check: PASS")


if __name__ == "__main__":
    main()
