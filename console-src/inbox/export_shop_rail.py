#!/usr/bin/env python3
"""Read-only Shopify rail snapshot. Runs outside the inbox sandbox."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

import shop_rail
from projection import DEFAULT_PATH as PROJECTION_PATH, connect as projection_connect

MAX_LOOKUPS = 25
CACHE_HIT_SECONDS = 6 * 3600
CACHE_MISS_SECONDS = 30 * 60
PAYLOAD_VERSION = shop_rail.PAYLOAD_VERSION
ORDER_RE = re.compile(r'(?i)(?:\border\s*#?\s*(\d{4,10})\b|#(\d{4,7})\b)')
EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[A-Za-z0-9.-]{1,253}$")


class _RefuseRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise RuntimeError('redirect refused')


def load_shopify_env(path):
    env = {}
    text = Path(path).read_text(encoding='utf-8')
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key.startswith('SHOPIFY_') and value and key not in env:
            env[key] = value
    shop = (env.get('SHOPIFY_SHOP') or '').strip()
    for prefix in ('https://', 'http://'):
        if shop.lower().startswith(prefix):
            shop = shop[len(prefix):]
    shop = shop.split('/', 1)[0].split('?', 1)[0].rstrip('.').lower()
    if not shop.endswith('.myshopify.com') or shop.count('.') < 2:
        raise SystemExit('SHOPIFY_SHOP must be a myshopify.com host')
    if not env.get('SHOPIFY_CLIENT_ID') or not env.get('SHOPIFY_CLIENT_SECRET'):
        raise SystemExit('Shopify client credentials are required')
    env['SHOPIFY_SHOP'] = shop
    env['SHOPIFY_API_VERSION'] = env.get('SHOPIFY_API_VERSION') or '2026-07'
    return env


def parse_order_names(*parts):
    names = []
    for part in parts:
        if not part:
            continue
        for match in ORDER_RE.finditer(str(part)):
            name = match.group(1) or match.group(2)
            if name and name not in names:
                names.append(name)
    return names


def parse_email(value):
    text = (value or '').strip()
    return text if EMAIL_RE.fullmatch(text) else None


def mint_token(env):
    opener = build_opener(_RefuseRedirects())
    body = json.dumps({
        'client_id': env['SHOPIFY_CLIENT_ID'],
        'client_secret': env['SHOPIFY_CLIENT_SECRET'],
        'grant_type': 'client_credentials',
    }).encode()
    request = Request(
        f"https://{env['SHOPIFY_SHOP']}/admin/oauth/access_token",
        data=body,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    with opener.open(request, timeout=30) as response:
        payload = json.loads(response.read().decode())
    token = payload.get('access_token')
    if not isinstance(token, str) or not token:
        raise RuntimeError('token mint failed')
    return token


def graphql(env, token, document, variables):
    if document not in (CUSTOMER_BY_EMAIL, ORDER_BY_NAME, PAST_ORDERS):
        raise RuntimeError('mutations are refused')
    request = Request(
        f"https://{env['SHOPIFY_SHOP']}/admin/api/{env['SHOPIFY_API_VERSION']}/graphql.json",
        data=json.dumps({'query': document, 'variables': variables or {}}).encode(),
        headers={
            'Content-Type': 'application/json',
            'X-Shopify-Access-Token': token,
        },
        method='POST',
    )
    opener = build_opener(_RefuseRedirects())
    with opener.open(request, timeout=30) as response:
        payload = json.loads(response.read().decode())
    if payload.get('errors'):
        raise RuntimeError('graphql errors')
    data = payload.get('data')
    if not isinstance(data, dict):
        raise RuntimeError('graphql empty')
    return data


CUSTOMER_BY_EMAIL = '''
query InboxCustomerByEmail($query: String!) {
  customers(first: 1, query: $query) {
    nodes { id displayName defaultEmailAddress { emailAddress } createdAt numberOfOrders amountSpent { amount currencyCode } tags }
  }
}
'''
ORDER_BY_NAME = '''
query InboxOrderByName($query: String!) {
  orders(first: 1, query: $query) {
    pageInfo { hasNextPage }
    nodes {
      id name email createdAt displayFinancialStatus displayFulfillmentStatus returnStatus discountCodes
      currentTotalPriceSet { shopMoney { amount currencyCode } presentmentMoney { amount currencyCode } }
      billingAddress { name address1 address2 city province zip country }
      shippingAddress { name address1 address2 city province zip country }
      lineItems(first: 50) { pageInfo { hasNextPage } nodes { title sku quantity unfulfilledQuantity originalUnitPriceSet { shopMoney { amount currencyCode } } image { url altText } } }
      fulfillments { displayStatus estimatedDeliveryAt trackingInfo { number url company } fulfillmentLineItems(first: 50) { nodes { quantity lineItem { title } } } }
      returns(first: 5) { pageInfo { hasNextPage } nodes { id name status createdAt totalQuantity
        returnLineItems(first: 25) { pageInfo { hasNextPage } nodes { __typename id quantity returnReasonDefinition { name } returnReasonNote
          ... on ReturnLineItem { fulfillmentLineItem { lineItem { title } } } } }
        exchangeLineItems(first: 5) { nodes { id } } } }
      customer { id defaultEmailAddress { emailAddress } }
    }
  }
}
'''
PAST_ORDERS = '''
query InboxPastOrders($id: ID!) {
  customer(id: $id) {
    orders(first: 50, sortKey: CREATED_AT, reverse: true) {
      pageInfo { hasNextPage }
      nodes { id name createdAt displayFulfillmentStatus currentTotalPriceSet { shopMoney { amount currencyCode } } }
    }
  }
}
'''


def _money(value):
    if not isinstance(value, dict):
        return None
    amount = value.get('amount')
    currency = value.get('currencyCode')
    if isinstance(amount, bool) or amount is None or not isinstance(currency, str) or not re.fullmatch(r'[A-Z]{3}', currency):
        return None
    try:
        parsed = Decimal(str(amount))
    except (InvalidOperation, ValueError):
        return None
    if not parsed.is_finite():
        return None
    return {'amount': str(amount), 'currencyCode': currency}


def _price_set(value):
    bag = value if isinstance(value, dict) else {}
    observed = _money(bag.get('shopMoney'))
    return {'shopMoney': observed} if observed else None


def _page_flag(connection):
    if not isinstance(connection, dict):
        return None
    page_info = connection.get('pageInfo')
    if not isinstance(page_info, dict) or not isinstance(page_info.get('hasNextPage'), bool):
        return None
    return page_info['hasNextPage']


def _clerk_customer(node):
    email = node.get('defaultEmailAddress') if isinstance(node.get('defaultEmailAddress'), dict) else None
    count = node.get('numberOfOrders')
    count = str(count) if not isinstance(count, bool) and count is not None and re.fullmatch(r'\d+', str(count)) else None
    return {
        'id': node.get('id'),
        'displayName': node.get('displayName'),
        'defaultEmailAddress': {'emailAddress': email['emailAddress']} if email and email.get('emailAddress') else None,
        'createdAt': node.get('createdAt'),
        'numberOfOrders': count,
        'amountSpent': _money(node.get('amountSpent')),
        'tags': list(node.get('tags') or []),
        'giftCards': [],
    }


def _clerk_order(node):
    lines = ((node.get('lineItems') or {}).get('nodes') or []) if isinstance(node.get('lineItems'), dict) else []
    fulfillments = []
    for item in node.get('fulfillments') or []:
        if not isinstance(item, dict):
            continue
        fulfillments.append({
            'displayStatus': item.get('displayStatus'),
            'estimatedDeliveryAt': item.get('estimatedDeliveryAt'),
            'trackingInfo': item.get('trackingInfo') or [],
            'fulfillmentLineItems': item.get('fulfillmentLineItems') or {'nodes': []},
        })
    return {
        'id': node.get('id'),
        'name': node.get('name'),
        'createdAt': node.get('createdAt'),
        'displayFinancialStatus': node.get('displayFinancialStatus'),
        'displayFulfillmentStatus': node.get('displayFulfillmentStatus'),
        'currentTotalPriceSet': _price_set(node.get('currentTotalPriceSet')),
        'billingAddress': node.get('billingAddress'),
        'shippingAddress': node.get('shippingAddress'),
        'lineItems': {'nodes': [{
            'title': line.get('title'), 'sku': line.get('sku'), 'quantity': line.get('quantity'),
            'unfulfilledQuantity': line.get('unfulfilledQuantity'),
            'originalUnitPriceSet': _price_set(line.get('originalUnitPriceSet')),
            'image': line.get('image'),
        } for line in lines if isinstance(line, dict)]},
        'fulfillments': fulfillments,
        'discountCodes': list(node.get('discountCodes') or []),
        'invoiceUrl': None, 'warranty': None, 'eta': None, 'shippingZone': None,
    }


def _return_item(line):
    # ReturnLineItemType: the title sits behind the fulfillment line item on
    # verified rows (UnverifiedReturnLineItem carries no fulfillment link).
    fulfillment = line.get('fulfillmentLineItem') if isinstance(line.get('fulfillmentLineItem'), dict) else {}
    linked = fulfillment.get('lineItem') if isinstance(fulfillment.get('lineItem'), dict) else {}
    reason = line.get('returnReasonDefinition') if isinstance(line.get('returnReasonDefinition'), dict) else {}
    return {
        'title': linked.get('title'),
        'quantity': line.get('quantity'),
        'reason': reason.get('name'),
        'note': line.get('returnReasonNote'),
    }


def _clerk_returns(node):
    nodes = []
    connection = node.get('returns') if isinstance(node.get('returns'), dict) else {}
    for item in connection.get('nodes') or []:
        if not isinstance(item, dict):
            continue
        items_connection = item.get('returnLineItems') if isinstance(item.get('returnLineItems'), dict) else {}
        items = [_return_item(line) for line in items_connection.get('nodes') or [] if isinstance(line, dict)]
        # cubic: more line items than the page read is honest data the pane
        # must show, never a silently partial list.
        items_truncated = _page_flag(items_connection)
        exchange_connection = item.get('exchangeLineItems') if isinstance(item.get('exchangeLineItems'), dict) else {}
        exchanges = len(exchange_connection.get('nodes') or [])
        # #46: Gorgias's "return type" — a return with exchange line items is
        # an exchange; without them it is a plain return.
        nodes.append({
            'id': item.get('id'), 'name': item.get('name'), 'status': item.get('status'),
            'createdAt': item.get('createdAt'), 'totalQuantity': item.get('totalQuantity'),
            'returnType': 'EXCHANGE' if exchanges else 'RETURN',
            'items': items, 'itemsTruncated': items_truncated,
        })
    return {
        'orderReturnStatus': node.get('returnStatus') or 'NO_RETURN',
        'returns': {'nodes': nodes},
        'inProgress': any(item.get('status') == 'OPEN' for item in nodes),
    }


def _clerk_history(nodes):
    rows = []
    for node in nodes or []:
        if not isinstance(node, dict) or not node.get('id'):
            continue
        rows.append({
            'id': node['id'], 'name': node.get('name'), 'createdAt': node.get('createdAt'),
            'displayFulfillmentStatus': node.get('displayFulfillmentStatus'),
            'currentTotalPriceSet': _price_set(node.get('currentTotalPriceSet')),
        })
    return rows


def read_projection_tickets(path):
    with closing(projection_connect(path)) as db:
        rows = db.execute('SELECT id, detail FROM tickets ORDER BY observed_at DESC,id').fetchall()
    tickets = []
    for row in rows:
        try:
            ticket = json.loads(row[1])
        except (TypeError, json.JSONDecodeError, ValueError):
            continue
        if isinstance(ticket, dict):
            ticket['id'] = ticket.get('id') or row[0]
            tickets.append(ticket)
    return tickets


def ticket_keys(ticket):
    context = ticket.get('customerContext') or {}
    if context.get('conflict'):
        return None, []
    identity = context.get('identity') or {}
    email = parse_email(identity.get('email') or ticket.get('fromEmail') or ticket.get('customerName'))
    bodies = [ticket.get('subject') or '']
    for message in ticket.get('messages') or []:
        if isinstance(message, dict):
            bodies.append(message.get('body') or '')
    return email, parse_order_names(*bodies)[:3]


def load_cache(path):
    if not Path(path).is_file():
        return {}
    with closing(sqlite3.connect(path)) as db:
        rows = db.execute('SELECT ticket_id, payload, updated_at FROM rail').fetchall()
    cache = {}
    for ticket_id, payload, updated_at in rows:
        try:
            cache[ticket_id] = {'payload': json.loads(payload), 'updated_at': float(updated_at)}
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    return cache


def fresh(entry, now):
    if not entry or (entry.get('payload') or {}).get('payloadVersion') != PAYLOAD_VERSION:
        return False
    if entry['payload'].get('refreshError'):
        return now < entry['payload'].get('retryAt', 0)
    age = now - entry['updated_at']
    status = (entry['payload'] or {}).get('status')
    limit = CACHE_HIT_SECONDS if status == 'found' else CACHE_MISS_SECONDS
    return age < limit


def known_store_mismatch(payload, env):
    observed, configured = payload.get('shop'), env.get('SHOPIFY_SHOP')
    return bool(observed and configured and observed != configured)


class LookupBudgetExceeded(Exception):
    pass


def keys_hash(ticket):
    return hashlib.sha256(json.dumps(ticket_keys(ticket)).encode()).hexdigest()


def lookup_ticket(env, token, ticket, caches):
    def fetch(document, variables):
        if caches['lookups'] >= MAX_LOOKUPS:
            raise LookupBudgetExceeded()
        caches['lookups'] += 1
        return caches['graphql'](env, token, document, variables)

    email, order_names = ticket_keys(ticket)
    customer = None
    order = None
    order_search = False
    page_flags = caches.setdefault('pageInfo', {})
    # Without a consistent ticket identity, never join an order mentioned in text.
    if email:
        key = email.casefold()
        if key not in caches['customers']:
            data = fetch(CUSTOMER_BY_EMAIL, {'query': f'email:"{email}"'})
            nodes = ((data.get('customers') or {}).get('nodes') or [])
            node = nodes[0] if nodes else None
            match = node and ((node.get('defaultEmailAddress') or {}).get('emailAddress') or '').casefold() == key
            caches['customers'][key] = _clerk_customer(node) if match else None
        customer = caches['customers'][key]
        for name in order_names:
            if name not in caches['orders']:
                data = fetch(ORDER_BY_NAME, {'query': f'name:"{name}"'})
                connection = data.get('orders') or {}
                nodes = connection.get('nodes') or []
                caches['orders'][name] = next((node for node in nodes if str(node.get('name', '')).lstrip('#') == name), None)
                page_flags[('order', name)] = _page_flag(connection)
            flag = page_flags.get(('order', name))
            order_search = True if order_search is True or flag is True else None if order_search is None or flag is None else False
            node = caches['orders'][name]
            if not node:
                continue
            owner = node.get('customer') or {}
            owner_email = ((owner.get('defaultEmailAddress') or {}).get('emailAddress') or '').casefold()
            same_owner = customer and owner.get('id') == customer.get('id')
            if same_owner or key in (owner_email, str(node.get('email') or '').casefold()):
                order = node
                break
    customer_id = (customer or {}).get('id')
    history = []
    if customer_id:
        if customer_id not in caches['history']:
            data = fetch(PAST_ORDERS, {'id': customer_id})
            connection = ((data.get('customer') or {}).get('orders') or {})
            caches['history'][customer_id] = _clerk_history(connection.get('nodes'))
            page_flags[('history', customer_id)] = _page_flag(connection)
        history = caches['history'][customer_id]
    payload = {
        'payloadVersion': PAYLOAD_VERSION,
        'status': 'found' if customer or order else 'missing',
        'email': email, 'customerId': customer_id,
        'orderId': (order or {}).get('id'),
        # #48: the store scope is explicit — the snapshot names the one store
        # it was built for (a myshopify.com host, never a credential).
        'shop': env.get('SHOPIFY_SHOP') or None,
        'customer': customer,
        'order': _clerk_order(order) if order else None,
        'returns': _clerk_returns(order) if order else None,
        'history': history,
        'partial': {
            'orderSearch': order_search,
            'orderItems': _page_flag(order.get('lineItems')) if order else False,
            'returns': _page_flag(order.get('returns')) if order else False,
            'history': page_flags.get(('history', customer_id)) if customer_id else False,
        },
        'keysHash': keys_hash(ticket),
    }
    return payload, caches['lookups'] >= MAX_LOOKUPS


def export(projection_path, destination, env_file, *, now=None, graphql_call=None, mint=None):
    now = time.time() if now is None else now
    destination = Path(destination)
    directory = destination.parent
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError('Rail directory must exist')
    tickets = read_projection_tickets(projection_path)
    cache = load_cache(destination)
    env = load_shopify_env(env_file)
    token = None
    caches = {'customers': {}, 'orders': {}, 'history': {}, 'lookups': 0, 'graphql': graphql_call or graphql}
    mint_fn = mint or mint_token
    payloads = {}
    timestamps = {}
    for ticket in tickets:
        ticket_id = ticket.get('id')
        if not ticket_id:
            continue
        entry = cache.get(ticket_id)
        if entry and entry['payload'].get('keysHash') != keys_hash(ticket):
            entry = None
        if entry and known_store_mismatch(entry['payload'], env):
            # A known different store cannot supply any cached details, even
            # while reads fail or the lookup budget is exhausted.
            entry = None
        if entry:
            timestamps[ticket_id] = entry['updated_at']
        if fresh(entry, now):
            payloads[ticket_id] = entry['payload']
            continue
        if caches['lookups'] >= MAX_LOOKUPS:
            if entry:
                payloads[ticket_id] = entry['payload']
            continue
        if token is None:
            token = mint_fn(env)
        try:
            payload, _ = lookup_ticket(env, token, ticket, caches)
            payload['fetchedAt'] = datetime.fromtimestamp(now, timezone.utc).isoformat()
            payload['fetchedAtEpoch'] = now
            timestamps[ticket_id] = now
        except LookupBudgetExceeded:
            if entry:
                payloads[ticket_id] = entry['payload']
            continue
        except (HTTPError, URLError, TimeoutError, RuntimeError, json.JSONDecodeError):
            # cubic: the fallback a failed refresh writes must still name the
            # store scope — every exported snapshot names the one store.
            old = (entry or {}).get('payload')
            payload = dict(shop_rail.display_payload(old)) if old else {
                'status': 'error', 'email': ticket_keys(ticket)[0],
                'keysHash': keys_hash(ticket), 'shop': env.get('SHOPIFY_SHOP') or None}
            payload['shop'] = env.get('SHOPIFY_SHOP') or payload.get('shop')
            # The failed attempt gets its own bounded retry clock. The retained
            # snapshot's observation time and unverified legacy prices stay honest.
            payload.update(payloadVersion=PAYLOAD_VERSION, refreshError=True,
                           retryAt=now + CACHE_MISS_SECONDS)
        payloads[ticket_id] = payload
    fd, name = tempfile.mkstemp(prefix='.shop-rail-', suffix='.sqlite3', dir=directory)
    os.close(fd)
    temporary = Path(name)
    try:
        with closing(sqlite3.connect(temporary)) as db:
            db.execute('PRAGMA journal_mode=DELETE')
            db.execute('PRAGMA synchronous=FULL')
            db.execute('CREATE TABLE rail(ticket_id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL)')
            for ticket_id, payload in payloads.items():
                db.execute('INSERT INTO rail VALUES(?,?,?)', (ticket_id, json.dumps(payload), str(timestamps.get(ticket_id, now))))
            db.commit()
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Invalid shop rail')
        os.chmod(temporary, 0o640)
        with temporary.open('rb') as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        temporary = None
        return {'tickets': len(payloads), 'lookups': caches['lookups'], 'generatedAt': datetime.fromtimestamp(now, timezone.utc).isoformat()}
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--projection', default=os.environ.get('INBOX_PROJECTION_PATH', PROJECTION_PATH))
    parser.add_argument('--destination', default=os.environ.get('SHOP_RAIL_PATH', shop_rail.DEFAULT_PATH))
    # No code default: the operator passes --env-file or SHOP_RAIL_ENV_FILE
    # explicitly (the systemd unit does). Falling back to a hardcoded
    # /root path would break the "no /root traversal" story for local dev
    # and hide a missing-config deployment as a confusing read error.
    parser.add_argument('--env-file', default=os.environ.get('SHOP_RAIL_ENV_FILE', ''))
    args = parser.parse_args()
    if not args.env_file:
        raise SystemExit('SHOP_RAIL_ENV_FILE or --env-file is required')
    print(json.dumps(export(args.projection, args.destination, args.env_file)))
