import test from "node:test";
import assert from "node:assert/strict";
import { createInboxOrgan } from "../js/inbox.js";
import { createMailbox } from "../js/mailbox.js";
import { forbiddenControlHits } from "../js/util.js";

// Issue #37: search the observed inbox. The organ owns the query; the list
// tissue renders the control and highlights; the URL carries the query so a
// search is shareable (#42's sync, extended). Everything stays local and
// read-only — the projection snapshot is the only source.
const OPERATOR = "operator@example.test";

function projected(id, state) {
  return {id: `gorgias:${id}`, projectionSource: true, historyIncomplete: true,
    subject: `Subject ${id}`, customerName: `customer${id}@example.test`,
    fromEmail: `customer${id}@example.test`,
    snippet: id === 3 ? "refund for the snowsuit" : "Observed", updatedAt: `2026-09-0${id}T00:00:00Z`,
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

function fakeHistory() {
  const entries = [];
  return {
    entries,
    replace({ticket, view, q}) { entries.push({kind: "replace", ticket, view, q}); },
    push({ticket, view, q}) { entries.push({kind: "push", ticket, view, q}); },
  };
}

function makeOrgan(overrides = {}) {
  const {shop: shopOverrides, ...rest} = overrides;
  return createInboxOrgan({
    shop: {
      observedHistory: true, operatorEmail: OPERATOR, projection: {generatedAt: "gen-1", stale: false},
      getCapabilities: async () => ({}),
      listTickets: async () => [projected(1), projected(2, {subject: "Where is my snowsuit?"}), projected(3)],
      getTicket: async ({ticketId}) => [projected(1), projected(2, {subject: "Where is my snowsuit?"}), projected(3)]
        .find((row) => row.id === ticketId) || null,
      ...shopOverrides,
    },
    storage: freshStorage(),
    ...rest,
  });
}

test("the search control exists in the list header", async () => {
  const organ = makeOrgan();
  const snap = await organ.ready();
  assert.match(snap.html, /type="search"/, "a search input renders in the list");
  assert.match(snap.html, /data-search-input/, "the search input carries its hook");
  assert.match(snap.html, /aria-label="Search tickets"/i, "the control is labeled");
  assert.doesNotMatch(snap.html, /placeholder="[^"]*(Edit|Refund|Cancel)/i, "no forbidden control wording");
  assert.deepEqual(forbiddenControlHits(snap.html), []);
});

test("a query filters the visible rows and shows a result count", async () => {
  const organ = makeOrgan();
  await organ.ready();
  const snap = await organ.selectSearch("snowsuit");
  assert.doesNotMatch(snap.html, /data-ticket="gorgias:1"/, "non-matching rows hide");
  assert.match(snap.html, /data-ticket="gorgias:2"/, "a subject match renders");
  // The query matches snippet text too — gorgias:3's snippet says
  // "refund for the snowsuit".
  assert.match(snap.html, /data-ticket="gorgias:3"/, "a snippet match renders");
  assert.match(snap.html, /2 results in the loaded history/, "the count renders and pluralizes");
  assert.equal(snap.searchQuery, "snowsuit", "the snapshot publishes the query");
  // A single match renders the singular count.
  const solo = await organ.selectSearch("Where is");
  assert.match(solo.html, /1 result in the loaded history/, "the singular count renders");
});

test("search matches customer name and address, and clears back to all rows", async () => {
  const organ = makeOrgan();
  await organ.ready();
  // "customer2" matches the derived name/address of ticket 2.
  const byName = await organ.selectSearch("customer2");
  assert.match(byName.html, /data-ticket="gorgias:2"/);
  assert.doesNotMatch(byName.html, /data-ticket="gorgias:1"/);
  const cleared = await organ.selectSearch("");
  assert.match(cleared.html, /data-ticket="gorgias:1"/, "clearing restores every row");
  assert.equal(cleared.searchQuery, "");
});

test("search stays scoped to the active view until escalated", async () => {
  const organ = makeOrgan({
    viewId: "open",
    shop: {
      listTickets: async () => [projected(1, {status: "closed"}), projected(2, {subject: "Where is my snowsuit?"}), projected(3)],
      getTicket: async ({ticketId}) => [projected(1, {status: "closed"}), projected(2, {subject: "Where is my snowsuit?"}), projected(3)]
        .find((row) => row.id === ticketId) || null,
    },
  });
  await organ.ready();
  // gorgias:1 is closed; searching from the Open view must not leak it even
  // though the query matches its subject.
  const scoped = await organ.selectSearch("Subject 1");
  assert.doesNotMatch(scoped.html, /data-ticket="gorgias:1"/, "a view-excluded row never leaks into a scoped search");
  assert.match(scoped.html, /search every view/i, "the escalation to all views is offered");
  const escalated = await organ.selectSearch("Subject 1", {allViews: true});
  assert.match(escalated.html, /data-ticket="gorgias:1"/, "the escalation searches every view");
  assert.match(escalated.html, /Searching all views/i, "the escalation is stated");
});

test("an empty search result explains itself", async () => {
  const organ = makeOrgan();
  await organ.ready();
  const snap = await organ.selectSearch("nothing-matches-this");
  assert.match(snap.html, /No tickets match/i, "the empty state has copy");
  assert.doesNotMatch(snap.html, /No tickets yet/, "the no-tickets state does not stand in");
});

test("matching text is highlighted and the query is escaped", async () => {
  const organ = makeOrgan();
  await organ.ready();
  const snap = await organ.selectSearch("snow");
  assert.match(snap.html, /<mark[^>]*>snow<\/mark>/i, "the matched fragment is marked");
  const hostile = await organ.selectSearch("<img onerror=alert(1)>");
  assert.doesNotMatch(hostile.html, /<img onerror/i, "the query never injects markup");
  assert.match(hostile.html, /&lt;img onerror/, "the raw query is escaped where rendered");
});

test("a long query is bounded, not fatal", async () => {
  const organ = makeOrgan();
  await organ.ready();
  const snap = await organ.selectSearch("x".repeat(5000));
  assert.ok(snap.searchQuery.length <= 200, "the organ clamps the query");
  assert.match(snap.html, /No tickets match/i, "a bounded overlong query behaves as a miss");
});

test("the query syncs into the URL and replays on popstate", async () => {
  const history = fakeHistory();
  const organ = makeOrgan({history});
  await organ.ready();
  await organ.selectSearch("snowsuit");
  // Live typing restamps the current entry (no per-keystroke history
  // spam); the query still lands in the URL so it is shareable.
  const current = history.entries.at(-1);
  assert.equal(current?.q, "snowsuit", "the live search restamps the URL with its query");
  // A replay of a search-less entry clears the query, like #42 clears the facets.
  await organ.replayEntry({ticket: "gorgias:1", view: "all"});
  assert.equal(organ.snapshot().searchQuery, "", "a replay without q clears the search");
  const last = history.entries.at(-1);
  assert.ok(!last.q, "the restamped entry drops the dead query");
});

test("a deep link with q restores the search on boot", async () => {
  const history = fakeHistory();
  const organ = makeOrgan({history, searchQuery: "snowsuit"});
  const snap = await organ.ready();
  assert.equal(snap.searchQuery, "snowsuit", "boot's q seeds the organ search");
  assert.match(snap.html, /data-ticket="gorgias:2"/, "the restored search filters rows");
  assert.match(snap.html, /<mark[^>]*>snowsuit<\/mark>/i, "the restored search highlights");
  assert.equal(history.entries[0]?.kind, "replace", "the landing entry is a replace");
  assert.equal(history.entries[0]?.q, "snowsuit", "the landing entry carries the query");
});

test("search clears on a view switch like every other filter", async () => {
  const organ = makeOrgan();
  await organ.ready();
  await organ.selectSearch("snowsuit");
  const switched = await organ.selectView("open");
  assert.equal(switched.searchQuery, "", "changing the view clears the search");
  assert.match(switched.html, /data-ticket="gorgias:1"/, "the view renders unfiltered");
});

test("search and pagination compose: the count stays honest and the bar hides", async () => {
  const organ = makeOrgan();
  await organ.ready();
  // "Subject" matches tickets 1 and 3; ticket 2's subject differs.
  await organ.selectSearch("Subject");
  const snap = organ.snapshot();
  assert.match(snap.html, /2 results in the loaded history/, "the search count is over the loaded rows");
  assert.doesNotMatch(snap.html, /Load more/, "the pagination bar does not contradict the search count");
});

test("the list tissue publishes the query as the operator types", async () => {
  const mailbox = createMailbox();
  const seen = [];
  mailbox.subscribe("list/searched", (msg) => seen.push(msg));
  // The tissue is mounted by the organ in real runs; drive it directly to
  // pin the input-to-topic wiring.
  const {createListTissue} = await import("../js/tissues/list.js");
  const tissue = createListTissue({ mailbox });
  const host = {
    innerHTML: "",
    set oninput(handler) { this._input = handler; },
    get oninput() { return this._input; },
    querySelector() { return null; },
  };
  tissue.mount(host);
  tissue.update({tickets: [], views: [], counts: {}, selectedViewId: "all", searchQuery: ""});
  tissue.render(); // repaint via render since the tissue paints through the host
  const input = {value: "snow", closest: (selector) => selector === "[data-search-input]" ? input : null};
  host.oninput?.({target: input});
  // The topic fires with the raw value; the organ clamps.
  assert.deepEqual(seen.at(-1), {query: "snow"}, "the search input publishes list/searched");
});

test("a search keystroke never steals focus from the input", async () => {
  // Drive the tissue directly: a keystroke records the caret, and the
  // next repaint (the organ's async paint arriving) must restore focus
  // and caret on the replacement input node — the composer idiom.
  const mailbox = createMailbox();
  const {createListTissue} = await import("../js/tissues/list.js");
  const tissue = createListTissue({ mailbox });
  const live = [];
  let liveInput = null;
  const host = {
    innerHTML: "",
    set oninput(h) { this._input = h; },
    get oninput() { return this._input; },
    querySelector(sel) {
      if (sel === "[data-search-input]" && this.innerHTML.includes("data-search-input")) {
        liveInput ||= {
          focus() { live.push("focus"); },
          setSelectionRange(a) { live.push(`caret:${a}`); },
        };
        return liveInput;
      }
      return null;
    },
  };
  tissue.mount(host);
  tissue.update({tickets: [], views: [], counts: {}, selectedViewId: "all", searchQuery: ""});
  const typing = {
    value: "a", selectionStart: 1,
    closest: (sel) => sel === "[data-search-input]" ? typing : null,
  };
  host.oninput?.({target: typing});
  assert.ok(!live.length, "keystroke publishes without stealing focus synchronously");
  // The organ's repaint arrives: mount + paint with the updated model.
  tissue.update({tickets: [], views: [], counts: {}, selectedViewId: "all", searchQuery: "a"});
  tissue.mount(host);
  assert.ok(live.includes("focus") && live.includes("caret:1"), "the repaint restores focus and caret");
});

test("overlong queries never prefix-match: the bound is a miss, not a truncation", async () => {
  const organ = makeOrgan({
    shop: {
      listTickets: async () => [{
        ...projected(1),
        subject: "x".repeat(300),
        customerName: "customer1@example.test", fromEmail: "customer1@example.test", snippet: "Observed",
      }],
      getTicket: async ({ticketId}) => null,
    },
  });
  await organ.ready();
  // The raw 5000-char paste's 200-x prefix IS ticket 1's subject — the
  // truncation must not turn the paste into a hit.
  const snap = await organ.selectSearch("x".repeat(5000));
  assert.doesNotMatch(snap.html, /data-ticket="gorgias:1"/, "an over-limit query is a miss");
  assert.match(snap.html, /No tickets match/i);
  assert.match(snap.html, /limited to 200 characters/, "the over-limit state says so, not a bare miss");
  const ok = await organ.selectSearch("x".repeat(100));
  assert.match(ok.html, /data-ticket="gorgias:1"/, "a within-bound query still matches");
});

test("back/forward after 'Search every view' restores a scoped, un-escalated search", async () => {
  const history = fakeHistory();
  const organ = makeOrgan({
    history,
    viewId: "open",
    shop: {
      listTickets: async () => [projected(1, {status: "closed"}), projected(2, {subject: "Where is my snowsuit?"}), projected(3)],
      getTicket: async ({ticketId}) => [projected(1, {status: "closed"}), projected(2, {subject: "Where is my snowsuit?"}), projected(3)]
        .find((row) => row.id === ticketId) || null,
    },
  });
  await organ.ready();
  await organ.selectSearch("Subject 1");
  await organ.selectSearch("Subject 1", {allViews: true});
  // Back pops the scoped entry: the same q, but no escalation.
  const scoped = await organ.replayEntry({ticket: null, view: "open", q: "Subject 1"});
  assert.equal(scoped.searchQuery, "Subject 1");
  assert.equal(scoped.searchAllViews, false, "a replay without escalation state de-escalates");
  assert.doesNotMatch(scoped.html, /data-ticket="gorgias:1"/, "the closed ticket stays out again");
});

test("the escalation searches the loaded snapshot on non-observed shops too", async () => {
  const organ = makeOrgan({
    shop: {
      observedHistory: false,
      projection: undefined,
      listTickets: async ({view, limit}) => {
        const rows = [
          {...projected(1), status: "closed"},
          {...projected(2), status: "open"},
        ];
        // The non-observed shop returns per-view pages, unfiltered shapes.
        return view === "closed" ? [rows[0]] : view === "open" ? [rows[1]] : rows;
      },
      getTicket: async ({ticketId}) => null,
    },
    viewId: "open",
  });
  await organ.ready();
  const scoped = await organ.selectSearch("Subject 1");
  assert.doesNotMatch(scoped.html, /data-ticket="gorgias:1"/, "the open view excludes the closed ticket");
  const escalated = await organ.selectSearch("Subject 1", {allViews: true});
  assert.match(escalated.html, /data-ticket="gorgias:1"/, "the escalation searches every view's loaded rows");
});

test("typing does not spam the history stack per keystroke", async () => {
  const history = fakeHistory();
  const organ = makeOrgan({history});
  await organ.ready();
  await organ.selectSearch("s");
  await organ.selectSearch("sn");
  await organ.selectSearch("sno");
  const pushes = history.entries.filter((e) => e.kind === "push");
  const replaces = history.entries.filter((e) => e.kind === "replace");
  assert.ok(pushes.length <= 1, `live typing replaces, not pushes (pushed ${pushes.length})`);
  assert.ok(replaces.length >= 2, `each keystroke restamps the current entry (replaced ${replaces.length})`);
});
