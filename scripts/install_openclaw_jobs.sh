#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$ROOT/.venv/bin/python"
PROMPT_TEMPLATE="$ROOT/deploy/openclaw/job_prompt.txt"

if [[ -f "$ROOT/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$ROOT/.env"
    set +a
fi

TARGET="${PAPER_MONITOR_FEISHU_TARGET:-}"
CHANNEL="${PAPER_MONITOR_OPENCLAW_CHANNEL:-feishu}"
ACCOUNT_ID="${PAPER_MONITOR_OPENCLAW_ACCOUNT_ID:-}"
TIMEZONE="${PAPER_MONITOR_TIMEZONE:-Asia/Shanghai}"
MODEL="${PAPER_MONITOR_OPENCLAW_MODEL:-}"

if [[ ! -x "$PYTHON" ]]; then
    echo "Missing virtualenv: run scripts/bootstrap.sh first" >&2
    exit 2
fi

if [[ -n "${PAPER_MONITOR_OPENCLAW_PATH:-}" ]]; then
    OPENCLAW="$PAPER_MONITOR_OPENCLAW_PATH"
elif command -v openclaw >/dev/null 2>&1; then
    OPENCLAW="$(command -v openclaw)"
elif [[ -x "$HOME/.openclaw/bin/openclaw" ]]; then
    OPENCLAW="$HOME/.openclaw/bin/openclaw"
else
    echo "OpenClaw was not found. Set PAPER_MONITOR_OPENCLAW_PATH in .env." >&2
    exit 2
fi

if [[ -z "$TARGET" ]]; then
    echo "PAPER_MONITOR_FEISHU_TARGET is required in .env" >&2
    exit 2
fi

if [[ ! -f "$PROMPT_TEMPLATE" ]]; then
    echo "Missing prompt template: $PROMPT_TEMPLATE" >&2
    exit 2
fi

NAMES=(
    "Top3 Finance Daily Monitor"
    "NBER Weekly Monitor"
    "LLM Finance Daily Monitor"
    "EconTop5 Daily Monitor"
)
SOURCES=("top3" "nber" "llm_finance" "econ5")
SCHEDULES=("0 8 * * *" "10 8 * * 1" "20 8 * * *" "30 8 * * *")
TIMEOUTS=("1200" "2400" "2400" "1200")

existing_json="$("$OPENCLAW" cron list --json)"
existing_names="$(printf '%s' "$existing_json" | "$PYTHON" -c '
import json, sys
payload = json.load(sys.stdin)
for job in payload.get("jobs", []):
    print(job.get("name", ""))
')"

for name in "${NAMES[@]}"; do
    if grep -Fxq "$name" <<<"$existing_names"; then
        echo "Refusing to overwrite existing cron job: $name" >&2
        echo "Remove or rename that job explicitly, then rerun this installer." >&2
        exit 2
    fi
done

created_ids=()
rollback() {
    local rc=$?
    if [[ "$rc" -ne 0 && "${#created_ids[@]}" -gt 0 ]]; then
        echo "Installation failed; removing jobs created by this run." >&2
        for id in "${created_ids[@]}"; do
            "$OPENCLAW" cron rm "$id" --json >/dev/null 2>&1 || true
        done
    fi
    exit "$rc"
}
trap rollback EXIT

for index in "${!NAMES[@]}"; do
    name="${NAMES[$index]}"
    source_name="${SOURCES[$index]}"
    prompt="$(<"$PROMPT_TEMPLATE")"
    prompt="${prompt//\{\{PROJECT_ROOT\}\}/$ROOT}"
    prompt="${prompt//\{\{PYTHON\}\}/$PYTHON}"
    prompt="${prompt//\{\{SOURCE\}\}/$source_name}"

    args=(
        "$OPENCLAW" cron add
        --name "$name"
        --cron "${SCHEDULES[$index]}"
        --tz "$TIMEZONE"
        --exact
        --session isolated
        --wake now
        --light-context
        --message "$prompt"
        --timeout-seconds "${TIMEOUTS[$index]}"
        --announce
        --channel "$CHANNEL"
        --to "$TARGET"
        --json
    )
    if [[ -n "$ACCOUNT_ID" ]]; then
        args+=(--account "$ACCOUNT_ID")
    fi
    if [[ -n "$MODEL" ]]; then
        args+=(--model "$MODEL")
    fi

    result="$("${args[@]}")"
    job_id="$(printf '%s' "$result" | "$PYTHON" -c '
import json, sys
payload = json.load(sys.stdin)
job_id = payload.get("id") or (payload.get("job") or {}).get("id")
if not job_id:
    raise SystemExit("OpenClaw response did not contain a job id")
print(job_id)
')"
    created_ids+=("$job_id")
    echo "Created: $name"
done

trap - EXIT
echo
echo "Installed ${#created_ids[@]} OpenClaw jobs in timezone $TIMEZONE."
echo "Review them with: $OPENCLAW cron list"
