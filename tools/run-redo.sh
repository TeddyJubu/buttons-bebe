#!/usr/bin/env bash
# Launcher for the Redo MCP tool. Deployed to /root/redo-mcp-run.sh (space-free
# path, because the tools folder path contains a space).
exec "/root/Buttonsbebe Agent/tools/.venv/bin/python" "/root/Buttonsbebe Agent/tools/redo_mcp.py"
