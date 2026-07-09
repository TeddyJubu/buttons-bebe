"""Integration: Shopify emulator contract (STRATEGY §2.2).

Exercised directly against the in-process emulator TestClient (env.shopify).
"""
import importlib.util
import pathlib
import sys
import time

import pytest

VER = "2026-07"


def _token(env, cid="test-client-id", sec="test-client-secret"):
    r = env.shopify.post("/admin/oauth/access_token", json={
        "client_id": cid, "client_secret": sec, "grant_type": "client_credentials"})
    return r


def _auth_headers(env):
    return {"X-Shopify-Access-Token": _token(env).json()["access_token"]}


# --- OAuth token grant variants ---------------------------------------------
def test_token_grant_happy(env):
    r = _token(env)
    assert r.status_code == 200
    body = r.json()
    assert body["access_token"]
    assert body["scope"] == "read_orders,read_customers,read_products"
    assert body["expires_in"] == 86399


def test_token_grant_bad_secret_401_exact_body(env):
    r = _token(env, sec="wrong-secret")
    assert r.status_code == 401
    assert r.json() == {
        "errors": "[API] Invalid API key or access token (unrecognized login or wrong password)"}


def test_token_grant_missing_grant_type_401(env):
    r = env.shopify.post("/admin/oauth/access_token",
                         json={"client_id": "test-client-id", "client_secret": "test-client-secret"})
    assert r.status_code == 401


def test_rest_without_token_is_401(env):
    r = env.shopify.get(f"/admin/api/{VER}/orders.json?status=any")
    assert r.status_code == 401


def test_rest_with_expired_token_is_401(env):
    tok = _token(env).json()["access_token"]
    # expire it directly in emulator state
    env.shopify.app  # noqa
    mod = sys.modules["fable_emu_shopify"]
    mod.STATE["tokens"][tok] = time.time() - 10
    r = env.shopify.get(f"/admin/api/{VER}/orders.json?status=any",
                        headers={"X-Shopify-Access-Token": tok})
    assert r.status_code == 401


# --- orders envelopes / filters ---------------------------------------------
def test_orders_by_email_envelope_and_money_strings(env):
    h = _auth_headers(env)
    r = env.shopify.get(f"/admin/api/{VER}/orders.json",
                        params={"email": "emma.wilson@example.com", "status": "any"}, headers=h)
    assert r.status_code == 200
    orders = r.json()["orders"]
    assert len(orders) >= 1
    o = orders[0]
    assert o["name"] == "#BB1015"
    assert isinstance(o["total_price"], str)       # money-as-strings
    assert o["admin_graphql_api_id"].startswith("gid://shopify/Order/")
    assert o["email"] == "emma.wilson@example.com"  # snake_case field


def test_orders_by_name(env):
    h = _auth_headers(env)
    r = env.shopify.get(f"/admin/api/{VER}/orders.json",
                        params={"name": "#BB1015", "status": "any"}, headers=h)
    names = [o["name"] for o in r.json()["orders"]]
    assert "#BB1015" in names


def test_single_order_by_id(env):
    h = _auth_headers(env)
    listing = env.shopify.get(f"/admin/api/{VER}/orders.json",
                              params={"email": "emma.wilson@example.com", "status": "any"},
                              headers=h).json()["orders"]
    oid = listing[0]["id"]
    r = env.shopify.get(f"/admin/api/{VER}/orders/{oid}.json", headers=h)
    assert r.status_code == 200
    assert r.json()["order"]["id"] == oid


def test_customers_search_by_email(env):
    h = _auth_headers(env)
    r = env.shopify.get(f"/admin/api/{VER}/customers/search.json",
                        params={"query": "email:emma.wilson@example.com"}, headers=h)
    assert r.status_code == 200
    matched = r.json()["customers"]
    assert len(matched) == 1
    assert matched[0]["email"] == "emma.wilson@example.com"


def test_call_limit_header_present(env):
    h = _auth_headers(env)
    r = env.shopify.get(f"/admin/api/{VER}/products.json", params={"limit": 5}, headers=h)
    assert "X-Shopify-Shop-Api-Call-Limit" in r.headers
    assert r.headers["X-Shopify-Shop-Api-Call-Limit"].endswith("/40")


# --- Link pagination follows ------------------------------------------------
def test_products_link_pagination_follows(env):
    h = _auth_headers(env)
    seen = []
    url = f"/admin/api/{VER}/products.json?limit=10"
    for _ in range(10):  # safety bound
        r = env.shopify.get(url, headers=h)
        assert r.status_code == 200
        for p in r.json()["products"]:
            assert p["admin_graphql_api_id"].startswith("gid://shopify/Product/")
            # variant prices are money-strings
            for v in p["variants"]:
                assert isinstance(v["price"], str)
            seen.append(p["id"])
        link = r.headers.get("Link", "")
        if 'rel="next"' not in link:
            break
        nxt = link.split("page_info=")[1].split(">")[0]
        url = f"/admin/api/{VER}/products.json?limit=10&page_info={nxt}"
    assert len(seen) == 30            # all products across pages
    assert len(set(seen)) == 30       # no duplicates


# --- leaky-bucket rate limit ------------------------------------------------
def test_leaky_bucket_429_with_retry_after(env):
    h = _auth_headers(env)
    got_429 = False
    for _ in range(60):
        r = env.shopify.get(f"/admin/api/{VER}/products.json", params={"limit": 1}, headers=h)
        if r.status_code == 429:
            got_429 = True
            assert r.headers.get("Retry-After") == "2.0"
            assert r.json()["errors"].startswith("Exceeded 2 calls per second")
            break
    assert got_429, "leaky bucket (cap 40) never tripped a 429"


# --- X-Emulator-Scenario headers --------------------------------------------
def test_scenario_server_error(env):
    h = _auth_headers(env)
    r = env.shopify.get(f"/admin/api/{VER}/orders.json?status=any",
                        headers={**h, "X-Emulator-Scenario": "server-error"})
    assert r.status_code == 500


def test_scenario_rate_limit(env):
    h = _auth_headers(env)
    r = env.shopify.get(f"/admin/api/{VER}/orders.json?status=any",
                        headers={**h, "X-Emulator-Scenario": "rate-limit"})
    assert r.status_code == 429
    assert r.headers.get("Retry-After") == "2.0"


def test_bad_api_version_is_404(env):
    h = _auth_headers(env)
    r = env.shopify.get("/admin/api/not-a-version/orders.json", headers=h)
    assert r.status_code == 404


# --- GraphQL products query used by kb/scripts/sync_products.py --------------
@pytest.fixture(scope="module")
def sync_products_mod():
    path = pathlib.Path(__file__).resolve().parents[3] / "kb" / "scripts" / "sync_products.py"
    spec = importlib.util.spec_from_file_location("bb_sync_products", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_sync_products_bulk_query_passes(env, sync_products_mod):
    """Build the exact bulk mutation sync_products.py sends, run the bulk flow
    against the emulator, download the JSONL, and parse it with the real
    split_records(). Proves the sync query shape is emulator-compatible."""
    h = _auth_headers(env)
    inner = sync_products_mod._BULK_INNER % "status:active"
    mutation = (
        'mutation { bulkOperationRunQuery(query: """%s""") '
        "{ bulkOperation { id status } userErrors { field message } } }"
    ) % inner

    r = env.shopify.post(f"/admin/api/{VER}/graphql.json",
                         json={"query": mutation}, headers=h)
    assert r.status_code == 200
    data = r.json()["data"]["bulkOperationRunQuery"]
    assert data["userErrors"] == []
    assert data["bulkOperation"]["status"] == "CREATED"
    # cost extension present
    assert "cost" in r.json()["extensions"]

    poll = "{ currentBulkOperation { id status errorCode objectCount url } }"
    pr = env.shopify.post(f"/admin/api/{VER}/graphql.json", json={"query": poll}, headers=h)
    cur = pr.json()["data"]["currentBulkOperation"]
    assert cur["status"] == "COMPLETED"
    assert cur["url"]

    # download JSONL (via the emulator path) and parse with the real logic
    url_path = "/" + cur["url"].split("/", 3)[3]
    jr = env.shopify.get(url_path)
    records = [__import__("json").loads(line) for line in jr.text.splitlines() if line.strip()]
    products, variants = sync_products_mod.split_records(records)
    assert len(products) == 30
    assert sum(len(v) for v in variants.values()) >= 30
    # write_files-style fields present
    any_pid = next(iter(products))
    assert "title" in products[any_pid]


def test_graphql_direct_products_query(env, sync_products_mod):
    h = _auth_headers(env)
    # matches the contract's products(first, after){edges{node{...}}} shape.
    query = """
    {
      products(first: 5) {
        edges { node { id title handle description bodyHtml onlineStoreUrl status tags
          variants(first: 5) { edges { node { id title price sku } } } } }
        pageInfo { hasNextPage endCursor }
      }
    }"""
    r = env.shopify.post(f"/admin/api/{VER}/graphql.json",
                         json={"query": query}, headers=h)
    assert r.status_code == 200
    data = r.json()["data"]["products"]
    assert len(data["edges"]) == 5
    node = data["edges"][0]["node"]
    assert node["id"].startswith("gid://shopify/Product/")
    assert node["status"] in ("ACTIVE", "DRAFT", "ARCHIVED")
    assert data["pageInfo"]["hasNextPage"] is True


# --- emulator test controls -------------------------------------------------
def test_emulator_controls_add_and_patch_order(env):
    add = env.shopify.post("/emulator/orders", json={
        "email": "newbie@example.com", "total_price": "25.00", "fulfillment_status": None})
    assert add.status_code == 201
    oid = add.json()["order"]["id"]
    patched = env.shopify.patch(f"/emulator/orders/{oid}",
                                json={"tracking_number": "1ZTESTTRACK", "fulfillment_status": "fulfilled"})
    assert patched.status_code == 200
    o = patched.json()["order"]
    assert o["fulfillment_status"] == "fulfilled"
    assert o["fulfillments"][0]["tracking_number"] == "1ZTESTTRACK"


def test_emulator_state_and_reset(env):
    st = env.shopify.get("/emulator/state").json()
    assert st["products"] == 30 and st["orders"] == 40 and st["customers"] == 25
