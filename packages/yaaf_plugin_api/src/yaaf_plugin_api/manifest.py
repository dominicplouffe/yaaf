"""`yaaf-plugin.toml`: the declaration the conformance suite holds a plugin to.

Everything a plugin does is declared here first (docs/plans/factory-plugins.md
§1.5). Core calls no hook the manifest did not declare, and the suite fails a
declared one the code does not keep. So this validator is strict in the
direction that matters: an unknown key is an error, not a key ignored, because
a typo such as `complection_gate = true` would otherwise switch a gate off with
no one told.

`schema/manifest.schema.json` says the same thing for editors and for AI
factories writing a plugin. This module is the source of truth; a test holds
the two to the same verdict on a corpus of manifests. Rules a JSON Schema
cannot say — names unique across sections, a review of an artifact that is
gated — are here only, and listed in `CROSS_FIELD_RULES` so the schema's
readers know they exist.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from yaaf_plugin_api.version import API_VERSION, compatible, parse_range, parse_version

FILENAME = "yaaf-plugin.toml"

#: Built-in project modes a plugin kind may not take the name of.
CORE_KINDS = frozenset({"explore", "deliver", "operate", "code_factory"})
SHAPES = ("ships_once", "runs_on_cycles")
INDEPENDENCE = ("hash", "strict")
#: Ceilings core holds whatever a manifest asks for.
LIMIT_CEILINGS = {"hook_timeout_s": 30, "action_timeout_s": 1800, "memory_mb": 2048}
LIMIT_DEFAULTS = {"hook_timeout_s": 10, "action_timeout_s": 600, "memory_mb": 512}

PLUGIN_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")
KIND_KEY = re.compile(r"^[a-z][a-z0-9_]{2,39}$")
SLUG = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
MODULE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*$")

CROSS_FIELD_RULES = (
    "every artifact, review, platform page and data-action output name is unique across them",
    "review.of names an artifact listed in capabilities.artifacts",
    "platform_pages needs at least one gated artifact to render from",
    "at least one mode is open, and mode keys are unique",
    "kind.lead is not also one of kind.crews",
    "an agent_files path is relative, inside the repo, and not a hidden file",
)

_TOP = {"plugin", "kind", "capabilities", "limits"}
_PLUGIN = {"id", "name", "version", "api", "module", "license", "homepage", "description"}
_KIND = {"key", "label", "lead", "crews", "modes"}
_MODE = {"key", "label", "shape", "open"}
_CAPS = {"brief", "plan_gate", "completion_gate", "coverage", "state", "artifacts",
         "platform_pages", "review", "receipts", "data_actions", "agent_files",
         "setup_steps", "view", "create_fields"}
_REVIEW = {"of", "artifact", "rederive_tolerance", "independent"}
_ACTION = {"description", "tools", "outputs"}


class ManifestProblem(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class Mode:
    key: str
    shape: str
    open: bool
    label: str = ""


@dataclass(frozen=True)
class Review:
    of: str
    artifact: str
    rederive_tolerance: float = 0.05
    independent: str = "hash"


@dataclass(frozen=True)
class DataAction:
    name: str
    tools: tuple[str, ...]
    outputs: tuple[str, ...] = ()
    description: str = ""


@dataclass(frozen=True)
class Manifest:
    id: str
    name: str
    version: str
    api: str
    module: str
    description: str
    kind_key: str
    kind_label: str
    modes: tuple[Mode, ...]
    lead: str | None = None
    crews: tuple[str, ...] = ()
    license: str = ""
    homepage: str = ""
    brief: bool = False
    plan_gate: bool = False
    completion_gate: bool = False
    coverage: bool = False
    state: bool = False
    artifacts: tuple[str, ...] = ()
    platform_pages: tuple[str, ...] = ()
    review: Review | None = None
    receipts: bool = False
    receipt_sources: tuple[str, ...] | None = None  # None = any source
    data_actions: tuple[DataAction, ...] = ()
    agent_files: Mapping[str, str] = field(default_factory=dict)
    setup_steps: tuple[str, ...] = ()
    view: str | None = None
    create_fields: bool = False
    limits: Mapping[str, int] = field(default_factory=lambda: dict(LIMIT_DEFAULTS))

    @property
    def kind(self) -> str:
        """The project kind core stores: `plugin:<key>`."""
        return f"plugin:{self.kind_key}"

    def reserved_names(self) -> tuple[str, ...]:
        """Names no seat may publish: the platform writes them."""
        return (*self.platform_pages, *(o for a in self.data_actions for o in a.outputs))


# ── parsing ────────────────────────────────────────────────────────────────────


def load(text: str | bytes) -> Manifest:
    """The manifest, or ManifestProblem naming every reason it is not one."""
    if isinstance(text, bytes):
        try:
            text = text.decode("utf-8")
        except UnicodeDecodeError:
            raise ManifestProblem([f"{FILENAME} is not UTF-8"]) from None
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ManifestProblem([f"{FILENAME} is not valid TOML ({exc})"]) from None
    problems = validate(raw)
    if problems:
        raise ManifestProblem(problems)
    return _build(raw)


def load_path(root: str | Path) -> Manifest:
    path = Path(root) / FILENAME
    if not path.is_file():
        raise ManifestProblem([f"no {FILENAME} at the root of the repo"])
    return load(path.read_bytes())


def _build(raw: Mapping) -> Manifest:
    p, k, c = raw["plugin"], raw["kind"], raw.get("capabilities") or {}
    review = c.get("review")
    receipts = c.get("receipts")
    sources = None
    if isinstance(receipts, Mapping) and isinstance(receipts.get("sources"), list):
        sources = tuple(receipts["sources"])
    return Manifest(
        id=p["id"], name=p["name"], version=p["version"], api=p["api"], module=p["module"],
        description=p["description"], license=p.get("license", ""),
        homepage=p.get("homepage", ""), kind_key=k["key"], kind_label=k.get("label") or p["name"],
        lead=k.get("lead"), crews=tuple(k.get("crews") or ()),
        modes=tuple(Mode(m["key"], m["shape"], bool(m.get("open", False)), m.get("label", ""))
                    for m in k["modes"]),
        brief=bool(c.get("brief")), plan_gate=bool(c.get("plan_gate")),
        completion_gate=bool(c.get("completion_gate")), coverage=bool(c.get("coverage")),
        state=bool(c.get("state")), artifacts=tuple(c.get("artifacts") or ()),
        platform_pages=tuple(c.get("platform_pages") or ()),
        review=Review(review["of"], review["artifact"],
                      float(review.get("rederive_tolerance", 0.05)),
                      review.get("independent", "hash")) if review else None,
        receipts=bool(receipts), receipt_sources=sources,
        data_actions=tuple(DataAction(n, tuple(a["tools"]), tuple(a.get("outputs") or ()),
                                      a.get("description", ""))
                           for n, a in (c.get("data_actions") or {}).items()),
        agent_files=dict(c.get("agent_files") or {}),
        setup_steps=tuple(c.get("setup_steps") or ()), view=c.get("view"),
        create_fields=bool(c.get("create_fields")),
        limits={**LIMIT_DEFAULTS, **(raw.get("limits") or {})},
    )


# ── validation ─────────────────────────────────────────────────────────────────


def _str(v: Any, limit: int = 200) -> bool:
    return isinstance(v, str) and bool(v.strip()) and len(v) <= limit


def plain_name(name: Any) -> bool:
    """An artifact name as artifact_put accepts one: a file name, never a path."""
    return (isinstance(name, str) and 0 < len(name) <= 200 and name.strip() == name
            and not any(ch in name for ch in "/\\\x00") and name not in (".", ".."))


def _unknown(where: str, got: Mapping, allowed: set[str]) -> list[str]:
    extra = sorted(set(got) - allowed)
    return [f"{where} has unknown keys: {', '.join(extra)} (allowed: "
            f"{', '.join(sorted(allowed))})"] if extra else []


def _names(where: str, value: Any) -> list[str]:
    if not isinstance(value, list) or not all(plain_name(n) for n in value):
        return [f"{where} is a list of plain file names (no paths)"]
    if len(set(value)) != len(value):
        return [f"{where} names the same file twice"]
    return []


def validate(raw: Any) -> list[str]:
    """Every problem with a parsed manifest, or [] when it is one."""
    if not isinstance(raw, Mapping):
        return ["the manifest must be a TOML table"]
    out = _unknown("the manifest", raw, _TOP)
    out += _plugin(raw.get("plugin"))
    out += _kind(raw.get("kind"))
    caps = raw.get("capabilities", {})
    if not isinstance(caps, Mapping):
        out.append("[capabilities] must be a table")
    else:
        out += _capabilities(caps)
    limits = raw.get("limits", {})
    if not isinstance(limits, Mapping):
        out.append("[limits] must be a table")
    else:
        out += _unknown("[limits]", limits, set(LIMIT_CEILINGS))
        for key, ceiling in LIMIT_CEILINGS.items():
            v = limits.get(key)
            if v is not None and not (isinstance(v, int) and not isinstance(v, bool)
                                      and 1 <= v <= ceiling):
                out.append(f"limits.{key} is an integer from 1 to {ceiling}")
    return out


def _plugin(p: Any) -> list[str]:
    if not isinstance(p, Mapping):
        return ["[plugin] is required"]
    out = _unknown("[plugin]", p, _PLUGIN)
    if not isinstance(p.get("id"), str) or not PLUGIN_ID.match(p["id"]):
        out.append(f"plugin.id must match {PLUGIN_ID.pattern} (3-40 chars, lowercase, dashes)")
    for key, limit in (("name", 80), ("description", 500)):
        if not _str(p.get(key), limit):
            out.append(f"plugin.{key} is required (≤{limit} chars)")
    try:
        parse_version(p.get("version") if isinstance(p.get("version"), str) else "")
    except ValueError:
        out.append("plugin.version is the plugin's own version, dotted integers such as 1.2.0")
    api = p.get("api")
    try:
        parse_range(api if isinstance(api, str) else "")
        if not compatible(api):
            out.append(f"plugin.api {api!r} excludes this API ({API_VERSION}); the plugin was "
                       "written for a different major version")
    except ValueError as exc:
        out.append(f"plugin.api: {exc}")
    if not isinstance(p.get("module"), str) or not MODULE.match(p["module"]):
        out.append('plugin.module is "package.module:object", the FactoryPlugin instance')
    for key in ("license", "homepage"):
        if key in p and not _str(p[key], 300):
            out.append(f"plugin.{key} is a non-empty string when given")
    if "homepage" in p and isinstance(p["homepage"], str) and \
            not p["homepage"].startswith("https://"):
        out.append("plugin.homepage must be https://")
    return out


def _kind(k: Any) -> list[str]:
    if not isinstance(k, Mapping):
        return ["[kind] is required"]
    out = _unknown("[kind]", k, _KIND)
    key = k.get("key")
    if not isinstance(key, str) or not KIND_KEY.match(key):
        out.append(f"kind.key must match {KIND_KEY.pattern}")
    elif key in CORE_KINDS:
        out.append(f"kind.key {key!r} is a built-in kind; choose another")
    if "label" in k and not _str(k["label"], 80):
        out.append("kind.label is a non-empty string (≤80 chars) when given")
    if "lead" in k and (not isinstance(k["lead"], str) or not SLUG.match(k["lead"])):
        out.append("kind.lead is a role slug from the plugin's roles/")
    crews = k.get("crews", [])
    if not isinstance(crews, list) or not all(isinstance(c, str) and SLUG.match(c) for c in crews):
        out.append("kind.crews is a list of crew slugs from the plugin's crews/")
    elif k.get("lead") in crews:
        out.append("kind.lead names a role, kind.crews names crews: the same slug is in both")
    modes = k.get("modes")
    if not isinstance(modes, list) or not modes:
        return [*out, "kind.modes needs at least one [[kind.modes]]"]
    keys = []
    for i, m in enumerate(modes):
        where = f"kind.modes[{i}]"
        if not isinstance(m, Mapping):
            out.append(f"{where} must be a table")
            continue
        out += _unknown(where, m, _MODE)
        if not isinstance(m.get("key"), str) or not SLUG.match(m["key"]):
            out.append(f"{where}.key must match {SLUG.pattern}")
        else:
            keys.append(m["key"])
        if m.get("shape") not in SHAPES:
            out.append(f"{where}.shape is ships_once or runs_on_cycles: how core treats the "
                       "project everywhere (one finish line, or standing obligations)")
        if "open" in m and not isinstance(m["open"], bool):
            out.append(f"{where}.open is true or false")
        if "label" in m and not _str(m["label"], 80):
            out.append(f"{where}.label is a non-empty string when given")
    if len(set(keys)) != len(keys):
        out.append("kind.modes has two modes with the same key")
    if not any(isinstance(m, Mapping) and m.get("open") is True for m in modes):
        out.append("kind.modes needs at least one open mode, or nothing can be created")
    return out


def _capabilities(c: Mapping) -> list[str]:
    out = _unknown("[capabilities]", c, _CAPS)
    for flag in ("brief", "plan_gate", "completion_gate", "coverage", "state", "create_fields"):
        if flag in c and not isinstance(c[flag], bool):
            out.append(f"capabilities.{flag} is true or false")
    named: list[tuple[str, str]] = []
    for key in ("artifacts", "platform_pages"):
        if key in c:
            problems = _names(f"capabilities.{key}", c[key])
            out += problems
            if not problems:
                named += [(n, key) for n in c[key]]
    if c.get("platform_pages") and not c.get("artifacts"):
        out.append("capabilities.platform_pages renders from a gated artifact: list one in "
                   "capabilities.artifacts")
    review = c.get("review")
    if review is not None:
        if not isinstance(review, Mapping):
            out.append("capabilities.review is a table {of, artifact, ...}")
        else:
            out += _unknown("capabilities.review", review, _REVIEW)
            for key in ("of", "artifact"):
                if not plain_name(review.get(key)):
                    out.append(f"capabilities.review.{key} is a plain file name")
            if plain_name(review.get("of")) and review["of"] not in (c.get("artifacts") or []):
                out.append(f"capabilities.review.of {review['of']!r} must be one of "
                           "capabilities.artifacts: a review is of an artifact the plugin gates")
            if plain_name(review.get("artifact")):
                named.append((review["artifact"], "review.artifact"))
            tol = review.get("rederive_tolerance", 0.05)
            if not (isinstance(tol, (int, float)) and not isinstance(tol, bool)
                    and 0 < tol <= 0.25):
                out.append("capabilities.review.rederive_tolerance is a fraction above 0 and "
                           "at most 0.25")
            if review.get("independent", "hash") not in INDEPENDENCE:
                out.append('capabilities.review.independent is "hash" (no publisher of the '
                           'same bytes) or "strict" (no publisher of any version)')
    receipts = c.get("receipts")
    if receipts is not None:
        ok = receipts is True or (
            isinstance(receipts, Mapping) and set(receipts) <= {"sources"}
            and (receipts.get("sources", "any") == "any"
                 or (isinstance(receipts.get("sources"), list)
                     and all(_str(s, 64) for s in receipts["sources"]))))
        if not ok:
            out.append('capabilities.receipts is true, or {sources = "any" | [source names]}')
    actions = c.get("data_actions")
    if actions is not None:
        if not isinstance(actions, Mapping) or not actions:
            out.append("capabilities.data_actions is a table of [capabilities.data_actions.<name>]")
        else:
            for name, a in actions.items():
                where = f"capabilities.data_actions.{name}"
                if not SLUG.match(str(name)):
                    out.append(f"{where}: the action name must match {SLUG.pattern}")
                if not isinstance(a, Mapping):
                    out.append(f"{where} must be a table")
                    continue
                out += _unknown(where, a, _ACTION)
                tools = a.get("tools")
                if not isinstance(tools, list) or not tools or \
                        not all(_str(t, 128) for t in tools):
                    out.append(f"{where}.tools lists every data-source tool it may call: the "
                               "gateway refuses any other")
                if "outputs" in a:
                    problems = _names(f"{where}.outputs", a["outputs"])
                    out += problems
                    if not problems:
                        named += [(n, where) for n in a["outputs"]]
                if "description" in a and not _str(a["description"], 500):
                    out.append(f"{where}.description is a non-empty string when given")
    files = c.get("agent_files")
    if files is not None:
        if not isinstance(files, Mapping) or not files:
            out.append("capabilities.agent_files is a table: name = \"path/in/repo\"")
        else:
            for name, path in files.items():
                if not SLUG.match(str(name)):
                    out.append(f"capabilities.agent_files: {name!r} must match {SLUG.pattern}")
                if not _repo_path(path):
                    out.append(f"capabilities.agent_files.{name} is a relative path inside "
                               "the repo, not hidden")
    steps = c.get("setup_steps")
    if steps is not None and (not isinstance(steps, list) or not all(
            isinstance(s, str) and SLUG.match(s) for s in steps) or len(set(steps)) != len(steps)):
        out.append(f"capabilities.setup_steps is a list of distinct keys matching {SLUG.pattern}")
    if "view" in c and not (_repo_path(c["view"]) and str(c["view"]).endswith(".json")):
        out.append("capabilities.view is the repo path of the view's JSON, such as ui/view.json")
    seen: dict[str, str] = {}
    for name, where in named:
        if name in seen:
            out.append(f"{name!r} is named by both {seen[name]} and {where}: each name has one "
                       "owner")
        else:
            seen[name] = where
    return out


def _repo_path(path: Any) -> bool:
    if not isinstance(path, str) or not path or len(path) > 300 or "\\" in path:
        return False
    parts = path.split("/")
    return not path.startswith("/") and all(p and p not in ("..", ".") and not p.startswith(".")
                                            for p in parts)
