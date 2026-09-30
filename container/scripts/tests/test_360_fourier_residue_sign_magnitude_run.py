import gzip
import hashlib
import importlib.util
import json
import math
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType

RUNNER_PATH = Path(__file__).resolve().parents[1] / "360_fourier_residue_sign_magnitude_run.py"
spec = importlib.util.spec_from_file_location("fourier_residue_runner", RUNNER_PATH)
assert spec is not None and spec.loader is not None
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

MS_DAY = 86400000
SYMBOLS = ["AAAUSDT", "BBBUSDT"]


def catalog():
    return {
        "venue": "BINANCE",
        "market_type": "usdm_perp",
        "symbols": SYMBOLS,
        "intervals": ["1d"],
        "datasets": {"klines": "Binance USD-M perpetual futures klines"},
    }


def instruments_meta():
    return {
        "AAAUSDT": {"raw_symbol": "AAAUSDT", "price_increment": "0.10", "taker_fee": "0.0005",
                    "maker_fee": "0.0002", "quote_currency": "USDT"},
        "BBBUSDT": {"raw_symbol": "BBBUSDT", "price_increment": "0.10", "taker_fee": "0.0005",
                    "maker_fee": "0.0002", "quote_currency": "USDT"},
    }


class LocalUniverseTests(unittest.TestCase):
    def test_load_instruments_reads_canonical_envelope(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "instruments.json"
            path.write_text(json.dumps({
                "schema_version": 1,
                "instruments": [
                    {"fields": fields} for fields in instruments_meta().values()
                ],
            }))
            got = runner.load_instruments(path)
        self.assertEqual(set(got), set(SYMBOLS))
        self.assertEqual(got["AAAUSDT"]["raw_symbol"], "AAAUSDT")

    def test_local_perpetual_universe_is_legal_without_kraken_exact_match(self):
        got = runner.inspect_local_universe(catalog(), "no trade count here",
                                            symbols=SYMBOLS, instruments=instruments_meta())
        self.assertTrue(got["legal"])
        self.assertEqual(set(got["symbols"]), set(SYMBOLS))
        self.assertEqual(got["claim_scope"], runner.CLAIM_SCOPE)
        self.assertTrue(got["universe_is_not_substituted"])

    def test_missing_instrument_fee_fails_closed(self):
        bad_meta = {"AAAUSDT": {"price_increment": "0.10"}}
        got = runner.inspect_local_universe(catalog(), "", symbols=["AAAUSDT"], instruments=bad_meta)
        self.assertFalse(got["legal"])

    def test_missing_1d_capability_fails_closed(self):
        bad_cat = {"venue": "BINANCE", "intervals": ["5m"]}
        got = runner.inspect_local_universe(bad_cat, "", symbols=SYMBOLS, instruments=instruments_meta())
        self.assertFalse(got["legal"])


class DomainAndGridTests(unittest.TestCase):
    def test_dca_grid_is_exact_registered_48(self):
        grid = runner.DCA_GRID
        self.assertEqual(len(grid), 48)
        spacings = {c["spacing_pct"] for c in grid}
        mults = {c["size_multiplier"] for c in grid}
        tps = {c["breakeven_tp_pct"] for c in grid}
        invs = {c["invalidation_pct"] for c in grid}
        self.assertEqual(spacings, {0.01, 0.02, 0.03, 0.04})
        self.assertEqual(mults, {1.0, 1.1})
        self.assertEqual(tps, {0.01, 0.02, 0.03})
        self.assertEqual(invs, {0.05, 0.10})

    def test_expected_counts_scale_with_the_local_universe(self):
        counts = runner.expected_counts(["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"])
        self.assertEqual(counts["cohorts"], 4)
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

    def test_fourier_residue_signal_rule_is_record_faithful(self):
        rule = runner.REGISTERED_SIGNAL_RULE
        self.assertEqual(rule["version"], 1)
        self.assertIn("Fourier-Residue Identity", rule["decomposition"])
        self.assertIn("lag3_directional_finding", rule)
        self.assertIn("contrarian to lag-3 return sign", rule["direction"])

    def test_phase_windows_never_overlap_and_cover_full(self):
        p = runner.PHASES
        self.assertEqual(p["historical"][0], "2022-01-01")
        self.assertEqual(p["historical"][1], "2025-09-30")
        self.assertEqual(p["oos"][0], "2025-10-01")
        self.assertEqual(p["oos"][1], "2026-09-11")
        self.assertEqual(p["full"][0], "2022-01-01")
        self.assertEqual(p["full"][1], "2026-09-11")

    def test_grid_phase_mapping_is_total(self):
        for g in runner.GRIDS:
            phase = runner.grid_phase(g)
            self.assertIn(phase, runner.PHASES)


class CostModelTests(unittest.TestCase):
    def test_fee_comes_from_canonical_instrument_metadata(self):
        m = {"taker_fee": "0.0004", "maker_fee": "0.0002", "price_increment": "0.01"}
        cost = runner.cost_for("historical", m)
        self.assertAlmostEqual(cost["fee_bps"], 4.0)
        cost_2x = runner.cost_for("fee_2x", m)
        self.assertAlmostEqual(cost_2x["fee_bps"], 8.0)
        cost_att = runner.cost_for("cost_attrition_40bps", m)
        self.assertAlmostEqual(cost_att["fee_bps"], 44.0)

    def test_slippage_is_price_ticks_not_bps(self):
        m = {"taker_fee": "0.0004", "price_increment": "0.50"}
        cost = runner.cost_for("historical", m)
        self.assertAlmostEqual(cost["slippage_ticks"], 1.0)
        self.assertAlmostEqual(cost["tick_size"], 0.50)
        cost_2t = runner.cost_for("slippage_2ticks", m)
        self.assertAlmostEqual(cost_2t["slippage_ticks"], 2.0)

    def test_funding_multipliers(self):
        m = {"taker_fee": "0.0004", "price_increment": "0.01"}
        self.assertEqual(runner.cost_for("funding_2x", m)["funding_mult"], 2.0)
        self.assertEqual(runner.cost_for("no_funding", m)["funding_mult"], 0.0)
        self.assertEqual(runner.cost_for("no_funding_full", m)["funding_mult"], 0.0)

    def test_fill_price_is_one_adverse_tick_per_side(self):
        cost = {"slippage_ticks": 1.0, "tick_size": 0.25}
        self.assertEqual(runner._fill_price(100.0, "buy", cost), 100.25)
        self.assertEqual(runner._fill_price(100.0, "sell", cost), 99.75)


class FourierResidueAnalysisTests(unittest.TestCase):
    def test_fri_sign_and_magnitude_decomposition(self):
        prices = [100.0]
        for i in range(1, 30):
            step = 1.02 if (i % 2 == 1) else 0.985
            prices.append(prices[-1] * step)
        diag = runner.fourier_residue_analysis(prices)
        self.assertIn("scalar_rho_1", diag)
        self.assertIn("scalar_rho_3", diag)
        self.assertIn("binary_sign_lag1", diag)
        self.assertIn("magnitude_fri_lag1", diag)
        self.assertIn("binary_sign_lag3", diag)
        self.assertIn("magnitude_fri_lag3", diag)
        self.assertIn("fejer_variance_ratios", diag)
        q2 = diag["fejer_variance_ratios"]["2"]
        self.assertAlmostEqual(q2["fejer_variance_ratio"], 1.0 + 2.0 * q2["fejer_sum"])
        self.assertIn("direct_variance_ratio", q2)
        self.assertGreater(diag["n_returns"], 20)

    def test_signal_generation_lag3_reversal(self):
        rows = {}
        prices = [100.0, 95.0, 96.0, 97.0, 98.0, 99.0]
        for i, p in enumerate(prices):
            ms = 1640995200000 + i * MS_DAY
            rows[ms] = {
                "open_time_ms": ms,
                "open": p,
                "high": p * 1.01,
                "low": p * 0.99,
                "close": p,
                "volume": 100.0,
            }
        bars, plans = runner.build_signals(rows)
        self.assertEqual(len(bars), len(prices))
        plan_at_3 = [p for p in plans if p["formation_idx"] == 3]
        self.assertEqual(len(plan_at_3), 1)
        self.assertEqual(plan_at_3[0]["direction"], "long")


class ExecutionTests(unittest.TestCase):
    def _bars(self, changes=None):
        start = 1640995200000
        changes = changes or {}
        rows = []
        price = 100.0
        for i in range(288):
            open_, high, low, close = changes.get(i, (price, price + 0.1, price - 0.1, price))
            rows.append({"open_time_ms": start + i * 300000,
                         "close_time_ms": start + (i + 1) * 300000 - 1,
                         "open": open_, "high": high, "low": low, "close": close})
            price = close
        return rows

    def _dca(self):
        return {"spacing_pct": 0.02, "size_multiplier": 1.0, "breakeven_tp_pct": 0.01, "invalidation_pct": 0.05}

    def _cost(self):
        return {"fee_bps": 5.0, "funding_mult": 1.0, "slippage_ticks": 1.0, "tick_size": 0.01}

    def test_same_timestamp_entry_then_funding_charges_initial_qty(self):
        bars = self._bars()
        event = {"t": bars[0]["open_time_ms"], "mark": 100.0, "cost_per_unit": 0.1}
        ep = runner.simulate_leg(bars, "long", self._dca(), self._cost(), [event])
        self.assertAlmostEqual(ep["funding"], 0.1 * ep["execution_accounting"][0]["qty_after"])
        self.assertEqual([e["type"] for e in ep["execution_accounting"][:2]], ["entry", "funding"])

    def test_dca_add_before_later_funding_charges_larger_qty(self):
        bars = self._bars({1: (100.0, 99.9, 97.9, 99.0)})
        event = {"t": bars[2]["open_time_ms"], "mark": 100.0, "cost_per_unit": 0.1}
        dca = {**self._dca(), "breakeven_tp_pct": 0.03}
        ep = runner.simulate_leg(bars, "long", dca, self._cost(), [event])
        ledger = ep["execution_accounting"]
        add = next(e for e in ledger if e["type"] == "add")
        charge = next(e for e in ledger if e["type"] == "funding")
        self.assertGreater(charge["qty_before"], add["qty_before"])
        self.assertAlmostEqual(charge["funding_delta"], 0.1 * charge["qty_before"])

    def test_exit_before_later_funding_means_no_later_charge(self):
        bars = self._bars({1: (100.0, 106.0, 99.9, 102.0)})
        event = {"t": bars[2]["open_time_ms"], "mark": 100.0, "cost_per_unit": 0.1}
        ep = runner.simulate_leg(bars, "long", self._dca(), self._cost(), [event])
        self.assertEqual(ep["exit_reason"], "tp")
        self.assertEqual(ep["funding_events_charged"], 0)
        self.assertFalse(any(e["type"] == "funding" for e in ep["execution_accounting"]))

    def test_no_funding_grid_has_zero_charge_and_event_count(self):
        bars = self._bars()
        event = {"t": bars[1]["open_time_ms"], "mark": 100.0, "cost_per_unit": 0.1}
        cost = {**self._cost(), "funding_mult": 0.0}
        ep = runner.simulate_leg(bars, "long", self._dca(), cost, [event])
        self.assertEqual(ep["funding"], 0.0)
        self.assertEqual(ep["funding_events_charged"], 0)

    def test_off_boundary_funding_fails_closed(self):
        bars = self._bars()
        event = {"t": bars[1]["open_time_ms"] + 1, "mark": 100.0, "cost_per_unit": 0.1}
        with self.assertRaisesRegex(ValueError, "exact 5m boundary"):
            runner.simulate_leg(bars, "long", self._dca(), self._cost(), [event])

    def test_off_boundary_official_funding_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "AAAUSDT"
            target.mkdir()
            path = target / "AAAUSDT-funding.jsonl.gz"
            row = {"funding_time_ms": 1640995230000, "funding_rate": 0.0001,
                   "mark_price": 100.0, "truth_status": "official"}
            path.write_bytes(gzip.compress((json.dumps(row) + "\n").encode()))
            with self.assertRaisesRegex(RuntimeError, "not on a 5m boundary"):
                runner.load_funding("AAAUSDT", 0, 2**63 - 1, root=tmp)

    def test_official_funding_receipt_jitter_snaps_to_funding_boundary(self):
        # canonical pack stamps official funding times with ms receipt jitter
        # (SCHEMA.md example row is ...0008; measured max on the family universe is 26 ms)
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
            events, report = runner.load_funding("AAAUSDT", 0, 2**63 - 1, root=tmp)
            self.assertEqual([e["t"] for e in events],
                             [1640995200000, 1640997600000, 1641001200000])
            self.assertTrue(all(e["t"] % 300000 == 0 for e in events))
            self.assertEqual(report["boundary_jitter_snapped"], 2)
            self.assertEqual(report["max_boundary_jitter_ms"], 26)
            self.assertEqual(report["official"], 3)

    def test_official_funding_jitter_beyond_tolerance_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "AAAUSDT"
            target.mkdir()
            path = target / "AAAUSDT-funding.jsonl.gz"
            row = {"funding_time_ms": 1640995201500, "funding_rate": 0.0001,
                   "mark_price": 100.0, "truth_status": "official"}
            path.write_bytes(gzip.compress((json.dumps(row) + "\n").encode()))
            with self.assertRaisesRegex(RuntimeError, "not on a 5m boundary"):
                runner.load_funding("AAAUSDT", 0, 2**63 - 1, root=tmp)

    def test_exact_daily_five_minute_coverage_required(self):
        bars = self._bars()
        lookup = {b["open_time_ms"]: b for b in bars}
        daily = {"open_time_ms": bars[0]["open_time_ms"],
                 "close_time_ms": bars[0]["open_time_ms"] + MS_DAY - 1}
        self.assertEqual(len(runner.execution_day_slice(daily, lookup)), 288)
        del lookup[bars[15]["open_time_ms"]]
        with self.assertRaisesRegex(ValueError, "incomplete 5m"):
            runner.execution_day_slice(daily, lookup)

    def test_daily_and_5m_close_times_must_be_exact(self):
        bars = self._bars()
        lookup = {b["open_time_ms"]: b for b in bars}
        daily = {"open_time_ms": bars[0]["open_time_ms"],
                 "close_time_ms": bars[0]["open_time_ms"] + MS_DAY - 1}
        malformed_first = dict(lookup)
        malformed_first[bars[0]["open_time_ms"]] = {**bars[0], "open_time_ms": bars[0]["open_time_ms"] + 300000}
        with self.assertRaisesRegex(ValueError, "first/last 5m"):
            runner.execution_day_slice(daily, malformed_first)
        malformed_last = dict(lookup)
        last_key = bars[-1]["open_time_ms"]
        malformed_last[last_key] = {**bars[-1], "open_time_ms": last_key - 300000}
        with self.assertRaisesRegex(ValueError, "first/last 5m"):
            runner.execution_day_slice(daily, malformed_last)
        malformed_daily = {**daily, "close_time_ms": daily["close_time_ms"] - 1}
        with self.assertRaisesRegex(ValueError, "daily close_time"):
            runner.execution_day_slice(malformed_daily, lookup)
        lookup[bars[10]["open_time_ms"]]["close_time_ms"] += 1
        with self.assertRaisesRegex(ValueError, "5m close_time"):
            runner.execution_day_slice(daily, lookup)

    def test_adverse_path_is_processed_before_favorable_tp(self):
        bars = self._bars({1: (100.0, 106.0, 90.0, 102.0)})
        ep = runner.simulate_leg(bars, "long", self._dca(), self._cost(), [])
        self.assertEqual(ep["exit_reason"], "stop")

    def test_ledger_reconciles_pnl_fees_funding_and_quantity_transitions(self):
        bars = self._bars({1: (100.0, 99.9, 97.9, 99.0)})
        event = {"t": bars[2]["open_time_ms"], "mark": 100.0, "cost_per_unit": 0.1}
        dca = {**self._dca(), "breakeven_tp_pct": 0.03}
        ep = runner.simulate_leg(bars, "long", dca, self._cost(), [event])
        ledger = ep["execution_accounting"]
        self.assertAlmostEqual(sum(e["gross_delta"] for e in ledger), ep["gross_pnl"])
        self.assertAlmostEqual(sum(e["fee_delta"] for e in ledger), ep["fees"])
        self.assertAlmostEqual(sum(e["funding_delta"] for e in ledger), ep["funding"])
        self.assertAlmostEqual(ep["gross_pnl"] - ep["fees"] - ep["funding"], ep["net_pnl"])
        self.assertEqual(ledger[0]["type"], "entry")
        self.assertEqual(ledger[-1]["type"], "exit")
        for before, after in zip(ledger, ledger[1:]):
            self.assertAlmostEqual(before["qty_after"], after["qty_before"])

    def test_neighbourhood_uses_historical_sign_agreement(self):
        grid_cells = runner.DCA_GRID
        historical = [
            {"symbol": "BTCUSDT", "timeframe": "1d", "signal_rule_version": 1, **cell, "net_pnl": 10.0 if i % 2 == 0 else -5.0}
            for i, cell in enumerate(grid_cells)
        ]
        winner = historical[0]
        res = runner.neighbourhood(winner, historical)
        self.assertIn("same_sign_fraction", res)
        self.assertGreater(res["neighbours"], 0)


class SelectorTests(unittest.TestCase):
    def _dummy_rows(self, n_episodes, net_pnl, sharpe):
        cell = runner.DCA_GRID[0]
        return [{
            "grid": "historical",
            "symbol": "BTCUSDT",
            "timeframe": "1d",
            "signal_rule_version": 1,
            **cell,
            "episodes": n_episodes,
            "net_pnl": net_pnl,
            "sharpe": sharpe,
        }]

    def test_insufficient_episodes_culls_before_selection(self):
        rows = self._dummy_rows(runner.MIN_EPISODES_IS - 1, 100.0, 1.5)
        winner, cull, _ = runner.select_cohort(rows)
        self.assertIsNone(winner)
        self.assertEqual(cull, "insufficient_trades")

    def test_no_qualifying_candidate_culls(self):
        rows = self._dummy_rows(runner.MIN_EPISODES_IS + 5, -10.0, -0.5)
        winner, cull, _ = runner.select_cohort(rows)
        self.assertIsNone(winner)
        self.assertEqual(cull, "no_qualifying_candidate")

    def test_full_survivor_requires_every_leg(self):
        rows = self._dummy_rows(runner.MIN_EPISODES_IS + 5, 100.0, 1.5)
        winner, cull, candidates = runner.select_cohort(rows)
        self.assertIsNotNone(winner)
        self.assertIsNone(cull)
        self.assertEqual(len(candidates), 1)


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
            "schema_version": 1, "document_kind": "round_spec", "family_id": family,
            "round_id": round_id, "semantic_fingerprint": fingerprint,
            "eligible_universe": {
                "execution_market": runner.EXECUTION_MARKET,
                "source_market": runner.SOURCE_MARKET,
                "instruments": symbols, "timeframes": [runner.TIMEFRAME],
                "cohort_definition": "instrument x timeframe",
                "claim_scope": runner.CLAIM_SCOPE,
            },
        }
        round_raw = (json.dumps(round_doc, indent=2) + "\n").encode()
        (attempt.parents[1] / "round-spec.json").write_bytes(round_raw)
        run_doc = {
            "schema_version": 1, "document_kind": "run_spec", "family_id": family,
            "round_id": round_id, "run_id": run_id, "fingerprint_input": fingerprint_input,
            "semantic_fingerprint": fingerprint,
            "round_spec_sha256": runner.sha256(round_raw),
            "selector_version": "cohort-selector-v1",
            "disposition_version": "cohort-disposition-v1",
            "disposition_mapping_version": "v1.4.0",
            "script": {"path": "/scripts/" + runner.RUNNER_NAME,
                       "sha256": runner.sha256(Path(runner_path).read_bytes())},
            "data": {"source": "/data/raw", "market": runner.EXECUTION_MARKET,
                     "symbols": symbols, "timeframes": [runner.TIMEFRAME]},
            "signal_rule": runner.REGISTERED_SIGNAL_RULE,
            "params": [{"signal_rule_version": 1}],
            "dca_domain": {**{k: list(v) for k, v in runner.DCA_AXES.items()}, "grid": runner.DCA_GRID, "base_quote": runner.BASE_QUOTE},
            "execution_semantics": runner.EXECUTION_SEMANTICS,
            "gates": runner.GATES,
            "expected": {"grids": list(runner.GRIDS), "cohorts": len(symbols),
                         "expected_case_evaluations": runner.expected_counts(symbols)["case_evaluations_total"]},
        }
        spec_path = attempt / "run-spec.json"
        spec_path.write_text(json.dumps(run_doc, indent=2) + "\n")
        return attempt, spec_path, round_doc, run_doc

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
            drifted = json.loads(json.dumps(run_doc))
            drifted["dca_domain"]["spacing_pct"] = [0.05]
            with self.assertRaisesRegex(ValueError, "DCA axis"):
                runner.validate_spec(drifted, round_doc)
            drifted = json.loads(json.dumps(run_doc))
            drifted["signal_rule"]["version"] = 2
            with self.assertRaisesRegex(ValueError, "signal rule"):
                runner.validate_spec(drifted, round_doc)
            drifted = json.loads(json.dumps(run_doc))
            drifted["gates"]["min_episodes_is"] = 1
            with self.assertRaisesRegex(ValueError, "gates"):
                runner.validate_spec(drifted, round_doc)
            drifted = json.loads(json.dumps(run_doc))
            drifted["expected"]["expected_case_evaluations"] = 1
            with self.assertRaisesRegex(ValueError, "expected coverage"):
                runner.validate_spec(drifted, round_doc)


class SyntheticRunTests(unittest.TestCase):
    def _write_synthetic_raw(self, root):
        klines = root / "binance" / "usdm" / "klines"
        funding_dir = root / "binance" / "usdm" / "funding"
        exec_day = int(datetime(2022, 1, 2, tzinfo=timezone.utc).timestamp() * 1000)
        for symbol in SYMBOLS:
            target = klines / symbol / "1d"
            target.mkdir(parents=True)
            day = date(2022, 1, 1)
            stop = date(2026, 9, 11)
            i = 0
            grouped = {}
            while day <= stop:
                ms = int(datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc).timestamp() * 1000)
                base = 100.0
                ret = 1.02 if (i % 25 == 0) else 1.00
                open_ = base
                close = base * ret
                high = max(open_, close) * 1.005
                low = min(open_, close) * 0.995
                row = {"open_time_ms": ms, "close_time_ms": ms + MS_DAY - 1,
                       "open": open_, "high": high, "low": low, "close": close, "volume": 100.0}
                month_key = day.strftime("%Y-%m")
                grouped.setdefault(month_key, []).append(row)
                day += timedelta(days=1)
                i += 1
            for month, rows in grouped.items():
                part = target / f"{symbol}-1d-{month}.jsonl.gz"
                lines = "\n".join(json.dumps(r) for r in rows) + "\n"
                part.write_bytes(gzip.compress(lines.encode("utf-8")))

            exec_target = klines / symbol / "5m"
            exec_target.mkdir(parents=True)
            exec_rows = []
            for day_offset in range(2):
                for j in range(288):
                    t = exec_day + day_offset * MS_DAY + j * 300000
                    exec_rows.append({"open_time_ms": t, "close_time_ms": t + 299999,
                                      "open": 100.0, "high": 100.1, "low": 99.9,
                                      "close": 100.0})
            exec_lines = "\n".join(json.dumps(r) for r in exec_rows) + "\n"
            (exec_target / f"{symbol}-5m-2022-01.jsonl.gz").write_bytes(
                gzip.compress(exec_lines.encode("utf-8")))

            funding_target = funding_dir / symbol
            funding_target.mkdir(parents=True)
            f_rows = []
            day = date(2022, 1, 1)
            while day <= stop:
                ms = int(datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc).timestamp() * 1000)
                for hour in (0, 8, 16):
                    f_rows.append({
                        "funding_time_ms": ms + hour * 3600000,
                        "funding_rate": 0.0001,
                        "mark_price": 100.0,
                        "truth_status": "official",
                    })
                day += timedelta(days=1)
            f_lines = "\n".join(json.dumps(r) for r in f_rows) + "\n"
            (funding_target / f"{symbol}-funding.jsonl.gz").write_bytes(gzip.compress(f_lines.encode("utf-8")))

        meta_dir = root / "_meta"
        meta_dir.mkdir(parents=True)
        (meta_dir / "CONFIG.json").write_text(json.dumps(catalog()))
        schema_doc = """
### Binance USD-M Perpetual Klines
- Fields: `open_time_ms`, `open`, `high`, `low`, `close`, `volume`, `close_time_ms`, `quote_volume`
"""
        (meta_dir / "SCHEMA.md").write_text(schema_doc)
        inst_dir = root / "binance" / "usdm" / "instruments"
        inst_dir.mkdir(parents=True)
        (inst_dir / "usdm-perp-instruments.json").write_text(json.dumps({
            "schema_version": 1,
            "instruments": [{"fields": f} for f in instruments_meta().values()],
        }))

    def test_end_to_end_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_root = root / "data" / "raw"
            self._write_synthetic_raw(raw_root)
            runner_path = root / runner.RUNNER_NAME
            runner_path.write_bytes(RUNNER_PATH.read_bytes())
            id_tests = IdentityTests()
            attempt, spec_path, round_doc, run_doc = id_tests._specs(root / "results", runner_path, symbols=SYMBOLS)

            # Mock DEFAULT_INSTRUMENTS temporarily during runner execution
            orig_defaults = runner.DEFAULT_INSTRUMENTS
            orig_qlib = sys.modules.get("qlib")
            orig_build_signals = runner.build_signals
            sys.modules["qlib"] = ModuleType("qlib")
            try:
                setattr(runner, "DEFAULT_INSTRUMENTS", tuple(SYMBOLS))
                def one_synthetic_plan(rows):
                    bars, _plans = orig_build_signals(rows)
                    return bars, [{"formation_idx": 0, "formation_day": date(2022, 1, 1),
                                   "direction": "long", "r_lag3": -0.01, "bar_idx": 1}]
                setattr(runner, "build_signals", one_synthetic_plan)
                runner.run(str(spec_path), str(attempt), raw_root=str(raw_root))
            finally:
                setattr(runner, "DEFAULT_INSTRUMENTS", orig_defaults)
                setattr(runner, "build_signals", orig_build_signals)
                if orig_qlib is None:
                    sys.modules.pop("qlib", None)
                else:
                    sys.modules["qlib"] = orig_qlib

            for name in ("DONE", "FAILED", "INCOMPLETE", "verdict.json"):
                self.assertFalse((attempt / name).exists(), f"{name} must not be emitted by runner")

            artifacts = attempt / "artifacts"
            self.assertTrue((artifacts / "progress.json").is_file())
            self.assertTrue((artifacts / "cohort_results.json").is_file())
            self.assertTrue((artifacts / "falsification.json").is_file())
            falsification = json.loads((artifacts / "falsification.json").read_text())
            self.assertFalse(falsification["exact_source_battery_measured"])
            self.assertEqual(
                [item["id"] for item in falsification["falsification_plan"][:4]],
                ["post_2026_equity_walk_forward",
                 "lag_3_directional_reversal_persistence_audit",
                 "intraday_taq_high_frequency_attribution",
                 "cryptocurrency_high_frequency_friction_stress"],
            )
            self.assertEqual(json.loads((attempt / "state.json").read_text())["stage"], "ARTIFACT_READY")
            self.assertEqual(json.loads((attempt / "result.json").read_text())["status"], "ARTIFACT_READY")
            for g in runner.GRIDS:
                self.assertTrue((artifacts / f"grid_{g}.csv").is_file())


if __name__ == '__main__':
    unittest.main()
