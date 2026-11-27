#!/usr/bin/env bash
# One-off settings for the GitHub repository: labels, merge settings, the rules that protect main and
# tags, and the environment that holds Claude's token. Needs the gh command signed in as an admin of the
# repository, and main already pushed. Safe to run again, except that the two rulesets must not exist yet.
#
#   .github/scripts/setup_repo.sh OWNER/REPO
set -euo pipefail
REPO=${1:?usage: setup_repo.sh OWNER/REPO}

label() { gh label create "$1" --repo "$REPO" --color "$2" --description "$3" --force; }
label bug              d73a4a "Something does not work as described"
label feature          a2eeef "Something new, or something done better"
label question         d876e3 "A question about using Jewelcase"
label docs             0075ca "About the documentation"
label duplicate        cfd3d7 "An earlier issue covers this"
label needs-info       fbca04 "Waiting for the reporter"
label out-of-scope     eeeeee "Not something Jewelcase does"
label needs-maintainer b60205 "Waits for the maintainer"
label claude:ready     0e8a16 "Claude will attempt a fix"
label claude:working   5319e7 "Claude has taken this"
label hold             000000 "Do not merge automatically"

# Squash merges only, branches deleted after merging, auto-merge available to the gate.
gh api -X PATCH "repos/$REPO" \
  -F allow_auto_merge=true -F delete_branch_on_merge=true \
  -F allow_squash_merge=true -F allow_merge_commit=false -F allow_rebase_merge=false >/dev/null

# Workflows get a read-only token unless a job asks for more, and may open pull requests (the fix
# workflow does). Workflows on pull requests from outside wait for the maintainer's approval to run.
gh api -X PUT "repos/$REPO/actions/permissions/workflow" \
  -f default_workflow_permissions=read -F can_approve_pull_request_reviews=true >/dev/null
gh api -X PUT "repos/$REPO/actions/permissions/fork-pr-contributor-approval" \
  -f approval_policy=all_external_contributors >/dev/null \
  || echo "Could not set the approval rule for outside pull requests; set it under Settings > Actions > General."

# main: changes only through pull requests whose `tests` and `gate` checks pass, both reported by GitHub
# Actions itself (integration 15368). Administrators may merge a pull request that fails a check (the
# bypass on the pull request page) but cannot push directly.
gh api -X POST "repos/$REPO/rulesets" --input - >/dev/null <<'JSON'
{
  "name": "main",
  "target": "branch",
  "enforcement": "active",
  "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
  "bypass_actors": [{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "pull_request"}],
  "rules": [
    {"type": "deletion"},
    {"type": "non_fast_forward"},
    {"type": "required_linear_history"},
    {"type": "pull_request", "parameters": {
      "required_approving_review_count": 0,
      "dismiss_stale_reviews_on_push": false,
      "require_code_owner_review": false,
      "require_last_push_approval": false,
      "required_review_thread_resolution": false,
      "allowed_merge_methods": ["squash"]}},
    {"type": "required_status_checks", "parameters": {
      "strict_required_status_checks_policy": false,
      "required_status_checks": [
        {"context": "tests", "integration_id": 15368},
        {"context": "gate", "integration_id": 15368}]}}
  ]
}
JSON

# Tags: only administrators create, move or delete them. A release needs a tag, so nothing automatic
# can publish one.
gh api -X POST "repos/$REPO/rulesets" --input - >/dev/null <<'JSON'
{
  "name": "tags",
  "target": "tag",
  "enforcement": "active",
  "conditions": {"ref_name": {"include": ["~ALL"], "exclude": []}},
  "bypass_actors": [{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}],
  "rules": [{"type": "creation"}, {"type": "update"}, {"type": "deletion"}]
}
JSON

# The environment that holds Claude's token, usable only by workflows that run on main. A workflow
# someone pushes on another branch cannot read the token.
gh api -X PUT "repos/$REPO/environments/claude" --input - >/dev/null <<'JSON'
{"deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}}
JSON
gh api -X POST "repos/$REPO/environments/claude/deployment-branch-policies" \
  -f name=main -f type=branch >/dev/null || true

# Private reports of security problems.
gh api -X PUT "repos/$REPO/private-vulnerability-reporting" >/dev/null || true

echo "Done. Still to do by hand:"
echo "  1. Store Claude's token (from \`claude setup-token\`) in the environment:"
echo "       gh secret set CLAUDE_CODE_OAUTH_TOKEN --env claude --repo $REPO"
echo "  2. For @claude mentions only: install the Claude GitHub App on $REPO: https://github.com/apps/claude"
echo "  3. Settings > General > Releases: switch on release immutability, so a published file cannot be replaced."
