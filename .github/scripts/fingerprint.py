#!/usr/bin/env python3
"""Content fingerprints and GHCR image verification."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def run_git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return result.stdout


def tracked_paths(repo: Path) -> list[str]:
    return sorted(
        item.decode("utf-8")
        for item in run_git(repo, "ls-files", "-z").split(b"\0")
        if item
    )


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha256(value: dict, *, ensure_ascii: bool = False) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=ensure_ascii
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def write_outputs(values: dict[str, str], output_file: Path | None = None) -> None:
    """Write GitHub Actions outputs, optionally to an explicit handoff file."""
    path = output_file or (
        Path(os.environ["GITHUB_OUTPUT"]) if os.environ.get("GITHUB_OUTPUT") else None
    )
    if path is not None:
        with path.open("a", encoding="utf-8") as output:
            for key, value in values.items():
                output.write(f"{key}={value}\n")


def publish_toolchain_outputs(
    input_file: Path, expected_source_commit: str, github_output: Path
) -> None:
    """Validate buildbot's fingerprint handoff before publishing Runner outputs."""
    required = {"toolchain_fingerprint", "toolchain_cache_key", "source_commit"}
    entries: dict[str, str] = {}
    for line in input_file.read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            raise ValueError("Invalid fingerprint output record")
        key, value = line.split("=", 1)
        if key not in required or key in entries or not value:
            raise ValueError(f"Invalid or duplicate fingerprint output: {key}")
        entries[key] = value

    if set(entries) != required:
        raise ValueError("Fingerprint output is incomplete")
    fingerprint = entries["toolchain_fingerprint"]
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise ValueError("Invalid Toolchain fingerprint format")
    if entries["toolchain_cache_key"] != fingerprint[:32]:
        raise ValueError("Toolchain cache key does not match fingerprint")
    if not re.fullmatch(r"[0-9a-f]{40}", entries["source_commit"]):
        raise ValueError("Invalid source commit format")
    expected_source_commit = expected_source_commit.lower()
    if not re.fullmatch(r"[0-9a-f]{40}", expected_source_commit):
        raise ValueError("Expected source commit input is invalid")
    if entries["source_commit"] != expected_source_commit:
        raise ValueError("Fingerprint source commit does not match requested commit")

    with github_output.open("a", encoding="utf-8") as output:
        for key in sorted(required):
            output.write(f"{key}={entries[key]}\n")
    input_file.unlink()


def ghcr_check(owner: str,image: str,tag: str,labels: dict[str,str]) -> bool:
    if not re.fullmatch(r"[a-z0-9_.-]+", owner.lower()):
        raise ValueError("Invalid GHCR owner.")
    if not re.fullmatch(r"[a-z0-9_.-]+", image):
        raise ValueError("Invalid GHCR image name.")
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", tag):
        raise ValueError("Invalid GHCR image tag.")
    if not labels or any(not key or not value for key,value in labels.items()):
        raise ValueError("At least one non-empty expected image label is required.")
    owner=owner.lower(); actor=os.environ.get("GITHUB_ACTOR"); token=os.environ.get("GITHUB_TOKEN")
    if not actor or not token: raise RuntimeError("GITHUB_ACTOR and GITHUB_TOKEN are required for GHCR verification.")
    basic=base64.b64encode(f"{actor}:{token}".encode()).decode()
    try:
        with urlopen(Request(f"https://ghcr.io/token?scope=repository:{owner}/{image}:pull",headers={"Authorization":f"Basic {basic}"}),timeout=30) as r: payload=json.load(r)
    except HTTPError as e:
        # A cache miss is established only by a 404 for the requested tag
        # manifest, after the registry token has been acquired successfully.
        raise RuntimeError(f"GHCR token request failed: HTTP {e.code}") from e
    if not isinstance(payload, dict):
        raise RuntimeError("GHCR token response is not a JSON object.")
    bearer=payload.get("token") or payload.get("access_token")
    if not isinstance(bearer, str) or not bearer:
        raise RuntimeError("GHCR returned an empty pull token or invalid token type.")
    cache_miss = object()
    def get(path,accept, *, missing_ok=False):
        try:
            with urlopen(Request(f"https://ghcr.io/v2/{owner}/{image}/{path}",headers={"Authorization":f"Bearer {bearer}","Accept":accept}),timeout=30) as r:return json.load(r)
        except HTTPError as e:
            if e.code==404 and missing_ok:return cache_miss
            raise RuntimeError(f"GHCR request failed: HTTP {e.code}") from e
    manifest=get(f"manifests/{tag}","application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json", missing_ok=True)
    if manifest is cache_miss:return False
    if not isinstance(manifest, dict):
        raise RuntimeError("GHCR returned an invalid tag manifest.")
    if "manifests" in manifest:
        platform_manifests = manifest["manifests"]
        if not isinstance(platform_manifests, list):
            raise RuntimeError("GHCR tag manifest has an invalid platform manifest list.")
        digest=next((
            item.get("digest")
            for item in platform_manifests
            if isinstance(item, dict)
            and isinstance(item.get("platform"), dict)
            and item["platform"].get("os") == "linux"
            and item["platform"].get("architecture") == "amd64"
        ), None)
        if not digest:
            raise RuntimeError("GHCR tag manifest exists but has no linux/amd64 image manifest.")
        manifest=get(f"manifests/{digest}","application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json")
    if not isinstance(manifest,dict):
        raise RuntimeError("GHCR returned an invalid image manifest.")
    config_descriptor = manifest.get("config")
    if not isinstance(config_descriptor, dict):
        raise RuntimeError("GHCR image manifest has an invalid config descriptor.")
    image_config_digest = config_descriptor.get("digest")
    if not isinstance(image_config_digest, str) or not image_config_digest:
        raise RuntimeError("GHCR image manifest has no valid config digest.")
    config=get(f"blobs/{image_config_digest}","application/json")
    if not isinstance(config, dict) or not isinstance(config.get("config"), dict):
        raise RuntimeError("GHCR returned an invalid image config.")
    actual_labels=config["config"].get("Labels")
    if actual_labels is None:
        actual_labels = {}
    elif not isinstance(actual_labels, dict):
        raise RuntimeError("GHCR image config contains invalid labels.")
    mismatches=[key for key,value in labels.items() if actual_labels.get(key)!=value]
    if mismatches:
        raise RuntimeError("GHCR image labels do not match the expected fingerprints: "+", ".join(sorted(mismatches)))
    return True

# OpenWrt's push-containers.yml is the upstream policy boundary for
# rebuilding the published build containers. Keep this list aligned with
# that workflow rather than attempting to reconstruct Make/Kconfig dependencies.
UPSTREAM_CONTAINER_EXACT_PATHS = frozenset({
    "include/version.mk",
    "include/cmake.mk",
    ".github/workflows/push-containers.yml",
})
UPSTREAM_CONTAINER_DIRECTORIES = ("tools/", "toolchain/")

HOST_TOOLS_BUILD_RECIPE = (
    "host-tools-v3; config:DEVEL,AUTOREMOVE,CCACHE; "
    "commands:make-defconfig,make-tools-install"
)
HOST_TOOLS_CONFIG = "CONFIG_DEVEL=y\nCONFIG_AUTOREMOVE=y\nCONFIG_CCACHE=y\n"
BUILDWORKER_IMAGE = "ghcr.io/openwrt/buildbot/buildworker-v3.11.8:v23"
HOST_TOOLS_BUILD_ENVIRONMENT = {
    "runner": "ubuntu-24.04",
    "platform": "linux/amd64",
    "buildworker_image": BUILDWORKER_IMAGE,
}
RUNTIME_DOCKERFILE = ".github/dockerfiles/Dockerfile.tools"

TOOLCHAIN_BUILD_RECIPE = (
    "toolchain-v2; config:CCACHE,DEVEL,AUTOREMOVE,TARGET,SUBTARGET; "
    "commands:make-defconfig,make-tools-install,make-toolchain-install,"
    "make-target-toolchain-compile; host-tools:prebuilt-artifact; "
    "command:./scripts/ext-tools.sh --refresh"
)
TOOLCHAIN_BUILD_ENVIRONMENT = {
    "runner": "ubuntu-24.04",
    "platform": "linux/amd64",
    "buildworker_image": BUILDWORKER_IMAGE,
}
TOOLCHAIN_DOCKERFILE = ".github/dockerfiles/Dockerfile.toolchain"


def is_upstream_container_input(path: str) -> bool:
    """Return whether a path matches OpenWrt's published container rebuild filter."""
    return (
        path in UPSTREAM_CONTAINER_EXACT_PATHS
        or path.startswith(UPSTREAM_CONTAINER_DIRECTORIES)
    )


def upstream_container_files(source_dir: Path) -> list[str]:
    """Tracked, existing source files selected by the upstream rebuild policy."""
    return [
        path for path in tracked_paths(source_dir)
        if is_upstream_container_input(path) and (source_dir / path).is_file()
    ]


def validate_worker_image(local_repo_dir: Path, workflow_path: str, dockerfile_path: str) -> None:
    workflow = (local_repo_dir / workflow_path).read_text(encoding="utf-8")
    dockerfile = (local_repo_dir / dockerfile_path).read_text(encoding="utf-8")
    # Host Tools declares the worker as a job container; the reusable firmware
    # workflow selects the same worker dynamically for its prebuilt-artifact path.
    worker_reference = BUILDWORKER_IMAGE.removeprefix("ghcr.io/")
    if BUILDWORKER_IMAGE not in workflow and worker_reference not in workflow:
        raise RuntimeError(f"Builder workflow does not reference {BUILDWORKER_IMAGE}.")
    if not dockerfile.startswith(f"FROM {BUILDWORKER_IMAGE}\n"):
        raise RuntimeError(f"Runtime Dockerfile does not use {BUILDWORKER_IMAGE}.")


def fingerprint_file_records(source_dir: Path, local_repo_dir: Path, dockerfile: str) -> list[dict]:
    records = [
        {
            "scope": "upstream-source",
            "path": path,
            "sha256": sha256_file(source_dir / path),
        }
        for path in upstream_container_files(source_dir)
    ]
    if not records:
        raise RuntimeError("No tracked upstream container inputs were found.")
    runtime_path = local_repo_dir / dockerfile
    if not runtime_path.is_file():
        raise RuntimeError(f"Required local runtime Dockerfile is missing: {dockerfile}")
    records.append({
        "scope": "runtime-image",
        "path": dockerfile,
        "sha256": sha256_file(runtime_path),
    })
    return records


def compute_tools_fingerprint(
    source_dir: Path, local_repo_dir: Path, source_repo: str
) -> tuple[str, dict]:
    source_dir = source_dir.resolve()
    local_repo_dir = local_repo_dir.resolve()
    validate_worker_image(
        local_repo_dir,
        ".github/workflows/reusable_build_tools.yml",
        RUNTIME_DOCKERFILE,
    )
    manifest = {
        "schema": 4,
        "source_repository": source_repo,
        "host_tools_config": HOST_TOOLS_CONFIG,
        "host_tools_build_recipe": HOST_TOOLS_BUILD_RECIPE,
        "build_environment": HOST_TOOLS_BUILD_ENVIRONMENT,
        "files": fingerprint_file_records(
            source_dir, local_repo_dir, RUNTIME_DOCKERFILE
        ),
    }
    return canonical_sha256(manifest, ensure_ascii=True), manifest


def compute_manifest(
    source_dir: Path,
    local_repo_dir: Path,
    source_repo: str,
    target: str,
    subtarget: str,
    tools_fingerprint: str,
) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", target) or not re.fullmatch(
        r"[A-Za-z0-9_-]+", subtarget
    ):
        raise RuntimeError("Target and subtarget must be simple OpenWrt identifiers.")
    if not re.fullmatch(r"[0-9a-f]{64}", tools_fingerprint):
        raise RuntimeError("Host Tools fingerprint must be a full 64-character lowercase SHA-256 digest.")

    source_dir = source_dir.resolve()
    local_repo_dir = local_repo_dir.resolve()
    validate_worker_image(
        local_repo_dir,
        ".github/workflows/reusable_build.yml",
        TOOLCHAIN_DOCKERFILE,
    )
    return {
        "schema": 2,
        "source_repository": source_repo,
        "target": target,
        "subtarget": subtarget,
        "dependencies": {"host_tools_fingerprint": tools_fingerprint},
        "toolchain_build_recipe": TOOLCHAIN_BUILD_RECIPE,
        "build_environment": TOOLCHAIN_BUILD_ENVIRONMENT,
        "files": fingerprint_file_records(
            source_dir, local_repo_dir, TOOLCHAIN_DOCKERFILE
        ),
    }


def compute_toolchain_fingerprint(*args, **kwargs):
    manifest = compute_manifest(*args, **kwargs)
    return canonical_sha256(manifest, ensure_ascii=False), manifest


def main():
    p=argparse.ArgumentParser(); s=p.add_subparsers(dest="command",required=True)
    q=s.add_parser("tools"); q.add_argument("--source-dir",required=True,type=Path); q.add_argument("--local-repo-dir",required=True,type=Path); q.add_argument("--source-repo",required=True)
    q=s.add_parser("toolchain"); q.add_argument("--source-dir",required=True,type=Path); q.add_argument("--local-repo-dir",required=True,type=Path); q.add_argument("--source-repo",required=True); q.add_argument("--target",required=True); q.add_argument("--subtarget",required=True); q.add_argument("--tools-fingerprint",required=True); q.add_argument("--output-file",type=Path,help="Write Actions outputs to this file instead of GITHUB_OUTPUT")
    q=s.add_parser("publish-toolchain-outputs"); q.add_argument("--input-file",required=True,type=Path); q.add_argument("--expected-source-commit",required=True); q.add_argument("--github-output",required=True,type=Path)
    q=s.add_parser("ghcr-check"); q.add_argument("--owner",required=True); q.add_argument("--image",required=True); q.add_argument("--tag",required=True); q.add_argument("--label",action="append",default=[])
    a=p.parse_args()
    if a.command=="tools":
        f,_=compute_tools_fingerprint(a.source_dir,a.local_repo_dir,a.source_repo); write_outputs({"tools_fingerprint":f,"source_commit":run_git(a.source_dir,"rev-parse","HEAD").decode().strip()}); print(f); return 0
    if a.command=="toolchain":
        f,_=compute_toolchain_fingerprint(a.source_dir,a.local_repo_dir,a.source_repo,a.target,a.subtarget,a.tools_fingerprint); write_outputs({"toolchain_fingerprint":f,"toolchain_cache_key":f[:32],"source_commit":run_git(a.source_dir,"rev-parse","HEAD").decode().strip()}, a.output_file); print(f); return 0
    if a.command=="publish-toolchain-outputs":
        publish_toolchain_outputs(a.input_file,a.expected_source_commit,a.github_output); return 0
    labels={}; 
    for item in a.label:
        if "=" not in item: raise ValueError("Invalid label")
        k,v=item.split("=",1); labels[k]=v
    return 0 if ghcr_check(a.owner,a.image,a.tag,labels) else 1
def run_cli():
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError, URLError) as e:
        print(f"fingerprint: {e}", file=sys.stderr)
        raise SystemExit(2)
    except Exception as e:
        print(f"fingerprint: unexpected {type(e).__name__}: {e}", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    run_cli()
