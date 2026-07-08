/**
 * Buttons Bebe — WhatsApp Connect
 *
 * Serves a web page with a live WhatsApp QR code (auto-refreshing). Scan it with
 * your phone (WhatsApp > Linked devices) to link this server to your WhatsApp.
 * Once linked:
 *   - messages you send are forwarded to Hermes, and Hermes' reply comes back
 *     (so you're "talking to Hermes" on WhatsApp), and
 *   - the support pipeline can POST important escalations to /send so they land
 *     on your chosen WhatsApp destination.
 *
 * Uses the Baileys bridge (personal WhatsApp). Bound to localhost; exposed via
 * Caddy at https://<host>/connect-whatsapp/<WA_TOKEN>/ (owner pairing page) and
 * at https://<host>/console/waapi/* (the /wa/* JSON API, behind the console's
 * own auth gate) for the in-dashboard Notifications tab.
 */
const express = require("express");
const QRCode = require("qrcode");
const fs = require("fs");
const { execFile } = require("child_process");
const P = require("pino");
const {
  default: makeWASocket,
  useMultiFileAuthState,
  DisconnectReason,
  Browsers,
  fetchLatestBaileysVersion,
} = require("@whiskeysockets/baileys");

const PORT = process.env.WA_PORT || 8085;
const TOKEN = process.env.WA_TOKEN || "changeme";
const PASSWORD = process.env.WA_PASSWORD || "chaim123"; // gate for the pairing page
const AUTH_DIR = process.env.WA_AUTH_DIR || "./auth";
const NOTIFY_FILE = process.env.WA_NOTIFY_FILE || "./notify.json";
const HERMES_BIN = process.env.HERMES_BIN || "hermes";
const BASE = `/connect-whatsapp/${TOKEN}`;

let state = "starting"; // starting | qr | connected
let qrDataUrl = null;
let ownerJid = null;
let sock = null;
const botSentIds = new Set(); // ids of messages we sent, so we don't reply to ourselves

// ---------------- notification destination config ----------------
// Where escalation alerts are delivered. mode "linked" = the linked owner
// account (default, most secure). mode "number" = a specific phone number the
// owner typed in the console. This ONLY affects OUTBOUND alerts — it does not
// change who is allowed to talk to Hermes (still owner-self-chat only).
function readNotify() {
  try {
    const o = JSON.parse(fs.readFileSync(NOTIFY_FILE, "utf8"));
    return { mode: o.mode === "number" ? "number" : "linked", number: String(o.number || "") };
  } catch (e) {
    return { mode: "linked", number: "" };
  }
}
function writeNotify(o) {
  fs.writeFileSync(NOTIFY_FILE, JSON.stringify(o));
}
function numberToJid(num) {
  const d = String(num || "").replace(/[^0-9]/g, "");
  return d ? d + "@s.whatsapp.net" : null;
}
// Resolve the delivery JID for alerts, based on saved config.
function destJid() {
  const n = readNotify();
  if (n.mode === "number") {
    const j = numberToJid(n.number);
    if (j) return j;
  }
  return ownerJid;
}

async function startSock() {
  const { state: authState, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  let version;
  try {
    ({ version } = await fetchLatestBaileysVersion());
  } catch (e) {
    version = undefined; // fall back to bundled version
  }
  sock = makeWASocket({
    version,
    auth: authState,
    printQRInTerminal: false,
    browser: Browsers.ubuntu("Chrome"),
    logger: P({ level: "silent" }),
  });

  sock.ev.on("creds.update", saveCreds);

  sock.ev.on("connection.update", async (u) => {
    const { connection, lastDisconnect, qr } = u;
    if (qr) {
      state = "qr";
      try {
        qrDataUrl = await QRCode.toDataURL(qr, { margin: 1, width: 320 });
      } catch (e) {
        console.error("qr render error", e);
      }
    }
    if (connection === "open") {
      state = "connected";
      qrDataUrl = null;
      const raw = sock.user && sock.user.id ? sock.user.id.split(":")[0] : null;
      ownerJid = raw ? `${raw}@s.whatsapp.net` : null;
      console.log("WhatsApp connected as", ownerJid);
    }
    if (connection === "close") {
      const code =
        lastDisconnect && lastDisconnect.error && lastDisconnect.error.output
          ? lastDisconnect.error.output.statusCode
          : undefined;
      if (code === DisconnectReason.loggedOut) {
        state = "qr";
        ownerJid = null;
        qrDataUrl = null;
        // The saved credentials are now invalid. If we reconnect with them we
        // just get logged out again — an infinite loop that never shows a QR.
        // Wipe the auth folder so Baileys starts fresh and emits a new QR.
        try {
          fs.rmSync(AUTH_DIR, { recursive: true, force: true });
          fs.mkdirSync(AUTH_DIR, { recursive: true });
        } catch (e) {
          console.error("auth wipe error", e);
        }
        console.log("logged out — cleared stale creds, generating a fresh QR");
        setTimeout(() => startSock().catch((e) => console.error(e)), 1500);
      } else {
        console.log("connection closed, reconnecting...");
        setTimeout(() => startSock().catch((e) => console.error(e)), 2000);
      }
    }
  });

  sock.ev.on("messages.upsert", async (m) => {
    try {
      const msg = m.messages && m.messages[0];
      if (!msg || !msg.message) return;
      // SECURITY: only the owner can talk to Hermes, and only inside their own
      // WhatsApp "Note to Self" chat. Any message from anyone else is ignored,
      // so a stranger who messages this number can never reach the AI.
      if (!msg.key.fromMe) return; // ignore everyone except the owner
      if (!ownerJid || msg.key.remoteJid !== ownerJid) return; // owner's self-chat only
      if (botSentIds.has(msg.key.id)) return; // don't react to Hermes' own replies
      const text =
        msg.message.conversation ||
        (msg.message.extendedTextMessage && msg.message.extendedTextMessage.text);
      if (!text) return;
      forwardToHermes(text, msg.key.remoteJid);
    } catch (e) {
      console.error("incoming msg error", e);
    }
  });
}

function forwardToHermes(text, jid) {
  execFile(
    HERMES_BIN,
    ["-z", text],
    { timeout: 150000, maxBuffer: 4 * 1024 * 1024 },
    (err, stdout) => {
      let reply = (stdout || "").trim();
      if (!reply) reply = "Sorry — I couldn't process that right now.";
      if (sock) {
        sock
          .sendMessage(jid, { text: reply.slice(0, 4000) })
          .then((sent) => {
            if (sent && sent.key && sent.key.id) botSentIds.add(sent.key.id);
          })
          .catch((e) => console.error("reply send error", e));
      }
    }
  );
}

// Deliver an alert to the configured destination. Returns a promise.
function sendAlert(text) {
  const jid = destJid();
  if (state !== "connected" || !jid) {
    return Promise.reject(new Error("whatsapp not connected / no destination"));
  }
  return sock.sendMessage(jid, { text: String(text).slice(0, 4000) }).then((sent) => {
    if (sent && sent.key && sent.key.id) botSentIds.add(sent.key.id);
    return jid;
  });
}

// ---------------- HTTP ----------------
const app = express();
app.use(express.json());

// Password gate for the human-facing pages (pairing QR + status). The browser
// shows a login box; enter any username and the password (WA_PASSWORD).
function requireAuth(req, res, next) {
  const hdr = req.headers.authorization || "";
  const m = hdr.match(/^Basic (.+)$/);
  if (m) {
    const decoded = Buffer.from(m[1], "base64").toString();
    const pass = decoded.slice(decoded.indexOf(":") + 1);
    if (pass === PASSWORD) return next();
  }
  res.set("WWW-Authenticate", 'Basic realm="Buttons Bebe - Connect WhatsApp"');
  return res.status(401).send("Password required");
}

app.get(`${BASE}/status`, requireAuth, (req, res) =>
  res.json({ state, qr: qrDataUrl, owner: ownerJid })
);

// Push an important message to the linked WhatsApp (used by the escalation path).
// Delivers to the configured destination (linked owner account, or a typed number).
app.post(`${BASE}/send`, (req, res) => {
  const text = req.body && req.body.text;
  if (!text) return res.status(400).json({ error: "text required" });
  sendAlert(text)
    .then((jid) => res.json({ ok: true, to: jid }))
    .catch((e) => res.status(409).json({ error: String(e.message || e) }));
});

// ---------------- /wa/* : JSON API for the in-console Notifications tab ----------------
// These are reached only via Caddy at /console/waapi/* behind the console's own
// auth gate (the service itself is bound to localhost), so no WA password here.

app.get("/wa/status", (req, res) => {
  const n = readNotify();
  res.json({ state, qr: qrDataUrl, owner: ownerJid, notify: n });
});

app.get("/wa/notify", (req, res) => res.json(readNotify()));

app.put("/wa/notify", (req, res) => {
  const b = req.body || {};
  const mode = b.mode === "number" ? "number" : "linked";
  const number = String(b.number || "").trim();
  if (mode === "number" && !numberToJid(number)) {
    return res.status(400).json({ error: "enter a valid phone number (digits, with country code)" });
  }
  const o = { mode, number };
  try {
    writeNotify(o);
    return res.json({ ok: true, ...o });
  } catch (e) {
    return res.status(500).json({ error: String(e) });
  }
});

// Send a test alert to the current destination so the owner can confirm delivery.
app.post("/wa/test", (req, res) => {
  sendAlert("✅ Test alert from your Buttons Bebe support console. If you can read this, escalation notifications are working.")
    .then((jid) => res.json({ ok: true, to: jid }))
    .catch((e) => res.status(409).json({ error: String(e.message || e) }));
});

// Unlink the current WhatsApp so a different account can be linked. Triggers a
// fresh QR on the next status poll.
app.post("/wa/logout", async (req, res) => {
  try {
    if (sock) await sock.logout().catch(() => {});
    state = "qr";
    ownerJid = null;
    qrDataUrl = null;
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ error: String(e) });
  }
});

app.get(`${BASE}/`, requireAuth, (req, res) => res.type("html").send(PAGE));
app.get(`${BASE}`, (req, res) => res.redirect(`${BASE}/`));
app.get("/connect-whatsapp/*", (req, res) => res.status(404).send("Not found"));

app.listen(PORT, "127.0.0.1", () =>
  console.log(`whatsapp-connect listening on 127.0.0.1:${PORT} base=${BASE}`)
);

startSock().catch((e) => console.error("startSock error", e));

const PAGE = `<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Connect WhatsApp — Hermes</title>
<style>
 *{box-sizing:border-box} body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
 background:#0b141a;color:#e9edef;margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center}
 .card{background:#111b21;padding:28px 28px 22px;border-radius:18px;text-align:center;max-width:360px;width:92%;box-shadow:0 10px 40px rgba(0,0,0,.4)}
 h2{margin:0 0 6px} .brand{color:#25d366;font-weight:600;letter-spacing:.3px;font-size:13px;margin-bottom:14px}
 img{width:300px;height:300px;background:#fff;border-radius:10px;padding:8px}
 .muted{color:#8696a0;font-size:14px;line-height:1.5} .ok{color:#25d366;font-size:22px;margin:10px 0}
 .steps{text-align:left;font-size:13px;color:#aebac1;margin:14px auto 0;max-width:280px}
 .steps li{margin:4px 0} .bar{height:3px;background:#25d366;border-radius:3px;margin-top:14px;transition:width 1s linear}
</style></head><body>
<div class="card">
  <div class="brand">HERMES · BUTTONS BEBE</div>
  <h2>Connect WhatsApp</h2>
  <div id="body"><p class="muted">Loading…</p></div>
  <div class="bar" id="bar" style="width:100%"></div>
</div>
<script>
 let left=30;
 async function tick(){
   try{
     const r=await fetch('./status',{cache:'no-store'}); const d=await r.json();
     const b=document.getElementById('body');
     if(d.state==='connected'){
       b.innerHTML='<p class="ok">✅ Connected to Hermes</p><p class="muted">You can message Hermes here, and important alerts will arrive on your WhatsApp.</p>';
       document.getElementById('bar').style.display='none';
     }else if(d.state==='qr' && d.qr){
       b.innerHTML='<img src="'+d.qr+'" alt="WhatsApp QR code">'+
         '<ol class="steps"><li>Open <b>WhatsApp</b> on your phone</li>'+
         '<li>Tap <b>Settings → Linked devices</b></li>'+
         '<li>Tap <b>Link a device</b> and scan this code</li></ol>';
     }else{
       b.innerHTML='<p class="muted">Preparing your QR code…</p>';
     }
   }catch(e){}
 }
 function loop(){ left--; if(left<=0){left=30;} document.getElementById('bar').style.width=(left/30*100)+'%'; }
 tick(); setInterval(tick,3000); setInterval(loop,1000);
</script></body></html>`;
