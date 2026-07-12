#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
REQUIREMENTS_FILE="${PAPER_MONITOR_REQUIREMENTS_FILE:-$ROOT/requirements.lock}"

"$PYTHON_BIN" - <<'PY'
import sys

if sys.version_info < (3, 10):
    raise SystemExit("Python 3.10 or newer is required")
print(f"Using Python {sys.version.split()[0]}")
PY

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
    "$PYTHON_BIN" -m venv "$ROOT/.venv"
fi

if [[ ! -f "$REQUIREMENTS_FILE" ]]; then
    REQUIREMENTS_FILE="$ROOT/requirements.txt"
fi
"$ROOT/.venv/bin/python" -m pip install -r "$REQUIREMENTS_FILE"

mkdir -p \
    "$ROOT/data/archive" \
    "$ROOT/data/archive_out" \
    "$ROOT/data/cache/nber" \
    "$ROOT/data/logs" \
    "$ROOT/data/state"

if [[ ! -f "$ROOT/.env" ]]; then
    cp "$ROOT/.env.example" "$ROOT/.env"
    echo "Created .env from .env.example"
fi

if [[ ! -f "$ROOT/config/doc_archive.local.yml" ]]; then
    cp "$ROOT/config/doc_archive.example.yml" "$ROOT/config/doc_archive.local.yml"
    echo "Created config/doc_archive.local.yml from the example"
fi

echo
echo "Bootstrap complete. Configure .env and config/doc_archive.local.yml, then run:"
echo "  $ROOT/.venv/bin/python $ROOT/scripts/doctor.py"
