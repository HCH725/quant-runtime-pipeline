#!/usr/bin/env python3
"""Direct-family VP-MACD: Qlib daily readback, causal signals, per-fill USD-M ledger.

Paper equity-market results are provenance, not claims about these four UTC perpetuals.
The source omits N and the range-STD window; both are explicitly frozen in r1.
Only historical cells select lambda/DCA. This runner writes recommendations, not verdicts.
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

FAMILY_ID = "volume-price-adjusted-macd-sensitivity-calibration-2026-09-02"
ENGINE_VERSION = "vp_macd_qlib_v1"
RAW_ROOT = "/data/raw"
RESULTS_ROOT = "/results"
WORK_ROOT = "/qlib/work/vp-macd-v1"
FIELDS = ("open", "high", "low", "close", "volume")
SYMBOLS = ("BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT")
TIMEFRAME = "1d"
# The canonical record explicitly fixes the 2023 OOS boundary and February 2026
# endpoint, so its split takes precedence over the body's default 2025 split.
# Local raw starts in 2022: 2018–2021 training data are absent, never imputed.
PHASES = {"historical": ("2022-01-01", "2022-12-31"),
          "oos": ("2023-01-01", "2026-02-28"),
          "full": ("2022-01-01", "2026-02-28")}
LAMBDA = tuple(round(0.8 + i * 0.02, 2) for i in range(11))
CASES = tuple({"lambda": x} for x in LAMBDA)
DCA_AXES = {"spacing_pct": (0.01, 0.02, 0.03, 0.04),
            "size_multiplier": (1.0, 1.1), "breakeven_tp_pct": (0.01, 0.02, 0.03),
            "invalidation_pct": (0.05, 0.10)}
DCA_GRID = tuple(dict(zip(DCA_AXES, vals)) for vals in itertools.product(*DCA_AXES.values()))
GRIDS = ("historical", "oos", "full", "fee_2x", "funding_2x", "entry_delay_1_bar",
         "slippage_2ticks", "no_funding", "no_funding_full", "cost_attrition_40bps")
MIN_EPISODES_IS = 6  # research-defined: source reports just 6 SPY trades in 38 months
N = 10               # source underspecified; chosen before computation, not tuned on OOS
SIGMA_WINDOW = 10    # likewise; sample population STD (ddof=0)
N_PERTURB = (5, 10, 15, 20, 30)
SIGMA_PERTURB = (5, 10, 20)
FALSIFICATION = [
    "Component ablation: volume-only, volatility-only, body-only, and MACD+lambda; volume-only or MACD+lambda OOS Sharpe >= VP-MACD falsifies synergy",
    "Lookback perturbation: N in [5,10,15,20,30] x sigma window in [5,10,20]; adjacent OOS Sharpe drop >50% falsifies stability",
    "Out-of-sample asset universe extension: large-cap AAPL/MSFT/JNJ/XOM and CL/GC/ZN; mean OOS Sharpe <=0 falsifies generality; unavailable local assets are INDETERMINATE, never substituted",
    "Friction and execution lag: 15/25bps round-trip fees and t+1 close execution; edge vanishing at 15bps falsifies tradability; absent equity close auction remains INDETERMINATE",
]
START_EQUITY = 30_000.0
LEVERAGE = 10.0
BASE_QUOTE = 1_000.0
MAX_ADD_LEVELS = 10     # tranche 1 plus 10 adds; tranche 12 remains reserve
MS_DAY = 86_400_000
TRACE = None              # inert §28 replay hook; never populated during grid search


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return "sha256:" + h.hexdigest()


def stamp(ms):
    return dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).strftime("%Y-%m-%d")


def utc_ms(day):
    return int(dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def publish(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "x", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False, allow_nan=False)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def expected():
    per = len(SYMBOLS) * len(CASES) * len(DCA_GRID)
    return {"cohorts": len(SYMBOLS), "strategy_cases_per_cohort": len(CASES),
            "dca_configs_per_cohort": len(DCA_GRID),
            "base_combinations_per_cohort": len(CASES) * len(DCA_GRID),
            "case_evaluations_per_grid": per, "grid_count": len(GRIDS),
            "case_evaluations_total": per * len(GRIDS)}


def outputs():
    return ["state.json", "result.json", "logs/run.log", "artifacts/progress.json",
            "artifacts/raw_build.json", "artifacts/funding_coverage.json",
            "artifacts/signal_metrics.json", "artifacts/falsification.json",
            "artifacts/stress_effects.json", "artifacts/dca_layer_histogram.json",
            "artifacts/cohort_results.json", "artifacts/cohort_survivors.json",
            "artifacts/assertions.json", *("artifacts/grid_%s.csv" % g for g in GRIDS)]


def check_spec(spec):
    rid, uid = spec.get("round_id"), spec.get("run_id")
    if spec.get("family_id") != FAMILY_ID or not isinstance(rid, str) or not re.fullmatch(
            re.escape(FAMILY_ID) + r"-r[1-9][0-9]*", rid) or not isinstance(uid, str) or not re.fullmatch(
            re.escape(rid) + r"-u[1-9][0-9]*", uid):
        raise ValueError("family/round/run identity mismatch")
    if any(k in spec for k in ("task_id", "kanban_task_id", "kanban_board")):
        raise ValueError("direct family must not carry task ownership")
    if spec.get("script") != {"path": "/scripts/290_vp_macd_run.py", "sha256": sha(__file__)}:
        raise ValueError("unverified script bytes")
    if spec.get("engine", {}).get("qlib_version") != "0.9.7":
        raise ValueError("wrong Qlib version")
    if spec.get("data") != {"raw_root": "/data/raw/binance/usdm", "fields": list(FIELDS),
                             "symbols": list(SYMBOLS), "timeframe": TIMEFRAME,
                             "start": PHASES["full"][0], "end": PHASES["full"][1]}:
        raise ValueError("raw/scope changed")
    if spec.get("split") != {k: list(v) for k, v in PHASES.items()}:
        raise ValueError("frozen split changed")
    if spec.get("params") != list(CASES) or spec.get("expected") != expected() or spec.get("expected_outputs") != outputs():
        raise ValueError("registered domain/coverage changed")
    if spec.get("falsification") != FALSIFICATION or spec.get("gates") != {
            "min_episodes_is": MIN_EPISODES_IS, "min_neighbour_same_sign_fraction": 0.6}:
        raise ValueError("falsification/selector gate changed")
    if spec.get("signal_constants") != {"N": N, "sigma_window": SIGMA_WINDOW, "std_ddof": 0,
                                            "ema_periods": [12, 26, 9], "n_perturb": list(N_PERTURB),
                                            "sigma_perturb": list(SIGMA_PERTURB)}:
        raise ValueError("signal constants changed")
    domain = spec.get("dca_domain", {})
    if (domain.get("grid") != list(DCA_GRID) or domain.get("base_quote") != BASE_QUOTE
            or domain.get("base_quote_status") != "PROJECT_PRE_REGISTERED_CONSTANT"
            or any(domain.get(k) != list(v) or domain.get(k + "_status") !=
                   "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN" for k, v in DCA_AXES.items())):
        raise ValueError("DCA domain/provenance changed")
    if spec.get("selector_version") != "cohort-selector-v1" or spec.get("disposition_version") != "cohort-disposition-v1":
        raise ValueError("selector/disposition changed")
    with open(RAW_ROOT + "/_meta/CONFIG.json", encoding="utf-8") as f:
        cat = json.load(f)
    if (cat.get("market_type") != "usdm_perp" or sorted(cat.get("symbols", [])) != list(SYMBOLS)
            or TIMEFRAME not in cat.get("intervals", []) or "klines" not in cat.get("datasets", {})):
        raise ValueError("canonical catalog diverged")


def instruments():
    path = Path(RAW_ROOT) / "binance/usdm/instruments/usdm-perp-instruments.json"
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    by_symbol = {r["fields"]["raw_symbol"]: r["fields"] for r in doc["instruments"]}
    result = {}
    for s in SYMBOLS:
        f = by_symbol[s]
        if f["type"] != "CryptoPerpetual" or f["quote_currency"] != "USDT" or f["is_inverse"]:
            raise ValueError("not a linear USDT perp: " + s)
        tick, fee = float(f["price_increment"]), float(f["taker_fee"])
        if not (tick > 0 and 0 <= fee < 0.01):
            raise ValueError("invalid instrument metadata: " + s)
        result[s] = {"tick": tick, "taker_fee": fee, "source_sha256": sha(path)}
    return result


def raw_panel(symbol):
    root = Path(RAW_ROOT) / "binance/usdm/klines" / symbol / TIMEFRAME
    files = [p for p in sorted(root.glob(symbol + "-1d-*.jsonl.gz"))
             if PHASES["full"][0][:7] <= p.name.split("-1d-")[1][:7] <= PHASES["full"][1][:7]]
    if not files:
        raise RuntimeError("no canonical 1d shards: " + symbol)
    times, vals = [], {k: [] for k in FIELDS}
    for path in files:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                row = json.loads(line)
                t = int(row["open_time_ms"])
                if not utc_ms(PHASES["full"][0]) <= t <= utc_ms(PHASES["full"][1]):
                    continue
                if times and t - times[-1] != MS_DAY:
                    raise RuntimeError("daily gap/duplicate %s/%s" % (symbol, stamp(t)))
                for k in FIELDS:
                    v = float(row[k])
                    if not math.isfinite(v) or (v <= 0 if k != "volume" else v < 0):
                        raise RuntimeError("invalid %s/%s" % (symbol, k))
                    vals[k].append(v)
                times.append(t)
    if not times or times[0] != utc_ms(PHASES["full"][0]) or times[-1] != utc_ms(PHASES["full"][1]):
        raise RuntimeError("incomplete 1d window: " + symbol)
    if any(vals["high"][i] < max(vals["open"][i], vals["close"][i]) or
           vals["low"][i] > min(vals["open"][i], vals["close"][i]) or
           vals["low"][i] > vals["high"][i] for i in range(len(times))):
        raise RuntimeError("invalid OHLC geometry: " + symbol)
    return {"open_ms": np.asarray(times, np.int64),
            **{k: np.asarray(v, np.float64) for k, v in vals.items()},
            "raw_files": [{"path": str(p), "sha256": sha(p)} for p in files]}


def qlib_readback(daily):
    work = Path(WORK_ROOT)
    if work.exists():
        shutil.rmtree(work)
    csv_root, qlib_root = work / "csv", work / "qlib-data"
    csv_root.mkdir(parents=True)
    for s, panel in daily.items():
        with open(csv_root / (s + ".csv"), "w", newline="", encoding="utf-8") as f:
            wr = csv.writer(f)
            wr.writerow(["date", *FIELDS])
            for i, ms in enumerate(panel["open_ms"]):
                wr.writerow([stamp(ms), *(panel[k][i] for k in FIELDS)])
    proc = subprocess.run(["/opt/venv/bin/python", "/opt/qlib-tools/dump_bin.py", "dump_all",
                           "--data_path", str(csv_root), "--qlib_dir", str(qlib_root), "--freq", "day",
                           "--include_fields", ",".join(FIELDS), "--date_field_name", "date",
                           "--max_workers", "1"], capture_output=True, text=True, timeout=3600)
    if proc.returncode:
        raise RuntimeError("Qlib dump_bin failed: " + proc.stderr[-700:])
    import qlib
    from qlib.data import D
    if qlib.__version__ != "0.9.7":
        raise RuntimeError("wrong Qlib version")
    qlib.init(provider_uri=str(qlib_root), region="cn", expression_cache=None, dataset_cache=None)
    readback = {}
    for s, p in daily.items():
        df = D.features([s], ["$" + k for k in FIELDS], start_time=PHASES["full"][0],
                        end_time=PHASES["full"][1] + " 23:59:59", freq="day").sort_index()
        ms = np.asarray([int(x.value // 1_000_000) for x in df.index.get_level_values("datetime")], np.int64)
        if not np.array_equal(ms, p["open_ms"]):
            raise RuntimeError("Qlib timestamp mismatch: " + s)
        cols = {k: df["$" + k].to_numpy(dtype=np.float64) for k in FIELDS}
        for k in FIELDS:
            if not np.isfinite(cols[k]).all() or not np.allclose(cols[k], p[k], rtol=1e-6, atol=1e-9):
                raise RuntimeError("Qlib value mismatch: %s/%s" % (s, k))
        readback[s] = {"open_ms": ms, **cols}
    return readback, {"qlib_version": qlib.__version__, "read_path": "qlib.data.D.features",
                      "qlib_rows_identical_to_raw": True,
                      "daily_rows": {s: len(daily[s]["open_ms"]) for s in SYMBOLS},
                      "float32_roundtrip_rtol": 1e-6}


def funding_events(symbol, ms):
    path = Path(RAW_ROOT) / "binance/usdm/funding" / symbol / (symbol + "-funding.jsonl.gz")
    events = [[] for _ in ms]
    report = {"file_missing": not path.is_file(), "official": 0, "modeled_ignored": 0,
              "other_ignored": 0, "missing_intervals_are_zero_not_modeled": True,
              "days_total": len(ms), "days_with_official": 0}
    if not path.is_file():
        return events, report
    report["source_sha256"] = sha(path)
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            t = int(row["funding_time_ms"])
            if not int(ms[0]) <= t < int(ms[-1]) + MS_DAY:
                continue
            if row.get("truth_status") != "official":
                report["modeled_ignored" if row.get("truth_status") in ("modeled", "modeled_funding")
                       else "other_ignored"] += 1
                continue
            rate, mark = float(row["funding_rate"]), float(row["mark_price"])
            if not math.isfinite(rate) or not math.isfinite(mark) or mark <= 0:
                raise RuntimeError("invalid official funding")
            index = int(np.searchsorted(ms, t, side="right") - 1)
            if not 0 <= index < len(events):
                raise RuntimeError("funding outside panel")
            events[index].append((t, rate * mark))
            report["official"] += 1
    report["days_with_official"] = sum(bool(x) for x in events)
    return events, report


def ema(values, period):
    result = np.full(len(values), np.nan)
    a = 2.0 / (period + 1)
    prev = None
    for i, value in enumerate(values):
        if not math.isfinite(float(value)):
            continue
        prev = float(value) if prev is None else prev + a * (float(value) - prev)
        result[i] = prev
    return result


def adjusted(panel, n=N, sigma_window=SIGMA_WINDOW, variant="full"):
    op, high, low, close, volume = (panel[k] for k in FIELDS)
    if variant == "standard":
        return close.copy()
    span = high - low
    sigma = np.full(len(close), np.nan)
    for i in range(sigma_window - 1, len(close)):
        sigma[i] = np.std(span[i - sigma_window + 1:i + 1], ddof=0) / close[i]
    ratio = np.divide(np.abs(close - op), span, out=np.zeros_like(close), where=span > 0)
    if variant == "volume":
        return close * volume
    if variant == "volatility":
        return close * sigma
    if variant == "body":
        return close * ratio
    if variant != "full":
        raise ValueError("unknown ablation")
    weighted = close * volume * sigma * ratio
    out = np.full(len(close), np.nan)
    # Equation (8) uses [t-N, t-1], never t; no forming bar is read.
    for t in range(n + sigma_window - 1, len(close)):
        a, b = t - n, t
        denominator = float(np.sum(volume[a:b]))
        if denominator > 0 and np.isfinite(weighted[a:b]).all():
            out[t] = float(np.sum(weighted[a:b])) / denominator
    return out


def signals(panel, lam, n=N, sigma_window=SIGMA_WINDOW, variant="full"):
    price = adjusted(panel, n=n, sigma_window=sigma_window, variant=variant)
    macd = ema(price, 12) - ema(price, 26)
    line = ema(macd, 9)
    entry, exit_ = np.zeros(len(price), bool), np.zeros(len(price), bool)
    for i in range(1, len(price)):
        if np.isfinite([macd[i - 1], line[i - 1], macd[i], line[i]]).all():
            entry[i] = macd[i - 1] <= lam * line[i - 1] and macd[i] > lam * line[i]
            exit_[i] = macd[i - 1] >= line[i - 1] and macd[i] < line[i]
    return entry, exit_


def metric_fields(m):
    return {k: m[k] for k in ("gross_pnl", "fees", "funding", "net_pnl", "ending_equity",
            "episodes", "fills", "adds", "turnover_usdt", "sharpe", "max_dd_pct", "max_dd_usdt",
            "annualized_return", "max_effective_leverage", "capital_utilization", "tp_hits",
            "stop_hits", "margin_calls", "end_exits", "signal_exits")}


def decomposition_ok(m):
    return abs(m["gross_pnl"] - m["fees"] - m["funding"] - m["net_pnl"]) < 1e-3 and abs(
        m["net_pnl"] - (m["ending_equity"] - START_EQUITY)) < 1e-3


def simulate(panel, funding, signal, dca, start, end, cost, inst):
    """Causal long/flat, stop-first conservative daily ordering; all fills costed now.

    Funding is settled at its official timestamp where the daily bar cannot reveal
    the intraday exit ordering: charge before the stop/TP (conservative disclosure).
    """
    entry, exit_ = signal
    if not 0 <= start < end <= len(panel["open_ms"]):
        raise ValueError("invalid phase indices")
    daily = np.zeros(end - start)
    realized = gross = fees = funded = turnover = 0.0
    qty = basis = first_price = quote0 = 0.0
    next_level = layers = 0
    eps = fills = adds = tp_hits = stop_hits = margin_calls = end_exits = signal_exits = 0
    max_lev = max_util = 0.0
    hist = [0] * 12
    tick = inst["tick"] * cost.get("slip_ticks", 1)
    rate = cost.get("fee_override", inst["taker_fee"] * cost.get("fee_mult", 1.0))
    delay = cost.get("entry_delay", 0)
    if rate < 0:
        raise ValueError("negative fee")

    def flatten(price, ix, reason):
        nonlocal qty, basis, gross, realized, fees, turnover, fills
        nonlocal signal_exits, end_exits, layers
        pnl = qty * price - basis   # independent price-only accumulator, not net reversal
        fee = qty * price * rate
        gross += pnl
        realized += pnl - fee
        fees += fee
        turnover += qty * price
        fills += 1
        daily[ix] += pnl - fee
        hist[0] += 1
        hist[layers] += 1
        if TRACE is not None:
            TRACE.append({"kind": "exit", "bar": start + ix, "price": price, "qty": qty,
                          "fee": fee, "gross": pnl, "reason": reason, "layers": layers})
        if reason == "signal":
            signal_exits += 1
        if reason == "end":
            end_exits += 1
        qty = basis = 0.0
        layers = 0

    for i in range(start, end):
        j = i - start
        op, high, low, close = (float(panel[k][i]) for k in ("open", "high", "low", "close"))
        # Exits keep original timing under entry-delay stress. An exit at the open
        # cannot be followed by a scale-in; a later fresh crossing is required.
        if qty > 0 and i > 0 and exit_[i - 1]:
            flatten(op - tick, j, "signal")
        sig_i = i - 1 - delay
        if qty == 0 and sig_i >= 0 and entry[sig_i] and START_EQUITY + realized > 0:
            price = op + tick
            quote0 = BASE_QUOTE * LEVERAGE
            fee = quote0 * rate
            if START_EQUITY + realized - fee > quote0 / LEVERAGE:
                qty = quote0 / price
                basis = qty * price
                realized -= fee
                fees += fee
                turnover += quote0
                fills += 1
                daily[j] -= fee
                first_price = price
                next_level = layers = 1
                eps += 1
                if TRACE is not None:
                    TRACE.append({"kind": "entry", "bar": i, "price": price, "qty": qty,
                                  "fee": fee, "layers": layers})
        if qty > 0:
            if not cost.get("no_funding"):
                for event_t, amount in funding[i]:
                    if event_t <= int(panel["open_ms"][i]):
                        continue
                    payment = qty * amount * cost.get("funding_mult", 1.0)
                    funded += payment
                    realized -= payment
                    daily[j] -= payment
                    if TRACE is not None:
                        TRACE.append({"kind": "funding", "bar": i, "timestamp_ms": event_t,
                                      "amount": payment})
            prior_avg = basis / qty
            stop = prior_avg * (1 - dca["invalidation_pct"])
            # Intrabar path unknown; if both resting stop and scale-ins cross, stop
            # precedes all adds. Gap through the stop fills at the adverse open.
            if op <= stop or low <= stop:
                flatten((op if op <= stop else stop) - tick, j, "stop")
                stop_hits += 1
            elif START_EQUITY + realized + qty * (low - prior_avg) <= 0:
                flatten(low - tick, j, "margin")
                margin_calls += 1
            else:
                while qty > 0 and next_level <= MAX_ADD_LEVELS:
                    trigger = first_price * (1 - dca["spacing_pct"] * next_level)
                    if low > trigger:
                        break
                    price = trigger + tick
                    quote = BASE_QUOTE * LEVERAGE * dca["size_multiplier"] ** next_level
                    fee = quote * rate
                    eq = START_EQUITY + realized + qty * price - basis - fee
                    new_qty = quote / price
                    if eq <= 0 or (qty + new_qty) * price / LEVERAGE > eq:
                        flatten(price - tick, j, "margin")
                        margin_calls += 1
                        break
                    qty += new_qty
                    basis += quote
                    realized -= fee
                    fees += fee
                    turnover += quote
                    fills += 1
                    adds += 1
                    daily[j] -= fee
                    layers += 1
                    next_level += 1
                    max_lev = max(max_lev, qty * price / eq)
                    max_util = max(max_util, qty * price / LEVERAGE / eq)
                    if TRACE is not None:
                        TRACE.append({"kind": "add", "bar": i, "price": price,
                                      "qty": new_qty, "fee": fee, "layers": layers})
                if qty > 0:
                    avg = basis / qty
                    new_stop = avg * (1 - dca["invalidation_pct"])
                    take = avg * (1 + dca["breakeven_tp_pct"])
                    if low <= new_stop:
                        flatten(new_stop - tick, j, "stop")
                        stop_hits += 1
                    elif high >= take:
                        flatten(take - tick, j, "tp")
                        tp_hits += 1
            if qty > 0 and i == end - 1:
                flatten(close - tick, j, "end")
        unreal = qty * close - basis if qty else 0.0
        equity_now = START_EQUITY + realized + unreal
        if qty and equity_now > 0:
            max_lev = max(max_lev, qty * close / equity_now)
            max_util = max(max_util, qty * close / LEVERAGE / equity_now)
        if TRACE is not None:
            TRACE.append({"kind": "equity", "bar": i, "equity": equity_now,
                          "realized": START_EQUITY + realized, "unrealized": unreal})
        # The cumulative realised array alone misses a still-open unrealised leg.
        daily[j] = equity_now
    equity = daily
    highwater = np.maximum.accumulate(np.r_[START_EQUITY, equity])
    dd = highwater[1:] - equity
    prior = np.r_[START_EQUITY, equity[:-1]]
    returns = np.diff(np.r_[START_EQUITY, equity]) / np.maximum(prior, 1e-9)
    sd = float(np.std(returns, ddof=1)) if len(returns) > 1 else 0.0
    sharpe = float(np.mean(returns) / sd * math.sqrt(365)) if sd > 1e-12 else 0.0
    annual = (max(float(equity[-1]), 1e-9) / START_EQUITY) ** (365 / len(equity)) - 1
    m = {"gross_pnl": float(gross), "fees": float(fees), "funding": float(funded),
         "net_pnl": float(realized), "ending_equity": float(equity[-1]), "episodes": eps,
         "fills": fills, "adds": adds, "turnover_usdt": float(turnover), "sharpe": sharpe,
         "max_dd_pct": float(np.max(dd / highwater[1:]) * 100),
         "max_dd_usdt": float(np.max(dd)), "annualized_return": float(annual),
         "max_effective_leverage": float(max_lev), "capital_utilization": float(max_util),
         "tp_hits": tp_hits, "stop_hits": stop_hits, "margin_calls": margin_calls,
         "end_exits": end_exits, "signal_exits": signal_exits, "layer_hist": hist,
         "daily_equity": equity.tolist()}
    m["decomposition_ok"] = decomposition_ok(m)
    return m


def cell_key(row):
    return (row["symbol"], row["timeframe"], row["lambda"], *(row[k] for k in DCA_AXES))


def order_key(r):
    return (-r["sharpe"], -r["net_pnl"], LAMBDA.index(r["lambda"]),
            *(DCA_AXES[k].index(r[k]) for k in DCA_AXES))


def neighbours(winner, hist):
    if winner["grid"] != "historical" or any(r["grid"] != "historical" for r in hist):
        raise ValueError("OOS entered historical selector")
    found = {cell_key(r): r for r in hist}
    key = list(cell_key(winner))
    axes = (LAMBDA, *DCA_AXES.values())
    agree = total = 0
    for pos, domain in enumerate(axes, start=2):
        idx = domain.index(key[pos])
        for delta in (-1, 1):
            if 0 <= idx + delta < len(domain):
                neighbour = key.copy()
                neighbour[pos] = domain[idx + delta]
                agree += found[tuple(neighbour)]["net_pnl"] > 0
                total += 1
    fraction = agree / total if total else 0.0
    return {"neighbours": total, "agreeing": agree, "same_sign_fraction": fraction,
            "passed": bool(total and fraction >= 0.6)}


def select(rows):
    hist = rows["historical"]
    if not hist or any(r["grid"] != "historical" for r in hist):
        raise ValueError("OOS entered historical selector")
    best = max(int(r["episodes"]) for r in hist)
    if best < MIN_EPISODES_IS:
        return None, {"best_historical_episodes": best, "cull_reasons": ["insufficient_trades"]}
    qualifying = [r for r in hist if r["episodes"] >= MIN_EPISODES_IS
                  and r["net_pnl"] > 0 and r["sharpe"] > 0]
    if not qualifying:
        return None, {"best_historical_episodes": best, "cull_reasons": ["no_qualifying_candidate"]}
    winner = min(qualifying, key=order_key)
    matches = {g: next(r for r in rows[g] if cell_key(r) == cell_key(winner)) for g in GRIDS}
    reasons = []
    if matches["oos"]["net_pnl"] <= 0 or matches["oos"]["sharpe"] <= 0:
        reasons.append("oos_economic")
    if matches["full"]["net_pnl"] <= 0:
        reasons.append("full_economic")
    weak = [g for g in ("fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks")
            if matches[g]["net_pnl"] <= 0]
    if weak:
        reasons.append("robustness_economic:" + ",".join(weak))
    hood = neighbours(winner, hist)
    if not hood["passed"]:
        reasons.append("parameter_neighbourhood")
    phases = {g: metric_fields(matches[g]) for g in ("historical", "oos", "full")}
    robustness = {g: metric_fields(matches[g]) for g in
                  ("fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks")}
    detail = {"winner": {k: winner[k] for k in ("lambda", *DCA_AXES)},
              "winner_source_grid": "historical", "best_historical_episodes": best,
              "neighbourhood": hood, "phases": phases, "robustness": robustness,
              "metrics": {**phases, "robustness": robustness, "neighbourhood": hood},
              "cull_reasons": reasons}
    return (winner if not reasons else None), detail


def cost_for(grid):
    return {"fee_2x": {"fee_mult": 2.0}, "funding_2x": {"funding_mult": 2.0},
            "entry_delay_1_bar": {"entry_delay": 1}, "slippage_2ticks": {"slip_ticks": 2},
            "no_funding": {"no_funding": True}, "no_funding_full": {"no_funding": True},
            "cost_attrition_40bps": {"fee_override": 0.004}}.get(grid, {})


def grid_csv(path, rows):
    cols = ["symbol", "timeframe", "lambda", *DCA_AXES, "grid", *metric_fields(rows[0]),
            "decomposition_ok"]
    with open(path, "x", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, cols, extrasaction="ignore")
        wr.writeheader()
        wr.writerows(rows)


def falsify(panel, events, inst, winner, window):
    """Same frozen lambda/DCA, OOS only; no alternative may replace the winner."""
    lam, dca = winner["lambda"], {k: winner[k] for k in DCA_AXES}
    ablations = {}
    for v in ("volume", "volatility", "body", "standard"):
        m = simulate(panel, events, signals(panel, lam, variant=v), dca, *window, {}, inst)
        ablations[v] = {"sharpe": m["sharpe"], "net_pnl": m["net_pnl"],
                        "episodes": m["episodes"]}
    synergy_hit = any(ablations[v]["sharpe"] >= winner["sharpe"]
                      for v in ("volume", "standard"))
    neighbourhood, signal_cache = {}, {}
    for n, sw in itertools.product(N_PERTURB, SIGMA_PERTURB):
        signal = signal_cache.get((n, sw))
        if signal is None:
            signal = signals(panel, lam, n=n, sigma_window=sw)
            signal_cache[(n, sw)] = signal
        m = simulate(panel, events, signal, dca, *window, {}, inst)
        neighbourhood["%s/%s" % (n, sw)] = {"sharpe": m["sharpe"], "net_pnl": m["net_pnl"]}
    drop_pairs = []
    for n, sw in itertools.product(N_PERTURB, SIGMA_PERTURB):
        here = neighbourhood["%s/%s" % (n, sw)]["sharpe"]
        for nn, ss in ((N_PERTURB[N_PERTURB.index(n) + 1], sw) if n != N_PERTURB[-1] else (n, sw),
                       (n, SIGMA_PERTURB[SIGMA_PERTURB.index(sw) + 1]) if sw != SIGMA_PERTURB[-1] else (n, sw)):
            if (nn, ss) == (n, sw):
                continue
            other = neighbourhood["%s/%s" % (nn, ss)]["sharpe"]
            if max(here, other) > 0 and min(here, other) < max(here, other) * 0.5:
                drop_pairs.append([n, sw, nn, ss])
    # Source battery items 3/4 are registered even though broad equity/commodity
    # cross-section and exact closing-auction replay are not in the local store.
    # The 15/25 bps scenarios below charge per side of the round-trip; the
    # already-frozen 1-tick adverse fill is additional, not silently removed.
    cost_stress = {}
    baseline_signal = signals(panel, lam)
    for bps in (15, 25):
        tested = simulate(panel, events, baseline_signal, dca, *window,
                          {"fee_override": bps / 2 / 10_000}, inst)
        cost_stress[str(bps)] = {"net_pnl": tested["net_pnl"],
                                 "sharpe": tested["sharpe"]}
    return {"ablation": ablations, "synergy_falsified": synergy_hit,
            "lookback_perturb": neighbourhood, "adjacent_sharpe_drop_gt_50pct": drop_pairs,
            "lookback_falsified": bool(drop_pairs),
            "universe_extension": {"status": "INDETERMINATE",
                                   "reason": "registered AAPL/MSFT/JNJ/XOM and CL/GC/ZN datasets absent from local canonical raw; no substitutes"},
            "friction_lag": {"cost_15_25bps": cost_stress,
                              "non_tradable_at_15bps": winner["net_pnl"] > 0 and cost_stress["15"]["net_pnl"] <= 0,
                              "next_day_close_execution": "INDETERMINATE: exact closing-auction order fill not represented by local UTC perp OHLCV"}}


def run(spec, out):
    check_spec(spec)
    out = Path(out)
    target = Path(RESULTS_ROOT) / FAMILY_ID / "rounds" / spec["round_id"] / "attempts" / spec["run_id"]
    if out != target or any((out / x).exists() for x in ("DONE", "FAILED", "INCOMPLETE", "result.json", "state.json")):
        raise ValueError("attempt path/immutable evidence violation")
    art = out / "artifacts"
    art.mkdir(parents=True, exist_ok=True)
    (out / "logs").mkdir(exist_ok=True)
    started = time.monotonic()
    state = {"schema_version": 1, "family_id": FAMILY_ID, "round_id": spec["round_id"],
             "run_id": spec["run_id"], "stage": "RUNNING_QLIB"}
    publish(out / "state.json", state)
    progress = {**state, "case_evaluations": 0, "expected_case_evaluations": expected()["case_evaluations_total"]}

    def tick():
        progress["updated_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        progress["runtime_seconds"] = time.monotonic() - started
        publish(art / "progress.json", progress)

    tick()
    try:
        inst = instruments()
        daily = {s: raw_panel(s) for s in SYMBOLS}
        panels, build = qlib_readback(daily)
        publish(art / "raw_build.json", {"qlib": build,
                "daily_files": {s: daily[s]["raw_files"] for s in SYMBOLS}})
        funding = {s: funding_events(s, panels[s]["open_ms"]) for s in SYMBOLS}
        publish(art / "funding_coverage.json", {s: funding[s][1] for s in SYMBOLS})
        grid_rows = {g: [] for g in GRIDS}
        records, survivors = [], []
        hist_total = [0] * 12
        signal_report, falsification = {}, {}
        for s in SYMBOLS:
            panel, ev = panels[s], funding[s][0]
            windows = {k: (int(np.searchsorted(panel["open_ms"], utc_ms(a))),
                           int(np.searchsorted(panel["open_ms"], utc_ms(b) + MS_DAY)))
                       for k, (a, b) in PHASES.items()}
            if windows["historical"][1] != windows["oos"][0]:
                raise RuntimeError("non-contiguous split")
            rows = {g: [] for g in GRIDS}
            for case in CASES:
                lam = case["lambda"]
                sig = signals(panel, lam)
                signal_report["%s/%s" % (s, lam)] = {
                    "entry_crossings": int(sig[0].sum()), "exit_crossings": int(sig[1].sum()),
                    "first_decidable": int(np.flatnonzero(np.isfinite(adjusted(panel)))[0]),
                    "pit": "P*_t strictly excludes bar t; cross formed after bar t; fill at t+1 open"}
                for dca in DCA_GRID:
                    for grid in GRIDS:
                        phase = ("historical" if grid in ("historical", "no_funding") else
                                 "oos" if grid == "oos" else "full")
                        m = simulate(panel, ev, sig, dca, *windows[phase], cost_for(grid), inst[s])
                        r = {"symbol": s, "timeframe": TIMEFRAME, "lambda": lam,
                             **dca, "grid": grid, **metric_fields(m),
                             "decomposition_ok": m["decomposition_ok"]}
                        rows[grid].append(r)
                        grid_rows[grid].append(r)
                        if grid == "full":
                            hist_total = [a + b for a, b in zip(hist_total, m["layer_hist"])]
                        progress["case_evaluations"] += 1
                        if progress["case_evaluations"] % 500 == 0:
                            tick()
            selected, detail = select(rows)
            if "winner" in detail:
                win = detail["winner"]
                oos = next(r for r in rows["oos"] if all(r[k] == win[k] for k in win))
                f = falsify(panel, ev, inst[s], oos, windows["oos"])
                falsification[s] = f
                if (f["synergy_falsified"] or f["lookback_falsified"] or
                        f["friction_lag"]["non_tradable_at_15bps"]):
                    detail["cull_reasons"].append("falsification:" + ",".join(
                        k for k, val in (("component_ablation", f["synergy_falsified"]),
                                       ("lookback_perturbation", f["lookback_falsified"]),
                                       ("friction_15bps", f["friction_lag"]["non_tradable_at_15bps"])) if val))
                    selected = None
                detail["falsification_battery"] = {"component_ablation": f["synergy_falsified"],
                                                   "lookback_perturbation": f["lookback_falsified"],
                                                   "universe_extension": "INDETERMINATE",
                                                   "friction_15bps": f["friction_lag"]["non_tradable_at_15bps"],
                                                   "execution_at_next_close": "INDETERMINATE"}
            else:
                falsification[s] = {"status": "NO_HISTORICAL_WINNER", "reason": detail["cull_reasons"]}
            rec = {"cohort": s + "/" + TIMEFRAME, "symbol": s,
                   "outcome": "SURVIVOR" if selected is not None else "CULLED", **detail}
            records.append(rec)
            if selected is not None:
                survivors.append(rec)
            tick()
            print("cohort %s: %s %s" % (s, rec["outcome"], rec["cull_reasons"]), flush=True)
        publish(art / "signal_metrics.json", signal_report)
        publish(art / "falsification.json", falsification)
        keys = {(s, TIMEFRAME, c["lambda"], *(d[k] for k in DCA_AXES))
                for s in SYMBOLS for c in CASES for d in DCA_GRID}
        coverage = {g: len(grid_rows[g]) == len(keys) and {cell_key(r) for r in grid_rows[g]} == keys
                    for g in GRIDS}
        full = grid_rows["full"]
        stressed = {g: sum(r["net_pnl"] for r in grid_rows[g]) - sum(r["net_pnl"] for r in full)
                    for g in ("fee_2x", "funding_2x", "slippage_2ticks", "cost_attrition_40bps")}
        traded = any(r["fills"] for r in full)
        assertions = {
            "qlib_readback_0_9_7": build["qlib_version"] == "0.9.7" and build["qlib_rows_identical_to_raw"],
            "coverage_complete": all(coverage.values()),
            "case_evaluations_total_exact": progress["case_evaluations"] == expected()["case_evaluations_total"],
            "independent_gross_net_decomposition": all(r["decomposition_ok"] for g in GRIDS for r in grid_rows[g]),
            "dca_histogram_reconciles": hist_total[0] == sum(r["episodes"] for r in full),
            "cost_stress_effective": (not traded or all(any(
                abs(a["net_pnl"] - b["net_pnl"]) > 1e-9
                for a, b in zip(full, grid_rows[g])) for g in
                ("fee_2x", "slippage_2ticks", "cost_attrition_40bps"))),
            "funding_2x_effective": (not any(abs(r["funding"]) > 1e-9 for r in full) or
                any(abs(a["net_pnl"] - b["net_pnl"]) > 1e-9
                    for a, b in zip(full, grid_rows["funding_2x"]) if abs(a["funding"]) > 1e-9)),
            "official_funding_only": all(funding[s][1]["missing_intervals_are_zero_not_modeled"] for s in SYMBOLS),
            "historical_only_selector": all(x.get("winner_source_grid") == "historical"
                                             for x in records if x.get("winner")),
            "falsification_battery_evaluated": all(s in falsification for s in SYMBOLS),
        }
        publish(art / "stress_effects.json", {"net_delta_vs_full": stressed, "any_fill": traded,
                "no_op_reason": None if traded else "no fills; cost deltas not observable"})
        publish(art / "cohort_results.json", records)
        publish(art / "cohort_survivors.json", survivors)
        publish(art / "dca_layer_histogram.json", {"level_%02d" % i: x for i, x in enumerate(hist_total)})
        for g in GRIDS:
            grid_csv(art / ("grid_%s.csv" % g), grid_rows[g])
        publish(art / "assertions.json", assertions)
        result = {"schema_version": 1, "family_id": FAMILY_ID, "round_id": spec["round_id"],
                  "run_id": spec["run_id"], "engine": ENGINE_VERSION, "script_sha256": sha(__file__),
                  "status": "ARTIFACT_READY", "qlib_version": build["qlib_version"],
                  "coverage_complete": all(coverage.values()), "coverage_by_grid": coverage,
                  "assertions_all_true": all(assertions.values()),
                  "assertion_failures": [k for k, ok in assertions.items() if not ok],
                  "case_evaluations_total": progress["case_evaluations"],
                  "expected_case_evaluations": expected()["case_evaluations_total"],
                  "cohort_count": len(records), "cohort_survivor_count": len(survivors),
                  "cohort_survivors": [r["cohort"] for r in survivors],
                  "selector_version": "cohort-selector-v1", "disposition_version": "cohort-disposition-v1",
                  "disposition_mapping_version": "v1.4.0", "grid_kinds": list(GRIDS),
                  "official_funding_events": {s: funding[s][1]["official"] for s in SYMBOLS},
                  "stress_net_delta": stressed,
                  "disposition": "NO_SURVIVOR" if not survivors else
                    "SURVIVOR_FOUND" if len(survivors) == 1 else "MULTIPLE_SURVIVORS",
                  "verdict_recommendation": "TECHNICAL_INCOMPLETE" if not all(assertions.values()) else
                    "REJECT" if not survivors else "PASS",
                  "claimability_note": "§9.6 and final verdict are evaluated host-side by default",
                  "runtime_seconds": time.monotonic() - started}
        publish(out / "result.json", result)
        state["stage"] = progress["stage"] = "ARTIFACT_READY"
        publish(out / "state.json", state)
        tick()
        return result
    except Exception as exc:
        state.update(stage="FAILED_SCRIPT", error="%s: %s" % (type(exc).__name__, exc))
        publish(out / "state.json", state)
        progress["stage"] = "FAILED_SCRIPT"
        tick()
        raise


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--run-spec", required=True)
    p.add_argument("--attempt-dir")
    a = p.parse_args()
    with open(a.run_spec, encoding="utf-8") as f:
        spec = json.load(f)
    result = run(spec, a.attempt_dir or str(Path(a.run_spec).parent))
    print(json.dumps({"status": result["status"], "case_evaluations_total": result["case_evaluations_total"],
                      "assertions_all_true": result["assertions_all_true"]}), flush=True)


if __name__ == "__main__":
    main()
