"""Caduceus composer tissue. Text out only — never a send.

Input is a ticket thread plus optional rail DTOs (or the six read tools).
Output is a merchant-reply draft or a mute thread peek. No Shopify writes.
processor/draft_generator.py is a retired processor stub that mentions Gorgias
threads; this organ does not import it.
"""

from __future__ import annotations

from typing import Any

from . import tickets
from .errors import HelpdeskError, bad_request
from .fixtures_sample import (
    ADA,
    CASEY,
    JORDAN,
    ORDER_ADA,
    ORDER_CASEY_A,
    ORDER_CASEY_B,
    ORDER_PARTIAL,
)
from .names import SAMPLE_SHOP
from .shop import rail_get_customer, rail_get_order, rail_get_returns, rail_list_past_orders

# Sample ticket → rail GIDs so CLI `draft-reply --ticket 1001` can load context.
SAMPLE_RAIL = {
    "1001": {"customerId": ADA, "orderId": ORDER_ADA},
    "t-ada-track": {"customerId": ADA, "orderId": ORDER_ADA},
    "1002": {"customerId": CASEY, "orderId": ORDER_CASEY_A},
    "1003": {"customerId": JORDAN, "orderId": None},
}

_SAMPLE_GIDS = frozenset({
    ADA,
    CASEY,
    JORDAN,
    ORDER_ADA,
    ORDER_CASEY_A,
    ORDER_CASEY_B,
    ORDER_PARTIAL,
})

def _ticket_id(args: dict[str, Any]) -> str:
    value = args.get("ticketId") or args.get("ticket") or args.get("ticket_id")
    return str(value).strip() if value is not None else ""


def _as_record(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _talk_messages(thread: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for message in thread.get("messages") or []:
        if not isinstance(message, dict):
            continue
        if message.get("kind") == "status":
            continue
        rows.append(message)
    return rows


def _from_customer(message: dict[str, Any]) -> bool:
    if message.get("fromAgent") is True:
        return False
    who = str(message.get("from") or "").strip().lower()
    return who in {"", "customer"}


def _customer_name(thread: dict[str, Any], _customer: dict[str, Any] | None) -> str:
    """Ticket customerName / message name only. Never Customer.displayName."""
    if thread.get("customerName"):
        return str(thread["customerName"])
    for message in _talk_messages(thread):
        if _from_customer(message) and message.get("name"):
            return str(message["name"])
    return ""


def _first_name(full: str) -> str:
    token = (full or "").strip().split()
    return token[0] if token else "there"


def _tracking(order: dict[str, Any] | None) -> tuple[str, str]:
    if not order:
        return "", ""
    for fulfillment in order.get("fulfillments") or []:
        if not isinstance(fulfillment, dict):
            continue
        for info in fulfillment.get("trackingInfo") or []:
            if not isinstance(info, dict):
                continue
            number = str(info.get("number") or "").strip()
            company = str(info.get("company") or "").strip()
            if number:
                return company, number
    return "", ""


def _open_return(returns: dict[str, Any] | None) -> bool:
    if not returns:
        return False
    if returns.get("inProgress") is True:
        return True
    for node in (returns.get("returns") or {}).get("nodes") or []:
        if isinstance(node, dict) and str(node.get("status") or "") == "OPEN":
            return True
    return False


def _last_customer_body(thread: dict[str, Any]) -> str:
    for message in reversed(_talk_messages(thread)):
        if _from_customer(message) and message.get("body"):
            return str(message["body"]).strip()
    return str(thread.get("snippet") or thread.get("subject") or "").strip()


def _customer_photo_count(thread: dict[str, Any]) -> int:
    total = 0
    for message in _talk_messages(thread):
        if not _from_customer(message):
            continue
        attachments = message.get("attachments") or []
        if isinstance(attachments, list):
            total += sum(1 for item in attachments if isinstance(item, dict) and item.get("url"))
    return total


def _load_thread(args: dict[str, Any]) -> dict[str, Any]:
    thread = _as_record(args.get("thread"))
    if thread and (thread.get("messages") is not None or thread.get("subject") or thread.get("id")):
        loaded = dict(thread)
        if not loaded.get("id"):
            loaded["id"] = _ticket_id(args)
        return loaded
    ticket_id = _ticket_id(args)
    if not ticket_id:
        raise bad_request("ticketId is required", field="ticketId")
    return tickets.get_ticket(ticket_id)


def _lookup_ids(args: dict[str, Any], thread: dict[str, Any]) -> tuple[str | None, str | None]:
    mapped = SAMPLE_RAIL.get(str(thread.get("id") or _ticket_id(args)))
    customer_id = args.get("customerId") or args.get("customer_id") or thread.get("customerId")
    order_id = args.get("orderId") or args.get("order_id") or thread.get("orderId")
    if mapped:
        customer_id = customer_id or mapped.get("customerId")
        order_id = order_id if order_id is not None else mapped.get("orderId")
    return (
        str(customer_id) if customer_id else None,
        str(order_id) if order_id else None,
    )


def _try_rail(loader, shop: str | None, ident: str | None) -> Any | None:
    if not shop or not ident:
        return None
    try:
        _source, payload = loader(shop, ident)
        return payload
    except HelpdeskError:
        return None


def _shop_for_rail(shop: str | None, customer_id: str | None, order_id: str | None, thread: dict[str, Any]) -> str | None:
    """Sample/SEED GIDs must load from SAMPLE_SHOP — never Cute Things (not_found → hollow draft)."""
    ticket_id = str(thread.get("id") or "")
    if ticket_id in SAMPLE_RAIL:
        return SAMPLE_SHOP
    if (customer_id and customer_id in _SAMPLE_GIDS) or (order_id and order_id in _SAMPLE_GIDS):
        return SAMPLE_SHOP
    return shop or None


def _load_rail(args: dict[str, Any], thread: dict[str, Any]) -> dict[str, Any]:
    customer = _as_record(args.get("customer"))
    order = _as_record(args.get("order"))
    returns = _as_record(args.get("returns"))
    past_orders = args.get("pastOrders") if args.get("pastOrders") is not None else args.get("past_orders")
    customer_id, order_id = _lookup_ids(args, thread)
    shop = _shop_for_rail(args.get("shop"), customer_id, order_id, thread)
    if customer is None:
        customer = _try_rail(rail_get_customer, shop, customer_id)
    if order is None:
        order = _try_rail(rail_get_order, shop, order_id)
    if returns is None:
        returns = _try_rail(rail_get_returns, shop, order_id)
    if not isinstance(past_orders, list):
        past_orders = _try_rail(rail_list_past_orders, shop, customer_id) or []
    return {
        "customer": customer,
        "order": order,
        "returns": returns,
        "pastOrders": _as_list(past_orders),
    }


REQUEST_TYPE_UNSUBSCRIBE = "marketing_unsubscribe"
REQUEST_TYPE_PRIVACY = "privacy_request"
REQUEST_TYPE_BUG = "bug"


def _request_type(thread: dict[str, Any]) -> str:
    return str(thread.get("requestType") or "").strip()


def draft_for_request_type(request_type: str, name: str) -> str | None:
    """Typed fixture drafts. Never invent order, catalog, or destination copy."""
    typed = str(request_type or "").strip()
    who = name or "there"
    if typed == REQUEST_TYPE_UNSUBSCRIBE:
        return (
            f"Hi {who} — I have your marketing unsubscribe request. I will confirm "
            "the preference out of band. This inbox does not change Shopify marketing settings."
        )
    if typed == REQUEST_TYPE_PRIVACY:
        return (
            f"Hi {who} — I have your privacy request. I will handle the data export "
            "or deletion out of band. This inbox does not write Shopify Customer Privacy."
        )
    if typed == REQUEST_TYPE_BUG:
        return (
            f"Hi {who} — I have your bug report. Reply with the device you reproduced "
            "this on (iOS or Android). This inbox does not invent order or catalog answers."
        )
    return None


# ponytail: one canonical copy each — scenario table and keyword fallbacks share these.
_CANADA_CATALOG_TEMPLATE = (
    "Hi {name} — Yes, we ship the demo catalog to Canada. International rates show at "
    "checkout; any customs or import duties are the customer’s responsibility. I cannot "
    "promise a carrier delivery date from this chat. Let me know if you need anything else."
)
_DAMAGE_UNKNOWN_ORDER_TEMPLATE = (
    "Hi {name} — {photo_bit}I am sorry it arrived that way. "
    "Reply with your order number (like #1001) so I can look this up, and we will sort next "
    "steps from here. I will not refund from this chat. Let me know if you need anything else."
)

# ponytail: adding a demo scenario is a table row, not a new elif.
# (ids, default order ref, template using any of {name}/{looked}/{money}/{ship}/{photo_bit})
_SCENARIO_TEMPLATES: tuple[tuple[frozenset[str], str, str], ...] = (
    (frozenset({"t-demo-04-return"}), "#1003", (
        "Hi {name} — I looked at {looked}. To start a return on the merino throw, reply with "
        "the item name and whether tags are still on, and we will walk you through the return "
        "portal steps from here. A prepaid label is not automatic — once the return is set up, "
        "we will confirm whether a label is included or you need to buy postage. I will not "
        "refund or cancel from this chat. Let me know if you need anything else."
    )),
    (frozenset({"t-demo-05-cancel"}), "#1001", (
        "Hi {name} — I looked at {looked}. It is {money} and {ship}, so it has not been "
        "handed to a carrier yet. I see you asked to cancel because of the wrong size. I will "
        "not cancel or refund from here — a teammate needs to review the hold before anything "
        "changes. I will write back once that review is done. Let me know if you need anything else."
    )),
    (frozenset({"t-demo-08-canada"}), "", (
        "Hi {name} — Yes, we can ship the Muslin Swaddle Trio to Montreal. International "
        "shipping is offered at checkout (about $35 USD as a typical rate — please confirm the "
        "live total before you place the order). Any customs or import duties charged in Canada "
        "are the customer’s responsibility. I cannot promise a carrier delivery date from this "
        "chat. Let me know if you need anything else."
    )),
    (frozenset({"t-demo-14-duplicate"}), "#1001", (
        "Hi {name} — I looked at {looked}. Thanks for flagging the two bank lines that look "
        "like this order. I am checking whether one is a pending authorization versus a second "
        "capture. I will not refund from here — once we confirm what the bank is showing, a "
        "teammate can advise next steps. Let me know if you need anything else."
    )),
    (frozenset({"t-demo-18-exchange"}), "#1003", (
        "Hi {name} — I looked at {looked}. Happy to help with an exchange on the Organic "
        "Cotton Bath Towel Hood for the next size. Reply with the size you want and whether "
        "the current towel is unused with tags on, and we will outline the swap steps from "
        "here. I will not issue a refund from this chat. Let me know if you need anything else."
    )),
    (frozenset({"t-demo-22-policy"}), "", (
        "Hi {name} — For unused baby apparel with tags still on, our demo return window is "
        "7 days after delivery for refund eligibility — the return needs a carrier scan within "
        "that window. After that, eligible returns are usually store credit instead. Final-sale "
        "items follow different rules. I will not process a refund from this chat; write back "
        "with an order number if you want us to check a specific item. Let me know if you need "
        "anything else."
    )),
    (frozenset({"t-demo-12-damaged-box"}), "#1004", (
        "Hi {name} — I looked at {looked}. Thanks for the photo of the damage. I am sorry it "
        "arrived that way. I will sort next steps from here. I will not refund from this chat. "
        "Let me know if you need anything else."
    )),
    (frozenset({"t-demo-03-damaged-rattle", "t-demo-17-plush"}), "", _DAMAGE_UNKNOWN_ORDER_TEMPLATE),
    (frozenset({"t-jordan-ship", "t-multi-snoozed"}), "", _CANADA_CATALOG_TEMPLATE),
)


def _scenario_draft(
    ticket_id: str,
    name: str,
    order_name: str,
    financial: str,
    fulfill: str,
) -> str | None:
    """Caduceus tone for seeded demo scenarios. Never refund/cancel/send promises."""
    oid = order_name or ""
    for ids, default_looked, template in _SCENARIO_TEMPLATES:
        if ticket_id in ids:
            return template.format(
                name=name,
                looked=oid or default_looked,
                money=financial or "Paid",
                ship=fulfill or "Unfulfilled",
                photo_bit="Thanks for the photo of the damage. ",
            )
    return None


def _looks_like_damage(asked: str, subject: str = "") -> bool:
    blob = f"{asked} {subject}".lower()
    needles = (
        "torn",
        "tear",
        "cracked",
        "crack",
        "damaged",
        "damage",
        "broke",
        "broken",
        "seam",
        "ripped",
    )
    return any(word in blob for word in needles)


def _looks_like_canada_ship(asked: str, subject: str = "") -> bool:
    blob = f"{asked} {subject}".lower()
    if "canada" not in blob and "montreal" not in blob:
        return False
    return any(word in blob for word in ("ship", "shipping", "catalog", "deliver"))


def fixture_draft(thread: dict[str, Any], rail: dict[str, Any]) -> str:
    """Merchant-reply draft. Never promises a refund, cancel, or send."""
    customer = rail.get("customer")
    order = rail.get("order")
    returns = rail.get("returns")
    name = _first_name(_customer_name(thread, customer if isinstance(customer, dict) else None))
    status = str(thread.get("status") or "").lower()
    order_rec = order if isinstance(order, dict) else None
    order_name = str((order_rec or {}).get("name") or "").strip()
    financial = str((order_rec or {}).get("displayFinancialStatus") or "").replace("_", " ").title()
    fulfill = str((order_rec or {}).get("displayFulfillmentStatus") or "").replace("_", " ").title()
    company, tracking = _tracking(order_rec)
    open_return = _open_return(returns if isinstance(returns, dict) else None)
    asked = _last_customer_body(thread)
    subject = str(thread.get("subject") or "")
    ticket_id = str(thread.get("id") or "").strip()

    typed = draft_for_request_type(_request_type(thread), name)
    if typed is not None:
        return typed

    scenario = _scenario_draft(ticket_id, name, order_name, financial, fulfill)
    if scenario is not None:
        return scenario

    if not order_name and _looks_like_damage(asked, subject):
        photos = _customer_photo_count(thread)
        photo_bit = "Thanks for the photo of the damage. " if photos else "Thanks for flagging the damage. "
        return _DAMAGE_UNKNOWN_ORDER_TEMPLATE.format(name=name, photo_bit=photo_bit)

    if not order_name and _looks_like_canada_ship(asked, subject):
        return _CANADA_CATALOG_TEMPLATE.format(name=name)

    sentences: list[str] = []
    if status == "closed":
        sentences.append(f"Glad this reached you, {name}.")
        if order_name:
            sentences.append(f"{order_name} can stay closed — write back if anything else comes up.")
        else:
            sentences.append("I am here if anything else comes up.")
        return " ".join(sentences)

    greet = f"Hi {name} —"
    if order_name and financial and fulfill:
        sentences.append(f"{greet} I looked at {order_name}. It is {financial} and {fulfill}.")
    elif order_name:
        sentences.append(f"{greet} I looked at {order_name}.")
    else:
        sentences.append(f"{greet} Happy to help once an order is on this ticket.")

    if tracking:
        label = f"{company} {tracking}".strip() if company else tracking
        sentences.append(f"The carrier update is {label}.")
    elif order_name and fulfill.lower() == "unfulfilled":
        sentences.append("It has not been handed to a carrier yet. I will write back when it ships.")

    if open_return:
        sentences.append("There is an open return on this order. I will not refund or cancel from here.")

    photos = _customer_photo_count(thread)
    if photos == 1:
        sentences.append("I can see the photo you attached and will use it while we sort this out.")
    elif photos > 1:
        sentences.append(f"I can see the {photos} photos you attached and will use them while we sort this out.")

    if asked and not order_name:
        sentences.append("Happy to answer from the published catalog once we have an order to check.")

    sentences.append("Let me know if you need anything else.")
    return " ".join(sentences)


def fixture_summary(thread: dict[str, Any]) -> str:
    """Short mute peek of the thread. Not a reply and never a send."""
    name = _customer_name(thread, None) or "Customer"
    subject = str(thread.get("subject") or "").strip()
    asked = _last_customer_body(thread)
    count = len(_talk_messages(thread))
    status = str(thread.get("status") or "open")
    screen = "Open" if status == "open" else status.replace("_", " ").title()
    who = name.split()[0] if name else "Customer"
    if asked and subject:
        lead = f"{who} asked: {asked}"
        if asked.rstrip().endswith("?"):
            lead = f"{who} asked {asked[0].lower() + asked[1:]}" if asked else lead
        return f"{lead} Subject: {subject}. {count} message{'s' if count != 1 else ''}. Ticket is {screen}."
    if asked:
        return f"{who} asked: {asked} {count} message{'s' if count != 1 else ''}. Ticket is {screen}."
    return f"{who} wrote in. {count} message{'s' if count != 1 else ''}. Ticket is {screen}."


def handle_draft_reply(args: dict[str, Any]) -> dict[str, Any]:
    thread = _load_thread(args)
    rail = _load_rail(args, thread)
    return {"source": "fixture", "draft": fixture_draft(thread, rail)}


def handle_summarize_thread(args: dict[str, Any]) -> dict[str, Any]:
    thread = _load_thread(args)
    return {"source": "fixture", "summary": fixture_summary(thread)}
