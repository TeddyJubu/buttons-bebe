import { icons } from './icons.js';
import {localKeys,stateRecords,readRecords,lastMessage,readState,localTicket,matchesLocal} from './local_state.js';
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
  if (m?.amount == null || !Number.isFinite(Number(m.amount)) || typeof m.currencyCode!=='string' || !m.currencyCode.trim()) return 'Not available';
  try {return new Intl.NumberFormat('en-US',{style:'currency',currency:m.currencyCode,currencyDisplay:code?'code':'symbol'}).format(Number(m.amount));} catch {return `${m.amount} ${m.currencyCode}`;}
};
function deliveryTransition(currentAction,expectedOperationId,result,draft) {
  if(!expectedOperationId||result?.operation_id!==expectedOperationId||currentAction?.operationId!==expectedOperationId)return {matched:false,action:currentAction,clearDraft:false};
  const action={...currentAction,status:result.delivery_status||'unknown'};
  return {matched:true,action,clearDraft:action.status==='sent'&&Boolean(action.submittedDraftVersion)&&draft?.version===action.submittedDraftVersion};
}
function mergeMessages(retained,incoming) {
  const merged=new Map();
  for(const [index,message] of (retained||[]).entries())merged.set(message?.id==null?`retained:${index}`:`id:${message.id}`,message);
  for(const [index,message] of (incoming||[]).entries())merged.set(message?.id==null?`incoming:${index}`:`id:${message.id}`,message);
  return [...merged.values()].sort((left,right)=>{
    const a=Date.parse(left?.at||''),b=Date.parse(right?.at||''),time=(Number.isFinite(a)?a:0)-(Number.isFinite(b)?b:0);
    return time||String(left?.id??'').localeCompare(String(right?.id??''));
  });
}
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
const keys = {state:'bb-inbox-ticket-state-v1',local:'bb-inbox-local-tickets-v1',read:'bb-inbox-read-v1',drafts:'bb-inbox2-composer-v1',dismiss:'bb-inbox2-dismissed-v1',rewrites:'bb-inbox2-rewrites-v1'};
const memory = new Map(),failedStorageKeys=new Set();let storageAvailable=true;
function stored(key,fallback) {if(memory.has(key))return memory.get(key);try{const raw=localStorage.getItem(key),value=raw?JSON.parse(raw):fallback;memory.set(key,value);return value;}catch{return fallback;}}
function persist(key, value) {memory.set(key,value);try {localStorage.setItem(key,JSON.stringify(value));failedStorageKeys.delete(key);storageAvailable=!failedStorageKeys.size;return true;} catch {failedStorageKeys.add(key);storageAvailable=false;toast('Browser storage is unavailable. Changes will last for this session only.');return false;}}
function objectStore(key) {if((key===keys.drafts||key==='bb-inbox-send-actions-v1')&&!failedStorageKeys.has(key))memory.delete(key);const v=stored(key,{});return v && typeof v==='object'&&!Array.isArray(v)?v:{};}
function arrayStore(key) {const v=stored(key,[]);return Array.isArray(v)?v:[];}
let toastTimer;
function toast(message) {$('#toast').textContent=message;$('#toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').hidden=true,6000);}
let params = new URLSearchParams(location.search);
const state = {rows:[],ticket:null,id:params.get('ticket')||'',query:params.get('q')||'',view:['assigned','unassigned','all','open','snoozed','closed','trash','spam'].includes(params.get('view'))?params.get('view'):'all',page:0,size:9,total:0,hasNext:false,oldest:false,loading:true,error:'',projection:null,tab:'conversation',operator:'',filters:{priority:'',assignee:'',tag:'',channel:''},selected:new Set(),categoryAvailability:{},ticketRequest:0,listRequest:0,olderRequest:0};
$('#search').value=state.query;
// Manual send authority lives only in this page's memory. Never persist a grant.
const sendAccess={token:'',expiresAt:0,busy:false,timer:null};
const sendState={review:null,busy:false,preparing:false,sequence:0};
const actionKey='bb-inbox-send-actions-v1';
function canSend(){return Boolean(sendAccess.token&&Date.now()<sendAccess.expiresAt*1000);}
function sendAction(id=state.id){return objectStore(actionKey)[id];}
function rememberAction(id,action){const actions=objectStore(actionKey);actions[id]=action;persist(actionKey,actions);}
function unresolved(action){return action&&['pending','unknown'].includes(action.status);}
function sendError(code){return ({inbox_read_only:'Read only. Switch on Gorgias replies to continue.',new_customer_message_refresh_ticket:'A newer customer message arrived. Refresh the ticket and review your reply again.',message_chronology_unavailable:'Message dates are incomplete. Staff must check this conversation before a reply can be sent.',draft_changed_refresh_ticket:'The suggested draft changed. Refresh the ticket and review again.',recipient_changed_refresh_ticket:'The recipient changed. Refresh the ticket and review again.',review_changed_refresh_ticket:'The conversation changed. Refresh the ticket and review again.',source_message_not_in_console:'This message is still syncing. Refresh the ticket and try again shortly.',previous_delivery_unresolved:'An earlier reply has an uncertain delivery status. Check its status before sending again.',review_context_unavailable:'Reply details are temporarily unavailable. Please try again.',remote_delivery_failed:'Gorgias reports delivery failed. Inspect the message in Gorgias.',reply_context_unavailable:'Reply routing is unavailable for this message.',confirmation_required:'Review your reply before confirming the send.'})[code]||'The reply could not be sent. Refresh the ticket and review again.';}
async function consoleRequest(path,options={}){
  const response=await fetch('/console/api'+path,{credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(65000),...options,headers:{'Content-Type':'application/json',...options.headers}});
  const body=await response.json().catch(()=>({}));
  if(response.status===401||response.redirected){resetSendAccess();const error=new Error('Your session has expired. Sign in again to continue.');error.auth=true;throw error;}
  if(!response.ok){const error=new Error(sendError(body.error));error.body=body;error.status=response.status;throw error;}
  return body;
}
function cancelSendReview(){sendState.sequence++;sendState.preparing=false;sendState.review=null;$('#send-review').close();}
function resetSendAccess(){clearTimeout(sendAccess.timer);sendAccess.token='';sendAccess.expiresAt=0;if(!sendState.busy)cancelSendReview();renderSendAccess();}
function renderSendAccess(){
  const enabled=canSend(),toggle=$('[data-action="toggle-send-access"]');
  toggle.setAttribute('aria-checked',String(enabled));toggle.disabled=sendAccess.busy||sendState.busy;
  toggle.setAttribute('aria-label',`Gorgias read and write access${enabled?' enabled':' disabled'}`);
  toggle.title=enabled?'Replies enabled for this page. Switch off to return to read only.':'Read only. Switch on to send replies from this page.';
  toggle.innerHTML=`<span class="access-label">Gorgias · ${enabled?'Read & write':'Read only'}</span><span class="access-switch" aria-hidden="true"><span></span></span>`;
  $('#window-label').textContent=`Gorgias · ${enabled?'Read & write':'Read only'}`;
  const send=$('[data-action="review-send"]'),copy=$('[data-action="copy-reply"]'),note=$('#send-mode-note'),status=$('#reply-delivery');
  if(send){send.hidden=!enabled||Boolean(state.ticket?.localOnly);send.disabled=sendState.busy||sendState.preparing||unresolved(sendAction());send.textContent=sendState.busy?'Sending…':sendState.preparing?'Preparing…':'Send reply';}
  if(copy)copy.classList.toggle('primary',!enabled);
  if(note)note.textContent=state.ticket?.localOnly?'Local ticket · Customer sending is unavailable.':enabled?'Replies enabled for this page · Review and confirm each send.':'Read only · Switch on Gorgias replies above to send from here.';
  if(status){const action=sendAction();status.innerHTML=action?`${esc(action.status==='sent'?'Reply sent via Gorgias.':action.status==='not_attempted'?'Reply not sent. Your draft is saved.':action.status==='failed'?'Gorgias reports delivery failed. Inspect it in Gorgias.':action.status==='pending'?'Gorgias accepted the reply; delivery is pending.':'Delivery is unconfirmed. Check status before sending again.')} ${unresolved(action)?'<button class="button" data-action="check-send-status">Check status</button>':''}`:'';status.hidden=!action;}
  const editor=$('#reply');if(editor)editor.disabled=sendState.busy;
}
async function toggleSendAccess(){
  if(sendAccess.busy||sendState.busy)return;
  const enabled=!canSend(),token=sendAccess.token;
  if(!enabled)resetSendAccess();
  sendAccess.busy=true;renderSendAccess();
  try{
    const result=await consoleRequest('/inbox/send-access',{method:'POST',headers:token?{'X-Inbox-Send-Access':token}:{},body:JSON.stringify({enabled})});
    if(enabled){
      if(!result.enabled||!result.token||!(result.expiresAt*1000>Date.now()))throw new Error('Send access could not be enabled. Please try again.');
      sendAccess.token=result.token;sendAccess.expiresAt=result.expiresAt;
      sendAccess.timer=setTimeout(()=>{resetSendAccess();toast('Gorgias has returned to read only. Switch on again when you need to reply.');},Math.max(0,result.expiresAt*1000-Date.now()));
    }
    toast(enabled?'Read & write enabled for this page. Each reply requires confirmation.':'Gorgias is read only.');
  }catch(error){resetSendAccess();toast(enabled?error.message:'This page is read only. Server revocation could not be confirmed; the previous access expires automatically.');}
  finally{sendAccess.busy=false;renderSendAccess();}
}
async function reviewSend(){
  if(state.ticket?.localOnly||!canSend()||sendState.busy||sendState.preparing||unresolved(sendAction()))return;
  const ticket=state.ticket,editor=$('#reply'),value=editor?.value||'',text=value.trim();
  if(!text){toast('Write a reply or use the suggested draft first.');return;}
  const source=[...(ticket?.messages||[])].reverse().find(m=>m.fromAgent===false);
  if(!/^gorgias:[1-9][0-9]{0,17}$/.test(ticket?.id||'')||!source?.id||ticket.syncStale){toast('Refresh this ticket to load the latest customer message before sending.');return;}
  const id=ticket.id,draftVersion=captureDraftRevision(id,value).version,sequence=++sendState.sequence,token=sendAccess.token;
  sendState.preparing=true;renderSendAccess();
  try{
    const query=new URLSearchParams({source_message_id:String(source.id),expected_recipient:ticket.fromEmail||''});
    const {context}=await consoleRequest(`/inbox/review-context/${encodeURIComponent(id)}?${query}`);
    if(sequence!==sendState.sequence||id!==state.id||token!==sendAccess.token||!canSend())return;
    if(!context||context.inboxTicketId!==id||context.sourceMessageId!==String(source.id)||!context.recipient||!context.channel||context.sourceMessageTruncated)throw new Error('Reply details are not available for this message. Refresh the ticket and try again.');
    const pending=context.unresolvedActions?.find(x=>x.kind==='send');
    if(pending){if(pending.operationId)rememberAction(id,{operationId:pending.operationId,status:'unknown'});throw new Error('An earlier reply is unresolved. Check its status before sending again.');}
    sendState.review={id,text,draftVersion,context,token};
    $('#send-review-recipient').textContent=`To ${context.recipient} · ${label(context.channel)} · Ticket #${context.ticketId}`;
    $('#send-review-source').textContent=context.sourceMessageText;
    $('#send-review-text').textContent=text;
    $('#send-review').showModal();$('#cancel-send').focus();
  }catch(error){toast(error.message);}
  finally{if(sequence===sendState.sequence){sendState.preparing=false;renderSendAccess();}}
}
function applyDelivery(id,expectedOperationId,result){
  const actions=objectStore(actionKey),transition=deliveryTransition(actions[id],expectedOperationId,result,objectStore(keys.drafts)[id]);
  if(!transition.matched)return false;
  actions[id]=transition.action;persist(actionKey,actions);
  if(transition.action.status==='sent'){
    const drafts=objectStore(keys.drafts);
    if(transition.clearDraft){delete drafts[id];persist(keys.drafts,drafts);if(state.id===id&&$('#reply')){$('#reply').value='';sizeReplyEditor($('#reply'));}}
    toast('Reply sent via Gorgias.');if(state.id===id)refreshTicket();
  }
  renderSendAccess();
  return true;
}
async function confirmSend(){
  const review=sendState.review;
  if(!review||sendState.busy||!canSend()||review.token!==sendAccess.token||state.id!==review.id)return;
  sendState.busy=true;sendState.review=null;$('#send-review').close();
  const operation=crypto.randomUUID(),context=review.context;
  rememberAction(review.id,{operationId:operation,status:'unknown',submittedDraftVersion:review.draftVersion});renderSendAccess();
  try{
    const result=await consoleRequest(`/inbox/ticket/${context.ticketId}/send`,{method:'POST',headers:{'X-Inbox-Send-Access':review.token},body:JSON.stringify({text:review.text,confirmed:true,operation_id:operation,source_message_id:context.sourceMessageId,draft_revision:context.draftRevision,expected_recipient:context.recipient,context_id:context.contextId,approve_learning:false})});
    if(!applyDelivery(review.id,operation,result))toast('Delivery is unconfirmed. Check status before sending again.');
  }catch(error){
    const applied=error.body?.delivery_status&&applyDelivery(review.id,operation,error.body);
    if(error.body?.error==='inbox_read_only')resetSendAccess();
    toast(applied&&error.body.delivery_status==='not_attempted'?error.message:'Delivery is unconfirmed. Check status before sending again.');
  }finally{sendState.busy=false;renderSendAccess();}
}
async function checkSendStatus(){
  const id=state.id,action=sendAction(id);if(state.ticket?.localOnly||!action?.operationId||!/^gorgias:[1-9][0-9]{0,17}$/.test(id))return;
  const button=$('[data-action="check-send-status"]');if(button)button.disabled=true;
  try{const result=await consoleRequest(`/ticket/${id.slice(8)}/actions/${encodeURIComponent(action.operationId)}`);if(!applyDelivery(id,action.operationId,result))toast('Delivery status is unavailable. Keep this draft and check Gorgias before trying another send.');}
  catch(error){if(!error.body?.delivery_status||!applyDelivery(id,action.operationId,error.body))toast('Delivery status is unavailable. Keep this draft and check Gorgias before trying another send.');}
  finally{if(button)button.disabled=false;}
}
$('#send-review').addEventListener('cancel',()=>{sendState.review=null;});
window.addEventListener('pagehide',()=>{closeRewrite();resetSendAccess();});

async function api(tool, args={}) {
  const response = await fetch('/inbox/api/helpdesk',{method:'POST',credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json'},body:JSON.stringify({tool:`helpdesk.${tool}`,arguments:args}),signal:AbortSignal.timeout(65000)});
  if (response.status===401 || response.redirected) {const error=new Error('Your session has expired. Sign in to continue.');error.auth=true;throw error;}
  if (!response.ok) {const body=await response.json().catch(()=>({}));const error=new Error(body.message||'Ticket data could not be loaded. Please try again.');error.gone=response.status===404;throw error;}
  const result=await response.json();
  if (!result.ok) throw new Error(result.message || 'The requested ticket is unavailable.');
  if (result.source && result.source!=='gorgias_api') throw new Error('Live ticket data is unavailable.');
  return result;
}
function localRows() {return arrayStore(keys.local).filter(t=>t?.localOnly&&/^local:[\w-]+$/.test(t.id));}
function localValue(ticket,field) {return stateRecords(stored(keys.state,{}))[ticket.id]?.[field]?.value ?? '';}
function observed(ticket, field) {
  if(field==='priority') return ticket.gorgiasPriority || '';
  if(field==='snooze') return ticket.snoozeUntil || ticket.snoozedUntil || '';
  if(field==='assignee') return ticket.assigneeEmail || (typeof ticket.assignee==='string'?ticket.assignee:ticket.assignee?.email || ticket.assignee?.name) || '';
  return ticket[field] || '';
}
function effective(ticket, field) {return localValue(ticket,field)||observed(ticket,field);}
function matchingLocalRows(){return (state.oldest?localRows().slice().reverse():localRows()).filter(t=>matchesLocal(t,state.view,{...state.filters,query:state.query},effective,state.operator));}
function allRows() {return [...matchingLocalRows().slice(state.page*state.size,(state.page+1)*state.size),...state.rows];}
function filtered() {return allRows().filter(t=>!t.localOnly&&!stateRecords(stored(keys.state,{}))[t.id]||matchesLocal(t,state.view,{...state.filters,query:t.localOnly?state.query:''},effective,state.operator));}
function markRead(ticket,read=true) {const records=readRecords(stored(keys.read,{}));records[ticket.id]={read,message:lastMessage(ticket),activity:ticket.lastMessageAt||ticket.messages?.at(-1)?.at||ticket.updatedAt,at:Date.now()};persist(keys.read,{version:1,records});}
function clearSelection(){state.selected.clear();}
function updateSelection(){const visible=new Set(filtered().map(t=>t.id));for(const id of state.selected)if(!visible.has(id))state.selected.delete(id);$('#selection-count').textContent=`${state.selected.size} selected on this page`;$('#select-page').checked=visible.size>0&&state.selected.size===visible.size;$('#select-page').indeterminate=state.selected.size>0&&state.selected.size<visible.size;$('[data-action="bulk-apply"]').disabled=!state.selected.size;}
function syncUrl(push=false) {
  const p=new URLSearchParams();if(state.id)p.set('ticket',state.id);if(state.view!=='all')p.set('view',state.view);if(state.query)p.set('q',state.query);
  const url='/inbox/'+(p.size?'?'+p.toString():'');
  if(location.pathname+location.search!==url) history[push?'pushState':'replaceState']({},'',url);
}
function rowTitle(t) {const selected=state.ticket;return ticketTitle(selected?.id===t.id&&String(selected.fromEmail||'').toLowerCase()===String(t.fromEmail||'').toLowerCase()?selected:t);}
function age(value) {const days=Math.max(0,Math.floor((Date.now()-new Date(value))/86400000));return Number.isFinite(days)?days===0?'Today':`${days}d`:'';}
function listRender() {
  const rows=filtered();
  const offset=state.page*state.size,page=rows;
  $('#ticket-list').innerHTML=page.map(t=>`<div class="ticket-row-wrap"><input type="checkbox" class="row-select" data-select-ticket="${esc(t.id)}" aria-label="Select ${esc(rowTitle(t))}" ${state.selected.has(t.id)?'checked':''}><button class="ticket-row ${readState(t,readRecords(stored(keys.read,{})))?'is-read':'is-unread'}" data-ticket="${esc(t.id)}" ${t.id===state.id?'aria-current="true"':''}><div class="row-top"><span class="row-name">${esc(t.customerName||'Unknown sender')}</span><span class="age">${esc(age(t.updatedAt))}</span></div><div class="row-subject">${esc(rowTitle(t))}</div><div class="row-snippet">${esc(t.snippet)||'No message preview'}</div><div class="row-state">${t.localOnly?'Local ticket · ':''}${readState(t,readRecords(stored(keys.read,{})))?'Read':'Unread'}${effective(t,'status')==='closed'?' · Closed':''}${localValue(t,'snooze')?' · Snoozed locally':''}</div></button></div>`).join('')||`<div class="empty-state${state.error?' is-error':''}">${esc(state.loading?'Loading tickets…':state.error||'No tickets match this view.')}${state.error?'<br><button class="button" data-action="refresh">Try again</button>':''}</div>`;
  updateSelection();
  $('#local-scope').hidden=!localRows().length&&!Object.keys(stateRecords(stored(keys.state,{}))).length;
  for(const button of document.querySelectorAll('[data-view]')) {const available=button.dataset.view==='assigned'&&!state.operator?false:state.categoryAvailability[button.dataset.view];button.disabled=available===false||available?.available===false;button.title=button.disabled?(button.dataset.view==='assigned'&&!state.operator?'Assigned to me is unavailable because the operator email is not configured.':available?.reason||'Gorgias has not supplied the fields needed for this view.'):`Show ${button.textContent.trim()} tickets`;}
  const providerShown=rows.filter(t=>!t.localOnly).length,localShown=rows.filter(t=>t.localOnly).length;
  const count=state.loading&&!rows.length?'Loading tickets…':`Page ${state.page+1} · ${providerShown} of ${state.rows.length} loaded Gorgias shown · ${state.total.toLocaleString()} total`;
  $('#count').textContent=`${count}${localRows().length?' · '+localShown+' local shown of '+matchingLocalRows().length+' matching in browser':''}`;
  $('[data-action="page-prev"]').disabled=state.page===0;
  $('[data-action="page-next"]').disabled=!state.hasNext;
  for(const button of document.querySelectorAll('[data-view]'))button.setAttribute('aria-pressed',String(button.dataset.view===state.view));
  $('#sort-button').innerHTML=`${state.oldest?'Oldest':'Newest'} first ${icon('sort')}`;
  $('#window-label').textContent=`Gorgias · ${canSend()?'Read & write':'Read only'}`;
  $('#refresh span').textContent=state.error||state.projection?.stale?'Sync delayed · Retry':state.projection&&!state.projection.complete?'Syncing ticket history…':state.projection?.generatedAt?`Synced ${date(state.projection.generatedAt)}`:'Connecting to Gorgias…';
  $('#sync-status').classList.toggle('sync-delayed', Boolean(state.error || state.projection?.stale));
  $('#sync-status').textContent=state.projection?.stale?'Gorgias refresh is delayed. Showing the last successful sync.':state.projection&&!state.projection.complete?'Importing ticket history. Search and counts will expand as tickets arrive.':'Refreshes automatically every 30 seconds.';
  if(state.ticket) updateTicketNavigation();
}
function updateTicketNavigation(){const rows=filtered();const index=rows.findIndex(t=>t.id===state.id);const prev=$('[data-action="ticket-prev"]'),next=$('[data-action="ticket-next"]');if(prev)prev.disabled=index<0||index===0&&state.page===0;if(next)next.disabled=index<0||index===rows.length-1&&!state.hasNext;}
async function loadList(background=false){
  if(background&&state.loading)return;
  const request=++state.listRequest;state.loading=true;state.error='';if(!background)listRender();
  try{
    const result=await api('list_tickets',{view:state.view,query:state.query,...state.filters,oldest:state.oldest,limit:state.size,offset:state.page*state.size});
    if(request!==state.listRequest)return;
    state.rows=result.tickets;state.categoryAvailability=result.categoryAvailability||{};if(result.operatorEmail)state.operator=result.operatorEmail;state.total=result.total;state.hasNext=result.nextOffset!=null||matchingLocalRows().length>(state.page+1)*state.size;state.projection=result.projection;state.loading=false;
    if(state.page>0&&!state.rows.length&&!allRows().length){state.page=Math.max(0,Math.ceil(Math.max(state.total,matchingLocalRows().length)/state.size)-1);return loadList();}
    listRender();
    if(!state.id){const first=filtered()[0];if(first)selectTicket(first.id,false);else $('#conversation').innerHTML='<div class="empty-state">'+(state.projection?.complete?'No tickets in this view.':'Connecting to Gorgias. Tickets will appear as they sync.')+'</div>';}
  }catch(error){if(request!==state.listRequest)return;state.loading=false;state.error=error.message;listRender();if(error.auth)showAuth();else if(!background)toast(error.message);}
}
let refreshingTicket=false;
async function refreshTicket(){
  if(state.ticket?.localOnly||!state.ticket||refreshingTicket)return;
  const id=state.id,request=state.ticketRequest;refreshingTicket=true;
  try{
    const result=await api('get_ticket',{ticketId:id});if(id!==state.id||request!==state.ticketRequest)return;
    const fresh=result.ticket;
    if(state.olderLoaded){
      fresh.messages=mergeMessages(state.ticket.messages,fresh.messages);
      fresh.messagesNextCursor=state.ticket.messagesNextCursor;
      fresh.historyIncomplete=Boolean(fresh.messagesNextCursor);
    }
    const focusedAction=document.activeElement?.dataset?.action;
    state.ticket=fresh;const row=state.rows.find(t=>t.id===id);if(row){row.lastMessageAt=fresh.lastMessageAt||fresh.messages?.at(-1)?.at||fresh.updatedAt;row.lastMessageId=lastMessage(fresh);}renderTicket();renderRail();listRender();
    if(focusedAction)document.querySelector(`[data-action="${focusedAction}"]`)?.focus({preventScroll:true});
  }catch(error){if(id!==state.id||request!==state.ticketRequest)return;if(error.auth)showAuth();else if(error.gone){state.ticket=null;$('#conversation').innerHTML='<div class="empty-state">This ticket is no longer available in Gorgias.</div>';$('#customer-rail').innerHTML='';}else{const status=$('#live-ticket-sync');if(status)status.textContent='Refresh delayed · Showing the last successful read';}}
  finally{refreshingTicket=false;}
}
async function loadOlder(){
  const cursor=state.ticket?.messagesNextCursor;if(state.ticket?.localOnly||!cursor)return;
  const request={sequence:++state.olderRequest,ticketId:state.id,ticketRequest:state.ticketRequest,cursor};
  const button=$('[data-action="older-messages"]');if(button)button.disabled=true;
  try{const result=await api('get_messages',{ticketId:request.ticketId,cursor});
    const current=state.ticket;
    if(request.sequence!==state.olderRequest||request.ticketId!==state.id||request.ticketRequest!==state.ticketRequest||current?.messagesNextCursor!==request.cursor)return;
    current.messages=mergeMessages(result.messages,current.messages);
    current.messagesNextCursor=result.nextCursor;current.historyIncomplete=Boolean(result.nextCursor);current.observedMessageCount=current.messages.length;state.olderLoaded=true;
    renderTicket();
  }catch(error){if(request.sequence===state.olderRequest&&request.ticketId===state.id&&request.ticketRequest===state.ticketRequest){toast(error.message);if(button)button.disabled=false;}}
}
function showAuth() {closeRewrite();resetSendAccess();const href='/console/login?next='+encodeURIComponent(location.pathname+location.search);$('#conversation').innerHTML=`<div class="empty-state"><h2>Sign in to continue</h2><p>Your support session has expired.</p><a class="button primary" href="${esc(href)}">Sign in</a></div>`;}
function currentDraft(t) {const revised=objectStore(keys.rewrites)[t.id];const body=plain(!t.draftSuperseded&&revised?.source===draftId(t)?revised.body:t.readonlyDraft).replace(/^\[SENSITIVE\s*[—–-]\s*REVIEW CAREFULLY BEFORE SENDING\]\s*/i,'').trim();return body.includes('\n')?body:body.replace(/([.!?])\s+(?=(?:Because|Since|However|Please note)\b)/g,'$1\n\n');}
function draftId(t) {return `${t.draftSourceMessageId||''}:${t.draftProcessedAt||''}:${t.readonlyDraft||''}`;}
function draftAvailable(t) {return Boolean(t.readonlyDraft&&!t.draftSuperseded&&!['failed','retry_wait','queued','superseded','no_reply'].includes(t.draftGenerationState)&&objectStore(keys.dismiss)[t.id]!==draftId(t));}
function draftNeedsStaff(t){return t.draftGenerationState==='needs_review'||t.draftReviewRequired===true;}
const draftRetries=new Map();
function draftStatusHtml(t){
  if(t.draftSuperseded)return '';
  const pending=draftRetries.get(t.id)?.pending||['queued','retry_wait'].includes(t.draftGenerationState);
  if(t.draftGenerationState==='failed'||pending)return `<section class="draft-card" aria-label="AI draft unavailable"><div class="draft-heading">${icon('info')}<h3>${pending?'AI retry queued':'AI draft unavailable'}</h3></div><p>${pending?'A new suggestion will appear here when ready. You can continue writing your reply.':'AI could not produce a usable reply. Retry or write your reply below.'}</p>${t.draftNextRetryAt?`<p class="small muted">Next attempt: ${esc(date(t.draftNextRetryAt))}</p>`:''}<button class="button" data-action="retry-draft" ${pending||t.syncStale||!t.draftRevision?'disabled':''}>${icon('refresh')} Retry AI draft</button></section>`;
  if(t.draftGenerationState==='no_reply')return '<p class="small muted">No new question to answer. You can still write a reply below.</p>';
  if(draftNeedsStaff(t))return `<div class="info-banner" role="status">${icon('info')}<span><strong>Needs staff input</strong><br>${esc(t.draftStaffNextStep||'Check the missing answer and write the completed reply below.')}</span></div>`;
  return '';
}
async function retryDraft(){
  const t=state.ticket;
  if(!t||t.localOnly||t.syncStale||t.draftSuperseded||t.draftGenerationState!=='failed'||!t.draftRevision||draftRetries.get(t.id)?.pending)return;
  const context={id:t.id,source:t.draftSourceMessageId,revision:t.draftRevision,processedAt:t.draftProcessedAt};
  const prior=draftRetries.get(t.id);
  const operation=prior?.source===context.source&&prior?.revision===context.revision?prior.operation:crypto.randomUUID();
  draftRetries.set(t.id,{...context,operation,pending:true});renderTicket();
  try{
    const response=await consoleRequest(`/ticket/${t.id.slice(8)}/retry-draft`,{method:'POST',body:JSON.stringify({operation_id:operation,source_message_id:context.source,draft_revision:context.revision})});
    if(response.ok!==true)throw new Error(response.error||'retry_failed');
    toast('AI retry queued. Your reply stays unchanged.');
  }catch(error){
    draftRetries.set(context.id,{...context,operation,pending:false});
    toast(['new_customer_message_refresh_ticket','draft_changed_refresh_ticket','human_action_already_initiated','failed_draft_required'].includes(error.message)?'This ticket changed. Refresh it before retrying.':'Retry could not be confirmed. Try again using the same request.');
  }
  if(state.id===context.id)renderTicket();
}
// AI edits are browser-local candidates, tied to the exact projected suggestion.
let rewriteState=null;
function closeRewrite(){
  rewriteState?.controller?.abort();rewriteState=null;
  $('#draft-rewrite').close();
  $('[data-action="edit-draft"]')?.focus({preventScroll:true});
}
function openRewrite(){
  const t=state.ticket;
  if(!t||!draftAvailable(t)||!t.draftSourceMessageId||t.syncStale||!/^gorgias:[1-9][0-9]{0,17}$/.test(t.id))return;
  rewriteState={id:t.id,source:draftId(t),sourceMessageId:String(t.draftSourceMessageId),draft:currentDraft(t),request:state.ticketRequest,busy:false};
  $('#rewrite-current').textContent=rewriteState.draft;
  $('#rewrite-instruction').value='';$('#rewrite-instruction').disabled=false;
  $('#rewrite-error').hidden=true;$('#rewrite-status').textContent='';
  $('#rewrite-submit').disabled=true;$('#rewrite-submit').textContent='Update suggestion';
  $('#draft-rewrite').showModal();$('#rewrite-instruction').focus();
}
function rewriteStillCurrent(context){
  const t=state.ticket;
  return rewriteState===context&&state.ticketRequest===context.request&&t?.id===context.id&&!t.syncStale&&draftAvailable(t)&&draftId(t)===context.source&&currentDraft(t)===context.draft;
}
function rewriteError(error){
  if(error.auth)return error.message;
  const code=error.body?.error;
  if(['source_message_not_in_console','ticket_not_in_console'].includes(code))return 'This message is still syncing. Refresh the ticket and try again shortly.';
  if(code==='rewrite_busy_try_later')return 'AI is working on another edit. Please try again shortly.';
  if(code==='rewrite_timed_out'||error.name==='TimeoutError')return 'AI took too long to respond. Your suggestion is unchanged. Please try again.';
  if(code==='rewrite_input_too_large')return 'The reply or instructions are too long. Shorten them and try again.';
  return 'AI could not update the suggestion. Your reply is unchanged. Please try again.';
}
async function submitRewrite(){
  const context=rewriteState,instruction=$('#rewrite-instruction').value.trim();
  if(!context||context.busy||!instruction)return;
  const showError=message=>{$('#rewrite-error').textContent=message;$('#rewrite-error').hidden=false;};
  const staleMessage='This ticket or suggestion changed. Close this window and review the latest suggestion before editing again.';
  if(!rewriteStillCurrent(context)){showError(staleMessage);return;}
  context.busy=true;context.controller=new AbortController();
  $('#rewrite-instruction').disabled=true;$('#rewrite-submit').disabled=true;
  $('#rewrite-submit').textContent='Updating…';$('#rewrite-error').hidden=true;
  $('#rewrite-status').textContent='AI is updating the suggestion…';
  try{
    const result=await consoleRequest(`/ticket/${context.id.slice(8)}/rewrite`,{method:'POST',signal:AbortSignal.any([context.controller.signal,AbortSignal.timeout(165000)]),body:JSON.stringify({draft:context.draft,instruction,source_message_id:context.sourceMessageId})});
    if(rewriteState!==context)return;
    if(!rewriteStillCurrent(context)){showError(staleMessage);return;}
    if(result.ok!==true||typeof result.draft!=='string'||!result.draft.trim()||result.draft.length>50000)throw new Error('invalid_rewrite');
    const revised=objectStore(keys.rewrites);revised[context.id]={source:context.source,body:result.draft.trim()};persist(keys.rewrites,revised);
    closeRewrite();renderTicket();
    $('[data-action="edit-draft"]')?.focus({preventScroll:true});
    toast('Suggestion updated. Review it, then choose Use draft.');
  }catch(error){if(rewriteState===context)showError(rewriteError(error));}
  finally{
    if(rewriteState===context){context.busy=false;$('#rewrite-instruction').disabled=false;$('#rewrite-submit').disabled=!$('#rewrite-instruction').value.trim();$('#rewrite-submit').textContent='Update suggestion';$('#rewrite-status').textContent='';}
  }
}
$('#draft-rewrite').addEventListener('cancel',event=>{event.preventDefault();closeRewrite();});
$('#rewrite-form').addEventListener('submit',event=>{event.preventDefault();submitRewrite();});
$('#rewrite-instruction').addEventListener('input',()=>{$('#rewrite-submit').disabled=Boolean(rewriteState?.busy)||!$('#rewrite-instruction').value.trim();});

async function selectTicket(id,push=true,focus=false) {
  if(!id)return;
  closeRewrite();
  if(!sendState.busy)cancelSendReview();
  const request=++state.ticketRequest;state.olderRequest++;state.id=id;state.ticket=null;state.olderLoaded=false;state.tab='conversation';syncUrl(push);listRender();
  $('#workspace').classList.add('ticket-open');$('#workspace').classList.remove('rail-open');
  $('#conversation').innerHTML='<div class="empty-state">Loading conversation…</div>';
  $('#customer-rail').innerHTML='<div class="rail-heading">Customer details</div><div class="empty-state">Loading customer details…</div>';
  try {
    const local=localRows().find(t=>t.id===id);
    if(id.startsWith('local:')&&!local)throw new Error('This local ticket is unavailable in this browser.');
    const result=local?{ticket:local}:await api('get_ticket',{ticketId:id});
    if(request!==state.ticketRequest)return;
    state.ticket=result.ticket;markRead(state.ticket);
    renderTicket();renderRail();listRender();
    document.title=`${ticketTitle(state.ticket)} · Buttons Bebe Support`;
    if(focus)$('#conversation').focus({preventScroll:true});
  } catch(error) {
    if(request!==state.ticketRequest)return;
    $('#customer-rail').innerHTML='<div class="rail-heading">Customer details</div><div class="empty-state">Customer details unavailable.</div>';
    if(error.auth)showAuth();else $('#conversation').innerHTML=`<div class="empty-state is-error"><h2>Couldn’t load this ticket</h2><p>${esc(error.message)}</p><button class="button" data-action="back">Back to tickets</button> <button class="button primary" data-action="retry-ticket">Try again</button></div>`;
  }
}
function ticketTitle(t) {const edited=localValue(t,'title');if(edited)return edited;const order=t.shopifyRail?.order;return order?.name||order?.number?`Order ${String(order.name||order.number).replace(/^#/,'')}`:'New Ticket';}
function options(values,selected) {return values.map(([v,text])=>`<option value="${esc(v)}"${v===selected?' selected':''}>${esc(text)}</option>`).join('');}
function control(t,field,values,iconName,prefix='') {
  const value=effective(t,field);
  return `<label class="control ${field==='status'?'status-control '+statusTone(value):field==='priority'?statusTone(value):''}">${icon(iconName)}<span class="sr-only">${esc(prefix)} in this browser</span><select data-field="${field}" aria-label="${esc(prefix)} in this browser">${options([['','Observed · '+(observed(t,field)||'not set')],...values],localValue(t,field))}</select></label>`;
}
function htmlElement(markup) {return document.createRange().createContextualFragment(markup).firstElementChild;}
function renderTicket() {
  const t=state.ticket;if(!t)return;
  const contentScroll=$('#ticket-content')?.scrollTop||0;
  const expandedDraft=$('.draft-card')?.classList.contains('draft-expanded');const actionsOpen=$('.ticket-actions-menu')?.open;
  const expandedQuotes=new Set([...document.querySelectorAll('.quoted-email[open]')].map(el=>el.dataset.messageId));
  const people=[...new Set([state.operator,...allRows().map(x=>observed(x,'assignee')),localValue(t,'assignee')].filter(Boolean))];
  const overrides=['title','status','priority','assignee','snooze'].filter(f=>localValue(t,f)).map(f=>`${label(f)}: ${localValue(t,f)} (observed: ${observed(t,f)||'unknown'})`);
  const header=`<header class="ticket-header"><button class="mobile-back" data-action="back">${icon('left')} All tickets</button><div class="title-row"><div><h2 class="ticket-title">${esc(ticketTitle(t))}</h2><button class="title-edit" data-action="rename" aria-label="Rename ticket in this browser">Rename locally</button><p class="ticket-subtitle">${esc(t.customerName||'Unknown sender')} · ${esc(t.localOnly?'Local ticket':'#'+t.id.replace(/^gorgias:/,''))} · ${esc(label(t.channel)||'Channel unknown')}</p></div><div class="ticket-navigation"><button class="icon-button" data-action="ticket-prev" aria-label="Previous ticket">${icon('left')}</button><button class="icon-button" data-action="ticket-next" aria-label="Next ticket">${icon('right')}</button><button class="icon-button show-customer" data-action="rail" aria-label="Show customer details" aria-controls="customer-rail" aria-expanded="false">${icon('user')}</button></div></div><details class="ticket-actions-menu"><summary aria-label="Ticket actions, local to this browser" title="Ticket actions saved only in this browser">${icon('more')} Ticket actions</summary><p class="small muted">These changes stay in this browser. Gorgias is unchanged.</p><div class="ticket-actions">${control(t,'status',[['open','Open · local'],['closed','Closed · local']],'','Status')}${control(t,'priority',[['low','Low · local'],['normal','Normal · local'],['high','High · local'],['critical','Critical · local']],'flag','Priority')}${control(t,'assignee',[['unassigned','Unassigned · local'],...people.filter(x=>x!=='unassigned').map(x=>[x,x+' · local'])],'user','Assignee')}<button class="button" data-action="toggle-read">${readState(t,readRecords(stored(keys.read,{})))?'Mark unread':'Mark read'}</button><label class="snooze-control">Snooze locally<input type="datetime-local" data-field="snooze" value="${esc(localValue(t,'snooze')?localValue(t,'snooze').slice(0,16):'')}"></label><button class="button" data-action="reset-local">Reset local changes</button><button class="button copy-link" data-action="copy">${icon('link')} Copy link</button></div></details>${overrides.length?`<div class="local-observed">Browser changes · ${esc(overrides.join(' · '))}</div>`:''}<p class="live-ticket-sync" id="live-ticket-sync">${t.localOnly?'Local ticket · Saved only in this browser':t.syncStale?'Refresh delayed · Showing the last successful read':'Read from Gorgias · '+esc(date(t.syncedAt))}</p><div class="ticket-tabs" role="tablist" aria-label="Ticket content"><button role="tab" id="conversation-tab" data-tab="conversation" aria-selected="${state.tab==='conversation'}" aria-controls="ticket-content">Conversation</button><button role="tab" id="details-tab" data-tab="details" aria-selected="${state.tab==='details'}" aria-controls="ticket-content">Ticket details</button></div></header>`;
  const content=`<section id="ticket-content" role="tabpanel" aria-labelledby="${state.tab==='conversation'?'conversation-tab':'details-tab'}">${state.tab==='conversation'?conversationHtml(t):detailsHtml(t)}</section>`;
  const conversation=$('#conversation'),sameTicket=conversation.dataset.ticketId===t.id&&Boolean($('.reply-area',conversation)?.isConnected);
  if(sameTicket){
    $('.ticket-header',conversation).replaceWith(htmlElement(header));
    $('#ticket-content',conversation).replaceWith(htmlElement(content));
    $('.reply-context',conversation).innerHTML=replyContextHtml(t);
    $('.composer .recipient',conversation).innerHTML=t.fromEmail?`to ${esc(t.fromEmail)}`:'Recipient not observed';
  }else{
    conversation.innerHTML=header+content+replyHtml(t);
    conversation.dataset.ticketId=t.id;
    const editor=$('#reply');if(editor){editor.value=objectStore(keys.drafts)[t.id]?.body??'';sizeReplyEditor(editor);if(editor.value)$('#saved-note').textContent=storageAvailable?'Saved in this browser':'Kept for this session only';}
  }
  for(const detail of document.querySelectorAll('.quoted-email'))if(expandedQuotes.has(detail.dataset.messageId))detail.open=true;
  updateTicketNavigation();
  syncRailAccessibility();
  if($('#ticket-content'))$('#ticket-content').scrollTop=contentScroll;
  if(expandedDraft){$('.draft-card')?.classList.add('draft-expanded');const expand=$('[data-action="expand-draft"]');if(expand){expand.textContent='Show less';expand.setAttribute('aria-expanded','true');}}if(actionsOpen)$('.ticket-actions-menu').open=true;
  document.title=`${ticketTitle(t)} · Buttons Bebe Support`;
  installTooltips();renderSendAccess();
}
function displayMessage(m){const n=m.normalized||m;const full=n.display_text??n.displayText??m.body??'',current=n.current_text??n.currentText;return {main:String(current&&current!==full?current:full),quoted:current&&current!==full?String(full):'',original:n.original_content??m.originalText??m.originalHtml??'',originalField:n.original_field||'',history:n.history_available,truncated:Boolean(n.source_truncated||m.truncated)};}
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
  const {main,quoted,original,originalField,history,truncated}=displayMessage(m);
  const name=(m.fromName&&m.fromName!==m.fromEmail?m.fromName:'')||(m.fromAgent?'Support':t.customerName)||m.fromName||'Unknown sender',email=m.fromEmail||(!m.fromAgent?t.fromEmail:'');
  return `<article class="message" data-message-id="${esc(m.id)}"><div class="message-heading"><span class="avatar">${esc(initials(name))}</span><div class="message-person"><strong>${esc(name)}${m.internal?' · Internal note':''}</strong>${email?`<span>${esc(email)}</span>`:''}</div><time datetime="${esc(m.at||'')}">${esc(date(m.at))}</time></div>${main?`<div class="message-body" dir="auto">${messageBodyHtml(main)}</div>`:quoted?'<p class="message-note">No new message text.</p>':'<p class="message-note">Message text is unavailable.</p>'}${quoted?`<details class="quoted-email" data-message-id="${esc(m.id)}"><summary>${icon('right')} Full email history</summary><div class="message-body" dir="auto">${messageBodyHtml(quoted)}</div></details>`:''}${original?`<details class="original-evidence"><summary>Original message evidence${originalField?' · '+esc(originalField):''}</summary><pre dir="auto">${esc(original)}</pre></details>`:'<p class="message-note">Original message evidence was not retained.</p>'}${history===false?'<p class="message-note">Full email history was not retained. Showing the available text.</p>':''}${truncated?'<p class="message-note">Only part of this message is available.</p>':''}${attachmentsHtml(m.attachments)}</article>`;
}
function attachmentsHtml(attachments){return (Array.isArray(attachments)?attachments:[]).slice(0,20).map(a=>{const url=webUrl(a.url||a.download_url||a.public_url);if(!url)return '';const name=String(a.name||a.filename||'Attachment'),image=/^image\/(?:jpeg|png|gif|webp|avif)$/.test(String(a.content_type||a.contentType||a.mime_type||''));return `<details class="message-attachment"><summary>${icon(image?'image':'link')}${esc(name)}</summary>${image?`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer"><img src="${esc(url)}" alt="${esc(name)}" loading="lazy" referrerpolicy="no-referrer"></a>`:`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">Open attachment ${icon('external')}</a>`}</details>`;}).join('');}
function conversationHtml(t) {
  return `<div class="message-area">${t.localOnly?`<div class="info-banner">${icon('info')} Local ticket · Saved in this browser. No customer has been contacted.</div>`:t.historyIncomplete?`<div class="info-banner">${icon('info')} Showing recent messages. Use “Load earlier messages” to read more.</div>`:''}${t.projection?.stale?`<div class="info-banner">${icon('info')} Ticket history is awaiting refresh. Last updated ${esc(date(t.projection.generatedAt))}.</div>`:''}${t.messagesNextCursor?'<button class="button older-messages" data-action="older-messages">Load earlier messages</button>':''}${t.messages?.length?t.messages.map(m=>messageHtml(m,t)).join(''):'<div class="empty-state">No messages returned by Gorgias.</div>'}</div>`;
}
function detailsHtml(t) {
  const fields=[['Ticket ID',t.id],['Original subject',t.subject||'Not observed'],['Subject',t.subject||'No subject'],['Customer',t.customerName||'Unknown'],['Email',t.fromEmail||'Not observed'],['Channel',label(t.channel)||'Not observed'],['Gorgias status',label(t.status)||'Not observed'],['Gorgias priority',label(t.gorgiasPriority)||'Not observed'],['Gorgias assignee',observed(t,'assignee')||'Not observed'],['Draft priority',label(t.priority)||'Not available'],['Last activity',date(t.updatedAt)],['Messages loaded',t.observedMessageCount??t.messages?.length??0],['Tags',(t.tags||[]).join(', ')||'None observed'],['Draft source',t.draftSourceMessageId||'Not available']];
  return `<div class="message-area"><dl class="ticket-fields">${fields.map(([k,v])=>`<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl></div>`;
}
function replyContextHtml(t) {
  const retry=draftRetries.get(t.id);
  if(retry&&(t.draftGenerationState!=='failed'||retry.source!==t.draftSourceMessageId||retry.processedAt!==t.draftProcessedAt))draftRetries.delete(t.id);
  const sensitive=t.draftAction==='sensitive_draft'||/^\[SENSITIVE/i.test(t.readonlyDraft||'');
  const draft=draftAvailable(t)?`<section class="draft-card" aria-label="Suggested reply"><div class="draft-heading">${icon('draft')}<h3>Suggested reply</h3><span class="badge amber">${icon('shield')}${sensitive?'Review required':'Review before sending'}</span></div>${sensitive?'<p class="compact-review">Review required before sending</p>':''}<div class="draft-body" dir="auto">${esc(currentDraft(t))}</div><button class="draft-expand" data-action="expand-draft" aria-expanded="false">Expand suggestion</button>${t.draftReason?`<div class="draft-warning">${icon('info')}<span>${esc(t.draftReason)}</span></div>`:''}<div class="draft-actions"><button class="button primary" data-action="use-draft" ${draftNeedsStaff(t)?'disabled title="Complete the missing answer in the reply below"':''}>${icon('check')} Use draft</button><button class="button draft-edit" data-action="edit-draft" aria-label="Edit suggested reply with AI" aria-haspopup="dialog" aria-controls="draft-rewrite" title="${t.draftSourceMessageId&&!t.syncStale?'Edit suggested reply with AI':'Refresh this ticket before editing the suggestion'}" ${!t.draftSourceMessageId||t.syncStale?'disabled':''}>${icon('edit')}</button><button class="button" data-action="dismiss-draft">Dismiss</button>${t.draftSourceMessageId?`<span class="draft-source" title="${esc(date(t.draftProcessedAt))}">Source: ${esc(t.draftSourceMessageId)}</span>`:''}</div></section>`:t.draftSuperseded?`<div class="info-banner">${icon('info')} The conversation has newer messages. The previous suggestion is out of date.</div>`:t.readonlyDraft?'<p class="small muted">Suggestion dismissed. <button data-action="restore-draft">Restore suggestion</button></p>':'<p class="small muted">No suggested reply is available for this ticket yet.</p>';
  return `${draftStatusHtml(t)}${draft}`;
}
function replyHtml(t) {
  return `<section class="reply-area"><div class="reply-context">${replyContextHtml(t)}</div><div class="composer"><div class="composer-heading">${icon('reply')}<strong>Reply</strong><span class="recipient">${t.fromEmail?`to ${esc(t.fromEmail)}`:'Recipient not observed'}</span></div><textarea id="reply" rows="3" maxlength="30000" placeholder="Write your reply…" aria-label="Reply message"></textarea><div class="composer-toolbar"><span class="saved-note" id="saved-note">Draft stays in this browser</span><button class="button primary" data-action="copy-reply" title="Copy your reply">${icon('copy')} Copy reply</button><button class="button primary" data-action="review-send" hidden>Send reply</button></div><div id="reply-delivery" class="reply-delivery" role="status" hidden></div></div><p class="composer-note">${icon('shield')} <span id="send-mode-note">Read only · Switch on Gorgias replies above to send from here.</span></p></section>`;
}
function railCompletenessHtml(rail) {
  const partial=rail.partial,notices=[];
  if(rail.legacyMoneyUnverified)notices.push('Prices from this older snapshot are hidden because their Shopify observation was not recorded.');
  if(partial?.orderSearch===true)notices.push('More orders matched this reference in Shopify. This snapshot uses the first exact match.');
  if(partial?.orderItems===true)notices.push('More order items exist in Shopify. This snapshot shows the first 50.');
  if(partial?.returns===true)notices.push('More returns exist in Shopify. This snapshot shows the first 5.');
  if(partial?.history===true)notices.push('More past orders exist in Shopify. This snapshot shows the first 50.');
  const nested=(rail.returns?.returns?.nodes||[]).map(item=>item.itemsTruncated);
  const relevant=partial?[partial.orderSearch,partial.orderItems,partial.returns,partial.history,...nested]:[null];
  if(relevant.some(value=>value==null))notices.push('Shopify did not record whether every bounded list is complete for this snapshot.');
  return notices.map(text=>`<div class="customer-load-status completeness-note">${icon('info')}<span>${esc(text)}</span></div>`).join('');
}
function renderRail() {
  const t=state.ticket;if(!t)return;
  if(t.localOnly){$('#customer-rail').innerHTML='<div class="rail-heading">Local ticket</div><p class="rail-section">This private browser ticket has no provider customer lookup.</p>';syncRailAccessibility();return;}
  const r=t.shopifyRail||{},c=r.customer,o=r.order;
  const identity=t.customerContext?.status==='observed'&&!t.customerContext?.conflict?t.customerContext.identity||{}:{};
  const name=c?.displayName||identity.name||t.customerName||'Unknown customer';
  const email=c?.defaultEmailAddress?.emailAddress||identity.email||t.fromEmail||'Email not observed';
  let html=`<div class="rail-heading">Customer details<button data-action="rail-close" aria-label="Close customer details">${icon('panel')}</button></div><section class="rail-section"><div class="customer-top"><span class="avatar">${esc(initials(name))}</span><div><div class="customer-name">${esc(name)}</div><div class="customer-email">${esc(email)}</div></div></div>${c?`<dl class="customer-stats"><div><dt>Orders</dt><dd>${esc(c.numberOfOrders??'Unknown')}</dd></div><div><dt>Total spent</dt><dd>${esc(money(c.amountSpent))}</dd></div></dl>${c.createdAt?`<p class="customer-since">Customer since ${esc(date(c.createdAt,false))}</p>`:''}${c.tags?.length?`<p class="customer-tags">${icon('bag')}<span>${esc(c.tags.join(' · '))}</span></p>`:''}`:`<p class="small muted">${esc(t.customerContext?.conflict?'Conflicting customer details need review.':r.status==='missing'?'No matching Shopify customer was found.':r.status==='loading'?'Loading Shopify customer details…':r.status==='unavailable'?'A consistent customer email is needed to look up Shopify details.':r.status==='error'?'Shopify details could not be loaded. Please retry.':'Shopify details are not available yet.')}</p>`}</section>`;
  if(r.status==='loading'||r.refreshing)html+=`<div class="customer-load-status" role="status">${icon('refresh')}<span>${r.refreshing?'Refreshing Shopify details…':'Loading orders, returns, and customer history…'}</span></div>`;
  if(r.status==='error'||r.refreshError)html+=`<div class="customer-load-status customer-load-error" role="status"><span>Shopify lookup is temporarily unavailable. ${r.customer||r.order?'Showing the last saved details.':''}</span><button class="button" data-action="retry-customer">${icon('refresh')} Retry customer details</button></div>`;
  html+=redoHtml(t.redoDetails);
  if(c||o||r.returns)html+=railCompletenessHtml(r);
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
  updateTitle();scheduleCustomerDetails();
}
const redoFields=[['id','Return ID'],['status','Status'],['type','Type'],['created_at','Created'],['updated_at','Updated'],['order_name','Matched order'],['complete_with_no_action','Completed without action'],['refund_amount','Refund amount'],['store_credit_amount','Store credit'],['tracking_number','Tracking number'],['tracking_url','Tracking link']];
const redoNestedFields=[['id','ID'],['status','Status'],['type','Type'],['amount','Amount'],['refund','Refund'],['storeCredit','Store credit'],['trackingNumber','Tracking number'],['trackingUrl','Tracking link'],['itemCount','Item count'],['createdAt','Created'],['updatedAt','Updated'],['quantity','Quantity'],['name','Name'],['currency','Currency'],['currencyCode','Currency code'],['carrier','Carrier']];
const redoStructures=[['totals','Totals'],['refunds','Refunds'],['compensation_methods','Compensation methods'],['gift_cards','Gift cards'],['exchange','Exchange'],['items','Items'],['shipments','Shipments'],['tracking','Tracking']];
function redoValue(value,key,depth=0){
  if(value==null)return 'Not reported by Redo';
  if(typeof value==='string'){
    if(['tracking_url','trackingUrl'].includes(key)){const href=webUrl(value.slice(0,200));return href?`<a href="${esc(href)}" target="_blank" rel="noopener noreferrer">Open tracking ${icon('external')}</a>`:'A usable tracking link was not reported';}
    return esc(value.slice(0,200))||'Not reported by Redo';
  }
  if(typeof value==='boolean')return value?'Yes':'No';
  if(typeof value==='number')return Number.isFinite(value)?esc(value):'Not reported by Redo';
  if(depth>=3)return 'Further detail is outside the retained read limit';
  if(Array.isArray(value))return value.length?`<ol>${value.slice(0,10).map(v=>`<li>${redoValue(v,key,depth+1)}</li>`).join('')}</ol>`:'No entries in the retained observation';
  if(typeof value==='object'){
    const fields=redoNestedFields.filter(([field])=>Object.hasOwn(value,field));
    return fields.length?`<dl class="ticket-fields">${fields.map(([field,title])=>`<dt>${title}</dt><dd>${redoValue(value[field],field,depth+1)}</dd>`).join('')}</dl>`:'No reviewed fields were retained';
  }
  return 'Not reported by Redo';
}
function redoReturnHtml(r){
  return `<section class="redo-return"><dl class="ticket-fields">${redoFields.map(([key,title])=>`<dt>${title}</dt><dd>${redoValue(r[key],key)}</dd>`).join('')}</dl>${redoStructures.map(([key,title])=>`<details class="redo-structure" data-redo-field="${key}"><summary>${title} ${icon('right')}</summary>${Object.hasOwn(r,key)?`<div>${redoValue(r[key],key)}</div>`:'<p class="small muted">Not reported by Redo.</p>'}${Array.isArray(r.structuredFieldsUnavailable)&&r.structuredFieldsUnavailable.includes(key)?'<p class="small muted">This field had no usable reviewed detail in the retained read.</p>':''}</details>`).join('')}<p class="small muted">Retained Redo details are limited to 10 entries per list, 3 nested levels, and 200 characters per value.</p></section>`;
}
function redoHtml(details) {
  const d=details||{status:'unavailable'},stateText={pending:'Waiting for a Redo read for the verified Shopify orders.',empty:'No matching Redo return was observed.',unavailable:'Redo details are unavailable for this verified identity.',partial:'Partial Redo observation. Some results are missing or could not be matched. Verified matches remain below.',stale:'The last Redo observation is shown. Its refresh is delayed.',observed:'Redo returns matched to verified Shopify orders.'};
  const orderDigits=value=>String(value||'').trim().replace(/^#/,'');
  const incomplete=(Array.isArray(d.incomplete)?d.incomplete:[]).slice(0,3).map(orderDigits).filter(name=>/^\d{4,10}$/.test(name));
  let html=`<details class="rail-section redo-section"><summary>Redo returns ${icon('right')}</summary><p class="redo-state">${esc(stateText[d.status]||stateText.unavailable)}</p>${incomplete.length?`<p class="small muted">Incomplete orders · ${esc(incomplete.join(', '))}</p>`:''}${d.fetchedAt?`<p class="small muted">Redo observation · ${esc(date(d.fetchedAt))}</p>`:''}`;
  if(['observed','empty','stale','partial'].includes(d.status))for(const [name,order] of Object.entries(d.orders||{}).slice(0,3)) {
    const digits=orderDigits(name);if(!/^\d{4,10}$/.test(digits)||!order||!['observed','empty'].includes(order.status))continue;
    const sourceReturns=(Array.isArray(order.returns)?order.returns:[]).slice(0,10);
    const returns=sourceReturns.filter(r=>r&&typeof r==='object'&&!Array.isArray(r)&&(r.order_name==null||orderDigits(r.order_name)===digits));
    html+=`<div class="return-row"><strong>Order ${esc(name)}</strong>${order.observedAt?`<p class="small muted">Observed ${esc(date(order.observedAt))}</p>`:''}${order.refreshFailed?'<p class="small muted">Refresh delayed. Showing the previous observation for this order.</p>':''}${returns.map(redoReturnHtml).join('')}`;
    if(!returns.length)html+=order.status==='empty'?'<p>No matching return in this observation.</p>':'<p>No matched return detail is available in this retained observation.</p>';
    if(order.truncated)html+='<p class="small muted">Showing the first 10 matching returns.</p>';
    if(order.rejected||returns.length!==sourceReturns.length)html+='<p class="small muted">Some records lacked a matching order identity and were withheld.</p>';
    html+='</div>';
  }
  return html+'</details>';
}
function returnsHtml(returns,order) {
  const nodes=returns.returns?.nodes||[],open=nodes.filter(x=>x.status==='OPEN').length;
  return `<details class="rail-section"${nodes.length?' open':''}><summary>Shopify returns <span class="badge ${open?'amber':'neutral'}">${open?`${open} open`:`${nodes.length} on file`}</span>${icon('right')}</summary>${nodes.map(n=>`<div class="return-row"><div class="return-title"><span>${esc(n.name||`Order ${order?.name||''}`)}</span><span class="badge ${n.status==='OPEN'?'amber':'neutral'}">${esc(label(n.status)||'Unknown')}</span></div>${n.createdAt?`<p>${esc(date(n.createdAt))}</p>`:''}${n.items?.length?n.items.map(i=>`<p>${esc(i.title||'Returned item')}${i.quantity!=null?` · Qty ${esc(i.quantity)}`:''}${i.reason?` · ${esc(i.reason)}`:''}${i.note?` · ${esc(i.note)}`:''}</p>`).join(''):'<p>Return on file. Item details unavailable.</p>'}${n.itemsTruncated===true?'<p class="small completeness-warning">More returned items exist in Shopify. This snapshot shows the first 25.</p>':''}</div>`).join('')||'<p class="small muted">No returns in this snapshot.</p>'}</details>`;
}
function orderHtml(o) {
  const shipments=(o.fulfillments?.nodes||o.fulfillments||[]).map(f=>`<div class="shipment ${statusTone(f.displayStatus)}"><div class="shipment-title">${icon('box')}${esc(label(f.displayStatus)||'Shipment observed')}</div>${(f.trackingInfo||[]).map(tr=>`<div class="shipment-carrier"><span>${esc(tr.company||'Carrier unavailable')}</span>${webUrl(tr.url)?`<a href="${esc(webUrl(tr.url))}" target="_blank" rel="noopener noreferrer">Track ${icon('external')}</a>`:''}</div><div class="tracking-number">${esc(tr.number||'Tracking number unavailable')}</div>`).join('')||'<div class="small muted">Tracking details unavailable</div>'}${f.estimatedDeliveryAt?`<p class="small">Expected ${esc(date(f.estimatedDeliveryAt,false))}</p>`:''}</div>`).join('');
  const items=(o.lineItems?.nodes||[]).slice().reverse().map(i=>{const url=webUrl(i.image?.url);return `<li class="order-item">${url&&new URL(url).hostname==='cdn.shopify.com'?`<img class="product-image" src="${esc(url)}" alt="${esc(i.image.altText||i.title)}" loading="lazy" referrerpolicy="no-referrer">`:`<span class="product-image product-placeholder">${icon(/gift/i.test(i.title)?'gift':'bag')}</span>`}<div><div class="product-name">${esc(i.title||'Product unavailable')}</div><div class="product-meta">${i.variantTitle?`${esc(i.variantTitle)} · `:''}Qty ${esc(i.quantity??'Unknown')}${i.unfulfilledQuantity>0?` · ${esc(i.unfulfilledQuantity)} unfulfilled`:''}</div><div class="product-price">${esc(money(i.originalUnitPriceSet))}</div></div></li>`;}).join('');
  return `<section class="rail-section"><div class="order-title"><h3>Order ${esc(String(o.name||'').replace(/^#/,''))}</h3></div><p class="order-date">${esc(date(o.createdAt))}</p><div class="order-statuses">${o.displayFinancialStatus?`<span class="badge ${statusTone(o.displayFinancialStatus)}">${esc(label(o.displayFinancialStatus))}</span>`:''}${o.displayFulfillmentStatus?`<span class="badge ${statusTone(o.displayFulfillmentStatus)}">${esc(label(o.displayFulfillmentStatus))}</span>`:''}</div>${shipments}<ul class="order-items">${items}</ul><div class="order-total"><span>Total</span><strong>${esc(money(o.currentTotalPriceSet,true))}</strong></div><div class="payment-lock">${icon('lock')} Payments locked</div></section>`;
}
function addressHtml(title,a) {return `<div class="address-block"><h4>${esc(title)}</h4>${a?['name','address1','address2','city','province','zip','country'].map(k=>a[k]?`${esc(a[k])}<br>`:'').join(''):'Address not available'}</div>`;}
function captureDraftRevision(id,value) {
  const drafts=objectStore(keys.drafts),existing=drafts[id];
  if(existing?.body===value&&existing.version)return existing;
  drafts[id]={body:value,at:Date.now(),version:crypto.randomUUID()};persist(keys.drafts,drafts);
  return drafts[id];
}
function saveReply(value) {if(state.ticket)captureDraftRevision(state.id,value);}
function sizeReplyEditor(editor) {editor.style.height='auto';editor.style.height=`${Math.min(editor.scrollHeight,Math.max(64,innerHeight*0.18))}px`;}
async function copyText(value,message) {try {await navigator.clipboard.writeText(value);toast(message);}catch {toast('Copy is unavailable in this browser. Select the text and copy it manually.');}}
function setField(field,value) {if(!state.ticket)return;if(field==='snooze'&&value){const d=new Date(value);if(!Number.isFinite(+d)||+d<=Date.now()){toast('Choose a future snooze time.');return;}value=d.toISOString();}const records=stateRecords(stored(keys.state,{}));records[state.id]={...(records[state.id]||{}),[field]:value?{value,by:state.operator||'operator',at:Date.now()}:null};persist(keys.state,{version:1,records});renderTicket();listRender();toast(storageAvailable?'Saved in this browser. Observed Gorgias values are unchanged.':'Browser storage is unavailable. Changes last for this session only.');}
// Refresh only the context rail while a background lookup runs; never touch the editor.
let customerDetailsTimer;
let customerDetailsRequest=0;
let customerDetailsAttempts=0;
let customerDetailsTicket='';
function scheduleCustomerDetails() {
  clearTimeout(customerDetailsTimer);
  if(customerDetailsTicket!==state.id){customerDetailsTicket=state.id;customerDetailsAttempts=0;}
  const rail=state.ticket?.shopifyRail;
  if(rail?.status==='loading'||rail?.refreshing||state.ticket?.redoDetails?.status==='pending') {
    customerDetailsTimer=setTimeout(()=>refreshCustomerDetails(),customerDetailsAttempts<15?2000:10000);
  } else if(rail?.refreshError) {
    customerDetailsTimer=setTimeout(()=>refreshCustomerDetails(),Math.max(30000,Math.min(300000,(Number(rail.retryAt||0)*1000)-Date.now())));
  }
}
async function refreshCustomerDetails(manual=false) {
  if(state.ticket?.localOnly)return;
  if(!state.ticket||document.hidden){scheduleCustomerDetails();return;}
  clearTimeout(customerDetailsTimer);
  const id=state.id,ticketRequest=state.ticketRequest,request=++customerDetailsRequest;
  const button=$('[data-action="retry-customer"]');if(button)button.disabled=true;
  try {
    const result=await api('get_ticket',{ticketId:id});
    if(state.id!==id||state.ticketRequest!==ticketRequest||!state.ticket||request!==customerDetailsRequest)return;
    state.ticket.shopifyRail=result.ticket.shopifyRail;state.ticket.redoDetails=result.ticket.redoDetails;
    customerDetailsAttempts++;
    renderRail();updateTitle();
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
$('#search').addEventListener('input',event=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{state.query=event.target.value;state.page=0;clearSelection();syncUrl();loadList();},250);});
document.addEventListener('input',event=>{if(event.target.id==='reply'){sizeReplyEditor(event.target);saveReply(event.target.value);$('#saved-note').textContent=storageAvailable?'Saved in this browser':'Kept for this session only';}});
window.addEventListener('resize',()=>{const editor=$('#reply');if(editor)sizeReplyEditor(editor);});
document.addEventListener('change',event=>{
  if(event.target.dataset.field)setField(event.target.dataset.field,event.target.value);
  if(event.target.dataset.filter){state.filters[event.target.dataset.filter]=event.target.value.trim();state.page=0;clearSelection();loadList();}
  if(event.target.dataset.selectTicket){const id=event.target.dataset.selectTicket;if(event.target.checked)state.selected.add(id);else state.selected.delete(id);updateSelection();}
  if(event.target.id==='select-page'){state.selected=event.target.checked?new Set(filtered().map(t=>t.id)):new Set();listRender();}
});
document.addEventListener('click',async event=>{
  const ticketButton=event.target.closest('[data-ticket]');if(ticketButton){await selectTicket(ticketButton.dataset.ticket,true,true);return;}
  const view=event.target.closest('[data-view]');if(view){state.view=view.dataset.view;state.page=0;clearSelection();syncUrl();loadList();return;}
  const tab=event.target.closest('[data-tab]');if(tab){state.tab=tab.dataset.tab;renderTicket();$(`[data-tab="${state.tab}"]`)?.focus({preventScroll:true});return;}
  const action=event.target.closest('[data-action]')?.dataset.action;if(!action)return;
  if(action==='refresh'){loadList(true);refreshTicket();return;}
  if(action==='retry-customer'){refreshCustomerDetails(true);return;}
  if(action==='older-messages'){loadOlder();return;}
  if(action==='retry-ticket'){selectTicket(state.id,false);return;}
  if(action==='sort'){state.oldest=!state.oldest;state.page=0;clearSelection();loadList();return;}
  if(action==='page-prev'||action==='page-next'){clearSelection();state.page=Math.max(0,state.page+(action==='page-next'?1:-1));await loadList();$('#ticket-list').scrollTop=0;return;}
  if(action==='ticket-prev'||action==='ticket-next'){
    const rows=filtered(),index=rows.findIndex(t=>t.id===state.id),direction=action==='ticket-next'?1:-1;
    let next=rows[index+direction];
    if(!next&&index>=0&&(direction>0?state.hasNext:state.page>0)){state.page+=direction;await loadList();clearSelection();next=direction>0?filtered()[0]:filtered().at(-1);}
    if(next)selectTicket(next.id,true,true);return;
  }
  if(action==='back'){$('#workspace').classList.remove('ticket-open','rail-open');$('#search').focus();return;}
  if(action==='rail'){railTrigger=event.target.closest('[data-action]');$('#workspace').classList.remove('rail-collapsed');$('#workspace').classList.add('rail-open');syncRailAccessibility();$('#customer-rail button')?.focus();return;}
  if(action==='rail-close'){closeRail();return;}
  if(action==='copy'){copyText(new URL('/inbox/?ticket='+encodeURIComponent(state.id),location.origin).href,'Ticket link copied.');return;}
  if(action==='toggle-send-access'){await toggleSendAccess();return;}
  if(action==='review-send'){await reviewSend();return;}
  if(action==='confirm-send'){await confirmSend();return;}
  if(action==='cancel-send'){cancelSendReview();return;}
  if(action==='check-send-status'){await checkSendStatus();return;}
  if(action==='copy-reply'){const value=$('#reply')?.value;if(value)copyText(value,'Reply copied.');else toast('Write a reply or use the suggested draft first.');return;}
  if(action==='edit-draft'){openRewrite();return;}
  if(action==='cancel-rewrite'){closeRewrite();return;}
  if(action==='retry-draft'){await retryDraft();return;}
  if(action==='use-draft'&&state.ticket&&draftAvailable(state.ticket)&&!draftNeedsStaff(state.ticket)){const editor=$('#reply'),body=currentDraft(state.ticket);if(editor.value.trim()&&editor.value!==body){editor.value=editor.value.trimEnd()+'\n\n'+body;toast('Suggestion added below your existing reply.');}else editor.value=body;sizeReplyEditor(editor);saveReply(editor.value);$('#saved-note').textContent=storageAvailable?'Saved in this browser':'Kept for this session only';editor.focus();return;}
  if(action==='dismiss-draft'||action==='restore-draft'){const dismissed=objectStore(keys.dismiss);if(action==='dismiss-draft')dismissed[state.id]=draftId(state.ticket);else delete dismissed[state.id];persist(keys.dismiss,dismissed);renderTicket();return;}
  if(action==='new'){$('#local-new-form').reset();$('#local-new-error').textContent='';$('#local-new').showModal();$('#local-new input').focus();return;}
  if(action==='cancel-new'){$('#local-new').close();return;}
  if(action==='list-collapse'||action==='list-expand'){$('#workspace').classList.toggle('list-collapsed',action==='list-collapse');$('.ticket-sidebar').inert=action==='list-collapse';$('.list-reopen button')?.focus({preventScroll:true});return;}
  if(action==='toggle-read'){markRead(state.ticket,!readState(state.ticket,readRecords(stored(keys.read,{}))));renderTicket();listRender();return;}
  if(action==='reset-local'){const records=stateRecords(stored(keys.state,{}));delete records[state.id];persist(keys.state,{version:1,records});const reads=readRecords(stored(keys.read,{}));delete reads[state.id];persist(keys.read,{version:1,records:reads});renderTicket();listRender();return;}
  if(action==='rename'){const title=prompt('Ticket title in this browser',ticketTitle(state.ticket));if(title!==null)setField('title',title.trim().slice(0,200));return;}
  if(action==='expand-draft'){$('.draft-card')?.classList.toggle('draft-expanded');const expanded=$('.draft-card')?.classList.contains('draft-expanded');event.target.closest('button').textContent=expanded?'Show less':'Expand suggestion';event.target.closest('button').setAttribute('aria-expanded',String(expanded));return;}
  if(action==='bulk-apply'){applyBulk();return;}
    if(action==='sign-out'){resetSendAccess();try {const response=await fetch('/console/api/auth/logout',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:'{}'});if(!response.ok)throw new Error();location.assign('/console/login?next=%2Finbox%2F');}catch {toast('Sign out failed. Please try again.');}}
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
window.addEventListener('popstate',()=>{params=new URLSearchParams(location.search);state.query=params.get('q')||'';state.view=['assigned','unassigned','all','open','snoozed','closed','trash','spam'].includes(params.get('view'))?params.get('view'):'all';$('#search').value=state.query;const id=params.get('ticket');if(id)selectTicket(id,false);else{state.id='';state.ticket=null;$('#workspace').classList.remove('ticket-open');const first=filtered()[0];if(first)selectTicket(first.id,false);}state.page=0;clearSelection();loadList();});

function updateTitle(){if(!state.ticket)return;$('.ticket-title')?.replaceChildren(document.createTextNode(ticketTitle(state.ticket)));document.title=`${ticketTitle(state.ticket)} · Buttons Bebe Support`;const row=state.rows.find(t=>t.id===state.id);if(row&&state.ticket.shopifyRail?.order){row.shopifyRail={order:state.ticket.shopifyRail.order};listRender();}}
function applyBulk(){const action=$('#bulk-action').value;if(!action)return;const rows=filtered().filter(t=>state.selected.has(t.id));const records=stateRecords(stored(keys.state,{})),reads=readRecords(stored(keys.read,{}));const at=Date.now();for(const t of rows){if(action==='read'||action==='unread')reads[t.id]={read:action==='read',message:lastMessage(t),activity:t.lastMessageAt||t.messages?.at(-1)?.at||t.updatedAt,at};else if(action==='reset'){delete records[t.id];delete reads[t.id];}else{let [field,value]=action.split(':');if(value==='me'){if(!state.operator){toast('Operator email is unavailable. Choose an assignee on the ticket.');return;}value=state.operator;}if(value==='tomorrow')value=new Date(at+86400000).toISOString();records[t.id]={...(records[t.id]||{}),[field]:{value,at,by:state.operator||'operator'}};}}persist(keys.state,{version:1,records});persist(keys.read,{version:1,records:reads});clearSelection();renderTicket();listRender();toast(storageAvailable?`Updated ${rows.length} tickets in this browser. Gorgias is unchanged.`:'Browser storage is unavailable. Updates last for this session only.');}
$('#local-new-form').addEventListener('submit',event=>{event.preventDefault();try{const input=Object.fromEntries(new FormData(event.target));const ticket=localTicket(input,crypto.randomUUID());persist(keys.local,[ticket,...localRows()]);state.view='all';state.query='';state.filters={priority:'',assignee:'',tag:'',channel:''};$('#search').value='';for(const input of document.querySelectorAll('[data-filter]'))input.value='';state.page=0;clearSelection();$('#local-new').close();selectTicket(ticket.id,true,true);loadList();}catch(error){$('#local-new-error').textContent=error.message;}});
let tooltipTarget=null;
const tooltip=document.createElement('div');tooltip.id='inbox-tooltip';tooltip.setAttribute('role','tooltip');tooltip.hidden=true;document.body.append(tooltip);
function hideTooltip(){if(tooltipTarget)tooltipTarget.removeAttribute('aria-describedby');tooltipTarget=null;tooltip.hidden=true;}
function showTooltip(el){if(!el||el.disabled)return;hideTooltip();tooltipTarget=el;tooltip.textContent=el.dataset.tooltip||el.getAttribute('aria-label')||el.title;el.setAttribute('aria-describedby',tooltip.id);tooltip.hidden=false;const r=el.getBoundingClientRect();tooltip.style.left=`${Math.max(8,Math.min(innerWidth-tooltip.offsetWidth-8,r.left))}px`;tooltip.style.top=`${r.bottom+tooltip.offsetHeight+8<innerHeight?r.bottom+6:Math.max(6,r.top-tooltip.offsetHeight-6)}px`;}
function installTooltips(){for(const el of document.querySelectorAll('button[data-action],.icon-button,[data-action="rail-close"],.access-pill')){el.dataset.tooltip=el.getAttribute('aria-label')||el.title||el.textContent.trim();}}
document.addEventListener('mouseover',event=>{const el=event.target.closest('[data-tooltip]');if(el)showTooltip(el);});document.addEventListener('mouseout',event=>{if(event.target.closest('[data-tooltip]'))hideTooltip();});document.addEventListener('focusin',event=>{const el=event.target.closest('[data-tooltip]');if(el)showTooltip(el);});document.addEventListener('focusout',hideTooltip);document.addEventListener('keydown',event=>{if(event.key==='Escape')hideTooltip();});installTooltips();
window.addEventListener('storage',event=>{if(event.key)memory.delete(event.key);else memory.clear();listRender();if(state.ticket)renderTicket();});
renderSendAccess();
api('capabilities').then(result=>{state.operator=result.operatorEmail||'';listRender();}).catch(()=>{});
if(state.id)selectTicket(state.id,false);
loadList();

let pollBusy=false;
async function pollLive(){if(document.hidden||pollBusy)return;pollBusy=true;try{await Promise.allSettled([loadList(true),refreshTicket()]);}finally{pollBusy=false;}}
setInterval(pollLive,30000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)pollLive();});
