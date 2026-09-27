#!/usr/bin/env python3
"""Focused executable checks for the same-day open-to-close engine."""
import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path

import numpy as np

ENGINE_PATH = os.environ.get(
    "SPY_ENGINE_PATH",
    str(Path(__file__).resolve().parents[1] / "260_spy_open_close_run.py"),
)
spec = importlib.util.spec_from_file_location("spy_open_close", ENGINE_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError("cannot load engine: %s" % ENGINE_PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

INST = {"tick": 0.01, "taker_fee": 0.0005}


def panel(days=16, start_price=100.0, step=0.01):
    ms = np.arange(mod.utc_ms("2025-01-01"),
                   mod.utc_ms("2025-01-01") + days * mod.MS_DAY, mod.MS_DAY, dtype=np.int64)
    close = start_price * np.power(1.0 + step, np.arange(days))
    open_ = np.r_[start_price, close[:-1]]
    high = np.maximum(open_, close) * 1.002
    low = np.minimum(open_, close) * 0.998
    return {"open_ms": ms, "open": open_, "high": high, "low": low, "close": close,
            "volume": np.ones(days)}


def intraday_from(daily):
    """288 5m bars per UTC day: first open and last close reconcile the daily row."""
    ms, o, h, l, c, v = [], [], [], [], [], []
    for i, t in enumerate(daily["open_ms"]):
        for j in range(288):
            ms.append(int(t) + j * mod.MS_5M)
            price_o = daily["open"][i] if j == 0 else c[-1]
            frac = (j + 1) / 288.0
            price_c = daily["open"][i] + (daily["close"][i] - daily["open"][i]) * frac
            o.append(price_o)
            c.append(price_c)
            h.append(max(price_o, price_c) * 1.0005)
            l.append(min(price_o, price_c) * 0.9995)
            v.append(1.0)
    return {"open_ms": np.asarray(ms, dtype=np.int64), "open": np.asarray(o),
            "high": np.asarray(h), "low": np.asarray(l), "close": np.asarray(c),
            "volume": np.asarray(v)}


def fixture(days=8, step=0.01):
    """(daily panel, per-day funding amounts [(ms, rate*mark)], exec cache)."""
    d = panel(days=days, step=step)
    cache = mod.build_exec_cache(d, intraday_from(d))
    amounts = [[] for _ in d["open_ms"]]
    return d, amounts, cache


def simulate(d, amounts, cache, layer, i0, i1, dca=None, cost=None, case=None):
    return mod.simulate(d, cache, amounts, layer, case or mod.CASES[0],
                        dca or dict(mod.DCA_GRID[0]), i0, i1, cost or {}, INST)


class TestSignalCausality(unittest.TestCase):
    def test_feature_rows_exclude_same_day_close(self):
        p = panel(320)
        layer = mod.signal_layer(p, mod.CASES[0])
        defined = np.flatnonzero(np.isfinite(layer["forecast"]))
        self.assertGreaterEqual(len(defined), 5)
        self.assertTrue(np.all(layer["train_counts"][defined] >= mod.TRAIN_WARMUP))
        self.assertGreater(int(np.sum(layer["fit_counts"])), 0)

    def test_tau_grid_is_the_source_percent_grid(self):
        self.assertEqual(mod.TAU_GRID, (0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5))
        self.assertEqual([c["tau_pct"] for c in mod.CASES[:8]], list(mod.TAU_GRID))

    def test_tau_threshold_is_measured_in_percent(self):
        # forecast 101 vs prior close 100 => delta 1.0%; tau 1.0% admits, tau 1.5% rejects.
        p = panel(days=6, step=0.0)
        forecast = np.full(6, np.nan)
        forecast[3:] = p["close"][2:-1] * 1.01
        lo = dict(mod.CASES[1]); lo["tau_pct"] = 1.0
        hi = dict(lo); hi["tau_pct"] = 1.5
        self.assertEqual(int(mod.derive_signal(p, lo, forecast)[3]), 1)
        self.assertEqual(int(mod.derive_signal(p, hi, forecast)[3]), 0)

    def test_tau_increases_selectivity(self):
        p = panel(400)
        low = mod.signal_layer(p, mod.CASES[0])["signal"]
        high = mod.signal_layer(p, mod.CASES[6])["signal"]
        self.assertGreaterEqual(int(np.sum(low)), int(np.sum(high)))

    def test_all_tau_cases_share_one_fit_path(self):
        # Thresholds must be derived from the same fitted forecast, not refit per tau.
        p = panel(300)
        reg = mod.walk_forward(p, "regression")
        for case in mod.CASES[:8]:
            layer = mod.signal_layer(p, case)
            np.testing.assert_array_equal(layer["forecast"], reg["forecast"])


class TestExecutionAccounting(unittest.TestCase):
    def test_costs_move_net_but_not_gross(self):
        d, amounts, cache = fixture(days=8)
        layer = np.ones(len(d["open_ms"]), dtype=np.int8)
        dca = dict(mod.DCA_GRID[0])
        base = simulate(d, amounts, cache, layer, 1, 7, dca=dca)
        stressed = simulate(d, amounts, cache, layer, 1, 7, dca=dca, cost={"fee_mult": 2.0})
        self.assertGreater(base["fees"], 0)
        self.assertAlmostEqual(base["gross_pnl"], stressed["gross_pnl"], places=8)
        self.assertLess(stressed["net_pnl"], base["net_pnl"])
        self.assertTrue(base["decomposition_ok"])

    def test_negative_decomposition_control(self):
        d, amounts, cache = fixture(days=6, step=0.0)
        m = simulate(d, amounts, cache, np.ones(6, dtype=np.int8), 1, 5)
        self.assertTrue(m["decomposition_ok"])
        self.assertNotAlmostEqual(m["gross_pnl"] - m["fees"] - m["funding"],
                                  m["net_pnl"] + 123.0, places=3)

    def test_official_funding_is_not_modeled(self):
        d, amounts, cache = fixture(days=4, step=0.0)
        # One official observation (rate*mark = 0.0001*100 = 0.01 per unit) at 08:00 UTC.
        amounts[1] = [(int(d["open_ms"][1]) + 8 * 3600_000, 0.0001 * 100.0)]
        m = simulate(d, amounts, cache, np.ones(4, dtype=np.int8), 1, 4)
        self.assertGreater(m["funding"], 0)
        self.assertTrue(m["decomposition_ok"])

    def test_fast_path_matches_hand_computed_ledger(self):
        # step small enough that no add/stop/TP level is reachable => hold-to-close fast path.
        d, amounts, cache = fixture(days=6, step=0.001)
        layer = np.zeros(6, dtype=np.int8)
        layer[2] = 1
        amounts[2] = [(int(d["open_ms"][2]) + 8 * 3600_000, 0.0002 * 50.0)]
        dca = dict(mod.DCA_GRID[0])
        m = simulate(d, amounts, cache, layer, 0, 6, dca=dca)
        entry = d["open"][2] + INST["tick"]
        quote0 = mod.BASE_QUOTE * mod.LEVERAGE
        qty = quote0 / entry
        fee0 = quote0 * INST["taker_fee"]
        exit_px = d["close"][2] - INST["tick"]
        exit_fee = qty * abs(exit_px) * INST["taker_fee"]
        funding = qty * 0.0002 * 50.0
        gross = qty * (exit_px - entry)
        self.assertEqual(m["episodes"], 1)
        self.assertEqual(m["end_exits"], 1)
        self.assertEqual(m["layer_hist"][0], 1)
        self.assertAlmostEqual(m["gross_pnl"], gross, places=6)
        self.assertAlmostEqual(m["funding"], funding, places=6)
        self.assertAlmostEqual(m["net_pnl"], gross - fee0 - exit_fee - funding, places=6)
        self.assertTrue(m["decomposition_ok"])

    def test_detailed_path_charges_funding_once_and_takes_tp(self):
        d, amounts, cache = fixture(days=6, step=0.001)
        intr = intraday_from(d)
        # Force the detailed path: session high above the TP only after the 08:00 settlement.
        intr["high"][3 * 288 + 100:4 * 288] = d["open"][3] * 1.05
        cache = mod.build_exec_cache(d, intr)
        layer = np.zeros(6, dtype=np.int8)
        layer[3] = 1
        amounts[3] = [(int(d["open_ms"][3]) + 8 * 3600_000, 0.0002 * 50.0)]
        m = simulate(d, amounts, cache, layer, 0, 6)
        entry = d["open"][3] + INST["tick"]
        qty = mod.BASE_QUOTE * mod.LEVERAGE / entry
        self.assertEqual(m["tp_hits"], 1)
        self.assertEqual(m["episodes"], 1)
        # Exactly one settlement: the old per-bar loop would have charged it 288 times.
        self.assertAlmostEqual(m["funding"], qty * 0.0002 * 50.0, places=6)
        self.assertTrue(m["decomposition_ok"])

    def test_window_boundary_outside_panel_is_rejected(self):
        d, amounts, cache = fixture(days=4)
        with self.assertRaises(ValueError):
            simulate(d, amounts, cache, np.ones(4, dtype=np.int8), 0, 5)


class TestRawWindowCoverage(unittest.TestCase):
    """Regression for the shard-month filter bug that silently truncated the window."""

    def setUp(self):
        import gzip
        import json
        import tempfile
        self._tmp = tempfile.mkdtemp(prefix="spy_raw_")
        self._gzip = gzip
        self._json = json
        self._orig_root = mod.RAW_ROOT
        mod.RAW_ROOT = self._tmp

    def tearDown(self):
        mod.RAW_ROOT = self._orig_root

    def _shard(self, symbol, interval, month, rows):
        root = Path(self._tmp) / "binance/usdm/klines" / symbol / interval
        root.mkdir(parents=True, exist_ok=True)
        path = root / ("%s-%s-%s.jsonl.gz" % (symbol, interval, month))
        with self._gzip.open(path, "wt", encoding="utf-8") as fh:
            for t in rows:
                fh.write(self._json.dumps({"open_time_ms": t, "open": 100.0, "high": 101.0,
                                           "low": 99.0, "close": 100.5, "volume": 1.0}) + "\n")
        return path

    def test_end_month_shard_is_selected(self):
        aug = mod.utc_ms("2026-08-31")
        sep = mod.utc_ms("2026-09-01")
        self._shard("TESTUSDT", "1d", "2026-08", [aug])
        self._shard("TESTUSDT", "1d", "2026-09", [sep])
        picked = [f.name for f in mod.raw_files("TESTUSDT", "1d", "2026-08-31", "2026-09-01")]
        self.assertEqual(sorted(picked), ["TESTUSDT-1d-2026-08.jsonl.gz",
                                          "TESTUSDT-1d-2026-09.jsonl.gz"])

    def test_load_rows_requires_full_window_coverage(self):
        aug = mod.utc_ms("2026-08-31")
        sep = mod.utc_ms("2026-09-01")
        self._shard("TESTUSDT", "1d", "2026-08", [aug])
        with self.assertRaises(RuntimeError):
            mod.load_rows("TESTUSDT", "1d", "2026-08-31", "2026-09-01", mod.FIELDS)
        self._shard("TESTUSDT", "1d", "2026-09", [sep])
        panel = mod.load_rows("TESTUSDT", "1d", "2026-08-31", "2026-09-01", mod.FIELDS)
        self.assertEqual(list(panel["open_ms"]), [aug, sep])


class TestFrozenSpecTemplates(unittest.TestCase):
    """Load the production templates and exercise the engine's fail-closed validator."""

    def _template(self, name):
        import json
        if name.startswith("spy_open_close_run"):
            path = Path(os.environ.get("SPY_RUN_TEMPLATE_PATH",
                                       "/tmp/spy_open_close_run_spec.template.json"))
        else:
            path = Path(os.environ.get("SPY_ROUND_TEMPLATE_PATH",
                                       "/tmp/spy_open_close_round_spec.template.json"))
        if not path.is_file():
            self.skipTest("template not present: %s" % path)
        return json.loads(path.read_text())

    def test_round_spec_contract_validates(self):
        pc_path = Path(os.environ.get("SPY_PARAMETER_CONTRACT_PATH", "/tmp/parameter_contract.py"))
        if not pc_path.is_file():
            self.skipTest("parameter contract not staged: %s" % pc_path)
        sys.path.insert(0, str(pc_path.parent))
        import parameter_contract as pc
        spec = self._template("spy_open_close_round_spec.template.json")
        spec["round_id"] = mod.FAMILY_ID + "-r1"
        self.assertEqual(pc.validate_round_spec_contract(spec), [])
        for key in ("task_id", "kanban_task_id", "kanban_board"):
            self.assertNotIn(key, spec)

    def test_run_spec_rejects_frozen_domain_changes(self):
        spec = self._template("spy_open_close_run_spec.template.json")
        spec["round_id"] = mod.FAMILY_ID + "-r1"
        spec["run_id"] = mod.FAMILY_ID + "-r1-u1"
        spec["created_at_utc"] = "2026-09-28T00:00:00Z"
        spec["script"]["sha256"] = mod.sha256_file(mod.__file__)
        spec["engine"]["self_check_sha256"] = mod.sha256_file(Path(__file__).resolve())
        mod.validate_spec(spec, script_path=mod.__file__, test_path=Path(__file__).resolve())
        changed = dict(spec)
        changed["params"] = changed["params"][:-1]
        with self.assertRaises(ValueError):
            mod.validate_spec(changed, script_path=mod.__file__, test_path=Path(__file__).resolve())
        leaked = dict(spec)
        leaked["task_id"] = "t_abc"
        with self.assertRaises(ValueError):
            mod.validate_spec(leaked, script_path=mod.__file__, test_path=Path(__file__).resolve())


class TestSpecAndCoverage(unittest.TestCase):
    def test_registered_product(self):
        self.assertEqual(mod.expected_counts(), {
            "cohorts": 4, "strategy_cases_per_cohort": 9,
            "dca_configs_per_cohort": 48, "base_combinations_per_cohort": 432,
            "case_evaluations_per_grid": 1728, "grid_count": 10,
            "case_evaluations_total": 17280})

    def test_grid_and_provenance_are_frozen(self):
        self.assertEqual(len(mod.DCA_GRID), 48)
        self.assertEqual(mod.DCA_AXES["spacing_pct"], (0.01, 0.02, 0.03, 0.04))
        self.assertEqual(len(mod.CASES), 9)
        self.assertEqual([c["model_kind"] for c in mod.CASES].count("logistic"), 1)

    def test_selector_only_reads_historical(self):
        winner = {"grid": "historical", "case_code": 0, "tau_pct": 0.0,
                  **dict(mod.DCA_GRID[0]), "net_pnl": 10.0, "sharpe": 1.0, "episodes": 10}
        with self.assertRaises(ValueError):
            mod.neighbourhood(winner, [dict(winner, grid="oos")])

    def test_signal_metric_fields_are_json_serialisable(self):
        # Regression for the r1-u2 tail crash: a fitted layer carries ndarrays
        # (train/fit counts and the signal vector) that json.dump rejects.
        layer = mod.signal_layer(panel(320), mod.CASES[0])
        fields = mod.signal_metric_fields(layer)
        self.assertNotIn("forecast", fields)
        self.assertIsInstance(fields["train_counts"], list)
        self.assertIsInstance(fields["signal"], list)
        json.dumps(fields, allow_nan=False)  # TypeError here == the production failure

    def test_signal_metric_fields_keeps_a_cohort_record_winner_grid(self):
        # Regression for the r1-u2 assertion crash: cohort records are
        # {cohort, outcome, **detail}; the grid label lives under "winner".
        detail = {"winner": {"case_code": 0, "tau_pct": 0.0, **dict(mod.DCA_GRID[0]),
                             "grid": "historical"}, "cull_reasons": []}
        record = {"cohort": "BNBUSDT/1d", "outcome": "SURVIVOR", **detail}
        with self.assertRaises(KeyError):
            record["grid"]
        self.assertEqual(record["winner"]["grid"], "historical")
        self.assertTrue(all(r["winner"]["grid"] == "historical"
                            for r in [record] if r.get("winner")))

    def test_neighbourhood_never_builds_an_unregistered_case_tau_pair(self):
        # Regression for r1-u1 KeyError: case_code and tau_pct are ONE registered
        # composite axis (round-spec parameter_contract research_axes_ordered[0] =
        # model_threshold_case, 9 registered pairs), so a face neighbour may never
        # mix case_code 0 with tau_pct 0.5 - that cell does not exist in the grid.
        self.assertNotIn((0, 0.5), [(c["case_code"], c["tau_pct"]) for c in mod.CASES])
        dca = {"spacing_pct": 0.04, "size_multiplier": 1.0,
               "breakeven_tp_pct": 0.02, "invalidation_pct": 0.05}
        rows = [dict({"symbol": "BNBUSDT", "timeframe": "1d", "case_code": c["case_code"],
                      "tau_pct": c["tau_pct"], **dict(d), "grid": "historical",
                      "net_pnl": 1.0, "sharpe": 1.0, "episodes": 10})
                for c in mod.CASES for d in mod.DCA_GRID]
        self.assertEqual(len(rows), 432)
        winner = next(r for r in rows if r["case_code"] == 1 and
                      all(r[k] == v for k, v in dca.items()))
        # Exactly one legal neighbour is negative: case_code 2 / tau 1.0, same DCA cell.
        next(r for r in rows if r["case_code"] == 2 and
             all(r[k] == v for k, v in dca.items()))["net_pnl"] = -1.0
        # case +-, spacing 0.04 -> 0.03 only, size 1.0 -> 1.1 only,
        # breakeven 0.02 -> 0.01/0.03, invalidation 0.05 -> 0.10 = 7 legal neighbours.
        self.assertEqual(mod.neighbourhood(winner, rows),
                         {"neighbours": 7, "agreeing": 6, "same_sign_fraction": 6 / 7,
                          "passed": True})


if __name__ == "__main__":
    unittest.main(verbosity=2)
