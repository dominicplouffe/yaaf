#!/usr/bin/env bash
#
# restore_single_server.sh — put a server back to a backup.
#
# Takes an archive made by backup_single_server.sh and restores the database,
# deploy/.env (with the master key that matches it) and the artifacts. Before
# it changes anything it saves what is there now: a dump in backups/, the
# current deploy/.env beside it, and the current artifacts folder renamed.
#
# Usage:
#   deploy/restore_single_server.sh backups/yaaf-backup-<date>.tar.gz
#   deploy/restore_single_server.sh <file> --yes      # does not ask
#
set -euo pipefail

ARCHIVE=""; ASSUME_YES="no"
while [ $# -gt 0 ]; do
  case "$1" in
    --yes|-y) ASSUME_YES="yes"; shift ;;
    -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "unknown argument: $1 (try --help)" >&2; exit 2 ;;
    *) ARCHIVE="$1"; shift ;;
  esac
done
[ -n "$ARCHIVE" ] || { echo "usage: $0 <backup file> [--yes]" >&2; exit 2; }
ARCHIVE="$(cd "$(dirname "$ARCHIVE")" && pwd)/$(basename "$ARCHIVE")"

cd "$(dirname "$0")/.."
. deploy/_common.sh

say "Checking where we are"
require_repo_root; ok "in the repository root ($REPO)"
resolve_docker;    ok "docker is usable ($DOCKER)"
[ -f "$ARCHIVE" ] || die "no such file: $ARCHIVE"
contents="$(tar -tzf "$ARCHIVE")" || die "$ARCHIVE is not a readable .tar.gz"
printf '%s\n' "$contents" | grep -qx 'yaaf.sql.gz' && printf '%s\n' "$contents" | grep -qx 'env' \
  || die "$ARCHIVE is not a yaaf backup (no yaaf.sql.gz or env inside)"
ok "$ARCHIVE is a yaaf backup"
load_stack_env
compose ps --status running --services 2>/dev/null | grep -q '^db$' \
  || die "the db container is not running. Start it: (cd deploy && docker compose up -d db)"
PORT="$(current_port)"; PORT="${PORT:-24247}"

if [ "$ASSUME_YES" != "yes" ]; then
  printf '\n  This replaces the database, deploy/.env and the artifacts with the backup.\n'
  printf '  What is there now is saved first. yaaf stops for a few minutes.\n\n'
  read -r -p "  Restore now? [y/N] " answer
  case "$answer" in [yY]*) ;; *) echo "  Nothing changed."; exit 1 ;; esac
fi

stamp="$(date +%Y%m%d-%H%M%S)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
tar -xzf "$ARCHIVE" -C "$work" yaaf.sql.gz env

say "Saving what is there now"
if [ -f deploy/.env ]; then
  cp -p deploy/.env "deploy/.env.before-restore-${stamp}"
  ok "deploy/.env.before-restore-${stamp}"
fi
dump_database "backups/yaaf-before-restore-${stamp}.sql.gz" \
  || die "could not dump the current database — refusing to replace it"

say "Stopping the api and runner"
compose stop api runner >/dev/null
ok "stopped"

say "Database"
compose exec -T db dropdb -U yaaf --if-exists yaaf
compose exec -T db createdb -U yaaf -O yaaf yaaf
gunzip -c "$work/yaaf.sql.gz" | compose exec -T db psql -q -U yaaf -d yaaf -v ON_ERROR_STOP=1 >/dev/null \
  || die "the restore failed part way. The database you had is in backups/yaaf-before-restore-${stamp}.sql.gz"
ok "restored"

say "deploy/.env"
install -m 600 "$work/env" deploy/.env
ok "restored (the master key now matches the restored database)"

if printf '%s\n' "$contents" | grep -q '^artifacts/'; then
  say "Artifacts"
  as_needed=""
  [ -w "$YAAF_STATE_ROOT" ] || as_needed="sudo"
  if [ -d "$YAAF_STATE_ROOT/artifacts" ]; then
    $as_needed mv "$YAAF_STATE_ROOT/artifacts" "$YAAF_STATE_ROOT/artifacts.before-restore-${stamp}"
    ok "the current artifacts are in $YAAF_STATE_ROOT/artifacts.before-restore-${stamp}"
  fi
  $as_needed tar -xzf "$ARCHIVE" -C "$YAAF_STATE_ROOT" artifacts
  ok "restored"
fi
if printf '%s\n' "$contents" | grep -q '^workspaces/'; then
  warn "the backup also holds workspaces; they were not restored over the live ones."
  warn "To restore them: sudo tar -xzf $ARCHIVE -C $YAAF_STATE_ROOT workspaces"
fi

say "Starting yaaf"
# up, not start: the containers must be recreated to read the restored .env.
compose up -d api runner >/dev/null
wait_for_api "$PORT"
wait_for_schema 300 || die "the restored database did not reach the current schema. Look at: (cd deploy && $DOCKER compose logs api)"
ok "yaaf is back, on the restored data"
