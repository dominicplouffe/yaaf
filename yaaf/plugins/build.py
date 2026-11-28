"""Building a plugin's dependencies, and running its tests, in a sandbox (§5.2).

A plugin that needs packages ships `requirements.lock`, in pip's hash format,
listing every package it imports, transitive ones included. The build
installs exactly those, `--require-hashes --no-deps --target <site>`, so a
lock that leaves a dependency out fails at build time, not at a user's first
gate. A plugin with no dependencies ships no lock and builds nothing.

The build has network (pip must reach the index); the hosts it produces never
do. Where it runs follows the sandbox mode, as plugin hosts do:

* ``subprocess`` — a plain process (development and CI only);
* ``host`` — a bubblewrap jail: the checkout read-only, the target directory
  the one writable mount, the network on (the build's only need);
* ``docker`` — a container the runner's broker starts (`broker` op "build"),
  with the same mounts. The Docker daemon mounts host paths, so the runner's
  own read-only view of the plugins root does not stop the build writing.

The plugin's own tests (MIN-16) run the same three ways, with the network off.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from yaaf.core.config import settings

LOCK = "requirements.lock"
SITE = ".site"
BUILD_TIMEOUT_S = 900
TESTS_TIMEOUT_S = 300
_PIN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9,._-]+\])?==[A-Za-z0-9.!+_-]+"
                  r"(\s*;[^\\]*)?$")


def declared_dependencies(root: Path) -> list[str]:
    import tomllib

    path = root / "pyproject.toml"
    if not path.is_file():
        return []
    try:
        deps = tomllib.loads(path.read_text("utf-8")).get("project", {}).get("dependencies")
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        return ["<pyproject.toml could not be read>"]
    return [str(d) for d in deps or [] if not str(d).startswith("yaaf-plugin-api")]


def lock_entries(root: Path) -> tuple[list[str], list[str]]:
    """(requirement lines, problems): every line pinned with == and a sha256 hash."""
    path = root / LOCK
    if not path.is_file():
        return [], []
    text = path.read_text("utf-8", "replace").replace("\\\n", " ")
    entries, problems = [], []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("-"):
            problems.append(f"{LOCK}: {line.split()[0]} is not allowed; the lock lists packages "
                            "only, and the build decides where they come from")
            continue
        spec, *hashes = [part.strip() for part in line.split("--hash=")]
        if not _PIN.match(spec):
            problems.append(f"{LOCK}: {spec!r} is not pinned to one version with ==")
        if not hashes or not all(h.startswith("sha256:") and len(h) == 71 for h in hashes):
            problems.append(f"{LOCK}: {spec} has no sha256 hash")
        entries.append(line)
    return entries, problems


def min_03(root: Path) -> list[str]:
    """MIN-03: every dependency is pinned with a hash."""
    declared = declared_dependencies(root)
    entries, problems = lock_entries(root)
    if declared and not entries:
        problems.append(f"pyproject.toml declares {', '.join(declared)}, and there is no "
                        f"{LOCK}: pin each one, and everything it needs, with a hash")
    names = {re.split(r"[=<>!~;\[ ]", e, maxsplit=1)[0].lower().replace("_", "-")
             for e in entries}
    for dep in declared:
        name = re.split(r"[=<>!~;\[ ]", dep, maxsplit=1)[0].lower().replace("_", "-")
        if entries and name not in names:
            problems.append(f"{dep} is declared in pyproject.toml but not in {LOCK}")
    return problems


def _pip_command(python: str, root: str, site: str) -> list[str]:
    extra = settings().plugin_pip_args.split() if settings().plugin_pip_args else []
    return [python, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
            "--no-input", "--require-hashes", "--no-deps", "--only-binary=:all:",
            "--target", site, "-r", f"{root}/{LOCK}", *extra]


def _run(argv: list[str], *, cwd: str, timeout: int, env: dict | None = None
         ) -> tuple[int, str]:
    try:
        proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                              env=env or {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
                                          "HOME": cwd, "PIP_NO_CACHE_DIR": "1"})
    except subprocess.TimeoutExpired:
        return 124, f"took longer than {timeout}s"
    return proc.returncode, (proc.stdout + proc.stderr)[-8000:]


def build(root: Path) -> tuple[bool, str, Path | None]:
    """(ok, log, the site directory or None when nothing was needed)."""
    entries, _ = lock_entries(root)
    if not entries:
        return True, "no dependencies to build", None
    site = root / SITE
    site.mkdir(exist_ok=True)
    mode = getattr(settings(), "sandbox_mode", "docker")
    if mode == "docker":
        from yaaf.plugins import broker_client

        code, log = broker_client.run_job("build", root, site, network=True,
                                          timeout_s=BUILD_TIMEOUT_S)
    elif mode == "host":
        code, log = _jailed(_pip_command(sys.executable, str(root), str(site)), root, site,
                            network=True, timeout=BUILD_TIMEOUT_S)
    else:
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8",
               "HOME": str(site), "PIP_NO_CACHE_DIR": "1"}
        for name in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy",
                     "PIP_INDEX_URL", "SSL_CERT_FILE", "PIP_CERT", "REQUESTS_CA_BUNDLE"):
            if os.environ.get(name):
                env[name] = os.environ[name]
        code, log = _run(_pip_command(sys.executable, str(root), str(site)), cwd=str(root),
                         timeout=BUILD_TIMEOUT_S, env=env)
    return code == 0, log, site if code == 0 else None


def run_tests(root: Path, site: Path | None) -> tuple[str, list[str]]:
    """MIN-16: the plugin's own tests, sandboxed, network off."""
    mode = getattr(settings(), "sandbox_mode", "docker")
    if mode == "subprocess":
        from yaaf_plugin_api import client

        return client.run_tests(root, extra_paths=[site] if site else [])
    if mode == "host":
        from yaaf_plugin_api.client import API_SRC, plugin_paths

        paths = [str(API_SRC), *map(str, plugin_paths(root)), *([str(site)] if site else [])]
        code, log = _jailed([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                             str(root / "tests")], root, site, network=False,
                            timeout=TESTS_TIMEOUT_S, pythonpath=paths)
    else:
        from yaaf.plugins import broker_client

        code, log = broker_client.run_job("tests", root, site, network=False,
                                          timeout_s=TESTS_TIMEOUT_S)
    tail = log.strip().splitlines()[-12:]
    if code == 0:
        return "pass", tail[-1:] if tail else []
    if code == 5:
        return "fail", ["tests/ collected no tests"]
    return "fail", ["the plugin's own tests failed:", *tail]


def _jailed(command: list[str], root: Path, site: Path | None, *, network: bool,
            timeout: int, pythonpath: list[str] | None = None) -> tuple[int, str]:
    from yaaf.runner import host_jail

    if host_jail.unavailable():
        return 126, f"the host jail is unavailable: {host_jail.unavailable()}"
    scratch = Path(root).parent / f".{Path(root).name}.scratch"
    scratch.mkdir(exist_ok=True)
    argv = host_jail.jail_argv(str(scratch), command)
    split = argv.index("--")
    binds = [] if network else ["--unshare-net"]
    binds += ["--ro-bind", str(root), str(root)]
    if site is not None:
        binds += ["--bind", str(site), str(site)]
    if pythonpath:
        from yaaf.plugins.host import API_SRC

        binds += ["--ro-bind", str(API_SRC), str(API_SRC),
                  "--setenv", "PYTHONPATH", os.pathsep.join(pythonpath)]
    try:
        return _run(argv[:split] + binds + argv[split:], cwd=str(scratch), timeout=timeout,
                    env=host_jail.jail_env({"PIP_NO_CACHE_DIR": "1"}))
    finally:
        import shutil

        shutil.rmtree(scratch, ignore_errors=True)
