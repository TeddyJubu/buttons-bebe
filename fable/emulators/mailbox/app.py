"""Mailbox emulator (Fable) — port 9603.

Fake email transport so NOTHING real ever leaves localhost.
  POST /simulate/incoming  -> forwards a fake customer email to Fable's intake API
  POST /send               -> Fable calls this to "send" a customer email; captured in outbox
  GET  /outbox             -> inspect everything that "left" the system
  DELETE /outbox           -> clear the outbox

Only stdlib + fastapi/uvicorn/httpx. Binds 127.0.0.1.
"""
import os
import time
import uuid

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

FABLE_INTAKE = os.environ.get(
    "FABLE_INTAKE_URL", "http://127.0.0.1:9600/fable/api/intake/email"
)

app = FastAPI(title="Fable Mailbox Emulator")

STATE = {"outbox": []}


@app.post("/simulate/incoming")
async def simulate_incoming(request: Request):
    """Pretend a customer sent an email; forward it to Fable's intake endpoint."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    payload = {
        "from_email": body.get("from_email", "customer@example.com"),
        "from_name": body.get("from_name", ""),
        "subject": body.get("subject", "(no subject)"),
        "body_text": body.get("body_text", ""),
        "message_id": body.get("message_id") or f"<{uuid.uuid4().hex}@mailbox.local>",
    }
    try:
        # trust_env=False: forwarding to a localhost service must never be routed
        # through any HTTP(S)/SOCKS proxy configured in the environment.
        async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
            r = await client.post(FABLE_INTAKE, json=payload)
        data = {}
        try:
            data = r.json()
        except Exception:
            data = {}
        if r.status_code >= 400:
            return JSONResponse(
                {"forwarded": False, "error": f"Fable intake returned {r.status_code}",
                 "detail": data},
                status_code=502,
            )
        return JSONResponse({
            "forwarded": True,
            "ticket_id": data.get("ticket_id"),
            "message_id": data.get("message_id"),
            "intake_status": r.status_code,
        })
    except Exception as e:
        # Fable is down / unreachable — do not crash; report cleanly.
        return JSONResponse(
            {"forwarded": False, "error": f"could not reach Fable intake: {e.__class__.__name__}",
             "target": FABLE_INTAKE},
            status_code=502,
        )


@app.post("/send")
async def send(request: Request):
    """Capture an outbound customer email. Never actually sends anything."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    msg = {
        "id": len(STATE["outbox"]) + 1,
        "to": body.get("to"),
        "from": body.get("from", "care@buttonsbebe.com"),
        "subject": body.get("subject", ""),
        "body_text": body.get("body_text", ""),
        "body_html": body.get("body_html"),
        "ticket_id": body.get("ticket_id"),
        "in_reply_to": body.get("in_reply_to"),
        "sent_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    STATE["outbox"].append(msg)
    return JSONResponse({"ok": True, "id": msg["id"], "message": msg}, status_code=201)


@app.get("/outbox")
async def get_outbox():
    return {"outbox": STATE["outbox"], "count": len(STATE["outbox"])}


@app.delete("/outbox")
async def clear_outbox():
    n = len(STATE["outbox"])
    STATE["outbox"].clear()
    return {"ok": True, "cleared": n}


@app.post("/emulator/reset")
async def reset():
    n = len(STATE["outbox"])
    STATE["outbox"].clear()
    return {"ok": True, "cleared": n}


@app.get("/health")
async def health():
    return {"ok": True, "service": "mailbox", "outbox": len(STATE["outbox"])}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=9603, log_level="warning")
