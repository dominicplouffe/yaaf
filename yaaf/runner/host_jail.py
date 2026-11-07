"""Host mode: agent runs on this machine, without Docker, inside bubblewrap.

Docker is the default and stays the default on a server. On a desktop it is a
daemon, an image build and a root-equivalent socket for somebody who wants a
few agents working while they do something else. ``sandbox_mode = "host"``
runs the same entry scripts as ordinary processes, on the host's own Python
and CLIs — but never in the host's own view of the filesystem.

Why bubblewrap, and not a bare subprocess: every agent is told its files live
in /workspace — by its role, by the context pack, by the tools that fence
artifact paths — and the success tests it writes name that path. A bare
process has no /workspace, and giving each run its own one without root takes
a mount namespace, which is exactly what ``bwrap`` is: unprivileged, no
daemon, no images. Path parity is also what keeps a project portable. One
started here moves to docker mode later with every check it wrote still
pointing at the right place.

The same namespace is what keeps a run out of what a process running as you
could otherwise read: your home folder, yaaf's own database and master key, the
Docker socket, and the runner's environment through /proc.

What a run sees:

- /usr, /etc, /opt and /sys, read-only; the Python yaaf runs on, read-only,
  with the symlinks that lead to it made again as symlinks; any extra path the
  operator lists in ``host_jail_binds``, read-only.
- its project's workspace, read-write, at /workspace — and nothing else of
  yaaf's data folder.
- a fresh /tmp, /run and /home/agent per run, in memory. /run/yaaf is where a
  file-shaped credential lands, as it does in the container, so a private key
  still never reaches a disk.
- its own process tree only (a pid namespace).
- the host's network. Docker's sandbox network is open too, but a bridge
  cannot reach the host's localhost and this can — SECURITY.md says so.

What a run is given: an environment built from nothing — PATH, HOME, the locale,
the host's proxy and CA settings, and the job's own variables. Never the
runner's, which holds the master key and the database address.
"""

from __future__ import annotations

import contextlib
import functools
import logging
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from yaaf.core.config import settings

log = logging.getLogger("yaaf.host_jail")

#: Where the entry scripts are mounted inside the jail. The image puts them in
#: /opt/yaaf; here /opt is the host's own, read-only, so they go under the
#: per-run /run instead. Nothing reads the directory by name but the command.
ENTRY_DIR = "/run/yaaf-entry"

_RUNNER = Path(__file__).resolve().parent
_PACKAGE = _RUNNER.parent

#: The files deploy/sandbox/Dockerfile copies into /opt/yaaf, by the name the
#: entry scripts import each other under. A test holds the two lists equal, so
#: a harness added to the image cannot be missing here.
ENTRY_FILES = {
    "sandbox_entry.py": _RUNNER / "sandbox_entry.py",
    "chat_entry.py": _RUNNER / "chat_entry.py",
    "claude_cli_entry.py": _RUNNER / "claude_cli_entry.py",
    "yaaf_tools.py": _RUNNER / "yaaf_tools.py",
    "loop_harness.py": _RUNNER / "loop_harness.py",
    "loop_entry.py": _RUNNER / "loop_entry.py",
    "wire.py": _PACKAGE / "runtimes" / "wire.py",
    "codex_cli_entry.py": _RUNNER / "codex_cli_entry.py",
    "opencode_cli_entry.py": _RUNNER / "opencode_cli_entry.py",
    "gemini_cli_entry.py": _RUNNER / "gemini_cli_entry.py",
}

#: Read-only, at their own paths. Everything a process needs to find its
#: binaries, libraries, certificates and users — and nothing that is anyone's.
READ_ONLY_ROOTS = ("/usr", "/etc", "/opt", "/sys")

#: On a merged-/usr system these are symlinks into /usr and are recreated as
#: such; elsewhere they are directories and are bound read-only.
TOP_LEVEL_LINKS = ("/bin", "/sbin", "/lib", "/lib64", "/lib32")

#: The host settings a run inherits: how to reach the network from this
#: machine, and how to read text. A proxy that intercepts TLS is useless
#: without its CA, so the file-valued ones are also bound into the jail.
PASS_THROUGH = (
    "LANG", "LC_ALL", "LC_CTYPE", "TZ",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
    "NODE_EXTRA_CA_CERTS", "PLAYWRIGHT_BROWSERS_PATH",
)
_PATH_VALUED = {"SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
                "NODE_EXTRA_CA_CERTS", "PLAYWRIGHT_BROWSERS_PATH"}

#: How a process finds a host by name. /etc is mounted whole, but on a
#: desktop the real resolv.conf is rarely there: systemd-resolved (Arch, so
#: Omarchy; Ubuntu; Fedora), NetworkManager and Debian's resolvconf keep it
#: in /run and link /etc/resolv.conf to it — and the jail's /run is its own,
#: empty one. The link dangled, and every run died at its first lookup with
#: "Temporary failure in name resolution". So the file each of these names
#: resolves to is mounted at its own path: the one file, never its folder,
#: which beside it holds resolved's control socket.
NAME_SERVICE = ("/etc/resolv.conf", "/etc/hosts", "/etc/nsswitch.conf", "/etc/host.conf",
                "/etc/gai.conf")

HOME = "/home/agent"
LOG_TAIL = 16000


def _bwrap() -> str | None:
    return shutil.which("bwrap")


def _under(path: str, roots) -> bool:
    return any(path == r or path.startswith(r.rstrip("/") + "/") for r in roots)


def _protected() -> list[str]:
    """What an operator's extra bind may never expose: the folder holding
    yaaf's database (and, under `yaaf up`, its master key), and a home folder
    whole. Binding either would undo the reason the jail exists. The home is
    named both ways because binds are compared resolved, and on Fedora's
    atomic desktops /home is itself a link to /var/home."""
    found = list(dict.fromkeys([str(Path.home()), str(Path.home().resolve())]))
    url = settings().database_url
    if url.startswith("sqlite:///"):
        found.append(str(Path(url.removeprefix("sqlite:///")).expanduser().resolve().parent))
    return found


def _exposes(path: str) -> list[str]:
    """The protected folders a bind of `path` would put inside the jail."""
    return [p for p in _protected() if _under(p, [path])]


def extra_binds() -> list[str]:
    """``host_jail_binds``, minus anything that would expose what the jail
    hides. A path that contains a protected folder is refused and logged, not
    bound — the mode keeps working, without the hole."""
    wanted = [p for p in settings().host_jail_binds.split(os.pathsep) if p.strip()]
    kept: list[str] = []
    for raw in wanted:
        path = str(Path(raw.strip()).expanduser().resolve())
        exposed = _exposes(path)
        if exposed:
            log.warning("host_jail_binds: refused %s, it would expose %s", path,
                        ", ".join(exposed))
            continue
        if Path(path).exists():
            kept.append(path)
    return kept


def _python_roots() -> list[str]:
    """The interpreter the runner runs on and its libraries: a virtualenv is
    sys.prefix, the Python it was made from is sys.base_prefix, and the
    executable may resolve to somewhere else again (a version manager).
    Resolved, so a bind lands on the folder itself; the names that lead to it
    are recreated by `_link_args`. One that is gone is left out: bwrap refuses
    the whole jail over a missing source, and `_starts_python` says it better."""
    roots = {str(Path(r).resolve()) for r in
             (sys.prefix, sys.base_prefix, sys.exec_prefix, Path(sys.executable).resolve().parent.parent)
             if r}
    return sorted(r for r in roots if Path(r).exists() and not _under(r, READ_ONLY_ROOTS))


def _venv_home() -> list[str]:
    """The `home` a virtualenv's pyvenv.cfg names: the interpreter inside the
    jail reads it on startup to find the standard library, and uv writes it as
    its `cpython-3.13` alias, not the folder that alias points to."""
    cfg = Path(sys.prefix) / "pyvenv.cfg"
    try:
        lines = cfg.read_text().splitlines()
    except OSError:
        return []
    for line in lines:
        key, sep, value = line.partition("=")
        if sep and key.strip() == "home" and value.strip():
            return [value.strip()]
    return []


def _links(name: str, *, hops: int = 40) -> list[tuple[str, str]]:
    """Every symlink the kernel follows to open `name`, in order, as (where
    the link is, what it says) — the same walk, one component at a time, so
    `..` after a link means what it means to the kernel. Stops where a
    component does not exist, and after `hops` links, as the kernel does."""
    found: list[tuple[str, str]] = []
    todo = list(Path(name).parts[1:]) if os.path.isabs(name) else []
    here = Path("/")
    while todo and len(found) < hops:
        part = todo.pop(0)
        if part == "..":
            here = here.parent
            continue
        step = here / part
        if not step.is_symlink():
            if not step.exists():
                break
            here = step
            continue
        target = os.readlink(step)
        found.append((str(step), target))
        if os.path.isabs(target):
            here = Path("/")
        todo = [*Path(target).parts[1 if os.path.isabs(target) else 0:], *todo]
    return found


#: What the jail itself puts at these paths. A host link at one of them, or
#: above one, cannot be recreated without unmaking the jail's own layout.
_JAIL_OWN = ("/proc", "/dev", "/tmp", "/run", HOME, "/workspace")


def _link_args(names: list[str], bound: list[str]) -> list[str]:
    """`--symlink` for each link on the way from `names` to their files.

    The jail binds folders at their resolved paths, so a name that reaches its
    file through a link — uv's `cpython-3.13` pointing at `cpython-3.13.18`, a
    venv whose `bin/python` points into that alias, /home on a system where it
    links to /var/home — would dangle inside, and every run die at exec. A link
    is only a name: recreating one shows nothing that is not already bound."""
    visible = (*READ_ONLY_ROOTS, *TOP_LEVEL_LINKS, *bound)
    made: dict[str, str] = {}
    for name in names:
        for link, target in _links(name):
            if link in made or _under(link, visible):
                continue
            if _under(link, _JAIL_OWN) and not _under(link, ("/tmp", "/run")) \
                    or any(_under(own, [link]) for own in _JAIL_OWN):
                log.warning("host jail: cannot recreate %s -> %s, it is where the jail "
                            "puts its own files", link, target)
                continue
            made[link] = target
    return [arg for link, target in made.items() for arg in ("--symlink", target, link)]


def jail_path(extra: list[str] | None = None) -> str:
    """PATH inside the jail: the runner's own interpreter first — the image
    puts its venv first for the same reason, so the `python3` an agent runs is
    the one with the libraries — then the system, then the bin folder of any
    extra bind."""
    dirs = [str(Path(sys.executable).parent), "/usr/local/bin", "/usr/bin", "/bin"]
    for path in extra or []:
        p = Path(path)
        if p.name == "bin":
            dirs.append(str(p))
        elif (p / "bin").is_dir():
            dirs.append(str(p / "bin"))
        else:
            # A tool installed as one binary at the top of its folder — how
            # mise lays out Claude Code. Bound to be found, so it is on PATH.
            dirs.append(str(p))
    return ":".join(dict.fromkeys(dirs))


def jail_env(job_env: dict[str, str] | None = None) -> dict[str, str]:
    """The run's whole environment. Built from an empty dict on purpose: the
    runner's own environment holds YAAF_MASTER_KEY and YAAF_DATABASE_URL, and
    your shell's may hold your own provider keys. A run pays with the
    project's credential, which arrives in `job_env`, or not at all."""
    env = {
        "PATH": jail_path(extra_binds()),
        "HOME": HOME,
        "USER": "agent",
        "LANG": "C.UTF-8",
        "PYTHONUNBUFFERED": "1",
    }
    env.update({k: os.environ[k] for k in PASS_THROUGH if os.environ.get(k)})
    env.update(job_env or {})
    return env


def _name_service() -> tuple[list[str], list[str]]:
    """The files name lookup needs mounted, and the names they are opened by.
    One that resolves into a read-only root is already there and is left out
    of the mounts by the caller; a dangling one is the host's own problem."""
    files, names = [], []
    for name in NAME_SERVICE:
        if os.path.exists(name):
            files.append(str(Path(name).resolve()))
            names.append(name)
    return files, names


def jail_argv(workspace_dir: str, command: list[str], *, entries: bool = False,
              project_dir: str | None = None) -> list[str]:
    """The bwrap command line for one run. Order matters to bwrap: a later
    mount lands on top of an earlier one, so the private binds come after the
    roots and the tmpfs they would otherwise be hidden under."""
    bwrap = _bwrap() or "bwrap"
    argv = [bwrap, "--die-with-parent", "--new-session",
            "--unshare-pid", "--unshare-ipc", "--unshare-uts", "--unshare-cgroup-try"]
    for root in READ_ONLY_ROOTS:
        if Path(root).exists():
            argv += ["--ro-bind", root, root]
    for top in TOP_LEVEL_LINKS:
        if os.path.islink(top):
            argv += ["--symlink", os.readlink(top), top]
        elif Path(top).is_dir():
            argv += ["--ro-bind", top, top]
    argv += ["--proc", "/proc", "--dev", "/dev",
             "--tmpfs", "/tmp", "--tmpfs", "/run", "--dir", HOME]
    extras = extra_binds()
    private = [*_python_roots(), *extras]
    # The names a run opens by: the interpreter's own path and prefixes, the
    # home its venv names, the CA files its environment points at.
    names = [sys.executable, sys.prefix, sys.base_prefix, sys.exec_prefix, *_venv_home()]
    lookup_files, lookup_names = _name_service()
    private += lookup_files
    names += lookup_names
    for name in PASS_THROUGH:
        value = os.environ.get(name)
        if name in _PATH_VALUED and value and Path(value).exists():
            private.append(str(Path(value).resolve()))
            names.append(value)
    bound: list[str] = []
    for path in dict.fromkeys(private):
        if _under(path, READ_ONLY_ROOTS):
            continue
        if _exposes(path):
            # Python installed over the data folder, or a CA file in it: the run
            # loses that path, which fails loudly, rather than seeing the key.
            log.warning("host jail: not binding %s, it would expose %s", path,
                        ", ".join(_exposes(path)))
            continue
        argv += ["--ro-bind", path, path]
        bound.append(path)
    argv += _link_args(names, bound)
    if entries:
        for name, source in ENTRY_FILES.items():
            argv += ["--ro-bind", str(source), f"{ENTRY_DIR}/{name}"]
    if project_dir:
        # The resident chat session's view of the project's working tree,
        # read-only — the container lane mounts it the same way, for the same
        # reason (sandbox.start_chat_container).
        argv += ["--ro-bind", project_dir, "/project"]
    # The user bus systemd-run needed to make the run's scope: the agent has
    # no use for it, and its socket is not mounted here anyway.
    argv += ["--unsetenv", "DBUS_SESSION_BUS_ADDRESS", "--unsetenv", "XDG_RUNTIME_DIR"]
    argv += ["--bind", workspace_dir, "/workspace", "--chdir", "/workspace", "--", *command]
    return argv


@functools.lru_cache(maxsize=4)
def _probe(bwrap: str) -> str | None:
    """Can this machine make a jail at all? Installed is not enough: a kernel
    or an AppArmor policy that forbids unprivileged user namespaces refuses
    every one, and that should be said once, by name, not as a dead run."""
    with tempfile.TemporaryDirectory() as scratch:
        try:
            proc = subprocess.run(jail_argv(scratch, ["true"]), capture_output=True,
                                  text=True, timeout=20, env={"PATH": "/usr/bin:/bin"})
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"bubblewrap could not start: {exc!r}"
    if proc.returncode != 0:
        return f"bubblewrap is installed but cannot make a sandbox here: {proc.stderr.strip()[:400]}"
    return None


@functools.lru_cache(maxsize=4)
def _starts_python(bwrap: str, executable: str) -> str | None:
    """Can a jail start the Python every agent run starts on? A jail that
    makes but cannot exec its interpreter is a mode where every run dies with
    one line of bwrap in its log; that should be one refusal, said once."""
    with tempfile.TemporaryDirectory() as scratch:
        try:
            proc = subprocess.run(jail_argv(scratch, [executable, "-c", "import encodings"]),
                                  capture_output=True, text=True, timeout=30,
                                  env={"PATH": "/usr/bin:/bin"})
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"the sandbox could not start {executable}: {exc!r}"
    if proc.returncode != 0:
        return (f"the sandbox cannot start the Python yaaf runs on ({executable}), so no "
                f"agent run can start either: {proc.stderr.strip()[-400:]}\nIf it lives "
                "somewhere the jail does not mount, add that folder to host_jail_binds "
                "(docs/DEPLOYMENT.md, Host mode).")
    return None


@functools.lru_cache(maxsize=4)
def _reads_resolver(bwrap: str, resolver: str) -> str | None:
    """Can a jail read this machine's DNS settings? Every agent run's first
    act is to reach its model's API by name, so a jail that cannot is a mode
    where every run dies at its first lookup — said once, naming the file."""
    if not os.path.exists(resolver):
        return None  # the host has none to share; nothing a jail could lack
    with tempfile.TemporaryDirectory() as scratch:
        try:
            proc = subprocess.run(jail_argv(scratch, ["cat", resolver]), capture_output=True,
                                  text=True, timeout=20, env={"PATH": "/usr/bin:/bin"})
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"the sandbox could not read {resolver}: {exc!r}"
    if proc.returncode == 0:
        return None
    where = f"{resolver} → {os.path.realpath(resolver)}" if os.path.islink(resolver) else resolver
    return (f"the sandbox cannot read this machine's DNS settings ({where}), so no agent "
            f"run could reach its model's API: {proc.stderr.strip()[-300:]}")


def unavailable() -> str | None:
    """Why host mode cannot run anything on this machine, or None."""
    bwrap = _bwrap()
    if bwrap is None:
        return ("Refused: sandbox_mode is 'host', which runs every agent inside "
                "bubblewrap, and `bwrap` is not installed. Install it (Arch: "
                "`pacman -S bubblewrap`, Debian/Ubuntu: `apt install bubblewrap`) "
                "or use sandbox_mode=docker. An agent is never run on the host "
                "without it.")
    problem = _probe(bwrap)
    if problem:
        return f"Refused: sandbox_mode is 'host' and {problem}" + _restricted_userns()
    problem = (_starts_python(bwrap, sys.executable)
               or _reads_resolver(bwrap, NAME_SERVICE[0]))
    if problem:
        return f"Refused: sandbox_mode is 'host' and {problem}"
    return None


#: Ubuntu's switch for its user-namespace restriction (1 is on, its default).
USERNS_RESTRICTION = Path("/proc/sys/kernel/apparmor_restrict_unprivileged_userns")


def _restricted_userns() -> str:
    """Ubuntu 23.10 and later move a program that creates a user namespace
    into an AppArmor profile with no capabilities, unless a profile of its own
    grants `userns` — and Ubuntu ships none for bwrap. Installing bwrap again
    cannot fix that, so the refusal says what can."""
    try:
        restricted = USERNS_RESTRICTION.read_text().strip() == "1"
    except OSError:
        return ""
    if not restricted:
        return ""
    return ("\nThis system restricts user namespaces with AppArmor (Ubuntu 23.10 and "
            "later), and has no AppArmor profile letting bwrap create one. Either give "
            "bwrap one — Ubuntu's own pattern, as it does for flatpak and podman — or lift "
            "the restriction for the whole machine (sudo sysctl "
            "kernel.apparmor_restrict_unprivileged_userns=0). Both reopen user namespaces, "
            "which Ubuntu restricts to shrink the kernel's attack surface; or use "
            "sandbox_mode=docker. docs/DEPLOYMENT.md, Host mode, has the profile.")


def _scope_env() -> dict[str, str]:
    """What systemd-run needs to find this user's manager — never more."""
    return {k: os.environ[k] for k in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS")
            if os.environ.get(k)}


def _scope_properties() -> list[str]:
    cfg = settings()
    props = []
    if cfg.host_run_memory_gb > 0:
        props += ["-p", f"MemoryMax={int(cfg.host_run_memory_gb * 1024)}M",
                  "-p", "MemorySwapMax=0"]
    if cfg.host_run_cpus > 0:
        props += ["-p", f"CPUQuota={int(cfg.host_run_cpus * 100)}%"]
    return props


@functools.lru_cache(maxsize=8)
def _scope_works(props: tuple[str, ...]) -> str | None:
    """Can this machine cap a run? A user systemd with the cpu and memory
    controllers delegated can; a container, a non-systemd distro or a bare
    TTY session may not. Asked once, and said by name, not as a dead run."""
    systemd_run = shutil.which("systemd-run")
    if systemd_run is None:
        return "systemd-run is not installed"
    try:
        proc = subprocess.run([systemd_run, "--user", "--scope", "--quiet", "--collect",
                               *props, "--", "true"], capture_output=True, text=True,
                              timeout=20, env={"PATH": "/usr/bin:/bin", **_scope_env()})
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"systemd-run could not start: {exc!r}"
    if proc.returncode != 0:
        return f"systemd-run cannot make a capped scope here: {proc.stderr.strip()[:300]}"
    return None


def limits_problem() -> str | None:
    """Why runs on this machine go uncapped though caps are set, or None."""
    props = tuple(_scope_properties())
    return _scope_works(props) if props else None


def limits_prefix() -> list[str]:
    """The command that puts one run in its own capped scope: Docker's 2 CPUs
    and 2 GB per run, unless the operator chose other numbers at setup. Where
    the machine cannot do it the run goes uncapped, with one warning and a line
    in `yaaf doctor` — refusing every run over a missing cap would be the
    brake eating the thing it exists to protect."""
    props = tuple(_scope_properties())
    if not props:
        return []
    problem = _scope_works(props)
    if problem:
        _warn_uncapped(problem)
        return []
    return ["systemd-run", "--user", "--scope", "--quiet", "--collect", *props, "--"]


@functools.lru_cache(maxsize=1)
def _warn_uncapped(problem: str) -> None:
    log.warning("host jail: runs are not capped (%s); `yaaf doctor` says how to fix it", problem)


def _tail(handle) -> str:
    handle.flush()
    size = os.fstat(handle.fileno()).st_size
    # Bytes, not characters: four per character covers any UTF-8 the tail holds.
    handle.seek(max(0, size - LOG_TAIL * 4))
    return handle.read().decode(errors="replace")[-LOG_TAIL:]


def run(command: list[str], *, workspace_dir: str, env: dict[str, str] | None,
        timeout_s: int, stop_event=None, entries: bool = False,
        timeout_note: str | None = None) -> tuple[int, str]:
    """Run `command` in a fresh jail; return (exit_code, output).

    The exit codes match the container lane, because deaths.py reads them:
    124 is the runner's own timeout, 137 a kill. Killing bwrap is enough to end
    the whole run — `--die-with-parent` takes its child down with it, and the
    pid namespace takes everything that child started.
    """
    problem = unavailable()
    if problem:
        log.warning("%s", problem.splitlines()[0])
        return 126, problem
    Path(workspace_dir).mkdir(parents=True, exist_ok=True)
    argv = limits_prefix() + jail_argv(workspace_dir, command, entries=entries)
    deadline = time.monotonic() + timeout_s
    with tempfile.TemporaryFile() as out:
        proc = subprocess.Popen(argv, stdout=out, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, env={**jail_env(env), **_scope_env()},
                                start_new_session=True)
        try:
            while True:
                try:
                    code = proc.wait(timeout=1)
                    # Killed by a signal — the memory cap's OOM kill among them —
                    # in the shell's spelling the container lane reports and
                    # deaths.py reads: 128 + the signal, not Popen's negative.
                    return (128 - code if code < 0 else code), _tail(out)
                except subprocess.TimeoutExpired:
                    if stop_event is not None and stop_event.is_set():
                        _kill(proc)
                        return 137, f"agent run stopped: token revoked\n{_tail(out)}"
                    if time.monotonic() >= deadline:
                        _kill(proc)
                        note = timeout_note or f"agent run killed at {timeout_s}s timeout"
                        return 124, f"{note}\n{_tail(out)}"
        finally:
            if proc.poll() is None:
                _kill(proc)


def _kill(proc: subprocess.Popen) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGKILL)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        log.warning("bwrap %s did not exit after SIGKILL", proc.pid)


def run_agent(*, entrypoint: str, workspace_dir: str, env: dict[str, str],
              timeout_s: int, stop_event=None) -> tuple[int, str]:
    """An agent run: the same entry script the image runs, on this Python."""
    if entrypoint not in ENTRY_FILES:
        return 126, f"Refused: {entrypoint!r} is not an entry script this runner ships."
    from yaaf.runner import host_tools

    refused = host_tools.refusal_for(entrypoint, jail_path(extra_binds()))
    if refused:
        log.warning("%s", refused)
        return 126, refused
    return run([sys.executable, f"{ENTRY_DIR}/{entrypoint}"], workspace_dir=workspace_dir,
               env=env, timeout_s=timeout_s, stop_event=stop_event, entries=True)


def run_check(command: str, workspace_dir: str, timeout_s: int) -> tuple[int, str]:
    """A success test or acceptance test: an agent-authored shell command, so
    it gets the jail and no credentials at all — the job env is empty."""
    code, output = run(["bash", "-lc", command], workspace_dir=workspace_dir, env=None,
                       timeout_s=timeout_s,
                       timeout_note=f"success test timed out after {timeout_s}s")
    return code, output[-8000:]


# ── the chat lane ───────────────────────────────────────────────────────────────
# The resident CEO session, in a jail that stays up instead of a container.
# Docker finds its chat containers by label after a runner restart; a jail made
# with --die-with-parent dies with the runner instead, so there is nothing to
# re-adopt and this table is the whole record: one live session per project.
# The supervisor reads a missing entry as a lost session and starts a new one,
# exactly as it does for a container that vanished.

_chats: dict[str, tuple[str, subprocess.Popen, Path, float]] = {}


def _chat_log(project_id: str) -> Path:
    folder = Path(settings().workspaces_root).parent / "chat-logs"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{project_id}.log"


def start_chat(*, project_id: str, run_id: str, workspace_dir: str, env: dict[str, str],
               project_dir: str | None = None) -> str:
    """Start the resident chat entry, detached; returns the jail's pid. Same
    jail, caps and entry scripts as a run — the talk lane gets a different
    entry and no timeout, and nothing else."""
    problem = unavailable()
    if problem:
        raise RuntimeError(problem)
    from yaaf.runner import host_tools

    refused = host_tools.refusal_for(env.get("YAAF_CHAT_ENTRY", ""), jail_path(extra_binds()))
    if refused:
        raise RuntimeError(refused)
    stop_chat(project_id)
    Path(workspace_dir).mkdir(parents=True, exist_ok=True)
    argv = limits_prefix() + jail_argv(workspace_dir, [sys.executable,
                                                       f"{ENTRY_DIR}/chat_entry.py"],
                                       entries=True, project_dir=project_dir)
    path = _chat_log(project_id)
    with path.open("wb") as out:
        proc = subprocess.Popen(argv, stdout=out, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL,
                                env={**jail_env(env), **_scope_env()},
                                start_new_session=True)
    _chats[project_id] = (run_id, proc, path, time.time())
    return str(proc.pid)


def chat_run_ids(project_id: str) -> set[str]:
    """The run id of the project's session when its jail is still running."""
    entry = _chats.get(project_id)
    if entry is None or entry[1].poll() is not None:
        return set()
    return {entry[0]}


def stop_chat(project_id: str) -> str:
    """Stop the project's session; returns what it died of and its log tail —
    the only chance to read why, as with a container's corpse."""
    entry = _chats.pop(project_id, None)
    if entry is None:
        # The session died with a runner that has since restarted. Its log is
        # still on disk, as a dead container's is in Docker: read it once, and
        # set it aside so the next stop does not report it again.
        path = _chat_log(project_id)
        if not path.exists():
            return ""
        try:
            tail = path.read_bytes()[-LOG_TAIL * 4:].decode(errors="replace")[-LOG_TAIL:]
        finally:
            path.replace(path.with_suffix(".previous.log"))
        return ("exit=unknown (the runner restarted) jail=bubblewrap\n" + tail).strip()
    run_id, proc, path, started = entry
    if proc.poll() is None:
        _kill(proc)
    try:
        tail = path.read_bytes()[-LOG_TAIL * 4:].decode(errors="replace")[-LOG_TAIL:]
    except OSError:
        tail = ""
    header = (f"exit={proc.returncode} run={run_id} "
              f"uptime={time.time() - started:.0f}s jail=bubblewrap")
    return (header + "\n" + tail).strip()
