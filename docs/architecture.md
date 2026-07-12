# Architecture

## Active execution path

OpenClaw owns scheduling and final Feishu group delivery. Python owns all deterministic workflow steps before that delivery.

```text
OpenClaw cron (isolated agentTurn)
  -> scripts/run_delivery.py
     -> source-specific entrypoint
        -> fetch and normalize metadata
        -> apply keyword/date filters
        -> remove pushed records
        -> OpenClaw agent translation/ranking
        -> write selected JSONL and final Markdown
     -> scripts/append_feishu_doc.py
        -> authenticate with Feishu
        -> convert Markdown to document blocks
        -> append/prepend blocks
        -> commit archive run key
     -> source-specific --commit-from-file
        -> commit pushed IDs
  -> cron final response equals script stdout
  -> OpenClaw announce delivers it to Feishu
```

The cron agent does not write or summarize the digest. Its prompt only invokes `run_delivery.py` and forwards stdout.

## Top3, EconTop5, and NBER path

`scripts/run_monitor.py` is the common metadata pipeline:

1. `paper_monitor/fetch.py` queries Crossref by ISSN for Top3 and EconTop5 journals.
2. `paper_monitor/fetch_nber.py` downloads or reuses cached NBER metadata tables.
3. `paper_monitor/filtering.py` applies source-specific include/exclude keywords and date limits.
4. `paper_monitor/state.py` removes identifiers already present in the source state file.
5. The source daily/weekly wrapper calls `src/translator.py` through the OpenClaw agent backend and renders the final Chinese Markdown.

The wrappers intentionally do not update state while generating. They expose `--commit-from-file` so the delivery orchestrator can commit exactly the records that were archived.

## LLMFin path

`scripts/run_llm_finance_daily.py` calls `src/pipeline.py` in dry-run mode. In this project, dry-run means the pipeline generates candidates, LLM ranking, translations, audit data, and final Markdown but leaves remote document delivery and pushed-state commit to `run_delivery.py`.

The pipeline stages are:

1. Fetch from `src/sources/arxiv.py`, `semanticscholar.py`, `ssrn.py`, and `nber.py`.
2. Normalize URLs, titles, authors, and stable IDs in `src/normalize.py`.
3. Deduplicate records across sources in `src/dedupe.py`.
4. Apply grouped keyword profiles and buckets in `src/filter_rules.py`.
5. Evaluate semantic relevance with the OpenClaw agent in `src/ranker_llm.py`.
6. Combine rule, source, completeness, recency, and LLM scores.
7. Remove globally pushed records and balance the selected buckets.
8. Translate complete abstracts in `src/translator.py`.
9. Render the outbound digest in `src/formatter.py`.

`src/notifier_openclaw.py` and `src/notifier_wecom_doc.py` support an older live pipeline mode. They remain for compatibility, but the shipped cron path does not use them for remote writes.

## Transaction boundary

For each source and run date/week, `run_delivery.py` creates:

```text
data/archive_out/<source>-<run-key>.jsonl
data/archive_out/<source>-<run-key>.md
data/archive_out/<source>-<run-key>.cron.log
```

It then runs three ordered steps:

1. Generate JSONL and Markdown without state mutation.
2. Archive Markdown to Feishu. `data/doc_archive_runs.json` makes the document operation idempotent by run key.
3. Commit IDs from that exact JSONL file.

If step 1 or 2 fails, pushed state is unchanged. If step 3 fails, the next run sees the existing archive run key, skips duplicate document insertion, and retries the state commit.

Final chat delivery happens after state commit because it is performed by OpenClaw `announce`. A delivery-channel failure therefore requires checking `openclaw cron runs`; it cannot be rolled back by the Python process.

## State ownership

Top3, EconTop5, and NBER use independent identifier sets:

```text
data/pushed_ids_top3.json
data/pushed_ids_econ5.json
data/pushed_ids_nber.json
```

LLMFin uses title, URL, and source-aware dedupe state:

```text
data/state/pushed_ids_global.json
data/state/pushed_ids_llm_finance.json
```

Document idempotency is separate from paper dedupe:

```text
data/doc_archive_runs.json
```

None of these files belong in Git.

## External boundaries

The application talks to:

- Crossref for journal and SSRN metadata
- NBER official metadata tables
- arXiv Atom API
- Semantic Scholar Graph API
- OpenClaw agent CLI for semantic ranking and translation
- Feishu Open APIs for document conversion/insertion
- OpenClaw delivery for final group messages

Individual source failures in the LLMFin pipeline are logged and skipped. Translation failures fall back to `deep-translator` when available. A complete external outage can still produce an empty digest rather than fabricated papers.
