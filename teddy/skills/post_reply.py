"""
post_reply.py — posts a draft reply to a Gorgias ticket.

Modes:
  "internal_note"  — only visible to your support team (safe, human reviews first)
  "public_reply"   — sent directly to the customer (use only for auto-send intents)

Gate: only posts when WORKFLOW_A_CONFIRM=1 in .env.
The caller (agent.py) checks this gate before calling this skill.

Gorgias requires a custom User-Agent — their WAF blocks the default Python one.
"""

import logging
import os

import requests

log = logging.getLogger('teddy.post_reply')

_USER_AGENT = 'Teddy-Agent/1.0 (Buttons Bebe support bot)'
_CHANNEL_MAP = {
    'internal_note': 'internal-note',
    'public_reply':  'email',
}


def post_reply(
    ticket_id: str,
    draft: str,
    mode: str,
    domain: str,
    email: str,
    api_key: str,
) -> dict:
    """
    post_reply(...) -> {"posted": bool, "message_id": str | None, "error": str | None}

    mode must be "internal_note" or "public_reply".
    """
    if not all([ticket_id, draft, domain, email, api_key]):
        log.error("post_reply called with missing required args")
        return {'posted': False, 'message_id': None, 'error': 'Missing required args'}

    channel = _CHANNEL_MAP.get(mode, 'internal-note')
    url = f"https://{domain}.gorgias.com/api/tickets/{ticket_id}/messages"

    payload = {
        'channel': channel,
        'via':     'api',
        'body_text': draft,
        'body_html': f'<p>{draft.replace(chr(10), "<br>")}</p>',
    }

    try:
        resp = requests.post(
            url,
            json=payload,
            auth=(email, api_key),
            headers={'User-Agent': _USER_AGENT},
            timeout=10,
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            msg_id = str(data.get('id', ''))
            log.info("Posted %s to ticket #%s (message_id=%s)", mode, ticket_id, msg_id)
            return {'posted': True, 'message_id': msg_id, 'error': None}
        else:
            log.error("Gorgias API error %s: %s", resp.status_code, resp.text[:200])
            return {'posted': False, 'message_id': None, 'error': f"HTTP {resp.status_code}"}

    except Exception as e:
        log.error("post_reply request failed: %s", e)
        return {'posted': False, 'message_id': None, 'error': str(e)}
