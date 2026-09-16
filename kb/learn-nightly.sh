#!/usr/bin/env bash
# Nightly learning: promote human-approved console replies into indexed
# exemplars (PII-masked), then rebuild the search index.
set -e
cd "$(dirname "$0")"
# Promotion failures are per-lesson (auto_promote_learned.py exits 1 if any
# lesson fails) and must not skip the reindex — the index would silently go
# stale while approved lessons pile up. Capture the promotion status, always
# run the reindex, then exit with the promotion status so systemd's oneshot
# reports the failure instead of hiding a recurring one.
promote_status=0
./.venv/bin/python scripts/auto_promote_learned.py || promote_status=$?
./.venv/bin/python scripts/index_kb.py
exit "$promote_status"
