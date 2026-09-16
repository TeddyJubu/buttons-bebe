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

test("notice creation rejects unsafe length and deadlines without changing the store", async (t) => {
  const { kb, baseUrl } = await startServer(t);
  const noticesFile = path.join(kb, "notices", "notices.json");

  const tooLong = await fetch(`${baseUrl}/notices`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ text: "x".repeat(1201), expires_at: null }),
  });
  assert.equal(tooLong.status, 400);
  assert.deepEqual(await tooLong.json(), { error: "text must be 1200 characters or fewer" });

  const past = await fetch(`${baseUrl}/notices`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ text: "Already expired", expires_at: "2000-01-01T00:00:00.000Z" }),
  });
  assert.equal(past.status, 400);
  assert.deepEqual(await past.json(), { error: "deadline must be in the future" });
  assert.deepEqual(JSON.parse(fs.readFileSync(noticesFile, "utf8")), []);
});

test("notice creation derives its actor on the server and preserves explicit no-expiry", async (t) => {
  const { kb, baseUrl } = await startServer(t);
  const noticesFile = path.join(kb, "notices", "notices.json");
  const response = await fetch(`${baseUrl}/notices`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ text: "  Confirmed store fact  ", expires_at: null, created_by: "spoofed-user" }),
  });
  assert.equal(response.status, 200);
  const { notice } = await response.json();
  assert.equal(notice.text, "Confirmed store fact");
  assert.equal(notice.expires_at, null);
  assert.equal(notice.created_by, "owner");
  assert.deepEqual(JSON.parse(fs.readFileSync(noticesFile, "utf8")), [notice]);
});

test("malformed notice storage is unavailable rather than an empty board", async (t) => {
  const { kb, baseUrl } = await startServer(t);
  fs.writeFileSync(path.join(kb, "notices", "notices.json"), JSON.stringify([{ id: "broken" }]));
  const response = await fetch(`${baseUrl}/notices`);
  assert.equal(response.status, 503);
  assert.deepEqual(await response.json(), { error: "notice store unavailable" });
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
  assert.doesNotMatch(html, />Healthy</);
  assert.match(html, /Content current/);
  assert.doesNotMatch(html, /Post drafts to Gorgias|gorgias_writes_enabled/);
  assert.match(html, /sensitiveDrafts=stats\.sensitive_draft\|\|0/);
  assert.match(html, /noKbDrafts=stats\.no_kb_match\|\|0/);
  assert.match(html, /Raw lessons stay out of search with restricted file permissions/);
  assert.doesNotMatch(html, /Personal details are stripped before anything is saved/);
  assert.match(html, /<form id="nb-form" class="notice-form" novalidate>/);
  assert.match(html, /<label class="notice-label" for="nb-text">/);
  assert.match(html, /Review before publishing/);
  assert.match(html, /Publish override/);
  assert.match(html, /No expiry — manual removal/);
  assert.match(html, /Notice Board status is unavailable/);
  assert.match(html, /Active overrides may still exist/);
  assert.match(html, /NOTICE_TEXT_MAX=1200/);
  assert.match(html, /if\(!noticeReview\|\|noticeBusy\|\|noticesError\)return/);
  assert.match(html, /noticesData=previous;noticeReview=false/);
  assert.match(html, /function bindCompactHeader\(\)/);
  assert.match(html, /const next=headerCompact\?y>12:y>36/);
  assert.match(html, /class="main \$\{headerCompact\?'header-compact':''\}"/);
  assert.match(html, /class="wrap" tabindex="0" aria-label="Dashboard content"/);
  assert.match(html, /\.main\.header-compact \.header-title \.sub/);
  assert.doesNotMatch(html, /if\(!confirm\("Remove this notice/);
  assert.doesNotMatch(html, /jget\(KBAPI\+"\/notices"\)\|\|\{notices:\[\]\}/);
});

test("invalid and oversized JSON do not alter KB content", async(t)=>{
 const {baseUrl,kb}=await startServer(t);
 const before=fs.readFileSync(path.join(kb,"intents","shipping.md"),"utf8");
 for(const body of ['null','[]','{',JSON.stringify({path:'intents/shipping.md',content:'x'.repeat(1024*1024)})]){
  const response=await fetch(baseUrl+'/save',{method:'POST',headers:{'content-type':'application/json'},body});
  assert.ok([400,413].includes(response.status));
 }
 assert.equal(fs.readFileSync(path.join(kb,"intents","shipping.md"),"utf8"),before);
});
test("KB file and folder symlinks cannot expose or overwrite outside files",async(t)=>{
 const {baseUrl,kb}=await startServer(t);
 const privateFile=path.join(kb,'private.txt');fs.writeFileSync(privateFile,'PRIVATE TEST VALUE');
 fs.symlinkSync(privateFile,path.join(kb,'faq','linked.md'));
 assert.equal((await fetch(baseUrl+'/file?path=faq/linked.md')).status,400);
 assert.equal((await fetch(baseUrl+'/save',{method:'POST',body:JSON.stringify({path:'faq/linked.md',content:'bad'})})).status,400);
 assert.equal(fs.readFileSync(privateFile,'utf8'),'PRIVATE TEST VALUE');
 fs.rmdirSync(path.join(kb,'tickets'));fs.symlinkSync(kb,path.join(kb,'tickets'));
 assert.equal((await fetch(baseUrl+'/file?path=tickets/private.md')).status,400);
});

test("interrupted atomic publication preserves old document and file mode",()=>{
 const vm=require('node:vm');
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'bb-kb-atomic-'));
 try{
  fs.mkdirSync(path.join(root,'intents'));const fp=path.join(root,'intents','test.md');
  fs.writeFileSync(fp,'old complete content');fs.chmodSync(fp,0o640);
  const source=fs.readFileSync(SERVER,'utf8');
  const functions=source.slice(source.indexOf('function safePath('),source.indexOf('function frontTitle('));
  const broken={...fs,renameSync(){throw new Error('simulated publication interruption');}};
  const context=vm.createContext({fs:broken,path,KB:root,FOLDERS:['intents'],process,Math,Date});
  vm.runInContext(functions,context);
  assert.throws(()=>context.atomicSave(fp,'replacement'),/interruption/);
  assert.equal(fs.readFileSync(fp,'utf8'),'old complete content');
  assert.equal(fs.statSync(fp).mode & 0o777,0o640);
  assert.ok(!fs.readdirSync(path.dirname(fp)).some(name=>name.startsWith('.kb-save-')));
  context.fs=fs;context.atomicSave(fp,'new complete content');
  assert.equal(fs.readFileSync(fp,'utf8'),'new complete content');
  assert.equal(fs.statSync(fp).mode & 0o777,0o640);
 }finally{fs.rmSync(root,{recursive:true,force:true});}
});
