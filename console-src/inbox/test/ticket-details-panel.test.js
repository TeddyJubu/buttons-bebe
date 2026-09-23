import test from "node:test";
import assert from "node:assert/strict";
import { createInboxOrgan } from "../js/inbox.js";
import { tissueOpen, toggleExpanded } from "../js/review-blocks.js";
import { forbiddenControlHits } from "../js/util.js";

// Issue #45: the ticket-details panel. Gorgias's right-hand rail leads with
// a "Ticket details" card; ours must show only what the observed snapshot or
// a first-party record carries (AGENTS.md §2). Values we do not observe
// render as an explicit unknown — never invented. The card starts toggled
// off and peeks the observed status; the toggle is organ-owned session state.
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
    snippet: "Observed", updatedAt: `2026-09-${String(id).padStart(2, "0")}T00:00:00Z`,
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

test("the rail leads with a Ticket details card for an observed ticket", async () => {
  const row = observedRow(1, {
    channel: "email",
    tags: ["vip", "shipping"],
    assigneeEmail: "agent@example.test",
    updatedAt: "2026-09-18T00:00:00Z",
    draftAction: "refund/request",
  });
  const organ = createInboxOrgan({shop: observedShop([row]), storage: freshStorage(), operatorEmail: OPERATOR});
  const snap = await organ.ready();
  assert.match(snap.html, /data-ticket-details/, "the panel renders");
  assert.match(snap.html, /Ticket details/, "the card is titled");
  // cubic P2: tie each value to its row — a bare /email/ regex passes on the
  // operator address even when the Channel row prints Unknown.
  const card = snap.html.split('data-ticket-details')[1]?.split("</section>")[0] || "";
  assert.match(card, /gorgias:1/, "the ticket id row renders");
  assert.match(card, /<dt>Channel<\/dt><dd>email<\/dd>/, "the observed channel value renders in its row");
  assert.match(card, /Tag/, "the tags row renders");
  assert.match(card, /<span class="ticket-detail-tag">vip<\/span>/, "an observed tag chip renders");
  assert.match(card, /Updated/, "the updated row renders");
  assert.deepEqual(forbiddenControlHits(snap.html), [], "no banned control language appears");
});

test("the ticket-details card starts toggled off and opens on toggle", async () => {
  // The vague unknowns stay one click away instead of leading the rail: the
  // card renders closed by default and the strip peeks the observed status.
  const row = observedRow(1, {channel: "email", draftAction: "refund/request"});
  const organ = createInboxOrgan({shop: observedShop([row]), storage: freshStorage(), operatorEmail: OPERATOR});
  let snap = await organ.ready();
  assert.equal(snap.ticketDetailsOpen, false, "the card starts toggled off");
  assert.equal(tissueOpen(snap.html, "ticket-details"), false, "data-open is false");
  assert.equal(toggleExpanded(snap.html, "ticket-details"), false, "aria-expanded is false");
  const card = snap.html.split('data-ticket-details')[1]?.split("</section>")[0] || "";
  assert.match(card, /<div class="rail-body" hidden>/, "the vague rows stay hidden until opened");
  assert.match(card, /<span class="peek">open<\/span>/, "the closed strip peeks the observed status");
  snap = await organ.toggleTicketDetails();
  assert.equal(snap.ticketDetailsOpen, true, "the toggle opens the card");
  assert.equal(tissueOpen(snap.html, "ticket-details"), true, "data-open is true");
  assert.equal(toggleExpanded(snap.html, "ticket-details"), true, "aria-expanded is true");
  const opened = snap.html.split('data-ticket-details')[1]?.split("</section>")[0] || "";
  assert.doesNotMatch(opened, /<div class="rail-body" hidden>/, "the rows render once opened");
  assert.match(opened, /Contact reason/, "the rows are intact once opened");
  assert.deepEqual(forbiddenControlHits(snap.html), [], "no banned control language appears");
});

test("switching tickets resets the card to toggled off", async () => {
  const rows = [observedRow(1, {channel: "email"}), observedRow(2, {channel: "chat"})];
  const organ = createInboxOrgan({shop: observedShop(rows), storage: freshStorage(), operatorEmail: OPERATOR});
  await organ.ready();
  await organ.toggleTicketDetails();
  assert.equal(organ.snapshot().ticketDetailsOpen, true, "the card opens");
  await organ.selectTicket("gorgias:2");
  const snap = organ.snapshot();
  assert.equal(snap.ticketDetailsOpen, false, "a new selection reverts to toggled off");
  assert.equal(tissueOpen(snap.html, "ticket-details"), false, "the new card renders closed");
});

test("unobserved detail fields render as an explicit unknown, never invented", async () => {
  const row = observedRow(2); // no channel, no tags, no request type, no draft action
  const organ = createInboxOrgan({shop: observedShop([row]), storage: freshStorage(), operatorEmail: OPERATOR});
  const snap = await organ.ready();
  const panel = snap.html.split('data-ticket-details')[1] || snap.html;
  assert.match(panel, /Unknown/, "the unknown value is explicit");
  assert.doesNotMatch(panel, /Add tags/, "no add-tags control: tags are read-only chips for now");
  assert.doesNotMatch(panel, /data-tag-add/, "no tag editor hook exists");
  assert.deepEqual(forbiddenControlHits(snap.html), [], "no banned control language appears");
});

test("a crafted observed value cannot inject markup into the details card", async () => {
  // Observed statuses/priorities arrive as strings from the projection; the
  // card must escape every value at the render boundary, never trust the row.
  const row = observedRow(7, {
    status: '<script>alert("x")</script>',
    tags: ['<b>vip</b>'],
    gorgiasPriority: '<img src=x onerror=alert(1)>',
  });
  const organ = createInboxOrgan({shop: observedShop([row]), storage: freshStorage(), operatorEmail: OPERATOR});
  const snap = await organ.ready();
  assert.doesNotMatch(snap.html, /<script>alert/, "the crafted status never renders as markup");
  assert.doesNotMatch(snap.html, /<img src=x/, "the crafted priority never renders as markup");
  // cubic: bound the slice to the card and positively assert each escaped
  // value — a doesNotMatch alone also passes if the row renders Unknown.
  const panel = snap.html.split('data-ticket-details')[1]?.split("</section>")[0] || "";
  assert.ok(panel, "the card bounds exist");
  assert.match(panel, /&lt;script&gt;/, "the status renders escaped inside the card");
  assert.match(panel, /&lt;img src=x onerror=alert\(1\)&gt;/, "the crafted priority renders escaped in its row");
  assert.match(panel, /&lt;b&gt;vip&lt;\/b&gt;/, "the tag chip renders escaped");
});

test("the card renders the observed status and priority, never the local override or draft priority", async () => {
  // cubic: the status row must show what Gorgias reported (observedStatus),
  // and the priority row reads gorgiasPriority — the processor draft's
  // priority is the draft's, not the ticket's. A projection row with no
  // observed status renders an explicit unknown, never lowercase "unknown".
  const row = observedRow(8, {
    status: "unknown", gorgiasPriority: null, priority: "high",
    draftAction: "order/damaged",
  });
  const organ = createInboxOrgan({shop: observedShop([row]), storage: freshStorage(), operatorEmail: OPERATOR});
  await organ.ready();
  let card = organ.snapshot().html.split('data-ticket-details')[1]?.split("</section>")[0] || "";
  assert.match(card, /<dt>Status<\/dt><dd>[^<]*<span[^>]*>Unknown<\/span>/, "an unobserved status renders the explicit unknown");
  assert.doesNotMatch(card, /<dt>Status<\/dt><dd>unknown<\/dd>/, "the raw lowercase unknown never renders");
  assert.match(card, /<dt>Priority<\/dt><dd>[^<]*<span[^>]*>Unknown<\/span>/, "a draft-only priority renders the explicit unknown");
  assert.doesNotMatch(card, /<dt>Priority<\/dt><dd>high<\/dd>/, "the draft priority never renders as the ticket priority");

  // A Gorgias-reported priority wins; the operator's local status override
  // stays off the card (the thread badge carries the observed value too).
  const reported = observedRow(9, {status: "open", gorgiasPriority: "urgent", priority: "low"});
  const organ2 = createInboxOrgan({shop: observedShop([reported]), storage: freshStorage(), operatorEmail: OPERATOR});
  await organ2.ready();
  await organ2.setTicketState(reported.id, {status: "closed"});
  card = organ2.snapshot().html.split('data-ticket-details')[1]?.split("</section>")[0] || "";
  assert.match(card, /<dt>Status<\/dt><dd>open<\/dd>/, "the observed status renders despite the local override");
  assert.match(card, /<dt>Priority<\/dt><dd>urgent<\/dd>/, "the Gorgias-reported priority renders");
  assert.doesNotMatch(card, /<dt>Priority<\/dt><dd>low<\/dd>/, "the draft priority stays off the card");
});

test("the contact reason renders the observed request type or draft action", async () => {
  // Agent-side rows carry requestType; observed projection rows carry
  // draftAction from the processor's classification. Both are read-only
  // observations — the panel renders whichever it has.
  const agentRow = {id: "t-ada-track", customerName: "Ada Demo", subject: "Hi", snippet: "Hi",
    status: "open", updatedAt: "2026-09-18T00:00:00Z", messages: [], statusEvents: [],
    requestType: "privacy_request", privacySubtype: "delete"};
  const organ = createInboxOrgan({tickets: [agentRow], storage: freshStorage(), operatorEmail: OPERATOR});
  const snap = await organ.ready();
  assert.match(snap.html, /Contact reason/, "the row is labeled");
  assert.match(snap.html, /privacy_request/, "the observed request type renders");

  const observed = observedRow(3, {draftAction: "order/damaged"});
  const organ2 = createInboxOrgan({shop: observedShop([observed]), storage: freshStorage(), operatorEmail: OPERATOR});
  const snap2 = await organ2.ready();
  assert.match(snap2.html, /Contact reason/, "the row is labeled on observed rows");
  assert.match(snap2.html, /order\/damaged/, "the processor's classified action renders");
});

test("the panel shows an empty state when no ticket is selected", async () => {
  const organ = createInboxOrgan({shop: observedShop([]), storage: freshStorage(), operatorEmail: OPERATOR});
  const snap = await organ.ready();
  const panel = snap.html.split('data-ticket-details')[1] || snap.html;
  assert.ok(panel.length > 0 || !snap.html.includes("data-ticket-details"));
  // No selection means no panel content to invent: the rail's own empty
  // state stands. The panel must not render a fabricated ticket.
  assert.doesNotMatch(snap.html, /data-ticket-details/, "no panel without a ticket");
});

test("the card leads the rail in snapshot mode when a Shopify rail is attached", async () => {
  // A ticket with a shopifyRail rides the rail tissue's snapshot render
  // (fromSnapshot) — the card must lead there too, not only in emptyRailHtml.
  const row = {...observedRow(4), shopifyRail: {
    customer: {id: "gid://shopify/Customer/1", displayName: "Cust", defaultEmailAddress: {emailAddress: "c@example.com"}},
    order: {id: "gid://shopify/Order/2", name: "#1001"},
    returns: null, history: [], orderId: "gid://shopify/Order/2", customerId: "gid://shopify/Customer/1",
  }};
  const organ = createInboxOrgan({shop: observedShop([row]), storage: freshStorage(), operatorEmail: OPERATOR});
  const snap = await organ.ready();
  assert.equal(snap.rail.models.fromSnapshot, true, "the rail renders from the Shopify snapshot");
  const railHtml = snap.html.split('data-pane="rail"')[1] || "";
  // Anchor on the first rail-card section — when the card leads, that first
  // section must carry ticket-details (fails cleanly if the card is absent).
  const firstCard = railHtml.slice(railHtml.indexOf("rail-card"));
  assert.match(firstCard.slice(0, firstCard.indexOf("</section>")), /ticket-details/, "the card is the first rail-card");
  assert.match(railHtml, /gorgias:4/, "the observed ticket id renders in the rail");
});

test("a stale snapshot announces once in the list banner, not inside the details card", async () => {
  const staleShop = {...observedShop([observedRow(1)]),
    projection: {generatedAt: "gen-1", stale: true}};
  const organ = createInboxOrgan({shop: staleShop, storage: freshStorage(), operatorEmail: OPERATOR});
  const snap = await organ.ready();
  const panel = snap.html.split('data-ticket-details')[1] || "";
  assert.match(panel, /gorgias:1/, "the card still renders its data");
  assert.doesNotMatch(panel, /stale|Stale/, "no stale copy inside the card");
  const staleHits = snap.html.match(/is stale|Stale snapshot|Snapshot is stale/g) || [];
  assert.equal(staleHits.length, 1, "exactly one stale verdict on the page");
  assert.match(snap.html, /data-history-refresh[^>]*>Refresh</, "the banner carries Refresh");
});

test("the ticket-details card also renders in the live fixture rail", async () => {
  // The live (non-observed) rail mounts the rail tissue; the card is
  // organ-owned so it renders in every mode. The fixture path proves the
  // non-observed mode renders it too. The default helpdesk shop fail-closes
  // createTicket, so the local create needs the capability named here.
  const organ = createInboxOrgan({tickets: [], storage: freshStorage(), operatorEmail: OPERATOR, viewId: "all",
    shop: {observedHistory: false, capabilities: {createTicket: true}, listTickets: async () => []}});
  const snap = await organ.ready();
  // No tickets selected ⇒ no card content; with a selection the card must
  // appear. Create a local ticket to force a selection without the shop.
  const created = await organ.createLocalTicket({customerName: "Ada", fromEmail: "ada@example.test", subject: "Hi", body: "Local question", channel: "chat"});
  const panel = created.html.split('data-ticket-details')[1] || "";
  assert.ok(panel.length > 0, "the panel renders for a local ticket too");
  assert.match(panel, /local:/, "the local ticket id renders");
  assert.match(panel, /chat/, "the local channel renders");
});
