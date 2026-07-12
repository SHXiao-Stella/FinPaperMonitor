from __future__ import annotations

from collections import OrderedDict
from datetime import date, datetime, timedelta
from typing import Dict, List

from .models import RankedPaper, TranslationResult
from .text_policy import sanitize_note_text


def format_digest(
    task_config,
    ranked_papers: List[RankedPaper],
    translations: Dict[str, TranslationResult],
    stats: Dict[str, object],
) -> str:
    target_count = int(task_config.get("target_count", len(ranked_papers)))
    if not ranked_papers:
        return "今日 LLM Finance 文献监控暂无符合主题且未曾推送过的新论文。\n"

    lines: List[str] = []
    if len(ranked_papers) < target_count:
        lines.extend(
            [
                f"本次符合条件的新论文不足目标篇数，因此只推送 {len(ranked_papers)} 篇。",
                "",
            ]
        )

    diagnostics_map = {}
    for item in stats.get("selected_diagnostics", []) or []:
        if isinstance(item, dict):
            diagnostics_map[str(item.get("paper_id") or "")] = item

    for index, ranked in enumerate(ranked_papers, start=1):
        paper = ranked.paper
        translation = translations[paper.paper_id]
        authors = "、".join(paper.authors) if paper.authors else "未知"
        lines.extend(
            [
                "",
                f"{index}. 标题：{paper.title}",
                f"作者：{authors}",
                f"来源：{paper.source}",
                f"发布日期：{paper.date or '未知'}",
                f"链接：{paper.link}",
                "中文摘要：",
                translation.translated_abstract_zh or "未生成中文摘要",
            ]
        )
        diagnostics = diagnostics_map.get(paper.paper_id)
        if diagnostics and bool(task_config.get("debug_output", False)):
            lines.append(
                "Dry-run诊断："
                f"source={diagnostics.get('source') or paper.source}; "
                f"query_group={', '.join(diagnostics.get('query_groups') or []) or 'none'}; "
                f"bucket={diagnostics.get('bucket') or 'none'}; "
                f"llm_core_terms={', '.join(diagnostics.get('matched_llm_core_terms') or []) or 'none'}; "
                f"finance_scope_terms={', '.join(diagnostics.get('matched_finance_scope_terms') or []) or 'none'}; "
                f"finance_task_data_outcome={', '.join(diagnostics.get('matched_finance_task_data_outcome_terms') or []) or 'none'}; "
                f"rule_score={diagnostics.get('rule_score')}; "
                f"semantic_score={diagnostics.get('semantic_score')}; "
                f"reason={diagnostics.get('selection_reason') or 'none'}"
            )
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def _window_start(anchor_date: date, window_cfg: Dict[str, object]) -> date:
    unit = str(window_cfg.get("unit", "days")).strip().lower()
    size = max(1, int(window_cfg.get("size", 1)))
    if unit == "weeks":
        return anchor_date - timedelta(weeks=size - 1)
    return anchor_date - timedelta(days=size - 1)


def format_doc_window(task_config, archive_state: Dict[str, object], doc_target: Dict[str, object]) -> str:
    display_name = task_config.get("display_name", task_config.get("task_name", "Paper Monitor"))
    today = datetime.now()
    today_str = today.strftime("%Y-%m-%d %H:%M")
    window_cfg = doc_target.get("window") or {}
    window_label = str(window_cfg.get("label") or f"最近 {window_cfg.get('size', 1)} {window_cfg.get('unit', 'days')}")
    start_date = _window_start(today.date(), window_cfg)

    records = []
    for record in archive_state.get("records", []):
        if not isinstance(record, dict):
            continue
        issue_date = str(record.get("issue_date") or "").strip()
        if not issue_date:
            continue
        try:
            issue_day = datetime.fromisoformat(issue_date).date()
        except ValueError:
            continue
        if issue_day < start_date:
            continue
        records.append(record)

    grouped = OrderedDict()
    for record in sorted(
        records,
        key=lambda item: (
            str(item.get("issue_date") or ""),
            str(item.get("first_seen_at") or ""),
            -(int(item.get("issue_rank") or 0)),
        ),
        reverse=True,
    ):
        grouped.setdefault(str(record.get("issue_date")), []).append(record)

    total_papers = len(records)
    lines: List[str] = [
        f"# {display_name}",
        "",
        f"- 更新时间：{today_str}",
        f"- 展示窗口：{window_label}",
        f"- 收录论文：{total_papers} 篇",
        "- 说明：企业微信文档只展示最近窗口，完整历史以本地归档为准。",
    ]

    if not grouped:
        lines.extend(
            [
                "",
                "> 当前窗口内暂无已归档内容。",
            ]
        )
        return "\n".join(lines).strip() + "\n"

    for issue_date, issue_records in grouped.items():
        lines.extend(["", f"## {issue_date}"])
        ordered_records = sorted(
            issue_records,
            key=lambda item: (
                str(item.get("first_seen_at") or ""),
                -(int(item.get("issue_rank") or 0)),
            ),
            reverse=True,
        )
        for index, record in enumerate(ordered_records, start=1):
            authors = "、".join(record.get("authors") or []) or "未知"
            note = sanitize_note_text(record.get("note_zh") or "")
            abstract_text = str(record.get("translated_abstract_zh") or "未生成中文摘要").strip()
            lines.extend(
                [
                    "",
                    f"### {index}. {record.get('title') or '未命名论文'}",
                    f"- 作者：{authors}",
                    f"- 来源：{record.get('source') or '未知'} | 日期：{record.get('paper_date') or '未知'}",
                    f"- 链接：{record.get('link') or ''}",
                    f"- 中文摘要：{abstract_text}",
                ]
            )
            if note:
                lines.append(f"- 备注：{note}")

    return "\n".join(lines).strip() + "\n"
