"""Unit tests for the Gorgias-compat field mappers (TESTING-STRATEGY §2.1).

Uses a bare in-memory DB with the real schema (`raw_db` fixture) so the mappers
run without any network or the full app.
"""
import pytest


@pytest.fixture
def gc(server_modules):
    return server_modules["gorgias_compat"]


def _seed(conn):
    conn.execute(
        "INSERT INTO customers (id, email, name, firstname, lastname, phone, created_at) "
        "VALUES (1, 'emma.wilson@example.com', 'Emma Wilson', 'Emma', 'Wilson', NULL, '2026-07-10T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO tickets (id, subject, status, channel, sensitive, sensitive_reason, "
        "customer_id, is_unread, created_at, updated_at, last_message_at) "
        "VALUES (1, 'Where is my order?', 'open', 'email', 0, NULL, 1, 1, "
        "'2026-07-10T00:00:00Z', '2026-07-10T00:05:00Z', '2026-07-10T00:05:00Z')"
    )
    # a customer (inbound) message and an agent internal note
    conn.execute(
        "INSERT INTO messages (id, ticket_id, from_agent, public, channel, body_text, "
        "sender_name, via, created_at) VALUES "
        "(1, 1, 0, 1, 'email', 'Where is my order #BB1015?', 'Emma Wilson', 'customer', '2026-07-10T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO messages (id, ticket_id, from_agent, public, channel, body_text, "
        "sender_name, via, created_at) VALUES "
        "(2, 1, 1, 0, 'internal-note', 'looked it up, shipped', 'Care Team', 'api', '2026-07-10T00:05:00Z')"
    )
    conn.commit()


# --- envelope ---------------------------------------------------------------
def test_envelope_shape(gc):
    env = gc.envelope([1, 2, 3], next_cursor=9, prev_cursor=None)
    assert env["object"] == "list"
    assert env["data"] == [1, 2, 3]
    assert env["meta"]["next_cursor"] == 9
    assert env["meta"]["prev_cursor"] is None
    assert env["meta"]["total_resources"] == 3


def test_envelope_total_override(gc):
    env = gc.envelope([1], total=57)
    assert env["meta"]["total_resources"] == 57


# --- message mapper ---------------------------------------------------------
def test_inbound_message_maps_to_gorgias_shape(gc, raw_db):
    _seed(raw_db)
    m = raw_db.execute("SELECT * FROM messages WHERE id=1").fetchone()
    obj = gc.message_obj(raw_db, m)
    assert obj["from_agent"] is False
    assert obj["public"] is True
    assert obj["channel"] == "email"
    assert obj["via"] == "customer"
    assert obj["body_text"] == "Where is my order #BB1015?"
    assert obj["created_datetime"] == "2026-07-10T00:00:00Z"
    # inbound: sender is the customer, no sent_datetime
    assert obj["sender"]["email"] == "emma.wilson@example.com"
    assert obj["sent_datetime"] is None
    assert "<br>" in obj["body_html"] or "<p>" in obj["body_html"]


def test_internal_note_message_is_not_public(gc, raw_db):
    _seed(raw_db)
    m = raw_db.execute("SELECT * FROM messages WHERE id=2").fetchone()
    obj = gc.message_obj(raw_db, m)
    assert obj["from_agent"] is True
    assert obj["public"] is False
    assert obj["channel"] == "internal-note"
    # agent message has a sent_datetime; internal note has no public receiver
    assert obj["sent_datetime"] == "2026-07-10T00:05:00Z"
    assert obj["receiver"] is None


# --- ticket mapper ----------------------------------------------------------
def test_ticket_maps_to_gorgias_shape(gc, raw_db):
    _seed(raw_db)
    t = raw_db.execute("SELECT * FROM tickets WHERE id=1").fetchone()
    obj = gc.ticket_obj(raw_db, t, include_messages=True)
    assert obj["id"] == 1
    assert obj["status"] == "open"
    assert obj["channel"] == "email"
    assert obj["via"] == "api"
    assert obj["priority"] == "normal"
    assert obj["created_datetime"] == "2026-07-10T00:00:00Z"
    assert obj["updated_datetime"] == "2026-07-10T00:05:00Z"
    assert obj["last_message_datetime"] == "2026-07-10T00:05:00Z"
    assert obj["customer"]["email"] == "emma.wilson@example.com"
    assert len(obj["messages"]) == 2


def test_sensitive_ticket_is_high_priority(gc, raw_db):
    _seed(raw_db)
    raw_db.execute("UPDATE tickets SET sensitive=1, sensitive_reason='mentions refund' WHERE id=1")
    t = raw_db.execute("SELECT * FROM tickets WHERE id=1").fetchone()
    obj = gc.ticket_obj(raw_db, t)
    assert obj["priority"] == "high"


def test_snoozed_status_presented_as_open(gc, raw_db):
    _seed(raw_db)
    raw_db.execute("UPDATE tickets SET status='snoozed' WHERE id=1")
    t = raw_db.execute("SELECT * FROM tickets WHERE id=1").fetchone()
    obj = gc.ticket_obj(raw_db, t)
    assert obj["status"] == "open"  # Gorgias has no 'snoozed' top-level status
