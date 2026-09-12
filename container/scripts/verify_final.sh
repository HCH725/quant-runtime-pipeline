#!/usr/bin/env bash
# Final independent read-back of the durable state (reruns the smallest executable checks).
set -u
export PATH="$HOME/.local/bin:$PATH"
WORK=/Users/hong/workspace/qlib-apple-container
RES=/Volumes/ExpansionDrive/qlib-results
RUN_HOST=$RES/qlib-smoke-20260912
OUT=$WORK/evidence
exec > >(tee "$OUT/final-verification.log") 2>&1

echo "=== $(date -u +%FT%TZ) FINAL VERIFICATION ==="

echo "-- A: container system + version --"
container system status | grep -E '^(status|client.version|server.version|host.architecture)'
echo "-- A: container list --"
container list --all
echo "-- A: native arch inside the container --"
container exec qlib-run uname -m
container exec qlib-run uname -a | cut -c1-80

echo "-- B: qlib version / python arch (fresh process) --"
container exec qlib-run /opt/venv/bin/python -c 'import qlib,sys,platform;print("qlib",qlib.__version__,"| python",platform.python_version(),"| machine",platform.machine(),"| exe",sys.executable)'

echo "-- C: raw mount ro + readable --"
container exec qlib-run sh -c 'grep " /data/raw " /proc/mounts'
container exec qlib-run sh -c 'ls /data/raw; ls /data/raw/binance/usdm/klines/BTCUSDT/1h | wc -l'
container exec qlib-run sh -c 'touch /data/raw/__final_probe__ 2>&1; echo "write_denied_exit=$?"'

echo "-- D: qlib-work volume --"
container exec qlib-run sh -c 'df -h /qlib/work | tail -1'
container volume inspect qlib-work | grep -E '"(size|sizeInBytes|format|source)"'

echo "-- E/F: BTCUSDT 1h sample really read through the qlib data layer --"
container exec qlib-run /opt/venv/bin/python /scripts/03_qlib_smoke.py /qlib/work/qlib-data-btcusdt-2024Q1 \
  "2024-01-01 00:00:00" "2024-04-01 00:00:00" /qlib/work/final-qlib-read.json | grep -E '"(rows|calendar_len|monotonic_increasing|calendar_duplicates|null_cells)"'

echo "-- G/H: results store on ExpansionDrive --"
ls -la "$RUN_HOST"
ls -la "$RUN_HOST/phase7-research-smoke"
du -sh "$RES"

echo "-- raw store unchanged --"
find /Volumes/ExpansionDrive/market-data-raw -type f -exec stat -f '%N|%z|%m' {} + | sort > /tmp/raw-final.txt
shasum -a 256 /tmp/raw-final.txt
diff -q /tmp/raw-pre-phase4-files.txt /tmp/raw-final.txt >/dev/null && echo "raw_baseline: IDENTICAL" || echo "raw_baseline: *** DIFFERS ***"
launchctl list ai.marketdata.raw-sync

echo "=== DONE $(date -u +%FT%TZ) ==="
