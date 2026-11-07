#!/usr/bin/env bash
# yaaf on your own desktop, in one command: the packages, yaaf itself, then
# `yaaf setup`, which asks for an email, a password and a key and leaves yaaf
# running in the background. Agents run on this machine in bubblewrap jails —
# no Docker (docs/INSTALL.md, "Option 2: Native").
#
#   deploy/install_desktop.sh
#
# Arch (Omarchy) first; Debian and Ubuntu work too. Safe to re-run: every step
# skips what is already there, and so does `yaaf setup`.
set -euo pipefail

cd "$(dirname "$0")/.."
REPO="$PWD"
say() { printf '\n==> %s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }
as_root() { if [ "$(id -u)" = 0 ]; then "$@"; else sudo "$@"; fi; }

cat <<'WARN'

  Native mode runs your agents on this computer, not in Docker.

  Each agent works in a bubblewrap sandbox that hides your home folder and
  yaaf's own keys, and each run is capped at 2 CPUs and 2 GB unless you choose
  otherwise. But it is far less contained than Docker. An agent can:
    - use your network as you can, including this machine's own services
      (a local database, a dev server, your router's admin page);
    - read the system's installed software and its settings (/usr, /etc, /opt);
    - use the CPU, memory and disk you give it.
  Docker keeps each run in its own container on its own network. If this
  machine holds anything you would not hand to a stranger's script, run yaaf
  with Docker instead (docs/INSTALL.md, "Option 1: Docker").

WARN
if [ -t 0 ] && [ -z "${YAAF_ACCEPT_NATIVE:-}" ]; then
  read -r -p "Continue with native mode? [y/N] " answer
  case "$answer" in [yY]*) ;; *) echo "Stopped; nothing was installed."; exit 1 ;; esac
fi
export YAAF_ACCEPT_NATIVE=1  # answered here; yaaf setup does not ask again

say "System packages"
missing=()
for tool in git bwrap node npm; do have "$tool" || missing+=("$tool"); done
if have pacman; then
  have uv || missing+=(uv)
  if [ ${#missing[@]} -gt 0 ]; then
    pkgs=()
    for tool in "${missing[@]}"; do
      case "$tool" in bwrap) pkgs+=(bubblewrap) ;; node) pkgs+=(nodejs) ;; *) pkgs+=("$tool") ;; esac
    done
    echo "installing: ${pkgs[*]} (your password may be asked once)"
    as_root pacman -S --needed --noconfirm "${pkgs[@]}"
  fi
elif have apt-get; then
  # Ubuntu ships python3 without venv; ask for it whenever it is missing, not
  # only when something else is.
  have uv || python3 -c 'import ensurepip' 2>/dev/null || missing+=(python3-venv)
  if [ ${#missing[@]} -gt 0 ]; then
    pkgs=()
    for tool in "${missing[@]}"; do
      case "$tool" in bwrap) pkgs+=(bubblewrap) ;; node) pkgs+=(nodejs) ;; *) pkgs+=("$tool") ;; esac
    done
    echo "installing: ${pkgs[*]} (your password may be asked once)"
    as_root apt-get update -qq
    as_root apt-get install -y -qq "${pkgs[@]}"
  fi
elif [ ${#missing[@]} -gt 0 ]; then
  echo "Install these with your package manager, then run this again: ${missing[*]}" >&2
  exit 1
fi
echo "git, bubblewrap, node and npm are here"

say "yaaf"
# Python 3.13 through uv where there is uv: a distribution's newest Python can
# be newer than every wheel yaaf depends on, and 3.13 is what this is tested on.
if [ ! -x .venv/bin/python ]; then
  if have uv; then
    uv venv --quiet --python 3.13 .venv
  else
    python3 -c 'import sys; sys.exit(sys.version_info < (3, 12))' || {
      echo "yaaf needs Python 3.12 or newer (or uv, which fetches one)" >&2; exit 1; }
    python3 -m venv .venv
  fi
fi
if have uv; then
  uv pip install --quiet --python .venv/bin/python -e ".[host]"
else
  .venv/bin/pip install --quiet -e ".[host]"
fi
mkdir -p "$HOME/.local/bin"
ln -sf "$REPO/.venv/bin/yaaf" "$HOME/.local/bin/yaaf"
echo "installed; the yaaf command is $HOME/.local/bin/yaaf"

say "The agents' browser"
# Chromium for Playwright, as the sandbox image carries it. Where the
# distribution needs system libraries for it, Playwright installs them (apt).
if have apt-get; then
  as_root .venv/bin/python -m playwright install-deps chromium || true
fi
if .venv/bin/python -m playwright install chromium; then
  echo "agents can drive Chromium"
else
  echo "note: Chromium did not install; agents will not be offered a browser (yaaf doctor)"
fi
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) echo "note: $HOME/.local/bin is not on your PATH — add it, or run $REPO/.venv/bin/yaaf" ;;
esac

say "Setup"
exec "$REPO/.venv/bin/yaaf" setup "$@"
