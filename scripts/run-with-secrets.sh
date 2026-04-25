#!/usr/bin/env bash
# Wrap any command with secrets pulled from Infisical.
#
# Local dev: requires a one-time `infisical login` (user account works).
# Then in demos/ticatag run:
#   ../../scripts/run-with-secrets.sh uv run uvicorn main:app --reload --port 8000
#
# Secrets pulled from Infisical project "Gradbots", env "dev":
#   GRADIUM_TICATAG_API_KEY  -> exported as GRADIUM_API_KEY (what gradbot expects)
#   LLM_API_KEY              -> exported as-is
#   jira                     -> exported as JIRA_API_TOKEN
#
# JIRA_EMAIL is set here (not a secret).

set -euo pipefail

export JIRA_EMAIL="${JIRA_EMAIL:-colin@gradium.sh}"

INFISICAL_PROJECT_ID="${INFISICAL_PROJECT_ID:-c5cd7459-8df3-4d6a-b53f-4686cde222f1}"
INFISICAL_ENV="${INFISICAL_ENV:-dev}"

# Pull secrets and remap to the names the app expects.
#
# Quirk: the LLM_API_KEY secret in Infisical actually contains the LLM base URL
# (value looks like "LLM_BASE_URL=https://..."). We extract that URL into
# LLM_BASE_URL and reuse GRADIUM_TICATAG_API_KEY as the actual LLM key (the
# Gradium LLM proxy authenticates with the Gradium API key).
exec infisical run \
  --projectId "$INFISICAL_PROJECT_ID" \
  --env "$INFISICAL_ENV" \
  --command "
    export GRADIUM_API_KEY=\"\$GRADIUM_TICATAG_API_KEY\"
    export JIRA_API_TOKEN=\"\$jira\"
    if [[ \"\$LLM_API_KEY\" == LLM_BASE_URL=* ]]; then
      export LLM_BASE_URL=\"\${LLM_API_KEY#LLM_BASE_URL=}\"
      export LLM_API_KEY=\"\$GRADIUM_TICATAG_API_KEY\"
    fi
    $*
  "
