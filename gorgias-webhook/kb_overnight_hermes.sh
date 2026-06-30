#!/usr/bin/env bash
# kb_overnight_hermes.sh — Hermes subagent supervisor (optional overlay).
#
# Spawns a Hermes agent session configured for overnight KB processing oversight.
# The actual data work runs in kb_overnight_worker.py (tmux session kb-overnight).
#
# Usage:
#   ./kb_overnight_hermes.sh

set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
HERMES_ENV="${HERMES_HOME:-/root/.hermes}/.env"

if [[ -f "$HERMES_ENV" ]]; then
  set -a; source "$HERMES_ENV"; set +a
fi

PROMPT="$(cat <<'EOF'
You are the overnight KB processing subagent for Buttons Bebe Hermes.

Your job: supervise the KB export pipeline until ALL stages complete.
Do NOT process rows manually — the worker script handles everything.

Worker: /root/gorgias-webhook/kb_overnight_worker.py
Tmux:   tmux attach -t kb-overnight
Log:    /root/gorgias-webhook/exports/kb_processing/overnight.log

Stages: clean → pair → dedupe → enrich (DeepSeek V4 Flash via Ollama Cloud)

Every check:
1. Run: python3 /root/gorgias-webhook/kb_overnight_worker.py status
2. If not complete and tmux session dead, run: /root/gorgias-webhook/kb_overnight_tmux.sh start
3. If a stage errored, run: python3 /root/gorgias-webhook/kb_overnight_worker.py run --verbose
4. Report progress: tickets cleaned, pairs, clusters, enriched count

When status shows complete, summarize output files in exports/kb_processing/ and stop.
EOF
)"

exec hermes chat -q "$PROMPT" \
  -m deepseek-v4-flash \
  --provider ollama-cloud \
  -Q --yolo \
  --source kb-overnight-supervisor
