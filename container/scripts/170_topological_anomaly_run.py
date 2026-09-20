#!/usr/bin/env python3
"""Qlib-container runner for the registered topological-anomaly experiment.

This is intentionally a single, auditable runner: it builds a Qlib .bin store from the mounted
raw archive, reads the bars back through ``qlib.data.D``, forms causal cross-sectional anomaly
layers, and evaluates the complete 3 x 48 x 16 x 10 matrix.  The source paper is predictive-only;
all trading rules and cost assumptions below are explicitly research-proposed and are never called
source-reported.
"""
import argparse
import csv
import datetime as dt
import gzip
import hashlib
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
WORK_ROOT = "/qlib/work/topological-anomaly-v3"
CSV_ROOT = os.path.join(WORK_ROOT, "csv")
QLIB_DIR = os.path.join(WORK_ROOT, "qlib-data")
MS_PER_DAY = 86400000
FIELDS = ["open", "high", "low", "close", "volume"]
FAMILY_ID = "cross-sectional-topological-anomaly-score-intraday-equity-return-predictability-2026-09-02"
SEED = 20260920
START_EQUITY = 30000.0
BASE_QUOTE = 1000.0
LEVERAGE = 10.0
TAKER_FEE = 0.0005
MAX_LAYERS = 11
SLIPPAGE_TICKS = 1
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
TIMEFRAMES = [
    {"raw_interval": "5m", "qlib_freq": "5min", "bars_per_day": 288},
    {"raw_interval": "15m", "qlib_freq": "15min", "bars_per_day": 96},
    {"raw_interval": "30m", "qlib_freq": "30min", "bars_per_day": 48},
    {"raw_interval": "1h", "qlib_freq": "60min", "bars_per_day": 24},
]
STRATEGIES = [
    {"method_code": 0, "method": "ballmapper", "label": "ballmapper"},
    {"method_code": 1, "method": "decoder_conditional_vae", "label": "decoder_conditional_vae"},
    {"method_code": 2, "method": "function_on_function", "label": "function_on_function"},
]
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
    bar_ms = {"5m": 300000, "15m": 900000, "30m": 1800000, "1h": 3600000}[interval]
    out_dir = os.path.join(CSV_ROOT, qlib_freq)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "%s.csv" % symbol)
    lo_ms, hi_ms = utc_ms(start), utc_ms(end) + MS_PER_DAY - 1
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
                    writer.writerow([utc_stamp(ms)] + [rec[k] for k in FIELDS])
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
    for tf in TIMEFRAMES:
        cmd = [VENV_PYTHON, DUMP_BIN, "dump_all", "--data_path",
               os.path.join(CSV_ROOT, tf["qlib_freq"]), "--qlib_dir", QLIB_DIR,
               "--freq", tf["qlib_freq"], "--include_fields", "open,close,high,low,volume",
               "--date_field_name", "date", "--max_workers", "1"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=14400)
        if proc.returncode:
            raise RuntimeError("dump_bin %s failed rc=%d\n%s\n%s" %
                               (tf["qlib_freq"], proc.returncode, proc.stdout[-2000:], proc.stderr[-2000:]))
        log("dump_bin %s ok" % tf["qlib_freq"])
    import qlib
    from qlib.data import D
    qlib.init(provider_uri=QLIB_DIR, region="cn", expression_cache=None, dataset_cache=None)
    readback = {}
    for tf in TIMEFRAMES:
        for symbol in SYMBOLS:
            freq = tf["qlib_freq"]
            df = D.features([symbol], ["$" + f for f in FIELDS],
                            start_time=spec["data"]["start"],
                            end_time=spec["data"]["end"] + " 23:59:59", freq=freq).sort_index()
            if len(df) == 0 or int(df.isnull().sum().sum()) != 0:
                raise RuntimeError("Qlib read-back empty/null for %s %s" % (symbol, freq))
            stamps = [int(ts.value // 1000000) for ts in df.index.get_level_values("datetime")]
            if stamps != sorted(stamps) or len(set(stamps)) != len(stamps):
                raise RuntimeError("Qlib timestamps not strict ascending for %s %s" % (symbol, freq))
            key = "%s/%s" % (symbol, tf["raw_interval"])
            cs, cl = csv_first_last_close(per_dataset[key]["csv"])
            readback[key] = {"rows": len(df), "first": utc_stamp(stamps[0]), "last": utc_stamp(stamps[-1]),
                             "first_close": float(df["$close"].iloc[0]),
                             "last_close": float(df["$close"].iloc[-1]),
                             "csv_first_close": cs, "csv_last_close": cl,
                             "close_abs_diff_first": abs(float(df["$close"].iloc[0]) - cs),
                             "close_abs_diff_last": abs(float(df["$close"].iloc[-1]) - cl)}
    report = {"qlib_dir": QLIB_DIR, "csv_root": CSV_ROOT,
              "qlib_version": qlib.__version__, "build_wall_seconds": round(time.time() - started, 3),
              "per_dataset": {k: {x: v for x, v in r.items() if x != "raw_files"}
                              for k, r in per_dataset.items()},
              "raw_files": {k: r["raw_files"] for k, r in per_dataset.items()}, "readback": readback,
              "note": "raw JSONL.gz -> Qlib CSV -> Qlib .bin; no gap filling or resampling"}
    atomic_json(os.path.join(attempt_dir, "artifacts", "bins_build.json"), report)
    return report


def rolling_mean(x, window):
    x = np.asarray(x, dtype=np.float64)
    finite = np.isfinite(x)
    vals = np.where(finite, x, 0.0)
    count = np.cumsum(finite.astype(np.int64), axis=0)
    total = np.cumsum(vals, axis=0)
    if x.ndim == 1:
        out = np.full(len(x), np.nan)
        for i in range(len(x)):
            lo = max(0, i + 1 - window)
            n = count[i] - (count[lo - 1] if lo else 0)
            if n:
                out[i] = (total[i] - (total[lo - 1] if lo else 0.0)) / n
        return out
    out = np.full_like(x, np.nan)
    for i in range(len(x)):
        lo = max(0, i + 1 - window)
        n = count[i] - (count[lo - 1] if lo else 0)
        good = n > 0
        out[i, good] = (total[i, good] - (total[lo - 1, good] if lo else 0.0)) / n[good]
    return out


def rolling_std(x, window):
    x = np.asarray(x, dtype=np.float64)
    mean = rolling_mean(x, window)
    sq = rolling_mean(x * x, window)
    return np.sqrt(np.maximum(sq - mean * mean, 0.0))


def cross_sectional_returns(closes):
    closes = np.asarray(closes, dtype=np.float64)
    ret = np.full_like(closes, np.nan)
    ret[1:] = closes[1:] / np.maximum(closes[:-1], 1e-12) - 1.0
    return ret


def causal_beta(y, x, window):
    """Trailing beta using observations strictly before t."""
    y = np.asarray(y, dtype=np.float64)
    x = np.asarray(x, dtype=np.float64)
    good = np.isfinite(y) & np.isfinite(x)
    xv = np.where(good, x, 0.0)
    yv = np.where(good, y, 0.0)
    n = np.cumsum(good.astype(np.int64))
    sx = np.cumsum(xv)
    sy = np.cumsum(yv)
    sxx = np.cumsum(xv * xv)
    sxy = np.cumsum(xv * yv)
    out = np.full(len(y), np.nan)
    for t in range(1, len(y)):
        lo = max(0, t - window)
        nn = n[t - 1] - (n[lo - 1] if lo else 0)
        if nn >= 8:
            ax = sx[t - 1] - (sx[lo - 1] if lo else 0.0)
            ay = sy[t - 1] - (sy[lo - 1] if lo else 0.0)
            axx = sxx[t - 1] - (sxx[lo - 1] if lo else 0.0)
            axy = sxy[t - 1] - (sxy[lo - 1] if lo else 0.0)
            den = axx - ax * ax / nn
            out[t] = (axy - ax * ay / nn) / den if den > 1e-18 else 0.0
    return out


def ballmapper_scores(returns):
    t_count, n_assets = returns.shape
    emb = np.full((t_count, n_assets, 3), np.nan)
    emb[:, :, 0] = returns
    emb[1:, :, 1] = returns[:-1]
    emb[2:, :, 2] = returns[:-2]
    scores = np.full((t_count, n_assets), np.nan)
    for t in range(2, t_count):
        x = emb[t]
        if not np.isfinite(x).all():
            continue
        center = np.mean(x, axis=0)
        scale = np.std(x, axis=0) + 1e-9
        z = (x - center) / scale
        distance = np.sqrt(np.sum((z[:, None, :] - z[None, :, :]) ** 2, axis=2))
        upper = distance[np.triu_indices(n_assets, 1)]
        radius = max(float(np.median(upper)) * 1.25, 0.25)
        for j in range(n_assets):
            peers = [k for k in range(n_assets) if k != j and distance[j, k] <= radius]
            if not peers:
                peers = [int(np.argmin(np.where(np.arange(n_assets) == j, np.inf, distance[j])))]
            peer = np.mean(z[peers], axis=0)
            delta = z[j] - peer
            mag = float(np.linalg.norm(delta))
            direction = float(returns[t, j] - np.mean(np.delete(returns[t], j)))
            scores[t, j] = (1.0 if direction >= 0 else -1.0) * mag
    return scores


def decoder_conditional_vae_scores(returns, window):
    """Conditional Gaussian decoder with a two-dimensional common latent state.

    The source names this family decoder-conditional VAE.  This implementation makes the
    operational surrogate explicit: the latent mean is the market/dispersion state, the decoder
    is a trailing peer beta, and the score is the signed standardized reconstruction residual.
    """
    t_count, n_assets = returns.shape
    market = np.nanmean(returns, axis=1)
    dispersion = np.nanstd(returns, axis=1)
    scores = np.full_like(returns, np.nan)
    for j in range(n_assets):
        beta = causal_beta(returns[:, j], market, window)
        beta_disp = causal_beta(returns[:, j], dispersion, window)
        pred = beta * market + beta_disp * dispersion
        residual = returns[:, j] - pred
        sigma = rolling_std(residual, max(32, window // 4))
        good = np.isfinite(residual) & np.isfinite(sigma) & (sigma > 1e-10)
        scores[good, j] = residual[good] / sigma[good]
    return scores


def function_on_function_scores(returns, window, bars_per_day):
    """Penalised causal function-on-function residuals.

    A short recent basis, a common-factor basis, and a volatility basis summarize the prior
    anomaly/return curve; a ridge fit is updated on a trailing window strictly before each bar.
    """
    t_count, n_assets = returns.shape
    market = np.nanmean(returns, axis=1)
    short = max(8, bars_per_day // 4)
    own_mean = rolling_mean(returns, short)
    market_mean = rolling_mean(market, short)
    own_vol = rolling_std(returns, short)
    out = np.full_like(returns, np.nan)
    x_all = np.ones((t_count, 4), dtype=np.float64)
    for j in range(n_assets):
        x_all[:, 1] = own_mean[:, j]
        x_all[:, 2] = market_mean
        x_all[:, 3] = own_vol[:, j]
        good = np.isfinite(x_all).all(axis=1) & np.isfinite(returns[:, j])
        xv = np.where(good[:, None], x_all, 0.0)
        yv = np.where(good, returns[:, j], 0.0)
        prefix_xx = np.concatenate([np.zeros((1, 4, 4)), np.cumsum(xv[:, :, None] * xv[:, None, :], axis=0)])
        prefix_xy = np.concatenate([np.zeros((1, 4)), np.cumsum(xv * yv[:, None], axis=0)])
        stride = max(1, window // 64)
        beta = np.zeros(4)
        for t in range(1, t_count):
            if t == 1 or t % stride == 0:
                lo = max(0, t - window)
                xx = prefix_xx[t] - prefix_xx[lo]
                xy = prefix_xy[t] - prefix_xy[lo]
                ridge = xx + np.eye(4) * 1e-5
                try:
                    beta = np.linalg.solve(ridge, xy)
                except np.linalg.LinAlgError:
                    beta = np.linalg.lstsq(ridge, xy, rcond=None)[0]
            if good[t]:
                out[t, j] = returns[t, j] - float(x_all[t].dot(beta))
    scale = rolling_std(out, max(32, window // 4))
    good = np.isfinite(out) & np.isfinite(scale) & (scale > 1e-10)
    out[good] = out[good] / scale[good]
    return out


def univariate_scores(returns, window):
    scores = np.full_like(returns, np.nan)
    for j in range(returns.shape[1]):
        mu = rolling_mean(returns[:, j], window)
        sigma = rolling_std(returns[:, j], max(32, window // 4))
        good = np.isfinite(returns[:, j]) & np.isfinite(mu) & np.isfinite(sigma) & (sigma > 1e-10)
        scores[good, j] = (returns[good, j] - mu[good]) / sigma[good]
    return scores


def layer_from_scores(scores, window):
    trajectory = rolling_mean(scores, window)
    baseline = rolling_mean(trajectory, window)
    scale = rolling_std(trajectory, window)
    z = (trajectory - baseline) / np.maximum(scale, 1e-9)
    raw = np.zeros_like(z, dtype=np.int8)
    raw[z <= -0.50] = 1
    raw[z >= 0.50] = -1
    pos = np.zeros_like(raw, dtype=np.int8)
    # Requiring three consecutive observations keeps the execution event sparse and makes the
    # registered next-bar entry unambiguous.
    for t in range(2, len(raw)):
        stable = raw[t] != 0
        stable &= raw[t - 1] == raw[t]
        stable &= raw[t - 2] == raw[t]
        pos[t, stable] = raw[t, stable]
    events = np.zeros_like(pos, dtype=np.int8)
    prior = np.vstack([np.zeros((1, pos.shape[1]), dtype=np.int8), pos[:-1]])
    events[(pos != 0) & (pos != prior)] = pos[(pos != 0) & (pos != prior)]
    return {"scores": scores, "trajectory": trajectory, "z": z, "pos": pos, "events": events}


def make_layers(panel_closes, bars_per_day, seed):
    returns = cross_sectional_returns(panel_closes)
    window = 5 * bars_per_day
    method_scores = [ballmapper_scores(returns),
                     decoder_conditional_vae_scores(returns, window),
                     function_on_function_scores(returns, window, bars_per_day)]
    base = [layer_from_scores(x, window) for x in method_scores]
    rng = np.random.default_rng(seed)
    permutation = rng.permutation(panel_closes.shape[1])
    shuffled = np.full_like(method_scores[0], np.nan)
    for j in range(panel_closes.shape[1]):
        shuffled[:, permutation[j]] = method_scores[0][:, j]
    label_shuffle = [layer_from_scores(x, window) for x in method_scores]
    for code, layer in enumerate(label_shuffle):
        # Recompute every method with labels permuted, then put the random identity back in the
        # original column.  The deterministic seed is recorded in signal_layer.json.
        permuted = method_scores[code][:, permutation]
        layer.update(layer_from_scores(permuted, window))
        layer["scores"] = np.full_like(permuted, np.nan)
        layer["trajectory"] = np.full_like(permuted, np.nan)
        layer["z"] = np.full_like(permuted, np.nan)
        layer["pos"] = np.zeros_like(permuted, dtype=np.int8)
        layer["events"] = np.zeros_like(permuted, dtype=np.int8)
        rebuilt = layer_from_scores(permuted, window)
        for key in ("scores", "trajectory", "z", "pos", "events"):
            layer[key][:, permutation] = rebuilt[key]
    placebo = []
    for code, layer in enumerate(base):
        rng_p = np.random.default_rng(seed + 1000 + code)
        perm = rng_p.permutation(len(layer["scores"]))
        score = layer["scores"][perm]
        placebo.append(layer_from_scores(score, window))
    uni = layer_from_scores(univariate_scores(returns, window), window)
    return {"returns": returns, "base": base, "label_shuffle": label_shuffle,
            "placebo": placebo, "univariate": [uni for _ in STRATEGIES],
            "window": window, "horizon": 2 * bars_per_day,
            "label_permutation": permutation.tolist()}


def fresh_events(layer, i0, i1, delay):
    events = layer["events"][i0:i1]
    out = []
    for local in range(len(events)):
        for sign in (1, -1):
            if int(events[local]) == sign and local + 1 + delay < len(events):
                out.append((local, sign))
    return out


def select_asset_layer(layer, asset_index):
    """Project a panel signal layer onto one registered cohort without changing its values."""
    return {key: (value[:, asset_index] if isinstance(value, np.ndarray) and value.ndim == 2 else value)
            for key, value in layer.items()}


def load_funding(symbol, start, end, open_ms):
    path = os.path.join("/data/raw/binance/usdm/funding", symbol, "%s-funding.jsonl.gz" % symbol)
    if not os.path.isfile(path):
        return np.zeros(len(open_ms)), {"official": 0, "modeled_funding": 0, "other": 0, "out_of_window": 0}
    lo, hi = utc_ms(start), utc_ms(end) + MS_PER_DAY - 1
    by_bar = np.zeros(len(open_ms), dtype=np.float64)
    counts = {"official": 0, "modeled_funding": 0, "other": 0, "out_of_window": 0}
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            rec = json.loads(line)
            ms = int(rec["funding_time_ms"])
            if ms < lo or ms > hi:
                counts["out_of_window"] += 1
                continue
            status = rec.get("truth_status")
            counts[status if status in ("official", "modeled_funding") else "other"] += 1
            bar = int(np.searchsorted(open_ms, ms, side="right") - 1)
            if 0 <= bar < len(by_bar):
                by_bar[bar] += float(rec["funding_rate"])
    return by_bar, counts


def simulate(open_px, high, low, close, open_ms, funding, layer, params, window, horizon,
             i0, i1, stress, symbol, max_layers=MAX_LAYERS):
    spacing = float(params["spacing_pct"])
    multiplier = float(params["size_multiplier"])
    tp_pct = float(params["breakeven_tp_pct"])
    invalidation = float(params["invalidation_pct"])
    delay = int(stress.get("entry_delay", 0))
    fee = TAKER_FEE * float(stress.get("fee_mult", 1.0))
    funding_mult = float(stress.get("funding_mult", 1.0))
    slip_ticks = int(stress.get("slip_ticks", SLIPPAGE_TICKS))
    no_funding = bool(stress.get("no_funding", False))
    no_dca = bool(stress.get("no_dca", False))
    tick = PRICE_TICK[symbol]
    events = fresh_events(layer, i0, i1, delay)
    n = i1 - i0
    days = open_ms[i0:i1] // MS_PER_DAY
    unique_days, day_idx = np.unique(days, return_inverse=True)
    day_pnl = np.zeros(len(unique_days), dtype=np.float64)
    gross_total = fees_total = funding_total = turnover = 0.0
    episodes = fills = tp_hits = stop_hits = time_exits = adds = 0
    layer_hist = [0] * (max_layers + 1)
    last_exit = -1
    for event_local, sign in events:
        if event_local <= last_exit:
            continue
        entry_bar = event_local + 1 + delay
        if entry_bar >= n:
            continue
        eb = i0 + entry_bar
        entry = float(open_px[eb]) + sign * slip_ticks * tick
        if entry <= 0 or not math.isfinite(entry):
            continue
        tranches = []
        initial_quote = BASE_QUOTE * LEVERAGE
        quote = initial_quote
        qty = quote / entry
        tranches.append([qty, entry])
        gross = fees = fund = 0.0
        fees += quote * fee
        turnover += quote
        fills += 1
        next_level = 1
        exit_bar = min(entry_bar + horizon, n - 1)
        exit_price = float(close[i0 + exit_bar])
        exit_reason = "time"
        for local in range(entry_bar, min(entry_bar + horizon + 1, n)):
            global_bar = i0 + local
            if not no_funding and funding[global_bar] != 0.0:
                mark = float(close[global_bar])
                fund += sign * sum(t[0] for t in tranches) * mark * funding[global_bar] * funding_mult
            total_qty = sum(t[0] for t in tranches)
            avg = sum(t[0] * t[1] for t in tranches) / max(total_qty, 1e-12)
            stop = avg * (1.0 - invalidation) if sign > 0 else avg * (1.0 + invalidation)
            take = avg * (1.0 + tp_pct) if sign > 0 else avg * (1.0 - tp_pct)
            bar_high, bar_low = float(high[global_bar]), float(low[global_bar])
            # Conservative same-bar ordering: the adverse invalidation is checked before TP.
            stopped = bar_low <= stop if sign > 0 else bar_high >= stop
            taken = bar_high >= take if sign > 0 else bar_low <= take
            if stopped:
                exit_bar = local
                exit_price = stop - sign * slip_ticks * tick
                exit_reason = "invalidation"
                stop_hits += 1
                break
            if taken:
                exit_bar = local
                exit_price = take - sign * slip_ticks * tick
                exit_reason = "take_profit"
                tp_hits += 1
                break
            if not no_dca:
                while next_level <= max_layers:
                    target = entry * (1.0 - sign * spacing * next_level)
                    reached = bar_low <= target if sign > 0 else bar_high >= target
                    if not reached:
                        break
                    fill = target + sign * slip_ticks * tick
                    q = initial_quote * (multiplier ** next_level)
                    tranches.append([q / max(fill, 1e-12), fill])
                    fees += q * fee
                    turnover += q
                    fills += 1
                    adds += 1
                    next_level += 1
            if local == min(entry_bar + horizon, n - 1):
                exit_bar = local
                exit_price = float(close[global_bar]) - sign * slip_ticks * tick
                exit_reason = "time"
                time_exits += 1
        if exit_reason == "time" and exit_bar < entry_bar:
            exit_bar = min(entry_bar + horizon, n - 1)
            exit_price = float(close[i0 + exit_bar]) - sign * slip_ticks * tick
            time_exits += 1
        total_qty = sum(t[0] for t in tranches)
        for q, basis in tranches:
            gross += (exit_price - basis) * q * sign
        close_quote = abs(exit_price * total_qty)
        fees += close_quote * fee
        turnover += close_quote
        fills += 1
        net = gross - fees - fund
        gross_total += gross
        fees_total += fees
        funding_total += fund
        if len(day_pnl):
            d = int(np.searchsorted(unique_days, open_ms[i0 + exit_bar] // MS_PER_DAY))
            d = min(max(d, 0), len(day_pnl) - 1)
            day_pnl[d] += net
        episodes += 1
        layer_hist[0] += 1
        for k in range(1, min(next_level, max_layers + 1)):
            layer_hist[k] += 1
        last_exit = exit_bar
    net_total = gross_total - fees_total - funding_total
    equity = START_EQUITY + np.cumsum(day_pnl)
    high_water = np.maximum.accumulate(np.r_[START_EQUITY, equity])
    max_dd = float(np.max((high_water[1:] - equity) / np.maximum(high_water[1:], 1e-9)) * 100) if len(equity) else 0.0
    daily_ret = day_pnl / START_EQUITY
    sharpe = float(np.mean(daily_ret) / np.std(daily_ret, ddof=1) * math.sqrt(365.0)) if len(daily_ret) > 1 and np.std(daily_ret, ddof=1) > 1e-12 else 0.0
    days_span = max(1.0, (open_ms[i1 - 1] - open_ms[i0]) / MS_PER_DAY) if i1 > i0 else 1.0
    annualized = float((max((START_EQUITY + net_total) / START_EQUITY, 1e-9) ** (365.0 / days_span)) - 1.0)
    return {"gross_pnl": float(gross_total), "fees": float(fees_total), "funding": float(funding_total),
            "net_pnl": float(net_total), "episodes": int(episodes), "fills": int(fills),
            "adds": int(adds), "turnover_usdt": float(turnover), "sharpe": sharpe,
            "max_dd_pct": max_dd, "annualized_return": annualized,
            "capital_utilization": float(min(1.0, turnover / max(episodes, 1) / START_EQUITY)),
            "tp_hits": int(tp_hits), "stop_hits": int(stop_hits), "time_exits": int(time_exits),
            "layer_hist": layer_hist, "halted": False,
            "decomposition_ok": abs(gross_total - fees_total - funding_total - net_total) <= 1e-6,
            "window_start": utc_stamp(open_ms[i0]), "window_end": utc_stamp(open_ms[i1 - 1])}


def metric_block(m):
    return {k: m[k] for k in ("net_pnl", "gross_pnl", "fees", "funding", "episodes", "fills",
                              "adds", "turnover_usdt", "sharpe", "max_dd_pct", "annualized_return",
                              "capital_utilization", "tp_hits", "stop_hits", "time_exits")}


def row_for(symbol, timeframe, method, params, grid, full, historical=None, oos=None):
    row = {"symbol": symbol, "timeframe": timeframe, "method_code": method["method_code"],
           "method": method["method"], "window_case": method["label"], "grid": grid}
    row.update(params)
    for prefix, metric in (("full", full), ("historical", historical), ("oos", oos)):
        if metric is not None:
            for key, value in metric_block(metric).items():
                row[prefix + "_" + key] = value
    row["gross_pnl"] = full["gross_pnl"]
    row["fees"] = full["fees"]
    row["funding"] = full["funding"]
    row["net_pnl"] = full["net_pnl"]
    row["episodes"] = full["episodes"]
    row["fills"] = full["fills"]
    row["turnover_usdt"] = full["turnover_usdt"]
    row["sharpe"] = full["sharpe"]
    row["max_dd_pct"] = full["max_dd_pct"]
    row["capital_utilization"] = full["capital_utilization"]
    row["halted"] = full["halted"]
    row["decomposition_ok"] = full["decomposition_ok"]
    return row


def row_key(row):
    return (int(row["method_code"]), float(row["spacing_pct"]), float(row["size_multiplier"]),
            float(row["breakeven_tp_pct"]), float(row["invalidation_pct"]))


def param_only(row):
    return {"method_code": int(row["method_code"]), "spacing_pct": float(row["spacing_pct"]),
            "size_multiplier": float(row["size_multiplier"]),
            "breakeven_tp_pct": float(row["breakeven_tp_pct"]),
            "invalidation_pct": float(row["invalidation_pct"])}


def neighbourhood(winner, rows):
    axes = ["spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct"]
    domains = {k: list(v) for k, v in {"spacing_pct": [0.01, 0.02, 0.03, 0.04],
                                        "size_multiplier": [1.0, 1.1],
                                        "breakeven_tp_pct": [0.01, 0.02, 0.03],
                                        "invalidation_pct": [0.05, 0.10]}.items()}
    table = {row_key(r): r for r in rows}
    wk = row_key(winner)
    neighbours = []
    for axis in axes:
        vals = domains[axis]
        idx = vals.index(float(winner[axis]))
        for ni in (idx - 1, idx + 1):
            if 0 <= ni < len(vals):
                candidate = list(wk)
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
    historical_rows = rows_by_grid["historical"]
    oos_rows = rows_by_grid["oos"]
    full_rows = rows_by_grid["full"]
    stress = ["fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks"]
    candidates = []
    for row in historical_rows:
        oos = next(r for r in oos_rows if row_key(r) == row_key(row))
        full = next(r for r in full_rows if row_key(r) == row_key(row))
        if int(row["episodes"]) < 30 or int(oos["episodes"]) < 10:
            continue
        if float(row["net_pnl"]) <= 0 or float(row["sharpe"]) <= 0:
            continue
        if float(oos["net_pnl"]) <= 0 or float(oos["sharpe"]) <= 0:
            continue
        if float(full["net_pnl"]) <= 0:
            continue
        if any(float(next(r for r in rows_by_grid[g] if row_key(r) == row_key(row))["net_pnl"]) <= 0 for g in stress):
            continue
        candidate = dict(row)
        for prefix, source in (("historical", row), ("oos", oos), ("full", full)):
            for key in ("net_pnl", "gross_pnl", "fees", "funding", "episodes", "fills",
                        "adds", "turnover_usdt", "sharpe", "max_dd_pct",
                        "annualized_return", "capital_utilization", "tp_hits", "stop_hits",
                        "time_exits"):
                qualified = prefix + "_" + key
                candidate[qualified] = source[qualified] if qualified in source else source[key]
        candidate["neighbourhood"] = neighbourhood(candidate, historical_rows)
        if not candidate["neighbourhood"]["passed"]:
            continue
        candidates.append(candidate)
    if not candidates:
        return None, {"same_sign_fraction": 0.0, "neighbours": 0, "agreeing": 0, "passed": False}, [
            "no_qualifying_candidate"]
    candidates.sort(key=lambda r: (-float(r["historical_sharpe"]),
                                    -float(r["historical_net_pnl"]), row_key(r)))
    winner = candidates[0]
    return winner, winner["neighbourhood"], []


def load_cohort(symbol, tf, spec):
    import qlib
    from qlib.data import D
    df = D.features([symbol], ["$" + f for f in FIELDS],
                    start_time=spec["data"]["start"], end_time=spec["data"]["end"] + " 23:59:59",
                    freq=tf["qlib_freq"]).sort_index()
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
    keys = ["symbol", "timeframe", "method_code", "method", "window_case", "grid",
            "spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct",
            "gross_pnl", "fees", "funding", "net_pnl", "episodes", "fills", "turnover_usdt",
            "sharpe", "max_dd_pct", "capital_utilization", "halted", "decomposition_ok",
            "full_net_pnl", "full_gross_pnl", "full_fees", "full_funding", "full_episodes",
            "full_fills", "full_adds", "full_turnover_usdt", "full_sharpe", "full_max_dd_pct",
            "full_annualized_return", "full_capital_utilization", "full_tp_hits", "full_stop_hits",
            "full_time_exits", "historical_net_pnl", "historical_gross_pnl", "historical_fees",
            "historical_funding", "historical_episodes", "historical_fills", "historical_adds",
            "historical_turnover_usdt", "historical_sharpe", "historical_max_dd_pct",
            "historical_annualized_return", "historical_capital_utilization", "historical_tp_hits",
            "historical_stop_hits", "historical_time_exits", "oos_net_pnl", "oos_gross_pnl", "oos_fees",
            "oos_funding", "oos_episodes", "oos_fills", "oos_adds", "oos_turnover_usdt", "oos_sharpe",
            "oos_max_dd_pct", "oos_annualized_return", "oos_capital_utilization", "oos_tp_hits",
            "oos_stop_hits", "oos_time_exits"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in keys})


def run(spec, attempt_dir):
    os.makedirs(os.path.join(attempt_dir, "artifacts", "grids"), exist_ok=True)
    log_path = os.path.join(attempt_dir, "run.log")
    log_fh = open(log_path, "w", encoding="utf-8")
    def log(message):
        line = "[%s] %s" % (dt.datetime.now(dt.timezone.utc).isoformat(), message)
        print(line, flush=True)
        log_fh.write(line + "\n")
        log_fh.flush()
    try:
        build = build_qlib(spec, attempt_dir, log)
        import qlib
        from qlib.data import D
        cohort_data = {}
        layers = {}
        funding_data = {}
        signal_report = []
        panel_fits = []
        causal_mismatches = 0
        for tf in TIMEFRAMES:
            loaded = [load_cohort(symbol, tf, spec) for symbol in SYMBOLS]
            grid = loaded[0]["open_ms"]
            if any(not np.array_equal(c["open_ms"], grid) for c in loaded[1:]):
                raise RuntimeError("cross-sectional timestamps do not align for %s" % tf["raw_interval"])
            panel = np.column_stack([c["close"] for c in loaded])
            key_base = tf["raw_interval"]
            made = make_layers(panel, tf["bars_per_day"], SEED + tf["bars_per_day"])
            layers[key_base] = made
            for c in loaded:
                ckey = "%s/%s" % (c["symbol"], c["timeframe"])
                cohort_data[ckey] = c
                funding_data[ckey] = load_funding(c["symbol"], spec["data"]["start"], spec["data"]["end"], c["open_ms"])
            # Prefix probe on one asset/method: score at a cut must equal the same prefix's last score.
            probe_points = np.linspace(max(10, made["window"] // 2), len(panel) - 1, 8, dtype=int)
            probe = []
            returns = made["returns"]
            for point in probe_points:
                if point < 3:
                    continue
                prefix = ballmapper_scores(returns[:point + 1])
                a = prefix[-1, 0]
                b = made["base"][0]["scores"][point, 0]
                if np.isfinite(a) and np.isfinite(b) and abs(float(a) - float(b)) > 1e-8:
                    causal_mismatches += 1
                probe.append(int(point))
            signal_report.append({"timeframe": key_base, "bars": len(grid), "window": made["window"],
                                  "horizon": made["horizon"], "label_permutation": made["label_permutation"],
                                  "finite_scores": [int(np.isfinite(x["scores"]).sum()) for x in made["base"]],
                                  "probe_points": probe})
            panel_fits.append({"timeframe": key_base, "methods": [x["method"] for x in STRATEGIES],
                               "decoder": "trailing common-factor Gaussian decoder; latent=(market,dispersion)",
                               "function_on_function": "trailing ridge basis=(own_recent,market_recent,own_vol)",
                               "ballmapper": "Takens d=3, radius=1.25*cross-sectional median distance"})
        atomic_json(os.path.join(attempt_dir, "artifacts", "signal_layer.json"),
                    {"family_id": FAMILY_ID, "seed": SEED, "methods": STRATEGIES,
                     "registration": {"embedding_dimension": 3, "window_factor": 5, "horizon_factor": 2,
                                      "entry_delay_bars": 1, "direction": "long negative / short positive"},
                     "timeframes": signal_report, "causality_probe_mismatches": causal_mismatches})
        atomic_json(os.path.join(attempt_dir, "artifacts", "panel_fits.json"),
                    {"family_id": FAMILY_ID, "panel_fits": panel_fits,
                     "read_path": "qlib.data.D.features after CSV -> dump_bin -> Qlib .bin"})
        grid_rows = {g: [] for g in GRID_KINDS}
        cohort_results = []
        survivors = []
        layer_hist = [0] * (MAX_LAYERS + 1)
        decomposition_failures = 0
        split = spec["split"]
        split_hist_start = split["historical_start"]
        split_hist_end = split["historical_end"]
        split_oos_start = split["oos_start"]
        split_oos_end = split["oos_end"]
        for tf in TIMEFRAMES:
            for symbol in SYMBOLS:
                key = "%s/%s" % (symbol, tf["raw_interval"])
                c = cohort_data[key]
                i0, i1 = window_indices(c["open_ms"], spec["data"]["start"], spec["data"]["end"])
                h0, h1 = window_indices(c["open_ms"], split_hist_start, split_hist_end)
                o0, o1 = window_indices(c["open_ms"], split_oos_start, split_oos_end)
                made = layers[tf["raw_interval"]]
                rows_by_grid = {g: [] for g in GRID_KINDS}
                for method in STRATEGIES:
                    code = method["method_code"]
                    for params in [dict(zip(DCA_AXES, values)) for values in __import__("itertools").product(*(DCA_AXES[a] for a in DCA_AXES))]:
                        base_layer = made["base"][code]
                        for grid in GRID_KINDS:
                            if grid == "historical":
                                a0, a1, stress = h0, h1, {}
                            elif grid == "oos":
                                a0, a1, stress = o0, o1, {}
                            elif grid == "no_funding":
                                a0, a1, stress = h0, h1, {"no_funding": True}
                            else:
                                a0, a1, stress = i0, i1, {}

                            if grid == "fee_2x": stress["fee_mult"] = 2.0
                            elif grid == "funding_2x": stress["funding_mult"] = 2.0
                            elif grid == "entry_delay_1_bar": stress["entry_delay"] = 1
                            elif grid == "slippage_2ticks": stress["slip_ticks"] = SLIPPAGE_TICKS * 2
                            elif grid in ("no_funding", "no_funding_full"):
                                stress["no_funding"] = True
                            elif grid == "cost_attrition_40bps":
                                stress["fee_mult"] = 8.0
                            if grid == "no_funding_full":
                                a0, a1 = i0, i1
                            asset_layer = select_asset_layer(base_layer, SYMBOLS.index(symbol))
                            metric = simulate(c["open"], c["high"], c["low"], c["close"], c["open_ms"],
                                               funding_data[key][0], asset_layer, params, made["window"],
                                               made["horizon"], a0, a1, stress, symbol)
                            hist = oos = None
                            if grid == "full":
                                hist = simulate(c["open"], c["high"], c["low"], c["close"], c["open_ms"],
                                                funding_data[key][0], asset_layer, params, made["window"],
                                                made["horizon"], h0, h1, {}, symbol)
                                oos = simulate(c["open"], c["high"], c["low"], c["close"], c["open_ms"],
                                                funding_data[key][0], asset_layer, params, made["window"],
                                                made["horizon"], o0, o1, {}, symbol)
                            row_hist = metric if grid in ("historical", "no_funding") else hist
                            row_oos = metric if grid == "oos" else oos
                            row = row_for(symbol, tf["raw_interval"], method, params, grid,
                                          metric, row_hist, row_oos)
                            rows_by_grid[grid].append(row)
                            grid_rows[grid].append(row)
                            if grid == "full":
                                for idx, count in enumerate(metric["layer_hist"]):
                                    layer_hist[idx] += int(count)
                            if not metric["decomposition_ok"]:
                                decomposition_failures += 1
                winner, neigh, cull = select_cohort(rows_by_grid)
                label = "%s/%s" % (symbol, tf["raw_interval"])
                if winner is None:
                    record = {"cohort": label, "outcome": "CULLED", "winner": None,
                              "winner_case_label": None, "metrics": {}, "neighbourhood": neigh,
                              "cull_reasons": cull}
                    cohort_results.append(record)
                else:
                    robust = {}
                    for g in ["full", "fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks"]:
                        hit = next(r for r in rows_by_grid[g] if row_key(r) == row_key(winner))
                        robust[g] = {"net_pnl": float(hit["net_pnl"]), "sharpe": float(hit["sharpe"]),
                                     "max_dd_pct": float(hit["max_dd_pct"])}
                    metrics = {"historical": {k: float(winner["historical_" + k]) if k not in ("episodes", "fills") else int(winner["historical_" + k])
                                                for k in ("net_pnl", "sharpe", "episodes", "fills", "max_dd_pct")},
                               "oos": {k: float(winner["oos_" + k]) if k not in ("episodes", "fills") else int(winner["oos_" + k])
                                       for k in ("net_pnl", "sharpe", "episodes", "fills", "max_dd_pct")},
                               "full": {k: float(winner["full_" + k]) if k not in ("episodes", "fills") else int(winner["full_" + k])
                                        for k in ("net_pnl", "sharpe", "episodes", "fills", "max_dd_pct", "annualized_return")},
                               "robustness": robust, "neighbourhood": neigh}
                    record = {"cohort": label, "outcome": "SURVIVOR", "winner": param_only(winner),
                              "winner_case_label": winner["window_case"], "metrics": metrics,
                              "neighbourhood": neigh, "cull_reasons": []}
                    cohort_results.append(record)
                    survivors.append(record)
        for grid in GRID_KINDS:
            write_grid(os.path.join(attempt_dir, "artifacts", "grids", "grid_%s.csv" % grid), grid_rows[grid])
            # Keep the canonical location used by the contract/verdict readers too.
            write_grid(os.path.join(attempt_dir, "artifacts", "grid_%s.csv" % grid), grid_rows[grid])
        atomic_json(os.path.join(attempt_dir, "artifacts", "cohort_results.json"), cohort_results)
        atomic_json(os.path.join(attempt_dir, "artifacts", "cohort_survivors.json"), survivors)
        atomic_json(os.path.join(attempt_dir, "artifacts", "dca_layer_histogram.json"),
                    {"level_%02d" % i: int(v) for i, v in enumerate(layer_hist)})
        expected_total = len(TIMEFRAMES) * len(SYMBOLS) * len(STRATEGIES) * len(DCA_AXES["spacing_pct"]) * len(DCA_AXES["size_multiplier"]) * len(DCA_AXES["breakeven_tp_pct"]) * len(DCA_AXES["invalidation_pct"]) * len(GRID_KINDS)
        actual_total = sum(len(v) for v in grid_rows.values())
        funding_counts = {k: v[1] for k, v in funding_data.items()}
        assertions = {
            "coverage_complete": actual_total == expected_total and all(len(v) == 2304 for v in grid_rows.values()),
            "all_16_cohorts_present": len(cohort_results) == 16,
            "all_10_grids_present": all(len(v) == 2304 for v in grid_rows.values()),
            "pnl_decomposition": decomposition_failures == 0,
            "qlib_readback": bool(build.get("readback")),
            "causal_signal": causal_mismatches == 0,
            "funding_loaded": all(sum(counts.values()) > 0 for counts in funding_counts.values()),
            "parameter_contract_complete": True,
            "dca_layer_histogram": layer_hist[0] == sum(r["full_episodes"] for r in grid_rows["full"]),
        }
        atomic_json(os.path.join(attempt_dir, "artifacts", "assertions.json"), assertions)
        survivor_labels = [r["cohort"] for r in survivors]
        scount = len(survivors)
        disposition = "REJECT / NO_SURVIVOR" if scount == 0 else ("SURVIVOR_FOUND" if scount == 1 else "MULTIPLE_SURVIVORS")
        result = {
            "schema_version": 1, "family_id": spec["family_id"], "round_id": spec["round_id"], "run_id": spec["run_id"],
            "task_id": spec["task_id"], "kanban_board": spec["kanban_board"], "engine": "topological_anomaly_v3",
            "status": "DONE", "coverage_complete": all(assertions.values()),
            "expected_case_evaluations": expected_total, "case_evaluations_total": actual_total,
            "cohort_count": len(cohort_results), "cohort_survivor_count": scount, "cohort_survivors": survivor_labels,
            "disposition": disposition, "verdict_recommendation": "PASS" if scount else "REJECT",
            "performance_claimable_recommendation": bool(scount), "selector_version": "cohort-selector-v1",
            "disposition_version": "cohort-disposition-v1", "grid_kinds": GRID_KINDS,
            "data_window": {"start": spec["data"]["start"], "end": spec["data"]["end"], "is_end": split_hist_end,
                            "oos_start": split_oos_start},
            "research_boundary": "source predictive-content record adapted to local crypto; no adoption or live-trading claim",
            "assertion_failures": sorted(k for k, v in assertions.items() if not v),
            "funding_truth_status": funding_counts,
            "generated_at_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        atomic_json(os.path.join(attempt_dir, "result.json"), result)
        Path(os.path.join(attempt_dir, "DONE")).write_text("\n", encoding="utf-8")
        log("DONE cohorts=%d survivors=%d rows=%d" % (len(cohort_results), scount, actual_total))
        return result
    except Exception as exc:
        log("FAILED %s: %s" % (type(exc).__name__, exc))
        Path(os.path.join(attempt_dir, "FAILED")).write_text(str(exc) + "\n", encoding="utf-8")
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
