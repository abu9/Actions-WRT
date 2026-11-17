#!/usr/bin/env bash
set -euo pipefail
if [[ $# -lt 4 ]]; then
  echo "Usage: $0 OWNER IMAGE TAG LABEL=EXPECTED [LABEL=EXPECTED ...]" >&2
  exit 2
fi
OWNER="${1,,}"
IMAGE="$2"
TAG="$3"
shift 3
if [[ ! "$OWNER" =~ ^[a-z0-9_.-]+$ || ! "$IMAGE" =~ ^[a-z0-9_.-]+$ || -z "$TAG" ]]; then
  echo "Invalid GHCR owner, image, or tag." >&2
  exit 2
fi
if [[ -z "${GITHUB_TOKEN:-}" || -z "${GITHUB_ACTOR:-}" ]]; then
  echo "GITHUB_TOKEN and GITHUB_ACTOR are required for GHCR verification." >&2
  exit 2
fi
for label_pair in "$@"; do
  if [[ "$label_pair" != *=* || -z "${label_pair#*=}" ]]; then
    echo "Invalid expected label expression: $label_pair" >&2
    exit 2
  fi
done

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT
if ! token_json="$(curl --fail --location --silent --show-error --retry 2 \
  --user "${GITHUB_ACTOR}:${GITHUB_TOKEN}" \
  "https://ghcr.io/token?scope=repository:${OWNER}/${IMAGE}:pull")"; then
  echo "Unable to obtain a GHCR pull token for ${OWNER}/${IMAGE}." >&2
  exit 2
fi
token="$(jq -r '.token // .access_token // empty' <<<"$token_json")"
if [[ -z "$token" ]]; then
  echo "GHCR returned an empty pull token for ${OWNER}/${IMAGE}." >&2
  exit 2
fi

accept_index='application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json'
if ! status="$(curl --silent --show-error --output "$tmp_dir/manifest.json" \
  --write-out '%{http_code}' \
  --header "Authorization: Bearer $token" \
  --header "Accept: $accept_index" \
  "https://ghcr.io/v2/${OWNER}/${IMAGE}/manifests/${TAG}")"; then
  echo "Unable to read GHCR manifest for ${OWNER}/${IMAGE}:${TAG}." >&2
  exit 2
fi
case "$status" in
  200) ;;
  404) echo "GHCR image not found: ${OWNER}/${IMAGE}:${TAG}"; exit 1 ;;
  *) echo "GHCR manifest request failed (HTTP $status)." >&2; exit 2 ;;
esac
if ! jq -e 'type == "object"' "$tmp_dir/manifest.json" >/dev/null; then
  echo "GHCR returned an invalid image manifest." >&2
  exit 2
fi

if jq -e '.manifests | type == "array"' "$tmp_dir/manifest.json" >/dev/null; then
  manifest_digest="$(jq -r '[.manifests[] | select(.platform.os == "linux" and .platform.architecture == "amd64")][0].digest // empty' "$tmp_dir/manifest.json")"
  if [[ -z "$manifest_digest" ]]; then
    echo "No linux/amd64 image manifest found in ${OWNER}/${IMAGE}:${TAG}."
    exit 1
  fi
  if ! status="$(curl --silent --show-error --output "$tmp_dir/platform-manifest.json" \
    --write-out '%{http_code}' \
    --header "Authorization: Bearer $token" \
    --header 'Accept: application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json' \
    "https://ghcr.io/v2/${OWNER}/${IMAGE}/manifests/${manifest_digest}")"; then
    echo "Unable to read linux/amd64 manifest." >&2
    exit 2
  fi
  case "$status" in
    200) mv "$tmp_dir/platform-manifest.json" "$tmp_dir/manifest.json" ;;
    404) echo "Platform manifest is missing."; exit 1 ;;
    *) echo "GHCR platform manifest request failed (HTTP $status)." >&2; exit 2 ;;
  esac
fi

config_digest="$(jq -r '.config.digest // empty' "$tmp_dir/manifest.json")"
if [[ -z "$config_digest" ]]; then
  echo "Image config digest missing in ${OWNER}/${IMAGE}:${TAG}."
  exit 1
fi
if ! status="$(curl --location --silent --show-error --output "$tmp_dir/config.json" \
  --write-out '%{http_code}' \
  --header "Authorization: Bearer $token" \
  "https://ghcr.io/v2/${OWNER}/${IMAGE}/blobs/${config_digest}")"; then
  echo "Unable to read image config." >&2
  exit 2
fi
case "$status" in
  200) ;;
  404) echo "Image config blob is missing."; exit 1 ;;
  *) echo "GHCR image config request failed (HTTP $status)." >&2; exit 2 ;;
esac

for label_pair in "$@"; do
  label_key="${label_pair%%=*}"
  expected="${label_pair#*=}"
  actual="$(jq -r --arg key "$label_key" '.config.Labels[$key] // empty' "$tmp_dir/config.json")"
  if [[ "$actual" != "$expected" ]]; then
    echo "Stale or unlabelled GHCR image ${OWNER}/${IMAGE}:${TAG}." >&2
    echo "  label: $label_key" >&2
    echo "  actual: ${actual:-<missing>}" >&2
    echo "  expected: $expected" >&2
    exit 1
  fi
done
echo "GHCR image labels verified: ${OWNER}/${IMAGE}:${TAG}"
