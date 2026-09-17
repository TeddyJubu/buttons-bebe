#!/usr/bin/env bash
# =============================================================================
#  verify_hermes_toolset.sh — run this on the VPS BEFORE restarting the
#  processor with the new tool allow-list (DEV-ISSUES #8).
#
#  WHY: a misspelled toolset name is silent. Hermes does not error; it just
#  loses the tool, and drafts quietly get worse with nothing in the logs.
#  This script proves the names are right before anything goes live.
#
#  READ-ONLY. It lists configuration and, only if every other check passed,
#  runs one throwaway one-shot prompt about store policy. It never writes to
#  Gorgias, Shopify, Redo or the queue, and never touches a real ticket.
#
#  USAGE:
#     bash tools/verify_hermes_toolset.sh
#     HERMES_TOOLSETS="buttonsbebe_kb,buttonsbebe_redo" bash tools/verify_hermes_toolset.sh
#     SKIP_LIVE=1 bash tools/verify_hermes_toolset.sh   # config checks only
# =============================================================================
set -u

TOOLSETS="$(printf '%s' "${HERMES_TOOLSETS:-buttonsbebe_kb,buttonsbebe_redo,buttonsbebe_gorgias}" | tr -d '[:space:]')"
EXPECTED_SERVERS="buttonsbebe_kb buttonsbebe_redo buttonsbebe_gorgias"
FAILED=0
VERIFIER_PYTHON="${HERMES_VERIFY_PYTHON:-${PYTHON:-python3}}"

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32mOK\033[0m   %s\n' "$*"; }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$*"; FAILED=1; }
note() { printf '  ..   %s\n' "$*"; }

say "Toolsets the processor will pass"
note "$TOOLSETS"

# ── 1. hermes is on PATH ─────────────────────────────────────────────────
say "1. Hermes CLI"
if command -v hermes >/dev/null 2>&1; then
    ok "hermes found at $(command -v hermes)"
else
    bad "hermes not on PATH — run this as the same user as the processor (root)"
    exit 1
fi

# ── 2. the three MCP servers are registered ──────────────────────────────
say "2. Registered MCP servers (hermes mcp list)"
set +e
MCP_OUT="$(hermes mcp list 2>&1)"
MCP_STATUS=$?
set -e
printf '%s\n' "$MCP_OUT" | sed 's/^/     /'
if [ "$MCP_STATUS" -ne 0 ]; then
    bad "'hermes mcp list' exited $MCP_STATUS — cannot verify anything below"
fi
for server in $EXPECTED_SERVERS; do
    # Word-boundary match. A plain substring test would accept
    # "buttonsbebe_kb_old", or an error message that merely names the server.
    if printf '%s' "$MCP_OUT" | grep -Eq "(^|[^A-Za-z0-9_])${server}([^A-Za-z0-9_]|$)"; then
        ok "$server registered"
    else
        bad "$server NOT found in 'hermes mcp list'"
    fi
done

# ── 3. every toolset we ask for maps to a registered server ──────────────
say "3. Toolset names match the server keys"
IFS=',' read -r -a WANTED <<< "$TOOLSETS"
if [ "${#WANTED[@]}" -eq 0 ]; then
    bad "HERMES_TOOLSETS is empty — the processor would fall back to whatever config.yaml grants"
fi
for ts in "${WANTED[@]}"; do
    [ -z "$ts" ] && continue
    if printf '%s' "$MCP_OUT" | grep -Eq "(^|[^A-Za-z0-9_])${ts}([^A-Za-z0-9_]|$)"; then
        ok "$ts -> registered server key '$ts'"
    else
        bad "$ts -> no registered server key '$ts'. THIS WOULD SILENTLY LOSE THE TOOL."
    fi
done

# ── 4. terminal / file must not be granted to the CLI platform ───────────
say "4. Dangerous toolsets are not in scope"
CFG="${HERMES_CONFIG:-${HOME:-/root}/.hermes/config.yaml}"
if [ ! -f "$CFG" ]; then
    note "no config at $CFG — skipping (set HERMES_CONFIG to point at it)"
elif ! command -v "$VERIFIER_PYTHON" >/dev/null 2>&1; then
    bad "Selected verifier Python not available — cannot parse $CFG, so this check did not run"
else
    # A real YAML parse. The previous awk version only recognised one exact
    # layout: 4-space indent, tabs, an inline list, quoted entries, a trailing
    # comment or any nesting all reported OK while terminal/file were granted.
    set +e
    CLI_TOOLS="$(HERMES_CFG="$CFG" "$VERIFIER_PYTHON" - <<'PY'
import os, sys
try:
    import yaml
except ImportError:
    print("__NOYAML__"); sys.exit(0)
try:
    with open(os.environ["HERMES_CFG"], encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
except Exception as exc:                      # noqa: BLE001
    print("__ERROR__" + str(exc)); sys.exit(0)

found = []
def walk(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "platform_toolsets" and isinstance(value, dict):
                for entry in (value.get("cli") or []):
                    found.append(str(entry).strip())
            walk(value)
    elif isinstance(node, list):
        for item in node:
            walk(item)
walk(cfg)
print("\n".join(found))
PY
)"
    PARSE_STATUS=$?
    set -e
    case "$CLI_TOOLS" in
        __NOYAML__*)
            bad "Selected verifier Python has no yaml module — cannot parse $CFG, so this check did not run" ;;
        __ERROR__*)
            bad "could not parse $CFG: ${CLI_TOOLS#__ERROR__}" ;;
        *)
            if [ "$PARSE_STATUS" -ne 0 ]; then
                bad "config parse failed (status $PARSE_STATUS)"
            elif [ -z "$CLI_TOOLS" ]; then
                ok "platform_toolsets.cli grants nothing extra"
            else
                printf '%s\n' "$CLI_TOOLS" | sed 's/^/     granted: /'
                if printf '%s\n' "$CLI_TOOLS" | grep -Eqi '^(terminal|file|code_execution|browser|computer_use|shell)$'; then
                    bad "platform_toolsets.cli still grants shell/file tools. If 'hermes -t'"
                    bad "MERGES with these rather than replacing them, the lockdown is not"
                    bad "real. Clear the cli: list in $CFG, or confirm -t replaces it."
                else
                    ok "platform_toolsets.cli grants no shell/file tools"
                fi
            fi ;;
    esac
fi

# ── 5. the live brain loads the same SOUL/skills the repo ships ──────────
say "5. Hermes home mirror matches the repo"
REPO_HERMES="$(cd "$(dirname "$0")/.." && pwd)/hermes"
LIVE_HERMES="${HERMES_HOME:-${HOME:-/root}/.hermes}"
# Live-only files the repo mirror never ships: config, credentials, session
# and log state. Ignored BY FILENAME so the check never depends on "hermes"
# appearing in HERMES_HOME's path, and a live-only auth.json/.env can never
# be mistaken for (or masked as) content drift.
MIRROR_LIVE_ONLY='config.yaml|config.example.yaml|auth.json|\.env(\..*)?|sessions?\.json|.*\.log'
if [ ! -d "$LIVE_HERMES" ]; then
    note "no live Hermes home at $LIVE_HERMES — skipping (set HERMES_HOME to point at it)"
elif [ ! -d "$REPO_HERMES" ]; then
    bad "repo hermes/ mirror missing at $REPO_HERMES — cannot compare"
else
    set +e
    MIRROR_DIFF="$(diff -r -x '*.pyc' -x '__pycache__' "$REPO_HERMES" "$LIVE_HERMES" 2>&1)"
    MIRROR_STATUS=$?
    set -e
    # diff exits 1 on any difference, 2 on trouble (missing dir, permissions).
    # Only regular-file content drift fails the check; extra live-only files
    # (config.yaml, credentials, logs) are expected and ignored.
    if [ "$MIRROR_STATUS" -eq 0 ]; then
        ok "live Hermes home matches the repo mirror"
    elif [ "$MIRROR_STATUS" -eq 1 ]; then
        DRIFT="$(printf '%s\n' "$MIRROR_DIFF" | grep '^diff ' | head -10; \
                 printf '%s\n' "$MIRROR_DIFF" | grep '^Only in ' | grep -Ev ": ($MIRROR_LIVE_ONLY)$" | head -10)"
        if [ -z "$DRIFT" ]; then
            ok "live Hermes home matches the repo mirror (live-only files ignored)"
        else
            bad "Hermes home has drifted from the repo mirror:"
            printf '%s\n' "$DRIFT" | sed 's/^/     /'
            bad "sync the direction the runbook names (SOUL.md: live first, then checkout mirror)"
        fi
    else
        bad "could not compare Hermes home ($MIRROR_DIFF)"
    fi
fi

# ── 6. a real one-shot with the new flags still reaches the KB ───────────
say "6. Smoke test — one read-only prompt with the new flags"
if [ "$FAILED" -ne 0 ]; then
    bad "skipping the live run: a check above failed, so the lockdown is unproven"
    bad "and this step would launch a root agent under it. Fix the above first."
elif [ -n "${SKIP_LIVE:-}" ]; then
    note "SKIP_LIVE set — not running the live prompt"
else
    SMOKE_PROMPT="Call the search_kb tool from the buttonsbebe_kb MCP server with query \"return policy\" and k 2. \
Begin your reply with the exact token KBOK: followed by a one-line summary of what you found. \
Do not write, post, tag or send anything anywhere. Do not run any shell command."
    # `timeout` is GNU coreutils; present on the VPS, absent on stock macOS.
    if command -v timeout >/dev/null 2>&1; then
        note "running: hermes -t $TOOLSETS -z '<kb lookup>'  (90s timeout)"
        set +e
        SMOKE="$(timeout 90 hermes -t "$TOOLSETS" -z "$SMOKE_PROMPT" 2>&1)"
        SMOKE_STATUS=$?
        set -e
    else
        note "running: hermes -t $TOOLSETS -z '<kb lookup>'  (no timeout available)"
        set +e
        SMOKE="$(hermes -t "$TOOLSETS" -z "$SMOKE_PROMPT" 2>&1)"
        SMOKE_STATUS=$?
        set -e
    fi
    printf '%s\n' "$SMOKE" | head -20 | sed 's/^/     /'
    if [ "$SMOKE_STATUS" -ne 0 ]; then
        bad "hermes exited $SMOKE_STATUS. Do NOT deploy."
    elif [ -z "$(printf '%s' "$SMOKE" | tr -d '[:space:]')" ]; then
        bad "empty output — Hermes produced nothing. Do NOT deploy."
    elif printf '%s' "$SMOKE" | grep -qiE "unknown toolset|no such toolset|invalid toolset|unrecognized"; then
        bad "Hermes rejected a toolset name — fix HERMES_TOOLSETS before deploying."
    elif printf '%s' "$SMOKE" | grep -qiE "approve|approval required|\[y/n\]|permission denied for tool"; then
        bad "looks like it stopped on an approval prompt. Investigate before deploying;"
        bad "Inspect MCP metadata and configuration; preserve the approval guard."
    elif ! printf '%s' "$SMOKE" | grep -q "KBOK:"; then
        # Require a POSITIVE signal. Checking only for the absence of a few
        # error strings meant any other failure - "connection refused", a
        # stack trace - read as a pass and printed "safe to restart".
        bad "no KBOK: token in the reply, so the KB tool did not answer."
        bad "Do NOT deploy — the allow-list is probably wrong."
    else
        ok "one-shot reached the knowledge base with the locked-down toolset"
        note "read the summary above: it should mention real return-policy content."
    fi
fi

say "Result"
if [ "$FAILED" -eq 0 ]; then
    printf '  \033[32mAll checks passed.\033[0m Safe to restart buttonsbebe-processor.\n\n'
    exit 0
fi
printf '  \033[31mSomething failed above. Do NOT restart the processor yet.\033[0m\n\n'
exit 1
