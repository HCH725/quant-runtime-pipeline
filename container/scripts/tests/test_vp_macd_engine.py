"""Focused executable checks for the direct VP-MACD production engine."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
import gzip
import json

import numpy as np

p = Path(__file__).resolve().parents[1] / "290_vp_macd_run.py"
spec = importlib.util.spec_from_file_location("vp_macd", p)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def panel(n=80):
    close = np.linspace(100.0, 120.0, n) + np.sin(np.arange(n) * 0.15)
    op = close - 0.3
    high = close + 0.7 + np.sin(np.arange(n) * 0.4) ** 2
    low = op - 0.8
    return {"open": op, "high": high, "low": low, "close": close,
            "volume": np.linspace(100, 200, n),
            "open_ms": np.arange(n, dtype=np.int64) * m.MS_DAY}


class TestVPMACD(unittest.TestCase):
    def setUp(self):
        self.dca = {"spacing_pct": 0.01, "size_multiplier": 1.0,
                    "breakeven_tp_pct": 0.03, "invalidation_pct": 0.1}
        self.inst = {"tick": 0.01, "taker_fee": 0.001}

    def test_adjusted_formula_and_no_lookahead(self):
        a = panel()
        out = m.adjusted(a, n=3, sigma_window=3)
        t = 17
        span = a["high"] - a["low"]
        values = [a["close"][i] * a["volume"][i] *
                  np.std(span[i-2:i+1]) / a["close"][i] *
                  abs(a["close"][i] - a["open"][i]) / span[i]
                  for i in range(t - 3, t)]
        self.assertAlmostEqual(out[t], sum(values) / sum(a["volume"][t-3:t]))
        original_entry, original_exit = m.signals(a, 0.88, n=3, sigma_window=3)
        b = {k: v.copy() for k, v in a.items()}
        for key in ("open", "close", "high", "low", "volume"):
            b[key][t] *= 1.2
        self.assertEqual(out[t], m.adjusted(b, n=3, sigma_window=3)[t])
        alt_entry, alt_exit = m.signals(b, 0.88, n=3, sigma_window=3)
        np.testing.assert_array_equal(original_entry[:t+1], alt_entry[:t+1])
        np.testing.assert_array_equal(original_exit[:t+1], alt_exit[:t+1])

    def test_next_open_fee_funding_exit_and_negative_decomposition_control(self):
        a = {"open": np.full(5, 100.0), "high": np.full(5, 100.5),
             "low": np.full(5, 99.5), "close": np.full(5, 100.0),
             "open_ms": np.arange(5, dtype=np.int64) * m.MS_DAY}
        entry, exit_ = np.zeros(5, bool), np.zeros(5, bool)
        entry[0], exit_[2] = True, True
        events = [[] for _ in range(5)]
        events[2] = [(2 * m.MS_DAY + 10, 0.05)]
        trace = []
        m.TRACE = trace
        try:
            result = m.simulate(a, events, (entry, exit_), self.dca, 0, 5, {}, self.inst)
        finally:
            m.TRACE = None
        qty = 10_000 / 100.01
        expected_gross = qty * (99.99 - 100.01)
        expected_fee = 10.0 + qty * 99.99 * 0.001
        expected_funding = qty * 0.05
        self.assertEqual(result["episodes"], 1)
        self.assertEqual(result["signal_exits"], 1)
        self.assertEqual(result["fills"], 2)
        self.assertAlmostEqual(result["gross_pnl"], expected_gross)
        self.assertAlmostEqual(result["fees"], expected_fee)
        self.assertAlmostEqual(result["funding"], expected_funding)
        self.assertAlmostEqual(result["net_pnl"], expected_gross - expected_fee - expected_funding)
        self.assertTrue(result["decomposition_ok"])
        self.assertEqual([x["bar"] for x in trace if x["kind"] == "entry"], [1])
        self.assertEqual([x["bar"] for x in trace if x["kind"] == "exit"], [3])
        self.assertAlmostEqual(trace[-1]["equity"], result["ending_equity"])
        self.assertFalse(m.decomposition_ok({**result, "gross_pnl": result["gross_pnl"] + 1}))
        expensive = m.simulate(a, events, (entry, exit_), self.dca, 0, 5,
                               {"fee_mult": 2, "funding_mult": 2}, self.inst)
        self.assertAlmostEqual(expensive["gross_pnl"], expected_gross)
        self.assertLess(expensive["ending_equity"], result["ending_equity"])
        self.assertAlmostEqual(expensive["funding"], 2 * expected_funding)
        self.assertAlmostEqual(expensive["fees"], 2 * expected_fee)

    def test_multilevel_crossing_and_reserve(self):
        a = {"open": np.full(3, 100.0), "high": np.full(3, 100.4),
             "low": np.array([99.9, 99.9, 94.5]), "close": np.full(3, 99.0),
             "open_ms": np.arange(3, dtype=np.int64) * m.MS_DAY}
        entry = np.array([True, False, False])
        result = m.simulate(a, [[], [], []], (entry, np.zeros(3, bool)),
                            self.dca, 0, 3, {}, self.inst)
        self.assertEqual(result["episodes"], 1)
        self.assertEqual(result["adds"], 5)
        self.assertEqual(result["fills"], 7)
        self.assertEqual(result["layer_hist"][6], 1)
        self.assertEqual(result["layer_hist"][11], 0)
        self.assertTrue(result["decomposition_ok"])

    def test_trace_is_inert_and_only_official_funding_charged(self):
        a = panel(8)
        entry = np.zeros(8, bool)
        entry[0] = True
        signal = (entry, np.zeros(8, bool))
        funding = [[] for _ in range(8)]
        funding[3].append((int(a["open_ms"][3]) + 100, 0.05))
        basic = m.simulate(a, funding, signal, self.dca, 0, 8, {}, self.inst)
        trace = []
        m.TRACE = trace
        try:
            replay = m.simulate(a, funding, signal, self.dca, 0, 8, {}, self.inst)
        finally:
            m.TRACE = None
        self.assertEqual(basic, replay)
        self.assertEqual(len([r for r in trace if r["kind"] == "equity"]), 8)
        with tempfile.TemporaryDirectory() as temp:
            old_root = m.RAW_ROOT
            m.RAW_ROOT = temp
            path = Path(temp) / "binance/usdm/funding/BTCUSDT/BTCUSDT-funding.jsonl.gz"
            path.parent.mkdir(parents=True)
            with gzip.open(path, "wt") as f:
                for status in ("modeled", "official"):
                    f.write(json.dumps({"funding_time_ms": int(a["open_ms"][3]) + 100,
                                        "truth_status": status, "funding_rate": "0.001",
                                        "mark_price": "100"}) + "\n")
            try:
                observed, report = m.funding_events("BTCUSDT", a["open_ms"])
            finally:
                m.RAW_ROOT = old_root
            self.assertEqual(report["official"], 1)
            self.assertEqual(report["modeled_ignored"], 1)
            self.assertEqual(len(observed[3]), 1)
            self.assertEqual(observed[3][0][1], 0.1)

    def test_source_boundary_and_full_grid_registered(self):
        self.assertEqual(m.PHASES["historical"], ("2022-01-01", "2022-12-31"))
        self.assertEqual(m.PHASES["oos"], ("2023-01-01", "2026-02-28"))
        self.assertEqual(m.expected()["case_evaluations_per_grid"], 4 * 11 * 48)
        self.assertEqual(len(m.GRIDS), 10)

    def test_selector_is_historical_only_and_lexical(self):
        row = {"symbol": "BTCUSDT", "timeframe": "1d", "lambda": 0.88,
               **self.dca, "grid": "historical", "episodes": 6, "net_pnl": 1,
               "sharpe": 1, **{k: 1.0 for k in m.metric_fields({k: 1.0 for k in
                   ("gross_pnl", "fees", "funding", "net_pnl", "ending_equity",
                    "episodes", "fills", "adds", "turnover_usdt", "sharpe",
                    "max_dd_pct", "max_dd_usdt", "annualized_return",
                    "max_effective_leverage", "capital_utilization", "tp_hits",
                    "stop_hits", "margin_calls", "end_exits", "signal_exits")})}}
        rows = {g: [{**row, "grid": g}] for g in m.GRIDS}
        with self.assertRaisesRegex(ValueError, "OOS entered historical selector"):
            m.select({**rows, "historical": [{**row, "grid": "oos"}]})
        self.assertEqual(m.expected()["case_evaluations_total"], 4 * 11 * 48 * 10)


if __name__ == "__main__":
    unittest.main()
