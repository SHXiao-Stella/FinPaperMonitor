#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="${1:-.}"
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/audit_public_repo.sh" "$ROOT"
