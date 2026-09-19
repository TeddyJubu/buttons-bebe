import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { dirname, join, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { IDS, SHOP, tickets as fixtureTickets } from "../js/fixtures/demo-inbox.js";
import { createHelpdeskClient } from "../js/shop/helpdesk-client.js";
import { createHelpdeskShop, resolveLiveInbox } from "../js/shop/helpdesk-shop.js";
import { TOOL_NAMES, WRITE_TOOLS } from "../js/shop/helpdesk-tools.js";
import { LIVE_IDS, LIVE_SHOP, liveTickets } from "../js/shop/live-catalog.js";
import { createInboxOrgan as createProductionInbox } from "../js/inbox.js";
import { createFixtureShop, draftForRequestType, fixtureDraftFromThread } from "../js/shop/fixture-shop.js";
import { createMailbox } from "../js/mailbox.js";
import { createRailOrgan } from "../js/tissues/rail.js";
import { projectOrder, renderOrder } from "../js/tissues/order.js";
import { projectReturns } from "../js/tissues/returns.js";
import { projectOrderHistory } from "../js/tissues/order-history.js";

const here = dirname(fileURLToPath(import.meta.url));
const helpdeskRoot = join(here, "../../helpdesk-agent");
const selectedPython = process.env.INBOX_PYTHON || process.env.PYTHON || "python3";
const python = selectedPython.includes("/") ? resolve(process.cwd(), selectedPython) : selectedPython;

const SAMPLE_SHOP = "demo-helpdesk.example";
const SAMPLE_ADA = "gid://shopify/Customer/9001";
const SAMPLE_ADA_ORDER = "gid://shopify/Order/9001";
const SAMPLE_CASEY = "gid://shopify/Customer/9002";

function pythonInvoke(tool, args) {
  const argv = [python, "-m", "helpdesk"];
  if (tool === "helpdesk.list_tickets") {
    argv.push("list-tickets", "--view", String(args.view || "open"), "--limit", String(args.limit || 20));
  } else if (tool === "helpdesk.get_ticket") {
    argv.push("get-ticket", "--ticket-id", String(args.ticketId));
  } else if (tool === "helpdesk.get_customer") {
    argv.push("get-customer", "--shop", args.shop, "--customer-id", args.customerId);
  } else if (tool === "helpdesk.get_order") {
    argv.push("get-order", "--shop", args.shop, "--order-id", args.orderId);
  } else if (tool === "helpdesk.get_returns") {
    argv.push("get-returns", "--shop", args.shop, "--order-id", args.orderId);
  } else if (tool === "helpdesk.list_past_orders") {
    argv.push("list-past-orders", "--shop", args.shop, "--customer-id", args.customerId);
  } else if (tool === "helpdesk.draft_reply") {
    argv.push("draft-reply", "--ticket", String(args.ticketId));
    if (args.shop) argv.push("--shop", args.shop);
  } else if (tool === "helpdesk.summarize_thread") {
    argv.push("summarize-thread", "--ticket", String(args.ticketId));
    if (args.shop) argv.push("--shop", args.shop);
  } else if (tool === "helpdesk.search_macros") {
    argv.push("search-macros", "--query", String(args.query || ""));
  } else if (tool === "helpdesk.apply_macro") {
    argv.push("apply-macro", "--macro-id", String(args.macroId), "--mode", String(args.mode || "replace"));
    if (args.currentBody) argv.push("--current-body", String(args.currentBody));
  } else if (tool === "helpdesk.ingest_email") {
    argv.push(
      "ingest-email",
      "--from", String(args.from),
      "--subject", String(args.subject),
      "--body", String(args.body),
      "--received-at", String(args.receivedAt),
    );
  } else if (tool === "helpdesk.ingest_chat") {
    argv.push(
      "ingest-chat",
      "--from-name", String(args.fromName),
      "--body", String(args.body),
      "--received-at", String(args.receivedAt),
    );
  } else if (tool === "helpdesk.pull_mailbox") {
    argv.push("pull-mailbox", "--limit", String(args.limit || 20));
  } else if (tool === "helpdesk.escalate_ticket") {
    argv.push("escalate-ticket", "--ticket-id", String(args.ticketId));
    if (args.reason) argv.push("--reason", String(args.reason));
  } else if (tool === "helpdesk.write_gate_status") {
    argv.push("write-gate-status");
  } else if (tool === "helpdesk.bridge_status") {
    argv.push("bridge-status");
  } else if (tool === "helpdesk.send_reply") {
    argv.push("send-reply", "--ticket-id", String(args.ticketId), "--text", String(args.text));
    if (args.confirmed) argv.push("--confirmed");
    if (args.close) argv.push("--close");
  } else {
    throw new Error(`unknown tool ${tool}`);
  }
  const result = spawnSync(argv[0], argv.slice(1), {
    cwd: helpdeskRoot,
    encoding: "utf8",
    env: {
      ...process.env,
      PYTHONPATH: helpdeskRoot,
      HELPDESK_SOURCE: args._source || "sample",
    },
  });
  assert.equal(result.error, undefined, result.stderr);
  const payload = JSON.parse(result.stdout);
  payload._exit = result.status;
  return payload;
}

function clientFromPython(source = "sample") {
  return createHelpdeskClient({
    invoke(tool, args) {
      return pythonInvoke(tool, { ...args, _source: source });
    },
  });
}

test("client exposes exactly the seventeen helpdesk tools", () => {
  const client = createHelpdeskClient({ invoke: async () => ({ ok: true }) });
  assert.deepEqual(client.tools, TOOL_NAMES);
  assert.equal(TOOL_NAMES.length, 17);
  assert.ok(TOOL_NAMES.includes("helpdesk.list_tickets"));
  assert.ok(TOOL_NAMES.includes("helpdesk.get_ticket"));
  assert.ok(TOOL_NAMES.includes("helpdesk.draft_reply"));
  assert.ok(TOOL_NAMES.includes("helpdesk.summarize_thread"));
  assert.ok(TOOL_NAMES.includes("helpdesk.search_macros"));
  assert.ok(TOOL_NAMES.includes("helpdesk.apply_macro"));
  assert.ok(TOOL_NAMES.includes("helpdesk.ingest_email"));
  assert.ok(TOOL_NAMES.includes("helpdesk.ingest_chat"));
  assert.ok(TOOL_NAMES.includes("helpdesk.pull_mailbox"));
  assert.ok(TOOL_NAMES.includes("helpdesk.escalate_ticket"));
  assert.ok(TOOL_NAMES.includes("helpdesk.write_gate_status"));
  assert.ok(TOOL_NAMES.includes("helpdesk.bridge_status"));
  assert.ok(TOOL_NAMES.includes("helpdesk.send_reply"));
  assert.deepEqual([...WRITE_TOOLS], ["helpdesk.send", "helpdesk.refund", "helpdesk.cancel"]);
  for (const name of WRITE_TOOLS) {
    assert.ok(!TOOL_NAMES.includes(name));
  }
});

test("list_tickets rows include customerId and orderId GIDs", async () => {
  const listed = pythonInvoke("helpdesk.list_tickets", { view: "open", limit: 20 });
  assert.equal(listed.ok, true);
  const ada = listed.tickets.find((row) => row.id === "t-ada-track");
  assert.ok(ada);
  assert.equal(ada.customerName, "Ada Demo");
  assert.equal(ada.displayName, undefined);
  assert.equal(ada.customerId, SAMPLE_ADA);
  assert.equal(ada.orderId, SAMPLE_ADA_ORDER);
  assert.match(ada.customerId, /^gid:\/\/shopify\/Customer\//);
  assert.match(ada.orderId, /^gid:\/\/shopify\/Order\//);
  assert.equal(ada.status, "open");
  assert.notEqual(ada.status, "OPEN");
  for (const row of listed.tickets) {
    assert.ok(["open", "closed", "snoozed"].includes(row.status), row.status);
    assert.notEqual(row.status, "OPEN");
    assert.equal(row.displayName, undefined);
  }

  const shop = createHelpdeskShop({ client: clientFromPython("sample"), shop: SAMPLE_SHOP });
  const rows = await shop.listTickets({ view: "mine", limit: 10 });
  assert.equal(rows[0].id, "t-ada-track");
  assert.equal(rows[0].customerId, SAMPLE_ADA);
  assert.equal(rows[0].orderId, SAMPLE_ADA_ORDER);
  assert.equal(rows[0].requestType, null);
  const priya = listed.tickets.find((row) => row.id === "t-priya-unsub");
  assert.ok(priya);
  assert.equal(priya.requestType, "marketing_unsubscribe");
  assert.equal(priya.status, "open");
  const lee = listed.tickets.find((row) => row.id === "t-lee-privacy");
  assert.ok(lee);
  assert.equal(lee.requestType, "privacy_request");
  assert.equal(lee.status, "open");
  const remy = listed.tickets.find((row) => row.id === "t-remy-bug");
  assert.ok(remy);
  assert.equal(remy.requestType, "bug");
  assert.equal(remy.severity, "high");
  assert.equal(remy.device, "iOS");
  const casey = listed.tickets.find((row) => row.id === "t-casey-visor");
  assert.equal(casey.requestType, null);
  assert.equal(casey.severity, null);
  assert.equal(casey.device, null);
});

test("get_ticket returns messages and statusEvents", async () => {
  const payload = pythonInvoke("helpdesk.get_ticket", { ticketId: "1001" });
  assert.equal(payload.ok, true);
  const ticket = payload.ticket;
  assert.equal(ticket.id, "t-ada-track");
  assert.equal(ticket.customerName, "Ada Demo");
  assert.equal(ticket.displayName, undefined);
  assert.ok(Array.isArray(ticket.messages));
  assert.ok(ticket.messages.length >= 1);
  assert.ok(Array.isArray(ticket.statusEvents));
  assert.ok(ticket.statusEvents.length >= 1);
  assert.equal(ticket.status, "open");
  assert.notEqual(ticket.status, "OPEN");
  assert.equal(ticket.customerId, SAMPLE_ADA);
  assert.equal(ticket.orderId, SAMPLE_ADA_ORDER);

  const shop = createHelpdeskShop({ client: clientFromPython("sample"), shop: SAMPLE_SHOP });
  const loaded = await shop.getTicket({ ticketId: "t-ada-closed" });
  assert.equal(loaded.status, "closed");
  assert.equal(loaded.statusEvents.at(-1).status, "closed");
  assert.notEqual(loaded.status, "OPEN");
});

test("ticket customerName is not displayName and status is not Return.status", async () => {
  const shop = createHelpdeskShop({ client: clientFromPython("sample"), shop: SAMPLE_SHOP });
  const ticket = await shop.getTicket({ ticketId: "t-ada-track" });
  const customer = await shop.getCustomer({ shop: SAMPLE_SHOP, customerId: SAMPLE_ADA });
  const returns = await shop.getReturns({ shop: SAMPLE_SHOP, orderId: SAMPLE_ADA_ORDER });
  assert.equal(ticket.customerName, "Ada Demo");
  assert.equal(ticket.displayName, undefined);
  assert.equal(customer.displayName, "Ada Demo");
  assert.equal(customer.customerName, undefined);
  assert.equal(ticket.status, "open");
  assert.equal(returns.returns.nodes[0].status, "OPEN");
  assert.notEqual(ticket.status, returns.returns.nodes[0].status);
});

test("inbox ingestEmail and ingestChat call the same intake tools", async () => {
  const calls = [];
  const shop = createHelpdeskShop({
    client: createHelpdeskClient({
      async invoke(tool, args) {
        calls.push(tool);
        if (tool === "helpdesk.ingest_email") {
          return {
            ok: true,
            spam: false,
            ticketId: "t-in-1",
            id: "t-in-1",
            customerName: "Ada",
            subject: args.subject,
            snippet: args.body,
            status: "open",
            updatedAt: args.receivedAt,
            customerId: LIVE_IDS.C_UNFULFILLED,
            orderId: LIVE_IDS.O_1001,
          };
        }
        if (tool === "helpdesk.ingest_chat") {
          return {
            ok: true,
            spam: false,
            ticketId: "t-in-2",
            id: "t-in-2",
            customerName: args.fromName,
            subject: args.body,
            snippet: args.body,
            status: "open",
            updatedAt: args.receivedAt,
            customerId: null,
            orderId: null,
          };
        }
        if (tool === "helpdesk.list_tickets") {
          return { ok: true, tickets: [] };
        }
        if (tool === "helpdesk.get_ticket") {
          return {
            ok: true,
            ticket: {
              id: args.ticketId,
              customerName: "Ada",
              subject: "Tracking",
              snippet: "",
              status: "open",
              updatedAt: "2026-08-30T14:02:00Z",
              customerId: LIVE_IDS.C_UNFULFILLED,
              orderId: LIVE_IDS.O_1001,
              messages: [],
              statusEvents: [],
            },
          };
        }
        return { ok: false };
      },
    }),
  });
  const organ = createInboxOrgan({ shop, viewId: "open" });
  const ingested = await organ.ingestEmail({
    from: "Ada <ada.tracking@example.com>",
    subject: "Tracking on order #1001 has not moved",
    body: "Where is my order #1001?",
    receivedAt: "2026-08-30T14:02:00Z",
  });
  assert.equal(ingested.id, "t-in-1");
  assert.ok(calls.includes("helpdesk.ingest_email"));
  const chat = await organ.ingestChat({
    fromName: "Sam",
    body: "The rattle is broken.",
    receivedAt: "2026-08-30T15:10:00Z",
  });
  assert.equal(chat.id, "t-in-2");
  assert.ok(calls.includes("helpdesk.ingest_chat"));
});

test("inbox pullMailbox calls helpdesk.pull_mailbox then list_tickets", async () => {
  const calls = [];
  const adaRow = {
    id: "t-in-1",
    customerName: "Ada",
    subject: "Tracking on order #1001 has not moved",
    snippet: "Where is my order #1001? The tracking has not updated.",
    status: "open",
    updatedAt: "2026-08-30T14:02:00Z",
    customerId: LIVE_IDS.C_UNFULFILLED,
    orderId: LIVE_IDS.O_1001,
  };
  const shop = createHelpdeskShop({
    client: createHelpdeskClient({
      async invoke(tool, args) {
        calls.push(tool);
        if (tool === "helpdesk.pull_mailbox") {
          return { ok: true, ingested: [adaRow], spam: [{ from: "Prize Desk <winner@prize-farm.example>", subject: "You won a $10,000 prize!" }], skipped: 0 };
        }
        if (tool === "helpdesk.list_tickets") {
          return { ok: true, tickets: [adaRow] };
        }
        if (tool === "helpdesk.get_ticket") {
          return {
            ok: true,
            ticket: {
              ...adaRow,
              messages: [{ id: "m1", fromAgent: false, name: "Ada", body: adaRow.snippet, at: adaRow.updatedAt }],
              statusEvents: [],
            },
          };
        }
        if (tool === "helpdesk.draft_reply") {
          return { ok: true, source: "fixture", draft: "Hi Ada — I looked at #1001." };
        }
        if (tool === "helpdesk.search_macros") {
          return { ok: true, macros: [] };
        }
        return { ok: false };
      },
    }),
  });
  const organ = createInboxOrgan({ shop, viewId: "open" });
  const pulled = await organ.pullMailbox({ limit: 5 });
  assert.equal(pulled.ingested[0].id, "t-in-1");
  assert.equal(pulled.ingested[0].customerName, "Ada");
  assert.ok(calls.includes("helpdesk.pull_mailbox"));
  assert.ok(calls.includes("helpdesk.list_tickets"));
  assert.ok(!calls.includes("helpdesk.ingest_email"));
  const snap = organ.snapshot();
  assert.equal(snap.selectedId, "t-in-1");
  assert.match(snap.html, /Tracking on order #1001/);
  assert.doesNotMatch(snap.html, /You won a \$10,000 prize/);
});

test("inbox panes call list_tickets and get_ticket on invoke", async () => {
  const calls = [];
  const shop = createHelpdeskShop({
    client: createHelpdeskClient({
      async invoke(tool, args) {
        calls.push(tool);
        if (tool === "helpdesk.list_tickets") {
          return {
            ok: true,
            tickets: [{
              id: "t-ada-track",
              customerName: "Ada Demo",
              subject: "Tracking on order #1001 has not moved",
              snippet: "Where is my order #1001? The tracking has not updated.",
              status: "open",
              updatedAt: "2026-08-28T15:10:00Z",
              customerId: IDS.ADA,
              orderId: IDS.ORDER_1001,
            }],
          };
        }
        if (tool === "helpdesk.get_ticket") {
          return {
            ok: true,
            ticket: {
              id: args.ticketId,
              customerName: "Ada Demo",
              subject: "Tracking on order #1001 has not moved",
              snippet: "Where is my order #1001? The tracking has not updated.",
              status: "open",
              updatedAt: "2026-08-28T15:10:00Z",
              customerId: IDS.ADA,
              orderId: IDS.ORDER_1001,
              messages: fixtureTickets[0].messages,
              statusEvents: fixtureTickets[0].statusEvents,
            },
          };
        }
        return { ok: false };
      },
    }),
  });
  const organ = createInboxOrgan({ shop, viewId: "mine" });
  const snap = await organ.ready();
  assert.ok(calls.includes("helpdesk.list_tickets"));
  assert.ok(calls.includes("helpdesk.get_ticket"));
  assert.equal(snap.selectedId, "t-ada-track");
  assert.match(snap.html, /Ada Demo/);
  assert.match(snap.html, /status-line">Open · Friday/);
});

test("CLI payloads match the JS shop adapter for sample rail tools", async () => {
  const shop = createHelpdeskShop({ client: clientFromPython("sample"), shop: SAMPLE_SHOP });
  const customer = await shop.getCustomer({ shop: SAMPLE_SHOP, customerId: SAMPLE_ADA });
  assert.equal(customer.displayName, "Ada Demo");
  assert.equal(customer.defaultEmailAddress.emailAddress, "ada@demo-helpdesk.example");
  assert.equal(typeof customer.numberOfOrders, "string");
  assert.equal(customer.email, undefined);

  const order = await shop.getOrder({ shop: SAMPLE_SHOP, orderId: SAMPLE_ADA_ORDER });
  assert.equal(order.billingAddress, null);
  assert.equal(order.lineItems.nodes[0].sku, undefined);
  assert.ok(order.currentTotalPriceSet.shopMoney);
  const projected = projectOrder(order);
  assert.equal(projected.addressPeek, "No billing");
  assert.equal(projected.skuLabels[0], "");

  const returns = await shop.getReturns({ shop: SAMPLE_SHOP, orderId: SAMPLE_ADA_ORDER });
  assert.equal(returns.returns.nodes[0].status, "OPEN");
  assert.equal(returns.inProgress, true);
  assert.equal(returns.orderReturnStatus, "IN_PROGRESS");

  const history = await shop.getOrderHistory({ shop: SAMPLE_SHOP, customerId: SAMPLE_CASEY });
  assert.deepEqual(history.map((row) => row.name), ["#9003", "#9002"]);
  assert.ok(history[0].currentTotalPriceSet.shopMoney);
  assert.equal(history[0].total, undefined);
  assert.equal(projectOrderHistory(history).rows[0].fulfillmentStatus, history[0].displayFulfillmentStatus);
});

test("all live CLI tools return ok on the same handler path", () => {
  const cases = [
    ["helpdesk.list_tickets", { view: "open", limit: 5 }],
    ["helpdesk.get_ticket", { ticketId: "1001" }],
    ["helpdesk.get_customer", { shop: SAMPLE_SHOP, customerId: SAMPLE_ADA }],
    ["helpdesk.get_order", { shop: SAMPLE_SHOP, orderId: SAMPLE_ADA_ORDER }],
    ["helpdesk.get_returns", { shop: SAMPLE_SHOP, orderId: SAMPLE_ADA_ORDER }],
    ["helpdesk.list_past_orders", { shop: SAMPLE_SHOP, customerId: SAMPLE_ADA }],
    ["helpdesk.draft_reply", { ticketId: "1001", shop: SAMPLE_SHOP }],
    ["helpdesk.summarize_thread", { ticketId: "1001" }],
    ["helpdesk.search_macros", { query: "shipping" }],
    ["helpdesk.apply_macro", { macroId: "shipping-delay", mode: "replace" }],
    ["helpdesk.ingest_email", {
      from: "Ada <ada.tracking@example.com>",
      subject: "Tracking on order #1001 has not moved",
      body: "Where is my order #1001? The tracking has not updated.",
      receivedAt: "2026-08-30T14:02:00Z",
    }],
    ["helpdesk.ingest_chat", {
      fromName: "Ada",
      body: "Any update on #1001? Tracking looks stuck.",
      receivedAt: "2026-08-30T15:02:00Z",
    }],
    ["helpdesk.pull_mailbox", { limit: 5 }],
    ["helpdesk.escalate_ticket", { ticketId: "t-ada-track" }],
    ["helpdesk.write_gate_status", {}],
    ["helpdesk.bridge_status", {}],
  ];
  for (const [tool, args] of cases) {
    const payload = pythonInvoke(tool, args);
    assert.equal(payload.ok, true, tool);
    assert.equal(payload.tool, tool);
  }
  const humanOnly = pythonInvoke("helpdesk.send_reply", {
    ticketId: "t-ada-track",
    text: "Hi",
    confirmed: true,
  });
  assert.equal(humanOnly.ok, false);
  assert.equal(humanOnly.error, "human_only");
});

test("live-holes returns stay empty and never inherit Ada OPEN", async () => {
  const shop = createHelpdeskShop({
    client: clientFromPython("live-holes"),
    shop: LIVE_SHOP,
  });
  const returns = await shop.getReturns({ shop: LIVE_SHOP, orderId: LIVE_IDS.O_1001 });
  assert.deepEqual(returns.returns.nodes, []);
  assert.equal(returns.inProgress, false);
  assert.equal(returns.orderReturnStatus, "NO_RETURN");
  assert.equal(projectReturns(returns).peek, "No returns");
  assert.equal(projectReturns(returns).collapsedDefault, true);

  const order = await shop.getOrder({ shop: LIVE_SHOP, orderId: LIVE_IDS.O_1001 });
  assert.equal(order.billingAddress, null);
  assert.equal(order.lineItems.nodes[0].sku, undefined);
  const projected = projectOrder(order);
  assert.equal(projected.addressPeek, "No billing");
  assert.equal(projected.hasTracking, false);
  assert.equal(projected.shipmentPeek, "No tracking");
  assert.equal(projected.lineFulfillLabels[0], "Unfulfilled");
  assert.equal(order.lineItems.nodes[0].unfulfilledQuantity, 1);
  assert.equal(order.fulfillments?.length || 0, 0);
  const html = renderOrder(projected);
  assert.match(html, /<h3>Shipment<\/h3>\s*<span class="peek">No tracking<\/span>/);
  assert.match(html, /<p class="tissue-empty">No tracking<\/p>/);
  assert.match(html, /data-line-fulfill="Unfulfilled">Unfulfilled</);
  assert.doesNotMatch(html, /AI-DEMO-|invented-track/i);
});

test("fixture fallback keeps Ada OPEN when helpdesk is down", async () => {
  const shop = createHelpdeskShop({
    client: createHelpdeskClient({
      invoke() {
        throw new Error("mint unavailable");
      },
    }),
  });
  const returns = await shop.getReturns({ shop: SHOP, orderId: IDS.ORDER_1001 });
  assert.equal(returns.returns.nodes[0].status, "OPEN");
  assert.equal(projectReturns(returns).peek, "In transit · 1 item");
  const customer = await shop.getCustomer({ shop: SHOP, customerId: IDS.ADA });
  assert.equal(customer.displayName, "Ada Demo");
});

test("default inbox organ still first-paints Ada fixtures when HTTP is down", async () => {
  const organ = createInboxOrgan({ viewId: "mine" });
  const snap = await organ.ready();
  assert.equal(snap.selectedId, "t-ada-track");
  assert.match(snap.html, /Ada Demo/);
  assert.match(snap.html, /In transit · 1 item/);
  assert.equal(snap.rail.open.returns, true);
  assert.equal(snap.rail.models.returns.record.returns.nodes[0].status, "OPEN");
});

test("live catalog has empty returns and does not replace This order on history peek", async () => {
  const shop = createHelpdeskShop({
    client: clientFromPython("live-holes"),
    shop: LIVE_SHOP,
  });
  const organ = createInboxOrgan({
    shop,
    shopHost: LIVE_SHOP,
    tickets: liveTickets,
    viewId: "unassigned",
    ticketId: "t-fulfilled",
  });
  const snap = await organ.ready();
  assert.equal(snap.rail.models.returns.inProgress, false);
  assert.equal(snap.rail.open.returns, false);
  assert.match(snap.html, /<h2>Returns<\/h2>\s*<span class="peek">No returns<\/span>/);
  assert.equal(snap.rail.models.order.record.name, "#1002");
  const names = snap.rail.models.history.rows.map((row) => row.name);
  assert.ok(names.includes("#1002"));
  assert.equal(snap.rail.currentOrderId, LIVE_IDS.O_1002);
});

test("ticket switch resets rail and does not leak a fixture OPEN onto live tickets", async () => {
  const fixtureShop = createFixtureShop();
  const liveShop = createHelpdeskShop({
    client: clientFromPython("live-holes"),
    shop: LIVE_SHOP,
  });
  const mailbox = createMailbox();
  const rail = createRailOrgan({ shop: fixtureShop, mailbox });
  await rail.load({ shop: SHOP, customerId: IDS.ADA, orderId: IDS.ORDER_1001, ticketId: "t-ada-track" });
  assert.equal(rail.snapshot().open.returns, true);

  const liveRail = createRailOrgan({ shop: liveShop, mailbox: createMailbox() });
  await liveRail.load({
    shop: LIVE_SHOP,
    customerId: LIVE_IDS.C_UNFULFILLED,
    orderId: LIVE_IDS.O_1001,
    ticketId: "t-unfulfilled",
  });
  assert.equal(liveRail.snapshot().open.returns, false);
  assert.equal(liveRail.snapshot().models.returns.inProgress, false);
  assert.equal(liveRail.snapshot().models.returns.peek, "No returns");
});

test("one helpdesk tissue failure isolates to its pane", async () => {
  const shop = createHelpdeskShop({
    client: createHelpdeskClient({
      async invoke(tool) {
        if (tool === "helpdesk.get_returns") return { ok: false, error: "shopify_error", message: "down" };
        throw new Error("offline");
      },
    }),
    fallback: createFixtureShop({ fail: { returns: "down" } }),
  });
  const organ = createInboxOrgan({ shop, viewId: "mine" });
  const snap = await organ.ready();
  assert.match(snap.html, /Where is my order #1001/);
  assert.match(snap.html, /Ada Demo/);
  assert.match(snap.html, /Oak Demo Rattle/);
  assert.match(snap.html, /Couldn(?:'|&#39;)t load Returns\. Retry\./);
  assert.equal(snap.rail.models.customer.ok, true);
  assert.equal(snap.rail.models.order.ok, true);
  assert.equal(snap.rail.models.returns.ok, false);
});

test("intake Ada #1001 first-paints a draft strip from draft_reply", async () => {
  const calls = [];
  const adaRow = {
    id: "t-in-1",
    customerName: "Ada",
    subject: "Tracking on order #1001 has not moved",
    snippet: "Where is my order #1001? The tracking has not updated.",
    status: "open",
    updatedAt: "2026-08-30T14:02:00Z",
    customerId: LIVE_IDS.C_UNFULFILLED,
    orderId: LIVE_IDS.O_1001,
  };
  const adaTicket = {
    ...adaRow,
    messages: [{
      id: "m-in-1",
      fromAgent: false,
      name: "Ada",
      body: "Where is my order #1001? The tracking has not updated.",
      at: "2026-08-30T14:02:00Z",
    }],
    statusEvents: [{ at: "2026-08-30T14:02:00Z", status: "open", note: "created" }],
  };
  const shop = createHelpdeskShop({
    shop: LIVE_SHOP,
    fallback: createFixtureShop({ fail: { draft: "no stubDraft fallback" } }),
    client: createHelpdeskClient({
      async invoke(tool, args) {
        calls.push(tool);
        if (tool === "helpdesk.ingest_email") {
          if (/prize|lottery/i.test(`${args.subject} ${args.body}`)) {
            return { ok: true, spam: true, ticketId: null };
          }
          return { ok: true, spam: false, ticketId: "t-in-1", id: "t-in-1", ...adaRow };
        }
        if (tool === "helpdesk.list_tickets") {
          return { ok: true, tickets: [adaRow] };
        }
        if (tool === "helpdesk.get_ticket") {
          return { ok: true, ticket: { ...adaTicket, id: args.ticketId } };
        }
        if (tool === "helpdesk.draft_reply") {
          return {
            ok: true,
            source: "fixture",
            draft: "Hi Ada — I looked at #1001. It is Paid and Unfulfilled. It has not been handed to a carrier yet. I will write back when it ships. Let me know if you need anything else.",
          };
        }
        if (tool === "helpdesk.get_customer") {
          return {
            ok: true,
            customer: {
              displayName: "Demo Unfulfilled",
              defaultEmailAddress: { emailAddress: "ai-demo-unfulfilled@example.com" },
            },
          };
        }
        if (tool === "helpdesk.get_order") {
          return {
            ok: true,
            order: {
              name: "#1001",
              displayFinancialStatus: "PAID",
              displayFulfillmentStatus: "UNFULFILLED",
              currentTotalPriceSet: { shopMoney: { amount: "28.00", currencyCode: "USD" } },
              lineItems: { nodes: [{ title: "Organic Cotton Baby Romper", sku: null, quantity: 1 }] },
              billingAddress: null,
              fulfillments: [],
            },
          };
        }
        if (tool === "helpdesk.get_returns") {
          return { ok: true, returns: { nodes: [] }, inProgress: false, items: [] };
        }
        if (tool === "helpdesk.list_past_orders") {
          return { ok: true, orders: [] };
        }
        if (tool === "helpdesk.search_macros") {
          return { ok: true, macros: [] };
        }
        return { ok: false };
      },
    }),
  });
  const organ = createInboxOrgan({ shop, shopHost: LIVE_SHOP, viewId: "open" });
  const ingested = await organ.ingestEmail({
    from: "Ada <ada.tracking@example.com>",
    subject: "Tracking on order #1001 has not moved",
    body: "Where is my order #1001? The tracking has not updated.",
    receivedAt: "2026-08-30T14:02:00Z",
  });
  assert.equal(ingested.id, "t-in-1");
  assert.ok(calls.includes("helpdesk.ingest_email"));
  assert.ok(calls.includes("helpdesk.draft_reply"));
  assert.ok(!calls.includes("helpdesk.send"));
  let snap = organ.snapshot();
  assert.equal(snap.selectedId, "t-in-1");
  assert.match(snap.html, /data-draft-strip/);
  assert.match(snap.html, /Hi Ada/);
  assert.doesNotMatch(snap.html, /Hi Demo/);
  const stripAt = snap.html.indexOf("data-draft-strip");
  const boxAt = snap.html.indexOf("composer-box");
  assert.ok(stripAt > -1 && boxAt > stripAt, "draft strip sits above the composer box");
  assert.equal(snap.sendDisabled, true);
  assert.equal(snap.sent.length, 0);

  organ.insertDraft();
  snap = organ.snapshot();
  assert.doesNotMatch(snap.html, /data-draft-strip/);
  assert.match(snap.html, /Hi Ada — I looked at #1001/);
  assert.equal(snap.sendDisabled, false);
  assert.equal(snap.sent.length, 0);

  const prize = await organ.ingestEmail({
    from: "Prize Desk <winner@prize-farm.example>",
    subject: "You won a $10,000 prize!",
    body: "Claim your lottery winnings today. Unsubscribe from this farm of cash prize emails.",
    receivedAt: "2026-08-30T14:16:00Z",
  });
  assert.equal(prize.spam, true);
  assert.equal(prize.ticketId, null);
  snap = organ.snapshot();
  assert.doesNotMatch(snap.html, /You won a \$10,000 prize/i);
  assert.doesNotMatch(snap.html, /lottery winnings/i);
  assert.ok(!calls.includes("helpdesk.send"));
});

test("fixture draft_reply branches on requestType for privacy, unsubscribe, and bug", () => {
  const invented = {
    name: "#1001",
    displayFinancialStatus: "PAID",
    displayFulfillmentStatus: "FULFILLED",
    fulfillments: [{ trackingInfo: [{ number: "DEMO-1001", company: "Demo Carrier" }] }],
  };
  const unsub = fixtureDraftFromThread({
    customerName: "Priya Lane",
    status: "open",
    requestType: "marketing_unsubscribe",
    messages: [{ fromAgent: false, name: "Priya Lane", body: "Please take me off the marketing list." }],
  }, { order: invented });
  assert.match(unsub, /Hi Priya/);
  assert.match(unsub, /marketing unsubscribe/i);
  assert.match(unsub, /preference/i);
  assert.match(unsub, /out of band/i);
  assert.doesNotMatch(unsub, /destination|published catalog|#1001|DEMO-1001|I looked at/i);

  const privacy = fixtureDraftFromThread({
    customerName: "Lee Chen",
    status: "open",
    requestType: "privacy_request",
    messages: [{ fromAgent: false, name: "Lee Chen", body: "Please delete my stored personal data." }],
  }, { order: invented });
  assert.match(privacy, /Hi Lee/);
  assert.match(privacy, /privacy request/i);
  assert.match(privacy, /out of band/i);
  assert.match(privacy, /export|deletion/i);
  assert.doesNotMatch(privacy, /destination|published catalog|#1001|DEMO-1001|I looked at/i);

  const shop = createFixtureShop();
  const priya = shop.draftReply({
    ticketId: "t-priya-unsub",
    thread: { customerName: "Priya Lane", requestType: "marketing_unsubscribe", stubDraft: "Hi Priya — I looked at #1001." },
    order: invented,
  });
  assert.match(priya.draft, /marketing unsubscribe/i);
  assert.doesNotMatch(priya.draft, /#1001|destination/i);

  const lee = shop.draftReply({
    ticketId: "t-lee-privacy",
    thread: { customerName: "Lee Chen", requestType: "privacy_request", stubDraft: "Happy to answer from the published catalog." },
    order: invented,
  });
  assert.match(lee.draft, /privacy request/i);
  assert.doesNotMatch(lee.draft, /published catalog|destination/i);

  const bug = fixtureDraftFromThread({
    customerName: "Ada Demo",
    status: "open",
    requestType: "bug",
    messages: [{ fromAgent: false, name: "Ada Demo", body: "The app crashes on checkout." }],
  }, { order: invented });
  assert.match(bug, /Hi Ada/);
  assert.match(bug, /bug report/i);
  assert.match(bug, /device/i);
  assert.match(bug, /iOS|Android/);
  assert.doesNotMatch(bug, /destination|published catalog|#1001|DEMO-1001|I looked at/i);
  assert.match(draftForRequestType("bug", "Ada"), /bug report/i);
  assert.equal(draftForRequestType(null, "Ada"), "");
});

test("CLI draft_reply branches on privacy and unsubscribe fixtures", () => {
  const unsub = pythonInvoke("helpdesk.draft_reply", { ticketId: "t-priya-unsub" });
  assert.equal(unsub.ok, true);
  assert.match(unsub.draft, /Hi Priya/);
  assert.match(unsub.draft, /marketing unsubscribe/i);
  assert.doesNotMatch(unsub.draft, /destination|published catalog|#1001/i);

  const privacy = pythonInvoke("helpdesk.draft_reply", { ticketId: "t-lee-privacy" });
  assert.equal(privacy.ok, true);
  assert.match(privacy.draft, /Hi Lee/);
  assert.match(privacy.draft, /privacy request/i);
  assert.doesNotMatch(privacy.draft, /destination|published catalog|#1001/i);

  const ada = pythonInvoke("helpdesk.draft_reply", { ticketId: "1001", shop: SAMPLE_SHOP });
  assert.equal(ada.ok, true);
  assert.match(ada.draft, /Ada|#1001/);
});

test("fixture draft_reply fallback greets ticket customerName not displayName", async () => {
  const shop = createFixtureShop();
  const draft = shop.draftReply({
    ticketId: "t-in-1",
    thread: {
      id: "t-in-1",
      customerName: "Ada",
      status: "open",
      messages: [{ fromAgent: false, name: "Ada", body: "Where is my order #1001?" }],
    },
    customer: { displayName: "Demo Unfulfilled" },
    order: {
      name: "#1001",
      displayFinancialStatus: "PAID",
      displayFulfillmentStatus: "UNFULFILLED",
    },
  });
  assert.match(draft.draft, /Hi Ada/);
  assert.match(draft.draft, /#1001/);
  assert.doesNotMatch(draft.draft, /Hi Demo|Demo Unfulfilled|gorgias/i);
});

test("composer tools share invoke and are not writes", async () => {
  const calls = [];
  const shop = createHelpdeskShop({
    client: createHelpdeskClient({
      async invoke(tool, args) {
        calls.push([tool, args.ticketId || args.macroId || args.query]);
        if (tool === "helpdesk.draft_reply") return { ok: true, source: "fixture", draft: "Hi Ada — fixture draft." };
        if (tool === "helpdesk.summarize_thread") return { ok: true, source: "fixture", summary: "Ada asked about #1001." };
        if (tool === "helpdesk.search_macros") {
          return {
            ok: true,
            source: "fixture",
            macros: [{ id: "shipping-delay", title: "Shipping delay", tags: ["shipping"], body: "Hi — delay." }],
          };
        }
        if (tool === "helpdesk.apply_macro") {
          return { ok: true, source: "fixture", text: "Hi — delay.", title: "Shipping delay", mode: "replace", body: "Hi — delay." };
        }
        return { ok: false };
      },
    }),
  });
  const draft = await shop.draftReply({ ticketId: "t-ada-track" });
  const summary = await shop.summarizeThread({ ticketId: "t-ada-track" });
  const found = await shop.searchMacros({ query: "delay" });
  const applied = await shop.applyMacro({ macroId: "shipping-delay", mode: "replace" });
  assert.equal(draft.draft, "Hi Ada — fixture draft.");
  assert.equal(summary.summary, "Ada asked about #1001.");
  assert.equal(found.macros[0].id, "shipping-delay");
  assert.equal(applied.text, "Hi — delay.");
  assert.deepEqual(calls, [
    ["helpdesk.draft_reply", "t-ada-track"],
    ["helpdesk.summarize_thread", "t-ada-track"],
    ["helpdesk.search_macros", "delay"],
    ["helpdesk.apply_macro", "shipping-delay"],
  ]);
});

test("CLI draft-reply and summarize-thread return text", () => {
  const draft = pythonInvoke("helpdesk.draft_reply", { ticketId: "1001", shop: SAMPLE_SHOP });
  assert.equal(draft.ok, true);
  assert.match(draft.draft, /Ada|#1001/);
  assert.doesNotMatch(draft.draft, /gorgias|Malky|Rivky|refund you/i);
  const summary = pythonInvoke("helpdesk.summarize_thread", { ticketId: "1001" });
  assert.equal(summary.ok, true);
  assert.match(summary.summary, /Ada/);
  assert.doesNotMatch(summary.summary, />Send</);
});

test("ingest_email Ada joins Cute Things and prize spam files in the Spam view", () => {
  const ada = pythonInvoke("helpdesk.ingest_email", {
    from: "Ada <ada.tracking@example.com>",
    subject: "Tracking on order #1001 has not moved",
    body: "Where is my order #1001? The tracking has not updated.",
    receivedAt: "2026-08-30T14:02:00Z",
  });
  assert.equal(ada.ok, true);
  assert.equal(ada.spam, false);
  assert.equal(ada.customerName, "Ada");
  assert.equal(ada.displayName, undefined);
  assert.equal(ada.customerId, LIVE_IDS.C_UNFULFILLED);
  assert.equal(ada.orderId, LIVE_IDS.O_1001);
  assert.equal(ada.status, "open");
  assert.notEqual(ada.status, "OPEN");

  const prize = pythonInvoke("helpdesk.ingest_email", {
    from: "Prize Desk <winner@prize-farm.example>",
    subject: "You won a $10,000 prize!",
    body: "Claim your lottery winnings today. Unsubscribe from this farm of cash prize emails.",
    receivedAt: "2026-08-30T14:16:00Z",
  });
  assert.equal(prize.ok, true);
  assert.equal(prize.spam, true);
  // #33: spam files a reviewable ticket instead of being dropped. Each CLI
  // call is its own process, so the view partition itself is covered by the
  // helpdesk-agent Python suite; here we pin the payload shape.
  assert.match(prize.ticketId, /^t-in-/);
  const spamRows = pythonInvoke("helpdesk.list_tickets", { view: "spam", limit: 100 });
  assert.equal(spamRows.ok, true);
  assert.ok(Array.isArray(spamRows.tickets));
});

test("CLI search-macros and apply-macro share dispatch and never send", () => {
  const found = pythonInvoke("helpdesk.search_macros", { query: "return" });
  assert.equal(found.ok, true);
  assert.equal(found.macros.length, 1);
  assert.equal(found.macros[0].id, "return-how-to");
  assert.equal(found.macros[0].title, "Return how-to");
  const applied = pythonInvoke("helpdesk.apply_macro", { macroId: "order-status", mode: "replace" });
  assert.equal(applied.ok, true);
  assert.match(applied.text, /I looked at this order/);
  assert.doesNotMatch(applied.text, /gorgias|refund you|i cancelled/i);
  assert.ok(!WRITE_TOOLS.includes("helpdesk.search_macros"));
  assert.ok(!WRITE_TOOLS.includes("helpdesk.apply_macro"));
});

test("escalate_ticket and write_gate_status share the CLI dispatch path", () => {
  const escalated = pythonInvoke("helpdesk.escalate_ticket", {
    ticketId: "t-ada-track",
    reason: "pending review",
  });
  assert.equal(escalated.ok, true);
  assert.equal(escalated.ticket.escalated, true);
  assert.equal(escalated.ticket.status, "open");
  assert.equal(escalated.ticket.escalationReason, "pending review");
  assert.ok(!WRITE_TOOLS.includes("helpdesk.escalate_ticket"));
  assert.ok(!WRITE_TOOLS.includes("helpdesk.mark_privacy_handled"));
  assert.ok(!WRITE_TOOLS.includes("helpdesk.mark_unsubscribed"));
  const gate = pythonInvoke("helpdesk.write_gate_status", {});
  assert.equal(gate.ok, true);
  assert.equal(gate.mutationsEnabled, false);
  assert.deepEqual(gate.refused, ["send", "refund", "cancel"]);
  assert.ok(gate.tools.includes("helpdesk.refund"));
  assert.ok(gate.tools.includes("helpdesk.cancel"));
});

test("shop adapter has no mutation surface", async () => {
  const shop = createHelpdeskShop({
    client: createHelpdeskClient({
      async invoke(tool) {
        if (WRITE_TOOLS.includes(tool)) {
          return { ok: false, error: "forbidden", message: "Shopify writes are refused. SHOPIFY_MUTATIONS_ENABLED stays 0." };
        }
        return { ok: false };
      },
    }),
  });
  assert.equal("getCustomer" in shop, true);
  assert.equal("mutate" in shop, false);
  assert.equal("createRefund" in shop, false);
  assert.equal("cancelOrder" in shop, false);
  const payload = await shop.client.invoke("helpdesk.refund", {});
  assert.equal(payload.ok, false);
  assert.equal(payload.error, "forbidden");
  assert.match(payload.message, /SHOPIFY_MUTATIONS_ENABLED stays 0/);
});

test("resolveLiveInbox remounts only when source is live", async () => {
  const live = await resolveLiveInbox({
    invoke: async () => ({ ok: true, source: "live", customer: { id: LIVE_IDS.C_UNFULFILLED } }),
  });
  assert.equal(live.shop, LIVE_SHOP);
  assert.equal(live.tickets.length, 5);
  assert.ok(live.tickets.every((ticket) => !/Ada Demo/.test(JSON.stringify(ticket))));

  const sample = await resolveLiveInbox({
    invoke: async () => ({ ok: true, source: "sample", customer: { id: LIVE_IDS.C_UNFULFILLED } }),
  });
  assert.equal(sample, null);

  const down = await resolveLiveInbox({
    invoke: async () => {
      throw new Error("no mint");
    },
  });
  assert.equal(down, null);
});

test("live shop host still joins Ada sample GIDs to rail and order-aware draft", async () => {
  const calls = [];
  const shop = createHelpdeskShop({
    shop: LIVE_SHOP,
    client: {
      async invoke(tool, args) {
        calls.push([tool, args.shop || null]);
        if (tool === "helpdesk.list_tickets") {
          return {
            ok: true,
            source: "sample",
            tickets: [{
              id: "t-ada-track",
              customerName: "Ada Demo",
              subject: "Tracking on order #1001 has not moved",
              snippet: "Where is my order #1001?",
              status: "open",
              updatedAt: "2026-08-28T15:10:00Z",
              customerId: SAMPLE_ADA,
              orderId: SAMPLE_ADA_ORDER,
              requestType: null,
            }],
          };
        }
        if (tool === "helpdesk.get_ticket") {
          return {
            ok: true,
            source: "sample",
            ticket: {
              id: "t-ada-track",
              customerName: "Ada Demo",
              subject: "Tracking on order #1001 has not moved",
              snippet: "Where is my order #1001?",
              status: "open",
              updatedAt: "2026-08-28T15:10:00Z",
              customerId: SAMPLE_ADA,
              orderId: SAMPLE_ADA_ORDER,
              messages: [{
                id: "m1",
                fromAgent: false,
                name: "Ada Demo",
                at: "2026-08-28T14:02:00Z",
                body: "Where is my order #1001? The tracking has not updated.",
              }],
              statusEvents: [],
            },
          };
        }
        if (tool === "helpdesk.get_customer") {
          if (args.shop === LIVE_SHOP) return { ok: false, error: "not_found" };
          return pythonInvoke(tool, args);
        }
        if (tool === "helpdesk.get_order" || tool === "helpdesk.get_returns" || tool === "helpdesk.list_past_orders") {
          if (args.shop === LIVE_SHOP) return { ok: false, error: "not_found" };
          return pythonInvoke(tool, args);
        }
        if (tool === "helpdesk.draft_reply") {
          assert.notEqual(args.shop, LIVE_SHOP, "sample GIDs must not draft against Cute Things");
          return pythonInvoke(tool, args);
        }
        if (tool === "helpdesk.write_gate_status") {
          return { ok: true, mutationsEnabled: false, refused: ["send", "refund", "cancel"] };
        }
        if (tool === "helpdesk.search_macros") return { ok: true, macros: [] };
        return { ok: false, error: "not_found" };
      },
    },
  });
  shop.setShop(LIVE_SHOP);
  const organ = createInboxOrgan({ shop, shopHost: LIVE_SHOP, viewId: "mine", ticketId: "t-ada-track" });
  const snap = await organ.ready();
  assert.equal(snap.selectedId, "t-ada-track");
  assert.equal(snap.rail.models.customer.ok, true);
  assert.match(snap.rail.models.customer.peek, /Ada/);
  assert.equal(snap.rail.models.order.ok, true);
  assert.match(snap.rail.models.order.peek, /#1001|Unfulfilled|Paid/);
  assert.doesNotMatch(snap.rail.models.order.peek, /#9001/);
  assert.doesNotMatch(snap.html, /data-customer-join-gate-open|data-order-link-gate-open/);
  assert.match(snap.strip || "", /#1001|looked at|Unfulfilled|Paid/i);
  assert.doesNotMatch(snap.strip || "", /#9001/);
  assert.doesNotMatch(snap.strip || "", /once an order is on this ticket/i);
  assert.ok(calls.some(([tool, shopHost]) => tool === "helpdesk.get_customer" && shopHost !== LIVE_SHOP));
});

test("null SKU and missing billing stay hidden on a live-hole order", async () => {
  const shop = createHelpdeskShop({
    client: clientFromPython("live-holes"),
    shop: LIVE_SHOP,
  });
  const order = await shop.getOrder({ shop: LIVE_SHOP, orderId: LIVE_IDS.O_1002 });
  assert.equal(order.lineItems.nodes[0].sku, undefined);
  assert.equal(order.billingAddress, null);
  const html = (await import("../js/tissues/order.js")).renderOrder(projectOrder(order), { addressesOpen: true });
  assert.doesNotMatch(html, /data-sku=/);
  assert.doesNotMatch(html, />\s*null\s*</);
  assert.match(html, /No billing/);
});

function createInboxOrgan(opts = {}) { return createProductionInbox({shop: createFixtureShop(), ...opts}); }
