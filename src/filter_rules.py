from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List

from .models import FilterDecision, Paper
from .utils import load_yaml


@dataclass
class CompiledGroup:
    name: str
    phrases: List[str]
    patterns: List[re.Pattern[str]]


def _compile_phrase(phrase: str) -> re.Pattern[str]:
    escaped = re.escape(phrase.strip())
    escaped = escaped.replace(r"\ ", r"\s+")
    return re.compile(rf"(?<!\w){escaped}(?!\w)", re.IGNORECASE)


class KeywordRuleEngine:
    def __init__(self, common_config_path, profile_name: str):
        cfg = load_yaml(common_config_path)
        self.scoring = cfg.get("scoring", {})
        self.profiles = cfg.get("profiles", {})
        self.buckets = cfg.get("buckets", {}) or {}
        self.profile_name = profile_name
        profile = self.profiles.get(profile_name)
        if not profile:
            raise ValueError(f"Unknown filter profile: {profile_name}")
        self.profile = profile
        self.groups: Dict[str, CompiledGroup] = {}
        for name, phrases in (cfg.get("groups") or {}).items():
            cleaned = [str(item).strip() for item in phrases if str(item).strip()]
            self.groups[name] = CompiledGroup(
                name=name,
                phrases=cleaned,
                patterns=[_compile_phrase(item) for item in cleaned],
            )

    def _match_group(self, group_name: str, text: str) -> List[str]:
        group = self.groups[group_name]
        matched: List[str] = []
        for phrase, pattern in zip(group.phrases, group.patterns):
            if pattern.search(text):
                matched.append(phrase)
        return matched

    @staticmethod
    def _field_presence(field_hits: Dict[str, Dict[str, List[str]]], field_name: str) -> Dict[str, List[str]]:
        return {group: hits[field_name] for group, hits in field_hits.items() if hits.get(field_name)}

    def _collect_hits(self, paper: Paper):
        title_text = paper.title or ""
        abstract_text = paper.abstract or ""
        author_text = " ".join(paper.authors or [])

        matched_groups: Dict[str, List[str]] = {}
        field_hits: Dict[str, Dict[str, List[str]]] = {}
        score = 0.0
        title_weight = float(self.scoring.get("title_match_weight", 2.4))
        abstract_weight = float(self.scoring.get("abstract_match_weight", 1.8))
        author_weight = float(self.scoring.get("author_keyword_weight", 0.3))

        for group_name in self.groups:
            title_hits = sorted(set(self._match_group(group_name, title_text)))
            abstract_hits = sorted(set(self._match_group(group_name, abstract_text)))
            author_hits = sorted(set(self._match_group(group_name, author_text)))
            merged = sorted(set(title_hits + abstract_hits + author_hits))
            if merged:
                matched_groups[group_name] = merged
                field_hits[group_name] = {
                    "title": title_hits,
                    "abstract": abstract_hits,
                    "authors": author_hits,
                }
                score += len(title_hits) * title_weight
                score += len(abstract_hits) * abstract_weight
                score += len(author_hits) * author_weight

        return matched_groups, field_hits, score

    def _evaluate_bucket(
        self,
        bucket_name: str,
        bucket_cfg: Dict[str, object],
        matched_groups: Dict[str, List[str]],
        field_hits: Dict[str, Dict[str, List[str]]],
        base_score: float,
    ) -> tuple[bool, float, List[str]]:
        required_all = list(bucket_cfg.get("require_all") or [])
        require_any = list(bucket_cfg.get("require_any") or [])
        optional_any = list(bucket_cfg.get("optional_any") or [])
        penalty_groups = list(bucket_cfg.get("penalty_groups") or [])
        require_title_any = list(bucket_cfg.get("require_title_any") or [])
        require_title_or_abstract_any = list(bucket_cfg.get("require_title_or_abstract_any") or [])
        min_total_matches = int(bucket_cfg.get("min_total_matches", 1))

        title_presence = self._field_presence(field_hits, "title")
        abstract_presence = self._field_presence(field_hits, "abstract")

        matched_required_all = all(group in matched_groups for group in required_all)
        matched_required_any = not require_any or any(group in matched_groups for group in require_any)
        matched_required_title_any = not require_title_any or any(group in title_presence for group in require_title_any)
        matched_required_title_or_abstract_any = not require_title_or_abstract_any or any(
            group in title_presence or group in abstract_presence for group in require_title_or_abstract_any
        )
        matched_optional = [group for group in optional_any if group in matched_groups]
        relevant_groups = set(required_all + require_any + optional_any)
        total_matches = sum(len(matched_groups.get(group, [])) for group in relevant_groups) or sum(
            len(values) for values in matched_groups.values()
        )

        score = base_score
        notes: List[str] = []
        if matched_required_all:
            score += float(self.scoring.get("require_all_bonus", 2.6))
        else:
            notes.append("Missing required bucket group(s)")
        if require_any:
            if matched_required_any:
                score += float(self.scoring.get("require_any_bonus", 1.2))
            else:
                notes.append("Did not match any bucket require_any group")
        if matched_required_title_any:
            score += float(self.scoring.get("title_alignment_bonus", 0.5))
        elif require_title_any:
            notes.append("Did not match bucket title hint")
        if matched_required_title_or_abstract_any:
            score += float(self.scoring.get("abstract_signal_bonus", 0.8))
        elif require_title_or_abstract_any:
            notes.append("Missing required explicit signal in title/abstract")

        score += len(matched_optional) * float(self.scoring.get("optional_group_bonus", 0.6))

        bucket_base_bonus = (self.scoring.get("bucket_base_bonus") or {}).get(bucket_name)
        if bucket_base_bonus is not None:
            score += float(bucket_base_bonus)

        if "llm_core_terms" in matched_groups:
            score += float(self.scoring.get("llm_core_bonus", 1.0))
        if "finance_scope_terms" in matched_groups:
            score += float(self.scoring.get("finance_scope_bonus", 1.2))
        if "strong_finance_outcome_terms" in matched_groups:
            score += float(self.scoring.get("strong_outcome_bonus", 1.3))
        if bucket_name == "finance_llm_tooling" and (
            "finance_tooling_terms" in title_presence or "finance_tooling_terms" in abstract_presence
        ):
            score += float(self.scoring.get("tooling_focus_bonus", 2.0))
        if any(group in abstract_presence for group in required_all + require_any):
            score += float(self.scoring.get("abstract_signal_bonus", 0.8))

        penalty_weight = float(self.scoring.get("deprioritize_penalty_weight", 1.6))
        penalty_hits = sum(len(matched_groups.get(group, [])) for group in penalty_groups)
        if penalty_hits:
            score -= penalty_hits * penalty_weight
            notes.append(f"Penalty groups matched: {', '.join(group for group in penalty_groups if group in matched_groups)}")

        matched = (
            matched_required_all
            and matched_required_any
            and matched_required_title_any
            and matched_required_title_or_abstract_any
            and total_matches >= min_total_matches
        )
        if total_matches < min_total_matches:
            notes.append(f"Bucket matches below threshold ({total_matches} < {min_total_matches})")

        return matched, score, notes

    def _evaluate_legacy_profile(
        self,
        matched_groups: Dict[str, List[str]],
        field_hits: Dict[str, Dict[str, List[str]]],
        base_score: float,
    ) -> FilterDecision:
        title_group_hits = self._field_presence(field_hits, "title")
        notes: List[str] = []
        score = base_score

        required_all = self.profile.get("require_all", [])
        require_any = self.profile.get("require_any", [])
        optional_any = self.profile.get("optional_any", [])
        excluded = self.profile.get("exclude", [])
        require_title_all = self.profile.get("require_title_all", [])
        require_title_any = self.profile.get("require_title_any", [])

        excluded_hits = [group for group in excluded if matched_groups.get(group)]
        if excluded_hits:
            notes.append(f"Matched excluded groups: {', '.join(excluded_hits)}")
            return FilterDecision(matched=False, score=0.0, matched_groups=matched_groups, notes=notes)

        matched_required_all = all(group in matched_groups for group in required_all)
        matched_required_any = not require_any or any(group in matched_groups for group in require_any)
        matched_required_title_all = all(group in title_group_hits for group in require_title_all)
        matched_required_title_any = not require_title_any or any(group in title_group_hits for group in require_title_any)
        matched_optional = [group for group in optional_any if group in matched_groups]
        total_matches = sum(len(items) for items in matched_groups.values())
        min_total_matches = int(self.profile.get("min_total_matches", 1))

        if required_all and matched_required_all:
            score += float(self.scoring.get("require_all_bonus", 2.6))
        if require_any and matched_required_any:
            score += float(self.scoring.get("require_any_bonus", 1.2))
        score += len(matched_optional) * float(self.scoring.get("optional_group_bonus", 0.6))

        if not matched_required_all:
            notes.append("Missing required group(s)")
        if not matched_required_any:
            notes.append("Did not match any required_any group")
        if not matched_required_title_all:
            notes.append("Missing required title group(s)")
        if not matched_required_title_any:
            notes.append("Did not match required title group in title")
        if total_matches < min_total_matches:
            notes.append(f"Total matches below threshold ({total_matches} < {min_total_matches})")

        matched = (
            matched_required_all
            and matched_required_any
            and matched_required_title_all
            and matched_required_title_any
            and total_matches >= min_total_matches
        )

        return FilterDecision(
            matched=matched,
            score=score,
            matched_groups=matched_groups,
            notes=notes,
        )

    def evaluate(self, paper: Paper) -> FilterDecision:
        matched_groups, field_hits, base_score = self._collect_hits(paper)
        notes: List[str] = []

        excluded = list(self.profile.get("exclude") or [])
        excluded_hits = [group for group in excluded if matched_groups.get(group)]
        if excluded_hits:
            notes.append(f"Matched excluded groups: {', '.join(excluded_hits)}")
            return FilterDecision(matched=False, score=0.0, matched_groups=matched_groups, notes=notes)

        allowed_buckets = list(self.profile.get("allowed_buckets") or [])
        if not allowed_buckets:
            return self._evaluate_legacy_profile(matched_groups, field_hits, base_score)

        bucket_scores: Dict[str, float] = {}
        best_bucket = None
        best_score = float("-inf")
        best_notes: List[str] = []
        for bucket_name in allowed_buckets:
            bucket_cfg = self.buckets.get(bucket_name) or {}
            matched, bucket_score, bucket_notes = self._evaluate_bucket(
                bucket_name=bucket_name,
                bucket_cfg=bucket_cfg,
                matched_groups=matched_groups,
                field_hits=field_hits,
                base_score=base_score,
            )
            bucket_scores[bucket_name] = bucket_score
            if matched and bucket_score > best_score:
                best_bucket = bucket_name
                best_score = bucket_score
                best_notes = bucket_notes

        if best_bucket is None:
            notes.append("No finance bucket matched")
            return FilterDecision(
                matched=False,
                score=base_score,
                matched_groups=matched_groups,
                notes=notes,
                bucket=None,
                bucket_scores=bucket_scores,
            )

        return FilterDecision(
            matched=True,
            score=best_score,
            matched_groups=matched_groups,
            notes=best_notes,
            bucket=best_bucket,
            bucket_scores=bucket_scores,
        )
