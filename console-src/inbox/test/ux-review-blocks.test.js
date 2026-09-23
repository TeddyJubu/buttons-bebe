import { createFixtureShop } from "../js/shop/fixture-shop.js";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { orders } from "../js/fixtures/demo-inbox.js";
import { createInboxOrgan as createProductionInbox } from "../js/inbox.js";
import {
  displayedSkus,
  reviewBlockViolations,
  toggleExpanded,
} from "../js/review-blocks.js";
import { formatSku } from "../js/util.js";

const here = dirname(fileURLToPath(import.meta.url));

function chrome() {
  return [
    readFileSync(join(here, "../index.html"), "utf8"),
    readFileSync(join(here, "../styles.css"), "utf8"),
  ].join("\n");
}

test("UX Pro blocks fail the default Ada paint", async () => {
  const organ = createInboxOrgan({ viewId: "mine" });
  const snap = await organ.ready();
  assert.deepEqual(reviewBlockViolations(snap.html), []);
  assert.equal(toggleExpanded(snap.html, "customer"), true);
  assert.equal(toggleExpanded(snap.html, "order"), true);
  assert.equal(toggleExpanded(snap.html, "returns"), true);
  assert.equal(toggleExpanded(snap.html, "order-history"), false);
  assert.equal(toggleExpanded(snap.html, "addresses"), false);
  assert.match(snap.html, /data-tissue="returns"[^>]*data-open="true"/);
  assert.match(snap.html, /In transit · 1 item/);
  assert.doesNotMatch(snap.html, /<h2>Returns<\/h2>\s*<span class="peek">No returns<\/span>/);
  assert.match(snap.html, /data-tissue="order-history"[^>]*data-open="false"/);
  assert.match(snap.html, /<h2>Past orders<\/h2>\s*<span class="peek">0 orders<\/span>/);
  assert.deepEqual(displayedSkus(snap.html), []);
  assert.doesNotMatch(snap.html, /data-sku=/);
  assert.doesNotMatch(snap.html, /<th>SKU<\/th>/);
  assert.doesNotMatch(snap.html, /data-sku="null"/);
  assert.doesNotMatch(snap.html, />\s*null\s*</);
  assert.match(snap.html, /<h3>Addresses<\/h3>\s*<span class="peek">No billing<\/span>/);
  assert.equal(toggleExpanded(snap.html, "giftCards"), true);
  assert.equal(toggleExpanded(snap.html, "discounts"), true);
  assert.equal(toggleExpanded(snap.html, "invoice"), true);
  assert.equal(toggleExpanded(snap.html, "warranty"), true);
  assert.equal(toggleExpanded(snap.html, "eta"), true);
  assert.match(snap.html, /<h3>Gift cards<\/h3>\s*<span class="peek">••••4291<\/span>/);
  assert.match(snap.html, /<h3>Discounts<\/h3>\s*<span class="peek">WELCOME10<\/span>/);
  assert.match(snap.html, /<h3>Invoice<\/h3>\s*<span class="peek">Invoice<\/span>/);
  assert.match(snap.html, /<a class="invoice-link"[^>]*>Invoice<\/a>/);
  assert.match(snap.html, /<h3>Warranty<\/h3>\s*<span class="peek">1 year · Active<\/span>/);
  assert.match(snap.html, /<p class="mute warranty-line">Ends 12 Mar 2027<\/p>/);
  assert.match(snap.html, /<h3>ETA<\/h3>\s*<span class="peek">ETA Tue 8 Sep<\/span>/);
  assert.match(snap.html, /<p class="mute eta-line">Zone: Domestic<\/p>/);
  assert.match(snap.html, /status-line[^>]*>[\s\S]*?Open</);
  assert.match(snap.html, /Status Open/);
  assert.doesNotMatch(snap.html, /status-line">OPEN</);
  assert.doesNotMatch(snap.html, /Status OPEN/);
  assert.doesNotMatch(snap.html, /<(button|a)\b[^>]*>\s*(?:Customer\s+)?Edit\b/i);
});

test("UX Pro mute unsubscribe chrome has no Shopify write control", async () => {
  const organ = createInboxOrgan({ viewId: "mine", ticketId: "t-priya-unsub" });
  const snap = await organ.ready();
  assert.deepEqual(reviewBlockViolations(snap.html), []);
  assert.match(snap.html, /class="ticket-badge ticket-request"[^>]*>Unsubscribe</);
  assert.match(snap.html, /class="thread-request mute"[^>]*>Marketing unsubscribe</);
  assert.match(snap.html, /data-marketing-gate-open[^>]*>Mark unsubscribed</);
  assert.match(snap.html, /data-pane="rail"[\s\S]*?<section class="rail-card"[^>]*data-tissue="customer"/);
  assert.doesNotMatch(snap.html, /data-tissue="preference"/);
  assert.doesNotMatch(snap.html, /<(button|a)\b[^>]*>\s*Unsubscribe</i);
  assert.doesNotMatch(snap.html, /<(button|a)\b[^>]*>[^<]*(?:opt out|marketing consent)/i);
  assert.doesNotMatch(snap.html, /#6B46C1|#7C3AED|#5B21B6/);
  const gated = organ.openMarketingGate();
  assert.match(gated.html, /id="gate-sheet-copy">Marketing consent stays locked\. No live unsubscribe\.</);
  assert.match(gated.html, /data-unsubscribe-handled[^>]*>Confirm</);
  const css = readFileSync(join(here, "../styles.css"), "utf8");
  assert.match(css, /\.ticket-request\s*\{[^}]*color:\s*var\(--mute\)/);
  assert.match(css, /\.thread-request\s*\{/);
});

test("UX Pro mute bug severity chrome has no Shopify write control", async () => {
  const snap = await createInboxOrgan({ viewId: "mine", ticketId: "t-remy-bug" }).ready();
  assert.deepEqual(reviewBlockViolations(snap.html), []);
  assert.match(snap.html, /class="ticket-badge ticket-request"[^>]*>Bug</);
  assert.match(snap.html, /class="ticket-badge ticket-severity"[^>]*>High</);
  assert.match(snap.html, /class="thread-request mute"[^>]*>Bug report</);
  assert.match(snap.html, /thread-request-subtype mute">High · iOS</);
  assert.match(snap.html, /data-bug-handled[^>]*>Mark bug handled</);
  assert.match(snap.html, /data-pane="rail"[\s\S]*?<section class="rail-card"[^>]*data-tissue="customer"/);
  assert.doesNotMatch(snap.html, /data-tissue="preference"/);
  assert.doesNotMatch(snap.html, /<h2>Bug report<\/h2>/);
  assert.doesNotMatch(snap.html, />Payments locked</);
  assert.doesNotMatch(snap.html, /<(button|a)\b[^>]*>[^<]*(?:productUpdate|severity|device)/i);
  assert.doesNotMatch(snap.html, /#6B46C1|#7C3AED|#5B21B6/);
  const css = readFileSync(join(here, "../styles.css"), "utf8");
  assert.match(css, /\.ticket-severity\s*\{[^}]*color:\s*var\(--mute\)/);
  assert.doesNotMatch(css, /\.ticket-severity\s*\{[^}]*background/);
  const ada = await createInboxOrgan({ viewId: "mine", ticketId: "t-ada-track" }).ready();
  assert.doesNotMatch(ada.html, /data-ticket="t-ada-track"[^>]*data-severity/);
  assert.doesNotMatch(ada.html, /data-tissue="preference"/);
  assert.doesNotMatch(ada.html, /class="thread-request mute"/);
});

test("UX Pro mute privacy chrome has no Shopify write control", async () => {
  const organ = createInboxOrgan({ viewId: "mine", ticketId: "t-lee-privacy" });
  const snap = await organ.ready();
  assert.deepEqual(reviewBlockViolations(snap.html), []);
  assert.match(snap.html, /class="ticket-badge ticket-request"[^>]*>Privacy</);
  assert.match(snap.html, /class="thread-request mute"[^>]*>Privacy request</);
  assert.match(snap.html, /thread-request-subtype mute">Delete</);
  assert.match(snap.html, /data-privacy-gate-open[^>]*>Mark privacy handled</);
  assert.match(snap.html, /data-pane="rail"[\s\S]*?<section class="rail-card"[^>]*data-tissue="customer"/);
  assert.doesNotMatch(snap.html, /data-tissue="preference"/);
  assert.doesNotMatch(snap.html, /<(button|a)\b[^>]*>[^<]*(?:erasure|redact|Customer Privacy)/i);
  assert.doesNotMatch(snap.html, /#6B46C1|#7C3AED|#5B21B6/);
  const gated = organ.openPrivacyGate();
  assert.match(gated.html, /data-privacy-gate/);
  assert.match(gated.html, /id="gate-sheet-copy">Privacy tools stay locked\. No live data erase or export\.</);
  assert.match(gated.html, /data-privacy-handled[^>]*>Confirm</);
  assert.doesNotMatch(gated.html, /#6B46C1|#7C3AED|#5B21B6/);
});

test("UX Pro blocks fail Casey and Jordan default paints", async () => {
  for (const opts of [
    { viewId: "unassigned", ticketId: "t-casey-visor" },
    { viewId: "unassigned", ticketId: "t-casey-throw" },
    { viewId: "snoozed", ticketId: "t-jordan-ship" },
  ]) {
    const snap = await createInboxOrgan(opts).ready();
    assert.deepEqual(reviewBlockViolations(snap.html), [], JSON.stringify(opts));
    assert.equal(toggleExpanded(snap.html, "returns"), false);
    assert.equal(toggleExpanded(snap.html, "order-history"), false);
    if (toggleExpanded(snap.html, "addresses") != null) {
      assert.equal(toggleExpanded(snap.html, "addresses"), false);
    }
    assert.match(snap.html, /<h2>Returns<\/h2>\s*<span class="peek">No returns<\/span>/);
    if (opts.ticketId === "t-casey-visor") {
      assert.match(snap.html, /<h3>Shipment<\/h3>\s*<span class="peek">No tracking<\/span>/);
      assert.equal(toggleExpanded(snap.html, "shipment"), false);
      assert.match(snap.html, /data-line-fulfill="Unfulfilled">Unfulfilled</);
      assert.equal(toggleExpanded(snap.html, "giftCards"), false);
      assert.equal(toggleExpanded(snap.html, "discounts"), false);
      assert.equal(toggleExpanded(snap.html, "invoice"), false);
      assert.equal(toggleExpanded(snap.html, "warranty"), false);
      assert.equal(toggleExpanded(snap.html, "eta"), false);
      assert.match(snap.html, /<h3>Gift cards<\/h3>\s*<span class="peek">No gift cards<\/span>/);
      assert.match(snap.html, /<h3>Discounts<\/h3>\s*<span class="peek">No discounts<\/span>/);
      assert.match(snap.html, /<h3>Invoice<\/h3>\s*<span class="peek">No invoice<\/span>/);
      assert.match(snap.html, /<h3>Warranty<\/h3>\s*<span class="peek">No warranty<\/span>/);
      assert.match(snap.html, /<h3>ETA<\/h3>\s*<span class="peek">No ETA<\/span>/);
    }
  }
});

test("closed Ada keeps the OPEN return open and hides no review-block wall", async () => {
  const snap = await createInboxOrgan({ viewId: "closed", ticketId: "t-ada-closed" }).ready();
  assert.deepEqual(reviewBlockViolations(snap.html), []);
  assert.equal(toggleExpanded(snap.html, "returns"), true);
  assert.equal(toggleExpanded(snap.html, "order-history"), false);
  assert.equal(toggleExpanded(snap.html, "addresses"), false);
  assert.doesNotMatch(snap.html, /data-send-close/);
});

test("empty returns stay collapsed with No returns peek", async () => {
  const snap = await createInboxOrgan({ viewId: "unassigned", ticketId: "t-casey-visor" }).ready();
  assert.equal(snap.rail.models.returns.record.returns.nodes.length, 0);
  assert.equal(snap.rail.models.returns.inProgress, false);
  assert.equal(snap.rail.open.returns, false);
  assert.match(snap.html, /data-toggle="returns"[^>]*aria-expanded="false"/);
  assert.match(snap.html, /<h2>Returns<\/h2>\s*<span class="peek">No returns<\/span>/);
  assert.match(snap.html, /data-tissue="returns"[^>]*>[\s\S]*?<div class="rail-body" hidden/);
});

test("OPEN return on Ada default-opens Returns", async () => {
  const snap = await createInboxOrgan({ viewId: "mine" }).ready();
  assert.equal(snap.rail.models.returns.record.status, "OPEN");
  assert.equal(snap.rail.models.returns.record.returns.nodes[0].status, "OPEN");
  assert.equal(snap.rail.models.returns.inProgress, true);
  assert.equal(snap.rail.open.returns, true);
  assert.match(snap.html, /data-toggle="returns"[^>]*aria-expanded="true"/);
  assert.match(snap.html, /<h2>Returns<\/h2>\s*<span class="peek">In transit · 1 item<\/span>/);
  assert.doesNotMatch(snap.html, /IN_PROGRESS/);
});

test("past orders stay collapsed with n orders in the header", async () => {
  const snap = await createInboxOrgan({ viewId: "unassigned", ticketId: "t-casey-visor" }).ready();
  assert.equal(snap.rail.open["order-history"], false);
  assert.match(snap.html, /data-toggle="order-history"[^>]*aria-expanded="false"/);
  assert.match(snap.html, /<h2>Past orders<\/h2>\s*<span class="peek">1 order<\/span>/);
  assert.match(snap.html, /data-tissue="order-history"[^>]*>[\s\S]*?<div class="rail-body" hidden/);
});

test("null SKUs stay null on the fixture and never print", () => {
  for (const order of Object.values(orders)) {
    for (const item of order.lineItems.nodes) {
      assert.equal(item.sku, null);
      assert.equal(formatSku(item.sku), "");
    }
  }
  assert.equal(formatSku("null"), "");
  assert.equal(formatSku("undefined"), "");
  assert.notEqual(formatSku(null), "null");
  assert.notEqual(formatSku(null), "—");
});

test("inbox fixtures are invented and do not name a live shop", () => {
  const tree = [
    readFileSync(join(here, "../index.html"), "utf8"),
    readFileSync(join(here, "../js/fixtures/demo-inbox.js"), "utf8"),
    readFileSync(join(here, "../js/inbox.js"), "utf8"),
    readFileSync(join(here, "../js/shop/fixture-shop.js"), "utf8"),
  ].join("\n");
  assert.doesNotMatch(tree, /Cute Things/i);
  assert.doesNotMatch(tree, /yznyc1-ez/);
  assert.doesNotMatch(tree, /myshopify\.com/);
  assert.doesNotMatch(tree, /7131035/);
  assert.doesNotMatch(tree, /Handcrafted Wooden Teether|Cashmere Knit Baby Blanket|Designer Linen Baby Sun Hat/);
});

test("chrome has no Gaia, Ask Gaia, or Gorgias purple", () => {
  const page = chrome();
  assert.doesNotMatch(page, /\bGaia\b/i);
  assert.doesNotMatch(page, /Ask Gaia/i);
  assert.doesNotMatch(page, /#6[Bb]46[Cc]1|#7[Cc]3[Aa][Ee][Dd]|#5[Bb]21[Bb]6|#7[Cc]4[Dd][Ff][Ff]/);
  assert.doesNotMatch(page, /#B5471D.*#6|#6.*#B5471D/);
  assert.match(page, /min-height:\s*40px/);
  assert.match(page, /\.draft-kicker[^}]*color:\s*var\(--mute\)/);
  assert.doesNotMatch(page, /\.draft-kicker[^}]*color:\s*var\(--accent\)/);
  assert.doesNotMatch(page, /\.status-line\s*\{[^}]*text-transform:\s*uppercase/);
});

test("review-block detector flags the wall and a printed null SKU", () => {
  const wall = `
    <button data-toggle="customer" aria-expanded="true"></button>
    <button data-toggle="order" aria-expanded="true"></button>
    <button data-toggle="returns" aria-expanded="true"></button>
    <button data-toggle="addresses" aria-expanded="true"></button>
    <button data-toggle="order-history" aria-expanded="true"></button>
    <button data-toggle="shipment" aria-expanded="true"></button>
    <span class="peek">No tracking</span>
    <button data-toggle="discounts" aria-expanded="true"></button>
    <span class="peek">No discounts</span>
    <button data-toggle="invoice" aria-expanded="true"></button>
    <span class="peek">No invoice</span>
    <button data-toggle="warranty" aria-expanded="true"></button>
    <span class="peek">No warranty</span>
    <button data-toggle="eta" aria-expanded="true"></button>
    <span class="peek">No ETA</span>
    <section data-tissue="returns"><span class="peek">No returns</span></section>
    <td class="mono" data-sku="null">null</td>
    <p class="mono line-sku" data-sku="—">—</p>
    <button>Ask Gaia</button>
    <aside data-pane="rail"><section data-tissue="preference"></section><button>Edit</button></aside>
    <style>:root{--acc:#6B46C1}</style>`;
  const hits = reviewBlockViolations(wall);
  assert.ok(hits.includes("fully-open rail wall"));
  assert.ok(hits.includes("empty returns open"));
  assert.ok(hits.includes("empty shipment open"));
  assert.ok(hits.includes("empty invoice open"));
  assert.ok(hits.includes("empty warranty open"));
  assert.ok(hits.includes("empty eta open"));
  assert.ok(hits.includes("past orders open by default"));
  assert.ok(hits.includes("literal null SKU"));
  assert.ok(hits.includes("em dash SKU"));
  assert.ok(hits.includes("Gaia"));
  assert.ok(hits.includes("Gorgias purple"));
  assert.ok(hits.includes("customer Edit"));
  assert.ok(hits.includes("fifth rail request-type tissue"));
});

test("UX Pro blocks absolute clock time in the list", async () => {
  const snap = await createInboxOrgan({ viewId: "mine" }).ready();
  assert.deepEqual(reviewBlockViolations(snap.html), []);
  assert.doesNotMatch(snap.html, /data-pane="list"[\s\S]*?<time class="ticket-time"[^>]*>[^<]*,\s*\d{1,2}:\d{2}</);
  const hits = reviewBlockViolations(`<aside data-pane="list"><time class="ticket-time">28 Aug, 15:10</time></aside>`);
  assert.ok(hits.includes("absolute list time"));
});

function createInboxOrgan(opts = {}) { return createProductionInbox({shop: createFixtureShop(), ...opts}); }
