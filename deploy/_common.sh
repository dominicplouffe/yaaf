# Shared by deploy_single_server.sh, update_single_server.sh and
# kill_single_server.sh. Sourced, never run on its own.
#
# shellcheck shell=bash

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
ok()   { printf '    \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '    \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '\n\033[31mFAILED:\033[0m %s\n\n' "$*" >&2; exit 1; }

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
# services — `compose down` does not touch them. Agent runs carry no labels and
# get Docker-random names, so the entrypoint command is the only reliable
# handle; chat containers also carry yaaf.chat.project but are matched the same
# way for consistency. Old ones reference untagged image IDs after a rebuild,
# which is why matching on the image tag misses almost all of them.
yaaf_sandbox_ids() {
  $DOCKER ps -a --no-trunc --format '{{.ID}}	{{.Command}}' \
    | grep -E 'sandbox_entry\.py|chat_entry\.py' | cut -f1 || true
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
# Wrapped in a timeout because this can block rather than answer: api and
# runner both run `alembic upgrade head` from the entrypoint at start, the
# whole chain runs in ONE transaction, and creating alembic_version takes an
# ACCESS EXCLUSIVE lock on it. A concurrent `alembic current` then waits for
# that transaction to commit — which on a first boot is the exact moment this
# gets called. Treat "no answer in time" as "not at head" and let the caller
# retry rather than hanging with no output.
schema_at_head() {
  timeout "${1:-60}" env COMPOSE_IGNORE_ORPHANS=1 sh -c \
    "cd '$REPO/deploy' && $DOCKER compose exec -T api alembic current 2>/dev/null" \
    | grep -q '(head)'
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

# The port the dashboard is published on, read back from the generated
# override so update/kill agree with whatever deploy chose.
current_port() {
  if [ -f "$REPO/deploy/docker-compose.override.yml" ]; then
    sed -n 's/.*"[0-9.]*:\([0-9]\+\):8080".*/\1/p' "$REPO/deploy/docker-compose.override.yml" | head -1
  fi
}
