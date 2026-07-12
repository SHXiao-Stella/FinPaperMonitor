#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "bin" / "python"
ARCHIVE_DIR = ROOT / "data" / "archive_out"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the full Economics Top5 cron workflow")
    parser.add_argument("--date", default=dt.date.today().isoformat(), help="Run date, e.g. 2026-04-26")
    parser.add_argument("--limit", type=int, default=5, help="Maximum number of papers to render")
    parser.add_argument("--from-date", default="2020-01-01", help="Lower bound publication date")
    parser.add_argument("--timeout", type=int, default=900, help="Per-step timeout in seconds")
    return parser.parse_args()


def run_step(name: str, command: list[str], log_path: Path, timeout: int) -> None:
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n\n===== {name} =====\n")
        log.flush()
        subprocess.run(
            command,
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=timeout,
            text=True,
        )


def fail(step: str, detail: str = "") -> int:
    suffix = f"（{detail}）" if detail else ""
    sys.stdout.write(f"Economics Top5 定时任务失败：{step}{suffix}\n")
    return 1


def main() -> int:
    args = parse_args()
    run_date = str(args.date).strip()
    if not run_date:
        return fail("初始化", "date 为空")

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    selected_path = ARCHIVE_DIR / f"econ5-{run_date}.jsonl"
    rendered_path = ARCHIVE_DIR / f"econ5-{run_date}.md"
    log_path = ARCHIVE_DIR / f"econ5-{run_date}.cron.log"
    run_key = f"econ5-{run_date}"

    try:
        run_step(
            "generate",
            [
                str(PYTHON),
                "scripts/run_econ5_daily.py",
                "--limit",
                str(args.limit),
                "--from-date",
                args.from_date,
                "--selected-out",
                str(selected_path),
                "--rendered-out",
                str(rendered_path),
            ],
            log_path,
            args.timeout,
        )
    except subprocess.TimeoutExpired:
        return fail("第一步", "生成正文超时")
    except subprocess.CalledProcessError:
        return fail("第一步", "生成正文失败")

    if not selected_path.exists() or selected_path.stat().st_size == 0:
        return fail("第一步", "selected JSONL 未生成")
    if not rendered_path.exists() or rendered_path.stat().st_size == 0:
        return fail("第一步", "md 正文未生成")

    try:
        run_step(
            "archive",
            [
                str(PYTHON),
                "scripts/append_feishu_doc.py",
                "--source",
                "econ5",
                "--input",
                str(rendered_path),
                "--run-key",
                run_key,
            ],
            log_path,
            args.timeout,
        )
    except subprocess.TimeoutExpired:
        return fail("第二步", "飞书文档归档超时")
    except subprocess.CalledProcessError:
        return fail("第二步", "飞书文档归档失败")

    try:
        run_step(
            "commit",
            [
                str(PYTHON),
                "scripts/run_econ5_daily.py",
                "--commit-from-file",
                str(selected_path),
            ],
            log_path,
            args.timeout,
        )
    except subprocess.TimeoutExpired:
        return fail("第三步", "提交 pushed state 超时")
    except subprocess.CalledProcessError:
        return fail("第三步", "提交 pushed state 失败")

    sys.stdout.write(rendered_path.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
