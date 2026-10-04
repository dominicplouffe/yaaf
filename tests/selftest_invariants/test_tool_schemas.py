"""Invariant selftests: a field a tool tells the model to leave out can be left out.

The SDK turns a bare ``{name: type}`` schema into one that requires every
field. task_create declared ten fields that way, and a top-level team task
must omit three of them — so the model either left them out and was refused
by the SDK, or filled them with "null" / a literal ``""`` and was refused by
the API, which looked them up as ids. A live CEO burned eight refusals over
two runs and then ran the desk off the leadership channel because no form of
the call worked. These tests read the schema as the model receives it.
"""

from __future__ import annotations

import asyncio
import importlib
import json

import pytest

pytest.importorskip("claude_agent_sdk")


@pytest.fixture()
def sandbox_entry(monkeypatch):
    monkeypatch.setenv("YAAF_API_BASE_URL", "http://api:8080")
    monkeypatch.setenv("YAAF_API_TOKEN", "test-token")
    monkeypatch.setenv("YAAF_RUN_ID", "run_test")
    monkeypatch.setenv("YAAF_TOOL_PERMISSIONS", "{}")
    import yaaf.runner.sandbox_entry as mod

    return importlib.reload(mod)


def _build_input_schema(tool) -> dict:
    """The schema the model sees, by the SDK's rule: a dict that is already a
    JSON schema passes through; a bare ``{name: type}`` dict requires every
    name. Mirrored here because the SDK's own helper is private and moves."""
    schema = tool.input_schema
    if isinstance(schema.get("type"), str) and "properties" in schema:
        return schema
    return {"type": "object", "properties": {name: {} for name in schema},
            "required": list(schema)}


def _required(tool) -> set[str]:
    return set(_build_input_schema(tool).get("required", []))


def test_a_team_task_needs_no_subtask_fields(sandbox_entry):
    assert _required(sandbox_entry.t_task_create) == {
        "team_id", "title", "goal", "success_test"}


def test_a_team_task_reaches_the_api_without_placeholder_ids(sandbox_entry, monkeypatch):
    sent = []
    monkeypatch.setattr(sandbox_entry, "_call",
                        lambda method, path, body=None, params=None:
                        sent.append(body) or {"status": 200, "body": {}})
    asyncio.run(sandbox_entry.t_task_create.handler({
        "team_id": "team_x", "title": "t", "goal": "g",
        "success_test": {"type": "observable", "target": "x"},
        "requirement_id": "req_x"}))
    assert set(sent[0]) == {"team_id", "title", "goal", "success_test", "requirement_id"}


@pytest.mark.parametrize("tool, field", [
    # Each of these descriptions tells the model to omit the field or pass null.
    ("t_task_create", "parent_task_id"),
    ("t_task_create", "assignee_agent_id"),
    ("t_task_create", "hypothesis_id"),
    ("t_task_create", "not_before"),
    ("t_data_fetch", "tool"),
    ("t_data_fetch", "path"),
    ("t_stand_down_when", "when"),
    ("t_hire", "stand_down_when"),
    ("t_message_post", "thread_id"),
    ("t_message_post", "override"),
    ("t_artifact_put", "path"),
    ("t_artifact_put", "content_base64"),
    ("t_commitment_propose", "note"),
])
def test_a_field_the_description_says_to_omit_is_not_required(sandbox_entry, tool, field):
    assert field not in _required(getattr(sandbox_entry, tool))


def test_every_tool_schema_is_valid_json_schema(sandbox_entry):
    for tool in sandbox_entry.ALL_TOOLS:
        schema = _build_input_schema(tool)
        assert schema["type"] == "object", tool.name
        assert set(schema.get("required", [])) <= set(schema["properties"]), tool.name
        json.dumps(schema)
