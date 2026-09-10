"""Resources — the stable documents, addressable by URI.

A convenience, never a dependency. claude.ai's connector calls tools and may
ignore resources entirely, so nothing here may be the only way to reach a fact:
every one of these is also answerable by a tool, and the tool is the contract.
What resources buy is a client that can re-read the plan cheaply without
spending a tool call to ask whether it changed.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from yaaf.core.constitution import effective, effective_version
from yaaf.server.api import observe
from yaaf.server.mcp import envelope
from yaaf.server.mcp.auth import Principal, ToolRefusal, chaired_projects, resolve_project

#: The goal grammar the Founder grades against, served so a drafting client can
#: read the rubric before it writes. Same file the grader loads.
GRAMMAR_PATH = Path(__file__).resolve().parents[2] / "founder" / "GOAL_GRAMMAR.md"

#: How a project is drafted, whichever door the drafter came through. Same file
#: the Founder's system prompt appends — the whole point is that this surface
#: and that one cannot drift about how a project is designed, which they had.
DOCTRINE_PATH = Path(__file__).resolve().parents[2] / "founder" / "DESIGN_DOCTRINE.md"

PER_PROJECT = {
    "briefing": "What changed in this project since you last looked",
    "plan": "Requirements, stages, the commitment and its projection",
    "goal": "The goal the CEO plans from, verbatim",
    "constitution": "The rules in force, and the version number they carry",
}


def listing(session: Session, principal: Principal) -> list[dict]:
    rows = [
        {"uri": "yaaf://desk", "name": "The desk",
         "description": "What needs the chairman, across every project",
         "mimeType": "application/json"},
        {"uri": "yaaf://docs/goal-grammar", "name": "GOAL_GRAMMAR.md",
         "description": "The ten things a goal has to contain",
         "mimeType": "text/markdown"},
        {"uri": "yaaf://docs/design-doctrine", "name": "DESIGN_DOCTRINE.md",
         "description": "How a project is drafted: the goal, dates, the roster, "
                        "the constitution knobs worth an opinion",
         "mimeType": "text/markdown"},
    ]
    for project in chaired_projects(session, principal):
        for slug, description in PER_PROJECT.items():
            rows.append({
                "uri": f"yaaf://project/{project.slug}/{slug}",
                "name": f"{project.name} — {slug}",
                "description": description,
                "mimeType": "text/markdown" if slug == "goal" else "application/json",
            })
    return rows


def read(session: Session, principal: Principal, uri: str) -> dict:
    """One resource's contents, or a refusal in the project gate's own words."""
    if uri == "yaaf://desk":
        return _json(uri, envelope.fit(observe.desk(live=True, session=session)))
    if uri == "yaaf://docs/goal-grammar":
        return _text(uri, _grammar(), "text/markdown")
    if uri == "yaaf://docs/design-doctrine":
        return _text(uri, _doctrine(), "text/markdown")

    prefix = "yaaf://project/"
    if uri.startswith(prefix) and uri.count("/") == 4:
        ref, view = uri[len(prefix):].split("/", 1)
        project = resolve_project(session, principal, ref)
        if view == "goal":
            return _text(uri, project.goal or "", "text/markdown")
        if view == "plan":
            return _json(uri, envelope.fit(observe.plan_view(project.id, session=session)))
        if view == "briefing":
            return _json(uri, envelope.fit(
                observe.project_briefing(project.id, session=session)))
        if view == "constitution":
            return _json(uri, {
                "version": effective_version(session, project.id),
                "rules": effective(session, project.id).model_dump(),
            })
    raise ToolRefusal(f"no resource {uri!r} is readable by this client")


def _grammar() -> str:
    from yaaf.founder import doctrine

    return doctrine.text(GRAMMAR_PATH.name)


def _doctrine() -> str:
    """The same text the Founder's prompt carries — cards substituted and all.

    Read through `doctrine.text` rather than off the disk, because half of what
    this file teaches about the tempo is a set of numbers that lives in code.
    A reader served the raw markdown is told to copy a card it was never shown.
    """
    from yaaf.founder import doctrine

    return doctrine.text(DOCTRINE_PATH.name)


def _json(uri: str, payload: dict) -> dict:
    import json

    return _text(uri, json.dumps(payload, default=str), "application/json")


def _text(uri: str, text: str, mime: str) -> dict:
    return {"contents": [{"uri": uri, "mimeType": mime, "text": text}]}
