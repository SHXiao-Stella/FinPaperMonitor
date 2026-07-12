from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable, Dict, Iterable, List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..utils import compact_whitespace


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

QUERY_TOKEN_RE = re.compile(r'"[^"]+"|\(|\)|\bAND\b|\bOR\b|\bNOT\b|[^()\s]+', re.IGNORECASE)


def build_session() -> requests.Session:
    retry = Retry(
        total=3,
        backoff_factor=0.8,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "HEAD"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def query_groups_for_source(task_config: Dict[str, object], source_name: str) -> List[Dict[str, object]]:
    all_groups = task_config.get("query_groups") or {}
    source_cfg = (task_config.get("source_settings") or {}).get(source_name, {})
    selected_names = list(source_cfg.get("query_groups") or all_groups.keys())
    max_queries_per_group = int(source_cfg.get("max_queries_per_group", 0) or 0)
    max_total_queries = int(source_cfg.get("max_total_queries", 0) or 0)
    groups: List[Dict[str, object]] = []
    total_queries = 0
    for group_name in selected_names:
        group = all_groups.get(group_name)
        if not isinstance(group, dict):
            continue
        queries = [compact_whitespace(str(item)) for item in (group.get("queries") or []) if compact_whitespace(str(item))]
        if max_queries_per_group > 0:
            queries = queries[:max_queries_per_group]
        if max_total_queries > 0:
            remaining = max_total_queries - total_queries
            if remaining <= 0:
                break
            queries = queries[:remaining]
        if not queries:
            continue
        groups.append(
            {
                "name": str(group_name),
                "description": compact_whitespace(str(group.get("description") or "")),
                "queries": queries,
            }
        )
        total_queries += len(queries)
    return groups


def _query_tokens(query: str) -> List[str]:
    return [match.group(0) for match in QUERY_TOKEN_RE.finditer(query or "")]


def query_terms(query: str) -> List[str]:
    terms: List[str] = []
    seen = set()
    for token in _query_tokens(query):
        stripped = token.strip()
        upper = stripped.upper()
        if not stripped or upper in {"AND", "OR", "NOT"} or stripped in {"(", ")"}:
            continue
        cleaned = compact_whitespace(stripped.strip('"'))
        lowered = cleaned.lower()
        if cleaned and lowered not in seen:
            seen.add(lowered)
            terms.append(cleaned)
    return terms


def boolean_query_to_free_text(query: str) -> str:
    return " ".join(query_terms(query))


def boolean_query_to_arxiv(query: str) -> str:
    parts: List[str] = []
    for token in _query_tokens(query):
        stripped = token.strip()
        upper = stripped.upper()
        if not stripped:
            continue
        if stripped in {"(", ")"}:
            parts.append(stripped)
            continue
        if upper in {"AND", "OR", "NOT"}:
            parts.append(upper)
            continue
        term = compact_whitespace(stripped.strip('"'))
        if not term:
            continue
        if " " in term:
            parts.append(f'all:"{term}"')
        else:
            parts.append(f"all:{term}")
    return " ".join(parts)


def attach_query_group_metadata(paper, group_name: str, raw_query: str, source_name: str) -> None:
    paper.raw_metadata["source_handler"] = source_name
    query_groups = paper.raw_metadata.setdefault("query_groups", [])
    if group_name and group_name not in query_groups:
        query_groups.append(group_name)
    matched_queries = paper.raw_metadata.setdefault("matched_queries", [])
    if raw_query and raw_query not in matched_queries:
        matched_queries.append(raw_query)


def fetch_html(session: requests.Session, url: str, logger, params: Optional[Dict[str, object]] = None) -> str:
    logger.info("Fetching %s", url)
    response = session.get(url, params=params, timeout=30)
    response.raise_for_status()
    return response.text


def make_soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def absolute_url(base: str, href: str) -> str:
    return urljoin(base, href)


def extract_meta_map(soup: BeautifulSoup) -> Dict[str, List[str]]:
    meta_map: Dict[str, List[str]] = {}
    for node in soup.find_all("meta"):
        key = node.get("name") or node.get("property") or node.get("itemprop")
        value = node.get("content")
        if not key or not value:
            continue
        meta_map.setdefault(key, []).append(compact_whitespace(value))
    return meta_map


def meta_first(meta_map: Dict[str, List[str]], *keys: str) -> Optional[str]:
    for key in keys:
        values = meta_map.get(key)
        if values:
            return values[0]
    return None


def meta_all(meta_map: Dict[str, List[str]], *keys: str) -> List[str]:
    collected: List[str] = []
    for key in keys:
        collected.extend(meta_map.get(key, []))
    return [value for value in collected if value]


def extract_json_ld(soup: BeautifulSoup) -> List[dict]:
    payloads: List[dict] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text(" ", strip=True)
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            payloads.append(data)
        elif isinstance(data, list):
            payloads.extend(item for item in data if isinstance(item, dict))
    return payloads


def parse_date_text(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    text = compact_whitespace(value).replace("Published:", "").replace("Posted:", "").strip(", ")
    formats = [
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%d %B %Y",
        "%d %b %Y",
        "%B %d, %Y",
        "%b %d, %Y",
        "%d %B %Y %H:%M:%S",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    try:
        parsed = parsedate_to_datetime(text)
        if parsed.tzinfo:
            parsed = parsed.astimezone(timezone.utc)
        return parsed.date().isoformat()
    except Exception:
        pass
    match = re.search(r"(20\d{2}-\d{2}-\d{2})", text)
    if match:
        return match.group(1)
    return None


def days_since(date_text: Optional[str]) -> Optional[int]:
    iso = parse_date_text(date_text)
    if not iso:
        return None
    try:
        delta = datetime.now().date() - datetime.fromisoformat(iso).date()
    except ValueError:
        return None
    return delta.days


def filter_recent_with_fallback(
    papers: List,
    lookback_days: int,
    fallback_lookback_days: Optional[int] = None,
    fallback_min_candidates: int = 0,
    *,
    drop_missing_date: bool = False,
    date_getter: Optional[Callable[[object], Optional[str]]] = None,
) -> List:
    fallback_days = max(lookback_days, int(fallback_lookback_days or lookback_days))
    primary: List = []
    fallback: List = []
    date_getter = date_getter or (lambda paper: getattr(paper, "date", None))

    for paper in papers:
        age = days_since(date_getter(paper))
        if age is None:
            if drop_missing_date:
                continue
            primary.append(paper)
            fallback.append(paper)
            continue
        if age <= lookback_days:
            primary.append(paper)
        if age <= fallback_days:
            fallback.append(paper)

    if len(primary) >= max(0, int(fallback_min_candidates or 0)):
        return primary
    return fallback


def within_lookback(date_text: Optional[str], lookback_days: int) -> bool:
    age = days_since(date_text)
    if age is None:
        return True
    return age <= lookback_days


def on_or_after_date(date_text: Optional[str], min_date: Optional[str]) -> bool:
    threshold = parse_date_text(min_date)
    if not threshold:
        return True
    candidate = parse_date_text(date_text)
    if not candidate:
        return False
    return candidate >= threshold


def extract_text_from_selectors(soup: BeautifulSoup, selectors: Iterable[str]) -> Optional[str]:
    for selector in selectors:
        node = soup.select_one(selector)
        if node:
            text = compact_whitespace(node.get_text(" ", strip=True))
            if text:
                return text
    return None


def extract_links_by_patterns(soup: BeautifulSoup, base_url: str, patterns: Iterable[re.Pattern[str]]) -> List[str]:
    links: List[str] = []
    seen = set()
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        full = absolute_url(base_url, href)
        if any(pattern.search(full) for pattern in patterns) and full not in seen:
            seen.add(full)
            links.append(full)
    return links


def strip_jats(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    raw = html.unescape(text)
    soup = BeautifulSoup(raw, "html.parser")
    cleaned = compact_whitespace(soup.get_text(" ", strip=True))
    cleaned = re.sub(r"^(abstract)\s*[:\-]?\s*", "", cleaned, flags=re.IGNORECASE)
    return cleaned or None
