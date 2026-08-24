#!/usr/bin/env bash
#
# update_single_server.sh — pull new code and bring the running stack onto it.
#
# Migrations need no separate step. They run from the image entrypoint at
# container start, and since they are no longer allowed to fail silently, a
# migration that cannot apply stops the container instead of leaving you with
# a server that answers /healthz and 500s everything else. This script backs
# the database up first, then verifies the schema really did reach head rather
# than trusting that it did.
#
# Usage:
#   ./update_single_server.sh                # pull, back up, rebuild, verify
#   ./update_single_server.sh --no-pull      # rebuild what is already checked out
#   ./update_single_server.sh --no-backup    # skip the pg_dump (not advised)
#   ./update_single_server.sh --keep-chat    # leave resident chat sessions running
#   ./update_single_server.sh --skip-roles   # do not load roles new to the checkout
#
set -euo pipefail

PULL="yes"; BACKUP="yes"; RESTART_CHAT="yes"; SKIP_ROLES="no"

while [ $# -gt 0 ]; do
  case "$1" in
    --no-pull)   PULL="no"; shift ;;
    --no-backup) BACKUP="no"; shift ;;
    --keep-chat) RESTART_CHAT="no"; shift ;;
    --skip-roles) SKIP_ROLES="yes"; shift ;;
    -h|--help)   sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1 (try --help)" >&2; exit 2 ;;
  esac
done

. "$(dirname "$0")/deploy/_common.sh"

say "Checking where we are"
require_repo_root; ok "in the repository root ($REPO)"
resolve_docker;    ok "docker is usable ($DOCKER)"
load_env;          ok "deploy/.env loaded"
PORT="$(current_port)"; PORT="${PORT:-8080}"
ok "dashboard port: $PORT"

# ── 1. new code ───────────────────────────────────────────────────────────────
say "Code"
if [ "$PULL" = "no" ]; then
  warn "skipping git pull (--no-pull)"
  BEFORE=""
elif [ ! -d .git ]; then
  warn "not a git checkout — nothing to pull"
  BEFORE=""
else
  # A dirty tree plus a pull is how local edits get clobbered or a merge
  # conflict lands mid-deploy. Stop while stopping is still cheap.
  if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    git status --short --untracked-files=no | sed 's/^/      /'
    die "the working tree has uncommitted changes. Commit, stash, or re-run with --no-pull."
  fi
  BEFORE="$(git rev-parse HEAD)"
  git pull --ff-only || die "git pull failed (not a fast-forward?). Resolve it, then re-run."
  AFTER="$(git rev-parse HEAD)"
  if [ "$BEFORE" = "$AFTER" ]; then
    ok "already up to date ($(git rev-parse --short HEAD))"
  else
    ok "updated $(git rev-parse --short "$BEFORE") -> $(git rev-parse --short "$AFTER")"
    git log --oneline "$BEFORE..$AFTER" | sed 's/^/      /'
    if git diff --name-only "$BEFORE" "$AFTER" | grep -q '^migrations/'; then
      warn "this update contains migrations — they apply on container start, below"
    fi
  fi
fi

# ── 2. backup ─────────────────────────────────────────────────────────────────
# Before migrations, not after. A migration that applies cleanly and turns out
# to be wrong is still a migration you want to undo.
say "Database backup"
if [ "$BACKUP" = "no" ]; then
  warn "skipped (--no-backup)"
elif ! compose ps --status running --services 2>/dev/null | grep -q '^db$'; then
  warn "the db container is not running — nothing to back up yet"
else
  mkdir -p backups
  stamp="$(date +%Y%m%d-%H%M%S)"
  if compose exec -T db pg_dump -U yaaf yaaf 2>/dev/null | gzip > "backups/yaaf-${stamp}.sql.gz"; then
    # A dump that failed halfway still leaves a valid-looking .gz, so judge it
    # by size rather than by exit status alone.
    size=$(wc -c < "backups/yaaf-${stamp}.sql.gz")
    [ "$size" -gt 1000 ] || die "the backup is only ${size} bytes — refusing to continue. Check: compose logs db"
    ok "backups/yaaf-${stamp}.sql.gz ($(( size / 1024 )) KB)"
  else
    rm -f "backups/yaaf-${stamp}.sql.gz"
    die "pg_dump failed — refusing to migrate without a backup (override with --no-backup)"
  fi
fi

# ── 3. images ─────────────────────────────────────────────────────────────────
# Two images, and compose only knows about one. The sandbox image is what agent
# runs execute inside; skipping it leaves runs on stale code after an update.
say "Rebuilding images"
$DOCKER build -f deploy/sandbox/Dockerfile -t yaaf-sandbox:latest . >/dev/null \
  || die "sandbox image build failed"
ok "yaaf-sandbox:latest rebuilt"
migrate_sandbox_network
# --remove-orphans: the proxy service was removed with the egress allowlist;
# an orphaned squid container would sit there advertising containment nothing uses.
compose up -d --build --remove-orphans || die "compose up failed"
ok "control plane restarted on the new code"

# ── 4. migrations ─────────────────────────────────────────────────────────────
say "Schema"
wait_for_api "$PORT"; ok "API answering on 127.0.0.1:${PORT}"
if wait_for_schema 300; then
  ok "migrations at head — applied by the entrypoint at start"
else
  # Reaching here means the entrypoint failed and the container came back
  # anyway, or the api container is older than this script expects.
  warn "schema is not at head — applying now"
  apply_migrations 300 || die "alembic upgrade failed, or took longer than 300s — the output
    above says which. If it timed out, something else is holding the schema; look at:
      cd deploy && $DOCKER compose logs api --tail=50
    Your backup is in backups/ ; restore with:
      gunzip -c backups/<file>.sql.gz | $DOCKER compose exec -T db psql -U yaaf -d yaaf"
  schema_at_head 60 || die "migrations ran but the schema is still not at head"
  ok "migrations applied"
fi

# ── 5. role library ───────────────────────────────────────────────────────────
# An update brings new code AND, sometimes, new roles — roles/ is checked out,
# not baked into the image, so nothing else notices them. Without this step an
# install seeded once keeps whatever library it had on day one, and the first
# sign of it is a setup file from examples/ landing on New Project with its
# teams' roles blank because the slugs it hires do not exist here.
#
# Only the missing slugs are loaded: a role already in the library is left
# alone, edits and all.
say "Role library"
if [ "$SKIP_ROLES" = "yes" ]; then
  warn "skipped (--skip-roles)"
else
  sync_role_library "$PORT" \
    || die "could not read the role library — is YAAF_ADMIN_TOKEN in deploy/.env the one the API booted with?"
  sync_crew_library "$PORT" \
    || die "could not read the crew library — is YAAF_ADMIN_TOKEN in deploy/.env the one the API booted with?"
fi

# ── 6. resident chat sessions ─────────────────────────────────────────────────
# Chat containers are adopted across runner restarts rather than recreated, so
# without this they keep running the code they started with.
say "Resident chat sessions"
chat_ids="$($DOCKER ps -q --filter 'label=yaaf.chat.project' || true)"
if [ -z "$chat_ids" ]; then
  ok "none running"
elif [ "$RESTART_CHAT" = "no" ]; then
  warn "left running (--keep-chat) — they are still on the old code"
else
  # shellcheck disable=SC2086
  $DOCKER stop $chat_ids >/dev/null
  ok "stopped $(echo "$chat_ids" | wc -w | tr -d ' ') session(s) — they relaunch on the new image on next use"
fi

say "Done"
cat <<EOF

  Dashboard   http://localhost:${PORT}/app
  Token       grep YAAF_ADMIN_TOKEN deploy/.env
  Backups     ./backups/   (this run's dump is the newest)

  Rolling back a bad migration means restoring the dump:

      gunzip -c backups/<file>.sql.gz | $DOCKER compose exec -T db psql -U yaaf -d yaaf

EOF
