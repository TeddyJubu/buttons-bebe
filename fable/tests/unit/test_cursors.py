"""Unit tests for pagination cursors (TESTING-STRATEGY §2.1).

Covers the Shopify emulator's opaque ``page_info`` encode/decode + Link-header
paginator, and the limit clamps.
"""
import pytest


@pytest.fixture
def shop(emulator_modules):
    return emulator_modules["shopify"]


# --- page_info opaque round-trip -------------------------------------------
def test_page_info_round_trips(shop):
    tok = shop.encode_page_info("orders", {"email": "a@b.com"}, since_id=42, limit=10)
    assert isinstance(tok, str)
    assert "=" not in tok  # url-safe, padding stripped
    info = shop.decode_page_info(tok)
    assert info["r"] == "orders"
    assert info["since_id"] == 42
    assert info["limit"] == 10
    assert info["f"] == {"email": "a@b.com"}


def test_page_info_is_opaque_not_plaintext(shop):
    tok = shop.encode_page_info("products", {}, 7, 5)
    assert "since_id" not in tok  # base64, not readable


# --- paginate windowing + Link header --------------------------------------
def _items(n):
    return [{"id": i} for i in range(1, n + 1)]


def test_paginate_first_page_sets_next_link(shop):
    page, headers = shop.paginate(_items(30), "products", {}, None, 10, "2026-07")
    assert [p["id"] for p in page] == list(range(1, 11))
    assert "Link" in headers
    assert 'rel="next"' in headers["Link"]


def test_paginate_follows_cursor_to_next_page(shop):
    items = _items(30)
    _page1, headers = shop.paginate(items, "products", {}, None, 10, "2026-07")
    # extract page_info from the Link header
    link = headers["Link"]
    token = link.split("page_info=")[1].split(">")[0]
    page2, headers2 = shop.paginate(items, "products", {}, token, 10, "2026-07")
    assert [p["id"] for p in page2] == list(range(11, 21))


def test_paginate_last_page_has_no_next(shop):
    page, headers = shop.paginate(_items(8), "products", {}, None, 10, "2026-07")
    assert len(page) == 8
    assert "Link" not in headers or 'rel="next"' not in headers.get("Link", "")


# --- limit clamps -----------------------------------------------------------
def test_paginate_clamps_limit_upper_bound(shop):
    page, _ = shop.paginate(_items(300), "products", {}, None, 9999, "2026-07")
    assert len(page) == 250  # Shopify hard cap


def test_paginate_clamps_limit_lower_bound(shop):
    # a negative limit is clamped up to the minimum of 1
    page, _ = shop.paginate(_items(10), "products", {}, None, -5, "2026-07")
    assert len(page) == 1  # min 1


def test_paginate_falsy_limit_uses_default(shop):
    # limit=0 is falsy -> treated as the default of 50
    page, _ = shop.paginate(_items(100), "products", {}, None, 0, "2026-07")
    assert len(page) == 50


def test_paginate_default_limit(shop):
    page, _ = shop.paginate(_items(100), "products", {}, None, None, "2026-07")
    assert len(page) == 50  # default when unspecified
