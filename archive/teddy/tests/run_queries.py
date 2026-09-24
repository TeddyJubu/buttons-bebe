"""
run_queries.py — run 30 real customer queries through Teddy using Ollama Cloud.

Calls the real LLM (deepseek-v4-flash:cloud via Ollama Cloud) for drafting.
All Gorgias posts and Telegram notifications are captured by the fake modules
in this directory — nothing is sent to real services.

Usage:
    cd /root/teddy
    python3 tests/run_queries.py
"""

import sys
import os
import json
import time

import tests._load_env  # noqa: F401 — loads /root/.env

# Add teddy root to path so we can import skills directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openai import OpenAI
from skills.classify    import classify
from skills.kb_router   import search as kb_search
from skills.prioritize  import prioritize, enforce_monotonic
from skills.scrub_pii   import scrub as scrub_pii

import tests.fake_gorgias  as fake_gorgias
import tests.fake_telegram as fake_telegram

# ── Ollama Cloud config ───────────────────────────────────────────────────────
OLLAMA_API_KEY = os.environ.get('OLLAMA_API_KEY', '')
OLLAMA_MODEL   = 'deepseek-v4-flash:cloud'
KB_DIR         = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'kb')

llm = OpenAI(
    base_url='https://ollama.com/v1',
    api_key=OLLAMA_API_KEY,
)

# ── 30 test queries ───────────────────────────────────────────────────────────
QUERIES = [
    # ── IMMEDIATE (8) — time-sensitive / irreversible, alert owner immediately ──
    {
        'id': 'Q01', 'label': 'Address change before shipment',
        'subject': 'Change my shipping address',
        'message': 'Hi, I just placed order #4821 and realised I typed the wrong address. '
                   'Please change it to 42 Oak Street, Brooklyn, NY 11201 before it ships!',
        'email': 'sarah@example.com',
    },
    {
        'id': 'Q02', 'label': 'Cancel order urgently',
        'subject': 'Cancel my order please',
        'message': "I need to cancel my order immediately — I ordered the wrong thing. "
                   "Please don't ship it, I need to cancel before it goes out.",
        'email': 'mike@example.com',
    },
    {
        'id': 'Q03', 'label': 'Wrong size ordered (pre-shipment)',
        'subject': 'Ordered wrong size',
        'message': 'I accidentally ordered size 2T but I need 4T. Can you change the size '
                   'before my order ships? Order placed about 20 minutes ago.',
        'email': 'jessica@example.com',
    },
    {
        'id': 'Q04', 'label': 'Switch pickup to shipping',
        'subject': 'Switch to shipping instead of pickup',
        'message': "I selected in-store pickup by mistake. I live in Miami and can't come pick it up. "
                   "Can you switch it to shipping? I'll pay the shipping fee.",
        'email': 'carlos@example.com',
    },
    {
        'id': 'Q05', 'label': 'Wrong zip code entered',
        'subject': 'Wrong zip code on my order',
        'message': 'I put zip code 10001 but my correct zip is 10002. Please fix this '
                   'before the package ships — I am worried it will go to the wrong place!',
        'email': 'anna@example.com',
    },
    {
        'id': 'Q06', 'label': 'Wrong colour ordered (pre-shipment)',
        'subject': 'I ordered the wrong colour',
        'message': 'I just placed order #6102 five minutes ago and chose the pink onesie by '
                   'mistake — I need the yellow one. Can you swap it before it ships?',
        'email': 'tina@example.com',
    },
    {
        'id': 'Q07', 'label': 'Double charge — payment issue',
        'subject': 'Charged twice for my order',
        'message': 'I was charged twice for my order this morning — I can see two identical '
                   'transactions of $68.50 on my bank statement. Please reverse one of them immediately.',
        'email': 'james@example.com',
    },
    {
        'id': 'Q08', 'label': 'Order out of stock after payment',
        'subject': 'Got confirmation but item is out of stock?',
        'message': 'I received my order confirmation but then got an email saying the item is '
                   'out of stock. I already paid. What happens now? Do I get a refund or a '
                   'substitute? I need this for a gift by Friday.',
        'email': 'urgentgift@example.com',
    },

    # ── HIGH (12) — needs human review, draft an internal note ────────────────
    {
        'id': 'Q09', 'label': 'Damaged item received',
        'subject': 'Package arrived damaged',
        'message': 'My order arrived today and the onesie is completely damaged — there is a '
                   'large tear in the fabric. This is unacceptable for a baby item. '
                   'What are you going to do about this?',
        'email': 'lisa@example.com',
    },
    {
        'id': 'Q10', 'label': 'Wrong item in box',
        'subject': 'Received wrong item',
        'message': 'I ordered the floral dress in size 3T but received a plain blue romper in 18M. '
                   'This is the wrong item entirely. I need the correct one sent ASAP.',
        'email': 'patricia@example.com',
    },
    {
        'id': 'Q11', 'label': 'Refund demand — poor quality',
        'subject': 'Requesting a refund',
        'message': 'The quality of the clothing I received is very poor. The stitching came '
                   'apart after one wash. I want a full refund. Very disappointed.',
        'email': 'david@example.com',
    },
    {
        'id': 'Q12', 'label': 'Angry customer — social media threat',
        'subject': 'Worst experience ever',
        'message': 'This is the worst online shopping experience I have ever had. My order '
                   'is 3 weeks late and nobody responds. I am posting about this on Instagram '
                   'and TikTok. Absolutely disgusted.',
        'email': 'angry@example.com',
    },
    {
        'id': 'Q13', 'label': 'Lost package — marked delivered',
        'subject': 'Package says delivered but not here',
        'message': 'According to the tracking my package was delivered yesterday but I never '
                   'received it. I checked with my neighbours and the building front desk — '
                   'nothing. It says delivered but it is not here.',
        'email': 'robert@example.com',
    },
    {
        'id': 'Q14', 'label': 'Safety concern — button/choking hazard',
        'subject': 'Button fell off — safety issue',
        'message': 'A decorative button came off the jacket I bought for my 8-month-old after '
                   'just two washes. This is a serious choking hazard. I am very concerned '
                   'about the safety of your products.',
        'email': 'safetymom@example.com',
    },
    {
        'id': 'Q15', 'label': 'Allergic reaction to fabric',
        'subject': 'My baby had a skin reaction',
        'message': 'My daughter developed a rash after wearing the romper I bought from you. '
                   'The doctor thinks it might be an allergic reaction to the fabric or dye. '
                   'I want to know what materials are used and I want a refund.',
        'email': 'worriedmom@example.com',
    },
    {
        'id': 'Q16', 'label': 'Package never arrived — 4 weeks',
        'subject': 'Order never arrived',
        'message': 'I placed my order 4 weeks ago and it has never arrived. Tracking has not '
                   'updated in 3 weeks. The carrier says to contact the sender. This is an '
                   'expensive order and I want it reshipped or refunded.',
        'email': 'neverarrived@example.com',
    },
    {
        'id': 'Q17', 'label': 'Partial order — item missing from box',
        'subject': 'Missing item from my order',
        'message': 'I ordered 3 items and only received 2. The missing item is the pink '
                   'bodysuit 3-pack in size 6M. The packing slip shows all 3 items but '
                   'only 2 were in the box.',
        'email': 'partial@example.com',
    },
    {
        'id': 'Q18', 'label': 'Item looks different from website',
        'subject': 'Product not as advertised',
        'message': 'The dress I received looks completely different from the photos on your '
                   'website. The colour is much darker, the fabric is thinner, and the '
                   'embroidery detail is missing. I feel misled. I want to return it.',
        'email': 'misled@example.com',
    },
    {
        'id': 'Q19', 'label': 'Exchange request — wrong size gifted',
        'subject': 'Need to exchange a gift',
        'message': 'My mother bought a onesie as a gift for my baby but it is the wrong size — '
                   'she got 3M and my baby is already in 9M. Can we exchange it for the '
                   'correct size? We do not have the receipt.',
        'email': 'giftexchange@example.com',
    },
    {
        'id': 'Q20', 'label': 'Fraud / scam accusation',
        'subject': 'This feels like a scam',
        'message': 'I ordered 6 weeks ago, the tracking says "in transit" since day 1, and '
                   'nobody responds to my emails. This is fraud. I am filing a chargeback '
                   'with my credit card and reporting you to the BBB.',
        'email': 'chargeback@example.com',
    },

    # ── LOW (10) — can be auto-drafted with KB context ─────────────────────────
    {
        'id': 'Q21', 'label': 'Order status inquiry',
        'subject': 'Where is my order?',
        'message': 'Hi! I placed my order 5 days ago and have not received a shipping '
                   'confirmation yet. When will it ship? Order number #5503.',
        'email': 'emily@example.com',
    },
    {
        'id': 'Q22', 'label': 'Shipping time question',
        'subject': 'How long does shipping take?',
        'message': 'Hi! I am ordering a gift for a baby shower happening in 10 days. '
                   'Do you ship to New Jersey and how long does standard shipping usually take?',
        'email': 'gift@example.com',
    },
    {
        'id': 'Q23', 'label': 'Sizing question for toddler',
        'subject': 'What size for a 3 year old?',
        'message': 'Hi, I want to buy clothes for my daughter who just turned 3. '
                   'She is average height and weight for her age. What size do you recommend — 2T or 3T?',
        'email': 'mom@example.com',
    },
    {
        'id': 'Q24', 'label': 'Return policy question',
        'subject': 'Return policy',
        'message': 'Hello! I received my order and the item is not quite what I expected — '
                   'the colour looks different in person. What is your return window and '
                   'how do I start a return?',
        'email': 'buyer@example.com',
    },
    {
        'id': 'Q25', 'label': 'First-time customer discount',
        'subject': 'Discount for first-time customers?',
        'message': 'Hi there! I am a first time customer and was wondering if you have '
                   'any discount codes or promotions for new customers. Excited to shop!',
        'email': 'new@example.com',
    },
    {
        'id': 'Q26', 'label': 'Gift wrapping available?',
        'subject': 'Do you offer gift wrapping?',
        'message': 'Hello! I am buying this as a baby shower gift. Do you offer gift wrapping '
                   'or a gift message option? I would love to send it directly to the recipient.',
        'email': 'giftwrap@example.com',
    },
    {
        'id': 'Q27', 'label': 'Care instructions — washing',
        'subject': 'How do I wash the clothes?',
        'message': 'I just received the organic cotton onesie set and want to make sure I wash '
                   'it correctly. Can I put it in the dryer or does it need to be hang dried? '
                   'Any special care instructions I should know about?',
        'email': 'careful@example.com',
    },
    {
        'id': 'Q28', 'label': 'International shipping question',
        'subject': 'Do you ship to Canada?',
        'message': 'Hi! I am based in Toronto, Canada. Do you ship internationally and if so '
                   'what are the shipping costs and estimated delivery times to Canada?',
        'email': 'canada@example.com',
    },
    {
        'id': 'Q29', 'label': 'Promo code not working',
        'subject': 'My discount code is not working',
        'message': 'I have a promo code BEBE10 that I got from your newsletter but it keeps '
                   'saying "invalid code" at checkout. I am trying to order the floral dress '
                   'set. Can you help?',
        'email': 'promo@example.com',
    },
    {
        'id': 'Q30', 'label': 'Restock question — out of stock item',
        'subject': 'When will this be back in stock?',
        'message': 'Hi! I have been trying to buy the rainbow stripe onesie in size 12M for '
                   'weeks but it is always out of stock. Do you know when it will be restocked? '
                   'Can I sign up for a notification?',
        'email': 'restock@example.com',
    },
]

# ── Draft generator (calls real Ollama Cloud) ─────────────────────────────────
_MAX_MSG   = 1500
_MAX_CTX   = 3000

def draft_reply(kb_context: str, message: str) -> str:
    context_snippet = (kb_context or '(no relevant articles found)')[:_MAX_CTX]
    system_prompt = (
        "You are a helpful customer support assistant for Buttons Bebe, "
        "a children's clothing brand.\n\n"
        "Draft a reply to the customer using ONLY the information provided "
        "in the Knowledge Base below.\n"
        "If the answer is not clearly stated, write: ESCALATE: [brief reason]\n\n"
        "Rules:\n"
        "- Never invent policies, prices, timelines, or promises.\n"
        "- Be warm, concise, and professional (2–4 sentences).\n"
        "- Sign off as 'The Buttons Bebe Team'.\n\n"
        f"## Knowledge Base\n{context_snippet}"
    )
    for attempt in range(2):
        try:
            resp = llm.chat.completions.create(
                model=OLLAMA_MODEL,
                messages=[
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user',   'content': f"Customer message:\n{message[:_MAX_MSG]}"},
                ],
                timeout=45,
                max_tokens=400,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            print(f"    [LLM attempt {attempt+1}/2 failed: {e}]")
            if attempt == 0:
                time.sleep(2)
    return 'LLM_UNAVAILABLE'


# ── Main test runner ──────────────────────────────────────────────────────────
def run():
    fake_gorgias.clear()
    fake_telegram.clear()

    print(f"\n{'╔' + '═'*60 + '╗'}")
    print(f"║  Teddy × Ollama Cloud (deepseek-v4-flash) — 30 query test  ║")
    print(f"╚{'═'*60}╝\n")

    results = []

    for q in QUERIES:
        qid     = q['id']
        label   = q['label']
        subject = q['subject']
        message = q['message']
        email   = q['email']

        print(f"{'─'*60}")
        print(f"  {qid}  {label}")
        print(f"  Subject : {subject}")
        print(f"  Message : {message[:80]}{'...' if len(message)>80 else ''}")

        # 1. Classify
        clf    = classify(subject, message)
        intent = clf['intent']

        # 2. KB search (keyword → semantic fallback)
        kb = kb_search(message, KB_DIR, llm)

        # 3. Prioritize
        msgs = [{'body_text': message, 'from_agent': False}]
        prio = prioritize(intent, kb['confidence'], None, message, msgs)

        print(f"  Intent  : {intent} ({clf['confidence']:.0%})  |  "
              f"KB: {kb['confidence']}  |  Priority: {prio['level']}")

        # 4. Draft (skip for IMMEDIATE — just alert owner)
        draft = ''
        posted = False

        if prio['level'] == 'IMMEDIATE':
            # No draft generated — just notify owner to act NOW
            fake_telegram.send_notification(
                ticket_id=qid,
                intent=intent,
                kb_confidence=kb['confidence'],
                priority_level='IMMEDIATE',
                priority_reason=prio['reason'],
                draft_preview='',
                posted=False,
            )
            print(f"  → IMMEDIATE alert sent to owner (no draft)")

        else:
            print(f"  → Calling deepseek-v4-flash:cloud for draft...", end='', flush=True)
            draft = draft_reply(kb['context'], message)
            print(" done")

            # Handle ESCALATE markers — LLM sometimes places them mid-draft or at the end
            if draft.startswith('ESCALATE:'):
                prio  = {'level': 'HIGH', 'reason': draft[9:].strip(), 'action': 'draft_internal_note'}
                draft = ''
            elif 'ESCALATE:' in draft:
                escalate_reason  = draft.split('ESCALATE:', 1)[1].strip()
                escalation_floor = {'level': 'HIGH', 'reason': escalate_reason, 'action': 'draft_internal_note'}
                prio = enforce_monotonic(escalation_floor, prio)

            # 5. Post to fake Gorgias — Phase 1: always internal note, never public reply
            mode = 'internal_note'
            fake_gorgias.post_message(ticket_id=qid, body=draft or prio['reason'], mode=mode)
            posted = True  # internal note was created (not a public reply)
            print(f"  → Gorgias: internal_note ({prio['level']})")

            # 6. Notify fake Telegram
            fake_telegram.send_notification(
                ticket_id=qid,
                intent=intent,
                kb_confidence=kb['confidence'],
                priority_level=prio['level'],
                priority_reason=scrub_pii(prio['reason']),
                draft_preview=scrub_pii(draft[:200]) if draft else '',
                posted=posted,
            )
            print(f"  → Telegram: {prio['level']} notification sent")

        results.append({
            'id':       qid,
            'label':    label,
            'intent':   intent,
            'kb':       kb['confidence'],
            'priority': prio['level'],
            'draft':    draft,
            'posted':   posted,
        })

    # ── Print full outputs ────────────────────────────────────────────────────
    fake_gorgias.print_inbox()
    fake_telegram.print_inbox()

    # ── Summary table ─────────────────────────────────────────────────────────
    print(f"\n{'═'*60}")
    print(f"  SUMMARY")
    print(f"{'═'*60}")
    print(f"  {'ID':<5} {'Label':<38} {'Priority':<10} {'KB':<8} {'Mode'}")
    print(f"  {'─'*5} {'─'*38} {'─'*10} {'─'*8} {'─'*12}")
    for r in results:
        mode = ('ALERTED'  if r['priority'] == 'IMMEDIATE' else 'INT.NOTE')
        print(f"  {r['id']:<5} {r['label']:<38} {r['priority']:<10} {r['kb']:<8} {mode}")

    imm   = sum(1 for r in results if r['priority'] == 'IMMEDIATE')
    high  = sum(1 for r in results if r['priority'] == 'HIGH')
    low   = sum(1 for r in results if r['priority'] == 'LOW')
    noted = sum(1 for r in results if r['posted'])
    print(f"\n  IMMEDIATE={imm} (alert only)  HIGH={high} (draft+review)  LOW={low} (draft+review)")
    print(f"  Internal notes created: {noted}/{len(results)}  |  Public replies: 0 (Phase 1)")
    print()


if __name__ == '__main__':
    run()
