import test from "node:test";
import assert from "node:assert/strict";
import { createInboxOrgan } from "../js/inbox.js";
import { forbiddenControlHits } from "../js/util.js";

// Issue #44: ticket-detail controls. The thread header gains first-party
// controls — status, priority, assignee (browser-store state only, never a
// Gorgias write), a three-dot overflow menu of the other first-party actions,
// and previous/next navigation that walks the current filtered list.
const OPERATOR = "operator@example.test";

function observedShop(rows) {
  return {
    observedHistory: true, operatorEmail: OPERATOR, projection: {generatedAt: "gen-1", stale: false},
    getCapabilities: async () => ({}),
    listTickets: async () => rows,
    getTicket: async ({ticketId}) => rows.find((row) => row.id === ticketId) || null,
  };
}

function observedRow(id, state = {}) {
  return {id: `gorgias:${id}`, projectionSource: true, historyIncomplete: true,
    subject: `Subject ${id}`, customerName: `customer${id}@example.test`,
    fromEmail: `customer${id}@example.test`,
    snippet: "Observed", updatedAt: `2026-09-0${id}T00:00:00Z`,
    status: "open", assignee: null, assigneeEmail: null, messages: [], statusEvents: [], ...state};
}

function freshStorage() {
  const backing = new Map();
  return {
    getItem: (k) => (backing.has(k) ? backing.get(k) : null),
    setItem: (k, v) => backing.set(k, String(v)),
    removeItem: (k) => backing.delete(k),
    dump: () => backing,
  };
}

const observedRows = [observedRow(1), observedRow(2), observedRow(3)];

test("the thread header renders first-party controls", async () => {
  const organ = createInboxOrgan({shop: observedShop(observedRows), storage: freshStorage()});
  await organ.ready();
  const html = organ.snapshot().html;
  assert.match(html, /data-detail-status/, "a status control exists");
  assert.match(html, /data-detail-priority/, "a priority control exists");
  assert.match(html, /data-detail-assignee/, "an assignee control exists");
  assert.match(html, /data-detail-menu/, "an overflow menu exists");
  assert.match(html, /data-ticket-prev/, "a previous control exists");
  assert.match(html, /data-ticket-next/, "a next control exists");
  assert.deepEqual(forbiddenControlHits(html), []);
});

test("next and previous walk the current list and stop at the ends", async () => {
  const organ = createInboxOrgan({shop: observedShop(observedRows), storage: freshStorage()});
  await organ.ready();
  assert.equal(organ.snapshot().selectedId, "gorgias:1", "the first ticket is selected");
  await organ.stepTicket(1);
  assert.equal(organ.snapshot().selectedId, "gorgias:2", "next moves forward");
  await organ.stepTicket(1);
  assert.equal(organ.snapshot().selectedId, "gorgias:3", "next moves forward again");
  await organ.stepTicket(1);
  assert.equal(organ.snapshot().selectedId, "gorgias:3", "next stops at the end");
  await organ.stepTicket(-1);
  assert.equal(organ.snapshot().selectedId, "gorgias:2", "previous moves back");
  await organ.stepTicket(-1);
  await organ.stepTicket(-1);
  assert.equal(organ.snapshot().selectedId, "gorgias:1", "previous stops at the start");
});

test("the nav buttons render disabled at the list boundaries", async () => {
  const organ = createInboxOrgan({shop: observedShop(observedRows), storage: freshStorage()});
  await organ.ready();
  let html = organ.snapshot().html;
  assert.match(html, /data-ticket-prev[^>]*disabled/, "previous is disabled on the first ticket");
  assert.doesNotMatch(html, /data-ticket-next[^>]*disabled/, "next is enabled on the first ticket");
  await organ.selectTicket("gorgias:3");
  html = organ.snapshot().html;
  assert.doesNotMatch(html, /data-ticket-prev[^>]*disabled/, "previous is enabled on the last ticket");
  assert.match(html, /data-ticket-next[^>]*disabled/, "next is disabled on the last ticket");
});

test("a single-ticket list disables both nav buttons", async () => {
  const organ = createInboxOrgan({shop: observedShop([observedRow(1)]), storage: freshStorage()});
  await organ.ready();
  const html = organ.snapshot().html;
  assert.match(html, /data-ticket-prev[^>]*disabled/, "previous is disabled with one ticket");
  assert.match(html, /data-ticket-next[^>]*disabled/, "next is disabled with one ticket");
});

test("setting status, priority and assignee is first-party and persists", async () => {
  const storage = freshStorage();
  const organ = createInboxOrgan({shop: observedShop(observedRows), storage});
  await organ.ready();
  const snap1 = await organ.setTicketState("gorgias:1", {status: "closed", priority: "high", assignee: OPERATOR});
  assert.equal(snap1.ticketState.status, "closed", "the local status applies");
  assert.equal(snap1.ticketState.priority, "high", "the local priority applies");
  assert.equal(snap1.ticketState.assignee, OPERATOR, "the local assignee applies");
  // The observed badge stays honest — the local override is marked as ours.
  assert.match(snap1.html, /Console only/, "the local override is labeled console-only");
  // A reload is a new organ over the same browser store.
  const reloaded = createInboxOrgan({shop: observedShop(observedRows), storage});
  await reloaded.ready();
  assert.equal(reloaded.snapshot().ticketState.status, "closed", "the status survives reload");
});

test("clearing a local override falls back to the observed value", async () => {
  const storage = freshStorage();
  const organ = createInboxOrgan({shop: observedShop(observedRows), storage});
  await organ.ready();
  await organ.setTicketState("gorgias:1", {status: "closed"});
  const snap = await organ.setTicketState("gorgias:1", {status: null});
  assert.equal(snap.ticketState.status, null, "clearing restores the observed value");
  assert.doesNotMatch(snap.html, /Console only/, "no override marker remains");
});

test("status view filters honor the local override", async () => {
  // A locally-closed ticket must appear in the Closed view, or the control
  // would lie about what it does.
  const storage = freshStorage();
  const organ = createInboxOrgan({shop: observedShop(observedRows), storage});
  await organ.ready();
  await organ.setTicketState("gorgias:1", {status: "closed"});
  await organ.selectView("closed");
  const html = organ.snapshot().html;
  assert.match(html, /data-ticket="gorgias:1"/, "the locally-closed ticket appears in the Closed view");
});

test("the overflow menu carries the first-party actions", async () => {
  const organ = createInboxOrgan({shop: observedShop(observedRows), storage: freshStorage()});
  await organ.ready();
  const html = organ.snapshot().html;
  assert.match(html, /data-menu-toggle/, "the menu opens");
  assert.match(html, /data-menu-mark-unread/, "mark-as-unread is offered");
  assert.match(html, /title="[^"]*[Bb]rowser only[^"]*"/, "menu actions say they are browser-only");
});
