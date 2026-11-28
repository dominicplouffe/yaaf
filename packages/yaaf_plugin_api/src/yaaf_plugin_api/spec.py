"""The plugin spec as one document: what an author, or an AI factory writing a
plugin, needs to build one in the shape yaaf requires.

Served by yaaf's MCP `plugin_spec` tool and printed by `python -m
yaaf_plugin_api spec`. It is built from the same objects the install checks
against (the hook table, the schemas, the requirement table), so it cannot
describe a plugin the install would refuse.
"""

from __future__ import annotations

import json
from importlib import resources

from yaaf_plugin_api import fixtures, hooks, manifest, requirements, view
from yaaf_plugin_api.version import API_VERSION


def _schema(name: str) -> dict:
    return json.loads(resources.files("yaaf_plugin_api").joinpath("schema", name)
                      .read_text(encoding="utf-8"))


def spec() -> dict:
    return {
        "api_version": API_VERSION,
        "repo_layout": {
            "yaaf-plugin.toml": "the manifest (manifest_schema)",
            "src/<package>/": "the plugin; `plugin.module` names its FactoryPlugin object",
            "requirements.lock": "every dependency pinned with == and --hash=sha256:...; "
                                 "omit when there are none",
            "roles/*.json, crews/*.json": "the kind's lead and crews, in yaaf's library shapes",
            "skills/*.md": "the playbook, given to every seat (front matter name/description)",
            "ui/view.json": "the project-home section (view_schema)",
            "fixtures/": "what the conformance suite asks with (fixtures_format)",
            "tests/test_*.py": "the plugin's own tests; each gate must be named by one",
        },
        "manifest_schema": _schema("manifest.schema.json"),
        "manifest_cross_field_rules": list(manifest.CROSS_FIELD_RULES),
        "view_schema": _schema("view.schema.json"),
        "view_components": {k: {"required": sorted(r), "optional": sorted(o)}
                            for k, (r, o) in view.COMPONENTS.items()},
        "hooks": [{"decorator": f"@hook.{s.kind}" + ('("<name>")' if s.keyed else ""),
                   "arguments": list(s.params), "returns": s.returns,
                   "declared_by": s.capability} for s in hooks.SPECS.values()],
        "limits": {"brief_max_chars": hooks.BRIEF_MAX_CHARS,
                   "refusal_max_chars": hooks.PROBLEM_MAX_CHARS,
                   "page_media_types": sorted(hooks.PAGE_MEDIA_TYPES),
                   "manifest_limit_ceilings": manifest.LIMIT_CEILINGS},
        "review_envelope": {"subject_sha256": "sha256 of the version reviewed",
                            "verdict": "pass | return",
                            "rederived": [{"claim": "<id>", "value": "<number>"},
                                          {"claim": "<id>", "matches": True}]},
        "fixtures_format": fixtures.__doc__,
        "requirements": [{"id": r.id, "level": r.level, "capability": r.capability,
                          "text": r.text, "checked_by": r.checked_by}
                         for r in requirements.REQUIREMENTS],
        "check": "python -m yaaf_plugin_api check <repo> --full",
        "install": "the instance admin: POST /admin/plugins {url, ref} with a public "
                   "https://github.com/<owner>/<repo> URL",
    }
