import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createInboxOrgan } from "../js/inbox.js";
import { forbiddenControlHits } from "../js/util.js";

const here = dirname(fileURLToPath(import.meta.url));

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
  assert.match(html, /title="First of 3 tickets — no previous ticket\."/, "previous names why it is disabled");
  assert.doesNotMatch(html, /data-ticket-next[^>]*disabled/, "next is enabled on the first ticket");
  await organ.selectTicket("gorgias:3");
  html = organ.snapshot().html;
  assert.doesNotMatch(html, /data-ticket-prev[^>]*disabled/, "previous is enabled on the last ticket");
  assert.match(html, /data-ticket-next[^>]*disabled/, "next is disabled on the last ticket");
  assert.match(html, /title="Last of 3 tickets — no next ticket\."/, "next names why it is disabled");
});

test("a disabled nav button stays readable", async () => {
  // Task 7: greyed but legible — the global .42 ghost does not apply here.
  const css = readFileSync(join(here, "../styles.css"), "utf8");
  assert.match(css, /\.detail-nav \.btn-quiet:disabled\s*\{[^}]*opacity:\s*\.65/, "nav disabled opacity stays legible");
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
  assert.equal(reloaded.snapshot().ticketState.priority, "high", "the priority survives reload");
  assert.equal(reloaded.snapshot().ticketState.assignee, OPERATOR, "the assignee survives reload");
});

test("clearing a local override falls back to the observed value", async () => {
  const storage = freshStorage();
  const organ = createInboxOrgan({shop: observedShop(observedRows), storage});
  await organ.ready();
  await organ.setTicketState("gorgias:1", {status: "closed"});
  const snap = await organ.setTicketState("gorgias:1", {status: null});
  assert.equal(snap.ticketState.status, null, "clearing drops the local override");
  // The effective value the control falls back to is the observed one.
  const control = snap.html.match(/<select data-detail-status[^>]*>([\s\S]*?)<\/select>/);
  assert.ok(control, "the status control renders");
  assert.match(control[1], /value="open" selected/, "clearing restores the observed value");
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

test("clearing one field keeps the ticket's other local overrides", async () => {
  const storage = freshStorage();
  const organ = createInboxOrgan({shop: observedShop(observedRows), storage});
  await organ.ready();
  await organ.setTicketState("gorgias:1", {status: "closed", priority: "high"});
  const snap = await organ.setTicketState("gorgias:1", {status: null});
  assert.equal(snap.ticketState.status, null, "the status override is cleared");
  assert.equal(snap.ticketState.priority, "high", "the priority override survives");
  assert.match(snap.html, /Console only/, "a badge remains for the surviving override");
});

test("pinned-catalog rows honor local state in views and counts", async () => {
  // Non-observed shops read pinned rows the organ was mounted with; the
  // overlay must reach those rows too or the Closed control lies.
  const storage = freshStorage();
  const organ = createInboxOrgan({tickets: observedRows, storage});
  await organ.ready();
  await organ.setTicketState("gorgias:1", {status: "closed"});
  await organ.selectView("closed");
  const snap = organ.snapshot();
  assert.match(snap.html, /data-ticket="gorgias:1"/, "the locally-closed pinned ticket appears in the Closed view");
  const closedCount = snap.html.match(/data-view="closed"[^>]*>[\s\S]*?<span class="list-menu-count">(\d+)<\/span>/);
  assert.ok(closedCount, "the Closed view count renders");
  assert.equal(closedCount[1], "1", "the Closed view count includes the local override");
});

test("the observed Gorgias status stays visible beside a local override", async () => {
  const storage = freshStorage();
  const organ = createInboxOrgan({shop: observedShop(observedRows), storage});
  await organ.ready();
  await organ.setTicketState("gorgias:1", {status: "closed"});
  const snap = organ.snapshot();
  // The observed badge names Gorgias, not the local pick.
  const badge = snap.html.match(/<span class="status-line" title="Ticket status"><span class="status-dot[^>]*><\/span>([^<]*)<\/span>/);
  assert.ok(badge, "the observed status readout renders");
  assert.equal(badge[1], "Open", "the readout shows the observed Gorgias status");
});

test("the priority picker starts from the observed Gorgias priority", async () => {
  const storage = freshStorage();
  const rows = [observedRow(1, {gorgiasPriority: "high", priority: "low"})];
  const organ = createInboxOrgan({shop: observedShop(rows), storage});
  await organ.ready();
  const snap = organ.snapshot();
  const control = snap.html.match(/<select data-detail-priority[^>]*>([\s\S]*?)<\/select>/);
  assert.ok(control, "the priority control renders");
  assert.match(control[1], /value="high" selected/, "the picker starts from the observed priority");
  assert.doesNotMatch(control[1], /value="low" selected/, "the draft priority never seeds the picker");
});

test("the assignee picker shows the observed address as a value", async () => {
  const storage = freshStorage();
  const rows = [observedRow(1, {assigneeEmail: "bo@example.test", assignee: "bo@example.test"})];
  const organ = createInboxOrgan({shop: observedShop(rows), storage});
  await organ.ready();
  const snap = organ.snapshot();
  const control = snap.html.match(/<select data-detail-assignee[^>]*>([\s\S]*?)<\/select>/);
  assert.ok(control, "the assignee control renders");
  assert.match(control[1], /value="bo@example.test"/, "the observed assignee address is offered");
});

test("two tabs editing different fields do not erase each other", async () => {
  const storage = freshStorage();
  const tabA = createInboxOrgan({shop: observedShop(observedRows), storage});
  await tabA.ready();
  const tabB = createInboxOrgan({shop: observedShop(observedRows), storage});
  await tabB.ready();
  // Both tabs load the same store; A owns status, B owns priority.
  await tabA.setTicketState("gorgias:1", {status: "closed"});
  await tabB.setTicketState("gorgias:1", {priority: "high"});
  const snapB = tabB.snapshot();
  assert.equal(snapB.ticketState.priority, "high", "tab B's priority override persists");
  assert.equal(snapB.ticketState.status, "closed", "tab A's status override survives tab B's write");
});

test("a locally-set assignee survives the picker's round trip", async () => {
  const storage = freshStorage();
  const longAddress = "a-very-long-operator-address-that-exceeds@example.test";
  const organ = createInboxOrgan({shop: observedShop(observedRows), storage, operatorEmail: longAddress});
  await organ.ready();
  await organ.setTicketState("gorgias:1", {assignee: longAddress});
  const snap = organ.snapshot();
  assert.equal(snap.ticketState.assignee, longAddress, "the full address round-trips untruncated");
  // The picker itself must offer the full address — a truncated option would
  // silently strand the override.
  const control = snap.html.match(/<select data-detail-assignee[^>]*>([\s\S]*?)<\/select>/);
  assert.ok(control, "the assignee control renders");
  assert.match(control[1], new RegExp(`value="${longAddress.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}"`), "the picker offers the full address");
});
