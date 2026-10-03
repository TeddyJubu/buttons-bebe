const test = require("node:test");
const assert = require("node:assert/strict");
const { loadServer } = require("./server-harness");

test("actual initial startup uses five-failure reconnect budget and exits", async () => {
  let starts = 0;
  const exit = new Error("synthetic process exit");
  const h = await loadServer({ auth: async () => { starts++; throw new Error("synthetic startup failure"); },
    exit: code => { assert.equal(code, 1); throw exit; } });
  assert.equal(starts, 1);
  for (let attempt = 0; attempt < 3; attempt++) await h.timers[attempt]();
  await assert.rejects(h.timers[3](), error => error === exit);
  assert.equal(starts, 5);
  assert.equal(h.timers.length, 4);
});

async function unlink(h) {
  let status = 200, payload;
  await h.handlers.get("POST /wa/logout")({}, {
    status(code) { status = code; return this; }, json(value) { payload = JSON.parse(JSON.stringify(value)); },
  });
  return { status, payload };
}

test("logout rejection and missing startup socket preserve observed identity", async () => {
  const h = await loadServer({ logout: async () => { throw new Error("synthetic-private-error@example.test"); } });
  await h.events.get("connection.update")({ connection: "open" });
  const before = h.observed();
  assert.deepEqual(await unlink(h), { status: 503, payload: { ok: false, error: "unlink_unavailable" } });
  assert.deepEqual(h.observed(), before);
  const starting = await loadServer({ auth: () => new Promise(() => {}) });
  assert.deepEqual(await unlink(starting), { status: 409, payload: { ok: false, error: "unlink_unconfirmed" } });
  assert.deepEqual(starting.observed(), { state: "starting", qr: null, owner: null });
});

test("successful logout requires an observed logged-out event or already-unlinked state", async () => {
  const h = await loadServer();
  await h.events.get("connection.update")({ connection: "open" });
  assert.deepEqual(await unlink(h), { status: 200, payload: { ok: true } });
  assert.deepEqual(h.observed(), { state: "qr", qr: null, owner: null });
  assert.deepEqual(await unlink(h), { status: 200, payload: { ok: true, alreadyUnlinked: true } });
  const delayed = await loadServer({ logout: async () => {} });
  await delayed.events.get("connection.update")({ connection: "open" });
  const before = delayed.observed();
  assert.deepEqual(await unlink(delayed), { status: 409, payload: { ok: false, error: "unlink_unconfirmed" } });
  assert.deepEqual(delayed.observed(), before);
});
