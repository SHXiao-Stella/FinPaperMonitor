from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from .archive_store import TaskArchiveStore
from .dedupe import PushedStateStore, dedupe_candidates
from .filter_rules import KeywordRuleEngine
from .formatter import format_digest, format_doc_window
from .models import Paper, PipelineRunResult
from .notifier_openclaw import OpenClawNotifier
from .notifier_wecom_doc import WeComDocNotifier
from .ranker_llm import LLMFinanceSemanticRanker, combine_rank
from .sources.arxiv import fetch_arxiv_candidates
from .sources.common import on_or_after_date, within_lookback
from .sources.nber import fetch_nber_candidates
from .sources.semanticscholar import fetch_semanticscholar_candidates
from .sources.ssrn import fetch_ssrn_candidates
from .sources.top3 import fetch_top3_candidates
from .translator import PaperTranslator, build_llm_backend
from .utils import ARCHIVE_DIR, CONFIG_DIR, DATA_DIR, LOG_DIR, ROOT_DIR, STATE_DIR, atomic_write_json, atomic_write_text, env_first, load_env_file, load_yaml, setup_logger


SOURCE_HANDLERS = {
    "arxiv": fetch_arxiv_candidates,
    "semanticscholar": fetch_semanticscholar_candidates,
    "ssrn": fetch_ssrn_candidates,
    "nber": fetch_nber_candidates,
    "top3": fetch_top3_candidates,
}


class PaperMonitorPipeline:
    BUCKET_PRIORITY = {
        "core_llm_in_finance": 3,
        "finance_llm_tooling": 2,
        "adjacent_or_noise": 1,
    }

    def __init__(self, config_path: Path, dry_run: bool = False):
        load_env_file(ROOT_DIR / ".env")
        self.config_path = config_path
        self.task_config = load_yaml(config_path)
        self.dry_run = dry_run
        self.task_name = self.task_config["task_name"]
        self.logger, self.log_path = setup_logger(self.task_name, dry_run=dry_run)
        self.notifier = OpenClawNotifier(self.logger)
        self.doc_notifier = WeComDocNotifier(self.logger)
        self.backend = build_llm_backend(self.logger, ensure_gateway=self.notifier.ensure_gateway_running)
        self.translator = PaperTranslator(self.backend, self.logger)
        self.rule_engine = KeywordRuleEngine(CONFIG_DIR / "keywords_common.yml", self.task_config["filter_profile"])
        doc_targets_path = CONFIG_DIR / "doc_targets.yml"
        doc_targets = ((load_yaml(doc_targets_path) if doc_targets_path.exists() else {}) or {}).get("tasks") or {}
        self.doc_target = doc_targets.get(self.task_name) or {}
        if not self.doc_target:
            if self.dry_run:
                self.doc_target = {
                    "docid": "",
                    "window": {
                        "label": "最近 14 天",
                        "size": 14,
                        "unit": "days",
                    },
                }
            else:
                raise RuntimeError(f"Missing doc target config for task {self.task_name}")
        self.archive_store = TaskArchiveStore(
            task_name=self.task_name,
            display_name=str(self.task_config.get("display_name", self.task_name)),
            archive_path=ARCHIVE_DIR / f"{self.task_name}.json",
            logger=self.logger,
        )
        task_state_name = {
            "llm_finance_daily": "pushed_ids_llm_finance.json",
            "top3_daily": "pushed_ids_top3.json",
            "nber_weekly": "pushed_ids_nber_weekly.json",
        }[self.task_name]
        self.state_store = PushedStateStore(
            global_state_path=STATE_DIR / "pushed_ids_global.json",
            task_state_path=STATE_DIR / task_state_name,
        )
        self.source_fetch_counts: Dict[str, int] = {}

    def _fetch_all(self) -> List[Paper]:
        papers: List[Paper] = []
        for source_name in self.task_config.get("sources", []):
            handler = SOURCE_HANDLERS[source_name]
            try:
                source_papers = handler(self.task_config, self.logger)
                self.source_fetch_counts[source_name] = len(source_papers)
                self.logger.info("Fetched %s paper(s) from %s", len(source_papers), source_name)
                papers.extend(source_papers)
            except Exception as exc:
                self.source_fetch_counts[source_name] = 0
                self.logger.exception("Source %s failed: %s", source_name, exc)
        return papers

    @staticmethod
    def _count_by_source(papers: List[Paper]) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for paper in papers:
            source_name = str(paper.raw_metadata.get("source_handler") or paper.source or "unknown")
            counts[source_name] = counts.get(source_name, 0) + 1
        return counts

    @staticmethod
    def _paper_filter_date(paper: Paper) -> str | None:
        return str(paper.raw_metadata.get("recency_date") or paper.raw_metadata.get("updated") or paper.date or "").strip() or None

    @staticmethod
    def _count_ranked_buckets(items) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for item in items:
            bucket = str(getattr(item, "bucket", None) or "unbucketed")
            counts[bucket] = counts.get(bucket, 0) + 1
        return counts

    @staticmethod
    def _count_bucket_map(bucket_map: Dict[str, str], papers: List[Paper]) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for paper in papers:
            bucket = str(bucket_map.get(paper.paper_id) or "unbucketed")
            counts[bucket] = counts.get(bucket, 0) + 1
        return counts

    def _select_balanced(self, ranked_items):
        ranking_cfg = self.task_config.get("ranking") or {}
        balance_cfg = ranking_cfg.get("balance") or {}
        target_count = int(self.task_config.get("target_count", 5))
        min_core = min(target_count, int(balance_cfg.get("min_core_papers", 0)))
        max_tooling = max(0, int(balance_cfg.get("max_tooling_papers", target_count)))

        selected = []
        selected_ids = set()
        tooling_count = 0

        core_candidates = [item for item in ranked_items if item.bucket == "core_llm_in_finance"]
        for item in core_candidates[:min_core]:
            selected.append(item)
            selected_ids.add(item.paper.paper_id)
            if item.bucket == "finance_llm_tooling":
                tooling_count += 1

        for item in ranked_items:
            if len(selected) >= target_count:
                break
            if item.paper.paper_id in selected_ids:
                continue
            if item.bucket == "finance_llm_tooling" and tooling_count >= max_tooling:
                continue
            selected.append(item)
            selected_ids.add(item.paper.paper_id)
            if item.bucket == "finance_llm_tooling":
                tooling_count += 1

        return selected

    def _backfill_when_short(self, selected, backfill_candidates):
        ranking_cfg = self.task_config.get("ranking") or {}
        balance_cfg = ranking_cfg.get("balance") or {}
        if not bool(balance_cfg.get("semantic_backfill_when_short", False)):
            return selected

        target_count = int(self.task_config.get("target_count", 5))
        if len(selected) >= target_count:
            return selected

        min_semantic = float(balance_cfg.get("semantic_backfill_min_score", 0) or 0)
        selected_ids = {item.paper.paper_id for item in selected}
        supplemented = list(selected)
        added = 0

        preferred = [item for item in backfill_candidates if float(getattr(item, "llm_relevance_score", 0) or 0) >= min_semantic]
        fallback = [item for item in backfill_candidates if item.paper.paper_id not in {cand.paper.paper_id for cand in preferred}]

        for pool in (preferred, fallback):
            for item in pool:
                if len(supplemented) >= target_count:
                    break
                if item.paper.paper_id in selected_ids:
                    continue
                supplemented.append(item)
                selected_ids.add(item.paper.paper_id)
                added += 1
            if len(supplemented) >= target_count:
                break

        if added:
            self.logger.info(
                "Backfilled %d paper(s) from semantic rejects due to low final count (preferred threshold=%s)",
                added,
                min_semantic,
            )
        return supplemented

    @staticmethod
    def _diagnostic_terms(matched_groups: Dict[str, List[str]], *group_names: str) -> List[str]:
        terms: List[str] = []
        seen = set()
        for group_name in group_names:
            for term in matched_groups.get(group_name, []) or []:
                key = str(term).lower()
                if key in seen:
                    continue
                seen.add(key)
                terms.append(str(term))
        return terms

    def _build_selected_diagnostics(self, selected):
        diagnostics = []
        for item in selected:
            paper = item.paper
            diagnostics.append(
                {
                    "paper_id": paper.paper_id,
                    "title": paper.title,
                    "source": paper.source,
                    "query_groups": list(item.query_groups or paper.raw_metadata.get("query_groups") or []),
                    "bucket": item.bucket,
                    "matched_llm_core_terms": self._diagnostic_terms(item.matched_groups, "llm_core_terms"),
                    "matched_finance_scope_terms": self._diagnostic_terms(item.matched_groups, "finance_scope_terms"),
                    "matched_finance_task_data_outcome_terms": self._diagnostic_terms(
                        item.matched_groups,
                        "finance_task_terms",
                        "finance_data_terms",
                        "strong_finance_outcome_terms",
                    ),
                    "rule_score": round(float(item.rule_score), 3),
                    "semantic_score": round(float(item.llm_relevance_score), 3),
                    "selection_reason": str(item.why_relevant or "").strip() or f"{item.bucket or 'unbucketed'} candidate",
                }
            )
        return diagnostics

    def _build_candidate_audit(
        self,
        recent_candidates: List[Paper],
        decisions_by_id,
        ranked_items,
        llm_results,
        selected_ids,
    ):
        ranked_map = {item.paper.paper_id: item for item in ranked_items}
        entries = []
        for paper in recent_candidates:
            decision = decisions_by_id.get(paper.paper_id)
            matched_groups = decision.matched_groups if decision else {}
            ranked_item = ranked_map.get(paper.paper_id)
            llm_item = llm_results.get(paper.paper_id, {}) if isinstance(llm_results, dict) else {}
            entries.append(
                {
                    "paper_id": paper.paper_id,
                    "title": paper.title,
                    "source": paper.source,
                    "date": paper.date,
                    "query_groups": list(paper.raw_metadata.get("query_groups") or []),
                    "bucket": (ranked_item.bucket if ranked_item else None) or (decision.bucket if decision else None),
                    "matched_keyword_groups": matched_groups,
                    "matched_llm_core_terms": self._diagnostic_terms(matched_groups, "llm_core_terms"),
                    "matched_finance_scope_terms": self._diagnostic_terms(matched_groups, "finance_scope_terms"),
                    "matched_finance_task_data_outcome_terms": self._diagnostic_terms(
                        matched_groups,
                        "finance_task_terms",
                        "finance_data_terms",
                        "strong_finance_outcome_terms",
                    ),
                    "rule_score": round(float(decision.score), 3) if decision else None,
                    "semantic_score": round(float(llm_item.get("relevance_score", 0) or 0), 3),
                    "semantic_top_pick_score": round(float(llm_item.get("top_pick_score", 0) or 0), 3),
                    "llm_is_relevant": llm_item.get("is_llm_finance"),
                    "final_score": round(float(ranked_item.final_score), 3) if ranked_item else None,
                    "selected": paper.paper_id in selected_ids,
                    "selection_reason": str((ranked_item.why_relevant if ranked_item else "") or "").strip() or None,
                    "filter_notes": list(decision.notes or []) if decision else [],
                }
            )
        return entries

    def run(self) -> PipelineRunResult:
        stats: Dict[str, object] = {}
        min_date = str(self.task_config.get("min_date") or "").strip()
        lookback_days = int(self.task_config.get("lookback_days", 90))
        fetched = dedupe_candidates(self._fetch_all())
        stats["source_counts_raw"] = dict(self.source_fetch_counts)
        stats["fetched_total"] = len(fetched)
        if min_date:
            recent_candidates = [paper for paper in fetched if on_or_after_date(self._paper_filter_date(paper), min_date)]
            stats["min_date"] = min_date
        else:
            recent_candidates = [paper for paper in fetched if within_lookback(self._paper_filter_date(paper), lookback_days)]
            stats["lookback_days"] = lookback_days
        stats["recent_candidates"] = len(recent_candidates)
        stats["recent_by_source"] = self._count_by_source(recent_candidates)
        self.logger.info("Source counts (raw): %s", stats["source_counts_raw"])
        self.logger.info("Source counts (after dedupe + recency): %s", stats["recent_by_source"])

        rule_matched: List[Paper] = []
        rule_scores: Dict[str, float] = {}
        matched_groups: Dict[str, Dict[str, List[str]]] = {}
        buckets: Dict[str, str] = {}
        bucket_counts_recent: Dict[str, int] = {}
        decisions_by_id = {}
        for paper in recent_candidates:
            decision = self.rule_engine.evaluate(paper)
            decisions_by_id[paper.paper_id] = decision
            if decision.bucket:
                bucket_counts_recent[decision.bucket] = bucket_counts_recent.get(decision.bucket, 0) + 1
            if decision.matched:
                rule_matched.append(paper)
                rule_scores[paper.paper_id] = decision.score
                matched_groups[paper.paper_id] = decision.matched_groups
                if decision.bucket:
                    buckets[paper.paper_id] = decision.bucket
        stats["rule_matched"] = len(rule_matched)
        stats["bucket_counts_recent"] = bucket_counts_recent
        stats["bucket_counts_rule_matched"] = self._count_bucket_map(buckets, rule_matched)
        self.logger.info("Bucket counts (recent): %s", stats["bucket_counts_recent"])
        self.logger.info("Bucket counts (after rule filter): %s", stats["bucket_counts_rule_matched"])

        llm_results = {}
        semantic_rejected: List[Paper] = []
        ranking_cfg = self.task_config.get("ranking") or {}
        if ranking_cfg.get("llm_semantic"):
            semantic_max = int(ranking_cfg.get("semantic_max_candidates", 20))
            llm_ranker = LLMFinanceSemanticRanker(self.backend, batch_size=int(ranking_cfg.get("semantic_batch_size", 8)))
            semantic_candidates = sorted(
                rule_matched,
                key=lambda item: (
                    self.BUCKET_PRIORITY.get(buckets.get(item.paper_id) or "", 0),
                    rule_scores.get(item.paper_id, 0.0),
                ),
                reverse=True,
            )[:semantic_max]
            try:
                llm_results = llm_ranker.evaluate(semantic_candidates)
                filtered_semantic = []
                for paper in rule_matched:
                    llm_item = llm_results.get(paper.paper_id)
                    if llm_item is None:
                        continue
                    if bool(llm_item.get("is_llm_finance", False)):
                        filtered_semantic.append(paper)
                    else:
                        semantic_rejected.append(paper)
                if filtered_semantic:
                    rule_matched = filtered_semantic
            except Exception as exc:
                self.logger.warning("LLM semantic ranking failed, fallback to rule-only ranking: %s", exc)
        stats["after_semantic"] = len(rule_matched)
        stats["bucket_counts_after_semantic"] = self._count_bucket_map(buckets, rule_matched)
        self.logger.info("Bucket counts (after semantic): %s", stats["bucket_counts_after_semantic"])

        ranked = combine_rank(
            rule_matched,
            rule_scores,
            matched_groups,
            self.task_config,
            llm_results=llm_results,
            buckets=buckets,
        )

        globally_new = []
        for item in ranked:
            match = self.state_store.was_pushed_globally(item.paper)
            if match:
                self.logger.info("Skip globally pushed paper %s via %s", item.paper.paper_id, match)
                continue
            globally_new.append(item)
        stats["globally_new"] = len(globally_new)

        selected = self._select_balanced(globally_new)
        if semantic_rejected:
            ranked_rejected = combine_rank(
                semantic_rejected,
                rule_scores,
                matched_groups,
                self.task_config,
                llm_results=llm_results,
                buckets=buckets,
            )
            rejected_new = []
            for item in ranked_rejected:
                match = self.state_store.was_pushed_globally(item.paper)
                if match:
                    self.logger.info("Skip globally pushed semantic-rejected paper %s via %s", item.paper.paper_id, match)
                    continue
                rejected_new.append(item)
            stats["semantic_rejected_count"] = len(semantic_rejected)
            stats["semantic_rejected_new"] = len(rejected_new)
            selected = self._backfill_when_short(selected, rejected_new)
        stats["selected_count"] = len(selected)
        stats["selected_bucket_counts"] = self._count_ranked_buckets(selected)
        if self.dry_run:
            stats["selected_diagnostics"] = self._build_selected_diagnostics(selected)
        self.logger.info("Selected bucket counts: %s", stats["selected_bucket_counts"])

        translations = self.translator.translate(
            [item.paper for item in selected],
            batch_size=int((self.task_config.get("translation") or {}).get("batch_size", 5)),
        )
        message_markdown = format_digest(self.task_config, selected, translations, stats)
        output_dir = LOG_DIR
        output_dir.mkdir(parents=True, exist_ok=True)
        run_suffix = self.log_path.stem.split(self.task_name + "-")[-1]
        output_path = output_dir / f"{self.task_name}-message-{run_suffix}.md"
        atomic_write_text(output_path, message_markdown)
        audit_output_path = output_dir / f"{self.task_name}-candidates-{run_suffix}.json"
        if self.dry_run:
            candidate_audit = self._build_candidate_audit(
                recent_candidates=recent_candidates,
                decisions_by_id=decisions_by_id,
                ranked_items=ranked,
                llm_results=llm_results,
                selected_ids={item.paper.paper_id for item in selected},
            )
            atomic_write_json(audit_output_path, {"task_name": self.task_name, "run_suffix": run_suffix, "candidates": candidate_audit})

        issue_date = self.archive_store.issue_date_today()
        projected_archive_state, archive_changed = self.archive_store.build_updated_state(issue_date, selected, translations)
        doc_markdown = format_doc_window(self.task_config, projected_archive_state, self.doc_target)

        archive_path = self.archive_store.archive_path
        doc_output_path = ARCHIVE_DIR / "rendered" / f"{self.task_name}-window.md"
        sent = False
        doc_written = False
        archive_updated = False
        state_updated = False
        success = True
        error = None
        notification_cfg = self.task_config.get("notification") or {}
        if self.dry_run:
            archive_path = output_dir / f"{self.task_name}-archive-{run_suffix}.json"
            doc_output_path = output_dir / f"{self.task_name}-doc-window-{run_suffix}.md"
            self.archive_store.write_preview(projected_archive_state, archive_path)
            atomic_write_text(doc_output_path, doc_markdown)
            self.logger.info("Dry run enabled; skip archive persist, doc write, chat notification, and state update.")
        else:
            if archive_changed:
                self.archive_store.persist(projected_archive_state)
                archive_updated = True
            elif not self.archive_store.archive_path.exists():
                self.archive_store.persist(projected_archive_state)
                archive_updated = True
            atomic_write_text(doc_output_path, doc_markdown)

            try:
                doc_result = self.doc_notifier.edit_markdown(str(self.doc_target["docid"]), doc_markdown)
                self.logger.info("Doc write result: %s", doc_result)
                doc_written = True
            except Exception as exc:
                success = False
                error = str(exc)
                self.logger.exception("Failed to update WeCom doc %s: %s", self.doc_target.get("docid"), exc)

            if doc_written and selected:
                for ranked_paper in selected:
                    self.state_store.mark_pushed(ranked_paper.paper, self.task_name)
                self.state_store.save()
                state_updated = True

            if doc_written and bool(notification_cfg.get("send_chat_notification", False)):
                try:
                    channel = str(notification_cfg.get("channel", "wecom"))
                    target = env_first(str(notification_cfg.get("target_env", "")), default=str(notification_cfg.get("target") or "")) or ""
                    if target:
                        account_id = env_first(
                            "PAPER_MONITOR_OPENCLAW_ACCOUNT_ID",
                            default=str(notification_cfg.get("account_id") or ""),
                        ) or None
                        short_message = str(
                            notification_cfg.get("short_message_template")
                            or f"{self.task_config.get('display_name', self.task_name)} 文档已更新"
                        )
                        send_result = self.notifier.send_markdown(
                            short_message.format(
                                display_name=self.task_config.get("display_name", self.task_name),
                                task_name=self.task_name,
                            ),
                            channel=channel,
                            target=target,
                            account_id=str(account_id) if account_id else None,
                            dry_run=False,
                        )
                        self.logger.info("Chat notification result: %s", send_result)
                        sent = True
                    else:
                        self.logger.warning("send_chat_notification=true but no chat target is configured")
                except Exception as exc:
                    self.logger.warning("Short chat notification failed: %s", exc)

        return PipelineRunResult(
            task_name=self.task_name,
            selected=selected,
            message_markdown=message_markdown,
            output_path=str(output_path),
            archive_path=str(archive_path),
            doc_output_path=str(doc_output_path),
            audit_path=str(audit_output_path) if self.dry_run else None,
            docid=str(self.doc_target.get("docid") or ""),
            dry_run=self.dry_run,
            sent=sent,
            doc_written=doc_written,
            archive_updated=archive_updated,
            state_updated=state_updated,
            success=success,
            error=error,
            stats=stats,
        )


def run_pipeline(config_path: Path, dry_run: bool = False) -> PipelineRunResult:
    return PaperMonitorPipeline(config_path=config_path, dry_run=dry_run).run()
