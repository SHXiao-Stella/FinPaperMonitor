from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from .models import Paper, RankedPaper
from .sources.common import days_since
from .utils import chunked


class HeuristicRanker:
    def __init__(self, task_config: Dict[str, object]):
        ranking_cfg = task_config.get("ranking") or {}
        self.source_weights = ranking_cfg.get("source_weights") or {}
        self.bucket_weights = ranking_cfg.get("bucket_weights") or {}
        self.query_group_weights = ranking_cfg.get("query_group_weights") or {}
        self.prefer_recent_days = int(ranking_cfg.get("prefer_recent_days", 90))
        self.completeness_weight = float(ranking_cfg.get("completeness_weight", 0.8))
        self.rule_score_weight = float(ranking_cfg.get("rule_score_weight", 1.0))
        self.recency_weight = float(ranking_cfg.get("recency_weight", 1.0))
        self.generic_penalty_weight = float(ranking_cfg.get("generic_penalty_weight", 1.2))

    def score(
        self,
        paper: Paper,
        rule_score: float,
        *,
        bucket: Optional[str] = None,
        matched_groups: Optional[Dict[str, List[str]]] = None,
        query_groups: Optional[List[str]] = None,
    ) -> float:
        completeness = 0.0
        if paper.abstract:
            completeness += 1.0
        if paper.authors:
            completeness += 0.4
        if paper.date:
            completeness += 0.4

        source_weight = float(self.source_weights.get(paper.source, 0.6))
        age_days = days_since(paper.date)
        if age_days is None:
            recency = 0.3
        elif age_days <= self.prefer_recent_days:
            recency = max(0.0, 1.0 - (age_days / max(self.prefer_recent_days, 1)))
        else:
            recency = max(0.0, 0.25 - ((age_days - self.prefer_recent_days) / 365.0))

        bucket_weight = float(self.bucket_weights.get(bucket or "", 0.0))
        query_group_boost = 0.0
        for group_name in query_groups or []:
            query_group_boost = max(query_group_boost, float(self.query_group_weights.get(group_name, 0.0)))

        generic_penalty = 0.0
        if matched_groups and matched_groups.get("deprioritize_generic_ai_terms"):
            generic_penalty = len(matched_groups.get("deprioritize_generic_ai_terms") or []) * self.generic_penalty_weight

        return (
            rule_score * self.rule_score_weight
            + completeness * self.completeness_weight
            + recency * self.recency_weight
            + source_weight
            + bucket_weight
            + query_group_boost
            - generic_penalty
        )


class LLMFinanceSemanticRanker:
    def __init__(self, backend, batch_size: int = 8):
        self.backend = backend
        self.batch_size = batch_size

    @staticmethod
    def _normalize_bool(value: object) -> Optional[bool]:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes", "y"}:
                return True
            if lowered in {"false", "0", "no", "n"}:
                return False
        return None

    @staticmethod
    def _normalize_score(value: object) -> float:
        try:
            score = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(score, 100.0))

    def _evaluate_batch(self, papers: List[Paper]) -> Dict[str, Dict[str, object]]:
        prompt_lines = [
            "你是金融学术文献筛选助手。",
            "请判断下面每篇论文是否属于“finance-first 的 LLM in Finance”主题，并给出相关性评分。",
            "只输出 JSON 数组，不要输出任何额外解释。",
            "字段要求：paper_id, is_llm_finance, relevance_score, top_pick_score, why_relevant_zh。",
            "relevance_score 和 top_pick_score 都是 0-100 的整数。",
            "优先保留：金融问题是核心、LLM/生成式AI 是核心方法或核心工具、使用金融数据/金融任务/金融结论的论文。",
            "优先风格参考：Can ChatGPT Forecast Stock Price Movements?、Can Large Language Models Trade?、Chronologically Consistent Large Language Models、PIXIU、FinBen。",
            "finance-specific tooling 只保留真正面向金融场景的 benchmark / evaluation / instruction tuning / agent / temporal consistency 论文。",
            "明确降权或排除：泛 LLM 架构论文、泛 AI for business 评论、finance 只被顺带提及的论文、把金融仅当作一个普通时间序列例子的论文。",
            "如果论文只是泛泛谈 AI 或泛泛谈 finance，但两者没有明确结合，请判定为 false。",
            "why_relevant_zh 用一句自然中文解释它为什么与 LLM in Finance 相关，不要写套话。",
            "",
        ]
        for paper in papers:
            query_groups = ", ".join(str(item) for item in (paper.raw_metadata.get("query_groups") or []))
            prompt_lines.extend(
                [
                    f"paper_id: {paper.paper_id}",
                    f"title: {paper.title}",
                    f"source: {paper.source}",
                    f"date: {paper.date or 'unknown'}",
                    f"query_groups: {query_groups or 'unknown'}",
                    f"abstract: {paper.abstract or 'abstract missing'}",
                    "",
                ]
            )
        raw = self.backend.generate_json("\n".join(prompt_lines), purpose="llm_finance_semantic_rank")
        result_map: Dict[str, Dict[str, object]] = {}
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                paper_id = str(item.get("paper_id", "")).strip()
                if paper_id:
                    result_map[paper_id] = {
                        "paper_id": paper_id,
                        "is_llm_finance": self._normalize_bool(item.get("is_llm_finance")),
                        "relevance_score": self._normalize_score(item.get("relevance_score", 0)),
                        "top_pick_score": self._normalize_score(item.get("top_pick_score", 0)),
                        "why_relevant_zh": str(item.get("why_relevant_zh", "")).strip(),
                    }
        return result_map

    def evaluate(self, papers: List[Paper]) -> Dict[str, Dict[str, object]]:
        if not papers:
            return {}

        result_map: Dict[str, Dict[str, object]] = {}
        for batch in chunked(papers, max(1, self.batch_size)):
            result_map.update(self._evaluate_batch(list(batch)))
        return result_map


def combine_rank(
    papers: List[Paper],
    rule_scores: Dict[str, float],
    matched_groups: Dict[str, Dict[str, List[str]]],
    task_config: Dict[str, object],
    llm_results: Optional[Dict[str, Dict[str, object]]] = None,
    buckets: Optional[Dict[str, str]] = None,
) -> List[RankedPaper]:
    llm_results = llm_results or {}
    buckets = buckets or {}
    ranker = HeuristicRanker(task_config)
    ranked: List[RankedPaper] = []

    for paper in papers:
        rule_score = float(rule_scores.get(paper.paper_id, 0.0))
        paper_bucket = buckets.get(paper.paper_id)
        paper_query_groups = [str(item) for item in (paper.raw_metadata.get("query_groups") or []) if str(item).strip()]
        paper_matched_groups = matched_groups.get(paper.paper_id, {})
        heuristic_score = ranker.score(
            paper,
            rule_score,
            bucket=paper_bucket,
            matched_groups=paper_matched_groups,
            query_groups=paper_query_groups,
        )
        llm_item = llm_results.get(paper.paper_id, {})
        llm_relevance_score = float(llm_item.get("relevance_score", 0) or 0)
        llm_top_pick_score = float(llm_item.get("top_pick_score", 0) or 0)
        llm_is_relevant = llm_item.get("is_llm_finance")
        final_score = heuristic_score + llm_relevance_score * 0.03 + llm_top_pick_score * 0.04

        ranked.append(
            RankedPaper(
                paper=paper,
                rule_score=rule_score,
                heuristic_score=heuristic_score,
                llm_relevance_score=llm_relevance_score,
                llm_top_pick_score=llm_top_pick_score,
                final_score=final_score,
                llm_is_relevant=llm_is_relevant if isinstance(llm_is_relevant, bool) else None,
                matched_groups=paper_matched_groups,
                why_relevant=str(llm_item.get("why_relevant_zh", "")).strip() or None,
                bucket=paper_bucket,
                query_groups=paper_query_groups,
            )
        )

    ranked.sort(key=lambda item: item.final_score, reverse=True)
    return ranked
