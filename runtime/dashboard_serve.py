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
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>棠元宗 · 量化研究詳細資訊</title>
<style>
:root { color-scheme: dark; --accent: #f0b90b; --bg: #111214; --panel: #1d1e22;
  --line: rgba(255,255,255,.08); --text: #f5f5f7; --muted: #9b9ba3;
  --ok: #30d158; --running: #ffd60a; }
* { box-sizing: border-box; }
body { margin: 0 auto; max-width: 1180px; padding: 20px; background: var(--bg); color: var(--text);
  font: 15px/1.45 -apple-system, BlinkMacSystemFont, system-ui, sans-serif; }
header { margin: 4px 0 18px; }
h1 { font-size: 22px; letter-spacing: -.02em; margin: 0 0 3px; }
.meta { color: var(--muted); font-size: 12px; margin: 0; }
.cards { display: grid; gap: 14px; }
section { min-width: 0; background: var(--panel); border: 1px solid var(--line); border-radius: 18px;
  padding: 15px 16px; scroll-margin-top: 18px; }
section:target { border-color: rgba(240,185,11,.72); box-shadow: 0 0 0 1px rgba(240,185,11,.28); }
h2 { font-size: 12px; letter-spacing: .07em; text-transform: uppercase; color: var(--accent);
  margin: 0 0 10px; padding-left: 8px; border-left: 3px solid var(--accent); border-radius: 2px; }
.summary { margin: 0 0 12px; font-size: 16px; font-weight: 600; letter-spacing: -.01em; }
dl.fields { margin: 0; display: grid; gap: 7px; }
dt { color: var(--muted); font-size: 12px; }
dd { min-width: 0; margin: 0 0 5px; word-break: break-word; }
.secondary { opacity: .72; }
.item { border-top: 1px solid var(--line); margin-top: 10px; padding-top: 10px; }
.item:first-child { border-top: 0; margin-top: 0; padding-top: 0; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 999px; border: 1px solid var(--line);
  font-size: 12px; font-weight: 650; line-height: 1.45; }
.badge.ok { color: var(--ok); background: rgba(48,209,88,.08); border-color: rgba(48,209,88,.22); }
.badge.running { color: var(--running); background: rgba(255,214,10,.08); border-color: rgba(255,214,10,.22); }
.badge.muted, .empty { color: var(--muted); }
.path { color: #c7c7cc; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
.metadata-footer { margin-top: 18px; padding: 12px 2px 2px; border-top: 1px solid var(--line); color: var(--muted); }
.metadata-footer h2 { border-left: 0; padding-left: 0; color: var(--muted); margin-bottom: 8px; }
.metadata-footer .fields { font-size: 12px; }
.leaderboard-list { display: grid; gap: 8px; }
.lb-row { display: grid; grid-template-columns: 48px minmax(130px, 1.1fr) minmax(0, 3fr); gap: 12px;
  align-items: center; border-top: 1px solid var(--line); padding: 10px 0; }
.lb-row:first-child { border-top: 0; padding-top: 2px; }
.lb-rank { color: var(--accent); font-weight: 750; }
.lb-name { font-weight: 700; letter-spacing: .01em; }
.lb-metrics { display: grid; grid-template-columns: repeat(5, minmax(90px, 1fr)); gap: 8px; }
.lb-metrics div { min-width: 0; }
.lb-metrics span { display: block; color: var(--muted); font-size: 11px; margin-bottom: 2px; }
.lb-metrics strong { font-size: 14px; font-weight: 700; overflow-wrap: anywhere; }
.lb-metrics .evidence { color: #c7c7cc; font-size: 12px; }
@media (max-width: 699px) {
  .lb-row { grid-template-columns: 42px minmax(0, 1fr); align-items: start; }
  .lb-metrics { grid-column: 2; grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (min-width: 700px) {
  .cards { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  section.wide { grid-column: 1 / -1; }
  dl.fields { grid-template-columns: minmax(120px, 170px) minmax(0, 1fr); column-gap: 16px; align-items: start; }
  dl.fields > dt, dl.fields > dd { margin: 0; }
}
</style>
</head>
<body>
<header><h1>量化研究詳細資訊</h1><p class="meta">%s</p></header>
<main class="cards">%s</main>
%s
</body>
</html>
"""

MAIN_SECTION_ORDER = ("health", "current", "leaderboard", "funnel", "agent", "sources")
METADATA_KEYS = ("schema_version", "monitoring_only", "scope_note", "generated_at_utc")
WIDE_SECTIONS = {"current", "leaderboard"}
SUMMARY_KEYS = ("summary", "board_summary")
FIELD_PRIORITY = ("status", "stage", "family_id", "symbol", "timeframe", "progress_text",
                  "profile", "profile_status", "card_status")
SECONDARY_KEYS = {"state_path", "results_root", "card_readback", "kanban_task_id", "source"}
PATH_KEYS = {"state_path", "results_root"}
STATUS_KEYS = {"status", "stage", "profile_status", "card_status", "evidence_state"}

SECTION_LABELS = {
    "health": "系統健康",
    "current": "目前研究",
    "leaderboard": "策略排行榜",
    "funnel": "研究漏斗",
    "agent": "任務看板",
    "sources": "資料來源",
    "metadata": "系統資訊",
}
FIELD_LABELS = {
    "status": "狀態", "available": "可用", "source": "來源", "state_path": "狀態檔",
    "active": "目前警示", "active_count": "警示數", "last_check_at_utc": "最後檢查 UTC",
    "last_healthy_at_utc": "最後健康 UTC", "stage": "階段", "family_id": "策略家族",
    "progress_text": "進度", "card_status": "任務狀態", "round_id": "回合", "attempt": "嘗試",
    "note": "說明", "progress_available": "進度可用", "progress_pct": "完成比例",
    "progress_done": "已完成", "progress_total": "總數", "cohort": "幣別／週期",
    "last_activity_utc": "最近活動 UTC", "kanban_task_id": "Kanban 任務", "board": "看板",
    "card_readback": "看板回讀", "count": "總數", "shown": "目前顯示", "as_of_utc": "資料時間 UTC",
    "entries": "策略", "rank": "排名", "sharpe": "Sharpe", "annualized_return": "年化報酬率",
    "max_dd_pct": "最大回撤", "avg_trades_per_year": "年均交易次數",
    "evidence_state": "證據狀態", "top_n": "最多顯示",
    "wiki_brain": "研究池", "reviewed": "已審閱策略", "ingested": "已納入策略",
    "share_pct": "比例", "delta_24h": "24 小時變化", "delta_available": "變化資料可用",
    "backtested": "回測進度", "families": "已完成回測家族", "registered": "已登錄家族",
    "running": "執行中", "blocked": "已阻塞", "results_root": "結果資料",
    "results_root_readable": "結果資料可讀", "leaderboard_as_of_utc": "排行榜時間 UTC",
    "family_created_at_utc": "策略家族建立 UTC", "schema_version": "Schema 版本",
    "monitoring_only": "唯讀監控", "scope_note": "範圍說明", "generated_at_utc": "產生時間 UTC",
}
VALUE_LABELS = {
    "ok": "正常", "healthy": "正常", "pass": "通過", "passed": "通過",
    "yes": "是", "no": "否", "true": "是", "false": "否",
    "not launched": "尚未啟動", "blocked": "需關注", "running": "執行中",
    "unavailable": "尚無資料", "unknown": "未知", "none": "無", "empty": "無",
    "frozen_only": "已凍結證據", "quant-strategy-research": "量化策略研究",
    "no round/attempt directory yet": "尚未建立回合／嘗試目錄",
    "(empty)": "無",
    "research progress snapshot, read-only. not live pnl and not a control plane: this file can start, stop or retry nothing.":
        "研究進度唯讀快照；不是即時損益，也不是控制平面，無法啟動、停止或重試任何任務。",
}


def render_detail(payload):
    """Render one human-readable view of dashboard.json without creating a second truth."""
    generated = _text(payload.get("generated_at_utc"))
    meta = "產生時間 %s &middot; 唯讀研究快照" % generated
    seen = set()
    sections = []
    for key in MAIN_SECTION_ORDER:
        if key in payload:
            sections.append(_section(key, payload[key], key in WIDE_SECTIONS))
            seen.add(key)
    for key, value in payload.items():
        if key not in seen and key not in METADATA_KEYS:
            sections.append(_section(key, value, False))
    return DETAIL_PAGE % (meta, "".join(sections), _metadata_footer(payload))


def _metadata_footer(payload):
    rows = "".join(_row(key, payload[key], force_secondary=True)
                   for key in METADATA_KEYS if key in payload)
    if not rows:
        return ""
    return '<footer id="metadata" class="metadata-footer"><h2>系統資訊</h2>' \
           '<dl class="fields">%s</dl></footer>' % rows


def _section(key, value, wide=False):
    class_attr = ' class="wide"' if wide else ""
    body = _leaderboard_block(value) if key == "leaderboard" and isinstance(value, dict) else _block(value)
    return '<section id="%s"%s><h2>%s</h2>%s</section>' % (
        _anchor(key), class_attr, _label(key), body)


def _leaderboard_block(value):
    entries = value.get("entries") or []
    count = value.get("count")
    top_n = value.get("top_n")
    headline = "目前 %s 個存活策略" % _display_scalar(count if count is not None else len(entries))
    if top_n is not None:
        headline += " · 詳細頁最多顯示前 %s 名" % _display_scalar(top_n)
    rows = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        rank = html.escape(_display_scalar(entry.get("rank"), "—"))
        cohort = html.escape(str(entry.get("cohort") or "—"))
        sharpe = _format_decimal(entry.get("sharpe"))
        annualized = _format_percent(entry.get("annualized_return"))
        max_dd = _format_percent(entry.get("max_dd_pct"))
        trade_frequency = _format_decimal(entry.get("avg_trades_per_year"))
        evidence = _translated_value(entry.get("evidence_state") or "unknown")
        rows.append(
            '<article class="lb-row">'
            '<div class="lb-rank">#%s</div><div class="lb-name">%s</div>'
            '<div class="lb-metrics">'
            '<div><span>夏普</span><strong>%s</strong></div>'
            '<div><span>年化報酬率</span><strong>%s</strong></div>'
            '<div><span>最大回撤</span><strong>%s</strong></div>'
            '<div><span>年均交易次數</span><strong>%s 次/年</strong></div>'
            '<div><span>證據狀態</span><strong class="evidence">%s</strong></div>'
            '</div></article>' % (rank, cohort, sharpe, annualized, max_dd, trade_frequency, html.escape(evidence))
        )
    if not rows:
        return '<p class="empty">尚無排行榜資料</p>'
    return '<p class="summary">%s</p><div class="leaderboard-list">%s</div>' % (
        html.escape(headline), "".join(rows))


def _display_scalar(value, empty="尚無資料"):
    return empty if value is None else str(value)


def _format_decimal(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return "%.2f" % value
    return "—"


def _format_percent(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return "%.2f%%" % (value * 100.0)
    return "—"


def _block(value):
    if isinstance(value, dict):
        summary_key = next((key for key in SUMMARY_KEYS
                            if key in value and not isinstance(value[key], (dict, list))
                            and value[key] not in (None, "")), None)
        # summary/board_summary are redundant derived strings; structured child fields below are the truth.
        summary = ""
        keys = [key for key in value if key != summary_key]
        priority = {key: index for index, key in enumerate(FIELD_PRIORITY)}
        keys.sort(key=lambda key: (priority.get(key, len(FIELD_PRIORITY)), list(value).index(key)))
        rows = "".join(_row(key, value[key]) for key in keys)
        return summary + ("<dl class=\"fields\">%s</dl>" % rows if rows else "")
    if isinstance(value, list):
        if not value:
            return '<p class="empty">無</p>'
        return "".join('<div class="item">%s</div>' % (_block(item) if isinstance(item, (dict, list))
                                                        else _value_html(None, item)) for item in value)
    return '<p class="summary">%s</p>' % _value_html(None, value)


def _row(key, value, force_secondary=False):
    secondary = force_secondary or key in SECONDARY_KEYS
    css = ' class="secondary"' if secondary else ""
    body = _block(value) if isinstance(value, (dict, list)) else _value_html(key, value)
    return "<dt%s>%s</dt><dd%s>%s</dd>" % (css, _label(key), css, body)


def _label(key):
    """Human-facing label for a payload key; raw keys remain unchanged in dashboard.json."""
    label = SECTION_LABELS.get(key) or FIELD_LABELS.get(key)
    if label is None:
        label = str(key).replace("_utc", " UTC").replace("_", " ")
    return html.escape(label)


def _anchor(key):
    """A stable, linkable id for the payload's own key (services.yaml links to these)."""
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in str(key).lower())


def _translated_value(value):
    raw = str(value)
    lowered = raw.strip().lower()
    if lowered.startswith("kanban show ok (status=") and lowered.endswith(")"):
        status = lowered[len("kanban show ok (status="):-1]
        return "Kanban 回讀正常（狀態：%s）" % VALUE_LABELS.get(status, status)
    return VALUE_LABELS.get(lowered, raw)


def _value_html(key, value):
    """Presentation-only scalar formatting; dashboard.json remains the sole truth."""
    if value is None:
        return '<span class="empty">尚無資料</span>'
    if value is True:
        return "是"
    if value is False:
        return "否"
    if key in {"annualized_return", "max_dd_pct"}:
        return html.escape(_format_percent(value))
    if key in {"share_pct", "progress_pct"} and isinstance(value, (int, float)) and not isinstance(value, bool):
        return html.escape("%.1f%%" % value)
    if key == "sharpe":
        return html.escape(_format_decimal(value))
    raw = str(value)
    translated = _translated_value(value)
    text = html.escape(translated)
    if key in PATH_KEYS and raw.startswith("/"):
        return '<span class="path" title="%s">%s</span>' % (
            html.escape(raw, quote=True), html.escape(Path(raw).name or raw))
    if key in STATUS_KEYS:
        lowered = raw.strip().lower()
        if lowered in {"ok", "healthy", "pass", "passed"}:
            badge = "ok"
        elif "running" in lowered:
            badge = "running"
        elif lowered in {"unavailable", "unknown", "not launched", "none", "empty"}:
            badge = "muted"
        else:
            badge = ""
        if badge:
            return '<span class="badge %s">%s</span>' % (badge, text)
    if raw.strip().lower().startswith("unavailable"):
        return '<span class="empty">尚無資料</span>'
    return text


def _text(value):
    """Header-safe scalar text; every payload value is escaped."""
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
