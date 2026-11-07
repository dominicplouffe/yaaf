"""What a native install's agents run with, held against what the image ships.

Docker mode runs every agent on one image whose tools are pinned
(deploy/sandbox/Dockerfile). Host mode runs them on this machine's own — so the
same seat can be on Codex 0.162 on the server and 0.120 on a laptop, or find
no Gemini at all, and the difference surfaced as a run that died on its first
command. The Python libraries are not the problem: they live in yaaf's own
virtualenv and are pinned by pyproject. The command-line tools are, because
they live wherever the machine put them.

So each tool the image carries has a floor here, the image's own version —
`test_host_tools` holds this table to the Dockerfile, so a pin raised there
cannot be left behind — and `yaaf doctor` (which `yaaf setup` ends with) says
which are missing or older. Looked up on the jail's PATH, not the shell's: a
CLI in a folder the jail does not mount is as missing to an agent as one that
was never installed.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from importlib.util import find_spec
from pathlib import Path


@dataclass(frozen=True)
class Tool:
    command: str
    #: The image's version, or None where the image takes the latest and
    #: there is no number to hold a machine to — then it only has to exist.
    minimum: str | None
    #: What goes wrong without it, in the words a person installing reads.
    needed_for: str
    install: str


#: Every tool the sandbox image installs that an agent or its harness calls.
TOOLS: tuple[Tool, ...] = (
    Tool("node", "22", "the Claude Code, Codex, opencode and Gemini harnesses",
         "install Node.js 22 or newer (Arch: pacman -S nodejs)"),
    Tool("claude", None, "seats on the Claude Code harness",
         "npm install -g @anthropic-ai/claude-code"),
    Tool("codex", "0.162.1", "seats on the Codex harness",
         "npm install -g @openai/codex@0.162.1"),
    Tool("opencode", "1.19.0", "seats on the opencode harness",
         "npm install -g opencode-ai@1.19.0"),
    Tool("gemini", "0.63.0", "seats on the Gemini harness",
         "npm install -g @google/gemini-cli@0.63.0"),
    Tool("git", None, "agents that commit their work", "install git"),
    Tool("curl", None, "agents and success tests that fetch a URL", "install curl"),
    Tool("jq", None, "agents and success tests that read JSON", "install jq"),
    Tool("gawk", None, "agents and success tests that use awk", "install gawk"),
)

_VERSION = re.compile(r"(\d+(?:\.\d+)+|\d+)")


def _parts(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in version.split("."))


def older(found: str, minimum: str) -> bool:
    """Whether `found` is below `minimum`, comparing as many parts as the
    minimum names: a floor of "22" is met by 22.4.1."""
    want = _parts(minimum)
    have = _parts(found)[:len(want)]
    return have + (0,) * (len(want) - len(have)) < want


def version_of(path: str) -> str | None:
    """The first version number `<tool> --version` prints, or None."""
    try:
        proc = subprocess.run([path, "--version"], capture_output=True, text=True,
                              timeout=20, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = _VERSION.search(proc.stdout + proc.stderr)
    return match.group(1) if match else None


def report(path: str | None = None) -> list[dict]:
    """Each tool as an agent would find it: where, which version, and what is
    wrong with it if anything. `path` is the PATH to search (the jail's)."""
    if path is None:
        from yaaf.runner import host_jail

        path = host_jail.jail_path(host_jail.extra_binds())
    rows = []
    for tool in TOOLS:
        where = shutil.which(tool.command, path=path)
        found = version_of(where) if where else None
        if where is None:
            problem = "missing"
        elif tool.minimum and found and older(found, tool.minimum):
            problem = "old"
        else:
            problem = None
        rows.append({"tool": tool, "path": where, "version": found, "problem": problem})
    return rows


def browsers_path() -> Path:
    """Where Playwright keeps its browsers: the variable when it is set,
    Playwright's own default otherwise."""
    value = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    return Path(value).expanduser() if value else Path.home() / ".cache" / "ms-playwright"


@lru_cache(maxsize=1)
def browser_available() -> bool:
    """Can an agent run on this machine drive Chromium through Playwright?

    The image always can. Outside it, only when the library is in yaaf's
    virtualenv and a Chromium build sits where Playwright looks — the agent
    was told the browser was there on every native install, and spent its run
    finding out that it was not."""
    from yaaf.core.config import settings

    if getattr(settings(), "sandbox_mode", "docker") == "docker":
        return True
    if find_spec("playwright") is None:
        return False
    root = browsers_path()
    return root.is_dir() and any(root.glob("chromium*"))


#: The CLI each harness's entry script drives. yaaf's own loop and the Agent
#: SDK run on the virtualenv's Python and need none.
ENTRY_TOOL = {
    "claude_cli_entry.py": "claude",
    "codex_cli_entry.py": "codex",
    "opencode_cli_entry.py": "opencode",
    "gemini_cli_entry.py": "gemini",
}

_checked: dict[str, tuple[float, str | None]] = {}
_CHECK_TTL_S = 60.0


def refusal_for(entrypoint: str, path: str) -> str | None:
    """Why a run on this entry cannot start here, or None. Missing or older
    than the image's floor is refused before the jail starts, by name and with
    the fix, rather than dying on its first command three layers down. Asked
    at most once a minute per tool: `--version` is a process start."""
    import time

    command = ENTRY_TOOL.get(entrypoint)
    if command is None:
        return None
    hit = _checked.get(command)
    if hit is not None and time.monotonic() - hit[0] < _CHECK_TTL_S:
        return hit[1]
    tool = next(t for t in TOOLS if t.command == command)
    row = next(r for r in report(path) if r["tool"].command == command)
    if row["problem"] == "missing":
        why = (f"Refused: this seat runs on {command}, and no {command} is installed "
               f"where agents look. {tool.install}, then run yaaf setup again so the "
               "sandbox can see it.")
    elif row["problem"] == "old":
        why = (f"Refused: this seat runs on {command} {row['version']}, older than the "
               f"{tool.minimum} the sandbox image runs. {tool.install}.")
    else:
        why = None
    _checked[command] = (time.monotonic(), why)
    return why
