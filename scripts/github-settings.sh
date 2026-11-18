#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Repository settings for this open-core repository. Needs `gh` signed in as
# an admin of the repository.
#
#   bash scripts/github-settings.sh            # dry run: print what it would change
#   bash scripts/github-settings.sh --apply    # apply the changes
#
# Touches this repository only. Safe to rerun: every call sets a value,
# nothing is deleted. The decisions baked in here are listed at the bottom.
# Older copies of this script had bugs: do not run them.
set -euo pipefail

ORG="BraidScope"
REPO="$ORG/braidscope-agency-reporting"

APPLY=0
for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=1 ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "unknown argument: $arg (use --apply, or nothing for a dry run)"; exit 2 ;;
  esac
done

FAILED=0
say() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }

# step LABEL COMMAND [ARGS...]
# Dry run: prints the command. With --apply: runs it and reports ok or FAILED.
step() {
  local label="$1"; shift
  if [ "$APPLY" -eq 0 ]; then
    printf '   would: %s\n          ' "$label"
    printf '%q ' "$@"
    printf '\n'
    return 0
  fi
  local err
  if err="$("$@" 2>&1 >/dev/null)"; then
    echo "   ok: $label"
  else
    echo "   FAILED: $label"
    if [ -n "$err" ]; then printf '           %s\n' "$err"; fi
    FAILED=1
  fi
}

# api_body METHOD PATH JSON: send JSON as the request body.
api_body() { printf '%s' "$3" | gh api -X "$1" "$2" --input -; }

command -v gh >/dev/null || { echo "gh is not installed: https://cli.github.com"; exit 1; }
if [ "$APPLY" -eq 1 ]; then
  gh auth status >/dev/null 2>&1 || { echo "run: gh auth login"; exit 1; }
else
  say "Dry run. Nothing is changed. Pass --apply to apply."
fi

TOPICS='["marketing-agency","client-reporting","reporting","dashboards","analytics","google-ads","meta-ads","facebook-ads","google-analytics","ga4","hubspot","klaviyo","shopify","self-hosted","open-source","agpl","fastapi","react","supabase","postgres"]'

ruleset_json() {
  # Branch rules for main: PR required, 1 approval, code owners, stale approvals
  # dismissed, required checks, no force push, no deletion.
  # bypass_actors: organisation admins may merge a PR without a second
  # reviewer (pull_request mode only, so they still cannot push to main).
  # Without it a single maintainer can never merge their own PR.
  cat <<JSON
{
  "name": "main-protect",
  "target": "branch",
  "enforcement": "active",
  "bypass_actors": [ { "actor_id": 1, "actor_type": "OrganizationAdmin", "bypass_mode": "pull_request" } ],
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

say "$REPO: description, website, topics"
step "description and website" gh api -X PATCH "repos/$REPO" \
  -f description="Open-source client reporting for marketing agencies. Read-only connectors for Google Ads, Meta Ads, GA4, HubSpot, Klaviyo and Shopify. One-command self-host." \
  -f homepage="https://braidscope.com"
step "20 topics" api_body PUT "repos/$REPO/topics" "{\"names\": $TOPICS}"

say "$REPO: merge and housekeeping"
step "squash only, delete branch on merge, wiki and projects off" gh api -X PATCH "repos/$REPO" \
  -F allow_squash_merge=true -F allow_merge_commit=false -F allow_rebase_merge=false \
  -F delete_branch_on_merge=true -F allow_auto_merge=true \
  -F has_wiki=false -F has_projects=false -F has_issues=true

say "$REPO: security features"
step "Dependabot alerts" gh api -X PUT "repos/$REPO/vulnerability-alerts"
step "Dependabot security updates" gh api -X PUT "repos/$REPO/automated-security-fixes"
step "private vulnerability reporting" gh api -X PUT "repos/$REPO/private-vulnerability-reporting"
step "secret scanning and push protection" api_body PATCH "repos/$REPO" \
  '{"security_and_analysis":{"secret_scanning":{"status":"enabled"},"secret_scanning_push_protection":{"status":"enabled"}}}'
step "CodeQL default setup" api_body PATCH "repos/$REPO/code-scanning/default-setup" \
  '{"state":"configured","query_suite":"default","languages":["python","javascript-typescript"]}'

say "$REPO: branch rules"
existing=""
if [ "$APPLY" -eq 1 ]; then
  existing="$(gh api "repos/$REPO/rulesets" --jq '.[] | select(.name=="main-protect") | .id' 2>/dev/null || true)"
fi
if [ -n "$existing" ]; then
  step "update ruleset main-protect" api_body PUT "repos/$REPO/rulesets/$existing" "$(ruleset_json)"
else
  step "create ruleset main-protect (updated instead if it already exists)" api_body POST "repos/$REPO/rulesets" "$(ruleset_json)"
fi

cat <<'TXT'

Decisions baked in:
- This repository only. Settings for any other repository are out of scope.
- Squash merges only, branch deleted after merge, 1 approval, Code Owners review, stale approvals dismissed.
- Organisation admins may bypass the review rule on a pull request, so a single maintainer can merge.
  Nobody can push to main directly, force-push it or delete it.
- Required checks: lint, unit, build, gitleaks, zizmor, DCO (the names in .github/workflows/ci.yml).
- Secret scanning with push protection, Dependabot alerts and security updates, private vulnerability reporting.
- Wiki and Projects off, 20 topics, CodeQL default setup. Discussions are not changed here.
TXT

if [ "$FAILED" -ne 0 ]; then
  echo "One or more steps FAILED. See the lines above."
  exit 1
fi
