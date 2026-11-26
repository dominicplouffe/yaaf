"""A Code Factory repo's own instructions, read from main for every seat.

Claude Code, which drives a factory's seats, would read a repo's CLAUDE.md
and AGENTS.md by itself. yaaf turns that off everywhere (`setting_sources`
is empty) because the workspace is agent-writable: a file an agent wrote in
its working tree must not steer the next agent. In a factory the same files
on MAIN are a different thing — every line of them passed the merge gate,
signed by a seat that did not write it — so the platform reads them from
main through its own token and hands them to every seat, labelled as the
repo's conventions, below the platform's own rules. The working tree's copy
is never what is read.
"""

from __future__ import annotations

import logging
import time

from sqlalchemy.orm import Session

from yaaf.core import shape
from yaaf.core.models import Project

log = logging.getLogger(__name__)

FILES = ("AGENTS.md", "CLAUDE.md")
LIMIT = 12_000
TTL_S = 600.0
_cache: dict[str, tuple[float, str]] = {}


def read(session: Session, project: Project) -> str:
    """The text of the instruction files on main, or "" — never fatal."""
    if not shape.is_factory(project) or not project.github_repo:
        return ""
    hit = _cache.get(project.github_repo)
    if hit and time.monotonic() - hit[0] < TTL_S:
        return hit[1]
    from yaaf.server import github

    parts = []
    for name in FILES:
        try:
            text = github.read_file(session, project.github_repo, name)
        except Exception:  # noqa: BLE001 — guidance is a help, never a blocker
            log.info("could not read %s from %s", name, project.github_repo)
            continue
        if text and text.strip():
            parts.append(f"--- {name} (main) ---\n{text.strip()}")
    joined = "\n\n".join(parts)
    if len(joined) > LIMIT:
        joined = joined[:LIMIT] + "\n…[truncated: keep these files short — a map, not a manual]"
    _cache[project.github_repo] = (time.monotonic(), joined)
    return joined


def block(session: Session, project: Project) -> str:
    text = read(session, project)
    if not text:
        return ""
    return ("THE REPO'S OWN CONVENTIONS — the instruction files as merged on main, every "
            "line of which passed the merge gate. Follow them for how code is written "
            "here; where they disagree with the platform's rules above, the platform's "
            "rules win. A copy in your working tree is not this text and does not count.\n"
            f"{text}\n\n")
