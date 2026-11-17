#!/usr/bin/env python3
"""Build-free regression tests for fingerprint.py."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock
from urllib.error import HTTPError
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
            source_ref="openwrt-25.12",
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
            source_ref="openwrt-25.12",
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
            source_ref="openwrt-25.12",
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

    def test_source_ref_is_metadata_only(self) -> None:
        baseline = self.fingerprint()
        fingerprint, manifest = FINGERPRINT_MODULE.compute_toolchain_fingerprint(
            source_dir=self.source,
            local_repo_dir=self.local,
            source_repo="immortalwrt/immortalwrt",
            source_ref="unrelated-upstream-ref",
            target="mediatek",
            subtarget="filogic",
            tools_fingerprint="a" * 64,
        )
        self.assertEqual(fingerprint, baseline)
        self.assertNotIn("source_commit", manifest)

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

    def test_verify_workflow_passes_authentication_to_both_ghcr_checks(self) -> None:
        workflow_path = (
            Path(__file__).resolve().parents[2]
            / "workflows"
            / "verify-fingerprints.yml"
        )
        workflow = workflow_path.read_text(encoding="utf-8")

        step_names = (
            "Verify existing Host Tools image labels",
            "Verify existing Toolchain image labels",
        )
        for step_name in step_names:
            marker = f"      - name: {step_name}\n"
            start = workflow.find(marker)
            self.assertNotEqual(start, -1, f"Missing workflow step: {step_name}")
            next_step = workflow.find("\n      - name: ", start + len(marker))
            block = workflow[start:next_step if next_step != -1 else len(workflow)]
            self.assertIn("GITHUB_ACTOR: ${{ github.actor }}", block)
            self.assertIn("GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}", block)

    def test_ghcr_reuse_is_content_addressed_not_source_commit_addressed(self) -> None:
        root = Path(__file__).resolve().parents[2] / "workflows"

        for name in ("build.yml", "verify-fingerprints.yml", "reusable_build.yml"):
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
            baseline,manifest=FINGERPRINT_MODULE.compute_tools_fingerprint(source,local,"test/repo","main")
            self.assertFalse(any(item["path"] == "scripts/ext-tools.sh" for item in manifest["files"]))
            (source/"scripts/ext-tools.sh").write_text("changed refresh implementation",encoding="utf-8")
            (source/"toolchain/gcc/Makefile").write_text("changed toolchain implementation",encoding="utf-8")
            (source/"package/foo/Makefile").write_text("changed package",encoding="utf-8")
            (source/"target/x/Config.in").write_text("changed target",encoding="utf-8")
            updated,_=FINGERPRINT_MODULE.compute_tools_fingerprint(source,local,"test/repo","main")
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
            baseline,_=FINGERPRINT_MODULE.compute_tools_fingerprint(source,local,"test/repo","main")
            (source/"toolchain/gcc/Config.in").write_text("changed Kconfig definition",encoding="utf-8")
            updated,_=FINGERPRINT_MODULE.compute_tools_fingerprint(source,local,"test/repo","main")
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
            self.assertFalse(
                FINGERPRINT_MODULE.ghcr_check(
                    "owner", "image", "tag", {"fingerprint": "expected"}
                )
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

    def test_oci_index_without_linux_amd64_returns_false(self) -> None:
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
            self.assertFalse(
                FINGERPRINT_MODULE.ghcr_check(
                    "owner", "image", "tag", {"fingerprint": "expected"}
                )
            )

    def test_missing_registry_token_fails_closed(self) -> None:
        with mock.patch.object(
            FINGERPRINT_MODULE, "urlopen", return_value=self.response({})
        ):
            with self.assertRaisesRegex(RuntimeError, "empty pull token"):
                FINGERPRINT_MODULE.ghcr_check(
                    "owner", "image", "tag", {"fingerprint": "expected"}
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
