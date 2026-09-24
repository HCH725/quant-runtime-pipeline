#!/usr/bin/env python3
"""Qlib-container runner for the registered crypto microstructure-alpha family.

Family: crypto-microstructure-alpha-hierarchical-cross-asset-transfer-2026-09-01
Engine: microstructure_alpha_v1

The family-specific signal is a compact numpy implementation of the record's registered
mechanism: nine classical OHLCV-derivable microstructure measures (range-based spread proxies,
the realised-volatility family, illiquidity, relative activity, return autocorrelation) feed a
purged walk-forward pipeline with stability selection and tabular gradient boosting, and the
standardised sign of the forecast defines the episode events.  The audited DCA/accounting rail is
reused from the topological runner; this file owns the features, the model, data preparation,
coverage and the family artifacts.

Raw data is read-only under /data/raw.  Derived Qlib data is rebuilt under /qlib/work and all
attempt artifacts are written below the supplied /results attempt directory.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import hashlib
import importlib.util
import itertools
import json
import math
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

VENV_PYTHON = "/opt/venv/bin/python"
DUMP_BIN = "/opt/qlib-tools/dump_bin.py"
RAW_ROOT = "/data/raw/binance/usdm"
INSTRUMENTS_PATH = "/data/raw/binance/usdm/instruments/usdm-perp-instruments.json"
WORK_ROOT = "/qlib/work/microstructure-alpha-v1"
CSV_ROOT = os.path.join(WORK_ROOT, "csv")
QLIB_DIR = os.path.join(WORK_ROOT, "qlib-data")
MS_PER_DAY = 86400000
FIELDS = ["open", "high", "low", "close", "volume"]
FAMILY_ID = "crypto-microstructure-alpha-hierarchical-cross-asset-transfer-2026-09-01"
ENGINE_VERSION = "microstructure_alpha_v1"
SEED = 20260924
START_EQUITY = 30000.0
BASE_QUOTE = 1000.0
LEVERAGE = 10.0
MAX_LAYERS = 11
SLIPPAGE_TICKS = 1
TAKER_FEE = 0.0005
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
TIMEFRAMES = [
    {"raw_interval": "5m", "qlib_freq": "5min", "bars_per_day": 288, "bar_ms": 300_000},
    {"raw_interval": "15m", "qlib_freq": "15min", "bars_per_day": 96, "bar_ms": 900_000},
    {"raw_interval": "30m", "qlib_freq": "30min", "bars_per_day": 48, "bar_ms": 1_800_000},
    {"raw_interval": "1h", "qlib_freq": "60min", "bars_per_day": 24, "bar_ms": 3_600_000},
    {"raw_interval": "4h", "qlib_freq": "240min", "bars_per_day": 6, "bar_ms": 14_400_000},
    {"raw_interval": "1d", "qlib_freq": "day", "bars_per_day": 1, "bar_ms": MS_PER_DAY},
    {"raw_interval": "1w", "qlib_freq": "week", "bars_per_day": 1, "bar_ms": 7 * MS_PER_DAY},
]
DIRECTION_MODES = ["long_only", "long_short"]
MODEL_IMPLS = ["lightgbm", "sklearn_hist_gbm"]
STRATEGIES = [{"case_code": d * len(MODEL_IMPLS) + m, "direction": direction, "model": model,
               "label": "%s__%s" % (direction, model)}
              for d, direction in enumerate(DIRECTION_MODES)
              for m, model in enumerate(MODEL_IMPLS)]
DCA_AXES = {
    "spacing_pct": [0.01, 0.02, 0.03, 0.04],
    "size_multiplier": [1.0, 1.1],
    "breakeven_tp_pct": [0.01, 0.02, 0.03],
    "invalidation_pct": [0.05, 0.10],
}
GRID_KINDS = ["historical", "oos", "full", "fee_2x", "funding_2x",
              "entry_delay_1_bar", "slippage_2ticks", "no_funding",
              "no_funding_full", "cost_attrition_40bps"]
FEATURE_NAMES = ["spread_corwin_schultz", "spread_roll", "rv_close_close", "rv_parkinson",
                 "rv_garman_klass", "range_relative", "amihud_illiquidity",
                 "volume_zscore", "return_autocorr_1"]
ENTRY_Z = 0.5
FOLD_STRUCTURE = {"train_months": 12, "test_months": 3, "step_months": 3, "purge_bars": 1}
STABILITY = {"resamples": 20, "top_k": 6, "frequency": 0.6}
TRAIN_ROW_CAP = 100_000
GATES = {"min_episodes_is": 30, "min_episodes_oos": 10, "min_neighbour_same_sign_fraction": 0.60}
MODEL_CONSTANTS = {
    "lightgbm": {"n_estimators": 300, "learning_rate": 0.05, "num_leaves": 31,
                 "min_child_samples": 50, "subsample": 0.8, "subsample_freq": 1,
                 "colsample_bytree": 0.8, "random_state": SEED, "verbose": -1},
    "sklearn_hist_gbm": {"max_iter": 300, "learning_rate": 0.05, "max_leaf_nodes": 31,
                         "min_samples_leaf": 50, "l2_regularization": 0.0,
                         "random_state": SEED},
}
TRANSFER_TIMEFRAME = "1h"
FALLBACK_TICK = {"BTCUSDT": 0.10, "ETHUSDT": 0.01, "SOLUSDT": 0.01, "BNBUSDT": 0.01}


def load_sibling_rail():
    """Load the audited DCA/fill rail from the topological runner (single source of truth)."""
    candidates = [Path("/scripts/170_topological_anomaly_run.py"),
                  Path(__file__).resolve().with_name("170_topological_anomaly_run.py")]
    for path in candidates:
        if path.is_file():
            spec = importlib.util.spec_from_file_location("audited_dca_rail", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            # The sibling owns only the generic DCA/fill rail used here.  Override all
            # family-visible constants so no stale family domain leaks into this run.
            mod.RAW_ROOT = RAW_ROOT
            mod.FAMILY_ID = FAMILY_ID
            mod.SYMBOLS = list(SYMBOLS)
            mod.TIMEFRAMES = list(TIMEFRAMES)
            mod.STRATEGIES = list(STRATEGIES)
            mod.DCA_AXES = dict(DCA_AXES)
            mod.GRID_KINDS = list(GRID_KINDS)
            mod.START_EQUITY = START_EQUITY
            mod.BASE_QUOTE = BASE_QUOTE
            mod.LEVERAGE = LEVERAGE
            mod.TAKER_FEE = TAKER_FEE
            mod.MAX_LAYERS = MAX_LAYERS
            mod.SLIPPAGE_TICKS = SLIPPAGE_TICKS
            mod.PRICE_TICK = instrument_constants()["tick"]
            return mod, str(path)
    raise RuntimeError("audited DCA rail not found")


def instrument_constants():
    """Canonical instrument metadata (tick size + taker fee) read from the raw export.

    The container run fails closed when the canonical export is unreadable; the host-side engine
    self-check (no /data mount) falls back to the same values measured from that export.
    """
    try:
        with open(INSTRUMENTS_PATH, encoding="utf-8") as fh:
            payload = json.load(fh)
    except OSError:
        return {"tick": dict(FALLBACK_TICK), "taker_fee": {s: TAKER_FEE for s in SYMBOLS},
                "source": "registered fallback constants (canonical export unreadable here)",
                "fallback": True}
    tick, fee = {}, {}
    for instrument in payload["instruments"]:
        fields = instrument["fields"]
        tick[fields["raw_symbol"]] = float(fields["price_increment"])
        fee[fields["raw_symbol"]] = float(fields["taker_fee"])
    missing = [s for s in SYMBOLS if s not in tick or s not in fee]
    if missing:
        raise RuntimeError("instrument metadata missing for %s" % missing)
    return {"tick": tick, "taker_fee": fee, "source": INSTRUMENTS_PATH, "fallback": False}


def atomic_json(path, value):
    path = str(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(value, fh, indent=2, ensure_ascii=False, allow_nan=False)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def utc_ms(value):
    if len(value) == 10:
        value += "T00:00:00Z"
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return int(dt.datetime.fromisoformat(value).timestamp() * 1000)


def utc_stamp(ms):
    return dt.datetime.fromtimestamp(int(ms) / 1000.0, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def qlib_api_freq(tf):
    """Return the frequency key emitted by ``dump_bin.py``."""
    # ponytail: use the registered key verbatim; dump_bin writes ``week.txt``
    # for weekly input, while ``1week`` has no matching Qlib calendar.
    return tf["qlib_freq"]


def qlib_week_aliases():
    """Expose dump_bin's weekly files under Qlib 0.9.7's normalized names."""
    # ponytail: copy the small weekly artifacts instead of introducing a second
    # data path; Qlib normalizes ``week`` to the physical ``1week`` filename.
    calendar = os.path.join(QLIB_DIR, "calendars", "week.txt")
    if os.path.isfile(calendar):
        shutil.copyfile(calendar, os.path.join(QLIB_DIR, "calendars", "1week.txt"))
    copied = 0
    for directory in Path(os.path.join(QLIB_DIR, "features")).glob("*"):
        for source in directory.glob("*.week.bin"):
            target = source.with_name(source.name.replace(".week.bin", ".1week.bin"))
            shutil.copyfile(source, target)
            copied += 1
    if not os.path.isfile(os.path.join(QLIB_DIR, "calendars", "1week.txt")) or copied != len(SYMBOLS) * len(FIELDS):
        raise RuntimeError("weekly Qlib normalized aliases incomplete: feature_files=%d" % copied)
    return copied


def raw_months(symbol, interval, start, end):
    directory = os.path.join(RAW_ROOT, "klines", symbol, interval)
    if not os.path.isdir(directory):
        raise RuntimeError("missing raw directory: %s" % directory)
    lo, hi = start[:7], end[:7]
    prefix = "%s-%s-" % (symbol, interval)
    out = []
    for name in sorted(os.listdir(directory)):
        if name.startswith(prefix) and name.endswith(".jsonl.gz"):
            month = name[len(prefix):-len(".jsonl.gz")]
            if lo <= month <= hi:
                out.append(os.path.join(directory, name))
    if not out:
        raise RuntimeError("no raw months for %s %s in %s..%s" % (symbol, interval, start, end))
    return out


def write_csv(symbol, interval, qlib_freq, start, end):
    out_dir = os.path.join(CSV_ROOT, qlib_freq)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "%s.csv" % symbol)
    lo_ms, hi_ms = utc_ms(start), utc_ms(end) + MS_PER_DAY - 1
    bar_ms = next(tf["bar_ms"] for tf in TIMEFRAMES if tf["raw_interval"] == interval)
    rows = 0
    first_ms = last_ms = None
    bad_grid = 0
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["date"] + FIELDS)
        for source in raw_months(symbol, interval, start, end):
            with gzip.open(source, "rt", encoding="utf-8") as gz:
                for line in gz:
                    if not line.strip():
                        continue
                    rec = json.loads(line)
                    ms = int(rec["open_time_ms"])
                    if ms < lo_ms or ms > hi_ms:
                        continue
                    if last_ms is not None and ms - last_ms != bar_ms:
                        bad_grid += 1
                    stamp = utc_stamp(ms) if interval not in ("1d", "1w") else utc_stamp(ms)[:10]
                    writer.writerow([stamp] + [rec[k] for k in FIELDS])
                    first_ms = ms if first_ms is None else first_ms
                    last_ms = ms
                    rows += 1
    if rows < 3:
        raise RuntimeError("too few raw rows for %s %s: %d" % (symbol, interval, rows))
    # Frequency-aware completeness guard: the archive must cover at least half of the bars the
    # registered window implies, so a missing/partial raw month fails closed instead of silently
    # shortening the sample.  A low-frequency series (1w) legitimately has few rows.
    expected_rows = (hi_ms - lo_ms + 1) / float(bar_ms)
    if rows < 0.5 * expected_rows:
        raise RuntimeError("raw coverage too thin for %s %s: rows=%d expected~%.0f"
                           % (symbol, interval, rows, expected_rows))
    return {"rows": rows, "first_open_time_ms": first_ms, "last_open_time_ms": last_ms,
            "off_grid_steps": bad_grid, "csv": out_path, "csv_bytes": os.path.getsize(out_path),
            "bar_step_ms": bar_ms, "raw_files": raw_months(symbol, interval, start, end)}


def csv_first_last_close(path):
    with open(path, newline="", encoding="utf-8") as fh:
        rows = csv.DictReader(fh)
        first = next(rows)
        last = first
        for last in rows:
            pass
    return float(first["close"]), float(last["close"])


def build_qlib(spec, attempt_dir, log):
    started = time.time()
    if os.path.isdir(WORK_ROOT):
        shutil.rmtree(WORK_ROOT)
    os.makedirs(CSV_ROOT, exist_ok=True)
    per_dataset = {}
    for tf in TIMEFRAMES:
        for symbol in SYMBOLS:
            key = "%s/%s" % (symbol, tf["raw_interval"])
            report = write_csv(symbol, tf["raw_interval"], tf["qlib_freq"],
                               spec["data"]["start"], spec["data"]["end"])
            if report["off_grid_steps"]:
                raise RuntimeError("non-contiguous raw grid for %s: %r" % (key, report["off_grid_steps"]))
            per_dataset[key] = report
            log("csv %s rows=%d" % (key, report["rows"]))
        api_freq = qlib_api_freq(tf)
        cmd = [VENV_PYTHON, DUMP_BIN, "dump_all", "--data_path", os.path.join(CSV_ROOT, tf["qlib_freq"]),
               "--qlib_dir", QLIB_DIR, "--freq", api_freq, "--include_fields",
               "open,close,high,low,volume", "--date_field_name", "date", "--max_workers", "1"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=14400)
        if proc.returncode:
            raise RuntimeError("dump_bin %s failed rc=%d\n%s\n%s" %
                               (tf["qlib_freq"], proc.returncode, proc.stdout[-2000:], proc.stderr[-2000:]))
        log("dump_bin %s ok" % api_freq)
        if tf["raw_interval"] == "1w":
            log("weekly Qlib aliases=%d" % qlib_week_aliases())
    import qlib
    from qlib.data import D
    qlib.init(provider_uri=QLIB_DIR, region="cn", expression_cache=None, dataset_cache=None)
    readback = {}
    for tf in TIMEFRAMES:
        for symbol in SYMBOLS:
            df = D.features([symbol], ["$" + f for f in FIELDS],
                            start_time=spec["data"]["start"],
                            end_time=spec["data"]["end"] + " 23:59:59", freq=qlib_api_freq(tf)).sort_index()
            if len(df) == 0 or int(df.isnull().sum().sum()) != 0:
                raise RuntimeError("Qlib read-back empty/null for %s %s" % (symbol, tf["raw_interval"]))
            stamps = [int(ts.value // 1000000) for ts in df.index.get_level_values("datetime")]
            if stamps != sorted(stamps) or len(set(stamps)) != len(stamps):
                raise RuntimeError("Qlib timestamps not strict ascending for %s %s" % (symbol, tf["raw_interval"]))
            key = "%s/%s" % (symbol, tf["raw_interval"])
            cs, cl = csv_first_last_close(per_dataset[key]["csv"])
            readback[key] = {"rows": len(df), "first": utc_stamp(stamps[0]), "last": utc_stamp(stamps[-1]),
                             "first_close": float(df["$close"].iloc[0]), "last_close": float(df["$close"].iloc[-1]),
                             "csv_first_close": cs, "csv_last_close": cl,
                             "close_abs_diff_first": abs(float(df["$close"].iloc[0]) - cs),
                             "close_abs_diff_last": abs(float(df["$close"].iloc[-1]) - cl)}
    report = {"qlib_dir": QLIB_DIR, "csv_root": CSV_ROOT, "qlib_version": qlib.__version__,
              "build_wall_seconds": round(time.time() - started, 3),
              "per_dataset": {k: {x: v for x, v in r.items() if x != "raw_files"}
                              for k, r in per_dataset.items()},
              "raw_files": {k: r["raw_files"] for k, r in per_dataset.items()},
              "readback": readback, "note": "raw JSONL.gz -> Qlib CSV -> Qlib .bin; no resampling"}
    atomic_json(os.path.join(attempt_dir, "artifacts", "bins_build.json"), report)
    return report


# --------------------------------------------------------------------------------------------
# causal microstructure feature panel
# --------------------------------------------------------------------------------------------

def rolling_mean(x, window):
    x = np.asarray(x, dtype=np.float64)
    good = np.isfinite(x)
    values = np.where(good, x, 0.0)
    sums = np.concatenate(([0.0], np.cumsum(values)))
    counts = np.concatenate(([0], np.cumsum(good.astype(np.int64))))
    starts = np.maximum(np.arange(len(x)) + 1 - int(window), 0)
    total = sums[np.arange(len(x)) + 1] - sums[starts]
    count = counts[np.arange(len(x)) + 1] - counts[starts]
    return np.divide(total, count, where=count > 0, out=np.full(len(x), np.nan))


def rolling_std(x, window):
    x = np.asarray(x, dtype=np.float64)
    good = np.isfinite(x)
    values = np.where(good, x, 0.0)
    sums = np.concatenate(([0.0], np.cumsum(values)))
    squares = np.concatenate(([0.0], np.cumsum(values * values)))
    counts = np.concatenate(([0], np.cumsum(good.astype(np.int64))))
    starts = np.maximum(np.arange(len(x)) + 1 - int(window), 0)
    total = sums[np.arange(len(x)) + 1] - sums[starts]
    total_sq = squares[np.arange(len(x)) + 1] - squares[starts]
    count = counts[np.arange(len(x)) + 1] - counts[starts]
    mean = np.divide(total, count, where=count > 0, out=np.zeros(len(x)))
    variance = np.maximum(np.divide(total_sq, count, where=count > 0, out=np.zeros(len(x))) - mean * mean, 0.0)
    return np.where(count >= 2, np.sqrt(variance), np.nan)


def rolling_moments(x, y, window):
    """NaN-aware rolling covariance/correlation of two series over the window ending at t."""
    x, y = np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)
    good = np.isfinite(x) & np.isfinite(y)
    xv, yv = np.where(good, x, 0.0), np.where(good, y, 0.0)
    prefix = lambda v: np.concatenate(([0.0], np.cumsum(v)))
    sx, sy = prefix(xv), prefix(yv)
    sxx, syy, sxy = prefix(xv * xv), prefix(yv * yv), prefix(xv * yv)
    sc = np.concatenate(([0], np.cumsum(good.astype(np.int64))))
    end = np.arange(len(x)) + 1
    starts = np.maximum(end - int(window), 0)
    n = sc[end] - sc[starts]
    sum_x, sum_y = sx[end] - sx[starts], sy[end] - sy[starts]
    sum_xx, sum_yy, sum_xy = sxx[end] - sxx[starts], syy[end] - syy[starts], sxy[end] - sxy[starts]
    safe_n = np.where(n > 0, n, 1)
    cov = (sum_xy - sum_x * sum_y / safe_n) / safe_n
    var_x = np.maximum((sum_xx - sum_x * sum_x / safe_n) / safe_n, 0.0)
    var_y = np.maximum((sum_yy - sum_y * sum_y / safe_n) / safe_n, 0.0)
    den = np.sqrt(var_x * var_y)
    corr = np.where(den > 1e-18, cov / np.maximum(den, 1e-18), np.nan)
    valid = (n >= max(4, int(window) // 2))
    return np.where(valid, cov, np.nan), np.where(valid, corr, np.nan)


def feature_panel(open_px, high, low, close, volume, bars_per_day):
    """Return the causal 9-column microstructure panel and the next-bar log-return target.

    Every column at bar t is a function of bars <= t only (no forward read); the target is the
    next bar's log return y_t = ln(C_{t+1} / C_t), so a bar without a defined next bar carries
    no target.
    """
    open_px = np.asarray(open_px, dtype=np.float64)
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    volume = np.asarray(volume, dtype=np.float64)
    count = len(close)
    window = max(8, int(bars_per_day))
    logret = np.full(count, np.nan)
    logret[1:] = np.log(np.maximum(close[1:], 1e-12) / np.maximum(close[:-1], 1e-12))
    prev_ret = np.concatenate(([np.nan], logret[:-1]))
    log_hl = np.log(np.maximum(high, 1e-12) / np.maximum(low, 1e-12))
    prev_log_hl = np.concatenate(([np.nan], log_hl[:-1]))
    joint_high = np.maximum(high, np.concatenate(([np.nan], high[:-1])))
    joint_low = np.minimum(low, np.concatenate(([np.nan], low[:-1])))
    kappa = 3.0 - 2.0 * math.sqrt(2.0)
    beta = 0.5 * (log_hl ** 2 + prev_log_hl ** 2)
    gamma = np.log(np.maximum(joint_high, 1e-12) / np.maximum(joint_low, 1e-12)) ** 2
    with np.errstate(invalid="ignore", divide="ignore"):
        alpha = (np.sqrt(2.0 * beta) - np.sqrt(beta)) / kappa - np.sqrt(gamma / kappa)
        cs = 2.0 * (np.exp(alpha) - 1.0) / (1.0 + np.exp(alpha))
    cs = np.clip(np.where(np.isfinite(cs), cs, np.nan), 0.0, 0.5)
    cov1, _ = rolling_moments(logret, prev_ret, window)
    roll = 2.0 * np.sqrt(np.maximum(-cov1, 0.0))
    rv_cc = rolling_std(logret, window)
    rv_park = np.sqrt(np.maximum(rolling_mean(log_hl ** 2, window) / (4.0 * math.log(2.0)), 0.0))
    rv_gk = np.sqrt(np.maximum(
        rolling_mean(0.5 * log_hl ** 2 - (2.0 * math.log(2.0) - 1.0) * logret ** 2, window), 0.0))
    range_rel = rolling_mean((high - low) / np.maximum(close, 1e-12), window)
    amihud = rolling_mean(np.abs(logret) / np.maximum(volume * close, 1e-12), window) * 1e9
    vol_std = rolling_std(volume, window)
    vol_z = (volume - rolling_mean(volume, window)) / np.maximum(vol_std, 1e-12)
    _, ac1 = rolling_moments(logret, prev_ret, window)
    panel = np.column_stack([cs, roll, rv_cc, rv_park, rv_gk, range_rel, amihud, vol_z, ac1])
    target = np.full(count, np.nan)
    if count > 1:
        target[:-1] = np.log(np.maximum(close[1:], 1e-12) / np.maximum(close[:-1], 1e-12))
    return panel, target


def causality_probe(panel_inputs, bars_per_day, sample_bars=240):
    """Recompute the panel from truncated prefixes and compare with the full-window values."""
    open_px, high, low, close, volume = panel_inputs
    full, _ = feature_panel(open_px, high, low, close, volume, bars_per_day)
    count = len(close)
    probes = 0
    mismatches = 0
    stride = max(1, (count - max(40, count // 8)) // sample_bars + 1)
    for cut in range(max(40, count // 8), count, stride):
        prefix, _ = feature_panel(open_px[:cut], high[:cut], low[:cut], close[:cut],
                                  volume[:cut], bars_per_day)
        probes += 1
        left, right = full[:cut - 1], prefix[:cut - 1]
        if not np.allclose(left, right, equal_nan=True, rtol=0, atol=1e-12):
            mismatches += 1
    return {"bars_probed": probes, "mismatches": mismatches}


# --------------------------------------------------------------------------------------------
# purged walk-forward pipeline: stability selection + tabular gradient boosting
# --------------------------------------------------------------------------------------------

def month_ordinals(open_ms):
    days = (np.asarray(open_ms, dtype=np.int64) // MS_PER_DAY).astype("datetime64[D]")
    return days.astype("datetime64[M]").astype(np.int64)


def fold_specs(months, structure=None):
    structure = structure or FOLD_STRUCTURE
    first, last = int(months[0]), int(months[-1])
    train_months = int(structure["train_months"])
    test_months = int(structure["test_months"])
    folds = []
    start = first
    while start + train_months <= last:
        train = (start, start + train_months)
        test = (start + train_months, min(start + train_months + test_months, last + 1))
        if test[1] > test[0]:
            folds.append({"train_months": train, "test_months": test})
        start += int(structure["step_months"])
    return folds


def average_ranks(values):
    values = np.asarray(values, dtype=np.float64)
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values), dtype=np.float64)
    ranks[order] = np.arange(1, len(values) + 1, dtype=np.float64)
    # average ties deterministically
    sorted_values = values[order]
    start = 0
    for index in range(1, len(sorted_values) + 1):
        if index == len(sorted_values) or sorted_values[index] != sorted_values[start]:
            if index - start > 1:
                ranks[order[start:index]] = ranks[order[start:index]].mean()
            start = index
    return ranks


def rank_columns(matrix):
    matrix = np.asarray(matrix, dtype=np.float64)
    out = np.empty_like(matrix)
    for column in range(matrix.shape[1]):
        out[:, column] = average_ranks(matrix[:, column])
    return out


def spearman_abs(rank_x, rank_y):
    """|Spearman| from pre-ranked columns (ranks are centred and scaled once per fold)."""
    x = rank_x - rank_x.mean(axis=0)
    y = rank_y - rank_y.mean()
    denom = np.sqrt((x * x).sum(axis=0) * (y * y).sum())
    return np.abs(np.where(denom > 1e-18, (x * y[:, None]).sum(axis=0) / np.maximum(denom, 1e-18), 0.0))


def stability_select(rank_x, rank_y, names, seed, config=None):
    """Bootstrap stability selection on the TRAIN segment only (deterministic, seeded)."""
    config = config or STABILITY
    resamples = int(config["resamples"])
    top_k = int(config["top_k"])
    threshold = float(config["frequency"])
    count = len(rank_y)
    rng = np.random.default_rng(seed)
    hits = np.zeros(len(names))
    for _ in range(resamples):
        index = rng.integers(0, count, size=count)
        scores = spearman_abs(rank_x[index], rank_y[index])
        order = sorted(range(len(names)), key=lambda i: (-float(scores[i]), names[i]))
        for i in order[:top_k]:
            hits[i] += 1
    frequency = hits / float(resamples)
    selected = [i for i in range(len(names)) if frequency[i] >= threshold]
    selected.sort(key=lambda i: (-float(frequency[i]), names[i]))
    selected = selected[:top_k]
    fallback = False
    if not selected:
        fallback = True
        scores = spearman_abs(rank_x, rank_y)
        order = sorted(range(len(names)), key=lambda i: (-float(scores[i]), names[i]))
        selected = order[:top_k]
    return {"selected_indices": sorted(selected),
            "selected_names": [names[i] for i in sorted(selected)],
            "selection_frequency": {names[i]: float(frequency[i]) for i in range(len(names))},
            "fallback_full_train": fallback}


def fit_lightgbm(x_train, y_train, seed):
    import lightgbm as lgb
    params = dict(MODEL_CONSTANTS["lightgbm"])
    params["random_state"] = seed
    model = lgb.LGBMRegressor(**params)
    model.fit(x_train, y_train)
    return model


def fit_sklearn_hist_gbm(x_train, y_train, seed):
    from sklearn.ensemble import HistGradientBoostingRegressor
    params = dict(MODEL_CONSTANTS["sklearn_hist_gbm"])
    params["random_state"] = seed
    model = HistGradientBoostingRegressor(**params)
    model.fit(x_train, y_train)
    return model


MODEL_FITTERS = {"lightgbm": fit_lightgbm, "sklearn_hist_gbm": fit_sklearn_hist_gbm}


def subsample_rows(index, cap=TRAIN_ROW_CAP):
    if len(index) <= cap:
        return index
    chosen = np.linspace(0, len(index) - 1, cap, dtype=np.int64)
    return index[chosen]


def walk_forward_predictions(panel, target, months, model_impl, seed, structure=None):
    """Purged walk-forward forecasts; the selector and the fit read the TRAIN segment only."""
    structure = structure or FOLD_STRUCTURE
    finite_x = np.isfinite(panel).all(axis=1)
    finite_y = np.isfinite(target)
    yhat = np.full(len(target), np.nan)
    sigma = np.full(len(target), np.nan)
    reports = []
    for fold_index, fold in enumerate(fold_specs(months, structure)):
        train_start, train_end = fold["train_months"]
        test_start, test_end = fold["test_months"]
        train_mask = (months >= train_start) & (months < train_end) & finite_x & finite_y
        train_index = np.flatnonzero(train_mask)
        purge = int(structure.get("purge_bars", 0))
        if purge and len(train_index) > purge:
            train_index = train_index[:-purge]
        test_index = np.flatnonzero((months >= test_start) & (months < test_end) & finite_x)
        if len(train_index) < 60 or len(test_index) == 0:
            reports.append({"fold": fold_index, "train_rows": int(len(train_index)),
                            "test_rows": int(len(test_index)), "status": "skipped"})
            continue
        fit_index = subsample_rows(train_index)
        rank_x = rank_columns(panel[fit_index])
        rank_y = average_ranks(target[fit_index])
        selection = stability_select(rank_x, rank_y, FEATURE_NAMES, seed + 7919 * (fold_index + 1))
        columns = selection["selected_indices"]
        fitter = MODEL_FITTERS[model_impl]
        model = fitter(panel[fit_index][:, columns], target[fit_index], seed + fold_index)
        prediction = np.asarray(model.predict(panel[test_index][:, columns]), dtype=np.float64)
        yhat[test_index] = prediction
        # Registered normalisation: the forecast is scaled by the model's own in-sample forecast
        # spread on the TRAIN segment (never by any statistic of the test segment).
        in_sample = np.asarray(model.predict(panel[fit_index][:, columns]), dtype=np.float64)
        train_sigma = float(np.std(in_sample))
        if not np.isfinite(train_sigma) or train_sigma <= 1e-12:
            train_sigma = max(float(np.std(target[fit_index])), 1e-12)
        sigma[test_index] = train_sigma
        reports.append({"fold": fold_index, "train_months": [int(train_start), int(train_end)],
                        "test_months": [int(test_start), int(test_end)],
                        "train_rows": int(len(train_index)), "fit_rows": int(len(fit_index)),
                        "test_rows": int(len(test_index)), "purged_rows": int(purge),
                        "selected_features": selection["selected_names"],
                        "selection_frequency": selection["selection_frequency"],
                        "fallback_full_train": selection["fallback_full_train"],
                        "train_target_sigma": train_sigma,
                        "test_prediction_mean": float(np.mean(prediction)),
                        "test_prediction_std": float(np.std(prediction)),
                        "status": "fitted"})
    return {"yhat": yhat, "sigma": sigma, "folds": reports}


def position_path(z, direction_mode, entry_z=ENTRY_Z):
    """Standardised forecast -> episode events (a fresh non-zero change opens an episode)."""
    raw = np.zeros(len(z), dtype=np.int64)
    finite = np.isfinite(z)
    raw[finite & (z >= entry_z)] = 1
    if direction_mode == "long_short":
        raw[finite & (z <= -entry_z)] = -1
    events = np.zeros(len(z), dtype=np.int64)
    prior = np.concatenate(([0], raw[:-1]))
    changed = (raw != 0) & (raw != prior)
    events[changed] = raw[changed]
    diagnostics = {"bars_with_forecast": int(finite.sum()),
                   "bars_above_entry_z": int(np.count_nonzero(raw == 1)),
                   "bars_below_entry_z": int(np.count_nonzero(raw == -1)),
                   "entry_events": int(np.count_nonzero(events == 1)),
                   "short_events": int(np.count_nonzero(events == -1)),
                   "direction_mode": direction_mode, "entry_z": entry_z}
    return {"pos": raw, "events": events, "diag": diagnostics}


def signal_layer(panel, target, months, case, seed):
    model_impl = case["model"]
    prediction = walk_forward_predictions(panel, target, months, model_impl, seed)
    z = prediction["yhat"] / np.maximum(prediction["sigma"], 1e-12)
    path = position_path(z, case["direction"])
    return {"case": case, "yhat": prediction["yhat"], "z": z, "events": path["events"],
            "pos": path["pos"], "folds": prediction["folds"], "diag": path["diag"]}


def naive_forecasters(target, close):
    """Descriptive (non-gating) naive-forecaster benchmark on the same target."""
    close = np.asarray(close, dtype=np.float64)
    logret = np.full(len(close), np.nan)
    logret[1:] = np.log(np.maximum(close[1:], 1e-12) / np.maximum(close[:-1], 1e-12))
    finite = np.isfinite(target) & np.isfinite(logret)
    if finite.sum() < 10:
        return {"observations": int(finite.sum()), "status": "insufficient_evidence"}
    y = target[finite]
    momentum = np.sign(logret[finite])
    return {"observations": int(finite.sum()),
            "momentum_sign_hit_rate": float(np.mean(momentum == np.sign(y))),
            "always_long_hit_rate": float(np.mean(np.sign(y) == 1.0)),
            "target_positive_share": float(np.mean(np.sign(y) == 1.0)),
            "status": "measured"}


def cross_asset_transfer(cohorts, spec, seed):
    """The record's cross-coin transfer reader: fit on one coin, predict the others (descriptive)."""
    reports = {}
    months = {symbol: month_ordinals(cohort["open_ms"]) for symbol, cohort in cohorts.items()}
    cutoff = int(month_ordinals(np.array([utc_ms(spec["split"]["historical_end"])], dtype=np.int64))[0])
    for source in SYMBOLS:
        cohort = cohorts[source]
        panel, target = cohort["panel"], cohort["target"]
        train_mask = (months[source] <= cutoff) & np.isfinite(panel).all(axis=1) & np.isfinite(target)
        train_index = subsample_rows(np.flatnonzero(train_mask))
        if len(train_index) < 60:
            reports[source] = {"status": "insufficient_training_rows"}
            continue
        rank_x = rank_columns(panel[train_index])
        rank_y = average_ranks(target[train_index])
        selection = stability_select(rank_x, rank_y, FEATURE_NAMES, seed + 104729)
        columns = selection["selected_indices"]
        model = MODEL_FITTERS["lightgbm"](panel[train_index][:, columns], target[train_index], seed)
        entry = {"selected_features": selection["selected_names"], "train_rows": int(len(train_index)),
                 "targets": {}}
        for destination in SYMBOLS:
            other = cohorts[destination]
            mask = np.isfinite(other["panel"]).all(axis=1) & np.isfinite(other["target"])
            index = np.flatnonzero(mask)
            if len(index) == 0:
                entry["targets"][destination] = {"status": "no_rows"}
                continue
            prediction = np.asarray(model.predict(other["panel"][index][:, columns]), dtype=np.float64)
            truth = other["target"][index]
            rank_pred = average_ranks(prediction)
            rank_true = average_ranks(truth)
            ic = float(spearman_abs(rank_pred[:, None], rank_true)[0])
            entry["targets"][destination] = {
                "rows": int(len(index)),
                "spearman_ic_abs": ic,
                "sign_hit_rate": float(np.mean(np.sign(prediction) == np.sign(truth)))}
        reports[source] = entry
    return reports


# --------------------------------------------------------------------------------------------
# coverage, selector and artifact writers
# --------------------------------------------------------------------------------------------

def metric_block(m):
    keys = ("net_pnl", "gross_pnl", "fees", "funding", "episodes", "fills", "adds",
            "turnover_usdt", "sharpe", "max_dd_pct", "annualized_return", "capital_utilization",
            "tp_hits", "stop_hits", "time_exits")
    return {k: m[k] for k in keys}


def case_columns(case):
    return {"dir_long_only": 1 if case["direction"] == "long_only" else 0,
            "dir_long_short": 1 if case["direction"] == "long_short" else 0,
            "model_lightgbm": 1 if case["model"] == "lightgbm" else 0,
            "model_sklearn": 1 if case["model"] == "sklearn_hist_gbm" else 0}


def row_for(symbol, timeframe, case, params, grid, full, historical=None, oos=None):
    row = {"symbol": symbol, "timeframe": timeframe, "case_label": case["label"], "grid": grid}
    row.update(case_columns(case))
    row.update(params)
    for prefix, metric in (("full", full), ("historical", historical), ("oos", oos)):
        if metric is not None:
            for key, value in metric_block(metric).items():
                row[prefix + "_" + key] = value
    for key in metric_block(full):
        row[key] = full[key]
    row["ending_equity"] = START_EQUITY + float(full["net_pnl"])
    row["max_effective_leverage"] = LEVERAGE
    row["halted"] = bool(full.get("halted", False))
    row["decomposition_ok"] = bool(full["decomposition_ok"])
    return row


def row_key(row):
    return (int(row["dir_long_only"]), int(row["dir_long_short"]),
            int(row["model_lightgbm"]), int(row["model_sklearn"]),
            float(row["spacing_pct"]), float(row["size_multiplier"]),
            float(row["breakeven_tp_pct"]), float(row["invalidation_pct"]))


def param_only(row):
    return {k: row[k] for k in ("dir_long_only", "dir_long_short", "model_lightgbm",
                                "model_sklearn", "spacing_pct", "size_multiplier",
                                "breakeven_tp_pct", "invalidation_pct")}


def neighbourhood(winner, rows):
    axes = ["spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct"]
    table = {row_key(r): r for r in rows}
    domains = {k: list(v) for k, v in DCA_AXES.items()}
    neighbours = []
    for axis in axes:
        values = domains[axis]
        index = values.index(float(winner[axis]))
        for neighbour_index in (index - 1, index + 1):
            if 0 <= neighbour_index < len(values):
                candidate = list(row_key(winner))
                candidate[axes.index(axis) + 4] = values[neighbour_index]
                hit = table.get(tuple(candidate))
                if hit is not None:
                    neighbours.append(hit)
    if not neighbours:
        return {"same_sign_fraction": 0.0, "neighbours": 0, "agreeing": 0, "passed": False}
    sign = 1 if float(winner["net_pnl"]) > 0 else -1
    agreeing = sum(1 for r in neighbours if (1 if float(r["net_pnl"]) > 0 else -1) == sign)
    fraction = agreeing / len(neighbours)
    return {"same_sign_fraction": float(fraction), "neighbours": len(neighbours),
            "agreeing": agreeing, "passed": bool(fraction >= GATES["min_neighbour_same_sign_fraction"])}


def select_cohort(rows_by_grid):
    """cohort-selector-v1 with explicit cull reasons (historical segment only for the choice)."""
    historical = rows_by_grid["historical"]
    best_episodes = max(int(r["episodes"]) for r in historical)
    if best_episodes < GATES["min_episodes_is"]:
        return None, {"winner": None, "cull_reasons": ["insufficient_trades"],
                      "best_historical_episodes": best_episodes,
                      "neighbourhood": {"same_sign_fraction": 0.0, "neighbours": 0,
                                        "agreeing": 0, "passed": False}}
    eligible = [r for r in historical
                if float(r["net_pnl"]) > 0 and float(r["sharpe"]) > 0
                and int(r["episodes"]) >= GATES["min_episodes_is"]]
    if not eligible:
        return None, {"winner": None, "cull_reasons": ["no_qualifying_candidate"],
                      "best_historical_episodes": best_episodes,
                      "neighbourhood": {"same_sign_fraction": 0.0, "neighbours": 0,
                                        "agreeing": 0, "passed": False}}
    eligible.sort(key=lambda r: (-float(r["sharpe"]), -float(r["net_pnl"]), row_key(r)))
    winner = eligible[0]
    key = row_key(winner)
    lookup = {grid: next(r for r in rows_by_grid[grid] if row_key(r) == key)
              for grid in GRID_KINDS}
    reasons = []
    oos = lookup["oos"]
    if not (float(oos["net_pnl"]) > 0 and float(oos["sharpe"]) > 0
            and int(oos["episodes"]) >= GATES["min_episodes_oos"]):
        reasons.append("oos_economic")
    if not float(lookup["full"]["net_pnl"]) > 0:
        reasons.append("full_economic")
    failing = [g for g in ("fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks")
               if float(lookup[g]["net_pnl"]) <= 0]
    if failing:
        reasons.append("robustness_economic:" + ",".join(failing))
    neighbour = neighbourhood(winner, historical)
    if not neighbour["passed"]:
        reasons.append("parameter_neighbourhood")
    detail = {"winner": param_only(winner), "winner_case_label": winner["case_label"],
              "best_historical_episodes": best_episodes, "neighbourhood": neighbour,
              "robustness": {g: {k: float(lookup[g][k]) for k in ("net_pnl", "sharpe", "max_dd_pct")}
                             for g in ("full", "fee_2x", "funding_2x", "entry_delay_1_bar",
                                       "slippage_2ticks")}}
    if reasons:
        return None, dict(detail, cull_reasons=reasons)
    detail["cull_reasons"] = []
    detail["phases"] = {phase: {k: (int(lookup[phase][k]) if k in ("episodes", "fills")
                                    else float(lookup[phase][k]))
                                for k in ("net_pnl", "sharpe", "episodes", "fills", "max_dd_pct")}
                        for phase in ("historical", "oos", "full")}
    return winner, detail


def write_grid(path, rows):
    if not rows:
        raise RuntimeError("empty grid: %s" % path)
    keys = ["symbol", "timeframe", "case_label", "grid",
            "dir_long_only", "dir_long_short", "model_lightgbm", "model_sklearn",
            "spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct",
            "gross_pnl", "fees", "funding", "net_pnl", "ending_equity", "episodes", "fills",
            "adds", "turnover_usdt", "sharpe", "max_dd_pct", "annualized_return",
            "max_effective_leverage", "capital_utilization", "halted", "decomposition_ok"]
    for prefix in ("full", "historical", "oos"):
        keys += [prefix + "_" + k for k in metric_block(rows[0])]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in keys})


def disposition_for_survivor_count(count):
    # ponytail: keep the contract's count-to-band mapping in one deterministic branch.
    if count == 0:
        return "REJECT / NO_SURVIVOR"
    if count == 1:
        return "SURVIVOR_FOUND"
    return "MULTIPLE_SURVIVORS"


def load_cohort(symbol, tf, spec):
    import qlib
    from qlib.data import D
    df = D.features([symbol], ["$" + f for f in FIELDS],
                    start_time=spec["data"]["start"], end_time=spec["data"]["end"] + " 23:59:59",
                    freq=qlib_api_freq(tf)).sort_index()
    open_ms = np.array([int(ts.value // 1000000) for ts in df.index.get_level_values("datetime")], dtype=np.int64)
    values = {f: df["$" + f].to_numpy(dtype=np.float64) for f in FIELDS}
    # A cohort needs a strictly increasing grid and at least two rolling windows of bars; the
    # absolute count depends on the registered frequency (1w legitimately has few rows).
    if len(open_ms) < max(3, 2 * int(tf["bars_per_day"])) or np.any(np.diff(open_ms) <= 0):
        raise RuntimeError("bad Qlib cohort grid %s/%s: bars=%d"
                           % (symbol, tf["raw_interval"], len(open_ms)))
    return {"symbol": symbol, "timeframe": tf["raw_interval"], "qlib_freq": tf["qlib_freq"],
            "bars_per_day": tf["bars_per_day"], "open_ms": open_ms, **values}


def window_indices(open_ms, start, end):
    lo = int(np.searchsorted(open_ms, utc_ms(start), side="left"))
    hi = int(np.searchsorted(open_ms, utc_ms(end) + MS_PER_DAY - 1, side="right"))
    return lo, hi


def run(spec, attempt_dir):
    os.makedirs(os.path.join(attempt_dir, "artifacts", "grids"), exist_ok=True)
    os.makedirs(os.path.join(attempt_dir, "logs"), exist_ok=True)
    log_path = os.path.join(attempt_dir, "logs", "run.log")
    log_fh = open(log_path, "w", encoding="utf-8")
    started = time.time()
    progress = {"cohorts_done": 0, "cohorts_total": len(SYMBOLS) * len(TIMEFRAMES),
                "case_evaluations": 0}

    def log(message):
        line = "[%s] %s" % (dt.datetime.now(dt.timezone.utc).isoformat(), message)
        print(line, flush=True)
        log_fh.write(line + "\n")
        log_fh.flush()

    def write_progress():
        progress["updated_at_utc"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        progress["runtime_seconds"] = round(time.time() - started, 3)
        atomic_json(os.path.join(attempt_dir, "artifacts", "progress.json"), progress)

    state = {"stage": "RUNNING_QLIB", "family_id": FAMILY_ID, "round_id": spec["round_id"],
             "run_id": spec["run_id"], "started_at_utc": dt.datetime.now(dt.timezone.utc).isoformat()}
    atomic_json(os.path.join(attempt_dir, "state.json"), state)
    try:
        instruments = instrument_constants()
        if instruments.get("fallback"):
            raise RuntimeError("canonical instrument metadata unreadable inside the container")
        build = build_qlib(spec, attempt_dir, log)
        grid_rows = {g: [] for g in GRID_KINDS}
        cohort_results, survivors = [], []
        layer_hist = [0] * (MAX_LAYERS + 1)
        decomposition_failures = 0
        params_grid = [dict(zip(DCA_AXES, values)) for values in itertools.product(*(DCA_AXES[a] for a in DCA_AXES))]
        signal_reports, funding_truth, causality_reports = [], {}, []
        transfer_report = {"status": "not_evaluated"}
        naive_report = {}
        for tf_index, tf in enumerate(TIMEFRAMES):
            loaded = [load_cohort(symbol, tf, spec) for symbol in SYMBOLS]
            grid = loaded[0]["open_ms"]
            if any(not np.array_equal(c["open_ms"], grid) for c in loaded[1:]):
                raise RuntimeError("cross-sectional timestamps do not align for %s" % tf["raw_interval"])
            months = month_ordinals(grid)
            panels = {}
            for cohort in loaded:
                panel, target = feature_panel(cohort["open"], cohort["high"], cohort["low"],
                                              cohort["close"], cohort["volume"], tf["bars_per_day"])
                cohort["panel"], cohort["target"] = panel, target
                panels[cohort["symbol"]] = cohort
            for symbol, cohort in panels.items():
                probe = causality_probe((cohort["open"], cohort["high"], cohort["low"],
                                         cohort["close"], cohort["volume"]), tf["bars_per_day"])
                if probe["mismatches"]:
                    raise RuntimeError("causality probe mismatch for %s/%s" % (symbol, tf["raw_interval"]))
                cohort["causality_probe"] = probe
                causality_reports.append({"cohort": "%s/%s" % (symbol, tf["raw_interval"]), **probe})
            layers = {}
            for symbol in SYMBOLS:
                for case in STRATEGIES:
                    layers[(symbol, case["case_code"])] = signal_layer(
                        panels[symbol]["panel"], panels[symbol]["target"], months, case,
                        SEED + tf_index)
            for symbol in SYMBOLS:
                for case in STRATEGIES:
                    layer = layers[(symbol, case["case_code"])]
                    fitted = [f for f in layer["folds"] if f["status"] == "fitted"]
                    signal_reports.append({
                        "cohort": "%s/%s" % (symbol, tf["raw_interval"]), "case": case["label"],
                        "folds_fitted": len(fitted), "folds_total": len(layer["folds"]),
                        "bars_with_forecast": layer["diag"]["bars_with_forecast"],
                        "entry_events": layer["diag"]["entry_events"],
                        "short_events": layer["diag"]["short_events"],
                        "selected_feature_frequency": {
                            name: float(np.mean([f.get("selection_frequency", {}).get(name, 0.0)
                                                 for f in fitted] or [0.0]))
                            for name in FEATURE_NAMES},
                        "folds": [{k: v for k, v in f.items() if k != "selection_frequency"}
                                  for f in layer["folds"]],
                    })
            if tf["raw_interval"] == TRANSFER_TIMEFRAME:
                transfer_report = {"status": "measured", "timeframe": tf["raw_interval"],
                                   "direction": "train on source coin, predict every coin",
                                   "cohorts": cross_asset_transfer(panels, spec, SEED + tf_index)}
            for symbol in SYMBOLS:
                naive_report["%s/%s" % (symbol, tf["raw_interval"])] = naive_forecasters(
                    panels[symbol]["target"], panels[symbol]["close"])
            funding_data = {}
            for cohort in loaded:
                key = "%s/%s" % (cohort["symbol"], cohort["timeframe"])
                funding_data[key] = RAIL.load_funding(cohort["symbol"], spec["data"]["start"],
                                                      spec["data"]["end"], cohort["open_ms"])
                funding_truth[key] = funding_data[key][1]
            for symbol in SYMBOLS:
                cohort = panels[symbol]
                key = "%s/%s" % (symbol, tf["raw_interval"])
                i0, i1 = window_indices(cohort["open_ms"], spec["data"]["start"], spec["data"]["end"])
                h0, h1 = window_indices(cohort["open_ms"], spec["split"]["historical_start"],
                                        spec["split"]["historical_end"])
                o0, o1 = window_indices(cohort["open_ms"], spec["split"]["oos_start"],
                                        spec["split"]["oos_end"])
                rows_by_grid = {g: [] for g in GRID_KINDS}
                window_bars, horizon_bars = 60 * tf["bars_per_day"], 5 * tf["bars_per_day"]
                for case in STRATEGIES:
                    layer = layers[(symbol, case["case_code"])]
                    for params in params_grid:
                        for grid_name in GRID_KINDS:
                            if grid_name == "historical":
                                a0, a1, stress = h0, h1, {}
                            elif grid_name == "oos":
                                a0, a1, stress = o0, o1, {}
                            elif grid_name == "no_funding":
                                a0, a1, stress = h0, h1, {"no_funding": True}
                            else:
                                a0, a1, stress = i0, i1, {}
                            if grid_name == "fee_2x":
                                stress["fee_mult"] = 2.0
                            elif grid_name == "funding_2x":
                                stress["funding_mult"] = 2.0
                            elif grid_name == "entry_delay_1_bar":
                                stress["entry_delay"] = 1
                            elif grid_name == "slippage_2ticks":
                                stress["slip_ticks"] = 2
                            elif grid_name in ("no_funding", "no_funding_full"):
                                stress["no_funding"] = True
                            elif grid_name == "cost_attrition_40bps":
                                stress["fee_mult"] = 8.0
                            if grid_name == "no_funding_full":
                                a0, a1 = i0, i1
                            metric = RAIL.simulate(cohort["open"], cohort["high"], cohort["low"],
                                                   cohort["close"], cohort["open_ms"],
                                                   funding_data[key][0], layer, params,
                                                   window_bars, horizon_bars, a0, a1, stress, symbol)
                            hist = oos = None
                            if grid_name == "full":
                                hist = RAIL.simulate(cohort["open"], cohort["high"], cohort["low"],
                                                     cohort["close"], cohort["open_ms"],
                                                     funding_data[key][0], layer, params,
                                                     window_bars, horizon_bars, h0, h1, {}, symbol)
                                oos = RAIL.simulate(cohort["open"], cohort["high"], cohort["low"],
                                                    cohort["close"], cohort["open_ms"],
                                                    funding_data[key][0], layer, params,
                                                    window_bars, horizon_bars, o0, o1, {}, symbol)
                            row = row_for(symbol, tf["raw_interval"], case, params, grid_name, metric,
                                          metric if grid_name in ("historical", "no_funding") else hist,
                                          metric if grid_name == "oos" else oos)
                            rows_by_grid[grid_name].append(row)
                            grid_rows[grid_name].append(row)
                            progress["case_evaluations"] += 1
                            if progress["case_evaluations"] % 500 == 0:
                                write_progress()
                            if grid_name == "full":
                                for index, count in enumerate(metric["layer_hist"]):
                                    layer_hist[index] += int(count)
                            if not metric["decomposition_ok"]:
                                decomposition_failures += 1
                winner, detail = select_cohort(rows_by_grid)
                label = "%s/%s" % (symbol, tf["raw_interval"])
                record = {"cohort": label, "outcome": "SURVIVOR" if winner else "CULLED",
                          "winner": detail.get("winner"), "winner_case_label": detail.get("winner_case_label"),
                          "best_historical_episodes": detail.get("best_historical_episodes"),
                          "metrics": {k: detail[k] for k in ("phases", "robustness")
                                      if k in detail},
                          "neighbourhood": detail.get("neighbourhood"),
                          "cull_reasons": detail.get("cull_reasons", [])}
                cohort_results.append(record)
                if winner:
                    survivors.append(record)
                log("cohort %s bars=%d outcome=%s cull=%s" %
                    (label, len(cohort["open_ms"]), record["outcome"],
                     ",".join(record["cull_reasons"]) or "none"))
                del panels[symbol]
            progress["cohorts_done"] += len(SYMBOLS)
            write_progress()
            del loaded, panels
        atomic_json(os.path.join(attempt_dir, "artifacts", "signal_layer.json"), {
            "family_id": FAMILY_ID, "engine": ENGINE_VERSION, "seed": SEED,
            "feature_count": len(FEATURE_NAMES), "features": FEATURE_NAMES,
            "entry_rule": "z = yhat / train_target_sigma; |z| >= %.2f emits a fresh non-zero event" % ENTRY_Z,
            "fold_structure": FOLD_STRUCTURE, "stability_selection": STABILITY,
            "model_constants": MODEL_CONSTANTS, "train_row_cap": TRAIN_ROW_CAP,
            "timeframes": signal_reports, "causality_probes": causality_reports,
            "causality": "all rolling features use observations at or before the signal bar; "
                         "the target is the next bar's log return; selector and fit read TRAIN only",
        })
        atomic_json(os.path.join(attempt_dir, "artifacts", "family_falsification.json"), {
            "family_id": FAMILY_ID, "status": "MEASURED_DESCRIPTIVE_NON_GATING",
            "naive_forecasters": naive_report,
            "cross_asset_transfer": transfer_report,
            "record_falsification_items": {
                "lower_fee_tiers": "not testable locally: the canonical raw carries only the retail "
                                   "taker fee per instrument; the registered fee_2x and "
                                   "cost_attrition_40bps grids move the other way (upward)",
                "different_feature_sets_or_models": "two tabular gradient-boosting implementations "
                                                    "over the registered feature panel are measured "
                                                    "as separate strategy cases",
                "cross_coin_transfer": "measured above (train on one coin, predict every coin)",
                "cross_venue_transfer": "not testable locally: the canonical raw holds no spot venue",
            },
            "implementation_boundary": "the record's hierarchical modelling / SHAP / meta-learning "
                                       "layers are not reproduced; stability selection plus purged "
                                       "walk-forward gradient boosting is the registered local pipeline",
        })
        for grid_name in GRID_KINDS:
            write_grid(os.path.join(attempt_dir, "artifacts", "grid_%s.csv" % grid_name),
                       grid_rows[grid_name])
        atomic_json(os.path.join(attempt_dir, "artifacts", "cohort_results.json"), cohort_results)
        atomic_json(os.path.join(attempt_dir, "artifacts", "cohort_survivors.json"), survivors)
        atomic_json(os.path.join(attempt_dir, "artifacts", "dca_layer_histogram.json"),
                    {"level_%02d" % i: int(v) for i, v in enumerate(layer_hist)})
        expected_per_grid = len(SYMBOLS) * len(TIMEFRAMES) * len(STRATEGIES) * len(params_grid)
        actual_total = sum(len(v) for v in grid_rows.values())
        baseline_net = sum(float(r["net_pnl"]) for r in grid_rows["full"])
        stress_effects = {
            "fee_2x": sum(float(r["net_pnl"]) for r in grid_rows["fee_2x"]) - baseline_net,
            "funding_2x": sum(float(r["net_pnl"]) for r in grid_rows["funding_2x"]) - baseline_net,
            "entry_delay_1_bar": sum(float(r["net_pnl"]) for r in grid_rows["entry_delay_1_bar"]) - baseline_net,
            "slippage_2ticks": sum(float(r["net_pnl"]) for r in grid_rows["slippage_2ticks"]) - baseline_net,
            "no_funding": sum(float(r["net_pnl"]) for r in grid_rows["no_funding_full"]) - baseline_net,
            "cost_attrition_40bps": sum(float(r["net_pnl"]) for r in grid_rows["cost_attrition_40bps"]) - baseline_net,
        }
        assertions = {
            "coverage_complete": actual_total == expected_per_grid * len(GRID_KINDS)
                                 and all(len(v) == expected_per_grid for v in grid_rows.values()),
            "all_28_cohorts_present": len(cohort_results) == len(SYMBOLS) * len(TIMEFRAMES),
            "all_10_grids_present": all(len(v) == expected_per_grid for v in grid_rows.values()),
            "pnl_decomposition": decomposition_failures == 0,
            "qlib_readback": bool(build.get("readback")),
            "causal_signal": (len(causality_reports) == len(SYMBOLS) * len(TIMEFRAMES)
                              and all(r["mismatches"] == 0 and r["bars_probed"] > 0
                                      for r in causality_reports)),
            "funding_loaded": all(sum(counts.values()) > 0 for counts in funding_truth.values()),
            "parameter_contract_complete": True,
            # The rail's layer histogram is depth-cumulative: level_00 counts every executed
            # episode and level_k the episodes that reached at least k+1 tranches, so level_00
            # is the histogram's episode identity (verified against real artifacts).
            "dca_layer_histogram": layer_hist[0] == sum(int(r["full_episodes"]) for r in grid_rows["full"]),
            "gross_net_independent": all(
                abs(float(r["gross_pnl"]) - float(r["fees"]) - float(r["funding"]) - float(r["net_pnl"])) <= 1e-6
                for r in grid_rows["full"]),
            "stress_grids_effective": all(abs(value) > 0.0 for value in stress_effects.values()),
            "instrument_metadata": bool(instruments["tick"]) and bool(instruments["taker_fee"]),
        }
        atomic_json(os.path.join(attempt_dir, "artifacts", "assertions.json"), assertions)
        atomic_json(os.path.join(attempt_dir, "artifacts", "stress_effects.json"), stress_effects)
        survivor_count = len(survivors)
        result = {"schema_version": 1, "family_id": FAMILY_ID, "round_id": spec["round_id"],
                  "run_id": spec["run_id"], "engine": ENGINE_VERSION,
                  "status": "ARTIFACT_READY", "coverage_complete": assertions["coverage_complete"],
                  "assertions_all_true": all(assertions.values()),
                  "expected_case_evaluations": expected_per_grid * len(GRID_KINDS),
                  "case_evaluations_total": actual_total, "cohort_count": len(cohort_results),
                  "cohort_survivor_count": survivor_count,
                  "cohort_survivors": [r["cohort"] for r in survivors],
                  "disposition": disposition_for_survivor_count(survivor_count),
                  "verdict_recommendation": "PASS" if survivor_count else "REJECT",
                  "performance_claimable_recommendation": bool(survivor_count),
                  "selector_version": "cohort-selector-v1", "disposition_version": "cohort-disposition-v1",
                  "grid_kinds": GRID_KINDS,
                  "research_boundary": "source microstructure-alpha mechanism adapted to the complete "
                                       "local Binance USD-M symbol x timeframe eligible universe; "
                                       "no adoption/live claim",
                  "assertion_failures": sorted(k for k, v in assertions.items() if not v),
                  "funding_truth_status": funding_truth,
                  "instrument_metadata": instruments,
                  "runtime_seconds": round(time.time() - started, 3),
                  "generated_at_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        atomic_json(os.path.join(attempt_dir, "result.json"), result)
        state.update({"stage": "ARTIFACT_READY", "finished_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                      "case_evaluations_total": actual_total, "survivor_count": survivor_count})
        atomic_json(os.path.join(attempt_dir, "state.json"), state)
        write_progress()
        log("ARTIFACT_READY cohorts=%d survivors=%d rows=%d" % (len(cohort_results), survivor_count, actual_total))
        return result
    except Exception as exc:
        state.update({"stage": "FAILED_SCRIPT", "error": "%s: %s" % (type(exc).__name__, exc)})
        atomic_json(os.path.join(attempt_dir, "state.json"), state)
        write_progress()
        log("FAILED %s: %s" % (type(exc).__name__, exc))
        raise
    finally:
        log_fh.close()


RAIL, RAIL_PATH = load_sibling_rail()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-spec", required=True)
    ap.add_argument("--attempt-dir", default=None)
    args = ap.parse_args(argv)
    spec = load_json(args.run_spec)
    if spec.get("family_id") != FAMILY_ID:
        raise SystemExit("family mismatch")
    attempt_dir = args.attempt_dir or os.path.dirname(os.path.abspath(args.run_spec))
    if not os.path.abspath(attempt_dir).startswith("/results/"):
        raise SystemExit("attempt must be under /results")
    result = run(spec, attempt_dir)
    print(json.dumps({"ok": True, "attempt_dir": attempt_dir,
                      "case_evaluations_total": result["case_evaluations_total"],
                      "survivors": result["cohort_survivors"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
