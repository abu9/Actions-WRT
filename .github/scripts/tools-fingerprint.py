#!/usr/bin/env python3
"""Fingerprint only inputs that determine the generic OpenWrt Host Tools artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath

# This recipe identifier must be bumped only when the actual Host Tools build
# commands, configuration, or required host dependencies change. Workflow
# orchestration (cache restore/save, triggers, firmware jobs) is deliberately
# excluded from the artifact fingerprint.
HOST_TOOLS_BUILD_RECIPE = (
    "host-tools-v1; config:DEVEL,AUTOREMOVE,CCACHE,"
    "BUILD_ALL_HOST_TOOLS; commands:make-defconfig,make-tools-install"
)
HOST_TOOLS_CONFIG = (
    "CONFIG_DEVEL=y\n"
    "CONFIG_AUTOREMOVE=y\n"
    "CONFIG_CCACHE=y\n"
    "CONFIG_BUILD_ALL_HOST_TOOLS=y\n"
)
BUILD_ENVIRONMENT = {
    "runner": "ubuntu-24.04",
    "platform": "linux/amd64",
    "buildworker_image": "ghcr.io/openwrt/buildbot/buildworker-v3.11.8:v28",
}
RUNTIME_DOCKERFILE = ".github/dockerfiles/Dockerfile.tools"


def git_output(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip()


def is_host_tool_input(path: str) -> bool:
    if path in {"Makefile", "rules.mk", "Config.in"}:
        return True
    if path.startswith(("include/", "tools/", "toolchain/", "scripts/", "config/")):
        return True
    parts = PurePosixPath(path).parts
    if parts and parts[0] in {"package", "target"}:
        return PurePosixPath(path).name in {"Makefile", "Config.in"}
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--local-repo-dir", required=True, type=Path)
    parser.add_argument("--source-repo", required=True)
    parser.add_argument("--source-ref", required=True)
    args = parser.parse_args()

    source_dir = args.source_dir.resolve()
    local_repo_dir = args.local_repo_dir.resolve()
    source_commit = git_output(source_dir, "rev-parse", "HEAD")
    source_files = sorted(
        path for path in git_output(source_dir, "ls-files", "-z").split("\0")
        if path and is_host_tool_input(path)
    )
    if not source_files:
        raise RuntimeError("No tracked host-tools source inputs were found.")

    worker = BUILD_ENVIRONMENT["buildworker_image"]
    builder_workflow = (
        local_repo_dir / ".github/workflows/reusable_build_tools.yml"
    ).read_text(encoding="utf-8")
    dockerfile = local_repo_dir / RUNTIME_DOCKERFILE
    dockerfile_text = dockerfile.read_text(encoding="utf-8")
    if f"container: {worker}" not in builder_workflow:
        raise RuntimeError("Host Tools builder image does not match the fingerprinted build environment.")
    if not dockerfile_text.startswith(f"FROM {worker}\n"):
        raise RuntimeError("Host Tools runtime image does not match the fingerprinted build environment.")

    files = []
    for relative in source_files:
        path = source_dir / relative
        files.append({
            "scope": "source",
            "path": relative,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
    files.append({
        "scope": "runtime-image",
        "path": RUNTIME_DOCKERFILE,
        "sha256": hashlib.sha256(dockerfile.read_bytes()).hexdigest(),
    })

    manifest = {
        "schema": 3,
        "source_repository": args.source_repo,
        "host_tools_config": HOST_TOOLS_CONFIG,
        "host_tools_build_recipe": HOST_TOOLS_BUILD_RECIPE,
        "build_environment": BUILD_ENVIRONMENT,
        "files": files,
    }
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    fingerprint = hashlib.sha256(canonical).hexdigest()
    print(f"Source ref: {args.source_ref}")
    print(f"Source commit: {source_commit}")
    print(f"Fingerprint input files: {len(files)}")
    print(f"Host Tools artifact fingerprint: {fingerprint}")

    output_path = os.environ.get("GITHUB_OUTPUT")
    outputs = {
        "tools_fingerprint": fingerprint,
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
        print(f"tools-fingerprint: {exc}", file=sys.stderr)
        raise SystemExit(1)
