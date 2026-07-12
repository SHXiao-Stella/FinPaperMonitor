from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional

from ..models import Paper
from ..normalize import finalize_paper, stable_paper_id
from ..utils import compact_whitespace, env_first
from .common import (
    attach_query_group_metadata,
    boolean_query_to_free_text,
    build_session,
    filter_recent_with_fallback,
    query_groups_for_source,
)


SEMANTIC_SCHOLAR_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
SEMANTIC_SCHOLAR_FIELDS = ",".join(
    [
        "paperId",
        "title",
        "abstract",
        "authors",
        "url",
        "publicationDate",
        "year",
        "venue",
        "publicationTypes",
        "externalIds",
    ]
)


def _request_semantic_scholar(session, logger, query_text: str, limit: int, from_date: str, to_date: str, timeout_seconds: int):
    headers = {}
    api_key = env_first("SEMANTIC_SCHOLAR_API_KEY", "S2_API_KEY")
    if api_key:
        headers["x-api-key"] = api_key

    attempts = [
        {
            "query": query_text,
            "limit": limit,
            "fields": SEMANTIC_SCHOLAR_FIELDS,
            "sort": "publicationDate:desc",
            "publicationDateOrYear": f"{from_date}:{to_date}",
        },
        {
            "query": query_text,
            "limit": limit,
            "fields": SEMANTIC_SCHOLAR_FIELDS,
        },
    ]
    last_error: Optional[Exception] = None
    for params in attempts:
        try:
            response = session.get(SEMANTIC_SCHOLAR_SEARCH_URL, params=params, headers=headers, timeout=timeout_seconds)
            if response.status_code == 429:
                raise RuntimeError("Semantic Scholar rate limited (429)")
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict):
                return payload.get("data", [])
        except Exception as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    return []


def _item_to_paper(item: Dict[str, object], source_name: str, query_group: str, raw_query: str) -> Optional[Paper]:
    title = compact_whitespace(str(item.get("title") or ""))
    if not title:
        return None

    authors = []
    for author in item.get("authors") or []:
        if not isinstance(author, dict):
            continue
        name = compact_whitespace(str(author.get("name") or ""))
        if name:
            authors.append(name)

    publication_date = compact_whitespace(str(item.get("publicationDate") or ""))
    year = str(item.get("year") or "").strip()
    url = compact_whitespace(str(item.get("url") or ""))
    external_ids = item.get("externalIds") or {}
    paper_id = str(item.get("paperId") or "").strip()
    fallback_id = str(
        (external_ids.get("DOI") if isinstance(external_ids, dict) else None)
        or (external_ids.get("ArXiv") if isinstance(external_ids, dict) else None)
        or paper_id
        or title
    ).strip()
    paper = finalize_paper(
        Paper(
            title=title,
            authors=authors,
            abstract=compact_whitespace(str(item.get("abstract") or "")) or None,
            source="Semantic Scholar",
            source_category="semanticscholar_paper",
            date=publication_date or None,
            link=url or (f"https://www.semanticscholar.org/paper/{paper_id}" if paper_id else ""),
            paper_id=stable_paper_id("semanticscholar", fallback_id, title),
            raw_metadata={
                "paperId": paper_id,
                "publication_date": publication_date,
                "year": year,
                "venue": compact_whitespace(str(item.get("venue") or "")),
                "external_ids": external_ids if isinstance(external_ids, dict) else {},
                "publication_types": item.get("publicationTypes") or [],
            },
        )
    )
    attach_query_group_metadata(paper, query_group, raw_query, source_name)
    return paper


def fetch_semanticscholar_candidates(task_config: Dict[str, object], logger) -> List[Paper]:
    session = build_session()
    source_name = "semanticscholar"
    source_cfg = (task_config.get("source_settings") or {}).get(source_name, {})
    query_groups = query_groups_for_source(task_config, source_name)
    lookback_days = int(source_cfg.get("lookback_days", 3))
    fallback_days = int(source_cfg.get("fallback_lookback_days", max(lookback_days, 7)))
    fallback_min_candidates = int(source_cfg.get("fallback_min_candidates", 6))
    max_results_per_query = int(source_cfg.get("max_results_per_query", 20))
    timeout_seconds = int(source_cfg.get("timeout_seconds", 20))
    drop_missing_date = bool(source_cfg.get("drop_missing_date", True))

    until = datetime.now().date()
    from_date = (until - timedelta(days=fallback_days)).isoformat()
    to_date = until.isoformat()

    papers: List[Paper] = []
    seen: Dict[str, Paper] = {}
    rate_limited = False
    for group in query_groups:
        if rate_limited:
            break
        group_name = str(group["name"])
        for raw_query in group["queries"]:
            query_text = boolean_query_to_free_text(raw_query)
            try:
                items = _request_semantic_scholar(
                    session=session,
                    logger=logger,
                    query_text=query_text,
                    limit=max_results_per_query,
                    from_date=from_date,
                    to_date=to_date,
                    timeout_seconds=timeout_seconds,
                )
            except Exception as exc:
                logger.warning("Semantic Scholar query failed for %s (%s): %s", group_name, raw_query, exc)
                if "429" in str(exc) or "rate limited" in str(exc).lower():
                    rate_limited = True
                continue

            for item in items:
                if not isinstance(item, dict):
                    continue
                paper = _item_to_paper(item, source_name, group_name, raw_query)
                if not paper:
                    continue
                existing = seen.get(paper.paper_id)
                if existing:
                    attach_query_group_metadata(existing, group_name, raw_query, source_name)
                    continue
                seen[paper.paper_id] = paper
                papers.append(paper)

    papers.sort(key=lambda item: (str(item.raw_metadata.get("publication_date") or item.date or ""), item.paper_id), reverse=True)
    return filter_recent_with_fallback(
        papers,
        lookback_days=lookback_days,
        fallback_lookback_days=fallback_days,
        fallback_min_candidates=fallback_min_candidates,
        drop_missing_date=drop_missing_date,
        date_getter=lambda paper: str(paper.raw_metadata.get("publication_date") or paper.date or ""),
    )
