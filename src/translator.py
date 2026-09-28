from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import Paper, TranslationResult
from .text_policy import first_sentence, sanitize_note_text
from .utils import ROOT_DIR, chunked, compact_whitespace, env_first, load_env_file, run_command

try:
    from deep_translator import GoogleTranslator
except Exception:  # pragma: no cover
    GoogleTranslator = None


def _decode_first_json(text: str, *, expect_type: type | tuple[type, ...] | None = None):
    decoder = json.JSONDecoder()
    text = text.strip()
    if text.startswith("```"):
        parts = text.split("```")
        for part in parts:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{") or part.startswith("["):
                try:
                    obj, _ = decoder.raw_decode(part)
                except json.JSONDecodeError:
                    continue
                if expect_type is None or isinstance(obj, expect_type):
                    return obj

    candidate_indexes = [idx for idx, ch in enumerate(text) if ch in "[{"]
    for start in candidate_indexes:
        try:
            obj, _ = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if expect_type is None or isinstance(obj, expect_type):
            return obj

    raise ValueError("No JSON payload found in model response")


def _extract_from_container(node: object) -> Optional[str]:
    if isinstance(node, str):
        return node.strip() or None

    if isinstance(node, dict):
        payloads = node.get("payloads")
        if isinstance(payloads, list):
            parts: List[str] = []
            for item in payloads:
                if not isinstance(item, dict):
                    continue
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
                    continue
                nested = _extract_from_container(item.get("content"))
                if nested:
                    parts.append(nested)
            if parts:
                return "\n".join(parts)

        for key in ("reply", "text", "message", "content", "assistant", "output"):
            value = node.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, (dict, list)):
                nested = _extract_from_container(value)
                if nested:
                    return nested

    if isinstance(node, list):
        parts: List[str] = []
        for item in node:
            nested = _extract_from_container(item)
            if nested:
                parts.append(nested)
        if parts:
            return "\n".join(parts)

    return None


def _extract_agent_text(payload: Dict[str, object]) -> str:
    for key in ("reply", "text", "message", "assistant", "content"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    direct_text = _extract_from_container(payload)
    if direct_text:
        return direct_text
    result_text = _extract_from_container(payload.get("result"))
    if result_text:
        return result_text
    messages = payload.get("messages")
    message_text = _extract_from_container(messages)
    if message_text:
        return message_text
    raise ValueError(f"Could not extract agent text from payload keys={list(payload.keys())}")


def _normalize_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y"}
    return False


class BaseLLMBackend:
    def generate_json(self, prompt: str, purpose: str):
        raise NotImplementedError


class OpenClawAgentBackend(BaseLLMBackend):
    def __init__(self, logger, ensure_gateway=None):
        self.logger = logger
        self.ensure_gateway = ensure_gateway
        configured_path = env_first("PAPER_MONITOR_OPENCLAW_PATH")
        home_path = Path.home() / ".openclaw" / "bin" / "openclaw"
        configured_executable = None
        if configured_path:
            expanded = str(Path(configured_path).expanduser())
            configured_executable = expanded if Path(expanded).exists() else shutil.which(configured_path)
        self.openclaw_path = configured_executable or shutil.which("openclaw") or (
            str(home_path) if home_path.exists() else "/usr/bin/openclaw"
        )
        self.agent_name = env_first("PAPER_MONITOR_OPENCLAW_AGENT", default="main") or "main"
        self.session_id = str(uuid.uuid4())
        self.thinking = "low"
        self.timeout_seconds = 600

    def generate_json(self, prompt: str, purpose: str):
        if self.ensure_gateway:
            self.ensure_gateway()
        command = [
            self.openclaw_path,
            "agent",
            "--agent",
            self.agent_name,
            "--session-id",
            self.session_id,
            "--json",
            "--thinking",
            self.thinking,
            "--timeout",
            str(self.timeout_seconds),
            "--message",
            prompt,
        ]
        completed = run_command(command, logger=self.logger, timeout=self.timeout_seconds + 60)
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "openclaw agent failed")
        raw_stdout = completed.stdout.strip()
        try:
            payload = _decode_first_json(raw_stdout, expect_type=dict)
        except Exception as exc:
            snippet = raw_stdout[:1000]
            self.logger.warning(
                "Failed to parse OpenClaw outer JSON for purpose=%s: %s | stdout_snippet=%r",
                purpose,
                exc,
                snippet,
            )
            raise

        text = _extract_agent_text(payload)
        self.logger.info("LLM purpose=%s response chars=%s", purpose, len(text))
        try:
            return _decode_first_json(text, expect_type=(dict, list))
        except Exception as exc:
            snippet = text[:1000]
            self.logger.warning(
                "Failed to parse model JSON payload for purpose=%s: %s | text_snippet=%r",
                purpose,
                exc,
                snippet,
            )
            raise


class MockLLMBackend(BaseLLMBackend):
    def __init__(self, logger):
        self.logger = logger

    def generate_json(self, prompt: str, purpose: str):
        self.logger.warning("Using mock LLM backend for purpose=%s", purpose)
        return []


def build_llm_backend(logger, ensure_gateway=None) -> BaseLLMBackend:
    load_env_file(ROOT_DIR / ".env")
    backend = os.getenv("PAPER_MONITOR_LLM_BACKEND", "openclaw_agent").strip().lower()
    if backend == "openclaw_agent":
        return OpenClawAgentBackend(logger=logger, ensure_gateway=ensure_gateway)
    return MockLLMBackend(logger=logger)


class PaperTranslator:
    def __init__(self, backend: BaseLLMBackend, logger):
        self.backend = backend
        self.logger = logger
        self.google_translator = GoogleTranslator(source="auto", target="zh-CN") if GoogleTranslator else None
        self.replacements = [
            (r"\bLLM\b", "大语言模型"),
            (r"法学硕士", "大语言模型"),
            (r"\bFinBERT\b", "FinBERT"),
            (r"收益电话会议", "业绩电话会"),
        ]

    def _post_process_translation(self, text: str) -> str:
        cleaned = text.strip()
        for pattern, replacement in self.replacements:
            cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
        return cleaned

    def _missing_abstract_summary(self, paper: Paper) -> str:
        meta_parts = []
        if paper.title:
            meta_parts.append(f"题目为《{paper.title}》")
        if paper.source:
            meta_parts.append(f"来源为 {paper.source}")
        if paper.date:
            meta_parts.append(f"日期为 {paper.date}")
        if not meta_parts:
            meta_parts.append("当前只拿到了题录元数据")
        return f"未获取到 abstract。该论文{'，'.join(meta_parts)}。"

    def _missing_abstract_note(self, paper: Paper) -> str:
        source = paper.source or "未知来源"
        date = paper.date or "未知日期"
        return sanitize_note_text(f"这篇论文目前只拿到了标题与元数据，具体细节还需要查看 {source} 在 {date} 发布的原文。")

    def _fallback_translate_text(self, paper: Paper) -> str:
        translated = paper.abstract or ""
        if paper.abstract and self.google_translator:
            try:
                translated = self.google_translator.translate(paper.abstract)
            except Exception as exc:
                self.logger.warning("GoogleTranslator fallback failed for %s: %s", paper.paper_id, exc)
        return self._post_process_translation(translated)

    def _looks_overcompressed(self, paper: Paper, translated_text: str) -> bool:
        source = compact_whitespace(paper.abstract or "")
        translated = compact_whitespace(translated_text)
        if not source:
            return False
        if not translated:
            return True
        if len(source) < 240:
            return False
        return len(translated) < max(80, int(len(source) * 0.22))

    def _fallback_note_from_translation(self, paper: Paper, translated_text: str) -> str:
        if not paper.abstract:
            return self._missing_abstract_note(paper)

        sentence = first_sentence(translated_text).strip("。；; ")
        sentence = re.sub(r"^(在本文中|在本研究中)[，,]?\s*", "", sentence)
        sentence = re.sub(r"^(本文|本研究|本论文)", "这篇论文", sentence)
        sentence = re.sub(r"^作者(?:们)?[，,]?\s*", "", sentence)
        sentence = re.sub(r"^我们[，,]?\s*", "", sentence)
        sentence = sentence.strip("。；; ")
        if not sentence:
            return sanitize_note_text(f"这篇论文发表于 {paper.source or '未知来源'}，日期为 {paper.date or '未知日期'}")
        if sentence.startswith("这篇论文"):
            return sanitize_note_text(sentence)
        if re.match(r"^(研究|讨论|分析|考察|检验|展示|提出|比较|解释|估计|构建|利用|说明|揭示|发现|关注|刻画|识别|量化|评估|检视|总结|梳理|回顾)", sentence):
            return sanitize_note_text("这篇论文" + sentence)
        return sanitize_note_text(f"这篇论文的重点是{sentence}")

    def _coerce_translation_result(self, paper: Paper, item: Optional[Dict[str, Any]]) -> TranslationResult:
        raw_translated = ""
        raw_note = ""
        abstract_missing = not bool(paper.abstract)
        if isinstance(item, dict):
            raw_translated = str(item.get("translated_abstract_zh", "")).strip()
            raw_note = str(item.get("worth_reading_note_zh", "")).strip()
            if "abstract_missing" in item:
                abstract_missing = _normalize_bool(item.get("abstract_missing"))

        if not paper.abstract:
            translated = self._missing_abstract_summary(paper)
            note = sanitize_note_text(raw_note) or self._missing_abstract_note(paper)
            return TranslationResult(
                paper_id=paper.paper_id,
                abstract_missing=True,
                translated_abstract_zh=translated,
                worth_reading_note_zh=note,
            )

        translated = self._post_process_translation(raw_translated)
        if not translated or self._looks_overcompressed(paper, translated):
            translated = self._fallback_translate_text(paper)
        note = sanitize_note_text(raw_note) or self._fallback_note_from_translation(paper, translated)
        return TranslationResult(
            paper_id=paper.paper_id,
            abstract_missing=bool(abstract_missing),
            translated_abstract_zh=translated,
            worth_reading_note_zh=note,
        )

    def _fallback_translate(self, paper: Paper) -> TranslationResult:
        if not paper.abstract:
            return TranslationResult(
                paper_id=paper.paper_id,
                abstract_missing=True,
                translated_abstract_zh=self._missing_abstract_summary(paper),
                worth_reading_note_zh=self._missing_abstract_note(paper),
            )

        translated = self._fallback_translate_text(paper)
        return TranslationResult(
            paper_id=paper.paper_id,
            abstract_missing=False,
            translated_abstract_zh=translated,
            worth_reading_note_zh=self._fallback_note_from_translation(paper, translated),
        )

    def _translate_batch(self, papers: List[Paper]) -> Dict[str, TranslationResult]:
        prompt_lines = [
            "你是金融学术论文中文翻译助手。",
            "请把每篇论文的 abstract 尽量完整翻译成中文。",
            "不要为了简洁而删掉关键方法、样本、识别设计、机制、结论或政策含义。",
            "如果 abstract 很长，也要按原文信息顺序完整翻译，不要改写成过短摘要。",
            "如果没有 abstract，translated_abstract_zh 必须以“未获取到 abstract。”开头，并结合标题、来源、日期补一句简短中文说明。",
            "worth_reading_note_zh 必须是一句自然、简洁的中文陈述句。",
            "不要写“这篇论文值得关注，因为它……”",
            "不要写“如果你关心……这篇论文……”",
            "不要写推荐语、不要用第二人称。",
            "优先写成“这篇论文讨论了…… / 这篇论文把……与……联系起来…… / 这篇论文的贡献在于……”这类直接陈述。",
            "只输出 JSON 数组，不要输出任何额外解释。",
            "JSON 字段：paper_id, abstract_missing, translated_abstract_zh, worth_reading_note_zh。",
            "",
        ]
        for paper in papers:
            prompt_lines.extend(
                [
                    f"paper_id: {paper.paper_id}",
                    f"title: {paper.title}",
                    f"authors: {', '.join(paper.authors) if paper.authors else 'unknown'}",
                    f"source: {paper.source}",
                    f"date: {paper.date or 'unknown'}",
                    f"abstract: {paper.abstract or 'abstract missing'}",
                    "",
                ]
            )

        raw = self.backend.generate_json("\n".join(prompt_lines), purpose="translate_selected_papers")
        raw_map: Dict[str, Dict[str, Any]] = {}
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                paper_id = str(item.get("paper_id", "")).strip()
                if not paper_id:
                    continue
                raw_map[paper_id] = item
        translation_map: Dict[str, TranslationResult] = {}
        for paper in papers:
            translation_map[paper.paper_id] = self._coerce_translation_result(paper, raw_map.get(paper.paper_id))
        return translation_map

    def translate(self, papers: List[Paper], batch_size: int = 5) -> Dict[str, TranslationResult]:
        if not papers:
            return {}

        translation_map: Dict[str, TranslationResult] = {}
        batch_size = max(1, int(batch_size or 1))
        for batch in chunked(papers, batch_size):
            batch_list = list(batch)
            try:
                batch_result = self._translate_batch(batch_list)
            except Exception as exc:
                self.logger.warning("Primary translation backend failed for batch, using fallback translator: %s", exc)
                batch_result = {}

            for paper in batch_list:
                if paper.paper_id in batch_result:
                    translation_map[paper.paper_id] = batch_result[paper.paper_id]
                    continue
                self.logger.info("Use fallback translation for %s", paper.paper_id)
                translation_map[paper.paper_id] = self._fallback_translate(paper)
        return translation_map
