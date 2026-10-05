import assert from 'node:assert/strict';

export async function verifyDelivery(page,{countText='8'}={}){
  await page.getByRole('button',{name:/Where is the rest of my order/}).click();
  await page.getByRole('heading',{name:'Where is the rest of my order?',exact:true}).waitFor();
  const ticket=()=>page.evaluate(async()=>{
    const {token}=await (await fetch('/api/session')).json();
    const headers={'X-Intake-Token':token};
    const list=await (await fetch('/api/tickets?query=Where%20is%20the%20rest&queue=all',{headers})).json();
    return (await fetch('/api/tickets/'+list.tickets[0].id,{headers})).json();
  });
  async function review(body,scenario='accepted'){
    await page.locator('#open-reply').click();
    await page.locator('#reply-body').fill(body);
    await page.locator('#reply-scenario').selectOption(scenario);
    await page.locator('#reply-preview').click();
    await page.locator('#reply-confirm').waitFor({state:'visible'});
    assert((await page.locator('#review-envelope').textContent()).includes('avery@example.test'));
    assert.equal(await page.locator('#review-body').textContent(),body);
  }
  async function confirm(state){
    await page.locator('#reply-confirm').click();
    await page.locator('#reply-dialog').waitFor({state:'hidden'});
    await page.locator(`.delivery-record[data-state="${state}"]`).first().waitFor();
  }
  await review('Cancelled synthetic draft');
  assert.equal((await ticket()).deliveries.length,0,'Review must not dispatch');
  await page.keyboard.press('Escape');
  assert.equal((await ticket()).deliveries.length,0,'Cancelling review must not dispatch');

  await review('Confirmed synthetic answer');
  await page.evaluate(()=>{const button=document.querySelector('#reply-confirm');button.click();button.click();});
  await page.locator('.delivery-record[data-state="simulated_delivered"]').waitFor();
  let saved=await ticket();
  assert.equal(saved.deliveries.length,1);
  assert.equal(saved.messages.filter(m=>m.origin==='offline_simulation').length,1);
  assert(await page.getByText('Simulated reply · No email sent',{exact:true}).isVisible());

  await review('Draft reviewed before another change');
  await page.evaluate(async()=>{
    const {token}=await (await fetch('/api/session')).json();
    const headers={'X-Intake-Token':token,'Content-Type':'application/json'};
    const list=await (await fetch('/api/tickets?query=Where%20is%20the%20rest',{headers})).json();
    const t=list.tickets[0];
    await fetch('/api/tickets/'+t.id+'/notes',{method:'POST',headers,body:JSON.stringify({operation_id:'d5-other-editor',revision:t.revision,body:'Synthetic concurrent edit'})});
  });
  await page.locator('#reply-confirm').click();
  await page.locator('#reply-error').filter({hasText:'This ticket changed.'}).waitFor();
  assert(await page.locator('#reply-confirm').isDisabled());
  assert.equal((await ticket()).deliveries.length,1);
  await page.getByRole('button',{name:'Close reply simulation',exact:true}).click();
  await page.locator('#refresh').click();
  await page.getByText('Synthetic concurrent edit',{exact:true}).waitFor();

  await review('Synthetic acceptance with lost acknowledgement','accepted_timeout');
  await confirm('uncertain');
  assert(await page.locator('#open-reply').isDisabled());
  saved=await ticket();
  assert.equal(saved.messages.filter(m=>m.origin==='offline_simulation').length,1);
  await page.reload();
  await page.locator('.delivery-record[data-state="uncertain"]').waitFor();
  assert(await page.locator('#open-reply').isDisabled(),'Uncertainty survives reload');
  await page.getByRole('button',{name:'Check fake receipt',exact:true}).click();
  await page.waitForFunction(()=>document.querySelectorAll('.delivery-record[data-state="simulated_delivered"]').length===2);
  assert.equal((await ticket()).messages.filter(m=>m.origin==='offline_simulation').length,2);

  await review('Synthetic rejected reply','rejected');
  await confirm('failed');
  assert.equal((await ticket()).messages.filter(m=>m.origin==='offline_simulation').length,2);
  await page.getByRole('button',{name:'Review retry',exact:true}).click();
  assert.equal(await page.locator('#reply-body').inputValue(),'Synthetic rejected reply');
  await page.locator('#reply-preview').click();
  await confirm('simulated_delivered');
  await page.waitForFunction(()=>document.querySelectorAll('.delivery-record[data-state="simulated_delivered"]').length===3);
  assert.equal((await ticket()).messages.filter(m=>m.origin==='offline_simulation').length,3);

  // Lose the HTTP response after the server committed. Refresh must recover the
  // ledger; composing the identical reply with a new operation cannot duplicate it.
  await review('Synthetic lost browser response');
  const pattern='**/simulation-confirm';
  await page.route(pattern,async route=>{await route.fetch();await route.abort();});
  await page.locator('#reply-confirm').click();
  await page.locator('#reply-error').filter({hasText:'inspect its delivery record'}).waitFor();
  await page.unroute(pattern);
  await page.getByRole('button',{name:'Close reply simulation',exact:true}).click();
  await page.locator('#refresh').click();
  await page.waitForFunction(()=>document.querySelectorAll('.delivery-record[data-state="simulated_delivered"]').length===4);
  await page.locator('#open-reply').click();
  await page.locator('#reply-body').fill('Synthetic lost browser response');
  await page.locator('#reply-preview').click();
  await page.locator('#reply-error').filter({hasText:'already simulated'}).waitFor();
  await page.keyboard.press('Escape');
  assert.equal((await ticket()).messages.filter(m=>m.origin==='offline_simulation').length,4);

  await review('Synthetic unresolved receipt','unknown');
  await confirm('uncertain');
  await page.getByRole('button',{name:'Check fake receipt',exact:true}).click();
  await page.getByText('The fake receipt is unavailable. Retry remains blocked.',{exact:true}).waitFor();
  assert(await page.locator('#open-reply').isDisabled());
  saved=await ticket();
  assert.equal(saved.deliveries.length,6);
  assert.equal(saved.messages.filter(m=>m.origin==='offline_simulation').length,4);
  // Reload intentionally resets the view; restore the caller's All queues view.
  await page.locator('#queue').selectOption('all');
  await page.waitForFunction(expected=>document.querySelector('#count').textContent===expected,countText);
}
