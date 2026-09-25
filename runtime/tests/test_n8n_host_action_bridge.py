#!/opt/homebrew/bin/python3
"""Focused checks for the n8n host-action bridge (card t_e886543c).

Contract under test, in one line: only the two allowlisted requests
(schema=quant-control-action/v1, action=production_handoff_once or
runtime_reconcile_once, bounded non-empty request_id) may invoke their exact
hardcoded existing wrapper command - every other payload fails closed with a
response and zero host action, the claim is an atomic rename (duplicate wakes
cannot double-run), the response is a bounded atomic temp+rename at a fixed
path, and no path ever comes from request data.

The action is always an injected stub runner: no test ever executes the real
handoff wrapper (a real run could append a candidate card).

Run: python3 runtime/tests/test_n8n_host_action_bridge.py  (stdlib unittest)
"""
import json
import os
import plistlib
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

RUNTIME = Path(__file__).resolve().parents[1]
CANONICAL_RUNTIME = Path("/Users/hong/workspace/quant-runtime-pipeline/runtime")
sys.path.insert(0, str(RUNTIME))
import n8n_host_action_bridge as br  # noqa: E402

VALID = {"schema": br.SCHEMA, "action": br.ACTION, "request_id": "req-0001"}
RUNTIME_VALID = {
    "schema": br.SCHEMA,
    "action": br.RUNTIME_RECONCILE_ACTION,
    "request_id": "req-reconcile-0001",
}


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

    def outside_path(self, name):
        outside_dir = Path(tempfile.mkdtemp(
            prefix="qrp-n8n-bridge-outside-", dir=str(Path(self.tmp).parent)))
        self.addCleanup(shutil.rmtree, outside_dir, True)
        return outside_dir / name

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

    def test_request_arriving_during_action_is_drained_once(self):
        """One wake drains a valid request published while its first action runs."""
        self.put_request(VALID)
        events = []
        real_claim = br._claim
        real_write_response = br._write_response
        real_remove_claim = br._remove_claim

        def tracked_claim():
            claim = real_claim()
            events.append(("claim", claim is not None))
            return claim

        def tracked_write_response(payload):
            result = real_write_response(payload)
            events.append(("response", payload["request_id"]))
            return result

        def tracked_remove_claim(claim_path):
            result = real_remove_claim(claim_path)
            events.append(("cleanup", os.path.basename(claim_path)))
            return result

        def overlapping_runner(cmd, env, timeout):
            events.append(("action", list(cmd)))
            if len([event for event in events if event[0] == "action"]) == 1:
                # Publish into the same fixed single slot while request one is in flight.
                self.put_request(RUNTIME_VALID)
                return 0, "first-ok", ""
            return 0, "second-ok", ""

        with patch.object(br, "_claim", side_effect=tracked_claim), \
                patch.object(br, "_write_response", side_effect=tracked_write_response), \
                patch.object(br, "_remove_claim", side_effect=tracked_remove_claim):
            rc = br.main(runner=overlapping_runner)

        self.assertEqual(rc, 0)
        self.assertEqual(
            [event for event in events if event[0] == "action"],
            [
                ("action", br.ACTION_COMMANDS[br.PRODUCTION_HANDOFF_ACTION]),
                ("action", br.ACTION_COMMANDS[br.RUNTIME_RECONCILE_ACTION]),
            ],
            "both fixed actions must dispatch exactly once in the same invocation",
        )
        claim_name = os.path.basename(br.REQUEST_PATH + (br.CLAIM_SUFFIX % os.getpid()))
        first_cleanup = events.index(("cleanup", claim_name))
        claim_indexes = [i for i, event in enumerate(events) if event == ("claim", True)]
        self.assertEqual(
            [event for event in events if event[0] == "claim"],
            [("claim", True), ("claim", True), ("claim", False)],
            "after the two present requests, one absent-path check ends the invocation",
        )
        self.assertLess(events.index(("response", "req-0001")), first_cleanup)
        self.assertLess(first_cleanup, claim_indexes[1],
                        "the first claim must be cleaned before draining the pending request")
        self.assertEqual(
            self.response()["request_id"], RUNTIME_VALID["request_id"],
            "the pending request must be handled after the first response is written",
        )
        self.assertEqual(self.response()["action"], br.RUNTIME_RECONCILE_ACTION)
        self.assertEqual(self.response()["stdout"], "second-ok")
        self.assertFalse(os.path.exists(br.REQUEST_PATH), "no pending request may be orphaned")
        self.assertEqual(sorted(os.listdir(self.tmp)), ["production_handoff.response.json"])

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

    def test_runtime_reconcile_invokes_exact_existing_wrapper_command(self):
        self.put_request(RUNTIME_VALID)
        rc = self.run_bridge()
        self.assertEqual(rc, 0, self.response())
        self.assertEqual(len(self.calls), 1)
        cmd, env, timeout = self.calls[0]
        self.assertEqual(
            cmd,
            ["/opt/homebrew/bin/python3", br.RUNTIME_RECONCILE_WRAPPER],
            "runtime reconcile must map to the exact existing wrapper",
        )
        self.assertTrue(os.path.isfile(br.RUNTIME_RECONCILE_WRAPPER))
        self.assertEqual(sorted(env), ["HOME", "PATH"], "minimal env only")
        self.assertNotIn("HERMES_DELEGATED_CHILD_CONTEXT", env)
        self.assertEqual(timeout, br.ACTION_TIMEOUT_S)
        resp = self.response()
        self.assertEqual(resp["schema"], br.RESPONSE_SCHEMA)
        self.assertEqual(resp["request_id"], RUNTIME_VALID["request_id"])
        self.assertEqual(resp["action"], br.RUNTIME_RECONCILE_ACTION)
        self.assertEqual(resp["status"], "ok")

    def test_allowlist_is_exactly_the_three_fixed_actions(self):
        self.assertEqual(
            br.ALLOWED_ACTIONS,
            (br.PRODUCTION_HANDOFF_ACTION, br.RUNTIME_RECONCILE_ACTION, br.RUNTIME_OBSERVE_ACTION),
        )
        self.assertEqual(
            br.ACTION_COMMANDS,
            {
                br.PRODUCTION_HANDOFF_ACTION: [
                    "/opt/homebrew/bin/python3",
                    "/Users/hong/.hermes/scripts/quant_production_handoff.py",
                ],
                br.RUNTIME_RECONCILE_ACTION: [
                    "/opt/homebrew/bin/python3",
                    "/Users/hong/.hermes/scripts/quant_runtime_reconcile.py",
                ],
                br.RUNTIME_OBSERVE_ACTION: [
                    "/opt/homebrew/bin/python3",
                    "/Users/hong/.hermes/scripts/quant_runtime_observe.py",
                ],
            },
        )
        # The read-only observation action is the only one with the larger cap, and the two mutating
        # actions keep the audited bound exactly.
        self.assertEqual(
            br.OUTPUT_CAP_BYTES,
            {br.PRODUCTION_HANDOFF_ACTION: br.MAX_OUTPUT_BYTES,
             br.RUNTIME_RECONCILE_ACTION: br.MAX_OUTPUT_BYTES,
             br.RUNTIME_OBSERVE_ACTION: br.MAX_OBSERVATION_BYTES},
        )
        self.assertGreater(br.MAX_OBSERVATION_BYTES, br.MAX_OUTPUT_BYTES)

    def test_observation_action_needs_no_arguments_and_is_not_a_mutating_wrapper(self):
        # It must invoke the observation module, never a scheduler/mutating wrapper, and the request
        # schema stays the same three keys (it can carry nothing that alters the command).
        self.assertEqual(br.ACTION_COMMANDS[br.RUNTIME_OBSERVE_ACTION][1].rsplit("/", 1)[-1],
                         "quant_runtime_observe.py")
        self.assertEqual(br.ALLOWED_KEYS, ("schema", "action", "request_id"))
        self.assertEqual(br.SCHEMA, "quant-control-action/v1")

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

    def test_action_timeout_kills_descendant_process_group(self):
        """A timed-out wrapper cannot leave a grandchild mutating host state later."""
        marker = Path(self.tmp) / "descendant-marker"
        grandchild = (
            "from pathlib import Path; import time; "
            f"p=Path({str(marker)!r}); p.write_text('ready'); "
            "time.sleep(2); p.write_text('late')"
        )
        parent = (
            "from pathlib import Path; import subprocess,sys,time\n"
            f"subprocess.Popen([sys.executable, '-c', {grandchild!r}])\n"
            f"p=Path({str(marker)!r}); deadline=time.time()+1.0\n"
            "while not p.exists() and time.time() < deadline:\n"
            "    time.sleep(0.01)\n"
            "time.sleep(10)\n"
        )
        with self.assertRaises(subprocess.TimeoutExpired):
            br._run_action([sys.executable, "-c", parent],
                           {"PATH": os.environ.get("PATH", "")}, 1.5)
        self.assertTrue(marker.exists(), "the regression child must have spawned its descendant")
        time.sleep(2.5)
        self.assertEqual(marker.read_text(encoding="utf-8"), "ready",
                         "the descendant must die with the timed-out process group")

    # --- fail closed: malformed / unknown / oversize ---
    def test_malformed_json_fails_closed(self):
        self.put_request(b'{"schema": "quant-control-')
        self.assertEqual(self.run_bridge(), 1)
        self.assertEqual(self.calls, [], "malformed JSON must never invoke the action")
        self.assertEqual(self.response()["status"], "rejected_malformed_json")
        self.assertIsNone(self.response()["request_id"])

    def test_invalid_utf8_fails_closed_and_cleans_claim(self):
        self.put_request(b"\xff\xfe\xfd")
        self.assertEqual(self.run_bridge(), 1)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.response()["status"], "rejected_malformed_json")
        self.assertEqual(sorted(os.listdir(self.tmp)), ["production_handoff.response.json"])

    def test_deep_json_recursion_fails_closed_and_cleans_claim(self):
        nested = b"[" * 2000 + b"]" * 2000
        self.assertLessEqual(len(nested), br.MAX_REQUEST_BYTES)
        self.put_request(nested)
        self.assertEqual(self.run_bridge(), 1)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.response()["status"], "rejected_malformed_json")
        self.assertEqual(sorted(os.listdir(self.tmp)), ["production_handoff.response.json"])

    def test_request_symlink_is_rejected_without_following_external_target(self):
        outside = Path(self.tmp) / "outside-request.json"
        outside.write_text(json.dumps(VALID), encoding="utf-8")
        Path(br.REQUEST_PATH).symlink_to(outside)
        self.assertEqual(self.run_bridge(), 1)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.response()["status"], "rejected_non_regular_file")
        self.assertEqual(outside.read_text(encoding="utf-8"), json.dumps(VALID))
        self.assertFalse(os.path.lexists(br.REQUEST_PATH))
        self.assertEqual(sorted(os.listdir(self.tmp)),
                         ["outside-request.json", "production_handoff.response.json"])

    def test_request_fifo_is_rejected_without_blocking(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("mkfifo is unavailable on this platform")
        os.mkfifo(br.REQUEST_PATH)
        started = time.monotonic()
        self.assertEqual(self.run_bridge(), 1)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 1.0, "FIFO input must not block the bridge")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.response()["status"], "rejected_non_regular_file")
        self.assertEqual(sorted(os.listdir(self.tmp)), ["production_handoff.response.json"])

    def test_nonempty_directory_is_rejected_without_claim_residue(self):
        request_dir = Path(br.REQUEST_PATH)
        request_dir.mkdir()
        nested = request_dir / "nested"
        nested.write_text("sentinel", encoding="utf-8")
        self.assertEqual(self.run_bridge(), 1)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.response()["status"], "rejected_non_regular_file")
        self.assertTrue(request_dir.is_dir(), "unsafe directory input must not be recursively deleted")
        self.assertEqual(nested.read_text(encoding="utf-8"), "sentinel")
        self.assertEqual(sorted(os.listdir(self.tmp)),
                         ["production_handoff.request.json", "production_handoff.response.json"])

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
        for bad in ("", "x" * (br.MAX_REQUEST_ID + 1), "\ud800", 42, None):
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
        """Response lands only via same-dir temp+rename; no strays remain."""
        real_replace = br.os.replace
        seen = []

        def spy(src, dst):
            seen.append((src, dst))
            return real_replace(src, dst)

        br.os.replace = spy
        self.addCleanup(setattr, br.os, "replace", real_replace)
        self.put_request(VALID)
        self.assertEqual(self.run_bridge(), 0)
        self.assertEqual(len(seen), 1)
        src, dst = seen[0]
        self.assertEqual(os.path.dirname(src), os.path.dirname(br.RESPONSE_PATH))
        self.assertEqual(dst, br.RESPONSE_PATH)
        self.assertNotEqual(src, dst)
        self.assertEqual(sorted(os.listdir(self.tmp)), ["production_handoff.response.json"],
                         "no temp/claim strays after a clean run")

    def test_response_temp_collision_never_follows_symlink(self):
        """A pre-existing temp symlink cannot redirect response bytes outside control/."""
        outside = self.outside_path("outside-target")
        outside.write_text("sentinel", encoding="utf-8")
        first = "preexisting"
        temp_link = Path(br.RESPONSE_PATH + ".tmp." + first)
        temp_link.symlink_to(outside)
        with patch.object(br.secrets, "token_hex", side_effect=[first, "fresh"]):
            self.put_request(VALID)
            self.assertEqual(self.run_bridge(), 0)
        self.assertEqual(outside.read_text(encoding="utf-8"), "sentinel")
        self.assertTrue(temp_link.is_symlink(), "the pre-existing link must not be followed or removed")
        self.assertTrue(stat.S_ISREG(os.lstat(br.RESPONSE_PATH).st_mode))
        self.assertEqual(self.response()["status"], "ok")

    def test_response_path_symlink_is_replaced_by_regular_file(self):
        """Replacing the fixed response symlink must not write its external target."""
        outside = self.outside_path("outside-target")
        outside.write_text("sentinel", encoding="utf-8")
        Path(br.RESPONSE_PATH).symlink_to(outside)
        self.put_request(VALID)
        self.assertEqual(self.run_bridge(), 0)
        self.assertEqual(outside.read_text(encoding="utf-8"), "sentinel")
        self.assertTrue(stat.S_ISREG(os.lstat(br.RESPONSE_PATH).st_mode))

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
        self.assertEqual(args[1], str(CANONICAL_RUNTIME / "n8n_host_action_bridge.py"))
        self.assertEqual(self.plist.get("Label"), "ai.quant.n8n-host-bridge")


if __name__ == "__main__":
    unittest.main(verbosity=2)
