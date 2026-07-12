from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Paper:
    title: str
    authors: List[str]
    abstract: Optional[str]
    source: str
    source_category: str
    date: Optional[str]
    link: str
    paper_id: str
    raw_metadata: Dict[str, Any] = field(default_factory=dict)
    canonical_url: Optional[str] = None
    normalized_title: Optional[str] = None

    def combined_text(self) -> str:
        parts = [self.title or "", self.abstract or ""]
        keywords = self.raw_metadata.get("keywords")
        if isinstance(keywords, list):
            parts.append(" ".join(str(item) for item in keywords))
        return "\n".join(part for part in parts if part).strip()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FilterDecision:
    matched: bool
    score: float
    matched_groups: Dict[str, List[str]] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    bucket: Optional[str] = None
    bucket_scores: Dict[str, float] = field(default_factory=dict)


@dataclass
class RankedPaper:
    paper: Paper
    rule_score: float
    heuristic_score: float
    llm_relevance_score: float = 0.0
    llm_top_pick_score: float = 0.0
    final_score: float = 0.0
    llm_is_relevant: Optional[bool] = None
    matched_groups: Dict[str, List[str]] = field(default_factory=dict)
    why_relevant: Optional[str] = None
    bucket: Optional[str] = None
    query_groups: List[str] = field(default_factory=list)


@dataclass
class TranslationResult:
    paper_id: str
    abstract_missing: bool
    translated_abstract_zh: str
    worth_reading_note_zh: str


@dataclass
class PipelineRunResult:
    task_name: str
    selected: List[RankedPaper]
    message_markdown: str
    output_path: str
    dry_run: bool
    sent: bool
    state_updated: bool
    archive_path: Optional[str] = None
    doc_output_path: Optional[str] = None
    audit_path: Optional[str] = None
    docid: Optional[str] = None
    doc_written: bool = False
    archive_updated: bool = False
    success: bool = True
    error: Optional[str] = None
    stats: Dict[str, Any] = field(default_factory=dict)
