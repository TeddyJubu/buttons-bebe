#!/usr/bin/env bash
# Launcher for the Gorgias MCP tool. Deployed to /root/gorgias-mcp-run.sh
# (space-free path, because the tools folder path contains a space).
exec "/root/Buttonsbebe Agent/tools/.venv/bin/python" "/root/Buttonsbebe Agent/tools/gorgias_mcp.py"
