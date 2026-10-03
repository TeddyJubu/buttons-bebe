"""Preview and atomic, idempotent import of historical conversation snapshots."""
import json

from .imports import account_name, parse_export
from .policy import Conflict
from .records import canonical, digest, now, uid


def prepare(payload, account):
    account = account_name(account)
    tickets, warnings = parse_export(payload)
    fingerprint = digest({"account": account, "tickets": tickets})
    return account, tickets, warnings, fingerprint


def plan(db, account, tickets, warnings, fingerprint):
    report = {
        "digest": fingerprint, "account": account, "mode": "offline_sandbox",
        "source": {"tickets": len(tickets), "messages": 0, "notes": 0, "attachments": 0},
        "new": {"tickets": 0, "messages": 0},
        "duplicates": {"tickets": 0, "messages": 0},
        "conflicts": [], "warnings": warnings[:100], "warningCount": len(warnings),
        "outboundActions": 0,
    }
    for index, ticket in enumerate(tickets):
        entries = [("ticket", ticket, f"tickets[{index}]")]
        entries.extend(("message", message, f"tickets[{index}].messages[{mi}]")
                       for mi, message in enumerate(ticket["messages"]))
        old_ticket = db.execute("SELECT internal_id FROM source_records WHERE kind='ticket' AND account=? AND external_id=?",
                                (account, ticket["external_id"])).fetchone()
        for kind, record, label in entries:
            old = db.execute("SELECT * FROM source_records WHERE kind=? AND account=? AND external_id=?",
                             (kind, account, record["external_id"])).fetchone()
            if kind == "message":
                report["source"]["messages"] += 1
                report["source"]["notes"] += record["kind"] == "note"
                report["source"]["attachments"] += len(record["attachments"])
            if old:
                wrong_ticket = kind == "message" and (not old_ticket or old["ticket_id"] != old_ticket["internal_id"])
                if old["digest"] != digest(record["raw"]) or wrong_ticket:
                    report["conflicts"].append(label + ": source ID already exists with different content or parent ticket.")
                else:
                    report["duplicates"][kind + "s"] += 1
            else:
                report["new"][kind + "s"] += 1
    report["canImport"] = not report["conflicts"]
    return report


def preview(store, payload, account):
    account, tickets, warnings, fingerprint = prepare(payload, account)
    with store.connection() as db:
        return plan(db, account, tickets, warnings, fingerprint)


def remember(db, kind, account, record, internal_id, ticket_id):
    db.execute("INSERT INTO source_records VALUES (?,?,?,?,?,?,?)",
               (kind, account, record["external_id"], internal_id, ticket_id,
                digest(record["raw"]), canonical(record["raw"])))


def apply_import(store, payload, account, expected_digest):
    account, tickets, warnings, fingerprint = prepare(payload, account)
    if expected_digest != fingerprint:
        raise Conflict("The export changed. Preview it again before importing.")
    with store.connection(write=True) as db:
        report = plan(db, account, tickets, warnings, fingerprint)
        if not report["canImport"]:
            raise Conflict("Import has conflicting source records. Nothing was written; review the preview.")
        existing = db.execute("SELECT id FROM import_batches WHERE account=? AND digest=?", (account, fingerprint)).fetchone()
        if existing:
            return {**report, "batchId": existing["id"], "alreadyImported": True}
        batch_id = uid()
        for ticket in tickets:
            source = db.execute("SELECT internal_id FROM source_records WHERE kind='ticket' AND account=? AND external_id=?",
                                (account, ticket["external_id"])).fetchone()
            is_new = source is None
            tid = uid() if is_new else source["internal_id"]
            if is_new:
                customer = ticket["customer"]
                # Source customer ID is a link, not proof of ownership of an order.
                key = canonical(["gorgias", account, "customer", str(customer["id"])]) if customer.get("id") else canonical(["gorgias", account, "ticket-contact", ticket["external_id"]])
                contact = db.execute("SELECT id FROM contacts WHERE identity_key=?", (key,)).fetchone()
                cid = contact["id"] if contact else uid()
                if contact is None:
                    db.execute("INSERT INTO contacts VALUES (?,?,?,?,?)",
                               (cid, key, ticket["customer_name"], ticket["customer_email"], canonical(customer)))
                db.execute("""INSERT INTO tickets(id,subject,contact_id,status,priority,assignee,channel,origin,tags_json,created_at,updated_at)
                            VALUES (?,?,?,?,?,?,?,'gorgias_export',?,?,?)""",
                           (tid, ticket["subject"], cid, ticket["status"], ticket["priority"], ticket["assignee"],
                            ticket["channel"], canonical(ticket["tags"]), ticket["created_at"], ticket["updated_at"]))
                remember(db, "ticket", account, ticket, tid, tid)
                db.execute("UPDATE tickets SET status_changed_at=? WHERE id=?", (ticket["updated_at"], tid))
            added = 0
            for message in ticket["messages"]:
                if db.execute("SELECT 1 FROM source_records WHERE kind='message' AND account=? AND external_id=?",
                              (account, message["external_id"])).fetchone():
                    continue
                mid = uid()
                db.execute("""INSERT INTO messages(id,ticket_id,kind,author_name,author_email,body,created_at,channel,headers_json,origin)
                            VALUES (?,?,?,?,?,?,?,?,?,'gorgias_export')""",
                           (mid, tid, message["kind"], message["author_name"], message["author_email"], message["body"],
                            message["created_at"], message["channel"], canonical(message["headers"])))
                remember(db, "message", account, message, mid, tid)
                for metadata in message["attachments"]:
                    db.execute("INSERT INTO attachments(id,message_id,metadata_json) VALUES (?,?,?)", (uid(), mid, canonical(metadata)))
                added += 1
            if not is_new and added:
                # Import never resets an operator's status/assignment edits.
                db.execute("UPDATE tickets SET revision=revision+1 WHERE id=?", (tid,))
            if is_new or added:
                store.event(db, tid, "history_imported", {"batch_id": batch_id, "messages": added}, "Offline import")
        report.update({"batchId": batch_id, "alreadyImported": False})
        db.execute("INSERT INTO import_batches VALUES (?,?,?,?,?)", (batch_id, account, fingerprint, now(), canonical(report)))
        return report
