"""Keep business urgency, generation health and missing facts independent."""
from __future__ import annotations
import re
from typing import Any
from draft_cleaner import SENSITIVE_DRAFT_PREFIX
from .priority import Priority, at_least, normalize

_HEADER = re.compile(r'^\s*\[SENSITIVE(?:[^\]\r\n]{0,120})\]\s*',re.IGNORECASE)


def final_review_result(value: dict[str,Any]) -> dict[str,Any]:
    """Return a copy with coherent warning, priority and notification fields.

    Escalation can only increase. No-draft/authentication failures stay empty;
    this function never creates a customer reply or performs an external action.
    """
    result=dict(value)
    priority=normalize(result.get('priority',Priority.NORMAL.value),default=Priority.NORMAL)
    action=str(result.get('action','')).strip().lower()
    draft=str(result.get('draft_text') or '').strip()
    state=result.get('generation_state')
    review=bool(result.get('review_required')) or action=='no_kb_match'
    header=_HEADER.match(draft)
    sensitive=(at_least(priority,Priority.HIGH) or
               (action in {'sensitive_draft','escalated'} and not review))
    if sensitive:
        result['action']='sensitive_draft'
        result['priority']=priority if at_least(priority,Priority.HIGH) else Priority.HIGH.value
        result['notify_owner']=True
        if draft and not result.get('no_draft'):
            if not draft.lower().startswith(SENSITIVE_DRAFT_PREFIX.lower()):
                header=_HEADER.match(draft)
                body=draft[header.end():] if header else draft
                draft=f'{SENSITIVE_DRAFT_PREFIX}\n\n{body}'
    else:
        result['priority']=priority
        result['notify_owner']=False
        if header:
            draft=draft[header.end():]
            review=True
    result['review_required']=review
    if state=='no_reply' or action=='no_draft_needed':
        result.update(priority='low', action='no_draft_needed', notify_owner=False,
                      review_required=False, generation_state='no_reply', no_draft=True)
    elif state=='failed':
        result['no_draft']=True
    else:
        result['generation_state']='needs_review' if review else 'ready'
    if review and not result.get('staff_next_step'):
        result['staff_next_step']='Check the missing answer and complete the reply before sending.'
    result['draft_text']='' if result.get('no_draft') else draft
    result['gorgias_priority_set']=False
    result['note_posted']=False
    return result
