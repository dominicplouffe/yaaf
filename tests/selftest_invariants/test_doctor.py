"""Invariant selftests: `yaaf doctor` names what is missing and how to fix it.

A fresh install met its missing pieces one at a time, each looking like
something else: an empty crew picker, a 500 on the first saved credential,
runs queued forever by a server that never executes them, a Founder asking for
a second key. The doctor checks them at once, and every failure carries the
command or the page that fixes it.
"""

from __future__ import annotations

import contextlib
from datetime import timedelta

from starlette.testclient import TestClient
from typer.testing import CliRunner

from tests.conftest import ADMIN, bearer, live_token, make_project, seed_role
from yaaf.cli import main as cli
from yaaf.core import ids
from yaaf.core.db import db_session
from yaaf.core.models import RunQueue, utcnow

URL = "/admin/settings/doctor"


def _checks(client) -> dict:
    response = client.get(URL, headers=ADMIN)
    assert response.status_code == 200, response.text
    return {c["name"]: c for c in response.json()["checks"]}


def test_an_empty_library_is_a_problem_with_its_command(client):
    roles = _checks(client)["roles"]
    assert roles["ok"] is False and roles["level"] == "problem"
    assert "yaaf seed-roles roles/" in roles["fix"]


def test_a_seeded_library_is_fine_and_carries_no_fix(client):
    seed_role(client, "ceo", is_ceo=True)
    roles = _checks(client)["roles"]
    assert roles["ok"] is True and roles["fix"] is None


def test_no_key_to_pay_with_says_where_to_add_one(client):
    check = _checks(client)["a key to pay with"]
    assert check["ok"] is False
    assert "create-llm-credential" in check["fix"]


def test_runs_waiting_with_nothing_started_point_at_the_runner(client):
    project = make_project(client)
    with db_session() as session:
        session.add(RunQueue(id=ids.new_id(ids.RUN), project_id=project["id"],
                             agent_id=project["ceo_agent_id"], trigger={},
                             created_at=utcnow() - timedelta(minutes=30)))
    runner = _checks(client)["runner"]
    assert runner["ok"] is False
    assert "yaaf-runner" in runner["fix"]


def test_only_an_instance_admin_reads_the_install(client):
    project = make_project(client)
    _, token = live_token(project["id"], project["ceo_agent_id"])
    for headers in ({}, bearer(token)):
        assert client.get(URL, headers=headers).status_code == 401


def test_the_command_prints_each_failure_with_its_fix(client, monkeypatch):
    as_admin = TestClient(client.app, headers=ADMIN)
    monkeypatch.setattr(cli, "_client", lambda: contextlib.nullcontext(as_admin))
    client.post("/admin/users", json={"email": "chair@example.com", "display_name": "Chair"},
                headers=ADMIN)
    result = CliRunner().invoke(cli.app, ["doctor"])
    assert result.exit_code == 1, result.output
    assert "✗ roles: 0 role(s) in the library" in result.output
    assert "→ yaaf seed-roles roles/" in result.output
