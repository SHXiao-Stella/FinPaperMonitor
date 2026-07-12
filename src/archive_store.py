from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .models import RankedPaper, TranslationResult
from .text_policy import sanitize_note_text
from .utils import atomic_write_json, ensure_dir, load_json, utc_now_iso


class TaskArchiveStore:
    def __init__(self, task_name: str, display_name: str, archive_path: Path, logger):
        self.task_name = task_name
        self.display_name = display_name
        self.archive_path = archive_path
        self.logger = logger
        self.state = self._load()

    def _empty_state(self) -> Dict[str, object]:
        return {
            "schema_version": 1,
            "task_name": self.task_name,
            "display_name": self.display_name,
            "updated_at": None,
            "records": [],
        }

    def _load(self) -> Dict[str, object]:
        loaded = load_json(self.archive_path, default=self._empty_state())
        if "records" not in loaded or not isinstance(loaded["records"], list):
            loaded["records"] = []
        loaded.setdefault("schema_version", 1)
        loaded.setdefault("task_name", self.task_name)
        loaded.setdefault("display_name", self.display_name)
        loaded.setdefault("updated_at", None)
        return loaded

    @staticmethod
    def _match_record(records: List[Dict[str, object]], ranked_paper: RankedPaper) -> Optional[Dict[str, object]]:
        paper = ranked_paper.paper
        for record in records:
            if record.get("paper_id") and record.get("paper_id") == paper.paper_id:
                return record
            if record.get("canonical_url") and record.get("canonical_url") == paper.canonical_url:
                return record
            if record.get("normalized_title") and record.get("normalized_title") == paper.normalized_title:
                return record
        return None

    @staticmethod
    def _sorted_records(records: List[Dict[str, object]]) -> List[Dict[str, object]]:
        return sorted(
            records,
            key=lambda item: (
                str(item.get("issue_date") or ""),
                str(item.get("first_seen_at") or ""),
                -int(item.get("issue_rank") or 0),
            ),
            reverse=True,
        )

    def build_updated_state(
        self,
        issue_date: str,
        ranked_papers: List[RankedPaper],
        translations: Dict[str, TranslationResult],
    ) -> tuple[Dict[str, object], bool]:
        state = deepcopy(self.state)
        records = state["records"]
        changed = False
        now_iso = utc_now_iso()

        for index, ranked in enumerate(ranked_papers, start=1):
            paper = ranked.paper
            translation = translations[paper.paper_id]
            matched_groups = sorted(ranked.matched_groups.keys()) if ranked.matched_groups else []
            note_text = str(translation.worth_reading_note_zh or "").strip()
            if not note_text and ranked.why_relevant:
                note_text = str(ranked.why_relevant).strip()
            note_text = sanitize_note_text(note_text)
            payload = {
                "paper_id": paper.paper_id,
                "title": paper.title,
                "authors": list(paper.authors),
                "source": paper.source,
                "source_category": paper.source_category,
                "paper_date": paper.date,
                "link": paper.link,
                "canonical_url": paper.canonical_url,
                "normalized_title": paper.normalized_title,
                "abstract": paper.abstract,
                "translated_abstract_zh": translation.translated_abstract_zh.strip(),
                "note_zh": note_text,
                "matched_groups": matched_groups,
                "issue_rank": index,
                "last_seen_at": now_iso,
                "last_seen_issue_date": issue_date,
            }
            existing = self._match_record(records, ranked)
            if existing:
                existing.update(payload)
                existing.setdefault("first_seen_at", now_iso)
                existing.setdefault("issue_date", issue_date)
                changed = True
                continue

            payload["first_seen_at"] = now_iso
            payload["issue_date"] = issue_date
            records.append(payload)
            changed = True

        state["records"] = self._sorted_records(records)
        if changed:
            state["updated_at"] = now_iso
        return state, changed

    def persist(self, state: Dict[str, object]) -> Path:
        self.logger.info("Persist archive to %s", self.archive_path)
        atomic_write_json(self.archive_path, state)
        self.state = state
        return self.archive_path

    def write_preview(self, state: Dict[str, object], preview_path: Path) -> Path:
        ensure_dir(preview_path.parent)
        atomic_write_json(preview_path, state)
        return preview_path

    def write_rendered_markdown(self, content: str, path: Path) -> Path:
        ensure_dir(path.parent)
        path.write_text(content, encoding="utf-8")
        return path

    @staticmethod
    def issue_date_today() -> str:
        return datetime.now().date().isoformat()
