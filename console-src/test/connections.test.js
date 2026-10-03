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

async function unlinkResult(fetch) {
  const from = source.indexOf("async function unlinkWa(){"), to = source.indexOf("\nfunction localDateTimeValue", from);
  const observed = { state: "connected", owner: "synthetic-owner" }, timers = [], messages = [];
  const context = { fetch, WAAPI: "/console/waapi", waMsg: "", waSig: "observed-signature", waData: observed,
    confirm: () => true, setTimeout: (fn, ms) => timers.push({ fn, ms }), loadNotifications() {},
    render: () => messages.push(context.waMsg) };
  vm.runInNewContext(source.slice(from, to), context);
  await context.unlinkWa();
  return { message: context.waMsg, signature: context.waSig, observed: context.waData, timers, messages };
}

test("unlink button accepts only HTTP success and confirmed JSON", async () => {
  const success = await unlinkResult(async () => ({ ok: true, json: async () => ({ ok: true }) }));
  assert.equal(success.message, "Unlinked — scan the new QR below to re-link.");
  assert.equal(success.signature, "");
  assert.equal(success.timers.length, 1);
  assert.equal(success.timers[0].ms, 1500);
  for (const fetch of [async () => ({ ok: false, json: async () => ({ ok: true }) }),
    async () => ({ ok: true, json: async () => ({ ok: false }) }),
    async () => ({ ok: true, json: async () => null }),
    async () => ({ ok: true, json: async () => { throw new Error("malformed JSON"); } }),
    async () => { throw new Error("network failure"); }]) {
    const failure = await unlinkResult(fetch);
    assert.equal(failure.message, "Could not confirm unlinking. WhatsApp may still be linked; try again.");
    assert.equal(failure.signature, "observed-signature");
    assert.deepEqual(failure.observed, { state: "connected", owner: "synthetic-owner" });
    assert.equal(failure.timers.length, 0);
  }
});

test("rendered unlink button preserves linked status on failures and refreshes after confirmation", async t => {
  let chromium;
  try { ({ chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright")); }
  catch (error) { if (error.code !== "MODULE_NOT_FOUND") throw error; t.skip("Playwright required"); return; }
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  t.after(() => assert.deepEqual(errors, []));
  page.on("dialog", dialog => dialog.accept());
  let response = { status: 503, json: { ok: false } }, observed = { state: "connected", owner: "15550000000@s.whatsapp.net" };
  await page.route("**/*", route => {
    const request = route.request(), pathname = new URL(request.url()).pathname;
    if (pathname === "/console/") return route.fulfill({ contentType: "text/html", body: source });
    if (pathname === "/console/waapi/logout") {
      if (response.network) return route.abort();
      if (response.json?.ok === true && response.status === 200) observed = { state: "qr", owner: null, qr: null };
      return route.fulfill(response.body ? { status: response.status, body: response.body } : response);
    }
    if (pathname === "/console/waapi/status") return route.fulfill({ json: observed });
    if (pathname === "/console/api/notifications") return route.fulfill({ json: { unread_count: 0, notifications: [] } });
    if (pathname === "/console/api/tickets") return route.fulfill({ json: [] });
    if (pathname.startsWith("/console/api/") || pathname === "/console/kbapi/health") return route.fulfill({ json: {} });
    return route.abort();
  });
  await page.goto("http://console.test/console/");
  await page.locator("[data-tab=notif]").click();
  await page.locator("#wa-unlink").waitFor();
  const before = await page.evaluate(() => ({ signature: waSig, data: waData }));
  for (const outcome of [{ status: 503, json: { ok: false } }, { status: 200, json: { ok: false } },
    { status: 200, body: "malformed" }, { network: true }]) {
    response = outcome;
    await page.locator("#wa-unlink").click();
    await page.locator(".destact .toast").filter({ hasText: "Could not confirm unlinking. WhatsApp may still be linked; try again." }).waitFor();
    assert.deepEqual(await page.evaluate(() => ({ signature: waSig, data: waData })), before);
    assert.equal(await page.locator("#wa-unlink").count(), 1);
  }
  response = { status: 200, json: { ok: true } };
  await page.locator("#wa-unlink").click();
  await page.locator(".destact .toast").filter({ hasText: "Unlinked — scan the new QR below to re-link." }).waitFor();
  await page.waitForFunction(() => waData?.state === "qr");
  assert.equal(await page.locator("#wa-unlink").count(), 0);
});
