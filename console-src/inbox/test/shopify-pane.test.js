import test from "node:test";
import assert from "node:assert/strict";
import { createInboxOrgan } from "../js/inbox.js";
import { forbiddenControlHits } from "../js/util.js";

// Issue #48: the Shopify pane. The snapshot plumbing already exists (export
// → shop-rail.sqlite3 → attach → rail.loadSnapshot); this issue locks the
// pane's UI: customer summary, this-order card, past orders with peek
// behaviour, explicit store scope, stale copy instead of silent zeros, and
// the read-only guarantee (no Refund/Cancel/Edit/Duplicate, AGENTS.md §2).
const OPERATOR = "operator@example.test";

function observedShop(rows) {
  return {
    observedHistory: true, operatorEmail: OPERATOR, projection: {generatedAt: "gen-1", stale: false},
    getCapabilities: async () => ({}),
    listTickets: async () => rows,
    getTicket: async ({ticketId}) => rows.find((row) => row.id === ticketId) || null,
  };
}

function freshStorage() {
  const backing = new Map();
  return {
    getItem: (k) => (backing.has(k) ? backing.get(k) : null),
    setItem: (k, v) => backing.set(k, String(v)),
    removeItem: (k) => backing.delete(k),
  };
}

// A minimal-but-faithful shopifyRail payload, the shape _clerk_* exports.
function railPayload(state = {}) {
  return {
    status: "found",
    email: "ada@example.test",
    customerId: "gid://shopify/Customer/1",
    orderId: "gid://shopify/Order/2",
    customer: {
      id: "gid://shopify/Customer/1", displayName: "Ada Lovelace",
      defaultEmailAddress: {emailAddress: "ada@example.test"},
      createdAt: "2025-03-01T00:00:00Z", numberOfOrders: "3",
      amountSpent: {amount: "150.00", currencyCode: "USD"},
      tags: ["vip"], giftCards: [],
    },
    order: {
      id: "gid://shopify/Order/2", name: "#1001", createdAt: "2026-09-01T00:00:00Z",
      displayFinancialStatus: "PAID", displayFulfillmentStatus: "FULFILLED",
      currentTotalPriceSet: {shopMoney: {amount: "49.00", currencyCode: "USD"}},
      billingAddress: null, shippingAddress: null,
      lineItems: {nodes: [
        {title: "Teal button 2-pack", sku: "BTN-TEAL-2", quantity: 2, unfulfilledQuantity: 0,
          originalUnitPriceSet: {shopMoney: {amount: "12.50", currencyCode: "USD"}},
          image: {url: "https://cdn.example.test/btn.jpg"}},
      ]},
      fulfillments: [], discountCodes: [],
    },
    returns: null,
    history: [
      // cubic: _clerk_history emits createdAt (order-history.js reads it) —
      // the fixture must model the exporter's real output.
      {id: "gid://shopify/Order/9", name: "#1009", createdAt: "2026-08-01T00:00:00Z",
       financialStatus: "PAID", fulfillmentStatus: "FULFILLED", totalPrice: "30.00"},
    ],
    keysHash: "kh",
    fetchedAt: "2026-09-18T00:00:00Z",
    fetchedAtEpoch: Math.floor(Date.parse("2026-09-18T00:00:00Z") / 1000),
    stale: false,
    shop: "buttons-bebe.myshopify.com",
    ...state,
  };
}

function observedRowWithRail(payload = railPayload()) {
  return {id: "gorgias:1", projectionSource: true, historyIncomplete: true,
    subject: "Order question", customerName: "ada@example.test", fromEmail: "ada@example.test",
    snippet: "Observed", updatedAt: "2026-09-18T00:00:00Z",
    status: "open", assignee: null, assigneeEmail: null, messages: [], statusEvents: [],
    customerContext: {source: "canonical_webhook", status: "observed", conflict: false,
      observedAt: "2026-09-18T00:00:00Z", identity: {name: "Ada Lovelace", email: "ada@example.test", phone: null, id: null}},
    shopifyRail: payload};
}

function railCard(html, tissue) {
  const idx = html.indexOf(`data-tissue="${tissue}"`);
  return idx < 0 ? "" : html.slice(idx, idx + 3000).split("</section>")[0];
}

test("a populated snapshot renders the customer summary, order card and history", async () => {
  const organ = createInboxOrgan({shop: observedShop([observedRowWithRail()]), storage: freshStorage(), operatorEmail: OPERATOR});
  const snap = await organ.ready();
  assert.equal(snap.rail.models.fromSnapshot, true, "the rail renders from the snapshot");
  const customer = railCard(snap.html, "customer");
  assert.match(customer, /Ada Lovelace/, "the customer name renders");
  assert.match(customer, /Total spent/, "the total spent row renders");
  assert.match(customer, /150\.00 USD/, "the spent amount renders");
  assert.match(customer, /Orders/, "the orders row renders");
  assert.match(customer, /Customer since/, "the created row renders");
  assert.match(customer, /vip/, "the customer tag renders");
  const order = railCard(snap.html, "order");
  assert.match(order, /#1001/, "the order name renders");
  assert.match(order, /Teal button 2-pack/, "the line item renders");
  assert.match(order, /49\.00 USD/, "the order total renders");
  assert.match(order, /Paid/i, "the financial status renders");
  const history = snap.html.split('data-tissue="order-history"')[1]?.slice(0, 2000) || "";
  assert.match(history, /#1009/, "the past order renders");
  assert.deepEqual(forbiddenControlHits(snap.html), [], "no banned control language appears");
});

test("the store scope is explicit in the snapshot notice", async () => {
  const organ = createInboxOrgan({shop: observedShop([observedRowWithRail()]), storage: freshStorage(), operatorEmail: OPERATOR});
  const snap = await organ.ready();
  const railHtml = snap.html.split('data-pane="rail"')[1] || "";
  assert.match(railHtml, /buttons-bebe\.myshopify\.com/, "the snapshot names its store scope");
  assert.match(railHtml, /Shopify snapshot/, "the snapshot notice renders");
});

test("a stale snapshot keeps its timestamp instead of a third stale banner", async () => {
  const organ = createInboxOrgan({shop: observedShop([observedRowWithRail(railPayload({stale: true}))]), storage: freshStorage(), operatorEmail: OPERATOR});
  const snap = await organ.ready();
  const railHtml = snap.html.split('data-pane="rail"')[1] || "";
  // The data still renders (it is the best observation we hold) with its
  // timestamp; the "stale" verdict lives once in the list banner, not here.
  assert.match(railHtml, /Shopify snapshot/, "the snapshot notice renders");
  assert.match(railHtml, /Sept/, "the snapshot timestamp renders");
  assert.doesNotMatch(railHtml, /Refresh delayed; details may be outdated/, "no third stale banner in the rail");
  assert.match(railHtml, /Ada Lovelace/, "the stale snapshot's data still renders");
});

test("no snapshot keeps the honest awaiting-refresh copy", async () => {
  const row = {id: "gorgias:2", projectionSource: true, historyIncomplete: true,
    subject: "S", customerName: "b@example.test", fromEmail: "b@example.test",
    snippet: "Observed", updatedAt: "2026-09-18T00:00:00Z",
    status: "open", assignee: null, assigneeEmail: null, messages: [], statusEvents: []};
  const organ = createInboxOrgan({shop: observedShop([row]), storage: freshStorage(), operatorEmail: OPERATOR});
  const snap = await organ.ready();
  const railHtml = snap.html.split('data-pane="rail"')[1] || "";
  assert.equal(snap.rail.models.fromSnapshot, false, "no snapshot attaches");
  assert.match(railHtml, /Shopify details are awaiting refresh|not included in the observed history/, "the awaiting-refresh copy renders");
  assert.doesNotMatch(railHtml, /data-tissue="order"/, "no order card without a snapshot");
});

test("line items render thumbnails and quantities", async () => {
  const organ = createInboxOrgan({shop: observedShop([observedRowWithRail()]), storage: freshStorage(), operatorEmail: OPERATOR});
  const snap = await organ.ready();
  const order = railCard(snap.html, "order");
  assert.match(order, /cdn\.example\.test\/btn\.jpg/, "the thumbnail renders");
  // cubic: assert the quantity row itself — the SKU (BTN-TEAL-2) and title
  // (Teal button 2-pack) both contain a 2, so a bare \b2\b could never fail.
  assert.match(order, />2 · <span class="mono">12\.50 USD<\/span>/, "the line-meta quantity and price render");
});
