/* ============================================================================
   Fable chat widget — self-contained, embeddable, no dependencies.
   Drop onto any page:  <script src="widget.js"></script>
   A floating purple bubble opens a 340px chat panel. It asks the visitor for
   a name + email once (kept in localStorage with a session id), then talks to
   the Fable help-desk server: POST /fable/api/intake/chat and long-polls
   /fable/api/chat/{session_id}/messages?after=<id> every 3s for replies.

   Server base URL: defaults to http://127.0.0.1:9600. Override with
   window.FABLE_WIDGET_BASE = "https://…"  OR  <script data-fable-base="…">.
   NOTE: when the host page is served from the same origin as the server
   (e.g. http://127.0.0.1:9600/widget/demo-store.html) requests are same-origin
   and need no CORS. A cross-origin production embed would require the server to
   send CORS headers.
   ============================================================================ */
(function () {
"use strict";
if (window.__fableWidgetLoaded) return;
window.__fableWidgetLoaded = true;

/* ---- config ---- */
var thisScript = document.currentScript;
var BASE = (window.FABLE_WIDGET_BASE
  || (thisScript && thisScript.getAttribute("data-fable-base"))
  || "http://127.0.0.1:9600").replace(/\/$/, "");

var LS_SESSION = "fable_session_id";
var LS_PROFILE = "fable_profile";
var POLL_MS = 3000;

/* ---- state ---- */
var open = false;
var profile = null;      // {name, email}
var sessionId = null;
var lastSeen = 0;
var pollTimer = null;
var seenIds = {};

function uuid(){
  return "s-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
}
function esc(s){ return (s==null?"":String(s)).replace(/[&<>"]/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[c];}); }

function loadProfile(){
  try { profile = JSON.parse(localStorage.getItem(LS_PROFILE) || "null"); } catch(e){ profile = null; }
  sessionId = localStorage.getItem(LS_SESSION);
  if(!sessionId){ sessionId = uuid(); localStorage.setItem(LS_SESSION, sessionId); }
}
function saveProfile(p){ profile = p; try { localStorage.setItem(LS_PROFILE, JSON.stringify(p)); } catch(e){} }

/* ---- styles (scoped under #fable-widget) ---- */
var CSS = ''
+'#fable-widget,#fable-widget *{box-sizing:border-box;margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Inter,Arial,sans-serif}'
+'#fable-widget{position:fixed;right:22px;bottom:22px;z-index:2147483000}'
+'#fable-bubble{width:60px;height:60px;border-radius:50%;background:linear-gradient(150deg,#6d5efc,#4d40cf);color:#fff;border:none;cursor:pointer;box-shadow:0 10px 26px -6px rgba(77,64,207,.55);display:flex;align-items:center;justify-content:center;transition:transform .16s cubic-bezier(.4,0,.2,1)}'
+'#fable-bubble:hover{transform:scale(1.06)}'
+'#fable-bubble svg{width:26px;height:26px}'
+'#fable-panel{position:absolute;right:0;bottom:74px;width:340px;max-width:calc(100vw - 32px);height:520px;max-height:calc(100vh - 120px);background:#fff;border-radius:16px;box-shadow:0 20px 50px -12px rgba(20,22,28,.35);display:none;flex-direction:column;overflow:hidden;border:1px solid #e5e7ef}'
+'#fable-widget.open #fable-panel{display:flex;animation:fw-in .18s cubic-bezier(.4,0,.2,1)}'
+'@keyframes fw-in{from{transform:translateY(10px);opacity:0}to{transform:translateY(0);opacity:1}}'
+'.fw-head{background:linear-gradient(150deg,#6d5efc,#5b4de0);color:#fff;padding:16px 18px;display:flex;align-items:center;gap:10px}'
+'.fw-head .av{width:34px;height:34px;border-radius:50%;background:rgba(255,255,255,.2);display:flex;align-items:center;justify-content:center;font-weight:700;flex:0 0 34px}'
+'.fw-head b{font-size:15px;font-weight:700;display:block;line-height:1.2}'
+'.fw-head small{font-size:12px;opacity:.85}'
+'.fw-head .x{margin-left:auto;background:none;border:none;color:#fff;cursor:pointer;font-size:22px;line-height:1;opacity:.85;padding:2px 6px}'
+'.fw-head .x:hover{opacity:1}'
+'.fw-body{flex:1;overflow-y:auto;padding:16px;background:#f6f7fb;display:flex;flex-direction:column;gap:10px}'
+'.fw-msg{max-width:82%;padding:10px 13px;border-radius:12px;font-size:14px;line-height:1.5;white-space:pre-wrap;word-break:break-word}'
+'.fw-msg.them{align-self:flex-start;background:#fff;border:1px solid #e5e7ef;color:#14161c;border-bottom-left-radius:4px}'
+'.fw-msg.me{align-self:flex-end;background:#5b4de0;color:#fff;border-bottom-right-radius:4px}'
+'.fw-note{align-self:center;font-size:12px;color:#6b7382;text-align:center;padding:2px 8px}'
+'.fw-foot{border-top:1px solid #e5e7ef;padding:10px;display:flex;gap:8px;background:#fff}'
+'.fw-foot input{flex:1;border:1px solid #e5e7ef;border-radius:999px;padding:10px 14px;font-size:14px;color:#14161c;outline:none}'
+'.fw-foot input:focus{border-color:#6d5efc;box-shadow:0 0 0 3px rgba(109,94,252,.25)}'
+'.fw-foot button{background:#5b4de0;color:#fff;border:none;border-radius:50%;width:40px;height:40px;cursor:pointer;flex:0 0 40px;display:flex;align-items:center;justify-content:center}'
+'.fw-foot button:hover{background:#4d40cf}'
+'.fw-foot button:disabled{opacity:.5;cursor:default}'
+'.fw-foot button svg{width:18px;height:18px}'
+'.fw-intro{padding:22px 18px;background:#fff;flex:1;display:flex;flex-direction:column;justify-content:center}'
+'.fw-intro h4{font-size:16px;color:#14161c;margin-bottom:4px}'
+'.fw-intro p{font-size:13px;color:#6b7382;margin-bottom:16px;line-height:1.5}'
+'.fw-intro label{font-size:12px;font-weight:600;color:#4a5262;display:block;margin:10px 0 5px}'
+'.fw-intro input{width:100%;border:1px solid #e5e7ef;border-radius:8px;padding:11px 13px;font-size:14px;color:#14161c;outline:none}'
+'.fw-intro input:focus{border-color:#6d5efc;box-shadow:0 0 0 3px rgba(109,94,252,.25)}'
+'.fw-intro .err{color:#b42318;font-size:12px;margin-top:8px;min-height:16px}'
+'.fw-intro button{margin-top:16px;width:100%;background:#5b4de0;color:#fff;border:none;border-radius:8px;padding:12px;font-size:14px;font-weight:600;cursor:pointer}'
+'.fw-intro button:hover{background:#4d40cf}';

var ICON_CHAT = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7.9 20A9 9 0 1 0 4 16.1L2 22z"/></svg>';
var ICON_SEND = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.536 21.686a.5.5 0 0 0 .937-.024l6.5-19a.496.496 0 0 0-.635-.635l-19 6.5a.5.5 0 0 0-.024.937l7.93 3.18a2 2 0 0 1 1.112 1.11z"/><path d="m21.854 2.147-10.94 10.939"/></svg>';

/* ---- DOM ---- */
var root, bodyEl, footEl;
function buildUI(){
  var style = document.createElement("style");
  style.textContent = CSS;
  document.head.appendChild(style);

  root = document.createElement("div");
  root.id = "fable-widget";
  root.innerHTML = ''
    + '<div id="fable-panel">'
    +   '<div class="fw-head"><div class="av">B</div><div><b>Buttons Bebe</b><small>We usually reply in a few minutes</small></div><button class="x" id="fw-close" aria-label="Close chat">×</button></div>'
    +   '<div class="fw-body" id="fw-body"></div>'
    +   '<div id="fw-foot-slot"></div>'
    + '</div>'
    + '<button id="fable-bubble" aria-label="Chat with us">' + ICON_CHAT + '</button>';
  document.body.appendChild(root);

  document.getElementById("fable-bubble").onclick = toggle;
  document.getElementById("fw-close").onclick = toggle;
  bodyEl = document.getElementById("fw-body");
}

function toggle(){
  open = !open;
  root.classList.toggle("open", open);
  if(open){
    if(!profile) showIntro(); else showChat();
  } else {
    stopPoll();
  }
}

/* ---- intro (name + email once) ---- */
function showIntro(){
  document.getElementById("fw-foot-slot").innerHTML = "";
  bodyEl.style.display = "none";
  var slot = document.getElementById("fw-foot-slot");
  slot.innerHTML = ''
    + '<div class="fw-intro">'
    +   '<h4>Hi there 👋</h4>'
    +   '<p>Tell us who you are and we\'ll help with your order, sizing, shipping — anything.</p>'
    +   '<label>Your name</label><input id="fw-name" type="text" placeholder="Emma Wilson">'
    +   '<label>Email</label><input id="fw-email" type="email" placeholder="you@example.com">'
    +   '<div class="err" id="fw-err"></div>'
    +   '<button id="fw-start">Start chatting</button>'
    + '</div>';
  document.getElementById("fw-start").onclick = function(){
    var name = document.getElementById("fw-name").value.trim();
    var email = document.getElementById("fw-email").value.trim();
    if(!name){ document.getElementById("fw-err").textContent = "Please add your name."; return; }
    if(!/^\S+@\S+\.\S+$/.test(email)){ document.getElementById("fw-err").textContent = "Please add a valid email."; return; }
    saveProfile({name:name, email:email});
    bodyEl.style.display = "flex";
    showChat();
  };
  setTimeout(function(){ var n=document.getElementById("fw-name"); if(n) n.focus(); }, 30);
}

/* ---- chat ---- */
function showChat(){
  bodyEl.style.display = "flex";
  document.getElementById("fw-foot-slot").innerHTML = ''
    + '<div class="fw-foot"><input id="fw-input" type="text" placeholder="Type a message…" autocomplete="off"><button id="fw-send" aria-label="Send">' + ICON_SEND + '</button></div>';
  var input = document.getElementById("fw-input");
  var sendBtn = document.getElementById("fw-send");
  sendBtn.onclick = sendMessage;
  input.onkeydown = function(e){ if(e.key==="Enter"){ e.preventDefault(); sendMessage(); } };
  setTimeout(function(){ input.focus(); }, 30);

  if(!bodyEl.dataset.greeted && !Object.keys(seenIds).length){
    bodyEl.dataset.greeted = "1";
    addMsg("them", "Hi " + (profile.name||"there").split(" ")[0] + "! How can we help today?");
  }
  startPoll();
  fetchMessages(); // pull any history right away
}

function addMsg(who, text){
  var m = document.createElement("div");
  m.className = "fw-msg " + who;
  m.textContent = text;
  bodyEl.appendChild(m);
  bodyEl.scrollTop = bodyEl.scrollHeight;
}

function sendMessage(){
  var input = document.getElementById("fw-input");
  var sendBtn = document.getElementById("fw-send");
  if(!input) return;
  var text = input.value.trim();
  if(!text) return;
  input.value = "";
  addMsg("me", text);            // optimistic — server won't echo it (after=lastSeen)
  sendBtn.disabled = true;

  fetch(BASE + "/fable/api/intake/chat", {
    method: "POST",
    headers: {"content-type":"application/json"},
    body: JSON.stringify({session_id:sessionId, name:profile.name, email:profile.email, body_text:text})
  }).then(function(r){ return r.json(); }).then(function(d){
    sendBtn.disabled = false;
    if(d && d.message_id){ lastSeen = Math.max(lastSeen, d.message_id); seenIds[d.message_id] = 1; }
    setTimeout(fetchMessages, 700); // check for a quick reply sooner than the poll
  }).catch(function(){
    sendBtn.disabled = false;
    addMsg("them", "Hmm, we couldn't reach the store just now. Please try again in a moment.");
  });
}

function fetchMessages(){
  fetch(BASE + "/fable/api/chat/" + encodeURIComponent(sessionId) + "/messages?after=" + lastSeen, {cache:"no-store"})
    .then(function(r){ return r.ok ? r.json() : {messages:[]}; })
    .then(function(d){
      (d.messages||[]).forEach(function(m){
        if(seenIds[m.id]) return;
        seenIds[m.id] = 1;
        lastSeen = Math.max(lastSeen, m.id);
        addMsg(m.from_agent ? "them" : "me", m.body_text || "");
      });
    }).catch(function(){});
}

function startPoll(){ stopPoll(); pollTimer = setInterval(fetchMessages, POLL_MS); }
function stopPoll(){ if(pollTimer){ clearInterval(pollTimer); pollTimer = null; } }

/* ---- init ---- */
function init(){ loadProfile(); buildUI(); }
if(document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
else init();

})();
