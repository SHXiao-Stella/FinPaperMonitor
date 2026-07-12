from __future__ import annotations

import html
import hashlib
import re
from typing import Iterable, List, Optional
from urllib.parse import urlparse, urlunparse

from bs4 import BeautifulSoup

from .models import Paper
from .utils import compact_whitespace


TITLE_NOISE_RE = re.compile(r"[^a-z0-9]+")


def normalize_title(title: str) -> str:
    lowered = compact_whitespace(title).lower()
    return TITLE_NOISE_RE.sub(" ", lowered).strip()


def canonicalize_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    parsed = urlparse(url.strip())
    if not parsed.scheme:
        return url.strip()
    cleaned = parsed._replace(query="", fragment="")
    return urlunparse(cleaned)


def normalize_authors(authors: Iterable[str]) -> List[str]:
    return [compact_whitespace(author) for author in authors if compact_whitespace(author)]


def _clean_text(value: Optional[str]) -> str:
    raw = html.unescape(value or "")
    text = BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)
    return compact_whitespace(text)


def stable_paper_id(source: str, source_id: str, title: str) -> str:
    if source_id:
        return f"{source.lower()}:{source_id}"
    digest = hashlib.sha1(normalize_title(title).encode("utf-8")).hexdigest()[:16]
    return f"{source.lower()}:{digest}"


def finalize_paper(paper: Paper) -> Paper:
    paper.title = _clean_text(paper.title)
    paper.abstract = _clean_text(paper.abstract) or None
    paper.authors = normalize_authors(_clean_text(author) for author in paper.authors)
    paper.canonical_url = canonicalize_url(paper.link)
    paper.normalized_title = normalize_title(paper.title)
    return paper
