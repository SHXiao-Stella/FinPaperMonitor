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

from paper_monitor.fetch import fetch_all_econ5_journals, fetch_all_journals
from paper_monitor.fetch_nber import fetch_nber_working_papers
from paper_monitor.filtering import filter_papers, load_keywords
from paper_monitor.state import RecordState

STATE_TOP3 = "data/pushed_ids_top3.json"
STATE_ECON5 = "data/pushed_ids_econ5.json"
STATE_NBER = "data/pushed_ids_nber.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Monitor top journals, economics top5, and NBER papers")
    parser.add_argument("--source", default="all", help="Data source selector: top3 | econ5 | nber | all")
    parser.add_argument("--limit", type=int, default=5, help="Maximum number of papers to output")
    parser.add_argument("--from-date", default="2020-01-01", help="Lower bound publication date (YYYY-MM-DD)")
    parser.add_argument("--keywords", default=None, help="Optional keyword config override")
    parser.add_argument("--nber-cache-dir", default="data/cache/nber", help="NBER local cache directory")
    parser.add_argument("--nber-cache-days", type=int, default=7, help="NBER cache max age in days")

    parser.add_argument(
        "--selected-out",
        default=None,
        help="Optional JSONL file path to save selected records for downstream delivery/archive/commit",
    )
    parser.add_argument(
        "--commit-state",
        action="store_true",
        help="Commit selected IDs to pushed state after this run finishes",
    )
    parser.add_argument(
        "--commit-from-file",
        default=None,
        help="Read a previously saved JSONL file and commit its IDs to pushed state without refetching",
    )
    return parser.parse_args()


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


def payload_from_paper(p: Dict) -> Dict:
    raw_source = str(p.get("source", "")).strip().lower()
    source_alias = {
        "top_journal": "top3",
        "top3": "top3",
        "econ5_journal": "econ5",
        "econ5": "econ5",
        "nber": "nber",
    }
    normalized_source = source_alias.get(raw_source, raw_source)

    return {
        "id": p.get("id", ""),
        "source": normalized_source,
        "title": p.get("title", ""),
        "authors": p.get("authors", []),
        "journal": p.get("journal", ""),
        "published": p.get("published", "") or p.get("year", ""),
        "DOI": p.get("DOI", ""),
        "abstract": p.get("abstract", "") or "",
        "url": p.get("url", ""),
    }


def save_jsonl(path: Path, payloads: List[Dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for item in payloads:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


def load_jsonl(path: Path) -> List[Dict]:
    if not path.exists():
        raise FileNotFoundError(f"JSONL file not found: {path}")

    items: List[Dict] = []
    with path.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            s = line.strip()
            if not s:
                continue
            try:
                obj = json.loads(s)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"invalid JSON on line {lineno} in {path}: {exc}") from exc
            if isinstance(obj, dict):
                items.append(obj)
    return items


def commit_payloads(payloads: List[Dict], logger: logging.Logger) -> None:
    grouped = {
        "top3": [],
        "econ5": [],
        "nber": [],
    }

    source_alias = {
        "top_journal": "top3",
        "top3": "top3",
        "econ5_journal": "econ5",
        "econ5": "econ5",
        "nber": "nber",
    }

    for item in payloads:
        raw_src = str(item.get("source", "")).strip().lower()
        src = source_alias.get(raw_src, raw_src)
        pid = str(item.get("id", "")).strip()
        if src in grouped and pid:
            grouped[src].append(pid)

    for src, ids in grouped.items():
        if not ids:
            continue

        state_file = {
            "top3": STATE_TOP3,
            "econ5": STATE_ECON5,
            "nber": STATE_NBER,
        }[src]
        state = RecordState(state_file)
        state.add_many(ids)
        logger.info("State saved: %s (+%d ids)", state_file, len(set(ids)))


def filter_payloads_by_source(payloads: List[Dict], source: str) -> List[Dict]:
    source = str(source or "").strip().lower()
    if source == "all":
        return payloads
    return [p for p in payloads if str(p.get("source", "")).strip().lower() == source]


def main() -> int:
    setup_logging()
    args = parse_args()
    logger = logging.getLogger("run_monitor")

    source = str(args.source or "").strip().lower()
    if source not in {"top3", "econ5", "nber", "all"}:
        logger.warning("Unknown source=%s, fallback to all", args.source)
        source = "all"

    if args.commit_from_file:
        payloads = load_jsonl(Path(args.commit_from_file).expanduser())
        payloads = filter_payloads_by_source(payloads, source)
        logger.info("Loaded %d payloads from %s for commit", len(payloads), args.commit_from_file)
        commit_payloads(payloads, logger)
        return 0

    keyword_by_source = {
        "top3": Path("config/keywords_top3.yml"),
        "econ5": Path("config/keywords_econ5.yml"),
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

    run_top3 = source in {"top3", "all"}
    run_econ5 = source in {"econ5", "all"}
    run_nber = source in {"nber", "all"}

    top_journal_papers: List[Dict] = []
    econ5_papers: List[Dict] = []
    nber_papers: List[Dict] = []

    if run_top3:
        logger.info("Fetching top journals from Crossref (from %s)", args.from_date)
        top_journal_papers = fetch_all_journals(from_date=args.from_date)
    logger.info("top3 fetched count: %d", len(top_journal_papers))

    if run_econ5:
        logger.info("Fetching economics top5 journals from Crossref (from %s)", args.from_date)
        econ5_papers = fetch_all_econ5_journals(from_date=args.from_date)
    logger.info("econ5 fetched count: %d", len(econ5_papers))

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
        return selected

    if run_top3:
        selected_all.extend(process_source("top3", top_journal_papers, STATE_TOP3))
    if run_econ5:
        selected_all.extend(process_source("econ5", econ5_papers, STATE_ECON5))
    if run_nber:
        selected_all.extend(process_source("nber", nber_papers, STATE_NBER))

    payloads = [payload_from_paper(p) for p in selected_all]

    if args.selected_out:
        out_path = Path(args.selected_out).expanduser()
        save_jsonl(out_path, payloads)
        logger.info("Selected payloads written to %s", out_path)

    for payload in payloads:
        print(json.dumps(payload, ensure_ascii=False))

    if args.commit_state:
        commit_payloads(payloads, logger)
    else:
        logger.info("Dry run only: pushed state NOT updated")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
