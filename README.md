# Finance Paper Monitor

A local monitoring project for:

- Top 3 finance journals:
  - Journal of Finance (JF)
  - Journal of Financial Economics (JFE)
  - Review of Financial Studies (RFS)
- NBER Working Papers

It fetches metadata, filters papers by keyword rules, avoids duplicate pushes using local state files, and can be used together with OpenClaw / Feishu for scheduled delivery.

---

## Features

- Monitor Top 3 finance journals from Crossref
- Monitor NBER working papers from NBER metadata tables
- Separate keyword configs for different sources
- Separate pushed-ID state files for different sources
- Local cache for NBER metadata
- JSON-line output for downstream agent / bot processing
- Easy integration with OpenClaw skills and cron jobs

---

## Repository structure

```text
finance-paper-monitor-public/
├── paper_monitor/
├── scripts/
├── config/
├── skills/
│   └── top3-journal-monitor/
│       └── SKILL.md
├── data/
│   └── .gitkeep
├── requirements.txt
└── README.md
```

---

## Requirements

- Python 3.10+
- Linux / macOS recommended
- Optional:
  - OpenClaw
  - Feishu bot integration

---

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## Keyword configuration

Top3 and NBER can use different keyword files.

Expected config files:

- `config/keywords_top3.yml`
- `config/keywords_nber.yml`

Example format:

```yaml
include:
  - asset pricing
  - corporate finance
exclude:
  - corrigendum
  - editorial
```

---

## Run manually

### Run Top3 monitor

```bash
source .venv/bin/activate
python scripts/run_monitor.py --source top3 --limit 5
```

### Run NBER monitor

```bash
source .venv/bin/activate
python scripts/run_monitor.py --source nber --limit 30
```

---

## Output behavior

The script prints one JSON object per selected paper.

Typical fields include:

- `id`
- `source`
- `title`
- `authors`
- `journal`
- `published`
- `DOI`
- `abstract`
- `url`

This makes it easy for an agent to:
- translate abstracts into Chinese
- summarize
- post to chat tools
- log results

---

## State files

The project may create local runtime files such as:

- `data/pushed_ids_top3.json`
- `data/pushed_ids_nber.json`
- `data/cache/nber/*`

These files should not be committed to a public repository.

---

## OpenClaw skill integration

A sample skill file is included at:

```text
skills/top3-journal-monitor/SKILL.md
```

This skill tells OpenClaw how to run:

- daily Top3 monitoring
- weekly NBER monitoring

---

## Privacy / publishing notes

Before publishing your own fork or deployment:

- remove all personal chat IDs
- remove all private session IDs
- remove all bot open IDs
- remove runtime cache/state files
- remove local cron/job files
- remove private config values or tokens
- review logs before sharing

---

## License

Add your preferred open-source license here, for example:

- MIT
- Apache-2.0
- GPL-3.0

