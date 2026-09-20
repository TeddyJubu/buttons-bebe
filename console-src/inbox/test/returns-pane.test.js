import test from "node:test";
import assert from "node:assert/strict";
import { createInboxOrgan } from "../js/inbox.js";
import { forbiddenControlHits } from "../js/util.js";

// Issue #46: the returns/refunds pane. Gorgias parity: returns with their
// created date, status, items with prices, return reason and return type
// (return vs exchange), plus a clear "no returns" state. The data comes
// from the read-only Shopify snapshot channel (Admin GraphQL Return fields
// exported into the rail); every write action stays refused (AGENTS.md §2).
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

// The shape _clerk_returns exports: returns.nodes carry id, name, status,
// createdAt, returnType, items[{title, quantity, price, reason, note}].
function returnsPayload(state = {}) {
  return {
    status: "found",
    email: "ada@example.test",
    customerId: "gid://shopify/Customer/1",
    order: null,
    returns: {
      orderReturnStatus: "IN_PROGRESS",
      returns: {nodes: [{
        id: "gid://shopify/Return/70001", name: "#1001-1", status: "OPEN",
        createdAt: "2026-09-15T12:00:00Z", returnType: "RETURN", totalQuantity: 2,
        items: [
          {title: "Teal button 2-pack", quantity: 2,
            price: {shopMoney: {amount: "24.00", currencyCode: "USD"}},
            reason: "Wrong size", note: "too small"},
        ],
      }]},
      inProgress: true,
    },
    history: [],
    keysHash: "kh",
    fetchedAt: "2026-09-18T00:00:00Z",
    fetchedAtEpoch: Math.floor(Date.parse("2026-09-18T00:00:00Z") / 1000),
    stale: false,
    shop: "buttons-bebe.myshopify.com",
    ...state,
  };
}

function observedRowWithRail(payload = returnsPayload()) {
  return {id: "gorgias:1", projectionSource: true, historyIncomplete: true,
    subject: "Return question", customerName: "ada@example.test", fromEmail: "ada@example.test",
    snippet: "Observed", updatedAt: "2026-09-18T00:00:00Z",
    status: "open", assignee: null, assigneeEmail: null, messages: [], statusEvents: [],
    shopifyRail: payload};
}

function railCard(html, tissue) {
  const idx = html.indexOf(`data-tissue="${tissue}"`);
  return idx < 0 ? "" : html.slice(idx, idx + 3000).split("</section>")[0];
}

test("a snapshot return renders its created date, status, items, prices and reason", async () => {
  const organ = createInboxOrgan({shop: observedShop([observedRowWithRail()]), storage: freshStorage(), operatorEmail: OPERATOR});
  const snap = await organ.ready();
  assert.equal(snap.rail.models.fromSnapshot, true, "the rail renders from the snapshot");
  const returns = railCard(snap.html, "returns");
  assert.ok(returns, "the returns card renders");
  assert.match(returns, /Return #1001-1/, "the return names itself");
  // cubic: created dates render in the host's local zone (formatWhen has no
  // explicit timeZone), so the fixture is midday UTC and the assertion is on
  // the month only — stable from +14 to -12.
  assert.match(returns, /Sept/, "the return's created date renders");
  assert.match(returns, /Open/, "the return status renders");
  assert.match(returns, /Teal button 2-pack/, "the returned item renders");
  assert.match(returns, /24\.00 USD/, "the item price renders");
  assert.match(returns, /Wrong size/, "the return reason renders");
  assert.match(returns, /too small/, "the return note renders");
  assert.doesNotMatch(returns, /EXCHANGE/, "a plain return is not labeled an exchange");
  assert.deepEqual(forbiddenControlHits(snap.html), [], "no banned control language appears");
});

test("an exchange renders as an exchange, never a plain return", async () => {
  const payload = returnsPayload();
  payload.returns.returns.nodes[0] = {
    id: "gid://shopify/Return/70002", name: "#1001-2", status: "OPEN",
    createdAt: "2026-09-02T12:00:00Z", returnType: "EXCHANGE", totalQuantity: 1,
    items: [{title: "Oak rattle", quantity: 1, price: {shopMoney: {amount: "18.00", currencyCode: "USD"}}, reason: "Different color", note: ""}],
  };
  const organ = createInboxOrgan({shop: observedShop([observedRowWithRail(payload)]), storage: freshStorage(), operatorEmail: OPERATOR});
  const snap = await organ.ready();
  const returns = railCard(snap.html, "returns");
  assert.match(returns, /Exchange/, "the exchange label renders");
  assert.match(returns, /Oak rattle/, "the exchanged item renders");
  assert.doesNotMatch(returns, /Return for order/, "an exchange is not labeled a plain return");
});

test("a bare snapshot return beside a detailed one still renders its line", async () => {
  // cubic: one detailed node must not let a sibling bare node (no items,
  // but snapshot signatures: returnType/createdAt) disappear.
  const payload = returnsPayload();
  payload.returns.returns.nodes.push({
    id: "gid://shopify/Return/70004", name: "#1001-4", status: "CLOSED",
    createdAt: "2026-08-20T12:00:00Z", returnType: "RETURN", items: [],
  });
  const organ = createInboxOrgan({shop: observedShop([observedRowWithRail(payload)]), storage: freshStorage(), operatorEmail: OPERATOR});
  const snap = await organ.ready();
  const returns = railCard(snap.html, "returns");
  assert.match(returns, /Return #1001-1/, "the detailed return still renders");
  assert.match(returns, /Return #1001-4/, "the bare sibling renders its own line");
  assert.match(returns, /Closed/, "the bare sibling's status renders");
});

test("a completed return still renders its details", async () => {
  const payload = returnsPayload();
  payload.returns.returns.nodes[0] = {
    id: "gid://shopify/Return/70003", name: "#1001-3", status: "CLOSED",
    createdAt: "2026-08-15T12:00:00Z", returnType: "RETURN", totalQuantity: 1,
    items: [{title: "Oak rattle", quantity: 1, price: {shopMoney: {amount: "18.00", currencyCode: "USD"}}, reason: "Damaged", note: ""}],
  };
  payload.returns.returns.inProgress = false;
  const organ = createInboxOrgan({shop: observedShop([observedRowWithRail(payload)]), storage: freshStorage(), operatorEmail: OPERATOR});
  const snap = await organ.ready();
  const returns = railCard(snap.html, "returns");
  assert.match(returns, /Oak rattle/, "the completed return's item renders");
  assert.match(returns, /18\.00 USD/, "the completed return's price renders");
  assert.match(returns, /Damaged/, "the completed return's reason renders");
});

test("an empty returns state says so instead of rendering an empty pane", async () => {
  const payload = returnsPayload();
  payload.returns = {orderReturnStatus: "NO_RETURN", returns: {nodes: []}, inProgress: false};
  const organ = createInboxOrgan({shop: observedShop([observedRowWithRail(payload)]), storage: freshStorage(), operatorEmail: OPERATOR});
  const snap = await organ.ready();
  const returns = railCard(snap.html, "returns");
  assert.ok(returns, "the returns card still renders");
  assert.match(returns, /No returns/, "the empty state is explicit");
});

test("a truncated item list says some items are not shown", async () => {
  const payload = returnsPayload();
  payload.returns.returns.nodes[0].itemsTruncated = true;
  const organ = createInboxOrgan({shop: observedShop([observedRowWithRail(payload)]), storage: freshStorage(), operatorEmail: OPERATOR});
  const snap = await organ.ready();
  const returns = railCard(snap.html, "returns");
  assert.match(returns, /Some items are not shown/, "the truncation is explicit");
});

test("an in-progress return opens by default in the snapshot rail", async () => {
  const organ = createInboxOrgan({shop: observedShop([observedRowWithRail()]), storage: freshStorage(), operatorEmail: OPERATOR});
  const snap = await organ.ready();
  const returns = railCard(snap.html, "returns");
  assert.match(returns, /data-open="true"/, "an OPEN return default-opens");
});
