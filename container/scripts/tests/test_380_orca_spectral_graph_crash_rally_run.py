import gzip
import importlib.util
import math
import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType

RUNNER_PATH = Path(__file__).resolve().parents[1] / "380_orca_spectral_graph_crash_rally_run.py"
spec = importlib.util.spec_from_file_location("orca_runner", RUNNER_PATH)
assert spec is not None and spec.loader is not None
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

MS_DAY = 86400000
SYMBOLS = ["AAAUSDT", "BBBUSDT"]


def _has(module_name):
    try:
        __import__(module_name)
        return True
    except ImportError:
        return False


requires_numpy = unittest.skipUnless(_has("numpy"), "numpy unavailable on this interpreter")
requires_sklearn = unittest.skipUnless(_has("sklearn"), "scikit-learn unavailable on this interpreter")


def catalog():
    return {
        "venue": "BINANCE",
        "market_type": "usdm_perp",
        "symbols": SYMBOLS,
        "intervals": ["1d", "5m"],
        "datasets": {"klines": "Binance USD-M perpetual futures klines"},
    }


def make_bars(changes=None):
    """One exact 288-bar UTC day starting 2022-01-02T00:00:00Z."""
    start = 1641052800000
    changes = changes or {}
    rows, price = [], 100.0
    for i in range(288):
        open_, high, low, close = changes.get(i, (price, price + 0.1, price - 0.1, price))
        rows.append({"open_time_ms": start + i * 300000,
                     "close_time_ms": start + (i + 1) * 300000 - 1,
                     "open": open_, "high": high, "low": low, "close": close})
        price = close
    return rows


def make_dca():
    return {"spacing_pct": 0.02, "size_multiplier": 1.0, "breakeven_tp_pct": 0.01,
            "invalidation_pct": 0.05}


def make_cost():
    return {"fee_bps": 5.0, "funding_mult": 1.0, "slippage_ticks": 1.0, "tick_size": 0.01}


def instruments_meta():
    return {
        "AAAUSDT": {"raw_symbol": "AAAUSDT", "price_increment": "0.10", "taker_fee": "0.0005",
                    "maker_fee": "0.0002", "quote_currency": "USDT"},
        "BBBUSDT": {"raw_symbol": "BBBUSDT", "price_increment": "0.10", "taker_fee": "0.0005",
                    "maker_fee": "0.0002", "quote_currency": "USDT"},
    }


def synthetic_daily_rows(symbol, start=date(2022, 1, 1), stop=date(2026, 9, 11)):
    rows, day, i = {}, start, 0
    while day <= stop:
        ms = int(datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc).timestamp() * 1000)
        base = 100.0
        ret = 1.02 if (i % 25 == 0) else 1.00
        open_ = base
        close = base * ret
        rows[ms] = {
            "open_time_ms": ms, "close_time_ms": ms + MS_DAY - 1,
            "open": open_, "high": max(open_, close) * 1.005,
            "low": min(open_, close) * 0.995, "close": close, "volume": 100.0,
        }
        day += timedelta(days=1)
        i += 1
    return rows


class LocalUniverseTests(unittest.TestCase):
    def test_load_instruments_reads_canonical_envelope(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "instruments.json"
            path.write_text(json.dumps({
                "schema_version": 1,
                "instruments": [{"fields": fields} for fields in instruments_meta().values()],
            }))
            got = runner.load_instruments(path)
        self.assertEqual(set(got), set(SYMBOLS))

    def test_local_universe_is_legal_without_source_exact_match(self):
        got = runner.inspect_local_universe(catalog(), "no venue note here",
                                            symbols=SYMBOLS, instruments=instruments_meta())
        self.assertTrue(got["legal"])
        self.assertEqual(set(got["symbols"]), set(SYMBOLS))
        self.assertEqual(got["claim_scope"], runner.CLAIM_SCOPE)
        self.assertTrue(got["universe_is_not_substituted"])
        self.assertFalse(got["source_exact_match_is_execution_prerequisite"])
        self.assertFalse(got["source_universe_breadth_is_execution_prerequisite"])

    def test_missing_instrument_fee_fails_closed(self):
        bad = {"AAAUSDT": {"price_increment": "0.10"}}
        got = runner.inspect_local_universe(catalog(), "", symbols=["AAAUSDT"], instruments=bad)
        self.assertFalse(got["legal"])

    def test_missing_5m_execution_capability_fails_closed(self):
        bad = {"venue": "BINANCE", "intervals": ["1d"]}
        got = runner.inspect_local_universe(bad, "", symbols=SYMBOLS, instruments=instruments_meta())
        self.assertFalse(got["legal"])


class PanelTests(unittest.TestCase):
    def _panel(self, rows=None):
        rows = rows or {s: synthetic_daily_rows(s) for s in SYMBOLS}
        return runner.build_panel(rows, "2022-01-01", "2026-09-11")

    def test_joint_panel_is_rectangular_and_aligned(self):
        panel = self._panel()
        self.assertEqual(panel["symbols"], sorted(SYMBOLS))
        n = len(panel["dates"])
        self.assertGreater(n, 1000)
        for sym in SYMBOLS:
            self.assertEqual(len(panel["closes"][sym]), n)
            self.assertEqual(len(panel["returns"][sym]), n)
            self.assertEqual(len(panel["bars"][sym]), n)
        self.assertEqual(panel["returns"][SYMBOLS[0]][0], 0.0)
        self.assertEqual(len(panel["index"]), n)

    def test_record_forward_fill_rule_and_dropped_gaps(self):
        rows = {s: synthetic_daily_rows(s) for s in SYMBOLS}
        # a 3-day hole on one symbol: the record's forward-fill keeps the joint date alive
        hole = [t for t, bar in rows["BBBUSDT"].items()
                if date(2023, 5, 2) <= datetime.fromtimestamp(t / 1000, tz=timezone.utc).date()
                <= date(2023, 5, 4)]
        for t in hole:
            del rows["BBBUSDT"][t]
        panel = runner.build_panel(rows, "2022-01-01", "2026-09-11")
        self.assertGreaterEqual(panel["ffilled_dates"], 3)
        self.assertEqual(panel["dropped_gap_dates"], 0)
        # the signal forward-fills, but the execution view marks the day unusable
        self.assertEqual(panel["missing_execution_dates"]["BBBUSDT"], 3)
        hole_index = [i for i, t in enumerate(panel["date_ms"]) if t in hole]
        self.assertEqual(len(hole_index), 3)
        for i in hole_index:
            self.assertTrue(panel["bars"]["BBBUSDT"][i].get("missing"))
        # a symbol with no observations leaves no joint-observable panel at all
        with self.assertRaisesRegex(ValueError, "too short"):
            runner.build_panel({SYMBOLS[0]: synthetic_daily_rows(SYMBOLS[0]),
                                SYMBOLS[1]: {}}, "2022-01-01", "2022-02-01")

    def test_forward_labels_match_record_thresholds(self):
        panel = self._panel()
        labels = runner.forward_labels(panel, 500)
        self.assertIsNotNone(labels)
        self.assertIn("rally", labels)
        self.assertIn("crash", labels)
        # a manufactured +10% endpoint move must be a rally and never a crash
        closes = {s: [100.0] * 30 for s in SYMBOLS}
        closes = {s: [100.0 + 0.5 * i for i in range(30)] for s in SYMBOLS}
        fake = {"symbols": SYMBOLS, "closes": closes,
                "index": [sum(closes[s][i] for s in SYMBOLS) / len(SYMBOLS) for i in range(30)]}
        lab = runner.forward_labels(fake, 5)
        self.assertAlmostEqual(lab["endpoint_return"],
                               (100.0 + 0.5 * 15) / (100.0 + 0.5 * 5) - 1.0, places=9)
        self.assertTrue(lab["rally"])
        self.assertFalse(lab["crash"])

    def test_crash_label_requires_seven_percent_drawdown(self):
        path = [100.0]
        for _ in range(10):
            path.append(path[-1] * 0.985)   # -14% cumulative, >7% drawdown
        closes = {s: list(path) for s in SYMBOLS}
        fake = {"symbols": SYMBOLS, "closes": closes,
                "index": [sum(closes[s][i] for s in SYMBOLS) / len(SYMBOLS)
                          for i in range(len(path))]}
        lab = runner.forward_labels(fake, 0)
        self.assertTrue(lab["crash"])
        self.assertFalse(lab["rally"])


class DomainAndGridTests(unittest.TestCase):
    def test_dca_grid_is_exact_registered_48(self):
        grid = runner.DCA_GRID
        self.assertEqual(len(grid), 48)
        self.assertEqual({c["spacing_pct"] for c in grid}, {0.01, 0.02, 0.03, 0.04})
        self.assertEqual({c["size_multiplier"] for c in grid}, {1.0, 1.1})
        self.assertEqual({c["breakeven_tp_pct"] for c in grid}, {0.01, 0.02, 0.03})
        self.assertEqual({c["invalidation_pct"] for c in grid}, {0.05, 0.10})

    def test_expected_counts_scale_with_the_local_universe(self):
        counts = runner.expected_counts(["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"])
        self.assertEqual(counts["cohorts"], 4)
        self.assertEqual(counts["strategy_cases_per_cohort"], 1)
        self.assertEqual(counts["dca_configs_per_cohort"], 48)
        self.assertEqual(counts["case_evaluations_per_grid"], 192)
        self.assertEqual(counts["case_evaluations_total"], 1920)

    def test_user_fixed_invariants_are_frozen(self):
        self.assertEqual(runner.START_EQUITY, 30000.0)
        self.assertEqual(runner.BASE_QUOTE, 1000.0)
        self.assertEqual(runner.LEVERAGE, 10.0)
        self.assertEqual(runner.ROUTINE_ACTIVE_TRANCHES_MAX, 11)
        self.assertEqual(runner.RESERVE_TRANCHE, 12)
        self.assertEqual(runner.MAX_ADD_LEVELS, 10)
        self.assertEqual(runner.EXECUTION_SEMANTICS["position_direction"],
                         "long_only_with_risk_off_rotation")

    def test_registered_signal_rule_is_record_faithful(self):
        rule = runner.REGISTERED_SIGNAL_RULE
        self.assertEqual(rule["version"], 1)
        self.assertFalse(rule["is_search_axis"])
        self.assertIn("long-only", rule["direction"])
        self.assertEqual(rule["holding_period"],
                         "minimum 8 trading days for non-exit positions; exit signals override")
        self.assertIn("60-day trailing", " ".join(rule["estimators"]))
        self.assertEqual(runner.HORIZON_DAYS, 10)
        self.assertEqual(runner.RALLY_THRESHOLD, 0.03)
        self.assertEqual(runner.CRASH_THRESHOLD, 0.07)
        self.assertEqual(runner.MIN_HOLD_DAYS, 8)
        self.assertEqual(runner.RF_PARAMS["n_estimators"], 200)
        self.assertEqual(runner.RF_PARAMS["max_depth"], 6)
        self.assertEqual(runner.RF_PARAMS["min_samples_leaf"], 30)
        self.assertEqual(runner.RF_PARAMS["min_samples_split"], 60)
        self.assertEqual(runner.GRAPH_THRESHOLDS, (0.3, 0.5, 0.7))
        self.assertEqual(runner.CORR_WINDOWS, (60, 120))
        self.assertEqual(runner.EWM_HALF_LIFE, 30)

    def test_phase_windows_never_overlap_and_cover_full(self):
        p = runner.PHASES
        self.assertEqual(p["historical"], ("2022-01-01", "2025-09-30"))
        self.assertEqual(p["oos"], ("2025-10-01", "2026-09-11"))
        self.assertEqual(p["full"], ("2022-01-01", "2026-09-11"))

    def test_grid_phase_mapping_is_total(self):
        for g in runner.GRIDS:
            self.assertIn(runner.grid_phase(g), runner.PHASES)

    def test_feature_names_are_unique_and_bounded_to_60_day_lookbacks(self):
        names = runner.FEATURE_NAMES
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(all(lag <= 60 for lag in runner.TRAD_LOOKBACKS))
        self.assertIn("corr60_dominant_eigenvalue_share", names)
        self.assertIn("corr120_spectral_gap", names)
        self.assertIn("ewm30_effective_rank", names)
        for thr in runner.GRAPH_THRESHOLDS:
            self.assertIn("corr60_thr%s_edge_density" % str(thr).replace(".", ""), names)


class ExposureMapTests(unittest.TestCase):
    def test_registered_states_are_exact(self):
        self.assertEqual(runner.exposure_map(0.85, 0.10), (1.5, "max_entry"))
        self.assertEqual(runner.exposure_map(0.78, 0.39), (1.5, "max_entry"))
        # exit overrides the entry band at exactly 0.90 (record: exit overrides holding period)
        self.assertEqual(runner.exposure_map(0.90, 0.10), (0.0, "euphoria_exit"))
        self.assertEqual(runner.exposure_map(0.95, 0.10), (0.0, "euphoria_exit"))
        self.assertEqual(runner.exposure_map(0.50, 0.60), (0.0, "danger_exit"))
        self.assertEqual(runner.exposure_map(0.85, 0.45), (0.3, "risk_off_reduced"))
        self.assertEqual(runner.exposure_map(0.50, 0.10), (0.9, "intermediate_0_9"))
        self.assertEqual(runner.exposure_map(0.65, 0.10), (1.2, "intermediate_1_2"))
        self.assertEqual(runner.exposure_map(0.10, 0.10), (0.3, "intermediate_0_3"))
        self.assertEqual(runner.exposure_map(None, None), (0.0, "no_signal"))

    def test_every_state_stays_inside_registered_bounds(self):
        for rally in [i / 100.0 for i in range(0, 101)]:
            for crash in [i / 100.0 for i in range(0, 101)]:
                exposure, _state = runner.exposure_map(rally, crash)
                self.assertGreaterEqual(exposure, 0.0)
                self.assertLessEqual(exposure, 1.5)
                if rally >= 0.90 or crash >= 0.60:
                    self.assertEqual(exposure, 0.0)
                if 0.78 <= rally < 0.90 and crash < 0.40:
                    self.assertEqual(exposure, 1.5)
                if exposure == 1.5:
                    self.assertLess(crash, 0.40)

    def test_percentile_rank_is_causal_and_bounded(self):
        history = [0.1, 0.5, 0.9]
        self.assertAlmostEqual(runner.percentile_rank(history, 0.5), 0.75)
        self.assertAlmostEqual(runner.percentile_rank([], 0.42), 1.0)
        for score in (0.0, 0.3, 1.0):
            self.assertGreaterEqual(runner.percentile_rank(history, score), 1.0 / 4.0)
            self.assertLessEqual(runner.percentile_rank(history, score), 1.0)

    def test_roc_auc_handles_ties_and_single_class(self):
        perfect = runner.roc_auc([(0.9, True), (0.8, True), (0.2, False), (0.1, False)])
        self.assertAlmostEqual(perfect, 1.0)
        tied = runner.roc_auc([(0.5, True), (0.5, False)])
        self.assertAlmostEqual(tied, 0.5)
        self.assertIsNone(runner.roc_auc([(0.5, True), (0.6, True)]))


class CostModelTests(unittest.TestCase):
    def test_fee_comes_from_canonical_instrument_metadata(self):
        m = {"taker_fee": "0.0004", "maker_fee": "0.0002", "price_increment": "0.01"}
        self.assertAlmostEqual(runner.cost_for("historical", m)["fee_bps"], 4.0)
        self.assertAlmostEqual(runner.cost_for("fee_2x", m)["fee_bps"], 8.0)
        self.assertAlmostEqual(runner.cost_for("cost_attrition_40bps", m)["fee_bps"], 44.0)

    def test_slippage_is_price_ticks_not_bps(self):
        m = {"taker_fee": "0.0004", "price_increment": "0.50"}
        self.assertAlmostEqual(runner.cost_for("historical", m)["tick_size"], 0.50)
        self.assertAlmostEqual(runner.cost_for("slippage_2ticks", m)["slippage_ticks"], 2.0)

    def test_funding_multipliers_and_entry_delay(self):
        m = {"taker_fee": "0.0004", "price_increment": "0.01"}
        self.assertEqual(runner.cost_for("funding_2x", m)["funding_mult"], 2.0)
        self.assertEqual(runner.cost_for("no_funding", m)["funding_mult"], 0.0)
        self.assertEqual(runner.cost_for("no_funding_full", m)["funding_mult"], 0.0)
        self.assertEqual(runner.cost_for("entry_delay_1_bar", m)["entry_delay_bars"], 1)
        self.assertEqual(runner.cost_for("historical", m)["entry_delay_bars"], 0)

    def test_fill_price_is_one_adverse_tick_per_side(self):
        cost = {"slippage_ticks": 1.0, "tick_size": 0.25}
        self.assertEqual(runner._fill_price(100.0, "buy", cost), 100.25)
        self.assertEqual(runner._fill_price(100.0, "sell", cost), 99.75)


class FundingTests(unittest.TestCase):
    def test_off_boundary_funding_fails_closed(self):
        bars = make_bars()
        event = {"t": bars[1]["open_time_ms"] + 1, "mark": 100.0, "cost_per_unit": 0.1}
        with self.assertRaisesRegex(ValueError, "exact 5m boundary"):
            runner.process_day(runner.open_position(bars[0], 1.5, make_cost(), make_dca(), 0),
                               bars, [event], make_cost(), make_dca(), False, None, False)

    def test_official_funding_receipt_jitter_snaps_to_funding_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "AAAUSDT"
            target.mkdir()
            path = target / "AAAUSDT-funding.jsonl.gz"
            rows = [
                {"funding_time_ms": 1640995200001, "funding_rate": 0.0001,
                 "mark_price": 100.0, "truth_status": "official"},
                {"funding_time_ms": 1640997600026, "funding_rate": 0.0002,
                 "mark_price": 101.0, "truth_status": "official"},
                {"funding_time_ms": 1641001200000, "funding_rate": 0.0003,
                 "mark_price": 102.0, "truth_status": "official"},
            ]
            path.write_bytes(gzip.compress(
                ("\n".join(json.dumps(r) for r in rows) + "\n").encode()))
            events, report = runner.load_funding("AAAUSDT", 0, 2 ** 63 - 1, root=tmp)
            self.assertEqual([e["t"] for e in events],
                             [1640995200000, 1640997600000, 1641001200000])
            self.assertEqual(report["boundary_jitter_snapped"], 2)
            self.assertEqual(report["official"], 3)

    def test_modeled_rows_are_never_charged(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "AAAUSDT"
            target.mkdir()
            path = target / "AAAUSDT-funding.jsonl.gz"
            rows = [
                {"funding_time_ms": 1640995200000, "funding_rate": 0.0001,
                 "mark_price": 100.0, "truth_status": "modeled"},
                {"funding_time_ms": 1640997600000, "funding_rate": 0.0002,
                 "mark_price": 101.0, "truth_status": "official"},
            ]
            path.write_bytes(gzip.compress(
                ("\n".join(json.dumps(r) for r in rows) + "\n").encode()))
            events, report = runner.load_funding("AAAUSDT", 0, 2 ** 63 - 1, root=tmp)
            self.assertEqual(len(events), 1)
            self.assertEqual(report["modeled_ignored"], 1)
            self.assertTrue(report["missing_intervals_are_zero_not_modeled"])


class ExecutionTests(unittest.TestCase):
    def _bars(self, changes=None):
        start = 1641081600000  # 2022-01-02T00:00:00Z
        changes = changes or {}
        rows, price = [], 100.0
        for i in range(288):
            open_, high, low, close = changes.get(i, (price, price + 0.1, price - 0.1, price))
            rows.append({"open_time_ms": start + i * 300000,
                         "close_time_ms": start + (i + 1) * 300000 - 1,
                         "open": open_, "high": high, "low": low, "close": close})
            price = close
        return rows

    def _dca(self):
        return {"spacing_pct": 0.02, "size_multiplier": 1.0, "breakeven_tp_pct": 0.01,
                "invalidation_pct": 0.05}

    def _cost(self):
        return {"fee_bps": 5.0, "funding_mult": 1.0, "slippage_ticks": 1.0, "tick_size": 0.01}

    def test_entry_quote_scales_with_registered_exposure(self):
        bars = self._bars()
        full = runner.open_position(bars[0], 1.5, self._cost(), self._dca(), 0)
        half = runner.open_position(bars[0], 0.6, self._cost(), self._dca(), 0)
        self.assertAlmostEqual(full["qty"], runner.BASE_QUOTE / full["initial"])
        self.assertAlmostEqual(half["qty"] / full["qty"], 0.6 / 1.5, places=9)
        self.assertEqual(full["ledger"][0]["type"], "entry")

    def test_record_exit_flattens_every_layer_at_the_session_open(self):
        bars = self._bars()
        pos = runner.open_position(bars[0], 1.5, self._cost(), self._dca(), 0)
        runner.process_day(pos, bars, {}, self._cost(), self._dca(), False, "euphoria_exit", False)
        self.assertEqual(pos["qty"], 0.0)
        self.assertEqual(pos["exit_reason"], "euphoria_exit")
        self.assertEqual(pos["ledger"][-1]["type"], "exit")
        self.assertEqual(pos["ledger"][-1]["qty_after"], 0.0)

    def test_min_holding_period_blocks_tp_and_stop_until_day_eight(self):
        # a bar that would otherwise be a runaway take-profit inside the first 7 days
        bars = self._bars({1: (100.0, 120.0, 99.9, 110.0)})
        pos = runner.open_position(bars[0], 1.5, self._cost(), self._dca(), 0)
        runner.process_day(pos, bars, {}, self._cost(), self._dca(), False, None, False)
        self.assertEqual(pos["qty"] > 0.0, True)
        self.assertIsNone(pos["exit_reason"])
        # the same bar on day 8 takes the registered breakeven TP
        pos2 = runner.open_position(bars[0], 1.5, self._cost(), self._dca(), 0)
        runner.process_day(pos2, bars, {}, self._cost(), self._dca(), True, None, False)
        self.assertEqual(pos2["exit_reason"], "tp")
        self.assertEqual(pos2["qty"], 0.0)

    def test_exit_signal_overrides_the_holding_period(self):
        bars = self._bars()
        pos = runner.open_position(bars[0], 1.5, self._cost(), self._dca(), 0)
        runner.process_day(pos, bars, {}, self._cost(), self._dca(), False, "danger_exit", False)
        self.assertEqual(pos["exit_reason"], "danger_exit")

    def test_adverse_path_is_processed_before_favorable_tp(self):
        bars = self._bars({1: (100.0, 106.0, 90.0, 102.0)})
        pos = runner.open_position(bars[0], 1.5, self._cost(), self._dca(), 0)
        runner.process_day(pos, bars, {}, self._cost(), self._dca(), True, None, False)
        self.assertEqual(pos["exit_reason"], "stop")

    def test_dca_scale_ins_uses_ladder_anchored_on_initial_entry_price(self):
        bars = self._bars({1: (100.0, 99.9, 97.0, 99.0)})
        dca = self._dca()
        pos = runner.open_position(bars[0], 1.5, self._cost(), dca, 0)
        runner.process_day(pos, bars, {}, self._cost(), dca, True, None, False)
        adds = [e for e in pos["ledger"] if e["type"] == "add"]
        self.assertTrue(adds)
        first_add = adds[0]
        expected = pos["initial"] * (1.0 - dca["spacing_pct"] * 1)
        expected_fill = runner._fill_price(expected, "buy", self._cost())
        self.assertAlmostEqual(first_add["price_mark"], expected_fill, places=9)

    def test_force_close_at_phase_end_is_reduce_only(self):
        bars = self._bars()
        pos = runner.open_position(bars[0], 1.5, self._cost(), self._dca(), 0)
        runner.process_day(pos, bars, {}, self._cost(), self._dca(), False, None, True)
        self.assertEqual(pos["exit_reason"], "phase_end")
        self.assertEqual(pos["qty"], 0.0)
        self.assertEqual(pos["ledger"][-1]["qty_after"], 0.0)

    def test_ledger_reconciles_gross_fees_funding(self):
        bars = self._bars({1: (100.0, 99.9, 97.9, 99.0)})
        event = {"t": bars[2]["open_time_ms"], "mark": 100.0, "cost_per_unit": 0.1}
        dca = {**self._dca(), "breakeven_tp_pct": 0.03}
        pos = runner.open_position(bars[0], 1.5, self._cost(), dca, 0)
        runner.process_day(pos, bars, [event], self._cost(), dca, True, None, False)
        ep = runner.episode_metrics(pos)
        ledger = ep["execution_accounting"]
        self.assertAlmostEqual(sum(e["gross_delta"] for e in ledger), ep["gross_pnl"])
        self.assertAlmostEqual(sum(e["fee_delta"] for e in ledger), ep["fees"])
        self.assertAlmostEqual(sum(e["funding_delta"] for e in ledger), ep["funding"])
        self.assertAlmostEqual(ep["gross_pnl"] - ep["fees"] - ep["funding"], ep["net_pnl"])
        self.assertEqual(ledger[0]["type"], "entry")
        for before, after in zip(ledger, ledger[1:]):
            self.assertAlmostEqual(before["qty_after"], after["qty_before"])

    def test_no_funding_grid_has_zero_charge(self):
        bars = self._bars()
        event = {"t": bars[1]["open_time_ms"], "mark": 100.0, "cost_per_unit": 0.1}
        cost = {**self._cost(), "funding_mult": 0.0}
        pos = runner.open_position(bars[0], 1.5, cost, self._dca(), 0)
        runner.process_day(pos, bars, [event], cost, self._dca(), False, None, True)
        ep = runner.episode_metrics(pos)
        self.assertEqual(ep["funding"], 0.0)
        self.assertEqual(ep["funding_events_charged"], 0)

    def test_exact_daily_five_minute_coverage_required(self):
        bars = self._bars()
        lookup = {b["open_time_ms"]: b for b in bars}
        daily = {"open_time_ms": bars[0]["open_time_ms"],
                 "close_time_ms": bars[0]["open_time_ms"] + MS_DAY - 1}
        self.assertEqual(len(runner.execution_day_slice(daily, lookup)), 288)
        del lookup[bars[15]["open_time_ms"]]
        with self.assertRaisesRegex(ValueError, "incomplete 5m"):
            runner.execution_day_slice(daily, lookup)


class SelectorTests(unittest.TestCase):
    def _dummy_rows(self, n_episodes, net_pnl, sharpe):
        cell = runner.DCA_GRID[0]
        return [{
            "grid": "historical", "symbol": "BTCUSDT", "timeframe": "1d",
            "signal_rule_version": 1, **cell, "episodes": n_episodes,
            "net_pnl": net_pnl, "sharpe": sharpe,
        }]

    def test_insufficient_episodes_culls_before_selection(self):
        winner, cull, _ = runner.select_cohort(self._dummy_rows(runner.MIN_EPISODES_IS - 1, 100.0, 1.5))
        self.assertIsNone(winner)
        self.assertEqual(cull, "insufficient_trades")

    def test_no_qualifying_candidate_culls(self):
        winner, cull, _ = runner.select_cohort(self._dummy_rows(runner.MIN_EPISODES_IS + 5, -10.0, -0.5))
        self.assertIsNone(winner)
        self.assertEqual(cull, "no_qualifying_candidate")

    def test_full_survivor_requires_every_leg(self):
        winner, cull, candidates = runner.select_cohort(
            self._dummy_rows(runner.MIN_EPISODES_IS + 5, 100.0, 1.5))
        self.assertIsNotNone(winner)
        self.assertIsNone(cull)
        self.assertEqual(len(candidates), 1)

    def test_neighbourhood_uses_historical_sign_agreement(self):
        historical = [
            {"symbol": "BTCUSDT", "timeframe": "1d", "signal_rule_version": 1, **cell,
             "net_pnl": 10.0 if i % 2 == 0 else -5.0}
            for i, cell in enumerate(runner.DCA_GRID)
        ]
        res = runner.neighbourhood(historical[0], historical)
        self.assertGreater(res["neighbours"], 0)
        self.assertIn("same_sign_fraction", res)


class IdentityTests(unittest.TestCase):
    def _specs(self, root, runner_path, symbols=SYMBOLS):
        family = runner.FAMILY_ID
        round_id = family + "-r1"
        run_id = round_id + "-u1"
        fingerprint_input = runner.FINGERPRINT_INPUT
        fingerprint = runner.sha256(fingerprint_input.encode())
        attempt = root / family / "rounds" / round_id / "attempts" / run_id
        attempt.mkdir(parents=True)
        round_doc = {
            "schema_version": 1, "document_kind": "round-spec", "family_id": family,
            "round_id": round_id, "semantic_fingerprint": fingerprint,
            "fingerprint_input": fingerprint_input,
            "eligible_universe": {
                "execution_market": runner.EXECUTION_MARKET,
                "source_market": runner.SOURCE_MARKET,
                "instruments": symbols, "timeframes": [runner.TIMEFRAME],
                "cohort_definition": "instrument x timeframe",
                "claim_scope": runner.CLAIM_SCOPE,
            },
            "signal_rule": runner.REGISTERED_SIGNAL_RULE,
            "exposure_map": runner.EXPOSURE_MAP,
            "execution_semantics": runner.EXECUTION_SEMANTICS,
            "gates": runner.GATES,
            "parameter_contract": self._parameter_contract(family, symbols),
        }
        round_raw = (json.dumps(round_doc, indent=2) + "\n").encode()
        (attempt.parents[1] / "round-spec.json").write_bytes(round_raw)
        counts = runner.expected_counts(symbols)
        run_doc = {
            "schema_version": 1, "document_kind": "run-spec", "family_id": family,
            "round_id": round_id, "run_id": run_id, "fingerprint_input": fingerprint_input,
            "semantic_fingerprint": fingerprint,
            "round_spec_sha256": runner.sha256(round_raw),
            "selector_version": "cohort-selector-v1",
            "disposition_version": "cohort-disposition-v1",
            "disposition_mapping_version": "v1.4.0",
            "script": {"path": "/scripts/" + runner.RUNNER_NAME,
                       "sha256": runner.sha256(Path(runner_path).read_bytes())},
            "data": {"source": "/data/raw", "market": runner.EXECUTION_MARKET,
                     "symbols": symbols, "timeframes": [runner.TIMEFRAME],
                     "execution_timeframe": runner.EXEC_TIMEFRAME},
            "signal_rule": runner.REGISTERED_SIGNAL_RULE,
            "exposure_map": runner.EXPOSURE_MAP,
            "params": [{"signal_rule_version": 1}],
            "dca_domain": {**{k: list(v) for k, v in runner.DCA_AXES.items()},
                           "grid": runner.DCA_GRID, "base_quote": runner.BASE_QUOTE},
            "execution_semantics": runner.EXECUTION_SEMANTICS,
            "gates": runner.GATES,
            "expected": {"grids": list(runner.GRIDS), "cohorts": len(symbols),
                         "strategy_cases_per_cohort": 1,
                         "dca_configs_per_cohort": len(runner.DCA_GRID),
                         "case_evaluations_per_grid": counts["case_evaluations_per_grid"],
                         "expected_case_evaluations": counts["case_evaluations_total"]},
        }
        spec_path = attempt / "run-spec.json"
        spec_path.write_text(json.dumps(run_doc, indent=2) + "\n")
        return attempt, spec_path, round_doc, run_doc

    def _parameter_contract(self, family, symbols):
        return {
            "parameter_contract_version": 1,
            "family_id": family,
            "contract_ref": "v1.8 generic family parameter contract; one frozen ORCA "
                            "correlation-network signal rule plus complete four-axis DCA domain",
            "research_axes_ordered": [
                {"name": "signal_rule_version", "kind": "atomic",
                 "members": ["signal_rule_version"], "registered_values": [1],
                 "row_fields": ["signal_rule_version"]},
                {"name": "spacing_pct", "kind": "atomic", "members": ["spacing_pct"],
                 "registered_values": [0.01, 0.02, 0.03, 0.04], "row_fields": ["spacing_pct"]},
                {"name": "size_multiplier", "kind": "atomic", "members": ["size_multiplier"],
                 "registered_values": [1.0, 1.1], "row_fields": ["size_multiplier"]},
                {"name": "breakeven_tp_pct", "kind": "atomic", "members": ["breakeven_tp_pct"],
                 "registered_values": [0.01, 0.02, 0.03], "row_fields": ["breakeven_tp_pct"]},
                {"name": "invalidation_pct", "kind": "atomic", "members": ["invalidation_pct"],
                 "registered_values": [0.05, 0.1], "row_fields": ["invalidation_pct"]},
            ],
            "row_fields": ["signal_rule_version", "spacing_pct", "size_multiplier",
                           "breakeven_tp_pct", "invalidation_pct"],
            "composite_map": {},
            "strategy_param_fields": ["signal_rule_version"],
            "dca_param_fields": ["spacing_pct", "size_multiplier", "breakeven_tp_pct",
                                 "invalidation_pct"],
            "canonical_recipe": {"sort_keys": True, "separators": [",", ":"],
                                 "ensure_ascii": False,
                                 "numeric_rule": "JSON number finite, bool excluded"},
            "row_match_recipe": {
                "keys": ["symbol", "timeframe", "signal_rule_version", "spacing_pct",
                         "size_multiplier", "breakeven_tp_pct", "invalidation_pct"],
                "equality": "exact, numeric == float compare, rest bytewise"},
            "non_params": ["symbol", "timeframe", "date", "metrics", "diagnostics"],
            "domain_cardinality": {"strategy": 1, "dca": 48, "per_cohort": 48},
        }

    def test_direct_identity_rejects_ownership_and_script_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            attempt, _spec, round_doc, run_doc = self._specs(Path(tmp), RUNNER_PATH)
            runner.validate_identity(run_doc, round_doc, attempt, RUNNER_PATH)
            with self.assertRaisesRegex(ValueError, "ownership"):
                runner.validate_identity(dict(run_doc, task_id="t_forbidden"), round_doc,
                                         attempt, RUNNER_PATH)
            broken = json.loads(json.dumps(run_doc))
            broken["script"]["sha256"] = "sha256:" + "0" * 64
            with self.assertRaisesRegex(ValueError, "script hash"):
                runner.validate_identity(broken, round_doc, attempt, RUNNER_PATH)

    def test_validate_spec_rejects_registered_domain_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            _attempt, _spec, round_doc, run_doc = self._specs(Path(tmp), RUNNER_PATH)
            runner.validate_spec(run_doc, round_doc)
            for key, value, message in (
                ("dca_domain", {"spacing_pct": [0.05]}, "DCA axis"),
                ("signal_rule", {"version": 2}, "signal rule"),
                ("exposure_map", {"version": 99}, "exposure map"),
                ("gates", {"min_episodes_is": 1}, "gates"),
                ("expected", {"expected_case_evaluations": 1}, "expected coverage"),
            ):
                drifted = json.loads(json.dumps(run_doc))
                drifted[key] = value
                with self.assertRaisesRegex(ValueError, message):
                    runner.validate_spec(drifted, round_doc)

    def test_round_spec_parameter_contract_is_valid(self):
        try:
            import parameter_contract as pc
        except ImportError:
            # the shared v1.8 validator ships with the repo runtime, not with /scripts
            self.skipTest("parameter_contract is not importable from this interpreter")
        with tempfile.TemporaryDirectory() as tmp:
            _attempt, _spec, round_doc, _run_doc = self._specs(Path(tmp), RUNNER_PATH)
            self.assertEqual(pc.validate_round_spec_contract(round_doc), [])


class SyntheticRunTests(unittest.TestCase):
    def _write_synthetic_raw(self, root):
        klines = root / "binance" / "usdm" / "klines"
        funding_dir = root / "binance" / "usdm" / "funding"
        for symbol in SYMBOLS:
            target = klines / symbol / "1d"
            target.mkdir(parents=True)
            rows = synthetic_daily_rows(symbol)
            grouped = {}
            for ms, row in rows.items():
                day = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
                grouped.setdefault(day.strftime("%Y-%m"), []).append(row)
            for month, chunk in grouped.items():
                part = target / ("%s-1d-%s.jsonl.gz" % (symbol, month))
                lines = "\n".join(json.dumps(r) for r in chunk) + "\n"
                part.write_bytes(gzip.compress(lines.encode("utf-8")))

            # execution coverage only for the days the synthetic signal touches:
            # 2022-01-02 (entry), 01-03 (entry under entry_delay_1_bar) and 01-04 (exit)
            exec_target = klines / symbol / "5m"
            exec_target.mkdir(parents=True)
            exec_rows = []
            for offset in (1, 2, 3):
                for j in range(288):
                    t = 1640995200000 + offset * MS_DAY + j * 300000
                    exec_rows.append({"open_time_ms": t, "close_time_ms": t + 299999,
                                      "open": 100.0, "high": 100.1, "low": 99.9,
                                      "close": 100.0})
            lines = "\n".join(json.dumps(r) for r in exec_rows) + "\n"
            (exec_target / ("%s-5m-2022-01.jsonl.gz" % symbol)).write_bytes(
                gzip.compress(lines.encode("utf-8")))

            funding_target = funding_dir / symbol
            funding_target.mkdir(parents=True)
            f_rows = []
            day = date(2022, 1, 1)
            stop = date(2026, 9, 11)
            while day <= stop:
                ms = int(datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc).timestamp() * 1000)
                for hour in (0, 8, 16):
                    f_rows.append({"funding_time_ms": ms + hour * 3600000,
                                   "funding_rate": 0.0001, "mark_price": 100.0,
                                   "truth_status": "official"})
                day += timedelta(days=1)
            lines = "\n".join(json.dumps(r) for r in f_rows) + "\n"
            (funding_target / ("%s-funding.jsonl.gz" % symbol)).write_bytes(
                gzip.compress(lines.encode("utf-8")))

        meta_dir = root / "_meta"
        meta_dir.mkdir(parents=True)
        (meta_dir / "CONFIG.json").write_text(json.dumps(catalog()))
        (meta_dir / "SCHEMA.md").write_text(
            "### Binance USD-M Perpetual Klines\n"
            "- Fields: `open_time_ms`, `open`, `high`, `low`, `close`, `volume`, "
            "`close_time_ms`, `quote_volume`\n")
        inst_dir = root / "binance" / "usdm" / "instruments"
        inst_dir.mkdir(parents=True)
        (inst_dir / "usdm-perp-instruments.json").write_text(json.dumps({
            "schema_version": 1,
            "instruments": [{"fields": f} for f in instruments_meta().values()],
        }))

    def _stub_signals(self, panel):
        """Deterministic exposure schedule: one long entry, then the registered exit states.

        Twelve consecutive formations keep the registered sufficiency floor happy while only
        the first two ever touch execution, so the synthetic raw only needs two covered days.
        """
        signals = [None] * len(panel["dates"])
        signals[0] = {"index": 0, "formation_day": panel["dates"][0], "p_rally": 0.5,
                      "p_crash": 0.1, "rally_rank": 0.85, "crash_rank": 0.10,
                      "exposure": 1.5, "state": "max_entry"}
        for i in range(1, 12):
            signals[i] = {"index": i, "formation_day": panel["dates"][i], "p_rally": 0.5,
                          "p_crash": 0.9, "rally_rank": 0.20, "crash_rank": 0.95,
                          "exposure": 0.0, "state": "danger_exit"}
        return signals, {
            "feature_count": len(runner.FEATURE_NAMES),
            "feature_names": list(runner.FEATURE_NAMES),
            "candidate_formations": 12, "trained_formations": 12,
            "min_train_samples": runner.MIN_TRAIN_SAMPLES,
            "rally_base_rate": 0.5, "crash_base_rate": 0.5,
            "signal_formations": 12,
            "state_counts": {"max_entry": 1, "danger_exit": 11},
            "exposure_mean": 1.5 / 12.0,
            "rf_params": dict(runner.RF_PARAMS),
            "auc_rally_local_diagnostic": 1.0,
            "auc_crash_local_diagnostic": 1.0,
            "auc_note": "synthetic",
        }

    def test_end_to_end_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_root = root / "data" / "raw"
            self._write_synthetic_raw(raw_root)
            runner_path = root / runner.RUNNER_NAME
            runner_path.write_bytes(RUNNER_PATH.read_bytes())
            id_tests = IdentityTests()
            attempt, spec_path, _round_doc, _run_doc = id_tests._specs(
                root / "results", runner_path, symbols=SYMBOLS)

            orig_defaults = runner.DEFAULT_INSTRUMENTS
            orig_signals = runner.build_signal_series
            orig_qlib = sys.modules.get("qlib")
            sys.modules["qlib"] = ModuleType("qlib")
            try:
                setattr(runner, "DEFAULT_INSTRUMENTS", tuple(SYMBOLS))
                setattr(runner, "build_signal_series", self._stub_signals)
                runner.run(str(spec_path), str(attempt), raw_root=str(raw_root))
            finally:
                setattr(runner, "DEFAULT_INSTRUMENTS", orig_defaults)
                setattr(runner, "build_signal_series", orig_signals)
                if orig_qlib is None:
                    sys.modules.pop("qlib", None)
                else:
                    sys.modules["qlib"] = orig_qlib

            for name in ("DONE", "FAILED", "INCOMPLETE", "verdict.json"):
                self.assertFalse((attempt / name).exists(), "%s must not be emitted" % name)

            artifacts = attempt / "artifacts"
            for name in ("progress.json", "cohort_results.json", "falsification.json",
                         "panel_evidence.json", "assertions.json", "local_data_evidence.json",
                         "stress_effects.json", "cohort_survivors.json"):
                self.assertTrue((artifacts / name).is_file(), name)
            assertions = json.loads((artifacts / "assertions.json").read_text())
            self.assertTrue(all(assertions.values()))
            self.assertTrue(assertions["strictly_causal_walk_forward"])
            self.assertTrue(assertions["long_only_no_short_leg"])

            falsification = json.loads((artifacts / "falsification.json").read_text())
            self.assertFalse(falsification["exact_source_battery_measured"])
            self.assertEqual(
                [item["id"] for item in falsification["falsification_plan"]],
                ["out_of_sample_extension_bcd_auc",
                 "alternative_universe_spectral_auc_contribution",
                 "rf_hyperparameter_perturbation",
                 "regime_breakdown_cagr",
                 "fee_stress_triple"],
            )

            panel_evidence = json.loads((artifacts / "panel_evidence.json").read_text())
            self.assertTrue(panel_evidence["non_gating"])
            self.assertEqual(panel_evidence["signal"]["signal_formations"], 12)

            for g in runner.GRIDS:
                grid = artifacts / ("grid_%s.csv" % g)
                self.assertTrue(grid.is_file(), g)
                rows = [r for r in grid.read_text().splitlines() if r.strip()]
                self.assertEqual(len(rows), 1 + len(SYMBOLS) * len(runner.DCA_GRID))

            state = json.loads((attempt / "state.json").read_text())
            result = json.loads((attempt / "result.json").read_text())
            self.assertEqual(state["stage"], "ARTIFACT_READY")
            self.assertEqual(result["status"], "ARTIFACT_READY")
            self.assertTrue(result["coverage_complete"])
            self.assertEqual(result["case_evaluations_total"],
                             len(SYMBOLS) * len(runner.DCA_GRID) * len(runner.GRIDS))
            self.assertEqual(result["cohorts_evaluated"], len(SYMBOLS))
            self.assertEqual(result["assertions_all_true"], True)


@requires_numpy
class SpectralMathTests(unittest.TestCase):
    def _matrix(self, rows):
        return runner._corr_matrix(runner._numpy(), rows)

    def test_perfectly_correlated_panel_compresses_the_spectrum(self):
        np = runner._numpy()
        lockstep = [[1.0, 1.0, 1.0], [1.01, 1.01, 1.01], [1.02, 1.02, 1.02],
                    [1.03, 1.03, 1.03], [1.04, 1.04, 1.04], [1.05, 1.05, 1.05]]
        independent = [[1.0, 0.5, 2.0], [1.1, 0.6, 1.8], [1.0, 0.7, 2.2],
                       [1.2, 0.5, 2.1], [1.1, 0.8, 1.9], [1.3, 0.6, 2.3]]
        lock = runner.spectral_features(self._matrix(lockstep))
        div = runner.spectral_features(self._matrix(independent))
        self.assertGreater(lock["dominant_eigenvalue_share"],
                           div["dominant_eigenvalue_share"])
        self.assertLess(lock["effective_rank"], div["effective_rank"])
        self.assertGreaterEqual(lock["spectral_gap"], 0.0)

    def test_graph_density_rises_with_the_correlation_threshold(self):
        rows = [[1.0, 0.4, 0.6, 0.8], [0.4, 1.0, 0.5, 0.7],
                [0.6, 0.5, 1.0, 0.9], [0.8, 0.7, 0.9, 1.0]]
        matrix = self._matrix(rows)
        low = runner.graph_features(matrix, 0.3)
        high = runner.graph_features(matrix, 0.7)
        self.assertGreater(low["edge_density"], high["edge_density"])
        self.assertLessEqual(high["edge_density"], 1.0)
        self.assertGreaterEqual(low["mean_clustering"], 0.0)

    def test_ewm_correlation_is_bounded_and_symmetric(self):
        np = runner._numpy()
        rows = [[1.0, 2.0], [1.1, 1.9], [1.2, 2.1], [1.15, 2.0], [1.25, 2.2],
                [1.3, 2.1], [1.35, 2.3]]
        matrix = runner._ewm_corr_matrix(np, rows, runner.EWM_HALF_LIFE)
        self.assertIsNotNone(matrix)
        self.assertAlmostEqual(float(matrix[0][1]), float(matrix[1][0]), places=9)
        self.assertAlmostEqual(float(matrix[0][0]), 1.0, places=9)


@requires_sklearn
class WalkForwardTests(unittest.TestCase):
    def test_walk_forward_is_strictly_causal_and_produces_exposures(self):
        np = runner._numpy()
        rng = np.random.default_rng(7)
        n, assets = 420, 4
        noise = rng.normal(0.0, 0.01, size=(n, assets))
        drift = rng.normal(0.0, 0.002, size=(n, 1))
        values = 100.0 * np.cumprod(1.0 + noise + drift * 0.0, axis=0)
        closes = {("AAAUSDT", "BBBUSDT", "CCBUSDT", "DDUSDT")[a]: values[:, a].tolist()
                  for a in range(assets)}
        dates = [date(2023, 1, 1) + timedelta(days=i) for i in range(n)]
        panel = {
            "symbols": sorted(closes), "dates": dates,
            "closes": closes,
            "returns": {s: [0.0] + [closes[s][i] / closes[s][i - 1] - 1.0
                                    for i in range(1, n)] for s in closes},
            "index": [sum(closes[s][i] for s in closes) / len(closes) for i in range(n)],
            "ffilled_dates": 0, "dropped_gap_dates": 0,
        }
        signals, diag = runner.build_signal_series(panel, min_train=120)
        self.assertGreater(diag["trained_formations"], 0)
        self.assertEqual(len(signals), n)
        first = next(i for i, row in enumerate(signals) if row is not None)
        self.assertGreaterEqual(first, runner.FEATURE_MIN_INDEX)
        for row in signals:
            if row is None:
                continue
            self.assertGreaterEqual(row["exposure"], 0.0)
            self.assertLessEqual(row["exposure"], 1.5)
            self.assertGreaterEqual(row["rally_rank"], 1.0 / (diag["trained_formations"] + 1))
            self.assertLessEqual(row["rally_rank"], 1.0)


def regime_panel(n=400):
    """Deterministic crash/rally regime panel: hits both registered label classes."""
    symbols = ["AAAUSDT", "BBBUSDT"]
    prices, level = [], 100.0
    for i in range(n):
        phase = i % 40
        if phase in (1, 2):
            level *= 0.96            # >7% intra-window drawdown -> crash label
        elif phase in (3, 4, 5, 6, 7):
            level *= 1.025           # >3% endpoint return -> rally label
        else:
            level *= 1.001
        prices.append(level)
    closes = {s: list(prices) for s in symbols}
    return {
        "symbols": symbols,
        "dates": [date(2023, 1, 1) + timedelta(days=i) for i in range(n)],
        "closes": closes,
        "returns": {s: [0.0] + [closes[s][i] / closes[s][i - 1] - 1.0
                                for i in range(1, n)] for s in symbols},
        "index": list(prices),
        "ffilled_dates": 0,
        "dropped_gap_dates": 0,
    }


class SignalConstructionTests(unittest.TestCase):
    def test_traditional_features_are_pure_python(self):
        panel = regime_panel()
        values = runner.traditional_features(panel, 200)
        self.assertEqual(len(values), 12)
        for name, value in values.items():
            self.assertTrue(math.isfinite(value), name)
        # 1-to-60-day lookbacks only
        self.assertIn("panel_return_1d", values)
        self.assertIn("panel_return_60d", values)
        self.assertNotIn("panel_return_120d", values)
        self.assertLessEqual(values["panel_drawdown_60d"], 0.0)
        self.assertGreaterEqual(values["panel_vol_20d"], 0.0)

    def test_forward_labels_are_reported_for_every_formable_index(self):
        panel = regime_panel()
        n = len(panel["index"])
        for i in (runner.FEATURE_MIN_INDEX, n - runner.HORIZON_DAYS - 1):
            self.assertIsNotNone(runner.forward_labels(panel, i))
        self.assertIsNone(runner.forward_labels(panel, n - runner.HORIZON_DAYS))


class _StubForest:
    """Minimal fit/predict_proba stand-in so the causal bookkeeping runs without sklearn."""

    def __init__(self, **kwargs):
        self._rate = 0.0

    def fit(self, x_train, y_train):
        self._rate = sum(y_train) / float(len(y_train))

    def predict_proba(self, probe):
        row = probe[0]
        score = (sum(row) * 7.31 + self._rate) % 1.0
        return [[1.0 - score, score]]


class _StubNumpy:
    @staticmethod
    def asarray(data, dtype=None):
        return data


class CausalWalkForwardTests(unittest.TestCase):
    """Exercises build_signal_series end-to-end with the feature/model seams stubbed."""

    def test_walk_forward_pool_ranks_and_exposures_are_strictly_causal(self):
        panel = regime_panel()
        orig_features = runner.feature_row
        orig_np = runner._numpy
        orig_cls = runner._classifiers
        try:
            setattr(runner, "feature_row",
                    lambda p, i: [float((i * 3 + k) % 11) / 11.0
                                  for k in range(len(runner.FEATURE_NAMES))])
            setattr(runner, "_numpy", lambda: _StubNumpy)
            setattr(runner, "_classifiers", lambda: _StubForest)
            signals, diag = runner.build_signal_series(panel, min_train=60)
        finally:
            setattr(runner, "feature_row", orig_features)
            setattr(runner, "_numpy", orig_np)
            setattr(runner, "_classifiers", orig_cls)

        self.assertEqual(len(signals), len(panel["index"]))
        self.assertGreater(diag["trained_formations"], 0)
        self.assertEqual(diag["signal_formations"],
                         sum(1 for row in signals if row is not None))
        first = next(i for i, row in enumerate(signals) if row is not None)
        self.assertGreaterEqual(first, runner.FEATURE_MIN_INDEX)
        # the training pool must never reach forward into the current formation
        self.assertGreater(diag["min_train_samples"], 0)

        ranks, exposures, states = [], set(), set()
        for row in signals:
            if row is None:
                continue
            self.assertTrue(0.0 < row["rally_rank"] <= 1.0)
            self.assertTrue(0.0 < row["crash_rank"] <= 1.0)
            self.assertGreaterEqual(row["exposure"], 0.0)
            self.assertLessEqual(row["exposure"], 1.5)
            self.assertEqual(runner.exposure_map(row["rally_rank"], row["crash_rank"]),
                             (row["exposure"], row["state"]))
            ranks.append((row["rally_rank"], row["crash_rank"]))
            exposures.add(row["exposure"])
            states.add(row["state"])
        # expanding percentile ranks must be non-decreasing in the sample count denominator
        self.assertLessEqual(min(r for r, _ in ranks), 1.0)
        self.assertTrue(states)
        self.assertTrue(exposures <= {0.0, 0.3, 0.6, 0.9, 1.2, 1.5})
        # both registered label classes must be learnable on this panel, so the
        # strictly-causal pool never degenerates to a single class
        self.assertTrue(0.0 < diag["rally_base_rate"] < 1.0)
        self.assertTrue(0.0 < diag["crash_base_rate"] < 1.0)


if __name__ == "__main__":
    unittest.main()
