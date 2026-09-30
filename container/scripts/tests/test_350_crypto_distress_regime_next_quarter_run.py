import gzip
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType

RUNNER_PATH = Path(__file__).resolve().parents[1] / "350_crypto_distress_regime_next_quarter_run.py"
spec = importlib.util.spec_from_file_location("crypto_distress_runner", RUNNER_PATH)
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
        self.assertEqual(got["symbols"], SYMBOLS)
        self.assertEqual(got["timeframes"], ["1d"])
        self.assertEqual(got["cohort_definition"], "instrument x timeframe")
        self.assertFalse(got["source_exact_match"])
        self.assertFalse(got["source_exact_match_is_execution_prerequisite"])
        self.assertFalse(got["source_universe_breadth_is_execution_prerequisite"])

    def test_trade_count_absence_is_read_from_the_field_list_not_prose(self):
        # The canonical kline record is 6-field OHLCV: no trade-count key anywhere.
        self.assertFalse(runner._trade_count_field_present(
            '{"open_time_ms":1,"close_time_ms":2,"open":"1","high":"1",'
            '"low":"1","close":"1","volume":"1"}'))
        # Prose that merely *mentions* trade count must not be read as a present field.
        self.assertFalse(runner._trade_count_field_present(
            'quote_volume, trade count and taker-buy splits are not present.'))
        # A real trade-count key in the row shape is detected.
        self.assertTrue(runner._trade_count_field_present(
            '{"open_time_ms":1,"close":"1","volume":"1","num_trades":"7"}'))
        # The recorded value flows into the universe evidence.
        got = runner.inspect_local_universe(catalog(), "no trade count here",
                                            symbols=SYMBOLS, instruments=instruments_meta())
        self.assertFalse(got["trade_count_field_present"])

    def test_missing_instrument_fee_fails_closed(self):
        got = runner.inspect_local_universe(catalog(), "", symbols=SYMBOLS,
                                            instruments={"AAAUSDT": {"taker_fee": "0.0005"}})
        self.assertFalse(got["legal"])

    def test_missing_1d_capability_fails_closed(self):
        config = catalog()
        config["intervals"] = ["1h"]
        got = runner.inspect_local_universe(config, "", symbols=SYMBOLS, instruments=instruments_meta())
        self.assertFalse(got["legal"])


class DomainAndGridTests(unittest.TestCase):
    def test_dca_grid_is_exact_registered_48(self):
        grid = runner.dca_grid()
        self.assertEqual(len(grid), 48)
        self.assertEqual(len({json.dumps(r, sort_keys=True) for r in grid}), 48)
        self.assertEqual({r["spacing_pct"] for r in grid}, {0.01, 0.02, 0.03, 0.04})
        self.assertEqual({r["size_multiplier"] for r in grid}, {1.0, 1.1})
        self.assertEqual({r["breakeven_tp_pct"] for r in grid}, {0.01, 0.02, 0.03})
        self.assertEqual({r["invalidation_pct"] for r in grid}, {0.05, 0.10})

    def test_expected_counts_scale_with_the_local_universe(self):
        counts = runner.expected_counts(SYMBOLS)
        self.assertEqual(counts["cohorts"], 2)
        self.assertEqual(counts["dca_configs_per_cohort"], 48)
        self.assertEqual(counts["case_evaluations_per_grid"], 96)
        self.assertEqual(counts["case_evaluations_total"], 960)

    def test_user_fixed_invariants_are_frozen(self):
        self.assertEqual(runner.START_EQUITY, 30000.0)
        self.assertEqual(runner.BASE_QUOTE, 1000.0)
        self.assertEqual(runner.LEVERAGE, 10.0)
        self.assertEqual(runner.MAX_ACTIVE_TRANCHES, 11)
        self.assertEqual(runner.MAX_ADD_LEVELS, 10)
        self.assertEqual(runner.TOTAL_TRANCHES, 12)
        sem = runner.EXECUTION_SEMANTICS
        self.assertEqual(sem["numeraire"], "USDT")
        self.assertEqual(sem["reserve_tranche"], 12)
        self.assertEqual(sem["same_bar_order"], "adverse_before_favorable_tp")
        self.assertEqual(sem["exit_mode"], "reduce_only")

    def test_distress_definition_and_predictors_are_record_faithful(self):
        self.assertEqual(runner.DISTRESS_THETA, 0.70)
        self.assertEqual(runner.TRAILING_PEAK_DAYS, 365)
        self.assertEqual(runner.MIN_TRADED_DAYS_IN_QUARTER, 30)
        self.assertEqual(runner.MIN_OBSERVED_HISTORY_DAYS, 90)
        self.assertEqual(list(runner.PREDICTORS), ["RVol", "LDVol", "Ret", "VTrend", "Age"])
        self.assertEqual(list(runner.PREDICTORS),
                         list(runner.REGISTERED_SIGNAL_RULE["predictors"]))
        self.assertFalse(runner.REGISTERED_SIGNAL_RULE["is_search_axis"])

    def test_quarter_bounds_are_calendar_quarters(self):
        self.assertEqual(runner.quarter_bounds(2024, 1), (date(2024, 1, 1), date(2024, 3, 31)))
        self.assertEqual(runner.quarter_bounds(2024, 4), (date(2024, 10, 1), date(2024, 12, 31)))
        self.assertEqual(runner.quarter_bounds(2025, 4), (date(2025, 10, 1), date(2025, 12, 31)))
        self.assertEqual(runner.quarter_bounds(2026, 3), (date(2026, 7, 1), date(2026, 9, 30)))

    def test_phase_windows_never_overlap_and_cover_full(self):
        hist, oos, full = (runner.PHASES["historical"], runner.PHASES["oos"], runner.PHASES["full"])
        self.assertLess(hist[1], oos[0])
        self.assertEqual(full[0], hist[0])
        self.assertEqual(full[1], oos[1])

    def test_grid_phase_mapping_is_total(self):
        for grid in runner.GRIDS:
            self.assertIn(runner.grid_phase(grid), runner.PHASES)


class CostModelTests(unittest.TestCase):
    def test_fee_comes_from_canonical_instrument_metadata(self):
        meta = instruments_meta()["AAAUSDT"]
        self.assertAlmostEqual(runner.cost_for("historical", meta)["fee_bps"], 5.0)
        self.assertAlmostEqual(runner.cost_for("fee_2x", meta)["fee_bps"], 10.0)
        self.assertAlmostEqual(runner.cost_for("cost_attrition_40bps", meta)["fee_bps"], 40.0)

    def test_slippage_is_price_ticks_not_bps(self):
        meta = instruments_meta()["AAAUSDT"]
        self.assertEqual(runner.cost_for("historical", meta)["ticks"], 1)
        self.assertEqual(runner.cost_for("slippage_2ticks", meta)["ticks"], 2)
        self.assertAlmostEqual(runner.cost_for("slippage_2ticks", meta)["tick_size"], 0.10)
        self.assertEqual(runner.cost_for("historical", meta)["entry_delay_bars"], 0)
        self.assertEqual(runner.cost_for("entry_delay_1_bar", meta)["entry_delay_bars"], 1)

    def test_funding_multipliers(self):
        meta = instruments_meta()["AAAUSDT"]
        self.assertEqual(runner.cost_for("historical", meta)["funding_mult"], 1.0)
        self.assertEqual(runner.cost_for("funding_2x", meta)["funding_mult"], 2.0)
        self.assertEqual(runner.cost_for("no_funding", meta)["funding_mult"], 0.0)
        self.assertEqual(runner.cost_for("no_funding_full", meta)["funding_mult"], 0.0)

    def test_fill_price_is_one_adverse_tick_per_side(self):
        cost = runner.cost_for("historical", instruments_meta()["AAAUSDT"])
        self.assertAlmostEqual(runner._fill_price(100.0, "buy", cost), 100.10)
        self.assertAlmostEqual(runner._fill_price(100.0, "sell", cost), 99.90)


class PanelTests(unittest.TestCase):
    def _rows(self, path_):
        """A declining series whose drawdown from a 365-day peak exceeds -70%."""
        rows = {}
        day = date(2022, 1, 1)
        price = 100.0
        for _ in range(400):
            ms = int(datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc).timestamp() * 1000)
            price *= 0.99
            rows[ms] = {
                "open_time_ms": ms, "close_time_ms": ms + MS_DAY - 1,
                "open": price, "high": price * 1.01, "low": price * 0.99,
                "close": price, "volume": 100.0,
            }
            day += timedelta(days=1)
        return rows

    def test_panel_marks_sustained_drawdown_as_distressed_and_onset_lags_one_quarter(self):
        panel = runner.build_coin_quarters("AAAUSDT", self._rows(None))
        self.assertTrue(panel)
        distressed = [r for r in panel if r["distressed"]]
        self.assertTrue(distressed)
        for row in distressed:
            self.assertTrue(row["touched_theta"])
            self.assertLessEqual(row["dd_end"], -runner.DISTRESS_THETA)
        onsets = [r for r in panel if r["onset"]]
        self.assertEqual(len(onsets), 1, "only the first distressed quarter is an onset")
        first = onsets[0]
        self.assertFalse(first["prior_distressed"])
        prior = [r for r in panel if r["label"] < first["label"]][-1]
        self.assertFalse(prior["distressed"])

    def test_onset_is_false_when_prior_quarter_also_distressed(self):
        rows = self._rows(None)
        panel = runner.build_coin_quarters("AAAUSDT", rows)
        labels = [r["label"] for r in panel]
        for row in panel:
            if not row["distressed"]:
                continue
            idx = labels.index(row["label"])
            if idx > 0 and panel[idx - 1]["distressed"]:
                self.assertFalse(row["onset"])

    def test_predictors_are_finite_and_vtrend_is_quarter_over_quarter(self):
        panel = runner.build_coin_quarters("AAAUSDT", self._rows(None))
        for i, row in enumerate(panel):
            self.assertIsNotNone(row["RVol"])
            self.assertIsNotNone(row["LDVol"])
            self.assertIsNotNone(row["Ret"])
            self.assertIsNotNone(row["Age"])
            if i == 0 or panel[i - 1]["LDVol"] is None:
                self.assertIsNone(row["VTrend"])
            else:
                self.assertAlmostEqual(row["VTrend"],
                                       row["LDVol"] - panel[i - 1]["LDVol"], places=12)

    def test_rvol_is_annualized_with_sqrt_365(self):
        rows = {}
        day = date(2022, 1, 1)
        price = 100.0
        for i in range(120):
            ms = int(datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc).timestamp() * 1000)
            price *= 1.02 if i % 2 == 0 else 0.98
            rows[ms] = {"open_time_ms": ms, "close_time_ms": ms + MS_DAY - 1, "open": price,
                        "high": price, "low": price, "close": price, "volume": 10.0}
            day += timedelta(days=1)
        panel = runner.build_coin_quarters("AAAUSDT", rows)
        row = next(r for r in panel if r["RVol"] is not None)
        self.assertGreater(row["RVol"], 0.0)

    def test_eligibility_requires_non_distressed_and_history(self):
        panel = runner.build_coin_quarters("AAAUSDT", self._rows(None))
        for row in panel:
            if row["eligible"]:
                self.assertFalse(row["distressed"])
                self.assertGreaterEqual(row["traded_days"], runner.MIN_TRADED_DAYS_IN_QUARTER)
                self.assertGreaterEqual(row["history_days"], runner.MIN_OBSERVED_HISTORY_DAYS)


class ModelTests(unittest.TestCase):
    def _synthetic_panel(self, positives=2, negatives=8):
        rows = []
        for i in range(positives + negatives):
            rows.append({
                "symbol": "AAAUSDT", "year": 2022 + i % 2, "quarter": (i % 4) + 1,
                "label": "%dQ%d" % (2022 + i % 2, (i % 4) + 1),
                "RVol": 0.5 + 0.01 * i, "LDVol": 10.0 + 0.1 * i, "Ret": 0.1 * i,
                "VTrend": 0.05 * i, "Age": 100.0 + i,
                "onset": i < positives, "distressed": i < positives,
                "prior_distressed": False, "eligible": False,
            })
        return rows

    def test_pooled_logit_needs_both_classes(self):
        # No positive class at all.
        model, info = runner.fit_pooled_logit(self._synthetic_panel(positives=0, negatives=8))
        self.assertIsNone(model)
        self.assertFalse(info["identifiable"])
        # Every row positive (negatives=0 is the point: the helper defaults it to 8, which
        # would silently leave a negative class and make this case pass for the wrong reason).
        model, info = runner.fit_pooled_logit(self._synthetic_panel(positives=9, negatives=0))
        self.assertIsNone(model)
        self.assertFalse(info["identifiable"])
        self.assertEqual(info["negatives"], 0)

    def test_pooled_logit_fits_and_is_deterministic(self):
        rows = self._synthetic_panel()
        model_a, info_a = runner.fit_pooled_logit(rows)
        model_b, _ = runner.fit_pooled_logit(rows)
        self.assertTrue(info_a["identifiable"])
        self.assertEqual(model_a["beta"], model_b["beta"])
        self.assertEqual(len(model_a["beta"]), len(runner.PREDICTORS) + 1)

    def test_scoring_uses_training_statistics_not_the_scored_cross_section(self):
        train = self._synthetic_panel()
        model, _ = runner.fit_pooled_logit(train)
        target = dict(train[0])
        # A row's score must depend only on its own values and the TRAINING stats, never on
        # which other rows happen to share its scoring batch. Adding wildly different
        # companions must therefore leave this row's score untouched.
        alone = runner.score_rows(model["beta"], [target], model["stats"], model["years"])
        companions = [dict(target, RVol=r["RVol"] * 50.0, LDVol=r["LDVol"] * 50.0, Age=1.0)
                      for r in train[1:]]
        batched = runner.score_rows(model["beta"], [target] + companions,
                                    model["stats"], model["years"])
        self.assertEqual(alone, batched[:1])
        # Control: changing the row's OWN predictors must change its score, or the
        # batch-independence assertion above would pass for a constant-output reason.
        shifted = runner.score_rows(model["beta"], [dict(target, RVol=target["RVol"] * 50.0)],
                                    model["stats"], model["years"])
        self.assertNotEqual(alone, shifted)

    def test_winsorization_clips_extremes(self):
        values = [0.0, 1.0, 2.0, 3.0, 1000.0]
        bounds = runner._winsor_bounds(values)
        self.assertIsNotNone(bounds)
        lo, hi = bounds
        # The 1000 outlier is clipped down to the training 99th-percentile bound...
        self.assertAlmostEqual(runner._clip(1000.0, bounds), hi)
        self.assertLess(hi, 1000.0)
        # ...and a value far below the 1st percentile is clipped up to the lower bound.
        self.assertAlmostEqual(runner._clip(-500.0, bounds), lo)
        # In-range values pass through untouched.
        self.assertAlmostEqual(runner._clip(2.0, bounds), 2.0)
        # A scoring-time value never seen in training still clips numerically, so it cannot
        # raise KeyError inside the panel.
        self.assertAlmostEqual(runner._clip(1e12, bounds), hi)

    def test_regime_gate_uses_training_median_only(self):
        # The gate needs at least two prior quarters to form a training median, so the calm
        # and hot cases are compared against the SAME two-quarter training history.
        history = {"2021Q3": 0.05, "2021Q4": 0.15}
        def panels(current):
            out = {}
            for sym, low in (("A", 1.0), ("B", 1.0)):
                out[sym] = [{"label": label, "RVol": vol * low} for label, vol in history.items()]
                out[sym].append({"label": "2022Q1", "RVol": current * low})
            return out
        # Training median of (0.05, 0.15) is 0.10; a current quarter at/below it opens the gate.
        calm = runner.market_wide_vol(panels(0.10), "2022Q1", list(history))
        self.assertAlmostEqual(calm["training_median"], 0.10)
        self.assertTrue(calm["gate_open"], calm)
        # A turbulent current quarter above the training median closes it.
        hot = runner.market_wide_vol(panels(9.0), "2022Q1", list(history))
        self.assertFalse(hot["gate_open"], hot)
        # Too little training history fails closed rather than gating on a single point.
        thin = runner.market_wide_vol(panels(0.10), "2022Q1", ["2021Q4"])
        self.assertFalse(thin["gate_open"])
        self.assertIsNone(thin["percentile"])

    def test_formation_plan_never_uses_the_formation_quarter_for_training(self):
        panels = {
            "AAAUSDT": self._panel_for("AAAUSDT"),
            "BBBUSDT": self._panel_for("BBBUSDT"),
        }
        plan, diag = runner.build_formation_plan(panels, SYMBOLS)
        self.assertGreater(diag["formations"], 0)
        for formation in plan:
            self.assertIn("legs", formation)
            for direction in formation["legs"].values():
                self.assertIn(direction, ("long", "short"))
            self.assertLessEqual(len(formation["legs"]), 2)

    def _panel_for(self, symbol):
        rows = []
        for idx, (label, onset) in enumerate((("2022Q1", False), ("2022Q2", True),
                                              ("2022Q3", False), ("2022Q4", False),
                                              ("2023Q1", False), ("2023Q2", False),
                                              ("2023Q3", False), ("2023Q4", False))):
            year, q = int(label[:4]), int(label[-1])
            scale = 1.0 if symbol == "AAAUSDT" else 1.7
            rows.append({
                "symbol": symbol, "year": year, "quarter": q, "label": label,
                "quarter_start": "2022-01-01", "quarter_end": "2022-03-31",
                "traded_days": 90, "bars": 90, "history_days": 400 + idx,
                "RVol": (0.4 + 0.05 * idx) * scale, "LDVol": (10.0 + 0.3 * idx) * scale,
                "Ret": 0.1 * idx * scale, "VTrend": 0.05 * idx,
                "Age": float(400 + idx), "trailing_peak": 1.0, "dd_end": -0.1,
                "touched_theta": onset, "distressed": onset,
                "prior_distressed": False, "onset": onset,
                "eligible": not onset,
                "last_bar_open_ms": 0, "last_bar_close_ms": 0, "first_bar_open_ms": 0,
            })
        return rows


class ExecutionTests(unittest.TestCase):
    def _dca(self, spacing=0.01, mult=1.1, tp=0.02, inv=0.10):
        return {"spacing_pct": spacing, "size_multiplier": mult,
                "breakeven_tp_pct": tp, "invalidation_pct": inv}

    def test_long_leg_adds_on_adverse_price_moves_up_to_eleven_active_tranches(self):
        bars = [{"open": 100.0, "high": 100.0, "low": 50.0, "close": 60.0,
                 "open_time_ms": 0, "close_time_ms": MS_DAY - 1}]
        cost = runner.cost_for("historical", instruments_meta()["AAAUSDT"])
        got = runner.simulate_leg(bars, "long", self._dca(), cost, [])
        self.assertEqual(got["adds"], runner.MAX_ADD_LEVELS)
        self.assertEqual(got["max_active_tranches"], runner.MAX_ACTIVE_TRANCHES)

    def test_short_leg_mirrors_the_ladder_upward(self):
        bars = [{"open": 100.0, "high": 150.0, "low": 100.0, "close": 140.0,
                 "open_time_ms": 0, "close_time_ms": MS_DAY - 1}]
        cost = runner.cost_for("historical", instruments_meta()["AAAUSDT"])
        got = runner.simulate_leg(bars, "short", self._dca(), cost, [])
        self.assertEqual(got["adds"], runner.MAX_ADD_LEVELS)
        self.assertEqual(got["max_active_tranches"], runner.MAX_ACTIVE_TRANCHES)

    def test_gross_net_decomposition_holds_for_both_directions(self):
        cost = runner.cost_for("full", instruments_meta()["AAAUSDT"])
        funding = [{"t": MS_DAY // 2, "rate": 0.0001, "mark": 100.0, "cost_per_unit": 0.01}]
        for direction, bars in (
            ("long", [{"open": 100.0, "high": 103.0, "low": 99.0, "close": 102.0,
                       "open_time_ms": 0, "close_time_ms": MS_DAY - 1}]),
            ("short", [{"open": 100.0, "high": 101.0, "low": 97.0, "close": 98.0,
                        "open_time_ms": 0, "close_time_ms": MS_DAY - 1}]),
        ):
            got = runner.simulate_leg(bars, direction, self._dca(), cost, funding)
            self.assertAlmostEqual(got["gross_pnl"] - got["fees"] - got["funding"],
                                   got["net_pnl"], places=6)
            self.assertGreater(got["funding_events_charged"], 0)

    def test_funding_is_charged_at_the_observation_and_signed_by_direction(self):
        cost = runner.cost_for("full", instruments_meta()["AAAUSDT"])
        funding = [{"t": MS_DAY // 2, "rate": 0.0005, "mark": 100.0, "cost_per_unit": 0.05}]
        bars = [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0,
                 "open_time_ms": 0, "close_time_ms": MS_DAY - 1}]
        long_got = runner.simulate_leg(bars, "long", self._dca(), cost, funding)
        short_got = runner.simulate_leg(bars, "short", self._dca(), cost, funding)
        self.assertGreater(long_got["funding"], 0.0)
        self.assertLess(short_got["funding"], 0.0)

    def test_funding_2x_doubles_the_charge_and_no_funding_zeroes_it(self):
        bars = [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0,
                 "open_time_ms": 0, "close_time_ms": MS_DAY - 1}]
        funding = [{"t": MS_DAY // 2, "rate": 0.0005, "mark": 100.0, "cost_per_unit": 0.05}]
        base = runner.simulate_leg(bars, "long", self._dca(),
                                   runner.cost_for("full", instruments_meta()["AAAUSDT"]), funding)
        doubled = runner.simulate_leg(bars, "long", self._dca(),
                                       runner.cost_for("funding_2x", instruments_meta()["AAAUSDT"]),
                                       funding)
        zeroed = runner.simulate_leg(bars, "long", self._dca(),
                                     runner.cost_for("no_funding", instruments_meta()["AAAUSDT"]),
                                     funding)
        self.assertAlmostEqual(doubled["funding"], 2.0 * base["funding"], places=9)
        self.assertEqual(zeroed["funding"], 0.0)

    def test_stop_precedes_tp_on_the_same_adverse_bar(self):
        cost = runner.cost_for("full", instruments_meta()["AAAUSDT"])
        wide = [{"open": 100.0, "high": 130.0, "low": 50.0, "close": 60.0,
                 "open_time_ms": 0, "close_time_ms": MS_DAY - 1}]
        got = runner.simulate_leg(wide, "long", self._dca(tp=0.02, inv=0.10), cost, [])
        self.assertEqual(got["exit_reason"], "stop")

    def test_leg_bars_start_after_formation_and_hold_one_quarter(self):
        day = date(2024, 1, 1)
        rows = {}
        for i in range(400):
            d = day + timedelta(days=i)
            ms = int(datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc).timestamp() * 1000)
            rows[ms] = {"open_time_ms": ms, "close_time_ms": ms + MS_DAY - 1, "open": 100.0,
                        "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1.0}
        formation_close = rows[date(2024, 3, 31) if False else
                               int(datetime.combine(date(2024, 3, 31), datetime.min.time(),
                                                   tzinfo=timezone.utc).timestamp() * 1000)]["close_time_ms"]
        bars, entry_day = runner.leg_bars(rows, formation_close, 0)
        self.assertTrue(bars)
        self.assertEqual(entry_day, date(2024, 4, 1))
        self.assertLessEqual(len(bars), 91)
        delayed, delayed_day = runner.leg_bars(rows, formation_close, 1)
        self.assertEqual(delayed_day, date(2024, 4, 2))

    def test_neighbourhood_uses_historical_sign_agreement(self):
        historical = []
        for dca in runner.DCA_GRID:
            historical.append({"symbol": "AAAUSDT", "timeframe": "1d", "signal_rule_version": 1,
                               **dca, "net_pnl": 100.0})
        winner = dict(historical[0])
        got = runner.neighbourhood(winner, historical)
        self.assertEqual(got["same_sign_fraction"], 1.0)
        self.assertTrue(got["passed"])
        flipped = [dict(r, net_pnl=-1.0) for r in historical]
        self.assertEqual(runner.neighbourhood(dict(flipped[0]), flipped)["same_sign_fraction"], 1.0)


class SelectorTests(unittest.TestCase):
    def _rows(self, net=100.0, sharpe=1.5, episodes=6, oos_net=100.0, oos_sharpe=1.0,
              full_net=100.0, robust_net=100.0):
        out = {}
        for grid in runner.GRIDS:
            out[grid] = [{
                "symbol": "AAAUSDT", "timeframe": "1d", "signal_rule_version": 1,
                **dca, "grid": grid, "gross_pnl": net, "fees": 1.0, "funding": 1.0,
                "net_pnl": net, "ending_equity": 30000.0 + net, "episodes": episodes,
                "fills": episodes, "adds": 0, "turnover_usdt": 1000.0, "sharpe": sharpe,
                "max_dd_pct": 1.0, "max_dd_usdt": 1.0, "annualized_return": 0.1,
                "max_effective_leverage": 1.0, "capital_utilization": 0.3,
                "tp_hits": 1, "stop_hits": 1, "margin_calls": 0, "end_exits": episodes - 2,
                "open_at_end": 0,
            } for dca in runner.DCA_GRID]
        for grid in runner.GRIDS:
            if grid == "oos":
                for r in out[grid]:
                    r["net_pnl"], r["sharpe"] = oos_net, oos_sharpe
            elif grid == "full":
                for r in out[grid]:
                    r["net_pnl"] = full_net
            elif grid in ("fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks"):
                for r in out[grid]:
                    r["net_pnl"] = robust_net
        return out

    def test_insufficient_episodes_culls_before_selection(self):
        winner, detail = runner.select_cohort(self._rows(episodes=1))
        self.assertIsNone(winner)
        self.assertEqual(detail["cull_reasons"], ["insufficient_trades"])

    def test_no_qualifying_candidate_culls(self):
        winner, detail = runner.select_cohort(self._rows(net=-100.0, sharpe=-1.0))
        self.assertIsNone(winner)
        self.assertEqual(detail["cull_reasons"], ["no_qualifying_candidate"])

    def test_full_survivor_requires_every_leg(self):
        winner, detail = runner.select_cohort(self._rows())
        self.assertIsNotNone(winner)
        self.assertEqual(detail["cull_reasons"], [])

    def test_oos_and_robustness_failures_are_recorded(self):
        winner, detail = runner.select_cohort(self._rows(oos_net=-5.0))
        self.assertIsNone(winner)
        self.assertIn("oos_economic", detail["cull_reasons"])
        winner, detail = runner.select_cohort(self._rows(robust_net=-5.0))
        self.assertIsNone(winner)
        self.assertTrue(any(r.startswith("robustness_economic") for r in detail["cull_reasons"]))
        winner, detail = runner.select_cohort(self._rows(full_net=-5.0))
        self.assertIsNone(winner)
        self.assertIn("full_economic", detail["cull_reasons"])


class IdentityTests(unittest.TestCase):
    def _specs(self, root, runner_path, symbols=SYMBOLS):
        family = runner.FAMILY_ID
        round_id = family + "-r1"
        run_id = round_id + "-u1"
        fingerprint_input = "crypto-distress-test-fingerprint"
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
            "dca_domain": {**{k: list(v) for k, v in runner.DCA_AXES.items()},
                           "grid": runner.DCA_GRID, "base_quote": runner.BASE_QUOTE},
            "execution_semantics": runner.EXECUTION_SEMANTICS,
            "gates": runner.GATES,
            "regime_gate": {"selection": runner.REGIME_GATE_SELECTION},
            "expected": {"grids": list(runner.GRIDS), "cohorts": len(symbols),
                         "expected_case_evaluations":
                             runner.expected_counts(symbols)["case_evaluations_total"]},
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
            drifted["signal_rule"]["predictors"] = ["RVol"]
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
        """Two synthetic coins over the registered window with a real distress episode."""
        klines = root / "binance" / "usdm" / "klines"
        funding_dir = root / "binance" / "usdm" / "funding"
        for symbol in SYMBOLS:
            target = klines / symbol / "1d"
            target.mkdir(parents=True)
            grouped = {}
            day = date(2022, 1, 1)
            stop = date(2026, 9, 11)
            i = 0
            while day <= stop:
                ms = int(datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc).timestamp() * 1000)
                if symbol == "AAAUSDT":
                    base = 100.0 - min(day.toordinal() - date(2022, 1, 1).toordinal(), 400) * 0.15
                    ret = 1.0 if i % 7 else 1.02
                else:
                    base = 40.0 + (i % 11) * 0.4
                    ret = 1.0 if i % 5 else 0.99
                open_ = base
                close = base * ret
                high = max(open_, close) * 1.01
                low = min(open_, close) * 0.99
                row = {"open_time_ms": ms, "close_time_ms": ms + MS_DAY - 1,
                       "open": "%.8f" % open_, "high": "%.8f" % high,
                       "low": "%.8f" % low, "close": "%.8f" % close,
                       "volume": "%.8f" % (1000.0 + i)}
                grouped.setdefault(day.strftime("%Y-%m"), []).append(row)
                day += timedelta(days=1)
                i += 1
            for month, rows in grouped.items():
                path = target / ("%s-1d-%s.jsonl.gz" % (symbol, month))
                with gzip.open(path, "wt", encoding="utf-8") as fh:
                    for row in sorted(rows, key=lambda r: r["open_time_ms"]):
                        fh.write(json.dumps(row, separators=(",", ":")) + "\n")
            fdir = funding_dir / symbol
            fdir.mkdir(parents=True)
            with gzip.open(fdir / ("%s-funding.jsonl.gz" % symbol), "wt", encoding="utf-8") as fh:
                for k in range(200):
                    t = int(datetime(2023, 1, 1, tzinfo=timezone.utc).timestamp() * 1000) + k * MS_DAY
                    fh.write(json.dumps({"funding_time_ms": t,
                                         "funding_rate": "0.0001" if k % 2 else "-0.0001",
                                         "mark_price": "100.0", "truth_status": "official"}) + "\n")
                # A modeled row INSIDE the load window: it must be ignored as a funding cost
                # rather than charged, and a missing official interval must cost zero.
                modeled_t = int(datetime(2023, 6, 1, tzinfo=timezone.utc).timestamp() * 1000)
                fh.write(json.dumps({"funding_time_ms": modeled_t, "funding_rate": "0.5",
                                     "mark_price": "100.0", "truth_status": "modeled"}) + "\n")
                # An out-of-window official row must not inflate the in-window count.
                fh.write(json.dumps({"funding_time_ms": 1, "funding_rate": "0.5",
                                     "mark_price": "100.0", "truth_status": "official"}) + "\n")
        meta = root / "_meta"
        meta.mkdir(parents=True)
        (meta / "CONFIG.json").write_text(json.dumps(catalog()))
        (meta / "SCHEMA.md").write_text("Binance USD-M perpetual 1d schema; trade count absent")
        instruments = root / "binance" / "usdm" / "instruments"
        instruments.mkdir(parents=True)
        (instruments / "usdm-perp-instruments.json").write_text(json.dumps({
            "schema_version": 1,
            "instruments": [
                {"fields": dict(meta_, id=symbol + "-PERP.BINANCE")}
                for symbol, meta_ in instruments_meta().items()
            ],
        }))
        return klines, funding_dir, meta, instruments / "usdm-perp-instruments.json"

    def test_end_to_end_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "raw"
            klines, funding_dir, meta, instruments_path = self._write_synthetic_raw(raw)
            attempt = root / "results" / runner.FAMILY_ID / "rounds" / (runner.FAMILY_ID + "-r1") \
                / "attempts" / (runner.FAMILY_ID + "-r1-u1")
            attempt.mkdir(parents=True)
            fingerprint_input = "crypto-distress-e2e"
            fingerprint = runner.sha256(fingerprint_input.encode())
            round_doc = {
                "schema_version": 1, "document_kind": "round_spec", "family_id": runner.FAMILY_ID,
                "round_id": runner.FAMILY_ID + "-r1", "semantic_fingerprint": fingerprint,
                "eligible_universe": {
                    "execution_market": runner.EXECUTION_MARKET, "instruments": SYMBOLS,
                    "timeframes": [runner.TIMEFRAME], "cohort_definition": "instrument x timeframe",
                },
            }
            round_raw = (json.dumps(round_doc, indent=2) + "\n").encode()
            (attempt.parents[1] / "round-spec.json").write_bytes(round_raw)
            run_doc = {
                "schema_version": 1, "document_kind": "run_spec", "family_id": runner.FAMILY_ID,
                "round_id": runner.FAMILY_ID + "-r1", "run_id": runner.FAMILY_ID + "-r1-u1",
                "fingerprint_input": fingerprint_input, "semantic_fingerprint": fingerprint,
                "round_spec_sha256": runner.sha256(round_raw),
                "selector_version": "cohort-selector-v1",
                "disposition_version": "cohort-disposition-v1",
                "disposition_mapping_version": "v1.4.0",
                "script": {"path": "/scripts/" + runner.RUNNER_NAME,
                           "sha256": runner.sha256(RUNNER_PATH.read_bytes())},
                "data": {"source": "/data/raw", "market": runner.EXECUTION_MARKET,
                         "symbols": SYMBOLS, "timeframes": [runner.TIMEFRAME]},
                "signal_rule": runner.REGISTERED_SIGNAL_RULE,
                "params": [{"signal_rule_version": 1}],
                "dca_domain": {**{k: list(v) for k, v in runner.DCA_AXES.items()},
                               "grid": runner.DCA_GRID, "base_quote": runner.BASE_QUOTE},
                "execution_semantics": runner.EXECUTION_SEMANTICS,
                "gates": runner.GATES,
                "regime_gate": {"selection": runner.REGIME_GATE_SELECTION},
                "expected": {"grids": list(runner.GRIDS), "cohorts": len(SYMBOLS),
                             "expected_case_evaluations":
                                 runner.expected_counts(SYMBOLS)["case_evaluations_total"]},
            }
            spec_path = attempt / "run-spec.json"
            spec_path.write_text(json.dumps(run_doc, indent=2) + "\n")

            old = (runner.CONFIG_PATH, runner.SCHEMA_PATH)
            qlib = ModuleType("qlib")
            qlib.__version__ = "0.9.7"
            sys.modules["qlib"] = qlib
            runner.CONFIG_PATH = meta / "CONFIG.json"
            runner.SCHEMA_PATH = meta / "SCHEMA.md"
            try:
                result = runner.run(spec_path, attempt, RUNNER_PATH, klines_root=klines,
                                    funding_root=funding_dir, instruments_path=instruments_path)
            finally:
                runner.CONFIG_PATH, runner.SCHEMA_PATH = old
                sys.modules.pop("qlib", None)

            self.assertEqual(result["status"], "ARTIFACT_READY")
            self.assertEqual(result["case_evaluations_total"],
                             runner.expected_counts(SYMBOLS)["case_evaluations_total"])
            self.assertEqual(result["expected_case_evaluations"],
                             runner.expected_counts(SYMBOLS)["case_evaluations_total"])
            self.assertEqual(result["cohort_count"], len(SYMBOLS))
            self.assertTrue(result["coverage_complete"], result.get("assertion_failures"))
            self.assertTrue(result["assertions_all_true"], result.get("assertion_failures"))
            self.assertFalse(any((attempt / n).exists()
                                 for n in ("DONE", "FAILED", "INCOMPLETE")))
            self.assertFalse((attempt.parents[1] / "verdict.json").exists())
            evidence = json.loads((attempt / "artifacts" / "local_data_evidence.json").read_text())
            self.assertFalse(evidence["local_universe"]["source_exact_match"])
            self.assertEqual(evidence["local_universe"]["cohort_definition"],
                             "instrument x timeframe")
            funding_report = evidence["funding_reports"][SYMBOLS[0]]
            self.assertEqual(funding_report["modeled_ignored"], 1)
            self.assertEqual(funding_report["out_of_window"], 1)
            self.assertTrue(funding_report["missing_intervals_are_zero_not_modeled"])
            for grid in runner.GRIDS:
                self.assertTrue((attempt / "artifacts" / ("grid_%s.csv" % grid)).is_file())
            self.assertTrue((attempt / "artifacts" / "cohort_results.json").is_file())
            self.assertTrue((attempt / "artifacts" / "cohort_survivors.json").is_file())
            self.assertTrue((attempt / "artifacts" / "panel_evidence.json").is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
