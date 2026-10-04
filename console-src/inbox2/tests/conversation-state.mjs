import assert from 'node:assert/strict';
import {createRequire} from 'node:module';

const require=createRequire(import.meta.url);
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const baseUrl=process.env.INBOX_TEST_URL||'http://127.0.0.1:8878';
const browser=await chromium.launch({headless:true,args:['--no-sandbox'],...(process.env.INBOX_TEST_BROWSER?{executablePath:process.env.INBOX_TEST_BROWSER}:{})});

try {
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  page.setDefaultTimeout(10000);
  const errors=[];
  page.on('pageerror',error=>errors.push(error.message));

  const at={
    old:'2026-09-01T10:00:00Z',
    equal:'2026-09-02T10:00:00Z',
    recent:'2026-09-03T10:00:00Z',
    new:'2026-09-04T10:00:00Z',
  };
  const message=(id,body,time)=>({id,body,display_text:body,current_text:body,original_content:body,original_field:'body_text',history_available:true,source_truncated:false,at:time,fromAgent:false,fromName:'Example customer'});
  const baseTicket={id:'gorgias:123',subject:'Conversation state',customerName:'Example customer',fromEmail:'customer@example.com',channel:'email',status:'open',
    updatedAt:at.recent,syncedAt:at.recent,readonlyDraft:'A current suggested reply.',draftSourceMessageId:'m4',draftProcessedAt:at.recent,
    messages:[message('m3','Current m3',at.equal),message('m4','Current m4',at.recent)],messagesNextCursor:'cursor-1',historyIncomplete:true,shopifyRail:{status:'missing'}};
  const otherTicket={...baseTicket,id:'gorgias:789',subject:'Other ticket',messages:[message('z1','Other ticket message',at.recent)],messagesNextCursor:null,historyIncomplete:false};
  const freshTicket={...baseTicket,messages:[message('m3','Fresh m3',at.equal),message('m4','Fresh m4',at.recent),message('m5','Fresh m5',at.new)]};
  const olderPage={messages:[message('m1','Older m1',at.old),message('m2','Equal-time m2',at.equal),message('m3','Stale m3',at.equal)],nextCursor:null};
  let current123=structuredClone(baseTicket);
  let ticketHandler=async ({ticketId})=>structuredClone(ticketId===otherTicket.id?otherTicket:current123);
  let messagesHandler=async ()=>structuredClone(olderPage);

  await page.route('**/inbox/api/helpdesk',async route=>{
    const {tool,arguments:args}=route.request().postDataJSON();
    assert(['helpdesk.capabilities','helpdesk.list_tickets','helpdesk.get_ticket','helpdesk.get_messages'].includes(tool));
    if(tool==='helpdesk.get_ticket')return route.fulfill({json:{ok:true,source:'gorgias_api',ticket:await ticketHandler(args)}});
    if(tool==='helpdesk.get_messages'){
      const result=await messagesHandler(args);
      return route.fulfill({json:{ok:true,source:'gorgias_api',...result}});
    }
    if(tool==='helpdesk.list_tickets')return route.fulfill({json:{ok:true,source:'gorgias_api',tickets:[baseTicket,otherTicket],total:2,nextOffset:null,projection:{complete:true,generatedAt:at.new}}});
    return route.fulfill({json:{ok:true,source:'gorgias_api',readOnly:true}});
  });

  const deferred=()=>{
    let resolve;
    const promise=new Promise(done=>resolve=done);
    return {promise,resolve};
  };
  const ids=()=>page.locator('.message[data-message-id]').evaluateAll(nodes=>nodes.map(node=>node.dataset.messageId));
  const reset=async ()=>{
    current123=structuredClone(baseTicket);
    ticketHandler=async ({ticketId})=>structuredClone(ticketId===otherTicket.id?otherTicket:current123);
    messagesHandler=async ()=>structuredClone(olderPage);
    await page.goto(`${baseUrl}/inbox/?ticket=gorgias%3A123`);
    await page.locator('#reply').waitFor();
  };
  const refresh=async ()=>page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));

  await reset();
  let started=deferred(),release=deferred();
  messagesHandler=async ()=>{started.resolve();await release.promise;return structuredClone(olderPage);};
  await page.getByRole('button',{name:'Load earlier messages'}).click();
  await started.promise;
  current123=structuredClone(freshTicket);
  await refresh();
  await page.locator('[data-message-id="m5"]').waitFor();
  release.resolve();
  await page.locator('[data-message-id="m1"]').waitFor();
  assert.deepEqual(await ids(),['m1','m2','m3','m4','m5']);
  assert.equal(await page.locator('[data-message-id="m3"] .message-body').textContent(),'Fresh m3');

  await reset();
  const cursors=[];
  messagesHandler=async ({cursor})=>{
    cursors.push(cursor);
    return cursor==='cursor-1'
      ? {messages:[message('m2','Equal-time m2',at.equal),message('m3','Stale m3',at.equal)],nextCursor:'cursor-2'}
      : {messages:[message('m1','Older m1',at.old)],nextCursor:null};
  };
  await page.getByRole('button',{name:'Load earlier messages'}).click();
  await page.locator('[data-message-id="m2"]').waitFor();
  await page.getByRole('button',{name:'Load earlier messages'}).click();
  await page.locator('[data-message-id="m1"]').waitFor();
  assert.deepEqual(cursors,['cursor-1','cursor-2']);
  assert.deepEqual(await ids(),['m1','m2','m3','m4']);
  assert.equal(await page.locator('[data-message-id="m3"] .message-body').textContent(),'Current m3');

  await reset();
  await page.getByRole('button',{name:'Load earlier messages'}).click();
  await page.locator('[data-message-id="m1"]').waitFor();
  started=deferred();release=deferred();
  ticketHandler=async ({ticketId})=>{
    if(ticketId===otherTicket.id)return structuredClone(otherTicket);
    started.resolve();await release.promise;return structuredClone(freshTicket);
  };
  await refresh();await started.promise;release.resolve();
  await page.locator('[data-message-id="m5"]').waitFor();
  assert.deepEqual(await ids(),['m1','m2','m3','m4','m5']);
  assert.equal(await page.locator('[data-message-id="m3"] .message-body').textContent(),'Fresh m3');

  await reset();
  started=deferred();release=deferred();
  messagesHandler=async ()=>{started.resolve();await release.promise;return structuredClone(olderPage);};
  await page.getByRole('button',{name:'Load earlier messages'}).click();await started.promise;
  current123={...structuredClone(freshTicket),messagesNextCursor:'cursor-2'};
  await refresh();await page.locator('[data-message-id="m5"]').waitFor();release.resolve();
  await page.waitForTimeout(100);
  assert.deepEqual(await ids(),['m3','m4','m5']);
  assert(await page.getByRole('button',{name:'Load earlier messages'}).isVisible());

  await reset();
  started=deferred();release=deferred();
  messagesHandler=async ()=>{started.resolve();await release.promise;return structuredClone(olderPage);};
  await page.getByRole('button',{name:'Load earlier messages'}).click();await started.promise;
  await page.locator('[data-ticket="gorgias:789"]').click();
  await page.locator('[data-message-id="z1"]').waitFor();release.resolve();await page.waitForTimeout(100);
  assert.deepEqual(await ids(),['z1']);

  await page.evaluate(()=>{localStorage.removeItem('bb-inbox2-composer-v1');history.pushState({},'', '/inbox/?ticket=gorgias%3A123');dispatchEvent(new PopStateEvent('popstate'));});
  const editor=page.locator('#reply');await editor.waitFor();
  await editor.focus();await page.keyboard.type('alpha');await page.keyboard.press('ArrowLeft');await page.keyboard.type('X');
  assert.equal(await editor.inputValue(),'alphXa');
  await editor.evaluate(element=>{window.__stableEditor=element;element.setSelectionRange(1,4,'backward');element.focus();});
  current123=structuredClone(freshTicket);
  await refresh();await page.locator('[data-message-id="m5"]').waitFor();
  assert.deepEqual(await editor.evaluate(element=>({
    same:element===window.__stableEditor,
    connected:element.isConnected,
    focused:element===document.activeElement,
    value:element.value,
    start:element.selectionStart,
    end:element.selectionEnd,
    direction:element.selectionDirection,
  })),{same:true,connected:true,focused:true,value:'alphXa',start:1,end:4,direction:'backward'});
  const modifier=await page.evaluate(()=>navigator.platform.includes('Mac')?'Meta':'Control');
  await page.keyboard.press(`${modifier}+z`);assert.equal(await editor.inputValue(),'alpha');
  await page.keyboard.press(`${modifier}+Shift+z`);assert.equal(await editor.inputValue(),'alphXa');

  await editor.evaluate(element=>window.__stableEditor=element);
  await page.locator('#details-tab').click();await page.locator('#conversation-tab').click();
  assert(await editor.evaluate(element=>element===window.__stableEditor&&element.isConnected));
  await page.getByRole('button',{name:'Dismiss'}).click();
  assert.equal(await editor.inputValue(),'alphXa');
  assert(await editor.evaluate(element=>element===window.__stableEditor&&element.isConnected));

  const cdp=await page.context().newCDPSession(page);
  await editor.fill('');
  await editor.evaluate(element=>{
    window.__stableEditor=element;
    window.__imeEvents=[];
    element.addEventListener('compositionstart',event=>window.__imeEvents.push(['start',event.isTrusted]));
    element.addEventListener('compositionend',event=>window.__imeEvents.push(['end',event.isTrusted]));
    element.focus();
  });
  await cdp.send('Input.imeSetComposition',{text:'あ',selectionStart:1,selectionEnd:1});
  await page.waitForFunction(()=>window.__imeEvents.some(([name])=>name==='start'));
  current123={...structuredClone(freshTicket),subject:'Refreshed during composition',messages:[...freshTicket.messages,message('ime-refresh','Refreshed during composition',at.new)]};
  await refresh();await page.locator('.message[data-message-id="ime-refresh"]').waitFor();
  assert.deepEqual(await editor.evaluate(element=>({
    same:element===window.__stableEditor,
    connected:element.isConnected,
    focused:element===document.activeElement,
    ended:window.__imeEvents.some(([name])=>name==='end'),
    trustedStart:window.__imeEvents.some(([name,trusted])=>name==='start'&&trusted),
  })),{same:true,connected:true,focused:true,ended:false,trustedStart:true});
  await cdp.send('Input.insertText',{text:'愛'});
  await page.waitForFunction(()=>window.__imeEvents.some(([name])=>name==='end'));
  assert.equal(await editor.inputValue(),'愛');

  assert.deepEqual(errors,[]);
  console.log('Passed: both history race orders, equal-time ID merge, fresh overlap precedence, cursor and ticket invalidation, stable connected editor, focus, selection, undo/redo, and trusted IME continuity.');
} finally {
  await browser.close();
}
