#!/usr/bin/env python3
"""Reproduce / disprove the u2 selector crash against real attempt rows (card t_9afe04ad).

Loads a Strategy B v2 runner (`SB_ENGINE_PATH`) and the u2 attempt's historical grid rows, then
calls `select_cohort_winner()` - the exact call that crashed the production attempt.

  exit 0 -> the selector returned; it prints reason + winner cell
  exit 3 -> the selector raised (the pre-fix bug); it prints the exception type/message
  exit 2 -> usage error

Usage:
  SB_ENGINE_PATH=<runner.py> <python> reproduce_u2_crash.py <run-spec.json> <grid_historical.csv>

The same script is the old/new differential instrument: point it at the pre-fix runner bytes
(`git show dbbc239:container/scripts/30_strategy_b_run.py`) and it must fail with
`ValueError: 10 is not in list`; point it at the fixed runner and it must return a winner for
BTCUSDT/15m.  Read-only: never writes to the attempt tree.
"""
import csv
import importlib.util
import json
import os
import sys

ENGINE = os.environ.get("SB_ENGINE_PATH", "/scripts/30_strategy_b_run.py")
INT_FIELDS = ("ema_fast", "ema_slow", "wf_train_days", "wf_test_days", "ema_pair_index",
              "walk_forward_index", "episodes")
FLOAT_FIELDS = ("spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct",
                "base_quote", "net_pnl", "sharpe")


def load_engine(path):
    spec = importlib.util.spec_from_file_location("sb_engine", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def rows_of(path):
    rows = []
    with open(path, newline="") as fh:
        for raw in csv.DictReader(fh):
            row = dict(raw)
            for key in INT_FIELDS:
                row[key] = int(row[key])
            for key in FLOAT_FIELDS:
                text = row[key]
                row[key] = None if text in ("", "None") else float(text)
            rows.append(row)
    return rows


def main():
    if len(sys.argv) != 3:
        sys.stderr.write("usage: reproduce_u2_crash.py <run-spec.json> <grid_historical.csv>\n")
        return 2
    with open(sys.argv[1]) as fh:
        spec = json.load(fh)
    sb = load_engine(ENGINE)
    rows = rows_of(sys.argv[2])
    print("engine=%s rows=%d" % (ENGINE, len(rows)))
    for sym, tf in sorted({(r["symbol"], r["timeframe"]) for r in rows}):
        hist = [r for r in rows if (r["symbol"], r["timeframe"]) == (sym, tf)
                and r["window_kind"] == "historical"]
        label = "%s/%s" % (sym, tf)
        try:
            winner, reason = sb.select_cohort_winner(hist, spec)
        except Exception as exc:  # noqa: BLE001 - the bug IS the point of this script
            print("%s: RAISED %s: %s" % (label, type(exc).__name__, exc))
            return 3
        cell = None if winner is None else list(sb.cell_key(winner))
        print("%s: reason=%s winner_cell=%s" % (label, reason, cell))
    return 0


if __name__ == "__main__":
    sys.exit(main())
