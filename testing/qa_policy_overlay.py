"""Fail-closed, hash-pinned proposed KB overlay for the isolated QA proxy."""
import hashlib
import json
from pathlib import Path
import re
from qa_safety import filter_policy_results, policy_files


def load_overlay(path, digest, repo):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 500000:
        raise ValueError('Invalid policy snapshot')
    raw = path.read_bytes()
    if not digest or hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError('Policy snapshot digest mismatch')
    rows = json.loads(raw)
    allowed = policy_files(repo)
    if not isinstance(rows, dict) or not rows or not set(rows).issubset(allowed):
        raise ValueError('Unexpected policy snapshot paths')
    safe, dropped = filter_policy_results(list(rows.values()), allowed)
    if dropped or len(safe) != len(rows) or {r['file'] for r in safe} != set(rows):
        raise ValueError('Policy snapshot must contain confirmed policies only')
    return {r['file']: r for r in safe}


def replace_hits(rows, overlay):
    """Replace only returned, already filtered hits. Preserve search ordering."""
    output = []
    for row in rows:
        replacement = overlay.get(row['file'])
        if not replacement:
            output.append(row)
            continue
        # Match unchanged headings to avoid flooding the model with irrelevant
        # policy sections. Renamed/removed headings use the bounded whole article.
        sections = re.split(r'^##\s+(.+?)\s*$', replacement['text'], flags=re.M)
        matching = next((sections[i+1].strip() for i in range(1, len(sections)-1, 2)
                         if sections[i] == row.get('heading')), None)
        output.append({**replacement, 'text': matching or replacement['text'],
                       'heading': row.get('heading', '') if matching else ''})
    return output
