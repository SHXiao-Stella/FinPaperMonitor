from __future__ import annotations

from typing import Dict, List

from ..models import Paper
from ..normalize import finalize_paper, stable_paper_id
from .common import build_session, strip_jats


CROSSREF_WORKS_URL = "https://api.crossref.org/works"
CROSSREF_JOURNALS = {
    "jf": {"label": "JF", "issn": "1540-6261"},
    "jfe": {"label": "JFE", "issn": "0304-405X"},
    "rfs": {"label": "RFS", "issn": "0893-9454"},
}


def _pick_date(item: Dict[str, object]) -> str | None:
    for key in ("published-online", "published-print", "published", "issued", "created"):
        payload = item.get(key)
        if isinstance(payload, dict):
            date_parts = payload.get("date-parts")
            if isinstance(date_parts, list) and date_parts and isinstance(date_parts[0], list):
                parts = date_parts[0]
                if parts:
                    year = int(parts[0])
                    month = int(parts[1]) if len(parts) >= 2 else 1
                    day = int(parts[2]) if len(parts) >= 3 else 1
                    return f"{year:04d}-{month:02d}-{day:02d}"
    return None


def _crossref_item_to_paper(item: Dict[str, object], journal_code: str) -> Paper:
    journal = CROSSREF_JOURNALS[journal_code]
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
    source_id = doi or title

    paper = Paper(
        title=title,
        authors=authors,
        abstract=abstract,
        source=journal["label"],
        source_category="top3_journal_article",
        date=_pick_date(item),
        link=link,
        paper_id=stable_paper_id(journal["label"].lower(), source_id, title),
        raw_metadata={"doi": doi, "journal_code": journal_code, "crossref_item": item},
    )
    return finalize_paper(paper)


def fetch_top3_candidates(task_config: Dict[str, object], logger) -> List[Paper]:
    session = build_session()
    source_cfg = (task_config.get("source_settings") or {}).get("top3", {})
    journals = list(source_cfg.get("journals") or ["jf", "jfe", "rfs"])
    min_date = str(source_cfg.get("min_date") or task_config.get("min_date") or "").strip()
    rows_per_page = int(source_cfg.get("rows_per_page", 100))
    max_pages_per_journal = int(source_cfg.get("max_pages_per_journal", 20))
    max_articles_per_journal = int(
        source_cfg.get("max_articles_per_journal", rows_per_page * max_pages_per_journal)
    )

    papers: List[Paper] = []
    for journal_code in journals:
        journal_items: List[Dict[str, object]] = []
        cursor = "*"
        seen_dois = set()
        try:
            issn = CROSSREF_JOURNALS[journal_code]["issn"]
        except Exception as exc:
            logger.warning("Top3 list fetch failed for %s: %s", journal_code, exc)
            continue

        for page in range(max_pages_per_journal):
            remaining = max_articles_per_journal - len(journal_items)
            if remaining <= 0:
                break
            params = {
                "filter": ",".join(
                    [part for part in [f"issn:{issn}", f"from-pub-date:{min_date}" if min_date else ""] if part]
                ),
                "rows": min(rows_per_page, remaining),
                "sort": "published",
                "order": "desc",
                "cursor": cursor,
                "select": (
                    "DOI,title,author,container-title,published-print,published-online,"
                    "published,issued,created,resource,URL,abstract,type"
                ),
            }
            try:
                response = session.get(
                    CROSSREF_WORKS_URL,
                    params=params,
                    timeout=30,
                    headers={"User-Agent": "paper-monitor/0.1 (mailto:none@example.com)"},
                )
                response.raise_for_status()
                message = response.json().get("message", {})
                items = message.get("items", [])
            except Exception as exc:
                logger.warning("Top3 list fetch failed for %s page %s: %s", journal_code, page + 1, exc)
                break

            if not items:
                break

            for item in items:
                if str(item.get("type") or "").strip() not in {"", "journal-article"}:
                    continue
                doi = str(item.get("DOI") or "").strip().lower()
                dedupe_key = doi or str(item.get("URL") or "") or str(item.get("title") or "")
                if dedupe_key in seen_dois:
                    continue
                seen_dois.add(dedupe_key)
                journal_items.append(item)
                if len(journal_items) >= max_articles_per_journal:
                    break

            next_cursor = str(message.get("next-cursor") or "").strip()
            if not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor

        for item in journal_items[:max_articles_per_journal]:
            try:
                papers.append(_crossref_item_to_paper(item, journal_code))
            except Exception as exc:
                logger.warning("Top3 item parse failed for %s: %s", journal_code, exc)
    return papers
