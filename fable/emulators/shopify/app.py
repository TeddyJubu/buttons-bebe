"""Shopify Admin API emulator (Fable) — port 9601.

Drop-in-compatible with the Shopify Admin API for the paths Fable's code touches:
  * OAuth client-credentials token grant (24h expiry, exact 401 body)
  * REST: orders / customers / products (filters, page_info + Link header)
  * GraphQL: the products query from kb/scripts/sync_products.py (incl. the Bulk
    Operations flow), plus a basic orders query, with extensions.cost.throttleStatus
  * Leaky-bucket rate limit (cap 40, leak 2/s) -> X-Shopify-Shop-Api-Call-Limit + 429
  * X-Emulator-Scenario header (rate-limit / server-error / slow)
  * /emulator/* test controls

Only stdlib + fastapi/uvicorn. Binds 127.0.0.1. Nothing leaves localhost.
"""
import base64
import json
import math
import os
import pathlib
import re
import time
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

SEED_DIR = pathlib.Path(__file__).resolve().parent / "seed"

SHOP = os.environ.get("SHOPIFY_SHOP", "buttons-bebe.myshopify.com")
CLIENT_ID = os.environ.get("SHOPIFY_CLIENT_ID", "test-client-id")
CLIENT_SECRET = os.environ.get("SHOPIFY_CLIENT_SECRET", "test-client-secret")
SELF_BASE = os.environ.get("SHOPIFY_BASE", "http://127.0.0.1:9601")

VER_RE = re.compile(r"^20\d\d-\d\d$")

# exact Shopify error bodies -------------------------------------------------
ERR_401 = {"errors": "[API] Invalid API key or access token (unrecognized login or wrong password)"}
ERR_404 = {"errors": "Not Found"}
ERR_429 = {"errors": "Exceeded 2 calls per second for api client. Reduce request rates to resume uninterrupted service."}
ERR_500 = {"errors": "Internal Server Error"}

app = FastAPI(title="Fable Shopify Emulator")

# ----------------------------------------------------------------- state ----
STATE = {
    "products": [],
    "customers": [],
    "orders": [],
    "tokens": {},   # token -> expiry epoch
    "buckets": {},  # token -> {"level": float, "ts": float}
}


def _load_seed():
    STATE["products"] = json.loads((SEED_DIR / "products.json").read_text())
    STATE["customers"] = json.loads((SEED_DIR / "customers.json").read_text())
    STATE["orders"] = json.loads((SEED_DIR / "orders.json").read_text())


_load_seed()


def now_iso():
    return datetime.now(timezone(timedelta(hours=-4))).isoformat()


# ---------------------------------------------------------- page_info util --
def encode_page_info(resource, filters, since_id, limit):
    payload = {"r": resource, "f": filters, "since_id": since_id, "limit": limit}
    raw = json.dumps(payload).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_page_info(token):
    pad = "=" * (-len(token) % 4)
    raw = base64.urlsafe_b64decode((token + pad).encode())
    return json.loads(raw)


# ---------------------------------------------------------------- auth -------
def valid_token(tok):
    exp = STATE["tokens"].get(tok)
    return bool(exp) and exp > time.time()


def bucket_take(tok):
    """Leaky bucket: cap 40, leak 2/s. Returns (ok, header_str)."""
    b = STATE["buckets"].setdefault(tok, {"level": 0.0, "ts": time.time()})
    t = time.time()
    b["level"] = max(0.0, b["level"] - (t - b["ts"]) * 2.0)
    b["ts"] = t
    if b["level"] + 1.0 > 40.0:
        used = min(40, int(math.ceil(b["level"])))
        return False, f"{used}/40"
    b["level"] += 1.0
    used = min(40, int(math.ceil(b["level"])))
    return True, f"{used}/40"


def resp_401():
    return JSONResponse(ERR_401, status_code=401)


def resp_429():
    return JSONResponse(ERR_429, status_code=429, headers={"Retry-After": "2.0"})


def resp_404():
    return JSONResponse(ERR_404, status_code=404)


def guard(request: Request, rest: bool = True):
    """Returns (short_circuit_response_or_None, call_limit_header_or_None)."""
    scen = request.headers.get("X-Emulator-Scenario", "").strip().lower()
    if scen == "server-error":
        return JSONResponse(ERR_500, status_code=500), None
    if scen == "slow":
        time.sleep(5)
    tok = request.headers.get("X-Shopify-Access-Token", "")
    if not valid_token(tok):
        return resp_401(), None
    if scen == "rate-limit":
        return resp_429(), None
    if not rest:
        return None, None
    ok, hdr = bucket_take(tok)
    if not ok:
        return resp_429(), None
    return None, hdr


def rest_headers(call_limit):
    return {"X-Shopify-Shop-Api-Call-Limit": call_limit} if call_limit else {}


# ------------------------------------------------------------ paginate ------
def paginate(items, resource, filters, page_info, limit, ver):
    """Generic since_id pagination + Link header. items already filtered+sorted asc by id."""
    since_id = 0
    if page_info:
        try:
            info = decode_page_info(page_info)
            since_id = info.get("since_id", 0)
            filters = info.get("f", filters)
            limit = info.get("limit", limit)
        except Exception:
            since_id = 0
    limit = max(1, min(int(limit or 50), 250))
    window = [it for it in items if it["id"] > since_id]
    page = window[:limit]
    headers = {}
    links = []
    if len(window) > limit and page:
        nxt = encode_page_info(resource, filters, page[-1]["id"], limit)
        links.append(f'<{SELF_BASE}/admin/api/{ver}/{resource}.json?limit={limit}&page_info={nxt}>; rel="next"')
    if since_id and page:
        # previous page cursor points at ids before the first shown id
        prev_since = 0  # simplistic: previous returns from the start
        prv = encode_page_info(resource, filters, prev_since, limit)
        links.append(f'<{SELF_BASE}/admin/api/{ver}/{resource}.json?limit={limit}&page_info={prv}>; rel="previous"')
    if links:
        headers["Link"] = ", ".join(links)
    return page, headers


# =============================================================== OAuth =======
@app.post("/admin/oauth/access_token")
async def oauth_token(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    cid = body.get("client_id")
    sec = body.get("client_secret")
    grant = body.get("grant_type")
    if grant != "client_credentials" or cid != CLIENT_ID or sec != CLIENT_SECRET:
        return resp_401()
    tok = uuid.uuid4().hex
    STATE["tokens"][tok] = time.time() + 86399
    return JSONResponse({
        "access_token": tok,
        "scope": "read_orders,read_customers,read_products",
        "expires_in": 86399,
    })


# =============================================================== REST ========
def _ver_ok(ver):
    return bool(VER_RE.match(ver))


@app.get("/admin/api/{ver}/orders.json")
async def rest_orders(ver: str, request: Request):
    if not _ver_ok(ver):
        return resp_404()
    sc, cl = guard(request)
    if sc:
        return sc
    q = request.query_params
    page_info = q.get("page_info")
    limit = q.get("limit", 50)
    filters = {
        "email": q.get("email"),
        "name": q.get("name"),
        "status": q.get("status", "open"),
        "financial_status": q.get("financial_status"),
        "fulfillment_status": q.get("fulfillment_status"),
        "created_at_min": q.get("created_at_min"),
        "created_at_max": q.get("created_at_max"),
        "ids": q.get("ids"),
    }
    orders = sorted(STATE["orders"], key=lambda o: o["id"])
    if not page_info:
        orders = _filter_orders(orders, filters)
    else:
        # filters restored inside paginate from cursor
        info = None
        try:
            info = decode_page_info(page_info)
        except Exception:
            info = None
        if info:
            orders = _filter_orders(orders, info.get("f", filters))
    page, headers = paginate(orders, "orders", filters, page_info, limit, ver)
    headers.update(rest_headers(cl))
    return JSONResponse({"orders": page}, headers=headers)


def _filter_orders(orders, f):
    out = []
    for o in orders:
        if f.get("email") and o["email"].lower() != f["email"].lower():
            continue
        if f.get("name"):
            want = f["name"].lstrip("#").lower()
            have = o["name"].lstrip("#").lower()
            if want not in (have, have.replace("bb", "")):
                continue
        status = (f.get("status") or "open").lower()
        if status == "cancelled":
            if not o.get("cancelled_at"):
                continue
        elif status == "closed":
            if not o.get("closed_at"):
                continue
        elif status == "open":
            if o.get("cancelled_at"):
                continue
        # "any" -> no status filter
        if f.get("financial_status") and f["financial_status"] not in ("any",) \
                and o.get("financial_status") != f["financial_status"]:
            continue
        if f.get("fulfillment_status"):
            fs = f["fulfillment_status"]
            if fs in ("unshipped", "unfulfilled") and o.get("fulfillment_status") is not None:
                continue
            if fs in ("shipped", "fulfilled") and o.get("fulfillment_status") != "fulfilled":
                continue
            if fs == "partial" and o.get("fulfillment_status") != "partial":
                continue
        if f.get("created_at_min") and o["created_at"] < f["created_at_min"]:
            continue
        if f.get("created_at_max") and o["created_at"] > f["created_at_max"]:
            continue
        if f.get("ids"):
            wanted = {int(x) for x in str(f["ids"]).split(",") if x.strip().isdigit()}
            if o["id"] not in wanted:
                continue
        out.append(o)
    return out


@app.get("/admin/api/{ver}/orders/{oid}.json")
async def rest_order(ver: str, oid: str, request: Request):
    if not _ver_ok(ver):
        return resp_404()
    sc, cl = guard(request)
    if sc:
        return sc
    try:
        oid_i = int(oid)
    except ValueError:
        return resp_404()
    o = next((x for x in STATE["orders"] if x["id"] == oid_i), None)
    if not o:
        return resp_404()
    return JSONResponse({"order": o}, headers=rest_headers(cl))


@app.get("/admin/api/{ver}/customers.json")
async def rest_customers(ver: str, request: Request):
    if not _ver_ok(ver):
        return resp_404()
    sc, cl = guard(request)
    if sc:
        return sc
    q = request.query_params
    page_info = q.get("page_info")
    limit = q.get("limit", 50)
    filters = {"email": q.get("email"), "ids": q.get("ids")}
    customers = sorted(STATE["customers"], key=lambda c: c["id"])
    if not page_info:
        customers = _filter_customers(customers, filters)
    else:
        try:
            info = decode_page_info(page_info)
            customers = _filter_customers(customers, info.get("f", filters))
        except Exception:
            pass
    page, headers = paginate(customers, "customers", filters, page_info, limit, ver)
    headers.update(rest_headers(cl))
    return JSONResponse({"customers": page}, headers=headers)


def _filter_customers(customers, f):
    out = []
    for c in customers:
        if f.get("email") and c["email"].lower() != f["email"].lower():
            continue
        if f.get("ids"):
            wanted = {int(x) for x in str(f["ids"]).split(",") if x.strip().isdigit()}
            if c["id"] not in wanted:
                continue
        out.append(c)
    return out


@app.get("/admin/api/{ver}/customers/search.json")
async def rest_customers_search(ver: str, request: Request):
    if not _ver_ok(ver):
        return resp_404()
    sc, cl = guard(request)
    if sc:
        return sc
    q = request.query_params
    query = q.get("query", "") or ""
    matched = _search_customers(query)
    limit = max(1, min(int(q.get("limit", 50)), 250))
    return JSONResponse({"customers": matched[:limit]}, headers=rest_headers(cl))


def _search_customers(query):
    # supports "email:x", "email:x AND ...", plus bare text
    terms = {}
    bare = []
    for part in re.split(r"\s+(?:AND|and)\s+|\s+", query.strip()):
        if not part:
            continue
        if ":" in part:
            k, v = part.split(":", 1)
            terms[k.lower()] = v.strip().strip('"')
        else:
            bare.append(part.lower())
    out = []
    for c in STATE["customers"]:
        ok = True
        if "email" in terms and c["email"].lower() != terms["email"].lower():
            ok = False
        if "first_name" in terms and c["first_name"].lower() != terms["first_name"].lower():
            ok = False
        if "last_name" in terms and c["last_name"].lower() != terms["last_name"].lower():
            ok = False
        if "phone" in terms and (c.get("phone") or "") != terms["phone"]:
            ok = False
        if bare:
            hay = f"{c['first_name']} {c['last_name']} {c['email']}".lower()
            if not all(b in hay for b in bare):
                ok = False
        if ok:
            out.append(c)
    return out


@app.get("/admin/api/{ver}/customers/{cid}.json")
async def rest_customer(ver: str, cid: str, request: Request):
    if not _ver_ok(ver):
        return resp_404()
    sc, cl = guard(request)
    if sc:
        return sc
    try:
        cid_i = int(cid)
    except ValueError:
        return resp_404()
    c = next((x for x in STATE["customers"] if x["id"] == cid_i), None)
    if not c:
        return resp_404()
    return JSONResponse({"customer": c}, headers=rest_headers(cl))


@app.get("/admin/api/{ver}/products.json")
async def rest_products(ver: str, request: Request):
    if not _ver_ok(ver):
        return resp_404()
    sc, cl = guard(request)
    if sc:
        return sc
    q = request.query_params
    page_info = q.get("page_info")
    limit = q.get("limit", 50)
    filters = {
        "status": q.get("status"),
        "handle": q.get("handle"),
        "product_type": q.get("product_type"),
        "vendor": q.get("vendor"),
        "ids": q.get("ids"),
    }
    products = sorted(STATE["products"], key=lambda p: p["id"])
    if not page_info:
        products = _filter_products(products, filters)
    else:
        try:
            info = decode_page_info(page_info)
            products = _filter_products(products, info.get("f", filters))
        except Exception:
            pass
    page, headers = paginate(products, "products", filters, page_info, limit, ver)
    headers.update(rest_headers(cl))
    return JSONResponse({"products": page}, headers=headers)


def _filter_products(products, f):
    out = []
    for p in products:
        if f.get("status") and p["status"] != f["status"]:
            continue
        if f.get("handle") and p["handle"] != f["handle"]:
            continue
        if f.get("product_type") and p["product_type"] != f["product_type"]:
            continue
        if f.get("vendor") and p["vendor"] != f["vendor"]:
            continue
        if f.get("ids"):
            wanted = {int(x) for x in str(f["ids"]).split(",") if x.strip().isdigit()}
            if p["id"] not in wanted:
                continue
        out.append(p)
    return out


# =============================================================== GraphQL =====
GQL_COST = {
    "requestedQueryCost": 12,
    "actualQueryCost": 12,
    "throttleStatus": {
        "maximumAvailable": 1000.0,
        "currentlyAvailable": 988.0,
        "restoreRate": 50.0,
    },
}


def gql_products_edges(first, after):
    prods = sorted(STATE["products"], key=lambda p: p["id"])
    since = 0
    if after:
        try:
            since = decode_page_info(after).get("since_id", 0)
        except Exception:
            since = 0
    window = [p for p in prods if p["id"] > since]
    page = window[:first]
    edges = []
    for p in page:
        cursor = encode_page_info("products", {}, p["id"], first)
        vedges = []
        for v in p["variants"]:
            vedges.append({"node": {
                "id": v["admin_graphql_api_id"],
                "title": v["title"],
                "price": v["price"],
                "sku": v["sku"],
                "availableForSale": v.get("available", v["inventory_quantity"] > 0),
                "inventoryQuantity": v["inventory_quantity"],
            }})
        node = {
            "id": p["admin_graphql_api_id"],
            "title": p["title"],
            "handle": p["handle"],
            "description": re.sub("<[^>]+>", "", p["body_html"]),
            "descriptionHtml": p["body_html"],
            "bodyHtml": p["body_html"],
            "onlineStoreUrl": p["online_store_url"],
            "productType": p["product_type"],
            "vendor": p["vendor"],
            "status": p["status"].upper(),
            "totalInventory": p["total_inventory"],
            "tags": [t.strip() for t in p["tags"].split(",") if t.strip()],
            "options": [{"name": o["name"], "values": o["values"]} for o in p["options"]],
            "variants": {"edges": vedges},
        }
        edges.append({"cursor": cursor, "node": node})
    has_next = len(window) > first
    end_cursor = edges[-1]["cursor"] if edges else None
    return edges, {"hasNextPage": has_next, "endCursor": end_cursor}


def gql_orders_edges(first, email):
    orders = sorted(STATE["orders"], key=lambda o: o["id"])
    if email:
        orders = [o for o in orders if o["email"].lower() == email.lower()]
    page = orders[:first]
    edges = []
    for o in page:
        li_edges = [{"node": {
            "title": li["title"], "quantity": li["quantity"], "sku": li["sku"],
        }} for li in o["line_items"]]
        f = o["fulfillments"][0] if o["fulfillments"] else None
        node = {
            "id": o["admin_graphql_api_id"],
            "name": o["name"],
            "email": o["email"],
            "createdAt": o["created_at"],
            "displayFinancialStatus": (o["financial_status"] or "").upper(),
            "displayFulfillmentStatus": (o["fulfillment_status"] or "UNFULFILLED").upper(),
            "totalPriceSet": {"shopMoney": {"amount": o["total_price"], "currencyCode": "USD"}},
            "fulfillments": ([{"trackingInfo": [{"number": f["tracking_number"], "url": f["tracking_url"]}]}] if f else []),
            "lineItems": {"edges": li_edges},
        }
        edges.append({"cursor": encode_page_info("orders", {}, o["id"], first), "node": node})
    return edges, {"hasNextPage": len(orders) > first, "endCursor": (edges[-1]["cursor"] if edges else None)}


def _parse_first(query, variables):
    m = re.search(r"first\s*:\s*(\d+)", query)
    if m:
        return int(m.group(1))
    if variables.get("first"):
        return int(variables["first"])
    return 50


def _parse_after(query, variables):
    m = re.search(r'after\s*:\s*"([^"]*)"', query)
    if m:
        return m.group(1)
    return variables.get("after") or variables.get("cursor")


def _parse_email_query(query, variables):
    m = re.search(r'query\s*:\s*"[^"]*email:\s*([^\s"]+)', query)
    if m:
        return m.group(1)
    return None


@app.post("/admin/api/{ver}/graphql.json")
async def graphql(ver: str, request: Request):
    if not _ver_ok(ver):
        return resp_404()
    sc, _ = guard(request, rest=False)
    if sc:
        return sc
    try:
        body = await request.json()
    except Exception:
        body = {}
    query = body.get("query", "") or ""
    variables = body.get("variables") or {}

    # ---- Bulk Operations flow (used by kb/scripts/sync_products.py) ----
    if "bulkOperationRunQuery" in query:
        STATE.setdefault("bulk", {})["status"] = "COMPLETED"
        return JSONResponse({
            "data": {"bulkOperationRunQuery": {
                "bulkOperation": {"id": "gid://shopify/BulkOperation/1", "status": "CREATED"},
                "userErrors": [],
            }},
            "extensions": {"cost": GQL_COST},
        })
    if "currentBulkOperation" in query:
        n_objs = len(STATE["products"]) + sum(len(p["variants"]) for p in STATE["products"])
        return JSONResponse({
            "data": {"currentBulkOperation": {
                "id": "gid://shopify/BulkOperation/1",
                "status": "COMPLETED",
                "errorCode": None,
                "createdAt": now_iso(),
                "completedAt": now_iso(),
                "objectCount": str(n_objs),
                "fileSize": "123456",
                "url": f"{SELF_BASE}/emulator/bulk/products.jsonl",
                "partialDataUrl": None,
            }},
            "extensions": {"cost": GQL_COST},
        })

    # ---- direct products query ----
    if re.search(r"\bproducts\s*\(", query) or re.search(r"\bproducts\b", query):
        if re.search(r"\borders\s*\(", query) and query.find("orders") < query.find("products"):
            pass  # orders comes first -> fall through to orders handler
        else:
            first = _parse_first(query, variables)
            after = _parse_after(query, variables)
            edges, page_info = gql_products_edges(first, after)
            return JSONResponse({
                "data": {"products": {"edges": edges, "pageInfo": page_info}},
                "extensions": {"cost": GQL_COST},
            })

    # ---- basic orders query ----
    if re.search(r"\borders\s*\(", query) or re.search(r"\borders\b", query):
        first = _parse_first(query, variables)
        email = _parse_email_query(query, variables)
        edges, page_info = gql_orders_edges(first, email)
        return JSONResponse({
            "data": {"orders": {"edges": edges, "pageInfo": page_info}},
            "extensions": {"cost": GQL_COST},
        })

    # ---- unknown ----
    return JSONResponse({
        "errors": [{
            "message": "Field or query not supported by emulator",
            "extensions": {"code": "undefinedField"},
        }],
        "data": None,
    })


@app.get("/emulator/bulk/products.jsonl")
async def bulk_jsonl():
    """JSONL export matching split_records() in sync_products.py (products + variant lines)."""
    lines = []
    for p in STATE["products"]:
        lines.append(json.dumps({
            "id": p["admin_graphql_api_id"],
            "title": p["title"],
            "handle": p["handle"],
            "productType": p["product_type"],
            "vendor": p["vendor"],
            "status": p["status"].upper(),
            "totalInventory": p["total_inventory"],
            "onlineStoreUrl": p["online_store_url"],
            "description": re.sub("<[^>]+>", "", p["body_html"]),
            "options": [{"name": o["name"], "values": o["values"]} for o in p["options"]],
        }))
        for v in p["variants"]:
            lines.append(json.dumps({
                "id": v["admin_graphql_api_id"],
                "title": v["title"],
                "sku": v["sku"],
                "price": v["price"],
                "availableForSale": v.get("available", v["inventory_quantity"] > 0),
                "__parentId": p["admin_graphql_api_id"],
            }))
    return PlainTextResponse("\n".join(lines) + "\n", media_type="application/jsonl")


# =========================================================== emulator ctl ===
@app.post("/emulator/reset")
async def emulator_reset():
    _load_seed()
    STATE["tokens"].clear()
    STATE["buckets"].clear()
    return {"ok": True, "products": len(STATE["products"]),
            "customers": len(STATE["customers"]), "orders": len(STATE["orders"])}


@app.post("/emulator/orders")
async def emulator_add_order(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    existing = [o["order_number"] for o in STATE["orders"]]
    onum = body.get("order_number") or (max(existing) + 1 if existing else 1001)
    oid = body.get("id") or (5_000_000_000_000 + onum)
    order = {
        "id": oid,
        "admin_graphql_api_id": f"gid://shopify/Order/{oid}",
        "name": body.get("name") or f"#BB{onum}",
        "order_number": onum,
        "email": body.get("email", "guest@example.com"),
        "created_at": body.get("created_at") or now_iso(),
        "updated_at": now_iso(),
        "processed_at": now_iso(),
        "cancelled_at": None,
        "closed_at": None,
        "cancel_reason": None,
        "currency": "USD",
        "total_price": body.get("total_price", "0.00"),
        "subtotal_price": body.get("subtotal_price", body.get("total_price", "0.00")),
        "total_tax": "0.00",
        "total_discounts": "0.00",
        "financial_status": body.get("financial_status", "paid"),
        "fulfillment_status": body.get("fulfillment_status"),
        "customer": body.get("customer"),
        "line_items": body.get("line_items", []),
        "fulfillments": body.get("fulfillments", []),
        "refunds": [],
        "shipping_address": body.get("shipping_address"),
        "billing_address": body.get("billing_address"),
        "tags": body.get("tags", ""),
        "note": body.get("note"),
    }
    STATE["orders"].append(order)
    return JSONResponse({"order": order}, status_code=201)


@app.patch("/emulator/orders/{oid}")
async def emulator_patch_order(oid: str, request: Request):
    try:
        oid_i = int(oid)
    except ValueError:
        return resp_404()
    o = next((x for x in STATE["orders"] if x["id"] == oid_i), None)
    if not o:
        # allow patch by order name too
        o = next((x for x in STATE["orders"] if x["name"].lstrip("#") == oid.lstrip("#")), None)
    if not o:
        return resp_404()
    try:
        body = await request.json()
    except Exception:
        body = {}
    for k in ("financial_status", "fulfillment_status", "cancelled_at", "cancel_reason",
              "closed_at", "tags", "note"):
        if k in body:
            o[k] = body[k]
    if "tracking_number" in body:
        tn = body["tracking_number"]
        turl = body.get("tracking_url", f"https://www.ups.com/track?tracknum={tn}")
        fid = 5_200_000_000_000 + o["order_number"]
        o["fulfillments"] = [{
            "id": fid,
            "admin_graphql_api_id": f"gid://shopify/Fulfillment/{fid}",
            "order_id": o["id"],
            "status": "success",
            "shipment_status": body.get("shipment_status", "in_transit"),
            "tracking_company": "UPS",
            "tracking_number": tn,
            "tracking_numbers": [tn],
            "tracking_url": turl,
            "tracking_urls": [turl],
            "created_at": now_iso(),
            "line_items": o["line_items"],
        }]
        o["fulfillment_status"] = "fulfilled"
    o["updated_at"] = now_iso()
    return JSONResponse({"order": o})


@app.get("/emulator/state")
async def emulator_state():
    return {
        "products": len(STATE["products"]),
        "customers": len(STATE["customers"]),
        "orders": len(STATE["orders"]),
        "active_tokens": sum(1 for e in STATE["tokens"].values() if e > time.time()),
        "buckets": {k: round(v["level"], 2) for k, v in STATE["buckets"].items()},
        "shop": SHOP,
    }


@app.get("/health")
async def health():
    return {"ok": True, "service": "shopify", "products": len(STATE["products"]),
            "customers": len(STATE["customers"]), "orders": len(STATE["orders"])}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=9601, log_level="warning")
