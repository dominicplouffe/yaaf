"""Invariant selftests: runs survive the network, and deliverables ship by path.

Two cost sinks share a root cause — the model streaming megabytes it never
needed to stream. ``artifact_put`` must accept a workspace path and move the
bytes itself, so a 5 MB report costs a filename of output tokens, not 6.7 MB
of base64. And a transient API drop mid-response must resume the same session
in place, not fail the run and hand the daemon a full re-bill of the turn.
"""

from __future__ import annotations

import asyncio
import base64
import importlib
import json

import pytest

pytest.importorskip("claude_agent_sdk")

from claude_agent_sdk import (  # noqa: E402
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
)


@pytest.fixture()
def sandbox_entry(monkeypatch):
    monkeypatch.setenv("YAAF_API_BASE_URL", "http://api:8080")
    monkeypatch.setenv("YAAF_API_TOKEN", "test-token")
    monkeypatch.setenv("YAAF_RUN_ID", "run_test")
    monkeypatch.setenv("YAAF_TOOL_PERMISSIONS", "{}")
    import yaaf.runner.sandbox_entry as mod

    return importlib.reload(mod)


def _put(sandbox_entry, args):
    reply = asyncio.run(sandbox_entry.t_artifact_put.handler(args))
    return json.loads(reply["content"][0]["text"])


# ── artifact_put ships bytes by path, never through model output ────────────────


def test_artifact_put_reads_a_workspace_path_itself(sandbox_entry, monkeypatch, tmp_path):
    (tmp_path / "reports").mkdir()
    payload = b"# Final report\n" * 100
    (tmp_path / "reports" / "q3.md").write_bytes(payload)
    monkeypatch.setattr(sandbox_entry, "WORKSPACE", tmp_path)

    calls = []

    def fake_call(method, path, body=None, params=None):
        calls.append((method, path, body))
        return {"status": 200, "body": {"uri": "file://x", "sha256": "d" * 64}}

    monkeypatch.setattr(sandbox_entry, "_call", fake_call)

    # Workspace-relative path; filename defaults to the file's own name.
    out = _put(sandbox_entry, {"path": "reports/q3.md"})
    assert out["status"] == 200
    method, path, body = calls[0]
    assert (method, path) == ("POST", "/agent/artifacts")
    assert body["filename"] == "q3.md"
    assert base64.b64decode(body["content_base64"]) == payload

    # Absolute path inside the workspace, with an explicit rename.
    out = _put(sandbox_entry, {"path": str(tmp_path / "reports" / "q3.md"),
                               "filename": "final.md"})
    assert out["status"] == 200
    assert calls[1][2]["filename"] == "final.md"


def test_artifact_put_refuses_escapes_missing_files_and_oversize(
    sandbox_entry, monkeypatch, tmp_path
):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (tmp_path / "outside.txt").write_text("not yours to ship")
    monkeypatch.setattr(sandbox_entry, "WORKSPACE", workspace)
    monkeypatch.setattr(
        sandbox_entry, "_call",
        lambda *a, **k: pytest.fail("a refused put must never reach the platform"),
    )

    # ".." resolving out of the workspace, and an absolute path elsewhere.
    for path in ("../outside.txt", str(tmp_path / "outside.txt")):
        out = _put(sandbox_entry, {"path": path})
        assert out["status"] == 422
        assert "workspace" in out["body"]["error"]

    absent = _put(sandbox_entry, {"path": "never-written.md"})
    assert absent["status"] == 404

    monkeypatch.setattr(sandbox_entry, "ARTIFACT_MAX_BYTES", 1024 * 1024)
    (workspace / "big.bin").write_bytes(b"x" * (1024 * 1024 + 1))
    too_big = _put(sandbox_entry, {"path": "big.bin"})
    assert too_big["status"] == 413
    assert "1 MB" in too_big["body"]["error"]


def test_artifact_put_inline_fallback_still_works_and_is_validated(
    sandbox_entry, monkeypatch
):
    sent = []
    monkeypatch.setattr(
        sandbox_entry, "_call",
        lambda method, path, body=None, params=None: sent.append(body)
        or {"status": 200, "body": {}},
    )

    ok = _put(sandbox_entry, {"filename": "tiny.txt",
                              "content_base64": base64.b64encode(b"hi").decode()})
    assert ok["status"] == 200 and sent[0]["filename"] == "tiny.txt"

    nameless = _put(sandbox_entry, {"content_base64": "aGk="})
    assert nameless["status"] == 422 and "filename" in nameless["body"]["error"]

    empty = _put(sandbox_entry, {})
    assert empty["status"] == 422 and "path" in empty["body"]["error"]


# ── transient API drops resume the session instead of failing the run ───────────


def test_transient_classifier_never_retries_credential_failures(sandbox_entry):
    transient = Exception(
        "Claude Code returned an error result: API Error: Connection lost "
        "mid-response. The response above may be incomplete. (exit code: 1)"
    )
    assert sandbox_entry._looks_transient(transient)
    assert sandbox_entry._looks_transient(Exception("upstream 529 overloaded_error"))
    # The daemon's credential auto-pause reads these off the summary; a retry
    # would only delay and dilute that signal.
    assert not sandbox_entry._looks_transient(
        Exception("connection error: authentication_error, invalid x-api-key")
    )
    assert not sandbox_entry._looks_transient(Exception("KeyError('board')"))


def _staged(monkeypatch, sandbox_entry, tmp_path):
    (tmp_path / "system_prompt-run_test.txt").write_text("You are the CEO.")
    (tmp_path / "context-run_test.json").write_text("{}")
    monkeypatch.setattr(sandbox_entry, "STAGING", tmp_path)
    monkeypatch.setattr(sandbox_entry, "RESUME_BACKOFF_S", (0.0, 0.0))


def _result_message(session_id, cost, tokens_out):
    return ResultMessage(
        subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
        num_turns=1, session_id=session_id, total_cost_usd=cost,
        usage={"input_tokens": 10, "output_tokens": tokens_out,
               "cache_creation_input_tokens": 3, "cache_read_input_tokens": 7},
    )


def test_a_mid_stream_drop_resumes_in_place_and_meters_every_attempt(
    sandbox_entry, monkeypatch, tmp_path
):
    _staged(monkeypatch, sandbox_entry, tmp_path)
    attempts = []

    async def fake_query(prompt, options):
        attempts.append((prompt, getattr(options, "resume", None)))
        if len(attempts) == 1:
            yield SystemMessage(subtype="init", data={"session_id": "sess_1"})
            yield AssistantMessage(
                content=[TextBlock(text="half the report")], model="m")
            yield _result_message("sess_1", 0.25, 100)
            raise RuntimeError("API Error: Connection lost mid-response.")
        yield AssistantMessage(content=[TextBlock(text="done")], model="m")
        yield _result_message("sess_1", 0.05, 20)

    monkeypatch.setattr(sandbox_entry, "query", fake_query)
    exit_code = asyncio.run(sandbox_entry.main())
    assert exit_code == 0

    # The retry resumed the SAME session with the resume prompt, not a redo.
    assert len(attempts) == 2
    assert attempts[0][1] is None
    assert attempts[1] == (sandbox_entry.RESUME_PROMPT, "sess_1")

    result = json.loads((tmp_path / "result-run_test.json").read_text())
    assert result["ok"] is True and result["partial"] is False
    # Both attempts were billed, so both are metered.
    assert result["tokens_out"] == 120
    assert result["cost_usd"] == pytest.approx(0.30)
    assert "resuming in place" in result["summary"]
    assert "done" in result["summary"]

    # Per-attempt figures stay auditable in the trace.
    lines = [json.loads(line)
             for line in (tmp_path / "trace-run_test.jsonl").read_text().splitlines()]
    assert [e["attempt"] for e in lines if e["type"] == "result"] == [1, 2]
    assert any(e["type"] == "transient_error" and e["resume"] == "sess_1"
               for e in lines)


def test_a_non_transient_error_still_fails_the_run_verbatim(
    sandbox_entry, monkeypatch, tmp_path
):
    _staged(monkeypatch, sandbox_entry, tmp_path)

    async def fake_query(prompt, options):
        yield SystemMessage(subtype="init", data={"session_id": "sess_1"})
        raise RuntimeError("Not logged in: please run /login")

    monkeypatch.setattr(sandbox_entry, "query", fake_query)
    assert asyncio.run(sandbox_entry.main()) == 1
    result = json.loads((tmp_path / "result-run_test.json").read_text())
    assert result["ok"] is False
    # Verbatim, because the daemon's credential auto-pause greps the summary.
    assert "Not logged in" in result["summary"]


def test_retries_are_bounded(sandbox_entry, monkeypatch, tmp_path):
    _staged(monkeypatch, sandbox_entry, tmp_path)
    attempts = []

    async def fake_query(prompt, options):
        attempts.append(prompt)
        if False:  # pragma: no cover — make this an async generator
            yield
        raise RuntimeError("connection reset by peer")

    monkeypatch.setattr(sandbox_entry, "query", fake_query)
    assert asyncio.run(sandbox_entry.main()) == 1
    assert len(attempts) == sandbox_entry.RESUME_ATTEMPTS
    result = json.loads((tmp_path / "result-run_test.json").read_text())
    assert result["ok"] is False
    assert "connection reset" in result["summary"]
