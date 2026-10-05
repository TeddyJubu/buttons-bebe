import {icons} from './icons.js';
import {request as api,start,canWork,accountBar} from './client.js';
import {setupDelivery} from '/delivery.js';
import {setupAssistance} from '/assistance.js';
const $=(selector,root=document)=>root.querySelector(selector);
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icon=name=>`<svg class="icon" xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name]||icons.info}</svg>`;
const date=value=>new Intl.DateTimeFormat('en-GB',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'}).format(new Date(value));
const label=value=>String(value||'').replaceAll('_',' ').replace(/^./,c=>c.toUpperCase());
const initials=value=>String(value||'?').trim().split(/\s+/).slice(0,2).map(x=>x[0]).join('').toUpperCase();
const statuses=['open','waiting_customer','waiting_team','snoozed','closed'];
const priorities=['low','normal','high','critical'];
const queues=['work','all','inbox','review','spam','automatic'];
const params=new URLSearchParams(location.search);
const state={id:params.get('ticket')||'',ticket:null,rows:[],query:(params.get('q')||'').slice(0,200),
  view:['open','closed'].includes(params.get('view'))?params.get('view'):'all',
  queue:queues.includes(params.get('queue'))?params.get('queue'):'work',offset:0,total:0,next:null,
  teamView:'all',listRequest:0,ticketRequest:0,listError:'',tab:'conversation',ready:false};
const drafts=new Map(),detailsDrafts=new Map(),operations=new Map();
let session,teammates=[];
let busy=false,toastTimer,searchTimer,railTrigger;
const drawer=matchMedia('(max-width:1040px)'),mobile=matchMedia('(max-width:650px)');
for(const el of document.querySelectorAll('[data-icon]'))el.outerHTML=icon(el.dataset.icon);
function notice(message){$('#toast').textContent=message;$('#toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').hidden=true,9000);}
function syncUrl(push=false){const p=new URLSearchParams();if(state.id)p.set('ticket',state.id);if(state.query)p.set('q',state.query);if(state.view!=='all')p.set('view',state.view);if(state.queue!=='work')p.set('queue',state.queue);history[push?'pushState':'replaceState']({},'','/inbox/'+(p.size?'?'+p:''));}
function options(values,selected){return values.map(v=>`<option value="${v}" ${v===selected?'selected':''}>${label(v)}</option>`).join('');}

// Retain stable composer and ledger nodes when a ticket refreshes.
$('#conversation').innerHTML=`<div id="blank" class="blank-conversation"><h2>Your offline Inbox</h2><p>Select a saved conversation or create a test ticket.</p></div><header id="ticket-header" class="ticket-header" hidden></header><section id="ticket-content" role="tabpanel" hidden></section><section id="inbox-composer" class="reply-area" hidden><form id="note-form"><div class="composer"><div class="composer-heading">${icon('note')}<strong>Internal note</strong><span class="recipient">Saved in this workspace</span></div><textarea id="note" rows="3" maxlength="100000" required aria-label="Internal note" placeholder="Record a note for this test ticket…"></textarea><div class="composer-toolbar"><span id="saved-note" class="saved-note">Unsaved text stays in this tab</span><button type="submit" class="button primary">Save note</button><span id="reply-tools"><button id="open-reply" type="button" class="button">Simulate reply…</button></span></div></div></form><p id="reply-unavailable" class="composer-note"></p></section>`;
$('#customer-rail').innerHTML=`<div class="rail-heading">Ticket details<button data-action="rail-close" aria-label="Close customer details">${icon('panel')}</button></div><section id="rail-person" class="rail-section"><p>Select a ticket.</p></section><section id="rail-fields" class="rail-section" hidden><h3>Workspace details</h3><form id="details-form"><label for="status">Status</label><select id="status">${options(statuses,'open')}</select><label for="priority">Priority</label><select id="priority">${options(priorities,'normal')}</select><label for="assignee">Assigned to</label><select id="assignee"><option value="">Unassigned</option></select><p id="source-assignee" class="small muted"></p><button class="button" type="submit">Save details</button><p id="details-error" class="field-error" role="alert"></p></form></section><section id="source" class="rail-section"></section><section id="fixture-context" class="rail-section"></section><section class="rail-section"><h3>Simulated delivery</h3><div id="delivery-ledger"><p class="muted">Select a ticket.</p></div></section><section class="rail-section"><h3>Activity</h3><div id="activity"></div></section><section class="rail-section"><p class="small muted">Assistance uses saved test fixtures only. Live AI and customer/order lookups remain disabled.</p></section>`;
const renderDelivery=setupDelivery({api,current:()=>state.ticket,refresh:refreshAfterWrite,notice});
const renderAssistance=setupAssistance({api,current:()=>state.ticket,refresh:refreshAfterWrite,notice,canWork,seedReply:result=>renderDelivery.useSuggestion(result)});

function lock(value){busy=value;for(const form of ['#note-form','#details-form','#create-form'])for(const el of $(form).querySelectorAll('button,input,textarea,select'))el.disabled=value||!canWork();$('#open-reply').disabled=value||!canWork()||!state.ticket?.reply_context.available;}
async function write(key,path,fields,revision){
  const signature=JSON.stringify(fields),previous=operations.get(key);
  if(previous?.uncertain&&previous.signature!==signature)throw new Error('The previous change is unconfirmed. Refresh and retry its unchanged values first.');
  const operation=previous?.signature===signature?previous:{signature,data:{...fields,operation_id:crypto.randomUUID(),...(revision===undefined?{}:{revision})}};
  operations.set(key,operation);
  try{const result=await api(path,operation.data);operations.delete(key);return result;}
  catch(error){if([400,403,409].includes(error.status))operations.delete(key);else operation.uncertain=true;throw error;}
}
function resetTicket(message='Select a saved conversation or create a test ticket.'){
  state.ticket=null;state.ticketRequest++;$('#blank').hidden=false;$('#blank').textContent=message;
  for(const id of ['#ticket-header','#ticket-content','#inbox-composer','#rail-fields'])$(id).hidden=true;
  $('#rail-person').textContent='Select a ticket.';$('#source').replaceChildren();$('#fixture-context').replaceChildren();$('#activity').replaceChildren();$('#delivery-ledger').replaceChildren();
}
function listRender(error=state.listError){
  $('#ticket-list').innerHTML=state.rows.map(t=>`<button class="ticket-row" data-ticket="${esc(t.id)}" ${t.id===state.id?'aria-current="true"':''}><div class="row-top"><span class="row-name">${t.read_state?.unread?'<span class="unread-label">Unread · </span>':''}${esc(t.name||'Unknown contact')}</span><span class="age">BB-${t.number}</span></div><div class="row-subject">${esc(t.subject)}</div><div class="row-snippet">${esc(date(t.updated_at))}</div><div class="row-state">${esc(label(t.status))} · ${esc(label(t.queue))} · ${esc(t.team_assignee?.name||'Unassigned')}${t.origin==='manual_test'?' · Test ticket':''}</div></button>`).join('')||`<div class="empty-state${error?' is-error':''}">${esc(error||'No tickets match this view.')}${error?'<br><button class="button" data-action="refresh">Try again</button>':''}</div>`;
  $('#count').textContent=state.rows.length?`${state.offset+1}–${state.offset+state.rows.length} of ${state.total}`:'0 tickets';
  $('[data-action="page-prev"]').disabled=state.offset===0;
  $('[data-action="page-next"]').disabled=state.next===null;
  for(const el of document.querySelectorAll('[data-view]'))el.setAttribute('aria-pressed',String(el.dataset.view===state.view));
  $('#sync-status').textContent=error?'Read failed. Use Refresh to try again.':'Saved workspace · Newest activity first';
}
async function loadList(choose=false){
  if(!state.ready)return;
  const sequence=++state.listRequest;
  try{
    const qs=new URLSearchParams({query:state.query,status:state.view==='all'?'':state.view,queue:state.queue,offset:String(state.offset),view:state.teamView});
    const result=await api('/api/tickets?'+qs);if(sequence!==state.listRequest)return;
    state.rows=result.tickets;state.total=result.total;state.next=result.nextOffset;state.listError='';
    if(state.offset&&state.offset>=result.total){state.offset=Math.max(0,Math.floor((result.total-1)/50)*50);return loadList(choose);}
    listRender();
    if(choose){state.id='';resetTicket();syncUrl();if(!mobile.matches&&state.rows.length)await selectTicket(state.rows[0].id,false);}
  }catch(error){if(sequence!==state.listRequest)return;state.rows=[];state.total=0;state.next=null;state.listError=error.message;listRender();notice(error.message);}
}
async function selectTicket(id,push=true){
  if(!state.ready)return;
  const changed=id!==state.id;state.id=id;if(changed)state.tab='conversation';
  const sequence=++state.ticketRequest;
  if(changed||!state.ticket){resetTicket('Loading conversation…');state.ticketRequest=sequence;}
  $('#workspace').classList.add('ticket-open');$('#workspace').classList.remove('rail-open');syncRail();syncUrl(push);listRender();
  try{
    const ticket=await api('/api/tickets/'+encodeURIComponent(id));if(sequence!==state.ticketRequest||id!==state.id)return;
    state.ticket=ticket;renderTicket();renderRail();listRender();
  }catch(error){if(sequence!==state.ticketRequest||id!==state.id)return;resetTicket(error.message);notice(error.message);}
}
async function refreshAfterWrite(id){
  if(state.id===id)await selectTicket(id,false);
  await loadList();
}
function messageHtml(m){
  const kind=m.origin==='offline_simulation'?'Simulated reply · No email sent':m.kind==='outgoing'?'Historical reply':m.kind==='note'?'Internal note':m.origin==='offline_replay'?'Simulated incoming':'Customer message';
  return `<article class="message" data-message="${esc(m.id)}"><div class="message-heading"><span class="avatar">${esc(initials(m.author_name))}</span><div class="message-person"><strong>${esc(m.author_name)}</strong><span>${esc(m.author_email)}</span></div><time datetime="${esc(m.created_at)}">${date(m.created_at)}</time></div><div class="message-note-label">${kind}</div><div class="message-body" dir="auto"></div>${m.attachment_records.map(a=>{const meta=JSON.parse(a.metadata_json);return `<div class="attachment-label">${esc(meta.name||meta.filename||'Attachment')} · ${a.availability==='private_copy'?`Private copy saved · ${a.byte_size} bytes · Not opened`:'Metadata only · File not downloaded'}</div>`;}).join('')}</article>`;
}
function renderTicket(){
  const t=state.ticket;if(!t)return;
  $('#blank').hidden=true;for(const id of ['#ticket-header','#ticket-content','#inbox-composer'])$(id).hidden=false;
  $('#ticket-header').innerHTML=`<button class="mobile-back" data-action="back">${icon('left')} All tickets</button><div class="title-row"><div><h2 class="ticket-title">${esc(t.subject)}</h2><p class="ticket-subtitle">${esc(t.name)} · BB-${t.number} · ${esc(label(t.channel))}</p></div><div class="ticket-navigation"><button class="icon-button" data-action="ticket-prev" aria-label="Previous ticket">${icon('left')}</button><button class="icon-button" data-action="ticket-next" aria-label="Next ticket">${icon('right')}</button><button class="icon-button show-customer" data-action="rail" aria-label="Show customer details" aria-controls="customer-rail">${icon('user')}</button></div></div><div class="ticket-actions"><span class="badge ${t.status==='closed'?'green':'amber'}">${esc(label(t.status))}</span><span class="badge neutral">${esc(label(t.priority))}</span><span class="badge neutral">${esc(label(t.queue))}</span><button class="button" data-action="rail">Ticket details</button><button class="button" data-action="mark-read">${t.read_state.unread?'Mark read':'Mark unread'}</button>${!t.team_assignee&&canWork()?'<button class="button" data-action="claim">Assign to me</button>':''}</div><p class="live-ticket-sync">${t.origin==='gorgias_export'?'Saved Gorgias export':t.origin==='manual_test'?'Manual test record':'Offline replay'} · Workspace changes stay here</p><div class="ticket-tabs" role="tablist" aria-label="Ticket content"><button id="conversation-tab" role="tab" data-tab="conversation" aria-selected="${state.tab==='conversation'}" aria-controls="ticket-content">Conversation</button><button id="details-tab" role="tab" data-tab="details" aria-selected="${state.tab==='details'}" aria-controls="ticket-content">Ticket details</button></div>`;
  const fields=[['Ticket',`BB-${t.number}`],['Status',label(t.status)],['Priority',label(t.priority)],['Assigned to',t.team_assignee?.name||'Unassigned'],['Source assignment',t.assignee||'None recorded'],['Queue',label(t.queue)],['Review reason',label(t.review_reason)||'None'],['Created',date(t.created_at)],['Last activity',date(t.updated_at)],['Messages',t.messages.length]];
  $('#ticket-content').setAttribute('aria-labelledby',state.tab+'-tab');
  $('#ticket-content').innerHTML=state.tab==='conversation'?`<div class="message-area">${t.messages.map(messageHtml).join('')||'<p>No messages.</p>'}<section id="assistance-card" class="assistance-card" aria-label="Offline assistance"></section></div>`:`<div class="message-area"><dl class="saved-fields">${fields.map(([k,v])=>`<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl></div>`;
  const byId=new Map(t.messages.map(m=>[m.id,m]));
  for(const el of document.querySelectorAll('#ticket-content .message-body'))el.textContent=byId.get(el.closest('.message').dataset.message).body;
  $('#note').value=drafts.get(t.id)||'';
  renderDelivery(t);renderAssistance(t);$('#inbox-composer').hidden=!canWork();if(!canWork())for(const b of $('#delivery-ledger').querySelectorAll('button'))b.disabled=true;lock(busy);navigation();syncRail();
  document.title='Offline Inbox · Buttons Bebe';
}
function renderRail(){
  const t=state.ticket;if(!t)return;
  $('#rail-person').innerHTML=`<div class="customer-top"><span class="avatar">${esc(initials(t.name))}</span><div><div class="customer-name">${esc(t.name||'Unknown contact')}</div><div class="customer-email">${esc(t.email||'No email recorded')}</div></div></div>`;
  $('#rail-fields').hidden=false;
  const values=detailsDrafts.get(t.id)||{...t,assignee:t.team_assignee?.id||''};
  const options=teammates.filter(u=>(u.active&&u.role!=='viewer')||u.id===t.team_assignee?.id);
  $('#assignee').innerHTML='<option value="">Unassigned</option>'+options.map(u=>`<option value="${esc(u.id)}">${esc(u.name)}${!u.active||u.role==='viewer'?' (unavailable)':''}</option>`).join('');
  for(const field of ['status','priority','assignee'])$('#'+field).value=values[field];
  $('#source-assignee').textContent='Source assignment: '+(t.assignee||'None recorded');
  $('#details-error').textContent='';
  $('#source').innerHTML=`<h3>Source</h3><p class="small">${t.sources.map(s=>`${esc(s.account)} · #${esc(s.external_id)}`).join('<br>')||'Created in this sandbox'}</p><p class="small">${esc(JSON.parse(t.tags_json).map(tag=>typeof tag==='string'?tag:tag?.name||'Unnamed tag').join(' · '))}</p>${t.review_reason?`<p class="small">Needs review: ${esc(label(t.review_reason))}</p>`:''}`;
  const events={assistance_fixture_imported:'Assistance fixture imported',assistance_run_started:'Fixture run started',assistance_run_ready:'Test suggestion ready',assistance_run_needs_staff:'Staff input needed',assistance_run_no_reply:'No reply suggested',assistance_run_failed:'Fixture run failed',assistance_draft_used:'Test suggestion used',assistance_draft_dismissed:'Suggestion dismissed',ticket_created:'Test ticket created',ticket_assigned:'Assignment changed',ticket_updated:'Ticket details updated',note_added:'Internal note added',history_imported:'History imported',inbound_simulated:'Incoming message simulated',attachment_files_imported:'Private attachment copies saved',reply_simulation_reviewed:'Reply simulation reviewed',reply_simulation_started:'Reply simulation started',reply_simulation_simulated_delivered:'Simulated delivery recorded',reply_simulation_failed:'Simulation rejected',reply_simulation_uncertain:'Simulation outcome uncertain'};
  $('#activity').innerHTML=t.events.map(e=>`<div class="activity-item">${esc(events[e.kind]||label(e.kind))}<p class="small muted">${esc(e.actor)} · ${date(e.created_at)}</p></div>`).join('');
  lock(busy);
}
function navigation(){const i=state.rows.findIndex(t=>t.id===state.id);const prev=$('[data-action="ticket-prev"]'),next=$('[data-action="ticket-next"]');if(prev)prev.disabled=i<0||(i===0&&state.offset===0);if(next)next.disabled=i<0||(i===state.rows.length-1&&state.next===null);}
function syncRail(){const w=$('#workspace'),open=drawer.matches&&w.classList.contains('rail-open'),visible=drawer.matches?open:!w.classList.contains('rail-collapsed');for(const el of document.querySelectorAll('[data-action="rail"]'))el.setAttribute('aria-expanded',String(visible));for(const el of [$('.app-header'),$('.ticket-sidebar'),$('#conversation')])el.inert=open;const rail=$('#customer-rail');if(open){rail.setAttribute('role','dialog');rail.setAttribute('aria-modal','true');}else{rail.removeAttribute('role');rail.removeAttribute('aria-modal');}}
function closeRail(){$('#workspace').classList.remove('rail-open');$('#workspace').classList.add('rail-collapsed');syncRail();const target=railTrigger?.isConnected?railTrigger:$('[data-action="rail"]');target?.focus();}
drawer.addEventListener('change',()=>{$('#workspace').classList.remove('rail-open');syncRail();});
new ResizeObserver(([entry])=>document.documentElement.style.setProperty('--header-height',entry.target.getBoundingClientRect().height+'px')).observe($('.app-header'));

$('#note').addEventListener('input',event=>{if(state.ticket)drafts.set(state.id,event.target.value);});
$('#note-form').addEventListener('submit',async event=>{
  event.preventDefault();if(busy||!state.ticket)return;const t=state.ticket,body=$('#note').value;if(!body.trim())return;lock(true);
  try{await write('note:'+t.id,`/api/tickets/${t.id}/notes`,{body},t.revision);drafts.delete(t.id);if(state.id===t.id)$('#note').value='';await refreshAfterWrite(t.id);notice('Internal note saved in the offline workspace.');}
  catch(error){notice(error.message);}finally{lock(false);}
});
$('#details-form').addEventListener('input',()=>{if(state.ticket)detailsDrafts.set(state.id,Object.fromEntries(['status','priority','assignee'].map(k=>[k,$('#'+k).value])));});
$('#details-form').addEventListener('submit',async event=>{
  event.preventDefault();if(busy||!state.ticket)return;const t=state.ticket,fields=Object.fromEntries(['status','priority','assignee'].map(k=>[k,$('#'+k).value]));lock(true);
  try{await write('details:'+t.id,`/api/tickets/${t.id}/update`,{status:fields.status,priority:fields.priority,assignee_id:fields.assignee},t.revision);detailsDrafts.delete(t.id);await refreshAfterWrite(t.id);notice('Ticket details saved in the offline workspace.');}
  catch(error){if(state.id===t.id)$('#details-error').textContent=error.message;notice(error.message);}finally{lock(false);}
});
$('#create-form').addEventListener('submit',async event=>{
  event.preventDefault();if(busy||!state.ready)return;const fields=Object.fromEntries(new FormData(event.target));lock(true);$('#create-error').textContent='';
  try{const result=await write('create','/api/tickets',fields);$('#create-dialog').close();event.target.reset();state.offset=0;state.query='';state.view='all';state.queue='work';$('#search').value='';$('#queue').value='work';await loadList();await selectTicket(result.id);notice('Test ticket saved. No customer was contacted.');}
  catch(error){$('#create-error').textContent=error.message;}finally{lock(false);}
});
$('#create-dialog').addEventListener('cancel',event=>{if(busy)event.preventDefault();});
$('#search').value=state.query;$('#queue').value=state.queue;
$('#search').addEventListener('input',event=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{state.query=event.target.value;state.offset=0;loadList(true);},250);});
$('#team-view').addEventListener('change',event=>{state.teamView=event.target.value;state.offset=0;loadList(true);});
$('#queue').addEventListener('change',event=>{state.queue=event.target.value;state.offset=0;loadList(true);});
document.addEventListener('click',async event=>{
  const close=event.target.closest('[data-close]');if(close&&!busy){$('#'+close.dataset.close).close();return;}
  const selected=event.target.closest('[data-ticket]');if(selected){await selectTicket(selected.dataset.ticket);return;}
  const view=event.target.closest('[data-view]');if(view){state.view=view.dataset.view;state.offset=0;await loadList(true);return;}
  const tab=event.target.closest('[data-tab]');if(tab){state.tab=tab.dataset.tab;renderTicket();$('#'+state.tab+'-tab').focus();return;}
  const action=event.target.closest('[data-action]')?.dataset.action;
  if(action==='new'&&state.ready&&!busy&&canWork()){$('#create-error').textContent='';$('#create-dialog').showModal();$('#customer-name').focus();}
  if(action==='refresh'){$('#toast').hidden=true;if(!state.ready){boot();return;}await Promise.all([loadList(),state.id?selectTicket(state.id,false):Promise.resolve()]);}
  if((action==='mark-read'||action==='claim')&&state.ticket&&!busy){
    const t=state.ticket;lock(true);
    try{await write(action+':'+t.id,`/api/tickets/${t.id}/${action==='claim'?'claim':'read-state'}`,action==='claim'?{}:{unread:!t.read_state.unread,version:t.read_state.version,through_sequence:t.read_state.through_sequence},action==='claim'?t.revision:undefined);await refreshAfterWrite(t.id);}
    catch(error){notice(error.message);}finally{lock(false);}
  }
  if(action==='back'){$('#workspace').classList.remove('ticket-open','rail-open');syncRail();$('#search').focus();}
  if(action==='rail'){railTrigger=event.target.closest('[data-action]');$('#workspace').classList.remove('rail-collapsed');$('#workspace').classList.add('rail-open');syncRail();$('#customer-rail button').focus();}
  if(action==='rail-close')closeRail();
  if(action==='page-prev'||action==='page-next'){state.offset=action==='page-next'?state.next??state.offset:Math.max(0,state.offset-50);await loadList();$('#ticket-list').scrollTop=0;navigation();}
  if(action==='ticket-prev'||action==='ticket-next'){
    const i=state.rows.findIndex(t=>t.id===state.id),direction=action==='ticket-next'?1:-1;if(i<0)return;let next=state.rows[i+direction];
    if(!next&&(direction===1?state.next!==null:state.offset>0)){state.offset=direction===1?state.next:Math.max(0,state.offset-50);await loadList();next=direction===1?state.rows[0]:state.rows.at(-1);}
    if(next)await selectTicket(next.id);
  }
});
document.addEventListener('keydown',event=>{
  if(event.key==='Escape'&&$('#workspace').classList.contains('rail-open')&&!document.querySelector('dialog[open]'))closeRail();
  if(drawer.matches&&$('#workspace').classList.contains('rail-open')&&event.key==='Tab'&&!document.querySelector('dialog[open]')){
    const controls=[...$('#customer-rail').querySelectorAll('button,input,select,summary')].filter(el=>!el.disabled&&el.getClientRects().length),first=controls[0],last=controls.at(-1);
    if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}
  }
  if(event.target.matches('[role="tab"]')&&['ArrowLeft','ArrowRight'].includes(event.key)){event.preventDefault();state.tab=state.tab==='conversation'?'details':'conversation';renderTicket();$('#'+state.tab+'-tab').focus();}
});
window.addEventListener('beforeunload',event=>{if(busy||[...drafts.values()].some(v=>v.trim())||detailsDrafts.size){event.preventDefault();event.returnValue='';}});
window.addEventListener('popstate',()=>{const p=new URLSearchParams(location.search);state.query=(p.get('q')||'').slice(0,200);state.view=['open','closed'].includes(p.get('view'))?p.get('view'):'all';state.queue=queues.includes(p.get('queue'))?p.get('queue'):'work';state.offset=0;$('#search').value=state.query;$('#queue').value=state.queue;const id=p.get('ticket');if(id)selectTicket(id,false);else{state.id='';resetTicket();}loadList();});
window.addEventListener('intake-session-ended',()=>{busy=false;drafts.clear();detailsDrafts.clear();operations.clear();state.ready=false;state.ticket=null;state.listRequest++;state.ticketRequest++;});
async function boot(){
  try{session=await start();teammates=await api('/api/team');accountBar($('.header-actions'));state.ready=true;for(const b of document.querySelectorAll('[data-action="new"]'))b.hidden=!canWork();$('#workspace-name').textContent='Workspace: '+session.workspace;await loadList();if(state.id)await selectTicket(state.id,false);else if(!mobile.matches&&state.rows.length)await selectTicket(state.rows[0].id,false);}
  catch(error){state.ready=false;resetTicket(error.message);listRender(error.message);notice(error.message);}
}
boot();
