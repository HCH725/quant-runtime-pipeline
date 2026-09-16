#!/usr/bin/env python3
"""Card t_0546d31b diagnosis: why is `no_entry_after_exhaustion` false in r1-u1?

Reads grid_full.csv from the frozen u1 attempt (read-only) and reports every row whose
min_entry_equity is <= 0, plus the surrounding distribution. Pure read-only analysis.
"""
import csv
import sys
from collections import Counter

CSV = ("/Volumes/ExpansionDrive/qlib-results/stochastic-rsi-renko-2026-08-31/rounds/"
       "stochastic-rsi-renko-2026-08-31-r1/attempts/stochastic-rsi-renko-2026-08-31-r1-u1/"
       "artifacts/grid_full.csv")

rows = []
with open(CSV, newline="") as fh:
    for r in csv.DictReader(fh):
        for k in ("min_entry_equity", "ending_equity", "net_pnl", "episodes", "fills",
                  "margin_calls", "halted", "signals_entered", "bars_in_market", "flip_exits",
                  "tp_hits", "stop_hits", "slice_end_flats", "open_at_end",
                  "max_effective_leverage", "max_dd_usdt", "max_dd_pct"):
            if k in ("halted",):
                r[k] = 1 if str(r[k]).strip().lower() in ("true", "1") else 0
            else:
                r[k] = float(r[k])
        rows.append(r)

print("rows:", len(rows))
bad = [r for r in rows if r["min_entry_equity"] <= 0.0]
print("rows with min_entry_equity <= 0:", len(bad))
print("rows with min_entry_equity < 0:", len([r for r in rows if r["min_entry_equity"] < 0.0]))
print("rows with min_entry_equity == 0:", len([r for r in rows if r["min_entry_equity"] == 0.0]))
print()
vals = sorted(r["min_entry_equity"] for r in rows)
print("min_entry_equity quantiles: min=%.2f p1=%.2f p5=%.2f median=%.2f max=%.2f" %
      (vals[0], vals[len(vals)//100], vals[len(vals)//20], vals[len(vals)//2], vals[-1]))
print()
print("=== violating-row corroboration (flip path) ===")
print("all violating rows have flip_exits >= 1:",
      all(r["flip_exits"] >= 1 for r in bad))
print("all violating rows have episodes == signals_entered:",
      all(r["episodes"] == r["signals_entered"] for r in bad))
print("violating-row halted counter:", Counter(r["halted"] for r in bad))
print("violating-row margin_calls>0 counter:", Counter(r["margin_calls"] > 0 for r in bad))
print("violating-row min_entry_equity range: (%.4f, %.4f)" %
      (min(r["min_entry_equity"] for r in bad), max(r["min_entry_equity"] for r in bad)))
nonbad = [r for r in rows if r["min_entry_equity"] > 0.0]
print("non-violating rows with flip_exits >= 1: %d/%d" %
      (sum(1 for r in nonbad if r["flip_exits"] >= 1), len(nonbad)))
print()

if bad:
    print("=== violating rows (min_entry_equity <= 0) ===")
    hdr = ("symbol timeframe case_name brick_pct rsi_period spacing_pct size_multiplier "
           "breakeven_tp_pct invalidation_pct min_entry_equity ending_equity net_pnl fees "
           "funding episodes fills margin_calls halted signals_entered bars_in_market "
           "max_effective_leverage").split()
    for r in bad:
        print(" ".join(str(r[h]) for h in hdr))
    print()
    print("by symbol:", Counter(r["symbol"] for r in bad))
    print("by timeframe:", Counter(r["timeframe"] for r in bad))
    print("by case:", Counter(r["case_name"] for r in bad))
    print("by spacing_pct:", Counter(r["spacing_pct"] for r in bad))
    print("by size_multiplier:", Counter(r["size_multiplier"] for r in bad))
    print("by invalidation_pct:", Counter(r["invalidation_pct"] for r in bad))
    print("halted:", Counter(r["halted"] for r in bad))
    print("margin_calls>0:", Counter(r["margin_calls"] > 0 for r in bad))
print()
print("=== global counters on the full grid ===")
print("halted rows:", sum(1 for r in rows if r["halted"] == 1))
print("margin_calls total:", sum(r["margin_calls"] for r in rows))
neg_end = [r for r in rows if r["ending_equity"] < 0]
print("rows ending equity < 0:", len(neg_end))
if neg_end:
    print("  min ending_equity: %.2f" % min(r["ending_equity"] for r in neg_end))
print("rows with min_entry_equity <= 0 anywhere in all grids -- checked only full grid here")
print("full-grid net_pnl: total=%.2f positive=%d/%d" %
      (sum(r["net_pnl"] for r in rows), sum(1 for r in rows if r["net_pnl"] > 0), len(rows)))
