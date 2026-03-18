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

from paper_monitor.fetch import fetch_all_journals
from paper_monitor.fetch_nber import fetch_nber_working_papers
from paper_monitor.filtering import filter_papers, load_keywords
from paper_monitor.state import RecordState

STATE_TOP3 = "data/pushed_ids_top3.json"
STATE_NBER = "data/pushed_ids_nber.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Monitor top journals and NBER papers")
    parser.add_argument("--source", default="all", help="Data source selector: top3 | nber | all")
    parser.add_argument("--limit", type=int, default=5, help="Maximum number of papers to output")
    parser.add_argument("--from-date", default="2020-01-01", help="Lower bound publication date (YYYY-MM-DD)")
    parser.add_argument("--keywords", default=None, help="Optional keyword config override")
    parser.add_argument("--nber-cache-dir", default="data/cache/nber", help="NBER local cache directory")
    parser.add_argument("--nber-cache-days", type=int, default=7, help="NBER cache max age in days")
    return parser.parse_args()


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


def main() -> int:
    setup_logging()
    args = parse_args()
    logger = logging.getLogger("run_monitor")

    source = str(args.source or "").strip().lower()
    keyword_by_source = {
        "top3": Path("config/keywords_top3.yml"),
        "nber": Path("config/keywords_nber.yml"),
    }

    if args.keywords:
        keywords_path = Path(args.keywords)
    else:
        keywords_path = keyword_by_source.get(source, Path("config/keywords.yml"))

    if not keywords_path.exists():
        logger.warning("Keyword file not found: %s, fallback to config/keywords.yml", keywords_path)
        keywords_path = Path("config/keywords.yml")

    logger.info("Loading keywords from %s", keywords_path)
    include_keywords, exclude_keywords = load_keywords(keywords_path)
    logger.info("Keywords file in use: %s", keywords_path)
    logger.info("Keywords loaded: include=%d exclude=%d", len(include_keywords), len(exclude_keywords))

    if source not in {"top3", "nber", "all"}:
        logger.warning("Unknown source=%s, fallback to all", args.source)
        source = "all"

    run_top3 = source in {"top3", "all"}
    run_nber = source in {"nber", "all"}

    top_journal_papers: List[Dict] = []
    nber_papers: List[Dict] = []

    if run_top3:
        logger.info("Fetching top journals from Crossref (from %s)", args.from_date)
        top_journal_papers = fetch_all_journals(from_date=args.from_date)
    logger.info("top3 fetched count: %d", len(top_journal_papers))

    if run_nber:
        logger.info("Fetching NBER metadata")
        try:
            nber_papers = fetch_nber_working_papers(
                cache_dir=args.nber_cache_dir,
                max_cache_age_days=args.nber_cache_days,
            )
        except Exception as exc:
            logger.error("Failed to fetch NBER metadata: %s", exc)
            nber_papers = []
    logger.info("nber fetched count: %d", len(nber_papers))
    for idx, paper in enumerate(nber_papers[:3], start=1):
        logger.info("NBER sample #%d: title=%s | id=%s", idx, paper.get("title", ""), paper.get("id", ""))

    selected_all: List[Dict] = []

    def process_source(source_name: str, papers: List[Dict], state_file: str) -> List[Dict]:
        logger.info("Using state file (%s): %s", source_name, state_file)
        state = RecordState(state_file)
        pushed_ids = state.load()
        logger.info("Loaded %d pushed IDs for %s", len(pushed_ids), source_name)

        filtered = filter_papers(
            papers=papers,
            include_keywords=include_keywords,
            exclude_keywords=exclude_keywords,
            min_date=args.from_date,
        )

        deduped: List[Dict] = []
        seen_ids = set()
        for paper in filtered:
            pid = (paper.get("id") or "").strip()
            if not pid or pid in seen_ids:
                continue
            seen_ids.add(pid)
            deduped.append(paper)

        unseen = [p for p in deduped if p["id"] not in pushed_ids]
        selected = unseen[: max(0, args.limit)]
        logger.info("selected count (%s): %d (unseen=%d)", source_name, len(selected), len(unseen))
        state.add_many([p.get("id", "") for p in selected])
        logger.info("State saved: %s", state_file)
        return selected

    if run_top3:
        selected_all.extend(process_source("top3", top_journal_papers, STATE_TOP3))
    if run_nber:
        selected_all.extend(process_source("nber", nber_papers, STATE_NBER))

    for p in selected_all:
        payload = {
            "id": p.get("id", ""),
            "source": p.get("source", ""),
            "title": p.get("title", ""),
            "authors": p.get("authors", []),
            "journal": p.get("journal", ""),
            "published": p.get("published", "") or p.get("year", ""),
            "DOI": p.get("DOI", ""),
            "abstract": p.get("abstract", "") or "",
            "url": p.get("url", ""),
        }
        print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
