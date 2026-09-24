#!/usr/bin/env python3
"""
run_old_system_queries_v3.py — 30 NEW queries through the old gorgias-webhook,
WITH connected Shopify order context.

Shopify is exercised through the real integration path (shopify_lookup ->
shared /root/shopify module), but the module's network calls are monkeypatched
to a fixed mock dataset (qa_v3/fixtures_v3.MOCK_ORDERS) because real Shopify
still 403s (API scopes not granted yet). Nothing hits real Gorgias/Telegram.

Usage:  cd /root/gorgias-webhook && python3 tests/run_old_system_queries_v3.py
"""
import os, sys, json, time

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPT_DIR)
import dotenv_loader
dotenv_loader.load()

os.environ['LLM_PROVIDER']       = 'openai-compatible'
os.environ['LLM_BASE_URL']       = 'https://ollama.com/v1'
os.environ['LLM_MODEL']          = 'deepseek-v4-flash:cloud'
os.environ['LLM_API_KEY']        = os.environ.get('OLLAMA_API_KEY', '')
os.environ['KB_SERVICE_ENABLED'] = '0'
# creds presence so shopify_lookup.is_configured() is True (values unused — patched)
os.environ.setdefault('SHOPIFY_STORE', 'buttons-bebe')
os.environ.setdefault('SHOPIFY_API_KEY', 'mock'); os.environ.setdefault('SHOPIFY_API_SECRET', 'mock')

sys.path.insert(0, SCRIPT_DIR); sys.path.insert(0, '/root/qa_v3'); sys.path.insert(0, '/root')

import fixtures_v3 as F
F.patch_shopify()                       # mock Shopify into the shared module
import classifier, draft_engine, shopify_lookup

_URG = {'immediate': 'IMMEDIATE', 'high': 'HIGH', 'normal': 'NORMAL', 'low': 'LOW'}


def build_order_context(email):
    oc = {"orders": [], "shopify_found": False, "customer_email": email}
    shopify_lookup.enrich_order_context(oc)
    return oc


def run():
    print("\n" + "="*64)
    print("  OLD SYSTEM (gorgias-webhook) x deepseek-v4-flash — v3 + Shopify ctx")
    print("="*64)
    results = []
    for q in F.QUERIES:
        oc = build_order_context(q['email'])
        order_used = bool(oc.get("orders"))
        ctx = {'text': q['message'], 'subject': q['subject'],
               'messages': [{'body_text': q['message'], 'from_agent': False}],
               'order_context': oc}
        clf = classifier.classify(ctx)
        print(f"\n{'-'*64}\n  {q['id']}  {q['label']}")
        print(f"  Classify: {clf.category} | urgency={clf.urgency} | escalate={clf.escalate} | auto_draft={clf.auto_draft_allowed}")
        print(f"  Shopify ctx: order_used={order_used} shopify_live={oc.get('shopify_live')}")
        try:
            res = draft_engine.generate_draft(ctx, clf, top_k=8)
        except Exception as e:
            print(f"  generate_draft ERROR: {e}")
            res = None
        if res is None:
            results.append({'id': q['id'], 'label': q['label'], 'category': clf.category,
                            'urgency': clf.urgency, 'kb_conf': 'none', 'kb_gap': False,
                            'is_escalation': True, 'should_post': False, 'order_used': order_used,
                            'draft': ''})
            continue
        draft = res.draft_text or ''
        results.append({'id': q['id'], 'label': q['label'], 'category': clf.category,
                        'urgency': clf.urgency, 'kb_conf': res.confidence or 'none',
                        'kb_gap': res.kb_gap, 'is_escalation': res.is_escalation,
                        'should_post': res.should_post, 'order_used': order_used,
                        'draft': draft})
        ntype = 'ESCALATION' if res.is_escalation else ('KB-GAP' if res.kb_gap else 'DRAFT-REVIEW')
        print(f"  KB: {res.confidence} ({len(res.kb_sources or [])} src) | gap={res.kb_gap} | esc={res.is_escalation} | type={ntype}")
        print(f"  Draft: {(draft[:200] or '(none)').replace(chr(10),' ')}")

    with open('/root/qa_v3/results_old.json', 'w') as f:
        json.dump(results, f, indent=2)

    print("\n" + "="*64 + "\n  SUMMARY — OLD SYSTEM (v3 + Shopify)\n" + "="*64)
    print(f"  {'ID':<5}{'Label':<40}{'Urg':<11}{'KB':<7}{'Type':<13}{'Ord'}")
    for r in results:
        ntype = 'ESCALATION' if r['is_escalation'] else ('KB-GAP' if r['kb_gap'] else 'DRAFT-REVIEW')
        print(f"  {r['id']:<5}{r['label'][:38]:<40}{_URG.get(r['urgency'], r['urgency']):<11}{r['kb_conf']:<7}{ntype:<13}{'Y' if r['order_used'] else '-'}")
    imm = sum(r['urgency']=='immediate' for r in results); high=sum(r['urgency']=='high' for r in results)
    nor = sum(r['urgency']=='normal' for r in results); low=sum(r['urgency']=='low' for r in results)
    esc = sum(r['is_escalation'] for r in results); gap=sum(r['kb_gap'] for r in results)
    drf = sum(r['should_post'] for r in results); ordu=sum(r['order_used'] for r in results)
    print(f"\n  IMMEDIATE={imm} HIGH={high} NORMAL={nor} LOW={low}")
    print(f"  escalations={esc}  kb_gaps={gap}  draftable={drf}  order_context_used={ordu}/30")
    print("  -> /root/qa_v3/results_old.json")


if __name__ == '__main__':
    run()
