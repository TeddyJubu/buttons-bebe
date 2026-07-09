"""Deterministic seed-data generator for the Shopify emulator.

Produces seed/products.json, seed/customers.json, seed/orders.json with a
realistic Buttons Bebe (baby/kids clothing) catalog, customers and a mix of
order statuses. Run once:  python3 generate_seed.py

The emulator (app.py) loads the generated JSON at boot and on /emulator/reset.
Everything here is deterministic (fixed RNG seed) so tests can rely on it.
"""
import json
import pathlib
import random
from datetime import datetime, timedelta, timezone

SEED_DIR = pathlib.Path(__file__).resolve().parent
TZ = timezone(timedelta(hours=-4))  # store timezone (America/New_York-ish)
NOW = datetime(2026, 7, 10, 15, 30, 0, tzinfo=TZ)

rng = random.Random(42)

# ---------------------------------------------------------------- products ---
# (title, product_type, base_price)  — 30 items, $12-$68
PRODUCT_DEFS = [
    ("Classic Cotton Onesie", "Onesie", 18.00),
    ("Ruffle Trim Bodysuit", "Bodysuit", 22.00),
    ("Organic Knit Romper", "Romper", 34.00),
    ("Pointelle Sleeper Gown", "Sleeper", 26.00),
    ("Quilted Bubble Romper", "Romper", 38.00),
    ("Waffle Knit Two-Piece Set", "Set", 42.00),
    ("Floral Wrap Dress", "Dress", 36.00),
    ("Corduroy Overall", "Overall", 44.00),
    ("Chunky Knit Cardigan", "Cardigan", 48.00),
    ("Fleece Zip Footie", "Footie", 32.00),
    ("Muslin Kimono Top", "Top", 20.00),
    ("Ribbed Legging Set", "Set", 28.00),
    ("Scalloped Sun Hat", "Hat", 16.00),
    ("Pom Pom Beanie", "Hat", 14.00),
    ("Bear Knit Booties", "Booties", 12.00),
    ("Linen Bloomers", "Bloomers", 24.00),
    ("Smocked Party Dress", "Dress", 52.00),
    ("Terry Cloth Romper", "Romper", 30.00),
    ("Striped Long Sleeve Tee", "Tee", 15.00),
    ("Denim Pinafore", "Pinafore", 46.00),
    ("Cable Knit Sweater", "Sweater", 54.00),
    ("Velour Tracksuit Set", "Set", 58.00),
    ("Gingham Button Blouse", "Blouse", 26.00),
    ("Cozy Sherpa Jacket", "Jacket", 62.00),
    ("Tulle Tutu Skirt", "Skirt", 34.00),
    ("Bamboo Footed Pajama", "Pajama", 36.00),
    ("Embroidered Cardigan Set", "Set", 66.00),
    ("Knotted Baby Gown", "Gown", 22.00),
    ("Suede Soft-Sole Shoes", "Shoes", 28.00),
    ("Puff Sleeve Knit Dress", "Dress", 68.00),
]

SIZES = ["0-3M", "3-6M", "6-12M", "12-18M"]
COLORS_BLURB = [
    "Buttery-soft, breathable fabric your little one will live in.",
    "Designed for wiggly babies — easy snaps and stretchy seams.",
    "A Buttons Bebe wardrobe staple, made to be handed down.",
    "Gentle on delicate skin, machine-washable, endlessly cute.",
]

PRODUCT_ID_BASE = 8_000_000_000_000
VARIANT_ID_BASE = 8_100_000_000_000
IMAGE_ID_BASE = 8_200_000_000_000
OPTION_ID_BASE = 8_300_000_000_000
INV_ITEM_BASE = 8_400_000_000_000


def slug(s):
    out = []
    for ch in s.lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in " -_":
            out.append("-")
    return "-".join(filter(None, "".join(out).split("-")))


def type_abbr(t):
    return (t[:3].upper())


def iso(dt):
    return dt.isoformat()


def gen_products():
    products = []
    for i, (title, ptype, price) in enumerate(PRODUCT_DEFS):
        pid = PRODUCT_ID_BASE + i + 1
        handle = slug(title)
        created = NOW - timedelta(days=rng.randint(120, 400))
        updated = created + timedelta(days=rng.randint(1, 60))
        abbr = type_abbr(ptype)
        variants = []
        for si, size in enumerate(SIZES):
            vid = VARIANT_ID_BASE + (i + 1) * 10 + si
            inv = rng.choice([0, 3, 8, 12, 15, 20, 25, 30, 40])
            variants.append({
                "id": vid,
                "product_id": pid,
                "title": size,
                "price": f"{price:.2f}",
                "sku": f"BB-{abbr}-{i+1:03d}-{size.replace('-', '')}",
                "position": si + 1,
                "inventory_policy": "deny",
                "compare_at_price": None,
                "fulfillment_service": "manual",
                "inventory_management": "shopify",
                "option1": size,
                "option2": None,
                "option3": None,
                "created_at": iso(created),
                "updated_at": iso(updated),
                "taxable": True,
                "barcode": f"00{pid}{si}",
                "grams": 200,
                "weight": 0.2,
                "weight_unit": "kg",
                "inventory_item_id": INV_ITEM_BASE + (i + 1) * 10 + si,
                "inventory_quantity": inv,
                "old_inventory_quantity": inv,
                "requires_shipping": True,
                "available": inv > 0,
                "admin_graphql_api_id": f"gid://shopify/ProductVariant/{vid}",
            })
        image_id = IMAGE_ID_BASE + i + 1
        image = {
            "id": image_id,
            "product_id": pid,
            "position": 1,
            "alt": title,
            "width": 1000,
            "height": 1000,
            "src": f"https://cdn.shopify.com/s/files/1/0001/products/{handle}.jpg",
            "variant_ids": [],
            "admin_graphql_api_id": f"gid://shopify/ProductImage/{image_id}",
        }
        opt_id = OPTION_ID_BASE + i + 1
        products.append({
            "id": pid,
            "title": title,
            "body_html": f"<p>{rng.choice(COLORS_BLURB)}</p>",
            "vendor": "Buttons Bebe",
            "product_type": ptype,
            "handle": handle,
            "status": "active",
            "published_scope": "web",
            "tags": ", ".join(["baby", slug(ptype), "buttons-bebe"]),
            "total_inventory": sum(v["inventory_quantity"] for v in variants),
            "online_store_url": f"https://buttons-bebe.myshopify.com/products/{handle}",
            "created_at": iso(created),
            "updated_at": iso(updated),
            "published_at": iso(created),
            "template_suffix": None,
            "options": [{
                "id": opt_id,
                "product_id": pid,
                "name": "Size",
                "position": 1,
                "values": SIZES,
            }],
            "images": [image],
            "image": image,
            "variants": variants,
            "admin_graphql_api_id": f"gid://shopify/Product/{pid}",
        })
    return products


# --------------------------------------------------------------- customers ---
CUSTOMER_DEFS = [
    ("Emma", "Wilson", "emma.wilson@example.com", "Portland", "Oregon", "OR", "97201"),
    ("Sophie", "Martin", "sophie.martin@example.com", "Austin", "Texas", "TX", "78701"),
    ("Lucas", "Brown", "lucas.brown@example.com", "Denver", "Colorado", "CO", "80202"),
    ("Olivia", "Garcia", "olivia.garcia@example.com", "Miami", "Florida", "FL", "33101"),
    ("Noah", "Davis", "noah.davis@example.com", "Seattle", "Washington", "WA", "98101"),
    ("Ava", "Rodriguez", "ava.rodriguez@example.com", "Phoenix", "Arizona", "AZ", "85001"),
    ("Liam", "Martinez", "liam.martinez@example.com", "Chicago", "Illinois", "IL", "60601"),
    ("Isabella", "Hernandez", "isabella.hernandez@example.com", "Boston", "Massachusetts", "MA", "02108"),
    ("Mason", "Lopez", "mason.lopez@example.com", "Nashville", "Tennessee", "TN", "37201"),
    ("Mia", "Gonzalez", "mia.gonzalez@example.com", "Columbus", "Ohio", "OH", "43201"),
    ("Ethan", "Clark", "ethan.clark@example.com", "Atlanta", "Georgia", "GA", "30301"),
    ("Charlotte", "Lewis", "charlotte.lewis@example.com", "Charlotte", "North Carolina", "NC", "28201"),
    ("James", "Walker", "james.walker@example.com", "San Diego", "California", "CA", "92101"),
    ("Amelia", "Hall", "amelia.hall@example.com", "Minneapolis", "Minnesota", "MN", "55401"),
    ("Benjamin", "Allen", "benjamin.allen@example.com", "Salt Lake City", "Utah", "UT", "84101"),
    ("Harper", "Young", "harper.young@example.com", "Raleigh", "North Carolina", "NC", "27601"),
    ("Elijah", "King", "elijah.king@example.com", "Kansas City", "Missouri", "MO", "64101"),
    ("Evelyn", "Wright", "evelyn.wright@example.com", "Sacramento", "California", "CA", "95814"),
    ("Henry", "Scott", "henry.scott@example.com", "Pittsburgh", "Pennsylvania", "PA", "15201"),
    ("Abigail", "Green", "abigail.green@example.com", "Richmond", "Virginia", "VA", "23218"),
    ("Alexander", "Baker", "alexander.baker@example.com", "Milwaukee", "Wisconsin", "WI", "53201"),
    ("Emily", "Adams", "emily.adams@example.com", "Providence", "Rhode Island", "RI", "02903"),
    ("Sebastian", "Nelson", "sebastian.nelson@example.com", "Hartford", "Connecticut", "CT", "06103"),
    ("Elizabeth", "Carter", "elizabeth.carter@example.com", "Omaha", "Nebraska", "NE", "68101"),
    ("Jack", "Mitchell", "jack.mitchell@example.com", "Boise", "Idaho", "ID", "83702"),
]

CUSTOMER_ID_BASE = 6_000_000_000_000
ADDRESS_ID_BASE = 6_100_000_000_000


def gen_customers():
    customers = []
    for i, (fn, ln, email, city, prov, pcode, zipc) in enumerate(CUSTOMER_DEFS):
        cid = CUSTOMER_ID_BASE + i + 1
        aid = ADDRESS_ID_BASE + i + 1
        phone = f"+1555{rng.randint(1000000, 9999999)}"
        created = NOW - timedelta(days=rng.randint(90, 500))
        address = {
            "id": aid,
            "customer_id": cid,
            "first_name": fn,
            "last_name": ln,
            "company": None,
            "address1": f"{rng.randint(100, 9999)} {rng.choice(['Maple', 'Oak', 'Cedar', 'Birch', 'Elm', 'Willow'])} {rng.choice(['St', 'Ave', 'Dr', 'Ln'])}",
            "address2": rng.choice([None, None, f"Apt {rng.randint(1, 40)}"]),
            "city": city,
            "province": prov,
            "country": "United States",
            "zip": zipc,
            "phone": phone,
            "name": f"{fn} {ln}",
            "province_code": pcode,
            "country_code": "US",
            "country_name": "United States",
            "default": True,
        }
        customers.append({
            "id": cid,
            "email": email,
            "first_name": fn,
            "last_name": ln,
            "phone": phone,
            "state": "enabled",
            "note": None,
            "verified_email": True,
            "tax_exempt": False,
            "tags": rng.choice(["", "", "vip", "repeat"]),
            "currency": "USD",
            "orders_count": 0,      # filled after orders generated
            "total_spent": "0.00",  # filled after orders generated
            "last_order_id": None,
            "last_order_name": None,
            "created_at": iso(created),
            "updated_at": iso(created),
            "addresses": [address],
            "default_address": address,
            "admin_graphql_api_id": f"gid://shopify/Customer/{cid}",
        })
    return customers


# ------------------------------------------------------------------ orders ---
ORDER_ID_BASE = 5_000_000_000_000
LINEITEM_ID_BASE = 5_100_000_000_000
FULFILLMENT_ID_BASE = 5_200_000_000_000
REFUND_ID_BASE = 5_300_000_000_000

# status label per order index 0..39 (order_number 1001..1040)
STATUS_PLAN = (
    ["fulfilled"] * 19          # idx 0-18
    + ["partial", "partial"]    # idx 19-20
    + ["delivered"]             # idx 21  -> #BB1022 (Sophie)
    + ["partial", "partial"]    # idx 22-23
    + ["unfulfilled"] * 8       # idx 24-31 (incl. #BB1031 Lucas at idx 30)
    + ["refunded"] * 4          # idx 32-35
    + ["pending", "pending"]    # idx 36-37
    + ["cancelled", "cancelled"]  # idx 38-39
)

# specific customer assignments by order_number
FIXED_CUSTOMER = {
    1015: "emma.wilson@example.com",   # fulfilled + specific tracking
    1022: "sophie.martin@example.com",  # delivered
    1031: "lucas.brown@example.com",    # unfulfilled
}


def make_tracking():
    return "1Z999AA1" + "".join(str(rng.randint(0, 9)) for _ in range(9))


def gen_orders(products, customers):
    by_email = {c["email"]: c for c in customers}
    cust_cycle = [c for c in customers]
    orders = []
    for idx, label in enumerate(STATUS_PLAN):
        onum = 1001 + idx
        oid = ORDER_ID_BASE + onum
        # choose customer
        if onum in FIXED_CUSTOMER:
            cust = by_email[FIXED_CUSTOMER[onum]]
        else:
            cust = cust_cycle[(idx * 7 + 3) % len(cust_cycle)]
        created = NOW - timedelta(days=rng.randint(0, 60), hours=rng.randint(0, 23),
                                  minutes=rng.randint(0, 59))
        updated = created + timedelta(hours=rng.randint(1, 72))

        # line items: 1-3 products
        n_items = rng.randint(1, 3)
        chosen = rng.sample(products, n_items)
        line_items = []
        subtotal = 0.0
        for li_i, p in enumerate(chosen):
            v = rng.choice(p["variants"])
            qty = rng.randint(1, 2)
            price = float(v["price"])
            subtotal += price * qty
            liid = LINEITEM_ID_BASE + onum * 10 + li_i
            line_items.append({
                "id": liid,
                "admin_graphql_api_id": f"gid://shopify/LineItem/{liid}",
                "product_id": p["id"],
                "variant_id": v["id"],
                "title": p["title"],
                "variant_title": v["title"],
                "name": f"{p['title']} - {v['title']}",
                "sku": v["sku"],
                "vendor": p["vendor"],
                "quantity": qty,
                "fulfillable_quantity": qty,
                "price": f"{price:.2f}",
                "price_set": {
                    "shop_money": {"amount": f"{price:.2f}", "currency_code": "USD"},
                    "presentment_money": {"amount": f"{price:.2f}", "currency_code": "USD"},
                },
                "total_discount": "0.00",
                "fulfillment_status": None,
                "requires_shipping": True,
                "taxable": True,
                "gift_card": False,
                "product_exists": True,
            })
        tax = round(subtotal * 0.0, 2)
        total = round(subtotal + tax, 2)

        addr = dict(cust["default_address"])
        addr = {k: addr.get(k) for k in (
            "first_name", "last_name", "company", "address1", "address2", "city",
            "province", "country", "zip", "phone", "name", "province_code",
            "country_code", "country_name", "latitude", "longitude")}

        order = {
            "id": oid,
            "admin_graphql_api_id": f"gid://shopify/Order/{oid}",
            "name": f"#BB{onum}",
            "order_number": onum,
            "number": onum - 1000,
            "email": cust["email"],
            "contact_email": cust["email"],
            "phone": cust["phone"],
            "created_at": iso(created),
            "updated_at": iso(updated),
            "processed_at": iso(created),
            "closed_at": None,
            "cancelled_at": None,
            "cancel_reason": None,
            "currency": "USD",
            "presentment_currency": "USD",
            "total_price": f"{total:.2f}",
            "subtotal_price": f"{subtotal:.2f}",
            "total_tax": f"{tax:.2f}",
            "total_discounts": "0.00",
            "total_line_items_price": f"{subtotal:.2f}",
            "total_shipping_price_set": {
                "shop_money": {"amount": "0.00", "currency_code": "USD"},
                "presentment_money": {"amount": "0.00", "currency_code": "USD"},
            },
            "total_price_set": {
                "shop_money": {"amount": f"{total:.2f}", "currency_code": "USD"},
                "presentment_money": {"amount": f"{total:.2f}", "currency_code": "USD"},
            },
            "subtotal_price_set": {
                "shop_money": {"amount": f"{subtotal:.2f}", "currency_code": "USD"},
                "presentment_money": {"amount": f"{subtotal:.2f}", "currency_code": "USD"},
            },
            "total_tax_set": {
                "shop_money": {"amount": f"{tax:.2f}", "currency_code": "USD"},
                "presentment_money": {"amount": f"{tax:.2f}", "currency_code": "USD"},
            },
            "financial_status": "paid",
            "fulfillment_status": None,
            "confirmed": True,
            "test": False,
            "tags": "",
            "note": None,
            "customer": {
                "id": cust["id"],
                "first_name": cust["first_name"],
                "last_name": cust["last_name"],
                "email": cust["email"],
                "admin_graphql_api_id": cust["admin_graphql_api_id"],
            },
            "billing_address": addr,
            "shipping_address": addr,
            "line_items": line_items,
            "fulfillments": [],
            "refunds": [],
        }

        def add_fulfillment(status_items, tracking=None, tracking_url=None,
                            shipment_status=None):
            fid = FULFILLMENT_ID_BASE + onum
            tn = tracking or make_tracking()
            turl = tracking_url or f"https://www.ups.com/track?loc=en_US&tracknum={tn}"
            fdt = created + timedelta(days=rng.randint(1, 3))
            f = {
                "id": fid,
                "admin_graphql_api_id": f"gid://shopify/Fulfillment/{fid}",
                "order_id": oid,
                "status": "success",
                "shipment_status": shipment_status,
                "created_at": iso(fdt),
                "updated_at": iso(fdt),
                "tracking_company": "UPS",
                "tracking_number": tn,
                "tracking_numbers": [tn],
                "tracking_url": turl,
                "tracking_urls": [turl],
                "line_items": status_items,
                "location_id": 70000000001,
                "name": f"{order['name']}.1",
                "service": "UPS Ground",
            }
            return f

        if label == "fulfilled" or label == "delivered":
            order["fulfillment_status"] = "fulfilled"
            for li in line_items:
                li["fulfillment_status"] = "fulfilled"
                li["fulfillable_quantity"] = 0
            if onum == 1015:
                tn = "1Z999AA10123456784"
                turl = f"https://www.ups.com/track?loc=en_US&tracknum={tn}"
                order["fulfillments"].append(add_fulfillment(
                    [dict(li) for li in line_items], tracking=tn, tracking_url=turl,
                    shipment_status="in_transit"))
            else:
                order["fulfillments"].append(add_fulfillment(
                    [dict(li) for li in line_items],
                    shipment_status="delivered" if label == "delivered" else "in_transit"))

        elif label == "partial":
            order["fulfillment_status"] = "partial"
            # fulfill only the first line item
            first = line_items[0]
            first["fulfillment_status"] = "fulfilled"
            first["fulfillable_quantity"] = 0
            order["fulfillments"].append(add_fulfillment(
                [dict(first)], shipment_status="in_transit"))

        elif label == "unfulfilled":
            order["fulfillment_status"] = None  # unfulfilled

        elif label == "refunded":
            order["financial_status"] = "refunded"
            order["fulfillment_status"] = rng.choice(["fulfilled", None])
            if order["fulfillment_status"] == "fulfilled":
                for li in line_items:
                    li["fulfillment_status"] = "fulfilled"
                order["fulfillments"].append(add_fulfillment(
                    [dict(li) for li in line_items], shipment_status="delivered"))
            rid = REFUND_ID_BASE + onum
            order["refunds"].append({
                "id": rid,
                "admin_graphql_api_id": f"gid://shopify/Refund/{rid}",
                "order_id": oid,
                "created_at": iso(updated),
                "processed_at": iso(updated),
                "note": "Customer refund",
                "restock": True,
                "transactions": [{
                    "amount": f"{total:.2f}",
                    "kind": "refund",
                    "status": "success",
                    "gateway": "manual",
                }],
                "refund_line_items": [],
            })

        elif label == "pending":
            order["financial_status"] = "pending"
            order["fulfillment_status"] = None

        elif label == "cancelled":
            order["financial_status"] = rng.choice(["refunded", "voided"])
            order["fulfillment_status"] = None
            order["cancelled_at"] = iso(updated)
            order["cancel_reason"] = rng.choice(["customer", "inventory", "declined"])
            order["closed_at"] = iso(updated)

        orders.append(order)

    # roll up customer order stats
    for c in customers:
        c_orders = [o for o in orders if o["email"] == c["email"] and not o["cancelled_at"]]
        c["orders_count"] = len(c_orders)
        c["total_spent"] = f"{sum(float(o['total_price']) for o in c_orders):.2f}"
        if c_orders:
            last = max(c_orders, key=lambda o: o["created_at"])
            c["last_order_id"] = last["id"]
            c["last_order_name"] = last["name"]
    return orders


def main():
    products = gen_products()
    customers = gen_customers()
    orders = gen_orders(products, customers)
    (SEED_DIR / "products.json").write_text(json.dumps(products, indent=2))
    (SEED_DIR / "customers.json").write_text(json.dumps(customers, indent=2))
    (SEED_DIR / "orders.json").write_text(json.dumps(orders, indent=2))
    print(f"wrote {len(products)} products, {len(customers)} customers, {len(orders)} orders")
    # quick sanity
    bb1015 = next(o for o in orders if o["name"] == "#BB1015")
    assert bb1015["email"] == "emma.wilson@example.com"
    assert bb1015["fulfillments"][0]["tracking_number"] == "1Z999AA10123456784"
    print("sanity: #BB1015 ->", bb1015["email"], bb1015["fulfillments"][0]["tracking_number"])


if __name__ == "__main__":
    main()
