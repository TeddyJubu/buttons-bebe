import test from 'node:test';
import assert from 'node:assert/strict';
import { createThreadTissue } from '../js/tissues/thread.js';

test('observed history and readonly drafts escape customer text and label unknown status', () => {
  const tissue = createThreadTissue({mailbox:{publish(){}}});
  const html=tissue.render({capabilities:{summarizeThread:false,escalateTicket:false},ticket:{
    id:'gorgias:1',projectionSource:true,projection:{stale:true},historyIncomplete:true,truncated:true,
    customerName:'Customer',subject:'<script>subject</script>',status:'unknown',statusEvents:[],
    messages:[{id:'m1',from:'customer',body:'<img onerror=alert(1)>',at:'2026-01-01'}],
    readonlyDraft:'<script>draft</script>'
  }});
  assert.doesNotMatch(html,/<script>|<img onerror/);
  assert.match(html,/&lt;script&gt;draft/);
  assert.match(html,/not sent · read only/);
  assert.match(html,/Status unknown/);
  assert.match(html,/Snapshot is stale/);
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
