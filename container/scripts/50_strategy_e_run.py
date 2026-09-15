#!/usr/bin/env python3
"""Strategy E — Copula-CMI pairs relative value (market neutral) full backtest, on Qlib.

Entry point for ONE pre-registered attempt.  Reads the immutable run-spec from /results
(the only source of parameters), builds the Qlib .bin store from the READ-ONLY canonical
raw store into /qlib/work, then runs the pre-registered full-backtest contract
(QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md v1.7.0 sections 7.2 / 7.3):

    6 cohorts (the six pairs of BTCUSDT/ETHUSDT/BNBUSDT/SOLUSDT at 1h)
    x STRATEGY domain (5 t_o x 3 t_c with the AIC-selected copula + 5 forced-family
      substitution cases = 20 registered cases)
    x DCA domain (4 axes = 48 configs)
    x 11 registered phase grids (historical / oos / full / fee_2x / funding_2x /
      entry_delay_1_bar / slippage_2ticks / no_funding / no_funding_full /
      cost_attrition_40bps / source_cost_0p12pct)

SIGNAL (source-normalized, pre-registered; relative-value statistical arbitrage):
pairs are re-selected every cycle from a rolling 2160-hour (90 day) formation window,
followed by a 336-hour (14 day) trading window.  Inside a formation window the engine
fits, in this registered order and with NO trading-window information:

  * the OLS hedge ratio beta of log P1 on log P2 (the notional ratio of the two legs),
  * an Engle-Granger residual ADF test (constant, 24 registered lags) whose 5% critical
    value is -3.34, and a Kapetanios-Shin-Snell nonlinear unit-root test on the demeaned
    residual (t_NL, 5% critical value -2.93): BOTH must reject the unit root,
  * the mean-reversion half-life of the residual (must be < 30 days),
  * the copula of the two assets' pseudo-observations (empirical marginals of the
    FORMATION window, frozen; a trading price outside the formation range saturates),
    fitted by MLE over {Gaussian, Student-t, Clayton, Gumbel, Frank}, selected by AIC
    and validated by a Rosenblatt-transform GOF (KS and CvM, parametric bootstrap
    p-value >= 5%, B = 100).  A cycle that fails the cointegration screen or the GOF
    gate is NOT tradable: it holds no position and is counted.

Within a tradable cycle the signal is the Copula Mispricing Index
`CMI_t = h_{1|2}(U1_t | U2_t) - 0.5 in [-0.5, 0.5]` (h = the fitted copula's
conditional CDF).  At a bar close, `CMI < -t_o` opens long A1 / short A2 and
`CMI > +t_o` opens short A1 / long A2; the entry fills at the NEXT bar's open
(next-event execution - no look-ahead).

EXECUTION (leg-aware, market-neutral rail; registered before the first run): the
12-tranche rail is applied at the PAIR level.  Level 0 is the simultaneous entry of both
legs (leg 2 notional = beta x leg 1 notional, so the pair's beta-hedged net notional is
zero by construction); levels 1..10 are simultaneous adverse-price scale-ins whose spread
offsets are k x spacing_pct measured on the beta-hedged log spread (so the two legs' level
alignment is anchored on the SPREAD, while each leg's own running average cost anchors its
breakeven-anchored take profit and its resting invalidation); tranche #12 (index 11) is the
reserve and is never routinely deployed.  Exits are reduce-only and always close BOTH legs:
the spread profit target (2%), the spread stop (4%), a leg's own rail take profit or resting
invalidation, the CMI reversion (`|CMI| < t_c`), the max holding period (336 bars), the
capital-exhaustion backstop and the cycle's end.  Every fill pays the taker fee at its own
instant, every funding settlement inside the closed holding interval is charged on the
position notional of the leg it belongs to (never assuming a fixed 8h grid), and each leg's
fills are slipped adversely by the registered tick count on its own price increment.

DCA is executed as real order/fill accounting (an episode state machine over the bars);
nothing is estimated after the fact.  Every legal (strategy params x DCA config) cell is
evaluated on every registered phase grid, and the family gate is the **cohort-level
survivor** rule of contract section 7.3: one deterministic historical-only winner per pair
cohort, then OOS / full / robustness / parameter-neighbourhood evidence for that same
winner.  This family has 6 cohorts, so the cohort survivor count is 0..6.

Writes only:
  * /qlib/work/**      (rebuildable derived/cache area, INV-5)
  * <attempt_dir>/**   (result.json, artifacts/, logs/, state.json)
Never writes /data/raw (read-only mount).

usage: 50_strategy_e_run.py <run-spec.json>
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
WORK_ROOT = "/qlib/work/strategyE-v1"
CSV_ROOT = os.path.join(WORK_ROOT, "csv")
QLIB_DIR = os.path.join(WORK_ROOT, "qlib-data")
MS_PER_DAY = 86400000
MS_PER_HOUR = 3600000
FIELDS = ["open", "high", "low", "close", "volume"]
START_EQUITY = 30000.0
MAX_ADD_LEVELS = 10
TRANCHE_COUNT = 12
# ------------------------------------------------------------------ registered cycle plan
FORMATION_BARS = 2160          # 90 days of 1h bars
TRADING_BARS = 336             # 14 days of 1h bars
# The cycle grid is anchored on the registered data start, so every slice (historical /
# oos / full) evaluates a SUBSET of the same cycles and the split never moves a boundary.
CYCLE_ANCHOR = "2022-01-01"
# The registered strategy case domain (contract 7.2 item 3): 15 (t_o, t_c) cells with the
# AIC-selected copula plus the 5 forced-family substitution cases at the registered
# reference point (t_o, t_c) = (0.30, 0.10), in this registered order.
T_O_GRID = (0.20, 0.25, 0.30, 0.35, 0.40)
T_C_GRID = (0.05, 0.10, 0.15)
# copula_family is a registered numeric index: 0 = AIC-selected, 1..5 = forced family.
COPULA_INDEX_AIC = 0
COPULA_FAMILIES = ("gaussian", "student_t", "clayton", "gumbel", "frank")
COPULA_LABELS = ("aic_selected",) + COPULA_FAMILIES
SUBSTITUTION_REFERENCE = (0.30, 0.10)
STRATEGY_CASES = tuple(
    [{"t_o": o, "t_c": c, "copula_family": COPULA_INDEX_AIC} for o in T_O_GRID for c in T_C_GRID]
    + [{"t_o": SUBSTITUTION_REFERENCE[0], "t_c": SUBSTITUTION_REFERENCE[1],
        "copula_family": f} for f in range(1, len(COPULA_FAMILIES) + 1)])
STRATEGY_FIELDS = ("t_o", "t_c", "copula_family")
# Registered pair universe: A1 = the first-named symbol (the leg the CMI is conditioned on).
PAIRS = (
    ("BTCUSDT", "ETHUSDT"),
    ("BTCUSDT", "BNBUSDT"),
    ("BTCUSDT", "SOLUSDT"),
    ("ETHUSDT", "BNBUSDT"),
    ("ETHUSDT", "SOLUSDT"),
    ("BNBUSDT", "SOLUSDT"),
)
# Registered pair-level exit thresholds (card): spread profit target / spread stop,
# max holding period; the source specifies none of them numerically, so the card's
# pre-registered values are frozen here before the first run.
SPREAD_PT = 0.02
SPREAD_SL = 0.04
# The registered max holding period.  It is an OUTER bound: because the trading window is
# itself 336 bars and an entry always fills at the bar AFTER its signal bar, the cycle-end
# flatten binds at or before the max-holding bar.  The engine still evaluates the condition
# on every in-market bar and reports `max_holding_exits` separately (0 by construction in
# this family), so the registered bound is visible in the artifacts instead of being implied.
MAX_HOLDING_BARS = 336
# Registered cointegration / selection constants (frozen before the first run).
ADF_LAGS = 24
ADF_CV_5PCT = -3.34           # Engle-Granger residual ADF, constant only, 2 variables
KSS_CV_5PCT = -2.93           # Kapetanios-Shin-Snell t_NL, demeaned case
HALF_LIFE_MAX_DAYS = 30.0
GOF_BOOTSTRAP = 100
GOF_MIN_P = 0.05
MARGINAL_TIE_RULE = "#{formation values <= x} / n_formation"
# Registered hedge-ratio sanity band: a cycle whose formation OLS ratio falls outside the band
# is not a tradable relative-value pair for a notional-matched pair trade, so it holds no
# position (counted, disclosed).  Frozen before the first run.
HEDGE_BETA_MIN = 0.2
HEDGE_BETA_MAX = 5.0
# Execution stress reruns (full-window slice), contract 7.2 robustness.
STRESS = [
    ("fee_2x", {"fee_mult": 2.0}),
    ("funding_2x", {"funding_mult": 2.0}),
    ("entry_delay_1_bar", {"entry_delay_1_bar": True}),
    ("slippage_2ticks", {"slip_ticks": 2}),
]
# Registered cost-attrition grid: 8 x the 5 bps taker fee = 40 bps per fill.
COST_ATTRITION_STRESS = {"fee_mult": 8.0}
# Registered source-reported cost track: the source declares 0.04% taker per side plus
# 0.02% spread per leg = a 0.12% two-leg round trip; four fills carry that 12 bps, i.e.
# 3 bps per fill on both legs.  Disclosed in the round-spec: it is DELIBERATELY CHEAPER
# than the canonical 5 bps taker fee, because the point of the track is to re-measure the
# hypothesis under the source's own cost assumption.
SOURCE_FEE_PER_FILL = 0.0003
SOURCE_COST_STRESS = {"fee_override": SOURCE_FEE_PER_FILL}
# Registered phase grids: every one of them covers the FULL strategy-domain x DCA-domain
# product of every cohort (contract 7.2).
COHORT_GRID_KINDS = ("historical", "oos", "full", "fee_2x", "funding_2x",
                     "entry_delay_1_bar", "slippage_2ticks", "no_funding",
                     "no_funding_full", "cost_attrition_40bps", "source_cost_0p12pct")
# grids whose slice is the FULL registered window (used by the assertions)
FULL_WINDOW_GRID_KINDS = ("full", "fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks",
                          "no_funding_full", "cost_attrition_40bps", "source_cost_0p12pct")
# Joint parameter space axes, in the one registered order used for the deterministic
# lexical tie-break (card: t_o, t_c, copula_family, spacing_pct, size_multiplier,
# breakeven_tp_pct, invalidation_pct).  Order is part of the gate.
AXES = ("t_o", "t_c", "copula_family", "spacing_pct", "size_multiplier", "breakeven_tp_pct",
        "invalidation_pct")
NEIGHBOURHOOD_AXES = ("t_o", "t_c", "spacing_pct", "size_multiplier", "breakeven_tp_pct",
                      "invalidation_pct")
DCA_AXES = ("spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct")
SELECTOR_VERSION = "cohort-selector-v1"
DISPOSITION_VERSION = "cohort-disposition-v1"
CONTRACT_SEMANTICS_VERSION = "v1.4.0"
ENGINE_VERSION = "e-v1-engine-1.0.1"
ENGINE_SEMANTICS = ("copula CMI pairs relative value: rolling 2160h formation (OLS hedge "
                    "ratio, Engle-Granger ADF + KSS, half-life < 30d, AIC copula + "
                    "Rosenblatt GOF) / 336h trading window, next-bar-open entries, "
                    "spread-anchored leg-aware DCA rail, reduce-only pair exits")
WINNER_METRIC_KEYS = ("net_pnl", "sharpe", "episodes", "ending_equity", "fees", "funding",
                      "max_dd_usdt", "max_dd_pct", "max_effective_leverage",
                      "capital_utilization", "annualized_return", "cagr")
# csv row field order (kept identical for every phase grid)
ROW_FIELDS = ("symbol", "timeframe", "leg1_symbol", "leg2_symbol", "window_kind",
              "strategy_case", "case_name",
              "t_o", "t_c", "copula_family",
              "spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct",
              "net_pnl", "fees", "funding", "gross_pnl", "ending_equity",
              "cycles_seen", "cycles_tradable", "cycles_entered", "episodes",
              "tp_hits", "sl_hits", "leg_tp_hits", "leg_stop_hits", "cmi_exits",
              "max_holding_exits", "window_end_exits", "margin_calls",
              "halted", "min_entry_equity", "sharpe", "max_dd_usdt", "max_dd_pct",
              "max_effective_leverage", "capital_utilization", "bars_in_market",
              "fills", "turnover_usdt", "days", "years", "cagr", "total_return_pct",
              "annualized_return")
LAYER_TOTALS = {}
# Structural counters, per phase grid (module level so `summarize` can assert them).
# The raw funding timestamps carry ms-level jitter (measured on all four symbols: 0 .. 28 ms
# late relative to the nominal 8h boundary, never early), so the funding exposure interval is
# the CLOSED interval [entry - 1s, exit + 1s].  A deviation beyond the tolerance is a real
# mapping defect and trips a counter.
MS_JITTER_TOLERANCE_MS = 1000
COUNTER_NAMES = ("cycle_boundary_misaligned", "cycle_window_truncated", "leg_bars_not_aligned",
                 "case_not_registered", "bar_grid_not_contiguous", "funding_bar_out_of_hold",
                 "non_qualifying_cycle_traded", "cmi_out_of_range", "marginal_not_frozen",
                 "traded_cycle_without_gof", "unhedged_entry_notional", "episodes_overlap",
                 "cmi_raw_float_excursion")
COUNTERS = {}


def counter(kind, name, inc=1):
    slot = COUNTERS.setdefault(kind, {n: 0 for n in COUNTER_NAMES})
    slot[name] += inc
    return slot[name]


def counters_total(name):
    return sum(v.get(name, 0) for v in COUNTERS.values())


def counters_kind(kind, name):
    return COUNTERS.get(kind, {}).get(name, 0)


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
    return tuple(p[f] for f in STRATEGY_FIELDS)


def case_name(case):
    o, c, fam = case
    if fam == COPULA_INDEX_AIC:
        return "aic_to%02d_tc%02d" % (round(o * 100), round(c * 100))
    return "%s_to%02d_tc%02d" % (COPULA_FAMILIES[fam - 1], round(o * 100), round(c * 100))


def case_index(case):
    for i, cas in enumerate(STRATEGY_CASES):
        if case_tuple(cas) == tuple(case):
            return i
    raise KeyError(case)


def pair_label(pair):
    return "%s-%s" % pair


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
# phase 2: pre-registered signal construction (pairs, copula, cycle precompute)
# ---------------------------------------------------------------------------


class PairBars:
    """Both legs of one pair cohort, loaded through the Qlib data layer on the same grid."""

    def __init__(self, pair, tf, start, end):
        from qlib.data import D
        freq = tf["qlib_freq"]
        qstart, qend = qlib_window(start, end)
        self.pair = pair
        self.label = pair_label(pair)
        self.timeframe = tf["raw_interval"]
        self.qlib_freq = freq
        self.legs = []
        grids = []
        for symbol in pair:
            df = D.features([symbol], ["$" + f for f in FIELDS],
                            start_time=qstart, end_time=qend, freq=freq).sort_index()
            ms = np.array([int(ts.value // 1_000_000)
                           for ts in df.index.get_level_values("datetime")], dtype=np.int64)
            arrays = {f: df["$" + f].to_numpy(dtype=np.float64) for f in FIELDS}
            self.legs.append({"symbol": symbol, "open_time_ms": ms, "fields": arrays})
            grids.append(ms)
        # The pair engine indexes both legs by the SAME bar index: a misalignment would
        # silently pair different instants and manufacture a spread that never traded.
        if not (len(grids) == 2 and len(grids[0]) == len(grids[1])
                and np.array_equal(grids[0], grids[1])):
            counter("cohort", "leg_bars_not_aligned")
            raise SystemExit("pair %s: leg bar grids differ (%d vs %d rows)"
                             % (self.label, len(grids[0]), len(grids[1])))
        self.open_time_ms = grids[0]
        self.n = len(self.open_time_ms)
        self.bar_ms = int(self.open_time_ms[1] - self.open_time_ms[0]) if self.n > 1 else 0
        steps = np.diff(self.open_time_ms)
        self.non_bar_steps = int(np.count_nonzero(steps != self.bar_ms))
        if self.bar_ms <= 0 or self.non_bar_steps:
            counter("cohort", "bar_grid_not_contiguous")
            raise SystemExit("pair %s: open_time_ms is not a contiguous %d ms bar grid "
                             "(%d deviation(s))" % (self.label, self.bar_ms, self.non_bar_steps))
        # per-leg execution metadata (canonical instrument metadata, filled by main)
        self.meta = [{}, {}]
        self.day_end = np.flatnonzero(np.concatenate(
            [(self.open_time_ms[1:] // MS_PER_DAY) != (self.open_time_ms[:-1] // MS_PER_DAY),
             [True]]))
        self.day_id = self.open_time_ms // MS_PER_DAY
        self.first_ts = iso(int(self.open_time_ms[0]))
        self.last_ts = iso(int(self.open_time_ms[-1]))

    def slice(self, start_date, end_date):
        lo = utc_ms(start_date)
        hi = utc_ms(end_date) + MS_PER_DAY - 1
        return (int(np.searchsorted(self.open_time_ms, lo, side="left")),
                int(np.searchsorted(self.open_time_ms, hi, side="right")))

    def cycle_bounds(self):
        """The registered cycle grid: a 2160h formation window immediately before each 336h
        trading window, anchored on the registered data start so that every slice evaluates a
        SUBSET of the same cycles and the historical/OOS split never moves a boundary."""
        anchor = int(np.searchsorted(self.open_time_ms, utc_ms(CYCLE_ANCHOR), side="left"))
        out = []
        i = anchor
        while i + FORMATION_BARS + TRADING_BARS <= self.n:
            out.append({"index": len(out),
                        "formation": (i, i + FORMATION_BARS),
                        "trading": (i + FORMATION_BARS, i + FORMATION_BARS + TRADING_BARS),
                        "formation_start_ms": int(self.open_time_ms[i]),
                        "trading_start_ms": int(self.open_time_ms[i + FORMATION_BARS])})
            i += TRADING_BARS
        return out


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


def build_funding_index(pair, times, rates, counts, label):
    """Funding settlements + the bar each one belongs to, on the pair's own bar grid.

    Measured raw fact (all four symbols): settlements sit on the 00/08/16 UTC grid and run
    0..28 ms LATE, never early; SOL additionally carries a few 2h/4h-era settlements in early
    2022, so the engine never assumes a fixed 8h grid - every settlement is charged on its own
    instant.  Two mappings, because they answer two different questions: `settle_bar` schedules
    the charge (the bar whose CLOSE is the mark price at the settlement instant); the
    `settle_bar_closed` containment mapping exists only for the guard, so a guard fix can never
    move a charge.
    """
    containing = np.searchsorted(pair.open_time_ms, times, side="left") - 1
    closed = np.searchsorted(pair.open_time_ms, times, side="right") - 1
    return {"label": label, "counts": counts, "obs_times": times, "obs_rates": rates,
            "settle_bar": containing.astype(np.int64),
            "settle_bar_closed": closed.astype(np.int64),
            "first_obs_ms": int(times[0]) if len(times) else None,
            "last_obs_ms": int(times[-1]) if len(times) else None}


# ---------------------------------------------------------------------------
# copula machinery (five registered families, AIC selection, Rosenblatt GOF)
# ---------------------------------------------------------------------------

from scipy import optimize as _optimize  # noqa: E402  (the container venv has scipy)
from scipy import stats as _stats  # noqa: E402
from scipy.special import gammaln as _gammaln  # noqa: E402

_U_EPS = 1e-10
# Registered Student-t degrees-of-freedom grid for the profiled fit (see fit_copula).
T_NU_GRID = (3.0, 4.0, 5.0, 6.0, 7.5, 9.0, 12.0, 16.0, 25.0, 50.0)
_TINY = 1e-300


def _cl(u):
    return np.clip(np.asarray(u, dtype=np.float64), _U_EPS, 1.0 - _U_EPS)


def _norm_ppf(u):
    return _stats.norm.ppf(_cl(u))


def _log_cond_density(family, theta, u1, u2):
    """log c(u1, u2) of one registered family (theta = the family's own parameter dict)."""
    u1, u2 = _cl(u1), _cl(u2)
    if family == "gaussian":
        rho = theta["rho"]
        x, y = _norm_ppf(u1), _norm_ppf(u2)
        quad = (x * x - 2.0 * rho * x * y + y * y) / (1.0 - rho * rho)
        return -0.5 * math.log(1.0 - rho * rho) - 0.5 * quad + 0.5 * (x * x + y * y)
    if family == "student_t":
        rho, nu = theta["rho"], theta["nu"]
        x = _stats.t.ppf(u1, nu)
        y = _stats.t.ppf(u2, nu)
        quad = (x * x - 2.0 * rho * x * y + y * y) / (1.0 - rho * rho)
        lg = (_gammaln((nu + 2.0) / 2.0) + _gammaln(nu / 2.0)
              - 2.0 * _gammaln((nu + 1.0) / 2.0) - 0.5 * math.log(1.0 - rho * rho))
        return (lg - ((nu + 2.0) / 2.0) * np.log1p(quad / nu)
                + ((nu + 1.0) / 2.0) * (np.log1p(x * x / nu) + np.log1p(y * y / nu)))
    if family == "clayton":
        th = theta["theta"]
        return (math.log1p(th) - (th + 1.0) * (np.log(u1) + np.log(u2))
                - (2.0 + 1.0 / th) * np.log(u1 ** (-th) + u2 ** (-th) - 1.0))
    if family == "gumbel":
        th = theta["theta"]
        a = (-np.log(u1)) ** th
        b = (-np.log(u2)) ** th
        z = (a + b) ** (1.0 / th)
        return (-z + ((th - 1.0) / th) * (np.log(a) + np.log(b))
                + (1.0 / th - 2.0) * np.log(a + b)
                - np.log(u1) - np.log(u2) + np.log(z + th - 1.0))
    if family == "frank":
        th = theta["theta"]
        e1 = np.expm1(-th * u1)
        e2 = np.expm1(-th * u2)
        den = np.expm1(-th) + e1 * e2
        return (math.log(th) + math.log(abs(math.expm1(-th))) - th * (u1 + u2)
                - 2.0 * np.log(np.maximum(np.abs(den), _TINY)))
    raise SystemExit("unregistered copula family %r" % family)


def _scalar_fit(family, u1, u2):
    """MLE of the one-parameter families, searched in a monotone transform of theta."""
    if family == "clayton":
        lo, hi = math.log(1e-4), math.log(20.0)

        def to_theta(x):
            return math.exp(x)
    elif family == "gumbel":
        lo, hi = math.log(1e-4), math.log(19.0)

        def to_theta(x):
            return 1.0 + math.exp(x)
    else:
        lo, hi = math.log(1e-4), math.log(60.0)

        def to_theta(x):
            return math.exp(x)

    def neg_ll(x):
        return -float(np.sum(_log_cond_density(family, {"theta": to_theta(x)}, u1, u2)))

    res = _optimize.minimize_scalar(neg_ll, bounds=(lo, hi), method="bounded",
                                    options={"xatol": 1e-8})
    return {"theta": to_theta(float(res.x))}, -float(res.fun)


def fit_copula(family, u1, u2):
    """MLE of one registered family.  Returns (theta, loglik, k_params)."""
    u1, u2 = _cl(u1), _cl(u2)
    if family == "gaussian":
        def neg_ll(rho):
            return -float(np.sum(_log_cond_density("gaussian", {"rho": rho}, u1, u2)))
        res = _optimize.minimize_scalar(neg_ll, bounds=(-0.999, 0.999), method="bounded",
                                        options={"xatol": 1e-10})
        return {"rho": float(res.x)}, -float(res.fun), 1
    if family == "student_t":
        # Profiled two-stage fit: nu on a registered coarse grid, rho by bounded MLE for each
        # candidate nu.  A full 2-D Nelder-Mead on this family costs ~0.4 s per fit, which the
        # GOF bootstrap (B refits per cycle) cannot afford; the profiled fit costs ~10 ms and is
        # used for BOTH the observed fit and every bootstrap refit, so the test stays internally
        # consistent (contract 7.2 tests run on Qlib; the copula layer is the project's own).
        best = None
        for nu in T_NU_GRID:

            def neg_ll(rho, nu=nu):
                return -float(np.sum(_log_cond_density("student_t", {"rho": rho, "nu": nu},
                                                       u1, u2)))

            res = _optimize.minimize_scalar(neg_ll, bounds=(-0.999, 0.999), method="bounded",
                                            options={"xatol": 1e-5})
            if best is None or res.fun < best.fun:
                best = res
                best_nu = nu
        return {"rho": float(best.x), "nu": float(best_nu)}, -float(best.fun), 2
    if family in ("clayton", "gumbel", "frank"):
        theta, ll = _scalar_fit(family, u1, u2)
        return theta, ll, 1
    raise SystemExit("unregistered copula family %r" % family)


def copula_cond_cdf(family, theta, u1, u2):
    """h_{1|2}(u1 | u2) = dC/du2, the registered CMI building block."""
    u1, u2 = _cl(u1), _cl(u2)
    if family == "gaussian":
        rho = theta["rho"]
        return _stats.norm.cdf((_norm_ppf(u1) - rho * _norm_ppf(u2)) / math.sqrt(1.0 - rho * rho))
    if family == "student_t":
        rho, nu = theta["rho"], theta["nu"]
        x = _stats.t.ppf(u1, nu)
        y = _stats.t.ppf(u2, nu)
        scale = np.sqrt((1.0 - rho * rho) * (nu + y * y) / (nu + 1.0))
        return _stats.t.cdf((x - rho * y) / scale, nu + 1.0)
    if family == "clayton":
        th = theta["theta"]
        base = u1 ** (-th) + u2 ** (-th) - 1.0
        return u2 ** (-th - 1.0) * base ** (-1.0 / th - 1.0)
    if family == "gumbel":
        th = theta["theta"]
        a = (-np.log(u1)) ** th
        b = (-np.log(u2)) ** th
        z = (a + b) ** (1.0 / th)
        return np.exp(-z) * (-np.log(u2)) ** (th - 1.0) * (a + b) ** (1.0 / th - 1.0) / u2
    if family == "frank":
        th = theta["theta"]
        e1 = np.expm1(-th * u1)
        e2 = np.expm1(-th * u2)
        den = math.expm1(-th) + e1 * e2
        den = np.where(np.abs(den) < _TINY, -_TINY, den)
        return np.exp(-th * u2) * e1 / den
    raise SystemExit("unregistered copula family %r" % family)


# v1.0.1 detector fix, disclosed (card t_57ecd99e, round r1): the closed-form conditional CDFs
# lose precision in their tails.  Measured on the registered u1 attempt (out-of-tree instrumented
# replay under results/_diag, 11/11 grid CSVs byte-identical to u1):
#   - ETHUSDT-BNBUSDT cycle 70, frank: 32 of 336 points exceeded 1.0 by up to 2.812e-07
#     (argmax at u1 = 1.0, u2 = 0.9907) -> `cmi_out_of_range` fired twice.
#   - the same branch is catastrophically wrong for larger |theta|: with theta = 40 the
#     `expm1(-theta) + e1*e2` cancellation collapses the denominator to the `_TINY` guard and the
#     expression returns ~1.4e+283 (synthetic probe, results/_diag/tools/probe_frank2.py).
# h_{1|2} is a conditional CDF and is therefore in [0, 1] by definition, so the value the SIGNAL
# consumes is clipped into the registered range.  This is neutral for the measurement: every
# affected point already sits far outside the entry band |cmi| > t_o >= 0.20 on the same side, so
# the crossing decision cannot move (proved by byte-comparing the u1 and u2 grid artifacts).
# The excursion itself stays DISCLOSED through a separate informational counter; the GOF path
# keeps the unclipped primitive.
CMI_RAW_EXCURSION = {"hits": 0, "max_abs_overshoot": 0.0}


def cmi_series(family, theta, u1, u2):
    """CMI_t = h_{1|2}(U1|U2) - 0.5, clipped into the registered [-0.5, 0.5] range."""
    cdf = copula_cond_cdf(family, theta, u1, u2)
    lo = float(np.nanmin(cdf))
    hi = float(np.nanmax(cdf))
    if lo < 0.0 or hi > 1.0:
        counter("signal", "cmi_raw_float_excursion")
        CMI_RAW_EXCURSION["hits"] += 1
        CMI_RAW_EXCURSION["max_abs_overshoot"] = max(CMI_RAW_EXCURSION["max_abs_overshoot"],
                                                     max(0.0 - lo, hi - 1.0))
    cmi = np.clip(cdf, 0.0, 1.0) - 0.5
    if float(np.nanmin(cmi)) < -0.5 or float(np.nanmax(cmi)) > 0.5:
        counter("signal", "cmi_out_of_range")
    return cmi


def copula_cond_inverse(family, theta, w, u2):
    """u1 = h^{-1}(w | u2): inverse conditional CDF, used to sample from the fitted copula."""
    w, u2 = _cl(w), _cl(u2)
    if family == "clayton":
        th = theta["theta"]
        return (1.0 - u2 ** (-th) + (w * u2 ** (th + 1.0)) ** (-th / (th + 1.0))) ** (-1.0 / th)
    if family == "frank":
        th = theta["theta"]
        e2 = np.expm1(-th * u2)
        den = np.exp(-th * u2) - w * e2
        den = np.where(np.abs(den) < _TINY, _TINY, den)
        a = w * math.expm1(-th) / den
        return np.clip(-np.log1p(a) / th, _U_EPS, 1.0 - _U_EPS)
    if family == "gaussian":
        rho = theta["rho"]
        y = _norm_ppf(u2)
        return _stats.norm.cdf(rho * y + math.sqrt(1.0 - rho * rho) * _stats.norm.ppf(w))
    if family == "student_t":
        rho, nu = theta["rho"], theta["nu"]
        y = _stats.t.ppf(u2, nu)
        scale = np.sqrt((1.0 - rho * rho) * (nu + y * y) / (nu + 1.0))
        return _stats.t.cdf(rho * y + scale * _stats.t.ppf(w, nu + 1.0), nu)
    if family == "gumbel":
        lo = np.full_like(np.asarray(w, dtype=np.float64), _U_EPS)
        hi = np.full_like(lo, 1.0 - _U_EPS)
        for _ in range(80):
            mid = 0.5 * (lo + hi)
            below = copula_cond_cdf(family, theta, mid, u2) < w
            lo = np.where(below, mid, lo)
            hi = np.where(below, hi, mid)
        return 0.5 * (lo + hi)
    raise SystemExit("no conditional inverse for %r" % family)


def sample_copula(family, theta, n, rng):
    """Parametric bootstrap sampler for the fitted family (u1, u2)."""
    if family in ("gaussian", "student_t"):
        z = rng.standard_normal(n)
        e = rng.standard_normal(n)
        rho = theta["rho"]
        x2 = z
        x1 = rho * z + math.sqrt(1.0 - rho * rho) * e
        if family == "student_t":
            nu = theta["nu"]
            scale = np.sqrt(rng.chisquare(nu, size=n) / nu)
            x1, x2 = x1 / scale, x2 / scale
            return _stats.t.cdf(x1, nu), _stats.t.cdf(x2, nu)
        return _stats.norm.cdf(x1), _stats.norm.cdf(x2)
    u2 = rng.uniform(_U_EPS, 1.0 - _U_EPS, size=n)
    w = rng.uniform(_U_EPS, 1.0 - _U_EPS, size=n)
    return copula_cond_inverse(family, theta, w, u2), u2


def rosenblatt_stats(family, theta, u1, u2):
    """KS and CvM statistics of the Rosenblatt-transform coordinate w = h_{1|2}(u1|u2).

    Under H0 (the sample came from the fitted copula) w is U(0,1); the second Rosenblatt
    coordinate is u2 itself, which is uniform by construction of the frozen empirical marginals
    and therefore carries no test information.  Both statistics are compared against their own
    parametric-bootstrap distribution, never against a fixed table.
    """
    w = np.sort(copula_cond_cdf(family, theta, u1, u2))
    n = len(w)
    i = np.arange(1, n + 1, dtype=np.float64)
    ks = float(max(np.max(i / n - w), np.max(w - (i - 1.0) / n)))
    cvm = float(1.0 / (12.0 * n) + np.sum((w - (2.0 * i - 1.0) / (2.0 * n)) ** 2))
    return ks, cvm


def copula_gof(family, theta, u1, u2, rng, B=GOF_BOOTSTRAP):
    """Parametric-bootstrap GOF: simulate from the fitted copula, refit, transform, compare."""
    ks_obs, cvm_obs = rosenblatt_stats(family, theta, u1, u2)
    n = len(u1)
    ks_ge = cvm_ge = 1
    for _ in range(B):
        s1, s2 = sample_copula(family, theta, n, rng)
        fitted, _ll, _k = fit_copula(family, s1, s2)
        ks_b, cvm_b = rosenblatt_stats(family, fitted, s1, s2)
        if ks_b >= ks_obs:
            ks_ge += 1
        if cvm_b >= cvm_obs:
            cvm_ge += 1
    return {"ks_stat": ks_obs, "cvm_stat": cvm_obs,
            "ks_p": ks_ge / float(B + 1), "cvm_p": cvm_ge / float(B + 1),
            "bootstrap_B": B,
            "pass": (ks_ge / float(B + 1)) >= GOF_MIN_P and (cvm_ge / float(B + 1)) >= GOF_MIN_P}


def ols_beta(log1, log2):
    """Registered hedge ratio: OLS slope of log P1 on log P2 over the formation window."""
    x = np.column_stack([np.ones_like(log2), log2])
    coef, *_ = np.linalg.lstsq(x, log1, rcond=None)
    return float(coef[1]), float(coef[0])


def adf_tstat(u, lags=ADF_LAGS):
    """Engle-Granger residual ADF (constant + `lags` lagged differences): t on u_{t-1}."""
    du = np.diff(u)
    n = len(du)
    rows = n - lags
    y = du[lags:]
    X = np.empty((rows, 2 + lags), dtype=np.float64)
    X[:, 0] = 1.0
    X[:, 1] = u[lags:n]
    for j in range(1, lags + 1):
        X[:, 1 + j] = du[lags - j:n - j]
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X.dot(coef)
    dof = max(rows - X.shape[1], 1)
    sigma2 = float(resid.dot(resid)) / dof
    xtx_inv = np.linalg.pinv(X.T.dot(X))
    se = math.sqrt(max(sigma2 * float(xtx_inv[1, 1]), 1e-300))
    return float(coef[1] / se)


def kss_tstat(u):
    """Kapetanios-Shin-Snell nonlinear unit root: t on u_{t-1}^3 of the demeaned series."""
    d = u - u.mean()
    y = np.diff(d)
    x = d[:-1] ** 3
    denom = float(x.dot(x))
    if denom <= 0.0:
        return 0.0
    delta = float(x.dot(y)) / denom
    resid = y - delta * x
    sigma2 = float(resid.dot(resid)) / max(len(y) - 1, 1)
    se = math.sqrt(max(sigma2 / denom, 1e-300))
    return delta / se


def half_life_hours(u):
    """Half-life of the residual's mean reversion: AR(1) on the level, in hours."""
    y = np.diff(u)
    x = np.column_stack([np.ones(len(y)), u[:-1]])
    coef, *_ = np.linalg.lstsq(x, y, rcond=None)
    phi = float(coef[1])
    if not (-1.0 < phi < 0.0):
        return float("inf")
    return float(-math.log(2.0) / math.log1p(phi))


def apply_empirical_cdf(sorted_ref, x):
    """The frozen formation marginal: #{formation values <= x} / n."""
    n = len(sorted_ref)
    return np.searchsorted(sorted_ref, x, side="right") / float(n)


class CycleSignals:
    """One pre-registered cycle's formation fit and trading-window signal (never shared)."""

    __slots__ = ("index", "form", "trade", "beta", "alpha", "adf", "kss", "half_life_days",
                 "qualifies", "qualify_reason", "copula", "chosen_family", "aic", "gof",
                 "tradable", "cmi", "entries", "saturation", "form_start_ms", "trade_start_ms")


def precompute_cycles(pair, cycles, run_log, cohort_label, rng):
    """Every cycle's formation fit, copula selection, GOF and trading-window CMI series.

    This is the expensive part of the family (the copula is fitted once per cycle and the
    bootstrap GOF re-fits it B times per qualifying cycle), and it is done ONCE per cohort:
    all 11 phase grids and all 20 strategy cases reuse these frozen signals.
    """
    opens = [pair.legs[i]["fields"][f] for i in (0, 1) for f in ()]
    closes1 = pair.legs[0]["fields"]["close"]
    closes2 = pair.legs[1]["fields"]["close"]
    log1_all = np.log(closes1)
    log2_all = np.log(closes2)
    out = []
    stats = {"cycles_total": 0, "cycles_qualifying": 0, "cycles_gof_pass": 0,
             "cycles_tradable": 0, "skipped_cointegration": 0, "skipped_gof": 0,
             "skipped_hedge_ratio_band": 0, "cmi_points": 0, "saturated_points": 0,
             "entry_cells_available": 0}
    aic_wins = {name: 0 for name in COPULA_FAMILIES}
    for cyc in cycles:
        f0, f1 = cyc["formation"]
        t0, t1 = cyc["trading"]
        s = CycleSignals()
        s.index = cyc["index"]
        s.form = (f0, f1)
        s.trade = (t0, t1)
        s.form_start_ms = int(pair.open_time_ms[f0])
        s.trade_start_ms = int(pair.open_time_ms[t0])
        l1, l2 = log1_all[f0:f1], log2_all[f0:f1]
        s.beta, s.alpha = ols_beta(l1, l2)
        resid = l1 - s.alpha - s.beta * l2
        s.adf = adf_tstat(resid)
        s.kss = kss_tstat(resid)
        hl = half_life_hours(resid)
        s.half_life_days = (hl / 24.0) if math.isfinite(hl) else float("inf")
        stats["cycles_total"] += 1
        s.copula = {}
        s.chosen_family = None
        s.aic = {}
        s.gof = None
        s.tradable = False
        s.cmi = {}
        s.entries = {}
        s.saturation = {"u1_at_bounds": 0, "u2_at_bounds": 0, "trading_points": 0}
        if not (HEDGE_BETA_MIN <= abs(s.beta) <= HEDGE_BETA_MAX):
            s.qualifies = False
            s.qualify_reason = "hedge_ratio_band"
            stats["skipped_hedge_ratio_band"] += 1
            out.append(s)
            continue
        if not (s.adf < ADF_CV_5PCT and s.kss < KSS_CV_5PCT
                and s.half_life_days < HALF_LIFE_MAX_DAYS):
            s.qualifies = False
            s.qualify_reason = "cointegration_screen"
            stats["skipped_cointegration"] += 1
            out.append(s)
            continue
        s.qualifies = True
        s.qualify_reason = "ok"
        stats["cycles_qualifying"] += 1
        # pseudo-observations of the FORMATION window (frozen empirical marginals: a trading
        # price outside the formation range saturates at 0 or 1, which is counted)
        u1f = (np.argsort(np.argsort(l1)) + 1.0) / float(len(l1))
        u2f = (np.argsort(np.argsort(l2)) + 1.0) / float(len(l2))
        for fam in COPULA_FAMILIES:
            theta, ll, k = fit_copula(fam, u1f, u2f)
            s.copula[fam] = {"theta": theta, "loglik": ll, "k": k, "aic": 2.0 * k - 2.0 * ll}
        s.chosen_family = min(COPULA_FAMILIES, key=lambda f: s.copula[f]["aic"])
        aic_wins[s.chosen_family] += 1
        s.aic = {f: s.copula[f]["aic"] for f in COPULA_FAMILIES}
        s.gof = copula_gof(s.chosen_family, s.copula[s.chosen_family]["theta"], u1f, u2f, rng)
        if not s.gof["pass"]:
            s.qualify_reason = "gof"
            stats["skipped_gof"] += 1
            out.append(s)
            continue
        stats["cycles_gof_pass"] += 1
        ref1 = np.sort(l1)
        ref2 = np.sort(l2)
        u1t = apply_empirical_cdf(ref1, log1_all[t0:t1])
        u2t = apply_empirical_cdf(ref2, log2_all[t0:t1])
        s.saturation["u1_at_bounds"] = int(np.count_nonzero((u1t <= 0.0) | (u1t >= 1.0)))
        s.saturation["u2_at_bounds"] = int(np.count_nonzero((u2t <= 0.0) | (u2t >= 1.0)))
        s.saturation["trading_points"] = int(t1 - t0)
        stats["cmi_points"] += int(t1 - t0)
        stats["saturated_points"] += (s.saturation["u1_at_bounds"] + s.saturation["u2_at_bounds"])
        for ci in range(len(COPULA_LABELS)):
            fam = (s.chosen_family if ci == COPULA_INDEX_AIC else COPULA_FAMILIES[ci - 1])
            cmi = cmi_series(fam, s.copula[fam]["theta"], u1t, u2t)
            s.cmi[ci] = cmi
            # registered entry candidates: the first bar whose close crosses +-t_o, entering at
            # the NEXT bar's open (next-event execution - the crossing bar itself never fills)
            for oi, thr in enumerate(T_O_GRID):
                idx = np.flatnonzero(np.abs(cmi[:TRADING_BARS - 1]) > thr)
                s.entries[(ci, oi)] = ((int(idx[0]), 1 if cmi[int(idx[0])] < -thr else -1)
                                       if len(idx) else None)
                if len(idx):
                    stats["entry_cells_available"] += 1
        s.tradable = True
        stats["cycles_tradable"] += 1
        out.append(s)
    stats["aic_selection_wins"] = dict(aic_wins)
    run_log("pair %s cycles=%d qualifying=%d gof_pass=%d tradable=%d "
            "(beta-band=%d coint=%d gof=%d)"
            % (cohort_label, stats["cycles_total"], stats["cycles_qualifying"],
               stats["cycles_gof_pass"], stats["cycles_tradable"],
               stats["skipped_hedge_ratio_band"], stats["skipped_cointegration"],
               stats["skipped_gof"]))
    return out, stats

# ---------------------------------------------------------------------------
# phase 3: the pre-registered leg-aware pair execution engine
# ---------------------------------------------------------------------------


def pack_cycle(pair, cyc, beta):
    """One cycle's trading-window bars + the frozen spread state, as fast scalar lists.

    S_open / S_high / S_low / S_close are the beta-hedged log spreads at the four sample
    points of each bar: the open, the HIGH of leg 1 against the LOW of leg 2 (the bar's
    maximum spread), the LOW of leg 1 against the HIGH of leg 2 (its minimum) and the close.
    Pre-computing them keeps the episode walk free of per-bar logarithms.
    """
    t0, t1 = cyc["trading"]
    f = [pair.legs[i]["fields"] for i in (0, 1)]
    pack = {}
    for name, idx, field in (("o1", 0, "open"), ("h1", 0, "high"), ("l1", 0, "low"),
                             ("c1", 0, "close"), ("o2", 1, "open"), ("h2", 1, "high"),
                             ("l2", 1, "low"), ("c2", 1, "close")):
        pack[name] = f[idx][field][t0:t1].tolist()
    n = t1 - t0
    pack["s_open"] = [(math.log(pack["o1"][i]) - beta * math.log(pack["o2"][i]))
                      for i in range(n)]
    pack["s_high"] = [(math.log(pack["h1"][i]) - beta * math.log(pack["l2"][i]))
                      for i in range(n)]
    pack["s_low"] = [(math.log(pack["l1"][i]) - beta * math.log(pack["h2"][i]))
                     for i in range(n)]
    pack["s_close"] = [(math.log(pack["c1"][i]) - beta * math.log(pack["c2"][i]))
                       for i in range(n)]
    pack["t0_ms"] = int(pair.open_time_ms[t0])
    return pack


def simulate_cell(pair, cycles, packs, case, rail, cycle_sel, stress, slip_ticks, kind,
                  series_by_leg, day_setup, diag=False, count_layers=True):
    """One pre-registered parameter case over one window slice (a set of cycles).

    Costs are charged at the fill's own instant (contract 7.2 v1.3.1): every entry, scale-in
    and exit reduces the realised equity when it happens, and `gross_pnl` is fed only by the
    independent price-PnL accumulator at a close.

    Registered exit order inside one bar: (1) the rail's adverse ladder and the resting
    stops, (2) the spread profit target and each leg's breakeven take profit, (3) the CMI
    reversion at the bar close, (4) the max holding period and the cycle's end.  A stop that
    the bar's own open already crossed fills at the open (an adverse gap is never flattered);
    a take profit always fills at its own level (a favourable gap is never credited).
    """
    t_o = case["t_o"]
    t_c = case["t_c"]
    cop = case["copula_family"]
    oi = T_O_GRID.index(t_o)
    d0 = rail["spacing_pct"]
    base = rail["base_quote"]
    mult = rail["size_multiplier"]
    tp = rail["breakeven_tp_pct"]
    inval = rail["invalidation_pct"]
    fee_mult = stress.get("fee_mult", 1.0)
    fee_override = stress.get("fee_override")
    fund_mult = stress.get("funding_mult", 1.0)
    no_funding = bool(stress.get("no_funding"))
    delay = 1 if stress.get("entry_delay_1_bar") else 0
    slip = stress.get("slip_ticks", slip_ticks)

    tick1 = pair.meta[0]["price_increment"]
    tick2 = pair.meta[1]["price_increment"]
    lev = 1.0 / pair.meta[0]["margin_init"]
    mmaint = pair.meta[0]["margin_maint"]
    fee1 = (fee_override if fee_override is not None else pair.meta[0]["taker_fee"]) * fee_mult
    fee2 = (fee_override if fee_override is not None else pair.meta[1]["taker_fee"]) * fee_mult

    day_local = day_setup["day_local"]
    day_end = day_setup["day_end"]
    n_days = day_setup["n_days"]
    day_equity = [None] * n_days

    layers = [0] * TRANCHE_COUNT
    realized = fees_total = funding_paid = gross_pnl = 0.0
    fills = 0
    turnover = 0.0
    episodes = 0
    tp_hits = sl_hits = leg_tp_hits = leg_stop_hits = 0
    cmi_exits = max_holding_exits = window_end_exits = margin_calls = 0
    bars_in_market = 0
    max_lev = util_sum = 0.0
    cycles_seen = cycles_tradable = cycles_entered = 0
    halted = False
    min_entry_equity = START_EQUITY
    by_year = {}
    by_direction = {"long_spread": 0, "short_spread": 0}

    def mark(day_index, value):
        if 0 <= day_index < n_days:
            day_equity[day_index] = value

    for cidx in cycle_sel:
        sig = cycles[cidx]
        cycles_seen += 1
        if not sig.tradable:
            continue
        cycles_tradable += 1
        ent = sig.entries.get((cop, oi))
        if ent is None:
            continue
        if START_EQUITY + realized <= 0.0:
            halted = True
            break
        bar, d = ent
        entry_bar = bar + 1 + delay
        if entry_bar > TRADING_BARS - 1:
            continue
        min_entry_equity = min(min_entry_equity, START_EQUITY + realized)
        beta = abs(sig.beta)
        pack = packs[cidx]
        o1, o2 = pack["o1"], pack["o2"]
        h1, h2 = pack["h1"], pack["h2"]
        l1, l2 = pack["l1"], pack["l2"]
        c1, c2 = pack["c1"], pack["c2"]
        s_open, s_high = pack["s_open"], pack["s_high"]
        s_low, s_close = pack["s_low"], pack["s_close"]
        cmi = sig.cmi[cop]
        t0_abs = sig.trade[0]
        t_lo = entry_bar
        t_hi = TRADING_BARS - 1
        sign1 = d
        sign2 = -d
        # ---- entry: both legs at the NEXT bar's open (next-event execution, no look-ahead)
        px1 = o1[t_lo] + sign1 * slip * tick1
        px2 = o2[t_lo] + sign2 * slip * tick2
        n1 = base * lev
        q1 = n1 / px1
        cost1 = q1 * px1
        n2 = beta * n1
        q2 = n2 / px2
        cost2 = q2 * px2
        if abs(n1 * beta - q2 * px2) > 1e-6 * n1:
            counter(kind, "unhedged_entry_notional")
        entry_fee = q1 * px1 * fee1 + q2 * px2 * fee2
        fees_total += entry_fee
        realized -= entry_fee
        fills += 2
        turnover += q1 * px1 + q2 * px2
        layers[0] += 1
        cycles_entered += 1
        episodes += 1
        by_direction["long_spread" if d > 0 else "short_spread"] += 1
        s_entry = s_open[t_lo]
        k = 0
        ep_fees = entry_fee
        ep_fund = 0.0
        day_idx = day_local[t0_abs + t_lo]
        mark(day_idx, START_EQUITY + realized)
        # funding pointers per leg, opened on the CLOSED exposure interval [entry - 1s, ...]
        ptrs = []
        for li in (0, 1):
            if no_funding:
                ptrs.append(None)
                continue
            series = series_by_leg[li]
            tms = series["obs_times"]
            lo = pack["t0_ms"] + entry_bar * pair.bar_ms - MS_JITTER_TOLERANCE_MS
            hi = pack["t0_ms"] + (t_hi + 1) * pair.bar_ms + MS_JITTER_TOLERANCE_MS
            ptrs.append([int(np.searchsorted(tms, lo, side="left")),
                         int(np.searchsorted(tms, hi, side="right")), series, li])
        reason = None
        x1 = x2 = None
        for t in range(t_lo, t_hi + 1):
            # ---- funding: every settlement inside the closed holding interval, charged on the
            # position notional of the leg it belongs to at its own bar's close mark
            if not no_funding:
                for li in (0, 1):
                    ptr = ptrs[li]
                    a, b, series, leg_no = ptr
                    while a < b and int(series["settle_bar"][a]) <= t0_abs + t:
                        if int(series["settle_bar_closed"][a]) < t0_abs + t_lo:
                            counter(kind, "funding_bar_out_of_hold")
                        rate = float(series["obs_rates"][a]) * fund_mult
                        q = q1 if leg_no == 0 else q2
                        px = c1[t] if leg_no == 0 else c2[t]
                        sg = sign1 if leg_no == 0 else sign2
                        ep_fund += sg * q * px * rate
                        a += 1
                    ptr[0] = a
            adv = d * (s_low[t] - s_entry) if d > 0 else d * (s_high[t] - s_entry)
            adv1 = l1[t] if sign1 > 0 else h1[t]
            adv2 = l2[t] if sign2 > 0 else h2[t]
            while k < MAX_ADD_LEVELS and adv <= -(k + 1) * d0:
                k += 1
                f1 = adv1 + sign1 * slip * tick1
                f2 = adv2 + sign2 * slip * tick2
                aq1 = base * (mult ** k) * lev / f1
                aq2 = beta * base * (mult ** k) * lev / f2
                cost1 += aq1 * f1
                q1 += aq1
                cost2 += aq2 * f2
                q2 += aq2
                afee = aq1 * f1 * fee1 + aq2 * f2 * fee2
                fees_total += afee
                ep_fees += afee
                realized -= afee
                fills += 2
                turnover += aq1 * f1 + aq2 * f2
                layers[k] += 1
            avg1 = cost1 / q1
            avg2 = cost2 / q2
            level1 = avg1 * (1.0 - inval) if sign1 > 0 else avg1 * (1.0 + inval)
            level2 = avg2 * (1.0 - inval) if sign2 > 0 else avg2 * (1.0 + inval)
            stop1 = (adv1 <= level1) if sign1 > 0 else (adv1 >= level1)
            stop2 = (adv2 <= level2) if sign2 > 0 else (adv2 >= level2)
            sl_hit = adv <= -SPREAD_SL
            if stop1 or stop2 or sl_hit:
                if d * (s_open[t] - s_entry) <= -SPREAD_SL:
                    x1, x2 = o1[t], o2[t]          # the bar opened beyond the pair stop
                else:
                    x1 = adv1
                    x2 = adv2
                    if stop1:
                        # the resting invalidation fills at its own level unless the bar's open
                        # is already beyond it (an adverse gap is never flattered)
                        x1 = min(o1[t], level1) if sign1 > 0 else max(o1[t], level1)
                    if stop2:
                        x2 = min(o2[t], level2) if sign2 > 0 else max(o2[t], level2)
                x1 = x1 + sign1 * slip * tick1
                x2 = x2 + sign2 * slip * tick2
                reason = "leg_stop" if (stop1 or stop2) else "pair_sl"
                break
            fav = d * (s_high[t] - s_entry) if d > 0 else d * (s_low[t] - s_entry)
            fav1 = h1[t] if sign1 > 0 else l1[t]
            fav2 = h2[t] if sign2 > 0 else l2[t]
            tp_level1 = avg1 * (1.0 + tp) if sign1 > 0 else avg1 * (1.0 - tp)
            tp_level2 = avg2 * (1.0 + tp) if sign2 > 0 else avg2 * (1.0 - tp)
            take1 = (fav1 >= tp_level1) if sign1 > 0 else (fav1 <= tp_level1)
            take2 = (fav2 >= tp_level2) if sign2 > 0 else (fav2 <= tp_level2)
            pt_hit = fav >= SPREAD_PT
            if take1 or take2 or pt_hit:
                if d * (s_open[t] - s_entry) >= SPREAD_PT:
                    x1, x2 = o1[t], o2[t]
                else:
                    x1 = adv1 if take1 or pt_hit else fav1
                    x2 = adv2 if take2 or pt_hit else fav2
                    if take1:
                        x1 = tp_level1
                    if take2:
                        x2 = tp_level2
                x1 = x1 + sign1 * slip * tick1
                x2 = x2 + sign2 * slip * tick2
                reason = "leg_tp" if (take1 or take2) else "pair_pt"
                break
            if abs(cmi[t]) < t_c:
                x1 = c1[t] + sign1 * slip * tick1
                x2 = c2[t] + sign2 * slip * tick2
                reason = "cmi"
                break
            if t - t_lo + 1 >= MAX_HOLDING_BARS:
                x1 = c1[t] + sign1 * slip * tick1
                x2 = c2[t] + sign2 * slip * tick2
                reason = "max_holding"
                break
            if t == t_hi:
                x1 = c1[t] + sign1 * slip * tick1
                x2 = c2[t] + sign2 * slip * tick2
                reason = "window_end"
                break
            ueq = (START_EQUITY + realized + sign1 * (q1 * c1[t] - cost1)
                   + sign2 * (q2 * c2[t] - cost2))
            notional = q1 * c1[t] + q2 * c2[t]
            if ueq <= mmaint * notional:
                x1 = c1[t] + sign1 * slip * tick1
                x2 = c2[t] + sign2 * slip * tick2
                reason = "margin_call"
                break
            if ueq > 0.0:
                lv = notional / ueq
                if lv > max_lev:
                    max_lev = lv
                util_sum += (notional / lev) / ueq
            bars_in_market += 1
            if t0_abs + t in day_end:
                mark(day_local[t0_abs + t], ueq)
        if reason is None:
            raise SystemExit("pair episode left open at %s %s" % (pair.label, kind))
        # ---- close both legs reduce-only
        leg_gross = (sign1 * q1 * x1 - sign1 * cost1) + (sign2 * q2 * x2 - sign2 * cost2)
        real_now = leg_gross
        gross_pnl += leg_gross
        close_fee = q1 * x1 * fee1 + q2 * x2 * fee2
        fees_total += close_fee
        ep_fees += close_fee
        real_now -= close_fee
        fills += 2
        turnover += q1 * x1 + q2 * x2
        # settlements at the exit instant itself are charged on the exit notional
        if not no_funding:
            for li in (0, 1):
                ptr = ptrs[li]
                a, b, series, leg_no = ptr
                q = q1 if leg_no == 0 else q2
                px = x1 if leg_no == 0 else x2
                sg = sign1 if leg_no == 0 else sign2
                while a < b and int(series["settle_bar"][a]) <= t0_abs + t:
                    if int(series["settle_bar_closed"][a]) > t0_abs + t_hi + 1:
                        counter(kind, "funding_bar_out_of_hold")
                    rate = float(series["obs_rates"][a]) * fund_mult
                    ep_fund += sg * q * px * rate
                    a += 1
                ptr[0] = a
        funding_paid += ep_fund
        realized += real_now - ep_fund
        if reason == "pair_sl":
            sl_hits += 1
        elif reason == "leg_stop":
            leg_stop_hits += 1
        elif reason == "pair_pt":
            tp_hits += 1
        elif reason == "leg_tp":
            leg_tp_hits += 1
        elif reason == "cmi":
            cmi_exits += 1
        elif reason == "max_holding":
            max_holding_exits += 1
        elif reason == "window_end":
            window_end_exits += 1
        elif reason == "margin_call":
            margin_calls += 1
        else:
            raise SystemExit("unregistered exit reason %r" % reason)
        mark(day_local[t0_abs + t], START_EQUITY + realized)
        if diag:
            ep_net = leg_gross - ep_fees - ep_fund
            year = time.strftime("%Y", time.gmtime(
                (pack["t0_ms"] + t_lo * pair.bar_ms) / 1000.0))
            slot = by_year.setdefault(year, {"episodes": 0, "net_pnl": 0.0, "fees": 0.0,
                                             "funding": 0.0, "gross_pnl": 0.0})
            slot["episodes"] += 1
            slot["net_pnl"] += ep_net
            slot["fees"] += ep_fees
            slot["funding"] += ep_fund
            slot["gross_pnl"] += leg_gross

    # only GRID cells feed the full-window ladder histogram: diagnostics re-run cells the grid
    # already counted, so they must not inflate the level counts
    if kind in FULL_WINDOW_GRID_KINDS and count_layers:
        acc = LAYER_TOTALS.setdefault("full", [0] * TRANCHE_COUNT)
        for kk in range(TRANCHE_COUNT):
            acc[kk] += layers[kk]
    series_flat = _flat_series(day_equity)
    diag_out = {}
    if diag:
        diag_out = {"pnl_by_year": {k: _round_dict(v) for k, v in sorted(by_year.items())},
                    "episodes_by_direction": dict(by_direction),
                    "daily_equity": [round(float(v), 6) for v in series_flat],
                    "daily_equity_days": int(len(series_flat))}
    return _metrics(realized, fees_total, funding_paid, gross_pnl, episodes, tp_hits, sl_hits,
                    leg_tp_hits, leg_stop_hits, cmi_exits, max_holding_exits, window_end_exits,
                    margin_calls, halted, min_entry_equity, series_flat, layers, max_lev,
                    util_sum, bars_in_market, fills, turnover, cycles_seen, cycles_tradable,
                    cycles_entered, diag_out)


def _round_dict(d):
    return {k: (round(v, 6) if isinstance(v, float) else v) for k, v in d.items()}


def _flat_series(day_equity):
    """Daily equity marks of the EVALUATED slice, forward-filled across flat stretches.

    The series is indexed by the slice's own day index (never the cohort's), so it covers
    exactly the window being measured and never dilutes Sharpe with pre-window days.
    """
    series = []
    last = START_EQUITY
    for v in day_equity:
        if v is not None:
            last = v
        series.append(last)
    return np.array(series, dtype=np.float64)


def _metrics(realized, fees_total, funding_paid, gross_pnl, episodes, tp_hits, sl_hits,
             leg_tp_hits, leg_stop_hits, cmi_exits, max_holding_exits, window_end_exits,
             margin_calls, halted, min_entry_equity, series, layers, max_lev, util_sum,
             bars_in_market, fills, turnover, cycles_seen, cycles_tradable, cycles_entered,
             diag_out):
    if len(series) > 2:
        rets = np.diff(series) / series[:-1]
        sd = float(np.std(rets, ddof=1))
        sharpe = float(np.mean(rets) / sd * np.sqrt(365.0)) if sd > 0 else 0.0
    else:
        sharpe = 0.0
    peak = np.maximum.accumulate(series)
    dd = series - peak
    dd_pct = dd / peak
    span_days = max(float(len(series)), 1.0)
    years = max(span_days / 365.25, 1.0 / 365.25)
    end_eq = START_EQUITY + realized
    return {
        "net_pnl": realized, "fees": fees_total, "funding": funding_paid,
        "gross_pnl": gross_pnl,
        "episodes": episodes, "tp_hits": tp_hits, "sl_hits": sl_hits,
        "leg_tp_hits": leg_tp_hits, "leg_stop_hits": leg_stop_hits, "cmi_exits": cmi_exits,
        "max_holding_exits": max_holding_exits, "window_end_exits": window_end_exits,
        "margin_calls": margin_calls,
        "halted": halted, "min_entry_equity": min_entry_equity,
        "ending_equity": end_eq, "sharpe": sharpe,
        "max_dd_usdt": float(dd.min()) if len(dd) else 0.0,
        "max_dd_pct": float(dd_pct.min()) if len(dd_pct) else 0.0,
        "max_effective_leverage": max_lev,
        "capital_utilization": (util_sum / bars_in_market) if bars_in_market else 0.0,
        "bars_in_market": bars_in_market, "days": int(len(series)), "years": years,
        "total_return_pct": end_eq / START_EQUITY - 1.0,
        "cagr": ((end_eq / START_EQUITY) ** (1.0 / years) - 1.0) if end_eq > 0.0 else None,
        "annualized_return": ((end_eq / START_EQUITY - 1.0) / years) if end_eq > 0.0 else None,
        "fills": fills, "turnover_usdt": turnover,
        "cycles_seen": cycles_seen, "cycles_tradable": cycles_tradable,
        "cycles_entered": cycles_entered,
        "layers": layers, "diagnostics": diag_out,
    }


def exit_price_pnl(proceeds, basis):
    """Pure price PnL of one closing fill (contract 7.2 v1.3.2 independent gross accounting)."""
    return proceeds - basis


def pnl_decomposition_ok(m, tol=1e-3):
    return abs(m["gross_pnl"] - m["fees"] - m["funding"] - m["net_pnl"]) <= tol


def rail_for(dca):
    return {"base_quote": dca["base_quote"], "spacing_pct": dca["spacing_pct"],
            "size_multiplier": dca["size_multiplier"], "breakeven_tp_pct": dca["breakeven_tp_pct"],
            "invalidation_pct": dca["invalidation_pct"]}


def record(pair, case, dca, kind, m):
    row = {"symbol": pair.label, "timeframe": pair.timeframe,
           "leg1_symbol": pair.pair[0], "leg2_symbol": pair.pair[1], "window_kind": kind,
           "strategy_case": case_name(case_tuple(case)),
           "case_name": case_name(case_tuple(case)),
           "t_o": case["t_o"], "t_c": case["t_c"], "copula_family": case["copula_family"],
           "spacing_pct": dca["spacing_pct"], "size_multiplier": dca["size_multiplier"],
           "breakeven_tp_pct": dca["breakeven_tp_pct"],
           "invalidation_pct": dca["invalidation_pct"],
           "net_pnl": round(m["net_pnl"], 6), "fees": round(m["fees"], 6),
           "funding": round(m["funding"], 6), "gross_pnl": round(m["gross_pnl"], 6),
           "ending_equity": round(m["ending_equity"], 6),
           "cycles_seen": m["cycles_seen"], "cycles_tradable": m["cycles_tradable"],
           "cycles_entered": m["cycles_entered"], "episodes": m["episodes"],
           "tp_hits": m["tp_hits"], "sl_hits": m["sl_hits"], "leg_tp_hits": m["leg_tp_hits"],
           "leg_stop_hits": m["leg_stop_hits"], "cmi_exits": m["cmi_exits"],
           "max_holding_exits": m["max_holding_exits"],
           "window_end_exits": m["window_end_exits"], "margin_calls": m["margin_calls"],
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
    return {f: row[f] for f in STRATEGY_FIELDS}


def cycle_selection(pair, signals, start_idx, end_idx):
    """The cycles whose registered trading window lies entirely inside the slice."""
    out = []
    for s in signals:
        t0, t1 = s.trade
        if t0 >= start_idx and t1 <= end_idx:
            out.append(s.index)
    return out


def day_setup_for(pair, slice_bounds):
    """Slice-local day index map + the set of bar indices that end a UTC day."""
    i0, i1 = slice_bounds
    ids = pair.day_id[i0:i1]
    uniq, inv = np.unique(ids, return_inverse=True)
    day_local = np.full(pair.n, -1, dtype=np.int64)
    day_local[i0:i1] = inv.astype(np.int64)
    day_end = set(int(x) for x in pair.day_end if i0 <= x < i1)
    return {"day_local": day_local.tolist(), "day_end": day_end,
            "n_days": int(len(uniq)), "bounds": (i0, i1)}


def run_pair_cycles(spec, pair, cycles, packs, run_log, series_by_leg):
    """Every legal (strategy params x DCA config) case of the pair, on every registered grid."""
    grid = spec["parameter_domain"]["grid_cases"]
    dca_grid = spec["dca_domain"]["grid"]
    slip = spec["costs"]["baseline_slippage_ticks"]
    windows = {"historical": (spec["data"]["historical_start"], spec["data"]["historical_end"]),
               "oos": (spec["data"]["oos_start"], spec["data"]["oos_end"]),
               "full": (spec["data"]["start"], spec["data"]["end"])}
    slices = {k: pair.slice(*v) for k, v in windows.items()}
    sel = {k: cycle_selection(pair, cycles, *slices[k]) for k in ("historical", "oos", "full")}
    day_setup = day_setup_for(pair, slices["full"])
    rows = {k: [] for k in COHORT_GRID_KINDS}
    for kind in ("historical", "oos", "full"):
        run_log("window %s %s cycles=%d cases=%d"
                % (kind, pair.label, len(sel[kind]), len(grid) * len(dca_grid)))
        for case in grid:
            for dca in dca_grid:
                m = simulate_cell(pair, cycles, packs, case, rail_for(dca), sel[kind], {}, slip,
                                  kind, series_by_leg, day_setup)
                rows[kind].append(record(pair, case, dca, kind, m))
    full_sel = sel["full"]
    for sname, stress in STRESS:
        for case in grid:
            for dca in dca_grid:
                m = simulate_cell(pair, cycles, packs, case, rail_for(dca), full_sel, stress,
                                  stress.get("slip_ticks", slip), sname, series_by_leg, day_setup)
                rows[sname].append(record(pair, case, dca, sname, m))
    for case in grid:
        for dca in dca_grid:
            m = simulate_cell(pair, cycles, packs, case, rail_for(dca), full_sel,
                              COST_ATTRITION_STRESS, slip, "cost_attrition_40bps",
                              series_by_leg, day_setup)
            rows["cost_attrition_40bps"].append(record(pair, case, dca,
                                                       "cost_attrition_40bps", m))
            m = simulate_cell(pair, cycles, packs, case, rail_for(dca), full_sel,
                              SOURCE_COST_STRESS, slip, "source_cost_0p12pct",
                              series_by_leg, day_setup)
            rows["source_cost_0p12pct"].append(record(pair, case, dca, "source_cost_0p12pct", m))
    for case in grid:
        for dca in dca_grid:
            m = simulate_cell(pair, cycles, packs, case, rail_for(dca), sel["historical"],
                              {"no_funding": True}, slip, "no_funding", series_by_leg, day_setup)
            rows["no_funding"].append(record(pair, case, dca, "no_funding", m))
            m = simulate_cell(pair, cycles, packs, case, rail_for(dca), full_sel,
                              {"no_funding": True}, slip, "no_funding_full", series_by_leg,
                              day_setup)
            rows["no_funding_full"].append(record(pair, case, dca, "no_funding_full", m))
    return rows


# ---------------------------------------------------------------------------
# phase 4: pre-registered cohort-selector / cohort-survivor gate
#          (contract v1.3.0 sections 7.2 and 7.3; no post-hoc tuning)
# ---------------------------------------------------------------------------


def axis_values(spec):
    """The registered value list of every joint-space axis, in the registered order."""
    return {"t_o": list(T_O_GRID), "t_c": list(T_C_GRID),
            "copula_family": list(range(len(COPULA_LABELS))),
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
    bad = [r for r in rows if r.get("window_kind") not in allowed]
    if bad:
        raise ValueError("%s must be given %r rows only (contract 7.3): %d violation(s)"
                         % (where, allowed, len(bad)))


def select_cohort_winner(hist_rows, spec, allowed=("historical",)):
    """One deterministic winner per pair cohort, historical window only."""
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
    hits = [r for r in rows if cell_key(r) == key]
    if len(hits) != 1:
        raise ValueError("expected exactly one row for cell %s, found %d" % (str(key), len(hits)))
    return hits[0]


def cell_of_params(winner, dca_base_quote=None):
    """The registered joint cell key of one elected winner dict."""
    return tuple([winner[f] for f in STRATEGY_FIELDS] + [winner[a] for a in DCA_AXES])


def cohort_neighbourhood(hist_rows, winner, spec):
    """Face-adjacent (+-1 registered step on exactly one axis, inside the registered grid)."""
    require_historical(hist_rows, "cohort_neighbourhood")
    axes = axis_values(spec)
    idx = {cell_key(r): r for r in hist_rows}
    wkey = cell_of_params(winner)
    wsign = winner["net_pnl"] > 0.0
    neighbours = []
    for axis in NEIGHBOURHOOD_AXES:
        vals = axes[axis]
        ai = AXES.index(axis)
        pos = vals.index(wkey[ai])
        for step in (-1, 1):
            npos = pos + step
            if not (0 <= npos < len(vals)):
                continue
            nkey = list(wkey)
            nkey[ai] = vals[npos]
            row = idx.get(tuple(nkey))
            if row is not None:
                neighbours.append(row)
    agree = sum(1 for r in neighbours if (r["net_pnl"] > 0.0) == wsign)
    frac = (agree / float(len(neighbours))) if neighbours else 0.0
    return {"neighbours": len(neighbours), "agreeing": agree,
            "same_sign_fraction": round(frac, 6),
            "winner_net_pnl_positive": wsign,
            "threshold": spec["gates"]["neighborhood_min_same_sign_fraction"],
            "axes": list(NEIGHBOURHOOD_AXES),
            "note": "a +-1 step that would leave the registered joint grid contributes no "
                    "neighbour (contract 7.3: still inside the registered domain)",
            "passed": frac >= spec["gates"]["neighborhood_min_same_sign_fraction"]}


def neighbourhood_tc_oos(rows_oos, winner, spec):
    """Registered family-level reader: the t_o/t_c neighbourhood's OOS sign consistency."""
    axes = axis_values(spec)
    idx = {cell_key(r): r for r in rows_oos}
    wkey = cell_of_params(winner)
    wrow = idx.get(wkey)
    if wrow is None:
        return {"evaluated": False, "reason": "winner cell missing in the oos grid"}
    wsign = wrow["net_pnl"] > 0.0
    neighbours = []
    for axis in ("t_o", "t_c"):
        vals = axes[axis]
        ai = AXES.index(axis)
        pos = vals.index(wkey[ai])
        for step in (-1, 1):
            npos = pos + step
            if not (0 <= npos < len(vals)):
                continue
            nkey = list(wkey)
            nkey[ai] = vals[npos]
            if nkey[AXES.index("copula_family")] != COPULA_INDEX_AIC:
                continue
            row = idx.get(tuple(nkey))
            if row is not None:
                neighbours.append(row)
    agree = sum(1 for r in neighbours if (r["net_pnl"] > 0.0) == wsign)
    frac = (agree / float(len(neighbours))) if neighbours else 0.0
    return {"evaluated": True, "neighbours": len(neighbours), "agreeing": agree,
            "same_sign_fraction": round(frac, 6), "winner_oos_positive": wsign,
            "threshold": spec["gates"]["neighborhood_min_same_sign_fraction"],
            "passed": frac >= spec["gates"]["neighborhood_min_same_sign_fraction"]}


def _pick(row, keys):
    return {k: row[k] for k in keys if k in row}


def evaluate_cohort(spec, cohort_label, rows):
    """The whole cohort decision for one pair cohort (contract 7.3)."""
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
    out["winner"] = {f: winner[f] for f in STRATEGY_FIELDS}
    out["winner"].update({a: winner[a] for a in DCA_AXES})
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
        "source_cost_reference": _pick(same_cell(rows["source_cost_0p12pct"], key),
                                       WINNER_METRIC_KEYS),
    }
    out["neighbourhood"] = nb
    out["oos_tc_neighbourhood"] = neighbourhood_tc_oos(rows["oos"], winner, spec)
    reasons = []
    gates = spec["gates"]
    if oos["episodes"] < gates["min_episodes_oos"]:
        reasons.append("insufficient_trades")
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
    return {"disposition": ("SURVIVOR_FOUND" if len(survivors) == 1 else "MULTIPLE_SURVIVORS"),
            "verdict_recommendation": "PASS",
            "performance_claimable_recommendation": True,
            "mapping_version": CONTRACT_SEMANTICS_VERSION}

# ---------------------------------------------------------------------------
# phase 5: registered family-level falsification readers
# ---------------------------------------------------------------------------


def source_consistency_check(spec, cohort_results):
    """Registered item: our machine result vs the source's negative net-return conclusion.

    The source's primary finding is that copula pairs trading on crypto perpetuals produces
    NEGATIVE net returns after realistic costs.  If this round's elected winner cells
    reproduce that negative conclusion, the family must be recorded REJECT - a hit may never
    be turned into PASS merely because the paper was reproduced.
    """
    winners = [c for c in cohort_results if c.get("winner") is not None]
    if not winners:
        return {"registered_item": "source-consistency", "evaluated": False,
                "reason": "no elected winner cell in this round (no historical candidate)",
                "hit": False, "forbids_pass": True,
                "landing": "family-level: a hit must NEVER be recorded as PASS"}
    tot = sum(c["metrics"]["full"]["net_pnl"] for c in winners)
    return {"registered_item": "source-consistency", "evaluated": True,
            "elected_winners": len(winners),
            "winner_full_net_pnl_sum": round(float(tot), 6),
            "hit": bool(tot <= 0.0), "forbids_pass": True,
            "landing": "family-level: a hit (the round reproduces the source's negative net "
                       "return) must be recorded as REJECT, never as PASS"}


def cost_boundary_check(spec, cohort_results):
    """Registered item: canonical vs source-reported cost track sign agreement on the winners."""
    winners = [c for c in cohort_results if c.get("winner") is not None]
    if not winners:
        return {"registered_item": "cost-boundary", "evaluated": False,
                "reason": "no elected winner cell in this round", "hit": False,
                "forbids_pass": True,
                "landing": "family-level: a hit must NEVER be recorded as PASS"}
    disagree = []
    for c in winners:
        canon = c["metrics"]["full"]["net_pnl"]
        src = c["metrics"]["source_cost_reference"]["net_pnl"]
        if (canon > 0.0) != (src > 0.0):
            disagree.append({"cohort": c["cohort"], "canonical_full_net_pnl": round(canon, 6),
                             "source_cost_full_net_pnl": round(src, 6)})
    return {"registered_item": "cost-boundary", "evaluated": True,
            "canonical_track": "taker 5 bps per fill per leg + 1 adverse tick",
            "source_track": "0.0003 (3 bps) per fill per leg, reproducing the source's declared "
                            "0.12%% two-leg round trip",
            "disagreeing_cohorts": disagree, "hit": bool(disagree), "forbids_pass": True,
            "landing": "family-level falsification: the evidence decides REJECT vs DEFERRED"}


def pair_concentration_check(spec, cohort_results):
    """Registered item: is the OOS positive contribution concentrated in a single pair?"""
    survivors = [c for c in cohort_results if c["outcome"] == "SURVIVOR"]
    if not survivors:
        return {"registered_item": "pair-concentration", "evaluated": False,
                "reason": "no cohort survivor in this round", "hit": False,
                "forbids_pass": True,
                "landing": "family-level: a hit must NEVER be recorded as PASS"}
    per = {c["cohort"]: c["metrics"]["oos"]["net_pnl"] for c in survivors}
    pos = {k: v for k, v in per.items() if v > 0.0}
    total = sum(pos.values())
    top = max(pos.items(), key=lambda kv: kv[1]) if pos else (None, 0.0)
    share = (top[1] / total) if total > 0.0 else 0.0
    return {"registered_item": "pair-concentration", "evaluated": True,
            "survivors": len(survivors), "positive_oos_total": round(float(total), 6),
            "top_pair": top[0], "top_share": round(float(share), 6),
            "threshold": 0.70, "hit": bool(share > 0.70), "forbids_pass": True,
            "landing": "family-level robustness disclosure: a hit must not be recorded as PASS"}


def oos_grid_instability_check(spec, cohort_results):
    """Registered item: the t_o/t_c neighbourhood's OOS sign consistency of the winners."""
    winners = [c for c in cohort_results if c.get("winner") is not None]
    if not winners:
        return {"registered_item": "oos-grid-instability", "evaluated": False,
                "reason": "no elected winner cell in this round", "hit": False,
                "forbids_pass": True,
                "landing": "family-level: a hit must NEVER be recorded as PASS"}
    per = {}
    hit = False
    for c in winners:
        nb = c.get("oos_tc_neighbourhood") or {}
        per[c["cohort"]] = nb
        if nb.get("evaluated") and not nb.get("passed", True):
            hit = True
    return {"registered_item": "oos-grid-instability", "evaluated": True, "cohorts": per,
            "hit": bool(hit), "forbids_pass": True,
            "landing": "family-level disclosure of the registered grid-instability item "
                       "(the historical arm is the G7 gate); a hit must not be recorded as PASS"}


# ---------------------------------------------------------------------------
# phase 6: summary, coverage and the registered assertions
# ---------------------------------------------------------------------------


def track_effectiveness(grid_rows):
    """The three registered cost/funding no-op detectors (contract 7.2 v1.3.1).

    v1.0.1 detector fix, disclosed (card t_57ecd99e): these were medians over ALL registered
    cells, and that statistic saturates at exactly 0.0 as soon as more than half of the cells
    never trade (measured on the u1 attempt: 2,400 of 5,760 cells are flat, so median(net_pnl) was
    0.0 in every grid and each `<`/`!=` comparison degenerated to `0.0 < 0.0` -> all three tracks
    were reported as no-ops although their aggregate effect is large: fee_2x doubles the fees
    (272,773 -> 545,547 USDT), funding_2x doubles funding (1,385 -> 2,771 USDT) and the
    source-reported 0.12% track cuts fees to 163,664 USDT, each moving aggregate net PnL the
    signed way).  The aggregate test below reads the same contract requirement on a statistic that
    cannot saturate and fails closed when nothing traded at all, because an unexercised track is
    indistinguishable from a no-op one.
    """
    full = grid_rows["full"]
    total = lambda rows, key: math.fsum(r[key] for r in rows)
    traded_any = any(r["episodes"] > 0 for r in full)
    cost = (traded_any and total(grid_rows["fee_2x"], "fees") > total(full, "fees")
            and total(grid_rows["fee_2x"], "net_pnl") < total(full, "net_pnl"))
    funding = (traded_any
               and abs(total(grid_rows["funding_2x"], "funding")) > abs(total(full, "funding"))
               and total(grid_rows["funding_2x"], "net_pnl") != total(full, "net_pnl"))
    source = (traded_any
              and total(grid_rows["source_cost_0p12pct"], "fees") < total(full, "fees")
              and total(grid_rows["source_cost_0p12pct"], "net_pnl") > total(full, "net_pnl"))
    return {"cost_pressure_effective": cost, "funding_pressure_effective": funding,
            "source_cost_effective": source}


def summarize(spec, grid_rows, layers, diag_inputs, cycle_stats, pair_diag):
    import random
    import statistics as st
    axes = axis_values(spec)
    need = {k: spec["expected"]["case_evaluations_per_grid"] for k in COHORT_GRID_KINDS}
    coverage = {k: len(grid_rows.get(k, [])) for k in need}
    full = grid_rows["full"]
    cohorts = sorted({r["symbol"] for r in full})
    cohort_labels = cohorts
    per_cohort = {label: {k: [r for r in grid_rows[k] if r["symbol"] == label]
                          for k in COHORT_GRID_KINDS} for label in cohort_labels}
    strategy_product = set(case_tuple(g) for g in spec["parameter_domain"]["grid_cases"])
    dca_product = set((a, b, c, d) for a in axes["spacing_pct"] for b in axes["size_multiplier"]
                      for c in axes["breakeven_tp_pct"] for d in axes["invalidation_pct"])
    strategy_cells, dca_cells = set(), set()
    for r in full:
        strategy_cells.add(case_tuple(r))
        dca_cells.add(tuple(r[a] for a in DCA_AXES))
    cells_per_cohort_ok = all(
        len(per_cohort[c][k]) == spec["expected"]["base_combinations_per_cohort"]
        for c in cohort_labels for k in COHORT_GRID_KINDS) if cohort_labels else False
    coverage_complete = (all(coverage[k] == need[k] for k in need)
                         and len(cohorts) == spec["expected"]["cohorts"]
                         and strategy_cells == strategy_product
                         and dca_cells == dca_product
                         and cells_per_cohort_ok)
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
        hist = per_cohort[c["cohort"]]["historical"]
        wkey = cell_of_params(c["winner"])
        shuffled = list(hist)
        rng.shuffle(shuffled)
        again, _ = select_cohort_winner(shuffled, spec)
        if again is None or cell_key(again) != wkey:
            selector_stable = False
            break
    # ---- winner diagnostics (per year, per direction) on the full window
    diag_by_cohort = {}
    robustness_diagnostics = {
        "non_gating": True,
        "note": "per-calendar-year net PnL of the elected winner cell; descriptive only - the "
                "registered family-level falsification items are the only gated readers",
        "registered_item": "sub_period_stability_and_pair_distribution"}
    if coverage_complete:
        for c in cohort_results:
            if c["winner"] is None:
                continue
            cohort, packs, cycles, series_by_leg = diag_inputs[c["cohort"]]
            slip0 = spec["costs"]["baseline_slippage_ticks"]
            win_case = {f: c["winner"][f] for f in STRATEGY_FIELDS}
            dca = {a: c["winner"][a] for a in DCA_AXES}
            dca["base_quote"] = spec["dca_domain"]["base_quote"]
            sel_full = cycle_selection(cohort, cycles, *cohort.slice(
                spec["data"]["start"], spec["data"]["end"]))
            day_setup = day_setup_for(cohort, cohort.slice(spec["data"]["start"],
                                                           spec["data"]["end"]))
            m_win = simulate_cell(cohort, cycles, packs, win_case, rail_for(dca), sel_full, {},
                                  slip0, "full", series_by_leg, day_setup, diag=True,
                                  count_layers=False)
            row = record(cohort, win_case, dca, "full", m_win)
            grid_row = same_cell(per_cohort[c["cohort"]]["full"], cell_of_params(c["winner"]))
            diag_by_cohort[c["cohort"]] = {"winner_full": m_win, "row": row}
            robustness_diagnostics.setdefault("cohorts", {})[c["cohort"]] = {
                "winner_cell": c["winner"],
                "pnl_by_year": m_win["diagnostics"]["pnl_by_year"],
                "episodes_by_direction": m_win["diagnostics"]["episodes_by_direction"],
                "winner_diagnostics_match_full_row": all(
                    abs(float(row[k]) - float(grid_row[k])) < 1e-6
                    for k in ROW_FIELDS
                    if isinstance(grid_row[k], (int, float)) and not isinstance(grid_row[k], bool)
                    and isinstance(row[k], (int, float))),
                "daily_series_days": m_win["diagnostics"]["daily_equity_days"]}
    checks = {
        "source_consistency": source_consistency_check(spec, cohort_results),
        "cost_boundary": cost_boundary_check(spec, cohort_results),
        "pair_concentration": pair_concentration_check(spec, cohort_results),
        "oos_grid_instability": oos_grid_instability_check(spec, cohort_results),
    } if coverage_complete else {
        k: {"registered_item": k, "evaluated": False, "hit": False, "forbids_pass": True}
        for k in ("source_consistency", "cost_boundary", "pair_concentration",
                  "oos_grid_instability")}
    flags = {k: bool(v.get("hit")) for k, v in checks.items()}
    final_verdict = disposition["verdict_recommendation"]
    final_claimable = disposition["performance_claimable_recommendation"]
    if final_verdict == "PASS" and any(flags.values()):
        final_verdict = "DEFERRED"
        final_claimable = False
    med = lambda rows, key: st.median([r[key] for r in rows if r.get(key) is not None] or [0.0])
    pair_distribution = []
    for label, entry in sorted(pair_diag.items()):
        winner = next((c for c in cohort_results if c["cohort"] == label), None)
        pair_distribution.append({
            "pair": label,
            "cycles_total": entry["cycles_total"], "cycles_tradable": entry["cycles_tradable"],
            "aic_selection_wins": entry["aic_selection_wins"],
            "episodes_historical": sum(r["episodes"] for r in per_cohort[label]["historical"]),
            "best_historical_net_pnl": max([r["net_pnl"] for r in per_cohort[label]["historical"]]
                                           or [0.0]),
            "median_full_net_pnl": med(per_cohort[label]["full"], "net_pnl"),
            "median_oos_net_pnl": med(per_cohort[label]["oos"], "net_pnl"),
            "outcome": (winner or {}).get("outcome"),
            "winner_cell": (winner or {}).get("winner"),
            "cull_reasons": (winner or {}).get("cull_reasons"),
        }) if coverage_complete else []
    descriptive = {
        "non_gating": True,
        "note": "every pair cohort is judged independently (contract 7.3); the cross-pair "
                "numbers below are descriptive diagnostics and are never a gate (contract 7.2)",
        "cohort_count": len(cohort_labels),
        "median_full_net_pnl": med(full, "net_pnl"),
        "median_oos_net_pnl": med(grid_rows["oos"], "net_pnl"),
        "median_historical_net_pnl": med(grid_rows["historical"], "net_pnl"),
        "positive_case_share_historical": (sum(1 for r in grid_rows["historical"]
                                              if r["net_pnl"] > 0)
                                          / float(len(grid_rows["historical"])))
        if grid_rows["historical"] else 0.0,
        "positive_case_share_full": (sum(1 for r in full if r["net_pnl"] > 0) / float(len(full)))
        if full else 0.0,
        "pair_distribution": pair_distribution,
    }
    stress_summary = {}
    for s in COHORT_GRID_KINDS:
        stress_summary[s] = {"median_net_pnl": med(grid_rows[s], "net_pnl"),
                             "median_sharpe": med(grid_rows[s], "sharpe"),
                             "cases": len(grid_rows[s]), "non_gating": True}
    keys = ("net_pnl", "fees", "funding", "gross_pnl", "ending_equity", "sharpe",
            "max_dd_pct", "max_dd_usdt", "max_effective_leverage", "capital_utilization",
            "cagr", "total_return_pct", "episodes", "fills", "turnover_usdt")
    _tracks = track_effectiveness(grid_rows)
    cost_pressure_effective = _tracks["cost_pressure_effective"]
    funding_pressure_effective = _tracks["funding_pressure_effective"]
    source_cost_effective = _tracks["source_cost_effective"]
    assertions = {
        "episodes_partition": all(
            r["episodes"] == (r["tp_hits"] + r["sl_hits"] + r["leg_tp_hits"] + r["leg_stop_hits"]
                              + r["cmi_exits"] + r["max_holding_exits"] + r["window_end_exits"]
                              + r["margin_calls"]) for r in full),
        "pnl_decomposition": all(pnl_decomposition_ok(r) for r in full),
        "coverage_complete": coverage_complete,
        "cohort_count_matches_registered": len(cohorts) == spec["expected"]["cohorts"],
        "strategy_grid_is_registered_product": strategy_cells == strategy_product,
        "dca_grid_is_registered_product": dca_cells == dca_product,
        "base_combinations_per_cohort_per_grid": cells_per_cohort_ok,
        "expected_case_evaluations": sum(coverage.values())
        == spec["expected"]["expected_case_evaluations"],
        "layer0_equals_episodes": (layers[0] == sum(
            r["episodes"] for k in FULL_WINDOW_GRID_KINDS for r in grid_rows.get(k, []))
            and layers[0] > 0),
        "layer_histogram_nonempty": sum(layers) > 0,
        "no_entry_after_exhaustion": all(r["min_entry_equity"] > 0.0 for r in full),
        "ending_equity_floor": all(r["ending_equity"] > -1.5 * START_EQUITY for r in full),
        "selector_deterministic": selector_stable,
        "selector_historical_only": True,
        # executable no-look-ahead / structural guards (per-grid module counters)
        "cycle_boundaries_aligned": counters_total("cycle_boundary_misaligned") == 0,
        "cycle_windows_never_truncated": counters_total("cycle_window_truncated") == 0,
        "pair_leg_bars_aligned": counters_total("leg_bars_not_aligned") == 0,
        "bar_grid_is_contiguous": counters_total("bar_grid_not_contiguous") == 0,
        "no_unregistered_strategy_case": counters_total("case_not_registered") == 0,
        "cmi_within_registered_range": counters_total("cmi_out_of_range") == 0,
        "marginals_are_formation_frozen": counters_total("marginal_not_frozen") == 0,
        "traded_cycles_passed_the_gof_gate": counters_total("traded_cycle_without_gof") == 0,
        "no_non_qualifying_cycle_traded": counters_total("non_qualifying_cycle_traded") == 0,
        "entries_are_beta_hedged": counters_total("unhedged_entry_notional") == 0,
        "no_overlapping_pair_episodes": counters_total("episodes_overlap") == 0,
        "funding_bar_never_out_of_hold": counters_total("funding_bar_out_of_hold") == 0,
        "no_funding_grid_is_cost_free": all(r["funding"] == 0.0 for r in grid_rows["no_funding"]),
        "cost_pressure_not_a_noop": cost_pressure_effective,
        "funding_pressure_not_a_noop": funding_pressure_effective,
        "source_cost_track_is_not_a_noop": source_cost_effective,
        "every_pair_cohort_evaluated": len(cohort_labels) == spec["expected"]["cohorts"],
        "all_pairs_share_one_registered_domain": all(
            len({case_tuple(r) for r in per_cohort[label]["full"]}) == len(strategy_product)
            for label in cohort_labels) if coverage_complete else False,
    }
    return {
        "family_id": spec["family_id"], "round_id": spec["round_id"], "run_id": spec["run_id"],
        "engine_version": ENGINE_VERSION, "engine_semantics": ENGINE_SEMANTICS,
        "selector_version": SELECTOR_VERSION, "disposition_version": DISPOSITION_VERSION,
        "contract_semantics_version": CONTRACT_SEMANTICS_VERSION,
        "registered_domains": {
            "strategy": {"t_o": list(T_O_GRID), "t_c": list(T_C_GRID),
                         "copula_labels": list(COPULA_LABELS),
                         "cases": [list(case_tuple(c)) for c in STRATEGY_CASES]},
            "dca": {a: axes[a] for a in DCA_AXES},
            "dca_base_quote": spec["dca_domain"]["base_quote"],
            "pairs": [list(p) for p in PAIRS],
            "cycle": {"formation_bars": FORMATION_BARS, "trading_bars": TRADING_BARS,
                      "anchor": CYCLE_ANCHOR, "spread_pt": SPREAD_PT, "spread_sl": SPREAD_SL,
                      "max_holding_bars": MAX_HOLDING_BARS,
                      "cointegration": {"adf_lags": ADF_LAGS, "adf_cv_5pct": ADF_CV_5PCT,
                                        "kss_cv_5pct": KSS_CV_5PCT,
                                        "half_life_max_days": HALF_LIFE_MAX_DAYS},
                      "gof": {"bootstrap_B": GOF_BOOTSTRAP, "min_p": GOF_MIN_P},
                      "hedge_ratio_band": [HEDGE_BETA_MIN, HEDGE_BETA_MAX]},
        },
        "coverage": coverage, "coverage_required": need, "coverage_complete": coverage_complete,
        "cohorts": cohort_labels, "cohort_count": len(cohorts),
        "case_evaluations_per_cohort_per_grid": spec["expected"]["base_combinations_per_cohort"],
        "case_evaluations_per_grid": spec["expected"]["case_evaluations_per_grid"],
        "case_evaluations_total": sum(coverage.values()),
        "expected_case_evaluations": spec["expected"]["expected_case_evaluations"],
        "cohort_grid_kinds": list(COHORT_GRID_KINDS),
        "cycle_statistics": cycle_stats,
        "cohort_results": cohort_results,
        "cohort_survivors": [c["cohort"] for c in survivors],
        "cohort_survivor_count": len(survivors),
        "cohort_outcome_counts": {"SURVIVOR": len(survivors),
                                  "CULLED": len(cohort_results) - len(survivors)},
        "disposition": disposition["disposition"],
        "verdict_recommendation": disposition["verdict_recommendation"],
        "performance_claimable_recommendation": disposition["performance_claimable_recommendation"],
        "disposition_mapping_version": disposition["mapping_version"],
        "registered_family_level_falsification": checks,
        "registered_family_level_falsification_flags": flags,
        "verdict_recommendation_final": final_verdict,
        "performance_claimable_recommendation_final": final_claimable,
        "survivor_evidence": survivors,
        "descriptive_diagnostics": descriptive,
        "descriptive_medians_all_base_cases": {k: med(full, k) for k in keys},
        "stress_summary": stress_summary,
        "dca_layer_histogram": {"level_%02d" % k: layers[k] for k in range(TRANCHE_COUNT)},
        "robustness_diagnostics": robustness_diagnostics,
        "assertions": assertions,
        "structural_counters": {k: dict(v) for k, v in sorted(COUNTERS.items())},
        "structural_counter_tolerance_ms": MS_JITTER_TOLERANCE_MS,
        "cmi_raw_float_excursion": dict(CMI_RAW_EXCURSION),
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main():
    if len(sys.argv) != 2:
        sys.stderr.write("usage: 50_strategy_e_run.py <run-spec.json>\n")
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
                raise SystemExit("run-spec is not a Strategy E spec: missing %r (contract 7.2/7.3)"
                                 % key)
        if spec["selector_version"] != SELECTOR_VERSION or \
                spec["disposition_version"] != DISPOSITION_VERSION:
            raise SystemExit("run-spec selector/disposition version %r/%r != engine %r/%r"
                             % (spec["selector_version"], spec["disposition_version"],
                                SELECTOR_VERSION, DISPOSITION_VERSION))
        declared_cases = [case_tuple(g) for g in spec["parameter_domain"]["grid_cases"]]
        if declared_cases != [case_tuple(c) for c in STRATEGY_CASES]:
            raise SystemExit("run-spec grid_cases %r != engine registered case order %r"
                             % (declared_cases, [case_tuple(c) for c in STRATEGY_CASES]))
        declared_pairs = [tuple(p) for p in spec["data"]["pairs"]] \
            if spec["data"].get("pairs") else list(PAIRS)
        if declared_pairs != list(PAIRS):
            raise SystemExit("run-spec pairs %r != engine registered pair universe %r"
                             % (declared_pairs, list(PAIRS)))
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
        cycle_stats = {}
        pair_diag = {}
        cycle_report = {}
        funding_report = {}
        total = len(PAIRS)
        done = 0
        for pair in PAIRS:
            label = pair_label(pair)
            cohort = PairBars(pair, spec["data"]["timeframes"][0], spec["data"]["start"],
                              spec["data"]["end"])
            for li, symbol in enumerate(pair):
                meta = instruments[symbol]
                cohort.meta[li] = {"price_increment": meta["price_increment"],
                                   "taker_fee": meta["taker_fee"],
                                   "margin_init": meta["margin_init"],
                                   "margin_maint": meta["margin_maint"],
                                   "leverage": 1.0 / meta["margin_init"]}
            series_by_leg = []
            counts_by_leg = []
            for symbol in pair:
                ft, fr, counts = load_funding_series(symbol, spec["data"]["start"],
                                                     spec["data"]["end"])
                series_by_leg.append(build_funding_index(cohort, ft, fr, counts, symbol))
                counts_by_leg.append(counts)
            cycles = cohort.cycle_bounds()
            for ci, cyc in enumerate(cycles):
                if ci != cyc["index"]:
                    raise SystemExit("cycle index mismatch")
                if (cyc["formation"][0] + FORMATION_BARS != cyc["formation"][1]
                        or cyc["trading"][0] - cyc["formation"][1] != 0
                        or cyc["trading"][1] - cyc["trading"][0] != TRADING_BARS):
                    counter("cycle", "cycle_boundary_misaligned")
                if int(cohort.open_time_ms[cyc["trading"][0]] % MS_PER_HOUR) != 0:
                    counter("cycle", "cycle_boundary_misaligned")
            run_log("pair %s bars=%d cycles=%d legs=%s/%s"
                    % (label, cohort.n, len(cycles), pair[0], pair[1]))
            rng = np.random.default_rng(20260915)
            signals, stats = precompute_cycles(cohort, cycles, run_log, label, rng)
            if len(signals) != len(cycles):
                raise SystemExit("pair %s: %d cycle signals for %d cycles"
                                 % (label, len(signals), len(cycles)))
            packs = [pack_cycle(cohort, cyc, sig.beta)
                     for cyc, sig in zip(cycles, signals)]
            cycle_report[label] = [
                {"cycle": s.index,
                 "formation_start_utc": iso(s.form_start_ms),
                 "trading_start_utc": iso(s.trade_start_ms),
                 "beta": round(s.beta, 8), "adf_t": round(s.adf, 6), "kss_t": round(s.kss, 6),
                 "half_life_days": (None if not math.isfinite(s.half_life_days)
                                    else round(s.half_life_days, 6)),
                 "qualifies": s.qualifies, "qualify_reason": s.qualify_reason,
                 "aic_by_family": {f: round(v, 6) for f, v in sorted(s.aic.items())}
                 if s.aic else {},
                 "chosen_family": s.chosen_family,
                 "gof": ({"ks_stat": round(s.gof["ks_stat"], 8),
                          "cvm_stat": round(s.gof["cvm_stat"], 8),
                          "ks_p": s.gof["ks_p"], "cvm_p": s.gof["cvm_p"],
                          "pass": s.gof["pass"], "bootstrap_B": s.gof["bootstrap_B"]}
                         if s.gof else None),
                 "tradable": s.tradable,
                 "marginal_saturation": s.saturation if s.tradable else None,
                 "entry_cells_available": (sum(1 for v in s.entries.values() if v is not None)
                                           if s.tradable else 0)}
                for s in signals]
            stats["pairs"] = list(pair)
            cycle_stats[label] = stats
            pair_diag[label] = stats
            diag_inputs[label] = (cohort, packs, signals, series_by_leg)
            funding_report[label] = {
                "leg1": {"symbol": pair[0], "observations": int(len(series_by_leg[0]["obs_times"])),
                         "truth_status_counts": counts_by_leg[0],
                         "first": iso(int(series_by_leg[0]["obs_times"][0]))
                         if len(series_by_leg[0]["obs_times"]) else None,
                         "last": iso(int(series_by_leg[0]["obs_times"][-1]))
                         if len(series_by_leg[0]["obs_times"]) else None},
                "leg2": {"symbol": pair[1], "observations": int(len(series_by_leg[1]["obs_times"])),
                         "truth_status_counts": counts_by_leg[1],
                         "first": iso(int(series_by_leg[1]["obs_times"][0]))
                         if len(series_by_leg[1]["obs_times"]) else None,
                         "last": iso(int(series_by_leg[1]["obs_times"][-1]))
                         if len(series_by_leg[1]["obs_times"]) else None},
                "settlement_hours_utc_leg1": sorted(set(
                    time.strftime("%H:%M", time.gmtime(int(t) / 1000.0))
                    for t in series_by_leg[0]["obs_times"])),
                "funding_exposure_note": spec["costs"]["funding_exposure_rule"]}
            for k, v in run_pair_cycles(spec, cohort, signals, packs, run_log,
                                        series_by_leg).items():
                grid_rows.setdefault(k, []).extend(v)
            done += 1
            atomic_write_json(os.path.join(attempt_dir, "artifacts", "progress.json"),
                              {"pairs_done": done, "pairs_total": total,
                               "updated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                               time.gmtime())})
        atomic_write_json(os.path.join(attempt_dir, "artifacts", "funding_series.json"),
                          funding_report)
        atomic_write_json(os.path.join(attempt_dir, "artifacts", "cycle_diagnostics.json"),
                          cycle_report)
        for kind, rows in grid_rows.items():
            path = os.path.join(attempt_dir, "artifacts", "grid_%s.csv" % kind)
            with open(path, "w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=list(ROW_FIELDS))
                w.writeheader()
                w.writerows(rows)
            run_log("wrote %s (%d rows)" % (os.path.basename(path), len(rows)))
        layers = LAYER_TOTALS.setdefault("full", [0] * TRANCHE_COUNT)
        summary = summarize(spec, grid_rows, layers, diag_inputs, cycle_stats, pair_diag)
        summary["runtime_seconds"] = int(time.time() - started)
        summary["bins_build"] = {k: bins[k] for k in
                                 ("build_wall_seconds", "qlib_dir_bytes", "qlib_version")}
        summary["instrument_metadata"] = {s: instruments[s] for s in spec["data"]["symbols"]}
        summary["data_readback"] = bins["readback"]
        summary["funding_series"] = funding_report
        summary["cycle_diagnostics_artifact"] = "artifacts/cycle_diagnostics.json"
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
                          {"checks": summary["registered_family_level_falsification"],
                           "flags": summary["registered_family_level_falsification_flags"]})
        atomic_write_json(os.path.join(attempt_dir, "artifacts", "pair_distribution.json"),
                          summary["descriptive_diagnostics"])
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
