#!/usr/bin/env python3
"""Validate a local FinPaperMonitor deployment without sending messages."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Report:
    def __init__(self) -> None:
        self.failures = 0
        self.warnings = 0

    def pass_(self, message: str) -> None:
        print(f"[PASS] {message}")

    def warn(self, message: str) -> None:
        self.warnings += 1
        print(f"[WARN] {message}")

    def fail(self, message: str) -> None:
        self.failures += 1
        print(f"[FAIL] {message}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check FinPaperMonitor deployment prerequisites")
    parser.add_argument("--skip-openclaw-health", action="store_true")
    return parser.parse_args()


def load_local_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def find_openclaw() -> str | None:
    configured = os.getenv("PAPER_MONITOR_OPENCLAW_PATH", "").strip()
    if configured:
        expanded = str(Path(configured).expanduser())
        return expanded if Path(expanded).exists() else shutil.which(configured)
    discovered = shutil.which("openclaw")
    if discovered:
        return discovered
    home_candidate = Path.home() / ".openclaw" / "bin" / "openclaw"
    return str(home_candidate) if home_candidate.exists() else None


def check_python(report: Report) -> None:
    if sys.version_info >= (3, 10):
        report.pass_(f"Python {sys.version.split()[0]}")
    else:
        report.fail("Python 3.10 or newer is required")

    dependencies = {
        "requests": "requests",
        "yaml": "PyYAML",
        "bs4": "beautifulsoup4",
        "deep_translator": "deep-translator",
        "rapidfuzz": "rapidfuzz",
    }
    missing = []
    for module, package in dependencies.items():
        try:
            importlib.import_module(module)
        except ImportError:
            missing.append(package)
    if missing:
        report.fail(f"Missing Python dependencies: {', '.join(missing)}")
    else:
        report.pass_("Python dependencies")


def check_files(report: Report) -> None:
    required = [
        ROOT / "config" / "keywords_top3.yml",
        ROOT / "config" / "keywords_econ5.yml",
        ROOT / "config" / "keywords_nber.yml",
        ROOT / "config" / "keywords_common.yml",
        ROOT / "config" / "llm_finance_daily.yml",
        ROOT / "scripts" / "run_delivery.py",
    ]
    missing = [str(path.relative_to(ROOT)) for path in required if not path.exists()]
    if missing:
        report.fail(f"Missing project files: {', '.join(missing)}")
    else:
        report.pass_("Monitor code and rule configs")

    if (ROOT / ".env").exists():
        report.pass_("Local .env exists")
    else:
        report.fail("Missing .env; copy .env.example and configure it")


def check_archive_config(report: Report) -> None:
    path = ROOT / "config" / "doc_archive.local.yml"
    if not path.exists():
        report.fail("Missing config/doc_archive.local.yml")
        return
    try:
        import yaml

        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        report.fail(f"Invalid doc_archive.local.yml: {exc}")
        return

    invalid = []
    for source in ("top3", "econ5", "nber", "llm_finance"):
        section = payload.get(source) if isinstance(payload, dict) else None
        if not isinstance(section, dict):
            invalid.append(source)
            continue
        values = [str(section.get(key) or "").strip() for key in ("doc_token", "wiki_token", "wiki_url")]
        values = [value for value in values if value and "paste_" not in value]
        if not values:
            invalid.append(source)
    if invalid:
        report.fail(f"Missing real Feishu document target for: {', '.join(invalid)}")
    else:
        report.pass_("Feishu document targets for all four monitors")


def check_feishu_credentials(report: Report) -> None:
    env_credentials = bool(os.getenv("FEISHU_APP_ID", "").strip() and os.getenv("FEISHU_APP_SECRET", "").strip())
    config_path = Path.home() / ".openclaw" / "openclaw.json"
    config_credentials = False
    feishu_enabled = False
    if config_path.exists():
        try:
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            feishu = ((payload.get("channels") or {}).get("feishu") or {})
            feishu_enabled = bool(feishu.get("enabled", True))
            pairs = [(feishu.get("appId"), feishu.get("appSecret"))]
            accounts = feishu.get("accounts") or {}
            if isinstance(accounts, dict):
                pairs.extend(
                    (account.get("appId"), account.get("appSecret"))
                    for account in accounts.values()
                    if isinstance(account, dict)
                )
            config_credentials = any(bool(app_id and secret) for app_id, secret in pairs)
        except Exception as exc:
            report.fail(f"Cannot parse OpenClaw config: {exc}")
            return

    if env_credentials or config_credentials:
        report.pass_("Feishu app credentials are configured")
    else:
        report.fail("Missing Feishu app credentials in OpenClaw config or environment")
    if config_path.exists() and not feishu_enabled:
        report.fail("OpenClaw Feishu channel is disabled")

    if os.getenv("PAPER_MONITOR_FEISHU_TARGET", "").strip():
        report.pass_("Feishu delivery target is configured")
    else:
        report.fail("PAPER_MONITOR_FEISHU_TARGET is empty in .env")


def check_openclaw(report: Report, skip_health: bool) -> None:
    executable = find_openclaw()
    if not executable:
        report.fail("OpenClaw executable was not found")
        return
    try:
        version = subprocess.run(
            [executable, "--version"],
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except Exception as exc:
        report.fail(f"Cannot execute OpenClaw: {exc}")
        return
    if version.returncode != 0:
        report.fail("OpenClaw --version failed")
        return
    report.pass_(version.stdout.strip() or "OpenClaw executable")

    if skip_health:
        report.warn("OpenClaw health check skipped")
        return
    health = subprocess.run(
        [executable, "health"],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if health.returncode == 0:
        report.pass_("OpenClaw gateway health")
    else:
        report.fail("OpenClaw gateway is not healthy")


def main() -> int:
    args = parse_args()
    load_local_env(ROOT / ".env")
    report = Report()
    check_python(report)
    check_files(report)
    check_archive_config(report)
    check_feishu_credentials(report)
    check_openclaw(report, skip_health=args.skip_openclaw_health)
    print(f"\nfailures={report.failures} warnings={report.warnings}")
    return 1 if report.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
