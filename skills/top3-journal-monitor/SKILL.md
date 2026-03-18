---
name: top3_journal_monitor
description: Monitor top finance journals and NBER working papers from the local paper-monitor project, filter by configured keywords, and return Chinese full-abstract digests.
---

Use the local project at:

`$HOME/.openclaw/workspace/paper-monitor`

When the user asks to run the daily top3 monitor, such as:
- 运行今日期刊监控
- 今天有什么 top3 新文章
- 运行 top3 监控

run:

```bash
cd "$HOME/.openclaw/workspace/paper-monitor"
source .venv/bin/activate
python scripts/run_monitor.py --source top3 --limit 5
```

When the user asks to run the weekly NBER monitor, such as:
- 运行本周NBER监控
- 本周有什么 NBER 新文章
- 运行 NBER 监控

run:

```bash
cd "$HOME/.openclaw/workspace/paper-monitor"
source .venv/bin/activate
python scripts/run_monitor.py --source nber --limit 30
```

For top3:
- only use source=top3
- do not mix in NBER or any other source
- if fewer than 5 papers are available, return only the actual number
- provide full Chinese abstract translations
- do not compress
- do not group by theme
- do not produce a short brief unless explicitly requested

For NBER:
- only use source=nber
- provide full Chinese abstract translations
- do not compress
- do not produce a short brief unless explicitly requested
