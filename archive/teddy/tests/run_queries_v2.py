"""
run_queries_v2.py — 30 NEW challenging queries through Teddy using Ollama Cloud.

Scenarios are designed to expose edge cases:
  - Calm tone hiding urgent situation
  - Urgent phrasing on routine issues
  - Multi-intent ambiguity
  - Safety/medical escalations
  - Emotional edge cases
  - Post-shipment masquerading as pre-ship
  - Traps the first 30 didn't cover

Usage:
    cd /root/teddy
    python3 tests/run_queries_v2.py
"""

import sys
import os
import json
import time

import tests._load_env  # noqa: F401 — loads /root/.env

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openai import OpenAI
from skills.classify    import classify
from skills.kb_router   import search as kb_search
from skills.prioritize  import prioritize, enforce_monotonic
from skills.scrub_pii   import scrub as scrub_pii

import tests.fake_gorgias  as fake_gorgias
import tests.fake_telegram as fake_telegram

OLLAMA_API_KEY = os.environ.get('OLLAMA_API_KEY', '')
OLLAMA_MODEL   = 'deepseek-v4-flash:cloud'
KB_DIR         = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'kb')

llm = OpenAI(
    base_url='https://ollama.com/v1',
    api_key=OLLAMA_API_KEY,
)

# ── 30 NEW challenging queries ────────────────────────────────────────────────
QUERIES = [

    # ── IMMEDIATE (Q01–Q08): pre-ship urgency, irreversible if missed ─────────

    {
        'id': 'Q01', 'label': 'Duplicate order — double checkout',
        'subject': 'I think I ordered twice',
        'message': 'I think I accidentally double-clicked the checkout button — I just got two '
                   'order confirmations #7890 and #7891, both for the same item placed literally '
                   '2 minutes apart. Can you cancel one of them right away?',
        'email': 'doubleclk@example.com',
    },
    {
        'id': 'Q02', 'label': 'Overnight shipping needed — baby shower tomorrow',
        'subject': 'Need overnight shipping urgently',
        'message': 'Hi! I completely forgot to order in advance and the baby shower is tomorrow '
                   'at 2pm. Is there ANY way to upgrade to overnight or same-day shipping? '
                   'I just placed order #3341. Happy to pay extra.',
        'email': 'lastminute@example.com',
    },
    {
        'id': 'Q03', 'label': 'Wrong apartment number — calm tone (urgency trap)',
        'subject': 'Small address correction needed',
        'message': 'Hi there! Hope you\'re having a great day. Quick thing — I noticed I put '
                   'apartment 3B instead of 3A on my order. No big deal probably but just in '
                   'case could you fix it before it ships? Thanks so much!',
        'email': 'calm@example.com',
    },
    {
        'id': 'Q04', 'label': 'Add item to existing order before dispatch',
        'subject': 'Can I add to my order?',
        'message': 'I just placed order #2209 for the floral onesie. Is there any way to add '
                   'the matching hat to the same order before it goes out? I\'d rather not '
                   'pay shipping twice.',
        'email': 'additem@example.com',
    },
    {
        'id': 'Q05', 'label': 'Gift surprise — wrong name on shipping label',
        'subject': 'Wrong name on shipping label — gift surprise',
        'message': 'I ordered a gift for my sister and accidentally put my own name on the '
                   'shipping label instead of something generic. She is the recipient and the '
                   'gift is a surprise — if she sees my name on the package she\'ll know it\'s '
                   'from me. Can you change the sender name before shipping?',
        'email': 'surprise@example.com',
    },
    {
        'id': 'Q06', 'label': 'Wrong country on order — needs immediate intercept',
        'subject': 'Wrong country on my order — urgent',
        'message': 'Oh no — I just realised I entered a US zip code but my address is in '
                   'Australia. My order was just placed (#5544) and needs to go to Sydney, '
                   'Australia not the US. Please stop the shipment before it goes out!',
        'email': 'australia@example.com',
    },
    {
        'id': 'Q07', 'label': 'Cancel to buy cheaper elsewhere — price dispute',
        'subject': 'Cancel my order — found it cheaper',
        'message': 'I just placed order #8823 but found the same item for $12 less on another '
                   'site. I want to cancel and get a refund. Just placed it about 10 minutes ago.',
        'email': 'cheaperfound@example.com',
    },
    {
        'id': 'Q08', 'label': 'Possibly fraudulent order — stolen card alert',
        'subject': 'Possible fraudulent order on my account',
        'message': 'I just got a fraud alert on my card. I was about to place an order with '
                   'you but now I\'m not sure if someone else already placed it using my card. '
                   'Can you check for any recent order and cancel it immediately? My card ends '
                   'in 4532.',
        'email': 'fraud@example.com',
    },

    # ── HIGH (Q09–Q20): serious post-delivery, financial, safety, or legal ────

    {
        'id': 'Q09', 'label': 'Package stolen from porch — has video proof',
        'subject': 'My package was stolen',
        'message': 'My package was delivered to my front porch yesterday (I have it on my Ring '
                   'camera) but when I came home it was gone. Someone stole it. I have the '
                   'video footage. What do I need to do to get a replacement?',
        'email': 'stolen@example.com',
    },
    {
        'id': 'Q10', 'label': 'Item arrived with no tags — appears used',
        'subject': 'Was I sent a used item?',
        'message': 'The dress I received had no hang tags and the packaging was already opened. '
                   'The bow on the back looks stretched out and there is a faint stain near the '
                   'hem. I think I was sent a customer return, not a new item. I paid full price '
                   'for a new product.',
        'email': 'useditem@example.com',
    },
    {
        'id': 'Q11', 'label': 'Paid for 2-day shipping — took 7 days (overcharged)',
        'subject': 'Shipping upgrade fee not honoured',
        'message': 'I paid $18.99 extra at checkout for 2-day shipping but my package took '
                   '7 days to arrive. I want a refund of the shipping upgrade fee since I '
                   'clearly did not receive the service I paid for.',
        'email': 'lateship@example.com',
    },
    {
        'id': 'Q12', 'label': 'Medical escalation — rash now referred to allergist',
        'subject': 'Baby rash worsened — now seeing allergist',
        'message': 'This is a follow-up to a previous complaint. My daughter\'s rash from your '
                   'romper has not cleared up and her doctor has now referred her to an '
                   'allergist. I need the full list of dyes and chemicals used in the fabric. '
                   'This is now a medical issue being tracked by her doctor.',
        'email': 'allergist@example.com',
    },
    {
        'id': 'Q13', 'label': 'Wrong item received — correct item now sold out',
        'subject': 'Wrong item and now correct one is out of stock',
        'message': 'I received the wrong item — got a blue sleeper instead of the yellow duck '
                   'onesie I ordered. When I checked your website, the yellow duck onesie is '
                   'now showing as out of stock. What are my options? I specifically do not '
                   'want a substitution.',
        'email': 'wrongitem2@example.com',
    },
    {
        'id': 'Q14', 'label': 'Refund promised 14 days ago — never received',
        'subject': 'My refund never arrived',
        'message': 'I returned an item 14 days ago and was told the refund would appear in '
                   '7-10 business days. I have checked my bank account and there is nothing. '
                   'I sent two follow-up emails and got no response. I need this resolved today.',
        'email': 'refundfail@example.com',
    },
    {
        'id': 'Q15', 'label': 'CPSC threat — polite tone (escalation trap)',
        'subject': 'Safety concern with snap on onesie',
        'message': 'Hi, I\'m sorry to bother you but I wanted to flag something. One of the '
                   'metal snaps on the onesie came loose after just a few washes, and my '
                   '6-month-old got it in his mouth. He is okay but we are quite concerned. '
                   'I believe product safety issues like this need to be reported to the CPSC '
                   '(Consumer Product Safety Commission). I hope you will look into this.',
        'email': 'polite_cpsc@example.com',
    },
    {
        'id': 'Q16', 'label': 'Chargeback already filed — wants to settle directly',
        'subject': 'Chargeback filed — want to resolve with you first',
        'message': 'I already opened a dispute with my credit card company about order #6611 '
                   'because I never received my package. However, I\'d rather settle this with '
                   'you directly if possible. Can you contact me before the bank closes the '
                   'dispute? I have until Thursday.',
        'email': 'chargeback2@example.com',
    },
    {
        'id': 'Q17', 'label': 'Carrier delivered to wrong house — neighbour unresponsive',
        'subject': 'Package left at wrong address — neighbour not answering',
        'message': 'The tracking photo shows my package was left at the house next door — you '
                   'can see their blue door in the delivery photo. I have knocked three times '
                   'today and twice yesterday and no one answers. The package is just sitting '
                   'on their porch exposed to rain. What can you do for me?',
        'email': 'wronghouse@example.com',
    },
    {
        'id': 'Q18', 'label': 'Emotional cancellation — pregnancy loss',
        'subject': 'Need to cancel — family bereavement',
        'message': 'I placed an order for a baby gift but sadly need to cancel it. Our close '
                   'friends suffered a pregnancy loss and we no longer need the items. '
                   'I know this is an unusual request and I appreciate your understanding. '
                   'Order #4477.',
        'email': 'emotional@example.com',
    },
    {
        'id': 'Q19', 'label': 'Change of mind return — item is fine (tests return policy)',
        'subject': 'Want to return — changed my mind',
        'message': 'Hi, I want to return the onesie set I received 5 days ago. Nothing is '
                   'wrong with it at all — I just changed my mind and the colours don\'t '
                   'match the nursery. Is that possible?',
        'email': 'changedmind@example.com',
    },
    {
        'id': 'Q20', 'label': 'Baby injured by snap — soft tone hiding severity',
        'subject': 'Small issue with romper snap',
        'message': 'Hi, I wanted to reach out about something that happened yesterday. My '
                   '9-month-old had a small cut on his finger — we think it might have been '
                   'from a rough edge on one of the snaps on the romper we bought from you. '
                   'It was not serious and healed quickly but I thought I should mention it '
                   'in case it is a known issue. Let me know if you need anything from me.',
        'email': 'softinjury@example.com',
    },

    # ── LOW (Q21–Q30): routine FAQ — some with urgency traps ──────────────────

    {
        'id': 'Q21', 'label': 'Stacking two promo codes',
        'subject': 'Can I use two discount codes?',
        'message': 'Hi! I have two discount codes — one from your newsletter (BEBE10) and one '
                   'from a friend\'s referral link (FRIEND5). Can I use both at the same time '
                   'on my order?',
        'email': 'twocodes@example.com',
    },
    {
        'id': 'Q22', 'label': 'Bulk purchase question — daycare order',
        'subject': 'Bulk discount for daycare?',
        'message': 'Hi! I run a small daycare and am looking to buy about 12-15 onesies for '
                   'our nursery. Do you offer any bulk pricing or wholesale discounts for '
                   'larger orders?',
        'email': 'daycare@example.com',
    },
    {
        'id': 'Q23', 'label': 'Pre-purchase — will item go on sale soon?',
        'subject': 'Will this go on sale soon?',
        'message': 'I really love the rainbow set but it is a bit over my budget. Do you have '
                   'any sales coming up soon? I don\'t want to buy now and then see it 20% off '
                   'next week.',
        'email': 'salewatch@example.com',
    },
    {
        'id': 'Q24', 'label': 'Split payment across two cards',
        'subject': 'Can I pay with two cards?',
        'message': 'Hi! Is it possible to split my payment across two different credit cards? '
                   'My one card doesn\'t have enough credit limit to cover the full amount.',
        'email': 'splitpay@example.com',
    },
    {
        'id': 'Q25', 'label': 'GOTS / organic certification question',
        'subject': 'Are your products certified organic?',
        'message': 'I specifically want certified organic clothing for my newborn. Can you '
                   'confirm whether your organic cotton onesies are GOTS certified or have '
                   'any other organic certification? I want to verify before I order.',
        'email': 'organic@example.com',
    },
    {
        'id': 'Q26', 'label': 'How to leave a product review',
        'subject': 'How do I leave a review?',
        'message': 'I really love the onesie I bought last month and want to leave a review '
                   'to help other parents. How do I do that? I couldn\'t find a link in my '
                   'order confirmation email.',
        'email': 'review@example.com',
    },
    {
        'id': 'Q27', 'label': 'Physical store location question',
        'subject': 'Do you have a physical store?',
        'message': 'Hi! I\'d love to see the clothes in person before buying. Do you have any '
                   'physical store locations I could visit, or do you sell at any local '
                   'markets or pop-ups in the New York / New Jersey area?',
        'email': 'physicalstore@example.com',
    },
    {
        'id': 'Q28', 'label': 'Loyalty / rewards programme question',
        'subject': 'Do you have a loyalty programme?',
        'message': 'Do you have a loyalty programme or any kind of rewards for repeat '
                   'customers? I have ordered from you four or five times now and love your '
                   'products — just wondering if there are any perks for regulars.',
        'email': 'loyal@example.com',
    },
    {
        'id': 'Q29', 'label': 'Sale item return policy — is it final sale?',
        'subject': 'Can I return a sale item?',
        'message': 'I bought something from your recent sale event. I know some stores have '
                   'final sale policies. Are your sale items returnable or is it final sale? '
                   'I want to know the policy before I commit to buying.',
        'email': 'salepolicy@example.com',
    },
    {
        'id': 'Q30', 'label': 'Colour swap after shipment (pre-ship trap)',
        'subject': 'Can I swap the colour? Tracking says in transit',
        'message': 'Hi! I ordered the romper in pink yesterday but I\'ve just been told the '
                   'nursery theme is changing to a gender-neutral blue. Is there any way to '
                   'swap it for the blue version? I can see the tracking says the package is '
                   '"in transit" — is it too late?',
        'email': 'colourlate@example.com',
    },
]

# ── Draft generator ───────────────────────────────────────────────────────────
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

    print(f"\n{'╔' + '═'*62 + '╗'}")
    print(f"║  Teddy × Ollama Cloud (deepseek-v4-flash) — 30 challenging queries  ║")
    print(f"╚{'═'*62}╝\n")

    results = []

    for q in QUERIES:
        qid     = q['id']
        label   = q['label']
        subject = q['subject']
        message = q['message']
        email   = q['email']

        print(f"{'─'*62}")
        print(f"  {qid}  {label}")
        print(f"  Subject : {subject}")
        print(f"  Message : {message[:80]}{'...' if len(message)>80 else ''}")

        clf    = classify(subject, message)
        intent = clf['intent']

        kb = kb_search(message, KB_DIR, llm)

        msgs = [{'body_text': message, 'from_agent': False}]
        prio = prioritize(intent, kb['confidence'], None, message, msgs)

        print(f"  Intent  : {intent} ({clf['confidence']:.0%})  |  "
              f"KB: {kb['confidence']}  |  Priority: {prio['level']}")

        draft = ''
        posted = False

        if prio['level'] == 'IMMEDIATE':
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

            if draft.startswith('ESCALATE:'):
                prio  = {'level': 'HIGH', 'reason': draft[9:].strip(), 'action': 'draft_internal_note'}
                draft = ''
            elif 'ESCALATE:' in draft:
                escalate_reason  = draft.split('ESCALATE:', 1)[1].strip()
                escalation_floor = {'level': 'HIGH', 'reason': escalate_reason, 'action': 'draft_internal_note'}
                prio = enforce_monotonic(escalation_floor, prio)

            mode = 'internal_note'
            fake_gorgias.post_message(ticket_id=qid, body=draft or prio['reason'], mode=mode)
            posted = True
            print(f"  → Gorgias: internal_note ({prio['level']})")

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

    fake_gorgias.print_inbox()
    fake_telegram.print_inbox()

    print(f"\n{'═'*62}")
    print(f"  SUMMARY")
    print(f"{'═'*62}")
    print(f"  {'ID':<5} {'Label':<42} {'Priority':<10} {'KB':<8} {'Mode'}")
    print(f"  {'─'*5} {'─'*42} {'─'*10} {'─'*8} {'─'*12}")
    for r in results:
        mode = ('ALERTED'  if r['priority'] == 'IMMEDIATE' else 'INT.NOTE')
        print(f"  {r['id']:<5} {r['label']:<42} {r['priority']:<10} {r['kb']:<8} {mode}")

    imm   = sum(1 for r in results if r['priority'] == 'IMMEDIATE')
    high  = sum(1 for r in results if r['priority'] == 'HIGH')
    low   = sum(1 for r in results if r['priority'] == 'LOW')
    noted = sum(1 for r in results if r['posted'])
    print(f"\n  IMMEDIATE={imm} (alert only)  HIGH={high} (draft+review)  LOW={low} (draft+review)")
    print(f"  Internal notes created: {noted}/{len(results)}  |  Public replies: 0 (Phase 1)")
    print()


if __name__ == '__main__':
    run()
