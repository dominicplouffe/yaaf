"""Invariant selftest: an agent is sent the tools it can use, and refused the
rest by the server rather than by the list.

Every agent used to receive all 56 tool schemas on every API call. For a plain
employee that was ~7,700 tokens, of which ~3,700 were verbs the server answers
with a 403 — on a cached prefix whose entire context pack was 4,600. The tool
block was the largest single thing sent to an agent, bigger than everything the
platform had actually worked out and wanted it to know.

Two properties are enforced here, and the second is the one that matters:

1. `CEO_ONLY_TOOLS` says exactly what the server refuses. The list is derived
   here by CALLING every endpoint as an employee over HTTP, so it cannot rot
   the way a hand-kept list rots — a verb that becomes CEO-only, or stops being
   one, fails this test rather than quietly costing money or quietly vanishing
   from an agent that needed it.
2. **Withholding a schema is not enforcement.** An agent that was never offered
   `hire` and forges the call anyway is refused exactly as before. If this test
   ever passes because the tool is missing rather than because the wall is
   there, the wall is gone and nobody would know.
"""

from __future__ import annotations

import ast
import importlib
import pathlib
import re
from datetime import datetime

import pytest

from tests.conftest import agent_by_name, bearer, live_token, make_project
from yaaf.core.constitution import AUTONOMY_PRESETS

pytest.importorskip("claude_agent_sdk")


@pytest.fixture()
def sandbox_entry(monkeypatch):
    """The entry module reads its run's identity at import, so it is loaded
    with an environment rather than imported at module scope."""
    monkeypatch.setenv("YAAF_API_BASE_URL", "http://api:8080")
    monkeypatch.setenv("YAAF_API_TOKEN", "test-token")
    monkeypatch.setenv("YAAF_RUN_ID", "run_test")
    monkeypatch.setenv("YAAF_TOOL_PERMISSIONS", "{}")
    monkeypatch.delenv("YAAF_AGENT_KIND", raising=False)
    import yaaf.runner.sandbox_entry as mod

    return importlib.reload(mod)

def _first_legal(pattern: str) -> str:
    """The first literal an `^(a|b|c)$` pattern accepts."""
    match = re.fullmatch(r"\^\(([^)]+)\)\$", pattern)
    if match:
        return match.group(1).split("|")[0]
    # Not an alternation — a slug shape like ^[a-z0-9-]{2,32}$. Something
    # lowercase and ordinary satisfies every one of those on this surface.
    return "aaa"


def _minimal_body(model) -> dict:
    """The smallest body that gets past request validation, built from the
    model's OWN rules.

    Hand-written fixtures were the first attempt and they rotted within the
    hour: FastAPI rejects a bad body with 422 before the handler runs, so every
    tightened `min_length` anywhere in the agent API would silently turn a
    CEO-only route into one this test believed was open. Reading the
    constraints off the model means a route can change its shape freely and
    this still reaches the authority check behind it.
    """
    body: dict = {}
    for name, field in model.model_fields.items():
        if not field.is_required():
            continue
        annotation = field.annotation
        longest = max((getattr(m, "min_length", 0) or 0 for m in field.metadata),
                      default=0)
        pattern = next((getattr(m, "pattern", None) for m in field.metadata
                        if getattr(m, "pattern", None)), None)
        if annotation is str:
            # A patterned field cannot be satisfied by padding. Every one of
            # them on this surface is an alternation of literals, so the first
            # alternative is read out of the pattern rather than guessed —
            # a guess that stops matching is a 422 and a test that can no
            # longer see the authority check behind it.
            body[name] = _first_legal(pattern) if pattern else "x" * max(longest, 1)
        elif annotation is bool:
            body[name] = True
        elif annotation is int:
            body[name] = 1
        elif annotation is float:
            body[name] = 1.0
        elif annotation is datetime:
            body[name] = "2099-01-01T00:00:00+00:00"
        elif annotation is dict or str(annotation).startswith("dict"):
            body[name] = {}
        elif str(annotation).startswith("list"):
            body[name] = ["x"] * max(longest, 1)
        else:
            body[name] = "x"
    return body


def _bodies() -> tuple[dict[str, dict], dict[str, dict]]:
    """({path: body}, {path: query}) for every agent route, from its own model.

    A GET with a required query parameter is stopped by validation exactly as a
    POST with a missing field is, and would leave this test just as blind, so
    both halves are synthesised.
    """
    from pydantic import BaseModel

    from yaaf.server.api.agent import router

    bodies: dict[str, dict] = {}
    queries: dict[str, dict] = {}
    for route in router.routes:
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            continue
        # "/teams/{team_id}/recall" -> "/agent/teams/*/recall", the same
        # spelling _endpoints produces for the f-string that calls it.
        path = f"/agent{re.sub(r'\{[^}]+\}', '*', route.path)}"
        params = dependant.body_params
        if params:
            model = params[0].field_info.annotation
            if isinstance(model, type) and issubclass(model, BaseModel):
                bodies[path] = _minimal_body(model)
        required_query = {
            p.name: ("x" if p.field_info.annotation is str else 1)
            for p in dependant.query_params if p.field_info.is_required()
        }
        if required_query:
            queries[path] = required_query
    return bodies, queries


def _endpoints(sandbox_entry) -> dict[str, tuple[str, str, str]]:
    """{tool name: (method, path to call, key to look its body up by)}, read
    out of the tool bodies themselves.

    Parsed rather than hand-listed for the same reason the set below is
    derived rather than hand-listed: a tool repointed at a different endpoint
    must not silently keep an old classification.
    """
    tree = ast.parse(pathlib.Path(sandbox_entry.__file__).read_text())
    found: dict[str, tuple[str, str, str]] = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.AsyncFunctionDef):
            continue
        name = next(
            (d.args[0].value for d in fn.decorator_list
             if isinstance(d, ast.Call) and isinstance(d.func, ast.Name)
             and d.func.id == "tool" and d.args
             and isinstance(d.args[0], ast.Constant)),
            None)
        if name is None:
            continue
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "_call" and len(node.args) >= 2):
                method = node.args[0].value
                raw = node.args[1]
                if isinstance(raw, ast.Constant):
                    path = key = raw.value
                else:
                    # f"/agent/teams/{args['team_id']}/recall". The id does not
                    # matter — an unknown one is a 404 raised inside the
                    # handler, which is past the authority check either way —
                    # but the path still has to line up with the ROUTE, whose
                    # own spelling is "/agent/teams/{team_id}/recall". So the
                    # call gets a literal and the lookup gets a wildcard.
                    path = "".join(
                        part.value if isinstance(part, ast.Constant) else "x"
                        for part in raw.values)
                    key = "".join(
                        part.value if isinstance(part, ast.Constant) else "*"
                        for part in raw.values)
                found.setdefault(name, (method, path, key))
                break
    return found


def _probe(client, project: dict, agent_id: str, method: str, path: str,
           key: str, bodies: dict, queries: dict) -> int:
    """Call one endpoint as one agent, and give back the status.

    One probe, one run, because a run has a refusal budget. Walking fifty
    endpoints on a single token terminated the run a third of the way through,
    after which every remaining probe came back as something other than 403 and
    the derivation quietly concluded the server allowed eighteen CEO-only
    verbs. A fresh token per probe is also what an agent actually looks like:
    one run, a handful of calls.
    """
    _, token = live_token(project["id"], agent_id)
    if method == "GET":
        response = client.get(path, headers=bearer(token), params=queries.get(key, {}))
    else:
        response = client.post(path, headers=bearer(token), json=bodies.get(key, {}))
    # A 422 from REQUEST VALIDATION stopped the call before the handler, where
    # the authority check lives — the fixture above is wrong for that route,
    # and a silent false "not refused" is the one outcome this test must never
    # produce. A 422 the handler itself raised is a real answer and means the
    # agent got past the gate. FastAPI's validation error is the one with a
    # `detail` list; a Refusal carries `error`.
    if response.status_code == 422:
        body = response.json()
        assert not isinstance(body.get("detail"), list), (
            f"{method} {path} never reached its handler, so this test cannot "
            f"see whether it refuses an employee. _minimal_body could not "
            f"satisfy it: {response.text[:300]}")
    return response.status_code


def _is_role_rule(client, project: dict, employee: str, ceo: str, method: str,
                  path: str, key: str, bodies: dict, queries: dict) -> bool:
    """Whether this endpoint refuses an employee BECAUSE it is an employee.

    Asked of the employee alone, `messages_read` looked CEO-only: the
    synthesised `channel_id` names a room it is not in, and the server answers
    403 to that whoever asks. The same arguments put to the CEO separate the
    two — a rule about the caller refuses one and not the other; a rule about
    the arguments refuses both.
    """
    as_employee = _probe(client, project, employee, method, path, key, bodies, queries)
    if as_employee != 403:
        return False
    return _probe(client, project, ceo, method, path, key, bodies, queries) != 403


def test_the_ceo_only_list_is_exactly_what_the_server_refuses(client, sandbox_entry):
    """Derived over HTTP, as an agent would find out. A hand-kept list would rot
    in both directions, and both are expensive: a verb that became CEO-only
    goes on costing every employee its schema forever, and a verb that stopped
    being one vanishes from the agents that now need it."""
    # Deliver, and autonomous, because the fixture defaults confound the
    # question this test is asking:
    #
    #   * Explore has no requirement list, so `requirement_propose` answers
    #     everyone with a 409 about the mode and its authority check is never
    #     reached.
    #   * Supervised parks `commitment_authority` and `capability_authority`
    #     with the chairman, so `commitment_reset` and `credential_scope`
    #     refuse the CEO too — for a reason about the DIAL rather than about
    #     who is asking. An employee is still refused at every dial setting,
    #     which is what makes them role rules and what this has to isolate.
    project = make_project(client, mode="deliver",
                           constitution=AUTONOMY_PRESETS["autonomous"])
    employee = agent_by_name(project, "Emp2")
    ceo = project["ceo_agent_id"]

    endpoints = _endpoints(sandbox_entry)
    bodies, queries = _bodies()
    derived = {
        name for name, (method, path, key) in sorted(endpoints.items())
        if _is_role_rule(client, project, employee, ceo, method, path, key,
                         bodies, queries)
    }
    declared = set(sandbox_entry.CEO_ONLY_TOOLS)
    assert derived == declared, (
        "CEO_ONLY_TOOLS in yaaf/runner/sandbox_entry.py disagrees with the "
        f"server.\n  the server refuses, the list omits: {sorted(derived - declared)}"
        f"\n  the list claims, the server allows:  {sorted(declared - derived)}")


def test_an_employee_is_not_sent_the_tools_it_cannot_use(monkeypatch, sandbox_entry):
    monkeypatch.setattr(sandbox_entry, "AGENT_KIND", "employee")
    monkeypatch.setattr(sandbox_entry, "_data_source_specs", lambda: {})
    names = {t.name for t in sandbox_entry.run_tools()}

    assert not (names & sandbox_entry.CEO_ONLY_TOOLS)
    assert "board" in names and "task_claim" in names and "message_post" in names


def test_the_ceo_is_sent_everything(monkeypatch, sandbox_entry):
    monkeypatch.setattr(sandbox_entry, "AGENT_KIND", "ceo")
    monkeypatch.setattr(sandbox_entry, "_data_source_specs", lambda: {})
    names = {t.name for t in sandbox_entry.run_tools()}
    assert names >= sandbox_entry.CEO_ONLY_TOOLS
    assert names == {t.name for t in sandbox_entry.ALL_TOOLS}


def test_an_unset_kind_sends_everything(sandbox_entry):
    """The failure that costs real money is withholding a tool an agent needed.
    A caller that forgets the env var must degrade toward the old behaviour,
    never toward a silently crippled agent — so the fixture deliberately leaves
    YAAF_AGENT_KIND unset and this asserts what that means."""
    assert sandbox_entry.AGENT_KIND == "ceo"
    assert {t.name for t in sandbox_entry.ALL_TOOLS} >= sandbox_entry.CEO_ONLY_TOOLS


def test_withholding_a_schema_is_not_the_enforcement(client):
    """The whole risk of this optimisation in one test.

    An agent that was never offered `hire` and forges the call anyway must be
    refused by the server exactly as before. If the refusal ever stops firing,
    the cost measure has quietly become the wall — and nothing else in the
    suite would notice, because no tool list is involved in an HTTP request.
    """
    project = make_project(client)
    bodies, _ = _bodies()
    for path in ("/agent/teams", "/agent/capacity", "/agent/directives"):
        _, employee = live_token(project["id"], agent_by_name(project, "Emp2"))
        refused = client.post(path, headers=bearer(employee), json=bodies[path])
        assert refused.status_code == 403, f"{path}: {refused.status_code} {refused.text[:200]}"


@pytest.mark.parametrize("kind", ["ceo", "employee"])
def test_every_tool_offered_is_a_tool_the_allowlist_permits(monkeypatch, kind, sandbox_entry):
    """`_allowed_tools` and `run_tools` must not drift apart: a tool offered but
    not permitted is a schema paid for and then refused by the SDK, which is
    the same waste this change exists to remove, wearing a different hat."""
    monkeypatch.setattr(sandbox_entry, "AGENT_KIND", kind)
    monkeypatch.setattr(sandbox_entry, "_data_source_specs", lambda: {})
    monkeypatch.setattr(sandbox_entry, "_external_mcp_servers", lambda: [])
    allowed = set(sandbox_entry._allowed_tools())
    for tool in sandbox_entry.run_tools():
        assert f"mcp__yaaf__{tool.name}" in allowed


def test_the_saving_is_real_and_is_measured(monkeypatch, sandbox_entry):
    """Not a vanity metric: if a later change pushes the CEO-only share back
    under a third of the block, the gating is no longer paying for the
    complexity and somebody should be told rather than left assuming."""
    import json

    def size(tool) -> int:
        return len(json.dumps({"name": tool.name, "description": tool.description,
                               "input_schema": getattr(tool, "input_schema", {})},
                              default=str))

    monkeypatch.setattr(sandbox_entry, "_data_source_specs", lambda: {})
    whole = sum(size(t) for t in sandbox_entry.ALL_TOOLS)
    withheld = sum(size(t) for t in sandbox_entry.ALL_TOOLS
                   if t.name in sandbox_entry.CEO_ONLY_TOOLS)
    assert withheld / whole > 0.33, (
        f"CEO-only tools are {100 * withheld / whole:.0f}% of the schema block; "
        "this gating was built when it was 48%")


def test_integration_request_is_sent_only_when_something_is_on_offer(monkeypatch, sandbox_entry):
    """Like data_fetch for a role with no source: a schema with nothing to ask
    for is paid for on every call and answered with a 404 every time. The
    lease names what is on offer (`integrations_offered`); nothing else sends it."""
    assert "integration_request" not in {t.name for t in sandbox_entry.run_tools()}
    monkeypatch.setattr(sandbox_entry, "PERMS", {"integrations_offered": [{"name": "hubspot"}]})
    monkeypatch.setattr(sandbox_entry, "AGENT_KIND", "employee")
    assert "integration_request" in {t.name for t in sandbox_entry.run_tools()}
