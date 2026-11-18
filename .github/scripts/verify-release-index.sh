#!/usr/bin/env bash
# Resolve source-pinned release signing material and verify a signed checksum index.
set -Eeuo pipefail
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
    author="$1"; source_root="$2"; keyring_dir="$3"; github_env="${4:-}"
    keyring_makefile="$source_root/package/system/openwrt-keyring/Makefile"
    [[ -f "$keyring_makefile" ]] || { echo "Source-pinned openwrt-keyring Makefile is missing: $keyring_makefile" >&2; exit 1; }
    keyring_commit="$(sed -n 's/^PKG_SOURCE_VERSION:=//p' "$keyring_makefile" | head -n1)"
    [[ "$keyring_commit" =~ ^[0-9a-f]{40}$ ]] || { echo "Unable to resolve a pinned 40-character keyring commit." >&2; exit 1; }
    case "$author" in
      immortalwrt) keyring_url="https://github.com/immortalwrt/keyring.git" ;;
      openwrt) keyring_url="https://git.openwrt.org/keyring.git" ;;
      *) echo "No trusted signing-keyring repository mapping for '$author'." >&2; exit 1 ;;
    esac
    rm -rf "$keyring_dir"
    mkdir -p "$(dirname "$keyring_dir")"
    git init -q "$keyring_dir"
    git -C "$keyring_dir" remote add origin "$keyring_url"
    git -C "$keyring_dir" fetch --quiet --depth 1 origin "$keyring_commit"
    git -C "$keyring_dir" checkout --quiet --detach FETCH_HEAD
    actual_keyring_commit="$(git -C "$keyring_dir" rev-parse HEAD)"
    [[ "$actual_keyring_commit" == "$keyring_commit" ]] || { echo "Fetched keyring commit does not match the source-pinned commit." >&2; exit 1; }

    usign_public_key=""
    gpg_keyring_dir=""
    case "$author" in
      immortalwrt)
        usign_key_id="$(grep -oE 'usign/[0-9a-f]{16}' "$keyring_makefile" | head -n1 | cut -d/ -f2)"
        [[ "$usign_key_id" =~ ^[0-9a-f]{16}$ ]] || { echo "Unable to resolve the source-pinned ImmortalWrt usign release key." >&2; exit 1; }
        usign_public_key="$keyring_dir/usign/$usign_key_id"
        [[ -s "$usign_public_key" ]] || { echo "Source-pinned usign public key is missing: $usign_public_key" >&2; exit 1; }
        ;;
      openwrt)
        # Match the GPG keys imported by OpenWrt's reusable_build.yml.
        gpg_keyring_dir="$keyring_dir/gpg"
        for key in CD54E82DADB3684D.asc 0x1D53D1877742E911.asc 626471F1.asc; do
          [[ -s "$gpg_keyring_dir/$key" ]] || { echo "Source-pinned OpenWrt GPG key is missing: $gpg_keyring_dir/$key" >&2; exit 1; }
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
    if [[ "$author" == "immortalwrt" ]]; then printf '%s\n' "$usign_public_key"; else printf '%s\n' "$gpg_keyring_dir"; fi
    ;;
  verify)
    [[ $# -eq 6 ]] || usage
    author="$1"; usign_bin="$2"; public_key="$3"; gpg_keyring_dir="$4"; checksum_file="$5"; signature_file="$6"
    [[ -s "$checksum_file" ]] || { echo "Signed checksum index is missing or empty: $checksum_file" >&2; exit 1; }
    [[ -s "$signature_file" ]] || { echo "Checksum index signature is missing or empty: $signature_file" >&2; exit 1; }
    case "$author" in
      immortalwrt)
        [[ -x "$usign_bin" ]] || { echo "usign verifier is missing or not executable: $usign_bin" >&2; exit 1; }
        [[ -s "$public_key" ]] || { echo "usign public key is missing or empty: $public_key" >&2; exit 1; }
        "$usign_bin" -V -m "$checksum_file" -p "$public_key" -x "$signature_file"
        ;;
      openwrt)
        [[ -d "$gpg_keyring_dir" ]] || { echo "OpenWrt GPG keyring directory is missing: $gpg_keyring_dir" >&2; exit 1; }
        gnupg_home="$(mktemp -d)"
        trap 'rm -rf "$gnupg_home"' EXIT
        chmod 700 "$gnupg_home"
        gpg --homedir "$gnupg_home" --batch --import \
          "$gpg_keyring_dir/CD54E82DADB3684D.asc" \
          "$gpg_keyring_dir/0x1D53D1877742E911.asc" \
          "$gpg_keyring_dir/626471F1.asc"
        gpg --homedir "$gnupg_home" --with-fingerprint --verify "$signature_file"
        ;;
      *) echo "No release signature verifier for '$author'." >&2; exit 1 ;;
    esac
    ;;
  *) usage ;;
esac
