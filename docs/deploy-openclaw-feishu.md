# Deploy with OpenClaw and Feishu

This guide configures the repository's optional reference integration on a new server. OpenClaw is used because it combines model access, cron-style jobs, and adapters for many delivery channels; Feishu is the reference document archive and message destination. Neither platform is required for the underlying paper monitor. See [customization.md](customization.md#replace-openclaw-or-feishu) for another LLM backend, scheduler, or destination app.

The steps below never require copying another server's `openclaw.json`, cron storage, state files, or credentials.

## 1. Install prerequisites

Install Python 3.10 or newer, Git, and OpenClaw. Configure an OpenClaw model provider and start its gateway according to the [OpenClaw documentation](https://docs.openclaw.ai/).

Verify the gateway:

```bash
openclaw --version
openclaw health
```

The original reference deployment was validated with OpenClaw 2026.4.24 and the `main` agent. Current OpenClaw releases may use a newer channel setup flow; for a fresh server, use a supported release and follow the current [OpenClaw Feishu channel guide](https://docs.openclaw.ai/channels/feishu). Its setup wizard requires OpenClaw 2026.5.29 or newer at the time of writing. To minimize output differences, use the same model for `PAPER_MONITOR_OPENCLAW_AGENT` on every server.

## 2. Configure the Feishu app and OpenClaw channel

Feishu separates three things that must all be configured:

1. **Application capabilities and API scopes** control which APIs the app may call.
2. **Resource permissions** control which specific group, document, or wiki node the app may access.
3. **OpenClaw channel configuration** tells OpenClaw which Feishu app credentials and destination to use.

Completing only one or two of these layers is not sufficient.

### 2.1 Create an enterprise self-built app

1. Open the [Feishu Developer Console](https://open.feishu.cn/app) and choose **Create enterprise self-built app** (创建企业自建应用).
2. Record the **App ID** and **App Secret** from **Credentials & Basic Info** (凭证与基础信息). The official [App ID guide](https://open.feishu.cn/document/faq/trouble-shooting/how-to-obtain-app-id) shows the current console location.
3. Under **Add application capability** (添加应用能力), enable **Bot** (机器人). Follow Feishu's [bot capability guide](https://open.feishu.cn/document/faq/trouble-shooting/how-to-enable-bot-ability).

Use an application bot, not a custom group webhook bot. This repository and OpenClaw call Feishu's authenticated app APIs; a custom webhook URL cannot replace the App ID/App Secret flow described here.

### 2.2 Grant the minimum API scopes

Open **Development Configuration > Permissions & Scopes** (开发配置 > 权限管理), search for the scopes below, and enable them. Feishu's [scope guide](https://open.feishu.cn/document/server-docs/application-scope/introduction) explains the approval model.

| Repository function | Minimum application scope | When it is needed |
| --- | --- | --- |
| Send the scheduled group message | `im:message:send_as_bot` | Required for Feishu message delivery. The broader `im:message` scope also covers it. |
| Convert rendered Markdown into document blocks | `docx:document.block:convert` | Required by every archive target. |
| Insert blocks into a new-style Feishu document | `docx:document:write_only` | Required by every archive target. The broader `docx:document` scope also covers it. |
| Resolve a wiki node to its backing document | `wiki:node:read` | Required only when `wiki_url` or `wiki_token` is used. The broader `wiki:wiki:readonly` or `wiki:wiki` scope also covers it. |

These are the minimum scopes used directly by this repository. OpenClaw may request additional scopes for interactive bot features such as receiving messages, mentions, user lookup, or richer message handling. Follow the current [OpenClaw Feishu guide](https://docs.openclaw.ai/channels/feishu) if you want those features.

An outbound-only scheduled push does **not** require an incoming message event subscription. If the bot should also respond to users, configure the event subscription required by OpenClaw, commonly `im.message.receive_v1`, and use the connection mode documented for your OpenClaw release. The current default is a WebSocket connection and does not require a public webhook URL.

### 2.3 Set availability, publish, and obtain approval

Adding a capability or changing a scope does not immediately update the installed app:

1. Configure the app's availability so the document owner and intended users are included.
2. Create a new application version under **Version Management & Release** (版本管理与发布).
3. Submit the version for release.
4. Ask the enterprise administrator to approve it when approval is required.
5. Repeat the release process after later scope, capability, event, or availability changes.

Feishu documents this lifecycle in its [self-built application development process](https://open.feishu.cn/document/develop-process/self-built-application-development-process). A frequent deployment failure is granting the correct scope in the console but forgetting to publish and approve a new version.

### 2.4 Connect the app to OpenClaw

For a current OpenClaw release, run:

```bash
openclaw channels login --channel feishu
openclaw gateway restart
openclaw health
```

Enter the App ID and App Secret when prompted. Older OpenClaw releases used manual configuration under `channels.feishu`; do not copy another server's complete `openclaw.json`. The file can contain unrelated providers, tokens, allowlists, and machine-specific settings.

For the document archive, `scripts/append_feishu_doc.py` looks for credentials in this order:

1. `channels.feishu.accounts.main.appId` and `appSecret` in `$HOME/.openclaw/openclaw.json`;
2. the first complete entry under `channels.feishu.accounts`;
3. legacy `channels.feishu.appId` and `appSecret` fields;
4. `FEISHU_APP_ID` and `FEISHU_APP_SECRET` in the repository's ignored `.env`.

The environment variables are fallback values; they do not override a complete account already found in `openclaw.json`. Keep credentials only in OpenClaw's local config or the ignored `.env`. Never commit them.

### 2.5 Add the bot to the destination group

1. Open the destination Feishu group and add the newly published application bot.
2. Confirm that the bot has permission to speak in the group.
3. Obtain the group's `chat_id`, which normally starts with `oc_`.
4. On Feishu client 7.60 or newer, the group settings page can display the group ID. Alternatively, use the [API Explorer](https://open.feishu.cn/api-explorer) or Feishu's [list chats API](https://open.feishu.cn/document/server-docs/group/chat/list) after adding the bot.

See Feishu's [chat ID description](https://open.feishu.cn/document/server-docs/group/chat/chat-id-description) for the supported lookup methods. Store the ID locally as `PAPER_MONITOR_FEISHU_TARGET`; do not publish it in an issue, screenshot, or example config.

## 3. Bootstrap the repository

```bash
git clone https://github.com/SHXiao-Stella/FinPaperMonitor.git
cd FinPaperMonitor
./scripts/bootstrap.sh
```

Bootstrap creates `.venv`, runtime directories, `.env`, and `config/doc_archive.local.yml`. It does not install cron jobs or send messages.

## 4. Configure delivery and archive targets

Edit `.env`:

```dotenv
PAPER_MONITOR_OPENCLAW_AGENT=main
PAPER_MONITOR_LLM_BACKEND=openclaw_agent
PAPER_MONITOR_FEISHU_TARGET=your_feishu_group_chat_id
PAPER_MONITOR_TIMEZONE=Asia/Shanghai
SEMANTIC_SCHOLAR_API_KEY=
```

`PAPER_MONITOR_FEISHU_TARGET` must be a delivery ID understood by the OpenClaw Feishu channel, normally the `oc_...` group `chat_id`. It is not the group's display name.

For a multi-account Feishu setup, also set:

```dotenv
PAPER_MONITOR_OPENCLAW_ACCOUNT_ID=your_openclaw_feishu_account
```

This account ID selects the OpenClaw account used for **message delivery**. It does not change the credentials selected by `scripts/append_feishu_doc.py` for document archiving. In a multi-account deployment, make the intended archive app the `main` account or verify that it is the first complete configured account. The `FEISHU_APP_ID` and `FEISHU_APP_SECRET` environment variables are used only when no complete app credentials are found in `openclaw.json`. Otherwise the message may be sent by one app while the archive is attempted by another.

### 4.1 Create new-style Feishu documents

Create one empty Feishu Doc for each source. Separate documents are easier to share and troubleshoot, although multiple sources may intentionally use the same document.

Use a **new-style document** whose URL contains `/docx/`:

```text
https://your-tenant.feishu.cn/docx/your_document_token
```

Do not use an old `/docs/` document, spreadsheet, Base, or other resource type. The archive code writes through Feishu's Docx block API.

For a direct document target, copy only the token after `/docx/`. For example, the local value for the URL above is `your_document_token`, not the complete URL.

### 4.2 Grant the app access to each document

API scopes and document sharing are independent. Even with `docx:document:write_only`, Feishu rejects a write unless the application also has edit access to that particular document.

For each direct document:

1. Open the document in Feishu.
2. Open **More** (更多) and choose **Add document application** (添加文档应用).
3. Search for the published self-built application.
4. Add it with document edit permission.

For the app to appear in the search results, it must be published, the document owner must be within the app's availability, and the app must already have an applicable cloud-document API scope. See Feishu's [cloud document FAQ](https://open.feishu.cn/document/server-docs/docs/faq) and [resource authorization guide](https://open.feishu.cn/document/faq/trouble-shooting/how-to-add-permissions-to-app).

### 4.3 Use a wiki target when needed

A wiki URL looks like:

```text
https://your-tenant.feishu.cn/wiki/your_node_token
```

The value after `/wiki/` is a wiki node token, not the backing document token. When `wiki_url` or `wiki_token` is configured, the archive script calls Feishu's [get wiki node API](https://open.feishu.cn/document/server-docs/docs/wiki-v2/space-node/get_node), reads its `obj_token`, and then writes to that document.

The target wiki node must point to a new-style `docx` document. Grant both:

1. the `wiki:node:read` application scope and permission to read the specific wiki node;
2. document edit permission on the node's backing document.

You can add the application directly as a collaborator on the node, or grant a group containing the application bot access to the knowledge space and add that group as a space member or administrator. Feishu describes these options in its [Wiki FAQ](https://open.feishu.cn/document/server-docs/docs/wiki-v2/wiki-qa).

### 4.4 Configure the local archive map

Edit `config/doc_archive.local.yml` and configure one target for every enabled source key: `top3`, `econ5`, `nber`, and `llm_finance`. A direct document token is the simplest option:

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

Use exactly one target field, `doc_token`, `wiki_url`, or `wiki_token`, per source. `prepend: true` inserts the newest issue at the top. Without it, new issues are appended.

The archive script obtains a short-lived tenant access token from Feishu for each run and then uses the official [Markdown conversion API](https://open.feishu.cn/document/ukTMukTMukTM/uUDN04SN0QjL1QDN/document-docx/docx-v1/document/convert) and [create nested blocks API](https://open.feishu.cn/document/ukTMukTMukTM/uUDN04SN0QjL1QDN/document-docx/docx-v1/document-block-descendant/create). You do not need to create or store a tenant access token manually.

## 5. Run the deployment doctor

```bash
.venv/bin/python scripts/doctor.py
```

The doctor verifies local dependencies, required configs, the presence of Feishu credentials and targets, the OpenClaw binary, and gateway health. It does not call source APIs, write Feishu documents, change state, or send group messages. It therefore cannot confirm that the Feishu app version is published, the scopes are approved, the bot is in the target group, or the app has access to a particular document/wiki node.

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

## 9. Troubleshooting Feishu

### The group message is not delivered

Check all of the following:

- the application version containing the Bot capability and `im:message:send_as_bot` scope is published and approved;
- `PAPER_MONITOR_FEISHU_TARGET` is the `oc_...` `chat_id`, not a group name, app ID, open ID, or document token;
- the application bot is a member of that group and is allowed to speak;
- `PAPER_MONITOR_OPENCLAW_ACCOUNT_ID` names the intended configured account when multiple Feishu accounts exist;
- `openclaw health` succeeds and the Feishu channel is logged in.

Feishu's [send message API reference](https://open.feishu.cn/document/server-docs/im-v1/message/create) documents the bot capability, scope, group-membership requirement, `chat_id` receiver type, and rate limits.

### Document conversion or writing reports a missing scope

In **Permissions & Scopes**, confirm that the published app version contains:

```text
docx:document.block:convert
docx:document:write_only
```

A permission error such as `99991672` generally means the app lacks an API scope or the newly granted scope has not yet been published and approved. After changing permissions, release a new app version before retrying.

### Document writing returns HTTP 403 or permission denied

The API scope is present, but the app probably lacks access to the specific document. Add the published app through **More > Add document application** (更多 > 添加文档应用), grant edit permission, and confirm that the target is a `/docx/` document.

### Wiki lookup returns error 131006

Feishu defines `131006` as permission denied for the wiki node lookup. Confirm both the `wiki:node:read` scope and actual access to the target node/knowledge space. Then confirm edit permission on the backing Docx document.

### Archiving uses the wrong Feishu app

`PAPER_MONITOR_OPENCLAW_ACCOUNT_ID` affects the OpenClaw message command, but it does not select credentials inside `append_feishu_doc.py`. Review the credential order in [section 2.4](#24-connect-the-app-to-openclaw) and make the intended archive app `main`. The `FEISHU_APP_ID` and `FEISHU_APP_SECRET` values in `.env` are only a fallback when `openclaw.json` contains no complete Feishu account.

### The bot cannot be found when sharing a document

Confirm that:

- the app has been published and approved;
- the document owner is included in the app's availability;
- the app has at least one applicable cloud-document API scope;
- you are searching under **Add document application**, not the normal user-only collaborator picker.

## Official references

All links below are first-party Feishu or OpenClaw documentation:

- [Feishu self-built application development process](https://open.feishu.cn/document/develop-process/self-built-application-development-process)
- [Feishu application scope and approval guide](https://open.feishu.cn/document/server-docs/application-scope/introduction)
- [Feishu tenant access token for self-built apps](https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token_internal)
- [Feishu send message API](https://open.feishu.cn/document/server-docs/im-v1/message/create)
- [Feishu chat ID description](https://open.feishu.cn/document/server-docs/group/chat/chat-id-description)
- [Feishu cloud document FAQ](https://open.feishu.cn/document/server-docs/docs/faq)
- [Feishu resource authorization guide](https://open.feishu.cn/document/faq/trouble-shooting/how-to-add-permissions-to-app)
- [Feishu Markdown/HTML to document blocks API](https://open.feishu.cn/document/ukTMukTMukTM/uUDN04SN0QjL1QDN/document-docx/docx-v1/document/convert)
- [Feishu create nested document blocks API](https://open.feishu.cn/document/ukTMukTMukTM/uUDN04SN0QjL1QDN/document-docx/docx-v1/document-block-descendant/create)
- [Feishu get wiki node API](https://open.feishu.cn/document/server-docs/docs/wiki-v2/space-node/get_node)
- [Feishu Wiki FAQ](https://open.feishu.cn/document/server-docs/docs/wiki-v2/wiki-qa)
- [Feishu API Explorer](https://open.feishu.cn/api-explorer)
- [OpenClaw Feishu channel guide](https://docs.openclaw.ai/channels/feishu)

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
