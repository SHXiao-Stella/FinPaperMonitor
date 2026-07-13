# FinPaperMonitor

FinPaperMonitor reproduces a production paper-monitoring workflow for fetching papers, filtering and ranking them, translating full abstracts into Chinese, and producing a push-ready Markdown digest. The included reference deployment uses OpenClaw for LLM access, scheduling, and channel delivery, and Feishu for document archiving and group messages. Neither OpenClaw nor Feishu is a required platform: the pipeline can be connected to another LLM backend, scheduler, and destination app.

## Monitors

| Monitor | Sources | Default schedule (Asia/Shanghai) |
| --- | --- | --- |
| FinTop3 | Journal of Finance, Journal of Financial Economics, Review of Financial Studies | Daily 08:00 |
| NBER | NBER Working Papers | Monday 08:10 |
| LLMFin | arXiv, Semantic Scholar, SSRN, NBER | Daily 08:20 |
| EconTop5 | AER, Econometrica, JPE, QJE, Review of Economic Studies | Daily 08:30 |

The project intentionally does not contain Feishu IDs, document tokens, OpenClaw credentials, API keys, runtime state, caches, or historical outputs.

## Delivery contract

The portable workflow contract is:

```text
fetch -> filter/dedupe -> rank -> translate -> render JSONL + Markdown
      -> publish through the chosen durable destination
      -> commit pushed IDs only after that publish succeeds
```

The included OpenClaw and Feishu adapter uses a Feishu document as the durable destination, commits state after document archiving succeeds, and then lets OpenClaw announce the same Markdown to a Feishu group. This makes document failures retryable and prevents a paper from being silently marked as delivered before it has been archived.

Another deployment can use system cron, a systemd timer, a Kubernetes CronJob, or an agent platform for scheduling, and any app API, webhook, email service, or message connector for delivery. Preserve the generate -> successful publish -> state commit order to avoid lost or repeatedly delivered papers.

## Requirements

- Python 3.10 or newer
- Network access to the enabled literature sources
- An LLM backend for semantic ranking and Chinese translation
- A scheduler and a delivery channel appropriate for the target server
- Linux or macOS; the included deployment scripts use Bash

For the reference deployment, the LLM backend, scheduler, and channel are provided by a working OpenClaw installation, while a Feishu app provides bot delivery and document/wiki read-write permissions. A deployment that uses different integrations does not need those OpenClaw or Feishu prerequisites.

The reference deployment is tested with Python 3.12 and OpenClaw 2026.4.24. LLM output is inherently nondeterministic, so matching the OpenClaw version, agent model, prompts, and configs gives functionally equivalent results, not guaranteed byte-identical text.

`scripts/bootstrap.sh` installs the tested direct and transitive versions from `requirements.lock`. Set `PAPER_MONITOR_REQUIREMENTS_FILE=requirements.txt` when deliberately testing newer compatible dependency versions.

## Installation

Every deployment starts with the same checkout and Python bootstrap:

```bash
git clone https://github.com/SHXiao-Stella/FinPaperMonitor.git
cd FinPaperMonitor
./scripts/bootstrap.sh
```

Bootstrap creates the virtual environment, runtime directories, `.env`, and local archive-config template. It does not install a scheduled job, call a literature API, write remote content, or send a message.

### OpenClaw and Feishu reference

Configure the local files created by bootstrap:

```text
.env
config/doc_archive.local.yml
```

At minimum, set the Feishu group ID in `.env`:

```dotenv
PAPER_MONITOR_FEISHU_TARGET=your_feishu_chat_id
```

Set one writable document or wiki target for each monitor in `config/doc_archive.local.yml`. These files are ignored by Git.

Validate the deployment without sending or writing remote content:

```bash
.venv/bin/python scripts/doctor.py
```

After the doctor passes, install the four OpenClaw cron jobs:

```bash
./scripts/install_openclaw_jobs.sh
openclaw cron list
```

The installer refuses to overwrite jobs with the same names. If creation fails partway through, it removes only the jobs created by that installer run.

See [docs/deploy-openclaw-feishu.md](docs/deploy-openclaw-feishu.md) for the full optional OpenClaw and Feishu setup. For another scheduler or destination, follow [docs/customization.md](docs/customization.md#replace-openclaw-or-feishu).

## Manual runs

Fetch and filter raw metadata without translating, archiving, or changing state:

```bash
.venv/bin/python scripts/run_monitor.py --source top3 --limit 5
.venv/bin/python scripts/run_monitor.py --source econ5 --limit 5
.venv/bin/python scripts/run_monitor.py --source nber --limit 30
```

Generate the same translated digest as production without archiving or changing pushed state:

```bash
.venv/bin/python scripts/run_delivery.py --source top3 --prepare-only
.venv/bin/python scripts/run_delivery.py --source llm_finance --prepare-only
```

A live delivery run archives the document and commits state. Its stdout is designed to be forwarded unchanged by OpenClaw:

```bash
.venv/bin/python scripts/run_delivery.py --source top3
```

Available sources are `top3`, `econ5`, `nber`, and `llm_finance`.

## Configuration

| File | Purpose |
| --- | --- |
| `config/keywords_top3.yml` | Top3 include/exclude rules |
| `config/keywords_econ5.yml` | EconTop5 include/exclude rules |
| `config/keywords_nber.yml` | NBER include/exclude rules |
| `config/keywords_common.yml` | LLMFin grouped rules, buckets, and scoring |
| `config/llm_finance_daily.yml` | LLMFin sources, queries, ranking, and schedule |
| `config/doc_archive.example.yml` | Safe template for local Feishu document targets |
| `.env.example` | Safe template for deployment environment variables |

`SEMANTIC_SCHOLAR_API_KEY` is optional. Without it, Semantic Scholar uses anonymous API limits.

For a focused change:

- Edit `config/keywords_top3.yml`, `config/keywords_econ5.yml`, or `config/keywords_nber.yml` to change the simple monitors' title/abstract include and exclude terms.
- Edit `config/keywords_common.yml` to change LLMFin term groups, filter profiles, buckets, and rule weights.
- Edit `config/llm_finance_daily.yml` to change LLMFin queries, enabled sources, lookback windows, ranking weights, and result count.
- Edit `paper_monitor/fetch.py` to change the Crossref journal/ISSN lists used by FinTop3 or EconTop5.
- Add or modify a fetcher under `src/sources/` and register it in `src/pipeline.py` to change the multi-source LLMFin source set.

Changing LLMFin from one finance topic to a completely different subject also requires updating the finance-specific semantic ranking and translation prompts in `src/ranker_llm.py` and `src/translator.py`; YAML changes alone are not sufficient. See [docs/customization.md](docs/customization.md) for the exact edit and validation paths.

## Replace OpenClaw or Feishu

OpenClaw is convenient because one installation can provide model access, cron-style scheduling, and adapters for many delivery channels. It is not part of the paper-fetching or filtering contract, and Feishu is only the repository's reference archive and message destination.

There are two separate replacement points:

1. **LLM backend:** the default `OpenClawAgentBackend` lives in `src/translator.py`. To run without OpenClaw at all, implement the same `BaseLLMBackend.generate_json(prompt, purpose)` interface for the chosen model API and select it in `build_llm_backend()`.
2. **Scheduler and destination:** have any scheduler run the generator, publish the rendered Markdown to the chosen app, and commit the matching JSONL state only after publishing succeeds. Do not run `scripts/run_delivery.py` without `--prepare-only` in a non-Feishu integration, because its live path intentionally invokes `scripts/append_feishu_doc.py`.

The generic adapter sequence and per-monitor commit commands are documented in [docs/customization.md](docs/customization.md#replace-openclaw-or-feishu). `scripts/doctor.py` and `scripts/install_openclaw_jobs.sh` validate and install only the reference OpenClaw/Feishu deployment; alternative adapters should provide their own credential and connectivity checks.

## Runtime state

All runtime files live under `data/` and are ignored by Git:

```text
data/
├── archive_out/             # selected JSONL, rendered Markdown, cron logs
├── cache/nber/              # NBER metadata cache
├── logs/                    # pipeline logs and candidate audits
├── state/                   # LLMFin global/task dedupe state
├── pushed_ids_top3.json
├── pushed_ids_econ5.json
├── pushed_ids_nber.json
└── doc_archive_runs.json    # idempotent Feishu document run keys
```

Back up `data/` before moving a deployment. Deleting it causes previously delivered papers to become eligible again.

## Repository layout

```text
paper_monitor/               # Crossref/NBER fetch, simple filtering, state
src/                         # multi-source LLMFin pipeline
src/sources/                 # arXiv, Semantic Scholar, SSRN, NBER, Top3
scripts/                     # entrypoints, archive, bootstrap, doctor, cron install
config/                      # public rules and safe examples
deploy/openclaw/             # OpenClaw cron prompt template
skills/                      # optional OpenClaw manual-run skill
tests/                       # offline unit and transaction tests
docs/                        # architecture and deployment guides
```

The active workflow is documented in [docs/architecture.md](docs/architecture.md). Topic, source, scheduler, and channel customization is documented in [docs/customization.md](docs/customization.md).

## Development

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q paper_monitor src scripts
bash scripts/audit_public_repo.sh .
```

Do not commit `.env`, `config/doc_archive.local.yml`, `~/.openclaw/openclaw.json`, cron exports, state files, logs, or generated digests.

## License

MIT. See [LICENSE](LICENSE).
