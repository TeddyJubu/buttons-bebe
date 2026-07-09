"""Shared pytest fixtures for the Fable test suite.

Wiring strategy (see tests/README.md for the full write-up):

* The Fable server package (``fable/server/app``) is put on ``sys.path`` so tests
  can ``import main`` / ``from app import ...`` exactly as the server does.
* The three emulators are imported *in-process* from their ``app.py`` files under
  unique module names (they are all called ``app.py`` so importlib is used to
  avoid a name clash with the server's ``app`` package).
* The server talks to the emulators over plain ``httpx.get`` / ``httpx.post``
  calls to ``http://127.0.0.1:96xx``. We monkeypatch those two module-level
  functions to a router that dispatches by port to the matching emulator's
  Starlette ``TestClient`` — so **no real sockets** are opened for the
  integration layer and there is nothing to tear down.
* SQLite lives on a per-test tempfile under ``/tmp`` (local disk — WAL needs it).
* For determinism the pipeline is driven by calling ``pipeline._run_once`` /
  ``env.run_pipeline()`` directly rather than sleeping on the worker thread; a
  couple of tests exercise the real thread explicitly.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import sqlite3
import sys
import tempfile

import pytest

# --- path anchors -----------------------------------------------------------
TESTS_DIR = pathlib.Path(__file__).resolve().parent
FABLE_DIR = TESTS_DIR.parent
REPO_ROOT = FABLE_DIR.parent
SERVER_DIR = FABLE_DIR / "server"
EMU_DIR = FABLE_DIR / "emulators"

# Make `import main` and `from app import ...` resolve to fable/server.
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))


def _load_emulator(mod_name: str, rel_path: str):
    """Import an emulator app.py under a unique module name (avoids the
    collision with the server's own top-level ``app`` package)."""
    path = EMU_DIR / rel_path
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# --- pytest config ----------------------------------------------------------
def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "e2e: live-stack end-to-end tests (boot the real 4 services; need FABLE_E2E=1)",
    )


def pytest_collection_modifyitems(config, items):
    """Skip @pytest.mark.e2e unless FABLE_E2E=1."""
    if os.environ.get("FABLE_E2E") == "1":
        return
    skip_e2e = pytest.mark.skip(reason="live-stack E2E: set FABLE_E2E=1 to run")
    for item in items:
        if "e2e" in item.keywords:
            item.add_marker(skip_e2e)


# --- server module handles (unit tests use these directly) ------------------
@pytest.fixture(scope="session")
def server_modules():
    import main  # noqa: F401  (module-level create_app() is harmless, no DB touched)
    from app import (  # noqa: WPS433
        actions,
        audit,
        config,
        context,
        db,
        gorgias_compat,
        intake,
        pipeline,
        risk,
        stats,
        tickets,
    )
    from app import brains

    return {
        "main": main, "actions": actions, "audit": audit, "config": config,
        "context": context, "db": db, "gorgias_compat": gorgias_compat,
        "intake": intake, "pipeline": pipeline, "risk": risk, "stats": stats,
        "tickets": tickets, "brains": brains,
    }


@pytest.fixture(scope="session")
def emulator_modules():
    return {
        "shopify": _load_emulator("fable_emu_shopify", "shopify/app.py"),
        "redo": _load_emulator("fable_emu_redo", "redo/app.py"),
        "mailbox": _load_emulator("fable_emu_mailbox", "mailbox/app.py"),
    }


# --- a bare in-memory DB with the real schema (fast unit fixture) -----------
@pytest.fixture
def raw_db(server_modules):
    db = server_modules["db"]
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(db.SCHEMA)
    yield conn
    conn.close()


# --- the full integration environment --------------------------------------
class _Env:
    """Namespace handed to integration tests."""


@pytest.fixture
def env(server_modules, emulator_modules):
    from fastapi.testclient import TestClient
    import httpx

    main = server_modules["main"]
    db = server_modules["db"]
    config = server_modules["config"]
    pipeline = server_modules["pipeline"]
    context = server_modules["context"]

    # 1. fresh SQLite tempfile on local disk (/tmp) — WAL requires local fs.
    fd, dbpath = tempfile.mkstemp(prefix="fable_test_", suffix=".db")
    os.close(fd)
    old_db = os.environ.get("FABLE_DB")
    os.environ["FABLE_DB"] = dbpath

    # 2. in-process emulator clients + reset state for isolation.
    shop = TestClient(emulator_modules["shopify"].app)
    redo = TestClient(emulator_modules["redo"].app)
    mail = TestClient(emulator_modules["mailbox"].app)
    shop.post("/emulator/reset")
    redo.post("/emulator/reset")
    mail.post("/emulator/reset")
    context._invalidate_token()

    clients = {9601: shop, 9602: redo, 9603: mail}
    down: set[int] = set()

    # 3. route the server's outbound httpx to the in-process emulators.
    orig_get, orig_post = httpx.get, httpx.post

    def _route(method, url, **kw):
        u = httpx.URL(url)
        port = u.port
        if port in down or port not in clients:
            raise httpx.ConnectError(f"connection refused (emulator :{port} down)")
        client = clients[port]
        return client.request(
            method, str(u),
            params=kw.get("params"), headers=kw.get("headers"),
            json=kw.get("json"), content=kw.get("content"),
        )

    httpx.get = lambda url, **kw: _route("GET", url, **kw)
    httpx.post = lambda url, **kw: _route("POST", url, **kw)

    # 4. build the server app (no context-manager → startup/pipeline-thread
    #    do NOT auto-run; we init the DB and drive the pipeline ourselves).
    db.init_db()
    app = main.create_app()
    client = TestClient(app)
    conn = db.connect()

    e = _Env()
    e.client = client
    e.conn = conn
    e.shopify, e.redo, e.mailbox = shop, redo, mail
    e.down = down
    e.db_path = dbpath
    e.main = main
    e.db = db
    e.config = config
    e.pipeline = pipeline
    e.context = context
    e.actions = server_modules["actions"]
    e.audit = server_modules["audit"]
    e.tickets = server_modules["tickets"]

    def kill(port: int):
        down.add(port)

    def revive(port: int):
        down.discard(port)

    def run_pipeline() -> int:
        n = 0
        while pipeline._run_once(conn):
            n += 1
        return n

    e.kill = kill
    e.revive = revive
    e.run_pipeline = run_pipeline

    # convenience intake helpers (return the parsed json body) ---------------
    def intake_email(from_email, body_text, from_name=None, subject=None, **extra):
        body = {"from_email": from_email, "body_text": body_text}
        if from_name is not None:
            body["from_name"] = from_name
        if subject is not None:
            body["subject"] = subject
        body.update(extra)
        r = client.post("/fable/api/intake/email", json=body)
        return r

    def intake_chat(session_id, body_text, name=None, email=None):
        body = {"session_id": session_id, "body_text": body_text}
        if name is not None:
            body["name"] = name
        if email is not None:
            body["email"] = email
        return client.post("/fable/api/intake/chat", json=body)

    def intake_whatsapp(phone, body_text, name=None):
        body = {"phone": phone, "body_text": body_text}
        if name is not None:
            body["name"] = name
        return client.post("/fable/api/intake/whatsapp", json=body)

    def ticket(ticket_id):
        return client.get(f"/fable/api/tickets/{ticket_id}").json()["ticket"]

    def draft_for(ticket_id):
        return ticket(ticket_id).get("draft")

    e.intake_email = intake_email
    e.intake_chat = intake_chat
    e.intake_whatsapp = intake_whatsapp
    e.ticket = ticket
    e.draft_for = draft_for

    try:
        yield e
    finally:
        # stop any pipeline thread a test may have started BEFORE unpatching.
        try:
            pipeline.stop()
        except Exception:
            pass
        httpx.get, httpx.post = orig_get, orig_post
        try:
            conn.close()
        except Exception:
            pass
        try:
            client.close()
        except Exception:
            pass
        if old_db is None:
            os.environ.pop("FABLE_DB", None)
        else:
            os.environ["FABLE_DB"] = old_db
        for suffix in ("", "-wal", "-shm", "-journal"):
            try:
                os.unlink(dbpath + suffix)
            except OSError:
                pass
