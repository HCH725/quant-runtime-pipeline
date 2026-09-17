#!/usr/bin/env python3
"""Read-only static server for the quant dashboard JSON (127.0.0.1 only).

The Homepage dashboard is a viewer: its `customapi` widgets fetch
http://127.0.0.1:<port>/dashboard.json, which this process serves from one data directory holding
the file written by `candidate_snapshot.py --json`.

Boundary by construction: the bind address is hard-coded to 127.0.0.1 (there is no host flag), the
route table is an explicit allowlist (no directory walking, no path translation, no access to the
results root or kanban.db), only GET/HEAD are answered, and nothing is ever written. A payload that
is missing or unreadable is a 503 - never a fabricated value.

Usage: python3 runtime/dashboard_serve.py [--data-dir DIR] [--port 8787]
"""
import argparse
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
# Explicit allowlist: route -> file name inside the data dir. Nothing else is reachable.
ROUTES = {"/": "dashboard.json", "/dashboard.json": "dashboard.json"}


class Handler(BaseHTTPRequestHandler):
    server_version = "quant-dashboard/1"
    data_dir = Path(DEFAULT_DATA_DIR)
    routes = ROUTES

    def do_GET(self):
        self._serve(head=False)

    def do_HEAD(self):
        self._serve(head=True)

    def _serve(self, head):
        name = self.routes.get(urlsplit(self.path).path)
        if name is None:
            self._send(404, json.dumps({"error": "not a dashboard route"}), head)
            return
        try:
            body = (self.data_dir / name).read_bytes()
        except OSError as exc:
            self._send(503, json.dumps({"error": "dashboard payload unavailable: %s" % exc}), head)
            return
        self._send(200, body.decode("utf-8", "replace"), head)

    def _send(self, code, text, head):
        body = (text if isinstance(text, bytes) else text.encode("utf-8"))
        self.send_response(code)
        self.send_header("Content-Type", JSON_TYPE)
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
