from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Dict, List, Tuple

import yaml


def load_keywords(path: str | Path) -> Tuple[List[str], List[str]]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    include = [str(x).strip().lower() for x in raw.get("include", []) if str(x).strip()]
    exclude = [str(x).strip().lower() for x in raw.get("exclude", []) if str(x).strip()]
    return include, exclude


def _parse_date(d: str) -> date | None:
    if not d:
        return None
    try:
        y, m, day = d.split("-")
        return date(int(y), int(m), int(day))
    except Exception:
        return None


def filter_papers(
    papers: List[Dict],
    include_keywords: List[str],
    exclude_keywords: List[str],
    min_date: str,
) -> List[Dict]:
    threshold = _parse_date(min_date)
    threshold_year = threshold.year if threshold else None
    out: List[Dict] = []

    for p in papers:
        if p.get("source") in {"top_journal", "econ5_journal"} and p.get("type") != "journal-article":
            continue

        pub_date = _parse_date(str(p.get("published", "")))
        if threshold:
            if pub_date:
                if pub_date < threshold:
                    continue
            else:
                year = int(p.get("year") or 0)
                if threshold_year and year and year < threshold_year:
                    continue

        text = f"{p.get('title', '')} {p.get('abstract', '') or ''}".lower()

        if include_keywords and not any(k in text for k in include_keywords):
            continue
        if exclude_keywords and any(k in text for k in exclude_keywords):
            continue

        out.append(p)

    out.sort(key=lambda x: (str(x.get("published", "")), int(x.get("year") or 0)), reverse=True)
    return out
