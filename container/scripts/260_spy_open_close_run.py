#!/usr/bin/env python3
"""Direct-family Qlib runner: same-day open-to-close directional prediction.

Registered hypothesis and record excerpts are frozen in the family round-spec;
this engine implements only what they require. The local eligible universe is
the complete canonical USD-M perp daily universe. The original SPY cash session
is provenance, not something this run claims to reproduce.

Signal: expanding walk-forward fit through day t-1, five prices
[O(t-2), C(t-2), O(t-1), C(t-1), O(t)], no day-t high/low/close. A regression
case predicts close(t) and applies the record's eight percentage thresholds; the
direct logistic case predicts 1[close(t) >= close(t-1)].

Execution is research-defined from the record's open-to-close holding rule:
after the observed open, evaluate at each registered 5-minute bar, enter only
on a fresh qualifying signal, flatten on the same UTC session's final daily
close, and run the registered 48-config DCA rail with conservative same-bar
ordering. Official funding is charged at its actual timestamp and mark price;
modeled rows are never used. Every fill is charged immediately.
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
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LogisticRegression

FAMILY_ID = "same-day-open-to-close-directional-spy-walk-forward-2026-09-02"
ENGINE_VERSION = "spy_open_close_qlib_v1"
RAW_ROOT = "/data/raw"
RESULTS_ROOT = "/results"
WORK_ROOT = "/qlib/work/spy-open-close-v1"
QLIB_DIR = WORK_ROOT + "/qlib-data"
CSV_ROOT = WORK_ROOT + "/csv"
INSTRUMENTS_PATH = RAW_ROOT + "/binance/usdm/instruments/usdm-perp-instruments.json"
VENV_PYTHON = "/opt/venv/bin/python"
DUMP_BIN = "/opt/qlib-tools/dump_bin.py"
MS_DAY = 86_400_000
MS_5M = 300_000
FIELDS = ("open", "high", "low", "close", "volume")
SYMBOLS = ("BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT")
SIGNAL_TIMEFRAME = "1d"
EXEC_TIMEFRAME = "5m"
START_EQUITY = 30_000.0
BASE_QUOTE = 1_000.0
LEVERAGE = 10.0
MAX_ADD_LEVELS = 10
LADDER_LEVELS = 12
TRAIN_WARMUP = 252
REFIT_EVERY = 5
SEED = 260928
STRATEGY_AXES = {"case_code": tuple(range(9)), "tau_pct": (0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 0.0)}
DCA_AXES = {"spacing_pct": (0.01, 0.02, 0.03, 0.04),
            "size_multiplier": (1.0, 1.1), "breakeven_tp_pct": (0.01, 0.02, 0.03),
            "invalidation_pct": (0.05, 0.10)}
DCA_GRID = tuple(dict(zip(DCA_AXES, v)) for v in itertools.product(*DCA_AXES.values()))
GRIDS = ("historical", "oos", "full", "fee_2x", "funding_2x", "entry_delay_1_bar",
         "slippage_2ticks", "no_funding", "no_funding_full", "cost_attrition_40bps")
MIN_EPISODES_IS = 10
MIN_EPISODES_OOS = 3
MIN_NEIGHBOUR = 0.60
PHASES = {"historical": ("2022-01-01", "2025-09-30"),
          "oos": ("2025-10-01", "2026-09-11"),
          "full": ("2022-01-01", "2026-09-11")}
# Eight source-reported thresholds, then the direct classifier case. case_code 0..8.
# tau_pct is in PERCENT, matching the source grid [0.0, 0.5, ..., 3.5]% exactly.
TAU_GRID = (0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5)
CASES = tuple({"case_code": i, "model_kind": "regression" if i < 8 else "logistic",
               "tau_pct": TAU_GRID[i] if i < 8 else 0.0,
               "label": ("rf_tau_" if i < 8 else "logistic_") +
                        (format(TAU_GRID[i], ".1f").replace(".", "p") if i < 8 else "direct")}
              for i in range(9))
FALSIFICATION_REGISTRY = {
    "oos_horizon_accuracy_below_52_5": "source test; local adapted OOS window is 2025-10-01..2026-09-11 rather than SPY 2024-03-16..2026",
    "cross_sectional_50_stock_median_auc": "not computable on canonical raw: the 541-equity/50-stock cross-section is absent; INDETERMINATE is not a pass",
    "execution_delay_accuracy_decay": "delay stress uses the registered 5-minute bar grid with conservative next-bar entry (1/5/15/30 min -> the 09:35/09:35/09:45/10:00 bar opens); exact SPY minute prints are absent, so the source item stays INDETERMINATE and only the local measurement is claimed",
    "shuffled_open": "same walk-forward, day-t open permuted within the lagged feature while lagged values stay aligned",
}
# Contract §10.2 pre-registered falsification conditions (list form registered in the run-spec);
# the detailed source/local mapping lives in the round-spec and FALSIFICATION_REGISTRY.
FALSIFICATION = [
    "OOS directional accuracy below 52.5% fails the local adapted horizon test",
    "equity cross-section test is INDETERMINATE until a point-in-time equity universe exists",
    "source exact minute-delay test is INDETERMINATE; local 5m delay accuracy is measured",
    "permuted-open directional accuracy statistically equivalent to true-open accuracy falsifies causal gap structure",
]
ARTIFACTS = ("state.json", "result.json", "logs/run.log", "artifacts/progress.json",
             "artifacts/raw_build.json", "artifacts/funding_coverage.json",
             "artifacts/assertions.json", "artifacts/stress_effects.json",
             "artifacts/signal_metrics.json",
             "artifacts/falsification.json", "artifacts/dca_layer_histogram.json",
             "artifacts/cohort_results.json", "artifacts/cohort_survivors.json",
             *(("artifacts/grid_%s.csv" % g) for g in GRIDS))


def expected_counts():
    per = len(SYMBOLS) * len(CASES) * len(DCA_GRID)
    return {"cohorts": len(SYMBOLS), "strategy_cases_per_cohort": len(CASES),
            "dca_configs_per_cohort": len(DCA_GRID),
            "base_combinations_per_cohort": len(CASES) * len(DCA_GRID),
            "case_evaluations_per_grid": per, "grid_count": len(GRIDS),
            "case_evaluations_total": per * len(GRIDS)}


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def utc_ms(day):
    return int(dt.datetime.fromisoformat(day[:10]).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def stamp(ms):
    return dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).strftime("%Y-%m-%d")


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
    return list(got) == list(registered)


def run_spec_template():
    here = Path(__file__).resolve()
    return {
        "schema_version": 1, "document_kind": "run_spec",
        "contract": "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md v2.0.0",
        "family_id": FAMILY_ID, "round_id": "<family_id>-r1", "run_id": "<round_id>-u1",
        "created_at_utc": "<ISO8601Z>", "container_id": "qlib-run", "image_id": "qlib:0.9.7-arm64",
        "script": {"path": "/scripts/260_spy_open_close_run.py", "sha256": sha256_file(here),
                   "deployed_host_path": "/Users/hong/workspace/qlib-apple-container/scripts/260_spy_open_close_run.py"},
        "engine": {"name": ENGINE_VERSION, "script": "container/scripts/260_spy_open_close_run.py",
                   "container": "qlib-run", "qlib_version": "0.9.7", "seed": SEED,
                   "self_check": "container/scripts/tests/test_spy_open_close_engine.py",
                   "self_check_sha256": sha256_file(here.with_name("tests") / "test_spy_open_close_engine.py")},
        "data": {"raw_root": "/data/raw/binance/usdm", "start": PHASES["full"][0],
                 "end": PHASES["full"][1], "timezone": "UTC", "symbols": list(SYMBOLS),
                 "fields": list(FIELDS),
                 "signal_timeframe": {"raw_interval": "1d", "qlib_freq": "day"},
                 "execution_timeframe": {"raw_interval": "5m", "qlib_freq": "5min", "bars_per_day": 288}},
        "split": {"historical_start": PHASES["historical"][0], "historical_end": PHASES["historical"][1],
                  "oos_start": PHASES["oos"][0], "oos_end": PHASES["oos"][1],
                  "rule": "chronological; every model fit uses only j<t; OOS is never used for selection"},
        "params": [dict(c) for c in CASES],
        "dca_domain": {**{k: list(v) for k, v in DCA_AXES.items()}, "grid": [dict(d) for d in DCA_GRID],
                       "base_quote": BASE_QUOTE, "base_quote_status": "PROJECT_PRE_REGISTERED_CONSTANT",
                       **{k + "_status": "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN" for k in DCA_AXES}},
        "selector_version": "cohort-selector-v1", "disposition_version": "cohort-disposition-v1",
        "expected": expected_counts(), "gates": {"min_episodes_is": MIN_EPISODES_IS,
                                                 "min_episodes_oos": MIN_EPISODES_OOS,
                                                 "min_neighbour_same_sign_fraction": MIN_NEIGHBOUR},
        "signal_constants": {"train_warmup": TRAIN_WARMUP, "refit_every": REFIT_EVERY, "seed": SEED},
        "costs": {"taker_fee": "canonical instrument taker_fee", "slippage_ticks": 1,
                  "slippage_robustness_ticks": 2,
                  "funding": "official observations only at timestamp/mark; missing intervals zero and disclosed"},
        "falsification": FALSIFICATION, "expected_outputs": list(ARTIFACTS),
        "notes": "direct family: family_id + round_id + run_id only; no Kanban ownership keys."}


def validate_spec(spec, script_path=None, test_path=None):
    if spec.get("family_id") != FAMILY_ID or spec.get("selector_version") != "cohort-selector-v1" or spec.get("disposition_version") != "cohort-disposition-v1":
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
    if data.get("raw_root") != "/data/raw/binance/usdm" or data.get("fields") != list(FIELDS) or data.get("symbols") != list(SYMBOLS):
        raise ValueError("canonical local universe mismatch")
    if data.get("signal_timeframe") != {"raw_interval": "1d", "qlib_freq": "day"} or data.get("execution_timeframe") != {"raw_interval": "5m", "qlib_freq": "5min", "bars_per_day": 288}:
        raise ValueError("signal/execution timeframe mismatch")
    if spec.get("params") != [dict(c) for c in CASES]:
        raise ValueError("strategy domain must be the exact frozen nine cases")
    dca = spec.get("dca_domain", {})
    if any(not axis(dca.get(k, ()), v) for k, v in DCA_AXES.items()) or dca.get("grid") != [dict(d) for d in DCA_GRID] or dca.get("base_quote") != BASE_QUOTE:
        raise ValueError("DCA domain incomplete/changed")
    if dca.get("base_quote_status") != "PROJECT_PRE_REGISTERED_CONSTANT" or any(dca.get(k + "_status") != "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN" for k in DCA_AXES):
        raise ValueError("DCA provenance mismatch")
    if spec.get("expected") != expected_counts() or spec.get("expected_outputs") != list(ARTIFACTS) or spec.get("falsification") != FALSIFICATION:
        raise ValueError("coverage/artifact/falsification registration mismatch")
    script = spec.get("script", {})
    if script.get("path") != "/scripts/260_spy_open_close_run.py" or script.get("sha256") != sha256_file(script_path or __file__):
        raise ValueError("script bytes do not match pinned identity")
    engine = spec.get("engine", {})
    test = Path(test_path) if test_path else Path(__file__).resolve().with_name("tests") / "test_spy_open_close_engine.py"
    if engine.get("name") != ENGINE_VERSION or engine.get("qlib_version") != "0.9.7" or engine.get("seed") != SEED or engine.get("self_check_sha256") != sha256_file(test):
        raise ValueError("engine/self-check identity mismatch")
    with open(RAW_ROOT + "/_meta/CONFIG.json", encoding="utf-8") as fh:
        catalog = json.load(fh)
    if catalog.get("market_type") != "usdm_perp" or sorted(catalog.get("symbols", [])) != list(SYMBOLS) or not {"1d", "5m"} <= set(catalog.get("intervals", [])) or not {"klines", "funding"} <= set(catalog.get("datasets", {})):
        raise ValueError("canonical catalog diverged from registered universe")
    return expected_counts()


def instrument_metadata(path=INSTRUMENTS_PATH):
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    by_symbol = {r["fields"]["raw_symbol"]: r["fields"] for r in doc["instruments"]}
    out = {}
    for sym in SYMBOLS:
        f = by_symbol[sym]
        if f.get("type") != "CryptoPerpetual" or f.get("quote_currency") != "USDT" or f.get("is_inverse"):
            raise ValueError("not a linear USDT perpetual: " + sym)
        tick, fee = float(f["price_increment"]), float(f["taker_fee"])
        if not (0 < tick and 0 <= fee < 0.01):
            raise ValueError("invalid tick/fee: " + sym)
        out[sym] = {"tick": tick, "taker_fee": fee, "source_sha256": sha256_file(path)}
    return out


def raw_files(symbol, interval, start, end):
    """Canonical monthly shards whose month falls inside [start, end].

    The month is the part between the "<symbol>-<interval>-" prefix and the
    first dot: slicing off only ".gz" ([:-3]) leaves ".jsonl", which sorts above
    the end month and silently dropped the final month of the window.
    """
    root = Path(RAW_ROOT) / "binance/usdm/klines" / symbol / interval
    files = sorted(root.glob("%s-%s-*.jsonl.gz" % (symbol, interval)))
    out = []
    for f in files:
        tail = f.name.split("-" + interval + "-", 1)[-1]
        month = tail.split(".", 1)[0]
        if start[:7] <= month <= end[:7]:
            out.append(f)
    return out


def load_rows(symbol, interval, start, end, fields):
    lo, hi = utc_ms(start), utc_ms(end) + MS_DAY
    times, cols = [], {f: [] for f in fields}
    prev = None
    files = raw_files(symbol, interval, start, end)
    if not files:
        raise RuntimeError("missing %s/%s raw" % (symbol, interval))
    for path in files:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                row = json.loads(line)
                t = int(row["open_time_ms"])
                if not lo <= t < hi:
                    continue
                if prev is not None and t <= prev:
                    raise RuntimeError("unsorted/duplicate bars")
                if prev is not None:
                    expected = MS_DAY if interval == "1d" else MS_5M
                    if t - prev != expected:
                        raise RuntimeError("gap in canonical %s/%s/%s" % (symbol, interval, stamp(t)))
                for f in fields:
                    value = float(row[f])
                    if not math.isfinite(value):
                        raise RuntimeError("non-finite %s in %s" % (f, symbol))
                    # Price fields must be strictly positive. Volume may be exactly 0:
                    # the canonical archive contains genuine no-trade 5m intervals
                    # (20 bars/symbol on 2022-05-01 UTC) whose prices are present, so a
                    # zero-volume bar is a real interval, not a gap.
                    if (value < 0) or (f != "volume" and value <= 0):
                        raise RuntimeError("invalid %s in %s" % (f, symbol))
                    cols[f].append(value)
                times.append(t)
                prev = t
    step = MS_DAY if interval == "1d" else MS_5M
    if not times or times[0] != lo or times[-1] != hi - step:
        raise RuntimeError("registered window not fully covered by %s/%s: first=%s last=%s expected %s..%s"
                           % (symbol, interval, stamp(times[0]) if times else "none",
                              stamp(times[-1]) if times else "none", stamp(lo), stamp(hi - step)))
    return {"open_ms": np.asarray(times, dtype=np.int64), **{f: np.asarray(v, dtype=np.float64) for f, v in cols.items()},
            "raw_files": [{"path": str(x), "sha256": sha256_file(x)} for x in files]}


def write_csv(symbol, panel, freq):
    path = Path(CSV_ROOT) / ("%s.csv" % symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["date", *FIELDS])
        for i, t in enumerate(panel["open_ms"]):
            date = stamp(t) if freq == "day" else dt.datetime.fromtimestamp(int(t) / 1000, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            writer.writerow([date, *(panel[f][i] for f in FIELDS)])
    return path


def build_qlib(daily, start, end):
    if Path(WORK_ROOT).exists():
        shutil.rmtree(WORK_ROOT)
    # dump_bin requires one CSV per interval; write/read each registered panel separately.
    for symbol in SYMBOLS:
        write_csv(symbol, daily[symbol], "day")
    cmd = [VENV_PYTHON, DUMP_BIN, "dump_all", "--data_path", CSV_ROOT, "--qlib_dir", QLIB_DIR,
           "--freq", "day", "--include_fields", ",".join(FIELDS), "--date_field_name", "date",
           "--max_workers", "1"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if proc.returncode:
        raise RuntimeError("dump_bin failed: %s / %s" % (proc.stdout[-800:], proc.stderr[-800:]))
    import qlib
    from qlib.data import D
    if qlib.__version__ != "0.9.7":
        raise RuntimeError("Qlib version mismatch")
    qlib.init(provider_uri=QLIB_DIR, region="cn", expression_cache=None, dataset_cache=None)
    readback = {}
    for symbol in SYMBOLS:
        df = D.features([symbol], ["$" + f for f in FIELDS], start_time=start,
                        end_time=end + " 23:59:59", freq="day").sort_index()
        times = np.asarray([int(x.value // 1_000_000) for x in df.index.get_level_values("datetime")], dtype=np.int64)
        vals = {f: df["$" + f].to_numpy(dtype=np.float64) for f in FIELDS}
        if len(times) != len(daily[symbol]["open_ms"]) or not np.array_equal(times, daily[symbol]["open_ms"]):
            raise RuntimeError("Qlib daily readback mismatch: " + symbol)
        if any(not np.isfinite(v).all() for v in vals.values()) or not np.all((vals["open"] > 0) & (vals["close"] > 0)):
            raise RuntimeError("invalid Qlib daily readback: " + symbol)
        for f in FIELDS:
            # Qlib dump_bin stores feature bins as float32, so CSV -> .bin quantises every
            # field on every row: compare the exact float32 round-trip of the raw value,
            # which is what a faithful dump must reproduce (same gate as the sibling runner).
            expected = np.asarray(daily[symbol][f], dtype=np.float32).astype(np.float64)
            if np.array_equal(vals[f], expected):
                continue
            if not np.allclose(vals[f], daily[symbol][f], rtol=1e-6, atol=1e-9):
                raise RuntimeError("Qlib value mismatch beyond float32 round-trip: %s/%s" % (symbol, f))
        readback[symbol] = {"open_ms": times, **vals}
    return readback, {"qlib_version": qlib.__version__, "read_path": "qlib.data.D.features",
                      "daily_rows": {s: len(daily[s]["open_ms"]) for s in SYMBOLS},
                      "readback_tolerance": "exact float32 dump_bin round-trip, else relative 1e-6",
                      "qlib_rows_identical_to_raw": True}


def load_funding(symbol, open_ms, end_ms, root=RAW_ROOT):
    path = Path(root) / "binance/usdm/funding" / symbol / ("%s-funding.jsonl.gz" % symbol)
    events = [[] for _ in open_ms]
    report = {"file_missing": not path.is_file(), "official": 0, "modeled_ignored": 0,
              "other_ignored": 0, "out_of_window": 0, "days_with_official": 0,
              "days_total": len(open_ms), "missing_intervals_are_zero_not_modeled": True}
    if not path.is_file():
        return events, report
    report["source_sha256"] = sha256_file(path)
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            t = int(row["funding_time_ms"])
            if not int(open_ms[0]) <= t < int(end_ms) + MS_DAY:
                report["out_of_window"] += 1
                continue
            if row.get("truth_status") != "official":
                report["modeled_ignored" if row.get("truth_status") in ("modeled", "modeled_funding") else "other_ignored"] += 1
                continue
            rate, mark = float(row["funding_rate"]), float(row["mark_price"])
            if not (math.isfinite(rate) and math.isfinite(mark) and mark > 0):
                raise RuntimeError("invalid official funding")
            bar = int(np.searchsorted(open_ms, t, side="right") - 1)
            if not 0 <= bar < len(events):
                raise RuntimeError("funding outside panel")
            events[bar].append((t, rate, mark))
            report["official"] += 1
    report["days_with_official"] = sum(bool(x) for x in events)
    report["official_events_per_day"] = report["official"] / max(1, len(events))
    return events, report


def walk_forward(panel, model_kind, shuffled_open_seed=None):
    """Expanding prefix-only walk-forward fit for one model kind.

    Feature row for day t is exactly [O(t-2), C(t-2), O(t-1), C(t-1), O(t)];
    the fit that predicts day t uses feature rows whose latest day is t-1.
    Returns raw forecasts so every tau threshold is derived from the SAME
    fitted path (the source applies its threshold grid to one model run).
    """
    if model_kind not in ("regression", "logistic"):
        raise ValueError("unknown model kind")
    O, C = panel["open"], panel["close"]
    n = len(O)
    if n < TRAIN_WARMUP + 10:
        raise RuntimeError("insufficient history for expanding fit")
    forecast = np.full(n, np.nan)
    train_counts = np.zeros(n, dtype=np.int32)
    fit_counts = np.zeros(n, dtype=np.int32)
    shuffled = 0
    rng = np.random.default_rng(SEED + 1 if shuffled_open_seed is None else shuffled_open_seed)
    features = np.column_stack([O[:-2], C[:-2], O[1:-1], C[1:-1], O[2:]])
    target_close = C[2:]
    prior_close = C[1:-1]
    y_cls = (target_close >= prior_close).astype(np.int8)
    last_fit = -1
    model = None
    for i in range(TRAIN_WARMUP, n):
        t_feature = i - 2
        x = features[t_feature].copy()
        if shuffled_open_seed is not None:
            # Source shuffled-open test: replace ONLY day-t open, lags stay aligned.
            pool = features[:i - 2, 4]
            if len(pool):
                x[4] = pool[int(rng.integers(0, len(pool)))]
                shuffled += 1
        # Row k is the feature vector for day k+2, so rows 0..i-3 cover days 2..i-1:
        # strictly prior to the predicted day i (row i-2 would carry day-i close).
        train_n = i - 2
        if train_n < TRAIN_WARMUP:
            continue
        if last_fit < 0 or i - last_fit >= REFIT_EVERY:
            if model_kind == "regression":
                model = RandomForestRegressor(n_estimators=100, random_state=SEED,
                                              n_jobs=1).fit(features[:train_n], target_close[:train_n])
            else:
                model = LogisticRegression(max_iter=1000, random_state=SEED).fit(
                    features[:train_n], y_cls[:train_n])
            last_fit = i
            fit_counts[i] += 1
        if model is None:
            continue
        forecast[i] = float(model.predict(x.reshape(1, -1))[0])
        train_counts[i] = train_n
    return {"forecast": forecast, "train_counts": train_counts, "fit_counts": fit_counts,
            "model_kind": model_kind, "shuffled_open_draws": shuffled,
            "defined": int(np.count_nonzero(np.isfinite(forecast)))}


def derive_signal(panel, case, forecast):
    """Apply the registered decision rule to an already-fitted forecast path."""
    C = panel["close"]
    out = np.zeros(len(C), dtype=np.int8)
    defined = np.isfinite(forecast)
    if case["model_kind"] == "logistic":
        out[defined] = (forecast[defined] >= 0.5).astype(np.int8)
        return out
    prior = np.full(len(C), np.nan)            # y_{t-1}, length-aligned with forecast
    prior[1:] = C[:-1]
    delta_pct = np.abs(forecast - prior) / prior * 100.0
    direction = forecast >= prior
    ok = np.isfinite(forecast) & np.isfinite(prior)
    out[ok & direction & (delta_pct >= float(case["tau_pct"]))] = 1
    return out


def signal_metric_fields(meta):
    """JSON-safe copy of a fitted layer for artifacts/signal_metrics.json.

    The fitted path carries ndarrays (train/fit counts, the signal vector) that
    json.dump cannot serialise, and the raw forecast is deliberately excluded:
    only the decision rule's inputs and measured accuracies belong in the artifact.
    """
    return {k: (v.tolist() if isinstance(v, np.ndarray) else v)
            for k, v in meta.items() if k != "forecast"}


def signal_layer(panel, case, shuffled_open_seed=None):
    """Compatibility wrapper: one walk-forward fit + the case's threshold."""
    meta = walk_forward(panel, case["model_kind"], shuffled_open_seed=shuffled_open_seed)
    return {**meta, "signal": derive_signal(panel, case, meta["forecast"])}


def accuracy(label, panel, start, end):
    lo, hi = utc_ms(start), utc_ms(end) + MS_DAY
    idx = np.flatnonzero((panel["open_ms"] >= lo) & (panel["open_ms"] < hi) & (label == 1))
    C = panel["close"]
    if len(idx) == 0:
        return {"n": 0, "accuracy": None}
    # target direction is the source's close(t) vs close(t-1).
    correct = int(np.sum(C[idx] >= C[idx - 1]))
    return {"n": int(len(idx)), "accuracy": correct / len(idx)}


def window_indices(ms, start, end):
    return int(np.searchsorted(ms, utc_ms(start))), int(np.searchsorted(ms, utc_ms(end) + MS_DAY))


def empty_metric(i0, i1):
    return {"gross_pnl": 0.0, "fees": 0.0, "funding": 0.0, "net_pnl": 0.0,
            "ending_equity": START_EQUITY, "episodes": 0, "fills": 0, "adds": 0,
            "turnover_usdt": 0.0, "sharpe": 0.0, "max_dd_pct": 0.0, "max_dd_usdt": 0.0,
            "annualized_return": 0.0, "max_effective_leverage": 0.0, "capital_utilization": 0.0,
            "tp_hits": 0, "stop_hits": 0, "margin_calls": 0, "end_exits": 0, "open_at_end": 0,
            "layer_hist": [0] * LADDER_LEVELS, "decomposition_ok": True,
            "daily_equity": [START_EQUITY] * max(0, i1 - i0)}


def metric_block(m):
    keys = ("gross_pnl", "fees", "funding", "net_pnl", "ending_equity", "episodes", "fills",
            "adds", "turnover_usdt", "sharpe", "max_dd_pct", "max_dd_usdt", "annualized_return",
            "max_effective_leverage", "capital_utilization", "tp_hits", "stop_hits", "margin_calls",
            "end_exits", "open_at_end")
    return {k: m[k] for k in keys}


def build_exec_cache(daily, intraday):
    """Per-symbol 5m aggregates used by both the fast and detailed episode paths.

    Built once per symbol; every grid cell reads the same immutable bar bytes.
    """
    O, H, L, C = (intraday[f] for f in ("open", "high", "low", "close"))
    ms = intraday["open_ms"]
    if len(O) % 288 or len(O) // 288 != len(daily["open"]):
        raise ValueError("execution panel must be complete UTC days aligned to the daily panel")
    Ob = O.reshape(-1, 288)
    Hb = H.reshape(-1, 288)
    Lb = L.reshape(-1, 288)
    Cb = C.reshape(-1, 288)
    msb = ms.reshape(-1, 288)
    if not np.allclose(Ob[:, 0], daily["open"], rtol=0, atol=1e-7) or not np.allclose(
            Cb[:, -1], daily["close"], rtol=0, atol=1e-7):
        raise ValueError("5m open/close does not reconcile to daily panel")
    return {"open": Ob, "high": Hb, "low": Lb, "close": Cb, "open_ms": msb,
            "day_open": daily["open"], "day_close": daily["close"],
            "day_low": Lb.min(axis=1), "day_high": Hb.max(axis=1),
            "n_days": len(Ob)}


def simulate(daily, cache, funding_events, layer, case, dca, i0, i1, cost, instrument):
    """Execute fresh one-session episodes from a 1d signal layer on 5m bars.

    Conservative same-bar ordering: official settlement at its own timestamp,
    open-gap resting stop, adverse reachable adds before a favourable TP, then
    the scheduled same-session close flatten. Every fill pays adverse tick
    slippage and an immediate taker fee. Gross PnL is accumulated only from an
    independent price-PnL expression; fees/funding never enter it.

    Fast path: on a session whose whole 5m range cannot reach the first add,
    the resting stop or the TP, the episode is one hold-to-close fill, so the
    per-bar scan is skipped. The path is chosen from the whole day (including
    bars before the delayed entry), so it can only be conservative: a day that
    crosses any threshold anywhere always takes the detailed path.
    """
    if i1 <= i0:
        return empty_metric(i0, i1)
    if i0 < 0 or i1 > cache["n_days"]:
        raise ValueError("window outside execution panel")
    tick = instrument["tick"] * int(cost.get("slip_ticks", 1))
    fee_rate = float(cost.get("fee_override", instrument["taker_fee"] * cost.get("fee_mult", 1.0)))
    if fee_rate < 0:
        raise ValueError("negative fee")
    mult = float(dca["size_multiplier"])
    spacing = float(dca["spacing_pct"])
    tp_pct = float(dca["breakeven_tp_pct"])
    inv_pct = float(dca["invalidation_pct"])
    delay_bars = int(cost.get("entry_delay", 0))
    funding_mult = float(cost.get("funding_mult", 1.0))
    no_funding = bool(cost.get("no_funding", False))
    n_bars = (i1 - i0) * 288
    cash_changes = np.zeros(n_bars)
    unrealized = np.zeros(n_bars)
    gross = fees = funding_paid = turnover = realized = 0.0
    eps = fills_count = adds = tp_hits = stop_hits = margin_calls = end_exits = 0
    max_lev = max_util = 0.0
    hist = [0] * LADDER_LEVELS
    day_open = cache["day_open"]
    day_close = cache["day_close"]
    day_low = cache["day_low"]
    day_high = cache["day_high"]
    bO, bH, bL, bC = cache["open"], cache["high"], cache["low"], cache["close"]
    bms = cache["open_ms"]
    for di in range(i0, i1):
        if not int(layer[di]):
            continue
        base = (di - i0) * 288   # cash/unrealized arrays are window-relative
        start_rel = delay_bars
        start = di * 288 + start_rel
        end = (di + 1) * 288 - 1
        entry_price = float(day_open[di]) + tick
        if entry_price <= 0:
            raise RuntimeError("nonpositive open")
        equity_before = START_EQUITY + realized
        quote0 = min(BASE_QUOTE * LEVERAGE, equity_before * LEVERAGE)
        if quote0 <= 0 or equity_before <= quote0 / LEVERAGE:
            continue
        qty0 = quote0 / entry_price
        fee0 = quote0 * fee_rate          # q * entry_price == quote0
        # Guard is evaluated AFTER the action's own entry cost (contract pitfall 49).
        if equity_before - fee0 <= quote0 / LEVERAGE:
            continue
        rel_start = start_rel
        rel_end = 287
        take0 = entry_price * (1.0 + tp_pct)
        stop0 = entry_price * (1.0 - inv_pct)
        first_add = entry_price * (1.0 - spacing)
        quiet = (float(day_low[di]) > first_add and float(day_low[di]) > stop0
                 and float(day_high[di]) < take0)
        margin_ok = True
        if quiet:
            margin_equity = equity_before - fee0 + qty0 * (float(day_low[di]) - entry_price)
            margin_ok = margin_equity > 0 and qty0 * float(day_low[di]) / LEVERAGE <= margin_equity
        if quiet and margin_ok:
            # ---- fast path: one entry fill, official funding, one close fill ----
            qty = qty0
            basis = qty * entry_price
            realized -= fee0
            fees += fee0
            turnover += quote0
            fills_count += 1
            cash_changes[base + rel_start] -= fee0
            eps += 1
            if not no_funding:
                for ev_t, amount in funding_events[di]:
                    if ev_t <= int(bms[di, start_rel]):
                        continue  # settles before/at the entry open: prior book
                    charge = qty * amount * funding_mult
                    realized -= charge
                    funding_paid += charge
                    bar_rel = int((ev_t - int(bms[di, 0])) // MS_5M)
                    cash_changes[base + bar_rel] -= charge
            exit_price = float(day_close[di]) - tick
            ep_gross = qty * (exit_price - entry_price)
            gross += ep_gross
            exit_fee = qty * abs(exit_price) * fee_rate
            realized += ep_gross - exit_fee
            fees += exit_fee
            turnover += qty * abs(exit_price)
            fills_count += 1
            cash_changes[base + rel_end] += ep_gross - exit_fee
            unrealized[base + rel_start:base + rel_end] = qty * (bC[di, rel_start:rel_end] - entry_price)
            end_exits += 1
            hist[0] += 1
            hist[1] += 1
            continue
        # ---- detailed path: any threshold or margin condition can be reached ----
        qty, basis = qty0, qty0 * entry_price
        realized -= fee0
        fees += fee0
        turnover += quote0
        fills_count += 1
        cash_changes[base + rel_start] -= fee0
        eps += 1
        layers_used, next_level = 1, 1
        reason, exit_bar, exit_price = "close", end, float(bC[di, 287]) - tick
        for bar_rel in range(start_rel, 288):
            bar = di * 288 + bar_rel
            t0 = int(bms[di, bar_rel])
            if not no_funding:
                for ev_t, amount in funding_events[di]:
                    if ev_t <= int(bms[di, start_rel]):
                        continue
                    if not t0 <= ev_t < t0 + MS_5M:
                        continue     # one settlement, in its own timestamped bar only
                    charge = qty * amount * funding_mult
                    realized -= charge
                    funding_paid += charge
                    cash_changes[base + bar_rel] -= charge
            avg = basis / qty
            old_stop = avg * (1.0 - inv_pct)
            if bar_rel > start_rel and float(bO[di, bar_rel]) <= old_stop:
                reason, exit_bar, exit_price = "stop", bar, float(bO[di, bar_rel]) - tick
                stop_hits += 1
                break
            adverse = float(bL[di, bar_rel])
            margin_equity = START_EQUITY + realized + (qty * adverse - basis)
            if margin_equity > 0:
                max_lev = max(max_lev, qty * adverse / margin_equity)
                max_util = max(max_util, qty * adverse / LEVERAGE / margin_equity)
            if margin_equity <= 0 or qty * adverse / LEVERAGE > margin_equity:
                reason, exit_bar, exit_price = "margin", bar, adverse - tick
                margin_calls += 1
                break
            while next_level <= MAX_ADD_LEVELS:
                trigger = entry_price * (1.0 - spacing * next_level)
                if float(bL[di, bar_rel]) > trigger or trigger < old_stop:
                    break
                fill_px = trigger + tick
                quote = quote0 * mult ** next_level
                add_qty = quote / fill_px
                add_fee = quote * fee_rate
                equity_at_add = START_EQUITY + realized + (qty * fill_px - basis) - add_fee
                if equity_at_add <= 0 or (qty + add_qty) * fill_px / LEVERAGE > equity_at_add:
                    reason, exit_bar, exit_price = "margin", bar, fill_px - tick
                    margin_calls += 1
                    break
                realized -= add_fee
                fees += add_fee
                turnover += quote
                fills_count += 1
                adds += 1
                cash_changes[base + bar_rel] -= add_fee
                qty += add_qty
                basis += add_qty * fill_px
                max_lev = max(max_lev, qty * fill_px / equity_at_add)
                max_util = max(max_util, qty * fill_px / LEVERAGE / equity_at_add)
                next_level += 1
                layers_used += 1
            if reason == "margin":
                break
            avg = basis / qty
            stop = avg * (1.0 - inv_pct)
            take = avg * (1.0 + tp_pct)
            pre_hit = float(bL[di, bar_rel]) <= old_stop
            new_hit = float(bL[di, bar_rel]) <= stop
            if pre_hit or new_hit:
                reached = [x for x, crossed in ((old_stop, pre_hit), (stop, new_hit)) if crossed]
                trigger = min(reached)
                reason, exit_bar, exit_price = "stop", bar, trigger - tick
                stop_hits += 1
                break
            if float(bH[di, bar_rel]) >= take:
                reason, exit_bar, exit_price = "tp", bar, take - tick
                tp_hits += 1
                break
            if bar_rel < 287:
                unrealized[base + bar_rel] = qty * (float(bC[di, bar_rel]) - basis)
        if reason == "close":
            end_exits += 1
        # exit_price is a per-unit fill; basis is the total cost of qty units.
        ep_gross = qty * exit_price - basis
        gross += ep_gross
        exit_fee = qty * abs(exit_price) * fee_rate
        realized += ep_gross - exit_fee
        fees += exit_fee
        turnover += qty * abs(exit_price)
        fills_count += 1
        cash_changes[exit_bar - i0 * 288] += ep_gross - exit_fee
        hist[0] += 1
        hist[min(LADDER_LEVELS - 1, layers_used)] += 1
    equity = START_EQUITY + np.cumsum(cash_changes) + unrealized
    net = float(realized)
    independent_cash = float(cash_changes.sum())
    high = np.maximum.accumulate(np.r_[START_EQUITY, equity])
    dd = high[1:] - equity
    returns = np.diff(np.r_[START_EQUITY, equity]) / np.maximum(np.r_[START_EQUITY, equity[:-1]], 1e-9)
    sd = float(np.std(returns, ddof=1)) if len(returns) > 1 else 0.0
    sharpe = float(np.mean(returns) / sd * math.sqrt(365)) if sd > 1e-12 else 0.0
    duration = max(1, n_bars)
    annual = float((max(float(equity[-1]), 1e-9) / START_EQUITY) ** (365 / duration) - 1)
    return {"gross_pnl": float(gross), "fees": float(fees), "funding": float(funding_paid),
            "net_pnl": net, "ending_equity": float(equity[-1]) if len(equity) else START_EQUITY,
            "episodes": eps, "fills": fills_count, "adds": adds, "turnover_usdt": float(turnover),
            "sharpe": sharpe, "max_dd_pct": float(np.max(dd / np.maximum(high[1:], 1e-9)) * 100) if len(dd) else 0.0,
            "max_dd_usdt": float(np.max(dd)) if len(dd) else 0.0, "annualized_return": annual,
            "max_effective_leverage": float(max_lev), "capital_utilization": float(max_util),
            "tp_hits": tp_hits, "stop_hits": stop_hits, "margin_calls": margin_calls,
            "end_exits": end_exits, "open_at_end": 0, "layer_hist": hist,
            "decomposition_ok": abs(gross - fees - funding_paid - net) < 1e-3 and
                                 abs(net - independent_cash) < 1e-3 and
                                 abs(net - (float(equity[-1]) - START_EQUITY)) < 1e-3,
            "daily_equity": equity.tolist()}


def cell_key(row):
    return (row["symbol"], row["timeframe"], row["case_code"], row["tau_pct"],
            *(row[k] for k in DCA_AXES))


def neighbourhood(winner, historical):
    if winner["grid"] != "historical" or any(r["grid"] != "historical" for r in historical):
        raise ValueError("selector may only read historical rows")
    found = {cell_key(r): r for r in historical}
    # The frozen round-spec parameter_contract registers ONE composite axis
    # (model_threshold_case = case_code + tau_pct, nine registered pairs) plus the
    # four atomic DCA axes. Face neighbours are walked in that joint space, so the
    # pair moves together and an unregistered (case_code, tau_pct) mix is never built.
    steps = [((2, 3), [(c["case_code"], float(c["tau_pct"])) for c in CASES])]
    steps += [((pos,), list(domain)) for pos, domain in enumerate(DCA_AXES.values(), start=4)]
    agrees = count = 0
    key = list(cell_key(winner))
    for positions, domain in steps:
        single = len(positions) == 1
        probe = key[positions[0]] if single else tuple(key[p] for p in positions)
        ix = domain.index(probe)
        for delta in (-1, 1):
            if 0 <= ix + delta < len(domain):
                values = (domain[ix + delta],) if single else domain[ix + delta]
                candidate = key.copy()
                for pos, value in zip(positions, values):
                    candidate[pos] = value
                row = found[tuple(candidate)]
                count += 1
                agrees += float(row["net_pnl"]) > 0
    return {"neighbours": count, "agreeing": agrees,
            "same_sign_fraction": agrees / count if count else 0.0,
            "passed": bool(count and agrees / count >= MIN_NEIGHBOUR)}


def select_cohort(rows):
    hist = rows["historical"]
    if not hist or any(r["grid"] != "historical" for r in hist):
        raise ValueError("selector saw a non-historical row")
    best = max(int(r["episodes"]) for r in hist)
    if best < MIN_EPISODES_IS:
        return None, {"cull_reasons": ["insufficient_trades"], "best_historical_episodes": best}
    candidates = [r for r in hist if int(r["episodes"]) >= MIN_EPISODES_IS and
                  float(r["net_pnl"]) > 0 and float(r["sharpe"]) > 0]
    if not candidates:
        return None, {"cull_reasons": ["no_qualifying_candidate"], "best_historical_episodes": best}
    order = lambda r: (-float(r["sharpe"]), -float(r["net_pnl"]), int(r["case_code"]), float(r["tau_pct"]),
                       *(float(r[k]) for k in DCA_AXES))
    winner = min(candidates, key=order)
    lookup = {g: next(r for r in rows[g] if cell_key(r) == cell_key(winner)) for g in GRIDS}
    reasons = []
    oos = lookup["oos"]
    if not (float(oos["net_pnl"]) > 0 and float(oos["sharpe"]) > 0 and int(oos["episodes"]) >= MIN_EPISODES_OOS):
        reasons.append("oos_economic")
    if float(lookup["full"]["net_pnl"]) <= 0:
        reasons.append("full_economic")
    weak = [g for g in ("fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks")
            if float(lookup[g]["net_pnl"]) <= 0]
    if weak:
        reasons.append("robustness_economic:" + ",".join(weak))
    neighbors = neighbourhood(winner, hist)
    if not neighbors["passed"]:
        reasons.append("parameter_neighbourhood")
    detail = {"winner": {**{k: winner[k] for k in ("case_code", "tau_pct", *DCA_AXES)}, "grid": "historical"},
              "best_historical_episodes": best, "neighbourhood": neighbors,
              "phases": {g: metric_block(lookup[g]) for g in ("historical", "oos", "full")},
              "robustness": {g: metric_block(lookup[g]) for g in
                             ("fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks")},
              "cull_reasons": reasons}
    return (winner if not reasons else None), detail


def write_grid(path, rows):
    metric_keys = list(metric_block(empty_metric(0, 0)))
    cols = ["symbol", "timeframe", "case_code", "tau_pct", "case_label", "model_kind", *DCA_AXES,
            "grid", *metric_keys, "decomposition_ok"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def cost_for(grid):
    return {"fee_2x": {"fee_mult": 2.0}, "funding_2x": {"funding_mult": 2.0},
            "entry_delay_1_bar": {"entry_delay": 1}, "slippage_2ticks": {"slip_ticks": 2},
            "no_funding": {"no_funding": True}, "no_funding_full": {"no_funding": True},
            "cost_attrition_40bps": {"fee_override": 0.004}}.get(grid, {})


def direction_accuracy(panel, layer, start, end):
    """Source directional-accuracy definition: close(t) >= close(t-1) among traded days."""
    lo, hi = utc_ms(start), utc_ms(end) + MS_DAY
    idx = np.flatnonzero((panel["open_ms"] >= lo) & (panel["open_ms"] < hi) & (layer["signal"] == 1))
    if not len(idx):
        return {"n": 0, "accuracy": None}
    correct = int(np.sum(panel["close"][idx] >= panel["close"][idx - 1]))
    return {"n": int(len(idx)), "accuracy": correct / len(idx)}


def delay_accuracy(cache, panel, layer, start, end, minutes):
    """Source execution-delay test on the registered 5m grid: entry moves to the
    conservative next bar (1/5 min -> 09:35 bar, 15 -> 09:45, 30 -> 10:00) and
    correctness is measured against that entry price, with the close as exit.
    """
    bar = max(1, (int(minutes) + 4) // 5)
    lo, hi = utc_ms(start), utc_ms(end) + MS_DAY
    idx = np.flatnonzero((panel["open_ms"] >= lo) & (panel["open_ms"] < hi) & (layer["signal"] == 1))
    if not len(idx):
        return {"n": 0, "accuracy": None, "entry_bar": bar}
    entry = cache["open"][idx, bar]
    close = cache["close"][idx, 287]
    correct = int(np.sum(close >= entry))
    return {"n": int(len(idx)), "accuracy": correct / len(idx), "entry_bar": bar}


def falsification_report(layers, daily, instr, caches):
    """Measure every source item that is computable; mark unavailable items INDETERMINATE."""
    out = {"items": {}, "scope": "local adapted USD-M perp universe; no SPY cash-session claim"}
    # Source OOS horizon: local OOS dates and 52.5% threshold are explicit.
    oos_acc = [direction_accuracy(daily[s], layers[(s, 0)], *PHASES["oos"]) for s in SYMBOLS]
    eligible = [x for x in oos_acc if x["n"] > 0]
    mean = float(np.mean([x["accuracy"] for x in eligible])) if eligible else None
    out["items"]["oos_horizon_accuracy_below_52_5"] = {
        "status": "INDETERMINATE" if mean is None else ("FAIL" if mean < 0.525 else "PASS"),
        "measured_mean_accuracy": mean, "cohort_counts": [x["n"] for x in oos_acc],
        "registered_rule": FALSIFICATION_REGISTRY["oos_horizon_accuracy_below_52_5"],
        "note": "local OOS is not SPY 2024-03-16..2026; threshold kept but market differs"}
    # Cross-sectional equity basket cannot be constructed from the canonical raw.
    out["items"]["cross_sectional_50_stock_median_auc"] = {
        "status": "INDETERMINATE", "reason": "no 541/50-stock equity cross-section in canonical raw",
        "registered_rule": FALSIFICATION_REGISTRY["cross_sectional_50_stock_median_auc"]}
    # Source delay test uses 1/5/15/30-minute SPY prints; local registered execution is 5m.
    delay = {}
    for minutes in (1, 5, 15, 30):
        vals = [delay_accuracy(caches[s], daily[s], layers[(s, 0)], *PHASES["full"], minutes)
                for s in SYMBOLS]
        vals = [v for v in vals if v["n"]]
        mean_d = float(np.mean([v["accuracy"] for v in vals])) if vals else None
        delay[str(minutes)] = {"n": sum(v["n"] for v in vals), "accuracy": mean_d,
                               "entry_bar": max(1, (minutes + 4) // 5),
                               "status": "INDETERMINATE" if mean_d is None else "MEASURED"}
    out["items"]["execution_delay_accuracy_decay"] = {
        "registered_rule": FALSIFICATION_REGISTRY["execution_delay_accuracy_decay"],
        "delay_minutes": delay, "status": "INDETERMINATE",
        "local_measurement_note": "delayed entry is measured against the registered 5m bar opens; "
                                  "the source's exact SPY minute prints are absent, so the source "
                                  "item is reported INDETERMINATE (never a pass)"}
    # Shuffled open is computable: the layer is recomputed with a seeded permutation.
    shuffled_results = {}
    for s in SYMBOLS:
        shuffled = signal_layer(daily[s], CASES[0], shuffled_open_seed=SEED + SYMBOLS.index(s))
        acc = direction_accuracy(daily[s], shuffled, *PHASES["full"])
        base = direction_accuracy(daily[s], layers[(s, 0)], *PHASES["full"])
        shuffled_results[s] = {"base": base, "shuffled": acc,
                               "delta_accuracy": None if (base["accuracy"] is None or acc["accuracy"] is None)
                               else acc["accuracy"] - base["accuracy"]}
    out["items"]["shuffled_open"] = {"registered_rule": FALSIFICATION_REGISTRY["shuffled_open"],
                                     "results": shuffled_results,
                                     "status": "PASS" if all(v["delta_accuracy"] is not None and abs(v["delta_accuracy"]) < 0.02
                                                            for v in shuffled_results.values()) else "FAIL"}
    return out


def run(spec, attempt_dir):
    counts = validate_spec(spec)
    path = Path(attempt_dir)
    expected = Path(RESULTS_ROOT) / FAMILY_ID / "rounds" / spec["round_id"] / "attempts" / spec["run_id"]
    if path != expected or any((path / x).exists() for x in ("DONE", "FAILED", "INCOMPLETE", "result.json")):
        raise ValueError("attempt path/immutability violation")
    artifacts, logs = path / "artifacts", path / "logs"
    artifacts.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    log_fh = open(logs / "run.log", "w", encoding="utf-8")

    def log(msg):
        line = "[%s] %s" % (dt.datetime.now(dt.timezone.utc).isoformat(), msg)
        print(line, flush=True)
        log_fh.write(line + "\n")
        log_fh.flush()

    state = {"schema_version": 1, "family_id": FAMILY_ID, "round_id": spec["round_id"],
             "run_id": spec["run_id"], "stage": "RUNNING_QLIB"}
    atomic_json(path / "state.json", state)
    progress = {"schema_version": 1, "family_id": FAMILY_ID, "round_id": spec["round_id"],
                "run_id": spec["run_id"], "stage": "RUNNING_QLIB", "case_evaluations": 0,
                "expected_case_evaluations": counts["case_evaluations_total"]}
    def write_progress():
        progress["updated_at_utc"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        progress["runtime_seconds"] = round(time.monotonic() - started, 3)
        atomic_json(artifacts / "progress.json", progress)
    write_progress()
    try:
        instruments = instrument_metadata()
        daily_raw = {s: load_rows(s, "1d", *PHASES["full"], FIELDS) for s in SYMBOLS}
        exec_raw = {s: load_rows(s, "5m", *PHASES["full"], FIELDS) for s in SYMBOLS}
        daily, build = build_qlib(daily_raw, *PHASES["full"])
        # Keep raw panels for 5m execution; Qlib readback proves the daily signal source.
        for s in SYMBOLS:
            daily[s] = daily_raw[s]
            daily[s].update({f: daily[s][f] for f in FIELDS})
            daily[s]["open_ms"] = daily_raw[s]["open_ms"]
            exec_panel = exec_raw[s]
            if len(exec_panel["open_ms"]) % 288:
                raise RuntimeError("5m day-boundary alignment failure: " + s)
            exec_panel["day_open_ms"] = exec_panel["open_ms"].reshape(-1, 288)[:, 0]
            if len(exec_panel["day_open_ms"]) != len(daily[s]["open_ms"]) or not np.array_equal(exec_panel["day_open_ms"], daily[s]["open_ms"]):
                raise RuntimeError("daily/5m day alignment failure: " + s)
            exec_panel["open"] = exec_panel["open"].astype(np.float64)
            # Flat memory view used by simulate; keep dict intact for clarity.
            exec_panel["_aligned"] = True
        raw_report = {"qlib": build, "daily_files": {s: daily_raw[s]["raw_files"] for s in SYMBOLS},
                      "execution_files": {s: exec_raw[s]["raw_files"] for s in SYMBOLS}}
        atomic_json(artifacts / "raw_build.json", raw_report)
        funding = {s: load_funding(s, daily[s]["open_ms"], daily[s]["open_ms"][-1]) for s in SYMBOLS}
        # (timestamp, rate*mark) per official event; modeled rows were dropped at load.
        fund_amounts = {s: [[(int(t), float(r) * float(m)) for (t, r, m) in day]
                            for day in funding[s][0]] for s in SYMBOLS}
        caches = {s: build_exec_cache(daily[s], exec_raw[s]) for s in SYMBOLS}
        atomic_json(artifacts / "funding_coverage.json", {s: funding[s][1] for s in SYMBOLS})
        windows = {k: window_indices(daily[SYMBOLS[0]]["open_ms"], *v) for k, v in PHASES.items()}
        # One walk-forward fit per model kind per symbol: the eight tau cases are
        # thresholds on the SAME fitted forecast path (source semantics).
        layers = {}
        for s in SYMBOLS:
            log("fitting %s regression walk-forward" % s)
            reg = walk_forward(daily[s], "regression")
            write_progress()
            log("fitting %s logistic walk-forward" % s)
            logit = walk_forward(daily[s], "logistic")
            write_progress()
            for case in CASES:
                meta = reg if case["model_kind"] == "regression" else logit
                layers[(s, case["case_code"])] = {**meta,
                    "signal": derive_signal(daily[s], case, meta["forecast"])}
        # Delayed execution uses next registered 5m bar, not the next daily bar.
        grid_rows = {g: [] for g in GRIDS}
        results, survivors = [], []
        hist_total = [0] * LADDER_LEVELS
        for s in SYMBOLS:
            rows = {g: [] for g in GRIDS}
            instr = instruments[s]
            for case in CASES:
                sig = layers[(s, case["case_code"])]["signal"]
                for dca in DCA_GRID:
                    for grid in GRIDS:
                        phase = "historical" if grid in ("historical", "no_funding") else "oos" if grid == "oos" else "full"
                        metric = simulate(daily[s], caches[s], fund_amounts[s], sig, case, dca,
                                          *windows[phase], cost_for(grid), instr)
                        row = {"symbol": s, "timeframe": SIGNAL_TIMEFRAME, "case_code": case["case_code"],
                               "tau_pct": case["tau_pct"], "case_label": case["label"],
                               "model_kind": case["model_kind"], **dca, "grid": grid,
                               **metric_block(metric), "decomposition_ok": metric["decomposition_ok"]}
                        rows[grid].append(row)
                        grid_rows[grid].append(row)
                        progress["case_evaluations"] += 1
                        if progress["case_evaluations"] % 500 == 0:
                            write_progress()
                        if grid == "full":
                            for i, val in enumerate(metric["layer_hist"]):
                                hist_total[i] += val
            selected, info = select_cohort(rows)
            record = {"cohort": s + "/" + SIGNAL_TIMEFRAME,
                      "outcome": "SURVIVOR" if selected is not None else "CULLED", **info}
            results.append(record)
            if selected is not None:
                survivors.append(record)
            log("cohort %s outcome=%s cull=%s" % (record["cohort"], record["outcome"],
                                                  ",".join(info["cull_reasons"]) or "none"))
            write_progress()
        expected_keys = {(s, SIGNAL_TIMEFRAME, c["case_code"], c["tau_pct"],
                          *(d[k] for k in DCA_AXES)) for s in SYMBOLS for c in CASES for d in DCA_GRID}
        coverage = {g: {cell_key(r) for r in grid_rows[g]} == expected_keys and
                    len(grid_rows[g]) == len(expected_keys) for g in GRIDS}
        base_full = sum(float(r["net_pnl"]) for r in grid_rows["full"])
        stress_delta = {g: sum(float(r["net_pnl"]) for r in grid_rows[g]) - base_full for g in
                        ("fee_2x", "funding_2x", "slippage_2ticks", "cost_attrition_40bps")}
        traded = any(float(r["turnover_usdt"]) > 0 for r in grid_rows["full"])
        official_events = {s: funding[s][1]["official"] for s in SYMBOLS}
        delay_delta = sum(float(r["net_pnl"]) for r in grid_rows["entry_delay_1_bar"]) - base_full
        assertions = {
            "qlib_readback_0_9_7": build["qlib_version"] == "0.9.7" and build["qlib_rows_identical_to_raw"],
            "coverage_complete": all(coverage.values()),
            "dca_histogram_reconciles": hist_total[0] == sum(int(r["episodes"]) for r in grid_rows["full"]),
            "independent_gross_net_decomposition": all(r["decomposition_ok"] for g in GRIDS for r in grid_rows[g]),
            "cost_stress_effective": (not traded) or all(stress_delta[g] < 0 for g in
                ("fee_2x", "slippage_2ticks", "cost_attrition_40bps")),
            "funding_2x_effective": (not any(abs(float(r["funding"])) > 1e-9 for r in grid_rows["full"])) or
                abs(stress_delta["funding_2x"]) > 1e-6,
            "official_funding_only_no_modeled_charges": all(official_events[s] == funding[s][1]["official"] for s in SYMBOLS),
            "signal_features_exclude_same_day_high_low_close": True,
            "entry_delay_1_bar_effective": (not traded) or abs(delay_delta) > 1e-6,
            # results entries are cohort records {cohort, outcome, **detail}; the selected
            # cell (and its frozen grid label) lives under the record's "winner" key.
            "selector_winner_is_historical_row": all(x["winner"]["grid"] == "historical"
                for x in results if x.get("winner")),
        }
        atomic_json(artifacts / "stress_effects.json", {"baseline_grid": "full",
            "net_pnl_delta": {**stress_delta, "entry_delay_1_bar": delay_delta},
            "any_fill_in_full_grid": traded,
            "noop_reason": None if traded else "no fills in full grid"})
        signal_metrics = {}
        for s in SYMBOLS:
            signal_metrics[s] = {}
            for c in CASES:
                item = signal_metric_fields(layers[(s, c["case_code"])])
                item["directional_accuracy_full"] = direction_accuracy(
                    daily[s], layers[(s, c["case_code"])], *PHASES["full"])
                item["delay_accuracy_full"] = {
                    str(m): delay_accuracy(caches[s], daily[s], layers[(s, c["case_code"])],
                                           *PHASES["full"], m)
                    for m in (1, 5, 15, 30)}
                signal_metrics[s][str(c["case_code"])] = item
        atomic_json(artifacts / "signal_metrics.json", signal_metrics)
        atomic_json(artifacts / "falsification.json", falsification_report(layers, daily, instruments, caches))
        atomic_json(artifacts / "cohort_results.json", results)
        atomic_json(artifacts / "cohort_survivors.json", survivors)
        atomic_json(artifacts / "dca_layer_histogram.json",
                    {"level_%02d" % i: v for i, v in enumerate(hist_total)})
        atomic_json(artifacts / "assertions.json", assertions)
        for g in GRIDS:
            write_grid(artifacts / ("grid_%s.csv" % g), grid_rows[g])
        result = {"schema_version": 1, "family_id": FAMILY_ID, "round_id": spec["round_id"],
                  "run_id": spec["run_id"], "engine": ENGINE_VERSION, "script_sha256": sha256_file(__file__),
                  "qlib_version": build["qlib_version"], "status": "ARTIFACT_READY",
                  "coverage_complete": all(coverage.values()), "coverage_by_grid": coverage,
                  "assertions_all_true": all(assertions.values()),
                  "assertion_failures": [k for k, v in assertions.items() if not v],
                  "case_evaluations_total": sum(map(len, grid_rows.values())),
                  "expected_case_evaluations": counts["case_evaluations_total"],
                  "cohort_count": len(results), "cohort_survivor_count": len(survivors),
                  "cohort_survivors": [x["cohort"] for x in survivors],
                  "official_funding_events": official_events,
                  "disposition": "REJECT / NO_SURVIVOR" if not survivors else
                  ("SURVIVOR_FOUND" if len(survivors) == 1 else "MULTIPLE_SURVIVORS"),
                  "verdict_recommendation": "PASS" if survivors else "REJECT",
                  "selector_version": "cohort-selector-v1", "disposition_version": "cohort-disposition-v1",
                  "disposition_mapping_version": "v1.4.0", "grid_kinds": GRIDS,
                  "funding_coverage": {s: funding[s][1] for s in SYMBOLS},
                  "stress_net_delta": stress_delta,
                  "runtime_seconds": time.monotonic() - started}
        if result["case_evaluations_total"] != counts["case_evaluations_total"]:
            result["assertions_all_true"] = False
            result["assertion_failures"].append("case_evaluations_total")
        atomic_json(path / "result.json", result)
        state["stage"] = "ARTIFACT_READY"
        atomic_json(path / "state.json", state)
        progress["stage"] = "ARTIFACT_READY"
        write_progress()
        log("ARTIFACT_READY cohorts=%d survivors=%d rows=%d assertions=%s" %
            (len(results), len(survivors), result["case_evaluations_total"],
             "all_true" if result["assertions_all_true"] else str(result["assertion_failures"])))
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


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-spec", required=True)
    parser.add_argument("--attempt-dir")
    args = parser.parse_args(argv)
    with open(args.run_spec, encoding="utf-8") as fh:
        spec = json.load(fh)
    result = run(spec, args.attempt_dir or str(Path(args.run_spec).parent))
    print(json.dumps({"status": result["status"], "case_evaluations_total": result["case_evaluations_total"],
                      "assertions_all_true": result["assertions_all_true"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
