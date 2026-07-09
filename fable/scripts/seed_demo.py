#!/usr/bin/env python3
"""Fable demo-data seeder — populates a lived-in support desk via the REAL intake
and action endpoints (API-CONTRACT.md §1), so every ticket/draft goes through the
true pipeline (risk classify -> brain draft -> store).

Run via fable/scripts/seed-demo.sh (which boots the stack first). Safe to run
more than once against the same DB: intake find-or-creates customers/tickets,
so a re-run just adds more messages/tickets rather than crashing.

stdlib + httpx only. httpx clients use trust_env=False so nothing is routed
through an ambient proxy — every call stays on localhost.
"""
from __future__ import annotations

import os
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

import httpx

FABLE_HOST = os.environ.get("FABLE_HOST", "127.0.0.1")
FABLE_PORT = os.environ.get("FABLE_PORT", "9600")
FABLE_BASE = os.environ.get("FABLE_BASE") or f"http://{FABLE_HOST}:{FABLE_PORT}"
MAILBOX_BASE = os.environ.get("MAILBOX_BASE", "http://127.0.0.1:9603")

TIMEOUT = 10.0
DRAFT_WAIT_TIMEOUT = 30.0
POLL_INTERVAL = 0.3

warnings: list[str] = []
fatal = False


def warn(msg: str) -> None:
    warnings.append(msg)
    print(f"   ! {msg}")


def fail(msg: str) -> None:
    global fatal
    fatal = True
    warnings.append(msg)
    print(f"   XX {msg}")


client = httpx.Client(base_url=FABLE_BASE, timeout=TIMEOUT, trust_env=False)
mail_client = httpx.Client(base_url=MAILBOX_BASE, timeout=TIMEOUT, trust_env=False)


# --- intake ------------------------------------------------------------------
def intake_email(from_email, from_name, subject, body_text):
    try:
        r = client.post("/fable/api/intake/email", json={
            "from_email": from_email, "from_name": from_name,
            "subject": subject, "body_text": body_text,
        })
    except httpx.HTTPError as e:
        fail(f"email intake for {from_email} failed: {e!r}")
        return None
    if r.status_code >= 400:
        fail(f"email intake for {from_email} returned {r.status_code}: {r.text[:200]}")
        return None
    return r.json()["ticket_id"]


def intake_chat(session_id, name, email, body_text):
    try:
        r = client.post("/fable/api/intake/chat", json={
            "session_id": session_id, "name": name, "email": email,
            "body_text": body_text,
        })
    except httpx.HTTPError as e:
        fail(f"chat intake for {name} failed: {e!r}")
        return None
    if r.status_code >= 400:
        fail(f"chat intake for {name} returned {r.status_code}: {r.text[:200]}")
        return None
    return r.json()["ticket_id"]


def intake_whatsapp(phone, name, body_text):
    try:
        r = client.post("/fable/api/intake/whatsapp", json={
            "phone": phone, "name": name, "body_text": body_text,
        })
    except httpx.HTTPError as e:
        fail(f"whatsapp intake for {name} failed: {e!r}")
        return None
    if r.status_code >= 400:
        fail(f"whatsapp intake for {name} returned {r.status_code}: {r.text[:200]}")
        return None
    return r.json()["ticket_id"]


# --- reads / actions -----------------------------------------------------------
def get_ticket(tid):
    r = client.get(f"/fable/api/tickets/{tid}")
    r.raise_for_status()
    return r.json()["ticket"]


def wait_for_drafts(ticket_ids, timeout=DRAFT_WAIT_TIMEOUT):
    """Poll until every ticket has a draft (or timeout). Returns (dict tid->ticket, set timed_out)."""
    pending = {tid for tid in ticket_ids if tid is not None}
    results = {}
    deadline = time.time() + timeout
    while pending and time.time() < deadline:
        for tid in list(pending):
            try:
                t = get_ticket(tid)
            except httpx.HTTPError:
                continue
            if t.get("draft"):
                results[tid] = t
                pending.discard(tid)
        if pending:
            time.sleep(POLL_INTERVAL)
    for tid in pending:
        try:
            results[tid] = get_ticket(tid)
        except httpx.HTTPError:
            pass
    return results, pending


def send_action(tid, text):
    try:
        r = client.post(f"/fable/api/tickets/{tid}/send", json={"text": text})
    except httpx.HTTPError as e:
        warn(f"send on ticket {tid} failed: {e!r}")
        return False
    if r.status_code >= 400:
        warn(f"send on ticket {tid} returned {r.status_code}: {r.text[:200]}")
        return False
    return True


def note_action(tid, text):
    try:
        r = client.post(f"/fable/api/tickets/{tid}/note", json={"text": text})
    except httpx.HTTPError as e:
        warn(f"note on ticket {tid} failed: {e!r}")
        return False
    if r.status_code >= 400:
        warn(f"note on ticket {tid} returned {r.status_code}: {r.text[:200]}")
        return False
    return True


def rewrite_action(tid, instruction):
    try:
        r = client.post(f"/fable/api/tickets/{tid}/rewrite", json={"instruction": instruction})
    except httpx.HTTPError as e:
        warn(f"rewrite on ticket {tid} failed: {e!r}")
        return False
    if r.status_code >= 400:
        warn(f"rewrite on ticket {tid} returned {r.status_code}: {r.text[:200]}")
        return False
    return True


def patch_ticket(tid, body):
    try:
        r = client.patch(f"/fable/api/tickets/{tid}", json=body)
    except httpx.HTTPError as e:
        warn(f"patch on ticket {tid} ({body}) failed: {e!r}")
        return False
    if r.status_code >= 400:
        warn(f"patch on ticket {tid} ({body}) returned {r.status_code}: {r.text[:200]}")
        return False
    return True


# --- the demo cast -------------------------------------------------------------
# Real seeded Shopify customers get real order context; the rest are invented.
EMAIL_TICKETS = [
    dict(label="emma", from_email="emma.wilson@example.com", from_name="Emma Wilson",
         subject="Order status?", body="Where is my order #BB1015?"),
    dict(label="sophie", from_email="sophie.martin@example.com", from_name="Sophie Martin",
         subject="Return request",
         body="I'd like to return the knit set from #BB1022, it's too small."),
    dict(label="lucas", from_email="lucas.brown@example.com", from_name="Lucas Brown",
         subject="Order not shipped",
         body="I ordered a week ago (#BB1031) and it still says not shipped — when will it go out?"),
    dict(label="priya", from_email="priya.shah@example.com", from_name="Priya Shah",
         subject="Sizing question", body="Does the 6-12M romper run small?"),
    dict(label="jordan", from_email="jordan.lee@example.com", from_name="Jordan Lee",
         subject="Discount code?", body="Do you have a discount code for first orders?"),
    dict(label="maya", from_email="maya.chen@example.com", from_name="Maya Chen",
         subject="Change shipping address",
         body="Can I change my shipping address? Order placed an hour ago."),
    dict(label="olivia", from_email="olivia.garcia@example.com", from_name="Olivia Garcia",
         subject="Package never arrived", sensitive=True,
         body="My package says delivered but it never arrived. I want a refund or replacement."),
    dict(label="taylor", from_email="taylor.reed@example.com", from_name="Taylor Reed",
         subject="WRONG ITEM AGAIN", sensitive=True,
         body="THIS IS THE THIRD TIME I AM WRITING!!! WRONG ITEM AGAIN. I WANT MY MONEY BACK!!!"),
]

CHAT_TICKETS = [
    dict(label="nora", name="Nora", email=None, body="Do you ship to Canada?"),
    dict(label="grace", name="Grace", email=None, body="Is the strawberry onesie back in stock?"),
    dict(label="ben", name="Ben Ortiz", email="ben.ortiz@example.com",
         body="How long does shipping take to Australia?"),
    dict(label="ivy", name="Ivy", email=None, body="Can I gift wrap an order?"),
    dict(label="sam", name="Sam", email=None, sensitive=True,
         body="the zipper on the sleeper is broken and scratched my baby, this is dangerous"),
]

WHATSAPP_TICKETS = [
    dict(label="chloe", name="Chloe Kim", phone="+15550001111",
         body="Hi! Can I add a matching hat to my order from yesterday?"),
    dict(label="diego", name="Diego Ramirez", phone="+15550002222",
         body="What's your return policy?"),
    dict(label="ruby", name="Ruby Fischer", phone="+15550003333",
         body="Do you have a size chart?"),
    dict(label="zoe", name="Zoe Patel", phone="+15550004444", sensitive=True,
         body="order arrived damaged, box was crushed"),
    dict(label="leo", name="Leo Martin", phone="+15550005555",
         body="Do you sell gift cards?"),
]


def main() -> int:
    print()
    print("============================================================")
    print("  Fable demo-data seeder — Buttons Bebe AI help desk")
    print("============================================================")
    print(f"  Fable:   {FABLE_BASE}")
    print(f"  Mailbox: {MAILBOX_BASE}")
    print()

    ids: dict[str, int | None] = {}

    print(f"E-mail tickets ({len(EMAIL_TICKETS)}) ...")
    for spec in EMAIL_TICKETS:
        tid = intake_email(spec["from_email"], spec["from_name"], spec["subject"], spec["body"])
        ids[spec["label"]] = tid
        tag = " [sensitive]" if spec.get("sensitive") else ""
        print(f"   {spec['from_name']:<16} -> ticket #{tid}{tag}")

    print(f"Chat tickets ({len(CHAT_TICKETS)}) ...")
    for spec in CHAT_TICKETS:
        tid = intake_chat(f"demo-chat-{spec['label']}", spec["name"], spec.get("email"), spec["body"])
        ids[spec["label"]] = tid
        tag = " [sensitive]" if spec.get("sensitive") else ""
        print(f"   {spec['name']:<16} -> ticket #{tid}{tag}")

    print(f"WhatsApp tickets ({len(WHATSAPP_TICKETS)}) ...")
    for spec in WHATSAPP_TICKETS:
        tid = intake_whatsapp(spec["phone"], spec["name"], spec["body"])
        ids[spec["label"]] = tid
        tag = " [sensitive]" if spec.get("sensitive") else ""
        print(f"   {spec['name']:<16} -> ticket #{tid}{tag}")

    print()
    print("Waiting for the AI pipeline to draft replies ...")
    all_ids = [v for v in ids.values() if v is not None]
    results, timed_out = wait_for_drafts(all_ids)
    if timed_out:
        warn(f"{len(timed_out)} ticket(s) had no draft after {DRAFT_WAIT_TIMEOUT:.0f}s: {sorted(timed_out)}")
    else:
        print(f"   all {len(all_ids)} tickets have a draft.")

    # --- Emma follow-up: second email within the 7-day reuse window --------
    print()
    print("Emma sends a follow-up e-mail (tests 7-day thread reuse) ...")
    followup_tid = intake_email(
        "emma.wilson@example.com", "Emma Wilson", "Re: Order status?",
        "Also — do you know if it'll arrive before Friday? Thanks so much!",
    )
    if followup_tid is not None and followup_tid != ids.get("emma"):
        warn(f"Emma's follow-up created a NEW ticket (#{followup_tid}) instead of reusing "
             f"#{ids.get('emma')} — 7-day reuse window may not be working as expected.")
    emma_tid = ids.get("emma")
    if emma_tid is not None:
        results2, timed_out2 = wait_for_drafts([emma_tid], timeout=20.0)
        if timed_out2:
            warn(f"Emma's follow-up draft did not arrive within 20s.")
        else:
            results[emma_tid] = results2.get(emma_tid, results.get(emma_tid))
            emma_msgs = [m for m in results[emma_tid].get("messages", []) if not m["from_agent"]]
            draft_text = (results[emma_tid].get("draft") or {}).get("body_text", "")
            print(f"   Emma's ticket now has {len(emma_msgs)} customer message(s).")
            if "1Z999AA10123456784" in draft_text:
                print("   draft cites the real tracking number 1Z999AA10123456784.")
            else:
                warn("Emma's latest draft does not mention tracking number 1Z999AA10123456784.")

    # --- console actions: every UI verb exercised at least once ------------
    print()
    print("Console actions ...")

    # send: one email (-> mailbox outbox) + one chat
    priya_tid = ids.get("priya")
    if priya_tid is not None:
        t = results.get(priya_tid) or get_ticket(priya_tid)
        draft_text = (t.get("draft") or {}).get("body_text") or (
            "Thanks so much for reaching out — we'll follow up shortly!\n\n"
            "— Buttons Bebe Care Team"
        )
        if send_action(priya_tid, draft_text):
            print(f"   sent reply on email ticket #{priya_tid} (Priya) -> mailbox outbox")

    nora_tid = ids.get("nora")
    if nora_tid is not None:
        t = results.get(nora_tid) or get_ticket(nora_tid)
        draft_text = (t.get("draft") or {}).get("body_text") or (
            "Thanks for reaching out! Happy to help.\n\n— Buttons Bebe Care Team"
        )
        if send_action(nora_tid, draft_text):
            print(f"   sent reply on chat ticket #{nora_tid} (Nora)")

    # note: one email + one chat
    jordan_tid = ids.get("jordan")
    if jordan_tid is not None and note_action(jordan_tid, "internal: no active discount code right now, checking with marketing"):
        print(f"   saved internal note on email ticket #{jordan_tid} (Jordan)")

    grace_tid = ids.get("grace")
    if grace_tid is not None and note_action(grace_tid, "internal: confirmed strawberry onesie restock ETA with warehouse"):
        print(f"   saved internal note on chat ticket #{grace_tid} (Grace)")

    # rewrite: one whatsapp draft, friendlier + shorter
    leo_tid = ids.get("leo")
    if leo_tid is not None and rewrite_action(leo_tid, "make it shorter and friendlier"):
        print(f"   asked the AI to rewrite the WhatsApp draft on ticket #{leo_tid} (Leo)")

    # close 3 tickets
    for label in ("maya", "chloe", "ruby"):
        tid = ids.get(label)
        if tid is not None and patch_ticket(tid, {"status": "closed"}):
            print(f"   closed ticket #{tid} ({label})")

    # snooze 1 ticket until tomorrow, tag it too
    lucas_tid = ids.get("lucas")
    if lucas_tid is not None:
        tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        if patch_ticket(lucas_tid, {"status": "snoozed", "snooze_until": tomorrow, "tags": ["shipping"]}):
            print(f"   snoozed ticket #{lucas_tid} (Lucas) until {tomorrow}, tagged 'shipping'")

    # tags on ~4 tickets total (lucas already tagged above)
    if priya_tid is not None and patch_ticket(priya_tid, {"tags": ["sizing"]}):
        print(f"   tagged ticket #{priya_tid} (Priya) 'sizing'")
    sophie_tid = ids.get("sophie")
    if sophie_tid is not None and patch_ticket(sophie_tid, {"tags": ["returns"]}):
        print(f"   tagged ticket #{sophie_tid} (Sophie) 'returns'")
    if emma_tid is not None and patch_ticket(emma_tid, {"tags": ["vip"]}):
        print(f"   tagged ticket #{emma_tid} (Emma) 'vip'")

    # --- summary -------------------------------------------------------------
    print()
    print("Gathering final state ...")
    try:
        r = client.get("/fable/api/tickets", params={"status": "all", "limit": 200})
        r.raise_for_status()
        payload = r.json()
        all_tickets = payload["tickets"]
        counts = payload["counts"]
    except httpx.HTTPError as e:
        fail(f"could not fetch final ticket list: {e!r}")
        all_tickets, counts = [], {}

    by_channel = Counter(t["channel"] for t in all_tickets)
    sensitive_total = sum(1 for t in all_tickets if t.get("sensitive"))
    tag_counts = Counter(tag for t in all_tickets for tag in t.get("tags", []))
    has_draft_total = sum(1 for t in all_tickets if t.get("has_draft"))

    outbox_count = None
    try:
        r = mail_client.get("/outbox")
        r.raise_for_status()
        outbox_count = r.json().get("count")
    except httpx.HTTPError as e:
        warn(f"could not read mailbox outbox: {e!r}")

    print()
    print("============================================================")
    print("  Seed summary")
    print("============================================================")
    print(f"  Total tickets:        {len(all_tickets)}")
    print(f"  Open / Closed / Snoozed: "
          f"{counts.get('open', '?')} / {counts.get('closed', '?')} / {counts.get('snoozed', '?')}")
    print(f"  Sensitive (flagged):  {sensitive_total} (of which {counts.get('sensitive_open', '?')} still open)")
    print(f"  By channel:           "
          f"email={by_channel.get('email', 0)}  chat={by_channel.get('chat', 0)}  "
          f"whatsapp={by_channel.get('whatsapp', 0)}")
    print(f"  Tickets with a draft: {has_draft_total}")
    if tag_counts:
        tag_str = ", ".join(f"{k}({v})" for k, v in sorted(tag_counts.items()))
        print(f"  Tags in use:          {tag_str}")
    if outbox_count is not None:
        print(f"  Mailbox outbox:       {outbox_count} message(s) sent")
    if warnings:
        print(f"  Warnings:             {len(warnings)} (see above)")
    print()
    print(f"  Open the console:   {FABLE_BASE}")
    print("============================================================")
    print()

    if fatal:
        print("Seeding finished WITH ERRORS — see 'XX' lines above.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
