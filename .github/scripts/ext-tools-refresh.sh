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

# OpenWrt's timestamp.pl compares whole-second mtimes. Refresh in dependency
# order and cross a second boundary between stamp classes so ties are avoided.
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

# The package source tree has already been checked out and all custom scripts,
# feed changes and defconfig steps have completed before this helper is called.
find -H "$HOST_BUILD_DIR" -type f -print0 |
  xargs -0 -r touch

# .prepared is checked against source Makefiles and PKG_FILE_DEPENDS.
sleep 1
find -H "$HOST_BUILD_DIR" -type f -name '.prepared*' -print0 |
  xargs -0 -r touch

# Preserve the HostBuild dependency chain: .configured newer than .prepared.
sleep 1
find -H "$HOST_BUILD_DIR" -type f -name .configured -print0 |
  xargs -0 -r touch

# Give each nesting depth a distinct timestamp, deepest first. Stamps at the
# same depth are siblings; an ancestor .built is always newer than every
# nested package's outputs and .built stamp.
previous_depth=""
for stamp in "${BUILT_STAMPS[@]}"; do
  slash_count="${stamp//[^\/]/}"
  depth="${#slash_count}"
  if [[ "$depth" != "$previous_depth" ]]; then
    sleep 1
    previous_depth="$depth"
  fi
  touch "$stamp"
done

# Verify the exact newest-file test used by include/host-build.mk's rdep.
for stamp in "${BUILT_STAMPS[@]}"; do
  package_dir="${stamp%/.built}"
  if ! perl "$TIMESTAMP_SCRIPT" -n "$stamp" "$package_dir"; then
    echo "Prebuilt host package stamp is not the newest file: $stamp" >&2
    exit 1
  fi
done

# Installed-tool stamps are touched after .built and remain in the past by the
# time make tools/install starts, so a real rebuild can invalidate them.
sleep 1
find -H "$HOST_STAGING_DIR_STAMP" -type f -print0 |
  xargs -0 -r touch

echo "Refreshed and verified $built_count prebuilt host package .built stamps."
