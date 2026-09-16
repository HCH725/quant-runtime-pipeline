"""Bound the materiality of the one convention note found while fixing the guard (card t_50c28da5).

The D engine schedules a funding settlement on the bar whose CLOSE is the mark at the settlement
instant (the bar ENDING at it).  For an instant exactly ON a bar open, the registered text ("the
close of the bar that contains it inside the window") can also be read as the bar STARTING there.
The two readings differ in value only for an instant on an INTERIOR bar open of a hold:

  * entry boundary  -> both readings charge at the entry bar's close        (identical)
  * exit boundary   -> both readings charge at the last held bar's close    (identical)
  * interior open   -> charge moves from C[W-1] to C[W]  (this script's bound)

Interior bar opens of a registered hold: short leg 02/03/04 (01:00-05:00), secondary leg 22/23
(21:00-24:00), long leg none (one bar).  Raw read-back: only SOLUSDT carries on-boundary
settlements at those hours (its early-2022 2h/4h era).
"""
import gzip
import json
import os

RAW = "/Volumes/ExpansionDrive/market-data-raw"
FUND = os.path.join(RAW, "binance/usdm/funding")
KLINES = os.path.join(RAW, "binance/usdm/klines")
SYMS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]
LO, HI = 1640995200000, 1789257599999
INTERIOR_HOURS = {2, 3, 4, 22, 23}
BAR_MS = 3600000
BASE = 1640995200000


def closes(sym):
    """1h closes of the registered window, indexed by (t - BASE) // BAR_MS (gzipped monthly jsonl)."""
    root = os.path.join(KLINES, sym, "1h")
    out = {}
    for name in sorted(os.listdir(root)):
        if not name.endswith(".jsonl.gz"):
            continue
        with gzip.open(os.path.join(root, name), "rt") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                t = int(r.get("open_time_ms", r.get("open_time")))
                if LO <= t <= HI:
                    out[(t - BASE) // BAR_MS] = float(r["close"])
    return out


total_bound = 0.0
print("%-9s %-22s %10s %12s %14s" % ("symbol", "instant", "rate", "|dC|/C", "bound_usdt"))
for sym in SYMS:
    c = closes(sym)
    if not c:
        print("%s: klines not found" % sym)
        continue
    with gzip.open(os.path.join(FUND, sym, "%s-funding.jsonl.gz" % sym), "rt") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            ms = r["funding_time_ms"]
            if ms < LO or ms > HI or ms % BAR_MS != 0:
                continue                       # only exact-on-boundary instants matter here
            if ((ms // BAR_MS) % 24) not in INTERIOR_HOURS:
                continue
            w = (ms - BASE) // BAR_MS          # the bar that OPENS at the instant
            cw, cw1 = c.get(w), c.get(w - 1)
            if not cw or not cw1:
                continue
            rate = float(r["funding_rate"])
            rel = abs(cw - cw1) / cw1
            # 12 registered tranches, base_quote 1000 USDT, 10x, geometric 1.1: the worst case is
            # the full ladder deployed (11 routine levels + reserve), i.e. <= 185,000 USDT notional.
            bound = 185000.0 * rel * abs(rate)
            total_bound += bound
            import time
            stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ms / 1000.0))
            print("%-9s %-22s %10.6f %12.6f %14.6f" % (sym, stamp, rate, rel, bound))

print()
print("upper bound on the total |funding| difference of this convention note: %.4f USDT"
      % total_bound)
print("(vs starting equity 30000 USDT; the actual deployed notional is <= the full ladder)")
