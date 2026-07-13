# Buttons Bebe — Integration tool modules

Each external integration is its **own module**: its own MCP server, its own
always-on service, its own port, and its own Hermes tool. They share one small
Python environment (`tools/.venv`) and one helper (`_common.py`) that reads the
agent `.env`. Everything here is **read-only** (GET requests only, no writes).

Set up that environment from a clean checkout with `./setup.sh`. The pinned
runtime dependencies live in `requirements.txt`; do not rely on packages from a
different service's virtual environment.

## Modules

| Module  | File           | Port | systemd service           | Hermes name          | Status |
|---------|----------------|------|---------------------------|----------------------|--------|
| Redo    | `redo_mcp.py`  | 8078 | `buttonsbebe-redo-mcp`    | `buttonsbebe_redo`   | ✅ LIVE |
| Gorgias | `gorgias_mcp.py` | 8079 | `buttonsbebe-gorgias-mcp` | `buttonsbebe_gorgias` | ✅ LIVE |

(The KB search tool is a separate module in `../KB`, port 8077.)

## Redo (live) — tools
- `list_recent_returns(limit)`
- `get_returns_for_order(order_name)`
- `get_return(return_id)`

Reads `REDO_API_KEY` + `REDO_STORE_ID` from the agent `.env`.

## Gorgias (live) — tools
- `list_recent_tickets`, `get_ticket`, `get_ticket_messages`, `get_customer`, `search_customer`

Basic Auth: `GORGIAS_API_EMAIL` (the Username from the Gorgias REST API page) +
`GORGIAS_API_KEY`, subdomain `GORGIAS_SUBDOMAIN` (bare, e.g. `buttonsbebe`).
Note: Gorgias pagination uses `limit` (not `per_page`).

## Managing any module
- status:  `systemctl status buttonsbebe-redo-mcp`   (or `-gorgias-mcp`)
- logs:    `journalctl -u buttonsbebe-redo-mcp -n 50`
- restart: `systemctl restart buttonsbebe-redo-mcp`
- verify:  `hermes mcp test buttonsbebe_redo`
- remove:  `hermes mcp remove buttonsbebe_redo && systemctl disable --now buttonsbebe-redo-mcp`

All services auto-start on boot and auto-restart on failure. Credentials live in
the agent `.env` (`/root/Buttonsbebe Agent/.env`).
