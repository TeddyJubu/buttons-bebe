import test from "node:test";
import assert from "node:assert/strict";
import { createInboxOrgan } from "../js/inbox.js";
import { MAILBOX_TOPICS } from "../js/contracts.js";
import { createHelpdeskShop } from "../js/shop/helpdesk-shop.js";
import { clientFromPython } from "./python-cli.js";
import { forbiddenControlHits } from "../js/util.js";

// Issue #40: the collapsed panes must stay useful. The list strip shows the
// view, its count, and unread state; the rail collapses in every mode —
// including the observed/production inbox — and its strip names what it
// hides. Collapse state persists in the browser store across reloads.
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

test("the collapsed list strip shows the view, its count, and unread state", async () => {
  const storage = freshStorage();
  const organ = createInboxOrgan({shop: observedShop(observedRows), storage});
  await organ.ready();
  await organ.collapseList(true);
  const html = organ.snapshot().html;
  assert.match(html, /data-list-expand/, "the strip carries its expand control");
  assert.match(html, /aria-label="Expand ticket list/, "the expand control is named");
  assert.match(html, /list-strip-view/, "the strip names the active view");
  assert.match(html, /All/, "the default view label renders");
  assert.match(html, /data-strip-count="3"/, "the strip counts the view's tickets");
  assert.deepEqual(forbiddenControlHits(html), []);
});

test("the collapsed list strip's unread count mirrors the unread rows", async () => {
  const storage = freshStorage();
  const organ = createInboxOrgan({shop: observedShop(observedRows), storage});
  await organ.ready();
  // ready() auto-selects (and reads) the first ticket; selecting ticket 2
  // reads it too. Only ticket 3 stays unread.
  await organ.selectTicket("gorgias:2");
  await organ.collapseList(true);
  const html = organ.snapshot().html;
  assert.match(html, /data-strip-unread="1"/, "the strip shows the unread count");
});

test("the observed rail renders a collapse control and collapses", async () => {
  // The production gap: the observed path rendered emptyRailHtml() with no
  // collapse button at all, so the pane could never collapse there.
  const storage = freshStorage();
  const organ = createInboxOrgan({shop: observedShop(observedRows), storage});
  await organ.ready();
  const open = organ.snapshot().html;
  assert.match(open, /data-rail-collapse/, "the observed rail offers a collapse control");
  const collapsed = await organ.collapseRail(true);
  assert.match(collapsed.html, /data-rail-expand/, "the collapsed rail strip carries its expand control");
  assert.match(collapsed.html, /Customer/, "the strip names what it hides");
  assert.deepEqual(forbiddenControlHits(collapsed.html), []);
});

test("the observed rail's collapse control is wired through the organ", async () => {
  // Browser-caught: the empty-rail toolbar is organ-painted HTML, and the rail
  // tissue never mounts in the observed path, so the tissue's own onclick
  // never wires the button. The organ's root click handler must carry it.
  const storage = freshStorage();
  const organ = createInboxOrgan({shop: observedShop(observedRows), storage});
  await organ.ready();
  assert.equal(organ.snapshot().railCollapsed, false, "the rail starts expanded");
  // Mount like boot does — the subscriptions and root click handler are
  // wired in mount(), not the constructor.
  const root = {innerHTML: "", onclick: null, querySelector() { return {innerHTML: "", querySelector() { return null; }}; }};
  await organ.mount(root);
  // A real click on the collapse button goes through root.onclick.
  root.onclick({target: {closest: (sel) => (sel === "[data-rail-collapse]" ? {} : null)}});
  assert.equal(organ.snapshot().railCollapsed, true, "clicking the collapse button collapses the rail");
  assert.match(organ.snapshot().html, /data-rail-expand/, "the collapsed strip renders");
  // And the expand control on the collapsed strip re-expands it.
  root.onclick({target: {closest: (sel) => (sel === "[data-rail-expand]" ? {} : null)}});
  assert.equal(organ.snapshot().railCollapsed, false, "clicking expand restores the rail");
});

test("the collapsed rail strip marks an open return", async () => {
  const storage = freshStorage();
  // The snapshot shape mirrors the observed webhook: customer/order gate the
  // rail, and returns.nodes[].status "OPEN" is the in-flight marker.
  const railSnapshot = {customer: {id: "c-1"}, returns: {returns: {nodes: [{id: "r-1", status: "OPEN"}]}}};
  const rows = [observedRow(1, {shopifyRail: {status: "ok", ...railSnapshot}}), observedRow(2)];
  const organ = createInboxOrgan({shop: observedShop(rows), storage});
  await organ.ready();
  await organ.collapseRail(true);
  const html = organ.snapshot().html;
  assert.match(html, /Returns/, "the strip names the returns pane");
  assert.match(html, /data-strip-return="open"/, "the strip marks the open return");
});

test("the open-return marker follows the selected ticket", async () => {
  // cubic: selecting a return ticket loaded the snapshot into the rail's
  // cached models; moving to a ticket without one left the stale open-return
  // dot on the collapsed strip. The marker must derive from the current
  // ticket's snapshot, not the rail tissue's last-loaded models.
  const storage = freshStorage();
  const railSnapshot = {customer: {id: "c-1"}, returns: {returns: {nodes: [{id: "r-1", status: "OPEN"}]}}};
  const rows = [observedRow(1, {shopifyRail: {status: "ok", ...railSnapshot}}), observedRow(2)];
  const organ = createInboxOrgan({shop: observedShop(rows), storage});
  await organ.ready();
  await organ.selectTicket("gorgias:1");
  await organ.collapseRail(true);
  assert.match(organ.snapshot().html, /data-strip-return="open"/, "ticket 1 marks the open return");
  await organ.selectTicket("gorgias:2");
  const html = organ.snapshot().html;
  assert.doesNotMatch(html, /data-strip-return="open"/, "ticket 2 carries no return marker");
});

test("the connected rail's open-return marker reads the loaded models", async () => {
  // cubic round 2: the observed-path fix read only the ticket snapshot, but
  // connected tickets carry no shopifyRail — their returns come from the
  // rail tissue's live load. The marker must survive on that path too.
  const shop = createHelpdeskShop({client: clientFromPython("sample"), shop: "demo-helpdesk.example"});
  const organ = createInboxOrgan({shop, storage: freshStorage()});
  await organ.ready();
  // t-ada-track's order has an OPEN return in the sample shop.
  const ada = (await shop.listTickets({view: "all", limit: 50})).find((row) => row.id === "t-ada-track");
  assert.ok(ada, "the sample shop lists t-ada-track");
  await organ.selectTicket(ada.id);
  await organ.collapseRail(true);
  assert.match(organ.snapshot().html, /data-strip-return="open"/, "the connected rail marks its live open return");
});

test("collapse state persists across organ instances", async () => {
  const storage = freshStorage();
  const first = createInboxOrgan({shop: observedShop(observedRows), storage});
  await first.ready();
  await first.collapseList(true);
  await first.collapseRail(true);
  // A reload is a new organ over the same browser store.
  const second = createInboxOrgan({shop: observedShop(observedRows), storage});
  await second.ready();
  const html = second.snapshot().html;
  assert.match(html, /class="pane pane-list is-collapsed"/, "the list stays collapsed after reload");
  assert.match(html, /class="pane pane-rail is-collapsed"/, "the rail stays collapsed after reload");
});

test("two instances do not erase each other's collapse choices", async () => {
  // cubic: tab A collapses the list, tab B collapses the rail. B's persist
  // used to overwrite the whole record with its stale list value, so A's
  // choice vanished on reload. Each write must preserve the other field.
  const storage = freshStorage();
  const tabA = createInboxOrgan({shop: observedShop(observedRows), storage});
  const tabB = createInboxOrgan({shop: observedShop(observedRows), storage});
  await tabA.ready();
  await tabB.ready();
  await tabA.collapseList(true);
  await tabB.collapseRail(true);
  // A reload in either tab reads the merged record.
  const reloaded = createInboxOrgan({shop: observedShop(observedRows), storage});
  await reloaded.ready();
  assert.equal(reloaded.snapshot().listCollapsed, true, "the list choice survives the other tab's write");
  assert.equal(reloaded.snapshot().railCollapsed, true, "the rail choice survives the other tab's write");
});

test("the collapsed list strip keeps keyboard-reachable controls", async () => {
  const storage = freshStorage();
  const organ = createInboxOrgan({shop: observedShop(observedRows), storage});
  await organ.ready();
  await organ.collapseList(true);
  const html = organ.snapshot().html;
  // A visible focus ring exists for the strip's only interactive control, and
  // the accessible name carries the view/count/unread signal (cubic).
  assert.match(html, /data-list-expand[^>]*aria-label="Expand ticket list \(/);
  assert.doesNotMatch(html, /tabindex="-1"[^>]*data-list-expand/, "the expand control is never focus-removed");
});

test("a narrow phone starts with the list collapsed so the composer shows", async () => {
  // Task 6: selecting a ticket on a <=780px viewport drops the list to its
  // strip. Session-local only — the persisted record must not learn it, or
  // desktop would inherit the phone's choice.
  globalThis.window = { matchMedia: () => ({ matches: true }) };
  try {
    const storage = freshStorage();
    const organ = createInboxOrgan({shop: observedShop(observedRows), storage});
    const snap = await organ.ready();
    assert.ok(snap.selectedId, "a ticket is selected");
    assert.equal(snap.listCollapsed, true, "the list starts collapsed on narrow");
    assert.match(snap.html, /data-list-expand/, "the expand strip renders");
    assert.equal(storage.dump().get("bb-inbox-collapsed-v1") ?? null, null, "nothing persisted");
    // An explicit expand is the operator's choice and survives.
    await organ.collapseList(false);
    assert.equal(organ.snapshot().listCollapsed, false, "explicit expand holds");
  } finally {
    delete globalThis.window;
  }
});

test("a wide viewport never auto-collapses the list", async () => {
  globalThis.window = { matchMedia: () => ({ matches: false }) };
  try {
    const organ = createInboxOrgan({shop: observedShop(observedRows), storage: freshStorage()});
    const snap = await organ.ready();
    assert.ok(snap.selectedId, "a ticket is selected");
    assert.equal(snap.listCollapsed, false, "the list stays open on wide");
  } finally {
    delete globalThis.window;
  }
});
