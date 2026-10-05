const test = require("node:test");
const assert = require("node:assert/strict");
const { hermesArguments, childEnvironment } = require("../hermes-permissions");
const { loadServer } = require("./server-harness");

const canonical = "buttonsbebe_kb,buttonsbebe_redo,buttonsbebe_gorgias";
const names = canonical.split(",");

test("only a complete read-only set produces canonical child arguments", () => {
  for (const a of names) for (const b of names) for (const c of names) {
    const value = ` ${a} , ${b} , ${c} `;
    if (new Set([a, b, c]).size === 3) assert.deepEqual(hermesArguments("question", value), ["-t", canonical, "-z", "question"]);
    else assert.throws(() => hermesArguments("question", value), /exactly/);
  }
  assert.deepEqual(hermesArguments("question"), ["-t", canonical, "-z", "question"]);
  for (const value of ["", "buttonsbebe_kb", canonical + ",", canonical + ",file", null]) {
    assert.throws(() => hermesArguments("question", value), /exactly/);
  }
  assert.deepEqual(childEnvironment({ WA_TOKEN: "private", OPENAI_API_KEY: "synthetic", HERMES_OS_HOME: "/home", HERMES_PATH: "/bin" }),
    { OPENAI_API_KEY: "synthetic", HOME: "/home", PATH: "/bin" });
});

test("owner message event launches restricted Hermes and filters private environment", async () => {
  const h = await loadServer({ env: { HERMES_TOOLSETS: "buttonsbebe_gorgias,buttonsbebe_redo,buttonsbebe_kb" } });
  await h.events.get("connection.update")({ connection: "open" });
  const jid = h.observed().owner;
  await h.events.get("messages.upsert")({ messages: [{ key: { fromMe: true, remoteJid: jid, id: "owner-id" }, message: { conversation: "Owner question" } }] });
  assert.deepEqual(h.launches, [{ bin: "/controlled/hermes", args: ["-t", canonical, "-z", "Owner question"],
    config: { timeout: 150000, maxBuffer: 4194304, env: { LANG: "C", OPENAI_API_KEY: "synthetic-model", HOME: "/controlled", PATH: "/controlled/bin" } } }]);
  assert.deepEqual(h.sent, [{ jid, text: "Private owner answer" }]);
  for (const key of [{ fromMe: false, remoteJid: jid, id: "stranger" }, { fromMe: true, remoteJid: "other@s.whatsapp.net", id: "other-chat" },
    { fromMe: true, remoteJid: jid, id: "bot-id" }]) {
    await h.events.get("messages.upsert")({ messages: [{ key, message: { conversation: "Ignored message" } }] });
  }
  assert.equal(h.launches.length, 1);
});

test("invalid tools refuse the model but preserve private fallback and alert delivery", async () => {
  for (const value of ["", "buttonsbebe_kb", canonical + ",terminal"]) {
    const h = await loadServer({ env: { HERMES_TOOLSETS: value } });
    await h.events.get("connection.update")({ connection: "open" });
    const jid = h.observed().owner;
    await h.events.get("messages.upsert")({ messages: [{ key: { fromMe: true, remoteJid: jid, id: "owner-id" }, message: { conversation: "Owner question" } }] });
    assert.equal(h.launches.length, 0);
    assert.deepEqual(h.sent, [{ jid, text: "Sorry — I couldn't process that right now." }]);
    const response = await new Promise(resolve => h.handlers.get(`POST /connect-whatsapp/${h.context.process.env.WA_TOKEN}/send`)(
      { body: { text: "Synthetic alert" } }, { json: resolve, status() { return this; } }));
    assert.deepEqual(JSON.parse(JSON.stringify(response)), { ok: true, to: jid });
    assert.deepEqual(h.sent[1], { jid, text: "Synthetic alert" });
  }
});
