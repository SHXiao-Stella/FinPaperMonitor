#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-.}"

echo "==> Checking possible private information under: $ROOT"
echo

echo "[1] Searching for likely chat/session/open ids..."
grep -RInE 'oc_[a-z0-9]{16,}|ou_[a-z0-9]{16,}|session=agent:|chat:[A-Za-z0-9_:.-]+' "$ROOT" \
  --exclude-dir=.git \
  --exclude-dir=.venv \
  --exclude='*.pyc' || true
echo

echo "[2] Searching for home-directory leakage..."
grep -RInE '/home/[^/ ]+|/Users/[^/ ]+' "$ROOT" \
  --exclude-dir=.git \
  --exclude-dir=.venv \
  --exclude='*.pyc' || true
echo

echo "[3] Searching for tokens / secrets / app ids..."
grep -RInE 'token|secret|app_id|app_secret|OPENAI_API_KEY|FEISHU|LARK' "$ROOT" \
  --exclude-dir=.git \
  --exclude-dir=.venv \
  --exclude='*.pyc' || true
echo

echo "[4] Searching for pushed state / runtime artifacts..."
find "$ROOT" \
  \( -name 'pushed_ids*.json' -o -path '*/data/cache/*' -o -name '*.log' \) \
  | sed 's#^\./##' || true
echo

echo "[5] Git status preview..."
git -C "$ROOT" status --short || true
echo

echo "Check complete."
