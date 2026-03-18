from __future__ import annotations

import csv
import io
import json
import logging
import time
from pathlib import Path
from typing import Dict, List, Tuple

import requests

logger = logging.getLogger(__name__)

NBER_TSV_BASE = "https://data.nber.org/nber_paper_chapter_metadata/tsv"
NBER_CSV_BASE = "https://data.nber.org/nber_paper_chapter_metadata/csv"
NBER_FILES = ("title", "auths", "date", "abs", "ref")
CACHE_MANIFEST = "manifest.json"


def _download_text(url: str, timeout: int = 60) -> str:
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.content.decode("utf-8", errors="replace")


def _normalize_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ",".join(str(x).strip() for x in value if str(x).strip())
    raw = str(value).strip()
    return "" if raw.upper() in {"NULL", "NONE", "N/A", "NA"} else raw


def _normalize_row(row: Dict) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for k, v in row.items():
        if k is None:
            continue
        out[str(k)] = _normalize_cell(v)
    return out


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
        paper, part0 = first.split("\t", 1)
        abstract = ",".join([part0] + row[1:]).strip().strip('"')
        abstract = abstract.rstrip(",").strip()
        rows.append({"paper": paper.strip(), "abstract": abstract})
    return rows


def _parse_text(name: str, fmt: str, text: str) -> List[Dict[str, str]]:
    if fmt == "tsv":
        return _read_tsv_text(text)
    if name == "abs":
        return _read_abs_csv_text(text)
    return _read_csv_text(text)


def _paper_url(paper: str) -> str:
    num = str(paper).strip()
    if not num:
        return ""
    if not num.startswith(("w", "h", "t", "c")):
        num = f"w{num}"
    return f"https://www.nber.org/papers/{num}"


def _load_manifest(cache_dir: Path) -> Dict:
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


def _save_manifest(cache_dir: Path, manifest: Dict) -> None:
    (cache_dir / CACHE_MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _cache_expired(cache_dir: Path, max_age_days: int) -> Tuple[bool, Dict]:
    manifest = _load_manifest(cache_dir)
    updated_at = int(manifest.get("updated_at", 0) or 0)
    files = manifest.get("files", {}) or _discover_cached_files(cache_dir)
    now = int(time.time())
    expired = (now - updated_at) > (max_age_days * 86400)
    if not updated_at:
        expired = True
    for name in NBER_FILES:
        rel = files.get(name)
        if not rel or not (cache_dir / rel).exists():
            expired = True
            break
    return expired, manifest


def _download_one_table(name: str, timeout: int) -> Tuple[str, str]:
    tsv_url = f"{NBER_TSV_BASE}/{name}.tsv"
    logger.info("Fetching NBER metadata: %s", tsv_url)
    tsv_text = _download_text(tsv_url, timeout=timeout)
    tsv_rows = _read_tsv_text(tsv_text)
    if tsv_rows:
        return tsv_text, "tsv"

    csv_url = f"{NBER_CSV_BASE}/{name}.csv"
    logger.info("NBER TSV empty for %s, fallback to CSV: %s", name, csv_url)
    csv_text = _download_text(csv_url, timeout=timeout)
    return csv_text, "csv"


def _read_cached_table(cache_dir: Path, rel_path: str, name: str) -> List[Dict[str, str]]:
    path = cache_dir / rel_path
    if not path.exists():
        return []
    fmt = "tsv" if path.suffix.lower() == ".tsv" else "csv"
    text = path.read_text(encoding="utf-8", errors="replace")
    return _parse_text(name=name, fmt=fmt, text=text)


def _load_nber_datasets_from_cache(
    cache_dir: str | Path = "data/cache/nber",
    max_cache_age_days: int = 7,
    timeout: int = 60,
) -> Dict[str, List[Dict[str, str]]]:
    cache_path = Path(cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)
    logger.info("NBER cache path: %s", cache_path)

    expired, manifest = _cache_expired(cache_path, max_age_days=max_cache_age_days)
    logger.info("NBER cache expired: %s", expired)

    datasets: Dict[str, List[Dict[str, str]]] = {}
    files_map = dict(manifest.get("files", {}) or _discover_cached_files(cache_path))

    if not expired:
        logger.info("Using NBER local cache")
        for name in NBER_FILES:
            rel = files_map.get(name, "")
            datasets[name] = _read_cached_table(cache_path, rel, name=name) if rel else []
            logger.info("NBER table loaded from cache: %s rows=%d", name, len(datasets[name]))
        return datasets

    logger.info("Refreshing NBER cache from remote")
    refreshed_files = dict(files_map)
    for name in NBER_FILES:
        cached_rel = refreshed_files.get(name, "")
        try:
            text, fmt = _download_one_table(name=name, timeout=timeout)
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
    logger.info("NBER cache refreshed successfully")
    return datasets


def fetch_nber_working_papers(
    timeout: int = 60,
    cache_dir: str | Path = "data/cache/nber",
    max_cache_age_days: int = 7,
) -> List[Dict]:
    """Fetch and merge NBER metadata into one record per paper, with local cache."""
    datasets = _load_nber_datasets_from_cache(
        cache_dir=cache_dir,
        max_cache_age_days=max_cache_age_days,
        timeout=timeout,
    )

    records: Dict[str, Dict] = {}

    def ensure(paper: str) -> Dict:
        p = str(paper).strip()
        if p not in records:
            records[p] = {
                "paper_number": p,
                "title": "",
                "authors": [],
                "journal": "NBER Working Paper",
                "published": "",
                "year": "",
                "DOI": "",
                "abstract": "",
                "source": "nber",
                "id": "",
                "url": _paper_url(p),
                "type": "working-paper",
            }
        return records[p]

    for row in datasets.get("title", []):
        paper = row.get("paper", "")
        if not paper:
            continue
        ensure(paper)["title"] = row.get("title", "")

    for row in datasets.get("auths", []):
        paper = row.get("paper", "")
        if not paper:
            continue
        rec = ensure(paper)
        raw_name = row.get("name", "")
        if raw_name:
            for name in [x.strip() for x in raw_name.split(",") if x.strip()]:
                if name not in rec["authors"]:
                    rec["authors"].append(name)

    for row in datasets.get("date", []):
        paper = row.get("paper", "")
        if not paper:
            continue
        rec = ensure(paper)
        rec["published"] = row.get("issue_date", "")
        if rec["published"][:4].isdigit():
            rec["year"] = int(rec["published"][:4])

    for row in datasets.get("abs", []):
        paper = row.get("paper", "")
        if not paper:
            continue
        ensure(paper)["abstract"] = row.get("abstract", "")

    for row in datasets.get("ref", []):
        paper = row.get("paper", "")
        if not paper:
            continue
        rec = ensure(paper)
        if not rec.get("title"):
            rec["title"] = row.get("title", "")
        if not rec.get("published"):
            rec["published"] = row.get("issue_date", "")
            if rec["published"][:4].isdigit():
                rec["year"] = int(rec["published"][:4])
        doi = row.get("doi", "")
        if doi:
            rec["DOI"] = doi
        if (not rec.get("authors")) and row.get("author"):
            rec["authors"] = [x.strip() for x in row.get("author", "").split(",") if x.strip()]

    merged: List[Dict] = []
    for paper, rec in records.items():
        doi = (rec.get("DOI") or "").strip()
        rec["id"] = doi if doi else f"NBER:{paper}"
        if not rec.get("url"):
            rec["url"] = _paper_url(paper)
        merged.append(rec)
    return merged
