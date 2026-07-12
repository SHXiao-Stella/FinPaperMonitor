from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List

from ..models import Paper
from ..normalize import finalize_paper, stable_paper_id
from .common import (
    attach_query_group_metadata,
    boolean_query_to_free_text,
    build_session,
    filter_recent_with_fallback,
    query_groups_for_source,
    strip_jats,
)


CROSSREF_WORKS_URL = "https://api.crossref.org/works"
SSRN_PREFIX = "10.2139"


def _pick_primary_date(item: Dict[str, object]) -> str | None:
    for key in ("posted", "published-online", "published-print", "published", "issued", "created"):
        payload = item.get(key)
        if isinstance(payload, dict):
            date_parts = payload.get("date-parts")
            if isinstance(date_parts, list) and date_parts and isinstance(date_parts[0], list):
                parts = date_parts[0]
                if len(parts) >= 3:
                    year, month, day = parts[:3]
                    return f"{year:04d}-{month:02d}-{day:02d}"
    return None


def _pick_recency_date(item: Dict[str, object]) -> str | None:
    for key in ("deposited", "indexed", "posted", "published-online", "published-print", "created", "issued"):
        payload = item.get(key)
        if isinstance(payload, dict):
            date_parts = payload.get("date-parts")
            if isinstance(date_parts, list) and date_parts and isinstance(date_parts[0], list):
                parts = date_parts[0]
                if len(parts) >= 3:
                    year, month, day = parts[:3]
                    return f"{year:04d}-{month:02d}-{day:02d}"
    return None


def _is_ssrn_record(item: Dict[str, object]) -> bool:
    container_title = " ".join(item.get("container-title") or [])
    short_title = " ".join(item.get("short-container-title") or [])
    group_title = str(item.get("group-title") or "")
    resource_url = ((item.get("resource") or {}).get("primary") or {}).get("URL", "")
    doi = str(item.get("DOI") or "")
    return any(
        [
            "SSRN" in container_title,
            "SSRN" in short_title,
            group_title == "SSRN",
            "ssrn.com" in resource_url.lower(),
            doi.lower().startswith("10.2139/ssrn."),
        ]
    )


def _item_to_paper(item: Dict[str, object], source_name: str, query_group: str, raw_query: str) -> Paper:
    title = (item.get("title") or [""])[0]
    authors = [
        " ".join(part for part in [author.get("given"), author.get("family")] if part)
        for author in (item.get("author") or [])
        if isinstance(author, dict)
    ]
    resource = (item.get("resource") or {}).get("primary") or {}
    link = resource.get("URL") or str(item.get("URL") or "")
    doi = str(item.get("DOI") or "")
    abstract = strip_jats(item.get("abstract"))
    source_id = doi.rsplit(".", 1)[-1] if doi else title

    paper = Paper(
        title=title,
        authors=authors,
        abstract=abstract,
        source="SSRN",
        source_category="ssrn_preprint",
        date=_pick_primary_date(item),
        link=link,
        paper_id=stable_paper_id("ssrn", source_id, title),
        raw_metadata={
            "doi": doi,
            "crossref_item": item,
            "recency_date": _pick_recency_date(item),
        },
    )
    paper = finalize_paper(paper)
    attach_query_group_metadata(paper, query_group, raw_query, source_name)
    return paper


def _crossref_request_variants(from_date: str) -> List[Dict[str, str]]:
    return [
        {
            "sort": "deposited",
            "order": "desc",
            "filter": f"prefix:{SSRN_PREFIX},from-deposit-date:{from_date}",
        },
        {
            "sort": "created",
            "order": "desc",
            "filter": f"prefix:{SSRN_PREFIX},from-created-date:{from_date}",
        },
    ]


def fetch_ssrn_candidates(task_config: Dict[str, object], logger) -> List[Paper]:
    session = build_session()
    source_name = "ssrn"
    source_cfg = (task_config.get("source_settings") or {}).get(source_name, {})
    max_results_per_query = int(source_cfg.get("max_results_per_query", 20))
    query_groups = query_groups_for_source(task_config, source_name)
    lookback_days = int(source_cfg.get("lookback_days", 7))
    fallback_days = int(source_cfg.get("fallback_lookback_days", max(lookback_days, 14)))
    fallback_min_candidates = int(source_cfg.get("fallback_min_candidates", 5))
    fetch_lookback_days = int(source_cfg.get("fetch_lookback_days", max(fallback_days, lookback_days)))
    timeout_seconds = int(source_cfg.get("timeout_seconds", 25))
    drop_missing_date = bool(source_cfg.get("drop_missing_date", True))
    from_created_date = (datetime.now().date() - timedelta(days=fetch_lookback_days)).isoformat()

    papers: List[Paper] = []
    seen: Dict[str, Paper] = {}
    for group in query_groups:
        group_name = str(group["name"])
        for raw_query in group["queries"]:
            free_text_query = boolean_query_to_free_text(raw_query)
            items = None
            last_error = None
            for variant in _crossref_request_variants(from_created_date):
                try:
                    response = session.get(
                        CROSSREF_WORKS_URL,
                        params={
                            "query.bibliographic": free_text_query,
                            "rows": max_results_per_query,
                            **variant,
                        },
                        timeout=timeout_seconds,
                        headers={"User-Agent": "paper-monitor/0.1 (mailto:none@example.com)"},
                    )
                    response.raise_for_status()
                    items = response.json().get("message", {}).get("items", [])
                    break
                except Exception as exc:
                    last_error = exc
            if items is None:
                logger.warning("SSRN Crossref query failed for %s (%s): %s", group_name, raw_query, last_error)
                continue

            for item in items:
                if not isinstance(item, dict) or not _is_ssrn_record(item):
                    continue
                paper = _item_to_paper(item, source_name, group_name, raw_query)
                existing = seen.get(paper.paper_id)
                if existing:
                    attach_query_group_metadata(existing, group_name, raw_query, source_name)
                    continue
                seen[paper.paper_id] = paper
                papers.append(paper)

    papers.sort(
        key=lambda item: (
            str(item.raw_metadata.get("recency_date") or item.date or ""),
            item.paper_id,
        ),
        reverse=True,
    )
    return filter_recent_with_fallback(
        papers,
        lookback_days=lookback_days,
        fallback_lookback_days=fallback_days,
        fallback_min_candidates=fallback_min_candidates,
        drop_missing_date=drop_missing_date,
        date_getter=lambda paper: str(paper.raw_metadata.get("recency_date") or paper.date or ""),
    )
