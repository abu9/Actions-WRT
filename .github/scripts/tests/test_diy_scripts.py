#!/usr/bin/env python3
"""Build-free regression tests."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


class DiyPart1InvocationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        root = Path(__file__).resolve().parents[3]
        workflow = (root / ".github/workflows/reusable_build.yml").read_text(
            encoding="utf-8"
        )
        start = workflow.index("      - name: Load pre-custom script\n")
        end = workflow.index("\n      - name: ", start + 1)
        step = workflow[start:end]
        run_marker = "        run: |\n"
        cls.run_script = step.split(run_marker, 1)[1]
        cls.run_script = "\n".join(
            line[10:] if line.startswith("          ") else line
            for line in cls.run_script.splitlines()
        ) + "\n"

    def test_workspace_is_resolved_inside_the_buildbot_shell(self) -> None:
        self.assertIn('GITHUB_WORKSPACE="$(cd .. && pwd)"', self.run_script)
        self.assertIn("export GITHUB_WORKSPACE", self.run_script)
        self.assertIn('bash "$GITHUB_WORKSPACE/diy-part1.sh"', self.run_script)
        self.assertNotIn('bash "${GITHUB_WORKSPACE}/diy-part1.sh"', self.run_script)

    def test_diy_script_runs_without_inherited_runner_environment(self) -> None:
        with tempfile.TemporaryDirectory(prefix="diy-part1-workspace-") as temporary:
            workspace = Path(temporary).resolve()
            openwrt = workspace / "openwrt"
            openwrt.mkdir()
            script = workspace / "diy-part1.sh"
            script.write_text(
                '#!/usr/bin/env bash\n'
                'set -euo pipefail\n'
                ': "${GITHUB_WORKSPACE:?GITHUB_WORKSPACE was not resolved}"\n'
                'printf "%s\n" "$GITHUB_WORKSPACE" > "$GITHUB_WORKSPACE/observed-workspace"\n',
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    "bash",
                    "--noprofile",
                    "--norc",
                    "-eo",
                    "pipefail",
                    "-c",
                    self.run_script,
                ],
                cwd=openwrt,
                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(
                (workspace / "observed-workspace").read_text(encoding="utf-8").strip(),
                str(workspace),
            )


class DiyPart2InvocationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        root = Path(__file__).resolve().parents[3]
        workflow = (root / ".github/workflows/reusable_build.yml").read_text(
            encoding="utf-8"
        )
        start = workflow.index("      - name: Load post-custom script\n")
        end = workflow.index("\n      - name: ", start + 1)
        step = workflow[start:end]
        run_marker = "        run: |\n"
        cls.run_script = step.split(run_marker, 1)[1]
        cls.run_script = "\n".join(
            line[10:] if line.startswith("          ") else line
            for line in cls.run_script.splitlines()
        ) + "\n"

    def test_workspace_is_resolved_inside_the_buildbot_shell(self) -> None:
        self.assertIn('GITHUB_WORKSPACE="$(cd .. && pwd)"', self.run_script)
        self.assertIn("export GITHUB_WORKSPACE", self.run_script)
        self.assertIn('bash "$GITHUB_WORKSPACE/diy-part2.sh"', self.run_script)

    def test_diy_script_runs_without_inherited_runner_environment(self) -> None:
        with tempfile.TemporaryDirectory(prefix="diy-part2-workspace-") as temporary:
            workspace = Path(temporary).resolve()
            openwrt = workspace / "openwrt"
            openwrt.mkdir()
            script = workspace / "diy-part2.sh"
            script.write_text(
                '#!/usr/bin/env bash\n'
                'set -euo pipefail\n'
                ': "${GITHUB_WORKSPACE:?GITHUB_WORKSPACE was not resolved}"\n'
                'printf "%s\\n" "$GITHUB_WORKSPACE" > "$GITHUB_WORKSPACE/observed-workspace"\n',
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    "bash",
                    "--noprofile",
                    "--norc",
                    "-eo",
                    "pipefail",
                    "-c",
                    self.run_script,
                ],
                cwd=openwrt,
                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(
                (workspace / "observed-workspace").read_text(encoding="utf-8").strip(),
                str(workspace),
            )

if __name__ == "__main__":
    unittest.main(verbosity=2)
