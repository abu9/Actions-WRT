#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
WORKFLOW="$ROOT/.github/workflows/reusable_build.yml"
PASS=0
TEST_ROOT=
fail(){ echo "FAIL: $*" >&2; exit 1; }
assert_has(){ grep -Fq -- "$2" "$1" || fail "missing '$2' in $1"; }
assert_lacks(){ ! grep -Fq -- "$2" "$1" || fail "unexpected '$2' in $1"; }
extract_step(){
  awk -v wanted="$1" '
    $0 == "      - name: " wanted { found=1; next }
    found && /^      - name: / { exit }
    found && /^        run: [|]$/ { in_run=1; next }
    in_run && /^          / { sub(/^          /,""); print; next }
    in_run && /^[[:space:]]*$/ { print ""; next }
    in_run { exit }
  ' "$WORKFLOW"
}
RESOLVE_CODE="$(extract_step 'Resolve official release signing material')"
VERIFY_CODE="$(extract_step 'Download external toolchain/sdk' | sed -n '/^test -s sha256sums/,$p')"
[[ -n "$RESOLVE_CODE" && -n "$VERIFY_CODE" ]] || fail 'could not extract inline release verification steps'
run_test(){ "$2"; PASS=$((PASS+1)); printf 'ok %02d - %s\n' "$PASS" "$1"; [[ -z "$TEST_ROOT" ]] || rm -rf "$TEST_ROOT"; TEST_ROOT=; }
make_keyring(){
  local dir="$1" origin="$2"
  mkdir -p "$dir"
  git -C "$dir" init -q
  git -C "$dir" config user.email ci-test@example.invalid
  git -C "$dir" config user.name 'Actions-WRT CI'
  git -C "$dir" remote add origin "$origin"
  git -C "$dir" add -A
  git -C "$dir" commit -qm fixture
}
prepare_workspace(){
  TEST_ROOT="$(mktemp -d)"
  mkdir -p "$TEST_ROOT/openwrt/package/system/openwrt-keyring" "$TEST_ROOT/.ci-keyring"
}
test_workflow_contract(){
  assert_has "$WORKFLOW" 'repository: openwrt/keyring'
  assert_has "$WORKFLOW" 'repository: immortalwrt/keyring'
  assert_has "$WORKFLOW" 'immortalwrt) SIGNATURE_FILE=sha256sums.sig'
  assert_has "$WORKFLOW" 'openwrt) SIGNATURE_FILE=sha256sums.asc'
  assert_has "$WORKFLOW" 'CHECKSUM_OUTPUT="$(sha256sum --check --ignore-missing sha256sums)"'
  assert_has "$WORKFLOW" 'grep -Fq "$TOOLCHAIN_ARCHIVE: OK" <<<"$CHECKSUM_OUTPUT"'
  assert_has "$WORKFLOW" 'set -euo pipefail'
  assert_lacks "$WORKFLOW" 'verify-release-index.sh'
  local v c x
  v="$(grep -n 'staging_dir/host/bin/usign -V' "$WORKFLOW" | head -n1 | cut -d: -f1)"
  c="$(grep -n 'CHECKSUM_OUTPUT="$(sha256sum' "$WORKFLOW" | head -n1 | cut -d: -f1)"
  x="$(grep -n 'tar --zstd -xf "$TOOLCHAIN_ARCHIVE"' "$WORKFLOW" | head -n1 | cut -d: -f1)"
  [[ "$v" -lt "$c" && "$c" -lt "$x" ]] || fail 'signature/checksum/extraction order changed'
  local stage
  stage="$(sed -n '/- name: Build toolchain/,/- name: Create dummy external toolchain archive/p' "$WORKFLOW")"
  ! grep -Fq 'if:' <<<"$stage" || fail 'Build toolchain step is no longer unconditional'
  grep -Fq 'make toolchain/install' <<<"$stage" || fail 'Build toolchain command missing'
}
test_resolve_openwrt_keyring(){
  prepare_workspace
  local k="$TEST_ROOT/.ci-keyring" envfile="$TEST_ROOT/github-env"
  mkdir -p "$k/gpg"
  for name in CD54E82DADB3684D.asc 0x1D53D1877742E911.asc 626471F1.asc; do printf 'key fixture\n' > "$k/gpg/$name"; done
  make_keyring "$k" https://github.com/openwrt/keyring
  GITHUB_WORKSPACE="$TEST_ROOT" GITHUB_ENV="$envfile" AUTHOR=openwrt bash -euo pipefail -c "$RESOLVE_CODE"
  grep -Fq "KEYRING_GPG_DIR=$k/gpg" "$envfile" || fail 'OpenWrt GPG output incorrect'
  grep -Fq 'KEYRING_USIGN_KEY=' "$envfile" || fail 'OpenWrt usign output should be empty'
  ! grep -Fq KEYRING_COMMIT "$envfile" || fail 'unexpected keyring commit environment output'
}
test_resolve_immortalwrt_key(){
  prepare_workspace
  local k="$TEST_ROOT/.ci-keyring" envfile="$TEST_ROOT/github-env"
  printf '%s\n' '$(INSTALL_DATA) $(PKG_BUILD_DIR)/usign/467a35c14d7fbc5c $(1)/etc/opkg/keys/' > "$TEST_ROOT/openwrt/package/system/openwrt-keyring/Makefile"
  mkdir -p "$k/usign"; printf 'official key fixture\n' > "$k/usign/467a35c14d7fbc5c"
  make_keyring "$k" https://github.com/immortalwrt/keyring.git
  GITHUB_WORKSPACE="$TEST_ROOT" GITHUB_ENV="$envfile" AUTHOR=immortalwrt bash -euo pipefail -c "$RESOLVE_CODE"
  grep -Fq "KEYRING_USIGN_KEY=$k/usign/467a35c14d7fbc5c" "$envfile" || fail 'selected usign key output incorrect'
  grep -Fq 'KEYRING_GPG_DIR=' "$envfile" || fail 'ImmortalWrt GPG output should be empty'
}
test_resolve_rejects_missing_key(){
  prepare_workspace
  local k="$TEST_ROOT/.ci-keyring"
  printf '%s\n' '$(INSTALL_DATA) $(PKG_BUILD_DIR)/usign/aaaaaaaaaaaaaaaa $(1)/etc/opkg/keys/' > "$TEST_ROOT/openwrt/package/system/openwrt-keyring/Makefile"
  mkdir -p "$k/usign"; printf 'different key\n' > "$k/usign/467a35c14d7fbc5c"
  make_keyring "$k" https://github.com/immortalwrt/keyring
  if GITHUB_WORKSPACE="$TEST_ROOT" GITHUB_ENV="$TEST_ROOT/env" AUTHOR=immortalwrt bash -euo pipefail -c "$RESOLVE_CODE" 2>"$TEST_ROOT/error"; then fail 'unavailable release key accepted'; fi
  grep -Fq 'not present in the official keyring checkout' "$TEST_ROOT/error" || fail 'missing-key error not explicit'
}
test_resolve_rejects_wrong_origin(){
  prepare_workspace
  local k="$TEST_ROOT/.ci-keyring"
  mkdir -p "$k/gpg"; for name in CD54E82DADB3684D.asc 0x1D53D1877742E911.asc 626471F1.asc; do : > "$k/gpg/$name"; done
  make_keyring "$k" https://github.com/attacker/keyring
  if GITHUB_WORKSPACE="$TEST_ROOT" GITHUB_ENV="$TEST_ROOT/env" AUTHOR=openwrt bash -euo pipefail -c "$RESOLVE_CODE" 2>"$TEST_ROOT/error"; then fail 'untrusted keyring origin accepted'; fi
  grep -Fq 'Unexpected openwrt keyring origin' "$TEST_ROOT/error" || fail 'wrong-origin error not explicit'
}
make_archive(){
  local dir="$1"
  printf 'verified payload\n' > "$dir/payload"
  tar -cJf "$dir/toolchain.tar.xz" -C "$dir" payload
  (cd "$dir" && sha256sum toolchain.tar.xz > sha256sums)
}
run_verify(){
  local dir="$1" author="$2" key="$3" gpgdir="$4" path="$5"
  (cd "$dir" && AUTHOR="$author" KEYRING_USIGN_KEY="$key" KEYRING_GPG_DIR="$gpgdir" TOOLCHAIN_ARCHIVE=toolchain.tar.xz PATH="$path" bash -euo pipefail -c "$VERIFY_CODE")
}
test_usign_real_crypto(){
  [[ -n "${USIGN_BIN:-}" && -x "$USIGN_BIN" ]] || fail 'USIGN_BIN must point to upstream usign'
  prepare_workspace
  local dir="$TEST_ROOT/work" key="$TEST_ROOT/release.pub" secret="$TEST_ROOT/release.sec" wrong="$TEST_ROOT/wrong.pub" wrongsec="$TEST_ROOT/wrong.sec"
  mkdir -p "$dir"; make_archive "$dir"
  "$USIGN_BIN" -G -c 'CI release test key' -s "$secret" -p "$key"
  "$USIGN_BIN" -S -m "$dir/sha256sums" -s "$secret" -x "$dir/sha256sums.sig"
  run_verify "$dir" immortalwrt "$key" '' "$PATH"
  make_archive "$dir"
  "$USIGN_BIN" -S -m "$dir/sha256sums" -s "$secret" -x "$dir/sha256sums.sig"
  "$USIGN_BIN" -G -c 'CI wrong key' -s "$wrongsec" -p "$wrong"
  if run_verify "$dir" immortalwrt "$wrong" '' "$PATH" 2>/dev/null; then fail 'wrong usign key accepted'; fi
  printf '# tampered\n' >> "$dir/sha256sums"
  if run_verify "$dir" immortalwrt "$key" '' "$PATH" 2>/dev/null; then fail 'tampered signed checksum index accepted'; fi
}
test_missing_signature(){
  prepare_workspace
  local dir="$TEST_ROOT/work" fake="$TEST_ROOT/usign"
  mkdir -p "$dir"; printf 'index\n' > "$dir/sha256sums"
  printf '#!/bin/sh\nexit 99\n' > "$fake"; chmod +x "$fake"
  if (cd "$dir" && AUTHOR=immortalwrt KEYRING_USIGN_KEY="$fake" KEYRING_GPG_DIR='' TOOLCHAIN_ARCHIVE=toolchain.tar.xz bash -euo pipefail -c "$VERIFY_CODE") 2>"$TEST_ROOT/error"; then fail 'missing signature accepted'; fi
  grep -Fq 'signature is missing or empty' "$TEST_ROOT/error" || fail 'missing signature error absent'
}
test_bad_usign_status(){
  prepare_workspace
  local dir="$TEST_ROOT/work" fake="$TEST_ROOT/usign"
  mkdir -p "$dir"; printf 'index\n' > "$dir/sha256sums"; printf 'sig\n' > "$dir/sha256sums.sig"; printf 'key\n' > "$TEST_ROOT/key"
  printf '#!/bin/sh\nexit 17\n' > "$fake"; chmod +x "$fake"
  if (cd "$dir" && AUTHOR=immortalwrt KEYRING_USIGN_KEY="$TEST_ROOT/key" KEYRING_GPG_DIR='' TOOLCHAIN_ARCHIVE=toolchain.tar.xz bash -euo pipefail -c "$VERIFY_CODE") 2>/dev/null; then fail 'bad signature accepted'; else [[ "$?" -eq 17 ]] || fail 'usign exit status not propagated'; fi
}
test_real_gpg_and_tamper(){
  command -v gpg >/dev/null || fail 'gpg is required'
  prepare_workspace
  local dir="$TEST_ROOT/work" home="$TEST_ROOT/signing-home" pub keydir name
  mkdir -p "$dir" "$home" "$TEST_ROOT/.ci-keyring/gpg"; chmod 700 "$home"; make_archive "$dir"
  gpg --homedir "$home" --batch --passphrase '' --quick-generate-key 'CI Release Verifier <ci-verifier@example.invalid>' ed25519 sign 0 >/dev/null 2>&1
  pub="$(gpg --homedir "$home" --batch --armor --export)"
  keydir="$TEST_ROOT/.ci-keyring/gpg"
  for name in CD54E82DADB3684D.asc 0x1D53D1877742E911.asc 626471F1.asc; do printf '%s\n' "$pub" > "$keydir/$name"; done
  gpg --homedir "$home" --batch --yes --detach-sign --output "$dir/sha256sums.asc" "$dir/sha256sums"
  run_verify "$dir" openwrt '' "$keydir" "$PATH"
  make_archive "$dir"
  printf '# tampered\n' >> "$dir/sha256sums"
  gpg --homedir "$home" --batch --yes --detach-sign --output "$dir/sha256sums.asc" "$dir/sha256sums"
  printf '# changed after signature\n' >> "$dir/sha256sums"
  if run_verify "$dir" openwrt '' "$keydir" "$PATH" 2>"$TEST_ROOT/gpg-error"; then fail 'tampered GPG-signed index accepted'; fi
  grep -Fq 'BAD signature' "$TEST_ROOT/gpg-error" || fail 'GPG tamper failure not reported'
}
test_checksum_manifest(){
  prepare_workspace
  local dir="$TEST_ROOT/work" digest
  mkdir -p "$dir"; printf 'valid\n' > "$dir/toolchain.tar.xz"; digest="$(sha256sum "$dir/toolchain.tar.xz" | cut -d' ' -f1)"
  printf '%s  toolchain.tar.xz\n%s  not-downloaded.tar.xz\n' "$digest" "$(printf '0%.0s' {1..64})" > "$dir/sha256sums"
  (cd "$dir" && sha256sum --check --ignore-missing sha256sums >/dev/null) || fail 'ignore-missing behavior changed'
  printf 'tampered\n' > "$dir/toolchain.tar.xz"
  if (cd "$dir" && sha256sum --check --ignore-missing sha256sums >/dev/null 2>&1); then fail 'archive tamper passed checksum'; fi
  printf '%064d  other.tar.xz\n' 0 > "$dir/sha256sums"
  if (cd "$dir" && sha256sum --check --ignore-missing sha256sums >/dev/null 2>"$TEST_ROOT/hash-error"); then fail 'unmatched archive manifest passed'; fi
  grep -Fq 'no file was verified' "$TEST_ROOT/hash-error" || fail 'unmatched manifest error absent'
}
test_no_extract_on_failed_usign(){
  [[ -n "${USIGN_BIN:-}" && -x "$USIGN_BIN" ]] || fail 'USIGN_BIN must point to upstream usign'
  prepare_workspace
  local dir="$TEST_ROOT/work" secret="$TEST_ROOT/key.sec" pub="$TEST_ROOT/key.pub" marker="$TEST_ROOT/tar-called" fakebin="$TEST_ROOT/bin"
  mkdir -p "$dir" "$fakebin"; printf 'archive\n' > "$dir/payload"; tar -cJf "$dir/toolchain.tar.xz" -C "$dir" payload
  (cd "$dir" && sha256sum toolchain.tar.xz > sha256sums)
  "$USIGN_BIN" -G -c 'fail closed test' -s "$secret" -p "$pub"
  "$USIGN_BIN" -S -m "$dir/sha256sums" -s "$secret" -x "$dir/sha256sums.sig"
  printf '# changed after signing\n' >> "$dir/sha256sums"
  cat > "$fakebin/tar" <<'STUB'
#!/bin/sh
touch "$TAR_MARKER"
exit 99
STUB
  chmod +x "$fakebin/tar"
  if (cd "$dir" && AUTHOR=immortalwrt KEYRING_USIGN_KEY="$pub" KEYRING_GPG_DIR='' TOOLCHAIN_ARCHIVE=toolchain.tar.xz TAR_MARKER="$marker" PATH="$fakebin:$PATH" bash -euo pipefail -c "$VERIFY_CODE") 2>/dev/null; then fail 'tampered index passed'; fi
  [[ ! -e "$marker" ]] || fail 'tar ran after signature verification failed'
}
run_test 'workflow trust format and verify-before-extract order' test_workflow_contract
run_test 'OpenWrt official keyring resolution' test_resolve_openwrt_keyring
run_test 'ImmortalWrt source-selected official usign key' test_resolve_immortalwrt_key
run_test 'reject key absent from official keyring' test_resolve_rejects_missing_key
run_test 'reject unexpected keyring origin' test_resolve_rejects_wrong_origin
run_test 'real usign positive, tamper and wrong-key checks' test_usign_real_crypto
run_test 'missing signature rejected before verifier' test_missing_signature
run_test 'usign failure status propagates' test_bad_usign_status
run_test 'real GPG detached signature and tamper check' test_real_gpg_and_tamper
run_test 'upstream sha256sum ignore-missing semantics' test_checksum_manifest
run_test 'failed usign verification prevents archive extraction' test_no_extract_on_failed_usign
printf '\n%d release-index shell regression tests passed.\n' "$PASS"
