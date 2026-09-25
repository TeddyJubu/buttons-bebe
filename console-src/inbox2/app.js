import { icons } from './icons.js';
const $ = (selector, root = document) => root.querySelector(selector);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icon = name => `<svg class="icon" xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${icons[name] || icons.info}</svg>`;
for (const el of document.querySelectorAll('[data-icon]')) el.replaceWith(document.createRange().createContextualFragment(icon(el.dataset.icon)));
const date = (value, time = true) => {
  const d = new Date(value);
  return value && Number.isFinite(+d) ? new Intl.DateTimeFormat('en-GB', {day:'numeric',month:'short',...(time ? {hour:'2-digit',minute:'2-digit'} : {})}).format(d) : 'Not observed';
};
const statusTone = value => {
  const status = String(value || '').toLowerCase();
  if (['closed','paid','fulfilled','delivered','confirmed','success'].includes(status)) return 'green';
  if (['open','pending','unpaid','partially_paid','partially_refunded','partially_fulfilled','unfulfilled','on_hold','in_progress','high'].includes(status)) return 'amber';
  if (['failed','error','critical','declined'].includes(status)) return 'red';
  return 'neutral';
};
const label = value => String(value || '').toLowerCase().replace(/_/g,' ').replace(/^./, c => c.toUpperCase());
const initials = value => String(value || '?').trim().split(/\s+/).slice(0,2).map(x=>x[0]).join('').toUpperCase();
const money = (value, code = false) => {
  const m = value?.shopMoney || value;
  if (m?.amount == null || !Number.isFinite(Number(m.amount))) return 'Not available';
  try {return new Intl.NumberFormat('en-US',{style:'currency',currency:m.currencyCode || 'USD',currencyDisplay:code?'code':'symbol'}).format(Number(m.amount));} catch {return `${m.amount} ${m.currencyCode || ''}`;}
};
const webUrl = value => {try {const u = new URL(value);return ['https:','http:'].includes(u.protocol) ? u.href : '';} catch {return '';}};
const plain = value => {
  let text = String(value || '');
  if (/<(?:html|div|p|br|table|blockquote)\b/i.test(text)) {
    const doc = new DOMParser().parseFromString(text, 'text/html');
    doc.querySelectorAll('script,style,head').forEach(el=>el.remove());
    doc.querySelectorAll('br').forEach(el=>el.replaceWith('\n'));
    doc.querySelectorAll('p,div,tr,blockquote').forEach(el=>el.append('\n'));
    text = doc.body.textContent || '';
  }
  return text.replace(/\r\n?/g,'\n').replace(/\u00a0/g,' ').replace(/\n{4,}/g,'\n\n\n').trim();
};
const keys = {state:'bb-inbox-ticket-state-v1',local:'bb-inbox-local-tickets-v1',read:'bb-inbox-read-v1',drafts:'bb-inbox2-composer-v1',dismiss:'bb-inbox2-dismissed-v1'};
const memory = new Map();
function stored(key, fallback) {if(memory.has(key))return memory.get(key);try {const raw = localStorage.getItem(key);return raw ? JSON.parse(raw) : (memory.get(key) ?? fallback);} catch {return memory.get(key) ?? fallback;}}
function persist(key, value) {memory.set(key,value);try {localStorage.setItem(key,JSON.stringify(value));memory.delete(key);return true;} catch {toast('Browser storage is unavailable. Changes will last for this session only.');return false;}}
function objectStore(key) {const v=stored(key,{});return v && typeof v==='object'&&!Array.isArray(v)?v:{};}
function arrayStore(key) {const v=stored(key,[]);return Array.isArray(v)?v:[];}
let toastTimer;
function toast(message) {$('#toast').textContent=message;$('#toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').hidden=true,6000);}
let params = new URLSearchParams(location.search);
const state = {rows:[],ticket:null,id:params.get('ticket')||'',query:params.get('q')||'',view:['all','open','closed'].includes(params.get('view'))?params.get('view'):'all',page:0,size:9,total:0,hasNext:false,oldest:false,loading:true,error:'',projection:null,tab:'conversation',operator:'',ticketRequest:0,listRequest:0};
$('#search').value=state.query;
async function api(tool, args={}) {
  const response = await fetch('/inbox2/api/helpdesk',{method:'POST',credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json'},body:JSON.stringify({tool:`helpdesk.${tool}`,arguments:args}),signal:AbortSignal.timeout(65000)});
  if (response.status===401 || response.redirected) {const error=new Error('Your session has expired. Sign in to continue.');error.auth=true;throw error;}
  if (!response.ok) {const body=await response.json().catch(()=>({}));const error=new Error(body.message||'Ticket data could not be loaded. Please try again.');error.gone=response.status===404;throw error;}
  const result=await response.json();
  if (!result.ok) throw new Error(result.message || 'The requested ticket is unavailable.');
  if (result.source && result.source!=='gorgias_api') throw new Error('Live ticket data is unavailable.');
  return result;
}
function localRows() {return [];}
function localValue() {return '';}
function observed(ticket, field) {
  if(field==='priority') return ticket.gorgiasPriority || '';
  if(field==='assignee') return ticket.assigneeEmail || (typeof ticket.assignee==='string'?ticket.assignee:ticket.assignee?.email || ticket.assignee?.name) || '';
  return ticket[field] || '';
}
function effective(ticket, field) {return observed(ticket,field);}
function allRows() {return [...localRows(),...state.rows];}
function filtered() {return state.rows;}
function syncUrl(push=false) {
  const p=new URLSearchParams();if(state.id)p.set('ticket',state.id);if(state.view!=='all')p.set('view',state.view);if(state.query)p.set('q',state.query);
  const url='/inbox2/'+(p.size?'?'+p.toString():'');
  if(location.pathname+location.search!==url) history[push?'pushState':'replaceState']({},'',url);
}
function rowTitle(t) {return t.subject || 'No subject';}
function age(value) {const days=Math.max(0,Math.floor((Date.now()-new Date(value))/86400000));return Number.isFinite(days)?days===0?'Today':`${days}d`:'';}
function listRender() {
  const rows=filtered();
  const offset=state.page*state.size,page=rows;
  $('#ticket-list').innerHTML=page.map(t=>`<button class="ticket-row" data-ticket="${esc(t.id)}" ${t.id===state.id?'aria-current="true"':''}><div class="row-top"><span class="row-name">${esc(t.customerName||'Unknown sender')}</span><span class="age">${esc(age(t.updatedAt))}</span></div><div class="row-subject">${esc(rowTitle(t))}</div><div class="row-snippet">${esc(plain(t.snippet)||'No message preview')}</div>${t.localOnly||effective(t,'status')==='closed'?`<div class="row-state">${t.localOnly?'Local ticket':`Closed${t.draftAction==='auto_close'?' · Auto-close':''}`}</div>`:''}</button>`).join('')||`<div class="empty-state${state.error?' is-error':''}">${esc(state.loading?'Loading tickets…':state.error||'No tickets match this view.')}${state.error?'<br><button class="button" data-action="refresh">Try again</button>':''}</div>`;
  const count=state.loading&&!rows.length?'Loading tickets…':rows.length?`${offset+1}–${Math.min(offset+rows.length,state.total)} of ${state.total.toLocaleString()}`:'0 tickets';
  $('#count').textContent=count;
  $('[data-action="page-prev"]').disabled=state.page===0;
  $('[data-action="page-next"]').disabled=!state.hasNext;
  for(const button of document.querySelectorAll('[data-view]'))button.setAttribute('aria-pressed',String(button.dataset.view===state.view));
  $('#sort-button').innerHTML=`${state.oldest?'Oldest':'Newest'} first ${icon('sort')}`;
  $('#window-label').textContent='Gorgias · Read only';
  $('#refresh span').textContent=state.error||state.projection?.stale?'Sync delayed · Retry':state.projection&&!state.projection.complete?'Syncing ticket history…':state.projection?.generatedAt?`Synced ${date(state.projection.generatedAt)}`:'Connecting to Gorgias…';
  $('#sync-status').classList.toggle('sync-delayed', Boolean(state.error || state.projection?.stale));
  $('#sync-status').textContent=state.projection?.stale?'Gorgias refresh is delayed. Showing the last successful sync.':state.projection&&!state.projection.complete?'Importing ticket history. Search and counts will expand as tickets arrive.':'Refreshes automatically every 30 seconds.';
  if(state.ticket) updateTicketNavigation();
}
function updateTicketNavigation(){const index=state.rows.findIndex(t=>t.id===state.id);const prev=$('[data-action="ticket-prev"]'),next=$('[data-action="ticket-next"]');if(prev)prev.disabled=index<0||index===0&&state.page===0;if(next)next.disabled=index<0||index===state.rows.length-1&&!state.hasNext;}
async function loadList(background=false){
  if(background&&state.loading)return;
  const request=++state.listRequest;state.loading=true;state.error='';if(!background)listRender();
  try{
    const result=await api('list_tickets',{view:state.view,query:state.query,oldest:state.oldest,limit:state.size,offset:state.page*state.size});
    if(request!==state.listRequest)return;
    state.rows=result.tickets;state.total=result.total;state.hasNext=result.nextOffset!=null;state.projection=result.projection;state.loading=false;
    if(state.page>0&&!state.rows.length){state.page=Math.max(0,Math.ceil(state.total/state.size)-1);return loadList();}
    listRender();
    if(!state.id){const first=state.rows[0];if(first)selectTicket(first.id,false);else $('#conversation').innerHTML='<div class="empty-state">'+(state.projection?.complete?'No tickets in this view.':'Connecting to Gorgias. Tickets will appear as they sync.')+'</div>';}
  }catch(error){if(request!==state.listRequest)return;state.loading=false;state.error=error.message;listRender();if(error.auth)showAuth();else if(!background)toast(error.message);}
}
let refreshingTicket=false;
async function refreshTicket(){
  if(!state.ticket||refreshingTicket||document.activeElement?.closest('.reply-area'))return;
  const id=state.id,request=state.ticketRequest;refreshingTicket=true;
  try{
    const result=await api('get_ticket',{ticketId:id});if(id!==state.id||request!==state.ticketRequest)return;
    const fresh=result.ticket;
    // Retain explicitly loaded earlier pages; refresh the current page in place.
    if(state.olderLoaded){
      const first=fresh.messages[0]?.at;
      fresh.messages=[...state.ticket.messages.filter(m=>first&&new Date(m.at)<new Date(first)),...fresh.messages];
      fresh.messages=[...new Map(fresh.messages.map(m=>[m.id,m])).values()];
      fresh.messagesNextCursor=state.ticket.messagesNextCursor;
      fresh.historyIncomplete=Boolean(fresh.messagesNextCursor);
    }
    const focusedAction=document.activeElement?.dataset?.action;
    state.ticket=fresh;if(document.activeElement?.closest('.reply-area'))return;renderTicket();renderRail();
    if(focusedAction)document.querySelector(`[data-action="${focusedAction}"]`)?.focus({preventScroll:true});
  }catch(error){if(id!==state.id||request!==state.ticketRequest)return;if(error.auth)showAuth();else if(error.gone){state.ticket=null;$('#conversation').innerHTML='<div class="empty-state">This ticket is no longer available in Gorgias.</div>';$('#customer-rail').innerHTML='';}else{const status=$('#live-ticket-sync');if(status)status.textContent='Refresh delayed · Showing the last successful read';}}
  finally{refreshingTicket=false;}
}
async function loadOlder(){
  const t=state.ticket,cursor=t?.messagesNextCursor;if(!cursor)return;
  const id=state.id,request=state.ticketRequest,button=$('[data-action="older-messages"]');if(button)button.disabled=true;
  try{const result=await api('get_messages',{ticketId:id,cursor});if(id!==state.id||request!==state.ticketRequest)return;
    t.messages=[...new Map([...result.messages,...t.messages].map(m=>[m.id,m])).values()];
    t.messagesNextCursor=result.nextCursor;t.historyIncomplete=Boolean(result.nextCursor);t.observedMessageCount=t.messages.length;state.olderLoaded=true;
    renderTicket();
  }catch(error){toast(error.message);if(button)button.disabled=false;}
}
function showAuth() {const href='/console/login?next='+encodeURIComponent(location.pathname+location.search);$('#conversation').innerHTML=`<div class="empty-state"><h2>Sign in to continue</h2><p>Your support session has expired.</p><a class="button primary" href="${esc(href)}">Sign in</a></div>`;}
function currentDraft(t) {const body=plain(t.readonlyDraft).replace(/^\[SENSITIVE\s*[—–-]\s*REVIEW CAREFULLY BEFORE SENDING\]\s*/i,'').trim();return body.includes('\n')?body:body.replace(/([.!?])\s+(?=(?:Because|Since|However|Please note)\b)/g,'$1\n\n');}
function draftId(t) {return `${t.draftSourceMessageId||''}:${t.draftProcessedAt||''}:${t.readonlyDraft||''}`;}
function draftAvailable(t) {return Boolean(t.readonlyDraft&&!t.draftSuperseded&&objectStore(keys.dismiss)[t.id]!==draftId(t));}
async function selectTicket(id,push=true,focus=false) {
  if(!id)return;
  const request=++state.ticketRequest;state.id=id;state.ticket=null;state.olderLoaded=false;state.tab='conversation';syncUrl(push);listRender();
  $('#workspace').classList.add('ticket-open');$('#workspace').classList.remove('rail-open');
  $('#conversation').innerHTML='<div class="empty-state">Loading conversation…</div>';
  $('#customer-rail').innerHTML='<div class="rail-heading">Customer details</div><div class="empty-state">Loading customer details…</div>';
  try {
    const result=await api('get_ticket',{ticketId:id});
    if(request!==state.ticketRequest)return;
    state.ticket=result.ticket;const read=new Set(arrayStore(keys.read));read.add(id);persist(keys.read,[...read]);
    renderTicket();renderRail();listRender();
    document.title=`${ticketTitle(state.ticket)} · Buttons Bebe Support`;
    if(focus)$('#conversation').focus({preventScroll:true});
  } catch(error) {
    if(request!==state.ticketRequest)return;
    $('#customer-rail').innerHTML='<div class="rail-heading">Customer details</div><div class="empty-state">Customer details unavailable.</div>';
    if(error.auth)showAuth();else $('#conversation').innerHTML=`<div class="empty-state is-error"><h2>Couldn’t load this ticket</h2><p>${esc(error.message)}</p><button class="button" data-action="back">Back to tickets</button> <button class="button primary" data-action="retry-ticket">Try again</button></div>`;
  }
}
function ticketTitle(t) {return t.shopifyRail?.order?.name?`Order ${t.shopifyRail.order.name.replace(/^#/,'')}`:t.subject||'No subject';}
function options(values,selected) {return values.map(([v,text])=>`<option value="${esc(v)}"${v===selected?' selected':''}>${esc(text)}</option>`).join('');}
function control(t,field,values,iconName,prefix='') {
  const value=observed(t,field),text=field==='status'?(label(value)||'Status unavailable'):`${prefix}: ${value?field==='assignee'?value:label(value):'not set'}`;
  return `<div class="control readonly-control ${field==='status'?'status-control '+statusTone(value):field==='priority'?statusTone(value):''}" title="Read-only value from Gorgias"><span>${field==='status'?'<span class="status-dot"></span>':icon(iconName)}</span><span>${esc(text)}</span></div>`;
}
function renderTicket() {
  const t=state.ticket;if(!t)return;
  const expandedQuotes=new Set([...document.querySelectorAll('.quoted-email[open]')].map(el=>el.dataset.messageId));
  const people=[...new Set([state.operator,...allRows().map(x=>observed(x,'assignee')),localValue(t,'assignee')].filter(Boolean))];
  const overrides=['status','priority','assignee'].filter(f=>localValue(t,f)).map(f=>`${label(f)}: ${localValue(t,f)} (observed: ${observed(t,f)||'unknown'})`);
  $('#conversation').innerHTML=`<header class="ticket-header"><button class="mobile-back" data-action="back">${icon('left')} All tickets</button><div class="title-row"><div><h2 class="ticket-title">${esc(ticketTitle(t))}</h2><p class="ticket-subtitle">${esc(t.customerName||'Unknown sender')} · ${esc(t.localOnly?'Local ticket':'#'+t.id.replace(/^gorgias:/,''))} · ${esc(label(t.channel)||'Channel unknown')}</p></div><div class="ticket-navigation"><button class="icon-button" data-action="ticket-prev" aria-label="Previous ticket">${icon('left')}</button><button class="icon-button" data-action="ticket-next" aria-label="Next ticket">${icon('right')}</button><button class="icon-button show-customer" data-action="rail" aria-label="Show customer details" aria-controls="customer-rail" aria-expanded="false">${icon('user')}</button></div></div><div class="ticket-actions">${control(t,'status',[['open','Open · local'],['closed','Closed · local']],'','Status')}${control(t,'priority',[['low','Low · local'],['normal','Normal · local'],['high','High · local'],['critical','Critical · local']],'flag','Priority')}${control(t,'assignee',[['unassigned','Unassigned · local'],...people.filter(x=>x!=='unassigned').map(x=>[x,x+' · local'])],'user','Assignee')}<button class="button copy-link" data-action="copy">${icon('link')} Copy link</button></div>${overrides.length?`<div class="local-observed">Browser changes · ${esc(overrides.join(' · '))}</div>`:''}<p class="live-ticket-sync" id="live-ticket-sync">${t.syncStale?'Refresh delayed · Showing the last successful read':'Read from Gorgias · '+esc(date(t.syncedAt))}</p><div class="ticket-tabs" role="tablist" aria-label="Ticket content"><button role="tab" id="conversation-tab" data-tab="conversation" aria-selected="${state.tab==='conversation'}" aria-controls="ticket-content">Conversation</button><button role="tab" id="details-tab" data-tab="details" aria-selected="${state.tab==='details'}" aria-controls="ticket-content">Ticket details</button></div></header><section id="ticket-content" role="tabpanel" aria-labelledby="${state.tab==='conversation'?'conversation-tab':'details-tab'}">${state.tab==='conversation'?conversationHtml(t):detailsHtml(t)}</section>${replyHtml(t)}`;
  for(const detail of document.querySelectorAll('.quoted-email'))if(expandedQuotes.has(detail.dataset.messageId))detail.open=true;
  updateTicketNavigation();
  syncRailAccessibility();
  const editor=$('#reply');if(editor)editor.value=objectStore(keys.drafts)[t.id]?.body || '';
}
// Presentation only: original Gorgias bodies are never changed.
function decodeMessageEntities(value) {
  const decoder=document.createElement('textarea');
  let text=value;
  for(let pass=0;pass<2;pass++)text=text.replace(/&(?:#\d{1,7}|#x[\da-f]{1,6}|[a-z][a-z\d]{1,31});/gi,entity=>{decoder.innerHTML=entity;return decoder.value;});
  return text;
}
function tidyMessageText(value) {
  return value.replace(/\r\n?/g,'\n').replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\u200b\ufeff\u00ad\u2060\u202a-\u202e\u2066-\u2069]/g,'')
    .replace(/\u00a0/g,' ').replace(/[\t ]+$/gm,'').replace(/\n[\t ]*\n(?:[\t ]*\n)+/g,'\n\n').trim();
}
function messageText(value) {
  let text=String(value||'');
  // Match known HTML tags only; a plain-text <email@example.com> is not HTML.
  if(/<\/?(?:html|body|div|p|br|table|tr|td|blockquote|span|a|ul|ol|li|b|strong|em|i|pre|img|font|style|script)\b[^>]*>/i.test(text)) {
    const doc=new DOMParser().parseFromString(text,'text/html');
    doc.querySelectorAll('script,style,head,meta,link,iframe,object,template,[hidden]').forEach(el=>el.remove());
    doc.querySelectorAll('a[href]').forEach(a=>{
      const href=webUrl(a.getAttribute('href')),caption=a.textContent.trim();
      if(href&&caption&&!caption.includes(href)&&!/^https?:/i.test(caption))a.append(` (${href})`);
    });
    doc.querySelectorAll('br').forEach(el=>el.replaceWith('\n'));
    doc.querySelectorAll('p,div,section,article,pre,tr,ul,ol').forEach(el=>{el.prepend('\n');el.append('\n');});
    doc.querySelectorAll('li').forEach(el=>{el.prepend('\n• ');el.append('\n');});
    doc.querySelectorAll('td,th').forEach(el=>el.append(' '));
    // Email clients mark older history with these containers.
    const selector='blockquote,.gmail_quote,.yahoo_quoted';
    for(const el of doc.querySelectorAll(selector))if(!el.parentElement?.closest(selector)) {
      const quoted=el.textContent.trim().split('\n').map(line=>'> '+line).join('\n');
      el.replaceWith('\n'+quoted+'\n');
    }
    text=doc.body.textContent||'';
  }
  return tidyMessageText(decodeMessageEntities(text));
}
const unquoteLine=line=>line.replace(/^(?:[\t ]*>[\t ]?)+/,'');
function replyHeaderEnd(lines,start) {
  const first=unquoteLine(lines[start]).trim();
  if(!/^(?:On\s|Am\s|Le\s|El\s|Em\s|Op\s|Il\s|בתאריך\s)/i.test(first))return -1;
  let header='';
  for(let end=start;end<Math.min(lines.length,start+8);end++) {
    header+=' '+unquoteLine(lines[end]).trim();
    if(header.length>1200)break;
    if(/(?:wrote|schrieb|a écrit|escribió|escreveu|schreef|ha scritto|כתב(?:ה)?)\s*:\s*$/i.test(header))return end;
  }
  return -1;
}
function metadataHeaderEnd(lines,start) {
  if(!/^\s*(?:From|Sent|Date|To|Cc|Subject):\s*\S/i.test(unquoteLine(lines[start])))return -1;
  let fields=0,end=start;const names=new Set();
  for(let i=start;i<Math.min(lines.length,start+12);i++) {
    const line=unquoteLine(lines[i]);
    if(/^\s*(?:From|Sent|Date|To|Cc|Subject):/i.test(line)){fields++;end=i;names.add(line.trim().split(':')[0].toLowerCase());}
    else if(!line.trim())continue;
    else if(/^\s+\S/.test(line)&&fields){end=i;}
    else break;
  }
  return fields>=2&&names.has('from')&&(names.has('sent')||names.has('date')||fields>=3&&names.has('subject'))?end:-1;
}
function cleanQuotedText(lines) {
  const output=[];
  for(let i=0;i<lines.length;i++) {
    let end=replyHeaderEnd(lines,i);
    if(end<0)end=metadataHeaderEnd(lines,i);
    if(end>=i){i=end;continue;}
    const line=unquoteLine(lines[i]);
    if(/^\s*(?:[-_]{3,}\s*(?:(?:Original|Forwarded) message\s*[-_]*)?|Begin forwarded message:)\s*$/i.test(line))continue;
    output.push(line);
  }
  return tidyMessageText(output.join('\n'));
}
function cleanMessage(value) {
  const body=messageText(value),lines=body.split('\n');
  let start=-1,headerEnd=-1;
  for(let i=0;i<lines.length;i++) {
    const end=replyHeaderEnd(lines,i),metadata=metadataHeaderEnd(lines,i);
    const separator=/^\s*(?:[-_]{3,}\s*(?:Original|Forwarded) message\s*[-_]*|Begin forwarded message:)\s*$/i.test(unquoteLine(lines[i]));
    if(end>=i||metadata>=i||separator||/^\s*>(?:[\t >]|$)/.test(lines[i])){start=i;headerEnd=Math.max(end,metadata,separator?i:-1);break;}
  }
  if(start<0)return {main:body,quoted:''};
  const main=lines.slice(0,start),tail=lines.slice(start),quoted=[];
  const hasQuotePrefixes=tail.some(line=>/^\s*>/.test(line));
  if(!hasQuotePrefixes)return {main:tidyMessageText(main.join('\n')),quoted:cleanQuotedText(tail)};
  // Preserve inline/bottom-posted answers outside the quoted lines.
  for(let i=start;i<lines.length;i++) {
    if(i<=headerEnd||/^\s*>/.test(lines[i]))quoted.push(lines[i]);
    else main.push(lines[i]);
  }
  return {main:tidyMessageText(main.join('\n')),quoted:cleanQuotedText(quoted)};
}
function messageBodyHtml(text) {
  // Keep working links without printing long tracking/query strings as prose.
  let result='',last=0;
  for(const match of text.matchAll(/https?:\/\/[^\s<>]+/gi)) {
    let raw=match[0],suffix='';
    while(/[.,!?;:]$/.test(raw)||/\)$/.test(raw)&&(raw.match(/\)/g)||[]).length>(raw.match(/\(/g)||[]).length){suffix=raw.slice(-1)+suffix;raw=raw.slice(0,-1);}
    result+=esc(text.slice(last,match.index));
    const href=webUrl(raw);
    if(href){const url=new URL(href),caption=raw.length>75?`Open link · ${url.hostname}`:raw;result+=`<a href="${esc(href)}" target="_blank" rel="noopener noreferrer" title="${esc(raw)}">${esc(caption)}</a>${esc(suffix)}`;}
    else result+=esc(match[0]);
    last=match.index+match[0].length;
  }
  return result+esc(text.slice(last));
}

function messageHtml(m,t) {
  const {main,quoted}=cleanMessage(m.body);
  const name=(m.fromName&&m.fromName!==m.fromEmail?m.fromName:'')||(m.fromAgent?'Support':t.customerName)||m.fromName||'Unknown sender',email=m.fromEmail||(!m.fromAgent?t.fromEmail:'');
  return `<article class="message"><div class="message-heading"><span class="avatar">${esc(initials(name))}</span><div class="message-person"><strong>${esc(name)}${m.internal?' · Internal note':''}</strong>${email?`<span>${esc(email)}</span>`:''}</div><time datetime="${esc(m.at||'')}">${esc(date(m.at))}</time></div>${main?`<div class="message-body" dir="auto">${messageBodyHtml(main)}</div>`:quoted?'<p class="message-note">No new message text.</p>':'<p class="message-note">Message text is unavailable.</p>'}${quoted?`<details class="quoted-email" data-message-id="${esc(m.id)}"><summary>${icon('right')} Earlier email</summary><div class="message-body" dir="auto">${messageBodyHtml(quoted)}</div></details>`:''}${m.truncated?'<p class="message-note">Only part of this message is available.</p>':''}</article>`;
}
function conversationHtml(t) {
  return `<div class="message-area">${t.localOnly?`<div class="info-banner">${icon('info')} Local ticket · Saved in this browser. No customer has been contacted.</div>`:t.historyIncomplete?`<div class="info-banner">${icon('info')} Showing recent messages. Use “Load earlier messages” to read more.</div>`:''}${t.projection?.stale?`<div class="info-banner">${icon('info')} Ticket history is awaiting refresh. Last updated ${esc(date(t.projection.generatedAt))}.</div>`:''}${t.messagesNextCursor?'<button class="button older-messages" data-action="older-messages">Load earlier messages</button>':''}${t.messages?.length?t.messages.map(m=>messageHtml(m,t)).join(''):'<div class="empty-state">No messages returned by Gorgias.</div>'}</div>`;
}
function detailsHtml(t) {
  const fields=[['Ticket ID',t.id],['Subject',t.subject||'No subject'],['Customer',t.customerName||'Unknown'],['Email',t.fromEmail||'Not observed'],['Channel',label(t.channel)||'Not observed'],['Gorgias status',label(t.status)||'Not observed'],['Gorgias priority',label(t.gorgiasPriority)||'Not observed'],['Gorgias assignee',observed(t,'assignee')||'Not observed'],['Draft priority',label(t.priority)||'Not available'],['Last activity',date(t.updatedAt)],['Messages loaded',t.observedMessageCount??t.messages?.length??0],['Tags',(t.tags||[]).join(', ')||'None observed'],['Draft source',t.draftSourceMessageId||'Not available']];
  return `<div class="message-area"><dl class="ticket-fields">${fields.map(([k,v])=>`<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl></div>`;
}
function replyHtml(t) {
  const sensitive=t.draftAction==='sensitive_draft'||/^\[SENSITIVE/i.test(t.readonlyDraft||'');
  const draft=draftAvailable(t)?`<section class="draft-card" aria-label="Suggested reply"><div class="draft-heading">${icon('draft')}<h3>Suggested reply</h3><span class="badge amber">${icon('shield')}${sensitive?'Review required':'Review before sending'}</span></div><div class="draft-body" dir="auto">${esc(currentDraft(t))}</div>${t.draftReason?`<div class="draft-warning">${icon('info')}<span>${esc(t.draftReason)}</span></div>`:''}<div class="draft-actions"><button class="button primary" data-action="use-draft">${icon('check')} Use draft</button><button class="button" data-action="dismiss-draft">Dismiss</button>${t.draftSourceMessageId?`<span class="draft-source" title="${esc(date(t.draftProcessedAt))}">Source: ${esc(t.draftSourceMessageId)}</span>`:''}</div></section>`:t.draftSuperseded?`<div class="info-banner">${icon('info')} The conversation has newer messages. The previous suggestion is out of date.</div>`:t.readonlyDraft?'<p class="small muted">Suggestion dismissed. <button data-action="restore-draft">Restore suggestion</button></p>':'<p class="small muted">No suggested reply is available for this ticket yet.</p>';
  return `<section class="reply-area">${draft}<div class="composer"><div class="composer-heading">${icon('reply')}<span>Reply</span><span class="recipient">to ${esc(t.fromEmail||'No email observed')}</span></div><textarea id="reply" maxlength="30000" placeholder="Write your reply…" aria-label="Reply message"></textarea><div class="composer-toolbar"><span class="saved-note" id="saved-note">Draft stays in this browser</span><button class="button" data-action="copy-reply" title="Copy your reply">${icon('copy')} Copy</button><button class="button" data-action="send-gate" aria-disabled="true">${icon('send')} Send</button><button class="button" data-action="send-gate" aria-disabled="true">${icon('down')} Send &amp; close</button></div></div><p class="composer-note">${icon('lock')} Read-only Gorgias access. Replies stay in this browser; use Gorgias to send.</p></section>`;
}
function renderRail() {
  const t=state.ticket;if(!t)return;
  const r=t.shopifyRail||{},c=r.customer,o=r.order;
  const identity=t.customerContext?.status==='observed'&&!t.customerContext?.conflict?t.customerContext.identity||{}:{};
  const name=c?.displayName||identity.name||t.customerName||'Unknown customer';
  const email=c?.defaultEmailAddress?.emailAddress||identity.email||t.fromEmail||'Email not observed';
  let html=`<div class="rail-heading">Customer details<button data-action="rail-close" aria-label="Close customer details">${icon('panel')}</button></div><section class="rail-section"><div class="customer-top"><span class="avatar">${esc(initials(name))}</span><div><div class="customer-name">${esc(name)}</div><div class="customer-email">${esc(email)}</div></div></div>${c?`<dl class="customer-stats"><div><dt>Orders</dt><dd>${esc(c.numberOfOrders??'Unknown')}</dd></div><div><dt>Total spent</dt><dd>${esc(money(c.amountSpent))}</dd></div></dl>${c.createdAt?`<p class="customer-since">Customer since ${esc(date(c.createdAt,false))}</p>`:''}${c.tags?.length?`<p class="customer-tags">${icon('bag')}<span>${esc(c.tags.join(' · '))}</span></p>`:''}`:`<p class="small muted">${esc(t.customerContext?.conflict?'Conflicting customer details need review.':r.status==='missing'?'No matching Shopify customer was found.':r.status==='loading'?'Loading Shopify customer details…':r.status==='unavailable'?'A consistent customer email is needed to look up Shopify details.':r.status==='error'?'Shopify details could not be loaded. Please retry.':'Shopify details are not available yet.')}</p>`}</section>`;
  if(r.status==='loading'||r.refreshing)html+=`<div class="customer-load-status" role="status">${icon('refresh')}<span>${r.refreshing?'Refreshing Shopify details…':'Loading orders, returns, and customer history…'}</span></div>`;
  if(r.status==='error'||r.refreshError)html+=`<div class="customer-load-status customer-load-error" role="status"><span>Shopify lookup is temporarily unavailable. ${r.customer||r.order?'Showing the last saved details.':''}</span><button class="button" data-action="retry-customer">${icon('refresh')} Retry customer details</button></div>`;
  if(r.returns)html+=returnsHtml(r.returns,o);
  if(o)html+=orderHtml(o);
  if(o)html+=`<details class="rail-section"><summary>Addresses ${icon('right')}</summary>${addressHtml('Shipping',o.shippingAddress)}${addressHtml('Billing',o.billingAddress)}</details>`;
  const history=(r.history||[]).filter(x=>x.id!==o?.id);
  if(c)html+=`<details class="rail-section"><summary>Past orders (${history.length}) ${icon('right')}</summary>${history.map(x=>`<div class="past-order"><div><strong>Order ${esc(x.name)}</strong><span>${esc(money(x.currentTotalPriceSet))}</span></div><p>${esc(date(x.createdAt))} · ${esc(label(x.displayFulfillmentStatus)||'Status unknown')}</p></div>`).join('')||'<p class="small muted">No other orders in this snapshot.</p>'}</details>`;
  if(r.fetchedAt)html+=`<div class="snapshot-note"><div>${icon('database')}<span>Shopify snapshot · ${esc(date(r.fetchedAt))}</span></div>${r.stale||r.refreshError?'<p>Snapshot is out of date. The latest refresh was unavailable.</p>':''}</div>`;
  else if(t.customerContext?.observedAt)html+=`<div class="snapshot-note">Observed customer details · ${esc(date(t.customerContext.observedAt))}</div>`;
  const rail=$('#customer-rail'),scrollTop=rail.scrollTop;
  const openSections=new Map([...rail.querySelectorAll('details')].map(el=>[el.querySelector('summary')?.textContent,el.open]));
  const focusedAction=rail.contains(document.activeElement)?document.activeElement.dataset.action:null;
  const focusedSummary=rail.contains(document.activeElement)&&document.activeElement.matches('summary')?document.activeElement.textContent:null;
  rail.innerHTML=html;
  for(const detail of rail.querySelectorAll('details'))if(openSections.has(detail.querySelector('summary')?.textContent))detail.open=openSections.get(detail.querySelector('summary').textContent);
  rail.scrollTop=scrollTop;
  if(focusedAction)rail.querySelector(`[data-action="${focusedAction}"]`)?.focus({preventScroll:true});
  if(focusedSummary)[...rail.querySelectorAll('summary')].find(el=>el.textContent===focusedSummary)?.focus({preventScroll:true});
  syncRailAccessibility();
  scheduleCustomerDetails();
}
function returnsHtml(returns,order) {
  const nodes=returns.returns?.nodes||[],open=nodes.filter(x=>x.status==='OPEN').length;
  return `<details class="rail-section"${nodes.length?' open':''}><summary>Returns <span class="badge ${open?'amber':'neutral'}">${open?`${open} open`:`${nodes.length} on file`}</span>${icon('right')}</summary>${nodes.map(n=>`<div class="return-row"><div class="return-title"><span>${esc(n.name||`Order ${order?.name||''}`)}</span><span class="badge ${n.status==='OPEN'?'amber':'neutral'}">${esc(label(n.status)||'Unknown')}</span></div>${n.createdAt?`<p>${esc(date(n.createdAt))}</p>`:''}${n.items?.length?n.items.map(i=>`<p>${esc(i.title||'Returned item')}${i.quantity!=null?` · Qty ${esc(i.quantity)}`:''}${i.reason?` · ${esc(i.reason)}`:''}${i.note?` · ${esc(i.note)}`:''}</p>`).join(''):'<p>Return on file. Item details unavailable.</p>'}</div>`).join('')||'<p class="small muted">No returns in this snapshot.</p>'}</details>`;
}
function orderHtml(o) {
  const shipments=(o.fulfillments?.nodes||o.fulfillments||[]).map(f=>`<div class="shipment ${statusTone(f.displayStatus)}"><div class="shipment-title">${icon('box')}${esc(label(f.displayStatus)||'Shipment observed')}</div>${(f.trackingInfo||[]).map(tr=>`<div class="shipment-carrier"><span>${esc(tr.company||'Carrier unavailable')}</span>${webUrl(tr.url)?`<a href="${esc(webUrl(tr.url))}" target="_blank" rel="noopener noreferrer">Track ${icon('external')}</a>`:''}</div><div class="tracking-number">${esc(tr.number||'Tracking number unavailable')}</div>`).join('')||'<div class="small muted">Tracking details unavailable</div>'}${f.estimatedDeliveryAt?`<p class="small">Expected ${esc(date(f.estimatedDeliveryAt,false))}</p>`:''}</div>`).join('');
  const items=(o.lineItems?.nodes||[]).slice().reverse().map(i=>{const url=webUrl(i.image?.url);return `<li class="order-item">${url&&new URL(url).hostname==='cdn.shopify.com'?`<img class="product-image" src="${esc(url)}" alt="${esc(i.image.altText||i.title)}" loading="lazy" referrerpolicy="no-referrer">`:`<span class="product-image product-placeholder">${icon(/gift/i.test(i.title)?'gift':'bag')}</span>`}<div><div class="product-name">${esc(i.title||'Product unavailable')}</div><div class="product-meta">${i.variantTitle?`${esc(i.variantTitle)} · `:''}Qty ${esc(i.quantity??'Unknown')}${i.unfulfilledQuantity>0?` · ${esc(i.unfulfilledQuantity)} unfulfilled`:''}</div><div class="product-price">${esc(money(i.originalUnitPriceSet))}</div></div></li>`;}).join('');
  return `<section class="rail-section"><div class="order-title"><h3>Order ${esc(String(o.name||'').replace(/^#/,''))}</h3></div><p class="order-date">${esc(date(o.createdAt))}</p><div class="order-statuses">${o.displayFinancialStatus?`<span class="badge ${statusTone(o.displayFinancialStatus)}">${esc(label(o.displayFinancialStatus))}</span>`:''}${o.displayFulfillmentStatus?`<span class="badge ${statusTone(o.displayFulfillmentStatus)}">${esc(label(o.displayFulfillmentStatus))}</span>`:''}</div>${shipments}<ul class="order-items">${items}</ul><div class="order-total"><span>Total</span><strong>${esc(money(o.currentTotalPriceSet,true))}</strong></div><div class="payment-lock">${icon('lock')} Payments locked</div></section>`;
}
function addressHtml(title,a) {return `<div class="address-block"><h4>${esc(title)}</h4>${a?['name','address1','address2','city','province','zip','country'].map(k=>a[k]?`${esc(a[k])}<br>`:'').join(''):'Address not available'}</div>`;}
function saveReply(value) {if(!state.ticket)return;const drafts=objectStore(keys.drafts);drafts[state.id]={body:value,at:Date.now()};persist(keys.drafts,drafts);}
async function copyText(value,message) {try {await navigator.clipboard.writeText(value);toast(message);}catch {toast('Copy is unavailable in this browser. Select the text and copy it manually.');}}
function setField(field,value) {if(!state.ticket)return;const records=objectStore(keys.state);records[state.id]={...(records[state.id]||{}),[field]:value?{value,by:state.operator||'operator',at:Date.now()}:null};persist(keys.state,records);renderTicket();listRender();toast('Saved in this browser. Observed Gorgias values are unchanged.');}
// Refresh only the context rail while a background lookup runs; never touch the editor.
let customerDetailsTimer;
let customerDetailsRequest=0;
let customerDetailsAttempts=0;
let customerDetailsTicket='';
function scheduleCustomerDetails() {
  clearTimeout(customerDetailsTimer);
  if(customerDetailsTicket!==state.id){customerDetailsTicket=state.id;customerDetailsAttempts=0;}
  const rail=state.ticket?.shopifyRail;
  if(rail?.status==='loading'||rail?.refreshing) {
    customerDetailsTimer=setTimeout(()=>refreshCustomerDetails(),customerDetailsAttempts<15?2000:10000);
  } else if(rail?.refreshError) {
    customerDetailsTimer=setTimeout(()=>refreshCustomerDetails(),Math.max(30000,Math.min(300000,(Number(rail.retryAt||0)*1000)-Date.now())));
  }
}
async function refreshCustomerDetails(manual=false) {
  if(!state.ticket||document.hidden){scheduleCustomerDetails();return;}
  clearTimeout(customerDetailsTimer);
  const id=state.id,ticketRequest=state.ticketRequest,request=++customerDetailsRequest;
  const button=$('[data-action="retry-customer"]');if(button)button.disabled=true;
  try {
    const result=await api('get_ticket',{ticketId:id});
    if(state.id!==id||state.ticketRequest!==ticketRequest||!state.ticket||request!==customerDetailsRequest)return;
    state.ticket.shopifyRail=result.ticket.shopifyRail;
    customerDetailsAttempts++;
    renderRail();
    if(manual&&result.ticket.shopifyRail?.refreshError)toast('Shopify is temporarily unavailable. The lookup will retry shortly.');
  } catch(error) {
    if(state.id!==id||state.ticketRequest!==ticketRequest||!state.ticket||request!==customerDetailsRequest)return;
    if(error.auth){showAuth();return;}
    state.ticket.shopifyRail={...(state.ticket.shopifyRail||{}),status:state.ticket.shopifyRail?.customer?'found':'error',refreshing:false,refreshError:true};
    renderRail();
  }
}
// The context rail is a modal drawer below the desktop breakpoint.
let railTrigger = null;
const drawerQuery = matchMedia('(max-width:1040px)');
function syncRailAccessibility() {
  const workspace = $('#workspace'), rail = $('#customer-rail');
  const drawerOpen = drawerQuery.matches && workspace.classList.contains('rail-open');
  const visible = drawerQuery.matches ? drawerOpen : !workspace.classList.contains('rail-collapsed');
  for (const button of document.querySelectorAll('[data-action="rail"]')) button.setAttribute('aria-expanded', String(visible));
  for (const region of [$('.app-header'), $('.ticket-sidebar'), $('#conversation')]) region.inert = drawerOpen;
  if (drawerOpen) { rail.setAttribute('role', 'dialog'); rail.setAttribute('aria-modal', 'true'); }
  else { rail.removeAttribute('role'); rail.removeAttribute('aria-modal'); }
}
function closeRail() {
  $('#workspace').classList.remove('rail-open');
  $('#workspace').classList.add('rail-collapsed');
  syncRailAccessibility();
  const candidate = railTrigger?.isConnected && railTrigger.getClientRects().length ? railTrigger : [...document.querySelectorAll('[data-action="rail"]')].find(el => el.getClientRects().length);
  candidate?.focus({preventScroll:true});
}
drawerQuery.addEventListener('change', () => {
  const focusWasInRail = $('#customer-rail').contains(document.activeElement);
  $('#workspace').classList.remove('rail-open');
  syncRailAccessibility();
  if (drawerQuery.matches && focusWasInRail) $('.show-customer')?.focus({preventScroll:true});
});
new MutationObserver(syncRailAccessibility).observe($('#workspace'), {attributes:true, attributeFilter:['class']});
// Account for wrapped navigation under text enlargement, not only viewport width.
new ResizeObserver(([entry]) => document.documentElement.style.setProperty('--header-height', `${entry.target.getBoundingClientRect().height}px`)).observe($('.app-header'));
let searchTimer;
$('#search').addEventListener('input',event=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{state.query=event.target.value;state.page=0;syncUrl();loadList();},250);});
document.addEventListener('input',event=>{if(event.target.id==='reply'){saveReply(event.target.value);$('#saved-note').textContent='Saved in this browser';}});
document.addEventListener('change',event=>{if(event.target.dataset.field)setField(event.target.dataset.field,event.target.value);});
document.addEventListener('click',async event=>{
  const ticketButton=event.target.closest('[data-ticket]');if(ticketButton){await selectTicket(ticketButton.dataset.ticket,true,true);return;}
  const view=event.target.closest('[data-view]');if(view){state.view=view.dataset.view;state.page=0;syncUrl();loadList();return;}
  const tab=event.target.closest('[data-tab]');if(tab){state.tab=tab.dataset.tab;renderTicket();$(`[data-tab="${state.tab}"]`)?.focus({preventScroll:true});return;}
  const action=event.target.closest('[data-action]')?.dataset.action;if(!action)return;
  if(action==='refresh'){loadList(true);refreshTicket();return;}
  if(action==='retry-customer'){refreshCustomerDetails(true);return;}
  if(action==='older-messages'){loadOlder();return;}
  if(action==='retry-ticket'){selectTicket(state.id,false);return;}
  if(action==='sort'){state.oldest=!state.oldest;state.page=0;loadList();return;}
  if(action==='page-prev'||action==='page-next'){state.page=Math.max(0,state.page+(action==='page-next'?1:-1));await loadList();$('#ticket-list').scrollTop=0;return;}
  if(action==='ticket-prev'||action==='ticket-next'){
    const index=state.rows.findIndex(t=>t.id===state.id),direction=action==='ticket-next'?1:-1;
    let next=state.rows[index+direction];
    if(!next&&index>=0&&(direction>0?state.hasNext:state.page>0)){state.page+=direction;await loadList();next=direction>0?state.rows[0]:state.rows.at(-1);}
    if(next)selectTicket(next.id,true,true);return;
  }
  if(action==='back'){$('#workspace').classList.remove('ticket-open','rail-open');$('#search').focus();return;}
  if(action==='rail'){railTrigger=event.target.closest('[data-action]');$('#workspace').classList.remove('rail-collapsed');$('#workspace').classList.add('rail-open');syncRailAccessibility();$('#customer-rail button')?.focus();return;}
  if(action==='rail-close'){closeRail();return;}
  if(action==='copy'){copyText(new URL('/inbox2/?ticket='+encodeURIComponent(state.id),location.origin).href,'Ticket link copied.');return;}
  if(action==='send-gate'){toast('Gorgias is connected read-only. You can prepare and copy replies here; send them from Gorgias.');return;}
  if(action==='copy-reply'){const value=$('#reply')?.value;if(value)copyText(value,'Reply copied.');else toast('Write a reply or use the suggested draft first.');return;}
  if(action==='use-draft'&&state.ticket&&draftAvailable(state.ticket)){const editor=$('#reply'),body=currentDraft(state.ticket);if(editor.value.trim()&&editor.value!==body){editor.value=editor.value.trimEnd()+'\n\n'+body;toast('Suggestion added below your existing reply.');}else editor.value=body;saveReply(editor.value);$('#saved-note').textContent='Saved in this browser';editor.focus();return;}
  if(action==='dismiss-draft'||action==='restore-draft'){const dismissed=objectStore(keys.dismiss);if(action==='dismiss-draft')dismissed[state.id]=draftId(state.ticket);else delete dismissed[state.id];persist(keys.dismiss,dismissed);renderTicket();return;}
  if(action==='new'){toast('This inbox reads Gorgias tickets. Create new tickets in Gorgias.');return;}
    if(action==='sign-out'){try {const response=await fetch('/console/api/auth/logout',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:'{}'});if(!response.ok)throw new Error();location.assign('/console/login?next=%2Finbox2%2F');}catch {toast('Sign out failed. Please try again.');}}
});
document.addEventListener('keydown', event => {
  const drawerOpen = drawerQuery.matches && $('#workspace').classList.contains('rail-open');
  if (event.key === 'Escape') {
    if (drawerOpen) closeRail();
    $('.profile-menu')?.removeAttribute('open');
  }
  if (event.key === 'Tab' && drawerOpen) {
    const controls = [...$('#customer-rail').querySelectorAll('button, a[href], summary, [tabindex="0"]')].filter(el => !el.disabled && el.getClientRects().length);
    const first = controls[0], last = controls.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
  }
  if (event.target.matches('[role="tab"]') && ['ArrowLeft','ArrowRight'].includes(event.key)) {
    event.preventDefault(); state.tab = state.tab === 'conversation' ? 'details' : 'conversation';
    renderTicket(); $(`[data-tab="${state.tab}"]`).focus();
  }
});
window.addEventListener('popstate',()=>{params=new URLSearchParams(location.search);state.query=params.get('q')||'';state.view=['all','open','closed'].includes(params.get('view'))?params.get('view'):'all';$('#search').value=state.query;const id=params.get('ticket');if(id)selectTicket(id,false);else{state.id='';state.ticket=null;$('#workspace').classList.remove('ticket-open');const first=filtered()[0];if(first)selectTicket(first.id,false);}state.page=0;loadList();});

api('capabilities').then(result=>{state.operator=result.operatorEmail||'';}).catch(()=>{});
if(state.id)selectTicket(state.id,false);
loadList();

let pollBusy=false;
async function pollLive(){if(document.hidden||pollBusy)return;pollBusy=true;try{await Promise.allSettled([loadList(true),refreshTicket()]);}finally{pollBusy=false;}}
setInterval(pollLive,30000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)pollLive();});
