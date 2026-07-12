from __future__ import annotations

import csv
import io
import json
import time
from pathlib import Path
from typing import Dict, List, Tuple

from ..models import Paper
from ..normalize import finalize_paper, stable_paper_id
from ..utils import DATA_DIR
from .common import build_session, filter_recent_with_fallback, on_or_after_date


NBER_TSV_BASE = "https://data.nber.org/nber_paper_chapter_metadata/tsv"
NBER_CSV_BASE = "https://data.nber.org/nber_paper_chapter_metadata/csv"
NBER_FILES = ("title", "auths", "date", "abs", "ref")
CACHE_MANIFEST = "manifest.json"


def _download_text(session, url: str, timeout: int = 60) -> str:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    return response.content.decode("utf-8", errors="replace")


def _normalize_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ",".join(str(item).strip() for item in value if str(item).strip())
    raw = str(value).strip()
    return "" if raw.upper() in {"NULL", "NONE", "N/A", "NA"} else raw


def _normalize_row(row: Dict) -> Dict[str, str]:
    normalized: Dict[str, str] = {}
    for key, value in row.items():
        if key is None:
            continue
        normalized[str(key)] = _normalize_cell(value)
    return normalized


def _read_tsv_text(text: str) -> List[Dict[str, str]]:
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    return [_normalize_row(row) for row in reader]


def _read_csv_text(text: str) -> List[Dict[str, str]]:
    reader = csv.DictReader(io.StringIO(text))
    return [_normalize_row(row) for row in reader]


def _read_abs_csv_text(text: str) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    reader = csv.reader(io.StringIO(text))
    next(reader, None)
    for row in reader:
        if not row:
            continue
        first = row[0]
        if "\t" not in first:
            continue
        paper, first_chunk = first.split("\t", 1)
        abstract = ",".join([first_chunk] + row[1:]).strip().strip('"').rstrip(",").strip()
        rows.append({"paper": paper.strip(), "abstract": abstract})
    return rows


def _parse_text(name: str, fmt: str, text: str) -> List[Dict[str, str]]:
    if fmt == "tsv":
        return _read_tsv_text(text)
    if name == "abs":
        return _read_abs_csv_text(text)
    return _read_csv_text(text)


def _normalize_paper_number(paper_number: str) -> str:
    raw = str(paper_number or "").strip()
    if raw.lower().startswith("w") and raw[1:].isdigit():
        return raw[1:]
    return raw


def _paper_url(paper_number: str) -> str:
    raw = str(paper_number or "").strip()
    if not raw:
        return ""
    if not raw.startswith(("w", "h", "t", "c")):
        raw = f"w{raw}"
    return f"https://www.nber.org/papers/{raw}"


def _load_manifest(cache_dir: Path) -> Dict[str, object]:
    path = cache_dir / CACHE_MANIFEST
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _discover_cached_files(cache_dir: Path) -> Dict[str, str]:
    found: Dict[str, str] = {}
    for name in NBER_FILES:
        for ext in ("tsv", "csv"):
            path = cache_dir / f"{name}.{ext}"
            if path.exists():
                found[name] = path.name
                break
    return found


def _save_manifest(cache_dir: Path, manifest: Dict[str, object]) -> None:
    (cache_dir / CACHE_MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _cache_expired(cache_dir: Path, max_age_days: int) -> Tuple[bool, Dict[str, object]]:
    manifest = _load_manifest(cache_dir)
    updated_at = int(manifest.get("updated_at", 0) or 0)
    files = manifest.get("files", {}) or _discover_cached_files(cache_dir)
    now = int(time.time())
    expired = not updated_at or (now - updated_at) > (max_age_days * 86400)
    for name in NBER_FILES:
        rel = str(files.get(name) or "").strip()
        if not rel or not (cache_dir / rel).exists():
            expired = True
            break
    return expired, manifest


def _download_one_table(session, name: str, timeout: int, logger) -> Tuple[str, str]:
    tsv_url = f"{NBER_TSV_BASE}/{name}.tsv"
    logger.info("Fetching NBER metadata: %s", tsv_url)
    tsv_text = _download_text(session, tsv_url, timeout=timeout)
    tsv_rows = _read_tsv_text(tsv_text)
    if tsv_rows:
        return tsv_text, "tsv"

    csv_url = f"{NBER_CSV_BASE}/{name}.csv"
    logger.info("NBER TSV empty for %s, fallback to CSV: %s", name, csv_url)
    csv_text = _download_text(session, csv_url, timeout=timeout)
    return csv_text, "csv"


def _read_cached_table(cache_dir: Path, rel_path: str, name: str) -> List[Dict[str, str]]:
    path = cache_dir / rel_path
    if not path.exists():
        return []
    fmt = "tsv" if path.suffix.lower() == ".tsv" else "csv"
    text = path.read_text(encoding="utf-8", errors="replace")
    return _parse_text(name=name, fmt=fmt, text=text)


def _load_nber_datasets_from_cache(
    logger,
    cache_dir: str | Path,
    max_cache_age_days: int = 7,
    timeout: int = 60,
) -> Dict[str, List[Dict[str, str]]]:
    session = build_session()
    cache_path = Path(cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)
    logger.info("NBER cache path: %s", cache_path)

    expired, manifest = _cache_expired(cache_path, max_age_days=max_cache_age_days)
    logger.info("NBER cache expired: %s", expired)

    datasets: Dict[str, List[Dict[str, str]]] = {}
    files_map = dict(manifest.get("files", {}) or _discover_cached_files(cache_path))

    if not expired:
        for name in NBER_FILES:
            rel = str(files_map.get(name) or "").strip()
            datasets[name] = _read_cached_table(cache_path, rel, name=name) if rel else []
            logger.info("NBER table loaded from cache: %s rows=%d", name, len(datasets[name]))
        return datasets

    refreshed_files = dict(files_map)
    for name in NBER_FILES:
        cached_rel = str(refreshed_files.get(name) or "").strip()
        try:
            text, fmt = _download_one_table(session=session, name=name, timeout=timeout, logger=logger)
            target = cache_path / f"{name}.{fmt}"
            target.write_text(text, encoding="utf-8")
            refreshed_files[name] = target.name
            datasets[name] = _parse_text(name=name, fmt=fmt, text=text)
            logger.info("NBER table refreshed: %s rows=%d", target.name, len(datasets[name]))
        except Exception as exc:
            if cached_rel and (cache_path / cached_rel).exists():
                logger.warning("NBER refresh failed for %s, fallback to cache (%s): %s", name, cached_rel, exc)
                datasets[name] = _read_cached_table(cache_path, cached_rel, name=name)
            else:
                raise RuntimeError(f"NBER table unavailable: {name}") from exc

    _save_manifest(
        cache_path,
        {
            "updated_at": int(time.time()),
            "files": refreshed_files,
        },
    )
    return datasets


def fetch_nber_candidates(task_config: Dict[str, object], logger) -> List[Paper]:
    source_cfg = (task_config.get("source_settings") or {}).get("nber", {})
    min_date = str(source_cfg.get("min_date") or task_config.get("min_date") or "").strip()
    cache_dir = str(source_cfg.get("cache_dir") or (DATA_DIR / "cache" / "nber"))
    max_cache_age_days = int(source_cfg.get("max_cache_age_days", 7))
    timeout = int(source_cfg.get("timeout", 60))
    max_records = int(source_cfg.get("max_records") or source_cfg.get("max_detail_papers") or 0)
    lookback_days = source_cfg.get("lookback_days")
    fallback_lookback_days = source_cfg.get("fallback_lookback_days")
    fallback_min_candidates = int(source_cfg.get("fallback_min_candidates", 0))
    drop_missing_date = bool(source_cfg.get("drop_missing_date", True))

    datasets = _load_nber_datasets_from_cache(
        logger=logger,
        cache_dir=cache_dir,
        max_cache_age_days=max_cache_age_days,
        timeout=timeout,
    )

    records: Dict[str, Dict[str, object]] = {}

    def ensure(paper_number: str) -> Dict[str, object]:
        normalized = _normalize_paper_number(paper_number)
        if normalized not in records:
            records[normalized] = {
                "paper_number": normalized,
                "title": "",
                "authors": [],
                "published": "",
                "year": "",
                "doi": "",
                "abstract": "",
            }
        return records[normalized]

    for row in datasets.get("title", []):
        paper_number = row.get("paper", "")
        if paper_number:
            ensure(paper_number)["title"] = row.get("title", "")

    for row in datasets.get("auths", []):
        paper_number = row.get("paper", "")
        if not paper_number:
            continue
        record = ensure(paper_number)
        raw_name = row.get("name", "")
        if raw_name:
            for name in [item.strip() for item in raw_name.split(",") if item.strip()]:
                if name not in record["authors"]:
                    record["authors"].append(name)

    for row in datasets.get("date", []):
        paper_number = row.get("paper", "")
        if not paper_number:
            continue
        record = ensure(paper_number)
        record["published"] = row.get("issue_date", "")
        if str(record["published"])[:4].isdigit():
            record["year"] = int(str(record["published"])[:4])

    for row in datasets.get("abs", []):
        paper_number = row.get("paper", "")
        if paper_number:
            ensure(paper_number)["abstract"] = row.get("abstract", "")

    for row in datasets.get("ref", []):
        paper_number = row.get("paper", "")
        if not paper_number:
            continue
        record = ensure(paper_number)
        if not record.get("title"):
            record["title"] = row.get("title", "")
        if not record.get("published"):
            record["published"] = row.get("issue_date", "")
            if str(record["published"])[:4].isdigit():
                record["year"] = int(str(record["published"])[:4])
        doi = row.get("doi", "")
        if doi:
            record["doi"] = doi
        if (not record.get("authors")) and row.get("author"):
            record["authors"] = [item.strip() for item in row.get("author", "").split(",") if item.strip()]

    sorted_records = sorted(
        records.values(),
        key=lambda item: (
            str(item.get("published") or ""),
            int(item.get("year") or 0),
            str(item.get("paper_number") or ""),
        ),
        reverse=True,
    )

    papers: List[Paper] = []
    for record in sorted_records:
        paper_number = str(record.get("paper_number") or "").strip()
        title = str(record.get("title") or paper_number).strip()
        paper = finalize_paper(
            Paper(
                title=title or paper_number,
                authors=list(record.get("authors") or []),
                abstract=str(record.get("abstract") or "").strip() or None,
                source="NBER",
                source_category="nber_working_paper",
                date=str(record.get("published") or "").strip() or None,
                link=_paper_url(paper_number),
                paper_id=stable_paper_id("nber", paper_number, title or paper_number),
                raw_metadata={
                    "paper_number": paper_number,
                    "doi": str(record.get("doi") or "").strip(),
                    "source_handler": "nber",
                },
            )
        )
        if min_date and not on_or_after_date(paper.date, min_date):
            continue
        papers.append(paper)
    if lookback_days:
        papers = filter_recent_with_fallback(
            papers,
            lookback_days=int(lookback_days),
            fallback_lookback_days=int(fallback_lookback_days or lookback_days),
            fallback_min_candidates=fallback_min_candidates,
            drop_missing_date=drop_missing_date,
        )

    if max_records:
        papers = papers[:max_records]
    return papers
