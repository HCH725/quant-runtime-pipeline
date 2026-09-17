#!/usr/bin/env python3
"""Checks for homepage/run_local.sh - the launcher must prove the real listening socket.

The launcher's hard boundary is "localhost only", and a reachable port is not a safe port:
`*:PORT` and `[::1]:PORT` both answer on 127.0.0.1, so an HTTP probe can be satisfied by a
LAN-exposed listener.  These checks drive the launcher with a stubbed `lsof` plus a `curl`
that always claims success - the exact fixture that let the previously reviewed launcher skip
its own socket assertion - and require it to refuse any listener that is not 127.0.0.1, both
when an instance was already running and when this run started one.

The stub answers `lsof -F n` in the shape real lsof prints (a `p`/`f`/`n` record per socket,
verified against macOS lsof for a wildcard bind: `p95608` / `f3` / `n*:3901`).

Nothing here starts Homepage or touches the real dashboard dirs: `lsof`, `curl`, `node` and the
payload interpreter are all stubbed into a temp bin dir, and data/run/app are temp paths.

Run: python3 runtime/tests/test_run_local_launcher.py     (stdlib unittest, no dependencies)
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LAUNCHER = REPO / "homepage" / "run_local.sh"
# The revision the review lane audited (d4871de).  It only asserted the socket when *it* started
# Homepage, so an already-listening wildcard instance made it print success - the bug pinned here.
PREFIX_REV = "d4871de"

LSOF_STUB = """#!/bin/sh
port=""
for arg in "$@"; do
  case "$arg" in -iTCP:*) port="${arg#-iTCP:}" ;; esac
done
case "$port" in
  3000) sock="${STUB_WEB:-}"; after="${STUB_WEB_AFTER:-0}" ;;
  8787) sock="${STUB_PAYLOAD:-}"; after="${STUB_PAYLOAD_AFTER:-0}" ;;
  *) sock=""; after=0 ;;
esac
if [ "$after" -gt 0 ]; then
  seen=$(cat "$STUB_STATE.$port" 2>/dev/null || echo 0)
  seen=$((seen + 1))
  printf '%s' "$seen" > "$STUB_STATE.$port"
  if [ "$seen" -lt "$after" ]; then sock=""; fi
fi
if [ -n "$sock" ]; then
  printf 'p1\\nf5\\n'
  printf '%s\\n' "$sock" | sed 's/^/n/'
fi
exit 0
"""

CURL_STUB = "#!/bin/sh\nexit 0\n"          # always "reachable", whatever the socket actually is
START_STUB = "#!/bin/sh\nexit 0\n"         # a stand-in for node / the payload interpreter


class LauncherChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="qrp-launcher-test-"))
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        # the launcher `cd`s into the Homepage checkout before starting it; the start itself is
        # stubbed (node/python), so an empty directory is the whole fixture.
        (self.tmp / "app-never-used").mkdir()
        for name, body in (("lsof", LSOF_STUB), ("curl", CURL_STUB), ("node", START_STUB),
                           ("python-stub", START_STUB)):
            target = self.bin / name
            target.write_text(body)
            target.chmod(0o755)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_launcher(self, web, payload, script=None, start_wait="0", web_after=0, payload_after=0):
        """Run the launcher (or a copy of it) with the stubbed PATH; return CompletedProcess."""
        env = {
            "PATH": "%s:%s" % (self.bin, os.environ.get("PATH", "/usr/bin:/bin")),
            "HOME": os.environ.get("HOME", str(self.tmp)),
            "STUB_WEB": web,
            "STUB_PAYLOAD": payload,
            "STUB_WEB_AFTER": str(web_after),
            "STUB_PAYLOAD_AFTER": str(payload_after),
            "STUB_STATE": str(self.tmp / "calls"),
            "START_WAIT": start_wait,
            "QUANT_DASHBOARD_DIR": str(self.tmp / "data"),
            "QUANT_DASHBOARD_RUN": str(self.tmp / "run"),
            "HOMEPAGE_APP": str(self.tmp / "app-never-used"),
            "PYTHON": str(self.bin / "python-stub"),
        }
        return subprocess.run(["sh", str(script or LAUNCHER)], capture_output=True, text=True,
                              env=env, timeout=60)

    def calls(self, port):
        state = self.tmp / ("calls.%d" % port)
        return int(state.read_text()) if state.exists() else 0

    # --- already-running instances: the socket is judged, not the HTTP probe --------------------
    def test_existing_wildcard_listener_is_refused(self):
        proc = self.run_launcher(web="*:3000", payload="127.0.0.1:8787")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("port 3000 is bound by '*:3000', not 127.0.0.1 only", proc.stderr)
        self.assertNotIn("dashboard:  http://localhost:3000/", proc.stdout)
        self.assertNotIn("started ", proc.stdout)

    def test_existing_ipv6_listener_is_refused(self):
        proc = self.run_launcher(web="[::1]:3000", payload="127.0.0.1:8787")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("port 3000 is bound by '[::1]:3000', not 127.0.0.1 only", proc.stderr)
        self.assertNotIn("dashboard:  http://localhost:3000/", proc.stdout)

    def test_wildcard_alongside_loopback_is_still_refused(self):
        # the previous assertion was `lsof ... | grep -q 127.0.0.1:3000`, which this set satisfies -
        # the refusal must still name the web port even though a loopback socket is also listed
        proc = self.run_launcher(web="*:3000\n127.0.0.1:3000", payload="127.0.0.1:8787")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("port 3000 is bound by '*:3000', not 127.0.0.1 only", proc.stderr)

    def test_wildcard_payload_listener_is_refused_too(self):
        proc = self.run_launcher(web="127.0.0.1:3000", payload="*:8787")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("port 8787 is bound by '*:8787', not 127.0.0.1 only", proc.stderr)
        self.assertNotIn("dashboard:  http://localhost:3000/", proc.stdout)

    def test_already_running_loopback_instance_passes_without_starting(self):
        proc = self.run_launcher(web="127.0.0.1:3000", payload="127.0.0.1:8787")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("dashboard:  http://localhost:3000/", proc.stdout)
        self.assertIn("payload:    http://127.0.0.1:8787/dashboard.json", proc.stdout)
        self.assertNotIn("started ", proc.stdout)

    # --- the freshly-started path is asserted just the same -------------------------------------
    def test_freshly_started_instance_is_asserted_too(self):
        # the payload is already loopback, nothing listens on the web port, so the launcher starts
        # one - and must still judge the real socket (the stub reports a start that never bound).
        proc = self.run_launcher(web="", payload="127.0.0.1:8787")
        self.assertIn("started homepage (pid", proc.stdout)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("nothing is listening on 127.0.0.1:3000", proc.stderr)
        self.assertNotIn("dashboard:  http://localhost:3000/", proc.stdout)

    def test_missing_payload_listener_is_refused_after_start(self):
        proc = self.run_launcher(web="127.0.0.1:3000", payload="")
        self.assertIn("started dashboard_serve (pid", proc.stdout)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("nothing is listening on 127.0.0.1:8787", proc.stderr)

    def test_listener_that_appears_late_is_accepted(self):
        # a real start needs time to bind: the wait buys it, and the socket is still judged
        proc = self.run_launcher(web="127.0.0.1:3000", payload="127.0.0.1:8787",
                                 start_wait="5", web_after=3)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("started homepage (pid", proc.stdout)
        self.assertIn("dashboard:  http://localhost:3000/", proc.stdout)
        self.assertGreaterEqual(self.calls(3000), 3)

    # --- the withdrawn form ---------------------------------------------------------------------
    def test_reachability_probe_no_longer_decides_the_start(self):
        text = LAUNCHER.read_text()
        self.assertIn('require_loopback "$PAYLOAD_PORT"', text)
        self.assertIn('require_loopback "$WEB_PORT"', text)
        self.assertNotIn("curl", text.lower())
        # the exact conditional the review lane flagged (`curl -sf` as the start decision)
        self.assertNotIn('if ! curl -sf "http://127.0.0.1:$WEB_PORT/"', text)

    def test_prefix_revision_skipped_its_own_assertion(self):
        """RED control: the same fixture on the audited revision printed success anyway."""
        shown = subprocess.run(["git", "show", "%s:homepage/run_local.sh" % PREFIX_REV],
                               cwd=REPO, capture_output=True, text=True)
        if shown.returncode != 0:
            self.skipTest("revision %s unavailable (shallow history): %s"
                          % (PREFIX_REV, shown.stderr.strip()))
        prefix = self.tmp / "run_local_prefix.sh"
        prefix.write_text(shown.stdout)
        proc = self.run_launcher(web="*:3000", payload="127.0.0.1:8787", script=prefix)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("dashboard:  http://localhost:3000/", proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
