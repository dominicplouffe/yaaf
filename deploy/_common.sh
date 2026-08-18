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
# services — `compose down` does not touch them. Agent runs carry no labels and
# get Docker-random names, so the entrypoint command is the only reliable
# handle; chat containers also carry yaaf.chat.project but are matched the same
# way for consistency. Old ones reference untagged image IDs after a rebuild,
# which is why matching on the image tag misses almost all of them.
yaaf_sandbox_ids() {
  $DOCKER ps -a --no-trunc --format '{{.ID}}	{{.Command}}' \
    | grep -E 'sandbox_entry\.py|chat_entry\.py' | cut -f1 || true
}

# The runner regenerates the per-role squid config every 15 seconds, and it
# used to write into ./proxy/generated — inside the git checkout. A tracked file
# that the running application rewrites is a permanent local modification: noise
# at best, and since this script refuses to run on a dirty tree, a deploy that
# blocks itself at worst. Two of that file's commits exist only to absorb the
# churn. Machine state now lives beside the other machine state.
#
# Squid's `include` is FATAL when the target is missing, so the directory has to
# carry a roles.conf before the proxy container ever starts. A comment-only file
# parses cleanly, and the runner replaces it within one tick.
PROXY_GENERATED_DIR="/var/lib/yaaf/proxy-generated"

ensure_proxy_generated() {
  local legacy="$REPO/deploy/proxy/generated"
  [ -d "$PROXY_GENERATED_DIR" ] \
    || mkdir -p "$PROXY_GENERATED_DIR" 2>/dev/null \
    || sudo mkdir -p "$PROXY_GENERATED_DIR" \
    || die "could not create $PROXY_GENERATED_DIR"

  # One-time carry-over for a box upgrading from the in-checkout location: it
  # keeps its role ports instead of losing egress until the next regeneration.
  if [ ! -f "$PROXY_GENERATED_DIR/roles.conf" ] && [ -f "$legacy/roles.conf" ]; then
    cp -a "$legacy"/. "$PROXY_GENERATED_DIR"/ 2>/dev/null \
      || sudo cp -a "$legacy"/. "$PROXY_GENERATED_DIR"/ 2>/dev/null || true
  fi

  if [ ! -f "$PROXY_GENERATED_DIR/roles.conf" ]; then
    local seed="# Seeded before first boot — the runner rewrites this within 15s.
# Squid refuses to start when this include target does not exist."
    printf '%s\n' "$seed" > "$PROXY_GENERATED_DIR/roles.conf" 2>/dev/null \
      || printf '%s\n' "$seed" | sudo tee "$PROXY_GENERATED_DIR/roles.conf" >/dev/null \
      || die "could not seed $PROXY_GENERATED_DIR/roles.conf"
  fi
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

# The port the dashboard is published on, read back from the generated
# override so update/kill agree with whatever deploy chose.
current_port() {
  if [ -f "$REPO/deploy/docker-compose.override.yml" ]; then
    sed -n 's/.*"[0-9.]*:\([0-9]\+\):8080".*/\1/p' "$REPO/deploy/docker-compose.override.yml" | head -1
  fi
}
