import test from "node:test";
import assert from "node:assert/strict";
import { createInboxOrgan } from "../js/inbox.js";

// Issue #39: Gorgias-parity multi-select with bulk actions, scoped to what the
// read-only safety model allows: mark read/unread (operator-browser state from
// #34), export (local download of observed rows), escalate (capability-gated,
// refuses honestly where unavailable). Gorgias writes — tag, assign, priority,
// delete — stay locked pending the client decision the issue calls out.
const OPERATOR = "operator@example.test";

function projected(id, state) {
  return {id: `gorgias:${id}`, projectionSource: true, historyIncomplete: true, subject: `Subject ${id}`,
    customerName: `customer${id}@example.test`, snippet: "Observed", updatedAt: `2026-09-${String(id).padStart(2, "0")}T00:00:00Z`,
    status: "open", assignee: null, assigneeEmail: null, spam: false, trashed: false,
    messages: [], statusEvents: [], customerContext: null, ...state};
}

const ROWS = [projected(1), projected(2), projected(3), projected(4)];

function observedShop(rows = ROWS, escalate = false) {
  return {observedHistory: true, operatorEmail: OPERATOR, projection: {generatedAt: "gen-1", stale: false},
    getCapabilities: async () => ({escalateTicket: escalate}),
    listTickets: async () => rows,
    getTicket: async ({ticketId}) => rows.find((row) => row.id === ticketId) || null,
    escalateTicket: escalate
      ? async ({ticketId}) => {
          const row = rows.find((r) => r.id === ticketId) || null;
          return row ? {...row, escalated: true} : null;
        }
      : undefined};
}

function freshStorage() {
  const backing = new Map();
  return {
    getItem: (k) => (backing.has(k) ? backing.get(k) : null),
    setItem: (k, v) => backing.set(k, String(v)),
    removeItem: (k) => backing.delete(k),
  };
}

function freshDownloads() {
  const downloads = [];
  return {downloads, download: (name, text, type) => downloads.push({name, text, type})};
}

async function organ(overrides = {}) {
  const handle = createInboxOrgan({
    shop: observedShop(), storage: freshStorage(), downloads: freshDownloads(), ...overrides,
  });
  await handle.ready();
  return handle;
}

async function organSnapshot(overrides = {}) {
  return (await organ(overrides)).snapshot();
}

test("rows expose an accessible checkbox and the bar stays hidden until a selection exists", async () => {
  const snap = await organSnapshot();
  for (const id of ["gorgias:2", "gorgias:3"]) {
    // Accessible name per the issue: "Select ticket for <customer>".
    assert.match(snap.html, new RegExp(`data-select-ticket="${id}"[^>]*aria-label="Select ticket for customer`), id);
  }
  assert.doesNotMatch(snap.html, /data-bulk-bar/);
});

test("selecting via the organ toggles membership and shows the All-selected bar", async () => {
  const handle = await organ();
  let snap = await handle.toggleSelect("gorgias:2");
  assert.deepEqual(snap.bulkSelection.ids, ["gorgias:2"]);
  assert.match(snap.html, /data-bulk-bar[\s\S]*?1 selected/);
  assert.match(snap.html, /data-bulk-clear[^>]*>Clear selection/);
  snap = await handle.toggleSelect("gorgias:3");
  assert.deepEqual(snap.bulkSelection.ids, ["gorgias:2", "gorgias:3"]);
  assert.match(snap.html, /2 selected/);
  // Toggling the same row again removes it.
  snap = await handle.toggleSelect("gorgias:2");
  assert.deepEqual(snap.bulkSelection.ids, ["gorgias:3"]);
});

test("shift-click range select spans the rendered rows between anchor and target", async () => {
  // Discriminating case: the shop returns rows in an order that differs from
  // the newest-first sort the operator applies, so the rendered order is
  // 5,4,3,2,1 while the raw order is 3,1,4,2,5. Anchor 2, shift-click 4: the
  // rendered span is 4,3,2 — the raw span would be just 4,2.
  const rows = [projected(3), projected(1), projected(4), projected(2), projected(5)];
  const handle = await organ({shop: observedShop(rows)});
  const sorted = await handle.selectSort("newest");
  assert.match(sorted.html, /data-ticket="gorgias:5"[\s\S]*?data-ticket="gorgias:4"[\s\S]*?data-ticket="gorgias:3"/, "rows render newest first");
  await handle.toggleSelect("gorgias:2");
  const snap = await handle.toggleSelect("gorgias:4", {shiftKey: true});
  assert.deepEqual(snap.bulkSelection.ids.slice().sort(), ["gorgias:2", "gorgias:3", "gorgias:4"]);
  assert.equal(snap.bulkSelection.ids.includes("gorgias:1"), false, "1 is outside the rendered range 4..2");
  assert.equal(snap.bulkSelection.ids.includes("gorgias:5"), false, "5 is outside the rendered range 4..2");
});

test("export neutralizes formula-shaped customer fields and escapes CR/LF", async () => {
  const rows = [
    projected(1),
    {...projected(2), subject: "=cmd|' /C calc'!A0"},
    {...projected(3), subject: "line1\r\nline2"},
    {...projected(4), subject: 'Needs a "quote", a comma'},
  ];
  const downloads = freshDownloads();
  const handle = await organ({shop: observedShop(rows), downloads});
  await handle.toggleSelect("gorgias:2");
  await handle.toggleSelect("gorgias:3");
  await handle.toggleSelect("gorgias:4");
  await handle.bulkExport();
  const [file] = downloads.downloads;
  const lines = file.text.split("\n");
  // Formula-leading cells are prefixed so spreadsheets render them as text
  // (injection is about the cell START; the chars may remain mid-cell).
  assert.ok(lines[1].includes(",'=cmd"), "the subject cell starts with the text prefix");
  // CR/LF inside a cell collapse to a space so the row stays one physical
  // CSV line — row structure survives hostile cell content, and the text
  // stays readable instead of showing escape markers.
  assert.ok(lines[2].includes("line1 line2"));
  assert.ok(!/\r/.test(file.text), "no raw CR anywhere in the file");
  // Comma/quote cells wrap in quotes with doubled inner quotes so the row
  // still parses back to one subject cell.
  assert.ok(lines[3].includes('"Needs a ""quote"", a comma"'), "quote/comma cell is quoted with doubled quotes");
  assert.equal(lines.length, 4, "header + three selected rows, each on one line");
});

test("export keeps the observed assignee address, not the synthetic display value", async () => {
  const rows = [
    projected(1),
    {...projected(2), assigneeEmail: OPERATOR, assignee: OPERATOR},
    {...projected(3), assigneeEmail: "colleague@example.test", assignee: "colleague@example.test"},
  ];
  const downloads = freshDownloads();
  const handle = await organ({shop: observedShop(rows), downloads});
  await handle.toggleSelect("gorgias:2");
  await handle.toggleSelect("gorgias:3");
  await handle.bulkExport();
  const [file] = downloads.downloads;
  const lines = file.text.split("\n");
  assert.ok(lines[1].endsWith(`,${OPERATOR},`), "mine row exports the operator address");
  assert.ok(lines[2].includes(",colleague@example.test,"), "colleague row exports the observed address");
  assert.ok(!/"me"/.test(file.text) && !/,"me",/.test(file.text), "synthetic 'me' never appears in the export");
});

test("a refresh that hides a selected row reconciles the selection before acting", async () => {
  const rows = [projected(1), projected(2), projected(3)];
  let visible = rows;
  const shop = {
    observedHistory: true, operatorEmail: OPERATOR, projection: {generatedAt: "gen-1", stale: false},
    getCapabilities: async () => ({}),
    listTickets: async () => visible,
    getTicket: async ({ticketId}) => visible.find((row) => row.id === ticketId) || null,
  };
  const handle = createInboxOrgan({shop, storage: freshStorage(), downloads: freshDownloads()});
  await handle.ready();
  await handle.toggleSelect("gorgias:2");
  // The next list refresh drops ticket 2 from the visible rows; the stale id
  // must not survive into bulk actions.
  visible = [projected(1), projected(3)];
  const snap = await handle.loadMore();
  assert.deepEqual(snap.bulkSelection.ids, [], "hidden ids are dropped from the selection");
});

test("bulk escalate continues past failures and reports every unconfirmed ticket", async () => {
  const rows = [projected(1), projected(2), projected(3)];
  const attempts = [];
  const shop = {
    observedHistory: true, operatorEmail: OPERATOR, projection: {generatedAt: "gen-1", stale: false},
    getCapabilities: async () => ({escalateTicket: true}),
    listTickets: async () => rows,
    getTicket: async ({ticketId}) => rows.find((row) => row.id === ticketId) || null,
    escalateTicket: async ({ticketId}) => {
      attempts.push(ticketId);
      if (ticketId === "gorgias:2") throw new Error("boom");
      return {...rows.find((row) => row.id === ticketId), escalated: true};
    },
  };
  const handle = createInboxOrgan({shop, storage: freshStorage(), downloads: freshDownloads()});
  await handle.ready();
  await handle.toggleSelect("gorgias:1");
  await handle.toggleSelect("gorgias:2");
  await handle.toggleSelect("gorgias:3");
  const snap = await handle.bulkEscalate();
  assert.deepEqual(attempts, ["gorgias:1", "gorgias:2", "gorgias:3"], "every ticket is attempted");
  assert.deepEqual(snap.bulkSelection.escalated, ["gorgias:1", "gorgias:3"], "successes are kept");
  assert.match(snap.html, /gorgias:2[^<]*<\/span>|Not escalated: gorgias:2/, "the failure names the ticket");
});

test("selection survives load more inside the view and clears on view change", async () => {
  const rows = [];
  for (let i = 0; i < 120; i++) rows.push(projected(`p${i}`));
  const shop = {
    observedHistory: true, operatorEmail: OPERATOR,
    projection: {generatedAt: "gen-1", stale: false, ticketCount: 120},
    getCapabilities: async () => ({}),
    listTickets: async ({offset, limit}) => rows.slice(offset, offset + limit),
    getTicket: async ({ticketId}) => rows.find((row) => row.id === ticketId) || null,
  };
  const handle = createInboxOrgan({shop, storage: freshStorage(), downloads: freshDownloads()});
  const first = await handle.ready();
  assert.equal(first.html.match(/class="ticket-row/g).length, 100);
  await handle.toggleSelect("gorgias:p99");
  const more = await handle.loadMore();
  // Still selected after pagination; bar survives the repaint.
  assert.match(more.html, /data-bulk-bar[\s\S]*?1 selected/);
  // Changing the view clears it (ids may no longer be visible in the next view).
  const next = await handle.selectView("open");
  assert.doesNotMatch(next.html, /data-bulk-bar/);
  assert.deepEqual(next.bulkSelection.ids, []);
});

test("bulk mark read and mark unread persist through the #34 read store", async () => {
  const storage = freshStorage();
  const handle = createInboxOrgan({shop: observedShop(), storage, downloads: freshDownloads()});
  const first = await handle.ready();
  // Rows 2..4 start unread (row 1 is auto-opened); select three unread rows.
  await handle.toggleSelect("gorgias:2");
  await handle.toggleSelect("gorgias:3");
  await handle.toggleSelect("gorgias:4");
  assert.deepEqual(first.unreadIds, ["gorgias:2", "gorgias:3", "gorgias:4"]);
  const read = await handle.bulkMarkRead();
  assert.deepEqual(read.unreadIds, []);
  // A fresh organ over the same storage sees all rows read: persisted.
  const reloaded = await createInboxOrgan({shop: observedShop(), storage, downloads: freshDownloads()}).ready();
  assert.deepEqual(reloaded.unreadIds, []);
  // Mark unread puts them back — for the session and the store.
  const unread = await handle.bulkMarkUnread();
  assert.deepEqual(unread.unreadIds, ["gorgias:2", "gorgias:3", "gorgias:4"]);
  const reloaded2 = await createInboxOrgan({shop: observedShop(), storage, downloads: freshDownloads()}).ready();
  assert.deepEqual(reloaded2.unreadIds, ["gorgias:2", "gorgias:3", "gorgias:4"]);
});

test("bulk export downloads the selected observed rows as a local CSV file", async () => {
  const downloads = freshDownloads();
  const handle = createInboxOrgan({shop: observedShop(), storage: freshStorage(), downloads});
  await handle.ready();
  await handle.toggleSelect("gorgias:2");
  await handle.toggleSelect("gorgias:3");
  const snap = await handle.bulkExport();
  assert.equal(downloads.downloads.length, 1);
  const [file] = downloads.downloads;
  assert.match(file.name, /^inbox-tickets-\d+\.csv$/);
  assert.equal(file.type, "text/csv");
  const lines = file.text.split("\n");
  assert.equal(lines[0], "id,subject,status,updatedAt,assignee,channel");
  assert.ok(lines[1].includes("gorgias:2"));
  assert.ok(lines[2].includes("gorgias:3"));
  // Export must not clear the selection; it is read-only.
  assert.deepEqual(snap.bulkSelection.ids, ["gorgias:2", "gorgias:3"]);
});

test("bulk escalate is capability-gated and refuses honestly without it", async () => {
  const downloads = freshDownloads();
  const handle = createInboxOrgan({shop: observedShop(ROWS, true), storage: freshStorage(), downloads});
  await handle.ready();
  await handle.toggleSelect("gorgias:2");
  const snap = await handle.bulkEscalate();
  assert.equal(snap.bulkSelection.escalated.length, 1);
  assert.match(snap.html, /Escalated 1 ticket/);
  // Without the capability the action refuses — no invented escalation.
  const locked = createInboxOrgan({shop: observedShop(ROWS, false), storage: freshStorage(), downloads});
  await locked.ready();
  await locked.toggleSelect("gorgias:2");
  const refused = await locked.bulkEscalate();
  assert.deepEqual(refused.bulkSelection.escalated, []);
  assert.match(refused.html, /Bulk escalate is not available/);
});

test("bulk actions disable when nothing is selected", async () => {
  const snap = await organSnapshot();
  for (const marker of ["data-bulk-read", "data-bulk-unread", "data-bulk-export", "data-bulk-escalate"]) {
    assert.doesNotMatch(snap.html, new RegExp(marker), marker);
  }
});

test("clearing the selection hides the bar and forgets the ids", async () => {
  const handle = await organ();
  await handle.toggleSelect("gorgias:2");
  const snap = await handle.clearSelection();
  assert.doesNotMatch(snap.html, /data-bulk-bar/);
  assert.deepEqual(snap.bulkSelection.ids, []);
});

test("selecting a row through the checkbox never opens the thread", async () => {
  const handle = await organ();
  const before = handle.snapshot().selectedId;
  const snap = await handle.toggleSelect("gorgias:4");
  assert.equal(snap.selectedId, before);
  assert.doesNotMatch(snap.html, /data-ticket="gorgias:4"[^>]*aria-current="true"/);
});
