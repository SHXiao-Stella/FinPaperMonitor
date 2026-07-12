# FinPaperMonitor

FinPaperMonitor reproduces a production paper-monitoring workflow built around Python, OpenClaw, and Feishu. It fetches papers, filters and ranks them, translates full abstracts into Chinese, archives the exact outbound Markdown to a Feishu document, and then delivers the same text to a Feishu group.

## Monitors

| Monitor | Sources | Default schedule (Asia/Shanghai) |
| --- | --- | --- |
| FinTop3 | Journal of Finance, Journal of Financial Economics, Review of Financial Studies | Daily 08:00 |
| NBER | NBER Working Papers | Monday 08:10 |
| LLMFin | arXiv, Semantic Scholar, SSRN, NBER | Daily 08:20 |
| EconTop5 | AER, Econometrica, JPE, QJE, Review of Economic Studies | Daily 08:30 |

The project intentionally does not contain Feishu IDs, document tokens, OpenClaw credentials, API keys, runtime state, caches, or historical outputs.

## Delivery contract

Every scheduled run follows the same order:

```text
fetch -> filter/dedupe -> rank -> translate -> render JSONL + Markdown
      -> archive Markdown to Feishu document
      -> commit pushed IDs
      -> OpenClaw announces the same Markdown to the Feishu group
```

Document archiving must succeed before pushed state is committed. This makes document failures retryable and prevents a paper from being silently marked as delivered before it has been archived.

## Requirements

- Python 3.10 or newer
- A working OpenClaw gateway and model provider
- An OpenClaw Feishu channel
- A Feishu app with bot delivery and document/wiki read-write permissions
- Linux or macOS; the deployment scripts use Bash

The reference deployment is tested with Python 3.12 and OpenClaw 2026.4.24. LLM output is inherently nondeterministic, so matching the OpenClaw version, agent model, prompts, and configs gives functionally equivalent results, not guaranteed byte-identical text.

`scripts/bootstrap.sh` installs the tested direct and transitive versions from `requirements.lock`. Set `PAPER_MONITOR_REQUIREMENTS_FILE=requirements.txt` when deliberately testing newer compatible dependency versions.

## Quick start

```bash
git clone https://github.com/SHXiao-Stella/FinPaperMonitor.git
cd FinPaperMonitor
./scripts/bootstrap.sh
```

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

See [docs/deploy-openclaw-feishu.md](docs/deploy-openclaw-feishu.md) for the full OpenClaw and Feishu setup.

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

The active workflow is documented in [docs/architecture.md](docs/architecture.md).

## Development

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q paper_monitor src scripts
bash scripts/audit_public_repo.sh .
```

Do not commit `.env`, `config/doc_archive.local.yml`, `~/.openclaw/openclaw.json`, cron exports, state files, logs, or generated digests.

## License

MIT. See [LICENSE](LICENSE).
