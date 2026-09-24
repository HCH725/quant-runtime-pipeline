#!/usr/bin/env python3
"""Contract checks for the read-only shadow end-to-end canvas (Phase 2: authoritative runtime feed).

Two layers:

  * static: the canvas identity, the fixed node/route topology, and the source-truth rule - the
    runtime fields may only come from the on-demand observation of the canonical runtime (one fixed
    read-only host-bridge action), never from the stale dashboard projection file;
  * behavioural: the assembler Code node is executed with stub source stdouts and the derived
    lifecycle node with stub snapshots (real `node`, no n8n), covering the four required lifecycle
    states, the counts projection, and that nothing routes from a card status or an older token.

Run: python3 n8n/test_shadow_workflow.py     (stdlib unittest; needs `node` for the code nodes)
"""
import base64
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
OBSERVATION_NODE = "Runtime Observation — canonical runtime evidence (read-only)"
ASSEMBLER_NODE = "Assemble shadow snapshot (read-only)"
LIFECYCLE_NODE = "Build lifecycle view (derived, read-only)"
SUMMARY_NODE = "Pipeline Counts Summary (derived, read-only)"
# Sentinel so a helper default never has to double as "no family at all".
UNSET = object()
SOURCES = {
    "scout": "Strategy Research — Hermes Scout cron state (read-only)",
    "pool": "Pool — alpha-strategy-research pool (read-only)",
    "intake": "Intake Review — canonical intake state (read-only)",
    "gate": "Preflight Gate — prerequisite evidence (read-only)",
    "observation": OBSERVATION_NODE,
    "parking": "Parking — private survivor repo metadata (read-only)",
}
# The projection file the runtime feed must not depend on any more, and the fields it carried.
FORBIDDEN_RUNTIME_SOURCES = ("dashboard.json", "quant-dashboard-data", "dashboard_meta")


class ShadowWorkflowContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))
        cls.nodes = cls.workflow["nodes"]
        cls.by_name = {node["name"]: node for node in cls.nodes}
        cls.build_code = cls.by_name[LIFECYCLE_NODE]["parameters"]["jsCode"]
        cls.assemble_code = cls.by_name[ASSEMBLER_NODE]["parameters"]["jsCode"]
        cls.node = shutil.which("node")

    # ------------------------------------------------------------------ static contracts
    def test_identity_and_source_refresh_lane_are_preserved(self):
        self.assertEqual(self.workflow["id"], "shadowQuantCp1")
        self.assertEqual(self.workflow["name"], "Quant Control Plane — End-to-End")
        self.assertTrue(self.workflow["active"])
        self.assertNotIn("credentials", self.workflow)
        self.assertEqual(
            [node["name"] for node in self.nodes[:10]],
            [
                "Manual Trigger — shadow validation",
                "Schedule — 5m observation",
                "Strategy Research — Hermes Scout cron state (read-only)",
                "Pool — alpha-strategy-research pool (read-only)",
                "Intake Review — canonical intake state (read-only)",
                "Preflight Gate — prerequisite evidence (read-only)",
                OBSERVATION_NODE,
                "Parking — private survivor repo metadata (read-only)",
                ASSEMBLER_NODE,
                "Emit snapshot (n8n shadow dir only)",
            ],
        )
        self.assertEqual(
            self.by_name["Schedule — 5m observation"]["parameters"]["rule"]["interval"],
            [{"field": "minutes", "minutesInterval": 5}],
        )
        self.assertIn("SOURCE / METRICS REFRESH", self.by_name["Sticky Note — SOURCE / METRICS REFRESH"]["parameters"]["content"])
        self.assertIn("not lifecycle activity", self.by_name["Sticky Note — SOURCE / METRICS REFRESH"]["parameters"]["content"])
        self.assertIn("exactly one lifecycle indicator", self.by_name["Sticky Note — CURRENT LIFECYCLE"]["parameters"]["content"])
        self.assertEqual(
            self.workflow["connections"][ASSEMBLER_NODE]["main"][0][0]["node"],
            LIFECYCLE_NODE,
        )

    def test_connection_names_reference_existing_nodes(self):
        node_names = set(self.by_name)
        connections = self.workflow["connections"]
        for source, outputs in connections.items():
            self.assertIn(source, node_names, f"unknown connection source: {source}")
            for branches in outputs.values():
                for branch in branches:
                    for connection in branch:
                        self.assertIn(
                            connection["node"],
                            node_names,
                            f"{source} targets unknown node: {connection['node']}",
                        )

    def test_single_existing_schedule_refreshes_every_five_minutes(self):
        schedules = [node for node in self.nodes if node["type"] == "n8n-nodes-base.scheduleTrigger"]
        self.assertEqual(len(schedules), 1)
        self.assertEqual(schedules[0]["id"], "f68608d5-ed85-4e26-99c5-8ea21c243b96")
        self.assertEqual(schedules[0]["name"], "Schedule — 5m observation")
        self.assertEqual(schedules[0]["parameters"]["rule"]["interval"],
                         [{"field": "minutes", "minutesInterval": 5}])

    def test_runtime_truth_never_comes_from_the_dashboard_projection(self):
        for node in self.nodes:
            serialized = json.dumps(node)
            for forbidden in FORBIDDEN_RUNTIME_SOURCES:
                self.assertNotIn(forbidden, serialized, node["name"])
        # The runtime source node is the fixed read-only observation action, correlated by request_id,
        # and it reads no results path itself (the host action owns that read).
        command = self.by_name[OBSERVATION_NODE]["parameters"]["command"]
        self.assertIn('const ACTION="runtime_observe_once"', command)
        self.assertIn('const REQUEST=CONTROL+"/production_handoff.request.json"', command)
        self.assertIn('const RESPONSE=CONTROL+"/production_handoff.response.json"', command)
        self.assertIn("response.request_id===REQUEST_ID", command)
        self.assertIn('const OBSERVATION="quant-runtime-observation/v1"', command)
        self.assertNotIn("qlib-results", command)
        self.assertNotIn("/host/quant-dashboard-data", command)
        # The preflight-gate source no longer needs a family hint from a projection file either.
        gate_command = self.by_name["Preflight Gate — prerequisite evidence (read-only)"]["parameters"]["command"]
        self.assertNotIn("dashboard", gate_command)
        self.assertIn("records", gate_command)

    def test_assembler_consumes_the_observation_and_keeps_invocation_separate(self):
        self.assertIn("observation: '" + OBSERVATION_NODE + "'", self.assemble_code)
        self.assertIn("const envelope = parseSource(NODE.observation", self.assemble_code)
        self.assertIn("const observation = envelope && envelope.observation", self.assemble_code)
        self.assertIn("invocation", self.assemble_code)
        self.assertIn("runtime_observation_invocation", self.assemble_code)
        self.assertIn("'runtime_observe_once'", self.assemble_code)
        for forbidden in FORBIDDEN_RUNTIME_SOURCES:
            self.assertNotIn(forbidden, self.assemble_code)
        # the runtime counts come from the observation, not from any other source node
        self.assertIn("num(get(obsCounts, 'families_registered'))", self.assemble_code)
        self.assertIn("str(get(obsCurrent, 'state'))", self.assemble_code)

    def test_derived_nodes_have_no_control_surface(self):
        summary = self.by_name[SUMMARY_NODE]["parameters"]["jsCode"]
        for code in (self.build_code, summary, self.assemble_code):
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

    def test_no_kanban_status_can_drive_current(self):
        """Routing reads the observation's own state; no card/board field is a routing input."""
        lowered = self.build_code.lower()
        for forbidden in ("card_status", "kanban_task_id", "current_kanban_task_id", "current_card_id",
                          "board_status", "kanban list"):
            self.assertNotIn(forbidden, lowered)
        self.assertIn("counts.current_state", self.build_code)
        self.assertIn("runtime observation", lowered)

    def test_both_observation_triggers_feed_only_the_source_refresh_lane(self):
        source = "Strategy Research — Hermes Scout cron state (read-only)"
        for trigger in ("Manual Trigger — shadow validation", "Schedule — 5m observation"):
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
            {item["node"] for item in self.workflow["connections"][LIFECYCLE_NODE]["main"][0]},
            {SUMMARY_NODE, "Current Stage Router", "Emit snapshot (n8n shadow dir only)"},
        )

    # ------------------------------------------------------------------ fixture helpers
    def _observation_body(self, state="qlib_active", stage=UNSET, verdict=None,
                          family_id=UNSET, why="fixture runtime reason", gaps=None):
        stage = "RUNNING_QLIB" if stage is UNSET else stage
        family_id = "family-x" if family_id is UNSET else family_id
        return {
            "schema": "quant-runtime-observation/v1",
            "generated_at_utc": "2026-09-24T00:00:00Z",
            "read_only": True,
            "scope_note": "On-demand projection of the canonical quant runtime for the n8n control plane.",
            "results_root": "/fixture/results",
            "results_root_readable": True,
            "current": {"state": state, "family_id": family_id, "round_id": "family-x-r1",
                        "attempt": "run1", "attempt_path": "/fixture/results/family-x/rounds/family-x-r1/attempts/run1",
                        "stage": stage, "verdict": verdict,
                        "progress": {"available": True, "pct": 42.0, "done": 420, "total": 1000,
                                     "text": "42.0% (420 / 1,000)"},
                        "cohort": "BTCUSDT / 1h", "last_activity_utc": "2026-09-24T00:00:00Z",
                        "age_minutes": 1.0,
                        "agent": {"prompt_frozen": True, "agent_log": None, "agent_log_bytes": None,
                                  "agent_log_last_write_utc": "2026-09-24T00:00:00Z"},
                        "why": why},
            "funnel": {"workload": {"available": True, "evaluations": 12, "grid_artifacts": 2},
                       "wiki_brain": {"reviewed": 10, "ingested": 5},
                       "backtested": {"families": 2, "registered": 6}},
            "counts": {"families_registered": 6, "families_backtested": 2, "families_in_flight": 1,
                       "families_unresolved": 0, "workload_evaluations": 12,
                       "workload_grid_artifacts": 2, "survivors": 1, "leaderboard_count": 1,
                       "leaderboard_shown": 1, "candidates_pool_total": 4, "candidates_consumed": 2,
                       "candidates_queued": 2, "last_pipeline_advance_utc": "2026-09-24T01:00:00Z",
                       "last_pipeline_advance_family_id": "family-x"},
            "pool": {"available": True, "rule": "consumed = pool entry with a registered family.json"},
            "leaderboard": {"available": True, "count": 1, "shown": 1, "top_n": 10,
                            "entries": [{"rank": 1, "cohort": "BTCUSDT / 1h", "sharpe": 1.5,
                                         "annualized_return": 0.2, "max_dd_pct": 0.1,
                                         "evidence_state": "PAPER"}]},
            "health": {"available": True, "status": "ok", "active": [], "active_count": 0},
            "sources": [{"id": "runtime_results_root", "readable": True}],
            "gaps": gaps or [],
        }

    def _envelope(self, body=None, status="ok", gap=None):
        return json.dumps({
            "schema": "quant-control-plane-runtime-observation-source/v1",
            "action": "runtime_observe_once", "request_id": "n8n-fixture",
            "invocation": {"status": status, "exit_code": 0 if status == "ok" else None,
                           "started_at_utc": "2026-09-24T00:00:00Z",
                           "finished_at_utc": "2026-09-24T00:00:01Z"},
            "family_id": (body or {}).get("current", {}).get("family_id"),
            "observation": body, "gap": gap})

    def _sources(self, observation=None, **overrides):
        payloads = {
            SOURCES["scout"]: json.dumps({"job": {"job_id": "f5c0648122f3", "name": "Quant Research Scout",
                                                  "enabled": True, "state": "active",
                                                  "schedule_display": "0 */2 * * *",
                                                  "last_run_at": "2026-09-24T00:00:00Z",
                                                  "last_status": "ok", "failure_streak": 0}}),
            SOURCES["pool"]: json.dumps({"records": 8, "root_md_total": 9, "checkout_head_sha": "a" * 40}),
            SOURCES["intake"]: json.dumps({"counts": {"pass": 1, "pass_with_caveat": 2, "remediate": 3,
                                                      "reject": 4, "pending_ingestion": 0,
                                                      "deferred_delta": 2, "ingested_wiki_records": 5},
                                           "path": "/host/workspace-ro/alpha-strategy-review-state.json",
                                           "readable": True, "bytes": 10, "sha256": "b" * 64,
                                           "last_reviewed_at": "2026-09-24T00:00:00Z"}),
            SOURCES["gate"]: json.dumps({"dir": "/fixture/evidence", "readable": True, "record_count": 2,
                                         "records": [
                                             {"file": "other-prerequisite-gate-20260901.json",
                                              "family_id": "other-family", "conclusion": "PASS"},
                                             {"file": "family-x-prerequisite-gate-20260924.json",
                                              "family_id": "family-x", "path": "/fixture/evidence/family-x-prerequisite-gate-20260924.json",
                                              "bytes": 100, "sha256": "c" * 64, "schema_version": 1,
                                              "document_kind": "prerequisite-gate",
                                              "conclusion": "RUNNING_QLIB: data ready",
                                              "created_at_utc": "2026-09-24T00:00:00Z",
                                              "performance_claimable": None, "required_data_available": True,
                                              "attempts_launched": 1, "terminal_sentinels": 0,
                                              "decisive_absences": 0}]}),
            SOURCES["observation"]: self._envelope(observation or self._observation_body()),
            SOURCES["parking"]: json.dumps({"repo_present": True, "survivor_dirs": 1, "leaderboard_present": True,
                                            "leaderboard_bytes": 100, "leaderboard_sha256": "d" * 64,
                                            "mirror_head_sha": "e" * 40,
                                            "meta": {"survivor_count": 1, "top10_count": 1,
                                                     "generated_at_utc": "2026-09-24T00:00:00Z",
                                                     "contract": "validated-survivor-research"}}),
        }
        payloads.update(overrides)
        return payloads

    def _assemble(self, sources, include_binary=False):
        if self.node is None:
            self.skipTest("node is unavailable")
        wrapper = (
            "(async function(){const sources=" + json.dumps(sources, ensure_ascii=False) + ";"
            "globalThis.$=(name)=>{if(!(name in sources))throw new Error('unknown node '+name);"
            "return {first:()=>({json:{stdout:sources[name]}})};};"
            + self.assemble_code
            + "}).call({helpers:{prepareBinaryData:async(buffer,fileName,mimeType)=>"
              "({data:{data:Buffer.from(buffer).toString('base64'),fileName:fileName,mimeType:mimeType}})}})"
              ".then((value)=>process.stdout.write(JSON.stringify(value)));"
        )
        result = subprocess.run([self.node, "-e", wrapper], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        item = json.loads(result.stdout)[0]
        return item if include_binary else item["json"]

    def _fixture(self, state, stage=None, verdict=None, family_id=UNSET, gate=None, why=None):
        family_id = "family-x" if family_id is UNSET else family_id
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
            "families_in_flight": 1,
            "families_unresolved": 0,
            "workload_evaluations": 12,
            "candidates_pool_total": 4,
            "candidates_consumed": 2,
            "candidates_queued": 2,
            "last_pipeline_advance_utc": "2026-09-24T01:00:00Z",
            "leaderboard_count": 1,
            "leaderboard_shown": 1,
            "parking_survivor_count": 1,
            "parking_top10_count": 1,
            "current_family_id": family_id,
            "current_state": state,
            "current_round_id": "family-x-r1",
            "current_attempt": "run1",
            "current_stage": stage,
            "current_verdict": verdict,
            "current_progress_pct": 42,
            "current_cohort": "BTCUSDT / 1h",
            "current_agent_log_at_utc": "2026-09-24T00:00:00Z",
            "current_why": why or "fixture runtime reason",
            "runtime_health_status": "ok",
            "runtime_health_active_count": 0,
            "gate_record_count": 1,
            "gate_record_matched_current_family": bool(gate),
        }
        topology = [
            {
                "stage": "qlib_full_backtest",
                "shadow_state": stage if stage in ("RUNNING_QLIB", "ARTIFACT_READY", "FAILED_SCRIPT") else None,
                "state_provenance": "verbatim runtime stage from the on-demand runtime observation",
                "observation": {"current_state": state, "stage": stage, "progress_text": "42.0% (420 / 1,000)",
                                "cohort": "BTCUSDT / 1h", "note": why or "fixture runtime reason"},
            },
            {
                "stage": "result_verdict",
                "shadow_state": None,
                "state_provenance": None,
                "observation": {"active_incidents": []},
            },
        ]
        if gate:
            topology.append({
                "stage": "data_preflight_gate",
                "shadow_state": gate[0],
                "state_provenance": "verbatim token inside the matched gate record",
                "observation": {"family_id": gate[1]},
            })
        return {
            "schema": "quant-control-plane-shadow/v1",
            "generated_at_utc": "2026-09-24T00:00:00Z",
            "counts": counts,
            "topology": topology,
        }

    def _build(self, snapshot, binary=None, include_binary=False):
        if self.node is None:
            self.skipTest("node is unavailable")
        payload = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
        binary_payload = json.dumps(binary, ensure_ascii=False, separators=(",", ":")) if binary is not None else "undefined"
        wrapper = (
            "(async function(){const $input={first:()=>({json:"
            + payload
            + ",binary:"
            + binary_payload
            + "})};"
            + self.build_code
            + "}).call({helpers:{prepareBinaryData:async(buffer,fileName,mimeType)=>"
              "({data:buffer.toString('base64'),fileName:fileName,mimeType:mimeType})}})"
              ".then((value)=>process.stdout.write(JSON.stringify(value)))"
        )
        result = subprocess.run([self.node, "-e", wrapper], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        item = json.loads(result.stdout)[0]
        return item if include_binary else item["json"]

    # ------------------------------------------------------------------ assembler behaviour
    def test_assembler_projects_the_observation_into_the_snapshot(self):
        snapshot = self._assemble(self._sources())
        self.assertEqual(snapshot["schema"], "quant-control-plane-shadow/v1")
        self.assertEqual(snapshot["mode"], "SHADOW_READ_ONLY")
        self.assertFalse(snapshot["control_plane"]["mutations_enabled"])
        self.assertEqual(snapshot["control_plane"]["observation_requests"]["action"], "runtime_observe_once")
        counts = snapshot["counts"]
        self.assertEqual(counts["current_state"], "qlib_active")
        self.assertEqual(counts["current_stage"], "RUNNING_QLIB")
        self.assertEqual(counts["current_family_id"], "family-x")
        self.assertEqual(counts["current_progress_pct"], 42.0)
        self.assertEqual(counts["current_cohort"], "BTCUSDT / 1h")
        self.assertEqual(counts["families_registered"], 6)
        self.assertEqual(counts["families_backtested"], 2)
        self.assertEqual(counts["workload_evaluations"], 12)
        self.assertEqual(counts["candidates_pool_total"], 4)
        self.assertEqual(counts["candidates_consumed"], 2)
        self.assertEqual(counts["candidates_queued"], 2)
        self.assertEqual(counts["last_pipeline_advance_utc"], "2026-09-24T01:00:00Z")
        self.assertEqual(counts["last_pipeline_advance_family_id"], "family-x")
        self.assertEqual(counts["leaderboard_count"], 1)
        self.assertEqual(counts["runtime_health_status"], "ok")
        # the gate record is adopted because the OBSERVATION named this family, never a file hint
        self.assertTrue(counts["gate_record_matched_current_family"])
        gate = next(stage for stage in snapshot["topology"] if stage["stage"] == "data_preflight_gate")
        self.assertEqual(gate["observation"]["family_id"], "family-x")
        self.assertEqual(gate["observation"]["shadow_state"], "RUNNING_QLIB")
        self.assertEqual(gate["shadow_state"], "RUNNING_QLIB")
        runtime = next(stage for stage in snapshot["topology"] if stage["stage"] == "qlib_full_backtest")
        self.assertEqual(runtime["observation"]["current_state"], "qlib_active")
        self.assertEqual(runtime["observation"]["progress_text"], "42.0% (420 / 1,000)")
        self.assertEqual(snapshot["gaps"], [])
        source = next(item for item in snapshot["sources"] if item["id"] == "runtime_observation")
        self.assertTrue(source["readable"])
        self.assertEqual(source["invocation_status"], "ok")
        self.assertEqual(source["request_id"], "n8n-fixture")
        self.assertNotIn("dashboard", json.dumps(snapshot))

    def test_assembler_reports_a_failed_invocation_as_a_gap_not_a_state(self):
        envelope = self._envelope(body=None, status="response_timeout",
                                  gap="no correlated response within 180000 ms")
        snapshot = self._assemble(self._sources(**{SOURCES["observation"]: envelope}))
        self.assertIsNone(snapshot["counts"]["current_state"])
        self.assertIsNone(snapshot["counts"]["families_registered"])
        fields = [gap["field"] for gap in snapshot["gaps"]]
        self.assertIn("runtime_observation", fields)
        self.assertIn("runtime_observation_invocation", fields)
        source = next(item for item in snapshot["sources"] if item["id"] == "runtime_observation")
        self.assertFalse(source["readable"])
        self.assertEqual(source["invocation_status"], "response_timeout")

    def test_assembler_surfaces_the_observations_own_gaps(self):
        body = self._observation_body(gaps=[{"field": "candidate_pool", "reason": "pool unreadable"}])
        snapshot = self._assemble(self._sources(observation=body))
        self.assertIn("runtime_observation.candidate_pool", [gap["field"] for gap in snapshot["gaps"]])
        self.assertEqual(snapshot["counts"]["current_state"], "qlib_active")

    def test_assembler_never_invents_a_gate_record_for_an_unknown_family(self):
        body = self._observation_body(state="idle", stage=None, family_id=None,
                                      why="nothing owes the pipeline runtime work")
        snapshot = self._assemble(self._sources(observation=body))
        self.assertIsNone(snapshot["counts"]["gate_record_matched_current_family"])
        gate = next(stage for stage in snapshot["topology"] if stage["stage"] == "data_preflight_gate")
        self.assertEqual(gate["observation"], {"note": "no prerequisite-gate record matched the current family"})
        self.assertIsNone(gate["shadow_state"])

    # ------------------------------------------------------------------ lifecycle routing
    def test_lifecycle_routes_from_the_observation_state(self):
        cases = [
            ("preflight", None, None, "family-x", "data_readiness_preflight"),
            ("qlib_active", "RUNNING_QLIB", None, "family-x", "qlib_full_backtest"),
            ("disposition", "ARTIFACT_READY", None, "family-x", "result_verdict"),
            ("disposition", "FAILED_SCRIPT", None, "family-x", "failure_analysis_result_validation"),
            ("terminal", "ARTIFACT_READY", "PASS", "family-x", "survivor_pass"),
            ("terminal", "ARTIFACT_READY", "REJECT", "family-x", "reject"),
            ("terminal", "ARTIFACT_READY", "TECHNICAL_INCOMPLETE", "family-x", "result_verdict"),
            ("idle", None, None, None, "candidate_queue"),
        ]
        for state, stage, verdict, family_id, stage_key in cases:
            with self.subTest(state=state, stage=stage, verdict=verdict):
                snapshot = self._fixture(state, stage, verdict, family_id=family_id)
                result = self._build(snapshot)
                lifecycle = result["lifecycle_view"]
                self.assertEqual(lifecycle["current_stage"]["key"], stage_key)
                self.assertTrue(lifecycle["exact_one_current_stage"])
                self.assertEqual(sum(item["active"] for item in lifecycle["stage_summaries"].values()), 1)
                self.assertEqual(result["pipeline_counts_summary"]["current_stage"], stage_key)
                self.assertEqual(result["pipeline_counts_summary"]["current_lifecycle_state"], state)
                self.assertIn("runtime observation", lifecycle["current_stage"]["provenance"])
                self.assertEqual(result["pipeline_counts_summary"]["current_family_id"], family_id)

    def test_idle_routes_to_the_queue_instead_of_an_attention_alarm(self):
        result = self._build(self._fixture("idle", family_id=None,
                                           why="no family holds a live attempt"))
        current = result["lifecycle_view"]["current_stage"]
        self.assertEqual(current["key"], "candidate_queue")
        self.assertIsNone(current["source_token"])
        self.assertIn("the runtime is idle", current["reason"])
        self.assertIsNone(current["context"]["current_family_id"])
        self.assertEqual(current["context"]["lifecycle_state"], "idle")

    def test_canonical_incident_routes_attention_with_identity_even_if_current_is_idle(self):
        for state, family_id, stage in (("idle", None, None),
                                        ("preflight", "family-x", None)):
            with self.subTest(state=state):
                body = self._observation_body(state=state, stage=stage, family_id=family_id)
                body["health"].update({
                    "status": "attention", "active_count": 1,
                    "active": [{"incident_id": "inc-184f01fd0e16db99", "kind": "sentinel_ambiguous",
                                "family_id": "family-incident", "why": "attempt run1 carries no clean terminal",
                                "label": "inc-184f01fd0e16db99 sentinel_ambiguous family-incident",
                                "source": "production_handoff.unresolved_incidents"}],
                })
                snapshot = self._assemble(self._sources(observation=body))
                self.assertEqual(snapshot["counts"]["current_state"], state)
                result = self._build(snapshot)
                current = result["lifecycle_view"]["current_stage"]
                self.assertEqual(current["key"], "attention_unresolved")
                self.assertTrue(current["available"])
                self.assertEqual(current["source_field"], "observation.health.active")
                for value in ("inc-184f01fd0e16db99", "sentinel_ambiguous", "family-incident",
                              "attempt run1 carries no clean terminal"):
                    self.assertIn(value, current["reason"])
                incident = current["context"]["active_incidents"][0]
                self.assertEqual(incident["incident_id"], "inc-184f01fd0e16db99")
                self.assertEqual(incident["family_id"], "family-incident")
                self.assertEqual(result["pipeline_counts_summary"]["current_lifecycle_state"], state)
                self.assertEqual(result["pipeline_counts_summary"]["runtime_health"]["active_count"], 1)

    def test_zero_canonical_incidents_keeps_normal_routing(self):
        for state, stage, family_id, expected in (("idle", None, None, "candidate_queue"),
                                                  ("qlib_active", "RUNNING_QLIB", "family-x",
                                                   "qlib_full_backtest")):
            with self.subTest(state=state):
                body = self._observation_body(state=state, stage=stage, family_id=family_id)
                if state == "idle":
                    body["health"].update({"status": "attention", "active_count": 1,
                                           "active": [{"kind": "terminal_pending",
                                                       "label": "watchdog alert",
                                                       "first_seen_utc": "2026-09-24T00:00:00Z"}]})
                snapshot = self._assemble(self._sources(observation=body))
                current = self._build(snapshot)["lifecycle_view"]["current_stage"]
                self.assertEqual(current["key"], expected)
                if state == "idle":
                    self.assertEqual(snapshot["topology"][7]["observation"]["active_incidents"],
                                     [{"kind": "terminal_pending", "label": "watchdog alert",
                                       "first_seen_utc": "2026-09-24T00:00:00Z"}])

    def test_unprovable_state_routes_attention_without_guessing(self):
        cases = [
            ("no state at all", self._fixture(None, "RUNNING_QLIB")),
            ("unknown state", self._fixture("warming_up", "RUNNING_QLIB")),
            ("state without a family", self._fixture("qlib_active", "RUNNING_QLIB", family_id=None)),
            ("unrecognised stage token", self._fixture("qlib_active", "BASELINE")),
        ]
        for label, snapshot in cases:
            with self.subTest(case=label):
                result = self._build(snapshot)
                current = result["lifecycle_view"]["current_stage"]
                self.assertEqual(current["key"], "attention_unresolved")
                self.assertFalse(current["available"])
                self.assertTrue(result["lifecycle_view"]["exact_one_current_stage"])

    def test_global_survivor_pass_never_drives_current(self):
        snapshot = self._fixture("qlib_active", "RUNNING_QLIB")
        snapshot["topology"].append({
            "stage": "survivor",
            "shadow_state": "PASS",
            "state_provenance": "historical leaderboard inventory",
            "observation": {"leaderboard_count": 29},
        })
        result = self._build(snapshot)
        self.assertEqual(result["lifecycle_view"]["current_stage"]["key"], "qlib_full_backtest")
        self.assertEqual(sum(item["active"] for item in result["lifecycle_view"]["stage_summaries"].values()), 1)

    def test_gate_evidence_only_corroborates_the_runtime_state(self):
        agreeing = self._build(self._fixture("qlib_active", "RUNNING_QLIB", gate=("RUNNING_QLIB", "family-x")))
        corroboration = agreeing["pipeline_counts_summary"]["corroboration"]
        self.assertTrue(corroboration["gate_record_matched"])
        self.assertTrue(corroboration["agrees"])
        self.assertEqual(agreeing["lifecycle_view"]["current_stage"]["key"], "qlib_full_backtest")

        disagreeing = self._build(self._fixture("qlib_active", "RUNNING_QLIB",
                                                gate=("TECHNICAL_INCOMPLETE", "family-x")))
        corroboration = disagreeing["pipeline_counts_summary"]["corroboration"]
        self.assertTrue(corroboration["gate_record_matched"])
        self.assertFalse(corroboration["agrees"])
        self.assertEqual(disagreeing["lifecycle_view"]["current_stage"]["key"], "qlib_full_backtest")

        absent = self._build(self._fixture("qlib_active", "RUNNING_QLIB"))
        corroboration = absent["pipeline_counts_summary"]["corroboration"]
        self.assertFalse(corroboration["gate_record_matched"])
        self.assertIsNone(corroboration["agrees"])
        self.assertEqual(absent["lifecycle_view"]["current_stage"]["key"], "qlib_full_backtest")

    def test_waiting_state_is_unavailable_not_invented(self):
        result = self._build(self._fixture("qlib_active", "RUNNING_QLIB"))
        waiting = result["lifecycle_view"]["stage_summaries"]["waiting_data_ready_to_resume_parking"]
        self.assertIsNone(waiting["state"])
        self.assertFalse(waiting["available"])
        self.assertIn("exact token", waiting["reason"])
        self.assertNotIn("WAITING_DATA", [item["token"] for item in result["lifecycle_view"]["evidence"]])

    def test_counts_summary_carries_the_runtime_counts_and_provenance(self):
        result = self._build(self._fixture("terminal", "ARTIFACT_READY", "PASS"))
        summary = result["pipeline_counts_summary"]
        for key in ("families_registered", "families_backtested", "workload_evaluations",
                    "leaderboard_count", "candidates_pool_total", "candidates_consumed",
                    "candidates_queued", "last_pipeline_advance_utc"):
            self.assertIn(key, summary)
        self.assertEqual(summary["families_registered"], 6)
        self.assertEqual(summary["workload_evaluations"], 12)
        self.assertEqual(summary["last_pipeline_advance_utc"], "2026-09-24T01:00:00Z")
        self.assertEqual(summary["current_verdict"], "PASS")
        self.assertIn("runtime observation of the canonical runtime", summary["provenance"])
        self.assertNotIn("kanban", json.dumps(summary).lower())

    def test_derived_view_rebuilds_binary_from_post_lifecycle_snapshot(self):
        binary = {
            "data": {
                "data": "c2VudGluZWw=",
                "fileName": "quant-control-plane-shadow.json",
                "mimeType": "application/json",
            }
        }
        result = self._build(self._fixture("qlib_active", "RUNNING_QLIB"), binary, include_binary=True)
        emitted = json.loads(base64.b64decode(result["binary"]["data"]["data"]).decode("utf-8"))
        self.assertNotEqual(result["binary"], binary)
        self.assertEqual(emitted, result["json"])
        self.assertIn("lifecycle_view", emitted)
        self.assertIn("pipeline_counts_summary", emitted)

    def test_emit_snapshot_binary_contains_the_post_lifecycle_displayed_snapshot(self):
        assembled = self._assemble(self._sources(), include_binary=True)
        pre_lifecycle = assembled["json"]
        self.assertNotIn("lifecycle_view", pre_lifecycle)
        self.assertNotIn("pipeline_counts_summary", pre_lifecycle)

        result = self._build(pre_lifecycle, assembled["binary"], include_binary=True)
        emitted = json.loads(base64.b64decode(result["binary"]["data"]["data"]).decode("utf-8"))
        snapshot = result["json"]
        self.assertEqual(emitted, snapshot)
        self.assertNotEqual(emitted, pre_lifecycle)
        self.assertIn("lifecycle_view", emitted)
        self.assertIn("pipeline_counts_summary", emitted)
        self.assertEqual(emitted["pipeline_counts_summary"]["current_stage"],
                         emitted["lifecycle_view"]["current_stage"]["key"])
        self.assertEqual(emitted["pipeline_counts_summary"]["current_family_id"],
                         emitted["counts"]["current_family_id"])

        if self.node is None:
            self.skipTest("node is unavailable")
        summary_code = self.by_name[SUMMARY_NODE]["parameters"]["jsCode"]
        wrapper = ("(async()=>{const $input={first:()=>({json:" + json.dumps(snapshot) + "})};"
                   + summary_code
                   + "})().then((value)=>process.stdout.write(JSON.stringify(value)))")
        summary_result = subprocess.run([self.node, "-e", wrapper], capture_output=True, text=True)
        self.assertEqual(summary_result.returncode, 0, summary_result.stderr)
        displayed = json.loads(summary_result.stdout)[0]["json"]
        self.assertEqual(displayed["summary"], emitted["pipeline_counts_summary"])
        self.assertEqual(displayed["current_stage"], emitted["lifecycle_view"]["current_stage"])


if __name__ == "__main__":
    unittest.main()
