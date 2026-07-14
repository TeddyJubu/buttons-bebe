#!/usr/bin/env bash
# Restricted SSH command for the GitHub Actions deployment account.
set -euo pipefail

readonly receiver="/usr/local/sbin/buttonsbebe-deploy-receive"

if [[ "${SSH_ORIGINAL_COMMAND:-}" =~ ^deploy\ ([0-9a-f]{40})\ ([0-9a-f]{64})$ ]]; then
  exec sudo -n "$receiver" "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
fi

echo "Only the deploy command is permitted." >&2
exit 64
