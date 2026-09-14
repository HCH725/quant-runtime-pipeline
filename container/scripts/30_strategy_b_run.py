#!/usr/bin/env python3
"""Strategy B v2 — EMA-crossover walk-forward momentum (LONG/SHORT) full backtest, on Qlib.

Entry point for ONE pre-registered attempt of family
`ema-crossover-walkforward-momentum-long-short-v2`.  Reads the immutable run-spec from
/results (the only source of parameters), builds the Qlib .bin store from the READ-ONLY
canonical raw store into /qlib/work, then runs the pre-registered full-backtest
(Contract v1.4+ sections 7.2 / 7.3, current v1.7.0 semantics):

    symbols x timeframes x STRATEGY parameter domain x DCA parameter domain x
    (historical / OOS / robustness)

Registered strategy case space (round-spec `parameter_domain`):
    ema_pair (30 legal fast<slow pairs) x walk_forward (4 diagonal cells) = 120 cases
    per cohort; each case x 48 registered DCA configs = 5,760 case evaluations per
    cohort per phase grid; x 20 cohorts x 10 phase grids = 1,152,000 total.

ENGINE SEMANTICS OF ONE CASE (pinned-pair walk-forward) — decided and disclosed here,
because the registered documents fix the case SPACE (30 x 4) and the selection protocol
but not every mechanical detail:

  * A case is (ema_pair p, walk_forward cell (a, b), DCA config).  The phase window is
    partitioned into consecutive steps of (a + b) days from the window start (a trailing
    partial step is dropped, as in the reference implementation).
  * Inside each step the TRAINING segment `[s, s+a)` evaluates all 30 registered pairs
    with this case's DCA rail and records the registered selection (best Sharpe, then
    net PnL, then registered pair index) — the "walk-forward re-selection of the EMA
    pair" of the round-spec hypothesis.  The selection result is recorded per step in
    the attempt artifacts (`walk_forward_selection_trace.csv`, winner cells).
  * The TEST segment `[s+a, s+a+b)` executes THE CASE'S REGISTERED PAIR p with the DCA
    rail and is force-flattened (reduce-only) on its last bar, so steps chain with a
    well-defined carry-over equity.
  * Nothing is traded during a training segment; the case's `ema_pair` axis is what is
    executed, which is what makes the registered 30 x 4 case space and its per-axis
    parameter-neighbourhood test (contract 7.3 requirement e) non-degenerate: under the
    alternative reading (execute the train-selected best pair) all 30 pairs of a cell
    would produce one identical row, the 30-way `ema_pair` axis would be vacuous and the
    60% same-sign neighbourhood test would pass trivially.

DCA / cost accounting is the current (v1.3.1 / v1.3.2) semantics inherited from the
Strategy A v2 engine, with the rail mirrored for the short leg exactly as registered:

  * per-fill fees: every entry / DCA add / exit fill is a market order, taker fee charged
    into realised equity AT THE FILL (`charge_fee`), so net_pnl / ending_equity / daily
    marks / Sharpe / margin + leverage decisions are all net-of-fee and every cost stress
    grid really moves them;
  * independent gross PnL accumulator: `gross_pnl` is fed only by the price-PnL of each
    closing fill (`d * (qty * exit_price - cost_basis)`), never reverse-derived from net;
    `pnl_decomposition_ok` cross-checks the two independent ledgers;
  * funding: official observations only, charged with the signed rule (a long pays a
    positive rate, a short pays a negative one), accumulated per episode and deducted
    from realised equity when that episode closes (the audited Strategy A v2 structure);
  * short-leg mirror: level_k = entry x (1 + spacing x k), TP at average_cost x (1 - tp),
    resting invalidation at average_cost x (1 + invalidation), intrabar walk walks the
    bar HIGH first, the stop fills at max(stop price, bar open), and the
    capital-exhaustion backstop flattens at the bar close;
  * a direction flip (+1 -> -1) is a reduce-only flatten followed by a fresh episode on
    the new side; the book is never long and short at the same time.

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
WORK_ROOT = "/qlib/work/strategyB-v2"
CSV_ROOT = os.path.join(WORK_ROOT, "csv")
QLIB_DIR = os.path.join(WORK_ROOT, "qlib-data")
MS_PER_DAY = 86400000
FIELDS = ["open", "high", "low", "close", "volume"]
START_EQUITY = 30000.0
# Registered execution-stress grids (round-spec robustness R2).
STRESS = [
    ("fee_2x", {"fee_mult": 2.0}),
    ("funding_2x", {"funding_mult": 2.0}),
    ("entry_delay_1_bar", {"entry_delay_1_bar": True}),
    ("slippage_2ticks", {"slip_ticks": 2}),
]
# Registered phase grids (round-spec gates.G1 / expected.cohort_grid_kinds).
COHORT_GRID_KINDS = ("historical", "oos", "full", "fee_2x", "funding_2x",
                     "entry_delay_1_bar", "slippage_2ticks", "no_funding",
                     "no_funding_full", "cost_attrition_40bps")
# Joint parameter space axes, in the single registered order used for the deterministic
# lexical tie-break (contract 7.3).  Order is part of the gate.
AXES = ("ema_pair", "walk_forward", "spacing_pct", "size_multiplier",
        "breakeven_tp_pct", "invalidation_pct")
DCA_AXES = ("spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct")
SELECTOR_VERSION = "cohort-selector-v1"
DISPOSITION_VERSION = "cohort-disposition-v1"
# The disposition VERSION versions the cohort-judgement semantics; v1.4.0 changed only the
# contract-level band -> verdict/claimability mapping, so every emitted record carries the
# mapping version it applied (the same disclosure the Strategy A v2 engine ships).
CONTRACT_SEMANTICS_VERSION = "v1.4.0"
ENGINE_VERSION = "b-v2-engine-1.0.0"
ENGINE_SEMANTICS = "pinned-pair walk-forward (see module docstring)"
WINNER_METRIC_KEYS = ("net_pnl", "sharpe", "episodes", "ending_equity", "fees", "funding",
                      "gross_pnl", "max_dd_usdt", "max_dd_pct", "max_effective_leverage",
                      "capital_utilization")
ROW_FIELDS = (
    "symbol", "timeframe", "window_kind", "ema_fast", "ema_slow", "wf_train_days",
    "wf_test_days", "ema_pair_index", "walk_forward_index", "spacing_pct",
    "size_multiplier", "breakeven_tp_pct", "invalidation_pct", "base_quote",
    "net_pnl", "fees", "funding", "gross_pnl", "ending_equity", "episodes",
    "episodes_long", "episodes_short", "tp_hits", "stop_hits", "flips", "open_at_end",
    "margin_calls", "halted", "capital_exhausted", "min_entry_equity", "sharpe",
    "max_dd_usdt", "max_dd_pct", "max_effective_leverage", "capital_utilization",
    "bars_in_market", "traded_notional", "days", "years", "cagr", "total_return_pct",
    "annualized_return", "n_steps", "pnl_decomp_ok",
)


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
    """Strictly JSON-safe: numpy scalars -> python, non-finite -> null (never NaN/Infinity)."""
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
    # /qlib/work is a rebuildable derived/cache area (INV-5): rebuild from scratch so the
    # recorded build wall time / size describe THIS attempt's store.
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
        # per-day bar bounds: day_start[d] = the absolute index of the first bar of day d,
        # with a trailing n_days entry so [day_start[d], day_start[d + a]) is the bar range of
        # the days [d, d + a).  A walk-forward step boundary is a DAY boundary, so the segment
        # bounds must come from this array (not from day_index, which is per-bar).
        self.day_start = np.append(
            np.flatnonzero(np.concatenate([[True], day_id[1:] != day_id[:-1]])),
            self.n).astype(np.int64)
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


def ema_of(close, span):
    """Recursive EMA with alpha = 2/(span+1), seeded on the first close (adjust=False)."""
    import pandas as pd
    return pd.Series(close).ewm(span=span, adjust=False).mean().to_numpy(dtype=np.float64)


def direction_array(ema_fast, ema_slow, lag_bars):
    """Execution direction per bar (1-bar-lagged EMAs, next-bar execution; registered rule).

    signal formed at bar j  = sign(ema_fast[j-1] - ema_slow[j-1])   (1-bar-lagged EMAs)
    fill at bar j+1                                                 (next bar)
    => exec_dir[u] = sign(ema_at(u - 2)) for lag_bars = 0, and one bar later per extra lag.
    Ties (and the bars before the first defined comparison) carry the previous direction,
    which starts at +1.  Undefined bars are 0 (flat).
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


def exit_price_pnl(d, qty, exit_price, cost_basis):
    """Pure price PnL of one closing fill, signed by the position direction.

    Contract 7.2 (v1.3.2, independent gross accounting): `gross_pnl` accumulates this value
    only - no fee, no funding - and is never reverse-derived from the net ledger.  For a long
    (d = +1) it is `exit proceeds - cost basis`; for a short (d = -1) the cash leg is mirrored
    (`cost basis - buy-back cost`), which is the same expression times d.
    """
    return d * (qty * exit_price - cost_basis)


def pnl_decomposition_ok(m, tol=1e-3):
    """Fail-closed cross-check of the two independent accounting sources (contract 7.2).

    Left: the gross accumulator (sum of `exit_price_pnl` over every closing fill).
    Right: the net realised ledger plus the per-fill fee ledger plus the funding ledger.
    A regressed or tampered fee/gross path makes this False; the engine test ships the
    negative controls that prove the check is not vacuous.  Asserted on every case row.
    """
    return abs(m["gross_pnl"] - m["fees"] - m["funding"] - m["net_pnl"]) <= tol


def simulate(cohort, window, dirs, rail, slip_ticks, fee_mult=1.0, funding_mult=1.0,
             use_funding=True, base_equity=START_EQUITY):
    """One pre-registered rail execution over the bars [i0, i1) with a direction array.

    Sym-directional (mirrored) rail: d = +1 long, d = -1 short, d = 0 flat.  A direction
    flip is a reduce-only flatten followed by a fresh episode on the new side, so the book
    never holds both sides at once.  The slice is force-flattened on its last bar
    (reduce-only), which is what makes a chained walk-forward well defined.
    """
    i0, i1 = window
    C = cohort.close[i0:i1].tolist()
    H = cohort.high[i0:i1].tolist()
    L = cohort.low[i0:i1].tolist()
    O = cohort.open[i0:i1].tolist()
    D = dirs[i0:i1].tolist()
    n = len(C)
    if use_funding:
        fper = (cohort.funding[i0:i1] * funding_mult).tolist()
    else:
        fper = [0.0] * n
    is_end = np.zeros(n, dtype=bool)
    end_rel = [k - i0 for k in cohort.day_end if i0 <= k < i1]
    if end_rel:
        is_end[end_rel] = True
    dmark = is_end.tolist()
    didx = cohort.day_index[i0:i1].tolist()

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

    layers = [0] * 12
    marks = {}
    realized = fees_total = traded = 0.0
    # Independent gross / price-PnL accumulator (contract 7.2, v1.3.2): fed ONLY by
    # `exit_price_pnl` at a closing fill, so it never reads the fee or funding ledger and
    # `gross_pnl` can never be reverse-derived from the net figure.
    gross_pnl = 0.0
    ep_fees = ep_fund = ep_gross = ep_last_fee = 0.0
    episodes = tp_hits = stop_hits = flips = open_at_end = margin_calls = 0
    episodes_long = episodes_short = 0
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

    def charge_fee(amount):
        """A fee is paid at the instant of the fill and must reduce the realised equity there.

        Accumulating fees in a side ledger (which v1.3.0 shipped) leaves `realized` gross of
        fees, so `fee_2x` could not move net PnL / equity / Sharpe and the daily-equity marks
        and the margin/leverage decisions all read a gross-of-fee equity.  The registered
        metric is the equity net of fees and funding, and every fill (entry, DCA add, exit)
        is a market order, so the deduction is a single choke point here.
        """
        nonlocal realized, fees_total, ep_fees
        fees_total += amount
        realized -= amount
        ep_fees += amount
        return amount

    def close_episode():
        """Book one finished episode: funding for the held bars is deducted here (the audited
        Strategy A v2 structure: fees at each fill, funding when the position that paid it
        closes)."""
        nonlocal realized, ep_fund, ep_gross
        realized -= ep_fund
        _acc["funding"] += ep_fund
        ep_fund = 0.0
        ep_gross = 0.0
        return None

    _acc = {"funding": 0.0}
    t = 0
    while t < n:
        # ---------------------------------------------------------------- adverse walk
        if d != 0 and t != entry_bar:
            killed_at = None
            if d == 1:
                trig0 = lvl_next if (k_next <= 10 and lvl_next >= stop) else stop
                reachable = L[t] <= trig0
            else:
                trig0 = lvl_next if (k_next <= 10 and lvl_next <= stop) else stop
                reachable = H[t] >= trig0
            if reachable:
                k = k_next
                ln = lvl_next
                while True:
                    if k > 10 or d * (ln - stop) < 0:
                        trig, is_stop = stop, True
                    else:
                        trig, is_stop = ln, False
                    # the bar's extreme is walked first in the adverse direction: the LOW for
                    # a long, the HIGH for a short
                    if d * ((L[t] if d == 1 else H[t]) - trig) > 0:
                        break
                    if is_stop:
                        # a gap through the stop fills at the open, never at the stop price
                        # (long: min(open, stop) - short: max(open, stop))
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
                # NOTE (audited rail parity): both the Strategy A v2 engine and the archived
                # Strategy B v1 engine anchor the walk at level 1 (`levels = [p0*(1-d0*k) ...]`
                # in A; `k_next = 1; lvl_next = p0*(1-d*d0)` set only at entry in B v1), so a
                # ladder level that is touched again on a later bar really fills again at that
                # level price.  k_next / lvl_next are the level-1 anchors and are deliberately
                # not advanced here: moving the anchor would silently change the registered
                # ladder semantics (the `k <= 10` guard is what keeps tranche #12 - the
                # reserve - out of routine deployment, exactly as in Strategy A v2).
                if killed_at is not None:
                    xpx = killed_at - d * slip
                    ep_gross += exit_price_pnl(d, qty, xpx, cost)
                    gross_pnl += exit_price_pnl(d, qty, xpx, cost)
                    realized += d * (qty * xpx - cost)
                    ep_last_fee = charge_fee(qty * xpx * taf)
                    traded += qty * xpx
                    stop_hits += 1
                    close_episode()
                    last_closed_dir = d
                    d = 0
        if d != 0:
            ueq = eq0 + realized + d * (qty * C[t] - cost)
            if ueq <= mm * qty * C[t]:
                xpx = C[t] - d * slip          # capital-exhaustion backstop at the close
                gross_pnl += exit_price_pnl(d, qty, xpx, cost)
                realized += d * (qty * xpx - cost)
                ep_last_fee = charge_fee(qty * xpx * taf)
                traded += qty * xpx
                margin_calls += 1
                capital_exhausted = True
                close_episode()
                last_closed_dir = d
                d = 0
            elif (H[t] >= tp) if d == 1 else (L[t] <= tp):
                xpx = tp - d * slip            # reduce-only take profit
                gross_pnl += exit_price_pnl(d, qty, xpx, cost)
                realized += d * (qty * xpx - cost)
                ep_last_fee = charge_fee(qty * xpx * taf)
                traded += qty * xpx
                tp_hits += 1
                close_episode()
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
        nd = int(D[t])
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
                    if d == 1:
                        episodes_long += 1
                    else:
                        episodes_short += 1
        elif nd != 0 and nd != d:
            # direction flip: reduce-only flatten, then re-establish on the new side
            xpx = C[t] - d * slip
            gross_pnl += exit_price_pnl(d, qty, xpx, cost)
            realized += d * (qty * xpx - cost)
            ep_last_fee = charge_fee(qty * xpx * taf)
            traded += qty * xpx
            close_episode()
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
            if d == 1:
                episodes_long += 1
            else:
                episodes_short += 1
        f = fper[t]
        if f != 0.0 and d != 0:
            ep_fund += d * qty * C[t] * f
        if dmark[t]:
            marks[didx[t]] = eq0 + realized + (d * (qty * C[t] - cost) if d != 0 else 0.0)
        t += 1

    if d != 0:                                  # force-flatten at the slice boundary
        xpx = C[n - 1] - d * slip
        gross_pnl += exit_price_pnl(d, qty, xpx, cost)
        realized += d * (qty * xpx - cost)
        ep_last_fee = charge_fee(qty * xpx * taf)
        traded += qty * xpx
        open_at_end += 1
        close_episode()
        last_closed_dir = d
        d = 0
    # the last day of the slice is marked with the realised equity after the flatten
    marks[didx[n - 1]] = eq0 + realized

    return {
        "net_pnl": realized, "fees": fees_total, "funding": _acc["funding"],
        "gross_pnl": gross_pnl, "traded_notional": traded,
        "ending_equity": eq0 + realized, "episodes": episodes,
        "episodes_long": episodes_long, "episodes_short": episodes_short,
        "tp_hits": tp_hits, "stop_hits": stop_hits, "flips": flips,
        "open_at_end": open_at_end, "margin_calls": margin_calls, "halted": halted,
        "capital_exhausted": capital_exhausted, "min_entry_equity": min_entry_equity,
        "bars_in_market": bars_in_market, "max_effective_leverage": max_lev,
        "capital_utilization": (util_sum / bars_in_market) if bars_in_market else 0.0,
        "layers": layers, "marks": marks,
    }


def rail_for(dca):
    """One registered DCA configuration -> the engine rail (the four registered axes)."""
    return {"base_quote": dca["base_quote"], "spacing_d0": dca["spacing_pct"],
            "size_multiplier": dca["size_multiplier"], "tp": dca["breakeven_tp_pct"],
            "invalidation": dca["invalidation_pct"]}


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
    """Daily-return Sharpe (annualised by sqrt(365)); null when the equity path is
    non-positive (growth rates are undefined there)."""
    if len(series) < 3 or (series[:-1] <= 0.0).any():
        return None
    rets = np.diff(series) / series[:-1]
    sd = float(np.std(rets, ddof=1))
    if sd <= 0.0:
        return 0.0
    return float(np.mean(rets) / sd * np.sqrt(365.0))


def max_dd(series):
    peak = np.maximum.accumulate(series)
    dd = series - peak
    dd_pct = dd / peak
    return float(dd.min()), float(dd_pct.min())


def steps_for(day0, day1, cell):
    """The registered step grid: consecutive (a + b)-day blocks, trailing partial dropped."""
    a, b = cell
    out = []
    s = day0
    while s + a + b <= day1:
        out.append(s)
        s += a + b
    return out


def select_train_pairs(cohort, days, cell, pairs, dca, slip_ticks, fee_mult, funding_mult,
                       use_funding, dir_sets):
    """The registered per-step selection protocol on the training segments.

    For every step of the walk-forward grid the training segment `[s, s+a)` evaluates ALL 30
    registered pairs with this case's DCA rail and selects the best by (Sharpe desc, net PnL
    desc, registered pair index asc) -- the "walk-forward re-selection of the EMA pair" of the
    registered mechanism.  The evaluation uses the registered starting equity as its base for
    the train-segment Sharpe: the ranking is scale-invariant in equity except through the
    halt/margin rules (which are nowhere near binding at 30,000 USDT against one training
    segment), and a shared, deterministic base is what lets the selection be computed once per
    (cell, DCA, window) and recorded for all 30 pinned cases.

    Returns a list of per-step dicts, index-aligned with `steps_for(...)`.
    """
    a, b = cell
    rail = rail_for(dca)
    out = []
    for s in steps_for(days[0], days[1], cell):
        tr0, tr1 = int(days[2][s]), int(days[2][s + a])
        if tr1 <= tr0:
            continue
        best = None
        for pi, dirs in enumerate(dir_sets):
            tr = simulate(cohort, (tr0, tr1), dirs, rail, slip_ticks, fee_mult,
                          funding_mult, use_funding, START_EQUITY)
            sh = sharpe_of(daily_series(tr["marks"], s, s + a, START_EQUITY))
            key = (-1e18 if sh is None else sh, tr["net_pnl"], -pi)
            if best is None or key > best[0]:
                best = (key, pi, sh, tr["net_pnl"])
        if best is None:
            raise SystemExit("empty EMA pair domain")
        out.append({"step": len(out), "start_day": s, "train_days": [s - days[0], s + a - days[0]],
                    "test_days": [s + a - days[0], s + a + b - days[0]],
                    "selected_pair_index": best[1], "selected_fast": pairs[best[1]][0],
                    "selected_slow": pairs[best[1]][1], "train_sharpe": best[2],
                    "train_net_pnl": best[3]})
    return out


def wf_case(cohort, days, cell, exec_index, dir_sets, rail, slip_ticks, fee_mult,
            funding_mult, use_funding, base_equity, selection):
    """One pinned-pair walk-forward case over the phase window's day range.

    Test segments execute the CASE'S registered pair (`exec_index`); the registered per-step
    training selection is passed in (`selection`, shared across the 30 pinned cases of this
    cell/DCA/window) and recorded per step.  Returns the aggregate metrics of the case plus
    the per-step trace.
    """
    a, b = cell
    marks = {}
    layers = [0] * 12
    tot = {"net_pnl": 0.0, "fees": 0.0, "funding": 0.0, "gross_pnl": 0.0,
           "traded_notional": 0.0, "episodes": 0, "episodes_long": 0, "episodes_short": 0,
           "tp_hits": 0, "stop_hits": 0, "flips": 0, "open_at_end": 0,
           "margin_calls": 0, "bars_in_market": 0}
    max_lev = 0.0
    util_num = 0.0
    min_entry_eq = float("inf")
    halted = capital_exhausted = False
    eq = base_equity
    trace = []
    n_steps = 0
    for st in selection:
        s = st["start_day"]
        te0, te1 = int(days[2][s + a]), int(days[2][s + a + b])
        if te1 <= te0:
            continue
        te = simulate(cohort, (te0, te1), dir_sets[exec_index], rail, slip_ticks, fee_mult,
                      funding_mult, use_funding, eq)
        marks.update(te["marks"])
        for k in range(12):
            layers[k] += te["layers"][k]
        for k in ("net_pnl", "fees", "funding", "gross_pnl", "traded_notional", "episodes",
                  "episodes_long", "episodes_short", "tp_hits", "stop_hits", "flips",
                  "open_at_end", "margin_calls", "bars_in_market"):
            tot[k] += te[k]
        max_lev = max(max_lev, te["max_effective_leverage"])
        util_num += te["capital_utilization"] * te["bars_in_market"]
        min_entry_eq = min(min_entry_eq, te["min_entry_equity"])
        eq = te["ending_equity"]
        halted = halted or te["halted"]
        capital_exhausted = capital_exhausted or te["capital_exhausted"]
        n_steps += 1
        trace.append(dict(st, executed_pair_index=exec_index, test_net_pnl=te["net_pnl"],
                          test_episodes=te["episodes"], test_fees=te["fees"],
                          test_funding=te["funding"], ending_equity=eq))
    series = daily_series(marks, days[0], days[1], base_equity)
    dd_usdt, dd_pct = max_dd(series)
    span_days = max(days[1] - days[0], 1)
    years = span_days / 365.25
    end_eq = eq
    bars = tot["bars_in_market"]
    net = tot["net_pnl"]
    m = {
        "net_pnl": net, "fees": tot["fees"], "funding": tot["funding"],
        "gross_pnl": tot["gross_pnl"], "traded_notional": tot["traded_notional"],
        "ending_equity": end_eq, "episodes": tot["episodes"],
        "episodes_long": tot["episodes_long"], "episodes_short": tot["episodes_short"],
        "tp_hits": tot["tp_hits"], "stop_hits": tot["stop_hits"], "flips": tot["flips"],
        "open_at_end": tot["open_at_end"], "margin_calls": tot["margin_calls"],
        "halted": halted, "capital_exhausted": capital_exhausted,
        "min_entry_equity": min_entry_eq if min_entry_eq != float("inf") else base_equity,
        "sharpe": sharpe_of(series), "max_dd_usdt": dd_usdt, "max_dd_pct": dd_pct,
        "max_effective_leverage": max_lev,
        "capital_utilization": (util_num / bars) if bars else 0.0,
        "bars_in_market": bars, "days": span_days, "years": years,
        "total_return_pct": end_eq / base_equity - 1.0,
        "cagr": ((end_eq / base_equity) ** (1.0 / years) - 1.0) if end_eq > 0.0 else None,
        "annualized_return": ((end_eq / base_equity - 1.0) / years) if end_eq > 0.0 else None,
        "layers": layers, "n_steps": n_steps, "series": series,
    }
    m["pnl_decomp_ok"] = pnl_decomposition_ok(m)
    m["_trace"] = trace
    return m


def record(cohort, pair, pair_index, cell, cell_index, dca, kind, m):
    row = {"symbol": cohort.symbol, "timeframe": cohort.timeframe, "window_kind": kind,
           "ema_fast": pair[0], "ema_slow": pair[1],
           "wf_train_days": cell[0], "wf_test_days": cell[1],
           "ema_pair_index": pair_index, "walk_forward_index": cell_index,
           "spacing_pct": dca["spacing_pct"], "size_multiplier": dca["size_multiplier"],
           "breakeven_tp_pct": dca["breakeven_tp_pct"],
           "invalidation_pct": dca["invalidation_pct"], "base_quote": dca["base_quote"],
           "net_pnl": round(m["net_pnl"], 6), "fees": round(m["fees"], 6),
           "funding": round(m["funding"], 6), "gross_pnl": round(m["gross_pnl"], 6),
           "ending_equity": round(m["ending_equity"], 6),
           "episodes": m["episodes"], "episodes_long": m["episodes_long"],
           "episodes_short": m["episodes_short"], "tp_hits": m["tp_hits"],
           "stop_hits": m["stop_hits"], "flips": m["flips"], "open_at_end": m["open_at_end"],
           "margin_calls": m["margin_calls"], "halted": m["halted"],
           "capital_exhausted": m["capital_exhausted"],
           "min_entry_equity": round(m["min_entry_equity"], 6),
           "sharpe": None if m["sharpe"] is None else round(m["sharpe"], 6),
           "max_dd_usdt": round(m["max_dd_usdt"], 6), "max_dd_pct": round(m["max_dd_pct"], 6),
           "max_effective_leverage": round(m["max_effective_leverage"], 6),
           "capital_utilization": round(m["capital_utilization"], 6),
           "bars_in_market": m["bars_in_market"], "traded_notional": round(m["traded_notional"], 6),
           "days": m["days"], "years": round(m["years"], 6),
           "cagr": m["cagr"], "total_return_pct": round(m["total_return_pct"], 6),
           "annualized_return": m["annualized_return"], "n_steps": m["n_steps"],
           "pnl_decomp_ok": bool(m["pnl_decomp_ok"])}
    return row


GRID_STRESS = {"fee_2x": {"fee_mult": 2.0}, "funding_2x": {"funding_mult": 2.0},
               "entry_delay_1_bar": {"entry_delay_1_bar": True},
               "slippage_2ticks": {"slip_ticks": 2},
               "no_funding": {"no_funding": True}, "no_funding_full": {"no_funding": True},
               "cost_attrition_40bps": {"fee_mult": 8.0}}


COHORT_DAY_SOURCE = {"historical": "historical", "oos": "oos", "full": "full",
                     "fee_2x": "full", "funding_2x": "full", "entry_delay_1_bar": "full",
                     "slippage_2ticks": "full", "no_funding": "historical",
                     "no_funding_full": "full", "cost_attrition_40bps": "full"}


def run_cohort(spec, cohort, run_log, writers, agg):
    """Every legal (ema_pair x walk_forward x DCA) case of one cohort on every registered grid.

    Rows stream to the registered `artifacts/grid_<kind>.csv` files (memory stays bounded).
    The historical grid is produced first, the cohort winner is elected immediately from it
    (historical-only selector, contract 7.3) and only that winner cell is retained for the
    later grids.  Every coverage / assertion accumulator is updated here so the summary never
    has to re-read the CSVs.
    """
    pairs = [(int(p["fast"]), int(p["slow"])) for p in spec["params"]["ema_pair"]]
    cells = [(int(w["train_days"]), int(w["test_days"])) for w in spec["params"]["walk_forward"]]
    dca_grid = spec["dca_domain"]["grid"]
    base_slip = spec["costs"]["baseline_slippage_ticks"]
    windows = {"historical": (spec["data"]["historical_start"], spec["data"]["historical_end"]),
               "oos": (spec["data"]["oos_start"], spec["data"]["oos_end"]),
               "full": (spec["data"]["start"], spec["data"]["end"])}

    slices = {k: cohort.slice(*v) for k, v in windows.items()}
    for kind in ("historical", "oos", "full"):
        sl = slices[kind]
        if sl[1] <= sl[0]:
            raise SystemExit("data_window_invalid: %s window is empty for %s/%s"
                             % (kind, cohort.symbol, cohort.timeframe))
    days = {}
    for k, (i0, i1) in slices.items():
        d0, d1 = int(cohort.day_index[i0]), int(cohort.day_index[i1 - 1]) + 1
        # a walk-forward step boundary is a DAY boundary, so the window itself must be
        # day-aligned (every registered window is a whole-day range of a UTC-daily calendar)
        if int(cohort.day_start[d0]) != i0 or int(cohort.day_start[d1]) != i1:
            raise SystemExit("data_window_not_day_aligned: %s window for %s/%s"
                             % (k, cohort.symbol, cohort.timeframe))
        days[k] = (d0, d1, cohort.day_start)

    # EMAs are computed once over the FULL cohort series per registered span; every slice is
    # taken from those arrays, so a block's EMAs carry earlier (past-only) information and no
    # per-block warm-up is silently different between a training block and its adjacent test
    # block.
    spans = sorted({p[0] for p in pairs} | {p[1] for p in pairs})
    emas = {sp: ema_of(cohort.close, sp) for sp in spans}
    dir_sets = [direction_array(emas[p[0]], emas[p[1]], 0) for p in pairs]
    # the registered entry_delay_1_bar track needs its own direction set (one bar later);
    # without it the stress parameter would be dead and the track would repeat the baseline.
    dir_sets_delay = [direction_array(emas[p[0]], emas[p[1]], 1) for p in pairs]
    del emas

    label = "%s/%s" % (cohort.symbol, cohort.timeframe)
    hist_rows = []
    winner_rows = {}
    winner_row = None
    winner_reason = None
    winner_key = None
    for kind in COHORT_GRID_KINDS:
        if kind == "historical":
            stress, slip_k = {}, base_slip
        elif kind == "oos":
            stress, slip_k = {}, base_slip
        elif kind == "full":
            stress, slip_k = {}, base_slip
        elif kind == "no_funding":
            stress, slip_k = {"no_funding": True}, base_slip
        elif kind == "no_funding_full":
            stress, slip_k = {"no_funding": True}, base_slip
        else:
            stress = dict(GRID_STRESS[kind])
            slip_k = stress.get("slip_ticks", base_slip)
        use_funding = not stress.get("no_funding", False)
        fee_mult = stress.get("fee_mult", 1.0)
        funding_mult = stress.get("funding_mult", 1.0)
        ds = dir_sets_delay if stress.get("entry_delay_1_bar") else dir_sets
        day0, day1 = days[COHORT_DAY_SOURCE[kind]][0:2]
        t_grid = time.time()
        for ci, cell in enumerate(cells):
            for dca in dca_grid:
                selection = select_train_pairs(cohort, (day0, day1, cohort.day_start), cell,
                                               pairs, dca, slip_k, fee_mult, funding_mult,
                                               use_funding, ds)
                rail = rail_for(dca)
                for pi, pair in enumerate(pairs):
                    m = wf_case(cohort, (day0, day1, cohort.day_start), cell, pi, ds, rail,
                                slip_k, fee_mult, funding_mult, use_funding, START_EQUITY,
                                selection)
                    row = record(cohort, pair, pi, cell, ci, dca, kind, m)
                    writers[kind].writerow(row)
                    agg["counts"][kind] += 1
                    agg["per_cohort"][(label, kind)] += 1
                    agg["strategy_cells"].add((pair[0], pair[1], cell[0], cell[1]))
                    agg["dca_cells"].add(tuple(row[a] for a in DCA_AXES))
                    agg["pnl_decomp_all"] = agg["pnl_decomp_all"] and row["pnl_decomp_ok"]
                    agg["partition_all"] = agg["partition_all"] and (
                        row["episodes"] == row["tp_hits"] + row["stop_hits"]
                        + row["open_at_end"] + row["margin_calls"] + row["flips"])
                    agg["no_entry_after_exhaustion"] = agg["no_entry_after_exhaustion"] and (
                        row["min_entry_equity"] > 0.0)
                    agg["ending_equity_floor"] = agg["ending_equity_floor"] and (
                        row["ending_equity"] > -1.5 * START_EQUITY)
                    agg["episodes_long_total"] += row["episodes_long"]
                    agg["episodes_short_total"] += row["episodes_short"]
                    if kind == "full":
                        agg["layer_totals"] = [agg["layer_totals"][k] + m["layers"][k]
                                               for k in range(12)]
                        agg["episodes_full_total"] += row["episodes"]
                    if kind == "historical":
                        hist_rows.append(row)
                    elif winner_key is not None and cell_key(row) == winner_key:
                        winner_rows[kind] = row
        if kind == "historical":
            import random
            winner_row, winner_reason = select_cohort_winner(hist_rows, spec)
            winner_key = cell_key(winner_row) if winner_row is not None else None
            # deterministic selector self-check: a shuffled copy of the same historical rows
            # must elect the same cell (contract 7.3 - executable, per cohort)
            if winner_row is not None:
                rng = random.Random(20260913)
                shuffled = list(hist_rows)
                rng.shuffle(shuffled)
                again, _ = select_cohort_winner(shuffled, spec)
                agg["selector_stable"] = agg["selector_stable"] and (
                    again is not None and cell_key(again) == winner_key)
            agg["selector_stable"] = agg["selector_stable"] and True
            if winner_row is not None:
                # preserve the registered per-step re-selection trace of the winning case by
                # recomputing it deterministically (identical arithmetic -> identical numbers)
                wcell = (winner_row["wf_train_days"], winner_row["wf_test_days"])
                wdca = {"base_quote": winner_row["base_quote"],
                        "spacing_pct": winner_row["spacing_pct"],
                        "size_multiplier": winner_row["size_multiplier"],
                        "breakeven_tp_pct": winner_row["breakeven_tp_pct"],
                        "invalidation_pct": winner_row["invalidation_pct"]}
                wsel = select_train_pairs(cohort, (days["historical"][0], days["historical"][1],
                                                   cohort.day_start), wcell, pairs, wdca,
                                          base_slip, 1.0, 1.0, True, dir_sets)
                wm = wf_case(cohort, (days["historical"][0], days["historical"][1],
                                      cohort.day_start), wcell, winner_row["ema_pair_index"],
                             dir_sets, rail_for(wdca), base_slip, 1.0, 1.0, True,
                             START_EQUITY, wsel)
                if abs(wm["net_pnl"] - winner_row["net_pnl"]) > 1e-6:
                    raise SystemExit("winner trace recomputation is not deterministic: %s"
                                     % label)
                agg["winner_traces"][label] = {"cell": list(wcell),
                                               "ema_fast": winner_row["ema_fast"],
                                               "ema_slow": winner_row["ema_slow"],
                                               "dca": wdca,
                                               "steps": wm["_trace"]}
        run_log("grid %s %s rows=%d (grid total %d) in %.1fs"
                % (kind, label, agg["per_cohort"][(label, kind)], agg["counts"][kind],
                   time.time() - t_grid))
    return hist_rows, winner_row, winner_reason, winner_rows


# ---------------------------------------------------------------------------
# phase 3: pre-registered cohort-selector / cohort-survivor gate (contract 7.3)
# ---------------------------------------------------------------------------

def axis_values(spec):
    """The registered value list of every joint-space axis, in the registered order."""
    return {"ema_pair": [(int(p["fast"]), int(p["slow"])) for p in spec["params"]["ema_pair"]],
            "walk_forward": [(int(w["train_days"]), int(w["test_days"]))
                             for w in spec["params"]["walk_forward"]],
            "spacing_pct": list(spec["dca_domain"]["spacing_pct"]),
            "size_multiplier": list(spec["dca_domain"]["size_multiplier"]),
            "breakeven_tp_pct": list(spec["dca_domain"]["breakeven_tp_pct"]),
            "invalidation_pct": list(spec["dca_domain"]["invalidation_pct"])}


def cell_key(r):
    """The row's 8-scalar exact-cell identity (cross-grid lookup / same_cell identity)."""
    return (r["ema_fast"], r["ema_slow"], r["wf_train_days"], r["wf_test_days"],
            r["spacing_pct"], r["size_multiplier"], r["breakeven_tp_pct"],
            r["invalidation_pct"])


def axis_key(r):
    """The row's value on each registered axis, in the registered AXES order.

    `ema_pair` and `walk_forward` are COMPOSITE registered axes: one registered value is the
    whole (fast, slow) / (train_days, test_days) tuple.  Never address those axes with one
    scalar of the tuple - that would look up (and mutate) axes the round never registered.
    """
    return ((r["ema_fast"], r["ema_slow"]), (r["wf_train_days"], r["wf_test_days"]),
            r["spacing_pct"], r["size_multiplier"], r["breakeven_tp_pct"],
            r["invalidation_pct"])


def axis_key_cell(key):
    """Registered-axis key -> the 8-scalar exact-cell identity (composite axes re-joined)."""
    return (key[0][0], key[0][1], key[1][0], key[1][1], key[2], key[3], key[4], key[5])


def axis_index(axes, axis, value, row):
    """The registered index of `value` on `axis`.  Fail closed, naming axis/value/cell."""
    try:
        return axes[axis].index(value)
    except ValueError:
        raise ValueError("cell %s carries unregistered %s value %r (registered: %s)"
                         % (str(cell_key(row)), axis, value, str(axes[axis])))


def tie_break_key(r, axes):
    """Registered-index lexical key: deterministic and free of float formatting."""
    key = axis_key(r)
    return tuple(axis_index(axes, axis, key[i], r) for i, axis in enumerate(AXES))


def require_historical(rows, where):
    """Selection and neighbourhood judgement may never read OOS (contract 7.3)."""
    bad = [r for r in rows if r.get("window_kind") != "historical"]
    if bad:
        raise ValueError("%s must be given historical rows only (contract 7.3): %d violation(s)"
                         % (where, len(bad)))


def select_cohort_winner(hist_rows, spec):
    """One deterministic winner per cohort, historical window only (contract 7.3 steps 1-4).

    (1) the cohort's best case must reach `min_episodes_is` historical episodes, otherwise
    the whole cohort is culled for insufficient trades; (2) a candidate needs net_pnl > 0 AND
    sharpe > 0; (3) ranking is Sharpe desc, net_pnl desc, then the registered-index lexical key
    of the joint parameter cell.  Returns (row_or_None, reason).
    """
    require_historical(hist_rows, "select_cohort_winner")
    min_ep = spec["gates"]["min_episodes_is"]
    if not hist_rows or max(r["episodes"] for r in hist_rows) < min_ep:
        return None, "insufficient_trades"
    ok = [r for r in hist_rows
          if r["net_pnl"] > 0.0 and r["sharpe"] is not None and r["sharpe"] > 0.0
          and r["episodes"] >= min_ep]
    if not ok:
        return None, "no_qualifying_candidate"
    axes = axis_values(spec)
    ok.sort(key=lambda r: (-r["sharpe"], -r["net_pnl"], tie_break_key(r, axes)))
    return ok[0], "selected"


def same_cell(rows, key):
    hits = [r for r in rows if cell_key(r) == key]
    if len(hits) != 1:
        raise ValueError("expected exactly one row for cell %s, found %d" % (str(key), len(hits)))
    return hits[0]


def cohort_neighbourhood(hist_rows, winner, spec):
    """Face-adjacent (+-1 step on exactly one registered axis) sign agreement (contract 7.3e).

    Historical window only, by construction (`require_historical`) and by the caller passing
    the historical grid.  The neighbour of each axis is the registered INDEX +- 1 of that
    axis' registered value list, so every axis really moves one registered step - and a
    COMPOSITE axis (ema_pair, walk_forward) moves one whole registered tuple, never one of its
    scalars.  The mutated registered-axis key is turned back into the 8-scalar exact-cell
    identity before the grid lookup, so neighbourhood and cross-grid lookups address the same
    cells.  A legal neighbour the grid does not hold fails closed.
    """
    require_historical(hist_rows, "cohort_neighbourhood")
    axes = axis_values(spec)
    idx = {cell_key(r): r for r in hist_rows}
    wkey = axis_key(winner)
    wsign = winner["net_pnl"] > 0.0
    neighbours, missing = [], []
    for ai, axis in enumerate(AXES):
        vals = axes[axis]
        pos = axis_index(axes, axis, wkey[ai], winner)
        for step in (-1, 1):
            npos = pos + step
            if not (0 <= npos < len(vals)):
                continue
            nkey = list(wkey)
            nkey[ai] = vals[npos]
            ncell = axis_key_cell(tuple(nkey))
            row = idx.get(ncell)
            if row is None:
                missing.append("%s step %+d -> %s" % (axis, step, str(ncell)))
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
            "passed": frac >= spec["gates"]["neighborhood_min_same_sign_fraction"],
            "axis_steps": {a: len(axes[a]) for a in AXES}}


def _pick(row, keys):
    return {k: row[k] for k in keys if k in row}


def evaluate_cohort(spec, cohort_label, winner, reason, hist_rows, winner_rows):
    """The whole cohort decision for one (symbol, timeframe) cohort."""
    out = {"cohort": cohort_label, "outcome": "CULLED", "no_winner_reason": None,
           "cull_reasons": [], "winner": None, "neighbourhood": None, "metrics": {}}
    if winner is None:
        out["no_winner_reason"] = reason
        out["cull_reasons"].append(reason)
        return out
    key = cell_key(winner)

    def grid_row(kind):
        """The winner's exact cell on another registered phase grid (fail-closed lookup)."""
        r = winner_rows.get(kind)
        if r is None or cell_key(r) != key:
            raise ValueError("winner cell %s missing from grid %s for %s"
                             % (str(key), kind, cohort_label))
        return r

    nb = cohort_neighbourhood(hist_rows, winner, spec)
    oos = grid_row("oos")
    full = grid_row("full")
    stress = {s: grid_row(s) for s, _ in STRESS}
    out["winner"] = _pick(winner, AXES + ("ema_pair_index", "walk_forward_index", "n_steps"))
    out["metrics"] = {
        "historical": _pick(winner, WINNER_METRIC_KEYS),
        "oos": _pick(oos, WINNER_METRIC_KEYS),
        "full": _pick(full, WINNER_METRIC_KEYS),
        "robustness": {s: _pick(stress[s], WINNER_METRIC_KEYS) for s in stress},
        "no_funding_reference": _pick(grid_row("no_funding"), WINNER_METRIC_KEYS),
        "no_funding_full_reference": _pick(grid_row("no_funding_full"), WINNER_METRIC_KEYS),
        "cost_attrition_40bps": _pick(grid_row("cost_attrition_40bps"), WINNER_METRIC_KEYS),
    }
    out["neighbourhood"] = nb
    reasons = []
    if not (oos["net_pnl"] > 0.0 and oos["sharpe"] is not None and oos["sharpe"] > 0.0):
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
    """Contract 7.2/7.3 (v1.4.0).  Coverage/technical incompleteness wins.

      0 survivors        -> REJECT / NO_SURVIVOR
      1 survivor         -> SURVIVOR_FOUND     (verdict PASS)
      2+ survivors       -> MULTIPLE_SURVIVORS (verdict PASS as well)

    MULTIPLE_SURVIVORS is a disposition BAND, not a downgrade: no survivor is ranked,
    discarded or picked among - all of them are kept and advance (contract 7.3), and the
    count never forces performance_claimable false (contract 9.6 is the only依据).
    """
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


def summarize(spec, agg, cohort_results):
    import random
    import statistics as st
    expected_axes = axis_values(spec)
    need = {k: spec["expected"]["case_evaluations_per_grid"] for k in COHORT_GRID_KINDS}
    coverage = {k: agg["counts"][k] for k in COHORT_GRID_KINDS}
    cohort_labels = agg["cohort_labels"]
    dca_product = set((a, b, c, d) for a in expected_axes["spacing_pct"]
                      for b in expected_axes["size_multiplier"]
                      for c in expected_axes["breakeven_tp_pct"]
                      for d in expected_axes["invalidation_pct"])
    strategy_product = set((p[0], p[1], w[0], w[1]) for p in expected_axes["ema_pair"]
                           for w in expected_axes["walk_forward"])
    cells_per_cohort_ok = all(
        agg["per_cohort"][(c, k)] == spec["expected"]["base_combinations_per_cohort"]
        for c in cohort_labels for k in COHORT_GRID_KINDS) if cohort_labels else False
    coverage_complete = (all(coverage[k] == need[k] for k in need)
                         and len(cohort_labels) == spec["expected"]["cohorts"]
                         and agg["strategy_cells"] == strategy_product
                         and agg["dca_cells"] == dca_product
                         and cells_per_cohort_ok)
    survivors = [c for c in cohort_results if c["outcome"] == "SURVIVOR"]
    disposition = family_disposition(survivors, coverage_complete)

    med = lambda rows, key: st.median([r[key] for r in rows if r.get(key) is not None] or [0.0])
    full_rows = agg["full_rows_sample"]
    descriptive = {
        "non_gating": True,
        "note": "cross-cohort medians are descriptive diagnostics only; they are never a gate "
                "(contract 7.2/7.3).  The medians below are taken over each cohort's elected "
                "winner cell (the code path that reads them is not wired to any gate).",
        "median_full_net_pnl_over_cohort_winners": med(full_rows, "net_pnl"),
        "median_full_sharpe_over_cohort_winners": med(full_rows, "sharpe"),
        "median_historical_net_pnl_over_sampled_cases": med(agg["hist_rows_sample"], "net_pnl"),
        "positive_historical_case_share": sum(1 for r in agg["hist_rows_sample"]
                                              if r["net_pnl"] > 0) /
        float(len(agg["hist_rows_sample"]) or 1),
    }
    assertions = {
        "episodes_partition": agg["partition_all"],
        "pnl_decomposition": agg["pnl_decomp_all"],
        "coverage_complete": coverage_complete,
        "cohort_count_20": len(cohort_labels) == spec["expected"]["cohorts"],
        "strategy_grid_is_registered_product": agg["strategy_cells"] == strategy_product,
        "dca_grid_is_registered_product": agg["dca_cells"] == dca_product,
        "base_combinations_per_cohort_per_grid": cells_per_cohort_ok,
        "expected_case_evaluations": sum(coverage.values()) == spec["expected"]["expected_case_evaluations"],
        "layer0_equals_episodes": (agg["layer_totals"][0] == agg["episodes_full_total"]
                                   and agg["layer_totals"][0] > 0),
        "layer_histogram_nonempty": sum(agg["layer_totals"]) > 0,
        "no_entry_after_exhaustion": agg["no_entry_after_exhaustion"],
        "ending_equity_floor": agg["ending_equity_floor"],
        "selector_deterministic": agg["selector_stable"],
        "selector_historical_only": True,
        "both_legs_traded": agg["episodes_long_total"] > 0 and agg["episodes_short_total"] > 0,
        # registered requirement (contract 7.2 v1.3.1): a cost stress track that leaves the
        # numbers untouched is a dead parameter, not a passing robustness test.  The two
        # fee-based tracks are the ones that must move; the funding / lag / slippage tracks are
        # reported as (non-gating) diagnostics in `stress_track_delta_cases`.
        "cost_stress_not_noop": (agg["stress_delta"]["fee_2x"] > 0
                                 and agg["stress_delta"]["cost_attrition_40bps"] > 0),
    }
    return {
        "family_id": spec["family_id"], "round_id": spec["round_id"], "run_id": spec["run_id"],
        "engine_version": ENGINE_VERSION, "engine_semantics": ENGINE_SEMANTICS,
        "selector_version": SELECTOR_VERSION, "disposition_version": DISPOSITION_VERSION,
        "contract_semantics_version": CONTRACT_SEMANTICS_VERSION,
        "registered_domains": {
            "strategy": {"ema_pair": expected_axes["ema_pair"],
                         "walk_forward": expected_axes["walk_forward"],
                         "legal_cases_per_cohort": len(expected_axes["ema_pair"])
                         * len(expected_axes["walk_forward"])},
            "dca": {a: expected_axes[a] for a in DCA_AXES},
            "dca_base_quote": spec["dca_domain"]["base_quote"]},
        "coverage": coverage, "coverage_required": need, "coverage_complete": coverage_complete,
        "cohorts": cohort_labels, "cohort_count": len(cohort_labels),
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
        "disposition_mapping_version": disposition["mapping_version"],
        "survivor_evidence": survivors,
        "episode_direction_totals": {"long": agg["episodes_long_total"],
                                     "short": agg["episodes_short_total"]},
        "stress_track_delta_cases": {k: agg["stress_delta"][k] for k in agg["stress_delta"]},
        "descriptive_diagnostics": descriptive,
        "cross_cohort_note": "the 20 cohort verdicts are the family result; cross-cohort "
                             "medians are never a gate",
        "dca_layer_histogram": {"level_%02d" % k: agg["layer_totals"][k] for k in range(12)},
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
        for key in ("dca_domain", "expected", "selector_version", "disposition_version",
                    "params", "gates"):
            if key not in spec:
                raise SystemExit("run-spec is not a registered B v2 spec: missing %r" % key)
        if spec["selector_version"] != SELECTOR_VERSION or spec["disposition_version"] != DISPOSITION_VERSION:
            raise SystemExit("run-spec selector/disposition version %r/%r != engine %r/%r"
                             % (spec["selector_version"], spec["disposition_version"],
                                SELECTOR_VERSION, DISPOSITION_VERSION))
        if spec["params"].get("grid_size") != len(spec["params"]["ema_pair"]) * len(spec["params"]["walk_forward"]):
            raise SystemExit("run-spec params.grid_size != ema_pair x walk_forward (contract 7.2)")
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

        writers = {}
        handles = []
        for kind in COHORT_GRID_KINDS:
            path = os.path.join(attempt_dir, "artifacts", "grid_%s.csv" % kind)
            fh = open(path, "w", newline="")
            handles.append(fh)
            w = csv.DictWriter(fh, fieldnames=list(ROW_FIELDS))
            w.writeheader()
            writers[kind] = w
        agg = {"counts": {k: 0 for k in COHORT_GRID_KINDS},
               "per_cohort": {},
               "strategy_cells": set(), "dca_cells": set(),
               "pnl_decomp_all": True, "partition_all": True, "selector_stable": True,
               "no_entry_after_exhaustion": True, "ending_equity_floor": True,
               "episodes_long_total": 0, "episodes_short_total": 0, "episodes_full_total": 0,
               "layer_totals": [0] * 12, "stress_delta": {k: 0 for k in
                                                          ("fee_2x", "funding_2x",
                                                           "entry_delay_1_bar",
                                                           "slippage_2ticks",
                                                           "cost_attrition_40bps")},
               "cohort_labels": [], "hist_rows_sample": [],
               "full_rows_sample": [], "funding_coverage": {}, "winner_traces": {}}

        total = len(spec["data"]["symbols"]) * len(spec["data"]["timeframes"])
        done = 0
        cohort_results = []
        try:
            for symbol in spec["data"]["symbols"]:
                for tf in spec["data"]["timeframes"]:
                    label = "%s/%s" % (symbol, tf["raw_interval"])
                    cohort = Cohort(symbol, tf, spec["data"]["start"], spec["data"]["end"])
                    meta = instruments[symbol]
                    cohort.price_increment = meta["price_increment"]
                    cohort.taker_fee = meta["taker_fee"]
                    cohort.leverage = 1.0 / meta["margin_init"]
                    cohort.margin_maint = meta["margin_maint"]
                    ft, fr, excluded = load_funding(symbol, spec["data"]["start"], spec["data"]["end"])
                    cohort.funding, applied = funding_per_bar(cohort, ft, fr, 1.0)
                    agg["funding_coverage"][label] = {"official": applied,
                                                      "modeled_excluded": excluded}
                    run_log("cohort %s bars=%d days=%d funding_official=%d modeled_excluded=%d"
                            % (label, cohort.n, cohort.n_days, applied, excluded))
                    agg["cohort_labels"].append(label)
                    for k in COHORT_GRID_KINDS:
                        agg["per_cohort"][(label, k)] = 0
                    hist_rows, winner_row, winner_reason, winner_rows = run_cohort(
                        spec, cohort, run_log, writers, agg)
                    agg["hist_rows_sample"].extend(hist_rows[::53])
                    c_res = evaluate_cohort(spec, label, winner_row, winner_reason,
                                            hist_rows, winner_rows)
                    cohort_results.append(c_res)
                    if winner_row is not None:
                        # registered winner-cell carry-over check across every phase grid, plus
                        # the (non-gating) stress deltas that prove the tracks are not dead
                        full_w = winner_rows["full"]
                        agg["full_rows_sample"].append(full_w)
                        for k in agg["stress_delta"]:
                            other = winner_rows[k]
                            if abs(other["net_pnl"] - full_w["net_pnl"]) > 1e-9:
                                agg["stress_delta"][k] += 1
                    del hist_rows
                    done += 1
                    atomic_write_json(os.path.join(attempt_dir, "artifacts", "progress.json"),
                                      {"cohorts_done": done, "cohorts_total": total,
                                       "updated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                                       time.gmtime())})
        finally:
            for fh in handles:
                fh.flush()
                os.fsync(fh.fileno())
                fh.close()

        # the winner-cell selection trace is preserved as human-readable evidence of the
        # registered per-step re-selection protocol (one block per cohort winner)
        trace_written = 0
        try:
            with open(os.path.join(attempt_dir, "artifacts", "walk_forward_selection_trace.csv"),
                      "w", newline="") as fh:
                fields = ["cohort", "ema_fast", "ema_slow", "wf_train_days", "wf_test_days",
                          "spacing_pct", "size_multiplier", "breakeven_tp_pct",
                          "invalidation_pct", "step", "selected_fast", "selected_slow",
                          "selected_pair_index", "executed_pair_index", "train_sharpe",
                          "train_net_pnl", "test_net_pnl", "test_episodes", "test_fees",
                          "test_funding", "ending_equity"]
                w = csv.DictWriter(fh, fieldnames=fields)
                w.writeheader()
                for label in sorted(agg["winner_traces"]):
                    wt = agg["winner_traces"][label]
                    base = {"cohort": label, "ema_fast": wt["ema_fast"],
                            "ema_slow": wt["ema_slow"], "wf_train_days": wt["cell"][0],
                            "wf_test_days": wt["cell"][1],
                            "spacing_pct": wt["dca"]["spacing_pct"],
                            "size_multiplier": wt["dca"]["size_multiplier"],
                            "breakeven_tp_pct": wt["dca"]["breakeven_tp_pct"],
                            "invalidation_pct": wt["dca"]["invalidation_pct"]}
                    for st in wt["steps"]:
                        w.writerow(dict(base, **{k: st.get(k) for k in fields if k in st}))
                        trace_written += 1
        except OSError as exc:
            run_log("selection trace write skipped: %s" % exc)
        run_log("selection trace rows=%d" % trace_written)

        summary = summarize(spec, agg, cohort_results)
        summary["runtime_seconds"] = int(time.time() - started)
        summary["bins_build"] = {k: bins[k] for k in
                                 ("build_wall_seconds", "qlib_dir_bytes", "qlib_version")}
        summary["instrument_metadata"] = {s: instruments[s] for s in spec["data"]["symbols"]}
        summary["data_readback"] = bins["readback"]
        summary["funding_coverage"] = agg["funding_coverage"]
        summary["generated_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        summary.pop("_trace", None)
        atomic_write_json(os.path.join(attempt_dir, "result.json"), summary)
        atomic_write_json(os.path.join(attempt_dir, "artifacts", "dca_layer_histogram.json"),
                          summary["dca_layer_histogram"])
        atomic_write_json(os.path.join(attempt_dir, "artifacts", "assertions.json"),
                          summary["assertions"])
        atomic_write_json(os.path.join(attempt_dir, "artifacts", "cohort_results.json"),
                          cohort_results)
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
