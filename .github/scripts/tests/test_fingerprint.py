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


class UpstreamContainerFingerprintTests(unittest.TestCase):
    WORKER = "ghcr.io/openwrt/buildbot/buildworker-v3.11.8:v23"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fingerprint-boundary-")
        self.root = Path(self.temp.name)
        self.source = self.root / "upstream"
        self.local = self.root / "local"
        self.source.mkdir()
        self.local.mkdir()
        source_files = {
            "include/version.mk": "VERSION:=1\n",
            "include/cmake.mk": "CMAKE:=1\n",
            ".github/workflows/push-containers.yml": "paths: [tools/**, toolchain/**]\n",
            "tools/foo/Makefile": "all:\n",
            "toolchain/bar/Makefile": "all:\n",
            "target/linux/mediatek/Makefile": "KERNEL_PATCHVER:=6.12\n",
            "target/toolchain/Makefile": "all:\n",
            "scripts/ext-tools.sh": "# refresh\n",
            ".config": "CONFIG_ARCH=mips\n",
            "package/foo/Makefile": "all:\n",
        }
        for relative, content in source_files.items():
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        for path in (
            self.local / ".github/workflows/reusable_build_tools.yml",
            self.local / ".github/workflows/reusable_build.yml",
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"jobs:\n  build:\n    container: {self.WORKER}\n", encoding="utf-8")
        docker_dir = self.local / ".github/dockerfiles"
        docker_dir.mkdir(parents=True)
        for name in ("Dockerfile.tools", "Dockerfile.toolchain"):
            (docker_dir / name).write_text(f"FROM {self.WORKER}\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(self.source)], check=True)
        subprocess.run(["git", "-C", str(self.source), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(self.source), "config", "user.name", "Fingerprint Test"], check=True)
        subprocess.run(["git", "-C", str(self.source), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(self.source), "commit", "-qm", "fixture"], check=True)

    def tearDown(self):
        self.temp.cleanup()

    def tools(self):
        return FINGERPRINT_MODULE.compute_tools_fingerprint(
            self.source, self.local, "openwrt/openwrt"
        )

    def toolchain(self, tools="a" * 64):
        return FINGERPRINT_MODULE.compute_toolchain_fingerprint(
            self.source, self.local, "openwrt/openwrt", "mediatek", "filogic", tools
        )

    def change_source_file(self, relative, content):
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        subprocess.run(["git", "-C", str(self.source), "add", "-A"], check=True)

    def test_official_rebuild_paths_are_included(self):
        expected = {
            "include/version.mk", "include/cmake.mk",
            ".github/workflows/push-containers.yml",
            "tools/foo/Makefile", "toolchain/bar/Makefile",
        }
        self.assertEqual(
            {p for p in FINGERPRINT_MODULE.tracked_paths(self.source)
             if FINGERPRINT_MODULE.is_upstream_container_input(p)},
            expected,
        )

    def test_unrelated_paths_are_excluded(self):
        for path in (
            "target/linux/mediatek/Makefile", "target/toolchain/Makefile",
            "scripts/ext-tools.sh", ".config", "package/foo/Makefile",
            "include/target.mk",
        ):
            self.assertFalse(FINGERPRINT_MODULE.is_upstream_container_input(path), path)

    def test_host_tools_fingerprint_changes_for_official_input(self):
        before, _ = self.tools()
        self.change_source_file("tools/foo/Makefile", "all: changed\n")
        after, _ = self.tools()
        self.assertNotEqual(before, after)

    def test_toolchain_fingerprint_changes_for_official_input(self):
        before, _ = self.toolchain()
        self.change_source_file("toolchain/bar/Makefile", "all: changed\n")
        after, _ = self.toolchain()
        self.assertNotEqual(before, after)

    def test_unrelated_source_changes_do_not_invalidate_either_fingerprint(self):
        tools_before, _ = self.tools()
        toolchain_before, _ = self.toolchain()
        self.change_source_file("target/linux/mediatek/Makefile", "KERNEL_PATCHVER:=6.13\n")
        self.change_source_file("scripts/ext-tools.sh", "# changed refresh\n")
        self.change_source_file("package/foo/Makefile", "all: changed\n")
        self.assertEqual(tools_before, self.tools()[0])
        self.assertEqual(toolchain_before, self.toolchain()[0])

    def test_license_and_unrelated_package_metadata_do_not_invalidate(self):
        tools_before, _ = self.tools()
        toolchain_before, _ = self.toolchain()
        self.change_source_file("LICENSES/GPL-2.0", "updated license text\\n")
        self.change_source_file("package/foo/Makefile", "all: updated\\n")
        self.assertEqual(tools_before, self.tools()[0])
        self.assertEqual(toolchain_before, self.toolchain()[0])

    def test_target_kernel_dts_patch_does_not_invalidate(self):
        tools_before, _ = self.tools()
        toolchain_before, _ = self.toolchain()
        self.change_source_file(
            "target/linux/mediatek/patches-6.12/001-dts.patch",
            "--- a/arch/arm64/boot/dts/example.dts\\n"
            "+++ b/arch/arm64/boot/dts/example.dts\\n"
            "@@ -1 +1 @@\\n-old\\n+new\\n",
        )
        self.assertEqual(tools_before, self.tools()[0])
        self.assertEqual(toolchain_before, self.toolchain()[0])

    def test_target_configuration_and_device_profile_changes_do_not_invalidate(self):
        tools_before, _ = self.tools()
        toolchain_before, _ = self.toolchain()
        self.change_source_file(
            "target/linux/mediatek/Makefile",
            "KERNEL_PATCHVER:=6.13\\nARCH:=changed\\n",
        )
        self.change_source_file(
            "target/linux/mediatek/image/Makefile",
            "DEFAULT_PROFILE:=different-device\\n",
        )
        self.change_source_file(
            ".config",
            "CONFIG_ARCH=x86_64\\nCONFIG_TARGET_mediatek_filogic_DEVICE_other=y\\n",
        )
        self.assertEqual(tools_before, self.tools()[0])
        self.assertEqual(toolchain_before, self.toolchain()[0])

    def test_config_is_not_required_or_read(self):
        (self.source / ".config").unlink()
        subprocess.run(["git", "-C", str(self.source), "rm", "-q", ".config"], check=True)
        self.tools()
        self.toolchain()

    def test_target_identity_and_host_tools_dependency_are_preserved(self):
        base, manifest = self.toolchain()
        other_target, _ = FINGERPRINT_MODULE.compute_toolchain_fingerprint(
            self.source, self.local, "openwrt/openwrt", "x86", "64", "a" * 64
        )
        other_tools, _ = self.toolchain("b" * 64)
        self.assertNotEqual(base, other_target)
        self.assertNotEqual(base, other_tools)
        self.assertEqual(manifest["dependencies"]["host_tools_fingerprint"], "a" * 64)

    def test_refresh_script_is_recipe_not_upstream_source_input(self):
        _, manifest = self.toolchain()
        paths = {item["path"] for item in manifest["files"]}
        self.assertNotIn("scripts/ext-tools.sh", paths)
        self.assertIn("./scripts/ext-tools.sh --refresh", manifest["toolchain_build_recipe"])

    def test_schema_versions_invalidate_old_manifest_namespaces(self):
        _, tools_manifest = self.tools()
        _, toolchain_manifest = self.toolchain()
        self.assertEqual(tools_manifest["schema"], 4)
        self.assertEqual(toolchain_manifest["schema"], 2)

    def test_worker_mismatch_fails_closed(self):
        dockerfile = self.local / ".github/dockerfiles/Dockerfile.toolchain"
        dockerfile.write_text("FROM ubuntu:latest\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "Runtime Dockerfile"):
            self.toolchain()

    def test_toolchain_output_handoff_is_validated_and_consumed(self):
        with tempfile.TemporaryDirectory(prefix="fingerprint-handoff-") as temporary:
            root = Path(temporary)
            fingerprint = "b" * 64
            source_commit = "c" * 40
            input_file = root / "input"
            output_file = root / "output"
            input_file.write_text(
                f"toolchain_fingerprint={fingerprint}\n"
                f"toolchain_cache_key={fingerprint[:32]}\n"
                f"source_commit={source_commit}\n",
                encoding="utf-8",
            )
            FINGERPRINT_MODULE.publish_toolchain_outputs(
                input_file, source_commit, output_file
            )
            self.assertFalse(input_file.exists())
            self.assertEqual(
                set(output_file.read_text(encoding="utf-8").splitlines()),
                {
                    f"toolchain_fingerprint={fingerprint}",
                    f"toolchain_cache_key={fingerprint[:32]}",
                    f"source_commit={source_commit}",
                },
            )


    def test_toolchain_outputs_can_use_explicit_handoff_file(self):
        with tempfile.TemporaryDirectory(prefix="fingerprint-output-") as directory:
            output = Path(directory) / "handoff"
            runner_output = Path(directory) / "runner-output"
            with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(runner_output)}):
                FINGERPRINT_MODULE.write_outputs(
                    {
                        "toolchain_fingerprint": "a" * 64,
                        "toolchain_cache_key": "a" * 32,
                        "source_commit": "b" * 40,
                    },
                    output,
                )
            self.assertEqual(
                output.read_text(encoding="utf-8").splitlines(),
                [
                    "toolchain_fingerprint=" + "a" * 64,
                    "toolchain_cache_key=" + "a" * 32,
                    "source_commit=" + "b" * 40,
                ],
            )
            self.assertFalse(runner_output.exists())

    def test_toolchain_output_publisher_rejects_invalid_handoffs(self):
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
