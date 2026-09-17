#!/usr/bin/env python3
"""Checks for runtime/dashboard_serve.py (the 127.0.0.1 allowlist server behind the Homepage cards).

The server is the only thing the dashboard can reach, so these checks pin the boundary rather than
the rendering: loopback-only bind, the allowlist route table (no results root, no kanban.db, no
traversal, no directory walking), GET/HEAD only, and an unreadable payload surfacing as 503 instead
of an invented value.  The `/detail` page the cards link to is pinned the same way: it is the same
payload, escaped, and it is the only route added.  Nothing here touches the real data dir, board or
results tree.

Run: python3 runtime/tests/test_dashboard_serve.py     (stdlib unittest, no dependencies)
"""
import http.client
import json
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
            return resp.status, resp.read().decode("utf-8", "replace"), resp.getheader("Content-Type") or ""
        finally:
            conn.close()

    def test_bind_is_loopback_only(self):
        self.assertEqual(dash.HOST, "127.0.0.1")
        self.assertEqual(self.httpd.server_address[0], "127.0.0.1")

    def test_serves_the_payload_at_root_and_its_route(self):
        for path in ("/", "/dashboard.json"):
            status, body, _ = self.request(path)
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
            status, body, _ = self.request(path)
            self.assertEqual(status, 404, path)
            self.assertNotIn("schema_version", body)

    def test_missing_payload_is_503_not_a_fabricated_value(self):
        (self.dir / "dashboard.json").unlink()
        status, body, _ = self.request("/dashboard.json")
        self.assertEqual(status, 503)
        self.assertIn("unavailable", body)

    def test_no_other_file_in_the_data_dir_is_reachable(self):
        (self.dir / "extra.json").write_text('{"leak": true}\n')
        self.assertEqual(self.request("/extra.json")[0], 404)

    def test_detail_serves_the_same_payload_as_a_read_only_page(self):
        status, body, ctype = self.request("/detail")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertIn('<section id="health"', body)
        self.assertIn('<span class="badge ok">ok</span>', body)
        self.assertIn('<footer id="metadata"', body)
        self.assertIn("schema version", body)
        self.assertNotIn('<section id="schema_version"', body)
        self.assertNotIn("<a ", body)
        self.assertEqual(self.request("/dashboard.json"),
                         (200, PAYLOAD, "application/json; charset=utf-8"))

    def test_detail_prioritizes_human_sections_and_keeps_internal_fields_secondary(self):
        payload = {
            "schema_version": 1,
            "generated_at_utc": "2026-09-17T13:20:03Z",
            "monitoring_only": True,
            "scope_note": "read-only",
            "sources": {"results_root": "/Volumes/ExpansionDrive/qlib-results"},
            "agent": {"profile_status": "running"},
            "funnel": {"wiki_brain": {"summary": "296 / 516 reviewed", "reviewed": 516}},
            "leaderboard": {"entries": [{"summary": "Sharpe 4.40", "rank": 1, "sharpe": 4.4}]},
            "current": {"family_id": "family-x", "stage": "not launched", "progress_text": None},
            "health": {"status": "ok", "state_path": "/Users/hong/.hermes/state/quant_runtime_watchdog.json"},
        }
        (self.dir / "dashboard.json").write_text(json.dumps(payload))
        status, body, _ = self.request("/detail")
        self.assertEqual(status, 200)
        order = [body.index('id="%s"' % key)
                 for key in ("health", "current", "leaderboard", "funnel", "agent", "sources", "metadata")]
        self.assertEqual(order, sorted(order))
        self.assertIn('class="summary">Sharpe 4.40</p>', body)
        self.assertIn('class="summary">296 / 516 reviewed</p>', body)
        self.assertIn('>quant_runtime_watchdog.json</span>', body)
        self.assertIn('>qlib-results</span>', body)
        self.assertIn('@media (min-width: 700px)', body)
        self.assertIn('section:target', body)
        self.assertIn('class="empty">unavailable</span>', body)

    def test_detail_escapes_the_payload_and_never_invents_a_value(self):
        # the payload is written by another process: markup in it must not become markup here
        (self.dir / "dashboard.json").write_text('{"health": {"status": "<script>x</script>"}}')
        status, body, _ = self.request("/detail")
        self.assertEqual(status, 200)
        self.assertNotIn("<script>", body)
        self.assertIn("&lt;script&gt;", body)
        # no payload (or an unparseable one) is a 503 for the page too, never an empty page
        for broken in ("{not json", "[]"):
            (self.dir / "dashboard.json").write_text(broken)
            status, body, _ = self.request("/detail")
            self.assertEqual(status, 503, broken)
            self.assertIn("unavailable", body)

    def test_detail_is_the_only_route_added(self):
        for path in ("/detail/", "/detail/json", "/detail/../kanban.db", "/dashboard.json/detail"):
            self.assertEqual(self.request(path)[0], 404, path)
        self.assertEqual(self.request("/detail", method="HEAD")[0], 200)
        self.assertEqual(self.request("/detail", method="POST")[0], 501)
        # a query string selects nothing: the page is always the whole payload
        status, body, _ = self.request("/detail?card=/etc/passwd")
        self.assertEqual(status, 200)
        self.assertIn('<section id="health">', body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
