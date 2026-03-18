from __future__ import annotations

import logging
import re
from datetime import date
from typing import Dict, Iterable, List

import requests

CROSSREF_WORKS_URL = "https://api.crossref.org/works"
JOURNAL_ISSN_MAP = {
    "The Journal of Finance": "0022-1082",
    "Journal of Financial Economics": "0304-405X",
    "Review of Financial Studies": "0893-9454",
}

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def _strip_tags(text: str) -> str:
    clean = _TAG_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", clean).strip()


def _first(items: Iterable[str]) -> str:
    for item in items:
        if item:
            return item
    return ""


def _extract_date_parts(item: Dict) -> Dict[str, str | int]:
    for key in ("published", "published-print", "published-online", "issued"):
        parts = item.get(key, {}).get("date-parts", [])
        if parts and parts[0]:
            dp = parts[0]
            year = int(dp[0])
            month = int(dp[1]) if len(dp) > 1 else 1
            day = int(dp[2]) if len(dp) > 2 else 1
            dt = date(year, month, day)
            return {"published": dt.isoformat(), "year": year}
    return {"published": "", "year": ""}


def _normalize_item(item: Dict) -> Dict:
    title = _first(item.get("title", []))

    authors = []
    for author in item.get("author", []):
        full_name = " ".join(
            p
            for p in [author.get("given", "").strip(), author.get("family", "").strip()]
            if p
        )
        if full_name:
            authors.append(full_name)

    abstract_raw = item.get("abstract", "") or ""
    abstract = _strip_tags(abstract_raw) if abstract_raw else ""

    date_info = _extract_date_parts(item)

    doi = item.get("DOI", "") or ""
    return {
        "id": doi,
        "source": "top_journal",
        "title": title,
        "authors": authors,
        "journal": _first(item.get("container-title", [])),
        "published": date_info["published"],
        "year": date_info["year"],
        "DOI": doi,
        "abstract": abstract,
        "url": f"https://doi.org/{doi}" if doi else "",
        "type": item.get("type", ""),
    }


def fetch_journal_articles(
    journal_title: str,
    issn: str,
    from_date: str,
    rows: int = 100,
    timeout: int = 30,
    max_pages: int = 20,
) -> List[Dict]:
    """Fetch article metadata from Crossref for one journal using ISSN."""
    logger.info("Fetching Crossref data for journal: %s (ISSN=%s)", journal_title, issn)

    results: List[Dict] = []
    cursor = "*"

    for page in range(max_pages):
        params = {
            "filter": f"from-pub-date:{from_date},issn:{issn}",
            "rows": max(rows, 50),
            "sort": "published",
            "order": "desc",
            "cursor": cursor,
            "select": "DOI,title,author,container-title,published-print,published-online,published,abstract,type",
        }

        req = requests.Request("GET", CROSSREF_WORKS_URL, params=params).prepare()
        logger.info("Request URL [%s page %d]: %s", journal_title, page + 1, req.url)
        resp = requests.get(CROSSREF_WORKS_URL, params=params, timeout=timeout)
        logger.info("HTTP status [%s page %d]: %s", journal_title, page + 1, resp.status_code)
        resp.raise_for_status()

        payload = resp.json().get("message", {})
        items = payload.get("items", [])
        if not items:
            break

        results.extend(_normalize_item(item) for item in items)

        next_cursor = payload.get("next-cursor")
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor

    logger.info("Fetched %d raw records for %s", len(results), journal_title)
    for idx, item in enumerate(results[:3], start=1):
        logger.info(
            "Sample [%s #%d] title=%s | DOI=%s",
            journal_title,
            idx,
            item.get("title", ""),
            item.get("DOI", ""),
        )
    return results


def fetch_all_journals(from_date: str) -> List[Dict]:
    """Fetch all target journals. Failures are logged and skipped."""
    all_items: List[Dict] = []

    for journal, issn in JOURNAL_ISSN_MAP.items():
        try:
            all_items.extend(fetch_journal_articles(journal_title=journal, issn=issn, from_date=from_date))
        except Exception as exc:  # Keep monitor stable.
            logger.error("Failed to fetch journal %s (ISSN=%s): %s", journal, issn, exc)

    return all_items
