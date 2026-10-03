import {provision,signIn} from './browser-auth.mjs';
import assert from 'node:assert/strict';
import {spawn,spawnSync} from 'node:child_process';
import {createRequire} from 'node:module';
import fs from 'node:fs';
import net from 'node:net';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {verifyDelivery} from './browser-delivery.mjs';
process.umask(0o077);
const {chromium}=createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE||'playwright');
const repo=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const workspace='inbox-'+Date.now(),artifacts=path.join(repo,'intake/.local',workspace);
const reserve=net.createServer();await new Promise(resolve=>reserve.listen(0,'127.0.0.1',resolve));
const port=reserve.address().port;await new Promise(resolve=>reserve.close(resolve));
const origin=`http://127.0.0.1:${port}`;
const server=spawn('python3',['-m','intake','--workspace',workspace,'serve','--port',String(port)],{cwd:repo,stdio:['ignore','pipe','pipe']});
const cli=args=>{const r=spawnSync('python3',['-m','intake','--workspace',workspace,...args],{cwd:repo,encoding:'utf8',timeout:15000});assert.equal(r.status,0,r.stderr);return JSON.parse(r.stdout);};
let browser;
try{
  await new Promise((resolve,reject)=>{const timeout=setTimeout(()=>reject(new Error('Server start timeout')),10000);server.stdout.on('data',chunk=>{if(chunk.toString().includes('Offline intake:')){clearTimeout(timeout);resolve();}});server.stderr.on('data',()=>reject(new Error('Server error')));});
  const admin=provision(repo,workspace);
  browser=await chromium.launch({headless:true,args:['--no-sandbox','--disable-background-networking','--disable-component-update','--disable-sync']});
  const context=await browser.newContext({viewport:{width:1440,height:1000},serviceWorkers:'block'});
  const external=[],forbidden=[],errors=[];
  await context.route('**/*',route=>{const u=new URL(route.request().url());if(u.origin!==origin){external.push(u.origin);return route.abort();}if(u.pathname.startsWith('/console/')||u.pathname.startsWith('/dashboard/')||u.pathname==='/inbox/api/helpdesk'){forbidden.push(u.pathname);return route.abort();}return route.continue();});
  await context.addInitScript(()=>{localStorage.setItem('bb-inbox-local-tickets-v1',JSON.stringify([{id:'poison',subject:'Must never become a sandbox ticket'}]));localStorage.setItem('bb-inbox-ticket-state-v1','{"poison":{"status":"closed"}}');});
  const page=await context.newPage();page.setDefaultTimeout(10000);page.on('pageerror',error=>errors.push(error.message));
  page.on('dialog',dialog=>dialog.accept());
  await signIn(page,origin);
  await page.getByText('No tickets match this view.',{exact:true}).waitFor();
  assert.equal(await page.locator('#count').textContent(),'0 tickets');
  assert(await page.getByText('Offline sandbox',{exact:true}).isVisible());
  assert.equal(await page.locator('[data-action="toggle-send-access"]').count(),0);
  await page.getByRole('button',{name:'+ New test ticket',exact:true}).click();
  await page.locator('#customer-name').fill('Inbox Synthetic Customer');
  await page.locator('#customer-email').fill('inbox@example.test');
  await page.locator('#subject').fill('Shared independent ticket');
  await page.locator('#description').fill('A private test description.');
  await page.getByRole('button',{name:'Create test ticket',exact:true}).click();
  await page.getByRole('heading',{name:'Shared independent ticket',exact:true}).waitFor();
  const manualURL=page.url();
  await page.locator('#note').fill('A durable internal note.');
  await page.getByRole('button',{name:'Save note',exact:true}).click();
  await page.getByText('A durable internal note.',{exact:true}).waitFor();
  await page.locator('#status').selectOption('waiting_team');
  await page.locator('#priority').selectOption('high');
  await page.locator('#assignee').selectOption(admin.id);
  await page.getByRole('button',{name:'Save details',exact:true}).click();
  await page.getByText('Ticket details saved in the offline workspace.',{exact:true}).waitFor();

  // A fresh browser context has no local ticket state; it reads the same DB.
  const secondContext=await browser.newContext({viewport:{width:1440,height:1000}});
  await secondContext.route('**/*',route=>route.request().url().startsWith(origin+'/')?route.continue():route.abort());
  const second=await secondContext.newPage();await signIn(second,origin,'admin',new URL(manualURL).pathname+new URL(manualURL).search);
  await second.getByText('A durable internal note.',{exact:true}).waitFor();
  assert.equal(await second.locator('#status').inputValue(),'waiting_team');
  assert.equal(await second.locator('#assignee').inputValue(),admin.id);
  assert(await second.locator('#open-reply').isDisabled());
  await second.locator('#note').fill('Saved by another browser.');await second.getByRole('button',{name:'Save note',exact:true}).click();
  await second.getByText('Saved by another browser.',{exact:true}).waitFor();
  await page.locator('#note').fill('Preserve this stale note.');await page.getByRole('button',{name:'Save note',exact:true}).click();
  await page.getByText('This ticket changed. Refresh it before saving your change.',{exact:true}).waitFor();
  assert.equal(await page.locator('#note').inputValue(),'Preserve this stale note.');
  await page.locator('#refresh').click();await page.getByText('Saved by another browser.',{exact:true}).waitFor();
  assert.equal(await page.locator('#note').inputValue(),'Preserve this stale note.');
  await page.getByRole('button',{name:'Save note',exact:true}).click();await page.getByText('Preserve this stale note.',{exact:true}).waitFor();
  await secondContext.close();

  // A lost response retries the same operation, even after a refresh.
  await page.locator('#note').fill('Note with an intentionally lost response.');
  await page.route('**/notes',async route=>{await route.fetch();await route.abort();});
  await page.getByRole('button',{name:'Save note',exact:true}).click();
  await page.getByText('The result was not received. Refresh to inspect the record; do not assume it failed.',{exact:true}).waitFor();
  await page.unroute('**/notes');await page.locator('#refresh').click();
  await page.locator('.message-body').filter({hasText:'Note with an intentionally lost response.'}).waitFor();
  await page.getByRole('button',{name:'Save note',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('#note').value==='');
  assert.equal(await page.locator('.message-body').filter({hasText:'Note with an intentionally lost response.'}).count(),1);

  const fixture=JSON.parse(fs.readFileSync(path.join(repo,'intake/fixtures/gorgias-synthetic.json'),'utf8'));
  fixture.tickets[0].messages[0].body_text+='\r\nOriginal CRLF retained.\rStandalone carriage return.\n<script>fetch("https://never-execute.example.test/")</script>';
  fixture.tickets[0].tags.push(null);
  const file=path.join(artifacts,'synthetic-inbox-export.json');fs.writeFileSync(file,JSON.stringify(fixture),{mode:0o600});
  const preview=cli(['preview',file,'--account','synthetic-inbox']);cli(['import',file,'--account','synthetic-inbox','--expected-digest',preview.digest]);
  const replay=path.join(repo,'intake/fixtures/replay-synthetic.json');
  cli(['replay',replay,'--expected-digest',cli(['replay-preview',replay]).digest]);
  await page.locator('#queue').selectOption('all');
  await page.waitForFunction(()=>document.querySelector('#count').textContent==='1–8 of 8');
  await page.getByRole('button',{name:/Where is the rest of my order/}).click();
  await page.waitForFunction(()=>document.querySelector('.message-body')?.textContent.includes('Original CRLF retained.'));
  assert.equal(await page.locator('.message-body').first().textContent(),fixture.tickets[0].messages[0].body_text.trim());
  assert.equal(await page.locator('script:not([src])').count(),0);
  await page.getByRole('button',{name:/Photo of a damaged button/}).click();
  await page.getByText('button-photo.jpg · Metadata only · File not downloaded',{exact:true}).waitFor();
  assert.equal(await page.locator('img,iframe,object').count(),0);
  assert(await page.locator('#open-reply').isDisabled());
  await page.locator('#queue').selectOption('spam');
  await page.getByRole('button',{name:/Synthetic spam/}).waitFor();
  await page.locator('#queue').selectOption('review');await page.getByRole('button',{name:/Synthetic missing header/}).click();
  await page.locator('#source').getByText('Needs review: Missing message id',{exact:true}).waitFor();
  assert(await page.locator('#open-reply').isDisabled());
  await page.locator('#queue').selectOption('all');await page.waitForFunction(()=>document.querySelector('#count').textContent==='1–8 of 8');
  await verifyDelivery(page,{countText:'1–8 of 8'});

  // No background provider polling is used. Refresh reports errors and recovers.
  await page.route('**/api/tickets?**',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Synthetic storage unavailable.'})}));
  await page.locator('#refresh').click();await page.locator('#ticket-list').getByText('Synthetic storage unavailable.').waitFor();
  await page.unroute('**/api/tickets?**');await page.locator('#refresh').click();await page.waitForFunction(()=>document.querySelector('#count').textContent==='1–8 of 8');
  await page.locator('#search').fill('durable internal note');await page.waitForFunction(()=>document.querySelector('#count').textContent==='1–1 of 1');
  await page.locator('#search').fill('');await page.waitForFunction(()=>document.querySelector('#count').textContent==='1–8 of 8');
  await page.getByRole('button',{name:/Shared independent ticket/}).click();
  await page.locator('[data-tab="details"]').click();await page.getByText('Messages',{exact:true}).waitFor();
  await page.keyboard.press('ArrowLeft');assert.equal(await page.locator('[data-tab="conversation"]').getAttribute('aria-selected'),'true');

  // Server pagination, beyond one 50-row page, without copying tickets to browser storage.
  await page.evaluate(async()=>{const {token}=await (await fetch('/api/session')).json();for(let i=0;i<51;i++){const response=await fetch('/api/tickets',{method:'POST',headers:{'X-Intake-Token':token,'Content-Type':'application/json'},body:JSON.stringify({operation_id:'pagination-'+i,subject:'Pagination fixture '+i,body:'Synthetic pagination description',name:'Pagination Test'})});if(!response.ok)throw new Error('Pagination fixture failed');}});
  await page.locator('#refresh').click();await page.waitForFunction(()=>document.querySelector('#count').textContent==='1–50 of 59');
  await page.getByRole('button',{name:'Next ticket page',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#count').textContent==='51–59 of 59');
  await page.getByRole('button',{name:'Previous ticket page',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#count').textContent==='1–50 of 59');
  await page.locator('#search').fill('Shared independent ticket');await page.getByRole('button',{name:/Shared independent ticket/}).click();
  await page.getByRole('heading',{name:'Shared independent ticket',exact:true}).waitFor();
  await page.locator('#toast').waitFor({state:'hidden'});
  await page.screenshot({path:path.join(artifacts,'inbox-desktop.png'),fullPage:true});
  for(const width of [1440,1024,800,768,390,320]){
    await page.setViewportSize({width,height:900});
    const sizes=await page.evaluate(()=>({page:document.documentElement.scrollWidth,viewport:innerWidth}));assert(sizes.page<=sizes.viewport,`Overflow at ${width}`);
    assert(await page.locator('#note').isVisible());
    const box=await page.locator('#note').boundingBox();assert(box.y+box.height<=900,'Composer remains visible');
  }
  for(const [width,height] of [[1440,768],[768,700],[390,844],[320,568]]){
    await page.setViewportSize({width,height});
    const box=await page.locator('#note').boundingBox();assert(box.y+box.height<=height,`Composer clips at ${width}x${height}`);
    const messages=await page.locator('#ticket-content').boundingBox();assert(messages.height>=70,`Message area too small at ${width}x${height}`);
  }
  await page.setViewportSize({width:390,height:900});
  await page.screenshot({path:path.join(artifacts,'inbox-mobile.png'),fullPage:true});
  await page.getByRole('button',{name:'Show customer details',exact:true}).click();
  assert.equal(await page.locator('#customer-rail').getAttribute('aria-modal'),'true');
  assert(await page.locator('#status').isVisible());
  await page.keyboard.press('Escape');assert.equal(await page.locator('#customer-rail').getAttribute('aria-modal'),null);
  await page.getByRole('button',{name:'All tickets',exact:true}).click();assert(await page.locator('#search').isVisible());

  // Refuse an unexpected live-capability response before reading any ticket.
  const guard=await context.newPage();let dataReads=0;
  await guard.route('**/api/session',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({token:'test',capabilities:{mode:'live'}})}));
  await guard.route('**/api/tickets**',route=>{dataReads++;return route.abort();});
  await guard.goto(origin+'/inbox/');await guard.locator('#blank').getByText('This page requires the isolated offline ticket API.').waitFor();assert.equal(dataReads,0);await guard.close();
  assert.deepEqual(external,[]);assert.deepEqual(forbidden,[]);assert.deepEqual(errors,[]);
  const result={passed:true,workspace,externalRequests:0,liveRouteRequests:0,checks:['empty start','browser storage ignored','durable manual ticket and note','fresh browser persistence','stale note retained','lost response duplicate prevention','export/replay queues','metadata-only attachments','fake delivery review/confirm/reconcile','source errors and recovery','search','59-ticket pagination','responsive composer','mobile rail keyboard','offline capability refusal']};
  fs.writeFileSync(path.join(artifacts,'inbox-browser-result.json'),JSON.stringify(result,null,2),{mode:0o600});
  console.log('Isolated Inbox browser checks passed: '+artifacts);
}finally{if(browser)await browser.close();server.kill('SIGTERM');}
