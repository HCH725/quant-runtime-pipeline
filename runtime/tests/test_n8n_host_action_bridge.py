#!/opt/homebrew/bin/python3
"""Focused checks for the n8n host-action bridge (card t_e886543c).

Contract under test, in one line: only the one allowlisted request
(schema=quant-control-action/v1, action=production_handoff_once, bounded
non-empty request_id) may invoke the ONE hardcoded existing wrapper command -
every other payload fails closed with a response and zero host action, the
claim is an atomic rename (duplicate wakes cannot double-run), the response is
a bounded atomic temp+rename at a fixed path, and no path ever comes from
request data.

The action is always an injected stub runner: no test ever executes the real
handoff wrapper (a real run could append a candidate card).

Run: python3 runtime/tests/test_n8n_host_action_bridge.py  (stdlib unittest)
"""
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME))
import n8n_host_action_bridge as br  # noqa: E402

VALID = {"schema": br.SCHEMA, "action": br.ACTION, "request_id": "req-0001"}


class BridgeCase(unittest.TestCase):
    """Every test runs main() against a temp control dir + stub runner."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="qrp-n8n-bridge-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.saved = (br.REQUEST_PATH, br.RESPONSE_PATH, list(sys.argv))
        br.REQUEST_PATH = os.path.join(self.tmp, "production_handoff.request.json")
        br.RESPONSE_PATH = os.path.join(self.tmp, "production_handoff.response.json")
        self.calls = []
        self.runner_result = (0, "handoff-ok", "")

    def tearDown(self):
        (br.REQUEST_PATH, br.RESPONSE_PATH) = self.saved[:2]
        sys.argv[:] = self.saved[2]

    # --- helpers ---
    def _runner(self, cmd, env, timeout):
        self.calls.append((list(cmd), dict(env), timeout))
        result = self.runner_result
        if isinstance(result, BaseException):
            raise result
        return result

    def put_request(self, payload):
        with open(br.REQUEST_PATH, "w") as fh:
            if isinstance(payload, bytes):
                fh.buffer.write(payload)
            else:
                json.dump(payload, fh)

    def response(self):
        with open(br.RESPONSE_PATH) as fh:
            return json.load(fh)

    def run_bridge(self):
        return br.main(runner=self._runner)

    # --- no request ---
    def test_no_request_is_pure_noop(self):
        rc = self.run_bridge()
        self.assertEqual(rc, 0)
        self.assertEqual(self.calls, [], "no request must never invoke the action")
        self.assertEqual(os.listdir(self.tmp), [], "no-op writes nothing at all")

    def test_second_wake_after_success_is_noop(self):
        self.put_request(VALID)
        self.assertEqual(self.run_bridge(), 0)
        self.assertEqual(self.run_bridge(), 0)  # duplicate launchd wake
        self.assertEqual(len(self.calls), 1, "duplicate wake must not double-run")

    # --- valid dispatch: EXACT command, minimal env ---
    def test_valid_request_invokes_exact_existing_wrapper_command(self):
        self.put_request(VALID)
        rc = self.run_bridge()
        self.assertEqual(rc, 0, self.response())
        self.assertEqual(len(self.calls), 1)
        cmd, env, timeout = self.calls[0]
        self.assertEqual(cmd, ["/opt/homebrew/bin/python3", br.WRAPPER],
                         "the only legal command is python3 + the existing wrapper")
        self.assertTrue(os.path.isfile(br.WRAPPER), "wrapper must be the existing host file")
        self.assertEqual(sorted(env), ["HOME", "PATH"], "minimal env only")
        self.assertNotIn("HERMES_DELEGATED_CHILD_CONTEXT", env)
        self.assertTrue(0 < timeout <= 900, "subprocess timeout must be bounded")
        resp = self.response()
        self.assertEqual(resp["schema"], br.RESPONSE_SCHEMA)
        self.assertEqual(resp["request_id"], "req-0001")
        self.assertEqual(resp["action"], br.ACTION)
        self.assertEqual(resp["exit_code"], 0)
        self.assertEqual(resp["status"], "ok")
        self.assertEqual(resp["stdout"], "handoff-ok")
        self.assertRegex(resp["started_at_utc"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertRegex(resp["finished_at_utc"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(sorted(os.listdir(self.tmp)), ["production_handoff.response.json"],
                         "request and claim must be cleaned up after the response")

    def test_action_failure_reports_exit_code_and_fails_closed(self):
        self.runner_result = (7, "partial", "boom")
        self.put_request(VALID)
        rc = self.run_bridge()
        self.assertEqual(rc, 1)
        resp = self.response()
        self.assertEqual(resp["exit_code"], 7)
        self.assertEqual(resp["status"], "action_failed")
        self.assertEqual(resp["stderr"], "boom")
        self.assertEqual(sorted(os.listdir(self.tmp)), ["production_handoff.response.json"])

    def test_action_timeout_is_bounded_and_recorded(self):
        self.runner_result = subprocess.TimeoutExpired(br.ACTION_CMD, br.ACTION_TIMEOUT_S,
                                                        output="late-out", stderr="late-err")
        self.put_request(VALID)
        rc = self.run_bridge()
        self.assertEqual(rc, 1)
        resp = self.response()
        self.assertEqual(resp["status"], "action_timeout")
        self.assertIsNone(resp["exit_code"])
        self.assertEqual(resp["stdout"], "late-out")
        self.assertEqual(sorted(os.listdir(self.tmp)), ["production_handoff.response.json"])

    # --- fail closed: malformed / unknown / oversize ---
    def test_malformed_json_fails_closed(self):
        self.put_request(b'{"schema": "quant-control-')
        self.assertEqual(self.run_bridge(), 1)
        self.assertEqual(self.calls, [], "malformed JSON must never invoke the action")
        self.assertEqual(self.response()["status"], "rejected_malformed_json")
        self.assertIsNone(self.response()["request_id"])

    def test_non_object_json_fails_closed(self):
        self.put_request(b'["quant-control-action/v1"]')
        self.assertEqual(self.run_bridge(), 1)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.response()["status"], "rejected_malformed_json")

    def test_unknown_action_fails_closed(self):
        self.put_request({**VALID, "action": "wipe_results_root"})
        self.assertEqual(self.run_bridge(), 1)
        self.assertEqual(self.calls, [], "unknown action must never invoke the action")
        resp = self.response()
        self.assertEqual(resp["status"], "rejected_unknown_action")
        self.assertEqual(resp["action"], "wipe_results_root", "rejection echoes the offender")
        self.assertIsNone(resp["exit_code"])

    def test_wrong_schema_fails_closed(self):
        self.put_request({**VALID, "schema": "quant-control-action/v2"})
        self.assertEqual(self.run_bridge(), 1)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.response()["status"], "rejected_schema")

    def test_unexpected_field_fails_closed(self):
        self.put_request({**VALID, "out_path": "/tmp/evil.json"})
        self.assertEqual(self.run_bridge(), 1)
        self.assertEqual(self.calls, [], "an allowlist is not a filter - extra fields reject")
        self.assertEqual(self.response()["status"], "rejected_field_set")
        self.assertFalse(os.path.exists("/tmp/evil.json"))

    def test_bad_request_id_fails_closed(self):
        for bad in ("", "x" * (br.MAX_REQUEST_ID + 1), 42, None):
            with self.subTest(request_id=bad):
                if os.path.exists(br.REQUEST_PATH):
                    os.remove(br.REQUEST_PATH)
                if os.path.exists(br.RESPONSE_PATH):
                    os.remove(br.RESPONSE_PATH)
                self.calls.clear()
                self.put_request({**VALID, "request_id": bad})
                self.assertEqual(self.run_bridge(), 1)
                self.assertEqual(self.calls, [])
                self.assertEqual(self.response()["status"], "rejected_request_id")

    def test_oversize_request_fails_closed_before_json_parse(self):
        self.put_request(b" " * (br.MAX_REQUEST_BYTES + 1))
        self.assertEqual(self.run_bridge(), 1)
        self.assertEqual(self.calls, [], "oversize must be rejected by the byte cap")
        self.assertEqual(self.response()["status"], "rejected_oversize")

    # --- atomic claim ---
    def test_claim_is_atomic_rename_to_same_dir_per_pid(self):
        self.put_request(VALID)
        claim = br._claim()
        self.assertIsNotNone(claim)
        claim = claim or ""
        self.assertEqual(claim, br.REQUEST_PATH + (br.CLAIM_SUFFIX % os.getpid()))
        self.assertEqual(os.path.dirname(claim), os.path.dirname(br.REQUEST_PATH),
                         "claim must stay on the same filesystem (same directory)")
        self.assertFalse(os.path.exists(br.REQUEST_PATH))
        self.assertIsNone(br._claim(), "a second claim after the rename must no-op")

    def test_orphaned_claim_is_never_reread(self):
        """A claim orphaned by a killed run (other PID) stays put; only a fresh request is served.

        Same-PID re-runs legitimately overwrite a stale claim (rename source bytes
        are fresh), which is harmless; the launchd-real case is an orphan from a
        dead PID, which must never be re-read or cleaned by a later run.
        """
        self.put_request(VALID)
        orphan = br.REQUEST_PATH + (br.CLAIM_SUFFIX % (os.getpid() + 1))
        Path(orphan).write_text('{"stale": true}')
        self.assertEqual(self.run_bridge(), 0)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.response()["request_id"], "req-0001")
        self.assertTrue(os.path.exists(orphan), "orphans are not this run's business")
        self.assertEqual(sorted(os.listdir(self.tmp)),
                         [os.path.basename(orphan), "production_handoff.response.json"])

    # --- bounded output, fixed paths ---
    def test_stdout_stderr_are_byte_bounded(self):
        self.runner_result = (0, "A" * (br.MAX_OUTPUT_BYTES * 3), "B" * (br.MAX_OUTPUT_BYTES * 3))
        self.put_request(VALID)
        self.assertEqual(self.run_bridge(), 0)
        resp = self.response()
        cap = br.MAX_OUTPUT_BYTES
        self.assertLessEqual(len(resp["stdout"].encode()), cap + len("\n...[truncated]"))
        self.assertLessEqual(len(resp["stderr"].encode()), cap + len("\n...[truncated]"))
        self.assertTrue(resp["stdout"].endswith("...[truncated]"))
        self.assertTrue(resp["stderr"].endswith("...[truncated]"))

    def test_response_is_atomic_temp_rename_no_partial_left(self):
        """Response lands only via fixed-path temp+rename; no strays remain."""
        real_replace = br.os.replace
        seen = []

        def spy(src, dst):
            seen.append((os.path.basename(src), os.path.basename(dst)))
            return real_replace(src, dst)

        br.os.replace = spy
        self.addCleanup(setattr, br.os, "replace", real_replace)
        self.put_request(VALID)
        self.assertEqual(self.run_bridge(), 0)
        self.assertEqual(seen, [(os.path.basename(br.RESPONSE_PATH) + ".tmp.%d" % os.getpid(),
                                 os.path.basename(br.RESPONSE_PATH))],
                         "response must be written by temp+rename to the fixed path")
        self.assertEqual(sorted(os.listdir(self.tmp)), ["production_handoff.response.json"],
                         "no temp/claim strays after a clean run")

    def test_request_cannot_steal_paths(self):
        """Path-shaped fields are rejected, not honored; only fixed paths touched."""
        self.put_request({**VALID, "request_path": "/etc/passwd",
                          "response_path": "/tmp/evil-response.json"})
        self.assertEqual(self.run_bridge(), 1)
        self.assertEqual(self.calls, [])
        self.assertEqual(sorted(os.listdir(self.tmp)), ["production_handoff.response.json"],
                         "exactly one fixed response file; no path from request data used")


class PlistContract(unittest.TestCase):
    """The tracked launchd plist must match the card's wake contract exactly."""

    def setUp(self):
        with open(RUNTIME / "ai.quant.n8n-host-bridge.plist", "rb") as fh:
            self.plist = plistlib.load(fh)

    def test_watch_paths_target_the_exact_request_file(self):
        self.assertEqual(self.plist.get("WatchPaths"),
                         ["/Users/hong/workspace/n8n/files/control/production_handoff.request.json"])

    def test_no_schedule_no_keepalive(self):
        self.assertIs(self.plist.get("RunAtLoad"), False)
        self.assertNotIn("StartInterval", self.plist)
        self.assertNotIn("KeepAlive", self.plist)

    def test_program_arguments_use_homebrew_python_and_repo_script(self):
        args = self.plist.get("ProgramArguments", [])
        self.assertEqual(args[0], "/opt/homebrew/bin/python3")
        self.assertEqual(args[1], str(RUNTIME / "n8n_host_action_bridge.py"))
        self.assertEqual(self.plist.get("Label"), "ai.quant.n8n-host-bridge")


if __name__ == "__main__":
    unittest.main(verbosity=2)
