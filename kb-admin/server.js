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

let reindex = { running: false, ok: null, at: null };

function safePath(p) {
  if (!p || typeof p !== "string" || p.includes("..") || !p.endsWith(".md")) return null;
  const parts = p.split("/");
  if (parts.length !== 2) return null;
  const [folder, name] = parts;
  if (!FOLDERS.includes(folder)) return null;
  if (!/^[A-Za-z0-9._-]+\.md$/.test(name)) return null;
  return path.join(KB, folder, name);
}
function frontTitle(txt) {
  const m = txt.match(/^title:\s*(.+)$/m);
  return m ? m[1].replace(/^["']|["']$/g, "").trim() : null;
}
function send(res, code, obj) {
  res.writeHead(code, { "content-type": "application/json" });
  res.end(JSON.stringify(obj));
}
function readBody(req, cb) {
  let b = "";
  req.on("data", (c) => (b += c));
  req.on("end", () => { try { cb(JSON.parse(b || "{}")); } catch (e) { cb({}); } });
}

// ---- Notice Board (owner overrides; shared JSON with notices_lib.py) --------
function loadNotices() {
  try { const d = JSON.parse(fs.readFileSync(NOTICES_FILE, "utf8")); return Array.isArray(d) ? d : []; }
  catch (e) { return []; }
}
function writeNotices(items) {
  fs.mkdirSync(NOTICES_DIR, { recursive: true });
  const tmp = NOTICES_FILE + ".tmp";
  fs.writeFileSync(tmp, JSON.stringify(items, null, 2), "utf8");
  fs.renameSync(tmp, NOTICES_FILE); // atomic swap so a reader never sees a half file
}
function noticeActive(n, now) {
  if (!n.expires_at) return true;
  const t = Date.parse(n.expires_at);
  return isNaN(t) ? true : t > now;
}

const server = http.createServer((req, res) => {
  const u = new URL(req.url, "http://x");
  const p = u.pathname;

  if (req.method === "GET" && p === "/list") {
    const folders = FOLDERS.map((f) => {
      const dir = path.join(KB, f);
      let files = [];
      try {
        files = fs.readdirSync(dir)
          .filter((n) => n.endsWith(".md") && !n.startsWith("_") && n.toLowerCase() !== "readme.md")
          .sort();
      } catch (e) {}
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
    return readBody(req, (d) => {
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
        if (fs.existsSync(fp)) fs.copyFileSync(fp, fp + ".bak-" + Date.now());
        fs.writeFileSync(fp, d.content != null ? String(d.content) : "", "utf8");
        return send(res, 200, { ok: true, path: rel });
      } catch (e) { return send(res, 500, { error: String(e) }); }
    });
  }

  if (req.method === "POST" && p === "/reindex") {
    if (reindex.running) return send(res, 200, { started: false, reindex });
    reindex = { running: true, ok: null, at: new Date().toISOString() };
    const ch = spawn("/bin/bash", [path.join(KB, "update.sh")], { cwd: KB });
    ch.on("close", (code) => { reindex = { running: false, ok: code === 0, at: new Date().toISOString() }; });
    ch.on("error", () => { reindex = { running: false, ok: false, at: new Date().toISOString() }; });
    return send(res, 200, { started: true, reindex: { running: true } });
  }

  if (req.method === "GET" && p === "/reindex-status") return send(res, 200, reindex);

  if (req.method === "GET" && p === "/notices") {
    const now = Date.now();
    const notices = loadNotices().map((n) => ({ ...n, active: noticeActive(n, now) }));
    return send(res, 200, { notices, now: new Date(now).toISOString() });
  }

  if (req.method === "POST" && p === "/notices") {
    return readBody(req, (d) => {
      const text = (d && d.text ? String(d.text) : "").trim();
      if (!text) return send(res, 400, { error: "text required" });
      let expires_at = null;
      if (d.expires_at) {
        const t = Date.parse(d.expires_at);
        if (isNaN(t)) return send(res, 400, { error: "bad deadline" });
        expires_at = new Date(t).toISOString();
      }
      const notice = {
        id: "n_" + Date.now() + "_" + Math.random().toString(16).slice(2, 6),
        text,
        created_at: new Date().toISOString(),
        expires_at,
        created_by: d && d.created_by ? String(d.created_by) : "owner",
      };
      const items = loadNotices();
      items.push(notice);
      try { writeNotices(items); } catch (e) { return send(res, 500, { error: String(e) }); }
      return send(res, 200, { ok: true, notice });
    });
  }

  if (req.method === "POST" && p === "/notices/delete") {
    return readBody(req, (d) => {
      const id = d && d.id ? String(d.id) : "";
      const items = loadNotices();
      const kept = items.filter((n) => n.id !== id);
      if (kept.length === items.length) return send(res, 404, { error: "not found" });
      try { writeNotices(kept); } catch (e) { return send(res, 500, { error: String(e) }); }
      return send(res, 200, { ok: true, removed: id });
    });
  }

  send(res, 404, { error: "not found" });
});

server.listen(PORT, "127.0.0.1", () => console.log("kb-admin listening on 127.0.0.1:" + PORT));
