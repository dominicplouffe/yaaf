"""The conformance suite: did this plugin keep the promises the spec asks of it?

One suite, two runners. An author runs it with `python -m yaaf_plugin_api
check --full`, through `client.LocalHost`. yaaf runs it at install through its
sandboxed plugin host. The suite is handed one function, `call`, that asks a
hook and either returns the validated answer or raises `Failure`. So the two
runs ask the same questions and judge the answers the same way. A plugin green
here and red at install is a bug in yaaf.

What it decides is behaviour, never wisdom: that every hook answers usable
values on the plugin's fixtures and survives the fuzz corpus, without raising,
hanging or reaching out, and the same way twice; that each gate gives the
verdict its fixtures expect; and the extended promise of each capability the
manifest declares. Requirements that core holds for every plugin (MIN-14, the
review's independence and arithmetic) are reported as held by core, naming
the tests that hold them. No plugin code can change them, so no plugin's run
of this suite needs to retest them.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from yaaf_plugin_api import corpus, wire
from yaaf_plugin_api import fixtures as F
from yaaf_plugin_api import types as T
from yaaf_plugin_api import view as V
from yaaf_plugin_api.hooks import HookKey
from yaaf_plugin_api.manifest import Manifest
from yaaf_plugin_api.requirements import BY_ID, applicable

#: How core holds each requirement no plugin's code can affect.
HELD_BY_CORE = {
    "MIN-14": "core asks its own rules before a plugin and only adds a plugin's refusals "
              "(tests/selftest_invariants/test_plugin_kinds.py, test_plugin_primitives.py)",
    "EXT-REV-1": "core binds a review to the subject's hash and refuses a seat that published "
                 "it (tests/selftest_invariants/test_plugin_primitives.py)",
    "EXT-REV-2": "core compares re-derivations with the subject's own numbers and never reads "
                 "a review's (tests/selftest_invariants/test_plugin_primitives.py)",
    "EXT-REV-3": "core holds completion on a passing review of the latest subject "
                 "(tests/selftest_invariants/test_plugin_primitives.py)",
}
#: Kinds of failure, and the requirement each one breaks.
BREAKS = {"timeout": "MIN-10", "violation": "MIN-12", "busy": "MIN-10"}
#: The extended requirement a hook's own failure also breaks.
EXTENDED_OF = {"render": "EXT-PAGE-1", "view": "EXT-VIEW-1", "fields_of_read": "EXT-RCT-1",
               "brief": "EXT-BRF-1", "data_action": "EXT-ACT-1", "setup_step": "EXT-STP-1",
               "create_fields": "EXT-CRT-1", "validate_create": "EXT-CRT-1",
               "plan_gate": "EXT-GATE-1", "completion_gate": "EXT-GATE-1",
               "migrate_state": "MIN-15"}
FUZZ_BREAKS = ("raised", "wire", "crash", "invalid", "protocol", "missing")


class Failure(Exception):
    def __init__(self, kind: str, message: str, violations: list[str] | None = None):
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.message = message
        self.violations = list(violations or [])


#: (hook, args, gateway or None) → the hook's validated answer, or raises Failure.
Call = Callable[[HookKey, list, Any], Any]
#: The plugin's own tests: () → (status, [why]).
RunTests = Callable[[], tuple[str, list[str]]]


class _Run:
    def __init__(self, root: Path, manifest: Manifest, call: Call):
        self.root, self.manifest, self.call = root, manifest, call
        self.fx = F.load(root, manifest)
        self.states = F.states(manifest, self.fx)
        self.problems: dict[str, list[str]] = {}
        self.checked: set[str] = set()

    def bad(self, rid: str, text: str) -> None:
        self.checked.add(rid)
        found = self.problems.setdefault(rid, [])
        if len(found) < 8 and text not in found:
            found.append(text[:400])

    def ok(self, rid: str) -> None:
        self.checked.add(rid)

    def ask(self, key: HookKey, args: list, *, gateway=None, during: str,
            fuzz: bool = False, extended: bool = True) -> tuple[bool, Any]:
        try:
            return True, self.call(key, args, gateway)
        except Failure as exc:
            rid = BREAKS.get(exc.kind)
            if rid is None:
                if exc.kind == "invalid" and "problems[" in exc.message:
                    rid = "MIN-17"
                else:
                    rid = "MIN-09" if fuzz and exc.kind in FUZZ_BREAKS else "MIN-08"
            self.bad(rid, f"@hook.{key} {during}: {exc.kind}: {exc.message}")
            if extended and key.kind in EXTENDED_OF and rid not in ("MIN-10", "MIN-12"):
                self.bad(EXTENDED_OF[key.kind], f"@hook.{key} {during}: {exc.message}")
            for v in exc.violations:
                self.bad("MIN-12", f"@hook.{key} {during}: {v}")
            return False, None

    def twice(self, key: HookKey, args: list, *, during: str, gateway_factory=None) -> Any:
        """Ask twice; MIN-11 fails if the two answers differ."""
        ok1, a = self.ask(key, args, gateway=gateway_factory() if gateway_factory else None,
                          during=during)
        ok2, b = self.ask(key, args, gateway=gateway_factory() if gateway_factory else None,
                          during=during)
        if ok1 and ok2:
            self.ok("MIN-11")
            self.ok("MIN-08")
            if _same(a) != _same(b):
                self.bad("MIN-11", f"@hook.{key} {during}: two calls on the same input gave "
                                   "different answers")
        return a if ok1 else None


def _same(value: Any) -> str:
    try:
        return json.dumps(wire.encode(value), sort_keys=True)
    except wire.WireError:
        return repr(value)


def _upload(name: str, data: bytes) -> T.Upload:
    return T.Upload(name, data, "seat-author", "run-conformance")


# ── the checks ─────────────────────────────────────────────────────────────────


def _gates(r: _Run) -> dict[str, tuple[bytes, dict]]:
    """MIN-13, EXT-ART-1/2 on every gated artifact. Returns each one's first
    accepted case, stored as the plugin normalised it, for the review and pages."""
    m, ctx = r.manifest, r.states["new"]
    accepted: dict[str, tuple[bytes, dict]] = {}
    def in_state(case):
        if case.state is None:
            return ctx
        if case.state not in r.states:
            r.bad("MIN-13", f"case {case.name} names state {case.state!r}, which "
                            "fixtures/states/ does not have")
        return r.states.get(case.state, ctx)
    for name in m.artifacts:
        key = HookKey("check_artifact", name)
        for case in r.fx.accept.get(name, []):
            verdict = r.twice(key, [in_state(case), _upload(name, case.data)],
                              during=f"on accept case {case.name}")
            if isinstance(verdict, T.Refuse):
                r.bad("MIN-13", f"{name}: accept case {case.name} was refused: "
                                + " · ".join(verdict.problems)[:200])
            elif isinstance(verdict, T.Accept):
                r.ok("MIN-13")
                stored = verdict.data if verdict.data is not None else case.data
                accepted.setdefault(name, (stored, dict(verdict.load_bearing or {})))
                if verdict.state_patch and not m.state:
                    r.bad("MIN-15", f"{name}: an accept patches kind_state, but the manifest "
                                    "does not declare state = true (so no migrate_state)")
                ok, again = r.ask(key, [in_state(case), _upload(name, stored)],
                                  during=f"on the stored bytes of {case.name}")
                if ok and not isinstance(again, T.Accept):
                    r.bad("EXT-ART-2", f"{name}: the bytes stored for accept case {case.name}, "
                                       "published again, were refused")
                elif ok:
                    r.ok("EXT-ART-2")
        for case in r.fx.refuse.get(name, []):
            ok, verdict = r.ask(key, [in_state(case), _upload(name, case.data)],
                                during=f"on refuse case {case.name}")
            if ok and not isinstance(verdict, T.Refuse):
                r.bad("MIN-13", f"{name}: refuse case {case.name} was accepted")
            elif ok:
                said = " ".join(verdict.problems)
                missing = [c for c in case.contains if c not in said]
                if missing:
                    r.bad("MIN-13", f"{name}: refuse case {case.name}'s refusal does not say "
                                    f"{', '.join(map(repr, missing))}")
                else:
                    r.ok("MIN-13")
        for label, data in corpus.UPLOADS:
            ok, verdict = r.ask(key, [ctx, _upload(name, data)], during=f"on fuzz {label}",
                                fuzz=True)
            if ok:
                r.ok("MIN-09")
                if isinstance(verdict, T.Accept):
                    r.bad("EXT-ART-1", f"{name}: the fuzz case {label!r} was accepted")
                else:
                    r.ok("EXT-ART-1")
    return accepted


def _review(r: _Run, accepted: dict[str, tuple[bytes, dict]]) -> None:
    spec = r.manifest.review
    if spec is None:
        return
    key = HookKey("check_review", spec.artifact)
    if spec.of not in accepted:
        r.bad("MIN-13", f"{spec.artifact}: no accept case of {spec.of} was accepted, so there "
                        "is no subject to review")
        return
    stored, lb = accepted[spec.of]
    sha = hashlib.sha256(stored).hexdigest()
    body = T.ArtifactBody(T.ArtifactMeta(spec.of, 1, sha, len(stored), "seat-author", None),
                          stored, lb)
    ctx = r.states["new"]

    def review(data: bytes) -> T.ReviewUpload:
        return T.ReviewUpload(spec.artifact, data.replace(F.SUBJECT_TOKEN.encode(),
                                                          sha.encode()),
                              "seat-reviewer", "run-conformance", subject_sha256=sha)
    for case in r.fx.accept.get(spec.artifact, []):
        verdict = r.twice(key, [ctx, review(case.data), body], during=f"on accept case "
                                                                      f"{case.name}")
        if isinstance(verdict, T.Refuse):
            r.bad("MIN-13", f"{spec.artifact}: accept case {case.name} was refused: "
                            + " · ".join(verdict.problems)[:200])
        elif verdict is not None:
            r.ok("MIN-13")
    for case in r.fx.refuse.get(spec.artifact, []):
        ok, verdict = r.ask(key, [ctx, review(case.data), body],
                            during=f"on refuse case {case.name}")
        if ok and not isinstance(verdict, T.Refuse):
            r.bad("MIN-13", f"{spec.artifact}: refuse case {case.name} was accepted")
        elif ok:
            missing = [c for c in case.contains if c not in " ".join(verdict.problems)]
            if missing:
                r.bad("MIN-13", f"{spec.artifact}: refuse case {case.name}'s refusal does not "
                                f"say {', '.join(map(repr, missing))}")
            else:
                r.ok("MIN-13")
    for label, data in corpus.UPLOADS:
        if r.ask(key, [ctx, review(data), body], during=f"on fuzz {label}", fuzz=True)[0]:
            r.ok("MIN-09")


def _pages(r: _Run, accepted: dict[str, tuple[bytes, dict]]) -> None:
    m = r.manifest
    if not m.platform_pages:
        return
    source = m.review.of if m.review else m.artifacts[0]
    if source not in accepted:
        r.bad("EXT-PAGE-1", f"no accept case of {source} was accepted, so nothing could be "
                            "rendered")
        return
    stored, lb = accepted[source]
    meta = T.ArtifactMeta(source, 1, hashlib.sha256(stored).hexdigest(), len(stored), None, None)
    body = T.ArtifactBody(meta, stored, lb)
    names = []
    for reviewed in (False, True):
        pages = r.twice(HookKey("render"), [r.states["new"], body, reviewed],
                        during=f"rendering {'final' if reviewed else 'draft'} pages")
        if pages is not None:
            r.ok("EXT-PAGE-1")
            names.append(sorted(p.name for p in pages))
    if len(names) == 2:
        if names[0] != names[1]:
            r.bad("EXT-PAGE-2", f"the draft renders {names[0]} and the final {names[1]}: the "
                                "same pages, a review apart")
        else:
            r.ok("EXT-PAGE-2")


def _receipts(r: _Run) -> None:
    if not r.manifest.receipts:
        return
    key = HookKey("fields_of_read")
    recorded = [(tool, c.get("result")) for tool, cs in r.fx.calls.items() for c in cs]
    for tool, result in recorded:
        if isinstance(result, dict):
            r.twice(key, [tool, result], during=f"on the recorded {tool} result")
            r.ok("EXT-RCT-1")
    for label, result in corpus.RESULTS:
        if r.ask(key, ["any_tool", result], during=f"on fuzz result {label}", fuzz=True)[0]:
            r.ok("MIN-09")
            r.ok("EXT-RCT-1")


def _actions(r: _Run) -> None:
    for action in r.manifest.data_actions:
        key = HookKey("data_action", action.name)
        gw = F.FakeGateway(r.fx.calls)
        ok, result = r.ask(key, [r.states["new"]], gateway=gw, during="on the fake source")
        if ok:
            r.ok("EXT-ACT-1")
        stray = sorted(set(gw.seen) - set(action.tools))
        if stray:
            r.bad("EXT-ACT-3", f"action {action.name} called {', '.join(stray)}, which its "
                               "manifest does not list")
        else:
            r.ok("EXT-ACT-3")
        for fail in ("unreachable", "needs_signin", "error", "timeout"):
            ok, _ = r.ask(key, [r.states["new"]], gateway=F.FakeGateway(r.fx.calls, fail=fail),
                          during=f"with the source {fail}", extended=False)
            if ok:
                r.ok("EXT-ACT-2")
            else:
                r.bad("EXT-ACT-2", f"action {action.name} did not answer when the source was "
                                   f"{fail}: it must catch ToolError and say so")
        ok, _ = r.ask(key, [r.states["new"]],
                      gateway=F.FakeGateway(r.fx.calls, off=set(action.tools)),
                      during="with every tool switched off", extended=False)
        if not ok:
            r.bad("EXT-ACT-2", f"action {action.name} did not answer with its tools switched off")


def _per_state(r: _Run) -> None:
    m = r.manifest
    view_doc = None
    if m.view:
        try:
            view_doc = json.loads((r.root / m.view).read_text("utf-8"))
        except (OSError, ValueError):
            r.bad("EXT-VIEW-1", f"{m.view} could not be read")
    for name, ctx in r.states.items():
        during = f"in state {name!r}"
        if m.brief and r.twice(HookKey("brief"), [ctx], during=during) is not None:
            r.ok("EXT-BRF-1")
        for gate in ("plan_gate", "completion_gate"):
            if not getattr(m, gate):
                continue
            owed = r.twice(HookKey(gate), [ctx], during=during)
            if owed is not None:
                r.ok("EXT-GATE-1")
                clears = (r.fx.states.get(name) or {}).get("clears") or []
                if gate in clears and owed:
                    r.bad("EXT-GATE-1", f"{gate} {during} should clear but owes "
                                        f"{', '.join(o.key for o in owed)}")
        for step in m.setup_steps:
            if r.twice(HookKey("setup_step", step), [ctx], during=during) is not None:
                r.ok("EXT-STP-1")
        if m.view and view_doc is not None:
            data = r.twice(HookKey("view"), [ctx], during=during)
            if data is not None:
                problems = V.validate_data(view_doc, data)
                for p in problems:
                    r.bad("EXT-VIEW-1", f"view {during}: {p}")
                if not problems:
                    r.ok("EXT-VIEW-1")
        if m.state and ctx.kind_state and r.twice(
                HookKey("migrate_state"), [dict(ctx.kind_state), "0.0.0"],
                during=during) is not None:
            r.ok("MIN-15")


def _coverage(r: _Run) -> None:
    if not r.manifest.coverage:
        return
    key = HookKey("classify_tools")
    r.twice(key, [list(r.fx.tools)], during="on the fixture sources' tools")
    for label, tools in corpus.TOOLS:
        if r.ask(key, [list(tools)], during=f"on fuzz tools {label}", fuzz=True)[0]:
            r.ok("MIN-09")


def _create(r: _Run) -> None:
    if not r.manifest.create_fields:
        return
    if r.twice(HookKey("create_fields"), [], during="listing the create fields") is not None:
        r.ok("EXT-CRT-1")
    mode = next((x.key for x in r.manifest.modes if x.open), r.manifest.modes[0].key)
    key = HookKey("validate_create")
    for fields in r.fx.create_accept:
        ok, refusals = r.ask(key, [T.CreateSpec(mode, fields)], during="on a create accept case")
        if ok and refusals:
            r.bad("EXT-CRT-1", "a create accept case was refused: " + "; ".join(refusals)[:200])
    for fields in r.fx.create_refuse:
        ok, refusals = r.ask(key, [T.CreateSpec(mode, fields)], during="on a create refuse case")
        if ok and not refusals:
            r.bad("EXT-CRT-1", "a create refuse case was accepted")
    for label, fields in corpus.FIELDS:
        if r.ask(key, [T.CreateSpec(mode, fields)], during=f"on fuzz fields {label}",
                 fuzz=True)[0]:
            r.ok("MIN-09")


def _tests_name_the_gates(root: Path, manifest: Manifest) -> list[str]:
    """MIN-16's static half: each gate is attacked by a test that names it."""
    text = "".join(p.read_text("utf-8", "replace") for p in (root / "tests").rglob("test_*.py")) \
        if (root / "tests").is_dir() else ""
    gated = [*manifest.artifacts, *([manifest.review.artifact] if manifest.review else [])]
    return [f"no test in tests/ names {name}: every gate ships an attack test" for name in gated
            if name not in text]


# ── the run ────────────────────────────────────────────────────────────────────


def run(root: str | Path, manifest: Manifest, call: Call, *,
        run_tests: RunTests | None = None) -> dict[str, tuple[str, list[str]]]:
    """Every requirement this suite decides: id → (pass | fail | pending, why)."""
    r = _Run(Path(root), manifest, call)
    accepted = _gates(r)
    _review(r, accepted)
    _pages(r, accepted)
    _receipts(r)
    _actions(r)
    _per_state(r)
    _coverage(r)
    _create(r)
    for problem in _tests_name_the_gates(r.root, manifest):
        r.bad("MIN-16", problem)
    out: dict[str, tuple[str, list[str]]] = {}
    for req in applicable(manifest):
        rid = req.id
        if rid in HELD_BY_CORE:
            out[rid] = ("pass", [f"held by core: {HELD_BY_CORE[rid]}"])
        elif rid == "EXT-CYC-1":
            out[rid] = ("fail", ["a mode that runs on cycles cannot be opened yet: core has no "
                                 "cycle model a plugin can declare obligations against"])
        elif rid == "MIN-16":
            if r.problems.get(rid):
                out[rid] = ("fail", r.problems[rid])
            elif run_tests is None:
                out[rid] = ("pending", ["the plugin's own tests have not been run here"])
            else:
                out[rid] = run_tests()
        elif rid in r.problems:
            out[rid] = ("fail", r.problems[rid])
        elif rid in r.checked:
            out[rid] = ("pass", [])
        elif rid in ("MIN-15",) and not manifest.state:
            out[rid] = ("pass", ["the plugin keeps no state"])
        elif BY_ID[rid].checked_by == "harness" or rid in ("MIN-10", "MIN-12"):
            # Decided by this run: nothing failed, and what ran, ran clean.
            out[rid] = ("pass", []) if r.checked else ("pending", ["nothing was asked"])
    return out
