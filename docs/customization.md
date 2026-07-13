# Customize monitors and delivery

This guide identifies the configuration and code boundaries to change when adapting FinPaperMonitor to another topic, keyword set, literature source, scheduler, or destination app.

## Choose the smallest change

| Goal | Primary edit |
| --- | --- |
| Tune Top3, EconTop5, or NBER filtering | `config/keywords_<monitor>.yml` |
| Tune the existing LLM-in-finance topic | `config/keywords_common.yml` and `config/llm_finance_daily.yml` |
| Change the Top3 or EconTop5 journal list | `paper_monitor/fetch.py` |
| Enable or disable an existing LLMFin API source | `config/llm_finance_daily.yml` |
| Add a new LLMFin API source | `src/sources/` and `src/pipeline.py` |
| Replace LLM-in-finance with a different subject | YAML plus the prompts in `src/ranker_llm.py` and `src/translator.py` |
| Use another scheduler or destination app | Add an adapter around the generated Markdown and JSONL artifacts |

Keep configuration-only changes in YAML where possible. Code changes are necessary when the source protocol, normalized data model, semantic prompt, or delivery transaction changes.

## Top3, EconTop5, and NBER keywords

The three simple monitors have independent rule files:

| Monitor | Rule file |
| --- | --- |
| FinTop3 | `config/keywords_top3.yml` |
| EconTop5 | `config/keywords_econ5.yml` |
| NBER | `config/keywords_nber.yml` |

Each file contains two lists:

```yaml
include:
  - asset pricing
  - market microstructure
exclude:
  - corrigendum
  - editorial
```

`paper_monitor/filtering.py` lowercases and searches the combined title and abstract. Matching is currently a case-insensitive substring test:

- when `include` is nonempty, at least one include term must match;
- when `include` is empty, all records pass the include stage;
- any matching `exclude` term rejects the record;
- publication date, record type, and pushed-state deduplication are applied separately.

Preview the metadata filter without translation, remote writes, or state changes:

```bash
.venv/bin/python scripts/run_monitor.py --source top3 --limit 20
.venv/bin/python scripts/run_monitor.py --source econ5 --limit 20
.venv/bin/python scripts/run_monitor.py --source nber --limit 30
```

## LLMFin topic and queries

The multi-source LLMFin monitor has two main configuration layers.

`config/keywords_common.yml` controls:

- keyword `groups`;
- group and field `scoring` weights;
- topic `buckets` and their required, optional, and penalty groups;
- reusable `profiles`, selected by `filter_profile` in the task config.

`config/llm_finance_daily.yml` controls:

- `display_name`, `target_count`, `min_count`, and `lookback_days`;
- the enabled `sources` list;
- `query_groups` sent to search APIs;
- per-source windows, limits, timeouts, and enabled query groups under `source_settings`;
- heuristic, semantic, source, bucket, query-group, recency, and balance weights under `ranking`;
- the selected `filter_profile`.

For a narrower or broader LLM-in-finance monitor, the usual sequence is:

1. Update phrases in `groups`.
2. Update bucket requirements and the selected profile.
3. Update API search strings in `query_groups`.
4. Update each source's `query_groups`, windows, and request limits.
5. Rebalance `ranking` weights and `target_count` after reviewing candidate audit logs.

For a completely different subject, also update these code-level assumptions:

- `src/ranker_llm.py`: the prompt in `LLMFinanceSemanticRanker._evaluate_batch()` explicitly defines finance-first LLM relevance.
- `src/translator.py`: `PaperTranslator` contains a finance-specific translation prompt and terminology replacements.
- `src/formatter.py`: the empty-result message currently names LLM Finance.
- `src/pipeline.py`: `BUCKET_PRIORITY` and balanced selection refer to the current bucket names. Update them if bucket names or selection policy change.

Treat `task_name: llm_finance_daily` as an internal identifier, not a display label. Renaming it also requires updating task-to-state mappings in `src/pipeline.py` and the wrapper/state wiring in `scripts/run_llm_finance_daily.py`. Change `display_name` alone when only the visible title should change.

Preview the full configured pipeline without remote writes or state changes:

```bash
.venv/bin/python scripts/run_delivery.py --source llm_finance --prepare-only
```

This command fetches live metadata and calls the configured LLM backend. Review the ignored candidate audit and output files under `data/` before installing a schedule.

## Literature sources

### Change journal lists

FinTop3 and EconTop5 fetch Crossref by ISSN. Edit these maps in `paper_monitor/fetch.py`:

```text
JOURNAL_ISSN_MAP
ECON5_JOURNAL_ISSN_MAP
```

Use a journal's correct ISSN and keep the display name unique. These maps are used by the simple scheduled wrappers. `src/sources/top3.py` has a separate `CROSSREF_JOURNALS` map used only if `top3` is enabled inside the multi-source pipeline; update both locations when both paths should use the same journal set.

The NBER simple monitor is implemented in `paper_monitor/fetch_nber.py`. Its official metadata endpoints, cache behavior, and normalized fields live there.

### Enable or disable built-in multi-source fetchers

The shipped LLMFin fetchers are:

```text
src/sources/arxiv.py
src/sources/semanticscholar.py
src/sources/ssrn.py
src/sources/nber.py
src/sources/top3.py
```

Add or remove their keys under `sources` in `config/llm_finance_daily.yml`. For every enabled source, retain the corresponding `source_settings` block. arXiv, Semantic Scholar, and SSRN consume the configured `query_groups`; NBER uses its own metadata feed and window settings.

### Add a new multi-source fetcher

1. Add `src/sources/<name>.py` with a function shaped like `fetch_<name>_candidates(task_config, logger) -> list[Paper]`.
2. Normalize each record into `src.models.Paper`, assign a stable `paper_id`, preserve source metadata in `raw_metadata`, and call the existing normalization helpers.
3. Import the function and add `<name>: fetch_<name>_candidates` to `SOURCE_HANDLERS` in `src/pipeline.py`.
4. Add the source key to `sources` and its limits, timeouts, lookback, and query-group settings to `config/llm_finance_daily.yml`.
5. Add offline parser/normalization tests and preview with a small request limit before enabling the schedule.

If the new source is a new top-level monitor rather than another LLMFin input, it also needs a generation/commit entrypoint and a `DeliverySpec` in `scripts/run_delivery.py`. Only reference OpenClaw/Feishu deployments additionally need matching entries in `scripts/install_openclaw_jobs.sh`, `scripts/append_feishu_doc.py`, and `config/doc_archive.local.yml`.

## Replace OpenClaw or Feishu

OpenClaw and Feishu fill several independent roles in the reference deployment:

| Role | Reference implementation | Replacement boundary |
| --- | --- | --- |
| Semantic ranking and translation | `OpenClawAgentBackend` in `src/translator.py` | Any backend implementing `BaseLLMBackend.generate_json()` |
| Scheduling | jobs created by `scripts/install_openclaw_jobs.sh` | cron, systemd, Kubernetes, another agent scheduler, or a hosted workflow |
| Durable document archive | `scripts/append_feishu_doc.py` | A database, object store, document API, or no separate archive |
| Final message delivery | OpenClaw channel announcement to Feishu | Any app API, webhook, email sender, or channel connector |

OpenClaw is useful because it already adapts many model providers and delivery channels. It is not required by the fetch/filter model. Feishu is only one possible destination.

### Replace only scheduling or delivery

An external adapter should use preview mode as the generation step, publish the generated Markdown, and commit the matching JSONL only after the chosen durable publish succeeds. For example, a Top3 adapter follows this sequence:

```bash
RUN_DATE="$(date +%F)"

.venv/bin/python scripts/run_delivery.py \
  --source top3 \
  --date "$RUN_DATE" \
  --prepare-only

MARKDOWN="data/archive_out/top3-${RUN_DATE}.md"
SELECTED="data/archive_out/top3-${RUN_DATE}.jsonl"

your_sender_command "$MARKDOWN"
.venv/bin/python scripts/run_top3_daily.py --commit-from-file "$SELECTED"
```

Replace `your_sender_command` with a script or agent action that returns nonzero on failure. Never run the commit command when publishing fails.

The commit entrypoints are:

| Source | Commit entrypoint | Artifact suffix |
| --- | --- | --- |
| `top3` | `scripts/run_top3_daily.py` | calendar date |
| `econ5` | `scripts/run_econ5_daily.py` | calendar date |
| `nber` | `scripts/run_nber_weekly.py` | ISO year and week, for example `2026-W29` |
| `llm_finance` | `scripts/run_llm_finance_daily.py` | calendar date |

Add a per-source lock so overlapping schedules cannot publish the same issue concurrently. Give the destination operation an idempotency key such as `<source>-<run-suffix>` when its API supports one. If publishing succeeds but state commit fails, an idempotent destination lets the next run retry without creating a duplicate.

Do not call `scripts/run_delivery.py` without `--prepare-only` from a non-Feishu adapter. Its live path intentionally runs `scripts/append_feishu_doc.py` before committing state.

### Remove OpenClaw completely

Preview mode still uses the configured LLM backend. The default backend invokes `openclaw agent`, so changing only the scheduler does not remove the OpenClaw runtime dependency.

To use a model API directly:

1. Implement a `BaseLLMBackend` subclass in `src/translator.py` or a new backend module.
2. Implement `generate_json(prompt, purpose)` so it returns the parsed JSON object or array requested by the prompt and raises on provider or parse failure.
3. Register the backend in `build_llm_backend()` and select it with `PAPER_MONITOR_LLM_BACKEND`.
4. Store provider credentials only in `.env` or another ignored secret store.
5. Test semantic ranking and translation responses before scheduling live delivery.

`MockLLMBackend` is intended for offline development and does not reproduce production-quality ranking or translation.

### Change schedules

For the reference OpenClaw deployment, the schedules actually installed are the `SCHEDULES` values in `scripts/install_openclaw_jobs.sh`. `config/monitors.example.yml`, the task `schedule` block, and `DeliverySpec.schedule` document the defaults but do not update existing OpenClaw jobs automatically.

For another scheduler, its own job or timer definition is authoritative. Run all schedules from the repository root, set the intended timezone explicitly, keep `data/` on persistent storage, and capture stderr plus the ignored logs under `data/archive_out/`.

## Validate a customization

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q paper_monitor src scripts
bash scripts/audit_public_repo.sh .
```

Then run the affected monitor with `--prepare-only` and inspect its Markdown and JSONL artifacts. Use a test destination for the first live publish. `scripts/doctor.py` validates only the included OpenClaw/Feishu reference deployment; a custom integration should add equivalent checks for its LLM provider, scheduler, credentials, destination permissions, and persistent state directory.
