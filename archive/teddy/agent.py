"""
agent.py — Teddy, the Buttons Bebe AI support agent.

Receives Gorgias webhooks, assigns priority, orchestrates skills, posts replies.

Priority tiers:
  IMMEDIATE — notify owner, act now. No draft. Time-sensitive / irreversible.
  HIGH      — notify owner, draft reply as internal note. Can wait a few hours.
  LOW       — auto-draft. Potentially auto-send for trusted intents.
"""

import hashlib
import hmac
import json
import logging
import os
import time
from pathlib import Path

import sys
sys.path.insert(0, '/root/gorgias-webhook')
import dotenv_loader
dotenv_loader.load()

from flask import Flask, request, jsonify
from openai import OpenAI

from skills.classify     import classify
from skills.kb_router    import search as kb_search
from skills.prioritize   import prioritize, enforce_monotonic
from skills.lookup_order import lookup_order
from skills.post_reply   import post_reply
from skills.notify       import notify
from skills.learn        import capture_reply as learn_capture
from skills.kb_gap       import record_gap
from skills.scrub_pii    import scrub as scrub_pii

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
)
log = logging.getLogger('teddy')

# ── Config ────────────────────────────────────────────────────────────────────
GORGIAS_DOMAIN  = os.environ.get('GORGIAS_DOMAIN', '')
GORGIAS_EMAIL   = os.environ.get('GORGIAS_EMAIL', '')
GORGIAS_API_KEY = os.environ.get('GORGIAS_API_KEY', '')
WEBHOOK_SECRET  = os.environ.get('WEBHOOK_SECRET', '')
LLM_BASE_URL    = os.environ.get('LLM_BASE_URL', '')
LLM_API_KEY     = os.environ.get('LLM_API_KEY', '')
LLM_MODEL       = os.environ.get('LLM_MODEL', 'deepseek-v4-flash:cloud')
PORT            = int(os.environ.get('PORT', 8000))

WORKFLOW_A_CONFIRM = os.environ.get('WORKFLOW_A_CONFIRM', '0') == '1'
AUTO_SEND_INTENTS  = [
    i.strip()
    for i in os.environ.get('AUTO_SEND_INTENTS', 'ORDER_STATUS').split(',')
    if i.strip()
]

KB_DIR   = Path(__file__).parent / 'kb'
LOG_FILE = Path(__file__).parent / 'log.jsonl'

# ── LLM client (module-level singleton, not recreated per request) ─────────────
_llm = OpenAI(base_url=LLM_BASE_URL or None, api_key=LLM_API_KEY or 'placeholder')

# ── Flask ─────────────────────────────────────────────────────────────────────
app = Flask(__name__)

# ── Deduplication ─────────────────────────────────────────────────────────────
_seen: dict = {}
_DEDUP_SECONDS = 90

def _is_duplicate(ticket_id: str) -> bool:
    now = time.time()
    _seen.update({k: v for k, v in _seen.items() if now - v < _DEDUP_SECONDS})
    if ticket_id in _seen:
        return True
    _seen[ticket_id] = now
    return False

# ── Draft store (for learning loop) ───────────────────────────────────────────
# Keyed by ticket_id → (intent, draft_text, timestamp)
# Holds the last draft we generated so that when the human agent replies,
# we can compare and capture any new knowledge into kb/learned/.
_pending_drafts: dict = {}
_DRAFT_TTL = 3600  # expire drafts after 1 hour

def _store_draft(ticket_id: str, intent: str, draft: str):
    now = time.time()
    for k in list(_pending_drafts.keys()):
        if now - _pending_drafts[k][2] > _DRAFT_TTL:
            del _pending_drafts[k]
    _pending_drafts[ticket_id] = (intent, draft, now)

# ── Webhook auth ──────────────────────────────────────────────────────────────
def _verify(req) -> bool:
    if not WEBHOOK_SECRET:
        log.debug("WEBHOOK_SECRET not set — accepting all requests")
        return True
    received = (
        req.headers.get('X-Gorgias-Secret') or
        req.headers.get('X-Webhook-Secret') or ''
    )
    return hmac.compare_digest(received, WEBHOOK_SECRET)

# ── LLM draft ────────────────────────────────────────────────────────────────
_MAX_MSG_CHARS = 1500   # truncate very long customer messages before sending to LLM
_MAX_CONTEXT_CHARS = 12000  # KB context ceiling (was 3000 — too small once
# returns.md grew to ~9K; the restocking-fee/gift-wrapped/missing-item sections
# sit past 3K, so a routed file's answer got truncated away -> false KB-gap.

def _draft_reply(kb_context: str, messages: list, order_data) -> str:
    order_section = ''
    if order_data:
        order_section = (
            '\n\n## Order Information\n'
            + json.dumps(order_data, indent=2, default=str)
        )

    context_snippet = (kb_context or '(no relevant articles found)')[:_MAX_CONTEXT_CHARS]

    system_prompt = (
        "You are a helpful customer support assistant for Buttons Bebe, "
        "a children's clothing brand.\n\n"
        "Draft a reply to the customer using ONLY the information provided "
        "in the Knowledge Base and Order Information below.\n"
        "If the answer is not clearly stated, write: ESCALATE: [brief reason]\n\n"
        "Rules:\n"
        "- Never invent policies, prices, timelines, or promises.\n"
        "- Be warm and concise (2–4 sentences is usually enough).\n"
        "- Sign off as 'The Buttons Bebe Team'.\n\n"
        f"## Knowledge Base\n{context_snippet}"
        f"{order_section}"
    )

    # Last 3 customer messages, truncated
    convo = '\n\n'.join(
        f"{'Agent' if m.get('from_agent') else 'Customer'}: "
        f"{(m.get('body_text') or '').strip()[:_MAX_MSG_CHARS]}"
        for m in messages[-3:]
        if (m.get('body_text') or '').strip()
    )

    # Two attempts with a short back-off
    for attempt in range(2):
        try:
            response = _llm.chat.completions.create(
                model=LLM_MODEL,
                messages=[
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user',   'content': f"Conversation:\n{convo}"},
                ],
                timeout=20,
                max_tokens=500,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            log.warning("LLM attempt %d/2 failed: %s", attempt + 1, e)
            if attempt == 0:
                time.sleep(2)

    return 'LLM_UNAVAILABLE'

# ── Audit log ─────────────────────────────────────────────────────────────────
def _log(record: dict):
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record) + '\n')
    except Exception as e:
        log.warning("Log write failed: %s", e)

# ── Main webhook ──────────────────────────────────────────────────────────────
@app.route('/webhook', methods=['POST'])
def webhook():

    # 1. Auth
    if not _verify(request):
        log.warning("Rejected webhook: bad secret")
        return jsonify({'error': 'forbidden'}), 403

    # 2. Parse
    try:
        payload = request.get_json(force=True) or {}
    except Exception:
        return jsonify({'error': 'invalid json'}), 400
    if not isinstance(payload, dict):
        return jsonify({'error': 'bad payload'}), 400

    ticket    = payload.get('ticket') or {}
    ticket_id = str(ticket.get('id', ''))
    subject   = ticket.get('subject', '') or ''
    customer  = ticket.get('customer') or {}
    email     = customer.get('email', '') or ''
    messages  = ticket.get('messages') or []

    if not ticket_id:
        return jsonify({'status': 'skipped', 'reason': 'no ticket id'}), 200

    # 3. Learning capture — must run BEFORE dedup so agent replies aren't blocked.
    #    When the last message is from an agent on a ticket we previously drafted
    #    for, compare the human reply to our draft and learn from any differences.
    last_msg_obj = messages[-1] if messages else {}
    if last_msg_obj.get('from_agent'):
        pending = _pending_drafts.pop(ticket_id, None)
        if pending:
            intent_p, draft_p, _ = pending
            agent_reply = (last_msg_obj.get('body_text') or '').strip()
            if agent_reply:
                learn_capture(ticket_id, agent_reply, intent_p, draft_p, str(KB_DIR))
        return jsonify({'status': 'skipped', 'reason': 'agent reply captured'}), 200

    # 4. Skip if there are no customer messages at all
    customer_msgs = [m for m in messages if not m.get('from_agent')]
    if not customer_msgs:
        log.info("Ticket #%s: only agent messages, skipping", ticket_id)
        return jsonify({'status': 'skipped', 'reason': 'agent message'}), 200

    # 5. Dedup
    if _is_duplicate(ticket_id):
        log.info("Ticket #%s: duplicate, skipping", ticket_id)
        return jsonify({'status': 'skipped', 'reason': 'duplicate'}), 200

    last_msg = (customer_msgs[-1].get('body_text') or '').strip()
    log.info("Ticket #%s | subject: %s", ticket_id, subject[:60])

    # 5. Classify intent
    clf    = classify(subject, last_msg)
    intent = clf['intent']
    log.info("Ticket #%s → intent=%s conf=%.2f", ticket_id, intent, clf['confidence'])

    # 6. Order lookup (ORDER_STATUS tickets only)
    order_data = None
    if intent == 'ORDER_STATUS' and email:
        result = lookup_order(email)
        order_data = result.get('order')
        if result.get('error'):
            log.warning("Ticket #%s Shopify lookup error: %s", ticket_id, result['error'])

    # 7. KB search (keyword-first, semantic fallback via kb_router)
    kb = kb_search(last_msg, str(KB_DIR), _llm)
    log.info("Ticket #%s KB: %s | files=%s", ticket_id, kb['confidence'], kb['files_used'])

    # Record gap for any ticket the KB couldn't answer
    if kb['confidence'] == 'NONE':
        record_gap(ticket_id, intent, last_msg, str(KB_DIR))

    # 8. Priority
    prio = prioritize(intent, kb['confidence'], order_data, last_msg, messages)
    log.info("Ticket #%s → priority=%s | %s", ticket_id, prio['level'], prio['reason'])

    # ── IMMEDIATE: alert owner, do NOT generate draft, do NOT post ────────────
    if prio['level'] == 'IMMEDIATE':
        notify(
            ticket_id=ticket_id,
            intent=intent,
            kb_confidence=kb['confidence'],
            priority_level='IMMEDIATE',
            priority_reason=prio['reason'],
            draft_preview='',
            posted=False,
        )
        _log({
            'ticket_id': ticket_id, 'intent': intent,
            'priority': 'IMMEDIATE', 'priority_reason': prio['reason'],
            'kb_confidence': kb['confidence'], 'files_used': kb['files_used'],
            'posted': False,
            'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        })
        return jsonify({'status': 'immediate', 'intent': intent, 'priority': 'IMMEDIATE'}), 200

    # ── HIGH / LOW: generate draft ────────────────────────────────────────────
    draft = _draft_reply(kb['context'], messages, order_data)

    # Handle ESCALATE markers — LLM sometimes places them mid-draft or at the end.
    if draft.startswith('ESCALATE:'):
        # Full escalation — no usable draft text
        prio  = {'level': 'HIGH', 'reason': draft[9:].strip(), 'action': 'draft_internal_note'}
        draft = ''
    elif 'ESCALATE:' in draft:
        # Partial draft with trailing escalation note — keep the draft content for
        # the internal note (it contains useful KB info), but bump priority to HIGH.
        escalate_reason = draft.split('ESCALATE:', 1)[1].strip()
        escalation_floor = {'level': 'HIGH', 'reason': escalate_reason, 'action': 'draft_internal_note'}
        prio = enforce_monotonic(escalation_floor, prio)

    if draft in ('LLM_UNAVAILABLE', '') or not draft:
        if prio['level'] == 'LOW' and kb['confidence'] in ('HIGH', 'MEDIUM'):
            # LLM is down but KB has relevant content — post KB excerpt as internal note.
            draft = (
                '⚠️ LLM unavailable. KB excerpt for manual reply:\n\n'
                + (kb['context'][:600] if kb['context'] else '(no KB context)')
            )
            log.info("Ticket #%s: LLM down, using KB fallback draft (keeping LOW)", ticket_id)
        else:
            draft = '⚠️ LLM unavailable — please draft this reply manually.'
            prio  = {'level': 'HIGH', 'reason': 'LLM unavailable + no KB coverage', 'action': 'draft_internal_note'}

    # Store draft for learning capture when the human agent replies
    _store_draft(ticket_id, intent, draft)

    # 9. Post to Gorgias — Phase 1: always internal note, never auto-send to customer.
    #    A human agent reviews every draft before it leaves Gorgias.
    posted = False
    if WORKFLOW_A_CONFIRM:
        result = post_reply(
            ticket_id=ticket_id, draft=draft, mode='internal_note',
            domain=GORGIAS_DOMAIN, email=GORGIAS_EMAIL, api_key=GORGIAS_API_KEY,
        )
        posted = result.get('posted', False)
    else:
        log.info("DRY RUN ticket #%s [%s]:\n%s", ticket_id, prio['level'], draft)

    # 10. Notify (scrub PII from draft preview before it goes to Telegram)
    notify(
        ticket_id=ticket_id,
        intent=intent,
        kb_confidence=kb['confidence'],
        priority_level=prio['level'],
        priority_reason=prio['reason'],
        draft_preview=scrub_pii(draft[:200]),
        posted=posted,
    )

    # 11. Log (scrub PII from any text fields before writing to disk)
    _log({
        'ticket_id':         ticket_id,
        'intent':            intent,
        'intent_confidence': clf['confidence'],
        'kb_confidence':     kb['confidence'],
        'files_used':        kb['files_used'],
        'priority':          prio['level'],
        'priority_reason':   scrub_pii(prio['reason']),
        'posted':            posted,
        'timestamp':         time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    })

    return jsonify({
        'status':   prio['level'].lower(),
        'intent':   intent,
        'priority': prio['level'],
        'posted':   posted,
    }), 200


# ── Debug / health ────────────────────────────────────────────────────────────
@app.route('/status', methods=['GET'])
def status():
    return jsonify({
        'recently_processed_tickets': list(_seen.keys()),
        'count':              len(_seen),
        'workflow_confirm':   WORKFLOW_A_CONFIRM,
        'auto_send_intents':  AUTO_SEND_INTENTS,
        'kb_dir':             str(KB_DIR),
        'kb_exists':          KB_DIR.exists(),
    })

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'}), 200


# ── Startup checks ────────────────────────────────────────────────────────────
def _startup_check():
    import requests as req

    missing = [v for v in ['GORGIAS_DOMAIN', 'GORGIAS_EMAIL', 'GORGIAS_API_KEY', 'LLM_API_KEY']
               if not os.environ.get(v)]
    if missing:
        log.warning("Missing .env vars: %s", ', '.join(missing))

    if GORGIAS_DOMAIN and GORGIAS_EMAIL and GORGIAS_API_KEY:
        try:
            r = req.get(
                f"https://{GORGIAS_DOMAIN}.gorgias.com/api/account",
                auth=(GORGIAS_EMAIL, GORGIAS_API_KEY),
                headers={'User-Agent': 'Teddy-Agent/1.0'},
                timeout=5,
            )
            log.info("Gorgias credentials: %s", 'OK' if r.status_code == 200 else f'FAILED HTTP {r.status_code}')
        except Exception as e:
            log.warning("Gorgias startup check skipped: %s", e)

    if LLM_API_KEY and LLM_API_KEY != 'placeholder':
        try:
            _llm.models.list()
            log.info("LLM endpoint: OK")
        except Exception as e:
            log.warning("LLM endpoint check failed: %s — drafts will use KB fallback until LLM is reachable", e)

    log.info("Teddy ready | port=%s | confirm=%s | auto_send=%s | kb=%s",
             PORT, WORKFLOW_A_CONFIRM, AUTO_SEND_INTENTS,
             'OK' if KB_DIR.exists() else 'MISSING')


if __name__ == '__main__':
    _startup_check()
    app.run(host='0.0.0.0', port=PORT, debug=False)
