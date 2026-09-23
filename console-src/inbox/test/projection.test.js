import test from 'node:test';
import assert from 'node:assert/strict';
import { createThreadTissue } from '../js/tissues/thread.js';

test('observed history escapes customer text and keeps the readonly draft out of the thread', () => {
  const tissue = createThreadTissue({mailbox:{publish(){}}});
  const html=tissue.render({capabilities:{summarizeThread:false,escalateTicket:false},ticket:{
    id:'gorgias:1',projectionSource:true,projection:{stale:true},historyIncomplete:true,truncated:true,
    customerName:'Customer',subject:'<script>subject</script>',status:'unknown',statusEvents:[],
    messages:[{id:'m1',from:'customer',body:'<img onerror=alert(1)>',at:'2026-01-01'}],
    readonlyDraft:'<script>draft</script>'
  }});
  assert.doesNotMatch(html,/<script>|<img onerror/);
  // The readonly draft lives in the composer strip now (Task 1): the thread
  // must not render it as a lookalike customer bubble.
  assert.doesNotMatch(html,/&lt;script&gt;draft/);
  assert.doesNotMatch(html,/not sent · read only/);
  // Task 2: the thread keeps its partial-history note; the stale verdict
  // lives once in the list banner, not here.
  assert.match(html,/Partial webhook history/);
  assert.doesNotMatch(html,/Snapshot is stale/);
  assert.match(html,/Status unknown/);
  assert.doesNotMatch(html,/data-escalate=/);
});


test('observed history thread shows Gorgias status when it was stored', () => {
  const tissue = createThreadTissue({mailbox:{publish(){}}});
  const html=tissue.render({capabilities:{summarizeThread:false,escalateTicket:false},ticket:{
    id:'gorgias:1',projectionSource:true,customerName:'Customer',subject:'Hello',status:'open',statusEvents:[],
    messages:[{id:'m1',from:'customer',body:'Hi',at:'2026-01-01'}]
  }});
  assert.match(html,/title="Ticket status">Open<\/span>/);
  assert.doesNotMatch(html,/>Status unknown</);
});

test('a stripped message body renders clean, with the original behind a toggle', () => {
  // #43: the bubble shows only the customer's own words; the verbatim
  // original — signature and quoted history included — collapses behind a
  // "Show original" toggle, like Gorgias. Hostile originalText must escape.
  const tissue = createThreadTissue({mailbox:{publish(){}}});
  const original='Please change it to a 12.\nSent from my Galaxy\n-------- Original message --------\nFrom: Buttons Bebe <hello@bb.com>';
  const html=tissue.render({capabilities:{summarizeThread:false,escalateTicket:false},ticket:{
    id:'gorgias:1',projectionSource:true,customerName:'Customer',subject:'Hello',status:'open',statusEvents:[],
    messages:[{id:'m1',from:'customer',body:'Please change it to a 12.',originalText:original,at:'2026-01-01'}]
  }});
  const bubble=html.split('class="bubble customer"')[1]?.split('</article>')[0] || '';
  assert.match(bubble,/Please change it to a 12\./);
  assert.doesNotMatch(bubble,/<p>Please change it to a 12\.\s*Sent from my Galaxy/,
    'the signature is not glued into the body paragraph');
  assert.match(html,/Show original/, 'the toggle renders');
  assert.match(html,/Sent from my Galaxy/, 'the original stays retrievable');
  assert.match(html,/Original message/, 'the quoted history stays retrievable');
  const hostile=tissue.render({capabilities:{summarizeThread:false,escalateTicket:false},ticket:{
    id:'gorgias:1',projectionSource:true,customerName:'Customer',subject:'Hello',status:'open',statusEvents:[],
    messages:[{id:'m1',from:'customer',body:'Hi',originalText:'<img onerror=alert(1)>',at:'2026-01-01'}]
  }});
  assert.doesNotMatch(hostile,/<img onerror/);
  // No originalText ⇒ no toggle at all.
  const clean=tissue.render({capabilities:{summarizeThread:false,escalateTicket:false},ticket:{
    id:'gorgias:1',projectionSource:true,customerName:'Customer',subject:'Hello',status:'open',statusEvents:[],
    messages:[{id:'m1',from:'customer',body:'Hi',at:'2026-01-01'}]
  }});
  assert.doesNotMatch(clean,/Show original/);
});

test('an opened Show-original toggle stays open across a repaint', () => {
  // cubic: the details disclosure is native DOM state; a bridge repaint
  // rebuilds the thread and would collapse it. The tissue must remember
  // which originals were open, like it remembers the open overflow menu.
  const tissue = createThreadTissue({mailbox:{publish(){}}});
  const ticket={capabilities:{summarizeThread:false,escalateTicket:false},ticket:{
    id:'gorgias:1',projectionSource:true,customerName:'Customer',subject:'Hello',status:'open',statusEvents:[],
    messages:[{id:'m1',from:'customer',body:'Clean body.',originalText:'Clean body.\nSent from my Galaxy',at:'2026-01-01'}]
  }};
  const host={innerHTML:'',onclick:null,querySelector(){return null;}};
  tissue.mount(host);
  let html=tissue.render(ticket);
  assert.doesNotMatch(html,/bubble-original" open/, 'starts closed');
  host.onclick({target:{closest:(sel)=>sel==='[data-original-toggle]'
    ?{dataset:{originalId:'m1'},hasAttribute:()=>false}:null}});
  html=tissue.render(ticket);
  assert.match(html,/bubble-original" open/, 'opened by the click');
  // A repaint with unchanged input keeps it open.
  html=tissue.render(ticket);
  assert.match(html,/bubble-original" open/, 'still open after a repaint');
  // cubic: clicking an already-open disclosure closes it (hasAttribute open
  // ⇒ the delete branch), and the next repaint stays closed — an inverted
  // ternary in the toggle handler must fail this.
  host.onclick({target:{closest:(sel)=>sel==='[data-original-toggle]'
    ?{dataset:{originalId:'m1'},hasAttribute:()=>true}:null}});
  html=tissue.render(ticket);
  assert.doesNotMatch(html,/bubble-original" open/, 'closed by the second click');
  html=tissue.render(ticket);
  assert.doesNotMatch(html,/bubble-original" open/, 'still closed after a repaint');
});
