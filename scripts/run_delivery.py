#!/usr/bin/env python3
"""Run one monitor with archive-before-state delivery semantics."""

from __future__ import annotations

import argparse
import datetime as dt
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.utils import load_env_file


@dataclass(frozen=True)
class DeliverySpec:
    source: str
    label: str
    failure_label: str
    entrypoint: str
    schedule: str
    extra_args: tuple[str, ...] = ()
    weekly_key: bool = False


SPECS = {
    "top3": DeliverySpec(
        source="top3",
        label="FinTop3",
        failure_label="Top3",
        entrypoint="run_top3_daily.py",
        schedule="0 8 * * *",
    ),
    "nber": DeliverySpec(
        source="nber",
        label="NBER",
        failure_label="NBER",
        entrypoint="run_nber_weekly.py",
        schedule="10 8 * * 1",
        extra_args=("--limit", "30"),
        weekly_key=True,
    ),
    "llm_finance": DeliverySpec(
        source="llm_finance",
        label="LLMFin",
        failure_label="LLM Finance",
        entrypoint="run_llm_finance_daily.py",
        schedule="20 8 * * *",
    ),
    "econ5": DeliverySpec(
        source="econ5",
        label="EconTop5",
        failure_label="EconTop5",
        entrypoint="run_econ5_daily.py",
        schedule="30 8 * * *",
        extra_args=("--limit", "5", "--from-date", "2020-01-01"),
    ),
}


class DeliveryStepError(RuntimeError):
    def __init__(self, step: str, detail: str):
        super().__init__(detail)
        self.step = step
        self.detail = detail


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate, archive, and commit one paper digest")
    parser.add_argument("--source", required=True, choices=sorted(SPECS))
    parser.add_argument("--date", default=dt.date.today().isoformat(), help="Run date in YYYY-MM-DD form")
    parser.add_argument("--timeout", type=int, default=1800, help="Timeout for each subprocess, in seconds")
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Generate the digest without archiving it or changing pushed state",
    )
    return parser.parse_args()


def run_suffix(spec: DeliverySpec, run_date: dt.date) -> str:
    if spec.weekly_key:
        iso = run_date.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    return run_date.isoformat()


def build_commands(
    spec: DeliverySpec,
    run_date: dt.date,
    *,
    root: Path = ROOT,
    python_executable: str = sys.executable,
) -> tuple[list[str], list[str], list[str], Path, Path, str]:
    suffix = run_suffix(spec, run_date)
    archive_dir = root / "data" / "archive_out"
    selected_path = archive_dir / f"{spec.source}-{suffix}.jsonl"
    rendered_path = archive_dir / f"{spec.source}-{suffix}.md"
    run_key = f"{spec.source}-{suffix}"
    entrypoint = root / "scripts" / spec.entrypoint

    generate = [
        python_executable,
        str(entrypoint),
        *spec.extra_args,
        "--selected-out",
        str(selected_path),
        "--rendered-out",
        str(rendered_path),
    ]
    archive = [
        python_executable,
        str(root / "scripts" / "append_feishu_doc.py"),
        "--source",
        spec.source,
        "--input",
        str(rendered_path),
        "--run-key",
        run_key,
    ]
    commit = [
        python_executable,
        str(entrypoint),
        "--commit-from-file",
        str(selected_path),
    ]
    return generate, archive, commit, selected_path, rendered_path, run_key


def run_step(
    command: Sequence[str],
    *,
    step: str,
    root: Path,
    log_path: Path,
    timeout: int,
) -> None:
    try:
        completed = subprocess.run(
            list(command),
            cwd=root,
            env=os.environ.copy(),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise DeliveryStepError(step, f"timed out after {timeout} seconds") from exc
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n[{step}] $ {shlex.join(command)}\n")
        if completed.stdout:
            handle.write(completed.stdout)
            if not completed.stdout.endswith("\n"):
                handle.write("\n")
        if completed.stderr:
            handle.write(completed.stderr)
            if not completed.stderr.endswith("\n"):
                handle.write("\n")

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or f"exit code {completed.returncode}").strip()
        raise DeliveryStepError(step, detail[-2000:])


CommandRunner = Callable[..., None]


def execute_delivery(
    spec: DeliverySpec,
    run_date: dt.date,
    *,
    root: Path = ROOT,
    python_executable: str = sys.executable,
    timeout: int = 1800,
    prepare_only: bool = False,
    runner: CommandRunner = run_step,
) -> str:
    load_env_file(root / ".env")
    generate, archive, commit, selected_path, rendered_path, _ = build_commands(
        spec,
        run_date,
        root=root,
        python_executable=python_executable,
    )
    archive_dir = rendered_path.parent
    archive_dir.mkdir(parents=True, exist_ok=True)
    log_path = archive_dir / f"{spec.source}-{run_suffix(spec, run_date)}.cron.log"

    runner(generate, step="第一步：生成摘要", root=root, log_path=log_path, timeout=timeout)
    if not selected_path.exists():
        raise DeliveryStepError("第一步：生成摘要", f"missing selected output: {selected_path}")
    if not rendered_path.exists() or rendered_path.stat().st_size == 0:
        raise DeliveryStepError("第一步：生成摘要", f"missing or empty rendered output: {rendered_path}")

    if not prepare_only:
        runner(archive, step="第二步：飞书文档归档", root=root, log_path=log_path, timeout=timeout)
        runner(commit, step="第三步：提交 pushed state", root=root, log_path=log_path, timeout=timeout)

    body = rendered_path.read_text(encoding="utf-8").rstrip("\n")
    return f"{spec.label}\n{body}\n"


def main() -> int:
    args = parse_args()
    spec = SPECS[args.source]
    try:
        run_date = dt.date.fromisoformat(args.date)
        output = execute_delivery(
            spec,
            run_date,
            timeout=max(1, args.timeout),
            prepare_only=args.prepare_only,
        )
    except (DeliveryStepError, ValueError) as exc:
        step = exc.step if isinstance(exc, DeliveryStepError) else "参数检查"
        detail = exc.detail if isinstance(exc, DeliveryStepError) else str(exc)
        print(f"{spec.failure_label} 定时任务失败：{step}")
        print(detail, file=sys.stderr)
        return 1

    sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
