#!/usr/bin/env bash
# Phase 4 — mounts / permissions (resume of t_517161ee after the Full Disk Access fix).
# Creates the runtime container with the three required mounts and proves the
# read-only / read-write semantics of each.  Local-only, task-scoped artifacts.
set -u
export PATH="$HOME/.local/bin:$PATH"
WORK=/Users/hong/workspace/qlib-apple-container
RAW=/Volumes/ExpansionDrive/market-data-raw
RES=/Volumes/ExpansionDrive/qlib-results
SCRIPTS=$WORK/scripts
OUT=$WORK/evidence
mkdir -p "$OUT"
exec > >(tee "$OUT/phase4.log") 2>&1

echo "=== $(date -u +%FT%TZ) host-side raw baseline BEFORE phase 4 ==="
find "$RAW" -type f -exec stat -f '%N|%z|%m' {} + | sort > /tmp/raw-pre-phase4-files.txt
shasum -a 256 /tmp/raw-pre-phase4-files.txt
wc -l < /tmp/raw-pre-phase4-files.txt

echo "=== stop + delete the old container (holds qlib-work) ==="
container stop qlib-dev; echo "stop_exit=$?"
container delete qlib-dev; echo "delete_exit=$?"

echo "=== create qlib-run with the three required mounts ==="
container run -d --name qlib-run -c 6 -m 4g \
  -v "$RAW":/data/raw:ro \
  -v qlib-work:/qlib/work \
  -v "$RES":/results \
  -v "$SCRIPTS":/scripts:ro \
  qlib:0.9.7-arm64
echo "run_exit=$?"

for i in $(seq 1 40); do
  if container list 2>/dev/null | grep -q '^qlib-run '; then
    echo "qlib-run is RUNNING (poll $i)"; break
  fi
  echo "poll $i: not running yet"
  sleep 2
done

echo "=== container list ==="
container list --all

echo "=== mount table inside the container ==="
container exec qlib-run sh -c 'cat /proc/mounts | grep -E " /data/raw | /qlib/work | /results | /scripts "'

echo "=== /data/raw must be READABLE ==="
container exec qlib-run sh -c 'ls -la /data/raw; echo; ls -la /data/raw/binance/usdm/klines/BTCUSDT/1h | head -5; echo; ls /data/raw/binance/usdm/klines/BTCUSDT/1h | wc -l'
echo "read_exit=$?"

echo "=== /data/raw must DENY create (expected EROFS) ==="
container exec qlib-run sh -c 'touch /data/raw/__ro_probe__ ; echo "touch_exit=$?"'
container exec qlib-run sh -c 'mkdir /data/raw/__ro_probe_dir__ ; echo "mkdir_exit=$?"'
container exec qlib-run sh -c 'dd if=/dev/zero of=/data/raw/__ro_probe__ bs=1 count=1 2>&1; echo "dd_exit=$?"'
echo "--- modify/delete denial on a ro VirtioFS mount, proven on our own /scripts "
echo "    (deliberately NOT attempted on real raw payload: a wrongly-rw mount would corrupt it) ---"
container exec qlib-run sh -c 'touch /scripts/__ro_probe__ ; echo "touch_exit=$?"'
container exec qlib-run sh -c 'chmod 600 /scripts/00_env_baseline.py ; echo "chmod_exit=$?"'
container exec qlib-run sh -c 'rm -f /scripts/00_env_baseline.py ; echo "rm_exit=$?"'
container exec qlib-run sh -c 'printf x >> /scripts/00_env_baseline.py ; echo "append_exit=$?"'
echo "--- did the create attempts leave anything? ---"
container exec qlib-run sh -c 'ls -la /data/raw/ | head; ls -la /data/raw/_meta | head'

echo "=== /qlib/work must be READ/WRITE ==="
container exec qlib-run sh -c 'echo "phase4-rw-probe $(date -u +%FT%TZ)" > /qlib/work/rw-probe.txt && cat /qlib/work/rw-probe.txt && ls -la /qlib/work'

echo "=== /results must be READ/WRITE (test file removed afterwards) ==="
container exec qlib-run sh -c 'echo "phase4-results-probe $(date -u +%FT%TZ)" > /results/__phase4_probe__.txt && cat /results/__phase4_probe__.txt'
container exec qlib-run sh -c 'sync; rm -f /results/__phase4_probe__.txt; ls -la /results; echo "removed=$(test -e /results/__phase4_probe__.txt && echo NO || echo YES)"'

echo "=== host-side raw baseline AFTER phase 4 ==="
find "$RAW" -type f -exec stat -f '%N|%z|%m' {} + | sort > /tmp/raw-post-phase4-files.txt
shasum -a 256 /tmp/raw-post-phase4-files.txt
if diff -q /tmp/raw-pre-phase4-files.txt /tmp/raw-post-phase4-files.txt >/dev/null; then
  echo "raw_baseline: IDENTICAL"
else
  echo "raw_baseline: *** DIFFERS ***"
  diff /tmp/raw-pre-phase4-files.txt /tmp/raw-post-phase4-files.txt | head -20
fi

echo "=== staging host-side (proves /results write landed on ExpansionDrive) ==="
ls -la "$RES"

echo "=== DONE $(date -u +%FT%TZ) ==="
