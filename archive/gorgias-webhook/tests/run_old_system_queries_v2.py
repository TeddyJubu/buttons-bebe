#!/usr/bin/env python3
"""
run_old_system_queries_v2.py — 30 NEW challenging queries through the old gorgias-webhook.

Same 30 scenarios as run_queries_v2.py (Teddy) for apples-to-apples comparison.
Nothing is sent to real Gorgias or Telegram.

Usage:
    cd /root/gorgias-webhook
    python3 tests/run_old_system_queries_v2.py
"""

import os
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPT_DIR)
import dotenv_loader
dotenv_loader.load()

os.environ['LLM_PROVIDER']       = 'openai-compatible'
os.environ['LLM_BASE_URL']       = 'https://ollama.com/v1'
os.environ['LLM_MODEL']          = 'deepseek-v4-flash:cloud'
os.environ['LLM_API_KEY']        = os.environ.get('OLLAMA_API_KEY', '')
os.environ['KB_SERVICE_ENABLED'] = '0'

import classifier
import draft_engine

_gorgias_inbox  = []
_telegram_inbox = []

_URGENCY_EMOJI = {
    'immediate': '🚨',
    'high':      '⚠️',
    'normal':    '📋',
    'low':       '📝',
}
_CONF_EMOJI = {
    'high':   '🟢',
    'medium': '🟡',
    'low':    '🟠',
    'none':   '🔴',
}


def _post_gorgias(ticket_id, body, mode='internal_note'):
    _gorgias_inbox.append({
        'ticket_id': str(ticket_id),
        'mode':      mode,
        'body':      body,
        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    })


def _send_telegram(ticket_id, category, urgency, kb_conf, reason,
                   draft_preview='', is_escalation=False, kb_gap=False):
    emoji   = _URGENCY_EMOJI.get(urgency, '❓')
    kb_icon = _CONF_EMOJI.get(kb_conf, '❓')
    tag = ' 🔴 ESCALATED' if is_escalation else (' 🟡 KB-GAP' if kb_gap else '')
    lines = [
        f"{emoji} {urgency.upper()}{tag}",
        f"Ticket #{ticket_id} | {category}",
        f"KB: {kb_icon} {kb_conf}",
        f"Reason: {reason}",
    ]
    if draft_preview:
        lines.append(f"Draft: {draft_preview[:200].replace(chr(10), ' ')}")
    _telegram_inbox.append({
        'ticket_id':     str(ticket_id),
        'urgency':       urgency,
        'category':      category,
        'kb_conf':       kb_conf,
        'is_escalation': is_escalation,
        'kb_gap':        kb_gap,
        'text':          '\n'.join(lines),
        'timestamp':     time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    })


def _print_gorgias():
    sep = '═' * 60
    print(f"\n{sep}")
    print(f"  Old System — Fake Gorgias inbox  ({len(_gorgias_inbox)} messages)")
    print(sep)
    for msg in _gorgias_inbox:
        label = '📤 PUBLIC REPLY' if msg['mode'] == 'public_reply' else '📋 INTERNAL NOTE'
        print(f"\n  Ticket #{msg['ticket_id']}  [{label}]")
        print(f"  {'-'*56}")
        for line in (msg['body'] or '').split('\n'):
            print(f"  {line}")


def _print_telegram():
    sep = '═' * 60
    print(f"\n{sep}")
    print(f"  Old System — Fake Telegram inbox  ({len(_telegram_inbox)} notifications)")
    print(sep)
    for msg in _telegram_inbox:
        print(f"\n  [{msg['timestamp']}]")
        for line in msg['text'].split('\n'):
            print(f"  {line}")


# ── 30 NEW challenging queries (identical to Teddy v2 test) ──────────────────
QUERIES = [
    # ── IMMEDIATE (Q01–Q08) ───────────────────────────────────────────────────
    {
        'id': 'Q01', 'label': 'Duplicate order — double checkout',
        'subject': 'I think I ordered twice',
        'message': 'I think I accidentally double-clicked the checkout button — I just got two '
                   'order confirmations #7890 and #7891, both for the same item placed literally '
                   '2 minutes apart. Can you cancel one of them right away?',
    },
    {
        'id': 'Q02', 'label': 'Overnight shipping needed — baby shower tomorrow',
        'subject': 'Need overnight shipping urgently',
        'message': 'Hi! I completely forgot to order in advance and the baby shower is tomorrow '
                   'at 2pm. Is there ANY way to upgrade to overnight or same-day shipping? '
                   'I just placed order #3341. Happy to pay extra.',
    },
    {
        'id': 'Q03', 'label': 'Wrong apartment number — calm tone (urgency trap)',
        'subject': 'Small address correction needed',
        'message': 'Hi there! Hope you\'re having a great day. Quick thing — I noticed I put '
                   'apartment 3B instead of 3A on my order. No big deal probably but just in '
                   'case could you fix it before it ships? Thanks so much!',
    },
    {
        'id': 'Q04', 'label': 'Add item to existing order before dispatch',
        'subject': 'Can I add to my order?',
        'message': 'I just placed order #2209 for the floral onesie. Is there any way to add '
                   'the matching hat to the same order before it goes out? I\'d rather not '
                   'pay shipping twice.',
    },
    {
        'id': 'Q05', 'label': 'Gift surprise — wrong name on shipping label',
        'subject': 'Wrong name on shipping label — gift surprise',
        'message': 'I ordered a gift for my sister and accidentally put my own name on the '
                   'shipping label instead of something generic. She is the recipient and the '
                   'gift is a surprise — if she sees my name on the package she\'ll know it\'s '
                   'from me. Can you change the sender name before shipping?',
    },
    {
        'id': 'Q06', 'label': 'Wrong country on order — needs immediate intercept',
        'subject': 'Wrong country on my order — urgent',
        'message': 'Oh no — I just realised I entered a US zip code but my address is in '
                   'Australia. My order was just placed (#5544) and needs to go to Sydney, '
                   'Australia not the US. Please stop the shipment before it goes out!',
    },
    {
        'id': 'Q07', 'label': 'Cancel to buy cheaper elsewhere — price dispute',
        'subject': 'Cancel my order — found it cheaper',
        'message': 'I just placed order #8823 but found the same item for $12 less on another '
                   'site. I want to cancel and get a refund. Just placed it about 10 minutes ago.',
    },
    {
        'id': 'Q08', 'label': 'Possibly fraudulent order — stolen card alert',
        'subject': 'Possible fraudulent order on my account',
        'message': 'I just got a fraud alert on my card. I was about to place an order with '
                   'you but now I\'m not sure if someone else already placed it using my card. '
                   'Can you check for any recent order and cancel it immediately? My card ends '
                   'in 4532.',
    },
    # ── HIGH (Q09–Q20) ────────────────────────────────────────────────────────
    {
        'id': 'Q09', 'label': 'Package stolen from porch — has video proof',
        'subject': 'My package was stolen',
        'message': 'My package was delivered to my front porch yesterday (I have it on my Ring '
                   'camera) but when I came home it was gone. Someone stole it. I have the '
                   'video footage. What do I need to do to get a replacement?',
    },
    {
        'id': 'Q10', 'label': 'Item arrived with no tags — appears used',
        'subject': 'Was I sent a used item?',
        'message': 'The dress I received had no hang tags and the packaging was already opened. '
                   'The bow on the back looks stretched out and there is a faint stain near the '
                   'hem. I think I was sent a customer return, not a new item. I paid full price '
                   'for a new product.',
    },
    {
        'id': 'Q11', 'label': 'Paid for 2-day shipping — took 7 days (overcharged)',
        'subject': 'Shipping upgrade fee not honoured',
        'message': 'I paid $18.99 extra at checkout for 2-day shipping but my package took '
                   '7 days to arrive. I want a refund of the shipping upgrade fee since I '
                   'clearly did not receive the service I paid for.',
    },
    {
        'id': 'Q12', 'label': 'Medical escalation — rash now referred to allergist',
        'subject': 'Baby rash worsened — now seeing allergist',
        'message': 'This is a follow-up to a previous complaint. My daughter\'s rash from your '
                   'romper has not cleared up and her doctor has now referred her to an '
                   'allergist. I need the full list of dyes and chemicals used in the fabric. '
                   'This is now a medical issue being tracked by her doctor.',
    },
    {
        'id': 'Q13', 'label': 'Wrong item received — correct item now sold out',
        'subject': 'Wrong item and now correct one is out of stock',
        'message': 'I received the wrong item — got a blue sleeper instead of the yellow duck '
                   'onesie I ordered. When I checked your website, the yellow duck onesie is '
                   'now showing as out of stock. What are my options? I specifically do not '
                   'want a substitution.',
    },
    {
        'id': 'Q14', 'label': 'Refund promised 14 days ago — never received',
        'subject': 'My refund never arrived',
        'message': 'I returned an item 14 days ago and was told the refund would appear in '
                   '7-10 business days. I have checked my bank account and there is nothing. '
                   'I sent two follow-up emails and got no response. I need this resolved today.',
    },
    {
        'id': 'Q15', 'label': 'CPSC threat — polite tone (escalation trap)',
        'subject': 'Safety concern with snap on onesie',
        'message': 'Hi, I\'m sorry to bother you but I wanted to flag something. One of the '
                   'metal snaps on the onesie came loose after just a few washes, and my '
                   '6-month-old got it in his mouth. He is okay but we are quite concerned. '
                   'I believe product safety issues like this need to be reported to the CPSC '
                   '(Consumer Product Safety Commission). I hope you will look into this.',
    },
    {
        'id': 'Q16', 'label': 'Chargeback already filed — wants to settle directly',
        'subject': 'Chargeback filed — want to resolve with you first',
        'message': 'I already opened a dispute with my credit card company about order #6611 '
                   'because I never received my package. However, I\'d rather settle this with '
                   'you directly if possible. Can you contact me before the bank closes the '
                   'dispute? I have until Thursday.',
    },
    {
        'id': 'Q17', 'label': 'Carrier delivered to wrong house — neighbour unresponsive',
        'subject': 'Package left at wrong address — neighbour not answering',
        'message': 'The tracking photo shows my package was left at the house next door — you '
                   'can see their blue door in the delivery photo. I have knocked three times '
                   'today and twice yesterday and no one answers. The package is just sitting '
                   'on their porch exposed to rain. What can you do for me?',
    },
    {
        'id': 'Q18', 'label': 'Emotional cancellation — pregnancy loss',
        'subject': 'Need to cancel — family bereavement',
        'message': 'I placed an order for a baby gift but sadly need to cancel it. Our close '
                   'friends suffered a pregnancy loss and we no longer need the items. '
                   'I know this is an unusual request and I appreciate your understanding. '
                   'Order #4477.',
    },
    {
        'id': 'Q19', 'label': 'Change of mind return — item is fine',
        'subject': 'Want to return — changed my mind',
        'message': 'Hi, I want to return the onesie set I received 5 days ago. Nothing is '
                   'wrong with it at all — I just changed my mind and the colours don\'t '
                   'match the nursery. Is that possible?',
    },
    {
        'id': 'Q20', 'label': 'Baby injured by snap — soft tone hiding severity',
        'subject': 'Small issue with romper snap',
        'message': 'Hi, I wanted to reach out about something that happened yesterday. My '
                   '9-month-old had a small cut on his finger — we think it might have been '
                   'from a rough edge on one of the snaps on the romper we bought from you. '
                   'It was not serious and healed quickly but I thought I should mention it '
                   'in case it is a known issue. Let me know if you need anything from me.',
    },
    # ── LOW (Q21–Q30) ─────────────────────────────────────────────────────────
    {
        'id': 'Q21', 'label': 'Stacking two promo codes',
        'subject': 'Can I use two discount codes?',
        'message': 'Hi! I have two discount codes — one from your newsletter (BEBE10) and one '
                   'from a friend\'s referral link (FRIEND5). Can I use both at the same time '
                   'on my order?',
    },
    {
        'id': 'Q22', 'label': 'Bulk purchase question — daycare order',
        'subject': 'Bulk discount for daycare?',
        'message': 'Hi! I run a small daycare and am looking to buy about 12-15 onesies for '
                   'our nursery. Do you offer any bulk pricing or wholesale discounts for '
                   'larger orders?',
    },
    {
        'id': 'Q23', 'label': 'Pre-purchase — will item go on sale soon?',
        'subject': 'Will this go on sale soon?',
        'message': 'I really love the rainbow set but it is a bit over my budget. Do you have '
                   'any sales coming up soon? I don\'t want to buy now and then see it 20% off '
                   'next week.',
    },
    {
        'id': 'Q24', 'label': 'Split payment across two cards',
        'subject': 'Can I pay with two cards?',
        'message': 'Hi! Is it possible to split my payment across two different credit cards? '
                   'My one card doesn\'t have enough credit limit to cover the full amount.',
    },
    {
        'id': 'Q25', 'label': 'GOTS / organic certification question',
        'subject': 'Are your products certified organic?',
        'message': 'I specifically want certified organic clothing for my newborn. Can you '
                   'confirm whether your organic cotton onesies are GOTS certified or have '
                   'any other organic certification? I want to verify before I order.',
    },
    {
        'id': 'Q26', 'label': 'How to leave a product review',
        'subject': 'How do I leave a review?',
        'message': 'I really love the onesie I bought last month and want to leave a review '
                   'to help other parents. How do I do that? I couldn\'t find a link in my '
                   'order confirmation email.',
    },
    {
        'id': 'Q27', 'label': 'Physical store location question',
        'subject': 'Do you have a physical store?',
        'message': 'Hi! I\'d love to see the clothes in person before buying. Do you have any '
                   'physical store locations I could visit, or do you sell at any local '
                   'markets or pop-ups in the New York / New Jersey area?',
    },
    {
        'id': 'Q28', 'label': 'Loyalty / rewards programme question',
        'subject': 'Do you have a loyalty programme?',
        'message': 'Do you have a loyalty programme or any kind of rewards for repeat '
                   'customers? I have ordered from you four or five times now and love your '
                   'products — just wondering if there are any perks for regulars.',
    },
    {
        'id': 'Q29', 'label': 'Sale item return policy — is it final sale?',
        'subject': 'Can I return a sale item?',
        'message': 'I bought something from your recent sale event. I know some stores have '
                   'final sale policies. Are your sale items returnable or is it final sale? '
                   'I want to know the policy before I commit to buying.',
    },
    {
        'id': 'Q30', 'label': 'Colour swap after shipment (pre-ship trap)',
        'subject': 'Can I swap the colour? Tracking says in transit',
        'message': 'Hi! I ordered the romper in pink yesterday but I\'ve just been told the '
                   'nursery theme is changing to a gender-neutral blue. Is there any way to '
                   'swap it for the blue version? I can see the tracking says the package is '
                   '"in transit" — is it too late?',
    },
]

_URGENCY_LABEL = {
    'immediate': 'IMMEDIATE',
    'high':      'HIGH',
    'normal':    'NORMAL',
    'low':       'LOW',
}


def run():
    print(f"\n{'╔' + '═'*62 + '╗'}")
    print(f"║  Old System (gorgias-webhook) × deepseek-v4-flash — 30 challenging  ║")
    print(f"╚{'═'*62}╝\n")

    results = []

    for q in QUERIES:
        qid     = q['id']
        label   = q['label']
        subject = q['subject']
        message = q['message']

        print(f"{'─'*62}")
        print(f"  {qid}  {label}")
        print(f"  Subject : {subject}")
        print(f"  Message : {message[:80]}{'...' if len(message) > 80 else ''}")

        ctx_dict = {'text': message, 'subject': subject, 'messages': [
            {'body_text': message, 'from_agent': False}
        ]}
        clf = classifier.classify(ctx_dict)

        urgency  = clf.urgency
        category = clf.category
        escalate = clf.escalate
        auto_ok  = clf.auto_draft_allowed
        reasons  = '; '.join(clf.reasons) if clf.reasons else 'no specific rule'
        kws      = ', '.join(clf.matched_keywords) if clf.matched_keywords else '—'

        print(f"  Classify: {category} | urgency={urgency} | escalate={escalate} | auto_draft={auto_ok}")
        print(f"  Triggers: {kws}")

        print(f"  → Calling deepseek-v4-flash:cloud for draft...", end='', flush=True)
        try:
            result = draft_engine.generate_draft(ctx_dict, clf, top_k=8)
        except Exception as e:
            print(f" ERROR: {e}")
            results.append({'id': qid, 'label': label, 'category': category,
                            'urgency': urgency, 'kb_gap': False,
                            'is_escalation': True, 'should_post': False,
                            'kb_conf': 'none', 'kb_sources': []})
            continue

        if result is None:
            print(" (failed)")
            results.append({'id': qid, 'label': label, 'category': category,
                            'urgency': urgency, 'kb_gap': False,
                            'is_escalation': True, 'should_post': False,
                            'kb_conf': 'none', 'kb_sources': []})
            continue

        print(" done")

        kb_conf     = result.confidence or 'none'
        kb_gap      = result.kb_gap
        is_esc      = result.is_escalation
        should_post = result.should_post
        draft_text  = result.draft_text or ''
        kb_sources  = result.kb_sources or []

        note_type = (
            'escalation note' if is_esc else
            'kb-gap note'     if kb_gap else
            'draft for review'
        )

        print(f"  KB: {kb_conf} ({len(kb_sources)} sources) | gap={kb_gap} | esc={is_esc} | should_post={should_post}")
        print(f"  Note type: {note_type}")

        _post_gorgias(qid, draft_text, mode='internal_note')
        print(f"  → Gorgias: internal_note")

        _send_telegram(
            ticket_id=qid,
            category=category,
            urgency=urgency,
            kb_conf=kb_conf,
            reason=reasons[:200],
            draft_preview=draft_text[:200] if not is_esc else '',
            is_escalation=is_esc,
            kb_gap=kb_gap,
        )
        print(f"  → Telegram: {urgency.upper()} notification sent")

        results.append({
            'id':            qid,
            'label':         label,
            'category':      category,
            'urgency':       urgency,
            'kb_gap':        kb_gap,
            'is_escalation': is_esc,
            'should_post':   should_post,
            'kb_conf':       kb_conf,
            'kb_sources':    kb_sources,
        })

    _print_gorgias()
    _print_telegram()

    print(f"\n{'═'*62}")
    print(f"  SUMMARY — Old System (v2 challenging queries)")
    print(f"{'═'*62}")
    print(f"  {'ID':<5} {'Label':<42} {'Priority':<10} {'KB':<8} {'Type'}")
    print(f"  {'─'*5} {'─'*42} {'─'*10} {'─'*8} {'─'*16}")
    for r in results:
        note_type = (
            'ESCALATION'   if r['is_escalation'] else
            'KB-GAP'       if r['kb_gap']        else
            'DRAFT-REVIEW'
        )
        print(f"  {r['id']:<5} {r['label']:<42} {_URGENCY_LABEL.get(r['urgency'], r['urgency']):<10} {r['kb_conf']:<8} {note_type}")

    imm    = sum(1 for r in results if r['urgency'] == 'immediate')
    high   = sum(1 for r in results if r['urgency'] == 'high')
    normal = sum(1 for r in results if r['urgency'] == 'normal')
    low    = sum(1 for r in results if r['urgency'] == 'low')
    esc    = sum(1 for r in results if r['is_escalation'])
    gap    = sum(1 for r in results if r['kb_gap'])
    draft  = sum(1 for r in results if r['should_post'])
    print(f"\n  IMMEDIATE={imm}  HIGH={high}  NORMAL={normal}  LOW={low}")
    print(f"  ESCALATED={esc}  KB-GAP={gap}  DRAFT-READY={draft}")
    print()


if __name__ == '__main__':
    run()
