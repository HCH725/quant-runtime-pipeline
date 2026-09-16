#!/usr/bin/env python3
"""Card t_50c28da5: 1h continuity over the registered window + funding schedule probe."""
import gzip
import json
import os
import time
from collections import Counter

USD = "/Volumes/ExpansionDrive/market-data-raw/binance/usdm"
SYMS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]
START = "2022-01-01"
END = "2026-09-11"


def utc_ms(d):
    return int(time.mktime(time.strptime(d + " 00:00:00", "%Y-%m-%d %H:%M:%S")) - time.timezone) * 1000


lo = utc_ms(START)
hi = utc_ms(END) + 86400000 - 1

for sym in SYMS:
    d = os.path.join(USD, "klines", sym, "1h")
    names = sorted(f for f in os.listdir(d) if f.endswith(".jsonl.gz") and
                   START[:7] <= f[len(sym) + 4:-len(".jsonl.gz")] <= END[:7])
    times = []
    for name in names:
        with gzip.open(os.path.join(d, name), "rt") as fh:
            for line in fh:
                if line.strip():
                    ms = json.loads(line)["open_time_ms"]
                    if lo <= ms <= hi:
                        times.append(ms)
    deltas = Counter(b - a for a, b in zip(times, times[1:]))
    print("%s: files=%d bars=%d first=%s last=%s deltas=%s" % (
        sym, len(names), len(times),
        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(times[0] / 1000.0)),
        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(times[-1] / 1000.0)),
        sorted(deltas.items())[:6]))

print("=== funding schedules ===")
for sym in SYMS:
    p = os.path.join(USD, "funding", sym, "%s-funding.jsonl.gz" % sym)
    ts, st = [], Counter()
    with gzip.open(p, "rt") as fh:
        for line in fh:
            if line.strip():
                r = json.loads(line)
                ts.append(r["funding_time_ms"])
                st[r.get("truth_status")] += 1
    deltas = Counter(b - a for a, b in zip(ts, ts[1:]))
    hours = Counter(time.strftime("%H:%M", time.gmtime(t / 1000.0)) for t in ts)
    jitter = Counter((t % 28800000) for t in ts)
    print("%s rows=%d deltas=%s status=%s hours=%s" % (
        sym, len(ts), sorted(deltas.items()), dict(st), dict(hours)))
    print("   ms-offset-within-8h-grids: %s" % sorted(jitter.items())[:8])
