const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const ROOT = path.resolve(__dirname, "..", "..");
const SERVER = path.join(ROOT, "kb-admin", "server.js");

function reservePort() {
  return new Promise((resolve, reject) => {
    const server = http.createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close((error) => error ? reject(error) : resolve(port));
    });
  });
}

async function waitUntilReady(baseUrl, child) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (child.exitCode !== null) throw new Error(`kb-admin exited with ${child.exitCode}`);
    try {
      const response = await fetch(`${baseUrl}/health`);
      if (response.ok) return;
    } catch (_) {}
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  throw new Error("kb-admin did not become ready");
}

async function startServer(t) {
  const kb = fs.mkdtempSync(path.join(os.tmpdir(), "bb-kb-admin-"));
  for (const folder of ["intents", "faq", "policies", "tickets", "products", "notices"])
    fs.mkdirSync(path.join(kb, folder), { recursive: true });
  fs.writeFileSync(path.join(kb, "intents", "shipping.md"), "---\ntitle: Shipping\n---\n\nCurrent text\n");
  fs.writeFileSync(path.join(kb, "intents", "README.md"), "not content\n");
  fs.writeFileSync(path.join(kb, "policies", "returns.md"), "---\ntitle: Returns\n---\n");
  fs.writeFileSync(path.join(kb, "products", "product-one.md"), "---\ntitle: One\n---\n");
  fs.writeFileSync(path.join(kb, "notices", "notices.json"), "[]\n");

  const port = await reservePort();
  const child = spawn(process.execPath, [SERVER], {
    env: { ...process.env, KB_DIR: kb, KB_ADMIN_PORT: String(port), KB_PRODUCT_FRESH_HOURS: "96" },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let stderr = "";
  child.stderr.on("data", (chunk) => { stderr += chunk; });
  const baseUrl = `http://127.0.0.1:${port}`;
  await waitUntilReady(baseUrl, child);
  t.after(() => {
    child.kill("SIGTERM");
    fs.rmSync(kb, { recursive: true, force: true });
    assert.equal(stderr, "");
  });
  return { kb, baseUrl };
}

test("health reports live file counts and product freshness", async (t) => {
  const { baseUrl } = await startServer(t);
  const response = await fetch(`${baseUrl}/health`);
  assert.equal(response.status, 200);
  const health = await response.json();
  assert.deepEqual(health.folders, { intents: 1, faq: 0, policies: 1, tickets: 0 });
  assert.equal(health.editable_files, 2);
  assert.equal(health.products.count, 1);
  assert.equal(health.products.fresh, true);
  assert.match(health.products.last_modified, /^\d{4}-\d{2}-\d{2}T/);
});

test("file reads reject traversal and distinguish missing files", async (t) => {
  const { baseUrl } = await startServer(t);
  const traversal = await fetch(`${baseUrl}/file?path=${encodeURIComponent("../.env")}`);
  assert.equal(traversal.status, 400);
  assert.deepEqual(await traversal.json(), { error: "bad path" });

  const missing = await fetch(`${baseUrl}/file?path=${encodeURIComponent("policies/missing.md")}`);
  assert.equal(missing.status, 404);
  assert.deepEqual(await missing.json(), { error: "not found" });
});

test("notice mutation respects the shared Python lock and preserves data", async (t) => {
  const { kb, baseUrl } = await startServer(t);
  const noticesFile = path.join(kb, "notices", "notices.json");
  fs.writeFileSync(noticesFile, JSON.stringify([{ id: "n_existing", text: "Keep me", created_at: new Date().toISOString(), expires_at: null, created_by: "owner" }]));
  fs.mkdirSync(path.join(kb, "notices", ".notices.lock"));

  const response = await fetch(`${baseUrl}/notices`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ text: "Do not add" }),
  });
  assert.equal(response.status, 409);
  assert.deepEqual(await response.json(), { error: "notice board busy" });
  assert.equal(JSON.parse(fs.readFileSync(noticesFile, "utf8")).length, 1);
});

test("console binds only KB item buttons and disables saving after a load error", () => {
  const html = fs.readFileSync(path.join(ROOT, "console-src", "index.html"), "utf8");
  const scripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)];
  assert.ok(scripts.length > 0, "console should contain an inline application script");
  for (const match of scripts) assert.doesNotThrow(() => new Function(match[1]));
  assert.match(html, /querySelectorAll\("\.kbitem\[data-open\]"\)/);
  assert.doesNotMatch(html, /querySelectorAll\("\[data-open\]"\)/);
  assert.match(html, /Nothing was changed, and saving is disabled/);
  assert.match(html, /!kbLoaded\|\|kbLoadError/);
  assert.doesNotMatch(html, /4,246 products|17 policies|All services responding/);
});
