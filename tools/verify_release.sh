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
WEBHOOK_PYTHON="${WEBHOOK_PYTHON:-$PYTHON}"
INBOX_PYTHON="${INBOX_PYTHON:-$WEBHOOK_PYTHON}"
QA_PYTHON="${QA_PYTHON:-$PROCESSOR_PYTHON}"

fail() {
  echo "release gate failed: $*" >&2
  exit 1
}

# Resolve relative executable paths once, before nested suites change cwd.
# Do not realpath Python: resolving a venv symlink loses its environment.
absolute_interpreter() {
  case "$1" in
    /*) printf '%s\n' "$1" ;;
    */*) printf '%s/%s\n' "$ROOT_DIR" "$1" ;;
    *) command -v "$1" ;;
  esac
}
PYTHON="$(absolute_interpreter "$PYTHON")"
PROCESSOR_PYTHON="$(absolute_interpreter "$PROCESSOR_PYTHON")"
WEBHOOK_PYTHON="$(absolute_interpreter "$WEBHOOK_PYTHON")"
INBOX_PYTHON="$(absolute_interpreter "$INBOX_PYTHON")"
QA_PYTHON="$(absolute_interpreter "$QA_PYTHON")"
HERMES_VERIFY_PYTHON="$(absolute_interpreter "${HERMES_VERIFY_PYTHON:-$PYTHON}")"
export PYTHON PROCESSOR_PYTHON WEBHOOK_PYTHON INBOX_PYTHON QA_PYTHON HERMES_VERIFY_PYTHON

cd "$ROOT_DIR"

demo_mode="${DEMO_MODE:-}"
if [[ -n "$demo_mode" && "$demo_mode" != "1" ]]; then
  fail "DEMO_MODE must be unset or 1"
fi

if [[ "$demo_mode" == "1" ]]; then
  demo_config="demo/verify_config.py"
  demo_env="demo/.env.example"
  [[ -f "$demo_config" ]] || fail "missing demo verifier: $demo_config"
  [[ -f "$demo_env" ]] || fail "missing checked-in demo profile: $demo_env"
  "$PYTHON" "$demo_config" "$demo_env" || \
    fail "demo isolation verification failed for $demo_env"
  # DEMO_MODE is a release-gate selector, not test-suite configuration. Keeping
  # it exported would silently switch processor/webhook imports into demo mode.
  unset DEMO_MODE
fi

for required in \
  "processor/pyproject.toml" \
  "processor/uv.lock" \
  "webhook/pyproject.toml" \
  "webhook/uv.lock" \
  "kb/requirements.txt" \
  "kb/requirements.lock" \
  "kb/runtime-constraints.txt" \
  "tools/requirements.txt" \
  "tools/requirements.lock" \
  "tools/runtime-constraints.txt" \
  "testing/requirements-qa.lock" \
  "whatsapp-connect/package.json" \
  "whatsapp-connect/package-lock.json"; do
  [[ -f "$required" ]] || fail "missing dependency manifest: $required"
done

# Keep first-party production Python modules small enough to review. The
# helper owns the path exclusions and fails closed on missing roots,
# traversal/read errors, and an empty production set.
bash tools/check_python_file_sizes.sh
"$PYTHON" tools/sync_console_brand.py --check

"$PYTHON" - <<'PY'
from pathlib import Path
import ast
import json

roots = [Path("feedback"), Path("kb"), Path("processor"), Path("testing"), Path("tools"), Path("webhook"), Path("deploy"), Path("console-src/inbox"), Path("console-src/helpdesk-agent")]

# Installed dependencies are not ours to syntax-check, and checking them made
# the gate's verdict depend on which interpreter happened to run it: a local
# .venv holds 779 third-party files against our 66, and anyio's `match`
# statement is a SyntaxError on the system python3 that this script defaults
# to. `sh tools/verify_release.sh` therefore failed on a clean tree, with a
# traceback pointing at site-packages and nothing to do with the change under
# test. Skipping them is also 13x less work.
SKIP_DIRS = {
    ".venv", "venv", ".env", "env", "node_modules", "__pycache__", ".git",
    "site-packages", "build", "dist", ".mypy_cache", ".pytest_cache",
    ".tox", ".eggs",
}

checked = 0
for root in roots:
    for path in root.rglob("*.py"):
        if SKIP_DIRS & set(path.parts):
            continue
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        checked += 1

# A filter is how a check turns into a no-op. This gate has already shipped
# one that "passed" while checking nothing at all, so the floor is asserted
# rather than trusted: a typo in SKIP_DIRS that eats the whole tree fails
# here instead of printing "release gate passed".
if checked < 40:
    raise SystemExit(
        f"syntax check reached only {checked} files - the skip list is eating "
        f"the source tree"
    )
print(f"syntax: parsed {checked} first-party Python files")

scenarios = json.loads(Path("testing/scenarios.json").read_text(encoding="utf-8"))
if len(scenarios) != 48:
    raise SystemExit(f"testing/scenarios.json must hold 48 scenarios, found {len(scenarios)}")
ids = [s.get("id") for s in scenarios]
if len(set(ids)) != len(ids):
    raise SystemExit("testing/scenarios.json has duplicate scenario ids")

package = json.loads(Path("whatsapp-connect/package.json").read_text(encoding="utf-8"))
lock = json.loads(Path("whatsapp-connect/package-lock.json").read_text(encoding="utf-8"))
if package.get("name") != lock.get("name") or package.get("version") != lock.get("version"):
    raise SystemExit("WhatsApp package.json and package-lock.json metadata differ")
if package.get("dependencies") != lock.get("packages", {}).get("", {}).get("dependencies"):
    raise SystemExit("WhatsApp dependency lock does not match package.json")
PY

# Tracked AND untracked. `git ls-files '*.sh'` alone skipped a new script
# until it was staged - exactly when a local pre-commit run is most useful -
# and silently checked nothing at all outside a git checkout, while the gate
# still printed "release gate passed: ... syntax ...".
shell_scripts=()
while IFS= read -r -d '' script; do
  [[ -f "$script" ]] || continue
  shell_scripts+=("$script")
done < <(git ls-files -z --cached --others --exclude-standard '*.sh') \
  || fail "could not list shell scripts (not a git checkout?)"
[[ ${#shell_scripts[@]} -gt 0 ]] || fail "no shell scripts found - the listing is broken"
echo "release gate: checking ${#shell_scripts[@]} shell scripts"
for script in "${shell_scripts[@]}"; do
  bash -n "$script"
done

# rg exits 0 on a match, 1 on none, 2+ on error - and it is in none of the
# eight manifests above. `if rg ...` treated both "not installed" (127) and
# "bad path" (2) as "clean", so the check could pass without ever running.
command -v rg >/dev/null 2>&1 || fail "ripgrep (rg) is required by this gate"
set +e
rg -n -i 'twilio|twilio_' processor webhook tools kb whatsapp-connect \
    --glob '*.py' --glob '*.js' --glob '*.sh' --glob '!verify_release.sh'
rg_status=$?
set -e
case "$rg_status" in
  0) fail "active Twilio reference found" ;;
  1) ;;  # clean
  *) fail "ripgrep failed with status $rg_status - the Twilio check did not run" ;;
esac

# The feedback and KB suites already replace optional network/vector modules in
# their tests. Keep the requests stub explicit so this gate remains offline.
"$PYTHON" -c 'import sys,types,unittest; requests=types.ModuleType("requests"); requests.get=lambda *a,**k: None; requests.post=lambda *a,**k: None; sys.modules["requests"]=requests; names=["feedback.tests.test_all","feedback.tests.test_retirement"]; suite=unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(n) for n in names); result=unittest.TextTestRunner(verbosity=1).run(suite); raise SystemExit(not result.wasSuccessful())'
"$QA_PYTHON" -m unittest discover -s testing -p 'test_*.py' -v
"$PYTHON" -m unittest discover -s kb/tests -v
"$PYTHON" -m unittest discover -s deploy/tests -v
"$PYTHON" -m unittest discover -s tools -p 'test_*.py' -v
for _test in shopify/test_*.py; do
  [[ -f "$_test" ]] || fail "no Shopify tests discovered"
  "$PYTHON" -m unittest "shopify.$(basename "$_test" .py)" -v
done
# Fresh process per module prevents one suite's fake optional modules leaking
# into another suite. Live diagnostics require an explicit opt-out marker.
webhook_count=0
for _test in webhook/test_*.py; do
  [[ -f "$_test" ]] || continue
  grep -q '^# offline-gate: skip' "$_test" && continue
  PYTHONPATH="$ROOT_DIR/webhook/src${PYTHONPATH:+:$PYTHONPATH}" \
    "$WEBHOOK_PYTHON" -m unittest "webhook.$(basename "$_test" .py)" -v
  webhook_count=$((webhook_count + 1))
done
((webhook_count > 0)) || fail "no webhook tests discovered"
PYTHONPATH="$ROOT_DIR/console-src/helpdesk-agent${PYTHONPATH:+:$PYTHONPATH}" \
  "$INBOX_PYTHON" -m unittest discover -s console-src/helpdesk-agent/tests -v
"$INBOX_PYTHON" -m unittest discover -s console-src/inbox/tests -p 'test_*.py' -v
"$PYTHON" tools/build_support_theme.py --check
"$PYTHON" tools/check_inbox_locks.py
node --test console-src/inbox/test/*.test.js
# Every processor/test_*.py runs, discovered rather than listed, so a new test
# file cannot be added without CI picking it up - and so each task in the Fable
# port does not have to edit this same line (which conflicts on merge).
# test_e2e.py is excluded: it is a live diagnostic script against a running VPS,
# not an offline unit test.
processor_tests=()
for _test in processor/test_*.py; do
  [[ -e "$_test" ]] || continue
  _name="$(basename "$_test" .py)"
  # Live diagnostics opt OUT with a marker line rather than by filename, so
  # renaming or adding one cannot silently pull a VPS-hitting script into the
  # offline gate.
  grep -q '^# offline-gate: skip' "$_test" && continue
  processor_tests+=("processor.$_name")
done
if [[ ${#processor_tests[@]} -eq 0 ]]; then
  fail "no processor test modules found - the discovery glob is wrong"
fi
echo "release gate: processor tests -> ${processor_tests[*]}"
"$PROCESSOR_PYTHON" -m unittest "${processor_tests[@]}" -v

# T-FIX-3 parity is a structural safety gate. Before the split lands there is
# no new package to compare, so keep the pre-split tree explicitly green; once
# processor/classifier/__init__.py exists, the fixed 10,000-case comparison is
# mandatory and uses the processor interpreter's isolated workers.
if [[ -f processor/classifier/__init__.py ]]; then
  echo "release gate: classifier parity -> 10000 synthetic samples"
  "$PROCESSOR_PYTHON" tools/compare_classifier.py \
    --old processor/classifier_shim.py \
    --new processor/classifier/__init__.py \
    --samples 10000
else
  echo "release gate: classifier parity skipped (T-FIX-3 package not present)"
fi

node --check whatsapp-connect/server.js
# test/*.test.js needs only node: builtins, so it runs unconditionally. The
# root test files need the real node_modules (qs), which npm ci owns — running
# them when it is absent must be a visible skip, never a silent pass hiding
# behind a collapsed glob.
node --test whatsapp-connect/test/*.test.js
if [ -d whatsapp-connect/node_modules ]; then
  node --test whatsapp-connect/*.test.js
else
  echo "release gate: whatsapp root tests skipped (no node_modules; CI runs them via npm ci)"
fi
node --test console-src/test/*.test.js
node --check kb-admin/server.js
node --test kb-admin/test/*.test.js

echo "release gate passed: manifests, syntax, offline tests, KB admin safety, WhatsApp auth, and no-Twilio check"
