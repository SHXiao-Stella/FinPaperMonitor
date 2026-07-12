#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
LOCAL_CONFIG_PATH = ROOT / "config" / "doc_archive.local.yml"
STATE_PATH = ROOT / "data" / "doc_archive_runs.json"
OPENCLAW_CONFIG_PATH = Path.home() / ".openclaw" / "openclaw.json"
FEISHU_BASE_URL = "https://open.feishu.cn/open-apis"
FEISHU_TIMEOUT = 30
MAX_CONVERT_CHARS = 12_000
DESCENDANT_BATCH_SIZE = 900
WIKI_URL_RE = re.compile(r"/wiki/([A-Za-z0-9]+)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Append monitor output to a Feishu document")
    parser.add_argument("--source", required=True, choices=["top3", "econ5", "nber", "llm_finance"])
    parser.add_argument("--input", required=True, help="Markdown or text file to archive")
    parser.add_argument("--run-key", required=True, help="Run key, e.g. top3-2026-03-24 or nber-2026-W13")
    return parser.parse_args()


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"failed to parse YAML file: {path} ({exc})") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"invalid YAML structure in {path}: expected a mapping at the top level")
    return data


def load_archive_target(source: str) -> dict[str, str]:
    if not LOCAL_CONFIG_PATH.exists():
        raise RuntimeError(
            "missing local config file: "
            f"{LOCAL_CONFIG_PATH}. Copy config/doc_archive.example.yml to "
            "config/doc_archive.local.yml and fill in the doc_token or wiki_url values."
        )

    data = load_yaml(LOCAL_CONFIG_PATH)
    section = data.get(source)
    if not isinstance(section, dict):
        raise RuntimeError(f"missing '{source}' section in {LOCAL_CONFIG_PATH}")

    doc_token = str(section.get("doc_token", "")).strip()
    wiki_url = str(section.get("wiki_url", "")).strip()
    wiki_token = str(section.get("wiki_token", "")).strip()
    if not any([doc_token, wiki_url, wiki_token]):
        raise RuntimeError(
            f"missing {source}.doc_token or {source}.wiki_url or {source}.wiki_token in {LOCAL_CONFIG_PATH}"
        )
    return {
        "doc_token": doc_token,
        "wiki_url": wiki_url,
        "wiki_token": wiki_token,
        "prepend": "__YES__" if bool(section.get("prepend", False)) else "__NO__",
    }


def extract_wiki_token(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    match = WIKI_URL_RE.search(raw)
    if match:
        return match.group(1)
    return raw


def _nested_get(data: dict[str, Any], path: list[str]) -> str:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return ""
        current = current.get(key)
    return str(current or "").strip()


def load_feishu_credentials() -> tuple[str, str]:
    app_id = ""
    app_secret = ""
    source_desc = ""

    if OPENCLAW_CONFIG_PATH.exists():
        try:
            config = json.loads(OPENCLAW_CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError(f"failed to parse {OPENCLAW_CONFIG_PATH}: {exc}") from exc

        main_app_id = _nested_get(config, ["channels", "feishu", "accounts", "main", "appId"])
        main_app_secret = _nested_get(config, ["channels", "feishu", "accounts", "main", "appSecret"])
        if main_app_id and main_app_secret:
            app_id = main_app_id
            app_secret = main_app_secret
            source_desc = "openclaw.json account: main"

        if not app_id or not app_secret:
            accounts = config.get("channels", {}).get("feishu", {}).get("accounts", {})
            if isinstance(accounts, dict):
                for name, acc in accounts.items():
                    if not isinstance(acc, dict):
                        continue
                    cand_id = str(acc.get("appId") or "").strip()
                    cand_secret = str(acc.get("appSecret") or "").strip()
                    if cand_id and cand_secret:
                        app_id = cand_id
                        app_secret = cand_secret
                        source_desc = f"openclaw.json account: {name}"
                        break

        if not app_id or not app_secret:
            top_id = _nested_get(config, ["channels", "feishu", "appId"])
            top_secret = _nested_get(config, ["channels", "feishu", "appSecret"])
            if top_id and top_secret:
                app_id = top_id
                app_secret = top_secret
                source_desc = "openclaw.json top-level channels.feishu"

    if not app_id:
        app_id = os.environ.get("FEISHU_APP_ID", "").strip()
    if not app_secret:
        app_secret = os.environ.get("FEISHU_APP_SECRET", "").strip()
    if app_id and app_secret and not source_desc:
        source_desc = "environment"

    if not app_id or not app_secret:
        raise RuntimeError(
            "missing Feishu app credentials: expected one of "
            "channels.feishu.accounts.main.appId/appSecret, "
            "a complete account under channels.feishu.accounts, "
            f"channels.feishu.appId/appSecret in {OPENCLAW_CONFIG_PATH}, "
            "or FEISHU_APP_ID / FEISHU_APP_SECRET in the environment"
        )

    logging.info("using Feishu credentials from %s", source_desc)
    return app_id, app_secret


def load_state() -> dict[str, list[str]]:
    if not STATE_PATH.exists():
        return {"top3": [], "econ5": [], "nber": [], "llm_finance": []}

    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"failed to parse state file {STATE_PATH}: {exc}") from exc

    if not isinstance(data, dict):
        raise RuntimeError(f"invalid state file structure in {STATE_PATH}")

    normalized: dict[str, list[str]] = {"top3": [], "econ5": [], "nber": [], "llm_finance": []}
    for source in ("top3", "econ5", "nber", "llm_finance"):
        raw = data.get(source, [])
        if isinstance(raw, list):
            normalized[source] = [str(item).strip() for item in raw if str(item).strip()]
    return normalized


def save_state(state: dict[str, list[str]]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run_already_archived(source: str, run_key: str) -> bool:
    state = load_state()
    return run_key in set(state.get(source, []))


def mark_archived(source: str, run_key: str) -> None:
    state = load_state()
    items = set(state.get(source, []))
    items.add(run_key)
    state[source] = sorted(items)
    save_state(state)


def validate_run_key(source: str, run_key: str) -> str:
    prefix = f"{source}-"
    if not run_key.startswith(prefix):
        raise RuntimeError(
            f"invalid run-key '{run_key}' for source '{source}': expected prefix '{prefix}'"
        )
    suffix = run_key[len(prefix) :].strip()
    if not suffix:
        raise RuntimeError(f"invalid run-key '{run_key}': missing suffix after '{prefix}'")
    return suffix


def build_archive_markdown(source: str, run_key: str, body: str) -> str:
    suffix = validate_run_key(source, run_key)
    title_map = {
        "top3": "Top3 Daily",
        "econ5": "Economics Top5 Daily",
        "nber": "NBER Weekly",
        "llm_finance": "LLM Finance Daily",
    }
    title = title_map[source]
    normalized_body = body.rstrip("\n")
    return f"## {title} | {suffix}\n\n{normalized_body}\n\n---\n\n"


def split_markdown_by_size(markdown: str, max_chars: int) -> list[str]:
    if len(markdown) <= max_chars:
        return [markdown]

    lines = markdown.split("\n")
    chunks: list[str] = []
    current: list[str] = []
    current_length = 0
    in_fenced_block = False

    for line in lines:
        if line.startswith("```") or line.startswith("~~~"):
            in_fenced_block = not in_fenced_block

        line_length = len(line) + 1
        if current and current_length + line_length > max_chars and not in_fenced_block:
            chunks.append("\n".join(current))
            current = []
            current_length = 0

        current.append(line)
        current_length += line_length

    if current:
        chunks.append("\n".join(current))

    return chunks if len(chunks) > 1 else [markdown]


def api_request(
    session: requests.Session,
    method: str,
    path: str,
    *,
    token: str | None = None,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    url = f"{FEISHU_BASE_URL}{path}"
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        response = session.request(
            method=method,
            url=url,
            headers=headers,
            json=json_body,
            timeout=FEISHU_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"request to Feishu failed for {path}: {exc}") from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"Feishu returned a non-JSON response for {path}: HTTP {response.status_code}"
        ) from exc

    if response.status_code >= 400:
        raise RuntimeError(
            f"Feishu API HTTP {response.status_code} for {path}: "
            f"{payload.get('msg') or payload.get('message') or payload}"
        )

    if payload.get("code", 0) != 0:
        raise RuntimeError(
            f"Feishu API error for {path}: code={payload.get('code')} "
            f"msg={payload.get('msg') or payload.get('message') or 'unknown error'}"
        )

    return payload


def get_tenant_access_token(session: requests.Session, app_id: str, app_secret: str) -> str:
    payload = api_request(
        session,
        "POST",
        "/auth/v3/tenant_access_token/internal",
        json_body={"app_id": app_id, "app_secret": app_secret},
    )
    token = str(payload.get("tenant_access_token", "")).strip()
    if not token:
        raise RuntimeError("Feishu auth response did not include tenant_access_token")
    return token


def resolve_wiki_doc_token(session: requests.Session, token: str, wiki_token: str) -> str:
    wiki_token = extract_wiki_token(wiki_token)
    if not wiki_token:
        raise RuntimeError("wiki token is empty")

    payload = api_request(
        session,
        "GET",
        f"/wiki/v2/spaces/get_node?token={wiki_token}",
        token=token,
    )
    node = (payload.get("data") or {}).get("node") or {}
    obj_token = str(node.get("obj_token", "")).strip()
    obj_type = str(node.get("obj_type", "")).strip().lower()
    if not obj_token:
        raise RuntimeError("wiki node resolution did not return obj_token")
    if obj_type and obj_type not in {"doc", "docx"}:
        raise RuntimeError(f"wiki target obj_type={obj_type} is not a writable document")
    return obj_token


def convert_markdown(session: requests.Session, token: str, markdown: str) -> tuple[list[dict[str, Any]], list[str]]:
    payload = api_request(
        session,
        "POST",
        "/docx/v1/documents/blocks/convert",
        token=token,
        json_body={"content_type": "markdown", "content": markdown},
    )
    data = payload.get("data") or {}
    blocks = data.get("blocks") or []
    first_level_ids = data.get("first_level_block_ids") or []
    if not isinstance(blocks, list) or not isinstance(first_level_ids, list):
        raise RuntimeError("unexpected Feishu convert response structure")
    return blocks, [str(item) for item in first_level_ids]


def convert_markdown_in_chunks(
    session: requests.Session, token: str, markdown: str
) -> tuple[list[dict[str, Any]], list[str]]:
    all_blocks: list[dict[str, Any]] = []
    all_first_level_ids: list[str] = []

    for chunk in split_markdown_by_size(markdown, MAX_CONVERT_CHARS):
        blocks, first_level_ids = convert_markdown(session, token, chunk)
        all_blocks.extend(blocks)
        all_first_level_ids.extend(first_level_ids)

    return all_blocks, all_first_level_ids


def append_blocks(
    session: requests.Session,
    token: str,
    doc_token: str,
    blocks: list[dict[str, Any]],
    first_level_ids: list[str],
    *,
    prepend: bool = False,
) -> None:
    if not blocks or not first_level_ids:
        raise RuntimeError("converted content is empty")

    block_map = {
        str(block.get("block_id", "")).strip(): block
        for block in blocks
        if str(block.get("block_id", "")).strip()
    }
    def collect_descendants(root_id: str) -> list[dict[str, Any]]:
        ordered: list[dict[str, Any]] = []
        seen: set[str] = set()

        def visit(block_id: str) -> None:
            if block_id in seen or block_id not in block_map:
                return
            seen.add(block_id)
            block = block_map[block_id]
            ordered.append(block)
            children = block.get("children")
            if isinstance(children, list):
                for child_id in children:
                    visit(str(child_id))
            elif isinstance(children, str):
                visit(children)

        visit(root_id)
        return ordered

    batches: list[tuple[list[str], list[dict[str, Any]]]] = []
    current_ids: list[str] = []
    current_blocks: list[dict[str, Any]] = []

    for first_level_id in first_level_ids:
        subtree = collect_descendants(first_level_id)
        if not subtree:
            continue
        if len(subtree) > DESCENDANT_BATCH_SIZE:
            raise RuntimeError(
                f"converted subtree for block {first_level_id} exceeds descendant batch size "
                f"({len(subtree)} > {DESCENDANT_BATCH_SIZE})"
            )
        if current_blocks and len(current_blocks) + len(subtree) > DESCENDANT_BATCH_SIZE:
            batches.append((current_ids, current_blocks))
            current_ids = []
            current_blocks = []
        current_ids.append(first_level_id)
        current_blocks.extend(subtree)

    if current_blocks:
        batches.append((current_ids, current_blocks))

    if not batches:
        raise RuntimeError("converted content did not contain appendable top-level blocks")

    ordered_batches = list(reversed(batches)) if prepend else batches

    for batch_ids, batch_blocks in ordered_batches:
        api_request(
            session,
            "POST",
            f"/docx/v1/documents/{doc_token}/blocks/{doc_token}/descendant",
            token=token,
            json_body={
                "children_id": batch_ids,
                "descendants": batch_blocks,
                "index": 0 if prepend else -1,
            },
        )


def append_to_feishu_doc(doc_token: str, markdown: str, app_id: str, app_secret: str) -> None:
    with requests.Session() as session:
        token = get_tenant_access_token(session, app_id, app_secret)
        blocks, first_level_ids = convert_markdown_in_chunks(session, token, markdown)
        append_blocks(session, token, doc_token, blocks, first_level_ids)


def main() -> int:
    setup_logging()
    args = parse_args()

    input_path = Path(args.input).expanduser()
    if not input_path.exists():
        logging.error("input file not found: %s", input_path)
        return 1

    try:
        if run_already_archived(args.source, args.run_key):
            print("already archived")
            return 0
        raw_text = input_path.read_text(encoding="utf-8")
    except Exception as exc:
        logging.error("failed during preflight for %s: %s", input_path, exc)
        return 1

    if not raw_text.strip():
        logging.error("input file is empty: %s", input_path)
        return 1

    try:
        archive_target = load_archive_target(args.source)
        app_id, app_secret = load_feishu_credentials()
        archive_markdown = build_archive_markdown(args.source, args.run_key, raw_text)
        with requests.Session() as session:
            tenant_token = get_tenant_access_token(session, app_id, app_secret)
            doc_token = archive_target.get("doc_token", "")
            if not doc_token:
                wiki_token = archive_target.get("wiki_token", "") or archive_target.get("wiki_url", "")
                doc_token = resolve_wiki_doc_token(session, tenant_token, wiki_token)
            blocks, first_level_ids = convert_markdown_in_chunks(session, tenant_token, archive_markdown)
            append_blocks(
                session,
                tenant_token,
                doc_token,
                blocks,
                first_level_ids,
                prepend=archive_target.get("prepend") == "__YES__",
            )
        mark_archived(args.source, args.run_key)
    except Exception as exc:
        logging.error("failed to archive %s run %s: %s", args.source, args.run_key, exc)
        return 1

    logging.info("archived %s run %s to Feishu doc", args.source, args.run_key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
