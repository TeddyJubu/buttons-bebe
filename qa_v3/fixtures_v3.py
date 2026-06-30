"""
fixtures_v3.py — SHARED test fixtures for the v3 "with Shopify context" run.

Both harnesses import this so the two systems see IDENTICAL inputs:
  - QUERIES        : 30 brand-new customer queries (distinct from v1/v2)
  - MOCK_ORDERS    : a fake Shopify dataset keyed by customer email
  - patch_shopify(): monkeypatches the SHARED /root/shopify/shopify.py network
                     calls so both systems exercise their real Shopify
                     integration code path against this fixed dataset
                     (real Shopify still 403s — API scopes not granted yet).

20 of the 30 queries reference a real order in MOCK_ORDERS (so order context
matters); 10 are policy-only (no order) to test the KB. Emails not present in
MOCK_ORDERS return [] — i.e. "no linked order", a real case to handle.
"""

import sys
sys.path.insert(0, "/root")

TODAY = "2026-06-29"

# ── Mock Shopify dataset: email -> [raw order dicts] ──────────────────────────
# Raw order shape mirrors Shopify Admin REST /orders.json objects; the shared
# module's get_order_status() derives tracking/carrier from `fulfillments`.
def _ord(name, fin, ful, created, items, fulfillments=None):
    return {
        "name": name, "order_number": int(name.lstrip("#")),
        "financial_status": fin, "fulfillment_status": ful,
        "created_at": created,
        "line_items": [{"title": t, "quantity": q} for t, q in items],
        "fulfillments": fulfillments or [],
    }

def _track(num, company, url):
    return {"tracking_number": num, "tracking_company": company, "tracking_url": url}

MOCK_ORDERS = {
    # unfulfilled (still being prepared) — pre-ship changes possible
    "wheresit@example.com":   [_ord("#1001", "paid", None, "2026-06-28T09:00:00Z", [("Floral Onesie", 1)])],
    "cancelme@example.com":   [_ord("#1005", "paid", None, "2026-06-29T08:00:00Z", [("Rainbow Knit Set", 1)])],
    "sizechange@example.com": [_ord("#1006", "paid", None, "2026-06-28T18:00:00Z", [("Ribbed Romper 6-12m", 1)])],
    "addr@example.com":       [_ord("#1007", "paid", None, "2026-06-29T07:30:00Z", [("Quilted Jacket", 1)])],
    "wrongsize@example.com":  [_ord("#1008", "paid", None, "2026-06-28T20:00:00Z", [("Tutu Dress 2T", 1)])],
    "additem@example.com":    [_ord("#1009", "paid", None, "2026-06-29T06:00:00Z", [("Bear Hat", 1)])],
    "onhold@example.com":     [_ord("#1010", "paid", None, "2026-06-22T10:00:00Z", [("Linen Bloomers", 1)])],
    "expedite@example.com":   [_ord("#1011", "paid", None, "2026-06-29T05:00:00Z", [("Smocked Dress 3T", 1)])],
    "cancelitem@example.com": [_ord("#1012", "paid", None, "2026-06-28T22:00:00Z", [("Knit Cardigan", 1), ("Matching Booties", 1)])],

    # fulfilled + tracking (shipped) — tracking should surface
    "shipped@example.com":    [_ord("#1002", "paid", "fulfilled", "2026-06-24T09:00:00Z", [("Stripe Pajama Set", 1)],
                                    [_track("1Z999AA10123456784", "UPS", "https://www.ups.com/track?tracknum=1Z999AA10123456784")])],
    "tracknum@example.com":   [_ord("#1003", "paid", "fulfilled", "2026-06-25T11:00:00Z", [("Velour Sleeper", 2)],
                                    [_track("9400110200881234567890", "USPS", "https://tools.usps.com/go/TrackConfirmAction?tLabels=9400110200881234567890")])],
    "delivered@example.com":  [_ord("#1004", "paid", "fulfilled", "2026-06-20T10:00:00Z", [("Muslin Swaddle 3pk", 1)],
                                    [_track("772233445566", "FedEx", "https://www.fedex.com/fedextrack/?trknbr=772233445566")])],
    "notgot@example.com":     [_ord("#1013", "paid", "fulfilled", "2026-06-23T14:00:00Z", [("Pointelle Gown", 1)],
                                    [_track("1Z999AA10198765432", "UPS", "https://www.ups.com/track?tracknum=1Z999AA10198765432")])],
    "exchship@example.com":   [_ord("#1014", "paid", "fulfilled", "2026-06-26T16:00:00Z", [("Corduroy Overalls 18m", 1)],
                                    [_track("9400110200887654321098", "USPS", "https://tools.usps.com/go/TrackConfirmAction?tLabels=9400110200887654321098")])],

    # partially fulfilled — one item shipped, one not
    "partial@example.com":    [_ord("#1015", "paid", "partial", "2026-06-25T09:00:00Z",
                                    [("Floral Romper", 1), ("Sun Hat", 1)],
                                    [_track("1Z999AA10111222333", "UPS", "https://www.ups.com/track?tracknum=1Z999AA10111222333")])],

    # refunded
    "whyrefund@example.com":  [_ord("#1016", "refunded", "fulfilled", "2026-06-18T09:00:00Z", [("Knit Booties", 1)])],
    "refundstatus@example.com":[_ord("#1017", "refunded", None, "2026-06-21T09:00:00Z", [("Pom Beanie", 1)])],

    # multiple orders for one email
    "twoorders@example.com":  [_ord("#1018", "paid", "fulfilled", "2026-06-24T09:00:00Z", [("Ruffle Leggings", 1)],
                                    [_track("1Z999AA10144455566", "UPS", "https://www.ups.com/track?tracknum=1Z999AA10144455566")]),
                               _ord("#1019", "paid", None, "2026-06-28T09:00:00Z", [("Bow Headband", 2)])],

    # "what did I order" — list items
    "whatdidiorder@example.com":[_ord("#1020", "paid", None, "2026-06-27T09:00:00Z",
                                    [("Gingham Dress 4T", 1), ("Lace Socks", 2), ("Cardigan", 1)])],
}

def patch_shopify():
    """Monkeypatch the shared module's network calls to read MOCK_ORDERS.

    Returns the patched module. Call once per harness before any lookup.
    """
    from shopify import shopify

    def _by_email(email):
        return [dict(o) for o in MOCK_ORDERS.get((email or "").strip().lower(), [])]

    def _by_number(num):
        n = str(num).lstrip("#").strip()
        for orders in MOCK_ORDERS.values():
            for o in orders:
                if str(o["order_number"]) == n:
                    return dict(o)
        return None

    shopify.get_orders_by_email = _by_email
    shopify.get_order_by_number = _by_number
    return shopify


# ── 30 NEW queries (v3) — distinct from v1/v2 ────────────────────────────────
# Each: id, label, subject, message, email. order=True means it references a
# customer with orders in MOCK_ORDERS (Shopify context should be used).
QUERIES = [
    # ── Order-context dependent (Q01–Q20) ────────────────────────────────────
    {"id": "Q01", "label": "Where is my order (unfulfilled)", "order": True,
     "email": "wheresit@example.com", "subject": "Where is my order?",
     "message": "Hi, I placed an order a couple days ago and haven't heard anything. Where is it / when will it ship?"},
    {"id": "Q02", "label": "Has my order shipped (fulfilled+tracking)", "order": True,
     "email": "shipped@example.com", "subject": "Has my order shipped yet?",
     "message": "Can you tell me if my order has shipped, and if so the tracking?"},
    {"id": "Q03", "label": "Tracking number request", "order": True,
     "email": "tracknum@example.com", "subject": "Tracking number please",
     "message": "Could you send me the tracking number for my recent order? I want to follow it."},
    {"id": "Q04", "label": "Is it delivered yet", "order": True,
     "email": "delivered@example.com", "subject": "Did my order arrive?",
     "message": "I think my order should be here by now — has it been delivered?"},
    {"id": "Q05", "label": "Cancel before ship (unfulfilled)", "order": True,
     "email": "cancelme@example.com", "subject": "Please cancel my order",
     "message": "I just placed an order this morning but changed my mind — can you cancel it before it ships?"},
    {"id": "Q06", "label": "Change size before ship", "order": True,
     "email": "sizechange@example.com", "subject": "Can I change the size?",
     "message": "I ordered the ribbed romper in 6-12m but I need 12-18m instead. Can you change it before it goes out?"},
    {"id": "Q07", "label": "Return eligibility (shipped order)", "order": True,
     "email": "delivered@example.com", "subject": "Can I return this?",
     "message": "I'd like to return the swaddle set from my last order. Is it still within the return window and how do I start?"},
    {"id": "Q08", "label": "Why was I refunded", "order": True,
     "email": "whyrefund@example.com", "subject": "Why did I get a refund?",
     "message": "I just saw a refund on my card from you but I didn't ask for one. Can you tell me what it was for?"},
    {"id": "Q09", "label": "Did both my orders ship (multi-order)", "order": True,
     "email": "twoorders@example.com", "subject": "Status of my two orders",
     "message": "I have two recent orders with you. Did they both ship? I only got one tracking email."},
    {"id": "Q10", "label": "Delivered but not received", "order": True,
     "email": "notgot@example.com", "subject": "Says delivered but nothing here",
     "message": "My tracking says delivered but I never got the package. What do I do?"},
    {"id": "Q11", "label": "Change shipping address", "order": True,
     "email": "addr@example.com", "subject": "Need to fix my address",
     "message": "I just realised I used my old address on my order. Can you update it before it ships?"},
    {"id": "Q12", "label": "Ordered wrong item, swap", "order": True,
     "email": "wrongsize@example.com", "subject": "Wrong size ordered",
     "message": "I ordered the tutu dress in 2T but meant 3T. Can you swap it before shipping?"},
    {"id": "Q13", "label": "Add item to existing order", "order": True,
     "email": "additem@example.com", "subject": "Add to my order",
     "message": "Can I add a matching pair of booties to my order so it ships together and I don't pay shipping twice?"},
    {"id": "Q14", "label": "Order seems stuck (older unfulfilled)", "order": True,
     "email": "onhold@example.com", "subject": "Is my order on hold?",
     "message": "I ordered the linen bloomers about a week ago and it still hasn't shipped. Is something wrong / is it on hold?"},
    {"id": "Q15", "label": "Partial shipment — missing item", "order": True,
     "email": "partial@example.com", "subject": "Only got part of my order",
     "message": "My order arrived but the sun hat wasn't in the box — only the romper. Where's the rest?"},
    {"id": "Q16", "label": "What did I order again", "order": True,
     "email": "whatdidiorder@example.com", "subject": "What was in my order?",
     "message": "I forgot what I ordered — can you remind me what's in my most recent order?"},
    {"id": "Q17", "label": "Refund status (returned item)", "order": True,
     "email": "refundstatus@example.com", "subject": "Where is my refund?",
     "message": "I sent back an item — has my refund gone through yet? I see the order but want to confirm the money."},
    {"id": "Q18", "label": "Expedite — need by Friday", "order": True,
     "email": "expedite@example.com", "subject": "Need it by Friday",
     "message": "I just ordered the smocked dress — is there any way to rush it so it arrives by Friday?"},
    {"id": "Q19", "label": "Did my exchange ship", "order": True,
     "email": "exchship@example.com", "subject": "Exchange shipped?",
     "message": "I did an exchange for the corduroy overalls — has the replacement shipped yet?"},
    {"id": "Q20", "label": "Cancel one item from order", "order": True,
     "email": "cancelitem@example.com", "subject": "Remove one item",
     "message": "Can you remove just the matching booties from my order and keep the cardigan? It hasn't shipped yet."},

    # ── Policy-only (Q21–Q30): no order needed, tests the KB ──────────────────
    {"id": "Q21", "label": "Do items run small (sizing)", "order": False,
     "email": "sizingq@example.com", "subject": "Sizing question",
     "message": "Do your clothes run small? Trying to decide whether to size up for a 9-month-old."},
    {"id": "Q22", "label": "Gift wrapping cost/eligibility", "order": False,
     "email": "giftq@example.com", "subject": "Gift wrapping?",
     "message": "Do you offer gift wrapping, and is there a charge for it?"},
    {"id": "Q23", "label": "International shipping to Israel", "order": False,
     "email": "intlq@example.com", "subject": "Ship to Israel?",
     "message": "Do you ship internationally? I'm in Israel and would love to order."},
    {"id": "Q24", "label": "Restocking fee", "order": False,
     "email": "feeq@example.com", "subject": "Is there a restocking fee?",
     "message": "If I return something, is there a restocking fee, and how much?"},
    {"id": "Q25", "label": "Final sale threshold", "order": False,
     "email": "finalq@example.com", "subject": "Is my discounted item returnable?",
     "message": "If I buy something on sale, can I still return it? At what discount does it become final sale?"},
    {"id": "Q26", "label": "Care / machine washable", "order": False,
     "email": "careq@example.com", "subject": "Washing instructions",
     "message": "Are your onesies machine washable or do they need hand washing?"},
    {"id": "Q27", "label": "Promo code stacking", "order": False,
     "email": "promoq@example.com", "subject": "Two codes?",
     "message": "Can I use two discount codes on the same order?"},
    {"id": "Q28", "label": "Warehouse pickup hours", "order": False,
     "email": "pickupq@example.com", "subject": "Pickup hours",
     "message": "Can I pick up my order in person, and what are your warehouse hours?"},
    {"id": "Q29", "label": "What is package protection", "order": False,
     "email": "ppq@example.com", "subject": "Package protection?",
     "message": "I saw a package protection option at checkout — what does it actually cover?"},
    {"id": "Q30", "label": "First-time customer discount", "order": False,
     "email": "firstq@example.com", "subject": "New customer discount?",
     "message": "I'm a first-time customer — do you have a welcome or first-order discount code?"},
]
