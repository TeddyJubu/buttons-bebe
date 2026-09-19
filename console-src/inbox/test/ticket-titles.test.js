import test from "node:test";
import assert from "node:assert/strict";
import { createInboxOrgan } from "../js/inbox.js";

// Issue #41: derive the ticket title and let the operator rename it inline.
// Resolution order (never writes to Gorgias): first-party title (browser
// store) → observed order name when a rail order is linked → observed
// subject → "New ticket". Null/empty/"No subject" never reaches the UI.
const OPERATOR = "operator@example.test";

function projected(id, state) {
  return {id: `gorgias:${id}`, projectionSource: true, historyIncomplete: true, subject: `Subject ${id}`,
    customerName: `customer${id}@example.test`, snippet: "Observed", updatedAt: `2026-09-${String(id).padStart(2, "0")}T00:00:00Z`,
    status: "open", assignee: null, assigneeEmail: null, messages: [], statusEvents: [], ...state};
}

function freshStorage() {
  const backing = new Map();
  return {
    getItem: (k) => (backing.has(k) ? backing.get(k) : null),
    setItem: (k, v) => backing.set(k, String(v)),
    removeItem: (k) => backing.delete(k),
  };
}

function makeOrgan(overrides = {}) {
  const {shop: shopOverrides, ...rest} = overrides;
  return createInboxOrgan({
    shop: {
      observedHistory: true, operatorEmail: OPERATOR, projection: {generatedAt: "gen-1", stale: false},
      getCapabilities: async () => ({}),
      listTickets: async () => [projected(1)],
      getTicket: async ({ticketId}) => [projected(1)].find((row) => row.id === ticketId) || null,
      ...shopOverrides,
    },
    storage: freshStorage(), ...rest,
  });
}

async function organ(overrides = {}) {
  return (await makeOrgan(overrides)).ready();
}

test("a ticket with a subject derives its title from the subject", async () => {
  const snap = await organ();
  assert.match(snap.html, /data-ticket-title="Subject 1"/);
  assert.match(snap.html, /class="thread-subject"[^>]*>Subject 1/);
});

test("a linked order name outranks the subject until the operator renames", async () => {
  // getTicket attaches the observed rail snapshot (projection.py does this
  // server-side); the thread render must prefer the linked order name.
  const handle = await organ({
    shop: {
      getTicket: async () => ({...projected(1), subject: "Where is my parcel?", shopifyRail: {
        status: "found", orderId: "gid://shopify/Order/1",
        order: {id: "gid://shopify/Order/1", name: "#1001", displayFulfillmentStatus: "FULFILLED"},
      }}),
    },
  });
  assert.match(handle.html, /data-ticket-title="#1001"/);
});

test("blank or No-subject rows derive New ticket and never leak the blank", async () => {
  const snap = await organ({
    shop: {
      listTickets: async () => [projected(1, {subject: ""}), projected(2, {subject: "No subject"})],
      getTicket: async ({ticketId}) => [projected(1, {subject: ""}), projected(2, {subject: "No subject"})].find((r) => r.id === ticketId) || null,
    },
  });
  assert.match(snap.html, /data-ticket-title="New ticket"/);
  assert.doesNotMatch(snap.html, /data-ticket-title=""/);
  assert.doesNotMatch(snap.html, /data-ticket-title="No subject"/);
});

test("renaming persists in the browser store and survives reload; it never touches Gorgias", async () => {
  const storage = freshStorage();
  const writes = [];
  const tracked = {getItem: storage.getItem, setItem: (k, v) => {writes.push(k); storage.setItem(k, v);}, removeItem: storage.removeItem};
  const first = makeOrgan({storage: tracked});
  await first.ready();
  assert.equal(writes.filter((key) => key === "bb-inbox-titles-v1").length, 0, "the title store is not written before a rename");
  const renamed = await first.renameTicket("gorgias:1", "Christmas order");
  assert.match(renamed.html, /data-ticket-title="Christmas order"/);
  assert.match(renamed.html, /class="thread-subject"[^>]*>Christmas order/);
  assert.ok(writes.includes("bb-inbox-titles-v1"), "the title store is persisted");
  const before = JSON.parse(tracked.getItem("bb-inbox-titles-v1"));
  assert.equal(before["gorgias:1"].title, "Christmas order", "the store holds the rename");
  // A fresh organ over the same storage sees the rename: it survives reload.
  const reloaded = makeOrgan({storage: tracked});
  assert.match((await reloaded.ready()).html, /data-ticket-title="Christmas order"/);
});

test("an empty or whitespace rename falls back to the derived title instead of blanking", async () => {
  const handle = makeOrgan();
  await handle.ready();
  const snap = await handle.renameTicket("gorgias:1", "   ");
  assert.match(snap.html, /data-ticket-title="Subject 1"/, "falls back to the derived title");
  assert.equal(snap.titles["gorgias:1"], undefined, "nothing is stored for an empty rename");
});

test("the rename guard trims, collapses whitespace and caps length", async () => {
  const handle = makeOrgan();
  await handle.ready();
  const snap = await handle.renameTicket("gorgias:1", "  A   very long  \n");
  assert.equal(snap.titles["gorgias:1"].title, "A very long", "trimmed and collapsed");
});

test("a rename records who renamed it and when", async () => {
  const handle = makeOrgan();
  await handle.ready();
  const snap = await handle.renameTicket("gorgias:1", "Christmas order");
  const record = snap.titles["gorgias:1"];
  assert.equal(record.title, "Christmas order", "the store holds {title, by, at}, not a bare string");
  assert.equal(record.by, OPERATOR, "the operator's observed email is recorded");
  assert.ok(Number.isFinite(record.at), "the epoch timestamp is recorded");
});

test("renaming one ticket never renames another", async () => {
  const handle = makeOrgan({
    shop: {
      listTickets: async () => [projected(1), projected(2)],
      getTicket: async ({ticketId}) => [projected(1), projected(2)].find((r) => r.id === ticketId) || null,
    },
  });
  await handle.ready();
  const snap = await handle.renameTicket("gorgias:2", "Mine");
  assert.match(snap.html, /data-ticket="gorgias:1"[\s\S]*?data-ticket-title="Subject 1"/);
  assert.match(snap.html, /data-ticket="gorgias:2"[\s\S]*?data-ticket-title="Mine"/);
});
