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
t03(){ local r="$WF/reusable_build.yml" t="$WF/reusable_build_tools.yml"; has "$r" 'CCACHE_TAG="$TARGET-$SUBTARGET-$CONTAINER_TAG"'; lacks "$r" 'CCACHE_TAG="$CONTAINER_TAG"'; has "$r" ccache-kernel; has "$t" ccache-host-tools-buildworker-v23; }
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
