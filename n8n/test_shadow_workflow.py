#!/usr/bin/env python3
"""Contract checks for the read-only shadow end-to-end canvas."""
import copy
import json
import shutil
import subprocess
import unittest
from pathlib import Path


WORKFLOW_PATH = Path(__file__).with_name("quant-control-plane-shadow.workflow.json")
STAGES = [
    ("strategy_research", "Strategy Research / Hermes Scout"),
    ("github_strategy_pool", "GitHub Strategy Pool"),
    ("intake_review", "Intake Review"),
    ("wiki_brain", "Wiki Brain"),
    ("candidate_queue", "Candidate Queue"),
    ("waiting_data_ready_to_resume_parking", "WAITING_DATA / READY_TO_RESUME Parking"),
    ("data_readiness_preflight", "Data Readiness / Preflight"),
    ("qlib_full_backtest", "Qlib Full Backtest (symbols x timeframes x parameter domain x DCA)"),
    ("historical_oos", "Historical / OOS"),
    ("robustness", "Robustness"),
    ("failure_analysis_result_validation", "Failure Analysis / Result Validation"),
    ("result_verdict", "Result / Verdict"),
    ("reject", "REJECT"),
    ("survivor_pass", "Survivor / PASS"),
    ("leaderboard", "Leaderboard"),
    ("private_repo_parking", "Private Survivor Repo Parking"),
    ("attention_unresolved", "Attention / Unresolved"),
]


class ShadowWorkflowContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))
        cls.nodes = cls.workflow["nodes"]
        cls.by_name = {node["name"]: node for node in cls.nodes}
        cls.build_code = cls.by_name["Build lifecycle view (derived, read-only)"]["parameters"]["jsCode"]
        cls.node = shutil.which("node")

    def test_identity_and_source_refresh_lane_are_preserved(self):
        self.assertEqual(self.workflow["id"], "shadowQuantCp1")
        self.assertEqual(self.workflow["name"], "Quant Control Plane — End-to-End")
        self.assertTrue(self.workflow["active"])
        self.assertNotIn("credentials", self.workflow)
        self.assertEqual(
            [node["name"] for node in self.nodes[:10]],
            [
                "Manual Trigger — shadow validation",
                "Schedule — 15m observation",
                "Strategy Research — Hermes Scout cron state (read-only)",
                "Pool — alpha-strategy-research pool (read-only)",
                "Intake Review — canonical intake state (read-only)",
                "Preflight Gate — prerequisite evidence (read-only)",
                "Candidate→Qlib→Leaderboard — dashboard projection (read-only)",
                "Parking — private survivor repo metadata (read-only)",
                "Assemble shadow snapshot (read-only)",
                "Emit snapshot (n8n shadow dir only)",
            ],
        )
        self.assertEqual(
            self.by_name["Schedule — 15m observation"]["parameters"]["rule"]["interval"],
            [{"field": "minutes", "minutesInterval": 15}],
        )
        self.assertIn("SOURCE / METRICS REFRESH", self.by_name["Sticky Note — SOURCE / METRICS REFRESH"]["parameters"]["content"])
        self.assertIn("not lifecycle activity", self.by_name["Sticky Note — SOURCE / METRICS REFRESH"]["parameters"]["content"])
        self.assertIn("exactly one lifecycle indicator", self.by_name["Sticky Note — CURRENT LIFECYCLE"]["parameters"]["content"])
        self.assertEqual(
            self.workflow["connections"]["Assemble shadow snapshot (read-only)"]["main"][0][0]["node"],
            "Build lifecycle view (derived, read-only)",
        )

    def test_both_observation_triggers_feed_only_the_source_refresh_lane(self):
        source = "Strategy Research — Hermes Scout cron state (read-only)"
        for trigger in ("Manual Trigger — shadow validation", "Schedule — 15m observation"):
            self.assertEqual(
                self.workflow["connections"][trigger]["main"][0],
                [{"node": source, "type": "main", "index": 0}],
            )

    def test_lifecycle_stage_set_order_and_router_outputs_are_fixed(self):
        stage_names = [f"Lifecycle {i:02d} — {label}" for i, (_, label) in enumerate(STAGES, 1)]
        self.assertEqual(
            [node["name"] for node in self.nodes if node["name"].startswith("Lifecycle ")],
            stage_names,
        )
        self.assertTrue(all(self.by_name[name]["type"] == "n8n-nodes-base.noOp" for name in stage_names))

        router = self.by_name["Current Stage Router"]
        self.assertEqual(router["type"], "n8n-nodes-base.switch")
        stage_one = self.by_name[stage_names[0]]
        self.assertGreaterEqual(stage_one["position"][0] - router["position"][0], 300)
        rules = router["parameters"]["rules"]["values"]
        self.assertEqual(
            [rule["conditions"]["conditions"][0]["rightValue"] for rule in rules],
            [key for key, _ in STAGES],
        )
        self.assertEqual(
            [rule["outputKey"] for rule in rules],
            [label for _, label in STAGES],
        )
        routed = self.workflow["connections"]["Current Stage Router"]["main"]
        self.assertEqual(len(routed), len(STAGES))
        self.assertEqual([branch[0]["node"] for branch in routed], stage_names)
        self.assertEqual(
            {item["node"] for item in self.workflow["connections"]["Build lifecycle view (derived, read-only)"]["main"][0]},
            {
                "Pipeline Counts Summary (derived, read-only)",
                "Current Stage Router",
                "Emit snapshot (n8n shadow dir only)",
            },
        )

    def test_derived_nodes_have_no_control_surface(self):
        summary = self.by_name["Pipeline Counts Summary (derived, read-only)"]["parameters"]["jsCode"]
        for code in (self.build_code, summary):
            lowered = code.lower()
            for forbidden in ("require(", "child_process", "fetch(", "http://", "https://", "execsync", "writefilesync"):
                self.assertNotIn(forbidden, lowered)
        for required in (
            "quant-control-plane-lifecycle-view/v1",
            "pipeline_counts_summary",
            "exact_one_current_stage",
            "RUNNING_QLIB",
            "ARTIFACT_READY",
            "FAILED_SCRIPT",
            "WAITING_DATA",
            "READY_TO_RESUME",
            "Attention / Unresolved",
        ):
            self.assertIn(required, self.build_code)

    def _fixture(self, current_stage):
        counts = {
            "pool_records_total": 8,
            "pool_root_md_total": 9,
            "intake_pass": 1,
            "intake_pass_with_caveat": 2,
            "intake_remediate": 3,
            "intake_reject": 4,
            "wiki_reviewed": 10,
            "wiki_ingested": 5,
            "families_registered": 6,
            "families_backtested": 2,
            "workload_evaluations": 12,
            "leaderboard_count": 1,
            "leaderboard_shown": 1,
            "parking_survivor_count": 1,
            "parking_top10_count": 1,
            "current_family_id": "family-x",
            "current_kanban_task_id": "t_x",
            "current_card_status": "blocked" if current_stage == "ambiguous" else "running",
            "current_stage": "not launched" if current_stage == "ambiguous" else current_stage,
            "current_progress_pct": 42,
            "runtime_health_status": "attention" if current_stage == "ambiguous" else "ok",
            "runtime_health_active_count": 1 if current_stage == "ambiguous" else 0,
            "gate_record_count": 1,
            "gate_record_matched_current_family": False if current_stage == "ambiguous" else True,
        }
        incident = {"kind": "terminal_pending", "label": "pending terminal", "first_seen_utc": "2026-09-23T00:00:00Z"}
        return {
            "schema": "quant-control-plane-shadow/v1",
            "generated_at_utc": "2026-09-23T00:00:00Z",
            "counts": counts,
            "topology": [
                {
                    "stage": "qlib_full_backtest",
                    "shadow_state": None if current_stage == "ambiguous" else current_stage,
                    "state_provenance": "fixture runtime token",
                    "observation": {"progress_text": "42%"},
                },
                {
                    "stage": "result_verdict",
                    "shadow_state": None,
                    "state_provenance": None,
                    "observation": {"active_incidents": [incident] if current_stage == "ambiguous" else []},
                },
            ],
        }

    def _build(self, snapshot):
        if self.node is None:
            self.skipTest("node is unavailable")
        payload = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
        wrapper = (
            "(async()=>{const $input={first:()=>({json:"
            + payload
            + "})};"
            + self.build_code
            + "})().then((value)=>process.stdout.write(JSON.stringify(value)))"
        )
        result = subprocess.run([self.node, "-e", wrapper], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)[0]["json"]

    def test_current_stage_mapping_routes_exactly_one_indicator(self):
        expected = {
            "RUNNING_QLIB": "qlib_full_backtest",
            "ARTIFACT_READY": "result_verdict",
            "FAILED_SCRIPT": "failure_analysis_result_validation",
            "PASS": "survivor_pass",
            "REJECT": "reject",
            "WAITING_DATA": "waiting_data_ready_to_resume_parking",
            "READY_TO_RESUME": "waiting_data_ready_to_resume_parking",
            "TECHNICAL_INCOMPLETE": "result_verdict",
        }
        for token, stage_key in expected.items():
            with self.subTest(token=token):
                result = self._build(self._fixture(token))
                lifecycle = result["lifecycle_view"]
                self.assertEqual(lifecycle["current_stage"]["key"], stage_key)
                self.assertTrue(lifecycle["exact_one_current_stage"])
                self.assertEqual(sum(item["active"] for item in lifecycle["stage_summaries"].values()), 1)
                self.assertEqual(result["pipeline_counts_summary"]["current_stage"], stage_key)

    def test_global_survivor_pass_never_drives_current_family_route(self):
        snapshot = self._fixture("RUNNING_QLIB")
        snapshot["topology"].append({
            "stage": "survivor",
            "shadow_state": "PASS",
            "state_provenance": "historical leaderboard inventory",
            "observation": {"leaderboard_count": 29},
        })
        result = self._build(snapshot)
        self.assertEqual(result["lifecycle_view"]["current_stage"]["key"], "qlib_full_backtest")
        self.assertEqual(sum(item["active"] for item in result["lifecycle_view"]["stage_summaries"].values()), 1)

        ambiguous = self._fixture("ambiguous")
        ambiguous["topology"].append({
            "stage": "survivor",
            "shadow_state": "PASS",
            "state_provenance": "historical leaderboard inventory",
            "observation": {"leaderboard_count": 29},
        })
        result = self._build(ambiguous)
        self.assertEqual(result["lifecycle_view"]["current_stage"]["key"], "attention_unresolved")

    def test_ambiguous_blocked_snapshot_routes_attention_with_context(self):
        result = self._build(self._fixture("ambiguous"))
        current = result["lifecycle_view"]["current_stage"]
        self.assertEqual(current["key"], "attention_unresolved")
        self.assertEqual(current["context"], {
            "current_family_id": "family-x",
            "kanban_task_id": "t_x",
            "card_status": "blocked",
            "stage": "not launched",
            "runtime_health_status": "attention",
            "health_incident_note": "terminal_pending",
            "gate_matched": False,
        })
        self.assertTrue(result["lifecycle_view"]["exact_one_current_stage"])

    def test_missing_waiting_state_is_unavailable_not_invented(self):
        snapshot = self._fixture("ambiguous")
        snapshot["counts"]["current_stage"] = "not launched"
        snapshot["topology"][0]["shadow_state"] = None
        snapshot["topology"][1]["observation"]["active_incidents"] = []
        result = self._build(snapshot)
        lifecycle = result["lifecycle_view"]
        self.assertEqual(lifecycle["current_stage"]["key"], "attention_unresolved")
        waiting = lifecycle["stage_summaries"]["waiting_data_ready_to_resume_parking"]
        self.assertIsNone(waiting["state"])
        self.assertFalse(waiting["available"])
        self.assertIn("exact token", waiting["reason"])
        self.assertIsNone(result["pipeline_counts_summary"]["current_stage_token"])


if __name__ == "__main__":
    unittest.main()
