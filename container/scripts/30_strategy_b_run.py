#!/usr/bin/env python3
"""Strategy B — EMA-crossover walk-forward momentum (LONG/SHORT) full backtest, on Qlib.

Entry point for ONE pre-registered attempt.  Reads the immutable run-spec from
/results (the only source of parameters), builds the Qlib .bin store from the
READ-ONLY canonical raw store into /qlib/work, then runs the pre-registered
full-backtest contract (Contract section 7.2):

    symbols x timeframes x EMA-pair domain x walk-forward window domain x
    DCA execution x (historical / OOS / robustness)

Pre-registered mechanism (see the round-spec; nothing here is chosen after the fact):

  * direction: sign(EMA_fast - EMA_slow) at bar t-1 is the signal formed at bar t's
    close; the fill happens on bar t+1.  The two clauses are composed literally, so the
    information cutoff of a fill at bar u is EMA(u-2) -- the strictly conservative
    reading.  `entry_delay_1_bar` shifts it one bar further.
  * position: +1 / -1 continuous intent (no neutral), executed through the inherited
    DCA rail applied sym-directionally to the long and the short leg.  A direction flip
    is a reduce-only flatten followed by a re-establishment in the new direction on the
    same bar close; both legs are never held at once.  Once an episode is flat or killed
    it may not add, and it may not be re-opened in the same direction: re-entry needs a
    fresh direction flip.
  * walk-forward: for each (T_train, T_test) cell the window is tiled by consecutive
    [train | test] blocks.  Each train block selects the best of the 30 registered EMA
    pairs by daily-equity Sharpe; that pair is executed on the immediately following test
    block; the test blocks are chained into one account.  The 16 cells are then smoothed
    by a 2D robust Sharpe (own 1/2 + mean of the in-domain orthogonal neighbours 1/2) and
    the cell with the highest smoothed Sharpe is selected -- on the HISTORICAL window only.

DCA is executed as real order/fill accounting (an episode state machine over the bars);
nothing is estimated after the fact.

This file is self-contained on purpose: the data-layer helpers below are a frozen copy of
Strategy A's, so this attempt's reproducibility depends on this script's own sha256 and on
nothing else.

Writes only:
  * /qlib/work/**      (rebuildable derived/cache area, INV-5)
  * <attempt_dir>/**   (result.json, artifacts/, logs/, state.json)
Never writes /data/raw (read-only mount).

usage: 30_strategy_b_run.py <run-spec.json>
"""
from __future__ import annotations

import calendar
import csv
import gzip
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import time

import numpy as np

VENV_PYTHON = "/opt/venv/bin/python"
DUMP_BIN = "/opt/qlib-tools/dump_bin.py"
RAW_ROOT = "/data/raw/binance/usdm"
WORK_ROOT = "/qlib/work/strategyB-v1"
CSV_ROOT = os.path.join(WORK_ROOT, "csv")
QLIB_DIR = os.path.join(WORK_ROOT, "qlib-data")
MS_PER_DAY = 86400000
FIELDS = ["open", "high", "low", "close", "volume"]
START_EQUITY = 30000.0

# ---------------------------------------------------------------------------
# generic helpers
# ---------------------------------------------------------------------------


def log(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), msg)
    print(line, flush=True)
    return line


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def _sanitize(o):
    """Make a payload strictly JSON-safe: numpy scalars -> python, non-finite -> null.

    json.dumps would otherwise emit the bare tokens NaN / Infinity, which are not valid
    JSON and are rejected by strict parsers (e.g. jq), making the durable artifact unreadable.
    """
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, bool) or o is None or isinstance(o, (int, str)):
        return o
    if hasattr(o, "item"):
        try:
            return _sanitize(o.item())
        except Exception:  # noqa: BLE001
            return str(o)
    if isinstance(o, dict):
        return {str(k): _sanitize(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_sanitize(v) for v in o]
    return str(o)


def atomic_write_json(path, payload):
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(json.dumps(_sanitize(payload), indent=2, ensure_ascii=False,
                            allow_nan=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.rename(tmp, path)


def dir_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def utc_ms(date_str):
    # calendar.timegm treats the parsed struct as UTC; time.mktime would silently use the
    # process timezone and shift every window boundary by the host offset.
    return int(calendar.timegm(time.strptime(date_str + " 00:00:00", "%Y-%m-%d %H:%M:%S"))) * 1000


def qlib_window(start, end):
    """Day-granularity dates -> qlib timestamps covering the WHOLE inclusive final day."""
    return start + " 00:00:00", end + " 23:59:59"


# ---------------------------------------------------------------------------
# phase 1: raw (read-only) -> Qlib bin store
# ---------------------------------------------------------------------------


def raw_months(symbol, interval, start, end):
    d = os.path.join(RAW_ROOT, "klines", symbol, interval)
    lo, hi = start[:7], end[:7]
    out = []
    prefix = "%s-%s-" % (symbol, interval)
    for name in sorted(os.listdir(d)):
        if not name.endswith(".jsonl.gz"):
            continue
        month = name[len(prefix):-len(".jsonl.gz")]
        if lo <= month <= hi:
            out.append(os.path.join(d, name))
    return out


def write_csv(symbol, interval, qlib_freq, start, end):
    out_dir = os.path.join(CSV_ROOT, qlib_freq)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "%s.csv" % symbol)
    lo_ms = utc_ms(start)
    hi_ms = utc_ms(end) + MS_PER_DAY - 1
    rows = 0
    first_ms = last_ms = None
    with open(out_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["date"] + FIELDS)
        for src in raw_months(symbol, interval, start, end):
            with gzip.open(src, "rt") as gz:
                for line in gz:
                    line = line.strip()
                    if not line:
                        continue
                    r = json.loads(line)
                    ms = r["open_time_ms"]
                    if ms < lo_ms or ms > hi_ms:
                        continue
                    ts = time.gmtime(ms / 1000.0)
                    w.writerow(["%04d-%02d-%02d %02d:%02d:%02d" % (
                        ts.tm_year, ts.tm_mon, ts.tm_mday, ts.tm_hour, ts.tm_min, ts.tm_sec),
                        r["open"], r["high"], r["low"], r["close"], r["volume"]])
                    rows += 1
                    if first_ms is None:
                        first_ms = ms
                    last_ms = ms
    return {"rows": rows, "first_open_time_ms": first_ms, "last_open_time_ms": last_ms,
            "csv": out_path, "csv_bytes": os.path.getsize(out_path)}


def csv_first_last_close(path):
    with open(path, newline="") as fh:
        r = csv.reader(fh)
        header = next(r)
        i = header.index("close")
        first = next(r)
        last = first
        for row in r:
            last = row
    return float(first[i]), float(last[i])


def build_bins(spec, attempt_dir, run_log):
    t0 = time.time()
    data = spec["data"]
    start, end = data["start"], data["end"]
    # /qlib/work is a rebuildable derived/cache area (INV-5): rebuild from scratch so
    # the recorded build wall time / size describe THIS attempt's store.
    if os.path.isdir(WORK_ROOT):
        shutil.rmtree(WORK_ROOT)
    per_dataset = {}
    for symbol in data["symbols"]:
        for tf in data["timeframes"]:
            key = "%s/%s" % (symbol, tf["raw_interval"])
            per_dataset[key] = write_csv(symbol, tf["raw_interval"], tf["qlib_freq"], start, end)
            run_log("csv %s rows=%d bytes=%d" % (key, per_dataset[key]["rows"],
                                                 per_dataset[key]["csv_bytes"]))

    for tf in data["timeframes"]:
        cmd = [VENV_PYTHON, DUMP_BIN, "dump_all",
               "--data_path", os.path.join(CSV_ROOT, tf["qlib_freq"]),
               "--qlib_dir", QLIB_DIR, "--freq", tf["qlib_freq"],
               "--include_fields", "open,close,high,low,volume",
               "--date_field_name", "date", "--max_workers", "1"]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=14400)
        if p.returncode != 0:
            raise SystemExit("dump_bin failed for freq %s rc=%d\n%s\n%s"
                             % (tf["qlib_freq"], p.returncode, p.stdout[-2000:], p.stderr[-2000:]))
        run_log("dump_bin %s ok" % tf["qlib_freq"])

    import qlib
    from qlib.data import D
    qlib.init(provider_uri=QLIB_DIR, region="cn", expression_cache=None, dataset_cache=None)

    readback = {}
    qstart, qend = qlib_window(start, end)
    for symbol in data["symbols"]:
        for tf in data["timeframes"]:
            freq = tf["qlib_freq"]
            cal = D.calendar(start_time=qstart, end_time=qend, freq=freq)
            df = D.features([symbol], ["$" + f for f in FIELDS],
                            start_time=qstart, end_time=qend, freq=freq)
            if len(df) == 0:
                raise SystemExit("qlib read-back empty for %s %s" % (symbol, freq))
            ts = list(df.index.get_level_values("datetime"))
            if ts != sorted(ts):
                raise SystemExit("qlib read-back not ascending for %s %s" % (symbol, freq))
            if int(df.isnull().sum().sum()) != 0:
                raise SystemExit("qlib read-back has nulls for %s %s" % (symbol, freq))
            if sorted(df.index.get_level_values("instrument").unique().tolist()) != [symbol]:
                raise SystemExit("qlib read-back instruments != [%s] for %s" % (symbol, freq))
            ds = per_dataset["%s/%s" % (symbol, tf["raw_interval"])]
            cs, cl = csv_first_last_close(ds["csv"])
            readback["%s/%s" % (symbol, freq)] = {
                "calendar_len": int(len(cal)), "rows": int(len(df)),
                "first": str(df.index[0][1]), "last": str(df.index[-1][1]),
                "first_close": float(df["$close"].iloc[0]),
                "last_close": float(df["$close"].iloc[-1]),
                "csv_first_close": cs, "csv_last_close": cl,
                "close_abs_diff_first": abs(float(df["$close"].iloc[0]) - cs),
                "close_abs_diff_last": abs(float(df["$close"].iloc[-1]) - cl),
                "raw_rows": ds["rows"]}
            run_log("readback %s %s rows=%d (raw %d) first=%s last=%s"
                    % (symbol, freq, len(df), ds["rows"], str(df.index[0][1]),
                       str(df.index[-1][1])))

    report = {"qlib_dir": QLIB_DIR, "csv_root": CSV_ROOT,
              "build_wall_seconds": round(time.time() - t0, 3),
              "qlib_dir_bytes": dir_size(QLIB_DIR), "per_dataset": per_dataset,
              "readback": readback, "qlib_version": qlib.__version__,
              "bin_precision_note": "qlib .bin stores OHLCV as float32; the recorded "
                                    "close_abs_diff_* values are that storage rounding only "
                                    "(no resampling, no gap filling)."}
    atomic_write_json(os.path.join(attempt_dir, "artifacts", "bins_build.json"), report)
    run_log("bins build wall=%.1fs bytes=%d" % (report["build_wall_seconds"],
                                                report["qlib_dir_bytes"]))
    return report


# ---------------------------------------------------------------------------
# phase 2: pre-registered execution engine
# ---------------------------------------------------------------------------


class Cohort:
    """Bars of one (symbol, timeframe) cohort, loaded through the Qlib data layer."""

    def __init__(self, symbol, tf, start, end):
        from qlib.data import D
        freq = tf["qlib_freq"]
        qstart, qend = qlib_window(start, end)
        df = D.features([symbol], ["$" + f for f in FIELDS],
                        start_time=qstart, end_time=qend, freq=freq).sort_index()
        self.symbol = symbol
        self.timeframe = tf["raw_interval"]
        self.qlib_freq = freq
        # qlib datetimes are UTC wall time; .value is the epoch in ns (never local-time aware)
        self.open_time_ms = np.array(
            [int(ts.value // 1_000_000) for ts in df.index.get_level_values("datetime")],
            dtype=np.int64)
        for f in FIELDS:
            setattr(self, f, df["$" + f].to_numpy(dtype=np.float64))
        self.n = len(self.open_time_ms)
        day_id = self.open_time_ms // MS_PER_DAY
        uniq, inv = np.unique(day_id, return_inverse=True)
        self.day_index = inv.astype(np.int64)
        self.n_days = int(len(uniq))
        is_de = np.zeros(self.n, dtype=bool)
        is_de[np.concatenate([day_id[1:] != day_id[:-1], [True]])] = True
        self.is_day_end = is_de
        # day_start[j] = first bar whose day_index >= j (j in 0..n_days)
        self.day_start = np.searchsorted(self.day_index, np.arange(self.n_days + 1),
                                        side="left").astype(np.int64)
        self.first_ts = str(df.index[0][1])
        self.last_ts = str(df.index[-1][1])
        self.price_increment = 0.0
        self.taker_fee = 0.0
        self.leverage = 1.0
        self.margin_maint = 0.0
        self.funding = np.zeros(self.n, dtype=np.float64)

    def slice(self, start_date, end_date):
        lo = utc_ms(start_date)
        hi = utc_ms(end_date) + MS_PER_DAY - 1
        return (int(np.searchsorted(self.open_time_ms, lo, side="left")),
                int(np.searchsorted(self.open_time_ms, hi, side="right")))


def load_funding(symbol, start, end):
    """Official funding observations only (raw SCHEMA: filter truth_status == official)."""
    path = os.path.join(RAW_ROOT, "funding", symbol, "%s-funding.jsonl.gz" % symbol)
    lo, hi = utc_ms(start), utc_ms(end) + MS_PER_DAY - 1
    times, rates, excluded = [], [], 0
    with gzip.open(path, "rt") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            ms = r["funding_time_ms"]
            if ms < lo or ms > hi:
                continue
            if r.get("truth_status") != "official":
                excluded += 1
                continue
            times.append(ms)
            rates.append(float(r["funding_rate"]))
    return np.array(times, dtype=np.int64), np.array(rates, dtype=np.float64), excluded


def funding_per_bar(cohort, fund_times, fund_rates):
    """Attach each official funding event to the bar whose close is <= the event time."""
    out = np.zeros(cohort.n, dtype=np.float64)
    if len(fund_times) == 0:
        return out, 0
    idx = np.searchsorted(cohort.open_time_ms, fund_times, side="left") - 1
    applied = 0
    for k in range(len(idx)):
        i = int(idx[k])
        if 0 <= i < cohort.n:
            out[i] += fund_rates[k]
            applied += 1
    return out, applied


# ---------------------------------------------------------------------------
# signal: EMA pairs, 1-bar-lagged direction, next-bar execution
# ---------------------------------------------------------------------------


def ema_of(close, span):
    """Recursive EMA with alpha = 2/(span+1), seeded on the first close (adjust=False)."""
    import pandas as pd
    return pd.Series(close).ewm(span=span, adjust=False).mean().to_numpy(dtype=np.float64)


def direction_array(ema_fast, ema_slow, lag_bars):
    """Execution direction per bar.

    signal formed at bar j  = sign(ema_fast[j-1] - ema_slow[j-1])   (1-bar-lagged EMAs)
    fill at bar j+1                                                 (next bar)
    => exec_dir[u] = sign(ema_at(u - 2)) for lag_bars = 0, and one bar later per extra lag.
    Ties (and the bars before the first defined comparison) carry the previous direction,
    which starts at +1.  Undefined bars are 0.
    """
    f, s = ema_fast, ema_slow
    sg = np.where(f > s, 1.0, np.where(f < s, -1.0, np.nan))
    mask = np.isnan(sg)
    if mask.any():
        idx = np.where(~mask, np.arange(len(sg)), 0)
        np.maximum.accumulate(idx, out=idx)
        sg = sg[idx]
        first = int(np.flatnonzero(~mask)[0]) if (~mask).any() else len(sg)
        sg[:first] = 1.0
    n = len(sg)
    shift = 2 + lag_bars
    out = np.zeros(n, dtype=np.int8)
    if n > shift:
        out[shift:] = sg[:n - shift].astype(np.int8)
    return out


# ---------------------------------------------------------------------------
# the DCA episode engine (sym-directional rail)
# ---------------------------------------------------------------------------


def simulate(cohort, i0, i1, dirs, rail, slip_ticks, fee_mult=1.0, funding_mult=1.0,
             use_funding=True, base_equity=START_EQUITY):
    """One pre-registered rail execution over bars [i0, i1) with a fixed direction array.

    Returns realised accounting plus the end-of-UTC-day equity marks of this slice.  The
    slice is force-flattened on its last bar (reduce-only), so a chained walk-forward has a
    well defined carry-over equity and never carries a position across a step boundary.
    """
    C = cohort.close[i0:i1].tolist()
    H = cohort.high[i0:i1].tolist()
    L = cohort.low[i0:i1].tolist()
    O = cohort.open[i0:i1].tolist()
    D = dirs[i0:i1].tolist()
    dmark = cohort.is_day_end[i0:i1].tolist()
    didx = cohort.day_index[i0:i1].tolist()
    n = len(C)
    if use_funding:
        fper = (cohort.funding[i0:i1] * funding_mult).tolist()
    else:
        fper = [0.0] * n

    tick = cohort.price_increment
    slip = slip_ticks * tick
    taf = cohort.taker_fee * fee_mult
    lev = cohort.leverage
    mm = cohort.margin_maint

    d0 = rail["spacing_d0"]
    tp_pct = rail["tp"]
    inval = rail["invalidation"]
    mult = rail["size_multiplier"]
    base = rail["base_quote"]
    notional = [base * (mult ** k) * lev for k in range(12)]

    def charge_fee(amount):
        """A fee is paid at the instant of the fill and must reduce the realised equity there.

        Tracking fees separately without deducting them makes every cost stress track a no-op
        and reports a 'net' figure that is gross of fees; the registered metric is the equity
        net of fees and funding, so the deduction is a single choke point here.
        """
        nonlocal fees, realized
        fees += amount
        realized -= amount

    layers = [0] * 12
    marks = {}
    realized = fees = fund_paid = traded = 0.0
    episodes = tp_hits = stop_hits = flips = open_at_end = margin_calls = 0
    bars_in_market = 0
    max_lev = 0.0
    util_sum = 0.0
    min_entry_equity = base_equity
    halted = False
    capital_exhausted = base_equity <= 0.0

    qty = cost = 0.0
    avg = p0 = lvl_next = stop = tp = 0.0
    d = 0
    k_next = 1
    entry_bar = -1
    last_closed_dir = 0
    eq0 = base_equity

    t = 0
    while t < n:
        # ---------------------------------------------------------------- adverse walk
        if d != 0 and t != entry_bar:
            if d == 1:
                trig0 = lvl_next if (k_next <= 10 and lvl_next >= stop) else stop
                reachable = L[t] <= trig0
            else:
                trig0 = lvl_next if (k_next <= 10 and lvl_next <= stop) else stop
                reachable = H[t] >= trig0
            if reachable:
                k = k_next
                ln = lvl_next
                killed_at = None
                while True:
                    if k > 10 or d * (ln - stop) < 0:
                        trig, is_stop = stop, True
                    else:
                        trig, is_stop = ln, False
                    if d * ((L[t] if d == 1 else H[t]) - trig) > 0:
                        break
                    if is_stop:
                        # a gap through the stop fills at the open, never at the stop price
                        if (O[t] >= trig) if d == 1 else (O[t] <= trig):
                            killed_at = trig
                        else:
                            killed_at = O[t]
                        break
                    fpx = trig + d * slip
                    q = notional[k] / fpx
                    qty += q
                    cost += q * fpx
                    traded += q * fpx
                    charge_fee(q * fpx * taf)
                    layers[k] += 1
                    avg = cost / qty
                    stop = avg * (1.0 - d * inval)
                    tp = avg * (1.0 + d * tp_pct)
                    k += 1
                    ln = p0 * (1.0 - d * d0 * k)
                if killed_at is not None:
                    xpx = killed_at - d * slip
                    realized += d * (qty * xpx - cost)
                    charge_fee(qty * xpx * taf)
                    traded += qty * xpx
                    stop_hits += 1
                    last_closed_dir = d
                    d = 0
            if d != 0:
                ueq = eq0 + realized + d * (qty * C[t] - cost)
                if ueq <= mm * qty * C[t]:
                    xpx = C[t] - d * slip          # capital-exhaustion backstop at the close
                    realized += d * (qty * xpx - cost)
                    charge_fee(qty * xpx * taf)
                    traded += qty * xpx
                    margin_calls += 1
                    capital_exhausted = True
                    last_closed_dir = d
                    d = 0
                elif (H[t] >= tp) if d == 1 else (L[t] <= tp):
                    xpx = tp - d * slip            # reduce-only take profit
                    realized += d * (qty * xpx - cost)
                    charge_fee(qty * xpx * taf)
                    traded += qty * xpx
                    tp_hits += 1
                    last_closed_dir = d
                    d = 0
        if d != 0:
            ueq = eq0 + realized + d * (qty * C[t] - cost)
            bars_in_market += 1
            if ueq > 0.0:
                lv = (qty * C[t]) / ueq
                if lv > max_lev:
                    max_lev = lv
                util_sum += (cost / lev) / ueq
        # ------------------------------------------------------- close-of-bar decisions
        nd = D[t]
        if d == 0:
            if nd != 0 and not halted and (last_closed_dir == 0 or nd != last_closed_dir):
                if eq0 + realized <= 0.0:
                    halted = True          # the account cannot lose more than itself
                else:
                    if eq0 + realized < min_entry_equity:
                        min_entry_equity = eq0 + realized
                    fpx = C[t] + nd * slip
                    q = notional[0] / fpx
                    qty = q
                    cost = q * fpx
                    traded += q * fpx
                    charge_fee(q * fpx * taf)
                    layers[0] += 1
                    p0 = fpx
                    d = nd
                    k_next = 1
                    lvl_next = p0 * (1.0 - d * d0)
                    avg = fpx
                    stop = avg * (1.0 - d * inval)
                    tp = avg * (1.0 + d * tp_pct)
                    entry_bar = t
                    episodes += 1
        elif nd != 0 and nd != d:
            # direction flip: reduce-only flatten, then re-establish on the new side
            xpx = C[t] - d * slip
            realized += d * (qty * xpx - cost)
            charge_fee(qty * xpx * taf)
            traded += qty * xpx
            last_closed_dir = d
            flips += 1
            d = 0
            fpx = C[t] + nd * slip
            q = notional[0] / fpx
            qty = q
            cost = q * fpx
            traded += q * fpx
            charge_fee(q * fpx * taf)
            layers[0] += 1
            p0 = fpx
            d = nd
            k_next = 1
            lvl_next = p0 * (1.0 - d * d0)
            avg = fpx
            stop = avg * (1.0 - d * inval)
            tp = avg * (1.0 + d * tp_pct)
            entry_bar = t
            episodes += 1
        f = fper[t]
        if f != 0.0 and d != 0:
            fund_paid += d * qty * C[t] * f
        if dmark[t]:
            marks[didx[t]] = eq0 + realized + (d * (qty * C[t] - cost) if d != 0 else 0.0)
        t += 1

    if d != 0:                                  # force-flatten at the slice boundary
        xpx = C[n - 1] - d * slip
        realized += d * (qty * xpx - cost)
        charge_fee(qty * xpx * taf)
        traded += qty * xpx
        open_at_end += 1
        last_closed_dir = d
        d = 0

    realized -= fund_paid
    return {
        "net_pnl": realized, "fees": fees, "funding": fund_paid,
        "gross_pnl": realized + fees + fund_paid, "traded_notional": traded,
        "ending_equity": eq0 + realized, "episodes": episodes, "tp_hits": tp_hits,
        "stop_hits": stop_hits, "flips": flips, "open_at_end": open_at_end,
        "margin_calls": margin_calls, "halted": halted,
        "capital_exhausted": capital_exhausted, "min_entry_equity": min_entry_equity,
        "bars_in_market": bars_in_market, "max_effective_leverage": max_lev,
        "capital_utilization": (util_sum / bars_in_market) if bars_in_market else 0.0,
        "layers": layers, "marks": marks,
    }


def daily_series(marks, day0, day1, base):
    out = np.empty(day1 - day0, dtype=np.float64)
    last = base
    for j in range(day0, day1):
        v = marks.get(j)
        if v is not None:
            last = v
        out[j - day0] = last
    return out


def sharpe_of(series):
    """Daily-return Sharpe; null when the equity path is non-positive (undefined growth)."""
    if len(series) < 3 or (series[:-1] <= 0.0).any():
        return None
    rets = np.diff(series) / series[:-1]
    sd = float(np.std(rets, ddof=1))
    if sd <= 0.0:
        return 0.0
    return float(np.mean(rets) / sd * np.sqrt(365.0))


# ---------------------------------------------------------------------------
# walk-forward driver
# ---------------------------------------------------------------------------


def wf_run(cohort, day0, day1, cell, dir_sets, weights, rail, slip_ticks, base_equity,
           selection=("train_best",), fee_mult=1.0, funding_mult=1.0, use_funding=True):
    """Chain consecutive [train | test] blocks over days [day0, day1).

    selection=("train_best",)              -> each train block picks the best of the 30 pairs
    selection=("shift", di, dj)            -> the EMA neighbour (i+di, j+dj) of that pick

    The direction set is the caller's: the registered entry-delay stress passes the set built
    with lag_bars=1, so the lag actually reaches the fills instead of being a dead parameter.
    """
    a, b = cell
    ds = cohort.day_start
    steps = []
    marks = {}
    layers = [0] * 12
    tot = {"net_pnl": 0.0, "fees": 0.0, "funding": 0.0, "traded_notional": 0.0,
           "episodes": 0, "tp_hits": 0, "stop_hits": 0, "flips": 0, "open_at_end": 0,
           "margin_calls": 0, "bars_in_market": 0}
    max_lev = 0.0
    util_num = 0.0
    min_entry_eq = float("inf")
    exhausted = False
    eq = base_equity
    s = day0
    while s + a + b <= day1:
        tr0, tr1 = int(ds[s]), int(ds[s + a])
        te0, te1 = int(ds[s + a]), int(ds[s + a + b])
        if te1 <= te0 or tr1 <= tr0:
            s += a + b
            continue
        if selection[0] == "train_best" or selection[0] == "shift":
            best = None
            for pi in range(len(weights)):
                tr = simulate(cohort, tr0, tr1, dir_sets[pi], rail, slip_ticks, fee_mult,
                              funding_mult, use_funding, eq)
                sh = sharpe_of(daily_series(tr["marks"], s, s + a, eq))
                key = (-1e18 if sh is None else sh, tr["net_pnl"], -pi)
                if best is None or key > best[0]:
                    best = (key, pi, sh, tr["net_pnl"])
            if best is None:
                raise SystemExit("empty EMA pair domain")
            sel = best[1]
            sel_sharpe = best[2]
            sel_train_pnl = best[3]
        else:
            sel, sel_sharpe, sel_train_pnl = 0, None, None
        if selection[0] == "shift":
            di, dj = selection[1], selection[2]
            fi, si = weights[sel]
            ex = (fi + di, si + dj)
            exec_pi = _index_of(weights, ex)
            if exec_pi is None:
                sel = None
        else:
            exec_pi = sel
        if exec_pi is None:
            s += a + b
            continue
        te = simulate(cohort, te0, te1, dir_sets[exec_pi], rail, slip_ticks, fee_mult,
                      funding_mult, use_funding, eq)
        marks.update(te["marks"])
        for k in range(12):
            layers[k] += te["layers"][k]
        for k in ("net_pnl", "fees", "funding", "traded_notional", "episodes", "tp_hits",
                  "stop_hits", "flips", "open_at_end", "margin_calls", "bars_in_market"):
            tot[k] += te[k]
        max_lev = max(max_lev, te["max_effective_leverage"])
        util_num += te["capital_utilization"] * te["bars_in_market"]
        min_entry_eq = min(min_entry_eq, te["min_entry_equity"])
        eq = te["ending_equity"]
        exhausted = exhausted or te["capital_exhausted"]
        steps.append({
            "step": len(steps), "train_days": [s - day0, s + a - day0],
            "test_days": [s + a - day0, s + a + b - day0],
            "selected_pair_index": sel,
            "selected_fast": weights[sel][0] if sel is not None else None,
            "selected_slow": weights[sel][1] if sel is not None else None,
            "selected_pair_index_used": exec_pi,
            "train_sharpe": sel_sharpe, "train_net_pnl": sel_train_pnl,
            "test_net_pnl": te["net_pnl"], "test_episodes": te["episodes"],
            "ending_equity": eq})
        s += a + b

    series = daily_series(marks, day0, day1, base_equity)
    span_days = max(day1 - day0, 1)
    years = span_days / 365.25
    end_eq = eq
    net = tot["net_pnl"]
    bars = tot["bars_in_market"]
    return {
        "cell": [a, b], "n_steps": len(steps), "net_pnl": net,
        "fees": tot["fees"], "funding": tot["funding"], "gross_pnl": net + tot["fees"] + tot["funding"],
        "traded_notional": tot["traded_notional"], "ending_equity": end_eq,
        "episodes": tot["episodes"], "tp_hits": tot["tp_hits"], "stop_hits": tot["stop_hits"],
        "flips": tot["flips"], "open_at_end": tot["open_at_end"],
        "margin_calls": tot["margin_calls"], "bars_in_market": bars,
        "sharpe": sharpe_of(series),
        "max_effective_leverage": max_lev,
        "capital_utilization": (util_num / bars) if bars else 0.0,
        "min_entry_equity": min_entry_eq if min_entry_eq != float("inf") else base_equity,
        "layers": layers, "series": series, "steps": steps, "capital_exhausted": exhausted,
        "max_dd_usdt": float((series - np.maximum.accumulate(series)).min()) if len(series) else 0.0,
        "max_dd_pct": float(((series - np.maximum.accumulate(series)) /
                             np.maximum.accumulate(series)).min()) if len(series) else 0.0,
        "cagr": ((end_eq / base_equity) ** (1.0 / years) - 1.0) if end_eq > 0.0 else None,
        "annualized_return": ((end_eq / base_equity - 1.0) / years) if end_eq > 0.0 else None,
        "years": years, "days": span_days,
    }


def _index_of(weights, pair):
    try:
        return weights.index((int(pair[0]), int(pair[1])))
    except ValueError:
        return None


def build_pairs(spec):
    fast = spec["params"]["ema_fast"]
    slow = spec["params"]["ema_slow"]
    return [(int(f), int(s)) for f in fast for s in slow if f < s]


def robust_sharpe(grid, ti, si, nt, ns):
    """2D robust Sharpe: own weight 1/2, mean of the in-domain orthogonal neighbours 1/2.

    A neighbour whose own Sharpe is undefined (equity path non-positive) simply drops out;
    with no legal neighbours left the cell falls back to its own value.
    """
    own = grid[ti][si]
    if own is None:
        return None
    nb = []
    for a, b in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        if 0 <= ti + a < nt and 0 <= si + b < ns:
            v = grid[ti + a][si + b]
            if v is not None:
                nb.append(v)
    return 0.5 * own + 0.5 * (sum(nb) / len(nb) if nb else own)


# ---------------------------------------------------------------------------
# phase 3: per-cohort walk-forward, selection and robustness tracks
# ---------------------------------------------------------------------------

STRESS = [("fee_2x", {"fee_mult": 2.0}), ("funding_2x", {"funding_mult": 2.0}),
          ("entry_delay_1_bar", {"lag_bars": 1}), ("slippage_2ticks", {"slip_ticks": 2}),
          ("source_reported_10bps", {"fee_mult": 2.0})]
GATE_STRESS = [s[0] for s in STRESS]
EXTRA_TRACKS = [("cost_attrition_40bps_per_fill", {"fee_mult": 8.0}),
                ("no_funding", {"use_funding": False})]


def run_cohort(spec, cohort, instruments, run_log):
    rail = spec["dca_rail"]
    slip = spec["costs"]["baseline_slippage_ticks"]
    pairs = build_pairs(spec)
    cells = [(int(a), int(b)) for a in spec["params"]["train_days"]
             for b in spec["params"]["test_days"]]
    wins = {"historical": (spec["data"]["historical_start"], spec["data"]["historical_end"]),
            "oos": (spec["data"]["oos_start"], spec["data"]["oos_end"]),
            "full": (spec["data"]["start"], spec["data"]["end"])}
    sl = {k: cohort.slice(*v) for k, v in wins.items()}
    days = {}
    for k, (i0, i1) in sl.items():
        if i1 <= i0:
            raise SystemExit("data_window_invalid: %s window is empty for %s/%s"
                             % (k, cohort.symbol, cohort.timeframe))
        days[k] = (int(cohort.day_index[i0]), int(cohort.day_index[i1 - 1]) + 1)

    # EMAs are computed once over the FULL cohort series per span; every slice is taken from
    # those arrays, so a block's EMAs carry earlier (past-only) information and no per-block
    # warm-up is silently different between a train block and its adjacent test block.
    spans = sorted({p[0] for p in pairs} | {p[1] for p in pairs})
    emas = {sp: ema_of(cohort.close, sp) for sp in spans}
    dir_sets = [direction_array(emas[p[0]], emas[p[1]], 0) for p in pairs]
    # the registered entry_delay_1_bar track needs its own direction set (one bar later);
    # without it the stress parameter would be dead and the track would repeat the baseline.
    dir_sets_delay = [direction_array(emas[p[0]], emas[p[1]], 1) for p in pairs]
    del emas

    out = {"symbol": cohort.symbol, "timeframe": cohort.timeframe, "cells": [], "steps": [],
           "representative": None, "tracks": {}, "neighbours": []}

    # ---- historical 16-cell walk-forward grid (the selection surface) ----
    hist_grid = [[None] * len(spec["params"]["test_days"]) for _ in spec["params"]["train_days"]]
    for ci, cell in enumerate(cells):
        r = wf_run(cohort, days["historical"][0], days["historical"][1], cell, dir_sets, pairs,
                   rail, slip, START_EQUITY)
        hist_grid[cells.index(cell) // len(spec["params"]["test_days"])][
            cells.index(cell) % len(spec["params"]["test_days"])] = r["sharpe"]
        out["cells"].append(_cell_record(cohort, r, "historical"))
        run_log("cell %s/%s hist %s wf_sharpe=%s net=%.1f steps=%d"
                % (cohort.symbol, cohort.timeframe, cell, r["sharpe"], r["net_pnl"], r["n_steps"]))
    nt, ns = len(spec["params"]["train_days"]), len(spec["params"]["test_days"])
    robust = [[robust_sharpe(hist_grid, ti, si, nt, ns) for si in range(ns)] for ti in range(nt)]
    best = None
    for ti in range(nt):
        for si in range(ns):
            v = robust[ti][si]
            if v is None:
                continue
            key = (v, hist_grid[ti][si] if hist_grid[ti][si] is not None else -1e18, -ti, -si)
            if best is None or key > best[0]:
                best = (key, ti, si)
    if best is None:
        raise SystemExit("no robust cell selectable for %s/%s" % (cohort.symbol, cohort.timeframe))
    ti, si = best[1], best[2]
    sel_cell = cells[ti * ns + si]
    run_log("selected %s/%s cell=%s robust=%.4f own=%.4f"
            % (cohort.symbol, cohort.timeframe, sel_cell, best[0][0], hist_grid[ti][si]))

    hist_sel = [c for c in out["cells"] if c["cell"] == list(sel_cell) and c["window"] == "historical"][0]

    # ---- representative: OOS + full window, baseline and every pre-registered track ----
    def carried(r, window):
        """Record one walk-forward result and keep its per-step selection trace."""
        for st in r["steps"]:
            out["steps"].append(dict(st, symbol=cohort.symbol, timeframe=cohort.timeframe,
                                     window=window))
        return _cell_record(cohort, r, window)

    rep = {"cell": sel_cell, "cell_index": [ti, si],
           "robust_sharpe": best[0][0], "is_sharpe": hist_grid[ti][si],
           "interior": (0 < ti < nt - 1) and (0 < si < ns - 1)}
    # Re-run the selected cell on the historical window: it reproduces the grid row exactly
    # (determinism assertion) and carries the per-step selection trace for the record.
    hist_rep = carried(wf_run(cohort, days["historical"][0], days["historical"][1], sel_cell,
                              dir_sets, pairs, rail, slip, START_EQUITY), "historical_rep")
    out["hist_determinism"] = {
        "net_pnl_equal": hist_rep["net_pnl"] == hist_sel["net_pnl"],
        "sharpe_equal": hist_rep["sharpe"] == hist_sel["sharpe"],
        "episodes_equal": hist_rep["episodes"] == hist_sel["episodes"]}
    rep["hist"] = hist_rep
    rep["oos"] = carried(wf_run(cohort, days["oos"][0], days["oos"][1], sel_cell, dir_sets,
                                pairs, rail, slip, START_EQUITY), "oos")
    rep["full"] = carried(wf_run(cohort, days["full"][0], days["full"][1], sel_cell, dir_sets,
                                 pairs, rail, slip, START_EQUITY), "full")
    decay = None
    if best[0][0] is not None and best[0][0] > 0 and rep["oos"]["sharpe"] is not None:
        decay = 1.0 - (rep["oos"]["sharpe"] / best[0][0])
    rep["oos_sharpe_decay"] = decay
    rep["interior_stable"] = bool(rep["interior"] and decay is not None and decay <=
                                  spec["gates"]["interior_max_decay"])
    out["representative"] = rep

    for name, kw in STRESS + EXTRA_TRACKS:
        applied = dict(kw)
        sets = dir_sets_delay if kw.pop("lag_bars", 0) else dir_sets
        slip_k = kw.pop("slip_ticks", slip)
        applied["slip_ticks"] = slip_k
        r = wf_run(cohort, days["full"][0], days["full"][1], sel_cell, sets, pairs, rail,
                   slip_k, START_EQUITY, **kw)
        rec = _cell_record(cohort, r, name)
        rec["applied_kwargs"] = applied
        out["tracks"][name] = rec
    r = wf_run(cohort, days["historical"][0], days["historical"][1], sel_cell, dir_sets, pairs,
               rail, slip, START_EQUITY, use_funding=False)
    out["tracks"]["no_funding"] = _cell_record(cohort, r, "no_funding")

    # ---- parameter neighbourhood: walk-forward cell neighbours + EMA neighbours ----
    nb = []
    for a, b in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        if 0 <= ti + a < nt and 0 <= si + b < ns:
            n_cell = cells[(ti + a) * ns + (si + b)]
            row = [c for c in out["cells"]
                   if c["cell"] == list(n_cell) and c["window"] == "historical"][0]
            nb.append({"probe": "window_cell_%d_%d" % (a, b), "cell": n_cell,
                       "net_pnl": row["net_pnl"], "legal": True})
    for label, di, dj in (("ema_fast_up", 1, 0), ("ema_fast_down", -1, 0),
                          ("ema_slow_up", 0, 1), ("ema_slow_down", 0, -1)):
        r = wf_run(cohort, days["historical"][0], days["historical"][1], sel_cell, dir_sets, pairs,
                   rail, slip, START_EQUITY, selection=("shift", di, dj))
        nb.append({"probe": label, "cell": sel_cell, "net_pnl": r["net_pnl"],
                   "legal": r["n_steps"] > 0, "steps_used": r["n_steps"]})
    base_sign = hist_sel["net_pnl"] > 0
    legal = [n for n in nb if n.get("legal", True)]
    agree = sum(1 for n in legal if (n["net_pnl"] > 0) == base_sign)
    out["neighbours"] = nb
    out["neighbour_agreement"] = (agree / len(legal)) if legal else 0.0
    out["neighbour_legal"] = len(legal)
    return out


def _cell_record(cohort, r, window):
    return {"symbol": cohort.symbol, "timeframe": cohort.timeframe, "window": window,
            "cell": list(r["cell"]), "n_steps": r["n_steps"],
            "net_pnl": round(r["net_pnl"], 6), "fees": round(r["fees"], 6),
            "funding": round(r["funding"], 6), "gross_pnl": round(r["gross_pnl"], 6),
            "traded_notional": round(r["traded_notional"], 6),
            "ending_equity": round(r["ending_equity"], 6),
            "sharpe": r["sharpe"], "max_dd_usdt": round(r["max_dd_usdt"], 6),
            "max_dd_pct": round(r["max_dd_pct"], 6), "cagr": r["cagr"],
            "annualized_return": r["annualized_return"],
            "episodes": r["episodes"], "tp_hits": r["tp_hits"], "stop_hits": r["stop_hits"],
            "flips": r["flips"], "open_at_end": r["open_at_end"],
            "margin_calls": r["margin_calls"], "bars_in_market": r["bars_in_market"],
            "min_entry_equity": round(r["min_entry_equity"], 6),
            "max_effective_leverage": round(r["max_effective_leverage"], 6),
            "capital_utilization": round(r["capital_utilization"], 6),
            "days": r["days"], "years": round(r["years"], 6),
            "capital_exhausted": r["capital_exhausted"],
            "layers": r["layers"]}


# ---------------------------------------------------------------------------
# phase 4: pre-registered gate (applied to the measured evidence)
# ---------------------------------------------------------------------------


def summarize(spec, cohorts_out, extra):
    import statistics as st
    gates = spec["gates"]
    cells = [c for co in cohorts_out for c in co["cells"]]
    reps = [co["representative"] for co in cohorts_out]
    n_hist_cells = sum(1 for c in cells if c["window"] == "historical")

    coverage = {"historical_cells": n_hist_cells,
                "oos_representatives": sum(1 for r in reps if r["oos"]),
                "full_representatives": sum(1 for r in reps if r["full"])}
    for name, _ in STRESS + EXTRA_TRACKS:
        coverage[name] = sum(1 for co in cohorts_out if name in co["tracks"])
    required = {"historical_cells": spec["expected_phase_cases"]["historical_cells"],
                "oos_representatives": spec["expected_cohorts"],
                "full_representatives": spec["expected_cohorts"]}
    for name, _ in STRESS + EXTRA_TRACKS:
        required[name] = spec["expected_cohorts"]
    coverage_complete = all(coverage[k] == required[k] for k in required)

    thin = {("%s/%s" % (co["symbol"], co["timeframe"])):
            min([c["episodes"] for c in co["cells"] if c["window"] == "historical"] or [0])
            for co in cohorts_out}
    thin_list = sorted(k for k, v in thin.items() if v < gates["min_episodes_is"])
    insufficient_trades = len(thin_list) >= gates["min_thin_cohorts_reject"]

    def med(rows, key):
        vals = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
        return st.median(vals) if vals else None

    med_hist_pnl = med([c for c in cells if c["window"] == "historical"], "net_pnl")
    med_hist_sharpe = med([c for c in cells if c["window"] == "historical"], "sharpe")
    med_hist_nofund = med([co["tracks"]["no_funding"] for co in cohorts_out], "net_pnl")
    reps_hist_nofund = [co["tracks"]["no_funding"] for co in cohorts_out]
    med_rep_hist_pnl = med([r["hist"] for r in reps], "net_pnl")
    med_oos_sharpe = med([r["oos"] for r in reps], "sharpe")
    med_oos_cagr = med([r["oos"] for r in reps], "cagr")

    neighbor_ok = sum(1 for co in cohorts_out
                      if co["neighbour_agreement"] >= gates["neighborhood_min_same_sign_fraction"])
    interior_ok = sum(1 for r in reps if r["interior_stable"])

    stress_summary = {}
    stress_ok = True
    for name in GATE_STRESS:
        rows = [co["tracks"][name] for co in cohorts_out]
        mp, ms = med(rows, "net_pnl"), med(rows, "sharpe")
        stress_summary[name] = {"median_net_pnl": mp, "median_sharpe": ms, "cases": len(rows),
                                "median_cagr": med(rows, "cagr")}
        if mp is None or ms is None or mp <= 0 or ms <= 0:
            stress_ok = False
    cost_rows = [co["tracks"]["cost_attrition_40bps_per_fill"] for co in cohorts_out]
    med_cost_annual = med(cost_rows, "annualized_return")
    med_cost_annual_base = med([r["full"] for r in reps], "annualized_return")
    stress_summary["cost_attrition_40bps_per_fill"] = {
        "median_net_pnl": med(cost_rows, "net_pnl"), "median_sharpe": med(cost_rows, "sharpe"),
        "median_cagr": med(cost_rows, "cagr"), "median_annualized_return": med_cost_annual,
        "cases": len(cost_rows)}

    checks = {
        "G1_coverage_complete": coverage_complete,
        "G2_sufficient_trades": not insufficient_trades,
        "G3_historical_economic": (med_hist_pnl is not None and med_hist_pnl > 0
                                   and med_hist_sharpe is not None and med_hist_sharpe > 0),
        "G4_oos_economic": (med_oos_sharpe is not None and med_oos_sharpe >= gates["oos_min_sharpe"]
                            and med_oos_cagr is not None
                            and med_oos_cagr >= gates["oos_min_annual_return"]),
        "G5_funding_stability": ((med_rep_hist_pnl is not None and med_rep_hist_pnl > 0)
                                 == (med_hist_nofund is not None and med_hist_nofund > 0)),
        "G6_parameter_neighbourhood": neighbor_ok >= gates["neighborhood_min_cohorts"],
        "G7_robustness_economic": stress_ok,
        "G8_interior_stability": interior_ok >= gates["interior_min_cohorts"],
        "G9_cost_attrition": (med_cost_annual is not None and med_cost_annual > 0.0),
    }
    verdict = ("TECHNICAL_INCOMPLETE" if not coverage_complete
               else "PASS" if all(checks.values()) else "REJECT")
    failed = sorted(k for k, v in checks.items() if not v)

    falsification_map = [
        ("G1_coverage_complete", "robustness incomplete (a pre-registered phase grid is not fully covered)"),
        ("G2_sufficient_trades", "no signal / insufficient trades"),
        ("G3_historical_economic", "historical economic rejection"),
        ("G4_oos_economic", "OOS economic rejection: median net annualised Sharpe < 0.40 or net annual return < 0%"),
        ("G5_funding_stability", "official-funding baseline instability"),
        ("G6_parameter_neighbourhood", "parameter neighbourhood fragility (walk-forward window AND EMA neighbours)"),
        ("G7_robustness_economic", "robustness economic fail"),
        ("G8_interior_stability", "interior-stability gate (selected cell on the search boundary, or OOS Sharpe decay > 35%)"),
        ("G9_cost_attrition", "cost attrition: the 0.40%/fill cost track turns the return non-positive"),
    ]
    triggered = [label for gid, label in falsification_map if gid in failed]

    layers = [0] * 12
    for co in cohorts_out:
        for k in range(12):
            layers[k] += co["representative"]["full"]["layers"][k]
    reps_full = [r["full"] for r in reps]
    # A cost/lag stress track that leaves every cohort's number untouched is a dead parameter,
    # not a passing robustness test (this is exactly how a fee-accounting bug stays invisible).
    track_deltas = {}
    for name, _ in STRESS:
        track_deltas[name] = sum(
            1 for co in cohorts_out
            if abs(co["tracks"][name]["net_pnl"] - co["representative"]["full"]["net_pnl"]) > 1e-9)
    for name, _ in EXTRA_TRACKS:
        base = "hist" if name == "no_funding" else "full"
        track_deltas[name] = sum(
            1 for co in cohorts_out
            if abs(co["tracks"][name]["net_pnl"] - co["representative"][base]["net_pnl"]) > 1e-9)
    keys = ("net_pnl", "fees", "funding", "gross_pnl", "ending_equity", "sharpe", "max_dd_pct",
            "max_dd_usdt", "max_effective_leverage", "capital_utilization", "cagr",
            "annualized_return", "episodes", "traded_notional")
    assertions = dict(extra["assertions"])
    assertions.update({
        "coverage_all_declared": coverage_complete,
        # A cost/lag stress that leaves every cohort's number untouched is a dead parameter
        # rather than a passing robustness test; the fee/slippage tracks must always move the
        # number whenever any fill happens. (funding_2x / no_funding / entry_delay can legitimately
        # be no-ops for a cohort, so they are reported as a diagnostic instead.)
        "cost_stress_tracks_differ": all(track_deltas.get(n, 0) > 0 for n in
                                         ("fee_2x", "source_reported_10bps",
                                          "cost_attrition_40bps_per_fill", "slippage_2ticks")),
        "cohort_count_20": len(cohorts_out) == spec["expected_cohorts"],
        "layer0_equals_episodes": layers[0] == sum(r["episodes"] for r in reps_full)
        and layers[0] > 0,
        "layer_histogram_nonempty": sum(layers) > 0,
        "reserve_tranche_never_deployed": layers[11] == 0,
        "no_entry_on_exhausted_account": all(r["min_entry_equity"] > 0.0 for r in reps_full),
        "ending_equity_floor": all(r["ending_equity"] > -1.5 * START_EQUITY for r in reps_full),
        "episode_partition": all(
            r["episodes"] == r["tp_hits"] + r["stop_hits"] + r["flips"] + r["open_at_end"]
            + r["margin_calls"] for r in reps_full),
        "pnl_decomposition": all(
            abs(r["gross_pnl"] - r["fees"] - r["funding"] - r["net_pnl"]) <= 1e-3
            for r in reps_full),
    })

    return {
        "family_id": spec["family_id"], "round_id": spec["round_id"], "run_id": spec["run_id"],
        "coverage": coverage, "coverage_required": required, "coverage_complete": coverage_complete,
        "cohorts": ["%s/%s" % (co["symbol"], co["timeframe"]) for co in cohorts_out],
        "cohort_count": len(cohorts_out),
        "legal_cases_historical_cells": n_hist_cells,
        "gate_checks": checks, "failed_gates": failed, "verdict_recommendation": verdict,
        "falsification_triggered": triggered,
        "median_historical_cells": {k: med([c for c in cells if c["window"] == "historical"], k)
                                    for k in keys},
        "median_oos_representatives": {k: med([r["oos"] for r in reps], k) for k in keys},
        "median_full_representatives": {k: med(reps_full, k) for k in keys},
        "median_historical_representatives": {k: med([r["hist"] for r in reps], k) for k in keys},
        "median_hist_net_pnl_representatives": med_rep_hist_pnl,
        "median_hist_net_pnl_representatives_no_funding": med_hist_nofund,
        "median_oos_sharpe": med_oos_sharpe, "median_oos_cagr": med_oos_cagr,
        "representatives": reps,
        "cross_cohort_positive_representatives": sum(1 for r in reps
                                                     if (r["full"]["net_pnl"] or 0) > 0),
        "cross_cohort_total": len(reps),
        "cross_cohort_positive_oos_representatives": sum(1 for r in reps
                                                         if (r["oos"]["net_pnl"] or 0) > 0),
        "selected_cells": {"%s/%s" % (co["symbol"], co["timeframe"]): co["representative"]["cell"]
                           for co in cohorts_out},
        "interior_cohorts": interior_ok,
        "interior_min_cohorts": gates["interior_min_cohorts"],
        "neighbourhood_cohorts_ok": neighbor_ok,
        "neighbour_agreement": {"%s/%s" % (co["symbol"], co["timeframe"]):
                                round(co["neighbour_agreement"], 4) for co in cohorts_out},
        "min_episodes_per_cohort": thin, "thin_cohorts": thin_list,
        "stress_summary": stress_summary,
        "stress_track_delta_cohorts": track_deltas,
        "stress_tracks_without_delta": sorted(k for k, v in track_deltas.items() if v == 0),
        "median_full_annualized_return_baseline": med_cost_annual_base,
        "dca_layer_histogram": {"level_%02d" % k: layers[k] for k in range(12)},
        "best_full_case": max(reps_full, key=lambda r: (r["sharpe"] if r["sharpe"] is not None
                                                        else -1e18)) if reps_full else None,
        "worst_full_case": min(reps_full, key=lambda r: (r["sharpe"] if r["sharpe"] is not None
                                                         else 1e18)) if reps_full else None,
        "assertions": assertions,
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main():
    if len(sys.argv) != 2:
        sys.stderr.write("usage: 30_strategy_b_run.py <run-spec.json>\n")
        return 2
    spec_path = os.path.abspath(sys.argv[1])
    with open(spec_path) as fh:
        spec = json.load(fh)
    attempt_dir = os.path.dirname(spec_path)
    os.makedirs(os.path.join(attempt_dir, "artifacts"), exist_ok=True)
    os.makedirs(os.path.join(attempt_dir, "logs"), exist_ok=True)
    run_log = log
    started = time.time()
    try:
        self_sha = sha256_file(os.path.abspath(__file__))
        if self_sha != spec["script"]["sha256"]:
            raise SystemExit("script sha256 mismatch: running %s spec %s"
                             % (self_sha, spec["script"]["sha256"]))
        atomic_write_json(os.path.join(attempt_dir, "state.json"), {
            "schema_version": 1, "family_id": spec["family_id"], "round_id": spec["round_id"],
            "run_id": spec["run_id"], "stage": "RUNNING_QLIB",
            "updated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "note": "started"})

        inst_path = os.path.join(RAW_ROOT, "instruments", "usdm-perp-instruments.json")
        with open(inst_path) as fh:
            inst_doc = json.load(fh)
        instruments = {}
        for item in inst_doc["instruments"]:
            f = item["fields"]
            instruments[f["raw_symbol"]] = {
                "price_increment": float(f["price_increment"]),
                "maker_fee": float(f["maker_fee"]), "taker_fee": float(f["taker_fee"]),
                "margin_init": float(f["margin_init"]), "margin_maint": float(f["margin_maint"]),
                "min_notional": f["min_notional"], "id": f["id"]}
        manifest = {"instruments_sha256": sha256_file(inst_path), "raw_files": {}}
        for symbol in spec["data"]["symbols"]:
            for tf in spec["data"]["timeframes"]:
                for src in raw_months(symbol, tf["raw_interval"], spec["data"]["start"],
                                      spec["data"]["end"]):
                    manifest["raw_files"][os.path.relpath(src, "/data/raw")] = sha256_file(src)
            fp = os.path.join(RAW_ROOT, "funding", symbol, "%s-funding.jsonl.gz" % symbol)
            manifest["raw_files"][os.path.relpath(fp, "/data/raw")] = sha256_file(fp)
        atomic_write_json(os.path.join(attempt_dir, "artifacts", "input_manifest.json"), manifest)
        run_log("input manifest: %d raw files hashed" % len(manifest["raw_files"]))

        bins = build_bins(spec, attempt_dir, run_log)

        cohorts_out = []
        total = len(spec["data"]["symbols"]) * len(spec["data"]["timeframes"])
        done = 0
        for symbol in spec["data"]["symbols"]:
            for tf in spec["data"]["timeframes"]:
                t_c = time.time()
                cohort = Cohort(symbol, tf, spec["data"]["start"], spec["data"]["end"])
                meta = instruments[symbol]
                cohort.price_increment = meta["price_increment"]
                cohort.taker_fee = meta["taker_fee"]
                cohort.leverage = 1.0 / meta["margin_init"]
                cohort.margin_maint = meta["margin_maint"]
                ft, fr, excluded = load_funding(symbol, spec["data"]["start"], spec["data"]["end"])
                cohort.funding, applied = funding_per_bar(cohort, ft, fr)
                run_log("cohort %s/%s bars=%d days=%d funding_official=%d modeled_excluded=%d"
                        % (symbol, tf["raw_interval"], cohort.n, cohort.n_days, applied, excluded))
                res = run_cohort(spec, cohort, instruments, run_log)
                res["funding_coverage"] = {"official": applied, "modeled_excluded": excluded}
                cohorts_out.append(res)
                run_log("cohort %s/%s done in %.1fs" % (symbol, tf["raw_interval"],
                                                        time.time() - t_c))
                done += 1
                atomic_write_json(os.path.join(attempt_dir, "artifacts", "progress.json"),
                                  {"cohorts_done": done, "cohorts_total": total,
                                   "updated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                                   time.gmtime())})

        all_cells = [c for co in cohorts_out for c in co["cells"]]
        _write_csv(os.path.join(attempt_dir, "artifacts", "walk_forward_cells.csv"), all_cells)
        step_rows = [s for co in cohorts_out for s in co["steps"]]
        _write_csv(os.path.join(attempt_dir, "artifacts", "walk_forward_steps.csv"), step_rows)
        _write_csv(os.path.join(attempt_dir, "artifacts", "representatives.csv"),
                   [dict(co["representative"]["hist"], window="historical_rep") for co in cohorts_out]
                   + [dict(co["representative"]["oos"], window="oos_rep") for co in cohorts_out]
                   + [dict(co["representative"]["full"], window="full_rep") for co in cohorts_out])
        _write_csv(os.path.join(attempt_dir, "artifacts", "tracks.csv"),
                   [dict(co["tracks"][name], window=name)
                    for co in cohorts_out for name in sorted(co["tracks"])])
        atomic_write_json(os.path.join(attempt_dir, "artifacts", "gate_detail.json"),
                          {"selected_cells": {("%s/%s" % (co["symbol"], co["timeframe"])):
                                              co["representative"] for co in cohorts_out},
                           "neighbours": {("%s/%s" % (co["symbol"], co["timeframe"])):
                                          co["neighbours"] for co in cohorts_out}})

        extra = {"assertions": {
            "selected_cell_reproduces_grid_row": all(
                all(co["hist_determinism"].values()) for co in cohorts_out),
            "step_trace_nonempty": len(step_rows) > 0,
        }}
        summary = summarize(spec, cohorts_out, extra)
        summary["runtime_seconds"] = int(time.time() - started)
        summary["bins_build"] = {k: bins[k] for k in
                                 ("build_wall_seconds", "qlib_dir_bytes", "qlib_version")}
        summary["instrument_metadata"] = {s: instruments[s] for s in spec["data"]["symbols"]}
        summary["data_readback"] = bins["readback"]
        summary["funding_coverage"] = {("%s/%s" % (co["symbol"], co["timeframe"])):
                                       co["funding_coverage"] for co in cohorts_out}
        summary["generated_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        atomic_write_json(os.path.join(attempt_dir, "artifacts", "assertions.json"),
                          summary["assertions"])
        atomic_write_json(os.path.join(attempt_dir, "artifacts", "dca_layer_histogram.json"),
                          summary["dca_layer_histogram"])
        atomic_write_json(os.path.join(attempt_dir, "result.json"), summary)
        atomic_write_json(os.path.join(attempt_dir, "state.json"), {
            "schema_version": 1, "family_id": spec["family_id"], "round_id": spec["round_id"],
            "run_id": spec["run_id"], "stage": "ARTIFACT_READY",
            "updated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "note": "all phases complete"})
        with open(os.path.join(attempt_dir, "logs", "summary_stdout.json"), "w") as fh:
            fh.write(json.dumps(_sanitize(summary), indent=2, ensure_ascii=False,
                                allow_nan=False) + "\n")
        print(json.dumps(_sanitize(summary), indent=2, ensure_ascii=False, allow_nan=False))
    except Exception as exc:  # noqa: BLE001 - a failure must still be visible
        import traceback
        traceback.print_exc()
        atomic_write_json(os.path.join(attempt_dir, "state.json"), {
            "schema_version": 1, "family_id": spec.get("family_id"),
            "round_id": spec.get("round_id"), "run_id": spec.get("run_id"),
            "stage": "FAILED_SCRIPT",
            "updated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "note": "%s: %s" % (type(exc).__name__, exc)})
        return 3
    return 0


def _write_csv(path, rows):
    if not rows:
        return
    fields = list(rows[0].keys())
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict))
                            else v) for k, v in r.items()})


if __name__ == "__main__":
    sys.exit(main())
