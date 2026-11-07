#!/usr/bin/env bash
#
# backup_single_server.sh — everything this server cannot rebuild, in one file.
#
# The archive holds a pg_dump of the database, deploy/.env (with the master key
# that opens every stored secret), and the artifacts agents produced. Add
# --workspaces to include the agents' working folders too.
#
# The archive holds the master key: it is written readable by you alone. Copy
# it off this server; a backup on the same disk does not survive the disk.
# Restore with deploy/restore_single_server.sh.
#
# Usage:
#   deploy/backup_single_server.sh                      # backups/yaaf-backup-<date>.tar.gz
#   deploy/backup_single_server.sh --out /mnt/nas/yaaf.tar.gz
#   deploy/backup_single_server.sh --workspaces
#
set -euo pipefail

OUT=""; WORKSPACES="no"
while [ $# -gt 0 ]; do
  case "$1" in
    --out) OUT="${2:?--out needs a path}"; shift 2 ;;
    --workspaces) WORKSPACES="yes"; shift ;;
    -h|--help) sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1 (try --help)" >&2; exit 2 ;;
  esac
done

cd "$(dirname "$0")/.."
. deploy/_common.sh

say "Checking where we are"
require_repo_root; ok "in the repository root ($REPO)"
resolve_docker;    ok "docker is usable ($DOCKER)"
load_env;          ok "deploy/.env loaded"
load_stack_env
compose ps --status running --services 2>/dev/null | grep -q '^db$' \
  || die "the db container is not running — start the stack first"

stamp="$(date +%Y%m%d-%H%M%S)"
OUT="${OUT:-backups/yaaf-backup-${stamp}.tar.gz}"
mkdir -p "$(dirname "$OUT")"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

say "Database"
dump_database "$work/yaaf.sql.gz" || die "pg_dump failed"

say "Archive"
cp -p deploy/.env "$work/env"
parts=(artifacts)
[ "$WORKSPACES" = "yes" ] && parts+=(workspaces)
present=(); included=""
for p in "${parts[@]}"; do
  if [ -d "$YAAF_STATE_ROOT/$p" ]; then present+=("$p"); included="$included, $p"; fi
done
# The state folders were made with sudo and are written by the containers, so
# reading them may need it too. The archive is handed back to you either way.
tar_cmd=(tar)
for p in ${present[@]+"${present[@]}"}; do
  [ -r "$YAAF_STATE_ROOT/$p" ] && [ -x "$YAAF_STATE_ROOT/$p" ] || tar_cmd=(sudo tar)
done
( umask 077
  "${tar_cmd[@]}" -czf "$OUT" -C "$work" yaaf.sql.gz env \
    ${present[@]+-C "$YAAF_STATE_ROOT" "${present[@]}"} )
[ "${tar_cmd[0]}" = "sudo" ] && sudo chown "$(id -u):$(id -g)" "$OUT"
chmod 600 "$OUT"
ok "$OUT ($(( $(wc -c < "$OUT") / 1024 )) KB): database, deploy/.env${included}"

say "Done"
cat <<EOF

  This file holds your master key. Store it like a password, and copy it off
  this server:

      scp you@this-server:$REPO/$OUT .

  Restore with: deploy/restore_single_server.sh $OUT

EOF
