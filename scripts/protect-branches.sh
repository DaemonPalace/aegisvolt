#!/usr/bin/env bash
# Protect dev/test/prod: PR-only, no force push, no deletion, admins included.
# Phase 1 (S0):  scripts/protect-branches.sh
# Phase 2 (S4):  scripts/protect-branches.sh --with-checks   (only after pr.yml has run green once)
set -euo pipefail
REPO="${REPO:-DaemonPalace/aegisvolt}"
CHECKS='null'
if [[ "${1:-}" == "--with-checks" ]]; then
  CHECKS='{"strict":true,"contexts":["promotion-guard","lint-build","unit-tests","integration-tests","e2e-tests","terraform-plan"]}'
fi
for b in dev test prod; do
  gh api -X PUT "repos/$REPO/branches/$b/protection" --input - >/dev/null <<JSON
{
  "required_status_checks": $CHECKS,
  "enforce_admins": true,
  "required_pull_request_reviews": {"required_approving_review_count": 0},
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_linear_history": false
}
JSON
  echo "protected $b (checks: $([[ $CHECKS == null ]] && echo none || echo 6))"
done
