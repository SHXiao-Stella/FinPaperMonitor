from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


FAKE_OPENCLAW = r"""#!/usr/bin/env bash
set -euo pipefail

if [[ "$1 $2" == "cron list" ]]; then
    if [[ -n "${FAKE_EXISTING_JOB:-}" ]]; then
        printf '{"jobs":[{"name":"%s"}]}' "$FAKE_EXISTING_JOB"
    else
        printf '{"jobs":[]}'
    fi
elif [[ "$1 $2" == "cron add" ]]; then
    shift 2
    name=""
    schedule=""
    timezone=""
    message=""
    while [[ "$#" -gt 0 ]]; do
        case "$1" in
            --name) name="$2"; shift 2 ;;
            --cron) schedule="$2"; shift 2 ;;
            --tz) timezone="$2"; shift 2 ;;
            --message) message="$2"; shift 2 ;;
            --timeout-seconds|--session|--wake|--channel|--to|--account|--model) shift 2 ;;
            *) shift ;;
        esac
    done
    message="${message//$'\n'/ }"
    printf '%s\t%s\t%s\t%s\n' "$name" "$schedule" "$timezone" "$message" >> "$FAKE_LOG"
    printf '{"id":"fake-%s"}' "${name// /-}"
elif [[ "$1 $2" == "cron rm" ]]; then
    printf 'ROLLBACK %s\n' "$3" >> "$FAKE_LOG"
    printf '{"ok":true}'
else
    printf 'unexpected fake OpenClaw command: %s\n' "$*" >&2
    exit 3
fi
"""


class InstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / "scripts").mkdir()
        (self.root / "deploy" / "openclaw").mkdir(parents=True)
        (self.root / ".venv" / "bin").mkdir(parents=True)
        shutil.copy2(ROOT / "scripts" / "install_openclaw_jobs.sh", self.root / "scripts")
        shutil.copy2(ROOT / "deploy" / "openclaw" / "job_prompt.txt", self.root / "deploy" / "openclaw")
        (self.root / ".venv" / "bin" / "python").symlink_to(sys.executable)

        self.fake_openclaw = self.root / "fake-openclaw"
        self.fake_openclaw.write_text(FAKE_OPENCLAW, encoding="utf-8")
        self.fake_openclaw.chmod(0o755)
        self.log_path = self.root / "fake.log"
        (self.root / ".env").write_text(
            "\n".join(
                [
                    f"PAPER_MONITOR_OPENCLAW_PATH={self.fake_openclaw}",
                    "PAPER_MONITOR_FEISHU_TARGET=test-chat-id",
                    "PAPER_MONITOR_TIMEZONE=Asia/Shanghai",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def run_installer(self, existing_job: str = "") -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["FAKE_LOG"] = str(self.log_path)
        env["FAKE_EXISTING_JOB"] = existing_job
        return subprocess.run(
            ["bash", str(self.root / "scripts" / "install_openclaw_jobs.sh")],
            cwd=self.root,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_installs_all_four_jobs_with_rendered_prompt(self) -> None:
        completed = self.run_installer()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        lines = self.log_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 4)
        self.assertIn("Top3 Finance Daily Monitor\t0 8 * * *\tAsia/Shanghai", lines[0])
        self.assertTrue(any("--source llm_finance" in line for line in lines))
        self.assertTrue(all("{{PROJECT_ROOT}}" not in line for line in lines))
        self.assertTrue(all(str(self.root) in line for line in lines))

    def test_duplicate_job_stops_before_creation(self) -> None:
        completed = self.run_installer(existing_job="Top3 Finance Daily Monitor")
        self.assertEqual(completed.returncode, 2)
        self.assertIn("Refusing to overwrite existing cron job", completed.stderr)
        self.assertFalse(self.log_path.exists())


if __name__ == "__main__":
    unittest.main()
