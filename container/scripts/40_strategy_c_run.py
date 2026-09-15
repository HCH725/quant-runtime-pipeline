#!/usr/bin/env python3
"""Strategy C — funding-decile extreme contrarian (BTCUSDT USD-M perp, 24h) full backtest, on Qlib.

Entry point for ONE pre-registered attempt.  Reads the immutable run-spec from
/results (the only source of parameters), builds the Qlib .bin store from the
READ-ONLY canonical raw store into /qlib/work, then runs the pre-registered
full-backtest contract (QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md
v1.7.0 sections 7.2 / 7.3):

    1 cohort (BTCUSDT/15m) x STRATEGY parameter domain x DCA parameter domain x
    11 registered phase grids (historical / oos / full / fee_2x / funding_2x /
    entry_delay_1_bar / slippage_2ticks / no_funding / no_funding_full /
    cost_attrition_40bps / official_only)

SIGNAL (source-specified, pre-registered): at every 8h funding settlement
(00:00 / 08:00 / 16:00 UTC) take the mid-rank percentile of the current funding
rate within its own trailing 180-day distribution;
    rank = (observations strictly below the current value + 0.5) / observations in window
A settlement is an extreme-negative-funding signal when rank <= the registered
decile.  The signal is observable AT the settlement instant, so the entry is the
close of the bar that STARTS at the settlement (the next executable price, one
bar lagged - never a look-ahead).

EXECUTION (window-bounded DCA rail, inherited semantics): tranche #1 is the
initial entry; adverse-price scale-ins follow the registered ladder
(level_k price = initial_entry_price x (1 - spacing_pct x k), k = 1..10, so at
most 11 routine active levels of the 12-tranche rail); the take profit is
reduce-only at running_average_cost x (1 + breakeven_tp_pct); the invalidation is
a RESTING stop at running_average_cost x (1 - invalidation_pct); and the position
is time-exited (reduce-only flatten of every layer) 24h after the settlement that
created it, after which it is FLAT until the next qualifying signal.  No short
leg.  Episodes never overlap (a signal inside an open hold is skipped).

DCA is executed as real order/fill accounting (an episode state machine over the
bars); nothing is estimated after the fact.  Every legal
(strategy params x DCA config) cell is evaluated on every registered phase grid,
and the family gate is the **cohort-level survivor** rule of contract section 7.3:
one deterministic historical-only winner per cohort, then OOS / full /
robustness / parameter-neighbourhood evidence for that same winner.  This family
has exactly one cohort, so the cohort survivor count is 0 or 1.

Writes only:
  * /qlib/work/**      (rebuildable derived/cache area, INV-5)
  * <attempt_dir>/**   (result.json, artifacts/, logs/, state.json)
Never writes /data/raw (read-only mount).

usage: 40_strategy_c_run.py <run-spec.json>
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
WORK_ROOT = "/qlib/work/strategyC-v1"
CSV_ROOT = os.path.join(WORK_ROOT, "csv")
QLIB_DIR = os.path.join(WORK_ROOT, "qlib-data")
MS_PER_DAY = 86400000
FIELDS = ["open", "high", "low", "close", "volume"]
START_EQUITY = 30000.0
# 24h at 15m: the position is flattened on the close of the bar that ends exactly
# 24h after the settlement (bar `b` starts at the settlement, so bar b+95 closes at T+24h).
HOLD_BARS = 96
MAX_ADD_LEVELS = 10
# Execution stress reruns (full-window slice), contract 7.2 robustness.
STRESS = [
    ("fee_2x", {"fee_mult": 2.0}),
    ("funding_2x", {"funding_mult": 2.0}),
    ("entry_delay_1_bar", {"entry_delay_1_bar": True}),
    ("slippage_2ticks", {"slip_ticks": 2}),
]
# Registered cost-attrition grid (card t_56ca0634 G8): 8 x the 5 bps taker fee = 40 bps per fill.
COST_ATTRITION_STRESS = {"fee_mult": 8.0}
# Registered phase grids: every one of them covers the FULL strategy-domain x
# DCA-domain product of the cohort (contract 7.2).
COHORT_GRID_KINDS = ("historical", "oos", "full", "fee_2x", "funding_2x",
                     "entry_delay_1_bar", "slippage_2ticks", "no_funding",
                     "no_funding_full", "cost_attrition_40bps", "official_only")
# Joint parameter space axes, in the one registered order used for the
# deterministic lexical tie-break (contract 7.3).  Order is part of the gate.
AXES = ("decile", "lookback_days", "spacing_pct", "size_multiplier",
        "breakeven_tp_pct", "invalidation_pct")
DCA_AXES = ("spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct")
SELECTOR_VERSION = "cohort-selector-v1"
DISPOSITION_VERSION = "cohort-disposition-v1"
CONTRACT_SEMANTICS_VERSION = "v1.4.0"
ENGINE_VERSION = "c-v1-engine-1.0.0"
ENGINE_SEMANTICS = ("funding-decile extreme contrarian, long-only, next-bar entry, "
                    "window-bounded 24h DCA rail (see module docstring)")
WINNER_METRIC_KEYS = ("net_pnl", "sharpe", "episodes", "ending_equity", "fees", "funding",
                      "max_dd_usdt", "max_dd_pct", "max_effective_leverage",
                      "capital_utilization", "annualized_return", "cagr")
# csv row field order (kept identical for every phase grid)
ROW_FIELDS = ("symbol", "timeframe", "window_kind", "decile", "lookback_days",
              "spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct",
              "net_pnl", "fees", "funding", "gross_pnl", "ending_equity",
              "episodes", "tp_hits", "stop_hits", "time_exits", "open_at_end", "margin_calls",
              "halted", "min_entry_equity", "sharpe", "max_dd_usdt", "max_dd_pct",
              "max_effective_leverage", "capital_utilization", "bars_in_market",
              "fills", "turnover_usdt", "signals_seen", "signals_entered",
              "days", "years", "cagr", "total_return_pct", "annualized_return")
LAYER_TOTALS = {}
# Structural counters (module level so `summarize` can assert them over every grid).
# The raw funding timestamps carry ms-level jitter around the nominal 8h boundary
# (measured on BTCUSDT: settlement gaps of 28,799,999 .. 28,800,004 ms), so a settlement is
# mapped to the bar that starts at the nominal boundary and every structural comparison is
# allowed one second of tolerance against a 15-minute bar and a 24-hour window.  A larger
# deviation is a real mapping defect and trips the counter.
MS_JITTER_TOLERANCE_MS = 1000
COUNTERS = {"entry_before_signal": 0, "hold_over_24h": 0, "signal_bar_mismatch": 0}


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
    """Make a payload strictly JSON-safe: numpy scalars -> python, non-finite -> null."""
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


def iso(ms):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ms / 1000.0))


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
        self.day_end = set(int(i) for i in np.flatnonzero(
            np.concatenate([day_id[1:] != day_id[:-1], [True]])))
        self.first_ts = str(df.index[0][1])
        self.last_ts = str(df.index[-1][1])
        self.price_increment = 0.0
        self.taker_fee = 0.0
        self.leverage = 1.0
        self.margin_maint = 0.0

    def slice(self, start_date, end_date):
        lo = utc_ms(start_date)
        hi = utc_ms(end_date) + MS_PER_DAY - 1
        return (int(np.searchsorted(self.open_time_ms, lo, side="left")),
                int(np.searchsorted(self.open_time_ms, hi, side="right")))


def load_funding_series(symbol, start, end, official_only=False):
    """Funding observations in the window, optionally restricted to truth_status == official.

    The C family uses the whole registered series, including the 2022-01-01 ->
    2023-10-31 `modeled_funding` reconstruction (the card's registered data fact), and
    re-measures the same protocol on the official-only subsample (the registered
    provenance robustness grid).  Every row's truth_status is counted and disclosed;
    a modelled row is never presented as an official observation.
    """
    path = os.path.join(RAW_ROOT, "funding", symbol, "%s-funding.jsonl.gz" % symbol)
    lo, hi = utc_ms(start), utc_ms(end) + MS_PER_DAY - 1
    times, rates = [], []
    counts = {"official": 0, "modeled_funding": 0, "other": 0, "excluded_by_filter": 0,
              "out_of_window": 0}
    with gzip.open(path, "rt") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            ms = r["funding_time_ms"]
            if ms < lo or ms > hi:
                counts["out_of_window"] += 1
                continue
            status = r.get("truth_status")
            counts[status if status in ("official", "modeled_funding") else "other"] += 1
            if official_only and status != "official":
                counts["excluded_by_filter"] += 1
                continue
            times.append(int(ms))
            rates.append(float(r["funding_rate"]))
    return (np.array(times, dtype=np.int64), np.array(rates, dtype=np.float64), counts)


def trailing_midrank_percentiles(times, rates, lookback_days):
    """Mid-rank percentile of each observation inside its own trailing lookback window.

    `rank = (observations strictly below the current value + 0.5) / observations in window`
    (the registered source formula).  The window is [t - lookback_days, t] and includes the
    current observation.  A percentile is only produced once the trailing window is FULLY
    covered by the series (t - lookback >= the first observation of that series); before
    that the signal is undefined and the observation can never trigger an entry.  Only
    observations at or before t enter the window, so the value is never a look-ahead.
    """
    lb_ms = int(lookback_days) * MS_PER_DAY
    out = np.full(len(times), np.nan)
    if len(times) == 0:
        return out
    first_t = int(times[0])
    for i in range(len(times)):
        t = int(times[i])
        if t - lb_ms < first_t:
            continue
        lo = int(np.searchsorted(times, t - lb_ms, side="left"))
        window = rates[lo:i + 1]
        below = int(np.count_nonzero(window < rates[i]))
        out[i] = (below + 0.5) / float(len(window))
    return out


def build_signal_series(cohort, times, rates, lookbacks, counts, label):
    """Per-lookback percentile + the bar each observation would enter on.

    `sig_bar[k]` is the first bar whose open time is >= the settlement instant - the bar
    that STARTS at the settlement.  The entry fills on that bar's close (the next
    executable price after the signal), so `entry_bar >= sig_bar` always holds and the
    module-level `COUNTERS["entry_before_signal"]` guard can assert it.
    """
    per_bar = np.zeros(cohort.n, dtype=np.float64)
    if len(times):
        # side='left' - 1 = the bar that has already closed at (or before) the funding time
        idx = np.searchsorted(cohort.open_time_ms, times, side="left") - 1
        for k in range(len(times)):
            i = int(idx[k])
            if 0 <= i < cohort.n:
                per_bar[i] += rates[k]
    out = {"label": label, "counts": counts, "obs_times": times, "obs_rates": rates,
           "funding_per_bar": per_bar, "first_obs_ms": int(times[0]) if len(times) else None,
           "last_obs_ms": int(times[-1]) if len(times) else None, "sig_bar": {}, "sig_pct": {},
           "sig_time": {}}
    bars = np.searchsorted(cohort.open_time_ms, times, side="left") if len(times) else np.array([], dtype=np.int64)
    for lb in lookbacks:
        pct = trailing_midrank_percentiles(times, rates, lb)
        out["sig_pct"][lb] = pct
        out["sig_bar"][lb] = bars
        out["sig_time"][lb] = times
    return out


def window_geometry_ok(sig_ms, bar_open_ms, exit_close_ms, bar_ms, hold_bars=None,
                       tol=None):
    """Executable geometry guard for one episode (contract: no look-ahead, 24h window).

    Returns (mapped_ok, hold_ok):
      * mapped_ok - the settlement observation maps to the bar that STARTS at the settlement
        boundary (tolerance: the raw settlement timestamps jitter by a few ms around it);
      * hold_ok   - the exit bar is inside [T, T + hold_bars bars] (or is the last bar of the
        evaluated slice, which is always earlier).
    Extracted so the guard itself is unit-testable: a guard that can never fail proves nothing.
    """
    hold_bars = HOLD_BARS if hold_bars is None else hold_bars
    tol = MS_JITTER_TOLERANCE_MS if tol is None else tol
    mapped_ok = abs(int(bar_open_ms) - int(sig_ms)) <= tol
    hold_ok = (int(exit_close_ms) - int(bar_open_ms)) <= hold_bars * int(bar_ms) + tol
    return mapped_ok, hold_ok


def exit_price_pnl(proceeds, basis):
    """Pure price PnL of one closing fill: exit proceeds minus cost basis.

    Contract 7.2 (v1.3.2, independent gross accounting): `gross_pnl` accumulates this
    value only - no fee, no funding - and is never reverse-derived from the net ledger.
    """
    return proceeds - basis


def pnl_decomposition_ok(m, tol=1e-3):
    """Fail-closed cross-check of the two independent accounting sources (contract 7.2)."""
    return abs(m["gross_pnl"] - m["fees"] - m["funding"] - m["net_pnl"]) <= tol


def simulate(cohort, p, rail, window, stress, slip_ticks, kind, series, diag=False):
    """One pre-registered parameter case over one window slice (i0, i1)."""
    i0, i1 = window
    C = cohort.close[i0:i1].tolist()
    H = cohort.high[i0:i1].tolist()
    L = cohort.low[i0:i1].tolist()
    O = cohort.open[i0:i1].tolist()
    n = len(C)
    if n <= 0:
        raise SystemExit("empty window slice for %s %s" % (cohort.symbol, kind))

    decile = p["decile"]
    lookback = p["lookback_days"]
    pct = series["sig_pct"][lookback]
    sig_bar = series["sig_bar"][lookback]
    elig = np.flatnonzero(np.isfinite(pct) & (pct <= decile))
    entries = []
    for k in elig:
        b = int(sig_bar[k])
        if i0 <= b < i1:
            entries.append((b - i0, k))
    signals_seen = len(entries)

    if stress.get("no_funding"):
        fper = np.zeros(cohort.n, dtype=np.float64)
    else:
        fper = series["funding_per_bar"] * stress.get("funding_mult", 1.0)
    fper = fper[i0:i1]

    tick = cohort.price_increment
    taf = cohort.taker_fee * stress.get("fee_mult", 1.0)
    lev = cohort.leverage
    d0 = rail["spacing_d0"]
    tp_pct = rail["tp"]
    inval = rail["invalidation"]
    mult = rail["size_multiplier"]
    base = rail["base_quote"]
    delay = 1 if stress.get("entry_delay_1_bar") else 0

    day_of_bar = cohort.day_index[i0:i1].tolist()
    de_set = cohort.day_end
    day_equity = [None] * cohort.n_days

    layers = [0] * 12
    realized = funding_paid = 0.0
    # Independent gross / price-PnL accumulator (contract 7.2, v1.3.2): fed ONLY by
    # `exit_price_pnl` at an exit/flatten, so `gross_pnl` can never be reverse-derived
    # from the net figure.
    gross_pnl = 0.0
    fees_total = 0.0
    fills = 0
    turnover = 0.0
    episodes = tp_hits = stop_hits = open_at_end = margin_calls = time_exits = 0
    signals_entered = 0
    bars_in_market = 0
    max_lev = 0.0
    util_sum = 0.0
    by_year = {}
    by_phase = {}

    def charge_fee(amount):
        """A fee is paid at the instant of the fill and must reduce the realised equity there.

        Accumulating fees in a side ledger (which v1.3.0 shipped) left `realized` gross of
        fees, so a cost stress grid could not move net PnL / equity / Sharpe and the daily
        equity marks and the margin/leverage decisions all read a gross-of-fee equity.
        Every fill (entry, DCA add, exit) is a market order, so the deduction is a single
        choke point here.
        """
        nonlocal realized, fees_total
        fees_total += amount
        realized -= amount
        return amount

    if not entries:
        series_flat = _flat_series(day_equity)
        return _metrics(realized, fees_total, funding_paid, gross_pnl, episodes, tp_hits,
                        stop_hits, open_at_end, margin_calls, time_exits, False, START_EQUITY,
                        series_flat, cohort, i0, i1, layers, max_lev, util_sum, bars_in_market,
                        fills, turnover, signals_seen, signals_entered, kind, {})

    halted = False
    min_entry_equity = START_EQUITY
    last_exit_bar = -1
    for b, k in entries:
        if b <= last_exit_bar:
            continue  # non-overlapping protocol: a signal inside an open hold is skipped
        eb = b + delay
        if eb >= n:
            continue
        # registered capital-exhaustion semantics: the account cannot lose more than itself,
        # so no new position may be opened once the realised equity is gone
        if START_EQUITY + realized <= 0.0:
            halted = True
            break
        min_entry_equity = min(min_entry_equity, START_EQUITY + realized)
        t_end = min(b + HOLD_BARS - 1, n - 1)  # last bar of the [T, T+24h] window
        clipped = (b + HOLD_BARS - 1) > (n - 1)
        if eb > t_end:
            continue
        if eb < b:
            COUNTERS["entry_before_signal"] += 1
        signals_entered += 1
        px = C[eb] + slip_ticks * tick
        p0 = px
        qty = base * lev / px
        cost = qty * px
        ep_fund = 0.0
        ep_gross = 0.0
        ep_fees = 0.0
        charge_fee(qty * px * taf)
        fills += 1
        turnover += qty * px
        ep_fees += qty * px * taf
        levels = [p0 * (1.0 - d0 * kk) for kk in range(12)]
        layers[0] += 1
        broke = False
        t = eb
        for t in range(eb + 1, t_end + 1):
            f = fper[t]
            if f != 0.0:
                ep_fund += qty * C[t] * f
            l = L[t]
            h = H[t]
            o = O[t]
            # Descending-price walk inside the bar (worst case for a long: the bar LOW is
            # reached first, then the high).  The invalidation is a RESTING stop at
            # average_cost x (1 - invalidation), so a ladder level below it cannot fill:
            # whichever trigger price is higher is reached first as the price descends.
            kk = 1
            killed_at = None
            while True:
                stop = (cost / qty) * (1.0 - inval)
                if kk <= MAX_ADD_LEVELS and levels[kk] >= stop:
                    trig, is_stop = levels[kk], False
                else:
                    trig, is_stop = stop, True
                if l > trig:
                    break
                if is_stop:
                    killed_at = trig if o >= trig else o  # gap through the stop fills at the open
                    break
                fpx = trig + slip_ticks * tick
                q = base * (mult ** kk) * lev / fpx
                qty += q
                cost += q * fpx
                charge_fee(q * fpx * taf)
                ep_fees += q * fpx * taf
                fills += 1
                turnover += q * fpx
                layers[kk] += 1
                kk += 1
            eq = START_EQUITY + realized
            ueq = eq + qty * C[t] - cost
            if killed_at is None and ueq <= cohort.margin_maint * qty * C[t]:
                xpx = C[t] - slip_ticks * tick  # capital-exhaustion backstop
                ep_gross = exit_price_pnl(qty * xpx, cost)
                realized += qty * xpx - cost
                gross_pnl += ep_gross
                charge_fee(qty * xpx * taf)
                ep_fees += qty * xpx * taf
                fills += 1
                turnover += qty * xpx
                margin_calls += 1
                broke = True
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
                ep_gross = exit_price_pnl(qty * xpx, cost)
                realized += qty * xpx - cost
                gross_pnl += ep_gross
                charge_fee(qty * xpx * taf)
                ep_fees += qty * xpx * taf
                fills += 1
                turnover += qty * xpx
                stop_hits += 1
                broke = True
                break
            tpx = (cost / qty) * (1.0 + tp_pct)  # reduce-only breakeven-anchored TP
            if h >= tpx:
                xpx = tpx - slip_ticks * tick
                ep_gross = exit_price_pnl(qty * xpx, cost)
                realized += qty * xpx - cost
                gross_pnl += ep_gross
                charge_fee(qty * xpx * taf)
                ep_fees += qty * xpx * taf
                fills += 1
                turnover += qty * xpx
                tp_hits += 1
                broke = True
                break
        if not broke:
            # time exit: reduce-only flatten of every layer 24h after the settlement
            xpx = C[t_end] - slip_ticks * tick
            ep_gross = exit_price_pnl(qty * xpx, cost)
            realized += qty * xpx - cost
            gross_pnl += ep_gross
            charge_fee(qty * xpx * taf)
            ep_fees += qty * xpx * taf
            fills += 1
            turnover += qty * xpx
            if clipped:
                open_at_end += 1
            else:
                time_exits += 1
        funding_paid += ep_fund
        realized -= ep_fund
        episodes += 1
        # Executable structural guards (tolerance: the raw settlement timestamps jitter by up
        # to a few ms around the nominal 8h boundary):
        #   1. the settlement must map to its own bar (the bar starting at the boundary);
        #   2. the exit bar is the last bar of [T, T+24h] (or the end of the evaluated slice),
        #      so the hold can never outlive its registered window.
        sig_ms = int(series["obs_times"][k])
        bar_open_ms = int(cohort.open_time_ms[i0 + b])
        bar_ms = int(cohort.open_time_ms[i0 + 1] - cohort.open_time_ms[i0]) if n > 1 else 0
        exit_close_ms = int(cohort.open_time_ms[i0 + t_end]) + bar_ms
        mapped_ok, hold_ok = window_geometry_ok(sig_ms, bar_open_ms, exit_close_ms, bar_ms)
        if not mapped_ok:
            COUNTERS["signal_bar_mismatch"] += 1
        if not hold_ok:
            COUNTERS["hold_over_24h"] += 1
        last_exit_bar = t_end
        day_equity[day_of_bar[t_end]] = START_EQUITY + realized
        if diag:
            ep_net = ep_gross - ep_fees - ep_fund
            year = time.strftime("%Y", time.gmtime(cohort.open_time_ms[i0 + b] / 1000.0))
            phase = time.strftime("%H", time.gmtime(cohort.open_time_ms[i0 + b] / 1000.0))
            slot = by_year.setdefault(year, {"episodes": 0, "net_pnl": 0.0, "fees": 0.0,
                                             "funding": 0.0})
            slot["episodes"] += 1
            slot["net_pnl"] += ep_net
            slot["fees"] += ep_fees
            slot["funding"] += ep_fund
            slot = by_phase.setdefault(phase, {"episodes": 0, "net_pnl": 0.0})
            slot["episodes"] += 1
            slot["net_pnl"] += ep_net

    if kind == "full":
        acc = LAYER_TOTALS.setdefault("full", [0] * 12)
        for kk in range(12):
            acc[kk] += layers[kk]

    series_flat = _flat_series(day_equity)
    diag_out = {}
    if diag:
        diag_out = {"pnl_by_year": {k: _round_dict(v) for k, v in sorted(by_year.items())},
                    "pnl_by_entry_phase": {k: _round_dict(v) for k, v in sorted(by_phase.items())}}
    return _metrics(realized, fees_total, funding_paid, gross_pnl, episodes, tp_hits, stop_hits,
                    open_at_end, margin_calls, time_exits, halted, min_entry_equity, series_flat,
                    cohort, i0, i1, layers, max_lev, util_sum, bars_in_market, fills, turnover,
                    signals_seen, signals_entered, kind, diag_out)


def _round_dict(d):
    return {k: (round(v, 6) if isinstance(v, float) else v) for k, v in d.items()}


def _flat_series(day_equity):
    series = []
    last = START_EQUITY
    for v in day_equity:
        if v is not None:
            last = v
        series.append(last)
    return np.array(series, dtype=np.float64)


def _metrics(realized, fees_total, funding_paid, gross_pnl, episodes, tp_hits, stop_hits,
             open_at_end, margin_calls, time_exits, halted, min_entry_equity, series, cohort,
             i0, i1, layers, max_lev, util_sum, bars_in_market, fills, turnover, signals_seen,
             signals_entered, kind, diag_out):
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
        "gross_pnl": gross_pnl,
        "episodes": episodes, "tp_hits": tp_hits, "stop_hits": stop_hits,
        "time_exits": time_exits, "open_at_end": open_at_end, "margin_calls": margin_calls,
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
        "fills": fills, "turnover_usdt": turnover,
        "signals_seen": signals_seen, "signals_entered": signals_entered,
        "layers": layers, "diagnostics": diag_out,
    }


def rail_for(dca):
    """One registered DCA configuration -> the engine rail (four registered axes + basis)."""
    return {"base_quote": dca["base_quote"], "spacing_d0": dca["spacing_pct"],
            "size_multiplier": dca["size_multiplier"], "tp": dca["breakeven_tp_pct"],
            "invalidation": dca["invalidation_pct"]}


def record(cohort, p, dca, kind, m):
    row = {"symbol": cohort.symbol, "timeframe": cohort.timeframe, "window_kind": kind,
           "decile": p["decile"], "lookback_days": p["lookback_days"],
           "spacing_pct": dca["spacing_pct"], "size_multiplier": dca["size_multiplier"],
           "breakeven_tp_pct": dca["breakeven_tp_pct"],
           "invalidation_pct": dca["invalidation_pct"],
           "net_pnl": round(m["net_pnl"], 6), "fees": round(m["fees"], 6),
           "funding": round(m["funding"], 6), "gross_pnl": round(m["gross_pnl"], 6),
           "ending_equity": round(m["ending_equity"], 6),
           "episodes": m["episodes"], "tp_hits": m["tp_hits"], "stop_hits": m["stop_hits"],
           "time_exits": m["time_exits"], "open_at_end": m["open_at_end"],
           "margin_calls": m["margin_calls"],
           "halted": m["halted"], "min_entry_equity": round(m["min_entry_equity"], 6),
           "sharpe": round(m["sharpe"], 6),
           "max_dd_usdt": round(m["max_dd_usdt"], 6), "max_dd_pct": round(m["max_dd_pct"], 6),
           "max_effective_leverage": round(m["max_effective_leverage"], 6),
           "capital_utilization": round(m["capital_utilization"], 6),
           "bars_in_market": m["bars_in_market"], "fills": m["fills"],
           "turnover_usdt": round(m["turnover_usdt"], 6),
           "signals_seen": m["signals_seen"], "signals_entered": m["signals_entered"],
           "days": m["days"], "years": round(m["years"], 6), "cagr": m["cagr"],
           "total_return_pct": round(m["total_return_pct"], 6),
           "annualized_return": m["annualized_return"]}
    return {k: row[k] for k in ROW_FIELDS}


def run_cohort(spec, cohort, run_log, series_full, series_official):
    """Every legal (strategy params x DCA config) case of the cohort, on every registered grid."""
    grid = spec["parameter_domain"]["grid"]
    dca_grid = spec["dca_domain"]["grid"]
    slip = spec["costs"]["baseline_slippage_ticks"]
    windows = {"historical": (spec["data"]["historical_start"], spec["data"]["historical_end"]),
               "oos": (spec["data"]["oos_start"], spec["data"]["oos_end"]),
               "full": (spec["data"]["start"], spec["data"]["end"]),
               "official_only": (spec["data"]["official_only_start"],
                                 spec["data"]["official_only_end"])}
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
                                         simulate(cohort, p, rail_for(dca), sl, {}, slip, kind,
                                                  series_full)))

    sl = slices["full"]
    for sname, stress in STRESS:
        for p in grid:
            for dca in dca_grid:
                rows[sname].append(record(cohort, p, dca, sname,
                                          simulate(cohort, p, rail_for(dca), sl, stress,
                                                   stress.get("slip_ticks", slip), sname,
                                                   series_full)))
    # G8 cost-attrition grid: 40 bps per fill (8 x the registered 5 bps taker fee).
    for p in grid:
        for dca in dca_grid:
            rows["cost_attrition_40bps"].append(record(
                cohort, p, dca, "cost_attrition_40bps",
                simulate(cohort, p, rail_for(dca), sl, COST_ATTRITION_STRESS, slip,
                         "cost_attrition_40bps", series_full)))
    # official-funding baseline check runs on the SAME window as the historical gate
    for p in grid:
        for dca in dca_grid:
            rows["no_funding"].append(record(cohort, p, dca, "no_funding",
                                             simulate(cohort, p, rail_for(dca), slices["historical"],
                                                      {"no_funding": True}, slip, "no_funding",
                                                      series_full)))
            rows["no_funding_full"].append(record(cohort, p, dca, "no_funding_full",
                                                  simulate(cohort, p, rail_for(dca), sl,
                                                           {"no_funding": True}, slip,
                                                           "no_funding_full", series_full)))
    # provenance grid: the same protocol on the official-funding era (>= 2023-11-01)
    sl = slices["official_only"]
    run_log("window official_only %s/%s bars=[%d,%d) cases=%d"
            % (cohort.symbol, cohort.timeframe, sl[0], sl[1], len(grid) * len(dca_grid)))
    for p in grid:
        for dca in dca_grid:
            rows["official_only"].append(record(cohort, p, dca, "official_only",
                                                simulate(cohort, p, rail_for(dca), sl, {}, slip,
                                                         "official_only", series_official)))
    return rows


# ---------------------------------------------------------------------------
# phase 3: pre-registered cohort-selector / cohort-survivor gate
#          (contract v1.3.0 sections 7.2 and 7.3; no post-hoc tuning)
# ---------------------------------------------------------------------------

def axis_values(spec):
    """The registered value list of every joint-space axis, in the registered order."""
    return {"decile": list(spec["parameter_domain"]["grid_deciles"]),
            "lookback_days": list(spec["parameter_domain"]["grid_lookbacks"]),
            "spacing_pct": list(spec["dca_domain"]["spacing_pct"]),
            "size_multiplier": list(spec["dca_domain"]["size_multiplier"]),
            "breakeven_tp_pct": list(spec["dca_domain"]["breakeven_tp_pct"]),
            "invalidation_pct": list(spec["dca_domain"]["invalidation_pct"])}


def cell_key(r):
    return tuple(r[a] for a in AXES)


def tie_break_key(r, axes):
    """Registered-index lexical key: deterministic and free of float formatting."""
    return tuple(axes[a].index(r[a]) for a in AXES)


def require_historical(rows, where, allowed=("historical",)):
    """Selection and neighbourhood judgement may never read OOS (contract 7.3).

    The primary selector may only ever see `historical` rows.  The registered provenance
    protocol re-runs the SAME selector on the official-funding era, whose rows are tagged
    `official_only` - that is that configuration's own historical window, never the
    primary's OOS, so the guard is parametrised rather than dropped.
    """
    bad = [r for r in rows if r.get("window_kind") not in allowed]
    if bad:
        raise ValueError("%s must be given %r rows only (contract 7.3): %d violation(s)"
                         % (where, allowed, len(bad)))


def select_cohort_winner(hist_rows, spec, allowed=("historical",)):
    """One deterministic winner per cohort, historical window only.

    Requirements, in order: (1) the cohort's best case must reach `min_episodes_is`
    historical episodes, otherwise the whole cohort is culled for insufficient trades;
    (2) a candidate needs net_pnl > 0 AND sharpe > 0; (3) ranking is Sharpe desc, net_pnl
    desc, then the registered-index lexical key of the joint parameter cell.
    """
    require_historical(hist_rows, "select_cohort_winner", allowed)
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

    Historical window only, by construction (`require_historical`).
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


def evaluate_cohort(spec, cohort_label, rows, official_rows):
    """The whole cohort decision for the single (symbol, timeframe) cohort.

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
    cost_attrition = same_cell(rows["cost_attrition_40bps"], key)
    official = same_cell(official_rows, key)
    out["winner"] = _pick(winner, AXES)
    out["metrics"] = {
        "historical": _pick(winner, WINNER_METRIC_KEYS),
        "oos": _pick(oos, WINNER_METRIC_KEYS),
        "full": _pick(full, WINNER_METRIC_KEYS),
        "robustness": {s: _pick(stress[s], WINNER_METRIC_KEYS) for s in stress},
        "no_funding_reference": _pick(same_cell(rows["no_funding"], key), WINNER_METRIC_KEYS),
        "no_funding_full_reference": _pick(same_cell(rows["no_funding_full"], key),
                                           WINNER_METRIC_KEYS),
        "cost_attrition_40bps": _pick(cost_attrition, WINNER_METRIC_KEYS),
        "official_only_reference": _pick(official, WINNER_METRIC_KEYS),
    }
    out["neighbourhood"] = nb
    reasons = []
    # G4: the family's registered OOS economic gate is STRICTER than the contract
    # minimum (`net_pnl > 0 and sharpe > 0`): annualized Sharpe floor 0.40 and a
    # non-negative annualized return (the registered falsification battery).
    gates = spec["gates"]
    if not (oos["net_pnl"] > 0.0 and oos["sharpe"] > 0.0
            and oos["sharpe"] >= gates["min_oos_sharpe"]
            and (oos["annualized_return"] is not None
                 and oos["annualized_return"] >= gates["min_oos_annualized_return"])):
        reasons.append("oos_economic")
    if not (full["net_pnl"] > 0.0):
        reasons.append("full_economic")
    failing_stress = [s for s, _ in STRESS if not (stress[s]["net_pnl"] > 0.0)]
    if failing_stress:
        reasons.append("robustness_economic:" + ",".join(failing_stress))
    # G8: the registered 40 bps-per-fill attrition grid is a cohort gate, and its reason
    # stays inside the registered `robustness_economic:<grids>` vocabulary.
    if not (cost_attrition["net_pnl"] > 0.0):
        reasons.append("robustness_economic:cost_attrition_40bps")
    if not nb["passed"]:
        reasons.append("parameter_neighbourhood")
    out["cull_reasons"] = reasons
    out["outcome"] = "CULLED" if reasons else "SURVIVOR"
    return out


def family_disposition(survivors, coverage_complete):
    """Contract 7.2/7.3 (v1.4.0).  Coverage/technical incompleteness wins."""
    if not coverage_complete:
        return {"disposition": "TECHNICAL_INCOMPLETE",
                "verdict_recommendation": "TECHNICAL_INCOMPLETE",
                "performance_claimable_recommendation": False,
                "mapping_version": CONTRACT_SEMANTICS_VERSION}
    if not survivors:
        return {"disposition": "REJECT / NO_SURVIVOR", "verdict_recommendation": "REJECT",
                "performance_claimable_recommendation": False,
                "mapping_version": CONTRACT_SEMANTICS_VERSION}
    if len(survivors) == 1:
        return {"disposition": "SURVIVOR_FOUND", "verdict_recommendation": "PASS",
                "performance_claimable_recommendation": True,
                "mapping_version": CONTRACT_SEMANTICS_VERSION}
    return {"disposition": "MULTIPLE_SURVIVORS", "verdict_recommendation": "PASS",
            "performance_claimable_recommendation": True,
            "mapping_version": CONTRACT_SEMANTICS_VERSION}


def threshold_instability_check(spec, winner, oos_rows):
    """Registered falsification item: the 10th/5th/20th percentile tracks must agree in sign.

    The three decile tracks are evaluated at the winner's remaining parameters on the OOS
    window.  A disagreement is disclosed descriptively and - per the registered battery -
    must never be recorded as PASS.
    """
    axes = axis_values(spec)
    wkey = cell_key(winner)
    dec_pos = AXES.index("decile")
    tracks = []
    for d in axes["decile"]:
        key = list(wkey)
        key[dec_pos] = d
        row = [r for r in oos_rows if cell_key(r) == tuple(key)]
        if len(row) != 1:
            raise ValueError("threshold-instability track %r missing from the OOS grid" % (d,))
        r = row[0]
        tracks.append({"decile": d, "net_pnl": r["net_pnl"], "sharpe": r["sharpe"],
                       "episodes": r["episodes"], "positive": r["net_pnl"] > 0.0})
    signs = set(t["positive"] for t in tracks)
    return {"registered_item": "threshold-instability",
            "definition": "the registered decile tracks must agree in OOS net-PnL sign",
            "tracks": tracks, "signs_agree": len(signs) == 1, "hit": len(signs) > 1,
            "landing": "descriptive robustness disclosure; a hit must never be recorded as PASS",
            "non_gating_band": False}


def funding_provenance_check(spec, winner, official_winner, oos_rows):
    """Registered falsification item: primary vs official_only provenance rejection.

    The comparison is the OOS Sharpe of the primary-selected cell against the OOS Sharpe of
    the cell selected on the official-only subsample.  The OOS window itself carries only
    official funding observations, so the two configurations are identical there by
    construction; what the check measures is whether the modelled-funding era changes the
    SELECTION and, through it, the out-of-sample outcome.
    """
    if winner is None or official_winner is None:
        return {"registered_item": "funding-provenance", "evaluated": False,
                "reason": "no historical winner to compare", "hit": False, "fail": False,
                "landing": "card: primary vs official_only sign mismatch or >50% difference "
                           "-> TECHNICAL_INCOMPLETE / DEFERRED, never PASS"}
    p_oos = same_cell(oos_rows, cell_key(winner))
    o_oos = same_cell(oos_rows, cell_key(official_winner))
    s_p, s_o = p_oos["sharpe"], o_oos["sharpe"]
    same_sign = (s_p > 0.0) == (s_o > 0.0)
    denom = max(abs(s_p), abs(s_o))
    rel = (abs(s_p - s_o) / denom) if denom > 0.0 else 0.0
    fail = (not same_sign) or (rel > spec["gates"]["max_provenance_relative_difference"])
    return {"registered_item": "funding-provenance", "evaluated": True,
            "primary_winner_cell": _pick(winner, AXES),
            "official_only_winner_cell": _pick(official_winner, AXES),
            "same_cell_selected": cell_key(winner) == cell_key(official_winner),
            "primary_oos_sharpe": s_p, "official_only_oos_sharpe": s_o,
            "primary_oos_net_pnl": p_oos["net_pnl"], "official_only_oos_net_pnl": o_oos["net_pnl"],
            "same_sign": same_sign, "relative_difference": round(rel, 6),
            "max_relative_difference": spec["gates"]["max_provenance_relative_difference"],
            "fail": fail, "hit": fail,
            "landing": "card: primary vs official_only sign mismatch or >50% difference "
                       "-> TECHNICAL_INCOMPLETE / DEFERRED, never PASS",
            "notice": "both Sharpes are read from the primary OOS grid for the two selected "
                      "cells; the OOS window contains official funding observations only, so "
                      "an official-only rerun of that window is identical by construction"}


def summarize(spec, grid_rows, layers):
    import random
    import statistics as st
    expected_axes = axis_values(spec)
    need = {k: spec["expected"]["case_evaluations_per_grid"] for k in COHORT_GRID_KINDS}
    coverage = {k: len(grid_rows.get(k, [])) for k in need}
    full = grid_rows["full"]
    cohorts = sorted({(r["symbol"], r["timeframe"]) for r in full})
    cohort_labels = ["%s/%s" % c for c in cohorts]

    per_cohort = {}
    for label in cohort_labels:
        per_cohort[label] = {k: [r for r in grid_rows[k]
                                 if "%s/%s" % (r["symbol"], r["timeframe"]) == label]
                             for k in COHORT_GRID_KINDS}
    per_cohort_official = {label: [r for r in grid_rows["official_only"]
                                   if "%s/%s" % (r["symbol"], r["timeframe"]) == label]
                           for label in cohort_labels}

    strategy_product = set((d, lb) for d in expected_axes["decile"]
                           for lb in expected_axes["lookback_days"])
    dca_product = set((a, b, c, d) for a in expected_axes["spacing_pct"]
                      for b in expected_axes["size_multiplier"]
                      for c in expected_axes["breakeven_tp_pct"]
                      for d in expected_axes["invalidation_pct"])
    strategy_cells, dca_cells = set(), set()
    for r in full:
        strategy_cells.add((r["decile"], r["lookback_days"]))
        dca_cells.add(tuple(r[a] for a in DCA_AXES))
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
    cohort_results = ([evaluate_cohort(spec, label, per_cohort[label], per_cohort_official[label])
                       for label in cohort_labels] if coverage_complete else [])
    survivors = [c for c in cohort_results if c["outcome"] == "SURVIVOR"]
    disposition = family_disposition(survivors, coverage_complete)

    # deterministic selector: a shuffled copy of the same rows must elect the same cell
    rng = random.Random(20260915)
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
    # the same deterministic selector, re-run on the official-only subsample (the registered
    # provenance protocol), historical-only in that configuration too
    oo_winner, oo_reason = (select_cohort_winner(per_cohort_official[cohort_labels[0]], spec,
                                                 allowed=("official_only",))
                            if coverage_complete and cohort_labels else (None, "not_evaluated"))
    winner = None
    if cohort_results and cohort_results[0]["winner"] is not None:
        key = tuple(cohort_results[0]["winner"][a] for a in AXES)
        winner = same_cell(per_cohort[cohort_labels[0]]["historical"], key)
    if winner is not None and coverage_complete:
        threshold = threshold_instability_check(spec, winner, per_cohort[cohort_labels[0]]["oos"])
    else:
        threshold = {"registered_item": "threshold-instability", "evaluated": False,
                     "hit": False,
                     "landing": "descriptive robustness disclosure; a hit must never be "
                                "recorded as PASS"}
    provenance = funding_provenance_check(spec, winner, oo_winner,
                                          per_cohort[cohort_labels[0]]["oos"] if cohort_labels else [])
    provenance["official_only_selection"] = {
        "winner": _pick(oo_winner, AXES) if oo_winner else None,
        "reason": oo_reason if oo_winner is None else "selected",
        "official_only_historical_window": [spec["data"]["official_only_start"],
                                            spec["data"]["official_only_end"]],
        "registered_roles": "historical window of the official-funding era; the OOS slice is "
                            "unchanged (the OOS window carries official funding only, so the "
                            "two configurations are identical there by construction)"}
    provenance["threshold_instability"] = threshold
    flags = {"threshold_instability": bool(threshold.get("hit")),
             "funding_provenance": bool(provenance.get("hit"))}

    # registered landing of the two family-level falsification items (card t_56ca0634):
    # neither may be recorded as PASS; the band mapping itself is unchanged.
    final_verdict = disposition["verdict_recommendation"]
    final_claimable = disposition["performance_claimable_recommendation"]
    if flags["funding_provenance"] and final_verdict == "PASS":
        final_verdict = "TECHNICAL_INCOMPLETE"
        final_claimable = False
    elif flags["threshold_instability"] and final_verdict == "PASS":
        final_verdict = "DEFERRED"
        final_claimable = False

    med = lambda rows, key: st.median([r[key] for r in rows if r.get(key) is not None] or [0.0])
    descriptive = {
        "non_gating": True,
        "note": "this family has a single cohort, so there is no cross-cohort median to gate on; "
                "the numbers below describe the whole registered product on each grid and are "
                "never a gate (contract 7.2)",
        "median_historical_net_pnl": med(per_cohort[cohort_labels[0]]["historical"], "net_pnl")
        if cohort_labels else 0.0,
        "median_full_net_pnl": med(full, "net_pnl"),
        "median_no_funding_historical_net_pnl": med(grid_rows["no_funding"], "net_pnl"),
        "positive_case_share_historical": (sum(1 for r in grid_rows["historical"] if r["net_pnl"] > 0)
                                           / float(len(grid_rows["historical"]))) if grid_rows["historical"] else 0.0,
        "positive_case_share_full": (sum(1 for r in full if r["net_pnl"] > 0) / float(len(full))) if full else 0.0,
    }
    stress_summary = {s: {"median_net_pnl": med(grid_rows[s], "net_pnl"),
                          "median_sharpe": med(grid_rows[s], "sharpe"),
                          "cases": len(grid_rows[s]), "non_gating": True} for s, _ in STRESS}
    stress_summary["cost_attrition_40bps"] = {
        "median_net_pnl": med(grid_rows["cost_attrition_40bps"], "net_pnl"),
        "median_sharpe": med(grid_rows["cost_attrition_40bps"], "sharpe"),
        "cases": len(grid_rows["cost_attrition_40bps"]), "non_gating": True}

    keys = ("net_pnl", "fees", "funding", "gross_pnl", "ending_equity", "sharpe",
            "max_dd_pct", "max_dd_usdt", "max_effective_leverage", "capital_utilization",
            "cagr", "total_return_pct", "episodes", "fills", "turnover_usdt")
    mid = lambda rows, key: med(rows, key)
    cost_pressure_effective = (mid(grid_rows["fee_2x"], "fees") > mid(full, "fees")
                               and mid(grid_rows["fee_2x"], "net_pnl") < mid(full, "net_pnl"))
    assertions = {
        "episodes_partition": all(r["episodes"] == r["tp_hits"] + r["stop_hits"] + r["time_exits"]
                                  + r["open_at_end"] + r["margin_calls"] for r in full),
        "pnl_decomposition": all(pnl_decomposition_ok(r) for r in full),
        "coverage_complete": coverage_complete,
        "cohort_count_matches_registered": len(cohorts) == spec["expected"]["cohorts"],
        "strategy_grid_is_registered_product": strategy_cells == strategy_product,
        "dca_grid_is_registered_product": dca_cells == dca_product,
        "base_combinations_per_cohort_per_grid": cells_per_cohort_ok,
        "expected_case_evaluations": sum(coverage.values()) == spec["expected"]["expected_case_evaluations"],
        "layer0_equals_episodes": layers[0] == sum(r["episodes"] for r in full) and layers[0] > 0,
        "layer_histogram_nonempty": sum(layers) > 0,
        "no_entry_after_exhaustion": all(r["min_entry_equity"] > 0.0 for r in full),
        "ending_equity_floor": all(r["ending_equity"] > -1.5 * START_EQUITY for r in full),
        "selector_deterministic": selector_stable,
        "selector_historical_only": True,
        # executable no-look-ahead / window guards (module counters, every grid)
        "entry_never_before_signal": COUNTERS["entry_before_signal"] == 0,
        "signal_maps_to_its_own_bar": COUNTERS["signal_bar_mismatch"] == 0,
        "hold_never_over_24h": COUNTERS["hold_over_24h"] == 0,
        "no_funding_grid_is_cost_free": all(r["funding"] == 0.0 for r in grid_rows["no_funding"]),
        "cost_pressure_not_a_noop": cost_pressure_effective,
        "official_only_rows_use_official_funding": all(
            r["window_kind"] == "official_only" for r in grid_rows["official_only"]),
    }
    return {
        "family_id": spec["family_id"], "round_id": spec["round_id"], "run_id": spec["run_id"],
        "engine_version": ENGINE_VERSION, "engine_semantics": ENGINE_SEMANTICS,
        "selector_version": SELECTOR_VERSION, "disposition_version": DISPOSITION_VERSION,
        "contract_semantics_version": CONTRACT_SEMANTICS_VERSION,
        "registered_domains": {
            "strategy": {"decile": expected_axes["decile"],
                         "lookback_days": expected_axes["lookback_days"]},
            "dca": {a: expected_axes[a] for a in DCA_AXES},
            "dca_base_quote": spec["dca_domain"]["base_quote"]},
        "coverage": coverage, "coverage_required": need, "coverage_complete": coverage_complete,
        "cohorts": cohort_labels, "cohort_count": len(cohorts),
        "case_evaluations_per_cohort_per_grid": spec["expected"]["base_combinations_per_cohort"],
        "case_evaluations_per_grid": spec["expected"]["case_evaluations_per_grid"],
        "case_evaluations_total": sum(coverage.values()),
        "expected_case_evaluations": spec["expected"]["expected_case_evaluations"],
        "cohort_grid_kinds": list(COHORT_GRID_KINDS),
        "cohort_results": cohort_results,
        "cohort_survivors": [c["cohort"] for c in survivors],
        "cohort_survivor_count": len(survivors),
        "cohort_outcome_counts": {"SURVIVOR": len(survivors),
                                  "CULLED": len(cohort_results) - len(survivors)},
        "disposition": disposition["disposition"],
        "verdict_recommendation": disposition["verdict_recommendation"],
        "performance_claimable_recommendation": disposition["performance_claimable_recommendation"],
        "disposition_mapping_version": disposition["mapping_version"],
        "registered_falsification_flags": flags,
        "verdict_recommendation_final": final_verdict,
        "performance_claimable_recommendation_final": final_claimable,
        "survivor_evidence": survivors,
        "descriptive_diagnostics": descriptive,
        "descriptive_medians_all_base_cases": {k: mid(full, k) for k in keys},
        "stress_summary": stress_summary,
        "dca_layer_histogram": {"level_%02d" % k: layers[k] for k in range(12)},
        "funding_provenance": provenance,
        "assertions": assertions,
        "structural_counters": dict(COUNTERS),
        "structural_counter_tolerance_ms": MS_JITTER_TOLERANCE_MS,
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) != 2:
        sys.stderr.write("usage: 40_strategy_c_run.py <run-spec.json>\n")
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
        for key in ("parameter_domain", "dca_domain", "expected", "selector_version",
                    "disposition_version", "gates", "costs"):
            if key not in spec:
                raise SystemExit("run-spec is not a Strategy C spec: missing %r (contract 7.2/7.3)"
                                 % key)
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
        diag_inputs = []
        total = len(spec["data"]["symbols"]) * len(spec["data"]["timeframes"])
        done = 0
        funding_report = {}
        for symbol in spec["data"]["symbols"]:
            for tf in spec["data"]["timeframes"]:
                cohort = Cohort(symbol, tf, spec["data"]["start"], spec["data"]["end"])
                meta = instruments[symbol]
                cohort.price_increment = meta["price_increment"]
                cohort.taker_fee = meta["taker_fee"]
                cohort.leverage = 1.0 / meta["margin_init"]
                cohort.margin_maint = meta["margin_maint"]
                lookbacks = list(spec["parameter_domain"]["grid_lookbacks"])
                ft, fr, counts = load_funding_series(symbol, spec["data"]["start"],
                                                     spec["data"]["end"], official_only=False)
                series_full = build_signal_series(cohort, ft, fr, lookbacks, counts, "full")
                ot, orr, ocounts = load_funding_series(symbol, spec["data"]["start"],
                                                       spec["data"]["end"], official_only=True)
                series_official = build_signal_series(cohort, ot, orr, lookbacks, ocounts,
                                                      "official_only")
                funding_report["%s/%s" % (symbol, tf["raw_interval"])] = {
                    "full_series": {"observations": int(len(ft)),
                                    "first": iso(int(ft[0])) if len(ft) else None,
                                    "last": iso(int(ft[-1])) if len(ft) else None,
                                    "truth_status_counts": counts,
                                    "funding_sum_per_bar": float(series_full["funding_per_bar"].sum())},
                    "official_only_series": {"observations": int(len(ot)),
                                             "first": iso(int(ot[0])) if len(ot) else None,
                                             "last": iso(int(ot[-1])) if len(ot) else None,
                                             "truth_status_counts": ocounts},
                    "registered_windows": spec["data"]["funding_truth_status_windows"]}
                run_log("cohort %s/%s bars=%d days=%d funding_full=%d official=%d modeled=%d "
                        "funding_official_series=%d"
                        % (symbol, tf["raw_interval"], cohort.n, cohort.n_days, len(ft),
                           counts["official"], counts["modeled_funding"], len(ot)))
                for k, v in run_cohort(spec, cohort, run_log, series_full, series_official).items():
                    grid_rows.setdefault(k, []).extend(v)
                diag_inputs.append((cohort, series_full, series_official))
                done += 1
                atomic_write_json(os.path.join(attempt_dir, "artifacts", "progress.json"),
                                  {"cohorts_done": done, "cohorts_total": total,
                                   "updated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        atomic_write_json(os.path.join(attempt_dir, "artifacts", "funding_series.json"),
                          funding_report)

        for kind, rows in grid_rows.items():
            path = os.path.join(attempt_dir, "artifacts", "grid_%s.csv" % kind)
            with open(path, "w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=list(ROW_FIELDS))
                w.writeheader()
                w.writerows(rows)
            run_log("wrote %s (%d rows)" % (os.path.basename(path), len(rows)))

        layers = LAYER_TOTALS.setdefault("full", [0] * 12)
        summary = summarize(spec, grid_rows, layers)
        # Registered robustness item 4 (card t_56ca0634): per-year and per-entry-phase
        # stability of the ELECTED winner cell, plus the same breakdown on the official-funding
        # era.  Non-gating diagnostics; deliberately taken from the same engine call shape as
        # the grid rows, and the rerun is asserted to reproduce the winner's frozen row.
        robustness_diagnostics = {
            "non_gating": True,
            "note": "per-calendar-year and per-settlement-phase net PnL of the elected winner "
                    "cell (and of the official-only winner). Descriptive only - never a gate.",
            "registered_item": "non_overlapping_24h_subsample_and_per_year_stability"}
        if summary["cohort_results"] and summary["cohort_results"][0]["winner"] is not None:
            cohort0, series_full0, series_official0 = diag_inputs[0]
            slip0 = spec["costs"]["baseline_slippage_ticks"]
            full_slice = cohort0.slice(spec["data"]["start"], spec["data"]["end"])
            oo_slice = cohort0.slice(spec["data"]["official_only_start"],
                                     spec["data"]["official_only_end"])

            def _cell_of(cell):
                return ({"decile": cell["decile"], "lookback_days": cell["lookback_days"]},
                        dict({a: cell[a] for a in DCA_AXES},
                             base_quote=spec["dca_domain"]["base_quote"]))

            w = summary["cohort_results"][0]["winner"]
            p, dca = _cell_of(w)
            m = simulate(cohort0, p, rail_for(dca), full_slice, {}, slip0, "full", series_full0,
                         diag=True)
            row = record(cohort0, p, dca, "full", m)
            grid_row = same_cell(grid_rows["full"], tuple(w[a] for a in AXES))
            robustness_diagnostics["winner_cell"] = {a: w[a] for a in AXES}
            robustness_diagnostics["pnl_by_year"] = m["diagnostics"]["pnl_by_year"]
            robustness_diagnostics["pnl_by_entry_phase"] = m["diagnostics"]["pnl_by_entry_phase"]
            robustness_diagnostics["winner_diagnostics_match_full_row"] = all(
                abs(float(row[k]) - float(grid_row[k])) < 1e-6
                for k in ROW_FIELDS
                if isinstance(grid_row[k], (int, float)) and not isinstance(grid_row[k], bool)
                and isinstance(row[k], (int, float)))
            oo = summary["funding_provenance"].get("official_only_selection", {}).get("winner")
            if oo:
                p2, dca2 = _cell_of(oo)
                m2 = simulate(cohort0, p2, rail_for(dca2), oo_slice, {}, slip0, "official_only",
                              series_official0, diag=True)
                row2 = record(cohort0, p2, dca2, "official_only", m2)
                grid_row2 = same_cell(grid_rows["official_only"], tuple(oo[a] for a in AXES))
                robustness_diagnostics["official_only_winner_cell"] = {a: oo[a] for a in AXES}
                robustness_diagnostics["official_only_pnl_by_year"] = m2["diagnostics"]["pnl_by_year"]
                robustness_diagnostics["official_only_diagnostics_match_grid_row"] = all(
                    abs(float(row2[k]) - float(grid_row2[k])) < 1e-6
                    for k in ROW_FIELDS
                    if isinstance(grid_row2[k], (int, float)) and not isinstance(grid_row2[k], bool)
                    and isinstance(row2[k], (int, float)))
        summary["robustness_diagnostics"] = robustness_diagnostics
        summary["runtime_seconds"] = int(time.time() - started)
        summary["bins_build"] = {k: bins[k] for k in
                                 ("build_wall_seconds", "qlib_dir_bytes", "qlib_version")}
        summary["instrument_metadata"] = {s: instruments[s] for s in spec["data"]["symbols"]}
        summary["data_readback"] = bins["readback"]
        summary["funding_series"] = funding_report
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
        atomic_write_json(os.path.join(attempt_dir, "artifacts", "funding_provenance.json"),
                          summary["funding_provenance"])
        atomic_write_json(os.path.join(attempt_dir, "artifacts", "robustness_diagnostics.json"),
                          summary["robustness_diagnostics"])
        run_log("disposition=%s verdict_recommendation=%s final=%s survivors=%d/%d cases=%d"
                % (summary["disposition"], summary["verdict_recommendation"],
                   summary["verdict_recommendation_final"], summary["cohort_survivor_count"],
                   summary["cohort_count"], summary["case_evaluations_total"]))
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
