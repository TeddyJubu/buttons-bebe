#!/usr/bin/env bash
# Nightly learning: promote human-approved console replies into indexed
# exemplars (PII-masked), then rebuild the search index.
set -e
cd "$(dirname "$0")"
# Promotion failures are per-lesson (auto_promote_learned.py exits 1 if any
# lesson fails) and must not skip the reindex — the index would silently go
# stale while approved lessons pile up. systemd reports the failure via the
# oneshot unit's result; the reindex below still runs.
./.venv/bin/python scripts/auto_promote_learned.py || true
./.venv/bin/python scripts/index_kb.py
