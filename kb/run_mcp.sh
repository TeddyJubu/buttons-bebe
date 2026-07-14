#!/usr/bin/env bash
# Launcher for the KB search connector (the "search_kb" tool for Hermes).
#
# Hermes calls this script. It exists at a space-free path on the server
# (/root/kb-mcp-run.sh) because the KB folder path contains a space, which the
# agent's command runner can't pass directly. This wrapper handles the quoting.
#
# Deployed copy on the server: /root/kb-mcp-run.sh
exec "/root/Buttonsbebe Agent/KB/.venv/bin/python" "/root/Buttonsbebe Agent/KB/scripts/kb_mcp_server.py"
