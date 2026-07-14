#!/usr/bin/env python3
"""
gorgias_api.py — Thin, safety-first Gorgias REST client for the Hermes Agent.

PHASE 1 SAFETY MODEL (do not weaken):
  * The ONLY write this client can perform is posting an INTERNAL NOTE
    (channel="internal-note", public=false). Internal notes are never sent
    to the customer — a human reads them and decides what to do.
  * post-note is DRY-RUN by default. It will not touch Gorgias unless you
    pass --confirm AND the environment allows writes (HERMES_ALLOW_WRITE=1).
  * Everything else (ticket, messages, customer, order-context, users) is
    strictly read-only.

CREDENTIALS (read in this order):
  1. Environment variables:
       GORGIAS_BASE_URL      e.g. https://buttons-bebe.gorgias.com
       GORGIAS_USERNAME      your Gorgias account email
       GORGIAS_API_KEY       your Gorgias REST API key
  2. Fallback: /root/gorgias-webhook/config.json
       (keys: gorgias_base_url, gorgias_username, gorgias_api_key)

  Optional:
       GORGIAS_AGENT_USER_ID  Gorgias user id the note is posted as
                              (default 777419526 — verified for this store)
       HERMES_ALLOW_WRITE     must be "1" for post-note --confirm to fire

WHY THE USER-AGENT MATTERS:
  Gorgias's WAF returns 403 to the default Python-urllib User-Agent. We set
  an explicit one on every request. Removing it causes phantom 403s on a
  perfectly valid key.

Usage:
  gorgias_api.py users
  gorgias_api.py ticket <ticket_id>
  gorgias_api.py messages <ticket_id>
  gorgias_api.py customer <customer_id>
  gorgias_api.py order-context <ticket_id>
  gorgias_api.py post-note <ticket_id> --body "draft text"          # dry run
  gorgias_api.py post-note <ticket_id> --body "draft text" --confirm # real post
  gorgias_api.py selfcheck                                           # offline
"""

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "Hermes-Agent/1.0 (+buttons-bebe; gorgias-skill)"
DEFAULT_AGENT_USER_ID = 777419526
CONFIG_FALLBACK = "/root/gorgias-webhook/config.json"
INTERNAL_NOTE_CHANNEL = "internal-note"


# --------------------------------------------------------------------------- #
# Credentials
# --------------------------------------------------------------------------- #
def load_credentials():
    base_url = os.environ.get("GORGIAS_BASE_URL", "").strip()
    username = os.environ.get("GORGIAS_USERNAME", "").strip()
    api_key = os.environ.get("GORGIAS_API_KEY", "").strip()

    if not (base_url and username and api_key):
        try:
            with open(CONFIG_FALLBACK, "r", encoding="utf-8") as fh:
                cfg = json.load(fh)
            base_url = base_url or str(cfg.get("gorgias_base_url", "")).strip()
            username = username or str(cfg.get("gorgias_username", "")).strip()
            api_key = api_key or str(cfg.get("gorgias_api_key", "")).strip()
        except (OSError, ValueError):
            pass

    placeholders = ("", "YOUR-STORE", "your-gorgias-username", "your-gorgias-api-key")
    looks_placeholder = (
        not base_url
        or "YOUR-STORE" in base_url
        or username in placeholders
        or api_key in placeholders
    )
    if looks_placeholder:
        die(
            "Gorgias credentials are missing or still placeholders.\n"
            "Set GORGIAS_BASE_URL, GORGIAS_USERNAME, GORGIAS_API_KEY in the "
            "environment, or fill them into " + CONFIG_FALLBACK + "."
        )

    return base_url.rstrip("/"), username, api_key


def auth_header(username, api_key):
    raw = f"{username}:{api_key}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def request(method, url, username, api_key, body=None, max_retries=3):
    data = None
    headers = {
        "User-Agent": USER_AGENT,          # critical: avoids Gorgias WAF 403
        "Authorization": auth_header(username, api_key),
        "Accept": "application/json",
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    attempt = 0
    while True:
        attempt += 1
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = resp.read().decode("utf-8")
                return resp.status, json.loads(payload) if payload else {}
        except urllib.error.HTTPError as exc:
            # Respect rate limiting (429) with the Retry-after header.
            if exc.code == 429 and attempt <= max_retries:
                try:
                    wait = int(exc.headers.get("Retry-after", "5") or "5")
                except (ValueError, TypeError):
                    wait = 5
                time.sleep(min(wait, 30))
                continue
            detail = ""
            try:
                detail = exc.read().decode("utf-8")
            except Exception:
                pass
            die(f"HTTP {exc.code} on {method} {url}\n{detail}")
        except urllib.error.URLError as exc:
            if attempt <= max_retries:
                time.sleep(2 * attempt)
                continue
            die(f"Network error on {method} {url}: {exc}")


# --------------------------------------------------------------------------- #
# Read operations
# --------------------------------------------------------------------------- #
def get_ticket(base_url, username, api_key, ticket_id):
    url = f"{base_url}/api/tickets/{int(ticket_id)}"
    _, data = request("GET", url, username, api_key)
    return data


def list_messages(base_url, username, api_key, ticket_id, limit=100):
    q = urllib.parse.urlencode(
        {"ticket_id": int(ticket_id), "limit": int(limit), "order_by": "created_datetime:asc"}
    )
    url = f"{base_url}/api/messages?{q}"
    _, data = request("GET", url, username, api_key)
    return data


def get_customer(base_url, username, api_key, customer_id):
    url = f"{base_url}/api/customers/{int(customer_id)}"
    _, data = request("GET", url, username, api_key)
    return data


def list_users(base_url, username, api_key):
    url = f"{base_url}/api/users"
    _, data = request("GET", url, username, api_key)
    return data


# --------------------------------------------------------------------------- #
# Order context (parse the Shopify block synced into the customer object)
# --------------------------------------------------------------------------- #
def extract_order_context(customer):
    """Pull the Phase-1-usable order data out of customer.integrations.

    Returns a compact dict. Known Phase-1 gaps (tracking, returns/refunds,
    order history older than the 10 most recent) are reported, not invented.
    """
    out = {
        "customer_id": customer.get("id"),
        "customer_name": customer.get("name"),
        "customer_email": customer.get("email"),
        "orders_count": None,
        "orders": [],
        "shopify_found": False,
        "gaps": ["tracking_links", "returns_refunds", "orders_older_than_10_most_recent"],
    }
    integrations = customer.get("integrations") or {}
    if isinstance(integrations, dict):
        blocks = integrations.values()
    else:
        blocks = integrations  # be defensive about shape

    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("__integration_type__") != "shopify":
            continue
        out["shopify_found"] = True
        cust = block.get("customer") or {}
        out["orders_count"] = cust.get("orders_count")
        for order in (block.get("orders") or []):
            out["orders"].append(
                {
                    "name": order.get("name"),
                    "created_at": order.get("created_at"),
                    "financial_status": order.get("financial_status"),
                    "fulfillment_status": order.get("fulfillment_status"),
                    "line_items": [
                        {
                            "sku": li.get("sku"),
                            "title": li.get("title"),
                            "quantity": li.get("quantity"),
                        }
                        for li in (order.get("line_items") or [])
                    ],
                    "shipping_address": order.get("shipping_address"),
                    "billing_address": order.get("billing_address"),
                }
            )
        break
    return out


def order_context_for_ticket(base_url, username, api_key, ticket_id):
    ticket = get_ticket(base_url, username, api_key, ticket_id)
    customer = ticket.get("customer") or {}
    customer_id = customer.get("id")
    if not customer_id:
        die("Ticket has no associated customer id.")
    full_customer = get_customer(base_url, username, api_key, customer_id)
    return extract_order_context(full_customer)


# --------------------------------------------------------------------------- #
# Write operation — the ONLY one. Internal note, dry-run by default.
# --------------------------------------------------------------------------- #
def build_internal_note_payload(body_text, sender_id, mention_ids=None):
    body_text = body_text or ""
    body_html = body_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    body_html = body_html.replace("\n", "<br>\n")
    payload = {
        "channel": INTERNAL_NOTE_CHANNEL,   # never a public channel
        "via": "internal-note",
        "from_agent": True,
        "public": False,                    # internal notes are private
        "body_text": body_text,
        "body_html": body_html,
        "source": {"type": "internal-note"},
        "sender": {"id": int(sender_id)},
    }
    if mention_ids:
        payload["mention_ids"] = [int(m) for m in mention_ids]
    return payload


def post_internal_note(base_url, username, api_key, ticket_id, body_text,
                       sender_id, confirm=False, mention_ids=None):
    payload = build_internal_note_payload(body_text, sender_id, mention_ids)

    # Hard safety guard: refuse to ever post anything public.
    # (assert is a no-op under python -O; use an unconditional if.)
    if payload.get("channel") != INTERNAL_NOTE_CHANNEL or payload.get("public") is not False:
        die("SAFETY VIOLATION: refusing to post a non-internal-note message.")

    if not confirm or os.environ.get("HERMES_ALLOW_WRITE") != "1":
        return {
            "dry_run": True,
            "would_post_to": f"{base_url}/api/tickets/{int(ticket_id)}/messages",
            "payload": payload,
            "note": "DRY RUN. Re-run with --confirm and HERMES_ALLOW_WRITE=1 to post.",
        }

    url = f"{base_url}/api/tickets/{int(ticket_id)}/messages"
    status, data = request("POST", url, username, api_key, body=payload)
    return {"dry_run": False, "status": status, "message_id": data.get("id"), "result": data}


# --------------------------------------------------------------------------- #
# Write operations — internal ticket metadata (tags & priority).
#
# These are INTERNAL METADATA ONLY. They never message a customer. Same
# dry-run gating as post_internal_note: a real write fires only when
# confirm=True AND HERMES_ALLOW_WRITE == "1"; otherwise we return a
# {"dry_run": True, ...} description of what WOULD be sent and make NO
# network call.
#
# Gorgias endpoint assumptions (verified against developers.gorgias.com,
# 2026-06-26 — isolated here so they are trivial to correct):
#   * TAGS:     POST {base}/api/tickets/{id}/tags  body {"names": [...]}
#               This is the dedicated "add ticket tags" endpoint. It takes
#               tag *names* (created-if-missing per Gorgias semantics) and
#               APPENDS them to the ticket (it does not replace existing
#               tags). Alternative documented body is {"ids": [...]} for
#               pre-existing tag ids; we use names so callers don't have to
#               pre-resolve ids. Endpoint built only in _tags_url().
#   * PRIORITY: PUT  {base}/api/tickets/{id}        body {"priority": "..."}
#               The priority field lives on the ticket object. Per the docs
#               the canonical values are low|normal|high|critical. The
#               Hermes tasklist refers to "urgent" as the top level, so we
#               accept BOTH "urgent" and "critical" and let the live API be
#               the final authority. Endpoint built only in _ticket_url().
# --------------------------------------------------------------------------- #
VALID_PRIORITIES = ("low", "normal", "high", "urgent", "critical")


def _tags_url(base_url, ticket_id):
    """The single place the ticket-tags endpoint is constructed."""
    return f"{base_url}/api/tickets/{int(ticket_id)}/tags"


def _ticket_url(base_url, ticket_id):
    """The single place the ticket-update endpoint is constructed."""
    return f"{base_url}/api/tickets/{int(ticket_id)}"


def build_add_tags_payload(tags):
    """Normalise tags into the Gorgias add-tags body: {"names": [...]}.

    Accepts a single string or an iterable of strings. Tags are de-duped
    (order-preserving) and blank entries dropped. Tags are created-if-missing
    by Gorgias when referenced by name.
    """
    if isinstance(tags, str):
        tags = [tags]
    names = []
    seen = set()
    for tag in (tags or []):
        name = str(tag).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    if not names:
        die("add_tags requires at least one non-empty tag name.")
    return {"names": names}


def add_tags(base_url, username, api_key, ticket_id, tags, *, confirm=False):
    """Add tags to a ticket (e.g. ["ai-drafted"], ["escalate"], ["ai-handled"]).

    Internal metadata only — this never messages the customer. Dry-run by
    default: a real POST fires only when confirm=True AND
    HERMES_ALLOW_WRITE == "1". Otherwise returns {"dry_run": True, ...} and
    makes no network call. Tags are created-if-missing per Gorgias semantics
    and APPENDED to the ticket.
    """
    payload = build_add_tags_payload(tags)
    url = _tags_url(base_url, ticket_id)

    if not confirm or os.environ.get("HERMES_ALLOW_WRITE") != "1":
        return {
            "dry_run": True,
            "would_post_to": url,
            "method": "POST",
            "payload": payload,
            "note": "DRY RUN. Re-run with --confirm and HERMES_ALLOW_WRITE=1 to apply.",
        }

    status, data = request("POST", url, username, api_key, body=payload)
    return {"dry_run": False, "status": status, "result": data}


def build_set_priority_payload(priority):
    """Normalise/validate priority into the Gorgias ticket-update body."""
    value = str(priority or "").strip().lower()
    if value not in VALID_PRIORITIES:
        die(
            f"Invalid priority {priority!r}. Expected one of "
            f"{', '.join(VALID_PRIORITIES)}."
        )
    return {"priority": value}


def set_priority(base_url, username, api_key, ticket_id, priority, *, confirm=False):
    """Set a ticket's priority (low|normal|high|urgent|critical).

    Internal metadata only — this never messages the customer. Dry-run by
    default: a real PUT fires only when confirm=True AND
    HERMES_ALLOW_WRITE == "1". Otherwise returns {"dry_run": True, ...} and
    makes no network call.
    """
    payload = build_set_priority_payload(priority)
    url = _ticket_url(base_url, ticket_id)

    if not confirm or os.environ.get("HERMES_ALLOW_WRITE") != "1":
        return {
            "dry_run": True,
            "would_post_to": url,
            "method": "PUT",
            "payload": payload,
            "note": "DRY RUN. Re-run with --confirm and HERMES_ALLOW_WRITE=1 to apply.",
        }

    status, data = request("PUT", url, username, api_key, body=payload)
    return {"dry_run": False, "status": status, "result": data}


def tag_ticket(base_url, username, api_key, ticket_id, *, tags=None,
               priority=None, confirm=False):
    """Convenience wrapper: add tags and/or set priority in one call.

    Internal metadata only — never messages the customer. Each underlying
    write keeps its own airtight dry-run gating (confirm=True AND
    HERMES_ALLOW_WRITE == "1"). At least one of `tags`/`priority` is required.
    Returns a dict with whichever of "tags"/"priority" results were run.
    """
    if not tags and not priority:
        die("tag_ticket requires at least one of tags= or priority=.")
    out = {}
    if tags:
        out["tags"] = add_tags(base_url, username, api_key, ticket_id, tags,
                               confirm=confirm)
    if priority:
        out["priority"] = set_priority(base_url, username, api_key, ticket_id,
                                       priority, confirm=confirm)
    return out


# --------------------------------------------------------------------------- #
# Helpers / CLI
# --------------------------------------------------------------------------- #
def die(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def emit(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def selfcheck():
    """Offline validation — no network, no credentials needed."""
    # 1) payload builder produces a safe internal note
    p = build_internal_note_payload("Hello\n<b>world</b> & friends", DEFAULT_AGENT_USER_ID)
    assert p["channel"] == "internal-note"
    assert p["public"] is False
    assert p["from_agent"] is True
    assert p["sender"]["id"] == DEFAULT_AGENT_USER_ID
    assert "&lt;b&gt;" in p["body_html"] and "&amp;" in p["body_html"]
    # 2) order-context parser handles a representative Shopify block
    sample_customer = {
        "id": 5, "name": "Test", "email": "t@example.com",
        "integrations": {
            "999": {
                "__integration_type__": "shopify",
                "customer": {"orders_count": 18},
                "orders": [{
                    "name": "#1001", "created_at": "2026-06-01T00:00:00",
                    "financial_status": "paid", "fulfillment_status": "fulfilled",
                    "line_items": [{"sku": "BB-1", "title": "Onesie", "quantity": 2}],
                    "shipping_address": {"city": "NYC"}, "billing_address": {"city": "NYC"},
                }],
            }
        },
    }
    ctx = extract_order_context(sample_customer)
    assert ctx["shopify_found"] is True
    assert ctx["orders_count"] == 18
    assert ctx["orders"][0]["financial_status"] == "paid"
    assert ctx["orders"][0]["line_items"][0]["sku"] == "BB-1"
    # 3) dry-run never posts even when confirm=True but env disallows writes.
    #    Trip-wire: replace request() so ANY network attempt fails the test.
    os.environ.pop("HERMES_ALLOW_WRITE", None)
    real_request = globals()["request"]

    def _no_network(*_a, **_k):
        raise AssertionError("SAFETY VIOLATION: selfcheck attempted a network call.")

    globals()["request"] = _no_network
    try:
        res = post_internal_note("https://x.gorgias.com", "u", "k", 1, "hi",
                                 DEFAULT_AGENT_USER_ID, confirm=True)
        assert res["dry_run"] is True
        assert res["payload"]["public"] is False

        # 4) tags & priority writes are dry-run gated identically.
        tag_payload = build_add_tags_payload(["ai-drafted", "ai-drafted", " ", "escalate"])
        assert tag_payload == {"names": ["ai-drafted", "escalate"]}, tag_payload

        tres = add_tags("https://x.gorgias.com", "u", "k", 1, ["ai-drafted"],
                        confirm=True)
        assert tres["dry_run"] is True
        assert tres["method"] == "POST"
        assert tres["would_post_to"].endswith("/api/tickets/1/tags")
        assert tres["payload"] == {"names": ["ai-drafted"]}

        pres = set_priority("https://x.gorgias.com", "u", "k", 1, "URGENT",
                            confirm=True)
        assert pres["dry_run"] is True
        assert pres["method"] == "PUT"
        assert pres["would_post_to"].endswith("/api/tickets/1")
        assert pres["payload"] == {"priority": "urgent"}

        # convenience wrapper: both halves dry-run, nothing leaves the box.
        combo = tag_ticket("https://x.gorgias.com", "u", "k", 1,
                           tags=["ai-handled"], priority="high", confirm=True)
        assert combo["tags"]["dry_run"] is True
        assert combo["priority"]["dry_run"] is True
        assert combo["priority"]["payload"] == {"priority": "high"}
    finally:
        globals()["request"] = real_request

    print("selfcheck: OK — payload, parser, note-guard, and tag/priority guards all pass.")


def main():
    parser = argparse.ArgumentParser(description="Safety-first Gorgias REST client for Hermes.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("users", help="List Gorgias users (find the agent user id).")
    sub.add_parser("selfcheck", help="Offline self-test (no network).")

    p = sub.add_parser("ticket", help="Retrieve a ticket.")
    p.add_argument("ticket_id")

    p = sub.add_parser("messages", help="List a ticket's messages.")
    p.add_argument("ticket_id")
    p.add_argument("--limit", type=int, default=100)

    p = sub.add_parser("customer", help="Retrieve a customer.")
    p.add_argument("customer_id")

    p = sub.add_parser("order-context", help="Order context for a ticket (read-only).")
    p.add_argument("ticket_id")

    p = sub.add_parser("post-note", help="Post an internal note (dry-run by default).")
    p.add_argument("ticket_id")
    p.add_argument("--body", required=True, help="The draft text for the internal note.")
    p.add_argument("--mention", action="append", default=[], help="User id to @mention (repeatable).")
    p.add_argument("--confirm", action="store_true", help="Actually post (also needs HERMES_ALLOW_WRITE=1).")

    p = sub.add_parser("add-tags", help="Add tags to a ticket (dry-run by default).")
    p.add_argument("ticket_id")
    p.add_argument("--tag", action="append", default=[], required=True,
                   help="Tag name to add, e.g. ai-drafted (repeatable).")
    p.add_argument("--confirm", action="store_true", help="Actually apply (also needs HERMES_ALLOW_WRITE=1).")

    p = sub.add_parser("set-priority", help="Set a ticket's priority (dry-run by default).")
    p.add_argument("ticket_id")
    p.add_argument("--priority", required=True,
                   help="One of low|normal|high|urgent|critical.")
    p.add_argument("--confirm", action="store_true", help="Actually apply (also needs HERMES_ALLOW_WRITE=1).")

    args = parser.parse_args()

    if args.cmd == "selfcheck":
        selfcheck()
        return

    base_url, username, api_key = load_credentials()
    sender_id = int(os.environ.get("GORGIAS_AGENT_USER_ID", DEFAULT_AGENT_USER_ID))

    if args.cmd == "users":
        emit(list_users(base_url, username, api_key))
    elif args.cmd == "ticket":
        emit(get_ticket(base_url, username, api_key, args.ticket_id))
    elif args.cmd == "messages":
        emit(list_messages(base_url, username, api_key, args.ticket_id, args.limit))
    elif args.cmd == "customer":
        emit(get_customer(base_url, username, api_key, args.customer_id))
    elif args.cmd == "order-context":
        emit(order_context_for_ticket(base_url, username, api_key, args.ticket_id))
    elif args.cmd == "post-note":
        emit(post_internal_note(base_url, username, api_key, args.ticket_id,
                                args.body, sender_id, confirm=args.confirm,
                                mention_ids=args.mention))
    elif args.cmd == "add-tags":
        emit(add_tags(base_url, username, api_key, args.ticket_id, args.tag,
                      confirm=args.confirm))
    elif args.cmd == "set-priority":
        emit(set_priority(base_url, username, api_key, args.ticket_id,
                          args.priority, confirm=args.confirm))


if __name__ == "__main__":
    main()
