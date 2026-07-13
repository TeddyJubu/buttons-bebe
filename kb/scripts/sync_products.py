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
import os
import pathlib
import re
import shutil
import tempfile
import time

import requests

KB_DIR = pathlib.Path(__file__).resolve().parent.parent
PRODUCTS_DIR = KB_DIR / "products"
ENV_CANDIDATES = [KB_DIR.parent / ".env", KB_DIR / ".env", KB_DIR.parent / "webhook" / ".env"]
DEFAULT_API_VERSION = "2026-04"
MAX_BULK_POLLS = 180  # 12 minutes at the four-second polling interval


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
    escaped_query = json.dumps(product_query)[1:-1]
    mutation = (
        'mutation { bulkOperationRunQuery(query: """%s""") '
        "{ bulkOperation { id status } userErrors { field message } } }"
    ) % (_BULK_INNER % escaped_query)
    resp = gql(shop, ver, tok, mutation)
    errs = resp["data"]["bulkOperationRunQuery"]["userErrors"]
    if errs:
        raise SystemExit(f"bulk start errors: {errs}")
    print("bulk export started; waiting for it to finish...")
    poll = "{ currentBulkOperation { id status errorCode objectCount url } }"
    for poll_number in range(1, MAX_BULK_POLLS + 1):
        time.sleep(4)
        c = gql(shop, ver, tok, poll)["data"]["currentBulkOperation"]
        status = c.get("status")
        print(f"  poll={poll_number}/{MAX_BULK_POLLS} status={status} objects={c.get('objectCount')}")
        if status == "COMPLETED":
            return c.get("url") or ""
        if status in ("FAILED", "CANCELED", "EXPIRED"):
            raise SystemExit(f"bulk export {status}: {c.get('errorCode')}")
    raise SystemExit(f"bulk export did not complete after {MAX_BULK_POLLS} polls")


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
        if not isinstance(rec, dict):
            raise SystemExit("refusing malformed Shopify export record")
        rid = rec.get("id", "")
        if isinstance(rid, str) and rid.startswith("gid://shopify/Product/"):
            products[rid] = rec
        elif "__parentId" in rec:  # a variant belonging to a product
            variants.setdefault(rec["__parentId"], []).append(rec)
    return products, variants


def _render_product(p: dict, variants: dict, pid: str) -> tuple[str, str]:
    title = p.get("title") or "Untitled product"
    raw_handle = p.get("handle") or _slug(title)
    handle = _slug(raw_handle) or "untitled-product"
    vs = variants.get(pid, [])
    in_stock = any(v.get("availableForSale") for v in vs) if vs else (p.get("totalInventory") or 0) > 0

    opts = [
        o for o in (p.get("options") or [])
        if not (o.get("name") == "Title" and o.get("values") == ["Default Title"])
    ]
    opt_lines = "; ".join(f"{o.get('name', 'Option')}: {', '.join(o.get('values', []))}" for o in opts) or "—"

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
    return f"product-{handle}.md", body


def _commit_staged(staged_dir: pathlib.Path, names: set[str]) -> None:
    """Replace product files transactionally, restoring the old corpus on error."""
    PRODUCTS_DIR.mkdir(parents=True, exist_ok=True)
    old_files = sorted(PRODUCTS_DIR.glob("product-*.md"))
    backup_dir = staged_dir.parent / "backup"
    backup_dir.mkdir()
    for old in old_files:
        shutil.copy2(old, backup_dir / old.name)

    replaced: list[pathlib.Path] = []
    try:
        for staged in sorted(staged_dir.glob("product-*.md")):
            destination = PRODUCTS_DIR / staged.name
            os.replace(staged, destination)
            replaced.append(destination)
        for old in old_files:
            if old.name not in names:
                old.unlink()
    except Exception:
        old_names = {old.name for old in old_files}
        for destination in replaced:
            backup = backup_dir / destination.name
            if backup.exists():
                shutil.copy2(backup, destination)
            elif destination.name not in old_names and destination.exists():
                destination.unlink()
        for old in old_files:
            backup = backup_dir / old.name
            if backup.exists() and not old.exists():
                shutil.copy2(backup, old)
        raise


def write_files(products, variants) -> int:
    if not products:
        raise SystemExit("refusing to replace product corpus with an empty export")
    for pid, product in products.items():
        if not isinstance(product, dict):
            raise SystemExit(f"refusing malformed product record: {pid}")
        if not isinstance(product.get("title"), str) or not product["title"].strip():
            raise SystemExit(f"refusing product with missing title: {pid}")
        if not isinstance(product.get("handle"), str) or not product["handle"].strip():
            raise SystemExit(f"refusing product with missing handle: {pid}")

    staging_root = pathlib.Path(tempfile.mkdtemp(prefix=".products-sync-", dir=PRODUCTS_DIR.parent))
    staged_dir = staging_root / "products"
    staged_dir.mkdir()
    names: set[str] = set()
    try:
        for pid, p in products.items():
            filename, body = _render_product(p, variants, pid)
            if filename in names:
                raise SystemExit(f"duplicate product filename in export: {filename}")
            names.add(filename)
            (staged_dir / filename).write_text(body)
        _commit_staged(staged_dir, names)
        return len(names)
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


def main():
    creds = load_creds()
    print(f"shop={creds['shop']} api={creds['ver']} filter='{creds['product_query']}'")
    tok = mint_token(creds["shop"], creds["cid"], creds["sec"])
    print("token minted (valid ~24h).")
    url = run_bulk_export(creds["shop"], creds["ver"], tok, creds["product_query"])
    records = download_jsonl(url)
    print(f"downloaded {len(records)} records (products + variants).")
    products, variants = split_records(records)
    if not products:
        raise SystemExit("refusing to replace product corpus: export contained no products")
    n = write_files(products, variants)
    print(f"wrote {n} product files to {PRODUCTS_DIR}")


if __name__ == "__main__":
    main()
