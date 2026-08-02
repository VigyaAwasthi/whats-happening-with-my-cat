#!/usr/bin/env bash
# Fail if anything git tracks looks like a credential.
#
# Scans tracked files only. Untracked working-tree files are irrelevant: they
# are not what gets pushed and not what Railway or Cloudflare builds from.
#
#   ./scripts/scan_secrets.sh            # working tree
#   ./scripts/scan_secrets.sh --history  # also every blob in git history
#
# Run this before every deploy. It is wired into .github/workflows/backend-ci.yml.

set -uo pipefail
cd "$(git rev-parse --show-toplevel)"

status=0

report() {
  printf '\n!! %s\n' "$1"
  status=1
}

# Provider key formats and private keys. `.env.example` is scanned like any
# other tracked file — it is meant to hold empty values and placeholders only.
PATTERNS=(
  'sk-ant-[A-Za-z0-9_-]{16,}'
  '\bpa-[A-Za-z0-9_-]{24,}'
  'eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}'
  '-----BEGIN [A-Z ]*PRIVATE KEY-----'
  'postgres(ql)?://[^:/[:space:]]+:[^@[:space:]]+@'
  '\bsb_secret_[A-Za-z0-9_-]{16,}'
  '(?i)(secret|passwd|password|api[_-]?key|access[_-]?token)[[:space:]]*[:=][[:space:]]*["'"'"'][^"'"'"'[:space:]]{12,}["'"'"']'
)

# Documented placeholders and local-only defaults. A scanner that reports these
# gets ignored, and an ignored scanner catches nothing — so they are excluded
# explicitly and narrowly rather than by loosening the patterns above.
PLACEHOLDERS='YOUR_[A-Z_]+|CHANGE_?ME|REPLACE_?ME|<[a-z_ -]+>|\*\*\*|xxxx|postgres:postgres@(127\.0\.0\.1|localhost)|:password@|example\.com'

# Known-synthetic fixture values that already exist in committed history, from
# the redaction tests. They are listed individually — not as a blanket
# tests/ exclusion — so that a genuinely leaked credential in the same file
# would still be caught. `--history` cannot be cleaned by editing the working
# tree, and rewriting published history to remove a fake password is a worse
# trade than naming it here.
HISTORICAL_FIXTURES='postgresql://user:secret@host|hunter2|realpassword|AbCdEfGhIjKlMnOpQrStUv|abcdefghijklmnopqrstuvwxyz01|dozjgNryP4J3jVmNHl0w5N|postgresql://u:p@'

# Synthetic credentials used as test fixtures — the redaction tests need real
# credential SHAPES to assert against. A line is exempt only when it carries the
# marker explicitly, so exempting something is a visible, reviewable act rather
# than a whole directory quietly falling out of the scan.
#
#   "sk-ant-api03-notarealkey"  # pragma: allowlist-secret
ALLOWLIST_MARKER='pragma: allowlist-secret'

echo "Scanning $(git ls-files | wc -l | tr -d ' ') tracked files..."

for pattern in "${PATTERNS[@]}"; do
  # -I skips binaries; the pathspec keeps the lockfile and this script itself
  # from matching their own pattern list.
  hits=$(git grep -nIE "$pattern" -- \
        ':!scripts/scan_secrets.sh' \
        ':!package-lock.json' \
        ':!*.lock' 2>/dev/null | grep -vE "$PLACEHOLDERS" | grep -vE "$HISTORICAL_FIXTURES" | grep -vF "$ALLOWLIST_MARKER")
  if [[ -n "$hits" ]]; then
    report "possible secret matching /$pattern/:"
    printf '%s\n' "$hits"
  fi
done

# A real .env must never be tracked, whatever it is called.
if tracked_env=$(git ls-files | grep -E '(^|/)\.env($|\.)' | grep -v '\.env\.example$'); then
  report "environment file is tracked:"
  printf '%s\n' "$tracked_env"
fi

# .env.example must ship placeholders, never filled-in values.
if git ls-files --error-unmatch .env.example >/dev/null 2>&1; then
  if filled=$(git show HEAD:.env.example 2>/dev/null | grep -nE '^(SUPABASE_ANON_KEY|SUPABASE_SERVICE_ROLE_KEY|ANTHROPIC_API_KEY|VOYAGE_API_KEY)=.+'); then
    report ".env.example has a filled-in secret value:"
    printf '%s\n' "$filled"
  fi
fi

if [[ "${1:-}" == "--history" ]]; then
  echo "Scanning full git history (this is slower)..."
  for pattern in "${PATTERNS[@]}"; do
    hits=$(git rev-list --all \
             | xargs -I{} git grep -nIE "$pattern" {} -- \
                 ':!scripts/scan_secrets.sh' ':!package-lock.json' 2>/dev/null \
             | grep -vE "$PLACEHOLDERS" | grep -vE "$HISTORICAL_FIXTURES" | grep -vF "$ALLOWLIST_MARKER")
    if [[ -n "$hits" ]]; then
      report "possible secret in git history matching /$pattern/:"
      printf '%s\n' "$hits" | head -20
    fi
  done
fi

if [[ $status -eq 0 ]]; then
  echo "OK: no credential-shaped content in tracked files."
else
  printf '\nA secret in the repository is not fixed by deleting the line. Rotate the\n'
  printf 'credential in the provider console first, then remove it from the repo.\n'
fi
exit $status
