"""Invented sample catalog. Labeled sample. One OPEN return for UI/tests only."""

from __future__ import annotations

from .fixtures_common import customer as _customer
from .names import SAMPLE_SHOP

USD = {"amount": "36.00", "currencyCode": "USD"}
BAG = {"shopMoney": USD, "presentmentMoney": USD}
SHIP = {
    "name": "Ada Demo",
    "address1": "1 Sample Wharf",
    "address2": None,
    "city": "Demo City",
    "province": "CA",
    "zip": "90001",
    "country": "US",
}

ADA = "gid://shopify/Customer/9001"
CASEY = "gid://shopify/Customer/9002"
JORDAN = "gid://shopify/Customer/9003"
SKY = "gid://shopify/Customer/9004"
GIFT_ADA = "gid://shopify/GiftCard/9001"
ORDER_ADA = "gid://shopify/Order/9001"
ORDER_CASEY_A = "gid://shopify/Order/9002"
ORDER_CASEY_B = "gid://shopify/Order/9003"
ORDER_PARTIAL = "gid://shopify/Order/9004"
RETURN_OPEN = "gid://shopify/Return/90011"

CUSTOMERS = {
    ADA: _customer(
        ADA, "Ada Demo", "1", "36.00", "ada@demo-helpdesk.example",
        created="2026-04-01T12:00:00Z",
        giftCards=[
            {
                "id": GIFT_ADA,
                "lastCharacters": "4291",
                "maskedCode": "••••4291",
                "enabled": True,
                "balance": {"amount": "25.00", "currencyCode": "USD"},
            }
        ],
    ),
    CASEY: _customer(
        CASEY, "Casey Sandbox", "2", "72.00", "casey@demo-helpdesk.example",
        created="2026-04-02T12:00:00Z",
    ),
    JORDAN: _customer(
        JORDAN, "Jordan Preview", "0", "0.00", created="2026-04-03T12:00:00Z",
    ),
    SKY: _customer(
        SKY, "Sky Jensen", "1", "50.00", "sky@demo-helpdesk.example",
        created="2026-04-04T12:00:00Z",
    ),
}

_LINE = {
    "title": "Sample Romper",
    "sku": None,
    "quantity": 1,
    "unfulfilledQuantity": 1,
    "originalUnitPriceSet": {"shopMoney": USD},
    "image": {
        "url": "https://images.pexels.com/photos/16222075/pexels-photo-16222075.jpeg?auto=compress&cs=tinysrgb&h=200&w=200&fit=crop",
        "altText": "Sample romper",
    },
}
_LINE_SHIPPED = {**_LINE, "unfulfilledQuantity": 0}
_SWADDLE = {
    "title": "Muslin Swaddle",
    "sku": None,
    "quantity": 1,
    "unfulfilledQuantity": 0,
    "originalUnitPriceSet": {"shopMoney": {"amount": "28.00", "currencyCode": "USD"}},
    "image": {
        "url": "https://images.pexels.com/photos/9448357/pexels-photo-9448357.jpeg?auto=compress&cs=tinysrgb&h=200&w=200&fit=crop",
        "altText": "Muslin swaddle",
    },
}
_BOOTIES = {
    "title": "Knit Baby Booties",
    "sku": None,
    "quantity": 1,
    "unfulfilledQuantity": 1,
    "originalUnitPriceSet": {"shopMoney": {"amount": "18.00", "currencyCode": "USD"}},
    "image": {
        "url": "https://images.pexels.com/photos/6902351/pexels-photo-6902351.jpeg?auto=compress&cs=tinysrgb&h=200&w=200&fit=crop",
        "altText": "Knit baby booties",
    },
}
PARTIAL_BAG = {
    "shopMoney": {"amount": "50.00", "currencyCode": "USD"},
    "presentmentMoney": {"amount": "50.00", "currencyCode": "USD"},
}

ORDERS = {
    ORDER_ADA: {
        "id": ORDER_ADA,
        # Display name matches LOCK / intake Ada reading state (#1001), not the sample GID.
        "name": "#1001",
        "createdAt": "2026-05-01T15:00:00Z",
        "displayFinancialStatus": "PAID",
        "displayFulfillmentStatus": "UNFULFILLED",
        "returnStatus": "IN_PROGRESS",
        "currentTotalPriceSet": BAG,
        "billingAddress": None,
        "shippingAddress": SHIP,
        "lineItems": {"nodes": [_LINE]},
        "fulfillments": [],
        "discountCodes": ["WELCOME10"],
        "invoiceUrl": "https://example.com/invoice/demo-1001",
        "warranty": {
            "period": "1 year",
            "status": "Active",
            "endsOn": "2027-03-12",
        },
        "eta": "2026-09-08T16:00:00Z",
        "shippingZone": "Domestic",
        "returns": {
            "nodes": [
                {"id": RETURN_OPEN, "name": "#1001-R1", "status": "OPEN", "totalQuantity": 1},
            ]
        },
        "customerId": ADA,
    },
    ORDER_CASEY_A: {
        "id": ORDER_CASEY_A,
        "name": "#9002",
        "createdAt": "2026-05-02T15:00:00Z",
        "displayFinancialStatus": "PAID",
        "displayFulfillmentStatus": "FULFILLED",
        "returnStatus": "NO_RETURN",
        "currentTotalPriceSet": BAG,
        "billingAddress": None,
        "shippingAddress": {**SHIP, "name": "Casey Sandbox"},
        "lineItems": {"nodes": [_LINE_SHIPPED]},
        "fulfillments": [
            {
                "displayStatus": "IN_TRANSIT",
                "trackingInfo": [
                    {
                        "number": "SAMPLE-9002",
                        "url": "https://example.com/sample/9002",
                        "company": "Sample Carrier",
                    }
                ],
            }
        ],
        "returns": {"nodes": []},
        "customerId": CASEY,
    },
    ORDER_CASEY_B: {
        "id": ORDER_CASEY_B,
        "name": "#9003",
        "createdAt": "2026-05-03T15:00:00Z",
        "displayFinancialStatus": "PAID",
        "displayFulfillmentStatus": "UNFULFILLED",
        "returnStatus": "NO_RETURN",
        "currentTotalPriceSet": BAG,
        "billingAddress": None,
        "shippingAddress": {**SHIP, "name": "Casey Sandbox"},
        "lineItems": {"nodes": [_LINE]},
        "fulfillments": [],
        "returns": {"nodes": []},
        "customerId": CASEY,
    },
    ORDER_PARTIAL: {
        "id": ORDER_PARTIAL,
        "name": "#9004",
        "createdAt": "2026-05-04T15:00:00Z",
        "displayFinancialStatus": "PAID",
        "displayFulfillmentStatus": "PARTIALLY_FULFILLED",
        "returnStatus": "NO_RETURN",
        "currentTotalPriceSet": PARTIAL_BAG,
        "billingAddress": None,
        "shippingAddress": {**SHIP, "name": "Sky Jensen"},
        "lineItems": {"nodes": [_SWADDLE, _BOOTIES]},
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
        "returns": {"nodes": []},
        "customerId": SKY,
    },
}

CUSTOMER_ORDERS = {
    ADA: [ORDER_ADA],
    CASEY: [ORDER_CASEY_B, ORDER_CASEY_A],
    JORDAN: [],
    SKY: [ORDER_PARTIAL],
}

SHOP = SAMPLE_SHOP
