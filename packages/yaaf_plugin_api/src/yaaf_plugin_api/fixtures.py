"""The inputs a plugin ships for its conformance suite, read from `fixtures/`.

    fixtures/
      artifacts/accept/<artifact name>/<case>              cases a gate accepts
      artifacts/refuse/<artifact name>/<case>              cases it refuses, each with
      artifacts/refuse/<artifact name>/<case>.expect.json  {"contains": ["words", ...]}
                                       (either kind may add "state": "<a states/ name>"
                                       to be asked in that state, not a new project)
      sources/<source>/tools.json      {"tools": [{"name", "description"}]}
      sources/<source>/calls/<tool>.json
                                       {"arguments": {...}, "result": {...}}  (or a list of them)
      states/<name>.json               optional snapshots: {"mode", "kind_state",
                                       "artifacts": [{"name", "version"}], "capabilities": [...]}
      create/accept/<case>.json        optional kind_fields validate_create accepts
      create/refuse/<case>.json        ... and refuses

A review fixture may write "$SUBJECT_SHA256" for the subject's hash. The suite
substitutes the sha256 of the reviewed artifact's first accept case, as the
plugin's own check_artifact stored it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from yaaf_plugin_api import types as T
from yaaf_plugin_api.manifest import Manifest
from yaaf_plugin_api.repo import EXPECT_SUFFIX, FIXTURES

#: The day every conformance snapshot is taken on, so two runs see one clock.
TODAY = date(2027, 1, 1)
SUBJECT_TOKEN = "$SUBJECT_SHA256"


@dataclass(frozen=True)
class Case:
    name: str
    data: bytes
    contains: tuple[str, ...] = ()
    #: The fixture state this case is asked in; None is a new project.
    state: str | None = None


@dataclass
class Fixtures:
    root: Path
    accept: dict[str, list[Case]] = field(default_factory=dict)
    refuse: dict[str, list[Case]] = field(default_factory=dict)
    tools: list[T.ToolInfo] = field(default_factory=list)
    calls: dict[str, list[dict]] = field(default_factory=dict)
    states: dict[str, dict] = field(default_factory=dict)
    create_accept: list[dict] = field(default_factory=list)
    create_refuse: list[dict] = field(default_factory=list)


def _cases(folder: Path) -> list[Case]:
    if not folder.is_dir():
        return []
    out = []
    for path in sorted(folder.iterdir()):
        if not path.is_file() or path.name.endswith(EXPECT_SUFFIX):
            continue
        expect = path.with_name(path.name + EXPECT_SUFFIX)
        contains: tuple[str, ...] = ()
        state = None
        if expect.is_file():
            try:
                body = json.loads(expect.read_text("utf-8"))
                contains = tuple(body.get("contains") or ())
                state = body.get("state") if isinstance(body.get("state"), str) else None
            except (ValueError, AttributeError):
                contains = ()
        out.append(Case(path.name, path.read_bytes(), contains, state))
    return out


def _json_files(folder: Path) -> list[tuple[str, Any]]:
    if not folder.is_dir():
        return []
    out = []
    for path in sorted(folder.glob("*.json")):
        try:
            out.append((path.stem, json.loads(path.read_text("utf-8"))))
        except ValueError:
            out.append((path.stem, None))
    return out


def load(root: str | Path, manifest: Manifest) -> Fixtures:
    root = Path(root)
    base = root / FIXTURES
    fx = Fixtures(root)
    names = [*manifest.artifacts, *([manifest.review.artifact] if manifest.review else [])]
    for name in names:
        fx.accept[name] = _cases(base / "artifacts" / "accept" / name)
        fx.refuse[name] = _cases(base / "artifacts" / "refuse" / name)
    sources = base / "sources"
    if sources.is_dir():
        for src in sorted(p for p in sources.iterdir() if p.is_dir()):
            try:
                listing = json.loads((src / "tools.json").read_text("utf-8"))
            except (OSError, ValueError):
                listing = {}
            for t in listing.get("tools") or []:
                if isinstance(t, dict) and isinstance(t.get("name"), str):
                    fx.tools.append(T.ToolInfo(src.name, t["name"], str(t.get("description", ""))))
            for tool, body in _json_files(src / "calls"):
                entries = body if isinstance(body, list) else [body]
                fx.calls.setdefault(tool, []).extend(e for e in entries if isinstance(e, dict))
    for name, body in _json_files(base / "states"):
        if isinstance(body, dict):
            fx.states[name] = body
    fx.create_accept = [b for _, b in _json_files(base / "create" / "accept") if isinstance(b, dict)]
    fx.create_refuse = [b for _, b in _json_files(base / "create" / "refuse") if isinstance(b, dict)]
    return fx


def snapshot(manifest: Manifest, state: dict | None = None) -> T.ProjectSnapshot:
    """A snapshot of a project of this kind, as a fixture state describes it.
    With no state: a new project, nothing published, nothing connected."""
    state = state or {}
    open_modes = [m.key for m in manifest.modes if m.open] or [manifest.modes[0].key]
    artifacts = tuple(
        T.ArtifactMeta(str(a.get("name")), int(a.get("version", 1)), "0" * 64,
                       int(a.get("size_bytes", 0)), a.get("published_by_seat"), None,
                       bool(a.get("by_platform", False)))
        for a in state.get("artifacts") or [] if isinstance(a, dict) and a.get("name"))
    caps = frozenset(str(c) for c in state.get("capabilities") or [])
    return T.ProjectSnapshot(
        project_id="conformance", kind=manifest.kind_key,
        mode=str(state.get("mode") or open_modes[0]), plugin_version="conformance",
        today=TODAY, kind_state=dict(state.get("kind_state") or {}),
        coverage=T.Coverage(capabilities=caps), artifacts=artifacts, lane="run")


def states(manifest: Manifest, fx: Fixtures) -> dict[str, T.ProjectSnapshot]:
    """Every state the suite asks the hooks in: a new project, and each fixture state."""
    out = {"new": snapshot(manifest)}
    for name, body in fx.states.items():
        out[name] = snapshot(manifest, body)
    return out


class FakeGateway:
    """A data source built from fixtures/sources: answers the recorded result for
    a tool, raises ToolError for one with no recording, and remembers each call."""

    def __init__(self, calls: dict[str, list[dict]], *, fail: str | None = None,
                 off: set[str] | None = None):
        self._calls = calls
        self.fail = fail
        self.off = off or set()
        self.seen: list[str] = []

    def call(self, tool: str, arguments=None):
        self.seen.append(str(tool))
        if self.fail:
            raise T.ToolError(f"the source is {self.fail}", state=self.fail)
        if tool in self.off:
            raise T.ToolError(f"{tool} is switched off for this source", state="denied")
        recorded = self._calls.get(tool) or []
        args = dict(arguments or {})
        match = next((c for c in recorded if c.get("arguments") == args), None) or \
            (recorded[0] if recorded else None)
        if match is None:
            raise T.ToolError(f"no recording of {tool} in fixtures/sources", state="unreachable")
        result = match.get("result")
        return result if isinstance(result, dict) else {"structuredContent": result}
