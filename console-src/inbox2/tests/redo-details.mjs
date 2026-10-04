import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const base=process.env.INBOX_TEST_URL||'http://127.0.0.1:8878';
const evidence=process.env.INBOX_EVIDENCE_DIR||'/tmp/bb-inbox-release-20261005/evidence';
const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
try {
  const page=await browser.newPage({viewport:{width:1280,height:800}});
  page.setDefaultTimeout(10000);
  const errors=[],mutations=[];
  page.on('pageerror',error=>errors.push(error.message));
  const stamp='2026-10-05T10:00:00Z';
  const matched={id:'matched-return',order_name:'#12345',status:'pending',tracking_url:'javascript:alert(1)',
    totals:{refund:'12.35',currency:'USD',private_address:'PRIVATE-TOTAL'},
    refunds:Array.from({length:12},(_,i)=>({amount:(5+i).toFixed(2),currencyCode:'USD',private_email:'PRIVATE-REFUND'})),
    compensation_methods:[{type:'store_credit',amount:'20.25'}],gift_cards:[{name:'Gift card retained',amount:'7.70'}],
    exchange:{itemCount:2,name:'Replacement dress',private_customer:'PRIVATE-EXCHANGE'},
    items:[{name:'<img src=x onerror=bad()>',quantity:2}],
    shipments:[{carrier:'USPS',trackingNumber:'SHIPMENT-123',trackingUrl:'https://example.invalid/shipment'}],
    tracking:{carrier:'UPS',trackingNumber:'TRACKING-123',trackingUrl:'https://example.invalid/tracking'},
    private_metadata:'PRIVATE-RETURN'};
  const mismatched={id:'MISMATCHED-RETURN',order_name:'99999',status:'MISMATCHED-STATUS',refunds:[{amount:'999.99'}]};
  const order=()=>({status:'observed',observedAt:stamp,returns:[matched,mismatched],rejected:1,truncated:true});
  let redo={status:'partial',source:'redo',fetchedAt:stamp,incomplete:['45678'],orders:{'12345':order()}};
  const ticket=()=>({id:'gorgias:123',subject:'Synthetic Redo detail test',customerName:'Example Customer',fromEmail:'example@example.invalid',status:'open',updatedAt:stamp,syncedAt:stamp,messages:[{id:'message',fromAgent:false,body:'Please check my return.',at:stamp}],shopifyRail:{status:'observed',customer:{displayName:'Example Customer'},order:{name:'#12345',lineItems:{nodes:[]}},returns:{returns:{nodes:[]}}},redoDetails:redo});
  await page.route('**/console/api/**',async route=>{mutations.push(route.request().url());await route.fulfill({status:403,json:{}});});
  await page.route('**/inbox/api/helpdesk',async route=>{
    const payload=route.request().postDataJSON(),name=payload.tool.split('.').at(-1);
    assert(['capabilities','get_ticket','get_messages','list_tickets'].includes(name));
    await route.fulfill({json:{ok:true,source:'gorgias_api',...(name==='get_ticket'?{ticket:ticket()}:name==='list_tickets'?{tickets:[ticket()],total:1,nextOffset:null,projection:{complete:true}}:{})}});
  });
  await page.goto(`${base}/inbox/?ticket=gorgias%3A123`);
  await page.locator('.redo-section').waitFor();
  await page.locator('.redo-section>summary').click();
  const section=page.locator('.redo-section');
  assert((await section.textContent()).includes('Partial Redo observation'));
  assert((await section.textContent()).includes('Incomplete orders · 45678'));
  assert.equal(await section.locator('.redo-return').count(),1);
  assert.equal(await section.locator('.return-row>strong').textContent(),'Order 12345');
  assert.equal(await section.locator('[data-redo-field="refunds"] li').count(),10);
  for(const text of ['5.00','12.35','20.25','7.70','Gift card retained','Replacement dress','SHIPMENT-123','TRACKING-123','<img src=x onerror=bad()>'])assert((await section.textContent()).includes(text),`Missing retained value ${text}`);
  assert.equal(await section.locator('img,script').count(),0);
  const urls=await section.locator('a').evaluateAll(links=>links.map(a=>a.href));
  assert.deepEqual(urls,['https://example.invalid/shipment','https://example.invalid/tracking']);
  assert.equal((await section.textContent()).includes('MISMATCHED'),false);
  assert.equal((await section.textContent()).includes('PRIVATE-'),false);
  assert((await section.textContent()).includes('Some records lacked a matching order identity'));
  assert((await section.textContent()).includes('Showing the first 10 matching returns'));
  assert((await section.textContent()).includes('10 entries per list, 3 nested levels, and 200 characters'));
  fs.mkdirSync(evidence,{recursive:true});
  await page.locator('#customer-rail').evaluate(el=>el.scrollTop=0);
  await page.screenshot({path:`${evidence}/redo-partial.png`});
  await section.locator('[data-redo-field="refunds"]>summary').click();
  await page.screenshot({path:`${evidence}/redo-partial-structured.png`});
  async function refresh(expected){await page.locator('[data-action="refresh"]').click();await page.waitForFunction(text=>document.querySelector('.redo-state')?.textContent.includes(text),expected);assert.equal(await page.locator('#reply').inputValue(),'My retained manual reply');}
  await page.locator('#reply').fill('My retained manual reply');
  redo={status:'observed',fetchedAt:stamp,orders:{'12345':{status:'observed',observedAt:stamp,returns:[{id:'minimal-return',order_name:'12345',status:'open'}]}}};
  await refresh('returns matched');
  for(const name of ['totals','refunds','compensation_methods','gift_cards','exchange','items','shipments','tracking'])assert.equal((await section.locator(`[data-redo-field="${name}"]`).textContent()).includes('Not reported by Redo.'),true,`Missing absence for ${name}`);
  redo={status:'stale',fetchedAt:stamp,orders:{'12345':{...order(),refreshFailed:true}}};
  await refresh('refresh is delayed');
  assert((await section.textContent()).includes('5.00'));
  assert((await section.textContent()).includes('Showing the previous observation for this order'));
  assert((await section.textContent()).includes('Redo observation'));
  assert((await section.textContent()).includes('Observed'));
  redo={status:'empty',fetchedAt:stamp,orders:{'12345':{status:'empty',observedAt:stamp,returns:[]}}};
  await refresh('No matching Redo return was observed');
  assert((await section.textContent()).includes('No matching return in this observation'));
  assert.equal(await section.locator('.redo-return').count(),0);
  redo={status:'unavailable',orders:{'12345':order()}};
  await refresh('unavailable for this verified identity');
  assert.equal(await section.locator('.return-row').count(),0);
  assert((await page.locator('#customer-rail').textContent()).includes('Shopify returns'));
  redo={status:'pending',requestedAt:stamp};
  await refresh('Waiting for a Redo read');
  assert.equal(await section.locator('.return-row').count(),0);
  assert.equal(mutations.length,0);
  assert.equal(errors.length,0,errors.join('\n'));
  console.log(`Passed partial retained matches, eight bounded reviewed structures, explicit missing fields, mismatched-return isolation, safe URLs, observed/empty/stale/unavailable/pending states, independent Shopify details and saved reply. Evidence ${evidence}/redo-partial-structured.png`);
} finally {await browser.close();}
