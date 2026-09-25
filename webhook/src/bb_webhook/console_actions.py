"""Human authority, durable write intent and read-only reconciliation."""
from __future__ import annotations

import asyncio
import json

from fastapi.responses import JSONResponse

from . import deps
from .logging_utils import get_logger, log_event
from .send_intents import ActionConflict, IntentStore, valid_operation

logger = get_logger(__name__)


def actor(request):
    if getattr(request.state, 'actor_role', None) != 'owner':
        return None
    return getattr(request.state, 'actor_id', None)


def stored_response(row):
    return JSONResponse(status_code=row['response_status'], content=json.loads(row['response_json']))


async def capture_learning(store, row, recorder):
    if row['kind'] != 'send' or row['state'] != 'sent' or not row['approve_learning'] or row['learning_recorded']:
        return
    try:
        captured = recorder('sent', row['ticket_id'], row['customer_message'], row['ai_draft'], row['approved_text'],
                            operation_id=row['operation_id'], review_actor=row['actor_id'],
                            learning_approved=True, delivery_status='sent', approved_at=row['created_at'])
        if captured is not True:
            raise RuntimeError('lesson capture returned false')
        await store.mark_learning_recorded(row['operation_id'])
    except Exception as exc:
        log_event(logger, 'ERROR', 'Approved learning capture failed; action retained for retry',
                  operation_id=row['operation_id'], error_type=type(exc).__name__)


async def preflight_refusal(status, error, body):
    """Do not erase an earlier ambiguous operation when this request is refused."""
    payload={'ok':False,'error':error}
    operation_id=body.get('operation_id') if isinstance(body,dict) else None
    if valid_operation(operation_id):
        try:
            existing=await IntentStore(deps.get_db()).get(operation_id)
        except Exception:
            return JSONResponse(status_code=status,content=payload)
        if existing is not None:
            return JSONResponse(status_code=status,content=payload)
    payload['delivery_status']='not_attempted'
    return JSONResponse(status_code=status,content=payload)


async def execute_action(kind, ticket_id, request, body, text, client_factory, recorder):
    actor_id = actor(request)
    if not actor_id:
        return JSONResponse(status_code=401, content={'error': 'not_authenticated'})
    if body.get('confirmed') is not True:
        return await preflight_refusal(409,'confirmation_required',body)
    source_id = body.get('source_message_id')
    if not isinstance(source_id, (str, int)) or isinstance(source_id, bool):
        return await preflight_refusal(400,'source_message_id_required',body)
    if 'approve_learning' in body and type(body['approve_learning']) is not bool:
        return await preflight_refusal(400,'invalid_learning_approval',body)
    for field in ('expected_recipient', 'context_id'):
        if field in body and not isinstance(body[field], str):
            return await preflight_refusal(400, 'invalid_review_context', body)
    store = IntentStore(deps.get_db())
    try:
        row, fresh = await store.reserve(operation_id=body.get('operation_id'), actor_id=actor_id,
                                         kind=kind, ticket_id=ticket_id, source_message_id=str(source_id),
                                         text=text, draft_revision=body.get('draft_revision'), approve_learning=kind == 'send' and body.get('approve_learning') is True,
                                         expected_recipient=body.get('expected_recipient'), expected_context_id=body.get('context_id'))
    except ActionConflict as exc:
        if exc.error in {'valid_operation_id_required','source_message_id_required','draft_revision_required','source_message_not_in_console','recipient_unavailable','draft_changed_refresh_ticket','recipient_changed_refresh_ticket','review_changed_refresh_ticket'}:
            return await preflight_refusal(exc.status,exc.error,body)
        return JSONResponse(status_code=exc.status, content={'error': exc.error,
            'message': {'previous_delivery_unresolved': 'An earlier action is unresolved. Check its status before sending again.',
                        'learning_approval_is_fixed_for_existing_action': 'This reply already has a recorded approval choice. Resending cannot change it; check its status.',
                        'operation_id_conflict': 'This operation belongs to a different reviewed action. Check its existing status.'}.get(exc.error, exc.error),
            **({'operation_id': exc.operation_id} if exc.operation_id else {})})
    if not fresh:
        await capture_learning(store, row, recorder)
        return stored_response(row)
    operation_id = row['operation_id']
    log_event(logger, 'INFO', 'Confirmed console action reserved', operation_id=operation_id,
              actor_id=actor_id, ticket_id=ticket_id, kind=kind)
    try:
        client = client_factory()
        async def on_created(message_id):
            await store.attach_message(operation_id, message_id)
        if kind == 'send':
            result = await client.send_public_reply(ticket_id, text, expected_recipient=row['recipient'],
                expected_source_message_id=row['source_message_id'], on_created=on_created)
            delivery = result.get('delivery_status', 'unknown')
        else:
            result = await client.post_internal_note(ticket_id, text, on_created=on_created)
            delivery = 'recorded' if result.get('ok') and (result.get('message') or {}).get('id') else 'unknown'
        # Read the persisted ID even if the transport's delivery check omitted it.
        current = await store.get(operation_id)
        message_id = current['remote_message_id']
        if delivery in {'sent', 'recorded'} and message_id:
            state, code = delivery, 200
            response = {'ok': True, 'delivery_status': delivery, 'operation_id': operation_id, 'message_id': message_id}
        elif delivery == 'not_attempted':
            state, code = 'failed', 409
            response = {'ok': False, 'delivery_status': 'not_attempted', 'operation_id': operation_id,
                        'error': result.get('error', 'send_not_attempted'),
                        'message': 'No message was posted. Refresh the ticket before reviewing again.'}
        elif delivery == 'failed' and message_id:
            state, code = 'failed', 409
            response = {'ok': False, 'delivery_status': 'failed', 'operation_id': operation_id,
                        'message_id': message_id, 'error': 'remote_delivery_failed',
                        'message': 'Gorgias reports delivery failed. Inspect the message in Gorgias.'}
        else:
            state, code = ('pending' if message_id else 'uncertain'), 202
            response = {'ok': False, 'delivery_status': 'pending' if message_id else 'unknown',
                        'operation_id': operation_id, 'error': 'delivery_unconfirmed',
                        'message': 'Check action status. Do not send again until the outcome is known.',
                        **({'message_id': message_id} if message_id else {})}
        row = await store.finish(operation_id, state, response, code)
    except asyncio.CancelledError:
        # The pre-POST intent survives cancellation; a second request cannot resend.
        raise
    except Exception as exc:
        log_event(logger, 'ERROR', 'Console action outcome uncertain; automatic resend blocked',
                  operation_id=operation_id, error_type=type(exc).__name__)
        row = await store.get(operation_id)
    await capture_learning(store, row, recorder)
    return stored_response(row)


async def action_status(ticket_id, operation_id, request, client_factory, recorder):
    if not actor(request):
        return JSONResponse(status_code=401, content={'error': 'not_authenticated'})
    if not valid_operation(operation_id):
        return JSONResponse(status_code=400, content={'error': 'invalid_operation_id'})
    store = IntentStore(deps.get_db())
    row = await store.get(operation_id)
    if row is None or row['ticket_id'] != ticket_id or row['actor_id'] != actor(request):
        return JSONResponse(status_code=404, content={'error': 'action_not_found'})
    if row['remote_message_id'] and row['state'] in {'pending', 'uncertain'}:
        # This method issues GET requests only. Never infer non-delivery from
        # absence, time elapsed or an unavailable/unauthorized provider response.
        try:
            result = await client_factory()._wait_for_delivery(ticket_id, row['remote_message_id'])
        except Exception as exc:
            log_event(logger, 'ERROR', 'Action reconciliation unavailable', operation_id=operation_id, error_type=type(exc).__name__)
            return stored_response(row)
        delivery = result.get('status')
        if delivery in {'sent', 'failed'}:
            state = 'recorded' if row['kind'] == 'note' and delivery == 'sent' else delivery
            response = {'ok': delivery == 'sent', 'delivery_status': state,
                        'operation_id': operation_id, 'message_id': row['remote_message_id']}
            if delivery == 'failed':
                response['error'] = 'remote_delivery_failed'
            row = await store.finish(operation_id, state, response, 200 if delivery == 'sent' else 409)
    await capture_learning(store, row, recorder)
    return stored_response(row)
