# Shared by deploy_single_server.sh, update_single_server.sh and
# kill_single_server.sh. Sourced, never run on its own.
#
# shellcheck shell=bash

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
ok()   { printf '    \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '    \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '\n\033[31mFAILED:\033[0m %s\n\n' "$*" >&2; exit 1; }

# Ctrl-C has to work. It did not, and the reason was two-fold: bash defers a
# trap until the foreground command returns, and `timeout` without --foreground
# puts its child in a separate process group where the terminal's SIGINT never
# reaches it. Between them the script would sit through a docker command that
# could not hear you, and closing the window was the only way out. Every long
# docker call below now runs with --foreground; this gives the interrupt
# somewhere to land.
#
# Re-running after an interrupt is safe: every step in these scripts is
# idempotent, which is the property that makes stopping cheap.
on_interrupt() {
  printf '\n'
  warn "interrupted — stopping here. Re-running is safe; every step is idempotent."
  exit 130
}
trap on_interrupt INT TERM

# The checkout root, judged by the things these scripts actually need. Being
# in the wrong directory otherwise fails much later, halfway through a build.
require_repo_root() {
  local marker
  for marker in pyproject.toml deploy/docker-compose.yml deploy/Dockerfile \
                deploy/sandbox/Dockerfile roles migrations; do
    [ -e "$marker" ] \
      || die "no ./$marker here — run this from the root of the yaaf checkout (you are in $PWD)"
  done
  REPO="$PWD"
}

# Prefer unprivileged docker, fall back to sudo rather than dying: a box where
# the usermod has not taken effect yet is the common case, not an error.
resolve_docker() {
  command -v docker >/dev/null 2>&1 || die "docker is not installed. Install it with:
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker \"\$USER\"   # then log out and back in"

  if docker info >/dev/null 2>&1; then
    DOCKER="docker"
  elif sudo -n docker info >/dev/null 2>&1 || sudo docker info >/dev/null 2>&1; then
    DOCKER="sudo docker"
    warn "using 'sudo docker' — add yourself to the docker group to drop the sudo:"
    warn "  sudo usermod -aG docker \"\$USER\"   (then log out and back in)"
  else
    die "the docker daemon is not reachable. Is it running? Try: sudo systemctl start docker"
  fi

  $DOCKER compose version >/dev/null 2>&1 \
    || die "the docker compose plugin is missing. On Debian/Ubuntu:
    sudo apt-get install -y docker-compose-plugin"
}

# Every compose call runs from deploy/, so the project name and the .env file
# resolve the same way no matter where the caller stood.
compose() { ( cd "$REPO/deploy" && $DOCKER compose "$@" ); }

load_env() {
  [ -f "$REPO/deploy/.env" ] || die "no deploy/.env — run ./deploy_single_server.sh first"
  set -a; . "$REPO/deploy/.env"; set +a
  [ -n "${YAAF_ADMIN_TOKEN:-}" ] || die "deploy/.env has no YAAF_ADMIN_TOKEN"
}

# Sandboxes are sibling containers the runner launches directly, not compose
# services — `compose down` does not touch them. Agent runs get Docker-random
# names, so the entrypoint command is the only reliable handle; chat containers
# also carry yaaf.chat.project but are matched the same way for consistency.
# Old ones reference untagged image IDs after a rebuild, which is why matching
# on the image tag misses almost all of them.
#
# The sweep is BOX-WIDE, so it needs a stack boundary or a teardown here kills
# the containers of any other stack on the box — the live one, mid-run. A
# container is ours when its yaaf.stack label matches; unlabelled containers
# predate the label and belong to the default stack, which is the only one that
# existed when they were started.
yaaf_sandbox_ids() {
  load_stack_env
  $DOCKER ps -a --no-trunc --format '{{.ID}}	{{.Command}}	{{.Label "yaaf.stack"}}' \
    | grep -E 'sandbox_entry\.py|chat_entry\.py' \
    | awk -F'\t' -v stack="$YAAF_STACK" \
        '$3 == stack || ($3 == "" && stack == "deploy") { print $1 }' || true
}

load_stack_env() {
  if [ -f "$REPO/deploy/.env" ]; then set -a; . "$REPO/deploy/.env"; set +a; fi
  YAAF_STACK="${YAAF_STACK:-deploy}"
  YAAF_STATE_ROOT="${YAAF_STATE_ROOT:-/var/lib/yaaf}"
  export YAAF_STACK YAAF_STATE_ROOT
}


# The Charter opened the sandbox network (docs/AUTONOMY.md): the per-role
# egress proxy is gone. A box that deployed before that has a network created
# with internal:true — with the proxy removed, containers on it would have NO
# route out at all, and compose refuses to alter a network's internal flag in
# place. Recreate it once, before compose up. Sandbox containers are ephemeral
# by design, so disconnecting stragglers loses nothing a retry won't redo.
migrate_sandbox_network() {
  load_stack_env
  local net="${YAAF_SANDBOX_NETWORK:-yaaf_sandbox}"
  local internal
  internal="$($DOCKER network inspect "$net" --format '{{.Internal}}' 2>/dev/null || true)"
  [ "$internal" = "true" ] || return 0
  say "Sandbox network '$net' is internal-only from a pre-Charter deploy; recreating it open"
  for cid in $($DOCKER network inspect "$net" --format '{{range $id, $_ := .Containers}}{{$id}} {{end}}' 2>/dev/null); do
    $DOCKER network disconnect -f "$net" "$cid" 2>/dev/null || true
  done
  $DOCKER network rm "$net" >/dev/null 2>&1 \
    || die "could not remove the old internal network '$net' — remove it by hand and re-run"
  ok "old internal network removed; compose recreates it open"
}

wait_for_api() {
  local port="$1" deadline=$((SECONDS + 180))
  until curl -fsS "http://127.0.0.1:${port}/healthz" >/dev/null 2>&1; do
    [ $SECONDS -lt $deadline ] || die "the API never became healthy. Look at:
    cd deploy && $DOCKER compose logs api --tail=50"
    sleep 2
  done
}

# /healthz only proves the process is alive and auth never touches the
# database, so a box with no schema looks healthy right up until the first real
# request dies on `relation "projects" does not exist`. Ask explicitly.
#
# Still wrapped in a timeout: migrations now serialize on an advisory lock
# (migrations/env.py) rather than racing, but a concurrent migrator still holds
# alembic_version while it works, so "no answer yet" remains a legitimate
# answer on a first boot. Treat it as "not at head" and let the caller retry.
#
# timeout runs the docker client directly rather than through `sh -c`: with an
# intervening shell, SIGTERM kills the shell and leaves the docker child
# running with the pipe still open, which is a hang dressed up as a timeout.
schema_at_head() {
  local out
  out="$(cd "$REPO/deploy" && COMPOSE_IGNORE_ORPHANS=1 \
         timeout --foreground "${1:-60}" $DOCKER compose exec -T api \
         alembic current 2>/dev/null)" || return 1
  printf '%s' "$out" | grep -q '(head)'
}

# The fallback for when the entrypoint's own upgrade did not get there. It used
# to run unbounded and with its output swallowed, so a migration that blocked
# left the deploy sitting on its last green line with nothing to read and
# nothing to interrupt. This is the one step whose failure you most need to
# see, so it is bounded and it prints.
apply_migrations() {
  ( cd "$REPO/deploy" && COMPOSE_IGNORE_ORPHANS=1 \
    timeout --foreground "${1:-300}" $DOCKER compose exec -T api \
    alembic upgrade head )
}

# Poll rather than ask once: on a first boot the answer is legitimately "wait",
# and the useful distinction is between "still migrating" and "never will".
wait_for_schema() {
  local deadline=$((SECONDS + ${1:-300}))
  while [ $SECONDS -lt $deadline ]; do
    schema_at_head 30 && return 0
    warn "schema not readable yet (migrations may still be running) — retrying"
  done
  return 1
}

# The role library, brought level with the checkout.
#
# Loading a role that already exists is not free: every POST to /admin/roles
# stamps a NEW VERSION of it, so re-seeding the whole directory churns versions
# and drags a role a chairman edited in the dashboard back to the file's
# wording. The old guard against that was "seed only when the library is
# empty", which is safe and also wrong the moment the repository grows a role:
# an install deployed before the markets roles landed never got them, and the
# only symptom was an example whose teams arrived at New Project with no role
# selected. So: compare by slug and load only what this install has never seen.
#
# Reads the library over HTTP rather than `compose exec api yaaf seed-roles`,
# because roles/ is deliberately not baked into the image — inside the
# container that glob matches nothing and exits 0.
sync_role_library() {
  local port="$1" library slug refile seeded=0 present=0 refiled=0 added=""
  library=$(curl -fsS -H "Authorization: Bearer ${YAAF_ADMIN_TOKEN}" \
    "http://127.0.0.1:${port}/admin/roles") \
    || return 1
  compgen -G "$REPO/roles/*.json" >/dev/null || { warn "no roles/*.json in the checkout"; return 0; }
  for f in "$REPO"/roles/*.json; do
    # The slug is the file's own first field; a prompt further down may well
    # contain the word, so stop at the first match.
    slug=$(sed -n 's/.*"slug"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$f" | head -1)
    [ -n "$slug" ] || die "${f##*/} has no \"slug\" — it is not a role definition"
    if printf '%s' "$library" | grep -q "\"slug\":[[:space:]]*\"${slug}\"" ; then
      present=$((present + 1))
      # Present means its prompt is left alone. But category and domains live
      # on the role, not the version, so an install seeded before the taxonomy
      # landed carries all thirty roles filed under nothing — and the hiring
      # desk would show every one of them as Uncategorised forever. Refile it
      # in place: no new version, so no team goes behind its role.
      refile=$(sed -n 's/.*"category"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$f" | head -1)
      if [ -n "$refile" ]; then
        curl -fsS -X PATCH "http://127.0.0.1:${port}/admin/roles/${slug}" \
          -H "Authorization: Bearer ${YAAF_ADMIN_TOKEN}" \
          -H "Content-Type: application/json" \
          --data "$(python3 -c '
import json, sys
d = json.load(open(sys.argv[1]))
print(json.dumps({"name": d["name"], "category": d.get("category"),
                  "domains": d.get("domains", [])}))' "$f")" >/dev/null \
          && refiled=$((refiled + 1)) || true
      fi
      continue
    fi
    curl -fsS -X POST "http://127.0.0.1:${port}/admin/roles" \
      -H "Authorization: Bearer ${YAAF_ADMIN_TOKEN}" \
      -H "Content-Type: application/json" \
      --data-binary @"$f" >/dev/null || die "failed to seed ${f##*/}"
    seeded=$((seeded + 1))
    added="${added:+$added, }$slug"
  done
  if [ "$refiled" -gt 0 ]; then
    ok "$refiled role(s) refiled into the hiring categories (no new versions)"
  fi
  if [ "$seeded" -eq 0 ]; then
    ok "$present role(s) already loaded, none missing — nothing re-seeded"
  elif [ "$present" -eq 0 ]; then
    ok "seeded $seeded role(s) — the whole library"
  else
    # Naming them matters here: this is the line that tells a chairman which
    # roles their install was missing, and why an example would not fill in.
    ok "$present already loaded; seeded $seeded new role(s): $added"
  fi
}

# Crews, on the same contract as roles: load what this install has never seen,
# leave anything already there alone. A crew is reference data — one is read at
# hiring time and copied into the project's own teams — so re-seeding can never
# reach a company that already started, and an edited crew stays edited.
sync_crew_library() {
  local port="$1" library slug seeded=0 present=0
  library=$(curl -fsS -H "Authorization: Bearer ${YAAF_ADMIN_TOKEN}" \
    "http://127.0.0.1:${port}/admin/crews") \
    || return 1
  compgen -G "$REPO/crews/*.json" >/dev/null || { warn "no crews/*.json in the checkout"; return 0; }
  for f in "$REPO"/crews/*.json; do
    slug=$(sed -n 's/.*"slug"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$f" | head -1)
    [ -n "$slug" ] || die "${f##*/} has no \"slug\" — it is not a crew definition"
    if printf '%s' "$library" | grep -q "\"slug\":[[:space:]]*\"${slug}\"" ; then
      present=$((present + 1))
      continue
    fi
    curl -fsS -X POST "http://127.0.0.1:${port}/admin/crews" \
      -H "Authorization: Bearer ${YAAF_ADMIN_TOKEN}" \
      -H "Content-Type: application/json" \
      --data-binary @"$f" >/dev/null || die "failed to seed ${f##*/}"
    seeded=$((seeded + 1))
  done
  if [ "$seeded" -eq 0 ]; then
    ok "$present crew(s) already loaded, none missing"
  else
    ok "seeded $seeded crew(s) — the starting points on the hiring desk"
  fi
}

# The port the dashboard is published on, read back from the generated
# override so update/kill agree with whatever deploy chose.
current_port() {
  if [ -f "$REPO/deploy/docker-compose.override.yml" ]; then
    sed -n 's/.*"[0-9.]*:\([0-9]\+\):8080".*/\1/p' "$REPO/deploy/docker-compose.override.yml" | head -1
  fi
}
