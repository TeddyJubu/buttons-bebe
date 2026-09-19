import test from "node:test";
import assert from "node:assert/strict";
import { createInboxOrgan } from "../js/inbox.js";
import { createThreadTissue } from "../js/tissues/thread.js";
import { createMailbox } from "../js/mailbox.js";
import { forbiddenControlHits } from "../js/util.js";

// Issue #42: surface the ticket id, keep the URL in sync with the selection,
// and make deep links honest. The organ owns selection state; boot owns
// history (the organ stays DOM-free, so the URL adapter is injected like
// #39's downloads adapter). Unknown ids show "Ticket not found", never a
// silent jump to another row.
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

// A fake history adapter with the same shape boot.js will pass: a log of
// every replace/push plus the entry the browser would land on.
function fakeHistory() {
  const entries = [];
  return {
    entries,
    replace({ticket, view}) { entries.push({kind: "replace", ticket, view}); },
    push({ticket, view}) { entries.push({kind: "push", ticket, view}); },
  };
}

function makeOrgan(overrides = {}) {
  const {shop: shopOverrides, ...rest} = overrides;
  return createInboxOrgan({
    shop: {
      observedHistory: true, operatorEmail: OPERATOR, projection: {generatedAt: "gen-1", stale: false},
      getCapabilities: async () => ({}),
      listTickets: async () => [projected(1), projected(2)],
      getTicket: async ({ticketId}) => [projected(1), projected(2)].find((row) => row.id === ticketId) || null,
      ...shopOverrides,
    },
    storage: freshStorage(),
    ...rest,
  });
}

test("the organ reports the selected ticket so boot can sync the URL", async () => {
  const history = fakeHistory();
  const organ = makeOrgan({history});
  await organ.ready();
  // Boot's paint loop reads the snapshot; the organ must publish the URL
  // state it owns: selection + active view.
  const snap = organ.snapshot();
  assert.equal(snap.selectedId, "gorgias:1");
  assert.equal(snap.viewId, "all");
  // #42: selecting moves via push; the initial ready() replaceSyncs.
  await organ.selectTicket("gorgias:2");
  const pushed = history.entries.filter((e) => e.kind === "push");
  assert.equal(pushed.at(-1)?.ticket, "gorgias:2", "a selection pushes a new history entry");
  assert.equal(pushed.at(-1)?.view, "all");
});

test("the snapshot exposes a deep-link descriptor the URL builder uses", async () => {
  const history = fakeHistory();
  const organ = makeOrgan({history});
  await organ.ready();
  await organ.selectView("open");
  const snap = organ.snapshot();
  // #42: the URL query carries the active view so a bookmark restores both
  // the list partition and the ticket.
  assert.equal(snap.viewId, "open");
  assert.equal(snap.selectedId, "gorgias:1");
  const last = history.entries.at(-1);
  assert.equal(last.view, "open", "a view change syncs the view into the URL");
  assert.ok(last.ticket, "the view change keeps the selected ticket in the URL");
});

test("an unknown deep-linked ticket shows Ticket not found instead of jumping rows", async () => {
  // Boot passes the unknown id in; the organ must not ensureSelection() it
  // away to the first visible row. The thread shows a not-found state.
  const organ = makeOrgan({ticketId: "gorgias:999"});
  const snap = await organ.ready();
  assert.match(snap.html, /Ticket not found/, "the thread states the ticket is unknown");
  assert.doesNotMatch(snap.html, /data-ticket="gorgias:999"/, "no row is invented for the id");
  // The list stays intact and the requested id stays the selection so the
  // not-found state has something to name.
  assert.match(snap.html, /data-ticket="gorgias:1"/);
  assert.equal(snap.selectedId, "gorgias:999", "the unknown id stays selected, not snapped away");
});

test("the thread shows the ticket id with a Copy link control", async () => {
  const mailbox = createMailbox();
  const tissue = createThreadTissue({ mailbox });
  tissue.mount({innerHTML: "", querySelector() { return null; }});
  tissue.update({ticket: projected(1), capabilities: {}, title: "Subject 1"});
  const html = tissue.render();
  assert.match(html, /data-ticket-id-badge="gorgias:1"/, "the id is visible as a badge");
  assert.match(html, /data-copy-link/, "a copy-link control exists");
  assert.deepEqual(forbiddenControlHits(html), [], "no forbidden write-control wording appears");
});
