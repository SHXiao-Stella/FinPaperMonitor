# Deploy with OpenClaw and Feishu

This guide configures the repository's optional reference integration on a new server. OpenClaw is used because it combines model access, cron-style jobs, and adapters for many delivery channels; Feishu is the reference document archive and message destination. Neither platform is required for the underlying paper monitor. See [customization.md](customization.md#replace-openclaw-or-feishu) for another LLM backend, scheduler, or destination app.

The steps below never require copying another server's `openclaw.json`, cron storage, state files, or credentials.

## 1. Install prerequisites

Install Python 3.10 or newer, Git, and OpenClaw. Configure an OpenClaw model provider and start its gateway according to the OpenClaw documentation.

Verify the gateway:

```bash
openclaw --version
openclaw health
```

The reference deployment uses OpenClaw 2026.4.24 and the `main` agent. To minimize output differences, use the same model for `PAPER_MONITOR_OPENCLAW_AGENT` on every server.

## 2. Configure the Feishu app

Create or reuse a Feishu app and configure it as the OpenClaw Feishu channel. The app needs capabilities for:

- sending bot messages to the target group;
- obtaining a tenant access token;
- converting Markdown into document blocks;
- inserting document blocks;
- reading wiki node metadata when a wiki URL/token is used.

Add the bot to the destination group. Grant the app access to every document or wiki page used for archiving. Feishu authorization and document sharing are both required; having only API scopes is not sufficient.

Keep `appId` and `appSecret` in `$HOME/.openclaw/openclaw.json`, or place them in the ignored `.env` as `FEISHU_APP_ID` and `FEISHU_APP_SECRET`. Never commit either file.

## 3. Bootstrap the repository

```bash
git clone https://github.com/SHXiao-Stella/FinPaperMonitor.git
cd FinPaperMonitor
./scripts/bootstrap.sh
```

Bootstrap creates `.venv`, runtime directories, `.env`, and `config/doc_archive.local.yml`. It does not install cron jobs or send messages.

## 4. Configure local targets

Edit `.env`:

```dotenv
PAPER_MONITOR_OPENCLAW_AGENT=main
PAPER_MONITOR_LLM_BACKEND=openclaw_agent
PAPER_MONITOR_FEISHU_TARGET=your_feishu_chat_id
PAPER_MONITOR_TIMEZONE=Asia/Shanghai
SEMANTIC_SCHOLAR_API_KEY=
```

`PAPER_MONITOR_FEISHU_TARGET` must be a delivery ID understood by the OpenClaw Feishu channel, not a group display name.

For a multi-account Feishu setup, also set:

```dotenv
PAPER_MONITOR_OPENCLAW_ACCOUNT_ID=your_openclaw_feishu_account
```

Edit `config/doc_archive.local.yml` and configure one target per source. A direct document token is the simplest option:

```yaml
top3:
  doc_token: "your_top3_document_token"
econ5:
  doc_token: "your_econ5_document_token"
nber:
  doc_token: "your_nber_document_token"
llm_finance:
  wiki_url: "https://your-tenant.feishu.cn/wiki/your_node_token"
  prepend: true
```

`prepend: true` inserts the newest issue at the top. Without it, new issues are appended.

## 5. Run the deployment doctor

```bash
.venv/bin/python scripts/doctor.py
```

The doctor verifies local dependencies, required configs, the presence of Feishu credentials and targets, the OpenClaw binary, and gateway health. It does not call source APIs, write Feishu documents, change state, or send group messages.

Resolve all failures before installing cron jobs.

## 6. Preview a digest

Preview mode fetches real metadata and calls the configured LLM, but it does not archive or commit state:

```bash
.venv/bin/python scripts/run_delivery.py --source top3 --prepare-only
```

The command may take several minutes. Its JSONL, Markdown, audit files, and logs are written under `data/` and remain ignored by Git.

## 7. Install OpenClaw jobs

```bash
./scripts/install_openclaw_jobs.sh
openclaw cron list
```

The installer uses the current absolute checkout path in each prompt and creates four isolated `agentTurn` jobs. It does not edit OpenClaw's internal JSON files directly.

Default schedules in `Asia/Shanghai`:

```text
0 8 * * *     Top3 Finance Daily Monitor
10 8 * * 1    NBER Weekly Monitor
20 8 * * *    LLM Finance Daily Monitor
30 8 * * *    EconTop5 Daily Monitor
```

The installer stops if any of these names already exist. Review or explicitly remove an old job before reinstalling; automatic replacement could disrupt an existing deployment.

## 8. Test one live job

First obtain its ID with `openclaw cron list`. A live cron test writes the document, commits state, and sends the group message:

```bash
openclaw cron run <job-id> --expect-final --timeout 2500000
openclaw cron runs <job-id>
```

Use a test group and test documents for the first deployment. Do not run a live test against production targets unless you intend to create a real issue.

## Upgrades

Back up runtime state, pull code, and refresh Python dependencies:

```bash
tar -czf finpaper-state-backup.tgz data
git pull --ff-only
./scripts/bootstrap.sh
.venv/bin/python scripts/doctor.py
```

Existing cron prompts continue to work as long as the checkout path does not change. If the repository moves, explicitly remove the four old jobs and rerun `scripts/install_openclaw_jobs.sh` from the new path.

## Security checklist

Before pushing a fork, run:

```bash
bash scripts/audit_public_repo.sh .
git status --short
git diff --cached
```

Never publish:

- `.env` or `config/doc_archive.local.yml`;
- `$HOME/.openclaw/openclaw.json`;
- OpenClaw cron exports or run logs;
- Feishu chat IDs, open IDs, app credentials, document/wiki tokens;
- `data/`, except its tracked `.gitkeep` placeholder.
