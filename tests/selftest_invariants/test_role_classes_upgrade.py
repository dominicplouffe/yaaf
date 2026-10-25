"""Invariant selftests: an install that already has the role library moves it
onto model classes on update, without a decision forced on anybody
(docs/PROVIDERS.md, migration; yaaf.core.role_versions).

- `adopt-class` stamps a version identical but for its model, only where the
  role still names the Claude model its class means on Anthropic; a role moved
  by hand is left alone, and only an administrator may do it.
- A team pinned to the old version is offered the new one only where it would
  change what the team runs: on a project paying Anthropic it would not, so
  nothing asks — no drift line, no "Adopt", no "behind".
"""

from __future__ import annotations

from tests.conftest import ADMIN, ROLE_MIN, headers_of, person


def _role(client, slug, model, ceo=False):
    assert client.post("/admin/roles", headers=ADMIN, json={
        "slug": slug, "name": slug.title(), **ROLE_MIN, "model": model,
        "is_ceo_role": ceo}).status_code == 200


def _adopt(client, slug, model, headers=ADMIN):
    return client.post(f"/admin/roles/{slug}/adopt-class", headers=headers, json={"model": model})


def _project(client, slug, billing, credentials=None):
    created = client.post("/admin/projects", headers=ADMIN, json={
        "slug": slug, "name": slug, "goal": "Prove the framework works.",
        "chairman_email": "chair@example.com", "ceo_role_slug": "chief", "ceo_name": "CEO",
        "billing_secret_id": billing, "credentials": credentials or {},
        "teams": [{"role_slug": "writer", "name": "Words", "employees": ["Ada"]}]})
    assert created.status_code == 200, created.text
    return created.json()


def _key(client, name, provider, value):
    return client.post("/admin/settings/llm-credentials", headers=ADMIN, json={
        "name": name, "mode": "api_key", "provider": provider, "value": value}).json()["id"]


def test_a_library_role_moves_onto_its_class_only_where_it_still_names_that_model(client):
    _role(client, "writer", "claude-sonnet-5-5")
    _role(client, "custom", "claude-opus-5-5")
    moved = _adopt(client, "writer", "@balanced")
    assert moved.status_code == 200 and moved.json() == {
        "slug": "writer", "changed": True, "version": 2}
    role = client.get("/admin/roles/writer", headers=ADMIN).json()
    assert (role["model"], role["system_prompt"]) == ("@balanced", ROLE_MIN["system_prompt"])
    assert _adopt(client, "writer", "@balanced").json()["changed"] is False
    # Somebody put this one on Opus by hand: theirs, left as it is.
    kept = _adopt(client, "custom", "@balanced")
    assert kept.status_code == 409 and "moved by hand" in kept.text
    assert client.get("/admin/roles/custom", headers=ADMIN).json()["version"] == 1
    assert _adopt(client, "writer", "@huge").status_code == 422
    member = headers_of(person("member@example.com"))
    assert _adopt(client, "writer", "@fast", headers=member).status_code == 403


def test_a_version_that_changes_nothing_a_team_runs_is_never_offered(client):
    client.post("/admin/users", headers=ADMIN, json={"email": "chair@example.com",
                                                     "display_name": "Chair"})
    _role(client, "chief", "claude-opus-5-5", ceo=True)
    _role(client, "writer", "claude-sonnet-5-5")
    claude = _key(client, "Claude", "anthropic", "sk-ant-api03-" + "u" * 40)
    on_claude = _project(client, "on-claude", claude)
    # The same roles on a project billed by OpenAI that pays Anthropic too:
    # there `@balanced` is an OpenAI model, so the new version matters.
    client.post("/admin/settings/model-prices/import", headers=ADMIN,
                json={"source": "snapshot", "apply": True})
    openai = _key(client, "OpenAI", "openai", "sk-proj-upgrade")
    on_openai = _project(client, "on-openai", openai, credentials={"anthropic": claude})
    assert _adopt(client, "writer", "@balanced").json()["changed"] is True

    def drift(project):
        checks = client.get(f"/observe/projects/{project['id']}/health",
                            headers=ADMIN).json()["checks"]
        return [c for c in checks if c["key"].startswith("role-drift:")]

    def team(project):
        org = client.get(f"/observe/projects/{project['id']}/org", headers=ADMIN).json()
        return next(t for t in org["teams"] if t["name"] == "Words")

    assert drift(on_claude) == []
    assert team(on_claude)["role_latest_version"] == 1
    assert [c["key"] for c in drift(on_openai)] == ["role-drift:Words"]
    assert team(on_openai)["role_latest_version"] == 2
    usage = next(r for r in client.get("/admin/roles", headers=ADMIN).json()
                 if r["slug"] == "writer")["usage"]
    behind = {u["project_name"]: u["behind"] for u in usage}
    assert behind == {"on-claude": False, "on-openai": True}


def test_a_team_whose_every_seat_names_its_own_model_is_not_offered_a_model_change(client):
    """Before roles named classes, a project on another provider's key put
    every seat on that provider's model by an override — so the role's model
    is read by nobody on the team, and a version changing only it is nothing
    to adopt."""
    client.post("/admin/users", headers=ADMIN, json={"email": "chair@example.com",
                                                     "display_name": "Chair"})
    _role(client, "chief", "claude-opus-5-5", ceo=True)
    _role(client, "writer", "claude-sonnet-5-5")
    client.put("/admin/settings/model-prices/gpt-5.5", headers=ADMIN,
               json={"input": 1.25, "output": 10})
    openai = _key(client, "OpenAI", "openai", "sk-proj-overridden")
    created = client.post("/admin/projects", headers=ADMIN, json={
        "slug": "overridden", "name": "overridden", "goal": "Prove the framework works.",
        "chairman_email": "chair@example.com", "ceo_role_slug": "chief", "ceo_name": "CEO",
        "billing_secret_id": openai, "ceo_model_override": "gpt-5.5",
        "teams": [{"role_slug": "writer", "name": "Words", "employees": ["Ada"],
                   "model_overrides": {"Ada": "gpt-5.5"}}]})
    assert created.status_code == 200, created.text
    assert _adopt(client, "writer", "@balanced").json()["changed"] is True
    checks = client.get(f"/observe/projects/{created.json()['id']}/health",
                        headers=ADMIN).json()["checks"]
    assert not [c for c in checks if c["key"].startswith("role-drift:")]
