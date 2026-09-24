"""
kb_router.py — routes KB search requests: keyword-first, semantic fallback.

Strategy
--------
1. Keyword search (fast, zero API cost). If confidence is HIGH or MEDIUM → done.
2. Semantic search (OpenAI embeddings). Only runs when keyword gives LOW or NONE.
3. Return whichever result has higher confidence.

To change the search strategy without touching agent.py, only edit this file.
To disable semantic search entirely, pass llm_client=None or remove the import.

Interface
---------
    from skills.kb_router import search

    result = search(message, kb_dir, llm_client)
    # result: {context: str, files_used: [str], confidence: HIGH|MEDIUM|LOW|NONE}
"""

import logging

from skills.search_kb      import search_kb as _kw_search
from skills.semantic_search import search   as _sem_search

log = logging.getLogger('teddy.kb_router')

_CONF_RANK = {'NONE': 0, 'LOW': 1, 'MEDIUM': 2, 'HIGH': 3}


def search(message: str, kb_dir: str, llm_client=None) -> dict:
    """
    Route KB search: keyword-first with optional semantic fallback.

    llm_client: openai.OpenAI instance for semantic search.
                Pass None to run keyword-only (no API cost, no fallback).
    """
    kw = _kw_search(message, kb_dir)

    if _CONF_RANK[kw['confidence']] >= _CONF_RANK['MEDIUM']:
        log.debug("KB router: keyword sufficient (%s)", kw['confidence'])
        return kw

    if not llm_client:
        return kw

    log.debug("KB router: keyword gave %s — trying semantic fallback", kw['confidence'])
    sem = _sem_search(message, kb_dir, llm_client)

    if _CONF_RANK[sem['confidence']] > _CONF_RANK[kw['confidence']]:
        log.info(
            "KB router: semantic improved %s → %s (files: %s)",
            kw['confidence'], sem['confidence'], sem['files_used'],
        )
        return sem

    return kw
