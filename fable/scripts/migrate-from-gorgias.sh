#!/usr/bin/env bash
# Move your Gorgias tickets into Fable.
#
# This is a friendly front door to `python -m app.migration`. It reads your
# Gorgias account over the API (read-only — it never changes anything in
# Gorgias) and copies every ticket, its customer, and its full message history
# (including internal notes) into Fable's local database. The original Gorgias
# ids are kept, so you can run it again safely: tickets that were already
# imported are skipped, never duplicated.
#
# Nothing is ever emailed or sent anywhere — this only reads Gorgias and writes
# rows into the local Fable database.
#
# USAGE
#   ./fable/scripts/migrate-from-gorgias.sh \
#       --base-url https://YOURSTORE.gorgias.com \
#       --email you@yourstore.com \
#       --api-key YOUR_GORGIAS_API_KEY \
#       [--dry-run]
#
#   --base-url   Your Gorgias API base URL (https://YOURSTORE.gorgias.com).
#   --email      The Gorgias account email (the Basic-auth username).
#   --api-key    Your Gorgias API key (the Basic-auth password).
#   --dry-run    Show exactly what WOULD be imported without writing anything.
#
# TIP: try --dry-run first to see the counts, then run it for real.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FABLE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SERVER_DIR="$FABLE_DIR/server"
PY="${PYTHON:-python3}"

# Plain-language help when asked (or when run with no arguments): print the
# leading comment banner (skipping the shebang), stopping at the first line
# that is not a comment.
if [ "$#" -eq 0 ] || [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
    awk 'NR==1 {next} /^#/ {sub(/^# ?/, ""); print; next} {exit}' "${BASH_SOURCE[0]}"
    echo
    echo "Full option help:"
    ( cd "$SERVER_DIR" && "$PY" -m app.migration --help ) 2>/dev/null || true
    exit 0
fi

# Run the importer from fable/server so `python -m app.migration` resolves.
cd "$SERVER_DIR"
exec "$PY" -m app.migration "$@"
