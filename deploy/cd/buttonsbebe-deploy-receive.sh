#!/usr/bin/env bash
# Receive a verified release archive from GitHub Actions and atomically update
# the application source while deliberately preserving production data/config.
set -euo pipefail

readonly live_root="/root/Buttonsbebe Agent"
readonly releases_root="/opt/buttonsbebe/releases"
readonly backups_root="/opt/buttonsbebe/backups"
readonly web_root="/var/www/console"
readonly uv_bin="/root/.local/bin/uv"
readonly approved_config_file="/etc/buttonsbebe-deploy-approved-config.sha256"
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
  echo "Deployment failed; restoring the prior application source." >&2
  for service in "${services[@]}"; do
    systemctl stop "$service" 2>/dev/null || true
  done
  rsync -a --delete "$backup_root/source/" "$live_root/"
  if [[ -f "$backup_root/console/index.html" ]]; then
    install -D -m 0644 "$backup_root/console/index.html" "$web_root/index.html"
  fi
  for service in "${services[@]}"; do
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
cat >"$staging_archive"
actual_digest="$(sha256sum "$staging_archive" | awk '{print $1}')"
if [[ "$actual_digest" != "$expected_digest" ]]; then
  echo "Release archive checksum did not match." >&2
  exit 65
fi

if ! python3 - "$staging_archive" <<'PY'
import pathlib
import sys
import tarfile

with tarfile.open(sys.argv[1], "r:gz") as archive:
    for member in archive.getmembers():
        path = pathlib.PurePosixPath(member.name)
        if member.name.startswith("/") or ".." in path.parts or member.isdev():
            raise SystemExit(f"unsafe archive member: {member.name!r}")
        if member.issym() or member.islnk():
            target = pathlib.PurePosixPath(member.linkname)
            if member.linkname.startswith("/") or ".." in target.parts:
                raise SystemExit(f"unsafe archive link: {member.name!r}")
PY
then
  echo "Release archive contains an unsafe path." >&2
  exit 65
fi

tmp_release="$(mktemp -d "$releases_root/.${release_sha}.XXXXXX")"
tar -xzf "$staging_archive" -C "$tmp_release"
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
rsync -a "$live_root/" "$backup_root/source/"
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
  rsync -a --delete "$@" "$release_dir/$source/" "$destination/"
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
for service in "${services[@]}"; do
  systemctl is-active --quiet "$service"
done
curl --fail --silent --show-error --max-time 10 http://127.0.0.1:8000/health >/dev/null
curl --fail --silent --show-error --max-time 10 http://127.0.0.1:8085/wa/status >/dev/null
(cd "$live_root/KB" && ./.venv/bin/python scripts/search_kb.py "size guide" >/dev/null)

rollback_needed=0
echo "Deployed Buttons Bebe release $release_sha successfully."
