#!/usr/bin/env python3
"""Benchmark the Strategy F simulate() cost per in-market bar, and extrapolate the round."""
import importlib.util
import time

import numpy as np

spec = importlib.util.spec_from_file_location(
    "sf", "/Users/hong/workspace/quant-runtime-pipeline/container/scripts/60_strategy_f_run.py")
sf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sf)

BAR_MS = 300000  # 5m
BASE_MS = 1767225600000
RAIL = {"base_quote": 1000.0, "spacing_d0": 0.01, "size_multiplier": 1.0,
        "tp": 0.01, "invalidation": 0.05}
CASE = {"brick_pct": 0.01, "rsi_period": 14}


class Fake:
    def __init__(self, n, seed=3):
        rng = np.random.default_rng(seed)
        px = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.002, n)))
        self.symbol = "SYN"
        self.timeframe = "5m"
        self.open_time_ms = np.array([BASE_MS + i * BAR_MS for i in range(n)], dtype=np.int64)
        self.open = px
        self.high = px * (1 + np.abs(rng.normal(0, 0.001, n)))
        self.low = px * (1 - np.abs(rng.normal(0, 0.001, n)))
        self.close = px * (1 + rng.normal(0, 0.0005, n))
        self.n = n
        self.bar_ms = BAR_MS
        self.price_increment = 0.01
        self.taker_fee = 0.0005
        self.leverage = 10.0
        self.margin_maint = 0.1

    def slice(self, _a, _b):
        return 0, self.n


def run(n=20000, case=CASE):
    coh = Fake(n)
    bricks, formed = sf.renko_bricks(case["brick_pct"], coh.close, "bench")
    ind = sf.indicator_events(bricks, formed, case["rsi_period"], "bench")
    sig = {(coh.symbol, coh.timeframe, case["brick_pct"], case["rsi_period"]):
           {"events": ind["events"],
            "funding": {"obs_times": np.array([], dtype=np.int64),
                        "obs_rates": np.array([], dtype=np.float64),
                        "settle_bar": np.array([], dtype=np.int64),
                        "settle_bar_closed": np.array([], dtype=np.int64)}}}
    t0 = time.time()
    m = sf.simulate(coh, case, RAIL, (0, n), {}, 1, "bench", sig, count_layers=False)
    dt = time.time() - t0
    print("bars=%d bricks=%d events=%d episodes=%d bars_in_market=%d fills=%d "
          "wall=%.2fs -> %.3f ms/bar (%.2f M bars/s)"
          % (n, len(bricks), len(ind["events"]), m["episodes"], m["bars_in_market"], m["fills"],
             dt, dt / n * 1000.0, n / dt / 1e6))
    return dt / max(m["bars_in_market"], 1), m


per_bar, m = run(20000)
# extrapolation: 20 cohorts x 9 cases x 48 DCA x (per-grid bar volume)
gr = {"5m": 494000, "15m": 165000, "30m": 82000, "1h": 41000, "4h": 10300}
SYMS = 4
cells = 9 * 48
# historical ~0.80 of the window, oos ~0.20, full 1.0, plus 7 more full-window grids
grid_factor = 0.80 + 0.20 + 1.0 + 7 * 1.0
total_bars = 0.0
for tf, bars in gr.items():
    total_bars += SYMS * cells * grid_factor * bars
est = total_bars * per_bar
print("\nestimated total in-market bar-steps: %.3e" % total_bars)
print("estimated simulate() wall time: %.0f s (%.1f h)" % (est, est / 3600.0))
