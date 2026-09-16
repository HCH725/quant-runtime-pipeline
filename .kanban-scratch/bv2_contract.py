#!/usr/bin/env python3
"""Dump the B v2 live round-spec parameter_contract (the composite-axis precedent)."""
import json

P = ("/Volumes/ExpansionDrive/qlib-results/ema-crossover-walkforward-momentum-long-short-v2/"
     "rounds/ema-crossover-walkforward-momentum-long-short-v2-r1/round-spec.json")
d = json.load(open(P))
c = d["parameter_contract"]
print(json.dumps(c, indent=1, ensure_ascii=False)[:6000])
print("=== legal_cases / expected ===")
print(json.dumps({k: v for k, v in d.items() if k in ("expected", "parameter_domain")},
                 indent=1, ensure_ascii=False)[:3000])
