#!/usr/bin/env bash
# Receive a verified release archive from GitHub Actions and atomically update
# the application source while deliberately preserving production data/config.
set -Eeuo pipefail

readonly live_root="/root/Buttonsbebe Agent"
readonly releases_root="/opt/buttonsbebe/releases"
readonly backups_root="/opt/buttonsbebe/backups"
readonly web_root="/var/www/console"
readonly uv_bin="/root/.local/bin/uv"
readonly approved_config_file="/etc/buttonsbebe-deploy-approved-config.sha256"
readonly max_archive_bytes=$((64 * 1024 * 1024))
readonly retention_count=5
readonly readiness_attempts=10
readonly readiness_delay_seconds=3
readonly services=(
  buttonsbebe-webhook
  buttonsbebe-processor
  buttonsbebe-kb-mcp
  buttonsbebe-gorgias-mcp
  buttonsbebe-redo-mcp
  buttonsbebe-kb-admin
  buttonsbebe-whatsapp-connect
)
readonly maintenance_services=(
  buttonsbebe-kb-sync.service
  buttonsbebe-kb-learn.service
  buttonsbebe-kb-notices-gc.service
)
readonly maintenance_timers=(
  buttonsbebe-kb-sync.timer
  buttonsbebe-kb-learn.timer
  buttonsbebe-kb-notices-gc.timer
)

if [[ $# -ne 2 || ! "$1" =~ ^[0-9a-f]{40}$ || ! "$2" =~ ^[0-9a-f]{64}$ ]]; then
  echo "Usage: buttonsbebe-deploy-receive <commit-sha> <archive-sha256>" >&2
  exit 64
fi

readonly release_sha="$1"
readonly expected_digest="$2"
readonly timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
readonly staging_archive="$(mktemp /var/tmp/buttonsbebe-release.XXXXXX.tar.gz)"
readonly backup_root="$backups_root/${release_sha}-${timestamp}"
readonly release_dir="$releases_root/$release_sha"
rollback_needed=0

cleanup() {
  rm -f "$staging_archive"
}
trap cleanup EXIT

rollback() {
  local service timer
  [[ "$rollback_needed" -eq 1 && -d "$backup_root/source" ]] || return 0
  trap - ERR
  echo "Deployment failed; restoring the prior application source." >&2
  for service in "${services[@]}"; do
    systemctl stop "$service" 2>/dev/null || true
  done
  rsync -a --delete --no-specials --no-devices "$backup_root/source/" "$live_root/"
  if [[ -f "$backup_root/console/index.html" ]]; then
    install -D -m 0644 "$backup_root/console/index.html" "$web_root/index.html"
  fi
  for service in "${services[@]}"; do
    systemctl start "$service" 2>/dev/null || true
  done
  for service in "${maintenance_services[@]}"; do
    systemctl start "$service" 2>/dev/null || true
  done
  for timer in "${maintenance_timers[@]}"; do
    systemctl start "$timer" 2>/dev/null || true
  done
}
trap rollback ERR

install -d -m 0750 "$releases_root" "$backups_root"
if [[ ! -x "$uv_bin" ]]; then
  echo "Required locked-environment tool is unavailable: $uv_bin" >&2
  exit 69
fi
if ! LC_ALL=C head -c "$((max_archive_bytes + 1))" >"$staging_archive"; then
  echo "Could not receive the release archive." >&2
  exit 65
fi
archive_bytes="$(wc -c <"$staging_archive")"
if ((archive_bytes > max_archive_bytes)); then
  echo "Release archive exceeds the ${max_archive_bytes}-byte limit." >&2
  exit 65
fi
actual_digest="$(sha256sum "$staging_archive" | awk '{print $1}')"
if [[ "$actual_digest" != "$expected_digest" ]]; then
  echo "Release archive checksum did not match." >&2
  exit 65
fi

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
        if member.name in seen:
            raise SystemExit(f"duplicate archive member: {member.name!r}")
        seen.add(member.name)
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

tmp_release="$(mktemp -d "$releases_root/.${release_sha}.XXXXXX")"
tar --extract --gzip --file "$staging_archive" --directory "$tmp_release" \
  --no-same-owner --no-same-permissions
rm -rf "$release_dir"
mv "$tmp_release" "$release_dir"

# System configuration contains credentials and is intentionally not part of
# release archives. Root approves its source fingerprint only after manually
# applying a configuration change; all other releases then remain automatic.
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

install -d -m 0700 "$backup_root/source" "$backup_root/console"
rsync -a --no-specials --no-devices "$live_root/" "$backup_root/source/"
if [[ -f "$web_root/index.html" ]]; then
  cp -p "$web_root/index.html" "$backup_root/console/index.html"
fi
rollback_needed=1

for timer in "${maintenance_timers[@]}"; do
  systemctl stop "$timer"
done
for service in "${maintenance_services[@]}"; do
  systemctl stop "$service"
done
for service in "${services[@]}"; do
  systemctl stop "$service"
done

sync_source() {
  local source="$1"
  local destination="$2"
  shift 2
  install -d "$destination"
  rsync -a --delete --no-specials --no-devices "$@" "$release_dir/$source/" "$destination/"
}

sync_source webhook "$live_root/webhook" \
  --exclude '.env' --exclude '.venv/' --exclude '__pycache__/' \
  --exclude 'data/' --exclude 'logs/'
sync_source processor "$live_root/processor" \
  --exclude '.env' --exclude '.venv/' --exclude '__pycache__/' \
  --exclude 'data/' --exclude 'logs/'
sync_source tools "$live_root/tools" \
  --exclude '.env' --exclude '.venv/' --exclude '__pycache__/' --exclude 'logs/'
sync_source whatsapp-connect "$live_root/whatsapp-connect" \
  --exclude '.env' --exclude 'auth/' --exclude 'node_modules/' --exclude '.wwebjs_auth/' \
  --exclude '.wwebjs_cache/' --exclude 'logs/'
sync_source kb "$live_root/KB" \
  --exclude '.env' --exclude '.venv/' --exclude '__pycache__/' \
  --exclude 'lancedb/' --exclude 'products/' --exclude 'learned/' \
  --exclude 'notices/' --exclude 'archive/' --exclude '_archive_learned/' \
  --exclude 'logs/'
sync_source kb-admin "$live_root/kb-admin" \
  --exclude '.env' --exclude '.venv/' --exclude '__pycache__/' \
  --exclude 'node_modules/' --exclude 'data/' --exclude 'logs/'
install -D -m 0644 "$release_dir/console-src/index.html" "$web_root/index.html"

(cd "$live_root/webhook" && "$uv_bin" sync --locked)
(cd "$live_root/processor" && "$uv_bin" sync --locked)
"$live_root/KB/.venv/bin/pip" install -r "$live_root/KB/requirements.txt"
"$live_root/tools/.venv/bin/pip" install -r "$live_root/tools/requirements.txt"
(cd "$live_root/whatsapp-connect" && npm ci --omit=dev)
(cd "$live_root/KB" && timeout 600 ./.venv/bin/python scripts/index_kb.py)

for service in "${services[@]}"; do
  systemctl start "$service"
done
for timer in "${maintenance_timers[@]}"; do
  systemctl start "$timer"
done

readiness_ok() {
  local service whatsapp_state
  for service in "${services[@]}"; do
    systemctl is-active --quiet "$service" || return 1
  done
  curl --fail --silent --show-error --max-time 10 \
    http://127.0.0.1:8000/health >/dev/null || return 1
  whatsapp_state="$(
    curl --fail --silent --show-error --max-time 10 http://127.0.0.1:8085/wa/status |
      python3 -c 'import json, sys; print(json.load(sys.stdin).get("state", ""))'
  )" || return 1
  [[ "$whatsapp_state" == "connected" ]] || return 1
  (cd "$live_root/KB" && \
    ./.venv/bin/python scripts/search_kb.py "size guide" >/dev/null) || return 1
}

ready=0
for ((attempt = 1; attempt <= readiness_attempts; attempt++)); do
  if readiness_ok; then
    ready=1
    break
  fi
  if ((attempt < readiness_attempts)); then
    sleep "$readiness_delay_seconds"
  fi
done
if ((ready == 0)); then
  echo "Services did not become ready after $readiness_attempts attempts." >&2
  false
fi

prune_old_directories() {
  local root="$1"
  local protected="$2"
  python3 - "$root" "$retention_count" "$protected" <<'PY'
import pathlib
import shutil
import sys

root = pathlib.Path(sys.argv[1])
limit = int(sys.argv[2])
protected = pathlib.Path(sys.argv[3])
directories = sorted(
    (path for path in root.iterdir() if path.is_dir() and not path.is_symlink()),
    key=lambda path: path.stat().st_mtime_ns,
    reverse=True,
)
kept = 0
for path in directories:
    if path == protected or kept < limit:
        kept += 1
        continue
    shutil.rmtree(path)
PY
}

prune_old_directories "$releases_root" "$release_dir"
prune_old_directories "$backups_root" "$backup_root"

rollback_needed=0
echo "Deployed Buttons Bebe release $release_sha successfully."
