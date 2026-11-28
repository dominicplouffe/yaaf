"""Where core asks a plugin project's plugin, beside the primitives (§4.5–4.9).

Each function here is called from the place that already answers the same
question for every project, and it only adds to that answer:

* `brief_block` — after core's rules and the repo guidance in every seat's
  system prompt, under a heading core writes, so a transcript always shows
  which words are the plugin's;
* `plan_owed` — owed items that keep the plan from holding (`evaluate_plan`);
* `completion_owed` — owed items that keep the project from completing
  (`delivery_outstanding`), beside the review primitive's own;
* `setup_steps` / `step_state` — the plugin's setup steps, on the checklist;
* `kind_view` — the project-home section, its declaration and its data.

Every call fails closed. An unreachable plugin owes a plan item and a
completion item and marks its steps not done, so its project cannot plan,
finish or look ready while it is down. Its brief is empty and its view says
it could not be read, rather than showing a guess.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy.orm import Session
from yaaf_plugin_api import hooks as api_hooks
from yaaf_plugin_api import view as api_view

from yaaf.core.models import Project
from yaaf.plugins import host as H
from yaaf.plugins import primitives, registry

log = logging.getLogger("yaaf.plugins.points")
STEP_PREFIX = "plugin:"


def _ask(session: Session, project: Project, key: api_hooks.HookKey, extra: list | None = None,
         lane: str | None = "run"):
    found = primitives.plugin_of(session, project)
    if found is None:
        return None, None
    version, manifest = found
    ctx = primitives.snapshot(session, project, version, manifest, lane=lane)
    answer, failure = H.ask_or_fail_closed(registry.pool_for(version), key,
                                           [ctx, *(extra or [])],
                                           primitives.callbacks(session, project))
    return answer, (version, manifest, failure)


def brief_block(session: Session, project: Project, lane: str = "run") -> str:
    found = primitives.plugin_of(session, project)
    if found is None or not found[1].brief:
        return ""
    answer, (version, manifest, failure) = _ask(session, project, api_hooks.HookKey("brief"),
                                                lane=lane)
    text = (answer.text if answer is not None else "").strip()
    if not text:
        return ""
    return (f"THIS PROJECT'S KIND: {manifest.kind_label} (plugin {manifest.id}@"
            f"{version.version[:14]}). What follows is the plugin's guidance; the platform's "
            "rules above still hold, and only the platform enforces anything.\n"
            f"{text}\n\n")


def _owed(session: Session, project: Project, kind: str) -> list[str]:
    found = primitives.plugin_of(session, project)
    if found is None or not getattr(found[1], kind):
        return []
    answer, _ = _ask(session, project, api_hooks.HookKey(kind))
    return [o.text for o in answer or []]


def plan_owed(session: Session, project: Project) -> list[str]:
    return _owed(session, project, "plan_gate")


def completion_owed(session: Session, project: Project) -> list[str]:
    return _owed(session, project, "completion_gate")


def setup_steps(session: Session, project: Project) -> list[str]:
    """The plugin's setup step keys, as checklist keys (`plugin:<key>`)."""
    found = primitives.plugin_of(session, project)
    return [STEP_PREFIX + s for s in found[1].setup_steps] if found else []


def step_state(session: Session, project: Project, key: str) -> tuple[bool, str]:
    answer, _ = _ask(session, project,
                     api_hooks.HookKey("setup_step", key.removeprefix(STEP_PREFIX)))
    if answer is None:
        return False, "This project's plugin could not be consulted."
    return answer.done, answer.text


def kind_view(session: Session, project: Project) -> dict | None:
    """The section the project home draws for a plugin project: what it is,
    the declared view, and its data, or why there is none."""
    found = primitives.plugin_of(session, project)
    if found is None:
        return None
    version, manifest = found
    out = {"kind": project.kind, "mode": project.kind_mode, "label": manifest.kind_label,
           "plugin": manifest.id, "version": version.version, "status": version.status,
           "doc": None, "data": None, "error": None}
    if not manifest.view:
        return out
    try:
        doc = json.loads((Path(version.root) / manifest.view).read_text("utf-8"))
    except (OSError, ValueError) as exc:
        out["error"] = f"the view could not be read: {exc}"
        return out
    out["doc"] = doc
    data, (_, _, failure) = _ask(session, project, api_hooks.HookKey("view"), lane="page")
    problems = api_view.validate_data(doc, data) if data is not None else []
    if data is None or problems:
        out["error"] = ("this project's plugin could not be consulted" if data is None
                        else "the plugin's view data did not fit its view: "
                             + "; ".join(problems)[:500])
        return out
    out["data"] = data
    return out
