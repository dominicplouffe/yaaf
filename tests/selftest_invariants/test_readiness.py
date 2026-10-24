"""Invariant selftests: provider readiness — what the doors would refuse, read
before anybody is refused (yaaf.core.readiness, docs/PROVIDERS.md §8).

A provider half set up used to be found one refusal at a time, by creating a
project. The readiness view must say every gap at once, each with its fix, and
must agree with the doors: a provider it calls ready creates the project, and
one it blocks is refused for the reason it gave.
"""

from __future__ import annotations

from tests.conftest import ADMIN, ROLE_MIN, headers_of, person

READINESS = "/admin/settings/providers/readiness"


def _deepseek(client) -> str:
    return client.post("/admin/settings/llm-credentials", headers=ADMIN, json={
        "name": "DeepSeek", "mode": "api_key", "provider": "deepseek",
        "value": "sk-deepseek-readiness"}).json()["id"]


def _roles(client) -> None:
    client.post("/admin/users", headers=ADMIN, json={
        "email": "chair@example.com", "display_name": "Chair"})
    for slug, model, web in (("chief", "@frontier", True), ("writer", "@fast", False)):
        assert client.post("/admin/roles", headers=ADMIN, json={
            "slug": slug, "name": slug.title(), **ROLE_MIN, "model": model,
            "tool_permissions": {"web": web}, "is_ceo_role": slug == "chief"}).status_code == 200


def _create(client, credential: str):
    return client.post("/admin/projects", headers=ADMIN, json={
        "slug": "ready", "name": "Ready", "goal": "Prove the framework works.",
        "chairman_email": "chair@example.com", "ceo_role_slug": "chief", "ceo_name": "CEO",
        "billing_secret_id": credential,
        "teams": [{"role_slug": "writer", "name": "Words", "employees": ["Ada"]}]})


def test_an_empty_install_says_it_is_not_connected(client):
    found = client.get(READINESS, headers=ADMIN).json()
    assert found["providers"] == []
    assert found["install"]["connected"] is False and found["install"]["ready"] is False


def test_every_gap_is_named_with_its_fix_and_agrees_with_the_doors(client):
    _roles(client)
    credential = _deepseek(client)
    [deepseek] = client.get(READINESS, headers=ADMIN).json()["providers"]
    issues = {i["code"]: i for i in deepseek["issues"]}
    assert deepseek["ready"] is False
    assert issues["unpriced"]["severity"] == "block"
    assert issues["unpriced"]["fix"] == {"action": "import_prices",
                                         "models": ["deepseek-reasoner", "deepseek-chat"]}
    assert issues["no_search"]["fix"] == {"action": "web_search"}
    assert "1 of the 2 roles" in issues["no_search"]["text"]
    assert [t["model"] for t in deepseek["tiers"]] == [
        "deepseek-reasoner", "deepseek-chat", "deepseek-chat"]
    # Named here is refused there, for the same reasons: this project's CEO
    # browses, so the missing search refuses it before the missing price does.
    refused = _create(client, credential)
    assert refused.status_code == 422 and "cannot search the web on DeepSeek" in refused.text
    # The fix it offered, applied — and the search backend — and it is ready.
    client.post("/admin/settings/model-prices/import", headers=ADMIN, json={
        "source": "snapshot", "apply": True, "models": issues["unpriced"]["fix"]["models"]})
    client.put("/admin/settings/web-search", headers=ADMIN, json={
        "backend": "brave", "key": "brave-readiness-key"})
    [deepseek] = client.get(READINESS, headers=ADMIN).json()["providers"]
    assert deepseek["ready"] is True and deepseek["issues"] == []
    assert deepseek["search"] == "platform"
    assert all(t["priced"] and t["context_window"] for t in deepseek["tiers"])
    assert _create(client, credential).status_code == 200


def test_only_an_administrator_reads_it(client):
    member = headers_of(person("member@example.com"))
    assert client.get(READINESS, headers=member).status_code == 403
