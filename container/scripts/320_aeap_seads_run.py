#!/usr/bin/env python3
"""Direct-family Qlib runner: AEAP/SEADS cross-sectional rank-product formulaic alpha.

Registered hypothesis, record excerpts and the frozen candidate body live in the
family round-spec; this engine implements only what they require.

Registered mechanism (source = canonical wiki record, excerpted in the candidate body):
    SEADS admits formulaic alpha signals of the form
        rank(a) x rank(b) x rank(c)
    evaluated as cross-sectional return predictors: rank percentiles are taken
    WITHIN the stock cross-section at each month-end, the composite rank-product
    score is sorted, and the factor is judged on productivity (universe coverage),
    performance (Rank-IC / Fama-MacBeth / out-of-sample Sharpe) and novelty
    (low correlation with a reference factor panel).  The three source-reported
    example factors are (1) Stable Liquidity Efficiency Gate,
    rank(bidaskhl_21d) x rank(zero_trades_126d) x rank(dolvol_var_126d /
    (1 + trail12m_mean)); (2) Distress-Amplified Performance Mispricing,
    rank(mispricing_perf) x rank(o_score); (3) Cash Operating Profit x Low Tax
    Payable Growth, rank(cop_bev) x (1 - rank(txp_gr1a)).

Local eligible universe (system-owned lifecycle rule; conclusions stay scoped to it):
    The canonical local raw is Binance USD-M perpetual, four symbols x seven
    intervals.  Example factor (1) is fully computable from canonical OHLCV
    (high/low range, zero-volume days, dollar volume = volume x close), so it is
    the registered local signal.  Example factors (2) and (3) need equity
    accounting fields (mispricing performance, Ohlson O-Score, cash operating
    profitability / book equity, tax payable growth) that the canonical catalog
    does not contain at all - clear absence, disclosed in round-spec
    prerequisite_evidence_phase_2b; they are outside the local eligible signal
    set and no claim is made for them.  This follows the record's own Crypto
    portability section, which registers the platform-agnostic AEAP method applied
    to crypto cross-sectional market-structure features as the adaptation path.

Construction implemented here (record "Signal / Lookback / Entry / Exit"):
    Formation = month-end close (last daily bar of each UTC month); the score is
    tradable at the next month's open (record's research-proposed timing).
    Components at formation t (lookback_scale s in {0.5, 1.0, 2.0} scales the
    source lookbacks 21d / 126d / 12 months, 12 months = 365 days, half-up):
      bidaskhl_w1     = mean over the trailing w1 daily bars of (high - low) /
                        ((high + low) / 2)          [high-low range spread proxy]
      zero_trades_w2  = count of daily bars with volume == 0 in trailing w2 days
      dolvol_var_w2   = population variance of daily dollar volume over w2 days
      trail_mean_w3   = mean daily dollar volume over w3 days
      score           = rank(bidaskhl) x rank(zero_trades)
                        x rank(dolvol_var / (1 + trail_mean))
    rank = cross-sectional percentile within the four-symbol cross-section,
    average ranks for ties, (avg_rank - 1) / (n - 1).  Direction: sort by
    (score desc, component-1 rank desc, symbol asc) and take long top-1, short
    bottom-1, the middle two flat - the record's research-proposed
    quintile/decile long-short translated to n = 4 (one name each tail).

Trading translation (candidate body DCA rail; USER_FIXED invariants inherited):
    tranche #1 fills at the first executable bar of the holding month (the
    month after formation) at open + one adverse tick; adverse-price scale-ins
    fire at entry_price x (1 -/+ spacing_pct x k) for k = 1..10 (11 routine
    active levels, tranche #12 stays reserve); breakeven TP and resting
    invalidation track the running average cost; the position is flattened at
    the close of the last bar of the holding month (monthly rebalance, one
    month holding period) and again at any window boundary; FLAT/kill opens
    nothing until the next qualified signal.  Same-bar ordering is the
    registered deterministic conservative ordering (open actions -> funding ->
    open-gap invalidation -> margin guard -> level-order scale-ins with
    stop-before-adds -> resting invalidation -> breakeven TP -> month exit).

Execution (research-defined where the source is silent): per-fill taker fee from
canonical instrument metadata, one tick adverse slippage on every leg of both
directions (two ticks in the slippage_2ticks grid), official funding observations
only charged at their own timestamp while the book is live (missing intervals
cost zero and coverage is disclosed; modeled rows are never charged), gross PnL
from an independent price-PnL accumulator cross-checked against net with a
negative control in the self-check (v1.3.2).  All metrics are computed on daily
equity marks that are already net-of-fee (v1.3.1).

Falsification battery: the frozen candidate body excerpts three of the record's
seven falsification items; all seven are registered verbatim from the canonical
record in round-spec.falsification_registry and evaluated in-run
(falsification.json), each with its pre-registered local operationalisation.

Execution: production compute runs only inside the qlib-run container via the
existing `container exec -d qlib-run` semantics; every registered timeframe is a
Qlib 0.9.7 dump/readback of the canonical raw and the readback is the production
signal source; raw is read through /data/raw read-only; no second engine, no host
pandas backtest, no external coding agent.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import hashlib
import itertools
import json
import math
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np

FAMILY_ID = "aeap-seads-llm-agentic-factor-discovery-formulaic-alpha-2026-09-03"
ENGINE_VERSION = "aeap_seads_rankproduct_factor_qlib_v1"
RAW_ROOT = "/data/raw"
RESULTS_ROOT = "/results"
WORK_ROOT = "/qlib/work/aeap-seads-v1"
CSV_ROOT = os.path.join(WORK_ROOT, "csv")
QLIB_DIR = os.path.join(WORK_ROOT, "qlib-data")
VENV_PYTHON = "/opt/venv/bin/python"
DUMP_BIN = "/opt/qlib-tools/dump_bin.py"
INSTRUMENTS_PATH = RAW_ROOT + "/binance/usdm/instruments/usdm-perp-instruments.json"
MS_DAY = 86_400_000
MS_HOUR = 3_600_000
FIELDS = ("open", "high", "low", "close", "volume")
SYMBOLS = ("BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT")
TIMEFRAMES = (
    {"raw_interval": "5m", "qlib_freq": "5min", "bars_per_day": 288, "bar_ms": 300_000},
    {"raw_interval": "15m", "qlib_freq": "15min", "bars_per_day": 96, "bar_ms": 900_000},
    {"raw_interval": "30m", "qlib_freq": "30min", "bars_per_day": 48, "bar_ms": 1_800_000},
    {"raw_interval": "1h", "qlib_freq": "60min", "bars_per_day": 24, "bar_ms": 3_600_000},
    {"raw_interval": "4h", "qlib_freq": "240min", "bars_per_day": 6, "bar_ms": 14_400_000},
    {"raw_interval": "1d", "qlib_freq": "day", "bars_per_day": 1, "bar_ms": 86_400_000},
    {"raw_interval": "1w", "qlib_freq": "week", "bars_per_day": 1, "bar_ms": 604_800_000},
)
BAR_MS = {tf["raw_interval"]: tf["bar_ms"] for tf in TIMEFRAMES}
SIGNAL_INTERVAL = "1d"
PHASES = {"historical": ("2022-01-01", "2025-09-30"),
          "oos": ("2025-10-01", "2026-09-11"),
          "full": ("2022-01-01", "2026-09-11")}

START_EQUITY = 30_000.0
BASE_QUOTE = 1_000.0
LEVERAGE = 10.0
MAX_ADD_LEVELS = 10          # tranche #1 + 10 scale-ins = 11 routine active levels
LADDER_LEVELS = 12           # tranche #12 stays reserve, never routinely deployed
SEED = 20260903

# --------------------------------------------------------------------------- #
# Registered strategy domain (frozen before any compute)                       #
# --------------------------------------------------------------------------- #
LOOKBACK_SCALES = (0.5, 1.0, 2.0)      # record falsification item 3: +/-50%
BASE_LOOKBACKS = {"bidaskhl_days": 21, "zero_dolvol_days": 126, "trail_mean_days": 365}
STRATEGY_AXES = {"lookback_scale": LOOKBACK_SCALES}
REFERENCE_SCALE = 1.0                  # source-specified lookbacks = pre-registered reference


def _half_up(value):
    return int(math.floor(float(value) + 0.5))


def lookback_days(scale):
    return (_half_up(BASE_LOOKBACKS["bidaskhl_days"] * scale),
            _half_up(BASE_LOOKBACKS["zero_dolvol_days"] * scale),
            _half_up(BASE_LOOKBACKS["trail_mean_days"] * scale))


CASES = tuple(dict({"case_code": i, "lookback_scale": scale,
                    "label": "lb%g" % scale},
                   **dict(zip(("bidaskhl_days", "zero_dolvol_days", "trail_mean_days"),
                              lookback_days(scale))))
              for i, scale in enumerate(LOOKBACK_SCALES))

DCA_AXES = {"spacing_pct": (0.01, 0.02, 0.03, 0.04),
            "size_multiplier": (1.0, 1.1),
            "breakeven_tp_pct": (0.01, 0.02, 0.03),
            "invalidation_pct": (0.05, 0.10)}
DCA_GRID = tuple(dict(zip(DCA_AXES, vals)) for vals in itertools.product(*DCA_AXES.values()))

GRIDS = ("historical", "oos", "full", "fee_2x", "funding_2x", "entry_delay_1_bar",
         "slippage_2ticks", "no_funding", "no_funding_full", "cost_attrition_40bps")
STRESS_GRIDS = ("fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks")

MIN_EPISODES_IS = 10
MIN_EPISODES_OOS = 3
MIN_NEIGHBOUR = 0.60
SELECTOR_VERSION = "cohort-selector-v1"
DISPOSITION_VERSION = "cohort-disposition-v1"
DISPOSITION_MAPPING_VERSION = "v1.4.0"
PIT_STATUS = "PASS_NO_LOOKAHEAD"

# Record falsification battery, all seven items registered before compute.
FALSIFICATION = [
    "Out-of-sample / walk-forward: re-run the discovery evaluation on a held-out period not "
    "used in the gate calibration; if mean OOS Rank-IC < 0.01 or mean OOS Sharpe < 0.1 across "
    "admitted factors, generalisability is weakened (record item 1)",
    "Point-in-time / leakage audit: every component entering factor construction must be "
    "available at formation time with the right lags; any look-ahead contamination invalidates "
    "the IC/FM results (record item 2)",
    "Parameter perturbation: vary the lookback windows +/-50% on 21d, 126d and 12m and "
    "re-evaluate; if Rank-IC drops below 0.01 or the sign flips under the perturbation the "
    "signal is parameter-unstable (record item 3)",
    "Reference panel expansion: re-evaluate novelty against an expanded reference panel; if "
    "max absolute correlation with any reference factor exceeds 0.8 the factor may be a "
    "repackaging of existing risk premia (record item 4)",
    "Ablation on LLM vs random search: replace the LLM hypothesis generator with a random "
    "formula sampler of similar complexity; if random search achieves comparable OOS "
    "performance the LLM-specific contribution is diminished (record item 5)",
    "Transaction cost stress: apply 5-20 bps per round-trip to the long-short portfolio; if "
    "costs consume > 50% of the factor's gross return the signal is not net-alpha-viable at "
    "the monthly frequency (record item 6)",
    "Subperiod / regime breakdown: evaluate the factor across bull (+15% YoY), bear (-15% "
    "YoY) and flat regimes; if it only works in one regime the mechanism is not regime-robust "
    "(record item 7)",
]

FALSIFICATION_REGISTRY = ("oos_walk_forward_generalization", "point_in_time_leakage_audit",
                          "lookback_parameter_perturbation", "reference_panel_novelty",
                          "llm_vs_random_search_ablation", "transaction_cost_stress",
                          "regime_subperiod_breakdown")

# Pre-registered local reference panel (record item 4 analogue of the 45-characteristic panel).
REFERENCE_PANEL = ("mom_12m", "rev_21d", "vol_21d", "dolvol_level_126d", "amihud_126d",
                   "price_range_126d")
REFERENCE_PANEL_NOTE = ("research-defined local proxy panel: the record's 45-characteristic "
                        "US-equity reference panel does not exist in the canonical local raw; "
                        "six market-based signals of the same rank-product form are pre-registered "
                        "instead and conclusions stay scoped to them")
RANDOM_ABLATION = {"formulas": 25, "seed": SEED, "components_per_formula": (2, 3),
                   "components": ("bidaskhl_21d", "zero_trades_126d", "dolvol_var_ratio_126d",
                                  "mom_12m", "rev_21d", "vol_21d", "dolvol_level_126d",
                                  "amihud_126d"),
                   "rule": "falsified when the reference case's mean OOS Rank-IC is at or below "
                           "the median mean OOS Rank-IC of the random formulas (comparable = "
                           "not better than a typical random formula)"}
COST_STRESS_BPS = (5, 10, 20)
COST_STRESS_TURNOVER = 1.0     # one round trip of notional per monthly rebalance, both legs
COST_STRESS_CONSUMPTION = 0.50
RANK_IC_FLOOR = 0.01
SHARPE_FLOOR = 0.1
NOVELTY_MAX_CORR = 0.8
REGIME_YOY = 0.15
REGIME_MIN_OBS = 3

ARTIFACTS = ("state.json", "result.json", "logs/run.log", "artifacts/progress.json",
             "artifacts/qlib_readback.json", "artifacts/funding_coverage.json",
             "artifacts/signal_metrics.json", "artifacts/falsification.json",
             "artifacts/stress_effects.json", "artifacts/dca_layer_histogram.json",
             "artifacts/cohort_results.json", "artifacts/cohort_survivors.json",
             "artifacts/assertions.json",
             *((("artifacts/grid_%s.csv" % g) for g in GRIDS)))

# --------------------------------------------------------------------------- #
# Small helpers                                                                #
# --------------------------------------------------------------------------- #
def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return "sha256:" + h.hexdigest()


def utc_ms(day):
    if len(day) == 10:
        day += "T00:00:00Z"
    if day.endswith("Z"):
        day = day[:-1] + "+00:00"
    return int(dt.datetime.fromisoformat(day).timestamp() * 1000)


def hour_stamp(ms):
    return dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def now_utc():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(value, fh, indent=2, ensure_ascii=False, allow_nan=False)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def axis(got, registered):
    return tuple(got) == tuple(registered)


def month_ordinals(open_ms):
    days = (np.asarray(open_ms, dtype=np.int64) // MS_DAY).astype("datetime64[D]")
    return days.astype("datetime64[M]").astype(np.int64)


def expected_counts():
    per_cohort = len(CASES) * len(DCA_GRID)
    cohorts = len(SYMBOLS) * len(TIMEFRAMES)
    return {"cohorts": cohorts, "strategy_cases_per_cohort": len(CASES),
            "dca_configs_per_cohort": len(DCA_GRID),
            "base_combinations_per_cohort": per_cohort,
            "case_evaluations_per_grid": per_cohort * cohorts,
            "grid_count": len(GRIDS),
            "case_evaluations_total": per_cohort * cohorts * len(GRIDS)}


def parameter_contract():
    """Frozen generic Strategy Family Parameter Contract (contract section 29)."""
    dca_fields = list(DCA_AXES)
    strategy_fields = list(STRATEGY_AXES)
    axes = [{"name": name, "kind": "atomic", "members": [name],
             "registered_values": list(dom), "row_fields": [name]}
            for name, dom in STRATEGY_AXES.items()]
    axes += [{"name": name, "kind": "atomic", "members": [name],
              "registered_values": list(dom), "row_fields": [name]}
             for name, dom in DCA_AXES.items()]
    return {
        "parameter_contract_version": 1, "family_id": FAMILY_ID,
        "contract_ref": "generic Strategy Family Parameter Contract, contract v2.0.0 "
                        "section 29; frozen copy lives here and is the single source of truth",
        "research_axes_ordered": axes,
        "row_fields": strategy_fields + dca_fields, "composite_map": {},
        "strategy_param_fields": strategy_fields, "dca_param_fields": dca_fields,
        "canonical_recipe": {"sort_keys": True, "separators": [",", ":"],
                             "ensure_ascii": False,
                             "numeric_rule": "JSON number finite, bool excluded"},
        "row_match_recipe": {"keys": ["symbol", "timeframe"] + strategy_fields + dca_fields,
                             "equality": "exact, numeric == float compare, rest bytewise"},
        "non_params": ["symbol", "timeframe", "case_label", "grid", "decomposition_ok"],
        "domain_cardinality": {"strategy": len(CASES), "dca": len(DCA_GRID),
                               "per_cohort": len(CASES) * len(DCA_GRID)}}


def signal_constants():
    return {
        "signal_timeframe": "daily (1d) bars; the record's lookbacks are day-counts and its "
                            "evaluation horizon is monthly, so the factor is formed on the "
                            "daily grid for every execution timeframe",
        "formation": "month-end close = last daily bar of each UTC month",
        "entry_timing": "first bar of the following month at open + 1 tick adverse slippage "
                        "(record: tradable at next month's open)",
        "exit_timing": "close of the last bar of the holding month - 1 tick adverse "
                       "(record: monthly rebalance, 1 month holding period)",
        "components": {
            "bidaskhl_w1": "mean over the trailing w1 daily bars of (high - low) / "
                           "((high + low) / 2), the locally computable high-low range "
                           "spread proxy for the record's bid-ask high-low characteristic",
            "zero_trades_w2": "count of daily bars with volume == 0 in the trailing w2 days",
            "dolvol_var_w2": "population variance of daily dollar volume (volume x close) "
                             "over the trailing w2 days",
            "trail_mean_w3": "mean daily dollar volume over the trailing w3 days "
                             "(record: 12 months = 365 days)",
            "score": "rank(bidaskhl) x rank(zero_trades) x "
                     "rank(dolvol_var / (1 + trail_mean))",
            "rank": "cross-sectional percentile inside the four-symbol cross-section, "
                    "average ranks for ties, (avg_rank - 1) / (n - 1)"},
        "direction_rule": "sort by (score desc, component-1 rank desc, symbol asc): "
                          "top-1 long, bottom-1 short, middle flat",
        "cross_section": "BNBUSDT, BTCUSDT, ETHUSDT, SOLUSDT (complete local universe; "
                         "coverage gate: every formation month must score all four symbols)",
        "lookback_scales": list(LOOKBACK_SCALES),
        "base_lookbacks_days": dict(BASE_LOOKBACKS),
        "lookback_days_by_case": {c["label"]: {"bidaskhl_days": c["bidaskhl_days"],
                                               "zero_dolvol_days": c["zero_dolvol_days"],
                                               "trail_mean_days": c["trail_mean_days"]}
                                  for c in CASES},
        "pit_status": PIT_STATUS,
        "pit_rule": "every component reads daily bars with open_time <= the formation bar; "
                    "verified in-run by a suffix-perturbation recheck",
    }


def validate_spec(spec, script_path=None, test_path=None):
    if spec.get("family_id") != FAMILY_ID \
            or spec.get("selector_version") != SELECTOR_VERSION \
            or spec.get("disposition_version") != DISPOSITION_VERSION:
        raise ValueError("wrong family/selector/disposition identity")
    rid = spec.get("round_id")
    if not (isinstance(rid, str) and re.fullmatch(re.escape(FAMILY_ID) + r"-r[1-9][0-9]*", rid)
            and isinstance(spec.get("run_id"), str)
            and re.fullmatch(re.escape(rid) + r"-u[1-9][0-9]*", spec["run_id"])):
        raise ValueError("round/run identity mismatch")
    if any(k in spec for k in ("task_id", "kanban_task_id", "kanban_board")):
        raise ValueError("direct family must not carry task/board ids")
    if not spec.get("created_at_utc"):
        raise ValueError("missing created_at_utc")
    data = spec.get("data", {})
    if data.get("raw_root") != "/data/raw/binance/usdm" or data.get("fields") != list(FIELDS) \
            or data.get("symbols") != list(SYMBOLS) or data.get("start") != PHASES["full"][0] \
            or data.get("end") != PHASES["full"][1]:
        raise ValueError("canonical local universe/window mismatch")
    if data.get("signal_interval") != SIGNAL_INTERVAL \
            or data.get("timeframes") != [dict(tf) for tf in TIMEFRAMES]:
        raise ValueError("signal timeframe/execution timeframes mismatch")
    if spec.get("split") != {"historical_start": PHASES["historical"][0],
                             "historical_end": PHASES["historical"][1],
                             "oos_start": PHASES["oos"][0], "oos_end": PHASES["oos"][1],
                             "rule": "chronological; frozen before compute; OOS never used "
                                     "for selection"}:
        raise ValueError("frozen split changed")
    if spec.get("params") != [dict(c) for c in CASES]:
        raise ValueError("strategy domain must be the exact registered case set")
    dca = spec.get("dca_domain", {})
    if any(not axis(dca.get(k, ()), v) for k, v in DCA_AXES.items()) \
            or dca.get("grid") != [dict(d) for d in DCA_GRID] \
            or dca.get("base_quote") != BASE_QUOTE:
        raise ValueError("DCA domain incomplete/changed")
    if dca.get("base_quote_status") != "PROJECT_PRE_REGISTERED_CONSTANT" \
            or any(dca.get(k + "_status") != "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN"
                   for k in DCA_AXES):
        raise ValueError("DCA provenance mismatch")
    if spec.get("expected") != expected_counts() \
            or spec.get("expected_outputs") != list(ARTIFACTS) \
            or spec.get("falsification") != list(FALSIFICATION):
        raise ValueError("coverage/artifact/falsification registration mismatch")
    if spec.get("gates") != {"min_episodes_is": MIN_EPISODES_IS,
                             "min_episodes_oos": MIN_EPISODES_OOS,
                             "min_neighbour_same_sign_fraction": MIN_NEIGHBOUR}:
        raise ValueError("selector gates changed")
    if spec.get("signal_constants") != signal_constants():
        raise ValueError("signal constants must stay the registered values")
    script = spec.get("script", {})
    if script.get("path") != "/scripts/320_aeap_seads_run.py" \
            or script.get("sha256") != sha256_file(script_path or __file__):
        raise ValueError("script bytes do not match pinned identity")
    engine = spec.get("engine", {})
    test = Path(test_path) if test_path else \
        Path(__file__).resolve().with_name("tests") / "test_aeap_seads_engine.py"
    if engine.get("name") != ENGINE_VERSION or engine.get("qlib_version") != "0.9.7" \
            or engine.get("seed") != SEED or engine.get("self_check_sha256") != sha256_file(test):
        raise ValueError("engine/self-check identity mismatch")
    with open(RAW_ROOT + "/_meta/CONFIG.json", encoding="utf-8") as fh:
        catalog = json.load(fh)
    if catalog.get("venue") != "BINANCE" or catalog.get("market_type") != "usdm_perp" \
            or not set(SYMBOLS) <= set(catalog.get("symbols", [])) \
            or not {tf["raw_interval"] for tf in TIMEFRAMES} <= set(catalog.get("intervals", [])) \
            or not {"klines", "funding"} <= set(catalog.get("datasets", {})):
        raise ValueError("canonical catalog diverged from registered universe")
    return expected_counts()


# --------------------------------------------------------------------------- #
# Canonical raw readers                                                        #
# --------------------------------------------------------------------------- #
def instrument_metadata(path=INSTRUMENTS_PATH):
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    by_symbol = {r["fields"]["raw_symbol"]: r["fields"] for r in doc["instruments"]}
    out = {}
    for s in SYMBOLS:
        f = by_symbol[s]
        if f["type"] != "CryptoPerpetual" or f["quote_currency"] != "USDT" or f["is_inverse"]:
            raise ValueError("not a linear USDT perp: " + s)
        tick, fee = float(f["price_increment"]), float(f["taker_fee"])
        if not (tick > 0 and 0 <= fee < 0.01):
            raise ValueError("invalid instrument metadata: " + s)
        out[s] = {"tick": tick, "taker_fee": fee, "source_sha256": sha256_file(path)}
    return out


def _shards(symbol, interval, start, end):
    root = Path(RAW_ROOT) / "binance" / "usdm" / "klines" / symbol / interval
    if not root.is_dir():
        raise RuntimeError("missing canonical raw directory: %s" % root)
    files = [p for p in sorted(root.glob("%s-%s-*.jsonl.gz" % (symbol, interval)))
             if start[:7] <= p.name.split("-%s-" % interval)[1][:7] <= end[:7]]
    if not files:
        raise RuntimeError("no canonical %s shards for %s %s" % (interval, symbol, start))
    return files


def raw_panel(symbol, interval, start, end):
    """Fail-closed OHLCV reader: strict native grid, no gaps, no imputation."""
    bar_ms = BAR_MS[interval]
    files = _shards(symbol, interval, start, end)
    lo, hi = utc_ms(start), utc_ms(end) + MS_DAY
    times, vals = [], {k: [] for k in FIELDS}
    for path in files:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                row = json.loads(line)
                t = int(row["open_time_ms"])
                if not lo <= t < hi:
                    continue
                if times and t - times[-1] != bar_ms:
                    raise RuntimeError("non-contiguous %s grid %s/%s at %s"
                                       % (interval, symbol, interval, hour_stamp(t)))
                for k in FIELDS:
                    v = float(row[k])
                    if not math.isfinite(v) or (v <= 0 if k != "volume" else v < 0):
                        raise RuntimeError("invalid %s %s" % (symbol, k))
                    vals[k].append(v)
                times.append(t)
    if len(times) < 3:
        raise RuntimeError("too few bars %s/%s: %d" % (symbol, interval, len(times)))
    if times[0] - lo > bar_ms or hi - (times[-1] + bar_ms) > bar_ms:
        raise RuntimeError("window coverage too thin %s/%s: %s..%s"
                           % (symbol, interval, hour_stamp(times[0]), hour_stamp(times[-1])))
    if any(vals["high"][i] < max(vals["open"][i], vals["close"][i])
           or vals["low"][i] > min(vals["open"][i], vals["close"][i])
           or vals["low"][i] > vals["high"][i] for i in range(len(times))):
        raise RuntimeError("invalid OHLC geometry: " + symbol)
    return {"open_ms": np.asarray(times, np.int64),
            **{k: np.asarray(vals[k], np.float64) for k in FIELDS},
            "raw_files": [{"path": str(p), "sha256": sha256_file(p)} for p in files]}


def load_funding_events(symbol, start, end, root=RAW_ROOT):
    """Official funding observations only, in the registered window (modeled rows ignored)."""
    path = Path(root) / "binance/usdm/funding" / symbol / (symbol + "-funding.jsonl.gz")
    report = {"file_missing": not path.is_file(), "official": 0, "modeled_ignored": 0,
              "other_ignored": 0, "invalid_official": 0,
              "missing_intervals_are_zero_not_modeled": True,
              "note": "official observations only; an unobserved interval costs zero and is "
                      "never replaced by a modelled row"}
    times, amounts = [], []
    if not path.is_file():
        report["charged_events"] = 0
        report["outside_bars"] = 0
        return np.zeros(0, np.int64), np.zeros(0, np.float64), report
    report["source_sha256"] = sha256_file(path)
    lo, hi = utc_ms(start), utc_ms(end) + MS_DAY
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            t = int(row["funding_time_ms"])
            if not lo <= t < hi:
                continue
            if row.get("truth_status") != "official":
                report["modeled_ignored" if str(row.get("truth_status", "")).startswith("modeled")
                       else "other_ignored"] += 1
                continue
            rate, mark = float(row["funding_rate"]), float(row["mark_price"])
            if not math.isfinite(rate) or not math.isfinite(mark) or mark <= 0:
                report["invalid_official"] += 1
                continue
            times.append(t)
            amounts.append(rate * mark)
            report["official"] += 1
    report["charged_events"] = len(times)
    return np.asarray(times, np.int64), np.asarray(amounts, np.float64), report


def bucket_funding(times, amounts, open_ms):
    """Bucket official observations into their bar (charged only while the book is live)."""
    events = [[] for _ in open_ms]
    outside = 0
    for t, amount in zip(times.tolist(), amounts.tolist()):
        idx = int(np.searchsorted(open_ms, t, side="right") - 1)
        if idx < 0:
            outside += 1
            continue
        events[idx].append((t, amount))
    return events, outside


# --------------------------------------------------------------------------- #
# Qlib 0.9.7 build + readback (the production signal source)                   #
# --------------------------------------------------------------------------- #
def qlib_api_freq(tf):
    # ponytail: use the registered key verbatim; dump_bin writes ``week.txt`` for weekly
    # input while ``1week`` has no matching Qlib calendar (proven in 190's build).
    return tf["qlib_freq"]


def qlib_week_aliases():
    calendar = os.path.join(QLIB_DIR, "calendars", "week.txt")
    if os.path.isfile(calendar):
        shutil.copyfile(calendar, os.path.join(QLIB_DIR, "calendars", "1week.txt"))
    copied = 0
    for directory in Path(os.path.join(QLIB_DIR, "features")).glob("*"):
        for source in directory.glob("*.week.bin"):
            target = source.with_name(source.name.replace(".week.bin", ".1week.bin"))
            shutil.copyfile(source, target)
            copied += 1
    if not os.path.isfile(os.path.join(QLIB_DIR, "calendars", "1week.txt")) \
            or copied != len(SYMBOLS) * len(FIELDS):
        raise RuntimeError("weekly Qlib aliases incomplete: copied=%d" % copied)
    return copied


def write_csv(symbol, tf, start, end):
    out_dir = os.path.join(CSV_ROOT, tf["qlib_freq"])
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "%s.csv" % symbol)
    panel = raw_panel(symbol, tf["raw_interval"], start, end)
    stamp = (hour_stamp if tf["raw_interval"] not in ("1d", "1w")
             else lambda ms: hour_stamp(ms)[:10])
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["date", *FIELDS])
        for i, ms in enumerate(panel["open_ms"]):
            writer.writerow([stamp(int(ms)), *(panel[k][i] for k in FIELDS)])
    report = {"rows": int(len(panel["open_ms"])),
              "first_open": hour_stamp(int(panel["open_ms"][0])),
              "last_open": hour_stamp(int(panel["open_ms"][-1])),
              "bar_step_ms": BAR_MS[tf["raw_interval"]],
              "csv": out_path, "csv_bytes": os.path.getsize(out_path),
              "raw_files": panel["raw_files"]}
    return panel, report


def build_qlib_and_readback(start, end, log):
    """Dump every registered timeframe through Qlib 0.9.7 and read every panel back.

    The readback - not the raw parse - is the production data source; the raw parse
    is only the equality reference (rtol 1e-6 for Qlib's float32 storage).
    """
    # wipe only the build outputs; the attempt dir can live under WORK_ROOT (smoke)
    for directory in (CSV_ROOT, QLIB_DIR):
        if os.path.isdir(directory):
            shutil.rmtree(directory)
    os.makedirs(CSV_ROOT, exist_ok=True)
    per_dataset, panels = {}, {}
    for tf in TIMEFRAMES:
        raw = {}
        for symbol in SYMBOLS:
            panel, report = write_csv(symbol, tf, start, end)
            raw[symbol] = panel
            per_dataset["%s/%s" % (symbol, tf["raw_interval"])] = report
            log("csv %s/%s rows=%d" % (symbol, tf["raw_interval"], report["rows"]))
        cmd = [VENV_PYTHON, DUMP_BIN, "dump_all", "--data_path",
               os.path.join(CSV_ROOT, tf["qlib_freq"]), "--qlib_dir", QLIB_DIR, "--freq",
               qlib_api_freq(tf), "--include_fields", ",".join(FIELDS),
               "--date_field_name", "date", "--max_workers", "1"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=14400)
        if proc.returncode:
            raise RuntimeError("dump_bin %s failed rc=%d\n%s\n%s"
                               % (tf["qlib_freq"], proc.returncode,
                                  proc.stdout[-1500:], proc.stderr[-1500:]))
        if tf["raw_interval"] == "1w":
            log("weekly Qlib aliases=%d" % qlib_week_aliases())
        log("dump_bin %s ok" % qlib_api_freq(tf))
        for symbol in SYMBOLS:
            panels[(symbol, tf["raw_interval"])] = raw.pop(symbol)
        del raw
    import qlib
    from qlib.data import D
    if qlib.__version__ != "0.9.7":
        raise RuntimeError("wrong Qlib version: %s" % qlib.__version__)
    qlib.init(provider_uri=QLIB_DIR, region="cn", expression_cache=None, dataset_cache=None)
    report = {"qlib_version": qlib.__version__, "read_path": "qlib.data.D.features",
              "float32_roundtrip_rtol": 1e-6, "datasets": {}}
    for tf in TIMEFRAMES:
        for symbol in SYMBOLS:
            reference = panels[(symbol, tf["raw_interval"])]
            df = D.features([symbol], ["$" + k for k in FIELDS], start_time=start,
                            end_time=end + " 23:59:59", freq=qlib_api_freq(tf)).sort_index()
            if len(df) == 0 or int(df.isnull().sum().sum()) != 0:
                raise RuntimeError("Qlib readback empty/null for %s %s"
                                   % (symbol, tf["raw_interval"]))
            ms = np.asarray([int(ts.value // 1_000_000)
                             for ts in df.index.get_level_values("datetime")], np.int64)
            if len(ms) != len(reference["open_ms"]) or not np.array_equal(ms, reference["open_ms"]):
                raise RuntimeError("Qlib timestamp mismatch for %s/%s (%d vs %d)"
                                   % (symbol, tf["raw_interval"], len(ms),
                                      len(reference["open_ms"])))
            readback = {"open_ms": ms}
            for k in FIELDS:
                col = df["$" + k].to_numpy(dtype=np.float64)
                if not np.isfinite(col).all() or not np.allclose(col, reference[k],
                                                                 rtol=1e-6, atol=1e-9):
                    raise RuntimeError("Qlib value mismatch for %s/%s field %s"
                                       % (symbol, tf["raw_interval"], k))
                readback[k] = col
            readback["raw_files"] = reference["raw_files"]
            panels[(symbol, tf["raw_interval"])] = readback
            report["datasets"]["%s/%s" % (symbol, tf["raw_interval"])] = {
                "rows": int(len(ms)), "qlib_freq": qlib_api_freq(tf),
                "first_open": hour_stamp(int(ms[0])), "last_open": hour_stamp(int(ms[-1])),
                "matches_raw": True,
                "raw_shards": len(reference["raw_files"])}
    return panels, report


# --------------------------------------------------------------------------- #
# Cross-sectional factor layer (record: rank-product formulaic alpha)          #
# --------------------------------------------------------------------------- #
def _trailing_mean(x, w):
    n = len(x)
    out = np.full(n, np.nan)
    cs = np.concatenate(([0.0], np.cumsum(x)))
    idx = np.arange(w - 1, n)
    out[idx] = (cs[idx + 1] - cs[idx + 1 - w]) / float(w)
    return out


def _trailing_var(x, w):
    n = len(x)
    out = np.full(n, np.nan)
    cs = np.concatenate(([0.0], np.cumsum(x)))
    cs2 = np.concatenate(([0.0], np.cumsum(x * x)))
    idx = np.arange(w - 1, n)
    s1 = cs[idx + 1] - cs[idx + 1 - w]
    s2 = cs2[idx + 1] - cs2[idx + 1 - w]
    out[idx] = np.maximum(s2 / w - (s1 / w) ** 2, 0.0)
    return out


def _trailing_count(mask, w):
    return _trailing_mean(np.asarray(mask, np.float64), w) * float(w)


def _trailing_max(x, w):
    n = len(x)
    out = np.full(n, np.nan)
    view = np.lib.stride_tricks.sliding_window_view(x, w)
    idx = np.arange(w - 1, n)
    out[idx] = view.max(axis=1)
    return out


def _trailing_min(x, w):
    n = len(x)
    out = np.full(n, np.nan)
    view = np.lib.stride_tricks.sliding_window_view(x, w)
    idx = np.arange(w - 1, n)
    out[idx] = view.min(axis=1)
    return out


def average_rank_percentile(values):
    """Average ranks for ties, mapped to (rank - 1) / (n - 1) in [0, 1]."""
    v = np.asarray(values, np.float64)
    n = len(v)
    order = np.argsort(v, kind="mergesort")
    ranks = np.empty(n, np.float64)
    ranks[order] = np.arange(1, n + 1, dtype=np.float64)
    sorted_v = v[order]
    start = 0
    for i in range(1, n + 1):
        if i == n or sorted_v[i] != sorted_v[start]:
            if i - start > 1:
                ranks[order[start:i]] = ranks[order[start:i]].mean()
            start = i
    if n == 1:
        return np.zeros(1, np.float64)
    return (ranks - 1.0) / float(n - 1)


def spearman(a, b):
    x = np.asarray(a, np.float64)
    y = np.asarray(b, np.float64)
    if len(x) < 2 or np.all(x == x[0]) or np.all(y == y[0]):
        return None
    rx, ry = average_rank_percentile(x), average_rank_percentile(y)
    x0, y0 = rx - rx.mean(), ry - ry.mean()
    denom = math.sqrt(float((x0 * x0).sum()) * float((y0 * y0).sum()))
    if denom <= 1e-18:
        return None
    return float((x0 * y0).sum() / denom)


def factor_components(daily, lookbacks):
    """Trailing component series for one symbol; every window ends at its own bar."""
    w1, w2, w3 = lookbacks
    high, low, close, volume = daily["high"], daily["low"], daily["close"], daily["volume"]
    mid = (high + low) / 2.0
    hl_range = np.where(mid > 0, (high - low) / np.maximum(mid, 1e-12), np.nan)
    dolvol = volume * close
    comps = {"bidaskhl": _trailing_mean(hl_range, w1),
             "zero_trades": _trailing_count(volume <= 0.0, w2),
             "dolvol_var": _trailing_var(dolvol, w2),
             "trail_mean": _trailing_mean(dolvol, w3),
             "dolvol": dolvol, "close": close}
    with np.errstate(invalid="ignore", divide="ignore"):
        comps["ratio"] = comps["dolvol_var"] / (1.0 + comps["trail_mean"])
    return comps


def reference_components(daily):
    """Pre-registered reference panel + random-ablation pool (fixed source windows)."""
    high, low, close, volume = daily["high"], daily["low"], daily["close"], daily["volume"]
    mid = (high + low) / 2.0
    dolvol = volume * close
    logret = np.full(len(close), np.nan)
    logret[1:] = np.log(np.maximum(close[1:], 1e-12) / np.maximum(close[:-1], 1e-12))
    out = {}
    for lag, name in ((365, "mom_12m"),):
        base = np.full(len(close), np.nan)
        base[lag:] = close[lag:] / np.maximum(close[:-lag], 1e-12) - 1.0
        out[name] = base
    rev = np.full(len(close), np.nan)
    rev[21:] = -(close[21:] / np.maximum(close[:-21], 1e-12) - 1.0)
    out["rev_21d"] = rev
    out["vol_21d"] = np.sqrt(_trailing_var(logret[1:], 21))
    vol_pad = np.concatenate(([np.nan], out["vol_21d"]))
    out["vol_21d"] = vol_pad[:len(close)]
    out["dolvol_level_126d"] = np.log(np.maximum(_trailing_mean(dolvol, 126), 1e-12))
    absret = np.abs(logret)
    absret[0] = np.nan
    amihud = absret / np.maximum(dolvol, 1.0)
    out["amihud_126d"] = _trailing_mean(np.nan_to_num(amihud, nan=0.0), 126)
    out["amihud_126d"][:125] = np.nan
    out["price_range_126d"] = ((_trailing_max(high, 126) - _trailing_min(low, 126))
                               / np.maximum(mid, 1e-12))
    w1, w2, _ = lookback_days(1.0)
    base = factor_components(daily, (w1, w2, lookback_days(1.0)[2]))
    out["bidaskhl_21d"] = base["bidaskhl"]
    out["zero_trades_126d"] = base["zero_trades"]
    out["dolvol_var_ratio_126d"] = base["ratio"]
    return out


def formation_indices(open_ms):
    """Last daily bar of every UTC month (the record's month-end formation timestamp)."""
    months = month_ordinals(open_ms)
    last_of_month = np.r_[months[1:] != months[:-1], True]
    return np.flatnonzero(last_of_month), months[last_of_month]


def build_case_signal(case, daily_by_symbol, log=None):
    """Cross-sectional rank-product scores and directions for one strategy case."""
    lookbacks = (case["bidaskhl_days"], case["zero_dolvol_days"], case["trail_mean_days"])
    comps = {sym: factor_components(daily_by_symbol[sym], lookbacks) for sym in SYMBOLS}
    ref_ts = daily_by_symbol[SYMBOLS[0]]["open_ms"]
    for sym in SYMBOLS[1:]:
        if not np.array_equal(daily_by_symbol[sym]["open_ms"], ref_ts):
            raise RuntimeError("cross-sectional daily timestamps do not align: " + sym)
    f_idx, f_months = formation_indices(ref_ts)
    scores, dirs, rows, skipped = {}, {}, [], []
    for k, (i, month) in enumerate(zip(f_idx, f_months)):
        values = {}
        complete = True
        for sym in SYMBOLS:
            rec = {c: comps[sym][c][i] for c in ("bidaskhl", "zero_trades", "ratio")}
            if any(not np.isfinite(v) for v in rec.values()):
                complete = False
                break
            values[sym] = rec
        if not complete:
            skipped.append(int(month))
            continue
        n = len(SYMBOLS)
        ranks = {}
        for comp in ("bidaskhl", "zero_trades", "ratio"):
            vec = np.array([values[s][comp] for s in SYMBOLS], np.float64)
            pct = average_rank_percentile(vec)
            for j, s in enumerate(SYMBOLS):
                ranks.setdefault(s, {})[comp] = float(pct[j])
        score = {s: ranks[s]["bidaskhl"] * ranks[s]["zero_trades"] * ranks[s]["ratio"]
                 for s in SYMBOLS}
        order = sorted(SYMBOLS, key=lambda s: (-score[s], -ranks[s]["bidaskhl"], s))
        direction = {s: 0 for s in SYMBOLS}
        direction[order[0]] = 1
        direction[order[-1]] = -1
        scores[int(month)] = score
        dirs[int(month)] = direction
        rows.append({"month": int(month), "formation_bar": hour_stamp(int(ref_ts[i])),
                     "cross_section": n, "coverage": n / float(len(SYMBOLS)),
                     "scores": {s: float(score[s]) for s in SYMBOLS},
                     "direction": {s: int(direction[s]) for s in SYMBOLS},
                     "component_ranks": {s: {c: float(ranks[s][c])
                                             for c in ("bidaskhl", "zero_trades", "ratio")}
                                         for s in SYMBOLS}})
    if log:
        log("signal %s: formations=%d skipped_warmup=%d nonzero=%d"
            % (case["label"], len(rows), len(skipped),
               sum(1 for d in dirs.values() if any(v != 0 for v in d.values()))))
    if not rows:
        raise RuntimeError("signal produced no formation months: " + case["label"])
    return {"case": case, "scores": scores, "directions": dirs, "formations": rows,
            "skipped_months": skipped}


def direction_series(signal, open_ms):
    """Per-bar target: the direction of the bar's own UTC month (0 before warm-up)."""
    months = month_ordinals(open_ms)
    keys = np.array(sorted(signal["directions"]), np.int64)
    if len(keys) == 0:
        return np.zeros(len(months), np.int8)
    vals = np.array([[signal["directions"][int(k)][s] for s in SYMBOLS] for k in keys],
                    np.int8)
    idx = np.searchsorted(keys, months)
    clipped = np.minimum(idx, len(keys) - 1)
    hit = keys[clipped] == months
    return np.where(hit[:, None], vals[clipped], 0).astype(np.int8)


def direction_for_symbol(signal, open_ms, symbol):
    months = month_ordinals(open_ms)
    keys = np.array(sorted(signal["directions"]), np.int64)
    vals = np.array([signal["directions"][int(k)][symbol] for k in keys], np.int8)
    idx = np.searchsorted(keys, months)
    clipped = np.minimum(idx, len(keys) - 1)
    hit = keys[clipped] == months
    return np.where(hit, vals[clipped], 0).astype(np.int8)


def episode_bounds(open_ms):
    """First/last bar index of every UTC-month episode (the holding period)."""
    months = month_ordinals(open_ms)
    starts = np.flatnonzero(np.r_[True, months[1:] != months[:-1]])
    ends = np.r_[starts[1:] - 1, len(months) - 1]
    span = np.diff(np.r_[starts, len(months)])
    ep_start = np.repeat(starts, span)
    ep_end = np.repeat(ends, span)
    return ep_start, ep_end, months


def pit_recheck(case, daily_by_symbol, signal, samples=(0.2, 0.45, 0.7, 0.9)):
    """Suffix-perturbation proof: formation t never reads data after its own bar."""
    ref_ts = daily_by_symbol[SYMBOLS[0]]["open_ms"]
    f_idx, _ = formation_indices(ref_ts)
    picks = sorted({int(f_idx[min(len(f_idx) - 1, int(f * (len(f_idx) - 1)))])
                    for f in samples})
    failed, shown = 0, []
    for i in picks:
        probe = {}
        for sym in SYMBOLS:
            p = dict(daily_by_symbol[sym])
            for k in FIELDS:
                arr = p[k].copy()
                arr[i + 1:] = arr[i + 1:] * 1.37 + 11.0
                p[k] = arr
            probe[sym] = p
        alt = build_case_signal(case, probe)
        month = int(month_ordinals(ref_ts)[i])
        same = (signal["scores"].get(month) == alt["scores"].get(month)
                and signal["directions"].get(month) == alt["directions"].get(month))
        if not same:
            failed += 1
        shown.append({"formation_bar": hour_stamp(int(ref_ts[i])), "unchanged": bool(same)})
    return {"checked": len(picks), "failed_samples": failed, "samples": shown,
            "method": "every field after the formation bar replaced by an arbitrary "
                      "deterministic series; the formation's score and direction must be "
                      "bit-identical"}


# --------------------------------------------------------------------------- #
# Monthly long-short portfolio diagnostics (falsification inputs)              #
# --------------------------------------------------------------------------- #
def next_month_returns(daily_by_symbol):
    """Per-symbol month-over-month close-to-close return keyed by formation month.

    A month is only kept when every symbol in the cross-section has both closes,
    so Rank-IC and the long-short diagnostics always see the same four names.
    """
    ref_ts = daily_by_symbol[SYMBOLS[0]]["open_ms"]
    months = month_ordinals(ref_ts)
    f_idx, f_months = formation_indices(ref_ts)
    close_at = {s: {int(m): float(daily_by_symbol[s]["close"][i])
                    for i, m in zip(f_idx, f_months)} for s in SYMBOLS}
    out = {}
    for m in sorted({int(x) for x in f_months}):
        nxt = m + 1            # month ordinals are linear; +1 is always the next month
        row = {s: close_at[s][nxt] / close_at[s][m] - 1.0
               for s in SYMBOLS
               if nxt in close_at[s] and close_at[s].get(m, 0.0) > 0}
        if len(row) == len(SYMBOLS):
            out[m] = row
    return out, months


def rank_ic_series(signal, daily_by_symbol):
    """Monthly cross-sectional Rank-IC of the score against the next month's return."""
    ret_by_month, _ = next_month_returns(daily_by_symbol)
    series, skipped = {}, []
    for month, score in sorted(signal["scores"].items()):
        realised = ret_by_month.get(month)
        if realised is None:
            continue
        x = np.array([score[s] for s in SYMBOLS], np.float64)
        y = np.array([realised[s] for s in SYMBOLS], np.float64)
        ic = spearman(x, y)
        if ic is None:
            skipped.append(int(month))
            continue
        series[int(month)] = ic
    return series, skipped


def long_short_returns(signal, daily_by_symbol):
    """Unlevered equal-weight top-1 minus bottom-1 monthly portfolio return."""
    ret_by_month, _ = next_month_returns(daily_by_symbol)
    out = {}
    for month, direction in sorted(signal["directions"].items()):
        realised = ret_by_month.get(month)
        if realised is None:
            continue
        longs = [s for s in SYMBOLS if direction[s] > 0]
        shorts = [s for s in SYMBOLS if direction[s] < 0]
        if not longs or not shorts:
            continue
        long_leg = float(np.mean([realised[s] for s in longs]))
        short_leg = float(np.mean([realised[s] for s in shorts]))
        out[int(month)] = 0.5 * (long_leg - short_leg)
    return out


# --------------------------------------------------------------------------- #
# Registered DCA execution rail (fpca-proven deterministic ordering)           #
# --------------------------------------------------------------------------- #
def empty_metric():
    return {"gross_pnl": 0.0, "fees": 0.0, "funding": 0.0, "net_pnl": 0.0,
            "ending_equity": START_EQUITY, "episodes": 0, "fills": 0, "adds": 0,
            "turnover_usdt": 0.0, "sharpe": 0.0, "max_dd_pct": 0.0, "max_dd_usdt": 0.0,
            "annualized_return": 0.0, "max_effective_leverage": 0.0,
            "capital_utilization": 0.0, "tp_hits": 0, "stop_hits": 0, "margin_calls": 0,
            "end_exits": 0, "open_at_end": 0, "month_exits": 0, "signal_flat_exits": 0,
            "layer_hist": [0] * LADDER_LEVELS,
            "decomposition_ok": True}


METRIC_KEYS = ("gross_pnl", "fees", "funding", "net_pnl", "ending_equity", "episodes",
               "fills", "adds", "turnover_usdt", "sharpe", "max_dd_pct", "max_dd_usdt",
               "annualized_return", "max_effective_leverage", "capital_utilization",
               "tp_hits", "stop_hits", "margin_calls", "end_exits", "open_at_end",
               "month_exits", "signal_flat_exits")


def metric_block(m):
    return {k: m[k] for k in METRIC_KEYS}


def simulate(panel, funding_events, direction, ep_start, ep_end, dca, i0, i1, cost, instrument):
    """Registered ladder on one cohort's bars for one (signal, DCA, phase, cost) cell.

    One bar = one deterministic conservative ordering:
      open actions (signal-flat / window flatten, then tranche #1 at the episode's
      entry bar at open + one adverse tick), then that bar's official funding
      settlements while the book is live, then the intrabar rail (open-gap
      invalidation, margin guard, adverse scale-ins in level order with
      stop-before-adds, resting invalidation, breakeven TP), then the record's
      registered exit - flatten at the close of the last bar of the holding month
      (monthly rebalance) - one adverse tick, then the window-boundary safety net.
    Gross PnL accumulates only from the independent price-PnL expression.
    """
    if i1 <= i0:
        return empty_metric()
    if i0 < 0 or i1 > len(panel["open"]):
        raise ValueError("window outside panel")
    tick = instrument["tick"] * int(cost.get("slip_ticks", 1))
    fee_rate = float(cost.get("fee_override",
                              instrument["taker_fee"] * float(cost.get("fee_mult", 1.0))))
    if fee_rate < 0:
        raise ValueError("negative fee")
    mult = float(dca["size_multiplier"])
    spacing = float(dca["spacing_pct"])
    tp_pct = float(dca["breakeven_tp_pct"])
    inv_pct = float(dca["invalidation_pct"])
    delay = int(cost.get("entry_delay", 0))
    funding_mult = float(cost.get("funding_mult", 1.0))
    no_funding = bool(cost.get("no_funding", False))
    n_bars = i1 - i0
    cash = np.zeros(n_bars)
    unreal = np.zeros(n_bars)
    gross = fees = funding_paid = turnover = realized = 0.0
    eps = fills = adds = tp_hits = stop_hits = margin_calls = 0
    month_exits = end_exits = signal_flat_exits = 0
    max_lev = max_util = 0.0
    hist = [0] * LADDER_LEVELS
    O, H, L, C, ms = (panel["open"], panel["high"], panel["low"], panel["close"],
                      panel["open_ms"])
    qty = basis = 0.0
    entry_price = 0.0
    quote0 = 0.0
    entry_bar = -1
    next_level = 1
    layers_used = 0
    held_dir = 0

    def close_position(exit_px, base, reason):
        nonlocal qty, basis, gross, fees, realized, turnover, fills, funding_paid
        nonlocal month_exits, end_exits, signal_flat_exits, hist
        ep_gross = qty * exit_px - basis          # independent price-only accumulator
        gross += ep_gross
        exit_fee = abs(qty) * abs(exit_px) * fee_rate
        realized += ep_gross - exit_fee
        fees += exit_fee
        turnover += abs(qty * exit_px)
        fills += 1
        cash[base] += ep_gross - exit_fee
        hist[0] += 1
        hist[min(LADDER_LEVELS - 1, layers_used)] += 1
        if reason == "month":
            month_exits += 1
        elif reason == "close":
            end_exits += 1
        elif reason == "signal_flat":
            signal_flat_exits += 1
        qty = basis = 0.0

    for i in range(i0, i1):
        base = i - i0
        target = int(direction[i])
        # register: tranche #1 fills only at the episode's own first bar (+delay);
        # a window that starts mid-episode never joins that book late.
        entry_bar_i = int(ep_start[i]) + delay
        open_px = float(O[i])
        if open_px <= 0:
            raise RuntimeError("nonpositive open")
        if qty != 0 and target == 0 and i == int(ep_start[i]):
            side = 1.0 if held_dir > 0 else -1.0
            close_position(open_px - tick * side, base, "signal_flat")
        if qty == 0 and target != 0 and i == entry_bar_i:
            side = 1.0 if target > 0 else -1.0
            fill = open_px + tick * side              # one adverse tick, both directions
            equity_before = START_EQUITY + realized
            quote0 = min(BASE_QUOTE * LEVERAGE, equity_before * LEVERAGE)
            if quote0 > 0 and equity_before > quote0 / LEVERAGE:
                qty0 = target * quote0 / fill
                fee0 = quote0 * fee_rate
                if equity_before - fee0 > quote0 / LEVERAGE:
                    realized -= fee0
                    fees += fee0
                    turnover += quote0
                    fills += 1
                    cash[base] -= fee0
                    qty, basis = qty0, qty0 * fill
                    entry_price = fill
                    entry_bar = i
                    next_level = 1
                    layers_used = 1
                    held_dir = target
                    eps += 1
        if qty != 0:
            side = 1.0 if qty > 0 else -1.0
            old_avg = basis / qty
            old_stop = old_avg * (1.0 - side * inv_pct)
            if not no_funding:
                for ev_t, amount in funding_events[i]:
                    if ev_t <= int(ms[i]):
                        continue
                    charge = qty * amount * funding_mult     # long pays, short receives
                    realized -= charge
                    funding_paid += charge
                    cash[base] -= charge
            if i > entry_bar and ((side > 0 and open_px <= old_stop)
                                  or (side < 0 and open_px >= old_stop)):
                close_position(open_px - tick * side, base, "stop")
                stop_hits += 1
            if qty != 0:
                adverse = float(L[i]) if side > 0 else float(H[i])
                margin_equity = START_EQUITY + realized + (qty * adverse - basis)
                if margin_equity > 0:
                    max_lev = max(max_lev, abs(qty) * adverse / margin_equity)
                    max_util = max(max_util, abs(qty) * adverse / LEVERAGE / margin_equity)
                if margin_equity <= 0 or abs(qty) * adverse / LEVERAGE > margin_equity:
                    close_position(adverse - tick * side, base, "margin")
                    margin_calls += 1
            if qty != 0:
                while next_level <= MAX_ADD_LEVELS:
                    trigger = entry_price * (1.0 - side * spacing * next_level)
                    reached = float(L[i]) <= trigger if side > 0 else float(H[i]) >= trigger
                    beyond = trigger <= old_stop if side > 0 else trigger >= old_stop
                    if not reached or beyond:
                        break
                    fill = trigger + tick * side
                    quote = quote0 * mult ** next_level
                    add_qty = side * quote / fill
                    add_fee = quote * fee_rate
                    equity_at_add = START_EQUITY + realized + (qty * fill - basis) - add_fee
                    if equity_at_add <= 0 or abs(qty + add_qty) * fill / LEVERAGE > equity_at_add:
                        close_position(fill - tick * side, base, "margin")
                        margin_calls += 1
                        break
                    realized -= add_fee
                    fees += add_fee
                    turnover += quote
                    fills += 1
                    adds += 1
                    cash[base] -= add_fee
                    qty += add_qty
                    basis += add_qty * fill
                    max_lev = max(max_lev, abs(qty) * fill / equity_at_add)
                    max_util = max(max_util, abs(qty) * fill / LEVERAGE / equity_at_add)
                    next_level += 1
                    layers_used += 1
            if qty != 0:
                avg = basis / qty
                stop = avg * (1.0 - side * inv_pct)
                take = avg * (1.0 + side * tp_pct)
                pre_hit = float(L[i]) <= old_stop if side > 0 else float(H[i]) >= old_stop
                new_hit = float(L[i]) <= stop if side > 0 else float(H[i]) >= stop
                if pre_hit or new_hit:
                    reached = [x for x, crossed in ((old_stop, pre_hit), (stop, new_hit))
                               if crossed]
                    trigger = min(reached) if side > 0 else max(reached)
                    close_position(trigger - tick * side, base, "stop")
                    stop_hits += 1
                elif (float(H[i]) >= take) if side > 0 else (float(L[i]) <= take):
                    close_position(take - tick * side, base, "tp")
                    tp_hits += 1
            # record exit: one-month holding period = close of the holding month's last bar
            if qty != 0 and i == int(ep_end[i]):
                close_position(float(C[i]) - tick * side, base, "month")
            if qty != 0 and i == i1 - 1:              # window-boundary safety net
                close_position(float(C[i]) - tick * side, base, "close")
        unreal[base] = qty * float(C[i]) - basis if qty != 0 else 0.0

    equity = START_EQUITY + np.cumsum(cash) + unreal
    net = float(realized)
    if abs(float(cash.sum()) - net) > 1e-6 or abs(float(equity[-1]) - START_EQUITY - net) > 1e-6:
        return {**empty_metric(), "decomposition_ok": False}

    # daily equity marks (net-of-fee) drive every reported metric, so cohorts with
    # different bar grids stay comparable (contract 7.2 daily marks, v1.3.1).
    bar_days = (np.asarray(ms[i0:i1], np.int64) // MS_DAY)
    days = np.arange(bar_days[0], bar_days[-1] + 1, dtype=np.int64)
    idx = np.searchsorted(bar_days, days, side="right") - 1
    idx = np.maximum(idx, 0)
    marks = equity[idx]
    rets = np.diff(np.r_[START_EQUITY, marks]) / np.maximum(np.r_[START_EQUITY, marks[:-1]],
                                                            1e-9)
    sd = float(np.std(rets, ddof=1)) if len(rets) > 1 else 0.0
    sharpe = float(np.mean(rets) / sd * math.sqrt(365)) if sd > 1e-12 else 0.0
    high = np.maximum.accumulate(np.r_[START_EQUITY, marks])
    dd = high[1:] - marks
    n_days = max(len(marks), 1)
    annual = float((max(float(marks[-1]), 1e-9) / START_EQUITY) ** (365.0 / n_days) - 1.0)
    return {"gross_pnl": float(gross), "fees": float(fees), "funding": float(funding_paid),
            "net_pnl": net, "ending_equity": float(marks[-1]), "episodes": eps,
            "fills": fills, "adds": adds, "turnover_usdt": float(turnover),
            "sharpe": sharpe,
            "max_dd_pct": float(np.max(dd / np.maximum(high[1:], 1e-9)) * 100) if len(dd) else 0.0,
            "max_dd_usdt": float(np.max(dd)) if len(dd) else 0.0, "annualized_return": annual,
            "max_effective_leverage": float(max_lev), "capital_utilization": float(max_util),
            "tp_hits": tp_hits, "stop_hits": stop_hits, "margin_calls": margin_calls,
            "end_exits": end_exits, "open_at_end": 1 if qty != 0 else 0,
            "month_exits": month_exits, "signal_flat_exits": signal_flat_exits,
            "layer_hist": hist,
            "decomposition_ok": abs(gross - fees - funding_paid - net) < 1e-3}


# --------------------------------------------------------------------------- #
# Selector / disposition (cohort-selector-v1, cohort-disposition-v1)           #
# --------------------------------------------------------------------------- #
def cell_key(row):
    return (row["symbol"], row["timeframe"], row["lookback_scale"],
            *(row[k] for k in DCA_AXES))


def _axis_index(axis_name, value):
    domain = STRATEGY_AXES.get(axis_name) or DCA_AXES[axis_name]
    return list(domain).index(value)


def neighbourhood(winner, historical):
    if winner["grid"] != "historical" or any(r["grid"] != "historical" for r in historical):
        raise ValueError("selector may only read historical rows")
    found = {cell_key(r): r for r in historical}
    # cell_key = (symbol, timeframe, strategy..., dca...); parameter positions start at 2
    steps = [((2,), list(STRATEGY_AXES["lookback_scale"]))]
    steps += [((pos,), list(domain)) for pos, domain in enumerate(DCA_AXES.values(), start=3)]
    agrees = count = 0
    key = list(cell_key(winner))
    for positions, domain in steps:
        pos = positions[0]
        ix = domain.index(key[pos])
        for delta in (-1, 1):
            if 0 <= ix + delta < len(domain):
                candidate = key.copy()
                candidate[pos] = domain[ix + delta]
                row = found.get(tuple(candidate))
                if row is None:
                    raise ValueError("neighbour cell missing from historical grid")
                count += 1
                agrees += float(row["net_pnl"]) > 0
    return {"neighbours": count, "agreeing": agrees,
            "same_sign_fraction": agrees / count if count else 0.0,
            "passed": bool(count and agrees / count >= MIN_NEIGHBOUR)}


def _order_key(r):
    """Sharpe desc, net_pnl desc, then registered-index lexical tie-break."""
    return (-float(r["sharpe"]), -float(r["net_pnl"]),
            _axis_index("lookback_scale", r["lookback_scale"]),
            *(_axis_index(k, r[k]) for k in DCA_AXES))


def select_cohort(rows):
    hist = rows["historical"]
    if not hist or any(r["grid"] != "historical" for r in hist):
        raise ValueError("selector saw a non-historical row")
    best = max(int(r["episodes"]) for r in hist)
    if best < MIN_EPISODES_IS:
        return None, {"cull_reasons": ["insufficient_trades"], "best_historical_episodes": best}
    candidates = [r for r in hist if int(r["episodes"]) >= MIN_EPISODES_IS
                  and float(r["net_pnl"]) > 0 and float(r["sharpe"]) > 0]
    if not candidates:
        return None, {"cull_reasons": ["no_qualifying_candidate"],
                      "best_historical_episodes": best}
    winner = min(candidates, key=_order_key)
    lookup = {g: next(r for r in rows[g] if cell_key(r) == cell_key(winner)) for g in GRIDS}
    reasons = []
    oos = lookup["oos"]
    if not (float(oos["net_pnl"]) > 0 and float(oos["sharpe"]) > 0
            and int(oos["episodes"]) >= MIN_EPISODES_OOS):
        reasons.append("oos_economic")
    if float(lookup["full"]["net_pnl"]) <= 0:
        reasons.append("full_economic")
    weak = [g for g in STRESS_GRIDS if float(lookup[g]["net_pnl"]) <= 0]
    if weak:
        reasons.append("robustness_economic:" + ",".join(weak))
    neighbours = neighbourhood(winner, hist)
    if not neighbours["passed"]:
        reasons.append("parameter_neighbourhood")
    phases = {g: metric_block(lookup[g]) for g in ("historical", "oos", "full")}
    robustness = {g: metric_block(lookup[g]) for g in STRESS_GRIDS}
    detail = {"winner": {k: winner[k] for k in ("lookback_scale", *DCA_AXES)},
              "winner_case_label": winner["case_label"],
              "winner_source_grid": "historical", "best_historical_episodes": best,
              "neighbourhood": neighbours, "phases": phases, "robustness": robustness,
              "metrics": {**phases, "robustness": robustness, "neighbourhood": neighbours},
              "cull_reasons": reasons}
    return (winner if not reasons else None), detail


def write_grid(path, rows):
    metric_keys = list(metric_block(empty_metric()))
    cols = ["symbol", "timeframe", "lookback_scale", "case_label", *DCA_AXES,
            "grid", *metric_keys, "decomposition_ok"]
    with open(path, "x", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def cost_for(grid):
    return {"fee_2x": {"fee_mult": 2.0}, "funding_2x": {"funding_mult": 2.0},
            "entry_delay_1_bar": {"entry_delay": 1}, "slippage_2ticks": {"slip_ticks": 2},
            "no_funding": {"no_funding": True}, "no_funding_full": {"no_funding": True},
            "cost_attrition_40bps": {"fee_override": 0.004}}.get(grid, {})


def phase_for(grid):
    if grid in ("historical", "no_funding"):
        return "historical"
    if grid == "oos":
        return "oos"
    return "full"


def window_indices(ms, bar_ms, start, end):
    """Split by BAR CLOSE time, so no bar's prices can cross a frozen boundary.

    A bar belongs to the split whose end its own close does not cross; the only
    grid where that matters is the weekly one (a week spans a month/split end),
    and there the straddling bar goes to the later split while the earlier split
    closes its still-open book at its own window edge (registered safety net).
    """
    closes = np.asarray(ms, np.int64) + int(bar_ms)
    lo, hi = utc_ms(start), utc_ms(end) + MS_DAY
    return (int(np.searchsorted(closes, lo, side="right")),
            int(np.searchsorted(closes, hi, side="right")))


# --------------------------------------------------------------------------- #
# Falsification battery (record items 1-7, all seven registered before compute) #
# --------------------------------------------------------------------------- #
def _status(flag):
    return "FALSIFIED" if flag else "NOT_FALSIFIED"


def falsification_report(signals, ic_series, reference_scores_by_month, random_report,
                         ls_returns, regime_report, windows, grid_rows):
    """Evaluate every registered record item on the pre-registered reference case."""
    ref_label = [c["label"] for c in CASES if c["lookback_scale"] == REFERENCE_SCALE][0]
    ref_ic = ic_series[ref_label]
    oos_lo = int(month_ordinals(np.array([utc_ms(PHASES["oos"][0])], np.int64))[0])
    oos_hi = int(month_ordinals(np.array([utc_ms(PHASES["oos"][1])], np.int64))[0])
    hist_lo = int(month_ordinals(np.array([utc_ms(PHASES["historical"][0])], np.int64))[0])
    hist_hi = int(month_ordinals(np.array([utc_ms(PHASES["historical"][1])], np.int64))[0])
    # formation month M is OOS when the month it predicts (M+1) lies in the OOS split
    oos_ic = [v for m, v in ref_ic.items() if oos_lo - 1 <= m <= oos_hi - 1]
    hist_ic = [v for m, v in ref_ic.items() if hist_lo <= m <= hist_hi]
    oos_sharpes = [float(r["sharpe"]) for r in grid_rows["oos"]
                   if r["case_label"] == ref_label and float(r["sharpe"]) != 0.0]
    mean_oos_ic = float(np.mean(oos_ic)) if oos_ic else None
    mean_oos_sharpe = float(np.mean(oos_sharpes)) if oos_sharpes else None
    items = {}

    # item 1 -----------------------------------------------------------------
    if not oos_ic or mean_oos_sharpe is None:
        items["oos_walk_forward_generalization"] = {
            "status": "INDETERMINATE", "reason": "no OOS Rank-IC / OOS Sharpe available"}
    else:
        ic_fail = mean_oos_ic < RANK_IC_FLOOR
        sh_fail = mean_oos_sharpe < SHARPE_FLOOR
        items["oos_walk_forward_generalization"] = {
            "status": _status(ic_fail or sh_fail), "reference_case": ref_label,
            "mean_oos_rank_ic": mean_oos_ic, "mean_oos_sharpe": mean_oos_sharpe,
            "oos_observations": len(oos_ic), "historical_mean_rank_ic":
                float(np.mean(hist_ic)) if hist_ic else None,
            "thresholds": "mean OOS Rank-IC < %s OR mean OOS Sharpe < %s falsifies"
                          % (RANK_IC_FLOOR, SHARPE_FLOOR),
            "cross_section_size": 4,
            "note": "Rank-IC is computed on a four-asset cross-section, so single-month ICs "
                    "are coarse; the record's numeric thresholds are applied unchanged"}

    # item 2 -----------------------------------------------------------------
    items["point_in_time_leakage_audit"] = {
        "status": "INDETERMINATE" if not PIT_RECHECK["checked"]
        else _status(bool(PIT_RECHECK["failed_samples"])),
        "pit_status": PIT_STATUS if not PIT_RECHECK["failed_samples"] else "LOOKAHEAD_DETECTED",
        **PIT_RECHECK,
        "selection": "lookback_scale is searched on the historical grid only; OOS is never "
                     "read for selection; the reference case is the source-specified scale 1.0",
        "action_if_falsified": "discard contaminated results and rerun from a clean split"}

    # item 3 -----------------------------------------------------------------
    perturb = {}
    base_mean = mean_oos_ic
    for label, series in ic_series.items():
        if label == ref_label:
            continue
        vals = [v for m, v in series.items() if oos_lo - 1 <= m <= oos_hi - 1]
        perturb[label] = {"mean_oos_rank_ic": float(np.mean(vals)) if vals else None,
                          "oos_observations": len(vals)}
    if base_mean is None or not perturb or any(v["mean_oos_rank_ic"] is None
                                               for v in perturb.values()):
        items["lookback_parameter_perturbation"] = {
            "status": "INDETERMINATE", "reason": "missing reference or perturbed OOS Rank-IC",
            "perturbations": perturb}
        perturb_fail = False
    else:
        fails = []
        for label, v in perturb.items():
            if abs(v["mean_oos_rank_ic"]) < RANK_IC_FLOOR:
                fails.append(label + ":|ic|<floor")
            if (v["mean_oos_rank_ic"] > 0) != (base_mean > 0):
                fails.append(label + ":sign_flip")
        perturb_fail = bool(fails)
        items["lookback_parameter_perturbation"] = {
            "status": _status(perturb_fail), "reference_case": ref_label,
            "reference_mean_oos_rank_ic": base_mean, "perturbations": perturb,
            "failed": fails,
            "threshold": "mean OOS Rank-IC < %s or a sign flip under +/-50%% falsifies"
                         % RANK_IC_FLOOR}

    # item 4 -----------------------------------------------------------------
    pooled = pooled_panel_correlations(signals[ref_label], reference_scores_by_month)
    if not pooled:
        items["reference_panel_novelty"] = {
            "status": "INDETERMINATE", "reason": "no pooled reference observations"}
        novelty_fail = False
    else:
        worst = max(pooled, key=lambda k: abs(pooled[k]))
        novelty_fail = abs(pooled[worst]) > NOVELTY_MAX_CORR
        items["reference_panel_novelty"] = {
            "status": _status(novelty_fail), "reference_panel": list(REFERENCE_PANEL),
            "panel_note": REFERENCE_PANEL_NOTE, "max_abs_correlation": pooled[worst],
            "worst_reference": worst, "correlations": pooled,
            "threshold": "|corr| > %s with any reference factor falsifies" % NOVELTY_MAX_CORR}

    # item 5 -----------------------------------------------------------------
    rand = random_report
    if rand.get("median_oos_rank_ic") is None or base_mean is None:
        items["llm_vs_random_search_ablation"] = {
            "status": "INDETERMINATE", "reason": "missing random-baseline or reference OOS "
                                                  "Rank-IC", **rand}
        random_fail = False
    else:
        random_fail = base_mean <= rand["median_oos_rank_ic"]
        items["llm_vs_random_search_ablation"] = {
            "status": _status(random_fail), "reference_case": ref_label,
            "reference_mean_oos_rank_ic": base_mean, **rand,
            "rule": RANDOM_ABLATION["rule"]}

    # item 6 -----------------------------------------------------------------
    cost_levels = {}
    gross_total = float(sum(ls_returns.values()))
    positive_gross = float(sum(v for v in ls_returns.values() if v > 0))
    for bps in COST_STRESS_BPS:
        cost_total = COST_STRESS_TURNOVER * (bps / 10_000.0) * len(ls_returns)
        denom = positive_gross if positive_gross > 0 else gross_total
        cost_levels["%dbps" % bps] = {"cost": cost_total,
                                      "consumption": (cost_total / denom) if denom > 0 else None}
    consumed = [v["consumption"] for v in cost_levels.values()]
    if positive_gross <= 0 or any(c is None for c in consumed):
        items["transaction_cost_stress"] = {
            "status": "INDETERMINATE", "reason": "non-positive long-short gross return",
            "levels": cost_levels, "gross_total": gross_total}
        cost_fail = False
    else:
        cost_fail = any(c > COST_STRESS_CONSUMPTION for c in consumed)
        items["transaction_cost_stress"] = {
            "status": _status(cost_fail), "levels": cost_levels,
            "gross_total": gross_total, "positive_gross_total": positive_gross,
            "turnover_per_rebalance": COST_STRESS_TURNOVER,
            "threshold": "consumption > %s at any registered level falsifies"
                         % COST_STRESS_CONSUMPTION,
            "action_if_falsified": "the signal is not net-alpha-viable at the monthly "
                                   "frequency as registered"}

    # item 7 -----------------------------------------------------------------
    regime = regime_report
    if len([r for r in regime["regimes"].values() if r["observations"] >= REGIME_MIN_OBS]) < 2:
        items["regime_subperiod_breakdown"] = {
            "status": "INDETERMINATE",
            "reason": "fewer than two regimes with >= %d observations" % REGIME_MIN_OBS,
            **regime}
        regime_fail = False
    else:
        positive = [k for k, r in regime["regimes"].items()
                    if r["observations"] >= REGIME_MIN_OBS and r["mean_ls_return"] is not None
                    and r["mean_ls_return"] > 0]
        regime_fail = len(positive) == 1
        items["regime_subperiod_breakdown"] = {
            "status": _status(regime_fail), **regime,
            "rule": "falsified when the mean monthly long-short return is positive in "
                    "exactly one regime among those with >= %d observations" % REGIME_MIN_OBS}

    hits = [k for k, v in items.items() if v.get("status") == "FALSIFIED"]
    indeterminate = [k for k, v in items.items() if v.get("status") == "INDETERMINATE"]
    return {"reference_case": ref_label, "items": items, "item_order": list(FALSIFICATION_REGISTRY),
            "oos_slice": {"first_formation_month": oos_lo - 1, "last_formation_month": oos_hi - 1,
                          "historical_first": hist_lo, "historical_last": hist_hi},
            "falsification_hits": hits, "indeterminate_items": indeterminate,
            "battery_status": "FALSIFIED" if hits else
            ("INDETERMINATE" if indeterminate else "NOT_FALSIFIED"),
            "registered_item_count": len(FALSIFICATION_REGISTRY),
            "scope": "signal-level items are evaluated on the reference case across the whole "
                     "registered window; item 1/3 use OOS formation months; the DCA grid "
                     "supplies the OOS Sharpe input"}


def pooled_panel_correlations(signal, reference_scores_by_month):
    """Pooled (month x symbol) Spearman between the factor score and each reference."""
    out = {}
    for name, series in reference_scores_by_month.items():
        xs, ys = [], []
        for month, score in signal["scores"].items():
            ref = series.get(month)
            if not ref:
                continue
            xs.extend([score[s] for s in SYMBOLS])
            ys.extend([ref[s] for s in SYMBOLS])
        value = spearman(xs, ys) if len(xs) >= 4 else None
        if value is not None:
            out[name] = float(value)
    return out


def random_formula_report(reference_by_symbol, signal, ic_series, start, end):
    """Record item 5: a seeded random rank-product sampler of similar complexity."""
    rng = np.random.default_rng(RANDOM_ABLATION["seed"])
    pool = list(RANDOM_ABLATION["components"])
    lo, hi = int(RANDOM_ABLATION["components_per_formula"][0]), \
        int(RANDOM_ABLATION["components_per_formula"][1])
    ref_label = [c["label"] for c in CASES if c["lookback_scale"] == REFERENCE_SCALE][0]
    oos_lo = int(month_ordinals(np.array([utc_ms(PHASES["oos"][0])], np.int64))[0])
    oos_hi = int(month_ordinals(np.array([utc_ms(PHASES["oos"][1])], np.int64))[0])
    formation_months = sorted(signal["scores"])
    means = []
    formulas = []
    for k in range(int(RANDOM_ABLATION["formulas"])):
        n = int(rng.integers(lo, hi + 1))
        idx = rng.choice(len(pool), size=n, replace=False)
        signs = rng.integers(0, 2, size=n) * 2 - 1
        chosen = [pool[i] for i in idx]
        formulas.append({"components": chosen,
                         "orientation": ["rank" if s > 0 else "1-rank" for s in signs]})
        ics = []
        for month in formation_months:
            if not (oos_lo - 1 <= month <= oos_hi - 1):
                continue
            vec = []
            for s in SYMBOLS:
                vals = []
                ok = True
                for comp, sign in zip(chosen, signs):
                    raw = reference_by_symbol[s].get(comp)
                    if raw is None:
                        ok = False
                        break
                    idx_map = _REFERENCE_INDEX[s]
                    v = raw[idx_map[month]] if month in idx_map else None
                    if v is None or not np.isfinite(v):
                        ok = False
                        break
                    vals.append(v * sign)
                if not ok:
                    continue
                vec.append((s, vals))
            if len(vec) != len(SYMBOLS):
                continue
            # rank each oriented component across the cross-section, then product
            score = {}
            per_comp = []
            for comp_i in range(n):
                column = np.array([v[comp_i] for _, v in vec], np.float64)
                per_comp.append(average_rank_percentile(column))
            for j, (sym, _) in enumerate(vec):
                score[sym] = float(np.prod([per_comp[c][j] for c in range(n)]))
            realised = _NEXT_RET.get(month)
            if realised is None:
                continue
            ic = spearman(np.array([score[s] for s in SYMBOLS]),
                          np.array([realised[s] for s in SYMBOLS]))
            if ic is not None:
                ics.append(ic)
        if ics:
            means.append(float(np.mean(ics)))
    ref_oos = [v for m, v in ic_series[ref_label].items() if oos_lo - 1 <= m <= oos_hi - 1]
    return {"formulas_requested": int(RANDOM_ABLATION["formulas"]),
            "formulas_evaluated": len(means), "formulas": formulas,
            "mean_oos_rank_ic_of_formulas": means,
            "median_oos_rank_ic": float(np.median(means)) if means else None,
            "max_oos_rank_ic": float(np.max(means)) if means else None,
            "reference_mean_oos_rank_ic": float(np.mean(ref_oos)) if ref_oos else None,
            "seed": RANDOM_ABLATION["seed"], "rule": RANDOM_ABLATION["rule"]}


def regime_report(signal, daily_by_symbol):
    """Record item 7: BTCUSDT YoY regimes (bull >= +15%, bear <= -15%, else flat)."""
    ref = daily_by_symbol["BTCUSDT"]
    close = ref["close"]
    yoy = np.full(len(close), np.nan)
    yoy[365:] = close[365:] / np.maximum(close[:-365], 1e-12) - 1.0
    f_idx, f_months = formation_indices(ref["open_ms"])
    yoy_at = {int(m): float(yoy[i]) for i, m in zip(f_idx, f_months) if np.isfinite(yoy[i])}
    ls = long_short_returns(signal, daily_by_symbol)
    buckets = {"bull": [], "bear": [], "flat": []}
    for month, value in ls.items():
        y = yoy_at.get(month)
        if y is None:
            continue
        key = "bull" if y >= REGIME_YOY else ("bear" if y <= -REGIME_YOY else "flat")
        buckets[key].append(value)
    return {"definition": "BTCUSDT daily close YoY at the formation month; bull >= +%.0f%%, "
                          "bear <= -%.0f%%, else flat (research-defined local analogue of the "
                          "record's SPX +/-15%% YoY regimes)" % (REGIME_YOY * 100, REGIME_YOY * 100),
            "regimes": {k: {"observations": len(v),
                            "mean_ls_return": float(np.mean(v)) if v else None}
                        for k, v in buckets.items()}}


_REFERENCE_INDEX = {}
_NEXT_RET = {}
PIT_RECHECK = {"checked": 0, "failed_samples": 0, "samples": []}


# --------------------------------------------------------------------------- #
# Cohort computation                                                           #
# --------------------------------------------------------------------------- #
COHORT_STATE = {}


def run_cohort(task):
    """All registered cells of one (timeframe, symbol) cohort.  Pure: returns rows."""
    tf_index, symbol = task
    tf = TIMEFRAMES[tf_index]
    panel = COHORT_STATE["panels"][(symbol, tf["raw_interval"])]
    funding = COHORT_STATE["funding"][(symbol, tf["raw_interval"])]
    ep_start, ep_end = COHORT_STATE["episodes"][tf_index]
    windows = COHORT_STATE["windows"][tf_index]
    inst = COHORT_STATE["instruments"][symbol]
    rows = {g: [] for g in GRIDS}
    hist_full = [0] * LADDER_LEVELS
    for case in CASES:
        direction = COHORT_STATE["directions"][(case["label"], symbol, tf_index)]
        for grid in GRIDS:
            phase = phase_for(grid)
            i0, i1 = windows[phase]
            for dca in DCA_GRID:
                metric = simulate(panel, funding, direction, ep_start, ep_end,
                                  dca, i0, i1, cost_for(grid), inst)
                rows[grid].append({"symbol": symbol, "timeframe": tf["raw_interval"],
                                   "lookback_scale": case["lookback_scale"],
                                   "case_label": case["label"], **dca, "grid": grid,
                                   **metric_block(metric),
                                   "decomposition_ok": metric["decomposition_ok"]})
                if grid == "full":
                    for level, value in enumerate(metric["layer_hist"]):
                        hist_full[level] += value
    return {"symbol": symbol, "timeframe": tf["raw_interval"], "rows": rows,
            "layer_hist": hist_full}


# --------------------------------------------------------------------------- #
# Spec templates                                                               #
# --------------------------------------------------------------------------- #
def run_spec_template(created_at=None):
    here = Path(__file__).resolve()
    return {
        "schema_version": 1, "document_kind": "run_spec",
        "contract": "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md v2.0.0",
        "family_id": FAMILY_ID, "round_id": "<family_id>-r1", "run_id": "<round_id>-u1",
        "created_at_utc": created_at or "<ISO8601Z>", "container_id": "qlib-run",
        "image_id": "qlib:0.9.7-arm64",
        "ownership_mode": "direct_hermes",
        "ownership_note": "direct family: family_id + round_id + run_id only; no Kanban "
                          "ownership key exists for this round and none may be written",
        "script": {"path": "/scripts/320_aeap_seads_run.py",
                   "sha256": sha256_file(here),
                   "deployed_host_path": "/Users/hong/workspace/qlib-apple-container/"
                                         "scripts/320_aeap_seads_run.py"},
        "engine": {"name": ENGINE_VERSION, "script": "container/scripts/320_aeap_seads_run.py",
                   "container": "qlib-run", "qlib_version": "0.9.7", "seed": SEED,
                   "self_check": "container/scripts/tests/test_aeap_seads_engine.py",
                   "self_check_sha256": sha256_file(
                       here.with_name("tests") / "test_aeap_seads_engine.py")},
        "data": {"raw_root": "/data/raw/binance/usdm", "start": PHASES["full"][0],
                 "end": PHASES["full"][1], "timezone": "UTC", "symbols": list(SYMBOLS),
                 "fields": list(FIELDS), "signal_interval": SIGNAL_INTERVAL,
                 "signal_source": "Qlib 0.9.7 dump/readback of the canonical daily panel",
                 "timeframes": [dict(tf) for tf in TIMEFRAMES]},
        "split": {"historical_start": PHASES["historical"][0],
                  "historical_end": PHASES["historical"][1],
                  "oos_start": PHASES["oos"][0], "oos_end": PHASES["oos"][1],
                  "rule": "chronological; frozen before compute; OOS never used for selection"},
        "params": [dict(c) for c in CASES],
        "dca_domain": {**{k: list(v) for k, v in DCA_AXES.items()},
                       "grid": [dict(d) for d in DCA_GRID], "base_quote": BASE_QUOTE,
                       "base_quote_status": "PROJECT_PRE_REGISTERED_CONSTANT",
                       **{k + "_status": "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN"
                          for k in DCA_AXES}},
        "selector_version": SELECTOR_VERSION, "disposition_version": DISPOSITION_VERSION,
        "expected": expected_counts(),
        "gates": {"min_episodes_is": MIN_EPISODES_IS, "min_episodes_oos": MIN_EPISODES_OOS,
                  "min_neighbour_same_sign_fraction": MIN_NEIGHBOUR},
        "signal_constants": signal_constants(),
        "costs": {"taker_fee": "canonical instrument taker_fee, per fill",
                  "slippage_ticks": 1, "slippage_robustness_ticks": 2,
                  "funding": "official observations only at timestamp/mark; missing intervals "
                             "zero and disclosed",
                  "accounting": "per-fill fee and funding deducted at the fill's own time; "
                                "net/ending_equity/daily marks/Sharpe are net-of-fee (v1.3.1)"},
        "falsification": list(FALSIFICATION), "expected_outputs": list(ARTIFACTS),
        "notes": "direct family: family_id + round_id + run_id only; no Kanban ownership keys."}


def round_spec_template():
    """Round-spec skeleton; the host authoring step fills provenance/excerpts verbatim."""
    fam_path = Path(RESULTS_ROOT) / FAMILY_ID / "family.json"
    family_sha = sha256_file(fam_path) if fam_path.is_file() else None
    return {
        "schema_version": 1, "document_kind": "round_spec",
        "contract": "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md v2.0.0",
        "family_id": FAMILY_ID, "round_id": FAMILY_ID + "-r1", "created_at_utc": now_utc(),
        "ownership_mode": "direct_hermes",
        "ownership_note": "direct family: ownership is family_id + round_id + run_id on the "
                          "fixed results path. No Kanban card, board, dispatcher or task id "
                          "exists for this round and none may be written into any spec or "
                          "evidence file.",
        "provenance": {"source_record": "wiki brain quant/%s.md" % FAMILY_ID,
                       "family_json_sha256": family_sha,
                       "family_handoff_execution": "direct_hermes"},
        "parameter_contract": parameter_contract(),
        "params": [dict(c) for c in CASES],
        "parameter_domain": {"strategy_axes": list(STRATEGY_AXES),
                             "legal_cases_per_cohort": len(CASES),
                             "cases": [dict(c) for c in CASES]},
        "dca_domain": {**{k: list(v) for k, v in DCA_AXES.items()},
                       "grid": [dict(d) for d in DCA_GRID], "base_quote": BASE_QUOTE,
                       "config_count": len(DCA_GRID),
                       "base_quote_status": "PROJECT_PRE_REGISTERED_CONSTANT",
                       **{k + "_status": "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN"
                          for k in DCA_AXES}},
        "selector_disposition": {"selector_version": SELECTOR_VERSION,
                                 "disposition_version": DISPOSITION_VERSION,
                                 "disposition_mapping_version": DISPOSITION_MAPPING_VERSION,
                                 "gates": {"min_episodes_is": MIN_EPISODES_IS,
                                           "min_episodes_oos": MIN_EPISODES_OOS,
                                           "min_neighbour_same_sign_fraction": MIN_NEIGHBOUR}},
        "grids": list(GRIDS),
        "coverage_gate_g1": {"product": "each registered grid must contain exactly "
                                        "cohorts x strategy cases x 48 DCA configs",
                             "expected": expected_counts()},
        "robustness": {"standard_stress_grids": list(STRESS_GRIDS),
                       "additional_grids": ["no_funding", "no_funding_full",
                                            "cost_attrition_40bps"]},
        "falsification": list(FALSIFICATION),
        "engine": {"name": ENGINE_VERSION,
                   "script": {"repo_path": "container/scripts/320_aeap_seads_run.py",
                              "container_path": "/scripts/320_aeap_seads_run.py",
                              "deployed_host_path": "/Users/hong/workspace/qlib-apple-container/"
                                                    "scripts/320_aeap_seads_run.py",
                              "sha256": sha256_file(Path(__file__).resolve())},
                   "self_check": {"repo_path": "container/scripts/tests/"
                                               "test_aeap_seads_engine.py",
                                  "container_path": "/scripts/tests/test_aeap_seads_engine.py",
                                  "sha256": sha256_file(Path(__file__).resolve().with_name(
                                      "tests") / "test_aeap_seads_engine.py")},
                   "container": "qlib-run", "image": "qlib:0.9.7-arm64",
                   "qlib_version": "0.9.7", "seed": SEED},
        "data": {"raw_root": "/data/raw/binance/usdm", "start": PHASES["full"][0],
                 "end": PHASES["full"][1], "timezone": "UTC", "symbols": list(SYMBOLS),
                 "fields": list(FIELDS), "signal_interval": SIGNAL_INTERVAL,
                 "signal_source": "Qlib 0.9.7 dump/readback of the canonical daily panel",
                 "cohorts": ["%s/%s" % (s, tf["raw_interval"])
                             for tf in TIMEFRAMES for s in SYMBOLS],
                 "timeframes": [dict(tf) for tf in TIMEFRAMES],
                 "point_in_time": "canonical raw snapshot; components at each formation read "
                                  "daily bars at or before the formation bar; no bar after "
                                  "the registered end",
                 "missing_data": "fail closed on a non-contiguous bar grid at every registered "
                                 "frequency; no gap fill, no resampling"},
        "split": {"historical_start": PHASES["historical"][0],
                  "historical_end": PHASES["historical"][1],
                  "oos_start": PHASES["oos"][0], "oos_end": PHASES["oos"][1],
                  "rule": "chronological; frozen before compute; OOS never used for selection"},
        "signal_constants": signal_constants(),
        "expected": expected_counts(), "expected_outputs": list(ARTIFACTS),
        "notes": "direct family: family_id + round_id + run_id only; no Kanban ownership keys "
                 "in this spec, in run-spec.json, or in any evidence file."}


# --------------------------------------------------------------------------- #
# Main run                                                                     #
# --------------------------------------------------------------------------- #
def run(spec, attempt_dir, log_path=None, smoke=False, phases=None):
    phases = phases or PHASES
    counts = validate_spec(spec)
    path = Path(attempt_dir)
    if not smoke:
        expected = (Path(RESULTS_ROOT) / FAMILY_ID / "rounds" / spec["round_id"]
                    / "attempts" / spec["run_id"])
        if path != expected or any((path / x).exists()
                                   for x in ("DONE", "FAILED", "INCOMPLETE", "result.json",
                                             "state.json")):
            raise ValueError("attempt path/immutability violation")
    artifacts, logs = path / "artifacts", path / "logs"
    artifacts.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    log_fh = open(log_path or (logs / "run.log"), "w", encoding="utf-8")

    def log(msg):
        line = "[%s] %s" % (dt.datetime.now(dt.timezone.utc).isoformat(), msg)
        print(line, flush=True)
        log_fh.write(line + "\n")
        log_fh.flush()

    state = {"schema_version": 1, "family_id": FAMILY_ID, "round_id": spec["round_id"],
             "run_id": spec["run_id"], "stage": "RUNNING_QLIB"}
    atomic_json(path / "state.json", state)
    progress = {"schema_version": 1, "family_id": FAMILY_ID, "round_id": spec["round_id"],
                "run_id": spec["run_id"], "stage": "RUNNING_QLIB", "cohorts_done": 0,
                "case_evaluations": 0, "cohorts_total": len(SYMBOLS) * len(TIMEFRAMES),
                "expected_case_evaluations": counts["case_evaluations_total"]}

    def write_progress():
        progress["updated_at_utc"] = now_utc()
        progress["runtime_seconds"] = round(time.monotonic() - started, 3)
        atomic_json(artifacts / "progress.json", progress)

    write_progress()
    global PIT_RECHECK, _REFERENCE_INDEX, _NEXT_RET
    try:
        inst = instrument_metadata()
        log("instrument metadata ok for %s" % ", ".join(SYMBOLS))

        # ---- Qlib build + readback (production data source) ---------------- #
        build_started = time.monotonic()
        # the DATA window is the registered raw window in the spec; `phases` only
        # bounds the simulation, so a shortened (smoke) run still gets full history
        data_start, data_end = spec["data"]["start"], spec["data"]["end"]
        panels, build = build_qlib_and_readback(data_start, data_end, log)
        atomic_json(artifacts / "qlib_readback.json", build)
        log("qlib readback ok (%d datasets) in %.1fs"
            % (len(build["datasets"]), time.monotonic() - build_started))
        daily = {sym: panels[(sym, SIGNAL_INTERVAL)] for sym in SYMBOLS}

        # ---- funding (official observations only) -------------------------- #
        funding_events, funding_report = {}, {}
        for sym in SYMBOLS:
            times, amounts, report = load_funding_events(sym, data_start, data_end)
            funding_events[sym] = (times, amounts)
            per_tf = {}
            for tf_index, tf in enumerate(TIMEFRAMES):
                open_ms = panels[(sym, tf["raw_interval"])]["open_ms"]
                events, outside = bucket_funding(times, amounts, open_ms)
                funding_events[(sym, tf["raw_interval"])] = events
                per_tf[tf["raw_interval"]] = {"bars": len(events),
                                              "charged": sum(len(b) for b in events),
                                              "outside_bars": outside}
            report["by_timeframe"] = per_tf
            report["charged_total"] = sum(v["charged"] for v in per_tf.values())
            report["charged_total_scope"] = (
                "sum over the 7 execution timeframes: the same official observations "
                "bucketed once per timeframe (each timeframe's charged == official - "
                "outside_bars); it is not 7x observations")
            funding_report[sym] = report
            del funding_events[sym]
        atomic_json(artifacts / "funding_coverage.json", funding_report)
        log("funding loaded: %s"
            % {s: {"official": funding_report[s]["official"],
                   "charged_per_timeframe":
                       funding_report[s]["by_timeframe"][TIMEFRAMES[0]["raw_interval"]]["charged"],
                   "modeled_ignored": funding_report[s]["modeled_ignored"]}
               for s in SYMBOLS})

        # ---- factor layer -------------------------------------------------- #
        signals, ic_series = {}, {}
        for case in CASES:
            signals[case["label"]] = build_case_signal(case, daily, log=log)
            ic_series[case["label"]], skipped = rank_ic_series(signals[case["label"]], daily)
            if skipped:
                log("rank-ic skipped %d constant-cross-section months for %s"
                    % (len(skipped), case["label"]))
        ref_case = next(c for c in CASES if c["lookback_scale"] == REFERENCE_SCALE)
        PIT_RECHECK = pit_recheck(ref_case, daily, signals[ref_case["label"]])
        log("PIT suffix recheck: checked=%d failed=%d"
            % (PIT_RECHECK["checked"], PIT_RECHECK["failed_samples"]))

        reference_by_symbol = {sym: reference_components(daily[sym]) for sym in SYMBOLS}
        ref_ts = daily[SYMBOLS[0]]["open_ms"]
        f_idx, f_months = formation_indices(ref_ts)
        _REFERENCE_INDEX = {sym: {int(m): int(i) for i, m in zip(f_idx, f_months)}
                            for sym in SYMBOLS}
        reference_scores_by_month = reference_panel_scores(reference_by_symbol, ref_ts)
        ls_returns = long_short_returns(signals[ref_case["label"]], daily)
        regime = regime_report(signals[ref_case["label"]], daily)
        random_report = random_formula_report(reference_by_symbol, signals[ref_case["label"]],
                                              ic_series, phases["full"][0], phases["full"][1])
        _NEXT_RET, _ = next_month_returns(daily)
        log("factor layer ok: formations=%d random_formulas=%d"
            % (len(signals[ref_case["label"]]["formations"]),
               random_report["formulas_evaluated"]))

        # ---- cohort bookkeeping -------------------------------------------- #
        COHORT_STATE["panels"] = panels
        COHORT_STATE["funding"] = funding_events
        COHORT_STATE["instruments"] = inst
        episodes, windows_by_tf, directions = {}, {}, {}
        for tf_index, tf in enumerate(TIMEFRAMES):
            open_ms = panels[(SYMBOLS[0], tf["raw_interval"])]["open_ms"]
            for sym in SYMBOLS:
                if not np.array_equal(panels[(sym, tf["raw_interval"])]["open_ms"], open_ms):
                    raise RuntimeError("cross-sectional timestamps do not align: %s/%s"
                                       % (sym, tf["raw_interval"]))
            ep_start, ep_end, _months = episode_bounds(open_ms)
            episodes[tf_index] = (ep_start, ep_end)
            windows_by_tf[tf_index] = {p: window_indices(open_ms,
                                                         BAR_MS[tf["raw_interval"]],
                                                         *phases[p])
                                       for p in ("historical", "oos", "full")}
            h = windows_by_tf[tf_index]["historical"]
            o = windows_by_tf[tf_index]["oos"]
            f = windows_by_tf[tf_index]["full"]
            if h[1] != o[0] or f != (h[0], o[1]):
                raise RuntimeError("non-contiguous split for %s" % tf["raw_interval"])
            for case in CASES:
                for sym in SYMBOLS:
                    directions[(case["label"], sym, tf_index)] = direction_for_symbol(
                        signals[case["label"]], panels[(sym, tf["raw_interval"])]["open_ms"], sym)
        COHORT_STATE["episodes"] = episodes
        COHORT_STATE["windows"] = windows_by_tf
        COHORT_STATE["directions"] = directions

        # ---- grid search ---------------------------------------------------- #
        grid_rows = {g: [] for g in GRIDS}
        results, survivors = [], []
        layer_hist_total = [0] * LADDER_LEVELS
        tasks = [(tf_index, sym) for tf_index in range(len(TIMEFRAMES)) for sym in SYMBOLS]
        last_progress = time.monotonic()
        for task in tasks:
            out = run_cohort(task)
            for g in GRIDS:
                grid_rows[g].extend(out["rows"][g])
            for level, value in enumerate(out["layer_hist"]):
                layer_hist_total[level] += value
            progress["cohorts_done"] += 1
            progress["case_evaluations"] += len(CASES) * len(DCA_GRID) * len(GRIDS)
            write_progress()
            last_progress = time.monotonic()
            log("cohort %s/%s done (%d cells cumulative)"
                % (out["symbol"], out["timeframe"], progress["case_evaluations"]))
        # ---- selector -------------------------------------------------------- #
        for tf in TIMEFRAMES:
            for sym in SYMBOLS:
                rows = {g: [r for r in grid_rows[g]
                            if r["symbol"] == sym and r["timeframe"] == tf["raw_interval"]]
                        for g in GRIDS}
                selected, info = select_cohort(rows)
                record = {"cohort": "%s/%s" % (sym, tf["raw_interval"]), "symbol": sym,
                          "timeframe": tf["raw_interval"],
                          "outcome": "SURVIVOR" if selected is not None else "CULLED", **info}
                results.append(record)
                if selected is not None:
                    survivors.append(record)
                log("cohort %s outcome=%s cull=%s"
                    % (record["cohort"], record["outcome"],
                       ",".join(info.get("cull_reasons", [])) or "none"))

        expected_keys = {(sym, tf["raw_interval"], c["lookback_scale"],
                          *(d[k] for k in DCA_AXES))
                         for tf in TIMEFRAMES for sym in SYMBOLS
                         for c in CASES for d in DCA_GRID}
        coverage = {g: {cell_key(r) for r in grid_rows[g]} == expected_keys
                    and len(grid_rows[g]) == len(expected_keys) for g in GRIDS}
        base_full = sum(float(r["net_pnl"]) for r in grid_rows["full"])
        stress_delta = {g: sum(float(r["net_pnl"]) for r in grid_rows[g]) - base_full
                        for g in ("fee_2x", "funding_2x", "slippage_2ticks",
                                  "cost_attrition_40bps")}
        delay_delta = (sum(float(r["net_pnl"]) for r in grid_rows["entry_delay_1_bar"])
                       - base_full)
        traded = any(float(r["turnover_usdt"]) > 0 for r in grid_rows["full"])

        def cell_delta(a, b, tol=1e-9):
            return any(abs(float(x["net_pnl"]) - float(y["net_pnl"])) > tol
                       for x, y in zip(grid_rows[a], grid_rows[b]))

        cells_with_funding = [i for i, r in enumerate(grid_rows["full"])
                              if abs(float(r["funding"])) > 1e-9]

        # ---- falsification --------------------------------------------------- #
        falsification = falsification_report(signals, ic_series, reference_scores_by_month,
                                             random_report, ls_returns, regime,
                                             windows_by_tf, grid_rows)
        atomic_json(artifacts / "falsification.json", falsification)
        for rec in survivors:
            rec["falsification_battery"] = {k: v.get("status")
                                            for k, v in falsification["items"].items()}

        signal_report = {}
        for case in CASES:
            sig = signals[case["label"]]
            ics = list(ic_series[case["label"]].values())
            dirs = list(sig["directions"].values())
            signal_report[case["label"]] = {
                "lookback_scale": case["lookback_scale"],
                "lookback_days": {"bidaskhl_days": case["bidaskhl_days"],
                                  "zero_dolvol_days": case["zero_dolvol_days"],
                                  "trail_mean_days": case["trail_mean_days"]},
                "formation_months": len(sig["formations"]),
                "warmup_skipped_months": len(sig["skipped_months"]),
                "cross_section_size": 4,
                "coverage": 1.0,
                "long_months": sum(1 for d in dirs if any(v > 0 for v in d.values())),
                "short_months": sum(1 for d in dirs if any(v < 0 for v in d.values())),
                "flat_months": sum(1 for d in dirs if not any(v != 0 for v in d.values())),
                "rank_ic_defined": len(ics),
                "mean_rank_ic": float(np.mean(ics)) if ics else None,
                "degenerate": not any(any(v != 0 for v in d.values()) for d in dirs),
                "pit": "components read daily bars with open_time <= formation bar; tranche #1 "
                       "fills at the next month's first bar open"}
        signal_report["_reference_case"] = ref_case["label"]
        signal_report["_pit_recheck"] = PIT_RECHECK
        signal_report["_direction_rule"] = signal_constants()["direction_rule"]
        signal_report["_zero_volume_days"] = zero_volume_day_counts(daily)
        atomic_json(artifacts / "signal_metrics.json", signal_report)

        assertions = {
            "qlib_readback_0_9_7": build["qlib_version"] == "0.9.7"
            and all(v["matches_raw"] for v in build["datasets"].values()),
            "cross_section_complete_every_formation": all(
                f["cross_section"] == len(SYMBOLS) and f["coverage"] == 1.0
                for case in CASES for f in signals[case["label"]]["formations"]),
            "coverage_complete": all(coverage.values()),
            "case_evaluations_total_exact":
                sum(map(len, grid_rows.values())) == counts["case_evaluations_total"],
            "independent_gross_net_decomposition":
                all(r["decomposition_ok"] for g in GRIDS for r in grid_rows[g]),
            "dca_histogram_reconciles": layer_hist_total[0] == sum(int(r["episodes"])
                                                                   for r in grid_rows["full"]),
            "cost_stress_effective": (not traded) or all(
                cell_delta("full", g) for g in ("fee_2x", "slippage_2ticks",
                                                "cost_attrition_40bps")),
            "funding_2x_effective": not cells_with_funding or all(
                abs(float(grid_rows["funding_2x"][i]["net_pnl"])
                    - float(grid_rows["full"][i]["net_pnl"])) > 1e-9
                for i in cells_with_funding),
            "entry_delay_1_bar_effective": (not traded) or cell_delta("entry_delay_1_bar",
                                                                      "full"),
            "official_funding_only_no_modeled_charges": all(
                funding_report[s]["modeled_ignored"] >= 0
                and funding_report[s]["missing_intervals_are_zero_not_modeled"] is True
                and all(v["charged"] == funding_report[s]["official"] - v["outside_bars"]
                        for v in funding_report[s]["by_timeframe"].values())
                and funding_report[s]["charged_total"]
                == sum(v["charged"] for v in funding_report[s]["by_timeframe"].values())
                for s in SYMBOLS),
            "point_in_time_no_lookahead":
                PIT_RECHECK["checked"] >= 3 and PIT_RECHECK["failed_samples"] == 0,
            "signal_supply_nonzero_every_case": not any(
                signal_report[c["label"]]["degenerate"] for c in CASES),
            "registered_signal_constants_match":
                LOOKBACK_SCALES == (0.5, 1.0, 2.0)
                and lookback_days(1.0) == (21, 126, 365)
                and BASE_LOOKBACKS == {"bidaskhl_days": 21, "zero_dolvol_days": 126,
                                       "trail_mean_days": 365}
                and len(DCA_GRID) == 48 and BASE_QUOTE == 1000.0
                and START_EQUITY == 30_000.0 and MAX_ADD_LEVELS == 10
                and LADDER_LEVELS == 12,
            "falsification_battery_evaluated":
                sorted(falsification["items"]) == sorted(FALSIFICATION_REGISTRY)
                and all(v.get("status") in ("FALSIFIED", "NOT_FALSIFIED", "INDETERMINATE")
                        for v in falsification["items"].values())
                and falsification["registered_item_count"] == 7,
            "selector_winner_is_historical_row":
                all(x.get("winner_source_grid") == "historical"
                    for x in results if x.get("winner")),
            "monthly_episode_exit_only": all(
                int(r["open_at_end"]) == 0
                and int(r["month_exits"]) + int(r["stop_hits"]) + int(r["tp_hits"])
                + int(r["margin_calls"]) + int(r["end_exits"]) + int(r["signal_flat_exits"])
                >= int(r["episodes"])
                for r in grid_rows["full"]),
        }
        atomic_json(artifacts / "stress_effects.json", {
            "baseline_grid": "full", "net_pnl_delta": {**stress_delta,
                                                       "entry_delay_1_bar": delay_delta},
            "any_fill_in_full_grid": traded,
            "noop_reason": None if traded else "no fills in full grid"})
        atomic_json(artifacts / "cohort_results.json", results)
        atomic_json(artifacts / "cohort_survivors.json", survivors)
        atomic_json(artifacts / "dca_layer_histogram.json",
                    {"level_%02d" % i: v for i, v in enumerate(layer_hist_total)})
        for g in GRIDS:
            write_grid(artifacts / ("grid_%s.csv" % g), grid_rows[g])
        atomic_json(artifacts / "assertions.json", assertions)

        hits = falsification.get("falsification_hits", [])
        verdict = "REJECT" if not survivors else "PASS"
        result = {"schema_version": 1, "family_id": FAMILY_ID, "round_id": spec["round_id"],
                  "run_id": spec["run_id"], "engine": ENGINE_VERSION,
                  "script_sha256": sha256_file(__file__), "qlib_version": build["qlib_version"],
                  "status": "ARTIFACT_READY", "coverage_complete": all(coverage.values()),
                  "coverage_by_grid": coverage,
                  "assertions_all_true": all(v for v in assertions.values()),
                  "assertion_failures": [k for k, v in assertions.items() if not v],
                  "assertions": assertions,
                  "case_evaluations_total": sum(map(len, grid_rows.values())),
                  "expected_case_evaluations": counts["case_evaluations_total"],
                  "cohort_count": len(results), "cohort_survivor_count": len(survivors),
                  "cohort_survivors": [x["cohort"] for x in survivors],
                  "official_funding_events": {s: funding_report[s]["official"]
                                              for s in SYMBOLS},
                  "disposition": "REJECT / NO_SURVIVOR" if not survivors else
                  ("SURVIVOR_FOUND" if len(survivors) == 1 else "MULTIPLE_SURVIVORS"),
                  "falsification_hits": hits,
                  "falsification_indeterminate": falsification.get("indeterminate_items", []),
                  "falsification_advice": {"rule": "record falsification battery hit => cull "
                                                   "or family REJECT (body FAILURE TAXONOMY); "
                                                   "verdict_recommendation is the v1.4.0 "
                                                   "disposition band only",
                                           "battery_status": falsification.get("battery_status"),
                                           "advice": "REJECT" if hits else None},
                  "verdict_recommendation": verdict,
                  "verdict_recommendation_basis":
                      "disposition band %s (0 survivors = REJECT, >=1 = PASS); the final "
                      "verdict is written to verdict.json by default (10.7)"
                      % DISPOSITION_MAPPING_VERSION,
                  "pit_status": PIT_STATUS,
                  "claimability_evidence": {
                      "basis": "performance_claimable is decided in verdict.json under "
                               "contract 9.6, not recommended here",
                      "missing_conditions": [],
                      "pit_note": "every component reads daily bars at or before its formation "
                                  "bar; tranche #1 fills at the next month's first bar open; "
                                  "in-run suffix recheck + self-check perturbation both gate this"},
                  "selector_version": SELECTOR_VERSION,
                  "disposition_version": DISPOSITION_VERSION,
                  "disposition_mapping_version": DISPOSITION_MAPPING_VERSION,
                  "grid_kinds": GRIDS,
                  "funding_coverage": {s: {k: funding_report[s][k] for k in
                                           ("official", "charged_total", "modeled_ignored",
                                            "missing_intervals_are_zero_not_modeled")}
                                       for s in SYMBOLS},
                  "stress_net_delta": stress_delta,
                  "signal_layer_report": {k: v for k, v in signal_report.items()
                                          if not k.startswith("_")},
                  "falsification_summary": {k: v.get("status")
                                             for k, v in falsification["items"].items()},
                  "runtime_seconds": time.monotonic() - started}
        if result["case_evaluations_total"] != counts["case_evaluations_total"]:
            result["assertions_all_true"] = False
            result["assertion_failures"].append("case_evaluations_total")
        if not result["assertions_all_true"]:
            result["verdict_recommendation"] = "TECHNICAL_INCOMPLETE"
        atomic_json(path / "result.json", result)
        state["stage"] = "ARTIFACT_READY"
        atomic_json(path / "state.json", state)
        progress["stage"] = "ARTIFACT_READY"
        write_progress()
        log("ARTIFACT_READY cohorts=%d survivors=%d rows=%d falsification=%s assertions=%s "
            "runtime=%.1fs" % (len(results), len(survivors),
                               result["case_evaluations_total"], ",".join(hits) or "none",
                               "all_true" if result["assertions_all_true"]
                               else str(result["assertion_failures"]),
                               time.monotonic() - started))
        return result
    except Exception as exc:
        state.update(stage="FAILED_SCRIPT", error="%s: %s" % (type(exc).__name__, exc))
        atomic_json(path / "state.json", state)
        progress["stage"] = "FAILED_SCRIPT"
        write_progress()
        log("FAILED %s: %s" % (type(exc).__name__, exc))
        raise
    finally:
        log_fh.close()


def reference_panel_scores(reference_by_symbol, ref_ts):
    f_idx, f_months = formation_indices(ref_ts)
    out = {name: {} for name in REFERENCE_PANEL}
    for i, month in zip(f_idx, f_months):
        for name in REFERENCE_PANEL:
            vec = np.array([reference_by_symbol[s][name][i] for s in SYMBOLS], np.float64)
            if not np.isfinite(vec).all():
                continue
            pct = average_rank_percentile(vec)
            out[name][int(month)] = {s: float(pct[j]) for j, s in enumerate(SYMBOLS)}
    return out


def zero_volume_day_counts(daily):
    """Data-property disclosure for the zero-trades component (not a result)."""
    out = {}
    for sym in SYMBOLS:
        v = daily[sym]["volume"]
        out[sym] = int(np.sum(v <= 0.0))
    return out


# --------------------------------------------------------------------------- #
# Entry points                                                                 #
# --------------------------------------------------------------------------- #
def emit_template(kind):
    if kind == "run-spec":
        doc = run_spec_template(created_at=now_utc())
        doc["round_id"] = FAMILY_ID + "-r1"
        doc["run_id"] = doc["round_id"] + "-u1"
    elif kind == "round-spec":
        doc = round_spec_template()
    else:
        raise SystemExit("unknown template kind: %s" % kind)
    print(json.dumps(doc, indent=2, ensure_ascii=False, allow_nan=False))
    return 0


def smoke():
    """Non-registered smoke: same engine, shortened window, scratch attempt path."""
    phases = {"historical": ("2024-01-01", "2024-06-30"),
              "oos": ("2024-07-01", "2024-09-30"),
              "full": ("2024-01-01", "2024-09-30")}
    spec = run_spec_template(created_at=now_utc())
    spec["round_id"] = FAMILY_ID + "-r1"
    spec["run_id"] = spec["round_id"] + "-u1"
    attempt = Path(WORK_ROOT) / "smoke"
    if attempt.exists():
        shutil.rmtree(attempt)
    attempt.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    result = run(spec, str(attempt), smoke=True, phases=phases)
    print(json.dumps({"smoke": True, "status": result["status"],
                      "rows": result["case_evaluations_total"],
                      "assertions_all_true": result["assertions_all_true"],
                      "assertion_failures": result["assertion_failures"],
                      "runtime_seconds": round(time.monotonic() - started, 1)}))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-spec")
    parser.add_argument("--attempt-dir")
    parser.add_argument("--emit-template", choices=("run-spec", "round-spec"))
    parser.add_argument("--validate", help="validate a run-spec/round-spec JSON file")
    parser.add_argument("--smoke", action="store_true",
                        help="non-registered shortened-window smoke (scratch paths)")
    args = parser.parse_args(argv)
    if args.emit_template:
        return emit_template(args.emit_template)
    if args.validate:
        with open(args.validate, encoding="utf-8") as fh:
            doc = json.load(fh)
        if doc.get("document_kind") == "round_spec":
            import sys
            sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "runtime"))
            import parameter_contract as pc
            problems = pc.validate_round_spec_contract(doc)
            if problems:
                raise SystemExit("round-spec problems: %s" % problems)
            if doc.get("family_id") != FAMILY_ID:
                raise SystemExit("round-spec family mismatch")
            if any(k in doc for k in ("task_id", "kanban_task_id", "kanban_board")):
                raise SystemExit("round-spec carries a Kanban ownership key")
            print(json.dumps({"ok": True, "document_kind": "round_spec",
                              "parameter_contract": "valid"}))
        else:
            validate_spec(doc)
            print(json.dumps({"ok": True, "document_kind": "run_spec"}))
        return 0
    if args.smoke:
        return smoke()
    if not args.run_spec:
        raise SystemExit("--run-spec is required (or --emit-template/--validate/--smoke)")
    with open(args.run_spec, encoding="utf-8") as fh:
        spec = json.load(fh)
    result = run(spec, args.attempt_dir or str(Path(args.run_spec).parent))
    print(json.dumps({"status": result["status"],
                      "case_evaluations_total": result["case_evaluations_total"],
                      "assertions_all_true": result["assertions_all_true"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
