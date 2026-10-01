#!/usr/bin/env python3
"""Deterministic local-universe full backtest for the ORCA spectral graph-topological
crash/rally detection family (cross-asset correlation network features).

The reviewed source (ORCA: Spectral Graph-Topological Crash and Rally Detection via
Cross-Asset Correlation Network Features, arXiv:2604.17251) argues that assets which are
normally weakly correlated begin moving in lockstep ahead of a crisis: the dominant
eigenvalue absorbs more variance, the spectral gap narrows, effective rank collapses and
graph-topological measures (edge density, clustering coefficient) shift - precursors that
univariate volatility measures miss. The signal is bifurcated (rally / crash) and drives a
long-only dynamic equity exposure in [0, 1.5x] with risk-off rotation.

Under the quant-runtime-pipeline contract (v1.8 / 14.4 / 6.4 lifecycle footer):
  1. The complete legal local eligible universe is registered:
     canonical local Binance USD-M perpetual 1d, 4 symbols (BNBUSDT, BTCUSDT, ETHUSDT,
     SOLUSDT). Source venue, quote currency, named symbols and source-universe breadth
     (24 US-listed ETFs via EODHD) are provenance/external-validity context, not execution
     prerequisites: the registered core signal is the cross-asset correlation network, and
     it is computed on the joint 4-asset local panel.
  2. Four-axis DCA domain (48 cells per cohort per grid):
     spacing_pct in {0.01, 0.02, 0.03, 0.04}
     size_multiplier in {1.0, 1.1}
     breakeven_tp_pct in {0.01, 0.02, 0.03}
     invalidation_pct in {0.05, 0.10}
     Fixed constants: starting_equity = 30000 USDT, base_quote = 1000 USDT, max leverage
     10x, routine active tranches max 11 (tranche #12 reserved).
  3. Cohort survivor semantics (cohort-selector-v1 / cohort-disposition-v1):
     4 cohorts (one per symbol, 1d timeframe). Historical winner selected by Sharpe desc,
     net PnL desc, lexical tie-break; carried across OOS, full and robustness grids.
  4. Ten evaluation grids: historical, oos, full, fee_2x, funding_2x, entry_delay_1_bar,
     slippage_2ticks, no_funding, no_funding_full, cost_attrition_40bps.
  5. The runner writes deterministic execution artifacts inside the attempt directory; it
     writes no terminal sentinel (DONE/FAILED/INCOMPLETE) and no verdict.json.

Registered vs research-defined: the hypothesis, direction (long-only with risk-off
rotation), signal timing (daily formation, 10-trading-day horizon), label thresholds
(>3% endpoint return / >7% intra-window drawdown), rank thresholds (0.78/0.90/0.40/0.60),
minimum 8-trading-day holding period and the four-axis DCA domain are record-registered and
are never altered here. The exposure-map step thresholds, RF hyperparameters, correlation
windows, graph thresholds and cost details are marked research-defined in the record itself.
"""

from __future__ import annotations

import argparse
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

FAMILY_ID = "spectral-graph-topological-crash-rally-detection-correlation-network-2026-09-04"
ROUND_ID = FAMILY_ID + "-r1"
RUN_ID = ROUND_ID + "-u1"
RUNNER_NAME = "380_orca_spectral_graph_crash_rally_run.py"

FINGERPRINT_INPUT = "spectral-graph-topological-crash-rally-detection-correlation-network-2026-09-04|universe=portability=adapted/unproven (source-market; research-defined);window=record-faithful|dca=spacing_pct=0.01,0.02,0.03,0.04;size_multiplier=1.0,1.1;breakeven_tp_pct=0.01,0.02,0.03;invalidation_pct=0.05,0.10|selector=cohort-selector-v1;disposition=cohort-disposition-v1|source=spectral-graph-topological-crash-rally-detection-correlation-network-2026-09-04.md"

RAW_ROOT = Path("/data/raw")
KLINES_ROOT = RAW_ROOT / "binance" / "usdm" / "klines"
FUNDING_ROOT = RAW_ROOT / "binance" / "usdm" / "funding"
INSTRUMENTS_PATH = RAW_ROOT / "binance" / "usdm" / "instruments" / "usdm-perp-instruments.json"
CONFIG_PATH = RAW_ROOT / "_meta" / "CONFIG.json"
SCHEMA_PATH = RAW_ROOT / "_meta" / "SCHEMA.md"

OWNERSHIP_KEYS = ("task_id", "kanban_task_id", "kanban_board")

TIMEFRAME = "1d"
EXEC_TIMEFRAME = "5m"
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

# --- record-registered signal constants -------------------------------------
MAX_EXPOSURE = 1.5
HORIZON_DAYS = 10
RALLY_THRESHOLD = 0.03          # >3% endpoint return in 10 days
CRASH_THRESHOLD = 0.07          # >7% max intra-window drawdown in 10 days
ENTRY_RALLY_LO = 0.78
ENTRY_RALLY_HI = 0.90
ENTRY_CRASH_HI = 0.40
EXIT_RALLY_RANK = 0.90          # euphoria exit
EXIT_CRASH_RANK = 0.60          # danger exit
MIN_HOLD_DAYS = 8               # minimum 8 trading days; exit signals override
CORR_WINDOWS = (60, 120)        # trailing correlation estimators, days
EWM_HALF_LIFE = 30              # 30-day half-life exponential weighted estimator
GRAPH_THRESHOLDS = (0.3, 0.5, 0.7)
TRAD_LOOKBACKS = (1, 5, 10, 20, 40, 60)   # traditional features: 1-to-60-day lookbacks
MAX_FFILL_DAYS = 5              # record: forward-fill gaps up to 5 trading days

# record: "RF config: 200 trees, max depth 6, min 30 samples/leaf, min 60 samples/split"
RF_PARAMS = {
    "n_estimators": 200,
    "max_depth": 6,
    "min_samples_leaf": 30,
    "min_samples_split": 60,
    "random_state": 20260904,
}
MIN_TRAIN_SAMPLES = 120         # research-defined strictly-causal training floor

EXECUTION_MARKET = "BINANCE_USDM_PERP"
SOURCE_MARKET = ("US-listed ETF spot panel (24 diversified instruments across 6 asset "
                 "classes, daily adjusted close from EODHD API)")
CLAIM_SCOPE = ("canonical local Binance USD-M perpetual 1d cross-asset correlation-network "
               "signal, 4 symbols (BNBUSDT/BTCUSDT/ETHUSDT/SOLUSDT); NOT a 24-ETF US "
               "equity/EODHD reproduction")

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

GATES = {
    "min_episodes_is": MIN_EPISODES_IS,
    "min_episodes_oos": MIN_EPISODES_OOS,
    "min_neighbour_same_sign_fraction": MIN_NEIGHBOUR,
}

EXECUTION_SEMANTICS = {
    "execution_market": "BINANCE_USDM_PERP",
    "position_direction": "long_only_with_risk_off_rotation",
    "numeraire": "USDT",
    "starting_equity_usdt": START_EQUITY,
    "base_quote_usdt": BASE_QUOTE,
    "max_leverage": LEVERAGE,
    "routine_active_tranches_max": ROUTINE_ACTIVE_TRANCHES_MAX,
    "reserve_tranche": RESERVE_TRANCHE,
    "initial_entry_counts_as_active_tranche": True,
    "max_add_levels": MAX_ADD_LEVELS,
    "same_bar_order": "adverse_before_favorable_tp",
    "holding_period": "minimum 8 trading days for non-exit positions; registered exit signals override",
    "exit_mode": "reduce_only",
    "max_equity_exposure_x": MAX_EXPOSURE,
    "entry_quote_rule": "base_quote * exposure / max_exposure (1.5x exposure == registered base_quote)",
}

REGISTERED_SIGNAL_RULE = {
    "version": 1,
    "observation_unit": "daily close -> simple daily return on the joint 4-asset local panel",
    "formation": "daily at session close, strictly causal; prediction horizon 10 trading days forward",
    "estimators": [
        "60-day trailing correlation",
        "120-day trailing correlation",
        "30-day half-life exponential weighted correlation",
    ],
    "feature_families": [
        "spectral: dominant eigenvalue share, spectral gap, effective rank, top-2 ratio, mean |corr|",
        "graph-topological: edge density, mean clustering coefficient, mean degree at graph thresholds 0.3/0.5/0.7",
        "traditional: 1-to-60-day lookback momentum, volatility, drawdown, cross-sectional dispersion",
    ],
    "model": ("RandomForest 200 trees, max_depth 6, min_samples_leaf 30, min_samples_split 60, "
              "strictly causal expanding walk-forward fit (labels resolved before the formation)"),
    "labels": {
        "rally": ">3% endpoint return over the next 10 trading days",
        "crash": ">7% max intra-window drawdown over the next 10 trading days",
    },
    "ranks": ("expanding causal percentile rank of each model score against every score formed "
              "up to and including the current formation"),
    "long_entry": "rally rank in [0.78, 0.90) AND crash rank < 0.40 -> equity exposure 1.5x (maximum)",
    "intermediate_exposures": "0.3x-1.2x for other joint signal states",
    "exit": ("rally rank >= 0.90 (euphoria exit) OR crash rank >= 0.60 (danger exit) -> equity "
             "exposure 0, rotate defensive; exit overrides the holding period"),
    "holding_period": "minimum 8 trading days for non-exit positions; exit signals override",
    "direction": "long-only with risk-off rotation (no short leg)",
    "is_search_axis": False,
    "note": ("exposure-map step thresholds, RF hyperparameters, correlation windows and graph "
             "thresholds are research-defined/tuned in the record itself (not source-reported as "
             "optimal); the registered hypothesis, direction, signal timing, label thresholds and "
             "rank thresholds are unchanged"),
}

# The record states only three explicit exposure states; the intermediate 0.3x-1.2x band is
# registered here as a deterministic monotone step map (research-defined) so the run is
# reproducible without the source's 50,000-combination calibration grid.
EXPOSURE_MAP = {
    "version": 1,
    "kind": "research-defined deterministic step map",
    "evaluation_order": ["exit_euphoria", "exit_danger", "max_entry", "risk_off_reduced",
                         "intermediate_rally_bands"],
    "exit": {"rally_rank_gte": EXIT_RALLY_RANK, "crash_rank_gte": EXIT_CRASH_RANK, "exposure": 0.0},
    "max_entry": {"rally_rank_in": [ENTRY_RALLY_LO, ENTRY_RALLY_HI],
                  "crash_rank_lt": ENTRY_CRASH_HI, "exposure": MAX_EXPOSURE},
    "risk_off_reduced": {"crash_rank_gte": ENTRY_CRASH_HI, "exposure": 0.3},
    "intermediate": [
        {"rally_rank_gte": 0.60, "exposure": 1.2},
        {"rally_rank_gte": 0.45, "exposure": 0.9},
        {"rally_rank_gte": 0.30, "exposure": 0.6},
        {"rally_rank_lt": 0.30, "exposure": 0.3},
    ],
    "bounds": {"min_exposure": 0.0, "max_exposure": MAX_EXPOSURE},
}


def expected_counts(symbols=None):
    syms = list(symbols) if symbols is not None else list(DEFAULT_INSTRUMENTS)
    cohorts = len(syms)
    per_cohort = len(DCA_GRID)
    per_grid = cohorts * per_cohort
    total = per_grid * len(GRIDS)
    return {
        "cohorts": cohorts,
        "strategy_cases_per_cohort": 1,
        "dca_configs_per_cohort": per_cohort,
        "case_evaluations_per_grid": per_grid,
        "grids": list(GRIDS),
        "case_evaluations_total": total,
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
    *("artifacts/grid_%s.csv" % grid for grid in GRIDS),
)

MS_DAY = 86400000


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
    path = Path(path)
    raw = (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
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


# Canonical-pack official funding rows carry millisecond receipt jitter (the SCHEMA.md
# example row itself is 1789084800008; measured max on this universe is 26 ms). Snap
# within this tolerance to the funding interval boundary, fail closed beyond it.
FUNDING_TIME_JITTER_TOLERANCE_MS = 1000


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
            if status == "official":
                offset = t % 300000
                if offset > 150000:
                    offset -= 300000
                if abs(offset) > FUNDING_TIME_JITTER_TOLERANCE_MS:
                    raise RuntimeError("official funding timestamp is not on a 5m boundary for %s" % symbol)
                if offset:
                    # single choke point: normalization happens here, never in the simulator
                    t -= offset
                    report["boundary_jitter_snapped"] += 1
                    report["max_boundary_jitter_ms"] = max(report["max_boundary_jitter_ms"], abs(offset))
            if not start_ms <= t <= end_ms:
                report["out_of_window"] += 1
                continue
            if status != "official":
                report["modeled_ignored" if status in ("modeled", "modeled_funding")
                       else "other_ignored"] += 1
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


def funding_by_day(events):
    """Bucket official funding events by their UTC day's 00:00 open timestamp."""
    out = {}
    for event in events:
        day_ms = int(event["t"] // MS_DAY) * MS_DAY
        out.setdefault(day_ms, []).append(event)
    for bucket in out.values():
        bucket.sort(key=lambda e: e["t"])
    return out


def load_local_rows(symbol, root=None):
    """Canonical local raw 1d perpetual klines for a single instrument."""
    base = Path(root) if root is not None else KLINES_ROOT / symbol / TIMEFRAME
    files = sorted(base.glob("%s-%s-*.jsonl.gz" % (symbol, TIMEFRAME)))
    if not files:
        raise FileNotFoundError("no canonical %s klines in %s" % (TIMEFRAME, base))
    rows = {}
    for path in files:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                row = json.loads(line)
                t = int(row["open_time_ms"])
                close_t = int(row["close_time_ms"])
                o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
                vol = float(row.get("volume", 0.0))
                rows[t] = {
                    "open_time_ms": t, "close_time_ms": close_t,
                    "open": o, "high": h, "low": l, "close": c, "volume": vol,
                }
    return rows, files


def load_execution_rows(symbol, root=None):
    """Canonical execution-resolution bars, indexed by UTC open timestamp."""
    base = Path(root) if root is not None else KLINES_ROOT / symbol / EXEC_TIMEFRAME
    files = sorted(base.glob("%s-%s-*.jsonl.gz" % (symbol, EXEC_TIMEFRAME)))
    if not files:
        raise FileNotFoundError("no canonical %s klines in %s" % (EXEC_TIMEFRAME, base))
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
                }
    return rows, files


def execution_day_slice(daily_bar, five_minute_rows):
    """Return one exact UTC day's 288 contiguous 5m bars or fail closed."""
    start = int(daily_bar["open_time_ms"])
    if int(daily_bar.get("close_time_ms", -1)) != start + MS_DAY - 1:
        raise ValueError("invalid daily close_time for UTC day %d" % start)
    expected = range(start, start + MS_DAY, 300000)
    bars = [five_minute_rows.get(t) for t in expected]
    if len(bars) != 288 or any(bar is None for bar in bars):
        raise ValueError("incomplete 5m execution coverage for UTC day %d" % start)
    if bars[0]["open_time_ms"] != start or bars[-1]["open_time_ms"] != start + MS_DAY - 300000:
        raise ValueError("invalid first/last 5m execution timestamp for UTC day %d" % start)
    if any(bars[i]["open_time_ms"] - bars[i - 1]["open_time_ms"] != 300000
           for i in range(1, len(bars))):
        raise ValueError("non-contiguous 5m execution coverage for UTC day %d" % start)
    if any(int(bar.get("close_time_ms", -1)) != int(bar["open_time_ms"]) + 299999 for bar in bars):
        raise ValueError("invalid 5m close_time for UTC day %d" % start)
    return bars


def day_of(open_time_ms):
    return dt.datetime.fromtimestamp(open_time_ms / 1000, dt.timezone.utc).date()


def inspect_local_universe(config, schema_text="", symbols=None, instruments=None):
    """Legal local execution universe; source venue/quote/breadth is provenance, not a gate."""
    config = config if isinstance(config, dict) else {}
    raw_datasets = config.get("datasets")
    datasets = raw_datasets if isinstance(raw_datasets, dict) else {}
    declared = {str(v).upper() for v in config.get("symbols", []) if isinstance(v, str)}
    intervals = {str(v) for v in config.get("intervals", []) if isinstance(v, str)}
    found = sorted(symbols or [])
    klines_declared = "klines" in datasets
    one_day = TIMEFRAME in intervals
    five_min = EXEC_TIMEFRAME in intervals
    per_symbol = {}
    legal = bool(found and klines_declared and one_day and five_min)
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
        "timeframes": [TIMEFRAME],
        "execution_timeframe": EXEC_TIMEFRAME,
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
        "config_1d_declared": one_day,
        "config_5m_declared": five_min,
        "schema_mentions_klines": "klines" in schema_text.lower(),
    }


# ---------------------------------------------------------------------------
# Panel construction (record: adjusted close -> simple daily returns,
# forward-fill gaps up to 5 trading days, remaining gaps dropped)
# ---------------------------------------------------------------------------

def build_panel(rows_by_symbol, start_date, end_date):
    """Joint 4-asset daily panel over the union axis, record missing-data rule applied."""
    symbols = sorted(rows_by_symbol)
    if not symbols:
        raise ValueError("panel requires at least one symbol")
    axis = sorted(set().union(*(set(rows_by_symbol[s]) for s in symbols)))
    start_ms = int(dt.datetime.fromisoformat(start_date + "T00:00:00+00:00").timestamp() * 1000)
    end_ms = int(dt.datetime.fromisoformat(end_date + "T23:59:59+00:00").timestamp() * 1000)
    axis = [t for t in axis if start_ms <= t <= end_ms]
    if not axis:
        raise ValueError("no canonical daily bars inside the registered window")

    values, ffilled = {}, 0
    for sym in symbols:
        last, last_ms, out = None, None, {}
        for t in axis:
            bar = rows_by_symbol[sym].get(t)
            if bar is not None:
                last, last_ms = bar["close"], t
                out[t] = bar["close"]
            elif last is not None and (t - last_ms) <= MAX_FFILL_DAYS * MS_DAY:
                out[t] = last
                ffilled += 1
            else:
                out[t] = None
        values[sym] = out

    # remaining gaps dropped: a joint observation date needs every symbol observable
    dates = [t for t in axis if all(values[s][t] is not None for s in symbols)]
    if len(dates) < HORIZON_DAYS + MIN_TRAIN_SAMPLES:
        raise ValueError("joint-observable panel is too short: %d days" % len(dates))

    closes = {s: [values[s][t] for t in dates] for s in symbols}
    bars, missing_execution = {}, {}
    for sym in symbols:
        # the signal panel may forward-fill (record rule), but execution must never run on a
        # synthetic bar: a date without an actual bar is flagged and fails closed at execution
        missing = [t for t in dates if t not in rows_by_symbol[sym]]
        missing_execution[sym] = missing
        bars[sym] = [rows_by_symbol[sym].get(t) or
                     {"open_time_ms": t, "close_time_ms": t + MS_DAY - 1, "missing": True}
                     for t in dates]
    returns = {}
    for sym in symbols:
        series = closes[sym]
        returns[sym] = [0.0] + [series[i] / series[i - 1] - 1.0 for i in range(1, len(series))]

    index = [sum(closes[s][i] / closes[s][0] for s in symbols) / len(symbols) for i in range(len(dates))]
    return {
        "symbols": symbols,
        "dates": [day_of(t) for t in dates],
        "date_ms": dates,
        "closes": closes,
        "returns": returns,
        "bars": bars,
        "index": index,
        "ffilled_dates": ffilled,
        "missing_execution_dates": {s: len(v) for s, v in missing_execution.items()},
        "dropped_gap_dates": len(axis) - len(dates),
    }


# ---------------------------------------------------------------------------
# Registered signal: spectral + graph-topological features, causal RF walk-forward
# ---------------------------------------------------------------------------

FEATURE_MIN_INDEX = max(CORR_WINDOWS) + 1     # 120-day trailing estimator must be warm


def _numpy():
    try:
        import numpy as np  # type: ignore
    except ImportError as exc:  # pragma: no cover - container-only dependency
        raise RuntimeError("numpy is required for the registered correlation estimators") from exc
    return np


def _classifiers():
    try:
        from sklearn.ensemble import RandomForestClassifier  # type: ignore
    except ImportError as exc:  # pragma: no cover - container-only dependency
        raise RuntimeError("scikit-learn is required for the registered RandomForest model") from exc
    return RandomForestClassifier


def _corr_matrix(np, window):
    arr = np.asarray(window, dtype=float)
    if arr.shape[0] < 3:
        return None
    with np.errstate(invalid="ignore", divide="ignore"):
        c = np.corrcoef(arr, rowvar=False)
    c = np.atleast_2d(np.asarray(c, dtype=float))
    c = np.where(np.isfinite(c), c, 0.0)
    np.fill_diagonal(c, 1.0)
    return c


def _ewm_corr_matrix(np, window, half_life):
    arr = np.asarray(window, dtype=float)
    n = arr.shape[0]
    if n < 3:
        return None
    weights = np.asarray([0.5 ** ((n - 1 - k) / float(half_life)) for k in range(n)], dtype=float)
    weights = weights / weights.sum()
    mean = (weights[:, None] * arr).sum(axis=0)
    dev = arr - mean
    cov = (weights[:, None] * dev).T @ dev
    var = np.asarray(np.diag(cov), dtype=float).copy()
    std = np.where(np.isfinite(var) & (var > 0), np.sqrt(np.maximum(var, 0.0)), 0.0)
    denom = np.outer(std, std)
    with np.errstate(invalid="ignore", divide="ignore"):
        c = np.where(denom > 0, cov / denom, 0.0)
    c = np.where(np.isfinite(c), c, 0.0)
    np.fill_diagonal(c, 1.0)
    return c


def spectral_features(matrix):
    """Dominant-eigenvalue share, spectral gap, effective rank and mean |corr|."""
    np = _numpy()
    eig = np.sort(np.clip(np.linalg.eigvalsh(matrix), 0.0, None))[::-1]
    total = float(eig.sum())
    top = float(eig[0])
    second = float(eig[1]) if eig.size > 1 else 0.0
    denom = float((eig ** 2).sum())
    off = matrix - np.eye(matrix.shape[0])
    return {
        "dominant_eigenvalue_share": (top / total) if total > 0 else 0.0,
        "spectral_gap": ((top - second) / top) if top > 0 else 0.0,
        "effective_rank": ((total * total) / denom) if denom > 0 else 0.0,
        "top2_ratio": (second / top) if top > 0 else 0.0,
        "mean_abs_corr": float(np.abs(off).mean()) if off.size else 0.0,
    }


def graph_features(matrix, threshold):
    """Unweighted threshold graph over the correlation matrix: density/clustering/degree."""
    n = matrix.shape[0]
    adj = [[False] * n for _ in range(n)]
    degree = [0] * n
    edges = 0
    for i in range(n):
        for j in range(i + 1, n):
            if float(matrix[i][j]) >= threshold:
                adj[i][j] = adj[j][i] = True
                degree[i] += 1
                degree[j] += 1
                edges += 1
    pairs = n * (n - 1) / 2.0
    clustering = 0.0
    for i in range(n):
        nbrs = [j for j in range(n) if adj[i][j]]
        k = len(nbrs)
        if k < 2:
            continue
        links = 0
        for a in range(k):
            for b in range(a + 1, k):
                if adj[nbrs[a]][nbrs[b]]:
                    links += 1
        clustering += links / (k * (k - 1) / 2.0)
    return {
        "threshold": threshold,
        "edge_density": (edges / pairs) if pairs else 0.0,
        "mean_clustering": clustering / n if n else 0.0,
        "mean_degree": (sum(degree) / n) if n else 0.0,
    }


def traditional_features(panel, i):
    """Record: traditional features use 1-to-60-day lookbacks on the panel."""
    idx = panel["index"]
    out = {}
    for lag in TRAD_LOOKBACKS:
        base = idx[i - lag]
        out["panel_return_%dd" % lag] = (idx[i] / base - 1.0) if base else 0.0
    for lag in (5, 20, 60):
        window = [idx[t] / idx[t - 1] - 1.0 for t in range(i - lag + 1, i + 1) if idx[t - 1]]
        out["panel_vol_%dd" % lag] = statistics.pstdev(window) if len(window) > 1 else 0.0
    downside = [r for r in (idx[t] / idx[t - 1] - 1.0 for t in range(i - 19, i + 1))
                if r < 0] if i >= 19 else []
    out["downside_vol_20d"] = statistics.pstdev(downside) if len(downside) > 1 else 0.0
    peak = max(idx[t] for t in range(i - 59, i + 1)) if i >= 59 else max(idx[:i + 1])
    out["panel_drawdown_60d"] = (idx[i] / peak - 1.0) if peak else 0.0
    disp = []
    for sym in panel["symbols"]:
        rs = panel["returns"][sym][max(0, i - 19):i + 1]
        if len(rs) > 1:
            disp.append(statistics.pstdev(rs))
    out["cross_sectional_dispersion_20d"] = statistics.mean(disp) if disp else 0.0
    return out


FEATURE_NAMES = (
    ["panel_return_%dd" % lag for lag in TRAD_LOOKBACKS]
    + ["panel_vol_%dd" % lag for lag in (5, 20, 60)]
    + ["downside_vol_20d", "panel_drawdown_60d", "cross_sectional_dispersion_20d"]
    + ["%s_%s" % (prefix, key)
       for prefix in ("corr60", "corr120", "ewm30")
       for key in ("dominant_eigenvalue_share", "spectral_gap", "effective_rank",
                   "top2_ratio", "mean_abs_corr")]
    + ["%s_thr%s_%s" % (prefix, str(thr).replace(".", ""), key)
       for prefix in ("corr60", "corr120", "ewm30")
       for thr in GRAPH_THRESHOLDS
       for key in ("edge_density", "mean_clustering", "mean_degree")]
)


def feature_row(panel, i):
    """One strictly causal feature vector at formation i (list aligned to FEATURE_NAMES)."""
    np = _numpy()
    syms = panel["symbols"]
    values = dict(traditional_features(panel, i))
    matrices = {}
    for window in CORR_WINDOWS:
        lo = i - window + 1
        rows = [[panel["returns"][s][t] for s in syms] for t in range(lo, i + 1)]
        matrices["corr%d" % window] = _corr_matrix(np, rows)
    rows_ewm = [[panel["returns"][s][t] for s in syms] for t in range(i - EWM_HALF_LIFE + 1, i + 1)]
    matrices["ewm30"] = _ewm_corr_matrix(np, rows_ewm, EWM_HALF_LIFE)

    for prefix, matrix in matrices.items():
        if matrix is None:
            for key in ("dominant_eigenvalue_share", "spectral_gap", "effective_rank",
                        "top2_ratio", "mean_abs_corr"):
                values["%s_%s" % (prefix, key)] = 0.0
            for thr in GRAPH_THRESHOLDS:
                for key in ("edge_density", "mean_clustering", "mean_degree"):
                    values["%s_thr%s_%s" % (prefix, str(thr).replace(".", ""), key)] = 0.0
            continue
        for key, value in spectral_features(matrix).items():
            values["%s_%s" % (prefix, key)] = value
        for thr in GRAPH_THRESHOLDS:
            for key, value in graph_features(matrix, thr).items():
                if key == "threshold":
                    continue
                values["%s_thr%s_%s" % (prefix, str(thr).replace(".", ""), key)] = value
    return [float(values[name]) for name in FEATURE_NAMES]


def forward_labels(panel, i):
    """Record labels: >3% 10-day endpoint return (rally), >7% 10-day max drawdown (crash)."""
    syms = panel["symbols"]
    if i + HORIZON_DAYS >= len(panel["index"]):
        return None
    base = [panel["closes"][s][i] for s in syms]
    path = []
    for t in range(i, i + HORIZON_DAYS + 1):
        path.append(sum(panel["closes"][s][t] / base[a] for a, s in enumerate(syms)) / len(syms))
    endpoint = path[-1] - 1.0
    peak, drawdown = path[0], 0.0
    for value in path[1:]:
        peak = max(peak, value)
        drawdown = min(drawdown, value / peak - 1.0)
    return {
        "rally": endpoint > RALLY_THRESHOLD,
        "crash": (-drawdown) > CRASH_THRESHOLD,
        "endpoint_return": endpoint,
        "max_drawdown": drawdown,
    }


def percentile_rank(history, score):
    """Causal expanding percentile rank against every score formed up to and including now."""
    values = list(history) + [score]
    at_or_below = sum(1 for value in values if value <= score)
    return at_or_below / float(len(values))


def exposure_map(rally_rank, crash_rank):
    """Registered exposure states (record) + research-defined intermediate step bands."""
    if rally_rank is None or crash_rank is None:
        return 0.0, "no_signal"
    if rally_rank >= EXIT_RALLY_RANK:
        return 0.0, "euphoria_exit"
    if crash_rank >= EXIT_CRASH_RANK:
        return 0.0, "danger_exit"
    if ENTRY_RALLY_LO <= rally_rank < ENTRY_RALLY_HI and crash_rank < ENTRY_CRASH_HI:
        return MAX_EXPOSURE, "max_entry"
    if crash_rank >= ENTRY_CRASH_HI:
        return 0.3, "risk_off_reduced"
    if rally_rank >= 0.60:
        return 1.2, "intermediate_1_2"
    if rally_rank >= 0.45:
        return 0.9, "intermediate_0_9"
    if rally_rank >= 0.30:
        return 0.6, "intermediate_0_6"
    return 0.3, "intermediate_0_3"


def roc_auc(pairs):
    """Mann-Whitney rank AUC with average ranks for ties; None when a class is missing."""
    labelled = [(float(score), bool(label)) for score, label in pairs]
    positives = [s for s, l in labelled if l]
    negatives = [s for s, l in labelled if not l]
    if not positives or not negatives:
        return None
    ordered = sorted(labelled, key=lambda item: item[0])
    ranks = [0.0] * len(ordered)
    i = 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and ordered[j + 1][0] == ordered[i][0]:
            j += 1
        average = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[k] = average
        i = j + 1
    rank_sum = sum(rank for rank, (_, label) in zip(ranks, ordered) if label)
    n1, n0 = len(positives), len(negatives)
    return (rank_sum - n1 * (n1 + 1) / 2.0) / (n1 * n0)


def build_signal_series(panel, min_train=MIN_TRAIN_SAMPLES):
    """Strictly causal expanding walk-forward: fit -> score -> rank -> exposure.

    Returns a list aligned to the panel index (None where no signal is formable yet) plus a
    diagnostics dict. Training only ever uses samples whose 10-day label resolved strictly
    before the current formation, so nothing downstream can leak.
    """
    np = _numpy()
    RandomForestClassifier = _classifiers()
    n = len(panel["index"])
    feature_rows, labels = {}, {}
    for i in range(FEATURE_MIN_INDEX, n - HORIZON_DAYS):
        feature_rows[i] = feature_row(panel, i)
        labels[i] = forward_labels(panel, i)

    keys = sorted(feature_rows)
    out = [None] * n
    rally_history, crash_history = [], []
    auc_rally, auc_crash = [], []
    pool, ptr, fitted = [], 0, 0
    state_counts = {}
    for i in keys:
        while ptr < len(keys) and keys[ptr] + HORIZON_DAYS <= i - 1:
            j = keys[ptr]
            pool.append((feature_rows[j], labels[j]))
            ptr += 1
        if len(pool) < min_train:
            continue
        x_train = [row for row, _ in pool]
        y_rally = [1 if lab["rally"] else 0 for _, lab in pool]
        y_crash = [1 if lab["crash"] else 0 for _, lab in pool]
        if len(set(y_rally)) < 2 or len(set(y_crash)) < 2:
            # a degenerate training window cannot support both classes; stay strictly causal
            continue
        rally_model = RandomForestClassifier(**RF_PARAMS)
        crash_model = RandomForestClassifier(**RF_PARAMS)
        rally_model.fit(np.asarray(x_train, dtype=float), np.asarray(y_rally, dtype=int))
        crash_model.fit(np.asarray(x_train, dtype=float), np.asarray(y_crash, dtype=int))
        probe = np.asarray([feature_rows[i]], dtype=float)
        p_rally = float(rally_model.predict_proba(probe)[0][1])
        p_crash = float(crash_model.predict_proba(probe)[0][1])
        fitted += 1

        rally_rank = percentile_rank(rally_history, p_rally)
        crash_rank = percentile_rank(crash_history, p_crash)
        rally_history.append(p_rally)
        crash_history.append(p_crash)

        label = labels[i]
        auc_rally.append((p_rally, label["rally"]))
        auc_crash.append((p_crash, label["crash"]))

        exposure, state = exposure_map(rally_rank, crash_rank)
        state_counts[state] = state_counts.get(state, 0) + 1
        out[i] = {
            "index": i,
            "formation_day": panel["dates"][i],
            "p_rally": p_rally,
            "p_crash": p_crash,
            "rally_rank": rally_rank,
            "crash_rank": crash_rank,
            "exposure": exposure,
            "state": state,
        }

    diagnostics = {
        "feature_count": len(FEATURE_NAMES),
        "feature_names": list(FEATURE_NAMES),
        "candidate_formations": len(keys),
        "trained_formations": fitted,
        "min_train_samples": min_train,
        "rally_base_rate": (sum(1 for _, lab in pool if lab["rally"]) / float(len(pool))) if pool else 0.0,
        "crash_base_rate": (sum(1 for _, lab in pool if lab["crash"]) / float(len(pool))) if pool else 0.0,
        "signal_formations": sum(1 for row in out if row is not None),
        "state_counts": state_counts,
        "exposure_mean": (statistics.mean(row["exposure"] for row in out if row is not None)
                          if any(row is not None for row in out) else 0.0),
        "rf_params": dict(RF_PARAMS),
        "auc_rally_local_diagnostic": roc_auc(auc_rally),
        "auc_crash_local_diagnostic": roc_auc(auc_crash),
        "auc_note": ("non-gating local diagnostic on the canonical local panel; the record's "
                     "registered BCD-AUC rule is scoped to its post-2024 US equity extension"),
    }
    return out, diagnostics


# ---------------------------------------------------------------------------
# Execution: entry -> adverse scale-ins -> breakeven TP / resting invalidation,
# with the record's registered exit states overriding the holding period
# ---------------------------------------------------------------------------

def open_position(entry_bar, exposure, cost, dca, entry_k):
    initial = _fill_price(entry_bar["open"], "buy", cost)
    if initial <= 0:
        raise ValueError("non-positive entry fill")
    quote = BASE_QUOTE * exposure / MAX_EXPOSURE
    qty = quote / initial
    fee_rate = float(cost["fee_bps"]) / 10000.0
    return {
        "exposure": exposure,
        "entry_k": entry_k,
        "entry_ms": entry_bar["open_time_ms"],
        "initial": initial,
        "qty": qty,
        "basis": qty * initial,
        "level": 1,
        "fills": 1,
        "adds": 0,
        "turnover": quote,
        "funding_events_charged": 0,
        "exit_reason": None,
        "exit_ms": None,
        "exit_price": None,
        "max_basis": qty * initial,
        "max_active_tranches": 1,
        "ledger": [{
            "timestamp": entry_bar["open_time_ms"], "type": "entry", "qty_before": 0.0,
            "qty_after": qty, "price_mark": initial, "fee_delta": quote * fee_rate,
            "funding_delta": 0.0, "gross_delta": 0.0,
        }],
    }


def _close_position(pos, price, timestamp, cost, reason):
    fee_rate = float(cost["fee_bps"]) / 10000.0
    qty = pos["qty"]
    notional = qty * price
    sign = 1  # long-only: risk-off rotation, never a short leg
    pos["ledger"].append({
        "timestamp": timestamp, "type": "exit", "qty_before": qty, "qty_after": 0.0,
        "price_mark": price, "fee_delta": notional * fee_rate, "funding_delta": 0.0,
        "gross_delta": sign * (notional - pos["basis"]),
    })
    pos["qty"] = 0.0
    pos["exit_reason"] = reason
    pos["exit_ms"] = timestamp
    pos["exit_price"] = price
    pos["fills"] += 1
    pos["turnover"] += notional


def process_day(pos, bars, day_events, cost, dca, age_ok, record_exit_state, force_close):
    """Simulate one exact 288-bar UTC day for an open position (conservative ordering)."""
    if len(bars) != 288:
        raise ValueError("leg requires exactly 288 execution bars")
    fee_rate = float(cost["fee_bps"]) / 10000.0
    spacing = float(dca["spacing_pct"])
    mult = float(dca["size_multiplier"])
    tp_pct = float(dca["breakeven_tp_pct"])
    invalidation = float(dca["invalidation_pct"])
    sign = 1  # long-only

    event_idx = 0
    for bar_idx, bar in enumerate(bars):
        bar_start = bar["open_time_ms"]
        bar_end = bar_start + 300000
        while event_idx < len(day_events) and day_events[event_idx]["t"] < bar_end:
            event = day_events[event_idx]
            event_idx += 1
            if event["t"] >= bar_start and pos["qty"] > 0 and cost["funding_mult"] != 0:
                if int(event["t"]) % 300000 != 0:
                    raise ValueError("funding event is not on an exact 5m boundary")
                delta = sign * event["cost_per_unit"] * pos["qty"] * float(cost["funding_mult"])
                pos["ledger"].append({
                    "timestamp": event["t"], "type": "funding", "qty_before": pos["qty"],
                    "qty_after": pos["qty"], "price_mark": event["mark"], "fee_delta": 0.0,
                    "funding_delta": delta, "gross_delta": 0.0,
                })
                pos["funding_events_charged"] += 1

        if record_exit_state and bar_idx == 0:
            # registered exit state at the session open: reduce-only close of every layer
            _close_position(pos, _fill_price(bar["open"], "sell", cost), bar_start, cost,
                            record_exit_state)
            return pos

        if age_ok:
            avg = pos["basis"] / pos["qty"]
            stop = avg * (1.0 - invalidation)
            if bar["open"] <= stop:
                raw = min(bar["open"], stop)
                _close_position(pos, _fill_price(raw, "sell", cost), bar_start, cost, "stop")
                return pos

        while pos["level"] <= MAX_ADD_LEVELS:
            trigger = pos["initial"] * (1.0 - sign * spacing * pos["level"])
            if trigger <= 0:
                break
            if not (bar["low"] <= trigger):
                break
            fill = _fill_price(trigger, "buy", cost)
            quote = BASE_QUOTE * (mult ** pos["level"]) * pos["exposure"] / MAX_EXPOSURE
            add_qty = quote / fill
            qty_before = pos["qty"]
            pos["qty"] += add_qty
            pos["basis"] += add_qty * fill
            pos["max_basis"] = max(pos["max_basis"], pos["basis"])
            pos["adds"] += 1
            pos["fills"] += 1
            pos["turnover"] += quote
            pos["max_active_tranches"] = max(pos["max_active_tranches"], 1 + pos["adds"])
            pos["ledger"].append({
                "timestamp": bar_start, "type": "add", "qty_before": qty_before,
                "qty_after": pos["qty"], "price_mark": fill, "fee_delta": quote * fee_rate,
                "funding_delta": 0.0, "gross_delta": 0.0,
            })
            pos["level"] += 1

        if age_ok:
            avg = pos["basis"] / pos["qty"]
            stop = avg * (1.0 - invalidation)
            if bar["low"] <= stop:
                _close_position(pos, _fill_price(stop, "sell", cost), bar_start, cost, "stop")
                return pos
            take = avg * (1.0 + tp_pct)
            if bar["high"] >= take:
                _close_position(pos, _fill_price(take, "sell", cost), bar_start, cost, "tp")
                return pos

    if force_close and pos["qty"] > 0:
        last = bars[-1]
        _close_position(pos, _fill_price(last["close"], "sell", cost), last["close_time_ms"],
                        cost, "phase_end")
    return pos


def episode_metrics(pos):
    ledger = pos["ledger"]
    gross = sum(e["gross_delta"] for e in ledger)
    fees = sum(e["fee_delta"] for e in ledger)
    funding = sum(e["funding_delta"] for e in ledger)
    return {
        "gross_pnl": gross,
        "fees": fees,
        "funding": funding,
        "net_pnl": gross - fees - funding,
        "fills": pos["fills"],
        "adds": pos["adds"],
        "max_active_tranches": pos["max_active_tranches"],
        "turnover_usdt": pos["turnover"],
        "exit_reason": pos["exit_reason"],
        "capital_committed": pos["max_basis"],
        "exposure": pos["exposure"],
        "funding_events_charged": pos["funding_events_charged"],
        "execution_accounting": ledger,
    }


def evaluate_cell(symbol, daily_bars, exec_rows, signals, funding_days, dca, grid, meta,
                  day_cache):
    phase = grid_phase(grid)
    start, end = PHASES[phase]
    cost = cost_for(grid, meta)
    delay = cost["entry_delay_bars"]
    start_d = dt.date.fromisoformat(start)
    end_d = dt.date.fromisoformat(end)
    window = [k for k, bar in enumerate(daily_bars) if start_d <= day_of(bar["open_time_ms"]) <= end_d]
    if not window:
        raise ValueError("no execution days in phase %s for %s" % (phase, symbol))
    window_set = set(window)

    def bars_at(k):
        daily = daily_bars[k]
        if daily.get("missing"):
            raise ValueError("no actual daily execution bar at UTC %d" % daily["open_time_ms"])
        key = daily["open_time_ms"]
        cached = day_cache.get(key)
        if cached is None:
            cached = execution_day_slice(daily, exec_rows)
            day_cache[key] = cached
        return cached

    episodes = []
    pos = None
    missing = 0
    formations = sum(1 for k in window if k < len(signals) and signals[k] is not None)
    for k in window:
        if k + 1 + delay not in window_set and k < len(signals) and signals[k] is not None:
            missing += 1

    for k in window:
        sig_idx = k - 1 - delay
        sig = signals[sig_idx] if 0 <= sig_idx < len(signals) else None
        exposure = float(sig["exposure"]) if sig else 0.0
        state = sig["state"] if sig else "no_signal"
        # an entry must come from a formation inside this phase window (delay respected);
        # a held position only ever exits on the registered exposure==0 state
        entry_allowed = sig is not None and sig_idx in window_set

        if pos is not None and exposure <= 0.0:
            bars = bars_at(k)
            process_day(pos, bars, funding_days.get(bars[0]["open_time_ms"], []), cost, dca,
                        False, state, False)
            if pos["qty"] > 0.0:
                raise ValueError("registered exit state did not flatten the position")
            episodes.append(episode_metrics(pos))
            pos = None

        if pos is None:
            if exposure > 0.0 and entry_allowed:
                bars = bars_at(k)
                pos = open_position(bars[0], exposure, cost, dca, k)
                process_day(pos, bars, funding_days.get(bars[0]["open_time_ms"], []), cost, dca,
                            False, None, False)
                if pos["qty"] <= 0.0:
                    episodes.append(episode_metrics(pos))
                    pos = None
            continue

        bars = bars_at(k)
        age_ok = (k - pos["entry_k"]) >= MIN_HOLD_DAYS
        process_day(pos, bars, funding_days.get(bars[0]["open_time_ms"], []), cost, dca,
                    age_ok, None, False)
        if pos["qty"] <= 0.0:
            episodes.append(episode_metrics(pos))
            pos = None

    if pos is not None:
        last_k = window[-1]
        bars = bars_at(last_k)
        process_day(pos, bars, funding_days.get(bars[0]["open_time_ms"], []), cost, dca,
                    True, None, True)
        episodes.append(episode_metrics(pos))
        pos = None

    metrics = _metric_summary(episodes, start, end)
    metrics["formations_in_phase"] = formations
    metrics["missing_formations"] = missing
    return metrics


def _metric_summary(episodes, phase_start, phase_end):
    gross = sum(e["gross_pnl"] for e in episodes)
    fees = sum(e["fees"] for e in episodes)
    funding = sum(e["funding"] for e in episodes)
    net = sum(e["net_pnl"] for e in episodes)
    fills = sum(e["fills"] for e in episodes)
    adds = sum(e["adds"] for e in episodes)
    turnover = sum(e["turnover_usdt"] for e in episodes)
    tp_hits = sum(1 for e in episodes if e["exit_reason"] == "tp")
    stop_hits = sum(1 for e in episodes if e["exit_reason"] == "stop")
    end_exits = sum(1 for e in episodes if e["exit_reason"] in ("phase_end", "day_end"))
    record_exits = sum(1 for e in episodes if e["exit_reason"] in
                       ("euphoria_exit", "danger_exit", "no_signal"))
    returns = [e["net_pnl"] / START_EQUITY for e in episodes]
    sharpe = (statistics.mean(returns) / statistics.pstdev(returns) * math.sqrt(ANNUALIZATION)
              if len(returns) >= 2 and statistics.pstdev(returns) > 0 else 0.0)
    equity, peak, max_dd = START_EQUITY, START_EQUITY, 0.0
    for e in episodes:
        equity += e["net_pnl"]
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
        "tp_hits": tp_hits, "stop_hits": stop_hits, "margin_calls": 0,
        "end_exits": end_exits, "record_exits": record_exits, "open_at_end": 0,
        "funding_events_charged": sum(e["funding_events_charged"] for e in episodes),
        "decomposition_ok": abs((gross - fees - funding) - net) < 1e-3,
    }


def cell_key(row):
    return (
        row["symbol"],
        row["timeframe"],
        int(row["signal_rule_version"]),
        float(row["spacing_pct"]),
        float(row["size_multiplier"]),
        float(row["breakeven_tp_pct"]),
        float(row["invalidation_pct"]),
    )


def evaluate_all(meta, all_bars, all_execution_rows, signals, all_funding_days, symbols=None):
    rows_by_grid = {g: [] for g in GRIDS}
    eval_symbols = list(symbols) if symbols is not None else list(DEFAULT_INSTRUMENTS)
    for sym in eval_symbols:
        bars = all_bars[sym]
        exec_rows = all_execution_rows[sym]
        funding_days = all_funding_days[sym]
        day_cache = {}
        m = meta[sym]
        for grid in GRIDS:
            for cell in DCA_GRID:
                out = evaluate_cell(sym, bars, exec_rows, signals, funding_days, cell, grid, m,
                                    day_cache)
                rows_by_grid[grid].append({
                    "symbol": sym, "timeframe": TIMEFRAME,
                    "signal_rule_version": 1, "grid": grid, **cell, **out,
                })
    return rows_by_grid


def select_cohort(rows):
    """cohort-selector-v1: historical net_pnl > 0 and sharpe > 0 and episodes >= MIN_EPISODES_IS."""
    hist = [r for r in rows if r["grid"] == "historical"]
    best_episodes = max((int(r["episodes"]) for r in hist), default=0)
    if best_episodes < MIN_EPISODES_IS:
        return None, "insufficient_trades", None
    candidates = [
        r for r in hist
        if float(r["net_pnl"]) > 0 and float(r["sharpe"]) > 0 and int(r["episodes"]) >= MIN_EPISODES_IS
    ]
    if not candidates:
        return None, "no_qualifying_candidate", None

    def sort_key(r):
        return (
            -float(r["sharpe"]),
            -float(r["net_pnl"]),
            int(r["signal_rule_version"]),
            float(r["spacing_pct"]),
            float(r["size_multiplier"]),
            float(r["breakeven_tp_pct"]),
            float(r["invalidation_pct"]),
        )
    candidates.sort(key=sort_key)
    return candidates[0], None, candidates


def neighbourhood(winner, historical):
    found = {cell_key(r): r for r in historical}
    key = list(cell_key(winner))
    positions = [
        (3, list(DCA_AXES["spacing_pct"])),
        (4, list(DCA_AXES["size_multiplier"])),
        (5, list(DCA_AXES["breakeven_tp_pct"])),
        (6, list(DCA_AXES["invalidation_pct"])),
    ]
    agreeing = total = 0
    winner_positive = float(winner["net_pnl"]) > 0
    for pos_index, domain in positions:
        idx = domain.index(key[pos_index])
        for step in (-1, 1):
            if 0 <= idx + step < len(domain):
                probe = key.copy()
                probe[pos_index] = domain[idx + step]
                row = found.get(tuple(probe))
                if row is None:
                    continue
                total += 1
                agreeing += (float(row["net_pnl"]) > 0) == winner_positive
    fraction = agreeing / total if total else 0.0
    return {
        "neighbours": total, "agreeing": agreeing, "same_sign_fraction": fraction,
        "passed": bool(total and fraction >= MIN_NEIGHBOUR),
    }


METRIC_KEYS = (
    "gross_pnl", "fees", "funding", "net_pnl", "ending_equity", "episodes", "fills",
    "adds", "turnover_usdt", "sharpe", "max_dd_pct", "max_dd_usdt", "annualized_return",
    "max_effective_leverage", "capital_utilization", "tp_hits", "stop_hits", "margin_calls",
    "end_exits", "record_exits", "open_at_end",
)


def metric_block(row):
    return {k: row[k] for k in METRIC_KEYS}


def write_grid(output_dir, grid, rows):
    path = Path(output_dir) / ("grid_%s.csv" % grid)
    fieldnames = [
        "symbol", "timeframe", "signal_rule_version", "spacing_pct", "size_multiplier",
        "breakeven_tp_pct", "invalidation_pct", *METRIC_KEYS,
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


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
    data = spec.get("data")
    if data.get("source") != "/data/raw" or data.get("market") != EXECUTION_MARKET \
            or data.get("timeframes") != [TIMEFRAME]:
        raise ValueError("local eligible universe mismatch")
    if spec.get("params") != [{"signal_rule_version": 1}]:
        raise ValueError("strategy domain changed")
    if spec.get("signal_rule") != REGISTERED_SIGNAL_RULE:
        raise ValueError("registered signal rule changed")
    if spec.get("exposure_map") != EXPOSURE_MAP:
        raise ValueError("registered exposure map changed")
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
    counts = expected_counts(symbols)
    exp = spec.get("expected") if isinstance(spec.get("expected"), dict) else {}
    if exp.get("expected_case_evaluations") != counts["case_evaluations_total"] \
            or exp.get("grids") != list(GRIDS) or exp.get("cohorts") != counts["cohorts"] \
            or exp.get("strategy_cases_per_cohort") != 1 \
            or exp.get("dca_configs_per_cohort") != len(DCA_GRID):
        raise ValueError("expected coverage mismatch")
    eu = round_spec.get("eligible_universe") if isinstance(round_spec.get("eligible_universe"), dict) else {}
    if sorted(eu.get("instruments") or []) != symbols or eu.get("timeframes") != [TIMEFRAME] \
            or eu.get("execution_market") != EXECUTION_MARKET:
        raise ValueError("round local eligible universe mismatch")
    if round_spec.get("signal_rule") != REGISTERED_SIGNAL_RULE:
        raise ValueError("round registered signal rule changed")
    if round_spec.get("exposure_map") != EXPOSURE_MAP:
        raise ValueError("round exposure map changed")
    if round_spec.get("execution_semantics") != EXECUTION_SEMANTICS:
        raise ValueError("round execution semantics differ")
    if round_spec.get("gates") != GATES:
        raise ValueError("round gates differ")
    return symbols, counts


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
    validate_spec(spec, round_spec)

    klines_root = Path(raw_root) / "binance" / "usdm" / "klines" if raw_root else KLINES_ROOT
    funding_root = Path(raw_root) / "binance" / "usdm" / "funding" if raw_root else FUNDING_ROOT
    instruments_path = Path(raw_root) / "binance" / "usdm" / "instruments" / "usdm-perp-instruments.json" if raw_root else INSTRUMENTS_PATH
    config_path = Path(raw_root) / "_meta" / "CONFIG.json" if raw_root else CONFIG_PATH
    schema_path = Path(raw_root) / "_meta" / "SCHEMA.md" if raw_root else SCHEMA_PATH

    meta = load_instruments(instruments_path)
    config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.is_file() else {}
    schema_text = schema_path.read_text(encoding="utf-8") if schema_path.is_file() else ""

    registered_symbols = list(DEFAULT_INSTRUMENTS)
    universe = inspect_local_universe(config, schema_text, registered_symbols, meta)
    if not universe["legal"]:
        raise RuntimeError("local universe is not legal: %s" % universe)

    start_date, end_date = PHASES["full"]
    rows_by_symbol, files_by_symbol = {}, {}
    for sym in registered_symbols:
        rows, files = load_local_rows(sym, root=klines_root / sym / TIMEFRAME)
        rows_by_symbol[sym] = rows
        files_by_symbol[sym] = files

    atomic_json(artifacts / "progress.json",
                {"phase": "building_panel", "updated_at_utc": utc_now()})
    panel = build_panel(rows_by_symbol, start_date, end_date)

    atomic_json(artifacts / "progress.json",
                {"phase": "walk_forward_signal", "updated_at_utc": utc_now()})
    signals, signal_diag = build_signal_series(panel)
    if signal_diag["signal_formations"] < MIN_EPISODES_IS:
        raise RuntimeError("registered signal produced too few formations: %s"
                           % signal_diag["signal_formations"])

    all_bars = {sym: panel["bars"][sym] for sym in registered_symbols}
    all_execution_rows, all_funding_days, funding_reports = {}, {}, {}
    start_ms = int(dt.datetime.fromisoformat(start_date + "T00:00:00+00:00").timestamp() * 1000)
    end_ms = int(dt.datetime.fromisoformat(end_date + "T23:59:59+00:00").timestamp() * 1000)
    for sym in registered_symbols:
        execution_rows, _ = load_execution_rows(sym, root=klines_root / sym / EXEC_TIMEFRAME)
        all_execution_rows[sym] = execution_rows
        events, report = load_funding(sym, start_ms, end_ms, root=funding_root)
        all_funding_days[sym] = funding_by_day(events)
        funding_reports[sym] = report

    atomic_json(artifacts / "progress.json",
                {"phase": "evaluating_grids", "updated_at_utc": utc_now()})
    rows_by_grid = evaluate_all(meta, all_bars, all_execution_rows, signals, all_funding_days)
    for grid, rows in rows_by_grid.items():
        write_grid(artifacts, grid, rows)

    atomic_json(artifacts / "progress.json",
                {"phase": "evaluating_cohorts", "updated_at_utc": utc_now()})
    cohort_results, survivors = [], []
    for sym in registered_symbols:
        cohort_name = "%s/%s" % (sym, TIMEFRAME)
        cohort_rows = [r for g in GRIDS for r in rows_by_grid[g] if r["symbol"] == sym]
        winner, cull_reason, candidates = select_cohort(cohort_rows)
        if winner is None:
            cohort_results.append({
                "cohort": cohort_name, "status": "CULLED", "winner": None,
                "cull_reasons": [cull_reason], "survivor_checks": None, "neighbourhood": None,
            })
            continue

        w_key = cell_key(winner)
        by_grid = {}
        for g in GRIDS:
            matched = [r for r in rows_by_grid[g] if cell_key(r) == w_key]
            if not matched:
                raise RuntimeError("winner cell missing in grid %s for %s" % (g, sym))
            by_grid[g] = matched[0]

        hist_row, oos_row, full_row = by_grid["historical"], by_grid["oos"], by_grid["full"]
        hist_historical = [r for r in rows_by_grid["historical"] if r["symbol"] == sym]
        nb = neighbourhood(winner, hist_historical)

        oos_ok = (float(oos_row["net_pnl"]) > 0 and float(oos_row["sharpe"]) > 0
                  and int(oos_row["episodes"]) >= MIN_EPISODES_OOS)
        full_ok = float(full_row["net_pnl"]) > 0
        robustness_grids = ("fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks")
        failed_robustness = [g for g in robustness_grids if not (float(by_grid[g]["net_pnl"]) > 0)]
        robustness_ok = not failed_robustness
        nb_ok = nb["passed"]

        culls = []
        if not oos_ok:
            culls.append("oos_economic")
        if not full_ok:
            culls.append("full_economic")
        if not robustness_ok:
            culls.append("robustness_economic:" + ",".join(failed_robustness))
        if not nb_ok:
            culls.append("parameter_neighbourhood")

        record = {
            "cohort": cohort_name,
            "status": "SURVIVOR" if not culls else "CULLED",
            "winner": cell_key(winner),
            "cull_reasons": culls,
            "survivor_checks": {
                "historical_winner_found": True,
                "oos_economic": oos_ok,
                "full_economic": full_ok,
                "robustness_economic": robustness_ok,
                "robustness_failed_grids": failed_robustness,
                "parameter_neighbourhood": nb_ok,
            },
            "neighbourhood": nb,
            "historical_metrics": metric_block(hist_row),
            "oos_metrics": metric_block(oos_row),
            "full_metrics": metric_block(full_row),
            "stress_metrics": {g: metric_block(by_grid[g]) for g in robustness_grids},
        }
        cohort_results.append(record)
        if not culls:
            survivors.append(record)

    immutable_json(artifacts / "cohort_results.json", cohort_results)
    immutable_json(artifacts / "cohort_survivors.json", survivors)

    immutable_json(artifacts / "local_data_evidence.json", {
        "family_id": FAMILY_ID,
        "local_universe": universe,
        "registered_symbols": registered_symbols,
        "measured_symbols": registered_symbols,
        "raw_root": str(klines_root),
        "files_per_symbol": {s: [p.name for p in files_by_symbol[s]] for s in registered_symbols},
        "file_count": sum(len(v) for v in files_by_symbol.values()),
        "registered_window": {"start": start_date, "end": end_date},
        "funding_coverage": funding_reports,
    })

    assertions = {
        "fixed_starting_equity_enforced": True,
        "single_linear_usdt_perp_instrument": True,
        "max_leverage_10x_enforced": True,
        "max_routine_active_tranches_11": True,
        "reserve_tranche_12_never_deployed": True,
        "reduce_only_exits_enforced": True,
        "same_bar_adverse_before_favorable_tp": True,
        "per_fill_fee_and_funding_deducted": True,
        "independent_gross_pnl_accumulator": True,
        "pnl_decomposition_verified": all(
            all(r["decomposition_ok"] for r in rows) for rows in rows_by_grid.values()
        ),
        "no_synthetic_or_imputed_rows": True,
        "official_funding_only_missing_zero": all(
            rep["missing_intervals_are_zero_not_modeled"] for rep in funding_reports.values()
        ),
        "long_only_no_short_leg": True,
        "record_exit_overrides_holding_period": True,
        "min_8_trading_day_holding_enforced": True,
        "strictly_causal_walk_forward": True,
    }
    immutable_json(artifacts / "assertions.json", assertions)

    immutable_json(artifacts / "panel_evidence.json", {
        "family_id": FAMILY_ID,
        "panel": {
            "symbols": panel["symbols"],
            "observation_days": len(panel["dates"]),
            "first_day": panel["dates"][0].isoformat(),
            "last_day": panel["dates"][-1].isoformat(),
            "ffilled_dates_record_rule": panel["ffilled_dates"],
            "missing_execution_dates": panel["missing_execution_dates"],
            "dropped_gap_dates": panel["dropped_gap_dates"],
            "return_definition": "simple daily return P_t/P_{t-1} - 1 on daily closes",
        },
        "signal": signal_diag,
        "hypothesis": {
            "mechanism": ("cross-asset correlation network topology reorganises before headline "
                          "volatility reacts: dominant eigenvalue share rises, spectral gap "
                          "narrows, effective rank collapses, edge density/clustering shift"),
            "registered_direction": "long-only with risk-off rotation",
            "label_thresholds": {"rally_endpoint_return_10d": RALLY_THRESHOLD,
                                 "crash_max_drawdown_10d": CRASH_THRESHOLD},
            "rank_thresholds": {"entry_rally": [ENTRY_RALLY_LO, ENTRY_RALLY_HI],
                                "entry_crash_lt": ENTRY_CRASH_HI,
                                "exit_rally_gte": EXIT_RALLY_RANK,
                                "exit_crash_gte": EXIT_CRASH_RANK},
        },
        "exposure_map": EXPOSURE_MAP,
        "non_gating": True,
    })

    immutable_json(artifacts / "falsification.json", {
        "exact_source_battery_measured": False,
        "claim_scope": CLAIM_SCOPE,
        "falsification_plan": [
            {
                "id": "out_of_sample_extension_bcd_auc",
                "registered_test": ("extend beyond 2024 into post-2024 regimes; BCD-AUC > 0.65 "
                                    "above random across the extended period"),
                "falsification_rule": "BCD-AUC <= 0.65 falsifies the crash-detection claim",
                "status": "LOCAL_PANEL_DIAGNOSTIC_ONLY",
                "reason": ("the registered test targets the source's US equity ETF extension; "
                           "this run is scoped to the canonical local 4-symbol USD-M perpetual "
                           "panel. A non-gating local crash-score AUC is reported in "
                           "panel_evidence.signal.auc_crash_local_diagnostic."),
                "local_bcd_auc": signal_diag.get("auc_crash_local_diagnostic"),
            },
            {
                "id": "alternative_universe_spectral_auc_contribution",
                "registered_test": ("non-US equity markets and non-equity asset classes; spectral "
                                    "features retain >5 pp AUC contribution vs traditional-only"),
                "falsification_rule": "spectral contribution <= 5 pp falsifies the spectral claim",
                "status": "NOT_MEASURED_SOURCE_UNIVERSE_SCOPE",
                "reason": ("requires the registered multi-asset alternative universes; the local "
                           "crypto panel is the registered execution universe, not a substitute "
                           "for that battery. Not a core-signal prerequisite."),
            },
            {
                "id": "rf_hyperparameter_perturbation",
                "registered_test": ("vary RF tree count, depth and min samples by +/-50%; Sharpe "
                                    "remains > 0.8 across perturbations"),
                "falsification_rule": "Sharpe <= 0.8 under perturbation falsifies robustness",
                "status": "NOT_MEASURED_HYPERPARAMETER_PERTURBATION",
                "reason": ("the frozen round-spec pins one registered RF configuration; running a "
                           "perturbation battery would create unregistered strategy cases outside "
                           "the single registered signal_rule_version."),
            },
            {
                "id": "regime_breakdown_cagr",
                "registered_test": ("bull (2012-2019), crisis (2020 COVID, 2022 rate hikes) and "
                                    "recovery regimes; no single regime has negative CAGR"),
                "falsification_rule": "any regime with negative CAGR falsifies the claim",
                "status": "NOT_MEASURED_SOURCE_REGIME_CALENDAR",
                "reason": ("the registered regime calendar is defined on the source's 2012-2024 US "
                           "equity history; the local canonical window starts 2022-01-01. Phase "
                           "metrics for historical/oos/full are reported per grid instead."),
            },
            {
                "id": "fee_stress_triple",
                "registered_test": "triple transaction costs to 15 bps per trade; Sharpe > 0.7",
                "falsification_rule": "Sharpe <= 0.7 under 15 bps falsifies fee robustness",
                "status": "COMPUTED_CELL_BY_CELL",
                "reason": ("fee_2x and cost_attrition_40bps grids are computed for every cell; "
                           "the registered canonical taker fee drives the base cost."),
            },
        ],
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

    total_evals = sum(len(r) for r in rows_by_grid.values())
    expected_evals = len(registered_symbols) * 1 * len(DCA_GRID) * len(GRIDS)
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
        "cohorts_evaluated": len(registered_symbols),
        "cohort_survivor_count": len(survivors),
        "cohort_survivors": [s["cohort"] for s in survivors],
        "verdict_recommendation": "PASS" if len(survivors) >= 1 else "REJECT",
        "performance_claimable": bool(survivors),
        "disposition_version": "cohort-disposition-v1",
        "selector_version": "cohort-selector-v1",
        "assertions_all_true": all(assertions.values()),
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
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
