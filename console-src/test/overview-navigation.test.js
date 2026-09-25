const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const test = require("node:test");

const source = fs.readFileSync(new URL("../index.html", `file://${__filename}`), "utf8");
function slice(from, to) {
  const start = source.indexOf(from), end = source.indexOf(to, start);
  assert.ok(start > 0 && end > start, `stable boundary: ${from}`);
  return source.slice(start, end);
}
function harness() {
  const context = {
    loadErrors: [],
    stats: { done: 91, drafted: 80, sensitive_draft: 10, escalated: 3, failed: 6, pending: 4, processing: 2, critical: 7 },
    tickets: [{ ticket_id: 42, ticket_subject: "Example ticket", escalated: true }],
    opsPanel: () => "", ownerAlertWarning: () => "", ownerAlertInspection: () => "", learnPanel: () => "",
    digits: String, esc: String, isEsc: ticket => ticket.escalated,
    keyOf: ticket => ticket.ticket_id, nameOf: () => "Example customer", ago: () => "recently",
    IC: { check: "CHECK" }, inboxNavView: "closed", inboxNavTicket: null, tab: "overview",
    document: { getElementById: () => null }, render: () => {},
    URLSearchParams, location: { assign(url) { this.destination = url; } },
  };
  vm.createContext(context);
  vm.runInContext(slice("function bbStandaloneInboxSrc(", "function ticketsView(){") +
    slice("function inboxDeepFilter(", "\nfunction render(){") +
    slice("function overview(){", "\nfunction learnPanel(){"), context);
  return context;
}

test("UI-03: dashboard metrics are informational, not unsupported filtered links", () => {
  const html = harness().overview();
  assert.doesNotMatch(html, /<(?:button|a)\b[^>]*class="(?:kpi|attention-btn|r risk-link)"/);
  assert.doesNotMatch(html, /data-go-filter="(?:draft|escalated|failed|queue|risk:[^"]*)"/);
  assert.doesNotMatch(html, /aria-label="View (?:AI drafts|Critical|High|Normal|Low)/);
  assert.match(html, /Informational (?:totals|summaries)/);
  assert.match(html, /[Ff]iltered inbox drill-downs (?:are )?unavailable/);
});

test("UI-03: explicit all-inbox and individual ticket actions remain available", () => {
  const html = harness().overview();
  assert.match(html, /<button[^>]*data-go-filter="all"[^>]*>Open inbox/);
  assert.match(html, /<button[^>]*data-go-ticket="42"/);
});

test("UI-03: historical KPI and risk totals stay separate from recent-ticket counts", () => {
  const html = harness().overview();
  assert.match(html, /<strong>1<\/strong>[\s\S]*?Recent escalations/);
  assert.match(html, /AI drafts written<\/div><div class="v">90<\/div>/);
  assert.match(html, /Escalated to you<\/div><div class="v">13<\/div>/);
  assert.match(html, /Critical<\/div>[\s\S]*?<div class="n">7<\/div>/);
});

test("UI-03: unsupported filter-only calls never silently navigate to All", () => {
  for (const filter of ["draft", "escalated", "failed", "queue", "risk:critical", "unknown", "mine", "unassigned", "snoozed", "trash", "spam"]) {
    const context = harness();
    context.goTickets(filter);
    assert.equal(context.tab, "overview", `${filter} is not an inbox view`);
    assert.equal(context.inboxNavView, "closed");
  }
});

test("UI-03: every supported inbox view retains its exact navigation", () => {
  const VIEW_IDS = ["all", "open", "closed"];
  for (const view of VIEW_IDS) {
    const context = harness();
    context.goTickets(view);
    const destination = new URL(context.location.destination, "https://support.example.com");
    assert.equal(destination.pathname, "/inbox2/");
    assert.equal(destination.searchParams.get("view") || "all", view);
    assert.equal(context.inboxNavView, view);
    assert.equal(context.inboxNavTicket, null);
  }
});

test("UI-03: per-ticket notifications remain reachable despite legacy dashboard filters", () => {
  for (const filter of ["all", "escalated", "failed", "risk:high"]) {
    const context = harness();
    context.goTickets(filter, 42);
    const destination = new URL(context.location.destination, "https://support.example.com");
    assert.equal(destination.pathname, "/inbox2/");
    assert.equal(destination.searchParams.get("ticket"), "gorgias:42");
    assert.equal(context.inboxNavView, "all");
    assert.equal(context.inboxNavTicket, "gorgias:42");
  }
  const context = harness();
  context.goTickets("closed", "local:42");
  assert.equal(context.inboxNavView, "closed");
  assert.equal(context.inboxNavTicket, "local:42");
});
