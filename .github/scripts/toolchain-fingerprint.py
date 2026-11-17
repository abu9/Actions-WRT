#!/usr/bin/env python3
"""Content fingerprint for an OpenWrt target toolchain and exported SDK archive."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

TOOLCHAIN_BUILD_RECIPE = (
    "toolchain-v1;"
    "config:CCACHE,DEVEL,AUTOREMOVE,TARGET,SUBTARGET;"
    "commands:make-defconfig,make-tools-install,make-toolchain-install,"
    "make-target-toolchain-compile;"
    "host-tools:prebuilt-image; ext-tools-refresh"
)
BUILD_ENVIRONMENT = {
    "runner": "ubuntu-24.04",
    "platform": "linux/amd64",
    "host_tools_image": "identified-by-host-tools-fingerprint",
}
TOOLCHAIN_DOCKERFILE = ".github/dockerfiles/Dockerfile.toolchain"
EXT_TOOLS_REFRESH = ".github/scripts/ext-tools-refresh.sh"

COMMON_BUILD_FILES = {
    "Makefile",
    "rules.mk",
    "Config.in",
    "include/toplevel.mk",
    "include/verbose.mk",
    "include/debug.mk",
    "include/target.mk",
    "include/toolchain-build.mk",
    "include/host-build.mk",
    "include/hardening.mk",
    "include/download.mk",
    "include/unpack.mk",
    "include/quilt.mk",
    "include/autotools.mk",
    "include/depends.mk",
    "include/subdir.mk",
    "include/prereq.mk",
    "include/kernel.mk",
    "include/kernel-build.mk",
    "include/kernel-defaults.mk",
    "include/kernel-version.mk",
    "include/version.mk",
    "include/scan.mk",
    "target/linux/Makefile",
    "scripts/kconfig.pl",
    "scripts/metadata.pm",
    "scripts/target-metadata.pl",
    "scripts/patch-kernel.sh",
    "LICENSES",
    "COPYING",
}

TARGET_RULE_KEYS = {
    "ARCH",
    "BOARD",
    "SUBTARGET",
    "CPU_TYPE",
    "CPU_SUBTYPE",
    "KERNEL_PATCHVER",
    "KERNEL_TESTING_PATCHVER",
    "TARGET_OPTIMIZATION",
    "TARGET_CFLAGS",
    "TARGET_CXXFLAGS",
    "TARGET_LDFLAGS",
    "TARGET_SUFFIX",
    "ARCH_SUFFIX",
    "GNU_TARGET_NAME",
    "REAL_GNU_TARGET_NAME",
    "GCC_ARCH",
    "ABI",
    "FLOAT_ABI",
}

ASSIGNMENT_RE = re.compile(
    r"^\s*([A-Z][A-Z0-9_]*)\s*(:=|\?=|\+=|=)\s*(.*?)\s*$"
)
KERNEL_PATCHVER_RE = re.compile(
    r"^\s*KERNEL_PATCHVER\s*(?::=|\?=|=)\s*([0-9]+(?:\.[0-9]+)*)\s*(?:#.*)?$",
    re.MULTILINE,
)
CONFIG_ASSIGN_RE = re.compile(r"^(CONFIG_[A-Za-z0-9_]+)=(.*)$")
CONFIG_UNSET_RE = re.compile(r"^# (CONFIG_[A-Za-z0-9_]+) is not set$")
CONFIG_REFERENCE_RE = re.compile(r"\b(CONFIG_[A-Za-z0-9_]+)\b")
DEVICE_PROFILE_RE = re.compile(r"^CONFIG_TARGET_.*_DEVICE_")
PATCH_DIR_NAMES = ("backport", "hack", "pending", "patches")
FILE_DIR_NAMES = ("files",)
PATCH_PATH_RE = re.compile(
    r"^(?:include/(?:uapi/|linux/|asm-generic/)|"
    r"arch/[^/]+/include/(?:uapi/|asm/|generated/uapi/)|"
    r"scripts/(?:headers_install\.sh|Makefile\.headersinst|unifdef\.c)|"
    r"(?:Makefile|Kbuild)$)"
)


def run_git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout


def tracked_paths(repo: Path) -> list[str]:
    raw = run_git(repo, "ls-files", "-z")
    return sorted(item.decode("utf-8") for item in raw.split(b"\0") if item)


def normalize_config_value(value: str) -> str:
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    return value


def parse_config(config_path: Path) -> dict[str, str]:
    if not config_path.is_file():
        raise RuntimeError(f"Resolved OpenWrt .config not found: {config_path}")
    values: dict[str, str] = {}
    for line in config_path.read_text(encoding="utf-8").splitlines():
        match = CONFIG_ASSIGN_RE.match(line)
        if match:
            values[match.group(1)] = normalize_config_value(match.group(2))
            continue
        match = CONFIG_UNSET_RE.match(line)
        if match:
            values[match.group(1)] = "n"
    return values


def parse_target_settings(path: Path) -> list[dict[str, str]]:
    settings: list[dict[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = ASSIGNMENT_RE.match(line)
        if match and match.group(1) in TARGET_RULE_KEYS:
            settings.append({
                "name": match.group(1),
                "operator": match.group(2),
                "value": match.group(3).strip(),
            })
    return settings


def get_kernel_patchver(target_files: list[Path]) -> str:
    version = ""
    for path in target_files:
        content = path.read_text(encoding="utf-8")
        matches = KERNEL_PATCHVER_RE.findall(content)
        if matches:
            version = matches[-1]
    if not version:
        raise RuntimeError("Unable to resolve KERNEL_PATCHVER from target Makefile/target.mk.")
    return version


def kernel_arch_from_config(arch: str) -> str:
    mapping = {
        "aarch64": "arm64",
        "aarch64_be": "arm64",
        "arm": "arm",
        "armeb": "arm",
        "mipsel": "mips",
        "mips64": "mips",
        "mips64el": "mips",
        "powerpc64": "powerpc",
        "riscv64": "riscv",
        "i386": "x86",
        "x86_64": "x86",
        "loongarch64": "loongarch",
        "arceb": "arc",
    }
    return mapping.get(arch, arch)


def patch_changes_kernel_headers(patch_path: Path, kernel_arch: str) -> bool:
    if patch_path.name.endswith(".gz"):
        with gzip.open(patch_path, "rt", encoding="utf-8", errors="replace") as patch_file:
            patch_lines = patch_file.read().splitlines()
    else:
        patch_lines = patch_path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in patch_lines:
        if not (line.startswith("+++ ") or line.startswith("--- ")):
            continue
        candidate = line[4:].split("\t", 1)[0].strip()
        if candidate in {"/dev/null", "dev/null"}:
            continue
        if candidate.startswith(("a/", "b/")):
            candidate = candidate[2:]
        if not PATCH_PATH_RE.match(candidate):
            continue
        if candidate.startswith("arch/"):
            parts = PurePosixPath(candidate).parts
            if len(parts) < 3 or parts[1] != kernel_arch:
                continue
        return True
    return False


def selected_kernel_inputs(
    paths: list[str],
    target: str,
    subtarget: str,
    kernel_patchver: str,
    kernel_arch: str,
    source_dir: Path,
) -> set[str]:
    selected: set[str] = set()
    roots = (
        "target/linux/generic",
        f"target/linux/{target}",
        f"target/linux/{target}/{subtarget}",
    )
    patch_dir_names = {f"{name}-{kernel_patchver}" for name in PATCH_DIR_NAMES}
    file_dir_names = {f"{name}-{kernel_patchver}" for name in FILE_DIR_NAMES}
    file_dir_names.add("files")

    for relative in paths:
        parts = PurePosixPath(relative).parts
        if not parts:
            continue
        for root in roots:
            root_parts = PurePosixPath(root).parts
            if parts[:len(root_parts)] != root_parts or len(parts) <= len(root_parts):
                continue
            tail = parts[len(root_parts):]
            if tail[0] in patch_dir_names and relative.endswith((".patch", ".patch.gz")):
                if patch_changes_kernel_headers(source_dir / relative, kernel_arch):
                    selected.add(relative)
                break
            if tail[0] in file_dir_names:
                file_path = "/".join(tail[1:])
                if file_path and PATCH_PATH_RE.match(file_path):
                    if file_path.startswith("arch/"):
                        arch_parts = PurePosixPath(file_path).parts
                        if len(arch_parts) < 3 or arch_parts[1] != kernel_arch:
                            continue
                    selected.add(relative)
                break
    return selected


def compute_manifest(
    source_dir: Path,
    local_repo_dir: Path,
    source_repo: str,
    source_ref: str,
    target: str,
    subtarget: str,
    tools_fingerprint: str,
) -> dict:
    source_dir = source_dir.resolve()
    local_repo_dir = local_repo_dir.resolve()

    if not re.fullmatch(r"[A-Za-z0-9_-]+", target) or not re.fullmatch(r"[A-Za-z0-9_-]+", subtarget):
        raise RuntimeError("Target and subtarget must be simple OpenWrt identifiers.")
    if not re.fullmatch(r"[0-9a-f]{64}", tools_fingerprint):
        raise RuntimeError("Host Tools fingerprint must be a full 64-character lowercase SHA-256 digest.")

    paths = tracked_paths(source_dir)
    tracked = set(paths)
    board_makefile = f"target/linux/{target}/Makefile"
    subtarget_makefile = f"target/linux/{target}/{subtarget}/target.mk"
    for required in ("Makefile", "rules.mk", "Config.in", board_makefile, subtarget_makefile):
        if required not in tracked:
            raise RuntimeError(f"Required Toolchain input is not tracked: {required}")

    target_files = [source_dir / board_makefile, source_dir / subtarget_makefile]
    kernel_patchver = get_kernel_patchver(target_files)

    config_values = parse_config(source_dir / ".config")
    arch = config_values.get("CONFIG_ARCH", "")
    if not arch:
        raise RuntimeError("CONFIG_ARCH is missing from the resolved OpenWrt .config.")
    kernel_arch = kernel_arch_from_config(arch)

    source_inputs = {
        path for path in paths
        if path.startswith(("toolchain/", "target/toolchain/"))
    }
    source_inputs.update(path for path in COMMON_BUILD_FILES if path in tracked)
    source_inputs.update(
        path for path in paths if path.startswith("scripts/config/")
    )
    site_file = f"include/site/{arch}"
    if site_file in tracked:
        source_inputs.add(site_file)

    kernel_version_file = f"target/linux/generic/kernel-{kernel_patchver}"
    if kernel_version_file not in tracked:
        raise RuntimeError(f"Kernel version metadata is not tracked: {kernel_version_file}")
    source_inputs.add(kernel_version_file)
    source_inputs.update(selected_kernel_inputs(
        paths, target, subtarget, kernel_patchver, kernel_arch, source_dir
    ))

    local_inputs = [TOOLCHAIN_DOCKERFILE, EXT_TOOLS_REFRESH]
    for relative in local_inputs:
        if not (local_repo_dir / relative).is_file():
            raise RuntimeError(f"Required local Toolchain build input is missing: {relative}")

    config_reference_paths = set(source_inputs)
    config_reference_paths.update((board_makefile, subtarget_makefile))
    referenced_config: set[str] = set()
    for relative in config_reference_paths:
        path = source_dir / relative
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            referenced_config.update(CONFIG_REFERENCE_RE.findall(text))

    # Device profile selections affect image composition, not the cross
    # compiler, libc, binutils, or exported kernel headers.
    referenced_config = {
        key for key in referenced_config if not DEVICE_PROFILE_RE.match(key)
    }
    referenced_config.update({
        f"CONFIG_TARGET_{target}",
        f"CONFIG_TARGET_{target}_{subtarget}",
    })
    resolved_config = {
        key: config_values.get(key, "<unset>")
        for key in sorted(referenced_config)
    }

    target_settings = []
    for relative, path in zip(
        (board_makefile, subtarget_makefile), target_files, strict=True
    ):
        target_settings.append({
            "path": relative,
            "assignments": parse_target_settings(path),
        })

    file_records = []
    for relative in sorted(source_inputs):
        path = source_dir / relative
        if not path.is_file():
            raise RuntimeError(f"Selected Toolchain input is not a regular file: {relative}")
        file_records.append({
            "scope": "source",
            "path": relative,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
    for relative in sorted(local_inputs):
        path = local_repo_dir / relative
        file_records.append({
            "scope": "workflow",
            "path": relative,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })

    return {
        "schema": 1,
        "source_repository": source_repo,
        "target": target,
        "subtarget": subtarget,
        "target_build_settings": target_settings,
        "kernel_patchver": kernel_patchver,
        "kernel_arch": kernel_arch,
        "dependencies": {
            "host_tools_fingerprint": tools_fingerprint,
        },
        "toolchain_config": resolved_config,
        "toolchain_build_recipe": TOOLCHAIN_BUILD_RECIPE,
        "build_environment": BUILD_ENVIRONMENT,
        "files": file_records,
    }


def compute_fingerprint(*args, **kwargs) -> tuple[str, dict]:
    manifest = compute_manifest(*args, **kwargs)
    canonical = json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest(), manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--local-repo-dir", required=True, type=Path)
    parser.add_argument("--source-repo", required=True)
    parser.add_argument("--source-ref", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--subtarget", required=True)
    parser.add_argument("--tools-fingerprint", required=True)
    args = parser.parse_args()

    source_dir = args.source_dir.resolve()
    source_commit = run_git(source_dir, "rev-parse", "HEAD").decode().strip()
    fingerprint, manifest = compute_fingerprint(
        source_dir=source_dir,
        local_repo_dir=args.local_repo_dir,
        source_repo=args.source_repo,
        source_ref=args.source_ref,
        target=args.target,
        subtarget=args.subtarget,
        tools_fingerprint=args.tools_fingerprint,
    )
    cache_key = fingerprint[:32]

    print(f"Source ref: {args.source_ref}")
    print(f"Source commit (informational only): {source_commit}")
    print(f"Toolchain fingerprint input files: {len(manifest['files'])}")
    print(f"Resolved Toolchain config symbols: {len(manifest['toolchain_config'])}")
    print(f"Toolchain content fingerprint: {fingerprint}")
    print(f"Toolchain cache key: {cache_key}")

    output_path = os.environ.get("GITHUB_OUTPUT")
    outputs = {
        "toolchain_fingerprint": fingerprint,
        "toolchain_cache_key": cache_key,
        "source_commit": source_commit,
    }
    if output_path:
        with open(output_path, "a", encoding="utf-8") as output:
            for key, value in outputs.items():
                output.write(f"{key}={value}\n")
    else:
        print(json.dumps(outputs))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"toolchain-fingerprint: {exc}", file=sys.stderr)
        raise SystemExit(1)
