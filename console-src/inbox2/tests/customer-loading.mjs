import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createRequire} from 'node:module';
const require = createRequire(import.meta.url);
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
import assert from 'node:assert/strict';
const baseUrl=process.env.INBOX_TEST_URL||'http://127.0.0.1:8878';
const artifactRoot=process.env.INBOX_TEST_ARTIFACT_DIR||os.tmpdir();
fs.mkdirSync(artifactRoot,{recursive:true});
const dir=fs.mkdtempSync(path.join(artifactRoot,'inbox2-loading-'));
const browser=await chromium.launch({headless:true,args:['--no-sandbox'],...(process.env.INBOX_TEST_BROWSER?{executablePath:process.env.INBOX_TEST_BROWSER}:{})});
const page=await browser.newPage({viewport:{width:1440,height:1000}});
page.setDefaultTimeout(10000);
const errors=[];page.on('pageerror',e=>errors.push(e.message));
let mode='loading',reads=0;
const base={id:'gorgias:123',subject:'Order 10312345',customerName:'Example Customer',fromEmail:'person@example.com',status:'open',messages:[{id:'m',body:'Please check my order.',at:new Date().toISOString()}]};
const found={payloadVersion:2,status:'found',
 customer:{displayName:'Example Customer',numberOfOrders:null,amountSpent:null},
 order:{id:'o',name:'#10312345',currentTotalPriceSet:{shopMoney:{amount:'0.00',currencyCode:'CAD'}},
   lineItems:{nodes:[{title:'Observed zero',quantity:1,originalUnitPriceSet:{shopMoney:{amount:'0.00',currencyCode:'CAD'}}},{title:'Unknown price',quantity:1,originalUnitPriceSet:null}]},
   shippingAddress:{address1:'Example address'}},
 history:[{id:'h',name:'#10000',currentTotalPriceSet:null}],
 returns:{returns:{nodes:[{id:'r',name:'#10312345-R1',status:'OPEN',items:[{title:'Returned item',quantity:1}],itemsTruncated:true}]}},
 partial:{orderSearch:true,orderItems:true,returns:true,history:true},fetchedAt:new Date().toISOString()};
const legacy={status:'found',legacyMoneyUnverified:true,
 customer:{displayName:'Example Customer',numberOfOrders:'4',amountSpent:null},
 order:{id:'o',name:'#10312345',currentTotalPriceSet:null,lineItems:{nodes:[{title:'Legacy price',quantity:1,originalUnitPriceSet:null}]}},
 history:[],returns:{returns:{nodes:[]}},refreshing:true,fetchedAt:new Date().toISOString()};
const withheld={...legacy,order:{...legacy.order,lineItems:{nodes:null}},history:null,
 returns:{returns:{nodes:[{id:'r',status:'OPEN',items:null}]}},
 partial:{orderSearch:null,orderItems:null,returns:null,history:null}};
await page.route('**/inbox/api/helpdesk',async route=>{
 const {tool}=route.request().postDataJSON();
 assert(['helpdesk.get_ticket','helpdesk.list_tickets','helpdesk.capabilities'].includes(tool));
 if(tool==='helpdesk.get_ticket')reads++;
 const rail=mode==='loading'?{status:'loading'}:mode==='found'?found:mode==='legacy'?legacy:mode==='withheld'?withheld:mode==='error'?{status:'error',refreshError:true}:mode==='missing'?{status:'missing'}:{status:'unavailable'};
 await route.fulfill({json:{ok:true,source:'gorgias_api',...(tool==='helpdesk.get_ticket'?{ticket:{...base,shopifyRail:rail}}:tool==='helpdesk.list_tickets'?{tickets:[base],total:1,nextOffset:null,projection:{complete:true}}:{})}});
});
await page.goto(`${baseUrl}/inbox/?ticket=gorgias%3A123`);
await page.getByText('Loading Shopify customer details…').waitFor();
await page.locator('#reply').fill('Do not replace my reply');
mode='found';
await page.locator('.customer-stats').waitFor();
assert.equal(await page.locator('#reply').inputValue(),'Do not replace my reply');
assert(await page.locator('#reply').evaluate(el=>el===document.activeElement));
assert(reads>=2);
for(const text of [
 'More orders matched this reference in Shopify. This snapshot uses the first exact match.',
 'More order items exist in Shopify. This snapshot shows the first 50.',
 'More returns exist in Shopify. This snapshot shows the first 5.',
 'More returned items exist in Shopify. This snapshot shows the first 25.',
 'More past orders exist in Shopify. This snapshot shows the first 50.',
])await page.getByText(text,{exact:true}).waitFor();
assert(await page.getByText('Not available',{exact:true}).count()>=3);
assert((await page.locator('#customer-rail').textContent()).includes('CA$0.00'));
await page.screenshot({path:path.join(dir,'loaded.png')});
mode='legacy';await page.reload();
await page.getByText('Prices from this older snapshot are hidden because their Shopify observation was not recorded.',{exact:true}).waitFor();
await page.getByText('Shopify did not record whether every bounded list is complete for this snapshot.',{exact:true}).waitFor();
assert(!(await page.locator('#customer-rail').textContent()).includes('$0.00'));
mode='withheld';await page.reload();
await page.getByText('Return on file. Item details unavailable.',{exact:true}).waitFor();
await page.getByText('Shopify did not record whether every bounded list is complete for this snapshot.',{exact:true}).waitFor();
assert(!(await page.locator('#customer-rail').textContent()).includes('$0.00'));
mode='error';await page.reload();await page.locator('[data-action="retry-customer"]').waitFor();
mode='found';await page.locator('[data-action="retry-customer"]').click();await page.locator('.customer-stats').waitFor();
mode='missing';await page.reload();await page.getByText('No matching Shopify customer was found.').waitFor();
mode='unavailable';await page.reload();await page.getByText('A consistent customer email is needed to look up Shopify details.').waitFor();
mode='loading';await page.setViewportSize({width:390,height:844});await page.reload();
await page.locator('.show-customer').click();
await page.locator('#customer-rail [data-action="rail-close"]').focus();
mode='found';await page.locator('.customer-stats').waitFor();
assert(await page.locator('#customer-rail [data-action="rail-close"]').evaluate(el=>el===document.activeElement));
await page.keyboard.press('Escape');
assert(await page.locator('.show-customer').evaluate(el=>el===document.activeElement));
assert.deepEqual(errors,[]);
await browser.close();
console.log('Passed: asynchronous details load, partial and legacy notices, observed and unknown money, reply text/focus preservation, retry, missing/unavailable states, mobile drawer focus, no browser errors.');
