#!/usr/bin/env python3
"""Build-free regression tests for fingerprint.py."""

from __future__ import annotations

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "toolchain-fingerprint.py"
SPEC = importlib.util.spec_from_file_location("toolchain_fingerprint", SCRIPT)
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
        self.write(
            self.local / ".github/scripts/ext-tools-refresh.sh",
            "#!/bin/bash\nexec true\n",
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
        fingerprint, manifest = FINGERPRINT_MODULE.compute_fingerprint(
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
        fingerprint, manifest = FINGERPRINT_MODULE.compute_fingerprint(
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



class HostToolsFingerprintTests(unittest.TestCase):
    def test_unrelated_package_file_does_not_change_host_tools_fingerprint(self):
        with tempfile.TemporaryDirectory(prefix="host-tools-fingerprint-") as d:
            root=Path(d); source=root/"openwrt"; local=root/"actions-wrt"; source.mkdir(); local.mkdir()
            for rel in ["Makefile","rules.mk","Config.in","include/a","tools/a","toolchain/a","scripts/a","config/a","package/foo/Makefile","target/x/Config.in"]:
                p=source/rel; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(rel,encoding="utf-8")
            (local/".github/workflows").mkdir(parents=True); (local/".github/dockerfiles").mkdir(parents=True)
            (local/".github/workflows/reusable_build_tools.yml").write_text("container: ghcr.io/openwrt/buildbot/buildworker-v3.11.8:v28\n",encoding="utf-8")
            (local/".github/dockerfiles/Dockerfile.tools").write_text("FROM ghcr.io/openwrt/buildbot/buildworker-v3.11.8:v28\n",encoding="utf-8")
            subprocess.run(["git","init","-q",str(source)],check=True); subprocess.run(["git","-C",str(source),"config","user.email","test@example.invalid"],check=True); subprocess.run(["git","-C",str(source),"config","user.name","Fingerprint Test"],check=True); subprocess.run(["git","-C",str(source),"add","-A"],check=True); subprocess.run(["git","-C",str(source),"commit","-qm","fixture"],check=True)
            a,_=FINGERPRINT_MODULE.compute_tools_fingerprint(source,local,"test/repo","main")
            (source/"package/foo/Makefile").write_text("changed",encoding="utf-8")
            b,_=FINGERPRINT_MODULE.compute_tools_fingerprint(source,local,"test/repo","main")
            self.assertEqual(a,b)


if __name__ == "__main__":
    unittest.main(verbosity=2)
