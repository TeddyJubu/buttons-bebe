#!/usr/bin/env python3
"""Run the real Inbox API and assets against isolated synthetic read providers."""

from __future__ import annotations

import argparse
from collections import deque
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import socket
import sqlite3
import sys
import tempfile
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener


ASSET_REFERENCE = re.compile(r'''(?:href|src)\s*=\s*["']([^"']+)["']''', re.I)
MODULE_REFERENCE = re.compile(r'''(?:from\s+|import\s*)["']([^"']+)["']''')
CSS_REFERENCE = re.compile(r'''url\(\s*["']?([^"')]+)["']?\s*\)''', re.I)
SAFE_ASSET_SUFFIXES = {".css", ".js"}
SCRUB_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "AUTH",
                 "ACCESS", "BEARER", "PRIVATE", "SHOPIFY", "GORGIAS", "REDO",
                 "OPENAI", "WHATSAPP", "CONSOLE", "AWS_", "AZURE_", "GOOGLE_", "GCP_")
FIXTURE_PROVIDER_OPERATIONS = {
    "synthetic_gorgias_mcp": frozenset({
        "list_inbox_tickets", "get_ticket", "get_ticket_messages",
    }),
    "synthetic_console_projection": frozenset({"helpdesk.get_ticket"}),
    "synthetic_shopify_snapshot": frozenset({"read"}),
    "synthetic_redo_snapshot": frozenset({"read"}),
}
PROVIDER_TICKET_ID = re.compile(r"[1-9][0-9]{0,17}", re.ASCII)


def scrub_provider_environment() -> list[str]:
    removed = []
    for name in list(os.environ):
        if any(marker in name.upper() for marker in SCRUB_MARKERS):
            removed.append(name)
            os.environ.pop(name, None)
    return removed


class LoopbackEgressGuard:
    """Block provider/network destinations while allowing this fixture HTTP server."""

    def __init__(self) -> None:
        self.blocked: deque[dict[str, str]] = deque(maxlen=100)
        self._originals: dict[str, Any] = {}

    @staticmethod
    def _host(address: Any) -> str:
        if isinstance(address, tuple) and address:
            return str(address[0])
        return str(address)

    @staticmethod
    def _is_loopback(host: str) -> bool:
        if host.casefold() in {"localhost", "localhost.localdomain"}:
            return True
        try:
            return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
        except ValueError:
            return False

    def _reject(self, operation: str, address: Any) -> None:
        host = self._host(address)
        self.blocked.append({"operation": operation, "host": host[:120]})
        raise OSError("fixture stack blocks non-loopback network access")

    def install(self) -> None:
        self._originals = {
            "connect": socket.socket.connect,
            "connect_ex": socket.socket.connect_ex,
            "sendto": socket.socket.sendto,
            "getaddrinfo": socket.getaddrinfo,
        }
        original_connect = self._originals["connect"]
        original_connect_ex = self._originals["connect_ex"]
        original_sendto = self._originals["sendto"]
        original_getaddrinfo = self._originals["getaddrinfo"]

        def connect(sock: socket.socket, address: Any) -> Any:
            if not self._is_loopback(self._host(address)):
                self._reject("connect", address)
            return original_connect(sock, address)

        def connect_ex(sock: socket.socket, address: Any) -> int:
            if not self._is_loopback(self._host(address)):
                self._reject("connect_ex", address)
            return original_connect_ex(sock, address)

        def sendto(sock: socket.socket, *args: Any) -> int:
            address = args[-1]
            if not self._is_loopback(self._host(address)):
                self._reject("sendto", address)
            return original_sendto(sock, *args)

        def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
            if host is not None and not self._is_loopback(str(host)):
                self._reject("getaddrinfo", host)
            return original_getaddrinfo(host, *args, **kwargs)

        socket.socket.connect = connect
        socket.socket.connect_ex = connect_ex
        socket.socket.sendto = sendto
        socket.getaddrinfo = getaddrinfo

    def restore(self) -> None:
        for name, original in self._originals.items():
            if name == "getaddrinfo":
                socket.getaddrinfo = original
            else:
                setattr(socket.socket, name, original)
        self._originals.clear()


def load_fixture(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("version") != 1 or not isinstance(value.get("tickets"), list):
        raise ValueError(f"Unsupported fixture file: {path}")
    ids = [str(ticket.get("id", "")) for ticket in value["tickets"]]
    if len(ids) != len(set(ids)) or any(PROVIDER_TICKET_ID.fullmatch(item) is None for item in ids):
        raise ValueError("Fixture tickets need unique ASCII positive provider IDs of 1 to 18 digits.")
    for case, ticket_id in value.get("cases", {}).items():
        if str(ticket_id) not in ids:
            raise ValueError(f"Fixture case {case!r} names a missing ticket.")
    return value


def bind_loopback_listener(port: int) -> tuple[socket.socket, int]:
    """Bind once and hand the still-owned socket directly to Uvicorn.

    Port zero asks the operating system for an unused loopback port. Keeping the
    socket bound until Uvicorn takes ownership avoids a reserve/close/rebind gap.
    """
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("Fixture port must be zero or between 1 and 65535.")
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind(("127.0.0.1", port))
        listener.listen(socket.SOMAXCONN)
        actual_port = int(listener.getsockname()[1])
        return listener, actual_port
    except BaseException:
        listener.close()
        raise


def discover_local_assets(assets_dir: Path) -> set[str]:
    """Serve only local assets referenced by the Inbox HTML, JS, and CSS."""
    pending = ["index.html"]
    found: set[str] = set()
    while pending:
        name = pending.pop()
        if name in found:
            continue
        if Path(name).name != name:
            raise ValueError(f"Inbox asset must be a top-level file: {name}")
        path = assets_dir / name
        if not path.is_file():
            raise FileNotFoundError(f"Inbox asset is missing: {path}")
        found.add(name)
        if path.suffix not in {".html", ".js", ".css"}:
            continue
        source = path.read_text(encoding="utf-8")
        references: list[str] = []
        if path.suffix == ".html":
            references.extend(match.group(1) for match in ASSET_REFERENCE.finditer(source))
        elif path.suffix == ".js":
            references.extend(match.group(1) for match in MODULE_REFERENCE.finditer(source))
        else:
            references.extend(match.group(1) for match in CSS_REFERENCE.finditer(source))
        for reference in references:
            if reference.startswith(("https://", "http://", "data:", "//", "#")):
                continue
            if reference.startswith("/inbox/"):
                reference = reference.removeprefix("/inbox/")
            elif reference.startswith("./"):
                reference = reference.removeprefix("./")
            else:
                continue
            candidate = PurePosixPath(reference)
            if len(candidate.parts) != 1 or candidate.suffix not in SAFE_ASSET_SUFFIXES:
                continue
            pending.append(candidate.name)
    return found


class FixtureProviders:
    """Synthetic provider reads and bounded observations for canonical reads."""

    def __init__(self, data: dict[str, Any], live_api: Any, content_adapter: Any) -> None:
        self.data = data
        self.live_api = live_api
        self.curate_message = content_adapter.curate_message
        self.curate_messages = content_adapter.curate_messages
        self.curate_ticket = content_adapter.curate_ticket
        self.curate_summaries = content_adapter.curate_summaries
        self.tickets = {str(ticket["id"]): deepcopy(ticket) for ticket in data["tickets"]}
        self.messages = {
            key: [deepcopy(message) for message in rows]
            for key, rows in data.get("messages", {}).items()
        }
        self.calls: deque[dict[str, Any]] = deque(maxlen=500)
        self.call_count = 0
        self.operation_counts: dict[tuple[str, str], int] = {}
        self.lock = threading.Lock()

    def record(self, provider: str, operation: str, arguments: dict[str, Any]) -> None:
        if operation not in FIXTURE_PROVIDER_OPERATIONS.get(provider, frozenset()):
            raise RuntimeError("Unexpected synthetic provider operation.")
        with self.lock:
            self.call_count += 1
            key = (provider, operation)
            self.operation_counts[key] = self.operation_counts.get(key, 0) + 1
            self.calls.append({"provider": provider, "operation": operation,
                               "arguments": deepcopy(arguments)})

    def diagnostic_snapshot(self) -> tuple[int, list[dict[str, Any]], list[dict[str, Any]]]:
        with self.lock:
            observations = [
                {"provider": provider, "operation": operation, "count": count}
                for (provider, operation), count in sorted(self.operation_counts.items())
            ]
            return self.call_count, deepcopy(list(self.calls)), observations

    def new_mcp(self, worker: Any = None) -> SyntheticMCP:
        return SyntheticMCP(self, worker)

    def list_tickets(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self.record("synthetic_gorgias_mcp", "list_inbox_tickets", arguments)
        rows = sorted(self.tickets.values(),
                      key=lambda ticket: ticket.get("updated_datetime") or "", reverse=True)
        try:
            offset = max(0, int(arguments.get("cursor") or 0))
            limit = min(100, max(1, int(arguments.get("limit", 100))))
        except (TypeError, ValueError) as error:
            raise ValueError("Synthetic Gorgias pagination arguments are invalid.") from error
        page = rows[offset:offset + limit]
        next_offset = offset + len(page)
        cursor = str(next_offset) if next_offset < len(rows) else None
        return self.curate_summaries({"data": deepcopy(page), "meta": {"next_cursor": cursor}})

    def get_ticket(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self.record("synthetic_gorgias_mcp", "get_ticket", arguments)
        key = str(arguments.get("ticket_id", ""))
        ticket = self.tickets.get(key)
        if ticket is None:
            return {"error": "404 synthetic ticket not found"}
        return self.curate_ticket(deepcopy(ticket))

    def get_messages(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self.record("synthetic_gorgias_mcp", "get_ticket_messages", arguments)
        key = str(arguments.get("ticket_id", ""))
        if key not in self.tickets:
            return {"error": "404 synthetic ticket not found"}
        rows = list(reversed(self.messages.get(key, [])))
        try:
            offset = max(0, int(arguments.get("cursor") or 0))
            limit = min(50, max(1, int(arguments.get("limit", 50))))
        except (TypeError, ValueError) as error:
            raise ValueError("Synthetic Gorgias message pagination arguments are invalid.") from error
        page = rows[offset:offset + limit]
        next_offset = offset + len(page)
        cursor = str(next_offset) if next_offset < len(rows) else None
        return self.curate_messages({"data": deepcopy(page), "meta": {"next_cursor": cursor}})

    def shopify_snapshot_read(self, ticket_id: str) -> None:
        self.record("synthetic_shopify_snapshot", "read", {"ticketId": ticket_id})

    def redo_snapshot_read(self, ticket_id: str) -> None:
        self.record("synthetic_redo_snapshot", "read", {"ticketId": ticket_id})


class SyntheticMCP:
    """In-process replacement for the fixed, read-only Gorgias MCP client."""

    def __init__(self, providers: FixtureProviders, worker: Any = None) -> None:
        self.providers = providers
        self.worker = worker
        self.entered = False

    def check_stop(self) -> None:
        if self.worker and self.worker.stop.is_set():
            raise self.providers.live_api.Cancelled()

    def __enter__(self) -> SyntheticMCP:
        api = self.providers.live_api
        if self.worker:
            self.worker.update("capacity")
        deadline = time.monotonic() + api.CAPACITY_TIMEOUT
        while True:
            self.check_stop()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise api.Unavailable()
            if api.CAPACITY.acquire(timeout=min(0.2, remaining)):
                break
        self.entered = True
        try:
            self.check_stop()
        except Exception:
            api.CAPACITY.release()
            self.entered = False
            raise
        if self.worker:
            self.worker.update("transport")
        return self

    def __exit__(self, *_: Any) -> None:
        if self.entered:
            self.providers.live_api.CAPACITY.release()
            self.entered = False

    def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.check_stop()
        if self.worker:
            self.worker.update("transport")
        if tool == "list_inbox_tickets":
            result = self.providers.list_tickets(arguments)
        elif tool == "get_ticket":
            result = self.providers.get_ticket(arguments)
        elif tool == "get_ticket_messages":
            result = self.providers.get_messages(arguments)
        else:
            raise RuntimeError("The synthetic provider only allows fixed Gorgias read tools.")
        self.check_stop()
        return result


class FixtureRuntime:
    def __init__(self, root: Path, data: dict[str, Any], port: int,
                 state_dir: Path, egress_guard: LoopbackEgressGuard) -> None:
        self.root = root
        self.assets_dir = root / "console-src" / "inbox2"
        self.asset_names = discover_local_assets(self.assets_dir)
        self.data = data
        self.port = port
        self.state_dir = state_dir
        self.egress_guard = egress_guard
        self.providers: FixtureProviders | None = None
        self.console_requests: deque[dict[str, str]] = deque(maxlen=100)
        self.mutation_attempts: deque[dict[str, str]] = deque(maxlen=100)
        self.request_lock = threading.Lock()

    def configure_api(self) -> Any:
        if str(self.root) not in sys.path:
            sys.path.insert(0, str(self.root))
        api_dir = self.root / "console-src" / "inbox2"
        if str(api_dir) not in sys.path:
            sys.path.insert(0, str(api_dir))
        from tools import gorgias_content
        import customer_details
        import live_api
        import redo_details
        import shop_rail

        with live_api.WORKER_LOCK:
            worker = live_api.WORKER
            if worker and worker.thread and worker.thread.is_alive():
                raise RuntimeError("A previous Inbox fixture worker is still running.")
            live_api.WORKER = None

        live_api.DB = self.state_dir / "live.sqlite3"
        live_api.CAPACITY = threading.BoundedSemaphore(2)
        live_api.DETAIL_LOCK = threading.Lock()
        live_api.DETAIL_CACHE = {}
        live_api.OPERATOR_EMAIL = str(self.data["operatorEmail"]).casefold()
        live_api.MCP_URL = "http://127.0.0.1:9/fixture-provider-disabled"
        customer_details.QUEUE = self.state_dir / "customer-requests.sqlite3"
        customer_details.SNAPSHOT = self.state_dir / "customer-snapshot.sqlite3"
        redo_details.QUEUE = self.state_dir / "redo-requests.sqlite3"
        redo_details.SNAPSHOT = self.state_dir / "redo-snapshot.sqlite3"
        os.environ["SHOP_RAIL_PATH"] = str(self.state_dir / "shop-rail-snapshot.sqlite3")
        os.environ["INBOX_PROJECTION_PATH"] = str(self.state_dir / "projection.sqlite3")

        self.providers = FixtureProviders(self.data, live_api, gorgias_content)
        live_api.MCP = self.providers.new_mcp
        canonical_projection_query = live_api.projection_query

        def observe_canonical_projection(tool: str, arguments: dict[str, Any]) -> Any:
            result = canonical_projection_query(tool, arguments)
            if (isinstance(result, dict) and result.get("ok") is True
                    and result.get("source") == "canonical_projection"):
                self.providers.record("synthetic_console_projection", tool, arguments)
            return result

        live_api.projection_query = observe_canonical_projection

        attach_shop_rail = live_api.attach_shop_rail
        attach_customer_details = live_api.attach_customer_details

        def read_shop_rail(ticket: dict[str, Any]) -> dict[str, Any]:
            self.providers.shopify_snapshot_read(str(ticket.get("id", "")))
            return attach_shop_rail(ticket)

        def read_customer_details(ticket: dict[str, Any]) -> dict[str, Any]:
            ticket_id = str(ticket.get("id", ""))
            self.providers.shopify_snapshot_read(ticket_id)
            self.providers.redo_snapshot_read(ticket_id)
            return attach_customer_details(ticket)

        live_api.attach_shop_rail = read_shop_rail
        live_api.attach_customer_details = read_customer_details
        self._seed_rail_snapshots(live_api, customer_details, redo_details, shop_rail.PAYLOAD_VERSION)
        self._seed_projection(live_api, live_api.projection)
        return live_api

    def _seed_rail_snapshots(self, live_api: Any, customer_details: Any,
                             redo_details: Any, payload_version: int) -> None:
        timestamp = datetime.now(timezone.utc).isoformat()
        fetched_epoch = time.time()
        shop_rows: list[tuple[str, dict[str, Any]]] = []
        customer_rows: list[tuple[str, dict[str, Any]]] = []
        redo_rows: list[tuple[str, dict[str, Any]]] = []

        for raw in self.data["tickets"]:
            fixture_id = str(raw["id"])
            shop_seed = self.data.get("shopify", {}).get(fixture_id)
            if not shop_seed:
                continue
            customer_email = str((raw.get("customer") or {}).get("email") or "").strip().casefold()
            ticket = live_api.summary(raw)
            raw_messages = self.providers.messages.get(fixture_id, []) if self.providers else []
            ticket["messages"] = [live_api.message(self.providers.curate_message(message))
                                   for message in raw_messages] if self.providers else []
            request = customer_details.request_ticket(ticket)
            if not request:
                raise ValueError(f"Fixture Shopify data has no verified identity for {fixture_id}.")

            payload = deepcopy(shop_seed)
            self._validate_fixture_currency(payload)
            payload.update(payloadVersion=payload_version, email=customer_email,
                           requestKey=customer_details.request_key(request),
                           fetchedAt=timestamp, fetchedAtEpoch=fetched_epoch)
            shop_rows.append((ticket["id"], payload))
            customer_rows.append((ticket["id"], deepcopy(payload)))

            redo_seed = self.data.get("redo", {}).get(fixture_id)
            if redo_seed:
                order = payload.get("order") or {}
                verified_orders = [order.get("name")] if order.get("name") else []
                verified_orders.extend(item.get("name") for item in payload.get("history", [])
                                       if isinstance(item, dict) and item.get("name"))
                redo_request = redo_details.make_request(ticket["id"], customer_email,
                                                         list(dict.fromkeys(verified_orders))[:3])
                if not redo_request:
                    raise ValueError(f"Fixture Redo data has no verified order for {fixture_id}.")
                order_rows = deepcopy(redo_seed.get("orders", {}))
                for name in redo_request["orders"]:
                    entry = order_rows.setdefault(name, {"status": "empty", "returns": []})
                    entry.setdefault("observedAt", timestamp)
                    entry.setdefault("observedAtEpoch", fetched_epoch)
                redo_payload = {"requestKey": redo_details.request_key(redo_request),
                                "email": customer_email, "orders": order_rows,
                                "fetchedAt": timestamp, "fetchedAtEpoch": fetched_epoch}
                redo_rows.append((ticket["id"], redo_payload))

        self._write_snapshot(self.state_dir / "shop-rail-snapshot.sqlite3", "rail", shop_rows)
        self._write_snapshot(customer_details.SNAPSHOT, "rail", customer_rows)
        self._write_snapshot(redo_details.SNAPSHOT, "redo", redo_rows)

    @staticmethod
    def _validate_fixture_currency(value: Any) -> None:
        if isinstance(value, dict):
            currency = value.get("currencyCode")
            if currency is not None and (not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency)):
                raise ValueError("Fixture Shopify money must use a three-letter currency code.")
            for child in value.values():
                FixtureRuntime._validate_fixture_currency(child)
        elif isinstance(value, list):
            for child in value:
                FixtureRuntime._validate_fixture_currency(child)

    def _seed_projection(self, live_api: Any, projection: Any) -> None:
        path = self.state_dir / "projection.sqlite3"
        generated_epoch = time.time()
        generated_at = datetime.fromtimestamp(generated_epoch, timezone.utc).isoformat()
        rows: list[tuple[str, str, dict[str, Any], dict[str, Any]]] = []
        for raw in self.data["tickets"]:
            ticket_id = str(raw["id"])
            curated = self.providers.curate_ticket(deepcopy(raw))
            ticket = live_api.summary(curated)
            raw_messages = self.providers.messages.get(ticket_id, [])
            messages = [live_api.message(self.providers.curate_message(message))
                        for message in raw_messages]
            messages.sort(key=lambda message: (live_api.epoch(message.get("at")), message.get("id", "")))
            ticket.update(messages=messages, messagesNextCursor=None, historyIncomplete=False,
                          observedMessageCount=len(messages), syncedAt=generated_at, syncStale=False,
                          statusEvents=[], projectionSource=True)
            ticket.update(deepcopy(self.data.get("drafts", {}).get(ticket_id, {})))
            summary = {key: value for key, value in ticket.items()
                       if key not in ("messages", "readonlyDraft")}
            rows.append((ticket["id"], ticket.get("updatedAt") or generated_at, summary, ticket))

        metadata = {
            "version": projection.VERSION,
            "generatedAtEpoch": generated_epoch,
            "generatedAt": generated_at,
            "ticketCount": len(rows),
            "windowDays": 90,
            "ticketLimit": None,
            "messageLimit": 100,
            "truncated": False,
            "historyIncomplete": True,
            "spamCount": sum(bool(row[2].get("spam")) for row in rows),
            "trashCount": sum(bool(row[2].get("trashed")) for row in rows),
            "flaggedOverlap": sum(bool(row[2].get("spam")) and bool(row[2].get("trashed")) for row in rows),
            "sourceWatermark": max((row[1] for row in rows), default=None),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("CREATE TABLE metadata(id INTEGER PRIMARY KEY,payload TEXT NOT NULL)")
            db.execute("CREATE TABLE tickets(id TEXT PRIMARY KEY,observed_at TEXT NOT NULL,summary TEXT NOT NULL,detail TEXT NOT NULL)")
            db.execute("CREATE INDEX ticket_order ON tickets(observed_at DESC,id)")
            db.execute("INSERT INTO metadata VALUES(1,?)", (json.dumps(metadata, ensure_ascii=False),))
            db.executemany("INSERT INTO tickets VALUES(?,?,?,?)", [
                (ticket_id, observed_at, json.dumps(summary, ensure_ascii=False),
                 json.dumps(detail, ensure_ascii=False))
                for ticket_id, observed_at, summary, detail in rows
            ])
            integrity = db.execute("PRAGMA integrity_check").fetchone()
            if not integrity or integrity[0] != "ok":
                raise sqlite3.DatabaseError("Invalid canonical fixture projection.")

    @staticmethod
    def _write_snapshot(path: Path, table: str,
                        rows: list[tuple[str, dict[str, Any]]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute(f"CREATE TABLE {table}(ticket_id TEXT PRIMARY KEY,payload TEXT NOT NULL,updated_at TEXT NOT NULL)")
            for ticket_id, payload in rows:
                db.execute(f"INSERT INTO {table} VALUES(?,?,?)",
                           (ticket_id, json.dumps(payload, ensure_ascii=False),
                            datetime.now(timezone.utc).isoformat()))
            result = db.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise sqlite3.DatabaseError(f"Invalid fixture snapshot at {path.name}.")

    def record_console_request(self, method: str, path: str) -> None:
        item = {"method": method.upper(), "path": path[:240]}
        with self.request_lock:
            self.console_requests.append(item)
            if item["method"] in {"POST", "PUT", "PATCH", "DELETE"}:
                self.mutation_attempts.append(item)

    def database_status(self, live_api: Any) -> dict[str, Any]:
        live_path = Path(live_api.DB)
        expected = {str(ticket["id"]) for ticket in self.data["tickets"]
                    if not ticket.get("trashed_datetime")}
        loaded: set[str] = set()
        meta: dict[str, Any] = {}
        with closing(sqlite3.connect(live_path)) as db:
            db.row_factory = sqlite3.Row
            loaded = {str(row[0]).removeprefix("gorgias:")
                      for row in db.execute("SELECT id FROM tickets")}
            meta = live_api.get_meta(db)
        stores = {
            "liveApi": self._sqlite_status(live_path, "tickets", "meta"),
            "canonicalProjection": self._sqlite_status(self.state_dir / "projection.sqlite3", "tickets", "metadata"),
            "shopifyRail": self._sqlite_status(self.state_dir / "shop-rail-snapshot.sqlite3", "rail"),
            "customerSnapshot": self._sqlite_status(self.state_dir / "customer-snapshot.sqlite3", "rail"),
            "customerQueue": self._sqlite_status(self.state_dir / "customer-requests.sqlite3", "shop_requests"),
            "redoSnapshot": self._sqlite_status(self.state_dir / "redo-snapshot.sqlite3", "redo"),
            "redoQueue": self._sqlite_status(self.state_dir / "redo-requests.sqlite3", "redo_requests"),
        }
        missing = sorted(expected - loaded)
        return {"stores": stores, "storesReady": all(item["ready"] for item in stores.values()),
                "syncComplete": bool(meta.get("complete")) and not meta.get("syncing") and not meta.get("error"),
                "expectedNonTrashTicketCount": len(expected), "loadedTicketCount": len(loaded),
                "missingNonTrashTicketIds": missing,
                "fixtureDataReady": not missing and all(item["ready"] for item in stores.values()),
                "projection": {"complete": bool(meta.get("complete")),
                               "syncing": bool(meta.get("syncing")),
                               "error": bool(meta.get("error")),
                               "generatedAt": meta.get("generatedAt")}}

    @staticmethod
    def _sqlite_status(path: Path, *tables: str) -> dict[str, Any]:
        result: dict[str, Any] = {"file": path.name, "ready": False, "integrity": "missing"}
        if not path.is_file():
            return result
        try:
            with closing(sqlite3.connect(path)) as db:
                integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
                present = {row[0] for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
            result.update(integrity=integrity,
                          tablesReady=all(table in present for table in tables),
                          ready=integrity == "ok" and all(table in present for table in tables))
        except sqlite3.Error as error:
            result["integrity"] = type(error).__name__
        return result

    def diagnostics(self, live_api: Any) -> dict[str, Any]:
        database = self.database_status(live_api)
        providers = self.providers
        if providers:
            call_count, calls, operation_counts = providers.diagnostic_snapshot()
        else:
            call_count, calls, operation_counts = 0, [], []
        return {"ok": True, "synthetic": True, "readOnly": True,
                "fixtureCase": self.case, "selectedTicketId": self.selected_ticket_id,
                **database,
                "providerMode": "injected in-process fixtures; no real provider client",
                "providerCallCount": call_count,
                "providerCalls": calls,
                "providerOperationCounts": operation_counts,
                "blockedNetworkAttempts": list(self.egress_guard.blocked),
                "consoleRequests": list(self.console_requests),
                "mutationAttempts": list(self.mutation_attempts)}

    def set_case(self, case: str) -> None:
        self.case = case
        self.selected_ticket_id = f"gorgias:{self.data['cases'][case]}"

    def build_app(self, live_api: Any) -> Any:
        from fastapi import Request
        from fastapi.responses import JSONResponse, RedirectResponse, Response

        app = live_api.app

        @app.middleware("http")
        async def fixture_browser_policy(request: Request, call_next: Any) -> Response:
            response = await call_next(request)
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; connect-src 'self'; img-src 'self' data:; "
                "style-src 'self' 'unsafe-inline'; font-src 'self' data:; "
                "script-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'"
            )
            return response

        @app.get("/", include_in_schema=False)
        def fixture_home() -> RedirectResponse:
            return RedirectResponse(f"/inbox/?{urlencode({'ticket': self.selected_ticket_id})}",
                                    status_code=307)

        @app.get("/inbox", include_in_schema=False)
        def fixture_inbox_slash() -> RedirectResponse:
            return RedirectResponse(f"/inbox/?{urlencode({'ticket': self.selected_ticket_id})}",
                                    status_code=308)

        @app.get("/inbox/", include_in_schema=False)
        def fixture_index() -> Response:
            return Response((self.assets_dir / "index.html").read_bytes(),
                            media_type="text/html; charset=utf-8")

        @app.get("/inbox/{asset_name}", include_in_schema=False)
        def fixture_asset(asset_name: str) -> Response:
            if asset_name not in self.asset_names or asset_name == "index.html":
                return Response(status_code=404)
            path = self.assets_dir / asset_name
            media_type = "text/css" if path.suffix == ".css" else "text/javascript"
            return Response(path.read_bytes(), media_type=media_type)

        @app.get("/__fixture__/diagnostics", include_in_schema=False)
        def fixture_diagnostics() -> dict[str, Any]:
            return self.diagnostics(live_api)

        async def deny_console(request: Request) -> JSONResponse:
            self.record_console_request(request.method, request.url.path)
            return JSONResponse({"ok": False, "error": "fixture_read_only",
                                 "message": "The synthetic fixture stack blocks console actions."},
                                status_code=403)

        app.add_route("/console", deny_console, methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"])
        app.add_route("/console/{path:path}", deny_console,
                      methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"])
        return app


def http_bytes(port: int, path: str, method: str = "GET",
               value: dict[str, Any] | None = None, timeout: float = 1.0) -> tuple[int, bytes]:
    data = None if value is None else json.dumps(value).encode("utf-8")
    headers = {"Accept": "application/json, text/html, text/css, text/javascript"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = Request(f"http://127.0.0.1:{port}{path}", data=data,
                      headers=headers, method=method)
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, response.read(1_000_000)
    except HTTPError as error:
        return error.code, error.read(1_000_000)


def http_json(port: int, path: str, method: str = "GET",
              value: dict[str, Any] | None = None, timeout: float = 1.0) -> tuple[int, Any]:
    status, body = http_bytes(port, path, method, value, timeout)
    if not body:
        return status, None
    try:
        return status, json.loads(body)
    except json.JSONDecodeError:
        try:
            return status, body.decode("utf-8", errors="replace")
        except AttributeError:
            return status, None


def wait_until_ready(port: int, runtime: FixtureRuntime, live_api: Any,
                     server_thread: threading.Thread, timeout: float = 25.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    last_error = "service has not answered yet"
    while time.monotonic() < deadline:
        if not server_thread.is_alive():
            raise RuntimeError("The Inbox API exited before readiness.")
        try:
            status, health = http_json(port, "/health")
            if status != 200 or not isinstance(health, dict) or not health.get("ok"):
                last_error = f"health returned HTTP {status}"
                time.sleep(0.1)
                continue
            ready_status, readiness = http_json(port, "/ready")
            expected_checks = {"storage": "ok", "worker": "ok", "ticketData": "fresh", "projection": "fresh"}
            if (ready_status != 200 or not isinstance(readiness, dict)
                    or readiness.get("status") != "ready"
                    or readiness.get("checks") != expected_checks):
                last_error = f"actual readiness returned HTTP {ready_status}: {readiness}"
                time.sleep(0.1)
                continue
            status, last = http_json(port, "/__fixture__/diagnostics")
            if status == 200 and last.get("fixtureDataReady") and last.get("syncComplete"):
                list_status, listing = http_json(port, "/inbox/api/helpdesk", "POST",
                                                 {"tool": "helpdesk.list_tickets",
                                                  "arguments": {"view": "all", "limit": 100}})
                if list_status != 200 or not listing.get("ok") or not listing.get("projection", {}).get("complete"):
                    last_error = f"actual list API returned HTTP {list_status}"
                    time.sleep(0.1)
                    continue
                selected_status, selected = http_json(
                    port, "/inbox/api/helpdesk", "POST",
                    {"tool": "helpdesk.get_ticket",
                     "arguments": {"ticketId": runtime.selected_ticket_id}})
                ticket = selected.get("ticket") if isinstance(selected, dict) else None
                if selected_status != 200 or not selected.get("ok") or ticket.get("id") != runtime.selected_ticket_id:
                    last_error = f"actual ticket API returned HTTP {selected_status}"
                    time.sleep(0.1)
                    continue
                if not isinstance(ticket.get("messages"), list):
                    last_error = "actual ticket API returned no message list"
                    time.sleep(0.1)
                    continue
                assets_ready = True
                for asset_name in sorted(runtime.asset_names):
                    asset_path = "/inbox/" if asset_name == "index.html" else f"/inbox/{asset_name}"
                    asset_status, asset_body = http_bytes(port, asset_path)
                    if asset_status != 200 or not asset_body:
                        last_error = f"UI asset {asset_name} returned HTTP {asset_status}"
                        assets_ready = False
                        break
                if not assets_ready:
                    time.sleep(0.1)
                    continue
                last["actualReadiness"] = readiness
                return last
            last_error = "fixture stores or ticket synchronization are not ready"
        except (OSError, URLError, json.JSONDecodeError, TypeError, AttributeError) as error:
            last_error = type(error).__name__
        time.sleep(0.1)
    raise TimeoutError(f"Inbox fixture readiness timed out: {last_error}. Last diagnostics: {last}")


def serve(args: argparse.Namespace) -> int:
    root = args.repo.resolve()
    fixture_path = (root / "testing" / "inbox_fixtures" / "long_email.json"
                    if args.fixtures is None else args.fixtures.resolve())
    data = load_fixture(fixture_path)
    if args.case not in data.get("cases", {}):
        choices = ", ".join(sorted(data.get("cases", {})))
        raise ValueError(f"Unknown fixture case {args.case!r}. Choose one of: {choices}.")
    if not (root / "console-src" / "inbox2" / "live_api.py").is_file():
        raise FileNotFoundError(f"Inbox API source not found under {root}.")

    removed_env = scrub_provider_environment()
    os.environ["INBOX_OPERATOR_GORGIAS_EMAIL"] = str(data["operatorEmail"])
    egress_guard = LoopbackEgressGuard()
    egress_guard.install()
    try:
        import uvicorn

        with tempfile.TemporaryDirectory(prefix="buttonsbebe-inbox-fixture-") as temporary:
            listener, port = bind_loopback_listener(args.port)
            try:
                state_dir = Path(temporary)
                runtime = FixtureRuntime(root, data, port, state_dir, egress_guard)
                runtime.set_case(args.case)
                live_api = runtime.configure_api()
                app = runtime.build_app(live_api)
                config = uvicorn.Config(app, host="127.0.0.1", port=port,
                                        access_log=False, log_level="warning",
                                        server_header=False)
                server = uvicorn.Server(config)
                server.install_signal_handlers = lambda: None
                thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]},
                                         name="fixture-inbox-api", daemon=False)
                try:
                    thread.start()
                    print(f"FIXTURE_PORT={port}", flush=True)
                    diagnostics = wait_until_ready(port, runtime, live_api, thread)
                    print("Synthetic Inbox fixture stack is ready.", flush=True)
                    print(f"UI: http://127.0.0.1:{port}/inbox/?ticket={runtime.selected_ticket_id}", flush=True)
                    print(f"Health: http://127.0.0.1:{port}/health", flush=True)
                    print(f"Readiness and captured calls: http://127.0.0.1:{port}/__fixture__/diagnostics", flush=True)
                    print(f"Loaded {diagnostics['loadedTicketCount']} fixture tickets into temporary SQLite stores.", flush=True)
                    print("Provider clients are replaced, credentials are removed, and non-loopback network access is blocked.", flush=True)
                    if removed_env:
                        print(f"Removed {len(removed_env)} credential or provider environment entries from this process.", flush=True)
                    print("Press Ctrl-C to stop the service and remove its temporary stores.", flush=True)
                    while thread.is_alive():
                        thread.join(timeout=0.5)
                    return 0
                except KeyboardInterrupt:
                    server.should_exit = True
                    return 0
                finally:
                    server.should_exit = True
                    if thread.ident is not None:
                        thread.join(timeout=35)
                        if thread.is_alive():
                            server.force_exit = True
                            thread.join(timeout=2)
                        if thread.is_alive():
                            raise RuntimeError("Fixture server did not stop; temporary stores were retained until process exit.")
                        with live_api.WORKER_LOCK:
                            worker = live_api.WORKER
                        if worker and worker.thread and worker.thread.is_alive():
                            live_api.stop_worker(worker)
                        if worker and worker.thread and worker.thread.is_alive():
                            raise RuntimeError("Fixture sync worker did not stop; temporary stores were retained until process exit.")
                        with live_api.WORKER_LOCK:
                            if live_api.WORKER is worker:
                                live_api.WORKER = None
            finally:
                listener.close()
    finally:
        egress_guard.restore()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1],
                        help="Repository root. Defaults to the checkout containing this script.")
    parser.add_argument("--fixtures", type=Path, default=None,
                        help="Synthetic data JSON. Defaults to testing/inbox_fixtures/long_email.json.")
    parser.add_argument("--port", type=int, default=8878,
                        help="Loopback port for both the actual Inbox API and its assets.")
    parser.add_argument("--case", default="long-email",
                        help="Fixture ticket to open first. Default: long-email.")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("--port must be zero (ephemeral) or between 1 and 65535")
    return args


if __name__ == "__main__":
    try:
        raise SystemExit(serve(parse_args()))
    except (FileNotFoundError, TimeoutError, ValueError, RuntimeError) as error:
        raise SystemExit(f"Inbox fixture stack failed: {error}") from error
