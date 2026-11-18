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


class ReleaseIndexVerifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[3]
        cls.helper = cls.root / ".github/scripts/verify-release-index.sh"
        cls.workflow = (cls.root / ".github/workflows/reusable_build.yml").read_text(
            encoding="utf-8"
        )

    def test_workflow_selects_distribution_specific_signature_format(self) -> None:
        self.assertIn("immortalwrt) SIGNATURE_FILE=sha256sums.sig", self.workflow)
        self.assertIn("openwrt) SIGNATURE_FILE=sha256sums.asc", self.workflow)
        self.assertIn('AUTHOR: ${{ inputs.author }}', self.workflow)
        self.assertIn("repository: openwrt/keyring", self.workflow)
        self.assertIn("repository: immortalwrt/keyring", self.workflow)
        self.assertNotIn("source-pinned release signing keyring", self.workflow)
        self.assertIn('verify "$AUTHOR" staging_dir/host/bin/usign', self.workflow)
        self.assertIn('CHECKSUM_OUTPUT="$(sha256sum --check --ignore-missing sha256sums)"', self.workflow)
        self.assertIn('grep -Fq "$TOOLCHAIN_ARCHIVE: OK" <<<"$CHECKSUM_OUTPUT"', self.workflow)
        verify_index = self.workflow.index('verify "$AUTHOR" staging_dir/host/bin/usign')
        verify_archive = self.workflow.index('CHECKSUM_OUTPUT="$(sha256sum --check --ignore-missing sha256sums)"')
        extract_archive = self.workflow.index('tar --zstd -xf "$TOOLCHAIN_ARCHIVE"')
        self.assertLess(verify_index, verify_archive)
        self.assertLess(verify_archive, extract_archive)

        lines = self.workflow.splitlines()
        download_start = next(
            index for index, line in enumerate(lines)
            if line == "      - name: Download external toolchain/sdk"
        )
        download_end = next(
            index for index in range(download_start + 1, len(lines))
            if lines[index].startswith("      - name: ")
        )
        download_step = lines[download_start:download_end]
        self.assertTrue(
            any(line.strip() == "set -euo pipefail" for line in download_step)
        )
        self.assertTrue(
            any(
                line.strip() == 'shell: su buildbot -c "bash --noprofile --norc -eo pipefail {0}"'
                for line in download_step
            )
        )
        self.assertNotIn('CHECKSUM="$(awk', self.workflow)

    def test_toolchain_install_stage_is_unconditional_like_upstream(self) -> None:
        lines = self.workflow.splitlines()
        start = next(
            index for index, line in enumerate(lines)
            if line == "      - name: Build toolchain"
        )
        end = next(
            index for index in range(start + 1, len(lines))
            if lines[index].startswith("      - name: ")
        )
        step_lines = lines[start:end]
        self.assertFalse(any(line.strip().startswith("if:") for line in step_lines))
        self.assertIn("make toolchain/install", "\\n".join(step_lines))

    @staticmethod
    def _create_keyring_checkout(root: Path, origin: str, files: dict[str, str]) -> tuple[Path, str]:
        keyring = root / "keyring"
        keyring.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(keyring)], check=True)
        subprocess.run(
            ["git", "-C", str(keyring), "config", "user.email", "keyring-test@example.invalid"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(keyring), "config", "user.name", "Keyring Test"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(keyring), "remote", "add", "origin", origin],
            check=True,
        )
        for relative, value in files.items():
            path = keyring / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value, encoding="utf-8")
        subprocess.run(["git", "-C", str(keyring), "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", str(keyring), "commit", "-qm", "keyring fixture"],
            check=True,
        )
        commit = subprocess.run(
            ["git", "-C", str(keyring), "rev-parse", "HEAD"],
            check=True, text=True, capture_output=True,
        ).stdout.strip()
        return keyring, commit

    def test_resolve_openwrt_uses_independently_checked_out_official_keyring(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-index-openwrt-resolve-") as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            names = (
                "gpg/CD54E82DADB3684D.asc",
                "gpg/0x1D53D1877742E911.asc",
                "gpg/626471F1.asc",
            )
            keyring, commit = self._create_keyring_checkout(
                root,
                "https://github.com/openwrt/keyring",
                {name: "test public key\n" for name in names},
            )
            github_env = root / "github-env"
            result = subprocess.run(
                ["bash", str(self.helper), "resolve", "openwrt", str(source), str(keyring), str(github_env)],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stdout.strip(), str(keyring / "gpg"))
            values = dict(line.split("=", 1) for line in github_env.read_text(encoding="utf-8").splitlines())
            self.assertEqual(values["KEYRING_COMMIT"], commit)
            self.assertEqual(values["KEYRING_GPG_DIR"], str(keyring / "gpg"))
            self.assertEqual(values["KEYRING_USIGN_KEY"], "")

    def test_resolve_immortalwrt_selects_key_only_from_official_keyring(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-index-immortalwrt-resolve-") as temporary:
            root = Path(temporary)
            source = root / "source"
            makefile = source / "package/system/openwrt-keyring/Makefile"
            makefile.parent.mkdir(parents=True)
            makefile.write_text(
                "PKG_SOURCE_VERSION:=1111111111111111111111111111111111111111\n"
                "$(INSTALL_DATA) $(PKG_BUILD_DIR)/usign/467a35c14d7fbc5c $(1)/etc/opkg/keys/\n",
                encoding="utf-8",
            )
            keyring, commit = self._create_keyring_checkout(
                root,
                "https://github.com/immortalwrt/keyring.git",
                {"usign/467a35c14d7fbc5c": "official public usign key\n"},
            )
            github_env = root / "github-env"
            result = subprocess.run(
                ["bash", str(self.helper), "resolve", "immortalwrt", str(source), str(keyring), str(github_env)],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stdout.strip(), str(keyring / "usign/467a35c14d7fbc5c"))
            values = dict(line.split("=", 1) for line in github_env.read_text(encoding="utf-8").splitlines())
            self.assertEqual(values["KEYRING_COMMIT"], commit)
            self.assertNotEqual(values["KEYRING_COMMIT"], "1" * 40)
            self.assertEqual(values["KEYRING_USIGN_KEY"], str(keyring / "usign/467a35c14d7fbc5c"))
            self.assertEqual(values["KEYRING_GPG_DIR"], "")

    def test_resolve_rejects_key_not_present_in_official_immortalwrt_keyring(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-index-untrusted-key-") as temporary:
            root = Path(temporary)
            source = root / "source"
            makefile = source / "package/system/openwrt-keyring/Makefile"
            makefile.parent.mkdir(parents=True)
            makefile.write_text(
                "$(INSTALL_DATA) $(PKG_BUILD_DIR)/usign/aaaaaaaaaaaaaaaa $(1)/etc/opkg/keys/\n",
                encoding="utf-8",
            )
            keyring, _ = self._create_keyring_checkout(
                root,
                "https://github.com/immortalwrt/keyring",
                {"usign/467a35c14d7fbc5c": "official key\n"},
            )
            result = subprocess.run(
                ["bash", str(self.helper), "resolve", "immortalwrt", str(source), str(keyring)],
                text=True, capture_output=True, check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("not present in the official keyring checkout", result.stderr)

    def test_resolve_rejects_unexpected_keyring_origin(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-index-wrong-origin-") as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            keyring, _ = self._create_keyring_checkout(
                root,
                "https://github.com/attacker/keyring",
                {
                    "gpg/CD54E82DADB3684D.asc": "key\n",
                    "gpg/0x1D53D1877742E911.asc": "key\n",
                    "gpg/626471F1.asc": "key\n",
                },
            )
            result = subprocess.run(
                ["bash", str(self.helper), "resolve", "openwrt", str(source), str(keyring)],
                text=True, capture_output=True, check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Unexpected openwrt keyring origin", result.stderr)

    def test_immortalwrt_verify_passes_expected_files_to_usign(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-index-usign-") as temporary:
            root = Path(temporary)
            args_file = root / "args.txt"
            usign = root / "usign"
            usign.write_text(
                "#!/usr/bin/env bash\n"
                'printf "%s\n" "$@" > "$USIGN_ARGS_FILE"\n',
                encoding="utf-8",
            )
            usign.chmod(0o755)
            public_key = root / "release.pub"
            checksum_file = root / "sha256sums"
            signature_file = root / "sha256sums.sig"
            public_key.write_text("public-key\n", encoding="utf-8")
            checksum_file.write_text("signed-checksum-index\n", encoding="utf-8")
            signature_file.write_text("signature\n", encoding="utf-8")

            result = subprocess.run(
                [
                    "bash", str(self.helper), "verify", "immortalwrt",
                    str(usign), str(public_key), "", str(checksum_file),
                    str(signature_file),
                ],
                env=dict(os.environ, USIGN_ARGS_FILE=str(args_file)),
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(
                args_file.read_text(encoding="utf-8").splitlines(),
                [
                    "-V", "-m", str(checksum_file), "-p", str(public_key),
                    "-x", str(signature_file),
                ],
            )

    def test_openwrt_verify_imports_official_gpg_keys_and_verifies_asc(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-index-gpg-") as temporary:
            root = Path(temporary)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            keyring = root / "gpg"
            keyring.mkdir()
            for name in (
                "CD54E82DADB3684D.asc",
                "0x1D53D1877742E911.asc",
                "626471F1.asc",
            ):
                (keyring / name).write_text("test public key\n", encoding="utf-8")
            args_file = root / "gpg-args.txt"
            fake_gpg = bin_dir / "gpg"
            fake_gpg.write_text(
                "#!/usr/bin/env bash\n"
                'printf "%s\n" "$@" >> "$GPG_ARGS_FILE"\n',
                encoding="utf-8",
            )
            fake_gpg.chmod(0o755)
            checksum_file = root / "sha256sums"
            signature_file = root / "sha256sums.asc"
            checksum_file.write_text("signed-checksum-index\n", encoding="utf-8")
            signature_file.write_text("signature\n", encoding="utf-8")

            result = subprocess.run(
                [
                    "bash", str(self.helper), "verify", "openwrt",
                    str(bin_dir / "unused-usign"), "", str(keyring),
                    str(checksum_file), str(signature_file),
                ],
                env=dict(
                    os.environ,
                    PATH=f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
                    GPG_ARGS_FILE=str(args_file),
                ),
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            args = args_file.read_text(encoding="utf-8").splitlines()
            self.assertIn("--import", args)
            self.assertIn(str(keyring / "CD54E82DADB3684D.asc"), args)
            self.assertIn(str(keyring / "0x1D53D1877742E911.asc"), args)
            self.assertIn(str(keyring / "626471F1.asc"), args)
            self.assertIn("--with-fingerprint", args)
            self.assertIn("--verify", args)
            self.assertIn(str(signature_file), args)
            self.assertIn(str(checksum_file), args)

    def test_verify_rejects_missing_signature_before_invoking_verifier(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-index-missing-signature-") as temporary:
            root = Path(temporary)
            usign = root / "usign"
            usign.write_text("#!/usr/bin/env bash\nexit 99\n", encoding="utf-8")
            usign.chmod(0o755)
            public_key = root / "release.pub"
            checksum_file = root / "sha256sums"
            public_key.write_text("public-key\n", encoding="utf-8")
            checksum_file.write_text("signed-checksum-index\n", encoding="utf-8")

            result = subprocess.run(
                [
                    "bash", str(self.helper), "verify", "immortalwrt",
                    str(usign), str(public_key), "", str(checksum_file),
                    str(root / "missing.sig"),
                ],
                text=True, capture_output=True, check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("signature is missing or empty", result.stderr)


    def test_immortalwrt_verifier_propagates_bad_signature_failure(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-index-usign-negative-") as temporary:
            root = Path(temporary)
            usign = root / "usign"
            usign.write_text("#!/usr/bin/env bash\nexit 17\n", encoding="utf-8")
            usign.chmod(0o755)
            public_key = root / "release.pub"
            checksum_file = root / "sha256sums"
            signature_file = root / "sha256sums.sig"
            public_key.write_text("public-key\n", encoding="utf-8")
            checksum_file.write_text("checksum-index\n", encoding="utf-8")
            signature_file.write_text("bad-signature\n", encoding="utf-8")
            result = subprocess.run(
                ["bash", str(self.helper), "verify", "immortalwrt", str(usign),
                 str(public_key), "", str(checksum_file), str(signature_file)],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 17)

    def test_openwrt_verifier_performs_real_detached_signature_check(self) -> None:
        if shutil.which("gpg") is None:
            self.skipTest("GnuPG is unavailable")
        with tempfile.TemporaryDirectory(prefix="release-index-real-gpg-") as temporary:
            root = Path(temporary)
            signing_home = root / "signing-home"
            signing_home.mkdir(mode=0o700)
            checksum_file = root / "sha256sums"
            payload = b"0123456789abcdef"
            checksum_file.write_text(
                hashlib.sha256(payload).hexdigest() + " *toolchain.tar.zst\n",
                encoding="utf-8",
            )
            subprocess.run(
                ["gpg", "--homedir", str(signing_home), "--batch", "--passphrase", "",
                 "--quick-generate-key", "CI Release Verifier <ci-verifier@example.invalid>",
                 "ed25519", "sign", "0"],
                check=True, text=True, capture_output=True,
            )
            public_key = subprocess.run(
                ["gpg", "--homedir", str(signing_home), "--batch", "--armor", "--export"],
                check=True, text=True, capture_output=True,
            ).stdout
            keyring = root / "gpg"
            keyring.mkdir()
            for name in ("CD54E82DADB3684D.asc", "0x1D53D1877742E911.asc", "626471F1.asc"):
                (keyring / name).write_text(public_key, encoding="utf-8")
            signature_file = root / "sha256sums.asc"
            subprocess.run(
                ["gpg", "--homedir", str(signing_home), "--batch", "--yes",
                 "--detach-sign", "--output", str(signature_file), str(checksum_file)],
                check=True, text=True, capture_output=True,
            )

            command = [
                "bash", str(self.helper), "verify", "openwrt",
                str(root / "unused-usign"), "", str(keyring),
                str(checksum_file), str(signature_file),
            ]
            valid = subprocess.run(command, text=True, capture_output=True, check=False)
            self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)

            checksum_file.write_text(
                checksum_file.read_text(encoding="utf-8") + "# tampered\n",
                encoding="utf-8",
            )
            invalid = subprocess.run(command, text=True, capture_output=True, check=False)
            self.assertNotEqual(invalid.returncode, 0)
            self.assertIn("BAD signature", invalid.stderr)

    def test_upstream_sha256sum_check_ignores_unavailable_manifest_entries(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-index-sha256-") as temporary:
            root = Path(temporary)
            archive = root / "toolchain.tar.zst"
            archive.write_bytes(b"verified archive fixture")
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            (root / "sha256sums").write_text(
                f"{digest} *toolchain.tar.zst\n"
                + f"{'0' * 64} *not-downloaded.tar.zst\n",
                encoding="utf-8",
            )
            valid = subprocess.run(
                ["sha256sum", "--check", "--ignore-missing", "sha256sums"],
                cwd=root, text=True, capture_output=True, check=False,
            )
            self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)

            archive.write_bytes(b"tampered archive fixture")
            invalid = subprocess.run(
                ["sha256sum", "--check", "--ignore-missing", "sha256sums"],
                cwd=root, text=True, capture_output=True, check=False,
            )
            self.assertNotEqual(invalid.returncode, 0)

            (root / "sha256sums").write_text(
                f"{digest} *not-the-selected-archive.tar.zst\n",
                encoding="utf-8",
            )
            unmatched = subprocess.run(
                ["sha256sum", "--check", "--ignore-missing", "sha256sums"],
                cwd=root, text=True, capture_output=True, check=False,
            )
            self.assertNotEqual(unmatched.returncode, 0)
            self.assertIn("no file was verified", unmatched.stderr)


    def test_immortalwrt_usign_real_crypto_positive_tamper_and_wrong_key(self) -> None:
        usign_value = os.environ.get("USIGN_BIN")
        if not usign_value:
            self.skipTest("USIGN_BIN is not configured; CI builds upstream usign")
        usign = Path(usign_value)
        if not usign.is_file() or not os.access(usign, os.X_OK):
            self.fail(f"USIGN_BIN is not an executable file: {usign}")

        with tempfile.TemporaryDirectory(prefix="release-index-real-usign-") as temporary:
            root = Path(temporary)
            public_key = root / "release.pub"
            secret_key = root / "release.sec"
            wrong_public_key = root / "wrong.pub"
            wrong_secret_key = root / "wrong.sec"
            checksum_file = root / "sha256sums"
            signature_file = root / "sha256sums.sig"
            checksum_file.write_text(
                hashlib.sha256(b"toolchain archive fixture").hexdigest()
                + " *toolchain.tar.zst\\n",
                encoding="utf-8",
            )
            subprocess.run(
                [str(usign), "-G", "-c", "Actions-WRT CI test key",
                 "-s", str(secret_key), "-p", str(public_key)],
                check=True, text=True, capture_output=True,
            )
            subprocess.run(
                [str(usign), "-S", "-m", str(checksum_file),
                 "-s", str(secret_key), "-x", str(signature_file)],
                check=True, text=True, capture_output=True,
            )
            command = [
                "bash", str(self.helper), "verify", "immortalwrt",
                str(usign), str(public_key), "", str(checksum_file),
                str(signature_file),
            ]
            valid = subprocess.run(command, text=True, capture_output=True, check=False)
            self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)

            checksum_file.write_text(
                checksum_file.read_text(encoding="utf-8") + "# tampered\\n",
                encoding="utf-8",
            )
            tampered = subprocess.run(command, text=True, capture_output=True, check=False)
            self.assertNotEqual(tampered.returncode, 0)

            checksum_file.write_text(
                hashlib.sha256(b"toolchain archive fixture").hexdigest()
                + " *toolchain.tar.zst\\n",
                encoding="utf-8",
            )
            subprocess.run(
                [str(usign), "-G", "-c", "Actions-WRT wrong test key",
                 "-s", str(wrong_secret_key), "-p", str(wrong_public_key)],
                check=True, text=True, capture_output=True,
            )
            wrong_key_command = command.copy()
            wrong_key_command[wrong_key_command.index(str(public_key))] = str(wrong_public_key)
            wrong_key = subprocess.run(
                wrong_key_command, text=True, capture_output=True, check=False
            )
            self.assertNotEqual(wrong_key.returncode, 0)

    def test_failed_real_usign_verification_prevents_archive_extraction(self) -> None:
        usign_value = os.environ.get("USIGN_BIN")
        if not usign_value:
            self.skipTest("USIGN_BIN is not configured; CI builds upstream usign")
        usign = Path(usign_value)
        if not usign.is_file() or not os.access(usign, os.X_OK):
            self.fail(f"USIGN_BIN is not an executable file: {usign}")

        with tempfile.TemporaryDirectory(prefix="release-index-no-extract-") as temporary:
            root = Path(temporary)
            public_key = root / "release.pub"
            secret_key = root / "release.sec"
            checksum_file = root / "sha256sums"
            signature_file = root / "sha256sums.sig"
            archive = root / "toolchain.tar.zst"
            archive.write_bytes(b"archive fixture")
            checksum_file.write_text(
                hashlib.sha256(b"archive fixture").hexdigest()
                + " *toolchain.tar.zst\\n",
                encoding="utf-8",
            )
            subprocess.run(
                [str(usign), "-G", "-c", "Actions-WRT fail-closed test key",
                 "-s", str(secret_key), "-p", str(public_key)],
                check=True, text=True, capture_output=True,
            )
            subprocess.run(
                [str(usign), "-S", "-m", str(checksum_file),
                 "-s", str(secret_key), "-x", str(signature_file)],
                check=True, text=True, capture_output=True,
            )
            checksum_file.write_text(
                checksum_file.read_text(encoding="utf-8") + "# post-signature tamper\\n",
                encoding="utf-8",
            )

            bin_dir = root / "bin"
            bin_dir.mkdir()
            tar_marker = root / "tar-was-called"
            tar = bin_dir / "tar"
            tar.write_text(
                "#!/usr/bin/env bash\\n"
                'touch "$TAR_MARKER"\\n'
                "exit 99\\n",
                encoding="utf-8",
            )
            tar.chmod(0o755)
            shell = (
                "set -euo pipefail\\n"
                'bash "$HELPER" verify immortalwrt "$USIGN_BIN" "$PUBLIC_KEY" "" '
                '"$CHECKSUM_FILE" "$SIGNATURE_FILE"\\n'
                'tar --zstd -xf "$ARCHIVE"\\n'
            )
            result = subprocess.run(
                ["bash", "-c", shell],
                env=dict(
                    os.environ,
                    PATH=f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
                    HELPER=str(self.helper),
                    USIGN_BIN=str(usign),
                    PUBLIC_KEY=str(public_key),
                    CHECKSUM_FILE=str(checksum_file),
                    SIGNATURE_FILE=str(signature_file),
                    ARCHIVE=str(archive),
                    TAR_MARKER=str(tar_marker),
                ),
                text=True, capture_output=True, check=False,
            )
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse(tar_marker.exists(), "tar ran after signature verification failed")

if __name__ == "__main__":
    unittest.main(verbosity=2)
