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
  // #42: ready() stamps the landing entry with a replace, never a push —
  // reload cannot grow the history stack.
  assert.equal(history.entries[0]?.kind, "replace", "the landing entry is a replace");
  assert.equal(history.entries.filter((e) => e.kind === "replace").length, 1, "exactly one landing replace");
  // #42: selecting moves via push.
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

test("a deep-linked ticket getTicket resolves never hides behind not-found", async () => {
  // The ticket exists (getTicket resolves it) but sits outside the active
  // view's list rows — the thread must show it, not the not-found state.
  const organ = makeOrgan({
    ticketId: "gorgias:7",
    shop: {
      listTickets: async () => [projected(1)],
      getTicket: async ({ticketId}) => (ticketId === "gorgias:7" ? projected(7) : null),
    },
  });
  const snap = await organ.ready();
  assert.doesNotMatch(snap.html, /Ticket not found/, "a resolved ticket is never hidden as missing");
  assert.match(snap.html, /data-ticket-id-badge="gorgias:7"/, "the resolved ticket renders its thread");
});

test("popstate replays the view as well as the ticket and clears an absent ticket", async () => {
  // back/forward carries both URL fields; replaying only the ticket would
  // leave the organ on the stale view and corrupt the popped entry.
  const history = fakeHistory();
  const organ = makeOrgan({history});
  await organ.ready();
  await organ.selectView("open");
  await organ.selectTicket("gorgias:2");
  // The organ exposes replayEntry so boot's popstate handler can restore
  // both fields without pushing.
  assert.equal(typeof organ.replayEntry, "function", "the organ exposes a replay path");
  await organ.replayEntry({ticket: "gorgias:1", view: "all"});
  assert.equal(organ.snapshot().viewId, "all", "the view replays");
  assert.equal(organ.snapshot().selectedId, "gorgias:1", "the ticket replays");
  const last = history.entries.at(-1);
  assert.equal(last.kind, "replace", "a replay replaces, never pushes");
  // A URL with no ticket falls back to the first visible row and restamps
  // the URL with what renders — the bar and the UI agree.
  await organ.replayEntry({ticket: null, view: "all"});
  const restamped = history.entries.at(-1);
  assert.ok(restamped.ticket, "the replace restamps the URL with the rendered selection");
  assert.equal(restamped.kind, "replace");
});

test("a replay never carries stale facets into the restored view", async () => {
  // The operator filters, then back lands on an unfiltered entry: the
  // restored view must not inherit the dead facet, or ensureSelection
  // snaps the popped ticket away.
  const history = fakeHistory();
  const organ = makeOrgan({
    history,
    shop: {
      listTickets: async () => [projected(1), {...projected(2), channel: "email"}],
      getTicket: async ({ticketId}) => [projected(1), projected(2)].find((row) => row.id === ticketId) || null,
    },
  });
  await organ.ready();
  await organ.selectChannel("email");
  await organ.replayEntry({ticket: "gorgias:2", view: "all"});
  const snap = organ.snapshot();
  assert.equal(snap.channelId, "", "the replay clears the facet filters");
  assert.equal(snap.selectedId, "gorgias:2", "the popped ticket survives selection");
});

test("a replayed ticket outside the rendered rows stays put until getTicket resolves", async () => {
  // Back/forward to an entry whose ticket is not in the current rows: the
  // replay must keep it selected (thread resolves it, or shows not-found)
  // — never snap to the first visible row and restamp over the entry.
  const history = fakeHistory();
  const organ = makeOrgan({
    history,
    shop: {
      listTickets: async () => [projected(1)],
      getTicket: async ({ticketId}) => (ticketId === "gorgias:7" ? projected(7) : null),
    },
  });
  await organ.ready();
  await organ.selectTicket("gorgias:1");
  await organ.replayEntry({ticket: "gorgias:7", view: "all"});
  assert.equal(organ.snapshot().selectedId, "gorgias:7", "the replayed id is not snapped away");
  const last = history.entries.at(-1);
  assert.equal(last.ticket, "gorgias:7", "the popped entry keeps its ticket");
  assert.match(organ.snapshot().html, /data-ticket-id-badge="gorgias:7"/, "the thread renders the resolved ticket");
});

test("an overlapping replay applies only the latest entry", async () => {
  // Rapid back/forward: an earlier replay's refreshes must not overwrite
  // the newest replay's thread. The slow path is armed only after ready()
  // so the initial load never hangs.
  const history = fakeHistory();
  let slowTicket = false;
  const slow = {resolve: null};
  const organ = makeOrgan({
    history,
    shop: {
      listTickets: async () => [projected(1), projected(2)],
      getTicket: async ({ticketId}) => {
        if (slowTicket && ticketId === "gorgias:1") {
          return new Promise((resolve) => { slow.resolve = () => resolve(projected(1)); });
        }
        return ticketId === "gorgias:1" ? projected(1) : projected(2);
      },
    },
  });
  await organ.ready();
  slowTicket = true;
  const first = organ.replayEntry({ticket: "gorgias:1", view: "all"});
  // The second replay must invalidate the first even while it hangs.
  await organ.replayEntry({ticket: "gorgias:2", view: "all"});
  slow.resolve?.();
  await first;
  assert.equal(organ.snapshot().selectedId, "gorgias:2", "the stale replay does not win");
  assert.match(organ.snapshot().html, /data-ticket-id-badge="gorgias:2"/, "the thread shows the latest replay's ticket");
});

test("the copy link derives from the mounted path boot reports", async () => {
  // The review server serves the SPA at /, production at /inbox/ — the
  // copied deep link must carry the real mount path, not a hardcoded one.
  const history = fakeHistory();
  const organ = makeOrgan({history, ticketPath: "/custom-mount/"});
  await organ.ready();
  assert.equal(organ.ticketLink("gorgias:1"), "/custom-mount/?view=all&ticket=gorgias%3A1");
  const bare = makeOrgan({history});
  await bare.ready();
  assert.equal(bare.ticketLink("gorgias:1"), "/inbox/?view=all&ticket=gorgias%3A1", "the default mount is /inbox/");
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
