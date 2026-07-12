from __future__ import annotations

import re


TECHNICAL_NOTE_MARKERS = (
    "当前模型后端不可用",
    "规则命中",
    "论文仍已通过规则筛选",
    "fallback",
    "后备路径",
    "调试",
    "报错",
)


def collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def ensure_sentence(text: str) -> str:
    cleaned = collapse_whitespace(text)
    if not cleaned:
        return ""
    if cleaned[-1] in "。！？!?；;":
        return cleaned
    return cleaned + "。"


def first_sentence(text: str) -> str:
    cleaned = collapse_whitespace(text)
    if not cleaned:
        return ""
    match = re.search(r"(.+?[。！？!?；;])(?:\s|$)", cleaned)
    if match:
        return match.group(1).strip()
    return ensure_sentence(cleaned)


def sanitize_note_text(text: str) -> str:
    cleaned = collapse_whitespace(text)
    if not cleaned:
        return ""

    lowered = cleaned.lower()
    if any(marker.lower() in lowered for marker in TECHNICAL_NOTE_MARKERS):
        return ""

    cleaned = cleaned.replace("这篇文章", "这篇论文")
    cleaned = re.sub(r"^仅从题目看[，,:：]?\s*", "从题目看，", cleaned)

    care_match = re.match(r"^如果你关心(.+?)[，,]\s*这篇(?:文章|论文)(.*)$", cleaned)
    if care_match:
        topic = collapse_whitespace(care_match.group(1))
        tail = collapse_whitespace(care_match.group(2))
        if tail.startswith("给出的证据"):
            qualifier = tail[len("给出的证据") :].strip(" ，,。")
            if qualifier:
                tail = f"给出了{qualifier}的证据"
            else:
                tail = "给出了直接证据"
        if not tail:
            tail = "提供了直接证据"
        cleaned = f"这篇论文围绕{topic}，{tail}"

    cleaned = re.sub(
        r"^这篇(?:文章|论文)\s*(?:确实|很)?值得(?:关注|一读|看|留意)[，,:：]?\s*因为(?:它)?",
        "这篇论文",
        cleaned,
    )
    cleaned = re.sub(
        r"^这篇(?:文章|论文)\s*(?:确实|很)?值得(?:关注|一读|看|留意)[，,:：]?\s*",
        "这篇论文",
        cleaned,
    )
    cleaned = re.sub(r"^这篇论文它", "这篇论文", cleaned)
    cleaned = re.sub(r"^这篇论文给出的", "这篇论文给出了", cleaned)
    cleaned = re.sub(r"^从题目看[，,:：]?\s*这篇(?:文章|论文)", "从题目看，这篇论文", cleaned)
    cleaned = re.sub(r"^文章", "这篇论文", cleaned)
    cleaned = collapse_whitespace(cleaned)
    return ensure_sentence(cleaned)
