import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { IDS, SHOP, emptyReturns, orders } from "../js/fixtures/demo-inbox.js";
import { formatSku, railWriteControlHits } from "../js/util.js";
import { createFixtureShop } from "../js/shop/fixture-shop.js";
import { projectCustomer, renderCustomer } from "../js/tissues/customer.js";
import { projectOrderHistory } from "../js/tissues/order-history.js";
import { projectOrder, renderOrder } from "../js/tissues/order.js";
import { projectReturns, renderReturns } from "../js/tissues/returns.js";
import { createMailbox } from "../js/mailbox.js";
import { createRailOrgan } from "../js/tissues/rail.js";

const shop = createFixtureShop();
const here = dirname(fileURLToPath(import.meta.url));

function clickToggle(host, name) {
  host.onclick({
    target: {
      closest(sel) {
        if (sel === "[data-history]") return null;
        if (sel === "[data-toggle]") return { dataset: { toggle: name } };
        return null;
      },
    },
  });
}

function assertMoneyBag(bag) {
  assert.deepEqual(Object.keys(bag).sort(), ["presentmentMoney", "shopMoney"]);
  assert.equal(typeof bag.shopMoney.amount, "string");
  assert.equal(typeof bag.presentmentMoney.amount, "string");
}

test("null customer GID uses the empty-ticket voice, not unavailable", () => {
  const model = projectCustomer(null);
  assert.equal(model.ok, false);
  assert.equal(model.peek, "No customer");
  assert.equal(model.record, null);
  const html = renderCustomer(model);
  assert.match(html, /<h2>Customer<\/h2>\s*<span class="peek">No customer<\/span>/);
  assert.match(html, /<p class="tissue-empty">No customer<\/p>/);
  assert.match(html, /data-customer-join-gate-open[^>]*>Find customer</);
  assert.doesNotMatch(html, /Customer unavailable|unavailable/);
});

test("customer DTO uses Clerk Admin GraphQL field names", async () => {
  const record = await shop.getCustomer({ shop: SHOP, customerId: IDS.ADA });
  const model = projectCustomer(record);
  assert.equal(model.ok, true);
  assert.equal(model.peek, "Ada Demo");
  assert.deepEqual(Object.keys(model.record).sort(), [
    "amountSpent",
    "createdAt",
    "defaultEmailAddress",
    "displayName",
    "giftCards",
    "numberOfOrders",
    "tags",
  ]);
  assert.equal(model.record.defaultEmailAddress.emailAddress, "ada.demo@example.com");
  assert.equal(model.record.email, undefined);
  assert.ok(!("email" in model.record));
  assert.deepEqual(model.record.tags, ["DEMO"]);
  assert.equal(typeof model.record.numberOfOrders, "string");
  assert.equal(model.record.numberOfOrders, "1");
  assert.equal(model.hasGiftCards, true);
  assert.equal(model.giftCardPeek, "••••4291");
  assert.equal(model.record.giftCards[0].lastCharacters, "4291");
  assert.equal(model.record.giftCards[0].enabled, true);
  assert.equal(model.record.giftCards[0].balance.amount, "25.00");
});

test("Ada fixture rail shows a gift card and a discount code", async () => {
  const customer = projectCustomer(await shop.getCustomer({ shop: SHOP, customerId: IDS.ADA }));
  const order = projectOrder(await shop.getOrder({ shop: SHOP, orderId: IDS.ORDER_1001 }));
  assert.equal(customer.hasGiftCards, true);
  assert.equal(order.hasDiscounts, true);
  assert.equal(order.hasInvoice, true);
  assert.equal(order.hasWarranty, true);
  assert.equal(order.hasEta, true);
  assert.deepEqual(order.discountCodes, ["WELCOME10"]);
  assert.equal(order.invoiceUrl, "https://example.com/invoice/demo-1001");
  assert.deepEqual(order.warranty, {
    period: "1 year",
    status: "Active",
    endsOn: "2027-03-12",
  });
  assert.equal(order.warrantyPeek, "1 year · Active");
  assert.equal(order.eta, "2026-09-08T16:00:00Z");
  assert.equal(order.shippingZone, "Domestic");
  assert.equal(order.etaPeek, "ETA Tue 8 Sep");
  const customerHtml = renderCustomer(customer, { giftCardsOpen: true });
  const orderHtml = renderOrder(order, { discountsOpen: true, invoiceOpen: true, warrantyOpen: true, etaOpen: true });
  assert.match(customerHtml, /<h3>Gift cards<\/h3>\s*<span class="peek">••••4291<\/span>/);
  assert.match(customerHtml, /<span class="mono gift-hint">••••4291<\/span>/);
  assert.match(customerHtml, /25\.00 USD · Enabled/);
  assert.match(orderHtml, /<h3>Discounts<\/h3>\s*<span class="peek">WELCOME10<\/span>/);
  assert.match(orderHtml, /<p class="mono discount-code">WELCOME10<\/p>/);
  assert.match(orderHtml, /<h3>Invoice<\/h3>\s*<span class="peek">Invoice<\/span>/);
  assert.match(orderHtml, /<a class="invoice-link" href="https:\/\/example\.com\/invoice\/demo-1001" rel="noreferrer" target="_blank">Invoice<\/a>/);
  assert.match(orderHtml, /<h3>Warranty<\/h3>\s*<span class="peek">1 year · Active<\/span>/);
  assert.match(orderHtml, /<p class="mute warranty-line">1 year · Active<\/p>/);
  assert.match(orderHtml, /<p class="mute warranty-line">Ends 12 Mar 2027<\/p>/);
  assert.match(orderHtml, /<h3>ETA<\/h3>\s*<span class="peek">ETA Tue 8 Sep<\/span>/);
  assert.match(orderHtml, /<p class="mute eta-line">ETA Tue 8 Sep<\/p>/);
  assert.match(orderHtml, /<p class="mute eta-line">Zone: Domestic<\/p>/);
  assert.doesNotMatch(customerHtml, /<(button|a)\b[^>]*>[^<]*Refund/i);
  assert.doesNotMatch(orderHtml, /<(button|a)\b[^>]*>[^<]*\bCancel\b/i);
  assert.doesNotMatch(orderHtml, /<(button|a)\b[^>]*>[^<]*Refund/i);
});

test("empty gift cards and discounts peek No gift cards / No discounts", async () => {
  const customer = projectCustomer(await shop.getCustomer({ shop: SHOP, customerId: IDS.CASEY }));
  const order = projectOrder(await shop.getOrder({ shop: SHOP, orderId: IDS.ORDER_1002 }));
  assert.equal(customer.hasGiftCards, false);
  assert.equal(customer.giftCardPeek, "No gift cards");
  assert.equal(order.hasDiscounts, false);
  assert.equal(order.discountPeek, "No discounts");
  assert.equal(order.hasInvoice, false);
  assert.equal(order.invoicePeek, "No invoice");
  assert.equal(order.hasWarranty, false);
  assert.equal(order.warrantyPeek, "No warranty");
  assert.equal(order.hasEta, false);
  assert.equal(order.etaPeek, "No ETA");
  const customerHtml = renderCustomer(customer);
  const orderHtml = renderOrder(order);
  assert.match(customerHtml, /<h3>Gift cards<\/h3>\s*<span class="peek">No gift cards<\/span>/);
  assert.match(customerHtml, /<p class="tissue-empty">No gift cards<\/p>/);
  assert.match(orderHtml, /<h3>Discounts<\/h3>\s*<span class="peek">No discounts<\/span>/);
  assert.match(orderHtml, /<p class="tissue-empty">No discounts<\/p>/);
  assert.match(orderHtml, /<h3>Invoice<\/h3>\s*<span class="peek">No invoice<\/span>/);
  assert.match(orderHtml, /<p class="tissue-empty">No invoice<\/p>/);
  assert.match(orderHtml, /<h3>Warranty<\/h3>\s*<span class="peek">No warranty<\/span>/);
  assert.match(orderHtml, /<p class="tissue-empty">No warranty<\/p>/);
  assert.match(orderHtml, /<h3>ETA<\/h3>\s*<span class="peek">No ETA<\/span>/);
  assert.match(orderHtml, /<p class="tissue-empty">No ETA<\/p>/);
  assert.match(orderHtml, /data-toggle="discounts"[^>]*aria-expanded="false"/);
  assert.match(orderHtml, /data-toggle="invoice"[^>]*aria-expanded="false"/);
  assert.match(orderHtml, /data-toggle="warranty"[^>]*aria-expanded="false"/);
  assert.match(orderHtml, /data-toggle="eta"[^>]*aria-expanded="false"/);
  assert.match(customerHtml, /data-toggle="giftCards"[^>]*aria-expanded="false"/);
});

test("unsafe invoice URLs stay empty No invoice", () => {
  const model = projectOrder({
    ...orders[IDS.ORDER_1002],
    invoiceUrl: "javascript:alert(1)",
  });
  assert.equal(model.hasInvoice, false);
  assert.equal(model.invoiceUrl, "");
  assert.equal(model.invoicePeek, "No invoice");
  const html = renderOrder(model);
  assert.match(html, /<h3>Invoice<\/h3>\s*<span class="peek">No invoice<\/span>/);
  assert.doesNotMatch(html, /javascript:/);
});

test("order fixture keeps null SKUs and missing billing", async () => {
  const order = await shop.getOrder({ shop: SHOP, orderId: IDS.ORDER_1001 });
  assert.equal(order.lineItems.nodes[0].sku, null);
  assert.equal(order.billingAddress, null);
  const model = projectOrder(order);
  assert.equal(model.skuLabels[0], "");
  assert.equal(formatSku(order.lineItems.nodes[0].sku), "");
  assert.equal(model.addressPeek, "No billing");
  assert.match(model.peek, /#1001/);
  assert.match(model.peek, /Paid/);
  assert.match(model.peek, /Fulfilled/);
  const html = renderOrder(model, { open: true, addressesOpen: true });
  assert.doesNotMatch(html, /data-sku=/);
  assert.doesNotMatch(html, /<th>SKU<\/th>/);
  assert.doesNotMatch(html, /class="mono line-sku"/);
  assert.doesNotMatch(html, /CT-TEETHER|SKU-1001|fake-sku/i);
  assert.match(html, /No billing/);
  assert.match(html, /Absent/);
  assert.match(html, /<span class="ship-company">Demo Carrier<\/span>/);
  assert.match(html, /<span class="mono ship-number">DEMO-1001<\/span>/);
  assert.match(html, /<a class="track-link" href="https:\/\/example\.com\/track\/demo-1001"[^>]*>Track<\/a>/);
  assert.doesNotMatch(html, /<a class="track-link"[^>]*>Demo Carrier/);
  assert.doesNotMatch(html, />https:\/\/example\.com\/track\/demo-1001</);
  assertMoneyBag(order.currentTotalPriceSet);
  assertMoneyBag(order.lineItems.nodes[0].originalUnitPriceSet);
  assert.equal(order.lineItems.nodes[0].originalUnitPriceSet.shopMoney.amount, "24.00");
  assert.equal(order.lineItems.nodes[0].price, undefined);
});

test("empty fulfillments with blank trackingInfo still peek No tracking", () => {
  const model = projectOrder({
    ...orders[IDS.ORDER_1002],
    fulfillments: [{ trackingInfo: [{}] }, { trackingInfo: [] }],
  });
  assert.equal(model.hasTracking, false);
  assert.equal(model.shipmentPeek, "No tracking");
  const html = renderOrder(model, { shipmentOpen: true });
  assert.match(html, /<p class="tissue-empty">No tracking<\/p>/);
  assert.doesNotMatch(html, /undefined undefined|null null/);
});

test("unfulfilled order peeks No tracking and stays collapsed", async () => {
  const order = await shop.getOrder({ shop: SHOP, orderId: IDS.ORDER_1002 });
  const model = projectOrder(order);
  assert.equal(order.lineItems.nodes[0].sku, null);
  assert.equal(model.hasTracking, false);
  assert.equal(model.shipmentPeek, "No tracking");
  assert.equal(model.lineFulfillLabels[0], "Unfulfilled");
  const html = renderOrder(model);
  assert.match(html, /<h3>Shipment<\/h3>\s*<span class="peek">No tracking<\/span>/);
  assert.match(html, /<p class="tissue-empty">No tracking<\/p>/);
  assert.match(html, /data-toggle="shipment"[^>]*aria-expanded="false"/);
  assert.match(html, /data-line-fulfill="Unfulfilled">Unfulfilled</);
  assert.doesNotMatch(html, /DEMO-1001|DEMO-1002|invented-track/i);
  assert.doesNotMatch(html, /data-sku=/);
  assert.equal(model.addressPeek, "No billing");
});

test("This order shows a Payments locked hairline", async () => {
  const order = await shop.getOrder({ shop: SHOP, orderId: IDS.ORDER_1001 });
  const html = renderOrder(projectOrder(order));
  assert.match(html, /<h2>This order<\/h2>/);
  assert.match(html, /data-write-gate-open[^>]*>Payments locked</);
  assert.doesNotMatch(html, /<(button|a)\b[^>]*>[^<]*Refund/i);
  assert.doesNotMatch(html, /<(button|a)\b[^>]*>[^<]*\bCancel\b/i);
});

test("empty This order hides Payments locked chrome", () => {
  const html = renderOrder(projectOrder(null));
  assert.match(html, /No order/);
  assert.match(html, /data-order-link-gate-open[^>]*>Link order</);
  assert.doesNotMatch(html, /data-write-gate-open/);
  assert.doesNotMatch(html, />Payments locked</);
  assert.doesNotMatch(html, /<(button|a)\b[^>]*>[^<]*Refund/i);
  assert.doesNotMatch(html, /<(button|a)\b[^>]*>[^<]*\bCancel\b/i);
});

test("partial ship shows mixed line status and tracking for the shipped line", async () => {
  const order = await shop.getOrder({ shop: SHOP, orderId: IDS.ORDER_1004 });
  const model = projectOrder(order);
  assert.equal(order.displayFulfillmentStatus, "PARTIALLY_FULFILLED");
  assert.match(model.peek, /#9004/);
  assert.match(model.peek, /Partially Fulfilled/);
  assert.deepEqual(model.lineFulfillLabels, ["Shipped", "Unfulfilled"]);
  assert.equal(model.hasTracking, true);
  assert.equal(model.shipmentPeek, "In transit");
  const html = renderOrder(model, { shipmentOpen: true });
  assert.match(html, /data-line-fulfill="Shipped">Shipped</);
  assert.match(html, /data-line-fulfill="Unfulfilled">Unfulfilled</);
  assert.match(html, /Muslin Swaddle/);
  assert.match(html, /Knit Baby Booties/);
  assert.match(html, /<span class="ship-company">Sample Carrier<\/span>/);
  assert.match(html, /<span class="mono ship-number">SAMPLE-9004<\/span>/);
  assert.match(html, /<a class="track-link" href="https:\/\/example\.com\/sample\/9004"[^>]*>Track<\/a>/);
  assert.match(html, /<h3>Shipment<\/h3>\s*<span class="peek">In transit<\/span>/);
  assert.doesNotMatch(html, /<p class="tissue-empty">No tracking<\/p>/);
});

test("different billing peeks Ship ≠ bill without crashing", async () => {
  const order = await shop.getOrder({ shop: SHOP, orderId: IDS.ORDER_1003 });
  assert.ok(order.billingAddress);
  const model = projectOrder(order);
  assert.equal(model.addressPeek, "Ship ≠ bill");
  assert.equal(renderOrder(model, { addressesOpen: true }).includes("4 Preview Court"), true);
});

test("matching billing peeks Same address", async () => {
  const order = await shop.getOrder({ shop: SHOP, orderId: IDS.ORDER_1003 });
  const model = projectOrder({ ...order, billingAddress: { ...order.shippingAddress } });
  assert.equal(model.addressPeek, "Same address");
});

test("a present SKU renders a mono row and a null SKU does not", () => {
  const withSku = projectOrder({
    ...orders[IDS.ORDER_1002],
    lineItems: { nodes: [{ title: "Canvas Demo Visor", sku: "DEMO-VISOR", quantity: 1, originalUnitPriceSet: orders[IDS.ORDER_1002].lineItems.nodes[0].originalUnitPriceSet }] },
  });
  assert.equal(withSku.skuLabels[0], "DEMO-VISOR");
  assert.match(renderOrder(withSku), /<p class="mono line-sku" data-sku="DEMO-VISOR">DEMO-VISOR<\/p>/);
  const hidden = projectOrder(orders[IDS.ORDER_1002]);
  assert.equal(hidden.skuLabels[0], "");
  assert.doesNotMatch(renderOrder(hidden), /line-sku|data-sku=/);
});

test("empty demo returns peek No returns and stay collapsed", async () => {
  const record = await shop.getReturns({ shop: SHOP, orderId: IDS.ORDER_1002 });
  assert.deepEqual(record.returns, emptyReturns.returns);
  assert.equal(record.returns.nodes.length, 0);
  assert.equal(record.returnStatus, undefined);
  const model = projectReturns(record);
  assert.equal(model.peek, "No returns");
  assert.equal(model.collapsedDefault, true);
  assert.equal(model.record.returns.nodes.length, 0);
  const html = renderReturns(model);
  assert.match(html, /data-open="false"/);
  assert.match(html, /No returns/);
});

test("Ada OPEN return peeks in-progress and default-opens", async () => {
  const record = await shop.getReturns({ shop: SHOP, orderId: IDS.ORDER_1001 });
  assert.equal(record.returns.nodes[0].status, "OPEN");
  assert.equal(record.returnStatus, undefined);
  assert.equal(record.items.length, 1);
  const model = projectReturns(record);
  assert.equal(model.peek, "In transit · 1 item");
  assert.equal(model.collapsedDefault, false);
  assert.equal(model.inProgress, true);
  assert.equal(model.record.status, "OPEN");
  const html = renderReturns(model);
  assert.match(html, /data-open="true"/);
  assert.match(html, /In transit · 1 item/);
  assert.doesNotMatch(html, /IN_PROGRESS/);
});

test("in-progress is Return.status OPEN only — no PENDING, ignore Order.returnStatus", () => {
  const src = readFileSync(join(here, "../js/tissues/returns.js"), "utf8");
  assert.doesNotMatch(src, /PENDING/);
  assert.equal(projectReturns({
    returnStatus: "OPEN",
    inProgress: true,
    returns: { nodes: [] },
    items: [],
  }).inProgress, false);
  assert.equal(projectReturns({
    returns: { nodes: [{ status: "REQUESTED" }] },
    items: [{ title: "Demo" }],
  }).inProgress, false);
  assert.equal(projectReturns({
    returns: { nodes: [{ status: "CLOSED" }] },
    items: [{ title: "Demo" }],
  }).inProgress, false);
  assert.equal(projectReturns({
    returns: { nodes: [{ status: "OPEN" }] },
    items: [{ title: "Demo" }],
  }).inProgress, true);
});

test("click closes OPEN returns and the user toggle wins", async () => {
  const rail = createRailOrgan({ shop, mailbox: createMailbox() });
  await rail.load({ shop: SHOP, customerId: IDS.ADA, orderId: IDS.ORDER_1001, ticketId: "t-ada-track" });
  assert.equal(rail.snapshot().open.returns, true);
  const host = { innerHTML: "", onclick: null };
  rail.mount(host);
  assert.match(host.innerHTML, /data-tissue="returns"[^>]*data-open="true"/);
  clickToggle(host, "returns");
  assert.equal(rail.snapshot().open.returns, false);
  assert.match(host.innerHTML, /data-tissue="returns"[^>]*data-open="false"/);
  await rail.load({ shop: SHOP, customerId: IDS.ADA, orderId: IDS.ORDER_1001, ticketId: "t-ada-track" });
  assert.equal(rail.snapshot().open.returns, false, "same ticket must not re-force OPEN");
});

test("switching tickets resets expand to lock defaults", async () => {
  const rail = createRailOrgan({ shop, mailbox: createMailbox() });
  await rail.load({ shop: SHOP, customerId: IDS.ADA, orderId: IDS.ORDER_1001, ticketId: "t-ada-track" });
  const host = { innerHTML: "", onclick: null };
  rail.mount(host);
  clickToggle(host, "returns");
  clickToggle(host, "order-history");
  clickToggle(host, "customer");
  assert.equal(rail.snapshot().open.returns, false);
  assert.equal(rail.snapshot().open["order-history"], true);
  assert.equal(rail.snapshot().open.customer, false);

  await rail.load({ shop: SHOP, customerId: IDS.CASEY, orderId: IDS.ORDER_1002, ticketId: "t-casey-visor" });
  const casey = rail.snapshot().open;
  assert.equal(casey.customer, true);
  assert.equal(casey.order, true);
  assert.equal(casey.returns, false);
  assert.equal(casey["order-history"], false);
  assert.equal(casey.addresses, false);
  assert.equal(casey.giftCards, false);
  assert.equal(casey.discounts, false);
  assert.equal(casey.invoice, false);
  assert.equal(casey.warranty, false);
  assert.equal(casey.eta, false);

  await rail.load({ shop: SHOP, customerId: IDS.JORDAN, orderId: null, ticketId: "t-jordan-ship" });
  assert.equal(rail.snapshot().open.returns, false);
  assert.equal(rail.snapshot().open.giftCards, false);
  assert.equal(rail.snapshot().open.discounts, false);
  assert.equal(rail.snapshot().open.invoice, false);
  assert.equal(rail.snapshot().open.warranty, false);
  assert.equal(rail.snapshot().open.eta, false);

  await rail.load({ shop: SHOP, customerId: IDS.ADA, orderId: IDS.ORDER_1001, ticketId: "t-ada-track" });
  assert.equal(rail.snapshot().open.returns, true);
  assert.equal(rail.snapshot().open["order-history"], false);
  assert.equal(rail.snapshot().open.customer, true);
  assert.equal(rail.snapshot().open.giftCards, true);
  assert.equal(rail.snapshot().open.discounts, true);
  assert.equal(rail.snapshot().open.invoice, true);
  assert.equal(rail.snapshot().open.warranty, true);
  assert.equal(rail.snapshot().open.eta, true);
});

test("order-history is newest first and does not replace This order", async () => {
  const rows = await shop.getOrderHistory({ shop: SHOP, customerId: IDS.CASEY });
  assert.deepEqual(Object.keys(rows[0]).sort(), [
    "createdAt",
    "currentTotalPriceSet",
    "displayFulfillmentStatus",
    "id",
    "name",
  ]);
  assert.ok(rows[0].currentTotalPriceSet.shopMoney.amount);
  assert.equal(rows[0].total, undefined);
  const model = projectOrderHistory(rows);
  assert.deepEqual(model.rows.map((row) => row.name), ["#1003", "#1002"]);
  assert.deepEqual(Object.keys(model.rows[0]).sort(), [
    "createdAt",
    "fulfillmentStatus",
    "id",
    "name",
    "total",
  ]);

  const mailbox = createMailbox();
  const peeks = [];
  mailbox.subscribe("history/peek", (payload) => peeks.push(payload));
  const rail = createRailOrgan({ shop, mailbox });
  await rail.load({ shop: SHOP, customerId: IDS.CASEY, orderId: IDS.ORDER_1002 });
  assert.equal(rail.snapshot().currentOrderId, IDS.ORDER_1002);
  assert.equal(rail.snapshot().models.order.record.name, "#1002");

  const host = {
    innerHTML: "",
    onclick: null,
  };
  rail.mount(host);
  const before = rail.snapshot().models.order.record.name;
  host.onclick({
    target: {
      closest(sel) {
        if (sel === "[data-history]") return { dataset: { history: IDS.ORDER_1003 } };
        return null;
      },
    },
  });
  assert.equal(rail.snapshot().models.order.record.name, before);
  assert.equal(rail.snapshot().peekedHistoryId, IDS.ORDER_1003);
  assert.equal(peeks[0].orderId, IDS.ORDER_1003);
});

test("rail error copy is isolated and Retry reloads one tissue", async () => {
  const base = createFixtureShop();
  let returnsCalls = 0;
  const shop = {
    ...base,
    getReturns(req) {
      returnsCalls += 1;
      if (returnsCalls === 1) throw new Error("down");
      return base.getReturns(req);
    },
  };
  const rail = createRailOrgan({ shop, mailbox: createMailbox() });
  await rail.load({ shop: SHOP, customerId: IDS.ADA, orderId: IDS.ORDER_1001, ticketId: "t-ada-track" });
  assert.equal(rail.snapshot().models.returns.ok, false);
  const host = { innerHTML: "", onclick: null };
  rail.mount(host);
  assert.match(host.innerHTML, /Couldn(?:'|&#39;)t load Returns\. Retry\./);
  assert.match(host.innerHTML, /Ada Demo/);
  assert.match(host.innerHTML, /Oak Demo Rattle/);
  await rail.retry("returns");
  assert.equal(rail.snapshot().models.returns.ok, true);
  assert.equal(rail.snapshot().models.returns.inProgress, true);
});

test("rail fails the PR if an Edit or write control ships", async () => {
  const rail = createRailOrgan({ shop, mailbox: createMailbox() });
  await rail.load({ shop: SHOP, customerId: IDS.ADA, orderId: IDS.ORDER_1001, ticketId: "t-ada-track" });
  const html = `<aside data-pane="rail">${rail.render()}</aside>`;
  assert.deepEqual(railWriteControlHits(html), []);
  assert.doesNotMatch(html, /<(button|a)\b[^>]*>\s*(?:Customer\s+)?Edit\b/i);
  assert.doesNotMatch(html, /Duplicate|Create order|data-edit|customerUpdate/i);
  assert.match(html, /Status Open/);
  assert.doesNotMatch(html, /Status OPEN/);
  assert.equal(railWriteControlHits(`${html}<button>Edit</button>`).includes("Edit"), true);
  assert.equal(railWriteControlHits(`${html}<a href="#">Customer Edit</a>`).includes("Edit"), true);
});

test("shipment Track is a separate control and the number stays mono text", async () => {
  const order = await shop.getOrder({ shop: SHOP, orderId: IDS.ORDER_1001 });
  const html = renderOrder(projectOrder(order), { shipmentOpen: true, etaOpen: true });
  assert.match(html, /<h3>ETA<\/h3>[\s\S]*<h3>Shipment<\/h3>/);
  assert.match(html, /<span class="ship-company">Demo Carrier<\/span>/);
  assert.match(html, /<span class="mono ship-number">DEMO-1001<\/span>/);
  assert.match(html, /<a class="track-link"[^>]*>Track<\/a>/);
  assert.doesNotMatch(html, /<a class="track-link"[^>]*>[^<]*Demo Carrier[^<]*DEMO-1001/);
  assert.doesNotMatch(html, /<a class="track-link"[^>]*>[^<]*DEMO-1001/);
  const noUrl = projectOrder({
    ...orders[IDS.ORDER_1001],
    fulfillments: [{
      displayStatus: "IN_TRANSIT",
      trackingInfo: [{ company: "Demo Carrier", number: "DEMO-1001" }],
    }],
  });
  const noUrlHtml = renderOrder(noUrl, { shipmentOpen: true });
  assert.match(noUrlHtml, /<span class="mono ship-number">DEMO-1001<\/span>/);
  assert.doesNotMatch(noUrlHtml, /class="track-link"/);
  assert.doesNotMatch(noUrlHtml, />Track</);
});

test("every customer-rail button carries a hover title", async () => {
  const untitled = (html) => (html.match(/<button\b[^>]*>/g) || []).filter((tag) => !/\stitle="/.test(tag));
  const rail = createRailOrgan({ shop, mailbox: createMailbox() });
  await rail.load({ shop: SHOP, customerId: IDS.ADA, orderId: IDS.ORDER_1001, ticketId: "t-ada-track" });
  const adaHtml = rail.render();
  await rail.load({ shop: SHOP, customerId: IDS.CASEY, orderId: IDS.ORDER_1002, ticketId: "t-casey-visor" });
  const caseyHtml = rail.render();
  const base = createFixtureShop();
  const throwing = { ...base, getReturns: () => { throw new Error("down"); } };
  const errRail = createRailOrgan({ shop: throwing, mailbox: createMailbox() });
  await errRail.load({ shop: SHOP, customerId: IDS.ADA, orderId: IDS.ORDER_1001, ticketId: "t-err-returns" });
  const variants = [
    ["ada rail", adaHtml],
    ["casey rail", caseyHtml],
    ["error rail", errRail.render()],
    ["no customer", renderCustomer(projectCustomer(null))],
    ["no order", renderOrder(projectOrder(null))],
    ["empty returns", renderReturns(projectReturns(null))],
  ];
  const missing = variants.flatMap(([name, html]) => untitled(html).map((tag) => `${name}: ${tag}`));
  assert.deepEqual(missing, []);
  assert.ok((adaHtml.match(/<button\b/g) || []).length > 10, "fixture should exercise many rail buttons");
});

test("shop tissue has no mutation surface", () => {
  assert.equal("getCustomer" in shop && "getOrder" in shop, true);
  assert.equal("mutate" in shop, false);
  assert.equal("createRefund" in shop, false);
  assert.equal("cancelOrder" in shop, false);
  assert.equal(orders[IDS.ORDER_1001].lineItems.nodes[0].sku, null);
});
