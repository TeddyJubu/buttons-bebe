"""sync_products.py -- fetch Buttons Bebe products from Shopify into the KB.

What it does:
  1. Mints a short-lived (24h) access token using the client-credentials grant
     (SHOPIFY_CLIENT_ID + SHOPIFY_CLIENT_SECRET) -- no manual token needed.
  2. Exports products via Shopify's Bulk Operations API (built for big catalogs,
     no rate-limit babysitting).
  3. Writes one concise markdown file per product into KB/products/ following the
     KB conventions (front-matter + one "## Product details" section = 1 chunk).

Reads credentials from the app's .env (searched in a few standard spots).
Values are sanitized (paste artifacts like trailing spaces/backslashes removed).

Env vars:
  SHOPIFY_SHOP            e.g. buttons-bebe.myshopify.com   (required)
  SHOPIFY_CLIENT_ID                                          (required)
  SHOPIFY_CLIENT_SECRET                                      (required)
  SHOPIFY_API_VERSION    default 2026-04
  SHOPIFY_PRODUCT_QUERY  default "status:active"  (set to "" to fetch ALL products)

Run ./sync-products.sh to run this AND re-index in one step.
"""
import json
import pathlib
import re
import time

import requests

KB_DIR = pathlib.Path(__file__).resolve().parent.parent
PRODUCTS_DIR = KB_DIR / "products"
ENV_CANDIDATES = [KB_DIR.parent / ".env", KB_DIR / ".env", KB_DIR.parent / "webhook" / ".env"]
DEFAULT_API_VERSION = "2026-04"


def _clean(v: str) -> str:
    return re.sub(r'^[\s"\']+|[\s"\'\\]+$', "", v).replace("\r", "")


def load_creds() -> dict:
    env: dict = {}
    for fp in ENV_CANDIDATES:
        if not fp.exists():
            continue
        for line in fp.read_text().splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            k, v = k.strip(), _clean(v)
            if v and not env.get(k):
                env[k] = v
    shop, cid, sec = env.get("SHOPIFY_SHOP"), env.get("SHOPIFY_CLIENT_ID"), env.get("SHOPIFY_CLIENT_SECRET")
    if not (shop and cid and sec):
        raise SystemExit("Missing SHOPIFY_SHOP / SHOPIFY_CLIENT_ID / SHOPIFY_CLIENT_SECRET in .env")
    return dict(
        shop=shop, cid=cid, sec=sec,
        ver=env.get("SHOPIFY_API_VERSION") or DEFAULT_API_VERSION,
        product_query=env.get("SHOPIFY_PRODUCT_QUERY", "status:active"),
    )


def mint_token(shop, cid, sec) -> str:
    r = requests.post(
        f"https://{shop}/admin/oauth/access_token",
        json={"client_id": cid, "client_secret": sec, "grant_type": "client_credentials"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def gql(shop, ver, tok, query, variables=None) -> dict:
    r = requests.post(
        f"https://{shop}/admin/api/{ver}/graphql.json",
        headers={"X-Shopify-Access-Token": tok, "Content-Type": "application/json"},
        json={"query": query, "variables": variables or {}}, timeout=90,
    )
    r.raise_for_status()
    return r.json()


_BULK_INNER = """
{
  products(query: "%s") {
    edges { node {
      id title handle productType vendor status totalInventory onlineStoreUrl description
      options { name values }
      variants { edges { node { title sku price availableForSale } } }
    } }
  }
}
"""


def run_bulk_export(shop, ver, tok, product_query) -> str:
    mutation = (
        'mutation { bulkOperationRunQuery(query: """%s""") '
        "{ bulkOperation { id status } userErrors { field message } } }"
    ) % (_BULK_INNER % product_query)
    resp = gql(shop, ver, tok, mutation)
    errs = resp["data"]["bulkOperationRunQuery"]["userErrors"]
    if errs:
        raise SystemExit(f"bulk start errors: {errs}")
    print("bulk export started; waiting for it to finish...")
    poll = "{ currentBulkOperation { id status errorCode objectCount url } }"
    while True:
        time.sleep(4)
        c = gql(shop, ver, tok, poll)["data"]["currentBulkOperation"]
        print(f"  status={c['status']} objects={c.get('objectCount')}")
        if c["status"] == "COMPLETED":
            return c.get("url") or ""
        if c["status"] in ("FAILED", "CANCELED", "EXPIRED"):
            raise SystemExit(f"bulk export {c['status']}: {c.get('errorCode')}")


def download_jsonl(url) -> list:
    if not url:
        return []
    r = requests.get(url, timeout=180)
    r.raise_for_status()
    return [json.loads(line) for line in r.text.splitlines() if line.strip()]


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def split_records(records):
    products, variants = {}, {}
    for rec in records:
        rid = rec.get("id", "")
        if rid.startswith("gid://shopify/Product/"):
            products[rid] = rec
        elif "__parentId" in rec:  # a variant belonging to a product
            variants.setdefault(rec["__parentId"], []).append(rec)
    return products, variants


def write_files(products, variants) -> int:
    PRODUCTS_DIR.mkdir(exist_ok=True)
    for old in PRODUCTS_DIR.glob("product-*.md"):
        old.unlink()  # clear previous sync so removed products don't linger

    count = 0
    for pid, p in products.items():
        title = p.get("title") or "Untitled product"
        handle = p.get("handle") or _slug(title)
        vs = variants.get(pid, [])
        in_stock = any(v.get("availableForSale") for v in vs) if vs else (p.get("totalInventory") or 0) > 0

        opts = [
            o for o in (p.get("options") or [])
            if not (o.get("name") == "Title" and o.get("values") == ["Default Title"])
        ]
        opt_lines = "; ".join(f"{o['name']}: {', '.join(o.get('values', []))}" for o in opts) or "—"

        var_lines = []
        for v in vs[:60]:
            avail = "in stock" if v.get("availableForSale") else "sold out"
            var_lines.append(f"- {v.get('title', '')} (SKU {v.get('sku') or '—'}): ${v.get('price') or '—'} — {avail}")
        var_block = "\n".join(var_lines) or "- (no variant detail)"

        desc = (p.get("description") or "").strip().replace("\n", " ")
        if len(desc) > 800:
            desc = desc[:800].rsplit(" ", 1)[0] + "…"

        url = p.get("onlineStoreUrl") or f"https://buttons-bebe.myshopify.com/products/{handle}"
        tags = ["product"] + [t for t in (_slug(p.get("productType")), _slug(p.get("vendor"))) if t]

        body = (
            "---\n"
            f"title: {json.dumps(title)}\n"
            "category: products\n"
            "status: confirmed\n"
            "source: shopify-sync\n"
            f"tags: [{', '.join(tags)}]\n"
            "---\n\n"
            "## Product details\n"
            f"{title} — {p.get('productType') or 'product'} by {p.get('vendor') or 'unknown vendor'}. "
            f"Availability: {'in stock' if in_stock else 'sold out'}.\n\n"
            f"Sizes / options: {opt_lines}\n\n"
            "Variants:\n"
            f"{var_block}\n\n"
            f"{('Description: ' + desc) if desc else ''}\n\n"
            f"Handle: {handle} · Product page: {url}\n"
        )
        (PRODUCTS_DIR / f"product-{handle}.md").write_text(body)
        count += 1
    return count


def main():
    creds = load_creds()
    print(f"shop={creds['shop']} api={creds['ver']} filter='{creds['product_query']}'")
    tok = mint_token(creds["shop"], creds["cid"], creds["sec"])
    print("token minted (valid ~24h).")
    url = run_bulk_export(creds["shop"], creds["ver"], tok, creds["product_query"])
    records = download_jsonl(url)
    print(f"downloaded {len(records)} records (products + variants).")
    products, variants = split_records(records)
    n = write_files(products, variants)
    print(f"wrote {n} product files to {PRODUCTS_DIR}")


if __name__ == "__main__":
    main()
