#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
python3 -m unittest discover -s intake/tests -p 'test_*.py' -v
node --check intake/static/app.js
node --check intake/static/auth.js
node --check intake/static/login.js
node --check intake/static/delivery.js
node --check intake/static/assistance.js
node --check intake/inbox/app.js
node --check intake/inbox/client.js
node --check intake/inbox/icons.js
git diff --check
