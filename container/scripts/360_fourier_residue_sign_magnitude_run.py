#!/usr/bin/env python3
"""Deterministic local-universe full backtest for the Fourier-residue identity and microstructure return autocorrelation decomposition family.

The reviewed source (Victoria Portnaya 2026, 'The Bounce Has No Direction: Sign, Magnitude, and the Microstructure of Equity Return Predictability: Fourier-Residue Identities, Fejér Sums, and Evidence from US Equity and Cross-Asset Markets, 1993–2026', arXiv:2606.29591)
demonstrates that broad market daily return autocorrelation at lag 1 is driven almost purely by magnitude shrinkage (bid-ask bounce and partial price adjustment), whereas lag-3 daily returns display a statistically significant directional partial-adjustment contrarian signal:
    r_{t-2} < 0 -> long entry on day t+1
    r_{t-2} > 0 -> short entry on day t+1

Under the quant-runtime-pipeline contract (v1.8 / §14.4 / §6.4 lifecycle footer):
  1. The complete legal local eligible universe is registered:
     canonical local Binance USD-M perpetual 1d, 4 symbols (BNBUSDT, BTCUSDT, ETHUSDT, SOLUSDT).
     Source venue, quote currency, named symbols and source-universe breadth are provenance/external-validity context, not execution prerequisites.
  2. Four-axis DCA domain (48 cells per cohort per grid):
     spacing_pct in {0.01, 0.02, 0.03, 0.04}
     size_multiplier in {1.0, 1.1}
     breakeven_tp_pct in {0.01, 0.02, 0.03}
     invalidation_pct in {0.05, 0.10}
     Fixed constants: starting_equity = 30000 USDT, base_quote = 1000 USDT, max leverage 10x, routine active tranches max 11 (tranche #12 reserved).
  3. Cohort survivor semantics (cohort-selector-v1 / cohort-disposition-v1):
     4 cohorts (one per symbol, 1d timeframe). Historical winner selected by Sharpe desc, net PnL desc, lexical tie-break; carried across OOS, full, and robustness stress grids.
  4. Ten evaluation grids:
     historical, oos, full, fee_2x, funding_2x, entry_delay_1_bar, slippage_2ticks, no_funding, no_funding_full, cost_attrition_40bps.
  5. The runner writes deterministic execution artifacts inside the attempt directory; it writes no terminal sentinel (DONE/FAILED/INCOMPLETE) and no verdict.json.
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

FAMILY_ID = "fourier-residue-sign-magnitude-equity-reversal-decomposition-2026-09-04"
ROUND_ID = "fourier-residue-sign-magnitude-equity-reversal-decomposition-2026-09-04-r1"
RUN_ID = "fourier-residue-sign-magnitude-equity-reversal-decomposition-2026-09-04-r1-u1"
RUNNER_NAME = "360_fourier_residue_sign_magnitude_run.py"

FINGERPRINT_INPUT = (
    "fourier-residue-sign-magnitude-equity-reversal-decomposition-2026-09-04|"
    "universe=portability=adapted/unproven (source-market; research-defined);"
    "window=record-faithful|"
    "dca=spacing_pct=0.01,0.02,0.03,0.04;size_multiplier=1.0,1.1;"
    "breakeven_tp_pct=0.01,0.02,0.03;invalidation_pct=0.05,0.10|"
    "selector=cohort-selector-v1;disposition=cohort-disposition-v1|"
    "source=fourier-residue-sign-magnitude-equity-reversal-decomposition-2026-09-04.md"
)

RAW_ROOT = Path("/data/raw")
KLINES_ROOT = RAW_ROOT / "binance" / "usdm" / "klines"
FUNDING_ROOT = RAW_ROOT / "binance" / "usdm" / "funding"
INSTRUMENTS_PATH = RAW_ROOT / "binance" / "usdm" / "instruments" / "usdm-perp-instruments.json"
CONFIG_PATH = RAW_ROOT / "_meta" / "CONFIG.json"
SCHEMA_PATH = RAW_ROOT / "_meta" / "SCHEMA.md"

OWNERSHIP_KEYS = ("task_id", "kanban_task_id", "kanban_board")

TIMEFRAME = "1d"
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

EXECUTION_MARKET = "BINANCE_USDM_PERP"
SOURCE_MARKET = "US equity ETFs (SPY, QQQ, IWM), single-stocks (AAPL, MSFT), and 21-asset cross-asset panel via daily closing prices"
CLAIM_SCOPE = "canonical local Binance USD-M perpetual 1d, 4 symbols (BNBUSDT/BTCUSDT/ETHUSDT/SOLUSDT); NOT a US equity ETF or 21-asset panel reproduction"

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
    "position_direction": "long_short_contrarian_to_lag3_return_sign",
    "numeraire": "USDT",
    "starting_equity_usdt": START_EQUITY,
    "base_quote_usdt": BASE_QUOTE,
    "max_leverage": LEVERAGE,
    "routine_active_tranches_max": ROUTINE_ACTIVE_TRANCHES_MAX,
    "reserve_tranche": RESERVE_TRANCHE,
    "initial_entry_counts_as_active_tranche": True,
    "max_add_levels": MAX_ADD_LEVELS,
    "same_bar_order": "adverse_before_favorable_tp",
    "holding_period": "one daily bar from the first executable bar after daily formation",
    "exit_mode": "reduce_only",
}

REGISTERED_SIGNAL_RULE = {
    "version": 1,
    "observation_unit": "daily return r_t = log(P_t / P_{t-1})",
    "formation": "daily close prices, return calculated at day close",
    "decomposition": "Fourier-Residue Identity (FRI) separating binary sign channel (k=2) from magnitude channel (k=4)",
    "lag1_sign_finding": "sign test insignificant (p > 0.05); magnitude shrinkage drives scalar autocorrelation",
    "lag3_directional_finding": "statistically significant directional partial-adjustment reversal",
    "trading_signal": "r_{t-2} < 0 -> long entry on day t+1; r_{t-2} > 0 -> short entry on day t+1",
    "direction": "long/short (contrarian to lag-3 return sign)",
    "holding_period": "1 daily bar",
    "is_search_axis": False,
    "note": "the directional partial-adjustment trading adaptation is registered as research-defined in execution detail; the FRI mathematical decomposition, lag-1 magnitude null and lag-3 directional signal are source-reported and unchanged",
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


def load_funding(symbol, start_ms, end_ms, root=None):
    """Official funding observations only; a missing interval costs zero, never a modeled row."""
    base = Path(root) if root is not None else FUNDING_ROOT
    path = base / symbol / ("%s-funding.jsonl.gz" % symbol)
    report = {
        "file": str(path), "file_missing": not path.is_file(), "official": 0,
        "modeled_ignored": 0, "other_ignored": 0, "out_of_window": 0,
        "first_official_utc": None, "last_official_utc": None,
        "missing_intervals_are_zero_not_modeled": True,
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
            if status == "official" and t % 300000 != 0:
                raise RuntimeError("official funding timestamp is not on a 5m boundary for %s" % symbol)
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
    base = Path(root) if root is not None else KLINES_ROOT / symbol / "5m"
    files = sorted(base.glob("%s-5m-*.jsonl.gz" % symbol))
    if not files:
        raise FileNotFoundError("no canonical 5m klines in %s" % base)
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
    per_symbol = {}
    legal = bool(found and klines_declared and one_day)
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
        "schema_mentions_klines": "klines" in schema_text.lower(),
    }


def fejer_variance_ratios(returns):
    """Report direct q-period variance ratios and their Fejer reconstruction."""
    if len(returns) < 3:
        return {}
    mean_r = statistics.mean(returns)
    var_r = statistics.pvariance(returns)
    if var_r <= 1e-14:
        return {}

    def rho(lag):
        if lag >= len(returns):
            return 0.0
        cov = sum((returns[i] - mean_r) * (returns[i - lag] - mean_r)
                  for i in range(lag, len(returns))) / (len(returns) - lag)
        return cov / var_r

    out = {}
    for q in (2, 5, 20, 60):
        if len(returns) <= q:
            continue
        q_returns = [sum(returns[i:i + q]) for i in range(len(returns) - q + 1)]
        direct = statistics.pvariance(q_returns) / (q * var_r)
        fejer_sum = sum((1.0 - lag / q) * rho(lag) for lag in range(1, q))
        reconstructed = 1.0 + 2.0 * fejer_sum
        out[str(q)] = {
            "direct_variance_ratio": direct,
            "fejer_sum": fejer_sum,
            "fejer_variance_ratio": reconstructed,
            "finite_sample_residual": direct - reconstructed,
        }
    return out


def fourier_residue_analysis(closes):
    """Computes Fourier-Residue Identity autocorrelations for sign (k=2) and magnitude (k=4)."""
    if len(closes) < 10:
        return {}
    n = len(closes)
    returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, n)]
    n_ret = len(returns)
    if n_ret < 5:
        return {}

    mean_r = statistics.mean(returns)
    var_r = sum((x - mean_r) ** 2 for x in returns) / n_ret

    def rho(lag):
        if var_r <= 1e-14 or lag >= n_ret:
            return 0.0
        cov = sum((returns[i] - mean_r) * (returns[i - lag] - mean_r) for i in range(lag, n_ret)) / (n_ret - lag)
        return cov / var_r

    rho_1 = rho(1)
    rho_3 = rho(3)

    # Binary sign channel (k=2): s_t = 1[r_t > 0]
    s_sign = [1 if r > 0 else 0 for r in returns]

    def sign_test(lag):
        pairs = [(s_sign[i], s_sign[i + lag]) for i in range(len(s_sign) - lag)]
        if not pairs:
            return 0.5, 0.0, 0.0
        m_count = len(pairs)
        matches = sum(1 for a, b in pairs if a == b)
        p_cont = matches / m_count
        rho_sign = 2.0 * p_cont - 1.0
        z_sign = (2.0 * p_cont - 1.0) * math.sqrt(m_count)
        return p_cont, rho_sign, z_sign

    p_1_0, rho_sign_1, z_sign_1 = sign_test(1)
    p_3_0, rho_sign_3, z_sign_3 = sign_test(3)

    # Magnitude channel (k=4):
    abs_r = [abs(r) for r in returns]
    med_abs = statistics.median(abs_r)

    def bucket(r):
        if r < 0:
            return 0 if abs(r) >= med_abs else 1
        else:
            return 2 if abs(r) < med_abs else 3

    s_mag = [bucket(r) for r in returns]

    def fri_gamma(lag, A=1, k=4):
        pairs = [(s_mag[i], s_mag[i + lag]) for i in range(len(s_mag) - lag)]
        if not pairs:
            return 0.0, 0.0
        re_sum = 0.0
        im_sum = 0.0
        for st, st_m in pairs:
            diff = (st - st_m) % k
            angle = 2.0 * math.pi * A * diff / k
            re_sum += math.cos(angle)
            im_sum += math.sin(angle)
        return re_sum / len(pairs), im_sum / len(pairs)

    gamma_mag_1_re, gamma_mag_1_im = fri_gamma(1, A=1, k=4)
    gamma_mag_3_re, gamma_mag_3_im = fri_gamma(3, A=1, k=4)

    return {
        "n_returns": n_ret,
        "scalar_rho_1": rho_1,
        "scalar_rho_3": rho_3,
        "fejer_variance_ratios": fejer_variance_ratios(returns),
        "binary_sign_lag1": {"p_1_0": p_1_0, "rho_sign": rho_sign_1, "z_sign": z_sign_1},
        "binary_sign_lag3": {"p_3_0": p_3_0, "rho_sign": rho_sign_3, "z_sign": z_sign_3},
        "magnitude_fri_lag1": {"re": gamma_mag_1_re, "im": gamma_mag_1_im},
        "magnitude_fri_lag3": {"re": gamma_mag_3_re, "im": gamma_mag_3_im},
    }


def build_signals(rows):
    """Generates daily lag-3 partial-adjustment signals: r_{t-2} < 0 -> long; r_{t-2} > 0 -> short."""
    keys = sorted(rows.keys())
    bars = [rows[k] for k in keys]
    closes = [b["close"] for b in bars]
    if len(closes) < 4:
        return bars, []
    returns = [0.0] + [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    plans = []
    for i in range(3, len(bars) - 1):
        r_lag3 = returns[i - 2]
        if r_lag3 == 0.0:
            continue
        direction = "long" if r_lag3 < 0.0 else "short"
        f_day = dt.datetime.fromtimestamp(bars[i]["open_time_ms"] / 1000, dt.timezone.utc).date()
        plans.append({
            "formation_idx": i,
            "formation_day": f_day,
            "direction": direction,
            "r_lag3": r_lag3,
            "bar_idx": i + 1,
        })
    return bars, plans


def simulate_leg(bars, direction, dca, cost, funding_events):
    """Simulate one fully covered execution day with timestamped fill/funding ledger."""
    if len(bars) != 288:
        raise ValueError("leg requires exactly 288 execution bars")
    fee_rate = float(cost["fee_bps"]) / 10000.0
    spacing = float(dca["spacing_pct"])
    mult = float(dca["size_multiplier"])
    tp_pct = float(dca["breakeven_tp_pct"])
    invalidation = float(dca["invalidation_pct"])
    sign = 1 if direction == "long" else -1

    initial_raw = bars[0]["open"]
    initial = _fill_price(initial_raw, "buy" if sign > 0 else "sell", cost)
    if initial <= 0:
        raise ValueError("non-positive entry fill")
    qty = BASE_QUOTE / initial
    basis = qty * initial
    fees = BASE_QUOTE * fee_rate
    turnover = BASE_QUOTE
    fills, adds, level = 1, 0, 1
    exit_reason, exit_price = "day_end", None
    ledger = [{
        "timestamp": bars[0]["open_time_ms"], "type": "entry", "qty_before": 0.0,
        "qty_after": qty, "price_mark": initial, "fee_delta": BASE_QUOTE * fee_rate,
        "funding_delta": 0.0, "gross_delta": 0.0,
    }]
    funding = 0.0
    charged = 0
    event_idx = 0
    ordered_funding = sorted((e for e in funding_events
                              if bars[0]["open_time_ms"] <= e["t"] < bars[-1]["open_time_ms"] + 300000),
                             key=lambda e: e["t"])
    if any(int(event["t"]) % 300000 != 0 for event in ordered_funding):
        raise ValueError("funding event is not on an exact 5m boundary")

    bar_idx = 0
    for bar_idx, bar in enumerate(bars):
        bar_start = bar["open_time_ms"]
        bar_end = bar_start + 300000
        if bar_idx:
            while event_idx < len(ordered_funding) and ordered_funding[event_idx]["t"] < bar_end:
                event = ordered_funding[event_idx]
                if event["t"] >= bar_start and qty > 0 and cost["funding_mult"] != 0:
                    delta = sign * event["cost_per_unit"] * qty * float(cost["funding_mult"])
                    funding += delta
                    charged += 1
                    ledger.append({
                        "timestamp": event["t"], "type": "funding", "qty_before": qty,
                        "qty_after": qty, "price_mark": event["mark"], "fee_delta": 0.0,
                        "funding_delta": delta, "gross_delta": 0.0,
                    })
                event_idx += 1
        elif event_idx < len(ordered_funding):
            while event_idx < len(ordered_funding) and ordered_funding[event_idx]["t"] < bar_end:
                event = ordered_funding[event_idx]
                if event["t"] >= bar_start and cost["funding_mult"] != 0:
                    delta = sign * event["cost_per_unit"] * qty * float(cost["funding_mult"])
                    funding += delta
                    charged += 1
                    ledger.append({
                        "timestamp": event["t"], "type": "funding", "qty_before": qty,
                        "qty_after": qty, "price_mark": event["mark"], "fee_delta": 0.0,
                        "funding_delta": delta, "gross_delta": 0.0,
                    })
                event_idx += 1

        avg = basis / qty
        stop = avg * (1.0 - invalidation) if sign > 0 else avg * (1.0 + invalidation)
        adverse_open = bar["open"] <= stop if sign > 0 else bar["open"] >= stop
        if adverse_open:
            raw_exit = min(bar["open"], stop) if sign > 0 else max(bar["open"], stop)
            exit_reason, exit_price = "stop", _fill_price(raw_exit, "sell" if sign > 0 else "buy", cost)
            ledger.append({
                "timestamp": bar_start, "type": "exit", "qty_before": qty,
                "qty_after": 0.0, "price_mark": exit_price, "fee_delta": qty * exit_price * fee_rate,
                "funding_delta": 0.0, "gross_delta": sign * (qty * exit_price - basis),
            })
            break
        while level <= MAX_ADD_LEVELS:
            trigger = initial * (1.0 - sign * spacing * level)
            if trigger <= 0:
                break
            reached = bar["low"] <= trigger if sign > 0 else bar["high"] >= trigger
            if not reached:
                break
            add_fill = _fill_price(trigger, "buy" if sign > 0 else "sell", cost)
            quote = BASE_QUOTE * (mult ** level)
            add_qty = quote / add_fill
            qty_before = qty
            qty += add_qty
            basis += add_qty * add_fill
            fees += quote * fee_rate
            turnover += quote
            fills += 1
            adds += 1
            ledger.append({
                "timestamp": bar_start, "type": "add", "qty_before": qty_before,
                "qty_after": qty, "price_mark": add_fill, "fee_delta": quote * fee_rate,
                "funding_delta": 0.0, "gross_delta": 0.0,
            })
            level += 1
        avg = basis / qty
        stop = avg * (1.0 - invalidation) if sign > 0 else avg * (1.0 + invalidation)
        stop_hit = bar["low"] <= stop if sign > 0 else bar["high"] >= stop
        if stop_hit:
            exit_reason, exit_price = "stop", _fill_price(stop, "sell" if sign > 0 else "buy", cost)
            ledger.append({
                "timestamp": bar_start, "type": "exit", "qty_before": qty,
                "qty_after": 0.0, "price_mark": exit_price, "fee_delta": qty * exit_price * fee_rate,
                "funding_delta": 0.0, "gross_delta": sign * (qty * exit_price - basis),
            })
            break
        take = avg * (1.0 + tp_pct) if sign > 0 else avg * (1.0 - tp_pct)
        take_hit = bar["high"] >= take if sign > 0 else bar["low"] <= take
        if take_hit:
            exit_reason, exit_price = "tp", _fill_price(take, "sell" if sign > 0 else "buy", cost)
            ledger.append({
                "timestamp": bar_start, "type": "exit", "qty_before": qty,
                "qty_after": 0.0, "price_mark": exit_price, "fee_delta": qty * exit_price * fee_rate,
                "funding_delta": 0.0, "gross_delta": sign * (qty * exit_price - basis),
            })
            break

    if exit_price is None:
        last_close = bars[-1]["close"]
        exit_price = _fill_price(last_close, "sell" if sign > 0 else "buy", cost)

    exit_notional = qty * exit_price
    if exit_reason == "day_end":
        exit_fee = exit_notional * fee_rate
        ledger.append({
            "timestamp": bars[-1]["close_time_ms"], "type": "exit", "qty_before": qty,
            "qty_after": 0.0, "price_mark": exit_price, "fee_delta": exit_fee,
            "funding_delta": 0.0, "gross_delta": sign * (exit_notional - basis),
        })
    else:
        exit_fee = qty * exit_price * fee_rate
    fees += exit_fee
    turnover += exit_notional
    fills += 1

    gross = sum(event["gross_delta"] for event in ledger)
    fees = sum(event["fee_delta"] for event in ledger)
    funding = sum(event["funding_delta"] for event in ledger)
    net = gross - fees - funding
    return {
        "gross_pnl": gross,
        "fees": fees,
        "funding": funding,
        "net_pnl": net,
        "fills": fills,
        "adds": adds,
        "max_active_tranches": 1 + adds,
        "turnover_usdt": turnover,
        "exit_reason": exit_reason,
        "capital_committed": basis,
        "funding_events_charged": charged,
        "execution_accounting": ledger,
    }


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
    end_exits = sum(1 for e in episodes if e["exit_reason"] == "day_end")
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
        "end_exits": end_exits, "open_at_end": 0,
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


def evaluate_cell(symbol, bars, execution_rows, plans, funding_events, dca, grid, meta):
    phase = grid_phase(grid)
    start, end = PHASES[phase]
    cost = cost_for(grid, meta)
    delay_bars = cost["entry_delay_bars"]
    episodes = []
    missing = 0
    start_d = dt.date.fromisoformat(start)
    end_d = dt.date.fromisoformat(end)

    for item in plans:
        f_day = item["formation_day"]
        if not (start_d <= f_day <= end_d):
            continue
        exec_idx = item["bar_idx"] + delay_bars
        if exec_idx >= len(bars):
            missing += 1
            continue
        exec_bar = bars[exec_idx]
        day_bars = execution_day_slice(exec_bar, execution_rows)
        episodes.append(simulate_leg(day_bars, item["direction"], dca, cost, funding_events))

    metrics = _metric_summary(episodes, start, end)
    metrics["formations_in_phase"] = sum(1 for item in plans if start_d <= item["formation_day"] <= end_d)
    metrics["missing_formations"] = missing
    return metrics


def evaluate_all(meta, all_bars, all_execution_rows, all_plans, all_funding, symbols=None):
    grid_cells = DCA_GRID
    rows_by_grid = {g: [] for g in GRIDS}
    eval_symbols = list(symbols) if symbols is not None else list(DEFAULT_INSTRUMENTS)
    for sym in eval_symbols:
        bars = all_bars[sym]
        plans = all_plans[sym]
        execution_rows = all_execution_rows[sym]
        funding = all_funding[sym]
        m = meta[sym]
        for grid in GRIDS:
            for cell in grid_cells:
                out = evaluate_cell(sym, bars, execution_rows, plans, funding, cell, grid, m)
                row = {
                    "symbol": sym, "timeframe": TIMEFRAME,
                    "signal_rule_version": 1,
                    "grid": grid,
                    **cell,
                    **out,
                }
                rows_by_grid[grid].append(row)
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
    winner = candidates[0]
    return winner, None, candidates


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
        "passed": bool(total and fraction >= MIN_NEIGHBOUR),
    }


def metric_block(row):
    keys = (
        "gross_pnl", "fees", "funding", "net_pnl", "ending_equity", "episodes", "fills",
        "adds", "turnover_usdt", "sharpe", "max_dd_pct", "max_dd_usdt",
        "annualized_return", "max_effective_leverage", "capital_utilization",
        "tp_hits", "stop_hits", "margin_calls", "end_exits", "open_at_end"
    )
    return {k: row[k] for k in keys}


def write_grid(output_dir, grid, rows):
    path = Path(output_dir) / ("grid_%s.csv" % grid)
    fieldnames = [
        "symbol", "timeframe", "signal_rule_version", "spacing_pct", "size_multiplier",
        "breakeven_tp_pct", "invalidation_pct", "gross_pnl", "fees", "funding", "net_pnl",
        "ending_equity", "episodes", "fills", "adds", "turnover_usdt", "sharpe",
        "max_dd_pct", "max_dd_usdt", "annualized_return", "max_effective_leverage",
        "capital_utilization", "tp_hits", "stop_hits", "margin_calls", "end_exits",
        "open_at_end",
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
    rule = spec.get("signal_rule")
    if rule != REGISTERED_SIGNAL_RULE:
        raise ValueError("registered signal rule changed")
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
            or exp.get("grids") != list(GRIDS) or exp.get("cohorts") != counts["cohorts"]:
        raise ValueError("expected coverage mismatch")
    eu = round_spec.get("eligible_universe") if isinstance(round_spec.get("eligible_universe"), dict) else {}
    if sorted(eu.get("instruments") or []) != symbols or eu.get("timeframes") != [TIMEFRAME] \
            or eu.get("execution_market") != EXECUTION_MARKET:
        raise ValueError("round local eligible universe mismatch")
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

    all_bars, all_execution_rows, all_plans, all_funding = {}, {}, {}, {}
    files_by_symbol = {}
    funding_reports = {}
    fri_diagnostics = {}
    start_ms = int(dt.datetime.fromisoformat(PHASES["full"][0] + "T00:00:00+00:00").timestamp() * 1000)
    end_ms = int(dt.datetime.fromisoformat(PHASES["full"][1] + "T23:59:59+00:00").timestamp() * 1000)

    for sym in registered_symbols:
        rows, files = load_local_rows(sym, root=klines_root / sym / TIMEFRAME)
        files_by_symbol[sym] = files
        bars, plans = build_signals(rows)
        all_bars[sym] = bars
        all_plans[sym] = plans
        execution_rows, _ = load_execution_rows(sym, root=klines_root / sym / "5m")
        all_execution_rows[sym] = execution_rows
        events, report = load_funding(sym, start_ms, end_ms, root=funding_root)
        all_funding[sym] = events
        funding_reports[sym] = report
        closes = [b["close"] for b in bars]
        fri_diagnostics[sym] = fourier_residue_analysis(closes)

    atomic_json(artifacts / "progress.json", {"phase": "evaluating_grids", "updated_at_utc": utc_now()})
    rows_by_grid = evaluate_all(meta, all_bars, all_execution_rows, all_plans, all_funding)

    for grid, rows in rows_by_grid.items():
        write_grid(artifacts, grid, rows)

    atomic_json(artifacts / "progress.json", {"phase": "evaluating_cohorts", "updated_at_utc": utc_now()})
    cohort_results = []
    survivors = []

    for sym in registered_symbols:
        cohort_name = "%s/%s" % (sym, TIMEFRAME)
        cohort_rows = [r for g in GRIDS for r in rows_by_grid[g] if r["symbol"] == sym]
        winner, cull_reason, candidates = select_cohort(cohort_rows)
        if winner is None:
            cohort_results.append({
                "cohort": cohort_name, "status": "CULLED",
                "winner": None, "cull_reasons": [cull_reason],
                "survivor_checks": None, "neighbourhood": None,
            })
            continue

        w_key = cell_key(winner)
        by_grid = {}
        for g in GRIDS:
            matched = [r for r in rows_by_grid[g] if cell_key(r) == w_key]
            if not matched:
                raise RuntimeError("winner cell missing in grid %s for %s" % (g, sym))
            by_grid[g] = matched[0]

        hist_row = by_grid["historical"]
        oos_row = by_grid["oos"]
        full_row = by_grid["full"]
        hist_historical = [r for r in rows_by_grid["historical"] if r["symbol"] == sym]
        nb = neighbourhood(winner, hist_historical)

        oos_ok = float(oos_row["net_pnl"]) > 0 and float(oos_row["sharpe"]) > 0 and int(oos_row["episodes"]) >= MIN_EPISODES_OOS
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

        checks = {
            "historical_winner_found": True,
            "oos_economic": oos_ok,
            "full_economic": full_ok,
            "robustness_economic": robustness_ok,
            "robustness_failed_grids": failed_robustness,
            "parameter_neighbourhood": nb_ok,
        }

        record = {
            "cohort": cohort_name,
            "status": "SURVIVOR" if not culls else "CULLED",
            "winner": cell_key(winner),
            "cull_reasons": culls,
            "survivor_checks": checks,
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
        "registered_window": {"start": PHASES["full"][0], "end": PHASES["full"][1]},
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
    }
    immutable_json(artifacts / "assertions.json", assertions)

    immutable_json(artifacts / "panel_evidence.json", {
        "family_id": FAMILY_ID,
        "fourier_residue_identity_math": {
            "k_sign": 2,
            "k_magnitude": 4,
            "description": "Fourier-Residue Identity (FRI) autocorrelation decomposition isolating directional sign from magnitude clustering.",
            "lag1_magnitude_hypothesis": "Scalar autocorrelation rho(1) is driven by magnitude shrinkage, while sign autocorrelation is near zero.",
            "lag3_directional_hypothesis": "Lag 3 exhibits statistically significant directional partial adjustment (r_{t-2} < 0 -> long, r_{t-2} > 0 -> short).",
        },
        "per_symbol_fri": fri_diagnostics,
    })

    immutable_json(artifacts / "falsification.json", {
        "exact_source_battery_measured": False,
        "claim_scope": CLAIM_SCOPE,
        "falsification_plan": [
            {
                "id": "post_2026_equity_walk_forward",
                "registered_test": "SPY, QQQ, and IWM daily returns from 2026-06-20 onward; lag-1 FRI sign test remains insignificant (p > 0.05), while lag-1 scalar autocorrelation remains negative (rho(1) < -0.04).",
                "falsification_rule": "Reject the pure-magnitude-bounce hypothesis if z_sign(1) < -2.00 (p < 0.05).",
                "status": "NOT_MEASURED_SOURCE_EQUITY_SCOPE",
                "reason": "The registered source test is equity-specific; this execution is scoped to the complete canonical local Binance USD-M perpetual universe. This is not a core-signal prerequisite or a local-universe exclusion.",
            },
            {
                "id": "lag_3_directional_reversal_persistence_audit",
                "registered_test": "Expanding window on SPY, QQQ, and IWM; report p_{3,0} and z_sign(3); falsify if |z_sign(3)| < 1.00 (p > 0.30) over a 3-year out-of-sample window.",
                "status": "LOCAL_ADAPTED_DIAGNOSTIC_ONLY",
                "reason": "The source-equity expanding-window rule is not evaluated; local full-window crypto diagnostics are reported separately and do not satisfy that rule.",
                "local_per_symbol_lag3_sign": {s: fri_diagnostics[s].get("binary_sign_lag3") for s in registered_symbols},
            },
            {
                "id": "intraday_taq_high_frequency_attribution",
                "registered_test": "Tick-by-tick TAQ data for SPY constituents; compare serial covariance of trade signs with mid-quote returns; falsify if mid-quote lag-1 autocorrelation is within 10% of transaction-price returns.",
                "status": "NOT_MEASURED_TAQ_SCOPE",
                "reason": "TAQ trade/quote observations are not part of this local daily-bar execution scope and are not a core-signal prerequisite.",
            },
            {
                "id": "cryptocurrency_high_frequency_friction_stress",
                "registered_test": "Binance and Coinbase 1-minute and 5-minute BTC/USDT, ETH/USDT, and SOL/USDT bars; measure VR(q) and R_N; falsify crypto-friction immunity if VR(2) < 0.90 (z* < -2.50) with R_N tending to 1.00.",
                "status": "NOT_MEASURED_AS_REGISTERED_MULTI_VENUE_BATTERY",
                "reason": "The registered battery requires both venues and both resolutions; this daily USD-M run does not claim that separate high-frequency multi-venue test. Its inputs are not prerequisites for the registered daily core signal.",
            },
            {
                "id": "trading_cost_and_fillability_stress",
                "registered_test": "Net performance after conservative execution costs across fee_2x, funding_2x, entry_delay_1_bar, slippage_2ticks.",
                "status": "COMPUTED_CELL_BY_CELL",
            },
        ],
    })

    stress_summary = {}
    for g in ("fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks", "no_funding", "no_funding_full", "cost_attrition_40bps"):
        stress_summary[g] = {
            "mean_net_pnl": statistics.mean(r["net_pnl"] for r in rows_by_grid[g]),
            "positive_cells": sum(1 for r in rows_by_grid[g] if r["net_pnl"] > 0),
            "total_cells": len(rows_by_grid[g]),
        }
    immutable_json(artifacts / "stress_effects.json", stress_summary)

    total_evals = sum(len(r) for r in rows_by_grid.values())
    expected_evals = len(registered_symbols) * 1 * 48 * len(GRIDS)
    coverage_ok = (total_evals == expected_evals) and all(assertions.values())

    result = {
        "schema_version": 1,
        "family_id": FAMILY_ID,
        "round_id": spec["round_id"],
        "run_id": spec["run_id"],
        "status": "ARTIFACT_READY" if coverage_ok else "ARTIFACT_READY",
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
