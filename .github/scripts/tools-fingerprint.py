#!/usr/bin/env python3
"""Create a content-based fingerprint for the generic OpenWrt host-tools image."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath

SHARED_WORKFLOWS_REF = "c3168d3fdc9d6c5f77108f94a4edff769af58016"

LOCAL_INPUTS = (
    ".github/workflows/build.yml",
    ".github/workflows/push-containers.yml",
    ".github/workflows/reusable_build.yml",
    ".github/workflows/reusable_tools_fingerprint.yml",
    ".github/scripts/ext-tools-refresh.sh",
    ".github/scripts/tools-fingerprint.py",
)
SHARED_INPUTS = (
    ".github/workflows/reusable_build-tools.yml",
    ".github/dockerfiles/Dockerfile.tools",
)
HOST_TOOLS_CONFIG = (
    "CONFIG_DEVEL=y\n"
    "CONFIG_AUTOREMOVE=y\n"
    "CONFIG_CCACHE=y\n"
    "CONFIG_BUILD_ALL_HOST_TOOLS=y\n"
)
BUILD_ENVIRONMENT = {
    "runner": "ubuntu-latest",
    "platform": "linux/amd64",
}


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


def add_file(manifest_files: list[dict[str, str]], scope: str, root: Path, relative: str) -> None:
    path = root / relative
    if not path.is_file():
        raise RuntimeError(f"Fingerprint input is missing: {scope}/{relative}")
    content = path.read_bytes()
    manifest_files.append(
        {
            "scope": scope,
            "path": relative,
            "sha256": hashlib.sha256(content).hexdigest(),
        }
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--local-repo-dir", required=True, type=Path)
    parser.add_argument("--shared-workflows-dir", required=True, type=Path)
    parser.add_argument("--source-repo", required=True)
    parser.add_argument("--source-ref", required=True)
    parser.add_argument("--shared-ref", required=True)
    args = parser.parse_args()

    source_dir = args.source_dir.resolve()
    local_repo_dir = args.local_repo_dir.resolve()
    shared_dir = args.shared_workflows_dir.resolve()

    if args.shared_ref != SHARED_WORKFLOWS_REF:
        raise RuntimeError(
            f"Shared-workflows ref mismatch: expected {SHARED_WORKFLOWS_REF}, got {args.shared_ref}"
        )

    source_commit = git_output(source_dir, "rev-parse", "HEAD")
    shared_commit = git_output(shared_dir, "rev-parse", "HEAD")
    if shared_commit != SHARED_WORKFLOWS_REF:
        raise RuntimeError(
            f"Shared-workflows checkout mismatch: expected {SHARED_WORKFLOWS_REF}, got {shared_commit}"
        )

    tracked = git_output(source_dir, "ls-files", "-z").split("\0")
    source_files = sorted(path for path in tracked if path and is_host_tool_input(path))

    if not source_files:
        raise RuntimeError("No tracked host-tools source inputs were found.")

    manifest_files: list[dict[str, str]] = []
    for path in source_files:
        add_file(manifest_files, "source", source_dir, path)
    for path in LOCAL_INPUTS:
        add_file(manifest_files, "actions-wrt", local_repo_dir, path)
    for path in SHARED_INPUTS:
        add_file(manifest_files, "shared-workflows", shared_dir, path)

    manifest = {
        "schema": 1,
        "source_repository": args.source_repo,
        "shared_workflows_ref": SHARED_WORKFLOWS_REF,
        "host_tools_config": HOST_TOOLS_CONFIG,
        "build_environment": BUILD_ENVIRONMENT,
        "files": manifest_files,
    }
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    fingerprint = hashlib.sha256(canonical).hexdigest()

    print(f"Source ref: {args.source_ref}")
    print(f"Source commit: {source_commit}")
    print(f"Shared workflows commit: {shared_commit}")
    print(f"Fingerprint input files: {len(manifest_files)}")
    print(f"Tools fingerprint: {fingerprint}")

    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as output:
            output.write(f"tools_fingerprint={fingerprint}\n")
            output.write(f"source_commit={source_commit}\n")
    else:
        print(json.dumps({"tools_fingerprint": fingerprint, "source_commit": source_commit}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"tools-fingerprint: {exc}", file=sys.stderr)
        raise SystemExit(1)
