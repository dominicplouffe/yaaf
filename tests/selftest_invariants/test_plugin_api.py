"""The plugin API's own promises (docs/plans/factory-plugins.md, build step 1).

The API is the spec a plugin is held to at install, so what is attacked here is
the spec itself: that a manifest which is not one is refused (and refused the
same way by the Python validator and the published JSON Schema), that code
which does not match its manifest is caught, that a hook's return core could not
act on is named, and that the requirement table the install reports against is
the one the design doc states. No app and no database: this package stands
alone, which is also something these tests hold it to.
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import sys
import tomllib
from datetime import date
from pathlib import Path

import jsonschema
import pytest
from yaaf_plugin_api import (
    Accept,
    ActionResult,
    Brief,
    Coverage,
    FactoryPlugin,
    Owed,
    Page,
    ProjectSnapshot,
    ReadFields,
    Refuse,
    StepState,
    hook,
    report,
    requirements,
    version,
    view,
)
from yaaf_plugin_api import hooks as H
from yaaf_plugin_api import manifest as M
from yaaf_plugin_api.__main__ import main as cli

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "packages" / "yaaf_plugin_api"
SRC = PKG / "src" / "yaaf_plugin_api"
EXAMPLE = PKG / "examples" / "grant-factory"
DOC = ROOT / "docs" / "plans" / "factory-plugins.md"
MANIFEST_SCHEMA = json.loads((SRC / "schema" / "manifest.schema.json").read_text())
VIEW_SCHEMA = json.loads((SRC / "schema" / "view.schema.json").read_text())


def _example_raw() -> dict:
    return tomllib.loads((EXAMPLE / M.FILENAME).read_text())


def _example() -> M.Manifest:
    return M.load((EXAMPLE / M.FILENAME).read_text())


def _schema_ok(raw) -> bool:
    return jsonschema.Draft202012Validator(MANIFEST_SCHEMA).is_valid(raw)


def _mutate(path: str, value):
    """The example manifest with one key set (value=...) or removed (value=DELETE)."""
    raw = json.loads(json.dumps(_example_raw()))
    *parents, last = path.split(".")
    node = raw
    for p in parents:
        node = node[int(p)] if isinstance(node, list) else node.setdefault(p, {})
    if value is DELETE:
        del node[last]
    elif isinstance(node, list):
        node[int(last)] = value
    else:
        node[last] = value
    return raw


DELETE = object()

# Each is a manifest that is not one, in a way a JSON Schema can see too.
SCHEMA_VISIBLE_BAD = [
    ("plugin.id", "Has Caps"), ("plugin.id", "ab"), ("plugin.id", DELETE),
    ("plugin.name", ""), ("plugin.name", "   "), ("plugin.version", "one"),
    ("plugin.api", "1.0"), ("plugin.api", ">=1.0;<2"), ("plugin.module", "no_colon"),
    ("plugin.homepage", "http://insecure.example"), ("plugin.nickname", "x"),
    ("kind.key", "deliver"), ("kind.key", "X"), ("kind.modes", []),
    ("kind.modes.0.shape", "sometimes"), ("kind.modes.0.open", False),
    ("kind.modes.0.open", "yes"), ("kind.lead", "Not A Slug"),
    ("capabilities.complection_gate", True),  # the typo that would switch a gate off
    ("capabilities.brief", "yes"), ("capabilities.artifacts", ["../escape.json"]),
    ("capabilities.artifacts", ["a.json", "a.json"]), ("capabilities.artifacts", [" pad.json"]),
    ("capabilities.review.rederive_tolerance", 0.9),
    ("capabilities.review.rederive_tolerance", 0),
    ("capabilities.review.independent", "loose"), ("capabilities.receipts", False),
    ("capabilities.receipts", {"sources": "some"}),
    ("capabilities.data_actions.call_for_proposals.tools", []),
    ("capabilities.data_actions.call_for_proposals.verbs", ["x"]),
    ("capabilities.agent_files", {"budget": "/etc/passwd"}),
    ("capabilities.agent_files", {"budget": "../outside.py"}),
    ("capabilities.agent_files", {"budget": "src/.hidden/x.py"}),
    ("capabilities.view", "ui/view.yaml"), ("capabilities.setup_steps", ["a", "a"]),
    ("limits.hook_timeout_s", 600), ("limits.memory_mb", True), ("limits.cpu", 2),
    ("surprise", {}),
]

# Manifests only the Python validator can refuse: the rules JSON Schema cannot say.
CROSS_FIELD_BAD = [
    ("capabilities.review.of", "not-gated.json"),
    ("capabilities.platform_pages", ["application.json"]),  # a gated name as a page
    ("capabilities.data_actions.call_for_proposals.outputs", ["application.md"]),
    ("kind.crews", ["grant-lead"]),  # the lead as a crew
    ("plugin.api", ">=2.0"),  # a range that excludes the installed API
]


def test_the_example_plugin_is_built_in_the_shape_the_spec_requires():
    """The reference plugin passes every requirement a checkout can decide and
    fails none. Its own attack test passes too."""
    r = report.check(EXAMPLE)
    assert r.failures() == []
    decided = {x.id: x.status for x in r.results}
    assert decided["MIN-01"] == decided["MIN-05"] == decided["MIN-06"] == "pass"
    assert r.status == "pending"  # never "ready" from a checkout alone
    assert decided["EXT-CYC-1"] == "n/a"  # it declares no mode that runs on cycles
    sys.path.insert(0, str(EXAMPLE / "tests"))
    try:
        report.import_plugin(EXAMPLE, "grant_factory:plugin")
        from test_application_gate import test_a_budget_line_with_no_rule_is_refused
        test_a_budget_line_with_no_rule_is_refused()
    finally:
        sys.path.remove(str(EXAMPLE / "tests"))


@pytest.mark.parametrize(("path", "value"), SCHEMA_VISIBLE_BAD,
                         ids=[p for p, _ in SCHEMA_VISIBLE_BAD])
def test_a_manifest_that_is_not_one_is_refused_by_both_the_validator_and_the_schema(path, value):
    raw = _mutate(path, value)
    assert M.validate(raw), f"the validator accepted {path}={value!r}"
    assert not _schema_ok(raw), f"the schema accepted {path}={value!r}"


@pytest.mark.parametrize(("path", "value"), CROSS_FIELD_BAD, ids=[p for p, _ in CROSS_FIELD_BAD])
def test_a_rule_the_schema_cannot_say_is_still_refused(path, value):
    raw = _mutate(path, value)
    assert M.validate(raw), f"the validator accepted {path}={value!r}"
    with pytest.raises(M.ManifestProblem):
        M.load(_toml(raw))


def test_the_schema_and_the_validator_accept_the_same_good_manifests():
    good = [_example_raw(),
            _mutate("capabilities", {}),
            _mutate("capabilities.receipts", {"sources": ["grants-db"]}),
            _mutate("kind.modes", [{"key": "draft", "shape": "ships_once", "open": True},
                                   {"key": "keep", "shape": "runs_on_cycles"}]),
            _mutate("limits", {"hook_timeout_s": 30, "memory_mb": 64})]
    for raw in good:
        assert M.validate(raw) == []
        assert _schema_ok(raw)


def test_the_schema_lists_every_cross_field_rule_the_validator_holds():
    listed = set(MANIFEST_SCHEMA["x-cross-field-rules"])
    assert set(M.CROSS_FIELD_RULES) <= listed


def test_a_manifest_that_is_not_toml_or_utf8_is_refused_with_its_reason():
    for text in (b"\xff\xfe", "[plugin\nid=", ""):
        with pytest.raises(M.ManifestProblem) as exc:
            M.load(text)
        assert exc.value.problems


def _toml(raw: dict) -> str:
    """Just enough TOML for the cases above: tables of scalars, lists and inline tables."""
    def val(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return repr(v)
        if isinstance(v, str):
            return json.dumps(v)
        if isinstance(v, list):
            return "[" + ", ".join(val(x) for x in v) + "]"
        return "{" + ", ".join(f"{json.dumps(k)} = {val(x)}" for k, x in v.items()) + "}"
    lines = []
    for table, body in raw.items():
        lines.append(f"[{table}]")
        for k, v in body.items():
            lines.append(f"{json.dumps(k)} = {val(v)}")
    return "\n".join(lines)


# ── code against its manifest (MIN-05) ──────────────────────────────────────────


def _plugin_from_example(**overrides):
    """The example plugin's class, with methods replaced or removed (None)."""
    from grant_factory import GrantFactory

    ns = {k: v for k, v in vars(GrantFactory).items() if not k.startswith("__")}
    for name, fn in overrides.items():
        if fn is None:
            ns.pop(name)
        else:
            ns[name] = fn
    return type("Variant", (FactoryPlugin,), ns)()


@pytest.fixture(scope="module", autouse=True)
def _example_importable():
    report.import_plugin(EXAMPLE, "grant_factory:plugin")


def test_a_declared_capability_with_no_hook_is_named():
    problems = H.check_plugin(_plugin_from_example(completion=None), _example())
    assert len(problems) == 1 and "completion_gate = true" in problems[0]


def test_a_hook_the_manifest_did_not_declare_is_refused():
    @hook.classify_tools
    def sneaky(self, tools):
        return Coverage()
    problems = H.check_plugin(_plugin_from_example(sneaky=sneaky), _example())
    assert len(problems) == 1 and "not declared" in problems[0]


def test_a_hook_with_the_wrong_arity_is_named():
    @hook.check_artifact("application.json")
    def check_application(self, upload):
        return Accept()

    @hook.view
    def view(self, *args):
        return {}
    problems = H.check_plugin(
        _plugin_from_example(check_application=check_application, view=view), _example())
    assert len(problems) == 2 and all("takes" in p for p in problems)


def test_two_methods_marked_for_one_hook_is_an_error_not_a_race():
    @hook.brief
    def brief_again(self, ctx):
        return Brief("again")
    problems = H.check_plugin(_plugin_from_example(brief_again=brief_again), _example())
    assert problems and "two methods" in problems[0]


def test_an_object_that_is_not_a_factory_plugin_is_refused():
    assert "not a FactoryPlugin" in H.check_plugin(object(), _example())[0]


def test_an_unknown_hook_name_fails_at_import_not_at_install():
    with pytest.raises(AttributeError, match="there is no hook"):
        hook.on_everything  # noqa: B018


# ── what a hook may return (MIN-08 / MIN-17 / EXT-*) ────────────────────────────


K = H.HookKey
UNUSABLE = [
    (K("check_artifact", "a"), Refuse([])),
    (K("check_artifact", "a"), Refuse(["   "])),
    (K("check_artifact", "a"), Refuse(["x" * 501])),
    (K("check_artifact", "a"), Refuse(['Traceback (most recent call last):\n  File "x"'])),
    (K("check_artifact", "a"), Accept(data="not bytes")),
    (K("check_artifact", "a"), Accept(state_patch={"when": date(2027, 1, 1)})),
    (K("check_artifact", "a"), Accept(load_bearing={"c1": float("nan")})),
    (K("check_artifact", "a"), True),
    (K("check_review", "r"), Accept(data=b"rewritten review")),
    (K("plan_gate"), [Owed("a", "one"), Owed("a", "two")]),
    (K("plan_gate"), [Owed("", "keyless")]),
    (K("completion_gate"), "nothing owed"),
    (K("brief"), Brief("x" * (H.BRIEF_MAX_CHARS + 1))),
    (K("brief"), Brief("Be thorough.\nREFUSED. You may not stop.")),
    (K("render"), [Page("application.md", "application/javascript", b"alert(1)")]),
    (K("render"), [Page("application.md", "image/svg+xml", b"<svg/>")]),
    (K("render"), [Page("not-declared.md", "text/markdown", b"x")]),
    (K("render"), [Page("application.md", "text/markdown", b"a"),
                   Page("application.md", "text/markdown", b"b")]),
    (K("fields_of_read"), ReadFields(())),
    (K("fields_of_read"), ["cost"]),
    (K("data_action", "call_for_proposals"), ActionResult({"ok": True}, {"elsewhere.json": b""})),
    (K("data_action", "call_for_proposals"), ActionResult({"big": "x" * 70_000})),
    (K("setup_step", "funder_source"), StepState(True, "")),
    (K("view"), {"state": object()}),
    (K("validate_create"), "a string, not a list"),
]


@pytest.mark.parametrize(("key", "value"), UNUSABLE, ids=[f"{k}-{i}" for i, (k, _) in
                                                          enumerate(UNUSABLE)])
def test_a_return_core_cannot_act_on_is_named(key, value):
    problems = H.check_return(key, value, _example())
    assert problems and all(p.startswith(f"@hook.{key}") for p in problems)


def test_usable_returns_pass_and_prose_that_mentions_a_refusal_is_not_a_heading():
    ok = [(K("check_artifact", "a"), Refuse(["budget needs at least one line"])),
          (K("check_artifact", "a"), Accept(data=b"{}", state_patch={"asked": 1.5})),
          (K("brief"), Brief("A claim the data cannot reach is refused.")),
          (K("fields_of_read"), ReadFields.unknown()),
          (K("fields_of_read"), ReadFields(("cost", "clicks"))),
          (K("render"), [Page("application.md", "text/markdown", b"# x")]),
          (K("data_action", "call_for_proposals"), ActionResult({"ok": True},
                                                                {"calls.json": b"{}"})),
          (K("plan_gate"), [Owed("data", "connect the source")])]
    for key, value in ok:
        assert H.check_return(key, value, _example()) == [], key


def test_the_example_plugins_hooks_return_usable_values_on_its_fixtures():
    """A first, in-process taste of MIN-08: the harness will do this sandboxed
    and on every fixture state."""
    from grant_factory import plugin
    from yaaf_plugin_api import Upload

    ctx = ProjectSnapshot("p1", "grant_application", "draft", "0" * 40, date(2027, 1, 1))
    found = plugin.hooks()
    case = EXAMPLE / "fixtures/artifacts/accept/application.json/minimal.json"
    verdict = found[K("check_artifact", "application.json")](
        ctx, Upload("application.json", case.read_bytes(), "s1", "r1"))
    assert isinstance(verdict, Accept)
    for key, value in ((K("check_artifact", "application.json"), verdict),
                       (K("brief"), found[K("brief")](ctx)),
                       (K("completion_gate"), found[K("completion_gate")](ctx)),
                       (K("view"), found[K("view")](ctx))):
        assert H.check_return(key, value, _example()) == []
    doc = json.loads((EXAMPLE / "ui/view.json").read_text())
    assert view.validate_data(doc, found[K("view")](ctx)) == []


# ── the view ────────────────────────────────────────────────────────────────────


BAD_VIEWS = [
    {"version": 2, "sections": [{"id": "a", "component": "status", "title": "A"}]},
    {"version": 1, "sections": []},
    {"version": 1, "sections": [{"id": "a", "component": "iframe", "title": "A"}]},
    {"version": 1, "sections": [{"id": "A!", "component": "status", "title": "A"}]},
    {"version": 1, "sections": [{"id": "a", "component": "status", "title": ""}]},
    {"version": 1, "sections": [{"id": "a", "component": "status", "title": "A",
                                 "script": "alert(1)"}]},
    {"version": 1, "sections": [{"id": "a", "component": "status", "title": "A"}], "css": ""},
]


@pytest.mark.parametrize("doc", BAD_VIEWS)
def test_a_view_the_dashboard_cannot_draw_is_refused_by_both_checks(doc):
    assert view.validate_doc(doc)
    assert not jsonschema.Draft202012Validator(VIEW_SCHEMA).is_valid(doc)


def test_view_data_that_does_not_fit_its_component_is_refused():
    doc = json.loads((EXAMPLE / "ui/view.json").read_text())
    for data in ({"state": {"state": "great", "text": "x"}},
                 {"state": {"state": "ok"}},
                 {"ask": {"label": "Asked", "value": True}},
                 {"undeclared": {"text": "x"}},
                 {"draft": {"name": "application.json", "version": 0}}):
        assert view.validate_data(doc, data), data
    links = {"version": 1, "sections": [{"id": "l", "component": "link", "title": "L"}]}
    assert view.validate_data(links, {"l": {"label": "x", "href": "javascript:alert(1)"}})
    assert view.validate_data(links, {"l": {"label": "x", "href": "https://a", "artifact": "b"}})
    assert view.validate_data(links, {"l": {"label": "x", "href": "https://example.com"}}) == []


# ── the repo around the manifest ────────────────────────────────────────────────


@pytest.fixture
def repo(tmp_path):
    dest = tmp_path / "plugin"
    shutil.copytree(EXAMPLE, dest, ignore=shutil.ignore_patterns("__pycache__"))
    return dest


def _failed(path) -> dict[str, list[str]]:
    return {r.id: r.why for r in report.check(path, load_module=False).results
            if r.status == "fail"}


def test_a_gate_with_no_refuse_fixture_fails_min_13(repo):
    shutil.rmtree(repo / "fixtures/artifacts/refuse/application.json")
    assert "refuses" in _failed(repo)["MIN-13"][0]


def test_a_refuse_fixture_that_does_not_say_what_it_expects_fails_min_13(repo):
    (repo / "fixtures/artifacts/refuse/application.json/no-budget.json.expect.json").unlink()
    assert "contains" in _failed(repo)["MIN-13"][0]


def test_a_symlink_in_the_repo_fails_min_01(repo):
    (repo / "skills/leak.md").symlink_to("/etc/hostname")
    assert any("symlink" in w for w in _failed(repo)["MIN-01"])


def test_a_lead_with_no_role_file_and_a_plugin_with_no_tests_fail(repo):
    (repo / "roles/grant-lead.json").unlink()
    shutil.rmtree(repo / "tests")
    failed = _failed(repo)
    assert "roles/grant-lead.json" in failed["MIN-01"][0]
    assert "MIN-16" in failed


def test_a_bad_view_file_fails_ext_view_1(repo):
    (repo / "ui/view.json").write_text('{"version": 1, "sections": [{"id": "x"}]}')
    assert _failed(repo)["EXT-VIEW-1"]


def test_a_manifest_problem_about_modes_is_filed_under_min_06(repo):
    text = (repo / M.FILENAME).read_text().replace('shape = "ships_once"', 'shape = "often"')
    (repo / M.FILENAME).write_text(text)
    failed = _failed(repo)
    assert "MIN-06" in failed and "MIN-01" not in failed


def test_no_manifest_claims_no_pass_and_the_cli_exits_1(tmp_path, capsys):
    r = report.check(tmp_path)
    assert r.status == "failed" and not any(x.status == "pass" for x in r.results)
    assert cli(["check", str(tmp_path)]) == 1
    assert cli(["check", str(EXAMPLE), "--json"]) == 0
    out = capsys.readouterr().out
    assert json.loads(out[out.index("{"):])["status"] == "pending"


# ── the table, the version, the package ─────────────────────────────────────────


def test_the_requirement_table_is_the_one_the_design_doc_states():
    """The install reports against `requirements.REQUIREMENTS`; the doc tells
    people what those are. Neither may grow an id the other lacks."""
    text = DOC.read_text()
    section = text[text.index("## 6. The conformance suite"):text.index("## 7.")]
    in_doc = set(re.findall(r"\b(MIN-\d{2}|EXT-[A-Z]+-\d)\b", section))
    in_code = {r.id for r in requirements.REQUIREMENTS}
    assert in_doc == in_code
    assert len(in_code) == len(requirements.REQUIREMENTS)


def test_extended_requirements_apply_only_to_what_a_plugin_declares():
    bare = M.load(_toml(_mutate("capabilities", {})))
    ids = {r.id for r in requirements.applicable(bare)}
    assert all(i.startswith("MIN-") for i in ids)
    assert {r.id for r in requirements.applicable(_example())} >= {"EXT-REV-2", "EXT-RCT-1"}


def test_api_ranges_mean_one_thing():
    assert version.compatible(">=1.0,<2", "1.0")
    assert version.compatible(">=1.0,<2", "1.9")
    assert not version.compatible(">=1.0,<2", "2.0")
    assert not version.compatible(">1.0", "1.0")
    assert version.compatible("==1", "1.0.0")
    for bad in ("", "1.0", "~=1.0", ">=1.0,", ">=1.x", ">=1.0.0.0"):
        with pytest.raises(ValueError):
            version.parse_range(bad)


def test_the_package_version_is_the_api_version():
    meta = tomllib.loads((PKG / "pyproject.toml").read_text())["project"]
    assert meta["version"].startswith(version.API_VERSION + ".")


def test_the_package_depends_on_nothing_but_the_standard_library():
    """It is imported into every plugin's sandbox before the plugin. A
    dependency here is code every plugin runs and no plugin chose."""
    meta = tomllib.loads((PKG / "pyproject.toml").read_text())["project"]
    assert meta["dependencies"] == []
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module] if isinstance(node, ast.ImportFrom) and node.level == 0 else []
            for name in names:
                top = name.split(".")[0]
                assert top in sys.stdlib_module_names or top == "yaaf_plugin_api" \
                    or top == "__future__", f"{path.name} imports {name}"


def test_every_hook_in_the_api_has_a_capability_that_obliges_it():
    """A hook no manifest could declare is a hook core would never call."""
    bare = M.load(_toml(_mutate("capabilities", {})))
    assert H.expected(bare) == set()
    everything = H.expected(_example()) | {K("plan_gate"), K("classify_tools"),
                                           K("migrate_state"), K("create_fields"),
                                           K("validate_create")}
    assert {k.kind for k in everything} == set(H.SPECS)


def test_the_manifest_the_design_doc_shows_is_one_the_spec_accepts():
    """Authors and AI factories copy the doc's example. It has to be valid."""
    block = re.search(r"### 2.1 The manifest\n\n```toml\n(.*?)```", DOC.read_text(), re.S)
    assert M.load(block.group(1)).kind == "plugin:amazon_ppc"
