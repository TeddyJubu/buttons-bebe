"""
run_queries_v3.py — 30 NEW queries through Teddy, WITH connected Shopify context.

Shopify is exercised through Teddy's real skill (skills.lookup_order -> shared
/root/shopify module), but the module's network calls are monkeypatched to a
fixed mock dataset (qa_v3/fixtures_v3.MOCK_ORDERS), since real Shopify still
403s (API scopes not granted yet). Nothing hits real Gorgias/Telegram.

Usage:  cd /root/teddy && python3 tests/run_queries_v3.py
"""
import sys, os, json, time

import tests._load_env  # noqa: F401 — loads /root/.env

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, '/root/qa_v3'); sys.path.insert(0, '/root')

from openai import OpenAI
import fixtures_v3 as F
F.patch_shopify()                       # mock Shopify into the shared module

from skills.classify    import classify
from skills.kb_router   import search as kb_search
from skills.prioritize  import prioritize, enforce_monotonic
from skills.scrub_pii   import scrub as scrub_pii
from skills.lookup_order import lookup_order
import tests.fake_gorgias  as fake_gorgias
import tests.fake_telegram as fake_telegram

OLLAMA_API_KEY = os.environ.get('OLLAMA_API_KEY', '')
OLLAMA_MODEL   = 'deepseek-v4-flash:cloud'
KB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'kb')
llm = OpenAI(base_url='https://ollama.com/v1', api_key=OLLAMA_API_KEY)

_MAX_MSG, _MAX_CTX = 1500, 12000   # context ceiling mirrors agent.py (_MAX_CONTEXT_CHARS)


def order_block(order):
    """Render a looked-up order status dict into a verified-facts block."""
    if not order:
        return ""
    lines = [f"- Order #{order.get('order_number','?')}: payment={order.get('financial_status','?')}, "
             f"fulfillment={order.get('fulfillment_status','?')}"]
    tus = order.get('tracking_urls') or []
    if tus:
        lines.append(f"  tracking ({order.get('carrier','')}): {tus[0]}")
    for it in (order.get('items') or [])[:6]:
        lines.append(f"  item: {it.get('title','item')} x{it.get('quantity',1)}")
    return "\n".join(lines)


def draft_reply(kb_context, message, order_ctx):
    ctx = (kb_context or '(no relevant articles found)')[:_MAX_CTX]
    oc = order_ctx or "(no linked order found for this customer)"
    system = (
        "You are a helpful customer support assistant for Buttons Bebe, a children's "
        "clothing brand.\n\nDraft a reply using ONLY the Knowledge Base and the verified "
        "Order Context below. If the answer is not clearly stated, write: ESCALATE: [reason]\n\n"
        "Rules:\n- Never invent policies, prices, timelines, tracking, or promises.\n"
        "- You MAY state order-specific facts (status, tracking) ONLY if present in Order Context.\n"
        "- Be warm, concise, professional (2-4 sentences). Sign off as 'The Buttons Bebe Team'.\n\n"
        f"## Order Context (verified)\n{oc}\n\n## Knowledge Base\n{ctx}"
    )
    for attempt in range(2):
        try:
            r = llm.chat.completions.create(
                model=OLLAMA_MODEL,
                messages=[{'role': 'system', 'content': system},
                          {'role': 'user', 'content': f"Customer message:\n{message[:_MAX_MSG]}"}],
                timeout=45, max_tokens=400)
            return r.choices[0].message.content.strip()
        except Exception as e:
            print(f"    [LLM {attempt+1}/2 failed: {e}]")
            if attempt == 0: time.sleep(2)
    return 'LLM_UNAVAILABLE'


def run():
    fake_gorgias.clear(); fake_telegram.clear()
    print("\n" + "="*64 + "\n  TEDDY x deepseek-v4-flash — v3 + Shopify context\n" + "="*64)
    results = []
    for q in F.QUERIES:
        subject, message, email = q['subject'], q['message'], q['email']
        clf = classify(subject, message); intent = clf['intent']
        kb = kb_search(message, KB_DIR, llm)
        look = lookup_order(email); order = look.get('order')
        order_used = bool(order)
        oc_str = order_block(order)
        msgs = [{'body_text': message, 'from_agent': False}]
        prio = prioritize(intent, kb['confidence'], order, message, msgs)

        print(f"\n{'-'*64}\n  {q['id']}  {q['label']}")
        print(f"  Intent: {intent} ({clf['confidence']:.0%}) | KB: {kb['confidence']} | Priority: {prio['level']}")
        print(f"  Shopify ctx: order_used={order_used}")

        draft, posted = '', False
        if prio['level'] == 'IMMEDIATE':
            fake_telegram.send_notification(ticket_id=q['id'], intent=intent,
                kb_confidence=kb['confidence'], priority_level='IMMEDIATE',
                priority_reason=prio['reason'], draft_preview='', posted=False)
            print("  -> IMMEDIATE alert (no draft)")
        else:
            draft = draft_reply(kb['context'], message, oc_str)
            if draft.startswith('ESCALATE:'):
                prio = {'level': 'HIGH', 'reason': draft[9:].strip(), 'action': 'draft_internal_note'}; draft = ''
            elif 'ESCALATE:' in draft:
                floor = {'level': 'HIGH', 'reason': draft.split('ESCALATE:',1)[1].strip(), 'action': 'draft_internal_note'}
                prio = enforce_monotonic(floor, prio)
            fake_gorgias.post_message(ticket_id=q['id'], body=draft or prio['reason'], mode='internal_note')
            posted = True
            print(f"  -> internal_note ({prio['level']}) | Draft: {(draft[:160] or '(esc)').replace(chr(10),' ')}")

        results.append({'id': q['id'], 'label': q['label'], 'intent': intent,
                        'kb': kb['confidence'], 'priority': prio['level'],
                        'order_used': order_used, 'draft': draft, 'posted': posted})

    with open('/root/qa_v3/results_teddy.json', 'w') as f:
        json.dump(results, f, indent=2)

    print("\n" + "="*64 + "\n  SUMMARY — TEDDY (v3 + Shopify)\n" + "="*64)
    print(f"  {'ID':<5}{'Label':<40}{'Priority':<11}{'KB':<8}{'Ord'}")
    for r in results:
        print(f"  {r['id']:<5}{r['label'][:38]:<40}{r['priority']:<11}{r['kb']:<8}{'Y' if r['order_used'] else '-'}")
    imm=sum(r['priority']=='IMMEDIATE' for r in results); high=sum(r['priority']=='HIGH' for r in results)
    low=sum(r['priority']=='LOW' for r in results); ordu=sum(r['order_used'] for r in results)
    print(f"\n  IMMEDIATE={imm} HIGH={high} LOW={low}  |  order_context_used={ordu}/30")
    print("  -> /root/qa_v3/results_teddy.json")


if __name__ == '__main__':
    run()
