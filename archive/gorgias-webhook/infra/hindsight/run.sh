#!/usr/bin/env bash
# run.sh — Create or recreate the Hindsight Docker container.
#
# Config: /root/.env (HINDSIGHT_API_* vars)
# Data:   hindsight-data volume (persists across recreates)
#
# Usage:
#   ./run.sh          # recreate container (stop/remove old, start fresh)
#   ./run.sh status   # show container + health
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$DIR/../.." && pwd)"
ROOT_ENV="${ROOT_ENV:-/root/.env}"
NAME="hindsight"
IMAGE="ghcr.io/vectorize-io/hindsight:latest"
VOLUME="hindsight-data"

if [[ ! -f "$ROOT_ENV" ]]; then
  echo "FATAL: $ROOT_ENV not found" >&2
  exit 1
fi

# shellcheck disable=SC1090
set -a; source "$ROOT_ENV"; set +a
eval "$(python3 "$REPO_DIR/env_loader.py" --shell)"
for req in HINDSIGHT_API_LLM_PROVIDER HINDSIGHT_API_LLM_API_KEY HINDSIGHT_API_LLM_MODEL; do
  if [[ -z "${!req:-}" ]]; then
    echo "FATAL: $req not set in $ROOT_ENV" >&2
    exit 1
  fi
done

status() {
  docker ps -a --filter "name=^${NAME}$" --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
  if docker inspect "$NAME" >/dev/null 2>&1; then
    echo
    docker inspect "$NAME" --format 'LLM provider={{range .Config.Env}}{{println .}}{{end}}' \
      | grep '^HINDSIGHT_API_LLM' || true
  fi
}

recreate() {
  if docker inspect "$NAME" >/dev/null 2>&1; then
    echo "Stopping and removing existing container ($NAME) — volume $VOLUME is kept."
    docker stop "$NAME" >/dev/null 2>&1 || true
    docker rm "$NAME" >/dev/null 2>&1 || true
  fi

  # HINDSIGHT_* vars are derived from LLM_PROVIDER + LLM_MODEL by env_loader,
  # not stored literally in /root/.env.
  local env_file
  env_file="$(mktemp)"
  {
    echo "HINDSIGHT_API_LLM_PROVIDER=${HINDSIGHT_API_LLM_PROVIDER}"
    echo "HINDSIGHT_API_LLM_API_KEY=${HINDSIGHT_API_LLM_API_KEY}"
    echo "HINDSIGHT_API_LLM_MODEL=${HINDSIGHT_API_LLM_MODEL}"
    echo "HINDSIGHT_API_RETAIN_MAX_COMPLETION_TOKENS=${HINDSIGHT_API_RETAIN_MAX_COMPLETION_TOKENS:-16000}"
  } > "$env_file"
  if [[ -z "${HINDSIGHT_API_LLM_API_KEY:-}" ]]; then
    echo "FATAL: HINDSIGHT_API_LLM_API_KEY not set — check OPENROUTER_API_KEY in $ROOT_ENV" >&2
    rm -f "$env_file"
    exit 1
  fi

  echo "Starting $NAME from $ROOT_ENV (model=$HINDSIGHT_API_LLM_MODEL provider=$HINDSIGHT_API_LLM_PROVIDER)"
  docker run -d \
    --name "$NAME" \
    --restart unless-stopped \
    -p 127.0.0.1:8888:8888 \
    -p 127.0.0.1:9999:9999 \
    --env-file "$env_file" \
    -e HINDSIGHT_API_WORKER_ID=hindsight-prod \
    -v "${VOLUME}:/home/hindsight/.pg0" \
    "$IMAGE"
  rm -f "$env_file"

  echo "Waiting for API..."
  for _ in $(seq 1 30); do
    if curl -sf "http://127.0.0.1:8888/health" >/dev/null 2>&1; then
      echo "Hindsight API healthy at http://127.0.0.1:8888"
      echo "UI at http://127.0.0.1:9999"
      return 0
    fi
    sleep 2
  done
  echo "WARN: health check did not pass within 60s — check: docker logs $NAME" >&2
  docker logs "$NAME" --tail 30
  return 1
}

case "${1:-recreate}" in
  status) status ;;
  recreate) recreate ;;
  *)
    echo "Usage: $0 {recreate|status}"
    exit 1
    ;;
esac
