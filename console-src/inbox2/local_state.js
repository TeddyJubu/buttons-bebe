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
export function readState(ticket, records) {
  const marker=records[ticket.id];
  return Boolean(marker?.read && (ticket.messages||ticket.lastMessageId||ticket.latestMessageId ? marker.message===lastMessage(ticket) : marker.activity===(ticket.lastMessageAt||ticket.updatedAt)));
}
export function localTicket(input, uuid, now=new Date().toISOString()) {
  const name=String(input.name||'').trim(), body=String(input.body||'').trim();
  if(!name || !body || name.length>200 || body.length>30000) throw new Error('Enter a name and a message within the limits.');
  const id=`local:${uuid}`, subject=String(input.subject||'').trim().slice(0,200);
  return {id,localOnly:true,customerName:name,subject,fromEmail:'',channel:'local',status:'open',updatedAt:now,messages:[{id:`${id}:message`,fromName:name,fromAgent:false,body,at:now}],snippet:body.slice(0,300),shopifyRail:{status:'unavailable'}};
}
export function matchesLocal(ticket, view, filters, effective, operator, now=Date.now()) {
  const status=effective(ticket,'status'), assignee=effective(ticket,'assignee'), snooze=effective(ticket,'snooze');
  const snoozed=Boolean(snooze && Date.parse(snooze)>now);
  if(view==='assigned' && (!operator || assignee!==operator)) return false;
  if(view==='unassigned' && (assignee && assignee!=='unassigned' || !effective(ticket,'assignee')&&ticket.assigneeTeam)) return false;
  if(view==='open' && status!=='open') return false;
  if(view==='closed' && status!=='closed') return false;
  if(view==='snoozed' && !snoozed) return false;
  if(view==='trash' && !(ticket.trashed||ticket.isTrash) && status!=='trash') return false;
  if(view==='spam' && !(ticket.spam||ticket.isSpam) && status!=='spam') return false;
  if(!['trash','spam'].includes(view) && (ticket.trashed||ticket.spam||ticket.isTrash||ticket.isSpam)) return false;
  if(filters.priority && effective(ticket,'priority')!==filters.priority) return false;
  if(filters.assignee && (filters.assignee==='unassigned' ? Boolean(assignee&&assignee!=='unassigned') : assignee!==filters.assignee)) return false;
  if(filters.channel && ticket.channel!==filters.channel) return false;
  if(filters.tag && !(ticket.tags||[]).map(t=>typeof t==='string'?t:t.name).includes(filters.tag)) return false;
  if(filters.query && ![ticket.customerName,ticket.subject,ticket.snippet,ticket.id].join(' ').toLowerCase().includes(filters.query.toLowerCase())) return false;
  return true;
}
