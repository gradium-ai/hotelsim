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

export JIRA_EMAIL="${JIRA_EMAIL:-colin@gradium.ai}"

INFISICAL_PROJECT_ID="${INFISICAL_PROJECT_ID:-c5cd7459-8df3-4d6a-b53f-4686cde222f1}"
INFISICAL_ENV="${INFISICAL_ENV:-dev}"

# Pull secrets and remap to the names the app expects.
exec infisical run \
  --projectId "$INFISICAL_PROJECT_ID" \
  --env "$INFISICAL_ENV" \
  --command "GRADIUM_API_KEY=\"\$GRADIUM_TICATAG_API_KEY\" JIRA_API_TOKEN=\"\$jira\" $*"
