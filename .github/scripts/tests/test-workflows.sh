#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
WF="$ROOT/.github/workflows"
PASS=0
has(){ grep -Fq -- "$2" "$1" || { echo "missing: $2" >&2; return 1; }; }
lacks(){ ! grep -Fq -- "$2" "$1" || { echo "unexpected: $2" >&2; return 1; }; }
count(){ local n; n="$(grep -Fc -- "$2" "$1" || true)"; [[ "$n" == "$3" ]]; }
run(){ "$2"; PASS=$((PASS+1)); printf 'ok %02d - %s\n' "$PASS" "$1"; }
t01(){ for f in build.yml push-containers.yml determine-targets.yml; do lacks "$WF/$f" openwrt-21.02; lacks "$WF/$f" hanwckf/immortalwrt-mt798x; done; }
t02(){ local f="$WF/fingerprint.yml" r="$WF/reusable_build.yml"; for x in CONFIG_DEVEL=y CONFIG_AUTOREMOVE=y CONFIG_CCACHE=y make-defconfig make-tools-install make-toolchain-install make-target-toolchain-compile; do has "$f" "$x"; done; lacks "$f" CONFIG_BUILD_ALL_HOST_TOOLS; has "$f" toolchain-v2; for x in 'make defconfig' 'make tools/install' 'make toolchain/install' 'make target/toolchain/compile' './scripts/ext-tools.sh --refresh'; do has "$r" "$x"; done; }
t02(){
  local f="$WF/fingerprint.yml" r="$WF/reusable_build.yml" tools toolchain
  tools="$(sed -n '/^  tools:/,/^  toolchain:/p' "$f")"
  toolchain="$(sed -n '/^  toolchain:/,$p' "$f")"
  [[ -n "$tools" && -n "$toolchain" ]] || { echo 'fingerprint mode sections are missing' >&2; return 1; }
  for setting in CONFIG_DEVEL=y CONFIG_AUTOREMOVE=y CONFIG_CCACHE=y; do
    grep -Fq "$setting" <<<"$tools" || { echo "missing Host Tools config: $setting" >&2; return 1; }
  done
  ! grep -Fq CONFIG_BUILD_ALL_HOST_TOOLS <<<"$tools" || { echo 'CONFIG_BUILD_ALL_HOST_TOOLS must remain excluded' >&2; return 1; }
  grep -Fq 'make-defconfig,make-tools-install' <<<"$tools" || { echo 'Host Tools recipe sequence changed' >&2; return 1; }
  grep -Fq toolchain-v2 <<<"$toolchain" || { echo 'Toolchain recipe version changed' >&2; return 1; }
  for command in make-defconfig make-tools-install make-toolchain-install make-target-toolchain-compile './scripts/ext-tools.sh --refresh'; do
    grep -Fq "$command" <<<"$toolchain" || { echo "missing Toolchain recipe command: $command" >&2; return 1; }
  done
  for command in 'make defconfig' 'make tools/install' 'make toolchain/install' 'make target/toolchain/compile' './scripts/ext-tools.sh --refresh'; do
    has "$r" "$command"
  done
}
t03(){
  local r="$WF/reusable_build.yml" t="$WF/reusable_build_tools.yml" keys first second third
  has "$r" 'CCACHE_TAG="$TARGET-$SUBTARGET-$CONTAINER_TAG"'
  lacks "$r" 'CCACHE_TAG="$CONTAINER_TAG"'
  keys="$(awk '/name: Restore ccache cache/{found=1} found && /restore-keys: [|]/{inside=1;next} inside && /^[[:space:]]*$/{exit} inside{sub(/^[[:space:]]+/,"");print}' "$r")"
  first="$(sed -n '1p' <<<"$keys")"; second="$(sed -n '2p' <<<"$keys")"; third="$(sed -n '3p' <<<"$keys")"
  [[ "$first" == *needs.setup_build.outputs.ccache_name* ]]
  [[ "$second" == *inputs.ccache_type* && "$second" == *inputs.target* && "$second" == *inputs.subtarget* ]]
  [[ "$third" == *inputs.ccache_type* ]]
  ! grep -Fq ccache-kernel- <<<"$keys"
  has "$r" 'key: ccache-kernel-'
  keys="$(awk '/name: Restore Host Tools ccache/{found=1} found && /restore-keys: [|]/{inside=1;next} inside && /^[[:space:]]*$/{exit} inside{sub(/^[[:space:]]+/,"");print}' "$t")"
  first="$(sed -n '1p' <<<"$keys")"; second="$(sed -n '2p' <<<"$keys")"
  [[ "$first" == *ccache-host-tools-buildworker-v23* && "$first" == *runner.os* && "$first" == *runner.arch* ]]
  [[ "$second" == *inputs.ccache_tag* && "$second" == *buildworker-v23* && "$second" == *runner.os* && "$second" == *runner.arch* ]]
  has "$t" 'key: ccache-host-tools-buildworker-v23-'
}
t04(){ local r="$WF/reusable_build.yml" f="$WF/fingerprint.yml"; count "$r" 'name: Prepare prebuilt tools' 2; has "$r" './scripts/ext-tools.sh --refresh'; lacks "$r" ext-tools-refresh.sh; lacks "$r" --preserve-environment; count "$f" 'name: Prepare prebuilt tools' 1; has "$f" './scripts/ext-tools.sh --refresh'; }
t05(){ local b="$WF/build.yml" p="$WF/push-containers.yml" r="$WF/reusable_build.yml"; count "$b" "curl -sS -o /dev/null -w '%{http_code}'" 2; has "$b" '200) echo "tools_required=false"'; has "$b" '*) echo "tools_required=true"'; has "$b" '200) echo "use_toolchain=true"'; has "$b" '*) echo "use_toolchain=false"'; count "$p" "curl -sS -o /dev/null -w '%{http_code}'" 2; has "$p" 'Verify published Host Tools image'; has "$p" 'Verify published target Toolchain image'; lacks "$r" GHCR_TOKEN; }
t06(){ local b="$WF/build.yml" p="$WF/push-containers.yml"; has "$p" 'needs.determine_targets.outputs.target'; has "$p" 'needs.determine_targets.outputs.repo_url'; has "$p" 'needs.compute-tools-fingerprint.outputs.tools_fingerprint'; has "$b" 'needs.determine_targets.outputs.target'; has "$b" 'needs.tools_fingerprint.outputs.tools_fingerprint'; has "$p" 'steps.info.outputs.source_commit'; }
t07(){ local b="$WF/build.yml" d="$WF/dummy-build-integration-test.yml" r="$WF/reusable_build.yml"; has "$b" EXPECTED_FINGERPRINT; has "$d" TOOLS_FINGERPRINT; has "$d" TOOLCHAIN_FINGERPRINT; has "$d" '200) TOOLS_REQUIRED=false'; has "$d" '*) TOOLS_REQUIRED=true'; has "$d" '200) USE_TOOLCHAIN=true'; has "$d" '*) USE_TOOLCHAIN=false'; has "$d" dummy_toolchain_image_consumer; has "$r" CONTAINER_TAG; }
t08(){ local b="$WF/build.yml" p="$WF/push-containers.yml" s; s="$(sed -n '/^  tools_ready:/,/^  toolchain_fingerprint:/p' "$b")"; grep -Fq tools_image: <<<"$s"; ! grep -Fq 'curl ' <<<"$s"; has "$p" 'Verify published Host Tools image'; }
t09(){ lacks "$WF/determine-targets.yml" 'eval "$(grep'; has "$WF/determine-targets.yml" 'CONFIG_TARGET_BOARD="$(sed -n'; has "$WF/determine-targets.yml" 'CONFIG_TARGET_SUBTARGET="$(sed -n'; has "$WF/determine-targets.yml" 'CONFIG_ARCH="$(sed -n'; }
t10(){ local s; s="$(sed -n '/- name: Get Commit Hash/,/- name: Compare Commit Hash/p' "$WF/update-checker.yml")"; grep -Fq 'set -e' <<<"$s"; grep -Fq 'COMMIT_HASH="$(git rev-parse HEAD)"' <<<"$s"; grep -Fq 'commitHash=%s' <<<"$s"; }
t11(){ has "$WF/determine-targets.yml" 'value" =~ ^[[:alnum:]][[:alnum:]_-]*$'; has "$WF/reusable_build.yml" 'Invalid $value_name workflow input'; lacks "$WF/reusable_build.yml" 'CONFIG_TARGET_inputs.target'; has "$WF/reusable_build.yml" CONFIG_TARGET_; }
t12(){ local f="$WF/reusable_build.yml"; lacks "$f" '$(ls /external-toolchain/ | grep wrt-toolchain)'; lacks "$f" '$(ls $TOOLCHAIN_FILE/staging_dir | grep toolchain)'; has "$f" toolchain_roots; has "$f" 'find -H "$TOOLCHAIN_FILE/staging_dir"'; }
t13(){ local f="$WF/reusable_build.yml"; has "$f" CHECK_PACKAGES_LIST; has "$f" 'read -r -a packages'; lacks "$f" 'for package in inputs.check_packages_list'; has "$f" COVERITY_API_TOKEN; has "$f" 'read -r -a compiler_templates'; has "$f" 'read -r -a coverity_check_packages'; }
t14(){ local f="$WF/reusable_build.yml"; has "$f" 'git diff --name-only -z'; has "$f" "while IFS= read -r -d '' file;"; has "$f" 'cp --parents -- "$file" packages-fixed/'; lacks "$f" 'for f in $(git diff --name-only)'; }
t15(){ local b="$WF/build.yml" p="$WF/push-containers.yml"; has "$b" EXPECTED_FINGERPRINT; has "$b" FINGERPRINT:0:32; has "$p" tools_fingerprint; has "$p" toolchain_cache_key; }
t16(){ local p="$WF/push-containers.yml" f="$WF/fingerprint.yml" r="$WF/reusable_build.yml" d="$ROOT/.github/dockerfiles/Dockerfile.toolchain"; has "$p" build-linux-buildbot; has "$p" use_prebuilt_tools_artifact; has "$d" /prebuilt_tools/staging_dir/host; has "$d" /prebuilt_tools/build_dir/host; has "$r" use_prebuilt_tools_artifact; has "$f" use_prebuilt_tools_artifact; }



restore_keys_exact(){
  awk -v wanted="$2" '
    $0 ~ "name: " wanted "$" { found=1; next }
    found && /restore-keys: [|]/ { inside=1; next }
    inside && /^[[:space:]]*$/ { exit }
    inside { sub(/^[[:space:]]+/,""); if ($0 !~ /^#/) print }
  ' "$1"
}
job_section(){
  awk -v first="  $2:" -v last="  $3:" '
    $0 == first { found=1 }
    found && $0 == last { exit }
    found { print }
  ' "$1"
}
t03(){
  local r="$WF/reusable_build.yml" t="$WF/reusable_build_tools.yml" actual expected d='$'
  has "$r" 'CCACHE_TAG="$TARGET-$SUBTARGET-$CONTAINER_TAG"'
  lacks "$r" 'CCACHE_TAG="$CONTAINER_TAG"'
  actual="$(restore_keys_exact "$r" 'Restore ccache cache')"
  expected='${{ needs.setup_build.outputs.ccache_name }}-
ccache-${{ inputs.ccache_type }}-${{ inputs.target }}-${{ inputs.subtarget }}-
ccache-${{ inputs.ccache_type }}-'
  [[ "$actual" == "$expected" ]] || { diff -u <(printf '%s\n' "$expected") <(printf '%s\n' "$actual"); return 1; }
  has "$r" "key: ccache-kernel-${d}{{ needs.setup_build.outputs.ccache_tag}}-${d}{{ hashFiles('openwrt/include/kernel-**') }}"
  ! grep -Fq 'ccache-kernel-' <<<"$actual"
  has "$t" 'key: ccache-host-tools-buildworker-v23-${{ runner.os }}-${{ runner.arch }}-${{ inputs.ccache_tag }}'
  actual="$(restore_keys_exact "$t" 'Restore Host Tools ccache')"
  expected='ccache-host-tools-buildworker-v23-${{ runner.os }}-${{ runner.arch }}-
ccache-host-tools-${{ inputs.ccache_tag }}-buildworker-v23-${{ runner.os }}-${{ runner.arch }}'
  [[ "$actual" == "$expected" ]] || { diff -u <(printf '%s\n' "$expected") <(printf '%s\n' "$actual"); return 1; }
}
t04(){
  local r="$WF/reusable_build.yml" f="$WF/fingerprint.yml"
  count "$r" 'name: Prepare prebuilt tools' 2; has "$r" './scripts/ext-tools.sh --refresh'
  lacks "$r" ext-tools-refresh.sh; lacks "$r" --preserve-environment
  count "$f" 'name: Prepare prebuilt tools' 1; has "$f" './scripts/ext-tools.sh --refresh'
  lacks "$f" ext-tools-refresh.sh; lacks "$f" --preserve-environment
}
t05(){
  local b="$WF/build.yml" p="$WF/push-containers.yml" r="$WF/reusable_build.yml"
  count "$b" "curl -sS -o /dev/null -w '%{http_code}'" 2
  count "$b" 'GHCR_TOKEN="$(printf '\''%s'\'' "$GITHUB_TOKEN" | base64 -w0)"' 2
  has "$b" 'https://ghcr.io/v2/$OWNER_LC/tools/manifests/$TAG'
  has "$b" 'https://ghcr.io/v2/$OWNER_LC/toolchain/manifests/$TAG'
  has "$b" '200) echo "tools_required=false"'; has "$b" '*) echo "tools_required=true"'
  lacks "$b" '404) echo "tools_required=true"'
  has "$b" '200) echo "use_toolchain=true"'; has "$b" '*) echo "use_toolchain=false"'
  lacks "$b" '404) echo "use_toolchain=false"'; lacks "$b" 'Verify selected image'
  count "$p" "curl -sS -o /dev/null -w '%{http_code}'" 2
  count "$p" 'if [ "$HTTP_STATUS" = "200" ]; then' 2
  has "$p" 'Verify published Host Tools image'; has "$p" 'Verify published target Toolchain image'
  lacks "$r" ghcr-check; lacks "$r" GHCR_TOKEN; lacks "$r" 'GITHUB_ACTOR:'
  lacks "$r" 'io.abu9.actions-wrt.toolchain-fingerprint='
}
t06(){
  local b="$WF/build.yml" p="$WF/push-containers.yml" d='$'
  has "$p" "if: ${d}{{ inputs.target == '' }}"
  has "$p" "inputs.tools_fingerprint == ''"
  has "$p" 'inputs.target || needs.determine_targets.outputs.target'
  has "$p" 'inputs.repo_url || needs.determine_targets.outputs.repo_url'
  has "$p" 'inputs.tools_fingerprint || needs.compute-tools-fingerprint.outputs.tools_fingerprint'
  has "$b" 'target: ${{ needs.determine_targets.outputs.target }}'
  has "$b" 'tools_fingerprint: ${{ needs.tools_fingerprint.outputs.tools_fingerprint }}'
  has "$p" 'source_commit: ${{ steps.info.outputs.source_commit }}'
  has "$p" 'SOURCE_COMMIT: ${{ inputs.source_commit || needs.determine_targets.outputs.source_commit }}'
  has "$p" 'jobs.determine_container_info.outputs.source_commit'
}
t07(){
  local b="$WF/build.yml" d="$WF/dummy-build-integration-test.yml" r="$WF/reusable_build.yml" dollar='$'
  has "$b" 'TAG="$AUTHOR"_"$REPO_BRANCH-$EXPECTED_FINGERPRINT"'
  has "$d" 'TOOLS_TAG="${AUTHOR}_${REPO_BRANCH}-${TOOLS_FINGERPRINT}-dummy-${GITHUB_RUN_ID}"'
  has "$b" 'TAG="$TARGET-$SUBTARGET-$AUTHOR"_"$REPO_BRANCH-${FINGERPRINT:0:32}"'
  has "$d" 'TOOLCHAIN_TAG="${TARGET}-${SUBTARGET}-${AUTHOR}_${REPO_BRANCH}-${TOOLCHAIN_FINGERPRINT:0:32}-dummy-${GITHUB_RUN_ID}"'
  has "$b" '200) echo "tools_required=false"'; has "$b" '*) echo "tools_required=true"'
  has "$d" '200) TOOLS_REQUIRED=false'; has "$d" '*) TOOLS_REQUIRED=true'
  has "$b" '200) echo "use_toolchain=true"'; has "$b" '*) echo "use_toolchain=false"'
  has "$d" '200) USE_TOOLCHAIN=true'; has "$d" '*) USE_TOOLCHAIN=false'
  has "$d" 'container_name: ${{ needs.verify_fixture_cache_hit.outputs.selected_container }}'
  has "$d" 'dummy_toolchain_image_consumer:'
  has "$b" "container_name: ${dollar}{{ (inputs.build_toolchain == true && needs.check_toolchain_container.outputs.use_toolchain == 'true') && 'toolchain' || 'tools' }}"
  has "$d" 'build_toolchain: true'
  has "$r" 'CONTAINER_TAG="${CONTAINER_TAG}-dummy-${GITHUB_RUN_ID}"'
}
t08(){
  local b="$WF/build.yml" p="$WF/push-containers.yml" s
  s="$(job_section "$b" tools_ready toolchain_fingerprint)"
  has <(printf '%s\n' "$s") 'tools_image: ${{ steps.image.outputs.tools_image }}'
  ! grep -Fq 'curl ' <<<"$s"
  lacks <(printf '%s\n' "$s") 'Verify selected image'
  has "$p" 'Verify published Host Tools image'
  has "$p" 'if [ "$HTTP_STATUS" = "200" ]; then'
}
t10(){
  local s expected
  s="$(sed -n '/- name: Get Commit Hash/,/- name: Compare Commit Hash/p' "$WF/update-checker.yml")"
  grep -Fq 'set -e' <<<"$s"
  grep -Fq 'COMMIT_HASH="$(git rev-parse HEAD)"' <<<"$s"
  expected='printf '\''commitHash=%s\n'\'' "$COMMIT_HASH"'
  grep -Fq "$expected" <<<"$s"
  ! grep -Fq 'echo "commitHash=$(git rev-parse HEAD)"' <<<"$s"
}
t11(){
  has "$WF/determine-targets.yml" '[[ ! "$value" =~ ^[[:alnum:]][[:alnum:]_-]*$ ]]'
  has "$WF/determine-targets.yml" 'Invalid $value_name value in resolved .config'
  has "$WF/reusable_build.yml" 'Invalid $value_name workflow input'
  lacks "$WF/reusable_build.yml" 'CONFIG_TARGET_${{ inputs.target }}'
  lacks "$WF/reusable_build.yml" 'targets/${{ inputs.target }}/${{ inputs.subtarget }}'
  has "$WF/reusable_build.yml" 'CONFIG_TARGET_${TARGET}'
  has "$WF/reusable_build.yml" '--config "$TARGET/$SUBTARGET"'
}
t12(){
  local f="$WF/reusable_build.yml" n
  lacks "$f" '$(ls /external-toolchain/ | grep wrt-toolchain)'
  lacks "$f" '$(ls $TOOLCHAIN_FILE/staging_dir | grep toolchain)'
  n="$(grep -Fc 'if [ "${#toolchain_dirs[@]}" -ne 1 ]; then' "$f")"
  [[ "$n" -ge 3 ]]
  has "$f" 'if [ "${#toolchain_roots[@]}" -ne 1 ]; then'
  has "$f" 'find -H "$TOOLCHAIN_FILE/staging_dir"'
}
t13(){
  local f="$WF/reusable_build.yml"
  has "$f" 'CHECK_PACKAGES_LIST: ${{ inputs.check_packages_list }}'
  has "$f" 'read -r -a packages <<< "$CHECK_PACKAGES_LIST"'
  lacks "$f" 'for package in ${{ inputs.check_packages_list }}'
  has "$f" 'COVERITY_API_TOKEN: ${{ secrets.coverity_api_token }}'
  has "$f" 'read -r -a compiler_templates <<< "$COVERITY_COMPILER_TEMPLATE_LIST"'
  has "$f" 'read -r -a coverity_check_packages <<< "$COVERITY_CHECK_PACKAGES"'
  lacks "$f" 'coverity_check_packages=(${{ inputs.coverity_check_packages }})'
  lacks "$f" 'for template in ${{ inputs.coverity_compiler_template_list }}'
}
t15(){
  local b="$WF/build.yml" p="$WF/push-containers.yml"
  has "$b" 'TAG="$AUTHOR"_"$REPO_BRANCH-$EXPECTED_FINGERPRINT"'
  has "$b" 'TAG="$TARGET-$SUBTARGET-$AUTHOR"_"$REPO_BRANCH-${FINGERPRINT:0:32}"'
  has "$p" 'tools:${{ needs.determine_container_info.outputs.container_tag }}-${{ inputs.tools_fingerprint || needs.compute-tools-fingerprint.outputs.tools_fingerprint }}'
  has "$p" 'toolchain:${{ inputs.target || needs.determine_targets.outputs.target }}-${{ inputs.subtarget || needs.determine_targets.outputs.subtarget }}-${{ needs.determine_container_info.outputs.container_tag }}-${{ needs.compute-toolchain-fingerprint.outputs.toolchain_cache_key }}'
}
t16(){
  local p="$WF/push-containers.yml" f="$WF/fingerprint.yml" r="$WF/reusable_build.yml" d="$ROOT/.github/dockerfiles/Dockerfile.toolchain" block
  block="$(job_section "$p" compute-toolchain-fingerprint build-target-toolchain)"
  has <(printf '%s\n' "$block") "needs.build-linux-buildbot.result == 'success'"
  lacks <(printf '%s\n' "$block") needs.push-tools-container
  has <(printf '%s\n' "$block") 'use_prebuilt_tools_artifact: true'
  block="$(job_section "$p" build-target-toolchain push-target-toolchain)"
  has <(printf '%s\n' "$block") 'needs.build-linux-buildbot'
  lacks <(printf '%s\n' "$block") push-tools-container
  has <(printf '%s\n' "$block") 'use_prebuilt_tools_artifact: true'
  has "$p" 'name: linux-buildbot-prebuilt-tools'
  has "$f" use_prebuilt_tools_artifact; has "$f" 'actions/download-artifact@v8.0.2'
  has "$r" use_prebuilt_tools_artifact; has "$r" 'actions/download-artifact@v8.0.2'
  has "$r" 'container=openwrt/buildbot/buildworker-v3.11.8:v23'
  [[ "$(head -n 1 "$d")" == 'FROM ghcr.io/openwrt/buildbot/buildworker-v3.11.8:v23' ]]
  has "$d" 'COPY --chown=buildbot staging_dir/host /prebuilt_tools/staging_dir/host'
  has "$d" 'COPY --chown=buildbot build_dir/host /prebuilt_tools/build_dir/host'
  lacks "$d" 'FROM ghcr.io/$OWNER_LC/tools:'
  has "$f" 'host-tools:prebuilt-artifact'
  has "$f" 'ghcr.io/openwrt/buildbot/buildworker-v3.11.8:v23'
}

run 'legacy source rejection' t01
run 'fingerprint/build recipe alignment' t02
run 'ccache restore policy' t03
run 'upstream prebuilt-tools refresh' t04
run 'GHCR status classification' t05
run 'resolved build-input reuse' t06
run 'dummy fixture cache contract' t07
run 'Host Tools readiness ownership' t08
run 'target config parsing' t09
run 'rev-parse failure propagation' t10
run 'target identifier validation' t11
run 'external Toolchain path validation' t12
run 'package and Coverity list parsing' t13
run 'NUL-delimited changed paths' t14
run 'content-addressed image tags' t15
run 'same-run Host Tools artifact reuse' t16
printf '\n16 workflow shell regression tests passed.\n'
