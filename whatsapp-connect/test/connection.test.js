const test = require("node:test");
const assert = require("node:assert/strict");

const { nextStateOnClose, sendWithRetry } = require("../connection");

test("nextStateOnClose: loggedOut re-pairs via QR, everything else reconnects", () => {
  assert.equal(nextStateOnClose(401, 401), "qr"); // DisconnectReason.loggedOut
  assert.equal(nextStateOnClose(428, 401), "connecting"); // restart required
  assert.equal(nextStateOnClose(500, 401), "connecting"); // generic close
  assert.equal(nextStateOnClose(undefined, 401), "connecting"); // no statusCode at all
});

test("sendWithRetry holds through retryable pre-check failures, then delivers", async () => {
  const attempts = [];
  const jid = await sendWithRetry(
    () => {
      attempts.push(1);
      if (attempts.length === 1) {
        const e = new Error("whatsapp not connected / no destination");
        e.retryable = true; // sendAlert tags pre-wire failures this way
        throw e;
      }
      return Promise.resolve("owner@s.whatsapp.net");
    },
    { intervalMs: 1, maxWaitMs: 1000 },
  );
  assert.equal(jid, "owner@s.whatsapp.net");
  assert.equal(attempts.length, 2);
});

test("sendWithRetry stops holding once the deadline passes and rethrows", async () => {
  let attempts = 0;
  await assert.rejects(
    sendWithRetry(
      () => {
        attempts += 1;
        const e = new Error("whatsapp not connected / no destination");
        e.retryable = true;
        throw e;
      },
      { intervalMs: 1, maxWaitMs: 100 },
    ),
    /no destination/,
  );
  assert.ok(attempts >= 2, "should have retried before giving up");
});

test("sendWithRetry never retries a non-retryable failure (no double-delivery)", async () => {
  let attempts = 0;
  await assert.rejects(
    sendWithRetry(
      () => {
        attempts += 1;
        throw new Error("boom mid-send"); // no .retryable tag
      },
      { intervalMs: 1, maxWaitMs: 1000 },
    ),
    /boom mid-send/,
  );
  assert.equal(attempts, 1);
});
