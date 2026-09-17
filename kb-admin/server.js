/**
 * Buttons Bebe - KB admin API (zero-dependency Node).
 * Lets the console read/edit the knowledge-base markdown files and re-index them.
 * Bound to localhost:8087; exposed via Caddy behind the console's auth gate at
 * /console/kbapi/*. Only the editable content folders are writable; products/ and
 * learned/ are excluded, and paths are strictly validated (no traversal).
 */
const http = require("http");
const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");

const PORT = process.env.KB_ADMIN_PORT || 8087;
const KB = process.env.KB_DIR || "/root/Buttonsbebe Agent/KB";
const FOLDERS = ["intents", "faq", "policies", "tickets"];
const NOTICES_DIR = path.join(KB, "notices");
const NOTICES_FILE = path.join(NOTICES_DIR, "notices.json");
const NOTICE_LOCK_DIR = path.join(NOTICES_DIR, ".notices.lock");
const NOTICE_LOCK_STALE_MS = 60 * 1000;
const NOTICE_TEXT_MAX = 1200;
const PRODUCT_FRESH_HOURS = Number(process.env.KB_PRODUCT_FRESH_HOURS || 96);

let reindex = { running: false, ok: null, at: null };

function safePath(p) {
  if (!p || typeof p !== "string" || p.includes("..") || !p.endsWith(".md")) return null;
  const parts = p.split("/");
  if (parts.length !== 2) return null;
  const [folder, name] = parts;
  if (!FOLDERS.includes(folder)) return null;
  if (!/^[A-Za-z0-9._-]+\.md$/.test(name)) return null;
  const parent = path.join(KB, folder), candidate = path.join(parent, name);
  try {
    if (fs.lstatSync(parent).isSymbolicLink() || fs.realpathSync(parent) !== path.join(fs.realpathSync(KB), folder)) return null;
    if (fs.existsSync(candidate) && (!fs.lstatSync(candidate).isFile() || fs.lstatSync(candidate).isSymbolicLink())) return null;
  } catch (_) { return null; }
  return candidate;
}
const BACKUP_DIR = path.join(KB, ".backups");
const BACKUP_KEEP = 20;
// Backups live in KB/.backups/, not beside the documents: dot-prefix keeps them
// invisible to the indexer and /list, and the ring caps unbounded growth.
function backupFile(fp) {
  const rel = path.relative(KB, fp).split(path.sep).join("/");
  fs.mkdirSync(BACKUP_DIR, { recursive: true });
  const backup = path.join(BACKUP_DIR, `${rel.replace("/", "__")}.bak-${Date.now()}-${Math.random().toString(16).slice(2)}`);
  fs.copyFileSync(fp, backup, fs.constants.COPYFILE_EXCL);
  const backupFd = fs.openSync(backup, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
  try { fs.fsyncSync(backupFd); } finally { fs.closeSync(backupFd); }
  const prefix = `${rel.replace("/", "__")}.bak-`;
  const ring = fs.readdirSync(BACKUP_DIR).filter((n) => n.startsWith(prefix)).sort();
  while (ring.length > BACKUP_KEEP) fs.unlinkSync(path.join(BACKUP_DIR, ring.shift()));
  // fsync the directory after create AND prune: after create, so a crash
  // cannot lose the just-made backup entry; after prune, so a crash cannot
  // resurrect the deleted ring entries (the earlier sync alone let a crash
  // post-unlink re-expose pruned backups).
  const dirFd = fs.openSync(BACKUP_DIR, fs.constants.O_RDONLY);
  try { fs.fsyncSync(dirFd); } finally { fs.closeSync(dirFd); }
  return backup;
}
function atomicSave(fp, content) {
  const rel=path.relative(KB,fp).split(path.sep).join("/");
  if(safePath(rel)!==fp)throw new Error("unsafe KB path");
  const existing=fs.existsSync(fp)?fs.lstatSync(fp):null;
  const mode=existing ? existing.mode & 0o777 : 0o644;
  const tmp=path.join(path.dirname(fp),`.kb-save-${process.pid}-${Date.now()}-${Math.random().toString(16).slice(2)}.tmp`);
  let fd;
  try {
    fd=fs.openSync(tmp,fs.constants.O_WRONLY|fs.constants.O_CREAT|fs.constants.O_EXCL|fs.constants.O_NOFOLLOW,mode);
    fs.fchmodSync(fd,mode);
    fs.writeFileSync(fd,content,"utf8");fs.fsyncSync(fd);fs.closeSync(fd);fd=undefined;
    if(safePath(rel)!==fp)throw new Error("KB path changed during save");
    if(existing) backupFile(fp);
    if(existing)fs.renameSync(tmp,fp);
    else {fs.linkSync(tmp,fp);fs.unlinkSync(tmp);} // Exclusive new-file publication.
    const directory=fs.openSync(path.dirname(fp),fs.constants.O_RDONLY);
    try {fs.fsyncSync(directory);} finally {fs.closeSync(directory);}
  } finally {
    if(fd!==undefined)fs.closeSync(fd);
    if(fs.existsSync(tmp))fs.unlinkSync(tmp);
  }
}

function frontTitle(txt) {
  const m = txt.match(/^title:\s*(.+)$/m);
  return m ? m[1].replace(/^["']|["']$/g, "").trim() : null;
}
function send(res, code, obj) {
  res.writeHead(code, { "content-type": "application/json" });
  res.end(JSON.stringify(obj));
}
function readBody(req, res, cb) {
  const chunks=[]; let size=0,done=false;
  const timer=setTimeout(()=>fail(408,"request body timed out"),10000);
  const fail=(code,message)=>{if(done)return;done=true;clearTimeout(timer);chunks.length=0;send(res,code,{error:message});req.resume();};
  req.on("data",chunk=>{if(done)return;size+=chunk.length;if(size>1024*1024)return fail(413,"request body too large");chunks.push(chunk);});
  req.on("error",()=>fail(400,"invalid request body"));
  req.on("close",()=>{done=true;clearTimeout(timer);chunks.length=0;});
  req.on("end",()=>{
    if(done)return;
    let body;
    try { body=JSON.parse(Buffer.concat(chunks).toString("utf8")); }
    catch (_) { return fail(400,"invalid JSON"); }
    if(!body || typeof body!=="object" || Array.isArray(body))return fail(400,"JSON object required");
    done=true;clearTimeout(timer);cb(body);
  });
}

function contentFiles(folder) {
  const dir = path.join(KB, folder);
  try {
    return fs.readdirSync(dir)
      .filter((n) => (folder === "products" || safePath(folder + "/" + n)) && n.endsWith(".md") && !n.startsWith("_") && n.toLowerCase() !== "readme.md")
      .sort();
  } catch (e) {
    return null;
  }
}

let _healthCache = null; // {expires, value} — /health scans every product file; 60s keeps console refreshes cheap
function kbHealth() {
  const now = Date.now();
  if (_healthCache && now < _healthCache.expires) return _healthCache.value;
  const folders = {};
  let ok = true;
  for (const folder of FOLDERS) {
    const files = contentFiles(folder);
    folders[folder] = files === null ? null : files.length;
    if (files === null) ok = false;
  }

  const productFiles = contentFiles("products");
  let newest = null;
  if (productFiles === null) {
    ok = false;
  } else {
    for (const name of productFiles) {
      try {
        const mtime = fs.statSync(path.join(KB, "products", name)).mtimeMs;
        if (newest === null || mtime > newest) newest = mtime;
      } catch (e) {
        ok = false;
      }
    }
  }
  const ageHours = newest === null ? null : Math.max(0, (now - newest) / 3600000);
  const threshold = Number.isFinite(PRODUCT_FRESH_HOURS) && PRODUCT_FRESH_HOURS > 0 ? PRODUCT_FRESH_HOURS : 96;
  const value = {
    ok,
    generated_at: new Date(now).toISOString(),
    folders,
    editable_files: Object.values(folders).every(Number.isInteger)
      ? Object.values(folders).reduce((sum, count) => sum + count, 0)
      : null,
    products: {
      count: productFiles === null ? null : productFiles.length,
      last_modified: newest === null ? null : new Date(newest).toISOString(),
      age_hours: ageHours === null ? null : Math.round(ageHours * 10) / 10,
      fresh: ageHours !== null && ageHours <= threshold,
      fresh_for_hours: threshold,
    },
  };
  _healthCache = { expires: now + 60000, value };
  return value;
}

// ---- Notice Board (owner overrides; shared JSON with notices_lib.py) --------
function validNotice(n) {
  if (!n || typeof n !== "object") return false;
  if (!["id", "text", "created_at", "created_by"].every((k) => typeof n[k] === "string" && n[k].trim())) return false;
  if (Number.isNaN(Date.parse(n.created_at))) return false;
  return n.expires_at === null || (typeof n.expires_at === "string" && !Number.isNaN(Date.parse(n.expires_at)));
}
function readNotices(strict) {
  const d = JSON.parse(fs.readFileSync(NOTICES_FILE, "utf8"));
  if (!Array.isArray(d)) throw new Error("notice store must be a JSON list");
  const valid = d.filter(validNotice);
  if (strict && valid.length !== d.length) throw new Error("notice store contains malformed entries");
  return valid;
}
function loadNoticesStrict() {
  return fs.existsSync(NOTICES_FILE) ? readNotices(true) : [];
}
function acquireNoticeLock() {
  fs.mkdirSync(NOTICES_DIR, { recursive: true });
  try {
    fs.mkdirSync(NOTICE_LOCK_DIR);
  } catch (e) {
    const stale = e.code === "EEXIST" && (() => {
      try { return Date.now() - fs.statSync(NOTICE_LOCK_DIR).mtimeMs > NOTICE_LOCK_STALE_MS; }
      catch (_) { return false; }
    })();
    if (!stale) { const busy = new Error("notice board write already running"); busy.code = "EBUSY"; throw busy; }
    try { fs.rmdirSync(NOTICE_LOCK_DIR); fs.mkdirSync(NOTICE_LOCK_DIR); }
    catch (err) { const busy = new Error("notice board write already running"); busy.code = "EBUSY"; throw busy; }
  }
}
function releaseNoticeLock() {
  try { fs.rmdirSync(NOTICE_LOCK_DIR); } catch (e) {}
}
function withNoticeLock(fn) {
  acquireNoticeLock();
  try { return fn(); }
  finally { releaseNoticeLock(); }
}
function writeNotices(items) {
  fs.mkdirSync(NOTICES_DIR, { recursive: true });
  const tmp = path.join(NOTICES_DIR, `.notices-${process.pid}-${Date.now()}-${Math.random().toString(16).slice(2)}.tmp`);
  try {
    fs.writeFileSync(tmp, JSON.stringify(items, null, 2), "utf8");
    fs.renameSync(tmp, NOTICES_FILE); // atomic swap so a reader never sees a half file
  } finally {
    try { fs.unlinkSync(tmp); } catch (e) {}
  }
}
function noticeActive(n, now) {
  if (!n.expires_at) return true;
  const t = Date.parse(n.expires_at);
  return isNaN(t) ? true : t > now;
}

const server = http.createServer((req, res) => {
  let u;
  try { u = new URL(req.url, "http://x"); } catch { return send(res, 400, { error: "bad url" }); }
  const p = u.pathname;

  if (req.method === "GET" && p === "/health") {
    const health = kbHealth();
    return send(res, health.ok ? 200 : 503, health);
  }

  if (req.method === "GET" && p === "/list") {
    const folders = FOLDERS.map((f) => {
      const dir = path.join(KB, f);
      const files = contentFiles(f) || [];
      return {
        folder: f,
        files: files.map((n) => {
          let title = null;
          try { title = frontTitle(fs.readFileSync(path.join(dir, n), "utf8")); } catch (e) {}
          return { name: n, path: f + "/" + n, title: title || n };
        }),
      };
    });
    return send(res, 200, { folders, reindex });
  }

  if (req.method === "GET" && p === "/file") {
    const fp = safePath(u.searchParams.get("path"));
    if (!fp) return send(res, 400, { error: "bad path" });
    try { return send(res, 200, { path: u.searchParams.get("path"), content: fs.readFileSync(fp, "utf8") }); }
    catch (e) { return send(res, 404, { error: "not found" }); }
  }

  if (req.method === "POST" && (p === "/save" || p === "/new")) {
    return readBody(req, res, (d) => {
      let rel = d.path;
      if (p === "/new") {
        if (!FOLDERS.includes(d.folder) || !d.filename) return send(res, 400, { error: "folder + filename required" });
        let fn = String(d.filename).trim().toLowerCase().replace(/[^a-z0-9-]+/g, "-").replace(/^-+|-+$/g, "");
        if (!fn) return send(res, 400, { error: "invalid filename" });
        if (!fn.endsWith(".md")) fn += ".md";
        rel = d.folder + "/" + fn;
      }
      const fp = safePath(rel);
      if (!fp) return send(res, 400, { error: "bad path" });
      if (p === "/new" && fs.existsSync(fp)) return send(res, 409, { error: "file already exists" });
      try {
        if (d.content != null && typeof d.content !== "string") return send(res,400,{error:"content must be text"});
        atomicSave(fp,d.content || "");
        // One-line audit per write: goes to journald via stdout (08-5/R4).
        console.log(JSON.stringify({ event: "kb-save", path: rel, size: Buffer.byteLength(d.content || ""), ts: new Date().toISOString() }));
        return send(res, 200, { ok: true, path: rel });
      } catch (e) { return send(res, 500, { error: String(e) }); }
    });
  }

  if (req.method === "POST" && p === "/reindex") {
    if (reindex.running) return send(res, 200, { started: false, reindex });
    reindex = { running: true, ok: null, at: new Date().toISOString() };
    const ch = spawn("/bin/bash", [path.join(KB, "update.sh")], { cwd: KB, stdio: ["ignore", "ignore", "ignore"] });
    ch.on("close", (code) => { reindex = { running: false, ok: code === 0, at: new Date().toISOString() }; });
    ch.on("error", () => { reindex = { running: false, ok: false, at: new Date().toISOString() }; });
    return send(res, 200, { started: true, reindex: { running: true } });
  }

  if (req.method === "GET" && p === "/reindex-status") return send(res, 200, reindex);

  if (req.method === "GET" && p === "/notices") {
    const now = Date.now();
    try {
      const notices = loadNoticesStrict().map((n) => ({ ...n, active: noticeActive(n, now) }));
      return send(res, 200, { notices, now: new Date(now).toISOString() });
    } catch (e) {
      return send(res, 503, { error: "notice store unavailable" });
    }
  }

  if (req.method === "POST" && p === "/notices") {
    return readBody(req, res, (d) => {
      const text = (d && d.text ? String(d.text) : "").trim();
      if (!text) return send(res, 400, { error: "text required" });
      if (text.length > NOTICE_TEXT_MAX) return send(res, 400, { error: `text must be ${NOTICE_TEXT_MAX} characters or fewer` });
      let expires_at = null;
      if (d.expires_at !== undefined && d.expires_at !== null && d.expires_at !== "") {
        const t = Date.parse(String(d.expires_at));
        if (isNaN(t)) return send(res, 400, { error: "bad deadline" });
        if (t <= Date.now()) return send(res, 400, { error: "deadline must be in the future" });
        expires_at = new Date(t).toISOString();
      }
      const notice = {
        id: "n_" + Date.now() + "_" + Math.random().toString(16).slice(2, 6),
        text,
        created_at: new Date().toISOString(),
        expires_at,
        created_by: "owner",
      };
      try {
        withNoticeLock(() => { const items = loadNoticesStrict(); items.push(notice); writeNotices(items); });
        return send(res, 200, { ok: true, notice });
      } catch (e) {
        return send(res, e.code === "EBUSY" ? 409 : 500, { error: e.code === "EBUSY" ? "notice board busy" : String(e) });
      }
    });
  }

  if (req.method === "POST" && p === "/notices/delete") {
    return readBody(req, res, (d) => {
      const id = d && d.id ? String(d.id) : "";
      try {
        const removed = withNoticeLock(() => {
          const items = loadNoticesStrict();
          const kept = items.filter((n) => n.id !== id);
          if (kept.length === items.length) return false;
          writeNotices(kept);
          return true;
        });
        if (!removed) return send(res, 404, { error: "not found" });
        return send(res, 200, { ok: true, removed: id });
      } catch (e) {
        return send(res, e.code === "EBUSY" ? 409 : 500, { error: e.code === "EBUSY" ? "notice board busy" : String(e) });
      }
    });
  }

  send(res, 404, { error: "not found" });
});

server.listen(PORT, "127.0.0.1", () => console.log("kb-admin listening on 127.0.0.1:" + PORT));
