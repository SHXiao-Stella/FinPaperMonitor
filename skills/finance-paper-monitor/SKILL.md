---
name: finance_paper_monitor
description: Run the local FinPaperMonitor for Top3 finance journals, Economics Top5 journals, NBER working papers, or LLM-in-finance papers and return the exact Chinese digest.
---

Use the checked-out FinPaperMonitor repository. During installation, replace `<FINPAPER_MONITOR_ROOT>` below with its absolute path.

Hard rules:

1. Use only `scripts/run_delivery.py`; do not recreate its transaction steps manually.
2. Never mix sources unless the user explicitly requests separate runs.
3. Wait for the command to finish.
4. Return stdout exactly. Do not summarize, rewrite, regroup, or add commentary.
5. A normal run archives to the configured Feishu document and commits pushed state.
6. Use `--prepare-only` only when the user explicitly asks for a preview with no archive/state changes.

Commands:

```bash
cd "<FINPAPER_MONITOR_ROOT>"

# Daily finance Top3
.venv/bin/python scripts/run_delivery.py --source top3

# Daily Economics Top5
.venv/bin/python scripts/run_delivery.py --source econ5

# Weekly NBER
.venv/bin/python scripts/run_delivery.py --source nber

# Daily LLM Finance
.venv/bin/python scripts/run_delivery.py --source llm_finance
```

Failure handling:

- If the command exits nonzero, return only the failure summary printed on stdout.
- Do not invent papers or report a successful archive/state update.
- Consult the corresponding ignored file under `data/archive_out/*.cron.log` for diagnostics.
