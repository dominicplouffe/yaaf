"""Fetching a plugin from a public GitHub repo, pinned to one commit (§5.2).

Only `https://github.com/<owner>/<repo>` (with an optional `/tree/<ref>`) is
accepted. The ref, a branch, a tag or a commit SHA, is resolved to a commit
once, with `git ls-remote`, and only that SHA is fetched, so a tag moved later
never changes what was installed. The fetch is shallow, never follows
submodules or LFS, never prompts for credentials (a private repo answers "not
found or not public"), and is capped in time and size. Symlinks are refused
by the static check that follows.

`settings.plugin_github_base` is where repos are fetched from: GitHub itself in
production; a directory of bare repos in tests, so the real git commands run
without a network.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from yaaf.core.config import settings

URL = re.compile(r"^https://github\.com/(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/"
                 r"(?P<repo>[A-Za-z0-9._-]{1,100}?)(?:\.git)?(?:/tree/(?P<ref>[^\s?#]{1,200}))?/?$")
SHA = re.compile(r"^[0-9a-f]{40}$")
REF = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")
GIT_TIMEOUT_S = 120
MAX_TREE_BYTES = 50 * 1024 * 1024


class FetchRefusal(Exception):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class Source:
    owner: str
    repo: str
    ref: str | None

    @property
    def url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}"

    @property
    def remote(self) -> str:
        base = settings().plugin_github_base.rstrip("/")
        return f"{base}/{self.owner}/{self.repo}"


def parse(url: str, ref: str | None = None) -> Source:
    m = URL.match((url or "").strip())
    if m is None or m.group("repo") in (".", ".."):
        raise FetchRefusal("a plugin is installed from https://github.com/<owner>/<repo>, "
                           "optionally with /tree/<branch, tag or commit>")
    wanted = ref or m.group("ref")
    if wanted is not None and (not REF.match(wanted) or ".." in wanted
                               or wanted.startswith(("-", "/"))):
        raise FetchRefusal(f"{wanted!r} is not a branch, tag or commit name")
    return Source(m.group("owner"), m.group("repo"), wanted)


def _git(args: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/nonexistent",
           "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/bin/false", "GIT_LFS_SKIP_SMUDGE": "1",
           "GIT_CONFIG_NOSYSTEM": "1", "LANG": "C"}
    for name in ("HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy",
                 "no_proxy", "SSL_CERT_FILE", "GIT_SSL_CAINFO"):
        if os.environ.get(name):
            env[name] = os.environ[name]
    try:
        return subprocess.run(["git", "-c", "protocol.file.allow=always", *args], cwd=cwd,
                              env=env, capture_output=True, text=True, timeout=GIT_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise FetchRefusal(f"git {args[0]} took longer than {GIT_TIMEOUT_S}s", 504) from None


def resolve(source: Source) -> str:
    """The commit SHA the ref names now. A SHA is taken as given (the fetch proves it)."""
    if source.ref and SHA.match(source.ref):
        return source.ref
    proc = _git(["ls-remote", "--", source.remote, *([source.ref] if source.ref else ["HEAD"])])
    if proc.returncode != 0:
        raise FetchRefusal(f"{source.url} was not found or is not public", 404)
    lines = [ln.split("\t") for ln in proc.stdout.splitlines() if "\t" in ln]
    if source.ref:
        wanted = {source.ref, f"refs/heads/{source.ref}", f"refs/tags/{source.ref}",
                  f"refs/tags/{source.ref}^{{}}"}
        # A peeled annotated tag names the commit; prefer it to the tag object.
        lines.sort(key=lambda parts: not parts[1].endswith("^{}"))
        found = next((sha for sha, name in lines if name in wanted), None)
    else:
        found = next((sha for sha, name in lines if name == "HEAD"), None)
    if not found or not SHA.match(found):
        raise FetchRefusal(f"{source.url} has no branch or tag {source.ref or 'HEAD'!r}", 404)
    return found


def fetch(source: Source, sha: str, dest: Path) -> None:
    """Exactly that commit's files into `dest` (which must not exist), with no .git."""
    dest.mkdir(parents=True)
    try:
        for args in (["init", "-q"], ["remote", "add", "origin", source.remote],
                     ["fetch", "-q", "--depth", "1", "--no-tags",
                      "--no-recurse-submodules", "origin", sha],
                     ["-c", "advice.detachedHead=false", "checkout", "-q", "FETCH_HEAD"]):
            proc = _git(args, cwd=str(dest))
            if proc.returncode != 0:
                raise FetchRefusal(f"commit {sha[:12]} of {source.url} could not be fetched: "
                                   f"{(proc.stderr or proc.stdout).strip()[:300]}", 404)
        head = _git(["rev-parse", "HEAD"], cwd=str(dest)).stdout.strip()
        if head != sha:
            raise FetchRefusal(f"fetched {head[:12]}, not the commit asked for ({sha[:12]})")
        shutil.rmtree(dest / ".git")
        total = sum(p.stat().st_size for p in dest.rglob("*") if p.is_file()
                    and not p.is_symlink())
        if total > MAX_TREE_BYTES:
            raise FetchRefusal(f"the repo at {sha[:12]} is larger than "
                               f"{MAX_TREE_BYTES // 2**20} MB")
    except BaseException:
        shutil.rmtree(dest, ignore_errors=True)
        raise
