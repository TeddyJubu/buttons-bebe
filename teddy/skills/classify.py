"""
classify.py — detects what a customer ticket is about.

Returns one of:
  ORDER_STATUS  — "where is my order / tracking / shipped?"
  RETURN        — "return / refund / exchange / wrong size"
  SHIPPING      — "lost package / wrong address / urgent / how long"
  PRODUCT       — "sizing / materials / care / availability"
  PAYMENT       — "charged twice / billing / payment issue / overcharged"
  COMPLAINT     — "Instagram/TikTok threat / furious / scam / fraud"
  GENERAL       — something recognised but no specific category (still search KB)
  UNKNOWN       — no signals found at all

No LLM call. Pure keyword rules — fast and free.
"""

import re

# ── Keyword sets ──────────────────────────────────────────────────────────────
_ORDER = {
    'track', 'tracking', 'where is my', 'order status', 'shipped', 'dispatch',
    'dispatched', 'transit', 'estimated delivery', 'eta', 'package',
    'not received', 'havent received', "haven't received", 'never arrived',
}

_RETURN = {
    'return', 'refund', 'store credit', 'send back', 'money back',
    'wrong size', 'wrong item', 'exchange', 'wrong order',
    'doesnt fit', "doesn't fit", 'too small', 'too big',
    'cancel order', 'changed my mind',
}

_SHIPPING = {
    'shipping', 'ship', 'how long', 'when will', 'delivery time',
    'address', 'zip code', 'zipcode', 'wrong address', 'change address',
    'lost', 'missing', 'damaged', 'urgent', 'rush', 'faster',
    'express', 'expedite', 'usps', 'ups', 'pickup', 'switch to shipping',
    'switch to pickup',
}

_PRODUCT = {
    'size', 'sizing', 'fit', 'measurement', 'measure',
    'material', 'fabric', 'cotton', 'care', 'wash',
    'color', 'colour', 'stock', 'available', 'availability',
    'sleeve', 'long sleeve', 'short sleeve', 'runs small', 'runs large',
    'true to size', 'how does it run',
}

_GENERAL = {
    'discount', 'promo', 'coupon', 'code', 'first time', 'new customer',
    'promotion', 'sale', 'deal', 'offer',
    'pickup', 'package protection', 'protection', 'gift', 'launch', 'when is',
    'brand', 'new arrival',
}

# Payment / billing issues — charged twice, billing errors, payment disputes
_PAYMENT = {
    'charged twice', 'double charged', 'overcharged', 'charged me twice',
    'two charges', 'duplicate charge', 'duplicate transaction',
    'billing', 'invoice', 'charge', 'transaction',
    'credit card', 'debit card', 'bank statement',
}

# Complaint / public-threat signals — used to give a proper intent label instead
# of UNKNOWN, and to make sure prioritize.py's HIGH signals always get a chance
# to fire even when no other bucket matches.
_COMPLAINT = {
    'instagram', 'tiktok', 'twitter', 'facebook', 'social media',
    'going public', 'post about', 'tell everyone', 'bad review',
    'worst', 'disgusted', 'furious', 'terrible', 'unacceptable',
    'never again', 'scam', 'fraud', 'false advertising',
    'speak to a manager', 'speak to someone', 'escalate',
}


def _tokens(text: str) -> set:
    return set(re.findall(r"[a-z']{3,}", text.lower()))


def _score(text_lower: str, tokens: set, keywords: set) -> int:
    count = 0
    for kw in keywords:
        if ' ' in kw:
            count += 1 if kw in text_lower else 0
        else:
            count += 1 if kw in tokens else 0
    return count


def classify(subject: str, message: str) -> dict:
    """
    classify(subject, message) -> {"intent": str, "confidence": float}
    """
    combined = f"{subject} {message}"
    text_lower = combined.lower()
    tokens = _tokens(text_lower)

    scores = {
        'ORDER_STATUS': _score(text_lower, tokens, _ORDER),
        'RETURN':       _score(text_lower, tokens, _RETURN),
        'SHIPPING':     _score(text_lower, tokens, _SHIPPING),
        'PRODUCT':      _score(text_lower, tokens, _PRODUCT),
        'PAYMENT':      _score(text_lower, tokens, _PAYMENT),
        'GENERAL':      _score(text_lower, tokens, _GENERAL),
        'COMPLAINT':    _score(text_lower, tokens, _COMPLAINT),
    }

    best_intent = max(scores, key=scores.get)
    best_score  = scores[best_intent]

    if best_score == 0:
        return {'intent': 'UNKNOWN', 'confidence': 0.0}

    if best_score >= 3:
        confidence = 0.9
    elif best_score == 2:
        confidence = 0.7
    else:
        confidence = 0.5

    # Penalise ties
    top_two = sorted(scores.values(), reverse=True)[:2]
    if top_two[0] == top_two[1]:
        confidence = max(0.4, confidence - 0.2)

    return {'intent': best_intent, 'confidence': round(confidence, 2)}
