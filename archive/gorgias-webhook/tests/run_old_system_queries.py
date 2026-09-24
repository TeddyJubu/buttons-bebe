#!/usr/bin/env python3
"""
run_old_system_queries.py — run the same 30 queries through the OLD gorgias-webhook
system and capture output the same way run_queries.py does for Teddy.

Uses Ollama Cloud (deepseek-v4-flash:cloud) for LLM — same model as Teddy's test.
KB service is not required — kb_client.py falls back to BM25 file search.
Nothing is sent to real Gorgias or Telegram.

Usage:
    cd /root/gorgias-webhook
    python3 tests/run_old_system_queries.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dotenv_loader
dotenv_loader.load()

# ── Configure model_gateway to use Ollama Cloud BEFORE importing anything else ──
os.environ['LLM_PROVIDER']  = 'openai-compatible'
os.environ['LLM_BASE_URL']  = 'https://ollama.com/v1'
os.environ['LLM_MODEL']     = 'deepseek-v4-flash:cloud'
os.environ['LLM_API_KEY']   = os.environ.get('OLLAMA_API_KEY', '')
# Disable kb_service so kb_client falls back to BM25 file search
os.environ['KB_SERVICE_ENABLED'] = '0'

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPT_DIR)

import classifier
import draft_engine

# ── In-memory fake outputs ────────────────────────────────────────────────────

_gorgias_inbox = []
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
    record = {
        'ticket_id': str(ticket_id),
        'mode':      mode,
        'body':      body,
        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }
    _gorgias_inbox.append(record)


def _send_telegram(ticket_id, category, urgency, kb_conf, reason, draft_preview='', is_escalation=False, kb_gap=False):
    emoji     = _URGENCY_EMOJI.get(urgency, '❓')
    kb_icon   = _CONF_EMOJI.get(kb_conf, '❓')

    tag = ''
    if is_escalation:
        tag = ' 🔴 ESCALATED'
    elif kb_gap:
        tag = ' 🟡 KB-GAP'

    lines = [
        f"{emoji} {urgency.upper()}{tag}",
        f"Ticket #{ticket_id} | {category}",
        f"KB: {kb_icon} {kb_conf}",
        f"Reason: {reason}",
    ]
    if draft_preview:
        preview = draft_preview[:200].replace('\n', ' ')
        lines.append(f"Draft: {preview}")

    text = '\n'.join(lines)

    _telegram_inbox.append({
        'ticket_id': str(ticket_id),
        'urgency':   urgency,
        'category':  category,
        'kb_conf':   kb_conf,
        'is_escalation': is_escalation,
        'kb_gap':    kb_gap,
        'text':      text,
        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    })


def _print_gorgias():
    sep = '═' * 60
    print(f"\n{sep}")
    print(f"  Old System — Fake Gorgias inbox  ({len(_gorgias_inbox)} message{'s' if len(_gorgias_inbox) != 1 else ''})")
    print(sep)
    if not _gorgias_inbox:
        print("  (empty)")
        return
    for msg in _gorgias_inbox:
        label = '📤 PUBLIC REPLY' if msg['mode'] == 'public_reply' else '📋 INTERNAL NOTE'
        print(f"\n  Ticket #{msg['ticket_id']}  [{label}]")
        print(f"  {'-'*56}")
        for line in (msg['body'] or '').split('\n'):
            print(f"  {line}")


def _print_telegram():
    sep = '═' * 60
    print(f"\n{sep}")
    print(f"  Old System — Fake Telegram inbox  ({len(_telegram_inbox)} notification{'s' if len(_telegram_inbox) != 1 else ''})")
    print(sep)
    if not _telegram_inbox:
        print("  (empty)")
        return
    for msg in _telegram_inbox:
        print(f"\n  [{msg['timestamp']}]")
        for line in msg['text'].split('\n'):
            print(f"  {line}")


# ── Same 30 queries as Teddy's test ──────────────────────────────────────────

QUERIES = [
    # ── IMMEDIATE (8) ─────────────────────────────────────────────────────────
    {
        'id': 'Q01', 'label': 'Address change before shipment',
        'subject': 'Change my shipping address',
        'message': 'Hi, I just placed order #4821 and realised I typed the wrong address. '
                   'Please change it to 42 Oak Street, Brooklyn, NY 11201 before it ships!',
    },
    {
        'id': 'Q02', 'label': 'Cancel order urgently',
        'subject': 'Cancel my order please',
        'message': "I need to cancel my order immediately — I ordered the wrong thing. "
                   "Please don't ship it, I need to cancel before it goes out.",
    },
    {
        'id': 'Q03', 'label': 'Wrong size ordered (pre-shipment)',
        'subject': 'Ordered wrong size',
        'message': 'I accidentally ordered size 2T but I need 4T. Can you change the size '
                   'before my order ships? Order placed about 20 minutes ago.',
    },
    {
        'id': 'Q04', 'label': 'Switch pickup to shipping',
        'subject': 'Switch to shipping instead of pickup',
        'message': "I selected in-store pickup by mistake. I live in Miami and can't come pick it up. "
                   "Can you switch it to shipping? I'll pay the shipping fee.",
    },
    {
        'id': 'Q05', 'label': 'Wrong zip code entered',
        'subject': 'Wrong zip code on my order',
        'message': 'I put zip code 10001 but my correct zip is 10002. Please fix this '
                   'before the package ships — I am worried it will go to the wrong place!',
    },
    {
        'id': 'Q06', 'label': 'Wrong colour ordered (pre-shipment)',
        'subject': 'I ordered the wrong colour',
        'message': 'I just placed order #6102 five minutes ago and chose the pink onesie by '
                   'mistake — I need the yellow one. Can you swap it before it ships?',
    },
    {
        'id': 'Q07', 'label': 'Double charge — payment issue',
        'subject': 'Charged twice for my order',
        'message': 'I was charged twice for my order this morning — I can see two identical '
                   'transactions of $68.50 on my bank statement. Please reverse one of them immediately.',
    },
    {
        'id': 'Q08', 'label': 'Order out of stock after payment',
        'subject': 'Got confirmation but item is out of stock?',
        'message': 'I received my order confirmation but then got an email saying the item is '
                   'out of stock. I already paid. What happens now? Do I get a refund or a '
                   'substitute? I need this for a gift by Friday.',
    },
    # ── HIGH (12) ─────────────────────────────────────────────────────────────
    {
        'id': 'Q09', 'label': 'Damaged item received',
        'subject': 'Package arrived damaged',
        'message': 'My order arrived today and the onesie is completely damaged — there is a '
                   'large tear in the fabric. This is unacceptable for a baby item. '
                   'What are you going to do about this?',
    },
    {
        'id': 'Q10', 'label': 'Wrong item in box',
        'subject': 'Received wrong item',
        'message': 'I ordered the floral dress in size 3T but received a plain blue romper in 18M. '
                   'This is the wrong item entirely. I need the correct one sent ASAP.',
    },
    {
        'id': 'Q11', 'label': 'Refund demand — poor quality',
        'subject': 'Requesting a refund',
        'message': 'The quality of the clothing I received is very poor. The stitching came '
                   'apart after one wash. I want a full refund. Very disappointed.',
    },
    {
        'id': 'Q12', 'label': 'Angry customer — social media threat',
        'subject': 'Worst experience ever',
        'message': 'This is the worst online shopping experience I have ever had. My order '
                   'is 3 weeks late and nobody responds. I am posting about this on Instagram '
                   'and TikTok. Absolutely disgusted.',
    },
    {
        'id': 'Q13', 'label': 'Lost package — marked delivered',
        'subject': 'Package says delivered but not here',
        'message': 'According to the tracking my package was delivered yesterday but I never '
                   'received it. I checked with my neighbours and the building front desk — '
                   'nothing. It says delivered but it is not here.',
    },
    {
        'id': 'Q14', 'label': 'Safety concern — button/choking hazard',
        'subject': 'Button fell off — safety issue',
        'message': 'A decorative button came off the jacket I bought for my 8-month-old after '
                   'just two washes. This is a serious choking hazard. I am very concerned '
                   'about the safety of your products.',
    },
    {
        'id': 'Q15', 'label': 'Allergic reaction to fabric',
        'subject': 'My baby had a skin reaction',
        'message': 'My daughter developed a rash after wearing the romper I bought from you. '
                   'The doctor thinks it might be an allergic reaction to the fabric or dye. '
                   'I want to know what materials are used and I want a refund.',
    },
    {
        'id': 'Q16', 'label': 'Package never arrived — 4 weeks',
        'subject': 'Order never arrived',
        'message': 'I placed my order 4 weeks ago and it has never arrived. Tracking has not '
                   'updated in 3 weeks. The carrier says to contact the sender. This is an '
                   'expensive order and I want it reshipped or refunded.',
    },
    {
        'id': 'Q17', 'label': 'Partial order — item missing from box',
        'subject': 'Missing item from my order',
        'message': 'I ordered 3 items and only received 2. The missing item is the pink '
                   'bodysuit 3-pack in size 6M. The packing slip shows all 3 items but '
                   'only 2 were in the box.',
    },
    {
        'id': 'Q18', 'label': 'Item looks different from website',
        'subject': 'Product not as advertised',
        'message': 'The dress I received looks completely different from the photos on your '
                   'website. The colour is much darker, the fabric is thinner, and the '
                   'embroidery detail is missing. I feel misled. I want to return it.',
    },
    {
        'id': 'Q19', 'label': 'Exchange request — wrong size gifted',
        'subject': 'Need to exchange a gift',
        'message': 'My mother bought a onesie as a gift for my baby but it is the wrong size — '
                   'she got 3M and my baby is already in 9M. Can we exchange it for the '
                   'correct size? We do not have the receipt.',
    },
    {
        'id': 'Q20', 'label': 'Fraud / scam accusation',
        'subject': 'This feels like a scam',
        'message': 'I ordered 6 weeks ago, the tracking says "in transit" since day 1, and '
                   'nobody responds to my emails. This is fraud. I am filing a chargeback '
                   'with my credit card and reporting you to the BBB.',
    },
    # ── LOW (10) ──────────────────────────────────────────────────────────────
    {
        'id': 'Q21', 'label': 'Order status inquiry',
        'subject': 'Where is my order?',
        'message': 'Hi! I placed my order 5 days ago and have not received a shipping '
                   'confirmation yet. When will it ship? Order number #5503.',
    },
    {
        'id': 'Q22', 'label': 'Shipping time question',
        'subject': 'How long does shipping take?',
        'message': 'Hi! I am ordering a gift for a baby shower happening in 10 days. '
                   'Do you ship to New Jersey and how long does standard shipping usually take?',
    },
    {
        'id': 'Q23', 'label': 'Sizing question for toddler',
        'subject': 'What size for a 3 year old?',
        'message': 'Hi, I want to buy clothes for my daughter who just turned 3. '
                   'She is average height and weight for her age. What size do you recommend — 2T or 3T?',
    },
    {
        'id': 'Q24', 'label': 'Return policy question',
        'subject': 'Return policy',
        'message': 'Hello! I received my order and the item is not quite what I expected — '
                   'the colour looks different in person. What is your return window and '
                   'how do I start a return?',
    },
    {
        'id': 'Q25', 'label': 'First-time customer discount',
        'subject': 'Discount for first-time customers?',
        'message': 'Hi there! I am a first time customer and was wondering if you have '
                   'any discount codes or promotions for new customers. Excited to shop!',
    },
    {
        'id': 'Q26', 'label': 'Gift wrapping available?',
        'subject': 'Do you offer gift wrapping?',
        'message': 'Hello! I am buying this as a baby shower gift. Do you offer gift wrapping '
                   'or a gift message option? I would love to send it directly to the recipient.',
    },
    {
        'id': 'Q27', 'label': 'Care instructions — washing',
        'subject': 'How do I wash the clothes?',
        'message': 'I just received the organic cotton onesie set and want to make sure I wash '
                   'it correctly. Can I put it in the dryer or does it need to be hang dried? '
                   'Any special care instructions I should know about?',
    },
    {
        'id': 'Q28', 'label': 'International shipping question',
        'subject': 'Do you ship to Canada?',
        'message': 'Hi! I am based in Toronto, Canada. Do you ship internationally and if so '
                   'what are the shipping costs and estimated delivery times to Canada?',
    },
    {
        'id': 'Q29', 'label': 'Promo code not working',
        'subject': 'My discount code is not working',
        'message': 'I have a promo code BEBE10 that I got from your newsletter but it keeps '
                   'saying "invalid code" at checkout. I am trying to order the floral dress '
                   'set. Can you help?',
    },
    {
        'id': 'Q30', 'label': 'Restock question — out of stock item',
        'subject': 'When will this be back in stock?',
        'message': 'Hi! I have been trying to buy the rainbow stripe onesie in size 12M for '
                   'weeks but it is always out of stock. Do you know when it will be restocked? '
                   'Can I sign up for a notification?',
    },
]

# Map old urgency → simple label for summary table
_URGENCY_LABEL = {
    'immediate': 'IMMEDIATE',
    'high':      'HIGH',
    'normal':    'NORMAL',
    'low':       'LOW',
}


def run():
    print(f"\n{'╔' + '═'*62 + '╗'}")
    print(f"║  Old System (gorgias-webhook) × deepseek-v4-flash — 30 query test  ║")
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

        # 1. Classify
        ctx_dict = {'text': message, 'subject': subject, 'messages': [
            {'body_text': message, 'from_agent': False}
        ]}
        clf = classifier.classify(ctx_dict)

        urgency   = clf.urgency
        category  = clf.category
        escalate  = clf.escalate
        auto_ok   = clf.auto_draft_allowed
        reasons   = '; '.join(clf.reasons) if clf.reasons else 'no specific rule'
        kws       = ', '.join(clf.matched_keywords) if clf.matched_keywords else '—'

        print(f"  Classify: {category} | urgency={urgency} | escalate={escalate} | auto_draft={auto_ok}")
        print(f"  Triggers: {kws}")

        # 2. Generate draft
        print(f"  → Calling deepseek-v4-flash:cloud for draft...", end='', flush=True)
        try:
            result = draft_engine.generate_draft(ctx_dict, clf, top_k=8)
        except Exception as e:
            print(f" ERROR: {e}")
            result = None

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

        # Old system NEVER auto-sends — all output goes as internal note
        # (except pure escalations which have no customer-ready draft)
        note_type = (
            'escalation note' if is_esc else
            'kb-gap note'     if kb_gap else
            'draft for review'
        )

        print(f"  KB: {kb_conf} ({len(kb_sources)} sources) | gap={kb_gap} | esc={is_esc} | should_post={should_post}")
        print(f"  Note type: {note_type}")

        # Post internal note to fake Gorgias
        _post_gorgias(qid, draft_text, mode='internal_note')
        print(f"  → Gorgias: internal_note")

        # Send Telegram notification
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
            'id':           qid,
            'label':        label,
            'category':     category,
            'urgency':      urgency,
            'kb_gap':       kb_gap,
            'is_escalation': is_esc,
            'should_post':  should_post,
            'kb_conf':      kb_conf,
            'kb_sources':   kb_sources,
        })

    # ── Full inbox printouts ──────────────────────────────────────────────────
    _print_gorgias()
    _print_telegram()

    # ── Summary table ─────────────────────────────────────────────────────────
    print(f"\n{'═'*62}")
    print(f"  SUMMARY — Old System")
    print(f"{'═'*62}")
    print(f"  {'ID':<5} {'Label':<40} {'Priority':<10} {'KB':<8} {'Type'}")
    print(f"  {'─'*5} {'─'*40} {'─'*10} {'─'*8} {'─'*16}")
    for r in results:
        note_type = (
            'ESCALATION'   if r['is_escalation'] else
            'KB-GAP'       if r['kb_gap']        else
            'DRAFT-REVIEW'
        )
        print(f"  {r['id']:<5} {r['label']:<40} {_URGENCY_LABEL.get(r['urgency'], r['urgency']):<10} {r['kb_conf']:<8} {note_type}")

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
