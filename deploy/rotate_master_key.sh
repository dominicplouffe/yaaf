#!/usr/bin/env bash
#
# rotate_master_key.sh — move every stored secret to a new YAAF_MASTER_KEY.
#
# Backs up the database and deploy/.env, stops the api and runner, re-encrypts
# every stored secret under a new key in one transaction, writes the new key
# into deploy/.env, and starts them again. If the re-encryption fails, nothing
# in the database or in deploy/.env changes and the stack is started again on
# the old key.
#
# Keep the old key (the script saves it in deploy/.env.<stamp>.bak): backups
# from before the rotation can only be read with it.
#
# Usage:
#   deploy/rotate_master_key.sh          # asks first
#   deploy/rotate_master_key.sh --yes    # does not ask
#
set -euo pipefail

ASSUME_YES="no"
while [ $# -gt 0 ]; do
  case "$1" in
    --yes|-y) ASSUME_YES="yes"; shift ;;
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
command -v openssl >/dev/null 2>&1 || die "openssl is missing (needed to make the new key)"
[ -n "${YAAF_MASTER_KEY:-}" ] || die "deploy/.env has no YAAF_MASTER_KEY — there is nothing to rotate"
compose ps --status running --services 2>/dev/null | grep -q '^db$' \
  || die "the db container is not running. Start the stack first: deploy/deploy_single_server.sh"
PORT="$(current_port)"; PORT="${PORT:-24247}"

if [ "$ASSUME_YES" != "yes" ]; then
  printf '\n  This stops yaaf for a minute, re-encrypts every stored secret under a new\n'
  printf '  master key, and starts it again. Running agent runs are interrupted and\n'
  printf '  retried.\n\n'
  read -r -p "  Rotate the master key now? [y/N] " answer
  case "$answer" in [yY]*) ;; *) echo "  Nothing changed."; exit 1 ;; esac
fi

stamp="$(date +%Y%m%d-%H%M%S)"

say "Backups"
dump_database "backups/yaaf-${stamp}.sql.gz" || die "pg_dump failed — refusing to rotate without a backup"
cp -p deploy/.env "deploy/.env.${stamp}.bak"
chmod 600 "deploy/.env.${stamp}.bak"
ok "deploy/.env.${stamp}.bak (holds the old key — keep it with the dump above)"

say "Stopping the api and runner"
compose stop api runner >/dev/null
ok "stopped"

say "Re-encrypting"
YAAF_NEW_MASTER_KEY="$(openssl rand -base64 32)"
export YAAF_NEW_MASTER_KEY
# A one-off container: it reads deploy/.env (the old key) like the api does,
# and gets the new key through its environment rather than its command line.
if ! compose run --rm --no-deps -T -e YAAF_NEW_MASTER_KEY api \
       yaaf rotate-master-key --yes; then
  warn "the rotation failed, so nothing changed. Starting yaaf again on the old key."
  compose start api runner >/dev/null
  die "see the message above"
fi

# The database is on the new key from here. Write it where compose reads it.
tmp="$(mktemp deploy/.env.XXXXXX)"
chmod 600 "$tmp"
awk -v key="$YAAF_NEW_MASTER_KEY" \
  '/^YAAF_MASTER_KEY=/ { print "YAAF_MASTER_KEY=" key; next } { print }' \
  deploy/.env > "$tmp"
mv "$tmp" deploy/.env
unset YAAF_NEW_MASTER_KEY
ok "deploy/.env now holds the new key"

say "Starting yaaf"
# up, not start: the containers must be recreated to read the new key.
compose up -d api runner >/dev/null
wait_for_api "$PORT"
ok "yaaf is back"

say "Done"
cat <<EOF

  The master key is rotated. Two things to do now:

  1. Copy the new YAAF_MASTER_KEY from deploy/.env to wherever you keep your
     keys, off this server.
  2. Keep deploy/.env.${stamp}.bak with backups/yaaf-${stamp}.sql.gz. That dump,
     and every older one, can only be read with the old key.

EOF
