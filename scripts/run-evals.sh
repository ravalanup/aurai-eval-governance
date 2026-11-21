#!/usr/bin/env bash
# Run evaluation suites live through the gateway and import the results as evidence.
#   AURAI_GATEWAY_URL   gateway base URL (without /v1)
#   AURAI_EVAL_KEY      virtual key for a gateway team at tier T1 or above (so contact details are masked)
#   AURAI_PROMPTFOO_CMD override the Promptfoo command (default: npx --yes promptfoo@0.126.0)
# Usage: scripts/run-evals.sh [suite-id ...]     (default: every suite except retrieval suites)
set -euo pipefail
: "${AURAI_GATEWAY_URL:?set AURAI_GATEWAY_URL}" "${AURAI_EVAL_KEY:?set AURAI_EVAL_KEY}"
PF="${AURAI_PROMPTFOO_CMD:-npx --yes promptfoo@0.126.0}"
export PROMPTFOO_DISABLE_TELEMETRY=1 PROMPTFOO_DISABLE_UPDATE=1

suites=("$@")
if [ ${#suites[@]} -eq 0 ]; then
  mapfile -t suites < <(aurai-governance evals list | awk '$2 != "retrieval" {print $1}')
fi

status=0
for id in "${suites[@]}"; do
  work="$(mktemp -d)"
  echo "== $id"
  aurai-governance evals export "$id" --out "$work" > /dev/null
  # Promptfoo exits non-zero when cases fail; the import records those failures, so keep going.
  $PF eval -c "$work/promptfooconfig.yaml" -o "$work/results.json" --no-cache --no-write || true
  aurai-governance evals import "$work/results.json" --suite "$id" || status=1
  rm -rf "$work"
done
exit $status
