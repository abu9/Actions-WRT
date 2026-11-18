#!/usr/bin/env bash
# Resolve trusted distribution key material from an independently checked-out keyring.
# Verify the detached release checksum index before sha256sum checks any archive.
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  verify-release-index.sh resolve AUTHOR SOURCE_ROOT KEYRING_DIR [GITHUB_ENV]
  verify-release-index.sh verify AUTHOR USIGN_BIN PUBLIC_KEY GPG_KEYRING_DIR CHECKSUM_FILE SIGNATURE_FILE
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

    case "$author" in
      immortalwrt) expected_origin="https://github.com/immortalwrt/keyring" ;;
      openwrt) expected_origin="https://github.com/openwrt/keyring" ;;
      *) echo "No trusted signing-keyring repository mapping for '$author'." >&2; exit 1 ;;
    esac

    [[ -d "$keyring_dir/.git" ]] || {
      echo "Official $author keyring checkout is missing: $keyring_dir" >&2
      exit 1
    }

    actual_origin="$(git -C "$keyring_dir" remote get-url origin 2>/dev/null || true)"
    case "$actual_origin" in
      "$expected_origin"|"$expected_origin.git") ;;
      *)
        echo "Unexpected $author keyring origin: ${actual_origin:-<missing>}" >&2
        echo "Expected: $expected_origin" >&2
        exit 1
        ;;
    esac

    keyring_commit="$(git -C "$keyring_dir" rev-parse HEAD)"
    [[ "$keyring_commit" =~ ^[0-9a-f]{40}$ ]] || {
      echo "Unable to resolve the checked-out $author keyring commit." >&2
      exit 1
    }

    usign_public_key=""
    gpg_keyring_dir=""
    case "$author" in
      immortalwrt)
        keyring_makefile="$source_root/package/system/openwrt-keyring/Makefile"
        [[ -f "$keyring_makefile" ]] || {
          echo "ImmortalWrt openwrt-keyring Makefile is missing: $keyring_makefile" >&2
          exit 1
        }

        mapfile -t usign_key_ids < <(
          grep -oE 'usign/[0-9a-f]{16}' "$keyring_makefile" |
            cut -d/ -f2 |
            sort -u
        )
        [[ ${#usign_key_ids[@]} -eq 1 ]] || {
          echo "Expected exactly one ImmortalWrt usign release key ID in the source keyring Makefile." >&2
          exit 1
        }

        usign_public_key="$keyring_dir/usign/${usign_key_ids[0]}"
        [[ -s "$usign_public_key" ]] || {
          echo "The selected ImmortalWrt key is not present in the official keyring checkout: $usign_public_key" >&2
          exit 1
        }
        ;;
      openwrt)
        # Match the exact GPG key files imported by OpenWrt's reusable_build.yml.
        gpg_keyring_dir="$keyring_dir/gpg"
        for key in CD54E82DADB3684D.asc 0x1D53D1877742E911.asc 626471F1.asc; do
          [[ -s "$gpg_keyring_dir/$key" ]] || {
            echo "Required OpenWrt GPG key is missing from the official keyring checkout: $gpg_keyring_dir/$key" >&2
            exit 1
          }
        done
        ;;
    esac

    if [[ -n "$github_env" ]]; then
      {
        printf 'KEYRING_DIR=%s\n' "$keyring_dir"
        printf 'KEYRING_COMMIT=%s\n' "$keyring_commit"
        printf 'KEYRING_USIGN_KEY=%s\n' "$usign_public_key"
        printf 'KEYRING_GPG_DIR=%s\n' "$gpg_keyring_dir"
      } >> "$github_env"
    fi

    if [[ "$author" == "immortalwrt" ]]; then
      printf '%s\n' "$usign_public_key"
    else
      printf '%s\n' "$gpg_keyring_dir"
    fi
    ;;

  verify)
    [[ $# -eq 6 ]] || usage
    author="$1"
    usign_bin="$2"
    public_key="$3"
    gpg_keyring_dir="$4"
    checksum_file="$5"
    signature_file="$6"

    [[ -s "$checksum_file" ]] || {
      echo "Signed checksum index is missing or empty: $checksum_file" >&2
      exit 1
    }
    [[ -s "$signature_file" ]] || {
      echo "Checksum index signature is missing or empty: $signature_file" >&2
      exit 1
    }

    case "$author" in
      immortalwrt)
        [[ -x "$usign_bin" ]] || {
          echo "usign verifier is missing or not executable: $usign_bin" >&2
          exit 1
        }
        [[ -s "$public_key" ]] || {
          echo "usign public key is missing or empty: $public_key" >&2
          exit 1
        }
        "$usign_bin" -V -m "$checksum_file" -p "$public_key" -x "$signature_file"
        ;;
      openwrt)
        [[ -d "$gpg_keyring_dir" ]] || {
          echo "OpenWrt GPG keyring directory is missing: $gpg_keyring_dir" >&2
          exit 1
        }
        gnupg_home="$(mktemp -d)"
        trap 'rm -rf "$gnupg_home"' EXIT
        chmod 700 "$gnupg_home"
        gpg --homedir "$gnupg_home" --batch --no-tty --import \
          "$gpg_keyring_dir/CD54E82DADB3684D.asc" \
          "$gpg_keyring_dir/0x1D53D1877742E911.asc" \
          "$gpg_keyring_dir/626471F1.asc"
        # Pass both files explicitly so GPG verifies this exact downloaded index.
        gpg --homedir "$gnupg_home" --batch --no-tty --with-fingerprint \
          --verify "$signature_file" "$checksum_file"
        ;;
      *)
        echo "No release signature verifier for '$author'." >&2
        exit 1
        ;;
    esac
    ;;
  *)
    usage
    ;;
esac
