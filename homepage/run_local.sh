#!/bin/sh
# Local launcher for the read-only quant dashboard (Homepage + its JSON server).
#
# Both processes bind 127.0.0.1 only and are viewers: the server exposes one allowlisted file, the
# dashboard renders it. Neither can start, stop, retry or unblock anything - the quant runtime does
# not depend on them, and stopping them changes nothing except the display.
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

if ! curl -sf "http://127.0.0.1:$PAYLOAD_PORT/dashboard.json" >/dev/null 2>&1; then
  nohup "$PYTHON" "$REPO/runtime/dashboard_serve.py" --data-dir "$DATA" --port "$PAYLOAD_PORT" \
    >"$RUN/dashboard_serve.log" 2>&1 &
  echo $! >"$RUN/dashboard_serve.pid"
  echo "started dashboard_serve (pid $(cat "$RUN/dashboard_serve.pid"))"
fi

if ! curl -sf "http://127.0.0.1:$WEB_PORT/" >/dev/null 2>&1; then
  cd "$APP"
  HOMEPAGE_CONFIG_DIR="$REPO/homepage" \
  HOMEPAGE_ALLOWED_HOSTS="localhost:$WEB_PORT,127.0.0.1:$WEB_PORT" \
  PORT="$WEB_PORT" \
    nohup pnpm start >"$RUN/homepage.log" 2>&1 &
  echo $! >"$RUN/homepage.pid"
  echo "started homepage (pid $(cat "$RUN/homepage.pid"))"
fi

echo "dashboard:  http://localhost:$WEB_PORT/"
echo "payload:    http://127.0.0.1:$PAYLOAD_PORT/dashboard.json"
