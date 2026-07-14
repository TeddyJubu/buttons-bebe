#!/usr/bin/env bash
# Offline release gate for the live Buttons Bebe source tree.
#
# This script deliberately does not install dependencies, start services, call
# external APIs, or mutate a VPS. CI installs the declared manifests first;
# local callers should point PYTHON/PROCESSOR_PYTHON at an already prepared env.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
PROCESSOR_PYTHON="${PROCESSOR_PYTHON:-$PYTHON}"

fail() {
  echo "release gate failed: $*" >&2
  exit 1
}

cd "$ROOT_DIR"

for required in \
  "processor/pyproject.toml" \
  "processor/uv.lock" \
  "webhook/pyproject.toml" \
  "webhook/uv.lock" \
  "kb/requirements.txt" \
  "tools/requirements.txt" \
  "whatsapp-connect/package.json" \
  "whatsapp-connect/package-lock.json"; do
  [[ -f "$required" ]] || fail "missing dependency manifest: $required"
done

"$PYTHON" - <<'PY'
from pathlib import Path
import ast
import json

roots = [Path("feedback"), Path("kb"), Path("processor"), Path("tools"), Path("webhook"), Path("deploy")]
for root in roots:
    for path in root.rglob("*.py"):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

package = json.loads(Path("whatsapp-connect/package.json").read_text(encoding="utf-8"))
lock = json.loads(Path("whatsapp-connect/package-lock.json").read_text(encoding="utf-8"))
if package.get("name") != lock.get("name") or package.get("version") != lock.get("version"):
    raise SystemExit("WhatsApp package.json and package-lock.json metadata differ")
if package.get("dependencies") != lock.get("packages", {}).get("", {}).get("dependencies"):
    raise SystemExit("WhatsApp dependency lock does not match package.json")
PY

for script in $(git ls-files '*.sh'); do
  bash -n "$script"
done

if rg -n -i 'twilio|twilio_' processor webhook tools kb whatsapp-connect \
    --glob '*.py' --glob '*.js' --glob '*.sh' --glob '!verify_release.sh'; then
  fail "active Twilio reference found"
fi

# The feedback and KB suites already replace optional network/vector modules in
# their tests. Keep the requests stub explicit so this gate remains offline.
"$PYTHON" -c 'import sys,types,unittest; requests=types.ModuleType("requests"); requests.get=lambda *a,**k: None; requests.post=lambda *a,**k: None; sys.modules["requests"]=requests; names=["feedback.tests.test_all","feedback.tests.test_retirement"]; suite=unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(n) for n in names); result=unittest.TextTestRunner(verbosity=1).run(suite); raise SystemExit(not result.wasSuccessful())'
"$PYTHON" -m unittest discover -s kb/tests -v
"$PYTHON" -m unittest discover -s deploy/tests -v
"$PYTHON" -m unittest tools.test_tool_contracts -v
PYTHONPATH="$ROOT_DIR/webhook/src${PYTHONPATH:+:$PYTHONPATH}" \
  "$PYTHON" -m unittest discover -s webhook -p 'test_notifications.py' -v
if "$PYTHON" -c 'import aiosqlite' >/dev/null 2>&1; then
  PYTHONPATH="$ROOT_DIR/webhook/src${PYTHONPATH:+:$PYTHONPATH}" \
    "$PYTHON" -m unittest discover -s webhook -p 'test_notification_api.py' -v
fi
"$PROCESSOR_PYTHON" -m unittest \
  processor.test_whatsapp_notifier \
  processor.test_feedback_retirement \
  processor.test_hermes_readonly_prompt \
  -v

node --check whatsapp-connect/server.js
node --test whatsapp-connect/test/security.test.js
node --check kb-admin/server.js
node --test kb-admin/test/server.test.js

echo "release gate passed: manifests, syntax, offline tests, KB admin safety, WhatsApp auth, and no-Twilio check"
