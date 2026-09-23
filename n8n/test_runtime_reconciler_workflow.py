#!/usr/bin/env python3
"""Contract checks for the C4 runtime-reconciler n8n workflow."""
import hashlib
import json
import re
import unittest
from pathlib import Path


WORKFLOW_PATH = Path(__file__).with_name(
    "quant-control-plane-runtime-reconciler.workflow.json"
)
PRODUCTION_WORKFLOW_PATH = Path(__file__).with_name(
    "quant-control-plane-production-handoff-manual.workflow.json"
)
PRODUCTION_WORKFLOW_SHA256 = (
    "7713da6d2e2ba726eddfdb9610904263315b006e99c0f9cdc4744c718fe5ad02"
)


class RuntimeReconcilerWorkflowContract(unittest.TestCase):
    ACTION_NODE = "Invoke runtime_reconcile_once through host bridge"
    MANUAL_TRIGGER = "Manual Trigger — runtime reconciler"
    SCHEDULE_TRIGGER = "Schedule — runtime reconciler :06/:21/:36/:51"

    @classmethod
    def setUpClass(cls):
        cls.workflow = json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))
        cls.nodes = cls.workflow["nodes"]
        cls.by_name = {node["name"]: node for node in cls.nodes}
        cls.command = cls.by_name[cls.ACTION_NODE]["parameters"]["command"]

    def test_stable_minimal_topology_and_cadence(self):
        self.assertEqual(self.workflow["id"], "runtimeReconcilerC4")
        self.assertEqual(
            self.workflow["name"], "Quant Control Plane — Runtime Reconciler"
        )
        self.assertFalse(self.workflow["active"])
        self.assertNotIn("credentials", self.workflow)
        self.assertEqual(
            [node["name"] for node in self.nodes],
            [self.MANUAL_TRIGGER, self.SCHEDULE_TRIGGER, self.ACTION_NODE],
        )
        self.assertEqual(
            [node["type"] for node in self.nodes],
            [
                "n8n-nodes-base.manualTrigger",
                "n8n-nodes-base.scheduleTrigger",
                "n8n-nodes-base.executeCommand",
            ],
        )
        schedule = self.by_name[self.SCHEDULE_TRIGGER]
        self.assertEqual(
            schedule["parameters"]["rule"]["interval"],
            [{"field": "cronExpression", "expression": "6,21,36,51 * * * *"}],
        )
        self.assertEqual(
            set(self.workflow["connections"]),
            {self.MANUAL_TRIGGER, self.SCHEDULE_TRIGGER},
        )
        for trigger in (self.MANUAL_TRIGGER, self.SCHEDULE_TRIGGER):
            self.assertEqual(
                self.workflow["connections"][trigger]["main"][0][0],
                {"node": self.ACTION_NODE, "type": "main", "index": 0},
            )

    def test_request_response_contract_is_fixed_correlated_and_bounded(self):
        command = self.command
        for required in (
            'CONTROL="/home/node/.n8n-files/control"',
            'REQUEST=CONTROL+"/production_handoff.request.json"',
            'RESPONSE=CONTROL+"/production_handoff.response.json"',
            'SCHEMA="quant-control-action/v1"',
            'RESPONSE_SCHEMA="quant-control-action-response/v1"',
            'ACTION="runtime_reconcile_once"',
            'crypto.randomUUID()',
            'fs.fsyncSync(fd)',
            'fs.linkSync(tmp,REQUEST)',
            'fs.unlinkSync(tmp)',
            'response.schema===RESPONSE_SCHEMA',
            'response.request_id===REQUEST_ID',
            'response.action===ACTION',
            'Date.now()+615000',
            'await sleep(250)',
        ):
            self.assertIn(required, command)
        self.assertRegex(
            command,
            r"JSON\.stringify\(\{schema:SCHEMA,action:ACTION,request_id:REQUEST_ID\}\)",
        )
        self.assertNotIn("fs.renameSync(tmp,REQUEST)", command)
        self.assertNotIn("process.argv", command)
        self.assertNotIn("process.env", command)
        self.assertNotIn("child_process", command)
        self.assertNotIn("execSync", command)
        self.assertNotIn('ACTION="production_handoff_once"', command)

    def test_no_extra_control_surface_or_mutating_workflow_path(self):
        node_types = [node["type"] for node in self.nodes]
        for forbidden_type in (
            "n8n-nodes-base.webhook",
            "n8n-nodes-base.httpRequest",
            "n8n-nodes-base.postgres",
            "n8n-nodes-base.mysql",
            "n8n-nodes-base.dataTable",
        ):
            self.assertNotIn(forbidden_type, node_types)
        self.assertFalse(any(".ai" in node_type for node_type in node_types))
        self.assertNotIn("shadowQuantCp1", json.dumps(self.workflow))
        self.assertNotIn("Full Canvas", json.dumps(self.workflow))
        for forbidden in (
            "/Users/",
            "/Volumes/",
            "hermes",
            "kanban",
            "ssh",
            "curl",
            "wget",
            "redis",
            "postgres",
            "credential",
        ):
            self.assertNotIn(forbidden, self.command.lower())
        self.assertNotRegex(
            self.command,
            re.compile(r"\b(action|path|command)\s*=\s*JSON\.parse"),
        )

    def test_production_handoff_workflow_bytes_are_unchanged(self):
        digest = hashlib.sha256(PRODUCTION_WORKFLOW_PATH.read_bytes()).hexdigest()
        self.assertEqual(digest, PRODUCTION_WORKFLOW_SHA256)


if __name__ == "__main__":
    unittest.main()
