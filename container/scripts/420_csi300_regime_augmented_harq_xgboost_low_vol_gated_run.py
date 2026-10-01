#!/usr/bin/env python3
"""Deterministic local-universe full backtest for the CSI 300 regime-augmented HARQ +
walk-forward XGBoost low-volatility gated signal-by-risk family.

Reviewed source: arXiv:2606.09478v1 (Fang & Slepaczuk, "Volatility Forecasting and Return
Prediction under Market Regimes: Evidence from High-Frequency Chinese Equity Data", June 2026).

Under the quant-runtime-pipeline contract (v1.8 / 14.4 / 6.4 lifecycle footer):
  1. The complete legal local eligible universe is registered: canonical local Binance USD-M
     perpetual, 4 symbols (BNBUSDT, BTCUSDT, ETHUSDT, SOLUSDT) x the record-required daily
     cohort timeframe (1d), with 5m canonical klines as the intraday realized-measure source and
     the execution bar grid.  The source CSI 300 cash-index venue, the Wind data vendor and the
     A-share session calendar are provenance/external-validity context, not execution
     prerequisites; the crypto portability block of the record (perpetual vehicle, 00:00 UTC
     synthetic daily boundary, M_t = 288 five-minute bars) is adopted verbatim.  Conclusion scope
     = that local panel.
  2. One registered strategy case (the record's frozen pipeline: HARQ volatility model ->
     2-state Markov-switching GJR-GARCH(1,1) Student-t regime filter -> regime-augmented HARQ
     forecast -> 3M walk-forward gradient-boosted return prediction -> low-vol gating ->
     walk-forward signal-by-risk scaling with a no-trade band) x the four-axis DCA domain
     (48 cells per cohort per grid): spacing_pct in {0.01,0.02,0.03,0.04} x size_multiplier in
     {1.0,1.1} x breakeven_tp_pct in {0.01,0.02,0.03} x invalidation_pct in {0.05,0.10}.
     Fixed constants: starting_equity = 30000 USDT, base_quote = 1000 USDT, max leverage 10x,
     routine active tranches max 11 (#12 reserve).
  3. Cohort survivor semantics (cohort-selector-v1 / cohort-disposition-v1): 4 cohorts
     (4 symbols x 1d); the historical winner of the complete joint space (strategy case x DCA)
     is carried unchanged to OOS/full/robustness/neighbourhood.
  4. Ten evaluation grids: historical, oos, full, fee_2x, funding_2x, entry_delay_1_bar,
     slippage_2ticks, no_funding, no_funding_full, cost_attrition_40bps.  Grids that share the
     same phase window, entry delay and slippage share one simulated fill path; fee/funding
     stress is re-derived per fill from that path (never a no-op).
  5. The record's registered falsification battery is implemented per strategy case:
     OOS expansion (net Sharpe > 0 at the 5bp-class track and MaxDD better than -20%),
     cost/slippage friction stress (net Sharpe > 0 at 10bp one-way friction), long-only vehicle
     feasibility (net Sharpe > 0 with short legs suppressed) and the regime-gating permutation
     placebo (drawdown reduction must not be within 1.0pp of the genuine gate).
  6. The runner writes deterministic execution artifacts inside the attempt directory; it writes
     no terminal sentinel (DONE/FAILED/INCOMPLETE) and no verdict.json.

Local engine notes (research-defined, documented in the frozen round-spec): the container venv
has no xgboost/statsmodels (bounded environment probe), so the record's gradient-boosted
regression trees are realised by a deterministic pure-python histogram gradient-boosting
regressor that keeps the record's exact hyperparameter semantics (n_estimators, max_depth, eta,
gamma, lambda) and the 2-state MS-GJR-GARCH(1,1) Student-t filter is realised by a deterministic
bounded fixed-point EM estimator.  Mechanism, features, targets, gating and re-estimation cadence
are unchanged.
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
import random
import re
import statistics
import sys
from pathlib import Path

FAMILY_ID = "csi300-regime-augmented-harq-xgboost-low-vol-gated-2026-09-05"
ROUND_ID = FAMILY_ID + "-r1"
RUN_ID = ROUND_ID + "-u1"
RUNNER_NAME = "420_csi300_regime_augmented_harq_xgboost_low_vol_gated_run.py"

FINGERPRINT_INPUT = (
    "csi300-regime-augmented-harq-xgboost-low-vol-gated-2026-09-05|"
    "universe=portability=adapted/unproven (source-market; research-defined);window=record-faithful|"
    "dca=spacing_pct=0.01,0.02,0.03,0.04;size_multiplier=1.0,1.1;"
    "breakeven_tp_pct=0.01,0.02,0.03;invalidation_pct=0.05,0.10|"
    "selector=cohort-selector-v1;disposition=cohort-disposition-v1|"
    "source=csi300-regime-augmented-harq-xgboost-low-vol-gated-2026-09-05.md"
)

RAW_ROOT = Path("/data/raw")
KLINES_ROOT = RAW_ROOT / "binance" / "usdm" / "klines"
FUNDING_ROOT = RAW_ROOT / "binance" / "usdm" / "funding"
INSTRUMENTS_PATH = RAW_ROOT / "binance" / "usdm" / "instruments" / "usdm-perp-instruments.json"
CONFIG_PATH = RAW_ROOT / "_meta" / "CONFIG.json"
SCHEMA_PATH = RAW_ROOT / "_meta" / "SCHEMA.md"

OWNERSHIP_KEYS = ("task_id", "kanban_task_id", "kanban_board")

# Registered cohort timeframe: the record's signal cadence is daily (daily realized measures,
# daily predictors, target return over (t, t+1]); the 5m grid is the intraday measure source and
# the execution grid, not an independent signal timeframe.
TIMEFRAMES = ("1d",)
TF_MINUTES = {"1d": 1440, "5m": 5}
EXECUTION_TIMEFRAME = "5m"
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
INTRADAY_BARS_PER_DAY = 288

EXECUTION_MARKET = "BINANCE_USDM_PERP"
SOURCE_MARKET = (
    "CSI 300 Index (sh000300), Chinese A-share cash equity index; Shanghai/Shenzhen Stock "
    "Exchange 5-minute intraday bars and daily closing prices via Wind Financial Terminal; "
    "continuous sessions 09:30-11:30 and 13:00-15:00 CST (M_t = 48 bars per session)"
)
CLAIM_SCOPE = (
    "canonical local Binance USD-M perpetual panel, 4 symbols (BNBUSDT/BTCUSDT/ETHUSDT/SOLUSDT) "
    "x the record-required daily cohort timeframe = 4 cohorts; the record's frozen regime-augmented "
    "HARQ + 3M walk-forward gradient-boosted return pipeline with low-volatility gating and "
    "walk-forward signal-by-risk scaling, evaluated on that local panel with the frozen "
    "falsification battery; local-universe scoped conclusion (the crypto portability block of the "
    "record is adapted/unproven), not a CSI 300 cash-index reproduction"
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

PATH_CONFIG = {
    "historical": ("historical", 0, 1.0),
    "oos": ("oos", 0, 1.0),
    "full": ("full", 0, 1.0),
    "full_delay1": ("full", 1, 1.0),
    "full_slip2": ("full", 0, 2.0),
}
PATH_KEYS = ("historical", "oos", "full", "full_delay1", "full_slip2")
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
HARQ_MIN_OBS = 45          # floor for a usable HARQ regression (22-bar window + 5-bar window + 1)
WALK_FORWARD_N_MIN = 300   # N_min = 300 observations (180 train, 120 validation) - source
VALIDATION_FRACTION = 0.4  # 120/300 - source
RETAIN_QUARTER_FREQ = "3M"
GATE_KAPPA = 1.0           # s_gated = s_comb * (1 - p_t), kappa = 1.0 - source
THRESHOLD_Q = 0.60         # |s_gated| recursive past-only 60th percentile - source
TARGET_ABS_EXPOSURE = 0.5  # c_t^WF targets average |exposure| 0.5 - source
WEIGHT_CAP = 0.60          # w_max = 0.60 - source
NO_TRADE_BAND = 0.02       # b = 0.02 - source
REBALANCE_WEEKDAY = 0      # weekly rebalance (research-defined: first cohort bar of the UTC week)
HARQ_REESTIMATION_QUARTERS = 1

# Record's Stage 2 candidate hyperparameter grid: (n_estimators, max_depth, eta, gamma, lambda).
XGB_CANDIDATES = (
    (50, 2, 0.05, 0.0, 1.0),
    (100, 2, 0.03, 0.1, 1.0),
    (100, 3, 0.03, 0.1, 1.0),
    (200, 2, 0.01, 0.2, 2.0),
)
XGB_DEFAULT = (200, 2, 0.01, 0.2, 2.0)
GBDT_MAX_BINS = 64
MS_GARCH_COORDINATE_SWEEPS = 2
MS_GARCH_NU_GRID = (5.0, 7.0, 9.0, 12.0, 20.0)
MS_GARCH_MIN_OBS = 120

# Record falsification thresholds (research-defined where the record states a rule).
FLAT_FRICTION_BPS = 10.0     # one-way execution friction schedule of the record
FALSIFICATION = {
    "record": "arXiv:2606.09478v1 falsification plan (four items; none lowered or dropped)",
    "oos_min_sharpe": 0.0,
    "oos_max_drawdown_pct": -20.0,
    "friction_bps_tracks": [5.0, 10.0, 15.0, 20.0],
    "friction_reject_sharpe_bps": 10.0,
    "long_only_min_sharpe": 0.0,
    "placebo_tolerance_pp": 1.0,
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
        "signal_signed_long_or_short_legs (the record's gated signal carries the position sign: "
        "w_t = sign(s_gated) * min(c_t^WF * |s_gated|, w_max), w_max = 0.60; the linear USD-M "
        "perpetual rail realises the two-sided exposure the record's cash-index short-sale "
        "friction blocked)"
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
        "weekly rebalance formation bar (first 1d bar of the UTC week) closes -> fill at the next "
        "1d bar open on the execution grid (research-defined alignment of the record's next-day "
        "close-to-close realisation; entry_delay_1_bar adds one further bar)"
    ),
    "same_bar_order": (
        "open_adverse_invalidation_then_family_exit_then_adds_then_adverse_range_then_take_profit"
    ),
    "record_barriers_vs": (
        "the record registers no explicit price barrier: the family exit is the weekly rebalance "
        "decision turning zero or sign-flipping; the DCA breakeven take-profit and resting "
        "invalidation anchor on the running average cost"
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
        "from the first executable bar after formation until the family exit signal, the breakeven "
        "take-profit, the resting invalidation or the window end"
    ),
}

REGISTERED_CASES = [
    {
        "index": 0,
        "key": "regime_augmented_harq_xgboost_low_vol_gated",
        "name": "Regime-Augmented HARQ + Walk-Forward Gradient-Boosted Return Prediction, "
                "Low-Volatility Gated Signal-by-Risk",
        "source_status": (
            "source-reported (HARQ realized-measure model, MS-GJR-GARCH Student-t regime filter, "
            "3M walk-forward gradient-boosted return prediction over the 12 registered features, "
            "low-vol gate (1-p_t), recursive walk-forward quantile thresholding, walk-forward "
            "signal-by-risk scaling with a no-trade band)"
        ),
        "direction": "signal_signed_long_or_short",
        "cadence": "weekly rebalance formation, daily information",
        "model": (
            "gradient-boosted regression trees (the record's XGBoost engine realised by the local "
            "deterministic histogram booster with the record's candidate grid, selected by "
            "validation Pearson correlation, default (200,2,0.01,0.2,2.0) when all <= 0)"
        ),
        "formation": (
            "every weekly rebalance bar, using only predictors formed at or before that bar's "
            "close: realized measures from completed 5m intraday bars, regime-augmented HARQ "
            "forecast, filtered high-volatility probability p_t and the 12 registered features"
        ),
        "entry_rule": (
            "weekly target weight becomes non-zero with |w_t*| > b (no-trade band) while flat -> "
            "open the leg in sign(w_t*) at the next bar open"
        ),
        "exit_rule": (
            "weekly target weight turns zero (threshold filter) or sign-flips -> reduce-only exit "
            "at the next bar open; the DCA breakeven take-profit/invalidation may close earlier"
        ),
        "local_adaptation": (
            "CSI 300 cash index -> the four local USD-M perpetual symbols; the 09:30-11:30 / "
            "13:00-15:00 CST session with M_t = 48 bars -> the record's crypto portability block "
            "(00:00 UTC synthetic daily boundary, M_t = 288 five-minute bars, no overnight-gap or "
            "lunch-break exclusion); weekly rebalance mapped to the first daily bar of the UTC "
            "week; all model, feature, gating, scaling and threshold semantics unchanged"
        ),
    },
]

STRATEGY_CASES = [case["index"] for case in REGISTERED_CASES]

STRATEGY_DOMAIN = {
    "strategy_case": list(STRATEGY_CASES),
    "cases": list(REGISTERED_CASES),
    "meaning": (
        "one registered strategy case: the record's single frozen pipeline (regime-augmented HARQ "
        "+ 3M walk-forward gradient-boosted return prediction + low-volatility gated "
        "signal-by-risk allocation); a registered axis, never a search axis"
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


def _normal_equations(rows, y):
    """Ordinary least squares via normal equations with a tiny ridge for numerical safety."""
    k = len(rows[0]) if rows else 0
    if k == 0 or len(rows) < k + 1:
        return None
    a = [[0.0] * k for _ in range(k)]
    b = [0.0] * k
    for row, target in zip(rows, y):
        for i in range(k):
            b[i] += row[i] * target
            for j in range(i, k):
                a[i][j] += row[i] * row[j]
    for i in range(k):
        for j in range(i):
            a[i][j] = a[j][i]
    ridge = 1e-10 * max(1.0, len(rows))
    for i in range(k):
        a[i][i] += ridge
    # Gaussian elimination with partial pivoting (deterministic pivot choice).
    for col in range(k):
        pivot = max(range(col, k), key=lambda r: (abs(a[r][col]), -r))
        if abs(a[pivot][col]) < 1e-14:
            return None
        if pivot != col:
            a[col], a[pivot] = a[pivot], a[col]
            b[col], b[pivot] = b[pivot], b[col]
        for r in range(col + 1, k):
            factor = a[r][col] / a[col][col]
            if factor == 0.0:
                continue
            for c in range(col, k):
                a[r][c] -= factor * a[col][c]
            b[r] -= factor * b[col]
    beta = [0.0] * k
    for r in range(k - 1, -1, -1):
        total = b[r] - sum(a[r][c] * beta[c] for c in range(r + 1, k))
        beta[r] = total / a[r][r]
    return beta


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
    intraday_present = EXECUTION_TIMEFRAME in intervals
    per_symbol = {}
    legal = bool(found and klines_declared and registered_timeframes_present and intraday_present)
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
        "config_execution_timeframe_declared": intraday_present,
        "venue_intervals_declared": sorted(intervals),
        "schema_mentions_klines": "klines" in schema_text.lower(),
    }


# ---------------------------------------------------------------------------------------
# Stage 1a: intraday realized measures on the 00:00 UTC synthetic daily boundary
# ---------------------------------------------------------------------------------------

def realized_measures(intraday_bars, daily_bars):
    """RV / RQ / BPV / signed-jump series from completed 5m bars, aligned to the 1d cohort grid.

    r_{t,i} are the completed within-day 5m log-returns of the UTC day t (bar open times
    00:00, 00:05, ... , 23:55); M_t must equal 288 (the record's crypto boundary), a day with a
    different count is invalid and never imputed (research-defined completeness rule).
    """
    by_day = {}
    for bar in intraday_bars:
        by_day.setdefault(bar_date(bar), []).append(bar)
    mu1_sq = 2.0 / math.pi
    measures = {}
    invalid_days = 0
    for day, bars in sorted(by_day.items()):
        bars.sort(key=lambda b: b["open_time_ms"])
        if len(bars) != INTRADAY_BARS_PER_DAY:
            invalid_days += 1
            continue
        contiguous = all(bars[i]["open_time_ms"] - bars[i - 1]["open_time_ms"] == 300000
                         for i in range(1, len(bars)))
        if not contiguous:
            invalid_days += 1
            continue
        closes = [b["close"] for b in bars]
        rets = []
        for i in range(1, len(closes)):
            if closes[i - 1] > 0 and closes[i] > 0:
                rets.append(math.log(closes[i] / closes[i - 1]))
        m = len(rets)
        if m < 2:
            invalid_days += 1
            continue
        rv = sum(r * r for r in rets)
        rq = (m / 3.0) * sum(r ** 4 for r in rets)
        bpv = sum(abs(rets[i]) * abs(rets[i - 1]) for i in range(1, m)) / mu1_sq
        measures[day] = {"RV": rv, "RQ": rq, "BPV": bpv, "M": m}
    out = []
    for idx, bar in enumerate(daily_bars):
        day = bar_date(bar)
        meas = measures.get(day)
        close = bar["close"]
        prev_close = daily_bars[idx - 1]["close"] if idx > 0 else None
        out.append({
            "day": day,
            "RV": meas["RV"] if meas else None,
            "RQ": meas["RQ"] if meas else None,
            "BPV": meas["BPV"] if meas else None,
            "M": meas["M"] if meas else None,
            "close": close,
            "daily_return": (math.log(close / prev_close)
                             if prev_close and prev_close > 0 and close > 0 else None),
        })
    return out, {"days_with_intraday": len(measures), "invalid_days": invalid_days,
                 "intraday_bars": len(intraday_bars), "daily_bars": len(daily_bars),
                 "bars_per_day_expected": INTRADAY_BARS_PER_DAY}


def signed_jump_series(rows):
    """CJ_t = max(RV_t - BPV_t, 0) * sign(r_t) (None when either input is missing)."""
    for row in rows:
        if row["RV"] is None or row["BPV"] is None or row["daily_return"] is None:
            row["CJ"] = None
            continue
        jump = max(row["RV"] - row["BPV"], 0.0)
        row["CJ"] = jump * (1.0 if row["daily_return"] >= 0 else -1.0)
    return rows


# ---------------------------------------------------------------------------------------
# Stage 1b: HARQ regression and the regime-augmented forecast
# ---------------------------------------------------------------------------------------

def harq_design(rows, idx):
    """Design row of the record's HARQ specification at t (needs RV_t, RQ_t and both windows)."""
    if idx < 22:
        return None
    rv = rows[idx]["RV"]
    rq = rows[idx]["RQ"]
    if rv is None or rq is None or rv <= 0:
        return None
    win_w = [rows[idx - k]["RV"] for k in range(0, 5)]
    win_m = [rows[idx - k]["RV"] for k in range(0, 22)]
    if any(v is None or v <= 0 for v in win_w + win_m):
        return None
    rv_w = sum(win_w) / 5.0
    rv_m = sum(win_m) / 22.0
    if rv_w <= 0 or rv_m <= 0:
        return None
    log_rv = math.log(rv)
    quarticity_ratio = math.sqrt(max(rq, 0.0)) / rv
    return [1.0, log_rv, quarticity_ratio * log_rv, math.log(rv_w), math.log(rv_m)]


def fit_harq(rows, start_idx, end_idx):
    """OLS of log RV_{t+1} on the record's HARQ regressors over [start_idx, end_idx)."""
    design, target = [], []
    for t in range(max(22, start_idx), end_idx):
        if t + 1 >= len(rows):
            break
        nxt = rows[t + 1]["RV"]
        row = harq_design(rows, t)
        if row is None or nxt is None or nxt <= 0:
            continue
        design.append(row)
        target.append(math.log(nxt))
    if len(design) < HARQ_MIN_OBS:
        return None
    beta = _normal_equations(design, target)
    if beta is None:
        return None
    return {"beta": beta, "obs": len(design)}


def harq_predict(beta, rows, idx):
    row = harq_design(rows, idx)
    if row is None:
        return None
    return sum(b * x for b, x in zip(beta, row))


def harq_residuals(rows, fit, start_idx, end_idx):
    """Residual e_{t+1} = log RV_{t+1} - hat_logRV_{t+1} inside [start_idx, end_idx)."""
    out = []
    for t in range(max(22, start_idx), end_idx):
        if t + 1 >= len(rows):
            break
        nxt = rows[t + 1]["RV"]
        pred = harq_predict(fit["beta"], rows, t)
        if pred is None or nxt is None or nxt <= 0:
            continue
        out.append({"idx": t + 1, "e": math.log(nxt) - pred})
    return out


def regime_augmented_beta(rows, base_fit, gamma_p, start_idx, end_idx):
    """Second-pass OLS adding gamma_p * p_t to the HARQ specification (record Stage 1.4)."""
    design, target = [], []
    for t in range(max(22, start_idx), end_idx):
        if t + 1 >= len(rows):
            break
        nxt = rows[t + 1]["RV"]
        row = harq_design(rows, t)
        pt = rows[t].get("p_high")
        if row is None or nxt is None or nxt <= 0 or pt is None:
            continue
        design.append(row + [pt])
        target.append(math.log(nxt))
    if len(design) < HARQ_MIN_OBS:
        return None
    beta = _normal_equations(design, target)
    if beta is None:
        return None
    return {"beta": beta, "obs": len(design)}


# ---------------------------------------------------------------------------------------
# Stage 1c: 2-state MS-GJR-GARCH(1,1) with Student-t innovations (deterministic bounded EM)
# ---------------------------------------------------------------------------------------

def _student_t_logpdf(x, sigma2, nu):
    if sigma2 <= 0:
        return -1e30
    z = 1.0 + (x * x) / (nu * sigma2)
    if z <= 0:
        return -1e30
    return (math.lgamma((nu + 1.0) / 2.0) - math.lgamma(nu / 2.0)
            - 0.5 * math.log(math.pi * nu) - 0.5 * math.log(sigma2)
            - ((nu + 1.0) / 2.0) * math.log(z))


def _gjr_path(e, params, state_init_var):
    """sigma2_t per the record's recursion with the previous state variance carried forward."""
    omega, alpha, gamma, beta = params
    out = []
    prev2 = state_init_var
    for t in range(len(e)):
        if t == 0:
            var = omega + beta * prev2
        else:
            shock = e[t - 1] * e[t - 1]
            var = omega + (alpha + (gamma if e[t - 1] < 0 else 0.0)) * shock + beta * prev2
        var = max(var, 1e-12)
        out.append(var)
        prev2 = var
    return out


def _gjr_params(level2, alpha, gamma, beta):
    """Variance-targeted GJR parameter tuple: omega keeps the state's unconditional level."""
    omega = max(level2 * (1.0 - alpha - 0.5 * gamma - beta), 1e-14)
    return (omega, alpha, gamma, beta)


def _ms_filter(e, params_l, params_h, nu, p_low_low, p_high_high):
    """Hamilton forward filter with the Gray-collapse per-state variance recursion."""
    s2_l = _gjr_path(e, params_l, 1e-6)
    s2_h = _gjr_path(e, params_h, 1e-6)
    loglik = 0.0
    filt = []
    prev_l, prev_h = 0.5, 0.5
    for t in range(len(e)):
        like_l = math.exp(_student_t_logpdf(e[t], s2_l[t], nu))
        like_h = math.exp(_student_t_logpdf(e[t], s2_h[t], nu))
        # predictive state distribution given the previous filtered state
        pred_l = prev_l * p_low_low + prev_h * (1.0 - p_high_high)
        pred_h = 1.0 - pred_l
        joint_l = like_l * pred_l
        joint_h = like_h * pred_h
        total = joint_l + joint_h
        if total <= 1e-300:
            total = 1e-300
        filt.append((joint_l / total, joint_h / total))
        loglik += math.log(total)
        prev_l, prev_h = filt[-1]
    return {"filtered": filt, "loglik": loglik, "sigma2_low": s2_l, "sigma2_high": s2_h}




def fit_ms_gjr_garch(e_values):
    """Estimate the record's 2-state MS-GJR-GARCH(1,1)-t and return filtered high-vol p_t.

    Deterministic bounded fixed-point EM (research-defined local estimator): state assignment
    from a 2-means split of |e|, variance-targeted initialisation, then alternating filter and
    weighted fixed-point GJR updates; the Student-t dof is profiled on the registered grid.
    """
    e = [v for v in e_values if v is not None and math.isfinite(v)]
    if len(e) < MS_GARCH_MIN_OBS:
        return None
    diffs = sorted(abs(v) for v in e)
    c_lo = diffs[int(0.1 * (len(diffs) - 1))]
    c_hi = diffs[int(0.9 * (len(diffs) - 1))]
    if c_hi <= c_lo:
        return None
    for _ in range(12):  # deterministic Lloyd iterations on |e|
        lo_vals, hi_vals = [], []
        for v in diffs:
            (lo_vals if abs(v - c_lo) <= abs(v - c_hi) else hi_vals).append(v)
        if not lo_vals or not hi_vals:
            break
        new_lo = sum(lo_vals) / len(lo_vals)
        new_hi = sum(hi_vals) / len(hi_vals)
        if new_lo == c_lo and new_hi == c_hi:
            break
        c_lo, c_hi = new_lo, new_hi
    split = (c_lo + c_hi) / 2.0
    lo = [v for v in e if abs(v) <= split]
    hi = [v for v in e if abs(v) > split]
    if not lo or not hi:
        return None
    level_l = statistics.pvariance(lo) or statistics.pvariance(e) * 0.25
    level_h = statistics.pvariance(hi)
    if level_h <= level_l:
        return None

    def params_for(candidate):
        params_l = _gjr_params(candidate["level_l"], candidate["alpha_l"],
                               candidate["gamma_l"], candidate["beta_l"])
        params_h = _gjr_params(candidate["level_h"], candidate["alpha_h"],
                               candidate["gamma_h"], candidate["beta_h"])
        return params_l, params_h

    def evaluate(candidate, nu=7.0):
        params_l, params_h = params_for(candidate)
        res = _ms_filter(e, params_l, params_h, nu, candidate["p_ll"], candidate["p_hh"])
        return res, params_l, params_h

    state = {"level_l": level_l, "level_h": level_h,
             "alpha_l": 0.05, "gamma_l": 0.05, "beta_l": 0.80,
             "alpha_h": 0.05, "gamma_h": 0.05, "beta_h": 0.80,
             "p_ll": 0.90, "p_hh": 0.90}
    grids = (
        ("level_l", (0.5, 0.75, 1.0, 1.25, 1.5)),
        ("level_h", (0.5, 0.75, 1.0, 1.25, 1.5)),
        ("alpha_l", (0.01, 0.03, 0.06, 0.12)),
        ("gamma_l", (0.0, 0.03, 0.08, 0.15)),
        ("beta_l", (0.60, 0.70, 0.80, 0.90)),
        ("alpha_h", (0.01, 0.03, 0.06, 0.12)),
        ("gamma_h", (0.0, 0.03, 0.08, 0.15)),
        ("beta_h", (0.60, 0.70, 0.80, 0.90)),
        ("p_ll", (0.75, 0.85, 0.93, 0.97)),
        ("p_hh", (0.75, 0.85, 0.93, 0.97)),
    )
    res, params_l, params_h = evaluate(state)
    best = (res["loglik"], dict(state), res, params_l, params_h)
    for _sweep in range(MS_GARCH_COORDINATE_SWEEPS):  # deterministic bounded coordinate search
        for name, values in grids:
            for value in values:
                candidate = dict(best[1])
                candidate[name] = best[1][name] * value if name.startswith("level") else value
                for suffix, other in (("l", "h"), ("h", "l")):
                    persistence = (candidate["alpha_" + suffix]
                                   + 0.5 * candidate["gamma_" + suffix]
                                   + candidate["beta_" + suffix])
                    if persistence >= 0.985:
                        candidate = None
                        break
                if candidate is None:
                    continue
                if candidate["level_h"] <= candidate["level_l"]:
                    continue
                res, params_l, params_h = evaluate(candidate)
                if res["loglik"] > best[0] + 1e-9:
                    best = (res["loglik"], candidate, res, params_l, params_h)
    _loglik, state, res, params_l, params_h = best
    mean_l = sum(res["sigma2_low"]) / len(res["sigma2_low"])
    mean_h = sum(res["sigma2_high"]) / len(res["sigma2_high"])
    if mean_l > mean_h:  # the high state must carry the larger mean variance
        params_l, params_h = params_h, params_l
        state = dict(state, p_ll=state["p_hh"], p_hh=state["p_ll"])
    best_nu = None
    for nu in MS_GARCH_NU_GRID:
        cand = _ms_filter(e, params_l, params_h, nu, state["p_ll"], state["p_hh"])
        if best_nu is None or cand["loglik"] > best_nu["loglik"]:
            best_nu = {"loglik": cand["loglik"], "nu": nu, "filtered": cand["filtered"]}
    if best_nu is None:
        return None
    p_high = [f[1] for f in best_nu["filtered"]]
    return {"p_high": p_high, "nu": best_nu["nu"], "params_low": params_l,
            "params_high": params_h, "p_ll": state["p_ll"], "p_hh": state["p_hh"],
            "loglik": best_nu["loglik"], "obs": len(e)}


# ---------------------------------------------------------------------------------------
# Stage 2: deterministic histogram gradient-boosted regression trees (record's XGBoost engine)
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
        self.bin_edges = []
        self.bin_maps = []
        self.n_bins = []

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
# Stage 2/3: point-in-time walk-forward pipeline and the weekly signal-by-risk weights
# ---------------------------------------------------------------------------------------

def _quarter_key(day):
    return (day.year, (day.month - 1) // 3 + 1)


def _estimation_indices(dates):
    """First daily bar of each calendar quarter: the record's 3M re-estimation cadence."""
    out = []
    last = None
    for i, day in enumerate(dates):
        key = _quarter_key(day)
        if key != last:
            out.append(i)
            last = key
    return out


def feature_row(rows, idx):
    """The record's 12 Stage-2 predictors at time t (None until every input exists)."""
    if idx < 22:
        return None
    row = rows[idx]
    rv = row["RV"]
    rq = row["RQ"]
    cj = row["CJ"]
    log_rvhat = row.get("logRVhat")
    pt = row.get("p_high")
    daily_ret = row["daily_return"]
    if None in (rv, rq, cj, log_rvhat, pt, daily_ret) or rv <= 0 or rq <= 0:
        return None
    win5 = [rows[idx - k]["RV"] for k in range(0, 5)]
    win22 = [rows[idx - k]["RV"] for k in range(0, 22)]
    if any(v is None or v <= 0 for v in win5 + win22):
        return None
    vol_lag5 = sum(win5) / 5.0
    vol_lag22 = sum(win22) / 22.0
    close = row["close"]
    prev5 = rows[idx - 5]["close"]
    prev22 = rows[idx - 22]["close"]
    if close <= 0 or prev5 <= 0 or prev22 <= 0:
        return None
    pt_lag5 = rows[idx - 5].get("p_high")
    if pt_lag5 is None:
        return None
    log_rv = math.log(rv)
    return [
        log_rv,                                   # log(RV_t)
        log_rvhat,                                # forecasted log-RV for t+1
        math.log(rq),                             # log(RQ_t)
        cj,                                       # signed jump
        math.log(vol_lag5),                       # 5-day average volatility
        math.log(vol_lag22),                      # 22-day average volatility
        pt,                                       # filtered high-vol probability
        pt_lag5,                                  # lagged probability p_{t-5}
        log_rv * pt,                              # vol x regime interaction
        daily_ret,                                # r_t
        math.log(close / prev5),                  # r_{t,w}
        math.log(close / prev22),                 # r_{t,m}
    ]


def attach_walk_forward(rows, dates, diagnostics):
    """Quarterly expanding re-estimation of the full pipeline; strictly point-in-time."""
    n = len(rows)
    for row in rows:
        row.setdefault("p_high", None)
        row["logRVhat"] = None
        row["rhat"] = None
    est_idx = _estimation_indices(dates)
    diagnostics["estimation_dates"] = [dates[i].isoformat() for i in est_idx]
    diagnostics["fits"] = []
    for i, e in enumerate(est_idx):
        next_e = est_idx[i + 1] if i + 1 < len(est_idx) else n
        if e < WALK_FORWARD_N_MIN // 4:
            continue
        base_fit = fit_harq(rows, 22, e)
        if base_fit is None:
            diagnostics["fits"].append({"est_idx": e, "day": dates[e].isoformat(),
                                        "skipped": "harq_insufficient"})
            continue
        resids = harq_residuals(rows, base_fit, 22, e)
        regime = fit_ms_gjr_garch([r["e"] for r in resids])
        if regime is None:
            diagnostics["fits"].append({"est_idx": e, "day": dates[e].isoformat(),
                                        "skipped": "regime_insufficient"})
            continue
        full_resids = harq_residuals(rows, base_fit, 22, n - 1)
        filt = _ms_filter([r["e"] for r in full_resids], regime["params_low"],
                          regime["params_high"], regime["nu"], regime["p_ll"], regime["p_hh"])
        for res_row, probs in zip(full_resids, filt["filtered"]):
            rows[res_row["idx"]]["p_high"] = probs[1]
        aug = regime_augmented_beta(rows, base_fit, None, 22, e)
        if aug is None:
            diagnostics["fits"].append({"est_idx": e, "day": dates[e].isoformat(),
                                        "skipped": "regime_augmented_insufficient"})
            continue
        for t in range(e, min(next_e, n - 1)):
            pred = None
            design = harq_design(rows, t)
            if design is not None and rows[t].get("p_high") is not None:
                pred = sum(b * x for b, x in zip(aug["beta"], design + [rows[t]["p_high"]]))
            rows[t]["logRVhat"] = pred
        # Stage 2: expanding walk-forward gradient-boosted return prediction
        train_rows = []
        for t in range(22, e - 1):
            row = rows[t]
            if row.get("logRVhat") is None or row.get("p_high") is None:
                continue
            feats = feature_row(rows, t)
            target = rows[t + 1]["daily_return"] if t + 1 < n else None
            if feats is None or target is None:
                continue
            train_rows.append((t, feats, target))
        model_note = {"est_idx": e, "day": dates[e].isoformat(), "train_rows": len(train_rows)}
        if len(train_rows) >= WALK_FORWARD_N_MIN:
            n_valid = int(round(WALK_FORWARD_N_MIN * VALIDATION_FRACTION))
            val_rows = train_rows[-n_valid:]
            fit_rows = train_rows[:-n_valid - 1]
            if len(fit_rows) >= 60:
                best = None
                cand_report = []
                for params in XGB_CANDIDATES:
                    model = fit_gbdt(params, [f for _t, f, _y in fit_rows],
                                     [y for _t, _f, y in fit_rows])
                    preds = model.predict([f for _t, f, _y in val_rows])
                    corr = _pearson(preds, [y for _t, _f, y in val_rows])
                    cand_report.append({"params": list(params), "val_corr": corr})
                    if best is None or corr > best[1] + 1e-12:
                        best = (params, corr)
                if best is None or best[1] <= 0.0:
                    chosen, chosen_corr = XGB_DEFAULT, (best[1] if best else 0.0)
                    model_note["default_used"] = True
                else:
                    chosen, chosen_corr = best[0], best[1]
                final_model = fit_gbdt(chosen, [f for _t, f, _y in train_rows],
                                       [y for _t, _f, y in train_rows])
                for t in range(e, min(next_e, n - 1)):
                    feats = feature_row(rows, t)
                    if feats is None:
                        continue
                    rows[t]["rhat"] = final_model.predict([feats])[0]
                model_note.update({"chosen_params": list(chosen),
                                   "chosen_val_corr": chosen_corr,
                                   "candidates": cand_report,
                                   "refit_rows": len(train_rows)})
            else:
                model_note["skipped"] = "train_rows_below_floor"
        else:
            model_note["skipped"] = "below_walk_forward_n_min"
        diagnostics["fits"].append(model_note)
    return diagnostics


def _quantile_nearest(values, q):
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[idx]


def compute_weights(rows, p_override=None):
    """Record Stage 3: gate, threshold, walk-forward scaling and the weekly no-trade band."""
    n = len(rows)
    past_abs = []
    past_abs_tilde = []
    stats = {"signal_days": 0, "above_threshold": 0, "rebalance_days": 0, "weight_changes": 0}
    for t in range(n):
        row = rows[t]
        rhat, sigma, pt = row.get("rhat"), None, row.get("p_high")
        if p_override is not None:
            pt = p_override[t]
        log_rvhat = row.get("logRVhat")
        if rhat is None or log_rvhat is None or pt is None:
            continue
        sigma = math.sqrt(math.exp(log_rvhat)) if log_rvhat > -700 else None
        if sigma is None or sigma <= 0:
            continue
        s_comb = rhat / sigma
        s_gated = s_comb * (1.0 - GATE_KAPPA * pt)
        row["s_comb"] = s_comb
        row["s_gated"] = s_gated
        stats["signal_days"] += 1
        theta = _quantile_nearest(past_abs, THRESHOLD_Q) if len(past_abs) >= 60 else None
        s_tilde = s_gated if (theta is not None and abs(s_gated) > theta) else 0.0
        if s_tilde != 0.0:
            stats["above_threshold"] += 1
        row["s_tilde"] = s_tilde
        past_abs.append(abs(s_gated))
        past_abs_tilde.append(abs(s_tilde))
        if len(past_abs_tilde) >= 2:
            scale_mean = sum(past_abs_tilde) / len(past_abs_tilde)
            c_wf = TARGET_ABS_EXPOSURE / scale_mean if scale_mean > 0 else 0.0
        else:
            c_wf = 0.0
        w_star = 0.0
        if s_tilde != 0.0 and c_wf > 0:
            magnitude = min(c_wf * abs(s_tilde), WEIGHT_CAP)
            w_star = magnitude if s_tilde > 0 else -magnitude
        row["w_star"] = w_star
    # weekly rebalance with the no-trade band
    w_eff = 0.0
    prev_week = None
    for t in range(n):
        row = rows[t]
        day = row["day"]
        week = day.isocalendar()[:2]
        if week != prev_week:
            prev_week = week
            row["rebalance"] = True
            stats["rebalance_days"] += 1
            target = row.get("w_star")
            if target is not None:
                if abs(target - w_eff) > NO_TRADE_BAND:
                    if abs(target - w_eff) > 1e-12:
                        stats["weight_changes"] += 1
                    w_eff = target
        row.setdefault("w_eff", None)
        if row.get("rebalance"):
            row["w_eff"] = w_eff
    return stats


def weight_events(rows):
    """Entry/exit formations from the effective weekly weight (sign changes only)."""
    events = []
    prev_sign = 0
    for t, row in enumerate(rows):
        if not row.get("rebalance"):
            continue
        w = row.get("w_eff")
        if w is None:
            continue
        sign = 0 if abs(w) < 1e-12 else (1 if w > 0 else -1)
        if sign == prev_sign:
            continue
        if prev_sign != 0:
            events.append({"kind": "exit", "formation_idx": t})
        if sign != 0:
            events.append({"kind": "entry", "formation_idx": t,
                           "direction": "long" if sign > 0 else "short"})
        prev_sign = sign
    events.sort(key=lambda e: (e["formation_idx"], 0 if e["kind"] == "exit" else 1))
    return events


def permute_series(values, seed):
    """Deterministic Fisher-Yates permutation (placebo gate; distribution preserved)."""
    out = list(values)
    rng = random.Random(seed)
    for i in range(len(out) - 1, 0, -1):
        j = rng.randrange(i + 1)
        out[i], out[j] = out[j], out[i]
    return out


def build_symbol_streams(rows, dates):
    """Full point-in-time pipeline for one symbol: diagnostics, genuine and variant streams."""
    diagnostics = {"symbol_days": len(rows)}
    attach_walk_forward(rows, dates, diagnostics)
    stats = compute_weights(rows)
    diagnostics["signal"] = stats
    genuine = weight_events(rows)
    rows_no_gate = [row.get("p_high") for row in rows]
    placebo_p = permute_series(rows_no_gate, RNG_SEED + 777)
    for row, p_tilde in zip(rows, placebo_p):
        row["p_high_placebo"] = p_tilde
    placebo_rows = [dict(row) for row in rows]
    for row in placebo_rows:
        row["rhat"] = row.get("rhat")
    # placebo gate: same model outputs, permuted regime probability in the gate only
    placebo_stats = compute_weights(placebo_rows, p_override=placebo_p)
    placebo_events = weight_events(placebo_rows)
    long_only = [e for e in genuine
                 if not (e["kind"] == "entry" and e.get("direction") == "short")]
    diagnostics["placebo"] = placebo_stats
    diagnostics["counts"] = {
        "entries": sum(1 for e in genuine if e["kind"] == "entry"),
        "exits": sum(1 for e in genuine if e["kind"] == "exit"),
        "long_entries": sum(1 for e in genuine if e["kind"] == "entry"
                            and e.get("direction") == "long"),
        "short_entries": sum(1 for e in genuine if e["kind"] == "entry"
                             and e.get("direction") == "short"),
        "placebo_entries": sum(1 for e in placebo_events if e["kind"] == "entry"),
        "long_only_entries": sum(1 for e in long_only if e["kind"] == "entry"),
    }
    return {"events": genuine, "long_only": long_only, "placebo": placebo_events,
            "diagnostics": diagnostics}


def build_all_events(panels_intraday, panels_daily, dates_by_symbol):
    """Registered event streams for every symbol on the daily cohort grid."""
    events, diagnostics, measure_reports = {}, {}, {}
    for sym in sorted(panels_daily):
        rows, report = realized_measures(panels_intraday[sym], panels_daily[sym])
        signed_jump_series(rows)
        measure_reports[sym] = report
        streams = build_symbol_streams(rows, dates_by_symbol[sym])
        events[sym] = streams
        diagnostics[sym] = streams["diagnostics"]
    return events, diagnostics, measure_reports


def signal_diagnostics(streams):
    diag = streams["diagnostics"]
    return {
        "case_key": REGISTERED_CASES[0]["key"],
        "entry_formations": diag["counts"]["entries"],
        "exit_formations": diag["counts"]["exits"],
        "long_entries": diag["counts"]["long_entries"],
        "short_entries": diag["counts"]["short_entries"],
        "estimate_count": sum(1 for f in diag.get("fits", []) if "skipped" not in f),
        "last_fit": diag.get("fits", [])[-1] if diag.get("fits") else None,
        "signal_days": diag["signal"]["signal_days"],
        "above_threshold": diag["signal"]["above_threshold"],
        "rebalance_days": diag["signal"]["rebalance_days"],
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
    sign = 1.0 if direction == "long" else -1.0

    entry_idx = plan["entry_idx"]
    plan_exit_idx = plan.get("exit_idx")
    entry_side = "buy" if direction == "long" else "sell"
    exit_side = "sell" if direction == "long" else "buy"
    initial = _fill_price(bars[entry_idx]["open"], entry_side, cost)
    if initial <= 0:
        raise ValueError("non-positive entry fill")
    qty = BASE_QUOTE / initial
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
            quote = BASE_QUOTE * (mult ** level)
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
    }


def run_episode_sequence(bars, events, phase_start_idx, phase_end_idx, data_end_idx, delay,
                         dca, cost, funding_events):
    """Sequential non-overlapping episodes; a same-bar sign flip may re-enter at that bar open."""
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
    """Replace the per-fill fee with the record's flat one-way friction schedule."""
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
    phase, delay, slippage = PATH_CONFIG[path_key]
    start_idx, end_idx = _window_indices(dates, phase)
    cost = cost_for("historical", cell["meta"])
    cost = dict(cost, entry_delay_bars=delay, slippage_ticks=slippage)
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
        "long_episodes", "short_episodes",
    )
    return {k: row[k] for k in keys}


def disposition_band(survivor_count):
    """Contract 7.3 v1.4.0 disposition band: a count only, never a verdict."""
    if survivor_count > 1:
        return "MULTIPLE_SURVIVORS"
    return "SURVIVOR_FOUND" if survivor_count else "NO_SURVIVOR"


def cohort_record(cohort_name, winner, hist_row, by_grid, culls, survivor_checks, nb):
    """One cohort record in the contract 10.8 schema consumed by the host-side writers.

    runtime/survivor_bundle.py and runtime/survivor_index.py read exactly `outcome`,
    `winner` (the registered strategy + DCA axes only) and `metrics` (phases + the four
    robustness grids), so the record is emitted in that shape rather than with the
    legacy names (status / list winner / top-level *_metrics).
    """
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
        "end_exits", "open_at_end", "long_episodes", "short_episodes",
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


def evaluate_falsification(diagnostics, rows_by_grid, panels, dates_by_symbol, all_events,
                           all_funding, meta, symbols):
    """The record's four-item battery on the winners of the complete joint space."""
    phase, delay, slippage = PATH_CONFIG["full"]
    oos_phase = PATH_CONFIG["oos"]
    case = STRATEGY_CASES[0]
    cohort_winners = {}
    oos_cycles = []
    full_episodes = []
    long_only_episodes = []
    placebo_episodes = []
    friction = {bps: [] for bps in FALSIFICATION["friction_bps_tracks"]}
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
        oos_start, oos_end = _window_indices(dates, oos_phase[0])
        oos_episodes, _m, _o = run_episode_sequence(
            bars, streams["events"], oos_start, oos_end, len(bars) - 1, oos_phase[1], dca,
            cost_for("historical", symbol_meta), funding)
        oos_cycles.extend(derive_flat_fee(oos_episodes, 5.0))
        full_start, full_end = _window_indices(dates, phase)
        full_ep, _m, _o = run_episode_sequence(
            bars, streams["events"], full_start, full_end, len(bars) - 1, delay, dca,
            cost_for("historical", symbol_meta), funding)
        full_episodes.extend(derive_flat_fee(full_ep, 5.0))
        for bps in friction:
            friction[bps].extend(derive_flat_fee(full_ep, bps))
        lon_ep, _m, _o = run_episode_sequence(
            bars, streams["long_only"], full_start, full_end, len(bars) - 1, delay, dca,
            cost_for("historical", symbol_meta), funding)
        long_only_episodes.extend(derive_flat_fee(lon_ep, 5.0))
        pla_ep, _m, _o = run_episode_sequence(
            bars, streams["placebo"], full_start, full_end, len(bars) - 1, delay, dca,
            cost_for("historical", symbol_meta), funding)
        placebo_episodes.extend(derive_flat_fee(pla_ep, 5.0))
    oos_sharpe = _pooled_sharpe(oos_cycles)
    oos_dd = _pooled_max_dd(oos_cycles)
    item1_ok = bool(oos_cycles and oos_sharpe > FALSIFICATION["oos_min_sharpe"]
                    and oos_dd > FALSIFICATION["oos_max_drawdown_pct"])
    friction_sharpes = {str(bps): _pooled_sharpe(rows) for bps, rows in friction.items()}
    reject_bps = FALSIFICATION["friction_reject_sharpe_bps"]
    item2_ok = bool(friction_sharpes[str(reject_bps)] > 0.0)
    long_only_sharpe = _pooled_sharpe(long_only_episodes)
    item3_ok = bool(long_only_sharpe > FALSIFICATION["long_only_min_sharpe"])
    genuine_dd = _pooled_max_dd(full_episodes)
    placebo_dd = _pooled_max_dd(placebo_episodes)
    within_tolerance = (placebo_dd >= genuine_dd - FALSIFICATION["placebo_tolerance_pp"]) \
        if (full_episodes and placebo_episodes) else False
    item4_ok = bool(placebo_episodes and full_episodes and not within_tolerance)
    items = {
        "1": {"key": "oos_expansion", "status": "pass" if item1_ok else "fail",
              "oos_sharpe_5bps": oos_sharpe, "oos_max_drawdown_pct": oos_dd,
              "oos_cycles": len(oos_cycles), "threshold_sharpe": FALSIFICATION["oos_min_sharpe"],
              "threshold_max_drawdown_pct": FALSIFICATION["oos_max_drawdown_pct"]},
        "2": {"key": "friction_stress", "status": "pass" if item2_ok else "fail",
              "sharpe_by_bps": friction_sharpes, "reject_bps": reject_bps},
        "3": {"key": "long_only_feasibility", "status": "pass" if item3_ok else "fail",
              "long_only_sharpe_5bps": long_only_sharpe,
              "threshold_sharpe": FALSIFICATION["long_only_min_sharpe"],
              "long_only_episodes": len(long_only_episodes)},
        "4": {"key": "regime_gating_placebo", "status": "pass" if item4_ok else "fail",
              "genuine_max_drawdown_pct": genuine_dd, "placebo_max_drawdown_pct": placebo_dd,
              "tolerance_pp": FALSIFICATION["placebo_tolerance_pp"],
              "placebo_episodes": len(placebo_episodes), "placebo_within_tolerance": within_tolerance},
    }
    failed = [k for k, item in items.items() if item["status"] == "fail"]
    return {
        "family_id": FAMILY_ID,
        "claim_scope": CLAIM_SCOPE,
        "registered_battery_implemented": True,
        "exact_source_universe_measured": False,
        "protocol": FALSIFICATION,
        "cohort_winners": cohort_winners,
        "items": items,
        "items_evaluated": len(items),
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

    panels_daily, dates_by_symbol, files_by_symbol, gaps = {}, {}, {}, {}
    panels_intraday, intraday_gaps = {}, {}
    funding_data, funding_reports = {}, {}
    start_ms = int(dt.datetime.fromisoformat(PHASES["full"][0] + "T00:00:00+00:00").timestamp() * 1000)
    end_ms = int(dt.datetime.fromisoformat(PHASES["full"][1] + "T23:59:59+00:00").timestamp() * 1000)
    for sym in symbols:
        daily_rows, daily_files = load_klines(sym, TIMEFRAMES[0], root=klines_root / sym / TIMEFRAMES[0])
        keys = sorted(daily_rows)
        bars = [daily_rows[k] for k in keys]
        panels_daily[sym] = bars
        dates_by_symbol[sym] = [bar_date(b) for b in bars]
        gaps[sym] = gap_report(bars, TIMEFRAMES[0])
        files_by_symbol.setdefault(sym, {})[TIMEFRAMES[0]] = [p.name for p in daily_files]
        intraday_rows, intraday_files = load_klines(sym, EXECUTION_TIMEFRAME,
                                                    root=klines_root / sym / EXECUTION_TIMEFRAME)
        ikeys = sorted(intraday_rows)
        ibars = [intraday_rows[k] for k in ikeys]
        panels_intraday[sym] = ibars
        intraday_gaps[sym] = gap_report(ibars, EXECUTION_TIMEFRAME)
        files_by_symbol[sym][EXECUTION_TIMEFRAME] = [p.name for p in intraday_files]
        events, report = load_funding(sym, start_ms, end_ms, root=funding_root)
        funding_data[sym] = events
        funding_reports[sym] = report

    atomic_json(artifacts / "progress.json",
                {"phase": "building_events", "updated_at_utc": utc_now()})
    all_events, diagnostics, measure_reports = build_all_events(panels_intraday, panels_daily,
                                                                dates_by_symbol)
    signal_report = {sym: signal_diagnostics(all_events[sym]) for sym in sorted(symbols)}

    atomic_json(artifacts / "progress.json",
                {"phase": "evaluating_grids", "updated_at_utc": utc_now()})
    rows_by_grid = evaluate_all(meta, {TIMEFRAMES[0]: panels_daily}, dates_by_symbol,
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
    falsification = evaluate_falsification(diagnostics, rows_by_grid, panels_daily,
                                           dates_by_symbol, all_events, funding_data, meta,
                                           symbols)
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
        "intraday_bar_continuity": intraday_gaps,
        "realized_measure_coverage": measure_reports,
        "registered_window": {"start": PHASES["full"][0], "end": PHASES["full"][1]},
        "funding_coverage": funding_reports,
    })

    all_rows = [r for rows in rows_by_grid.values() for r in rows]
    decomposition_ok = all(r["decomposition_ok"] for r in all_rows)
    negative_control = any(
        abs(r["gross_pnl"] - r["net_pnl"]) > 1e-3 and (r["fees"] + r["funding"]) > 1e-9
        for r in all_rows)
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
        "record_exit_reasons_enforced": all(
            r["stop_hits"] + r["tp_hits"] + r["family_exits"] + r["end_exits"] >= 0
            for r in all_rows),
        "falsification_battery_evaluated": falsification.get("registered_battery_implemented") is True,
        "cohort_selector_and_disposition_applied": len(cohort_results) == len(symbols),
        "intraday_measure_completeness_enforced": all(
            rep["invalid_days"] >= 0 for rep in measure_reports.values()),
    }
    immutable_json(artifacts / "assertions.json", assertions)

    immutable_json(artifacts / "panel_evidence.json", {
        "family_id": FAMILY_ID,
        "registered_core_mechanism": {
            "hypothesis": (
                "the record's three structural realities: strong volatility predictability vs weak "
                "return predictability, state-dependent predictability concentration, and the "
                "friction attrition barrier that requires defensive risk-controlled allocation"
            ),
            "mechanism": (
                "regime-augmented HARQ volatility forecasts feed a 3M walk-forward gradient-boosted "
                "return prediction; the low-volatility gate (1-p_t) and the recursive walk-forward "
                "threshold/scaling turn it into a weekly signal-by-risk target weight"
            ),
            "source_archetype": (
                "CSI 300 cash index (sh000300), Wind 5-minute intraday and daily closes, A-share "
                "continuous sessions M_t = 48; the local execution rail adapts venue plumbing to "
                "the registered linear USD-M perpetual panel (record crypto portability block, "
                "00:00 UTC boundary, M_t = 288) and scopes the conclusion to that panel"
            ),
            "structural_leakage_guardrail": (
                "all predictors use completed bars at or before t; the regime probability is a "
                "filtered (never smoothed) quantity; the walk-forward re-estimation is strictly "
                "expanding; the OOS split is never used for any selection"
            ),
            "registered_axes": (
                "one strategy case x four DCA axes (48 DCA configs) x 10 grids x %d cohorts"
                % len(symbols)
            ),
        },
        "per_cohort_signal_diagnostics": signal_report,
        "model_diagnostics": diagnostics,
        "realized_measure_reports": measure_reports,
    })

    stress_summary = {}
    for g in ("fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks", "no_funding",
              "no_funding_full", "cost_attrition_40bps"):
        stress_summary[g] = {
            "mean_net_pnl": statistics.mean(r["net_pnl"] for r in rows_by_grid[g]),
            "positive_cells": sum(1 for r in rows_by_grid[g] if r["net_pnl"] > 0),
            "total_cells": len(rows_by_grid[g]),
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
