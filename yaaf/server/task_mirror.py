"""Mirror a project's task board into a task manager the company already uses.

A project may use an integration that is a task manager — Trello, Linear,
Jira — and ask for its board to show up there, so the people who live in that
tool can follow the work without opening yaaf. It is a mirror and nothing
more. yaaf's board stays the record, because that is where the rules live: a
claim, a lease, a success test, the verifier — a card in Trello can refuse an
agent none of those things. So writes go one way, out, and nothing read back
from the task manager changes a task.

How tasks are written is the administrator's, per integration (`task_mirror`):
which tool makes a card and which updates one, and their arguments, with
`{title}`, `{description}`, `{status}`, `{url}`, `{project}` and
`{external_id}` filled in per task. Every server names these differently, and
a mapping in data is cheaper than a connector per vendor.

Best effort by construction, in the shape of the Slack mirror: a failed write
keeps its error on the item and is retried next pass, a card that keeps
failing stops being retried until its task changes, and one misconfigured
project never stops the others.
"""

from __future__ import annotations

import json
import logging
import re
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core import ids
from yaaf.core.db import db_session
from yaaf.core.models import (
    Integration,
    IntegrationDecision,
    Project,
    ProjectIntegration,
    Task,
    TaskMirrorItem,
    utcnow,
)

log = logging.getLogger("yaaf.task_mirror")

#: What a template may name. Anything else is refused when the administrator
#: saves the mapping, not discovered as a literal "{assignee}" on a card.
FIELDS = ("title", "description", "status", "url", "project", "external_id")

#: A card that failed this many times in a row waits for its task to change.
MAX_ATTEMPTS = 5

#: Calls per pass, across every project. A board of four hundred tasks turned
#: on for the first time is copied out over a few minutes, not in one stall.
CALLS_PER_PASS = 25

PASS_SECONDS = 30.0

_PLACEHOLDER = re.compile(r"\{(\w+)\}")


class MirrorConfigError(ValueError):
    """Said to the administrator saving a mapping, verbatim."""


def validate(config: dict) -> dict:
    """The mapping as stored, or a MirrorConfigError naming what is wrong."""
    if not isinstance(config, dict):
        raise MirrorConfigError("the task mirror must be an object")
    create_tool = str(config.get("create_tool") or "").strip()
    if not create_tool:
        raise MirrorConfigError("name the tool that makes a card (create_tool)")
    clean = {"create_tool": create_tool,
             "create_arguments": config.get("create_arguments") or {},
             "id_path": str(config.get("id_path") or "id").strip()}
    if config.get("update_tool"):
        clean["update_tool"] = str(config["update_tool"]).strip()
        clean["update_arguments"] = config.get("update_arguments") or {}
    for key in ("create_arguments", "update_arguments"):
        if key not in clean:
            continue
        if not isinstance(clean[key], dict):
            raise MirrorConfigError(f"{key} must be an object of the tool's arguments")
        unknown = sorted({m for m in _PLACEHOLDER.findall(json.dumps(clean[key]))
                          if m not in FIELDS})
        if unknown:
            raise MirrorConfigError(
                f"{key} names {', '.join('{' + u + '}' for u in unknown)}; a card can "
                f"be filled from {', '.join('{' + f + '}' for f in FIELDS)}")
    if "{external_id}" in json.dumps(clean["create_arguments"]):
        raise MirrorConfigError("a card has no id before it is made — {external_id} "
                                "belongs in update_arguments")
    return clean


def render(template, values: dict):
    """Fill a template's strings, at any depth. A string that is exactly one
    placeholder keeps the value's own type, so an id stays a number."""
    if isinstance(template, str):
        whole = _PLACEHOLDER.fullmatch(template)
        if whole and whole.group(1) in values:
            return values[whole.group(1)]
        return _PLACEHOLDER.sub(lambda m: str(values.get(m.group(1), m.group(0))), template)
    if isinstance(template, dict):
        return {k: render(v, values) for k, v in template.items()}
    if isinstance(template, list):
        return [render(v, values) for v in template]
    return template


def dig(result: dict, path: str):
    """The card id out of a tools/call result: `structuredContent` when the
    server sends it, else the first text block parsed as JSON. Dotted path;
    a number indexes a list."""
    candidates = []
    if isinstance(result.get("structuredContent"), dict):
        candidates.append(result["structuredContent"])
    for block in result.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            try:
                candidates.append(json.loads(block.get("text") or ""))
            except ValueError:
                continue
            break
    for found in candidates:
        for part in [p for p in path.split(".") if p]:
            if isinstance(found, list) and part.isdigit() and int(part) < len(found):
                found = found[int(part)]
            elif isinstance(found, dict) and part in found:
                found = found[part]
            else:
                found = None
                break
        if found not in (None, "", [], {}):
            return found
    return None


def call_tool(url: str, token: str | None, tool: str, arguments: dict, transport=None) -> dict:
    """One MCP tools/call from the control plane: initialize, then the call.
    Raises RuntimeError with a sentence the health page can show."""
    import httpx

    from yaaf.server.api.admin import _sse_or_json

    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    def rpc(client, payload: dict, session_id: str | None):
        sent = {**headers, **({"Mcp-Session-Id": session_id} if session_id else {})}
        return client.post(url, json=payload, headers=sent)

    try:
        with httpx.Client(timeout=20, follow_redirects=True, transport=transport) as client:
            opened = rpc(client, {
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                           "clientInfo": {"name": "yaaf-task-mirror", "version": "1.0"}},
            }, None)
            if opened.status_code >= 400:
                raise RuntimeError(f"the server refused the handshake (HTTP {opened.status_code})")
            session_id = opened.headers.get("mcp-session-id")
            rpc(client, {"jsonrpc": "2.0", "method": "notifications/initialized"}, session_id)
            called = rpc(client, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                  "params": {"name": tool, "arguments": arguments}}, session_id)
            if called.status_code >= 400:
                raise RuntimeError(f"{tool} answered HTTP {called.status_code}")
            envelope = _sse_or_json(called) or {}
    except httpx.HTTPError as exc:
        raise RuntimeError(f"could not reach the task manager ({exc.__class__.__name__})") from exc
    except ValueError as exc:
        raise RuntimeError(f"{tool} answered with something that is not MCP") from exc
    if envelope.get("error"):
        raise RuntimeError(f"{tool} refused: {(envelope['error'] or {}).get('message', '?')}")
    result = envelope.get("result") or {}
    if result.get("isError"):
        text = " ".join(b.get("text", "") for b in result.get("content") or []
                        if isinstance(b, dict))
        raise RuntimeError(f"{tool} failed: {text[:300] or 'no reason given'}")
    return result


def task_url(project: Project, task: Task) -> str:
    from yaaf.core.config import settings

    base = (settings().public_base_url or "").rstrip("/")
    return f"{base}/app/#/projects/{project.id}/details/tasks"


def _values(project: Project, task: Task, external_id: str | None) -> dict:
    return {"title": task.title, "description": (task.goal or "")[:2000],
            "status": task.status, "url": task_url(project, task), "project": project.name,
            "external_id": external_id or ""}


def mirroring(session: Session) -> list[tuple[Project, Integration]]:
    rows = session.execute(
        select(Project, Integration)
        .join(ProjectIntegration, ProjectIntegration.project_id == Project.id)
        .join(Integration, Integration.id == ProjectIntegration.integration_id)
        .where(ProjectIntegration.decision == IntegrationDecision.USE,
               ProjectIntegration.mirror_tasks.is_(True),
               Integration.task_mirror.is_not(None))).all()
    return [(r[0], r[1]) for r in rows]


def sync(session: Session, *, budget: int = CALLS_PER_PASS, transport=None) -> int:
    """One pass: make the cards that do not exist and move the ones whose task
    moved. Returns how many calls it made."""
    from yaaf.server import integrations

    calls = 0
    for project, integration in mirroring(session):
        if calls >= budget:
            break
        config = integration.task_mirror or {}
        home = integrations.IntegrationHome(integration)
        url = home.read(integrations.URL_KEY)
        if not url:
            continue
        token = None
        items = {i.task_id: i for i in session.execute(select(TaskMirrorItem).where(
            TaskMirrorItem.project_id == project.id,
            TaskMirrorItem.integration_id == integration.id)).scalars()}
        tasks = session.execute(select(Task).where(Task.project_id == project.id)
                                .order_by(Task.created_at)).scalars()
        for task in tasks:
            if calls >= budget:
                break
            item = items.get(task.id)
            if item is not None and item.external_id and (
                    item.synced_status == task.status or not config.get("update_tool")):
                continue
            if item is not None and _resting(item, task):
                continue
            if item is None:
                if task.status == "killed":
                    continue  # never made, never wanted: no card for a dead task
                item = TaskMirrorItem(id=ids.new_id(ids.TASK_MIRROR), project_id=project.id,
                                      task_id=task.id, integration_id=integration.id,
                                      attempts=0)
                session.add(item)
            if token is None:
                token = (integrations.ensure_fresh_locked(session, integration)
                         if (integration.entry or {}).get("auth") == "oauth"
                         else home.read((integration.entry or {}).get("bearer_secret")))
            calls += 1
            values = _values(project, task, item.external_id)
            try:
                if item.external_id:
                    call_tool(url, token, config["update_tool"],
                              render(config.get("update_arguments") or {}, values), transport)
                else:
                    result = call_tool(url, token, config["create_tool"],
                                       render(config.get("create_arguments") or {}, values),
                                       transport)
                    found = dig(result, config.get("id_path") or "id")
                    if found is None:
                        raise RuntimeError(f"made a card, but found no id at "
                                           f"{config.get('id_path') or 'id'!r} in the answer")
                    item.external_id = str(found)[:200]
            except RuntimeError as exc:
                if (item.attempts or 0) >= MAX_ATTEMPTS:
                    item.attempts = 0  # it was resting, so its task moved: fresh tries
                item.attempts = (item.attempts or 0) + 1
                item.error = str(exc)[:1000]
                item.updated_at = utcnow()
                continue
            item.synced_status = task.status
            item.error = None
            item.attempts = 0
            item.updated_at = utcnow()
    return calls


def _resting(item: TaskMirrorItem, task: Task) -> bool:
    """A card that failed MAX_ATTEMPTS times waits until its task changes —
    retrying a refused write every thirty seconds forever is noise, and the
    error is already on the health page."""
    if (item.attempts or 0) < MAX_ATTEMPTS:
        return False
    return not (task.updated_at and item.updated_at and task.updated_at > item.updated_at)


def failing(session: Session, project_id: str) -> list[tuple[str, int, str]]:
    """(integration name, cards failing, the latest error) for the health page."""
    rows = session.execute(
        select(Integration.name, TaskMirrorItem.error)
        .join(Integration, Integration.id == TaskMirrorItem.integration_id)
        .where(TaskMirrorItem.project_id == project_id, TaskMirrorItem.error.is_not(None))
        .order_by(TaskMirrorItem.updated_at.desc())).all()
    out: dict[str, list] = {}
    for name, error in rows:
        out.setdefault(name, []).append(error)
    return [(name, len(errors), errors[0]) for name, errors in out.items()]


def task_mirror_loop() -> None:  # pragma: no cover - thin thread entrypoint
    while True:
        try:
            with db_session() as session:
                sync(session)
        except Exception:
            log.warning("task mirror pass failed", exc_info=True)
        time.sleep(PASS_SECONDS)
