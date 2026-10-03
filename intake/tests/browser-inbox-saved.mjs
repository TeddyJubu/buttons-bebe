// Read-only display proof for a fresh saved-export workspace. Never screenshot PII.
import {provision,signIn} from './browser-auth.mjs';
import assert from 'node:assert/strict';
import {spawn,spawnSync} from 'node:child_process';
import {createHash,randomBytes} from 'node:crypto';
import {createRequire} from 'node:module';
import fs from 'node:fs';
import net from 'node:net';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
process.umask(0o077);
const workspace=process.argv[2];
assert(/^[a-z0-9][a-z0-9-]{0,47}$/.test(workspace||''),'Provide a saved-export proof workspace');
const repo=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..'),directory=path.join(repo,'intake/.local',workspace);
assert(fs.existsSync(path.join(directory,'intake.sqlite3')),'Workspace must already exist');
const reportPath=path.join(directory,'inbox-display-reconciliation.json');
assert(!fs.existsSync(reportPath),'Preserve existing evidence; use a fresh proof workspace');
const databaseDigest=()=>{
  const check=spawnSync('python3',['-c',`import hashlib,json,sqlite3,sys
from pathlib import Path
p=Path(sys.argv[1])
db=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True)
assert db.execute('PRAGMA application_id').fetchone()[0]==0x4242494E
assert db.execute('PRAGMA user_version').fetchone()[0]==7
tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
value={name:sorted(list(db.execute('SELECT * FROM '+name)),key=repr) for name in tables}
print(hashlib.sha256(json.dumps(value,sort_keys=True,default=lambda v:v.hex()).encode()).hexdigest())
db.close()`,path.join(directory,'intake.sqlite3')],{encoding:'utf8',cwd:repo,timeout:15000});
  assert.equal(check.status,0,'Could not verify private workspace');return check.stdout.trim();
};
const initial=databaseDigest(); // Refuse old evidence; only use a fresh schema-7 workspace.
const secret=randomBytes(32).toString('base64url');
provision(repo,workspace,'display-proof','Display proof operator','admin',secret);
const {chromium}=createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE||'playwright');
const reserve=net.createServer();await new Promise(resolve=>reserve.listen(0,'127.0.0.1',resolve));
const port=reserve.address().port;await new Promise(resolve=>reserve.close(resolve));
const origin=`http://127.0.0.1:${port}`;
const server=spawn('python3',['-m','intake','--workspace',workspace,'serve','--port',String(port)],{cwd:repo,stdio:['ignore','pipe','pipe']});
let browser,stage='startup',caseIndex=0,diagnostic={};
try{
  await new Promise((resolve,reject)=>{const timeout=setTimeout(()=>reject(new Error('Server start timeout')),10000);server.stdout.on('data',chunk=>{if(chunk.toString().includes('Offline intake:')){clearTimeout(timeout);resolve();}});server.stderr.on('data',()=>reject(new Error('Private server failed')));});
  browser=await chromium.launch({headless:true,args:['--no-sandbox','--disable-background-networking','--disable-component-update','--disable-sync']});
  const context=await browser.newContext({viewport:{width:1440,height:1000},serviceWorkers:'block'});
  let external=0,writes=0,errors=0,authenticated=false;
  await context.route('**/*',route=>{if(!route.request().url().startsWith(origin+'/')){external++;return route.abort();}if(route.request().method()!=='GET'&&!(new URL(route.request().url()).pathname==='/api/auth/login'&&!authenticated)){writes++;return route.abort();}return route.continue();});
  const page=await context.newPage();page.on('pageerror',()=>errors++);await signIn(page,origin,'display-proof','/inbox/',secret);authenticated=true;const before=databaseDigest();
  const rows=await page.evaluate(async()=>{
    const {token}=await (await fetch('/api/session')).json();
    const result=await (await fetch('/api/tickets?queue=all',{headers:{'X-Intake-Token':token}})).json();
    if(result.nextOffset!==null)throw new Error('Use a sample of at most 50 tickets');
    return result.tickets;
  });
  let messages=0,attachments=0,eligible=0;
  const checks=[];
  for(const row of rows){
    caseIndex++;stage='load saved conversation';
    const ticket=await page.evaluate(async id=>{const {token}=await (await fetch('/api/session')).json();return (await fetch('/api/tickets/'+id,{headers:{'X-Intake-Token':token}})).json();},row.id);
    assert(ticket.messages.length>0,'Expected a saved conversation');
    assert.equal(ticket.deliveries.length,0,'Use a fresh source-only workspace');
    await page.locator(`[data-ticket="${row.id}"]`).click();
    await page.waitForFunction(id=>document.querySelector('.message')?.dataset.message===id,ticket.messages[0].id);
    const actual=await page.locator('.message').evaluateAll(nodes=>nodes.map(node=>({id:node.dataset.message,body:node.querySelector('.message-body').textContent,author:node.querySelector('.message-person strong').textContent,email:node.querySelector('.message-person span').textContent,at:node.querySelector('time').getAttribute('datetime')})));
    const expected=ticket.messages.map(m=>({id:m.id,body:m.body,author:m.author_name,email:m.author_email,at:m.created_at}));
    stage='message display';
    diagnostic={sameMessages:actual.length===expected.length,
      sameAfterNewlineNormalization:JSON.stringify(actual).replaceAll('\\r\\n','\\n').replaceAll('\\r','\\n')===JSON.stringify(expected).replaceAll('\\r\\n','\\n').replaceAll('\\r','\\n')};
    assert(JSON.stringify(actual)===JSON.stringify(expected),'Conversation display mismatch');
    stage='ticket fields';diagnostic={};
    assert.equal(await page.locator('.ticket-title').textContent(),ticket.subject,'Subject display mismatch');
    assert.equal(await page.locator('#rail-person .customer-name').textContent(),ticket.name||'Unknown contact','Contact display mismatch');
    assert.equal(await page.locator('#status').inputValue(),ticket.status,'Status display mismatch');
    assert.equal(await page.locator('#priority').inputValue(),ticket.priority,'Priority display mismatch');
    assert.equal(await page.locator('#assignee').inputValue(),ticket.team_assignee?.id||'','Local assignment display mismatch');
    assert.equal(await page.locator('#source-assignee').textContent(),'Source assignment: '+(ticket.assignee||'None recorded'),'Source assignment display mismatch');
    assert.equal(await page.locator('#open-reply').isDisabled(),!ticket.reply_context.available,'Reply eligibility mismatch');
    const files=ticket.messages.reduce((n,m)=>n+m.attachment_records.length,0);
    assert.equal(await page.locator('.attachment-label').count(),files,'Attachment reference mismatch');
    assert.equal(await page.locator('img,iframe,object').count(),0,'Remote content rendered');
    messages+=ticket.messages.length;attachments+=files;eligible+=Number(ticket.reply_context.available);
    checks.push({ticket_digest:createHash('sha256').update(row.id).digest('hex'),messages:ticket.messages.length,passed:true});
  }
  stage='read-only database verification';
  assert.equal(databaseDigest(),before,'Reading the Inbox changed stored records');
  assert.equal(external,0);assert.equal(writes,0);assert.equal(errors,0);
  const report={passed:true,workspace,tickets:rows.length,messages,attachmentReferences:attachments,replyEligible:eligible,replyWithheld:rows.length-eligible,allStoredRowsUnchangedDuringDisplay:true,authenticated:true,role:'admin',externalRequests:external,writeRequests:writes,screenshots:0,checks};
  fs.writeFileSync(reportPath,JSON.stringify(report,null,2),{mode:0o600,flag:'wx'});
  console.log(JSON.stringify({...report,checks:checks.length},null,2));
}catch{
  // Do not print assertion values, URLs or exception traces containing customer data.
  const failure={passed:false,stage,caseIndex,...diagnostic};
  fs.writeFileSync(path.join(directory,'inbox-display-failure-'+Date.now()+'.json'),JSON.stringify(failure),{mode:0o600,flag:'wx'});
  console.error(JSON.stringify(failure));process.exitCode=1;
}finally{if(browser)await browser.close();server.kill('SIGTERM');}
