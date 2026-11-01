#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# One-shot repository settings for Braidscope. Run from any machine where
# `gh` is signed in as an owner of the BraidScope organisation:
#
#   bash scripts/github-settings.sh                 # apply to both repos
#   bash scripts/github-settings.sh --public        # also make the open-core repo public
#
# Safe to rerun: every call sets a value, nothing is deleted. The decisions
# baked in here are listed at the bottom of this file.
set -euo pipefail

ORG="BraidScope"
PRIVATE="$ORG/braidscope"
PUBLIC="$ORG/braidscope-agency-reporting"
FLIP_PUBLIC="${1:-}"

say() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
try() { "$@" >/dev/null 2>&1 && echo "   ok: ${*:3}" || echo "   skipped (not available on this plan/visibility yet): ${*:3}"; }

command -v gh >/dev/null || { echo "gh is not installed: https://cli.github.com"; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "run: gh auth login"; exit 1; }

TOPICS='["marketing-agency","client-reporting","reporting","dashboards","analytics","google-ads","meta-ads","facebook-ads","google-analytics","ga4","hubspot","klaviyo","shopify","self-hosted","open-source","agpl","fastapi","react","supabase","postgres"]'

ruleset_json() {
  # Branch rules for main: PR required, 1 approval, code owners, stale approvals
  # dismissed, required checks, no force push, no deletion.
  cat <<JSON
{
  "name": "main-protect",
  "target": "branch",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["refs/heads/main"], "exclude": [] } },
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    { "type": "pull_request", "parameters": {
        "required_approving_review_count": 1,
        "dismiss_stale_reviews_on_push": true,
        "require_code_owner_review": true,
        "require_last_push_approval": false,
        "required_review_thread_resolution": true,
        "allowed_merge_methods": ["squash"] } },
    { "type": "required_status_checks", "parameters": {
        "strict_required_status_checks_policy": true,
        "required_status_checks": [
          { "context": "lint" }, { "context": "unit" }, { "context": "build" },
          { "context": "gitleaks" }, { "context": "zizmor" }, { "context": "DCO" } ] } }
  ]
}
JSON
}

apply_ruleset() {
  local repo="$1"
  local existing
  existing="$(gh api "repos/$repo/rulesets" --jq '.[] | select(.name=="main-protect") | .id' 2>/dev/null || true)"
  if [ -n "$existing" ]; then
    ruleset_json | gh api -X PUT "repos/$repo/rulesets/$existing" --input - >/dev/null 2>&1 && echo "   ok: ruleset main-protect updated" || echo "   skipped: rulesets need a public repo or GitHub Team on a private one"
  else
    ruleset_json | gh api -X POST "repos/$repo/rulesets" --input - >/dev/null 2>&1 && echo "   ok: ruleset main-protect created" || echo "   skipped: rulesets need a public repo or GitHub Team on a private one"
  fi
}

common() {
  local repo="$1"
  say "$repo: merge and housekeeping"
  gh api -X PATCH "repos/$repo" \
    -F allow_squash_merge=true -F allow_merge_commit=false -F allow_rebase_merge=false \
    -F delete_branch_on_merge=true -F allow_auto_merge=true \
    -F has_wiki=false -F has_projects=false -F has_issues=true >/dev/null && echo "   ok: squash only, delete branch on merge, wiki off"

  say "$repo: security features"
  try gh api -X PUT "repos/$repo/vulnerability-alerts"                       "Dependabot alerts"
  try gh api -X PUT "repos/$repo/automated-security-fixes"                   "Dependabot security updates"
  try gh api -X PUT "repos/$repo/private-vulnerability-reporting"            "private vulnerability reporting"
  gh api -X PATCH "repos/$repo" --input - >/dev/null 2>&1 <<'JSON' && echo "   ok: secret scanning + push protection" || echo "   skipped: secret scanning needs a public repo or Advanced Security"
{"security_and_analysis":{"secret_scanning":{"status":"enabled"},"secret_scanning_push_protection":{"status":"enabled"}}}
JSON

  say "$repo: branch rules"
  apply_ruleset "$repo"
}

say "Open-core repository"
gh api -X PATCH "repos/$PUBLIC" \
  -f description="Open-source client reporting for marketing agencies. Read-only connectors for Google Ads, Meta Ads, GA4, HubSpot, Klaviyo and Shopify. One-command self-host." \
  -f homepage="https://braidscope.com" -F has_discussions=true >/dev/null && echo "   ok: description, website, discussions on"
echo "{\"names\": $TOPICS}" | gh api -X PUT "repos/$PUBLIC/topics" --input - >/dev/null && echo "   ok: 20 topics"
if [ "$FLIP_PUBLIC" = "--public" ]; then
  gh api -X PATCH "repos/$PUBLIC" -f visibility=public >/dev/null && echo "   ok: repository is now PUBLIC"
fi
common "$PUBLIC"
# CodeQL default setup: free on a public repository.
try gh api -X PATCH "repos/$PUBLIC/code-scanning/default-setup" --input - <<<'{"state":"configured","query_suite":"default","languages":["python","javascript-typescript"]}' "CodeQL default setup"

say "Private production repository"
common "$PRIVATE"

say "Supabase: leaked password protection"
if [ -n "${SUPABASE_ACCESS_TOKEN:-}" ]; then
  curl -fsS -X PATCH "https://api.supabase.com/v1/projects/wdhjkzrqirzkscxufzav/config/auth" \
    -H "Authorization: Bearer $SUPABASE_ACCESS_TOKEN" -H "Content-Type: application/json" \
    -d '{"password_hibp_enabled": true}' >/dev/null && echo "   ok: leaked password protection on"
else
  echo "   skipped: set SUPABASE_ACCESS_TOKEN (Supabase dashboard > Account > Access tokens) and rerun, or toggle it in Authentication > Password settings"
fi

cat <<'TXT'

Decisions baked in:
- Squash merges only, branch deleted after merge, 1 approval, Code Owners review, stale approvals dismissed.
- Required checks: lint, unit, build, gitleaks, zizmor, DCO (the names in .github/workflows/ci.yml).
- Secret scanning with push protection, Dependabot alerts and security updates, private vulnerability reporting.
- Public repo: Discussions on, Wiki off, 20 topics, CodeQL default setup.
- Branch rules on a PRIVATE repo need GitHub Team (about $4 per user per month). On the Free plan the
  script skips them there and applies everything else. The public repo gets them for free once public.
TXT
