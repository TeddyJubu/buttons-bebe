"""Snapshot proposed policy content for isolated QA; never publish or index it.

Run with the existing KB environment (python-frontmatter), then supply the
printed digest to run_live_tests --policy-overlay/--policy-overlay-sha256.
Search ranking still comes from the published index; only already allowlisted
policy/FAQ/intent hits are replaced. Products and customer records are excluded.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import frontmatter
from qa_safety import policy_files


def snapshot(repo):
    sys.path.insert(0, str(repo/'kb/scripts'))
    from sensitivity import is_sensitive_metadata
    records = {}
    for name in sorted(policy_files(repo)):
        post = frontmatter.load(repo/'kb'/name)
        if post.metadata.get('status') != 'confirmed':
            continue
        category = name.split('/')[0]
        if post.metadata.get('category') != category or len(post.content) > 10000:
            raise ValueError('Policy snapshot needs a bounded confirmed category')
        records[name] = dict(file=name, category=category, status='confirmed',
            title=str(post.metadata.get('title', '')), text=post.content,
            sensitive=is_sensitive_metadata(post.metadata), heading='')
    return records


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Use a new snapshot path')
    data = snapshot(Path(__file__).resolve().parent.parent)
    with os.fdopen(os.open(args.output, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600), 'w') as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    print(json.dumps(dict(documents=len(data), sha256=hashlib.sha256(args.output.read_bytes()).hexdigest())))
