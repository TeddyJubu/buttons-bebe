const test = require("node:test");
const assert = require("node:assert/strict");

const {
  clientAddress,
  createSendAuth,
  isAuthorized,
  validateSecret,
} = require("../security");

const SECRET = "send-secret-that-is-at-least-32-bytes";

test("accepts the dedicated send secret as a bearer token", () => {
  assert.equal(isAuthorized(`Bearer ${SECRET}`, SECRET), true);
});

test("accepts the dedicated send secret as a Basic Auth password", () => {
  const credentials = Buffer.from(`processor:${SECRET}`).toString("base64");
  assert.equal(isAuthorized(`Basic ${credentials}`, SECRET), true);
});

test("rejects missing, malformed, and incorrect credentials", () => {
  assert.equal(isAuthorized(undefined, SECRET), false);
  assert.equal(isAuthorized("Basic not-base64!", SECRET), false);
  assert.equal(isAuthorized("Bearer wrong", SECRET), false);
  assert.equal(isAuthorized("Digest anything", SECRET), false);
});

test("middleware fails closed without exposing the secret", () => {
  const rejected = [];
  const middleware = createSendAuth(SECRET, (req) => rejected.push(req));
  let nextCalled = false;
  const response = {
    headers: {},
    statusCode: null,
    body: null,
    set(name, value) { this.headers[name] = value; return this; },
    status(code) { this.statusCode = code; return this; },
    json(body) { this.body = body; return this; },
  };

  middleware({ headers: { authorization: "Bearer wrong" } }, response, () => {
    nextCalled = true;
  });

  assert.equal(nextCalled, false);
  assert.equal(response.statusCode, 401);
  assert.deepEqual(response.body, { error: "unauthorized" });
  assert.equal(JSON.stringify(response).includes(SECRET), false);
  assert.equal(rejected.length, 1);
  assert.equal(rejected[0].headers.authorization, "Bearer wrong");
});

test("middleware passes an authenticated request", () => {
  let rejectionLogged = false;
  const middleware = createSendAuth(SECRET, () => { rejectionLogged = true; });
  let nextCalled = false;
  middleware(
    { headers: { authorization: `Bearer ${SECRET}` } },
    {},
    () => { nextCalled = true; },
  );
  assert.equal(nextCalled, true);
  assert.equal(rejectionLogged, false);
});

test("secret validation rejects missing, weak, and placeholder values", () => {
  assert.throws(() => validateSecret("WA_SEND_SECRET", ""), /WA_SEND_SECRET/);
  assert.throws(() => validateSecret("WA_SEND_SECRET", "short"), /WA_SEND_SECRET/);
  assert.throws(() => validateSecret("WA_SEND_SECRET", " ".repeat(40)), /WA_SEND_SECRET/);
  assert.throws(
    () => validateSecret("WA_SEND_SECRET", "replace-with-random-32-plus-character-send-secret"),
    /WA_SEND_SECRET/,
  );
  assert.throws(() => validateSecret("WA_TOKEN", "changeme"), /WA_TOKEN/);
  assert.equal(validateSecret("WA_SEND_SECRET", SECRET), SECRET);
  assert.equal(validateSecret("WA_SEND_SECRET", `  ${SECRET}  `), SECRET);
});

test("uses a valid forwarded client address only from the loopback proxy", () => {
  assert.equal(
    clientAddress({
      socket: { remoteAddress: "127.0.0.1" },
      headers: { "x-forwarded-for": "203.0.113.9, 127.0.0.1" },
    }),
    "203.0.113.9",
  );
  assert.equal(
    clientAddress({
      socket: { remoteAddress: "198.51.100.4" },
      headers: { "x-forwarded-for": "203.0.113.9" },
    }),
    "198.51.100.4",
  );
  assert.equal(
    clientAddress({
      socket: { remoteAddress: "::1" },
      headers: { "x-forwarded-for": "not-an-ip" },
    }),
    "::1",
  );
});
