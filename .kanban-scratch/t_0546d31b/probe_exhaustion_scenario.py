#!/usr/bin/env python3
"""Dev probe: dump the flip-exhaustion scenario metrics for the frozen u1 engine and the
fixed engine side by side, to author exact regression assertions."""
import importlib.util
import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
U1 = os.path.join(HERE, "u1_engine_frozen.py")
FIXED = "/Users/hong/workspace/quant-runtime-pipeline/container/scripts/60_strategy_f_run.py"

BAR_MS = 3600000
BASE_MS = 1767225600000
TICK, FEE, LEV = 0.01, 0.0005, 10.0
BIG_RAIL = {"base_quote": 75000.0, "spacing_d0": 0.5, "size_multiplier": 1.0,
            "tp": 0.01, "invalidation": 0.05}
CASE = {"brick_pct": 0.02, "rsi_period": 14}


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeCohort:
    def __init__(self, o, h, l, c, mmaint=0.002):
        self.symbol = "SYNTH"
        self.timeframe = "1h"
        self.open_time_ms = np.array([BASE_MS + i * BAR_MS for i in range(len(c))],
                                     dtype=np.int64)
        self.open = np.array(o, dtype=float)
        self.high = np.array(h, dtype=float)
        self.low = np.array(l, dtype=float)
        self.close = np.array(c, dtype=float)
        self.n = len(c)
        self.bar_ms = BAR_MS
        self.price_increment = TICK
        self.taker_fee = FEE
        self.leverage = LEV
        self.margin_maint = mmaint

    def slice(self, _a, _b):
        return 0, self.n


def run(mod, cohort, events, rail=BIG_RAIL):
    fund = {"obs_times": np.array([], dtype=np.int64),
            "obs_rates": np.array([], dtype=np.float64),
            "settle_bar": np.array([], dtype=np.int64),
            "settle_bar_closed": np.array([], dtype=np.int64)}
    signals = {("SYNTH", "1h", CASE["brick_pct"], CASE["rsi_period"]):
               {"events": events, "funding": fund}}
    return mod.simulate(cohort, CASE, rail, (0, cohort.n), {}, 0, "full", signals,
                        count_layers=True)


KEYS = ("episodes", "signals_seen", "signals_entered", "tp_hits", "stop_hits", "flip_exits",
        "slice_end_flats", "open_at_end", "margin_calls", "halted", "min_entry_equity",
        "net_pnl", "fees", "gross_pnl", "ending_equity", "fills")


def scenario(bar, px, mmaint=0.002):
    n = 30
    o = [100.0] * n
    h = [100.0] * n
    l = [100.0] * n
    c = [100.0] * n
    o[bar] = h[bar] = l[bar] = c[bar] = px
    return FakeCohort(o, h, l, c, mmaint=mmaint)


SCENARIOS = (("flip-gap: short signal on bar 8 fills on the 96 bar", [(2, 1), (8, -1)], 9, 96.0),
             ("stop: long stopped out on the 95 bar, next signal ignored", [(2, 1), (12, 1)], 8, 95.0))

for label, path in (("u1-frozen f-v1-engine-1.0.0", U1), ("fixed f-v1-engine-1.0.1", FIXED)):
    mod = load(path, "sf_" + label.split()[0])
    print("=== %s" % label)
    for name, events, bar, px in SCENARIOS:
        m = run(mod, scenario(bar, px), events)
        print("  %s" % name)
        for k in KEYS:
            v = m[k]
            print("    %-22s %s" % (k, round(v, 6) if isinstance(v, float) else v))
