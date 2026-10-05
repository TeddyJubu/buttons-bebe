// Renders saved test evidence and canned adapter results; never requests a model.
const node=(tag,text='',className='')=>{const el=document.createElement(tag);el.textContent=text;el.className=className;return el;};
const titles={ready:'Suggested reply · Test fixture',needs_staff:'Needs staff input',no_reply:'No reply needed · Test fixture',failed:'Suggestion unavailable',running:'Fixture run pending',stale:'Suggestion out of date',dismissed:'Suggestion dismissed'};
export function setupAssistance({api,current,refresh,notice,canWork,seedReply}){
  let busy=false;const pending=new Map();
  window.addEventListener('intake-session-ended',()=>{pending.clear();busy=false;});
  async function action(kind){
    const ticket=current(),a=ticket?.assistance;if(!a||busy||!canWork())return;
    const fields=kind==='run'?{mode:'offline_fixture',revision:ticket.revision,fixture_digest:a.fixture.digest}:{run_id:a.draft.id};
    const key=JSON.stringify([ticket.id,kind,fields]);const data=pending.get(key)||{...fields,operation_id:crypto.randomUUID()};pending.set(key,data);
    busy=true;render(ticket);
    try{
      const result=await api(`/api/tickets/${ticket.id}/assistance-${kind}`,data);pending.delete(key);
      if(kind==='use'){
        if(current()?.id!==ticket.id){notice('The selected ticket changed. Open that ticket to use its suggestion.');return;}
        if(!seedReply(result)){notice('Your existing reply is still in the editor. Clear it before using a different suggestion.');return;}
        notice('Test suggestion copied to the reply editor. Review and confirm any simulation.');
      }else{await refresh(ticket.id);notice(kind==='dismiss'?'Suggestion dismissed for this workspace.':'Offline fixture run recorded. No model was called.');}
    }catch(error){if([400,403,409].includes(error.status))pending.delete(key);notice(error.message);}
    finally{busy=false;if(current()?.id===ticket.id)render(current());}
  }
  function button(text,kind,disabled){const el=node('button',text,'button');el.type='button';el.disabled=busy||disabled;el.addEventListener('click',()=>action(kind));return el;}
  function render(ticket){
    const card=document.querySelector('#assistance-card')||document.createElement('section'),rail=document.querySelector('#fixture-context');if(!rail)return;
    card.replaceChildren();rail.replaceChildren();const a=ticket.assistance;
    if(!a?.fixture){card.append(node('p',a?.reason||'Assistance fixtures are unavailable.','small muted'));rail.append(node('h3','Saved test context'),node('p','No context fixture loaded. Live lookups are disabled.','small muted'));return;}
    card.append(node('p','Offline test fixture · No model call','fixture-badge'),node('h3',a.draft?titles[a.draft.state]:'Saved fixture ready to run'),node('p',a.fixture.label,'small muted'));
    if(a.draft){
      const d=a.draft;
      if(d.body)card.append(node('div',d.body,'draft-body'));
      if(d.reason)card.append(node('p',d.reason,'small'));
      if(d.priority)card.append(node('p',`Suggested priority: ${d.priority}${d.review_required?' · Review required':''} · Ticket unchanged`,'small muted'));
      if(d.missing_facts?.length){const list=node('ul');for(const item of d.missing_facts)list.append(node('li',item));card.append(list);}
      if(d.staff_next_step)card.append(node('p','Staff next step: '+d.staff_next_step,'staff-step'));
      if(d.cited_evidence_ids?.length)card.append(node('p','Evidence: '+d.cited_evidence_ids.join(', '),'small muted'));
    }
    if(a.reason)card.append(node('p',a.reason,'small'));
    if(canWork()){
      const actions=node('div','','fixture-actions');actions.append(button(a.draft?'Run fixture again':'Run fixture','run',!a.can_run));
      if(a.draft){actions.append(button('Use test suggestion','use',!a.can_use));if(!['dismissed','running'].includes(a.draft.state))actions.append(button('Dismiss suggestion','dismiss',false));}
      card.append(actions);
    }
    rail.append(node('h3','Saved test context'),node('p','Offline fixture · Not a live lookup','fixture-badge'));
    if(!a.context){rail.append(node('p',a.reason||'Current context is unavailable.','small'));return;}
    rail.append(node('p','Captured '+new Date(a.context.captured_at).toLocaleString()+' · Expires '+new Date(a.context.expires_at).toLocaleString(),'small muted'));
    for(const [key,section] of Object.entries(a.context.sections)){
      const group=node('details');group.open=section.state==='available';group.append(node('summary',key[0].toUpperCase()+key.slice(1)+' · '+section.state));
      if(section.reason)group.append(node('p',section.reason,'small'));
      for(const evidence of section.records){const record=node('div','','fixture-evidence');record.append(node('strong',evidence.title),node('p',evidence.text),node('span',evidence.id,'small muted'));group.append(record);}
      rail.append(group);
    }
  }
  return render;
}
