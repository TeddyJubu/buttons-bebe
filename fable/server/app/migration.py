"""Migrate tickets out of Gorgias and into Fable.

This reads the Gorgias REST API (the emulator in tests, the real account in
production), walks every ticket page-by-page, and copies each ticket, its
customer, and its messages into Fable's SQLite database. The original Gorgias
ids are preserved in an ``external_id`` column so nothing is ever lost and the
import can be run again safely.

Two things make this safe to run more than once:

* ``dry_run`` — count exactly what *would* be imported and write nothing.
* idempotency — a ticket whose ``external_id`` is already in Fable is skipped,
  so re-running never creates duplicates (even if the ticket changed in
  Gorgias in the meantime).

Nothing here ever *sends* a message anywhere — it only reads from Gorgias and
writes rows into the local database.

Command line (run from ``fable/server``)::

    python -m app.migration --base-url https://acme.gorgias.com \\
        --email agent@acme.com --api-key XXXX [--dry-run]

The wrapper script ``fable/scripts/migrate-from-gorgias.sh`` is a friendlier
front door to the same thing.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Callable, Iterator, Optional

from .db import now_iso

_AGENT_NAME = "Buttons Bebe Care Team"
_PAGE = 100  # Gorgias max page size


class MigrationError(RuntimeError):
    """A Gorgias API call returned something we can't proceed with."""


def _noop(*_args, **_kwargs) -> None:  # default progress logger
    pass


# --------------------------------------------------------------- HTTP walk ---
def _walk(client, path: str, params: Optional[dict] = None) -> Iterator[dict]:
    """Yield every record across all cursor-paginated pages of a Gorgias list.

    ``client`` is any object with a ``.get(path, params=...)`` method returning a
    response that exposes ``.status_code`` and ``.json()`` — an ``httpx.Client``
    in production or a Starlette ``TestClient`` (pointed at the emulator) in
    tests, so no real network is required to exercise this.
    """
    page_params = dict(params or {})
    page_params.setdefault("limit", _PAGE)
    while True:
        resp = client.get(path, params=page_params)
        status = getattr(resp, "status_code", None)
        if status != 200:
            body = getattr(resp, "text", "")
            raise MigrationError(f"GET {path} returned {status}: {body[:200]}")
        payload = resp.json()
        for item in payload.get("data", []) or []:
            yield item
        meta = payload.get("meta") or {}
        next_cursor = meta.get("next_cursor")
        if not next_cursor:
            break
        page_params["cursor"] = next_cursor


def _fetch_messages(client, ticket_id) -> list:
    return list(_walk(client, f"/api/tickets/{ticket_id}/messages", {"limit": _PAGE}))


# ------------------------------------------------------------- DB helpers ----
def _lookup_customer_id(conn: sqlite3.Connection, ext_id, email) -> Optional[int]:
    if ext_id:
        row = conn.execute(
            "SELECT id FROM customers WHERE external_id=?", (str(ext_id),)
        ).fetchone()
        if row:
            return row["id"]
    if email:
        row = conn.execute(
            "SELECT id FROM customers WHERE lower(email)=lower(?) ORDER BY id ASC LIMIT 1",
            (email,),
        ).fetchone()
        if row:
            return row["id"]
    return None


def _phone_from_channels(gc: dict) -> Optional[str]:
    for ch in gc.get("channels") or []:
        if ch.get("type") in ("phone", "sms", "whatsapp"):
            return ch.get("address")
    return None


def _insert_customer(conn: sqlite3.Connection, gc: dict) -> int:
    ext = str(gc["id"]) if gc.get("id") is not None else None
    cur = conn.execute(
        "INSERT INTO customers (email, name, firstname, lastname, phone, external_id, "
        "created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (gc.get("email"), gc.get("name"), gc.get("firstname"), gc.get("lastname"),
         _phone_from_channels(gc), ext, gc.get("created_datetime") or now_iso()),
    )
    return cur.lastrowid


def _resolve_customer_id(conn: sqlite3.Connection, brief: Optional[dict]) -> int:
    """Find the Fable customer for a ticket's embedded customer, else create one."""
    brief = brief or {}
    ext = brief.get("id")
    fid = _lookup_customer_id(conn, ext, brief.get("email"))
    if fid is not None:
        return fid
    cur = conn.execute(
        "INSERT INTO customers (email, name, firstname, lastname, external_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (brief.get("email"), brief.get("name"), brief.get("firstname"),
         brief.get("lastname"), str(ext) if ext is not None else None, now_iso()),
    )
    return cur.lastrowid


def _ticket_exists(conn: sqlite3.Connection, ext_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM tickets WHERE external_id=? LIMIT 1", (ext_id,)
    ).fetchone() is not None


def _insert_ticket(conn: sqlite3.Connection, gt: dict, customer_id: int, ext_id: str) -> int:
    status = "closed" if gt.get("status") == "closed" else "open"
    channel = gt.get("channel") or "email"
    created = gt.get("created_datetime") or now_iso()
    updated = gt.get("updated_datetime") or created
    last_msg = gt.get("last_message_datetime") or created
    cur = conn.execute(
        "INSERT INTO tickets (subject, status, channel, sensitive, customer_id, "
        "is_unread, external_id, created_at, updated_at, last_message_at) "
        "VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?, ?)",
        (gt.get("subject"), status, channel, customer_id,
         1 if gt.get("is_unread") else 0, ext_id, created, updated, last_msg),
    )
    return cur.lastrowid


def _insert_message(conn: sqlite3.Connection, ticket_id: int, m: dict) -> None:
    public = bool(m.get("public"))
    from_agent = bool(m.get("from_agent"))
    channel = m.get("channel") or "email"
    if not public:
        channel = "internal-note"
    via = "console" if from_agent else "customer"
    sender = m.get("sender") or {}
    sender_name = sender.get("name") or (_AGENT_NAME if from_agent else None)
    ext = str(m["id"]) if m.get("id") is not None else None
    conn.execute(
        "INSERT INTO messages (ticket_id, from_agent, public, channel, body_text, "
        "sender_name, via, external_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ticket_id, 1 if from_agent else 0, 1 if public else 0, channel,
         m.get("body_text") or "", sender_name, via, ext,
         m.get("created_datetime") or now_iso()),
    )


# ------------------------------------------------------------- the import ----
def new_report(dry_run: bool) -> dict:
    return {
        "dry_run": dry_run,
        "customers_created": 0,
        "customers_reused": 0,
        "tickets_imported": 0,
        "tickets_skipped": 0,
        "messages_imported": 0,
    }


def import_from_gorgias(conn: sqlite3.Connection, client, *, dry_run: bool = False,
                        log: Callable = _noop) -> dict:
    """Copy every Gorgias ticket (and its customer + messages) into Fable.

    Returns a report dict of what happened (or, for ``dry_run``, what *would*
    happen). Writes are committed only when ``dry_run`` is False.
    """
    report = new_report(dry_run)

    # -- phase 1: customers ---------------------------------------------------
    # In dry-run we can't rely on the DB to dedupe, so track keys we've counted.
    seen_new_customers: set = set()
    for gc in _walk(client, "/api/customers", {"limit": _PAGE}):
        existing = _lookup_customer_id(conn, gc.get("id"), gc.get("email"))
        if existing is not None:
            report["customers_reused"] += 1
            continue
        if dry_run:
            key = str(gc.get("id") or gc.get("email"))
            if key not in seen_new_customers:
                seen_new_customers.add(key)
                report["customers_created"] += 1
            continue
        _insert_customer(conn, gc)
        report["customers_created"] += 1
        log(f"customer imported: {gc.get('email')}")

    # -- phase 2: tickets + messages -----------------------------------------
    for gt in _walk(client, "/api/tickets", {"limit": _PAGE}):
        ext_id = str(gt["id"])
        if _ticket_exists(conn, ext_id):
            report["tickets_skipped"] += 1
            continue
        messages = _fetch_messages(client, gt["id"])
        if dry_run:
            report["tickets_imported"] += 1
            report["messages_imported"] += len(messages)
            continue
        customer_id = _resolve_customer_id(conn, gt.get("customer"))
        new_tid = _insert_ticket(conn, gt, customer_id, ext_id)
        for m in sorted(messages, key=lambda x: (x.get("created_datetime") or "", x.get("id") or 0)):
            _insert_message(conn, new_tid, m)
            report["messages_imported"] += 1
        report["tickets_imported"] += 1
        log(f"ticket #{gt['id']} imported ({len(messages)} messages)")

    if not dry_run:
        conn.commit()
    return report


# ------------------------------------------------------------------ CLI ------
def _build_client(base_url: str, email: str, api_key: str):
    import httpx  # local import so the module has no hard httpx dependency to import
    return httpx.Client(
        base_url=base_url.rstrip("/"),
        auth=httpx.BasicAuth(email, api_key),
        timeout=30.0,
        # trust_env=False: never route through an ambient proxy; this talks
        # straight to the configured Gorgias host (or a localhost emulator).
        trust_env=False,
        headers={"User-Agent": "Fable-Migration/1.0"},
    )


def main(argv=None) -> int:
    import argparse

    from . import db

    parser = argparse.ArgumentParser(
        prog="python -m app.migration",
        description="Import tickets from Gorgias into Fable (read-only on Gorgias; "
                    "writes only into the local Fable database).",
    )
    parser.add_argument("--base-url", required=True,
                        help="Gorgias API base, e.g. https://acme.gorgias.com")
    parser.add_argument("--email", required=True,
                        help="Gorgias account email (HTTP Basic auth username)")
    parser.add_argument("--api-key", required=True,
                        help="Gorgias API key (HTTP Basic auth password)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Count what would be imported without writing anything")
    args = parser.parse_args(argv)

    db.init_db()
    conn = db.connect()
    client = _build_client(args.base_url, args.email, args.api_key)
    try:
        report = import_from_gorgias(conn, client, dry_run=args.dry_run, log=print)
    finally:
        try:
            client.close()
        except Exception:
            pass
        conn.close()

    print(json.dumps(report, indent=2))
    if report["dry_run"]:
        print(f"\nDRY RUN — nothing was written. "
              f"{report['tickets_imported']} tickets / "
              f"{report['messages_imported']} messages / "
              f"{report['customers_created']} new customers would be imported.")
    else:
        print(f"\nDone. Imported {report['tickets_imported']} tickets "
              f"({report['messages_imported']} messages, "
              f"{report['customers_created']} new customers); "
              f"skipped {report['tickets_skipped']} already-imported tickets.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
