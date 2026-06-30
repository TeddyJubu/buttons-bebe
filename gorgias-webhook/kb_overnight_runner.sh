#!/usr/bin/env bash
# kb_overnight_runner.sh — Inner loop executed inside tmux (do not run directly).
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_ENV="${ROOT_ENV:-/root/.env}"
LABEL="${KB_EXPORT_LABEL:-12mo_2026-06-26}"
LOG="$DIR/exports/kb_processing/overnight.log"

mkdir -p "$DIR/exports/kb_processing"

if [[ -f "$ROOT_ENV" ]]; then
  set -a; source "$ROOT_ENV"; set +a
fi
eval "$(python3 "$DIR/env_loader.py" --shell)"

export KB_EXPORT_LABEL="$LABEL"

cd "$DIR"

echo "=== KB Overnight Worker $(date -u +%Y-%m-%dT%H:%M:%SZ) ===" | tee -a "$LOG"
echo "Model: $LLM_MODEL | Input: messages_${LABEL}.csv" | tee -a "$LOG"

python3 "$DIR/model_gateway.py" selfcheck 2>&1 | tee -a "$LOG" || {
  echo "FATAL: LLM selfcheck failed" | tee -a "$LOG"
  exit 1
}

while true; do
  if python3 "$DIR/kb_overnight_worker.py" status 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); raise SystemExit(0 if d.get('status')=='complete' else 1)"; then
    echo "=== ALL STAGES COMPLETE $(date -u +%Y-%m-%dT%H:%M:%SZ) ===" | tee -a "$LOG"
    python3 "$DIR/kb_overnight_worker.py" status | tee -a "$LOG"
    ls -lh "$DIR/exports/kb_processing/" | tee -a "$LOG"
    break
  fi

  echo "--- run $(date -u +%Y-%m-%dT%H:%M:%SZ) ---" | tee -a "$LOG"
  if python3 "$DIR/kb_overnight_worker.py" run --verbose 2>&1 | tee -a "$LOG"; then
    true
  else
    echo "Worker error — retry in 60s" | tee -a "$LOG"
    sleep 60
    continue
  fi

  if python3 "$DIR/kb_overnight_worker.py" status 2>/dev/null | grep -q '"status": "complete"'; then
    break
  fi
  sleep 30
done

echo "Done. Shell open for inspection." | tee -a "$LOG"
exec bash
