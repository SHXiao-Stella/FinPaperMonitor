from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Dict, Iterable, List, Optional, Sequence

import yaml


ROOT_DIR = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT_DIR / "config"
DATA_DIR = ROOT_DIR / "data"
LOG_DIR = DATA_DIR / "logs"
STATE_DIR = DATA_DIR / "state"
ARCHIVE_DIR = DATA_DIR / "archive"


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_yaml(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_json(path: Path, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if not path.exists():
        return default or {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    ensure_dir(path.parent)
    with NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=path.parent) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=False)
        handle.write("\n")
        temp_name = handle.name
    os.replace(temp_name, path)


def atomic_write_text(path: Path, text: str) -> None:
    ensure_dir(path.parent)
    with NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=path.parent) as handle:
        handle.write(text)
        temp_name = handle.name
    os.replace(temp_name, path)


def load_env_file(path: Path) -> None:
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


def utc_now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def now_local_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def slugify(text: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or "paper-monitor"


def compact_whitespace(text: Optional[str]) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def chunked(seq: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for start in range(0, len(seq), size):
        yield seq[start : start + size]


def setup_logger(task_name: str, dry_run: bool = False) -> tuple[logging.Logger, Path]:
    ensure_dir(LOG_DIR)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = "-dryrun" if dry_run else ""
    log_path = LOG_DIR / f"{task_name}-{stamp}{suffix}.log"

    logger = logging.getLogger(f"paper_monitor.{task_name}.{stamp}")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    return logger, log_path


def run_command(
    args: Sequence[str],
    logger: Optional[logging.Logger] = None,
    cwd: Optional[Path] = None,
    timeout: int = 120,
    env: Optional[Dict[str, str]] = None,
) -> subprocess.CompletedProcess[str]:
    if logger:
        logger.info("Running command: %s", " ".join(args))
    return subprocess.run(
        list(args),
        cwd=str(cwd) if cwd else None,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def env_first(*names: str, default: Optional[str] = None) -> Optional[str]:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


def first_non_empty(values: Iterable[Optional[str]]) -> Optional[str]:
    for value in values:
        if value:
            return value
    return None
