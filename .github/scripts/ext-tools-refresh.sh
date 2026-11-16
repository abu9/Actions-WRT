#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 || "$1" != "--refresh" ]]; then
  echo "Usage: $0 --refresh" >&2
  exit 2
fi

# Use the implementation maintained alongside this exact OpenWrt source tree.
# It refreshes only empty stamp files under build_dir/host and
# staging_dir/host/stamp, leaving the actual prebuilt outputs untouched.
if [[ ! -f ./scripts/ext-tools.sh ]]; then
  echo "OpenWrt scripts/ext-tools.sh not found; run this helper from the OpenWrt source root." >&2
  exit 1
fi

exec bash ./scripts/ext-tools.sh --refresh
