"""Creating a project of a plugin kind: what is checked before a row exists.

`POST /admin/projects` (and so an import, which posts the same body) names a
plugin kind as `kind: "plugin:<key>"`, with `kind_mode` and `kind_fields`.
Every refusal here happens before anything is written, as the route's other
refusals do:

* the kind must be offered on this box: a version that may be chosen
  (`registry.offered`). A kind that is not installed, failed, or was disabled is
  refused by name;
* the mode must be one the plugin declares *open*. The project's built-in
  `mode` is set from that mode's shape, so a caller cannot also ask for
  another;
* a plugin with a fixed lead is led by it, as a Code Factory is by its Tech
  Lead: an unnamed lead is filled, and a different one is refused;
* `kind_fields` are checked against the plugin's `create_fields` (types,
  choices, required, no unknown keys) and then by its `validate_create`. A
  plugin that cannot be asked refuses the creation (fail closed).

What a plugin can do here is refuse. It cannot widen anything the route
already refuses.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session
from yaaf_plugin_api import hooks as api_hooks
from yaaf_plugin_api import types as T
from yaaf_plugin_api.manifest import Manifest

from yaaf.core import shape
from yaaf.core.models import PluginVersion
from yaaf.plugins import host as H
from yaaf.plugins import registry

MAX_FIELD_TEXT = 5_000


class KindRefusal(Exception):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class Choice:
    version: PluginVersion
    manifest: Manifest
    mode_key: str
    builtin_mode: str
    lead: str | None
    fields: dict


def offered_version(session: Session, kind: str) -> PluginVersion | None:
    key = shape.plugin_kind({"kind": kind})
    return next((v for v in registry.offered(session)
                 if (v.manifest or {}).get("kind_key") == key), None)


def _field_problems(specs: list[T.FieldSpec], given: dict) -> list[str]:
    by_key = {f.key: f for f in specs}
    out = [f"kind_fields.{k} is not a field of this kind (its fields: "
           f"{', '.join(by_key) or 'none'})" for k in given if k not in by_key]
    for f in specs:
        if f.key not in given:
            if f.required and f.default is None:
                out.append(f"kind_fields.{f.key} ({f.label}) is required")
            continue
        v = given[f.key]
        ok = {
            "string": isinstance(v, str) and len(v) <= 500,
            "text": isinstance(v, str) and len(v) <= MAX_FIELD_TEXT,
            "integer": isinstance(v, int) and not isinstance(v, bool),
            "number": isinstance(v, (int, float)) and not isinstance(v, bool),
            "boolean": isinstance(v, bool),
            "enum": v in f.choices,
        }.get(f.type, False)
        if not ok:
            want = f"one of {', '.join(f.choices)}" if f.type == "enum" else f"a {f.type}"
            out.append(f"kind_fields.{f.key} ({f.label}) must be {want}")
    return out


def choose(session: Session, *, kind: str, kind_mode: str | None, kind_fields: dict,
           mode: str, mode_given: bool, ceo_role_slug: str, ceo_role_given: bool,
           factory_start: str | None, goal: str, title: str) -> Choice:
    if not shape.is_plugin_kind({"kind": kind}):
        raise KindRefusal(f"kind {kind!r} is not a plugin kind: a built-in project is chosen "
                          "with mode, and a plugin kind is named plugin:<key>")
    version = offered_version(session, kind)
    if version is None:
        raise KindRefusal(f"{kind} is not offered on this box: no installed version of it may "
                          "be chosen (GET /admin/kinds lists what may)")
    manifest = registry.manifest_for(version)
    open_modes = [m for m in manifest.modes if m.open]
    picked = next((m for m in manifest.modes if m.key == kind_mode), None) if kind_mode \
        else open_modes[0]
    if picked is None:
        raise KindRefusal(f"kind_mode {kind_mode!r} is not a mode of {kind} (its modes: "
                          f"{', '.join(m.key for m in manifest.modes)})")
    if not picked.open:
        raise KindRefusal(f"{kind}'s mode {picked.key!r} is not open: the plugin declares it "
                          "and does not yet allow a project to be created in it")
    builtin = shape.mode_for_shape(picked.shape)
    if mode_given and mode != builtin:
        raise KindRefusal(f"a {kind} project in mode {picked.key!r} behaves as {builtin}; "
                          f"leave mode out rather than ask for {mode}")
    if factory_start is not None:
        raise KindRefusal("factory_start belongs to a Code Factory project")
    lead = manifest.lead
    if lead is not None and ceo_role_given and ceo_role_slug != lead:
        raise KindRefusal(f"a {kind} project is led by its plugin's lead ({lead}), not "
                          f"{ceo_role_slug}")
    fields = dict(kind_fields or {})
    pool = registry.pool_for(version)
    if manifest.create_fields:
        specs, failure = H.ask_or_fail_closed(pool, api_hooks.HookKey("create_fields"), [])
        if specs is None:
            raise KindRefusal(H.UNAVAILABLE + f" (its create fields: {failure.kind})")
        if problems := _field_problems(list(specs), fields):
            raise KindRefusal("; ".join(problems))
        for f in specs:
            if f.key not in fields and f.default is not None:
                fields[f.key] = f.default
        refusals, _ = H.ask_or_fail_closed(
            pool, api_hooks.HookKey("validate_create"),
            [T.CreateSpec(picked.key, fields, title=title, goal=goal)])
        if refusals:
            raise KindRefusal("; ".join(refusals))
    elif fields:
        raise KindRefusal(f"{kind} takes no kind_fields")
    return Choice(version, manifest, picked.key, builtin, lead, fields)


def refuse_stray(kind: str | None, kind_mode: str | None, kind_fields: dict | None) -> None:
    """kind_mode and kind_fields belong to a plugin kind; on a built-in project
    they would be sentences the platform silently ignored."""
    if kind is None and (kind_mode is not None or kind_fields):
        raise KindRefusal("kind_mode and kind_fields belong to a plugin kind: name it as kind")


def initial_state(choice: Choice) -> dict:
    return {"create": dict(choice.fields)} if choice.fields else {}


def manifest_of(session: Session, version_id: str | None) -> Any:
    version = session.get(PluginVersion, version_id) if version_id else None
    return registry.manifest_for(version) if version is not None else None
