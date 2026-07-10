"""Gorgias help-desk API emulator (Fable) — port 9604.

A read-only stand-in for the Gorgias REST API, shaped exactly like the real
thing (see fable/docs/RESEARCH-gorgias-api.md) so the migration importer and the
existing read tools can be pointed at it by changing only their base URL.

What it serves (HTTP Basic auth on every /api route — email + API key):
  GET  /api/tickets                 cursor pagination: ?limit=&cursor=  (envelope
                                    with meta.next_cursor / meta.total_resources)
  GET  /api/tickets/{id}            one ticket, messages included inline
  GET  /api/tickets/{id}/messages   the ticket's messages, paginated; internal
                                    notes are included (public=false, channel
                                    "internal-note", no receiver)
  GET  /api/customers               ?email= filter, else the full paginated list
  GET  /api/customers/{id}          one customer

Test controls (no auth):
  POST /emulator/reset              reseed to a known state
  GET  /emulator/state              row counts
  GET  /health                      liveness

Only stdlib + fastapi/uvicorn. Binds 127.0.0.1. Nothing ever leaves localhost.
Wrong credentials return a Gorgias-shaped 401 body.
"""
import base64
import json
import os
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# ---------------------------------------------------------------- config -----
EMAIL = os.environ.get("GORGIAS_EMULATOR_EMAIL", "agent@buttonsbebe.com")
API_KEY = os.environ.get("GORGIAS_EMULATOR_API_KEY", "test-gorgias-key")
SUPPORT_EMAIL = os.environ.get("SUPPORT_EMAIL", "care@buttonsbebe.com")
AGENT_NAME = "Buttons Bebe Care Team"

app = FastAPI(title="Fable Gorgias Emulator")

# Gorgias-shaped error bodies -------------------------------------------------
def _err_401():
    return JSONResponse(
        {"error": {
            "code": 401,
            "name": "Unauthorized",
            "message": "Invalid credentials. Provide a valid account email and "
                       "API key using HTTP Basic authentication.",
            "request_id": uuid.uuid4().hex,
        }},
        status_code=401,
    )


def _err_404():
    return JSONResponse(
        {"error": {"code": 404, "name": "NotFound", "message": "Resource not found."}},
        status_code=404,
    )


# ================================================================ seed =======
def build_seed():
    """Return a fresh (deep, literal) seed dict every call.

    ~10 customers and ~15 Buttons Bebe tickets across email / chat / whatsapp /
    sms; a few are closed, a few are sensitive (refund / damaged / chargeback /
    missing item), and several carry agent replies and internal notes.
    """
    _mid = [7000]  # message-id counter, shared across the builder

    def next_mid():
        _mid[0] += 1
        return _mid[0]

    def incoming(ticket_id, channel, cust, body, when):
        """A customer-originated message (public, from_agent=false)."""
        return {
            "id": next_mid(),
            "ticket_id": ticket_id,
            "public": True,
            "from_agent": False,
            "channel": channel,
            "via": channel,
            "source": {
                "type": channel,
                "from": {"address": cust["email"], "name": cust["name"]},
                "to": [{"address": SUPPORT_EMAIL, "name": AGENT_NAME}],
                "cc": [],
                "bcc": [],
            },
            "sender": {"id": cust["id"], "email": cust["email"], "name": cust["name"]},
            "receiver": None,
            "subject": None,
            "body_text": body,
            "body_html": "<p>" + body.replace("\n", "<br>") + "</p>",
            "stripped_text": body,
            "attachments": [],
            "imported": False,
            "created_datetime": when,
            "sent_datetime": when,
        }

    def agent_reply(ticket_id, channel, cust, body, when):
        """A public agent reply sent to the customer (from_agent=true)."""
        return {
            "id": next_mid(),
            "ticket_id": ticket_id,
            "public": True,
            "from_agent": True,
            "channel": channel,
            "via": channel,
            "source": {
                "type": channel,
                "from": {"address": SUPPORT_EMAIL, "name": AGENT_NAME},
                "to": [{"address": cust["email"], "name": cust["name"]}],
                "cc": [],
                "bcc": [],
            },
            "sender": {"email": SUPPORT_EMAIL, "name": AGENT_NAME},
            "receiver": {"id": cust["id"], "email": cust["email"], "name": cust["name"]},
            "subject": None,
            "body_text": body,
            "body_html": "<p>" + body.replace("\n", "<br>") + "</p>",
            "stripped_text": body,
            "attachments": [],
            "imported": False,
            "created_datetime": when,
            "sent_datetime": when,
        }

    def note(ticket_id, body, when):
        """A staff-only internal note (public=false, no receiver)."""
        return {
            "id": next_mid(),
            "ticket_id": ticket_id,
            "public": False,
            "from_agent": True,
            "channel": "internal-note",
            "via": "internal-note",
            "source": None,
            "sender": {"email": SUPPORT_EMAIL, "name": AGENT_NAME},
            "receiver": None,
            "subject": None,
            "body_text": body,
            "body_html": "<p>" + body.replace("\n", "<br>") + "</p>",
            "stripped_text": body,
            "attachments": [],
            "imported": False,
            "created_datetime": when,
            "sent_datetime": None,
        }

    # -- customers ------------------------------------------------------------
    people = [
        (5001, "Emma", "Wilson", "emma.wilson@example.com"),
        (5002, "Sophie", "Martin", "sophie.martin@example.com"),
        (5003, "Olivia", "Brown", "olivia.brown@example.com"),
        (5004, "Liam", "Johnson", "liam.johnson@example.com"),
        (5005, "Ava", "Davis", "ava.davis@example.com"),
        (5006, "Noah", "Garcia", "noah.garcia@example.com"),
        (5007, "Mia", "Gonzalez", "mia.gonzalez@example.com"),
        (5008, "Amelia", "Hall", "amelia.hall@example.com"),
        (5009, "Harper", "Young", "harper.young@example.com"),
        (5010, "Ethan", "Clark", "ethan.clark@example.com"),
    ]
    customers = {}
    for cid, fn, ln, email in people:
        customers[cid] = {
            "id": cid,
            "email": email,
            "firstname": fn,
            "lastname": ln,
            "name": f"{fn} {ln}",
            "external_id": str(6_100_000_000_000 + cid),   # mock Shopify customer id
            "channels": [{"type": "email", "address": email}],
            "language": "en",
            "timezone": "America/New_York",
            "note": None,
            "meta": {},
            "created_datetime": "2025-11-01T09:00:00-04:00",
            "updated_datetime": "2026-06-01T09:00:00-04:00",
        }

    def C(cid):
        return customers[cid]

    # -- ticket builder -------------------------------------------------------
    tickets = []

    def ticket(tid, cust_id, channel, status, subject, priority, messages,
               created, updated, closed=None, is_unread=False, spam=False):
        cust = C(cust_id)
        last = messages[-1]["created_datetime"] if messages else created
        last_recv = created
        for m in messages:
            if not m["from_agent"]:
                last_recv = m["created_datetime"]
        tickets.append({
            "id": tid,
            "status": status,                     # "open" | "closed"
            "priority": priority,                 # critical|high|normal|low
            "channel": channel,
            "via": "api",
            "from_agent": False,
            "subject": subject,
            "language": "en",
            "summary": None,
            "is_unread": is_unread,
            "spam": spam,
            "external_id": None,
            "customer": {
                "id": cust["id"], "email": cust["email"], "name": cust["name"],
                "firstname": cust["firstname"], "lastname": cust["lastname"],
            },
            "assignee_user": {"id": 200, "email": SUPPORT_EMAIL, "name": AGENT_NAME},
            "assignee_team": None,
            "tags": [],
            "custom_fields": [],
            "satisfaction_survey": None,
            "messages": messages,
            "created_datetime": created,
            "opened_datetime": created,
            "last_received_message_datetime": last_recv,
            "last_message_datetime": last,
            "updated_datetime": updated,
            "closed_datetime": closed,
            "snooze_datetime": None,
        })

    # 6001 — order status (email, open, agent reply + internal note)
    ticket(
        6001, 5001, "email", "open", "Where is my order #BB1015?", "normal",
        [
            incoming(6001, "email", C(5001),
                     "Hi, I placed order #BB1015 last week and haven't seen a "
                     "tracking update. Can you tell me where it is?",
                     "2026-06-15T09:12:00-04:00"),
            agent_reply(6001, "email", C(5001),
                        "Hi Emma! Your order #BB1015 shipped and is on the way — "
                        "tracking should update within 24 hours. Thanks for your "
                        "patience!", "2026-06-15T11:40:00-04:00"),
            note(6001, "Checked Shopify — fulfilled, UPS label created 06/14.",
                 "2026-06-15T11:35:00-04:00"),
        ],
        created="2026-06-15T09:12:00-04:00", updated="2026-06-15T11:40:00-04:00"),

    # 6002 — return for wrong size (email, CLOSED, agent reply)
    ticket(
        6002, 5002, "email", "closed", "Return request — wrong size", "normal",
        [
            incoming(6002, "email", C(5002),
                     "The waffle knit set I received is too small. How do I return "
                     "it for the next size up?", "2026-06-03T14:05:00-04:00"),
            agent_reply(6002, "email", C(5002),
                        "Hi Sophie, no problem! I've started a return for you and "
                        "emailed a prepaid label. Once it's scanned we'll ship the "
                        "larger size.", "2026-06-03T15:20:00-04:00"),
        ],
        created="2026-06-03T14:05:00-04:00", updated="2026-06-04T10:00:00-04:00",
        closed="2026-06-04T10:00:00-04:00"),

    # 6003 — shipping question (chat, open, agent reply)
    ticket(
        6003, 5003, "chat", "open", "Do you ship to Canada?", "low",
        [
            incoming(6003, "chat", C(5003),
                     "Hi! Do you ship to Canada, and how much is shipping?",
                     "2026-06-20T16:30:00-04:00"),
            agent_reply(6003, "chat", C(5003),
                        "We do! Canadian shipping is a flat rate at checkout and "
                        "usually arrives in 6–9 business days.",
                        "2026-06-20T16:34:00-04:00"),
        ],
        created="2026-06-20T16:30:00-04:00", updated="2026-06-20T16:34:00-04:00"),

    # 6004 — damaged / refund (email, open, SENSITIVE, internal note only)
    ticket(
        6004, 5004, "email", "open", "My order arrived damaged — I want a refund",
        "high",
        [
            incoming(6004, "email", C(5004),
                     "My order showed up with a torn seam on the cardigan. This is "
                     "unacceptable, I want a full refund!",
                     "2026-07-01T08:45:00-04:00"),
            note(6004, "Damaged item + refund request → sensitive. Escalated to "
                       "Chaim; hold customer-facing reply for human review.",
                 "2026-07-01T08:52:00-04:00"),
        ],
        created="2026-07-01T08:45:00-04:00", updated="2026-07-01T08:52:00-04:00",
        is_unread=True),

    # 6005 — sizing question (whatsapp, open)
    ticket(
        6005, 5005, "whatsapp", "open", "Is the cardigan true to size?", "low",
        [
            incoming(6005, "whatsapp", C(5005),
                     "Is the chunky knit cardigan true to size for a 6 month old?",
                     "2026-07-02T12:10:00-04:00"),
        ],
        created="2026-07-02T12:10:00-04:00", updated="2026-07-02T12:10:00-04:00",
        is_unread=True),

    # 6006 — address change (email, CLOSED, agent reply)
    ticket(
        6006, 5006, "email", "closed", "Change shipping address on #BB1030",
        "normal",
        [
            incoming(6006, "email", C(5006),
                     "I need to update the shipping address on order #BB1030 before "
                     "it ships.", "2026-06-10T10:00:00-04:00"),
            agent_reply(6006, "email", C(5006),
                        "Done! I've updated the address on #BB1030 — it hadn't "
                        "shipped yet, so you're all set.",
                        "2026-06-10T10:30:00-04:00"),
        ],
        created="2026-06-10T10:00:00-04:00", updated="2026-06-10T10:35:00-04:00",
        closed="2026-06-10T10:35:00-04:00"),

    # 6007 — exchange for larger size (email, open, agent reply + note)
    ticket(
        6007, 5007, "email", "open", "Exchange for a larger size", "normal",
        [
            incoming(6007, "email", C(5007),
                     "Could I exchange the footie set for the 3–6M size instead?",
                     "2026-07-05T09:00:00-04:00"),
            agent_reply(6007, "email", C(5007),
                        "Absolutely, Mia — I can set up an exchange for the 3–6M. "
                        "I'll send a return label shortly.",
                        "2026-07-05T09:25:00-04:00"),
            note(6007, "Confirmed 3–6M in stock before promising exchange.",
                 "2026-07-05T09:20:00-04:00"),
        ],
        created="2026-07-05T09:00:00-04:00", updated="2026-07-05T09:25:00-04:00"),

    # 6008 — discount code (chat, CLOSED, agent reply)
    ticket(
        6008, 5008, "chat", "closed", "Discount code not working", "normal",
        [
            incoming(6008, "chat", C(5008),
                     "My code WELCOME10 won't apply at checkout.",
                     "2026-06-25T13:15:00-04:00"),
            agent_reply(6008, "chat", C(5008),
                        "That code expired, but here's a fresh one: WELCOME15. "
                        "Sorry for the trouble!", "2026-06-25T13:19:00-04:00"),
        ],
        created="2026-06-25T13:15:00-04:00", updated="2026-06-25T13:40:00-04:00",
        closed="2026-06-25T13:40:00-04:00"),

    # 6009 — chargeback / dispute (email, open, SENSITIVE, internal note)
    ticket(
        6009, 5009, "email", "open", "Chargeback filed by mistake", "critical",
        [
            incoming(6009, "email", C(5009),
                     "My bank filed a dispute on the charge but I actually did "
                     "receive my order. How do I fix this?",
                     "2026-07-06T15:00:00-04:00"),
            note(6009, "Chargeback/dispute → sensitive. Do not draft a promise; "
                       "route to finance for the dispute response.",
                 "2026-07-06T15:05:00-04:00"),
        ],
        created="2026-07-06T15:00:00-04:00", updated="2026-07-06T15:05:00-04:00",
        is_unread=True),

    # 6010 — ship date (sms, open)
    ticket(
        6010, 5010, "sms", "open", "When will my order ship?", "normal",
        [
            incoming(6010, "sms", C(5010),
                     "Hey, when is my order going to ship out?",
                     "2026-07-07T11:00:00-04:00"),
        ],
        created="2026-07-07T11:00:00-04:00", updated="2026-07-07T11:00:00-04:00",
        is_unread=True),

    # 6011 — gift wrapping (email, open, agent reply)
    ticket(
        6011, 5001, "email", "open", "Gift wrapping request", "low",
        [
            incoming(6011, "email", C(5001),
                     "Can this order be gift wrapped? It's a baby shower present.",
                     "2026-07-03T10:20:00-04:00"),
            agent_reply(6011, "email", C(5001),
                        "How lovely! Yes, I've added complimentary gift wrapping "
                        "and a gift note to your order.",
                        "2026-07-03T10:45:00-04:00"),
        ],
        created="2026-07-03T10:20:00-04:00", updated="2026-07-03T10:45:00-04:00"),

    # 6012 — care instructions (whatsapp, open)
    ticket(
        6012, 5002, "whatsapp", "open", "Fabric care instructions", "low",
        [
            incoming(6012, "whatsapp", C(5002),
                     "How should I wash the velour tracksuit so it doesn't shrink?",
                     "2026-07-04T18:00:00-04:00"),
        ],
        created="2026-07-04T18:00:00-04:00", updated="2026-07-04T18:00:00-04:00",
        is_unread=True),

    # 6013 — cancellation (email, CLOSED, agent reply + note)
    ticket(
        6013, 5003, "email", "closed", "Cancel my order please", "normal",
        [
            incoming(6013, "email", C(5003),
                     "I ordered the wrong item — can you cancel order #BB1041?",
                     "2026-06-28T08:00:00-04:00"),
            note(6013, "Order not yet fulfilled — safe to cancel and refund.",
                 "2026-06-28T08:10:00-04:00"),
            agent_reply(6013, "email", C(5003),
                        "All done — #BB1041 is cancelled and your refund is on its "
                        "way. You'll see it in 3–5 business days.",
                        "2026-06-28T08:15:00-04:00"),
        ],
        created="2026-06-28T08:00:00-04:00", updated="2026-06-28T08:20:00-04:00",
        closed="2026-06-28T08:20:00-04:00"),

    # 6014 — missing item / never arrived (email, open, SENSITIVE, note)
    ticket(
        6014, 5004, "email", "open", "Missing item — package never arrived",
        "high",
        [
            incoming(6014, "email", C(5004),
                     "Tracking says delivered but the package never arrived and an "
                     "item is missing. Please help!",
                     "2026-07-08T09:30:00-04:00"),
            note(6014, "Never-arrived + missing item → sensitive. Open carrier "
                       "trace before any resolution is offered.",
                 "2026-07-08T09:36:00-04:00"),
        ],
        created="2026-07-08T09:30:00-04:00", updated="2026-07-08T09:36:00-04:00",
        is_unread=True),

    # 6015 — restock question (chat, open, agent reply)
    ticket(
        6015, 5005, "chat", "open", "Do you restock sold-out items?", "low",
        [
            incoming(6015, "chat", C(5005),
                     "The smocked party dress in 12–18M is sold out — will it come "
                     "back?", "2026-07-09T14:00:00-04:00"),
            agent_reply(6015, "chat", C(5005),
                        "We're expecting a restock in about two weeks — you can add "
                        "your email on the product page for a back-in-stock alert.",
                        "2026-07-09T14:06:00-04:00"),
        ],
        created="2026-07-09T14:00:00-04:00", updated="2026-07-09T14:06:00-04:00"),

    return {"customers": list(customers.values()), "tickets": tickets}


STATE = build_seed()


# ---------------------------------------------------------------- auth -------
def _check_auth(request: Request) -> bool:
    hdr = request.headers.get("Authorization", "")
    if not hdr.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(hdr.split(" ", 1)[1].strip()).decode("utf-8")
    except Exception:
        return False
    if ":" not in decoded:
        return False
    email, _, key = decoded.partition(":")
    return email == EMAIL and key == API_KEY


# ------------------------------------------------------------- pagination ----
def _encode_cursor(last_id: int) -> str:
    raw = json.dumps({"after": last_id}).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(token: str) -> int:
    try:
        pad = "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode((token + pad).encode())
        return int(json.loads(raw).get("after", 0))
    except Exception:
        return 0


def _clamp_limit(raw, default=30, maximum=100):
    try:
        n = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        n = default
    return max(1, min(n, maximum))


def _envelope(data, *, next_cursor=None, prev_cursor=None, total=None):
    return {
        "data": data,
        "object": "list",
        "meta": {
            "next_cursor": next_cursor,
            "prev_cursor": prev_cursor,
            "total_resources": total if total is not None else len(data),
        },
    }


def _paginate(items, cursor, limit):
    """Cursor paginate a list of {id: int, ...} dicts sorted ascending by id."""
    after = _decode_cursor(cursor) if cursor else 0
    window = [it for it in items if it["id"] > after]
    page = window[:limit]
    next_cursor = None
    if len(window) > limit and page:
        next_cursor = _encode_cursor(page[-1]["id"])
    return page, next_cursor


def _ticket_without_messages(t):
    """List views omit the (potentially large) inline message array."""
    out = {k: v for k, v in t.items() if k != "messages"}
    out["messages"] = None
    return out


# ============================================================= endpoints =====
@app.get("/api/tickets")
async def list_tickets(request: Request):
    if not _check_auth(request):
        return _err_401()
    q = request.query_params
    limit = _clamp_limit(q.get("limit"))
    tickets = sorted(STATE["tickets"], key=lambda t: t["id"])

    # optional real-Gorgias filters
    if q.get("customer_id"):
        try:
            cid = int(q["customer_id"])
            tickets = [t for t in tickets if t["customer"]["id"] == cid]
        except ValueError:
            pass
    if q.get("external_id"):
        tickets = [t for t in tickets if t.get("external_id") == q["external_id"]]

    page, next_cursor = _paginate(tickets, q.get("cursor"), limit)
    data = [_ticket_without_messages(t) for t in page]
    return JSONResponse(_envelope(data, next_cursor=next_cursor, total=len(tickets)))


@app.get("/api/tickets/{tid}")
async def get_ticket(tid: str, request: Request):
    if not _check_auth(request):
        return _err_401()
    try:
        tid_i = int(tid)
    except ValueError:
        return _err_404()
    t = next((x for x in STATE["tickets"] if x["id"] == tid_i), None)
    if not t:
        return _err_404()
    return JSONResponse(t)


@app.get("/api/tickets/{tid}/messages")
async def get_ticket_messages(tid: str, request: Request):
    if not _check_auth(request):
        return _err_401()
    try:
        tid_i = int(tid)
    except ValueError:
        return _err_404()
    t = next((x for x in STATE["tickets"] if x["id"] == tid_i), None)
    if not t:
        return _err_404()
    q = request.query_params
    limit = _clamp_limit(q.get("limit"), default=30, maximum=100)
    msgs = sorted(t["messages"], key=lambda m: m["id"])
    page, next_cursor = _paginate(msgs, q.get("cursor"), limit)
    return JSONResponse(_envelope(page, next_cursor=next_cursor, total=len(msgs)))


@app.get("/api/customers")
async def list_customers(request: Request):
    if not _check_auth(request):
        return _err_401()
    q = request.query_params
    customers = sorted(STATE["customers"], key=lambda c: c["id"])
    email = q.get("email")
    if email:
        matched = [c for c in customers if c["email"].lower() == email.lower()]
        return JSONResponse(_envelope(matched, total=len(matched)))
    ext = q.get("external_id")
    if ext:
        matched = [c for c in customers if c.get("external_id") == ext]
        return JSONResponse(_envelope(matched, total=len(matched)))
    limit = _clamp_limit(q.get("limit"))
    page, next_cursor = _paginate(customers, q.get("cursor"), limit)
    return JSONResponse(_envelope(page, next_cursor=next_cursor, total=len(customers)))


@app.get("/api/customers/{cid}")
async def get_customer(cid: str, request: Request):
    if not _check_auth(request):
        return _err_401()
    try:
        cid_i = int(cid)
    except ValueError:
        return _err_404()
    c = next((x for x in STATE["customers"] if x["id"] == cid_i), None)
    if not c:
        return _err_404()
    return JSONResponse(c)


# ============================================================ emulator ctl ===
@app.post("/emulator/reset")
async def reset():
    global STATE
    STATE = build_seed()
    return {"ok": True, "tickets": len(STATE["tickets"]),
            "customers": len(STATE["customers"])}


@app.get("/emulator/state")
async def state():
    n_msgs = sum(len(t["messages"]) for t in STATE["tickets"])
    return {"tickets": len(STATE["tickets"]), "customers": len(STATE["customers"]),
            "messages": n_msgs}


@app.get("/health")
async def health():
    return {"ok": True, "service": "gorgias", "tickets": len(STATE["tickets"]),
            "customers": len(STATE["customers"])}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=9604, log_level="warning")
