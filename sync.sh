#!/usr/bin/env bash
# Sends this job's GitHub Environment variables and secrets to Inferra.
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
  echo "::error::secrets must be a JSON object, e.g. \${{ toJSON(secrets) }}"
  exit 1
fi

# The OIDC token for Inferra.
oidc=$(curl -fsS -H "Authorization: bearer $ACTIONS_ID_TOKEN_REQUEST_TOKEN" \
  "${ACTIONS_ID_TOKEN_REQUEST_URL}&audience=${INFERRA_AUDIENCE:-inferra}" | jq -r .value)
echo "::add-mask::$oidc"

# toJSON(secrets) always includes GitHub's own github_token: never send it.
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
