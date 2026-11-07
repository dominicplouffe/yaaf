#!/usr/bin/env bash
#
# deploy_single_server.sh — take a bare box to a running yaaf, or bring an
# existing one up to date. Safe to re-run: it never overwrites secrets, never
# re-versions a role the library already has (it loads only the ones missing),
# and never widens an exposed port you did not ask for.
#
# Everything runs here: Postgres, the API + dashboard, the runner. Splitting
# Postgres onto its own host is a later variant — the seam is marked
# EXTERNAL-DB below.
#
# Usage:
#   deploy/deploy_single_server.sh --admin-email you@example.com   # first install
#   deploy/deploy_single_server.sh                  # dashboard on 127.0.0.1:24247
#   deploy/deploy_single_server.sh --bind 0.0.0.0   # publish it (see the warning)
#   deploy/deploy_single_server.sh --bind 127.0.0.1 --bind 192.168.0.5   # both
#   deploy/deploy_single_server.sh --port 9090
#   deploy/deploy_single_server.sh --skip-roles
#   deploy/deploy_single_server.sh --build        # build images from source, never pull
#
set -euo pipefail

BINDS=()
PORT=""
SKIP_ROLES="no"
ADMIN_EMAIL=""
BUILD_MODE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --bind)  BINDS+=("${2:?--bind needs an address}"); shift 2 ;;
    --port)  PORT="${2:?--port needs a port}"; shift 2 ;;
    --skip-roles) SKIP_ROLES="yes"; shift ;;
    --build) BUILD_MODE="build"; shift ;;
    --admin-email) ADMIN_EMAIL="${2:?--admin-email needs an address}"; shift 2 ;;
    -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1 (try --help)" >&2; exit 2 ;;
  esac
done

# Everything below speaks in paths from the checkout root, wherever this was
# run from.
cd "$(dirname "$0")/.."
. deploy/_common.sh

# ── 1. preflight ──────────────────────────────────────────────────────────────
say "Checking where we are and what we have"
require_repo_root; ok "in the repository root ($REPO)"
resolve_docker;    ok "docker is usable ($DOCKER)"
ok "docker compose plugin present"

command -v openssl >/dev/null 2>&1 || die "openssl is missing (needed to generate secrets).
    sudo apt-get install -y openssl"
command -v curl >/dev/null 2>&1 || die "curl is missing.
    sudo apt-get install -y curl"

# ── 2. host paths ─────────────────────────────────────────────────────────────
# Sandboxes are sibling containers, so a workspace path must mean the same
# thing on the host and inside the runner. Identical strings or nothing.
say "Host directories"
load_stack_env
[ "$YAAF_STACK" = "deploy" ] || ok "stack '$YAAF_STACK' — state under $YAAF_STATE_ROOT"
for d in "$YAAF_STATE_ROOT/workspaces" "$YAAF_STATE_ROOT/artifacts"; do
  if [ -d "$d" ]; then
    ok "$d exists"
  else
    # Only as root where it has to be: /var/lib needs it, a folder in your home
    # (YAAF_STATE_ROOT, as on a Mac) does not.
    { mkdir -p "$d" 2>/dev/null || sudo mkdir -p "$d"; } && ok "created $d"
  fi
done

# ── 3. secrets ────────────────────────────────────────────────────────────────
# YAAF_MASTER_KEY must be a Fernet key (32 url-safe base64 bytes, padded) —
# `openssl rand -base64 32` satisfies it; secrets.token_urlsafe(32) does NOT, it
# strips the padding. There is no login secret here any more: people sign in
# with an account, and the first one is made in step 8.
say "Secrets (deploy/.env)"
if [ -f deploy/.env ]; then
  ok "deploy/.env already exists — keeping what it says"
  if grep -qE "^YAAF_MASTER_KEY=.+" deploy/.env; then
    ok "the master key is set"
  elif ! $DOCKER volume inspect "${YAAF_STACK}_pgdata" >/dev/null 2>&1; then
    # A first install whose .env was started by hand (YAAF_STATE_ROOT on a
    # Mac, or a copy of .env.example): no database exists, so no secret was
    # ever encrypted, and a new key strands nothing. Any empty
    # YAAF_MASTER_KEY= line is replaced rather than left to shadow it.
    tmp="$(mktemp deploy/.env.XXXXXX)"
    grep -vE "^YAAF_MASTER_KEY=" deploy/.env > "$tmp" || true
    echo "YAAF_MASTER_KEY=$(openssl rand -base64 32)" >> "$tmp"
    chmod 600 "$tmp"; mv "$tmp" deploy/.env
    ok "added a master key to deploy/.env (no database yet, so nothing was encrypted before)"
    warn "back up YAAF_MASTER_KEY somewhere off this box — losing it makes every"
    warn "stored secret unreadable, and there is no recovery path."
  else
    # A database exists, so its secrets were encrypted under SOME key. Making
    # a new one would strand every one of them: stop, rather than "fix" it.
    die "deploy/.env has no value for YAAF_MASTER_KEY, but this box already has a database.
    Put back the key it was encrypted with (from your backup of deploy/.env) and re-run."
  fi
else
  echo "YAAF_MASTER_KEY=$(openssl rand -base64 32)" > deploy/.env
  chmod 600 deploy/.env
  ok "generated deploy/.env"
  warn "back up YAAF_MASTER_KEY somewhere off this box — losing it makes every"
  warn "stored secret unreadable, and there is no recovery path."
fi
# The optional MinIO store (--profile s3) has no default password: MinIO refuses
# to start without one, so a box never runs it on a password from the repo.
# Added to an existing deploy/.env too, which is why this is not in the branch
# above. Appending never changes a value that is already there.
if ! grep -qE "^MINIO_ROOT_PASSWORD=.+" deploy/.env; then
  echo "MINIO_ROOT_PASSWORD=$(openssl rand -hex 24)" >> deploy/.env
  ok "generated a password for the optional MinIO store"
fi

set -a; . ./deploy/.env; set +a
warn_retired_token
ADMIN_EMAIL="${ADMIN_EMAIL:-${YAAF_ADMIN_EMAIL:-}}"

# ── 4. where the dashboard listens ────────────────────────────────────────────
# Compose merges docker-compose.override.yml automatically. The !override tag
# is load-bearing: without it compose APPENDS to ports and you keep the
# original 0.0.0.0 binding alongside the one you asked for.
say "Dashboard binding"
# A bare re-run keeps whatever the last run published on. Silently narrowing a
# binding you asked for would strand you as surely as widening one you did not,
# and the update script only ever reads this file back.
if [ ${#BINDS[@]} -eq 0 ]; then
  while IFS= read -r addr; do
    [ -n "$addr" ] && BINDS+=("$addr")
  done <<EOF
$(current_binds)
EOF
fi
[ ${#BINDS[@]} -eq 0 ] && BINDS=("127.0.0.1")
[ -n "$PORT" ] || PORT="$(current_port)"
# A first install publishes on 24247 (yaaf.core.config.DEFAULT_PORT); one that
# already published elsewhere — 8080, before that default — keeps its port.
PORT="${PORT:-24247}"

# Everything below here — the health wait, the seeding, the login token —
# talks to 127.0.0.1. A binding that leaves loopback out would fail all of it
# against a server that is perfectly healthy, so keep loopback published
# alongside whatever else was asked for. Duplicates would collide on the port.
seen=""; keep=()
for addr in "${BINDS[@]}"; do
  case " $seen " in *" $addr "*) continue ;; esac
  seen="$seen $addr"; keep+=("$addr")
done
BINDS=("${keep[@]}")
case " ${BINDS[*]} " in
  *" 127.0.0.1 "*|*" 0.0.0.0 "*) ;;
  *) BINDS=("127.0.0.1" "${BINDS[@]}") ;;
esac

# One published port per address: 127.0.0.1 alone answers localhost and nothing
# else, so reaching the box by its LAN address means naming that address too.
{ echo "# Generated by deploy_single_server.sh — per-machine, not committed."
  echo "services:"
  echo "  api:"
  echo "    ports: !override"
  for addr in "${BINDS[@]}"; do
    echo "      - \"${addr}:${PORT}:8080\""
  done
} > deploy/docker-compose.override.yml

exposed="no"
for addr in "${BINDS[@]}"; do
  if [ "$addr" = "127.0.0.1" ]; then
    ok "bound to 127.0.0.1:${PORT} — reach it with: ssh -N -L ${PORT}:localhost:${PORT} $(whoami)@<this-host>"
  else
    warn "bound to ${addr}:${PORT} — anyone who can route to that address can reach it."
    exposed="yes"
  fi
done
if [ "$exposed" = "yes" ]; then
  warn "Passwords and session cookies cross that address in cleartext, and Docker's"
  warn "iptables rules sit in front of ufw, so this really is reachable."
  warn "Put TLS in front of it before using this over an untrusted network."
fi

# ── 5. images ─────────────────────────────────────────────────────────────────
# Pulled when CI published this exact commit, built from source otherwise
# (prepare_images in _common.sh). Both images, every time: the sandbox image is
# what agent runs execute inside, and a stale one runs old code.
say "Images"
prepare_images "$BUILD_MODE"

migrate_sandbox_network
# --remove-orphans: the proxy service was removed with the egress allowlist;
# an orphaned squid container would sit there advertising containment nothing uses.
compose up -d --no-build --remove-orphans || die "compose up failed"
ok "control plane up"

# ── 6. wait for the API ───────────────────────────────────────────────────────
say "Waiting for the API"
wait_for_api "$PORT"
ok "API answering on 127.0.0.1:${PORT}"

# ── 7. schema ─────────────────────────────────────────────────────────────────
# /healthz only proves the process is alive, and auth never touches the
# database — so a box with no schema looks healthy right up until the first
# real request dies on `relation "projects" does not exist`. Check explicitly.
say "Database schema"
if wait_for_schema 300; then
  ok "migrations at head"
else
  warn "not at head after 5 minutes — applying migrations directly"
  compose exec -T api alembic upgrade head \
    || die "alembic upgrade failed — the output above says why"
  schema_at_head 60 || die "migrations ran but the schema is still not at head"
  ok "migrations applied"
fi

# ── 8. the first admin ────────────────────────────────────────────────────────
# Nobody can sign in to a box with no admin, so a first install makes one. An
# existing box is left alone: after the first, admins are made by admins.
say "People"
if [ -z "$ADMIN_EMAIL" ] && [ -t 0 ] \
    && ! compose exec -T api yaaf users has-admin >/dev/null 2>&1; then
  read -r -p "    The first admin's email: " ADMIN_EMAIL
fi
# Where a person will open the link: through the tunnel when loopback is
# published, otherwise this host's own address.
URL_BASE="http://<this-host>:${PORT}"
case " ${BINDS[*]} " in *" 127.0.0.1 "*) URL_BASE="http://localhost:${PORT}" ;; esac
HAS_ADMIN="yes"
ensure_admin "$ADMIN_EMAIL" "${YAAF_PUBLIC_BASE_URL:-$URL_BASE}" || HAS_ADMIN="no"

# ── 9. role library ───────────────────────────────────────────────────────────
# Every role in the checkout that this install does not already have, loaded by
# slug (see sync_role_library). Roles the library already carries are left
# exactly as they are: re-posting one stamps a new version and would overwrite
# a chairman's dashboard edits with the file's wording.
say "Role library"
if [ "$SKIP_ROLES" = "yes" ]; then
  warn "skipped (--skip-roles)"
elif [ "$HAS_ADMIN" = "no" ]; then
  warn "skipped — loading the library needs an admin, and the box has none yet"
else
  deploy_token || die "could not mint a deploy token inside the api container"
  sync_role_library "$PORT" || die "could not read the role library through the API"
  sync_crew_library "$PORT" || die "could not read the crew library through the API"
fi

# ── EXTERNAL-DB ───────────────────────────────────────────────────────────────
# To move Postgres off this box: drop the `db` service, point
# YAAF_DATABASE_URL at the real host on both `api` and `runner`, and keep
# YAAF_MASTER_KEY identical to whatever encrypted the secrets already there.
# The runner needs the database; it does not need the API.

say "Running"
cat <<EOF
  Postgres, the API + dashboard and the runner, all in
  Docker on this box. Postgres publishes no port — only the other containers
  can reach it, and compose passes them YAAF_DATABASE_URL directly, so there
  is nothing for you to set.
EOF

# A first install that ends without saying how to get in strands you at the
# sign-in screen, so the setup link — printed once, by the bootstrap — is
# repeated here, where it will be read.
printf '\n\033[1m==> HOW TO SIGN IN\033[0m\n'
URL="${URL_BASE}/app"
case " ${BINDS[*]} " in
  *" 127.0.0.1 "*)
    cat <<EOF

  From your laptop, open the tunnel (leave it running):

       ssh -N -L ${PORT}:localhost:${PORT} $(whoami)@<this-host>
EOF
    ;;
esac
if [ -n "$BOOTSTRAP_OUT" ]; then
  printf '\n%s\n' "$BOOTSTRAP_OUT" | sed 's/^/  /'
  cat <<EOF

  Choose a password on that page and you are in.
EOF
elif [ "$HAS_ADMIN" = "no" ]; then
  cat <<EOF

  Nobody can sign in yet. Make the first admin, then follow the link it prints:

       $0 --admin-email you@example.com
EOF
else
  cat <<EOF

  Browse to ${URL} and sign in with your email and password.
EOF
fi

cat <<EOF

  Forgotten password? From a shell on this box:

       docker compose -f deploy/docker-compose.yml exec api yaaf users setup-link you@example.com

  The yaaf CLI signs in the same way — \`yaaf login\` exchanges your email and
  password for a personal token. Invite colleagues from Settings → People.

  Next: create a project in the dashboard. That step needs an Anthropic API
  key, which this script does NOT create — bring your own from
  console.anthropic.com and give it to the project in Settings. No agent can
  run until it has one.

  Re-run this script any time; it is idempotent.
EOF
