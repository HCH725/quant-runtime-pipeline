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

RUNNER_PATH = Path(__file__).resolve().parents[1] / "340_bitcoin_friday_post_event_drift_run.py"
spec = importlib.util.spec_from_file_location("bitcoin_friday_runner", RUNNER_PATH)
assert spec is not None and spec.loader is not None
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class BitcoinFridayLocalRunnerTests(unittest.TestCase):
    def catalog(self):
        return {
            "venue": "BINANCE",
            "market_type": "usdm_perp",
            "symbols": ["BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"],
            "intervals": ["15m", "1d", "1h", "1w", "30m", "4h", "5m"],
            "datasets": {
                "klines": "Binance USD-M perpetual futures klines",
                "spot_klines": "Binance spot klines (BTCUSDT/ETHUSDT/BNBUSDT/SOLUSDT)",
            },
        }

    def test_local_binance_spot_is_legal_without_kraken_exact_match(self):
        got = runner.inspect_local_universe(self.catalog(), "Binance spot schema")
        self.assertTrue(got["legal"])
        self.assertEqual(got["symbol"], "BTCUSDT")
        self.assertEqual(got["market_type"], "spot")
        self.assertFalse(got["source_exact_match"])
        self.assertFalse(got["source_exact_match_is_execution_prerequisite"])
        self.assertIn("provenance", got["reason"])

    def test_friday_fixed_est_clock_is_exact(self):
        got = runner.episode_times(date(2026, 9, 11))
        event = datetime.fromtimestamp(got["event_ms"] / 1000, timezone.utc)
        entry = datetime.fromtimestamp(got["entry_ms"] / 1000, timezone.utc)
        exit_bar = datetime.fromtimestamp(got["exit_bar_ms"] / 1000, timezone.utc)
        self.assertEqual((event.weekday(), event.hour), (4, 20))
        self.assertEqual((entry.weekday(), entry.hour), (4, 21))
        self.assertEqual((exit_bar.weekday(), exit_bar.hour), (5, 0))
        delayed = runner.episode_times(date(2026, 9, 11), 1)
        self.assertEqual(datetime.fromtimestamp(delayed["entry_ms"] / 1000, timezone.utc).hour, 22)

    def test_dca_grid_is_exact_registered_48(self):
        grid = runner.dca_grid()
        self.assertEqual(len(grid), 48)
        self.assertEqual(len({json.dumps(row, sort_keys=True) for row in grid}), 48)
        self.assertEqual({row["spacing_pct"] for row in grid}, {0.01, 0.02, 0.03, 0.04})
        self.assertEqual({row["size_multiplier"] for row in grid}, {1.0, 1.1})
        self.assertEqual({row["breakeven_tp_pct"] for row in grid}, {0.01, 0.02, 0.03})
        self.assertEqual({row["invalidation_pct"] for row in grid}, {0.05, 0.10})
        self.assertEqual(runner.expected_counts()["case_evaluations_total"], 480)

    def _row(self, when, open_, high, low, close):
        ms = int(when.timestamp() * 1000)
        return {
            "open_time_ms": ms,
            "close_time_ms": ms + runner.MS_HOUR - 1,
            "open": "%.8f" % open_,
            "high": "%.8f" % high,
            "low": "%.8f" % low,
            "close": "%.8f" % close,
            "volume": "100.0",
        }

    def _write_synthetic_raw(self, root):
        target = root / "binance" / "spot" / "klines" / runner.SYMBOL / runner.TIMEFRAME
        target.mkdir(parents=True)
        grouped = {}
        friday = date.fromisoformat(runner.PHASES["full"][0])
        friday += timedelta(days=(4 - friday.weekday()) % 7)
        stop = date.fromisoformat(runner.PHASES["full"][1])
        idx = 0
        while friday <= stop:
            event = datetime.combine(friday, datetime.min.time(), tzinfo=timezone.utc).replace(hour=20)
            # Vary each week's drift so positive-return cells have finite non-zero volatility.
            drift = 1.20 + (idx % 7) * 0.08
            prices = [
                (event, 100.00, 100.20, 99.90, 100.05),
                (event + timedelta(hours=1), 100.05, 100.45, 99.95, 100.20),
                (event + timedelta(hours=2), 100.20, 100.75, 100.05, 100.50),
                (event + timedelta(hours=3), 100.50, 101.10, 100.30, 100.80),
                (event + timedelta(hours=4), 100.80, 100.80 + drift, 100.60, 100.00 + drift),
            ]
            for values in prices:
                row = self._row(*values)
                stamp = values[0]
                grouped.setdefault(stamp.strftime("%Y-%m"), []).append(row)
            friday += timedelta(days=7)
            idx += 1
        for month, rows in grouped.items():
            path = target / ("%s-%s-%s.jsonl.gz" % (runner.SYMBOL, runner.TIMEFRAME, month))
            with gzip.open(path, "wt", encoding="utf-8") as fh:
                for row in sorted(rows, key=lambda x: x["open_time_ms"]):
                    fh.write(json.dumps(row, separators=(",", ":")) + "\n")
        return target

    def _specs(self, root, runner_path):
        family = runner.FAMILY_ID
        round_id = family + "-r2"
        run_id = round_id + "-u1"
        fingerprint_input = "bitcoin-friday-r2-test"
        fingerprint = "sha256:" + hashlib.sha256(fingerprint_input.encode()).hexdigest()
        attempt = root / family / "rounds" / round_id / "attempts" / run_id
        attempt.mkdir(parents=True)
        round_doc = {
            "schema_version": 1,
            "document_kind": "round_spec",
            "family_id": family,
            "round_id": round_id,
            "semantic_fingerprint": fingerprint,
            "eligible_universe": {
                "execution_market": "BINANCE_SPOT",
                "source_market": "Kraken BTC/USD spot",
                "instruments": [runner.SYMBOL],
                "timeframes": [runner.TIMEFRAME],
                "cohort_definition": "instrument x timeframe",
                "claim_scope": "canonical local Binance BTCUSDT spot 1h only",
            },
        }
        round_raw = (json.dumps(round_doc, indent=2) + "\n").encode()
        round_path = attempt.parents[1] / "round-spec.json"
        round_path.write_bytes(round_raw)
        run_doc = {
            "schema_version": 1,
            "document_kind": "run_spec",
            "family_id": family,
            "round_id": round_id,
            "run_id": run_id,
            "fingerprint_input": fingerprint_input,
            "semantic_fingerprint": fingerprint,
            "round_spec_sha256": runner.sha256(round_raw),
            "selector_version": "cohort-selector-v1",
            "disposition_version": "cohort-disposition-v1",
            "script": {
                "path": "/scripts/" + runner.RUNNER_NAME,
                "sha256": runner.sha256(Path(runner_path).read_bytes()),
            },
            "data": {
                "source": "/data/raw",
                "market": "BINANCE_SPOT",
                "symbols": [runner.SYMBOL],
                "timeframes": [runner.TIMEFRAME],
                "clock_convention": "fixed EST (UTC-05:00)",
            },
            "params": [{"signal_rule_version": 1}],
            "dca_domain": {
                **{k: list(v) for k, v in runner.DCA_AXES.items()},
                "grid": runner.DCA_GRID,
                "base_quote": runner.BASE_QUOTE,
            },
            "gates": {
                "min_episodes_is": runner.MIN_EPISODES_IS,
                "min_episodes_oos": runner.MIN_EPISODES_OOS,
                "min_neighbour_same_sign_fraction": runner.MIN_NEIGHBOUR,
            },
            "expected": {
                "grids": list(runner.GRIDS),
                "expected_case_evaluations": runner.expected_counts()["case_evaluations_total"],
            },
        }
        spec_path = attempt / "run-spec.json"
        spec_path.write_text(json.dumps(run_doc, indent=2) + "\n")
        return attempt, spec_path, round_doc, run_doc

    def test_direct_identity_rejects_ownership_and_script_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            attempt, _spec_path, round_doc, run_doc = self._specs(Path(tmp), RUNNER_PATH)
            runner.validate_identity(run_doc, round_doc, attempt, RUNNER_PATH)
            leaked = dict(run_doc, task_id="t_forbidden")
            with self.assertRaisesRegex(ValueError, "ownership"):
                runner.validate_identity(leaked, round_doc, attempt, RUNNER_PATH)
            wrong = json.loads(json.dumps(run_doc))
            wrong["script"]["sha256"] = "sha256:" + "0" * 64
            with self.assertRaisesRegex(ValueError, "script hash"):
                runner.validate_identity(wrong, round_doc, attempt, RUNNER_PATH)

    def test_full_run_emits_480_cases_and_no_terminal_or_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "raw"
            kline_dir = self._write_synthetic_raw(raw)
            meta = raw / "_meta"
            meta.mkdir(parents=True)
            (meta / "CONFIG.json").write_text(json.dumps(self.catalog()))
            (meta / "SCHEMA.md").write_text("Binance spot BTCUSDT 1h schema")
            attempt, spec_path, _round_doc, _run_doc = self._specs(root / "results", RUNNER_PATH)

            old_config, old_schema = runner.CONFIG_PATH, runner.SCHEMA_PATH
            old_qlib = sys.modules.get("qlib")
            runner.CONFIG_PATH = meta / "CONFIG.json"
            runner.SCHEMA_PATH = meta / "SCHEMA.md"
            qlib = ModuleType("qlib")
            qlib.__version__ = "0.9.7"
            sys.modules["qlib"] = qlib
            try:
                result = runner.run(spec_path, attempt, RUNNER_PATH, kline_dir=kline_dir)
            finally:
                runner.CONFIG_PATH, runner.SCHEMA_PATH = old_config, old_schema
                if old_qlib is None:
                    sys.modules.pop("qlib", None)
                else:
                    sys.modules["qlib"] = old_qlib

            self.assertEqual(result["status"], "ARTIFACT_READY")
            self.assertEqual(result["case_evaluations_total"], 480)
            self.assertEqual(result["expected_case_evaluations"], 480)
            self.assertTrue(result["coverage_complete"], result.get("assertion_failures"))
            self.assertTrue(result["assertions_all_true"], result.get("assertion_failures"))
            self.assertEqual(result["cohort_count"], 1)
            self.assertEqual(json.loads((attempt / "state.json").read_text())["stage"], "ARTIFACT_READY")
            self.assertTrue((attempt / "artifacts" / "cohort_results.json").is_file())
            self.assertTrue((attempt / "artifacts" / "cohort_survivors.json").is_file())
            self.assertTrue((attempt / "artifacts" / "assertions.json").is_file())
            self.assertTrue((attempt / "artifacts" / "grid_historical.csv").is_file())
            self.assertFalse(any((attempt / name).exists() for name in ("DONE", "FAILED", "INCOMPLETE")))
            self.assertFalse((attempt.parents[1] / "verdict.json").exists())
            evidence = json.loads((attempt / "artifacts" / "local_data_evidence.json").read_text())
            self.assertEqual(evidence["local_universe"]["symbol"], "BTCUSDT")
            self.assertFalse(evidence["local_universe"]["source_exact_match"])
            self.assertEqual(evidence["missing_relevant_event_dates"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
