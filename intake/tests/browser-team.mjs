import assert from 'node:assert/strict';
import {spawn,spawnSync} from 'node:child_process';
import {createRequire} from 'node:module';
import fs from 'node:fs';
import net from 'node:net';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {provision,signIn,api,password} from './browser-auth.mjs';
process.umask(0o077);
const {chromium}=createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE||'playwright');
const repo=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..'),workspace='team-'+Date.now(),artifacts=path.join(repo,'intake/.local',workspace);
const admin=provision(repo,workspace,'admin','Alex Administrator'),agent=provision(repo,workspace,'agent','Sam Agent','agent');provision(repo,workspace,'viewer','Taylor Viewer','viewer');
const reserve=net.createServer();await new Promise(r=>reserve.listen(0,'127.0.0.1',r));const port=reserve.address().port;await new Promise(r=>reserve.close(r));const origin=`http://127.0.0.1:${port}`;
const server=spawn('python3',['-m','intake','--workspace',workspace,'serve','--port',String(port)],{cwd:repo,stdio:['ignore','pipe','pipe']});
let browser;const errors=[],external=[];
try{
  await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('Server timeout')),10000);server.stdout.on('data',s=>{if(s.toString().includes('Offline intake:')){clearTimeout(timer);resolve();}});server.stderr.on('data',()=>reject(new Error('Server failed')));});
  browser=await chromium.launch({headless:true,args:['--no-sandbox','--disable-background-networking','--disable-component-update','--disable-sync']});
  async function newPage(){const context=await browser.newContext({viewport:{width:1440,height:1000},serviceWorkers:'block'});await context.route('**/*',route=>{if(new URL(route.request().url()).origin!==origin){external.push('blocked');return route.abort();}return route.continue();});const page=await context.newPage();page.setDefaultTimeout(10000);page.on('pageerror',error=>errors.push(error.message));page.on('dialog',dialog=>dialog.accept());return page;}
  const a=await newPage(),b=await newPage(),v=await newPage();
  await a.goto(origin+'/inbox/');await a.waitForURL('**/login?**');
  await a.locator('#username').fill('admin');await a.locator('#password').fill('Wrong synthetic password');await a.getByRole('button',{name:'Sign in',exact:true}).click();await a.getByText('Invalid sandbox username or password.',{exact:true}).waitFor();
  await signIn(a,origin);await signIn(b,origin,'agent');await signIn(v,origin,'viewer');
  const created=await api(a,'/api/tickets',{operation_id:'team-create',subject:'Synthetic team handoff',body:'A private synthetic ticket.',name:'Synthetic Customer'});assert.equal(created.status,200);const id=created.body.id,url=origin+'/inbox/?ticket='+id;
  for(const p of [a,b,v]){await p.goto(url);await p.getByRole('heading',{name:'Synthetic team handoff'}).waitFor();}
  assert(await v.locator('#inbox-composer').isHidden());assert(await v.locator('[data-action="new"]').isHidden());assert(await v.locator('#status').isDisabled());
  const denied=await api(v,`/api/tickets/${id}/notes`,{operation_id:'spoof',revision:1,body:'Must not save',actor:'admin',role:'admin'});assert.equal(denied.status,403);
  assert.equal((await api(b,'/api/import/preview',{})).status,403);
  // Read state belongs to a user, while shared changes use ticket revisions.
  await a.getByRole('button',{name:'Mark read',exact:true}).click();await a.getByRole('button',{name:'Mark unread',exact:true}).waitFor();
  assert(await b.getByRole('button',{name:'Mark read',exact:true}).isVisible());assert(await v.getByRole('button',{name:'Mark read',exact:true}).isVisible());
  await a.reload();await a.getByRole('button',{name:'Mark unread',exact:true}).waitFor();
  await b.getByRole('button',{name:'Assign to me',exact:true}).click();await b.waitForFunction(id=>document.querySelector('#assignee').value===id,agent.id);
  await a.getByRole('button',{name:'Assign to me',exact:true}).click();await a.getByText('This ticket changed. Refresh it before saving your change.',{exact:true}).waitFor();await a.locator('#refresh').click();await a.waitForFunction(id=>document.querySelector('#assignee').value===id,agent.id);
  await b.locator('#team-view').selectOption('mine');await b.waitForFunction(()=>document.querySelector('#count').textContent==='1–1 of 1');
  await a.locator('#team-view').selectOption('mine');await a.getByText('No tickets match this view.',{exact:true}).waitFor();await a.locator('#team-view').selectOption('all');
  await a.getByRole('button',{name:/Synthetic team handoff/}).click();
  await a.locator('#assignee').selectOption(admin.id);await a.locator('#status').selectOption('waiting_team');await a.getByRole('button',{name:'Save details',exact:true}).click();await a.getByText('Ticket details saved in the offline workspace.',{exact:true}).waitFor();
  await b.locator('#refresh').click();await b.getByText('No tickets match this view.',{exact:true}).waitFor();await b.locator('#team-view').selectOption('all');await b.getByRole('button',{name:/Synthetic team handoff/}).click();
  await b.locator('#note').fill('Sam recorded the local handoff.');await b.getByRole('button',{name:'Save note',exact:true}).click();await b.getByText('Sam recorded the local handoff.',{exact:true}).waitFor();
  assert.equal(await b.locator('.message-person strong').last().textContent(),'Sam Agent');
  await a.locator('#refresh').click();await a.getByRole('button',{name:'Mark read',exact:true}).waitFor();
  await a.locator('#team-view').selectOption('unread');await a.waitForFunction(()=>document.querySelector('#count').textContent==='1–1 of 1');
  await v.getByRole('button',{name:'Mark read',exact:true}).click();await v.getByRole('button',{name:'Mark read',exact:true}).waitFor(); // newer note was outside viewer watermark
  await v.getByRole('button',{name:'Mark read',exact:true}).click();await v.getByRole('button',{name:'Mark unread',exact:true}).waitFor();
  // No cookie or token is readable from browser storage.
  const storage=await b.evaluate(()=>({cookie:document.cookie,local:Object.keys(localStorage),session:Object.keys(sessionStorage)}));assert.deepEqual(storage,{cookie:'',local:[],session:[]});
  for(const width of [1440,768,390,320]){await a.setViewportSize({width,height:width===320?568:900});assert(await a.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Team controls overflow viewport');await a.screenshot({path:path.join(artifacts,`team-${width}.png`)});}
  await a.setViewportSize({width:1440,height:1000});
  // Signing out clears other tabs of this browser, including unsaved drafts.
  const other=await a.context().newPage();other.on('pageerror',error=>errors.push(error.message));other.on('dialog',dialog=>dialog.accept());await other.goto(url);await other.locator('#note').fill('Unsaved private draft to clear');await a.locator('#sign-out').click();await a.waitForURL('**/login?**');await other.waitForURL('**/login?**');assert.equal(await other.locator('.message').count(),0);
  await signIn(a,origin);await a.goto(url);await a.locator('#note').waitFor();assert.equal(await a.locator('#note').inputValue(),'');
  // CLI role/active changes revoke existing sessions; a stale page cannot save.
  const revoke=spawnSync('python3',['-m','intake','--workspace',workspace,'user-set','--username','agent','--name','Sam Agent','--role','agent','--disable','--keep-password'],{cwd:repo,encoding:'utf8',timeout:15000});assert.equal(revoke.status,0);
  await b.locator('#note').fill('This revoked write must fail');await b.getByRole('button',{name:'Save note',exact:true}).click();await b.waitForURL('**/login?**');
  // Change password in the UI and verify all old sessions end.
  await v.goto(origin+'/login?password=1');await v.locator('#current-password').fill(password);await v.locator('#new-password').fill('New synthetic viewer password!');await v.locator('#repeat-password').fill('New synthetic viewer password!');await v.getByRole('button',{name:'Change password',exact:true}).click();await v.waitForURL(origin+'/login');
  await v.locator('#username').fill('viewer');await v.locator('#password').fill(password);await v.getByRole('button',{name:'Sign in',exact:true}).click();await v.getByText('Invalid sandbox username or password.',{exact:true}).waitFor();
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  const report={passed:true,roles:3,separateBrowserContexts:3,assignmentRace:true,unreadIsolation:true,staleWatermark:true,sourceAssignmentPreserved:true,sessionRevocation:true,crossTabSignOut:true,passwordChange:true,externalRequests:0,realMessagesSent:0};
  fs.writeFileSync(path.join(artifacts,'team-browser-proof.json'),JSON.stringify(report,null,2),{mode:0o600});console.log('Offline team browser proof passed: '+artifacts);
}catch(error){console.error(error);process.exitCode=1;}finally{if(browser)await browser.close();server.kill('SIGTERM');}
