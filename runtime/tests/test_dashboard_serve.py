#!/usr/bin/env python3
"""Checks for runtime/dashboard_serve.py (the 127.0.0.1 allowlist server behind the Homepage cards).

The server is the only thing the dashboard can reach, so these checks pin the boundary rather than
the rendering: loopback-only bind, the allowlist route table (no results root, no kanban.db, no
traversal, no directory walking), GET/HEAD only, and an unreadable payload surfacing as 503 instead
of an invented value.  Nothing here touches the real data dir, board or results tree.

Run: python3 runtime/tests/test_dashboard_serve.py     (stdlib unittest, no dependencies)
"""
import http.client
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME))
import dashboard_serve as dash  # noqa: E402

PAYLOAD = '{"schema_version": 1, "health": {"status": "ok"}}\n'


class ServerChecks(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="qrp-dashboard-test-"))
        (self.dir / "dashboard.json").write_text(PAYLOAD)
        (self.dir / "not-listed.json").write_text("{}\n")
        self.httpd = dash.serve(self.dir, 0)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        shutil.rmtree(self.dir, ignore_errors=True)

    def request(self, path, method="GET"):
        # http.client (not urllib) so the path reaches the server verbatim - no client-side
        # normalisation of ".." segments - and no system-proxy lookup per call.
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=5)
        try:
            conn.request(method, path)
            resp = conn.getresponse()
            return resp.status, resp.read().decode("utf-8", "replace")
        finally:
            conn.close()

    def test_bind_is_loopback_only(self):
        self.assertEqual(dash.HOST, "127.0.0.1")
        self.assertEqual(self.httpd.server_address[0], "127.0.0.1")

    def test_serves_the_payload_at_root_and_its_route(self):
        for path in ("/", "/dashboard.json"):
            status, body = self.request(path)
            self.assertEqual(status, 200)
            self.assertEqual(body, PAYLOAD)

    def test_only_get_and_head_are_answered(self):
        self.assertEqual(self.request("/dashboard.json", method="HEAD")[0], 200)
        self.assertEqual(self.request("/dashboard.json", method="POST")[0], 501)
        self.assertEqual(self.request("/dashboard.json", method="DELETE")[0], 501)

    def test_everything_outside_the_allowlist_is_404(self):
        # not in the route table, an unlisted file in the data dir, a results-root path, kanban.db,
        # and an encoded traversal escape - the route lookup is exact, so none of them resolve.
        for path in ("/kanban.db", "/not-listed.json", "/results/_survivors/leaderboard.json",
                     "/%2e%2e/kanban.db", "/..%2fkanban.db", "/dashboard.json/../kanban.db",
                     "/../../etc/passwd"):
            status, body = self.request(path)
            self.assertEqual(status, 404, path)
            self.assertNotIn("schema_version", body)

    def test_missing_payload_is_503_not_a_fabricated_value(self):
        (self.dir / "dashboard.json").unlink()
        status, body = self.request("/dashboard.json")
        self.assertEqual(status, 503)
        self.assertIn("unavailable", body)

    def test_no_other_file_in_the_data_dir_is_reachable(self):
        (self.dir / "extra.json").write_text('{"leak": true}\n')
        self.assertEqual(self.request("/extra.json")[0], 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
