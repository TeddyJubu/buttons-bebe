const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const test=require('node:test');
const html=fs.readFileSync(new URL('../index.html',`file://${__filename}`),'utf8');
const start=html.indexOf('function opsHealthSummary('),end=html.indexOf('\nfunction bindCompactHeader()',start);
assert.ok(start>0&&end>start);
function harness(fetch){
 const card={innerHTML:''};let timeout;
 const context={fetch,AbortController,Date,document:{hidden:false,querySelector:()=>card},
  setTimeout:fn=>(timeout=fn,1),clearTimeout:()=>{},setInterval:()=>1,
  esc:value=>String(value).replace(/[<>&"]/g,char=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;'}[char])),IC:{check:'✓'}};
 vm.createContext(context);
 vm.runInContext('const API="/console/api";let opsData=null,opsState="loading",opsPoll=null,opsBusy=false;\n'+html.slice(start,end),context);
 return {context,card,set(data,state='loaded'){vm.runInContext(`opsData=${JSON.stringify(data)};opsState=${JSON.stringify(state)};paintOps();`,context);},timeout:()=>timeout()};
}
const checkNames=['buttonsbebe-webhook','buttonsbebe-processor','processor_progress','webhook_readiness','buttonsbebe-heartbeat_timer','buttonsbebe-heartbeat_result','helpdesk-inbox','buttonsbebe-inbox-projection_timer','buttonsbebe-inbox-projection_result','inbox_readiness','buttonsbebe-backup_timer','buttonsbebe-backup_result','backup_freshness','buttonsbebe-kb-mcp','buttonsbebe-redo-mcp','buttonsbebe-gorgias-mcp','buttonsbebe-whatsapp-connect','buttonsbebe-kb-admin','kb_socket','redo_socket','gorgias_socket','whatsapp_socket','kb_admin_socket','disk_space'];
const fresh=()=>({status:'ok',checked_at:new Date().toISOString(),checks:Object.fromEntries(checkNames.map(key=>[key,'ok']))});

test('rendered card distinguishes healthy, missing, stale and failed checks',()=>{
 const h=harness();h.set(fresh());assert.match(h.card.innerHTML,/Local checks are passing/);
 assert.match(h.card.innerHTML,/does not send external outage alerts/);
 for(const [data,title] of [[{status:'missing'},'Monitoring has not reported yet'],[{...fresh(),checked_at:'2001-01-01T00:00:00Z'},'out of date'],[{...fresh(),status:'attention',checks:{backup_freshness:'stale'}},'System health needs attention']]){
  h.set(data);assert.ok(h.card.innerHTML.includes(title));assert.doesNotMatch(h.card.innerHTML,/Local checks are passing/);
 }
 assert.match(h.card.innerHTML,/Backup checks/);assert.doesNotMatch(h.card.innerHTML,/backup_freshness/);
});

test('unavailable and malformed reports never render a green empty status or raw text',()=>{
 const h=harness();
 for(const data of [null,{},{status:'ok',checks:{}}, {...fresh(),checked_at:'invalid'}, {...fresh(),checks:{private:'<img src=x onerror=alert(1)>'}}]){
  h.set(data);assert.doesNotMatch(h.card.innerHTML,/Local checks are passing|onerror=|private/);
 }
});

test('authentication failure replaces previous healthy card with sign-in action',async()=>{
 const calls=[];const h=harness(async(...args)=>(calls.push(args),{status:401,ok:false}));h.set(fresh());
 await h.context.loadOps();
 assert.match(h.card.innerHTML,/Sign in to view system health/);assert.match(h.card.innerHTML,/href="\/console\/login"/);
 assert.doesNotMatch(h.card.innerHTML,/Local checks are passing/);
 assert.equal(calls[0][0],'/console/api/ops');assert.equal(calls[0][1].cache,'no-store');
 assert.equal(calls[0][1].method,undefined);
});

test('network failure changes only the health card and never calls whole-page render',async()=>{
 const h=harness(async()=>{throw Error('synthetic-private-error');});
 h.context.render=()=>{throw Error('must not rerender ticket editor');};h.set(fresh());await h.context.loadOps();
 assert.match(h.card.innerHTML,/System health is unavailable/);assert.doesNotMatch(h.card.innerHTML,/synthetic-private-error/);
});

test('bounded timeout makes unavailable visible instead of keeping old green status',async()=>{
 const h=harness((_url,options)=>new Promise((_resolve,reject)=>options.signal.addEventListener('abort',()=>reject(Error('timeout')))));
 h.set(fresh());const pending=h.context.loadOps();h.timeout();await pending;
 assert.match(h.card.innerHTML,/System health is unavailable/);
});

test('overview and settings include health card; ticket view is unchanged',()=>{
 const overview=html.slice(html.indexOf('function overview(){'),html.indexOf('function learnPanel'));
 assert.match(overview,/opsPanel\(\)/);
 const settings=html.slice(html.indexOf('function settingsView(){'),html.indexOf('async function loadKb'));
 assert.match(settings,/opsPanel\(\)/);assert.doesNotMatch(settings,/you're all set/);
 const boot=html.slice(html.indexOf('async function boot(){'),html.indexOf('function goTickets'));
 assert.match(boot,/loadOps\(\);startOpsPoll\(\)/);assert.doesNotMatch(boot,/await loadOps/);
});

test('partial reports and unverified setup steps cannot imply healthy connections',()=>{
 const h=harness();h.set({...fresh(),checks:{processor_progress:'ok'}});
 assert.doesNotMatch(h.card.innerHTML,/Local checks are passing/);
 const settings=html.slice(html.indexOf('function settingsView(){'),html.indexOf('async function loadKb'));
 assert.match(settings,/index\+1/);assert.doesNotMatch(settings,/class="ck"/);
});
