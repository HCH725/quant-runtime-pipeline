#!/usr/bin/env python3
"""Strategy F - Stochastic RSI (Renko) family representative, on Qlib.

Entry point for ONE pre-registered attempt.  Reads the immutable run-spec from /results (the
only source of parameters), builds the Qlib .bin store from the READ-ONLY canonical raw store
into /qlib/work, then runs the pre-registered full-backtest contract
(QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md sections 7.2 / 7.3):

    20 cohorts (BTCUSDT/ETHUSDT/BNBUSDT/SOLUSDT x {5m,15m,30m,1h,4h} source grids)
    x STRATEGY domain (the 9 registered (brick_pct, rsi_period) cells)
    x DCA domain (4 axes = 48 configs)
    x 10 registered phase grids (historical / oos / full / fee_2x / funding_2x /
      entry_delay_1_bar / slippage_2ticks / no_funding / no_funding_full /
      cost_attrition_40bps)

SIGNAL (the source's own semantics; execution details fixed before the first run): the
published strategy is a Stochastic-RSI K/D crossover **evaluated on Renko charts**.  Renko
bricks are built causally from the cohort's own canonical bars by the registered geometric
contract below; the indicator is computed on the BRICK CLOSE series; a K-above-D cross is the
source's buy signal and a K-below-D cross its sell signal, so the family trades BOTH
directions and flips on the crossing.  Nothing in the signal path reads a future bar: a brick
becomes known at the close of the bar that formed it, all bricks formed inside one bar are
stamped at that bar's close (never earlier), and the order decision fills at the NEXT bar's
open.

The economic claim under test is the source's own: that a Renko-constructed price series
filters noise well enough for a Stochastic-RSI crossover to be worth trading after real costs
and funding.  The family is culled by the same deterministic cohort gate as the other
families; a negative outcome is a result, not a defect (contract 13).

EXECUTION (inherited DCA rail semantics): tranche #1 opens at the entry bar's open, adverse
price scale-ins follow the registered ladder (level_k price = initial_entry_price x
(1 -/+ spacing_pct x k), k = 1..10, so at most 11 routine active levels of the 12-tranche
rail), the take profit is reduce-only at running_average_cost x (1 +/- breakeven_tp_pct), the
invalidation is a RESTING stop at running_average_cost x (1 -/+ invalidation_pct), an opposite
crossing reduce-only flattens every layer and opens the mirrored side, and an open position is
reduce-only flattened at the slice's last bar.  Between positions the book is FLAT and no layer
may be added.  No new episode is opened once the realised equity is gone: the capital-exhaustion
guard is re-evaluated after the opposite-crossing flatten as well as before the signal, because
that flatten is itself a fill that can consume the remaining equity.  Every fill is a market
order: taker fee on its own notional plus registered
ADVERSE slippage of `slip_ticks` instrument ticks (entry and scale-in pay up for a buy / down
for a sell, exits the mirror: the price always moves against the position), and every funding
settlement inside the closed holding interval is charged on the position notional that was at
risk.  DCA is executed as real order/fill accounting (an episode state machine over the bars);
nothing is estimated after the fact.

Writes only:
  * /qlib/work/**      (rebuildable derived/cache area, INV-5)
  * <attempt_dir>/**   (result.json, artifacts/, logs/, state.json)
Never writes /data/raw (read-only mount).

usage: 60_strategy_f_run.py <run-spec.json>
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
WORK_ROOT = "/qlib/work/strategyF-v1"
CSV_ROOT = os.path.join(WORK_ROOT, "csv")
QLIB_DIR = os.path.join(WORK_ROOT, "qlib-data")
MS_PER_DAY = 86400000
MS_PER_HOUR = 3600000
FIELDS = ["open", "high", "low", "close", "volume"]
START_EQUITY = 30000.0
MAX_ADD_LEVELS = 10
MS_JITTER_TOLERANCE_MS = 1000
# ------------------------------------------------------------- registered Renko contract
# Geometric ("percent") bricks: the brick threshold is brick_pct of the CURRENT reference
# price, so the ladder is self-similar across the sample's price range and is defined by past
# prices only.  Within one source bar bricks are formed by the registered deterministic loop;
# when a single bar's range crosses both thresholds the side is chosen by the bar's own
# direction (close >= open -> up first).  Every brick formed inside a bar is stamped at that
# bar's CLOSE, the conservative (latest possible) timestamp.
MAX_BRICKS_PER_BAR = 200
# ------------------------------------------------------- registered indicator contract
STOCH_PERIOD = 14          # Stochastic window over the RSI series (bricks)
K_SMOOTH = 3               # K = SMA(raw stochastic, K_SMOOTH) on bricks
D_SMOOTH = 3               # D = SMA(K, D_SMOOTH) on bricks
# The registered strategy domain: (brick_pct, rsi_period) cells in the registered order.
CASE_ORDER = ((0.005, 7), (0.005, 14), (0.005, 21),
              (0.01, 7), (0.01, 14), (0.01, 21),
              (0.02, 7), (0.02, 14), (0.02, 21))
CASE_NAMES = ("brick0p5_rsi7", "brick0p5_rsi14", "brick0p5_rsi21",
              "brick1_rsi7", "brick1_rsi14", "brick1_rsi21",
              "brick2_rsi7", "brick2_rsi14", "brick2_rsi21")
# Execution stress reruns (full-window slice), contract 7.2 robustness.
STRESS = [
    ("fee_2x", {"fee_mult": 2.0}),
    ("funding_2x", {"funding_mult": 2.0}),
    ("entry_delay_1_bar", {"entry_delay_1_bar": True}),
    ("slippage_2ticks", {"slip_ticks": 2}),
]
# Registered cost-attrition grid: 8 x the 5 bps taker fee = 40 bps per fill.
COST_ATTRITION_STRESS = {"fee_mult": 8.0}
# Registered phase grids: every one of them covers the FULL strategy-domain x DCA-domain
# product of every cohort (contract 7.2).
COHORT_GRID_KINDS = ("historical", "oos", "full", "fee_2x", "funding_2x",
                     "entry_delay_1_bar", "slippage_2ticks", "no_funding",
                     "no_funding_full", "cost_attrition_40bps")
# grids whose slice is the FULL registered window (used by the assertions)
FULL_WINDOW_GRID_KINDS = ("full", "fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks",
                          "no_funding_full", "cost_attrition_40bps")
# Joint parameter space axes, in the one registered order used for the deterministic lexical
# tie-break (contract 7.3).  Order is part of the gate.
AXES = ("brick_pct", "rsi_period", "spacing_pct", "size_multiplier", "breakeven_tp_pct",
        "invalidation_pct")
DCA_AXES = ("spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct")
CASE_FIELDS = ("brick_pct", "rsi_period")
SELECTOR_VERSION = "cohort-selector-v1"
DISPOSITION_VERSION = "cohort-disposition-v1"
CONTRACT_SEMANTICS_VERSION = "v1.4.0"
ENGINE_VERSION = "f-v1-engine-1.0.1"
ENGINE_SEMANTICS = ("geometric Renko bricks (brick_pct of the current reference price) built "
                    "from the cohort's own bars, stamped at the forming bar's close; Wilder RSI "
                    "and a Stochastic-RSI K/D cross on the BRICK CLOSE series; next-bar-open "
                    "entries; DCA rail with flip/TP/invalidation/slice-end exits; per-fill "
                    "taker fee, adverse tick slippage and per-settlement funding (see module "
                    "docstring)")
WINNER_METRIC_KEYS = ("net_pnl", "sharpe", "episodes", "ending_equity", "fees", "funding",
                      "max_dd_usdt", "max_dd_pct", "max_effective_leverage",
                      "capital_utilization", "annualized_return", "cagr")
# csv row field order (kept identical for every phase grid)
ROW_FIELDS = ("symbol", "timeframe", "window_kind", "case_name",
              "brick_pct", "rsi_period",
              "spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct",
              "net_pnl", "fees", "funding", "gross_pnl", "ending_equity",
              "signals_seen", "signals_entered", "episodes",
              "tp_hits", "stop_hits", "flip_exits", "slice_end_flats", "open_at_end",
              "margin_calls", "halted", "min_entry_equity", "sharpe", "max_dd_usdt",
              "max_dd_pct", "max_effective_leverage", "capital_utilization",
              "bars_in_market", "fills", "turnover_usdt", "days", "years", "cagr",
              "total_return_pct", "annualized_return")
COUNTERS = {}
LAYER_TOTALS = {}


def counter(kind, name, inc=1):
    COUNTERS.setdefault(kind, {})
    COUNTERS[kind][name] = COUNTERS[kind].get(name, 0) + inc


def counters_total(name):
    return sum(v.get(name, 0) for v in COUNTERS.values())


def log(msg):
    sys.stderr.write("[%s] %s\n" % (time.strftime("%H:%M:%S", time.gmtime()), msg))
    sys.stderr.flush()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def _sanitize(o):
    """JSON-safe copy: numpy scalars -> python, NaN/Inf -> null (never a fake number)."""
    if isinstance(o, dict):
        return {str(k): _sanitize(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_sanitize(v) for v in o]
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        o = float(o)
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def atomic_write_json(path, payload):
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(_sanitize(payload), fh, indent=2, ensure_ascii=False, allow_nan=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


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
    return int(calendar.timegm(time.strptime(date_str[:10], "%Y-%m-%d"))) * 1000


def iso(ms):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000.0))


def qlib_window(start, end):
    return "%s 00:00:00" % start[:10], "%s 23:59:59" % end[:10]


def case_tuple(p):
    return (p["brick_pct"], p["rsi_period"])


def case_name(case):
    return CASE_NAMES[CASE_ORDER.index(case)]


def params_of(row):
    """One frozen grid row -> the strategy params dict the engine consumes."""
    return {"brick_pct": row["brick_pct"], "rsi_period": row["rsi_period"]}


# ---------------------------------------------------------------------------
# phase 1: raw (read-only) -> Qlib bin store
# ---------------------------------------------------------------------------

def interval_ms(raw_interval):
    unit = raw_interval[-1]
    mult = int(raw_interval[:-1] or 1)
    base = {"m": 60000, "h": MS_PER_HOUR, "d": MS_PER_DAY, "w": 7 * MS_PER_DAY}[unit]
    return mult * base


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
    step = interval_ms(interval)
    rows = 0
    first_ms = last_ms = None
    off_grid = 0
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
                    if first_ms is not None and ms - last_ms != step:
                        off_grid += 1
                    ts = time.gmtime(ms / 1000.0)
                    w.writerow(["%04d-%02d-%02d %02d:%02d:%02d" % (
                        ts.tm_year, ts.tm_mon, ts.tm_mday, ts.tm_hour, ts.tm_min, ts.tm_sec),
                        r["open"], r["high"], r["low"], r["close"], r["volume"]])
                    rows += 1
                    if first_ms is None:
                        first_ms = ms
                    last_ms = ms
    return {"rows": rows, "first_open_time_ms": first_ms, "last_open_time_ms": last_ms,
            "csv": out_path, "csv_bytes": os.path.getsize(out_path),
            "off_interval_grid_steps": off_grid, "interval_ms": step}


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
    # /qlib/work is a rebuildable derived/cache area (INV-5): rebuild from scratch so the
    # recorded build wall time / size describe THIS attempt's store.
    if os.path.isdir(WORK_ROOT):
        shutil.rmtree(WORK_ROOT)
    per_dataset = {}
    for symbol in data["symbols"]:
        for tf in data["timeframes"]:
            key = "%s/%s" % (symbol, tf["raw_interval"])
            per_dataset[key] = write_csv(symbol, tf["raw_interval"], tf["qlib_freq"], start, end)
            run_log("csv %s rows=%d bytes=%d off_grid_steps=%d"
                    % (key, per_dataset[key]["rows"], per_dataset[key]["csv_bytes"],
                       per_dataset[key]["off_interval_grid_steps"]))

    for tf in data["timeframes"]:
        cmd = [VENV_PYTHON, DUMP_BIN, "dump_all",
               "--data_path", os.path.join(CSV_ROOT, tf["qlib_freq"]),
               "--qlib_dir", QLIB_DIR, "--freq", tf["qlib_freq"],
               "--include_fields", "open,close,high,low,volume",
               "--date_field_name", "date", "--max_workers", "1"]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=14400)
        if p.returncode != 0:
            raise SystemExit("dump_bin failed for freq %s rc=%d\n%s\n%s"
                             % (tf["qlib_freq"], p.returncode, p.stdout[-2000:],
                                p.stderr[-2000:]))
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
                "raw_rows": ds["rows"], "off_interval_grid_steps": ds["off_interval_grid_steps"],
                "timestamp_is_strictly_increasing": bool(
                    all(int(a.value // 1_000_000) < int(b.value // 1_000_000)
                        for a, b in zip(ts[:-1], ts[1:])))}
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
        self.bar_ms = int(self.open_time_ms[1] - self.open_time_ms[0]) if self.n > 1 else 0
        steps = np.diff(self.open_time_ms)
        self.non_bar_steps = int(np.count_nonzero(steps != self.bar_ms))
        # This family's signal is bar-SEQUENTIAL (bricks are price-driven, never clock-driven),
        # so a missing bar is measured and disclosed rather than fatal: nothing maps a clock
        # boundary to a bar index here.  Only the ordering may not break.
        if self.n > 1 and int(np.count_nonzero(steps <= 0)):
            counter("cohort", "bar_grid_not_ascending", 1)
            raise SystemExit("cohort %s/%s: open_time_ms is not strictly ascending"
                             % (symbol, self.timeframe))
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


def load_funding_series(symbol, start, end):
    """Every registered funding settlement in the window, with its truth_status disclosed."""
    path = os.path.join(RAW_ROOT, "funding", symbol, "%s-funding.jsonl.gz" % symbol)
    lo, hi = utc_ms(start), utc_ms(end) + MS_PER_DAY - 1
    times, rates = [], []
    counts = {"official": 0, "modeled_funding": 0, "other": 0, "out_of_window": 0}
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
            times.append(int(ms))
            rates.append(float(r["funding_rate"]))
    return (np.array(times, dtype=np.int64), np.array(rates, dtype=np.float64), counts)


def build_funding_index(cohort, times, rates, counts, label):
    """Funding settlements + the bar each one belongs to (the bar that CONTAINS it).

    `settle_bar` is the bar whose CLOSE is the mark price at the settlement instant (the bar
    that ENDS at it) and is what schedules a charge; `settle_bar_closed` is the bar that
    CONTAINS the instant (half-open [open, open + interval)) and is read by the out-of-hold
    guard only, so a charge can never move because of the guard.
    """
    containing = np.searchsorted(cohort.open_time_ms, times, side="left") - 1
    closed = np.searchsorted(cohort.open_time_ms, times, side="right") - 1
    return {"label": label, "counts": counts, "obs_times": times, "obs_rates": rates,
            "settle_bar": containing.astype(np.int64),
            "settle_bar_closed": closed.astype(np.int64),
            "first_obs_ms": int(times[0]) if len(times) else None,
            "last_obs_ms": int(times[-1]) if len(times) else None}


# ---------------------------------------------------------------- signal kernel
def renko_bricks(brick_pct, close, kind="full"):
    """Causal geometric Renko driven by each source bar's CLOSE.

    The registered construction contract: the brick threshold is brick_pct of the CURRENT
    reference price (so the ladder is self-similar across the sample's price range and depends
    on past prices only), the reference is initialised at the first source bar's close, and a
    bar forms bricks until its CLOSE is back inside the ladder.  The ladder therefore moves
    MONOTONICALLY toward each bar's close and only one direction is ever in play inside one
    bar: the engine never invents an intrabar round trip it cannot observe, and the number of
    bricks a bar can form is bounded by its own close-vs-reference ratio.  Every brick formed
    by a bar is stamped at THAT bar's close - the conservative (latest possible) timestamp - so
    nothing in the signal path reads a bar that has not closed yet.

    Returns (brick_close, formed_bar): brick_close[j] is the reference price after the j-th
    brick and formed_bar[j] the source bar index at whose close it became known.
    """
    n = len(close)
    if n == 0:
        return np.zeros(0, dtype=np.float64), np.zeros(0, dtype=np.int64)
    ref = float(close[0])
    closes = []
    formed = []
    for b in range(1, n):
        tgt = float(close[b])
        made = 0
        while tgt >= ref * (1.0 + brick_pct) and made < MAX_BRICKS_PER_BAR:
            ref = ref * (1.0 + brick_pct)
            closes.append(ref)
            formed.append(b)
            made += 1
        while tgt <= ref / (1.0 + brick_pct) and made < MAX_BRICKS_PER_BAR:
            ref = ref / (1.0 + brick_pct)
            closes.append(ref)
            formed.append(b)
            made += 1
        if made >= MAX_BRICKS_PER_BAR:
            counter(kind, "brick_cap_reached")
    if not closes:
        return np.zeros(0, dtype=np.float64), np.zeros(0, dtype=np.int64)
    return (np.array(closes, dtype=np.float64), np.array(formed, dtype=np.int64))


def wilder_rsi(x, period):
    """Wilder's RSI on the brick close series (NaN until the period is warm)."""
    n = len(x)
    out = np.full(n, np.nan, dtype=np.float64)
    if n <= period:
        return out
    d = np.diff(x)
    gain = np.where(d > 0.0, d, 0.0)
    loss = np.where(d < 0.0, -d, 0.0)
    ag = float(gain[:period].mean())
    al = float(loss[:period].mean())
    out[period] = 100.0 * ag / (ag + al) if (ag + al) > 0.0 else 50.0
    for i in range(period + 1, n):
        ag = (ag * (period - 1) + gain[i - 1]) / period
        al = (al * (period - 1) + loss[i - 1]) / period
        out[i] = 100.0 * ag / (ag + al) if (ag + al) > 0.0 else 50.0
    return out


def sma(x, window):
    """Simple moving average over `window` samples; NaN until the window is full.

    A NaN sample makes the value NaN (no partial-window shortcut), so the caller's warm-up
    rule is the only thing that decides when the indicator exists.
    """
    n = len(x)
    out = np.full(n, np.nan, dtype=np.float64)
    if window <= 0 or n < window:
        return out
    for i in range(window - 1, n):
        chunk = x[i - window + 1:i + 1]
        if np.isnan(chunk).any():
            continue
        out[i] = float(chunk.sum()) / float(window)
    return out


def indicator_events(brick_close, formed, rsi_period, kind="full"):
    """The registered signal on a brick series: Stochastic-RSI K/D crossings.

    Stochastic RSI (source): RSI over `rsi_period` bricks, then the stochastic of that RSI over
    STOCH_PERIOD bricks, K = SMA(raw, K_SMOOTH), D = SMA(K, D_SMOOTH).  K crossing above D is
    the source's buy signal, K crossing below D its sell signal (strict inequalities, so a flat
    stretch is not a crossing).  Returns the arrays plus `events` = [(formed_bar, sign)] with
    same-bar crossings collapsed to the LAST one (the bar-close reality).
    """
    m = len(brick_close)
    rsi = wilder_rsi(brick_close, rsi_period)
    raw = np.full(m, np.nan, dtype=np.float64)
    for i in range(m):
        if i + 1 < STOCH_PERIOD:
            continue
        win = rsi[i - STOCH_PERIOD + 1:i + 1]
        if np.isnan(win).any():
            continue
        lo, hi = float(win.min()), float(win.max())
        raw[i] = 0.5 if hi <= lo else (float(rsi[i]) - lo) / (hi - lo)
    k = sma(raw, K_SMOOTH)
    d = sma(k, D_SMOOTH)
    events = []
    for i in range(1, m):
        if np.isnan(k[i]) or np.isnan(d[i]) or np.isnan(k[i - 1]) or np.isnan(d[i - 1]):
            continue
        if k[i - 1] <= d[i - 1] and k[i] > d[i]:
            events.append((int(formed[i]), 1))
        elif k[i - 1] >= d[i - 1] and k[i] < d[i]:
            events.append((int(formed[i]), -1))
    collapsed = []
    for bar, sign in events:
        if collapsed and collapsed[-1][0] == bar:
            if collapsed[-1][1] != sign:
                counter(kind, "mixed_sign_same_bar_collapsed")
            collapsed[-1] = (bar, sign)
        else:
            collapsed.append((bar, sign))
    return {"rsi": rsi, "raw": raw, "k": k, "d": d, "events": collapsed}


def exit_price_pnl(proceeds, basis):
    """Pure price PnL of one closing fill: exit proceeds minus cost basis.

    Contract 7.2 (v1.3.2, independent gross accounting): `gross_pnl` accumulates this value
    only - no fee, no funding - and is never reverse-derived from the net ledger.
    """
    return proceeds - basis


def pnl_decomposition_ok(m, tol=1e-3):
    """Fail-closed cross-check of the two independent accounting sources (contract 7.2)."""
    return abs(m["gross_pnl"] - m["fees"] - m["funding"] - m["net_pnl"]) <= tol


def episodes_for_window(events, i0, i1, delay, kind):
    """Registered signal events of the slice -> [(event_bar_local, entry_bar_local, sign)].

    The decision is taken at the forming bar's close, so the fill is the NEXT bar's open;
    `delay` (the registered 1-bar execution stress) pushes it one bar further.  Every slice is
    evaluated with a FLAT book at its first bar (registered: no position crosses a slice edge),
    and a signal whose entry bar would fall outside the slice is counted, not traded.
    """
    out = []
    for bar, sign in events:
        if bar < i0 or bar >= i1:
            continue
        entry = bar + 1 + delay
        if entry >= i1:
            counter(kind, "entry_clipped_at_slice_edge")
            continue
        out.append((bar - i0, entry - i0, sign))
    return out


def simulate(cohort, p, rail, window, stress, slip_ticks, kind, signals, diag=False,
             count_layers=True):
    """One pre-registered parameter case over one window slice (i0, i1).

    `stress` may carry: fee_mult / funding_mult / entry_delay_1_bar / slip_ticks / no_funding.
    Long positions walk the bar LOW first then the HIGH; short positions mirror that
    sym-directionally (HIGH first, then LOW).  `count_layers=False` keeps a diagnostic re-run
    out of the full-window DCA ladder histogram (the grid already counted that cell).
    """
    i0, i1 = window
    C = cohort.close[i0:i1].tolist()
    H = cohort.high[i0:i1].tolist()
    L = cohort.low[i0:i1].tolist()
    O = cohort.open[i0:i1].tolist()
    n = len(C)
    if n <= 0:
        raise SystemExit("empty window slice for %s %s" % (cohort.symbol, kind))

    case = case_tuple(p)
    if case not in CASE_ORDER:
        counter(kind, "case_not_registered")
        raise SystemExit("unregistered strategy case %r (registered: %r)" % (case, CASE_ORDER))
    delay = 1 if stress.get("entry_delay_1_bar") else 0
    key = (cohort.symbol, cohort.timeframe, case[0], case[1])
    signal = signals[key]
    episodes = episodes_for_window(signal["events"], i0, i1, delay, kind)
    signals_seen = len(episodes)

    tick = cohort.price_increment
    taf = cohort.taker_fee * stress.get("fee_mult", 1.0)
    lev = cohort.leverage
    d0 = rail["spacing_d0"]
    tp_pct = rail["tp"]
    inval = rail["invalidation"]
    mult = rail["size_multiplier"]
    base = rail["base_quote"]

    if stress.get("no_funding"):
        fund_times = np.array([], dtype=np.int64)
        fund_rates = np.array([], dtype=np.float64)
        settle_bar = np.array([], dtype=np.int64)
        settle_bar_closed = np.array([], dtype=np.int64)
    else:
        series = signal["funding"]
        fund_times = series["obs_times"]
        fund_rates = series["obs_rates"] * stress.get("funding_mult", 1.0)
        settle_bar = series["settle_bar"]
        settle_bar_closed = series.get("settle_bar_closed", series["settle_bar"])

    day_id = cohort.open_time_ms[i0:i1] // MS_PER_DAY
    _uniq, day_local = np.unique(day_id, return_inverse=True)
    day_local = day_local.astype(np.int64)
    n_days = int(day_local[-1]) + 1 if n else 0
    day_end = set(int(i) for i in np.flatnonzero(
        np.concatenate([day_local[1:] != day_local[:-1], [True]])))
    day_equity = [None] * n_days

    layers = [0] * 12
    realized = funding_paid = 0.0
    gross_pnl = 0.0
    fees_total = 0.0
    fills = 0
    turnover = 0.0
    signals_entered = tp_hits = stop_hits = flip_exits = slice_end_flats = 0
    open_at_end = margin_calls = 0
    bars_in_market = 0
    max_lev = 0.0
    util_sum = 0.0
    by_year = {}
    by_sign = {}
    halted = False
    min_entry_equity = START_EQUITY

    def charge_fee(amount):
        nonlocal realized, fees_total
        fees_total += amount
        realized -= amount
        return amount

    if not episodes:
        series_flat = _flat_series(day_equity)
        return _metrics(realized, fees_total, funding_paid, gross_pnl, 0, tp_hits, stop_hits,
                        flip_exits, slice_end_flats, open_at_end, margin_calls, halted,
                        min_entry_equity, series_flat, cohort, i0, i1, layers, max_lev, util_sum,
                        bars_in_market, fills, turnover, signals_seen, signals_entered, kind, {})

    state = {"sign": 0, "qty": 0.0, "cost": 0.0, "levels": None, "entry_ms": 0,
             "ep_gross": 0.0, "ep_fees": 0.0, "ep_fund": 0.0, "kptr": 0}

    def fund_drain(t_bar):
        """Charge every settlement scheduled at or before bar `t_bar` on the open notional."""
        while (state["kptr"] < len(fund_times)
               and int(settle_bar[state["kptr"]]) <= i0 + t_bar):
            state["ep_fund"] += (state["sign"] * state["qty"] * C[t_bar]
                                 * fund_rates[state["kptr"]])
            state["kptr"] += 1

    def close_position(bar, base_price, reason, exit_ms):
        """Reduce-only flatten of every layer at `base_price` (adverse slippage applied here)."""
        nonlocal realized, gross_pnl, funding_paid, fills, turnover, tp_hits, stop_hits
        nonlocal flip_exits, slice_end_flats, open_at_end, margin_calls
        sign = state["sign"]
        xpx = base_price - sign * slip_ticks * tick
        ep_gross = exit_price_pnl(sign * state["qty"] * xpx, sign * state["cost"])
        realized += sign * state["qty"] * xpx - sign * state["cost"]
        gross_pnl += ep_gross
        state["ep_gross"] = ep_gross
        charge_fee(state["qty"] * xpx * taf)
        state["ep_fees"] += state["qty"] * xpx * taf
        fills += 1
        turnover += state["qty"] * xpx
        if reason == "tp":
            tp_hits += 1
        elif reason == "stop":
            stop_hits += 1
        elif reason == "margin":
            margin_calls += 1
        elif reason == "flip":
            flip_exits += 1
        elif reason == "slice_end":
            if bar == n - 1:
                open_at_end += 1
            else:
                slice_end_flats += 1
        # every settlement inside the CLOSED exposure interval [entry - 1s, exit + 1s] is
        # charged on the notional that was at risk, at the exit price
        kend = int(np.searchsorted(fund_times, exit_ms + MS_JITTER_TOLERANCE_MS, side="right"))
        while state["kptr"] < kend:
            if int(settle_bar_closed[state["kptr"]]) > i0 + bar + 1:
                counter(kind, "funding_bar_out_of_hold")
            state["ep_fund"] += sign * state["qty"] * xpx * fund_rates[state["kptr"]]
            state["kptr"] += 1
        funding_paid += state["ep_fund"]
        realized -= state["ep_fund"]
        day_equity[day_local[bar]] = START_EQUITY + realized
        if diag:
            ep_net = state["ep_gross"] - state["ep_fees"] - state["ep_fund"]
            year = time.strftime("%Y", time.gmtime(cohort.open_time_ms[i0 + bar] / 1000.0))
            slot = by_year.setdefault(year, {"episodes": 0, "net_pnl": 0.0, "fees": 0.0,
                                             "funding": 0.0})
            slot["episodes"] += 1
            slot["net_pnl"] += ep_net
            slot["fees"] += state["ep_fees"]
            slot["funding"] += state["ep_fund"]
            slot = by_sign.setdefault("sign_%+d" % sign, {"episodes": 0, "net_pnl": 0.0})
            slot["episodes"] += 1
            slot["net_pnl"] += ep_net

    def walk(t_from, t_to):
        """Advance the open position over [t_from, t_to]; True when it closed in range."""
        nonlocal fills, turnover, bars_in_market, max_lev, util_sum
        sign = state["sign"]
        qty = state["qty"]
        cost = state["cost"]
        levels = state["levels"]
        for t in range(t_from, t_to + 1):
            fund_drain(t)
            l = L[t]
            h = H[t]
            o = O[t]
            killed_at = None
            if sign > 0:
                # Descending-price walk (worst case for a long: the bar LOW is reached first).
                # The invalidation is a RESTING stop at average_cost x (1 - invalidation), so a
                # ladder level below it cannot fill: whichever trigger is higher comes first.
                kk = 1
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
                    fpx = trig + slip_ticks * tick      # buying: adverse = pay up
                    q = base * (mult ** kk) * lev / fpx
                    qty += q
                    cost += q * fpx
                    charge_fee(q * fpx * taf)
                    state["ep_fees"] += q * fpx * taf
                    fills += 1
                    turnover += q * fpx
                    layers[kk] += 1
                    kk += 1
            else:
                # Ascending-price walk (mirror image): the bar HIGH is reached first.  Walking up
                # from the bar open, the next trigger is whichever of the next ladder level and
                # the resting stop is LOWER.
                kk = 1
                while True:
                    stop = (cost / qty) * (1.0 + inval)
                    if kk <= MAX_ADD_LEVELS and levels[kk] <= stop:
                        trig, is_stop = levels[kk], False
                    else:
                        trig, is_stop = stop, True
                    if h < trig:
                        break
                    if is_stop:
                        killed_at = trig if o <= trig else o
                        break
                    fpx = trig - slip_ticks * tick      # selling: adverse = sell lower
                    q = base * (mult ** kk) * lev / fpx
                    qty += q
                    cost += q * fpx
                    charge_fee(q * fpx * taf)
                    state["ep_fees"] += q * fpx * taf
                    fills += 1
                    turnover += q * fpx
                    layers[kk] += 1
                    kk += 1
            state["qty"] = qty
            state["cost"] = cost
            eq = START_EQUITY + realized
            ueq = eq + sign * (qty * C[t] - cost)
            if killed_at is None and ueq <= cohort.margin_maint * qty * C[t]:
                # capital-exhaustion backstop: forced full flatten at the bar close
                close_position(t, C[t], "margin",
                               int(cohort.open_time_ms[i0 + t]) + cohort.bar_ms)
                return True
            if ueq > 0:
                lv = (qty * C[t]) / ueq
                if lv > max_lev:
                    max_lev = lv
                util_sum += (cost / lev) / ueq
            bars_in_market += 1
            if t in day_end:
                day_equity[day_local[t]] = ueq
            if killed_at is not None:
                close_position(t, killed_at, "stop",
                               int(cohort.open_time_ms[i0 + t]) + cohort.bar_ms)
                return True
            tpx = (cost / qty) * (1.0 + sign * tp_pct)
            if (h >= tpx) if sign > 0 else (l <= tpx):
                close_position(t, tpx, "tp", int(cohort.open_time_ms[i0 + t]) + cohort.bar_ms)
                return True
        return False

    def open_position(entry, sign):
        nonlocal fills, turnover, signals_entered, min_entry_equity
        px = O[entry] + sign * slip_ticks * tick     # buying pays up, selling sells lower
        qty = base * lev / px
        cost = qty * px
        charge_fee(qty * px * taf)
        fills += 1
        turnover += qty * px
        state.update({"sign": sign, "qty": qty, "cost": cost,
                      "levels": [px * (1.0 - sign * d0 * kk) for kk in range(12)],
                      "entry_ms": int(cohort.open_time_ms[i0 + entry]),
                      "ep_gross": 0.0, "ep_fees": qty * px * taf, "ep_fund": 0.0})
        layers[0] += 1
        signals_entered += 1
        min_entry_equity = min(min_entry_equity, START_EQUITY + realized)
        state["kptr"] = int(np.searchsorted(
            fund_times, state["entry_ms"] - MS_JITTER_TOLERANCE_MS, side="left"))

    pos_open = False
    ptr = 0
    for _bar, entry, sign in episodes:
        if pos_open and ptr <= entry - 1:
            if walk(ptr, entry - 1):
                pos_open = False
        if START_EQUITY + realized <= 0.0:
            # registered capital-exhaustion semantics: no new position once realised equity is gone
            halted = True
            break
        if pos_open and state["sign"] == sign:
            counter(kind, "same_sign_signal_ignored")
            continue
        if pos_open:
            close_position(entry, O[entry], "flip", int(cohort.open_time_ms[i0 + entry]))
            pos_open = False
            if START_EQUITY + realized <= 0.0:
                # the flatten above is itself a fill: it can consume the last of the equity, so
                # the registered capital-exhaustion semantics ("no new episode is opened once
                # the realised equity is gone") must be re-checked before the mirrored side is
                # opened.  Without this the mirrored episode would open on an exhausted account.
                halted = True
                break
        open_position(entry, sign)
        pos_open = True
        ptr = entry
    if pos_open:
        if not walk(ptr, n - 1):
            close_position(n - 1, C[n - 1], "slice_end",
                           int(cohort.open_time_ms[i0 + n - 1]) + cohort.bar_ms)

    episodes_total = signals_entered
    # only GRID cells feed the full-window ladder histogram: the winner diagnostics re-run cells
    # the grid already counted, so they must not inflate the level counts.
    if kind in FULL_WINDOW_GRID_KINDS and count_layers:
        acc = LAYER_TOTALS.setdefault("full", [0] * 12)
        for kk in range(12):
            acc[kk] += layers[kk]

    series_flat = _flat_series(day_equity)
    diag_out = {}
    if diag:
        diag_out = {"pnl_by_year": {k: _round_dict(v) for k, v in sorted(by_year.items())},
                    "pnl_by_sign": {k: _round_dict(v) for k, v in sorted(by_sign.items())},
                    "daily_equity": [round(float(v), 6) for v in series_flat],
                    "daily_equity_days": int(len(series_flat)),
                    "slice_first_bar_utc": iso(int(cohort.open_time_ms[i0])),
                    "slice_last_bar_utc": iso(int(cohort.open_time_ms[i1 - 1]))}
    return _metrics(realized, fees_total, funding_paid, gross_pnl, episodes_total, tp_hits,
                    stop_hits, flip_exits, slice_end_flats, open_at_end, margin_calls, halted,
                    min_entry_equity, series_flat, cohort, i0, i1, layers, max_lev, util_sum,
                    bars_in_market, fills, turnover, signals_seen, signals_entered, kind,
                    diag_out)


def _round_dict(d):
    return {k: (round(v, 6) if isinstance(v, float) else v) for k, v in d.items()}


def _flat_series(day_equity):
    """Daily equity marks of the EVALUATED slice, forward-filled across flat stretches.

    Indexed by the slice's own day index (never the cohort's), so the series covers exactly the
    window being measured; a padded series would dilute Sharpe by sqrt(slice/cohort).
    """
    series = []
    last = START_EQUITY
    for v in day_equity:
        if v is not None:
            last = v
        series.append(last)
    return np.array(series, dtype=np.float64)


def _metrics(realized, fees_total, funding_paid, gross_pnl, episodes, tp_hits, stop_hits,
             flip_exits, slice_end_flats, open_at_end, margin_calls, halted, min_entry_equity,
             series, cohort, i0, i1, layers, max_lev, util_sum, bars_in_market, fills, turnover,
             signals_seen, signals_entered, kind, diag_out):
    if len(series) > 2:
        rets = np.diff(series) / series[:-1]
        sd = float(np.std(rets, ddof=1))
        sharpe = float(np.mean(rets) / sd * np.sqrt(365.0)) if sd > 0 else 0.0
    else:
        sharpe = 0.0
    peak = np.maximum.accumulate(series)
    dd = series - peak
    dd_pct = dd / peak
    span_days = (cohort.open_time_ms[i1 - 1] + cohort.bar_ms
                 - cohort.open_time_ms[i0]) / 86400000.0
    years = max(span_days / 365.25, 1.0 / 365.25)
    end_eq = START_EQUITY + realized
    return {
        "net_pnl": realized, "fees": fees_total, "funding": funding_paid,
        "gross_pnl": gross_pnl,
        "episodes": episodes, "tp_hits": tp_hits, "stop_hits": stop_hits,
        "flip_exits": flip_exits, "slice_end_flats": slice_end_flats,
        "open_at_end": open_at_end, "margin_calls": margin_calls,
        "halted": halted, "min_entry_equity": min_entry_equity,
        "ending_equity": end_eq, "sharpe": sharpe,
        "max_dd_usdt": float(dd.min()) if len(dd) else 0.0,
        "max_dd_pct": float(dd_pct.min()) if len(dd_pct) else 0.0,
        "max_effective_leverage": max_lev,
        "capital_utilization": (util_sum / bars_in_market) if bars_in_market else 0.0,
        "bars_in_market": bars_in_market, "days": int(len(series)), "years": years,
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
           "case_name": case_name(case_tuple(p)),
           "brick_pct": p["brick_pct"], "rsi_period": p["rsi_period"],
           "spacing_pct": dca["spacing_pct"], "size_multiplier": dca["size_multiplier"],
           "breakeven_tp_pct": dca["breakeven_tp_pct"],
           "invalidation_pct": dca["invalidation_pct"],
           "net_pnl": round(m["net_pnl"], 6), "fees": round(m["fees"], 6),
           "funding": round(m["funding"], 6), "gross_pnl": round(m["gross_pnl"], 6),
           "ending_equity": round(m["ending_equity"], 6),
           "signals_seen": m["signals_seen"], "signals_entered": m["signals_entered"],
           "episodes": m["episodes"], "tp_hits": m["tp_hits"], "stop_hits": m["stop_hits"],
           "flip_exits": m["flip_exits"], "slice_end_flats": m["slice_end_flats"],
           "open_at_end": m["open_at_end"], "margin_calls": m["margin_calls"],
           "halted": m["halted"], "min_entry_equity": round(m["min_entry_equity"], 6),
           "sharpe": round(m["sharpe"], 6),
           "max_dd_usdt": round(m["max_dd_usdt"], 6), "max_dd_pct": round(m["max_dd_pct"], 6),
           "max_effective_leverage": round(m["max_effective_leverage"], 6),
           "capital_utilization": round(m["capital_utilization"], 6),
           "bars_in_market": m["bars_in_market"], "fills": m["fills"],
           "turnover_usdt": round(m["turnover_usdt"], 6),
           "days": m["days"], "years": round(m["years"], 6), "cagr": m["cagr"],
           "total_return_pct": round(m["total_return_pct"], 6),
           "annualized_return": m["annualized_return"]}
    return {k: row[k] for k in ROW_FIELDS}


def run_cohort(spec, cohort, run_log, signals):
    """Every legal (strategy params x DCA config) case of the cohort, on every registered grid."""
    grid = spec["parameter_domain"]["grid_cases"]
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
                                         simulate(cohort, p, rail_for(dca), sl, {}, slip, kind,
                                                  signals)))

    sl = slices["full"]
    for sname, stress in STRESS:
        for p in grid:
            for dca in dca_grid:
                rows[sname].append(record(cohort, p, dca, sname,
                                          simulate(cohort, p, rail_for(dca), sl, stress,
                                                   stress.get("slip_ticks", slip), sname,
                                                   signals)))
    # G8 cost-attrition grid: 40 bps per fill (8 x the registered 5 bps taker fee).
    for p in grid:
        for dca in dca_grid:
            rows["cost_attrition_40bps"].append(record(
                cohort, p, dca, "cost_attrition_40bps",
                simulate(cohort, p, rail_for(dca), sl, COST_ATTRITION_STRESS, slip,
                         "cost_attrition_40bps", signals)))
    # funding-baseline diagnostics run on the SAME windows as their counterparts
    for p in grid:
        for dca in dca_grid:
            rows["no_funding"].append(record(cohort, p, dca, "no_funding",
                                             simulate(cohort, p, rail_for(dca),
                                                      slices["historical"], {"no_funding": True},
                                                      slip, "no_funding", signals)))
            rows["no_funding_full"].append(record(cohort, p, dca, "no_funding_full",
                                                  simulate(cohort, p, rail_for(dca), sl,
                                                           {"no_funding": True}, slip,
                                                           "no_funding_full", signals)))
    return rows


# ---------------------------------------------------------------------------
# phase 3: pre-registered cohort-selector / cohort-survivor gate
#          (contract sections 7.2 and 7.3; no post-hoc tuning)
# ---------------------------------------------------------------------------

def axis_values(spec):
    """The registered value list of every joint-space axis, in the registered order."""
    return {"brick_pct": sorted({g["brick_pct"] for g in spec["parameter_domain"]["grid_cases"]}),
            "rsi_period": sorted({g["rsi_period"]
                                  for g in spec["parameter_domain"]["grid_cases"]}),
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
    """Selection and neighbourhood judgement may never read OOS (contract 7.3)."""
    bad = [r for r in rows if r.get("window_kind") not in allowed]
    if bad:
        raise ValueError("%s must be given %r rows only (contract 7.3): %d violation(s)"
                         % (where, allowed, len(bad)))


def select_cohort_winner(hist_rows, spec, allowed=("historical",)):
    """One deterministic winner per cohort, historical window only.

    Requirements, in order: (1) the cohort's best case must reach `min_episodes_is` historical
    episodes (the registered no-signal floor of this family), otherwise the whole cohort is
    culled for insufficient trades; (2) a candidate needs net_pnl > 0 AND sharpe > 0;
    (3) ranking is Sharpe desc, net_pnl desc, then the registered-index lexical key of the
    joint parameter cell.
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
    """Face-adjacent (+-1 registered step on exactly one axis) sign agreement.

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


def cell_key_row(winner):
    """The frozen winner dict carries exactly the registered parameter axes."""
    return tuple(winner[a] for a in AXES)


def evaluate_cohort(spec, cohort_label, rows):
    """The whole cohort decision for one (symbol, timeframe) cohort."""
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
    out["winner"] = _pick(winner, DCA_AXES)
    out["winner"].update({f: winner[f] for f in CASE_FIELDS})
    # NOTE: the winner cell must carry ONLY registered parameter axes - the survivor
    # index / evidence readers refuse any key outside strategy_param_fields + dca_param_fields.
    out["winner_case_label"] = case_name(case_tuple(winner))
    out["metrics"] = {
        "historical": _pick(winner, WINNER_METRIC_KEYS),
        "oos": _pick(oos, WINNER_METRIC_KEYS),
        "full": _pick(full, WINNER_METRIC_KEYS),
        "robustness": {s: _pick(stress[s], WINNER_METRIC_KEYS) for s in stress},
        "no_funding_reference": _pick(same_cell(rows["no_funding"], key), WINNER_METRIC_KEYS),
        "no_funding_full_reference": _pick(same_cell(rows["no_funding_full"], key),
                                           WINNER_METRIC_KEYS),
        "cost_attrition_40bps": _pick(cost_attrition, WINNER_METRIC_KEYS),
    }
    out["neighbourhood"] = nb
    reasons = []
    gates = spec["gates"]
    if oos["episodes"] < gates["min_episodes_oos"]:
        reasons.append("insufficient_trades")
    if not (oos["net_pnl"] > 0.0 and oos["sharpe"] > 0.0):
        reasons.append("oos_economic")
    if not (full["net_pnl"] > 0.0):
        reasons.append("full_economic")
    failing_stress = [s for s, _ in STRESS if not (stress[s]["net_pnl"] > 0.0)]
    if failing_stress:
        reasons.append("robustness_economic:" + ",".join(failing_stress))
    # G8: the registered 40 bps-per-fill attrition grid is a cohort gate, and its reason stays
    # inside the registered `robustness_economic:<grids>` vocabulary.
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
    band = "NO_SURVIVOR" if not survivors else ("SURVIVOR_FOUND" if len(survivors) == 1
                                               else "MULTIPLE_SURVIVORS")
    verdict = "PASS" if survivors else "REJECT"
    return {"disposition": "SURVIVOR" if survivors else "REJECT", "band": band,
            "verdict_recommendation": verdict,
            "performance_claimable_recommendation": bool(survivors),
            "mapping_version": CONTRACT_SEMANTICS_VERSION}


# ---------------- registered family-level falsification readers (never cull a cohort) -------
def cost_boundary_check(spec, cohort_results):
    """Registered family-level reader: a winner whose full-window net-PnL SIGN flips between the
    canonical cost track and the registered 40 bps-per-fill attrition track.  A hit is never
    recorded as PASS; the evidence decides REJECT vs DEFERRED."""
    hits = []
    for c in cohort_results:
        if c["winner"] is None:
            continue
        canonical = c["metrics"]["full"]["net_pnl"]
        attrition = c["metrics"]["cost_attrition_40bps"]["net_pnl"]
        if canonical is not None and attrition is not None and (canonical > 0.0) != (attrition > 0.0):
            hits.append(c["cohort"])
    return {"registered_item": "cost-boundary", "evaluated": True, "hit": bool(hits),
            "cohorts": hits,
            "definition": "full-window net-PnL sign differs between the canonical cost track "
                          "and cost_attrition_40bps",
            "landing": "family-level: a hit must NEVER be recorded as PASS"}


def symbol_concentration_check(spec, cohort_results):
    """Registered family-level reader: among the survivors, one SYMBOL's share of the total
    positive OOS net PnL exceeds the registered 70% band.  Descriptive disclosure: a hit is
    never recorded as PASS."""
    survivors = [c for c in cohort_results if c["outcome"] == "SURVIVOR"]
    pos = [(c["cohort"].split("/")[0], c["metrics"]["oos"]["net_pnl"]) for c in survivors
           if c["metrics"]["oos"]["net_pnl"] and c["metrics"]["oos"]["net_pnl"] > 0]
    total = sum(v for _s, v in pos)
    shares = {}
    for s, v in pos:
        shares[s] = shares.get(s, 0.0) + v
    shares = {k: (v / total) for k, v in shares.items()} if total > 0 else {}
    top = max(shares.items(), key=lambda kv: kv[1]) if shares else (None, 0.0)
    hit = bool(shares) and top[1] > 0.70
    return {"registered_item": "symbol-concentration", "evaluated": bool(survivors), "hit": hit,
            "top_symbol": top[0], "top_share": round(float(top[1]), 6),
            "shares": {k: round(v, 6) for k, v in sorted(shares.items())},
            "threshold": 0.70,
            "landing": "family-level robustness disclosure: a hit must NEVER be recorded as PASS"}


def oos_grid_instability_check(spec, cohort_results, rows_by_cohort):
    """Registered family-level reader: an elected winner's legal face-adjacent neighbours
    disagree with the winner's OOS net-PnL sign on 40% or more of the neighbours.  The OOS arm
    is evaluated AFTER selection (it never selects anything).  A hit is never recorded as PASS."""
    axes = axis_values(spec)
    hits, detail = [], {}
    for c in cohort_results:
        if c["winner"] is None:
            continue
        idx = {cell_key(r): r for r in rows_by_cohort[c["cohort"]]["oos"]}
        wkey = cell_key_row(c["winner"])
        wsign = c["metrics"]["oos"]["net_pnl"] > 0.0
        neigh = []
        for ai, axis in enumerate(AXES):
            vals = axes[axis]
            pos = vals.index(wkey[ai])
            for step in (-1, 1):
                npos = pos + step
                if not (0 <= npos < len(vals)):
                    continue
                nkey = list(wkey)
                nkey[ai] = vals[npos]
                r = idx.get(tuple(nkey))
                if r is not None:
                    neigh.append(r)
        if not neigh:
            continue
        disagree = sum(1 for r in neigh if (r["net_pnl"] > 0.0) != wsign)
        frac = disagree / float(len(neigh))
        detail[c["cohort"]] = {"neighbours": len(neigh), "disagreeing": disagree,
                               "disagree_fraction": round(frac, 6)}
        if frac >= 0.40:
            hits.append(c["cohort"])
    return {"registered_item": "oos-grid-instability", "evaluated": True, "hit": bool(hits),
            "cohorts": hits, "detail": detail, "threshold": 0.40,
            "landing": "family-level disclosure of the registered grid-instability item (the "
                       "historical arm is the G7 gate); a hit must NEVER be recorded as PASS"}


def track_effectiveness(grid_rows):
    """The registered cost/funding tracks must actually move the aggregate, or they are no-ops.

    Detectors read AGGREGATE SUMS (a median saturates at exactly 0.0 in a sparse grid and would
    report a working track as a no-op) and fail closed when nothing traded at all.
    """
    def s(kind, key):
        return float(sum(r[key] for r in grid_rows.get(kind, [])))
    traded = sum(r["fills"] for r in grid_rows.get("full", []))
    if traded <= 0:
        return {"evaluated": False,
                "note": "no fill at all: an unexercised track is indistinguishable from a no-op one",
                "fee_2x_fees_delta": 0.0, "funding_2x_funding_delta": 0.0,
                "cost_attrition_fees_delta": 0.0, "no_funding_funding_delta": 0.0,
                "entry_delay_net_delta": 0.0, "slippage_net_delta": 0.0, "aggregate_fills": 0}
    return {
        "evaluated": True,
        "fee_2x_fees_delta": s("fee_2x", "fees") - s("full", "fees"),
        "funding_2x_funding_delta": abs(s("funding_2x", "funding")) - abs(s("full", "funding")),
        "cost_attrition_fees_delta": s("cost_attrition_40bps", "fees") - s("full", "fees"),
        "no_funding_funding_delta": abs(s("no_funding_full", "funding")),
        "entry_delay_net_delta": s("entry_delay_1_bar", "net_pnl") - s("full", "net_pnl"),
        "slippage_net_delta": s("slippage_2ticks", "net_pnl") - s("full", "net_pnl"),
        "aggregate_fills": traded}


def summarize(spec, grid_rows, layers, diag_inputs, slice_days):
    import random
    import statistics as st
    expected_axes = axis_values(spec)
    need = {k: spec["expected"]["case_evaluations_per_grid"] for k in COHORT_GRID_KINDS}
    coverage = {k: len(grid_rows.get(k, [])) for k in need}
    full = grid_rows["full"]
    cohorts = sorted({(r["symbol"], r["timeframe"]) for r in full})
    cohort_labels = ["%s/%s" % c for c in cohorts]

    per_cohort = {label: {k: [r for r in grid_rows[k]
                              if "%s/%s" % (r["symbol"], r["timeframe"]) == label]
                          for k in COHORT_GRID_KINDS} for label in cohort_labels}

    strategy_product = set(case_tuple(g) for g in spec["parameter_domain"]["grid_cases"])
    dca_product = set((a, b, c, d) for a in expected_axes["spacing_pct"]
                      for b in expected_axes["size_multiplier"]
                      for c in expected_axes["breakeven_tp_pct"]
                      for d in expected_axes["invalidation_pct"])
    strategy_cells, dca_cells = set(), set()
    for r in full:
        strategy_cells.add(case_tuple(r))
        dca_cells.add(tuple(r[a] for a in DCA_AXES))
    cells_per_cohort_ok = all(len(per_cohort[c][k])
                              == spec["expected"]["base_combinations_per_cohort"]
                              for c in cohort_labels for k in COHORT_GRID_KINDS) if cohort_labels else False
    coverage_complete = (all(coverage[k] == need[k] for k in need)
                         and len(cohorts) == spec["expected"]["cohorts"]
                         and strategy_cells == strategy_product
                         and dca_cells == dca_product
                         and cells_per_cohort_ok)

    # An incomplete measurement is never judged: if any registered grid or cohort cell is
    # missing, no cohort is evaluated at all and the family lands on TECHNICAL_INCOMPLETE.
    cohort_results = ([evaluate_cohort(spec, label, per_cohort[label])
                       for label in cohort_labels] if coverage_complete else [])
    survivors = [c for c in cohort_results if c["outcome"] == "SURVIVOR"]
    disposition = family_disposition(survivors, coverage_complete)

    # deterministic selector: a shuffled copy of the same rows must elect the same cell
    rng = random.Random(20260916)
    selector_stable = True
    for c in cohort_results:
        if c["no_winner_reason"] is not None:
            continue
        shuffled = list(per_cohort[c["cohort"]]["historical"])
        rng.shuffle(shuffled)
        again, _ = select_cohort_winner(shuffled, spec)
        if again is None or cell_key(again) != cell_key_row(c["winner"]):
            selector_stable = False
            break

    # ---- per-cohort winner diagnostics (per-year, per-direction, winner cell re-run)
    diag_by_cohort = {}
    robustness_diagnostics = {
        "non_gating": True,
        "note": "per-calendar-year and per-direction net PnL of the elected winner cell; "
                "descriptive only - the three family-level falsification readers are the only "
                "gated readers",
        "registered_item": "sub_period_stability_by_direction"}
    if coverage_complete:
        for c in cohort_results:
            if c["winner"] is None:
                continue
            cohort, signals = diag_inputs[c["cohort"]]
            slip0 = spec["costs"]["baseline_slippage_ticks"]
            full_slice = cohort.slice(spec["data"]["start"], spec["data"]["end"])
            dca = {a: c["winner"][a] for a in DCA_AXES}
            dca["base_quote"] = spec["dca_domain"]["base_quote"]
            p_win = params_of(c["winner"])
            m_win = simulate(cohort, p_win, rail_for(dca), full_slice, {}, slip0, "full",
                             signals, diag=True, count_layers=False)
            row = record(cohort, p_win, dca, "full", m_win)
            grid_row = same_cell(per_cohort[c["cohort"]]["full"], cell_key_row(c["winner"]))
            diag_by_cohort[c["cohort"]] = {"winner_full": m_win, "row": row}
            robustness_diagnostics.setdefault("cohorts", {})[c["cohort"]] = {
                "winner_cell": c["winner"],
                "pnl_by_year": m_win["diagnostics"]["pnl_by_year"],
                "pnl_by_direction": m_win["diagnostics"]["pnl_by_sign"],
                "winner_diagnostics_match_full_row": all(
                    abs(float(row[k]) - float(grid_row[k])) < 1e-6
                    for k in ROW_FIELDS
                    if isinstance(grid_row[k], (int, float)) and not isinstance(grid_row[k], bool)
                    and isinstance(row[k], (int, float))),
                "daily_series_days": m_win["diagnostics"]["daily_equity_days"]}

    rows_by_cohort = {label: per_cohort[label] for label in cohort_labels}
    cost_boundary = (cost_boundary_check(spec, cohort_results) if coverage_complete else
                     {"registered_item": "cost-boundary", "evaluated": False, "hit": False,
                      "landing": "family-level: a hit must NEVER be recorded as PASS"})
    concentration = (symbol_concentration_check(spec, cohort_results) if coverage_complete else
                     {"registered_item": "symbol-concentration", "evaluated": False, "hit": False,
                      "landing": "family-level: a hit must NEVER be recorded as PASS"})
    instability = (oos_grid_instability_check(spec, cohort_results, rows_by_cohort)
                   if coverage_complete else
                   {"registered_item": "oos-grid-instability", "evaluated": False, "hit": False,
                    "landing": "family-level: a hit must NEVER be recorded as PASS"})
    flags = {"cost_boundary": bool(cost_boundary.get("hit")),
             "symbol_concentration": bool(concentration.get("hit")),
             "oos_grid_instability": bool(instability.get("hit"))}

    # registered landing of the family-level falsification readers: none may be recorded as PASS
    final_verdict = disposition["verdict_recommendation"]
    final_claimable = disposition["performance_claimable_recommendation"]
    if final_verdict == "PASS" and any(flags.values()):
        final_verdict = "DEFERRED"
        final_claimable = False

    def med(rows, key):
        return st.median([r[key] for r in rows if r.get(key) is not None] or [0.0])

    descriptive = {
        "non_gating": True,
        "note": "every cohort is judged independently (contract 7.3); the cross-cohort numbers "
                "below are descriptive diagnostics and are never a gate (contract 7.2)",
        "cohort_count": len(cohort_labels),
        "median_full_net_pnl": med(full, "net_pnl"),
        "median_oos_net_pnl": med(grid_rows["oos"], "net_pnl"),
        "median_historical_net_pnl": med(grid_rows["historical"], "net_pnl"),
        "positive_case_share_historical": (
            sum(1 for r in grid_rows["historical"] if r["net_pnl"] > 0)
            / float(len(grid_rows["historical"]))) if grid_rows["historical"] else 0.0,
        "positive_case_share_full": (sum(1 for r in full if r["net_pnl"] > 0)
                                     / float(len(full))) if full else 0.0,
        "cohort_outcomes": [{"cohort": label, "winner_case": c.get("winner_case_label"),
                             "outcome": c["outcome"]}
                            for label, c in zip(cohort_labels, cohort_results)],
    }
    stress_summary = {s: {"median_net_pnl": med(grid_rows[s], "net_pnl"),
                          "median_sharpe": med(grid_rows[s], "sharpe"),
                          "cases": len(grid_rows[s]), "non_gating": True} for s, _ in STRESS}
    for extra in ("cost_attrition_40bps", "no_funding", "no_funding_full"):
        stress_summary[extra] = {"median_net_pnl": med(grid_rows[extra], "net_pnl"),
                                 "median_sharpe": med(grid_rows[extra], "sharpe"),
                                 "cases": len(grid_rows[extra]), "non_gating": True}

    keys = ("net_pnl", "fees", "funding", "gross_pnl", "ending_equity", "sharpe",
            "max_dd_pct", "max_dd_usdt", "max_effective_leverage", "capital_utilization",
            "cagr", "total_return_pct", "episodes", "fills", "turnover_usdt")
    tracks = track_effectiveness(grid_rows)
    tracks["fee_2x_is_not_a_noop"] = tracks["fee_2x_fees_delta"] > 0.0
    tracks["funding_2x_is_not_a_noop"] = tracks["funding_2x_funding_delta"] > 0.0
    tracks["cost_attrition_is_not_a_noop"] = tracks["cost_attrition_fees_delta"] > 0.0
    tracks["no_funding_is_cost_free_track"] = tracks["no_funding_funding_delta"] == 0.0
    tracks["entry_delay_is_not_a_noop"] = tracks["entry_delay_net_delta"] != 0.0
    tracks["slippage_is_not_a_noop"] = tracks["slippage_net_delta"] != 0.0
    assertions = {
        "episodes_partition": all(
            r["episodes"] == r["tp_hits"] + r["stop_hits"] + r["flip_exits"]
            + r["slice_end_flats"] + r["open_at_end"] + r["margin_calls"] for r in full),
        "pnl_decomposition": all(pnl_decomposition_ok(r) for r in full),
        "coverage_complete": coverage_complete,
        "cohort_count_matches_registered": len(cohorts) == spec["expected"]["cohorts"],
        "strategy_grid_is_registered_product": strategy_cells == strategy_product,
        "dca_grid_is_registered_product": dca_cells == dca_product,
        "base_combinations_per_cohort_per_grid": cells_per_cohort_ok,
        "expected_case_evaluations": (sum(coverage.values())
                                      == spec["expected"]["expected_case_evaluations"]),
        "layer0_equals_episodes": (layers[0] == sum(
            r["episodes"] for k in FULL_WINDOW_GRID_KINDS for r in grid_rows.get(k, []))
            and layers[0] > 0),
        "layer_histogram_nonempty": sum(layers) > 0,
        "no_entry_after_exhaustion": all(r["min_entry_equity"] > 0.0 for r in full),
        "ending_equity_floor": all(r["ending_equity"] > -1.5 * START_EQUITY for r in full),
        "selector_deterministic": selector_stable,
        "selector_historical_only": True,
        # executable causality / accounting guards (module counters + track deltas)
        "bar_grid_is_strictly_ascending": counters_total("bar_grid_not_ascending") == 0,
        "no_unregistered_strategy_case": counters_total("case_not_registered") == 0,
        "brick_cap_never_reached": counters_total("brick_cap_reached") == 0,
        "funding_bar_never_out_of_hold": counters_total("funding_bar_out_of_hold") == 0,
        "no_funding_grid_is_cost_free": all(r["funding"] == 0.0 for r in grid_rows["no_funding"]),
        "fee_2x_track_is_not_a_noop": tracks["fee_2x_is_not_a_noop"],
        "funding_2x_track_is_not_a_noop": tracks["funding_2x_is_not_a_noop"],
        "cost_attrition_track_is_not_a_noop": tracks["cost_attrition_is_not_a_noop"],
        "no_funding_track_is_cost_free": tracks["no_funding_is_cost_free_track"],
        "entry_delay_track_is_not_a_noop": tracks["entry_delay_is_not_a_noop"],
        "slippage_track_is_not_a_noop": tracks["slippage_is_not_a_noop"],
        "daily_series_is_slice_scoped": all(
            {r["days"] for r in per_cohort[label][k]} == {slice_days[label][k]}
            for label in cohort_labels for k in ("historical", "oos", "full")
        ) if coverage_complete else False,
    }
    return {
        "family_id": spec["family_id"], "round_id": spec["round_id"], "run_id": spec["run_id"],
        "engine_version": ENGINE_VERSION, "engine_semantics": ENGINE_SEMANTICS,
        "selector_version": SELECTOR_VERSION, "disposition_version": DISPOSITION_VERSION,
        "contract_semantics_version": CONTRACT_SEMANTICS_VERSION,
        "registered_domains": {
            "strategy": {"grid_cases": [list(t) for t in CASE_ORDER],
                         "case_names": list(CASE_NAMES),
                         "renko_contract": {
                             "mechanism": "geometric bricks: the brick threshold is brick_pct of "
                                          "the current reference price",
                             "stamp": "the forming source bar's close (conservative)",
                             "same_bar_tie_break": "close >= open -> up first",
                             "max_bricks_per_bar": MAX_BRICKS_PER_BAR},
                         "indicator_contract": {"stoch_period": STOCH_PERIOD,
                                                "k_smooth": K_SMOOTH, "d_smooth": D_SMOOTH}},
            "dca": {a: expected_axes[a] for a in DCA_AXES},
            "dca_base_quote": spec["dca_domain"]["base_quote"]},
        "coverage": coverage, "coverage_required": need, "coverage_complete": coverage_complete,
        "cohorts": cohort_labels, "cohort_count": len(cohorts),
        "slice_days": slice_days,
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
        "disposition_band": disposition.get("band"),
        "verdict_recommendation": disposition["verdict_recommendation"],
        "performance_claimable_recommendation": disposition["performance_claimable_recommendation"],
        "disposition_mapping_version": disposition["mapping_version"],
        "registered_family_level_falsification_flags": flags,
        "verdict_recommendation_final": final_verdict,
        "performance_claimable_recommendation_final": final_claimable,
        "survivor_evidence": survivors,
        "descriptive_diagnostics": descriptive,
        "descriptive_medians_all_base_cases": {k: med(full, k) for k in keys},
        "stress_summary": stress_summary,
        "cost_track_effectiveness": tracks,
        "dca_layer_histogram": {"level_%02d" % k: layers[k] for k in range(12)},
        "cost_boundary": cost_boundary,
        "symbol_concentration": concentration,
        "oos_grid_instability": instability,
        "robustness_diagnostics": robustness_diagnostics,
        "assertions": assertions,
        "structural_counters": {k: dict(v) for k, v in sorted(COUNTERS.items())},
        "structural_counter_tolerance_ms": MS_JITTER_TOLERANCE_MS,
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) != 2:
        sys.stderr.write("usage: 60_strategy_f_run.py <run-spec.json>\n")
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
                raise SystemExit("run-spec is not a Strategy F spec: missing %r (contract 7.2/7.3)"
                                 % key)
        if (spec["selector_version"] != SELECTOR_VERSION
                or spec["disposition_version"] != DISPOSITION_VERSION):
            raise SystemExit("run-spec selector/disposition version %r/%r != engine %r/%r"
                             % (spec["selector_version"], spec["disposition_version"],
                                SELECTOR_VERSION, DISPOSITION_VERSION))
        declared_cases = [case_tuple(g) for g in spec["parameter_domain"]["grid_cases"]]
        if declared_cases != list(CASE_ORDER):
            raise SystemExit("run-spec grid_cases %r != engine registered case order %r"
                             % (declared_cases, CASE_ORDER))
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
        diag_inputs = {}
        slice_days = {}
        total = len(spec["data"]["symbols"]) * len(spec["data"]["timeframes"])
        done = 0
        funding_report = {}
        renko_report = {}
        bricks_by_pct = {g["brick_pct"] for g in spec["parameter_domain"]["grid_cases"]}
        for symbol in spec["data"]["symbols"]:
            for tf in spec["data"]["timeframes"]:
                cohort = Cohort(symbol, tf, spec["data"]["start"], spec["data"]["end"])
                meta = instruments[symbol]
                cohort.price_increment = meta["price_increment"]
                cohort.taker_fee = meta["taker_fee"]
                cohort.leverage = 1.0 / meta["margin_init"]
                cohort.margin_maint = meta["margin_maint"]
                ft, fr, counts = load_funding_series(symbol, spec["data"]["start"],
                                                     spec["data"]["end"])
                series = build_funding_index(cohort, ft, fr, counts, "full")
                label = "%s/%s" % (symbol, tf["raw_interval"])
                # The brick series is built once per (cohort, brick_pct) and the indicator once
                # per (cohort, brick_pct, rsi_period): the 48 DCA configs and the 10 phase grids
                # of a cell all share the same signal.
                signals = {}
                for brick_pct in sorted(bricks_by_pct):
                    brick_close, formed = renko_bricks(brick_pct, cohort.close, label)
                    renko_report["%s/brick_pct=%s" % (label, brick_pct)] = {
                        "bricks": int(len(brick_close)),
                        "first_brick_utc": (iso(int(cohort.open_time_ms[int(formed[0])]))
                                            if len(formed) else None),
                        "last_brick_utc": (iso(int(cohort.open_time_ms[int(formed[-1])]))
                                           if len(formed) else None)}
                    for case in spec["parameter_domain"]["grid_cases"]:
                        if case["brick_pct"] != brick_pct:
                            continue
                        ind = indicator_events(brick_close, formed, case["rsi_period"], label)
                        ind["funding"] = series
                        signals[(symbol, tf["raw_interval"], brick_pct, case["rsi_period"])] = ind
                        renko_report["%s/brick_pct=%s/rsi=%s"
                                     % (label, brick_pct, case["rsi_period"])] = {
                            "bricks": int(len(brick_close)),
                            "events": int(len(ind["events"])),
                            "events_up": int(sum(1 for _b, s in ind["events"] if s > 0)),
                            "events_down": int(sum(1 for _b, s in ind["events"] if s < 0)),
                            "first_event_utc": (iso(int(cohort.open_time_ms[ind["events"][0][0]]))
                                                if ind["events"] else None),
                            "last_event_utc": (iso(int(cohort.open_time_ms[ind["events"][-1][0]]))
                                               if ind["events"] else None)}
                diag_inputs[label] = (cohort, signals)
                slice_days[label] = {}
                for kind, (a, b) in (("historical", (spec["data"]["historical_start"],
                                                     spec["data"]["historical_end"])),
                                     ("oos", (spec["data"]["oos_start"],
                                              spec["data"]["oos_end"])),
                                     ("full", (spec["data"]["start"], spec["data"]["end"]))):
                    i0, i1 = cohort.slice(a, b)
                    slice_days[label][kind] = int(len(np.unique(
                        cohort.open_time_ms[i0:i1] // MS_PER_DAY)))
                funding_report[label] = {
                    "observations": int(len(ft)),
                    "first": iso(int(ft[0])) if len(ft) else None,
                    "last": iso(int(ft[-1])) if len(ft) else None,
                    "truth_status_counts": counts,
                    "settlement_hours_utc": sorted(set(
                        time.strftime("%H:%M", time.gmtime(int(t) / 1000.0)) for t in ft)),
                    "settlement_exposure_note": spec["costs"]["funding_exposure_rule"]}
                run_log("cohort %s bars=%d days=%d funding_obs=%d modeled=%d official=%d"
                        % (label, cohort.n, cohort.n, len(ft), counts["modeled_funding"],
                           counts["official"]))
                for k, v in run_cohort(spec, cohort, run_log, signals).items():
                    grid_rows.setdefault(k, []).extend(v)
                done += 1
                atomic_write_json(os.path.join(attempt_dir, "artifacts", "progress.json"),
                                  {"cohorts_done": done, "cohorts_total": total,
                                   "updated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                                   time.gmtime())})
        atomic_write_json(os.path.join(attempt_dir, "artifacts", "funding_series.json"),
                          funding_report)
        atomic_write_json(os.path.join(attempt_dir, "artifacts", "renko_signals.json"),
                          renko_report)

        for kind, rows in grid_rows.items():
            path = os.path.join(attempt_dir, "artifacts", "grid_%s.csv" % kind)
            with open(path, "w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=list(ROW_FIELDS))
                w.writeheader()
                w.writerows(rows)
            run_log("wrote %s (%d rows)" % (os.path.basename(path), len(rows)))

        layers = LAYER_TOTALS.setdefault("full", [0] * 12)
        summary = summarize(spec, grid_rows, layers, diag_inputs, slice_days)
        summary["runtime_seconds"] = int(time.time() - started)
        summary["bins_build"] = {k: bins[k] for k in
                                 ("build_wall_seconds", "qlib_dir_bytes", "qlib_version")}
        summary["instrument_metadata"] = {s: instruments[s] for s in spec["data"]["symbols"]}
        summary["data_readback"] = bins["readback"]
        summary["funding_series"] = funding_report
        summary["renko_signals"] = renko_report
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
        atomic_write_json(os.path.join(attempt_dir, "artifacts", "family_falsification.json"),
                          {"cost_boundary": summary["cost_boundary"],
                           "symbol_concentration": summary["symbol_concentration"],
                           "oos_grid_instability": summary["oos_grid_instability"],
                           "cost_track_effectiveness": summary["cost_track_effectiveness"],
                           "flags": summary["registered_family_level_falsification_flags"]})
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
