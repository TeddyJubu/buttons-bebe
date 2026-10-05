export const localKeys = {state:'bb-inbox-ticket-state-v1',read:'bb-inbox-read-v1',local:'bb-inbox-local-tickets-v1'};
export function stateRecords(value) {
  if(!value || typeof value!=='object' || Array.isArray(value)) return {};
  return 'version' in value ? value.version===1&&value.records&&typeof value.records==='object'&&!Array.isArray(value.records)?value.records:{} : value;
}
export function readRecords(value) {
  if(Array.isArray(value)) return {};
  return stateRecords(value);
}
export function lastMessage(ticket) {
  return String(ticket.lastMessageId || ticket.latestMessageId || ticket.messages?.at(-1)?.id || ticket.updatedAt || '');
}
export function messageActivity(ticket) {
  return ticket.lastMessageAt || ticket.messages?.at(-1)?.at || ticket.updatedAt || '';
}
function observedMessageTime(ticket) {
  return ticket.lastMessageAt || ticket.messages?.at(-1)?.at || '';
}
export function readMarker(ticket, read=true, at=Date.now()) {
  const knownMessage=ticket.lastMessageId || ticket.latestMessageId || ticket.messages?.at(-1)?.id;
  return {read,kind:knownMessage?'message':'activity',message:knownMessage?String(knownMessage):'',activity:messageActivity(ticket),at};
}
export function readState(ticket, records) {
  const marker=records[ticket.id];
  if(!marker?.read)return false;
  const activity=messageActivity(ticket),knownMessage=ticket.lastMessageId || ticket.latestMessageId || ticket.messages?.at(-1)?.id;
  // Keep the recorded watermark kind when a summary later becomes a detail.
  const kind=marker.kind || (/^\d{4}-\d{2}-\d{2}T/.test(marker.message||'')?'activity':'message');
  if(kind==='activity'||!knownMessage)return Boolean(activity&&marker.activity===activity);
  return marker.message===String(knownMessage);
}
export const MAX_OBSERVED_SUMMARIES=2000;
const providerId=/^gorgias:[1-9][0-9]{0,17}$/;
function verifiedTimestamp(value) {
  if(typeof value!=='string')return NaN;
  const parts=/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(Z|[+-](\d{2}):(\d{2}))$/.exec(value);
  if(!parts)return NaN;
  const [,year,month,day,hour,minute,second,,offsetHour='0',offsetMinute='0']=parts;
  const y=Number(year),m=Number(month),d=Number(day),days=[31,y%4===0&&(y%100!==0||y%400===0)?29:28,31,30,31,30,31,31,30,31,30,31];
  if(m<1||m>12||d<1||d>days[m-1]||Number(hour)>23||Number(minute)>59||Number(second)>59||Number(offsetHour)>23||Number(offsetMinute)>59)return NaN;
  return Date.parse(value);
}
export function enrichMessageEvidence(row,detail) {
  if(!providerId.test(row?.id||'')||row.localOnly||detail?.localOnly||detail?.id!==row.id)return row;
  const knownId=ticket=>{
    const value=ticket.lastMessageId||ticket.latestMessageId||ticket.messages?.at(-1)?.id;
    return typeof value==='string'||Number.isSafeInteger(value)&&value>0?String(value):'';
  };
  const activity=observedMessageTime(detail),incomingTime=verifiedTimestamp(activity),previousTime=verifiedTimestamp(observedMessageTime(row));
  const incomingUpdated=verifiedTimestamp(detail.updatedAt),previousUpdated=verifiedTimestamp(row.updatedAt);
  if(Number.isFinite(previousUpdated)&&(!Number.isFinite(incomingUpdated)||incomingUpdated<previousUpdated))return row;
  if(Number.isFinite(previousTime)&&(!Number.isFinite(incomingTime)||incomingTime<previousTime))return row;
  const message=knownId(detail),previousMessage=knownId(row);
  if(previousMessage&&message&&previousMessage!==message&&!(Number.isFinite(previousTime)&&Number.isFinite(incomingTime)&&incomingTime>previousTime))return row;
  if(!Number.isFinite(incomingTime)&&!message)return row;
  return {...row,...(Number.isFinite(incomingTime)?{lastMessageAt:activity}:{}),...(message?{lastMessageId:message}:{})};
}
export function observedSummary(ticket, savedAt=Date.now(), prior=null) {
  if(!providerId.test(ticket?.id||'')||ticket.localOnly)return null;
  const text=(value,max)=>typeof value==='string'?value.slice(0,max):'';
  const fields={id:40,customerName:200,fromEmail:254,subject:200,snippet:300,status:16,gorgiasPriority:16,assigneeEmail:254,assigneeTeam:200,channel:80,updatedAt:40,lastMessageAt:40,snoozedUntil:40,snoozeUntil:40,syncedAt:40};
  const summary={browserOverride:true,savedAt};
  for(const [field,max] of Object.entries(fields))summary[field]=text(ticket[field],max);
  summary.assignee=text(typeof ticket.assignee==='string'?ticket.assignee:ticket.assignee?.email||ticket.assignee?.name,254);
  summary.spam=ticket.spam===true;summary.trashed=ticket.trashed===true;
  summary.tags=(Array.isArray(ticket.tags)?ticket.tags:[]).slice(0,20).map(tag=>text(typeof tag==='string'?tag:tag?.name,200)).filter(Boolean);
  summary.lastMessageAt=text(observedMessageTime(ticket),40);
  const known=ticket.lastMessageId||ticket.latestMessageId||ticket.messages?.at(-1)?.id;
  const knownId=typeof known==='string'||Number.isSafeInteger(known)&&known>0?String(known):'';
  summary.lastMessageId=text(knownId,100) || (prior?.lastMessageAt===summary.lastMessageAt?text(prior.lastMessageId,100):'');
  // Keep only the observed order label, never the order/customer/message bodies.
  if(ticket.shopifyRail?.order?.name)summary.orderName=text(ticket.shopifyRail.order.name,200);
  else if(ticket.orderName)summary.orderName=text(ticket.orderName,200);
  else if(!Object.hasOwn(ticket,'shopifyRail')&&prior?.fromEmail===summary.fromEmail&&prior?.orderName)summary.orderName=text(prior.orderName,200);
  return summary;
}
export function rememberObserved(records,ticket,at=Date.now()) {
  const prior=records[ticket.id]?.observed,summary=observedSummary(ticket,at,prior);
  if(!summary)return records;
  // The two clocks guard one atomic observation. Without per-field versions,
  // mixed older/unknown and newer clocks cannot safely refresh its fields.
  for(const field of ['lastMessageAt','updatedAt']) {
    const incoming=verifiedTimestamp(summary[field]),previous=verifiedTimestamp(prior?.[field]);
    if(Number.isFinite(previous)&&(!Number.isFinite(incoming)||incoming<previous))return records;
  }
  records[ticket.id]={...(records[ticket.id]||{}),observed:summary};
  const saved=Object.entries(records).filter(([,record])=>record?.observed).sort((a,b)=>(Number(b[1].observed.savedAt)||0)-(Number(a[1].observed.savedAt)||0));
  for(const [,record] of saved.slice(MAX_OBSERVED_SUMMARIES))delete record.observed;
  return records;
}
export function syncObservedOverride(records,ticket,at=Date.now()) {
  const record=records[ticket.id],fields=['title','status','priority','assignee','snooze'];
  if(fields.some(field=>record?.[field]?.value))return rememberObserved(records,ticket,at);
  if(record){
    delete record.observed;
    for(const field of fields)if(!record[field]?.value)delete record[field];
    if(!Object.keys(record).length)delete records[ticket.id];
  }
  return records;
}
export function observedRows(records) {
  return Object.entries(records).filter(([id,record])=>providerId.test(id)&&record?.observed?.id===id)
    .sort((a,b)=>(Number(b[1].observed.savedAt)||0)-(Number(a[1].observed.savedAt)||0)).slice(0,MAX_OBSERVED_SUMMARIES)
    .map(([,record])=>observedSummary(record.observed,Number(record.observed.savedAt)||0,record.observed));
}
export function localTicket(input, uuid, now=new Date().toISOString()) {
  const name=String(input.name||'').trim(), body=String(input.body||'').trim();
  if(!name || !body || name.length>200 || body.length>30000) throw new Error('Enter a name and a message within the limits.');
  const id=`local:${uuid}`, subject=String(input.subject||'').trim().slice(0,200);
  return {id,localOnly:true,customerName:name,subject,fromEmail:'',channel:'local',status:'open',updatedAt:now,messages:[{id:`${id}:message`,fromName:name,fromAgent:false,body,at:now}],snippet:body.slice(0,300),shopifyRail:{status:'unavailable'}};
}
export function matchesLocal(ticket, view, filters, effective, operator, now=Date.now()) {
  const status=effective(ticket,'status'), assignee=effective(ticket,'assignee'), snooze=effective(ticket,'snooze');
  const fold=value=>String(value||'').trim().toLowerCase();
  const snoozed=Boolean(snooze && Date.parse(snooze)>now);
  if(view==='assigned' && (!operator || fold(assignee)!==fold(operator))) return false;
  if(view==='unassigned' && (assignee && assignee!=='unassigned' || !effective(ticket,'assignee')&&ticket.assigneeTeam)) return false;
  if(view==='open' && status!=='open') return false;
  if(view==='closed' && status!=='closed') return false;
  if(view==='snoozed' && !snoozed) return false;
  if(view==='trash' && !(ticket.trashed||ticket.isTrash) && status!=='trash') return false;
  if(view==='spam' && !(ticket.spam||ticket.isSpam) && status!=='spam') return false;
  if(!['trash','spam'].includes(view) && (ticket.trashed||ticket.spam||ticket.isTrash||ticket.isSpam)) return false;
  if(filters.priority && effective(ticket,'priority')!==filters.priority) return false;
  if(filters.assignee && (fold(filters.assignee)==='unassigned' ? Boolean(assignee&&fold(assignee)!=='unassigned') : fold(assignee)!==fold(filters.assignee))) return false;
  if(filters.channel && fold(ticket.channel)!==fold(filters.channel)) return false;
  if(filters.tag && !(ticket.tags||[]).map(t=>fold(typeof t==='string'?t:t.name)).includes(fold(filters.tag))) return false;
  const query=String(filters.query||'').trim().replace(/\s+/g,' ').toLowerCase();
  if(query.length>200)return false;
  if(query && ![ticket.customerName,ticket.fromEmail,ticket.subject,ticket.snippet,ticket.id].join(' ').toLowerCase().includes(query)) return false;
  return true;
}
