from __future__ import annotations

from typing import Dict, List
from xml.etree import ElementTree

from ..models import Paper
from ..normalize import finalize_paper, stable_paper_id
from ..utils import compact_whitespace
from .common import (
    attach_query_group_metadata,
    boolean_query_to_arxiv,
    build_session,
    filter_recent_with_fallback,
    query_groups_for_source,
)


ARXIV_API_URL = "https://export.arxiv.org/api/query"
ATOM_NS = {
    "atom": "http://www.w3.org/2005/Atom",
}


def _entry_text(entry, field: str) -> str:
    return compact_whitespace(entry.findtext(f"atom:{field}", default="", namespaces=ATOM_NS))


def _pick_html_link(entry) -> str:
    for node in entry.findall("atom:link", ATOM_NS):
        href = str(node.get("href") or "").strip()
        rel = str(node.get("rel") or "").strip()
        mime = str(node.get("type") or "").strip()
        if href and (not rel or rel == "alternate") and (not mime or mime == "text/html"):
            return href
    return _entry_text(entry, "id")


def _entry_to_paper(entry, source_name: str, query_group: str, raw_query: str) -> Paper:
    title = _entry_text(entry, "title")
    abstract = _entry_text(entry, "summary")
    authors = [compact_whitespace(node.text or "") for node in entry.findall("atom:author/atom:name", ATOM_NS)]
    link = _pick_html_link(entry)
    arxiv_id = _entry_text(entry, "id").rstrip("/").rsplit("/", 1)[-1]
    published = _entry_text(entry, "published")
    updated = _entry_text(entry, "updated")

    paper = finalize_paper(
        Paper(
            title=title,
            authors=authors,
            abstract=abstract or None,
            source="arXiv",
            source_category="arxiv_preprint",
            date=(updated or published or "")[:10] or None,
            link=link,
            paper_id=stable_paper_id("arxiv", arxiv_id, title),
            raw_metadata={
                "arxiv_id": arxiv_id,
                "published": published,
                "updated": updated,
            },
        )
    )
    attach_query_group_metadata(paper, query_group, raw_query, source_name)
    return paper


def fetch_arxiv_candidates(task_config: Dict[str, object], logger) -> List[Paper]:
    session = build_session()
    source_name = "arxiv"
    source_cfg = (task_config.get("source_settings") or {}).get(source_name, {})
    query_groups = query_groups_for_source(task_config, source_name)
    lookback_days = int(source_cfg.get("lookback_days", 3))
    fallback_days = int(source_cfg.get("fallback_lookback_days", max(lookback_days, 7)))
    fallback_min_candidates = int(source_cfg.get("fallback_min_candidates", 6))
    max_results_per_query = int(source_cfg.get("max_results_per_query", 20))
    timeout_seconds = int(source_cfg.get("timeout_seconds", 25))
    drop_missing_date = bool(source_cfg.get("drop_missing_date", True))

    papers: List[Paper] = []
    seen: Dict[str, Paper] = {}
    rate_limited = False
    for group in query_groups:
        if rate_limited:
            break
        group_name = str(group["name"])
        for raw_query in group["queries"]:
            query_text = boolean_query_to_arxiv(raw_query)
            try:
                response = session.get(
                    ARXIV_API_URL,
                    params={
                        "search_query": query_text,
                        "start": 0,
                        "max_results": max_results_per_query,
                        "sortBy": "submittedDate",
                        "sortOrder": "descending",
                    },
                    timeout=timeout_seconds,
                )
                response.raise_for_status()
                root = ElementTree.fromstring(response.text)
            except Exception as exc:
                logger.warning("arXiv query failed for %s (%s): %s", group_name, raw_query, exc)
                if "429" in str(exc) or "too many" in str(exc).lower():
                    rate_limited = True
                continue

            for entry in root.findall("atom:entry", ATOM_NS):
                paper = _entry_to_paper(entry, source_name, group_name, raw_query)
                existing = seen.get(paper.paper_id)
                if existing:
                    attach_query_group_metadata(existing, group_name, raw_query, source_name)
                    continue
                seen[paper.paper_id] = paper
                papers.append(paper)

    papers.sort(key=lambda item: (str(item.raw_metadata.get("updated") or item.date or ""), item.paper_id), reverse=True)
    return filter_recent_with_fallback(
        papers,
        lookback_days=lookback_days,
        fallback_lookback_days=fallback_days,
        fallback_min_candidates=fallback_min_candidates,
        drop_missing_date=drop_missing_date,
        date_getter=lambda paper: str(paper.raw_metadata.get("updated") or paper.date or ""),
    )
