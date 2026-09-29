import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType

RUNNER_PATH = Path(__file__).resolve().parents[1] / "340_bitcoin_friday_post_event_drift_run.py"
spec = importlib.util.spec_from_file_location("bitcoin_friday_runner", RUNNER_PATH)
assert spec is not None and spec.loader is not None
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class BitcoinFridayPrerequisiteTests(unittest.TestCase):
    def test_binance_spot_catalog_is_not_treated_as_kraken_btc_usd(self):
        config = {
            "venue": "BINANCE",
            "market_type": "usdm_perp",
            "symbols": ["BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"],
            "intervals": ["1d", "15m", "1h", "4h"],
            "datasets": {
                "spot_klines": "Binance spot klines (BTCUSDT/ETHUSDT/BNBUSDT/SOLUSDT)",
                "klines": "Binance USD-M perpetual futures klines",
            },
        }
        report = runner.inspect_prerequisites(config, "Binance spot and USD-M schemas")
        self.assertEqual(report["catalog_summary"]["binance_spot_dataset_ids"], ["spot_klines"])
        self.assertEqual(report["catalog_summary"]["kraken_dataset_ids"], [])
        self.assertEqual([item["status"] for item in report["checks"]], ["absent"] * 3)
        self.assertIn("Kraken BTC/USD", report["missing_prerequisites"][0])
        self.assertNotIn("BTCUSDT", report["catalog_summary"]["kraken_dataset_ids"])

    def test_declared_kraken_series_still_requires_date_coverage_evidence(self):
        config = {
            "venue": "KRAKEN",
            "market_type": "spot",
            "symbols": ["BTC/USD"],
            "intervals": ["1h"],
            "datasets": {"kraken_btcusd_spot_1h": "Kraken BTC/USD spot hourly closes"},
        }
        report = runner.inspect_prerequisites(config, "Kraken hourly spot schema")
        self.assertEqual(report["checks"][0]["status"], "coverage_unverified")
        self.assertEqual(report["checks"][1]["status"], "coverage_unverified")
        self.assertEqual(report["checks"][2]["status"], "absent")
        self.assertIn("does not prove", report["checks"][0]["evidence"]["reason"])

    def test_direct_identity_checks_fingerprint_script_and_ownership(self):
        fingerprint_input = "bitcoin-friday-prerequisite-test"
        fingerprint = "sha256:" + hashlib.sha256(fingerprint_input.encode()).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            attempt = Path(tmp) / runner.FAMILY_ID / "rounds" / "r1" / "attempts" / "u1"
            attempt.mkdir(parents=True)
            round_doc = {
                "family_id": runner.FAMILY_ID,
                "round_id": "r1",
                "semantic_fingerprint": fingerprint,
            }
            round_raw = (json.dumps(round_doc) + "\n").encode()
            (attempt.parents[1] / "round-spec.json").write_bytes(round_raw)
            run_doc = {
                "family_id": runner.FAMILY_ID,
                "round_id": "r1",
                "run_id": "u1",
                "fingerprint_input": fingerprint_input,
                "semantic_fingerprint": fingerprint,
                "round_spec_sha256": runner.sha256(round_raw),
                "script": {
                    "path": "/scripts/" + runner.RUNNER_NAME,
                    "sha256": runner.sha256(RUNNER_PATH.read_bytes()),
                },
            }
            runner.validate_identity(run_doc, round_doc, attempt, RUNNER_PATH)
            run_doc["task_id"] = "forbidden"
            with self.assertRaisesRegex(ValueError, "ownership fields"):
                runner.validate_identity(run_doc, round_doc, attempt, RUNNER_PATH)

    def test_run_emits_only_prerequisite_evidence_without_terminal_verdict(self):
        fingerprint_input = "bitcoin-friday-prerequisite-run-test"
        fingerprint = "sha256:" + hashlib.sha256(fingerprint_input.encode()).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            attempt = root / runner.FAMILY_ID / "rounds" / (runner.FAMILY_ID + "-r1") / "attempts" / (runner.FAMILY_ID + "-r1-u1")
            attempt.mkdir(parents=True)
            round_doc = {
                "schema_version": 1,
                "family_id": runner.FAMILY_ID,
                "round_id": attempt.parents[1].name,
                "semantic_fingerprint": fingerprint,
            }
            round_raw = (json.dumps(round_doc, indent=2) + "\n").encode()
            round_path = attempt.parents[1] / "round-spec.json"
            round_path.write_bytes(round_raw)
            run_doc = {
                "schema_version": 1,
                "family_id": runner.FAMILY_ID,
                "round_id": round_doc["round_id"],
                "run_id": attempt.name,
                "fingerprint_input": fingerprint_input,
                "semantic_fingerprint": fingerprint,
                "round_spec_sha256": runner.sha256(round_raw),
                "expected": {"expected_case_evaluations": 480},
                "script": {
                    "path": "/scripts/" + runner.RUNNER_NAME,
                    "sha256": runner.sha256(RUNNER_PATH.read_bytes()),
                },
            }
            spec_path = attempt / "run-spec.json"
            spec_path.write_text(json.dumps(run_doc, indent=2) + "\n", encoding="utf-8")
            raw_root = root / "raw"
            meta = raw_root / "_meta"
            meta.mkdir(parents=True)
            (meta / "CONFIG.json").write_text(json.dumps({
                "venue": "BINANCE",
                "market_type": "usdm_perp",
                "symbols": ["BTCUSDT"],
                "intervals": ["1h"],
                "datasets": {"spot_klines": "Binance spot BTCUSDT"},
            }), encoding="utf-8")
            (meta / "SCHEMA.md").write_text("Binance spot schema", encoding="utf-8")

            previous_config, previous_schema = runner.CONFIG_PATH, runner.SCHEMA_PATH
            previous_qlib = sys.modules.get("qlib")
            setattr(runner, "CONFIG_PATH", meta / "CONFIG.json")
            setattr(runner, "SCHEMA_PATH", meta / "SCHEMA.md")
            qlib_stub = ModuleType("qlib")
            setattr(qlib_stub, "__version__", "test-qlib")
            sys.modules["qlib"] = qlib_stub
            try:
                result = runner.run(spec_path, attempt, RUNNER_PATH)
            finally:
                setattr(runner, "CONFIG_PATH", previous_config)
                setattr(runner, "SCHEMA_PATH", previous_schema)
                if previous_qlib is None:
                    sys.modules.pop("qlib", None)
                else:
                    sys.modules["qlib"] = previous_qlib

            evidence = json.loads((attempt / "artifacts" / "prerequisite_evidence.json").read_text())
            self.assertEqual(result["disposition"], "TECHNICAL_INCOMPLETE")
            self.assertFalse(result["performance_claimable_recommendation"])
            self.assertEqual(result["coverage"]["cells_computed"], 0)
            self.assertEqual(evidence["catalog_summary"]["venue"], "BINANCE")
            self.assertTrue((attempt / "result.json").is_file())
            self.assertEqual(json.loads((attempt / "state.json").read_text())["stage"], "ARTIFACT_READY")
            self.assertFalse(any((attempt / name).exists() for name in ("DONE", "FAILED", "INCOMPLETE", "verdict.json")))


if __name__ == "__main__":
    unittest.main()
