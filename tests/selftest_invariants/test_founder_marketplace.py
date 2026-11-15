"""The Founder hiring from the marketplace, attacked.

It may search a library source and name a listing in its draft. It may not
install anything: the role reaches the library only when the chairman creates
the project, through the checks in test_library_install.py.
"""

from __future__ import annotations

import pytest

from tests.conftest import ADMIN
from tests.selftest_invariants import test_founder as _founding
from tests.selftest_invariants.test_founder import PROJECT, _tool_payload, start
from tests.selftest_invariants.test_library_install import WRITER, Registry
from yaaf.server import library

#: The Founder test harness's own fixture: a scripted turn through the real tools.
founder = _founding.founder

SKEPTIC = {**WRITER, "slug": "skeptic", "name": "Skeptic", "category": "challenge",
           "description": "Attacks the plan before the world does.",
           "system_prompt": "You find what the plan missed and say so with evidence."}


@pytest.fixture
def registry(tmp_path, monkeypatch):
    reg = Registry(tmp_path / "reg")
    reg.root.mkdir()
    reg.write()
    monkeypatch.setattr(library, "configured_sources", lambda: [reg.url])
    yield reg
    library.clear_cache()


def _library(client):
    return {r["slug"]: r for r in client.get("/admin/roles", headers=ADMIN).json()}


def test_the_founder_searches_the_marketplace(client, founder, registry):
    registry.publish("@rae/writer", "role", {"role": WRITER})
    calls = founder(tool_calls=[("search_marketplace", {"query": "writer"})])
    start(client, "I need a blog")
    found = _tool_payload(next(c for c in calls if c.get("tool") == "search_marketplace"))
    assert [r["id"] for r in found["results"]] == ["@rae/writer"]
    assert found["results"][0]["source"] == registry.url


def test_a_marketplace_hire_waits_for_the_chairman(client, founder, registry):
    registry.publish("@rae/writer", "role", {"role": WRITER})
    project = {**PROJECT, "teams": [{"role_slug": "writer", "name": "Words",
                                     "employees": ["Ann"], "lead": "Ann"}]}
    founder(tool_calls=[("propose_bundle", {
        "project": project, "marketplace": [{"id": "@rae/writer", "source": registry.url}]})])
    thread = start(client, "write for me")
    draft = thread["draft_bundle"]
    assert draft["roles"][0]["provenance"]["source"] == "@rae/writer"
    # Drafted, not installed.
    assert "writer" not in _library(client)

    created = client.post("/admin/projects", headers=ADMIN,
                          json={**draft["project"], "chairman_email": None})
    assert created.status_code == 200, created.text
    assert _library(client)["writer"]["source"] == "@rae/writer"


def test_a_removed_listing_never_reaches_a_draft(client, founder, registry):
    registry.publish("@rae/writer", "role", {"role": WRITER}, status="removed", reason="leaked keys")
    project = {**PROJECT, "teams": [{"role_slug": "writer", "name": "Words",
                                     "employees": ["Ann"], "lead": "Ann"}]}
    calls = founder(tool_calls=[("propose_bundle", {
        "project": project, "marketplace": [{"id": "@rae/writer", "source": registry.url}]})])
    thread = start(client, "write for me")
    refused = _tool_payload(next(c for c in calls if c.get("tool") == "propose_bundle"))
    assert refused["ok"] is False and "leaked keys" in " ".join(refused["problems"])
    assert thread["draft_bundle"] is None


def test_a_marketplace_skeptic_counts_as_the_challenge_seat(client, founder, registry):
    registry.publish("@rae/skeptic", "role", {"role": SKEPTIC})
    project = {**PROJECT, "mode": "deliver",
               "teams": [{"role_slug": "worker", "name": "Build", "employees": ["Mika"]},
                         {"role_slug": "skeptic", "name": "Doubt", "employees": ["Sol"]}]}
    calls = founder(tool_calls=[("propose_bundle", {
        "project": project, "marketplace": [{"id": "@rae/skeptic", "source": registry.url}]})])
    start(client, "ship it")
    result = _tool_payload(next(c for c in calls if c.get("tool") == "propose_bundle"))
    assert not any("challenge" in p for p in result.get("problems", [])), result


# ── a role the Founder writes ─────────────────────────────────────────────────

LEGAL = {"slug": "contract-reviewer", "name": "Contract Reviewer", "category": "challenge",
         "domains": ["legal"], "description": "Reads contracts for risk before anyone signs.",
         "system_prompt": "You read each contract clause by clause and flag what shifts risk.",
         "effort": "high", "tool_permissions": {"files": True}, "secret_scopes": []}


def _with_new_role(role):
    return {**PROJECT, "teams": [{"role_slug": role["slug"], "name": "Review",
                                  "employees": ["Ines"], "lead": "Ines"}]}


def test_a_role_the_founder_writes_waits_for_the_chairman(client, founder):
    founder(tool_calls=[("propose_bundle", {"project": _with_new_role(LEGAL), "new_roles": [LEGAL]})])
    thread = start(client, "review my contracts")
    draft = thread["draft_bundle"]
    assert draft["roles"][0]["drafted"] is True
    assert "contract-reviewer" not in _library(client)
    created = client.post("/admin/projects", headers=ADMIN, json={**draft["project"], "chairman_email": None})
    assert created.status_code == 200, created.text
    role = _library(client)["contract-reviewer"]
    assert role["source"] is None and role["domains"] == ["legal"]


@pytest.mark.parametrize("change, said", [
    ({"tool_permissions": {"egress_unrestricted": True}}, "cannot grant itself"),
    ({"slug": "worker"}, "slug is taken"),
    ({"system_prompt": "You review {{client_name}}'s contracts carefully, clause by clause."}, "no blanks"),
    ({"category": "vibes"}, "category is one of"),
])
def test_a_role_the_founder_writes_is_held_to_the_rules(client, founder, change, said):
    role = {**LEGAL, **change}
    calls = founder(tool_calls=[("propose_bundle", {"project": _with_new_role(role), "new_roles": [role]})])
    start(client, "review my contracts")
    refused = _tool_payload(next(c for c in calls if c.get("tool") == "propose_bundle"))
    assert refused["ok"] is False and said in " ".join(refused["problems"]), refused
