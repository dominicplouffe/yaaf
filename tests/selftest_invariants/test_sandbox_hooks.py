"""Invariant selftests: the platform's SDK hooks are record-only, and skills
are dark unless the chairman's role definition names them.

Both matter for the same reason every rule here matters: the sandbox is the
agent's house. A Stop hook that could block a run from ending would be a lever
an agent's output might learn to pull; a skills default of "whatever is on
disk" would let an agent author its own instructions into the next run via the
writable workspace. Neither lever exists.
"""

from __future__ import annotations

import asyncio
import importlib

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


def test_the_stop_hook_cannot_block_a_run_from_ending(sandbox_entry):
    """Record-only BY CONSTRUCTION: whatever the run was doing, the hook's
    answer carries no decision key — there is nothing for the SDK to obey."""
    for payload in ({}, {"reason": "anything"}, None):
        out = asyncio.run(sandbox_entry.hook_stop(payload, None, None))
        assert out == {}, f"a Stop hook that answers {out} is a lever, not a log"
    assert any(e["hook"] == "Stop" for e in sandbox_entry.HOOK_LOG)


def test_precompact_records_and_never_decides(sandbox_entry):
    out = asyncio.run(sandbox_entry.hook_precompact({"trigger": "auto"}, None, None))
    assert out == {}
    entry = next(e for e in sandbox_entry.HOOK_LOG if e["hook"] == "PreCompact")
    assert entry["trigger"] == "auto"


def test_skills_default_dark_and_only_granted_names_pass(sandbox_entry):
    """options.skills gets an explicit list, never None: None means "CLI
    defaults", and the workspace is agent-writable — a skill an agent wrote
    into .claude/skills must stay hidden unless the role names it."""
    assert sandbox_entry.skill_names() == []

    sandbox_entry.PERMS = {"skills": [
        {"name": "Write Better Emails", "body": "…"},
        {"name": "../../etc/evil", "body": "…"},
        {"name": "", "body": "…"},
    ]}
    assert sandbox_entry.skill_names() == ["write-better-emails", "etcevil"]


def test_hook_events_land_in_the_trace_shape(sandbox_entry):
    asyncio.run(sandbox_entry.hook_stop({}, None, None))
    entry = sandbox_entry.HOOK_LOG[-1]
    assert entry["type"] == "hook", "trace consumers key on type"
