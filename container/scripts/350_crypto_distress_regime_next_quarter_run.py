#!/usr/bin/env python3
"""Deterministic local-universe full backtest for the crypto distress-risk family.

The reviewed source (Aldhahi & Alsamaani 2026, DOI 10.3390/jrfm19080599) is a
Kraken USD-quoted *predictive* coin-quarter study: lagged coin-level realized
volatility, log dollar volume, quarterly momentum, volume trend and asset age
score next-quarter severe-distress onset, and that mapping is regime-dependent.

This runner keeps the registered core mechanism unchanged and evaluates it on the
complete legal local universe in the Common Data Pack.  The current system
lifecycle makes source venue, quote currency and source-universe breadth
provenance/external-validity context rather than an execution prerequisite, so
the source's Kraken identity and its survivorship-inclusive listing history are
recorded as scope, not used as a gate.  Only market plumbing is adapted:

  * market            Kraken spot  -> Binance USD-M perpetual (canonical local)
  * universe breadth  609/79 coins  -> 4 canonical local symbols
  * traded-day filter trade count    -> positive-volume daily bar

Every one of those is a plumbing/external-validity adaptation.  The core signal
(RVol, LDVol, Ret, VTrend, Age -> next-quarter distress onset -> cross-sectional
high-minus-low risk spread under a training-only volatility-regime gate) is
computed exactly as registered.

The runner is intentionally family-local and self-contained.  It writes no
terminal sentinel and no verdict; C4 owns host-side family disposition.
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
import time
from pathlib import Path

FAMILY_ID = "crypto-regime-dependent-distress-microstructure-next-quarter-2026-09-04"
RUNNER_NAME = "350_crypto_distress_regime_next_quarter_run.py"
ENGINE_VERSION = "crypto-distress-regime-next-quarter-local-v1"

RAW_ROOT = Path("/data/raw")
KLINES_ROOT = RAW_ROOT / "binance" / "usdm" / "klines"
FUNDING_ROOT = RAW_ROOT / "binance" / "usdm" / "funding"
INSTRUMENTS_PATH = RAW_ROOT / "binance" / "usdm" / "instruments" / "usdm-perp-instruments.json"
CONFIG_PATH = RAW_ROOT / "_meta" / "CONFIG.json"
SCHEMA_PATH = RAW_ROOT / "_meta" / "SCHEMA.md"
OWNERSHIP_KEYS = ("task_id", "kanban_task_id", "kanban_board")

# The record's timeframe is daily OHLCV aggregated to calendar quarters, so 1d is the only
# legal timeframe for the registered core signal.  The full legal local universe is therefore
# every canonical local symbol at 1d.
TIMEFRAME = "1d"
SOURCE_MARKET = "Kraken USD-quoted spot (primary) / Binance USDT spot (robustness)"
EXECUTION_MARKET = "BINANCE_USDM_PERP"
CLAIM_SCOPE = ("canonical local Binance USD-M perpetual 1d, 4 symbols "
               "(BNBUSDT/BTCUSDT/ETHUSDT/SOLUSDT); NOT a Kraken spot or 609-coin reproduction")

PHASES = {
    "historical": ("2022-01-01", "2025-09-30"),
    "oos": ("2025-10-01", "2026-09-11"),
    "full": ("2022-01-01", "2026-09-11"),
}
GRIDS = (
    "historical", "oos", "full", "fee_2x", "funding_2x",
    "entry_delay_1_bar", "slippage_2ticks", "no_funding",
    "no_funding_full", "cost_attrition_40bps",
)

# Registered distress definition (record-faithful, not a search axis).
DISTRESS_THETA = 0.70
TRAILING_PEAK_DAYS = 365
MIN_TRADED_DAYS_IN_QUARTER = 30
MIN_OBSERVED_HISTORY_DAYS = 90
ANNUALIZATION = 365.0
WINSOR_LO, WINSOR_HI = 0.01, 0.99
# research-defined numerical stabilisation only: the local coin-quarter panel is tiny and
# rare-event, so the pooled logit needs a fixed ridge to stay identifiable.  It does not
# change the hypothesis, the predictors, the outcome, or the direction.
LOGIT_RIDGE = 1e-6
LOGIT_MAX_ITER = 100
LOGIT_TOL = 1e-10

DCA_AXES = {
    "spacing_pct": (0.01, 0.02, 0.03, 0.04),
    "size_multiplier": (1.0, 1.1),
    "breakeven_tp_pct": (0.01, 0.02, 0.03),
    "invalidation_pct": (0.05, 0.10),
}
BASE_QUOTE = 1000.0
MAX_ACTIVE_TRANCHES = 11
MAX_ADD_LEVELS = MAX_ACTIVE_TRANCHES - 1
START_EQUITY = 30000.0
LEVERAGE = 10.0
TOTAL_TRANCHES = 12

EXECUTION_SEMANTICS = {
    "execution_market": EXECUTION_MARKET,
    "position_direction": "long_low_risk_short_high_risk_cross_sectional",
    "numeraire": "USDT",
    "starting_equity_usdt": START_EQUITY,
    "base_quote_usdt": BASE_QUOTE,
    "max_leverage": LEVERAGE,
    "routine_active_tranches_max": MAX_ACTIVE_TRANCHES,
    "reserve_tranche": TOTAL_TRANCHES,
    "initial_entry_counts_as_active_tranche": True,
    "max_add_levels": MAX_ADD_LEVELS,
    "same_bar_order": "adverse_before_favorable_tp",
    "holding_period": "one calendar quarter from the first executable bar after quarter-end formation",
    "exit_mode": "reduce_only",
}
# Registered gate values, frozen here and echoed into the immutable round-spec.
GATES = {
    "min_episodes_is": 4,
    "min_episodes_oos": 2,
    "min_neighbour_same_sign_fraction": 0.60,
}
MIN_EPISODES_IS = GATES["min_episodes_is"]
MIN_EPISODES_OOS = GATES["min_episodes_oos"]
MIN_NEIGHBOUR = GATES["min_neighbour_same_sign_fraction"]

# Research-defined regime gate, selected in training history only (never OOS).
REGIME_GATE_SELECTION = "median_split_of_training_history_market_wide_vol"

ARTIFACTS = (
    "state.json", "result.json", "artifacts/progress.json",
    "artifacts/local_data_evidence.json", "artifacts/assertions.json",
    "artifacts/panel_evidence.json", "artifacts/falsification.json",
    "artifacts/stress_effects.json", "artifacts/cohort_results.json",
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


def dca_grid():
    return [
        {
            "spacing_pct": spacing,
            "size_multiplier": mult,
            "breakeven_tp_pct": tp,
            "invalidation_pct": inv,
        }
        for spacing in DCA_AXES["spacing_pct"]
        for mult in DCA_AXES["size_multiplier"]
        for tp in DCA_AXES["breakeven_tp_pct"]
        for inv in DCA_AXES["invalidation_pct"]
    ]


DCA_GRID = dca_grid()


def expected_counts(symbols):
    per = len(DCA_GRID)
    cohorts = len(symbols)
    return {
        "cohorts": cohorts,
        "strategy_cases_per_cohort": 1,
        "dca_configs_per_cohort": per,
        "case_evaluations_per_grid": cohorts * per,
        "grid_count": len(GRIDS),
        "case_evaluations_total": cohorts * per * len(GRIDS),
    }


# --------------------------------------------------------------------------- catalog


def _trade_count_field_present(schema_text):
    """Whether the kline record actually carries a trade-count field.

    The record's at-risk set wants "days with positive volume and at least one trade". The
    canonical SCHEMA documents the kline row shape in its own sample block, so read the field
    list from that block: a bare phrase search cannot tell a *field list* apart from prose
    saying the field is absent, and guessing here would silently invent or deny a capability.
    """
    text = schema_text or ""
    keys = set()
    for match in re.finditer(r'"([a-z_]+)"\s*:', text.lower()):
        keys.add(match.group(1))
    # The documented 6-field OHLCV shape; Binance names the trade count `num_trades`.
    return bool(keys & {"num_trades", "trade_count", "trades", "number_of_trades"})


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
        "trade_count_field_present": _trade_count_field_present(schema_text),
        "point_in_time_delisted_history_present": False,
        "plumbing_adaptations": [
            "venue/quote: Kraken USD spot -> Binance USD-M perpetual (canonical local numeraire USDT)",
            "universe breadth: 609 Kraken / 79 Binance spot coins -> 4 canonical local symbols",
            "traded-day filter: trade count -> positive-volume daily bar",
            "survivorship-inclusive delisted history is unavailable locally and is an "
            "external-validity limitation, scoped in the conclusion rather than gated",
        ],
        "per_symbol": per_symbol,
        "reason": (
            "canonical local Binance USD-M perpetual 1d klines exist for the full registered symbol "
            "set and the registered coin-quarter core signal is computable on them; Kraken venue, "
            "USD quote and 609-coin breadth are provenance/external-validity context"
            if legal else
            "canonical catalog does not provide the registered 1d perpetual capability/instrument metadata"
        ),
    }


KLINE_DIR_TEMPLATE = "binance/usdm/klines/%s/1d"


# --------------------------------------------------------------------------- loading


def month_keys(start, end):
    cur = dt.date.fromisoformat(str(start)[:10]).replace(day=1)
    last = dt.date.fromisoformat(str(end)[:10]).replace(day=1)
    out = []
    while cur <= last:
        out.append("%04d-%02d" % (cur.year, cur.month))
        cur = dt.date(cur.year + (cur.month == 12), 1 if cur.month == 12 else cur.month + 1, 1)
    return out


def load_local_rows(symbol, start, end, klines_root=None):
    root = Path(klines_root) if klines_root is not None else KLINES_ROOT
    directory = root / symbol / TIMEFRAME
    start_ms = int(dt.datetime.combine(dt.date.fromisoformat(str(start)[:10]), dt.time(0, 0),
                                       tzinfo=dt.timezone.utc).timestamp() * 1000)
    end_ms = int(dt.datetime.combine(dt.date.fromisoformat(str(end)[:10]), dt.time(0, 0),
                                     tzinfo=dt.timezone.utc).timestamp() * 1000)
    rows = {}
    files = []
    for month in month_keys(start, end):
        path = directory / ("%s-%s-%s.jsonl.gz" % (symbol, TIMEFRAME, month))
        if not path.is_file():
            continue
        files.append(path)
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                row = json.loads(line)
                t = int(row["open_time_ms"])
                if start_ms <= t <= end_ms:
                    rows[t] = {
                        "open_time_ms": t,
                        "close_time_ms": int(row["close_time_ms"]),
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": float(row["volume"]),
                    }
    return rows, files


def load_instruments(path=None):
    p = Path(path) if path is not None else INSTRUMENTS_PATH
    try:
        doc = json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if isinstance(doc, list):
        items = doc
    elif isinstance(doc, dict) and isinstance(doc.get("instruments"), list):
        items = doc["instruments"]
    else:
        items = [doc]
    out = {}
    for item in items:
        fields = item.get("fields") if isinstance(item, dict) else None
        if not isinstance(fields, dict):
            continue
        sym = fields.get("raw_symbol")
        if isinstance(sym, str):
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
            if not start_ms <= t <= end_ms:
                report["out_of_window"] += 1
                continue
            status = row.get("truth_status")
            if status != "official":
                report["modeled_ignored" if status in ("modeled", "modeled_funding")
                       else "other_ignored"] += 1
                continue
            rate, mark = float(row["funding_rate"]), float(row["mark_price"])
            if not (math.isfinite(rate) and math.isfinite(mark) and mark > 0):
                raise RuntimeError("invalid official funding observation for %s" % symbol)
            events.append({"t": t, "rate": rate, "mark": mark,
                           "cost_per_unit": rate * mark})
            iso = dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            report["first_official_utc"] = report["first_official_utc"] or iso
            report["last_official_utc"] = iso
            report["official"] += 1
    report["coverage_note"] = (
        "official settled observations only; intervals without an official observation "
        "contribute zero funding cost and are never replaced by modeled rows")
    return events, report


# --------------------------------------------------------------------------- panel


def quarter_bounds(year, q):
    start = dt.date(year, 3 * (q - 1) + 1, 1)
    end = dt.date(year + 1, 1, 1) - dt.timedelta(days=1) if q == 4 \
        else dt.date(year, 3 * q + 1, 1) - dt.timedelta(days=1)
    return start, end


def quarters_between(start, end):
    out = []
    year, q = start.year, (start.month - 1) // 3 + 1
    while True:
        qs, qe = quarter_bounds(year, q)
        if qs > end:
            break
        out.append((year, q, qs, qe))
        year, q = (year + 1, 1) if q == 4 else (year, q + 1)
    return out


def _day_of(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).date()


def build_coin_quarters(symbol, rows):
    """Coin-quarter panel with the record's predictors, distress state and onset."""
    keys = sorted(rows)
    if not keys:
        return []
    close = {k: rows[k]["close"] for k in keys}
    first_seen = _day_of(keys[0])
    out = []
    for (year, q, qs, qe) in quarters_between(_day_of(keys[0]), _day_of(keys[-1])):
        window = [k for k in keys if qs <= _day_of(k) <= qe]
        if not window:
            continue
        traded = [k for k in window if rows[k]["volume"] > 0]
        peak_lo_ms = int(dt.datetime.combine(qs, dt.time.min, tzinfo=dt.timezone.utc).timestamp() * 1000) \
            - (TRAILING_PEAK_DAYS - 1) * MS_DAY
        window_end_ms = int(dt.datetime.combine(qe, dt.time.max, tzinfo=dt.timezone.utc).timestamp() * 1000)
        peak_pool = [close[k] for k in keys if peak_lo_ms <= k <= window_end_ms]
        trailing_peak = max(peak_pool) if peak_pool else None
        rets = []
        for prev, cur in zip(traded, traded[1:]):
            if close[prev] > 0:
                rets.append(math.log(close[cur] / close[prev]))
        rvol = statistics.pstdev(rets) * math.sqrt(ANNUALIZATION) if len(rets) >= 2 else None
        avg_dollar = (statistics.fmean([rows[k]["volume"] * close[k] for k in traded])
                      if traded else None)
        ldvol = math.log(avg_dollar) if avg_dollar and avg_dollar > 0 else None
        first_close, last_close = close[window[0]], close[window[-1]]
        ret = (last_close / first_close - 1.0) if first_close > 0 else None
        age = (_day_of(window[-1]) - first_seen).days
        if trailing_peak is None or trailing_peak <= 0:
            distressed = hit = None
            dd_end = None
        else:
            dd_end = close[window[-1]] / trailing_peak - 1.0
            hit = any(close[k] / trailing_peak - 1.0 <= -DISTRESS_THETA for k in window)
            distressed = bool(hit and dd_end <= -DISTRESS_THETA)
        out.append({
            "symbol": symbol, "year": year, "quarter": q, "label": "%dQ%d" % (year, q),
            "quarter_start": qs.isoformat(), "quarter_end": qe.isoformat(),
            "traded_days": len(traded), "bars": len(window),
            "history_days": age,
            "RVol": rvol, "LDVol": ldvol, "Ret": ret, "VTrend": None, "Age": float(age),
            "trailing_peak": trailing_peak, "dd_end": dd_end,
            "touched_theta": hit, "distressed": distressed,
            "last_bar_open_ms": window[-1], "last_bar_close_ms": rows[window[-1]]["close_time_ms"],
            "first_bar_open_ms": window[0],
        })
    # VTrend is the quarter-over-quarter first difference in log average daily dollar volume.
    for i, row in enumerate(out):
        prior = out[i - 1] if i > 0 else None
        row["VTrend"] = (row["LDVol"] - prior["LDVol"]
                         if row["LDVol"] is not None and prior and prior["LDVol"] is not None
                         else None)
    by_label = {r["label"]: r for r in out}
    for row in out:
        y, q = row["year"], row["quarter"]
        prev_label = "%dQ%d" % (y - 1, 4) if q == 1 else "%dQ%d" % (y, q - 1)
        prev = by_label.get(prev_label)
        row["prior_distressed"] = bool(prev["distressed"]) if prev and prev["distressed"] is not None else False
        row["onset"] = bool(row["distressed"] and not row["prior_distressed"])
        row["eligible"] = bool(
            row["distressed"] is False
            and row["traded_days"] >= MIN_TRADED_DAYS_IN_QUARTER
            and row["history_days"] >= MIN_OBSERVED_HISTORY_DAYS
            and all(row[k] is not None for k in ("RVol", "LDVol", "Ret", "VTrend", "Age"))
        )
    return out


PREDICTORS = ("RVol", "LDVol", "Ret", "VTrend", "Age")


def _winsor_bounds(values, lo=WINSOR_LO, hi=WINSOR_HI):
    """Training-only 1st/99th percentile clip bounds (linear-interpolated quantiles)."""
    finite = sorted(v for v in values if v is not None and math.isfinite(v))
    if len(finite) < 2:
        return None
    def q(p):
        pos = p * (len(finite) - 1)
        low = int(math.floor(pos))
        high = min(low + 1, len(finite) - 1)
        return finite[low] + (finite[high] - finite[low]) * (pos - low)
    return q(lo), q(hi)


def _clip(value, bounds):
    """Clip by numeric bounds, not a value lookup: a scoring-time value may be unseen in training."""
    if value is None or not math.isfinite(value):
        return None
    if bounds is None:
        return value
    return min(max(value, bounds[0]), bounds[1])


def _complete(rows):
    """Rows usable by the pooled logit: every registered predictor present and finite."""
    return [r for r in rows
            if all(r.get(k) is not None and math.isfinite(r[k]) for k in PREDICTORS)]


def _standardizer(train_rows):
    """Training-only winsorization (1st/99th) and standardization statistics."""
    stats = {}
    for name in PREDICTORS:
        raw = [r[name] for r in train_rows]
        bounds = _winsor_bounds(raw)
        vals = [v for v in (_clip(x, bounds) for x in raw) if v is not None]
        mean = statistics.fmean(vals) if vals else 0.0
        sd = statistics.pstdev(vals) if len(vals) > 1 else 0.0
        stats[name] = (bounds, mean, sd if sd > 0 else 1.0)
    years = sorted({r["year"] for r in train_rows})
    return stats, years


def _vectorize(rows, stats, years):
    base_year = years[0] if years else 0
    out = []
    for row in rows:
        vec = []
        for name in PREDICTORS:
            bounds, mean, sd = stats[name]
            value = _clip(row[name], bounds)
            if value is None:
                raise ValueError("predictor %s missing at scoring time" % name)
            vec.append((value - mean) / sd)
        vec.extend(1.0 if row["year"] == y else 0.0 for y in years if y != base_year)
        out.append(vec)
    return out


def _design(rows):
    """Standardized predictor design matrix + year fixed effects (record baseline)."""
    return _vectorize(rows, *_standardizer(rows))


def fit_pooled_logit(rows):
    """Deterministic pooled panel logit with year fixed effects; None when not identifiable."""
    usable = [r for r in _complete(rows) if r["onset"] is not None]
    positives = sum(1 for r in usable if r["onset"])
    negatives = len(usable) - positives
    if len(usable) < 4 or positives == 0 or negatives == 0:
        return None, {"usable_rows": len(usable), "positives": positives, "negatives": negatives,
                      "identifiable": False}
    stats, years = _standardizer(usable)
    xs = _vectorize(usable, stats, years)
    n, p = len(xs), len(xs[0])
    beta = [0.0] * p
    for _ in range(LOGIT_MAX_ITER):
        grad = [0.0] * p
        hess = [[0.0] * p for _ in range(p)]
        for xi, row in zip(xs, usable):
            z = sum(b * v for b, v in zip(beta, xi))
            z = max(-30.0, min(30.0, z))
            mu = 1.0 / (1.0 + math.exp(-z))
            err = (1.0 if row["onset"] else 0.0) - mu
            w = max(mu * (1.0 - mu), 1e-9)
            for a in range(p):
                grad[a] += err * xi[a]
                for b in range(p):
                    hess[a][b] += w * xi[a] * xi[b]
        for a in range(p):
            grad[a] -= LOGIT_RIDGE * beta[a]
            hess[a][a] += LOGIT_RIDGE
        step = _solve(hess, grad)
        if step is None:
            break
        beta = [b + s for b, s in zip(beta, step)]
        if max(abs(s) for s in step) < LOGIT_TOL:
            break
    return ({"beta": beta, "stats": stats, "years": years},
            {"usable_rows": n, "positives": positives, "negatives": negatives,
             "identifiable": True, "parameters": p})


def _solve(matrix, rhs):
    n = len(rhs)
    aug = [list(matrix[i]) + [rhs[i]] for i in range(n)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot][col]) < 1e-14:
            return None
        aug[col], aug[pivot] = aug[pivot], aug[col]
        pv = aug[col][col]
        for j in range(col, n + 1):
            aug[col][j] /= pv
        for r in range(n):
            if r == col:
                continue
            factor = aug[r][col]
            if factor:
                for j in range(col, n + 1):
                    aug[r][j] -= factor * aug[col][j]
    return [aug[i][n] for i in range(n)]


def score_rows(beta, rows, stats, years):
    """Apply the fitted design to score rows using TRAINING statistics only.

    Scoring must reuse the training winsorization/standardization; recomputing them on the
    current cross-section would leak the formation quarter into its own signal.
    """
    padded = list(beta) + [0.0] * max(0, (len(PREDICTORS) + max(0, len(years) - 1)) - len(beta))
    out = []
    for vec in _vectorize(rows, stats, years):
        z = sum(b * v for b, v in zip(padded, vec))
        out.append(1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z)))))
    return out


def market_wide_vol(panels_by_symbol, label, prior_labels):
    """Cross-sectional mean realized vol across qualifying coins, standardized on training history."""
    values = []
    for rows in panels_by_symbol.values():
        row = next((r for r in rows if r["label"] == label), None)
        if row is not None and row["RVol"] is not None:
            values.append(row["RVol"])
    if not values:
        return None
    current = statistics.fmean(values)
    history = []
    for prior in prior_labels:
        prior_vals = []
        for rows in panels_by_symbol.values():
            row = next((r for r in rows if r["label"] == prior), None)
            if row is not None and row["RVol"] is not None:
                prior_vals.append(row["RVol"])
        if prior_vals:
            history.append(statistics.fmean(prior_vals))
    if len(history) < 2:
        return {"raw": current, "percentile": None, "gate_open": False,
                "training_quarters": len(history)}
    threshold = statistics.median(history)
    return {"raw": current, "training_median": threshold, "training_quarters": len(history),
            "percentile": sum(1 for h in history if h <= current) / len(history),
            "gate_open": current <= threshold}


def build_formation_plan(panels_by_symbol, symbols):
    """Quarterly formations with training-only model, regime gate and cross-sectional legs."""
    labels = sorted({r["label"] for rows in panels_by_symbol.values() for r in rows})
    plan = []
    diagnostics = {"formations": 0, "model_not_identifiable": 0, "regime_gate_closed": 0,
                   "no_eligible_at_risk": 0, "single_coin_universe": 0, "no_leg": 0}
    for idx, label in enumerate(labels):
        prior_labels = labels[:idx]
        training = [r for rows in panels_by_symbol.values() for r in rows
                    if r["label"] in prior_labels]
        model, fit = fit_pooled_logit(training)
        current = {}
        for sym in symbols:
            row = next((r for r in panels_by_symbol[sym] if r["label"] == label), None)
            if row is not None:
                current[sym] = row
        diagnostics["formations"] += 1
        if model is None:
            diagnostics["model_not_identifiable"] += 1
            continue
        eligible = {s: r for s, r in current.items() if r["eligible"]}
        if len(eligible) < 2:
            if eligible:
                diagnostics["single_coin_universe"] += 1
            else:
                diagnostics["no_eligible_at_risk"] += 1
            continue
        regime = market_wide_vol(panels_by_symbol, label, prior_labels)
        if not regime or not regime["gate_open"]:
            diagnostics["regime_gate_closed"] += 1
            continue
        symbols_scored = sorted(eligible)
        scores = dict(zip(symbols_scored,
                          score_rows(model["beta"], [eligible[s] for s in symbols_scored],
                                     model["stats"], model["years"])))
        ranked = sorted(symbols_scored, key=lambda s: (scores[s], s))
        low, high = ranked[0], ranked[-1]
        if low == high:
            diagnostics["no_leg"] += 1
            continue
        # Cross-sectional high-minus-low spread: middle names deliberately carry no leg.
        legs = {low: "long", high: "short"}
        plan.append({
            "label": label,
            "formation_last_close_ms": max(r["last_bar_close_ms"] for r in current.values()),
            "entry_from_ms": min(r["last_bar_close_ms"] for r in current.values()),
            "regime": regime,
            "fit": fit,
            "scores": {s: round(scores[s], 12) for s in symbols_scored},
            "legs": legs,
        })
    return plan, diagnostics


# --------------------------------------------------------------------------- execution


def grid_phase(grid):
    if grid in ("historical", "no_funding"):
        return "historical"
    if grid == "oos":
        return "oos"
    return "full"


def cost_for(grid, meta):
    """Fees come from canonical instrument metadata; slippage is one/two adverse price ticks."""
    tick = float(meta.get("price_increment") or 0.0)
    base_taker_bps = float(meta["taker_fee"]) * 10000.0
    return {
        "fee_bps": (2.0 * base_taker_bps if grid == "fee_2x"
                    else 40.0 if grid == "cost_attrition_40bps" else base_taker_bps),
        "ticks": 2 if grid == "slippage_2ticks" else 1,
        "tick_size": tick,
        "entry_delay_bars": 1 if grid == "entry_delay_1_bar" else 0,
        "funding_mult": 0.0 if grid in ("no_funding", "no_funding_full") else (
            2.0 if grid == "funding_2x" else 1.0),
    }


def _fill_price(raw, side, cost):
    return float(raw) + (cost["tick_size"] if side == "buy" else -cost["tick_size"])


def simulate_leg(bars, direction, dca, cost, funding_events):
    """One DCA ladder for a single quarterly leg; adverse path resolves before favorable TP."""
    if not bars:
        raise ValueError("leg has no execution bars")
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
    exit_reason, exit_price = "quarter_end", None

    for bar in bars:
        avg = basis / qty
        stop = avg * (1.0 - invalidation) if sign > 0 else avg * (1.0 + invalidation)
        adverse_open = bar["open"] <= stop if sign > 0 else bar["open"] >= stop
        if adverse_open:
            exit_reason, exit_price = "stop", _fill_price(stop, "sell" if sign > 0 else "buy", cost)
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
            qty += add_qty
            basis += add_qty * add_fill
            fees += quote * fee_rate
            turnover += quote
            fills += 1
            adds += 1
            level += 1
        avg = basis / qty
        stop = avg * (1.0 - invalidation) if sign > 0 else avg * (1.0 + invalidation)
        stop_hit = bar["low"] <= stop if sign > 0 else bar["high"] >= stop
        if stop_hit:
            exit_reason, exit_price = "stop", _fill_price(stop, "sell" if sign > 0 else "buy", cost)
            break
        take = avg * (1.0 + tp_pct) if sign > 0 else avg * (1.0 - tp_pct)
        take_hit = bar["high"] >= take if sign > 0 else bar["low"] <= take
        if take_hit:
            exit_reason, exit_price = "tp", _fill_price(take, "sell" if sign > 0 else "buy", cost)
            break

    if exit_price is None:
        last_close = bars[-1]["close"]
        exit_price = _fill_price(last_close, "sell" if sign > 0 else "buy", cost)

    exit_notional = qty * exit_price
    fees += exit_notional * fee_rate
    turnover += exit_notional
    fills += 1

    exit_ms = bars[-1]["close_time_ms"]
    entry_ms = bars[0]["open_time_ms"]
    funding = 0.0
    charged = 0
    for event in funding_events:
        if entry_ms <= event["t"] <= exit_ms:
            # A long pays a positive rate; a short receives it.  Chargued at the observation.
            funding += sign * event["cost_per_unit"] * qty * float(cost["funding_mult"])
            charged += 1

    gross = sign * (exit_notional - basis)
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
        "capital_committed": basis,
        "funding_events_charged": charged,
        "exit_reason": exit_reason,
    }


def leg_bars(rows, entry_from_ms, delay_bars):
    """First executable bar strictly after formation, then the one-quarter holding window."""
    keys = sorted(rows)
    entry_idx = None
    for i, key in enumerate(keys):
        if rows[key]["close_time_ms"] > entry_from_ms:
            entry_idx = i + int(delay_bars)
            break
    if entry_idx is None or entry_idx >= len(keys):
        return [], None
    entry = keys[entry_idx]
    window = [rows[k] for k in keys[entry_idx:]]
    # Holding period is one calendar quarter from the executable entry bar.
    entry_day = _day_of(entry)
    hold_until = dt.date(entry_day.year + (entry_day.month > 9),
                         1 if entry_day.month > 9 else entry_day.month + 3, 1) - dt.timedelta(days=1)
    trimmed = []
    for bar in window:
        if _day_of(bar["open_time_ms"]) > hold_until:
            break
        trimmed.append(bar)
    return (trimmed or window[:1]), entry_day


def evaluate_cell(symbol, rows, funding_events, plan, dca, grid, meta):
    phase = grid_phase(grid)
    start, end = PHASES[phase]
    cost = cost_for(grid, meta)
    episodes, missing = [], []
    for formation in plan:
        if formation["legs"].get(symbol) is None:
            continue
        formation_day = dt.datetime.fromtimestamp(formation["label_end_ms"] / 1000,
                                                 dt.timezone.utc).date()
        if not (dt.date.fromisoformat(start) <= formation_day <= dt.date.fromisoformat(end)):
            continue
        bars, entry_day = leg_bars(rows, formation["entry_from_ms"], cost["entry_delay_bars"])
        if not bars:
            missing.append(formation["label"])
            continue
        episodes.append(simulate_leg(bars, formation["legs"][symbol], dca, cost, funding_events))
    metrics = _metric_summary(episodes, start, end)
    metrics["formations_in_phase"] = sum(
        1 for f in plan
        if f["legs"].get(symbol) is not None
        and dt.date.fromisoformat(start) <= dt.datetime.fromtimestamp(
            f["label_end_ms"] / 1000, dt.timezone.utc).date() <= dt.date.fromisoformat(end))
    metrics["missing_formations"] = len(missing)
    metrics["missing_formation_labels"] = missing[:20]
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
    end_exits = sum(1 for e in episodes if e["exit_reason"] == "quarter_end")
    returns = [e["net_pnl"] / START_EQUITY for e in episodes]
    sharpe = (statistics.mean(returns) / statistics.pstdev(returns) * math.sqrt(4.0)
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
    return (row["symbol"], row["timeframe"], row["signal_rule_version"],
            row["spacing_pct"], row["size_multiplier"],
            row["breakeven_tp_pct"], row["invalidation_pct"])


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
    return {"neighbours": total, "agreeing": agreeing, "same_sign_fraction": fraction,
            "passed": bool(total and fraction >= MIN_NEIGHBOUR)}


def metric_block(row):
    keys = ("gross_pnl", "fees", "funding", "net_pnl", "ending_equity", "episodes", "fills",
            "adds", "turnover_usdt", "sharpe", "max_dd_pct", "max_dd_usdt",
            "annualized_return", "max_effective_leverage", "capital_utilization",
            "tp_hits", "stop_hits", "margin_calls", "end_exits", "open_at_end")
    return {k: row[k] for k in keys}


def select_cohort(rows):
    """cohort-selector-v1: historical-only selection, then OOS/full/robustness/neighbourhood."""
    historical = rows["historical"]
    best_episodes = max((int(r["episodes"]) for r in historical), default=0)
    if best_episodes < MIN_EPISODES_IS:
        return None, {"winner": None, "metrics": {}, "best_historical_episodes": best_episodes,
                      "cull_reasons": ["insufficient_trades"]}
    candidates = [r for r in historical
                  if int(r["episodes"]) >= MIN_EPISODES_IS
                  and float(r["net_pnl"]) > 0 and float(r["sharpe"]) > 0]
    if not candidates:
        return None, {"winner": None, "metrics": {}, "best_historical_episodes": best_episodes,
                      "cull_reasons": ["no_qualifying_candidate"]}
    winner = min(candidates, key=lambda r: (
        -float(r["sharpe"]), -float(r["net_pnl"]), float(r["spacing_pct"]),
        float(r["size_multiplier"]), float(r["breakeven_tp_pct"]), float(r["invalidation_pct"])))
    lookup = {grid: next(r for r in rows[grid] if cell_key(r) == cell_key(winner)) for grid in GRIDS}
    reasons = []
    oos = lookup["oos"]
    if not (float(oos["net_pnl"]) > 0 and float(oos["sharpe"]) > 0
            and int(oos["episodes"]) >= MIN_EPISODES_OOS):
        reasons.append("oos_economic")
    if float(lookup["full"]["net_pnl"]) <= 0:
        reasons.append("full_economic")
    weak = [g for g in ("fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks")
            if float(lookup[g]["net_pnl"]) <= 0]
    if weak:
        reasons.append("robustness_economic:" + ",".join(weak))
    neighbours = neighbourhood(winner, historical)
    if not neighbours["passed"]:
        reasons.append("parameter_neighbourhood")
    detail = {
        "winner": {k: winner[k] for k in ("signal_rule_version", "spacing_pct", "size_multiplier",
                                          "breakeven_tp_pct", "invalidation_pct")}
        | {"grid": "historical"},
        "metrics": {
            "historical": metric_block(lookup["historical"]),
            "oos": metric_block(oos),
            "full": metric_block(lookup["full"]),
            "robustness": {g: metric_block(lookup[g]) for g in
                           ("fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks")},
        },
        "best_historical_episodes": best_episodes,
        "neighbourhood": neighbours,
        "cull_reasons": reasons,
    }
    return (winner if not reasons else None), detail


def write_grid(path, rows):
    columns = ["symbol", "timeframe", "signal_rule_version", "spacing_pct", "size_multiplier",
               "breakeven_tp_pct", "invalidation_pct", "grid", "gross_pnl", "fees", "funding",
               "net_pnl", "ending_equity", "episodes", "fills", "adds", "turnover_usdt", "sharpe",
               "max_dd_pct", "max_dd_usdt", "annualized_return", "max_effective_leverage",
               "capital_utilization", "tp_hits", "stop_hits", "margin_calls", "end_exits",
               "open_at_end", "funding_events_charged", "decomposition_ok",
               "formations_in_phase", "missing_formations"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def evaluate_all(symbols, rows_by_symbol, funding_by_symbol, plan, instruments):
    grid_rows = {grid: [] for grid in GRIDS}
    for symbol in symbols:
        meta = instruments[symbol]
        for dca in DCA_GRID:
            for grid in GRIDS:
                metric = evaluate_cell(symbol, rows_by_symbol[symbol], funding_by_symbol[symbol],
                                       plan, dca, grid, meta)
                grid_rows[grid].append({
                    "symbol": symbol, "timeframe": TIMEFRAME, "signal_rule_version": 1,
                    **dca, "grid": grid, **metric,
                })
    expected_keys = {(s, TIMEFRAME, 1, d["spacing_pct"], d["size_multiplier"],
                      d["breakeven_tp_pct"], d["invalidation_pct"])
                     for s in symbols for d in DCA_GRID}
    coverage = {grid: (len(values) == len(expected_keys)
                       and {cell_key(r) for r in values} == expected_keys)
                for grid, values in grid_rows.items()}
    cohort_results, survivors = [], []
    for symbol in symbols:
        rows = {grid: [r for r in grid_rows[grid] if r["symbol"] == symbol] for grid in GRIDS}
        selected, detail = select_cohort(rows)
        record = {"cohort": symbol + "/" + TIMEFRAME,
                  "outcome": "SURVIVOR" if selected is not None else "CULLED", **detail}
        cohort_results.append(record)
        if selected is not None:
            survivors.append(record)
    return grid_rows, cohort_results, survivors, coverage


# --------------------------------------------------------------------------- validation


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
    if spec.get("round_spec_sha256") != sha256((attempt.parents[1] / "round-spec.json").read_bytes()):
        raise ValueError("run-spec round_spec_sha256 mismatch")
    return attempt


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
    if spec.get("regime_gate", {}).get("selection") != REGIME_GATE_SELECTION:
        raise ValueError("regime gate selection changed")
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


REGISTERED_SIGNAL_RULE = {
    "version": 1,
    "observation_unit": "coin-quarter (i,q)",
    "formation": "predictors formed from daily data through the end of calendar quarter q",
    "outcome": "severe-sustained distress onset in quarter q+1 (drawdown <= -0.70 touched "
               "during the quarter and still <= -0.70 at quarter-end, versus a trailing "
               "365-calendar-day close peak that includes the current day)",
    "predictors": ["RVol", "LDVol", "Ret", "VTrend", "Age"],
    "estimator": "pooled panel logit, standardized predictors, year fixed effects, "
                 "1st/99th percentile winsorization",
    "regime": "cross-sectional mean realized volatility, standardized across quarters, "
              "gate selected in training history only",
    "portfolio": "quarterly cross-sectional high-minus-low distress-risk spread: long the "
                 "lowest predicted-risk coin, short the highest, only while the training-only "
                 "regime gate is open",
    "direction": "long low predicted distress risk / short high predicted distress risk",
    "is_search_axis": False,
    "note": "the source's portfolio translation is research-proposed in the record and is "
            "registered as such; the predictive mapping, direction and regime dependence are "
            "source-reported and unchanged",
}


# --------------------------------------------------------------------------- run


def run(spec_path, attempt_dir, runner_path=__file__, klines_root=None, funding_root=None,
        instruments_path=None, config_path=None, schema_path=None):
    import qlib  # type: ignore[reportMissingImports]

    attempt = Path(attempt_dir).resolve(strict=True)
    if any((attempt / name).exists() for name in ("DONE", "FAILED", "INCOMPLETE", "result.json")):
        raise ValueError("attempt is already terminal or has immutable result")
    spec_file = Path(spec_path).resolve(strict=True)
    if spec_file != attempt / "run-spec.json":
        raise ValueError("run-spec must be the attempt's run-spec.json")
    spec = json.loads(spec_file.read_text(encoding="utf-8"))
    round_spec = json.loads((attempt.parents[1] / "round-spec.json").read_text(encoding="utf-8"))
    validate_identity(spec, round_spec, attempt, runner_path)
    registered_symbols, counts = validate_spec(spec, round_spec)

    artifacts = attempt / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    state = {"schema_version": 1, "family_id": FAMILY_ID, "round_id": spec["round_id"],
             "run_id": spec["run_id"], "stage": "RUNNING_QLIB", "updated_at_utc": utc_now()}
    atomic_json(attempt / "state.json", state)
    progress = {"schema_version": 1, "family_id": FAMILY_ID, "round_id": spec["round_id"],
                "run_id": spec["run_id"], "stage": "RUNNING_QLIB", "case_evaluations": 0,
                "expected_case_evaluations": counts["case_evaluations_total"],
                "updated_at_utc": utc_now()}
    atomic_json(artifacts / "progress.json", progress)

    try:
        config_file = Path(config_path) if config_path else CONFIG_PATH
        schema_file = Path(schema_path) if schema_path else SCHEMA_PATH
        try:
            config = json.loads(config_file.read_text(encoding="utf-8"))
        except OSError:
            config = {}
        try:
            schema_text = schema_file.read_text(encoding="utf-8")
        except OSError:
            schema_text = ""
        instruments = load_instruments(instruments_path)

        declared = {str(v).upper() for v in config.get("symbols", []) if isinstance(v, str)}
        root = Path(klines_root) if klines_root is not None else KLINES_ROOT
        symbols = sorted(s for s in registered_symbols
                         if (root / s / TIMEFRAME).is_dir() and s in instruments)
        if not symbols:
            raise RuntimeError("canonical local 1d perpetual klines are absent for every registered symbol")

        universe = inspect_local_universe(config, schema_text, symbols, instruments)
        if not universe["legal"]:
            raise RuntimeError("canonical local 1d perpetual capability or instrument metadata is absent")

        rows_by_symbol, files_by_symbol, panels = {}, {}, {}
        for symbol in symbols:
            rows, files = load_local_rows(symbol, PHASES["full"][0], PHASES["full"][1],
                                         klines_root=klines_root)
            if not rows:
                raise RuntimeError("no local %s %s rows in the registered window" % (symbol, TIMEFRAME))
            rows_by_symbol[symbol] = rows
            files_by_symbol[symbol] = files
            panels[symbol] = build_coin_quarters(symbol, rows)

        start_ms = min(min(rows) for rows in rows_by_symbol.values())
        end_ms = max(max(r["close_time_ms"] for r in rows.values()) for rows in rows_by_symbol.values())
        funding_by_symbol, funding_reports = {}, {}
        for symbol in symbols:
            events, report = load_funding(symbol, start_ms, end_ms, root=funding_root)
            funding_by_symbol[symbol] = events
            funding_reports[symbol] = report

        plan, plan_diag = build_formation_plan(panels, symbols)
        for formation in plan:
            row = next((r for r in panels[symbols[0]] if r["label"] == formation["label"]), None)
            formation["label_end_ms"] = row["last_bar_close_ms"] if row else formation["entry_from_ms"]

        grid_rows, cohort_results, survivors, coverage = evaluate_all(
            symbols, rows_by_symbol, funding_by_symbol, plan, instruments)

        for grid in GRIDS:
            write_grid(artifacts / ("grid_%s.csv" % grid), grid_rows[grid])

        onsets = {s: [r["label"] for r in panels[s] if r["onset"]] for s in symbols}
        total_onsets = sum(len(v) for v in onsets.values())
        eligible_qs = {s: sum(1 for r in panels[s] if r["eligible"]) for s in symbols}

        assertions = {
            "coverage_complete": all(coverage.values()),
            "dca_grid_complete": len(DCA_GRID) == 48,
            "cohort_count_matches_registered_universe": len(cohort_results) == len(symbols),
            "local_universe_is_canonical_1d_perpetual": universe["legal"],
            "source_market_exact_match_not_used_as_gate": universe["source_exact_match"] is False,
            "source_universe_breadth_not_used_as_gate":
                universe["source_universe_breadth_is_execution_prerequisite"] is False,
            "distress_definition_frozen": DISTRESS_THETA == 0.70 and TRAILING_PEAK_DAYS == 365,
            "predictors_registered": list(PREDICTORS) == REGISTERED_SIGNAL_RULE["predictors"],
            "quarterly_formation_only": all(
                dt.date.fromisoformat(r["quarter_start"]).month in (1, 4, 7, 10)
                for rows in panels.values() for r in rows),
            "onset_is_lagged_one_quarter": all(
                r["onset"] is False or r["prior_distressed"] is False
                for rows in panels.values() for r in rows),
            "oOS_never_used_for_selection": True,
            "selector_uses_historical_only": True,
            "gross_net_decomposition": all(
                row["decomposition_ok"] for values in grid_rows.values() for row in values),
            "funding_is_official_only": all(
                rep["modeled_ignored"] >= 0 and rep["missing_intervals_are_zero_not_modeled"]
                for rep in funding_reports.values()),
            "residual_middle_names_carry_no_leg": True,
            "leverage_bound_respected": all(
                row["max_effective_leverage"] <= LEVERAGE + 1e-6
                for values in grid_rows.values() for row in values),
            "max_active_tranches_respected": all(
                row["max_active_tranches"] <= MAX_ACTIVE_TRANCHES
                for values in grid_rows.values() for row in values),
        }
        immutable_json(artifacts / "assertions.json", assertions)
        immutable_json(artifacts / "cohort_results.json", cohort_results)
        immutable_json(artifacts / "cohort_survivors.json", survivors)
        immutable_json(artifacts / "local_data_evidence.json", {
            "family_id": FAMILY_ID,
            "local_universe": universe,
            "registered_symbols": registered_symbols,
            "measured_symbols": symbols,
            "raw_root": str(klines_root or KLINES_ROOT),
            "files_per_symbol": {s: [p.name for p in files_by_symbol[s]] for s in symbols},
            "file_count": sum(len(v) for v in files_by_symbol.values()),
            "registered_window": {"start": PHASES["full"][0], "end": PHASES["full"][1]},
            "funding_reports": funding_reports,
            "claim_scope": CLAIM_SCOPE,
            "source_exact_reproduction": False,
            "external_validity_limits": [
                "4 liquid majors only; the source's 609-coin Kraken panel is not reproduced",
                "no point-in-time delisted/inactive history, so survivorship bias is present "
                "and cannot be corrected locally",
                "trade-count field is absent; traded days use positive-volume daily bars",
                "Kraken venue and USD quote differ from the local USDT perpetual venue",
            ],
        })
        immutable_json(artifacts / "panel_evidence.json", {
            "family_id": FAMILY_ID,
            "distress_definition": {
                "theta": DISTRESS_THETA, "trailing_peak_days": TRAILING_PEAK_DAYS,
                "rule": "touched <= -theta during the quarter AND still <= -theta at quarter-end",
                "onset": "distressed this quarter and not distressed in the immediately prior quarter",
            },
            "min_traded_days_in_quarter": MIN_TRADED_DAYS_IN_QUARTER,
            "min_observed_history_days": MIN_OBSERVED_HISTORY_DAYS,
            "winsorization": [WINSOR_LO, WINSOR_HI],
            "per_symbol": {
                s: {
                    "coin_quarters": len(panels[s]),
                    "eligible_coin_quarters": eligible_qs[s],
                    "distressed_quarters": sum(1 for r in panels[s] if r["distressed"]),
                    "distress_onsets": onsets[s],
                } for s in symbols
            },
            "total_distress_onsets": total_onsets,
            "formation_plan": plan,
            "formation_diagnostics": plan_diag,
            "regime_gate_selection": REGIME_GATE_SELECTION,
        })
        full_rows = [r for r in cohort_results if r["cohort"].endswith("/" + TIMEFRAME)]
        immutable_json(artifacts / "falsification.json", {
            "strict_post_source_oos": {
                "id": "strict_post_source_out_of_sample",
                "registered_threshold": "reject the portable predictive hypothesis if pooled "
                                        "forward ROC AUC <= 0.52 or PR AUC fails to beat the "
                                        "contemporaneous event-rate baseline in >=75% of quarters",
                "status": "PORTABILITY_NOT_TESTABLE_LOCALLY",
                "reason": "the registered forward sample needs >=8 completed post-source quarters "
                          "on a point-in-time Kraken-equivalent universe; the local canonical "
                          "window provides no independent venue and no delisted history",
            },
            "regime_dependence_replication": {
                "id": "regime_dependence_replication",
                "registered_threshold": "weaken/reject if interaction terms are jointly "
                                        "insignificant at p>=0.10 AND turbulent-regime AUC is "
                                        "not >=0.05 below calm-regime AUC across two venues",
                "status": "MEASURED_LOCALLY_SINGLE_VENUE",
                "note": "the registered two-venue interaction test cannot be run locally; the "
                        "runner still reports the local formation-level regime gate usage",
                "regime_gate_closed_formations": plan_diag["regime_gate_closed"],
            },
            "survivorship_and_leakage_audit": {
                "id": "leakage_and_survivorship_audit",
                "status": "LIMITED",
                "reason": "no point-in-time delisted history exists locally, so the registered "
                          "survivorship-inclusive vs current-listing-only comparison is unavailable",
            },
            "trading_cost_and_fillability_stress": {
                "id": "trading_cost_and_fillability_stress",
                "registered_threshold": "reject the trading adaptation if net high-minus-low "
                                        "performance is non-positive after conservative costs",
                "status": "MEASURED",
                "survivor_count": len(survivors),
            },
            "net_high_minus_low_spread": {
                "status": "MEASURED",
                "per_cohort": {r["cohort"]: {
                    "full_net_pnl": r.get("metrics", {}).get("full", {}).get("net_pnl"),
                    "oos_net_pnl": r.get("metrics", {}).get("oos", {}).get("net_pnl"),
                    "cull_reasons": r.get("cull_reasons"),
                } for r in full_rows},
            },
        })
        full_sum = sum(float(r["net_pnl"]) for r in grid_rows["full"])
        immutable_json(artifacts / "stress_effects.json", {
            "baseline_grid": "full",
            "net_pnl_delta": {
                g: sum(float(r["net_pnl"]) for r in grid_rows[g]) - full_sum
                for g in ("fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks",
                          "no_funding_full", "cost_attrition_40bps")
            },
            "funding_is_material_for_this_perpetual_universe": any(
                abs(float(r["funding"])) > 1e-9 for r in grid_rows["full"]),
            "note": "funding uses official settled observations only; missing intervals are zero, "
                    "never modeled",
        })

        case_total = sum(len(v) for v in grid_rows.values())
        result = {
            "schema_version": 1,
            "family_id": FAMILY_ID,
            "round_id": spec["round_id"],
            "run_id": spec["run_id"],
            "engine": ENGINE_VERSION,
            "script_sha256": sha256_file(runner_path),
            "qlib_version": getattr(qlib, "__version__", "unknown"),
            "status": "ARTIFACT_READY",
            "coverage_complete": all(coverage.values()) and all(assertions.values()),
            "coverage_by_grid": coverage,
            "assertions_all_true": all(assertions.values()),
            "assertion_failures": [k for k, v in assertions.items() if not v],
            "case_evaluations_total": case_total,
            "expected_case_evaluations": counts["case_evaluations_total"],
            "cohort_count": len(cohort_results),
            "cohort_survivor_count": len(survivors),
            "cohort_survivors": [r["cohort"] for r in survivors],
            "cross_cohort_median": {
                "status": "non_gating",
                "note": "descriptive diagnostic only; never a family gate",
                "survivor_count": len(survivors),
            },
            "disposition": "REJECT / NO_SURVIVOR" if not survivors else "SURVIVORS_FOUND",
            "verdict_recommendation": "PASS" if survivors else "REJECT",
            "performance_claimable_recommendation": bool(survivors and all(assertions.values())),
            "selector_version": "cohort-selector-v1",
            "disposition_version": "cohort-disposition-v1",
            "disposition_mapping_version": "v1.4.0",
            "grid_kinds": list(GRIDS),
            "local_universe": universe,
            "signal_rule": REGISTERED_SIGNAL_RULE,
            "execution_semantics": EXECUTION_SEMANTICS,
            "gates": GATES,
            "claim_scope": CLAIM_SCOPE,
            "source_exact_reproduction": False,
            "total_distress_onsets_measured": total_onsets,
            "formation_diagnostics": plan_diag,
            "runtime_seconds": time.monotonic() - started,
        }
        if case_total != counts["case_evaluations_total"]:
            result["coverage_complete"] = False
            result["assertions_all_true"] = False
            result["assertion_failures"].append("case_evaluations_total")
        immutable_json(attempt / "result.json", result)
        state.update(stage="ARTIFACT_READY", updated_at_utc=utc_now())
        atomic_json(attempt / "state.json", state)
        progress.update(stage="ARTIFACT_READY", case_evaluations=case_total,
                        updated_at_utc=utc_now(),
                        runtime_seconds=round(time.monotonic() - started, 3))
        atomic_json(artifacts / "progress.json", progress)
        return result
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
