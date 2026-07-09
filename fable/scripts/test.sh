#!/usr/bin/env bash
# Fable test runner — one command proves the whole help desk works AND that it can
# never send anything off localhost.
#
#   ./fable/scripts/test.sh            unit + integration (with coverage)
#   FABLE_E2E=1 ./fable/scripts/test.sh   also boots the real 4-service stack
#
# Exits non-zero on any failure. Must be run from the repo root (or anywhere —
# it cd's to the repo root itself).
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FABLE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$FABLE_DIR/.." && pwd)"
cd "$REPO_ROOT"

PY="${PYTHON:-python3}"

# Coverage data on local disk (/tmp): the repo may live on a fuse mount where
# coverage's parallel-file cleanup is not permitted.
export COVERAGE_FILE="${COVERAGE_FILE:-/tmp/fable-cov/.coverage}"
mkdir -p "$(dirname "$COVERAGE_FILE")"

# --- ensure test deps are present -------------------------------------------
need_install=0
$PY -c "import pytest, pytest_cov, coverage" >/dev/null 2>&1 || need_install=1
if [ "$need_install" = 1 ]; then
    echo "== Installing test dependencies (pytest, pytest-cov, coverage, httpx) =="
    $PY -m pip install --break-system-packages -q pytest pytest-cov coverage httpx \
        || $PY -m pip install -q pytest pytest-cov coverage httpx \
        || { echo "FAILED to install test deps" >&2; exit 2; }
fi

# --- unit + integration (with coverage) -------------------------------------
echo "== Running unit + integration tests =="
$PY -m pytest fable/tests/unit fable/tests/integration -q \
    --cov=fable/server/app --cov-report=term
rc=$?
if [ "$rc" -ne 0 ]; then
    echo "UNIT/INTEGRATION TESTS FAILED (rc=$rc)" >&2
    exit "$rc"
fi

# --- optional E2E (live stack) ----------------------------------------------
if [ "${FABLE_E2E:-0}" = "1" ]; then
    echo "== Running E2E live-stack tests (FABLE_E2E=1) =="
    $PY -m pytest fable/tests/e2e -q
    rc=$?
    if [ "$rc" -ne 0 ]; then
        echo "E2E TESTS FAILED (rc=$rc)" >&2
        exit "$rc"
    fi
fi

echo "== All tests passed. =="
exit 0
