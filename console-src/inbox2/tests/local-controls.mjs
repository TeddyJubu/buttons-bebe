import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const base=process.env.INBOX_TEST_URL||'http://127.0.0.1:8878';
const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
const context=await browser.newContext({viewport:{width:1280,height:800},timezoneId:'Asia/Dhaka'});
const page=await context.newPage();
await page.clock.setFixedTime(new Date('2026-10-05T12:00:00Z'));
page.setDefaultTimeout(10000);
const dir=fs.mkdtempSync(path.join(os.tmpdir(),'bb-local-controls-'));
const stamp='2026-10-05T10:00:00Z';
let next=false,order=false;let listPages=false,observedStatus='open',observedUpdated='';
let releaseCapabilities;const heldCapabilities=new Promise(resolve=>{releaseCapabilities=resolve;});
const readTools=['helpdesk.capabilities','helpdesk.get_ticket','helpdesk.get_messages','helpdesk.list_tickets'];
function exactKeys(value,keys,label){assert.deepEqual(Object.keys(value||{}).sort(),[...keys].sort(),`${label}: exact response/request keys`);}
function assertReadRequest(payload,label='read request'){
  assert(readTools.includes(payload?.tool),`${label}: only the four named read tools are allowed`);
  const args=payload.arguments||{};
  const keys={
    'helpdesk.capabilities':[],
    'helpdesk.get_ticket':['ticketId'],
    'helpdesk.get_messages':args.cursor===undefined?['ticketId']:['cursor','ticketId'],
    'helpdesk.list_tickets':['assignee','channel','limit','offset','oldest','priority','query','tag','view'],
  }[payload.tool];
  exactKeys(args,keys,`${label} ${payload.tool} arguments`);
  if(payload.tool==='helpdesk.get_ticket'||payload.tool==='helpdesk.get_messages')assert.match(args.ticketId,/^gorgias:[1-9][0-9]{0,17}$/,'provider reads require a literal Gorgias ticket ID');
  if(payload.tool==='helpdesk.get_messages'&&args.cursor!==undefined)assert.equal(typeof args.cursor,'string','message cursor is a string');
}
function assertReadResponse(tool,result,label='read response',{allowAvailabilityReason=false}={}){
  assert.equal(result.ok,true,`${label} ${tool} is successful`);assert.equal(result.source,'gorgias_api',`${label} ${tool} identifies its source`);
  if(tool==='helpdesk.capabilities'){
    exactKeys(result,['capabilities','ok','readOnly','source'],`${label} capabilities`);assert.equal(result.readOnly,true);
    exactKeys(result.capabilities,['createTicket','getTicket','listTickets','sendReply'],`${label} capability flags`);
    assert.deepEqual(result.capabilities,{listTickets:true,getTicket:true,sendReply:false,createTicket:false});return;
  }
  if(tool==='helpdesk.get_ticket'){
    exactKeys(result,['ok','source','ticket'],`${label} ticket`);assert.equal(typeof result.ticket?.id,'string');return;
  }
  if(tool==='helpdesk.get_messages'){
    exactKeys(result,['messages','nextCursor','ok','source'],`${label} message page`);assert(Array.isArray(result.messages));assert(result.nextCursor===null||typeof result.nextCursor==='string');return;
  }
  exactKeys(result,['categoryAvailability','nextOffset','ok','operatorEmail','projection','source','tickets','total'],`${label} ticket list`);
  assert(Array.isArray(result.tickets));assert(Number.isInteger(result.total)&&result.total>=0);assert(result.nextOffset===null||Number.isInteger(result.nextOffset));assert.equal(typeof result.operatorEmail,'string');
  exactKeys(result.categoryAvailability,['assigned','snoozed','spam','trash'],`${label} category availability`);
  for(const field of ['assigned','snoozed','spam','trash']){
    const value=result.categoryAvailability[field];
    if(field==='snoozed'&&allowAvailabilityReason&&value&&typeof value==='object'){
      exactKeys(value,['available','reason'],`${label} compatibility snoozed availability`);assert.equal(value.available,false);assert.equal(typeof value.reason,'string');assert(value.reason.length>0);
    }else assert.equal(typeof value,'boolean',`${label} ${field} availability is boolean`);
  }
  exactKeys(result.projection,['complete','generatedAt','stale','syncing','ticketCount'],`${label} projection`);
  for(const field of ['complete','stale','syncing'])assert.equal(typeof result.projection[field],'boolean');
  assert(result.projection.generatedAt===null||typeof result.projection.generatedAt==='string');assert(Number.isInteger(result.projection.ticketCount));
}
function listEnvelope({tickets=[],total=tickets.length,nextOffset=null,operatorEmail='',categoryAvailability={assigned:Boolean(operatorEmail),snoozed:false,spam:false,trash:false},generatedAt=stamp,stale=false,syncing=false,complete=true,allowAvailabilityReason=false}={}){
  const result={ok:true,source:'gorgias_api',tickets,total,nextOffset,operatorEmail,categoryAvailability,projection:{generatedAt,stale,syncing,complete,ticketCount:total}};
  assertReadResponse('helpdesk.list_tickets',result,'synthetic',{allowAvailabilityReason});return result;
}
async function installReadConsumerProbe(page){
  await page.addInitScript(()=>{
    window.__inboxReadResponses=[];
    const originalFetch=window.fetch.bind(window);
    window.fetch=async(...args)=>{
      const response=await originalFetch(...args),url=new URL(typeof args[0]==='string'?args[0]:args[0].url,location.href);
      if(url.pathname==='/inbox/api/helpdesk'){
        const request=JSON.parse(args[1]?.body||'{}'),originalJson=response.json.bind(response);
        response.json=async()=>{const result=await originalJson();setTimeout(()=>window.__inboxReadResponses.push({tool:request.tool,args:request.arguments||{},result}),0);return result;};
      }
      return response;
    };
  });
}
async function waitForConsumedResponse(page,before,tool,args){
  await page.waitForFunction(({before,tool,args})=>window.__inboxReadResponses.slice(before).some(entry=>entry.tool===tool&&JSON.stringify(entry.args)===JSON.stringify(args)),{before,tool,args});
}
async function waitForRenderedMessage(page,id,body){
  await page.waitForFunction(({id,body})=>Array.from(document.querySelectorAll(`[data-message-id="${CSS.escape(id)}"] .message-body`)).some(node=>node.textContent===body),{id,body});
}
const fixture=()=>({id:'gorgias:123',subject:'Original subject retained',customerName:'Example Customer',fromEmail:'example@example.invalid',status:observedStatus,gorgiasPriority:'normal',assigneeEmail:'support@example.invalid',channel:'email',updatedAt:observedUpdated||stamp,lastMessageAt:next?'2026-10-05T10:01:00Z':stamp,syncedAt:stamp,readonlyDraft:Array.from({length:80},(_,i)=>`Suggestion paragraph ${i+1}.`).join('\n\n'),draftSourceMessageId:'m1',draftProcessedAt:stamp,messages:Array.from({length:30},(_,i)=>({id:next&&i===29?'new-message':`m${i}`,fromAgent:false,body:`Message ${i}. `.repeat(20),at:next&&i===29?'2026-10-05T10:01:00Z':stamp,...(i===1?{normalized:{display_text:'Historical quoted request only',current_text:'',history_available:true}}:i===2?{normalized:{display_text:'Legacy message without current-text field'}}:{}),...(i===0?{normalized:{display_text:'Question #12345\n\n> Earlier Bengali history বাংলা',current_text:'Question #12345',original_content:'<script>bad()</script><p>Question #12345</p>',original_field:'body_html',history_available:true},attachments:[{name:'Example.png',url:`${base}/fixture.png`,content_type:'image/png'},{name:'Unsafe',url:'javascript:alert(1)',content_type:'image/png'}]}:{})})),shopifyRail:order?{status:'observed',order:{name:'#12345',lineItems:{nodes:[]}},customer:{displayName:'Example Customer'}}:{status:'unavailable'},redoDetails:{status:'observed',fetchedAt:stamp,orders:{'#12345':{status:'observed',returns:[{status:'pending',refund_amount:'15',tracking_url:'javascript:alert(1)'}],observedAt:stamp}}}});
const apiCalls=[],consoleCalls=[],errors=[];
page.on('pageerror',e=>errors.push(e.message));
await page.route('**/console/api/**',async route=>{consoleCalls.push(route.request().url());await route.fulfill({status:403,json:{error:'inbox_read_only'}});});
await page.route('**/inbox/api/helpdesk',async route=>{
  const payload=route.request().postDataJSON();apiCalls.push(payload);assertReadRequest(payload,'main browser');const tool=payload.tool,t=fixture();
  if(tool==='helpdesk.capabilities'){
    await heldCapabilities;const result={ok:true,source:'gorgias_api',readOnly:true,capabilities:{listTickets:true,getTicket:true,sendReply:false,createTicket:false}};assertReadResponse(tool,result,'main browser');await route.fulfill({json:result});return;
  }
  if(tool==='helpdesk.get_ticket'){
    const result={ok:true,source:'gorgias_api',ticket:t};assertReadResponse(tool,result,'main browser');await route.fulfill({json:result});return;
  }
  if(tool==='helpdesk.get_messages'){
    const result={ok:true,source:'gorgias_api',messages:[],nextCursor:null};assertReadResponse(tool,result,'main browser');await route.fulfill({json:result});return;
  }
  const matches=!(payload.arguments.view==='closed'&&t.status!=='closed'||payload.arguments.view==='open'&&t.status!=='open');
  const tickets=matches?(payload.arguments.offset?[{...t,id:'gorgias:124'}]:[t]):[];
  const result=listEnvelope({tickets,total:matches?(listPages?2:1):0,nextOffset:matches&&listPages&&!payload.arguments.offset?9:null,operatorEmail:'support@example.invalid',categoryAvailability:{assigned:true,snoozed:true,spam:true,trash:true}});
  await route.fulfill({json:result});
});
async function checkLocalSnoozedAccess(){
  const probe=await browser.newContext({viewport:{width:1280,height:800},timezoneId:'Asia/Dhaka'});
  const future='2026-10-07T12:00:00Z',expired='2026-10-04T12:00:00Z',stamp='2026-10-05T10:00:00Z';
  async function run({name,kind='observed',availability=false,value=future,includeSnooze=true,count=1,orphan=false,reset=false,filtersAndPages=false,failRefresh=false,providerAvailable=false}={}){
    const p=await probe.newPage();await p.clock.setFixedTime(new Date('2026-10-05T12:00:00Z'));
    const calls=[],writes=[],pageErrors=[];let failSnoozed=false,compatibilityAvailabilityReturn;
    const records={},locals=[];
    for(let i=0;i<count;i++){
      const id=`gorgias:${501+i}`;
      const summary={id,browserOverride:true,savedAt:Date.parse(stamp),customerName:`Saved customer ${i}`,fromEmail:`saved-${i}@example.invalid`,subject:`Saved snooze ${i}`,snippet:'Saved browser observation',status:'open',gorgiasPriority:'normal',assigneeEmail:'',channel:'email',updatedAt:stamp,lastMessageAt:stamp,lastMessageId:`saved-${i}`,snoozedUntil:'',snoozeUntil:'',syncedAt:stamp};
      if(!orphan&&kind==='observed')records[id]={observed:summary,...(includeSnooze?{snooze:{value,by:'synthetic operator',at:Date.parse(stamp)}}:{})};
    }
    if(kind==='local'){
      const id='local:snoozed-probe';
      locals.push({id,localOnly:true,customerName:'Private local customer',subject:'Private local snooze',fromEmail:'',status:'open',channel:'local',updatedAt:stamp,snippet:'Private local message',messages:[]});
      records[id]=includeSnooze?{snooze:{value,by:'synthetic operator',at:Date.parse(stamp)}}:{};
    }
    if(orphan)records['gorgias:501']={snooze:{value,by:'synthetic operator',at:Date.parse(stamp)}};
    const storedSeed=JSON.parse(JSON.stringify(records));
    if(name==='blank observed snooze'){
      assert.equal(Object.hasOwn(storedSeed['gorgias:501'],'snooze'),true,'blank control stores a snooze record before browser initialization');
      assert.equal(Object.hasOwn(storedSeed['gorgias:501'].snooze,'value'),true,'blank snooze value is an own property before browser initialization');
      assert.equal(storedSeed['gorgias:501'].snooze.value,'','blank control stores the literal empty value before browser initialization');
    }
    if(name==='absent observed snooze record')assert.equal(Object.hasOwn(storedSeed['gorgias:501'],'snooze'),false,'absent control has no snooze record before browser initialization');
    await p.addInitScript(({seedRecords,seedLocals})=>{
      localStorage.setItem('bb-inbox-ticket-state-v1',JSON.stringify({version:1,records:seedRecords}));
      localStorage.setItem('bb-inbox-local-tickets-v1',JSON.stringify(seedLocals));
    },{seedRecords:storedSeed,seedLocals:locals});
    p.on('pageerror',error=>pageErrors.push(error.message));
    await p.route('**/console/api/**',async route=>{writes.push(route.request().url());await route.fulfill({status:403,json:{error:'inbox_read_only'}});});
    await p.route('**/inbox/api/helpdesk',async route=>{
      const payload=route.request().postDataJSON(),tool=payload.tool,args=payload.arguments||{};calls.push(payload);assertReadRequest(payload,name);
      assert(!String(args.ticketId||'').startsWith('local:'),`${name}: local-only ID reached provider read`);
      const ticket={id:'gorgias:501',subject:'Provider ticket',customerName:'Provider customer',fromEmail:'provider@example.invalid',status:'open',channel:'email',gorgiasPriority:'normal',updatedAt:stamp,lastMessageAt:stamp,syncedAt:stamp,messages:[],shopifyRail:{status:'unavailable'}};
      assert.equal(Object.hasOwn(ticket,'snoozeUntil'),false,'provider fixture must omit snooze fields');
      if(tool==='helpdesk.capabilities'){
        const result={ok:true,source:'gorgias_api',readOnly:true,capabilities:{listTickets:true,getTicket:true,sendReply:false,createTicket:false}};assertReadResponse(tool,result,name);await route.fulfill({json:result});return;
      }
      if(tool==='helpdesk.list_tickets'){
        if(failSnoozed&&args.view==='snoozed'){await route.fulfill({status:503,json:{message:'Synthetic provider list failure'}});return;}
        const result=listEnvelope({tickets:[],total:0,operatorEmail:'',categoryAvailability:{assigned:false,snoozed:providerAvailable?true:availability,spam:false,trash:false},allowAvailabilityReason:true});
        if(!providerAvailable&&availability&&typeof availability==='object')compatibilityAvailabilityReturn=structuredClone(result.categoryAvailability.snoozed);
        await route.fulfill({json:result});return;
      }
      if(tool==='helpdesk.get_ticket'){const result={ok:true,source:'gorgias_api',ticket};assertReadResponse(tool,result,name);await route.fulfill({json:result});return;}
      if(tool==='helpdesk.get_messages'){const result={ok:true,source:'gorgias_api',messages:[],nextCursor:null};assertReadResponse(tool,result,name);await route.fulfill({json:result});return;}
      assert.fail(`${name}: exhaustive read-tool dispatch missed ${tool}`);
    });
    await p.goto(`${base}/inbox/?ticket=gorgias%3A501`);await p.locator('.ticket-title').waitFor();
    await p.waitForFunction(()=>document.querySelector('#count')?.textContent.startsWith('Page'));
    const snoozed=p.locator('[data-view="snoozed"]');
    const parsedSnooze=Date.parse(value),hasLocalSnooze=!orphan&&includeSnooze&&['observed','local'].includes(kind)&&Number.isFinite(parsedSnooze)&&parsedSnooze>Date.parse('2026-10-05T12:00:00Z');
    assert.equal(await snoozed.isDisabled(),!providerAvailable&&!hasLocalSnooze,`${name}: only provider availability or a valid future local member enables Snoozed`);
    assert.equal(await p.locator('[data-view="assigned"]').isDisabled(),true,`${name}: Assigned still requires an operator`);
    assert.equal(await p.locator('[data-view="spam"]').isDisabled(),true,`${name}: local Snoozed membership must not enable Spam`);
    assert.equal(await p.locator('[data-view="trash"]').isDisabled(),true,`${name}: local Snoozed membership must not enable Trash`);
    if(!providerAvailable&&hasLocalSnooze){
      const title=await snoozed.getAttribute('title');
      assert.match(title,/browser-only/i,`${name}: explain browser-only Snoozed access`);
      assert.match(title,/Gorgias.*snooze fields.*unavailable/i,`${name}: explain missing Gorgias snooze fields`);
      if(typeof availability==='object'){
        assert.deepEqual(compatibilityAvailabilityReturn,availability,`${name}: the exact legacy availability reason is returned by the intercepted list response`);
        assert.equal(title,`Browser-only Snoozed tickets are available. Gorgias snooze fields are unavailable: ${availability.reason}`,`${name}: the rendered UI uses the exact returned reason`);
      }
    }
    if(reset){
      await snoozed.click();await p.waitForFunction(()=>new URLSearchParams(location.search).get('view')==='snoozed');
      await p.locator('[data-ticket="gorgias:501"]').click();await p.locator('.ticket-actions-menu summary').click();await p.locator('[data-action="reset-local"]').click();
      assert.equal(await snoozed.isDisabled(),true,`${name}: resetting the last local override removes local Snoozed access`);
    }else if(filtersAndPages){
      const ids=()=>p.locator('.ticket-row').evaluateAll(nodes=>nodes.map(node=>node.dataset.ticket));
      await p.locator('[data-action="page-next"]').click();await p.waitForFunction(()=>document.querySelector('.ticket-row')?.dataset.ticket==='gorgias:510');
      assert.deepEqual(await ids(),['gorgias:510','gorgias:511','gorgias:512']);
      await p.locator('[data-select-ticket="gorgias:510"]').check();await snoozed.click();
      await p.waitForFunction(()=>new URLSearchParams(location.search).get('view')==='snoozed'&&document.querySelector('.ticket-row')?.dataset.ticket==='gorgias:501');
      assert.deepEqual(await ids(),['gorgias:501','gorgias:502','gorgias:503','gorgias:504','gorgias:505','gorgias:506','gorgias:507','gorgias:508','gorgias:509'],'view click resets page to the first local page');
      assert.equal(await p.locator('#selection-count').textContent(),'0 selected on this page','view click clears selection');
      assert((await p.locator('#count').textContent()).includes('0 of 0 loaded Gorgias shown · 0 total · 9 browser rows shown of 12 matching'));
      await p.locator('[data-action="page-next"]').click();await p.waitForFunction(()=>document.querySelector('.ticket-row')?.dataset.ticket==='gorgias:510');assert.deepEqual(await ids(),['gorgias:510','gorgias:511','gorgias:512']);
      await p.locator('#search').fill('no saved snoozed item matches this');await p.waitForFunction(()=>new URLSearchParams(location.search).get('q')==='no saved snoozed item matches this'&&!document.querySelector('.ticket-row'));
      assert.equal(await snoozed.isDisabled(),false,`${name}: query and current page do not control Snoozed availability`);
      await p.locator('.list-filters summary').click();await p.locator('[data-filter="priority"]').selectOption('low');await p.waitForFunction(()=>!document.querySelector('.ticket-row'));
      assert.equal(await snoozed.isDisabled(),false,`${name}: filters do not control Snoozed availability`);
      await p.locator('#search').fill('');await p.locator('[data-filter="priority"]').selectOption('');
      await p.waitForFunction(()=>!new URLSearchParams(location.search).get('q'));
      failSnoozed=failRefresh;await p.locator('[data-action="refresh"]').click();
      if(failRefresh){await p.waitForFunction(()=>document.querySelector('#refresh span')?.textContent.includes('Sync delayed · Retry'));
        assert.equal(await snoozed.isDisabled(),false);assert.deepEqual((await ids()).length?await ids():[],['gorgias:501','gorgias:502','gorgias:503','gorgias:504','gorgias:505','gorgias:506','gorgias:507','gorgias:508','gorgias:509']);
        assert((await p.locator('#count').textContent()).includes('0 of 0 loaded Gorgias shown · 0 total · 9 browser rows shown of 12 matching'));
        assert(await p.locator('#sync-status').evaluate(el=>el.classList.contains('sync-delayed')),'provider failure must retain delayed-refresh status');}
    }else if(hasLocalSnooze&&!providerAvailable){
      await snoozed.click();await p.waitForFunction(()=>new URLSearchParams(location.search).get('view')==='snoozed');
      const expected=kind==='local'?'local:snoozed-probe':'gorgias:501';
      await p.waitForFunction(id=>document.querySelector(`[data-ticket="${id}"]`)!==null,expected);
      assert.deepEqual(await p.locator('.ticket-row').evaluateAll(nodes=>nodes.map(node=>node.dataset.ticket)),[expected],`${name}: Snoozed shows the literal browser row`);
      assert((await p.locator('#count').textContent()).includes('0 of 0 loaded Gorgias shown · 0 total'));
      assert((await p.locator('#count').textContent()).includes('1 browser rows shown of 1 matching'));
    }
    assert.equal(calls.filter(call=>call.tool==='helpdesk.list_tickets'&&call.arguments.view==='snoozed').length>0,hasLocalSnooze||reset||filtersAndPages,`${name}: entering Snoozed must issue its normal read request`);
    assert.equal(calls.some(call=>String(call.arguments.ticketId||'').startsWith('local:')),false,`${name}: local IDs never reach provider read`);
    assert.deepEqual(writes,[],`${name}: local organization never calls console mutations`);assert.deepEqual(pageErrors,[],`${name}: browser page errors`);
    await p.close();
  }
  await run({name:'observed future / provider false',kind:'observed',availability:false});
  await run({name:'observed future / provider object false',kind:'observed',availability:{available:false,reason:'Gorgias omitted snooze_datetime'}});
  await run({name:'local-only future / provider false',kind:'local',availability:false});
  await run({name:'local-only future / provider object false',kind:'local',availability:{available:false,reason:'Gorgias omitted snooze_datetime'}});
  await run({name:'expired observed snooze',value:expired});
  await run({name:'invalid observed snooze',value:'not-a-time'});
  await run({name:'blank observed snooze',value:''});
  await run({name:'absent observed snooze record',includeSnooze:false});
  await run({name:'orphan snooze without observed summary',orphan:true});
  await run({name:'reset last observed snooze',reset:true});
  await run({name:'provider view counts, pagination, filters and failed refresh',count:12,availability:{available:false,reason:'Gorgias omitted snooze_datetime'},filtersAndPages:true,failRefresh:true});
  await run({name:'provider-available Snoozed',kind:'none',value:'',providerAvailable:true});
  await probe.close();
}
async function checkOpenedMessageEvidence(){
  const probe=await browser.newContext({viewport:{width:1280,height:800}}),p=await probe.newPage();
  p.setDefaultTimeout(10000);
  const id='gorgias:601',messageAt='2026-10-05T00:00:00Z',updatedAt='2026-10-05T01:00:00Z';
  const original={id,subject:'Missing list activity',customerName:'Synthetic reader',status:'open',gorgiasPriority:'normal',assigneeEmail:'reader@example.invalid',updatedAt};
  const other={...original,id:'gorgias:602',subject:'Other synthetic ticket',lastMessageAt:messageAt,lastMessageId:'z1',messages:[{id:'z1',at:messageAt,body:'Other selected ticket',fromAgent:false}]};
  let row={...original},detail={...original,messages:[{id:'m1',at:messageAt,body:'Actual observed message',fromAgent:false}],messagesNextCursor:'older-page-1',historyIncomplete:true};
  const calls=[],writes=[],pageErrors=[];
  let held=null;
  await installReadConsumerProbe(p);
  p.on('pageerror',error=>pageErrors.push(error.message));
  await p.route('**/console/api/**',async route=>{writes.push(route.request().url());await route.fulfill({status:403,json:{error:'read_only'}});});
  await p.route('**/inbox/api/helpdesk',async route=>{
    const payload=route.request().postDataJSON();calls.push(payload);assertReadRequest(payload,'opened-message evidence');
    assert(!String(payload.arguments.ticketId||'').startsWith('local:'));
    if(payload.tool==='helpdesk.get_ticket'){
      const ticket=structuredClone(payload.arguments.ticketId===id?detail:other),pending=held;
      if(pending){held=null;pending.started(route.request());await pending.promise;}
      const result={ok:true,source:'gorgias_api',ticket};assertReadResponse(payload.tool,result,'opened-message evidence');await route.fulfill({json:result});return;
    }
    if(payload.tool==='helpdesk.capabilities'){
      const result={ok:true,source:'gorgias_api',readOnly:true,capabilities:{listTickets:true,getTicket:true,sendReply:false,createTicket:false}};assertReadResponse(payload.tool,result,'opened-message evidence');await route.fulfill({json:result});return;
    }
    if(payload.tool==='helpdesk.get_messages'){
      assert.equal(payload.arguments.ticketId,id);assert.equal(payload.arguments.cursor,'older-page-1','older-message request must carry the exact current cursor');
      const result={ok:true,source:'gorgias_api',messages:[{id:'m0',at:'2026-10-04T23:00:00Z',body:'Controlled older page message',fromAgent:false}],nextCursor:null};assertReadResponse(payload.tool,result,'opened-message evidence');await route.fulfill({json:result});return;
    }
    if(payload.tool==='helpdesk.list_tickets'){
      const result=listEnvelope({tickets:[row,other],total:2,operatorEmail:'reader@example.invalid',categoryAvailability:{assigned:true,snoozed:true,spam:true,trash:true}});await route.fulfill({json:result});return;
    }
    assert.fail(`opened-message evidence handler missed allowed read tool ${payload.tool}`);
  });
  const button=p.locator(`[data-ticket="${id}"]`);
  const marker=()=>p.evaluate(id=>JSON.parse(localStorage.getItem('bb-inbox-read-v1'))?.records[id],id);
  const listOnly=async()=>{
    const before=await p.evaluate(()=>window.__inboxReadResponses.length);
    const requestPromise=p.waitForRequest(request=>request.url().endsWith('/inbox/api/helpdesk')&&request.postDataJSON()?.tool==='helpdesk.list_tickets');
    await p.locator('[data-view="all"]').click();const request=await requestPromise;
    const response=await request.response();assert(response,'the exact list request receives a response');assert.equal(await response.finished(),null,'list response must finish without a transport error');
    await waitForConsumedResponse(p,before,'helpdesk.list_tickets',request.postDataJSON().arguments);
  };
  const refresh=async(expectedBody,expectedMessageId='m1')=>{
    const before=await p.evaluate(()=>window.__inboxReadResponses.length);
    const requestPromise=p.waitForRequest(request=>request.url().endsWith('/inbox/api/helpdesk')&&request.postDataJSON()?.tool==='helpdesk.get_ticket'&&request.postDataJSON()?.arguments?.ticketId===id);
    await p.locator('[data-action="refresh"]').click();const request=await requestPromise;
    const response=await request.response();assert(response,'the exact detail request receives a response');assert.equal(await response.finished(),null,'detail response must finish without a transport error');
    await waitForConsumedResponse(p,before,'helpdesk.get_ticket',request.postDataJSON().arguments);
    if(expectedBody)await waitForRenderedMessage(p,expectedMessageId,expectedBody);
  };
  const hold=()=>{
    let release,started;const requested=new Promise(resolve=>{started=resolve;});
    held={started:request=>started(request),promise:new Promise(resolve=>{release=resolve;})};
    return {requested,release,finish:async(expectedBody='')=>{
      const request=await requested;assertReadRequest(request.postDataJSON(),'held response');
      const before=await p.evaluate(()=>window.__inboxReadResponses.length),responsePromise=p.waitForResponse(response=>response.request()===request);
      release();const response=await responsePromise;assert.equal(await response.finished(),null,'the exact held ticket response must finish successfully');
      await waitForConsumedResponse(p,before,'helpdesk.get_ticket',request.postDataJSON().arguments);
      if(expectedBody)await waitForRenderedMessage(p,'m1',expectedBody);
    }};
  };
  try{
    await p.goto(`${base}/inbox/`);await p.locator('.ticket-title').waitFor();
    assert.equal(await button.getAttribute('class'),'ticket-row is-read','opening dated detail must immediately read a list missing both message time and ID');
    await p.locator('.ticket-actions-menu summary').click();
    assert.equal(await p.locator('[data-action="toggle-read"]').textContent(),'Mark unread');
    const firstMarker=await marker();
    assert.equal(firstMarker.message,'m1');assert.equal(firstMarker.activity,messageAt);
    await p.screenshot({path:path.join(dir,'opened-missing-activity-read.png')});
    const olderBefore=await p.evaluate(()=>window.__inboxReadResponses.length);
    const olderRequestPromise=p.waitForRequest(request=>request.url().endsWith('/inbox/api/helpdesk')&&request.postDataJSON()?.tool==='helpdesk.get_messages');
    await p.locator('[data-action="older-messages"]').click();const olderRequest=await olderRequestPromise;
    assertReadRequest(olderRequest.postDataJSON(),'controlled older page');
    const olderResponse=await olderRequest.response();assert(olderResponse,'the exact older-page request receives a response');assert.equal(await olderResponse.finished(),null,'the controlled older page must finish successfully');
    await waitForConsumedResponse(p,olderBefore,'helpdesk.get_messages',olderRequest.postDataJSON().arguments);
    await waitForRenderedMessage(p,'m0','Controlled older page message');
    assert.equal(await p.locator('[data-action="older-messages"]').count(),0,'the explicit null nextCursor ends message pagination');
    assert.deepEqual(await marker(),firstMarker,'loading an older page does not move the read watermark');
    await listOnly();assert.equal(await button.getAttribute('class'),'ticket-row is-read','unchanged deficient list refresh reuses accepted real message evidence');
    assert.deepEqual(await marker(),firstMarker);
    row={...original,updatedAt:'2026-10-05T02:00:00Z'};detail={...detail,updatedAt:row.updatedAt,messages:[{id:'m1',at:messageAt,body:'Metadata-only refresh of m1',fromAgent:false}]};
    await refresh('Metadata-only refresh of m1');assert.equal(await button.getAttribute('class'),'ticket-row is-read','same-message metadata refresh stays read');
    assert.deepEqual(await marker(),firstMarker);
    row={...row,lastMessageAt:'2026-10-05T03:00:00Z',lastMessageId:'m2'};
    detail={...row,messages:[{id:'m2',at:row.lastMessageAt,body:'A genuine new message',fromAgent:false}]};
    await refresh('A genuine new message','m2');assert.equal(await button.getAttribute('class'),'ticket-row is-unread','genuine new message invalidates the old marker');
    assert.equal(await p.locator('[data-action="toggle-read"]').textContent(),'Mark read');assert.deepEqual(await marker(),firstMarker);
    detail={...original,messages:[{id:'m1',at:messageAt,body:'Delayed old detail',fromAgent:false}]};
    await refresh('Delayed old detail');assert.equal(await button.getAttribute('class'),'ticket-row is-unread','stale detail cannot overwrite newer list evidence');
    assert.deepEqual(await marker(),firstMarker);
    const heldBody='Held response rendered after newer list completion',pendingRace=hold();detail={...original,messages:[{id:'m1',at:messageAt,body:heldBody,fromAgent:false}]};
    const heldRequestPromise=pendingRace.requested;
    await p.locator('[data-action="refresh"]').click();const heldRequest=await heldRequestPromise;
    assertReadRequest(heldRequest.postDataJSON(),'held newer-list race');
    row={...row,updatedAt:'2026-10-05T04:00:00Z',lastMessageAt:'2026-10-05T04:00:00Z',lastMessageId:'m3'};
    await listOnly();
    assert.equal(await p.locator(`[data-message-id="m1"] .message-body`).filter({hasText:heldBody}).count(),0,'held detail is not rendered before release');
    assert.equal(heldRequest.postDataJSON().arguments.ticketId,id);
    // The exact held response must finish and its unique returned text must replace the pre-existing m1 body.
    await pendingRace.finish(heldBody);
    console.log(`Race barrier reached: exact held get_ticket response finished, browser consumed it, and rendered unique m1 body for ${id}.`);
    assert.equal(await button.getAttribute('class'),'ticket-row is-unread','delayed detail cannot erase an independently newer list observation');
    assert.deepEqual(await marker(),firstMarker);
    await p.evaluate(({id,row})=>localStorage.setItem('bb-inbox-ticket-state-v1',JSON.stringify({version:1,records:{[id]:{title:{value:'Saved newer title'},observed:{...row,browserOverride:true,savedAt:1}}}})),{id,row});
    await p.reload();await p.locator('.ticket-title').waitFor();
    assert.equal(await button.getAttribute('class'),'ticket-row is-unread','opening older detail preserves a saved newer observation');
    assert.equal(await p.evaluate(id=>JSON.parse(localStorage.getItem('bb-inbox-ticket-state-v1')).records[id].observed.lastMessageId,id),'m3');
    const savedMarker=await marker();
    let pending=hold();
    await p.locator('[data-action="refresh"]').click();await pending.requested;
    await p.locator(`[data-ticket="${other.id}"]`).click();await p.locator('[data-message-id="z1"]').waitFor();await pending.finish();
    assert.equal(await p.locator('[data-message-id="z1"]').count(),1,'delayed refresh cannot change the newly selected ticket');
    assert.equal(await button.getAttribute('class'),'ticket-row is-unread');assert.deepEqual(await marker(),savedMarker);
    pending=hold();await button.click();await pending.requested;
    await p.locator(`[data-ticket="${other.id}"]`).click();await p.locator('[data-message-id="z1"]').waitFor();await pending.finish();
    assert.equal(await p.locator('[data-message-id="z1"]').count(),1,'delayed selection cannot change the newly selected ticket');
    assert.equal(await button.getAttribute('class'),'ticket-row is-unread');assert.deepEqual(await marker(),savedMarker,'discarded selection must not write an old read marker');
    await p.evaluate(()=>localStorage.removeItem('bb-inbox-ticket-state-v1'));
    row={...original,lastMessageAt:messageAt,lastMessageId:'m1'};
    detail={...original,messages:[{id:'m1',at:messageAt,body:'Actual observed message',fromAgent:false}]};
    await p.goto(`${base}/inbox/?ticket=${encodeURIComponent(id)}`);await p.locator('.ticket-title').waitFor();
    assert.equal(await button.getAttribute('class'),'ticket-row is-read');const missingIdMarker=await marker();
    detail={...original,lastMessageAt:messageAt,messages:[{id:'',at:messageAt,body:'Same message with absent identity',fromAgent:false}]};
    await refresh();assert.equal(await button.getAttribute('class'),'ticket-row is-read','unchanged actual activity with absent incoming ID preserves known read identity');
    assert.deepEqual(await marker(),missingIdMarker);
    detail={...detail,updatedAt:'2026-10-05T02:00:00Z',lastMessageAt:'2026-10-05T02:00:00Z',messages:[{id:'',at:'2026-10-05T02:00:00Z',body:'A newer actual message without ID',fromAgent:false}]};
    await refresh();assert.equal(await button.getAttribute('class'),'ticket-row is-unread','strictly newer actual activity without ID must clear stale read identity');
    await p.locator('.ticket-actions-menu summary').click();
    assert.equal(await p.locator('[data-action="toggle-read"]').textContent(),'Mark read');assert.deepEqual(await marker(),missingIdMarker);
    await p.screenshot({path:path.join(dir,'newer-missing-identity-unread.png')});
    assert.deepEqual(writes,[]);assert.deepEqual(pageErrors,[]);
    fs.writeFileSync(path.join(dir,'opened-message-evidence.json'),JSON.stringify({scope:'Rendered Chromium with isolated browser storage and synthetic intercepted read responses only',cases:['immediate-open missing time and identity','unchanged deficient list','unchanged activity missing identity','newer activity missing identity','metadata-only same message','genuine new message','stale detail','delayed detail/newer list','saved newer observation','delayed refresh after selection change','delayed selection response'],calls,consoleWrites:writes,pageErrors},null,2));
    console.log(`Passed rendered opened-message evidence, eleven focused cases and zero mutations. Evidence ${dir}`);
  }finally{await probe.close();}
}
async function checkFreshMessageEvidenceControls(){
  const probe=await browser.newContext({viewport:{width:1280,height:800},timezoneId:'Asia/Dhaka'});
  async function runFreshShape(shape){
    const p=await probe.newPage();p.setDefaultTimeout(10000);
    const id=shape==='false'?'gorgias:681':'gorgias:682',oldAt='2026-10-05T00:00:00Z',freshAt='2026-10-05T01:00:00Z';
    const row={id,subject:'Incomplete provider row',customerName:'Fresh proof',fromEmail:'fresh@example.invalid',status:'open',gorgiasPriority:'normal',channel:'email',updatedAt:oldAt};
    let ticket={id,subject:row.subject,customerName:row.customerName,fromEmail:row.fromEmail,status:'open',gorgiasPriority:'normal',channel:'email',updatedAt:freshAt,lastMessageAt:freshAt,lastMessageId:'fresh-m1',syncedAt:freshAt,messages:[{id:'fresh-m1',at:freshAt,body:'Fresh detail enriches the incomplete row',fromAgent:false}],shopifyRail:{status:'unavailable'}};
    if(shape==='false')ticket.syncStale=false;
    const calls=[],writes=[],pageErrors=[];await installReadConsumerProbe(p);
    p.on('pageerror',error=>pageErrors.push(error.message));
    await p.route('**/console/api/**',async route=>{writes.push(route.request().url());await route.fulfill({status:403,json:{error:'read_only'}});});
    await p.route('**/inbox/api/helpdesk',async route=>{
      const payload=route.request().postDataJSON();calls.push(payload);assertReadRequest(payload,`fresh ${shape} control`);
      if(payload.tool==='helpdesk.capabilities'){
        const result={ok:true,source:'gorgias_api',readOnly:true,capabilities:{listTickets:true,getTicket:true,sendReply:false,createTicket:false}};assertReadResponse(payload.tool,result,`fresh ${shape} control`);await route.fulfill({json:result});return;
      }
      if(payload.tool==='helpdesk.get_ticket'){
        assert.equal(payload.arguments.ticketId,id);const result={ok:true,source:'gorgias_api',ticket:structuredClone(ticket)};assertReadResponse(payload.tool,result,`fresh ${shape} control`);await route.fulfill({json:result});return;
      }
      if(payload.tool==='helpdesk.get_messages'){
        assert.equal(payload.arguments.ticketId,id);const result={ok:true,source:'gorgias_api',messages:[],nextCursor:null};assertReadResponse(payload.tool,result,`fresh ${shape} control`);await route.fulfill({json:result});return;
      }
      if(payload.tool==='helpdesk.list_tickets'){
        const result=listEnvelope({tickets:[structuredClone(row)],total:1,operatorEmail:'fresh@example.invalid'});await route.fulfill({json:result});return;
      }
      assert.fail(`fresh ${shape} handler missed ${payload.tool}`);
    });
    try{
      const before=0;
      const detailRequestPromise=p.waitForRequest(request=>request.url().endsWith('/inbox/api/helpdesk')&&request.postDataJSON()?.tool==='helpdesk.get_ticket'&&request.postDataJSON()?.arguments?.ticketId===id);
      const listRequestPromise=p.waitForRequest(request=>request.url().endsWith('/inbox/api/helpdesk')&&request.postDataJSON()?.tool==='helpdesk.list_tickets');
      await p.goto(base+'/inbox/?ticket='+encodeURIComponent(id));
      const [detailRequest,listRequest]=await Promise.all([detailRequestPromise,listRequestPromise]);
      const [detailResponse,listResponse]=await Promise.all([detailRequest.response(),listRequest.response()]);
      assert.equal(await detailResponse.finished(),null);assert.equal(await listResponse.finished(),null);
      await Promise.all([waitForConsumedResponse(p,before,'helpdesk.get_ticket',detailRequest.postDataJSON().arguments),waitForConsumedResponse(p,before,'helpdesk.list_tickets',listRequest.postDataJSON().arguments)]);
      await waitForRenderedMessage(p,'fresh-m1','Fresh detail enriches the incomplete row');
      assert.equal(await p.locator(`[data-ticket="${id}"]`).getAttribute('class'),'ticket-row is-read',`explicitly ${shape==='false'?'fresh syncStale:false':'unflagged fresh'} detail enriches the incomplete list row`);
      const firstMarker=await p.evaluate(id=>JSON.stringify(JSON.parse(localStorage.getItem('bb-inbox-read-v1')).records[id]),id);
      assert.equal(JSON.parse(firstMarker).message,'fresh-m1');assert.equal(JSON.parse(firstMarker).activity,freshAt);

      ticket={...ticket,updatedAt:'2026-10-05T02:00:00Z',lastMessageAt:'2026-10-05T02:00:00Z',lastMessageId:'stale-m2',syncStale:true,messages:[{id:'stale-m2',at:'2026-10-05T02:00:00Z',body:'Unverified stale detail after a proven read',fromAgent:false}]};
      const refreshBefore=await p.evaluate(()=>window.__inboxReadResponses.length),refreshRequestPromise=p.waitForRequest(request=>request.url().endsWith('/inbox/api/helpdesk')&&request.postDataJSON()?.tool==='helpdesk.get_ticket'&&request.postDataJSON()?.arguments?.ticketId===id);
      await p.locator('[data-action="refresh"]').click();const refreshRequest=await refreshRequestPromise,refreshResponse=await refreshRequest.response();assert(refreshResponse,'the exact stale-control request receives a response');
      assert.equal(await refreshResponse.finished(),null,'stale response transport must finish before checking the application');
      await waitForConsumedResponse(p,refreshBefore,'helpdesk.get_ticket',refreshRequest.postDataJSON().arguments);
      await waitForRenderedMessage(p,'stale-m2','Unverified stale detail after a proven read');
      console.log(`Stale-detail barrier reached: exact get_ticket response finished, browser consumed it, and rendered unique stale-m2 body for ${id}.`);
      assert.equal(await p.locator(`[data-ticket="${id}"]`).getAttribute('class'),'ticket-row is-read','stale detail cannot overwrite the already-rendered row evidence');
      assert.equal(await p.evaluate(id=>JSON.stringify(JSON.parse(localStorage.getItem('bb-inbox-read-v1')).records[id]),id),firstMarker,'stale refresh cannot move the existing read marker');
      assert.equal(calls.filter(call=>call.tool==='helpdesk.capabilities').length,1,'the exact read-only capabilities contract is consumed at startup');
      assert.deepEqual(writes,[]);assert.deepEqual(pageErrors,[]);
    }finally{await p.close();}
  }
  try{await runFreshShape('false');await runFreshShape('absent');}finally{await probe.close();}
}
async function checkStaleOpeningPreservesBrowserEvidence(){
  const probe=await browser.newContext({viewport:{width:1280,height:800},timezoneId:'Asia/Dhaka'}),p=await probe.newPage();
  p.setDefaultTimeout(10000);
  const id='gorgias:701',priorAt='2026-10-05T01:00:00Z',stamp='2026-10-05T02:00:00Z';
  const observed={browserOverride:true,savedAt:1,id,customerName:'Saved customer',fromEmail:'saved@example.invalid',subject:'Saved observed subject',snippet:'Saved observed snippet',status:'open',gorgiasPriority:'normal',assigneeEmail:'',assignee:'',channel:'email',updatedAt:priorAt,lastMessageAt:'',lastMessageId:'',syncedAt:priorAt,tags:[],spam:false,trashed:false};
  let row={id,subject:'Unverified list subject',customerName:'Unverified list customer',fromEmail:'saved@example.invalid',status:'open',gorgiasPriority:'normal',channel:'email',updatedAt:stamp,syncedAt:stamp,syncStale:true,snippet:'Unverified stale summary'};
  let ticket={id,subject:'Provider subject',customerName:'Provider customer',fromEmail:'saved@example.invalid',status:'open',gorgiasPriority:'normal',channel:'email',updatedAt:stamp,lastMessageAt:'2026-10-05T01:50:00Z',lastMessageId:'stale-m1',syncedAt:stamp,syncStale:true,messages:[{id:'stale-m1',at:'2026-10-05T01:50:00Z',body:'Readable but unverified detail',fromAgent:false}],shopifyRail:{status:'unavailable'}};
  const calls=[],writes=[],pageErrors=[];
  const stateBefore=JSON.stringify({version:1,records:{[id]:{title:{value:'Saved browser title'},observed}}});
  const readBefore=JSON.stringify({version:1,records:{[id]:{read:false,kind:'message',message:'saved-unread',activity:priorAt,at:1}}});
  await installReadConsumerProbe(p);
  await p.addInitScript(({state,read})=>{localStorage.setItem('bb-inbox-ticket-state-v1',state);localStorage.setItem('bb-inbox-read-v1',read);},{state:stateBefore,read:readBefore});
  p.on('pageerror',error=>pageErrors.push(error.message));
  await p.route('**/console/api/**',async route=>{writes.push(route.request().url());await route.fulfill({status:403,json:{error:'read_only'}});});
  await p.route('**/inbox/api/helpdesk',async route=>{
    const payload=route.request().postDataJSON();calls.push(payload);assertReadRequest(payload,'stale saved-observation case');
    assert(!String(payload.arguments?.ticketId||'').startsWith('local:'),'local-only IDs never reach provider read');
    if(payload.tool==='helpdesk.capabilities'){
      const result={ok:true,source:'gorgias_api',readOnly:true,capabilities:{listTickets:true,getTicket:true,sendReply:false,createTicket:false}};assertReadResponse(payload.tool,result,'stale saved-observation case');await route.fulfill({json:result});return;
    }
    if(payload.tool==='helpdesk.get_ticket'){
      assert.equal(payload.arguments.ticketId,id);const result={ok:true,source:'gorgias_api',ticket:structuredClone(ticket)};assertReadResponse(payload.tool,result,'stale saved-observation case');await route.fulfill({json:result});return;
    }
    if(payload.tool==='helpdesk.get_messages'){
      assert.equal(payload.arguments.ticketId,id);const result={ok:true,source:'gorgias_api',messages:[],nextCursor:null};assertReadResponse(payload.tool,result,'stale saved-observation case');await route.fulfill({json:result});return;
    }
    if(payload.tool==='helpdesk.list_tickets'){
      const result=listEnvelope({tickets:[structuredClone(row)],total:1,operatorEmail:'saved@example.invalid'});await route.fulfill({json:result});return;
    }
    assert.fail('stale saved-observation handler missed '+payload.tool);
  });
  try{
    const before=0;
    const detailRequestPromise=p.waitForRequest(request=>request.url().endsWith('/inbox/api/helpdesk')&&request.postDataJSON()?.tool==='helpdesk.get_ticket'&&request.postDataJSON()?.arguments?.ticketId===id);
    const listRequestPromise=p.waitForRequest(request=>request.url().endsWith('/inbox/api/helpdesk')&&request.postDataJSON()?.tool==='helpdesk.list_tickets');
    await p.goto(base+'/inbox/?ticket='+encodeURIComponent(id));
    const [detailRequest,listRequest]=await Promise.all([detailRequestPromise,listRequestPromise]);
    const [detailResponse,listResponse]=await Promise.all([detailRequest.response(),listRequest.response()]);
    assert.equal(await detailResponse.finished(),null,'stale open detail response must finish successfully');assert.equal(await listResponse.finished(),null,'initial stale list response must finish successfully');
    await Promise.all([waitForConsumedResponse(p,before,'helpdesk.get_ticket',detailRequest.postDataJSON().arguments),waitForConsumedResponse(p,before,'helpdesk.list_tickets',listRequest.postDataJSON().arguments)]);
    await waitForRenderedMessage(p,'stale-m1','Readable but unverified detail');
    await p.locator('[data-ticket="'+id+'"]').waitFor();
    assert.equal(await p.locator('#live-ticket-sync').textContent(),'Refresh delayed · Showing the last successful read','stale details remain readable and visibly labelled');
    assert.equal(await p.locator('[data-ticket="'+id+'"]').getAttribute('class'),'ticket-row is-unread','opening explicitly stale details cannot auto-mark the row read');
    assert.equal(await p.evaluate(()=>localStorage.getItem('bb-inbox-read-v1')),readBefore,'automatic open must preserve the exact prior manual read-marker bytes');
    assert.equal(await p.evaluate(()=>localStorage.getItem('bb-inbox-ticket-state-v1')),stateBefore,'stale open and stale list must preserve the exact saved observation/title bytes');
    assert.equal(await p.locator('[data-ticket="'+id+'"] .row-name').textContent(),'Saved customer','stale list summary cannot replace the saved customer name');
    assert.equal(await p.locator('[data-ticket="'+id+'"] .row-subject').textContent(),'Saved browser title','stale list summary cannot replace the local title');

    ticket={...ticket,updatedAt:'2026-10-05T02:10:00Z',lastMessageAt:'2026-10-05T02:05:00Z',lastMessageId:'stale-m2',messages:[{id:'stale-m2',at:'2026-10-05T02:05:00Z',body:'Stale refresh detail remains readable',fromAgent:false}]};
    const refreshBefore=await p.evaluate(()=>window.__inboxReadResponses.length),refreshRequestPromise=p.waitForRequest(request=>request.url().endsWith('/inbox/api/helpdesk')&&request.postDataJSON()?.tool==='helpdesk.get_ticket'&&request.postDataJSON()?.arguments?.ticketId===id);
    await p.locator('[data-action="refresh"]').click();const refreshRequest=await refreshRequestPromise,refreshResponse=await refreshRequest.response();assert(refreshResponse,'the exact stale refresh request receives a response');
    assert.equal(await refreshResponse.finished(),null,'stale refresh detail response must finish successfully');await waitForConsumedResponse(p,refreshBefore,'helpdesk.get_ticket',refreshRequest.postDataJSON().arguments);await waitForRenderedMessage(p,'stale-m2','Stale refresh detail remains readable');
    assert.equal(await p.locator('[data-ticket="'+id+'"]').getAttribute('class'),'ticket-row is-unread','stale refresh cannot change the rendered read state');
    assert.equal(await p.evaluate(()=>localStorage.getItem('bb-inbox-read-v1')),readBefore,'stale refresh preserves exact read-marker bytes');
    assert.equal(await p.evaluate(()=>localStorage.getItem('bb-inbox-ticket-state-v1')),stateBefore,'stale refresh preserves exact observation bytes');

    row={...row,updatedAt:'2026-10-06T03:00:00Z',customerName:'Later unverified list name',subject:'Later unverified list subject',snippet:'Later unverified list snippet'};
    ticket={...ticket,updatedAt:'2026-10-06T03:00:00Z',lastMessageAt:'2026-10-06T02:55:00Z',lastMessageId:'stale-m3',messages:[{id:'stale-m3',at:'2026-10-06T02:55:00Z',body:'Stale detail during list refresh',fromAgent:false}]};
    const listBefore=await p.evaluate(()=>window.__inboxReadResponses.length),listRefreshRequestPromise=p.waitForRequest(request=>request.url().endsWith('/inbox/api/helpdesk')&&request.postDataJSON()?.tool==='helpdesk.list_tickets'),listDetailRequestPromise=p.waitForRequest(request=>request.url().endsWith('/inbox/api/helpdesk')&&request.postDataJSON()?.tool==='helpdesk.get_ticket'&&request.postDataJSON()?.arguments?.ticketId===id);
    await p.locator('[data-action="refresh"]').click();const [listRefreshRequest,listDetailRequest]=await Promise.all([listRefreshRequestPromise,listDetailRequestPromise]);
    const [listRefreshResponse,listDetailResponse]=await Promise.all([listRefreshRequest.response(),listDetailRequest.response()]);
    assert.equal(await listRefreshResponse.finished(),null,'stale list response must finish successfully');assert.equal(await listDetailResponse.finished(),null,'detail paired with stale list response must finish successfully');
    await Promise.all([waitForConsumedResponse(p,listBefore,'helpdesk.list_tickets',listRefreshRequest.postDataJSON().arguments),waitForConsumedResponse(p,listBefore,'helpdesk.get_ticket',listDetailRequest.postDataJSON().arguments)]);
    await waitForRenderedMessage(p,'stale-m3','Stale detail during list refresh');
    assert.equal(await p.locator('[data-ticket="'+id+'"] .row-name').textContent(),'Saved customer','stale list refresh keeps the saved observation rendered');
    assert.equal(await p.locator('[data-ticket="'+id+'"] .row-subject').textContent(),'Saved browser title');
    assert.equal(await p.locator('[data-ticket="'+id+'"]').getAttribute('class'),'ticket-row is-unread');
    assert.equal(await p.evaluate(()=>localStorage.getItem('bb-inbox-read-v1')),readBefore,'stale list refresh preserves exact read-marker bytes');
    assert.equal(await p.evaluate(()=>localStorage.getItem('bb-inbox-ticket-state-v1')),stateBefore,'stale list refresh preserves exact saved-observation bytes');

    await p.locator('.ticket-actions-menu summary').click();await p.locator('[data-action="toggle-read"]').click();
    assert.equal(await p.locator('[data-action="toggle-read"]').textContent(),'Mark unread','manual Mark read remains available on stale detail');
    await p.locator('[data-action="toggle-read"]').click();assert.equal(await p.locator('[data-action="toggle-read"]').textContent(),'Mark read','manual Mark unread remains available on stale detail');
    const manualMarker=await p.evaluate(id=>JSON.parse(localStorage.getItem('bb-inbox-read-v1')).records[id],id);assert.equal(manualMarker.read,false);assert.equal(manualMarker.message,'stale-m3','manual unread writes the selected message watermark');
    assert.equal(calls.filter(call=>call.tool==='helpdesk.capabilities').length,1,'startup consumes one read-only capabilities response');
    assert.deepEqual(writes,[],'stale detail and manual local read controls issue no provider mutations');assert.deepEqual(pageErrors,[]);
  }finally{await probe.close();}
}
await checkOpenedMessageEvidence();
await checkFreshMessageEvidenceControls();
await checkStaleOpeningPreservesBrowserEvidence();
await checkLocalSnoozedAccess();
await installReadConsumerProbe(page);
const capabilityBefore=0;
const capabilityRequestPromise=page.waitForRequest(request=>request.url().endsWith('/inbox/api/helpdesk')&&request.postDataJSON()?.tool==='helpdesk.capabilities');
await page.goto(`${base}/inbox/?ticket=gorgias%3A123`);
await page.locator('.ticket-title').waitFor();
assert.equal(await page.locator('.ticket-title').textContent(),'New Ticket');
await page.waitForFunction(()=>!document.querySelector('[data-view="assigned"]').disabled);
const capabilitiesResponse=page.waitForResponse(response=>response.url().endsWith('/inbox/api/helpdesk')&&response.request().postDataJSON()?.tool==='helpdesk.capabilities',{timeout:30000});
const capabilityRequest=await capabilityRequestPromise;assertReadRequest(capabilityRequest.postDataJSON(),'held startup capabilities');
assert.equal(await page.locator('[data-action="review-send"]').isVisible(),false,'startup read-only capabilities never expose customer sending');
releaseCapabilities();const capabilityResponse=await capabilitiesResponse;
assert.equal(await capabilityResponse.finished(),null,'held startup capabilities response finishes successfully');
const capabilityResult=await capabilityResponse.json();assertReadResponse('helpdesk.capabilities',capabilityResult,'held startup capabilities');
await waitForConsumedResponse(page,capabilityBefore,'helpdesk.capabilities',capabilityRequest.postDataJSON().arguments);
await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
assert.equal(await page.locator('[data-view="assigned"]').isDisabled(),false,'late capabilities without operatorEmail must not erase configured fixture identity');
async function visibleComposer(){const boxes=await page.locator('#reply,[data-action="copy-reply"]').evaluateAll(els=>els.map(el=>({top:el.getBoundingClientRect().top,bottom:el.getBoundingClientRect().bottom,right:el.getBoundingClientRect().right,left:el.getBoundingClientRect().left})));for(const r of boxes)assert(r.top>=0&&r.bottom<=await page.evaluate(()=>innerHeight)&&r.left>=0&&r.right<=await page.evaluate(()=>innerWidth),JSON.stringify(r));}
assert.equal(await page.locator('.original-evidence').count(),1);assert.equal(await page.locator('.message script').count(),0);assert.equal(await page.locator('.original-evidence pre').textContent(),'<script>bad()</script><p>Question #12345</p>');assert.equal(await page.locator('.message-attachment').count(),1);assert((await page.locator('.quoted-email .message-body').first().textContent()).includes('বাংলা'));
assert.equal(await page.locator('.message[data-message-id="m1"] > .message-body').count(),0,'empty current_text must not render historical request as new text');
assert.equal(await page.locator('.message[data-message-id="m1"] > .message-note').first().textContent(),'No new message text.');
assert.equal(await page.locator('.message[data-message-id="m1"] .quoted-email .message-body').textContent(),'Historical quoted request only');
assert.equal(await page.locator('.message[data-message-id="m1"] .quoted-email').evaluate(el=>el.open),false);
assert.equal(await page.locator('.message[data-message-id="m2"] > .message-body').textContent(),'Legacy message without current-text field','undefined current_text still uses available display text');
for(const [width,height] of [[1280,800],[390,844],[640,450]]){await page.setViewportSize({width,height});await visibleComposer();assert(await page.locator('[data-action="use-draft"]').evaluate(el=>el.getBoundingClientRect().bottom<=innerHeight));await page.locator('[data-action="expand-draft"]').click();await visibleComposer();await page.locator('[data-action="expand-draft"]').click();await page.screenshot({path:path.join(dir,`anchored-${width}.png`)});}
await page.setViewportSize({width:1280,height:800});
await page.locator('#reply').fill('My private reply');
await page.locator('#reply').evaluate(el=>{el.focus();el.setSelectionRange(3,8);});
order=true;
await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
await page.waitForFunction(()=>document.title.startsWith('Order 12345'));
assert.equal(await page.locator('#reply').inputValue(),'My private reply');
assert.deepEqual(await page.locator('#reply').evaluate(el=>({focused:el===document.activeElement,start:el.selectionStart,end:el.selectionEnd})),{focused:true,start:3,end:8});
assert.equal(await page.locator('.redo-section').count(),1);
assert.equal(await page.locator('.redo-section a').count(),0);
await page.locator('.ticket-actions-menu summary').click();
await page.evaluate(()=>{const records={version:1,records:{'gorgias:123':{notes:{value:'Private note retained'}}}};localStorage.setItem('bb-inbox-ticket-state-v1',JSON.stringify(records));dispatchEvent(new StorageEvent('storage',{key:'bb-inbox-ticket-state-v1'}));});
await page.locator('[data-field="priority"]').selectOption('high');
assert((await page.locator('.local-observed').textContent()).includes('Priority: high (observed: normal)'));
await page.locator('#search').fill('example@example.invalid');
await page.waitForFunction(()=>document.querySelector('.ticket-row')!==null&&document.querySelector('#count')?.textContent.includes('1 browser rows shown of 1 matching'));
assert((await page.locator('.row-state').textContent()).includes('Browser changes'),'edited provider row remains searchable by customer email');
await page.locator('#search').fill('');
await page.waitForFunction(()=>!new URLSearchParams(location.search).get('q'));
await page.locator('[data-field="priority"]').selectOption('');
assert.equal(await page.locator('.local-observed').count(),0);
assert.equal((await page.locator('.row-state').textContent()).includes('Browser changes'),false,'last cleared override returns row to provider grouping');
const afterClear=await page.evaluate(()=>JSON.parse(localStorage.getItem('bb-inbox-ticket-state-v1')).records['gorgias:123']);
assert.deepEqual(afterClear,{notes:{value:'Private note retained'}},'disposable grouping cleanup must not delete a private note');
await page.locator('[data-field="priority"]').selectOption('high');

await page.reload();await page.locator('.ticket-title').waitFor();
assert((await page.locator('.local-observed').textContent()).includes('Priority: high'));
assert.equal(await page.evaluate(()=>Intl.DateTimeFormat().resolvedOptions().timeZone),'Asia/Dhaka');
assert.equal(await page.evaluate(()=>new Date('2026-10-07T14:30').toISOString()),'2026-10-07T08:30:00.000Z');
await page.locator('.ticket-actions-menu summary').click();
const snoozeField=page.locator('[data-field="snooze"]');
const storedSnooze=()=>page.evaluate(()=>JSON.parse(localStorage.getItem('bb-inbox-ticket-state-v1')).records['gorgias:123']?.snooze?.value??null);
await snoozeField.fill('2026-10-07T14:30');
assert.equal(await page.locator('[data-field="snooze"]').inputValue(),'2026-10-07T14:30','snooze wall time must survive the change-triggered render');
assert.equal(await storedSnooze(),'2026-10-07T08:30:00.000Z','snooze time must remain stored as UTC');
await page.reload();await page.locator('.ticket-title').waitFor();await page.locator('.ticket-actions-menu summary').click();
assert.equal(await page.locator('[data-field="snooze"]').inputValue(),'2026-10-07T14:30','stored UTC must display as the same Dhaka wall time after reload');
await page.locator('[data-field="snooze"]').fill('2026-10-07T14:30');
assert.equal(await page.locator('[data-field="snooze"]').inputValue(),'2026-10-07T14:30','editing the existing value must not shift its wall time');
assert.equal(await storedSnooze(),'2026-10-07T08:30:00.000Z');
await page.reload();await page.locator('.ticket-title').waitFor();await page.locator('.ticket-actions-menu summary').click();
assert.equal(await page.locator('[data-field="snooze"]').inputValue(),'2026-10-07T14:30');
await page.locator('[data-field="snooze"]').fill('');
assert.equal(await page.locator('[data-field="snooze"]').inputValue(),'','clearing the snooze must keep the existing clear behavior');
assert.equal(await storedSnooze(),null);await page.locator('.ticket-actions-menu summary').click();

await page.locator('.ticket-actions-menu summary').click();
await page.locator('[data-action="toggle-read"]').click();
assert((await page.locator('.row-state').textContent()).includes('Unread'));
await page.locator('[data-action="toggle-read"]').click();
assert((await page.locator('.row-state').textContent()).includes('Read'));
next=true;await page.locator('[data-action="refresh"]').click();
await page.waitForFunction(()=>document.querySelector('.row-state')?.textContent.includes('Unread'));
await page.locator('#select-page').check();
await page.locator('#bulk-action').selectOption('read');await page.locator('[data-action="bulk-apply"]').click();
assert((await page.locator('.row-state').textContent()).includes('Read'));
assert.equal(await page.locator('[data-action="toggle-read"]').textContent(),'Mark unread','bulk Read must agree with the opened detail immediately');
await page.locator('[data-field="status"]').selectOption('closed');
await page.locator('[data-view="closed"]').click();
await page.waitForFunction(()=>document.querySelector('[data-ticket="gorgias:123"]')!==null&&document.querySelector('#count')?.textContent.includes('0 total'));
assert((await page.locator('.row-state').textContent()).includes('Browser changes · Last saved read'));
assert((await page.locator('#count').textContent()).includes('1 browser rows shown of 1 matching'));
await page.reload();await page.locator('.ticket-title').waitFor();
assert.equal(await page.locator('.ticket-row').count(),1,'closed browser organization survives reload although provider remains open');
await page.locator('.ticket-actions-menu summary').click();await page.locator('[data-field="status"]').selectOption('open');
await page.locator('[data-view="open"]').click();await page.waitForFunction(()=>document.querySelector('#count')?.textContent.includes('1 total'));
observedStatus='closed';observedUpdated='2026-10-05T10:02:00Z';
await page.locator('[data-action="refresh"]').click();
await page.waitForFunction(()=>JSON.parse(localStorage.getItem('bb-inbox-ticket-state-v1')).records['gorgias:123'].observed.status==='closed');
assert((await page.locator('.local-observed').textContent()).includes('Status: open (observed: closed)'));
assert.equal(await page.locator('.ticket-row').count(),1,'fresh observation updates without losing browser grouping');
observedStatus='open';observedUpdated='2026-10-05T10:03:00Z';await page.locator('[data-action="refresh"]').click();
await page.locator('[data-view="all"]').click();
await page.locator('#select-page').check();
assert.equal(await page.locator('#selection-count').textContent(),'1 selected on this page');
await page.locator('#bulk-action').selectOption('priority:normal');await page.locator('[data-action="bulk-apply"]').click();
assert.equal(await page.locator('#selection-count').textContent(),'0 selected on this page');
await page.locator('#select-page').check();await page.locator('[data-view="all"]').click();
assert.equal(await page.locator('#selection-count').textContent(),'0 selected on this page');
listPages=true;await page.locator('[data-action="refresh"]').click();await page.waitForFunction(()=>!document.querySelector('[data-action="page-next"]').disabled);await page.locator('#select-page').check();await page.locator('[data-action="page-next"]').click();assert.equal(await page.locator('#selection-count').textContent(),'0 selected on this page');await page.locator('[data-action="page-prev"]').click();listPages=false;
await page.locator('[data-action="list-collapse"]').click();assert(await page.locator('.list-reopen').isVisible());
await page.setViewportSize({width:390,height:844});await page.locator('[data-action="back"]').click();
assert(await page.locator('.ticket-sidebar').isVisible());assert.equal(await page.locator('.ticket-sidebar').evaluate(el=>el.inert),false);
assert.equal(await page.locator('[data-action="list-collapse"]').isVisible(),false,'phone list must not expose an unrecoverable collapse');
await page.locator('#search').fill('Example');await page.locator('#search').fill('');
await page.setViewportSize({width:1280,height:800});
assert.equal(await page.locator('.ticket-sidebar').evaluate(el=>el.inert),false,'desktop collapse followed by phone resize must recover the list');
await page.locator('[data-action="list-collapse"]').click();await page.locator('.list-reopen .icon-button').click();assert(await page.locator('.ticket-sidebar').isVisible());
await page.locator('[data-action="new"]').click();
await page.locator('#local-new [name="name"]').fill('Private example');await page.locator('#local-new [name="subject"]').fill('Private title');await page.locator('#local-new [name="body"]').fill('Private message <script>bad()</script>');await page.locator('#local-new button[type="submit"]').click();
await page.waitForFunction(()=>location.search.includes('local%3A'));
assert((await page.locator('.ticket-subtitle').textContent()).includes('Local ticket'));
assert.equal(await page.locator('[data-action="review-send"]').isVisible(),false);
assert((await page.locator('#send-mode-note').textContent()).includes('Customer sending is unavailable'));
assert.equal(await page.locator('.message script').count(),0);
await page.locator('#reply').fill('Private ticket draft');await page.reload();await page.locator('.ticket-title').waitFor();assert.equal(await page.locator('#reply').inputValue(),'Private ticket draft');
assert((await page.locator('.message-body').textContent()).includes('<script>bad()</script>'));
const locals=await page.evaluate(()=>JSON.parse(localStorage.getItem('bb-inbox-local-tickets-v1')));assert.equal(locals.length,1);assert.match(locals[0].id,/^local:[a-f0-9-]{36}$/);
await page.locator('[data-action="new"]').click();await page.locator('#local-new [name="name"]').fill('Second private example');await page.locator('#local-new [name="body"]').fill('A separate message');await page.locator('#local-new button[type="submit"]').click();await page.waitForFunction(()=>JSON.parse(localStorage.getItem('bb-inbox-local-tickets-v1')).length===2);const ids=await page.evaluate(()=>JSON.parse(localStorage.getItem('bb-inbox-local-tickets-v1')).map(t=>t.id));assert.equal(new Set(ids).size,2);
await page.evaluate(()=>{Storage.prototype.setItem=function(){throw new Error('storage unavailable');};});await page.locator('#reply').fill('Session only reply');assert((await page.locator('#saved-note').textContent()).includes('session only'));assert((await page.locator('#toast').textContent()).includes('storage is unavailable'));
await page.locator('[data-action="new"]').click();await page.locator('#local-new [name="name"]').fill('Session example');await page.locator('#local-new [name="body"]').fill('A ticket that storage cannot save');await page.locator('#local-new button[type="submit"]').click();
assert((await page.locator('#live-ticket-sync').textContent()).includes('session only'),'failed local save must not claim persisted ticket');
assert.equal(apiCalls.every(call=>['helpdesk.capabilities','helpdesk.get_ticket','helpdesk.get_messages','helpdesk.list_tickets'].includes(call.tool)),true);
assert.equal(apiCalls.some(call=>String(call.arguments.ticketId||'').startsWith('local:')),false);
assert.equal(consoleCalls.length,0,'local controls must never request provider writes');
assert.equal(errors.length,0,errors.join('\n'));
await page.screenshot({path:path.join(dir,'private-local-ticket.png')});
console.log(`Passed anchored long-history/long-draft layout at desktop/mobile/zoom, refreshed caret/focus, source title, local controls/read watermark/bulk selection/persistence, local creation and zero provider mutations. Evidence ${dir}`);
await browser.close();
