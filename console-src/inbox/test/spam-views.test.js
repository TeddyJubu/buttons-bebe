import test from "node:test";
import assert from "node:assert/strict";
import { ticketInView, viewCounts, views } from "../js/view-model.js";
import { createInboxOrgan } from "../js/inbox.js";

// Issue #33: Gorgias parity — working views (including All) exclude spam and
// trashed tickets; Spam and Trash show only their own rows; counts stay
// consistent with the rows.
const OPERATOR = "operator@example.test";

function projected(id, state) {
  return {id: `gorgias:${id}`, projectionSource: true, historyIncomplete: true, subject: `Subject ${id}`,
    customerName: `customer${id}@example.test`, snippet: "Observed", updatedAt: `2026-09-${String(id).padStart(2, "0")}T00:00:00Z`,
    status: "unknown", assignee: null, assigneeEmail: null, spam: false, trashed: false,
    messages: [], statusEvents: [], customerContext: null, ...state};
}

const ROWS = [
  projected(1, {status: "open"}),
  projected(2, {status: "open", assigneeEmail: OPERATOR}),
  projected(3, {spam: true}),
  projected(4, {trashed: true}),
  projected(5, {status: "closed"}),
];

function observedShop(rows = ROWS, operatorEmail = OPERATOR) {
  return {observedHistory: true, operatorEmail, projection: {generatedAt: "gen-1", stale: false},
    getCapabilities: async () => ({}),
    listTickets: async () => rows,
    getTicket: async ({ticketId}) => rows.find((row) => row.id === ticketId) || null};
}

test("All excludes spam and trashed rows exactly like the working views", () => {
  for (const flagged of [projected(9, {spam: true}), projected(10, {trashed: true})]) {
    assert.equal(ticketInView(flagged, "all"), false, flagged.id);
    for (const viewId of ["open", "mine", "unassigned", "snoozed", "closed"]) {
      assert.equal(ticketInView(flagged, viewId), false, viewId);
    }
  }
  assert.equal(ticketInView(projected(11, {}), "all"), true);
});

test("spam and trash views show only their own rows", () => {
  const spamRow = projected(12, {spam: true, status: "closed"});
  const trashedRow = projected(13, {trashed: true, status: "open"});
  assert.equal(ticketInView(spamRow, "spam"), true);
  assert.equal(ticketInView(trashedRow, "spam"), false);
  assert.equal(ticketInView(trashedRow, "trash"), true);
  assert.equal(ticketInView(spamRow, "trash"), false);
});

test("viewCounts stay consistent: all + spam + trash partition the snapshot", () => {
  // Raw snapshot rows: ticket 2's operator identity is not resolved here,
  // so it counts as unassigned-open in viewCounts, matching the raw columns.
  const counts = viewCounts(ROWS);
  assert.deepEqual(counts, {open: 2, mine: 0, unassigned: 2, all: 3, snoozed: 0, closed: 1, trash: 1, spam: 1});
});

test("observed inbox renders the Gorgias partition in every view", async () => {
  const organ = createInboxOrgan({shop: observedShop()});
  await organ.ready();
  const expected = {all: 3, open: 2, mine: 1, unassigned: 1, snoozed: 0, closed: 1, trash: 1, spam: 1};
  for (const [viewId, count] of Object.entries(expected)) {
    const snap = await organ.selectView(viewId);
    assert.equal(snap.html.match(/class="ticket-row/g)?.length ?? 0, count, viewId);
  }
});

test("spam rows keep their Spam badge and spam view copy stays honest", async () => {
  const organ = createInboxOrgan({shop: observedShop()});
  await organ.ready();
  const snap = await organ.selectView("spam");
  assert.match(snap.html, /data-ticket="gorgias:3"/);
  assert.doesNotMatch(snap.html, /data-ticket="gorgias:1"/);
});

test("load more keeps flagged rows out of the working views and the All total exact", async () => {
  // #33: a spam row beyond the first page must not leak into All after
  // pagination, and the All total must subtract it even unfetched.
  const rows = [];
  for (let i = 0; i < 120; i++) {
    rows.push(projected(`p${i}`, {status: "open"}));
  }
  rows.push(projected("spam-late", {spam: true}));
  const shop = {
    observedHistory: true, operatorEmail: OPERATOR,
    projection: {generatedAt: "gen-1", stale: false, ticketCount: 121, spamCount: 1, trashCount: 0},
    getCapabilities: async () => ({}),
    listTickets: async ({offset, limit}) => rows.slice(offset, offset + limit),
    getTicket: async ({ticketId}) => rows.find((row) => row.id === ticketId) || null,
  };
  const organ = createInboxOrgan({shop, viewId: "all"});
  const first = await organ.ready();
  assert.match(first.html, /Showing 100 of 120/);
  assert.doesNotMatch(first.html, /data-ticket="gorgias:spam-late"/);
  const more = await organ.loadMore();
  // The row is loaded (raw prefix) but must stay out of the All render.
  assert.doesNotMatch(more.html, /data-ticket="gorgias:spam-late"/);
  assert.match(more.html, /Showing 121 of 120/);
});
