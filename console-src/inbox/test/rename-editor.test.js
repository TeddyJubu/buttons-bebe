import test from "node:test";
import assert from "node:assert/strict";
import { createThreadTissue } from "../js/tissues/thread.js";
import { createMailbox } from "../js/mailbox.js";

// #41 cubic round-1 finding 4: a mid-edit repaint (e.g. the 30s bridge poll)
// fires while the rename input is open. The typed draft must survive it —
// the input re-renders with the operator's text, not the stored title, and
// is not select-all'd out from under them.
const OPERATOR = "operator@example.test";

function threadFixture(overrides = {}) {
  return {
    id: "gorgias:1",
    subject: "Subject 1",
    customerName: "customer1@example.test",
    toEmail: "customer1@example.test",
    status: "open",
    assignee: null,
    assigneeEmail: null,
    messages: [],
    statusEvents: [],
    updatedAt: "2026-09-01T00:00:00Z",
    ...overrides,
  };
}

function fakeHost() {
  return { innerHTML: "", querySelector() { return null; } };
}

test("the rename editor carries its draft over a mid-edit repaint", () => {
  const mailbox = createMailbox();
  const tissue = createThreadTissue({ mailbox });
  const host = fakeHost();
  tissue.mount(host);
  tissue.update({ ticket: threadFixture(), capabilities: {}, title: "Subject 1" });
  // Open the editor, type a draft, then a repaint happens (paint() re-renders).
  host.onclick({ target: { closest: (sel) => (sel === "[data-rename-open]" ? { dataset: { ticketId: "gorgias:1", ticketTitle: "Subject 1" } } : null) } });
  const typed = "Christmas order in progress";
  host.querySelector = (sel) => (sel === "[data-rename-input]" ? { value: typed } : null);
  host.oninput?.({ target: { closest: (sel) => (sel === "[data-rename-input]" ? { value: typed } : null) } });
  // Repaint: the organ calls paint() (host.innerHTML = render(model)), the
  // update() that precedes it re-derives the model title from the store.
  tissue.update({ ticket: threadFixture(), capabilities: {}, title: "Subject 1" });
  const html = tissue.render();
  assert.match(html, /value="Christmas order in progress"/, "the draft survives the repaint");
});

test("selecting a ticket carries its rail title into the list row", async () => {
  // #41 cubic finding 5: the thread shows the linked order name (#1001) once
  // getTicket attaches the rail; the list row for that same ticket must not
  // fall back to the raw subject while it is selected.
  const { createInboxOrgan } = await import("../js/inbox.js");
  const storage = {
    backing: new Map(),
    getItem(k) { return this.backing.has(k) ? this.backing.get(k) : null; },
    setItem(k, v) { this.backing.set(k, String(v)); },
    removeItem(k) { this.backing.delete(k); },
  };
  const projected = (id) => ({ id: `gorgias:${id}`, projectionSource: true, historyIncomplete: true, subject: `Subject ${id}`,
    customerName: `customer${id}@example.test`, snippet: "Observed", updatedAt: `2026-09-0${id}T00:00:00Z`,
    status: "open", assignee: null, assigneeEmail: null, messages: [], statusEvents: [] });
  const railTicket = {
    ...projected(1),
    subject: "Where is my parcel?",
    shopifyRail: { status: "found", orderId: "gid://shopify/Order/1",
      order: { id: "gid://shopify/Order/1", name: "#1001", displayFulfillmentStatus: "FULFILLED" } },
  };
  const organ = createInboxOrgan({
    shop: {
      observedHistory: true, operatorEmail: OPERATOR,
      projection: { generatedAt: "gen-1", stale: false },
      getCapabilities: async () => ({}),
      listTickets: async () => [projected(1), projected(2)],
      getTicket: async ({ ticketId }) => (ticketId === "gorgias:1" ? railTicket : null),
    },
    storage,
  });
  await organ.ready();
  await organ.selectTicket?.("gorgias:1");
  const snap = await organ.snapshot();
  const row = snap.html.match(/data-ticket="gorgias:1"[\s\S]*?data-ticket-title="([^"]*)"/);
  assert.equal(row && row[1], "#1001", "the selected row's title matches the thread's rail-derived title");
});
