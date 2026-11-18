#!/usr/bin/env bash
set -euo pipefail

follow_ups=${FOLLOW_UP_RUNS:-0}
if [[ ! "$follow_ups" =~ ^([0-9]|1[0-2])$ ]]; then
  echo "::error::follow_up_runs must be an integer from 0 to 12."
  exit 1
fi

args=(--contextual-tables --no-figure-reading --skip-index)
if [[ -n "$MAX_PAPERS" ]]; then
  args+=(--max-papers "$MAX_PAPERS")
fi
if [[ -n "$PAPER_IDS" ]]; then
  read -r -a paper_ids <<< "$PAPER_IDS"
  args+=(--paper-ids "${paper_ids[@]}")
fi
if [[ "$follow_ups" == 0 ]]; then
  exec uv run parse-pdfs "${args[@]}"
fi

if [[ "$GITHUB_REF" != refs/heads/exp/table-corpus-audit || "$GITHUB_RUN_ATTEMPT" != 1 ]]; then
  echo "::error::Automatic continuation requires a fresh dispatch on exp/table-corpus-audit."
  exit 1
fi

log=$(mktemp)
trap 'rm -f -- "$log"' EXIT

# Leave time to hand off before GitHub kills the entire job at 180 minutes.
if timeout --kill-after=30s 150m uv run parse-pdfs "${args[@]}" 2>&1 | tee "$log"; then
  if ! grep -Fxq 'Parsing summary (Drive folder _parsed_v3/)' "$log"; then
    echo "::error::Parse exited without a v3 summary; not continuing."
    exit 1
  fi
  if ! grep -Eq '^ +[1-9][0-9]*  Not started yet \(beyond --max-papers\)$' "$log"; then
    echo "No papers deferred by the batch cap. Check the summary for skipped papers."
    exit 0
  fi
else
  statuses=("${PIPESTATUS[@]}")
  if [[ "${statuses[0]}" != 124 || "${statuses[1]}" != 0 ]]; then
    echo "::error::Parser or log capture failed (statuses: ${statuses[*]}); not continuing."
    exit 1
  fi
  echo "Batch reached its soft time limit. Published papers will be reused."
fi

# Both markers are emitted only after the Drive inventory has been saved.
if ! grep -Eq ' (INFO Published W[0-9]+ with [0-9]+ figures \([0-9]+ read\)|WARNING Skipped W[0-9]+: .+)$' "$log"; then
  echo "::error::No paper was published or recorded as skipped; not continuing."
  exit 1
fi

echo "follow_up_runs=$((follow_ups - 1))" >> "$GITHUB_OUTPUT"
