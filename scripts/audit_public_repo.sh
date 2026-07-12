#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="${1:-.}"
cd "$ROOT"

fail_count=0

pass() { printf '[PASS] %s\n' "$*"; }
fail() {
    printf '[FAIL] %s\n' "$*"
    fail_count=$((fail_count + 1))
}
section() { printf '\n== %s ==\n' "$*"; }

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "Not a Git repository: $ROOT" >&2
    exit 2
fi

candidate_files=()
while IFS= read -r -d '' file; do
    candidate_files+=("$file")
done < <(git ls-files -z --cached --others --exclude-standard)
scan_files=()
for file in "${candidate_files[@]}"; do
    case "$file" in
        scripts/audit_public_repo.sh|scripts/check_public_release.sh)
            ;;
        *)
            [[ -f "$file" ]] && scan_files+=("$file")
            ;;
    esac
done

section "Repository"
printf 'branch: %s\n' "$(git branch --show-current)"
printf 'commit: %s\n' "$(git rev-parse --short HEAD)"
printf 'candidate files scanned: %s\n' "${#scan_files[@]}"
if [[ -n "$(git status --short)" ]]; then
    printf '[INFO] Working tree contains changes; audit includes untracked non-ignored files.\n'
else
    pass "Working tree is clean"
fi

section "Sensitive filenames"
candidate_names="$(printf '%s\n' "${candidate_files[@]}")"
sensitive_names="$(printf '%s\n' "$candidate_names" | grep -Ei '(^|/)(\.env|openclaw\.json|jobs\.json|credentials[^/]*|.*\.pem|.*\.key|.*\.p12|.*\.pfx|id_rsa|id_ed25519|.*\.secret|.*\.token)$' || true)"
sensitive_names="$(printf '%s\n' "$sensitive_names" | grep -vE '^\.env\.example$' || true)"
if [[ -n "$sensitive_names" ]]; then
    fail "Sensitive-looking files are tracked"
    printf '%s\n' "$sensitive_names"
else
    pass "No sensitive filenames are tracked"
fi

section "Runtime artifacts"
runtime_files="$(printf '%s\n' "$candidate_names" | grep -E '(^data/|/data/|(^|/).*__pycache__/|.*\.pyc$|.*\.log$|.*\.bak($|\.)|(^|/)jobs\.json($|\.))' || true)"
runtime_files="$(printf '%s\n' "$runtime_files" | grep -vE '^data/\.gitkeep$' || true)"
if [[ -n "$runtime_files" ]]; then
    fail "Runtime, cache, log, or backup files are tracked"
    printf '%s\n' "$runtime_files"
else
    pass "No runtime artifacts are tracked"
fi

section "Private identifiers and paths"
private_matches=""
if [[ "${#scan_files[@]}" -gt 0 ]]; then
    private_matches="$(grep -nIHE '/home/[^/[:space:]]+|/Users/[^/[:space:]]+|(oc_|ou_)[a-zA-Z0-9_-]{16,}|session=agent:|chat:[A-Za-z0-9_:.-]{12,}' "${scan_files[@]}" || true)"
fi
if [[ -n "$private_matches" ]]; then
    fail "Private paths or Feishu/OpenClaw identifiers were found"
    printf '%s\n' "$private_matches"
else
    pass "No private paths or private identifiers found"
fi

section "Credential values"
credential_matches=""
if [[ "${#scan_files[@]}" -gt 0 ]]; then
    credential_matches="$(grep -nIHE '(FEISHU_APP_SECRET|OPENAI_API_KEY|SEMANTIC_SCHOLAR_API_KEY|S2_API_KEY)[[:space:]]*=[[:space:]]*[^[:space:]#]{8,}|BEGIN (RSA |OPENSSH |EC )?PRIVATE KEY' "${scan_files[@]}" || true)"
fi
if [[ -n "$credential_matches" ]]; then
    fail "Possible credential values or private keys were found"
    printf '%s\n' "$credential_matches"
else
    pass "No populated credential values or private keys found"
fi

section "Document targets"
doc_target_matches=""
for file in "${scan_files[@]}"; do
    case "$file" in
        config/doc_archive.example.yml|README.md|docs/*)
            continue
            ;;
    esac
    matches="$(grep -nE '(doc_token|wiki_token|wiki_url):[[:space:]]*["]?[A-Za-z0-9_:/.-]{12,}' "$file" 2>/dev/null || true)"
    matches="$(printf '%s\n' "$matches" | grep -vEi 'paste_|your_|example|placeholder' || true)"
    if [[ -n "$matches" ]]; then
        doc_target_matches+="$file:$matches"$'\n'
    fi
done
if [[ -n "$doc_target_matches" ]]; then
    fail "Possible real Feishu document targets were found"
    printf '%s' "$doc_target_matches"
else
    pass "No real document targets found"
fi

section "Ignore policy"
required_ignores=(
    ".env"
    ".env.*"
    "!.env.example"
    "config/*.local.yml"
    "data/*"
    "!data/.gitkeep"
)
for pattern in "${required_ignores[@]}"; do
    if grep -qxF "$pattern" .gitignore; then
        pass ".gitignore contains $pattern"
    else
        fail ".gitignore is missing $pattern"
    fi
done

section "Required release files"
required_files=(
    README.md
    LICENSE
    .env.example
    requirements.lock
    config/doc_archive.example.yml
    scripts/bootstrap.sh
    scripts/doctor.py
    scripts/install_openclaw_jobs.sh
    scripts/run_delivery.py
    docs/architecture.md
    docs/deploy-openclaw-feishu.md
)
for file in "${required_files[@]}"; do
    if [[ -f "$file" ]]; then
        pass "$file"
    else
        fail "Missing $file"
    fi
done

section "Large tracked files"
large_files=()
while IFS= read -r file; do
    [[ -f "$file" ]] || continue
    if [[ "$(wc -c < "$file")" -gt 1048576 ]]; then
        large_files+=("$file")
    fi
done < <(printf '%s\n' "${candidate_files[@]}")
if [[ "${#large_files[@]}" -gt 0 ]]; then
    fail "Tracked files larger than 1 MiB: ${large_files[*]}"
else
    pass "No tracked file exceeds 1 MiB"
fi

section "Result"
printf 'fail_count=%s\n' "$fail_count"
if [[ "$fail_count" -gt 0 ]]; then
    exit 2
fi
pass "Repository is ready for public review"
