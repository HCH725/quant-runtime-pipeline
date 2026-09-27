#!/usr/bin/env python3
"""Self-check for the path-signature Qlib engine (240_path_signature_run.py).

Run on the host (python3) or inside the container (/opt/venv/bin/python); neither
path writes anything.  Every assertion is a minimal executable check of one piece
of non-trivial logic: signature correctness, causality, the defect-form allocator,
the registered entry/exit rules, per-fill accounting, ladder bounds, the cohort
selector, the frozen coverage product, and the pre-registered falsification battery.
"""
import importlib.util
import itertools
import json
import math
import os
import sys
from pathlib import Path

import numpy as np

ENGINE_PATH = Path(__file__).resolve().parents[1] / "240_path_signature_run.py"
SPEC = importlib.util.spec_from_file_location("pathsig_engine", ENGINE_PATH)
assert SPEC is not None and SPEC.loader is not None
eng = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(eng)

CHECKS = []


def check(name, condition, detail=""):
    if not condition:
        raise AssertionError("%s failed %s" % (name, detail))
    CHECKS.append(name)
    print("PASS %s%s" % (name, (" | " + str(detail) if detail else "")))


def brute_force_signature(window):
    """Explicit iterated-integral definition of the level-<=2 signature."""
    x = np.asarray(window, dtype=np.float64)
    anchored = x - x[0]
    level1 = anchored[-1]
    level2 = np.zeros((x.shape[1], x.shape[1]))
    for k in range(len(anchored) - 1):
        level2 += np.outer(anchored[k], anchored[k + 1] - anchored[k])
    return np.concatenate([level1, level2.ravel()])


def test_signature():
    rng = np.random.default_rng(20260902)
    prices = np.cumsum(rng.normal(0.0, 0.01, size=(400, 4)), axis=0) + 10.0
    for path_bars in (12, 48, 3):
        bank = eng.signature_bank(prices, path_bars)
        assert bank.shape == (len(prices) - path_bars + 1, 4 + 16)
        for endpoint in (path_bars - 1, path_bars + 5, len(prices) - 1):
            window = prices[endpoint - path_bars + 1:endpoint + 1]
            got = bank[endpoint - (path_bars - 1)]
            want = brute_force_signature(window)
            if not np.allclose(got, want, atol=1e-10):
                raise AssertionError("signature mismatch at %d/%d" % (path_bars, endpoint))
            # S^i is exactly the log-price change of the window, record section 1.
            if not np.allclose(got[:4], window[-1] - window[0], atol=1e-12):
                raise AssertionError("level-1 coordinate is not the window change")
    check("signature_matches_iterated_integral_and_level1", True)

    # Causality: perturbing bars strictly after an endpoint must not move that window.
    bank_before = eng.signature_bank(prices, 12)
    endpoint = 100
    shocked = prices.copy()
    shocked[endpoint + 1:] += 3.0
    bank_after = eng.signature_bank(shocked, 12)
    idx = endpoint - (12 - 1)
    if not np.allclose(bank_before[:idx + 1], bank_after[:idx + 1], atol=1e-12):
        raise AssertionError("signature bank reads ahead of its window endpoint")
    check("signature_bank_is_strictly_causal", True)

    bars = eng.decision_bars(5000, 12)
    first_ok = int(bars[0]) >= 12 + eng.M_MIN - 1
    check("decision_warmup_respects_estimation_floor",
          bool(first_ok) and bool(np.all(bars % 12 == 0)),
          "first=%d floor=%d" % (int(bars[0]), 12 + eng.M_MIN - 1))


def test_allocate():
    rng = np.random.default_rng(11)
    block = rng.normal(0.0, 1.0, size=(250, 20))
    mean = block.mean(axis=0)
    defect = (block.T @ block) / len(block) - np.outer(mean, mean)
    if not np.allclose(defect, defect.T, atol=1e-12) or np.min(
            np.linalg.eigvalsh(defect)) < -1e-9:
        raise AssertionError("defect form must be a symmetric PSD covariance")
    ell1, info1 = eng.allocate(block, 1.0, "ridge")
    ell_half, info_half = eng.allocate(block, 0.5, "ridge")
    if ell1 is None or ell_half is None:
        raise AssertionError("ridge allocator returned no solution: %s %s" % (info1, info_half))
    if not np.allclose(ell_half, 2.0 * ell1, atol=1e-9):
        raise AssertionError("risk-aversion scaling must be exactly 1/gamma")
    if info1["trace"] <= 0 or info1["ridge_delta"] <= 0:
        raise AssertionError("Marchenko-Pastur ridge shift must be strictly positive")
    # Negative control: M <= p means the raw plug-in is rank deficient; it must be
    # reported as singular instead of returning a silently unbounded solution.
    degenerate = np.repeat(block[:5], 2, axis=0)   # 10 rows, 20 columns -> rank <= 4
    ell_raw, info_raw = eng.allocate(degenerate, 1.0, "raw")
    if ell_raw is not None or info_raw.get("reason") not in ("singular", "zero_trace"):
        raise AssertionError("raw plug-in on a degenerate system must fail closed: %r" % (info_raw,))
    check("defect_form_psd_ridge_positive_gamma_homogeneous_raw_fails_closed",
          True, "trace=%.4f" % info1["trace"])


def test_signal_layer_causality_and_entry_rule():
    rng = np.random.default_rng(5)
    n = 4000
    logcloses = np.cumsum(rng.normal(0.0, 0.01, size=(n, 4)), axis=0) + 4.0
    bank = eng.signature_bank(logcloses, 12)
    layer = eng.signal_layer(logcloses, bank, 12, 1.0)
    live = [int(t) for t in layer["decisions"] if np.any(layer["weights"][t])]
    if not live:
        raise AssertionError("no live decisions in the synthetic layer")
    # No lookahead: a decision made at the close of bar t must ignore every later bar.
    for t in live[:20]:
        shocked = logcloses.copy()
        shocked[t + 1:] += 0.25
        layer2 = eng.signal_layer(shocked, eng.signature_bank(shocked, 12), 12, 1.0)
        if not np.allclose(layer["weights"][t], layer2["weights"][t], atol=1e-12):
            raise AssertionError("decision %d is not causal" % t)
    # Registered entry rule: every live leg executes the feedback at the decision bar.
    checked = 0
    for t in live[:40]:
        for leg in range(4):
            w = float(layer["weights"][t, leg])
            if w == 0.0:
                continue
            expected = float(np.clip(layer["feedback"][t, leg], -1, 1))
            if abs(w - expected) > 1e-12:
                raise AssertionError("entry weight is not the decision-bar feedback at %d/%d" % (t, leg))
            if abs(w) < eng.MIN_WEIGHT:
                raise AssertionError("entry below the registered floor at %d/%d" % (t, leg))
            checked += 1
    if not checked:
        raise AssertionError("no live leg exercised the entry rule")
    check("signal_layer_no_lookahead_and_entry_equals_decision_bar_feedback",
          True, "live_decisions=%d/%d" % (len(live), len(layer["decisions"])))


def make_panel(n=900, seed=7, crash_at=None, crash_size=-0.20, vol=0.012):
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0, vol, n)))
    if crash_at is not None:
        close[crash_at:] = close[crash_at] * (1.0 + crash_size)
    opened = np.r_[close[0], close[:-1]]
    panel = {"open": opened,
             "high": np.maximum(opened, close) * 1.0015,
             "low": np.minimum(opened, close) * 0.9985,
             "close": close,
             "volume": np.ones(n),
             "open_ms": 1_640_995_200_000 + np.arange(n, dtype=np.int64) * 300_000}
    return panel


def crafted_layer(n, entries, sign=1.0, flip_from=None, decisions=None, leg=None):
    leg = eng.SYMBOLS.index("BTCUSDT") if leg is None else leg
    weights = np.zeros((n, 4))
    feedback = np.full((n, 4), sign * 0.5)
    for t in entries:
        weights[t, leg] = sign * 0.5
    if flip_from is not None:
        feedback[flip_from:, leg] = -sign * 0.5
    bars = np.asarray(decisions if decisions is not None else entries, dtype=np.int64)
    return {"weights": weights, "feedback": feedback, "decisions": bars,
            "diag": {"decisions": len(bars)}}


def test_simulate_accounting():
    n = 600
    panel = make_panel(n, seed=7)
    events = [[] for _ in range(n)]
    instrument = {"tick": 0.01, "taker_fee": 0.0005}
    dca = dict(eng.DCA_GRID[0])
    layer = crafted_layer(n, [40, 300], decisions=[40, 300, 560])
    base = eng.simulate(panel, events, layer, "BTCUSDT", dca, 0, n, {}, instrument)
    if base["episodes"] < 1:
        raise AssertionError("synthetic book produced no episodes")
    if not base["decomposition_ok"]:
        raise AssertionError("gross - fees - funding != net_pnl: %r" % base)
    # Independent sign check on the price-only ledger.
    if abs(base["gross_pnl"] - (base["net_pnl"] + base["fees"] + base["funding"])) >= 1e-3:
        raise AssertionError("gross/net identity violated")
    # Negative control: doubling the taker fee must move net PnL while gross is untouched.
    stressed = eng.simulate(panel, events, layer, "BTCUSDT", dca, 0, n,
                            {"fee_mult": 2.0}, instrument)
    if abs(stressed["gross_pnl"] - base["gross_pnl"]) > 1e-9:
        raise AssertionError("gross ledger must be independent of fee accounting")
    if not (stressed["net_pnl"] < base["net_pnl"] and stressed["fees"] > base["fees"]):
        raise AssertionError("fee stress is a no-op: %r vs %r"
                             % (stressed["fees"], base["fees"]))
    check("simulate_per_fill_accounting_and_independent_gross", True,
          "episodes=%d fees=%.4f net=%.4f" % (base["episodes"], base["fees"], base["net_pnl"]))


def test_ladder_and_feedback_exit():
    n = 700
    # a flat pre-gap tape keeps both the resting stop and the breakeven TP untouched,
    # so the registered -30%-class gap is what exercises the scale-in ladder
    panel = make_panel(n, seed=9, crash_at=430, crash_size=-0.35, vol=0.0)
    events = [[] for _ in range(n)]
    instrument = {"tick": 0.01, "taker_fee": 0.0005}
    dca = dict(eng.DCA_GRID[0])
    layer = crafted_layer(n, [380, 620], decisions=[380, 620, 690])
    m = eng.simulate(panel, events, layer, "BTCUSDT", dca, 0, n, {}, instrument)
    hist = m["layer_hist"]
    # level_0 counts every episode; deeper bins count episodes that reached that layer.
    if hist[0] != m["episodes"]:
        raise AssertionError("layer histogram level_0 must equal the episode count")
    if sum(hist) != m["episodes"] + m["adds"]:
        raise AssertionError("layer histogram bins must account for every scale-in")
    if hist[eng.LADDER_LEVELS - 1] != 0:
        raise AssertionError("the registered 12th tranche must never deploy")
    active = max([i + 1 for i, v in enumerate(hist) if v] or [0])
    if active > eng.MAX_ADD_LEVELS + 1:
        raise AssertionError("routine active levels exceeded 11: %d" % active)
    if m["adds"] == 0:
        raise AssertionError("the adverse crash produced no scale-ins; ladder unexercised")

    flip_layer = crafted_layer(n, [40, 300], flip_from=120, decisions=[40, 300, 560])
    calm = make_panel(n, seed=3)
    f = eng.simulate(calm, events, flip_layer, "BTCUSDT", dca, 0, n, {}, instrument)
    if f["feedback_exits"] < 1:
        raise AssertionError("registered dynamic-feedback sign-flip exit never fired")
    check("ladder_bounds_and_feedback_exit", True,
          "max_active=%d feedback_exits=%d adds=%d" % (active, f["feedback_exits"], m["adds"]))


def synthetic_rows(sharpe_shift=0.0, oos_sign=1.0):
    rows = {g: [] for g in eng.GRIDS}
    for ci, case in enumerate(eng.STRATEGIES):
        for di, dca in enumerate(eng.DCA_GRID):
            quality = 100.0 + ci * 10.0 + di * 0.01 + sharpe_shift
            for g in eng.GRIDS:
                sign = 1.0 if g != "oos" else oos_sign
                rows[g].append({"symbol": "BTCUSDT", "timeframe": "1h", **case, **dca,
                                "grid": g, "net_pnl": sign * quality,
                                "sharpe": sign * (quality / 100.0),
                                "episodes": 200, "gross_pnl": sign * quality, "fees": 1.0,
                                "funding": 0.0, "ending_equity": 30000.0, "fills": 10,
                                "adds": 3, "turnover_usdt": 1000.0, "max_dd_pct": 1.0,
                                "max_dd_usdt": 300.0, "annualized_return": 0.1,
                                "max_effective_leverage": 1.5, "capital_utilization": 0.2,
                                "tp_hits": 1, "stop_hits": 1, "margin_calls": 0,
                                "rebalance_exits": 5, "feedback_exits": 2, "open_at_end": 0,
                                "decomposition_ok": True})
    return rows


def test_selector():
    rows = synthetic_rows(sharpe_shift=0.5)
    winner, info = eng.select_cohort(rows)
    if winner is None or info["cull_reasons"]:
        raise AssertionError("positive cohort was culled: %r" % (info,))
    axes = {**eng.STRATEGY_AXES, **eng.DCA_AXES}
    if tuple(winner[k] for k in axes) != tuple(info["winner"][k] for k in axes):
        raise AssertionError("winner cell and reported winner differ")
    # Every legal face-neighbour must be present in the joint space (2-value axes
    # legitimately expose a single neighbour at their registered boundary).
    expected = 0
    for name, domain in axes.items():
        ix = list(domain).index(winner[name])
        expected += int(ix > 0) + int(ix < len(domain) - 1)
    if info["neighbourhood"]["neighbours"] != expected:
        raise AssertionError("neighbourhood face count %r != %d"
                             % (info["neighbourhood"], expected))
    if not info["neighbourhood"]["passed"] or info["neighbourhood"]["same_sign_fraction"] < 0.6:
        raise AssertionError("positive cohort failed the 60%% neighbourhood gate")
    loser = synthetic_rows(sharpe_shift=0.5, oos_sign=-1.0)
    _, bad = eng.select_cohort(loser)
    if "oos_economic" not in bad["cull_reasons"]:
        raise AssertionError("negative OOS leg was not culled: %r" % bad["cull_reasons"])
    check("selector_historical_only_winner_and_neighbourhood", True,
          "neighbours=%d" % info["neighbourhood"]["neighbours"])


def test_coverage_and_registration():
    counts = eng.expected_counts()
    product = (len(eng.COHORTS) * len(eng.STRATEGIES) * len(eng.DCA_GRID))
    if counts["case_evaluations_per_grid"] != product:
        raise AssertionError("per-grid product mismatch")
    if counts["case_evaluations_total"] != product * len(eng.GRIDS):
        raise AssertionError("total product mismatch")
    if counts["cohorts"] != len(eng.SYMBOLS) * len(eng.TIMEFRAME_IDS):
        raise AssertionError("cohort set must be every symbol x every local interval")
    if len(eng.DCA_GRID) != 48:
        raise AssertionError("DCA domain must stay the full 4 x 2 x 3 x 2 product")
    rec = eng.FALSIFICATION["record_tests"]
    if set(rec) != {"levy_area_asymmetry", "shrinkage_boundary", "lift_ruin"}:
        raise AssertionError("record falsification battery incomplete")
    if rec["levy_area_asymmetry"]["t_stat_threshold"] != 2.0:
        raise AssertionError("Lévy-area threshold must stay 2.0")
    if rec["shrinkage_boundary"]["ratios"] != [0.5, 1.0, 2.5, 5.0, 10.0]:
        raise AssertionError("M/p grid must stay [0.5, 10.0] inclusive of the 2.5 barrier")
    if rec["lift_ruin"]["jump_size"] != -0.30:
        raise AssertionError("lift-ruin gap must stay -30%")
    if rec["shrinkage_boundary"]["raw_condition_limit"] != eng.RAW_CONDITION_LIMIT:
        raise AssertionError("raw singularity gate drifted from the frozen spec")
    if tuple(eng.REQUIRED_STRESS) != ("fee_2x", "funding_2x", "entry_delay_1_bar",
                                      "slippage_2ticks"):
        raise AssertionError("standard stress grid changed")
    check("coverage_product_and_frozen_registration", True,
          "%d cohorts x %d x %d x %d grids = %d" % (counts["cohorts"],
                                                    counts["strategy_cases_per_cohort"],
                                                    counts["dca_configs_per_cohort"],
                                                    counts["grid_count"],
                                                    counts["case_evaluations_total"]))


def test_spec_template():
    template = eng.run_spec_template()
    for forbidden in ("task_id", "kanban_task_id", "kanban_board"):
        if forbidden in template:
            raise AssertionError("direct family spec carries %s" % forbidden)
    spec = dict(template)
    spec.update(round_id=eng.FAMILY_ID + "-r1", run_id=eng.FAMILY_ID + "-r1-u1",
                created_at_utc="2026-09-27T00:00:00Z")
    script = ENGINE_PATH
    test_file = Path(__file__).resolve()
    meta = "/data/raw/_meta/CONFIG.json"
    if not os.path.isfile(meta):
        meta = "/Volumes/ExpansionDrive/market-data-raw/_meta/CONFIG.json"
    if not os.path.isfile(meta):
        check("spec_template_validates", False, "no canonical CONFIG.json reachable")
        return
    counts = eng.validate_spec(spec, script, test_file, meta_path=meta)
    if counts != eng.expected_counts():
        raise AssertionError("validate_spec returned unexpected counts")
    # Fail-closed: mutating the registered grid must be rejected.
    bad = dict(spec)
    bad["grids"] = list(eng.GRIDS)[:-1]
    try:
        eng.validate_spec(bad, script, test_file, meta_path=meta)
    except ValueError:
        pass
    else:
        raise AssertionError("validate_spec accepted a truncated grid set")
    check("run_spec_template_validates_and_fails_closed", True)


def main():
    test_signature()
    test_allocate()
    test_signal_layer_causality_and_entry_rule()
    test_simulate_accounting()
    test_ladder_and_feedback_exit()
    test_selector()
    test_coverage_and_registration()
    test_spec_template()
    print("ALL_CHECKS_PASS %d" % len(CHECKS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
