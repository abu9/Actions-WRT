#!/usr/bin/env python3
"""Build-free regression tests."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError, URLError

SCRIPT = Path(__file__).resolve().parents[1] / "fingerprint.py"
SPEC = importlib.util.spec_from_file_location("fingerprint", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
FINGERPRINT_MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FINGERPRINT_MODULE)


class WorkflowIntegrationTests(unittest.TestCase):
    def test_legacy_openwrt_21_02_source_is_not_supported(self) -> None:
        workflows = Path(__file__).resolve().parents[2] / "workflows"
        for name in ("build.yml", "push-containers.yml", "determine-targets.yml"):
            source = (workflows / name).read_text(encoding="utf-8")
            self.assertNotIn("openwrt-21.02", source)
            self.assertNotIn("hanwckf/immortalwrt-mt798x", source)

    def test_build_workflows_match_fingerprint_recipes(self) -> None:
        root = Path(__file__).resolve().parents[2]
        tools_workflow = (root / "workflows" / "reusable_build_tools.yml").read_text(
            encoding="utf-8"
        )
        for setting in (
            "CONFIG_DEVEL=y",
            "CONFIG_AUTOREMOVE=y",
            "CONFIG_CCACHE=y",
        ):
            self.assertIn(f"echo {setting} >> .config", tools_workflow)
            self.assertIn(setting, FINGERPRINT_MODULE.HOST_TOOLS_CONFIG)
        self.assertNotIn("CONFIG_BUILD_ALL_HOST_TOOLS", tools_workflow)
        self.assertNotIn("CONFIG_BUILD_ALL_HOST_TOOLS", FINGERPRINT_MODULE.HOST_TOOLS_CONFIG)
        self.assertIn("make defconfig", tools_workflow)
        self.assertIn("make tools/install", tools_workflow)
        self.assertIn("make-defconfig", FINGERPRINT_MODULE.HOST_TOOLS_BUILD_RECIPE)
        self.assertIn("make-tools-install", FINGERPRINT_MODULE.HOST_TOOLS_BUILD_RECIPE)

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
        for recipe_command in (
            "make-defconfig",
            "make-tools-install",
            "make-toolchain-install",
            "make-target-toolchain-compile",
            "./scripts/ext-tools.sh --refresh",
        ):
            self.assertIn(recipe_command, FINGERPRINT_MODULE.TOOLCHAIN_BUILD_RECIPE)

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

    def test_build_workflow_passes_authentication_to_both_ghcr_checks(self) -> None:
        workflow_path = (
            Path(__file__).resolve().parents[2]
            / "workflows"
            / "build.yml"
        )
        workflow = workflow_path.read_text(encoding="utf-8")

        self.assertGreaterEqual(
            workflow.count("GITHUB_ACTOR: ${{ github.actor }}"), 2
        )
        self.assertGreaterEqual(
            workflow.count("GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}"), 2
        )

    def test_ghcr_reuse_is_content_addressed_not_source_commit_addressed(self) -> None:
        root = Path(__file__).resolve().parents[2] / "workflows"

        for name in ("build.yml", "reusable_build.yml"):
            workflow = (root / name).read_text(encoding="utf-8")
            self.assertNotIn("io.abu9.actions-wrt.tools-source-commit=", workflow)
            self.assertNotIn(
                "SOURCE_COMMIT: ${{ needs.tools_fingerprint.outputs.source_commit }}",
                workflow,
            )

        build_workflow = (root / "build.yml").read_text(encoding="utf-8")
        self.assertIn(
            'io.abu9.actions-wrt.tools-source-repository=$SOURCE_REPOSITORY',
            build_workflow,
        )
        reusable = (root / "reusable_build.yml").read_text(encoding="utf-8")
        workflow_lines = reusable.splitlines()
        start = workflow_lines.index("      - name: Determine container name")
        end = workflow_lines.index("      - name: Determine container", start + 1)
        block = "\n".join(workflow_lines[start:end])
        self.assertIn("GITHUB_ACTOR: ${{ github.actor }}", block)
        self.assertIn("GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}", block)
        self.assertNotIn("SOURCE_COMMIT", block)
        self.assertNotIn("GHCR_USER:", block)
        self.assertNotIn("GHCR_PASSWORD:", block)
        self.assertNotIn("tools_image_fingerprint", reusable)



    def test_toolchain_fingerprint_handoff_separates_buildbot_and_runner_outputs(self) -> None:
        workflow = (
            Path(__file__).resolve().parents[2]
            / "workflows"
            / "fingerprint.yml"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'run: install -o buildbot -g buildbot -m 600 /dev/null "$RUNNER_TEMP/toolchain-fingerprint.outputs"',
            workflow,
        )
        self.assertIn('shell: su buildbot -c "sh -e {0}"', workflow)
        self.assertIn('--output-file "$FINGERPRINT_OUTPUT_FILE"', workflow)
        self.assertIn("Validate and publish Toolchain fingerprint outputs", workflow)
        self.assertIn("publish-toolchain-outputs", workflow)
        self.assertTrue(hasattr(FINGERPRINT_MODULE, "publish_toolchain_outputs"))
        self.assertIn('id: fp', workflow)
        toolchain_workflow = workflow.split("  toolchain:" + chr(10), 1)[1]
        self.assertNotIn("id: fp" + chr(10) + "        env:" + chr(10) + "          SOURCE_REPO:", toolchain_workflow)

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
        self.assertIn("host-tools:prebuilt-artifact", FINGERPRINT_MODULE.TOOLCHAIN_BUILD_RECIPE)
        self.assertEqual(
            FINGERPRINT_MODULE.TOOLCHAIN_BUILD_ENVIRONMENT["buildworker_image"],
            "ghcr.io/openwrt/buildbot/buildworker-v3.11.8:v23",
        )

if __name__ == "__main__":
    unittest.main(verbosity=2)
