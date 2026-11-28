"""The questions core may ask a plugin, and the check that the code answers
exactly the ones its manifest declared.

A hook is a method marked with a decorator from `hook`. Each one has a spec
here: the capability that requires it, how many arguments it takes, and the
validator for what it returns. Three things follow from that one table, and
nothing else defines them:

* `expected(manifest)` — the hooks a manifest obliges the code to have;
* `check_plugin(plugin, manifest)` — MIN-05: every expected hook exists with
  the right arity, and no hook exists that was not declared;
* `check_return(key, value, manifest)` — MIN-08 / MIN-17: what a hook gave
  back is a value core may act on. Anything else is a host failure, and a gate
  that asked fails closed (§1.3).
"""

from __future__ import annotations

import inspect
import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from yaaf_plugin_api import types as T
from yaaf_plugin_api.manifest import Manifest, plain_name

#: What a brief may be, and the core headings it may not imitate: a reader of a
#: transcript must always be able to tell the platform's words from a plugin's.
BRIEF_MAX_CHARS = 8_000
RESERVED_BRIEF_HEADINGS = (
    "REFUSED.", "PRECEDENCE:", "MECHANICS:", "PROJECT GOAL:", "THIS PROJECT IS FINISHED.",
    "THIS PROJECT'S KIND", "CORE RULES",
)
#: Media types a rendered page may have. HTML is allowed because core stamps
#: every HTML page it stores with a Content-Security-Policy that permits no
#: script, no network and no form (yaaf/plugins/primitives.py), and the
#: dashboard previews it in an opaque-origin frame. SVG is not: an SVG is
#: shown as an image on the dashboard's own origin, where no such stamp applies.
PAGE_MEDIA_TYPES = frozenset({
    "text/html", "text/markdown", "text/plain", "text/csv", "application/json", "application/pdf",
    "image/png", "image/jpeg",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
})
PROBLEM_MAX_CHARS = 500
OWED_MAX_CHARS = 500
STEP_TEXT_MAX_CHARS = 300
SUMMARY_MAX_BYTES = 64_000
MAX_FIELDS = 20


@dataclass(frozen=True)
class HookKey:
    """One hook: its kind, and for a keyed hook the name it answers for
    (`check_artifact("ppc-audit.json")` → HookKey("check_artifact", "ppc-audit.json"))."""

    kind: str
    key: str | None = None

    def __str__(self) -> str:
        return f"{self.kind}({self.key!r})" if self.key is not None else self.kind


@dataclass(frozen=True)
class HookSpec:
    kind: str
    #: Argument names after self, as the API documents them. Arity is checked; names are not.
    params: tuple[str, ...]
    keyed: bool
    #: The manifest capability that obliges it, for the error message.
    capability: str
    returns: str


SPECS: dict[str, HookSpec] = {s.kind: s for s in (
    HookSpec("brief", ("ctx",), False, "brief = true", "Brief"),
    HookSpec("plan_gate", ("ctx",), False, "plan_gate = true", "list[Owed]"),
    HookSpec("completion_gate", ("ctx",), False, "completion_gate = true", "list[Owed]"),
    HookSpec("classify_tools", ("tools",), False, "coverage = true", "Coverage"),
    HookSpec("migrate_state", ("old_state", "from_version"), False, "state = true", "dict"),
    HookSpec("check_artifact", ("ctx", "upload"), True, "artifacts = [...]", "Accept | Refuse"),
    HookSpec("check_review", ("ctx", "review", "subject"), True, "review = {...}",
             "Accept | Refuse"),
    HookSpec("render", ("ctx", "accepted", "reviewed"), False, "platform_pages = [...]",
             "list[Page]"),
    HookSpec("fields_of_read", ("tool", "result"), False, "receipts", "ReadFields"),
    HookSpec("data_action", ("ctx", "gateway"), True, "[capabilities.data_actions.<name>]",
             "ActionResult"),
    HookSpec("setup_step", ("ctx",), True, "setup_steps = [...]", "StepState"),
    HookSpec("view", ("ctx",), False, "view = \"ui/view.json\"", "ViewData"),
    HookSpec("create_fields", (), False, "create_fields = true", "list[FieldSpec]"),
    HookSpec("validate_create", ("spec",), False, "create_fields = true", "list[str]"),
)}

_MARK = "__yaaf_hook__"


def _mark(kind: str, key: str | None = None) -> Callable[[Callable], Callable]:
    def decorate(fn: Callable) -> Callable:
        marks = list(getattr(fn, _MARK, ()))
        marks.append(HookKey(kind, key))
        setattr(fn, _MARK, tuple(marks))
        return fn
    return decorate


class _Hook:
    """`@hook.brief` for a plain hook, `@hook.check_artifact("name")` for a keyed one."""

    def __getattr__(self, kind: str):
        spec = SPECS.get(kind)
        if spec is None:
            raise AttributeError(f"there is no hook {kind!r}; the hooks are: "
                                 f"{', '.join(SPECS)}")
        if not spec.keyed:
            return _mark(kind)

        def keyed(key: str) -> Callable[[Callable], Callable]:
            if not isinstance(key, str) or not key:
                raise TypeError(f"@hook.{kind}(...) takes the name it answers for")
            return _mark(kind, key)
        return keyed


hook = _Hook()


class FactoryPlugin:
    """Subclass this and expose one instance as the manifest's `module` object.
    A plugin holds no state between calls: every answer comes from the snapshot,
    so the host can be recycled at any moment and nothing is lost."""

    def hooks(self) -> dict[HookKey, Callable]:
        """Every marked hook, bound. Two methods marked for the same hook is an
        error, not a race over which one core calls."""
        found: dict[HookKey, Callable] = {}
        for name, member in inspect.getmembers(type(self), callable):
            for mark in getattr(member, _MARK, ()):
                if mark in found:
                    raise TypeError(f"{mark} is marked on two methods; keep one")
                found[mark] = getattr(self, name)
        return found


def expected(manifest: Manifest) -> set[HookKey]:
    """The hooks this manifest obliges the plugin's code to have."""
    out: set[HookKey] = set()
    for flag in ("brief", "plan_gate", "completion_gate"):
        if getattr(manifest, flag):
            out.add(HookKey(flag))
    if manifest.coverage:
        out.add(HookKey("classify_tools"))
    if manifest.state:
        out.add(HookKey("migrate_state"))
    out |= {HookKey("check_artifact", n) for n in manifest.artifacts}
    if manifest.review:
        out.add(HookKey("check_review", manifest.review.artifact))
    if manifest.platform_pages:
        out.add(HookKey("render"))
    if manifest.receipts:
        out.add(HookKey("fields_of_read"))
    out |= {HookKey("data_action", a.name) for a in manifest.data_actions}
    out |= {HookKey("setup_step", s) for s in manifest.setup_steps}
    if manifest.view:
        out.add(HookKey("view"))
    if manifest.create_fields:
        out |= {HookKey("create_fields"), HookKey("validate_create")}
    return out


def check_plugin(plugin: Any, manifest: Manifest) -> list[str]:
    """MIN-05: the code has every hook its manifest declares, each with the
    API's arity, and none it does not declare."""
    if not isinstance(plugin, FactoryPlugin):
        return [f"{manifest.module} is a {type(plugin).__name__}, not a FactoryPlugin instance"]
    try:
        found = plugin.hooks()
    except TypeError as exc:
        return [str(exc)]
    want = expected(manifest)
    out = []
    for key in sorted(want - set(found), key=str):
        spec = SPECS[key.kind]
        out.append(f"the manifest declares {spec.capability} but there is no @hook.{key} "
                   f"method (it takes {', '.join(spec.params) or 'nothing'} and returns "
                   f"{spec.returns})")
    for key in sorted(set(found) - want, key=str):
        spec = SPECS[key.kind]
        out.append(f"@hook.{key} is not declared in the manifest: add {spec.capability} or "
                   "remove the hook — core calls only what is declared")
    for key in sorted(want & set(found), key=str):
        spec = SPECS[key.kind]
        try:
            sig = inspect.signature(found[key])
        except (TypeError, ValueError):
            out.append(f"@hook.{key} has no readable signature")
            continue
        params = [p for p in sig.parameters.values()
                  if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        if any(p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD) for p in sig.parameters.values()) \
                or len(params) != len(spec.params):
            out.append(f"@hook.{key} takes ({', '.join(spec.params)}); it takes "
                       f"({', '.join(sig.parameters)})")
    return out


# ── return values ──────────────────────────────────────────────────────────────


def _json_safe(value: Any, depth: int = 0) -> bool:
    if depth > 32:
        return False
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, (list, tuple)):
        return all(_json_safe(v, depth + 1) for v in value)
    if isinstance(value, Mapping):
        return all(isinstance(k, str) and _json_safe(v, depth + 1) for k, v in value.items())
    return False


def _problem_text(text: Any, limit: int) -> str | None:
    """Why a sentence meant for an agent is not one, or None."""
    if not isinstance(text, str) or not text.strip():
        return "is empty"
    if len(text) > limit:
        return f"is longer than {limit} chars"
    if "Traceback (most recent call last)" in text or '  File "' in text:
        return "is a stack trace, not a reason an agent can act on"
    return None


def _owed(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return ["a gate returns a list of Owed"]
    out, keys = [], set()
    for i, o in enumerate(value):
        if not isinstance(o, T.Owed):
            out.append(f"[{i}] is not an Owed")
            continue
        if not isinstance(o.key, str) or not o.key.strip() or len(o.key) > 80:
            out.append(f"[{i}].key is a stable, non-empty key (≤80 chars)")
        elif o.key in keys:
            out.append(f"[{i}].key {o.key!r} is used twice")
        keys.add(o.key)
        why = _problem_text(o.text, OWED_MAX_CHARS)
        if why:
            out.append(f"[{i}].text {why}")
    return out


def _verdict(value: Any) -> list[str]:
    if isinstance(value, T.Refuse):
        if not value.problems:
            return ["a Refuse names at least one problem"]
        out = []
        for i, p in enumerate(value.problems):
            why = _problem_text(p, PROBLEM_MAX_CHARS)
            if why:
                out.append(f"problems[{i}] {why}")
        return out
    if isinstance(value, T.Accept):
        out = []
        if value.data is not None and not isinstance(value.data, bytes):
            out.append("Accept.data is bytes or None")
        if value.state_patch is not None and not (isinstance(value.state_patch, Mapping)
                                                  and _json_safe(value.state_patch)):
            out.append("Accept.state_patch is a JSON object")
        if value.note is not None and _problem_text(value.note, PROBLEM_MAX_CHARS):
            out.append(f"Accept.note is a non-empty string of at most {PROBLEM_MAX_CHARS} chars")
        if value.load_bearing is not None and not (
                isinstance(value.load_bearing, Mapping) and all(
                    isinstance(k, str) and (v is None or (isinstance(v, (int, float))
                                                          and not isinstance(v, bool)
                                                          and math.isfinite(v)))
                    for k, v in value.load_bearing.items())):
            out.append("Accept.load_bearing maps claim ids to a finite number or None")
        return out
    return ["a gate returns Accept or Refuse"]


def check_return(key: HookKey, value: Any, manifest: Manifest | None = None) -> list[str]:
    """Every reason core may not act on what this hook returned, or []."""
    kind = key.kind
    prefix = f"@hook.{key} returned a value core cannot use: "
    problems = _returned(kind, key, value, manifest)
    return [prefix + p for p in problems]


def _returned(kind: str, key: HookKey, value: Any, manifest: Manifest | None) -> list[str]:
    if kind == "brief":
        if not isinstance(value, T.Brief) or not isinstance(value.text, str):
            return ["a brief is a Brief"]
        if len(value.text) > BRIEF_MAX_CHARS:
            return [f"the brief is longer than {BRIEF_MAX_CHARS} chars"]
        # A heading opens a line, in capitals. "…is refused." mid-sentence is
        # ordinary prose and stays allowed.
        lines = [ln.lstrip() for ln in value.text.splitlines()]
        hit = [h for h in RESERVED_BRIEF_HEADINGS if any(ln.startswith(h) for ln in lines)]
        return [f"the brief opens a line with core's heading {h!r}" for h in hit]
    if kind in ("plan_gate", "completion_gate"):
        return _owed(value)
    if kind in ("check_artifact", "check_review"):
        out = _verdict(value)
        if kind == "check_review" and isinstance(value, T.Accept) and value.data is not None:
            out.append("a review is stored as published: Accept.data must be None")
        return out
    if kind == "classify_tools":
        if not isinstance(value, T.Coverage) or not all(
                isinstance(c, str) for c in value.capabilities) or not all(
                isinstance(a, T.CoverageArea) for a in value.areas):
            return ["classify_tools returns a Coverage of capability names and CoverageArea"]
        keys = [a.key for a in value.areas]
        return ["two coverage areas share a key"] if len(set(keys)) != len(keys) else []
    if kind == "migrate_state":
        return [] if isinstance(value, Mapping) and _json_safe(value) else \
            ["migrate_state returns the new kind_state as a JSON object"]
    if kind == "render":
        if not isinstance(value, (list, tuple)):
            return ["render returns a list of Page"]
        out, names = [], set()
        allowed = set(manifest.platform_pages) if manifest else None
        for i, page in enumerate(value):
            if not isinstance(page, T.Page):
                out.append(f"[{i}] is not a Page")
                continue
            if not plain_name(page.name):
                out.append(f"[{i}].name is a plain file name")
            elif allowed is not None and page.name not in allowed:
                out.append(f"[{i}].name {page.name!r} is not one of platform_pages")
            elif page.name in names:
                out.append(f"[{i}].name {page.name!r} is rendered twice")
            names.add(page.name)
            if page.media_type not in PAGE_MEDIA_TYPES:
                out.append(f"[{i}].media_type {page.media_type!r} is not allowed (no SVG, "
                           "and nothing that is not a document: a page opens in the "
                           "chairman's browser)")
            if not isinstance(page.body, bytes):
                out.append(f"[{i}].body is bytes")
        return out
    if kind == "fields_of_read":
        if not isinstance(value, T.ReadFields):
            return ["fields_of_read returns ReadFields"]
        if value.fields is None:
            return []
        if not isinstance(value.fields, tuple) or not all(
                isinstance(f, str) and f for f in value.fields):
            return ["ReadFields.fields is a tuple of field names, or None"]
        if not value.fields:
            return ["ReadFields(()) reads as 'this pull returned no fields' — when the fields "
                    "cannot be told, return ReadFields.unknown()"]
        return []
    if kind == "data_action":
        if not isinstance(value, T.ActionResult):
            return ["a data action returns ActionResult"]
        out = []
        if not isinstance(value.summary, Mapping) or not _json_safe(value.summary):
            out.append("ActionResult.summary is a JSON object")
        elif len(json.dumps(value.summary)) > SUMMARY_MAX_BYTES:
            out.append(f"ActionResult.summary is larger than {SUMMARY_MAX_BYTES} bytes: put the "
                       "rest in its artifacts")
        declared = None
        if manifest is not None:
            declared = next((set(a.outputs) for a in manifest.data_actions
                             if a.name == key.key), set())
        for name, body in (value.artifacts or {}).items():
            if declared is not None and name not in declared:
                out.append(f"artifact {name!r} is not one of this action's declared outputs")
            if not isinstance(body, bytes):
                out.append(f"artifact {name!r} is bytes")
        return out
    if kind == "setup_step":
        if not isinstance(value, T.StepState) or not isinstance(value.done, bool):
            return ["a setup step returns StepState"]
        why = _problem_text(value.text, STEP_TEXT_MAX_CHARS)
        return [f"StepState.text {why}"] if why else []
    if kind == "view":
        return [] if isinstance(value, Mapping) and _json_safe(value) else \
            ["view returns a JSON object: section id → data"]
    if kind == "create_fields":
        return _fields(value)
    if kind == "validate_create":
        if not isinstance(value, (list, tuple)):
            return ["validate_create returns a list of refusal reasons"]
        return [f"[{i}] {why}" for i, p in enumerate(value)
                if (why := _problem_text(p, PROBLEM_MAX_CHARS))]
    return [f"there is no hook {kind!r}"]


def _fields(value: Any) -> list[str]:
    from yaaf_plugin_api.manifest import SLUG

    if not isinstance(value, (list, tuple)):
        return ["create_fields returns a list of FieldSpec"]
    if len(value) > MAX_FIELDS:
        return [f"at most {MAX_FIELDS} create fields"]
    out, keys = [], set()
    types = ("string", "text", "integer", "number", "boolean", "enum")
    for i, f in enumerate(value):
        if not isinstance(f, T.FieldSpec):
            out.append(f"[{i}] is not a FieldSpec")
            continue
        if not isinstance(f.key, str) or not SLUG.match(f.key):
            out.append(f"[{i}].key must match {SLUG.pattern}")
        elif f.key in keys:
            out.append(f"[{i}].key {f.key!r} is used twice")
        keys.add(f.key)
        if f.type not in types:
            out.append(f"[{i}].type is one of {', '.join(types)}")
        if _problem_text(f.label, 80):
            out.append(f"[{i}].label is required (≤80 chars)")
        if f.type == "enum" and (not f.choices or not all(isinstance(c, str) for c in f.choices)):
            out.append(f"[{i}] is an enum with no choices")
        if f.type != "enum" and f.choices:
            out.append(f"[{i}] has choices but is not an enum")
        if not _json_safe(f.default):
            out.append(f"[{i}].default is a JSON value")
    return out
