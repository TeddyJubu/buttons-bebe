"""Conservative offline email identity/thread parsing. No network operations.

RFC 5322 3.6.4 and RFC 3834 5 inform these fields. Ambiguous or unsupported
header syntax goes to review rather than fuzzy subject/customer matching.
"""
from collections import defaultdict
from email.utils import getaddresses
import json
import re

from .imports import parse_message
from .policy import Conflict, Invalid
from .records import digest

ID = re.compile(r"<([^<>\s@]+@[^<>\s@]+)>")
BARE_ID = re.compile(r"[^<>\s@]+@[^<>\s@]+")


def header(headers, name):
    values = [v for k, v in headers.items() if k.lower() == name.lower()]
    if not values:
        return ""
    if len(values) != 1 or not isinstance(values[0], str) or len(values[0]) > 16000:
        raise Invalid("Ambiguous or oversized email header.")
    return values[0].strip()


def ids(value, single=False):
    if not value:
        return []
    if BARE_ID.fullmatch(value):
        result = [value]
    else:
        result = ID.findall(value)
        if ID.sub("", value).strip() or not result:
            raise Invalid("Unsupported message ID header syntax.")
    if len(result) > 100 or (single and len(result) != 1):
        raise Invalid("Ambiguous message ID header.")
    # Preserve case: identifiers are opaque, unlike header field names.
    return list(dict.fromkeys(result))


def addresses(value):
    if isinstance(value, dict):
        value = value.get("address") or value.get("email") or ""
    if isinstance(value, list):
        return set().union(*(addresses(v) for v in value)) if value else set()
    if not isinstance(value, str):
        return set()
    return {address.casefold() for _, address in getaddresses([value])
            if re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", address)}


def parties(raw):
    source = raw.get("source") or {}
    if not isinstance(source, dict):
        source = {}
    sender = addresses(source.get("from")) or addresses(raw.get("sender"))
    recipients = addresses(source.get("to")) or addresses(raw.get("receiver"))
    return sender, recipients


def metadata(raw):
    message = parse_message(raw, "message")
    sender, recipients = parties(raw)
    headers = message["headers"]
    issue, own, refs, automatic = "", [], [], False
    try:
        own = ids(header(headers, "Message-ID"), single=True)
        refs = list(dict.fromkeys(ids(header(headers, "In-Reply-To"), single=True) + ids(header(headers, "References"))))
        auto = header(headers, "Auto-Submitted").split(";", 1)[0].strip().lower()
        automatic = bool(auto and auto != "no")
        content_type = header(headers, "Content-Type").lower()
        automatic = automatic or ("multipart/report" in content_type and "delivery-status" in content_type)
    except Invalid:
        # Retain the message; do not trust any threading fields in this case.
        issue, own, refs = "invalid_headers", [], []
    if len(sender) != 1:
        issue = "ambiguous_sender"
    message.update({"sender": next(iter(sender)) if len(sender) == 1 else "", "recipients": sorted(recipients),
                    "rfc_id": own[0] if own else "", "references": refs, "header_issue": issue,
                    "automatic": automatic})
    message["signature"] = digest({"sender": sorted(sender), "recipients": sorted(recipients),
                                    "body": message["body"], "kind": message["kind"],
                                    "attachments": message["attachments"], "references": refs,
                                    "automatic": automatic, "header_issue": issue, "rfc_id": message["rfc_id"]})
    return message


class Context:
    """Per-transaction index of saved messages only; subject is never an index."""
    def __init__(self, db):
        self.by_rfc = defaultdict(list)
        self.by_external = defaultdict(list)
        rows = db.execute("""SELECT s.account,s.external_id,s.payload_json,m.id,m.ticket_id
                          FROM source_records s JOIN messages m ON m.id=s.internal_id
                          WHERE s.kind='message' AND m.channel='email'""").fetchall()
        for row in rows:
            raw = json.loads(row["payload_json"])
            sender, recipients = parties(raw)
            mailboxes = sender if raw.get("from_agent") else recipients
            for mailbox in mailboxes:
                self.add(row["account"], mailbox, "gorgias", row["id"], row["ticket_id"], raw)
        rows = db.execute("""SELECT r.*,m.ticket_id FROM replay_messages r
                          JOIN messages m ON m.id=r.message_id""").fetchall()
        for row in rows:
            self.add(row["account"], row["mailbox"], row["provider"], row["message_id"],
                     row["ticket_id"], json.loads(row["payload_json"]))
        for row in db.execute('SELECT s.*,m.ticket_id FROM simulated_outgoing s JOIN messages m ON m.id=s.message_id'):
            self.add(row['account'], row['mailbox'], 'fake-delivery', row['message_id'],
                     row['ticket_id'], json.loads(row['payload_json']))
        for row in db.execute("SELECT r.*,m.ticket_id FROM replay_aliases r JOIN messages m ON m.id=r.message_id"):
            self.by_external[(row["account"], row["mailbox"], row["provider"], row["external_id"])].append(dict(row))

    def add(self, account, mailbox, provider, mid, tid, raw):
        message = metadata(raw)
        participants = message["recipients"] if raw["from_agent"] else [message["sender"]]
        entry = {"message_id": mid, "ticket_id": tid, "signature": message["signature"],
                 "participants": participants, "kind": message["kind"]}
        self.by_external[(account, mailbox, provider, message["external_id"])].append(entry)
        if message["rfc_id"] and message["kind"] != "note":
            self.by_rfc[(account, mailbox, message["rfc_id"])].append(entry)

    def duplicate(self, account, mailbox, provider, message):
        rows = self.by_external.get((account, mailbox, provider, message["external_id"]), [])
        if message["rfc_id"]:
            rows = rows + self.by_rfc.get((account, mailbox, message["rfc_id"]), [])
        unique = {row["message_id"]: row for row in rows}
        if not unique:
            return None
        if any(row["signature"] != message["signature"] for row in unique.values()) or len(unique) != 1:
            raise Conflict("A saved message ID has different content or multiple owners. Nothing was replayed.")
        return next(iter(unique.values()))

    def match(self, account, mailbox, message):
        if message["header_issue"]:
            return None, message["header_issue"]
        if not message["rfc_id"]:
            return None, "missing_message_id"
        if not message["references"]:
            return None, "new_conversation"
        rows = [row for ref in message["references"] for row in self.by_rfc.get((account, mailbox, ref), [])]
        tickets = {row["ticket_id"] for row in rows}
        if len(tickets) > 1:
            return None, "ambiguous_references"
        if not tickets:
            return None, "unknown_reference"
        if not any(message["sender"] in row["participants"] for row in rows):
            return None, "participant_mismatch"
        return next(iter(tickets)), "header_match"
