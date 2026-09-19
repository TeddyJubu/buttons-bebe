"""Email and chat intake tissues. Spam is ours. Not a Shopify object.

#33 spam-decision model: the observed (production) inbox mirrors Gorgias's
spam flag only — it never runs its own rules (export_projection.py maps the
observed webhook flag; there is no local detector). This demo/intake path is
the one place we decide, so the decision is recorded per ticket:
`spamSource: "intake-keywords"` names the marker-rule version that fired.
Flagged messages file a reviewable ticket in the Spam view (#33); they are
never dropped, never joined to Shopify, never notified.
"""

from __future__ import annotations

from typing import Any

from .errors import bad_request
from .fixtures_intake import MAILBOX_ADDRESS, MAILBOX_DISPLAY
from .join import join_shopify
from .speakers import split_from
from . import tickets

_SPAM_MARKERS = (
    "prize",
    "lottery",
    "you won",
    "winner",
    "cash prize",
    "unsubscribe-farm",
    "unsubscribe farm",
    "claim your",
    "congratulations you have won",
)


def parse_from(value: str) -> tuple[str, str | None]:
    name, email = split_from(str(value or ""))
    if not name:
        raise bad_request("from is required", field="from")
    return name, email


def is_spam(subject: str = "", body: str = "") -> bool:
    blob = f"{subject}\n{body}".lower()
    return any(marker in blob for marker in _SPAM_MARKERS)


def _snippet(body: str) -> str:
    line = " ".join(str(body or "").split())
    return line[:140]


def _subject_from_chat(body: str) -> str:
    line = " ".join(str(body or "").split())
    return line[:80] or "Chat"


def _intake_record(
    *,
    channel: str,
    from_name: str,
    from_email: str | None,
    subject: str,
    body: str,
    received_at: str,
    spam: bool,
) -> dict[str, Any]:
    return {
        "channel": channel,
        "fromName": from_name,
        "fromEmail": from_email,
        "subject": subject,
        "body": body,
        "receivedAt": received_at,
        "spam": spam,
        "mailbox": MAILBOX_ADDRESS,
        "mailboxDisplay": MAILBOX_DISPLAY,
        "messageId": None,
    }


def _require(args: dict[str, Any], *keys: str) -> None:
    for key in keys:
        if args.get(key) in (None, ""):
            raise bad_request(f"{key} is required", field=key)


def handle_ingest_email(args: dict[str, Any]) -> dict[str, Any]:
    _require(args, "from", "subject", "body", "receivedAt")
    from_name, from_email = parse_from(str(args["from"]))
    subject = str(args["subject"])
    body = str(args["body"])
    received_at = str(args["receivedAt"])
    message_id = str(args["messageId"]).strip() if args.get("messageId") else None
    source = str(args.get("source") or "agentmail").strip().lower() or "agentmail"
    if source not in {"agentmail", "gorgias"}:
        source = "agentmail"
    # source=gorgias is bridge-webhook only — prevents forged external.ticketId routing.
    if source == "gorgias" and args.get("_fromBridge") is not True:
        raise bad_request("source=gorgias is bridge-only", field="source")
    external = tickets.normalize_external(args.get("external"))
    if external is None and message_id:
        if source == "gorgias":
            external = {"system": "gorgias", "messageId": message_id}
            if args.get("externalTicketId") is not None:
                external["ticketId"] = str(args["externalTicketId"])
            if from_email:
                external["customerEmail"] = from_email
        else:
            external = {"system": "agentmail", "messageId": message_id}
            if from_email:
                external["customerEmail"] = from_email
    record = _intake_record(
        channel="email",
        from_name=from_name,
        from_email=from_email,
        subject=subject,
        body=body,
        received_at=received_at,
        spam=is_spam(subject, body),
    )
    record["messageId"] = message_id
    record["source"] = source
    if external:
        record["external"] = external
    tickets.remember_intake(record)
    if message_id:
        dedupe_key = (source, message_id)
    else:
        dedupe_key = ("email", from_email or from_name, subject, body, received_at)
    if record["spam"]:
        # #33: Gorgias files spam in Spam for review/rescue instead of
        # dropping it. No Shopify join, no customer notification — just a
        # reviewable ticket flagged out of the working views.
        ticket = tickets.add_ticket(
            customer_name=from_name,
            subject=subject,
            body=body,
            received_at=received_at,
            customer_id=None,
            order_id=None,
            channel="email",
            from_email=from_email,
            dedupe_key=dedupe_key,
            source=source,
            external=external,
            spam=True,
        )
        return {"spam": True, "ticketId": ticket["id"], **ticket}
    customer_id, order_id = join_shopify(
        subject=subject,
        body=body,
        from_email=from_email,
        channel="email",
    )
    ticket = tickets.add_ticket(
        customer_name=from_name,
        subject=subject,
        body=body,
        received_at=received_at,
        customer_id=customer_id,
        order_id=order_id,
        channel="email",
        from_email=from_email,
        dedupe_key=dedupe_key,
        source=source,
        external=external,
    )
    return {"spam": False, "ticketId": ticket["id"], **ticket}


def handle_ingest_chat(args: dict[str, Any]) -> dict[str, Any]:
    _require(args, "fromName", "body", "receivedAt")
    from_name = str(args["fromName"]).strip()
    if not from_name:
        raise bad_request("fromName is required", field="fromName")
    body = str(args["body"])
    received_at = str(args["receivedAt"])
    subject = _subject_from_chat(body)
    record = _intake_record(
        channel="chat",
        from_name=from_name,
        from_email=None,
        subject=subject,
        body=body,
        received_at=received_at,
        spam=is_spam(subject, body),
    )
    tickets.remember_intake(record)
    if record["spam"]:
        ticket = tickets.add_ticket(
            customer_name=from_name,
            subject=subject,
            body=body,
            received_at=received_at,
            customer_id=None,
            order_id=None,
            channel="chat",
            from_email=None,
            dedupe_key=("chat", from_name, subject, body, received_at),
            source="chat",
            spam=True,
        )
        return {"spam": True, "ticketId": ticket["id"], **ticket}
    customer_id, order_id = join_shopify(
        subject=subject,
        body=body,
        from_email=None,
        channel="chat",
    )
    ticket = tickets.add_ticket(
        customer_name=from_name,
        subject=subject,
        body=body,
        received_at=received_at,
        customer_id=customer_id,
        order_id=order_id,
        channel="chat",
        from_email=None,
        dedupe_key=("chat", from_name, subject, body, received_at),
        source="chat",
    )
    return {"spam": False, "ticketId": ticket["id"], **ticket}
