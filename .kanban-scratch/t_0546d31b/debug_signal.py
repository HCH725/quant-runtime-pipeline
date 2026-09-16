#!/usr/bin/env python3
"""Debug the Strategy F signal kernel on a synthetic brick ramp."""
import importlib.util
import numpy as np

spec = importlib.util.spec_from_file_location(
    "sf", "/Users/hong/workspace/quant-runtime-pipeline/container/scripts/60_strategy_f_run.py")
sf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sf)

up = [100.0 * (1.01 ** k) for k in range(1, 13)]
down = [up[-1] * (0.99 ** k) for k in range(1, 13)]
up2 = [down[-1] * (1.01 ** k) for k in range(1, 13)]
bricks = np.array(up + down + up2, dtype=float)
formed = np.arange(len(bricks), dtype=np.int64)
ind = sf.indicator_events(bricks, formed, 7)
print("bricks:", len(bricks), "rsi periods=7")
print("rsi non-nan:", int(np.count_nonzero(~np.isnan(ind["rsi"]))))
print("raw non-nan:", int(np.count_nonzero(~np.isnan(ind["raw"]))))
print("k non-nan:", int(np.count_nonzero(~np.isnan(ind["k"]))))
print("d non-nan:", int(np.count_nonzero(~np.isnan(ind["d"]))))
print("events:", ind["events"])
for i in range(len(bricks)):
    print(i, round(ind["rsi"][i], 4) if not np.isnan(ind["rsi"][i]) else "nan",
          round(ind["raw"][i], 4) if not np.isnan(ind["raw"][i]) else "nan",
          round(ind["k"][i], 4) if not np.isnan(ind["k"][i]) else "nan",
          round(ind["d"][i], 4) if not np.isnan(ind["d"][i]) else "nan")
