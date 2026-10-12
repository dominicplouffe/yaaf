"""Invariant selftest: every harness renders the tools the model has always seen.

The platform tools are declared once, as data, and rendered per harness
(docs/RUNTIMES.md). The Agent SDK used to own both the declaration and the
conversion to the schema the model receives — through a helper the SDK keeps
private. Every harness after it renders from `wire_schema` instead, so that
function is held here to the record of what the SDK actually sent:
`tests/fixtures/tool_catalog.json` was captured through the SDK's own
conversion, before the tools stopped being SDK objects.

A diff here is a change in what agents see, which CONTRIBUTING house rule #3
says is regression-gated. If it is deliberate, re-record and say so in the PR:

    python tests/selftest_invariants/test_tool_catalog.py --record
"""

from __future__ import annotations

import importlib
import json
import pathlib
import sys

import pytest

pytest.importorskip("claude_agent_sdk")

FIXTURE = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "tool_catalog.json"
ENV = {"YAAF_API_BASE_URL": "http://api:8080", "YAAF_API_TOKEN": "test-token",
       "YAAF_RUN_ID": "run_test", "YAAF_TOOL_PERMISSIONS": "{}"}


@pytest.fixture()
def sandbox_entry(monkeypatch):
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    import yaaf.runner.sandbox_entry as mod

    return importlib.reload(mod)


def _catalog(mod) -> list:
    return sorted((v for v in vars(mod).values() if isinstance(v, mod.Tool)),
                  key=lambda t: t.name)


def _rendered(mod) -> list[dict]:
    return [{"name": t.name, "description": t.description,
             "input_schema": mod.wire_schema(t)} for t in _catalog(mod)]


def test_every_tool_reaches_the_model_exactly_as_recorded(sandbox_entry):
    recorded = {entry["name"]: entry for entry in json.loads(FIXTURE.read_text())}
    rendered = {entry["name"]: entry for entry in _rendered(sandbox_entry)}
    assert sorted(rendered) == sorted(recorded), "a tool was added or removed — re-record"
    for name, entry in rendered.items():
        assert entry == recorded[name], f"{name} no longer reaches the model as recorded"


def test_the_sdk_is_handed_the_declared_objects_untouched(sandbox_entry):
    """The SDK rendering is a copy, not a conversion: it receives the very
    schema objects and handlers it always received, so the SDK harness cannot
    have changed by passing through the catalog."""
    tools = _catalog(sandbox_entry)
    for declared, sdk_tool in zip(tools, sandbox_entry.render_sdk(tools), strict=True):
        assert isinstance(sdk_tool, sandbox_entry.SdkMcpTool)
        assert sdk_tool.name == declared.name
        assert sdk_tool.description == declared.description
        assert sdk_tool.input_schema is declared.input_schema
        assert sdk_tool.handler is declared.handler


def test_wire_schema_still_agrees_with_the_installed_sdk(sandbox_entry):
    """The early warning: if the SDK changes its own rule, the two harness
    families would start sending different schemas for one tool. Skipped, not
    failed, when the private helper moves — the fixture remains the record."""
    import claude_agent_sdk

    convert = getattr(claude_agent_sdk, "_build_input_schema", None)
    if convert is None:
        pytest.skip("the SDK no longer exposes its schema helper under this name")
    for declared, sdk_tool in zip(_catalog(sandbox_entry),
                                  sandbox_entry.render_sdk(_catalog(sandbox_entry)),
                                  strict=True):
        assert sandbox_entry.wire_schema(declared) == convert(sdk_tool), declared.name


if __name__ == "__main__" and "--record" in sys.argv:
    import os

    os.environ.update(ENV)
    import yaaf.runner.sandbox_entry as mod

    FIXTURE.write_text(json.dumps(_rendered(mod), indent=2, sort_keys=True) + "\n")
    print(f"recorded {len(_catalog(mod))} tools to {FIXTURE}")
