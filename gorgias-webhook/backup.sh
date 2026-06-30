#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# backup.sh — Buttons Bebe AI support agent — Stage 5 / Task 17: Backups
#
# Backs up the three things that hold state:
#   1. pgvector DB  (Postgres in Docker `kb-postgres`)  -> backups/pgvector/
#   2. feedback.db  (SQLite, IRREPLACEABLE metrics)      -> backups/feedback-db/
#   3. KB repo      (git bundle --all, self-contained)   -> backups/kb-repo/
#
# Properties:
#   * Idempotent  — safe to run any number of times; artifacts are timestamped.
#   * Error-isolated — one failing step does not skip the others; the script
#     exits non-zero at the end if ANY step failed.
#   * Reversible  — see "RESTORE" notes at the bottom of this file.
#   * Rotation    — keeps the last $KEEP of each kind, deletes older.
#   * Secret-safe — reads POSTGRES_PASSWORD from /root/.env,
#     passes it to the container via env, NEVER prints/echoes/commits it.
#   * Verify      — every artifact is checked: non-empty, gzip integrity
#     (gunzip -t), pg dump header sane, git bundle verifies, sqlite backup
#     re-opens + integrity_check.
#
# Usage:
#   ./backup.sh              run all backups + rotation + verify
#   ./backup.sh --verify     verify the most recent artifact of each kind only
#                            (no new backups taken)
#   KEEP=30 ./backup.sh      override retention count (default 14)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Config ───────────────────────────────────────────────────────────────────
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_ROOT="${BACKUP_ROOT:-$REPO_DIR/backups}"
KEEP="${KEEP:-14}"                      # how many of each kind to retain

PG_CONTAINER="kb-postgres"
PG_DB="kb"
PG_USER="kb"
PG_ENV_FILE="${ROOT_ENV:-/root/.env}"

FEEDBACK_DB="$REPO_DIR/feedback.db"

TS="$(date +%Y%m%d-%H%M%S)"

PG_DIR="$BACKUP_ROOT/pgvector"
FB_DIR="$BACKUP_ROOT/feedback-db"
KB_DIR="$BACKUP_ROOT/kb-repo"

# ── State ────────────────────────────────────────────────────────────────────
declare -a FAILED=()          # names of steps that failed
declare -a SUMMARY=()         # "OK|FAIL  <name>  <detail>" lines

note()  { printf '[backup] %s\n' "$*"; }
warn()  { printf '[backup][WARN] %s\n' "$*" >&2; }
human() { numfmt --to=iec --suffix=B "$1" 2>/dev/null || echo "${1}B"; }

# size of a file in bytes (0 if missing)
fsize() { stat -c '%s' "$1" 2>/dev/null || echo 0; }

# ── Verify helpers ───────────────────────────────────────────────────────────
# returns 0 if file exists and is non-empty
nonempty() { [ -s "$1" ]; }

# ── Step 1: pgvector (pg_dump | gzip via docker exec) ────────────────────────
backup_pgvector() {
  local name="pgvector"
  local out="$PG_DIR/kb-$TS.sql.gz"
  mkdir -p "$PG_DIR"

  if ! command -v docker >/dev/null 2>&1; then
    warn "docker not found; skipping pgvector backup"
    FAILED+=("$name"); SUMMARY+=("FAIL  pgvector       docker not available"); return
  fi
  if ! docker ps --format '{{.Names}}' | grep -qx "$PG_CONTAINER"; then
    warn "container '$PG_CONTAINER' not running; skipping pgvector backup"
    FAILED+=("$name"); SUMMARY+=("FAIL  pgvector       container not running"); return
  fi
  if [ ! -r "$PG_ENV_FILE" ]; then
    warn "PG env file not readable: $PG_ENV_FILE"
    FAILED+=("$name"); SUMMARY+=("FAIL  pgvector       .env not readable"); return
  fi

  # Read the password WITHOUT echoing it. Strip optional surrounding quotes.
  local pgpw
  pgpw="$(grep -E '^POSTGRES_PASSWORD=' "$PG_ENV_FILE" | head -n1 | cut -d= -f2-)"
  pgpw="${pgpw%\"}"; pgpw="${pgpw#\"}"; pgpw="${pgpw%\'}"; pgpw="${pgpw#\'}"
  if [ -z "$pgpw" ]; then
    warn "POSTGRES_PASSWORD empty/missing in $PG_ENV_FILE"
    FAILED+=("$name"); SUMMARY+=("FAIL  pgvector       password missing"); return
  fi

  note "pg_dump $PG_DB (container $PG_CONTAINER) -> $(basename "$out")"
  # PGPASSWORD is set only inside the container env (-e), never on our cmdline,
  # never printed. pg_dump writes plain SQL to stdout -> we gzip on the host.
  if docker exec -e "PGPASSWORD=$pgpw" "$PG_CONTAINER" \
        pg_dump -U "$PG_USER" -d "$PG_DB" 2>/dev/null | gzip -c > "$out"; then
    if nonempty "$out" && gunzip -t "$out" 2>/dev/null \
       && [ "$(gunzip -c "$out" | head -c 200 | grep -c 'PostgreSQL database dump')" -ge 1 ]; then
      SUMMARY+=("OK    pgvector       $(human "$(fsize "$out")")  $(basename "$out")")
    else
      warn "pgvector artifact failed verification: $out"
      FAILED+=("$name"); SUMMARY+=("FAIL  pgvector       artifact bad/empty")
    fi
  else
    warn "pg_dump failed"
    FAILED+=("$name"); SUMMARY+=("FAIL  pgvector       pg_dump errored")
  fi
  unset pgpw
}

# ── Step 2: feedback.db (SQLite online backup -> gzip) ───────────────────────
# Uses Python's sqlite3.Connection.backup() (online-backup API) so the copy is
# transactionally consistent even if server.py is writing. NOT a raw cp.
backup_feedback() {
  local name="feedback-db"
  local out="$FB_DIR/feedback-$TS.sqlite.gz"
  local tmp="$FB_DIR/.feedback-$TS.sqlite.tmp"
  mkdir -p "$FB_DIR"

  if [ ! -r "$FEEDBACK_DB" ]; then
    warn "feedback.db not readable: $FEEDBACK_DB"
    FAILED+=("$name"); SUMMARY+=("FAIL  feedback-db    feedback.db missing"); return
  fi

  note "sqlite online-backup feedback.db -> $(basename "$out")"
  # Online backup to a temp file, verify integrity, then gzip + remove temp.
  if python3 - "$FEEDBACK_DB" "$tmp" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
s = sqlite3.connect(src, timeout=30)
d = sqlite3.connect(dst)
with d:
    s.backup(d)          # online backup API: consistent snapshot
# integrity check on the backup copy
row = d.execute("PRAGMA integrity_check").fetchone()
d.close(); s.close()
if not row or row[0] != "ok":
    print("integrity_check:", row, file=sys.stderr)
    sys.exit(3)
PY
  then
    if nonempty "$tmp" && gzip -c "$tmp" > "$out" && nonempty "$out" && gunzip -t "$out" 2>/dev/null; then
      rm -f "$tmp"
      SUMMARY+=("OK    feedback-db    $(human "$(fsize "$out")")  $(basename "$out")")
    else
      warn "feedback artifact failed to gzip/verify"
      rm -f "$tmp" "$out"
      FAILED+=("$name"); SUMMARY+=("FAIL  feedback-db    gzip/verify failed")
    fi
  else
    warn "sqlite online backup failed (integrity or connect error)"
    rm -f "$tmp"
    FAILED+=("$name"); SUMMARY+=("FAIL  feedback-db    sqlite backup failed")
  fi
}

# ── Step 3: KB repo (git bundle --all) ───────────────────────────────────────
backup_kb_repo() {
  local name="kb-repo"
  local out="$KB_DIR/kb-$TS.bundle"
  mkdir -p "$KB_DIR"

  if ! git -C "$REPO_DIR" rev-parse --git-dir >/dev/null 2>&1; then
    warn "$REPO_DIR is not a git repo; skipping kb-repo bundle"
    FAILED+=("$name"); SUMMARY+=("FAIL  kb-repo        not a git repo"); return
  fi

  note "git bundle create --all -> $(basename "$out")"
  # --all bundles every ref (full self-contained mirror incl. kb/ history).
  if git -C "$REPO_DIR" bundle create "$out" --all >/dev/null 2>&1; then
    if nonempty "$out" && git -C "$REPO_DIR" bundle verify "$out" >/dev/null 2>&1; then
      SUMMARY+=("OK    kb-repo        $(human "$(fsize "$out")")  $(basename "$out")")
    else
      warn "kb-repo bundle failed verification: $out"
      FAILED+=("$name"); SUMMARY+=("FAIL  kb-repo        bundle verify failed")
    fi
  else
    warn "git bundle create failed"
    FAILED+=("$name"); SUMMARY+=("FAIL  kb-repo        bundle create failed")
  fi
}

# ── Rotation: keep newest $KEEP, delete older ───────────────────────────────
rotate() {
  local dir="$1" glob="$2" label="$3"
  [ -d "$dir" ] || return 0
  # List matching files newest-first; delete everything past $KEEP.
  local files
  mapfile -t files < <(ls -1t "$dir"/$glob 2>/dev/null || true)
  local n=${#files[@]}
  if [ "$n" -gt "$KEEP" ]; then
    local i
    for ((i=KEEP; i<n; i++)); do
      note "rotate: removing old $label backup $(basename "${files[$i]}")"
      rm -f "${files[$i]}"
    done
  fi
}

# ── --verify only: re-verify the most recent artifact of each kind ──────────
verify_latest() {
  local rc=0
  echo "── verify-only: checking most recent artifact of each kind ──"

  local pg; pg="$(ls -1t "$PG_DIR"/kb-*.sql.gz 2>/dev/null | head -n1 || true)"
  if [ -n "$pg" ] && nonempty "$pg" && gunzip -t "$pg" 2>/dev/null \
     && [ "$(gunzip -c "$pg" | head -c 200 | grep -c 'PostgreSQL database dump')" -ge 1 ]; then
    echo "OK    pgvector     $(human "$(fsize "$pg")")  $(basename "$pg")"
  else echo "FAIL  pgvector     (none or bad)"; rc=1; fi

  local fb; fb="$(ls -1t "$FB_DIR"/feedback-*.sqlite.gz 2>/dev/null | head -n1 || true)"
  if [ -n "$fb" ] && nonempty "$fb" && gunzip -t "$fb" 2>/dev/null; then
    echo "OK    feedback-db  $(human "$(fsize "$fb")")  $(basename "$fb")"
  else echo "FAIL  feedback-db  (none or bad)"; rc=1; fi

  local kb; kb="$(ls -1t "$KB_DIR"/kb-*.bundle 2>/dev/null | head -n1 || true)"
  if [ -n "$kb" ] && nonempty "$kb" && git -C "$REPO_DIR" bundle verify "$kb" >/dev/null 2>&1; then
    echo "OK    kb-repo      $(human "$(fsize "$kb")")  $(basename "$kb")"
  else echo "FAIL  kb-repo      (none or bad)"; rc=1; fi

  return $rc
}

# ── Main ─────────────────────────────────────────────────────────────────────
main() {
  if [ "${1:-}" = "--verify" ]; then
    verify_latest
    exit $?
  fi

  note "starting backups  (root=$BACKUP_ROOT  keep=$KEEP  ts=$TS)"
  mkdir -p "$BACKUP_ROOT"

  # Each step is wrapped so a failure inside one (despite set -e) is captured
  # and does NOT abort the others. We temporarily relax -e around each call.
  set +e
  backup_feedback      # most important first
  backup_pgvector
  backup_kb_repo
  set -e

  # Rotation per kind (independent; never fatal).
  rotate "$FB_DIR" 'feedback-*.sqlite.gz' feedback-db || true
  rotate "$PG_DIR" 'kb-*.sql.gz'          pgvector    || true
  rotate "$KB_DIR" 'kb-*.bundle'          kb-repo     || true

  echo
  echo "──────────────── backup summary ($TS) ────────────────"
  printf '%s\n' "${SUMMARY[@]}"
  echo "retention: keep last $KEEP of each kind in $BACKUP_ROOT"
  echo "current counts:"
  printf '  feedback-db : %s\n' "$(ls -1 "$FB_DIR"/feedback-*.sqlite.gz 2>/dev/null | wc -l)"
  printf '  pgvector    : %s\n' "$(ls -1 "$PG_DIR"/kb-*.sql.gz 2>/dev/null | wc -l)"
  printf '  kb-repo     : %s\n' "$(ls -1 "$KB_DIR"/kb-*.bundle 2>/dev/null | wc -l)"
  echo "───────────────────────────────────────────────────────"

  if [ "${#FAILED[@]}" -gt 0 ]; then
    warn "completed with failures: ${FAILED[*]}"
    exit 1
  fi
  note "all backups completed successfully"
  exit 0
}

main "$@"

# ─────────────────────────────────────────────────────────────────────────────
# RESTORE (reversible — for the record; not run by this script):
#
#   pgvector:
#     gunzip -c backups/pgvector/kb-YYYYMMDD-HHMMSS.sql.gz \
#       | docker exec -i -e PGPASSWORD="$PW" kb-postgres psql -U kb -d kb
#     (or rebuild from KB: python ingestion_worker.py full)
#
#   feedback.db (stop the webhook first so nothing is writing):
#     gunzip -c backups/feedback-db/feedback-YYYYMMDD-HHMMSS.sqlite.gz \
#       > feedback.db
#
#   KB repo (clone the self-contained mirror):
#     git clone backups/kb-repo/kb-YYYYMMDD-HHMMSS.bundle restored-repo
#     # or fetch into an existing repo:
#     git fetch backups/kb-repo/kb-YYYYMMDD-HHMMSS.bundle '*:*'
# ─────────────────────────────────────────────────────────────────────────────
