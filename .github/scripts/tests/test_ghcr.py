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

if __name__ == "__main__":
    unittest.main(verbosity=2)
