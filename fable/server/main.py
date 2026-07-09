"""Fable help desk server — FastAPI app (port 9600).

Mounts the native API (/fable/api/*), the Gorgias-compat layer (/api/*), and (if
present) the console static files at /. Starts the AI pipeline worker thread.
"""
from __future__ import annotations

import logging
import os

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app import (
    actions,
    audit,
    config,
    db,
    gorgias_compat as gc,
    intake as intake_mod,
    pipeline,
    stats,
    tickets as tickets_mod,
)
from app.models import (
    ChatIntake,
    EmailIntake,
    GorgiasMessageBody,
    NoteBody,
    PatchTicketBody,
    RewriteBody,
    SendBody,
    WhatsappIntake,
)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("fable.main")


def get_db():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


def create_app() -> FastAPI:
    app = FastAPI(title="Fable Help Desk", version="1.0.0")

    @app.on_event("startup")
    def _startup():
        db.init_db()
        pipeline.start()
        log.info("Fable server ready (brain=%s, db=%s)", config.BRAIN, config.db_path())

    @app.on_event("shutdown")
    def _shutdown():
        pipeline.stop()

    # ---- health ----------------------------------------------------------
    def _health(conn):
        return {
            "ok": True,
            "brain": config.BRAIN,
            "db": config.db_path(),
            "queue_depth": pipeline.queue_depth(conn),
        }

    @app.get("/fable/api/health")
    def health(conn=Depends(get_db)):
        return _health(conn)

    @app.get("/health")
    def health_root(conn=Depends(get_db)):
        return _health(conn)

    # ---- tickets ---------------------------------------------------------
    @app.get("/fable/api/tickets")
    def list_tickets(
        status: str = "all", channel: str = "all", sensitive: str = None,
        q: str = None, limit: int = 50, cursor: str = None, conn=Depends(get_db),
    ):
        sens = True if str(sensitive).lower() == "true" else None
        return tickets_mod.list_tickets(conn, status=status, channel=channel,
                                        sensitive=sens, q=q, limit=limit, cursor=cursor)

    @app.get("/fable/api/tickets/{ticket_id}")
    def get_ticket(ticket_id: int, conn=Depends(get_db)):
        t = tickets_mod.get_full(conn, ticket_id)
        if not t:
            return JSONResponse({"detail": "ticket not found"}, status_code=404)
        return {"ticket": t}

    @app.patch("/fable/api/tickets/{ticket_id}")
    def patch_ticket(ticket_id: int, body: PatchTicketBody, conn=Depends(get_db)):
        t = tickets_mod.patch(conn, ticket_id, body)
        if not t:
            return JSONResponse({"detail": "ticket not found"}, status_code=404)
        return {"ticket": t}

    # ---- actions ---------------------------------------------------------
    @app.post("/fable/api/tickets/{ticket_id}/send")
    def send(ticket_id: int, body: SendBody, conn=Depends(get_db)):
        return actions.send(conn, ticket_id, body.text)

    @app.post("/fable/api/tickets/{ticket_id}/note")
    def note(ticket_id: int, body: NoteBody, conn=Depends(get_db)):
        return actions.note(conn, ticket_id, body.text)

    @app.post("/fable/api/tickets/{ticket_id}/rewrite")
    def rewrite(ticket_id: int, body: RewriteBody, conn=Depends(get_db)):
        return actions.rewrite(conn, ticket_id, body.instruction)

    # ---- intake ----------------------------------------------------------
    @app.post("/fable/api/intake/email", status_code=202)
    def intake_email(body: EmailIntake, conn=Depends(get_db)):
        return intake_mod.intake_email(conn, body)

    @app.post("/fable/api/intake/chat", status_code=202)
    def intake_chat(body: ChatIntake, conn=Depends(get_db)):
        return intake_mod.intake_chat(conn, body)

    @app.post("/fable/api/intake/whatsapp", status_code=202)
    def intake_whatsapp(body: WhatsappIntake, conn=Depends(get_db)):
        return intake_mod.intake_whatsapp(conn, body)

    # ---- chat widget long-poll ------------------------------------------
    @app.get("/fable/api/chat/{session_id}/messages")
    def chat_messages(session_id: str, after: int = 0, conn=Depends(get_db)):
        sess = conn.execute(
            "SELECT ticket_id FROM chat_sessions WHERE session_id=?", (session_id,)
        ).fetchone()
        if not sess or not sess["ticket_id"]:
            return {"messages": []}
        rows = conn.execute(
            "SELECT id, from_agent, body_text, created_at FROM messages "
            "WHERE ticket_id=? AND public=1 AND id>? ORDER BY id ASC",
            (sess["ticket_id"], after),
        ).fetchall()
        return {"messages": [
            {"id": r["id"], "from_agent": bool(r["from_agent"]),
             "body_text": r["body_text"], "created_at": r["created_at"]}
            for r in rows
        ]}

    # ---- customers -------------------------------------------------------
    @app.get("/fable/api/customers/{customer_id}")
    def get_customer(customer_id: int, conn=Depends(get_db)):
        row = conn.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
        if not row:
            return JSONResponse({"detail": "customer not found"}, status_code=404)
        trows = conn.execute(
            "SELECT * FROM tickets WHERE customer_id=? ORDER BY id DESC", (customer_id,)
        ).fetchall()
        return {
            "customer": tickets_mod.customer_full(row),
            "tickets": [tickets_mod.summary(conn, t) for t in trows],
        }

    @app.get("/fable/api/customers")
    def list_customers(email: str = None, q: str = None, limit: int = 50,
                       conn=Depends(get_db)):
        if email:
            rows = conn.execute(
                "SELECT * FROM customers WHERE lower(email)=lower(?) ORDER BY id ASC",
                (email,),
            ).fetchall()
        elif q:
            like = f"%{q}%"
            rows = conn.execute(
                "SELECT * FROM customers WHERE name LIKE ? OR email LIKE ? OR phone LIKE ? "
                "ORDER BY id DESC LIMIT ?",
                (like, like, like, int(limit)),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM customers ORDER BY id DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return {"customers": [tickets_mod.customer_full(r) for r in rows]}

    # ---- stats / audit / macros -----------------------------------------
    @app.get("/fable/api/stats")
    def get_stats(conn=Depends(get_db)):
        return stats.compute(conn)

    @app.get("/fable/api/audit")
    def get_audit(limit: int = 100, conn=Depends(get_db)):
        return {"audit": audit.list_recent(conn, limit)}

    @app.get("/fable/api/macros")
    def get_macros():
        return {"macros": []}

    # ---- Gorgias-compat (/api) — Basic auth accepted-but-ignored ---------
    @app.get("/api/tickets")
    def gc_list_tickets(limit: int = 30, cursor: str = None, conn=Depends(get_db)):
        return gc.list_tickets(conn, limit=limit, cursor=cursor)

    @app.get("/api/tickets/{ticket_id}")
    def gc_get_ticket(ticket_id: int, conn=Depends(get_db)):
        t = gc.get_ticket(conn, ticket_id)
        if not t:
            return JSONResponse({"error": "Not Found"}, status_code=404)
        return t

    @app.get("/api/tickets/{ticket_id}/messages")
    def gc_ticket_messages(ticket_id: int, limit: int = 30, conn=Depends(get_db)):
        return gc.get_ticket_messages(conn, ticket_id, limit=limit)

    @app.post("/api/tickets/{ticket_id}/messages", status_code=201)
    def gc_post_message(ticket_id: int, body: GorgiasMessageBody, conn=Depends(get_db)):
        m = gc.post_message(conn, ticket_id, body)
        if not m:
            return JSONResponse({"error": "Not Found"}, status_code=404)
        return m

    @app.get("/api/customers")
    def gc_customers(email: str = None, conn=Depends(get_db)):
        return gc.search_customers(conn, email or "")

    @app.get("/api/customers/{customer_id}")
    def gc_customer(customer_id: int, conn=Depends(get_db)):
        c = gc.get_customer(conn, customer_id)
        if not c:
            return JSONResponse({"error": "Not Found"}, status_code=404)
        return c

    # ---- console static files (optional) --------------------------------
    if os.path.isdir(config.CONSOLE_DIR):
        app.mount("/", StaticFiles(directory=str(config.CONSOLE_DIR), html=True),
                  name="console")
        log.info("serving console from %s", config.CONSOLE_DIR)
    else:
        @app.get("/")
        def root():
            return {"service": "fable", "console": "not built yet",
                    "api": "/fable/api", "health": "/fable/api/health"}

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info")
