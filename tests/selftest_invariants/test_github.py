"""Invariant selftests: GitHub workspace provisioning (M4). The platform-level
"github" token creates/verifies repos and performs the runner's one-time clone,
but must never persist anywhere an agent can read — .git/config keeps the
credential-free URL, and agents push with their own role-scoped secret."""

from __future__ import annotations

import subprocess
from pathlib import Path

from sqlalchemy import select

from tests.conftest import ADMIN, make_project
from yaaf.core.db import db_session
from yaaf.core.models import AuditLog, Project
from yaaf.runner import daemon
from yaaf.server import github

PLATFORM_TOKEN = "ghp_platform_token_for_tests"


def _install_platform_secret(client) -> None:
    response = client.post(
        "/admin/secrets",
        json={"project_id": None, "name": "github",
              "value": PLATFORM_TOKEN, "role_scopes": []},
        headers=ADMIN,
    )
    assert response.status_code == 200, response.text


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload


def _boom(*_args, **_kwargs):
    raise AssertionError("unexpected call")


# ── admin attach endpoint ───────────────────────────────────────────────────────


def test_attach_creates_private_repo_and_audits(client, monkeypatch):
    project = make_project(client, slug="ghcreate")
    _install_platform_secret(client)
    calls = []

    def fake_request(method, path, token, json=None):
        calls.append((method, path, token, json))
        return _FakeResponse(201, {"full_name": "platform-bot/widget"})

    monkeypatch.setattr(github, "_request", fake_request)
    response = client.post(f"/admin/projects/{project['id']}/github",
                           json={"repo": "widget"}, headers=ADMIN)
    assert response.status_code == 200, response.text
    assert response.json() == {"github_repo": "platform-bot/widget"}

    method, path, token, payload = calls[0]
    assert (method, path) == ("POST", "/user/repos")
    assert token == PLATFORM_TOKEN  # decrypted server-side from the platform secret
    assert payload["name"] == "widget"
    assert payload["private"] is True
    assert payload["auto_init"] is True

    with db_session() as session:
        assert session.get(Project, project["id"]).github_repo == "platform-bot/widget"
        audit = session.execute(
            select(AuditLog).where(AuditLog.action == "project.github_attach")
        ).scalar_one()
        assert audit.entity_id == project["id"]
        assert audit.detail == {"github_repo": "platform-bot/widget"}


def test_attach_verifies_existing_owner_repo(client, monkeypatch):
    project = make_project(client, slug="ghverify")
    _install_platform_secret(client)

    def fake_request(method, path, token, json=None):
        assert (method, path) == ("GET", "/repos/acme/widget")
        assert token == PLATFORM_TOKEN
        return _FakeResponse(200, {"full_name": "acme/widget", "private": True})

    monkeypatch.setattr(github, "_request", fake_request)
    response = client.post(f"/admin/projects/{project['id']}/github",
                           json={"repo": "acme/widget"}, headers=ADMIN)
    assert response.status_code == 200, response.text
    assert response.json() == {"github_repo": "acme/widget"}
    with db_session() as session:
        assert session.get(Project, project["id"]).github_repo == "acme/widget"

    # An owner/repo the token cannot see is refused — and the attach is not recorded.
    monkeypatch.setattr(github, "_request",
                        lambda *a, **k: _FakeResponse(404, {"message": "Not Found"}))
    denied = client.post(f"/admin/projects/{project['id']}/github",
                         json={"repo": "acme/nope"}, headers=ADMIN)
    assert denied.status_code == 422
    assert "acme/nope" in denied.json()["detail"]
    with db_session() as session:
        assert session.get(Project, project["id"]).github_repo == "acme/widget"


def test_attach_without_platform_secret_is_a_clean_422(client, monkeypatch):
    project = make_project(client, slug="ghnosecret")
    monkeypatch.setattr(github, "_request", _boom)  # must fail before any network hop
    response = client.post(f"/admin/projects/{project['id']}/github",
                           json={"repo": "widget"}, headers=ADMIN)
    assert response.status_code == 422, response.text
    assert "github" in response.json()["detail"]
    with db_session() as session:
        assert session.get(Project, project["id"]).github_repo is None


# ── runner clone ────────────────────────────────────────────────────────────────


def _seed_bare_repo(tmp_path: Path) -> Path:
    """A local bare repo with one commit, standing in for github.com."""
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(bare), str(seed)], check=True)
    (seed / "README.md").write_text("seeded\n")
    git = ["git", "-C", str(seed),
           "-c", "user.email=test@example.com", "-c", "user.name=Test"]
    subprocess.run([*git, "add", "README.md"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "seed"], check=True)
    subprocess.run([*git, "push", "-q", "origin", "HEAD"], check=True)
    return bare


def _set_github_repo(project_id: str, full_name: str) -> None:
    with db_session() as session:
        session.get(Project, project_id).github_repo = full_name


def _prepare(project_id: str) -> Path:
    with db_session() as session:
        return Path(daemon._prepare_workspace(session, session.get(Project, project_id)))


def test_runner_clones_and_scrubs_credentials(client, monkeypatch, tmp_path):
    project = make_project(client, slug="ghclone")
    _install_platform_secret(client)
    bare = _seed_bare_repo(tmp_path)
    full_name = "acme/ghclone"
    _set_github_repo(project["id"], full_name)

    seen = {}

    def fake_clone_url(name, token):
        seen["name"], seen["token"] = name, token
        return f"file://{bare}"

    monkeypatch.setattr(github, "clone_url", fake_clone_url)
    workspace = _prepare(project["id"])

    assert seen == {"name": full_name, "token": PLATFORM_TOKEN}
    assert (workspace / ".git").is_dir()
    assert (workspace / "README.md").read_text() == "seeded\n"

    # The whole point: the credentialed URL lived only for the clone itself.
    origin = subprocess.run(
        ["git", "-C", str(workspace), "remote", "get-url", "origin"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert origin == f"https://github.com/{full_name}.git"
    config = (workspace / ".git" / "config").read_text()
    assert PLATFORM_TOKEN not in config
    assert "x-access-token" not in config

    # An existing .git is the agents' working tree: a second prepare leaves it be.
    (workspace / "WIP.txt").write_text("agent work\n")
    monkeypatch.setattr(github, "clone_url", _boom)
    assert _prepare(project["id"]) == workspace
    assert (workspace / "WIP.txt").read_text() == "agent work\n"


def test_clone_failure_falls_back_to_plain_dir(client, monkeypatch, tmp_path):
    project = make_project(client, slug="ghfallback")
    _install_platform_secret(client)
    _set_github_repo(project["id"], "acme/ghfallback")
    monkeypatch.setattr(github, "clone_url",
                        lambda name, token: f"file://{tmp_path}/does-not-exist.git")

    workspace = _prepare(project["id"])
    assert workspace.is_dir()  # the run still gets a workspace
    assert not (workspace / ".git").exists()


def test_runner_without_platform_secret_uses_plain_dir(client, monkeypatch):
    project = make_project(client, slug="ghtokenless")
    _set_github_repo(project["id"], "acme/ghtokenless")
    monkeypatch.setattr(github, "clone_url", _boom)  # no token, so no clone attempt

    workspace = _prepare(project["id"])
    assert workspace.is_dir()
    assert not (workspace / ".git").exists()
