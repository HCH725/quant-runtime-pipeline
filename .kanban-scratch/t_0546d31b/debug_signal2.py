#!/usr/bin/env python3
"""Debug the Strategy F signal kernel on a sawtooth brick path."""
import importlib.util
import numpy as np

spec = importlib.util.spec_from_file_location(
    "sf", "/Users/hong/workspace/quant-runtime-pipeline/container/scripts/60_strategy_f_run.py")
sf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sf)

for rsi_period in (7, 14, 21):
    px = 100.0
    path = []
    for _cycle in range(6):
        for _k in range(4):
            px *= 1.01
            path.append(px)
        for _k in range(4):
            px /= 1.01
            path.append(px)
    bricks = np.array(path, dtype=float)
    formed = np.arange(len(bricks), dtype=np.int64)
    ind = sf.indicator_events(bricks, formed, rsi_period)
    signs = [s for _b, s in ind["events"]]
    print(f"rsi={rsi_period} bricks={len(bricks)} events={len(ind['events'])} "
          f"up={signs.count(1)} down={signs.count(-1)} first_bar={ind['events'][0][0] if ind['events'] else None}")
    print("   events:", ind["events"][:12])
