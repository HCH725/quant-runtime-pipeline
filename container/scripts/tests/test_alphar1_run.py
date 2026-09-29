import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

RUNNER_PATH = Path(__file__).resolve().parents[1] / "330_alphar1_run.py"
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "runtime"))
spec = importlib.util.spec_from_file_location("alphar1_runner", RUNNER_PATH)
assert spec is not None and spec.loader is not None
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class AlphaR1PrerequisiteTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "venue": "BINANCE",
            "market_type": "usdm_perp",
            "symbols": ["BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"],
            "intervals": ["1d", "1w", "15m", "30m", "4h", "5m", "1h"],
            "datasets": {"klines": "Binance USD-M perpetual futures klines",
                         "funding": "Binance USD-M perpetual funding rates",
                         "fred": "FRED bounded macro pack"},
        }
        self.source_release = {
            "release_status": runner.MODEL_RELEASE_STATUS,
            "public_repo_commit": runner.SOURCE_REPO_COMMIT,
            "inference_code_available": False,
            "model_weights_available": False,
        }

    def test_reviewed_source_release_is_a_hard_prerequisite(self):
        report = runner.inspect_prerequisites(self.config, "OHLCV schema only", self.source_release)
        model = next(x for x in report["checks"]
                     if x["requirement"].startswith("Pinned Alpha-R1"))
        self.assertEqual(model["status"], "absent_in_reviewed_source")
        self.assertIn("Qwen3-8B", model["requirement"])
        self.assertEqual(report["missing_prerequisites"][-1],
                         "Exact Alpha-R1 preprocessing, news APIs, and prompt templates")

    def test_crypto_catalog_is_not_relabelled_as_equity_prerequisite_data(self):
        report = runner.inspect_prerequisites(self.config, "OHLCV schema only", self.source_release)
        by_name = {item["requirement"]: item for item in report["checks"]}
        self.assertEqual(by_name["CSI 300 / CSI 1000 point-in-time Chinese A-share universe"]["status"], "absent")
        self.assertEqual(by_name["A-share daily OHLCV plus 1-minute 09:31-10:00 VWAP inputs"]["status"], "absent")
        self.assertEqual(by_name["Decision-time financial news, corporate announcements, and Chinese macro context"]["status"], "absent")
        self.assertEqual(report["catalog_summary"]["market_type"], "usdm_perp")
        self.assertEqual(report["catalog_summary"]["intervals"], sorted(self.config["intervals"]))

    def test_released_model_flag_does_not_create_crypto_substitution(self):
        release = dict(self.source_release, release_status="released",
                       inference_code_available=True, model_weights_available=True)
        report = runner.inspect_prerequisites(self.config, "OHLCV schema only", release)
        self.assertEqual(report["catalog_summary"]["market_type"], "usdm_perp")
        self.assertEqual(report["checks"][0]["status"], "absent")

    def test_direct_identity_and_hashes_are_checked(self):
        fingerprint_input = "alpha-r1-test"
        fingerprint = "sha256:" + hashlib.sha256(fingerprint_input.encode()).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            family = Path(tmp) / runner.FAMILY_ID
            attempt = family / "rounds" / "r1" / "attempts" / "u1"
            attempt.mkdir(parents=True)
            round_doc = {"family_id": runner.FAMILY_ID, "round_id": "r1",
                         "semantic_fingerprint": fingerprint}
            round_raw = (json.dumps(round_doc) + "\n").encode()
            (attempt.parents[1] / "round-spec.json").write_bytes(round_raw)
            run_doc = {
                "family_id": runner.FAMILY_ID, "round_id": "r1", "run_id": "u1",
                "fingerprint_input": fingerprint_input, "semantic_fingerprint": fingerprint,
                "round_spec_sha256": runner.sha256(round_raw),
                "source_release": {"release_status": runner.MODEL_RELEASE_STATUS,
                                   "public_repo_commit": runner.SOURCE_REPO_COMMIT},
                "script": {"path": "/scripts/330_alphar1_run.py",
                           "sha256": runner.sha256(RUNNER_PATH.read_bytes())},
            }
            runner.validate_identity(run_doc, round_doc, attempt, RUNNER_PATH)
            run_doc["task_id"] = "t_forbidden"
            with self.assertRaisesRegex(ValueError, "ownership fields"):
                runner.validate_identity(run_doc, round_doc, attempt, RUNNER_PATH)


if __name__ == "__main__":
    unittest.main()
