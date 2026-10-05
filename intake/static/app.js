import {request as api,start,accountBar,canWork} from '/auth.js';
import {setupDelivery} from './delivery.js';
import {setupAssistance} from '/assistance.js';
const $ = selector => document.querySelector(selector);
const state = {token: '', ticket: null, offset: 0, next: null, listSeq: 0, ticketSeq: 0, importData: null};
const notes = new Map();
const labels = {open:'Open',waiting_customer:'Waiting on customer',waiting_team:'Waiting on team',snoozed:'Snoozed',closed:'Closed'};
const queues = {inbox:'Inbox',review:'Needs review',spam:'Spam',automatic:'Automatic response'};
const reasons = {missing_message_id:'Missing message ID',invalid_headers:'Unsupported or ambiguous headers',ambiguous_sender:'Unclear sender',unknown_reference:'Previous message not found',ambiguous_references:'References point to multiple conversations',participant_mismatch:'Sender does not match the conversation',spam_flag:'Marked as spam',new_conversation:'Separate conversation'};
const reasonLabel = value => value.startsWith('automatic_response:')?'Automatic response · '+(reasons[value.split(':')[1]]||'Needs review'):(reasons[value]||value);
const node = (tag, value, className='') => { const el=document.createElement(tag);el.textContent=value;if(className)el.className=className;return el; };
const stamp = value => new Date(value).toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'});
function notice(message=''){ $('#notice').textContent=message;$('#notice').hidden=!message; }
const renderDelivery=setupDelivery({api,current:()=>state.ticket,notice,refresh:async id=>{if(state.ticket?.id===id)await select(id);await loadList();}});
const renderAssistance=setupAssistance({api,current:()=>state.ticket,notice,canWork,refresh:async id=>{if(state.ticket?.id===id)await select(id);await loadList();},seedReply:result=>renderDelivery.useSuggestion(result)});
function rememberNote(){if(state.ticket)notes.set(state.ticket.id,$('#note').value);}
async function loadList(){
  const seq=++state.listSeq,query=new URLSearchParams({query:$('#search').value,status:$('#filter').value,queue:$('#queue').value,offset:state.offset});
  const data=await api('/api/tickets?'+query);if(seq!==state.listSeq)return;
  state.next=data.nextOffset;$('#count').textContent=data.total;$('#ticket-list').replaceChildren();
  $('#previous').disabled=state.offset===0;$('#next').disabled=state.next===null;
  $('#page-label').textContent=data.total?`${state.offset+1}–${state.offset+data.tickets.length}`:'0 tickets';
  for(const ticket of data.tickets){
    const button=node('button','', 'ticket-row');button.dataset.ticket=ticket.id;button.setAttribute('aria-current',String(ticket.id===state.ticket?.id));
    const top=node('span','','row-top');top.append(node('span',ticket.name),node('span',`BB-${ticket.number}`));
    const bottom=node('span','','row-bottom');bottom.append(node('span',labels[ticket.status]),node('span',ticket.queue!=='inbox'?queues[ticket.queue]:ticket.origin==='gorgias_export'?'Imported':ticket.origin==='offline_replay'?'Simulated':'Test ticket'));
    button.append(top,node('span',ticket.subject,'row-title'),bottom);button.addEventListener('click',()=>select(ticket.id).catch(e=>notice(e.message)));$('#ticket-list').append(button);
  }
  if(!data.tickets.length)$('#ticket-list').append(node('p','No tickets in this view.','empty'));
  if(!state.ticket&&data.tickets.length)await select(data.tickets[0].id);
}
async function select(id){
  rememberNote();const seq=++state.ticketSeq;const ticket=await api('/api/tickets/'+encodeURIComponent(id));if(seq!==state.ticketSeq)return;
  state.ticket=ticket;notice();renderTicket(ticket);
  for(const row of document.querySelectorAll('.ticket-row'))row.setAttribute('aria-current',String(row.dataset.ticket===id));
}
function renderTicket(ticket){
  renderDelivery(ticket);
  $('#ticket-header').replaceChildren(node('p',`BB-${ticket.number} · ${ticket.origin==='gorgias_export'?'Imported conversation':ticket.origin==='offline_replay'?'Simulated conversation':'Manual test ticket'}`,'ticket-number'),node('h2',ticket.subject,'ticket-subject'),node('p',`${ticket.name} · ${ticket.channel} · ${queues[ticket.queue]}`,'ticket-subtitle'));
  if(ticket.review_reason)$('#ticket-header').append(node('p',reasonLabel(ticket.review_reason),'ticket-subtitle'));
  $('#messages').replaceChildren();
  for(const message of ticket.messages){
    const article=node('article','',`message ${message.kind}`),heading=node('div','','message-heading');
    const kind=message.origin==='offline_simulation'?'Simulated reply · No email sent':message.origin==='offline_replay'?'Simulated incoming':message.kind==='note'?'Internal note':message.kind==='outgoing'?'Historical reply':'Customer message';
    heading.append(node('strong',message.author_name),node('span',kind,'message-kind'),node('time',stamp(message.created_at)));
    article.append(heading,node('div',message.body||'(No text content)','message-body'));
    for(const record of message.attachment_records){
      const attachment=JSON.parse(record.metadata_json);
      const name=attachment.name||attachment.filename||'Attachment';
      article.append(node('div',record.availability==='private_copy'?`${name} · Private copy saved · ${record.byte_size} bytes · Not opened`:`${name} · Metadata only · File not downloaded`,'attachment'));
    }
    $('#messages').append(article);
  }
  if(!ticket.messages.length)$('#messages').append(node('p','This export contains no messages.','empty'));
  const assistance=node('section','','assistance-card');assistance.id='assistance-card';assistance.setAttribute('aria-label','Offline assistance');$('#messages').append(assistance);renderAssistance(ticket);
  $('#contact').replaceChildren(node('p',ticket.name,'contact-name'),node('p',ticket.email||'No email on record','contact-email'));
  $('#status').value=ticket.status;$('#priority').value=ticket.priority;$('#assignee').value=ticket.team_assignee?.id||'';
  $('#details-form').hidden=!canWork();$('#note-form').hidden=!canWork();$('#reply-tools').hidden=!canWork();if(!canWork())for(const b of $('#delivery-ledger').querySelectorAll('button'))b.disabled=true;$('#note').value=notes.get(ticket.id)||'';
  $('#provenance').replaceChildren(node('h3','Source'));
  if(ticket.sources.length){for(const source of ticket.sources)$('#provenance').append(node('p',`Gorgias export · ${source.account} · #${source.external_id}`,'source'));}
  else $('#provenance').append(node('p','Created in this sandbox.','source'));
  for(const tag of JSON.parse(ticket.tags_json))$('#provenance').append(node('span',typeof tag==='string'?tag:(tag.name||String(tag.id||'Tag')),'badge'));
  $('#activity').replaceChildren();
  const names={ticket_created:'Test ticket created',ticket_updated:'Ticket details updated',note_added:'Internal note added',history_imported:'History imported',inbound_simulated:'Incoming message simulated',reply_simulation_reviewed:'Reply simulation reviewed',reply_simulation_started:'Reply simulation started',reply_simulation_simulated_delivered:'Simulated delivery recorded',reply_simulation_failed:'Simulation rejected',reply_simulation_uncertain:'Simulation outcome uncertain',attachment_files_imported:'Private attachment copies saved'};
  for(const event of ticket.events){const row=node('div',names[event.kind]||event.kind,'activity-item');row.append(node('time',`${event.actor} · ${stamp(event.created_at)}`));$('#activity').append(row);}
}
async function busy(form, action, errorTarget){
  const controls=[...form.querySelectorAll('button,input,select,textarea')],disabled=controls.map(c=>c.disabled);controls.forEach(c=>c.disabled=true);
  if(errorTarget)errorTarget.textContent='';
  try{await action();}catch(error){if(errorTarget)errorTarget.textContent=error.message;else notice(error.message);}
  finally{controls.forEach((c,i)=>c.disabled=disabled[i]);}
}
// Keep an operation ID while a request's outcome is uncertain. A changed payload
// starts a new operation; stale-revision checks stop accidental duplicate notes.
const pending=new Map();
function operation(scope, payload){const key=JSON.stringify(payload),old=pending.get(scope);if(old?.key===key)return old.data;const data={...payload,operation_id:crypto.randomUUID()};pending.set(scope,{key,data});return data;}
for(const button of document.querySelectorAll('[data-close]'))button.addEventListener('click',()=>document.getElementById(button.dataset.close).close());
$('#open-create').addEventListener('click',()=>$('#create-dialog').showModal());
$('#open-import').addEventListener('click',()=>$('#import-dialog').showModal());
$('#refresh').addEventListener('click',()=>Promise.all([loadList(),state.ticket?select(state.ticket.id):Promise.resolve()]).catch(e=>notice(e.message)));
let timer;
$('#search').addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(()=>{state.offset=0;loadList().catch(e=>notice(e.message));},200);});
$('#filter').addEventListener('change',()=>{state.offset=0;loadList().catch(e=>notice(e.message));});
$('#queue').addEventListener('change',()=>{state.offset=0;loadList().catch(e=>notice(e.message));});
$('#previous').addEventListener('click',()=>{state.offset=Math.max(0,state.offset-50);loadList().catch(e=>notice(e.message));});
$('#next').addEventListener('click',()=>{state.offset=state.next||0;loadList().catch(e=>notice(e.message));});
$('#create-form').addEventListener('submit',event=>{
  event.preventDefault();const payload=Object.fromEntries(new FormData(event.target));
  busy(event.target,async()=>{const result=await api('/api/tickets',operation('create',payload));pending.delete('create');$('#create-dialog').close();event.target.reset();$('#search').value='';$('#filter').value='';$('#queue').value='work';state.offset=0;await select(result.id);await loadList();},$('#create-error'));
});
$('#details-form').addEventListener('submit',event=>{
  event.preventDefault();const id=state.ticket.id,payload={revision:state.ticket.revision,status:$('#status').value,priority:$('#priority').value,assignee_id:$('#assignee').value};
  busy(event.target,async()=>{await api(`/api/tickets/${id}/update`,operation('details:'+id,payload));pending.delete('details:'+id);if(state.ticket?.id===id)await select(id);await loadList();});
});
$('#note-form').addEventListener('submit',event=>{
  event.preventDefault();const id=state.ticket.id,body=$('#note').value,payload={body,revision:state.ticket.revision};
  busy(event.target,async()=>{await api(`/api/tickets/${id}/notes`,operation('note:'+id,payload));pending.delete('note:'+id);notes.delete(id);if(state.ticket?.id===id){$('#note').value='';await select(id);}await loadList();});
});
function clearPreview(){state.importData=null;$('#commit-import').disabled=true;$('#import-report').replaceChildren();$('#import-error').textContent='';}
$('#export-file').addEventListener('change',clearPreview);$('#account').addEventListener('input',clearPreview);
function reportView(report){
  const box=node('div','','import-summary');
  box.append(node('strong',report.alreadyImported?'Already imported — no duplicate records.':report.batchId?'Import saved in this sandbox.':report.canImport?'Ready for review':'Import blocked'));
  box.append(node('p',`${report.source.tickets} tickets · ${report.source.messages} messages · ${report.source.notes} private notes · ${report.source.attachments} attachment references`));
  box.append(node('p',`${report.new.tickets} new tickets · ${report.new.messages} new messages · ${report.duplicates.messages} existing messages`));
  const list=node('ul','');for(const line of [...report.conflicts,...report.warnings])list.append(node('li',line));box.append(list,node('p','Outbound actions: 0. Attachments remain metadata only.'));return box;
}
$('#import-form').addEventListener('submit',event=>{
  event.preventDefault();const file=$('#export-file').files[0],account=$('#account').value;clearPreview();
  busy(event.target,async()=>{
    if(!file||file.size>20*1024*1024)throw new Error('Choose a JSON export no larger than 20 MiB.');
    const data=await file.text();
    const report=await api('/api/import/preview',{account,export_text:data});state.importData={account,export_text:data,expected_digest:report.digest};$('#import-report').replaceChildren(reportView(report));
    // busy() restores the previous disabled state; enable after it completes.
    state.importData.canImport=report.canImport;
  },$('#import-error')).then(()=>{$('#commit-import').disabled=!state.importData?.canImport;});
});
$('#commit-import').addEventListener('click',()=>{
  if(!state.importData?.canImport)return;
  busy($('#import-form'),async()=>{const report=await api('/api/import/commit',state.importData);$('#import-report').replaceChildren(reportView(report));state.importData=null;await loadList();},$('#import-error')).then(()=>{$('#commit-import').disabled=!state.importData?.canImport;});
});
$('#open-history').addEventListener('click',async()=>{
  try{const entries=await api('/api/imports');$('#history').replaceChildren();for(const entry of entries){const report=JSON.parse(entry.report_json),row=node('div','','history-entry');row.append(node('strong',entry.account),node('p',stamp(entry.created_at)),node('p',`Saved: ${report.new.tickets} tickets and ${report.new.messages} messages. Outbound actions: 0.`));$('#history').append(row);}if(!entries.length)$('#history').append(node('p','No exports imported yet.','muted'));$('#history-dialog').showModal();}catch(e){notice(e.message);}
});
try{const session=await start();state.token=session.token;accountBar($('.topbar'));$('#open-create').hidden=!canWork();$('#open-import').hidden=!session.permissions.includes('import');$('#open-history').hidden=!session.permissions.includes('import');const members=await api('/api/team');for(const user of members.filter(u=>u.active&&u.role!=='viewer')){const option=node('option',user.name);option.value=user.id;$('#assignee').append(option);}$('#workspace-name').textContent='Workspace: '+session.workspace;await loadList();}catch(e){notice(e.message);$('#ticket-list').replaceChildren(node('p','Sandbox unavailable. Refresh to retry.','empty'));}
