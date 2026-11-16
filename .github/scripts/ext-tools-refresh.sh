#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 || "$1" != "--refresh" ]]; then
  echo "Usage: $0 --refresh" >&2
  exit 2
fi

TOPDIR="${OPENWRT_TOPDIR:-$(pwd)}"
HOST_BUILD_DIR="${TOPDIR}/build_dir/host"
HOST_STAGING_DIR_STAMP="${TOPDIR}/staging_dir/host/stamp"
TIMESTAMP_SCRIPT="${TOPDIR}/scripts/timestamp.pl"

for path in "$HOST_BUILD_DIR" "$HOST_STAGING_DIR_STAMP" "$TIMESTAMP_SCRIPT"; do
  if [[ ! -e "$path" ]]; then
    echo "Required prebuilt-tools path is missing: $path" >&2
    exit 1
  fi
done

# OpenWrt's timestamp.pl compares whole-second mtimes. Set all restored host
# build outputs to one baseline, then assign strictly increasing timestamps
# to .built markers from deepest package directory to shallowest. This also
# handles nested host-build directories without ties between parent/child
# stamps, and keeps the final timestamp at or before the current time.
mapfile -d '' BUILT_STAMPS < <(
  find -H "$HOST_BUILD_DIR" -type f -name .built -print0 |
    python3 -c '
import sys
paths = [p for p in sys.stdin.buffer.read().split(b"\0") if p]
paths.sort(key=lambda p: (p.count(b"/"), p), reverse=True)
if paths:
    sys.stdout.buffer.write(b"\0".join(paths) + b"\0")
'
)

built_count="${#BUILT_STAMPS[@]}"
if [[ "$built_count" -eq 0 ]]; then
  echo "No .built stamps found under $HOST_BUILD_DIR; refusing to reuse an empty host-tools artifact." >&2
  exit 1
fi

now_epoch="$(date +%s)"
base_epoch="$((now_epoch - built_count - 2))"
find -H "$HOST_BUILD_DIR" -type f -print0 |
  xargs -0 -r touch -d "@$base_epoch"

stamp_epoch="$base_epoch"
for stamp in "${BUILT_STAMPS[@]}"; do
  stamp_epoch="$((stamp_epoch + 1))"
  touch -d "@$stamp_epoch" "$stamp"
done

# Verify the exact newest-file test used by include/host-build.mk's rdep.
for stamp in "${BUILT_STAMPS[@]}"; do
  package_dir="${stamp%/.built}"
  if ! perl "$TIMESTAMP_SCRIPT" -n "$stamp" "$package_dir"; then
    echo "Prebuilt host package stamp is not the newest file: $stamp" >&2
    exit 1
  fi
done

# Staging stamps are refreshed after all package .built stamps.
find -H "$HOST_STAGING_DIR_STAMP" -type f -print0 |
  xargs -0 -r touch

echo "Refreshed and verified $built_count prebuilt host package .built stamps."
