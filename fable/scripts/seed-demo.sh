#!/usr/bin/env bash
# Fable demo-data seeder — makes the inbox look like a real, lived-in support
# desk (~18 tickets across email/chat/whatsapp, a mix of sensitive + routine,
# some sent/noted/rewritten/closed/snoozed/tagged) using the REAL intake and
# action endpoints, so every draft is genuinely produced by the AI pipeline.
#
#   ./fable/scripts/seed-demo.sh              # seed on top of whatever's there
#   ./fable/scripts/seed-demo.sh --fresh      # wipe the DB first, then seed
#
# Works from any cwd. Boots the stack if it isn't already up. Leaves the stack
# RUNNING when it's done. Exits 0 only if seeding fully succeeded.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FABLE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$FABLE_DIR/.." && pwd)"

PY="${PYTHON:-python3}"

FRESH=0
for arg in "$@"; do
    case "$arg" in
        --fresh) FRESH=1 ;;
        *) echo "unknown arg: $arg (only --fresh is supported)" >&2; exit 2 ;;
    esac
done

export FABLE_HOST="${FABLE_HOST:-127.0.0.1}"
export FABLE_PORT="${FABLE_PORT:-9600}"
BASE="http://$FABLE_HOST:$FABLE_PORT"

# --- resolve the DB path exactly the way server/app/config.py does ---------
resolve_db_path() {
    local raw="${FABLE_DB:-}"
    if [ -z "$raw" ] && [ -f "$FABLE_DIR/.env.fable" ]; then
        raw="$(grep -E '^FABLE_DB=' "$FABLE_DIR/.env.fable" | head -1 | cut -d= -f2- \
              | sed 's/[[:space:]]*#.*$//' | sed 's/^["'"'"']//; s/["'"'"']$//' | xargs)"
    fi
    [ -z "$raw" ] && raw="fable/server/data/fable.db"
    case "$raw" in
        /*) echo "$raw" ;;
        *)  echo "$REPO_ROOT/$raw" ;;
    esac
}
DB_PATH="$(resolve_db_path)"
# pin FABLE_DB for every child process (server) so we all agree on one path
export FABLE_DB="$DB_PATH"

echo
echo "============================================================"
echo "  Fable demo-data seeder"
echo "============================================================"
echo "  repo root: $REPO_ROOT"
echo "  DB path:   $DB_PATH"
echo "  fresh:     $([ "$FRESH" = 1 ] && echo yes || echo no)"
echo

if [ "$FRESH" = 1 ]; then
    echo "▶ --fresh: stopping the stack and wiping the DB ..."
    bash "$SCRIPT_DIR/stop-server.sh" >/dev/null 2>&1 || true
    bash "$FABLE_DIR/emulators/stop-emulators.sh" >/dev/null 2>&1 || true
    rm -f "$DB_PATH" "$DB_PATH-wal" "$DB_PATH-shm"
    sleep 0.5
    echo "  done."
    echo
fi

# --- ensure the stack is up -------------------------------------------------
wait_all_health() {
    local timeout="${1:-40}" waited=0
    while [ "$waited" -lt "$timeout" ]; do
        if curl -sf "$BASE/fable/api/health" >/dev/null 2>&1 \
           && curl -sf "http://127.0.0.1:9601/health" >/dev/null 2>&1 \
           && curl -sf "http://127.0.0.1:9602/health" >/dev/null 2>&1 \
           && curl -sf "http://127.0.0.1:9603/health" >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
        waited=$((waited + 1))
    done
    return 1
}

if wait_all_health 1; then
    echo "▶ Stack already up."
else
    echo "▶ Booting the stack (Fable + Shopify/Redo/Mailbox emulators) ..."
    bash "$SCRIPT_DIR/run-all.sh" 2>&1 | sed 's/^/  /'
    if ! wait_all_health 40; then
        echo "  ERROR: stack did not become healthy in time." >&2
        exit 1
    fi
    echo "  all services healthy."
fi
echo

# --- seed --------------------------------------------------------------------
echo "▶ Seeding demo data (real intake + action endpoints) ..."
"$PY" "$SCRIPT_DIR/seed_demo.py"
RC=$?
echo

if [ "$RC" -eq 0 ]; then
    echo "============================================================"
    echo "  Done. The stack is STILL RUNNING."
    echo "  Open the console:   $BASE"
    echo "  Stop it with:       $SCRIPT_DIR/stop-server.sh && $FABLE_DIR/emulators/stop-emulators.sh"
    echo "============================================================"
else
    echo "============================================================"
    echo "  Seeding FAILED (exit $RC). Stack left running for inspection."
    echo "  Open the console:   $BASE"
    echo "============================================================" >&2
fi

exit "$RC"
