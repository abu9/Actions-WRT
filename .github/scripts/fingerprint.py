#!/usr/bin/env python3
"""Unified content fingerprint and GHCR verification entry point."""
from __future__ import annotations
import argparse, base64, gzip, hashlib, json, os, re, subprocess, sys
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

def run_git(repo: Path, *args: str) -> bytes:
    result=subprocess.run(["git","-C",str(repo),*args],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE); return result.stdout
def tracked_paths(repo: Path) -> list[str]:
    return sorted(item.decode("utf-8") for item in run_git(repo,"ls-files","-z").split(b"\0") if item)
def sha256_file(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()
def canonical_sha256(value: dict, *, ensure_ascii: bool = False) -> str: return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=ensure_ascii).encode("utf-8")).hexdigest()
def write_outputs(values: dict[str,str], output_file: Path | None = None) -> None:
    """Write GitHub Actions outputs, optionally to an explicitly supplied handoff file."""
    path = output_file or (Path(os.environ["GITHUB_OUTPUT"]) if os.environ.get("GITHUB_OUTPUT") else None)
    if path is not None:
        with path.open("a", encoding="utf-8") as f:
            for key, value in values.items():
                f.write(f"{key}={value}\n")
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
        if e.code==404:return False
        raise RuntimeError(f"GHCR token request failed: HTTP {e.code}") from e
    bearer=payload.get("token") or payload.get("access_token")
    if not bearer: raise RuntimeError("GHCR returned an empty pull token.")
    def get(path,accept):
        try:
            with urlopen(Request(f"https://ghcr.io/v2/{owner}/{image}/{path}",headers={"Authorization":f"Bearer {bearer}","Accept":accept}),timeout=30) as r:return json.load(r)
        except HTTPError as e:
            if e.code==404:return None
            raise RuntimeError(f"GHCR request failed: HTTP {e.code}") from e
    manifest=get(f"manifests/{tag}","application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json")
    if manifest is None:return False
    if isinstance(manifest.get("manifests"),list):
        digest=next((m.get("digest") for m in manifest["manifests"] if (m.get("platform") or {}).get("os")=="linux" and (m.get("platform") or {}).get("architecture")=="amd64"),None)
        if not digest:return False
        manifest=get(f"manifests/{digest}","application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json")
        if manifest is None:return False
    digest=(manifest.get("config") or {}).get("digest")
    if not digest:return False
    config=get(f"blobs/{digest}","application/json")
    return config is not None and all((config.get("config",{}).get("Labels") or {}).get(k)==v for k,v in labels.items())

HOST_TOOLS_BUILD_RECIPE=("host-tools-v2; config:DEVEL,AUTOREMOVE,CCACHE; commands:make-defconfig,make-tools-install")
HOST_TOOLS_CONFIG="CONFIG_DEVEL=y\nCONFIG_AUTOREMOVE=y\nCONFIG_CCACHE=y\n"
HOST_TOOLS_BUILD_ENVIRONMENT={"runner":"ubuntu-24.04","platform":"linux/amd64","buildworker_image":"ghcr.io/openwrt/buildbot/buildworker-v3.11.8:v23"}
RUNTIME_DOCKERFILE=".github/dockerfiles/Dockerfile.tools"
def is_host_tool_input(path: str) -> bool:
    if path == "scripts/ext-tools.sh":
        return False
    if path in {"Makefile", "rules.mk", "Config.in"}:
        return True
    if path.startswith(("include/", "tools/", "scripts/", "config/")):
        return True
    # Host Tools run make defconfig, so retain toolchain Kconfig definitions.
    # Toolchain component implementation files are not built by make tools/install.
    return path == "toolchain/Config.in" or (
        path.startswith("toolchain/") and path.endswith("/Config.in")
    )
def compute_tools_fingerprint(source_dir: Path,local_repo_dir: Path,source_repo: str,source_ref: str) -> tuple[str,dict]:
    source_dir=source_dir.resolve(); local_repo_dir=local_repo_dir.resolve()
    source_files=sorted(p for p in run_git(source_dir,"ls-files","-z").decode().split("\0") if p and is_host_tool_input(p))
    if not source_files: raise RuntimeError("No tracked host-tools source inputs were found.")
    worker=HOST_TOOLS_BUILD_ENVIRONMENT["buildworker_image"]
    builder=(local_repo_dir/".github/workflows/reusable_build_tools.yml").read_text(encoding="utf-8"); dockerfile=local_repo_dir/RUNTIME_DOCKERFILE
    if f"container: {worker}" not in builder: raise RuntimeError("Host Tools builder image does not match the fingerprinted build environment.")
    if not dockerfile.read_text(encoding="utf-8").startswith(f"FROM {worker}\n"): raise RuntimeError("Host Tools runtime image does not match the fingerprinted build environment.")
    files=[{"scope":"source","path":p,"sha256":sha256_file(source_dir/p)} for p in source_files]+[{"scope":"runtime-image","path":RUNTIME_DOCKERFILE,"sha256":sha256_file(dockerfile)}]
    manifest={"schema":3,"source_repository":source_repo,"host_tools_config":HOST_TOOLS_CONFIG,"host_tools_build_recipe":HOST_TOOLS_BUILD_RECIPE,"build_environment":HOST_TOOLS_BUILD_ENVIRONMENT,"files":files}
    return canonical_sha256(manifest, ensure_ascii=True), manifest
TOOLCHAIN_BUILD_RECIPE = (
    "toolchain-v1;"
    "config:CCACHE,DEVEL,AUTOREMOVE,TARGET,SUBTARGET;"
    "commands:make-defconfig,make-tools-install,make-toolchain-install,"
    "make-target-toolchain-compile;"
    "host-tools:prebuilt-artifact; command:./scripts/ext-tools.sh --refresh"
)
TOOLCHAIN_BUILD_ENVIRONMENT = {
    "runner": "ubuntu-24.04",
    "platform": "linux/amd64",
    "buildworker_image": "ghcr.io/openwrt/buildbot/buildworker-v3.11.8:v23",
}
TOOLCHAIN_DOCKERFILE = ".github/dockerfiles/Dockerfile.toolchain"

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
    for required in ("Makefile", "rules.mk", "Config.in", board_makefile, subtarget_makefile, "scripts/ext-tools.sh"):
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
    source_inputs.add("scripts/ext-tools.sh")
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

    local_inputs = [TOOLCHAIN_DOCKERFILE]
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
        "build_environment": TOOLCHAIN_BUILD_ENVIRONMENT,
        "files": file_records,
    }


def compute_toolchain_fingerprint(*args,**kwargs):
    manifest=compute_manifest(*args,**kwargs); return canonical_sha256(manifest, ensure_ascii=False),manifest
def main():
    p=argparse.ArgumentParser(); s=p.add_subparsers(dest="command",required=True)
    q=s.add_parser("tools"); q.add_argument("--source-dir",required=True,type=Path); q.add_argument("--local-repo-dir",required=True,type=Path); q.add_argument("--source-repo",required=True); q.add_argument("--source-ref",required=True)
    q=s.add_parser("toolchain"); q.add_argument("--source-dir",required=True,type=Path); q.add_argument("--local-repo-dir",required=True,type=Path); q.add_argument("--source-repo",required=True); q.add_argument("--source-ref",required=True); q.add_argument("--target",required=True); q.add_argument("--subtarget",required=True); q.add_argument("--tools-fingerprint",required=True); q.add_argument("--output-file",type=Path,help="Write Actions outputs to this file instead of GITHUB_OUTPUT")
    q=s.add_parser("ghcr-check"); q.add_argument("--owner",required=True); q.add_argument("--image",required=True); q.add_argument("--tag",required=True); q.add_argument("--label",action="append",default=[])
    a=p.parse_args()
    if a.command=="tools":
        f,_=compute_tools_fingerprint(a.source_dir,a.local_repo_dir,a.source_repo,a.source_ref); write_outputs({"tools_fingerprint":f,"source_commit":run_git(a.source_dir,"rev-parse","HEAD").decode().strip()}); print(f); return 0
    if a.command=="toolchain":
        f,_=compute_toolchain_fingerprint(a.source_dir,a.local_repo_dir,a.source_repo,a.source_ref,a.target,a.subtarget,a.tools_fingerprint); write_outputs({"toolchain_fingerprint":f,"toolchain_cache_key":f[:32],"source_commit":run_git(a.source_dir,"rev-parse","HEAD").decode().strip()}, a.output_file); print(f); return 0
    labels={}; 
    for item in a.label:
        if "=" not in item: raise ValueError("Invalid label")
        k,v=item.split("=",1); labels[k]=v
    return 0 if ghcr_check(a.owner,a.image,a.tag,labels) else 1
if __name__=="__main__":
    try: raise SystemExit(main())
    except (OSError,RuntimeError,ValueError,subprocess.CalledProcessError,URLError) as e: print(f"fingerprint: {e}",file=sys.stderr); raise SystemExit(2)
