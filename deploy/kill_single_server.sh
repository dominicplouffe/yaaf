#!/usr/bin/env bash
#
# kill_single_server.sh — stop yaaf on this box.
#
# By default this stops and removes the containers but KEEPS your data: the
# Postgres volume, deploy/.env, workspaces and artifacts all survive, so
# deploy/deploy_single_server.sh brings the same project back.
#
# It also removes the sandbox containers the runner launched. Those are
# siblings, not compose services, so `docker compose down` leaves them behind —
# which is how a long-running box accumulates dozens of exited containers.
# That sweep is BOX-WIDE, not scoped to one compose project: it matches every
# yaaf sandbox on the host, which is right for a single-server deployment and
# worth knowing if you ever run two stacks side by side (--keep-sandboxes
# opts out).
#
# Usage:
#   deploy/kill_single_server.sh                    # stop everything, keep the data
#   deploy/kill_single_server.sh --wipe             # ALSO destroy the database volume
#   deploy/kill_single_server.sh --wipe --yes       # no confirmation prompt
#   deploy/kill_single_server.sh --sandboxes        # only clean up leaked sandboxes
#   deploy/kill_single_server.sh --keep-sandboxes   # leave sandbox containers alone
#
set -euo pipefail

WIPE="no"; ASSUME_YES="no"; ONLY_SANDBOXES="no"; KEEP_SANDBOXES="no"

while [ $# -gt 0 ]; do
  case "$1" in
    --wipe)      WIPE="yes"; shift ;;
    --yes|-y)    ASSUME_YES="yes"; shift ;;
    --sandboxes) ONLY_SANDBOXES="yes"; shift ;;
    --keep-sandboxes) KEEP_SANDBOXES="yes"; shift ;;
    -h|--help)   sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1 (try --help)" >&2; exit 2 ;;
  esac
done

# Everything below speaks in paths from the checkout root, wherever this was
# run from.
cd "$(dirname "$0")/.."
. deploy/_common.sh

say "Checking where we are"
require_repo_root; ok "in the repository root ($REPO)"
resolve_docker;    ok "docker is usable ($DOCKER)"

# ── sandbox siblings ──────────────────────────────────────────────────────────
say "Sandbox containers"
ids="$(yaaf_sandbox_ids)"
if [ -z "$ids" ]; then
  ok "none to clean up"
else
  count="$(echo "$ids" | wc -l | tr -d ' ')"
  # shellcheck disable=SC2086
  $DOCKER rm -f $ids >/dev/null
  ok "removed $count sandbox/chat container(s)"
fi

if [ "$ONLY_SANDBOXES" = "yes" ]; then
  say "Done"
  echo "  Left the stack running (--sandboxes)."
  exit 0
fi

# ── the stack ─────────────────────────────────────────────────────────────────
if [ "$WIPE" = "yes" ]; then
  say "Destroying the database"
  warn "--wipe removes the Postgres volume. Every project, run, decision,"
  warn "memory and stored secret on this box goes with it. There is no undo."
  warn "deploy/.env survives, so YAAF_MASTER_KEY is kept — but it will have"
  warn "nothing left to decrypt."
  if [ "$ASSUME_YES" != "yes" ]; then
    printf '\n    Type DESTROY to confirm: '
    read -r reply
    [ "$reply" = "DESTROY" ] || die "not confirmed — nothing was wiped (the sandboxes above were still cleaned up)"
  fi
  compose down -v || die "compose down -v failed"
  ok "containers and the database volume are gone"
else
  say "Stopping the stack"
  compose down || die "compose down failed"
  ok "containers removed; the database volume is intact"
fi

say "Done"
if [ "$WIPE" = "yes" ]; then
cat <<EOF

  Everything is gone except deploy/.env and the checkout.
  Start fresh with:  deploy/deploy_single_server.sh

EOF
else
cat <<EOF

  Data kept: the Postgres volume, deploy/.env, /var/lib/yaaf/{workspaces,artifacts}.
  Bring it back with:  deploy/deploy_single_server.sh

EOF
fi
