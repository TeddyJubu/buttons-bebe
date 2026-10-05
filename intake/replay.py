"""Explicit local-file inbound simulation. No receiver, polling or provider I/O."""
from collections import Counter
from datetime import datetime
import json

from .imports import MAX_BYTES, account_name
from .mail import Context, metadata
from .policy import Conflict, Invalid
from .records import canonical, digest, email, external_id, now, text, uid


def prepare(payload):
    if not isinstance(payload, (bytes, bytearray)) or len(payload) > MAX_BYTES:
        raise Invalid("Choose an offline replay JSON file no larger than 20 MiB.")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Repeated field")
            result[key] = value
        return result
    def reject_constant(value):
        raise ValueError("Non-finite JSON")
    try:
        batch = json.loads(payload.decode("utf-8-sig"), object_pairs_hook=unique, parse_constant=reject_constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise Invalid("Invalid offline replay JSON.") from exc
    if not isinstance(batch, dict) or batch.get("mode") != "offline_replay":
        raise Invalid("Replay requires the explicit offline_replay mode marker.")
    account = account_name(batch.get("account"))
    mailbox = email(batch.get("mailbox")).casefold()
    if not mailbox:
        raise Invalid("Replay requires a mailbox address.")
    events = batch.get("events")
    if not isinstance(events, list) or not 1 <= len(events) <= 500:
        raise Invalid("Replay between 1 and 500 events per file.")
    normalized = []
    for event in events:
        if not isinstance(event, dict):
            raise Invalid("Replay events must be objects.")
        event_id = external_id(event.get("event_id"), "event_id")
        provider = account_name(event.get("provider"))
        subject = text(event.get("subject"), "subject", 2000, True)
        spam = event.get("spam", False)
        if type(spam) is not bool:
            raise Invalid("spam must be a boolean.")
        message = metadata(event.get("message"))
        if message["channel"] != "email":
            raise Invalid("This replay milestone supports email messages only.")
        if message["kind"] == "incoming" and mailbox not in message["recipients"]:
            raise Invalid("Incoming replay recipients must include the declared mailbox.")
        normalized.append({"id": event_id, "provider": provider, "subject": subject,
                           "spam": spam, "message": message, "raw": event})
    return account, mailbox, normalized, digest(batch)


def instant(value):
    return datetime.fromisoformat(value)


def create_ticket(db, account, mailbox, event, queue, reason):
    message = event["message"]
    tid, cid = uid(), uid()
    db.execute("INSERT INTO contacts VALUES (?,?,?,?,?)",
               (cid, canonical(["replay", account, mailbox, event["provider"], event["id"]]),
                message["author_name"], message["sender"], "{}"))
    db.execute("""INSERT INTO tickets(id,subject,contact_id,status,priority,assignee,channel,origin,
                  created_at,updated_at,queue,review_reason,status_changed_at)
                  VALUES (?,?,?,'open','normal','','email','offline_replay',?,?,?,?,?)""",
               (tid, event["subject"], cid, message["created_at"], message["created_at"],
                queue, reason if queue != "inbox" else "", message["created_at"]))
    return tid


def receive(db, store, context, account, mailbox, event):
    message = event["message"]
    if message["kind"] != "incoming" or message["sender"] == mailbox:
        return {"outcome": "ignored", "reason": "agent_echo_or_internal_note", "reopened": False}
    duplicate = context.duplicate(account, mailbox, event["provider"], message)
    if duplicate:
        db.execute("INSERT OR IGNORE INTO replay_aliases VALUES (?,?,?,?,?,?)",
                   (account, mailbox, event["provider"], message["external_id"], duplicate["message_id"], message["signature"]))
        context.by_external[(account, mailbox, event["provider"], message["external_id"])].append(duplicate)
        return {"outcome": "duplicate_message", "ticket_id": duplicate["ticket_id"],
                "message_id": duplicate["message_id"], "reopened": False}
    tid, reason = context.match(account, mailbox, message)
    queue = "inbox" if reason in ("header_match", "new_conversation") else "review"
    if event["spam"]:
        tid, queue, reason = None, "spam", "spam_flag"
    elif message["automatic"] and not tid:
        queue, reason = "automatic", "automatic_response:" + reason
    created, reopened = tid is None, False
    if created:
        tid = create_ticket(db, account, mailbox, event, queue, reason)
    else:
        ticket = db.execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone()
        # All stored times are canonical UTC ISO. Removing Z sorts whole-second
        # and fractional values correctly without SQLite's millisecond rounding.
        latest = db.execute("SELECT created_at FROM messages WHERE ticket_id=? ORDER BY rtrim(created_at,'Z') DESC LIMIT 1",
                            (tid,)).fetchone()
        watermark = max(instant(ticket["status_changed_at"]), instant(latest[0]) if latest else instant(ticket["created_at"]))
        reopened = (ticket["queue"] == "inbox" and ticket["status"] != "open" and
                    not message["automatic"] and instant(message["created_at"]) > watermark)
        updated = max((ticket["updated_at"], message["created_at"]), key=instant)
        db.execute("""UPDATE tickets SET revision=revision+1,updated_at=?,status=?,status_changed_at=? WHERE id=?""",
                   (updated, "open" if reopened else ticket["status"],
                    message["created_at"] if reopened else ticket["status_changed_at"], tid))
        queue = ticket["queue"]
    mid = uid()
    db.execute("""INSERT INTO messages(id,ticket_id,kind,author_name,author_email,body,created_at,channel,headers_json,origin)
                  VALUES (?,?,'incoming',?,?,?,?,'email',?,'offline_replay')""",
               (mid, tid, message["author_name"], message["sender"], message["body"],
                message["created_at"], canonical(message["headers"])))
    for attachment in message["attachments"]:
        db.execute("INSERT INTO attachments(id,message_id,metadata_json) VALUES (?,?,?)", (uid(), mid, canonical(attachment)))
    db.execute("INSERT INTO replay_messages VALUES (?,?,?,?,?,?)",
               (mid, account, mailbox, event["provider"], message["external_id"], canonical(message["raw"])))
    result = {"outcome": "created" if created else "appended", "ticket_id": tid, "message_id": mid,
              "queue": queue, "reason": reason, "automatic": message["automatic"], "reopened": reopened}
    store.event(db, tid, "inbound_simulated", result, "Offline replay")
    context.add(account, mailbox, event["provider"], mid, tid, message["raw"])
    return result


def run(store, payload, expected_digest=None, *, preview=False):
    account, mailbox, events, fingerprint = prepare(payload)
    if not preview and expected_digest != fingerprint:
        raise Conflict("Replay file changed or preview digest missing. Preview it again.")
    results = []
    with store.connection(write=True) as db:
        context = Context(db)
        for event in events:
            key = (account, mailbox, event["id"])
            old = db.execute("SELECT * FROM replay_receipts WHERE account=? AND mailbox=? AND event_id=?", key).fetchone()
            if old:
                if old["digest"] != digest(event["raw"]):
                    raise Conflict("A replay event ID was reused with changed content. Nothing was replayed.")
                result = {**json.loads(old["result_json"]), "outcome": "duplicate_event", "reopened": False}
            else:
                result = receive(db, store, context, account, mailbox, event)
                db.execute("INSERT INTO replay_receipts VALUES (?,?,?,?,?,?,?)",
                           (*key, digest(event["raw"]), canonical(result), canonical(event["raw"]), now()))
            results.append(result)
        if preview:
            db.rollback()
    return {"mode": "offline_replay", "phase": "preview" if preview else "committed", "digest": fingerprint,
            "events": len(events), "outcomes": dict(Counter(r["outcome"] for r in results)),
            "queues": dict(Counter(r["queue"] for r in results if r["outcome"] in ("created", "appended"))),
            "reopened": sum(r["reopened"] for r in results), "outboundActions": 0, "results": results}
