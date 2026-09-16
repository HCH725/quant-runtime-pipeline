"""How many settlements land EXACTLY on an hour boundary (0 ms jitter), and at which hours?

The bar mapping `searchsorted(open_time_ms, t, 'left') - 1` puts such an instant in the PREVIOUS
bar, so any 0 ms settlement that a window holds is a candidate for the money-vs-detector question
(card t_50c28da5).
"""
import collections
import gzip
import json
import os

RAW = "/Volumes/ExpansionDrive/market-data-raw/binance/usdm/funding"
SYMS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]

for sym in SYMS:
    path = os.path.join(RAW, sym, "%s-funding.jsonl.gz" % sym)
    total = 0
    zero_ms_hours = collections.Counter()
    late_ms = collections.Counter()
    zero_list = []
    with gzip.open(path, "rt") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            ms = r["funding_time_ms"]
            if ms < 1640995200000 or ms > 1789257599999:   # registered window 2022-01-01..2026-09-11
                continue
            total += 1
            rem = ms % 3600000
            hour = (ms // 3600000) % 24
            if rem == 0:
                zero_ms_hours[hour] += 1
                zero_list.append(ms)
            else:
                late_ms[min(rem // 100, 9)] += 1       # bucket the jitter in 100 ms steps
    print("== %s == settlements=%d" % (sym, total))
    print("   exactly-on-hour settlements: %d  hours=%s"
          % (sum(zero_ms_hours.values()), dict(sorted(zero_ms_hours.items()))))
    print("   jitter buckets (0.1 s units): %s" % dict(sorted(late_ms.items())))
    if zero_list:
        print("   first 5 exact instants: %s" % [ms for ms in zero_list[:5]])
