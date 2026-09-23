import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createInboxOrgan } from "../js/inbox.js";
import { forbiddenControlHits } from "../js/util.js";

const here = dirname(fileURLToPath(import.meta.url));

// Issue #47: the customer-details panel. The observed rail must render every
// observed identity field with its source and timestamp, and "unknown" (never
// blank, never invented) when a field was never observed (AGENTS.md §2).
// Conflict handling stays visible. Notes and customer type are first-party
// browser state — the write question stays out (AGENTS.md §2(6)).
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

// Slices the customer-details card out of the rail html (the observed branch).
function customerCard(html) {
  return html.split('data-customer-details')[1]?.split("</section>")[0] || "";
}

test("the observed rail renders a customer-details card with every observed field", async () => {
  const row = observedRow(1, {
    customerContext: {
      source: "canonical_webhook", status: "observed", conflict: false,
      observedAt: "2026-09-18T00:00:00Z",
      identity: {name: "Ada Lovelace", email: "ada@example.test", phone: "+1 555 0100", id: "987654"},
    },
  });
  const organ = createInboxOrgan({shop: observedShop([row]), storage: freshStorage(), operatorEmail: OPERATOR});
  const snap = await organ.ready();
  const card = customerCard(snap.html);
  assert.ok(card, "the customer card renders");
  assert.match(card, /Customer details/, "the card is titled");
  assert.match(card, /<dt>Name<\/dt><dd>Ada Lovelace<\/dd>/, "the observed name renders");
  assert.match(card, /<dt>Email<\/dt><dd>ada@example\.test<\/dd>/, "the observed email renders");
  assert.match(card, /<dt>Phone<\/dt><dd>\+1 555 0100<\/dd>/, "the observed phone renders");
  assert.match(card, /<dt>Gorgias customer ID<\/dt><dd>987654<\/dd>/, "the observed id renders");
  assert.match(card, /observed Gorgias webhook/, "the observation source is named");
  assert.match(card, /Sept/, "the observation timestamp renders");
  assert.deepEqual(forbiddenControlHits(snap.html), [], "no banned control language appears");
});

test("unobserved identity fields collapse into one explicit line, never blank", async () => {
  // The webhook carried only the address: name/phone/id were never observed.
  // Task 3: three "Unknown" rows collapse into one named line — still
  // explicit, never blank, never invented.
  const row = observedRow(2, {
    customerContext: {
      source: "canonical_webhook", status: "observed", conflict: false,
      observedAt: "2026-09-18T00:00:00Z",
      identity: {name: null, email: "customer2@example.test", phone: null, id: null},
    },
  });
  const organ = createInboxOrgan({shop: observedShop([row]), storage: freshStorage(), operatorEmail: OPERATOR});
  const snap = await organ.ready();
  const card = customerCard(snap.html);
  assert.ok(card, "the card renders");
  assert.doesNotMatch(card, />Unknown</, "no Unknown rows in the customer card");
  assert.match(card, /Not observed: Name, Phone, Gorgias customer ID\./, "the missing fields are named in one line");
  assert.match(card, /<dt>Email<\/dt><dd>customer2@example\.test<\/dd>/, "the observed email still renders");
});

test("a conflicted identity keeps the conflict visible instead of picking a side", async () => {
  const row = observedRow(3, {
    customerContext: {
      // The webhook observed real identity values, but they conflict — the
      // card must never render any side of the conflict as fact.
      source: "canonical_webhook", status: "conflict", conflict: true,
      observedAt: "2026-09-18T00:00:00Z",
      identity: {name: "Ada Lovelace", email: "ada@example.test", phone: "+1 555 0100", id: "987654"},
    },
  });
  const organ = createInboxOrgan({shop: observedShop([row]), storage: freshStorage(), operatorEmail: OPERATOR});
  const snap = await organ.ready();
  const card = customerCard(snap.html);
  assert.ok(card, "the card renders");
  assert.match(card, /Conflicting customer details were observed/, "the conflict copy renders");
  assert.doesNotMatch(card, /Ada Lovelace/, "no conflicted name renders as fact");
  assert.doesNotMatch(card, /<dd>ada@example\.test<\/dd>/, "no conflicted email renders as fact");
  assert.doesNotMatch(card, /<dd>\+1 555 0100<\/dd>/, "no conflicted phone renders as fact");
  assert.doesNotMatch(card, /<dd>987654<\/dd>/, "no conflicted id renders as fact");
});

test("a crafted identity value cannot inject markup into the customer card", async () => {
  const row = observedRow(4, {
    customerContext: {
      source: "canonical_webhook", status: "observed", conflict: false,
      observedAt: "2026-09-18T00:00:00Z",
      identity: {name: '<script>alert("x")</script>', email: "customer4@example.test", phone: null, id: null},
    },
  });
  const organ = createInboxOrgan({shop: observedShop([row]), storage: freshStorage(), operatorEmail: OPERATOR});
  const snap = await organ.ready();
  assert.doesNotMatch(snap.html, /<script>alert/, "the crafted name never renders as markup");
  const card = customerCard(snap.html);
  assert.match(card, /&lt;script&gt;/, "the crafted name renders escaped inside the card");
});

test("the customer card's rows share the card's side padding", async () => {
  // Regression: the rows once sat flush on the card edge while the heading
  // and source lines carried 12px sides.
  const css = readFileSync(join(here, "../styles.css"), "utf8");
  assert.match(css, /\.customer-details \.ticket-detail-fields\s*\{[^}]*padding:\s*0 12px/);
});

test("the card names when the identity was never observed at all", async () => {
  // No customerContext on the row — the panel must not invent identity.
  const row = observedRow(5);
  delete row.customerContext;
  const organ = createInboxOrgan({shop: observedShop([row]), storage: freshStorage(), operatorEmail: OPERATOR});
  const snap = await organ.ready();
  const card = customerCard(snap.html);
  assert.ok(card, "the card renders");
  assert.match(card, /Customer identity was not included in the observed history/, "the no-identity copy renders");
});

test("the same-address ticket history strip lists other observed tickets", async () => {
  // Parity with Gorgias's "3 tickets · Show all": other tickets from the
  // same address exist in the projection — the card lists them read-only.
  const same = observedRow(6, {subject: "Second ticket", fromEmail: "customer1@example.test", customerName: "customer1@example.test"});
  const other = observedRow(7, {subject: "Third ticket", fromEmail: "someoneelse@example.test", customerName: "someoneelse@example.test"});
  const selected = observedRow(1, {
    customerContext: {source: "canonical_webhook", status: "observed", conflict: false, observedAt: "2026-09-18T00:00:00Z",
      identity: {name: null, email: "customer1@example.test", phone: null, id: null}},
  });
  const organ = createInboxOrgan({shop: observedShop([selected, same, other]), storage: freshStorage(), operatorEmail: OPERATOR});
  const snap = await organ.ready();
  const card = customerCard(snap.html);
  assert.ok(card, "the card renders");
  assert.match(card, /Ticket history/, "the strip is labeled");
  assert.match(card, /Second ticket/, "the same-address ticket renders");
  assert.doesNotMatch(card, /Third ticket/, "a different address stays out of the strip");
  assert.match(card, /1 ticket from this address/, "the strip counts the address's other tickets");
  // cubic: the strip lists observed tickets only — a local-only ticket
  // shares the address but was never observed by Gorgias. Creating it
  // selects it, so re-select the observed ticket to read its strip.
  await organ.createLocalTicket({customerName: "Local Person", fromEmail: "customer1@example.test", subject: "Local only", body: "Never observed", channel: "email"});
  await organ.selectTicket("gorgias:1");
  const after = customerCard(organ.snapshot().html);
  assert.ok(after, "the observed ticket's card still renders");
  assert.doesNotMatch(after, /Local only/, "a local-only ticket never appears in the observed history strip");
  assert.match(after, /1 ticket from this address/, "the count still names only the observed tickets");
});

test("the history strip reads the whole loaded snapshot, not the active view", async () => {
  // cubic: a same-address ticket sitting in another view (here: closed
  // while the operator is in open) still counts — the strip is about the
  // address, not the view partition.
  const closedSameAddress = observedRow(6, {subject: "Closed ticket", status: "closed", fromEmail: "customer1@example.test", customerName: "customer1@example.test"});
  const selected = observedRow(1, {
    customerContext: {source: "canonical_webhook", status: "observed", conflict: false, observedAt: "2026-09-18T00:00:00Z",
      identity: {name: null, email: "customer1@example.test", phone: null, id: null}},
  });
  const organ = createInboxOrgan({shop: observedShop([selected, closedSameAddress]), storage: freshStorage(), operatorEmail: OPERATOR, viewId: "open"});
  const snap = await organ.ready();
  const card = customerCard(snap.html);
  assert.ok(card, "the card renders");
  assert.match(card, /Closed ticket/, "the closed same-address ticket still renders in the strip");
  assert.match(card, /1 ticket from this address/, "the count includes the other view's ticket");
});

test("no customer card renders when there is no observed ticket", async () => {
  // cubic: the observed identity card belongs to the observed path only.
  // With no ticket selected, the rail shows the plain empty pane — four
  // Unknowns and an "identity was not included" line would be invented
  // state for a ticket that does not exist.
  const organ = createInboxOrgan({shop: observedShop([]), storage: freshStorage(), operatorEmail: OPERATOR});
  const snap = await organ.ready();
  assert.doesNotMatch(snap.html, /data-customer-details/, "no identity card without an observed ticket");
  assert.match(snap.html, /Select a conversation|No tickets yet/, "the plain empty pane stands");
});

test("no customer card renders for a ticket that was never observed", async () => {
  // cubic: a non-observed ticket (local-only rows, disconnected inboxes)
  // has no observed identity — the card would render invented Unknowns.
  // The connected rail (live tissue) renders here, so the honest check is
  // the absence of the observed-mode card and its invented-history copy.
  const organ = createInboxOrgan({tickets: [], storage: freshStorage(), operatorEmail: OPERATOR, viewId: "all",
    shop: {observedHistory: false, capabilities: {createTicket: true}, listTickets: async () => []}});
  const created = await organ.createLocalTicket({customerName: "Ada", fromEmail: "ada@example.test", subject: "Hi", body: "Local question", channel: "chat"});
  assert.doesNotMatch(created.html, /data-customer-details/, "no identity card for a never-observed ticket");
  assert.doesNotMatch(created.html, /Customer identity was not included in the observed history/, "no invented observed-history copy for a never-observed ticket");
});

test("the history strip is a description list", async () => {
  // cubic: dt/dd must sit inside a dl — the strip's terms keep their
  // description-list semantics for assistive technology.
  const same = observedRow(6, {subject: "Second ticket", fromEmail: "customer1@example.test", customerName: "customer1@example.test"});
  const selected = observedRow(1, {
    customerContext: {source: "canonical_webhook", status: "observed", conflict: false, observedAt: "2026-09-18T00:00:00Z",
      identity: {name: null, email: "customer1@example.test", phone: null, id: null}},
  });
  const organ = createInboxOrgan({shop: observedShop([selected, same]), storage: freshStorage(), operatorEmail: OPERATOR});
  const snap = await organ.ready();
  const card = customerCard(snap.html);
  assert.match(card, /<dl class="customer-history" data-customer-history>/, "the strip is wrapped in a dl");
});

test("the customer card heading styles identically to the ticket-details card", async () => {
  // cubic: the ticket-details header was the only rail-card heading rule; the
  // new card's h2 fell back to the browser default. The ticket-details
  // rail-toggle and the customer h2 must share one rule so the two cards
  // read identically.
  const css = readFileSync(join(here, "../styles.css"), "utf8");
  assert.match(css, /\.ticket-details \.rail-toggle,\s*\.customer-details h2\s*\{/, "both rail-card headings share one rule");
});

test("no notes or customer-type control renders — they stay out of scope", async () => {
  // AGENTS.md §2(6): ticket-detail controls are first-party local state and
  // the notes/customer-type write is not in this issue — the card must not
  // render an editor for them, and the copy names them out of scope.
  const row = observedRow(1, {
    customerContext: {source: "canonical_webhook", status: "observed", conflict: false, observedAt: "2026-09-18T00:00:00Z",
      identity: {name: "Ada", email: "ada@example.test", phone: null, id: null}},
  });
  const organ = createInboxOrgan({shop: observedShop([row]), storage: freshStorage(), operatorEmail: OPERATOR});
  const snap = await organ.ready();
  const card = customerCard(snap.html);
  assert.ok(card, "the card renders");
  assert.doesNotMatch(card, /data-note-add|Add note|Note:/, "no notes editor exists");
  assert.doesNotMatch(card, /Customer type/, "no customer-type field renders");
  assert.deepEqual(forbiddenControlHits(snap.html), [], "no banned control language appears");
});
