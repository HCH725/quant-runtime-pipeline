#!/usr/bin/env python3
"""Structural summary of Strategy E's frozen round-spec / run-spec / result.json,
plus the raw data inventory. Read-only."""
import json, os, glob

RES = "/Volumes/ExpansionDrive/qlib-results"

def summarize(obj, prefix="", depth=0, maxdepth=3):
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                if depth < maxdepth:
                    out.append(f"{path}: dict({len(v)}) keys={list(v)[:12]}")
                    out.extend(summarize(v, path, depth + 1, maxdepth))
                else:
                    out.append(f"{path}: dict({len(v)})")
            elif isinstance(v, list):
                sample = v[0] if v else None
                s = json.dumps(sample, ensure_ascii=False)
                out.append(f"{path}: list({len(v)}) first={s[:220]}")
            else:
                s = json.dumps(v, ensure_ascii=False)
                out.append(f"{path}: {s[:220]}")
    return out

fam = "copula-cmi-pairs-relative-value-perp-v1"
paths = sorted(glob.glob(f"{RES}/{fam}/rounds/*/round-spec.json"))
print("round-specs:", paths)
for p in paths:
    d = json.load(open(p))
    print(f"\n===== {p} ({os.path.getsize(p)} bytes) =====")
    for line in summarize(d):
        print("  " + line)

for p in sorted(glob.glob(f"{RES}/{fam}/rounds/*/attempts/*/run-spec.json")):
    d = json.load(open(p))
    print(f"\n===== {p} ({os.path.getsize(p)} bytes) =====")
    for line in summarize(d):
        print("  " + line)

for p in sorted(glob.glob(f"{RES}/{fam}/rounds/*/attempts/*/result.json")):
    d = json.load(open(p))
    print(f"\n===== {p} ({os.path.getsize(p)} bytes) =====")
    for line in summarize(d, maxdepth=2):
        print("  " + line)
    break

print("\n===== raw _meta/INVENTORY.md =====")
print(open("/Volumes/ExpansionDrive/market-data-raw/_meta/INVENTORY.md").read()[:4000])
