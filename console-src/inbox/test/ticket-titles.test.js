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

test("clearing an existing rename removes it and falls back to the derived title", async () => {
  const storage = freshStorage();
  const handle = makeOrgan({storage});
  await handle.ready();
  await handle.renameTicket("gorgias:1", "Christmas order");
  const cleared = await handle.renameTicket("gorgias:1", "   ");
  assert.match(cleared.html, /data-ticket-title="Subject 1"/, "the rename is gone; the subject shows again");
  assert.equal(cleared.titles["gorgias:1"], undefined, "the record is deleted, not kept");
  // And the deletion survives a reload over the same storage.
  const reloaded = makeOrgan({storage});
  assert.equal((await reloaded.ready()).titles["gorgias:1"], undefined, "the deletion persists");
});

test("derived fallbacks get the same guard: whitespace collapses and length caps", async () => {
  const handle = makeOrgan({
    shop: {
      listTickets: async () => [projected(1, {subject: "  Where   is\n\nmy parcel? "}), projected(2, {subject: "x".repeat(200)})],
      getTicket: async ({ticketId}) => [
        projected(1, {subject: "  Where   is\n\nmy parcel? "}),
        projected(2, {subject: "x".repeat(200)}),
      ].find((r) => r.id === ticketId) || null,
    },
  });
  const snap = await handle.ready();
  assert.match(snap.html, /data-ticket-title="Where is my parcel\?"/, "fallback whitespace collapses");
  assert.doesNotMatch(snap.html, /data-ticket-title="x{121,}"/, "fallback caps at 120");
  assert.match(snap.html, /data-ticket-title="x{120}"/, "exactly 120 survives");
});

test("the rename guard trims, collapses whitespace and caps length", async () => {
  const handle = makeOrgan();
  await handle.ready();
  const snap = await handle.renameTicket("gorgias:1", "y".repeat(140));
  assert.equal(snap.titles["gorgias:1"].title, "y".repeat(120), "capped at 120");
  const collapsed = await handle.renameTicket("gorgias:1", "  A   very long  \n");
  assert.equal(collapsed.titles["gorgias:1"].title, "A very long", "trimmed and collapsed");
});

test("a cleared rename stays cleared through later persists in the same session", async () => {
  // The two-tab merge re-adds stored keys this organ lacks — a deletion must
  // not be resurrected by that merge on the NEXT persist (rename another
  // ticket, clear a different one, any later write).
  const storage = freshStorage();
  const handle = makeOrgan({
    storage,
    shop: {
      listTickets: async () => [projected(1), projected(2)],
      getTicket: async ({ticketId}) => [projected(1), projected(2)].find((r) => r.id === ticketId) || null,
    },
  });
  await handle.ready();
  await handle.renameTicket("gorgias:1", "Christmas order");
  await handle.renameTicket("gorgias:1", "   ");
  await handle.renameTicket("gorgias:2", "Mine");
  const store = JSON.parse(storage.getItem("bb-inbox-titles-v1") || "{}");
  assert.equal(store["gorgias:1"], undefined, "the merge never resurrects a cleared rename");
  assert.equal(store["gorgias:2"].title, "Mine");
});

test("a stale tab's persist follows storage for tickets it never touched", async () => {
  // Two tabs load a store that already holds ticket 1's old title. Tab A
  // renames ticket 1; tab B — still holding the load-time copy — renames a
  // different ticket. B's persist must adopt A's newer title instead of
  // writing its stale copy: key presence is not local ownership.
  const storage = freshStorage();
  storage.setItem("bb-inbox-titles-v1", JSON.stringify({"gorgias:1": {title: "Old title", by: OPERATOR, at: 1}}));
  const shop = {
    listTickets: async () => [projected(1), projected(2)],
    getTicket: async ({ticketId}) => [projected(1), projected(2)].find((r) => r.id === ticketId) || null,
  };
  const tabA = makeOrgan({storage, shop});
  const tabB = makeOrgan({storage, shop});
  await tabA.ready();
  await tabB.ready();
  await tabA.renameTicket("gorgias:1", "A's title");
  await tabB.renameTicket("gorgias:2", "Mine");
  const store = JSON.parse(storage.getItem("bb-inbox-titles-v1") || "{}");
  assert.equal(store["gorgias:1"]?.title, "A's title", "B's persist adopts A's newer rename");
  assert.equal(store["gorgias:2"]?.title, "Mine");
});

test("a stale tab's persist drops a title another tab cleared", async () => {
  // Mirror case: A clears ticket 1's stored title; B, which still holds the
  // load-time copy and never touched ticket 1, persists after renaming a
  // different ticket. B must drop ticket 1 — not resurrect it.
  const storage = freshStorage();
  storage.setItem("bb-inbox-titles-v1", JSON.stringify({"gorgias:1": {title: "Old title", by: OPERATOR, at: 1}}));
  const shop = {
    listTickets: async () => [projected(1), projected(2)],
    getTicket: async ({ticketId}) => [projected(1), projected(2)].find((r) => r.id === ticketId) || null,
  };
  const tabA = makeOrgan({storage, shop});
  const tabB = makeOrgan({storage, shop});
  await tabA.ready();
  await tabB.ready();
  await tabA.renameTicket("gorgias:1", "   ");
  await tabB.renameTicket("gorgias:2", "Mine");
  const store = JSON.parse(storage.getItem("bb-inbox-titles-v1") || "{}");
  assert.equal(store["gorgias:1"], undefined, "B's persist does not resurrect the cleared title");
  assert.equal(store["gorgias:2"]?.title, "Mine");
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
