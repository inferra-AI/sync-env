#!/usr/bin/env bash
# Sends this job's GitHub Environment variables and selected secrets to Inferra.
#
# Identity: a GitHub Actions OIDC token (audience "inferra") proves which
# repository, environment and commit this run is; Inferra checks it against
# GitHub's published keys. Nothing here stores or prints a credential.
# Never trace: `bash -x`, SHELLOPTS=xtrace or a debug wrapper would print the
# secrets this script handles. Off before anything sensitive is read.
set +x
set +o xtrace
set -euo pipefail

# Per-request limits in seconds; override with positive integer environment settings.
INFERRA_CONNECT_TIMEOUT=${INFERRA_CONNECT_TIMEOUT:-10}
INFERRA_MAX_TIME=${INFERRA_MAX_TIME:-60}
if [[ ! "$INFERRA_CONNECT_TIMEOUT" =~ ^[1-9][0-9]*$ || ! "$INFERRA_MAX_TIME" =~ ^[1-9][0-9]*$ ]]; then
  echo "::error::Connection and request timeouts must be positive integer seconds."
  exit 1
fi

# An empty api-url means the production API.
INFERRA_API=${INFERRA_API:-https://api.inferralab.com}
if [[ "$INFERRA_API" != [hH][tT][tT][pP][sS]://* ]]; then
  echo "::error::api-url must use HTTPS to protect credentials and secrets."
  exit 1
fi

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

# toJSON(vars) also carries organisation and repository variables, such as
# INFERRA_API_URL. Names the platform sets itself (PORT, INFERRA_*,
# KUBERNETES_*) would make Inferra refuse the whole sync, so leave them out
# (names only in the notice).
reserved_vars=$(jq -c '[keys[] | select(. == "PORT" or startswith("INFERRA_") or startswith("KUBERNETES_"))]' <<<"$vars_json")
if [[ "$reserved_vars" != '[]' ]]; then
  echo "::notice::Not sent, set by Inferra itself: $reserved_vars"
  vars_json=$(jq -c 'with_entries(select(.key != "PORT" and (.key | startswith("INFERRA_") | not) and (.key | startswith("KUBERNETES_") | not)))' <<<"$vars_json")
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
oidc=$(curl -q --connect-timeout "$INFERRA_CONNECT_TIMEOUT" --max-time "$INFERRA_MAX_TIME" \
  --proto '=https' -fsS -H "Authorization: bearer $ACTIONS_ID_TOKEN_REQUEST_TOKEN" \
  "${ACTIONS_ID_TOKEN_REQUEST_URL}&audience=${INFERRA_AUDIENCE:-inferra}" | jq -r .value)
echo "::add-mask::$oidc"

# Never send GitHub's own token, even if a caller explicitly includes it.
body=$(jq -n --arg space "$INFERRA_SPACE" \
  --argjson vars "$vars_json" --argjson secrets "$secrets_json" \
  '{space: $space, variables: $vars, secrets: ($secrets | del(.github_token, .GITHUB_TOKEN))}')

out=$(mktemp)
trap 'rm -f "$out"' EXIT
code=$(curl -q --connect-timeout "$INFERRA_CONNECT_TIMEOUT" --max-time "$INFERRA_MAX_TIME" \
  --proto '=https' -sS -o "$out" -w '%{http_code}' -X PUT "${INFERRA_API%/}/v1/github/env" \
  -H "Authorization: Bearer $oidc" -H "Content-Type: application/json" --data-binary @- <<<"$body")

# Nothing from an error response is ever printed: its text could carry a
# value. Only the numeric status and a fixed sentence per status.
if [[ "$code" != 2* ]]; then
  [[ "$code" =~ ^[0-9]{3}$ ]] || code=000
  case "$code" in
    400|422) why="the request was invalid; check the variable and secret names and sizes" ;;
    401|403) why="authentication failed; check 'permissions: id-token: write', the Environment name, and that this repository is an installed Inferra org" ;;
    404) why="the space or repository was not found; check the Environment name matches a space" ;;
    409) why="the sync conflicts with current state (an older or repeated run, or a value managed elsewhere)" ;;
    413) why="the request is too large" ;;
    429) why="too many syncs; retry in a minute" ;;
    5??) why="Inferra had a server error; re-run the workflow" ;;
    *) why="unexpected response" ;;
  esac
  echo "::error::Inferra refused the sync (HTTP $code): $why."
  exit 1
fi
summary=$(jq -r --arg space "$INFERRA_SPACE" '"Synced to space \($space): \(.variables | numbers) variables, \(.secrets | numbers) secrets\(if (.restarted | numbers) > 0 then ", restarted \(.restarted) apps" else "" end)."' "$out" 2>/dev/null || true)
echo "${summary:-Synced.}"
# One owner per secret: names Inferra did not overwrite because they were set
# in Inferra. Only strings shaped like a secret name are printed, at most 20.
conflicts=$(jq -r '[.conflicts[]? | select(type == "string" and test("^[A-Z_][A-Z0-9_]{0,127}$"))] | .[0:20] | join(", ")' "$out" 2>/dev/null || true)
if [[ -n "$conflicts" ]]; then
  echo "::warning::Not overwritten, already set in Inferra: $conflicts. Run 'inferra secrets rm APP NAME' to let GitHub own it, or remove it from this workflow's secrets."
fi
