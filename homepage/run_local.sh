#!/bin/sh
# Local launcher for the read-only quant dashboard (Homepage + its JSON server).
#
# Both processes bind 127.0.0.1 only and are viewers: the server exposes one allowlisted file, the
# dashboard renders it. Neither can start, stop, retry or unblock anything - the quant runtime does
# not depend on them, and stopping them changes nothing except the display.  The launcher judges each
# port by its actual listening socket (`lsof`), not by an HTTP probe or the host env var, and refuses
# any listener that is not 127.0.0.1 - wildcard (`*:PORT`) and IPv6 (`[::1]:PORT`) included.
#
#   sh homepage/run_local.sh          # start what is not already listening
#   sh homepage/run_local.sh stop     # stop both
#
# The payload itself is written by the hourly snapshot job (quant_candidate_snapshot.py); to refresh
# it by hand:  /opt/homebrew/bin/python3 runtime/candidate_snapshot.py \
#                 --dashboard-json ~/quant-dashboard/data/dashboard.json
set -eu

REPO="$(cd "$(dirname "$0")/.." && pwd)"
APP="${HOMEPAGE_APP:-$HOME/workspace/quant-homepage-app}"
DATA="${QUANT_DASHBOARD_DIR:-$HOME/quant-dashboard/data}"
RUN="${QUANT_DASHBOARD_RUN:-$HOME/quant-dashboard/run}"
PAYLOAD_PORT="${QUANT_DASHBOARD_PORT:-8787}"
WEB_PORT="${HOMEPAGE_PORT:-3000}"
PYTHON="${PYTHON:-/opt/homebrew/bin/python3}"

mkdir -p "$DATA" "$RUN"

if [ "${1:-start}" = "stop" ]; then
  for name in homepage dashboard_serve; do
    if [ -f "$RUN/$name.pid" ]; then
      kill "$(cat "$RUN/$name.pid")" 2>/dev/null || true
      rm -f "$RUN/$name.pid"
      echo "stopped $name"
    fi
  done
  exit 0
fi

# Ports are judged by their real listeners, never by a reachability probe: a wildcard (`*:PORT`) or
# IPv6 (`[::1]:PORT`) bind answers on 127.0.0.1 too, so a successful HTTP probe proves nothing about
# the bind.  Both paths - a process this script just started, and one that was already running -
# are asserted unconditionally, and any listener that is not 127.0.0.1 is a refusal.
START_WAIT="${START_WAIT:-10}"

listeners() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN -F n 2>/dev/null | sed -n 's/^n//p' | sort -u
}

# A process that was just started needs a moment to bind; the wait only buys time for a listener to
# appear, it never excuses one that is not loopback (that is checked next, and refused at once).
wait_for_listener() {
  waited=0
  while [ -z "$(listeners "$1")" ] && [ "$waited" -lt "$START_WAIT" ]; do
    sleep 1
    waited=$((waited + 1))
  done
}

require_loopback() {
  found="$(listeners "$1")"
  if [ -z "$found" ]; then
    echo "refusing: nothing is listening on 127.0.0.1:$1" >&2
    return 1
  fi
  for sock in $found; do
    if [ "$sock" != "127.0.0.1:$1" ]; then
      echo "refusing: port $1 is bound by '$sock', not 127.0.0.1 only:" >&2
      lsof -nP -iTCP:"$1" -sTCP:LISTEN >&2 || true
      return 1
    fi
  done
  return 0
}

if [ -z "$(listeners "$PAYLOAD_PORT")" ]; then
  nohup "$PYTHON" "$REPO/runtime/dashboard_serve.py" --data-dir "$DATA" --port "$PAYLOAD_PORT" \
    >"$RUN/dashboard_serve.log" 2>&1 &
  echo $! >"$RUN/dashboard_serve.pid"
  echo "started dashboard_serve (pid $(cat "$RUN/dashboard_serve.pid"))"
  wait_for_listener "$PAYLOAD_PORT"
fi
require_loopback "$PAYLOAD_PORT" || exit 1

if [ -z "$(listeners "$WEB_PORT")" ]; then
  cd "$APP"
  # Bind, not just a Host-header guard: `next start` defaults to hostname 0.0.0.0, while the
  # standalone server (the build is `output: standalone`) takes the host from HOSTNAME - which is
  # also the invocation Next asks for, so the "output: standalone" warning goes away.
  # HOMEPAGE_ALLOWED_HOSTS stays as the Host-header guard; it proves nothing about the socket.
  HOMEPAGE_CONFIG_DIR="$REPO/homepage" \
  HOMEPAGE_ALLOWED_HOSTS="localhost:$WEB_PORT,127.0.0.1:$WEB_PORT" \
  HOSTNAME=127.0.0.1 \
  PORT="$WEB_PORT" \
    nohup node "$APP/.next/standalone/server.js" >"$RUN/homepage.log" 2>&1 &
  echo $! >"$RUN/homepage.pid"
  echo "started homepage (pid $(cat "$RUN/homepage.pid"))"
  wait_for_listener "$WEB_PORT"
fi
require_loopback "$WEB_PORT" || exit 1

echo "dashboard:  http://localhost:$WEB_PORT/"
echo "payload:    http://127.0.0.1:$PAYLOAD_PORT/dashboard.json"
