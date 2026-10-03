const $ = selector => document.querySelector(selector);
const node = (tag,text,className='') => {const el=document.createElement(tag);el.textContent=text;if(className)el.className=className;return el;};
const states={attempting:'Attempt unresolved',uncertain:'Outcome uncertain',failed:'Rejected by simulator',simulated_delivered:'Simulated delivery recorded'};
const outcomes={accepted:'Simulated success',rejected:'Rejected before acceptance',accepted_timeout:'Accepted, acknowledgement lost',unknown:'Outcome unavailable'};
const mode='offline_simulation';

export function setupDelivery({api,current,refresh,notice}){
  let draft=null,review=null,busy=false;
  const drafts=new Map();
  function controls(){
    $('#reply-editor').hidden=!!review;$('#reply-review').hidden=!review;
    $('#reply-preview').hidden=!!review;$('#reply-confirm').hidden=!review;$('#reply-edit').hidden=!review;
    for(const el of $('#reply-form').querySelectorAll('button,textarea,select'))el.disabled=busy;
    $('#reply-dialog [data-close]').disabled=busy;
  }
  function open(retry=null,suggestion=null){
    if(busy)return false;
    const ticket=current();if(!ticket?.reply_context.available)return false;
    const existing=drafts.get(ticket.id);
    if(suggestion&&(existing?.body||($('#reply-dialog').open&&$('#reply-body').value)))return false;
    if(suggestion)retry=ticket.deliveries.find(item=>item.state==='failed'&&item.body===suggestion.body&&item.parent_id===ticket.reply_context.parent_id)||null;
    draft={ticket_id:ticket.id,revision:ticket.revision,retry_of:retry?.attempt_id||'',operation_id:crypto.randomUUID(),assistance_run_id:suggestion?.run_id||retry?.assistance_run_id||existing?.assistance_run_id||null};
    review=null;busy=false;$('#reply-error').textContent='';
    $('#reply-body').value=suggestion?.body||retry?.body||existing?.body||'';
    if(suggestion)drafts.set(ticket.id,{body:suggestion.body,assistance_run_id:suggestion.run_id});
    $('#reply-scenario').value='accepted';
    $('#reply-target').textContent=`BB-${ticket.number}${retry?' · Retry of a rejected simulation':''}${draft.assistance_run_id?' · Saved test suggestion':''} · From: ${ticket.reply_context.from} · To: ${ticket.reply_context.to}`;
    controls();if(!$('#reply-dialog').open)$('#reply-dialog').showModal();$('#reply-body').focus();return true;
  }
  $('#reply-clear').addEventListener('click',()=>{if(busy||!draft)return;drafts.delete(draft.ticket_id);draft.assistance_run_id=null;draft.retry_of='';draft.operation_id=crypto.randomUUID();$('#reply-body').value='';$('#reply-target').textContent='Blank manual test reply · Review the current recipient before confirming';$('#reply-body').focus();});
  $('#open-reply').addEventListener('click',()=>open());
  $('#reply-dialog').addEventListener('cancel',event=>{if(busy)event.preventDefault();});
  $('#reply-body').addEventListener('input',()=>{if(draft){drafts.set(draft.ticket_id,{body:$('#reply-body').value,assistance_run_id:draft.assistance_run_id});draft.retry_of='';draft.operation_id=crypto.randomUUID();}});
  $('#reply-scenario').addEventListener('change',()=>{if(draft)draft.operation_id=crypto.randomUUID();});
  $('#reply-edit').addEventListener('click',()=>{review=null;draft.operation_id=crypto.randomUUID();$('#reply-error').textContent='';controls();});
  $('#reply-form').addEventListener('submit',async event=>{
    event.preventDefault();if(busy||review||!draft)return;
    busy=true;controls();$('#reply-error').textContent='';
    try{
      review=await api(`/api/tickets/${draft.ticket_id}/simulation-review`,{mode,operation_id:draft.operation_id,revision:draft.revision,retry_of:draft.retry_of,body:$('#reply-body').value,scenario:$('#reply-scenario').value,...(draft.assistance_run_id?{assistance_run_id:draft.assistance_run_id}:{})});
      $('#review-envelope').textContent=`From: ${review.envelope.from}\nTo: ${review.envelope.to}\nSubject: ${review.envelope.subject}`;
      $('#review-body').textContent=review.body;
      $('#review-outcome').textContent=`Test outcome: ${outcomes[review.scenario]}. Review expires in 10 minutes.`;
    }catch(error){$('#reply-error').textContent=error.message;}
    finally{busy=false;controls();}
  });
  $('#reply-confirm').addEventListener('click',async()=>{
    if(busy||!review)return;
    const frozen=review;busy=true;controls();$('#reply-error').textContent='';
    let failed=false;
    try{
      const result=await api(`/api/tickets/${frozen.ticket_id}/simulation-confirm`,{mode,review_id:frozen.review_id,digest:frozen.digest,confirmed:true});
      drafts.delete(frozen.ticket_id);$('#reply-dialog').close();review=null;
      await refresh(frozen.ticket_id);
      notice(`${states[result.state]}. No email was sent.${['uncertain','attempting'].includes(result.state)?' Check the fake receipt before any further attempt.':''}`);
    }catch(error){
      failed=true;$('#reply-error').textContent=error.message+' Close this dialog and refresh the ticket to inspect its delivery record before another attempt.';
    }finally{busy=false;controls();if(failed){$('#reply-confirm').disabled=true;$('#reply-edit').disabled=true;}}
  });
  const render=ticket=>{
    const context=ticket.reply_context;$('#reply-tools').hidden=false;$('#open-reply').disabled=!context.available;
    $('#reply-unavailable').textContent=context.available?'Review and confirm a local test reply.':context.reason;
    const ledger=$('#delivery-ledger');ledger.replaceChildren();
    const retried=new Set(ticket.deliveries.map(row=>row.retry_of));
    for(const item of ticket.deliveries){
      const row=node('article','','delivery-record');row.dataset.attempt=item.attempt_id;row.dataset.state=item.state;
      row.append(node('strong',states[item.state]),node('p',new Date(item.updated_at).toLocaleString(),'hint'));
      const detail=node('details','');detail.append(node('summary','View reviewed reply'),node('p',`From: ${item.from}\nTo: ${item.to}\nSubject: ${item.subject}`,'source'),node('p',item.body,'delivery-body'));row.append(detail);
      if(['attempting','uncertain'].includes(item.state)){
        row.append(node('p','Acceptance is unproven. Retry is blocked.','hint'));
        const button=node('button','Check fake receipt');button.addEventListener('click',async()=>{
          button.disabled=true;
          try{const result=await api(`/api/tickets/${ticket.id}/simulation-reconcile`,{mode,attempt_id:item.attempt_id,confirmed:true});await refresh(ticket.id);notice(result.state==='uncertain'?'The fake receipt is unavailable. Retry remains blocked.':`${states[result.state]}. No email was sent.`);}
          catch(error){notice(error.message);button.disabled=false;}
        });row.append(button);
      }
      if(item.state==='failed'&&!retried.has(item.attempt_id)&&context.available&&item.parent_id===context.parent_id){
        const button=node('button','Review retry');button.addEventListener('click',()=>open(item));row.append(button);
      }
      ledger.append(row);
    }
    if(!ticket.deliveries.length)ledger.append(node('p','No simulations yet.','muted'));
  };
  render.useSuggestion=suggestion=>open(null,suggestion);
  window.addEventListener('intake-session-ended',()=>{drafts.clear();draft=null;review=null;busy=false;});
  return render;
}
