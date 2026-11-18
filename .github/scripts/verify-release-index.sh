#!/usr/bin/env bash
# Resolve the source-pinned usign release key and verify a signed checksum index.
set -Eeuo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  verify-release-index.sh resolve AUTHOR SOURCE_ROOT KEYRING_DIR [GITHUB_ENV]
  verify-release-index.sh verify USIGN_BIN PUBLIC_KEY CHECKSUM_FILE SIGNATURE_FILE
USAGE
  exit 2
}

[[ $# -ge 1 ]] || usage
command="$1"
shift

case "$command" in
  resolve)
    [[ $# -ge 3 && $# -le 4 ]] || usage
    author="$1"
    source_root="$2"
    keyring_dir="$3"
    github_env="${4:-}"

    keyring_makefile="$source_root/package/system/openwrt-keyring/Makefile"
    [[ -f "$keyring_makefile" ]] || {
      echo "Source-pinned openwrt-keyring Makefile is missing: $keyring_makefile" >&2
      exit 1
    }

    keyring_commit="$(sed -n 's/^PKG_SOURCE_VERSION:=//p' "$keyring_makefile" | head -n1)"
    [[ "$keyring_commit" =~ ^[0-9a-f]{40}$ ]] || {
      echo "Unable to resolve a pinned 40-character keyring commit." >&2
      exit 1
    }

    case "$author" in
      immortalwrt) keyring_url="https://github.com/immortalwrt/keyring.git" ;;
      openwrt) keyring_url="https://git.openwrt.org/keyring.git" ;;
      *)
        echo "No trusted signing-keyring repository mapping for '$author'." >&2
        exit 1
        ;;
    esac

    rm -rf "$keyring_dir"
    mkdir -p "$(dirname "$keyring_dir")"
    git init -q "$keyring_dir"
    git -C "$keyring_dir" remote add origin "$keyring_url"
    git -C "$keyring_dir" fetch --quiet --depth 1 origin "$keyring_commit"
    git -C "$keyring_dir" checkout --quiet --detach FETCH_HEAD

    actual_keyring_commit="$(git -C "$keyring_dir" rev-parse HEAD)"
    [[ "$actual_keyring_commit" == "$keyring_commit" ]] || {
      echo "Fetched keyring commit does not match the source-pinned commit." >&2
      exit 1
    }

    usign_key_id="$(grep -oE 'usign/[0-9a-f]{16}' "$keyring_makefile" | head -n1 | cut -d/ -f2)"
    [[ "$usign_key_id" =~ ^[0-9a-f]{16}$ ]] || {
      echo "Unable to resolve the source-pinned $author usign release key." >&2
      exit 1
    }

    public_key="$keyring_dir/usign/$usign_key_id"
    [[ -s "$public_key" ]] || {
      echo "Source-pinned usign public key is missing: $public_key" >&2
      exit 1
    }

    if [[ -n "$github_env" ]]; then
      [[ -n "${GITHUB_ENV:-}" || "$github_env" != "" ]] || true
      {
        printf 'KEYRING_DIR=%s\n' "$keyring_dir"
        printf 'KEYRING_COMMIT=%s\n' "$keyring_commit"
        printf 'KEYRING_USIGN_KEY=%s\n' "$public_key"
      } >> "$github_env"
    fi

    printf '%s\n' "$public_key"
    ;;

  verify)
    [[ $# -eq 4 ]] || usage
    usign_bin="$1"
    public_key="$2"
    checksum_file="$3"
    signature_file="$4"

    [[ -x "$usign_bin" ]] || {
      echo "usign verifier is missing or not executable: $usign_bin" >&2
      exit 1
    }
    [[ -s "$public_key" ]] || {
      echo "usign public key is missing or empty: $public_key" >&2
      exit 1
    }
    [[ -s "$checksum_file" ]] || {
      echo "Signed checksum index is missing or empty: $checksum_file" >&2
      exit 1
    }
    [[ -s "$signature_file" ]] || {
      echo "Checksum index signature is missing or empty: $signature_file" >&2
      exit 1
    }

    "$usign_bin" -V -m "$checksum_file" -p "$public_key" -x "$signature_file"
    ;;

  *)
    usage
    ;;
esac
