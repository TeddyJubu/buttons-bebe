const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const test = require("node:test");

const source = fs.readFileSync(new URL("../index.html", `file://${__filename}`), "utf8");
const start = source.indexOf("function whatsappConnectionSummary(data){");
const end = source.indexOf("\n}\n\nfunction conns(){", start);

assert.notEqual(start, -1, "WhatsApp summary helper is present");
assert.notEqual(end, -1, "WhatsApp summary helper has a stable boundary");

const context = {};
vm.runInNewContext(`${source.slice(start, end + 2)};this.summary=whatsappConnectionSummary;`, context);
const summary = value => JSON.parse(JSON.stringify(context.summary(value)));

test("connection state transitions fail closed", () => {
  assert.deepEqual(summary({ state: "qr" }), {
    label: "Needs linking",
    detail: "Scan the QR in Notifications",
    healthy: false,
  });
  assert.deepEqual(summary({ state: "connected", owner: "15551234567@s.whatsapp.net" }), {
    label: "Connected",
    detail: "Linked device",
    healthy: true,
  });
  assert.deepEqual(summary(null), {
    label: "Status unavailable",
    detail: "Connection status could not be confirmed",
    healthy: false,
  });
});

test("only the explicit connected state is healthy", () => {
  for (const state of ["starting", "qr", "closed", "", null, undefined]) {
    assert.equal(summary(state == null ? null : { state }).healthy, false);
  }
});

function renderConnections(kbHealth, waData, kbHealthError = "") {
  const context = { kbHealth, waData, kbHealthError, whatsappConnectionSummary: summary,
    esc: value => String(value), IC: { check: "SUCCESS_CHECK" } };
  const from = source.indexOf("function conns(){"), to = source.indexOf("\nfunction kbView(){", from);
  assert.ok(from > 0 && to > from);
  vm.runInNewContext(source.slice(from, to), context);
  return context.conns();
}
const kb = fresh => ({ ok: true, folders: {}, editable_files: 12,
  products: { fresh, count: 10, age_hours: fresh ? 2 : 300, last_modified: "2026-09-11T00:00:00Z" } });
function badgeClasses(html, label) {
  const match = html.match(new RegExp(`<span class="([^"]*\\bstt\\b[^"]*)"><span[^>]*><\\/span> ${label}<\\/span>`));
  assert.ok(match, `rendered status badge: ${label}`);
  return match[1];
}

test("UI-04: stale content, refresh-needed Shopify and unlinked WhatsApp are warning badges", () => {
  const html = renderConnections(kb(false), { state: "qr" });
  for (const label of ["Needs refresh", "Needs linking", "Content stale"]) {
    assert.match(badgeClasses(html, label), /\bwarning\b/);
    assert.doesNotMatch(badgeClasses(html, label), /\bhealthy\b/);
  }
  assert.match(html, /class="ic a"[^>]*>!<\/div>/);
  assert.match(html, /Knowledge-base content files need attention/);
  assert.doesNotMatch(html, /SUCCESS_CHECK/);
});

test("UI-04: current connections keep success badges and the success health icon", () => {
  const html = renderConnections(kb(true), { state: "connected" });
  for (const label of ["Configured", "Current", "Connected", "Content current"]) {
    assert.match(badgeClasses(html, label), /\bhealthy\b/);
  }
  assert.match(html, /class="ic g"[^>]*>SUCCESS_CHECK<\/div>/);
  assert.match(html, /Knowledge-base content files are current/);
});

test("UI-04: unavailable data or a failed refresh cannot imply current health", () => {
  for (const [health, error] of [[null, "Could not load file status"], [{ ok: false }, ""], [kb(true), "Refresh failed"]]) {
    const html = renderConnections(health, null, error);
    assert.doesNotMatch(html, /SUCCESS_CHECK|content files are current/);
    assert.match(html, /class="ic a"[^>]*>!<\/div>/);
    assert.match(badgeClasses(html, "Status unavailable"), /\bwarning\b/);
    assert.match(badgeClasses(html, "Files unavailable"), /\bwarning\b/);
  }
});

test("UI-04: badges have distinct semantic styles, not just an off-colored dot", () => {
  assert.match(source, /\.conn \.stt\.healthy\{[^}]*color:var\(--green\)[^}]*background:var\(--green-s\)/);
  assert.match(source, /\.conn \.stt\.warning\{[^}]*color:var\(--amber\)[^}]*background:var\(--amber-s\)/);
});
