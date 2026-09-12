#!/usr/bin/env bash
# Fetch the Apple-Container-recommended kernel tarball (kata-static 3.32.0 arm64)
# with a segmented download, then verify it against the digest that `container`
# itself pins for `container system kernel set --recommended`.
#
# The digest/URL/member below are the hardcoded defaults in
#   apple/container @1.4.1  Sources/ContainerPersistence/ContainerSystemConfig.swift
#     KernelConfig.defaultBinaryPath = opt/kata/share/kata-containers/vmlinux-6.18.35-197-debug
#     KernelConfig.defaultURL        = .../3.32.0/kata-static-3.32.0-arm64.tar.zst
#     KernelConfig.defaultDigest     = sha256:8736c054d9223974735394f822000823baef509e1c33405ec798240fa9b6e4b5
set -euo pipefail

URL="https://github.com/kata-containers/kata-containers/releases/download/3.32.0/kata-static-3.32.0-arm64.tar.zst"
EXPECTED="8736c054d9223974735394f822000823baef509e1c33405ec798240fa9b6e4b5"
OUT="${1:-/tmp/apple-container-install/kata-static-3.32.0-arm64.tar.zst}"
PARTS=8

mkdir -p "$OUT.parts"
SIZE=$(curl -sIL "$URL" | awk 'BEGIN{IGNORECASE=1} /^content-length:/{v=$2} END{gsub(/\r/,"",v); print v}')
echo "remote size: $SIZE"
CHUNK=$(( (SIZE + PARTS - 1) / PARTS ))

pids=()
for i in $(seq 0 $((PARTS - 1))); do
  START=$(( i * CHUNK ))
  END=$(( START + CHUNK - 1 ))
  if [ "$END" -ge "$SIZE" ]; then END=$(( SIZE - 1 )); fi
  curl -fsL --retry 3 -r "${START}-${END}" -o "$OUT.parts/part.$i" "$URL" &
  pids+=($!)
done
for p in "${pids[@]}"; do wait "$p"; done

: > "$OUT"
for i in $(seq 0 $((PARTS - 1))); do cat "$OUT.parts/part.$i" >> "$OUT"; done
ACTUAL=$(shasum -a 256 "$OUT" | awk '{print $1}')
echo "size:   $(stat -f '%z' "$OUT") (expected $SIZE)"
echo "sha256: $ACTUAL (expected $EXPECTED)"
[ "$ACTUAL" = "$EXPECTED" ] || { echo "DIGEST MISMATCH"; exit 1; }
echo "OK"
