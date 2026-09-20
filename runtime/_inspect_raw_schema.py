from __future__ import annotations
import gzip
import json
from pathlib import Path

root = Path("/Volumes/ExpansionDrive/market-data-raw/binance")
for market in ("spot", "usdm"):
    base = root / market
    print("MARKET", market, "exists", base.exists())
    for kind in ("klines", "funding", "mark", "index", "premium", "instruments"):
        path = base / kind
        if not path.exists():
            continue
        files = sorted(path.rglob("*.jsonl.gz"))
        print(" KIND", kind, "files", len(files))
        for f in files[:3]:
            print("  FILE", f.relative_to(base), "bytes", f.stat().st_size)
            try:
                with gzip.open(f, "rt", encoding="utf-8") as fh:
                    line = next((line for line in fh if line.strip()), "")
                rec = json.loads(line)
                print("   KEYS", sorted(rec), "SAMPLE", {k: rec[k] for k in sorted(rec)[:8]})
            except Exception as exc:
                print("   ERROR", repr(exc))
