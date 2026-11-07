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

- /usr, /etc, /opt and /sys, read-only; the Python yaaf runs on, read-only;
  any extra path the operator lists in ``host_jail_binds``, read-only.
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

HOME = "/home/agent"
LOG_TAIL = 16000


def _bwrap() -> str | None:
    return shutil.which("bwrap")


def _under(path: str, roots) -> bool:
    return any(path == r or path.startswith(r.rstrip("/") + "/") for r in roots)


def _protected() -> list[str]:
    """What an operator's extra bind may never expose: the folder holding
    yaaf's database (and, under `yaaf up`, its master key), and a home folder
    whole. Binding either would undo the reason the jail exists."""
    found = [str(Path.home())]
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
    executable may resolve to somewhere else again (a version manager)."""
    roots = {sys.prefix, sys.base_prefix, sys.exec_prefix,
             str(Path(sys.executable).resolve().parent.parent)}
    return sorted(r for r in roots if r and not _under(r, READ_ONLY_ROOTS))


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


def jail_argv(workspace_dir: str, command: list[str], *, entries: bool = False) -> list[str]:
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
    for name in PASS_THROUGH:
        value = os.environ.get(name)
        if name in _PATH_VALUED and value and Path(value).exists():
            private.append(str(Path(value).resolve()))
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
    if entries:
        for name, source in ENTRY_FILES.items():
            argv += ["--ro-bind", str(source), f"{ENTRY_DIR}/{name}"]
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
    argv = jail_argv(workspace_dir, command, entries=entries)
    deadline = time.monotonic() + timeout_s
    with tempfile.TemporaryFile() as out:
        proc = subprocess.Popen(argv, stdout=out, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, env=jail_env(env),
                                start_new_session=True)
        try:
            while True:
                try:
                    return proc.wait(timeout=1), _tail(out)
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
    return run([sys.executable, f"{ENTRY_DIR}/{entrypoint}"], workspace_dir=workspace_dir,
               env=env, timeout_s=timeout_s, stop_event=stop_event, entries=True)


def run_check(command: str, workspace_dir: str, timeout_s: int) -> tuple[int, str]:
    """A success test or acceptance test: an agent-authored shell command, so
    it gets the jail and no credentials at all — the job env is empty."""
    code, output = run(["bash", "-lc", command], workspace_dir=workspace_dir, env=None,
                       timeout_s=timeout_s,
                       timeout_note=f"success test timed out after {timeout_s}s")
    return code, output[-8000:]
