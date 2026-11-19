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
t09(){
  local f="$WF/determine-targets.yml"
  lacks "$f" 'eval "$(grep'
  has "$f" 'CONFIG_TARGET_BOARD="$(sed -n'
  has "$f" 'CONFIG_TARGET_SUBTARGET="$(sed -n'
  has "$f" 'TARGET="$CONFIG_TARGET_BOARD"'
  has "$f" 'SUBTARGET="$CONFIG_TARGET_SUBTARGET"'
  lacks "$f" 'TARGET=${TARGET:-'
  lacks "$f" 'SUBTARGET=${SUBTARGET:-'
  lacks "$f" 'CONFIG_ARCH='
  lacks "$f" 'outputs.arch'
  lacks "$f" 'printf '\''arch=%s'
}
t14(){ local f="$WF/reusable_build.yml"; has "$f" 'git diff --name-only -z'; has "$f" "while IFS= read -r -d '' file;"; has "$f" 'cp --parents -- "$file" packages-fixed/'; lacks "$f" 'for f in $(git diff --name-only)'; }



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
  lacks "$b" "github.event_name == 'push'"
  lacks "$b" "github.event_name != 'push'"
}
t07(){
  local b="$WF/build.yml" r="$WF/reusable_build.yml" rt="$WF/reusable_build_tools.yml"
  local p="$WF/push-containers.yml" f="$WF/fingerprint.yml" v="$WF/tests.yml"
  lacks "$b" 'test_fingerprints:'
  lacks "$b" 'test-workflows.sh'
  lacks "$b" 'validate_only'
  for workflow in "$b" "$r" "$rt" "$p" "$f"; do
    lacks "$workflow" '.github/scripts/tests/'
    lacks "$workflow" 'test-workflows.sh'
    lacks "$workflow" 'test-release-index.sh'
    lacks "$workflow" 'test-diy-scripts.sh'
    lacks "$workflow" 'dummy_mode'
    lacks "$workflow" 'DUMMY_MODE'
    lacks "$workflow" 'TEST ONLY'
    lacks "$workflow" 'test_expected_toolchain_type'
  done
  has "$v" '.github/scripts/tests/test-diy-scripts.sh'
  has "$v" '.github/scripts/tests/test-workflows.sh'
  has "$v" '.github/scripts/tests/test-release-index.sh'
  lacks "$v" 'uses: ./.github/workflows/build.yml'
  lacks "$v" 'uses: ./.github/workflows/reusable_build.yml'
  [[ ! -e "$WF/static-validation.yml" ]] || { echo 'obsolete static-validation workflow remains' >&2; return 1; }
  [[ ! -e "$WF/dummy-build-integration-test.yml" ]] || { echo 'obsolete dummy integration workflow remains' >&2; return 1; }
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
  local f="$WF/reusable_build.yml" block
  lacks "$f" '$(ls /external-toolchain/ | grep wrt-toolchain)'
  lacks "$f" '$(ls $TOOLCHAIN_FILE/staging_dir | grep toolchain)'
  has "$f" "steps.parse-toolchain.outputs.toolchain-type == 'external_container'"
  has "$f" "steps.parse-toolchain.outputs.toolchain-type == 'external_toolchain'"
  has "$f" "steps.parse-toolchain.outputs.toolchain-type == 'external_sdk'"
  block="$(sed -n '/^      - name: Configure external toolchain$/,/^      - name: Configure internal toolchain$/p' "$f")"
  has <(printf '%s\n' "$block") 'case "$TOOLCHAIN_TYPE" in'
  has <(printf '%s\n' "$block") 'TOOLCHAIN_PARENT="$TOOLCHAIN_FILE"'
  has <(printf '%s\n' "$block") 'TOOLCHAIN_PARENT="$TOOLCHAIN_FILE/staging_dir"'
  has <(printf '%s\n' "$block") 'TOOLCHAIN_PARENT="${toolchain_roots[0]}"'
  count <(printf '%s\n' "$block") 'find -H "$TOOLCHAIN_PARENT"' 1
  count <(printf '%s\n' "$block") '--overwrite-config' 1
  has "$f" 'find -H "$TOOLCHAIN_FILE/staging_dir" -mindepth 1 -maxdepth 1 -type d -name'
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
  local b="$WF/build.yml" p="$WF/push-containers.yml" r="$WF/reusable_build.yml"
  has "$b" 'TAG="$AUTHOR"_"$REPO_BRANCH-$EXPECTED_FINGERPRINT"'
  has "$b" 'TAG="$TARGET-$SUBTARGET-$AUTHOR"_"$REPO_BRANCH-${FINGERPRINT:0:32}"'
  has "$p" 'tools:${{ needs.determine_container_info.outputs.container_tag }}-${{ inputs.tools_fingerprint || needs.compute-tools-fingerprint.outputs.tools_fingerprint }}'
  has "$p" 'toolchain:${{ inputs.target || needs.determine_targets.outputs.target }}-${{ inputs.subtarget || needs.determine_targets.outputs.subtarget }}-${{ needs.determine_container_info.outputs.container_tag }}-${{ needs.compute-toolchain-fingerprint.outputs.toolchain_cache_key }}'
  has "$r" 'ccache_name: ${{ steps.determine_container_name.outputs.ccache_name }}'
  has "$r" 'echo "ccache_name=ccache-$CCACHE_TYPE-$CCACHE_TAG"'
  lacks "$r" 'steps.determine_ccache_info'
  lacks "$r" '- name: Set lower case owner name'
  lacks "$r" '- name: Determine ccache info'
  has "$r" 'CONTAINER="$OWNER_LC/$CONTAINER_NAME"'
}
t16(){
  local p="$WF/push-containers.yml" f="$WF/fingerprint.yml" r="$WF/reusable_build.yml" rb="$WF/reusable_build_tools.yml" d="$ROOT/.github/dockerfiles/Dockerfile.toolchain" block
  block="$(job_section "$p" compute-toolchain-fingerprint build-target-toolchain)"
  has <(printf '%s\n' "$block") "needs.build-linux-buildbot.result == 'success'"
  lacks <(printf '%s\n' "$block") needs.push-tools-container
  has <(printf '%s\n' "$block") 'use_prebuilt_tools_artifact: true'
  block="$(job_section "$p" build-target-toolchain push-target-toolchain)"
  has <(printf '%s\n' "$block") 'needs.build-linux-buildbot'
  lacks <(printf '%s\n' "$block") push-tools-container
  has <(printf '%s\n' "$block") 'use_prebuilt_tools_artifact: true'
  has "$p" 'name: linux-buildbot-prebuilt-tools'
  lacks "$p" 'generate_prebuilt_artifacts:'
  lacks "$rb" 'generate_prebuilt_artifacts'
  has "$rb" 'tar -cf tools.tar staging_dir/host build_dir/host'
  has "$rb" 'name: linux-buildbot-prebuilt-tools'
  has "$f" use_prebuilt_tools_artifact; has "$f" 'actions/download-artifact@v8.0.2'
  has "$r" use_prebuilt_tools_artifact; has "$r" 'actions/download-artifact@v8.0.2'
  has "$r" 'CONTAINER=openwrt/buildbot/buildworker-v3.11.8:v23'
  has "$r" 'echo "container=$CONTAINER" >> "$GITHUB_OUTPUT"'
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
run 'production/test workflow separation' t07
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
