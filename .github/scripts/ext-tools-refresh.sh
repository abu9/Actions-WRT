#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 || "$1" != "--refresh" ]]; then
  echo "Usage: $0 --refresh" >&2
  exit 2
fi

TOPDIR="\${OPENWRT_TOPDIR:-$(pwd)}"
HOST_BUILD_DIR="\${TOPDIR}/build_dir/host"
HOST_STAGING_DIR_STAMP="\${TOPDIR}/staging_dir/host/stamp"
TIMESTAMP_SCRIPT="\${TOPDIR}/scripts/timestamp.pl"

for path in "$HOST_BUILD_DIR" "$HOST_STAGING_DIR_STAMP" "$TIMESTAMP_SCRIPT"; do
  if [[ ! -e "$path" ]]; then
    echo "Required prebuilt-tools path is missing: $path" >&2
    exit 1
  fi
done

refresh_files() {
  find -H "$1" -type f -print0 | xargs -0 -r touch
}

# First refresh all restored host build outputs. OpenWrt's timestamp.pl
# selects the newest file below each package build directory.
refresh_files "$HOST_BUILD_DIR"

# timestamp.pl reports whole-second mtimes. Put every package's .built
# stamp in a later second so it is unambiguously the newest file.
sleep 1
find -H "$HOST_BUILD_DIR" -type f -name .built -print0 | xargs -0 -r touch

# Verify the exact condition used by include/host-build.mk's rdep check.
built_count=0
while IFS= read -r -d '' stamp; do
  built_count=$((built_count + 1))
  package_dir="\${stamp%/.built}"
  if ! perl "$TIMESTAMP_SCRIPT" -n "$stamp" "$package_dir"; then
    echo "Prebuilt host package stamp is not the newest file: $stamp" >&2
    exit 1
  fi
done < <(find -H "$HOST_BUILD_DIR" -type f -name .built -print0)

if [[ "$built_count" -eq 0 ]]; then
  echo "No .built stamps found under $HOST_BUILD_DIR; refusing to reuse an empty host-tools artifact." >&2
  exit 1
fi

# Refresh installed-tool stamps only after package build stamps have been
# made newest within their own package directories.
sleep 1
refresh_files "$HOST_STAGING_DIR_STAMP"

echo "Refreshed and verified $built_count prebuilt host package .built stamps."
