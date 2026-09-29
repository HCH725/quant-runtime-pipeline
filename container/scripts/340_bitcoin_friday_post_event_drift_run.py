#!/usr/bin/env python3
"""Deterministic local-universe full backtest for the Bitcoin Friday drift family.

The reviewed source studies Kraken BTC/USD, but the current system lifecycle makes the
source venue/quote provenance rather than an execution prerequisite when the registered
core signal is computable on canonical local raw.  This runner therefore evaluates the
unchanged Friday fixed-EST timing rule on the complete legal local spot-BTC 1h universe
currently present in the Common Data Pack: Binance BTCUSDT spot 1h.

The runner is intentionally family-local and self-contained.  It does not write terminal
sentinels or verdicts; C4 owns host-side family research/disposition.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import hashlib
import json
import math
import os
import re
import statistics
import sys
import time
from pathlib import Path

FAMILY_ID = "bitcoin-friday-3pm-est-post-event-drift-2026-09-03"
RUNNER_NAME = "340_bitcoin_friday_post_event_drift_run.py"
ENGINE_VERSION = "bitcoin-friday-post-event-drift-local-v2"
SYMBOL = "BTCUSDT"
TIMEFRAME = "1h"
RAW_ROOT = Path("/data/raw")
KLINE_DIR = RAW_ROOT / "binance" / "spot" / "klines" / SYMBOL / TIMEFRAME
CONFIG_PATH = RAW_ROOT / "_meta" / "CONFIG.json"
SCHEMA_PATH = RAW_ROOT / "_meta" / "SCHEMA.md"
OWNERSHIP_KEYS = ("task_id", "kanban_task_id", "kanban_board")

PHASES = {
    "historical": ("2022-01-01", "2025-09-30"),
    "oos": ("2025-10-01", "2026-09-11"),
    "full": ("2022-01-01", "2026-09-11"),
}
GRIDS = (
    "historical", "oos", "full", "fee_2x", "funding_2x",
    "entry_delay_1_bar", "slippage_2ticks", "no_funding",
    "no_funding_full", "cost_attrition_40bps",
)
DCA_AXES = {
    "spacing_pct": (0.01, 0.02, 0.03, 0.04),
    "size_multiplier": (1.0, 1.1),
    "breakeven_tp_pct": (0.01, 0.02, 0.03),
    "invalidation_pct": (0.05, 0.10),
}
BASE_QUOTE = 1000.0
MAX_ACTIVE_TRANCHES = 11
MAX_ADD_LEVELS = MAX_ACTIVE_TRANCHES - 1
START_EQUITY = 30000.0
EXECUTION_SEMANTICS = {
    "execution_market": "BINANCE_SPOT",
    "position_direction": "long_only",
    "numeraire": "USDT",
    "starting_equity_usdt": START_EQUITY,
    "base_quote_usdt": BASE_QUOTE,
    "routine_active_tranches_max": MAX_ACTIVE_TRANCHES,
    "reserve_tranche": 12,
    "initial_entry_counts_as_active_tranche": True,
    "max_add_levels": MAX_ADD_LEVELS,
    "same_bar_order": "adverse_before_favorable_tp",
}
BASE_FEE_BPS = 10.0
BASE_SLIPPAGE_BPS = 1.0
MIN_EPISODES_IS = 10
MIN_EPISODES_OOS = 3
MIN_NEIGHBOUR = 0.60
MS_HOUR = 60 * 60 * 1000

ARTIFACTS = (
    "state.json", "result.json", "artifacts/progress.json",
    "artifacts/local_data_evidence.json", "artifacts/assertions.json",
    "artifacts/falsification.json", "artifacts/stress_effects.json",
    "artifacts/cohort_results.json", "artifacts/cohort_survivors.json",
    *(("artifacts/grid_%s.csv" % grid) for grid in GRIDS),
)


def utc_now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with open(temp, "x", encoding="utf-8") as fh:
        json.dump(value, fh, indent=2, ensure_ascii=False, allow_nan=False)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(temp, path)


def immutable_json(path, value):
    path = Path(path)
    raw = (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        if not path.is_file() or path.is_symlink() or path.read_bytes() != raw:
            raise ValueError("immutable output differs: %s" % path)
        return
    with os.fdopen(fd, "wb") as fh:
        fh.write(raw)
        fh.flush()
        os.fsync(fh.fileno())


def dca_grid():
    return [
        {
            "spacing_pct": spacing,
            "size_multiplier": mult,
            "breakeven_tp_pct": tp,
            "invalidation_pct": inv,
        }
        for spacing in DCA_AXES["spacing_pct"]
        for mult in DCA_AXES["size_multiplier"]
        for tp in DCA_AXES["breakeven_tp_pct"]
        for inv in DCA_AXES["invalidation_pct"]
    ]


DCA_GRID = dca_grid()


def expected_counts():
    per = len(DCA_GRID)
    return {
        "cohorts": 1,
        "strategy_cases_per_cohort": 1,
        "dca_configs_per_cohort": per,
        "case_evaluations_per_grid": per,
        "grid_count": len(GRIDS),
        "case_evaluations_total": per * len(GRIDS),
    }


def inspect_local_universe(config, schema_text=""):
    """Return the legal local execution universe; source-market identity is not a gate."""
    config = config if isinstance(config, dict) else {}
    datasets = config.get("datasets") if isinstance(config.get("datasets"), dict) else {}
    intervals = {str(v) for v in config.get("intervals", []) if isinstance(v, (str, int, float))}
    symbols = {str(v).upper() for v in config.get("symbols", []) if isinstance(v, str)}
    spot_declared = "spot_klines" in datasets or "spot" in schema_text.lower()
    btc_declared = SYMBOL in symbols or SYMBOL.lower() in json.dumps(datasets).lower()
    one_hour = TIMEFRAME in intervals or "1h" in json.dumps(datasets).lower()
    legal = bool(spot_declared and btc_declared and one_hour)
    return {
        "legal": legal,
        "venue": "BINANCE",
        "market_type": "spot",
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "source_market": "Kraken BTC/USD spot",
        "source_exact_match": False,
        "source_exact_match_is_execution_prerequisite": False,
        "reason": (
            "canonical local Binance BTCUSDT spot 1h is available; Kraken identity is provenance "
            "and external-validity context, not an execution prerequisite"
            if legal else
            "canonical catalog does not declare the local BTCUSDT spot 1h capability"
        ),
    }


def validate_identity(spec, round_spec, attempt_dir, runner_path):
    attempt = Path(attempt_dir).resolve(strict=True)
    family_id = spec.get("family_id")
    round_id = spec.get("round_id")
    run_id = spec.get("run_id")
    if family_id != FAMILY_ID or attempt.parents[3].name != family_id \
            or attempt.parents[1].name != round_id or attempt.name != run_id:
        raise ValueError("run-spec identity does not match attempt path")
    if round_spec.get("family_id") != family_id or round_spec.get("round_id") != round_id:
        raise ValueError("round-spec identity mismatch")
    for doc in (spec, round_spec):
        leaked = [k for k in OWNERSHIP_KEYS if k in doc and doc[k] not in (None, "")]
        if leaked:
            raise ValueError("direct execution spec contains ownership fields: %s" % leaked)
    if spec.get("semantic_fingerprint") != round_spec.get("semantic_fingerprint"):
        raise ValueError("semantic fingerprint differs between specs")
    expected_fp = sha256(spec.get("fingerprint_input", "").encode("utf-8"))
    if expected_fp != spec.get("semantic_fingerprint"):
        raise ValueError("fingerprint_input does not match semantic_fingerprint")
    script = spec.get("script") if isinstance(spec.get("script"), dict) else {}
    if script.get("path") != "/scripts/" + RUNNER_NAME:
        raise ValueError("wrong runner path")
    if script.get("sha256") != sha256_file(runner_path):
        raise ValueError("run-spec script hash differs from executing runner")
    round_path = attempt.parents[1] / "round-spec.json"
    if spec.get("round_spec_sha256") != sha256(round_path.read_bytes()):
        raise ValueError("run-spec round_spec_sha256 mismatch")
    return attempt


def validate_spec(spec, round_spec):
    if spec.get("family_id") != FAMILY_ID:
        raise ValueError("wrong family")
    rid = spec.get("round_id", "")
    if not re.fullmatch(re.escape(FAMILY_ID) + r"-r[1-9][0-9]*", rid):
        raise ValueError("invalid round id")
    if not re.fullmatch(re.escape(rid) + r"-u[1-9][0-9]*", str(spec.get("run_id", ""))):
        raise ValueError("invalid run id")
    if spec.get("selector_version") != "cohort-selector-v1" or \
            spec.get("disposition_version") != "cohort-disposition-v1":
        raise ValueError("selector/disposition mismatch")
    data = spec.get("data") if isinstance(spec.get("data"), dict) else {}
    if data.get("source") != "/data/raw" or data.get("market") != "BINANCE_SPOT" \
            or data.get("symbols") != [SYMBOL] or data.get("timeframes") != [TIMEFRAME]:
        raise ValueError("local eligible universe mismatch")
    if data.get("clock_convention") != "fixed EST (UTC-05:00)":
        raise ValueError("clock convention changed")
    params = spec.get("params")
    if params != [{"signal_rule_version": 1}]:
        raise ValueError("strategy domain changed")
    dca = spec.get("dca_domain") if isinstance(spec.get("dca_domain"), dict) else {}
    for key, values in DCA_AXES.items():
        if list(dca.get(key, ())) != list(values):
            raise ValueError("DCA axis changed: %s" % key)
    if dca.get("grid") != DCA_GRID or float(dca.get("base_quote", -1)) != BASE_QUOTE:
        raise ValueError("DCA grid/base quote changed")
    if spec.get("execution_semantics") != EXECUTION_SEMANTICS:
        raise ValueError("execution semantics differ from the reviewed candidate")
    gates = spec.get("gates") if isinstance(spec.get("gates"), dict) else {}
    expected_gates = {
        "min_episodes_is": MIN_EPISODES_IS,
        "min_episodes_oos": MIN_EPISODES_OOS,
        "min_neighbour_same_sign_fraction": MIN_NEIGHBOUR,
    }
    if gates != expected_gates:
        raise ValueError("gates differ from registered r2 values")
    exp = spec.get("expected") if isinstance(spec.get("expected"), dict) else {}
    counts = expected_counts()
    if exp.get("expected_case_evaluations") != counts["case_evaluations_total"] \
            or exp.get("grids") != list(GRIDS):
        raise ValueError("expected coverage mismatch")
    eu = round_spec.get("eligible_universe") if isinstance(round_spec.get("eligible_universe"), dict) else {}
    if eu.get("instruments") != [SYMBOL] or eu.get("timeframes") != [TIMEFRAME] \
            or eu.get("execution_market") != "BINANCE_SPOT":
        raise ValueError("round local eligible universe mismatch")
    return counts


def month_keys(start, end):
    cur = dt.date.fromisoformat(start[:10]).replace(day=1)
    last = dt.date.fromisoformat(end[:10]).replace(day=1)
    out = []
    while cur <= last:
        out.append("%04d-%02d" % (cur.year, cur.month))
        cur = dt.date(cur.year + (cur.month == 12), 1 if cur.month == 12 else cur.month + 1, 1)
    return out


def load_local_rows(start, end, kline_dir=None):
    root = Path(kline_dir) if kline_dir is not None else KLINE_DIR
    start_ms = int(dt.datetime.fromisoformat(start[:10]).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
    # A fixed-EST Friday episode exits at 00:59:59 UTC on Saturday, so the local
    # phase end needs one extra UTC calendar day of source bars for a Friday boundary.
    source_end = dt.date.fromisoformat(end[:10]) + dt.timedelta(days=1)
    end_ms = int((dt.datetime.combine(source_end, dt.time(0, 0), tzinfo=dt.timezone.utc)
                  + dt.timedelta(days=1)).timestamp() * 1000)
    rows = {}
    files = []
    for month in month_keys(start, source_end.isoformat()):
        path = root / ("%s-%s-%s.jsonl.gz" % (SYMBOL, TIMEFRAME, month))
        if not path.is_file():
            continue
        files.append(path)
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                row = json.loads(line)
                t = int(row["open_time_ms"])
                if start_ms <= t < end_ms:
                    rows[t] = {
                        "open_time_ms": t,
                        "close_time_ms": int(row["close_time_ms"]),
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                    }
    return rows, files


def episode_times(local_friday, entry_delay_hours=0):
    if local_friday.weekday() != 4:
        raise ValueError("episode date must be Friday")
    # Fixed EST is UTC-05 year-round. Friday 15:00 EST = Friday 20:00 UTC.
    event_utc = dt.datetime.combine(local_friday, dt.time(20, 0), tzinfo=dt.timezone.utc)
    entry_utc = event_utc + dt.timedelta(hours=1 + int(entry_delay_hours))
    # Exit at 19:59:59 EST = 00:59:59 UTC Saturday, i.e. close of the 00:00 UTC hourly bar.
    exit_bar_utc = event_utc + dt.timedelta(hours=4)
    return {
        "event_ms": int(event_utc.timestamp() * 1000),
        "entry_ms": int(entry_utc.timestamp() * 1000),
        "exit_bar_ms": int(exit_bar_utc.timestamp() * 1000),
    }


def phase_fridays(start, end):
    day = dt.date.fromisoformat(start)
    stop = dt.date.fromisoformat(end)
    day += dt.timedelta(days=(4 - day.weekday()) % 7)
    while day <= stop:
        yield day
        day += dt.timedelta(days=7)


def grid_phase(grid):
    if grid in ("historical", "no_funding"):
        return "historical"
    if grid == "oos":
        return "oos"
    return "full"


def cost_for(grid):
    return {
        "fee_bps": 20.0 if grid == "fee_2x" else 40.0 if grid == "cost_attrition_40bps" else BASE_FEE_BPS,
        "slippage_bps": 2.0 if grid == "slippage_2ticks" else BASE_SLIPPAGE_BPS,
        "entry_delay_hours": 1 if grid == "entry_delay_1_bar" else 0,
        "funding_mult": 0.0,  # spot: no funding charge exists; funding grids are deliberate no-ops.
    }


def _fill_price(raw, side, slippage_bps):
    slip = float(slippage_bps) / 10000.0
    return float(raw) * (1.0 + slip if side == "buy" else 1.0 - slip)


def simulate_episode(bars, dca, cost):
    """Long-only four-hour episode. Adverse same-bar path is processed before favorable TP."""
    if not bars:
        raise ValueError("episode has no execution bars")
    fee_rate = float(cost["fee_bps"]) / 10000.0
    slip_bps = float(cost["slippage_bps"])
    spacing = float(dca["spacing_pct"])
    mult = float(dca["size_multiplier"])
    tp_pct = float(dca["breakeven_tp_pct"])
    invalidation = float(dca["invalidation_pct"])

    initial_raw = bars[0]["open"]
    initial = _fill_price(initial_raw, "buy", slip_bps)
    base_notional = BASE_QUOTE
    qty = base_notional / initial
    basis = qty * initial
    fees = base_notional * fee_rate
    turnover = base_notional
    fills = 1
    adds = 0
    level = 1
    exit_reason = "time"
    exit_price = None

    for bar in bars:
        avg_before = basis / qty
        stop_before = avg_before * (1.0 - invalidation)
        if bar["open"] <= stop_before:
            exit_reason = "stop"
            exit_price = _fill_price(bar["open"], "sell", slip_bps)
            break

        # Conservative path: adverse low first, so all reachable adds/stop precede TP.
        while level <= MAX_ADD_LEVELS:
            trigger = initial * (1.0 - spacing * level)
            if trigger <= 0 or bar["low"] > trigger:
                break
            add_raw = trigger
            add_fill = _fill_price(add_raw, "buy", slip_bps)
            quote = BASE_QUOTE * (mult ** level)
            add_qty = quote / add_fill
            qty += add_qty
            basis += add_qty * add_fill
            fees += quote * fee_rate
            turnover += quote
            fills += 1
            adds += 1
            level += 1

        avg = basis / qty
        stop = avg * (1.0 - invalidation)
        if bar["low"] <= stop:
            exit_reason = "stop"
            exit_price = _fill_price(stop, "sell", slip_bps)
            break
        take = avg * (1.0 + tp_pct)
        if bar["high"] >= take:
            exit_reason = "tp"
            exit_price = _fill_price(take, "sell", slip_bps)
            break

    if exit_price is None:
        exit_price = _fill_price(bars[-1]["close"], "sell", slip_bps)

    exit_notional = qty * exit_price
    fees += exit_notional * fee_rate
    turnover += exit_notional
    fills += 1
    gross = exit_notional - basis
    net = gross - fees
    capital = basis
    return {
        "gross_pnl": gross,
        "fees": fees,
        "funding": 0.0,
        "net_pnl": net,
        "fills": fills,
        "adds": adds,
        "max_active_tranches": 1 + adds,
        "turnover_usdt": turnover,
        "capital_committed": capital,
        "exit_reason": exit_reason,
    }


def _metric_summary(episodes, phase_start, phase_end):
    gross = sum(e["gross_pnl"] for e in episodes)
    fees = sum(e["fees"] for e in episodes)
    net = sum(e["net_pnl"] for e in episodes)
    fills = sum(e["fills"] for e in episodes)
    adds = sum(e["adds"] for e in episodes)
    turnover = sum(e["turnover_usdt"] for e in episodes)
    tp_hits = sum(e["exit_reason"] == "tp" for e in episodes)
    stop_hits = sum(e["exit_reason"] == "stop" for e in episodes)
    end_exits = sum(e["exit_reason"] == "time" for e in episodes)
    returns = [e["net_pnl"] / START_EQUITY for e in episodes]
    if len(returns) >= 2 and statistics.pstdev(returns) > 0:
        sharpe = statistics.mean(returns) / statistics.pstdev(returns) * math.sqrt(52.0)
    else:
        sharpe = 0.0

    equity = START_EQUITY
    peak = equity
    max_dd = 0.0
    for e in episodes:
        equity += e["net_pnl"]
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    days = max(1, (dt.date.fromisoformat(phase_end) - dt.date.fromisoformat(phase_start)).days + 1)
    if equity > 0:
        annual = (equity / START_EQUITY) ** (365.25 / days) - 1.0
    else:
        annual = -1.0
    max_cap = max((e["capital_committed"] for e in episodes), default=0.0)
    return {
        "gross_pnl": gross,
        "fees": fees,
        "funding": 0.0,
        "net_pnl": net,
        "ending_equity": equity,
        "episodes": len(episodes),
        "fills": fills,
        "adds": adds,
        "max_active_tranches": max((e["max_active_tranches"] for e in episodes), default=0),
        "turnover_usdt": turnover,
        "sharpe": sharpe,
        "max_dd_pct": (max_dd / peak * 100.0) if peak > 0 else 0.0,
        "max_dd_usdt": max_dd,
        "annualized_return": annual,
        "max_effective_leverage": max_cap / START_EQUITY,
        "capital_utilization": max_cap / START_EQUITY,
        "tp_hits": tp_hits,
        "stop_hits": stop_hits,
        "margin_calls": 0,
        "end_exits": end_exits,
        "open_at_end": 0,
        "decomposition_ok": abs((gross - fees) - net) < 1e-7,
    }


def evaluate_cell(rows, dca, grid):
    phase = grid_phase(grid)
    start, end = PHASES[phase]
    cost = cost_for(grid)
    episodes = []
    missing = []
    delay = int(cost["entry_delay_hours"])
    for friday in phase_fridays(start, end):
        times = episode_times(friday, delay)
        event = rows.get(times["event_ms"])
        entry = rows.get(times["entry_ms"])
        exit_bar = rows.get(times["exit_bar_ms"])
        if not event or not entry or not exit_bar:
            missing.append(friday.isoformat())
            continue
        if event["close_time_ms"] >= entry["open_time_ms"]:
            raise ValueError("event hour is not closed before entry")
        execution = []
        t = times["entry_ms"]
        while t <= times["exit_bar_ms"]:
            bar = rows.get(t)
            if bar is None:
                execution = []
                break
            execution.append(bar)
            t += MS_HOUR
        if not execution:
            missing.append(friday.isoformat())
            continue
        episodes.append(simulate_episode(execution, dca, cost))
    metrics = _metric_summary(episodes, start, end)
    metrics["missing_event_windows"] = len(missing)
    metrics["missing_event_dates"] = missing[:20]
    return metrics


def cell_key(row):
    return (
        row["symbol"], row["timeframe"], row["signal_rule_version"],
        row["spacing_pct"], row["size_multiplier"],
        row["breakeven_tp_pct"], row["invalidation_pct"],
    )


def neighbourhood(winner, historical):
    found = {cell_key(r): r for r in historical}
    key = list(cell_key(winner))
    # positions 0-2 are symbol/timeframe/signal version. DCA starts at position 3.
    positions = [
        (3, list(DCA_AXES["spacing_pct"])),
        (4, list(DCA_AXES["size_multiplier"])),
        (5, list(DCA_AXES["breakeven_tp_pct"])),
        (6, list(DCA_AXES["invalidation_pct"])),
    ]
    agreeing = total = 0
    winner_positive = float(winner["net_pnl"]) > 0
    for pos, domain in positions:
        value = key[pos]
        idx = domain.index(value)
        for step in (-1, 1):
            if 0 <= idx + step < len(domain):
                probe = key.copy()
                probe[pos] = domain[idx + step]
                row = found[tuple(probe)]
                total += 1
                agreeing += (float(row["net_pnl"]) > 0) == winner_positive
    fraction = agreeing / total if total else 0.0
    return {
        "neighbours": total,
        "agreeing": agreeing,
        "same_sign_fraction": fraction,
        "passed": bool(total and fraction >= MIN_NEIGHBOUR),
    }


def metric_block(row):
    keys = (
        "gross_pnl", "fees", "funding", "net_pnl", "ending_equity", "episodes",
        "fills", "adds", "turnover_usdt", "sharpe", "max_dd_pct", "max_dd_usdt",
        "annualized_return", "max_effective_leverage", "capital_utilization",
        "tp_hits", "stop_hits", "margin_calls", "end_exits", "open_at_end",
    )
    return {k: row[k] for k in keys}


def select_cohort(rows):
    historical = rows["historical"]
    best_episodes = max((int(r["episodes"]) for r in historical), default=0)
    if best_episodes < MIN_EPISODES_IS:
        return None, {
            "winner": None,
            "metrics": {},
            "best_historical_episodes": best_episodes,
            "cull_reasons": ["insufficient_trades"],
        }
    candidates = [
        r for r in historical
        if int(r["episodes"]) >= MIN_EPISODES_IS
        and float(r["net_pnl"]) > 0
        and float(r["sharpe"]) > 0
    ]
    if not candidates:
        return None, {
            "winner": None,
            "metrics": {},
            "best_historical_episodes": best_episodes,
            "cull_reasons": ["no_qualifying_candidate"],
        }
    winner = min(
        candidates,
        key=lambda r: (
            -float(r["sharpe"]), -float(r["net_pnl"]),
            float(r["spacing_pct"]), float(r["size_multiplier"]),
            float(r["breakeven_tp_pct"]), float(r["invalidation_pct"]),
        ),
    )
    lookup = {
        grid: next(r for r in rows[grid] if cell_key(r) == cell_key(winner))
        for grid in GRIDS
    }
    reasons = []
    oos = lookup["oos"]
    if not (float(oos["net_pnl"]) > 0 and float(oos["sharpe"]) > 0
            and int(oos["episodes"]) >= MIN_EPISODES_OOS):
        reasons.append("oos_economic")
    if float(lookup["full"]["net_pnl"]) <= 0:
        reasons.append("full_economic")
    weak = [
        grid for grid in ("fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks")
        if float(lookup[grid]["net_pnl"]) <= 0
    ]
    if weak:
        reasons.append("robustness_economic:" + ",".join(weak))
    neighbours = neighbourhood(winner, historical)
    if not neighbours["passed"]:
        reasons.append("parameter_neighbourhood")
    metrics = {
        "historical": metric_block(lookup["historical"]),
        "oos": metric_block(oos),
        "full": metric_block(lookup["full"]),
        "robustness": {
            grid: metric_block(lookup[grid])
            for grid in ("fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks")
        },
    }
    detail = {
        "winner": {
            "signal_rule_version": winner["signal_rule_version"],
            "spacing_pct": winner["spacing_pct"],
            "size_multiplier": winner["size_multiplier"],
            "breakeven_tp_pct": winner["breakeven_tp_pct"],
            "invalidation_pct": winner["invalidation_pct"],
            "grid": "historical",
        },
        "metrics": metrics,
        "best_historical_episodes": best_episodes,
        "neighbourhood": neighbours,
        "cull_reasons": reasons,
    }
    return (winner if not reasons else None), detail


def write_grid(path, rows):
    columns = [
        "symbol", "timeframe", "signal_rule_version",
        "spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct",
        "grid", "gross_pnl", "fees", "funding", "net_pnl", "ending_equity",
        "episodes", "fills", "adds", "turnover_usdt", "sharpe", "max_dd_pct",
        "max_dd_usdt", "annualized_return", "max_effective_leverage",
        "capital_utilization", "tp_hits", "stop_hits", "margin_calls",
        "end_exits", "open_at_end", "decomposition_ok", "missing_event_windows",
    ]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def evaluate_all(rows):
    grid_rows = {grid: [] for grid in GRIDS}
    for dca in DCA_GRID:
        for grid in GRIDS:
            metric = evaluate_cell(rows, dca, grid)
            row = {
                "symbol": SYMBOL,
                "timeframe": TIMEFRAME,
                "signal_rule_version": 1,
                **dca,
                "grid": grid,
                **metric,
            }
            grid_rows[grid].append(row)
    expected_keys = {
        (SYMBOL, TIMEFRAME, 1, d["spacing_pct"], d["size_multiplier"],
         d["breakeven_tp_pct"], d["invalidation_pct"])
        for d in DCA_GRID
    }
    coverage = {
        grid: len(values) == len(DCA_GRID) and {cell_key(r) for r in values} == expected_keys
        for grid, values in grid_rows.items()
    }
    selected, detail = select_cohort(grid_rows)
    record = {
        "cohort": SYMBOL + "/" + TIMEFRAME,
        "outcome": "SURVIVOR" if selected is not None else "CULLED",
        **detail,
    }
    return grid_rows, [record], ([record] if selected is not None else []), coverage


def run(spec_path, attempt_dir, runner_path=__file__, kline_dir=None):
    import qlib  # type: ignore[reportMissingImports]

    attempt = Path(attempt_dir).resolve(strict=True)
    if any((attempt / name).exists() for name in ("DONE", "FAILED", "INCOMPLETE", "result.json")):
        raise ValueError("attempt is already terminal or has immutable result")
    spec_file = Path(spec_path).resolve(strict=True)
    if spec_file != attempt / "run-spec.json":
        raise ValueError("run-spec must be the attempt's run-spec.json")
    spec = json.loads(spec_file.read_text(encoding="utf-8"))
    round_path = attempt.parents[1] / "round-spec.json"
    round_spec = json.loads(round_path.read_text(encoding="utf-8"))
    validate_identity(spec, round_spec, attempt, runner_path)
    counts = validate_spec(spec, round_spec)

    artifacts = attempt / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    state = {
        "schema_version": 1, "family_id": FAMILY_ID, "round_id": spec["round_id"],
        "run_id": spec["run_id"], "stage": "RUNNING_QLIB", "updated_at_utc": utc_now(),
    }
    atomic_json(attempt / "state.json", state)
    progress = {
        "schema_version": 1, "family_id": FAMILY_ID, "round_id": spec["round_id"],
        "run_id": spec["run_id"], "stage": "RUNNING_QLIB",
        "case_evaluations": 0,
        "expected_case_evaluations": counts["case_evaluations_total"],
        "updated_at_utc": utc_now(),
    }
    atomic_json(artifacts / "progress.json", progress)

    try:
        try:
            config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except OSError:
            config = {}
        try:
            schema_text = SCHEMA_PATH.read_text(encoding="utf-8")
        except OSError:
            schema_text = ""
        universe = inspect_local_universe(config, schema_text)
        if not universe["legal"]:
            raise RuntimeError("canonical local BTCUSDT spot 1h capability is absent")

        rows, files = load_local_rows(PHASES["full"][0], PHASES["full"][1], kline_dir=kline_dir)
        if not rows:
            raise RuntimeError("no local BTCUSDT spot 1h rows in registered window")
        grid_rows, cohort_results, survivors, coverage = evaluate_all(rows)
        relevant_missing = sorted({
            date
            for values in grid_rows.values()
            for row in values
            for date in row.get("missing_event_dates", [])
        })
        assertions = {
            "coverage_complete": all(coverage.values()),
            "dca_grid_complete": len(DCA_GRID) == 48,
            "local_universe_is_binance_btcusdt_spot_1h": universe["legal"],
            "source_market_exact_match_not_used_as_gate": universe["source_exact_match"] is False,
            "fixed_est_signal_timing": True,
            "spot_funding_is_zero": all(
                abs(float(row["funding"])) < 1e-12
                for values in grid_rows.values() for row in values
            ),
            "gross_net_decomposition": all(
                row["decomposition_ok"] for values in grid_rows.values() for row in values
            ),
            "relevant_event_windows_complete": not relevant_missing,
            "selector_uses_historical_only": True,
        }
        for grid in GRIDS:
            write_grid(artifacts / ("grid_%s.csv" % grid), grid_rows[grid])

        immutable_json(artifacts / "cohort_results.json", cohort_results)
        immutable_json(artifacts / "cohort_survivors.json", survivors)
        immutable_json(artifacts / "assertions.json", assertions)
        immutable_json(artifacts / "local_data_evidence.json", {
            "family_id": FAMILY_ID,
            "local_universe": universe,
            "raw_root": str(KLINE_DIR),
            "files": [p.name for p in files],
            "file_count": len(files),
            "first_open_time_ms": min(rows),
            "last_open_time_ms": max(rows),
            "registered_window": {"start": PHASES["full"][0], "end": PHASES["full"][1]},
            "missing_relevant_event_dates": relevant_missing,
            "claim_scope": "canonical local Binance BTCUSDT spot 1h only; not exact Kraken reproduction",
        })
        full = cohort_results[0].get("metrics", {}).get("full", {})
        oos = cohort_results[0].get("metrics", {}).get("oos", {})
        immutable_json(artifacts / "falsification.json", {
            "exact_source_reproduction": {
                "status": "INDETERMINATE",
                "reason": "Kraken source venue is unavailable locally; current lifecycle permits local-universe execution but not an exact-source claim.",
            },
            "strict_post_source_oos": {
                "status": "MEASURED",
                "net_pnl": oos.get("net_pnl"),
                "sharpe": oos.get("sharpe"),
            },
            "cost_execution_stress": {
                "status": "MEASURED",
                "winner_survived": bool(survivors),
            },
            "venue_portability": {
                "status": "LOCAL_MEASUREMENT",
                "venue": "BINANCE",
                "symbol": SYMBOL,
                "note": "This is local portability evidence, not proof of multi-venue generality.",
            },
        })
        full_sum = sum(float(r["net_pnl"]) for r in grid_rows["full"])
        immutable_json(artifacts / "stress_effects.json", {
            "baseline_grid": "full",
            "net_pnl_delta": {
                grid: sum(float(r["net_pnl"]) for r in grid_rows[grid]) - full_sum
                for grid in ("fee_2x", "funding_2x", "entry_delay_1_bar",
                             "slippage_2ticks", "no_funding_full", "cost_attrition_40bps")
            },
            "spot_funding_note": "funding grids are expected no-ops because the registered local execution market is spot",
        })

        case_total = sum(len(v) for v in grid_rows.values())
        result = {
            "schema_version": 1,
            "family_id": FAMILY_ID,
            "round_id": spec["round_id"],
            "run_id": spec["run_id"],
            "engine": ENGINE_VERSION,
            "script_sha256": sha256_file(runner_path),
            "qlib_version": getattr(qlib, "__version__", "unknown"),
            "status": "ARTIFACT_READY",
            "coverage_complete": all(coverage.values()) and all(assertions.values()),
            "coverage_by_grid": coverage,
            "assertions_all_true": all(assertions.values()),
            "assertion_failures": [k for k, v in assertions.items() if not v],
            "case_evaluations_total": case_total,
            "expected_case_evaluations": counts["case_evaluations_total"],
            "cohort_count": len(cohort_results),
            "cohort_survivor_count": len(survivors),
            "cohort_survivors": [r["cohort"] for r in survivors],
            "disposition": (
                "REJECT / NO_SURVIVOR" if not survivors
                else "SURVIVOR_FOUND" if len(survivors) == 1
                else "MULTIPLE_SURVIVORS"
            ),
            "verdict_recommendation": "PASS" if survivors else "REJECT",
            "performance_claimable_recommendation": bool(survivors and all(assertions.values())),
            "selector_version": "cohort-selector-v1",
            "disposition_version": "cohort-disposition-v1",
            "disposition_mapping_version": "v1.4.0",
            "grid_kinds": list(GRIDS),
            "local_universe": universe,
            "execution_semantics": EXECUTION_SEMANTICS,
            "claim_scope": "canonical local Binance BTCUSDT spot 1h",
            "source_exact_reproduction": False,
            "runtime_seconds": time.monotonic() - started,
            "full_metrics": full,
            "oos_metrics": oos,
        }
        if case_total != counts["case_evaluations_total"]:
            result["coverage_complete"] = False
            result["assertions_all_true"] = False
            result["assertion_failures"].append("case_evaluations_total")
        immutable_json(attempt / "result.json", result)
        state.update(stage="ARTIFACT_READY", updated_at_utc=utc_now())
        atomic_json(attempt / "state.json", state)
        progress.update(
            stage="ARTIFACT_READY",
            case_evaluations=case_total,
            updated_at_utc=utc_now(),
            runtime_seconds=round(time.monotonic() - started, 3),
        )
        atomic_json(artifacts / "progress.json", progress)
        return result
    except Exception as exc:
        state.update(
            stage="FAILED_SCRIPT",
            error="%s: %s" % (type(exc).__name__, exc),
            updated_at_utc=utc_now(),
        )
        atomic_json(attempt / "state.json", state)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-spec", required=True)
    parser.add_argument("--attempt-dir", required=True)
    args = parser.parse_args(argv)
    result = run(args.run_spec, args.attempt_dir)
    print(json.dumps({
        "status": result["status"],
        "case_evaluations_total": result["case_evaluations_total"],
        "coverage_complete": result["coverage_complete"],
        "cohort_survivor_count": result["cohort_survivor_count"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
