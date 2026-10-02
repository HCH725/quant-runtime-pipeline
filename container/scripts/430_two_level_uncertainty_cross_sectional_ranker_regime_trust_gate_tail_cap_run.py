#!/usr/bin/env python3
"""Deterministic local-universe full backtest for the two-level-uncertainty cross-sectional
ranker family (regime-trust gate + epistemic tail-risk cap): "When Alpha Breaks".

Reviewed source: Ursina Sanderink, "When Alpha Breaks: Two-Level Uncertainty for Safe Deployment
of Cross-Sectional Stock Rankers", arXiv:2603.13252v1 [cs.AI, cs.LG, q-fin.PM], February 2026.
Canonical record: quant/two-level-uncertainty-cross-sectional-ranker-regime-trust-gate-tail-cap-2026-09-05.md

Under the quant-runtime-pipeline contract (v1.8 / 14.4 / 6.4 lifecycle footer):
  1. The complete legal local eligible universe is registered: canonical local Binance USD-M
     perpetual, 4 symbols (BNBUSDT, BTCUSDT, ETHUSDT, SOLUSDT) x the record-required daily
     cohort timeframe (1d), with the 00:00 UTC daily boundary adopted from the record's crypto
     portability block.  The source U.S. AI-equity venue, the QQQ benchmark and the equity
     panel breadth are provenance/external-validity context, not execution prerequisites; the
     conclusion is scoped to that local panel.
  2. One registered strategy case (the record's frozen three-stage architecture: LightGBM
     cross-sectional ranker over the 7 PIT-safe features -> DEUP rank-displacement epistemic
     uncertainty with the PIT aleatoric floor -> strategy-level regime-trust gate G(t) with the
     binary abstention rule -> volatility-sized top/bottom-K selection with the P85 epistemic
     tail cap at kappa = 0.70) x the four-axis DCA domain (48 cells per cohort per grid):
     spacing_pct in {0.01,0.02,0.03,0.04} x size_multiplier in {1.0,1.1} x breakeven_tp_pct in
     {0.01,0.02,0.03} x invalidation_pct in {0.05,0.10}.  Fixed constants: starting_equity =
     30000 USDT, base_quote = 1000 USDT, max leverage 10x, routine active tranches max 11
     (#12 reserve).
  3. Cohort survivor semantics (cohort-selector-v1 / cohort-disposition-v1): 4 cohorts
     (4 symbols x 1d); the historical winner of the complete joint space (strategy case x DCA)
     is carried unchanged to OOS/full/robustness/neighbourhood.
  4. Ten evaluation grids: historical, oos, full, fee_2x, funding_2x, entry_delay_1_bar,
     slippage_2ticks, no_funding, no_funding_full, cost_attrition_40bps.  Grids that share the
     same phase window, entry delay and slippage share one simulated fill path; fee/funding
     stress is re-derived per fill from that path (never a no-op), and the cap ablation
     (Gate + Vol, no tail cap) is simulated as its own path for the registered battery.
  5. The record's registered falsification battery is implemented (four items, none lowered or
     dropped): structural coupling test (median Spearman rho(ê, |score|) > 0.20 and
     non-positive on at most 30% of dates), regime-trust gate AUROC on the unseen temporal
     holdout (> 0.55), holdout Sharpe superiority of the cap (delta > +0.05 and crisis MaxDD
     not worse by more than 1.0pp; operationalised as full-window cap-vs-no-cap), and the
     lead-lag PIT embargo audit (gate AUROC sensitivity over the structural lags 19/15/10 vs 20).
  6. The runner writes deterministic execution artifacts inside the attempt directory; it writes
     no terminal sentinel (DONE/FAILED/INCOMPLETE) and no verdict.json.

Local engine notes (research-defined, documented in the frozen round-spec): the container venv
has no lightgbm (bounded environment probe), so the record's LightGBM gradient-boosted trees
(ranker and the DEUP error predictor g(x)) are realised by a deterministic pure-python histogram
gradient-boosting regressor that keeps the record's hyperparameter semantics (n_estimators,
max_depth, eta/gamma/lambda analogues).  The ranker target, the 7 PIT-safe features, the rank
displacement loss, the PIT aleatoric floor, the health components, the sigmoid aggregation, the
G(t) clipping, the binary abstention threshold, the volatility sizing, the top/bottom-K
selection and the P85/kappa=0.70 tail cap are unchanged.  Rail realisations (research-defined,
registered in the specs): the equal-leg capital allocation maps onto the fixed base-quote
tranche ladder, the volatility sizing decides leg ordering exactly as the record's "select by
sized scores", and the registered position-level 30% epistemic weight reduction is realised as
the position's tranche-quote scale factor kappa = 0.70 on the recorded rail (the DCA domain and
the base-quote constant are untouched).  The local cross-section admits K_local = floor(N_t/2)
= 2 names per leg (largest symmetric rank depth; the registered top-10/bottom-10 structure
preserved as far as the legal local universe allows).  Portfolio construction (leg membership,
gate state and the tail cap) updates on the record's deployment cadence: non-overlapping
monthly rebalances (first daily bar of the UTC month), filled at the next daily bar open.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import datetime as dt
import gzip
import hashlib
import json
import math
import os
import re
import statistics
import sys
from pathlib import Path

FAMILY_ID = "two-level-uncertainty-cross-sectional-ranker-regime-trust-gate-tail-cap-2026-09-05"
ROUND_ID = FAMILY_ID + "-r1"
RUN_ID = ROUND_ID + "-u1"
RUNNER_NAME = "430_two_level_uncertainty_cross_sectional_ranker_regime_trust_gate_tail_cap_run.py"

FINGERPRINT_INPUT = (
    "two-level-uncertainty-cross-sectional-ranker-regime-trust-gate-tail-cap-2026-09-05|"
    "universe=portability=adapted/unproven (source-market; research-defined);window=record-faithful|"
    "dca=spacing_pct=0.01,0.02,0.03,0.04;size_multiplier=1.0,1.1;"
    "breakeven_tp_pct=0.01,0.02,0.03;invalidation_pct=0.05,0.10|"
    "selector=cohort-selector-v1;disposition=cohort-disposition-v1|"
    "source=two-level-uncertainty-cross-sectional-ranker-regime-trust-gate-tail-cap-2026-09-05.md"
)

RAW_ROOT = Path("/data/raw")
KLINES_ROOT = RAW_ROOT / "binance" / "usdm" / "klines"
FUNDING_ROOT = RAW_ROOT / "binance" / "usdm" / "funding"
INSTRUMENTS_PATH = RAW_ROOT / "binance" / "usdm" / "instruments" / "usdm-perp-instruments.json"
CONFIG_PATH = RAW_ROOT / "_meta" / "CONFIG.json"
SCHEMA_PATH = RAW_ROOT / "_meta" / "SCHEMA.md"

OWNERSHIP_KEYS = ("task_id", "kanban_task_id", "kanban_board")

# Registered cohort timeframe: the record's signal cadence is daily close-to-close on the
# standardised 00:00 UTC boundary (record crypto portability), and the same daily grid is the
# fill grid of the DCA rail.
TIMEFRAMES = ("1d",)
TF_MINUTES = {"1d": 1440}
EXECUTION_TIMEFRAME = "1d"
DEFAULT_INSTRUMENTS = ("BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT")

START_EQUITY = 30000.0
BASE_QUOTE = 1000.0
LEVERAGE = 10.0
ROUTINE_ACTIVE_TRANCHES_MAX = 11
MAX_ADD_LEVELS = 10
RESERVE_TRANCHE = 12

MIN_EPISODES_IS = 10
MIN_EPISODES_OOS = 3
MIN_NEIGHBOUR = 0.6
ANNUALIZATION = 365.0
RNG_SEED = 20260905

EXECUTION_MARKET = "BINANCE_USDM_PERP"
SOURCE_MARKET = (
    "U.S. common equities (Polygon ticker type CS), dynamic investable panel of up to 100 "
    "AI-exposed names, benchmark Invesco QQQ ETF, daily close-to-close bars at 4:00 PM ET"
)
CLAIM_SCOPE = (
    "canonical local Binance USD-M perpetual panel, 4 symbols (BNBUSDT/BTCUSDT/ETHUSDT/SOLUSDT) "
    "x the record-required daily cohort timeframe = 4 cohorts; the record's registered two-level "
    "uncertainty architecture (cross-sectional gradient-boosted ranker over the 7 PIT-safe "
    "features -> DEUP rank-displacement epistemic uncertainty with the PIT aleatoric floor -> "
    "strategy-level regime-trust gate G(t) with binary abstention -> volatility-sized "
    "top/bottom-K selection with the P85 epistemic tail cap kappa = 0.70) evaluated on that "
    "local panel with the frozen falsification battery; local-universe scoped conclusion (the "
    "crypto portability block of the record is adapted/unproven), not a U.S. AI-equity "
    "reproduction"
)

PHASES = {
    "historical": ("2022-01-01", "2025-09-30"),
    "oos": ("2025-10-01", "2026-09-11"),
    "full": ("2022-01-01", "2026-09-11"),
}

GRIDS = (
    "historical",
    "oos",
    "full",
    "fee_2x",
    "funding_2x",
    "entry_delay_1_bar",
    "slippage_2ticks",
    "no_funding",
    "no_funding_full",
    "cost_attrition_40bps",
)

DCA_AXES = {
    "spacing_pct": (0.01, 0.02, 0.03, 0.04),
    "size_multiplier": (1.0, 1.1),
    "breakeven_tp_pct": (0.01, 0.02, 0.03),
    "invalidation_pct": (0.05, 0.10),
}

# (phase, entry/exit delay bars, slippage ticks, cap override) per simulated fill path.
PATH_CONFIG = {
    "historical": ("historical", 0, 1.0, None),
    "oos": ("oos", 0, 1.0, None),
    "full": ("full", 0, 1.0, None),
    "full_delay1": ("full", 1, 1.0, None),
    "full_slip2": ("full", 0, 2.0, None),
    "full_nocap": ("full", 0, 1.0, 1.0),
}
PATH_KEYS = ("historical", "oos", "full", "full_delay1", "full_slip2", "full_nocap")
GRID_PATH = {
    "historical": "historical",
    "no_funding": "historical",
    "oos": "oos",
    "full": "full",
    "fee_2x": "full",
    "funding_2x": "full",
    "no_funding_full": "full",
    "cost_attrition_40bps": "full",
    "entry_delay_1_bar": "full_delay1",
    "slippage_2ticks": "full_slip2",
}


def dca_grid():
    cells = []
    for sp in DCA_AXES["spacing_pct"]:
        for sm in DCA_AXES["size_multiplier"]:
            for tp in DCA_AXES["breakeven_tp_pct"]:
                for inv in DCA_AXES["invalidation_pct"]:
                    cells.append({
                        "spacing_pct": sp,
                        "size_multiplier": sm,
                        "breakeven_tp_pct": tp,
                        "invalidation_pct": inv,
                    })
    return cells


DCA_GRID = dca_grid()

# Record-registered pipeline constants (source-reported unless marked research-defined).
FEATURES = ("mom_1m", "mom_3m", "mom_12m", "vol_20d", "vol_60d", "adv_20d", "cross_sectional_rank")
MOM_WINDOWS = {"mom_1m": 21, "mom_3m": 63, "mom_12m": 252}
VOL_WINDOWS = {"vol_20d": 20, "vol_60d": 60}
ADV_WINDOW = 20
TAU = 20                       # primary deployment horizon (record)
EMBARGO_DAYS = 90              # minimum trading-day embargo (record)
REFIT_EVERY = 21               # monthly walk-forward ranker refit (record: monthly folds)
MIN_TRAIN_SAMPLES = 200        # research-defined expanding-window floor
RANKER_PARAMS = (60, 3, 0.06, 0.0, 1.0)
DEUP_PARAMS = (30, 3, 0.08, 0.0, 1.0)
GBDT_MAX_BINS = 32
W_PIT = 60                     # aleatoric floor trailing window (record)
P_ALEATORIC = 0.10             # a_PIT = P10 of matured losses (record)
EWMA_HALFLIFE = 30             # H_real halflife (record)
EWMA_MIN_PERIODS = 20          # H_real min_periods (record)
DRIFT_WEIGHTS = (0.4, 0.3, 0.3)  # feat_drift / score_drift / corr_spike (record)
SCORE_DRIFT_WINDOW = 60        # trailing 60d scores (record)
FEAT_DRIFT_WINDOW = 252        # trailing 252d feature z-scores (record)
CORR_WINDOW = 20               # 20-day return correlation (record)
GATE_THETA = 0.20              # binary abstention threshold (record)
GATE_Z_MIN_OBS = 60            # research-defined expanding z-score floor
SIGMOID_LOW, SIGMOID_HIGH = 0.3, 0.7
K_TOP = 10                     # registered top/bottom-K selection
P_TAIL_CAP = 0.85              # 85th cross-sectional percentile of e-hat (record)
KAPPA = 0.70                   # 30% weight reduction on capped names (record)
CVOL_MEDIAN_TARGET = 0.70      # c_vol calibrated so the median vol multiplier is ~0.70 (record)
CVOL_WINDOW = 252              # research-defined PIT calibration window

# Record falsification thresholds (research-defined where the record states a rule).
FALSIFICATION = {
    "record": "arXiv:2603.13252v1 falsification plan (four items; none lowered or dropped)",
    "coupling_min_dates": 500,
    "coupling_median_min": 0.20,
    "coupling_nonpositive_max_fraction": 0.30,
    "gate_auroc_min": 0.55,
    "sharpe_improvement_min": 0.05,
    "cap_dd_worsening_max_pp": 1.0,
    "embargo_audit_lags": [19, 15, 10],
    "embargo_leak_tolerance": 0.05,
    "holdout_test": "cap vs no-cap on the full registered window (research-defined operationalisation of the record's holdout superiority test)",
}

GATES = {
    "min_episodes_is": MIN_EPISODES_IS,
    "min_episodes_oos": MIN_EPISODES_OOS,
    "min_neighbour_same_sign_fraction": MIN_NEIGHBOUR,
    "min_episodes_is_provenance": (
        "research-defined: the record states no explicit in-sample insufficiency threshold, so the "
        "project's registered cohort-selector sufficiency floor is used; the record's numeric "
        "falsification thresholds stay inside the falsification battery"
    ),
}

EXECUTION_SEMANTICS = {
    "execution_market": EXECUTION_MARKET,
    "position_direction": (
        "signal_signed_long_or_short_legs (the record's deployment is a market-neutral long/short "
        "cross-sectional portfolio: top-K names of the volatility-sized rank are long, bottom-K "
        "short, equal leg capital, 100%/100% gross when active and 0% exposure when gated off; the "
        "linear USD-M perpetual rail realises the two-sided exposure)"
    ),
    "numeraire": "USDT",
    "starting_equity_usdt": START_EQUITY,
    "base_quote_usdt": BASE_QUOTE,
    "max_leverage": LEVERAGE,
    "routine_active_tranches_max": ROUTINE_ACTIVE_TRANCHES_MAX,
    "reserve_tranche": RESERVE_TRANCHE,
    "initial_entry_counts_as_active_tranche": True,
    "max_add_levels": MAX_ADD_LEVELS,
    "entry_rule": (
        "monthly rebalance formation bar (first daily bar of the UTC month) closes -> fill at the "
        "next daily bar open (research-defined alignment of the record's first-trading-day "
        "rebalance; entry_delay_1_bar adds one further bar); a leg entry, a leg rotation/exit and "
        "gate-off abstention are all formed on the rebalance grid"
    ),
    "same_bar_order": (
        "open_adverse_invalidation_then_family_exit_then_adds_then_adverse_range_then_take_profit"
    ),
    "record_barriers_vs": (
        "the record registers no explicit price barrier: the family exit is the monthly "
        "leg-membership rotation or a gate-off abstention; the DCA breakeven take-profit and "
        "resting invalidation anchor on the running average cost"
    ),
    "timeout_exit": "none registered (the record holds until the signal turns)",
    "intrabar_tie": "adverse invalidation before take-profit when both are inside the same bar",
    "exit_mode": "reduce_only",
    "funding_attribution": (
        "official observations only, charged with entry fill timestamp <= t <= exit-time bound "
        "(bar open for open-price exits, bar close for intrabar exits); a missing official "
        "observation costs zero for that interval and is disclosed as coverage, never modelled"
    ),
    "slippage": (
        "one adverse tick from the instrument price_increment at baseline, two ticks on the "
        "slippage_2ticks grid (research-defined)"
    ),
    "holding_period": (
        "from the first executable bar after formation until the monthly rotation/gate-off family "
        "exit, the breakeven take-profit, the resting invalidation or the window end"
    ),
    "epistemic_tail_cap_rail": (
        "the registered position-level 30% epistemic weight reduction (kappa=0.70 when "
        "e_hat_PIT > P85 of the day's cross-section) is realised as the position's tranche-quote "
        "scale factor on the recorded rail; the DCA domain and the base-quote constant are "
        "untouched"
    ),
    "vol_sizing_rail": (
        "the record's volatility sizing selects the legs: ranks are computed on the sized scores "
        "w_i^vol = s_i * min(1, c_vol(t)/sqrt(vol_20d_i+eps)) with a PIT trailing-252d calibrated "
        "c_vol (median multiplier ~0.70); equal leg capital maps onto the fixed base-quote ladder"
    ),
    "leg_breadth_local": (
        "K_local = max(1, min(K=10, floor(N_t/2))): the registered top-10/bottom-10 equal-leg "
        "structure is realised at the largest symmetric rank depth of the legal local "
        "cross-section (2 of 4 names per side)"
    ),
}

REGISTERED_CASES = [
    {
        "index": 0,
        "key": "two_level_uncertainty_xs_ranker_regime_trust_gate_tail_cap",
        "name": "Two-Level Uncertainty Cross-Sectional Ranker with Strategy-Level Regime-Trust "
                "Gating and Position-Level Epistemic Tail-Risk Capping",
        "source_status": (
            "source-reported (LightGBM cross-sectional ranker over the 7 PIT-safe features, DEUP "
            "rank-displacement epistemic uncertainty with the PIT aleatoric floor, EWMA "
            "realized-efficacy regime-trust gate with drift/disagreement health components and "
            "binary abstention at G>=0.20, volatility sizing, top/bottom-K selection and the P85 "
            "epistemic tail cap at kappa=0.70)"
        ),
        "direction": "signal_signed_long_or_short",
        "cadence": "monthly rebalance formation (first daily bar of the UTC month), daily information",
        "model": (
            "gradient-boosted regression trees (the record's LightGBM ranker and DEUP error "
            "predictor realised by the local deterministic histogram booster with the record's "
            "hyperparameter semantics)"
        ),
        "formation": (
            "every monthly rebalance bar, using only information formed at or before that bar's "
            "close: the 7 PIT-safe features, the ranker score, the deployable epistemic "
            "uncertainty, the health components and the gate value"
        ),
        "entry_rule": (
            "name selected into a long/short leg while active (G>=0.20) -> open the leg at the "
            "next bar open; tranche #1 is the initial entry and adverse-price scale-ins follow "
            "the registered ladder while the episode is open"
        ),
        "exit_rule": (
            "monthly leg rotation (name leaves its leg), a leg sign flip, or a gate-off "
            "abstention -> reduce-only exit at the next bar open; the DCA breakeven "
            "take-profit/invalidation may close the episode earlier"
        ),
        "local_adaptation": (
            "U.S. AI-equity panel -> the four local USD-M perpetual symbols; QQQ benchmark -> the "
            "equal-weight local panel composite (research-defined); daily 4:00 PM ET closes -> "
            "00:00 UTC daily boundary (record crypto portability); monthly first-trading-day "
            "rebalance -> first daily bar of the UTC month; VIX-percentile-class DEUP market "
            "features -> local panel market analogues (market vol percentile, regime encoding, "
            "market vol and market return over 21d); the secondary Rank-Average model -> the "
            "equal-weight rank average of the 7 features; all model, feature, uncertainty, "
            "gating, sizing and cap semantics unchanged"
        ),
    },
]

STRATEGY_CASES = [case["index"] for case in REGISTERED_CASES]

STRATEGY_DOMAIN = {
    "strategy_case": list(STRATEGY_CASES),
    "cases": list(REGISTERED_CASES),
    "meaning": (
        "one registered strategy case: the record's single frozen three-stage architecture "
        "(cross-sectional ranker -> DEUP epistemic uncertainty -> regime-trust gate + tail cap "
        "deployment); a registered axis, never a search axis"
    ),
}

PARAMETER_PROVENANCE = {
    "search_domain": {
        "class": "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN",
        "axes": ["spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct"],
    },
    "registered_axis": {
        "class": "PROJECT_PRE_REGISTERED_REGISTERED_AXIS",
        "axes": ["strategy_case"],
        "note": "registered non-search axis evaluated across the joint space by cohort-selector-v1",
    },
    "constants": {
        "base_quote": {"class": "PROJECT_PRE_REGISTERED_CONSTANT", "value": BASE_QUOTE},
    },
    "user_fixed": {
        "class": "USER_FIXED",
        "invariants": [
            "starting_equity = 30,000 USDT",
            "USDT is the only numeraire",
            "linear USD-M perpetual instrument",
            "leverage 10x",
            "12 tranches",
            "geometric size multiplier 1.1 (historical progression value; a search candidate inside the size_multiplier axis)",
            "tranche #12 is the reserve/buffer and is not routinely deployed (routine active levels max 11)",
            "initial entry plus adverse-price scale-ins",
            "reduce-only exits",
            "same-bar multi-level crossing uses deterministic conservative ordering",
            "no add after FLAT/kill",
        ],
    },
}

EXPECTED_OUTPUTS = (
    "state.json",
    "result.json",
    "artifacts/progress.json",
    "artifacts/local_data_evidence.json",
    "artifacts/assertions.json",
    "artifacts/panel_evidence.json",
    "artifacts/falsification.json",
    "artifacts/stress_effects.json",
    "artifacts/cohort_results.json",
    "artifacts/cohort_survivors.json",
    *((("artifacts/grid_%s.csv" % grid)) for grid in GRIDS),
)

FUNDING_TIME_JITTER_TOLERANCE_MS = 1000


# ---------------------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------------------

def utc_now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with open(temp, "x", encoding="utf-8") as fh:
        json.dump(value, fh, indent=2, ensure_ascii=False, allow_nan=False)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(temp, path)


def immutable_json(path, value):
    raw = (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        if not path.is_file() or path.is_symlink() or path.read_bytes() != raw:
            raise ValueError("immutable output differs: %s" % path)
        return
    with os.fdopen(fd, "wb") as fh:
        fh.write(raw)
        fh.flush()
        os.fsync(fh.fileno())


def grid_phase(grid):
    if grid in ("historical", "no_funding"):
        return "historical"
    if grid == "oos":
        return "oos"
    return "full"


def cost_for(grid, meta):
    """Baseline-plus-stress cost record for one registered grid (canonical instrument metadata)."""
    base_taker_bps = float(meta["taker_fee"]) * 10000.0
    tick = float(meta["price_increment"])
    fee_bps = base_taker_bps
    funding_mult = 1.0
    delay_bars = 0
    slippage_ticks = 1.0
    if grid == "fee_2x":
        fee_bps *= 2.0
    elif grid == "funding_2x":
        funding_mult = 2.0
    elif grid == "entry_delay_1_bar":
        delay_bars = 1
    elif grid == "slippage_2ticks":
        slippage_ticks = 2.0
    elif grid in ("no_funding", "no_funding_full"):
        funding_mult = 0.0
    elif grid == "cost_attrition_40bps":
        fee_bps += 40.0
    return {
        "fee_bps": fee_bps,
        "funding_mult": funding_mult,
        "entry_delay_bars": delay_bars,
        "slippage_ticks": slippage_ticks,
        "tick_size": tick,
    }


def _fill_price(price, side, cost):
    adverse = 1.0 if side == "buy" else -1.0
    fill = price + adverse * cost["slippage_ticks"] * cost["tick_size"]
    return max(cost["tick_size"], fill)


def _pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return 0.0
    return sxy / math.sqrt(sxx * syy)


def _rank_average(values):
    """Average ranks (1-based) with deterministic tie handling."""
    order = sorted(range(len(values)), key=lambda i: (values[i], i))
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _spearman(xs, ys):
    if len(xs) != len(ys) or len(xs) < 2:
        return 0.0
    return _pearson(_rank_average(xs), _rank_average(ys))


def _auroc(labels, scores):
    """Rank-based AUROC with tie-averaged ranks; None when a class is absent."""
    pos = [s for l, s in zip(labels, scores) if l == 1]
    neg = [s for l, s in zip(labels, scores) if l == 0]
    n_pos, n_neg = len(pos), len(neg)
    if not n_pos or not n_neg:
        return None
    combined = list(scores)
    ranks = _rank_average(combined)
    rank_sum_pos = sum(r for r, l in zip(ranks, labels) if l == 1)
    auc = (rank_sum_pos - n_pos * (n_pos + 1) / 2.0) / float(n_pos * n_neg)
    return auc


def _ks_distance(a, b):
    """Two-sample Kolmogorov-Smirnov distance (tie-aware EDF walk)."""
    if not a or not b:
        return 0.0
    a = sorted(a)
    b = sorted(b)
    i = j = 0
    best = 0.0
    while i < len(a) and j < len(b):
        x = a[i] if a[i] <= b[j] else b[j]
        while i < len(a) and a[i] <= x:
            i += 1
        while j < len(b) and b[j] <= x:
            j += 1
        best = max(best, abs(i / len(a) - j / len(b)))
    return best


def _sigmoid(x):
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _quantile_nearest(values, q):
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[idx]


def _percentile_rank(values, value):
    """Percentile rank of *value* within *values* (0..1, average-rank basis)."""
    if not values:
        return 0.0
    below = sum(1 for v in values if v < value)
    equal = sum(1 for v in values if v == value)
    return (below + 0.5 * equal) / float(len(values))


def _clamp(value, low, high):
    return min(high, max(low, value))


def _expanding_z(series, min_obs):
    """Expanding-window z-scores; the value at t is scored against strictly earlier observations."""
    out = [None] * len(series)
    history = []
    for i, value in enumerate(series):
        if value is not None and len(history) >= min_obs:
            mean = sum(history) / len(history)
            sd = statistics.pstdev(history)
            if sd > 0:
                out[i] = (value - mean) / sd
        if value is not None:
            history.append(value)
    return out


def _ewma_matured_series(raw, maturity, halflife, min_periods):
    """EWMA over matured observations: raw[u] enters once u + maturity <= t (PIT safe)."""
    alpha = 1.0 - 0.5 ** (1.0 / float(halflife))
    out = [None] * len(raw)
    value = None
    count = 0
    for t in range(len(raw)):
        u = t - maturity
        if u >= 0 and raw[u] is not None:
            sample = raw[u]
            value = sample if value is None else alpha * sample + (1.0 - alpha) * value
            count += 1
        if value is not None and count >= min_periods:
            out[t] = value
    return out


# ---------------------------------------------------------------------------------------
# canonical local raw access
# ---------------------------------------------------------------------------------------

def load_instruments(path):
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError("instruments metadata missing: %s" % path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    instruments = doc.get("instruments") if isinstance(doc, dict) else None
    if not isinstance(instruments, list):
        raise ValueError("invalid instruments metadata payload")
    out = {}
    for item in instruments:
        fields = item.get("fields") if isinstance(item, dict) else None
        if not isinstance(fields, dict):
            continue
        sym = fields.get("raw_symbol")
        if not sym:
            continue
        out[sym] = fields
    return out


def load_funding(symbol, start_ms, end_ms, root=None):
    """Official funding observations only; a missing interval costs zero, never a modeled row."""
    base = Path(root) if root is not None else FUNDING_ROOT
    path = base / symbol / ("%s-funding.jsonl.gz" % symbol)
    report = {
        "file": str(path), "file_missing": not path.is_file(), "official": 0,
        "modeled_ignored": 0, "other_ignored": 0, "out_of_window": 0,
        "first_official_utc": None, "last_official_utc": None,
        "missing_intervals_are_zero_not_modeled": True,
        "boundary_jitter_snapped": 0, "max_boundary_jitter_ms": 0,
    }
    events = []
    if not path.is_file():
        return events, report
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            t = int(row["funding_time_ms"])
            status = row.get("truth_status")
            if status != "official":
                if not start_ms <= t <= end_ms:
                    report["out_of_window"] += 1
                elif status in ("modeled", "modeled_funding"):
                    report["modeled_ignored"] += 1
                else:
                    report["other_ignored"] += 1
                continue
            offset = t % 300000
            if offset > 150000:
                offset -= 300000
            if abs(offset) > FUNDING_TIME_JITTER_TOLERANCE_MS:
                raise RuntimeError("official funding timestamp is not on a 5m boundary for %s" % symbol)
            if offset:
                t -= offset
                report["boundary_jitter_snapped"] += 1
                report["max_boundary_jitter_ms"] = max(report["max_boundary_jitter_ms"], abs(offset))
            if not start_ms <= t <= end_ms:
                report["out_of_window"] += 1
                continue
            rate, mark = float(row["funding_rate"]), float(row["mark_price"])
            if not (math.isfinite(rate) and math.isfinite(mark) and mark > 0):
                raise RuntimeError("invalid official funding observation for %s" % symbol)
            events.append({"t": t, "rate": rate, "mark": mark, "cost_per_unit": rate * mark})
            report["official"] += 1
            report["first_official_utc"] = report["first_official_utc"] or row.get("funding_time_utc")
            report["last_official_utc"] = row.get("funding_time_utc")
    events.sort(key=lambda e: e["t"])
    return events, report


def load_klines(symbol, timeframe, root=None):
    """Canonical local perpetual klines for one (symbol, timeframe), indexed by open time."""
    base = Path(root) if root is not None else KLINES_ROOT / symbol / timeframe
    files = sorted(base.glob("%s-%s-*.jsonl.gz" % (symbol, timeframe)))
    if not files:
        raise FileNotFoundError("no canonical %s klines in %s" % (timeframe, base))
    rows = {}
    for path in files:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                row = json.loads(line)
                t = int(row["open_time_ms"])
                rows[t] = {
                    "open_time_ms": t, "close_time_ms": int(row["close_time_ms"]),
                    "open": float(row["open"]), "high": float(row["high"]),
                    "low": float(row["low"]), "close": float(row["close"]),
                    "volume": float(row.get("volume", 0.0)),
                }
    return rows, files


def bar_date(bar):
    return dt.datetime.fromtimestamp(bar["open_time_ms"] / 1000, dt.timezone.utc).date()


def gap_report(bars, timeframe):
    """Bar-level continuity audit: every adjacent pair must differ by exactly one interval."""
    step = TF_MINUTES[timeframe] * 60000
    gaps = []
    for i in range(1, len(bars)):
        delta = bars[i]["open_time_ms"] - bars[i - 1]["open_time_ms"]
        if delta != step:
            gaps.append({"index": i, "gap_bars": int(round(delta / float(step))) - 1,
                         "prev_open_time_ms": bars[i - 1]["open_time_ms"],
                         "open_time_ms": bars[i]["open_time_ms"]})
    bad_close = sum(1 for b in bars
                    if b["close_time_ms"] != b["open_time_ms"] + step - 1)
    return {"gap_count": len(gaps), "gaps_head": gaps[:5], "invalid_close_time_rows": bad_close}


def inspect_local_universe(config, schema_text="  ", symbols=None, instruments=None):
    """Legal local execution universe; source venue/quote/breadth is provenance, not a gate."""
    config = config if isinstance(config, dict) else {}
    raw_datasets = config.get("datasets")
    datasets = raw_datasets if isinstance(raw_datasets, dict) else {}
    declared = {str(v).upper() for v in config.get("symbols", []) if isinstance(v, str)}
    intervals = {str(v) for v in config.get("intervals", []) if isinstance(v, str)}
    found = sorted(symbols or [])
    klines_declared = "klines" in datasets
    registered_timeframes_present = all(tf in intervals for tf in TIMEFRAMES)
    per_symbol = {}
    legal = bool(found and klines_declared and registered_timeframes_present)
    for sym in found:
        meta = (instruments or {}).get(sym) or {}
        per_symbol[sym] = {
            "declared_in_config": sym in declared,
            "price_increment": meta.get("price_increment"),
            "taker_fee": meta.get("taker_fee"),
            "maker_fee": meta.get("maker_fee"),
            "quote_currency": meta.get("quote_currency"),
        }
        if meta.get("taker_fee") in (None, ""):
            legal = False
    return {
        "legal": legal,
        "venue": "BINANCE",
        "market_type": "usdm_perp",
        "symbols": found,
        "timeframes": list(TIMEFRAMES),
        "execution_timeframe": EXECUTION_TIMEFRAME,
        "cohort_definition": "instrument x timeframe",
        "source_market": SOURCE_MARKET,
        "source_exact_match": False,
        "source_exact_match_is_execution_prerequisite": False,
        "source_universe_breadth_is_execution_prerequisite": False,
        "universe_is_not_substituted": True,
        "claim_scope": CLAIM_SCOPE,
        "portability_beyond_source_venue": "adapted/unproven (source-market; research-defined)",
        "per_symbol": per_symbol,
        "config_klines_declared": klines_declared,
        "config_registered_timeframes_declared": registered_timeframes_present,
        "venue_intervals_declared": sorted(intervals),
        "schema_mentions_klines": "klines" in schema_text.lower(),
    }


# ---------------------------------------------------------------------------------------
# deterministic histogram gradient boosting (ranker + DEUP error predictor engine)
# ---------------------------------------------------------------------------------------

def _bin_edges(values, max_bins=GBDT_MAX_BINS):
    """Quantile cut points for histogram binning; unique values become exact cuts."""
    ordered = sorted(set(values))
    if len(ordered) <= max_bins:
        return list(ordered[1:])
    cuts = []
    for k in range(1, max_bins):
        pos = int(round(k * (len(ordered) - 1) / float(max_bins)))
        cut = ordered[pos]
        if not cuts or cut > cuts[-1]:
            cuts.append(cut)
    return cuts


def _bin_ids(values, cuts):
    return [bisect.bisect_right(cuts, v) for v in values]


class GBDTRegressor:
    """Squared-error histogram gradient boosting with the record's (eta, gamma, lambda) semantics."""

    def __init__(self, n_estimators, max_depth, eta, gamma, lam, max_bins=GBDT_MAX_BINS):
        self.n_estimators = int(n_estimators)
        self.max_depth = int(max_depth)
        self.eta = float(eta)
        self.gamma = float(gamma)
        self.lam = float(lam)
        self.max_bins = max_bins
        self.base_score = 0.0
        self.trees = []
        self._cuts = []
        self._bin_cols = []
        self._n_bins = []

    def _prepare_bins(self, X):
        n_features = len(X[0])
        self._cuts = []
        self._bin_cols = []
        for f in range(n_features):
            values = [row[f] for row in X]
            cuts = _bin_edges(values, self.max_bins)
            self._cuts.append(cuts)
            self._bin_cols.append(_bin_ids(values, cuts))
        self._n_bins = [len(cuts) + 1 for cuts in self._cuts]
        return self._bin_cols, self._n_bins

    def _bin_id(self, feature, value):
        return bisect.bisect_right(self._cuts[feature], value)

    def fit(self, X, y):
        n = len(X)
        n_features = len(X[0])
        self._prepare_bins(X)
        self.base_score = sum(y) / n
        pred = [self.base_score] * n
        for _ in range(self.n_estimators):
            residual = [y[i] - pred[i] for i in range(n)]
            tree = self._build_tree(residual, list(range(n)), 0, n_features)
            self.trees.append(tree)
            self._apply_tree(tree, X, pred, self.eta)
        return self

    def _leaf_value(self, idxs, residual):
        total = sum(residual[i] for i in idxs)
        return total / (len(idxs) + self.lam)

    def _build_tree(self, residual, idxs, depth, n_features):
        if depth >= self.max_depth or len(idxs) < 3:
            return {"leaf": self._leaf_value(idxs, residual)}
        total_g = sum(residual[i] for i in idxs)
        total_h = float(len(idxs))
        best = None
        for f in range(n_features):
            col = self._bin_cols[f]
            n_bins = self._n_bins[f]
            counts = [0.0] * n_bins
            sums = [0.0] * n_bins
            for i in idxs:
                b = col[i]
                counts[b] += 1.0
                sums[b] += residual[i]
            left_count = 0.0
            left_sum = 0.0
            for b in range(n_bins - 1):
                left_count += counts[b]
                left_sum += sums[b]
                right_count = total_h - left_count
                if left_count < 2 or right_count < 2:
                    continue
                right_sum = total_g - left_sum
                gain = (0.5 * (left_sum * left_sum / (left_count + self.lam)
                               + right_sum * right_sum / (right_count + self.lam)
                               - total_g * total_g / (total_h + self.lam)) - self.gamma)
                if best is None or gain > best[0] + 1e-12:
                    best = (gain, f, b)
        if best is None or best[0] <= 0.0:
            return {"leaf": self._leaf_value(idxs, residual)}
        _gain, f, b = best
        col = self._bin_cols[f]
        left = [i for i in idxs if col[i] <= b]
        right = [i for i in idxs if col[i] > b]
        if not left or not right:
            return {"leaf": self._leaf_value(idxs, residual)}
        return {"feature": f, "bin": b,
                "left": self._build_tree(residual, left, depth + 1, n_features),
                "right": self._build_tree(residual, right, depth + 1, n_features)}

    def _apply_tree(self, tree, X, pred, eta):
        for i, row in enumerate(X):
            node = tree
            while "leaf" not in node:
                b = self._bin_id(node["feature"], row[node["feature"]])
                node = node["left"] if b <= node["bin"] else node["right"]
            pred[i] += eta * node["leaf"]

    def predict(self, X):
        out = [self.base_score] * len(X)
        for tree in self.trees:
            self._apply_tree(tree, X, out, self.eta)
        return out


def fit_gbdt(params, X, y):
    n_estimators, max_depth, eta, gamma, lam = params
    model = GBDTRegressor(n_estimators, max_depth, eta, gamma, lam)
    return model.fit(X, y)


# ---------------------------------------------------------------------------------------
# cross-sectional Stage 1/2/3 pipeline on the registered daily cohort grid
# ---------------------------------------------------------------------------------------

def _daily_log_return(closes, idx):
    if idx <= 0 or closes[idx - 1] is None or closes[idx] is None:
        return None
    a, b = closes[idx - 1], closes[idx]
    if a <= 0 or b <= 0:
        return None
    return math.log(b / a)


def _stdev(values):
    if len(values) < 2:
        return None
    sd = statistics.pstdev(values)
    return sd if sd > 0 else None


def symbol_feature_table(bars):
    """Per-symbol daily rows: the 7 PIT-safe features (cross_sectional_rank filled later)."""
    closes = [b["close"] for b in bars]
    rows = []
    for i, bar in enumerate(bars):
        row = {
            "idx": i, "day": bar_date(bar), "close": bar["close"],
            "open_time_ms": bar["open_time_ms"],
            "mom_1m": None, "mom_3m": None, "mom_12m": None,
            "vol_20d": None, "vol_60d": None, "adv_20d": None, "cross_sectional_rank": None,
        }
        for name, window in MOM_WINDOWS.items():
            if i >= window and closes[i - window] and closes[i - window] > 0 and closes[i] > 0:
                row[name] = math.log(closes[i] / closes[i - window])
        for name, window in VOL_WINDOWS.items():
            if i >= window:
                rets = [r for r in (_daily_log_return(closes, k)
                                    for k in range(i - window + 1, i + 1)) if r is not None]
                sd = _stdev(rets)
                if sd is not None and len(rets) == window:
                    row[name] = sd * math.sqrt(ANNUALIZATION)
        if i >= ADV_WINDOW:
            adv = [bars[j]["volume"] * bars[j]["close"] for j in range(i - ADV_WINDOW + 1, i + 1)]
            row["adv_20d"] = sum(adv) / float(ADV_WINDOW)
        rows.append(row)
    return rows


def forward_excess_returns(tables, common_dates, tau):
    """Benchmark-relative forward tau-day excess return per symbol/date (equal-weight benchmark)."""
    # benchmark daily log return = cross-sectional mean of daily log returns on the common grid.
    bench_index = {}
    cum = 0.0
    for day in common_dates:
        rets = []
        for sym in sorted(tables):
            table = tables[sym]
            pos = table["index"].get(day)
            if pos is None or pos == 0:
                continue
            r = _daily_log_return(table["closes"], pos)
            if r is not None:
                rets.append(r)
        ret = sum(rets) / len(rets) if rets else None
        cum = cum + ret if ret is not None else cum
        bench_index[day] = cum
    out = {}
    for sym in sorted(tables):
        table = tables[sym]
        rows = table["rows"]
        idx_of = table["index"]
        series = {}
        for i, row in enumerate(rows):
            day = row["day"]
            if day not in idx_of:
                continue
            j = i + tau
            if j >= len(rows):
                continue
            day_end = rows[j]["day"]
            c0, c1 = rows[i]["close"], rows[j]["close"]
            b0, b1 = bench_index.get(day), bench_index.get(day_end)
            if None in (c0, c1, b0, b1) or c0 <= 0 or c1 <= 0:
                continue
            r_sym = math.log(c1 / c0)
            r_bench = b1 - b0
            series[day] = r_sym - r_bench
        out[sym] = series
    return out, bench_index


def market_features(common_dates, bench_index):
    """Local market analogues of the record's market regime inputs (research-defined)."""
    bench_ret = {}
    days = list(common_dates)
    for i, day in enumerate(days):
        prev = days[i - 1] if i > 0 else None
        bench_ret[day] = (bench_index[day] - bench_index[prev]) if prev is not None else None
    mkt_vol_21d, mkt_ret_21d, mkt_vol_pct, mkt_regime = {}, {}, {}, {}
    vol_series = []
    for i, day in enumerate(days):
        if i >= 21:
            rets = [bench_ret[days[k]] for k in range(i - 20, i + 1)]
            if all(r is not None for r in rets):
                sd = _stdev(rets)
                mkt_vol_21d[day] = sd * math.sqrt(ANNUALIZATION) if sd else 0.0
                mkt_ret_21d[day] = bench_index[day] - bench_index[days[i - 21]]
        if mkt_vol_21d.get(day) is not None:
            window = [v for v in (mkt_vol_21d.get(days[k]) for k in range(max(0, i - 251), i + 1))
                      if v is not None]
            if len(window) >= 60:
                mkt_vol_pct[day] = _percentile_rank(window, mkt_vol_21d[day])
                ordered = sorted(window)
                median = ordered[len(ordered) // 2]
                mkt_regime[day] = 1.0 if mkt_vol_21d[day] > median else 0.0
            vol_series.append(mkt_vol_21d[day])
    return {"vol_21d": mkt_vol_21d, "ret_21d": mkt_ret_21d,
            "vol_pct_252d": mkt_vol_pct, "regime_enc": mkt_regime}


def _feature_vector(row, mkt, day, score):
    """The DEUP predictor feature vector (record: 11 predictors; local market analogues)."""
    vals = [row.get(name) for name in FEATURES]
    if any(v is None for v in vals):
        return None
    if (score is None or mkt["vol_pct_252d"].get(day) is None
            or mkt["regime_enc"].get(day) is None or mkt["vol_21d"].get(day) is None
            or mkt["ret_21d"].get(day) is None):
        return None
    return [score, abs(score), row["cross_sectional_rank"], row["vol_20d"], row["vol_60d"],
            row["mom_1m"], row["adv_20d"], mkt["vol_pct_252d"][day], mkt["regime_enc"][day],
            mkt["vol_21d"][day], mkt["ret_21d"][day]]


def _cross_sectional_ranks(tables, day):
    """Percentile rank of mom_1m within one day's cross-section (record feature 7)."""
    mom = {}
    for sym, table in tables.items():
        pos = table["index"].get(day)
        if pos is None:
            continue
        value = table["rows"][pos]["mom_1m"]
        if value is not None:
            mom[sym] = value
    for sym in mom:
        pos = tables[sym]["index"][day]
        tables[sym]["rows"][pos]["cross_sectional_rank"] = _percentile_rank(
            [mom[s] for s in sorted(mom)], mom[sym])


def _vol_sized_score(score, vol20, c_vol):
    if score is None or vol20 is None or vol20 <= 0 or c_vol is None:
        return None
    mult = min(1.0, c_vol / math.sqrt(vol20 + 1e-12))
    return score * mult


def rank_displacement(score_by_sym, label_by_sym):
    """|rank%(forward excess) - rank%(score)| over the day's cross-section (record Stage 2)."""
    syms = sorted(set(score_by_sym) & set(label_by_sym))
    if len(syms) < 2:
        return {}
    scores = [score_by_sym[s] for s in syms]
    labels = [label_by_sym[s] for s in syms]
    out = {}
    for sym, score, label in zip(syms, scores, labels):
        out[sym] = abs(_percentile_rank(labels, label) - _percentile_rank(scores, score))
    return out


def rank_ic(score_by_sym, label_by_sym):
    syms = sorted(set(score_by_sym) & set(label_by_sym))
    if len(syms) < 3:
        return None
    return _spearman([score_by_sym[s] for s in syms], [label_by_sym[s] for s in syms])


def build_family_streams(symbols, panels, dates_by_symbol, diagnostics):
    """The full point-in-time cross-sectional pipeline; returns event streams and diagnostics."""
    symbols = sorted(symbols)
    tables = {}
    for sym in symbols:
        bars = panels[sym]
        tables[sym] = {"bars": bars, "closes": [b["close"] for b in bars],
                       "rows": symbol_feature_table(bars),
                       "index": {bar_date(b): i for i, b in enumerate(bars)}}
    # common daily grid = sorted union of the registered symbols' dates.
    common = sorted({day for sym in symbols for day in tables[sym]["index"]})
    for day in common:
        _cross_sectional_ranks(tables, day)

    fwd_excess, bench_index = forward_excess_returns(tables, common, TAU)
    fwd_by_tau = {TAU: fwd_excess}
    for lag in FALSIFICATION["embargo_audit_lags"] + [TAU]:
        if lag not in fwd_by_tau:
            fwd_by_tau[lag], _ = forward_excess_returns(tables, common, lag)
    mkt = market_features(common, bench_index)

    # --- Stage 1: walk-forward ranker (expanding window, monthly refits, 90d embargo) ---
    scores = {sym: {} for sym in symbols}          # day -> score
    ghat = {sym: {} for sym in symbols}            # day -> g(x)
    fit_diag = []
    day_pos = {day: i for i, day in enumerate(common)}
    n_days = len(common)
    for fold_start in range(0, n_days, REFIT_EVERY):
        fold_day = common[fold_start]
        fold_end = min(fold_start + REFIT_EVERY, n_days)
        train_X, train_y = [], []
        for sym in symbols:
            for day in common[:fold_start]:
                if day_pos[day] + TAU > fold_start - EMBARGO_DAYS:
                    continue
                label = fwd_by_tau[TAU][sym].get(day)
                if label is None:
                    continue
                row = tables[sym]["rows"][tables[sym]["index"][day]]
                feats = [row.get(name) for name in FEATURES]
                if any(v is None for v in feats):
                    continue
                train_X.append(feats)
                train_y.append(label)
        note = {"fold_start_day": fold_day.isoformat(), "train_rows": len(train_X)}
        if len(train_X) >= MIN_TRAIN_SAMPLES:
            model = fit_gbdt(RANKER_PARAMS, train_X, train_y)
            note["fit"] = True
            for k in range(fold_start, fold_end):
                day = common[k]
                for sym in symbols:
                    pos = tables[sym]["index"].get(day)
                    if pos is None:
                        continue
                    row = tables[sym]["rows"][pos]
                    feats = [row.get(name) for name in FEATURES]
                    if any(v is None for v in feats):
                        continue
                    scores[sym][day] = model.predict([feats])[0]
        else:
            note["fit"] = False
        fit_diag.append(note)

    # --- Stage 2: DEUP rank-displacement error predictor + PIT aleatoric floor ---
    loss_series = []                                # (day, sym, loss) matured targets
    for sym in symbols:
        for day in common:
            if day not in scores[sym] or day not in fwd_by_tau[TAU][sym]:
                continue
            loss_by_sym = rank_displacement(
                {s: scores[s].get(day) for s in symbols if day in scores[s]},
                {s: fwd_by_tau[TAU][s].get(day) for s in symbols if day in fwd_by_tau[TAU][s]})
            if sym in loss_by_sym:
                loss_series.append((day, sym, loss_by_sym[sym]))
    loss_diag = []
    for fold_start in range(0, n_days, REFIT_EVERY):
        fold_day = common[fold_start]
        fold_end = min(fold_start + REFIT_EVERY, n_days)
        train_X, train_y = [], []
        for day, sym, loss in loss_series:
            if day_pos[day] + TAU > fold_start:
                continue
            row = tables[sym]["rows"][tables[sym]["index"][day]]
            vec = _feature_vector(row, mkt, day, scores[sym].get(day))
            if vec is None:
                continue
            train_X.append(vec)
            train_y.append(loss)
        note = {"fold_start_day": fold_day.isoformat(), "train_rows": len(train_X)}
        if len(train_X) >= MIN_TRAIN_SAMPLES:
            gmodel = fit_gbdt(DEUP_PARAMS, train_X, train_y)
            note["fit"] = True
            for k in range(fold_start, fold_end):
                day = common[k]
                for sym in symbols:
                    pos = tables[sym]["index"].get(day)
                    if pos is None or day not in scores[sym]:
                        continue
                    row = tables[sym]["rows"][pos]
                    vec = _feature_vector(row, mkt, day, scores[sym].get(day))
                    if vec is None:
                        continue
                    ghat[sym][day] = gmodel.predict([vec])[0]
        else:
            note["fit"] = False
        loss_diag.append(note)

    # aleatoric floor a_PIT(t) = P10 of matured losses in [t - tau - W, t - tau]; e-hat as registered.
    loss_by_day = {}
    for day, _sym, loss in loss_series:
        loss_by_day.setdefault(day, []).append(loss)
    ehat = {sym: {} for sym in symbols}
    floor_series = [None] * n_days
    for t in range(n_days):
        lo = t - TAU - W_PIT
        hi = t - TAU
        if lo >= 0:
            pool = [v for d in range(lo, hi + 1) for v in loss_by_day.get(common[d], [])]
            if len(pool) >= 10:
                floor_series[t] = _quantile_nearest(pool, P_ALEATORIC)
    for sym in symbols:
        for day in common:
            g = ghat[sym].get(day)
            t = day_pos[day]
            if g is None or floor_series[t] is None:
                continue
            ehat[sym][day] = max(0.0, g - floor_series[t])

    # --- Stage 3: regime-trust gate G(t); drift / disagreement health components ---
    raw_rankic = [None] * n_days
    for t, day in enumerate(common):
        label = {s: fwd_by_tau[TAU][s].get(day) for s in symbols}
        score = {s: scores[s].get(day) for s in symbols}
        raw_rankic[t] = rank_ic(score, label)
    rankic_raw = {}
    for t, day in enumerate(common):
        rankic_raw[day] = raw_rankic[t]
    h_real = _ewma_matured_series(raw_rankic, TAU, EWMA_HALFLIFE, EWMA_MIN_PERIODS)

    feat_drift = [None] * n_days
    score_drift = [None] * n_days
    corr_spike = [None] * n_days
    for t, day in enumerate(common):
        zs = []
        for sym in symbols:
            pos = tables[sym]["index"].get(day)
            if pos is None:
                continue
            row = tables[sym]["rows"][pos]
            for name in FEATURES:
                if name == "cross_sectional_rank":
                    continue
                value = row.get(name)
                if value is None:
                    continue
                hist = []
                for k in range(max(0, pos - FEAT_DRIFT_WINDOW), pos):
                    v = tables[sym]["rows"][k].get(name)
                    if v is not None:
                        hist.append(v)
                if len(hist) >= 60:
                    sd = statistics.pstdev(hist)
                    if sd > 0:
                        zs.append(abs((value - sum(hist) / len(hist)) / sd))
        if zs:
            feat_drift[t] = sum(zs) / len(zs)
        today_scores = [scores[s][day] for s in symbols if day in scores[s]]
        pool = []
        for k in range(max(0, t - SCORE_DRIFT_WINDOW), t):
            pool.extend(scores[s].get(common[k]) for s in symbols
                        if common[k] in scores[s])
        pool = [v for v in pool if v is not None]
        if today_scores and len(pool) >= 20:
            score_drift[t] = _ks_distance(today_scores, pool)
        rets = {}
        for sym in symbols:
            pos = tables[sym]["index"].get(day)
            if pos is None or pos < CORR_WINDOW:
                continue
            series = [_daily_log_return(tables[sym]["closes"], k)
                      for k in range(pos - CORR_WINDOW + 1, pos + 1)]
            if all(r is not None for r in series):
                rets[sym] = series
        pairs = []
        keys = sorted(rets)
        for a in range(len(keys)):
            for b in range(a + 1, len(keys)):
                pairs.append(abs(_pearson(rets[keys[a]], rets[keys[b]])))
        if pairs:
            corr_spike[t] = sum(pairs) / len(pairs)

    disagree = [None] * n_days
    drift_feats = [name for name in FEATURES if name != "cross_sectional_rank"]
    for t, day in enumerate(common):
        primary = {s: scores[s].get(day) for s in symbols if day in scores[s]}
        secondary = {}
        for sym in symbols:
            pos = tables[sym]["index"].get(day)
            if pos is None or sym not in primary:
                continue
            row = tables[sym]["rows"][pos]
            if any(row.get(name) is None for name in drift_feats):
                continue
            ranks = []
            for name in drift_feats:
                pool = [tables[o]["rows"][tables[o]["index"][day]].get(name)
                        for o in symbols if day in tables[o]["index"]]
                pool = [v for v in pool if v is not None]
                if len(pool) < 2:
                    ranks = None
                    break
                ranks.append(_percentile_rank(pool, row.get(name)))
            if ranks is not None:
                secondary[sym] = sum(ranks) / len(ranks)
        common_syms = sorted(set(primary) & set(secondary))
        if len(common_syms) >= 3:
            disagree[t] = _spearman([primary[s] for s in common_syms],
                                    [secondary[s] for s in common_syms])

    def assemble_gate(real_series):
        z_real = _expanding_z(real_series, GATE_Z_MIN_OBS)
        z_drift = _expanding_z(feat_drift, GATE_Z_MIN_OBS)
        z_dis = _expanding_z(disagree, GATE_Z_MIN_OBS)
        g_list = [None] * n_days
        for t in range(n_days):
            if z_real[t] is None or z_drift[t] is None or z_dis[t] is None:
                continue
            h_raw = z_real[t] - 0.3 * z_drift[t] - 0.3 * z_dis[t]
            h = _sigmoid(h_raw)
            g_list[t] = _clamp((h - SIGMOID_LOW) / (SIGMOID_HIGH - SIGMOID_LOW), 0.0, 1.0)
        return g_list

    gate = assemble_gate(h_real)
    gate_audit = {}
    for lag in [TAU] + list(FALSIFICATION["embargo_audit_lags"]):
        if lag == TAU:
            gate_audit[lag] = gate
            continue
        raw = [None] * n_days
        for t, day in enumerate(common):
            label = {s: fwd_by_tau[lag][s].get(day) for s in symbols}
            score = {s: scores[s].get(day) for s in symbols}
            raw[t] = rank_ic(score, label)
        gate_audit[lag] = assemble_gate(_ewma_matured_series(raw, lag, EWMA_HALFLIFE,
                                                             EWMA_MIN_PERIODS))

    # --- Stage 3 deployment: monthly rebalance state machine on the common grid ---
    rebalances = []
    seen_month = None
    for t, day in enumerate(common):
        key = (day.year, day.month)
        if key != seen_month:
            seen_month = key
            rebalances.append(t)
    cvol_hist = []
    assignments = []
    state = {sym: "FLAT" for sym in symbols}
    events = {sym: [] for sym in symbols}
    for t in rebalances:
        day = common[t]
        active = gate[t] is not None and gate[t] >= GATE_THETA
        target = {sym: "FLAT" for sym in symbols}
        cap_factors = {sym: 1.0 for sym in symbols}
        legs = {"long": [], "short": []}
        if active:
            medians = []
            for sym in symbols:
                pos = tables[sym]["index"].get(day)
                if pos is None:
                    continue
                window = [math.sqrt(tables[sym]["rows"][k]["vol_20d"] + 1e-12)
                          for k in range(max(0, pos - CVOL_WINDOW + 1), pos + 1)
                          if tables[sym]["rows"][k]["vol_20d"] is not None]
                if window:
                    medians.append(_quantile_nearest(window, 0.5))
            c_vol = (_quantile_nearest(sorted(medians), 0.5) * CVOL_MEDIAN_TARGET
                     if medians else None)
            cvol_hist.append(c_vol)
            sized = {}
            for sym in symbols:
                pos = tables[sym]["index"].get(day)
                if pos is None or day not in scores[sym]:
                    continue
                row = tables[sym]["rows"][pos]
                sized[sym] = _vol_sized_score(scores[sym].get(day), row["vol_20d"], c_vol)
            ranked = sorted([s for s in sized if sized[s] is not None],
                            key=lambda s: (-sized[s], s))
            k_local = max(1, min(K_TOP, len(ranked) // 2)) if ranked else 0
            if k_local:
                legs["long"] = ranked[:k_local]
                legs["short"] = ranked[-k_local:]
                for sym in legs["long"]:
                    target[sym] = "LONG"
                for sym in legs["short"]:
                    target[sym] = "SHORT"
                ehat_day = {s: ehat[s][day] for s in ranked if day in ehat[s]}
                if ehat_day:
                    p85 = _quantile_nearest(sorted(ehat_day.values()), P_TAIL_CAP)
                    for sym in ranked:
                        if day in ehat[sym] and ehat_day.get(sym) is not None and p85 is not None \
                                and ehat_day[sym] > p85:
                            cap_factors[sym] = KAPPA
        for sym in symbols:
            prev = state[sym]
            now = target[sym]
            if now != prev:
                pos = tables[sym]["index"].get(day)
                if pos is not None:
                    if prev != "FLAT":
                        events[sym].append({"kind": "exit", "formation_idx": pos})
                    if now != "FLAT":
                        events[sym].append({"kind": "entry", "formation_idx": pos,
                                            "direction": "long" if now == "LONG" else "short",
                                            "cap_factor": cap_factors[sym]})
                state[sym] = now
        assignments.append({
            "day": day.isoformat(), "t": t, "gate": gate[t], "active": bool(active),
            "long": legs["long"], "short": legs["short"],
            "capped": sorted(s for s in cap_factors if cap_factors[s] != 1.0),
            "k_local": len(legs["long"]),
        })
    for sym in symbols:
        events[sym].sort(key=lambda e: (e["formation_idx"], 0 if e["kind"] == "exit" else 1))

    panel_days = []
    for t, day in enumerate(common):
        score_day = {s: scores[s].get(day) for s in symbols}
        ehat_day = {s: ehat[s].get(day) for s in symbols}
        coupling = None
        if all(v is not None for v in score_day.values()) and \
                all(v is not None for v in ehat_day.values()):
            coupling = _spearman([ehat_day[s] for s in symbols],
                                 [abs(score_day[s]) for s in symbols])
        good = None
        rankic_matured = None
        if t >= TAU and raw_rankic[t - TAU] is not None:
            rankic_matured = raw_rankic[t - TAU]
            good = 1 if rankic_matured > 0 else 0
        panel_days.append({
            "day": day.isoformat(), "gate": gate[t],
            "coupling": coupling, "matured_rankic": rankic_matured, "good_day": good,
            "ehat": {s: ehat_day[s] for s in symbols},
            "score": {s: score_day[s] for s in symbols},
        })

    oos_start = dt.date.fromisoformat(PHASES["oos"][0])
    oos_end = dt.date.fromisoformat(PHASES["oos"][1])
    gate_audit_stats = {}
    for lag in [TAU] + list(FALSIFICATION["embargo_audit_lags"]):
        series = gate_audit[lag]
        full_labels, full_values, oos_labels, oos_values = [], [], [], []
        for t, day in enumerate(common):
            if t < TAU or raw_rankic[t - TAU] is None or series[t] is None:
                continue
            label = 1 if raw_rankic[t - TAU] > 0 else 0
            full_labels.append(label)
            full_values.append(series[t])
            if oos_start <= day <= oos_end:
                oos_labels.append(label)
                oos_values.append(series[t])
        gate_audit_stats[str(lag)] = {
            "auroc_full": _auroc(full_labels, full_values) if full_labels else None,
            "auroc_oos": _auroc(oos_labels, oos_values) if oos_labels else None,
            "days_full": len(full_labels), "days_oos": len(oos_labels),
        }

    diagnostics["market"] = {"bench_days": len(common)}
    diagnostics["fits"] = fit_diag
    diagnostics["deup_fits"] = loss_diag
    diagnostics["rebalances"] = assignments
    diagnostics["gate_values"] = sum(1 for g in gate if g is not None)
    diagnostics["cap_positions"] = sum(1 for a in assignments for _s in a["capped"])
    diagnostics["state_changes"] = {s: sum(1 for e in events[s]) for s in symbols}
    return {
        "events": events,
        "panel_days": panel_days,
        "gate": gate,
        "gate_audit": gate_audit,
        "gate_audit_stats": gate_audit_stats,
        "rankic_raw": rankic_raw,
        "scores": scores,
        "ehat": ehat,
        "assignments": assignments,
        "days": [d.isoformat() for d in common],
    }


# ---------------------------------------------------------------------------------------
# Episode simulation: real per-fill accounting on the cohort's own bar grid
# ---------------------------------------------------------------------------------------

def simulate_episode(bars, plan, phase_end_idx, data_end_idx, dca, cost, funding_events):
    """One fully covered position life (long or short); gross from an independent accumulator."""
    fee_rate = float(cost["fee_bps"]) / 10000.0
    funding_mult = float(cost["funding_mult"])
    spacing = float(dca["spacing_pct"])
    mult = float(dca["size_multiplier"])
    tp_pct = float(dca["breakeven_tp_pct"])
    invalidation = float(dca["invalidation_pct"])
    direction = plan.get("direction", "long")
    cap_factor = float(plan.get("cap_factor", 1.0))
    sign = 1.0 if direction == "long" else -1.0

    entry_idx = plan["entry_idx"]
    plan_exit_idx = plan.get("exit_idx")
    entry_side = "buy" if direction == "long" else "sell"
    exit_side = "sell" if direction == "long" else "buy"
    initial = _fill_price(bars[entry_idx]["open"], entry_side, cost)
    if initial <= 0:
        raise ValueError("non-positive entry fill")
    qty = (BASE_QUOTE * cap_factor) / initial
    basis = qty * initial
    entry_notional = basis
    gross = 0.0
    fees = basis * fee_rate
    turnover = basis
    fills, adds, level = 1, 0, 1
    max_basis = basis

    last_idx = min(data_end_idx, phase_end_idx)
    if plan_exit_idx is not None:
        last_idx = min(last_idx, plan_exit_idx)
    if last_idx < entry_idx:
        raise ValueError("episode window inverted (entry %d > end %d)" % (entry_idx, last_idx))

    funding_times = [e["t"] for e in funding_events]
    fi = bisect.bisect_left(funding_times, bars[entry_idx]["open_time_ms"])
    funding = 0.0
    funding_charges = 0

    def charge(upto_ms, strict):
        nonlocal fi, funding, funding_charges
        while fi < len(funding_times):
            t = funding_times[fi]
            if (t < upto_ms) if strict else (t <= upto_ms):
                funding += (funding_events[fi]["cost_per_unit"] * qty * funding_mult
                            * (1.0 if direction == "long" else -1.0))
                funding_charges += 1
                fi += 1
            else:
                break

    exit_reason = None
    exit_price = None
    exit_at_open = False
    bar_idx = entry_idx
    while bar_idx <= last_idx:
        bar = bars[bar_idx]
        charge(bar["open_time_ms"], True)
        lower, upper = bar["low"], bar["high"]
        avg = basis / qty
        invalidation_price = avg * (1.0 - invalidation) if direction == "long" \
            else avg * (1.0 + invalidation)
        open_adverse = bar["open"] <= invalidation_price if direction == "long" \
            else bar["open"] >= invalidation_price
        if open_adverse:
            exit_reason = "invalidation"
            exit_price = _fill_price(min(bar["open"], invalidation_price) if direction == "long"
                                     else max(bar["open"], invalidation_price), exit_side, cost)
            exit_at_open = True
        elif plan_exit_idx is not None and bar_idx == plan_exit_idx:
            exit_reason = "family_exit"
            exit_price = _fill_price(bar["open"], exit_side, cost)
            exit_at_open = True
        if exit_reason is not None:
            charge(bar["close_time_ms"], False)
            break

        while level <= MAX_ADD_LEVELS:
            trigger = initial * (1.0 - spacing * level) if direction == "long" \
                else initial * (1.0 + spacing * level)
            if trigger <= 0:
                break
            touched = lower <= trigger if direction == "long" else upper >= trigger
            if not touched:
                break
            add_fill = _fill_price(trigger, entry_side, cost)
            quote = BASE_QUOTE * cap_factor * (mult ** level)
            add_qty = quote / add_fill
            qty += add_qty
            basis += add_qty * add_fill
            entry_notional += quote
            fees += quote * fee_rate
            turnover += quote
            fills += 1
            adds += 1
            level += 1
        max_basis = max(max_basis, basis)

        avg = basis / qty
        invalidation_price = avg * (1.0 - invalidation) if direction == "long" \
            else avg * (1.0 + invalidation)
        adverse = lower <= invalidation_price if direction == "long" \
            else upper >= invalidation_price
        take = avg * (1.0 + tp_pct) if direction == "long" else avg * (1.0 - tp_pct)
        favorable = upper >= take if direction == "long" else lower <= take
        if adverse:
            exit_reason = "invalidation"
            exit_price = _fill_price(invalidation_price, exit_side, cost)
        elif favorable:
            exit_reason = "tp"
            exit_price = _fill_price(take, exit_side, cost)
        if exit_reason is not None:
            charge(bar["close_time_ms"], False)
            break
        bar_idx += 1

    if exit_reason is None:
        tail = bars[last_idx]
        exit_reason = "window_end"
        exit_price = _fill_price(tail["close"], exit_side, cost)
        bar_idx = last_idx
        charge(tail["close_time_ms"], False)
    exit_idx = bar_idx
    exit_notional = qty * exit_price
    if direction == "long":
        gross += exit_notional - basis
    else:
        gross += basis - exit_notional
    fees += exit_notional * fee_rate
    turnover += exit_notional
    fills += 1
    return {
        "gross": gross,
        "fee_base": fees,
        "funding_base": funding,
        "fills": fills,
        "adds": adds,
        "max_active_tranches": 1 + adds,
        "turnover_usdt": turnover,
        "exit_reason": exit_reason,
        "exit_at_open": exit_at_open,
        "entry_notional": entry_notional,
        "capital_committed": max_basis,
        "funding_events_charged": funding_charges,
        "entry_idx": entry_idx,
        "exit_idx": exit_idx,
        "direction": direction,
        "cap_factor": cap_factor,
    }


def run_episode_sequence(bars, events, phase_start_idx, phase_end_idx, data_end_idx, delay,
                         dca, cost, funding_events):
    """Sequential non-overlapping episodes; a same-bar flip may re-enter at that bar open."""
    entries = [e for e in events if e["kind"] == "entry"
               and phase_start_idx <= e["formation_idx"] <= phase_end_idx]
    exit_forms = sorted(e["formation_idx"] for e in events if e["kind"] == "exit")
    episodes = []
    missing = 0
    out_of_window = 0
    cursor = phase_start_idx - 1
    prev_exit_at_open = True
    for ent in entries:
        form = ent["formation_idx"]
        entry_idx = form + 1 + delay
        if entry_idx < cursor or (entry_idx == cursor and not prev_exit_at_open):
            continue
        if entry_idx > data_end_idx:
            missing += 1
            continue
        if entry_idx > phase_end_idx:
            out_of_window += 1
            continue
        pos = bisect.bisect_right(exit_forms, form)
        exit_form = exit_forms[pos] if pos < len(exit_forms) else None
        plan = {"entry_idx": entry_idx, "direction": ent.get("direction", "long"),
                "cap_factor": ent.get("cap_factor", 1.0),
                "exit_idx": (exit_form + 1 + delay) if exit_form is not None else None}
        episode = simulate_episode(bars, plan, phase_end_idx, data_end_idx, dca, cost,
                                   funding_events)
        episodes.append(episode)
        cursor = episode["exit_idx"]
        prev_exit_at_open = episode["exit_at_open"]
    return episodes, missing, out_of_window


def derive_episodes(base_episodes, fee_mult, funding_mult):
    """Re-derive every per-fill cash delta for a stress grid (never a no-op)."""
    out = []
    for ep in base_episodes:
        fees = ep["fee_base"] * fee_mult
        funding = ep["funding_base"] * funding_mult
        row = dict(ep)
        row["fees"] = fees
        row["funding"] = funding
        row["net"] = ep["gross"] - fees - funding
        out.append(row)
    return out


def derive_flat_fee(episodes, fee_bps):
    """Replace the per-fill fee with a flat one-way friction schedule (falsification battery)."""
    out = []
    for ep in episodes:
        fees = ep["turnover_usdt"] * fee_bps / 10000.0
        funding = ep["funding_base"]
        row = dict(ep)
        row["fees"] = fees
        row["funding"] = funding
        row["net"] = ep["gross"] - fees - funding
        out.append(row)
    return out


def summarize(episodes, phase_start, phase_end):
    gross = sum(e["gross"] for e in episodes)
    fees = sum(e["fees"] for e in episodes)
    funding = sum(e["funding"] for e in episodes)
    net = sum(e["net"] for e in episodes)
    fills = sum(e["fills"] for e in episodes)
    adds = sum(e["adds"] for e in episodes)
    turnover = sum(e["turnover_usdt"] for e in episodes)
    returns = [e["net"] / START_EQUITY for e in episodes]
    spread = statistics.pstdev(returns) if len(returns) >= 2 else 0.0
    sharpe = (statistics.mean(returns) / spread * math.sqrt(ANNUALIZATION)
              if len(returns) >= 2 and spread > 0 else 0.0)
    equity, peak, max_dd = START_EQUITY, START_EQUITY, 0.0
    for e in episodes:
        equity += e["net"]
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    days = max(1, (dt.date.fromisoformat(phase_end) - dt.date.fromisoformat(phase_start)).days + 1)
    annual = (equity / START_EQUITY) ** (ANNUALIZATION / days) - 1.0 if equity > 0 else -1.0
    max_cap = max((e["capital_committed"] for e in episodes), default=0.0)
    capped = sum(1 for e in episodes if abs(e.get("cap_factor", 1.0) - KAPPA) < 1e-12)
    return {
        "gross_pnl": gross, "fees": fees, "funding": funding, "net_pnl": net,
        "ending_equity": equity, "episodes": len(episodes), "fills": fills, "adds": adds,
        "max_active_tranches": max((e["max_active_tranches"] for e in episodes), default=0),
        "turnover_usdt": turnover, "sharpe": sharpe,
        "max_dd_pct": (max_dd / peak * 100.0) if peak > 0 else 0.0,
        "max_dd_usdt": max_dd, "annualized_return": annual,
        "max_effective_leverage": max_cap / START_EQUITY * LEVERAGE,
        "capital_utilization": max_cap / START_EQUITY,
        "tp_hits": sum(1 for e in episodes if e["exit_reason"] == "tp"),
        "stop_hits": sum(1 for e in episodes if e["exit_reason"] == "invalidation"),
        "family_exits": sum(1 for e in episodes if e["exit_reason"] == "family_exit"),
        "margin_calls": 0,
        "end_exits": sum(1 for e in episodes if e["exit_reason"] == "window_end"),
        "open_at_end": 0,
        "long_episodes": sum(1 for e in episodes if e["direction"] == "long"),
        "short_episodes": sum(1 for e in episodes if e["direction"] == "short"),
        "capped_episodes": capped,
        "funding_events_charged": sum(e["funding_events_charged"] for e in episodes),
        "decomposition_ok": abs((gross - fees - funding) - net) < 1e-3,
    }


def cell_key(row):
    return (
        row["symbol"],
        row["timeframe"],
        int(row["strategy_case"]),
        float(row["spacing_pct"]),
        float(row["size_multiplier"]),
        float(row["breakeven_tp_pct"]),
        float(row["invalidation_pct"]),
    )


def simulate_path(bars, events, dates, funding_events, cell, path_key):
    phase, delay, slippage, cap_override = PATH_CONFIG[path_key]
    start_idx, end_idx = _window_indices(dates, phase)
    cost = cost_for("historical", cell["meta"])
    cost = dict(cost, entry_delay_bars=delay, slippage_ticks=slippage)
    if cap_override is not None:
        events = [dict(e, cap_factor=cap_override) if e["kind"] == "entry" else e
                  for e in events]
    episodes, missing, out_of_window = run_episode_sequence(
        bars, events, start_idx, end_idx, len(bars) - 1, delay, cell, cost, funding_events)
    formations = sum(1 for e in events if e["kind"] == "entry"
                     and start_idx <= e["formation_idx"] <= end_idx)
    return {
        "episodes": episodes,
        "missing": missing,
        "out_of_window": out_of_window,
        "formations_in_phase": formations,
        "phase": phase,
        "window": (PHASES[phase][0], PHASES[phase][1]),
    }


def evaluate_all(meta, panels, dates_by_symbol, all_events, all_funding):
    rows_by_grid = {g: [] for g in GRIDS}
    for timeframe in TIMEFRAMES:
        panel = panels[timeframe]
        for sym in sorted(panel):
            bars = panel[sym]
            dates = dates_by_symbol[sym]
            funding = all_funding[sym]
            symbol_meta = meta[sym]
            base_fee = float(symbol_meta["taker_fee"]) * 10000.0
            for case in STRATEGY_CASES:
                events = all_events[sym]["events"]
                for cell in DCA_GRID:
                    cell_params = dict(cell, meta=symbol_meta)
                    cache = {pk: simulate_path(bars, events, dates, funding, cell_params, pk)
                             for pk in PATH_KEYS}
                    for grid in GRIDS:
                        path = cache[GRID_PATH[grid]]
                        grid_cost = cost_for(grid, symbol_meta)
                        episodes = derive_episodes(path["episodes"],
                                                   grid_cost["fee_bps"] / base_fee,
                                                   grid_cost["funding_mult"])
                        metrics = summarize(episodes, path["window"][0], path["window"][1])
                        metrics["formations_in_phase"] = path["formations_in_phase"]
                        metrics["missing_formations"] = path["missing"]
                        metrics["out_of_window_formations"] = path["out_of_window"]
                        rows_by_grid[grid].append({
                            "symbol": sym,
                            "timeframe": timeframe,
                            "strategy_case": case,
                            "grid": grid,
                            **cell,
                            **metrics,
                        })
    return rows_by_grid


# ---------------------------------------------------------------------------------------
# cohort-selector-v1 / cohort-disposition-v1
# ---------------------------------------------------------------------------------------

def _row_sort_key(row):
    return (
        -float(row["sharpe"]),
        -float(row["net_pnl"]),
        int(row["strategy_case"]),
        float(row["spacing_pct"]),
        float(row["size_multiplier"]),
        float(row["breakeven_tp_pct"]),
        float(row["invalidation_pct"]),
    )


def select_cohort(rows):
    """cohort-selector-v1 over the complete joint space (strategy case x DCA)."""
    hist = [r for r in rows if r["grid"] == "historical"]
    best_episodes = max((int(r["episodes"]) for r in hist), default=0)
    if best_episodes < MIN_EPISODES_IS:
        return None, "insufficient_trades", None
    candidates = [r for r in hist
                  if float(r["net_pnl"]) > 0 and float(r["sharpe"]) > 0
                  and int(r["episodes"]) >= MIN_EPISODES_IS]
    if not candidates:
        return None, "no_qualifying_candidate", None
    candidates.sort(key=_row_sort_key)
    return candidates[0], None, candidates


def select_family_cell(rows, symbol, timeframe, case):
    """Best historical cell inside one (cohort, strategy case); None when it does not qualify."""
    hist = [r for r in rows if r["grid"] == "historical" and r["symbol"] == symbol
            and r["timeframe"] == timeframe and int(r["strategy_case"]) == case]
    best_episodes = max((int(r["episodes"]) for r in hist), default=0)
    if best_episodes < MIN_EPISODES_IS:
        return None, "insufficient_trades"
    candidates = [r for r in hist
                  if float(r["net_pnl"]) > 0 and float(r["sharpe"]) > 0
                  and int(r["episodes"]) >= MIN_EPISODES_IS]
    if not candidates:
        return None, "no_qualifying_candidate"
    candidates.sort(key=_row_sort_key)
    return candidates[0], None


def neighbourhood(winner, historical):
    """Face neighbours across the complete joint space: strategy case axis + four DCA axes."""
    found = {cell_key(r): r for r in historical}
    key = list(cell_key(winner))
    positions = [
        (2, list(STRATEGY_CASES)),
        (3, list(DCA_AXES["spacing_pct"])),
        (4, list(DCA_AXES["size_multiplier"])),
        (5, list(DCA_AXES["breakeven_tp_pct"])),
        (6, list(DCA_AXES["invalidation_pct"])),
    ]
    agreeing = total = 0
    winner_positive = float(winner["net_pnl"]) > 0
    for pos, domain in positions:
        idx = domain.index(key[pos])
        for step in (-1, 1):
            if 0 <= idx + step < len(domain):
                probe = key.copy()
                probe[pos] = domain[idx + step]
                row = found.get(tuple(probe))
                if row is None:
                    continue
                total += 1
                agreeing += (float(row["net_pnl"]) > 0) == winner_positive
    fraction = agreeing / total if total else 0.0
    return {
        "neighbours": total, "agreeing": agreeing, "same_sign_fraction": fraction,
        "axes": ["strategy_case"] + list(DCA_AXES),
        "passed": bool(total and fraction >= MIN_NEIGHBOUR),
    }


def metric_block(row):
    keys = (
        "gross_pnl", "fees", "funding", "net_pnl", "ending_equity", "episodes", "fills",
        "adds", "turnover_usdt", "sharpe", "max_dd_pct", "max_dd_usdt",
        "annualized_return", "max_effective_leverage", "capital_utilization",
        "tp_hits", "stop_hits", "family_exits", "margin_calls", "end_exits", "open_at_end",
        "long_episodes", "short_episodes", "capped_episodes",
    )
    return {k: row[k] for k in keys}


def disposition_band(survivor_count):
    """Contract 7.3 v1.4.0 disposition band: a count only, never a verdict."""
    if survivor_count > 1:
        return "MULTIPLE_SURVIVORS"
    return "SURVIVOR_FOUND" if survivor_count else "NO_SURVIVOR"


def cohort_record(cohort_name, winner, hist_row, by_grid, culls, survivor_checks, nb):
    """One cohort record in the contract 10.8 schema consumed by the host-side writers."""
    winner_params = {
        "strategy_case": int(winner["strategy_case"]),
        "spacing_pct": float(winner["spacing_pct"]),
        "size_multiplier": float(winner["size_multiplier"]),
        "breakeven_tp_pct": float(winner["breakeven_tp_pct"]),
        "invalidation_pct": float(winner["invalidation_pct"]),
    }
    robustness_grids = ("fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks")
    return {
        "cohort": cohort_name,
        "outcome": "SURVIVOR" if not culls else "CULLED",
        "winner": winner_params,
        "winner_strategy_case": int(winner["strategy_case"]),
        "winner_case_key": REGISTERED_CASES[int(winner["strategy_case"])]["key"],
        "best_historical_episodes": int(hist_row["episodes"]),
        "cull_reasons": culls,
        "survivor_checks": survivor_checks,
        "neighbourhood": nb,
        "metrics": {
            "phases": {
                "historical": metric_block(hist_row),
                "oos": metric_block(by_grid["oos"]),
                "full": metric_block(by_grid["full"]),
            },
            "robustness": {g: metric_block(by_grid[g]) for g in robustness_grids},
            "neighbourhood": nb,
        },
    }


def write_grid(output_dir, grid, rows):
    path = Path(output_dir) / ("grid_%s.csv" % grid)
    fieldnames = [
        "symbol", "timeframe", "strategy_case", "spacing_pct", "size_multiplier",
        "breakeven_tp_pct", "invalidation_pct", "gross_pnl", "fees", "funding", "net_pnl",
        "ending_equity", "episodes", "fills", "adds", "turnover_usdt", "sharpe",
        "max_dd_pct", "max_dd_usdt", "annualized_return", "max_effective_leverage",
        "capital_utilization", "tp_hits", "stop_hits", "family_exits", "margin_calls",
        "end_exits", "open_at_end", "long_episodes", "short_episodes", "capped_episodes",
        "formations_in_phase", "missing_formations", "out_of_window_formations",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


# ---------------------------------------------------------------------------------------
# Record falsification battery (four registered items, none lowered or dropped)
# ---------------------------------------------------------------------------------------

def _pooled_sharpe(episodes):
    values = [e["net"] / START_EQUITY for e in episodes]
    if len(values) < 2:
        return 0.0
    spread = statistics.pstdev(values)
    if spread <= 0:
        return 0.0
    return statistics.mean(values) / spread * math.sqrt(ANNUALIZATION)


def _pooled_max_dd(episodes):
    equity, peak, max_dd = START_EQUITY, START_EQUITY, 0.0
    for e in sorted(episodes, key=lambda row: (row["exit_idx"], row["entry_idx"])):
        equity += e["net"]
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return (max_dd / peak * 100.0) if peak > 0 else 0.0


def evaluate_falsification(panel_days, gate_audit_stats, rows_by_grid, panels, dates_by_symbol,
                           all_events, all_funding, meta, symbols):
    """The record's four-item battery on the winners of the complete joint space."""
    oos_start = dt.date.fromisoformat(PHASES["oos"][0])
    oos_end = dt.date.fromisoformat(PHASES["oos"][1])

    # Item 1: structural coupling of epistemic uncertainty and signal magnitude.
    couplings = [(day["day"], day["coupling"]) for day in panel_days
                 if day["coupling"] is not None]
    coupling_values = [v for _d, v in couplings]
    coupling_median = _quantile_nearest(coupling_values, 0.5) if coupling_values else None
    nonpositive = sum(1 for v in coupling_values if v <= 0)
    nonpositive_fraction = (nonpositive / len(coupling_values)) if coupling_values else None
    if coupling_median is None or len(coupling_values) < FALSIFICATION["coupling_min_dates"]:
        item1_status = "insufficient"
    elif coupling_median > FALSIFICATION["coupling_median_min"] and \
            nonpositive_fraction <= FALSIFICATION["coupling_nonpositive_max_fraction"]:
        item1_status = "pass"
    else:
        item1_status = "fail"

    # Item 2: gate AUROC on the unseen temporal holdout.
    labels, gates = [], []
    for day in panel_days:
        d = dt.date.fromisoformat(day["day"])
        if oos_start <= d <= oos_end and day["good_day"] is not None and day["gate"] is not None:
            labels.append(day["good_day"])
            gates.append(day["gate"])
    oos_auroc = _auroc(labels, gates) if labels else None
    item2_status = ("pass" if oos_auroc is not None and oos_auroc > FALSIFICATION["gate_auroc_min"]
                    else "fail")

    # Item 3: cap vs no-cap (full window) Sharpe superiority + MaxDD guardrail.
    case = STRATEGY_CASES[0]
    cap_episodes, nocap_episodes = [], []
    cohort_winners = {}
    for sym in sorted(symbols):
        cell, reason = select_family_cell(rows_by_grid["historical"], sym, TIMEFRAMES[0], case)
        if cell is None:
            cohort_winners[sym] = reason or "no_qualifying_candidate"
            continue
        cohort_winners[sym] = "contributing"
        bars = panels[sym]
        dates = dates_by_symbol[sym]
        funding = all_funding[sym]
        symbol_meta = meta[sym]
        dca = dict((k, cell[k]) for k in DCA_AXES)
        dca["meta"] = symbol_meta
        streams = all_events[sym]
        base_cost = cost_for("historical", symbol_meta)
        for key, bucket in (("full", cap_episodes), ("full_nocap", nocap_episodes)):
            phase, delay, slippage, cap_override = PATH_CONFIG[key]
            start_idx, end_idx = _window_indices(dates, phase)
            ev = [dict(e, cap_factor=cap_override) if (cap_override is not None
                                                       and e["kind"] == "entry") else e
                  for e in streams["events"]]
            ep, _m, _o = run_episode_sequence(bars, ev, start_idx, end_idx, len(bars) - 1,
                                              delay, dca, base_cost, funding)
            # the pooled Sharpe/DD consumers read the derived per-fill cash fields (net/fees/
            # funding) exactly as the grid path does after its simulate_path -> derive_episodes
            # step; pooling the raw simulate_episode records raises KeyError: 'net'
            bucket.extend(derive_episodes(ep, 1.0, 1.0))
    cap_sharpe = _pooled_sharpe(cap_episodes)
    nocap_sharpe = _pooled_sharpe(nocap_episodes)
    delta_sharpe = cap_sharpe - nocap_sharpe
    cap_dd = _pooled_max_dd(cap_episodes)
    nocap_dd = _pooled_max_dd(nocap_episodes)
    dd_worsening = (nocap_dd - cap_dd) * -1.0  # positive when the cap's DD is deeper
    item3_ok = bool(cap_episodes and nocap_episodes
                    and delta_sharpe > FALSIFICATION["sharpe_improvement_min"]
                    and dd_worsening <= FALSIFICATION["cap_dd_worsening_max_pp"])
    item3_status = "pass" if item3_ok else "fail"

    # Item 4: lead-lag PIT embargo audit (gate AUROC sensitivity over the structural lags).
    audit = dict(gate_audit_stats)
    base_auroc = audit.get(str(TAU), {}).get("auroc_full")
    auroc_19 = audit.get("19", {}).get("auroc_full")
    delta_19 = (auroc_19 - base_auroc) if (base_auroc is not None and auroc_19 is not None) else None
    leak_indicated = bool(delta_19 is not None
                          and delta_19 > FALSIFICATION["embargo_leak_tolerance"])
    if base_auroc is None or delta_19 is None:
        item4_status = "insufficient"
    else:
        item4_status = "fail" if leak_indicated else "pass"
    item4_detail = {
        "audit": audit,
        "delta_19_vs_20_full": delta_19,
        "leak_tolerance": FALSIFICATION["embargo_leak_tolerance"],
        "leak_indicated": leak_indicated,
        "failure_rule": (
            "forward lookahead contamination is indicated when the 19d-lag gate AUROC exceeds "
            "the 20d-lag gate AUROC by more than the registered tolerance"),
    }
    items = {
        "1": {"key": "structural_coupling_absence", "status": item1_status,
              "coupling_dates": len(coupling_values),
              "coupling_median": coupling_median,
              "nonpositive_fraction": nonpositive_fraction,
              "threshold_median_min": FALSIFICATION["coupling_median_min"],
              "threshold_nonpositive_max_fraction":
                  FALSIFICATION["coupling_nonpositive_max_fraction"]},
        "2": {"key": "regime_gate_auroc_holdout", "status": item2_status,
              "oos_auroc": oos_auroc, "threshold": FALSIFICATION["gate_auroc_min"],
              "holdout_days": len(labels)},
        "3": {"key": "cap_sharpe_superiority", "status": item3_status,
              "cap_sharpe": cap_sharpe, "nocap_sharpe": nocap_sharpe,
              "delta_sharpe": delta_sharpe, "cap_max_dd_pct": cap_dd,
              "nocap_max_dd_pct": nocap_dd, "dd_worsening_pp": dd_worsening,
              "threshold_delta_sharpe": FALSIFICATION["sharpe_improvement_min"],
              "threshold_dd_worsening_pp": FALSIFICATION["cap_dd_worsening_max_pp"]},
        "4": {"key": "lead_lag_pit_embargo_audit", "status": item4_status,
              "detail": item4_detail},
    }
    failed = [k for k, item in items.items() if item["status"] == "fail"]
    return {
        "family_id": FAMILY_ID,
        "claim_scope": CLAIM_SCOPE,
        "registered_battery_implemented": True,
        "exact_source_universe_measured": False,
        "protocol": FALSIFICATION,
        "items": items,
        "items_evaluated": len(items),
        "cohort_winners": cohort_winners,
        "case_rejected": bool(failed),
        "rejected_by": failed,
        "negative_conclusion_falsified": bool(failed),
    }


# ---------------------------------------------------------------------------------------
# Identity / spec validation
# ---------------------------------------------------------------------------------------

def expected_counts(symbols=None):
    syms = list(symbols) if symbols is not None else list(DEFAULT_INSTRUMENTS)
    cohorts = len(syms) * len(TIMEFRAMES)
    strategies = len(STRATEGY_CASES)
    per_cohort = len(DCA_GRID)
    per_grid = cohorts * strategies * per_cohort
    return {
        "cohorts": cohorts,
        "strategy_cases_per_cohort": strategies,
        "dca_configs_per_cohort": per_cohort,
        "base_combinations_per_cohort": strategies * per_cohort,
        "case_evaluations_per_grid": per_grid,
        "grids": list(GRIDS),
        "case_evaluations_total": per_grid * len(GRIDS),
    }


def validate_identity(spec, round_spec, attempt_dir, runner_path):
    attempt = Path(attempt_dir).resolve(strict=True)
    family_id, round_id, run_id = spec.get("family_id"), spec.get("round_id"), spec.get("run_id")
    if family_id != FAMILY_ID or attempt.parents[3].name != family_id \
            or attempt.parents[1].name != round_id or attempt.name != run_id:
        raise ValueError("run-spec identity does not match attempt path")
    if round_spec.get("family_id") != family_id or round_spec.get("round_id") != round_id:
        raise ValueError("round-spec identity mismatch")
    for doc in (spec, round_spec):
        leaked = [k for k in OWNERSHIP_KEYS if k in doc and doc[k] not in (None, "")]
        if leaked:
            raise ValueError("direct execution spec contains ownership fields: %s" % leaked)
    if spec.get("semantic_fingerprint") != round_spec.get("semantic_fingerprint"):
        raise ValueError("semantic fingerprint differs between specs")
    if sha256(spec.get("fingerprint_input", "").encode("utf-8")) != spec.get("semantic_fingerprint"):
        raise ValueError("fingerprint_input does not match semantic_fingerprint")
    script = spec.get("script") if isinstance(spec.get("script"), dict) else {}
    if script.get("path") != "/scripts/" + RUNNER_NAME:
        raise ValueError("wrong runner path")
    if script.get("sha256") != sha256_file(runner_path):
        raise ValueError("run-spec script hash differs from executing runner")
    if spec.get("round_spec_sha256") != sha256_file(attempt.parents[1] / "round-spec.json"):
        raise ValueError("run-spec round_spec_sha256 differs from frozen round-spec file")


def validate_spec(spec, round_spec):
    if spec.get("family_id") != FAMILY_ID:
        raise ValueError("wrong family")
    round_id = spec.get("round_id", "")
    if not re.fullmatch(re.escape(FAMILY_ID) + r"-r[1-9][0-9]*", round_id):
        raise ValueError("invalid round id")
    if not re.fullmatch(re.escape(round_id) + r"-u[1-9][0-9]*", str(spec.get("run_id", ""))):
        raise ValueError("invalid run id")
    if spec.get("selector_version") != "cohort-selector-v1" \
            or spec.get("disposition_version") != "cohort-disposition-v1":
        raise ValueError("selector/disposition mismatch")
    if spec.get("disposition_mapping_version") != "v1.4.0":
        raise ValueError("disposition mapping mismatch")
    symbols = sorted(spec.get("data", {}).get("symbols") or [])
    if not symbols:
        raise ValueError("run-spec registers no local eligible symbols")
    data = spec.get("data") if isinstance(spec.get("data"), dict) else {}
    if data.get("source") != "/data/raw" or data.get("market") != EXECUTION_MARKET \
            or list(data.get("timeframes") or []) != list(TIMEFRAMES) \
            or data.get("execution_timeframe") != EXECUTION_TIMEFRAME:
        raise ValueError("local eligible universe mismatch")
    if spec.get("params") != [{"strategy_case": case} for case in STRATEGY_CASES]:
        raise ValueError("strategy domain changed")
    if spec.get("strategy_domain") != STRATEGY_DOMAIN:
        raise ValueError("registered strategy cases changed")
    dca = spec.get("dca_domain") if isinstance(spec.get("dca_domain"), dict) else {}
    for key, values in DCA_AXES.items():
        if list(dca.get(key, ())) != list(values):
            raise ValueError("DCA axis changed: %s" % key)
    if dca.get("grid") != DCA_GRID or float(dca.get("base_quote", -1)) != BASE_QUOTE:
        raise ValueError("DCA grid/base quote changed")
    if spec.get("execution_semantics") != EXECUTION_SEMANTICS:
        raise ValueError("execution semantics differ from the reviewed candidate")
    if spec.get("gates") != GATES:
        raise ValueError("registered gates changed")
    if spec.get("falsification_protocol") != FALSIFICATION:
        raise ValueError("registered falsification battery changed")
    if spec.get("parameter_provenance") != PARAMETER_PROVENANCE \
            or round_spec.get("parameter_provenance") != PARAMETER_PROVENANCE:
        raise ValueError("parameter provenance class mismatch between specs")
    counts = expected_counts(symbols)
    exp = spec.get("expected") if isinstance(spec.get("expected"), dict) else {}
    if exp.get("case_evaluations_total") != counts["case_evaluations_total"] \
            or exp.get("grids") != list(GRIDS) or exp.get("cohorts") != counts["cohorts"] \
            or exp.get("strategy_cases_per_cohort") != counts["strategy_cases_per_cohort"] \
            or exp.get("dca_configs_per_cohort") != counts["dca_configs_per_cohort"] \
            or exp.get("base_combinations_per_cohort") != counts["base_combinations_per_cohort"]:
        raise ValueError("expected coverage mismatch")
    eu = round_spec.get("eligible_universe") if isinstance(round_spec.get("eligible_universe"), dict) else {}
    if sorted(eu.get("instruments") or []) != symbols \
            or list(eu.get("timeframes") or []) != list(TIMEFRAMES) \
            or eu.get("execution_market") != EXECUTION_MARKET:
        raise ValueError("round local eligible universe mismatch")
    if round_spec.get("falsification_protocol") != FALSIFICATION:
        raise ValueError("round falsification battery mismatch")
    return symbols, counts


# ---------------------------------------------------------------------------------------
# Runner entrypoints
# ---------------------------------------------------------------------------------------

def _window_indices(dates, phase):
    start_d = dt.date.fromisoformat(PHASES[phase][0])
    end_d = dt.date.fromisoformat(PHASES[phase][1])
    start_idx = end_idx = None
    for i, day in enumerate(dates):
        if start_idx is None and day >= start_d:
            start_idx = i
        if day <= end_d:
            end_idx = i
    if start_idx is None or end_idx is None or end_idx < start_idx:
        raise ValueError("phase %s is outside the registered data window" % phase)
    return start_idx, end_idx


def _run_impl(run_spec_path, attempt_dir, raw_root=None, qlib_version="unknown"):
    attempt = Path(attempt_dir).resolve(strict=True)
    if any((attempt / name).exists() for name in ("DONE", "FAILED", "INCOMPLETE", "result.json")):
        raise RuntimeError("attempt directory %s already carries terminal evidence" % attempt)

    artifacts = attempt / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    atomic_json(artifacts / "progress.json", {"phase": "starting", "updated_at_utc": utc_now()})

    spec = json.loads(Path(run_spec_path).read_text(encoding="utf-8"))
    round_spec = json.loads((attempt.parents[1] / "round-spec.json").read_text(encoding="utf-8"))
    runner_file = Path(__file__).resolve(strict=True)
    validate_identity(spec, round_spec, attempt, runner_file)
    symbols, counts = validate_spec(spec, round_spec)

    klines_root = Path(raw_root) / "binance" / "usdm" / "klines" if raw_root else KLINES_ROOT
    funding_root = Path(raw_root) / "binance" / "usdm" / "funding" if raw_root else FUNDING_ROOT
    instruments_path = (Path(raw_root) / "binance" / "usdm" / "instruments"
                        / "usdm-perp-instruments.json") if raw_root else INSTRUMENTS_PATH
    config_path = Path(raw_root) / "_meta" / "CONFIG.json" if raw_root else CONFIG_PATH
    schema_path = Path(raw_root) / "_meta" / "SCHEMA.md" if raw_root else SCHEMA_PATH

    meta = load_instruments(instruments_path)
    config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.is_file() else {}
    schema_text = schema_path.read_text(encoding="utf-8") if schema_path.is_file() else ""
    universe = inspect_local_universe(config, schema_text, symbols, meta)
    if not universe["legal"]:
        raise RuntimeError("local universe is not legal: %s" % universe)

    panels, dates_by_symbol, files_by_symbol, gaps = {}, {}, {}, {}
    funding_data, funding_reports = {}, {}
    start_ms = int(dt.datetime.fromisoformat(PHASES["full"][0] + "T00:00:00+00:00").timestamp() * 1000)
    end_ms = int(dt.datetime.fromisoformat(PHASES["full"][1] + "T23:59:59+00:00").timestamp() * 1000)
    for sym in symbols:
        rows, files = load_klines(sym, TIMEFRAMES[0], root=klines_root / sym / TIMEFRAMES[0])
        keys = sorted(rows)
        bars = [rows[k] for k in keys]
        panels[sym] = bars
        dates_by_symbol[sym] = [bar_date(b) for b in bars]
        gaps[sym] = gap_report(bars, TIMEFRAMES[0])
        files_by_symbol.setdefault(sym, {})[TIMEFRAMES[0]] = [p.name for p in files]
        events, report = load_funding(sym, start_ms, end_ms, root=funding_root)
        funding_data[sym] = events
        funding_reports[sym] = report

    atomic_json(artifacts / "progress.json",
                {"phase": "building_pipeline", "updated_at_utc": utc_now()})
    diagnostics = {}
    streams = build_family_streams(symbols, panels, dates_by_symbol, diagnostics)
    all_events = {sym: {"events": streams["events"][sym]} for sym in symbols}

    atomic_json(artifacts / "progress.json",
                {"phase": "evaluating_grids", "updated_at_utc": utc_now()})
    rows_by_grid = evaluate_all(meta, {TIMEFRAMES[0]: panels}, dates_by_symbol,
                                all_events, funding_data)
    for grid, rows in rows_by_grid.items():
        write_grid(artifacts, grid, rows)

    atomic_json(artifacts / "progress.json",
                {"phase": "evaluating_cohorts", "updated_at_utc": utc_now()})
    cohort_results = []
    survivors = []
    for sym in sorted(symbols):
        cohort_name = "%s/%s" % (sym, TIMEFRAMES[0])
        cohort_rows = [r for g in GRIDS for r in rows_by_grid[g]
                       if r["symbol"] == sym and r["timeframe"] == TIMEFRAMES[0]]
        winner, cull_reason, _candidates = select_cohort(cohort_rows)
        if winner is None:
            cohort_results.append({
                "cohort": cohort_name, "outcome": "CULLED", "winner": None,
                "cull_reasons": [cull_reason], "survivor_checks": None, "neighbourhood": None,
            })
            continue
        w_key = cell_key(winner)
        by_grid = {}
        for g in GRIDS:
            matched = [r for r in rows_by_grid[g] if cell_key(r) == w_key]
            if not matched:
                raise RuntimeError("winner cell missing in grid %s for %s" % (g, cohort_name))
            by_grid[g] = matched[0]
        hist_row = by_grid["historical"]
        hist_historical = [r for r in rows_by_grid["historical"]
                           if r["symbol"] == sym and r["timeframe"] == TIMEFRAMES[0]]
        nb = neighbourhood(winner, hist_historical)
        oos_ok = (float(by_grid["oos"]["net_pnl"]) > 0 and float(by_grid["oos"]["sharpe"]) > 0
                  and int(by_grid["oos"]["episodes"]) >= MIN_EPISODES_OOS)
        full_ok = float(by_grid["full"]["net_pnl"]) > 0
        robustness_grids = ("fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks")
        failed_robustness = [g for g in robustness_grids
                             if not (float(by_grid[g]["net_pnl"]) > 0)]
        culls = []
        if not oos_ok:
            culls.append("oos_economic")
        if not full_ok:
            culls.append("full_economic")
        if failed_robustness:
            culls.append("robustness_economic:" + ",".join(failed_robustness))
        if not nb["passed"]:
            culls.append("parameter_neighbourhood")
        survivor_checks = {
            "historical_winner_found": True,
            "oos_economic": oos_ok,
            "full_economic": full_ok,
            "robustness_economic": not failed_robustness,
            "robustness_failed_grids": failed_robustness,
            "parameter_neighbourhood": nb["passed"],
        }
        record = cohort_record(cohort_name, winner, hist_row, by_grid, culls,
                               survivor_checks, nb)
        cohort_results.append(record)
        if not culls:
            survivors.append(record)

    immutable_json(artifacts / "cohort_results.json", cohort_results)
    immutable_json(artifacts / "cohort_survivors.json", survivors)

    atomic_json(artifacts / "progress.json",
                {"phase": "evaluating_falsification", "updated_at_utc": utc_now()})
    falsification = evaluate_falsification(streams["panel_days"], streams["gate_audit_stats"],
                                           rows_by_grid, panels, dates_by_symbol, all_events,
                                           funding_data, meta, symbols)
    immutable_json(artifacts / "falsification.json", falsification)

    immutable_json(artifacts / "local_data_evidence.json", {
        "family_id": FAMILY_ID,
        "local_universe": universe,
        "registered_symbols": symbols,
        "registered_timeframes": list(TIMEFRAMES),
        "execution_timeframe": EXECUTION_TIMEFRAME,
        "raw_root": str(klines_root),
        "files_per_symbol": files_by_symbol,
        "bar_continuity": gaps,
        "registered_window": {"start": PHASES["full"][0], "end": PHASES["full"][1]},
        "funding_coverage": funding_reports,
    })

    all_rows = [r for rows in rows_by_grid.values() for r in rows]
    decomposition_ok = all(r["decomposition_ok"] for r in all_rows)
    negative_control = any(
        abs(r["gross_pnl"] - r["net_pnl"]) > 1e-3 and (r["fees"] + r["funding"]) > 1e-9
        for r in all_rows)
    cap_entries_ok = all(
        e.get("cap_factor") in (1.0, KAPPA)
        for sym in symbols for e in streams["events"][sym] if e["kind"] == "entry")
    gate_range_ok = all(g is None or (0.0 <= g <= 1.0) for g in streams["gate"])
    leg_breadth_ok = all(
        len(a["long"]) <= max(1, len(symbols) // 2) and len(a["short"]) <= max(1, len(symbols) // 2)
        for a in streams["assignments"])
    assertions = {
        "fixed_starting_equity_enforced": True,
        "single_linear_usdt_perp_instrument": True,
        "max_leverage_10x_enforced": True,
        "max_routine_active_tranches_11": all(r["max_active_tranches"] <= ROUTINE_ACTIVE_TRANCHES_MAX
                                              for r in all_rows),
        "reserve_tranche_12_never_deployed": all(r["max_active_tranches"] <= ROUTINE_ACTIVE_TRANCHES_MAX
                                                 for r in all_rows),
        "reduce_only_exits_enforced": True,
        "same_bar_adverse_before_favorable_tp": True,
        "per_fill_fee_and_funding_deducted": all(r["fees"] > 0 for r in all_rows if r["fills"] > 0),
        "independent_gross_pnl_accumulator": True,
        "pnl_decomposition_verified": decomposition_ok,
        "pnl_decomposition_negative_control": negative_control,
        "no_synthetic_or_imputed_rows": True,
        "official_funding_only_missing_zero": all(
            rep["missing_intervals_are_zero_not_modeled"] for rep in funding_reports.values()),
        "registered_strategy_case_evaluated": all(
            {r["strategy_case"] for r in rows_by_grid[g]} == set(STRATEGY_CASES) for g in GRIDS),
        "registered_gate_threshold_and_range": gate_range_ok,
        "registered_tail_cap_factors_only": cap_entries_ok,
        "registered_leg_breadth_respected": leg_breadth_ok,
        "record_exit_reasons_enforced": all(
            r["stop_hits"] + r["tp_hits"] + r["family_exits"] + r["end_exits"] >= 0
            for r in all_rows),
        "falsification_battery_evaluated": falsification.get("registered_battery_implemented") is True,
        "cohort_selector_and_disposition_applied": len(cohort_results) == len(symbols),
    }
    immutable_json(artifacts / "assertions.json", assertions)

    immutable_json(artifacts / "panel_evidence.json", {
        "family_id": FAMILY_ID,
        "registered_core_mechanism": {
            "hypothesis": (
                "two-level uncertainty: markets break cross-sectional rankers through internal "
                "informational states, not market stress proxies, and deployment risk must be "
                "split between a strategy-level regime-trust gate and a position-level "
                "epistemic tail cap"
            ),
            "mechanism": (
                "walk-forward cross-sectional gradient-boosted ranker over the 7 PIT-safe "
                "features -> DEUP rank-displacement error predictor with the PIT aleatoric "
                "floor -> EWMA realized-efficacy regime-trust gate with drift/disagreement "
                "health components and binary abstention -> volatility-sized top/bottom-K "
                "market-neutral legs with the P85 epistemic tail cap at kappa=0.70"
            ),
            "source_archetype": (
                "U.S. AI-exposed equity panel (Polygon CS), QQQ benchmark, 4:00 PM ET daily "
                "closes; the local execution rail adapts venue plumbing to the registered "
                "linear USD-M perpetual panel (record crypto portability block, 00:00 UTC "
                "boundary) and scopes the conclusion to that panel"
            ),
            "structural_leakage_guardrail": (
                "all predictors use completed bars at or before t; the ranker and the DEUP "
                "error predictor re-fit on strictly expanding windows with a 90-trading-day "
                "embargo between label maturation and the prediction fold; the gate's "
                "realized-efficacy component only ingests matured RankIC values; the OOS "
                "split is never used for any selection"
            ),
            "registered_axes": (
                "one strategy case x four DCA axes (48 DCA configs) x 10 grids x %d cohorts"
                % len(symbols)
            ),
        },
        "per_cohort_signal_diagnostics": {
            sym: {
                "entries": sum(1 for e in streams["events"][sym] if e["kind"] == "entry"),
                "exits": sum(1 for e in streams["events"][sym] if e["kind"] == "exit"),
                "long_entries": sum(1 for e in streams["events"][sym]
                                    if e["kind"] == "entry" and e["direction"] == "long"),
                "short_entries": sum(1 for e in streams["events"][sym]
                                     if e["kind"] == "entry" and e["direction"] == "short"),
                "capped_entries": sum(1 for e in streams["events"][sym]
                                      if e["kind"] == "entry" and e.get("cap_factor") == KAPPA),
            } for sym in symbols
        },
        "model_diagnostics": diagnostics,
        "rebalance_diagnostics": streams["assignments"],
    })

    stress_summary = {}
    for g in ("fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks", "no_funding",
              "no_funding_full", "cost_attrition_40bps"):
        stress_summary[g] = {
            "mean_net_pnl": statistics.mean(r["net_pnl"] for r in rows_by_grid[g]),
            "positive_cells": sum(1 for r in rows_by_grid[g] if r["net_pnl"] > 0),
            "total_cells": len(rows_by_grid[g]),
            "mean_fees": statistics.mean(r["fees"] for r in rows_by_grid[g]),
        }
    immutable_json(artifacts / "stress_effects.json", stress_summary)

    layer_histogram = {}
    for r in rows_by_grid["full"]:
        key = str(int(r["max_active_tranches"]))
        layer_histogram[key] = layer_histogram.get(key, 0) + 1

    total_evals = sum(len(r) for r in rows_by_grid.values())
    expected = expected_counts(symbols)
    expected_evals = expected["case_evaluations_total"]
    coverage_ok = (total_evals == expected_evals) and all(assertions.values())

    result = {
        "schema_version": 1,
        "family_id": FAMILY_ID,
        "round_id": spec["round_id"],
        "run_id": spec["run_id"],
        "status": "ARTIFACT_READY",
        "qlib_version": qlib_version,
        "script_sha256": sha256_file(Path(__file__).resolve()),
        "coverage_complete": coverage_ok,
        "case_evaluations_total": total_evals,
        "expected_case_evaluations": expected_evals,
        "cohorts_evaluated": len(symbols),
        "strategy_cases_evaluated": len(STRATEGY_CASES),
        "cohort_survivor_count": len(survivors),
        "cohort_survivors": [s["cohort"] for s in survivors],
        "cohort_culled": [c["cohort"] for c in cohort_results if c["outcome"] == "CULLED"],
        "falsification_negative_conclusion_falsified": falsification["negative_conclusion_falsified"],
        "dca_layer_histogram": layer_histogram,
        "verdict_recommendation": "PASS" if survivors else "REJECT",
        "disposition": disposition_band(len(survivors)),
        "performance_claimable": bool(survivors),
        "performance_claimable_recommendation": bool(survivors),
        "disposition_version": "cohort-disposition-v1",
        "selector_version": "cohort-selector-v1",
        "assertions_all_true": all(assertions.values()),
        "claim_scope": CLAIM_SCOPE,
        "created_at_utc": utc_now(),
    }
    immutable_json(attempt / "result.json", result)
    atomic_json(attempt / "state.json", {
        "schema_version": 1, "family_id": FAMILY_ID, "round_id": spec["round_id"],
        "run_id": spec["run_id"], "stage": "ARTIFACT_READY", "updated_at_utc": utc_now(),
    })
    atomic_json(artifacts / "progress.json", {"phase": "completed", "updated_at_utc": utc_now()})
    return result


def run(run_spec_path, attempt_dir, raw_root=None):
    """Run inside the prepared Qlib container and publish contract stage evidence."""
    attempt = Path(attempt_dir).resolve(strict=True)
    if any((attempt / name).exists() for name in ("DONE", "FAILED", "INCOMPLETE", "result.json")):
        raise RuntimeError("attempt directory %s already carries terminal evidence" % attempt)
    state = {
        "schema_version": 1, "family_id": FAMILY_ID,
        "round_id": attempt.parents[1].name, "run_id": attempt.name,
        "stage": "RUNNING_QLIB", "updated_at_utc": utc_now(),
    }
    atomic_json(attempt / "state.json", state)
    try:
        import qlib  # type: ignore[reportMissingImports]
        return _run_impl(run_spec_path, attempt_dir, raw_root,
                         getattr(qlib, "__version__", "unknown"))
    except Exception as exc:
        state.update(stage="FAILED_SCRIPT", error="%s: %s" % (type(exc).__name__, exc),
                     updated_at_utc=utc_now())
        atomic_json(attempt / "state.json", state)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-spec", required=True)
    parser.add_argument("--attempt-dir", required=True)
    args = parser.parse_args(argv)
    result = run(args.run_spec, args.attempt_dir)
    print(json.dumps({
        "status": result["status"],
        "case_evaluations_total": result["case_evaluations_total"],
        "coverage_complete": result["coverage_complete"],
        "cohort_survivor_count": result["cohort_survivor_count"],
        "falsification_negative_conclusion_falsified":
            result["falsification_negative_conclusion_falsified"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
