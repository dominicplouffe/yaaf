"""Invariant selftests: one Anthropic key is enough to start.

The Founder — the recommended way to create a project — ran only on a key of
its own, "deliberately not one of the LLM credentials". An administrator who
had just added an Anthropic key for projects met the Founder asking for the
same key again, under a second name, before the first project could be drafted.
Over MCP, `grade_goal` quietly did nothing until they found the second door.

So the Founder may borrow an LLM credential. The rules under attack here:

1. Borrowing makes the Founder runnable on that credential's value, mode and
   provider, and every screen says which credential it is — never the value.
2. There is one answer to "what does the Founder run on?": installing a key of
   its own ends the borrowing, and borrowing replaces a key of its own.
3. Stopping it never touches the LLM credential, which projects may pay with.
4. Only an offerable credential can be borrowed, and only by an administrator.
5. It runs one way only: borrowing never makes the Founder's key — or the
   borrowed one — a project's billing credential.
"""

from __future__ import annotations

import json

from tests.conftest import ADMIN, bearer, live_token, make_project
from yaaf.core import platform
from yaaf.core.db import db_session
from yaaf.core.models import Project
from yaaf.runner.daemon import _billing_env

LLM = "/admin/settings/llm-credentials"
API_KEY = "sk-ant-api-not-a-real-key-1111"
OWN_KEY = "sk-ant-api-not-a-real-key-2222"


def _llm_credential(client, name="Production", value=API_KEY, mode="api_key") -> dict:
    response = client.post(LLM, headers=ADMIN, json={"name": name, "value": value, "mode": mode})
    assert response.status_code == 201, response.text
    return response.json()


def _borrow(client, credential_id: str):
    return client.put("/admin/founder/credential/llm", headers=ADMIN,
                      json={"credential_id": credential_id})


def _status(client) -> dict:
    return client.get("/admin/founder/status", headers=ADMIN).json()


def test_one_key_runs_the_founder_and_the_screens_name_it(client):
    credential = _llm_credential(client)
    assert _status(client)["credential_configured"] is False

    borrowed = _borrow(client, credential["id"])
    assert borrowed.status_code == 200, borrowed.text

    status = _status(client)
    assert status["credential_configured"] is True
    assert status["credential_source"] == {
        "source": "llm_credential", "credential_id": credential["id"], "name": "Production"}
    assert status["billing_mode"] == "api_key"
    settings = client.get("/admin/settings", headers=ADMIN).json()["anthropic"]
    assert settings["configured"] is True
    assert settings["source"]["name"] == "Production"
    # Which credential, never what it holds.
    assert API_KEY not in json.dumps(status) + json.dumps(settings) + borrowed.text

    with db_session() as session:
        assert platform.credential(session) == API_KEY


def test_the_borrowed_credential_bills_the_way_it_says(client):
    credential = _llm_credential(client, name="Plan", value="sk-ant-oat-not-a-real-token-33",
                                 mode="subscription")
    assert _borrow(client, credential["id"]).status_code == 200
    assert _status(client)["billing_mode"] == "subscription"


def test_a_key_of_its_own_ends_the_borrowing(client):
    credential = _llm_credential(client)
    _borrow(client, credential["id"])

    own = client.put("/admin/founder/credential", headers=ADMIN,
                     json={"value": OWN_KEY, "mode": "api_key"})
    assert own.status_code == 200, own.text
    assert _status(client)["credential_source"] == {"source": "own"}
    with db_session() as session:
        assert platform.credential(session) == OWN_KEY


def test_borrowing_replaces_a_key_of_its_own(client):
    client.put("/admin/founder/credential", headers=ADMIN,
               json={"value": OWN_KEY, "mode": "api_key"})
    credential = _llm_credential(client)
    _borrow(client, credential["id"])
    with db_session() as session:
        assert platform.credential(session) == API_KEY
        assert platform._own_row(session) is None


def test_stopping_leaves_the_llm_credential_alone(client):
    credential = _llm_credential(client)
    _borrow(client, credential["id"])

    removed = client.delete("/admin/founder/credential", headers=ADMIN)
    assert removed.status_code == 200 and removed.json()["removed"] is True
    assert _status(client)["credential_configured"] is False
    names = [c["name"] for c in client.get(LLM, headers=ADMIN).json()]
    assert "Production" in names


def test_a_deleted_credential_leaves_the_founder_saying_it_has_no_key(client):
    credential = _llm_credential(client)
    _borrow(client, credential["id"])
    deleted = client.request("DELETE", f"{LLM}/{credential['id']}", headers=ADMIN)
    assert deleted.status_code in (200, 204), deleted.text
    assert _status(client)["credential_configured"] is False


def test_only_a_credential_on_the_menu_can_be_borrowed(client):
    assert _borrow(client, "sec_does_not_exist").status_code == 422
    credential = _llm_credential(client)
    hidden = client.post(f"{LLM}/hide", headers=ADMIN, json={"ids": [credential["id"]]})
    assert hidden.status_code == 200, hidden.text
    refused = _borrow(client, credential["id"])
    assert refused.status_code == 422
    assert _status(client)["credential_configured"] is False


def test_nobody_but_an_administrator_may_point_the_founder_at_one(client):
    credential = _llm_credential(client)
    project = make_project(client)
    _, token = live_token(project["id"], project["ceo_agent_id"])
    for headers in ({}, bearer(token)):
        refused = client.put("/admin/founder/credential/llm", headers=headers,
                             json={"credential_id": credential["id"]})
        assert refused.status_code == 401, refused.text
    assert _status(client)["credential_configured"] is False


def test_it_runs_one_way_borrowing_makes_nobody_a_projects_payer(client):
    credential = _llm_credential(client)
    _borrow(client, credential["id"])
    project = make_project(client)
    with db_session() as session:
        row = session.get(Project, project["id"])
        assert row.billing_secret_id is None
        assert _billing_env(session, row) == {}
