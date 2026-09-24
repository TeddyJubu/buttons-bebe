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
 const consoleLinks = result.html.match(/<a\b[^>]*href="\/console\/"[^>]*>/g) || [];
 assert.equal(consoleLinks.length, 2, 'toolbar and error recovery links are present');
 for (const link of consoleLinks) assert.match(link, /target="_top"/, 'console navigation must leave an embedded inbox');
});
test('production client rejects a stale fixture response', async () => {
 const shop=createHelpdeskShop({client:{invoke:async () => ({ok:true,source:'sample',tickets:[{id:'t-ada-track'}]})}});
 await assert.rejects(shop.listTickets({}),/Preview data/);
});
test('helpdesk.capabilities wires operatorEmail and clears on failure', async () => {
  const client = {invoke: async (tool) => {
    if (tool === 'helpdesk.capabilities') return {ok:true, source:'inbox', operatorEmail:'Agent@Buttons.test', capabilities:{}};
    return {ok:true, source:'inbox', tickets:[], projection:{generatedAt:'one', stale:false}};
  }};
  const shop = createHelpdeskShop({client});
  await shop.getCapabilities();
  assert.equal(shop.operatorEmail, 'Agent@Buttons.test');
  client.invoke = async (tool) => {
    if (tool === 'helpdesk.capabilities') return {ok:true, source:'inbox', capabilities:{}};
    return {ok:true, source:'inbox', tickets:[]};
  };
  await shop.getCapabilities();
  assert.equal(shop.operatorEmail, '');
  client.invoke = async (tool) => {
    if (tool === 'helpdesk.capabilities') throw new Error('offline');
    return {ok:true, source:'inbox', tickets:[]};
  };
  await assert.rejects(shop.getCapabilities(), /offline/);
  assert.equal(shop.operatorEmail, '');
});

// The production shop's capability vocabulary must name every gate the organ
// consults, or refreshCapabilities can never flip that gate off: it only
// refreshes keys already present in the vocabulary.
test('the production capability vocabulary covers the createTicket gate', async () => {
  const shop = createHelpdeskShop({client:{invoke:async () => ({ok:true, source:'inbox', tickets:[]})}});
  assert.equal(typeof shop.capabilities.createTicket, 'boolean', 'createTicket is in the vocabulary');
  const organ = createInboxOrgan({shop, viewId:'all'});
  const result = await organ.ready();
  // The gate must be live in production: an absent service value flips it
  // off after refreshCapabilities, hiding the button.
  assert.doesNotMatch(result.html, /data-create-ticket/);
  const snap = await organ.createLocalTicket({customerName:'Ada', fromEmail:'ada@example.test', subject:'Hi', body:'Help', channel:'email'});
  assert.equal(snap.createError, 'Ticket creation is not enabled.');
});

// The live service reports createTicket (review_server.py CAPABILITIES) — the
// §2(7) local-only flow. The organ must surface the button and create into the
// browser store. The mock is closed-world: every tool outside the read
// vocabulary (capabilities/list_tickets/write_gate_status) throws, so a
// regression routing the create through any server RPC fails loudly instead
// of quietly passing — "nothing leaves the page" stays asserted, not assumed.
test('a live createTicket capability surfaces the New ticket button end to end', async () => {
  const invoked = [];
  const client = {invoke: async (tool) => {
    invoked.push(tool);
    if (tool === 'helpdesk.capabilities') return {ok:true, source:'inbox', capabilities:{listTickets:true, getTicket:true, createTicket:true}};
    if (tool === 'helpdesk.list_tickets') return {ok:true, source:'inbox', tickets:[], projection:{generatedAt:'gen-1', stale:false}};
    if (tool === 'helpdesk.write_gate_status') return {ok:true, source:'inbox', gorgiasEnabled:false, outboundEnabled:false};
    throw new Error(`unexpected tool invoked: ${tool}`);
  }};
  const shop = createHelpdeskShop({client});
  const backing = new Map();
  const organ = createInboxOrgan({shop, viewId:'all', storage:{
    getItem:k => backing.has(k) ? backing.get(k) : null,
    setItem:(k,v) => backing.set(k, String(v)),
    removeItem:k => backing.delete(k),
  }});
  const result = await organ.ready();
  assert.match(result.html, /data-create-ticket/, 'the New ticket button renders');
  assert.match(result.html, /New ticket/, 'the button is labeled');
  const snap = await organ.createLocalTicket({customerName:'Ada Lovelace', fromEmail:'ada@example.test', subject:'Hello', body:'A question', channel:'email'});
  assert.equal(snap.createError, '', 'the local create succeeds');
  assert.match(JSON.stringify(backing.get('bb-inbox-local-tickets-v1')), /Ada Lovelace/, 'the ticket lands in the browser store');
  // The create path must be pure browser state: no create-shaped RPC ever ran.
  assert.ok(!invoked.some(tool => /create|ingest|intake/i.test(tool)), 'no server tool took part in the create');
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
 const recovery = result.html.match(/<a\b[^>]*>Open support console<\/a>/);
 assert.ok(recovery, 'thread failure offers console recovery');
 assert.match(recovery[0], /target="_top"/, 'thread recovery must leave an embedded inbox');
});
test('stale empty projection announces delayed history without invented rows',async()=>{
  const shop=createHelpdeskShop({client:{invoke:async()=>({ok:true,tickets:[],projection:{generatedAt:'one',stale:true}})}});
  const result=await createInboxOrgan({shop}).ready();
  assert.match(result.html,/role="status">Observed history is stale/);
  assert.match(result.html,/data-history-refresh[^>]*>Refresh</,"the single banner carries a Refresh action");
  assert.match(result.html,/No tickets yet/);
  assert.equal(result.selectedId,null);
});
test('one stale banner carries the time and Refresh; thread and rail carry no stale copies', async () => {
  const ticket = {id:'gorgias:61003', projectionSource:true, historyIncomplete:true,
    customerName:'Ai Demo Multi', fromEmail:'ai-demo-multi@example.com', subject:'Cancel and refund order #1003',
    snippet:'Please cancel', status:'open', updatedAt:'2026-08-23T04:48:00Z',
    messages:[{id:'m1', fromAgent:false, name:'Ai Demo Multi', at:'2026-08-23T04:48:00Z', body:'Please cancel.'}],
    statusEvents:[],
    customerContext:{source:'canonical_webhook', status:'observed', conflict:false, observedAt:'2026-08-23T04:48:00Z',
      identity:{name:'Ai Demo Multi', email:'ai-demo-multi@example.com', phone:null, id:null}}};
  const epoch = Math.floor(Date.parse('2026-08-23T04:48:00Z')/1000);
  const shop = createHelpdeskShop({client:{invoke:async (tool)=>{
    if(tool==='helpdesk.capabilities') return {ok:true,source:'inbox',capabilities:{}};
    if(tool==='helpdesk.get_ticket') return {ok:true,source:'inbox',ticket,projection:{generatedAtEpoch:epoch,stale:true}};
    return {ok:true,source:'inbox',tickets:[ticket],projection:{generatedAtEpoch:epoch,stale:true}};
  }}});
  const organ = createInboxOrgan({shop, viewId:'all'});
  const result = await organ.ready();
  assert.equal(result.selectedId, 'gorgias:61003');
  assert.match(result.html, /Observed history is stale\. Last updated \d{1,2} Aug/);
  assert.match(result.html, /data-history-refresh[^>]*>Refresh</);
  const staleHits = result.html.match(/is stale|Stale snapshot|Snapshot is stale/g) || [];
  assert.equal(staleHits.length, 1, 'exactly one stale verdict on the page');
  assert.match(result.html, /Partial webhook history/, 'the thread keeps its distinct incompleteness note');
  assert.match(result.html, /This is a snapshot, not a live customer lookup/, 'the rail keeps its source line');
});
test('banner Refresh re-reads history without clearing the reply', async () => {
  let lists = 0;
  const ticket = {id:'gorgias:61003', projectionSource:true, customerName:'Ai Demo Multi',
    fromEmail:'ai-demo-multi@example.com', subject:'Cancel?', snippet:'Please cancel', status:'open',
    updatedAt:'2026-08-23T04:48:00Z', messages:[], statusEvents:[]};
  const epoch = Math.floor(Date.parse('2026-08-23T04:48:00Z')/1000);
  const shop = createHelpdeskShop({client:{invoke:async (tool)=>{
    if(tool==='helpdesk.capabilities') return {ok:true,source:'inbox',capabilities:{}};
    if(tool==='helpdesk.list_tickets') lists += 1;
    if(tool==='helpdesk.get_ticket') return {ok:true,source:'inbox',ticket,projection:{generatedAtEpoch:epoch,stale:true}};
    return {ok:true,source:'inbox',tickets:[ticket],projection:{generatedAtEpoch:epoch,stale:true}};
  }}});
  const organ = createInboxOrgan({shop, viewId:'all'});
  await organ.ready();
  organ.setBody('Typing a reply');
  const before = lists;
  const snap = await organ.refreshHistory();
  assert.ok(lists > before, 'list_tickets re-ran');
  assert.equal(snap.selectedId, 'gorgias:61003', 'the selection survives the refresh');
  assert.match(snap.html, /Typing a reply/, 'the typed reply survives the refresh');
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
test('channel facet hides when no ticket carries a channel but the builder stays',async()=>{
 // #36 cubic: the filter control is the seven-field builder, not just the
 // channel facet menu — with no channel data the facet entries hide, but
 // customer/updated/priority remain filterable, so the control remains.
 const plain=channelTickets.map(({channel,...rest})=>rest);
 const result=await createInboxOrgan({shop:channelShop(plain)}).ready();
 assert.doesNotMatch(result.html,/data-channel=/);
 assert.match(result.html,/data-list-filter/);
 assert.match(result.html,/data-ticket="t-email"/);
});
test('views funnel shows when the observed history offers several views',async()=>{
 const result=await createInboxOrgan({shop:channelShop(channelTickets)}).ready();
 assert.match(result.html,/data-list-inbox/);
 assert.match(result.html,/data-view="all"/);
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
 assert.doesNotMatch(result.html,/data-status-pick=/);
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
const assigneeTickets=[
 {id:'t-amy',customerName:'Amy Customer',subject:'Amy question',snippet:'Hi',status:'open',updatedAt:'2026-09-14T00:00:00Z',channel:'email',assignee:'amy@example.com',messages:[],statusEvents:[],projectionSource:true},
 {id:'t-bo',customerName:'Bo Customer',subject:'Bo question',snippet:'Hey',status:'open',updatedAt:'2026-09-13T00:00:00Z',channel:'email',assignee:'Bo Ex',messages:[],statusEvents:[],projectionSource:true},
 {id:'t-none',customerName:'Nobody Customer',subject:'Mystery',snippet:'Yo',status:'open',updatedAt:'2026-09-12T00:00:00Z',channel:'email',assignee:null,messages:[],statusEvents:[],projectionSource:true},
];
test('assignee menu lists people plus Unassigned',async()=>{
 const result=await createInboxOrgan({shop:channelShop(assigneeTickets)}).ready();
 assert.match(result.html,/data-assignee-pick="amy@example.com"/);
 assert.match(result.html,/data-assignee-pick="Bo Ex"/);
 assert.match(result.html,/data-assignee-pick="unassigned"/);
 assert.match(result.html,/Unassigned/);
 assert.match(result.html,/Everyone/);
 assert.match(result.html,/ticket-assignee/);
});
test('selectAssignee filters to that person',async()=>{
 const organ=createInboxOrgan({shop:channelShop(assigneeTickets)});
 await organ.ready();
 const result=await organ.selectAssignee('amy@example.com');
 assert.equal(result.assigneeId,'amy@example.com');
 assert.equal(result.selectedId,'t-amy');
 assert.match(result.html,/data-ticket="t-amy"/);
 assert.doesNotMatch(result.html,/data-ticket="t-bo"/);
 assert.doesNotMatch(result.html,/data-ticket="t-none"/);
});
test('Unassigned filter shows only blank assignees',async()=>{
 const organ=createInboxOrgan({shop:channelShop(assigneeTickets)});
 await organ.ready();
 const result=await organ.selectAssignee('unassigned');
 assert.match(result.html,/data-ticket="t-none"/);
 assert.doesNotMatch(result.html,/data-ticket="t-amy"/);
 assert.doesNotMatch(result.html,/data-ticket="t-bo"/);
});
test('unknown assignee fails closed with an empty list',async()=>{
 const organ=createInboxOrgan({shop:channelShop(assigneeTickets)});
 await organ.ready();
 const result=await organ.selectAssignee('ghost@example.com');
 assert.equal(result.selectedId,null);
 assert.match(result.html,/No tickets yet/);
});
test('clearing the assignee restores every loaded row',async()=>{
 const organ=createInboxOrgan({shop:channelShop(assigneeTickets)});
 await organ.ready();
 await organ.selectAssignee('unassigned');
 const result=await organ.selectAssignee('');
 assert.equal(result.assigneeId,'');
 assert.match(result.html,/data-ticket="t-amy"/);
 assert.match(result.html,/data-ticket="t-none"/);
});
test('assignee control hides when every ticket is blank',async()=>{
 const tickets=assigneeTickets.map(t=>({...t,assignee:null}));
 const result=await createInboxOrgan({shop:channelShop(tickets)}).ready();
 assert.doesNotMatch(result.html,/data-assignee-pick=/);
 assert.match(result.html,/data-ticket="t-amy"/);
});
const tagTickets=[
 {id:'t-vip',customerName:'Vip Customer',subject:'Vip question',snippet:'Hi',status:'open',updatedAt:'2026-09-14T00:00:00Z',channel:'email',assignee:'amy@example.com',tags:['vip','urgent'],messages:[],statusEvents:[],projectionSource:true},
 {id:'t-urgent',customerName:'Urgent Customer',subject:'Urgent question',snippet:'Hey',status:'open',updatedAt:'2026-09-13T00:00:00Z',channel:'email',assignee:'bo@example.com',tags:['urgent'],messages:[],statusEvents:[],projectionSource:true},
 {id:'t-plain',customerName:'Plain Customer',subject:'Plain question',snippet:'Yo',status:'open',updatedAt:'2026-09-12T00:00:00Z',channel:'email',assignee:'amy@example.com',tags:[],messages:[],statusEvents:[],projectionSource:true},
];
test('tag menu lists observed tags with counts',async()=>{
 const result=await createInboxOrgan({shop:channelShop(tagTickets)}).ready();
 assert.match(result.html,/data-tag-pick="vip"/);
 assert.match(result.html,/All tags/);
 assert.doesNotMatch(result.html,/data-tag-pick="ghost"/);
 assert.match(result.html,/ticket-tag">urgent<\/span>/);
});
test('selectTag filters to tickets carrying the tag',async()=>{
 const organ=createInboxOrgan({shop:channelShop(tagTickets)});
 await organ.ready();
 const result=await organ.selectTag('vip');
 assert.equal(result.tagId,'vip');
 assert.equal(result.selectedId,'t-vip');
 assert.match(result.html,/data-ticket="t-vip"/);
 assert.doesNotMatch(result.html,/data-ticket="t-urgent"/);
 assert.doesNotMatch(result.html,/data-ticket="t-plain"/);
});
test('tag filter composes with assignee and status',async()=>{
 const organ=createInboxOrgan({shop:channelShop(tagTickets)});
 await organ.ready();
 await organ.selectAssignee('amy@example.com');
 const result=await organ.selectTag('urgent');
 assert.match(result.html,/data-ticket="t-vip"/);
 assert.doesNotMatch(result.html,/data-ticket="t-urgent"/);
 assert.doesNotMatch(result.html,/data-ticket="t-plain"/);
});
test('unknown tag fails closed with an empty list',async()=>{
 const organ=createInboxOrgan({shop:channelShop(tagTickets)});
 await organ.ready();
 const result=await organ.selectTag('ghost');
 assert.equal(result.selectedId,null);
 assert.match(result.html,/No tickets yet/);
});
test('clearing the tag restores every loaded row',async()=>{
 const organ=createInboxOrgan({shop:channelShop(tagTickets)});
 await organ.ready();
 await organ.selectTag('vip');
 const result=await organ.selectTag('');
 assert.equal(result.tagId,'');
 assert.match(result.html,/data-ticket="t-vip"/);
 assert.match(result.html,/data-ticket="t-urgent"/);
 assert.match(result.html,/data-ticket="t-plain"/);
});
test('tag control hides and rows stay clean when no tags exist',async()=>{
 const tickets=tagTickets.map(t=>({...t,tags:[]}));
 const result=await createInboxOrgan({shop:channelShop(tickets)}).ready();
 assert.doesNotMatch(result.html,/data-tag-pick=/);
 assert.doesNotMatch(result.html,/ticket-tag/);
 assert.match(result.html,/data-ticket="t-vip"/);
});
test('row shows at most three tags with a plus-N overflow badge',async()=>
{
 const many=[...tagTickets.map(t=>({...t,tags:['a','b','c','d','e']}))][0];
 const result=await createInboxOrgan({shop:channelShop([many])}).ready();
 assert.match(result.html,/ticket-tag">a<\/span>/);
 assert.match(result.html,/ticket-tag">b<\/span>/);
 assert.match(result.html,/ticket-tag">c<\/span>/);
 assert.doesNotMatch(result.html,/ticket-tag">d<\/span>/);
 assert.match(result.html,/ticket-tag-more">\+2<\/span>/);
});
test('gorgias priority badge renders without touching AI priority',async()=>{
 const withGorgias=[{...tagTickets[0],gorgiasPriority:'urgent',severity:'normal',tags:[]}];
 const result=await createInboxOrgan({shop:channelShop(withGorgias)}).ready();
 assert.match(result.html,/ticket-gorgias-priority" title="Gorgias priority">urgent<\/span>/);
 assert.doesNotMatch(result.html,/ticket-severity/);
});
test('no gorgias priority means no badge and no invented value',async()=>{
 const result=await createInboxOrgan({shop:channelShop(tagTickets)}).ready();
 assert.doesNotMatch(result.html,/ticket-gorgias-priority/);
 assert.doesNotMatch(result.html,/Gorgias priority/);
});
test('thread header shows gorgias priority without touching AI priority',async()=>{
 const withGorgias=[{...tagTickets[0],gorgiasPriority:'urgent',severity:'normal',tags:[]}];
 const result=await createInboxOrgan({shop:channelShop(withGorgias)}).ready();
  assert.match(result.html,/status-line" title="Gorgias priority"><span class="status-dot is-urgent" aria-hidden="true"><\/span>urgent<\/span>/);
 assert.doesNotMatch(result.html,/ticket-severity/);
});
test('spam trashed and snoozed tickets badge but are never hidden',async()=>{
 const flagged=[{...tagTickets[0],gorgiasSpam:true,gorgiasTrashed:true,gorgiasSnoozed:true}];
 const result=await createInboxOrgan({shop:channelShop(flagged)}).ready();
 assert.match(result.html,/ticket-gorgias-spam" title="Marked as spam in Gorgias">Spam<\/span>/);
 assert.match(result.html,/ticket-gorgias-trashed" title="Trashed in Gorgias">Trashed<\/span>/);
 assert.match(result.html,/ticket-gorgias-snoozed" title="Snoozed in Gorgias">Snoozed<\/span>/);
 assert.match(result.html,/data-ticket="t-vip"/);
  assert.match(result.html,/status-line" title="Marked as spam in Gorgias">[\s\S]*?Spam<\/span>/);
  assert.match(result.html,/status-line" title="Trashed in Gorgias">[\s\S]*?Trashed<\/span>/);
  assert.match(result.html,/status-line" title="Snoozed in Gorgias"><span class="status-dot is-snoozed" aria-hidden="true"><\/span>Snoozed<\/span>/);
});
test('flagged tickets stay visible under a matching channel filter',async()=>{
 const flagged=[{...tagTickets[0],gorgiasSpam:true,gorgiasTrashed:false,gorgiasSnoozed:true}];
 const organ=createInboxOrgan({shop:channelShop(flagged)});
 await organ.ready();
 const result=await organ.selectChannel('email');
 assert.equal(result.channelId,'email');
 assert.match(result.html,/data-ticket="t-vip"/);
 assert.match(result.html,/ticket-gorgias-spam/);
});
test('no state flags means no state badges and no invented value',async()=>{
 const result=await createInboxOrgan({shop:channelShop(tagTickets)}).ready();
 assert.doesNotMatch(result.html,/ticket-gorgias-spam/);
 assert.doesNotMatch(result.html,/ticket-gorgias-trashed/);
 assert.doesNotMatch(result.html,/ticket-gorgias-snoozed/);
 assert.doesNotMatch(result.html,/Marked as spam in Gorgias/);
 assert.match(result.html,/data-ticket="t-vip"/);
});
const filterBarTickets=[
 {id:'t-vip',customerName:'Vip Customer',subject:'Vip question',snippet:'Hi',status:'open',updatedAt:'2026-09-14T00:00:00Z',channel:'email',assignee:'amy@example.com',tags:['vip','urgent'],messages:[],statusEvents:[],projectionSource:true},
 {id:'t-urgent',customerName:'Urgent Customer',subject:'Urgent question',snippet:'Hey',status:'open',updatedAt:'2026-09-13T00:00:00Z',channel:'chat',assignee:'bo@example.com',tags:['urgent'],messages:[],statusEvents:[],projectionSource:true},
 {id:'t-plain',customerName:'Plain Customer',subject:'Plain question',snippet:'Yo',status:'closed',updatedAt:'2026-09-12T00:00:00Z',channel:'email',assignee:'amy@example.com',tags:[],messages:[],statusEvents:[],projectionSource:true},
 {id:'t-other',customerName:'Other Customer',subject:'Other question',snippet:'Ho',status:'open',updatedAt:'2026-09-11T00:00:00Z',channel:'email',assignee:'amy@example.com',tags:['vip'],messages:[],statusEvents:[],projectionSource:true},
];
test('filter bar offers channel status assignee and tag together',async()=>{
 const result=await createInboxOrgan({shop:channelShop(filterBarTickets)}).ready();
 assert.match(result.html,/data-list-filter/);
 assert.match(result.html,/data-channel="email"/);
 assert.match(result.html,/data-status-pick="open"/);
 assert.match(result.html,/data-assignee-pick="amy@example.com"/);
 assert.match(result.html,/data-tag-pick="vip"/);
 assert.match(result.html,/data-ticket="t-vip"/);
 assert.match(result.html,/data-ticket="t-urgent"/);
 assert.match(result.html,/data-ticket="t-plain"/);
 assert.match(result.html,/data-ticket="t-other"/);
});
test('all four filters compose to a single ticket',async()=>{
 const organ=createInboxOrgan({shop:channelShop(filterBarTickets)});
 await organ.ready();
 await organ.selectChannel('email');
 await organ.selectStatus('open');
 await organ.selectAssignee('amy@example.com');
 const result=await organ.selectTag('urgent');
 assert.equal(result.channelId,'email');
 assert.equal(result.statusId,'open');
 assert.equal(result.assigneeId,'amy@example.com');
 assert.equal(result.tagId,'urgent');
 assert.equal(result.selectedId,'t-vip');
 assert.match(result.html,/data-ticket="t-vip"/);
 assert.doesNotMatch(result.html,/data-ticket="t-urgent"/);
 assert.doesNotMatch(result.html,/data-ticket="t-plain"/);
 assert.doesNotMatch(result.html,/data-ticket="t-other"/);
});
test('impossible four-filter combo fails closed with an honest empty list',async()=>{
 const organ=createInboxOrgan({shop:channelShop(filterBarTickets)});
 await organ.ready();
 await organ.selectChannel('email');
 await organ.selectStatus('closed');
 await organ.selectAssignee('bo@example.com');
 const result=await organ.selectTag('vip');
 assert.equal(result.selectedId,null);
 assert.match(result.html,/No tickets yet/);
 assert.doesNotMatch(result.html,/data-ticket="t-/);
});
test('clearing all four filters restores every loaded row',async()=>{
 const organ=createInboxOrgan({shop:channelShop(filterBarTickets)});
 await organ.ready();
 await organ.selectChannel('email');
 await organ.selectStatus('open');
 await organ.selectAssignee('amy@example.com');
 await organ.selectTag('urgent');
 await organ.selectChannel('');
 await organ.selectStatus('');
 await organ.selectAssignee('');
 const result=await organ.selectTag('');
 assert.equal(result.channelId,'');
 assert.equal(result.statusId,'');
 assert.equal(result.assigneeId,'');
 assert.equal(result.tagId,'');
 assert.match(result.html,/data-ticket="t-vip"/);
 assert.match(result.html,/data-ticket="t-urgent"/);
 assert.match(result.html,/data-ticket="t-plain"/);
 assert.match(result.html,/data-ticket="t-other"/);
});
test('switching views resets channel status assignee and tag',async()=>{
 const organ=createInboxOrgan({shop:channelShop(filterBarTickets)});
 await organ.ready();
 await organ.selectChannel('email');
 await organ.selectStatus('open');
 await organ.selectAssignee('amy@example.com');
 await organ.selectTag('urgent');
 const result=await organ.selectView('all');
 assert.equal(result.channelId,'');
 assert.equal(result.statusId,'');
 assert.equal(result.assigneeId,'');
 assert.equal(result.tagId,'');
 assert.match(result.html,/data-ticket="t-vip"/);
 assert.match(result.html,/data-ticket="t-urgent"/);
 assert.match(result.html,/data-ticket="t-plain"/);
 assert.match(result.html,/data-ticket="t-other"/);
});
test('flagged tickets stay visible under status assignee and tag filters',async()=>{
 const flagged=[{...filterBarTickets[0],gorgiasSpam:true,gorgiasTrashed:false,gorgiasSnoozed:true}];
 const organ=createInboxOrgan({shop:channelShop(flagged)});
 await organ.ready();
 await organ.selectStatus('open');
 await organ.selectAssignee('amy@example.com');
 const result=await organ.selectTag('vip');
 assert.match(result.html,/data-ticket="t-vip"/);
 assert.match(result.html,/ticket-gorgias-spam/);
 assert.match(result.html,/ticket-gorgias-snoozed/);
});
