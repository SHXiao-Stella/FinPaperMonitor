#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from paper_monitor.state import RecordState
from src.models import Paper
from src.translator import PaperTranslator, build_llm_backend

STATE_ECON5 = ROOT / "data" / "pushed_ids_econ5.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Economics Top5 daily monitor")
    parser.add_argument("--selected-out", default=None, help="Optional JSONL file to save selected paper payloads")
    parser.add_argument("--rendered-out", default=None, help="Optional Markdown file to save the final rendered digest")
    parser.add_argument("--limit", type=int, default=5, help="Maximum number of papers to render")
    parser.add_argument("--from-date", default="2020-01-01", help="Lower bound publication date (YYYY-MM-DD)")
    parser.add_argument(
        "--commit-from-file",
        default=None,
        help="Read a previously saved JSONL file and commit its IDs to the Economics Top5 pushed state",
    )
    return parser.parse_args()


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")


def resolve_path(value: str | None, fallback: Path) -> Path:
    if not value:
        return fallback
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    return path


def save_jsonl(path: Path, payloads: List[Dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for payload in payloads:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def load_jsonl(path: Path) -> List[Dict]:
    if not path.exists():
        raise FileNotFoundError(f"JSONL file not found: {path}")

    payloads: List[Dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"invalid JSON on line {lineno} in {path}: {exc}") from exc
            if isinstance(payload, dict):
                payloads.append(payload)
    return payloads


def commit_payloads(payloads: List[Dict], logger: logging.Logger) -> None:
    ids = [str(item.get("id") or "").strip() for item in payloads]
    ids = [item for item in ids if item]
    state = RecordState(str(STATE_ECON5))
    state.add_many(ids)
    logger.info("State saved: %s (+%d ids)", STATE_ECON5.relative_to(ROOT), len(set(ids)))


def payload_to_paper(payload: Dict) -> Paper:
    journal = str(payload.get("journal") or "").strip() or "Economics Top5 Journal"
    published = str(payload.get("published") or "").strip() or None
    abstract = str(payload.get("abstract") or "").strip() or None
    url = str(payload.get("url") or "").strip()
    return Paper(
        title=str(payload.get("title") or "").strip(),
        authors=[str(item).strip() for item in (payload.get("authors") or []) if str(item).strip()],
        abstract=abstract,
        source=journal,
        source_category="econ5",
        date=published,
        link=url,
        paper_id=str(payload.get("id") or "").strip(),
        raw_metadata={"doi": str(payload.get("DOI") or "").strip()},
    )


def render_markdown(payloads: List[Dict], translations: Dict[str, object]) -> str:
    lines: List[str] = []
    for index, payload in enumerate(payloads, start=1):
        paper = payload_to_paper(payload)
        translation = translations.get(paper.paper_id)
        authors = ", ".join(paper.authors) if paper.authors else "未知"
        doi = str(payload.get("DOI") or "").strip() or "N/A"
        abstract_zh = "元数据未提供摘要。"
        if paper.abstract and translation is not None:
            abstract_zh = getattr(translation, "translated_abstract_zh", "").strip() or "未生成中文摘要。"

        lines.extend(
            [
                f"{index}. 标题：{paper.title}",
                f"作者：{authors}",
                f"期刊：{paper.source}",
                f"发布日期：{paper.date or '未知'}",
                f"DOI：{doi}",
                f"链接：{paper.link}",
                "中文摘要：",
                abstract_zh,
                "",
            ]
        )

    return "\n".join(lines).strip() + "\n"


def generate_payloads(selected_out: Path, logger: logging.Logger, limit: int, from_date: str) -> List[Dict]:
    command = [
        sys.executable,
        str(ROOT / "scripts" / "run_monitor.py"),
        "--source",
        "econ5",
        "--limit",
        str(limit),
        "--from-date",
        from_date,
        "--selected-out",
        str(selected_out),
    ]
    logger.info("Running command: %s", " ".join(command))
    completed = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        env=os.environ.copy(),
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(detail or "run_monitor.py failed")

    payloads = load_jsonl(selected_out)
    logger.info("Loaded %d Economics Top5 payloads from %s", len(payloads), selected_out)
    return payloads


def main() -> int:
    setup_logging()
    logger = logging.getLogger("run_econ5_daily")
    args = parse_args()

    if args.commit_from_file:
        payloads = load_jsonl(resolve_path(args.commit_from_file, ROOT / "data" / "archive_out" / "econ5-commit.jsonl"))
        logger.info("Loaded %d payloads from %s for commit", len(payloads), args.commit_from_file)
        commit_payloads(payloads, logger)
        return 0

    selected_out = resolve_path(args.selected_out, ROOT / "data" / "archive_out" / "econ5-selected.jsonl")
    rendered_out = resolve_path(args.rendered_out, ROOT / "data" / "archive_out" / "econ5-rendered.md")

    payloads = generate_payloads(selected_out, logger, limit=args.limit, from_date=args.from_date)
    papers = [payload_to_paper(payload) for payload in payloads]
    backend = build_llm_backend(logger=logger)
    translator = PaperTranslator(backend=backend, logger=logger)
    translations = translator.translate(papers, batch_size=5)
    markdown = render_markdown(payloads, translations)

    if args.selected_out:
        save_jsonl(selected_out, payloads)
        logger.info("Selected payloads written to %s", selected_out)

    if args.rendered_out:
        rendered_out.parent.mkdir(parents=True, exist_ok=True)
        rendered_out.write_text(markdown, encoding="utf-8")
        logger.info("Rendered digest written to %s", rendered_out)

    sys.stdout.write(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
