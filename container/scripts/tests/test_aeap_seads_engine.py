#!/usr/bin/env python3
"""Self-check for the AEAP/SEADS cross-sectional rank-product Qlib runner.

Run inside the production image (validate_spec reads /data/raw/_meta/CONFIG.json):

    container exec qlib-run /opt/venv/bin/python /scripts/tests/test_aeap_seads_engine.py

Every check is a plain assert; any failure exits non-zero.  No production artifact
is written: panels are synthetic and CSVs go to a temp directory.
"""
from __future__ import annotations

import importlib.util
import json
import math
import sys
import tempfile
from pathlib import Path

import numpy as np

ENGINE_PATH = Path(__file__).resolve().parents[1] / "320_aeap_seads_run.py"
_spec = importlib.util.spec_from_file_location("aeap_seads_engine", ENGINE_PATH)
assert _spec is not None and _spec.loader is not None, "cannot load %s" % ENGINE_PATH
eng = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(eng)

INST = {"tick": 0.10, "taker_fee": 0.0005}
DCA0 = dict(spacing_pct=0.02, size_multiplier=1.0,
            breakeven_tp_pct=0.02, invalidation_pct=0.05)
MS_DAY = eng.MS_DAY
MS_HOUR = eng.MS_HOUR
EPOCH = eng.utc_ms("2024-01-01")
CHECKS = []


def ok(name):
    CHECKS.append(name)
    print("ok  %s" % name)


# --------------------------------------------------------------------------- #
# Synthetic panels                                                             #
# --------------------------------------------------------------------------- #
def synthetic_hourly(n=2160, seed=11, constant=None):
    """Contiguous hourly OHLCV panel; optional constant price (round-trip math)."""
    rng = np.random.default_rng(seed)
    if constant is None:
        close = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.005, n)))
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


def synthetic_weekly(n=40, start="2024-01-01"):
    """Weekly bars opening on consecutive Mondays (split-straddle geometry)."""
    base = eng.utc_ms(start)
    return {"open_ms": base + np.arange(n, dtype=np.int64) * eng.BAR_MS["1w"],
            "open": np.full(n, 100.0), "high": np.full(n, 101.0),
            "low": np.full(n, 99.0), "close": np.full(n, 100.0),
            "volume": np.ones(n)}


def synthetic_daily(n=900, seed=7, start="2022-01-01"):
    """Daily panel long enough for the 365-day lookback plus formation months."""
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.02, n)))
    open_ = np.r_[100.0, close[:-1]]
    high = np.maximum(open_, close) * 1.005
    low = np.minimum(open_, close) * 0.995
    volume = rng.uniform(1e5, 1e6, n)
    volume[rng.choice(n, 40, replace=False)] = 0.0     # zero-trade days exist
    return {"open_ms": eng.utc_ms(start) + np.arange(n, dtype=np.int64) * MS_DAY,
            "open": open_.astype(np.float64), "high": high.astype(np.float64),
            "low": low.astype(np.float64), "close": close.astype(np.float64),
            "volume": volume}


def synthetic_cross_section(n=900):
    return {s: synthetic_daily(n=n, seed=100 + i) for i, s in enumerate(eng.SYMBOLS)}


# --------------------------------------------------------------------------- #
# Registered domain / specs                                                     #
# --------------------------------------------------------------------------- #
def test_registered_domain():
    assert eng.expected_counts() == {
        "cohorts": 28, "strategy_cases_per_cohort": 3, "dca_configs_per_cohort": 48,
        "base_combinations_per_cohort": 144, "case_evaluations_per_grid": 4032,
        "grid_count": 10, "case_evaluations_total": 40320}
    assert len(eng.CASES) == 3 and len(eng.DCA_GRID) == 48 and len(eng.GRIDS) == 10
    assert len(eng.SYMBOLS) == 4 and len(eng.TIMEFRAMES) == 7
    assert list(eng.STRATEGY_AXES) == ["lookback_scale"]
    assert list(eng.STRATEGY_AXES["lookback_scale"]) == [0.5, 1.0, 2.0]
    assert [list(v) for v in eng.DCA_AXES.values()] == [
        [0.01, 0.02, 0.03, 0.04], [1.0, 1.1], [0.01, 0.02, 0.03], [0.05, 0.10]]
    assert eng.BASE_QUOTE == 1000.0 and eng.START_EQUITY == 30_000.0
    assert eng.MAX_ADD_LEVELS == 10 and eng.LADDER_LEVELS == 12
    assert eng.PHASES == {"historical": ("2022-01-01", "2025-09-30"),
                          "oos": ("2025-10-01", "2026-09-11"),
                          "full": ("2022-01-01", "2026-09-11")}
    keys = {eng.cell_key(r) for r in
            [{"symbol": "BTCUSDT", "timeframe": "1h", **c, **d}
             for c in eng.CASES for d in eng.DCA_GRID]}
    assert len(keys) == 144, len(keys)
    assert len({eng.cell_key(r) for r in
                [{"symbol": s, "timeframe": tf["raw_interval"], **c, **d}
                 for tf in eng.TIMEFRAMES for s in eng.SYMBOLS
                 for c in eng.CASES for d in eng.DCA_GRID]}) == 4032
    ok("registered_domain_28x3x48x10")


def test_lookback_scaling_is_record_falsification_item_3():
    assert eng.lookback_days(1.0) == (21, 126, 365)
    assert eng.lookback_days(0.5) == (11, 63, 183)      # round-half-up on +/-50%
    assert eng.lookback_days(2.0) == (42, 252, 730)
    assert [c["lookback_scale"] for c in eng.CASES] == [0.5, 1.0, 2.0]
    assert [c["label"] for c in eng.CASES] == ["lb0.5", "lb1", "lb2"]
    assert all(c["case_code"] == i for i, c in enumerate(eng.CASES))
    assert eng.lookback_days(eng.REFERENCE_SCALE) == (21, 126, 365)
    ok("lookback_plus_minus_50pct_registered_cases")


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
    assert spec["script"]["path"] == "/scripts/320_aeap_seads_run.py"
    assert len(spec["falsification"]) == 7
    assert spec["split"]["historical_end"] == "2025-09-30"
    ok("spec_identity_direct_family_no_kanban")


def test_parameter_contract():
    c = eng.parameter_contract()
    assert c["parameter_contract_version"] == 1
    assert c["family_id"] == eng.FAMILY_ID
    assert c["row_fields"] == list(eng.STRATEGY_AXES) + list(eng.DCA_AXES)
    assert c["strategy_param_fields"] == list(eng.STRATEGY_AXES)
    assert c["dca_param_fields"] == list(eng.DCA_AXES)
    assert c["row_match_recipe"]["keys"] == ["symbol", "timeframe"] + c["row_fields"]
    assert c["domain_cardinality"] == {"strategy": 3, "dca": 48, "per_cohort": 144}
    assert len(c["research_axes_ordered"]) == 5
    assert {a["name"] for a in c["research_axes_ordered"]} == set(c["row_fields"])
    rs = eng.round_spec_template()
    assert rs["parameter_contract"] == c
    assert rs["dca_domain"]["base_quote_status"] == "PROJECT_PRE_REGISTERED_CONSTANT"
    assert rs["dca_domain"]["config_count"] == 48
    for axis in eng.DCA_AXES:
        assert rs["dca_domain"][axis + "_status"] == "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN"
    ok("parameter_contract_v1_partition")


def test_signal_constants_and_falsification_registration():
    sc = eng.signal_constants()
    assert sc["lookback_scales"] == [0.5, 1.0, 2.0]
    assert sc["base_lookbacks_days"] == {"bidaskhl_days": 21, "zero_dolvol_days": 126,
                                         "trail_mean_days": 365}
    assert sc["lookback_days_by_case"]["lb2"]["trail_mean_days"] == 730
    assert sc["formation"].startswith("month-end close")
    assert "open + 1 tick" in sc["entry_timing"]
    assert "1 tick" in sc["exit_timing"]
    assert len(eng.FALSIFICATION) == 7
    assert len(eng.FALSIFICATION_REGISTRY) == 7
    assert len(set(eng.FALSIFICATION_REGISTRY)) == 7
    texts = "\n".join(eng.FALSIFICATION)
    for needle in ("record item 1", "record item 2", "record item 3",
                   "+/-50%", "record item 4", "0.8", "record item 5",
                   "random formula sampler", "record item 6", "5-20 bps", "> 50%",
                   "record item 7", "+15% YoY"):
        assert needle in texts, needle
    assert eng.RANK_IC_FLOOR == 0.01 and eng.SHARPE_FLOOR == 0.1
    assert eng.NOVELTY_MAX_CORR == 0.8
    assert eng.COST_STRESS_BPS == (5, 10, 20) and eng.COST_STRESS_CONSUMPTION == 0.50
    assert eng.REGIME_YOY == 0.15 and eng.REGIME_MIN_OBS == 3
    assert eng.RANDOM_ABLATION["formulas"] == 25
    assert eng.RANDOM_ABLATION["seed"] == eng.SEED
    assert len(eng.RANDOM_ABLATION["components"]) == 8
    assert len(eng.REFERENCE_PANEL) == 6
    factor_components = {"bidaskhl_21d", "zero_trades_126d", "dolvol_var_ratio_126d"}
    assert not (set(eng.REFERENCE_PANEL) & factor_components)   # novelty panel is not
    assert factor_components <= set(eng.RANDOM_ABLATION["components"])  # own components
    assert eng.MIN_EPISODES_IS == 10 and eng.MIN_EPISODES_OOS == 3
    assert eng.MIN_NEIGHBOUR == 0.60
    assert eng.SELECTOR_VERSION == "cohort-selector-v1"
    assert eng.DISPOSITION_VERSION == "cohort-disposition-v1"
    assert eng.DISPOSITION_MAPPING_VERSION == "v1.4.0"
    ok("falsification_7_items_registered")


def test_expected_outputs_registration():
    names = list(eng.ARTIFACTS)
    assert len(names) == 23 and len(set(names)) == 23
    for required in ("state.json", "result.json", "logs/run.log",
                     "artifacts/assertions.json", "artifacts/falsification.json",
                     "artifacts/dca_layer_histogram.json", "artifacts/cohort_results.json",
                     "artifacts/cohort_survivors.json", "artifacts/qlib_readback.json",
                     "artifacts/funding_coverage.json", "artifacts/signal_metrics.json",
                     "artifacts/stress_effects.json", "artifacts/progress.json"):
        assert required in names, required
    for g in eng.GRIDS:
        assert ("artifacts/grid_%s.csv" % g) in names, g
    ok("expected_outputs_23_registered")


# --------------------------------------------------------------------------- #
# Signal layer                                                                  #
# --------------------------------------------------------------------------- #
def test_rank_percentile_and_spearman():
    pct = eng.average_rank_percentile(np.array([3.0, 1.0, 2.0, 4.0]))
    assert np.allclose(pct, [2 / 3, 0.0, 1 / 3, 1.0])
    ties = eng.average_rank_percentile(np.array([5.0, 5.0, 1.0, 9.0]))
    assert np.allclose(ties, [0.5, 0.5, 0.0, 1.0])
    assert eng.spearman(np.array([1.0, 2.0, 3.0]), np.array([1.0, 2.0, 3.0])) == 1.0
    assert eng.spearman(np.array([1.0, 2.0, 3.0]), np.array([3.0, 2.0, 1.0])) == -1.0
    assert eng.spearman(np.array([1.0, 1.0, 1.0]), np.array([1.0, 2.0, 3.0])) is None
    ok("rank_percentile_and_spearman")


def test_next_month_returns_is_per_symbol():
    daily = synthetic_cross_section(n=500)
    out, months = eng.next_month_returns(daily)
    assert len(months) == 500
    assert len(out) >= 10
    first = next(iter(out.values()))
    assert set(first) == set(eng.SYMBOLS)
    for row in out.values():
        for sym in eng.SYMBOLS:
            assert math.isfinite(row[sym])
    ok("next_month_returns_per_symbol")


def test_formation_and_episode_bounds():
    daily = synthetic_daily(n=400)
    f_idx, f_months = eng.formation_indices(daily["open_ms"])
    months = eng.month_ordinals(daily["open_ms"])
    assert f_months[-1] == months[-1]                     # last bar of the last month
    assert np.all(np.diff(f_idx) > 0)
    ep_start, ep_end, months2 = eng.episode_bounds(daily["open_ms"])
    assert len(ep_start) == len(ep_end) == len(months2) == 400
    assert ep_start[0] == 0 and ep_end[-1] == 399
    # every bar of a month shares one episode, and the episode covers exactly that month
    for i in (0, 100, 250, 399):
        assert months2[ep_start[i]] == months2[ep_end[i]] == months2[i]
        assert ep_start[i] <= i <= ep_end[i]
        assert ep_end[i] - ep_start[i] + 1 == int(np.sum(months2 == months2[i]))
    ok("formation_and_episode_bounds")


def test_signal_direction_rule_and_pit():
    daily = synthetic_cross_section(n=900)
    case = [c for c in eng.CASES if c["lookback_scale"] == 1.0][0]
    signal = eng.build_case_signal(case, daily)
    assert len(signal["formations"]) > 0
    for row in signal["formations"]:
        assert row["cross_section"] == 4 and row["coverage"] == 1.0
        d = row["direction"]
        assert sorted(d.values()) == [-1, 0, 0, 1], d          # exactly one long, one short
        assert set(d) == set(eng.SYMBOLS)
    months = sorted(signal["directions"])
    assert months == sorted(set(months))
    # direction broadcast onto a bar grid keeps the month's own decision only
    hourly = synthetic_hourly(n=2160)
    series = eng.direction_for_symbol(signal, hourly["open_ms"], eng.SYMBOLS[0])
    assert series.dtype == np.int8 and len(series) == 2160
    assert set(np.unique(series)) <= {-1, 0, 1}
    pit = eng.pit_recheck(case, daily, signal)
    assert pit["checked"] >= 3 and pit["failed_samples"] == 0, pit
    ok("signal_direction_rule_and_pit_suffix_recheck")


def test_rank_ic_series_and_long_short_returns():
    daily = synthetic_cross_section(n=900)
    case = [c for c in eng.CASES if c["lookback_scale"] == 1.0][0]
    signal = eng.build_case_signal(case, daily)
    ic, skipped = eng.rank_ic_series(signal, daily)
    assert ic, "Rank-IC series must exist"
    for v in ic.values():
        assert -1.0 - 1e-9 <= v <= 1.0 + 1e-9
    ls = eng.long_short_returns(signal, daily)
    assert ls and all(math.isfinite(v) for v in ls.values())
    assert set(ls) <= set(ic) | set(skipped)
    ok("rank_ic_and_long_short_diagnostics")


def test_window_indices_close_time_split():
    """A weekly bar may not carry its prices past a frozen split boundary."""
    panel = synthetic_weekly(n=40, start="2024-01-01")   # Mondays from 2024-01-01
    bar_ms = eng.BAR_MS["1w"]
    phases = {"historical": ("2024-01-01", "2024-06-30"),
              "oos": ("2024-07-01", "2024-09-30"),
              "full": ("2024-01-01", "2024-09-30")}
    w = {p: eng.window_indices(panel["open_ms"], bar_ms, *phases[p]) for p in phases}
    h, o, f = w["historical"], w["oos"], w["full"]
    assert h[1] == o[0], (h, o)                    # adjacent, no bar in two splits
    assert f == (h[0], o[1])
    closes = panel["open_ms"] + bar_ms
    hist_end = eng.utc_ms("2024-06-30") + MS_DAY
    assert closes[h[1] - 1] <= hist_end             # last historical bar closes in-window
    assert closes[h[1]] > hist_end                  # the straddling week goes to OOS
    assert h[0] == 0
    # the final week opens on the window's last day and closes past the registered
    # end, so close-time attribution must keep it out of every split
    assert f[1] == 39, f
    assert closes[39] > eng.utc_ms("2024-09-30") + MS_DAY
    # a non-weekly grid is unaffected: hourly bars close exactly on the boundary
    hourly = synthetic_hourly(n=2160)
    wh = {p: eng.window_indices(hourly["open_ms"], MS_HOUR, *phases[p]) for p in phases}
    assert wh["historical"][1] == wh["oos"][0]
    assert wh["full"] == (wh["historical"][0], wh["oos"][1])
    ok("window_indices_close_time_split")


# --------------------------------------------------------------------------- #
# Execution rail                                                                #
# --------------------------------------------------------------------------- #
def test_simulate_adverse_tick_roundtrip():
    panel = synthetic_hourly(n=2160, constant=100.0)
    n = len(panel["open"])
    ep_start, ep_end, months = eng.episode_bounds(panel["open_ms"])
    episodes = int(len(np.unique(months)))
    assert episodes == 3, episodes
    for direction_value in (1, -1):
        direction = np.full(n, direction_value, dtype=np.int8)
        m = eng.simulate(panel, [[] for _ in range(n)], direction, ep_start, ep_end,
                         DCA0, 0, n, {"slip_ticks": 1},
                         {"tick": 0.10, "taker_fee": 0.0})
        qty = eng.BASE_QUOTE * eng.LEVERAGE / (100.0 + 0.10 * direction_value)
        expected = episodes * (-2.0 * 0.10 * abs(qty))
        assert m["episodes"] == episodes, (m["episodes"], episodes)
        assert math.isclose(m["net_pnl"], expected, rel_tol=1e-9), (m["net_pnl"], expected)
        assert m["month_exits"] == episodes          # registered monthly rebalance exit
        assert m["fees"] == 0.0 and m["funding"] == 0.0
        assert m["decomposition_ok"] is True
        assert m["open_at_end"] == 0
        assert m["layer_hist"][0] == episodes
        assert sum(m["layer_hist"][1:]) == episodes
        assert m["adds"] == 0                        # price never reaches the ladder
    ok("simulate_adverse_tick_both_sides")


def test_simulate_episode_accounting_and_funding():
    panel = synthetic_hourly(n=2160, seed=4)
    n = len(panel["open"])
    ep_start, ep_end, months = eng.episode_bounds(panel["open_ms"])
    direction = np.ones(n, dtype=np.int8)            # one book per calendar month
    events = [[] for _ in range(n)]
    events[1] = [(int(panel["open_ms"][1]) + 8, 0.0001 * 100.0)]     # official stamp
    m = eng.simulate(panel, events, direction, ep_start, ep_end, DCA0, 0, n, {}, INST)
    assert m["episodes"] == 3, m["episodes"]
    assert m["month_exits"] + m["stop_hits"] + m["tp_hits"] + m["margin_calls"] \
        + m["end_exits"] + m["signal_flat_exits"] >= m["episodes"]
    assert m["layer_hist"][0] == m["episodes"]
    assert sum(m["layer_hist"][1:]) == m["episodes"]
    assert sum(m["layer_hist"]) == 2 * m["episodes"]
    assert m["open_at_end"] == 0
    assert m["fills"] == 2 * m["episodes"] + m["adds"], m
    assert math.isclose(m["net_pnl"], m["gross_pnl"] - m["fees"] - m["funding"],
                        abs_tol=1e-3)
    assert m["decomposition_ok"] is True
    assert m["funding"] != 0.0                       # charged once, while live
    assert m["turnover_usdt"] > 0
    assert m["max_effective_leverage"] <= eng.LEVERAGE + 1e-9
    assert m["capital_utilization"] <= 1.0 + 1e-9
    ok("simulate_episode_funding_and_decomposition")


def test_simulate_entry_is_the_episode_first_bar():
    panel = synthetic_hourly(n=2160, constant=100.0)
    n = len(panel["open"])
    ep_start, ep_end, months = eng.episode_bounds(panel["open_ms"])
    direction = np.ones(n, dtype=np.int8)
    no_delay = eng.simulate(panel, [[] for _ in range(n)], direction, ep_start, ep_end,
                            DCA0, 0, n, {}, INST)
    delayed = eng.simulate(panel, [[] for _ in range(n)], direction, ep_start, ep_end,
                           DCA0, 0, n, {"entry_delay": 1}, INST)
    # one bar later at a fixed price costs nothing, but the episode count must hold
    assert no_delay["episodes"] == delayed["episodes"] == 3
    assert math.isclose(no_delay["net_pnl"], delayed["net_pnl"], rel_tol=1e-9)
    # a window that starts mid-episode never joins that book late
    late = eng.simulate(panel, [[] for _ in range(n)], direction, ep_start, ep_end,
                        DCA0, 50, n, {}, INST)
    assert late["episodes"] <= 2, late["episodes"]
    ok("simulate_entry_gate_at_episode_start")


def test_cost_stresses_change_net_not_gross():
    panel = synthetic_hourly(n=2160, seed=6)
    n = len(panel["open"])
    ep_start, ep_end, _ = eng.episode_bounds(panel["open_ms"])
    direction = np.ones(n, dtype=np.int8)
    events = [[] for _ in range(n)]
    events[1] = [(int(panel["open_ms"][1]) + 8, 0.0001 * 100.0)]
    base = eng.simulate(panel, events, direction, ep_start, ep_end, DCA0, 0, n, {}, INST)
    assert base["episodes"] == 3 and base["funding"] != 0.0
    for grid, changes_gross in (("fee_2x", False), ("cost_attrition_40bps", False),
                                ("funding_2x", False), ("slippage_2ticks", True),
                                ("entry_delay_1_bar", True)):
        cost = eng.cost_for(grid)
        m = eng.simulate(panel, events, direction, ep_start, ep_end, DCA0, 0, n,
                         cost, INST)
        assert not math.isclose(m["net_pnl"], base["net_pnl"], abs_tol=1e-9), grid
        if not changes_gross:
            assert math.isclose(m["gross_pnl"], base["gross_pnl"], abs_tol=1e-9), grid
    if eng.cost_for("slippage_2ticks")["slip_ticks"] == 2:
        pass
    assert eng.cost_for("fee_2x") == {"fee_mult": 2.0}
    assert eng.cost_for("cost_attrition_40bps") == {"fee_override": 0.004}
    assert eng.cost_for("funding_2x") == {"funding_mult": 2.0}
    assert eng.cost_for("entry_delay_1_bar") == {"entry_delay": 1}
    assert eng.cost_for("slippage_2ticks") == {"slip_ticks": 2}
    assert eng.cost_for("historical") == {} and eng.cost_for("full") == {}
    nofunding = eng.simulate(panel, events, direction, ep_start, ep_end, DCA0, 0, n,
                             eng.cost_for("no_funding"), INST)
    assert nofunding["funding"] == 0.0
    assert math.isclose(nofunding["gross_pnl"], base["gross_pnl"], abs_tol=1e-9)
    assert not math.isclose(nofunding["net_pnl"], base["net_pnl"], abs_tol=1e-9)
    ok("cost_stresses_change_net_not_gross")


def test_ladder_scale_ins_and_reserve():
    n = 180
    rng = np.random.default_rng(3)
    path = 100.0 * np.cumprod(np.r_[1.0, np.full(60, 0.997), np.full(n - 61, 1.0)])
    open_ = np.r_[100.0, path[:-1]]
    panel = {"open_ms": EPOCH + np.arange(n, dtype=np.int64) * MS_HOUR,
             "open": open_, "high": np.maximum(open_, path) * 1.0001,
             "low": np.minimum(open_, path) * 0.9999, "close": path,
             "volume": np.ones(n)}
    ep_start, ep_end, _ = eng.episode_bounds(panel["open_ms"])
    dca = dict(spacing_pct=0.01, size_multiplier=1.0,
               breakeven_tp_pct=0.03, invalidation_pct=0.10)
    direction = np.ones(n, dtype=np.int8)
    m = eng.simulate(panel, [[] for _ in range(n)], direction, ep_start, ep_end,
                     dca, 0, n, {}, INST)
    assert m["adds"] >= 5, m["adds"]
    assert m["adds"] <= eng.MAX_ADD_LEVELS * m["episodes"]      # tranche #12 reserve
    assert m["layer_hist"][0] == m["episodes"]
    assert sum(m["layer_hist"][1:]) == m["episodes"]
    assert m["max_effective_leverage"] <= eng.LEVERAGE + 1e-9
    assert m["decomposition_ok"] is True
    assert m["open_at_end"] == 0
    assert rng is not None
    ok("ladder_scale_ins_reserve_and_histogram")


# --------------------------------------------------------------------------- #
# Selector / disposition                                                        #
# --------------------------------------------------------------------------- #
def cohort_rows(net_h=1000.0, net_oos=500.0, net_full=1500.0, stress=100.0,
                sharpe_h=1.0, sharpe_oos=1.0, episodes_h=40, episodes_oos=12,
                stress_grids=("fee_2x", "funding_2x", "entry_delay_1_bar",
                              "slippage_2ticks"), negative_grids=()):
    """One cohort's full registered cell set with tunable economics."""
    rows = {g: [] for g in eng.GRIDS}
    for case in eng.CASES:
        for dca in eng.DCA_GRID:
            for grid in eng.GRIDS:
                m = eng.empty_metric()
                m.update(episodes=episodes_h if grid == "historical" else episodes_oos,
                         gross_pnl=net_h, fees=1.0, funding=0.0,
                         sharpe=sharpe_h if grid == "historical" else sharpe_oos)
                net = net_h if grid == "historical" else (
                    net_oos if grid == "oos" else net_full)
                if grid in stress_grids:
                    net = stress
                if grid in negative_grids:
                    net = -abs(net)
                m.update(net_pnl=net, ending_equity=eng.START_EQUITY + net)
                rows[grid].append({"symbol": "BTCUSDT", "timeframe": "1h",
                                   "lookback_scale": case["lookback_scale"],
                                   "case_label": case["label"], **dca, "grid": grid,
                                   **eng.metric_block(m), "decomposition_ok": True})
    return rows


def test_selector_survivor_semantics():
    rows = cohort_rows()
    selected, info = eng.select_cohort(rows)
    assert selected is not None, info
    assert info["cull_reasons"] == []
    assert info["winner_source_grid"] == "historical"
    # all rows tie, so the registered-index tie-break picks the first (corner) cell,
    # which has 5 legal face neighbours (one per axis step available at a corner)
    assert info["neighbourhood"]["neighbours"] == 5
    assert info["neighbourhood"]["passed"] is True
    assert set(info["winner"]) == {"lookback_scale", *eng.DCA_AXES}
    assert info["phases"]["historical"]["net_pnl"] == 1000.0
    assert info["robustness"]["fee_2x"]["net_pnl"] == 100.0

    _, info = eng.select_cohort(cohort_rows(episodes_h=4))
    assert info["cull_reasons"] == ["insufficient_trades"]

    _, info = eng.select_cohort(cohort_rows(net_h=-1.0))
    assert info["cull_reasons"] == ["no_qualifying_candidate"]

    _, info = eng.select_cohort(cohort_rows(net_oos=-1.0))
    assert info["cull_reasons"] == ["oos_economic"]

    _, info = eng.select_cohort(cohort_rows(episodes_oos=1))
    assert info["cull_reasons"] == ["oos_economic"]

    _, info = eng.select_cohort(cohort_rows(net_full=-1.0))
    assert info["cull_reasons"] == ["full_economic"]

    _, info = eng.select_cohort(cohort_rows(stress=-1.0))
    assert info["cull_reasons"][0].startswith("robustness_economic:")
    assert set(info["cull_reasons"][0].split(":", 1)[1].split(",")) == set(eng.STRESS_GRIDS)

    _, info = eng.select_cohort(cohort_rows(negative_grids=("fee_2x",)))
    assert info["cull_reasons"] == ["robustness_economic:fee_2x"]
    ok("selector_cull_reasons_and_survivor")


def test_selector_is_deterministic_and_historical_only():
    rows_a = cohort_rows(sharpe_h=1.0, net_h=1000.0)
    rows_b = cohort_rows(sharpe_h=1.0, net_h=1000.0)
    a, ia = eng.select_cohort(rows_a)
    b, ib = eng.select_cohort(rows_b)
    assert a == b and ia["winner"] == ib["winner"]
    # ordering: Sharpe desc, net_pnl desc, then registered-index lexical tie-break
    order = eng._order_key({"sharpe": 2.0, "net_pnl": 1.0, "lookback_scale": 1.0,
                            **{k: eng.DCA_AXES[k][0] for k in eng.DCA_AXES}})
    better = eng._order_key({"sharpe": 2.0, "net_pnl": 9.0, "lookback_scale": 1.0,
                             **{k: eng.DCA_AXES[k][0] for k in eng.DCA_AXES}})
    assert better < order                      # same sharpe -> larger net_pnl wins
    lower_scale = eng._order_key({"sharpe": 2.0, "net_pnl": 1.0, "lookback_scale": 0.5,
                                  **{k: eng.DCA_AXES[k][0] for k in eng.DCA_AXES}})
    assert lower_scale < order                 # registered index 0 breaks the tie
    ok("selector_deterministic_and_historical_only")


def test_neighbourhood_threshold():
    rows = cohort_rows()
    winner = next(r for r in rows["historical"]
                  if r["lookback_scale"] == 1.0 and r["spacing_pct"] == 0.02
                  and r["size_multiplier"] == 1.0 and r["breakeven_tp_pct"] == 0.02
                  and r["invalidation_pct"] == 0.05)
    neigh = eng.neighbourhood(winner, rows["historical"])
    assert neigh["neighbours"] == 8 and neigh["agreeing"] == 8
    assert neigh["passed"] is True
    # the eight legal face neighbours of this interior winner cell
    domains = [list(eng.STRATEGY_AXES["lookback_scale"])] + [list(v)
                                                              for v in eng.DCA_AXES.values()]
    base = [winner["lookback_scale"], winner["spacing_pct"], winner["size_multiplier"],
            winner["breakeven_tp_pct"], winner["invalidation_pct"]]
    neighbour_tuples = []
    for pos, dom in enumerate(domains):
        ix = dom.index(base[pos])
        for delta in (-1, 1):
            if 0 <= ix + delta < len(dom):
                cand = list(base)
                cand[pos] = dom[ix + delta]
                neighbour_tuples.append(tuple(cand))
    assert len(neighbour_tuples) == 8, neighbour_tuples

    def param_key(r):
        return (r["lookback_scale"], r["spacing_pct"], r["size_multiplier"],
                r["breakeven_tp_pct"], r["invalidation_pct"])

    def flip(hist, count):
        done = 0
        for r in hist:
            if param_key(r) in set(neighbour_tuples) and done < count:
                r["net_pnl"] = -1.0
                done += 1
        return done

    # flip exactly one neighbour's sign -> 7/8 = 0.875 still passes at 60%
    rows2 = cohort_rows()
    assert flip(rows2["historical"], 1) == 1
    neigh = eng.neighbourhood(winner, rows2["historical"])
    assert neigh["neighbours"] == 8 and neigh["agreeing"] == 7
    assert neigh["passed"] is True
    # flip five neighbours -> 3/8 = 0.375 fails the 60% gate
    rows3 = cohort_rows()
    assert flip(rows3["historical"], 5) == 5
    neigh = eng.neighbourhood(winner, rows3["historical"])
    assert neigh["agreeing"] == 3 and neigh["passed"] is False
    try:
        eng.neighbourhood(winner, cohort_rows()["oos"])
        raise AssertionError("selector must refuse non-historical rows")
    except ValueError:
        pass
    ok("neighbourhood_60pct_face_neighbours")


def test_phase_mapping_and_grid_csv():
    assert eng.phase_for("historical") == "historical"
    assert eng.phase_for("no_funding") == "historical"
    assert eng.phase_for("oos") == "oos"
    for g in eng.GRIDS:
        if g not in ("historical", "no_funding", "oos"):
            assert eng.phase_for(g) == "full", g
    rows = cohort_rows()["full"]
    assert len(rows) == 144
    assert len({eng.cell_key(r) for r in rows}) == 144
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "grid_full.csv"
        eng.write_grid(path, rows)
        header = path.read_text(encoding="utf-8").splitlines()[0].split(",")
        assert header[:8] == ["symbol", "timeframe", "lookback_scale", "case_label",
                              *eng.DCA_AXES]
        assert header[-1] == "decomposition_ok"
        for key in ("net_pnl", "sharpe", "max_dd_pct", "ending_equity",
                    "turnover_usdt", "capital_utilization", "month_exits"):
            assert key in header, key
        assert len(path.read_text(encoding="utf-8").splitlines()) == 145
    ok("phase_mapping_and_grid_csv")


def test_metric_block_daily_marks_shape():
    empty = eng.empty_metric()
    assert set(eng.metric_block(empty)) == set(eng.METRIC_KEYS)
    assert "layer_hist" not in eng.metric_block(empty)
    assert empty["layer_hist"] == [0] * eng.LADDER_LEVELS
    panel = synthetic_hourly(n=2160, constant=100.0)
    n = len(panel["open"])
    ep_start, ep_end, _ = eng.episode_bounds(panel["open_ms"])
    m = eng.simulate(panel, [[] for _ in range(n)], np.ones(n, dtype=np.int8),
                     ep_start, ep_end, DCA0, 0, n, {}, INST)
    assert m["ending_equity"] == eng.START_EQUITY + m["net_pnl"] \
        or math.isclose(m["ending_equity"], eng.START_EQUITY + m["net_pnl"], abs_tol=1e-6)
    assert m["annualized_return"] < 0.0            # round-trip slippage costs money
    assert m["max_dd_pct"] >= 0.0 and m["max_dd_usdt"] >= 0.0
    assert m["turnover_usdt"] > 0 and m["fills"] > 0
    ok("metric_block_daily_marks")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
    print("ALL %d CHECKS PASSED (%s)" % (len(CHECKS), ", ".join(CHECKS)))
    sys.exit(0)
