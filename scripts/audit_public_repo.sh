#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="${1:-.}"
cd "$ROOT"

RED='\033[31m'
YEL='\033[33m'
GRN='\033[32m'
BLU='\033[34m'
NC='\033[0m'

fail_count=0
warn_count=0

pass() { echo -e "${GRN}[PASS]${NC} $*"; }
warn() { echo -e "${YEL}[WARN]${NC} $*"; warn_count=$((warn_count+1)); }
fail() { echo -e "${RED}[FAIL]${NC} $*"; fail_count=$((fail_count+1)); }
info() { echo -e "${BLU}[INFO]${NC} $*"; }

section() {
  echo
  echo "============================================================"
  echo "$*"
  echo "============================================================"
}

section "1) 仓库基本信息"
info "repo root: $(pwd)"
info "branch   : $(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
info "commit   : $(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
info "remote   : $(git remote -v | awk 'NR<=2{print}')"

section "2) 工作区是否干净"
if git diff --quiet && git diff --cached --quiet; then
  pass "git 工作区干净"
else
  warn "git 工作区有未提交改动"
  git status --short || true
fi

section "3) 是否跟踪了高风险文件"
tracked_sensitive="$(git ls-files | grep -E '(^|/)(\.env|\.env\..+|.*\.pem|.*\.key|.*\.p12|.*\.pfx|id_rsa|id_ed25519|known_hosts|authorized_keys|.*\.secret|.*\.token|.*credentials.*|.*cookie.*|.*session.*|.*sqlite|.*db)$' || true)"
tracked_sensitive="$(printf '%s\n' "$tracked_sensitive" | grep -vE '(^|/)\.env\.example$' || true)"

if [[ -n "$tracked_sensitive" ]]; then
  fail "发现疑似敏感文件被 git 跟踪："
  printf '%s\n' "$tracked_sensitive"
else
  pass "未发现高风险敏感文件被跟踪"
fi

section "4) 是否跟踪了运行状态 / 缓存 / 日志 / 测试产物"
runtime_files="$(git ls-files | grep -E '(^|/)(data/.*pushed_ids.*\.json|data/cache/|.*\.log$|.*\.bak$|.*\.bak\..*$|.*\.tmp$|.*\.swp$|.*__pycache__/.*|.*\.pyc$)' || true)"
runtime_files="$(printf '%s\n' "$runtime_files" | sed '/^$/d' || true)"

if [[ -n "$runtime_files" ]]; then
  warn "发现运行状态或缓存类文件被跟踪："
  printf '%s\n' "$runtime_files"
else
  pass "未发现明显运行状态/缓存/日志文件被跟踪"
fi

section "5) 搜索疑似敏感字符串（仅扫描已跟踪文件，排除示例配置和检查脚本）"
tmp_report="$(mktemp)"

git ls-files -z | \
xargs -0 grep -nIE \
'app_secret|app_id|open_id|chat:oc_|user:ou_|tenant_access_token|access_token|refresh_token|authorization:|bearer |api[_-]?key|secret|password|PRIVATE KEY|BEGIN RSA PRIVATE KEY|BEGIN OPENSSH PRIVATE KEY|BEGIN EC PRIVATE KEY' \
> "$tmp_report" || true

grep -vE '^\.env\.example:' "$tmp_report" | \
grep -vE '^scripts/check_public_release\.sh:' | \
grep -vE '^scripts/audit_public_repo\.sh:' \
> "${tmp_report}.filtered" || true

if [[ -s "${tmp_report}.filtered" ]]; then
  warn "发现疑似敏感字符串命中。请人工逐条确认："
  cat "${tmp_report}.filtered"
else
  pass "未发现明显敏感字符串"
fi

section "6) 专门检查是否出现飞书 / OpenClaw 会话标识"
tmp_ids="$(mktemp)"
git ls-files -z | xargs -0 grep -nE 'oc_[a-z0-9]{32}|ou_[a-z0-9]{32}' > "$tmp_ids" || true
if [[ -s "$tmp_ids" ]]; then
  fail "发现疑似 Feishu/OpenClaw 会话或用户标识："
  cat "$tmp_ids"
else
  pass "未发现明显 chat/open_id 标识"
fi

section "7) 检查 .gitignore 是否覆盖常见私有文件"
required_literals=(
  ".env"
  ".env.*"
  "!.env.example"
  "*.bak"
)

missing=0
for pat in "${required_literals[@]}"; do
  if grep -qxF "$pat" .gitignore 2>/dev/null; then
    :
  else
    warn ".gitignore 可能缺少规则: $pat"
    missing=1
  fi
done

if [[ "$missing" -eq 0 ]]; then
  pass ".gitignore 看起来覆盖了常见私有文件"
fi

section "8) 检查是否存在大文件（已跟踪）"
large_found=0
while IFS= read -r f; do
  [[ -f "$f" ]] || continue
  size=$(wc -c < "$f")
  if [[ "$size" -gt 1048576 ]]; then
    if [[ "$large_found" -eq 0 ]]; then
      warn "发现大于 1MB 的已跟踪文件："
      large_found=1
    fi
    printf '  %s (%s bytes)\n' "$f" "$size"
  fi
done < <(git ls-files)

if [[ "$large_found" -eq 0 ]]; then
  pass "未发现大于 1MB 的已跟踪文件"
fi

section "9) 检查最近提交里是否出现可疑文件名"
recent_names="$(mktemp)"
git log --name-only --pretty=format: -n 20 | sed '/^$/d' | sort -u > "$recent_names" || true
filtered_recent="$(grep -E '(\.env|\.pem|\.key|pushed_ids.*\.json|data/cache/|\.log$|\.bak$|\.bak\.)' "$recent_names" || true)"
filtered_recent="$(printf '%s\n' "$filtered_recent" | grep -vE '^\.env\.example$' || true)"

if [[ -n "$filtered_recent" ]]; then
  warn "最近 20 个提交涉及以下可疑文件名，请确认是否真的该公开："
  printf '%s\n' "$filtered_recent"
else
  pass "最近 20 个提交的文件名没有明显异常"
fi

section "10) README / 示例配置是否合理"
[[ -f README.md ]] && pass "README.md 存在" || warn "README.md 不存在"
[[ -f .env.example ]] && pass ".env.example 存在" || warn ".env.example 不存在"

if git ls-files | grep -Eq '^config/.*\.example\.yml$'; then
  pass "存在 example 配置文件"
else
  warn "没有发现 example 配置文件"
fi

section "11) 汇总"
rm -f "$tmp_report" "${tmp_report}.filtered" "$tmp_ids" "$recent_names"

echo "fail_count=$fail_count"
echo "warn_count=$warn_count"

if [[ "$fail_count" -gt 0 ]]; then
  echo
  echo -e "${RED}结论：未通过公开验收。请先处理 FAIL 项。${NC}"
  exit 2
elif [[ "$warn_count" -gt 0 ]]; then
  echo
  echo -e "${YEL}结论：基本可公开，但建议先处理 WARN 项。${NC}"
  exit 1
else
  echo
  echo -e "${GRN}结论：通过公开仓库验收。${NC}"
  exit 0
fi
