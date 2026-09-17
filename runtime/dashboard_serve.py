#!/usr/bin/env python3
"""Read-only static server for the quant dashboard JSON (127.0.0.1 only).

The Homepage dashboard is a viewer: its `customapi` widgets fetch
http://127.0.0.1:<port>/dashboard.json, which this process serves from one data directory holding
the file written by `candidate_snapshot.py --json`. The cards' drill-down link is `/detail` - the
same payload rendered as one plain HTML page, addressed relatively so that a remote device reaches
it through the same public hostname.

Boundary by construction: the bind address is hard-coded to 127.0.0.1 (there is no host flag), the
route table is an explicit allowlist (no directory walking, no path translation, no access to the
results root or kanban.db), only GET/HEAD are answered, and nothing is ever written. A payload that
is missing or unreadable is a 503 - never a fabricated value.

Usage: python3 runtime/dashboard_serve.py [--data-dir DIR] [--port 8787]
"""
import argparse
import html
import json
import os
import socketserver
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

HOST = "127.0.0.1"  # never a flag: localhost-stage viewer only
DEFAULT_PORT = 8787
DEFAULT_DATA_DIR = os.environ.get("QUANT_DASHBOARD_DIR",
                                  str(Path.home() / "quant-dashboard" / "data"))
JSON_TYPE = "application/json; charset=utf-8"
HTML_TYPE = "text/html; charset=utf-8"
# Explicit allowlist: route -> file name inside the data dir. Nothing else is reachable.
ROUTES = {"/": "dashboard.json", "/dashboard.json": "dashboard.json"}
# The one non-JSON route: the payload again, as a read-only page (no second computation).
DETAIL_PATH = "/detail"

DETAIL_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Quant Runtime - snapshot detail</title>
<style>
:root { color-scheme: dark; --accent: #f0b90b; --line: rgba(255,255,255,.08); }
body { margin: 0; padding: 16px; background: #121214; color: #d6d6dc;
  font: 15px/1.45 -apple-system, BlinkMacSystemFont, system-ui, sans-serif; }
h1 { font-size: 19px; letter-spacing: -.01em; margin: 0 0 2px; }
.meta { color: #a8a8b0; font-size: 12px; margin: 0 0 14px; }
section { background: #202023; border: 1px solid var(--line); border-radius: 18px;
  padding: 12px 14px; margin-bottom: 14px; }
h2 { font-size: 12px; letter-spacing: .06em; text-transform: uppercase; color: var(--accent);
  margin: 0 0 8px; padding-left: 8px; border-left: 3px solid var(--accent); border-radius: 2px; }
dl { margin: 0; } dt { color: #a8a8b0; font-size: 12px; } dd { margin: 0 0 6px; word-break: break-word; }
.item { border-top: 1px solid var(--line); margin-top: 8px; padding-top: 8px; }
</style>
</head>
<body>
<h1>Snapshot detail</h1>
<p class="meta">%s</p>
%s
</body>
</html>
"""


def render_detail(payload):
    """The payload as one read-only page; presentation only, never a second calculation.

    Every section, field and value comes from dashboard.json verbatim - nothing here recomputes a
    Sharpe, a drawdown, a progress figure or a health verdict. A null renders as `unavailable` (the
    same convention the Homepage cards use) and a key that is absent stays absent.
    """
    meta = "generated %s &middot; read-only &middot; same payload as the dashboard cards" % _text(
        payload.get("generated_at_utc"))
    return DETAIL_PAGE % (meta, "".join(_section(key, value) for key, value in payload.items()))


def _section(key, value):
    return '<section id="%s"><h2>%s</h2>%s</section>' % (_anchor(key), _label(key), _block(value))


def _block(value):
    if isinstance(value, dict):
        return "<dl>%s</dl>" % "".join(_row(key, value) for key, value in value.items())
    if isinstance(value, list):
        items = "".join('<div class="item">%s</div>' % (_block(item) if isinstance(item, dict)
                                                        else _text(item)) for item in value)
        return items or "<p>(empty)</p>"
    return "<p>%s</p>" % _text(value)


def _row(key, value):
    body = _block(value) if isinstance(value, (dict, list)) else _text(value)
    return "<dt>%s</dt><dd>%s</dd>" % (_label(key), body)


def _label(key):
    """Field name as a heading: the payload's own key, only de-underscored."""
    return html.escape(str(key).replace("_utc", " UTC").replace("_", " "))


def _anchor(key):
    """A stable, linkable id for the payload's own key (services.yaml links to these)."""
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in str(key).lower())


def _text(value):
    """A scalar as text. The payload is written by another process, so every value is escaped."""
    if value is None:
        return "unavailable"
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return html.escape(str(value))


class Handler(BaseHTTPRequestHandler):
    server_version = "quant-dashboard/1"
    data_dir = Path(DEFAULT_DATA_DIR)
    routes = ROUTES

    def do_GET(self):
        self._serve(head=False)

    def do_HEAD(self):
        self._serve(head=True)

    def _serve(self, head):
        path = urlsplit(self.path).path
        if path == DETAIL_PATH:
            self._serve_detail(head)
            return
        name = self.routes.get(path)
        if name is None:
            self._send(404, json.dumps({"error": "not a dashboard route"}), head)
            return
        try:
            body = (self.data_dir / name).read_bytes()
        except OSError as exc:
            self._send(503, json.dumps({"error": "dashboard payload unavailable: %s" % exc}), head)
            return
        self._send(200, body.decode("utf-8", "replace"), head)

    def _serve_detail(self, head):
        """The same payload as a page: an unreadable payload is a 503, never an empty page."""
        try:
            payload = json.loads((self.data_dir / self.routes["/"]).read_text("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("payload is not a JSON object")
        except (OSError, ValueError) as exc:
            self._send(503, json.dumps({"error": "dashboard payload unavailable: %s" % exc}), head)
            return
        self._send(200, render_detail(payload), head, HTML_TYPE)

    def _send(self, code, text, head, content_type=JSON_TYPE):
        body = (text if isinstance(text, bytes) else text.encode("utf-8"))
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if not head:
            self.wfile.write(body)


class Server(ThreadingHTTPServer):
    """ThreadingHTTPServer without the per-bind reverse-DNS lookup.

    `HTTPServer.server_bind` resolves `socket.getfqdn(host)`, which on a host with no reverse zone
    stalls every start (and every test) for seconds; this server is loopback-only and never needs an
    FQDN, so it keeps the numeric address as its name.
    """

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


def serve(data_dir, port):
    """One allowlist-only HTTP server bound to 127.0.0.1; port 0 picks a free port (tests)."""
    Handler.data_dir = Path(data_dir)
    httpd = Server((HOST, port), Handler)
    httpd.daemon_threads = True
    return httpd


def main():
    ap = argparse.ArgumentParser(description="Serve the quant dashboard JSON on 127.0.0.1 (read-only).")
    ap.add_argument("--data-dir", default=DEFAULT_DATA_DIR, help="directory holding dashboard.json")
    ap.add_argument("--port", type=int,
                    default=int(os.environ.get("QUANT_DASHBOARD_PORT", DEFAULT_PORT)))
    args = ap.parse_args()
    httpd = serve(args.data_dir, args.port)
    print("quant dashboard JSON: http://%s:%d/dashboard.json (data dir %s)"
          % (HOST, httpd.server_address[1], args.data_dir), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
