#!/usr/bin/env bash
# =============================================================================
#  heartbeat.sh — Buttons Bebe processor liveness check (IMPROVEMENT-PLAN F5).
#
#  Runs from a systemd timer every few minutes. If the processor looks DOWN or
#  has gone quiet for 10+ minutes, it POSTs a short alert to the owner's WhatsApp
#  via the whatsapp-connect service (WHATSAPP_SEND_URL — see CLAUDE.md §5).
#
#  SAFETY: this script must NEVER take anything else down. If required tools or
#  env vars are missing, it logs the reason and exits 0. It only ever READS
#  systemd/journal state and (best-effort) POSTs one alert.
#
#  INSTALL (see deploy/vps-patches/README.md §2 "heartbeat.sh" for full unit text):
#    1. install -m 755 heartbeat.sh "/root/Buttonsbebe Agent/processor/heartbeat.sh"
#    2. drop the buttonsbebe-heartbeat.service + .timer units into
#       /etc/systemd/system/ (unit text is inline in the README)
#    3. systemctl daemon-reload
#       systemctl enable --now buttonsbebe-heartbeat.timer
#    4. verify:  systemctl list-timers | grep buttonsbebe-heartbeat
#                journalctl -u buttonsbebe-heartbeat -n 20
#  Reads WHATSAPP_SEND_URL (or WA_TOKEN/WA_PORT) from the service EnvironmentFile.
# =============================================================================
set -u

log() { echo "[heartbeat $(date -u +%FT%TZ)] $*" >&2; }

# --- configuration (all overridable via the environment / EnvironmentFile) ---
UNIT="${PROCESSOR_UNIT:-buttonsbebe-processor}"
STALE_MIN="${PROCESSOR_STALE_MINUTES:-10}"
STATE_FILE="${HEARTBEAT_STATE_FILE:-/tmp/buttonsbebe-heartbeat.state}"
SEND_URL="${WHATSAPP_SEND_URL:-}"

# Convenience: if WHATSAPP_SEND_URL wasn't given but the WhatsApp token is in the
# environment (it lives in the main .env as WA_TOKEN), build the send URL from it.
if [ -z "$SEND_URL" ] && [ -n "${WA_TOKEN:-}" ]; then
    SEND_URL="http://127.0.0.1:${WA_PORT:-8085}/connect-whatsapp/${WA_TOKEN}/send"
fi

# --- best-effort alert delivery (never fails the caller) ---------------------
send_alert() {
    msg="$1"
    if [ -z "$SEND_URL" ]; then
        log "no WHATSAPP_SEND_URL / WA_TOKEN set — cannot deliver; logging only: $msg"
        return 0
    fi
    if ! command -v curl >/dev/null 2>&1; then
        log "curl not installed — cannot deliver; logging only: $msg"
        return 0
    fi
    # The whatsapp-connect /send endpoint expects JSON {"text": "..."}.
    if curl -fsS -m 10 -X POST "$SEND_URL" \
            -H "Content-Type: application/json" \
            -d "{\"text\": \"$msg\"}" >/dev/null 2>&1; then
        log "alert delivered to WhatsApp"
    else
        log "alert POST failed (continuing anyway)"
    fi
    return 0
}

# --- 1. we need systemd to inspect the service -------------------------------
if ! command -v systemctl >/dev/null 2>&1; then
    log "systemctl not available (not a systemd host?) — nothing to check; exiting 0"
    exit 0
fi

# --- 2. decide whether the processor is alive --------------------------------
alive=1
reason=""

active="$(systemctl is-active "$UNIT" 2>/dev/null || true)"
if [ "$active" != "active" ]; then
    alive=0
    reason="service '$UNIT' is '${active:-unknown}' (expected 'active')"
elif command -v journalctl >/dev/null 2>&1; then
    # Active — but is it doing anything? No journal output for the whole window
    # means it is likely hung. (This assumes the processor logs periodically —
    # every processed job, plus ideally a per-loop heartbeat line; see README.)
    lines="$(journalctl -u "$UNIT" --since "${STALE_MIN} min ago" --no-pager -q 2>/dev/null | wc -l | tr -d ' ')"
    if [ "${lines:-0}" -eq 0 ]; then
        alive=0
        reason="no journal output from '$UNIT' in the last ${STALE_MIN} min (possibly hung)"
    fi
else
    log "journalctl not available — relying on the service-active check only"
fi

# --- 3. act on the verdict, de-duping repeat alerts --------------------------
if [ "$alive" -eq 0 ]; then
    if [ -f "$STATE_FILE" ]; then
        log "processor still down ($reason) — alert already sent, staying quiet"
    else
        log "PROCESSOR DOWN: $reason"
        send_alert "Buttons Bebe alert: the support processor looks DOWN. $reason. New tickets may not be getting draft replies. Please check the server."
        : > "$STATE_FILE" 2>/dev/null || true
    fi
else
    if [ -f "$STATE_FILE" ]; then
        log "processor recovered — clearing alert state"
        send_alert "Buttons Bebe: the support processor is back up and processing tickets again."
        rm -f "$STATE_FILE" 2>/dev/null || true
    else
        log "processor healthy ('$UNIT' active with recent activity)"
    fi
fi

exit 0
