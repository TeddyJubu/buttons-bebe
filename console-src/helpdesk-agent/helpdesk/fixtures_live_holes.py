"""Offline mirror of Cute Things live holes. Empty returns. No OPEN invented."""

from __future__ import annotations

from .fixtures_common import customer as _customer
from .names import LIVE_HOLE_SHOP

C_UNFULFILLED = "gid://shopify/Customer/10207427887277"
C_FULFILLED = "gid://shopify/Customer/10207427920045"
C_MULTI = "gid://shopify/Customer/10207427952813"
C_PARTIAL = "gid://shopify/Customer/9004"
O_1001 = "gid://shopify/Order/7131035795629"
O_1002 = "gid://shopify/Order/7131035861165"
O_1003 = "gid://shopify/Order/7131035893933"
O_1004 = "gid://shopify/Order/7131035992237"
O_PARTIAL = "gid://shopify/Order/9004"

USD = {"amount": "28.00", "currencyCode": "USD"}
BAG = {"shopMoney": USD, "presentmentMoney": USD}
SHIP = {
    "name": "Demo Unfulfilled",
    "address1": "10 Hole Street",
    "address2": None,
    "city": "Sandbox",
    "province": "NY",
    "zip": "10001",
    "country": "US",
}
LINE = {
    "title": "Organic Cotton Baby Romper",
    "sku": None,
    "quantity": 1,
    "unfulfilledQuantity": 1,
    "originalUnitPriceSet": {"shopMoney": USD},
    "image": {
        "url": "https://images.pexels.com/photos/9534298/pexels-photo-9534298.jpeg?auto=compress&cs=tinysrgb&h=200&w=200&fit=crop",
        "altText": "Organic Cotton Baby Romper",
    },
}
EMPTY_RETURNS = {"nodes": []}


def _order(gid: str, name: str, created: str, fulfill: str, customer_id: str, tracking=None, title=None):
    line = dict(LINE)
    if title:
        line["title"] = title
    shipped = fulfill == "FULFILLED"
    line["unfulfilledQuantity"] = 0 if shipped else line["quantity"]
    fulfillment = None
    if tracking:
        fulfillment = {"trackingInfo": [tracking], "displayStatus": "IN_TRANSIT"}
    return {
        "id": gid,
        "name": name,
        "createdAt": created,
        "displayFinancialStatus": "PAID",
        "displayFulfillmentStatus": fulfill,
        "returnStatus": "NO_RETURN",
        "currentTotalPriceSet": BAG,
        "billingAddress": None,
        "shippingAddress": {**SHIP, "name": name.replace("#", "Order ")},
        "lineItems": {"nodes": [line]},
        "fulfillments": [fulfillment] if fulfillment else [],
        "returns": EMPTY_RETURNS,
        "customerId": customer_id,
    }


CUSTOMERS = {
    C_UNFULFILLED: _customer(
        C_UNFULFILLED, "Demo Unfulfilled", "1", "28.00", "ai-demo-unfulfilled@example.com"
    ),
    C_FULFILLED: _customer(
        C_FULFILLED, "Demo Fulfilled", "1", "28.00", "ai-demo-fulfilled@example.com"
    ),
    C_MULTI: _customer(
        C_MULTI, "Demo Multiple Orders", "2", "56.00", "ai-demo-multi@example.com"
    ),
    C_PARTIAL: _customer(C_PARTIAL, "Sky Jensen", "1", "50.00", "sky@demo-helpdesk.example"),
}

ORDERS = {
    O_1001: _order(O_1001, "#1001", "2026-08-10T14:00:00Z", "UNFULFILLED", C_UNFULFILLED),
    O_1002: _order(
        O_1002,
        "#1002",
        "2026-08-10T14:10:00Z",
        "FULFILLED",
        C_FULFILLED,
        tracking={
            "number": "AI-DEMO-1002",
            "url": "https://example.com/ai-demo/1002",
            "company": "Demo Carrier",
        },
        title="Handcrafted Wooden Teether Toy",
    ),
    O_1003: _order(
        O_1003, "#1003", "2026-08-10T14:20:00Z", "UNFULFILLED", C_MULTI, title="Designer Linen Baby Sun Hat"
    ),
    O_1004: _order(
        O_1004,
        "#1004",
        "2026-08-10T14:30:00Z",
        "FULFILLED",
        C_MULTI,
        tracking={
            "number": "AI-DEMO-1004",
            "url": "https://example.com/ai-demo/1004",
            "company": "Demo Carrier",
        },
        title="Cashmere Knit Baby Blanket",
    ),
    O_PARTIAL: {
        "id": O_PARTIAL,
        "name": "#9004",
        "createdAt": "2026-08-10T14:40:00Z",
        "displayFinancialStatus": "PAID",
        "displayFulfillmentStatus": "PARTIALLY_FULFILLED",
        "returnStatus": "NO_RETURN",
        "currentTotalPriceSet": {
            "shopMoney": {"amount": "50.00", "currencyCode": "USD"},
            "presentmentMoney": {"amount": "50.00", "currencyCode": "USD"},
        },
        "billingAddress": None,
        "shippingAddress": {**SHIP, "name": "Sky Jensen"},
        "lineItems": {
            "nodes": [
                {
                    "title": "Muslin Swaddle",
                    "sku": None,
                    "quantity": 1,
                    "unfulfilledQuantity": 0,
                    "originalUnitPriceSet": {
                        "shopMoney": {"amount": "28.00", "currencyCode": "USD"}
                    },
                    "image": {
                        "url": "https://images.pexels.com/photos/9448357/pexels-photo-9448357.jpeg?auto=compress&cs=tinysrgb&h=200&w=200&fit=crop",
                        "altText": "Muslin swaddle",
                    },
                },
                {
                    "title": "Knit Baby Booties",
                    "sku": None,
                    "quantity": 1,
                    "unfulfilledQuantity": 1,
                    "originalUnitPriceSet": {
                        "shopMoney": {"amount": "18.00", "currencyCode": "USD"}
                    },
                    "image": {
                        "url": "https://images.pexels.com/photos/6902351/pexels-photo-6902351.jpeg?auto=compress&cs=tinysrgb&h=200&w=200&fit=crop",
                        "altText": "Knit baby booties",
                    },
                },
            ]
        },
        "fulfillments": [
            {
                "displayStatus": "IN_TRANSIT",
                "trackingInfo": [
                    {
                        "number": "SAMPLE-9004",
                        "url": "https://example.com/sample/9004",
                        "company": "Sample Carrier",
                    }
                ],
                "fulfillmentLineItems": {
                    "nodes": [{"quantity": 1, "lineItem": {"title": "Muslin Swaddle"}}]
                },
            }
        ],
        "returns": EMPTY_RETURNS,
        "customerId": C_PARTIAL,
    },
}

CUSTOMER_ORDERS = {
    C_UNFULFILLED: [O_1001],
    C_FULFILLED: [O_1002],
    C_MULTI: [O_1004, O_1003],
    C_PARTIAL: [O_PARTIAL],
}

SHOP = LIVE_HOLE_SHOP
