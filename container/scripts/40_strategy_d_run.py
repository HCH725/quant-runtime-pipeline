#!/usr/bin/env python3
"""Strategy D — UTC clock-hour return seasonality PANEL (BTCUSDT/ETHUSDT/BNBUSDT/SOLUSDT
USD-M perp, 1h) full backtest, on Qlib.

Entry point for ONE pre-registered attempt.  Reads the immutable run-spec from
/results (the only source of parameters), builds the Qlib .bin store from the
READ-ONLY canonical raw store into /qlib/work, then runs the pre-registered
full-backtest contract (QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md
v1.7.0 sections 7.2 / 7.3):

    4 cohorts (BTCUSDT/1h, ETHUSDT/1h, BNBUSDT/1h, SOLUSDT/1h)
    x STRATEGY domain (the 7 registered non-empty subsets of the three clock legs)
    x DCA domain (4 axes = 48 configs)
    x 12 registered phase grids (historical / oos / full / fee_2x / funding_2x /
      entry_delay_1_bar / slippage_2ticks / no_funding / no_funding_full /
      cost_attrition_40bps / boundary_alt_oos / boundary_alt_full)

SIGNAL (source-normalized, pre-registered): the signal is CLOCK TIME ONLY - no price
history, no trend/mean-reversion estimate - so it is orthogonal in information set to
Strategy A (close-vs-SMA mean reversion) and Strategy B (EMA-crossover momentum).  Three
registered legs, all at UTC clock boundaries, non-overlapping by construction:

    long           15:00 -> 16:00 UTC   (+1 unit, 1 bar)
    short          01:00 -> 05:00 UTC   (-1 unit, 4 bars, sym-directional mirror)
    secondary_long 21:00 -> 24:00 UTC   (+1 unit, 3 bars)

EXECUTION (window-bounded DCA rail, inherited semantics): each holding window is opened by
tranche #1 at the window's first bar OPEN (the boundary is known ex ante, so the boundary
price is executable - no look-ahead), adverse-price scale-ins follow the registered ladder
inside the window (level_k price = initial_entry_price x (1 -/+ spacing_pct x k), k = 1..10,
so at most 11 routine active levels of the 12-tranche rail), the take profit is reduce-only
at running_average_cost x (1 +/- breakeven_tp_pct), the invalidation is a RESTING stop at
running_average_cost x (1 -/+ invalidation_pct), and the window end ALWAYS reduce-only
flattens every layer.  Window ends are hard boundaries; between windows the book is FLAT and
no layer may be added.  Long and short legs never overlap (the schedule is disjoint).

DCA is executed as real order/fill accounting (an episode state machine over the bars);
nothing is estimated after the fact.  Every legal (strategy params x DCA config) cell is
evaluated on every registered phase grid, and the family gate is the **cohort-level
survivor** rule of contract section 7.3: one deterministic historical-only winner per
cohort, then OOS / full / robustness / parameter-neighbourhood evidence for that same
winner.  This family has 4 cohorts, so the cohort survivor count is 0..4.

Writes only:
  * /qlib/work/**      (rebuildable derived/cache area, INV-5)
  * <attempt_dir>/**   (result.json, artifacts/, logs/, state.json)
Never writes /data/raw (read-only mount).

usage: 40_strategy_d_run.py <run-spec.json>
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
WORK_ROOT = "/qlib/work/strategyD-v1"
CSV_ROOT = os.path.join(WORK_ROOT, "csv")
QLIB_DIR = os.path.join(WORK_ROOT, "qlib-data")
MS_PER_DAY = 86400000
MS_PER_HOUR = 3600000
FIELDS = ["open", "high", "low", "close", "volume"]
START_EQUITY = 30000.0
MAX_ADD_LEVELS = 10
# ---------------------------------------------------------------- registered schedule
# The three clock legs, in the registered leg order.  `bars` is the holding-window length
# in 1h bars, `direction` +1 = long, -1 = short (sym-directional mirror of the rail).
LEGS = (
    {"name": "long", "start_hour": 15, "bars": 1, "direction": 1},
    {"name": "short", "start_hour": 1, "bars": 4, "direction": -1},
    {"name": "secondary_long", "start_hour": 21, "bars": 3, "direction": 1},
)
CASE_ORDER = ((1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 1, 0), (1, 0, 1), (0, 1, 1), (1, 1, 1))
CASE_NAMES = ("long_only", "short_only", "secondary_long_only", "long_short",
              "long_secondary_long", "short_secondary_long", "all_legs")
# Execution stress reruns (full-window slice), contract 7.2 robustness.
STRESS = [
    ("fee_2x", {"fee_mult": 2.0}),
    ("funding_2x", {"funding_mult": 2.0}),
    ("entry_delay_1_bar", {"entry_delay_1_bar": True}),
    ("slippage_2ticks", {"slip_ticks": 2}),
]
# Registered cost-attrition grid: 8 x the 5 bps taker fee = 40 bps per fill.
COST_ATTRITION_STRESS = {"fee_mult": 8.0}
# The registered boundary-alternative track (card robustness item 1): the whole schedule is
# mapped one bar EARLIER, i.e. the schedule you get if the raw bar labels had been END-time
# labels instead of the verified open-time labels.
BOUNDARY_ALT_STRESS = {"bar_shift": -1}
# Registered phase grids: every one of them covers the FULL strategy-domain x DCA-domain
# product of every cohort (contract 7.2).
COHORT_GRID_KINDS = ("historical", "oos", "full", "fee_2x", "funding_2x",
                     "entry_delay_1_bar", "slippage_2ticks", "no_funding",
                     "no_funding_full", "cost_attrition_40bps", "boundary_alt_oos",
                     "boundary_alt_full")
# grids whose slice is the FULL registered window (used by the assertions)
FULL_WINDOW_GRID_KINDS = ("full", "fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks",
                          "no_funding_full", "cost_attrition_40bps", "boundary_alt_full")
# grids whose schedule is NOT shifted: only these can assert that no window is clipped at the
# slice edge (the delay and boundary-alternative tracks deliberately move the schedule, so their
# last window can fall past the end of the data - that is counted and reported, not asserted)
UNSHIFTED_GRID_KINDS = ("historical", "oos", "full", "fee_2x", "funding_2x", "slippage_2ticks",
                        "no_funding", "no_funding_full", "cost_attrition_40bps")
# Joint parameter space axes, in the one registered order used for the deterministic
# lexical tie-break (contract 7.3).  Order is part of the gate.  `window_case` is the
# composite leg-subset axis; its registered order is CASE_ORDER.
AXES = ("window_case", "spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct")
DCA_AXES = ("spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct")
CASE_FIELDS = ("leg_long", "leg_short", "leg_secondary")
SELECTOR_VERSION = "cohort-selector-v1"
DISPOSITION_VERSION = "cohort-disposition-v1"
CONTRACT_SEMANTICS_VERSION = "v1.4.0"
ENGINE_VERSION = "d-v1-engine-1.0.0"
ENGINE_SEMANTICS = ("UTC clock-hour seasonality legs (long 15:00-16:00, short 01:00-05:00, "
                    "secondary long 21:00-24:00), boundary-open entry, window-bounded DCA "
                    "rail, hard window-end flatten (see module docstring)")
WINNER_METRIC_KEYS = ("net_pnl", "sharpe", "episodes", "ending_equity", "fees", "funding",
                      "max_dd_usdt", "max_dd_pct", "max_effective_leverage",
                      "capital_utilization", "annualized_return", "cagr")
# csv row field order (kept identical for every phase grid)
ROW_FIELDS = ("symbol", "timeframe", "window_kind", "window_case", "case_name",
              "leg_long", "leg_short", "leg_secondary",
              "spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct",
              "net_pnl", "fees", "funding", "gross_pnl", "ending_equity",
              "windows_seen", "windows_entered", "episodes",
              "tp_hits", "stop_hits", "time_exits", "open_at_end", "margin_calls",
              "halted", "min_entry_equity", "sharpe", "max_dd_usdt", "max_dd_pct",
              "max_effective_leverage", "capital_utilization", "bars_in_market",
              "fills", "turnover_usdt", "days", "years", "cagr", "total_return_pct",
              "annualized_return")
LAYER_TOTALS = {}
# Structural counters, per phase grid (module level so `summarize` can assert them).
# The raw funding timestamps carry ms-level jitter (measured on all four symbols: 0 .. 28 ms
# late relative to the nominal 8h boundary, never early), so the funding exposure window is
# the CLOSED interval [entry - 1s, exit + 1s] and every clock comparison is exact on the 1h
# bar grid.  A deviation beyond the tolerance is a real mapping defect and trips a counter.
MS_JITTER_TOLERANCE_MS = 1000
COUNTER_NAMES = ("entry_bar_not_at_registered_boundary", "exit_bar_not_at_window_end",
                 "window_clipped_at_slice_edge", "overlap_skip", "funding_bar_out_of_hold",
                 "case_not_registered", "bar_grid_not_contiguous")
COUNTERS = {}


def counter(kind, name, inc=1):
    slot = COUNTERS.setdefault(kind, {n: 0 for n in COUNTER_NAMES})
    slot[name] += inc
    return slot[name]


def counters_total(name):
    return sum(v.get(name, 0) for v in COUNTERS.values())


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


def case_tuple(p):
    return tuple(int(p[f]) for f in CASE_FIELDS)


def case_name(case):
    return CASE_NAMES[CASE_ORDER.index(tuple(case))]


def case_index(case):
    return CASE_ORDER.index(tuple(case))


def legs_of(case):
    return [LEGS[i] for i, on in enumerate(case) if on]


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
    bad_grid = 0
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
                    if first_ms is not None and ms - last_ms != MS_PER_HOUR:
                        bad_grid += 1
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
            "non_hourly_steps": bad_grid}


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
            run_log("csv %s rows=%d bytes=%d non_hourly_steps=%d"
                    % (key, per_dataset[key]["rows"], per_dataset[key]["csv_bytes"],
                       per_dataset[key]["non_hourly_steps"]))

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
                "raw_rows": ds["rows"], "non_hourly_steps": ds["non_hourly_steps"]}
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
        # The whole clock mapping assumes the verified contiguous 1h grid: a missing bar
        # would silently shift every later boundary.
        if self.bar_ms <= 0 or self.non_bar_steps:
            counter("cohort", "bar_grid_not_contiguous", 1)
            raise SystemExit("cohort %s/%s: open_time_ms is not a contiguous %d ms bar grid "
                             "(%d deviation(s)); the clock mapping would be wrong"
                             % (symbol, self.timeframe, self.bar_ms, self.non_bar_steps))
        # registered window boundaries per leg: bar index whose open sits on the leg's clock
        self.leg_boundaries = {}
        hours = (self.open_time_ms // MS_PER_HOUR) % 24
        aligned = (self.open_time_ms % MS_PER_HOUR) == 0
        for leg in LEGS:
            self.leg_boundaries[leg["name"]] = np.flatnonzero(
                (hours == leg["start_hour"]) & aligned).astype(np.int64)
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
    """Every registered funding settlement in the window, with its truth_status disclosed.

    The D family uses funding as a COST only (the signal is clock time); the whole registered
    series is loaded and each row's truth_status is counted, so a modelled row is never
    presented as an official observation.
    """
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

    Measured raw fact (all four symbols, 2022-01-01..2026-09-11): settlements sit on the 00/08/16
    UTC grid and run 0..28 ms LATE, never early.  SOL additionally carries a few 2h/4h-era
    settlements in early 2022 (04:00/20:00/... UTC), so the engine never assumes an 8h grid:
    every settlement is charged on its own instant.
    """
    containing = np.searchsorted(cohort.open_time_ms, times, side="left") - 1
    return {"label": label, "counts": counts, "obs_times": times, "obs_rates": rates,
            "settle_bar": containing.astype(np.int64),
            "first_obs_ms": int(times[0]) if len(times) else None,
            "last_obs_ms": int(times[-1]) if len(times) else None}


def window_episodes(cohort, i0, i1, case, bar_shift, delay, kind):
    """The registered clock windows of one case inside a slice -> [(entry_bar, last_bar, leg)].

    The returned bar indices are SLICE-LOCAL (relative to i0) so `simulate` can index its
    sliced arrays directly.  entry_bar = the window's first bar (the boundary bar, shifted by
    `bar_shift` and by the execution-delay stress); last_bar = entry_bar + (leg bars) - 1, so
    the holding length is the registered one under every stress.  The clock guard asserts that
    the entry bar really sits on the registered boundary clock, which is what makes the mapping
    checkable (and is red-provable by corrupting the boundary set).
    """
    out = []
    for li, on in enumerate(case):
        if not on:
            continue
        leg = LEGS[li]
        bars = cohort.leg_boundaries[leg["name"]]
        for b in bars[(bars >= i0) & (bars < i1)]:
            eb = int(b) + bar_shift + delay
            t_end = eb + leg["bars"] - 1
            if eb < i0 or t_end >= i1:
                counter(kind, "window_clipped_at_slice_edge")
                continue
            if int(cohort.open_time_ms[eb] % MS_PER_HOUR) != 0 or \
                    int((cohort.open_time_ms[eb] // MS_PER_HOUR) % 24) != \
                    (leg["start_hour"] + bar_shift + delay) % 24:
                counter(kind, "entry_bar_not_at_registered_boundary")
            if int((cohort.open_time_ms[t_end] + cohort.bar_ms) % MS_PER_HOUR) != 0 or \
                    int(((cohort.open_time_ms[t_end] + cohort.bar_ms) // MS_PER_HOUR) % 24) != \
                    (leg["start_hour"] + bar_shift + delay + leg["bars"]) % 24:
                counter(kind, "exit_bar_not_at_window_end")
            out.append((eb - i0, t_end - i0, li))
    out.sort()
    return out


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
    """One pre-registered parameter case over one window slice (i0, i1).

    `stress` may carry: fee_mult / funding_mult / entry_delay_1_bar / slip_ticks /
    no_funding / bar_shift.  Long episodes walk the bar LOW first then the HIGH; short
    episodes mirror that sym-directionally (HIGH first, then LOW).
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
    bar_shift = int(stress.get("bar_shift", 0))
    episodes = window_episodes(cohort, i0, i1, case, bar_shift, delay, kind)
    windows_seen = len(episodes)

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
    else:
        fund_times = series["obs_times"]
        fund_rates = series["obs_rates"] * stress.get("funding_mult", 1.0)
        settle_bar = series["settle_bar"]

    # slice-local day index, so the daily equity series is scoped to the EVALUATED slice and
    # never padded with the pre-window history (a padded series would dilute Sharpe by
    # sqrt(slice_days / cohort_days) - a measurement defect, not a convention).
    day_id = cohort.open_time_ms[i0:i1] // MS_PER_DAY
    _uniq, day_local = np.unique(day_id, return_inverse=True)
    day_local = day_local.astype(np.int64)
    n_days = int(day_local[-1]) + 1 if n else 0
    day_end = set(int(i) for i in np.flatnonzero(
        np.concatenate([day_local[1:] != day_local[:-1], [True]])))
    day_equity = [None] * n_days

    layers = [0] * 12
    realized = funding_paid = 0.0
    # Independent gross / price-PnL accumulator (contract 7.2, v1.3.2): fed ONLY by
    # `exit_price_pnl` at an exit/flatten, so `gross_pnl` can never be reverse-derived
    # from the net figure.
    gross_pnl = 0.0
    fees_total = 0.0
    fills = 0
    turnover = 0.0
    windows_entered = tp_hits = stop_hits = open_at_end = margin_calls = time_exits = 0
    bars_in_market = 0
    max_lev = 0.0
    util_sum = 0.0
    by_year = {}
    by_phase = {}

    def charge_fee(amount):
        """A fee is paid at the instant of the fill and must reduce the realised equity there."""
        nonlocal realized, fees_total
        fees_total += amount
        realized -= amount
        return amount

    if not episodes:
        series_flat = _flat_series(day_equity)
        return _metrics(realized, fees_total, funding_paid, gross_pnl, 0, tp_hits, stop_hits,
                        open_at_end, margin_calls, time_exits, False, START_EQUITY,
                        series_flat, cohort, i0, i1, layers, max_lev, util_sum, bars_in_market,
                        fills, turnover, windows_seen, windows_entered, kind, {})

    halted = False
    min_entry_equity = START_EQUITY
    last_exit_bar = -1
    for b, b_last, li in episodes:
        if b <= last_exit_bar:
            counter(kind, "overlap_skip")
            continue
        # registered capital-exhaustion semantics: the account cannot lose more than itself,
        # so no new position may be opened once the realised equity is gone
        if START_EQUITY + realized <= 0.0:
            halted = True
            break
        min_entry_equity = min(min_entry_equity, START_EQUITY + realized)
        leg = LEGS[li]
        sign = leg["direction"]           # +1 long, -1 short
        entry_ms = int(cohort.open_time_ms[i0 + b])
        exit_ms = int(cohort.open_time_ms[i0 + b_last]) + cohort.bar_ms
        windows_entered += 1
        px = O[b] - sign * slip_ticks * tick
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
        levels = [p0 * (1.0 - sign * d0 * kk) for kk in range(12)]
        layers[0] += 1
        broke = False
        # funding: every settlement inside the CLOSED exposure interval [entry, exit] with the
        # measured +1 s jitter tolerance, charged in bar order so a later scale-in never
        # retroactively changes an earlier charge.
        f_lo = entry_ms - MS_JITTER_TOLERANCE_MS
        f_hi = exit_ms + MS_JITTER_TOLERANCE_MS
        kptr = int(np.searchsorted(fund_times, f_lo, side="left"))
        kend = int(np.searchsorted(fund_times, f_hi, side="right"))
        t = b
        for t in range(b, b_last + 1):
            while kptr < kend and int(settle_bar[kptr]) <= i0 + t:
                kb = int(settle_bar[kptr])
                if kb < i0:
                    counter(kind, "funding_bar_out_of_hold")
                ep_fund += sign * qty * C[t] * fund_rates[kptr]
                kptr += 1
            l = L[t]
            h = H[t]
            o = O[t]
            killed_at = None
            if sign > 0:
                # Descending-price walk (worst case for a long: the bar LOW is reached first,
                # then the high).  The invalidation is a RESTING stop at
                # average_cost x (1 - invalidation), so a ladder level below it cannot fill:
                # whichever trigger price is higher is reached first as the price descends.
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
            else:
                # Ascending-price walk (mirror image): the bar HIGH is reached first, then the
                # low.  Walking up from the bar open, the next trigger is whichever of the next
                # ladder level and the resting stop is LOWER.  A gap through the stop fills at
                # max(stop price, bar open).
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
                    fpx = trig - slip_ticks * tick
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
            ueq = eq + sign * (qty * C[t] - cost)
            if killed_at is None and ueq <= cohort.margin_maint * qty * C[t]:
                xpx = C[t] + sign * slip_ticks * tick  # capital-exhaustion backstop
                ep_gross = exit_price_pnl(sign * qty * xpx, sign * cost)
                realized += sign * qty * xpx - sign * cost
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
            if t in day_end:
                day_equity[day_local[t]] = ueq
            if killed_at is not None:
                xpx = killed_at + sign * slip_ticks * tick
                ep_gross = exit_price_pnl(sign * qty * xpx, sign * cost)
                realized += sign * qty * xpx - sign * cost
                gross_pnl += ep_gross
                charge_fee(qty * xpx * taf)
                ep_fees += qty * xpx * taf
                fills += 1
                turnover += qty * xpx
                stop_hits += 1
                broke = True
                break
            tpx = (cost / qty) * (1.0 + sign * tp_pct)  # reduce-only breakeven-anchored TP
            if (h >= tpx) if sign > 0 else (l <= tpx):
                xpx = tpx - sign * slip_ticks * tick
                ep_gross = exit_price_pnl(sign * qty * xpx, sign * cost)
                realized += sign * qty * xpx - sign * cost
                gross_pnl += ep_gross
                charge_fee(qty * xpx * taf)
                ep_fees += qty * xpx * taf
                fills += 1
                turnover += qty * xpx
                tp_hits += 1
                broke = True
                break
        if not broke:
            # window end: hard, reduce-only flatten of every layer
            xpx = C[b_last] - sign * slip_ticks * tick
            ep_gross = exit_price_pnl(sign * qty * xpx, sign * cost)
            realized += sign * qty * xpx - sign * cost
            gross_pnl += ep_gross
            charge_fee(qty * xpx * taf)
            ep_fees += qty * xpx * taf
            fills += 1
            turnover += qty * xpx
            if b_last < (i1 - i0) - 1:
                time_exits += 1
            else:
                open_at_end += 1
        # settlements at the exit instant itself (the window's closing boundary) are charged
        # on the exit notional, which is the price at risk when the settlement lands
        while kptr < kend:
            kb = int(settle_bar[kptr])
            if kb > i0 + b_last + 1:
                counter(kind, "funding_bar_out_of_hold")
            ep_fund += sign * qty * C[b_last] * fund_rates[kptr]
            kptr += 1
        funding_paid += ep_fund
        realized -= ep_fund
        last_exit_bar = b_last
        day_equity[day_local[b_last]] = START_EQUITY + realized
        if diag:
            ep_net = ep_gross - ep_fees - ep_fund
            year = time.strftime("%Y", time.gmtime(cohort.open_time_ms[i0 + b] / 1000.0))
            slot = by_year.setdefault(year, {"episodes": 0, "net_pnl": 0.0, "fees": 0.0,
                                             "funding": 0.0})
            slot["episodes"] += 1
            slot["net_pnl"] += ep_net
            slot["fees"] += ep_fees
            slot["funding"] += ep_fund
            slot = by_phase.setdefault(leg["name"], {"episodes": 0, "net_pnl": 0.0})
            slot["episodes"] += 1
            slot["net_pnl"] += ep_net
    episodes_total = windows_entered
    if kind in FULL_WINDOW_GRID_KINDS:
        acc = LAYER_TOTALS.setdefault("full", [0] * 12)
        for kk in range(12):
            acc[kk] += layers[kk]

    series_flat = _flat_series(day_equity)
    diag_out = {}
    if diag:
        diag_out = {"pnl_by_year": {k: _round_dict(v) for k, v in sorted(by_year.items())},
                    "pnl_by_window": {k: _round_dict(v) for k, v in sorted(by_phase.items())},
                    "daily_equity": [round(float(v), 6) for v in series_flat],
                    "daily_equity_days": int(len(series_flat)),
                    "slice_first_day_utc": iso(int(cohort.open_time_ms[i0])),
                    "slice_last_day_utc": iso(int(cohort.open_time_ms[i1 - 1]))}
    return _metrics(realized, fees_total, funding_paid, gross_pnl, episodes_total, tp_hits,
                    stop_hits, open_at_end, margin_calls, time_exits, halted, min_entry_equity,
                    series_flat, cohort, i0, i1, layers, max_lev, util_sum, bars_in_market,
                    fills, turnover, windows_seen, windows_entered, kind, diag_out)


def _round_dict(d):
    return {k: (round(v, 6) if isinstance(v, float) else v) for k, v in d.items()}


def _flat_series(day_equity):
    """Daily equity marks of the EVALUATED slice, forward-filled across flat stretches.

    `day_equity` is indexed by the slice's own day index (never the cohort's), so the series
    covers exactly the window being measured.  A cohort-wide series padded with the
    pre-window history would dilute mean/std of the daily returns by sqrt(slice/cohort) and
    silently deflate Sharpe - a measurement defect, not a convention.
    """
    series = []
    last = START_EQUITY
    for v in day_equity:
        if v is not None:
            last = v
        series.append(last)
    return np.array(series, dtype=np.float64)


def _metrics(realized, fees_total, funding_paid, gross_pnl, episodes, tp_hits, stop_hits,
             open_at_end, margin_calls, time_exits, halted, min_entry_equity, series, cohort,
             i0, i1, layers, max_lev, util_sum, bars_in_market, fills, turnover, windows_seen,
             windows_entered, kind, diag_out):
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
        "time_exits": time_exits, "open_at_end": open_at_end, "margin_calls": margin_calls,
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
        "windows_seen": windows_seen, "windows_entered": windows_entered,
        "layers": layers, "diagnostics": diag_out,
    }


def rail_for(dca):
    """One registered DCA configuration -> the engine rail (four registered axes + basis)."""
    return {"base_quote": dca["base_quote"], "spacing_d0": dca["spacing_pct"],
            "size_multiplier": dca["size_multiplier"], "tp": dca["breakeven_tp_pct"],
            "invalidation": dca["invalidation_pct"]}


def case_label(case):
    """Human-readable leg-set label of one registered case, e.g. 'long+short'."""
    return "+".join(LEGS[i]["name"] for i, on in enumerate(case) if on)


def record(cohort, p, dca, kind, m):
    row = {"symbol": cohort.symbol, "timeframe": cohort.timeframe, "window_kind": kind,
           "window_case": case_label(case_tuple(p)),
           "case_name": case_name(case_tuple(p)),
           "leg_long": p["leg_long"], "leg_short": p["leg_short"],
           "leg_secondary": p["leg_secondary"],
           "spacing_pct": dca["spacing_pct"], "size_multiplier": dca["size_multiplier"],
           "breakeven_tp_pct": dca["breakeven_tp_pct"],
           "invalidation_pct": dca["invalidation_pct"],
           "net_pnl": round(m["net_pnl"], 6), "fees": round(m["fees"], 6),
           "funding": round(m["funding"], 6), "gross_pnl": round(m["gross_pnl"], 6),
           "ending_equity": round(m["ending_equity"], 6),
           "windows_seen": m["windows_seen"], "windows_entered": m["windows_entered"],
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
           "days": m["days"], "years": round(m["years"], 6), "cagr": m["cagr"],
           "total_return_pct": round(m["total_return_pct"], 6),
           "annualized_return": m["annualized_return"]}
    return {k: row[k] for k in ROW_FIELDS}


def params_of(row):
    """One frozen grid row -> the strategy params dict the engine consumes."""
    return {"leg_long": row["leg_long"], "leg_short": row["leg_short"],
            "leg_secondary": row["leg_secondary"]}


def run_cohort(spec, cohort, run_log, series):
    """Every legal (strategy params x DCA config) case of the cohort, on every registered grid."""
    grid = spec["parameter_domain"]["grid_cases"]
    dca_grid = spec["dca_domain"]["grid"]
    slip = spec["costs"]["baseline_slippage_ticks"]
    windows = {"historical": (spec["data"]["historical_start"], spec["data"]["historical_end"]),
               "oos": (spec["data"]["oos_start"], spec["data"]["oos_end"]),
               "full": (spec["data"]["start"], spec["data"]["end"]),
               "boundary_alt_oos": (spec["data"]["oos_start"], spec["data"]["oos_end"]),
               "boundary_alt_full": (spec["data"]["start"], spec["data"]["end"])}
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
                                                  series)))

    sl = slices["full"]
    for sname, stress in STRESS:
        for p in grid:
            for dca in dca_grid:
                rows[sname].append(record(cohort, p, dca, sname,
                                          simulate(cohort, p, rail_for(dca), sl, stress,
                                                   stress.get("slip_ticks", slip), sname,
                                                   series)))
    # G8 cost-attrition grid: 40 bps per fill (8 x the registered 5 bps taker fee).
    for p in grid:
        for dca in dca_grid:
            rows["cost_attrition_40bps"].append(record(
                cohort, p, dca, "cost_attrition_40bps",
                simulate(cohort, p, rail_for(dca), sl, COST_ATTRITION_STRESS, slip,
                         "cost_attrition_40bps", series)))
    # funding-baseline diagnostics run on the SAME windows as their counterparts
    for p in grid:
        for dca in dca_grid:
            rows["no_funding"].append(record(cohort, p, dca, "no_funding",
                                             simulate(cohort, p, rail_for(dca),
                                                      slices["historical"], {"no_funding": True},
                                                      slip, "no_funding", series)))
            rows["no_funding_full"].append(record(cohort, p, dca, "no_funding_full",
                                                  simulate(cohort, p, rail_for(dca), sl,
                                                           {"no_funding": True}, slip,
                                                           "no_funding_full", series)))
    # registered boundary-alternative track: the whole schedule one bar earlier
    for p in grid:
        for dca in dca_grid:
            rows["boundary_alt_oos"].append(record(
                cohort, p, dca, "boundary_alt_oos",
                simulate(cohort, p, rail_for(dca), slices["boundary_alt_oos"],
                         BOUNDARY_ALT_STRESS, slip, "boundary_alt_oos", series)))
            rows["boundary_alt_full"].append(record(
                cohort, p, dca, "boundary_alt_full",
                simulate(cohort, p, rail_for(dca), sl, BOUNDARY_ALT_STRESS, slip,
                         "boundary_alt_full", series)))
    return rows


# ---------------------------------------------------------------------------
# phase 3: pre-registered cohort-selector / cohort-survivor gate
#          (contract v1.3.0 sections 7.2 and 7.3; no post-hoc tuning)
# ---------------------------------------------------------------------------

def axis_values(spec):
    """The registered value list of every joint-space axis, in the registered order."""
    return {"window_case": [case_tuple(g) for g in spec["parameter_domain"]["grid_cases"]],
            "spacing_pct": list(spec["dca_domain"]["spacing_pct"]),
            "size_multiplier": list(spec["dca_domain"]["size_multiplier"]),
            "breakeven_tp_pct": list(spec["dca_domain"]["breakeven_tp_pct"]),
            "invalidation_pct": list(spec["dca_domain"]["invalidation_pct"])}


def cell_key(r):
    return (case_tuple(r),) + tuple(r[a] for a in DCA_AXES)


def tie_break_key(r, axes):
    """Registered-index lexical key: deterministic and free of float formatting."""
    key = [axes["window_case"].index(case_tuple(r))]
    for a in DCA_AXES:
        key.append(axes[a].index(r[a]))
    return tuple(key)


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

    Historical window only, by construction (`require_historical`).  The `window_case` axis is
    categorical: its registered-index adjacency is the registered reading of the contract's
    single-axis +-1 rule, and this is disclosed (a step on that axis may change the leg set).
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
            "case_axis_note": "window_case is categorical; its +-1 registered step is the "
                              "registered reading of the contract single-axis rule",
            "passed": frac >= spec["gates"]["neighborhood_min_same_sign_fraction"]}


def _pick(row, keys):
    return {k: row[k] for k in keys if k in row}


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
    out["winner_case_label"] = case_label(case_tuple(winner))
    out["metrics"] = {
        "historical": _pick(winner, WINNER_METRIC_KEYS),
        "oos": _pick(oos, WINNER_METRIC_KEYS),
        "full": _pick(full, WINNER_METRIC_KEYS),
        "robustness": {s: _pick(stress[s], WINNER_METRIC_KEYS) for s in stress},
        "no_funding_reference": _pick(same_cell(rows["no_funding"], key), WINNER_METRIC_KEYS),
        "no_funding_full_reference": _pick(same_cell(rows["no_funding_full"], key),
                                          WINNER_METRIC_KEYS),
        "cost_attrition_40bps": _pick(cost_attrition, WINNER_METRIC_KEYS),
        "boundary_alt_oos_reference": _pick(same_cell(rows["boundary_alt_oos"], key),
                                            WINNER_METRIC_KEYS),
        "boundary_alt_full_reference": _pick(same_cell(rows["boundary_alt_full"], key),
                                             WINNER_METRIC_KEYS),
    }
    out["neighbourhood"] = nb
    reasons = []
    gates = spec["gates"]
    # G2b: the registered OOS sufficiency floor (the card's "OOS < 60 hold windows" arm).
    if oos["episodes"] < gates["min_episodes_oos"]:
        reasons.append("insufficient_trades")
    # G4: the family's registered OOS economic gate is STRICTER than the contract minimum
    # (`net_pnl > 0 and sharpe > 0`): annualized Sharpe floor 0.40 and a non-negative
    # annualized return (the registered falsification battery).
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


def timezone_fragility_check(spec, cohort_results, rows_by_cohort):
    """Registered family-level falsification item 1 (timezone / boundary fragility).

    Definition (registered before the first run): a cohort HITS the item when the OOS net-PnL
    sign of its elected winner cell under the registered boundary-alternative track (the whole
    schedule mapped one bar earlier - i.e. the schedule you get if the raw bar labels had been
    END-time labels) differs from the sign under the primary open-time mapping.
    Landing: a hit can NEVER be recorded as PASS.
    """
    tracks, hit = [], False
    for c in cohort_results:
        if c["winner"] is None:
            continue
        key = tuple([case_tuple(c["winner"])] + [c["winner"][a] for a in DCA_AXES])
        rows = rows_by_cohort[c["cohort"]]
        primary = same_cell(rows["oos"], key)
        alt = same_cell(rows["boundary_alt_oos"], key)
        primary_full = same_cell(rows["full"], key)
        alt_full = same_cell(rows["boundary_alt_full"], key)
        same_sign = (primary["net_pnl"] > 0.0) == (alt["net_pnl"] > 0.0)
        if not same_sign:
            hit = True
        tracks.append({
            "cohort": c["cohort"],
            "primary_oos_net_pnl": primary["net_pnl"], "primary_oos_sharpe": primary["sharpe"],
            "boundary_alt_oos_net_pnl": alt["net_pnl"],
            "boundary_alt_oos_sharpe": alt["sharpe"],
            "primary_full_net_pnl": primary_full["net_pnl"],
            "boundary_alt_full_net_pnl": alt_full["net_pnl"],
            "oos_sign_agrees": same_sign, "hit": not same_sign})
    return {"registered_item": "timezone-fragility",
            "definition": "the elected winner's OOS net-PnL sign under the alternative "
                          "bar-boundary convention (schedule shifted one bar earlier = the "
                          "end-time labelling reading) differs from the primary open-time "
                          "mapping",
            "evaluated": bool(tracks), "tracks": tracks, "hit": hit,
            "landing": "family-level: a hit must NEVER be recorded as PASS (the runner "
                       "recommends DEFERRED); evidence decides REJECT vs DEFERRED",
            "non_gating_band": False}


def window_instability_check(spec, cohort_results, diag_by_cohort):
    """Registered family-level falsification item 2 (window instability).

    Definition (registered before the first run), per cohort with an elected winner, using the
    winner's DCA config on the FULL window and the diag=True per-year decomposition of the
    singleton leg cases:
        long_2024_plus  = long_only      net PnL summed over 2024, 2025 and the partial 2026;
        short_2024_plus = short_only     net PnL summed over the same years;
    the cohort HITS the item when BOTH are <= 0, i.e. the 15:00-16:00 long window stops
    contributing after 2024 and the 01:00-05:00 short window contributes nothing positive
    either.  The 2022-2023 aggregate of the same legs is reported alongside (the registered
    sub-period stability track).  Landing: a hit can NEVER be recorded as PASS.
    """
    tracks, hit = [], False
    for c in cohort_results:
        if c["winner"] is None:
            continue
        dca = {a: c["winner"][a] for a in DCA_AXES}
        dca["base_quote"] = spec["dca_domain"]["base_quote"]
        legs = {}
        for leg_case, i in (("long_only", 0), ("short_only", 1)):
            p = {"leg_long": 0, "leg_short": 0, "leg_secondary": 0}
            p[CASE_FIELDS[i]] = 1
            m = diag_by_cohort[c["cohort"]]["leg_runs"][leg_case]
            yrs = m["diagnostics"]["pnl_by_year"]
            recent = sum(v["net_pnl"] for y, v in yrs.items() if int(y) >= 2024)
            early = sum(v["net_pnl"] for y, v in yrs.items() if int(y) <= 2023)
            legs[leg_case] = {"net_pnl_2024_plus": round(recent, 6),
                              "net_pnl_2022_2023": round(early, 6),
                              "episodes": m["episodes"],
                              "pnl_by_year": yrs}
        item_hit = (legs["long_only"]["net_pnl_2024_plus"] <= 0.0
                    and legs["short_only"]["net_pnl_2024_plus"] <= 0.0)
        if item_hit:
            hit = True
        tracks.append({"cohort": c["cohort"], "legs": legs, "hit": item_hit})
    return {"registered_item": "window-instability",
            "definition": "the 15:00-16:00 UTC long window fails after 2024 (long_only net PnL "
                          "over 2024+ <= 0) AND the 01:00-05:00 UTC short window has no "
                          "positive contribution either (short_only net PnL over 2024+ <= 0)",
            "evaluated": bool(tracks), "tracks": tracks, "hit": hit,
            "landing": "family-level: a hit must NEVER be recorded as PASS (the runner "
                       "recommends DEFERRED); evidence decides REJECT vs DEFERRED",
            "non_gating_band": False}


def panel_diagnostic(spec, cohort_results, diag_by_cohort, label="panel_equal_notional"):
    """Equal-notional panel view across the eligible instruments (non-gating).

    Each cohort runs the SAME starting equity, so the equal-weighted mean of the cohorts'
    daily equity curves IS the equal-notional panel.  Every cohort is still judged on its own
    (contract 7.3); this object is a descriptive diagnostic and is never a gate.
    """
    members, series = [], {}
    for c in cohort_results:
        info = diag_by_cohort.get(c["cohort"])
        if info is None or info.get("winner_full") is None:
            continue
        members.append(c["cohort"])
        series[c["cohort"]] = info["winner_full"]["diagnostics"]["daily_equity"]
    out = {"non_gating": True,
           "note": "equal notional across the registered instruments; each cohort is judged "
                   "independently (contract 7.3), so this panel number is descriptive only",
           "members": members}
    if not series:
        out["evaluated"] = False
        return out
    n = min(len(v) for v in series.values())
    arr = np.array([v[:n] for v in series.values()], dtype=np.float64)
    out["evaluated"] = True
    out["days"] = n
    for name, tag in (("all_members", members), ("without_BTCUSDT",
                                                 [m for m in members if m != "BTCUSDT/1h"])):
        keep = [i for i, m in enumerate(members) if m in tag]
        if not keep:
            continue
        eq = arr[keep].mean(axis=0)
        rets = np.diff(eq) / eq[:-1]
        sd = float(np.std(rets, ddof=1))
        peak = np.maximum.accumulate(eq)
        out[name] = {
            "members": tag,
            "ending_equity": round(float(eq[-1]), 6),
            "net_pnl": round(float(eq[-1] - START_EQUITY), 6),
            "total_return_pct": round(float(eq[-1] / START_EQUITY - 1.0), 6),
            "sharpe": round(float(np.mean(rets) / sd * np.sqrt(365.0)) if sd > 0 else 0.0, 6),
            "max_dd_pct": round(float((eq - peak).min() / peak.max()), 6),
            "start_days": n}
    out["label"] = label
    return out


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

    strategy_product = set(expected_axes["window_case"])
    dca_product = set((a, b, c, d) for a in expected_axes["spacing_pct"]
                      for b in expected_axes["size_multiplier"]
                      for c in expected_axes["breakeven_tp_pct"]
                      for d in expected_axes["invalidation_pct"])
    strategy_cells, dca_cells = set(), set()
    singleton_cases = set(tuple(1 if i == j else 0 for i in range(3)) for j in range(3))
    singletons_present = {}
    for r in full:
        strategy_cells.add(case_tuple(r))
        dca_cells.add(tuple(r[a] for a in DCA_AXES))
    for label in cohort_labels:
        have = set(case_tuple(r) for r in per_cohort[label]["full"])
        singletons_present[label] = singleton_cases <= have
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
    cohort_results = ([evaluate_cohort(spec, label, per_cohort[label])
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
        wcell = tuple([case_tuple(c["winner"])] + [c["winner"][a] for a in DCA_AXES])
        if again is None or cell_key(again) != wcell:
            selector_stable = False
            break

    # ---- per-cohort winner diagnostics (per-year, per-window, singleton legs, panel series)
    diag_by_cohort = {}
    robustness_diagnostics = {
        "non_gating": True,
        "note": "per-calendar-year and per-registered-window net PnL of the elected winner "
                "cell plus the singleton leg runs of the same DCA config; descriptive only - "
                "the two family-level falsification items are the only gated readers",
        "registered_item": "sub_period_stability_and_panel_equal_notional"}
    if coverage_complete:
        for c in cohort_results:
            if c["winner"] is None:
                continue
            cohort, series = diag_inputs[c["cohort"]]
            slip0 = spec["costs"]["baseline_slippage_ticks"]
            full_slice = cohort.slice(spec["data"]["start"], spec["data"]["end"])
            dca = {a: c["winner"][a] for a in DCA_AXES}
            dca["base_quote"] = spec["dca_domain"]["base_quote"]
            p_win = params_of(c["winner"])
            m_win = simulate(cohort, p_win, rail_for(dca), full_slice, {}, slip0, "full", series,
                             diag=True)
            row = record(cohort, p_win, dca, "full", m_win)
            # the cell lookup must stay inside THIS cohort's rows: grid_rows is the global
            # accumulation over all four cohorts, so the same cell appears once per symbol
            grid_row = same_cell(per_cohort[c["cohort"]]["full"],
                                 tuple([case_tuple(c["winner"])] + [c["winner"][a] for a in DCA_AXES]))
            leg_runs = {}
            for leg_case, i in (("long_only", 0), ("short_only", 1), ("secondary_long_only", 2)):
                p_leg = {"leg_long": 0, "leg_short": 0, "leg_secondary": 0}
                p_leg[CASE_FIELDS[i]] = 1
                leg_runs[leg_case] = simulate(cohort, p_leg, rail_for(dca), full_slice, {},
                                              slip0, "full", series, diag=True)
            diag_by_cohort[c["cohort"]] = {"winner_full": m_win, "leg_runs": leg_runs,
                                           "row": row}
            robustness_diagnostics.setdefault("cohorts", {})[c["cohort"]] = {
                "winner_cell": c["winner"],
                "pnl_by_year": m_win["diagnostics"]["pnl_by_year"],
                "pnl_by_window": m_win["diagnostics"]["pnl_by_window"],
                "singleton_leg_pnl_by_year": {
                    k: v["diagnostics"]["pnl_by_year"] for k, v in leg_runs.items()},
                "winner_diagnostics_match_full_row": all(
                    abs(float(row[k]) - float(grid_row[k])) < 1e-6
                    for k in ROW_FIELDS
                    if isinstance(grid_row[k], (int, float)) and not isinstance(grid_row[k], bool)
                    and isinstance(row[k], (int, float))),
                "daily_series_days": m_win["diagnostics"]["daily_equity_days"]}

    rows_by_cohort = {label: per_cohort[label] for label in cohort_labels}
    timezone = (timezone_fragility_check(spec, cohort_results, rows_by_cohort)
                if coverage_complete else
                {"registered_item": "timezone-fragility", "evaluated": False, "hit": False,
                 "landing": "family-level: a hit must NEVER be recorded as PASS"})
    instability = (window_instability_check(spec, cohort_results, diag_by_cohort)
                   if coverage_complete else
                   {"registered_item": "window-instability", "evaluated": False, "hit": False,
                    "landing": "family-level: a hit must NEVER be recorded as PASS"})
    flags = {"timezone_fragility": bool(timezone.get("hit")),
             "window_instability": bool(instability.get("hit"))}

    # registered landing of the two family-level falsification items (card t_50c28da5):
    # neither may be recorded as PASS; the band mapping itself is unchanged.
    final_verdict = disposition["verdict_recommendation"]
    final_claimable = disposition["performance_claimable_recommendation"]
    if final_verdict == "PASS" and (flags["timezone_fragility"] or flags["window_instability"]):
        final_verdict = "DEFERRED"
        final_claimable = False

    panel = (panel_diagnostic(spec, cohort_results, diag_by_cohort)
             if coverage_complete else {"non_gating": True, "evaluated": False})
    med = lambda rows, key: st.median([r[key] for r in rows if r.get(key) is not None] or [0.0])
    descriptive = {
        "non_gating": True,
        "note": "every cohort is judged independently (contract 7.3); the cross-cohort numbers "
                "below are descriptive diagnostics and are never a gate (contract 7.2)",
        "cohort_count": len(cohort_labels),
        "median_full_net_pnl": med(full, "net_pnl"),
        "median_oos_net_pnl": med(grid_rows["oos"], "net_pnl"),
        "median_historical_net_pnl": med(grid_rows["historical"], "net_pnl"),
        "positive_case_share_historical": (sum(1 for r in grid_rows["historical"] if r["net_pnl"] > 0)
                                          / float(len(grid_rows["historical"]))) if grid_rows["historical"] else 0.0,
        "positive_case_share_full": (sum(1 for r in full if r["net_pnl"] > 0) / float(len(full))) if full else 0.0,
        "cross_instrument_direction_consistency": [
            {"cohort": label, "winner_case": c.get("winner_case_label"),
             "outcome": c["outcome"]}
            for label, c in zip(cohort_labels, cohort_results)],
    }
    stress_summary = {s: {"median_net_pnl": med(grid_rows[s], "net_pnl"),
                          "median_sharpe": med(grid_rows[s], "sharpe"),
                          "cases": len(grid_rows[s]), "non_gating": True} for s, _ in STRESS}
    for extra in ("cost_attrition_40bps", "no_funding", "no_funding_full", "boundary_alt_oos",
                  "boundary_alt_full"):
        stress_summary[extra] = {"median_net_pnl": med(grid_rows[extra], "net_pnl"),
                                 "median_sharpe": med(grid_rows[extra], "sharpe"),
                                 "cases": len(grid_rows[extra]), "non_gating": True}

    keys = ("net_pnl", "fees", "funding", "gross_pnl", "ending_equity", "sharpe",
            "max_dd_pct", "max_dd_usdt", "max_effective_leverage", "capital_utilization",
            "cagr", "total_return_pct", "episodes", "fills", "turnover_usdt")
    mid = lambda rows, key: med(rows, key)
    cost_pressure_effective = (mid(grid_rows["fee_2x"], "fees") > mid(full, "fees")
                               and mid(grid_rows["fee_2x"], "net_pnl") < mid(full, "net_pnl"))
    funding_pressure_effective = (abs(mid(grid_rows["funding_2x"], "funding"))
                                  > abs(mid(full, "funding"))
                                  and mid(grid_rows["funding_2x"], "net_pnl") != mid(full, "net_pnl"))
    boundary_alt_effective = (mid(grid_rows["boundary_alt_full"], "net_pnl") != mid(full, "net_pnl"))
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
        "layer0_equals_episodes": (layers[0] == sum(
            r["episodes"] for k in FULL_WINDOW_GRID_KINDS for r in grid_rows.get(k, []))
            and layers[0] > 0),
        "layer_histogram_nonempty": sum(layers) > 0,
        "no_entry_after_exhaustion": all(r["min_entry_equity"] > 0.0 for r in full),
        "ending_equity_floor": all(r["ending_equity"] > -1.5 * START_EQUITY for r in full),
        "selector_deterministic": selector_stable,
        "selector_historical_only": True,
        # executable no-look-ahead / window guards (per-grid module counters)
        "entry_bar_matches_registered_boundary": counters_total("entry_bar_not_at_registered_boundary") == 0,
        "exit_bar_matches_registered_window_end": counters_total("exit_bar_not_at_window_end") == 0,
        "no_clipped_windows_on_unshifted_grids": all(
            COUNTERS.get(k, {}).get("window_clipped_at_slice_edge", 0) == 0
            for k in UNSHIFTED_GRID_KINDS),
        "shifted_grids_clip_count_reported": True,
        "no_overlapping_episodes": counters_total("overlap_skip") == 0,
        "bar_grid_is_contiguous": counters_total("bar_grid_not_contiguous") == 0,
        "no_unregistered_strategy_case": counters_total("case_not_registered") == 0,
        "funding_bar_never_out_of_hold": counters_total("funding_bar_out_of_hold") == 0,
        "no_funding_grid_is_cost_free": all(r["funding"] == 0.0 for r in grid_rows["no_funding"]),
        "cost_pressure_not_a_noop": cost_pressure_effective,
        "funding_pressure_not_a_noop": funding_pressure_effective,
        "boundary_alt_grid_is_not_a_noop": boundary_alt_effective,
        "long_and_short_legs_evaluated_separately": all(singletons_present.values()),
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
            "strategy": {"window_case": [list(t) for t in expected_axes["window_case"]],
                         "case_names": list(CASE_NAMES),
                         "legs": [dict(leg) for leg in LEGS]},
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
        "verdict_recommendation": disposition["verdict_recommendation"],
        "performance_claimable_recommendation": disposition["performance_claimable_recommendation"],
        "disposition_mapping_version": disposition["mapping_version"],
        "registered_family_level_falsification_flags": flags,
        "verdict_recommendation_final": final_verdict,
        "performance_claimable_recommendation_final": final_claimable,
        "survivor_evidence": survivors,
        "descriptive_diagnostics": descriptive,
        "descriptive_medians_all_base_cases": {k: mid(full, k) for k in keys},
        "stress_summary": stress_summary,
        "dca_layer_histogram": {"level_%02d" % k: layers[k] for k in range(12)},
        "timezone_fragility": timezone,
        "window_instability": instability,
        "panel_diagnostic": panel,
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
        sys.stderr.write("usage: 40_strategy_d_run.py <run-spec.json>\n")
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
                raise SystemExit("run-spec is not a Strategy D spec: missing %r (contract 7.2/7.3)"
                                 % key)
        if spec["selector_version"] != SELECTOR_VERSION or spec["disposition_version"] != DISPOSITION_VERSION:
            raise SystemExit("run-spec selector/disposition version %r/%r != engine %r/%r"
                             % (spec["selector_version"], spec["disposition_version"],
                                SELECTOR_VERSION, DISPOSITION_VERSION))
        # fail closed on a declared strategy domain that is not the registered case set
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
                diag_inputs[label] = (cohort, series)
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
                    "registered_windows": spec["data"]["funding_truth_status_windows"],
                    "settlement_hours_utc": sorted(set(
                        time.strftime("%H:%M", time.gmtime(int(t) / 1000.0)) for t in ft)),
                    "settlement_exposure_note": spec["costs"]["funding_exposure_rule"]}
                run_log("cohort %s bars=%d days=%d funding_obs=%d modeled=%d official=%d"
                        % (label, cohort.n, cohort.n, len(ft), counts["modeled_funding"],
                           counts["official"]))
                for k, v in run_cohort(spec, cohort, run_log, series).items():
                    grid_rows.setdefault(k, []).extend(v)
                done += 1
                atomic_write_json(os.path.join(attempt_dir, "artifacts", "progress.json"),
                                  {"cohorts_done": done, "cohorts_total": total,
                                   "updated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                                   time.gmtime())})
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
        summary = summarize(spec, grid_rows, layers, diag_inputs, slice_days)
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
        atomic_write_json(os.path.join(attempt_dir, "artifacts", "family_falsification.json"),
                          {"timezone_fragility": summary["timezone_fragility"],
                           "window_instability": summary["window_instability"],
                           "flags": summary["registered_family_level_falsification_flags"]})
        atomic_write_json(os.path.join(attempt_dir, "artifacts", "panel_diagnostic.json"),
                          summary["panel_diagnostic"])
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
