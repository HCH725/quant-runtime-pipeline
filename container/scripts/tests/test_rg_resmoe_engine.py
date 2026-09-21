#!/usr/bin/env python3
"""Deterministic host self-check for the RG-ResMoE engine; no Qlib or raw data required."""
import importlib.util
from pathlib import Path

import numpy as np

SCRIPT = Path(__file__).resolve().parents[1] / "180_rg_resmoe_run.py"
spec = importlib.util.spec_from_file_location("rg_resmoe_run", SCRIPT)
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)


def main():
    rng = np.random.default_rng(17)
    t = 420
    factor = rng.normal(0.0002, 0.012, size=t)
    returns = np.column_stack([
        factor + rng.normal(0, 0.004, size=t),
        factor + rng.normal(0, 0.005, size=t),
        factor + rng.normal(0, 0.006, size=t),
        -0.35 * factor + rng.normal(0, 0.007, size=t),
    ])
    closes = 100.0 * np.exp(np.cumsum(returns, axis=0))
    x, target, z, logret = engine.feature_panel(closes)
    assert x.shape == (t, 4, 16)
    assert target.shape == (t, 4)
    assert z.shape == (t, 4, 2)
    model = engine.model_panel(closes, train_end=300, seed=engine.SEED)
    assert model["scores"].shape == (t, 4)
    assert model["training_samples"] > 40
    assert np.isfinite(model["scores"]).sum() > 0
    assert np.count_nonzero(model["events"]) > 0
    # Causal feature guard: changing a future close cannot alter an earlier feature.
    altered = closes.copy()
    altered[350:] *= 3.0
    x2, target2, z2, _ = engine.feature_panel(altered)
    assert np.allclose(x[:300], x2[:300], equal_nan=True)
    assert np.allclose(z[:300], z2[:300], equal_nan=True)
    params = {"spacing_pct": 0.02, "size_multiplier": 1.0,
              "breakeven_tp_pct": 0.01, "invalidation_pct": 0.05}
    open_px = closes[:, 0]
    high = open_px * 1.01
    low = open_px * 0.99
    ms = np.arange(t, dtype=np.int64) * 86400000 + 1743465600000
    funding = np.zeros(t)
    layer = {"events": np.zeros(t, dtype=np.int8), "pos": np.zeros(t, dtype=np.int8),
             "scores": np.zeros(t), "trajectory": np.zeros(t), "z": np.zeros(t)}
    layer["events"][40] = 1
    layer["pos"][40:100] = 1
    metrics = engine.RAIL.simulate(open_px, high, low, open_px, ms, funding, layer,
                                   params, 60, 5, 0, t, {}, "BTCUSDT")
    assert metrics["episodes"] == 1
    assert metrics["decomposition_ok"]
    assert abs(metrics["gross_pnl"] - metrics["fees"] - metrics["funding"] - metrics["net_pnl"]) <= 1e-6
    assert engine.disposition_for_survivor_count(0) == "REJECT / NO_SURVIVOR"
    assert engine.disposition_for_survivor_count(1) == "SURVIVOR_FOUND"
    assert engine.disposition_for_survivor_count(2) == "MULTIPLE_SURVIVORS"
    assert engine.FAMILY_ID.startswith("cross-sectional-volatility-regime-gated")
    assert [tf["raw_interval"] for tf in engine.TIMEFRAMES] == ["5m", "15m", "30m", "1h", "4h", "1d", "1w"]
    assert [tf["bars_per_day"] for tf in engine.TIMEFRAMES] == [288, 96, 48, 24, 6, 1, 1]
    print("RG-ResMoE engine self-check: PASS")


if __name__ == "__main__":
    main()
