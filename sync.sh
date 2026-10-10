#!/usr/bin/env bash
# Sends this job's GitHub Environment variables and selected secrets to Inferra.
#
# Identity: a GitHub Actions OIDC token (audience "inferra") proves which
# repository, environment and commit this run is; Inferra checks it against
# GitHub's published keys. Nothing here stores or prints a credential.
set -euo pipefail

: "${INFERRA_SPACE:?space is required}"
if [[ -z "${ACTIONS_ID_TOKEN_REQUEST_URL:-}" || -z "${ACTIONS_ID_TOKEN_REQUEST_TOKEN:-}" ]]; then
  echo "::error::No OIDC token available. Add 'permissions: id-token: write' to the workflow or job."
  exit 1
fi
vars_json=${INFERRA_VARS:-}; [[ -n "$vars_json" ]] || vars_json='{}'
secrets_json=${INFERRA_SECRETS:-}; [[ -n "$secrets_json" ]] || secrets_json='{}'
if ! jq -e 'type == "object"' >/dev/null 2>&1 <<<"$vars_json"; then
  echo "::error::variables must be a JSON object, e.g. \${{ toJSON(vars) }}"
  exit 1
fi
if ! jq -e 'type == "object"' >/dev/null 2>&1 <<<"$secrets_json"; then
  echo '::error::secrets must be a JSON object of selected names, e.g. {"DATABASE_URL": ${{ toJSON(secrets.DATABASE_URL) }}}'
  exit 1
fi

# A secret listed in the workflow but not set in this Environment arrives as
# null (toJSON of an unset secret) or "". Skip it (names only in the warning):
# sending it would store an empty value, and leaving it out mirrors GitHub,
# where the secret doesn't exist.
empty_secrets=$(jq -c '[to_entries[] | select(.value == null or .value == "") | .key]' <<<"$secrets_json")
if [[ "$empty_secrets" != '[]' ]]; then
  echo "::warning::Not set in this GitHub Environment, skipped: $empty_secrets"
  secrets_json=$(jq -c 'with_entries(select(.value != null and .value != ""))' <<<"$secrets_json")
fi
# Report names only. Other non-string values are a workflow mistake: refuse
# the whole sync before it can change anything in Inferra.
invalid_secrets=$(jq -c '[to_entries[] | select(.value | type != "string") | .key]' <<<"$secrets_json")
if [[ "$invalid_secrets" != '[]' ]]; then
  echo "::error::Selected secrets must have string values: $invalid_secrets"
  exit 1
fi

# The OIDC token for Inferra.
oidc=$(curl -fsS -H "Authorization: bearer $ACTIONS_ID_TOKEN_REQUEST_TOKEN" \
  "${ACTIONS_ID_TOKEN_REQUEST_URL}&audience=${INFERRA_AUDIENCE:-inferra}" | jq -r .value)
echo "::add-mask::$oidc"

# Never send GitHub's own token, even if a caller explicitly includes it.
body=$(jq -n --arg space "$INFERRA_SPACE" \
  --argjson vars "$vars_json" --argjson secrets "$secrets_json" \
  '{space: $space, variables: $vars, secrets: ($secrets | del(.github_token, .GITHUB_TOKEN))}')

out=$(mktemp)
trap 'rm -f "$out"' EXIT
code=$(curl -sS -o "$out" -w '%{http_code}' -X PUT "${INFERRA_API%/}/v1/github/env" \
  -H "Authorization: Bearer $oidc" -H "Content-Type: application/json" --data-binary @- <<<"$body")

if [[ "$code" != 2* ]]; then
  # The API's problem+json never contains values; show its explanation.
  echo "::error::Inferra refused the sync (HTTP $code): $(jq -r '.detail // .title // "unknown error"' "$out" 2>/dev/null)"
  exit 1
fi
jq -r '"Synced to space \(.space): \(.variables) variables, \(.secrets) secrets\(if .restarted > 0 then ", restarted \(.restarted) apps" else "" end)."' "$out" 2>/dev/null || echo "Synced."
