#!/usr/bin/env bash
# kb_overnight_tmux.sh — Launch overnight KB processor in a persistent tmux session.
#
# Uses DeepSeek V4 Flash via Ollama Cloud (OLLAMA_API_KEY from /root/.env).
# Auto-resumes on failure until all stages complete.
#
# Usage:
#   ./kb_overnight_tmux.sh start     # start (or attach if running)
#   ./kb_overnight_tmux.sh status    # tmux + worker status
#   ./kb_overnight_tmux.sh logs      # tail the log
#   ./kb_overnight_tmux.sh stop      # kill session

set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
SESSION="kb-overnight"
LOG="$DIR/exports/kb_processing/overnight.log"
ROOT_ENV="${ROOT_ENV:-/root/.env}"
WORKER="$DIR/kb_overnight_worker.py"
LABEL="${KB_EXPORT_LABEL:-12mo_2026-06-26}"

mkdir -p "$DIR/exports/kb_processing"

load_env() {
  if [[ -f "$ROOT_ENV" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ROOT_ENV"
    set +a
  fi
  eval "$(python3 "$DIR/env_loader.py" --shell)"
  export KB_EXPORT_LABEL="$LABEL"
}

case "${1:-start}" in
  start)
    if tmux has-session -t "$SESSION" 2>/dev/null; then
      echo "Session '$SESSION' already running. Attach with:"
      echo "  tmux attach -t $SESSION"
      exit 0
    fi
    tmux new-session -d -s "$SESSION" -c "$DIR" \
      "bash -lc '$DIR/kb_overnight_runner.sh'"
    echo "Started tmux session: $SESSION"
    echo "  Attach:  tmux attach -t $SESSION"
    echo "  Logs:    tail -f $LOG"
    echo "  Status:  $0 status"
    ;;
  status)
    if tmux has-session -t "$SESSION" 2>/dev/null; then
      echo "tmux session: RUNNING ($SESSION)"
    else
      echo "tmux session: STOPPED"
    fi
    load_env
    python3 "$WORKER" status 2>/dev/null || echo "No state file yet"
  ;;
  logs)
    tail -f "$LOG"
    ;;
  stop)
    tmux kill-session -t "$SESSION" 2>/dev/null && echo "Stopped $SESSION" || echo "No session"
    ;;
  *)
    echo "Usage: $0 {start|status|logs|stop}"
    exit 1
    ;;
esac
