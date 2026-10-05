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
const fixture=()=>({id:'gorgias:123',subject:'Original subject retained',customerName:'Example Customer',fromEmail:'example@example.invalid',status:observedStatus,gorgiasPriority:'normal',assigneeEmail:'support@example.invalid',channel:'email',updatedAt:observedUpdated||stamp,lastMessageAt:next?'2026-10-05T10:01:00Z':stamp,syncedAt:stamp,readonlyDraft:Array.from({length:80},(_,i)=>`Suggestion paragraph ${i+1}.`).join('\n\n'),draftSourceMessageId:'m1',draftProcessedAt:stamp,messages:Array.from({length:30},(_,i)=>({id:next&&i===29?'new-message':`m${i}`,fromAgent:false,body:`Message ${i}. `.repeat(20),at:next&&i===29?'2026-10-05T10:01:00Z':stamp,...(i===1?{normalized:{display_text:'Historical quoted request only',current_text:'',history_available:true}}:i===2?{normalized:{display_text:'Legacy message without current-text field'}}:{}),...(i===0?{normalized:{display_text:'Question #12345\n\n> Earlier Bengali history বাংলা',current_text:'Question #12345',original_content:'<script>bad()</script><p>Question #12345</p>',original_field:'body_html',history_available:true},attachments:[{name:'Example.png',url:`${base}/fixture.png`,content_type:'image/png'},{name:'Unsafe',url:'javascript:alert(1)',content_type:'image/png'}]}:{})})),shopifyRail:order?{status:'observed',order:{name:'#12345',lineItems:{nodes:[]}},customer:{displayName:'Example Customer'}}:{status:'unavailable'},redoDetails:{status:'observed',fetchedAt:stamp,orders:{'#12345':{status:'observed',returns:[{status:'pending',refund_amount:'15',tracking_url:'javascript:alert(1)'}],observedAt:stamp}}}});
const apiCalls=[],consoleCalls=[],errors=[];
page.on('pageerror',e=>errors.push(e.message));
await page.route('**/console/api/**',async route=>{consoleCalls.push(route.request().url());await route.fulfill({status:403,json:{error:'inbox_read_only'}});});
await page.route('**/inbox/api/helpdesk',async route=>{
  const payload=route.request().postDataJSON();apiCalls.push(payload);const tool=payload.tool.split('.').at(-1),t=fixture();
  assert(['capabilities','get_ticket','get_messages','list_tickets'].includes(tool),'only read tools may be requested');
  assert(!String(payload.arguments.ticketId||'').startsWith('local:'));
  if(tool==='capabilities'){await heldCapabilities;await route.fulfill({json:{ok:true,source:'gorgias_api',readOnly:true,capabilities:{sendReply:false}}});return;}
  const matches=!(payload.arguments.view==='closed'&&t.status!=='closed'||payload.arguments.view==='open'&&t.status!=='open');
  await route.fulfill({json:{ok:true,source:'gorgias_api',...(tool==='get_ticket'?{ticket:t}:tool==='list_tickets'?{tickets:matches?(payload.arguments.offset?[{...t,id:'gorgias:124'}]:[t]):[],total:matches?(listPages?2:1):0,nextOffset:matches&&listPages&&!payload.arguments.offset?9:null,projection:{complete:true},operatorEmail:'support@example.invalid',categoryAvailability:{assigned:true,snoozed:true,spam:true,trash:true}}:{})}});
});
async function checkLocalSnoozedAccess(){
  const probe=await browser.newContext({viewport:{width:1280,height:800},timezoneId:'Asia/Dhaka'});
  const future='2026-10-07T12:00:00Z',expired='2026-10-04T12:00:00Z',stamp='2026-10-05T10:00:00Z';
  async function run({name,kind='observed',availability=false,value=future,count=1,orphan=false,reset=false,filtersAndPages=false,failRefresh=false,providerAvailable=false}={}){
    const p=await probe.newPage();await p.clock.setFixedTime(new Date('2026-10-05T12:00:00Z'));
    const calls=[],writes=[],pageErrors=[];let failSnoozed=false;
    const records={},locals=[];
    for(let i=0;i<count;i++){
      const id=`gorgias:${501+i}`;
      const summary={id,browserOverride:true,savedAt:Date.parse(stamp),customerName:`Saved customer ${i}`,fromEmail:`saved-${i}@example.invalid`,subject:`Saved snooze ${i}`,snippet:'Saved browser observation',status:'open',gorgiasPriority:'normal',assigneeEmail:'',channel:'email',updatedAt:stamp,lastMessageAt:stamp,lastMessageId:`saved-${i}`,snoozedUntil:'',snoozeUntil:'',syncedAt:stamp};
      if(!orphan&&kind==='observed')records[id]={observed:summary,...(value?{snooze:{value,by:'synthetic operator',at:Date.parse(stamp)}}:{})};
    }
    if(kind==='local'){
      const id='local:snoozed-probe';
      locals.push({id,localOnly:true,customerName:'Private local customer',subject:'Private local snooze',fromEmail:'',status:'open',channel:'local',updatedAt:stamp,snippet:'Private local message',messages:[]});
      records[id]={snooze:{value,by:'synthetic operator',at:Date.parse(stamp)}};
    }
    if(orphan)records['gorgias:501']={snooze:{value,by:'synthetic operator',at:Date.parse(stamp)}};
    await p.addInitScript(({seedRecords,seedLocals})=>{
      localStorage.setItem('bb-inbox-ticket-state-v1',JSON.stringify({version:1,records:seedRecords}));
      localStorage.setItem('bb-inbox-local-tickets-v1',JSON.stringify(seedLocals));
    },{seedRecords:records,seedLocals:locals});
    p.on('pageerror',error=>pageErrors.push(error.message));
    await p.route('**/console/api/**',async route=>{writes.push(route.request().url());await route.fulfill({status:403,json:{error:'inbox_read_only'}});});
    await p.route('**/inbox/api/helpdesk',async route=>{
      const payload=route.request().postDataJSON(),tool=payload.tool.split('.').at(-1),args=payload.arguments||{};calls.push(payload);
      assert(['capabilities','get_ticket','get_messages','list_tickets'].includes(tool),`${name}: unexpected read tool ${tool}`);
      assert(!String(args.ticketId||'').startsWith('local:'),`${name}: local-only ID reached provider read`);
      const ticket={id:'gorgias:501',subject:'Provider ticket',customerName:'Provider customer',fromEmail:'provider@example.invalid',status:'open',channel:'email',gorgiasPriority:'normal',updatedAt:stamp,lastMessageAt:stamp,syncedAt:stamp,messages:[],shopifyRail:{status:'unavailable'}};
      assert.equal(Object.hasOwn(ticket,'snoozeUntil'),false,'provider fixture must omit snooze fields');
      if(tool==='capabilities'){await route.fulfill({json:{ok:true,source:'gorgias_api',readOnly:true,capabilities:{sendReply:false}}});return;}
      if(tool==='list_tickets'){
        if(failSnoozed&&args.view==='snoozed'){await route.fulfill({status:503,json:{message:'Synthetic provider list failure'}});return;}
        await route.fulfill({json:{ok:true,source:'gorgias_api',tickets:[],total:0,nextOffset:null,projection:{complete:true,generatedAt:stamp},categoryAvailability:{assigned:true,snoozed:providerAvailable?true:availability,spam:false,trash:false}}});return;
      }
      await route.fulfill({json:{ok:true,source:'gorgias_api',...(tool==='get_ticket'?{ticket}:tool==='get_messages'?{messages:[],nextCursor:null}:{})}});
    });
    await p.goto(`${base}/inbox/?ticket=gorgias%3A501`);await p.locator('.ticket-title').waitFor();
    await p.waitForFunction(()=>document.querySelector('#count')?.textContent.startsWith('Page'));
    const snoozed=p.locator('[data-view="snoozed"]');
    const parsedSnooze=Date.parse(value),hasLocalSnooze=!orphan&&['observed','local'].includes(kind)&&Number.isFinite(parsedSnooze)&&parsedSnooze>Date.parse('2026-10-05T12:00:00Z');
    assert.equal(await snoozed.isDisabled(),!providerAvailable&&!hasLocalSnooze,`${name}: only provider availability or a valid future local member enables Snoozed`);
    assert.equal(await p.locator('[data-view="assigned"]').isDisabled(),true,`${name}: Assigned still requires an operator`);
    assert.equal(await p.locator('[data-view="spam"]').isDisabled(),true,`${name}: local Snoozed membership must not enable Spam`);
    assert.equal(await p.locator('[data-view="trash"]').isDisabled(),true,`${name}: local Snoozed membership must not enable Trash`);
    if(!providerAvailable&&hasLocalSnooze){
      const title=await snoozed.getAttribute('title');
      assert.match(title,/browser-only/i,`${name}: explain browser-only Snoozed access`);
      assert.match(title,/Gorgias.*snooze fields.*unavailable/i,`${name}: explain missing Gorgias snooze fields`);
      if(typeof availability==='object')assert(title.includes(availability.reason),`${name}: retain the provider unavailability reason in the explanation`);
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
  await run({name:'orphan snooze without observed summary',orphan:true});
  await run({name:'reset last observed snooze',reset:true});
  await run({name:'provider view counts, pagination, filters and failed refresh',count:12,availability:{available:false,reason:'Gorgias omitted snooze_datetime'},filtersAndPages:true,failRefresh:true});
  await run({name:'provider-available Snoozed',kind:'none',value:'',providerAvailable:true});
  await probe.close();
}
await checkLocalSnoozedAccess();
await page.goto(`${base}/inbox/?ticket=gorgias%3A123`);
await page.locator('.ticket-title').waitFor();
assert.equal(await page.locator('.ticket-title').textContent(),'New Ticket');
await page.waitForFunction(()=>!document.querySelector('[data-view="assigned"]').disabled);
const capabilitiesResponse=page.waitForResponse(response=>response.url().endsWith('/inbox/api/helpdesk')&&response.request().postDataJSON()?.tool==='helpdesk.capabilities',{timeout:30000});
releaseCapabilities();await (await capabilitiesResponse).finished();
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
