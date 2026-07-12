from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from subprocess import STDOUT, Popen
from typing import Dict, Optional

from .utils import LOG_DIR, env_first, run_command


class OpenClawNotifier:
    def __init__(self, logger):
        self.logger = logger
        configured_path = env_first("PAPER_MONITOR_OPENCLAW_PATH")
        home_path = Path.home() / ".openclaw" / "bin" / "openclaw"
        configured_executable = None
        if configured_path:
            expanded = str(Path(configured_path).expanduser())
            configured_executable = expanded if Path(expanded).exists() else shutil.which(configured_path)
        self.openclaw_path = configured_executable or shutil.which("openclaw") or (
            str(home_path) if home_path.exists() else "/usr/bin/openclaw"
        )
        self.gateway_timeout = int(env_first("PAPER_MONITOR_GATEWAY_START_TIMEOUT", default="30") or "30")
        self.gateway_log_path = LOG_DIR / "openclaw-gateway.log"

    def ensure_gateway_running(self) -> None:
        check = run_command([self.openclaw_path, "health"], logger=self.logger, timeout=20)
        if check.returncode == 0:
            return

        self.logger.warning("OpenClaw gateway is not healthy, attempting foreground background start.")
        self.gateway_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.gateway_log_path.open("ab") as log_handle:
            Popen(
                [self.openclaw_path, "gateway", "run"],
                stdout=log_handle,
                stderr=STDOUT,
                start_new_session=True,
            )

        deadline = time.time() + self.gateway_timeout
        while time.time() < deadline:
            time.sleep(2)
            check = run_command([self.openclaw_path, "health"], logger=self.logger, timeout=20)
            if check.returncode == 0:
                self.logger.info("OpenClaw gateway is now healthy.")
                return
        raise RuntimeError("OpenClaw gateway did not become healthy in time")

    def send_markdown(
        self,
        message: str,
        channel: str,
        target: str,
        account_id: Optional[str] = None,
        dry_run: bool = False,
    ) -> Dict[str, object]:
        self.ensure_gateway_running()
        command = [
            self.openclaw_path,
            "message",
            "send",
            "--channel",
            channel,
            "--target",
            target,
            "--message",
            message,
            "--json",
        ]
        if account_id:
            command.extend(["--account", account_id])
        if dry_run:
            command.append("--dry-run")

        completed = run_command(command, logger=self.logger, timeout=180)
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "openclaw message send failed")
        output = completed.stdout.strip()
        return json.loads(output) if output else {}
