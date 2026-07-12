#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.dedupe import PushedStateStore
from src.models import Paper, RankedPaper
from src.normalize import finalize_paper
from src.pipeline import run_pipeline

TASK_NAME = "llm_finance_daily"
CONFIG_PATH = ROOT / "config" / "llm_finance_daily.yml"
STATE_GLOBAL = ROOT / "data" / "state" / "pushed_ids_global.json"
STATE_TASK = ROOT / "data" / "state" / "pushed_ids_llm_finance.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the LLM Finance daily monitor")
    parser.add_argument("--config", default=str(CONFIG_PATH), help="Task config path")
    parser.add_argument("--selected-out", default=None, help="Optional JSONL file to save selected paper payloads")
    parser.add_argument("--rendered-out", default=None, help="Optional Markdown file to save the final rendered digest")
    parser.add_argument(
        "--commit-from-file",
        default=None,
        help="Read a previously saved JSONL file and commit its IDs to the independent LLM Finance pushed state",
    )
    return parser.parse_args()


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")


def payload_from_ranked(item: RankedPaper) -> Dict:
    paper = item.paper
    return {
        "paper_id": paper.paper_id,
        "title": paper.title,
        "authors": list(paper.authors),
        "abstract": paper.abstract,
        "source": paper.source,
        "source_category": paper.source_category,
        "date": paper.date,
        "link": paper.link,
        "canonical_url": paper.canonical_url,
        "normalized_title": paper.normalized_title,
        "raw_metadata": paper.raw_metadata,
        "bucket": item.bucket,
        "why_relevant": item.why_relevant,
        "rule_score": item.rule_score,
        "heuristic_score": item.heuristic_score,
        "llm_relevance_score": item.llm_relevance_score,
        "llm_top_pick_score": item.llm_top_pick_score,
        "final_score": item.final_score,
    }


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


def paper_from_payload(payload: Dict) -> Paper:
    return finalize_paper(
        Paper(
            title=str(payload.get("title") or ""),
            authors=[str(item) for item in (payload.get("authors") or []) if str(item).strip()],
            abstract=str(payload.get("abstract") or "").strip() or None,
            source=str(payload.get("source") or ""),
            source_category=str(payload.get("source_category") or "llm_finance"),
            date=str(payload.get("date") or "").strip() or None,
            link=str(payload.get("link") or ""),
            paper_id=str(payload.get("paper_id") or ""),
            raw_metadata=payload.get("raw_metadata") if isinstance(payload.get("raw_metadata"), dict) else {},
        )
    )


def commit_payloads(payloads: List[Dict], logger: logging.Logger) -> None:
    state_store = PushedStateStore(
        global_state_path=STATE_GLOBAL,
        task_state_path=STATE_TASK,
    )
    committed = 0
    for payload in payloads:
        paper = paper_from_payload(payload)
        if not paper.paper_id:
            continue
        state_store.mark_pushed(paper, TASK_NAME)
        committed += 1

    state_store.save()
    logger.info("State saved: %s (+%d ids)", STATE_TASK.relative_to(ROOT), committed)


def main() -> int:
    setup_logging()
    args = parse_args()
    logger = logging.getLogger("run_llm_finance_daily")

    if args.commit_from_file:
        payloads = load_jsonl(Path(args.commit_from_file).expanduser())
        logger.info("Loaded %d payloads from %s for commit", len(payloads), args.commit_from_file)
        commit_payloads(payloads, logger)
        return 0

    config_path = Path(args.config).expanduser()
    if not config_path.is_absolute():
        config_path = ROOT / config_path

    result = run_pipeline(config_path=config_path, dry_run=True)
    payloads = [payload_from_ranked(item) for item in result.selected]

    if args.selected_out:
        selected_out = Path(args.selected_out).expanduser()
        if not selected_out.is_absolute():
            selected_out = ROOT / selected_out
        save_jsonl(selected_out, payloads)
        logger.info("Selected payloads written to %s", selected_out)

    if args.rendered_out:
        rendered_out = Path(args.rendered_out).expanduser()
        if not rendered_out.is_absolute():
            rendered_out = ROOT / rendered_out
        rendered_out.parent.mkdir(parents=True, exist_ok=True)
        rendered_out.write_text(result.message_markdown, encoding="utf-8")
        logger.info("Rendered digest written to %s", rendered_out)

    sys.stdout.write(result.message_markdown)
    if result.error:
        sys.stderr.write(result.error.rstrip() + "\n")
    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
