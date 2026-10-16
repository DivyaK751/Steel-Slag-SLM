#!/usr/bin/env bash
# Write the Google sign-ins stored in secrets to files, and point later steps at them.
set -euo pipefail

# Drive: a saved personal login uploads into that person's My Drive; a service account
# only works when the folder is in a Shared Drive
if [ -n "${GOOGLE_OAUTH_TOKEN_JSON:-}" ]; then
  printf '%s' "$GOOGLE_OAUTH_TOKEN_JSON" > "$RUNNER_TEMP/google_token.json"
  echo "GOOGLE_OAUTH_TOKEN_FILE=$RUNNER_TEMP/google_token.json" >> "$GITHUB_ENV"
else
  printf '%s' "${GOOGLE_SERVICE_ACCOUNT_JSON:-}" > "$RUNNER_TEMP/service_account.json"
  echo "GOOGLE_APPLICATION_CREDENTIALS=$RUNNER_TEMP/service_account.json" >> "$GITHUB_ENV"
fi

# BigQuery: a service account in the Google Cloud project; without it the registry is skipped
if [ -n "${GCP_SERVICE_ACCOUNT_JSON:-}" ]; then
  printf '%s' "$GCP_SERVICE_ACCOUNT_JSON" > "$RUNNER_TEMP/gcp_service_account.json"
  echo "GCP_CREDENTIALS_FILE=$RUNNER_TEMP/gcp_service_account.json" >> "$GITHUB_ENV"
fi
