from __future__ import annotations

import difflib
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .models import Paper
from .normalize import canonicalize_url, normalize_title
from .utils import atomic_write_json, load_json, utc_now_iso

try:
    from rapidfuzz import fuzz
except Exception:  # pragma: no cover
    fuzz = None


def _title_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if fuzz:
        return float(fuzz.ratio(left, right))
    return difflib.SequenceMatcher(a=left, b=right).ratio() * 100.0


class PushedStateStore:
    def __init__(self, global_state_path: Path, task_state_path: Path):
        self.global_state_path = global_state_path
        self.task_state_path = task_state_path
        self.global_state = load_json(global_state_path, default=self._empty_state())
        self.task_state = load_json(task_state_path, default=self._empty_state())

    @staticmethod
    def _empty_state() -> Dict[str, object]:
        return {
            "schema_version": 1,
            "updated_at": None,
            "paper_ids": {},
            "canonical_urls": {},
            "normalized_titles": {},
            "history": [],
        }

    def _match_state(self, state: Dict[str, object], paper: Paper) -> Optional[Tuple[str, str]]:
        paper_id = paper.paper_id
        title_key = normalize_title(paper.title)
        url_key = canonicalize_url(paper.link) or ""

        if paper_id and paper_id in state["paper_ids"]:
            return "paper_id", paper_id
        if url_key and url_key in state["canonical_urls"]:
            return "canonical_url", url_key
        if title_key and title_key in state["normalized_titles"]:
            return "normalized_title", title_key

        existing_titles = state.get("normalized_titles", {})
        for existing in existing_titles.keys():
            if _title_similarity(title_key, existing) >= 96.0:
                return "title_similarity", existing
        return None

    def was_pushed_globally(self, paper: Paper) -> Optional[Tuple[str, str]]:
        return self._match_state(self.global_state, paper)

    def was_pushed_for_task(self, paper: Paper) -> Optional[Tuple[str, str]]:
        return self._match_state(self.task_state, paper)

    def mark_pushed(self, paper: Paper, task_name: str) -> None:
        record = {
            "paper_id": paper.paper_id,
            "task_name": task_name,
            "title": paper.title,
            "source": paper.source,
            "date": paper.date,
            "link": paper.link,
            "pushed_at": utc_now_iso(),
        }
        for state in (self.global_state, self.task_state):
            state["paper_ids"][paper.paper_id] = record
            if paper.canonical_url:
                state["canonical_urls"][paper.canonical_url] = record
            if paper.normalized_title:
                state["normalized_titles"][paper.normalized_title] = record
            state["history"].append(record)
            state["updated_at"] = record["pushed_at"]

    def save(self) -> None:
        atomic_write_json(self.global_state_path, self.global_state)
        atomic_write_json(self.task_state_path, self.task_state)


def dedupe_candidates(papers: List[Paper]) -> List[Paper]:
    seen_ids = set()
    seen_urls = set()
    kept: List[Paper] = []
    seen_titles: List[str] = []

    for paper in papers:
        title_key = normalize_title(paper.title)
        url_key = canonicalize_url(paper.link) or ""

        duplicate = False
        if paper.paper_id in seen_ids:
            duplicate = True
        elif url_key and url_key in seen_urls:
            duplicate = True
        elif title_key and any(_title_similarity(title_key, prior) >= 96.0 for prior in seen_titles):
            duplicate = True

        if duplicate:
            continue

        seen_ids.add(paper.paper_id)
        if url_key:
            seen_urls.add(url_key)
        if title_key:
            seen_titles.append(title_key)
        kept.append(paper)

    return kept
