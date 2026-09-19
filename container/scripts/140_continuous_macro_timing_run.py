#!/usr/bin/env python3
"""Strategy CMT — Continuous Timing Signals for Growth-Defensive Style Allocation
(Continuous Macro Timing): a softplus-smoothed macro composite score timed on the registered
LOCAL eligible universe (BTCUSDT / ETHUSDT / BNBUSDT / SOLUSDT USD-M perpetuals, 1 d) on Qlib.

Entry point for ONE pre-registered attempt.  Reads the immutable run-spec from /results (the
only source of parameters), builds the Qlib .bin store from the READ-ONLY canonical raw store
into /qlib/work, then runs the pre-registered full-backtest contract
(QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md sections 7.2 / 7.3):

    4 cohorts (BTCUSDT / ETHUSDT / BNBUSDT / SOLUSDT, raw interval 1d)
    x STRATEGY domain (2 registered axes: the score variant {full, core_only, rate_only} x the
      smoothing arm {ewma, none} = 6 cases)
    x DCA domain (4 axes = 48 configs)
    x 10 registered phase grids (historical / oos / full / fee_2x / funding_2x /
      entry_delay_1_bar / slippage_2ticks / no_funding / no_funding_full /
      cost_attrition_40bps)
    = 4 x 6 x 48 x 10 = 11,520 expected case evaluations.

SOURCE CLAIM UNDER TEST (the record's own): replacing discrete bull/bear style switches with a
CONTINUOUS, softplus-smoothed macro composite score raises the Sharpe ratio of a
growth-versus-defensive style allocation above its static 50/50 benchmark while preserving
post-crisis growth participation (the record reports Sharpe 1.01 vs 0.91 on US ETFs under
10 bps costs).  The record's own portability paragraph classifies the crypto translation as
`adapted` / `unproven` and names the macro proxy replacements used here; this round executes
that extension on the four canonical local instruments, with the source market (10 liquid US
ETFs plus TNX / VIX / SPY, daily closes 2017-2026) recorded as provenance and external-validity
context only (system-owned lifecycle footer, contract 14.4).

THE REGISTERED SIGNAL (record-faithful; every value the record leaves open is a project
pre-registered constant frozen before the first run — see the round-spec's
`signal_semantics.registered_constants`):

  * direction-normalized inputs at the daily close, each `z()` an EXPANDING-window Z-score
    (ddof = 1, minimum 60 defined observations), computed on the registered crypto proxy
    mapping (operator correction 2026-09-18, contract section 6):
      r_t  = -z(d FUND_21)      perpetual funding carries the record's rate-relief slot (TNX);
      d_t  = -z(BTC drawdown)   the benchmark's spot drawdown carries the SPY-drawdown slot;
      vh_t =  z(DVOL pct 756)   the Deribit BTC implied-vol index carries the VIX slots;
      vr_t = -z(d DVOL_21);
      g126 =  z(G/D trailing 126) with G = the equal-weighted local non-BTC growth basket
                                (ETHUSDT + BNBUSDT + SOLUSDT spot) and D = BTC + USDT cash;
  * smooth components: softplus_tau with tau = 1.0 (HighVIX, VIXRelief, LowVIX, GrowthExt) and
    RateQuiet = exp(-0.5 r^2);
  * interactions: i1 = r*vh, i2 = HighVIX*VIXRelief, i3 = GrowthExt*LowVIX,
    i4 = GrowthExt*LowVIX*RateQuiet;
  * composite: Core = alpha r + (1-alpha) d, Stress = 0.5 z(i1) + 0.5 z(i2),
    Crowded = 0.5 z(i3) + 0.5 z(i4), Raw = Core + lambda_s Stress - lambda_c Crowded,
    Score~ = z(Raw), with the record's calibrated alpha = 0.50, lambda_s = 0.50,
    lambda_c = 0.05, MaxTilt = 0.50, tau_w = 0.75, eta = 0.05;
  * target weight in the growth basket w_G^target = 0.5 + MaxTilt tanh(Score~/tau_w), EWMA
    smoothing w_G,t = (1-eta) w_G,t-1 + eta w_G,t^target seeded at the neutral 0.5;
  * target state (research-defined reading of the record's continuous allocation inside a
    one-instrument rail): LONG a growth-basket instrument exactly while w_G > 0.5, LONG the
    defensive instrument (BTC) exactly while w_G < 0.5, otherwise FLAT.  The record is
    LONG-ONLY (w_G + w_D = 1, w >= 0, no leverage, no shorting), so the rail carries long
    positions only and never opens a short leg.  The alternative readings (a dead-band around
    0.5; the continuous weight as a position size) are disclosed and NOT evaluated.

ENTRY / EXIT (the record's own position semantics wrapped in the card-mandated DCA rail):
an ENTRY event is a bar whose target state CHANGES to a non-zero value; the entry executes at
the NEXT bar's open (the record's next-day execution), optionally moved by the registered
`entry_delay_1_bar` execution stress.  An episode ends when (1) the rail take profit at
running_average_cost x (1 +- breakeven_tp_pct) fills reduce-only, (2) the resting invalidation
at running_average_cost x (1 -+ invalidation_pct) fills, (3) the record's own exit condition
holds at a bar's close - the target state is no longer the episode's direction - and every
layer is flattened at the NEXT bar's open, or (4) the slice ends (reduce-only flatten of every
layer).  tranche #1 is the initial entry; adverse-price scale-ins follow the registered ladder
(level_k price = initial_entry_price x (1 -+ spacing_pct x k), k = 1..10, i.e. at most 11
routine active levels of the 12-tranche rail).

DCA is executed as real order/fill accounting (an episode state machine over the bars); nothing
is estimated after the fact.  Every legal (strategy params x DCA config) cell is evaluated on
every registered phase grid, and the family gate is the **cohort-level survivor** rule of
contract section 7.3: one deterministic historical-only winner per cohort, then OOS / full /
robustness / parameter-neighbourhood evidence for that same winner.  This family has four
cohorts, so the cohort survivor count is 0..4 and EVERY survivor advances (v1.4.0 band mapping).

The record's four-item falsification battery is registered as four family-level readers
(the stationary-random placebo test, the cost-stress boundary test, the parameter-perturbation
grid and the subperiod rate-hike drawdown test), each EXECUTED where the local rail can
express it and each carrying its measured local analogue plus an explicit disclosure where the
record's absolute threshold is not portable.  A reader hit can only move a PASS to DEFERRED and
is never PASS-bearing.

Writes only:
  * /qlib/work/**      (rebuildable derived/cache area, INV-5)
  * <attempt_dir>/**   (result.json, artifacts/, logs/, state.json)
Never writes /data/raw (read-only mount).

usage: 140_continuous_macro_timing_run.py <run-spec.json>
"""



from __future__ import annotations

import bisect
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
WORK_ROOT = "/qlib/work/strategyCMT-v1"
CSV_ROOT = os.path.join(WORK_ROOT, "csv")
QLIB_DIR = os.path.join(WORK_ROOT, "qlib-data")
MS_PER_DAY = 86400000
MS_PER_HOUR = 3600000
FIELDS = ["open", "high", "low", "close", "volume"]
START_EQUITY = 30000.0
MAX_ADD_LEVELS = 10
BAR_MS = {"1d": MS_PER_DAY, "1h": MS_PER_HOUR}

# ------------------------------------- registered signal contract (RESEARCH_DEFINED, frozen)
# The record fixes the MECHANISM and the mathematics; this block fixes every value the record
# leaves open.  It mirrors the pre-registered search axes of the round-spec; nothing here may
# change after the first run.
SCORE_VARIANTS = ("full", "core_only", "rate_only")
VARIANT_LABELS = ("full", "core", "rate")
SMOOTHINGS = ("ewma", "none")
SMOOTH_LABELS = ("sm", "raw")
CASE_FIELDS = ("var_full", "var_core", "var_rate", "sm_ewma", "sm_none")
CASE_ORDER = tuple(
    tuple([1 if a == i else 0 for a in range(len(SCORE_VARIANTS))]
          + [1 if b == j else 0 for b in range(len(SMOOTHINGS))])
    for j in range(len(SMOOTHINGS)) for i in range(len(SCORE_VARIANTS)))
CASE_NAMES = tuple("%s__%s" % (VARIANT_LABELS[i], SMOOTH_LABELS[j])
                   for j in range(len(SMOOTHINGS)) for i in range(len(SCORE_VARIANTS)))
# ---- the record's own calibrated constants (source-specified; reproduced verbatim) -----------
TAU_SOFTPLUS = 1.0        # record: softplus scale parameter tau = 1.0
ALPHA_RATE = 0.5          # record: Rate/Drawdown weight alpha = 0.50
LAMBDA_S = 0.5            # record: Stress relief multiplier lambda_s = 0.50
LAMBDA_C = 0.05           # record: Crowding penalty multiplier lambda_c = 0.05
MAX_TILT = 0.5            # record: Maximum active tilt MaxTilt = 50%
TAU_W = 0.75              # record: Tanh temperature scale tau_w = 0.75
ETA_SMOOTH = 0.05         # record: EWMA smoothing rate eta = 0.05
DELTA_WINDOW = 21         # record: the 21-day changes (d TNX / d VIX)
DVOL_PCT_WINDOW = 756     # record: the 756-day (3-year) rolling percentile window
MOMENTUM_WINDOW = 126     # record: the 126-day trailing growth/defensive momentum
# ---- project pre-registered constants (the record leaves these open) -------------------------
Z_MIN_OBS = 60            # registered expanding-z minimum observations
W_G_INIT = 0.5            # registered EWMA seed: the neutral weight
ETA_NONE = 1.0            # registered no-smoothing arm: eta = 1.0 -> w_G,t = w_G,t^target
MATERIALITY_W = 0.5       # registered entry state: the smoothed weight crossing the neutral 0.5
PROBE_CASE = CASE_ORDER[0]     # the case the causality probe re-derives (full / ewma)
PANEL_SIZE = 4            # the registered local eligible universe
PANEL_COHORTS = {}        # filled by main(): the four cohorts the panel is built from
# ---- the registered crypto proxy mapping (operator correction 2026-09-18, contract 6) --------
SPOT_ROOT = "/data/raw/binance/spot/klines"
DVOL_ROOT = "/data/raw/deribit/dvol"
FUNDING_SYMBOL = "BTCUSDT"            # the benchmark funding series carrying the TNX slot
DVOL_ASSET = "BTC"                    # the Deribit BTC implied-vol index carrying the VIX slots
GROWTH_BASKET = ("ETHUSDT", "BNBUSDT", "SOLUSDT")   # local non-BTC growth basket
DEFENSIVE_ASSET = "BTCUSDT"           # the defensive leg: BTC + USDT cash (the numeraire)
DEFENSIVE_CASH = 0.5                  # the registered cash share of the defensive leg
MARKET_ROLE = {"BTCUSDT": "defensive", "ETHUSDT": "growth", "BNBUSDT": "growth",
               "SOLUSDT": "growth"}
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
# Joint parameter space axes, in the one registered order used for the deterministic
# lexical tie-break (contract 7.3).  Order is part of the gate.  `window_case` is the
# composite (score variant, smoothing arm) axis; its registered order is CASE_ORDER.
AXES = ("window_case", "spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct")
DCA_AXES = ("spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct")
SELECTOR_VERSION = "cohort-selector-v1"
DISPOSITION_VERSION = "cohort-disposition-v1"
CONTRACT_SEMANTICS_VERSION = "v1.4.0"
ENGINE_VERSION = "cmt-v1-engine-1.0.0"
ENGINE_SEMANTICS = ("the record's continuous macro-timing score chain (expanding-z direction-"
                    "normalized inputs on the registered crypto proxy mapping, softplus_tau "
                    "smooth components with tau = 1.0, the four registered interactions, the "
                    "alpha / lambda_s / lambda_c composite, the expanding-z re-standardization, "
                    "the tanh target weight with MaxTilt = 0.5 and tau_w = 0.75 and the EWMA "
                    "smoothing eta) turned into target-state change entry events at the next "
                    "bar's open, the record's target exit at the next bar's open, plus the "
                    "registered DCA rail (see module docstring)")
WINNER_METRIC_KEYS = ("net_pnl", "sharpe", "episodes", "ending_equity", "fees", "funding",
                      "max_dd_usdt", "max_dd_pct", "max_effective_leverage",
                      "capital_utilization", "annualized_return", "cagr")
# csv row field order (kept identical for every phase grid)
ROW_FIELDS = ("symbol", "timeframe", "window_kind", "window_case", "case_name",
              "var_full", "var_core", "var_rate", "sm_ewma", "sm_none",
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
# the CLOSED interval [entry - 1s, exit + 1s] and every clock comparison is exact on the
# registered bar grid.  A deviation beyond the tolerance is a real mapping defect.
MS_JITTER_TOLERANCE_MS = 1000
COUNTER_NAMES = ("entry_after_slice_end", "slice_end_flatten", "record_exit_taken",
                 "record_exit_deferred_to_slice_end", "entry_refused_exhausted",
                 "ladder_cap_reached", "stop_at_ladder_boundary",
                 "entry_bar_not_the_next_bar_after_the_signal", "reentry_skipped_in_position",
                 "case_not_registered", "bar_grid_not_contiguous", "funding_bar_out_of_hold",
                 "entry_before_a_defined_signal", "signal_undefined_on_event_bar",
                 "causality_probe_mismatch", "signal_before_the_registered_warmup",
                 "panel_grid_mismatch", "state_not_derived_from_the_registered_rule",
                 "materiality_threshold_violation", "signal_input_alignment_gap",
                 "score_nonfinite_on_a_defined_bar", "expanding_stat_undefined",
                 "defensive_market_missing")
COUNTERS = {}
# ---- registered family-level reader constants (frozen before the first run) -----------------
PLACEBO_SEED = 20260902      # the registered single draw of the stationary random placebo
PERTURB_ALPHA = (0.3, 0.7)   # record: alpha perturbation range
PERTURB_LAMBDA_S = (0.2, 0.6)  # record: lambda_s perturbation range
PERTURB_TAU_W = (0.5, 1.2)   # record: tau_w perturbation range
PERTURB_ETA = (0.02, 0.08)   # record: eta perturbation range
SHARPE_DROP_LIMIT = 0.20     # record: "a drop in Sharpe of greater than 0.20" (source scale)
PERCENTILE_TOLERANCE = 1e-9



def counter(kind, name, inc=1):
    slot = COUNTERS.setdefault(kind, {n: 0 for n in COUNTER_NAMES})
    slot[name] += inc
    return slot[name]


def counters_total(name):
    return sum(v.get(name, 0) for v in COUNTERS.values())


def counters_sum(name, kinds):
    """Structural counters of an explicit set of phase grids (never the diagnostic re-runs)."""
    return sum(COUNTERS.get(k, {}).get(name, 0) for k in kinds)


def counters_snapshot():
    return {k: dict(v) for k, v in COUNTERS.items()}


def counters_delta(before):
    """Per-name delta of the structural counters across one measurement (a single simulate)."""
    out = {}
    for kind, slot in COUNTERS.items():
        prev = before.get(kind, {})
        for name, val in slot.items():
            d = val - prev.get(name, 0)
            if d:
                out[name] = out.get(name, 0) + d
    return out


# ---------------------------------------------------------------------------
# optional inert trace hook (contract v1.6.0 section 28.2, unchanged in v1.8.0)
#
# `TRACE` is the module-level sink used by the survivor-evidence replay driver
# (container/scripts/130_family_survivor_replay.py).  It is None on every
# production path, and every emit point sits behind an explicit
# `if TRACE is not None` guard, so with tracing off the engine executes the very
# same arithmetic in the very same order and returns the very same aggregate
# (enforced by the trace off/on equality check the replay driver runs on every
# replayed cell, and by container/scripts/tests/test_survivor_trace_jkl.py).
# A traced value is only ever observed: nothing recorded here is read back by
# any decision, accounting or return value of the engine.
# ---------------------------------------------------------------------------
TRACE = None


def _trace(event, **fields):
    """Emit one trace record to the optional sink; a no-op when TRACE is None."""
    if TRACE is None:
        return
    TRACE(event, fields)


def _trace_equity(kind, cohort, i0, day_local, series_flat, day_equity):
    """Trace-only equity ledger of one evaluated slice (contract section 28.2).

    `day_local` is the slice's own day index (`np.unique(..., return_inverse=True)`
    inside `simulate`), so the emitted marks are the very series `_flat_series`
    feeds to `_metrics`; `day_start_ms` carries the first bar's open time of each
    day so a replay can label the rows without re-deriving the mapping.
    """
    starts = np.searchsorted(day_local, np.arange(len(series_flat)), side="left")
    _trace("equity_marks", window_kind=kind,
           cohort="%s/%s" % (cohort.symbol, cohort.timeframe),
           day_index=list(range(len(series_flat))),
           day_start_ms=[int(cohort.open_time_ms[i0 + int(s)]) for s in starts],
           equity=[float(v) for v in series_flat],
           in_window=[v is not None for v in day_equity])


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


def day_range(cohort, start_date, end_date):
    """Bar range [lo, hi) of the half-open day interval [start_date, end_date) on the bar grid."""
    lo = utc_ms(start_date)
    hi = utc_ms(end_date)
    return (int(np.searchsorted(cohort.open_time_ms, lo, side="left")),
            int(np.searchsorted(cohort.open_time_ms, hi, side="left")))


def case_tuple(p):
    return tuple(int(p[f]) for f in CASE_FIELDS)


def case_index(case):
    return CASE_ORDER.index(tuple(case))


def case_name(case):
    return CASE_NAMES[case_index(case)]

def case_variant(case):
    """Registered score-variant index of one case: 0..len(SCORE_VARIANTS)-1."""
    return list(case).index(1, 0, len(SCORE_VARIANTS))


def case_smoothing(case):
    """Registered smoothing-arm index of one case (0 = ewma, 1 = none)."""
    return (list(case).index(1, len(SCORE_VARIANTS), len(SCORE_VARIANTS) + len(SMOOTHINGS))
            - len(SCORE_VARIANTS))



# ---------------------------------------------------------------------------
# continuous-macro-timing science  (record-faithful signal; research-defined
# operationalization of every value the record leaves open)
#
#   1. expanding-window Z-scores (ddof = 1, minimum Z_MIN_OBS defined observations) of the
#      direction-normalized inputs on the registered crypto proxy mapping;
#   2. softplus_tau smooth components (tau = 1.0) and RateQuiet = exp(-0.5 r^2);
#   3. the record's four interactions i1..i4 and their own expanding-z transforms;
#   4. the alpha / lambda_s / lambda_c composite and its expanding-z re-standardization;
#   5. the tanh target weight and the EWMA weight path (or the raw target when eta = 1);
#   6. the registered long-only target state (growth markets long above the neutral weight,
#      the defensive market long below it).
#
# Every step is causal: each statistic is trailing, so truncating the input history cannot
# change an earlier bar (the executable check is `causality_probe`).
# ---------------------------------------------------------------------------

_BUNDLE_CACHE = {}
_BUNDLE = None


def _day_str(ms):
    return time.strftime("%Y-%m-%d", time.gmtime(int(ms) / 1000.0))


def _month_files(dirpath):
    if not os.path.isdir(dirpath):
        return []
    return [os.path.join(dirpath, name) for name in sorted(os.listdir(dirpath))
            if name.endswith(".jsonl.gz")]


def read_spot_closes(symbol):
    """Every 1 d close of one Binance SPOT symbol: {YYYY-MM-DD: close} (full history)."""
    out = {}
    for src in _month_files(os.path.join(SPOT_ROOT, symbol, "1d")):
        with gzip.open(src, "rt") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                out[_day_str(r["open_time_ms"])] = float(r["close"])
    return out


def read_dvol_closes(asset):
    """Every 1 d close of one Deribit DVOL index: {YYYY-MM-DD: close}."""
    out = {}
    path = os.path.join(DVOL_ROOT, asset, "deribit-%s-dvol-1d.jsonl.gz" % asset)
    with gzip.open(path, "rt") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            out[_day_str(r["open_time_ms"])] = float(r["close"])
    return out


def read_funding_daily(symbol):
    """The registered benchmark funding series aggregated to a UTC day.

    FUND_d = 365 x sum(funding_rate over every settlement whose funding_time_ms falls in day d):
    the daily total funding paid by a long, annualized — the registered local counterpart of the
    record's annualized macro rate series (the record's own portability note asks for
    "annualized crypto perpetual funding rates").
    """
    out = {}
    counts = {"official": 0, "modeled_funding": 0, "other": 0}
    path = os.path.join(RAW_ROOT, "funding", symbol, "%s-funding.jsonl.gz" % symbol)
    with gzip.open(path, "rt") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            status = r.get("truth_status")
            counts[status if status in ("official", "modeled_funding") else "other"] += 1
            day = _day_str(r["funding_time_ms"])
            out[day] = out.get(day, 0.0) + float(r["funding_rate"])
    return {day: 365.0 * rate for day, rate in out.items()}, counts


def signal_bundle():
    """The registered market-level input bundle, read once from the read-only raw store.

    The calendar is the sorted union of the dates carried by the source series; a series that
    does not cover a date contributes NaN there (never an interpolation and never a
    back-fill), and the first bar on which every registered input is defined is the measured
    signal warm-up.
    """
    global _BUNDLE
    if _BUNDLE is not None:
        return _BUNDLE
    btc = read_spot_closes(DEFENSIVE_ASSET)
    growth = {s: read_spot_closes(s) for s in GROWTH_BASKET}
    dvol = read_dvol_closes(DVOL_ASSET)
    fund, fund_counts = read_funding_daily(FUNDING_SYMBOL)
    dates = sorted(set(btc) | set(dvol) | set(fund) | set().union(*[set(g) for g in growth.values()]))
    idx = {d: i for i, d in enumerate(dates)}
    n = len(dates)

    def arr(src):
        out = np.full(n, np.nan, dtype=np.float64)
        for day, val in src.items():
            out[idx[day]] = val
        return out

    # G: the equal-weighted growth basket, normalised at the first day ALL its members quote;
    # D: the defensive leg = DEFENSIVE_CASH x BTC + (1 - DEFENSIVE_CASH) x USDT cash.
    members = [arr(growth[s]) for s in GROWTH_BASKET]
    g = np.full(n, np.nan, dtype=np.float64)
    live = np.ones(n, dtype=bool)
    for col in members:
        live &= np.isfinite(col)
    base_i = int(np.flatnonzero(live)[0]) if bool(live.any()) else None
    if base_i is not None:
        cols = [col / col[base_i] for col in members]
        g = np.mean(np.vstack(cols), axis=0)
        g[:base_i] = np.nan
    btc_arr = arr(btc)
    d_base = float(np.nan)
    ps = np.flatnonzero(np.isfinite(btc_arr))
    if ps.size:
        d_base = float(btc_arr[ps[0]])
    d = DEFENSIVE_CASH * (btc_arr / d_base) + (1.0 - DEFENSIVE_CASH)
    d[~np.isfinite(btc_arr)] = np.nan
    _BUNDLE = {"dates": dates, "index": idx, "n": n, "btc": btc_arr, "g": g, "d": d,
               "dvol": arr(dvol), "fund": arr(fund),
               "funding_counts": fund_counts,
               "btc_first_ms": None, "dvol_first": (min(dvol) if dvol else None),
               "fund_first": (min(fund) if fund else None),
               "g_first": (dates[base_i] if base_i is not None else None),
               "source": {"spot": "%s/<SYMBOL>/1d" % SPOT_ROOT,
                          "dvol": "%s/%s/deribit-%s-dvol-1d.jsonl.gz" % (DVOL_ROOT, DVOL_ASSET,
                                                                        DVOL_ASSET),
                          "funding": "%s/funding/%s" % (RAW_ROOT, FUNDING_SYMBOL)}}
    return _BUNDLE


def _softplus(x, tau=TAU_SOFTPLUS):
    """softplus_tau(x) = tau log(1 + exp(x/tau)), numerically stable."""
    z = x / tau
    return tau * (np.maximum(z, 0.0) + np.log1p(np.exp(-np.abs(z))))


def _diff_window(x, window):
    """x_t - x_{t-window} (NaN while either end is undefined)."""
    out = np.full(len(x), np.nan, dtype=np.float64)
    for i in range(window, len(x)):
        if np.isfinite(x[i]) and np.isfinite(x[i - window]):
            out[i] = x[i] - x[i - window]
    return out


def _expanding_z(x):
    """The record's expanding-window Z-score: (x_t - mean_{1..t}) / std_{1..t}, ddof = 1.

    Defined only once Z_MIN_OBS values have been seen; an undefined input leaves the output
    undefined (fail-closed) rather than carrying the last value.
    """
    out = np.full(len(x), np.nan, dtype=np.float64)
    n = 0
    s = 0.0
    ss = 0.0
    for i in range(len(x)):
        v = x[i]
        if not np.isfinite(v):
            continue
        n += 1
        s += v
        ss += v * v
        if n < Z_MIN_OBS:
            continue
        var = (ss - s * s / n) / (n - 1)
        if var <= 0.0:
            counter("signal", "expanding_stat_undefined")
            continue
        out[i] = (v - s / n) / math.sqrt(var)
    return out


def _rolling_percentile(x, window):
    """The record's rolling percentile rank: the share of the trailing `window` OBSERVATIONS
    (including the current one) that are <= x_t; undefined until `window` observations exist.

    Registered convention (research-defined): the rank is `count(values <= x_t) / window`, so a
    tie with the running maximum scores exactly 1.0; the window counts defined observations, not
    calendar slots.
    """
    out = np.full(len(x), np.nan, dtype=np.float64)
    win = []
    queue = []
    for i in range(len(x)):
        v = x[i]
        if not np.isfinite(v):
            continue
        bisect.insort(win, v)
        queue.append(v)
        if len(win) > window:
            old = queue.pop(0)
            win.pop(bisect.bisect_left(win, old))
        if len(win) == window:
            out[i] = float(bisect.bisect_right(win, v)) / float(window)
    return out


def _drawdown(x):
    """Drawdown of a level series against its running maximum over the defined observations
    (<= 0, NaN while the level itself is undefined; the maximum is never reset by a gap)."""
    out = np.full(len(x), np.nan, dtype=np.float64)
    peak = None
    for i in range(len(x)):
        v = x[i]
        if not np.isfinite(v):
            continue
        peak = v if peak is None else max(peak, v)
        out[i] = v / peak - 1.0
    return out


def _relative_momentum(g, d, window):
    """G/D trailing-window momentum: (G_t/G_{t-w}) / (D_t/D_{t-w}) - 1."""
    out = np.full(len(g), np.nan, dtype=np.float64)
    for i in range(window, len(g)):
        if (np.isfinite(g[i]) and np.isfinite(g[i - window])
                and np.isfinite(d[i]) and np.isfinite(d[i - window])):
            out[i] = (g[i] / g[i - window]) / (d[i] / d[i - window]) - 1.0
    return out


def _cmt_features(bundle, upto=None):
    """The registered direction-normalized inputs and interaction terms (causal by construction).

    `upto` truncates every source array at that index (used by the prefix-truncation probe).
    """
    cut = slice(None) if upto is None else slice(0, upto + 1)
    fund = bundle["fund"][cut]
    btc = bundle["btc"][cut]
    dvol = bundle["dvol"][cut]
    g = bundle["g"][cut]
    d = bundle["d"][cut]
    r = -_expanding_z(_diff_window(fund, DELTA_WINDOW))
    ddepth = -_expanding_z(_drawdown(btc))
    vh = _expanding_z(_rolling_percentile(dvol, DVOL_PCT_WINDOW))
    vr = -_expanding_z(_diff_window(dvol, DELTA_WINDOW))
    g126 = _expanding_z(_relative_momentum(g, d, MOMENTUM_WINDOW))
    high_vix = _softplus(vh)
    vix_relief = _softplus(vr)
    low_vix = _softplus(-vh)
    growth_ext = _softplus(g126)
    rate_quiet = np.exp(-0.5 * r * r)
    i1 = r * vh
    i2 = high_vix * vix_relief
    i3 = growth_ext * low_vix
    i4 = growth_ext * low_vix * rate_quiet
    return {"rate": r, "ddepth": ddepth, "vh": vh, "vr": vr, "g126": g126,
            "stress": 0.5 * _expanding_z(i1) + 0.5 * _expanding_z(i2),
            "crowded": 0.5 * _expanding_z(i3) + 0.5 * _expanding_z(i4)}


def _ewma_weight_path(target, eta):
    """w_G,t = (1-eta) w_G,t-1 + eta w_G,t^target, seeded at the registered neutral weight on the
    first defined target bar; an undefined target leaves the weight undefined (fail-closed)."""
    out = np.full(len(target), np.nan, dtype=np.float64)
    prev = W_G_INIT
    started = False
    for i in range(len(target)):
        tv = target[i]
        if not np.isfinite(tv):
            continue
        if not started:
            prev = W_G_INIT
            started = True
        prev = (1.0 - eta) * prev + eta * tv
        out[i] = prev
    return out


def score_path(features, variant, smoothing, alpha=ALPHA_RATE, lambda_s=LAMBDA_S,
               lambda_c=LAMBDA_C, tau_w=TAU_W, eta=ETA_SMOOTH):
    """The registered composite score -> the target and realised growth-basket weight."""
    if variant == "full":
        core = alpha * features["rate"] + (1.0 - alpha) * features["ddepth"]
        raw = core + lambda_s * features["stress"] - lambda_c * features["crowded"]
    elif variant == "core_only":
        raw = alpha * features["rate"] + (1.0 - alpha) * features["ddepth"]
    elif variant == "rate_only":
        raw = features["rate"]
    else:
        raise SystemExit("unregistered score variant %r (registered: %r)"
                         % (variant, SCORE_VARIANTS))
    score = _expanding_z(raw)
    target = 0.5 + MAX_TILT * np.tanh(score / tau_w)
    weight = target if smoothing == "none" else _ewma_weight_path(target, eta)
    return {"score": score, "target": target, "weight": weight, "raw": raw}


def _states_from_weight(weight, markets):
    """The registered long-only target state of every market.

    Growth-basket instruments are LONG exactly while the realised growth weight is above the
    neutral MATERIALITY_W; the defensive instrument is LONG exactly while it is below; an
    undefined weight carries state 0.  The record never shorts and never levers.
    """
    st = np.zeros((len(weight), len(markets)), dtype=np.int64)
    for j, m in enumerate(markets):
        role = MARKET_ROLE.get(m)
        if role is None:
            counter("signal", "defensive_market_missing")
            raise SystemExit("market %r has no registered basket role" % m)
        if role == "defensive":
            st[:, j] = np.where(np.isfinite(weight) & (weight < MATERIALITY_W), 1, 0)
        else:
            st[:, j] = np.where(np.isfinite(weight) & (weight > MATERIALITY_W), 1, 0)
    return st


def _panel_core(case, closes_by_market, upto=None):
    """The registered signal of ONE strategy case over the registered universe.

    One market-level score path (the signal is a market-timing score, not a per-asset
    forecast) mapped onto the four instruments by their registered basket role.  The path is
    read on the cohort's own trading grid; a trading date the signal calendar does not carry is
    a measured alignment gap (fail-closed, never interpolated).
    """
    labels = sorted(closes_by_market)
    if len(labels) != PANEL_SIZE:
        counter("signal", "panel_grid_mismatch")
        raise SystemExit("the registered panel needs exactly %d instruments, got %r"
                         % (PANEL_SIZE, labels))
    bundle = signal_bundle()
    feats = _cmt_features(bundle, upto=upto)
    variant = SCORE_VARIANTS[case_variant(case)]
    smoothing = SMOOTHINGS[case_smoothing(case)]
    path = score_path(feats, variant, smoothing)
    grid_ms = None
    for m in labels:
        ms = np.asarray(closes_by_market[m], dtype=np.int64)
        if grid_ms is None:
            grid_ms = ms
        elif not np.array_equal(grid_ms, ms):
            counter("signal", "panel_grid_mismatch")
            raise SystemExit("panel series do not share one bar grid: %r" % labels)
    n = len(grid_ms)
    idx = bundle["index"]
    weight = np.full(n, np.nan, dtype=np.float64)
    score = np.full(n, np.nan, dtype=np.float64)
    for k in range(n):
        j = idx.get(_day_str(grid_ms[k]))
        if j is None:
            counter("signal", "signal_input_alignment_gap")
            continue
        if upto is not None and j > upto:
            # a truncated history cannot know the bar's own future: the probe builds the past
            # only, and the compared bars must still reproduce their value
            continue
        weight[k] = path["weight"][j]
        score[k] = path["score"][j]
    state = _states_from_weight(weight, labels)
    events = _fresh_events(state)
    rule = np.zeros(state.shape, dtype=np.int64)
    for j, m in enumerate(labels):
        role = MARKET_ROLE[m]
        if role == "defensive":
            rule[:, j] = np.where(np.isfinite(weight) & (weight < MATERIALITY_W), 1, 0)
        else:
            rule[:, j] = np.where(np.isfinite(weight) & (weight > MATERIALITY_W), 1, 0)
    bad = int(np.count_nonzero(rule != state))
    if bad:
        counter("signal", "state_not_derived_from_the_registered_rule", bad)
    finite = np.isfinite(weight)
    pos = np.flatnonzero(finite)
    first = int(pos[0]) if pos.size else n
    diag = {"variant": variant, "smoothing": smoothing,
            "defined_observations": int(pos.size),
            "first_defined_date": (_day_str(grid_ms[first]) if pos.size else None),
            "last_defined_date": (_day_str(grid_ms[pos[-1]]) if pos.size else None),
            "weight_mean": (round(float(weight[finite].mean()), 6) if pos.size else None),
            "weight_min": (round(float(weight[finite].min()), 6) if pos.size else None),
            "weight_max": (round(float(weight[finite].max()), 6) if pos.size else None),
            "score_mean": (round(float(np.nanmean(score)), 6) if pos.size else None),
            "bars_above_neutral": int(np.count_nonzero(finite & (weight > MATERIALITY_W))),
            "bars_below_neutral": int(np.count_nonzero(finite & (weight < MATERIALITY_W))),
            "long_bars": {m: int((state[:, j] > 0).sum()) for j, m in enumerate(labels)},
            "events_per_market": {m: int((events[:, j] != 0).sum())
                                  for j, m in enumerate(labels)}}
    return {"labels": labels, "n": n, "state": state, "events": events,
            "weight": weight, "score": score, "bundle": bundle,
            "grid_ms": np.asarray(grid_ms, dtype=np.int64),
            "variant": variant, "smoothing": smoothing, "case": tuple(case),
            "warmup_bars": first, "defined_bars": int(pos.size),
            "long_bars": diag["long_bars"], "score_diagnostics": diag}


_PANEL_CACHE = {}


def _panel_core_cached(case):
    """The cached panel science of one case over the LIVE registered cohorts."""
    if not PANEL_COHORTS:
        raise SystemExit("panel requested before the cohorts were registered")
    key = tuple(case)
    core = _PANEL_CACHE.get(key)
    if core is None:
        core = _panel_core(key, {m: PANEL_COHORTS[m].open_time_ms for m in sorted(PANEL_COHORTS)})
        _PANEL_CACHE[key] = core
    return core




def _fresh_events(state):
    """Registered entry events: a bar whose target state CHANGES to a non-zero value."""
    n, k = state.shape
    events = np.zeros((n, k), dtype=np.int64)
    prev = np.zeros(k, dtype=np.int64)
    for t in range(n):
        cur = state[t]
        fresh = (cur != prev) & (cur != 0)
        events[t] = np.where(fresh, cur, 0)
        prev = cur
    return events


class Panel:
    """The registered four-instrument panel of one (score variant, smoothing arm) case.

    The score is market-level, so the panel carries ONE weight path and the four instruments
    differ only by their registered basket role; both arms of the case axis change the score
    path itself (not a gate on top of it).
    """

    _cache = {}

    def __init__(self, case, cohorts):
        self.case = tuple(case)
        self.case_label = case_name(self.case)
        self.variant = SCORE_VARIANTS[case_variant(self.case)]
        self.smoothing = SMOOTHINGS[case_smoothing(self.case)]
        labels = sorted(cohorts)
        core = _panel_core_cached(self.case)
        if labels != core["labels"]:
            raise SystemExit("panel labels %r != registered %r" % (labels, core["labels"]))
        self.markets = labels
        self.n = core["n"]
        self.core = core
        self.state = core["state"].astype(np.int64)
        self.events = core["events"]
        self.weights = np.repeat(core["weight"][:, None], len(labels), axis=1)
        self.diag = {
            "case_name": self.case_label, "variant": self.variant, "smoothing": self.smoothing,
            "panel": labels, "bars": int(self.n),
            "warmup_bars": int(core["warmup_bars"]),
            "defined_bars": int(core["defined_bars"]),
            "defined_bar_fraction": round(float(core["defined_bars"]) / float(max(1, self.n)), 6),
            "states_per_market": {m: {"long": int(core["long_bars"][m]),
                                      "flat": int(self.n - core["long_bars"][m]),
                                      "events": int((self.events[:, j] != 0).sum())}
                                  for j, m in enumerate(labels)},
            "materiality_weight": MATERIALITY_W,
            "policy_note": "the registered target state is the long-only weight reading: LONG a "
                           "growth-basket instrument while w_G > 0.5, LONG the defensive "
                           "instrument while w_G < 0.5; the alternative readings (a dead-band "
                           "around 0.5, the continuous weight as a position size) are NOT "
                           "evaluated (disclosed)",
        }

    def layer_for(self, market):
        i = self.markets.index(market)
        return {"pos": np.ascontiguousarray(self.state[:, i]),
                "events": np.ascontiguousarray(self.events[:, i]),
                "yhat": np.ascontiguousarray(self.core["score"])}

    def report(self):
        out = dict(self.diag)
        out["score_diagnostics"] = self.core["score_diagnostics"]
        return out


def panel_for(case):
    """Cached panel of one registered case over the registered universe."""
    key = tuple(case)
    p = Panel._cache.get(key)
    if p is None:
        if not PANEL_COHORTS:
            raise SystemExit("panel requested before the cohorts were registered")
        p = Panel(key, PANEL_COHORTS)
        Panel._cache[key] = p
    return p


class SignalLayer:
    """One (cohort, case) signal layer: the market-level weight path mapped onto the cohort's
    own trading grid, its long-only target state and its entry events.  Built once per case and
    cached (the DCA grid re-reads it 48x per phase grid)."""

    def __init__(self, cohort, case, spec, panel=None):
        self.case = tuple(case)
        self.case_label = case_name(self.case)
        panel = panel if panel is not None else panel_for(self.case)
        if cohort.symbol not in panel.markets:
            raise SystemExit("cohort %s is not a member of the registered panel %r"
                             % (cohort.symbol, panel.markets))
        view = panel.layer_for(cohort.symbol)
        self.pos = view["pos"]
        self.events = view["events"]
        self.yhat = view["yhat"]
        self.panel = panel
        self.diag = dict(panel.diag)
        self.diag["cohort"] = cohort.symbol
        self.diag["states_per_market"] = panel.diag["states_per_market"][cohort.symbol]
        self.diag["role"] = MARKET_ROLE[cohort.symbol]

    def report(self):
        return {"case_name": self.case_label, "variant": self.diag["variant"],
                "smoothing": self.diag["smoothing"], "role": self.diag["role"],
                "defined_bars": self.diag["defined_bars"],
                "states_per_market": self.diag["states_per_market"],
                "warmup_bars": self.diag["warmup_bars"]}


def signals_for(cohort, case):
    """The cached signal layer of one registered strategy case (frozen engine seam)."""
    key = tuple(case)
    layer = cohort._signal_cache.get(key)
    if layer is None:
        layer = SignalLayer(cohort, key, cohort.spec)
        cohort._signal_cache[key] = layer
    return layer


def signal_report_of(cohort):
    """The registered signal diagnostics of every case of one cohort."""
    out = {}
    for case in CASE_ORDER:
        layer = cohort._signal_cache.get(case)
        if layer is None:
            continue
        out[layer.case_label] = layer.report()
    return out


class SyntheticLayer:
    """A reader-side signal layer (stationary-random placebo / parameter perturbation).

    The registered readers re-derive the registered score from a modified input bundle or a
    perturbed parameter set; the re-derived path enters the SAME rail through the same
    interface `simulate` reads, so no reader is ever a post-hoc estimate.
    """

    def __init__(self, cohort, markets, weight, score, label, note):
        i = markets.index(cohort.symbol)
        state = _states_from_weight(weight, markets)
        self.case_label = label
        self.note = note
        self.pos = np.ascontiguousarray(state[:, i])
        self.events = np.ascontiguousarray(_fresh_events(state)[:, i])
        self.yhat = np.ascontiguousarray(np.asarray(score, dtype=np.float64))


def build_network_inputs(cohort):
    """The registered causal input of one cohort.

    The record's score is a MARKET-LEVEL macro composite: it reads the shared signal bundle
    (funding, DVOL, the benchmark spot drawdown and the growth/defensive baskets) on the
    cohort's own trading grid, never the cohort's own future.  The cohort's own close and
    return series are carried for the rail and for the alignment guards.
    """
    close = np.asarray(cohort.close, dtype=np.float64)
    ret = np.zeros_like(close)
    if len(close) > 1:
        ret[1:] = close[1:] / close[:-1] - 1.0
    return {"ret": ret, "close": close, "names": ["ret"],
            "grid_ms": np.asarray(cohort.open_time_ms, dtype=np.int64)}


def causality_probe(cohort, inputs, sample_bars=16):
    """Prefix-truncation equality on the registered signal of one cohort.

    Every statistic of the registered chain is trailing, so the score (and therefore the target
    state) of a bar must be reproduced when the source history is truncated at that bar's date.
    A non-zero mismatch count is a look-ahead defect.
    """
    out = {"bars_probed": 0, "mismatches": 0, "columns": ["weight", "state"],
           "examples": [], "probe_case": case_name(PROBE_CASE),
           "variant": SCORE_VARIANTS[case_variant(PROBE_CASE)],
           "note": "prefix-truncation equality on the registered continuous-macro-timing score; "
                   "a non-zero mismatch count is a look-ahead defect"}
    if not PANEL_COHORTS or cohort.symbol not in PANEL_COHORTS:
        out["note"] = "the panel was not registered when the probe ran"
        return out
    labels = sorted(PANEL_COHORTS)
    if cohort.symbol not in labels:
        return out
    core = _panel_core_cached(PROBE_CASE)
    grid = core["grid_ms"]
    T = len(grid)
    lo = int(core["warmup_bars"])
    if T <= lo + 1:
        return out
    step = max(1, (T - 1 - lo) // max(1, sample_bars))
    bundle = core["bundle"]
    for t in range(lo, T - 1, step):
        upto = bundle["index"].get(_day_str(grid[t]))
        if upto is None:
            continue
        cut = _panel_core(PROBE_CASE, {m: np.asarray(PANEL_COHORTS[m].open_time_ms,
                                                     dtype=np.int64) for m in labels},
                          upto=upto)
        a = cut["weight"][:t + 1]
        b = core["weight"][:t + 1]
        same_defined = bool(np.array_equal(np.isfinite(a), np.isfinite(b)))
        fin = np.isfinite(a) & np.isfinite(b)
        dw = float(np.max(np.abs(a[fin] - b[fin]))) if bool(fin.any()) else 0.0
        ds = int(np.max(np.abs(cut["state"][:t + 1, :] - core["state"][:t + 1, :])))
        out["bars_probed"] += 1
        if not (same_defined and dw <= 1e-9 and ds == 0):
            out["mismatches"] += 1
            counter("signal", "causality_probe_mismatch")
            if len(out["examples"]) < 5:
                out["examples"].append({"bar": int(t), "weight_dev": dw, "state_dev": ds,
                                        "same_defined_bars": same_defined})
    return out


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
    step = BAR_MS[interval]
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
                    if first_ms is not None and ms - last_ms != step:
                        bad_grid += 1
                    ts = time.gmtime(ms / 1000.0)
                    if interval == "1d":
                        stamp = "%04d-%02d-%02d" % (ts.tm_year, ts.tm_mon, ts.tm_mday)
                    else:
                        stamp = "%04d-%02d-%02d %02d:%02d:%02d" % (
                            ts.tm_year, ts.tm_mon, ts.tm_mday, ts.tm_hour, ts.tm_min, ts.tm_sec)
                    w.writerow([stamp, r["open"], r["high"], r["low"], r["close"], r["volume"]])
                    rows += 1
                    if first_ms is None:
                        first_ms = ms
                    last_ms = ms
    return {"rows": rows, "first_open_time_ms": first_ms, "last_open_time_ms": last_ms,
            "csv": out_path, "csv_bytes": os.path.getsize(out_path),
            "off_grid_steps": bad_grid, "bar_step_ms": step}


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
    symbols = list(data["symbols"]) + list(
        (spec.get("falsification") or {}).get("cross_asset_symbols", []))
    # /qlib/work is a rebuildable derived/cache area (INV-5): rebuild from scratch so
    # the recorded build wall time / size describe THIS attempt's store.
    if os.path.isdir(WORK_ROOT):
        shutil.rmtree(WORK_ROOT)
    per_dataset = {}
    for symbol in symbols:
        for tf in data["timeframes"]:
            key = "%s/%s" % (symbol, tf["raw_interval"])
            per_dataset[key] = write_csv(symbol, tf["raw_interval"], tf["qlib_freq"], start, end)
            run_log("csv %s rows=%d bytes=%d off_grid_steps=%d"
                    % (key, per_dataset[key]["rows"], per_dataset[key]["csv_bytes"],
                       per_dataset[key]["off_grid_steps"]))

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
    for symbol in symbols:
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
                "raw_rows": ds["rows"], "off_grid_steps": ds["off_grid_steps"]}
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
    """Bars of one (symbol, timeframe) cohort, loaded through the Qlib data layer.

    Carries the registered causal signal inputs (`build_network_inputs`), the panel signal-layer
    cache and the record's own univariate-baseline cache (falsification reader only)."""

    def __init__(self, symbol, tf, start, end, spec=None):
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
        self._signal_cache = {}
        self._pred_cache = {}
        self.spec = spec
        self.features = None
        # the registered causal signal inputs (prefix invariance is probed by `causality_probe`
        # and reported with the run)
        self.inputs = build_network_inputs(self)
        self.inputs_report = {}
        self._baseline_cache = {}
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

    def signals_for(self, case):
        """The cached signal layer of one registered strategy case (see `signals_for`)."""
        return signals_for(self, case)


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
    # Two bar mappings, because they answer two different questions; only `settle_bar` schedules a
    # charge, so the charge can never move because of the guard:
    #   settle_bar        - the bar whose CLOSE is the mark price at the settlement instant (the bar
    #                       that ENDS at it): the charge scheduling of `simulate`, unchanged.
    #   settle_bar_closed - the bar that CONTAINS the instant, half-open [open, open + 1h).  The two
    #                       differ only for an instant landing EXACTLY on a bar open, which the raw
    #                       data really does (0 ms jitter, measured 2025-10-01T00:00Z on all four
    #                       symbols): an ON-TIME settlement belongs to the bar it opens, so the
    #                       out-of-hold guard must not push it one bar out of the hold.  The guard
    #                       reads this one only.
    closed = np.searchsorted(cohort.open_time_ms, times, side="right") - 1
    return {"label": label, "counts": counts, "obs_times": times, "obs_rates": rates,
            "settle_bar": containing.astype(np.int64),
            "settle_bar_closed": closed.astype(np.int64),
            "first_obs_ms": int(times[0]) if len(times) else None,
            "last_obs_ms": int(times[-1]) if len(times) else None}


def fresh_events_of_slice(layer, i0, i1, delay, kind):
    """Every registered entry event of one slice: (slice-local event bar, sign).

    A fresh signal event is the entry event; the entry bar is the NEXT base bar (optionally
    moved by the registered `entry_delay_1_bar` execution stress) and must lie inside the
    slice, otherwise the event is skipped and counted (`entry_after_slice_end`) - it is never
    pulled back into the slice.
    """
    n = i1 - i0
    out = []
    for bl in np.flatnonzero(layer.events[i0:i1] != 0):
        b = int(bl)
        if b + 1 + delay >= n:
            counter(kind, "entry_after_slice_end")
            continue
        out.append((b, int(layer.events[i0 + b])))
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


def simulate(cohort, p, rail, window, stress, slip_ticks, kind, series, diag=False,
             count_layers=True, layer=None):
    """One pre-registered parameter case over one window slice (i0, i1).

    `stress` may carry: fee_mult / funding_mult / entry_delay_1_bar / slip_ticks / no_funding.
    The episode source is the registered signal layer: a fresh signal event at bar b enters at
    O[b+1+delay]; the episode is then held bar by bar through the registered exits (rail TP,
    resting invalidation, margin backstop, the record's target-state exit) and finally the slice
    end.  Long episodes walk the bar LOW first then the HIGH; short
    episodes mirror that sym-directionally (HIGH first, then LOW).

    `count_layers=False` keeps one call out of the full-window DCA ladder histogram: the winner
    and singleton-leg diagnostics re-run cells the grid already counted (card t_50c28da5).
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
    layer = signals_for(cohort, case) if layer is None else layer
    pos_path = layer.pos
    episodes = fresh_events_of_slice(layer, i0, i1, delay, kind)
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
        settle_bar_closed = np.array([], dtype=np.int64)
    else:
        fund_times = series["obs_times"]
        fund_rates = series["obs_rates"] * stress.get("funding_mult", 1.0)
        settle_bar = series["settle_bar"]
        # guard-only containment mapping; a caller that predates it falls back to the scheduling bar
        settle_bar_closed = series.get("settle_bar_closed", series["settle_bar"])
    nfund = int(len(fund_times))

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
    ep_stats = {"episodes": 0, "wins": 0, "net_pnl": 0.0, "hold_bars": 0}
    # Trace-only per-episode bookkeeping (contract 28.2): computed alongside the engine's
    # own ledgers and never read back by the engine.  `ep_reason` / `ep_trigger` / `ep_ref`
    # are set at the exit that closes the episode and read only at the one emit point below.
    ep_mae = ep_mfe = 0.0
    ep_reason = ep_trigger = ep_ref = None

    def charge_fee(amount):
        """A fee is paid at the instant of the fill and must reduce the realised equity there."""
        nonlocal realized, fees_total
        fees_total += amount
        realized -= amount
        return amount

    if not episodes:
        series_flat = _flat_series(day_equity)
        if TRACE is not None:
            _trace_equity(kind, cohort, i0, day_local, series_flat, day_equity)
        return _metrics(realized, fees_total, funding_paid, gross_pnl, 0, tp_hits, stop_hits,
                        open_at_end, margin_calls, time_exits, False, START_EQUITY,
                        series_flat, cohort, i0, i1, layers, max_lev, util_sum, bars_in_market,
                        fills, turnover, windows_seen, windows_entered, kind, {})

    halted = False
    min_entry_equity = START_EQUITY
    last_exit_bar = -1
    for b, sign in episodes:
        if b < last_exit_bar:
            # an event whose entry bar lies inside the previous hold is skipped: no re-entry
            # without a new, later position-change event.  An event exactly ON the previous
            # flatten bar is the long-short FLIP of the registered rule (flatten and enter at
            # the same open) and is therefore allowed.
            counter(kind, "reentry_skipped_in_position")
            continue
        # registered capital-exhaustion semantics: the account cannot lose more than itself,
        # so no new position may be opened once the realised equity is gone
        if START_EQUITY + realized <= 0.0:
            halted = True
            counter(kind, "entry_refused_exhausted")
            continue
        min_entry_equity = min(min_entry_equity, START_EQUITY + realized)
        eb = b + 1 + delay
        # executable mapping guard: the entry bar is the NEXT base bar after the signal event
        # (and the event really is a FRESH signal, i.e. not the previous bar's signal again)
        if (int(layer.events[i0 + b]) != sign
                or (i0 + b > 0 and int(layer.events[i0 + b - 1]) == sign)
                or not np.isfinite(layer.yhat[i0 + b])
                or int(cohort.open_time_ms[i0 + eb])
                != int(cohort.open_time_ms[i0 + b]) + (1 + delay) * cohort.bar_ms):
            counter(kind, "entry_bar_not_the_next_bar_after_the_signal")
        if not np.isfinite(layer.yhat[i0 + b]):
            counter(kind, "entry_before_a_defined_signal")
        entry_ms = int(cohort.open_time_ms[i0 + eb])
        windows_entered += 1
        # The card registers "1 tick ADVERSE (by instrument price_increment)" for every fill:
        # a long entry pays UP, a short entry sells LOWER (the D/G spelling copied into this
        # file had `O - sign * tick`, i.e. a one-tick CREDIT on the entry; corrected here and
        # pinned for every fill leg by the self-check).
        px = O[eb] + sign * slip_ticks * tick
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
        if TRACE is not None:
            ep_mae = 0.0
            ep_mfe = 0.0
            _trace("fill", episode=windows_entered - 1, event_type="ENTRY",
                   bar_index=i0 + eb, open_time_ms=entry_ms, price=px, qty=qty, dca_level=0,
                   trigger_price=None, ref_price=O[eb], fee=qty * px * taf,
                   slip_ticks=slip_ticks, sign=sign)
        levels = [p0 * (1.0 - sign * d0 * kk) for kk in range(12)]
        layers[0] += 1
        # funding: every settlement inside the CLOSED exposure interval [entry, exit] with the
        # measured +1 s jitter tolerance, charged in bar order so a later scale-in never
        # retroactively changes an earlier charge.
        kptr = int(np.searchsorted(fund_times, entry_ms - MS_JITTER_TOLERANCE_MS, side="left"))
        broke = False
        exit_bar = None
        exit_at_open = False
        t = eb
        while t < n:
            close_ms = int(cohort.open_time_ms[i0 + t]) + cohort.bar_ms
            while kptr < nfund and int(fund_times[kptr]) <= close_ms + MS_JITTER_TOLERANCE_MS:
                # the guard reads the CONTAINMENT bar (an on-time settlement belongs to the bar
                # it opens); the charge below keeps using the scheduling bar, so no charge moves.
                if not (i0 + eb <= int(settle_bar_closed[kptr]) <= i0 + t + 1):
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
                        if kk > MAX_ADD_LEVELS:
                            counter(kind, "stop_at_ladder_boundary")
                        break
                    fpx = trig + slip_ticks * tick
                    q = base * (mult ** kk) * lev / fpx
                    qty += q
                    cost += q * fpx
                    charge_fee(q * fpx * taf)
                    ep_fees += q * fpx * taf
                    if TRACE is not None:
                        _trace("fill", episode=windows_entered - 1, event_type="DCA_ADD",
                               bar_index=i0 + t, open_time_ms=int(cohort.open_time_ms[i0 + t]),
                               price=fpx, qty=q, dca_level=kk, trigger_price=trig,
                               ref_price=trig, fee=q * fpx * taf, slip_ticks=slip_ticks,
                               sign=sign)
                    fills += 1
                    turnover += q * fpx
                    layers[kk] += 1
                    if kk == MAX_ADD_LEVELS:
                        counter(kind, "ladder_cap_reached")
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
                        if kk > MAX_ADD_LEVELS:
                            counter(kind, "stop_at_ladder_boundary")
                        break
                    fpx = trig - slip_ticks * tick
                    q = base * (mult ** kk) * lev / fpx
                    qty += q
                    cost += q * fpx
                    charge_fee(q * fpx * taf)
                    ep_fees += q * fpx * taf
                    if TRACE is not None:
                        _trace("fill", episode=windows_entered - 1, event_type="DCA_ADD",
                               bar_index=i0 + t, open_time_ms=int(cohort.open_time_ms[i0 + t]),
                               price=fpx, qty=q, dca_level=kk, trigger_price=trig,
                               ref_price=trig, fee=q * fpx * taf, slip_ticks=slip_ticks,
                               sign=sign)
                    fills += 1
                    turnover += q * fpx
                    layers[kk] += 1
                    if kk == MAX_ADD_LEVELS:
                        counter(kind, "ladder_cap_reached")
                    kk += 1
            eq = START_EQUITY + realized
            ueq = eq + sign * (qty * C[t] - cost)
            if TRACE is not None:
                excursion = ueq - eq
                ep_mae = min(ep_mae, excursion)
                ep_mfe = max(ep_mfe, excursion)
            if killed_at is None and ueq <= cohort.margin_maint * qty * C[t]:
                xpx = C[t] - sign * slip_ticks * tick  # capital-exhaustion exit, adverse
                ep_gross = exit_price_pnl(sign * qty * xpx, sign * cost)
                realized += sign * qty * xpx - sign * cost
                gross_pnl += ep_gross
                charge_fee(qty * xpx * taf)
                ep_fees += qty * xpx * taf
                fills += 1
                turnover += qty * xpx
                margin_calls += 1
                exit_bar = t
                broke = True
                if TRACE is not None:
                    ep_reason, ep_trigger, ep_ref = "MARGIN_CALL", C[t], C[t]
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
                xpx = killed_at - sign * slip_ticks * tick
                ep_gross = exit_price_pnl(sign * qty * xpx, sign * cost)
                realized += sign * qty * xpx - sign * cost
                gross_pnl += ep_gross
                charge_fee(qty * xpx * taf)
                ep_fees += qty * xpx * taf
                fills += 1
                turnover += qty * xpx
                stop_hits += 1
                exit_bar = t
                broke = True
                if TRACE is not None:
                    ep_reason, ep_trigger, ep_ref = "STOP", killed_at, L[t]
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
                exit_bar = t
                broke = True
                if TRACE is not None:
                    ep_reason, ep_trigger, ep_ref = "TP", tpx, H[t]
                break
            # registered exit (3): the record's own exit condition, evaluated at this bar's
            # close.  The filtered target position is no longer the episode's direction (0 in
            # long-only mode, the opposite sign in long-short mode), so every layer is flattened
            # reduce-only at the NEXT bar's open.  A long-short flip is therefore a flatten at
            # that open, with the flip's own entry event carrying the new episode at the same
            # open (see the `b < last_exit_bar` guard above).
            pos_now = int(pos_path[i0 + t]) if (i0 + t) < len(pos_path) else 0
            if pos_now != sign:
                if t + 1 < n:
                    xpx = O[t + 1] - sign * slip_ticks * tick
                    ep_gross = exit_price_pnl(sign * qty * xpx, sign * cost)
                    realized += sign * qty * xpx - sign * cost
                    gross_pnl += ep_gross
                    charge_fee(qty * xpx * taf)
                    ep_fees += qty * xpx * taf
                    fills += 1
                    turnover += qty * xpx
                    time_exits += 1
                    counter(kind, "record_exit_taken")
                    exit_bar = t
                    exit_at_open = True
                    broke = True
                    if TRACE is not None:
                        ep_reason, ep_trigger, ep_ref = "TIME_EXIT", None, O[t + 1]
                    break
                # an exit condition on the last bar has no next open to execute at: it is
                # deferred to the slice-end flatten
                counter(kind, "record_exit_deferred_to_slice_end")
            t += 1
        if not broke:
            # slice end: hard, reduce-only flatten of every layer
            exit_bar = n - 1
            xpx = C[exit_bar] - sign * slip_ticks * tick
            ep_gross = exit_price_pnl(sign * qty * xpx, sign * cost)
            realized += sign * qty * xpx - sign * cost
            gross_pnl += ep_gross
            charge_fee(qty * xpx * taf)
            ep_fees += qty * xpx * taf
            fills += 1
            turnover += qty * xpx
            open_at_end += 1
            counter(kind, "slice_end_flatten")
            if TRACE is not None:
                ep_reason, ep_trigger, ep_ref = "EOD_FLATTEN", C[-1], C[-1]
        # settlements at the exit instant itself (the hold's closing boundary) are charged on
        # the exit notional, which is the price at risk when the settlement lands
        if exit_at_open:
            exit_ms = int(cohort.open_time_ms[i0 + exit_bar + 1])
        else:
            exit_ms = int(cohort.open_time_ms[i0 + exit_bar]) + cohort.bar_ms
        while kptr < nfund and int(fund_times[kptr]) <= exit_ms + MS_JITTER_TOLERANCE_MS:
            # containment bar again: an on-time settlement exactly on the exit boundary belongs
            # to the bar it opens, i.e. the first bar after the hold - not a defect
            if not (i0 + eb <= int(settle_bar_closed[kptr]) <= i0 + exit_bar + 1):
                counter(kind, "funding_bar_out_of_hold")
            ep_fund += sign * qty * C[exit_bar] * fund_rates[kptr]
            kptr += 1
        if TRACE is not None:
            exit_bar_index = i0 + exit_bar + (1 if exit_at_open else 0)
            _trace("fill", episode=windows_entered - 1,
                   event_type="FLATTEN" if ep_reason == "EOD_FLATTEN" else "EXIT",
                   bar_index=exit_bar_index, open_time_ms=exit_ms, price=xpx, qty=qty,
                   dca_level=kk, trigger_price=ep_trigger, ref_price=ep_ref,
                   fee=qty * xpx * taf, slip_ticks=slip_ticks, sign=sign)
            _trace("episode", episode=windows_entered - 1, exit_reason=ep_reason,
                   entry_bar_index=i0 + eb, entry_time_ms=entry_ms, entry_price=p0,
                   exit_bar_index=exit_bar_index, exit_time_ms=exit_ms, exit_price=xpx,
                   gross_pnl=ep_gross, fees=ep_fees, funding=ep_fund,
                   net_pnl=ep_gross - ep_fees - ep_fund,
                   holding_bars=exit_bar_index - (i0 + eb), layers_used=kk,
                   mae_usdt=ep_mae, mfe_usdt=ep_mfe, sign=sign)
        funding_paid += ep_fund
        realized -= ep_fund
        last_exit_bar = exit_bar
        day_equity[day_local[exit_bar]] = START_EQUITY + realized
        if diag:
            ep_net = ep_gross - ep_fees - ep_fund
            year = time.strftime("%Y", time.gmtime(cohort.open_time_ms[i0 + b] / 1000.0))
            slot = by_year.setdefault(year, {"episodes": 0, "net_pnl": 0.0, "fees": 0.0,
                                             "funding": 0.0})
            slot["episodes"] += 1
            slot["net_pnl"] += ep_net
            slot["fees"] += ep_fees
            slot["funding"] += ep_fund
            ep_stats["episodes"] += 1
            ep_stats["net_pnl"] += ep_net
            ep_stats["hold_bars"] += (exit_bar - eb + 1)
            if ep_net > 0.0:
                ep_stats["wins"] += 1
    episodes_total = windows_entered
    # only GRID cells feed the full-window ladder histogram: the winner and singleton-leg
    # diagnostics re-run cells the grid already counted, so they must not inflate the level counts
    # (card t_50c28da5: the histogram over-counted level 0 by exactly the diagnostic episodes).
    if kind in FULL_WINDOW_GRID_KINDS and count_layers:
        acc = LAYER_TOTALS.setdefault("full", [0] * 12)
        for kk in range(12):
            acc[kk] += layers[kk]

    series_flat = _flat_series(day_equity)
    if TRACE is not None:
        _trace_equity(kind, cohort, i0, day_local, series_flat, day_equity)
    diag_out = {}
    if diag:
        diag_out = {"pnl_by_year": {k: _round_dict(v) for k, v in sorted(by_year.items())},
                    "episode_stats": _round_dict({
                        "episodes": ep_stats["episodes"], "wins": ep_stats["wins"],
                        "win_rate": (ep_stats["wins"] / float(ep_stats["episodes"]))
                                    if ep_stats["episodes"] else 0.0,
                        "mean_hold_bars": (ep_stats["hold_bars"] / float(ep_stats["episodes"]))
                                          if ep_stats["episodes"] else 0.0,
                        "net_pnl": ep_stats["net_pnl"]}),
                    "signal_diagnostics": dict(layer.diag),
                    "signal_case": layer.case_label,
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
    """Human-readable label of one registered case, e.g. 'levy__lb22'."""
    return case_name(case)


def record(cohort, p, dca, kind, m):
    case = case_tuple(p)
    row = {"symbol": cohort.symbol, "timeframe": cohort.timeframe, "window_kind": kind,
           "window_case": case_label(case), "case_name": case_name(case)}
    row.update({f: p[f] for f in CASE_FIELDS})
    row.update({
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
           "annualized_return": m["annualized_return"]})
    return {k: row[k] for k in ROW_FIELDS}


def params_of(row):
    """One frozen grid row -> the strategy params dict the engine consumes."""
    return {f: row[f] for f in CASE_FIELDS}


def run_cohort(spec, cohort, run_log, series):
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
                                                  series)))

    sl = slices["full"]
    for sname, stress in STRESS:
        for p in grid:
            for dca in dca_grid:
                rows[sname].append(record(cohort, p, dca, sname,
                                          simulate(cohort, p, rail_for(dca), sl, stress,
                                                   stress.get("slip_ticks", slip), sname,
                                                   series)))
    # H8 cost-attrition grid: 40 bps per fill (8 x the registered 5 bps taker fee).
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
        "cost_attrition_40bps": _pick(same_cell(rows["cost_attrition_40bps"], key),
                                      WINNER_METRIC_KEYS),
    }
    out["neighbourhood"] = nb
    reasons = []
    gates = spec["gates"]
    # G2b: the registered OOS sufficiency floor.
    if oos["episodes"] < gates["min_episodes_oos"]:
        reasons.append("insufficient_trades")
    # G4: the registered OOS economic gate: net_pnl > 0 AND sharpe > 0 for the winner cell.
    if not (oos["net_pnl"] > 0.0 and oos["sharpe"] > 0.0):
        reasons.append("oos_economic")
    if not (full["net_pnl"] > 0.0):
        reasons.append("full_economic")
    failing_stress = [s for s, _ in STRESS if not (stress[s]["net_pnl"] > 0.0)]
    if failing_stress:
        reasons.append("robustness_economic:" + ",".join(failing_stress))
    # The 40 bps cost-attrition grid is deliberately NOT a cohort cull here: the card's
    # registered survivor requirements are the four execution stress grids, and attrition is
    # registered as a FAMILY-level reader (cost-boundary, never PASS-bearing) instead.
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

# ---------------- registered family-level readers (never cull a cohort; never PASS-bearing) ---
# Every reader below is registered in the immutable round-spec BEFORE the first run and mirrors
# one item of the record's own falsification battery.  A reader never culls a cohort by itself:
# a hit can only move a PASS to DEFERRED, and the evidence decides the final verdict
# (contract 6.4 / 7.3).  Every reader reports its per-cohort landing, the measured local
# analogue and an explicit disclosure where the record's absolute threshold does not port.

def _cell(rows_by_cohort, cohort, kind, fields, dca):
    """One registered case x DCA cell of one cohort on one phase grid (None when absent)."""
    want = (tuple(fields),) + tuple(dca[a] for a in DCA_AXES)
    for r in rows_by_cohort[cohort][kind]:
        if cell_key(r) == want:
            return r
    return None


def _winner_case(c, spec):
    """The winner's registered joint cell: strategy case fields + the four DCA axes + the
    registered project constant base_quote (the rail cannot be built without it)."""
    w = c["winner"]
    dca = {a: w[a] for a in DCA_AXES}
    dca["base_quote"] = spec["dca_domain"]["base_quote"]
    return tuple(int(w[f]) for f in CASE_FIELDS), dca


def _rollout_return(series, window):
    """Total return of a level series over a slice (None when the slice has no defined pair)."""
    out = []
    for k in range(window[0], window[1]):
        v = series[k] if k < len(series) else np.nan
        out.append(v)
    arr = np.asarray(out, dtype=np.float64)
    fin = np.isfinite(arr)
    if int(fin.sum()) < 3:
        return None
    return float(arr[fin][-1] / arr[fin][0] - 1.0)


def static_benchmarks(bundle, grid_ms, window):
    """The registered local static benchmarks of the record's own comparison table.

    G = the equal-weighted local growth basket index (ETHUSDT + BNBUSDT + SOLUSDT spot);
    D = the defensive leg (0.5 x BTC + 0.5 x USDT cash); the 50/50 mix is the record's static
    benchmark and 100 % growth is its strongest static comparator.  The 50/50 mix is read as a
    daily-rebalanced equal-weight allocation (disclosed), and every figure here is GROSS of
    trading costs, which makes the cost-boundary comparison strictly conservative against the
    strategy (the winner is measured net of fees, funding and slippage).
    """
    g = bundle["g"]
    d = bundle["d"]
    idx = bundle["index"]
    gs, ds = [], []
    for k in range(window[0], window[1]):
        j = idx.get(_day_str(grid_ms[k]))
        if j is None:
            gs.append(np.nan)
            ds.append(np.nan)
        else:
            gs.append(g[j])
            ds.append(d[j])
    gs = np.asarray(gs, dtype=np.float64)
    ds = np.asarray(ds, dtype=np.float64)

    def stats(levels):
        fin = np.isfinite(levels)
        if int(fin.sum()) < 3:
            return {"days": 0, "cagr": None, "max_dd_pct": None, "sharpe": None}
        v = levels[fin]
        rets = v[1:] / v[:-1] - 1.0
        eq = np.cumprod(1.0 + rets)
        days = int(rets.size + 1)
        years = max(days / 365.25, 1.0 / 365.25)
        peak = np.maximum.accumulate(eq)
        dd = float((eq / peak - 1.0).min())
        sd = float(np.std(rets, ddof=1))
        return {"days": days, "years": round(years, 6),
                "total_return": round(float(eq[-1] - 1.0), 6),
                "cagr": (round(float(eq[-1] ** (1.0 / years) - 1.0), 6) if eq[-1] > 0 else None),
                "max_dd_pct": round(dd, 6),
                "sharpe": (round(float(np.mean(rets) / sd * np.sqrt(365.0)), 6) if sd > 0 else 0.0)}

    mix = 0.5 * (gs / gs[np.isfinite(gs)][0]) + 0.5 * (ds / ds[np.isfinite(ds)][0])
    mix[~np.isfinite(gs) | ~np.isfinite(ds)] = np.nan
    return {"growth_100": stats(gs), "defensive_100": stats(ds), "mix_50_50": stats(mix),
            "aggregation": "levels indexed at the first defined slice observation; the 50/50 mix "
                           "is re-balanced to equal weights every bar (disclosed)",
            "cost_basis": "gross of trading costs and funding (the strategy side is net)"}


def _synthetic_walk(values, mode, rng):
    """One drift-matched synthetic random walk of a level series (registered placebo input).

    The walk reproduces the series' own defined support and its measured per-step drift and
    scale: `log` mode draws N(mean(d log x), sd(d log x)) on the log level (rates, prices),
    `arith` mode draws N(mean(d x), sd(d x)) on the level itself (the annualized funding rate,
    which is signed and can be negative).  One registered draw per series (seeded), never a
    fitted model and never a re-draw after the result is seen.
    """
    x = np.asarray(values, dtype=np.float64)
    fin = np.isfinite(x)
    pos = np.flatnonzero(fin)
    out = np.full(x.size, np.nan, dtype=np.float64)
    if pos.size < 3:
        return out
    v = x[pos]
    steps = np.zeros(x.size, dtype=np.float64)
    if mode == "log":
        d = np.diff(np.log(np.maximum(v, 1e-12)))
        steps[pos[0] + 1:] = rng.normal(float(np.mean(d)), float(np.std(d, ddof=1)), size=x.size - pos[0] - 1)
        out[pos[0]] = v[0]
        cur = v[0]
        for i in range(pos[0] + 1, x.size):
            cur = cur * math.exp(steps[i])
            out[i] = cur
    else:
        d = np.diff(v)
        steps[pos[0] + 1:] = rng.normal(float(np.mean(d)), float(np.std(d, ddof=1)), size=x.size - pos[0] - 1)
        out[pos[0]] = v[0]
        cur = v[0]
        for i in range(pos[0] + 1, x.size):
            cur = cur + steps[i]
            out[i] = cur
    return out


def _synthetic_bundle(bundle):
    """The placebo bundle: the record's three replaced inputs walk randomly; the baskets stay.

    The record replaces "actual TNX, VIX and SPYDrawdown signals with drift-matched synthetic
    random walks"; here that is the funding series (rate relief), the DVOL index (both
    volatility slots) and the benchmark spot path used for the drawdown slot.  The growth /
    defensive baskets keep their measured values, so only the three named signal slots move.
    """
    rng = np.random.default_rng(PLACEBO_SEED)
    out = dict(bundle)
    out["fund"] = _synthetic_walk(bundle["fund"], "arith", rng)
    out["dvol"] = _synthetic_walk(bundle["dvol"], "log", rng)
    out["btc"] = _synthetic_walk(bundle["btc"], "log", rng)
    return out


def _align_weight(bundle, grid_ms, values):
    idx = bundle["index"]
    out = np.full(len(grid_ms), np.nan, dtype=np.float64)
    for k in range(len(grid_ms)):
        j = idx.get(_day_str(grid_ms[k]))
        if j is not None and j < len(values):
            out[k] = values[j]
    return out


def _winner_full_cell(c, rows_by_cohort, spec):
    fields, dca = _winner_case(c, spec)
    return _cell(rows_by_cohort, c["cohort"], "full", fields, dca)


def _reader_shell(record_item, definition):
    return {"registered_item": record_item, "evaluated": False, "hit": False,
            "status": "executed", "definition": definition,
            "landing": "family-level: a hit must NEVER be recorded as PASS",
            "aggregation": "per-cohort landing, majority rule across the evaluated cohorts",
            "cohorts": {}}


def stationary_random_placebo(spec, cohort_results, diag_inputs, rows_by_cohort):
    """Reader 1 - record falsification item 1 ("Stationary Random Placebo Test").

    Procedure (record): "Replace actual TNX, VIX, and SPYDrawdown signals with drift-matched
    synthetic random walks.  Falsification Rule: If the resulting synthetic score generates a
    Sharpe ratio >= 0.98, the empirical outperformance is an artifact of curve fitting on the
    expanding Z-score."

    Definition (EXECUTED): the three registered proxy slots are replaced by one seeded,
    drift-matched synthetic walk each (registered construction, see `_synthetic_walk`); the
    composite score is rebuilt through the SAME registered chain, mapped to the same long-only
    target state, and the elected winner's DCA cell is re-simulated on the same rail, costs,
    funding policy and window.  The record's absolute 0.98 is a value of its own Sharpe
    construct and is NOT portable, so the registered local analogue is the RELATIVE comparison:
    a cohort triggers when the synthetic score's Sharpe is NOT strictly below the winner's own
    Sharpe.  Hit when that holds in a majority of the evaluated cohorts.
    """
    out = _reader_shell("stationary-random-placebo",
                        "the three registered macro proxy slots replaced by seeded drift-matched "
                        "synthetic walks; the winner's cell re-simulated through the same rail")
    out["seed"] = PLACEBO_SEED
    out["record_threshold"] = 0.98
    out["threshold_disclosure"] = ("the record's 0.98 is stated in its own Sharpe construct; the "
                                   "local rail Sharpe is a different estimator, so the local "
                                   "analogue is the relative comparison and the absolute number "
                                   "is not used")
    bundle = signal_bundle()
    synth = _synthetic_bundle(bundle)
    feats = _cmt_features(synth)
    hits = total = 0
    for c in cohort_results:
        if c["winner"] is None:
            continue
        cohort, series = diag_inputs[c["cohort"]]
        fields, dca = _winner_case(c, spec)
        case = tuple(int(v) for v in fields)
        variant = SCORE_VARIANTS[case_variant(case)]
        smoothing = SMOOTHINGS[case_smoothing(case)]
        path = score_path(feats, variant, smoothing)
        labels = sorted(cohort_results_labels(cohort_results))
        grid_ms = np.asarray(cohort.open_time_ms, dtype=np.int64)
        weight = _align_weight(synth, grid_ms, path["weight"])
        score = _align_weight(synth, grid_ms, path["score"])
        layer = SyntheticLayer(cohort, labels, weight, score,
                               "placebo__" + case_name(case), "stationary random placebo")
        base = _winner_full_cell(c, rows_by_cohort, spec)
        if base is None:
            continue
        m = simulate(cohort, params_of(c["winner"]), rail_for(dca),
                     cohort.slice(spec["data"]["start"], spec["data"]["end"]), {},
                     spec["costs"]["baseline_slippage_ticks"], "reader_placebo", series,
                     layer=layer, count_layers=False)
        total += 1
        hit_i = float(m["sharpe"]) >= float(base["sharpe"])
        hits += 1 if hit_i else 0
        out["cohorts"][c["cohort"]] = {
            "case_name": case_name(case),
            "winner_sharpe": round(float(base["sharpe"]), 6),
            "synthetic_sharpe": round(float(m["sharpe"]), 6),
            "winner_net_pnl": round(float(base["net_pnl"]), 6),
            "synthetic_net_pnl": round(float(m["net_pnl"]), 6),
            "synthetic_episodes": int(m["episodes"]),
            "synthetic_hit": bool(hit_i)}
        out["evaluated"] = True
    out["cohorts_evaluated"] = total
    out["cohorts_hit"] = hits
    out["hit"] = bool(total and hits * 2 >= total)
    return out


def cohort_results_labels(cohort_results):
    """The registered MARKET labels of the cohort results (the panel's own market axis).

    Cohort keys are "<symbol>/<timeframe>" labels (the diag_inputs key), while the signal layer's
    market axis - and every consumer of these labels, e.g. SyntheticLayer - indexes by the bare
    market symbol.  Returning the raw keys raised ValueError("'BNBUSDT' is not in list") the
    moment a placebo/perturbation reader built its synthetic layer, before any reader reading was
    recorded; the panel's own axis (Panel.__init__ -> sorted(cohorts)) is the bare symbol.
    """
    return sorted(c["cohort"].split("/", 1)[0] for c in cohort_results)


def cost_stress_boundary(spec, cohort_results, diag_inputs, rows_by_cohort):
    """Reader 2 - record falsification item 2 ("Cost Stress Boundary Test").

    Procedure (record): "Increase assumed transaction fees from 10 bps to 25 bps and 40 bps.  If
    net CAGR drops below 17.12% (the static 50/50 return), the strategy's trading velocity is
    unviable under institutional trading friction."

    Definition (EXECUTED): the winner cell's net CAGR on the registered `cost_attrition_40bps`
    grid (8 x the 5 bps taker fee = 40 bps per fill, i.e. the record's upper stress leg) is
    compared with the registered local static 50/50 G/D benchmark's CAGR on the same window,
    read from the same read-only bundle.  The record's absolute 17.12 % is its own market's
    benchmark CAGR and is therefore not portable; the local boundary is the local benchmark.
    Because the benchmark is gross of costs while the strategy is net of fees, funding and
    slippage, the local reading is strictly conservative against the strategy.  A cohort
    triggers when its stressed net CAGR does not strictly exceed the local static benchmark.
    """
    out = _reader_shell("cost-stress-boundary",
                        "the winner's net CAGR under the 40 bps cost-attrition grid against the "
                        "local static 50/50 growth/defensive benchmark CAGR")
    out["record_threshold_cagr"] = 0.1712
    out["stress_grid"] = "cost_attrition_40bps"
    out["threshold_disclosure"] = ("the record's 17.12 % is its own market's static 50/50 CAGR; "
                                   "the local boundary is the local mix's CAGR and the absolute "
                                   "source number is carried only as provenance")
    bundle = signal_bundle()
    hits = total = 0
    for c in cohort_results:
        if c["winner"] is None:
            continue
        cohort, _series = diag_inputs[c["cohort"]]
        fields, dca = _winner_case(c, spec)
        row = _cell(rows_by_cohort, c["cohort"], "cost_attrition_40bps", fields, dca)
        base = _winner_full_cell(c, rows_by_cohort, spec)
        if row is None or base is None:
            continue
        bench = static_benchmarks(bundle, np.asarray(cohort.open_time_ms, dtype=np.int64),
                                  cohort.slice(spec["data"]["start"], spec["data"]["end"]))
        mix_cagr = bench["mix_50_50"]["cagr"]
        total += 1
        stressed = row["cagr"]
        hit_i = (stressed is None) or (mix_cagr is not None and float(stressed) <= float(mix_cagr))
        hits += 1 if hit_i else 0
        out["cohorts"][c["cohort"]] = {
            "winner_net_cagr_full": base["cagr"], "stressed_net_cagr_40bps": stressed,
            "stressed_net_pnl_40bps": row["net_pnl"], "stressed_fees_40bps": row["fees"],
            "static_mix_50_50_cagr": mix_cagr, "static_growth_100_cagr": bench["growth_100"]["cagr"],
            "static_mix_days": bench["mix_50_50"]["days"], "hit": bool(hit_i)}
        out["evaluated"] = True
    out["cohorts_evaluated"] = total
    out["cohorts_hit"] = hits
    out["hit"] = bool(total and hits * 2 >= total)
    return out


def parameter_perturbation_grid(spec, cohort_results, diag_inputs, rows_by_cohort):
    """Reader 3 - record falsification item 3 ("Parameter Perturbation Grid").

    Procedure (record): "Perturb parameters alpha in [0.3, 0.7], lambda_s in [0.2, 0.6],
    tau_w in [0.5, 1.2], and eta in [0.02, 0.08].  A drop in Sharpe of greater than 0.20
    indicates fragile parameter tuning."

    Definition (EXECUTED): the registered perturbation set is the eight single-parameter moves
    to the range ENDPOINTS (alpha 0.3 / 0.7, lambda_s 0.2 / 0.6, tau_w 0.5 / 1.2, eta 0.02 /
    0.08), each evaluated alone on the winner's cell with the other parameters at the record's
    calibrated values, through the same rail, costs and window.  The eta perturbations are not
    applicable to a winner whose smoothing arm fixes eta = 1.0 (recorded, never silently
    skipped).  The record's 0.20 Sharpe bound is a source-scale value: it is applied to the
    rail's Sharpe of the winner cell and disclosed as such (the rail Sharpe is a different
    estimator from the record's portfolio Sharpe).  A cohort triggers when the largest drop
    across the applicable perturbations exceeds the bound.
    """
    out = _reader_shell("parameter-perturbation-grid",
                        "eight registered single-parameter perturbations evaluated on the "
                        "winner's cell (full window, same rail and costs)")
    out["record_threshold_sharpe_drop"] = SHARPE_DROP_LIMIT
    out["registers"] = {"alpha": list(PERTURB_ALPHA), "lambda_s": list(PERTURB_LAMBDA_S),
                        "tau_w": list(PERTURB_TAU_W), "eta": list(PERTURB_ETA)}
    out["threshold_disclosure"] = ("the record's 0.20 is stated in its own Sharpe construct; the "
                                   "local analogue applies it to the rail Sharpe of the winner "
                                   "cell, which is disclosed as not portable")
    bundle = signal_bundle()
    feats = _cmt_features(bundle)
    hits = total = 0
    for c in cohort_results:
        if c["winner"] is None:
            continue
        cohort, series = diag_inputs[c["cohort"]]
        fields, dca = _winner_case(c, spec)
        case = tuple(int(v) for v in fields)
        variant = SCORE_VARIANTS[case_variant(case)]
        smoothing = SMOOTHINGS[case_smoothing(case)]
        base = _winner_full_cell(c, rows_by_cohort, spec)
        if base is None:
            continue
        labels = sorted(cohort_results_labels(cohort_results))
        grid_ms = np.asarray(cohort.open_time_ms, dtype=np.int64)
        window = cohort.slice(spec["data"]["start"], spec["data"]["end"])
        moves = [("alpha", v) for v in PERTURB_ALPHA] + \
                [("lambda_s", v) for v in PERTURB_LAMBDA_S] + \
                [("tau_w", v) for v in PERTURB_TAU_W] + \
                [("eta", v) for v in PERTURB_ETA]
        rows = {}
        drops = []
        for name, value in moves:
            if name == "eta" and smoothing == "none":
                rows["%s=%s" % (name, value)] = {"applicable": False,
                                                 "reason": "the winner's smoothing arm fixes eta = 1.0"}
                continue
            kwargs = {"alpha": ALPHA_RATE, "lambda_s": LAMBDA_S, "lambda_c": LAMBDA_C,
                      "tau_w": TAU_W, "eta": ETA_SMOOTH}
            kwargs[name] = value
            path = score_path(feats, variant, smoothing, **kwargs)
            weight = _align_weight(bundle, grid_ms, path["weight"])
            score = _align_weight(bundle, grid_ms, path["score"])
            layer = SyntheticLayer(cohort, labels, weight, score,
                                   "perturb__%s_%s" % (name, value), "registered perturbation")
            m = simulate(cohort, params_of(c["winner"]), rail_for(dca), window, {},
                         spec["costs"]["baseline_slippage_ticks"], "reader_perturbation", series,
                         layer=layer, count_layers=False)
            drop = float(base["sharpe"]) - float(m["sharpe"])
            drops.append(drop)
            rows["%s=%s" % (name, value)] = {
                "applicable": True, "sharpe": round(float(m["sharpe"]), 6),
                "net_pnl": round(float(m["net_pnl"]), 6), "episodes": int(m["episodes"]),
                "sharpe_drop": round(drop, 6), "exceeds_bound": bool(drop > SHARPE_DROP_LIMIT)}
        worst = max(drops) if drops else None
        hit_i = bool(worst is not None and worst > SHARPE_DROP_LIMIT)
        total += 1
        hits += 1 if hit_i else 0
        out["cohorts"][c["cohort"]] = {"case_name": case_name(case),
                                       "smoothing_arm": smoothing,
                                       "winner_sharpe": round(float(base["sharpe"]), 6),
                                       "perturbations": rows,
                                       "applicable_perturbations": len(drops),
                                       "worst_drop": (None if worst is None else round(worst, 6)),
                                       "hit": hit_i}
        out["evaluated"] = True
    out["cohorts_evaluated"] = total
    out["cohorts_hit"] = hits
    out["hit"] = bool(total and hits * 2 >= total)
    return out


def subperiod_rate_hike_drawdown(spec, cohort_results, diag_inputs, rows_by_cohort):
    """Reader 4 - record falsification item 4 ("Subperiod Breakdown").

    Procedure (record): "Evaluate across the 2022 rate hike regime exclusively.  The strategy
    must maintain a maximum drawdown strictly shallower than -25% (versus -33.9% for 100 % G).
    Failure to protect drawdown during rising yield regimes disconfirms the discount-rate timing
    thesis."

    Definition (EXECUTED): the registered local analogue evaluates the winner cell on the
    HISTORICAL slice (the slice that contains the 2022 rate-hike regime; the local signal is
    undefined before the measured warm-up, which is reported) and compares its maximum drawdown
    with the maximum drawdown of the local 100 % growth basket over the same slice, read from
    the same bundle.  The record's absolute -25 % / -33.9 % are that market's numbers and are
    carried only as provenance; the local boundary is the growth basket's own drawdown.  A
    cohort triggers when its winner's drawdown is NOT strictly shallower than the growth
    basket's.
    """
    out = _reader_shell("subperiod-rate-hike-drawdown",
                        "the winner's historical-slice maximum drawdown against the local 100 % "
                        "growth basket's maximum drawdown on the same slice")
    out["record_thresholds"] = {"strategy_max_dd_pct": -0.25, "growth_100_max_dd_pct": -0.339}
    out["threshold_disclosure"] = ("the record's -25 % / -33.9 % are its own market's numbers; "
                                   "the local boundary is the local growth basket's drawdown")
    bundle = signal_bundle()
    hits = total = 0
    for c in cohort_results:
        if c["winner"] is None:
            continue
        cohort, _series = diag_inputs[c["cohort"]]
        fields, dca = _winner_case(c, spec)
        row = _cell(rows_by_cohort, c["cohort"], "historical", fields, dca)
        if row is None:
            continue
        window = cohort.slice(spec["data"]["historical_start"], spec["data"]["historical_end"])
        bench = static_benchmarks(bundle, np.asarray(cohort.open_time_ms, dtype=np.int64), window)
        growth_dd = bench["growth_100"]["max_dd_pct"]
        total += 1
        win_dd = row["max_dd_pct"]
        hit_i = bool(growth_dd is None or win_dd is None or float(win_dd) <= float(growth_dd))
        hits += 1 if hit_i else 0
        out["cohorts"][c["cohort"]] = {
            "winner_historical_max_dd_pct": win_dd,
            "winner_historical_net_pnl": row["net_pnl"],
            "winner_historical_episodes": row["episodes"],
            "growth_100_max_dd_pct": growth_dd,
            "growth_100_cagr": bench["growth_100"]["cagr"],
            "hit": hit_i}
        out["evaluated"] = True
    out["cohorts_evaluated"] = total
    out["cohorts_hit"] = hits
    out["hit"] = bool(total and hits * 2 >= total)
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
    for r in full:
        strategy_cells.add(case_tuple(r))
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
    cohort_results = ([evaluate_cohort(spec, label, per_cohort[label])
                       for label in cohort_labels] if coverage_complete else [])
    survivors = [c for c in cohort_results if c["outcome"] == "SURVIVOR"]
    disposition = family_disposition(survivors, coverage_complete)

    # deterministic selector: a shuffled copy of the same rows must elect the same cell
    rng = random.Random(20260918)
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

    # ---- per-cohort winner diagnostics (per-year, per-episode stats, registered counters)
    diag_by_cohort = {}
    robustness_diagnostics = {
        "non_gating": True,
        "note": "per-calendar-year net PnL, per-episode statistics and the registered signal "
                "diagnostics of the elected winner cell, plus the structural counters of its "
                "FULL-window run (isolated from the grid counters); descriptive only - the four "
                "family-level readers are the only gated evidence",
        "registered_item": "sub_period_stability_and_winner_signal_diagnostics"}
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
            before = counters_snapshot()
            m_win = simulate(cohort, p_win, rail_for(dca), full_slice, {}, slip0,
                             "winner_diag", series, diag=True, count_layers=False)
            win_delta = counters_delta(before)
            row = record(cohort, p_win, dca, "full", m_win)
            grid_row = same_cell(per_cohort[c["cohort"]]["full"],
                                 tuple([case_tuple(c["winner"])] + [c["winner"][a] for a in DCA_AXES]))
            diag_by_cohort[c["cohort"]] = {"winner_full": m_win, "row": row,
                                           "counter_delta": win_delta,
                                           "episode_stats": m_win["diagnostics"].get(
                                               "episode_stats", {})}
            robustness_diagnostics.setdefault("cohorts", {})[c["cohort"]] = {
                "winner_cell": c["winner"],
                "pnl_by_year": m_win["diagnostics"]["pnl_by_year"],
                "winner_episode_stats": m_win["diagnostics"]["episode_stats"],
                "winner_signal_diagnostics": m_win["diagnostics"]["signal_diagnostics"],
                "winner_full_window_counters": win_delta,
                "winner_diagnostics_match_full_row": all(
                    abs(float(row[k]) - float(grid_row[k])) < 1e-6
                    for k in ROW_FIELDS
                    if isinstance(grid_row[k], (int, float)) and not isinstance(grid_row[k], bool)
                    and isinstance(row[k], (int, float))),
                "daily_series_days": m_win["diagnostics"]["daily_equity_days"]}

    rows_by_cohort = {label: per_cohort[label] for label in cohort_labels}
    empty_reader = {"evaluated": False, "hit": False,
                    "landing": "family-level: a hit must NEVER be recorded as PASS"}
    stationary_placebo = (stationary_random_placebo(spec, cohort_results, diag_inputs,
                                                   rows_by_cohort)
                          if coverage_complete
                          else dict(empty_reader, registered_item="stationary-random-placebo"))
    cost_boundary = (cost_stress_boundary(spec, cohort_results, diag_inputs, rows_by_cohort)
                     if coverage_complete
                     else dict(empty_reader, registered_item="cost-stress-boundary"))
    perturbation = (parameter_perturbation_grid(spec, cohort_results, diag_inputs, rows_by_cohort)
                    if coverage_complete
                    else dict(empty_reader, registered_item="parameter-perturbation-grid"))
    subperiod = (subperiod_rate_hike_drawdown(spec, cohort_results, diag_inputs, rows_by_cohort)
                 if coverage_complete
                 else dict(empty_reader, registered_item="subperiod-rate-hike-drawdown"))
    flags = {"stationary_random_placebo": bool(stationary_placebo.get("hit")),
             "cost_stress_boundary": bool(cost_boundary.get("hit")),
             "parameter_perturbation_grid": bool(perturbation.get("hit")),
             "subperiod_rate_hike_drawdown": bool(subperiod.get("hit"))}

    # registered landing of the four family-level readers: none of them may be recorded as
    # PASS; the band mapping itself is unchanged (v1.4.0).
    final_verdict = disposition["verdict_recommendation"]
    final_claimable = disposition["performance_claimable_recommendation"]
    if final_verdict == "PASS" and any(flags.values()):
        final_verdict = "DEFERRED"
        final_claimable = False

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
    for extra in ("cost_attrition_40bps", "no_funding", "no_funding_full"):
        stress_summary[extra] = {"median_net_pnl": med(grid_rows[extra], "net_pnl"),
                                 "median_sharpe": med(grid_rows[extra], "sharpe"),
                                 "cases": len(grid_rows[extra]), "non_gating": True}

    keys = ("net_pnl", "fees", "funding", "gross_pnl", "ending_equity", "sharpe",
            "max_dd_pct", "max_dd_usdt", "max_effective_leverage", "capital_utilization",
            "cagr", "total_return_pct", "episodes", "fills", "turnover_usdt")
    mid = lambda rows, key: med(rows, key)
    fee_2x_effective = (mid(grid_rows["fee_2x"], "fees") > mid(full, "fees")
                        and mid(grid_rows["fee_2x"], "net_pnl") < mid(full, "net_pnl"))
    funding_2x_effective = (abs(mid(grid_rows["funding_2x"], "funding"))
                            > abs(mid(full, "funding"))
                            and mid(grid_rows["funding_2x"], "net_pnl") != mid(full, "net_pnl"))
    cost_attrition_effective = (mid(grid_rows["cost_attrition_40bps"], "fees") > mid(full, "fees")
                                and mid(grid_rows["cost_attrition_40bps"], "net_pnl")
                                < mid(full, "net_pnl"))
    entry_delay_effective = (mid(grid_rows["entry_delay_1_bar"], "net_pnl") != mid(full, "net_pnl")
                             or mid(grid_rows["entry_delay_1_bar"], "fills") != mid(full, "fills")
                             or mid(grid_rows["entry_delay_1_bar"], "episodes")
                             != mid(full, "episodes"))
    slippage_effective = (mid(grid_rows["slippage_2ticks"], "net_pnl") != mid(full, "net_pnl")
                          or mid(grid_rows["slippage_2ticks"], "fees") != mid(full, "fees"))
    no_funding_free = all(r["funding"] == 0.0 for r in grid_rows["no_funding"])
    no_funding_track_free = (no_funding_free
                             and all(r["funding"] == 0.0 for r in grid_rows["no_funding_full"]))
    full_events = sum(r["windows_seen"] for k in FULL_WINDOW_GRID_KINDS
                      for r in grid_rows.get(k, []))
    full_episodes = sum(r["episodes"] for k in FULL_WINDOW_GRID_KINDS
                        for r in grid_rows.get(k, []))
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
        "layer0_equals_episodes": (layers[0] == full_episodes and layers[0] > 0),
        "layer_histogram_nonempty": sum(layers) > 0,
        "no_entry_after_exhaustion": all(r["min_entry_equity"] > 0.0 for r in full),
        "ending_equity_floor": all(r["ending_equity"] > -1.5 * START_EQUITY for r in full),
        "selector_deterministic": selector_stable,
        "selector_historical_only": True,
        "episodes_never_exceed_windows_seen": all(r["episodes"] <= r["windows_seen"]
                                                  for r in full),
        # executable no-look-ahead / window guards (per-grid module counters)
        "bar_grid_is_contiguous": counters_total("bar_grid_not_contiguous") == 0,
        "no_unregistered_strategy_case": counters_total("case_not_registered") == 0,
        "funding_bar_never_out_of_hold": counters_total("funding_bar_out_of_hold") == 0,
        "no_funding_grid_is_cost_free": no_funding_free,
        "no_funding_track_is_cost_free": no_funding_track_free,
        "fee_2x_track_is_not_a_noop": fee_2x_effective,
        "funding_2x_track_is_not_a_noop": funding_2x_effective,
        "cost_attrition_track_is_not_a_noop": cost_attrition_effective,
        "entry_delay_track_is_not_a_noop": entry_delay_effective,
        "slippage_track_is_not_a_noop": slippage_effective,
        # CMT registered guards (names frozen in the round-spec)
        "entry_bar_is_the_next_bar_after_the_signal":
            counters_total("entry_bar_not_the_next_bar_after_the_signal") == 0,
        "no_entry_without_a_defined_signal": counters_total("entry_before_a_defined_signal") == 0,
        "no_signal_before_the_registered_warmup":
            counters_total("signal_before_the_registered_warmup") == 0,
        "target_state_follows_the_registered_materiality_rule":
            counters_total("state_not_derived_from_the_registered_rule") == 0,
        "no_signal_input_alignment_gap": counters_total("signal_input_alignment_gap") == 0,
        "panel_series_share_one_bar_grid": counters_total("panel_grid_mismatch") == 0,
        "signal_inputs_are_prefix_invariant": counters_total("causality_probe_mismatch") == 0,
        "no_reentry_without_a_new_position_event": (
            counters_sum("entry_bar_not_the_next_bar_after_the_signal", FULL_WINDOW_GRID_KINDS) == 0
            and counters_sum("reentry_skipped_in_position", FULL_WINDOW_GRID_KINDS)
            + full_episodes + counters_sum("entry_refused_exhausted", FULL_WINDOW_GRID_KINDS)
            == full_events),
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
                         "score_variants": list(SCORE_VARIANTS),
                         "smoothings": list(SMOOTHINGS),
                         "signal_constants": {
                             "tau_softplus": TAU_SOFTPLUS, "alpha_rate": ALPHA_RATE,
                             "lambda_s": LAMBDA_S, "lambda_c": LAMBDA_C,
                             "max_tilt": MAX_TILT, "tau_w": TAU_W, "eta_smooth": ETA_SMOOTH,
                             "delta_window": DELTA_WINDOW,
                             "dvol_percentile_window": DVOL_PCT_WINDOW,
                             "momentum_window": MOMENTUM_WINDOW,
                             "z_min_obs": Z_MIN_OBS, "w_g_init": W_G_INIT,
                             "eta_none": ETA_NONE, "materiality_weight": MATERIALITY_W,
                             "probe_case": case_name(PROBE_CASE),
                             "panel_size": PANEL_SIZE,
                             "growth_basket": list(GROWTH_BASKET),
                             "defensive_leg": DEFENSIVE_ASSET,
                             "defensive_cash_share": DEFENSIVE_CASH,
                             "funding_proxy": FUNDING_SYMBOL, "dvol_proxy": DVOL_ASSET,
                             "proxy_mapping": "operator-corrected contract section 6 mapping "
                                              "(record 'Crypto portability')"}},
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
        "stationary_random_placebo": stationary_placebo,
        "cost_stress_boundary": cost_boundary,
        "parameter_perturbation_grid": perturbation,
        "subperiod_rate_hike_drawdown": subperiod,
        "signal_input_layer": ({label: diag_inputs[label][0].inputs_report
                                for label in cohort_labels} if coverage_complete else {}),
        "signal_layer": ({label: signal_report_of(diag_inputs[label][0])
                          for label in cohort_labels} if coverage_complete else {}),
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
        sys.stderr.write("usage: 140_continuous_macro_timing_run.py <run-spec.json>\n")
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
                    "disposition_version", "gates", "costs", "falsification"):
            if key not in spec:
                raise SystemExit("run-spec is not a Strategy CMT spec: missing %r (contract 7.2/7.3)"
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
                cohort = Cohort(symbol, tf, spec["data"]["start"], spec["data"]["end"], spec)
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
                cohort.inputs_report = {
                    "bars": cohort.n, "columns": len(cohort.inputs["names"]),
                    "warmup_bars": None, "warmup_measured_after_the_panel": True,
                    "causality_probe": None,
                    "note": "the registered market-level macro signal bundle (the benchmark "
                            "funding series, the implied-vol index, the benchmark spot "
                            "drawdown and the growth/defensive baskets) is read on the "
                            "cohort's own trading grid; the score chain is built per case on "
                            "the shared four-instrument panel and is reported in "
                            "signal_layer.json"}
                run_log("cohort %s bars=%d funding_obs=%d modeled=%d official=%d"
                        % (label, cohort.n, len(ft), counts["modeled_funding"],
                           counts["official"]))

        # the registered panel: exactly the four cohorts above, built once per case (the
        # features are cached across cases, so the read-only input reads and the expanding
        # statistics happen once).  Building every case before the grid also fails fast on a
        # signal defect, and the registered causality probe can only run once the panel exists.
        PANEL_COHORTS.clear()
        PANEL_COHORTS.update({c.symbol: c for c in [diag_inputs[k][0] for k in sorted(diag_inputs)]})
        panel_report = {}
        t_panel = time.time()
        for case in CASE_ORDER:
            p = panel_for(case)
            # The score diagnostics live on the cached panel CORE - the same source the causality
            # probe below reads; Panel.diag carries the panel-level fields only (case_name, bars,
            # warmup_bars, defined_bars, long_bars, ...).  Reading them off p.diag raised
            # KeyError('score_diagnostics') before this fix.
            score_diag = _panel_core_cached(case)["score_diagnostics"]
            panel_report[p.case_label] = {
                "bars": p.n, "markets": list(p.markets),
                "variant": p.diag["variant"], "smoothing": p.diag["smoothing"],
                "warmup_bars": p.diag["warmup_bars"], "defined_bars": p.diag["defined_bars"],
                "score_diagnostics": score_diag}
        run_log("panels built: %d cases in %.1fs" % (len(CASE_ORDER), time.time() - t_panel))
        for label in sorted(diag_inputs):
            cohort = diag_inputs[label][0]
            probe = causality_probe(cohort, cohort.inputs)
            core = _panel_core_cached(PROBE_CASE)
            cohort.inputs_report["causality_probe"] = probe
            cohort.inputs_report["warmup_bars"] = int(core["warmup_bars"])
            cohort.inputs_report["signal_calendar"] = {
                "first_defined_bar": core["score_diagnostics"]["first_defined_date"],
                "last_defined_bar": core["score_diagnostics"]["last_defined_date"],
                "defined_bars": core["score_diagnostics"]["defined_observations"],
                "readings": "the record's 756-day DVOL percentile window plus the registered "
                            "expanding-z minimum bound the first defined bar"}
            run_log("causality probe %s bars=%d probes=%d mismatches=%d warmup=%d"
                    % (label, cohort.n, probe["bars_probed"], probe["mismatches"],
                       core["warmup_bars"]))
        for label in sorted(diag_inputs):
            for k, v in run_cohort(spec, diag_inputs[label][0], run_log,
                                   diag_inputs[label][1]).items():
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
        summary["panel_build"] = panel_report
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
                          {"stationary_random_placebo":
                               summary["stationary_random_placebo"],
                           "cost_stress_boundary": summary["cost_stress_boundary"],
                           "parameter_perturbation_grid":
                               summary["parameter_perturbation_grid"],
                           "subperiod_rate_hike_drawdown":
                               summary["subperiod_rate_hike_drawdown"],
                           "flags": summary["registered_family_level_falsification_flags"]})
        atomic_write_json(os.path.join(attempt_dir, "artifacts", "signal_input_layer.json"),
                          summary["signal_input_layer"])
        atomic_write_json(os.path.join(attempt_dir, "artifacts", "signal_layer.json"),
                          summary["signal_layer"])
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
