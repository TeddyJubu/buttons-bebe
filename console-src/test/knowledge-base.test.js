const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const source = fs.readFileSync(new URL('../index.html', `file://${__filename}`), 'utf8');

async function fixture(t, width = 1440) {
  let chromium;
  try { ({ chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright')); }
  catch (e) { if (e.code !== 'MODULE_NOT_FOUND') throw e; t.skip('Playwright required'); return; }
  const browser = await chromium.launch({headless:true, args:['--no-sandbox']});
  t.after(() => browser.close());
  const page = await browser.newPage({viewport:{width,height:1000},reducedMotion:'reduce'});
  page.setDefaultTimeout(5000);
  const state = {writes:[], errors:[], failList:false, failFile:false, failSave:false, failIndex:false, indexOk:true, delayFile:null};
  const files = {
    'policies/shipping.md': '---\ntitle: Shipping and delivery\ncategory: policies\n---\n\n## Before you reply\n\nRead the **order details**.\n\n- Check tracking\n- Confirm the address\n\n<img src=x onerror=alert(1)>\n\n[Unsafe](javascript:alert) [Tracking](https://example.com/track)\n\n| Status | Action |\n| --- | --- |\n| Delayed | Review |',
    'faq/sizing.md': '# Finding the right size\n\nA sample answer.',
  };
  const library = {folders:[{folder:'policies',files:[{path:'policies/shipping.md',title:'Shipping and delivery'}]},{folder:'faq',files:[{path:'faq/sizing.md',title:'Finding the right size'}]}]};
  page.on('pageerror',e=>state.errors.push(e.message));
  t.after(() => assert.deepEqual(state.errors,[], 'no browser exceptions'));
  await page.route('**/*', async route => {
    const req=route.request(),url=new URL(req.url()),p=url.pathname;
    if(p==='/console/')return route.fulfill({contentType:'text/html',body:source});
    if(req.method()==='POST')state.writes.push({path:p,data:req.postDataJSON()});
    if(p.endsWith('/list'))return route.fulfill({status:state.failList?503:200,json:state.failList?{}:library});
    if(p.endsWith('/file')){
      const path=url.searchParams.get('path');
      if(state.delayFile===path)await new Promise(resolve=>state.releaseFile=resolve);
      return route.fulfill({status:state.failFile?404:200,json:state.failFile?{error:'File unavailable'}:{content:files[path]}});
    }
    if(p.endsWith('/save')){const d=req.postDataJSON();if(!state.failSave)files[d.path]=d.content;return route.fulfill({status:state.failSave?500:200,json:{ok:!state.failSave}});}
    if(p.endsWith('/new')){const d=req.postDataJSON(),path=d.folder+'/new-article.md';files[path]=d.content;library.folders.find(g=>g.folder===d.folder).files.push({path,title:d.filename});return route.fulfill({json:{ok:true,path}});}
    if(p.endsWith('/reindex'))return route.fulfill({status:state.failIndex?503:200,json:{started:true}});
    if(p.endsWith('/reindex-status'))return route.fulfill({json:{running:false,ok:state.indexOk}});
    const data={'/console/api/stats':{},'/console/api/tickets':[],'/console/api/learning':{},'/console/api/ops':{status:'missing'},'/console/kbapi/health':{ok:true,folders:{},products:{}},'/console/api/notifications':{unread_count:0,notifications:[]},'/console/waapi/status':{state:'connected'}};
    if(Object.hasOwn(data,p))return route.fulfill({json:data[p]});
    return route.abort();
  });
  await page.goto('http://console.test/console/');
  await page.locator('.kpi').first().waitFor();
  if(width<=900)await page.locator('#menu-btn').click();
  await page.locator('[data-tab=kb]').click();
  await page.locator('#kb-search').waitFor();
  return {page,state,files};
}

async function openArticle(page,path='policies/shipping.md'){
  await page.locator(`[data-open="${path}"]`).click();
  await page.locator('.kb-document').waitFor();
}

test('KB browsing filters titles and paths, previews safe Markdown, and performs no writes',async t=>{
  const f=await fixture(t);if(!f)return;const {page,state}=f;
  await page.locator('#kb-search').fill('SIZING');
  assert.equal(await page.locator('.kbitem').count(),1);
  await page.locator('#kb-search').fill('policies/');
  assert.equal(await page.locator('.kbitem').count(),1);
  await page.locator('#kb-search').fill('no-such-article');
  await page.getByRole('heading',{name:'No matching articles'}).waitFor();
  await page.locator('#kb-clear-search').click();
  await page.locator('[data-kb-category=faq]').click();
  assert.equal(await page.locator('.kbitem').count(),1);
  await page.locator('[data-kb-category=all]').click();
  await openArticle(page);
  assert.match(await page.locator('.kb-document strong').innerText(),/order details/);
  assert.equal(await page.locator('.kb-document img, .kb-document script, .kb-document a[href^="javascript:"]').count(),0);
  assert.equal(await page.locator('.kb-document table').count(),1);
  assert.doesNotMatch(await page.locator('.kb-document').innerText(),/category: policies/);
  assert.deepEqual(state.writes,[]);
});

test('KB keeps unsaved edits through preview, filtering, background render, file switches and failed saves',async t=>{
  const f=await fixture(t);if(!f)return;const {page,state,files}=f;
  await openArticle(page);await page.locator('[data-kb-mode=edit]').click();
  const draft=files['policies/shipping.md']+'\n\nLocal draft that must survive.';
  await page.locator('#kb-text').fill(draft);
  await page.locator('#kb-text').evaluate(el=>el.setSelectionRange(12,15));
  await page.evaluate(()=>render());
  assert.equal(await page.locator('#kb-text').inputValue(),draft);
  assert.equal(await page.locator('#kb-text').evaluate(el=>el.selectionStart),12);
  assert.equal(await page.locator('#kb-text').evaluate(el=>el===document.activeElement),true);
  await page.locator('[data-kb-mode=preview]').click();
  assert.match(await page.locator('.kb-document').innerText(),/Local draft that must survive/);
  await openArticle(page,'faq/sizing.md');
  await page.locator('[data-open="policies/shipping.md"]').click();
  assert.equal(await page.locator('#kb-text').inputValue(),draft);
  await page.locator('#kb-search').fill('shipping');
  assert.equal(await page.locator('#kb-text').inputValue(),draft);
  state.failSave=true;await page.locator('#kb-save').click();
  await page.getByRole('alert').filter({hasText:'Could not save'}).waitFor();
  assert.equal(await page.locator('#kb-text').inputValue(),draft);
  state.failSave=false;await page.locator('#kb-save').click();
  await page.getByRole('status').filter({hasText:'Article saved'}).waitFor();
  assert.equal(files['policies/shipping.md'],draft);
  assert.equal(await page.locator('#kb-save').isDisabled(),true);
  assert.equal(await page.evaluate(()=>kbDrafts.size),0);
  assert.deepEqual(state.writes.map(w=>w.path),['/console/kbapi/save','/console/kbapi/save']);
});

test('KB ignores late file responses and disables editing when a file fails to load',async t=>{
  const f=await fixture(t);if(!f)return;const {page,state}=f;
  state.delayFile='policies/shipping.md';
  await page.locator('[data-open="policies/shipping.md"]').click();
  await openArticle(page,'faq/sizing.md');
  state.releaseFile();state.delayFile=null;
  await page.waitForTimeout(100);
  assert.equal(await page.locator('#kb-article-title').innerText(),'Finding the right size');
  assert.match(await page.locator('.kb-document').innerText(),/A sample answer/);
  state.failFile=true;await page.locator('[data-open="policies/shipping.md"]').click();
  await page.locator('#kb-retry-file').waitFor();
  assert.equal(await page.locator('#kb-save, #kb-text').count(),0);
  state.failFile=false;await page.locator('#kb-retry-file').click();await page.locator('.kb-document').waitFor();
  assert.deepEqual(state.writes,[]);
});

test('KB creates an article through a labeled form and leaves search publication explicit',async t=>{
  const f=await fixture(t);if(!f)return;const {page,state}=f;
  await page.locator('.kb-toolbar [data-kb-new]').click();
  await page.getByLabel('Article title',{exact:true}).fill('New article');
  await page.getByLabel('Collection',{exact:true}).selectOption('faq');
  await page.getByRole('button',{name:'Create article',exact:true}).click();
  await page.locator('#kb-text').waitFor();
  assert.match(await page.locator('#kb-text').inputValue(),/title: "New article"/);
  assert.equal(await page.locator('#kb-article-title').innerText(),'New article');
  assert.deepEqual(state.writes.map(w=>w.path),['/console/kbapi/new']);
});

test('KB reports list, re-index start and re-index completion failures truthfully',async t=>{
  const f=await fixture(t);if(!f)return;const {page,state}=f;
  state.failList=true;await page.locator('#refresh').click();await page.locator('#kb-retry-list').waitFor();
  state.failList=false;await page.locator('#kb-retry-list').click();await page.locator('#kb-reindex').waitFor();
  state.failIndex=true;await page.locator('#kb-reindex').click();
  await page.getByRole('alert').filter({hasText:'Could not start re-indexing'}).waitFor();
  state.failIndex=false;state.indexOk=false;await page.locator('#kb-reindex').click();
  await page.getByRole('alert').filter({hasText:'Re-indexing failed'}).waitFor();
  state.indexOk=true;await page.locator('#kb-reindex').click();
  await page.getByRole('status').filter({hasText:'Search updated.'}).first().waitFor();
});

test('KB reading, editing and article creation fit desktop, tablet and mobile widths',async t=>{
  const f=await fixture(t);if(!f)return;const {page}=f;
  await openArticle(page);
  for(const width of [1440,1100,1024,900,768,760,640,390,320]){
    await page.setViewportSize({width,height:900});
    for(const mode of ['preview','edit']){
      await page.locator(`[data-kb-mode=${mode}]`).click();
      const layout=await page.evaluate(()=>({body:document.documentElement.scrollWidth,viewport:innerWidth,wrap:document.querySelector('.wrap').scrollWidth,client:document.querySelector('.wrap').clientWidth}));
      assert(layout.body<=layout.viewport&&layout.wrap<=layout.client,`${mode} at ${width}: ${JSON.stringify(layout)}`);
    }
  }
  await page.locator('.kb-toolbar [data-kb-new]').click();
  assert.equal(await page.getByLabel('Article title',{exact:true}).isVisible(),true);
  await page.locator('#kb-new-cancel').click();
  assert.equal(await page.locator('.kb-toolbar [data-kb-new]').evaluate(el=>el===document.activeElement),true);
});
