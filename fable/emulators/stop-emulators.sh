#!/usr/bin/env bash
# Stop the three Fable emulators started by run-emulators.sh.
set -u
for name in shopify redo mailbox; do
  pidfile="/tmp/fable-emu-$name.pid"
  if [ -f "$pidfile" ]; then
    pid="$(cat "$pidfile")"
    if kill "$pid" 2>/dev/null; then echo "stopped $name (pid $pid)"; fi
    rm -f "$pidfile"
  fi
done
# belt-and-suspenders: kill any strays by module path.
# NOTE: the bracket in "app[.]py" makes the regex not match this script's own
# command line (classic self-exclusion), so we never signal our own shell.
pkill -f "emulators/shopify/app[.]py" 2>/dev/null
pkill -f "emulators/redo/app[.]py" 2>/dev/null
pkill -f "emulators/mailbox/app[.]py" 2>/dev/null
echo "done."
