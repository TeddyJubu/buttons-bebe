import test from 'node:test';
import assert from 'node:assert/strict';
import {createInboxOrgan} from '../js/inbox.js';
import {createHelpdeskShop} from '../js/shop/production-shop.js';

test('empty production responses do not become demo tickets', async () => {
 const shop=createHelpdeskShop({client:{invoke:async tool => ({ok:true,source:'inbox',tickets:[],gorgiasEnabled:false,outboundEnabled:false})}});
 const result=await createInboxOrgan({shop,viewId:'all'}).ready();
 assert.equal(result.selectedId,null);
 assert.match(result.html,/No tickets yet/);
 assert.doesNotMatch(result.html,/Ada Demo|Casey Sandbox|Sample Romper/);
});
test('HTTP failures show unavailable state without fixture fallback', async () => {
 const shop=createHelpdeskShop({client:{invoke:async () => {throw new Error('offline');}}});
 const result=await createInboxOrgan({shop,viewId:'all'}).ready();
 assert.equal(result.selectedId,null);
 assert.match(result.html,/Tickets unavailable/);
 assert.doesNotMatch(result.html,/Ada Demo|Casey Sandbox/);
});
test('production client rejects a stale fixture response', async () => {
 const shop=createHelpdeskShop({client:{invoke:async () => ({ok:true,source:'sample',tickets:[{id:'t-ada-track'}]})}});
 await assert.rejects(shop.listTickets({}),/Preview data/);
});

const localTicket = {id:'t-in-test',customerName:'Local test',subject:'Privacy request',snippet:'Test',status:'open',updatedAt:'2026-09-07T00:00:00Z',messages:[],statusEvents:[],requestType:'privacy_request'};

test('failed mutations never fabricate a completed workflow', async () => {
  for (const [method, action] of [['escalateTicket','escalate'],['markPrivacyHandled','markPrivacyHandled'],['markUnsubscribed','markUnsubscribed'],['markBugHandled','markBugHandled']]) {
    const shop = {[method]:async () => {throw new Error('Persistence failed');}};
    const organ = createInboxOrgan({shop,tickets:[localTicket],viewId:'all'});
    await organ.ready();
    await assert.rejects(organ[action](), /Persistence failed/);
    assert.doesNotMatch(organ.snapshot().html,/data-escalated|thread-request-handled/);
  }
});

test('missing or failed capability response leaves unsupported controls hidden', async () => {
  const shop = createHelpdeskShop({client:{invoke:async tool => {
    if (tool === 'helpdesk.capabilities') throw new Error('offline');
    return {ok:true,source:'inbox',tickets:[localTicket],ticket:localTicket};
  }}});
  const organ = createInboxOrgan({shop,viewId:'all'});
  const result = await organ.ready();
  assert.doesNotMatch(result.html,/data-escalate=|data-summarize=|data-macros\b|data-privacy-gate-open/);
  assert.match(result.html,/Customer and order lookup is not connected/);
  await assert.rejects(organ.escalate(),/not available/);
  assert.equal(organ.attemptSend().sendError,'Activate the send access.');
});

test('query-requested privacy dialog cannot reveal an unavailable workflow', async () => {
 const shop=createHelpdeskShop({client:{invoke:async () => ({ok:true,source:'inbox',tickets:[]})}});
 const result=await createInboxOrgan({shop,viewId:'all',privacyGate:true}).ready();
 assert.doesNotMatch(result.html,/data-privacy-handled|data-privacy-gate/);
});
test('a failed thread fetch is announced instead of rendering an apparently empty conversation',async()=>{
 const shop=createHelpdeskShop({client:{invoke:async tool=>{
  if(tool==='helpdesk.get_ticket')throw new Error('offline');
  return {ok:true,tickets:[{...localTicket,projectionSource:true}],projection:{generatedAt:'one',stale:false}};
 }}});
 const result=await createInboxOrgan({shop,viewId:'all'}).ready();
 assert.match(result.html,/role="alert">Ticket history is unavailable/);
});
test('stale empty projection announces delayed history without invented rows',async()=>{
 const shop=createHelpdeskShop({client:{invoke:async()=>({ok:true,tickets:[],projection:{generatedAt:'one',stale:true}})}});
 const result=await createInboxOrgan({shop}).ready();
 assert.match(result.html,/role="status">Observed history is stale/);
 assert.match(result.html,/No tickets yet/);
 assert.equal(result.selectedId,null);
});
const channelTickets=[
 {id:'t-email',customerName:'Email Customer',subject:'Email question',snippet:'Hi',status:'unknown',updatedAt:'2026-09-14T00:00:00Z',channel:'email',messages:[],statusEvents:[],projectionSource:true},
 {id:'t-chat',customerName:'Chat Customer',subject:'Chat question',snippet:'Hey',status:'unknown',updatedAt:'2026-09-13T00:00:00Z',channel:'chat',messages:[],statusEvents:[],projectionSource:true},
 {id:'t-blank',customerName:'No Channel',subject:'Mystery',snippet:'Yo',status:'unknown',updatedAt:'2026-09-12T00:00:00Z',messages:[],statusEvents:[],projectionSource:true},
];
function channelShop(tickets){
 return createHelpdeskShop({client:{invoke:async (tool,args)=>{
  if(tool==='helpdesk.get_ticket')return {ok:true,source:'inbox',ticket:tickets.find(t=>t.id===args?.ticketId)||null};
  return {ok:true,source:'inbox',tickets,projection:{generatedAt:'2026-09-15T00:00:00Z',stale:false,ticketCount:tickets.length}};
 }}});
}
test('channel menu lists loaded channels with counts',async()=>{
 const result=await createInboxOrgan({shop:channelShop(channelTickets)}).ready();
 assert.match(result.html,/data-channel="email"/);
 assert.match(result.html,/data-channel="chat"/);
 assert.match(result.html,/All channels/);
 assert.doesNotMatch(result.html,/data-channel="sms"/);
});
test('selectChannel filters the observed list to that channel',async()=>{
 const organ=createInboxOrgan({shop:channelShop(channelTickets)});
 await organ.ready();
 const result=await organ.selectChannel('chat');
 assert.equal(result.channelId,'chat');
 assert.equal(result.selectedId,'t-chat');
 assert.match(result.html,/data-ticket="t-chat"/);
 assert.doesNotMatch(result.html,/data-ticket="t-email"/);
 assert.doesNotMatch(result.html,/data-ticket="t-blank"/);
});
test('unknown channel fails closed with an empty list',async()=>{
 const organ=createInboxOrgan({shop:channelShop(channelTickets)});
 await organ.ready();
 const result=await organ.selectChannel('sms');
 assert.equal(result.channelId,'sms');
 assert.equal(result.selectedId,null);
 assert.match(result.html,/No tickets yet/);
 assert.doesNotMatch(result.html,/data-ticket="t-/);
});
test('clearing the channel restores every loaded row',async()=>{
 const organ=createInboxOrgan({shop:channelShop(channelTickets)});
 await organ.ready();
 await organ.selectChannel('chat');
 const result=await organ.selectChannel('');
 assert.equal(result.channelId,'');
 assert.match(result.html,/data-ticket="t-chat"/);
 assert.match(result.html,/data-ticket="t-email"/);
 assert.match(result.html,/data-ticket="t-blank"/);
});
test('channel control hides when no ticket carries a channel',async()=>{
 const plain=channelTickets.map(({channel,...rest})=>rest);
 const result=await createInboxOrgan({shop:channelShop(plain)}).ready();
 assert.doesNotMatch(result.html,/data-list-channel/);
 assert.match(result.html,/data-ticket="t-email"/);
});
test('observed status renders a badge only when known',async()=>{
 const tickets=[{...channelTickets[0],id:'t-closed',status:'closed'},channelTickets[2]];
 const result=await createInboxOrgan({shop:channelShop(tickets)}).ready();
 assert.match(result.html,/<span class="ticket-status">Closed<\/span>/);
 assert.doesNotMatch(result.html,/ticket-status">Unknown/);
});
const statusTickets=[
 {id:'t-open',customerName:'Open Customer',subject:'Open question',snippet:'Hi',status:'open',updatedAt:'2026-09-14T00:00:00Z',channel:'email',messages:[],statusEvents:[],projectionSource:true},
 {id:'t-closed',customerName:'Closed Customer',subject:'Closed question',snippet:'Hey',status:'closed',updatedAt:'2026-09-13T00:00:00Z',channel:'chat',messages:[],statusEvents:[],projectionSource:true},
 {id:'t-unknown',customerName:'Unknown Customer',subject:'Mystery',snippet:'Yo',status:'unknown',updatedAt:'2026-09-12T00:00:00Z',channel:'email',messages:[],statusEvents:[],projectionSource:true},
];
test('status menu lists known statuses but never unknown',async()=>{
 const result=await createInboxOrgan({shop:channelShop(statusTickets)}).ready();
 assert.match(result.html,/data-status-pick="open"/);
 assert.match(result.html,/data-status-pick="closed"/);
 assert.match(result.html,/All statuses/);
 assert.doesNotMatch(result.html,/data-status-pick="unknown"/);
});
test('selectStatus filters the observed list to that status',async()=>{
 const organ=createInboxOrgan({shop:channelShop(statusTickets)});
 await organ.ready();
 const result=await organ.selectStatus('closed');
 assert.equal(result.statusId,'closed');
 assert.equal(result.selectedId,'t-closed');
 assert.match(result.html,/data-ticket="t-closed"/);
 assert.doesNotMatch(result.html,/data-ticket="t-open"/);
 assert.doesNotMatch(result.html,/data-ticket="t-unknown"/);
});
test('unknown status fails closed with an empty list',async()=>{
 const organ=createInboxOrgan({shop:channelShop(statusTickets)});
 await organ.ready();
 const result=await organ.selectStatus('snoozed');
 assert.equal(result.statusId,'snoozed');
 assert.equal(result.selectedId,null);
 assert.match(result.html,/No tickets yet/);
 assert.doesNotMatch(result.html,/data-ticket="t-/);
});
test('clearing the status restores every loaded row',async()=>{
 const organ=createInboxOrgan({shop:channelShop(statusTickets)});
 await organ.ready();
 await organ.selectStatus('closed');
 const result=await organ.selectStatus('');
 assert.equal(result.statusId,'');
 assert.match(result.html,/data-ticket="t-closed"/);
 assert.match(result.html,/data-ticket="t-open"/);
 assert.match(result.html,/data-ticket="t-unknown"/);
});
test('status control hides when every ticket is unknown',async()=>{
 const tickets=statusTickets.map(t=>({...t,status:'unknown'}));
 const result=await createInboxOrgan({shop:channelShop(tickets)}).ready();
 assert.doesNotMatch(result.html,/data-list-status/);
 assert.match(result.html,/data-ticket="t-open"/);
});
test('channel and status filters compose',async()=>{
 const organ=createInboxOrgan({shop:channelShop(statusTickets)});
 await organ.ready();
 await organ.selectChannel('email');
 const result=await organ.selectStatus('open');
 assert.match(result.html,/data-ticket="t-open"/);
 assert.doesNotMatch(result.html,/data-ticket="t-closed"/);
 assert.doesNotMatch(result.html,/data-ticket="t-unknown"/);
});
