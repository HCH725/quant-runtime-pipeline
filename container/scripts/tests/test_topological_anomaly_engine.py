#!/usr/bin/env python3
"""Minimal executable self-check for topological_anomaly_run.py.

It does not touch Qlib or external data: it checks the causal signal primitives and the execution
ledger on a deterministic synthetic panel.  The production run records the same checks separately.
"""
import importlib.util
import os
import sys
from pathlib import Path

import numpy as np

SCRIPT = Path(__file__).resolve().parents[1] / "170_topological_anomaly_run.py"
spec = importlib.util.spec_from_file_location("topological_anomaly_run", SCRIPT)
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)


def main():
    rng = np.random.default_rng(7)
    t = 720
    # Common factor plus one deliberately idiosyncratic asset; all prices remain positive.
    factor = rng.normal(0.0001, 0.002, size=t)
    returns = np.column_stack([
        factor + rng.normal(0, 0.0008, size=t),
        factor + rng.normal(0, 0.0009, size=t),
        factor + rng.normal(0, 0.0010, size=t),
        -0.25 * factor + rng.normal(0, 0.0012, size=t),
    ])
    closes = 100.0 * np.exp(np.cumsum(returns, axis=0))
    layers = engine.make_layers(closes, bars_per_day=24, seed=20260920)
    assert len(layers["base"]) == 3
    assert all(x["scores"].shape == (t, 4) for x in layers["base"])
    assert np.array_equal(layers["returns"][1:], closes[1:] / closes[:-1] - 1.0)
    # Prefix invariance: BallMapper uses no post-prefix observation.
    for cut in (120, 360, 719):
        prefix = engine.ballmapper_scores(layers["returns"][:cut + 1])
        full = layers["base"][0]["scores"]
        if np.isfinite(prefix[-1, 0]) and np.isfinite(full[cut, 0]):
            assert abs(float(prefix[-1, 0]) - float(full[cut, 0])) < 1e-10
    open_px = closes[:, 0]
    high = open_px * 1.01
    low = open_px * 0.99
    close = open_px
    ms = np.arange(t, dtype=np.int64) * 3600000 + 1743465600000
    funding = np.zeros(t)
    params = {"spacing_pct": 0.02, "size_multiplier": 1.0,
              "breakeven_tp_pct": 0.01, "invalidation_pct": 0.05}
    synthetic = {"pos": np.zeros(t, dtype=np.int8), "events": np.zeros(t, dtype=np.int8),
                 "scores": np.zeros(t), "trajectory": np.zeros(t), "z": np.zeros(t)}
    synthetic["events"][20] = 1
    synthetic["pos"][20:100] = 1
    m = engine.simulate(open_px, high, low, close, ms, funding, synthetic, params, 24, 48,
                         0, t, {}, "BTCUSDT")
    assert m["episodes"] == 1
    assert m["decomposition_ok"]
    assert abs(m["gross_pnl"] - m["fees"] - m["funding"] - m["net_pnl"]) <= 1e-6
    assert m["fills"] >= 2
    assert engine.RAW_ROOT == "/data/raw/binance/usdm"
    assert engine.LEVERAGE == 10.0
    assert engine.MAX_LAYERS == 11
    assert engine.SLIPPAGE_TICKS == 1
    print("topological anomaly engine self-check: PASS")


if __name__ == "__main__":
    main()
