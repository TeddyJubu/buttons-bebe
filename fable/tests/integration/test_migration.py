"""Integration: the Gorgias -> Fable migration importer (Stream R / R2).

Reads the Gorgias emulator in-process (Basic auth, cursor pagination) and imports
tickets / customers / messages into a fresh Fable DB. Verifies dry-run writes
nothing, a real run imports everything with source ids preserved, and re-running
is idempotent (skips, never duplicates) — even when a ticket changed in Gorgias
after the first import. No sockets.
"""
import base64
import importlib.util
import pathlib
import sys
import uuid

import pytest
from fastapi.testclient import TestClient

EMU_PATH = pathlib.Path(__file__).resolve().parents[2] / "emulators" / "gorgias" / "app.py"
GOOD_EMAIL = "agent@buttonsbebe.com"
GOOD_KEY = "test-gorgias-key"


def _load_gorgias():
    name = f"fable_emu_gorgias_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, EMU_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _auth_header():
    token = base64.b64encode(f"{GOOD_EMAIL}:{GOOD_KEY}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture
def gorgias():
    """A freshly-seeded Gorgias emulator and an authenticated in-process client."""
    emu = _load_gorgias()
    client = TestClient(emu.app, headers=_auth_header())
    return emu, client


@pytest.fixture
def migration(server_modules):
    from app import migration as mig
    return mig


def _counts(conn):
    def n(table):
        return conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
    return {"tickets": n("tickets"), "customers": n("customers"), "messages": n("messages")}


def _seed_totals(emu):
    tickets = emu.STATE["tickets"]
    return {
        "tickets": len(tickets),
        "customers": len(emu.STATE["customers"]),
        "messages": sum(len(t["messages"]) for t in tickets),
    }


# --- dry run ----------------------------------------------------------------
def test_dry_run_writes_nothing_but_counts_correctly(env, gorgias, migration):
    emu, client = gorgias
    totals = _seed_totals(emu)

    before = _counts(env.conn)
    report = migration.import_from_gorgias(env.conn, client, dry_run=True)
    after = _counts(env.conn)

    assert report["dry_run"] is True
    assert after == before  # nothing written
    assert before["tickets"] == 0 and before["messages"] == 0
    # counts what WOULD be imported
    assert report["tickets_imported"] == totals["tickets"]
    assert report["messages_imported"] == totals["messages"]
    assert report["customers_created"] == totals["customers"]
    assert report["tickets_skipped"] == 0


# --- real import ------------------------------------------------------------
def test_real_run_imports_everything_with_ids_preserved(env, gorgias, migration):
    emu, client = gorgias
    totals = _seed_totals(emu)

    report = migration.import_from_gorgias(env.conn, client, dry_run=False)
    after = _counts(env.conn)

    assert report["tickets_imported"] == totals["tickets"]
    assert report["messages_imported"] == totals["messages"]
    assert report["customers_created"] == totals["customers"]
    assert after["tickets"] == totals["tickets"]
    assert after["messages"] == totals["messages"]
    assert after["customers"] == totals["customers"]

    # Gorgias ids preserved in external_id ------------------------------------
    ext_tickets = {
        r["external_id"]
        for r in env.conn.execute("SELECT external_id FROM tickets").fetchall()
    }
    assert ext_tickets == {str(t["id"]) for t in emu.STATE["tickets"]}

    ext_customers = {
        r["external_id"]
        for r in env.conn.execute("SELECT external_id FROM customers").fetchall()
    }
    assert ext_customers == {str(c["id"]) for c in emu.STATE["customers"]}

    # every imported message carries its Gorgias id
    null_ext = env.conn.execute(
        "SELECT COUNT(*) c FROM messages WHERE external_id IS NULL"
    ).fetchone()["c"]
    assert null_ext == 0

    # an internal note came across as a non-public internal-note message
    note_count = env.conn.execute(
        "SELECT COUNT(*) c FROM messages WHERE public=0 AND channel='internal-note'"
    ).fetchone()["c"]
    assert note_count >= 3  # seed has several sensitive-ticket notes

    # a closed ticket stayed closed
    closed = env.conn.execute(
        "SELECT status FROM tickets WHERE external_id='6002'"
    ).fetchone()
    assert closed["status"] == "closed"


def test_message_order_is_preserved(env, gorgias, migration):
    emu, client = gorgias
    migration.import_from_gorgias(env.conn, client, dry_run=False)
    tid = env.conn.execute(
        "SELECT id FROM tickets WHERE external_id='6013'"
    ).fetchone()["id"]
    rows = env.conn.execute(
        "SELECT external_id FROM messages WHERE ticket_id=? ORDER BY id ASC", (tid,)
    ).fetchall()
    got = [r["external_id"] for r in rows]
    # ticket 6013 seeded order: incoming, note, agent_reply
    src = [str(m["id"]) for m in sorted(
        next(t for t in emu.STATE["tickets"] if t["id"] == 6013)["messages"],
        key=lambda m: (m["created_datetime"], m["id"]))]
    assert got == src


# --- idempotency ------------------------------------------------------------
def test_rerun_skips_all_and_adds_nothing(env, gorgias, migration):
    emu, client = gorgias
    totals = _seed_totals(emu)

    migration.import_from_gorgias(env.conn, client, dry_run=False)
    first = _counts(env.conn)

    report2 = migration.import_from_gorgias(env.conn, client, dry_run=False)
    second = _counts(env.conn)

    assert report2["tickets_imported"] == 0
    assert report2["tickets_skipped"] == totals["tickets"]
    assert report2["customers_reused"] == totals["customers"]
    assert report2["customers_created"] == 0
    assert report2["messages_imported"] == 0
    assert second == first  # nothing changed on the second run


def test_ticket_updated_after_import_is_not_duplicated(env, gorgias, migration):
    emu, client = gorgias
    migration.import_from_gorgias(env.conn, client, dry_run=False)
    before = _counts(env.conn)

    # simulate the ticket changing in Gorgias AFTER we imported it
    t = next(x for x in emu.STATE["tickets"] if x["id"] == 6001)
    original_subject = t["subject"]
    t["subject"] = "EDITED IN GORGIAS AFTER IMPORT"
    t["messages"].append({
        "id": 999001, "ticket_id": 6001, "public": True, "from_agent": False,
        "channel": "email", "via": "email", "source": None,
        "sender": {"id": 5001, "email": "emma.wilson@example.com", "name": "Emma Wilson"},
        "receiver": None, "subject": None, "body_text": "one more reply",
        "body_html": "<p>one more reply</p>", "stripped_text": "one more reply",
        "attachments": [], "imported": False,
        "created_datetime": "2026-07-11T09:00:00-04:00", "sent_datetime": "2026-07-11T09:00:00-04:00",
    })

    report = migration.import_from_gorgias(env.conn, client, dry_run=False)
    after = _counts(env.conn)

    assert report["tickets_skipped"] >= 1
    assert after == before  # no duplicate ticket, no extra message
    # the already-imported ticket keeps its original (pre-edit) subject
    stored = env.conn.execute(
        "SELECT subject FROM tickets WHERE external_id='6001'"
    ).fetchone()
    assert stored["subject"] == original_subject


def test_missing_external_id_column_is_added_by_init(server_modules):
    """The additive migration adds external_id to a pre-existing DB."""
    import sqlite3
    db = server_modules["db"]
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    # an OLD schema without external_id
    conn.execute(
        "CREATE TABLE tickets (id INTEGER PRIMARY KEY, subject TEXT, status TEXT, "
        "channel TEXT, customer_id INTEGER, created_at TEXT, updated_at TEXT, "
        "last_message_at TEXT)"
    )
    cols_before = {r["name"] for r in conn.execute("PRAGMA table_info(tickets)").fetchall()}
    assert "external_id" not in cols_before
    db._ensure_column(conn, "tickets", "external_id", "TEXT")
    cols_after = {r["name"] for r in conn.execute("PRAGMA table_info(tickets)").fetchall()}
    assert "external_id" in cols_after
    conn.close()
