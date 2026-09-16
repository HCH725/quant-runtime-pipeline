#!/usr/bin/env python3
"""Card t_50c28da5 reconnaissance: raw layout, instrument metadata, bar labelling (read-only)."""
import gzip
import json
import os
import time

RAW = "/Volumes/ExpansionDrive/market-data-raw"
USD = os.path.join(RAW, "binance", "usdm")
print("=== usdm tree ===")
for root, dirs, files in os.walk(USD):
    depth = root[len(USD):].count(os.sep)
    if depth <= 1:
        print(dir, sorted(dirs)[:8], sorted(files)[:5])
    if depth >= 1 and depth <= 2 and root.split(os.sep)[-1] in ("1h", "15m"):
        names = sorted(f for f in files if f.endswith(".jsonl.gz"))
        print("  %s: %d files %s .. %s" % (os.path.relpath(root, USD), len(names),
                                           names[0] if names else "-", names[-1] if names else "-"))

print("=== instruments ===")
ip = os.path.join(USD, "instruments", "usdm-perp-instruments.json")
doc = json.load(open(ip))
print("keys:", list(doc)[:6], "count:", len(doc.get("instruments", [])))
wanted = {"BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"}
for it in doc["instruments"]:
    f = it["fields"]
    if f.get("raw_symbol") in wanted:
        print({k: f[k] for k in ("raw_symbol", "price_increment", "maker_fee", "taker_fee",
                                 "margin_init", "margin_maint", "min_notional", "id")})

print("=== 1h bar labelling probe (first/last bars of each symbol) ===")
for sym in sorted(wanted):
    d = os.path.join(USD, "klines", sym, "1h")
    names = sorted(f for f in os.listdir(d) if f.endswith(".jsonl.gz"))
    first, last = names[0], names[-1]
    rows_f, rows_l = [], []
    for path, acc in ((os.path.join(d, first), rows_f), (os.path.join(d, last), rows_l)):
        with gzip.open(path, "rt") as fh:
            for line in fh:
                if line.strip():
                    acc.append(json.loads(line))
    print("%s files=%d span=%s..%s" % (sym, len(names), first, last))
    for label, acc in (("first-file", rows_f), ("last-file", rows_l)):
        print("   %s rows=%d  head=%s  tail=%s" % (
            label, len(acc),
            {k: acc[0][k] for k in ("open_time_ms", "close_time_ms", "open", "close")},
            {k: acc[-1][k] for k in ("open_time_ms", "close_time_ms", "open", "close")}))
        for r in acc[:3] + acc[-2:]:
            ot, ct = r["open_time_ms"], r["close_time_ms"]
            print("     ot=%s ct=%s delta_ms=%d" % (
                time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ot / 1000.0)),
                time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ct / 1000.0)), ct - ot))
    # 1h grid check on the last file
    gaps = set()
    for a, b in zip(rows_l, rows_l[1:]):
        gaps.add(b["open_time_ms"] - a["open_time_ms"])
    print("   open_time deltas:", sorted(gaps))
    print("   keys:", sorted(rows_l[0].keys()))

print("=== funding file ===")
for sym in sorted(wanted):
    p = os.path.join(USD, "funding", sym, "%s-funding.jsonl.gz" % sym)
    n = 0
    first = last = None
    if os.path.exists(p):
        with gzip.open(p, "rt") as fh:
            for line in fh:
                if not line.strip():
                    continue
                r = json.loads(line)
                n += 1
                first = first or r
                last = r
    print("%s exists=%s rows=%d first=%s last=%s" % (
        sym, os.path.exists(p), n,
        {k: first[k] for k in ("funding_time_ms", "funding_rate", "truth_status")} if first else None,
        {k: last[k] for k in ("funding_time_ms", "funding_rate", "truth_status")} if last else None))
print("=== host scripts deploy dir ===")
hd = "/Users/hong/workspace/qlib-apple-container/scripts"
for root, dirs, files in os.walk(hd):
    if ".git" in root:
        continue
    print(os.path.relpath(root, hd), sorted(f for f in files if f.endswith((".py", ".sh"))))
