#!/usr/bin/env bash
# Start the three Fable emulators (Shopify :9601, Redo :9602, Mailbox :9603).
# Each runs under nohup; logs go to /tmp/fable-emu-*.log. Waits, then health-checks.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON:-python3}"

start() {  # name port module_dir
  local name="$1" port="$2" dir="$3"
  echo "starting $name on :$port ..."
  # launch via absolute path so the process command line is distinct/greppable
  ( nohup "$PY" "$HERE/$dir/app.py" >"/tmp/fable-emu-$name.log" 2>&1 & echo $! >"/tmp/fable-emu-$name.pid" )
}

start shopify 9601 shopify
start redo    9602 redo
start mailbox 9603 mailbox

echo "waiting for services to come up ..."
ok=1
for pair in "shopify:9601" "redo:9602" "mailbox:9603"; do
  name="${pair%%:*}"; port="${pair##*:}"
  up=0
  for _ in $(seq 1 30); do
    if curl -sf "http://127.0.0.1:$port/health" >/dev/null 2>&1; then up=1; break; fi
    sleep 0.5
  done
  if [ "$up" = 1 ]; then
    echo "  OK  $name  $(curl -s http://127.0.0.1:$port/health)"
  else
    echo "  FAIL $name (:$port) — see /tmp/fable-emu-$name.log"; ok=0
  fi
done

[ "$ok" = 1 ] && echo "all emulators healthy." || { echo "one or more emulators failed."; exit 1; }
