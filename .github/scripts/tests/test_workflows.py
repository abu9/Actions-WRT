#!/usr/bin/env python3
"""Build-free regression tests."""

from __future__ import annotations

import unittest
from pathlib import Path


class WorkflowIntegrationTests(unittest.TestCase):
    def test_legacy_openwrt_21_02_source_is_not_supported(self) -> None:
        workflows = Path(__file__).resolve().parents[2] / "workflows"
        for name in ("build.yml", "push-containers.yml", "determine-targets.yml"):
            source = (workflows / name).read_text(encoding="utf-8")
            self.assertNotIn("openwrt-21.02", source)
            self.assertNotIn("hanwckf/immortalwrt-mt798x", source)

    def test_build_workflows_match_inline_fingerprint_inputs(self) -> None:
        root = Path(__file__).resolve().parents[2]
        fingerprint = (root / "workflows" / "fingerprint.yml").read_text(
            encoding="utf-8"
        )
        tools_start = fingerprint.index("  tools:")
        toolchain_start = fingerprint.index("  toolchain:")
        tools = fingerprint[tools_start:toolchain_start]
        toolchain = fingerprint[toolchain_start:]

        for setting in (
            "CONFIG_DEVEL=y",
            "CONFIG_AUTOREMOVE=y",
            "CONFIG_CCACHE=y",
        ):
            self.assertIn(setting, tools)
        self.assertNotIn("CONFIG_BUILD_ALL_HOST_TOOLS", tools)
        self.assertIn("make-defconfig,make-tools-install", tools)
        self.assertIn("toolchain-v2", toolchain)
        for recipe_command in (
            "make-defconfig",
            "make-tools-install",
            "make-toolchain-install",
            "make-target-toolchain-compile",
            "./scripts/ext-tools.sh --refresh",
        ):
            self.assertIn(recipe_command, toolchain)

        reusable = (root / "workflows" / "reusable_build.yml").read_text(
            encoding="utf-8"
        )
        for command in (
            "make defconfig",
            "make tools/install",
            "make toolchain/install",
            "make target/toolchain/compile",
            "./scripts/ext-tools.sh --refresh",
        ):
            self.assertIn(command, reusable)

    def test_ccache_restore_keys_follow_priority_and_isolate_namespaces(self) -> None:
        root = Path(__file__).resolve().parents[2] / "workflows"
        reusable = (root / "reusable_build.yml").read_text(encoding="utf-8")
        tools = (root / "reusable_build_tools.yml").read_text(encoding="utf-8")

        def restore_keys(workflow: str, step_name: str) -> list[str]:
            step_start = workflow.index(f"name: {step_name}")
            block_start = workflow.index("restore-keys: |", step_start)
            result: list[str] = []
            for line in workflow[block_start:].splitlines()[1:]:
                if not line.strip():
                    break
                value = line.strip()
                if value.startswith("#"):
                    continue
                result.append(value)
            return result

        # The exact Firmware cache namespace must include target/subtarget.
        # Otherwise the first same-branch prefix can match another device
        # before the explicit same-target and cross-target fallbacks.
        self.assertIn(
            'CCACHE_TAG="$TARGET-$SUBTARGET-$CONTAINER_TAG"',
            reusable,
        )
        self.assertNotIn('CCACHE_TAG="$CONTAINER_TAG"', reusable)

        # Firmware restore order: same target/source tag, same target across
        # source tags, then any target in the same ccache type.
        self.assertEqual(
            restore_keys(reusable, "Restore ccache cache"),
            [
                "${{ needs.setup_build.outputs.ccache_name }}-",
                "ccache-${{ inputs.ccache_type }}-${{ inputs.target }}-${{ inputs.subtarget }}-",
                "ccache-${{ inputs.ccache_type }}-",
            ],
        )

        # The separate kernel save uses the kernel namespace, while regular
        # restore prefixes remain scoped to the selected ccache_type.
        self.assertIn(
            "key: ccache-kernel-${{ needs.setup_build.outputs.ccache_tag}}-${{ hashFiles('openwrt/include/kernel-**') }}",
            reusable,
        )
        firmware_keys = restore_keys(reusable, "Restore ccache cache")
        self.assertTrue(all(key.startswith("ccache-${{ inputs.ccache_type }}-") or
                            key.startswith("${{ needs.setup_build.outputs.ccache_name }}-")
                            for key in firmware_keys))
        self.assertFalse(any(key.startswith("ccache-kernel-") for key in firmware_keys))

        # Host Tools exact keys retain the source tag; the first fallback
        # removes only that dimension while keeping worker and runner fixed.
        self.assertIn(
            "key: ccache-host-tools-buildworker-v23-${{ runner.os }}-${{ runner.arch }}-${{ inputs.ccache_tag }}",
            tools,
        )
        self.assertEqual(
            restore_keys(tools, "Restore Host Tools ccache"),
            [
                "ccache-host-tools-buildworker-v23-${{ runner.os }}-${{ runner.arch }}-",
                "ccache-host-tools-${{ inputs.ccache_tag }}-buildworker-v23-${{ runner.os }}-${{ runner.arch }}",
            ],
        )

    def test_prebuilt_tools_prepare_uses_upstream_refresh_script(self) -> None:
        workflow = (
            Path(__file__).resolve().parents[2]
            / "workflows"
            / "reusable_build.yml"
        ).read_text(encoding="utf-8")
        self.assertEqual(workflow.count("name: Prepare prebuilt tools"), 2)
        self.assertIn("./scripts/ext-tools.sh --refresh", workflow)
        self.assertNotIn("ext-tools-refresh.sh", workflow)
        self.assertNotIn("--preserve-environment", workflow)

        fingerprint_workflow = (
            Path(__file__).resolve().parents[2]
            / "workflows"
            / "fingerprint.yml"
        ).read_text(encoding="utf-8")
        self.assertEqual(
            fingerprint_workflow.count("name: Prepare prebuilt tools"), 1
        )
        self.assertIn("./scripts/ext-tools.sh --refresh", fingerprint_workflow)
        self.assertNotIn("ext-tools-refresh.sh", fingerprint_workflow)
        self.assertNotIn("--preserve-environment", fingerprint_workflow)

    def test_build_workflows_use_upstream_style_ghcr_head_checks(self) -> None:
        root = Path(__file__).resolve().parents[2] / "workflows"
        build = (root / "build.yml").read_text(encoding="utf-8")
        reusable = (root / "reusable_build.yml").read_text(encoding="utf-8")

        # HTTP 200 is a hit; every other HTTP response is a cache miss.
        # A curl transport failure still fails the step.
        self.assertEqual(build.count("curl -sS -o /dev/null -w '%{http_code}'"), 3)
        self.assertEqual(build.count('GHCR_TOKEN="$(printf \'%s\' "$GITHUB_TOKEN" | base64 -w0)"'), 3)
        self.assertIn("https://ghcr.io/v2/$OWNER_LC/tools/manifests/$TAG", build)
        self.assertIn("https://ghcr.io/v2/$OWNER_LC/toolchain/manifests/$TAG", build)
        self.assertIn('200) echo "tools_required=false"', build)
        self.assertIn('*) echo "tools_required=true"', build)
        self.assertNotIn('404) echo "tools_required=true"', build)
        self.assertIn('200) echo "use_toolchain=true"', build)
        self.assertIn('*) echo "use_toolchain=false"', build)
        self.assertNotIn('404) echo "use_toolchain=false"', build)
        self.assertIn('if [ "$HTTP_STATUS" != "200" ]; then', build)

        # The reusable workflow consumes the selected image and does not
        # independently query GHCR or inspect image labels.
        self.assertNotIn("ghcr-check", reusable)
        self.assertNotIn("GHCR_TOKEN", reusable)
        self.assertNotIn("GITHUB_ACTOR:", reusable)
        self.assertNotIn("io.abu9.actions-wrt.toolchain-fingerprint=", reusable)

    def test_container_workflow_reuses_resolved_build_inputs(self) -> None:
        workflows = Path(__file__).resolve().parents[2] / "workflows"
        build = (workflows / "build.yml").read_text(encoding="utf-8")
        push = (workflows / "push-containers.yml").read_text(encoding="utf-8")

        # A build caller passes its already-resolved target metadata and
        # fingerprint. Manual dispatch retains the self-resolving path.
        self.assertIn("if: ${{ inputs.target == '' }}", push)
        self.assertIn("inputs.tools_fingerprint == ''", push)
        self.assertIn("inputs.target || needs.determine_targets.outputs.target", push)
        self.assertIn("inputs.repo_url || needs.determine_targets.outputs.repo_url", push)
        self.assertIn("inputs.tools_fingerprint || needs.compute-tools-fingerprint.outputs.tools_fingerprint", push)
        self.assertIn("target: ${{ needs.determine_targets.outputs.target }}", build)
        self.assertIn("tools_fingerprint: ${{ needs.tools_fingerprint.outputs.tools_fingerprint }}", build)

    def test_host_tools_cache_hit_skips_redundant_second_head_request(self) -> None:
        build = (
            Path(__file__).resolve().parents[2] / "workflows" / "build.yml"
        ).read_text(encoding="utf-8")
        ready = build.split("  tools_ready:", 1)[1].split(
            "  toolchain_fingerprint:", 1
        )[0]
        self.assertIn('if [ "$TOOLS_REQUIRED" != "true" ]; then', ready)
        self.assertIn("Host Tools image was already confirmed by the cache check.", ready)
        self.assertIn('if [ "$HTTP_STATUS" != "200" ]; then', ready)
        self.assertNotIn("checked_image:", ready)

    def test_target_config_is_parsed_without_shell_eval(self) -> None:
        workflow = (
            Path(__file__).resolve().parents[2]
            / "workflows"
            / "determine-targets.yml"
        ).read_text(encoding="utf-8")
        self.assertNotIn('eval "$(grep', workflow)
        self.assertIn('CONFIG_TARGET_BOARD="$(sed -n', workflow)
        self.assertIn('CONFIG_TARGET_SUBTARGET="$(sed -n', workflow)
        self.assertIn('CONFIG_ARCH="$(sed -n', workflow)

    def test_image_tags_remain_content_addressed(self) -> None:
        root = Path(__file__).resolve().parents[2] / "workflows"
        build = (root / "build.yml").read_text(encoding="utf-8")
        push = (root / "push-containers.yml").read_text(encoding="utf-8")

        self.assertIn('TAG="$AUTHOR"_"$REPO_BRANCH-$EXPECTED_FINGERPRINT"', build)
        self.assertIn('TAG="$TARGET-$SUBTARGET-$AUTHOR"_"$REPO_BRANCH-${FINGERPRINT:0:32}"', build)
        self.assertIn("tools:${{ needs.determine_container_info.outputs.container_tag }}-${{ inputs.tools_fingerprint || needs.compute-tools-fingerprint.outputs.tools_fingerprint }}", push)
        self.assertIn("toolchain:${{ inputs.target || needs.determine_targets.outputs.target }}-${{ inputs.subtarget || needs.determine_targets.outputs.subtarget }}-${{ needs.determine_container_info.outputs.container_tag }}-${{ needs.compute-toolchain-fingerprint.outputs.toolchain_cache_key }}", push)

    def test_toolchain_build_uses_same_run_host_tools_artifact_in_parallel(self) -> None:
        workflows = Path(__file__).resolve().parents[2] / "workflows"
        push = (workflows / "push-containers.yml").read_text(encoding="utf-8")
        fingerprint = (workflows / "fingerprint.yml").read_text(encoding="utf-8")
        reusable = (workflows / "reusable_build.yml").read_text(encoding="utf-8")
        dockerfile = (
            Path(__file__).resolve().parents[2]
            / "dockerfiles"
            / "Dockerfile.toolchain"
        ).read_text(encoding="utf-8")

        toolchain_fp = push.split("  compute-toolchain-fingerprint:", 1)[1].split(
            "  build-target-toolchain:", 1
        )[0]
        self.assertIn("needs.build-linux-buildbot.result == 'success'", toolchain_fp)
        self.assertNotIn("needs.push-tools-container", toolchain_fp)
        self.assertIn("use_prebuilt_tools_artifact: true", toolchain_fp)
        toolchain_build = push.split("  build-target-toolchain:", 1)[1].split(
            "  push-target-toolchain:", 1
        )[0]
        self.assertIn("needs.build-linux-buildbot", toolchain_build)
        self.assertNotIn("push-tools-container", toolchain_build)
        self.assertIn("use_prebuilt_tools_artifact: true", toolchain_build)
        self.assertIn("name: linux-buildbot-prebuilt-tools", push)
        self.assertIn("use_prebuilt_tools_artifact", fingerprint)
        self.assertIn("actions/download-artifact@v8.0.2", fingerprint)
        self.assertIn("use_prebuilt_tools_artifact", reusable)
        self.assertIn("actions/download-artifact@v8.0.2", reusable)
        self.assertIn("container=openwrt/buildbot/buildworker-v3.11.8:v23", reusable)
        self.assertEqual(
            dockerfile.splitlines()[0],
            "FROM ghcr.io/openwrt/buildbot/buildworker-v3.11.8:v23",
        )
        self.assertIn(
            "COPY --chown=buildbot staging_dir/host /prebuilt_tools/staging_dir/host",
            dockerfile,
        )
        self.assertIn(
            "COPY --chown=buildbot build_dir/host /prebuilt_tools/build_dir/host",
            dockerfile,
        )
        self.assertNotIn("FROM ghcr.io/$OWNER_LC/tools:", dockerfile)
        self.assertIn("host-tools:prebuilt-artifact", fingerprint)
        self.assertIn("ghcr.io/openwrt/buildbot/buildworker-v3.11.8:v23", fingerprint)

if __name__ == "__main__":
    unittest.main(verbosity=2)
