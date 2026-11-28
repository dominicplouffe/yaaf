"""`yaaf plugin new`: a new plugin repo that already passes the spec.

It writes the manifest, a package with one stub hook per capability asked for,
fixtures for every gate, an attack test per gate, and only the optional files
the flags ask for (a lead role, a view, a data source recording). Every stub
answers the way the conformance suite requires, not merely in the right
shape. A gate accepts its accept fixture and refuses the fuzz corpus. A data
action catches the source's failure. A view fills its view. So a fresh
scaffold passes `check --full`, with only what an install decides left
pending, and an author starts from green and keeps it green.

The stubs are deliberately small and say what to replace. The placeholder gate
accepts a JSON object with a non-empty `title` and `summary`, because a gate
that accepted any object with a title would accept the fuzz corpus's unicode
case and fail EXT-ART-1.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from yaaf_plugin_api.manifest import CORE_KINDS, KIND_KEY, PLUGIN_ID, SLUG, plain_name


class ScaffoldProblem(ValueError):
    pass


@dataclass
class Options:
    plugin_id: str
    name: str = ""
    kind_key: str = ""
    artifacts: list[str] = field(default_factory=list)
    review: bool = False
    pages: bool = False
    brief: bool = False
    plan_gate: bool = False
    completion_gate: bool = False
    view: bool = False
    lead: bool = False
    create_fields: bool = False
    receipts: bool = False
    action: str | None = None
    action_tool: str | None = None
    setup_step: str | None = None

    def resolve(self) -> Options:
        if not PLUGIN_ID.match(self.plugin_id):
            raise ScaffoldProblem(f"the plugin id must match {PLUGIN_ID.pattern}")
        self.name = self.name or self.plugin_id.replace("-", " ").title()
        self.kind_key = self.kind_key or self.plugin_id.replace("-", "_")
        if not KIND_KEY.match(self.kind_key) or self.kind_key in CORE_KINDS:
            raise ScaffoldProblem(f"the kind key {self.kind_key!r} must match {KIND_KEY.pattern} "
                                  "and not be a built-in mode")
        for a in self.artifacts:
            if not plain_name(a) or not a.endswith(".json"):
                raise ScaffoldProblem(f"{a!r}: a gated artifact here is a plain .json file name")
        if len(set(self.artifacts)) != len(self.artifacts):
            raise ScaffoldProblem("an artifact is named twice")
        if (self.review or self.pages) and not self.artifacts:
            raise ScaffoldProblem("--review and --pages need an --artifact to review and render")
        if self.action and not self.action_tool:
            raise ScaffoldProblem("--action needs --action-tool: the one data-source tool it calls")
        for key, value in (("--action", self.action), ("--setup-step", self.setup_step)):
            if value is not None and not SLUG.match(value):
                raise ScaffoldProblem(f"{key} must match {SLUG.pattern}")
        return self

    @property
    def package(self) -> str:
        return re.sub(r"[^a-z0-9_]", "_", self.plugin_id.replace("-", "_"))

    @property
    def subject(self) -> str | None:
        return self.artifacts[0] if self.artifacts else None

    @property
    def review_name(self) -> str:
        return f"{Path(self.subject).stem}-review.json"

    @property
    def page_name(self) -> str:
        return f"{Path(self.subject).stem}.md"

    @property
    def output_name(self) -> str:
        return f"{self.action}.json"

    @property
    def lead_slug(self) -> str:
        return f"{self.plugin_id}-lead"[:64]


def _toml_list(items: list[str]) -> str:
    return "[" + ", ".join(json.dumps(i) for i in items) + "]"


def manifest(o: Options) -> str:
    caps = []
    for flag in ("brief", "plan_gate", "completion_gate", "create_fields"):
        if getattr(o, flag):
            caps.append(f"{flag:<15} = true")
    if o.artifacts:
        caps.append(f"artifacts       = {_toml_list(o.artifacts)}")
    if o.pages:
        caps.append(f"platform_pages  = {_toml_list([o.page_name])}")
    if o.review:
        caps.append(f'review          = {{ of = "{o.subject}", artifact = "{o.review_name}", '
                    "rederive_tolerance = 0.05 }")
    if o.receipts:
        caps.append("receipts        = true")
    if o.setup_step:
        caps.append(f"setup_steps     = {_toml_list([o.setup_step])}")
    if o.view:
        caps.append('view            = "ui/view.json"')
    action = ""
    if o.action:
        action = (f"\n  [capabilities.data_actions.{o.action}]\n"
                  f'  description = "Replace: what this action pulls and computes"\n'
                  f"  tools       = {_toml_list([o.action_tool])}\n"
                  f"  outputs     = {_toml_list([o.output_name])}\n")
    lead = f'lead  = "{o.lead_slug}"\n' if o.lead else ""
    return f'''# A yaaf factory plugin. Every capability below has its hook in
# src/{o.package}/__init__.py. Check it before you push:
#   python -m yaaf_plugin_api check . --full
[plugin]
id          = "{o.plugin_id}"
name        = "{o.name}"
version     = "0.1.0"
api         = ">=1.0,<2"
module      = "{o.package}:plugin"
description = "Replace: what a project of this kind delivers, in one sentence."

[kind]
key   = "{o.kind_key}"
{lead}
  [[kind.modes]]
  key   = "deliver"
  shape = "ships_once"
  open  = true

[capabilities]
{chr(10).join(caps)}
{action}'''


def plugin_module(o: Options) -> str:
    names = {"FactoryPlugin", "hook"}
    body: list[str] = []
    if o.brief:
        names.add("Brief")
        body.append('''    @hook.brief
    def brief(self, ctx):
        # Replace: what every seat of a project of this kind should know. It is
        # placed after the platform's own rules, under the platform's heading.
        return Brief("Replace with this kind's guidance for its seats.")
''')
    for gate in ("plan_gate", "completion_gate"):
        if getattr(o, gate) and o.subject:
            names.add("Owed")
            body.append(f'''    @hook.{gate}
    def {gate}(self, ctx):
        # Replace: what this kind owes before {"a plan holds" if gate == "plan_gate"
                                              else "the project completes"}.
        if ctx.latest({json.dumps(o.subject)}) is None:
            return [Owed("{Path(o.subject).stem}", "no {o.subject} has been accepted yet")]
        return []
''')
        elif getattr(o, gate):
            body.append(f'''    @hook.{gate}
    def {gate}(self, ctx):
        # Replace: return Owed(key, text) items for what is still owed.
        return []
''')
    for i, a in enumerate(o.artifacts):
        names |= {"Accept", "Refuse"}
        body.append(f'''    @hook.check_artifact({json.dumps(a)})
    def check_{i}(self, ctx, upload):
        # Replace: this kind's rules for {a}. A refusal names every reason, in
        # words an agent can act on; an accept may normalise the bytes.
        return _check_document({json.dumps(a)}, upload.data)
''')
    if o.review:
        names |= {"Accept", "Refuse"}
        body.append(f'''    @hook.check_review({json.dumps(o.review_name)})
    def check_review(self, ctx, review, subject):
        # The platform has already checked that this review names the latest
        # {o.subject}, comes from a seat that did not publish it, and re-derives
        # its load-bearing numbers. Replace: this kind's review rubric.
        try:
            doc = json.loads(review.data)
        except (ValueError, UnicodeDecodeError, RecursionError):
            return Refuse(["{o.review_name} is not valid JSON"])
        if not isinstance(doc, dict) or doc.get("verdict") not in ("pass", "return"):
            return Refuse(['verdict is "pass" or "return"'])
        if not isinstance(doc.get("summary"), str) or not doc["summary"].strip():
            return Refuse(["summary says what was checked"])
        return Accept()
''')
    if o.pages:
        names.add("Page")
        body.append(f'''    @hook.render
    def render(self, ctx, accepted, reviewed):
        # Replace: the pages the platform stores from the accepted {o.subject}.
        doc = json.loads(accepted.data)
        mark = "" if reviewed else "> Draft, not reviewed yet\\n\\n"
        text = f"{{mark}}# {{doc['title']}}\\n\\n{{doc['summary']}}\\n"
        return [Page({json.dumps(o.page_name)}, "text/markdown", text.encode())]
''')
    if o.receipts:
        names.add("ReadFields")
        body.append('''    @hook.fields_of_read
    def fields_of_read(self, tool, result):
        # The fields a data-source read returned: what a claim may cite. Say
        # unknown when you cannot tell; that read then backs no field.
        content = result.get("structuredContent") if isinstance(result, dict) else None
        if isinstance(content, dict) and content:
            return ReadFields(tuple(str(k) for k in content))
        return ReadFields.unknown()
''')
    if o.action:
        names |= {"ActionResult", "ToolError"}
        body.append(f'''    @hook.data_action({json.dumps(o.action)})
    def action_{o.action.replace("-", "_")}(self, ctx, gateway):
        # Replace: pull through the gateway (only the tools the manifest names,
        # only those the chairman allows) and compute. A source that fails
        # raises ToolError: answer, never raise.
        try:
            result = gateway.call({json.dumps(o.action_tool)}, {{}})
        except ToolError as exc:
            return ActionResult({{"read": False, "why": exc.message}})
        content = result.get("structuredContent") if isinstance(result, dict) else None
        body = json.dumps(content or {{}}, sort_keys=True).encode()
        return ActionResult({{"read": True, "bytes": len(body)}},
                            {{{json.dumps(o.output_name)}: body}})
''')
    if o.setup_step:
        names.add("StepState")
        body.append(f'''    @hook.setup_step({json.dumps(o.setup_step)})
    def setup_{o.setup_step.replace("-", "_")}(self, ctx):
        # Replace: whether this project has what the step asks for.
        if ctx.coverage.capabilities:
            return StepState(True, "A data source is connected.")
        return StepState(False, "Replace: what the chairman connects or sets up.")
''')
    if o.view:
        body.append('''    @hook.view
    def view(self, ctx):
        # Section id → data, in the shape ui/view.json's component takes.
        return {"state": {"state": "info", "text": "Replace: this project's state."}}
''')
    if o.create_fields:
        names.add("FieldSpec")
        body.append('''    @hook.create_fields
    def create_fields(self):
        # Replace: what the New Project form asks for a project of this kind.
        return [FieldSpec("audience", "string", "Who it is for", "Replace: a field's help.")]

    @hook.validate_create
    def validate_create(self, spec):
        # Replace: refusals for kind fields that cannot work. Empty accepts.
        audience = spec.fields.get("audience")
        if audience is not None and not isinstance(audience, str):
            return ["audience is text"]
        return []
''')
    helper = ""
    if o.artifacts:
        helper = '''

def _check_document(name, data):
    """The placeholder rule: a JSON object with a non-empty title and summary."""
    try:
        doc = json.loads(data)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return Refuse([f"{name} is not valid JSON"])
    if not isinstance(doc, dict):
        return Refuse([f"{name} must be one JSON object"])
    problems = [f"{key} is required" for key in ("title", "summary")
                if not isinstance(doc.get(key), str) or not doc[key].strip()]
    return Refuse(problems) if problems else Accept()
'''
    imports = ",\n    ".join(sorted(names, key=lambda n: (n[0].islower(), n)))
    return f'''"""{o.name}: a yaaf factory plugin. Replace this docstring with what it is."""

from __future__ import annotations

import json

from yaaf_plugin_api import (
    {imports},
)


class Plugin(FactoryPlugin):
{chr(10).join(body) if body else "    pass" + chr(10)}{helper}

plugin = Plugin()
'''


def tests_module(o: Options) -> str:
    cases = []
    for i, a in enumerate(o.artifacts):
        cases.append(f'''

def test_{i}_{Path(a).stem.replace("-", "_")}_accepts_a_good_one_and_refuses_one_without_a_title():
    """{a}: the attack every gate ships with (house rule 2)."""
    ok = json.dumps({{"title": "Example", "summary": "One line."}}).encode()
    assert isinstance(plugin.check_{i}(CTX, Upload("{a}", ok, "s1", "r1")), Accept)
    verdict = plugin.check_{i}(CTX, Upload("{a}", b'{{"summary": "x"}}', "s1", "r1"))
    assert isinstance(verdict, Refuse) and "title is required" in verdict.problems''')
    if o.review:
        cases.append(f'''

def test_a_review_with_no_verdict_is_refused():
    """{o.review_name}: the rubric left to this plugin still refuses."""
    subject = ArtifactBody(ArtifactMeta("{o.subject}", 1, "a" * 64, 2, "s1", "r1"), b"{{}}")
    review = ReviewUpload("{o.review_name}", b'{{"summary": "fine"}}', "s2", "r2",
                          subject_sha256="a" * 64)
    assert isinstance(plugin.check_review(CTX, review, subject), Refuse)''')
    if not cases:
        cases.append('''

def test_the_plugin_loads():
    assert plugin.hooks() is not None''')
    imports = ["ProjectSnapshot"]
    if o.artifacts:
        imports += ["Accept", "Refuse", "Upload"]
    if o.review:
        imports += ["ArtifactBody", "ArtifactMeta", "ReviewUpload"]
    return f'''"""This plugin's own attacks: one per gate, at least. yaaf runs them at install."""

import json
from datetime import date

from {o.package} import plugin
from yaaf_plugin_api import {", ".join(sorted(set(imports)))}

CTX = ProjectSnapshot("p1", "{o.kind_key}", "deliver", "0" * 40, date(2027, 1, 1))
{"".join(cases)}
'''


def _fixtures(o: Options) -> dict[str, str]:
    out: dict[str, str] = {}
    good = json.dumps({"title": "Example", "summary": "One line."}, indent=1)
    for a in o.artifacts:
        out[f"fixtures/artifacts/accept/{a}/good.json"] = good
        out[f"fixtures/artifacts/refuse/{a}/no-title.json"] = json.dumps({"summary": "x"})
        out[f"fixtures/artifacts/refuse/{a}/no-title.json.expect.json"] = json.dumps(
            {"contains": ["title is required"]})
    if o.review:
        r = o.review_name
        out[f"fixtures/artifacts/accept/{r}/pass.json"] = json.dumps(
            {"subject_sha256": "$SUBJECT_SHA256", "verdict": "pass", "rederived": [],
             "summary": "Replace: what the reviewer checked."}, indent=1)
        out[f"fixtures/artifacts/refuse/{r}/no-verdict.json"] = json.dumps({"summary": "x"})
        out[f"fixtures/artifacts/refuse/{r}/no-verdict.json.expect.json"] = json.dumps(
            {"contains": ["verdict"]})
    if o.action or o.receipts:
        tool = o.action_tool or "get_example"
        out["fixtures/sources/example/tools.json"] = json.dumps(
            {"tools": [{"name": tool, "description": "Replace: the tool's description"}]},
            indent=1)
        out[f"fixtures/sources/example/calls/{tool}.json"] = json.dumps(
            {"arguments": {}, "result": {"structuredContent": {"example": 1}}}, indent=1)
    return out


def _lead_role(o: Options) -> str:
    return json.dumps({
        "slug": o.lead_slug, "name": f"{o.name} Lead", "is_ceo_role": True,
        "category": "leadership", "domains": ["general"],
        "description": f"Leads one {o.name} project. Replace with what this lead does.",
        "system_prompt": f"You lead one {o.name} project. Replace with this lead's brief.",
        "tool_permissions": {"files": True}}, indent=2)


def files(o: Options) -> dict[str, str]:
    o.resolve()
    out = {
        "yaaf-plugin.toml": manifest(o),
        f"src/{o.package}/__init__.py": plugin_module(o),
        "tests/test_gates.py": tests_module(o),
        "tests/conftest.py": ('import sys\nfrom pathlib import Path\n\n'
                              'sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '
                              '"src"))\n'),
        "skills/runbook.md": (f"---\nname: {o.plugin_id}-runbook\ndescription: How a "
                              f"{o.name} project is worked. Load it first.\n---\n\n"
                              "# Runbook\n\nReplace: how a seat of this kind works.\n"),
        ".gitignore": "__pycache__/\n*.pyc\n.site/\n.pytest_cache/\n",
        "README.md": (f"# {o.name}\n\nA [yaaf](https://github.com/dominicplouffe/yaaf) "
                      "factory plugin.\n\n```bash\npython -m pytest tests -q\n"
                      "python -m yaaf_plugin_api check . --full\n```\n"),
        **_fixtures(o),
    }
    if o.view:
        out["ui/view.json"] = json.dumps({"version": 1, "sections": [
            {"id": "state", "component": "status", "title": "State"}]}, indent=1)
    if o.lead:
        out[f"roles/{o.lead_slug}.json"] = _lead_role(o)
    return out


def write(target: str | Path, o: Options) -> list[Path]:
    """Write the scaffold into `target`, which must not exist or be empty."""
    target = Path(target)
    if target.exists() and any(target.iterdir()):
        raise ScaffoldProblem(f"{target} is not empty: a scaffold writes into a new directory")
    written = []
    for rel, text in files(o).items():
        path = target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written
