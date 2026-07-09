#!/usr/bin/env bash
# Boot the whole Fable stack: emulators (if present) + server, then check health.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FABLE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

EMU_SCRIPT="$FABLE_DIR/emulators/run-emulators.sh"
if [ -f "$EMU_SCRIPT" ]; then
    echo "== Starting emulators =="
    bash "$EMU_SCRIPT" || echo "WARNING: emulators failed to start (continuing)." >&2
else
    echo "== Emulators not built yet ($EMU_SCRIPT missing) — skipping. =="
fi

echo "== Starting Fable server =="
bash "$SCRIPT_DIR/run-server.sh" || echo "WARNING: server start reported non-zero." >&2

echo "== Health checks =="
check() {
    local name="$1" url="$2"
    if curl -sf "$url" >/dev/null 2>&1; then
        echo "  OK   $name  ($url)"
    else
        echo "  DOWN $name  ($url)"
    fi
}
check "fable-server" "http://127.0.0.1:9600/fable/api/health"
check "shopify-emu"  "http://127.0.0.1:9601/health"
check "redo-emu"     "http://127.0.0.1:9602/health"
check "mailbox-emu"  "http://127.0.0.1:9603/health"
echo "== Done =="
