#!/usr/bin/env python3
"""Self-check for the SciPhy direct-family Qlib runner (contract 16 / body preflight).

Run inside the production image (the spec checks read /data/raw/_meta/CONFIG.json):

    container exec qlib-run /opt/venv/bin/python /scripts/tests/test_sciphy_rl_engine.py

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

ENGINE_PATH = Path(__file__).resolve().parents[1] / "270_sciphy_rl_run.py"
_spec = importlib.util.spec_from_file_location("sciphy_rl_engine", ENGINE_PATH)
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
        rng = np.random.default_rng(20260928)
        close = 100.0 * np.exp(np.cumsum(rng.normal(0.0004, 0.02, n)))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * 1.01
    low = np.minimum(open_, close) * 0.99
    return {"open_ms": ms, "open": open_, "high": high, "low": low,
            "close": close, "volume": np.full(n, 1_000_000.0)}


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
           "episode_days": case["episode_days"], "case_label": case["label"],
           **dca, "grid": grid,
           **eng.metric_block(eng.empty_metric(0, 0)),
           "decomposition_ok": True}
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
    return case["episode_days"] == 31 and all(dca[k] == v for k, v in WINNER_CELL.items())


# --------------------------------------------------------------------------------------
def test_frozen_domain_registration():
    spec = eng.run_spec_template()
    assert [p["episode_days"] for p in spec["params"]] == [31, 63], spec["params"]
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
    assert spec["expected"] == {"cohorts": 4, "strategy_cases_per_cohort": 2,
                                "dca_configs_per_cohort": 48, "base_combinations_per_cohort": 96,
                                "case_evaluations_per_grid": 384, "grid_count": 10,
                                "case_evaluations_total": 3840}
    assert list(eng.GRIDS) == ["historical", "oos", "full", "fee_2x", "funding_2x",
                               "entry_delay_1_bar", "slippage_2ticks", "no_funding",
                               "no_funding_full", "cost_attrition_40bps"]
    assert spec["gates"] == {"min_episodes_is": 10, "min_episodes_oos": 3,
                             "min_neighbour_same_sign_fraction": 0.60}
    assert spec["selector_version"] == "cohort-selector-v1"
    assert spec["disposition_version"] == "cohort-disposition-v1"
    assert len(spec["falsification"]) == 4
    assert list(spec["expected_outputs"]) == list(eng.ARTIFACTS)
    assert spec["signal_constants"] == dict(eng.SIGNAL_PARAMS)
    assert spec["script"]["path"] == "/scripts/270_sciphy_rl_run.py"
    assert spec["data"]["signal_timeframe"] == {"raw_interval": "1d", "qlib_freq": "day"}
    assert spec["split"]["historical_end"] == "2025-09-30"
    assert spec["split"]["oos_start"] == "2025-10-01"
    assert not any(k in spec for k in ("task_id", "kanban_task_id", "kanban_board"))
    assert eng.expected_counts()["case_evaluations_total"] == 4 * 2 * 48 * 10


def test_spec_validation_and_rejections():
    if not Path(eng.RAW_ROOT + "/_meta/CONFIG.json").is_file():
        print("  [skip] /data/raw/_meta/CONFIG.json not present (host run?)")
        return
    ok = build_spec()
    assert eng.validate_spec(ok)["case_evaluations_total"] == 3840

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
    rejects(lambda s: s["params"].append({"case_code": 2, "episode_days": 126,
                                          "label": "t126"}), "extra strategy case")
    rejects(lambda s: s["dca_domain"]["spacing_pct"].append(0.05), "DCA axis widened")
    rejects(lambda s: s["dca_domain"].update(base_quote_status="USER_FIXED"),
            "provenance class rewrite")
    rejects(lambda s: s["expected"].update(case_evaluations_total=3839), "coverage rewrite")
    rejects(lambda s: s["signal_constants"].update(alpha1=0.3), "published constant rewrite")
    rejects(lambda s: s["script"].update(sha256="sha256:" + "0" * 64), "script pin mismatch")
    rejects(lambda s: s.update(selector_version="cohort-selector-v2"), "selector rewrite")
    rejects(lambda s: s.update(round_id="some-other-family-r1"), "round identity")
    rejects(lambda s: s["dca_domain"].update(grid=s["dca_domain"]["grid"][:47]), "grid shrink")


def test_signal_constants_formula_and_oracle_lookahead():
    assert (eng.SIGNAL_PARAMS["alpha1"], eng.SIGNAL_PARAMS["beta1"],
            eng.SIGNAL_PARAMS["phi"]) == (0.2672, 0.2943, -0.0831)
    assert eng.SIGNAL_PARAMS["memory_share_m"] == 0.3
    assert eng.SIGNAL_PARAMS["informative_variance_q"] == 0.1
    assert eng.SIGNAL_PARAMS["rho_asset"] == -0.0229
    assert eng.SIGNAL_PARAMS["ewma_span"] == 3
    assert eng.PIT_STATUS == "FAIL_LOOKAHEAD_BY_SOURCE_DESIGN"

    panel = make_panel(300)
    layer = eng.build_signal(panel, seed_offset=0)
    d = layer["direction"]
    assert set(np.unique(d)) <= {-1, 0, 1}
    assert np.all(d[:eng.SIGNAL_PARAMS["warmup_days"] + 1] == 0), "signal must be undefined in warmup"
    assert np.count_nonzero(d) > 50, "signal must actually form"
    assert layer["oracle_r2_full"] is not None and 0.0 < layer["oracle_r2_full"] < 1.0

    # Determinism: same seed -> identical signal, different noise draw -> different signal.
    again = eng.build_signal(panel, seed_offset=0)
    assert np.array_equal(np.nan_to_num(again["zeta"]), np.nan_to_num(layer["zeta"]))
    other = eng.build_signal(panel, seed_offset=1)
    assert not np.allclose(np.nan_to_num(other["zeta"]), np.nan_to_num(layer["zeta"]),
                           equal_nan=True)

    # The registered oracle term is a REAL look-ahead, pinned here so it can never be
    # silently described as point-in-time: zeta at k-1 must move when day k's return moves.
    k = 200
    touched = {key: (val.copy() if isinstance(val, np.ndarray) else val)
               for key, val in panel.items()}
    touched["close"][k] *= 1.10
    touched["high"][k] = max(touched["high"][k], touched["close"][k])
    tainted = eng.build_signal(touched, seed_offset=0)
    assert not np.isclose(layer["zeta"][k - 1], tainted["zeta"][k - 1]), \
        "zeta_{k-1} must embed day k's return (look-ahead by source design)"


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
    assert m["adds"] >= 0

    # Negative control: gross_pnl comes from an independent price-PnL accumulator, so
    # removing every fee must leave gross untouched while net moves by exactly the fees.
    free = eng.simulate(panel, funding, direction, case, DCA, 50, 250,
                        {"fee_mult": 0.0}, INST)
    assert abs(free["fees"]) < 1e-9
    assert abs(free["gross_pnl"] - m["gross_pnl"]) < 1e-6, "gross must not be back-solved from net"
    assert abs((free["net_pnl"] - m["net_pnl"]) - m["fees"]) < 1e-3, \
        "removing every fee must lift net by exactly the fees charged"
    assert free["net_pnl"] > m["net_pnl"], "taker fees must actually bite"
    # And the decomposition tolerance is not vacuous: perturb fees by 1 USDT and it breaks.
    assert abs(m["gross_pnl"] - (m["fees"] + 1.0) - m["funding"] - m["net_pnl"]) >= 1e-3


def test_registered_stress_grids_all_move_net():
    panel = make_panel(300)
    direction = flipping_direction(300)
    funding = positive_funding(panel)
    case = eng.CASES[0]
    base = eng.simulate(panel, funding, direction, case, DCA, 50, 250, {}, INST)
    lower = {"fee_2x": {"fee_mult": 2.0}, "slippage_2ticks": {"slip_ticks": 2},
             "cost_attrition_40bps": {"fee_override": 0.004}}
    for grid, cost in lower.items():
        m = eng.simulate(panel, funding, direction, case, DCA, 50, 250, cost, INST)
        assert m["net_pnl"] < base["net_pnl"], "%s must reduce net" % grid
        assert abs(m["net_pnl"] - base["net_pnl"]) > 1e-9
    for grid, cost in {"entry_delay_1_bar": {"entry_delay": 1},
                       "funding_2x": {"funding_mult": 2.0},
                       "no_funding": {"no_funding": True}}.items():
        m = eng.simulate(panel, funding, direction, case, DCA, 50, 250, cost, INST)
        assert abs(m["net_pnl"] - base["net_pnl"]) > 1e-9, "%s was a no-op" % grid
    # cost grid mapping itself is the registered one
    assert eng.cost_for("fee_2x") == {"fee_mult": 2.0}
    assert eng.cost_for("cost_attrition_40bps") == {"fee_override": 0.004}
    assert eng.cost_for("historical") == {}


def test_episode_horizon_tiling_binds_the_strategy_axis():
    """The T axis must actually change execution: flat prices mean only the episode
    boundary (never a TP/invalidation) can close a leg, so terminal counts are exact."""
    panel = make_panel(300, kind="flat")
    direction = np.ones(300, dtype=np.int8)
    funding = [[] for _ in range(300)]
    m31 = eng.simulate(panel, funding, direction, eng.CASES[0], DCA, 50, 250, {}, INST)
    m63 = eng.simulate(panel, funding, direction, eng.CASES[1], DCA, 50, 250, {}, INST)
    # window offsets 0..199; boundaries at multiples of T; the offset-0 boundary starts flat
    assert m31["terminal_exits"] == len([o for o in range(31, 200, 31)]) == 6
    assert m63["terminal_exits"] == len([o for o in range(63, 200, 63)]) == 3
    assert m31["terminal_exits"] > m63["terminal_exits"]
    assert m31["tp_hits"] == 0 and m31["stop_hits"] == 0, "flat prices: no barrier exit"
    assert m31["episodes"] == 1 + 6 and m63["episodes"] == 1 + 3
    assert m31["end_exits"] == 1 and m63["end_exits"] == 1
    assert m31["net_pnl"] < 0 < eng.START_EQUITY + m31["net_pnl"]
    assert m31["net_pnl"] != m63["net_pnl"], "the two strategy cases must not be identical"
    assert m31["decomposition_ok"] and m63["decomposition_ok"]


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
    assert info["winner"] == {**{k: selected[k] for k in ("episode_days", *eng.DCA_AXES)}}
    assert set(info["winner"]) == {"episode_days", "spacing_pct", "size_multiplier",
                                   "breakeven_tp_pct", "invalidation_pct"}
    assert info["winner_source_grid"] == "historical"
    assert info["cull_reasons"] == []
    # face neighbours of the lexicographically first cell: 1 strategy + 1 + 1 + 1 + 1
    assert info["neighbourhood"]["neighbours"] == 5, info["neighbourhood"]
    assert info["neighbourhood"]["passed"] is True
    assert info["neighbourhood"]["agreeing"] == 5
    assert set(info["metrics"]) >= {"historical", "oos", "full", "robustness", "neighbourhood"}
    assert set(info["metrics"]["robustness"]) == {"fee_2x", "funding_2x",
                                                  "entry_delay_1_bar", "slippage_2ticks"}
    assert info["metrics"]["oos"]["sharpe"] > 0 and info["metrics"]["full"]["net_pnl"] > 0

    # tie-break: identical sharpe/net -> registered axis index order (episode_days first)
    def tied_fn(grid, case, dca):
        if grid == "historical":
            return 10.0, 1.0, 50
        return 10.0, 1.0, 20

    tied, tied_info = eng.select_cohort(rows_from(tied_fn))
    assert tied is not None
    assert (tied["episode_days"], tied["spacing_pct"], tied["size_multiplier"],
            tied["breakeven_tp_pct"], tied["invalidation_pct"]) == \
           (31, 0.01, 1.0, 0.01, 0.05), tied

    # OOS economics cull the frozen winner (no re-selection after seeing OOS)
    def oos_fail(grid, case, dca):
        win = is_winner(case, dca)
        if grid == "historical":
            return (100.0 if win else 10.0), (2.0 if win else 0.5), 50
        if grid == "oos":
            return (-5.0 if win else 5.0), (0.5 if win else 0.3), 20
        return (50.0 if win else 5.0), (1.0 if win else 0.3), 20

    _, info = eng.select_cohort(rows_from(oos_fail))
    assert info["cull_reasons"] == ["oos_economic"], info["cull_reasons"]

    # robustness cull: winner loses under fee_2x only
    def robust_fail(grid, case, dca):
        win = is_winner(case, dca)
        if grid == "historical":
            return (100.0 if win else 10.0), (2.0 if win else 0.5), 50
        if grid == "fee_2x":
            return (-7.0 if win else 5.0), (-0.4 if win else 0.3), 20
        return (50.0 if win else 5.0), (1.0 if win else 0.3), 20

    _, info = eng.select_cohort(rows_from(robust_fail))
    assert info["cull_reasons"] == ["robustness_economic:fee_2x"], info["cull_reasons"]

    # neighbourhood cull: winner positive, every face neighbour negative in historical
    def neigh_fail(grid, case, dca):
        win = is_winner(case, dca)
        if grid == "historical":
            return (100.0 if win else -1.0), (2.0 if win else -0.5), 50
        return (50.0 if win else -1.0), (1.0 if win else -0.2), 20

    _, info = eng.select_cohort(rows_from(neigh_fail))
    assert info["cull_reasons"] == ["parameter_neighbourhood"], info["cull_reasons"]

    # sufficiency gate
    _, info = eng.select_cohort(rows_from(lambda g, c, d: (1.0, 0.1, 9)))
    assert info["cull_reasons"] == ["insufficient_trades"], info["cull_reasons"]
    # no qualifying candidate
    _, info = eng.select_cohort(rows_from(lambda g, c, d: (-1.0, -0.1, 50)))
    assert info["cull_reasons"] == ["no_qualifying_candidate"], info["cull_reasons"]
    # the selector may only ever read historical rows
    bad = rows_from(survivor_fn)
    bad["historical"][0]["grid"] = "oos"
    try:
        eng.select_cohort(bad)
    except ValueError:
        pass
    else:
        raise AssertionError("selector accepted a non-historical row")


def test_grid_csv_contract_and_decay_causality():
    rows = rows_from(lambda g, c, d: (1.0, 0.1, 5))
    tmp = Path(tempfile.mkdtemp(prefix="sciphy-test-"))
    try:
        path = tmp / "grid_historical.csv"
        eng.write_grid(path, rows["historical"])
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            header = reader.fieldnames
            body = list(reader)
        expected = ["symbol", "timeframe", "episode_days", "case_label", *eng.DCA_AXES,
                    "grid", *eng.metric_block(eng.empty_metric(0, 0)), "decomposition_ok"]
        assert header == expected, (header, expected)
        assert len(body) == 96 == 2 * 48
        assert body[0]["symbol"] == "BTCUSDT" and body[0]["timeframe"] == "1d"
        assert float(body[0]["net_pnl"]) == 1.0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # falsification item 2's substitute signal must be causal (no oracle term inside it)
    panel = make_panel(300)
    d0 = eng.decay_direction(panel)
    touched = {k: (v.copy() if isinstance(v, np.ndarray) else v) for k, v in panel.items()}
    touched["close"][-1] *= 3.0
    d1 = eng.decay_direction(touched)
    assert np.array_equal(d0[:-1], d1[:-1]), "decay signal must not read the future"
    assert set(np.unique(d0)) <= {-1, 0, 1}


def test_round_spec_parameter_contract_when_present():
    """Best-effort cross-check of the frozen round-spec against this engine's registered
    domain; skipped when the round-spec has not been written yet."""
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
    assert list(axes) == ["episode_days", "spacing_pct", "size_multiplier",
                          "breakeven_tp_pct", "invalidation_pct"]
    assert axes["episode_days"]["registered_values"] == [31, 63]
    for name, domain in eng.DCA_AXES.items():
        assert axes[name]["registered_values"] == list(domain), name
    assert contract["row_fields"] == ["episode_days", "spacing_pct", "size_multiplier",
                                      "breakeven_tp_pct", "invalidation_pct"]
    assert contract["strategy_param_fields"] == ["episode_days"]
    assert contract["dca_param_fields"] == list(eng.DCA_AXES)
    assert contract["domain_cardinality"] == {"strategy": 2, "dca": 48, "per_cohort": 96}
    assert contract["composite_map"] == {}
    assert spec["expected"] == eng.expected_counts()
    assert spec["params"] == [dict(c) for c in eng.CASES]
    print("  round-spec parameter_contract cross-check: OK")


def test_corwin_schultz_published_identity():
    """CS alpha > 0 iff beta > gamma: the defined-day count must equal an independent count."""
    n = 40
    ms = np.array([eng.utc_ms("2024-01-01") + i * eng.MS_DAY for i in range(n)], dtype=np.int64)
    mid = np.array([100.0 * (1.01 ** i) for i in range(n)])   # disjoint day ranges
    panel = {"open_ms": ms, "open": mid, "high": mid * 1.005, "low": mid * 0.995,
             "close": mid.copy(), "volume": np.full(n, 1e6)}
    spread, valid = eng.corwin_schultz(panel)
    H, L = panel["high"], panel["low"]
    pos = sum(1 for t in range(n - 1)
              if math.log(max(H[t], H[t + 1]) / min(L[t], L[t + 1])) ** 2 >
              math.log(H[t] / L[t]) ** 2 + math.log(H[t + 1] / L[t + 1]) ** 2)
    assert pos > 0.8 * (n - 1), pos
    assert abs(int(valid.sum()) - pos) <= 1, (int(valid.sum()), pos)
    assert np.all(spread[valid] > 0)
    assert np.all(spread[~valid] == 0.0)
    # the identity must also hold on the noisy panel, where coverage is partial
    noisy = make_panel(300)
    _, nvalid = eng.corwin_schultz(noisy)
    nh, nl = noisy["high"], noisy["low"]
    npos = sum(1 for t in range(len(nh) - 1)
               if math.log(max(nh[t], nh[t + 1]) / min(nl[t], nl[t + 1])) ** 2 >
               math.log(nh[t] / nl[t]) ** 2 + math.log(nh[t + 1] / nl[t + 1]) ** 2)
    assert abs(int(nvalid.sum()) - npos) <= 1, (int(nvalid.sum()), npos)
    print("  cs ramp-defined=%d/%d noisy-defined=%d beta-gamma-count=%d" %
          (int(valid.sum()), n - 1, int(nvalid.sum()), npos))


TESTS = [test_frozen_domain_registration,
         test_spec_validation_and_rejections,
         test_signal_constants_formula_and_oracle_lookahead,
         test_ladder_accounting_is_independent_and_net_of_cost,
         test_registered_stress_grids_all_move_net,
         test_episode_horizon_tiling_binds_the_strategy_axis,
         test_selector_disposition_and_neighbourhood,
         test_grid_csv_contract_and_decay_causality,
         test_round_spec_parameter_contract_when_present,
         test_corwin_schultz_published_identity]


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
    print("%d/%d passed" % (len([t for t in TESTS if not only or only in t.__name__]) - len(failures),
                            len([t for t in TESTS if not only or only in t.__name__])))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
