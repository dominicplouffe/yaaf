"""The three tools a seat of a plugin project has (§4.6), and nothing more.

A plugin adds no sandbox tools of its own: the tool list is core's, and what
agents see is regression-gated (house rule 3). Its seats get three generic
ones, each answering only for what the manifest declares:

* `plugin_describe` — the kind, its mode, its coverage, what is gated and how;
* `plugin_file` — a declared agent file (the PPC kit), served verbatim;
* `plugin_action` — run a declared data action. The plugin's hook reaches a
  data source only through `Gateway`, which core answers with the same gateway
  an agent's own calls use: only tools the chairman allows and this seat's
  role was given, only those the manifest names for this action, each read
  recorded as a receipt, and every failure turned into `ToolError`. What the
  action returns under its declared outputs is stored as the platform's.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from yaaf_plugin_api import hooks as api_hooks
from yaaf_plugin_api import types as T

from yaaf.core.models import DataSourceTool, Project, ToolPermission
from yaaf.plugins import host as H
from yaaf.plugins import primitives, registry


def _refusal(message: str, status: int = 422):
    from yaaf.server.policy import Refusal

    return Refusal(message, status_code=status)


def _plugin(session: Session, project: Project):
    found = primitives.plugin_of(session, project)
    if found is None:
        raise _refusal("this project is not a factory plugin's; it has no plugin tools")
    return found


def describe(session: Session, project: Project) -> dict:
    version, m = _plugin(session, project)
    cov = primitives.coverage(session, project, version, m)
    return {
        "kind": project.kind, "mode": project.kind_mode, "label": m.kind_label,
        "plugin": f"{m.id}@{version.version[:14]}",
        "coverage": {"capabilities": sorted(cov.capabilities),
                     "areas": [{"key": a.key, "available": a.available, "note": a.note}
                               for a in cov.areas]},
        "gated": list(m.artifacts),
        "review": ({"of": m.review.of, "artifact": m.review.artifact,
                    "rederive_tolerance": m.review.rederive_tolerance,
                    "envelope": {"subject_sha256": "<sha256 of the latest version>",
                                 "verdict": "pass | return",
                                 "rederived": [{"claim": "<id>", "value": "<number>"}]}}
                   if m.review else None),
        "platform_writes": list(m.reserved_names()),
        "files": sorted(m.agent_files),
        "actions": [{"name": a.name, "description": a.description} for a in m.data_actions],
    }


def agent_file(session: Session, project: Project, name: str) -> dict:
    version, m = _plugin(session, project)
    rel = m.agent_files.get(name)
    if rel is None:
        raise _refusal(f"{name!r} is not one of this plugin's files "
                       f"({', '.join(sorted(m.agent_files)) or 'it has none'})", 404)
    path = Path(version.root) / rel
    return {"name": name, "filename": Path(rel).name,
            "source": path.read_text("utf-8")}


def _gateway(session: Session, project: Project, agent, run_id: str | None,
             tools: tuple[str, ...]):
    """The data action's only way out, through the agent gateway's own checks."""
    from yaaf.server import datasource_tools as gw

    def call(tool: str, arguments: dict) -> T.ToolResult:
        if tool not in tools:
            raise T.ToolError(f"{tool} is not one of this action's tools", state="denied")
        sources = list(session.execute(select(DataSourceTool.source).where(
            DataSourceTool.project_id == project.id, DataSourceTool.name == tool,
            DataSourceTool.kind == "read", DataSourceTool.permission == ToolPermission.ALLOW,
            DataSourceTool.gone_at.is_(None))).scalars())
        if not sources:
            raise T.ToolError(f"no connected source offers {tool} as an allowed read",
                              state="denied")
        try:
            decision = gw.decide(session, project, agent, sources[0], tool)
            return gw.forward(session, project, agent, decision, dict(arguments or {}),
                              run_id)
        except gw.UpstreamError as exc:
            raise T.ToolError(str(exc), state=getattr(exc, "state", "unreachable")) from None
        except Exception as exc:
            from yaaf.server.policy import Refusal

            if isinstance(exc, Refusal):
                raise T.ToolError(exc.message, state="denied") from None
            raise
    return call


def run_action(session: Session, project: Project, agent, run_id: str | None,
               name: str) -> dict:
    version, m = _plugin(session, project)
    action = next((a for a in m.data_actions if a.name == name), None)
    if action is None:
        raise _refusal(f"{name!r} is not one of this plugin's actions "
                       f"({', '.join(a.name for a in m.data_actions) or 'it has none'})", 404)
    ctx = primitives.snapshot(session, project, version, m)
    cb = primitives.callbacks(session, project)
    cb.gateway_call = _gateway(session, project, agent, run_id, action.tools)
    answer, failure = H.ask_or_fail_closed(registry.pool_for(version),
                                           api_hooks.HookKey("data_action", name), [ctx], cb)
    if answer is None:
        raise _refusal(f"{H.UNAVAILABLE} (the {name} action: "
                       f"{failure.kind if failure else 'no answer'})", 502)
    stored = []
    for out_name, body in (answer.artifacts or {}).items():
        row = primitives.store_platform(session, project, run_id, out_name, body,
                                        role="output")
        stored.append({"name": out_name, "version": row.version, "bytes": row.size_bytes})
    summary: Any = answer.summary
    return {"action": name, "summary": json.loads(json.dumps(summary)), "stored": stored}
