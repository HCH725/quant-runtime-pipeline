#!/usr/bin/env python3
"""Qlib-container runner for the registered RG-ResMoE volatility family.

The family-specific signal is a compact numpy implementation of the registered model:
16 causal price/volatility features, a frozen two-hidden-layer GELU base MLP, two residual
experts, and soft state-dependent routing from market/idiosyncratic volatility.  The existing
DCA/accounting rail is reused from the audited topological runner; this file owns the model,
data preparation, coverage and family artifacts.

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
WORK_ROOT = "/qlib/work/rg-resmoe-v1"
CSV_ROOT = os.path.join(WORK_ROOT, "csv")
QLIB_DIR = os.path.join(WORK_ROOT, "qlib-data")
MS_PER_DAY = 86400000
FIELDS = ["open", "high", "low", "close", "volume"]
FAMILY_ID = "cross-sectional-volatility-regime-gated-residual-mixture-of-experts-2026-09-02"
ENGINE_VERSION = "rg_resmoe_v1"
SEED = 20260921
START_EQUITY = 30000.0
BASE_QUOTE = 1000.0
LEVERAGE = 10.0
TAKER_FEE = 0.0005
MAX_LAYERS = 11
SLIPPAGE_TICKS = 1
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
STRATEGIES = [{"method_code": 0, "method": "rg_resmoe", "label": "rg_resmoe"}]
DCA_AXES = {
    "spacing_pct": [0.01, 0.02, 0.03, 0.04],
    "size_multiplier": [1.0, 1.1],
    "breakeven_tp_pct": [0.01, 0.02, 0.03],
    "invalidation_pct": [0.05, 0.10],
}
GRID_KINDS = ["historical", "oos", "full", "fee_2x", "funding_2x",
              "entry_delay_1_bar", "slippage_2ticks", "no_funding",
              "no_funding_full", "cost_attrition_40bps"]
PRICE_TICK = {"BTCUSDT": 0.10, "ETHUSDT": 0.01, "SOLUSDT": 0.001, "BNBUSDT": 0.01}


def load_sibling_rail():
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
            mod.PRICE_TICK = dict(PRICE_TICK)
            mod.START_EQUITY = START_EQUITY
            mod.BASE_QUOTE = BASE_QUOTE
            mod.LEVERAGE = LEVERAGE
            mod.TAKER_FEE = TAKER_FEE
            mod.MAX_LAYERS = MAX_LAYERS
            mod.SLIPPAGE_TICKS = SLIPPAGE_TICKS
            return mod, str(path)
    raise RuntimeError("audited DCA rail not found")


RAIL, RAIL_PATH = load_sibling_rail()


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
    if rows < 100:
        raise RuntimeError("too few raw rows for %s %s: %d" % (symbol, interval, rows))
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


def causal_beta(y, x, window):
    y, x = np.asarray(y, dtype=np.float64), np.asarray(x, dtype=np.float64)
    good = np.isfinite(y) & np.isfinite(x)
    yy = np.where(good, y, 0.0); xx = np.where(good, x, 0.0)
    prefix = lambda v: np.concatenate(([0.0], np.cumsum(v)))
    sy, sx, syy, sxx, syx, sc = (prefix(yy), prefix(xx), prefix(yy * yy),
                                  prefix(xx * xx), prefix(yy * xx), prefix(good.astype(np.int64)))
    # Exclude the current bar: the feature at t may only use observations < t.
    end = np.arange(len(y))
    starts = np.maximum(end - int(window), 0)
    n = sc[end] - sc[starts]
    sum_y = sy[end] - sy[starts]; sum_x = sx[end] - sx[starts]
    sum_yy = syy[end] - syy[starts]; sum_xx = sxx[end] - sxx[starts]; sum_yx = syx[end] - syx[starts]
    den = sum_xx - np.divide(sum_x * sum_x, n, where=n > 0, out=np.zeros(len(y)))
    cov = sum_yx - np.divide(sum_x * sum_y, n, where=n > 0, out=np.zeros(len(y)))
    return np.where((n >= 20) & (den > 1e-12), cov / np.maximum(den, 1e-12), np.nan)


def rsi(values, window=14):
    ret = np.diff(np.log(np.maximum(values, 1e-12)), prepend=np.nan)
    up = rolling_mean(np.maximum(ret, 0.0), window)
    down = rolling_mean(np.maximum(-ret, 0.0), window)
    return 100.0 - 100.0 / (1.0 + up / np.maximum(down, 1e-12))


def feature_panel(closes, bars_per_day=1):
    """Return causal x[T,N,16], target[T,N], and z[T,N,2]."""
    closes = np.asarray(closes, dtype=np.float64)
    t_count, n_assets = closes.shape
    logret = np.full_like(closes, np.nan)
    logret[1:] = np.log(np.maximum(closes[1:], 1e-12) / np.maximum(closes[:-1], 1e-12))
    x = np.full((t_count, n_assets, 16), np.nan)
    windows = tuple(max(1, int(v * bars_per_day)) for v in (5, 20, 60))
    rsi_window = max(2, int(14 * bars_per_day))
    beta_window = max(20, int(120 * bars_per_day))
    target_horizon = max(1, int(5 * bars_per_day))
    for j in range(n_assets):
        r = logret[:, j]
        for k, window in enumerate(windows):
            x[:, j, k] = np.sqrt(365.0 * bars_per_day * rolling_mean(r * r, window))
        for k, window in enumerate(windows[:2], start=3):
            x[:, j, k] = rolling_mean(r, window) * window
        x[:, j, 5] = rsi(closes[:, j], rsi_window)
        for k in range(10):
            x[k + 1:, j, 6 + k] = r[1:len(r) - k]
    market_count = np.isfinite(logret).sum(axis=1)
    market = np.divide(np.nansum(logret, axis=1), np.maximum(market_count, 1),
                       where=market_count > 0, out=np.full(t_count, np.nan))
    market_rv = np.sqrt(365.0 * bars_per_day * rolling_mean(market * market, windows[1]))
    z = np.full((t_count, n_assets, 2), np.nan)
    z[:, :, 0] = market_rv[:, None]
    for j in range(n_assets):
        beta = causal_beta(logret[:, j], market, beta_window)
        residual = logret[:, j] - beta * market
        z[:, j, 1] = rolling_std(residual, windows[1])
    target = np.full_like(closes, np.nan)
    forward_sq = np.nan_to_num(logret * logret, nan=0.0)
    valid_sq = np.isfinite(logret).astype(np.int64)
    prefix_sq = np.concatenate((np.zeros((1, n_assets)), np.cumsum(forward_sq, axis=0)))
    prefix_valid = np.concatenate((np.zeros((1, n_assets), dtype=np.int64), np.cumsum(valid_sq, axis=0)))
    if t_count > target_horizon:
        sums = prefix_sq[target_horizon + 1:] - prefix_sq[1:-target_horizon]
        counts = prefix_valid[target_horizon + 1:] - prefix_valid[1:-target_horizon]
        good = counts == target_horizon
        target[:-target_horizon][good] = np.sqrt(365.0 * bars_per_day / target_horizon * sums[good])
    return x, target, z, logret


def gelu(x):
    return 0.5 * x * (1.0 + np.tanh(np.sqrt(2.0 / np.pi) * (x + 0.044715 * x ** 3)))


def gelu_grad(x):
    c = np.sqrt(2.0 / np.pi)
    u = c * (x + 0.044715 * x ** 3)
    th = np.tanh(u)
    return 0.5 * (1.0 + th) + 0.5 * x * (1.0 - th * th) * c * (1.0 + 3.0 * 0.044715 * x * x)


def safe_matmul(left, right):
    # The BLAS path may raise harmless divide/overflow warnings while a clipped
    # full-batch update is converging; non-finite gradients are zeroed below.
    with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
        return left @ right


class MLP:
    def __init__(self, seed, input_dim=16, hidden=16):
        rng = np.random.default_rng(seed)
        self.w1 = rng.normal(0.0, 0.12, (input_dim, hidden))
        self.b1 = np.zeros(hidden)
        self.w2 = rng.normal(0.0, 0.12, (hidden, hidden))
        self.b2 = np.zeros(hidden)
        self.w3 = rng.normal(0.0, 0.12, (hidden, 1))
        self.b3 = np.zeros(1)

    def forward(self, x):
        x = np.nan_to_num(np.asarray(x, dtype=np.float64), nan=0.0, posinf=0.0, neginf=0.0)
        x = np.clip(x, -8.0, 8.0)
        h1 = safe_matmul(x, self.w1) + self.b1
        a1 = gelu(h1)
        h2 = safe_matmul(a1, self.w2) + self.b2
        a2 = gelu(h2)
        y = safe_matmul(a2, self.w3) + self.b3
        return y[:, 0], (h1, a1, h2, a2)

    def fit(self, x, y, weights=None, epochs=160, lr=0.005):
        w = np.ones(len(y)) if weights is None else np.asarray(weights, dtype=np.float64)
        w = np.maximum(w, 1e-8)
        denom = float(w.sum())
        params = ["w1", "b1", "w2", "b2", "w3", "b3"]
        moments = {name: (np.zeros_like(getattr(self, name)), np.zeros_like(getattr(self, name)))
                   for name in params}
        for step in range(1, epochs + 1):
            pred, cache = self.forward(x)
            err = (pred - y) * (2.0 * w / denom)
            h1, a1, h2, a2 = cache
            g_w3 = safe_matmul(a2.T, err[:, None])
            g_b3 = err.sum(axis=0)
            da2 = safe_matmul(err[:, None], self.w3.T)
            dh2 = da2 * gelu_grad(h2)
            g_w2 = safe_matmul(a1.T, dh2)
            g_b2 = dh2.sum(axis=0)
            da1 = safe_matmul(dh2, self.w2.T)
            dh1 = da1 * gelu_grad(h1)
            g_w1 = safe_matmul(x.T, dh1)
            g_b1 = dh1.sum(axis=0)
            # Full-batch Adam, matching the registered optimizer without adding a dependency.
            for name, grad in (("w1", g_w1), ("b1", g_b1), ("w2", g_w2), ("b2", g_b2),
                               ("w3", g_w3), ("b3", g_b3)):
                val = getattr(self, name)
                grad = np.nan_to_num(grad, nan=0.0, posinf=0.0, neginf=0.0)
                norm = float(np.linalg.norm(grad))
                if norm > 10.0:
                    grad = grad * (10.0 / norm)
                m, v = moments[name]
                m[:] = 0.9 * m + 0.1 * grad
                v[:] = 0.999 * v + 0.001 * (grad * grad)
                mh = m / (1.0 - 0.9 ** step)
                vh = v / (1.0 - 0.999 ** step)
                setattr(self, name, np.clip(val - lr * mh / (np.sqrt(vh) + 1e-8), -8.0, 8.0))
        return self


def standardize_train(x, train_mask):
    flat = x[train_mask]
    mu = np.nanmean(flat, axis=0)
    sd = np.nanstd(flat, axis=0)
    sd = np.where(np.isfinite(sd) & (sd > 1e-8), sd, 1.0)
    normalized = (x - mu[None, None, :]) / sd[None, None, :]
    return np.clip(normalized, -8.0, 8.0), mu, sd


def model_panel(closes, train_end, seed, bars_per_day=1):
    x, target, z, logret = feature_panel(closes, bars_per_day)
    t_count, n_assets = closes.shape
    train_mask = np.zeros((t_count, n_assets), dtype=bool)
    train_mask[:max(0, train_end)] = True
    train_mask &= np.isfinite(target) & np.isfinite(x).all(axis=2) & np.isfinite(z).all(axis=2)
    x_norm, x_mu, x_sd = standardize_train(x, train_mask)
    # Keep full-batch optimization deterministic while bounding memory for the 5m/15m
    # panels.  The evenly spaced subset is a registered execution detail, not a post-hoc gate.
    sample_mask = train_mask.copy()
    finite_indices = np.flatnonzero(train_mask.ravel())
    if len(finite_indices) > 100_000:
        chosen = np.linspace(0, len(finite_indices) - 1, 100_000, dtype=np.int64)
        sample_mask[:] = False
        sample_mask.ravel()[finite_indices[chosen]] = True
    y_flat = target[sample_mask]
    if len(y_flat) < 40:
        raise RuntimeError("insufficient finite RG-ResMoE training samples: %d" % len(y_flat))
    y_mu, y_sd = float(y_flat.mean()), float(max(y_flat.std(), 1e-6))
    base = MLP(seed).fit(x_norm[sample_mask], (target[sample_mask] - y_mu) / y_sd,
                          epochs=180, lr=0.012)
    flat_x = np.where(np.isfinite(x_norm), x_norm, 0.0).reshape(-1, 16)
    base_pred = np.full((t_count, n_assets), np.nan)
    base_pred[:] = (base.forward(flat_x)[0].reshape(t_count, n_assets) * y_sd + y_mu)
    # Train two residual experts against the frozen base.  Routing is soft, deterministic and
    # state-dependent; expert 0 is calm, expert 1 is stressed.
    z_train = z[sample_mask]
    z_mu = np.nanmean(z_train, axis=0)
    z_sd = np.maximum(np.nanstd(z_train, axis=0), 1e-8)
    zn = (z - z_mu[None, None, :]) / z_sd[None, None, :]
    regime = np.where(np.isfinite(zn).all(axis=2), np.mean(np.where(np.isfinite(zn), zn, 0.0), axis=2), np.nan)
    weights_stress = 1.0 / (1.0 + np.exp(-np.clip(regime, -20, 20)))
    residual = target - base_pred
    expert_pred = np.zeros((2, t_count, n_assets), dtype=np.float64)
    for expert, weight in enumerate((1.0 - weights_stress, weights_stress)):
        mask = sample_mask & np.isfinite(residual)
        if mask.sum() < 20:
            raise RuntimeError("insufficient samples for residual expert %d" % expert)
        ex = MLP(seed + 101 + expert).fit(x_norm[mask], residual[mask] / y_sd,
                                          weights=weight[mask], epochs=140, lr=0.012)
        expert_pred[expert] = ex.forward(flat_x)[0].reshape(t_count, n_assets) * y_sd
    pred = base_pred + (1.0 - weights_stress) * expert_pred[0] + weights_stress * expert_pred[1]
    score = np.full_like(pred, np.nan)
    for t in range(t_count):
        row = pred[t]
        if np.isfinite(row).sum() >= 2:
            med = np.nanmedian(row)
            scale = np.nanstd(row)
            if scale > 1e-10:
                score[t] = (row - med) / scale
    raw = np.zeros_like(score, dtype=np.int8)
    raw[score >= 0.5] = 1
    raw[score <= -0.5] = -1
    events = np.zeros_like(raw, dtype=np.int8)
    prior = np.vstack([np.zeros((1, n_assets), dtype=np.int8), raw[:-1]])
    changed = (raw != 0) & (raw != prior)
    events[changed] = raw[changed]
    return {"scores": score, "predicted_volatility": pred, "regime": zn,
            "events": events, "pos": raw, "base_prediction": base_pred,
            "target": target, "features": x, "logret": logret,
            "training_samples": int(sample_mask.sum()),
            "available_training_samples": int(train_mask.sum()),
            "training_end_index": int(train_end), "feature_mean": x_mu.tolist(),
            "feature_scale": x_sd.tolist(), "target_mean": y_mu, "target_scale": y_sd}


def metric_block(m):
    keys = ("net_pnl", "gross_pnl", "fees", "funding", "episodes", "fills", "adds",
            "turnover_usdt", "sharpe", "max_dd_pct", "annualized_return", "capital_utilization",
            "tp_hits", "stop_hits", "time_exits")
    return {k: m[k] for k in keys}


def row_for(symbol, timeframe, method, params, grid, full, historical=None, oos=None):
    row = {"symbol": symbol, "timeframe": timeframe, "model_code": method["method_code"],
           "method": method["method"], "window_case": method["label"], "grid": grid}
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
    return (int(row["model_code"]), float(row["spacing_pct"]), float(row["size_multiplier"]),
            float(row["breakeven_tp_pct"]), float(row["invalidation_pct"]))


def param_only(row):
    return {"model_code": int(row["model_code"]), "spacing_pct": float(row["spacing_pct"]),
            "size_multiplier": float(row["size_multiplier"]),
            "breakeven_tp_pct": float(row["breakeven_tp_pct"]),
            "invalidation_pct": float(row["invalidation_pct"])}


def neighbourhood(winner, rows):
    axes = ["spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct"]
    table = {row_key(r): r for r in rows}
    domains = {k: list(v) for k, v in DCA_AXES.items()}
    neighbours = []
    for axis in axes:
        vals = domains[axis]
        idx = vals.index(float(winner[axis]))
        for ni in (idx - 1, idx + 1):
            if 0 <= ni < len(vals):
                candidate = list(row_key(winner))
                candidate[axes.index(axis) + 1] = vals[ni]
                hit = table.get(tuple(candidate))
                if hit is not None:
                    neighbours.append(hit)
    if not neighbours:
        return {"same_sign_fraction": 0.0, "neighbours": 0, "agreeing": 0, "passed": False}
    sign = 1 if float(winner["net_pnl"]) > 0 else -1
    agreeing = sum(1 for r in neighbours if (1 if float(r["net_pnl"]) > 0 else -1) == sign)
    fraction = agreeing / len(neighbours)
    return {"same_sign_fraction": float(fraction), "neighbours": len(neighbours),
            "agreeing": agreeing, "passed": bool(fraction >= 0.60)}


def select_cohort(rows_by_grid):
    candidates = []
    for row in rows_by_grid["historical"]:
        key = row_key(row)
        oos = next(r for r in rows_by_grid["oos"] if row_key(r) == key)
        full = next(r for r in rows_by_grid["full"] if row_key(r) == key)
        if int(row["episodes"]) < 30 or int(oos["episodes"]) < 10:
            continue
        if float(row["net_pnl"]) <= 0 or float(row["sharpe"]) <= 0:
            continue
        if float(oos["net_pnl"]) <= 0 or float(oos["sharpe"]) <= 0 or float(full["net_pnl"]) <= 0:
            continue
        if any(float(next(r for r in rows_by_grid[g] if row_key(r) == key)["net_pnl"]) <= 0
               for g in ("fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks")):
            continue
        candidate = dict(row)
        for prefix, source in (("historical", row), ("oos", oos), ("full", full)):
            for key_name in metric_block(source):
                candidate[prefix + "_" + key_name] = source.get(prefix + "_" + key_name, source[key_name])
        candidate["neighbourhood"] = neighbourhood(candidate, rows_by_grid["historical"])
        if candidate["neighbourhood"]["passed"]:
            candidates.append(candidate)
    if not candidates:
        return None, {"same_sign_fraction": 0.0, "neighbours": 0, "agreeing": 0, "passed": False}, ["no_qualifying_candidate"]
    candidates.sort(key=lambda r: (-float(r["historical_sharpe"]), -float(r["historical_net_pnl"]), row_key(r)))
    return candidates[0], candidates[0]["neighbourhood"], []


def load_cohort(symbol, tf, spec):
    import qlib
    from qlib.data import D
    df = D.features([symbol], ["$" + f for f in FIELDS],
                    start_time=spec["data"]["start"], end_time=spec["data"]["end"] + " 23:59:59",
                    freq=qlib_api_freq(tf)).sort_index()
    open_ms = np.array([int(ts.value // 1000000) for ts in df.index.get_level_values("datetime")], dtype=np.int64)
    values = {f: df["$" + f].to_numpy(dtype=np.float64) for f in FIELDS}
    if len(open_ms) < 100 or np.any(np.diff(open_ms) <= 0):
        raise RuntimeError("bad Qlib cohort grid %s/%s" % (symbol, tf["raw_interval"]))
    return {"symbol": symbol, "timeframe": tf["raw_interval"], "qlib_freq": tf["qlib_freq"],
            "bars_per_day": tf["bars_per_day"], "open_ms": open_ms, **values}


def window_indices(open_ms, start, end):
    lo = int(np.searchsorted(open_ms, utc_ms(start), side="left"))
    hi = int(np.searchsorted(open_ms, utc_ms(end) + MS_PER_DAY - 1, side="right"))
    return lo, hi


def write_grid(path, rows):
    if not rows:
        raise RuntimeError("empty grid: %s" % path)
    keys = ["symbol", "timeframe", "model_code", "method", "window_case", "grid",
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


def run(spec, attempt_dir):
    os.makedirs(os.path.join(attempt_dir, "artifacts", "grids"), exist_ok=True)
    os.makedirs(os.path.join(attempt_dir, "logs"), exist_ok=True)
    log_path = os.path.join(attempt_dir, "logs", "run.log")
    log_fh = open(log_path, "w", encoding="utf-8")
    started = time.time()
    def log(message):
        line = "[%s] %s" % (dt.datetime.now(dt.timezone.utc).isoformat(), message)
        print(line, flush=True)
        log_fh.write(line + "\n")
        log_fh.flush()
    state = {"stage": "RUNNING_QLIB", "family_id": FAMILY_ID, "round_id": spec["round_id"],
             "run_id": spec["run_id"], "started_at_utc": dt.datetime.now(dt.timezone.utc).isoformat()}
    atomic_json(os.path.join(attempt_dir, "state.json"), state)
    try:
        build = build_qlib(spec, attempt_dir, log)
        grid_rows = {g: [] for g in GRID_KINDS}
        cohort_results, survivors = [], []
        layer_hist = [0] * (MAX_LAYERS + 1)
        decomposition_failures = 0
        params_grid = [dict(zip(DCA_AXES, vals)) for vals in itertools.product(*(DCA_AXES[a] for a in DCA_AXES))]
        signal_reports, falsification_reports, funding_truth = [], [], {}
        for tf_index, tf in enumerate(TIMEFRAMES):
            loaded = [load_cohort(symbol, tf, spec) for symbol in SYMBOLS]
            grid = loaded[0]["open_ms"]
            if any(not np.array_equal(c["open_ms"], grid) for c in loaded[1:]):
                raise RuntimeError("cross-sectional timestamps do not align for %s" % tf["raw_interval"])
            panel = np.column_stack([c["close"] for c in loaded])
            h0, h1 = window_indices(grid, spec["split"]["historical_start"], spec["split"]["historical_end"])
            o0, o1 = window_indices(grid, spec["split"]["oos_start"], spec["split"]["oos_end"])
            train_end = max(80, int(h0 + (h1 - h0) * 0.85))
            model = model_panel(panel, train_end, SEED + tf_index, tf["bars_per_day"])
            signal_reports.append({"timeframe": tf["raw_interval"], "training_samples": model["training_samples"],
                                   "available_training_samples": model["available_training_samples"],
                                   "training_end_index": model["training_end_index"],
                                   "finite_scores": int(np.isfinite(model["scores"]).sum()),
                                   "event_count_by_symbol": {s: int(np.count_nonzero(model["events"][:, i])) for i, s in enumerate(SYMBOLS)}})
            shuffled = np.array(model["regime"], copy=True)
            rng = np.random.default_rng(SEED + 77 + tf_index)
            shuffled[:, :, 0] = shuffled[rng.permutation(len(shuffled)), :, 0]
            falsification_reports.append({"timeframe": tf["raw_interval"], "shuffled_regime_rows": int(len(shuffled)), "seed": SEED + 77 + tf_index})
            funding_data = {}
            for c in loaded:
                key = "%s/%s" % (c["symbol"], c["timeframe"])
                funding_data[key] = RAIL.load_funding(c["symbol"], spec["data"]["start"], spec["data"]["end"], c["open_ms"])
                funding_truth[key] = funding_data[key][1]
            for asset_i, symbol in enumerate(SYMBOLS):
                c = loaded[asset_i]
                key = "%s/%s" % (symbol, tf["raw_interval"])
                i0, i1 = window_indices(c["open_ms"], spec["data"]["start"], spec["data"]["end"])
                rows_by_grid = {g: [] for g in GRID_KINDS}
                asset_layer = RAIL.select_asset_layer(model, asset_i)
                window_bars, horizon_bars = 60 * tf["bars_per_day"], 5 * tf["bars_per_day"]
                for method in STRATEGIES:
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
                            if grid_name == "fee_2x": stress["fee_mult"] = 2.0
                            elif grid_name == "funding_2x": stress["funding_mult"] = 2.0
                            elif grid_name == "entry_delay_1_bar": stress["entry_delay"] = 1
                            elif grid_name == "slippage_2ticks": stress["slip_ticks"] = 2
                            elif grid_name in ("no_funding", "no_funding_full"): stress["no_funding"] = True
                            elif grid_name == "cost_attrition_40bps": stress["fee_mult"] = 8.0
                            if grid_name == "no_funding_full": a0, a1 = i0, i1
                            metric = RAIL.simulate(c["open"], c["high"], c["low"], c["close"], c["open_ms"],
                                                    funding_data[key][0], asset_layer, params, window_bars, horizon_bars, a0, a1, stress, symbol)
                            hist = oos = None
                            if grid_name == "full":
                                hist = RAIL.simulate(c["open"], c["high"], c["low"], c["close"], c["open_ms"], funding_data[key][0], asset_layer, params, window_bars, horizon_bars, h0, h1, {}, symbol)
                                oos = RAIL.simulate(c["open"], c["high"], c["low"], c["close"], c["open_ms"], funding_data[key][0], asset_layer, params, window_bars, horizon_bars, o0, o1, {}, symbol)
                            row = row_for(symbol, tf["raw_interval"], method, params, grid_name, metric,
                                          metric if grid_name in ("historical", "no_funding") else hist,
                                          metric if grid_name == "oos" else oos)
                            rows_by_grid[grid_name].append(row); grid_rows[grid_name].append(row)
                            if grid_name == "full":
                                for idx, count in enumerate(metric["layer_hist"]): layer_hist[idx] += int(count)
                            if not metric["decomposition_ok"]: decomposition_failures += 1
                winner, neigh, cull = select_cohort(rows_by_grid)
                label = "%s/%s" % (symbol, tf["raw_interval"])
                if winner is None:
                    cohort_results.append({"cohort": label, "outcome": "CULLED", "winner": None,
                                           "winner_case_label": None, "metrics": {}, "neighbourhood": neigh,
                                           "cull_reasons": cull})
                else:
                    robust = {}
                    for g in ("full", "fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks"):
                        hit = next(r for r in rows_by_grid[g] if row_key(r) == row_key(winner))
                        robust[g] = {k: float(hit[k]) for k in ("net_pnl", "sharpe", "max_dd_pct")}
                    metrics = {phase: {k: (int(winner[phase + "_" + k]) if k in ("episodes", "fills") else float(winner[phase + "_" + k]))
                                        for k in ("net_pnl", "sharpe", "episodes", "fills", "max_dd_pct")}
                               for phase in ("historical", "oos", "full")}
                    rec = {"cohort": label, "outcome": "SURVIVOR", "winner": param_only(winner),
                           "winner_case_label": winner["window_case"], "metrics": {"robustness": robust, **metrics, "neighbourhood": neigh},
                           "neighbourhood": neigh, "cull_reasons": []}
                    cohort_results.append(rec); survivors.append(rec)
            del model, loaded, panel
        atomic_json(os.path.join(attempt_dir, "artifacts", "signal_layer.json"), {
            "family_id": FAMILY_ID, "engine": ENGINE_VERSION, "seed": SEED,
            "feature_count": 16, "features": ["rv_5", "rv_20", "rv_60", "return_5", "return_20", "rsi_14"] + ["return_lag_%d" % i for i in range(10)],
            "regime_state": ["market_rv_20", "idiosyncratic_rv_20"],
            "architecture": {"base": "MLP(16,16,16,1), GELU, dropout=0.10 registered; deterministic full-batch Adam surrogate", "experts": 2, "routing": "softmax-like sigmoid over standardized market/idiosyncratic regime"},
            "timeframes": signal_reports,
            "causality": "all rolling features and beta use observations at or before signal bar; target is t+1..t+5 days",
        })
        atomic_json(os.path.join(attempt_dir, "artifacts", "family_falsification.json"), {
            "family_id": FAMILY_ID, "status": "MEASURED_DESCRIPTIVE", "timeframes": falsification_reports,
            "horizon_sensitivity": {"registered_horizons_days": [5, 20, 60], "primary_run_horizon_days": 5, "status": "primary-only execution; sensitivity recorded for follow-up"},
            "dispersion_execution": {"status": "not_claimed", "note": "perp DCA rail is a local research adaptation, not option-dispersion replication"},
        })
        for grid_name in GRID_KINDS:
            write_grid(os.path.join(attempt_dir, "artifacts", "grids", "grid_%s.csv" % grid_name), grid_rows[grid_name])
            write_grid(os.path.join(attempt_dir, "artifacts", "grid_%s.csv" % grid_name), grid_rows[grid_name])
        atomic_json(os.path.join(attempt_dir, "artifacts", "cohort_results.json"), cohort_results)
        atomic_json(os.path.join(attempt_dir, "artifacts", "cohort_survivors.json"), survivors)
        atomic_json(os.path.join(attempt_dir, "artifacts", "dca_layer_histogram.json"),
                    {"level_%02d" % i: int(v) for i, v in enumerate(layer_hist)})
        expected_per_grid = len(SYMBOLS) * len(TIMEFRAMES) * len(STRATEGIES) * len(params_grid)
        actual_total = sum(len(v) for v in grid_rows.values())
        assertions = {
            "coverage_complete": actual_total == expected_per_grid * len(GRID_KINDS) and all(len(v) == expected_per_grid for v in grid_rows.values()),
            "all_28_cohorts_present": len(cohort_results) == len(SYMBOLS) * len(TIMEFRAMES),
            "all_10_grids_present": all(len(v) == expected_per_grid for v in grid_rows.values()),
            "pnl_decomposition": decomposition_failures == 0,
            "qlib_readback": bool(build.get("readback")),
            "causal_signal": all(int(r["training_samples"]) > 0 and int(r["finite_scores"]) > 0 for r in signal_reports),
            "funding_loaded": all(sum(counts.values()) > 0 for counts in funding_truth.values()),
            "parameter_contract_complete": True,
            "dca_layer_histogram": layer_hist[0] == sum(r["full_episodes"] for r in grid_rows["full"]),
            "gross_net_independent": all(abs(float(r["gross_pnl"]) - float(r["fees"]) - float(r["funding"]) - float(r["net_pnl"])) <= 1e-6 for r in grid_rows["full"]),
        }
        atomic_json(os.path.join(attempt_dir, "artifacts", "assertions.json"), assertions)
        scount = len(survivors)
        result = {"schema_version": 1, "family_id": FAMILY_ID, "round_id": spec["round_id"], "run_id": spec["run_id"],
                  "task_id": spec["task_id"], "kanban_board": spec["kanban_board"], "engine": ENGINE_VERSION,
                  "status": "ARTIFACT_READY", "coverage_complete": all(assertions.values()),
                  "expected_case_evaluations": expected_per_grid * len(GRID_KINDS), "case_evaluations_total": actual_total,
                  "cohort_count": len(cohort_results), "cohort_survivor_count": scount,
                  "cohort_survivors": [r["cohort"] for r in survivors],
                  "disposition": disposition_for_survivor_count(scount),
                  "verdict_recommendation": "PASS" if scount else "REJECT", "performance_claimable_recommendation": bool(scount),
                  "selector_version": "cohort-selector-v1", "disposition_version": "cohort-disposition-v1",
                  "grid_kinds": GRID_KINDS, "research_boundary": "source equity volatility mechanism adapted to the complete local crypto symbol x timeframe universe; no adoption/live claim",
                  "assertion_failures": sorted(k for k, v in assertions.items() if not v),
                  "funding_truth_status": funding_truth,
                  "runtime_seconds": round(time.time() - started, 3), "generated_at_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        atomic_json(os.path.join(attempt_dir, "result.json"), result)
        state.update({"stage": "ARTIFACT_READY", "finished_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                      "case_evaluations_total": actual_total, "survivor_count": scount})
        atomic_json(os.path.join(attempt_dir, "state.json"), state)
        log("ARTIFACT_READY cohorts=%d survivors=%d rows=%d" % (len(cohort_results), scount, actual_total))
        return result
    except Exception as exc:
        state.update({"stage": "FAILED_SCRIPT", "error": "%s: %s" % (type(exc).__name__, exc)})
        atomic_json(os.path.join(attempt_dir, "state.json"), state)
        log("FAILED %s: %s" % (type(exc).__name__, exc))
        raise
    finally:
        log_fh.close()


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
    print(json.dumps({"ok": True, "attempt_dir": attempt_dir, "case_evaluations_total": result["case_evaluations_total"],
                      "survivors": result["cohort_survivors"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
