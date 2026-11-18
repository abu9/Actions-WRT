#!/usr/bin/env python3
"""Build-free regression tests for fingerprint.py."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
from urllib.error import HTTPError, URLError
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "fingerprint.py"
SPEC = importlib.util.spec_from_file_location("fingerprint", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
FINGERPRINT_MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FINGERPRINT_MODULE)


class ToolchainFingerprintTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="toolchain-fingerprint-")
        self.root = Path(self.temp_dir.name)
        self.source = self.root / "openwrt"
        self.local = self.root / "actions-wrt"
        self.source.mkdir()
        self.local.mkdir()

        files = {
            "Makefile": "include $(TOPDIR)/rules.mk\n",
            "rules.mk": (
                "TARGET_OPTIMIZATION:=$(CONFIG_TARGET_OPTIMIZATION)\n"
                "CPU:=$(CONFIG_CPU_TYPE)\n"
                "ARCH:=$(CONFIG_ARCH)\n"
            ),
            "Config.in": 'source "toolchain/Config.in"\n',
            "include/toplevel.mk": "include $(TOPDIR)/include/verbose.mk\n",
            "include/verbose.mk": "QUIET:=1\n",
            "include/debug.mk": "DEBUG:=0\n",
            "include/target.mk": (
                "ARCH:=$(CONFIG_ARCH)\n"
                "CPU_TYPE:=$(CONFIG_CPU_TYPE)\n"
            ),
            "include/toolchain-build.mk": "include $(INCLUDE_DIR)/host-build.mk\n",
            "include/host-build.mk": "include $(INCLUDE_DIR)/download.mk\n",
            "include/hardening.mk": "ifdef CONFIG_PKG_SSP\nendif\n",
            "include/download.mk": "DOWNLOAD:=1\n",
            "include/unpack.mk": "UNPACK:=1\n",
            "include/quilt.mk": "PATCH:=patch\n",
            "include/autotools.mk": "AUTOTOOLS:=1\n",
            "include/depends.mk": "DEPENDS:=1\n",
            "include/subdir.mk": "SUBDIR:=1\n",
            "include/prereq.mk": "PREREQ:=1\n",
            "include/kernel.mk": "KERNEL_CFLAGS:=$(CONFIG_KERNEL_CFLAGS)\n",
            "include/kernel-build.mk": "include $(INCLUDE_DIR)/kernel-defaults.mk\n",
            "include/kernel-defaults.mk": "PATCH_KERNEL:=1\n",
            "include/kernel-version.mk": "include $(KERNEL_DETAILS_FILE)\n",
            "include/version.mk": "VERSION_CODE:=$(CONFIG_VERSION_CODE)\n",
            "include/scan.mk": "scan\n",
            "scripts/kconfig.pl": "config\n",
            "scripts/metadata.pm": "metadata\n",
            "scripts/target-metadata.pl": "metadata\n",
            "scripts/patch-kernel.sh": "#!/bin/sh\n",
            "COPYING": "GPL-2.0-only license metadata\n",
            "LICENSES/GPL-2.0": "license text\n",
            "scripts/ext-tools.sh": "#!/usr/bin/env bash\n# upstream refresh implementation fixture\n",
            "scripts/config/conf.c": "int main(void) { return 0; }\n",
            "target/linux/Makefile": "include $(INCLUDE_DIR)/target.mk\n",
            "target/linux/mediatek/Makefile": (
                "ARCH:=arm\nBOARD:=mediatek\nKERNEL_PATCHVER:=6.12\n"
            ),
            "target/linux/mediatek/filogic/target.mk": (
                "ARCH:=aarch64\nSUBTARGET:=filogic\nCPU_TYPE:=cortex-a53\n"
                "DEFAULT_PROFILE:=openwrt_one\n"
            ),
            "target/linux/generic/kernel-6.12": "LINUX_VERSION-6.12 = .116\n",
            "target/linux/generic/backport-6.12/010-uapi.patch": (
                "--- a/include/uapi/linux/example.h\n"
                "+++ b/include/uapi/linux/example.h\n"
                "@@ -1 +1 @@\n-old\n+new\n"
            ),
            "target/linux/mediatek/patches-6.12/001-dts.patch": (
                "--- a/arch/arm64/boot/dts/mediatek/foo.dts\n"
                "+++ b/arch/arm64/boot/dts/mediatek/foo.dts\n"
                "@@ -1 +1 @@\n-old\n+new\n"
            ),
            "toolchain/Config.in": 'source "toolchain/gcc/Config.in"\n',
            "toolchain/Makefile": "all: gcc\n",
            "toolchain/gcc/Config.in": "config GCC_VERSION\n string\n",
            "toolchain/gcc/Makefile": (
                "EXTRA_GCC_OPTIONS:=$(CONFIG_EXTRA_GCC_CONFIG_OPTIONS)\n"
            ),
            "target/toolchain/Makefile": "all: install\n",
            "target/toolchain/Config.in": "config EXT\n bool\n",
            "package/foo/Makefile": "all:\n\techo package\n",
            "target/linux/mediatek/image/Makefile": "DEFAULT_PROFILE:=profile-a\n",
            "include/site/aarch64": "CFLAGS=-O2\n",
        }
        for relative, content in files.items():
            self.write(self.source / relative, content)

        self.write(
            self.local / ".github/dockerfiles/Dockerfile.toolchain",
            "FROM ghcr.io/test/tools:test\n",
        )
        self.write_config("openwrt_one")

        subprocess.run(["git", "init", "-q", str(self.source)], check=True)
        subprocess.run(
            ["git", "-C", str(self.source), "config", "user.email", "test@example.invalid"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.source), "config", "user.name", "Fingerprint Test"],
            check=True,
        )
        subprocess.run(["git", "-C", str(self.source), "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", str(self.source), "commit", "-qm", "fixture"],
            check=True,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def write_config(self, profile: str, cpu_type: str = "cortex-a53",
                     extra_gcc: str = "--with-foo") -> None:
        self.write(
            self.source / ".config",
            'CONFIG_ARCH="aarch64"\n'
            'CONFIG_TARGET_OPTIMIZATION="-O2"\n'
            f'CONFIG_CPU_TYPE="{cpu_type}"\n'
            f'CONFIG_EXTRA_GCC_CONFIG_OPTIONS="{extra_gcc}"\n'
            'CONFIG_KERNEL_CFLAGS="-O2"\n'
            "CONFIG_TARGET_mediatek=y\n"
            "CONFIG_TARGET_mediatek_filogic=y\n"
            f"CONFIG_TARGET_mediatek_filogic_DEVICE_{profile}=y\n",
        )

    def fingerprint(self, tools_fingerprint: str = "a" * 64) -> str:
        return FINGERPRINT_MODULE.compute_toolchain_fingerprint(
            source_dir=self.source,
            local_repo_dir=self.local,
            source_repo="immortalwrt/immortalwrt",
            target="mediatek",
            subtarget="filogic",
            tools_fingerprint=tools_fingerprint,
        )[0]

    def test_license_metadata_does_not_invalidate_toolchain_fingerprint(self) -> None:
        baseline = self.fingerprint()
        self.write(self.source / "COPYING", "updated license metadata\n")
        self.write(self.source / "LICENSES/GPL-2.0", "updated license text\n")
        fingerprint, manifest = FINGERPRINT_MODULE.compute_toolchain_fingerprint(
            source_dir=self.source,
            local_repo_dir=self.local,
            source_repo="immortalwrt/immortalwrt",
            target="mediatek",
            subtarget="filogic",
            tools_fingerprint="a" * 64,
        )
        self.assertEqual(fingerprint, baseline)
        paths = {item["path"] for item in manifest["files"]}
        self.assertNotIn("COPYING", paths)
        self.assertNotIn("LICENSES/GPL-2.0", paths)

    def test_unrelated_package_and_device_image_makefiles_do_not_invalidate(self) -> None:
        baseline = self.fingerprint()
        self.write(self.source / "package/foo/Makefile", "all:\n\techo unrelated\n")
        self.write(
            self.source / "target/linux/mediatek/image/Makefile",
            "DEFAULT_PROFILE:=profile-b\n",
        )
        self.assertEqual(self.fingerprint(), baseline)

    def test_device_profile_selection_and_default_profile_do_not_invalidate(self) -> None:
        baseline = self.fingerprint()
        self.write(
            self.source / "target/linux/mediatek/filogic/target.mk",
            "ARCH:=aarch64\nSUBTARGET:=filogic\nCPU_TYPE:=cortex-a53\n"
            "DEFAULT_PROFILE:=openwrt_two\n",
        )
        self.write_config("openwrt_two")
        fingerprint, manifest = FINGERPRINT_MODULE.compute_toolchain_fingerprint(
            source_dir=self.source,
            local_repo_dir=self.local,
            source_repo="immortalwrt/immortalwrt",
            target="mediatek",
            subtarget="filogic",
            tools_fingerprint="a" * 64,
        )
        self.assertEqual(fingerprint, baseline)
        self.assertFalse(any("_DEVICE_" in key for key in manifest["toolchain_config"]))

    def test_unrelated_dts_patch_does_not_invalidate(self) -> None:
        baseline = self.fingerprint()
        self.write(
            self.source / "target/linux/mediatek/patches-6.12/001-dts.patch",
            "--- a/arch/arm64/boot/dts/mediatek/foo.dts\n"
            "+++ b/arch/arm64/boot/dts/mediatek/foo.dts\n"
            "@@ -1 +1 @@\n-old\n+changed\n",
        )
        self.assertEqual(self.fingerprint(), baseline)

    def test_toolchain_source_change_invalidates(self) -> None:
        baseline = self.fingerprint()
        self.write(
            self.source / "toolchain/gcc/Makefile",
            "EXTRA_GCC_OPTIONS:=$(CONFIG_EXTRA_GCC_CONFIG_OPTIONS)\n# changed\n",
        )
        self.assertNotEqual(self.fingerprint(), baseline)

    def test_ext_tools_refresh_script_change_invalidates(self) -> None:
        baseline = self.fingerprint()
        self.write(
            self.source / "scripts/ext-tools.sh",
            "#!/usr/bin/env bash\\n# changed upstream refresh implementation\\n",
        )
        self.assertNotEqual(self.fingerprint(), baseline)

    def test_missing_ext_tools_refresh_script_fails_closed(self) -> None:
        script = self.source / "scripts/ext-tools.sh"
        script.unlink()
        subprocess.run(
            ["git", "-C", str(self.source), "rm", "-q", "--cached", "scripts/ext-tools.sh"],
            check=True,
        )
        with self.assertRaisesRegex(
            RuntimeError, "Required Toolchain input is not tracked: scripts/ext-tools.sh"
        ):
            self.fingerprint()

    def test_toolchain_configuration_change_invalidates(self) -> None:
        baseline = self.fingerprint()
        self.write_config("openwrt_one", extra_gcc="--with-bar")
        self.assertNotEqual(self.fingerprint(), baseline)

    def test_target_cpu_configuration_change_invalidates(self) -> None:
        baseline = self.fingerprint()
        self.write_config("openwrt_one", cpu_type="cortex-a55")
        self.assertNotEqual(self.fingerprint(), baseline)

    def test_host_tools_dependency_change_invalidates(self) -> None:
        baseline = self.fingerprint()
        self.assertNotEqual(self.fingerprint("b" * 64), baseline)

    def test_toolchain_outputs_can_use_explicit_handoff_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fingerprint-output-") as directory:
            output = Path(directory) / "handoff"
            with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(Path(directory) / "runner-output")}):
                FINGERPRINT_MODULE.write_outputs(
                    {"toolchain_fingerprint": "a" * 64, "toolchain_cache_key": "a" * 32,
                     "source_commit": "b" * 40},
                    output,
                )
            self.assertEqual(
                output.read_text(encoding="utf-8").splitlines(),
                ["toolchain_fingerprint=" + "a" * 64,
                 "toolchain_cache_key=" + "a" * 32,
                 "source_commit=" + "b" * 40],
            )
            self.assertFalse((Path(directory) / "runner-output").exists())

    def test_toolchain_output_publisher_validates_and_consumes_handoff(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fingerprint-publish-") as directory:
            root = Path(directory)
            handoff = root / "handoff"
            github_output = root / "github-output"
            fingerprint = "a" * 64
            commit = "b" * 40
            handoff.write_text(
                f"toolchain_fingerprint={fingerprint}\n"
                f"toolchain_cache_key={fingerprint[:32]}\n"
                f"source_commit={commit}\n",
                encoding="utf-8",
            )
            FINGERPRINT_MODULE.publish_toolchain_outputs(handoff, commit, github_output)
            self.assertEqual(
                set(github_output.read_text(encoding="utf-8").splitlines()),
                {
                    f"toolchain_fingerprint={fingerprint}",
                    f"toolchain_cache_key={fingerprint[:32]}",
                    f"source_commit={commit}",
                },
            )
            self.assertFalse(handoff.exists())

    def test_toolchain_output_publisher_rejects_invalid_handoffs(self) -> None:
        fingerprint = "a" * 64
        commit = "b" * 40
        valid = [
            f"toolchain_fingerprint={fingerprint}",
            f"toolchain_cache_key={fingerprint[:32]}",
            f"source_commit={commit}",
        ]
        invalid_cases = [
            ("missing", valid[:2], commit),
            ("duplicate", valid + [valid[0]], commit),
            ("fingerprint", [f"toolchain_fingerprint={'x' * 64}", valid[1], valid[2]], commit),
            ("cache-key", [valid[0], f"toolchain_cache_key={'c' * 32}", valid[2]], commit),
            ("source-commit", [valid[0], valid[1], f"source_commit={'d' * 40}"], commit),
            ("expected-commit", valid, "not-a-commit"),
        ]
        for name, records, expected in invalid_cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                handoff = root / "handoff"
                github_output = root / "github-output"
                handoff.write_text("\n".join(records) + "\n", encoding="utf-8")
                with self.assertRaises(ValueError):
                    FINGERPRINT_MODULE.publish_toolchain_outputs(
                        handoff, expected, github_output
                    )
                self.assertFalse(github_output.exists())
                self.assertTrue(handoff.exists())

    def test_exported_kernel_header_patch_change_invalidates(self) -> None:
        baseline = self.fingerprint()
        self.write(
            self.source / "target/linux/generic/backport-6.12/010-uapi.patch",
            "--- a/include/uapi/linux/example.h\n"
            "+++ b/include/uapi/linux/example.h\n"
            "@@ -1 +1 @@\n-old\n+changed\n",
        )
        self.assertNotEqual(self.fingerprint(), baseline)


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

class HostToolsFingerprintTests(unittest.TestCase):
    def test_unrelated_package_and_target_files_do_not_change_host_tools_fingerprint(self):
        with tempfile.TemporaryDirectory(prefix="host-tools-fingerprint-") as d:
            root=Path(d); source=root/"openwrt"; local=root/"actions-wrt"; source.mkdir(); local.mkdir()
            for rel in ["Makefile","rules.mk","Config.in","include/a","tools/a","toolchain/Config.in","toolchain/gcc/Config.in","toolchain/gcc/Makefile","scripts/a","scripts/ext-tools.sh","config/a","package/foo/Makefile","target/x/Config.in"]:
                p=source/rel; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(rel,encoding="utf-8")
            (local/".github/workflows").mkdir(parents=True); (local/".github/dockerfiles").mkdir(parents=True)
            (local/".github/workflows/reusable_build_tools.yml").write_text("container: ghcr.io/openwrt/buildbot/buildworker-v3.11.8:v23\n",encoding="utf-8")
            (local/".github/dockerfiles/Dockerfile.tools").write_text("FROM ghcr.io/openwrt/buildbot/buildworker-v3.11.8:v23\n",encoding="utf-8")
            subprocess.run(["git","init","-q",str(source)],check=True); subprocess.run(["git","-C",str(source),"config","user.email","test@example.invalid"],check=True); subprocess.run(["git","-C",str(source),"config","user.name","Fingerprint Test"],check=True); subprocess.run(["git","-C",str(source),"add","-A"],check=True); subprocess.run(["git","-C",str(source),"commit","-qm","fixture"],check=True)
            baseline,manifest=FINGERPRINT_MODULE.compute_tools_fingerprint(source,local,"test/repo")
            self.assertFalse(any(item["path"] == "scripts/ext-tools.sh" for item in manifest["files"]))
            (source/"scripts/ext-tools.sh").write_text("changed refresh implementation",encoding="utf-8")
            (source/"toolchain/gcc/Makefile").write_text("changed toolchain implementation",encoding="utf-8")
            (source/"package/foo/Makefile").write_text("changed package",encoding="utf-8")
            (source/"target/x/Config.in").write_text("changed target",encoding="utf-8")
            updated,_=FINGERPRINT_MODULE.compute_tools_fingerprint(source,local,"test/repo")
            self.assertEqual(baseline,updated)

    def test_toolchain_kconfig_changes_invalidate_host_tools_fingerprint(self):
        with tempfile.TemporaryDirectory(prefix="host-tools-kconfig-") as d:
            root=Path(d); source=root/"openwrt"; local=root/"actions-wrt"; source.mkdir(); local.mkdir()
            for rel in ["Makefile","rules.mk","Config.in","include/a","tools/a","toolchain/Config.in","toolchain/gcc/Config.in","scripts/a","config/a"]:
                p=source/rel; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(rel,encoding="utf-8")
            (local/".github/workflows").mkdir(parents=True); (local/".github/dockerfiles").mkdir(parents=True)
            (local/".github/workflows/reusable_build_tools.yml").write_text("container: ghcr.io/openwrt/buildbot/buildworker-v3.11.8:v23\n",encoding="utf-8")
            (local/".github/dockerfiles/Dockerfile.tools").write_text("FROM ghcr.io/openwrt/buildbot/buildworker-v3.11.8:v23\n",encoding="utf-8")
            subprocess.run(["git","init","-q",str(source)],check=True); subprocess.run(["git","-C",str(source),"config","user.email","test@example.invalid"],check=True); subprocess.run(["git","-C",str(source),"config","user.name","Fingerprint Test"],check=True); subprocess.run(["git","-C",str(source),"add","-A"],check=True); subprocess.run(["git","-C",str(source),"commit","-qm","fixture"],check=True)
            baseline,_=FINGERPRINT_MODULE.compute_tools_fingerprint(source,local,"test/repo")
            (source/"toolchain/gcc/Config.in").write_text("changed Kconfig definition",encoding="utf-8")
            updated,_=FINGERPRINT_MODULE.compute_tools_fingerprint(source,local,"test/repo")
            self.assertNotEqual(baseline,updated)


class GHCRCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old_actor = os.environ.get("GITHUB_ACTOR")
        self.old_token = os.environ.get("GITHUB_TOKEN")
        os.environ["GITHUB_ACTOR"] = "test-actor"
        os.environ["GITHUB_TOKEN"] = "test-token"

    def tearDown(self) -> None:
        if self.old_actor is None:
            os.environ.pop("GITHUB_ACTOR", None)
        else:
            os.environ["GITHUB_ACTOR"] = self.old_actor
        if self.old_token is None:
            os.environ.pop("GITHUB_TOKEN", None)
        else:
            os.environ["GITHUB_TOKEN"] = self.old_token

    @staticmethod
    def response(payload: dict):
        class Response:
            def __enter__(self):
                return self
            def __exit__(self, exc_type, exc, tb):
                return False
            def read(self):
                return json.dumps(payload).encode("utf-8")
        return Response()

    def test_matching_labels_pass(self) -> None:
        digest = "sha256:" + "a" * 64
        responses = [
            self.response({"token": "pull-token"}),
            self.response({"config": {"digest": digest}}),
            self.response({"config": {"Labels": {"fingerprint": "abc", "dependency": "def"}}}),
        ]
        with mock.patch.object(FINGERPRINT_MODULE, "urlopen", side_effect=responses):
            self.assertTrue(
                FINGERPRINT_MODULE.ghcr_check(
                    "owner", "image", "tag",
                    {"fingerprint": "abc", "dependency": "def"},
                )
            )

    def test_label_mismatch_fails(self) -> None:
        digest = "sha256:" + "b" * 64
        responses = [
            self.response({"token": "pull-token"}),
            self.response({"config": {"digest": digest}}),
            self.response({"config": {"Labels": {"fingerprint": "wrong"}}}),
        ]
        with mock.patch.object(FINGERPRINT_MODULE, "urlopen", side_effect=responses):
            with self.assertRaisesRegex(RuntimeError, "labels do not match"):
                FINGERPRINT_MODULE.ghcr_check(
                    "owner", "image", "tag", {"fingerprint": "expected"}
                )

    def test_invalid_inputs_fail_before_network(self) -> None:
        with mock.patch.object(FINGERPRINT_MODULE, "urlopen") as mocked:
            with self.assertRaises(ValueError):
                FINGERPRINT_MODULE.ghcr_check(
                    "OWNER/invalid", "image", "tag", {"fingerprint": "abc"}
                )
            mocked.assert_not_called()

    def test_empty_expected_labels_fail_before_network(self) -> None:
        with mock.patch.object(FINGERPRINT_MODULE, "urlopen") as mocked:
            with self.assertRaises(ValueError):
                FINGERPRINT_MODULE.ghcr_check("owner", "image", "tag", {})
            mocked.assert_not_called()

    def test_unexpected_cli_exception_exits_as_error_not_cache_miss(self) -> None:
        argv = [
            "fingerprint.py", "ghcr-check", "--owner", "owner",
            "--image", "tools", "--tag", "tag",
            "--label", "fingerprint=expected",
        ]
        with mock.patch.object(FINGERPRINT_MODULE.sys, "argv", argv):
            with mock.patch.object(
                FINGERPRINT_MODULE, "ghcr_check",
                side_effect=TypeError("unexpected registry response shape"),
            ):
                with self.assertRaises(SystemExit) as raised:
                    FINGERPRINT_MODULE.run_cli()
        self.assertEqual(raised.exception.code, 2)

    def test_malformed_token_response_fails_closed(self) -> None:
        for payload in ([], "not-an-object", None):
            with self.subTest(payload=payload), mock.patch.object(
                FINGERPRINT_MODULE, "urlopen", return_value=self.response(payload)
            ):
                with self.assertRaisesRegex(RuntimeError, "token response is not a JSON object"):
                    FINGERPRINT_MODULE.ghcr_check(
                        "owner", "image", "tag", {"fingerprint": "expected"}
                    )

    def test_malformed_tag_manifest_fails_closed(self) -> None:
        for payload in ([], "not-an-object", None):
            with self.subTest(payload=payload), mock.patch.object(
                FINGERPRINT_MODULE, "urlopen",
                side_effect=[self.response({"token": "pull-token"}), self.response(payload)],
            ):
                with self.assertRaisesRegex(RuntimeError, "invalid tag manifest"):
                    FINGERPRINT_MODULE.ghcr_check(
                        "owner", "image", "tag", {"fingerprint": "expected"}
                    )

    def test_malformed_platform_manifest_list_fails_closed(self) -> None:
        with mock.patch.object(
            FINGERPRINT_MODULE, "urlopen",
            side_effect=[
                self.response({"token": "pull-token"}),
                self.response({"manifests": "not-a-list"}),
            ],
        ):
            with self.assertRaisesRegex(RuntimeError, "invalid platform manifest list"):
                FINGERPRINT_MODULE.ghcr_check(
                    "owner", "image", "tag", {"fingerprint": "expected"}
                )

    def test_malformed_image_config_descriptor_fails_closed(self) -> None:
        for descriptor in ([], "not-an-object", None):
            with self.subTest(descriptor=descriptor), mock.patch.object(
                FINGERPRINT_MODULE, "urlopen",
                side_effect=[
                    self.response({"token": "pull-token"}),
                    self.response({"config": descriptor}),
                ],
            ):
                with self.assertRaisesRegex(RuntimeError, "invalid config descriptor"):
                    FINGERPRINT_MODULE.ghcr_check(
                        "owner", "image", "tag", {"fingerprint": "expected"}
                    )

    def test_malformed_image_config_fails_closed(self) -> None:
        digest = "sha256:" + "f" * 64
        for payload in ([], {"config": []}, {"config": {"Labels": []}}):
            with self.subTest(payload=payload), mock.patch.object(
                FINGERPRINT_MODULE, "urlopen",
                side_effect=[
                    self.response({"token": "pull-token"}),
                    self.response({"config": {"digest": digest}}),
                    self.response(payload),
                ],
            ):
                expected = "invalid labels" if payload == {"config": {"Labels": []}} else "invalid image config"
                with self.assertRaisesRegex(RuntimeError, expected):
                    FINGERPRINT_MODULE.ghcr_check(
                        "owner", "image", "tag", {"fingerprint": "expected"}
                    )

    def test_token_endpoint_http_errors_never_become_cache_misses(self) -> None:
        for status in (401, 403, 404, 429, 500, 503):
            with self.subTest(status=status):
                error = HTTPError(
                    "https://ghcr.io/token?scope=repository:owner/image:pull",
                    status, "registry error", {}, None,
                )
                with mock.patch.object(
                    FINGERPRINT_MODULE, "urlopen", side_effect=error
                ):
                    with self.assertRaisesRegex(
                        RuntimeError, f"GHCR token request failed: HTTP {status}"
                    ):
                        FINGERPRINT_MODULE.ghcr_check(
                            "owner", "image", "tag", {"fingerprint": "expected"}
                        )

    def test_token_endpoint_network_errors_never_become_cache_misses(self) -> None:
        with mock.patch.object(
            FINGERPRINT_MODULE, "urlopen",
            side_effect=URLError("simulated registry connection failure"),
        ):
            with self.assertRaises(URLError):
                FINGERPRINT_MODULE.ghcr_check(
                    "owner", "image", "tag", {"fingerprint": "expected"}
                )

    def test_manifest_auth_or_server_errors_are_not_cache_misses(self) -> None:
        for status in (401, 403, 429, 500, 503):
            with self.subTest(status=status):
                error = HTTPError(
                    "https://ghcr.io/v2/owner/image/manifests/tag",
                    status, "registry error", {}, None,
                )
                with mock.patch.object(
                    FINGERPRINT_MODULE, "urlopen",
                    side_effect=[self.response({"token": "pull-token"}), error],
                ):
                    with self.assertRaisesRegex(
                        RuntimeError, f"GHCR request failed: HTTP {status}"
                    ):
                        FINGERPRINT_MODULE.ghcr_check(
                            "owner", "image", "tag", {"fingerprint": "expected"}
                        )

    def test_missing_referenced_platform_manifest_is_a_registry_error(self) -> None:
        index = {
            "manifests": [
                {
                    "digest": "sha256:" + "e" * 64,
                    "platform": {"os": "linux", "architecture": "amd64"},
                }
            ]
        }
        missing = HTTPError(
            "https://ghcr.io/v2/owner/image/manifests/sha256:missing",
            404, "referenced manifest not found", {}, None,
        )
        with mock.patch.object(
            FINGERPRINT_MODULE, "urlopen",
            side_effect=[self.response({"token": "pull-token"}), self.response(index), missing],
        ):
            with self.assertRaisesRegex(RuntimeError, "GHCR request failed: HTTP 404"):
                FINGERPRINT_MODULE.ghcr_check(
                    "owner", "image", "tag", {"fingerprint": "expected"}
                )

    def test_missing_config_blob_is_a_registry_error(self) -> None:
        manifest = {"config": {"digest": "sha256:" + "c" * 64}}
        missing = HTTPError(
            "https://ghcr.io/v2/owner/image/blobs/sha256:missing",
            404, "config blob not found", {}, None,
        )
        with mock.patch.object(
            FINGERPRINT_MODULE, "urlopen",
            side_effect=[self.response({"token": "pull-token"}), self.response(manifest), missing],
        ):
            with self.assertRaisesRegex(RuntimeError, "GHCR request failed: HTTP 404"):
                FINGERPRINT_MODULE.ghcr_check(
                    "owner", "image", "tag", {"fingerprint": "expected"}
                )

    def test_missing_manifest_returns_false(self) -> None:
        not_found = HTTPError(
            "https://ghcr.io/v2/owner/image/manifests/missing",
            404, "Not Found", {}, None,
        )
        with mock.patch.object(
            FINGERPRINT_MODULE, "urlopen",
            side_effect=[self.response({"token": "pull-token"}), not_found],
        ):
            self.assertFalse(
                FINGERPRINT_MODULE.ghcr_check(
                    "owner", "image", "missing", {"fingerprint": "expected"}
                )
            )

    def test_oci_index_resolves_linux_amd64_config_labels(self) -> None:
        index = {
            "manifests": [
                {
                    "digest": "sha256:" + "d" * 64,
                    "platform": {"os": "linux", "architecture": "arm64"},
                },
                {
                    "digest": "sha256:" + "e" * 64,
                    "platform": {"os": "linux", "architecture": "amd64"},
                },
            ]
        }
        image_manifest = {"config": {"digest": "sha256:" + "c" * 64}}
        image_config = {
            "config": {
                "Labels": {
                    "fingerprint": "expected",
                    "dependency": "host-tools",
                }
            }
        }
        with mock.patch.object(
            FINGERPRINT_MODULE, "urlopen",
            side_effect=[
                self.response({"token": "pull-token"}),
                self.response(index),
                self.response(image_manifest),
                self.response(image_config),
            ],
        ):
            self.assertTrue(
                FINGERPRINT_MODULE.ghcr_check(
                    "owner", "image", "tag",
                    {"fingerprint": "expected", "dependency": "host-tools"},
                )
            )

    def test_oci_index_without_linux_amd64_is_an_error(self) -> None:
        index = {
            "manifests": [
                {
                    "digest": "sha256:" + "d" * 64,
                    "platform": {"os": "linux", "architecture": "arm64"},
                }
            ]
        }
        with mock.patch.object(
            FINGERPRINT_MODULE, "urlopen",
            side_effect=[
                self.response({"token": "pull-token"}),
                self.response(index),
            ],
        ):
            with self.assertRaisesRegex(RuntimeError, "no linux/amd64 image manifest"):
                FINGERPRINT_MODULE.ghcr_check(
                    "owner", "image", "tag", {"fingerprint": "expected"}
                )

    def test_missing_registry_token_fails_closed(self) -> None:
        with mock.patch.object(
            FINGERPRINT_MODULE, "urlopen", return_value=self.response({})
        ):
            with self.assertRaisesRegex(RuntimeError, "empty pull token"):
                FINGERPRINT_MODULE.ghcr_check(
                    "owner", "image", "tag", {"fingerprint": "expected"}
                )



class DiyPart1InvocationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        root = Path(__file__).resolve().parents[3]
        workflow = (root / ".github/workflows/reusable_build.yml").read_text(
            encoding="utf-8"
        )
        start = workflow.index("      - name: Load pre-custom script\n")
        end = workflow.index("\n      - name: ", start + 1)
        step = workflow[start:end]
        run_marker = "        run: |\n"
        cls.run_script = step.split(run_marker, 1)[1]
        cls.run_script = "\n".join(
            line[10:] if line.startswith("          ") else line
            for line in cls.run_script.splitlines()
        ) + "\n"

    def test_workspace_is_resolved_inside_the_buildbot_shell(self) -> None:
        self.assertIn('GITHUB_WORKSPACE="$(cd .. && pwd)"', self.run_script)
        self.assertIn("export GITHUB_WORKSPACE", self.run_script)
        self.assertIn('bash "$GITHUB_WORKSPACE/diy-part1.sh"', self.run_script)
        self.assertNotIn('bash "${GITHUB_WORKSPACE}/diy-part1.sh"', self.run_script)

    def test_diy_script_runs_without_inherited_runner_environment(self) -> None:
        with tempfile.TemporaryDirectory(prefix="diy-part1-workspace-") as temporary:
            workspace = Path(temporary).resolve()
            openwrt = workspace / "openwrt"
            openwrt.mkdir()
            script = workspace / "diy-part1.sh"
            script.write_text(
                '#!/usr/bin/env bash\n'
                'set -euo pipefail\n'
                ': "${GITHUB_WORKSPACE:?GITHUB_WORKSPACE was not resolved}"\n'
                'printf "%s\n" "$GITHUB_WORKSPACE" > "$GITHUB_WORKSPACE/observed-workspace"\n',
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    "bash",
                    "--noprofile",
                    "--norc",
                    "-eo",
                    "pipefail",
                    "-c",
                    self.run_script,
                ],
                cwd=openwrt,
                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(
                (workspace / "observed-workspace").read_text(encoding="utf-8").strip(),
                str(workspace),
            )



class ReleaseIndexVerifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[3]
        cls.helper = cls.root / ".github/scripts/verify-release-index.sh"
        cls.workflow = (cls.root / ".github/workflows/reusable_build.yml").read_text(
            encoding="utf-8"
        )

    def test_workflow_selects_distribution_specific_signature_format(self) -> None:
        self.assertIn("immortalwrt) SIGNATURE_FILE=sha256sums.sig", self.workflow)
        self.assertIn("openwrt) SIGNATURE_FILE=sha256sums.asc", self.workflow)
        self.assertIn('AUTHOR: ${{ inputs.author }}', self.workflow)
        self.assertIn("repository: openwrt/keyring", self.workflow)
        self.assertIn("repository: immortalwrt/keyring", self.workflow)
        self.assertNotIn("source-pinned release signing keyring", self.workflow)
        self.assertIn('verify "$AUTHOR" staging_dir/host/bin/usign', self.workflow)
        self.assertIn('CHECKSUM_OUTPUT="$(sha256sum --check --ignore-missing sha256sums)"', self.workflow)
        self.assertIn('grep -Fq "$TOOLCHAIN_ARCHIVE: OK" <<<"$CHECKSUM_OUTPUT"', self.workflow)
        verify_index = self.workflow.index('verify "$AUTHOR" staging_dir/host/bin/usign')
        verify_archive = self.workflow.index('CHECKSUM_OUTPUT="$(sha256sum --check --ignore-missing sha256sums)"')
        extract_archive = self.workflow.index('tar --zstd -xf "$TOOLCHAIN_ARCHIVE"')
        self.assertLess(verify_index, verify_archive)
        self.assertLess(verify_archive, extract_archive)

        lines = self.workflow.splitlines()
        download_start = next(
            index for index, line in enumerate(lines)
            if line == "      - name: Download external toolchain/sdk"
        )
        download_end = next(
            index for index in range(download_start + 1, len(lines))
            if lines[index].startswith("      - name: ")
        )
        download_step = lines[download_start:download_end]
        self.assertTrue(
            any(line.strip() == "set -euo pipefail" for line in download_step)
        )
        self.assertTrue(
            any(
                line.strip() == 'shell: su buildbot -c "bash --noprofile --norc -eo pipefail {0}"'
                for line in download_step
            )
        )
        self.assertNotIn('CHECKSUM="$(awk', self.workflow)

    def test_toolchain_install_stage_is_unconditional_like_upstream(self) -> None:
        lines = self.workflow.splitlines()
        start = next(
            index for index, line in enumerate(lines)
            if line == "      - name: Build toolchain"
        )
        end = next(
            index for index in range(start + 1, len(lines))
            if lines[index].startswith("      - name: ")
        )
        step_lines = lines[start:end]
        self.assertFalse(any(line.strip().startswith("if:") for line in step_lines))
        self.assertIn("make toolchain/install", "\\n".join(step_lines))

    @staticmethod
    def _create_keyring_checkout(root: Path, origin: str, files: dict[str, str]) -> tuple[Path, str]:
        keyring = root / "keyring"
        keyring.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(keyring)], check=True)
        subprocess.run(
            ["git", "-C", str(keyring), "config", "user.email", "keyring-test@example.invalid"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(keyring), "config", "user.name", "Keyring Test"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(keyring), "remote", "add", "origin", origin],
            check=True,
        )
        for relative, value in files.items():
            path = keyring / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value, encoding="utf-8")
        subprocess.run(["git", "-C", str(keyring), "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", str(keyring), "commit", "-qm", "keyring fixture"],
            check=True,
        )
        commit = subprocess.run(
            ["git", "-C", str(keyring), "rev-parse", "HEAD"],
            check=True, text=True, capture_output=True,
        ).stdout.strip()
        return keyring, commit

    def test_resolve_openwrt_uses_independently_checked_out_official_keyring(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-index-openwrt-resolve-") as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            names = (
                "gpg/CD54E82DADB3684D.asc",
                "gpg/0x1D53D1877742E911.asc",
                "gpg/626471F1.asc",
            )
            keyring, commit = self._create_keyring_checkout(
                root,
                "https://github.com/openwrt/keyring",
                {name: "test public key\n" for name in names},
            )
            github_env = root / "github-env"
            result = subprocess.run(
                ["bash", str(self.helper), "resolve", "openwrt", str(source), str(keyring), str(github_env)],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stdout.strip(), str(keyring / "gpg"))
            values = dict(line.split("=", 1) for line in github_env.read_text(encoding="utf-8").splitlines())
            self.assertEqual(values["KEYRING_COMMIT"], commit)
            self.assertEqual(values["KEYRING_GPG_DIR"], str(keyring / "gpg"))
            self.assertEqual(values["KEYRING_USIGN_KEY"], "")

    def test_resolve_immortalwrt_selects_key_only_from_official_keyring(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-index-immortalwrt-resolve-") as temporary:
            root = Path(temporary)
            source = root / "source"
            makefile = source / "package/system/openwrt-keyring/Makefile"
            makefile.parent.mkdir(parents=True)
            makefile.write_text(
                "PKG_SOURCE_VERSION:=1111111111111111111111111111111111111111\n"
                "$(INSTALL_DATA) $(PKG_BUILD_DIR)/usign/467a35c14d7fbc5c $(1)/etc/opkg/keys/\n",
                encoding="utf-8",
            )
            keyring, commit = self._create_keyring_checkout(
                root,
                "https://github.com/immortalwrt/keyring.git",
                {"usign/467a35c14d7fbc5c": "official public usign key\n"},
            )
            github_env = root / "github-env"
            result = subprocess.run(
                ["bash", str(self.helper), "resolve", "immortalwrt", str(source), str(keyring), str(github_env)],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stdout.strip(), str(keyring / "usign/467a35c14d7fbc5c"))
            values = dict(line.split("=", 1) for line in github_env.read_text(encoding="utf-8").splitlines())
            self.assertEqual(values["KEYRING_COMMIT"], commit)
            self.assertNotEqual(values["KEYRING_COMMIT"], "1" * 40)
            self.assertEqual(values["KEYRING_USIGN_KEY"], str(keyring / "usign/467a35c14d7fbc5c"))
            self.assertEqual(values["KEYRING_GPG_DIR"], "")

    def test_resolve_rejects_key_not_present_in_official_immortalwrt_keyring(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-index-untrusted-key-") as temporary:
            root = Path(temporary)
            source = root / "source"
            makefile = source / "package/system/openwrt-keyring/Makefile"
            makefile.parent.mkdir(parents=True)
            makefile.write_text(
                "$(INSTALL_DATA) $(PKG_BUILD_DIR)/usign/aaaaaaaaaaaaaaaa $(1)/etc/opkg/keys/\n",
                encoding="utf-8",
            )
            keyring, _ = self._create_keyring_checkout(
                root,
                "https://github.com/immortalwrt/keyring",
                {"usign/467a35c14d7fbc5c": "official key\n"},
            )
            result = subprocess.run(
                ["bash", str(self.helper), "resolve", "immortalwrt", str(source), str(keyring)],
                text=True, capture_output=True, check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("not present in the official keyring checkout", result.stderr)

    def test_resolve_rejects_unexpected_keyring_origin(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-index-wrong-origin-") as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            keyring, _ = self._create_keyring_checkout(
                root,
                "https://github.com/attacker/keyring",
                {
                    "gpg/CD54E82DADB3684D.asc": "key\n",
                    "gpg/0x1D53D1877742E911.asc": "key\n",
                    "gpg/626471F1.asc": "key\n",
                },
            )
            result = subprocess.run(
                ["bash", str(self.helper), "resolve", "openwrt", str(source), str(keyring)],
                text=True, capture_output=True, check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Unexpected openwrt keyring origin", result.stderr)

    def test_immortalwrt_verify_passes_expected_files_to_usign(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-index-usign-") as temporary:
            root = Path(temporary)
            args_file = root / "args.txt"
            usign = root / "usign"
            usign.write_text(
                "#!/usr/bin/env bash\n"
                'printf "%s\n" "$@" > "$USIGN_ARGS_FILE"\n',
                encoding="utf-8",
            )
            usign.chmod(0o755)
            public_key = root / "release.pub"
            checksum_file = root / "sha256sums"
            signature_file = root / "sha256sums.sig"
            public_key.write_text("public-key\n", encoding="utf-8")
            checksum_file.write_text("signed-checksum-index\n", encoding="utf-8")
            signature_file.write_text("signature\n", encoding="utf-8")

            result = subprocess.run(
                [
                    "bash", str(self.helper), "verify", "immortalwrt",
                    str(usign), str(public_key), "", str(checksum_file),
                    str(signature_file),
                ],
                env=dict(os.environ, USIGN_ARGS_FILE=str(args_file)),
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(
                args_file.read_text(encoding="utf-8").splitlines(),
                [
                    "-V", "-m", str(checksum_file), "-p", str(public_key),
                    "-x", str(signature_file),
                ],
            )

    def test_openwrt_verify_imports_official_gpg_keys_and_verifies_asc(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-index-gpg-") as temporary:
            root = Path(temporary)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            keyring = root / "gpg"
            keyring.mkdir()
            for name in (
                "CD54E82DADB3684D.asc",
                "0x1D53D1877742E911.asc",
                "626471F1.asc",
            ):
                (keyring / name).write_text("test public key\n", encoding="utf-8")
            args_file = root / "gpg-args.txt"
            fake_gpg = bin_dir / "gpg"
            fake_gpg.write_text(
                "#!/usr/bin/env bash\n"
                'printf "%s\n" "$@" >> "$GPG_ARGS_FILE"\n',
                encoding="utf-8",
            )
            fake_gpg.chmod(0o755)
            checksum_file = root / "sha256sums"
            signature_file = root / "sha256sums.asc"
            checksum_file.write_text("signed-checksum-index\n", encoding="utf-8")
            signature_file.write_text("signature\n", encoding="utf-8")

            result = subprocess.run(
                [
                    "bash", str(self.helper), "verify", "openwrt",
                    str(bin_dir / "unused-usign"), "", str(keyring),
                    str(checksum_file), str(signature_file),
                ],
                env=dict(
                    os.environ,
                    PATH=f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
                    GPG_ARGS_FILE=str(args_file),
                ),
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            args = args_file.read_text(encoding="utf-8").splitlines()
            self.assertIn("--import", args)
            self.assertIn(str(keyring / "CD54E82DADB3684D.asc"), args)
            self.assertIn(str(keyring / "0x1D53D1877742E911.asc"), args)
            self.assertIn(str(keyring / "626471F1.asc"), args)
            self.assertIn("--with-fingerprint", args)
            self.assertIn("--verify", args)
            self.assertIn(str(signature_file), args)
            self.assertIn(str(checksum_file), args)

    def test_verify_rejects_missing_signature_before_invoking_verifier(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-index-missing-signature-") as temporary:
            root = Path(temporary)
            usign = root / "usign"
            usign.write_text("#!/usr/bin/env bash\nexit 99\n", encoding="utf-8")
            usign.chmod(0o755)
            public_key = root / "release.pub"
            checksum_file = root / "sha256sums"
            public_key.write_text("public-key\n", encoding="utf-8")
            checksum_file.write_text("signed-checksum-index\n", encoding="utf-8")

            result = subprocess.run(
                [
                    "bash", str(self.helper), "verify", "immortalwrt",
                    str(usign), str(public_key), "", str(checksum_file),
                    str(root / "missing.sig"),
                ],
                text=True, capture_output=True, check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("signature is missing or empty", result.stderr)


    def test_immortalwrt_verifier_propagates_bad_signature_failure(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-index-usign-negative-") as temporary:
            root = Path(temporary)
            usign = root / "usign"
            usign.write_text("#!/usr/bin/env bash\nexit 17\n", encoding="utf-8")
            usign.chmod(0o755)
            public_key = root / "release.pub"
            checksum_file = root / "sha256sums"
            signature_file = root / "sha256sums.sig"
            public_key.write_text("public-key\n", encoding="utf-8")
            checksum_file.write_text("checksum-index\n", encoding="utf-8")
            signature_file.write_text("bad-signature\n", encoding="utf-8")
            result = subprocess.run(
                ["bash", str(self.helper), "verify", "immortalwrt", str(usign),
                 str(public_key), "", str(checksum_file), str(signature_file)],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 17)

    def test_openwrt_verifier_performs_real_detached_signature_check(self) -> None:
        if shutil.which("gpg") is None:
            self.skipTest("GnuPG is unavailable")
        with tempfile.TemporaryDirectory(prefix="release-index-real-gpg-") as temporary:
            root = Path(temporary)
            signing_home = root / "signing-home"
            signing_home.mkdir(mode=0o700)
            checksum_file = root / "sha256sums"
            payload = b"0123456789abcdef"
            checksum_file.write_text(
                hashlib.sha256(payload).hexdigest() + " *toolchain.tar.zst\n",
                encoding="utf-8",
            )
            subprocess.run(
                ["gpg", "--homedir", str(signing_home), "--batch", "--passphrase", "",
                 "--quick-generate-key", "CI Release Verifier <ci-verifier@example.invalid>",
                 "ed25519", "sign", "0"],
                check=True, text=True, capture_output=True,
            )
            public_key = subprocess.run(
                ["gpg", "--homedir", str(signing_home), "--batch", "--armor", "--export"],
                check=True, text=True, capture_output=True,
            ).stdout
            keyring = root / "gpg"
            keyring.mkdir()
            for name in ("CD54E82DADB3684D.asc", "0x1D53D1877742E911.asc", "626471F1.asc"):
                (keyring / name).write_text(public_key, encoding="utf-8")
            signature_file = root / "sha256sums.asc"
            subprocess.run(
                ["gpg", "--homedir", str(signing_home), "--batch", "--yes",
                 "--detach-sign", "--output", str(signature_file), str(checksum_file)],
                check=True, text=True, capture_output=True,
            )

            command = [
                "bash", str(self.helper), "verify", "openwrt",
                str(root / "unused-usign"), "", str(keyring),
                str(checksum_file), str(signature_file),
            ]
            valid = subprocess.run(command, text=True, capture_output=True, check=False)
            self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)

            checksum_file.write_text(
                checksum_file.read_text(encoding="utf-8") + "# tampered\n",
                encoding="utf-8",
            )
            invalid = subprocess.run(command, text=True, capture_output=True, check=False)
            self.assertNotEqual(invalid.returncode, 0)
            self.assertIn("BAD signature", invalid.stderr)

    def test_upstream_sha256sum_check_ignores_unavailable_manifest_entries(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-index-sha256-") as temporary:
            root = Path(temporary)
            archive = root / "toolchain.tar.zst"
            archive.write_bytes(b"verified archive fixture")
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            (root / "sha256sums").write_text(
                f"{digest} *toolchain.tar.zst\n"
                + f"{'0' * 64} *not-downloaded.tar.zst\n",
                encoding="utf-8",
            )
            valid = subprocess.run(
                ["sha256sum", "--check", "--ignore-missing", "sha256sums"],
                cwd=root, text=True, capture_output=True, check=False,
            )
            self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)

            archive.write_bytes(b"tampered archive fixture")
            invalid = subprocess.run(
                ["sha256sum", "--check", "--ignore-missing", "sha256sums"],
                cwd=root, text=True, capture_output=True, check=False,
            )
            self.assertNotEqual(invalid.returncode, 0)

            (root / "sha256sums").write_text(
                f"{digest} *not-the-selected-archive.tar.zst\n",
                encoding="utf-8",
            )
            unmatched = subprocess.run(
                ["sha256sum", "--check", "--ignore-missing", "sha256sums"],
                cwd=root, text=True, capture_output=True, check=False,
            )
            self.assertNotEqual(unmatched.returncode, 0)
            self.assertIn("no file was verified", unmatched.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
