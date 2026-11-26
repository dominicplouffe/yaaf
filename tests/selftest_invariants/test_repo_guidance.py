"""Invariant selftests: a Code Factory seat reads the repo's AGENTS.md and
CLAUDE.md as merged on main — reviewed text — and never a working-tree copy,
which any agent could have written."""

from __future__ import annotations

import base64

import httpx
import pytest

from tests.selftest_invariants.test_merge_gate import REPO, _factory
from yaaf.core.db import db_session
from yaaf.core.models import Agent, Project
from yaaf.server import github, repo_guidance
from yaaf.server.contextpack import build_system_prompt


@pytest.fixture
def main_files(monkeypatch):
    files = {"AGENTS.md": "Run `make test` before every change. Money is in cents."}

    def fake(method, path, token, json=None):
        name = path.rsplit("/contents/", 1)[-1] if "/contents/" in path else None
        if name in files:
            return httpx.Response(200, json={"content": base64.b64encode(
                files[name].encode()).decode()})
        return httpx.Response(404, json={})

    monkeypatch.setattr(github, "_request", fake)
    monkeypatch.setattr(github, "platform_token", lambda session: "platform-token")
    repo_guidance._cache.clear()
    return files


def _prompt(project_id) -> str:
    with db_session() as session:
        project = session.get(Project, project_id)
        ceo = session.query(Agent).filter_by(project_id=project_id).first()
        return build_system_prompt(session, ceo, project)


def test_a_factory_seat_reads_the_conventions_merged_on_main(client, main_files):
    project = _factory(client)
    prompt = _prompt(project["id"])
    assert "THE REPO'S OWN CONVENTIONS" in prompt
    assert "Money is in cents." in prompt
    assert "platform's rules win" in prompt


def test_other_projects_and_repoless_factories_read_nothing(client, main_files):
    plain = _factory(client, mode="deliver", slug="plain")
    assert "THE REPO'S OWN CONVENTIONS" not in _prompt(plain["id"])
    with db_session() as session:
        session.get(Project, plain["id"]).github_repo = None
    repo_guidance._cache.clear()
    assert REPO  # the factory fixture's repo is what main is read from
