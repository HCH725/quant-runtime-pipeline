"""Raw funding settlement instants around the two slice starts (read-only)."""
import gzip
import json
import os

RAW = "/Volumes/ExpansionDrive/market-data-raw/binance/usdm/funding"
SYMS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]
PROBE = {
    "2022-01-01T00:00:00Z": 1640995200000,
    "2025-10-01T00:00:00Z": 1759276800000,
}
WIN = 20 * 60 * 1000  # +/- 20 minutes

for sym in SYMS:
    path = os.path.join(RAW, sym, "%s-funding.jsonl.gz" % sym)
    rows = []
    with gzip.open(path, "rt") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            ms = r["funding_time_ms"]
            for label, tgt in PROBE.items():
                if abs(ms - tgt) <= WIN:
                    rows.append((label, ms, ms - tgt, r.get("funding_rate"), r.get("truth_status")))
    print("== %s ==" % sym)
    for label, ms, delta, rate, status in sorted(rows, key=lambda x: x[1]):
        print("   %s  delta_ms=%+6d  rate=%s  status=%s" % (label, delta, rate, status))
