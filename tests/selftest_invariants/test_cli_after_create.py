"""After `yaaf create-project`: what the project still needs, said in the terminal.

The New Project form asks for the keys a project's agents need before it will
create it, and the project's Inbox lists what is left to set up. The terminal
used to do neither: a setup file's credentials were dropped without a word, and
a project whose engineer needed a GitHub token was created looking finished.
The attacks:

  * a key the setup file or a hired role names is asked for, hidden, and stored
    for exactly the roles that need it — and Enter skips it, with the command
    and the link that finish it later
  * a script is never prompted, and its stdout stays the JSON it parses
  * the rest is the server's own setup checklist, each step linked to the
    Settings section that does it — data sources among them, since adding one
    is a browser's job
  * a project with no model key is not offered a start that would only fail
"""

from __future__ import annotations

import contextlib
import json
import pathlib

import pytest
from starlette.testclient import TestClient
from typer.testing import CliRunner

from tests.conftest import ADMIN, seed_role
from tests.selftest_invariants.test_providers import _credential, _id
from yaaf.cli import main as cli

EXAMPLES = pathlib.Path(__file__).resolve().parents[2] / "examples"
runner = CliRunner()


@pytest.fixture()
def terminal(client, monkeypatch):
    as_admin = TestClient(client.app, headers=ADMIN)
    monkeypatch.setattr(cli, "_client", lambda: contextlib.nullcontext(as_admin))
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.delenv("YAAF_PUBLIC_BASE_URL", raising=False)
    monkeypatch.setenv("YAAF_API", "http://yaaf.test:24247")
    client.post("/admin/users", json={"email": "chair@example.com", "display_name": "Chair"},
                headers=ADMIN)
    seed_role(client, "ceo", is_ceo=True)
    return as_admin


def _project(client, slug: str) -> dict:
    return next(p for p in client.get("/observe/projects", headers=ADMIN).json()
                if p["slug"] == slug)


def _secrets(client, project_id: str) -> dict:
    rows = client.get("/admin/secrets", params={"project_id": project_id}, headers=ADMIN)
    return {s["name"]: s for s in rows.json()}


def test_a_setup_files_key_is_asked_for_and_stored_for_its_role(client, terminal):
    seed_role(client, "engineer")  # what `seed-roles` gives every real install
    result = runner.invoke(cli.app, ["create-project", "--from-file",
                                     str(EXAMPLES / "04-an-overnight-crew.json")],
                           input="ghp_test_value_not_real\n")

    assert result.exit_code == 0, result.output
    assert "engineer needs github" in result.output
    assert "ghp_test_value_not_real" not in result.output, "a key is never echoed"
    created = json.loads(result.stdout)
    held = _secrets(client, created["id"])
    assert held["github"]["role_scopes"] == ["engineer"]


def test_a_script_is_never_prompted_and_its_stdout_stays_json(client, terminal, monkeypatch):
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    seed_role(client, "engineer")

    result = runner.invoke(cli.app, ["create-project", "--from-file",
                                     str(EXAMPLES / "04-an-overnight-crew.json")])

    assert result.exit_code == 0, result.output
    created = json.loads(result.stdout)
    assert "github" not in _secrets(client, created["id"])
    assert (f"yaaf set-secret github --project {created['id']} --scopes engineer"
            in result.output)
    assert f"http://yaaf.test:24247/app/projects/{created['id']}/settings?s=secrets" \
        in result.output


def test_a_roles_declared_key_skipped_says_how_to_finish_it(client, terminal, tmp_path):
    seed_role(client, "analyst", secret_scopes=["market-data"])
    spec = tmp_path / "bundle.json"
    spec.write_text(json.dumps({"yaaf_bundle": 1, "roles": [], "project": {
        "slug": "needs-a-feed", "name": "Needs a feed",
        "goal": "Write one sourced note on the market's week.",
        "ceo_role_slug": "ceo",
        "teams": [{"role_slug": "analyst", "name": "Desk", "employees": ["Ada"]}]}}))

    result = runner.invoke(cli.app, ["create-project", "--from-file", str(spec)], input="\n")

    assert result.exit_code == 0, result.output
    project = _project(client, "needs-a-feed")
    assert "analyst needs market-data" in result.output
    assert "Still needed by your agents" in result.output
    assert f"--project {project['id']} --scopes analyst" in result.output
    assert _secrets(client, project["id"]) == {}


def test_the_checklist_links_each_step_and_no_key_means_no_start(client, terminal, tmp_path):
    spec = tmp_path / "bundle.json"
    spec.write_text(json.dumps({"yaaf_bundle": 1, "roles": [], "project": {
        "slug": "unpaid", "name": "Unpaid", "goal": "Write one carried note, sourced.",
        "ceo_role_slug": "ceo"}}))

    result = runner.invoke(cli.app, ["create-project", "--from-file", str(spec), "--start"])

    assert result.exit_code == 0, result.output
    project = _project(client, "unpaid")
    base = f"http://yaaf.test:24247/app/projects/{project['id']}/settings?s="
    assert "Still to set up, in the dashboard" in result.output
    for section in ("money", "sources"):
        assert base + section in result.output
    assert "It cannot start until that is done (model key)" in result.output
    assert "Started." not in result.output
    assert project["status"] != "running"


def test_a_paid_project_starts_when_asked(client, terminal, tmp_path):
    _id(_credential(client, "Main key"))
    spec = tmp_path / "bundle.json"
    spec.write_text(json.dumps({"yaaf_bundle": 1, "roles": [], "project": {
        "slug": "paid", "name": "Paid", "goal": "Write one carried note, sourced.",
        "ceo_role_slug": "ceo", "billing_secret_name": "Main key"}}))

    result = runner.invoke(cli.app, ["create-project", "--from-file", str(spec), "--start"])

    assert result.exit_code == 0, result.output
    assert "settings?s=money" not in result.output, "the key step is done"
    assert "Started." in result.output
    assert _project(client, "paid")["status"] == "running"
