/* ============================================================================
   Fable Support Console — app.js
   Vanilla JS, no framework, no build step. Talks to the Fable server on the
   same origin (relative /fable/api/... URLs). Views: Inbox, Ticket, Customers,
   Stats, Settings. All user-supplied text is escaped (never innerHTML'd raw).
   ============================================================================ */
(function () {
"use strict";

var API = "/fable/api";

/* ---- tiny helpers -------------------------------------------------------- */
function esc(s){ return (s==null?"":String(s)).replace(/[&<>"]/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[c];}); }
function $(sel,root){ return (root||document).querySelector(sel); }
function $all(sel,root){ return Array.prototype.slice.call((root||document).querySelectorAll(sel)); }
function el(id){ return document.getElementById(id); }

function initials(name){
  var n=(name||"").trim(); if(!n) return "?";
  var p=n.split(/\s+/); return (p[0][0]+(p[1]?p[1][0]:"")).toUpperCase();
}
function firstName(name){ var n=(name||"").trim(); return n?n.split(/\s+/)[0]:"there"; }

function ago(iso){
  if(!iso) return "";
  var t=new Date(iso).getTime(); if(isNaN(t)) return "";
  var s=(Date.now()-t)/1000;
  if(s<45) return "just now";
  if(s<3600) return Math.max(1,Math.floor(s/60))+"m ago";
  if(s<86400) return Math.floor(s/3600)+"h ago";
  if(s<604800) return Math.floor(s/86400)+"d ago";
  return new Date(iso).toLocaleDateString();
}
function money(v,cur){ if(v==null||v==="") return ""; var n=Number(v); var s=isNaN(n)?v:n.toFixed(2); return (cur==="USD"?"$":(cur?cur+" ":"$"))+s; }

var CHAN = {
  email:    {label:"Email"},
  chat:     {label:"Chat"},
  whatsapp: {label:"WhatsApp"}
};
/* Inline SVG line icons per channel (replaces the old emoji channel glyphs). They
   use currentColor so they inherit the channel-ink color of whatever chip holds them. */
var CHAN_SVG = {
  email:'<svg class="chi" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/></svg>',
  chat:'<svg class="chi" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7.9 20A9 9 0 1 0 4 16.1L2 22z"/></svg>',
  whatsapp:'<svg class="chi" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13.83 16.57a1 1 0 0 0 1.21-.3l.36-.47A2 2 0 0 1 17 15h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2A18 18 0 0 1 2 4a2 2 0 0 1 2-2h3a2 2 0 0 1 2 2v3a2 2 0 0 1-.8 1.6l-.47.35a1 1 0 0 0-.29 1.23 14 14 0 0 0 6.39 6.39"/></svg>'
};
var CHAN_INK = { email:"var(--chan-email-ink)", chat:"var(--chan-chat-ink)", whatsapp:"var(--chan-wa-ink)" };
function chanSvg(ch){ return CHAN_SVG[ch] || '<svg class="chi" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/></svg>'; }
function chanChip(ch){ var c=CHAN[ch]||{label:ch||"Message"}; return '<span class="cc '+esc(ch)+'">'+chanSvg(ch)+esc(c.label)+"</span>"; }
/* Channel icon tinted with its own ink color — for neutral surfaces (stats rows). */
function chanIconInk(ch){ var col=CHAN_INK[ch]||"var(--ink3)"; return '<span class="chi-ink" style="color:'+col+'">'+chanSvg(ch)+'</span>'; }

/* Plain-language order status (contract order_context.orders[]) */
function orderStatus(o){
  var fin=(o.financial_status||"").toLowerCase();
  var ful=(o.fulfillment_status||"").toLowerCase();
  if(fin==="refunded") return {label:"Refunded", cls:"refunded"};
  if(fin==="partially_refunded") return {label:"Partly refunded", cls:"refunded"};
  if(ful==="fulfilled") return o.tracking_url||o.tracking_number
      ? {label:"On its way", cls:"ontheway"} : {label:"Delivered", cls:"delivered"};
  if(ful==="partial") return {label:"Partly shipped", cls:"partly"};
  return {label:"Not shipped yet", cls:"notshipped"};
}

/* ---- SVG icons ----------------------------------------------------------- */
var IC = {
  inbox:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/></svg>',
  customers:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>',
  stats:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v16a2 2 0 0 0 2 2h16"/><path d="M18 17V9"/><path d="M13 17V5"/><path d="M8 17v-3"/></svg>',
  settings:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/></svg>',
  refresh:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/></svg>',
  back:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m15 18-6-6 6-6"/></svg>',
  chev:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m9 18 6-6-6-6"/></svg>',
  alert:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/><path d="M12 9v4"/><path d="M12 17h.01"/></svg>',
  check:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>',
  send:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.536 21.686a.5.5 0 0 0 .937-.024l6.5-19a.496.496 0 0 0-.635-.635l-19 6.5a.5.5 0 0 0-.024.937l7.93 3.18a2 2 0 0 1 1.112 1.11z"/><path d="m21.854 2.147-10.94 10.939"/></svg>',
  note:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15.5 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V8.5z"/><path d="M15 3v6h6"/></svg>',
  spark:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .962 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.962 0z"/></svg>',
  search:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>'
};

/* ---- toasts -------------------------------------------------------------- */
function toast(msg, type){
  var wrap=el("toasts"); if(!wrap) return;
  var t=document.createElement("div");
  t.className="toast "+(type==="err"?"err":"ok");
  t.textContent=msg;
  wrap.appendChild(t);
  setTimeout(function(){ t.style.transition="opacity .25s"; t.style.opacity="0"; setTimeout(function(){ if(t.parentNode) t.parentNode.removeChild(t); },260); }, 3600);
}

/* ---- fetch wrapper ------------------------------------------------------- */
function jfetch(url, opts){
  return fetch(url, Object.assign({cache:"no-store"}, opts||{})).then(function(r){
    return r.text().then(function(txt){
      var data=null; try{ data=txt?JSON.parse(txt):null; }catch(e){ data=null; }
      return {ok:r.ok, status:r.status, data:data};
    });
  }).catch(function(){ return {ok:false, status:0, data:null}; });
}

/* ---- app state ----------------------------------------------------------- */
var S = {
  view:"inbox",             // inbox | ticket | customers | stats | settings
  counts:{open:0,closed:0,snoozed:0,sensitive_open:0},
  health:null,
  // inbox
  filters:{status:"open", channel:"all", q:""},
  tickets:[],
  // ticket
  ticket:null,
  draftText:"",
  draftOriginal:"",        // the AI's original draft text (for the "Edited" flag)
  draftEdited:false,       // true once the human's text diverges from the AI original
  confirmSend:false,
  rewriteOpen:false,
  snoozeOpen:false,
  tagInputOpen:false,      // inline "add tag" input is showing
  showAllMsgs:false,       // older messages expanded
  sendPending:false,       // inside the 5s undo window
  staleWarning:false,      // customer wrote again while this ticket was open
  staleTicket:null,
  busy:false,
  // customers
  customers:[],
  custQuery:"",
  custDetail:null
};
var poll=null, tpoll=null, searchTimer=null;
var pendingSend=null;      // {ticketId, text, edited, timer, bar} for the undo-send window

function stopPoll(){ if(poll){ clearInterval(poll); poll=null; } }
function startPoll(){ stopPoll(); poll=setInterval(function(){ if(S.view==="inbox") refreshInbox(true); }, 5000); }
function stopTicketPoll(){ if(tpoll){ clearInterval(tpoll); tpoll=null; } }

/* ============================================================================
   SHELL
   ========================================================================= */
function shell(title, body){
  var nav=[["inbox","Inbox"],["customers","Customers"],["stats","Stats"],["settings","Settings"]];
  var live = S.health && S.health.ok;
  return ''
    +'<aside class="side">'
    +  '<div class="logo"><div class="mark">F</div><div class="txt"><b>Fable</b><small>Buttons Bebe</small></div></div>'
    +  '<nav class="nav">'+nav.map(function(n){
         var badge = (n[0]==="inbox" && S.counts.open) ? '<span class="cnt">'+S.counts.open+'</span>' : '';
         return '<a data-nav="'+n[0]+'" class="'+(S.view===n[0]||(S.view==="ticket"&&n[0]==="inbox")?"on":"")+'" role="button" tabindex="0">'+IC[n[0]]+'<span class="lbl">'+n[1]+'</span>'+badge+'</a>';
       }).join("")+'</nav>'
    +  '<div class="store"><div class="av">B</div><div><div class="nm">Buttons Bebe</div><div class="pl">'+esc(S.health&&S.health.brain?("Brain: "+S.health.brain):"AI support")+'</div></div></div>'
    +'</aside>'
    +'<div class="main">'
    +  '<div class="top"><div class="lhs">'+title+'</div>'
    +    '<div class="topact"><div class="live '+(live?"":"off")+'"><span class="dot"></span> '+(live?"Fable is running":"Server offline")+'</div>'
    +    '<button class="iconbtn" id="refresh" title="Refresh" aria-label="Refresh">'+IC.refresh+'</button></div></div>'
    +  '<div class="wrap" id="wrap">'+body+'</div>'
    +'</div>';
}
function titleBlock(h1, sub){ return '<div><h1>'+esc(h1)+'</h1>'+(sub?'<div class="sub">'+esc(sub)+'</div>':'')+'</div>'; }

function render(){
  var app=el("app");
  if(S.view==="inbox") app.innerHTML=shell(titleBlock("Inbox","Every message in one place — Fable drafts, you decide"), inboxBody());
  else if(S.view==="ticket") app.innerHTML=shell('<button class="backbtn" id="back">'+IC.back+' Back to inbox</button>', ticketBody());
  else if(S.view==="customers") app.innerHTML=shell(titleBlock("Customers","Look up anyone who has written in"), customersBody());
  else if(S.view==="stats") app.innerHTML=shell(titleBlock("Stats","How support is going"), statsBody());
  else if(S.view==="settings") app.innerHTML=shell(titleBlock("Settings","What's connected and how Fable stays safe"), settingsBody());
  bindShell();
  bindView();
}

function bindShell(){
  $all("[data-nav]").forEach(function(a){
    a.onclick=function(){ go(a.getAttribute("data-nav")); };
    a.onkeydown=function(e){ if(e.key==="Enter"||e.key===" "){ e.preventDefault(); a.click(); } };
  });
  var rf=el("refresh");
  if(rf) rf.onclick=function(){ rf.classList.add("spinning"); rf.disabled=true; reloadView().then(function(){ rf.classList.remove("spinning"); rf.disabled=false; }); };
  var b=el("back"); if(b) b.onclick=function(){ go("inbox"); };
}

function go(view){
  flushPendingSend();       // leaving the ticket? finish any send already in its undo window
  S.view=view; S.custDetail=null;
  stopPoll(); stopTicketPoll();
  render();
  if(view==="inbox"){ refreshInbox(false); startPoll(); }
  else if(view==="customers"){ loadCustomers(""); }
  else if(view==="stats"){ loadStats(); }
  else if(view==="settings"){ loadHealth().then(render); }
}

function reloadView(){
  if(S.view==="inbox") return refreshInbox(false);
  if(S.view==="ticket") return openTicket(S.ticket.id, {});
  if(S.view==="customers") return loadCustomers(S.custQuery);
  if(S.view==="stats") return loadStats();
  if(S.view==="settings") return loadHealth().then(render);
  return Promise.resolve();
}

/* ============================================================================
   INBOX
   ========================================================================= */
function inboxBody(){
  var segs=[["open","Waiting for you"],["snoozed","Snoozed"],["closed","Done"],["all","All"]];
  var chans=[["all","All channels"],["email","Email"],["chat","Chat"],["whatsapp","WhatsApp"]];
  return ''
    +'<div class="filterbar">'
    +  '<div class="segs">'+segs.map(function(s){ return '<button class="seg '+(S.filters.status===s[0]?"on":"")+'" data-status="'+s[0]+'">'+esc(s[1])+'</button>'; }).join("")+'</div>'
    +  '<div class="chipset">'+chans.map(function(c){ var ico=(c[0]==="all"?"":chanSvg(c[0])); return '<button class="chip '+(S.filters.channel===c[0]?"on":"")+'" data-chan="'+c[0]+'">'+ico+esc(c[1])+'</button>'; }).join("")+'</div>'
    +  '<div class="search">'+IC.search+'<input id="q" type="search" placeholder="Search name, email, message…" value="'+esc(S.filters.q)+'"></div>'
    +'</div>'
    +'<div id="tklist">'+ticketListHtml()+'</div>';
}

function ticketListHtml(){
  if(!S.tickets) return '<div class="loading"><span class="spin"></span> Loading…</div>';
  if(S.tickets.length===0){
    var msg = S.filters.status==="open"
      ? {big:"☕", h:"Nothing waiting for you", p:"Enjoy the quiet — new messages will land here."}
      : S.filters.q ? {big:"🔍", h:"No matches", p:"Try a different name, email, or word."}
      : {big:"📭", h:"Nothing here", p:"No tickets in this view yet."};
    return '<div class="empty"><div class="big">'+msg.big+'</div><h3>'+esc(msg.h)+'</h3><div class="hint">'+esc(msg.p)+'</div></div>';
  }
  return '<div class="tklist">'+S.tickets.map(ticketCard).join("")+'</div>';
}

function ticketCard(t){
  var name=(t.customer&&t.customer.name)||"Customer";
  var right=[];
  right.push(chanChip(t.channel));
  if(t.status==="closed") right.push('<span class="badge done">Done</span>');
  else if(t.status==="snoozed") right.push('<span class="badge snoozed">Snoozed</span>');
  if(t.sensitive) right.push('<span class="badge sens">Needs a careful look</span>');
  return ''
    +'<div class="tkcard" data-tk="'+t.id+'" role="button" tabindex="0">'
    +  '<div class="avt">'+esc(initials(name))+'</div>'
    +  '<div class="body">'
    +    '<div class="l1">'+(t.has_draft?'<span class="draftdot" title="Fable has a suggested reply"></span>':'')+'<b>'+esc(name)+'</b></div>'
    +    '<div class="subj">'+esc(t.subject||"(no subject)")+'</div>'
    +    (t.preview?'<div class="prev">'+esc(t.preview)+'</div>':'')
    +  '</div>'
    +  '<div class="meta">'+right.join("")+'<span class="tm">'+esc(ago(t.last_message_at||t.created_at))+'</span></div>'
    +'</div>';
}

function bindInbox(){
  $all("[data-status]").forEach(function(b){ b.onclick=function(){ S.filters.status=b.getAttribute("data-status"); render(); refreshInbox(false); }; });
  $all("[data-chan]").forEach(function(b){ b.onclick=function(){ S.filters.channel=b.getAttribute("data-chan"); render(); refreshInbox(false); }; });
  var q=el("q");
  if(q){ q.oninput=function(){ S.filters.q=q.value; if(searchTimer) clearTimeout(searchTimer); searchTimer=setTimeout(function(){ refreshInbox(false); }, 300); }; }
  bindTicketCards();
}
function bindTicketCards(){
  $all("[data-tk]").forEach(function(c){
    c.onclick=function(){ openTicket(parseInt(c.getAttribute("data-tk"),10)); };
    c.onkeydown=function(e){ if(e.key==="Enter"||e.key===" "){ e.preventDefault(); c.click(); } };
  });
}

function inboxQuery(){
  var f=S.filters, p=[];
  p.push("status="+encodeURIComponent(f.status));
  if(f.channel!=="all") p.push("channel="+encodeURIComponent(f.channel));
  if(f.q) p.push("q="+encodeURIComponent(f.q));
  p.push("limit=50");
  return API+"/tickets?"+p.join("&");
}
function refreshInbox(quiet){
  var prevIds = (quiet && S.tickets) ? S.tickets.map(function(t){ return t.id; }) : null;
  return jfetch(inboxQuery()).then(function(r){
    if(!r.ok||!r.data){ if(!quiet) toast("Couldn't load the inbox.","err"); return; }
    S.tickets=r.data.tickets||[];
    S.counts=r.data.counts||S.counts;
    if(S.view==="inbox"){
      var list=el("tklist"); if(list){ list.innerHTML=ticketListHtml(); bindTicketCards(); }
      // keep the sidebar Inbox badge current
      var inboxLink=$('[data-nav="inbox"] .cnt');
      if(inboxLink) inboxLink.textContent=S.counts.open;
      // A background poll that surfaced a brand-new ticket → gently flag it.
      if(prevIds){
        var seen={}; prevIds.forEach(function(id){ seen[id]=1; });
        var fresh=S.tickets.filter(function(t){ return !seen[t.id]; });
        if(fresh.length){
          fresh.forEach(function(t){
            var row=$('[data-tk="'+t.id+'"]');
            if(row){ row.classList.add("justin"); setTimeout((function(el){ return function(){ if(el) el.classList.remove("justin"); }; })(row), 4000); }
          });
          pulseInboxBadge();
        }
      }
    }
  });
}
function pulseInboxBadge(){
  var b=$('[data-nav="inbox"] .cnt');
  if(!b) return;
  b.classList.remove("pulse");
  void b.offsetWidth;            // restart the animation
  b.classList.add("pulse");
  setTimeout(function(){ if(b) b.classList.remove("pulse"); }, 900);
}

/* ============================================================================
   TICKET
   ========================================================================= */
function openTicket(id, opts){
  opts = (opts && typeof opts==="object") ? opts : {};
  flushPendingSend();          // if a send was mid-undo on the last ticket, finish it
  stopPoll(); stopTicketPoll();
  return jfetch(API+"/tickets/"+id).then(function(r){
    if(!r.ok||!r.data||!r.data.ticket){ toast("Couldn't open that ticket.","err"); return; }
    S.ticket=r.data.ticket;
    S.view="ticket";
    var aiOriginal=(S.ticket.draft&&S.ticket.draft.body_text)||"";
    S.draftOriginal=aiOriginal;
    if(typeof opts.preserveText==="string"){
      // Refresh that keeps the human's edited text (see the stale-ticket banner).
      S.draftText=opts.preserveText;
      S.draftEdited=(S.draftText!==aiOriginal);
    } else {
      S.draftText=aiOriginal;
      S.draftEdited=false;
    }
    S.confirmSend=false; S.rewriteOpen=false; S.snoozeOpen=false;
    S.tagInputOpen=false; S.showAllMsgs=false; S.sendPending=false;
    S.staleWarning=false; S.staleTicket=null; S.busy=false;
    render();
    startTicketPoll(id);
  });
}

/* Poll the OPEN ticket every 5s. If a new customer message lands while the human
   is reading/editing, we DON'T touch their draft — we show a quiet banner. */
function startTicketPoll(id){
  stopTicketPoll();
  tpoll=setInterval(function(){
    if(S.view!=="ticket"||!S.ticket||S.ticket.id!==id){ stopTicketPoll(); return; }
    if(S.staleWarning) return;                 // already flagged; stop nagging
    jfetch(API+"/tickets/"+id).then(function(r){
      if(!r.ok||!r.data||!r.data.ticket) return;
      if(S.view!=="ticket"||!S.ticket||S.ticket.id!==id) return;
      if(hasNewCustomerMessage(S.ticket, r.data.ticket)){
        S.staleWarning=true;
        S.staleTicket=r.data.ticket;
        showStaleBanner();
      }
    });
  }, 5000);
}
function lastCustMsgId(t){
  var id=0;
  (t.messages||[]).forEach(function(m){ if(m.public && !m.from_agent && m.id>id) id=m.id; });
  return id;
}
function hasNewCustomerMessage(oldT, freshT){
  return lastCustMsgId(freshT) > lastCustMsgId(oldT);
}
/* Inject the banner via the DOM (not a full render) so the textarea — and the
   cursor/selection inside it — are never disturbed. */
function showStaleBanner(){
  if(el("stalebanner")) return;
  var thread=$(".thread"); if(!thread) return;
  var b=document.createElement("div");
  b.id="stalebanner"; b.className="stalebanner"; b.setAttribute("role","status");
  var ico=document.createElement("span"); ico.className="sbico"; ico.innerHTML=IC.alert;
  var span=document.createElement("span"); span.className="sbmsg";
  span.textContent="This customer just wrote again — refresh before sending.";
  var btn=document.createElement("button"); btn.className="btn"; btn.id="stale-refresh";
  btn.textContent="Refresh"; btn.onclick=refreshOpenTicketPreservingDraft;
  b.appendChild(ico); b.appendChild(span); b.appendChild(btn);
  var head=$(".tkhead", thread);
  if(head && head.nextSibling) thread.insertBefore(b, head.nextSibling);
  else thread.insertBefore(b, thread.firstChild);
}
function refreshOpenTicketPreservingDraft(){
  captureDraft();
  var keep = S.draftEdited ? S.draftText : null;   // keep the human's edits, drop untouched drafts
  S.staleWarning=false; S.staleTicket=null;
  openTicket(S.ticket.id, keep===null ? {} : {preserveText:keep});
}

function ticketBody(){
  var t=S.ticket; if(!t) return '<div class="loading"><span class="spin"></span> Loading…</div>';
  return '<div class="tkview"><div class="thread">'+ticketHead(t)+convoHtml(t)+draftCard(t)+'</div>'+asideHtml(t)+'</div>';
}

function ticketHead(t){
  var name=(t.customer&&t.customer.name)||"Customer";
  var closed=t.status==="closed";
  var hmeta=[];
  hmeta.push(chanChip(t.channel));
  if(t.sensitive) hmeta.push('<span class="badge sens">Needs a careful look</span>');
  if(closed) hmeta.push('<span class="badge done">Done</span>');
  else if(t.status==="snoozed") hmeta.push('<span class="badge snoozed">Snoozed</span>');
  var tags=(t.tags||[]).map(function(x){
    return '<span class="tag">'+esc(x)
      +'<button class="tagx" data-rmtag="'+esc(x)+'" title="Remove label" aria-label="Remove label '+esc(x)+'">&times;</button></span>';
  }).join("");
  var tagInput = S.tagInputOpen
    ? '<input class="taginput" id="tagin" type="text" placeholder="Label, then Enter" aria-label="Add a label">'
    : '<button class="btn ghost" id="addtag">+ Tag</button>';
  return ''
    +'<div class="tkhead">'
    +  '<h2>'+esc(t.subject||"(no subject)")+'</h2>'
    +  '<div class="hmeta">'+hmeta.join("")+'<span class="hint">· '+esc(name)+' · opened '+esc(ago(t.created_at))+'</span></div>'
    +  '<div class="hactions">'
    +    (closed
        ? '<button class="btn" id="reopen">Reopen ticket</button>'
        : '<button class="btn" id="close">Mark as done</button>'
          +'<div class="snoozemenu"><button class="btn" id="snoozebtn">Snooze</button>'+(S.snoozeOpen?snoozePop():'')+'</div>')
    +    '<div class="tags">'+tags+tagInput+'</div>'
    +  '</div>'
    +'</div>';
}
function snoozePop(){
  return '<div class="snoozepop">'
    +'<button data-snooze="1">Until tomorrow</button>'
    +'<button data-snooze="3">For 3 days</button>'
    +'</div>';
}

function msgBub(m, t){
  if(!m.public){ // internal note
    return '<div class="bub note"><div class="bmeta">'+IC.note+' Private note · '+esc(m.sender_name||"Team")+'<span class="bt">'+esc(ago(m.created_at))+'</span></div>'+esc(m.body_text)+'</div>';
  }
  if(m.from_agent){
    return '<div class="bub agent"><div class="bmeta">'+esc(m.sender_name||"Buttons Bebe")+'<span class="bt">'+esc(ago(m.created_at))+'</span></div>'+esc(m.body_text)+'</div>';
  }
  return '<div class="bub cust"><div class="bmeta">'+esc(m.sender_name||(t.customer&&t.customer.name)||"Customer")+'<span class="bt">'+esc(ago(m.created_at))+'</span></div>'+esc(m.body_text)+'</div>';
}
function convoHtml(t){
  var msgs=(t.messages||[]);
  if(!msgs.length) return '';
  // Long threads: show the newest 3 with a "Show N earlier messages" expander.
  var hidden=0, shown=msgs;
  if(msgs.length>4 && !S.showAllMsgs){
    hidden=msgs.length-3;
    shown=msgs.slice(msgs.length-3);
  }
  var more = hidden>0
    ? '<button class="showmore" id="showmore">'+IC.chev+' Show '+hidden+' earlier message'+(hidden===1?'':'s')+'</button>'
    : '';
  return '<div class="convo">'+more+shown.map(function(m){ return msgBub(m, t); }).join("")+'</div>';
}

function draftCard(t){
  var d=t.draft;
  var closed=t.status==="closed";
  if(!d || d.status!=="proposed"){
    // no live draft to act on
    var reason = d && d.status==="sent" ? "You already sent a reply on this ticket."
               : d && d.status==="noted" ? "The suggestion was saved as a private note."
               : "Fable hasn't written a suggestion for this ticket yet.";
    return '<div class="nodraft"><b>No suggested reply right now.</b><div class="hint" style="margin-top:6px">'+esc(reason)+'</div></div>';
  }
  var sensitive = t.sensitive || d.risk==="sensitive";
  var name=firstName(t.customer&&t.customer.name);
  var disabled = closed || S.sendPending;
  var body=''
    +'<div class="draftcard">'
    +  orderChipHtml(t)
    +  '<h3>'+IC.spark+' Suggested reply <span class="editedtag" id="edited-tag"'+(S.draftEdited?'':' hidden')+'>Edited</span></h3>'
    +  '<div class="kb">Fable wrote this draft for you. Read it, edit anything, then choose what to do.</div>'
    +  (sensitive?'<div class="warnbanner">'+IC.alert+'<span>'+esc(sensitiveNote(t,d))+'</span></div>':'')
    +  '<textarea class="dedit" id="draft-text" spellcheck="true" '+(disabled?'disabled':'')+'>'+esc(S.draftText)+'</textarea>';
  if(closed){
    body+='<div class="hint" style="margin-top:10px">This ticket is done. Reopen it to reply.</div>';
  } else if(S.sendPending){
    body+='<div class="sendingnote">'+IC.send+'<span>Sending your reply in a few seconds — use <b>Undo</b> on the bar to stop it.</span></div>';
  } else if(S.confirmSend){
    body+='<div class="confirm '+(sensitive?"sens":"")+'"><b>Really send this to '+esc(name)+'?</b>'
      +'<button class="btn send" id="do-send">Yes, send</button>'
      +'<button class="btn" id="cancel-send">Cancel</button></div>';
  } else {
    body+='<div class="draftact">'
      +'<button class="btn send big" id="send" '+(S.busy?"disabled":"")+'>'+IC.send+' Send to customer</button>'
      +'<button class="btn" id="savenote" '+(S.busy?"disabled":"")+'>'+IC.note+' Save as private note</button>'
      +'<button class="btn ghost" id="rewrite" '+(S.busy?"disabled":"")+'>'+IC.spark+' Ask AI to rewrite</button>'
      +'</div>';
    if(S.rewriteOpen){
      body+='<div class="rewritebox"><label>Tell the AI what to change</label>'
        +'<input class="tin" id="rw-instr" placeholder="e.g. Make it warmer and offer free reshipping">'
        +'<div class="row"><button class="btn p" id="do-rewrite" '+(S.busy?"disabled":"")+'>'+(S.busy?"Rewriting…":"Rewrite it")+'</button>'
        +'<button class="btn ghost" id="cancel-rewrite">Cancel</button></div></div>';
    }
  }
  body+='</div>';
  return body;
}
function sensitiveNote(t,d){
  var r=(t.sensitive_reason||d.risk_reason||"").toLowerCase();
  var word = r.indexOf("refund")>=0?"a refund"
    : r.indexOf("damage")>=0||r.indexOf("broken")>=0?"a damaged item"
    : r.indexOf("wrong")>=0?"a wrong item"
    : r.indexOf("missing")>=0||r.indexOf("never")>=0||r.indexOf("lost")>=0?"a missing order"
    : r.indexOf("charge")>=0||r.indexOf("dispute")>=0?"a payment dispute"
    : "a sensitive issue";
  return "This one mentions "+word+" — please read carefully before sending.";
}

function orderCardHtml(o){
  var st=orderStatus(o);
  var li=(o.line_items||[]).map(function(x){ return esc((x.quantity||1)+"× "+(x.title||"item")); }).join("<br>");
  var track = (o.tracking_url||o.tracking_number)
    ? '<div class="track">📦 <a href="'+esc(o.tracking_url||"#")+'" target="_blank" rel="noopener">Track parcel</a></div>' : '';
  return '<div class="order"><div class="otop"><span class="onm">'+esc(o.name||"Order")+'</span><span class="ost '+st.cls+'">'+esc(st.label)+'</span></div>'
    +(li?'<div class="oli">'+li+'</div>':'')
    +track
    +(o.total_price?'<div class="oprice">'+esc(money(o.total_price,o.currency))+' · '+esc(ago(o.created_at))+'</div>':'')
    +'</div>';
}
function returnCardHtml(r){
  var items=(r.items||[]).map(function(x){ return esc((x.qty||x.quantity||1)+"× "+(x.title||"item")+(x.reason?(" — "+x.reason):"")); }).join("<br>");
  return '<div class="ret"><div class="rtop"><span>'+esc(r.order_name||"Return")+'</span><span>'+esc((r.status||"").replace(/_/g," "))+'</span></div>'
    +(items?'<div class="rli">'+items+'</div>':'')
    +(r.refund_amount?'<div class="rli">Refund: '+esc(money(r.refund_amount,"USD"))+'</div>':'')
    +'</div>';
}
function orderCtx(t){
  var oc=t.order_context||{orders:[],returns:[]};
  return {orders:oc.orders||[], returns:oc.returns||[]};
}
/* One-line summary like "2 orders · 1 on its way" for the mobile chip. */
function orderSummaryText(orders){
  if(!orders.length) return "No orders on file";
  var onWay=0;
  orders.forEach(function(o){ if(orderStatus(o).cls==="ontheway") onWay++; });
  var s=orders.length+" order"+(orders.length===1?"":"s");
  if(onWay) s+=" · "+onWay+" on its way";
  return s;
}
/* Mobile-only chip inside the draft card: shows the order summary above the draft
   (on phones the sidebar stacks below, so orders would otherwise be out of sight),
   expands the order/return detail on tap. Hidden on wide screens via CSS. */
function orderChipHtml(t){
  var oc=orderCtx(t);
  var inner = (oc.orders.length ? oc.orders.map(orderCardHtml).join("") : '<div class="hint">No orders found for this customer.</div>')
    + (oc.returns.length ? '<div class="sec-title" style="margin:14px 0 10px">Returns &amp; refunds</div>'+oc.returns.map(returnCardHtml).join("") : '');
  return '<div class="ordchip-wrap">'
    +'<button class="ordchip" id="ordchip" aria-expanded="false">'
    +  '<span class="ocleft">📦 '+esc(orderSummaryText(oc.orders))+'</span>'
    +  '<span class="occhev">'+IC.chev+'</span>'
    +'</button>'
    +'<div class="ordchip-panel" id="ordchip-panel">'+inner+'</div>'
    +'</div>';
}
function asideHtml(t){
  var c=t.customer||{};
  var oc=orderCtx(t);
  var orders=oc.orders, returns=oc.returns;
  var custPanel=''
    +'<div class="panel"><h3>Customer</h3>'
    +  '<div class="custrow"><div class="avt">'+esc(initials(c.name))+'</div><div><b>'+esc(c.name||"Customer")+'</b><small>'+esc(c.email||"")+'</small></div></div>'
    +'</div>';
  // On narrow screens these are replaced by the in-draft order chip (item 6).
  var ordPanel = orders.length
    ? '<div class="panel hide-narrow"><h3>Their orders</h3>'+orders.map(orderCardHtml).join("")+'</div>'
    : '<div class="panel hide-narrow"><h3>Their orders</h3><div class="hint">No orders found for this customer.</div></div>';
  var retPanel = returns.length
    ? '<div class="panel hide-narrow"><h3>Returns &amp; refunds</h3>'+returns.map(returnCardHtml).join("")+'</div>'
    : '';
  return '<div class="aside">'+custPanel+ordPanel+retPanel+'</div>';
}

function captureDraft(){
  var d=el("draft-text");
  if(d){ S.draftText=d.value; S.draftEdited=(d.value!==(S.draftOriginal||"")); }
}

function bindTicket(){
  var b;
  // These handlers all re-render — capture the human's in-progress edits FIRST so
  // they're never clobbered by the AI's original draft (P0 bug B1).
  b=el("close"); if(b) b.onclick=function(){ captureDraft(); patchTicket({status:"closed"}, "Marked as done."); };
  b=el("reopen"); if(b) b.onclick=function(){ captureDraft(); patchTicket({status:"open"}, "Reopened."); };
  b=el("snoozebtn"); if(b) b.onclick=function(){ captureDraft(); S.snoozeOpen=!S.snoozeOpen; render(); };
  $all("[data-snooze]").forEach(function(x){ x.onclick=function(){
    captureDraft();
    var days=parseInt(x.getAttribute("data-snooze"),10);
    var until=new Date(Date.now()+days*86400000).toISOString();
    S.snoozeOpen=false;
    patchTicket({status:"snoozed", snooze_until:until}, days===1?"Snoozed until tomorrow.":"Snoozed for 3 days.");
  }; });

  // Tags: inline input (no more native prompt) + per-tag removal.
  b=el("addtag"); if(b) b.onclick=function(){ captureDraft(); S.tagInputOpen=true; render(); setTimeout(function(){ var i=el("tagin"); if(i) i.focus(); },0); };
  b=el("tagin"); if(b){
    b.onkeydown=function(e){
      if(e.key==="Enter"){
        e.preventDefault();
        var v=b.value.trim();
        captureDraft();
        if(v){
          var tags=(S.ticket.tags||[]).slice();
          if(tags.indexOf(v)<0) tags.push(v);
          S.tagInputOpen=false;
          patchTicket({tags:tags}, "Tag added.");
        } else { S.tagInputOpen=false; render(); }
      } else if(e.key==="Escape"){ e.preventDefault(); captureDraft(); S.tagInputOpen=false; render(); }
    };
    b.onblur=function(){ if(S.tagInputOpen){ captureDraft(); S.tagInputOpen=false; render(); } };
  }
  $all("[data-rmtag]").forEach(function(x){ x.onclick=function(){
    captureDraft();
    var name=x.getAttribute("data-rmtag");
    var tags=(S.ticket.tags||[]).filter(function(tg){ return tg!==name; });
    patchTicket({tags:tags}, "Label removed.");
  }; });

  // Collapse/expand older messages.
  b=el("showmore"); if(b) b.onclick=function(){ captureDraft(); S.showAllMsgs=true; render(); };

  // Mobile order chip: toggle in place (DOM only) so a re-render never collapses it.
  b=el("ordchip"); if(b) b.onclick=function(){
    var p=el("ordchip-panel"); if(!p) return;
    var open=p.classList.toggle("open");
    b.setAttribute("aria-expanded", open?"true":"false");
    b.classList.toggle("open", open);
  };

  // Live "Edited" flag as the human types (no full re-render).
  var dt=el("draft-text");
  if(dt) dt.oninput=function(){
    S.draftText=dt.value; S.draftEdited=(dt.value!==(S.draftOriginal||""));
    var et=el("edited-tag");
    if(et){ if(S.draftEdited) et.removeAttribute("hidden"); else et.setAttribute("hidden",""); }
  };

  // Draft actions.
  b=el("send"); if(b) b.onclick=function(){ captureDraft(); if(!S.draftText.trim()){ toast("Nothing to send — the reply is empty.","err"); return; } S.confirmSend=true; render(); };
  b=el("cancel-send"); if(b) b.onclick=function(){ captureDraft(); S.confirmSend=false; render(); };
  b=el("do-send"); if(b) b.onclick=scheduleSend;
  b=el("savenote"); if(b) b.onclick=doNote;
  b=el("rewrite"); if(b) b.onclick=function(){ captureDraft(); S.rewriteOpen=!S.rewriteOpen; render(); setTimeout(function(){ var i=el("rw-instr"); if(i) i.focus(); },0); };
  b=el("cancel-rewrite"); if(b) b.onclick=function(){ captureDraft(); S.rewriteOpen=false; render(); };
  b=el("do-rewrite"); if(b) b.onclick=doRewrite;
}

function patchTicket(body, okMsg){
  S.busy=true;
  return jfetch(API+"/tickets/"+S.ticket.id,{method:"PATCH",headers:{"content-type":"application/json"},body:JSON.stringify(body)})
    .then(function(r){
      S.busy=false;
      if(r.ok&&r.data&&r.data.ticket){ S.ticket=r.data.ticket; toast(okMsg||"Saved."); render(); }
      else toast("That didn't work — please try again.","err");
    });
}
/* ---- undo-send: confirm → 5s "Undo" window → actual POST -------------------
   The confirm step still gates every send. After "Yes, send" we do NOT post
   right away: a 5-second "Sending — Undo" bar appears. Undo returns to the
   editable draft; letting it run — or navigating away — actually sends. */
function scheduleSend(){
  captureDraft();
  if(!S.draftText.trim()){ toast("Nothing to send — the reply is empty.","err"); return; }
  if(pendingSend) flushPendingSend();
  var rec={ ticketId:S.ticket.id, text:S.draftText, edited:S.draftEdited, timer:null, bar:null };
  S.confirmSend=false; S.sendPending=true; render();
  rec.bar=showUndoBar(cancelSend);
  rec.timer=setTimeout(commitSend, 5000);
  pendingSend=rec;
}
function cancelSend(){
  if(!pendingSend) return;
  clearTimeout(pendingSend.timer);
  removeUndoBar(pendingSend.bar);
  var here = S.view==="ticket" && S.ticket && pendingSend.ticketId===S.ticket.id;
  pendingSend=null; S.sendPending=false;
  if(here) render();
  toast("Okay — nothing sent. Keep editing.");
}
function commitSend(){
  if(!pendingSend) return;
  var ps=pendingSend; pendingSend=null;
  if(ps.timer) clearTimeout(ps.timer);
  removeUndoBar(ps.bar);
  var body={text:ps.text}; if(ps.edited) body.edited=true;   // server ignores unknown fields (Pydantic extra=ignore)
  return jfetch(API+"/tickets/"+ps.ticketId+"/send",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify(body)})
    .then(function(r){
      var here = S.view==="ticket" && S.ticket && S.ticket.id===ps.ticketId;
      if(r.ok&&r.data&&r.data.ok){
        toast("Sent to the customer.");
        if(here){ S.sendPending=false; openTicket(ps.ticketId); }
      } else {
        var msg = r.status===502 ? "The message couldn't leave — the mailbox is unavailable. Nothing was sent."
                : r.status===409 ? "That ticket is closed — reopen it to reply. Nothing was sent."
                : "Send failed — nothing was sent. Please try again.";
        toast(msg,"err");
        if(here){ S.sendPending=false; render(); }
      }
    });
}
// Finish a still-pending send immediately (called when the user navigates away).
function flushPendingSend(){ if(pendingSend) commitSend(); }

function showUndoBar(onUndo){
  var wrap=el("toasts"); if(!wrap) return null;
  var t=document.createElement("div");
  t.className="toast undo"; t.setAttribute("role","status");
  var secs=5;
  var span=document.createElement("span"); span.className="undomsg"; span.textContent="Sending in "+secs+"s…";
  var btn=document.createElement("button"); btn.className="undobtn"; btn.type="button"; btn.textContent="Undo";
  btn.onclick=function(){ if(typeof onUndo==="function") onUndo(); };
  t.appendChild(span); t.appendChild(btn);
  wrap.appendChild(t);
  t._iv=setInterval(function(){ secs--; if(secs<=0){ clearInterval(t._iv); t._iv=null; span.textContent="Sending…"; } else span.textContent="Sending in "+secs+"s…"; }, 1000);
  return t;
}
function removeUndoBar(t){ if(!t) return; if(t._iv){ clearInterval(t._iv); t._iv=null; } if(t.parentNode) t.parentNode.removeChild(t); }

function doNote(){
  captureDraft();
  if(!S.draftText.trim()){ toast("Nothing to save.","err"); return; }
  S.busy=true; render();
  var body={text:S.draftText}; if(S.draftEdited) body.edited=true;
  jfetch(API+"/tickets/"+S.ticket.id+"/note",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify(body)})
    .then(function(r){
      S.busy=false;
      if(r.ok&&r.data&&r.data.ok){ toast("Saved as a private note."); openTicket(S.ticket.id); }
      else { toast("Couldn't save the note.","err"); render(); }
    });
}
function doRewrite(){
  var i=el("rw-instr"); var instr=i?i.value.trim():"";
  captureDraft();
  if(!instr){ toast("Tell the AI what to change first.","err"); return; }
  S.busy=true; render();
  jfetch(API+"/tickets/"+S.ticket.id+"/rewrite",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({instruction:instr})})
    .then(function(r){
      S.busy=false; S.rewriteOpen=false;
      if(r.ok&&r.data&&r.data.draft){
        S.ticket.draft=r.data.draft;
        S.draftText=r.data.draft.body_text||"";
        S.draftOriginal=S.draftText;   // a fresh AI draft — no longer "edited"
        S.draftEdited=false;
        toast("Rewritten — take a look."); render();
      }
      else if(r.status===409){ toast("There's no live draft to rewrite.","err"); render(); }
      else { toast("Rewrite failed — please try again.","err"); render(); }
    });
}

/* ============================================================================
   CUSTOMERS
   ========================================================================= */
function customersBody(){
  if(S.custDetail) return customerDetailHtml(S.custDetail);
  return ''
    +'<div class="filterbar"><div class="search" style="margin-left:0;flex:0 1 340px">'+IC.search+'<input id="cq" type="search" placeholder="Search by name or email…" value="'+esc(S.custQuery)+'"></div></div>'
    +'<div id="custlist">'+customerListHtml()+'</div>';
}
function customerListHtml(){
  if(!S.customers) return '<div class="loading"><span class="spin"></span> Loading…</div>';
  if(!S.customers.length) return '<div class="empty"><div class="big">🔍</div><h3>No customers found</h3><div class="hint">Try a different name or email.</div></div>';
  return '<div class="custlist">'+S.customers.map(function(c){
    return '<div class="custcard" data-cust="'+c.id+'" role="button" tabindex="0"><div class="avt">'+esc(initials(c.name))+'</div><div><b>'+esc(c.name||"Customer")+'</b><small>'+esc(c.email||c.phone||"")+'</small></div><span class="chev">'+IC.chev+'</span></div>';
  }).join("")+'</div>';
}
function customerDetailHtml(d){
  var c=d.customer||{};
  var tickets=d.tickets||[];
  return ''
    +'<button class="backbtn" id="cust-back" style="margin-bottom:14px">'+IC.back+' All customers</button>'
    +'<div class="panel" style="margin-bottom:16px"><div class="custrow"><div class="avt">'+esc(initials(c.name))+'</div><div><b>'+esc(c.name||"Customer")+'</b><small>'+esc(c.email||"")+(c.phone?" · "+esc(c.phone):"")+'</small></div></div></div>'
    +'<div class="sec-title">Their tickets ('+tickets.length+')</div>'
    +(tickets.length?'<div class="tklist">'+tickets.map(ticketCard).join("")+'</div>'
       :'<div class="empty"><div class="big">📭</div><h3>No tickets yet</h3></div>');
}
function loadCustomers(q){
  S.custQuery=q||"";
  var url=API+"/customers"+(q?("?q="+encodeURIComponent(q)):"?limit=50");
  return jfetch(url).then(function(r){
    S.customers=(r.data&&r.data.customers)||[];
    if(S.view==="customers"&&!S.custDetail){ var l=el("custlist"); if(l){ l.innerHTML=customerListHtml(); bindCustCards(); } }
  });
}
function openCustomer(id){
  jfetch(API+"/customers/"+id).then(function(r){
    if(!r.ok||!r.data){ toast("Couldn't load that customer.","err"); return; }
    S.custDetail=r.data; render();
  });
}
function bindCustomers(){
  var cq=el("cq");
  if(cq) cq.oninput=function(){ if(searchTimer) clearTimeout(searchTimer); searchTimer=setTimeout(function(){ loadCustomers(cq.value); },300); };
  var cb=el("cust-back"); if(cb) cb.onclick=function(){ S.custDetail=null; render(); loadCustomers(S.custQuery); };
  bindCustCards();
}
function bindCustCards(){
  $all("[data-cust]").forEach(function(c){
    c.onclick=function(){ openCustomer(parseInt(c.getAttribute("data-cust"),10)); };
    c.onkeydown=function(e){ if(e.key==="Enter"||e.key===" "){ e.preventDefault(); c.click(); } };
  });
  // in the detail view, tickets are clickable too
  bindTicketCards();
}

/* ============================================================================
   STATS
   ========================================================================= */
var STATS=null;
function loadStats(){
  return Promise.all([jfetch(API+"/stats"), jfetch(API+"/tickets?status=open&limit=1")]).then(function(res){
    STATS=(res[0].data)||{};
    if(res[1].data&&res[1].data.counts) S.counts=res[1].data.counts;
    if(S.view==="stats") render();
  });
}
function statsBody(){
  if(!STATS) return '<div class="loading"><span class="spin"></span> Loading…</div>';
  var s=STATS;
  var avg = s.avg_first_response_minutes;
  var avgLabel = (avg==null?"—":(avg>=60?(Math.round(avg/6)/10)+" h":avg+" min"));
  var kpis=[
    {l:"Tickets today", v:(s.tickets_today!=null?s.tickets_today:0), sub:"new conversations"},
    {l:"Waiting for you", v:(S.counts.open||0), sub:"need a reply"},
    {l:"Avg first reply", v:avgLabel, sub:"time to first response"},
    {l:"AI drafts accepted", v:((s.drafts_accepted_pct!=null?s.drafts_accepted_pct:0)+"%"), sub:"sent as written or edited"}
  ];
  var bc=s.by_channel||{};
  var rows=[["email","Email"],["chat","Chat"],["whatsapp","WhatsApp"]];
  var mx=Math.max(1, bc.email||0, bc.chat||0, bc.whatsapp||0);
  var colors={email:"var(--chan-email-ink)",chat:"var(--acc)",whatsapp:"var(--green)"};
  return ''
    +'<div class="kpis">'+kpis.map(function(k){ return '<div class="kpi"><div class="l">'+esc(k.l)+'</div><div class="v">'+esc(k.v)+'</div><div class="s">'+esc(k.sub)+'</div></div>'; }).join("")+'</div>'
    +'<div class="sec-title">Where messages come from</div>'
    +'<div class="panel"><div class="hb">'+rows.map(function(r){
        var n=bc[r[0]]||0;
        return '<div class="r"><div class="lab">'+chanIconInk(r[0])+esc(r[1])+'</div><div class="track"><div class="fill" style="width:'+Math.round(n/mx*100)+'%;background:'+colors[r[0]]+'"></div></div><div class="n">'+n+'</div></div>';
      }).join("")+'</div></div>';
}

/* ============================================================================
   SETTINGS
   ========================================================================= */
function settingsBody(){
  var h=S.health||{};
  var conns=[
    {ic:"F", nm:"Fable server", sub:"The help desk itself", ok:!!h.ok, note:(h.ok?("Brain: "+(h.brain||"?")+" · "+(h.queue_depth||0)+" jobs queued"):"Not responding")},
    {ic:"S", nm:"Shopify (orders)", sub:"Where order info comes from", ok:!!h.ok, note:"Read-only — Fable never changes orders"},
    {ic:"R", nm:"Redo (returns)", sub:"Return & refund status", ok:!!h.ok, note:"Read-only"},
    {ic:"@", nm:"Mailbox", sub:"Sends approved email replies", ok:!!h.ok, note:"Nothing leaves without your click"}
  ];
  var rules=[
    ["Fable never sends anything by itself.","Every reply is a suggestion. It only goes out when you click Send.","check"],
    ["You confirm every send.","Sending always asks 'Really send?' first — no accidental replies.","check"],
    ["Tricky tickets are flagged, not hidden.","Refunds, damaged items and upset customers get an amber warning so you look closely.","alert"],
    ["Outside data is read-only.","Fable can read orders and returns, but can't change them.","check"],
    ["Everything is written down.","Every send, note and rewrite is logged.","check"]
  ];
  return ''
    +'<div class="sec-title">What\'s connected</div>'
    +'<div class="setgrid">'+conns.map(function(c){
        return '<div class="conn"><div class="h"><div class="ic">'+esc(c.ic)+'</div><div><b>'+esc(c.nm)+'</b><small>'+esc(c.sub)+'</small></div></div>'
          +'<span class="stt '+(c.ok?"ok":"bad")+'"><span class="dot"></span> '+(c.ok?"Connected":"Offline")+'</span>'
          +'<div class="hint" style="margin-top:10px">'+esc(c.note)+'</div></div>';
      }).join("")+'</div>'
    +'<div class="sec-title">How Fable keeps you safe</div>'
    +'<div class="safety"><ul>'+rules.map(function(r){
        return '<li><span class="ck" style="'+(r[2]==="alert"?"background:var(--amber-s);color:var(--amber-ink)":"")+'">'+IC[r[2]]+'</span><div><b>'+esc(r[0])+'</b><br>'+esc(r[1])+'</div></li>';
      }).join("")+'</ul></div>';
}

/* ============================================================================
   BIND DISPATCH + BOOT
   ========================================================================= */
function bindView(){
  if(S.view==="inbox") bindInbox();
  else if(S.view==="ticket") bindTicket();
  else if(S.view==="customers") bindCustomers();
  // stats + settings have no interactive controls beyond the shell
}

function loadHealth(){
  return jfetch(API+"/health").then(function(r){ S.health=(r.ok&&r.data)?r.data:{ok:false}; });
}

// If the tab closes mid-undo-window, still deliver the send (best-effort, keepalive).
window.addEventListener("beforeunload", function(){
  if(!pendingSend) return;
  var ps=pendingSend; pendingSend=null;
  if(ps.timer) clearTimeout(ps.timer);
  var body={text:ps.text}; if(ps.edited) body.edited=true;
  try{
    fetch(API+"/tickets/"+ps.ticketId+"/send",{
      method:"POST", headers:{"content-type":"application/json"},
      body:JSON.stringify(body), keepalive:true
    });
  }catch(e){/* nothing more we can do on unload */}
});

function boot(){
  loadHealth().then(function(){
    return refreshInbox(false);
  }).then(function(){
    render();
    startPoll();
  });
}
boot();

})();
