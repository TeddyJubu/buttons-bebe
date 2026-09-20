import test from "node:test";
import assert from "node:assert/strict";
import { createInboxOrgan } from "../js/inbox.js";
import { forbiddenControlHits } from "../js/util.js";

// Issue #38: the create-new-ticket entry point. Model 1 (local-only), the
// decision recorded in AGENTS.md §2(7): the New ticket button opens a form
// in the inbox header area, the created ticket is first-party browser-store
// state (never a Gorgias write, never a customer notification), it is
// selected immediately, and it appears in the views its status/assignee
// put it in. The capability gate refuses the flow when off.
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

const observedRows = [observedRow(1), observedRow(2)];

test("the list toolbar offers a New ticket button when the capability is on", async () => {
  const organ = createInboxOrgan({shop: observedShop(observedRows), storage: freshStorage(), operatorEmail: OPERATOR});
  await organ.ready();
  const html = organ.snapshot().html;
  assert.match(html, /data-create-ticket/, "a New ticket button exists");
  assert.match(html, /New ticket/, "the button is labeled");
  assert.deepEqual(forbiddenControlHits(html), [], "no banned control language appears");
});

test("the capability gate refuses the create flow entirely when off", async () => {
  const shop = {...observedShop(observedRows), capabilities: {createTicket: false}};
  const organ = createInboxOrgan({shop, storage: freshStorage(), operatorEmail: OPERATOR});
  await organ.ready();
  const html = organ.snapshot().html;
  assert.doesNotMatch(html, /data-create-ticket/, "the button is hidden when the capability is off");
  const snap = await organ.createLocalTicket({customerName: "Ada Lovelace", fromEmail: "ada@example.test", subject: "Hello", body: "A question", channel: "email"});
  assert.equal(snap.createError, "Ticket creation is not enabled.", "the direct call is refused");
  assert.equal(snap.selectedId, "gorgias:1", "the selection never moves on refusal");
});

test("the create sheet opens and closes first-party", async () => {
  const organ = createInboxOrgan({shop: observedShop(observedRows), storage: freshStorage(), operatorEmail: OPERATOR});
  await organ.ready();
  let html = (await organ.openCreateSheet()).html;
  assert.match(html, /data-create-sheet/, "the sheet renders");
  assert.match(html, /data-create-customer/, "the customer field renders");
  assert.match(html, /data-create-email/, "the email field renders");
  assert.match(html, /data-create-subject/, "the subject field renders");
  assert.match(html, /data-create-body/, "the body field renders");
  assert.match(html, /data-create-channel/, "the channel field renders");
  html = (await organ.closeCreateSheet()).html;
  assert.doesNotMatch(html, /data-create-sheet/, "the sheet closes");
});

test("creating a local ticket validates required fields", async () => {
  const organ = createInboxOrgan({shop: observedShop(observedRows), storage: freshStorage(), operatorEmail: OPERATOR});
  await organ.ready();
  let snap = await organ.createLocalTicket({customerName: "", fromEmail: "", subject: "", body: "", channel: "email"});
  assert.ok(snap.createError, "an empty submit is refused");
  assert.equal(snap.selectedId, "gorgias:1", "the selection never moves on a validation error");
  // Body is required; subject may auto-title from it.
  snap = await organ.createLocalTicket({customerName: "Ada", fromEmail: "ada@example.test", subject: "", body: "Where is my order?", channel: "email"});
  assert.ok(!snap.createError, "a subject-less create auto-titles and succeeds");
  assert.match(snap.html, /Where is my order\?/, "the created ticket renders in the list");
});

test("a created ticket persists in the browser store and survives reload", async () => {
  const storage = freshStorage();
  const organ = createInboxOrgan({shop: observedShop(observedRows), storage, operatorEmail: OPERATOR});
  await organ.ready();
  const snap = await organ.createLocalTicket({customerName: "Ada Lovelace", fromEmail: "ada@example.test", subject: "Refund question", body: "I need help", channel: "email"});
  const id = snap.selectedId;
  assert.ok(/^local:/.test(id), "the created ticket has a local id");
  assert.match(snap.html, new RegExp(`data-ticket="${id}"`), "the created ticket is selected in the list");
  assert.equal(snap.ticket?.channel, "email", "the channel rides on the ticket");
  // Reload = a new organ over the same browser store.
  const reloaded = createInboxOrgan({shop: observedShop(observedRows), storage, operatorEmail: OPERATOR});
  await reloaded.ready();
  const reloadHtml = reloaded.snapshot().html;
  assert.match(reloadHtml, /data-ticket="local:[^"]+"/, "the local ticket survives reload");
  assert.match(reloadHtml, /Ada Lovelace/, "the customer name survives reload");
});

test("a created ticket appears in the Open and All views and not in Closed", async () => {
  const organ = createInboxOrgan({shop: observedShop(observedRows), storage: freshStorage(), operatorEmail: OPERATOR});
  await organ.ready();
  await organ.createLocalTicket({customerName: "Ada", fromEmail: "ada@example.test", subject: "Hi", body: "Hello there", channel: "email"});
  // The observed shop defaults to All, so Open must be selected explicitly —
  // the default view is not "open" there.
  let html = (await organ.selectView("open")).html;
  assert.match(html, /data-ticket="local:[^"]+"/, "the local ticket renders in Open");
  await organ.selectView("closed");
  html = organ.snapshot().html;
  assert.doesNotMatch(html, /data-ticket="local:/, "the local ticket stays out of Closed");
  await organ.selectView("all");
  html = organ.snapshot().html;
  assert.match(html, /data-ticket="local:[^"]+"/, "the local ticket renders in All");
});

// A connected shop that serves per-view rows (observedHistory absent) must
// keep local tickets inside their views: the union may not drop a local
// open/unassigned row into Mine, Closed, Trash, or Spam, and the per-view
// counts must include it.
function perViewShop() {
  const rows = [observedRow(1), observedRow(2)].map((row) => ({...row, projectionSource: false}));
  const closedRow = observedRow(3, {status: "closed", projectionSource: false});
  return {...observedShop([...rows, closedRow]), observedHistory: false, listTickets: async ({view}) => {
    if (view === "closed") return [closedRow];
    if (view === "all") return [...rows, closedRow];
    if (view === "open" || view === "mine" || view === "unassigned") return rows;
    return [];
  }};
}

test("a local ticket joins only its views on a per-view connected shop and lifts the counts", async () => {
  const organ = createInboxOrgan({shop: perViewShop(), storage: freshStorage(), operatorEmail: OPERATOR, viewId: "open"});
  await organ.ready();
  await organ.createLocalTicket({customerName: "Ada", fromEmail: "ada@example.test", subject: "Hi", body: "Local question", channel: "email"});
  const openHtml = organ.snapshot().html;
  assert.match(openHtml, /data-ticket="local:[^"]+"/, "the local ticket renders in Open");
  for (const viewId of ["mine", "closed", "trash", "spam"]) {
    const html = (await organ.selectView(viewId)).html;
    assert.doesNotMatch(html, /data-ticket="local:/, `the local ticket stays out of ${viewId}`);
  }
  // The per-view counts must carry the local row: Open and Unassigned see one
  // more than the server-only count; Closed/Spam stay at the server-only
  // numbers (the local row is open and unassigned).
  const counts = organ.snapshot().counts;
  assert.equal(counts.open, 3, "Open counts the local ticket");
  assert.equal(counts.unassigned, 3, "Unassigned counts the local ticket");
  assert.equal(counts.all, 4, "All counts the local ticket");
  assert.equal(counts.closed, 1, "Closed keeps the server-only count");
  assert.equal(counts.spam, 0, "Spam stays empty");
  assert.equal(counts.mine, 2, "Mine keeps the server-only count");
});

// A scoped search over the per-view shop must still escalate to every view
// and find the local ticket there — the escalation's source is allRows.
test("the search-every-view escalation finds a local ticket on a per-view shop", async () => {
  const organ = createInboxOrgan({shop: perViewShop(), storage: freshStorage(), operatorEmail: OPERATOR, viewId: "open"});
  await organ.ready();
  await organ.createLocalTicket({customerName: "Ada", fromEmail: "ada@example.test", subject: "Hi", body: "Local question", channel: "email"});
  // Scoped to Open the local ticket matches its own body.
  const scoped = await organ.selectSearch("Local question");
  assert.match(scoped.html, /data-ticket="local:[^"]+"/, "a scoped search finds the local ticket");
  // Escalated to every view, it must still match — allRows holds it.
  const escalated = await organ.selectSearch("Local question", {allViews: true});
  assert.match(escalated.html, /data-ticket="local:[^"]+"/, "the all-views escalation still finds the local ticket");
});

// Load more re-reads the observed prefix; the local rows must survive the
// re-read instead of being overwritten by the observed-only response.
test("Load more keeps the local tickets in the loaded list", async () => {
  const pages = [
    observedRow(1), observedRow(2),
  ];
  const bigShop = {
    ...observedShop([]),
    observedHistory: true,
    projection: {generatedAt: "gen-1", stale: false, ticketCount: 2},
    listTickets: async ({limit = 100}) => pages.slice(0, limit),
  };
  const organ = createInboxOrgan({shop: bigShop, storage: freshStorage(), operatorEmail: OPERATOR});
  await organ.ready();
  await organ.createLocalTicket({customerName: "Ada", fromEmail: "ada@example.test", subject: "Hi", body: "Local question", channel: "email"});
  const html = (await organ.loadMore()).html;
  assert.match(html, /data-ticket="local:[^"]+"/, "the local ticket survives Load more");
  assert.match(html, /data-ticket="gorgias:1"/, "the observed rows survive Load more");
});

test("two tabs do not erase each other's local tickets", async () => {
  const storage = freshStorage();
  const tabA = createInboxOrgan({shop: observedShop(observedRows), storage, operatorEmail: OPERATOR});
  await tabA.ready();
  const tabB = createInboxOrgan({shop: observedShop(observedRows), storage, operatorEmail: OPERATOR});
  await tabB.ready();
  const snapA = await tabA.createLocalTicket({customerName: "Ada", fromEmail: "ada@example.test", subject: "A", body: "From A", channel: "email"});
  const snapB = await tabB.createLocalTicket({customerName: "Bo", fromEmail: "bo@example.test", subject: "B", body: "From B", channel: "email"});
  // After tab B's later write, tab A's ticket must survive in the merged store.
  const mergedHtml = tabB.snapshot().html;
  assert.match(mergedHtml, /From A/, "tab A's ticket survives tab B's write");
  assert.match(mergedHtml, /From B/, "tab B's ticket renders");
  assert.ok(/^local:/.test(snapA.selectedId) && /^local:/.test(snapB.selectedId), "both tabs selected their own ticket");
  assert.notEqual(snapA.selectedId, snapB.selectedId, "each create gets a distinct id");
});

test("a created ticket renders a customer message bubble in the thread", async () => {
  const organ = createInboxOrgan({shop: observedShop(observedRows), storage: freshStorage(), operatorEmail: OPERATOR});
  await organ.ready();
  const snap = await organ.createLocalTicket({customerName: "Ada Lovelace", fromEmail: "ada@example.test", subject: "Refund question", body: "I need help", channel: "email"});
  const html = snap.html;
  const thread = html.split('data-pane="thread"')[1] || html;
  assert.match(thread, /bubble customer/, "a customer bubble renders");
  assert.match(thread, /I need help/, "the body text renders");
  assert.match(thread, /From Ada Lovelace/, "the bubble names the customer");
});

test("a validation error keeps the typed fields in the sheet", async () => {
  const organ = createInboxOrgan({shop: observedShop(observedRows), storage: freshStorage(), operatorEmail: OPERATOR});
  await organ.ready();
  await organ.openCreateSheet();
  // Submit with everything filled except the message — the error repaint
  // must not wipe what the operator already typed.
  const snap = await organ.createLocalTicket({customerName: "Ada Lovelace", fromEmail: "ada@example.test", subject: "Refund question", body: "", channel: "chat"});
  assert.equal(snap.createError, "Message is required.", "the error names the missing field");
  const sheet = snap.html.match(/<form class="gate-sheet create-sheet"[\s\S]*?<\/form>/)?.[0] || "";
  assert.ok(sheet, "the sheet stays open");
  assert.match(sheet, /value="Ada Lovelace"/, "the customer name survives the repaint");
  assert.match(sheet, /value="ada@example\.test"/, "the email survives the repaint");
  assert.match(sheet, /value="Refund question"/, "the subject survives the repaint");
  assert.match(sheet, /value="chat" selected/, "the channel pick survives the repaint");
  // Closing abandons the draft.
  const closed = await organ.closeCreateSheet();
  assert.doesNotMatch(closed.html, /data-create-sheet/, "the sheet closes");
  const reopened = await organ.openCreateSheet();
  assert.doesNotMatch(reopened.html, /value="Ada Lovelace"/, "closing clears the draft");
});
