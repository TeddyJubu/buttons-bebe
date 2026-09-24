#!/usr/bin/env python3
"""
demo_server.py — Demo Dashboard HTTP server on 127.0.0.1:8081.

Serves the dashboard UI, /api/demo/* control endpoints, and mock Gorgias REST
routes used by the patched gorgias_api client.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional, Tuple

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PROJECT_DIR)
sys.path.insert(0, SCRIPT_DIR)

import demo_patches
demo_patches.apply()

import feedback_db
from demo_gorgias_handler import handle as gorgias_handle
from demo_store import get_store
import fixtures

HOST = "127.0.0.1"
PORT = 8081
DASHBOARD_PATH = os.path.join(SCRIPT_DIR, "demo_dashboard.html")


def _read_body(handler: BaseHTTPRequestHandler) -> Optional[dict]:
    length = int(handler.headers.get("Content-Length", 0) or 0)
    if length <= 0:
        return None
    raw = handler.rfile.read(length)
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return None


def _json_response(handler: BaseHTTPRequestHandler, status: int, data) -> None:
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(body)


def _html_response(handler: BaseHTTPRequestHandler, html: str) -> None:
    body = html.encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _parse_path(path: str) -> Tuple[str, dict]:
    parsed = urllib.parse.urlparse(path)
    return parsed.path, urllib.parse.parse_qs(parsed.query)


class DemoHandler(BaseHTTPRequestHandler):
    server_version = "DemoDashboard/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("[demo] %s - %s\n" % (self.address_string(), fmt % args))

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        route, query = _parse_path(self.path)

        if route in ("/", "/demo", "/demo-dashboard.html"):
            try:
                with open(DASHBOARD_PATH, "r", encoding="utf-8") as fh:
                    return _html_response(self, fh.read())
            except OSError as e:
                return _json_response(self, 500, {"error": str(e)})

        if route == "/api/demo/state":
            store = get_store()
            stats = store.stats()
            # Feedback metrics
            fb_path = os.environ.get("FEEDBACK_DB_PATH", "")
            draft_count = 0
            last_similarity = None
            try:
                if fb_path and os.path.exists(fb_path):
                    conn = feedback_db.get_conn(fb_path)
                    try:
                        row = conn.execute("SELECT COUNT(*) AS c FROM drafts").fetchone()
                        draft_count = row["c"] if row else 0
                        row = conn.execute(
                            "SELECT similarity FROM comparisons ORDER BY id DESC LIMIT 1"
                        ).fetchone()
                        if row:
                            last_similarity = row["similarity"]
                    finally:
                        conn.close()
            except Exception:
                pass
            stats["feedback_draft_count"] = draft_count
            stats["last_similarity"] = last_similarity
            stats["scenarios"] = fixtures.list_scenarios()
            return _json_response(self, 200, stats)

        if route == "/api/demo/tickets":
            return _json_response(self, 200, {"tickets": get_store().list_tickets()})

        if route.startswith("/api/demo/tickets/"):
            tid_str = route.split("/")[-1]
            try:
                tid = int(tid_str)
            except ValueError:
                return _json_response(self, 400, {"error": "Invalid ticket id"})
            detail = get_store().get_ticket_detail(tid)
            if detail is None:
                return _json_response(self, 404, {"error": "Ticket not found"})
            return _json_response(self, 200, detail)

        if route == "/api/demo/telegram":
            bot = (query.get("bot") or ["notify"])[0]
            if bot not in ("notify", "priority"):
                return _json_response(self, 400, {"error": "bot must be notify or priority"})
            return _json_response(self, 200, {"messages": get_store().get_telegram(bot)})

        # Mock Gorgias REST (also reachable via patched gorgias_api.request)
        if route.startswith("/api/"):
            status, data = gorgias_handle("GET", self.path)
            return _json_response(self, status, data)

        return _json_response(self, 404, {"error": "Not found"})

    def do_POST(self):
        route, _query = _parse_path(self.path)
        body = _read_body(self)

        if route == "/api/demo/reset":
            get_store().reset()
            fb_path = os.environ.get("FEEDBACK_DB_PATH", "")
            if fb_path and os.path.exists(fb_path):
                os.remove(fb_path)
            feedback_db.init_db(fb_path)
            return _json_response(self, 200, {"ok": True})

        if route == "/api/demo/tickets":
            import demo_runner
            email = (body or {}).get("email", "")
            subject = (body or {}).get("subject", "Support request")
            message = (body or {}).get("message", "")
            name = (body or {}).get("name")
            if not email or not message:
                return _json_response(self, 400, {"error": "email and message required"})
            result = demo_runner.create_and_run(email, subject, message, name=name)
            return _json_response(self, 201, result)

        if route.startswith("/api/demo/tickets/") and route.endswith("/customer-message"):
            import demo_runner
            parts = route.strip("/").split("/")
            try:
                tid = int(parts[3])
            except (IndexError, ValueError):
                return _json_response(self, 400, {"error": "Invalid ticket id"})
            message = (body or {}).get("message", "")
            if not message:
                return _json_response(self, 400, {"error": "message required"})
            result = demo_runner.add_customer_message_and_run(tid, message)
            status = 404 if result.get("error") else 200
            return _json_response(self, status, result)

        if route.startswith("/api/demo/tickets/") and route.endswith("/agent-reply"):
            import demo_runner
            parts = route.strip("/").split("/")
            try:
                tid = int(parts[3])
            except (IndexError, ValueError):
                return _json_response(self, 400, {"error": "Invalid ticket id"})
            message = (body or {}).get("message", "")
            if not message:
                return _json_response(self, 400, {"error": "message required"})
            result = demo_runner.add_agent_reply_and_run(tid, message)
            status = 404 if result.get("error") else 200
            return _json_response(self, status, result)

        if route == "/api/demo/telegram/reply":
            text = (body or {}).get("text", "")
            if not text:
                return _json_response(self, 400, {"error": "text required"})
            entry = get_store().enqueue_owner_reply(text)
            return _json_response(self, 200, {"ok": True, "update_id": entry["update_id"]})

        if route.startswith("/api/demo/scenarios/"):
            name = route.split("/")[-1]
            store = get_store()
            ticket = fixtures.load_scenario_into_store(store, name)
            if ticket is None:
                return _json_response(self, 404, {"error": f"Unknown scenario: {name}"})
            import demo_runner
            result = demo_runner.run_customer_message(ticket["id"], is_new_ticket=True)
            result["scenario"] = name
            result["ticket"] = ticket
            return _json_response(self, 201, result)

        # Mock Gorgias REST writes
        if route.startswith("/api/"):
            status, data = gorgias_handle("POST", self.path, body)
            return _json_response(self, status, data)

        return _json_response(self, 404, {"error": "Not found"})

    def do_PUT(self):
        route, _query = _parse_path(self.path)
        body = _read_body(self)
        if route.startswith("/api/"):
            status, data = gorgias_handle("PUT", self.path, body)
            return _json_response(self, status, data)
        return _json_response(self, 404, {"error": "Not found"})


def main():
    store = get_store()
    store.reset()

    httpd = HTTPServer((HOST, PORT), DemoHandler)
    print(f"Demo Dashboard running at http://{HOST}:{PORT}/demo")
    print("Press Ctrl+C to stop.")

    def _shutdown(signum, frame):
        print("\nShutting down demo server...")
        demo_patches.restore()
        httpd.shutdown()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        httpd.serve_forever()
    finally:
        demo_patches.restore()


if __name__ == "__main__":
    main()
