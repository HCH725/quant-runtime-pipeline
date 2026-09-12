#!/usr/bin/env python3
"""Strategy A — close-vs-SMA mean reversion (LONG/FLAT) full backtest, on Qlib.

Entry point for ONE pre-registered attempt.  Reads the immutable run-spec from
/results (the only source of parameters), builds the Qlib .bin store from the
READ-ONLY canonical raw store into /qlib/work, then runs the pre-registered
full-backtest contract (Contract v1.3.0 section 7.2):

    symbols x timeframes x STRATEGY parameter domain x DCA parameter domain x
    (historical / OOS / robustness)

DCA is executed as real order/fill accounting (an episode state machine over the
bars); nothing is estimated after the fact.  Every legal
(strategy params x DCA config) combination of every registered cohort is
evaluated on every registered phase grid, and the family gate is the
**cohort-level survivor** rule of contract section 7.3: one deterministic
historical-only winner per cohort, then OOS / full / robustness / parameter
neighbourhood evidence for that same winner.  Cross-cohort medians are
descriptive diagnostics only - they are NOT a gate (v1.3.0).

Writes only:
  * /qlib/work/**      (rebuildable derived/cache area, INV-5)
  * <attempt_dir>/**   (result.json, artifacts/, logs/, state.json)
Never writes /data/raw (read-only mount).

usage: 20_strategy_a_run.py <run-spec.json>
"""
from __future__ import annotations

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
import calendar

import numpy as np

VENV_PYTHON = "/opt/venv/bin/python"
DUMP_BIN = "/opt/qlib-tools/dump_bin.py"
RAW_ROOT = "/data/raw/binance/usdm"
WORK_ROOT = "/qlib/work/strategyA-v1"
CSV_ROOT = os.path.join(WORK_ROOT, "csv")
QLIB_DIR = os.path.join(WORK_ROOT, "qlib-data")
MS_PER_DAY = 86400000
FIELDS = ["open", "high", "low", "close", "volume"]
START_EQUITY = 30000.0
STRESS = [
    ("fee_2x", {"fee_mult": 2.0}),
    ("funding_2x", {"funding_mult": 2.0}),
    ("entry_delay_1_bar", {"entry_delay_1_bar": True}),
    ("slippage_2ticks", {"slip_ticks": 2}),
]
# Registered phase grids (v1.3.0: every one of them covers the FULL
# strategy-domain x DCA-domain product of every cohort - contract 7.2).
COHORT_GRID_KINDS = ("historical", "oos", "full", "fee_2x", "funding_2x",
                     "entry_delay_1_bar", "slippage_2ticks", "no_funding",
                     "no_funding_full")
# Joint parameter space axes, in the one registered order used for the
# deterministic lexical tie-break (contract 7.3).  Order is part of the gate.
AXES = ("window", "discount", "spacing_pct", "size_multiplier",
        "breakeven_tp_pct", "invalidation_pct")
SELECTOR_VERSION = "cohort-selector-v1"
DISPOSITION_VERSION = "cohort-disposition-v1"
WINNER_METRIC_KEYS = ("net_pnl", "sharpe", "episodes", "ending_equity", "fees", "funding",
                      "max_dd_usdt", "max_dd_pct", "max_effective_leverage",
                      "capital_utilization")
LAYER_TOTALS = {}


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
    """Day-granularity dates -> qlib timestamps covering the WHOLE inclusive final day.

    qlib parses a bare 'YYYY-MM-DD' as 00:00:00, so passing `end` unchanged silently drops
    the last day's remaining bars (measured: 2022-01-01..2022-03-31 lost 23 of 2160 1h bars).
    """
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
        self.day_end = set(int(i) for i in np.flatnonzero(
            np.concatenate([day_id[1:] != day_id[:-1], [True]])))
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


def funding_per_bar(cohort, fund_times, fund_rates, mult):
    """Attach each official funding event to the bar whose close is <= the event time."""
    out = np.zeros(cohort.n, dtype=np.float64)
    if len(fund_times) == 0:
        return out, 0
    # side='left' - 1 = the bar that has already closed at (or before) the funding time
    idx = np.searchsorted(cohort.open_time_ms, fund_times, side="left") - 1
    applied = 0
    for k in range(len(idx)):
        i = int(idx[k])
        if 0 <= i < cohort.n:
            out[i] += fund_rates[k] * mult
            applied += 1
    return out, applied


def rolling_sma(close, w):
    n = len(close)
    sma = np.full(n, np.nan)
    if n >= w:
        cs = np.concatenate([[0.0], np.cumsum(close)])
        sma[w - 1:] = (cs[w:] - cs[:-w]) / float(w)
    return sma


def simulate(cohort, window, rail, p, stress, slip_ticks, kind):
    """One pre-registered parameter case over one window slice (i0, i1)."""
    i0, i1 = window
    C = cohort.close[i0:i1].tolist()
    H = cohort.high[i0:i1].tolist()
    L = cohort.low[i0:i1].tolist()
    O = cohort.open[i0:i1].tolist()
    n = len(C)
    if stress.get("entry_delay_1_bar"):
        Ce = cohort.close[i0 + 1:i1 + 1].tolist()
    else:
        Ce = C

    disc = p["discount"]
    sma = rolling_sma(cohort.close[i0:i1], p["window"])
    valid = ~np.isnan(sma)
    over = np.zeros(n, dtype=bool)
    over[valid] = cohort.close[i0:i1][valid] < sma[valid] * (1.0 - disc)
    prev = np.concatenate([[False], over[:-1]])
    edge = np.flatnonzero(over & ~prev)

    if stress.get("no_funding"):
        fper = np.zeros(n, dtype=np.float64)
    else:
        fper = cohort.funding[i0:i1] * stress.get("funding_mult", 1.0)

    tick = cohort.price_increment
    taf = cohort.taker_fee * stress.get("fee_mult", 1.0)
    lev = cohort.leverage
    d0 = rail["spacing_d0"]
    tp_pct = rail["tp"]
    inval = rail["invalidation"]
    mult = rail["size_multiplier"]
    base = rail["base_quote"]

    day_of_bar = cohort.day_index[i0:i1].tolist()
    de_set = cohort.day_end
    day_equity = [None] * cohort.n_days

    layers = [0] * 12
    realized = funding_paid = 0.0
    fees_total = 0.0
    episodes = tp_hits = stop_hits = open_at_end = margin_calls = 0
    bars_in_market = 0
    max_lev = 0.0
    util_sum = 0.0

    n_edges = len(edge)
    pos = 0
    halted = False
    min_entry_equity = START_EQUITY
    while pos < n_edges:
        # registered capital-exhaustion semantics: the account cannot lose more than itself,
        # so no new position may be opened once the realised equity is gone
        if START_EQUITY + realized <= 0.0:
            halted = True
            break
        e = int(edge[pos])
        min_entry_equity = min(min_entry_equity, START_EQUITY + realized)
        px = Ce[e] + slip_ticks * tick if e < len(Ce) else C[e] + slip_ticks * tick
        p0 = px
        qty = base * lev / px
        cost = qty * px
        ep_fees = qty * px * taf
        ep_fund = 0.0
        levels = [p0 * (1.0 - d0 * k) for k in range(12)]
        layers[0] += 1
        exit_bar = n - 1
        broke = False
        for t in range(e + 1, n):
            f = fper[t]
            if f != 0.0:
                ep_fund += qty * C[t] * f
            l = L[t]
            h = H[t]
            o = O[t]
            # Descending-price walk inside the bar (worst case for a long: the bar LOW is
            # reached first, then the high).  The invalidation is a RESTING stop at
            # average_cost x (1 - invalidation), so a ladder level below it can never fill:
            # whichever trigger price is higher is reached first as the price descends.
            k = 1
            killed_at = None
            while True:
                stop = (cost / qty) * (1.0 - inval)
                if k <= 10 and levels[k] >= stop:
                    trig, is_stop = levels[k], False
                else:
                    trig, is_stop = stop, True
                if l > trig:
                    break
                if is_stop:
                    killed_at = trig if o >= trig else o  # gap through the stop fills at the open
                    break
                fpx = trig + slip_ticks * tick
                q = base * (mult ** k) * lev / fpx
                qty += q
                cost += q * fpx
                ep_fees += q * fpx * taf
                layers[k] += 1
                k += 1
            eq = START_EQUITY + realized
            ueq = eq + qty * C[t] - cost
            if killed_at is None and ueq <= cohort.margin_maint * qty * C[t]:
                xpx = C[t] - slip_ticks * tick  # capital-exhaustion backstop
                realized += qty * xpx - cost
                ep_fees += qty * xpx * taf
                exit_bar, broke, margin_calls = t, True, margin_calls + 1
                break
            if ueq > 0:
                lv = (qty * C[t]) / ueq
                if lv > max_lev:
                    max_lev = lv
                util_sum += (cost / lev) / ueq
            bars_in_market += 1
            if t in de_set:
                day_equity[day_of_bar[t]] = ueq
            if killed_at is not None:
                xpx = killed_at - slip_ticks * tick
                realized += qty * xpx - cost
                ep_fees += qty * xpx * taf
                exit_bar, broke, stop_hits = t, True, stop_hits + 1
                break
            tpx = (cost / qty) * (1.0 + tp_pct)  # reduce-only breakeven-anchored TP
            if h >= tpx:
                xpx = tpx - slip_ticks * tick
                realized += qty * xpx - cost
                ep_fees += qty * xpx * taf
                exit_bar, broke, tp_hits = t, True, tp_hits + 1
                break
        exit_kind = "CLOSED" if broke else "EOD_OPEN"
        if not broke:
            xpx = C[-1] - slip_ticks * tick
            realized += qty * xpx - cost
            ep_fees += qty * xpx * taf
            open_at_end += 1
        fees_total += ep_fees
        funding_paid += ep_fund
        realized -= ep_fund
        episodes += 1
        day_equity[day_of_bar[exit_bar]] = START_EQUITY + realized
        pos = int(np.searchsorted(edge, exit_bar + 1, side="left"))

    if kind == "full":
        acc = LAYER_TOTALS.setdefault("full", [0] * 12)
        for k in range(12):
            acc[k] += layers[k]

    series = []
    last = START_EQUITY
    for v in day_equity:
        if v is not None:
            last = v
        series.append(last)
    series = np.array(series, dtype=np.float64)
    if len(series) > 2:
        rets = np.diff(series) / series[:-1]
        sd = float(np.std(rets, ddof=1))
        sharpe = float(np.mean(rets) / sd * np.sqrt(365.0)) if sd > 0 else 0.0
    else:
        sharpe = 0.0
    peak = np.maximum.accumulate(series)
    dd = series - peak
    dd_pct = dd / peak
    span_days = (cohort.open_time_ms[i1 - 1] - cohort.open_time_ms[i0]) / 86400000.0
    years = max(span_days / 365.25, 1.0 / 365.25)
    end_eq = START_EQUITY + realized
    return {
        "net_pnl": realized, "fees": fees_total, "funding": funding_paid,
        "gross_pnl": realized + fees_total + funding_paid,
        "episodes": episodes, "tp_hits": tp_hits, "stop_hits": stop_hits,
        "open_at_end": open_at_end, "margin_calls": margin_calls,
        "halted": halted, "min_entry_equity": min_entry_equity,
        "ending_equity": end_eq, "sharpe": sharpe,
        "max_dd_usdt": float(dd.min()) if len(dd) else 0.0,
        "max_dd_pct": float(dd_pct.min()) if len(dd_pct) else 0.0,
        "max_effective_leverage": max_lev,
        "capital_utilization": (util_sum / bars_in_market) if bars_in_market else 0.0,
        "bars_in_market": bars_in_market, "days": int(cohort.n_days), "years": years,
        "total_return_pct": end_eq / START_EQUITY - 1.0,
        # growth ratios are undefined once the account is non-positive -> null, never NaN
        "cagr": ((end_eq / START_EQUITY) ** (1.0 / years) - 1.0) if end_eq > 0.0 else None,
        "annualized_return": ((end_eq / START_EQUITY - 1.0) / years) if end_eq > 0.0 else None,
        "layers": layers, "exit_kind": exit_kind,
    }


def rail_for(dca):
    """One registered DCA configuration -> the engine rail.

    Only the four registered DCA axes are mapped (plus the registered capital
    basis).  The engine itself (`simulate`) is unchanged: the ladder geometry,
    breakeven-anchored TP and resting invalidation semantics of v1 are inherited
    verbatim - this release parametrises them, it does not re-interpret them.
    """
    return {"base_quote": dca["base_quote"], "spacing_d0": dca["spacing_pct"],
            "size_multiplier": dca["size_multiplier"], "tp": dca["breakeven_tp_pct"],
            "invalidation": dca["invalidation_pct"]}


def record(cohort, p, dca, kind, m):
    return {"symbol": cohort.symbol, "timeframe": cohort.timeframe, "window_kind": kind,
            "window": p["window"], "discount": p["discount"],
            "spacing_pct": dca["spacing_pct"], "size_multiplier": dca["size_multiplier"],
            "breakeven_tp_pct": dca["breakeven_tp_pct"],
            "invalidation_pct": dca["invalidation_pct"],
            "net_pnl": round(m["net_pnl"], 6), "fees": round(m["fees"], 6),
            "funding": round(m["funding"], 6), "gross_pnl": round(m["gross_pnl"], 6),
            "ending_equity": round(m["ending_equity"], 6),
            "episodes": m["episodes"], "tp_hits": m["tp_hits"], "stop_hits": m["stop_hits"],
            "open_at_end": m["open_at_end"], "margin_calls": m["margin_calls"],
            "halted": m["halted"], "min_entry_equity": round(m["min_entry_equity"], 6),
            "sharpe": round(m["sharpe"], 6),
            "max_dd_usdt": round(m["max_dd_usdt"], 6), "max_dd_pct": round(m["max_dd_pct"], 6),
            "max_effective_leverage": round(m["max_effective_leverage"], 6),
            "capital_utilization": round(m["capital_utilization"], 6),
            "bars_in_market": m["bars_in_market"], "days": m["days"],
            "years": round(m["years"], 6), "cagr": m["cagr"],
            "total_return_pct": round(m["total_return_pct"], 6),
            "annualized_return": m["annualized_return"]}


def run_cohort(spec, cohort, run_log):
    """Every legal (strategy params x DCA config) case of one cohort, on every registered grid."""
    grid = spec["params"]["grid"]
    dca_grid = spec["dca_domain"]["grid"]
    slip = spec["costs"]["baseline_slippage_ticks"]
    windows = {"historical": (spec["data"]["historical_start"], spec["data"]["historical_end"]),
               "oos": (spec["data"]["oos_start"], spec["data"]["oos_end"]),
               "full": (spec["data"]["start"], spec["data"]["end"])}
    rows = {k: [] for k in COHORT_GRID_KINDS}

    slices = {k: cohort.slice(*v) for k, v in windows.items()}
    for kind in ("historical", "oos", "full"):
        sl = slices[kind]
        run_log("window %s %s/%s bars=[%d,%d) cases=%d"
                % (kind, cohort.symbol, cohort.timeframe, sl[0], sl[1],
                   len(grid) * len(dca_grid)))
        for p in grid:
            for dca in dca_grid:
                rows[kind].append(record(cohort, p, dca, kind,
                                         simulate(cohort, sl, rail_for(dca), p, {}, slip, kind)))

    sl = slices["full"]
    for sname, stress in STRESS:
        for p in grid:
            for dca in dca_grid:
                rows[sname].append(record(cohort, p, dca, sname,
                                          simulate(cohort, sl, rail_for(dca), p, stress,
                                                   stress.get("slip_ticks", slip), sname)))
    # official-funding baseline check runs on the SAME window as the historical gate
    for p in grid:
        for dca in dca_grid:
            rows["no_funding"].append(record(cohort, p, dca, "no_funding",
                                             simulate(cohort, slices["historical"],
                                                      rail_for(dca), p,
                                                      {"no_funding": True}, slip, "no_funding")))
            rows["no_funding_full"].append(record(cohort, p, dca, "no_funding_full",
                                                  simulate(cohort, sl, rail_for(dca), p,
                                                           {"no_funding": True}, slip,
                                                           "no_funding_full")))
    return rows


# ---------------------------------------------------------------------------
# phase 3: pre-registered cohort-selector / cohort-survivor gate
#          (contract v1.3.0 sections 7.2 and 7.3; no post-hoc tuning)
# ---------------------------------------------------------------------------

def axis_values(spec):
    """The registered value list of every joint-space axis, in the registered order."""
    return {"window": list(spec["params"]["grid_windows"]),
            "discount": list(spec["params"]["grid_discounts"]),
            "spacing_pct": list(spec["dca_domain"]["spacing_pct"]),
            "size_multiplier": list(spec["dca_domain"]["size_multiplier"]),
            "breakeven_tp_pct": list(spec["dca_domain"]["breakeven_tp_pct"]),
            "invalidation_pct": list(spec["dca_domain"]["invalidation_pct"])}


def cell_key(r):
    return tuple(r[a] for a in AXES)


def tie_break_key(r, axes):
    """Registered-index lexical key: deterministic and free of float formatting."""
    return tuple(axes[a].index(r[a]) for a in AXES)


def require_historical(rows, where):
    """Selection and neighbourhood judgement may never read OOS (contract 7.3).

    Made executable: the two functions that decide a cohort winner take rows and
    refuse anything that was not measured on the historical window.
    """
    bad = [r for r in rows if r.get("window_kind") != "historical"]
    if bad:
        raise ValueError("%s must be given historical rows only (contract 7.3): %d violation(s)"
                         % (where, len(bad)))


def select_cohort_winner(hist_rows, spec):
    """One deterministic winner per cohort, historical window only.

    Requirements, in order: (1) the cohort's best case must reach
    `min_episodes_is` historical episodes, otherwise the whole cohort is culled
    for insufficient trades; (2) a candidate needs net_pnl > 0 AND sharpe > 0;
    (3) ranking is Sharpe desc, net_pnl desc, then the registered-index lexical
    key of the joint parameter cell.  Returns (row_or_None, reason).
    """
    require_historical(hist_rows, "select_cohort_winner")
    min_ep = spec["gates"]["min_episodes_is"]
    if not hist_rows or max(r["episodes"] for r in hist_rows) < min_ep:
        return None, "insufficient_trades"
    ok = [r for r in hist_rows
          if r["net_pnl"] > 0.0 and r["sharpe"] > 0.0 and r["episodes"] >= min_ep]
    if not ok:
        return None, "no_qualifying_candidate"
    axes = axis_values(spec)
    ok.sort(key=lambda r: (-r["sharpe"], -r["net_pnl"], tie_break_key(r, axes)))
    return ok[0], "selected"


def same_cell(rows, key):
    """The one row of another phase grid carrying the winner's exact joint cell."""
    hits = [r for r in rows if cell_key(r) == key]
    if len(hits) != 1:
        raise ValueError("expected exactly one row for cell %s, found %d" % (str(key), len(hits)))
    return hits[0]


def cohort_neighbourhood(hist_rows, winner, spec):
    """Face-adjacent (+-1 step on exactly one registered axis) sign agreement.

    Historical window only, by construction (`require_historical`) and by the
    caller passing the historical grid.
    """
    require_historical(hist_rows, "cohort_neighbourhood")
    axes = axis_values(spec)
    idx = {cell_key(r): r for r in hist_rows}
    wkey = cell_key(winner)
    wsign = winner["net_pnl"] > 0.0
    neighbours, missing = [], []
    for ai, axis in enumerate(AXES):
        vals = axes[axis]
        pos = vals.index(wkey[ai])
        for step in (-1, 1):
            npos = pos + step
            if not (0 <= npos < len(vals)):
                continue
            nkey = list(wkey)
            nkey[ai] = vals[npos]
            row = idx.get(tuple(nkey))
            if row is None:
                missing.append(tuple(nkey))
            else:
                neighbours.append(row)
    if missing:
        raise ValueError("registered joint grid is not the full product: missing neighbours %s"
                         % str(missing[:3]))
    agree = sum(1 for r in neighbours if (r["net_pnl"] > 0.0) == wsign)
    frac = (agree / float(len(neighbours))) if neighbours else 0.0
    return {"neighbours": len(neighbours), "agreeing": agree,
            "same_sign_fraction": round(frac, 6),
            "winner_net_pnl_positive": wsign,
            "threshold": spec["gates"]["neighborhood_min_same_sign_fraction"],
            "passed": frac >= spec["gates"]["neighborhood_min_same_sign_fraction"]}


def _pick(row, keys):
    return {k: row[k] for k in keys if k in row}


def evaluate_cohort(spec, cohort_label, rows):
    """The whole v1.3 cohort decision for one (symbol, timeframe) cohort.

    `rows` maps every registered phase grid to that cohort's rows only.
    """
    hist = rows["historical"]
    winner, reason = select_cohort_winner(hist, spec)
    out = {"cohort": cohort_label, "outcome": "CULLED", "no_winner_reason": None,
           "cull_reasons": [], "winner": None, "neighbourhood": None, "metrics": {}}
    if winner is None:
        out["no_winner_reason"] = reason
        out["cull_reasons"].append(reason)
        return out
    key = cell_key(winner)
    nb = cohort_neighbourhood(hist, winner, spec)
    oos = same_cell(rows["oos"], key)
    full = same_cell(rows["full"], key)
    stress = {s: same_cell(rows[s], key) for s, _ in STRESS}
    out["winner"] = _pick(winner, AXES)
    out["metrics"] = {
        "historical": _pick(winner, WINNER_METRIC_KEYS),
        "oos": _pick(oos, WINNER_METRIC_KEYS),
        "full": _pick(full, WINNER_METRIC_KEYS),
        "robustness": {s: _pick(stress[s], WINNER_METRIC_KEYS) for s in stress},
        "no_funding_reference": _pick(same_cell(rows["no_funding"], key), WINNER_METRIC_KEYS),
    }
    out["neighbourhood"] = nb
    reasons = []
    if not (oos["net_pnl"] > 0.0 and oos["sharpe"] > 0.0):
        reasons.append("oos_economic")
    if not (full["net_pnl"] > 0.0):
        reasons.append("full_economic")
    failing_stress = [s for s, _ in STRESS if not (stress[s]["net_pnl"] > 0.0)]
    if failing_stress:
        reasons.append("robustness_economic:" + ",".join(failing_stress))
    if not nb["passed"]:
        reasons.append("parameter_neighbourhood")
    out["cull_reasons"] = reasons
    out["outcome"] = "CULLED" if reasons else "SURVIVOR"
    return out


def family_disposition(survivors, coverage_complete):
    """Contract 7.2/7.3 family disposition.  Coverage/technical incompleteness wins.

    0 survivor -> REJECT / NO_SURVIVOR; exactly 1 -> SURVIVOR_FOUND (PASS);
    more than 1 -> MULTIPLE_SURVIVORS (FINALIST: the research threshold is met,
    but picking among survivors is a downstream/operator decision, so
    performance_claimable stays false).
    """
    if not coverage_complete:
        return {"disposition": "TECHNICAL_INCOMPLETE", "verdict_recommendation": "TECHNICAL_INCOMPLETE",
                "performance_claimable_recommendation": False}
    if not survivors:
        return {"disposition": "REJECT / NO_SURVIVOR", "verdict_recommendation": "REJECT",
                "performance_claimable_recommendation": False}
    if len(survivors) == 1:
        return {"disposition": "SURVIVOR_FOUND", "verdict_recommendation": "PASS",
                "performance_claimable_recommendation": True}
    return {"disposition": "MULTIPLE_SURVIVORS", "verdict_recommendation": "FINALIST",
            "performance_claimable_recommendation": False}


def summarize(spec, grid_rows, layers):
    import random
    import statistics as st
    hist, full = grid_rows["historical"], grid_rows["full"]
    need = {k: spec["expected"]["case_evaluations_per_grid"] for k in COHORT_GRID_KINDS}
    coverage = {k: len(grid_rows.get(k, [])) for k in need}
    cohorts = sorted({(r["symbol"], r["timeframe"]) for r in full})
    cohort_labels = ["%s/%s" % c for c in cohorts]

    per_cohort = {}
    for label in cohort_labels:
        per_cohort[label] = {k: [r for r in grid_rows[k] if "%s/%s" % (r["symbol"], r["timeframe"]) == label]
                             for k in COHORT_GRID_KINDS}

    expected_axes = axis_values(spec)
    dca_axes = ("spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct")
    strategy_product = set((w, d) for w in expected_axes["window"] for d in expected_axes["discount"])
    dca_product = set((a, b, c, d) for a in expected_axes["spacing_pct"]
                      for b in expected_axes["size_multiplier"]
                      for c in expected_axes["breakeven_tp_pct"]
                      for d in expected_axes["invalidation_pct"])
    strategy_cells, dca_cells = set(), set()
    for r in full:
        strategy_cells.add((r["window"], r["discount"]))
        dca_cells.add(tuple(r[a] for a in dca_axes))
    cells_per_cohort_ok = all(len(per_cohort[c][k]) == spec["expected"]["base_combinations_per_cohort"]
                              for c in cohort_labels for k in COHORT_GRID_KINDS) if cohort_labels else False
    coverage_complete = (all(coverage[k] == need[k] for k in need)
                         and len(cohorts) == spec["expected"]["cohorts"]
                         and strategy_cells == strategy_product
                         and dca_cells == dca_product
                         and cells_per_cohort_ok)

    # An incomplete measurement is never judged: if any registered grid or cohort cell is
    # missing, no cohort is evaluated at all and the family lands on TECHNICAL_INCOMPLETE
    # (contract 7.2/7.3 - the same fail-closed shape as the v1 coverage gate).
    cohort_results = ([evaluate_cohort(spec, label, per_cohort[label]) for label in cohort_labels]
                      if coverage_complete else [])
    survivors = [c for c in cohort_results if c["outcome"] == "SURVIVOR"]
    disposition = family_disposition(survivors, coverage_complete)

    # deterministic selector: a shuffled copy of the same rows must elect the same cell
    rng = random.Random(20260913)
    selector_stable = True
    for c in cohort_results:
        if c["no_winner_reason"] is not None:
            continue
        shuffled = list(per_cohort[c["cohort"]]["historical"])
        rng.shuffle(shuffled)
        again, _ = select_cohort_winner(shuffled, spec)
        if again is None or cell_key(again) != tuple(c["winner"][a] for a in AXES):
            selector_stable = False
            break

    med = lambda rows, key: st.median([r[key] for r in rows if r.get(key) is not None] or [0.0])
    # Descriptive diagnostics only: cross-cohort medians are NOT a gate in v1.3.0.
    descriptive = {
        "non_gating": True,
        "note": "cross-cohort medians are descriptive diagnostics only; they are never a gate "
                "(contract 7.2, v1.3.0)",
        "median_historical_net_pnl": med(hist, "net_pnl"),
        "median_historical_sharpe": med(hist, "sharpe"),
        "median_full_net_pnl": med(full, "net_pnl"),
        "median_full_sharpe": med(full, "sharpe"),
        "median_no_funding_historical_net_pnl": med(grid_rows["no_funding"], "net_pnl"),
        "positive_case_share_historical": sum(1 for r in hist if r["net_pnl"] > 0) / float(len(hist)),
    }
    stress_summary = {s: {"median_net_pnl": med(grid_rows[s], "net_pnl"),
                          "median_sharpe": med(grid_rows[s], "sharpe"),
                          "cases": len(grid_rows[s]),
                          "non_gating": True} for s, _ in STRESS}

    keys = ("net_pnl", "fees", "funding", "gross_pnl", "ending_equity", "sharpe",
            "max_dd_pct", "max_dd_usdt", "max_effective_leverage", "capital_utilization",
            "cagr", "total_return_pct", "episodes")
    assertions = {
        "episodes_partition": all(r["episodes"] == r["tp_hits"] + r["stop_hits"]
                                  + r["open_at_end"] + r["margin_calls"] for r in full),
        "pnl_decomposition": all(abs(r["gross_pnl"] - r["fees"] - r["funding"]
                                     - r["net_pnl"]) <= 1e-3 for r in full),
        "coverage_complete": coverage_complete,
        "cohort_count_20": len(cohorts) == spec["expected"]["cohorts"],
        "strategy_grid_is_registered_product": strategy_cells == strategy_product,
        "dca_grid_is_registered_product": dca_cells == dca_product,
        "base_combinations_per_cohort_per_grid": cells_per_cohort_ok,
        "expected_case_evaluations": sum(coverage.values()) == spec["expected"]["expected_case_evaluations"],
        "layer0_equals_episodes": layers[0] == sum(r["episodes"] for r in full) and layers[0] > 0,
        "layer_histogram_nonempty": sum(layers) > 0,
        "no_entry_after_exhaustion": all(r["min_entry_equity"] > 0.0 for r in full),
        "ending_equity_floor": all(r["ending_equity"] > -1.5 * 30000.0 for r in full),
        "selector_deterministic": selector_stable,
        "selector_historical_only": True,
    }
    return {
        "family_id": spec["family_id"], "round_id": spec["round_id"], "run_id": spec["run_id"],
        "selector_version": SELECTOR_VERSION, "disposition_version": DISPOSITION_VERSION,
        "registered_domains": {
            "strategy": {"window": expected_axes["window"], "discount": expected_axes["discount"]},
            "dca": {a: expected_axes[a] for a in dca_axes},
            "dca_base_quote": spec["dca_domain"]["base_quote"]},
        "coverage": coverage, "coverage_required": need, "coverage_complete": coverage_complete,
        "cohorts": cohort_labels, "cohort_count": len(cohorts),
        "case_evaluations_per_cohort_per_grid": spec["expected"]["base_combinations_per_cohort"],
        "case_evaluations_per_grid": spec["expected"]["case_evaluations_per_grid"],
        "case_evaluations_total": sum(coverage.values()),
        "expected_case_evaluations": spec["expected"]["expected_case_evaluations"],
        "cohort_results": cohort_results,
        "cohort_survivors": [c["cohort"] for c in survivors],
        "cohort_survivor_count": len(survivors),
        "cohort_outcome_counts": {"SURVIVOR": len(survivors),
                                  "CULLED": len(cohort_results) - len(survivors)},
        "disposition": disposition["disposition"],
        "verdict_recommendation": disposition["verdict_recommendation"],
        "performance_claimable_recommendation": disposition["performance_claimable_recommendation"],
        "survivor_evidence": survivors,
        "descriptive_diagnostics": descriptive,
        "descriptive_medians_all_base_cases": {k: med(full, k) for k in keys},
        "stress_summary": stress_summary,
        "dca_layer_histogram": {"level_%02d" % k: layers[k] for k in range(12)},
        "assertions": assertions,
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) != 2:
        sys.stderr.write("usage: 20_strategy_a_run.py <run-spec.json>\n")
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
        for key in ("dca_domain", "expected", "selector_version", "disposition_version"):
            if key not in spec:
                raise SystemExit("run-spec is not a v1.3.0 spec: missing %r (contract 7.2/7.3)" % key)
        if spec["selector_version"] != SELECTOR_VERSION or spec["disposition_version"] != DISPOSITION_VERSION:
            raise SystemExit("run-spec selector/disposition version %r/%r != engine %r/%r"
                             % (spec["selector_version"], spec["disposition_version"],
                                SELECTOR_VERSION, DISPOSITION_VERSION))
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

        grid_rows = {}
        total = len(spec["data"]["symbols"]) * len(spec["data"]["timeframes"])
        done = 0
        for symbol in spec["data"]["symbols"]:
            for tf in spec["data"]["timeframes"]:
                cohort = Cohort(symbol, tf, spec["data"]["start"], spec["data"]["end"])
                meta = instruments[symbol]
                cohort.price_increment = meta["price_increment"]
                cohort.taker_fee = meta["taker_fee"]
                cohort.leverage = 1.0 / meta["margin_init"]
                cohort.margin_maint = meta["margin_maint"]
                ft, fr, excluded = load_funding(symbol, spec["data"]["start"], spec["data"]["end"])
                cohort.funding, applied = funding_per_bar(cohort, ft, fr, 1.0)
                run_log("cohort %s/%s bars=%d days=%d funding_official=%d modeled_excluded=%d"
                        % (symbol, tf["raw_interval"], cohort.n, cohort.n_days, applied, excluded))
                for k, v in run_cohort(spec, cohort, run_log).items():
                    grid_rows.setdefault(k, []).extend(v)
                done += 1
                atomic_write_json(os.path.join(attempt_dir, "artifacts", "progress.json"),
                                  {"cohorts_done": done, "cohorts_total": total,
                                   "updated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})

        for kind, rows in grid_rows.items():
            path = os.path.join(attempt_dir, "artifacts", "grid_%s.csv" % kind)
            with open(path, "w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
                w.writeheader()
                w.writerows(rows)
            run_log("wrote %s (%d rows)" % (os.path.basename(path), len(rows)))

        layers = LAYER_TOTALS.setdefault("full", [0] * 12)
        summary = summarize(spec, grid_rows, layers)
        summary["runtime_seconds"] = int(time.time() - started)
        summary["bins_build"] = {k: bins[k] for k in
                                 ("build_wall_seconds", "qlib_dir_bytes", "qlib_version")}
        summary["instrument_metadata"] = {s: instruments[s] for s in spec["data"]["symbols"]}
        summary["data_readback"] = bins["readback"]
        summary["generated_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        atomic_write_json(os.path.join(attempt_dir, "result.json"), summary)
        atomic_write_json(os.path.join(attempt_dir, "artifacts", "dca_layer_histogram.json"),
                          summary["dca_layer_histogram"])
        atomic_write_json(os.path.join(attempt_dir, "artifacts", "assertions.json"),
                          summary["assertions"])
        atomic_write_json(os.path.join(attempt_dir, "artifacts", "cohort_results.json"),
                          summary["cohort_results"])
        atomic_write_json(os.path.join(attempt_dir, "artifacts", "cohort_survivors.json"),
                          summary["survivor_evidence"])
        run_log("disposition=%s verdict_recommendation=%s survivors=%d/%d cases=%d"
                % (summary["disposition"], summary["verdict_recommendation"],
                   summary["cohort_survivor_count"], summary["cohort_count"],
                   summary["case_evaluations_total"]))
        atomic_write_json(os.path.join(attempt_dir, "state.json"), {
            "schema_version": 1, "family_id": spec["family_id"], "round_id": spec["round_id"],
            "run_id": spec["run_id"], "stage": "ARTIFACT_READY",
            "updated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "note": "all phases complete"})
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


if __name__ == "__main__":
    sys.exit(main())
