#!/usr/bin/env python3
"""Loopback-only Inbox UI preview with synthetic data and no provider calls."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import urlsplit

ASSETS = {
    "index.html": "text/html; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "styles.css": "text/css; charset=utf-8",
    "icons.js": "text/javascript; charset=utf-8",
    "lucide-LICENSE.txt": "text/plain; charset=utf-8",
}


def synthetic_ticket() -> dict:
    stamp = datetime.now(timezone.utc).isoformat()
    return {
        "id": "gorgias:123",
        "subject": "Order #10312345 — size question",
        "customerName": "Example Customer",
        "fromEmail": "customer@example.invalid",
        "status": "open",
        "gorgiasPriority": "normal",
        "assignee": "Support",
        "assigneeEmail": "support@example.invalid",
        "channel": "email",
        "updatedAt": stamp,
        "syncedAt": stamp,
        "snippet": "Could you help me choose the right size for this order?",
        "readonlyDraft": "Hello,\n\nThanks for reaching out. Please share the item and size you are considering, and our team can help.",
        "draftAction": "drafted",
        "draftReason": "Synthetic local preview draft.",
        "draftSourceMessageId": "preview-message-1",
        "draftProcessedAt": stamp,
        "messages": [
            {
                "id": "preview-message-1",
                "fromName": "Example Customer",
                "fromEmail": "customer@example.invalid",
                "body": "Hi! I am considering a different size for order #10312345. Could you help me choose?",
                "at": stamp,
            }
        ],
        "shopifyRail": {
            "status": "observed",
            "customer": {
                "displayName": "Example Customer",
                "numberOfOrders": 2,
                "amountSpent": {"amount": "120.00", "currencyCode": "USD"},
            },
            "order": {
                "id": "preview-order",
                "name": "#10312345",
                "displayFinancialStatus": "PAID",
                "displayFulfillmentStatus": "UNFULFILLED",
                "currentTotalPriceSet": {
                    "shopMoney": {"amount": "64.00", "currencyCode": "USD"}
                },
                "fulfillments": [],
                "lineItems": {"nodes": []},
            },
            "returns": {"returns": {"nodes": []}},
            "history": [],
        },
    }


def make_handler(assets_dir: Path):
    ticket = synthetic_ticket()

    class Handler(BaseHTTPRequestHandler):
        def respond(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def respond_json(self, status: int, value: dict) -> None:
            self.respond(status, json.dumps(value).encode("utf-8"), "application/json")

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path == "/":
                self.send_response(302)
                self.send_header("Location", "/inbox/")
                self.end_headers()
                return
            if path == "/health":
                self.respond_json(200, {"ok": True, "synthetic": True, "readOnly": True})
                return
            if path == "/inbox":
                self.send_response(308)
                self.send_header("Location", "/inbox/")
                self.end_headers()
                return
            if not path.startswith("/inbox/"):
                self.send_error(404)
                return
            name = path.removeprefix("/inbox/") or "index.html"
            if name not in ASSETS:
                self.send_error(404)
                return
            source = assets_dir / name
            if not source.is_file():
                self.send_error(404)
                return
            self.respond(200, source.read_bytes(), ASSETS[name])

        def do_POST(self) -> None:
            if urlsplit(self.path).path != "/inbox/api/helpdesk":
                self.respond_json(404, {"ok": False, "message": "Not found."})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 8192:
                    raise ValueError("Invalid request size")
                request = json.loads(self.rfile.read(size))
                if not isinstance(request, dict) or not isinstance(request.get("arguments", {}), dict):
                    raise ValueError("Invalid invocation")
                tool = request.get("tool")
                args = request.get("arguments", {})
            except (ValueError, TypeError, json.JSONDecodeError):
                self.respond_json(400, {"ok": False, "message": "Invalid read request."})
                return

            if tool == "helpdesk.capabilities":
                result = {
                    "ok": True, "source": "gorgias_api", "readOnly": True,
                    "capabilities": {
                        "listTickets": True, "getTicket": True,
                        "sendReply": False, "createTicket": False,
                    },
                }
            elif tool == "helpdesk.list_tickets":
                rows = [ticket]
                if args.get("view", "all") not in ("all", ticket["status"]):
                    rows = []
                query = str(args.get("query", "")).strip().casefold()
                if query and not any(
                    query in str(ticket.get(key, "")).casefold()
                    for key in ("id", "subject", "customerName", "fromEmail", "snippet")
                ):
                    rows = []
                try:
                    offset = max(0, int(args.get("offset", 0)))
                    limit = min(100, max(1, int(args.get("limit", 100))))
                except (ValueError, TypeError):
                    self.respond_json(400, {"ok": False, "message": "Invalid read request."})
                    return
                result = {
                    "ok": True, "source": "gorgias_api",
                    "tickets": rows[offset:offset + limit], "total": len(rows),
                    "nextOffset": offset + limit if offset + limit < len(rows) else None,
                    "projection": {
                        "generatedAt": ticket["updatedAt"],
                        "stale": False, "syncing": False,
                        "complete": True, "ticketCount": len(rows),
                    },
                }
            elif tool == "helpdesk.get_ticket":
                if args.get("ticketId") != ticket["id"]:
                    self.respond_json(404, {"ok": False, "message": "Synthetic ticket not found."})
                    return
                result = {"ok": True, "source": "gorgias_api", "ticket": ticket}
            elif tool == "helpdesk.get_messages":
                if args.get("ticketId") != ticket["id"]:
                    self.respond_json(404, {"ok": False, "message": "Synthetic ticket not found."})
                    return
                result = {"ok": True, "source": "gorgias_api", "messages": [], "nextCursor": None}
            else:
                self.respond_json(403, {"ok": False, "message": "This preview only supports ticket reads."})
                return
            self.respond_json(200, result)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--port", type=int, default=8878)
    args = parser.parse_args()
    assets_dir = args.repo.resolve() / "console-src" / "inbox2"
    if not all((assets_dir / name).is_file() for name in ASSETS):
        parser.error(f"active Inbox assets not found in {assets_dir}")
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(assets_dir))
    print(f"Synthetic Inbox preview: http://127.0.0.1:{args.port}/inbox/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
