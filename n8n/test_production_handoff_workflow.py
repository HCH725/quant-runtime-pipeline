#!/usr/bin/env python3
"""Contract checks for the manual n8n production-handoff workflow."""
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


WORKFLOW_PATH = Path(__file__).with_name(
    "quant-control-plane-production-handoff-manual.workflow.json"
)


class ProductionHandoffWorkflowContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))
        cls.nodes = cls.workflow["nodes"]
        cls.by_name = {node["name"]: node for node in cls.nodes}
        cls.command = cls.by_name[
            "Invoke production_handoff_once through host bridge"
        ]["parameters"]["command"]

    def test_is_a_separate_manual_only_workflow(self):
        self.assertEqual(self.workflow["id"], "productionHandoffManualC2")
        self.assertEqual(
            self.workflow["name"], "Quant Control Plane — Production Handoff (manual)"
        )
        self.assertFalse(self.workflow["active"])
        self.assertNotIn("credentials", self.workflow)
        self.assertEqual(
            [node["type"] for node in self.nodes],
            ["n8n-nodes-base.manualTrigger", "n8n-nodes-base.executeCommand"],
        )
        self.assertNotIn("n8n-nodes-base.scheduleTrigger", [node["type"] for node in self.nodes])
        self.assertNotIn("n8n-nodes-base.webhook", [node["type"] for node in self.nodes])
        self.assertEqual(
            self.workflow["connections"]["Manual Trigger — production handoff"]["main"][0][0]["node"],
            "Invoke production_handoff_once through host bridge",
        )

    def test_request_response_contract_is_fixed_and_bounded(self):
        command = self.command
        for required in (
            'CONTROL="/home/node/.n8n-files/control"',
            'REQUEST=CONTROL+"/production_handoff.request.json"',
            'RESPONSE=CONTROL+"/production_handoff.response.json"',
            'SCHEMA="quant-control-action/v1"',
            'RESPONSE_SCHEMA="quant-control-action-response/v1"',
            'ACTION="production_handoff_once"',
            'crypto.randomUUID()',
            'fs.fsyncSync(fd)',
            'fs.linkSync(tmp,REQUEST)',
            'fs.unlinkSync(tmp)',
            'response.request_id===REQUEST_ID',
            'response.action===ACTION',
            'Date.now()+615000',
        ):
            self.assertIn(required, command)
        self.assertNotIn("fs.renameSync(tmp,REQUEST)", command)
        self.assertRegex(command, r"JSON\.stringify\(\{schema:SCHEMA,action:ACTION,request_id:REQUEST_ID\}\)")
        self.assertNotIn("process.argv", command)
        self.assertNotIn("process.env", command)
        self.assertNotIn("child_process", command)
        self.assertNotIn("execSync", command)

    def test_atomic_publish_does_not_replace_existing_request(self):
        """The same-dir hard-link publish is atomic and no-overwrite."""
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is unavailable")
        script = (
            "const fs=require('fs');"
            "const [tmp,request]=process.argv.slice(1);"
            "let conflict=false;"
            "try{fs.linkSync(tmp,request);}"
            "catch(error){if(error.code!=='EEXIST')throw error;conflict=true;}"
            "finally{try{fs.unlinkSync(tmp);}catch{}}"
            "if(!conflict)process.exit(2);"
        )
        with tempfile.TemporaryDirectory(prefix="n8n-publish-contract-") as directory:
            root = Path(directory)
            request = root / "production_handoff.request.json"
            temp = root / ".production_handoff.request.tmp"
            request.write_text("existing", encoding="utf-8")
            temp.write_text("new", encoding="utf-8")
            result = subprocess.run(
                [node, "-e", script, str(temp), str(request)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(request.read_text(encoding="utf-8"), "existing")
            self.assertFalse(temp.exists())

    def test_workflow_has_no_host_or_arbitrary_control_surface(self):
        for forbidden in (
            "/Users/",
            "/Volumes/",
            "/host/",
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
        self.assertNotRegex(self.command, re.compile(r"\b(action|path|command)\s*=\s*JSON\.parse"))


if __name__ == "__main__":
    unittest.main()
