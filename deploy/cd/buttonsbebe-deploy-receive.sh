#!/usr/bin/env bash
# Journaled source-file deployment. Per-file replacement is atomic; the whole
# release is NOT atomic. Stop affected readers until apply/rollback completes.
set -Eeuo pipefail
readonly live_root="/root/Buttonsbebe Agent"
readonly releases_root="/opt/buttonsbebe/releases"
readonly backups_root="/opt/buttonsbebe/backups"
readonly web_root="/var/www/console"
readonly inbox_root="/opt/buttonsbebe/inbox"
readonly state_file="/var/lib/buttonsbebe-deploy/source-manifest.json"
readonly approved_config_file="/etc/buttonsbebe-deploy-approved-config.sha256"
readonly source_helper="/usr/local/lib/buttonsbebe-deploy/source_release.py"
readonly max_archive_bytes=$((64 * 1024 * 1024))
readonly readiness_attempts=10
readonly readiness_delay_seconds=3
if [[ $# -ne 2 || ! "$1" =~ ^[0-9a-f]{40}$ || ! "$2" =~ ^[0-9a-f]{64}$ ]]; then
  echo "Usage: buttonsbebe-deploy-receive <commit-sha> <archive-sha256>" >&2
  exit 64
fi
# Covers all entrypoints (GitHub and manual), before staging, backups or stops.
exec 9>/run/lock/buttonsbebe-deploy.lock
flock -n 9 || { echo "Another deployment is in progress." >&2; exit 75; }
readonly release_sha="$1"
readonly expected_digest="$2"
readonly timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
readonly staging_archive="$(mktemp /var/tmp/buttonsbebe-release.XXXXXX.tar.gz)"
readonly backup_root="$backups_root/${release_sha}-${timestamp}"
readonly release_dir="$releases_root/$release_sha"
services=()
active_services=()
active_timers=()
rollback_needed=0
cleanup() { rm -f "$staging_archive"; }
trap cleanup EXIT
install -d -m 0750 "$releases_root" "$backups_root"
[[ -f "$source_helper" ]] || { echo "Install reviewed source helper first." >&2; exit 78; }
if ! LC_ALL=C head -c "$((max_archive_bytes + 1))" >"$staging_archive"; then
  echo "Could not receive the release archive." >&2; exit 65
fi
archive_bytes="$(wc -c <"$staging_archive")"
if ((archive_bytes > max_archive_bytes)); then
  echo "Release archive exceeds the byte limit." >&2; exit 65
fi
actual_digest="$(sha256sum "$staging_archive" | awk '{print $1}')"
[[ "$actual_digest" == "$expected_digest" ]] || { echo "Archive checksum mismatch." >&2; exit 65; }
if ! python3 - "$staging_archive" <<'PY'
import pathlib
import sys
import tarfile

MAX_MEMBER_COUNT = 20_000
MAX_MEMBER_BYTES = 32 * 1024 * 1024
MAX_EXPANDED_BYTES = 256 * 1024 * 1024

member_count = 0
expanded_bytes = 0
seen = set()
with tarfile.open(sys.argv[1], "r|gz") as archive:
    for member in archive:
        member_count += 1
        if member_count > MAX_MEMBER_COUNT:
            raise SystemExit("archive contains too many members")
        path = pathlib.PurePosixPath(member.name)
        if not member.name or member.name.startswith("/") or ".." in path.parts:
            raise SystemExit(f"unsafe archive member: {member.name!r}")
        canonical = str(path)
        if canonical == '.':
            raise SystemExit('archive root entry is not permitted')
        if canonical in seen:
            raise SystemExit(f"duplicate archive member: {member.name!r}")
        seen.add(canonical)
        if not (member.isfile() or member.isdir()):
            raise SystemExit(f"unsupported archive member type: {member.name!r}")
        if member.size < 0 or member.size > MAX_MEMBER_BYTES:
            raise SystemExit(f"archive member is too large: {member.name!r}")
        expanded_bytes += member.size
        if expanded_bytes > MAX_EXPANDED_BYTES:
            raise SystemExit("archive expands beyond the permitted size")
PY
then
  echo "Release archive failed safety validation." >&2
  exit 65
fi

# Never delete/replace an existing immutable release directory.
tmp_release="$(mktemp -d "$releases_root/.${release_sha}.XXXXXX")"
tar --extract --gzip --file "$staging_archive" --directory "$tmp_release" \
  --no-same-owner --no-same-permissions
if [[ -e "$release_dir" ]]; then
  if ! diff -qr "$tmp_release" "$release_dir" >/dev/null; then
    rm -rf "$tmp_release"
    echo "Existing release differs; manual inspection required." >&2; exit 78
  fi
  rm -rf "$tmp_release"
else
  mv "$tmp_release" "$release_dir"
fi
assert_config_approved() {
  local relative_path="$1"
  local expected actual
  if [[ ! -f "$approved_config_file" || ! -d "$release_dir/$relative_path" ]]; then
    echo "Required deployment configuration approval is absent; deploy it manually." >&2
    exit 78
  fi
  expected="$(awk -v path="$relative_path" '$1 == path { print $2; exit }' "$approved_config_file")"
  actual="$(cd "$release_dir/$relative_path" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum | awk '{print $1}')"
  if [[ ! "$expected" =~ ^[0-9a-f]{64}$ || "$actual" != "$expected" ]]; then
    echo "Deployment configuration '$relative_path' changed; deploy it manually before CD can continue." >&2
    exit 78
  fi
}
assert_config_approved deploy/systemd
assert_config_approved deploy/caddy


python3 - "$release_dir/.buttonsbebe-release.json" "$release_sha" <<'PYMETA'
import json, pathlib, sys
metadata = json.loads(pathlib.Path(sys.argv[1]).read_text())
if metadata.get('commit') != sys.argv[2]:
    raise SystemExit('Archive metadata does not match requested verified commit')
PYMETA
# Validate the applied files as well as the reviewed source fingerprints.
# The approval file uses: absolute-path sha256, one protected file per line.
python3 - "$approved_config_file" <<'PYCONFIG'
import hashlib, pathlib, sys
entries = []
for line in pathlib.Path(sys.argv[1]).read_text().splitlines():
    if line.startswith('/'):
        path, expected = line.rsplit(None, 1)
        actual = hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()
        if actual != expected:
            raise SystemExit('Applied configuration drift: ' + path)
        entries.append(path)
required = {'/etc/caddy/sites/support.caddy', '/etc/systemd/system/helpdesk-inbox2.service',
            '/etc/systemd/system/buttonsbebe-inbox-projection.service',
            '/etc/systemd/system/buttonsbebe-inbox-projection.timer'}
if not required.issubset(entries):
    raise SystemExit('Support Caddy, inbox and projection unit applied fingerprints are required')
PYCONFIG
# A root-approved helper owns the exclusions; an incoming archive cannot weaken
# rollback data protection. Dependency changes fail here, before service stops.
python3 "$source_helper" prepare --release "$release_dir" --journal "$backup_root" \
  --live "$live_root" --web "$web_root" --inbox "$inbox_root" --state "$state_file"
service_output="$(python3 "$source_helper" services --journal "$backup_root")"
while IFS= read -r service; do
  [[ -n "$service" ]] && services+=("$service")
done <<< "$service_output"
for service in "${services[@]}"; do
  if systemctl is-active --quiet "$service"; then active_services+=("$service"); fi
done

readiness_ok() (
  trap - ERR
  local service
  for service in "${active_services[@]}"; do
    systemctl is-active --quiet "$service" || return 1
    case "$service" in
      buttonsbebe-webhook)
        curl --fail --silent --show-error --max-time 10 http://127.0.0.1:8000/ready >/dev/null || return 1 ;;
      helpdesk-inbox2)
        curl --fail --silent --show-error --max-time 10 http://127.0.0.1:8767/health |
          python3 -c 'import json,sys; x=json.load(sys.stdin); assert x.get("ok") is True and x.get("readOnly") is True' || return 1
        curl --fail --silent --show-error --max-time 10 -X POST \
          http://127.0.0.1:8767/inbox/api/helpdesk -H 'content-type: application/json' \
          -d '{"tool":"helpdesk.capabilities","arguments":{}}' |
          python3 -c 'import json,sys; x=json.load(sys.stdin); assert x.get("ok") is True and x.get("readOnly") is True and x.get("capabilities",{}).get("sendReply") is False' || return 1 ;;
      buttonsbebe-whatsapp-connect)
        # Connected vs QR/disconnected is business health, not code liveness.
        curl --fail --silent --show-error --max-time 10 http://127.0.0.1:8085/wa/status >/dev/null || return 1 ;;
      buttonsbebe-kb-mcp)
        (cd "$live_root/KB" && timeout 20 ./.venv/bin/python scripts/search_kb.py "size guide" >/dev/null) || return 1 ;;
    esac
  done
)
wait_ready() {
  local attempt
  for ((attempt=1; attempt<=readiness_attempts; attempt++)); do
    if readiness_ok; then return 0; fi
    if ((attempt < readiness_attempts)); then sleep "$readiness_delay_seconds"; fi
  done
  return 1
}
start_active_services() {
  local service attempt api_ready
  for service in "${active_services[@]}"; do
    if [[ "$service" == "buttonsbebe-processor" ]]; then
      api_ready=0
      for ((attempt=1; attempt<=readiness_attempts; attempt++)); do
        if curl --fail --silent --show-error --max-time 10 http://127.0.0.1:8000/ready >/dev/null; then
          api_ready=1; break
        fi
        if ((attempt < readiness_attempts)); then sleep "$readiness_delay_seconds"; fi
      done
      ((api_ready)) || return 1
    fi
    systemctl start "$service" || return 1
  done
  if [[ " ${active_timers[*]} " == *" buttonsbebe-inbox-projection.timer "* ]]; then
    # Its timer stays paused until deployment succeeds. Build a fresh snapshot
    # using the selected source only after canonical schema startup is ready;
    # never activate an exporter that was not already scheduled by the operator.
    api_ready=0
    for ((attempt=1; attempt<=readiness_attempts; attempt++)); do
      if curl --fail --silent --show-error --max-time 10 http://127.0.0.1:8000/ready >/dev/null; then
        api_ready=1; break
      fi
      if ((attempt < readiness_attempts)); then sleep "$readiness_delay_seconds"; fi
    done
    ((api_ready)) || return 1
    systemctl start buttonsbebe-inbox-projection.service || return 1
  fi
}
stop_active_services() {
  local index failed=0
  # Stop the worker before its result API; startup takes the opposite order.
  for ((index=${#active_services[@]}-1; index>=0; index--)); do
    systemctl stop "${active_services[index]}" || failed=1
  done
  ((failed == 0))
}
restore_timers() {
  local timer
  for timer in "${active_timers[@]}"; do systemctl start "$timer" || return 1; done
}
rollback() {
  local service failed=0
  trap - ERR INT TERM
  if ((rollback_needed)); then
    echo "Deployment failed; restoring only journaled source files." >&2
    stop_active_services || failed=1
    if ((failed == 0)) && python3 "$source_helper" rollback --journal "$backup_root"; then
      start_active_services || failed=1
      wait_ready || failed=1
    else
      failed=1
    fi
  fi
  restore_timers || failed=1
  if ((failed)); then
    echo "ROLLBACK INCOMPLETE: operator inspection required; journal retained at $backup_root" >&2
  else
    echo "Prior source restored and previous active services passed readiness." >&2
  fi
  exit 1
}
trap rollback ERR INT TERM
# Do not interrupt an active index/learning transaction. Stop only active timers
# before checking jobs, and restore precisely those timers on either outcome.
if [[ " ${services[*]} " == *" buttonsbebe-kb-mcp "* ]]; then
  for timer in buttonsbebe-kb-sync.timer buttonsbebe-kb-learn.timer buttonsbebe-kb-notices-gc.timer; do
    if systemctl is-active --quiet "$timer"; then
      active_timers+=("$timer")
      systemctl stop "$timer"
    fi
  done
  for service in buttonsbebe-kb-sync.service buttonsbebe-kb-learn.service buttonsbebe-kb-notices-gc.service; do
    if systemctl is-active --quiet "$service"; then
      echo "KB maintenance active; retry deployment after it finishes." >&2
      false
    fi
  done
fi
# The projection exporter reads the same /opt Python tree as the inbox. Pause
# its scheduler before swapping files; never interrupt an in-flight snapshot.
# Also cover webhook-only changes because exporter reads its canonical schema.
if [[ " ${services[*]} " == *" helpdesk-inbox2 "* || " ${services[*]} " == *" buttonsbebe-webhook "* ]]; then
  if systemctl is-active --quiet buttonsbebe-inbox-projection.timer; then
    active_timers+=("buttonsbebe-inbox-projection.timer")
    systemctl stop buttonsbebe-inbox-projection.timer
  fi
  if systemctl is-active --quiet buttonsbebe-inbox-projection.service; then
    echo "Inbox projection active; retry deployment after it finishes." >&2
    false
  fi
fi
rollback_needed=1
stop_active_services
python3 "$source_helper" apply --journal "$backup_root"
start_active_services
wait_ready
restore_timers
python3 "$source_helper" commit --journal "$backup_root" --state "$state_file"
rollback_needed=0
trap - ERR INT TERM
# Retain journals until separately backed up; automatic pruning never deletes recovery evidence.
echo "Deployed Buttons Bebe release $release_sha successfully."
