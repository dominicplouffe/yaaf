"""Invariant selftests: a Code Factory's home-page line counts the platform's
own rows — a merged change, a release the guard rolled back — and shows
nothing for a project that is not a factory."""

from __future__ import annotations

import pytest

from tests.conftest import ADMIN
from tests.selftest_invariants.test_merge_gate import FakeGitHub, _factory
from tests.selftest_invariants.test_releases import (
    _deployed,
    _propose,
    _shipped_change,
    _tick,
    _with_prod,
)
from yaaf.server import github, releases


@pytest.fixture
def gh(monkeypatch):
    fake = FakeGitHub()
    monkeypatch.setattr(github, "_request", fake)
    monkeypatch.setattr(github, "platform_token", lambda session: "platform-token")
    return fake


def _view(client, project):
    response = client.get(f"/observe/projects/{project['id']}/factory", headers=ADMIN)
    assert response.status_code == 200, response.text
    return response.json()


def test_the_line_counts_what_the_platform_recorded(client, gh, monkeypatch):
    monkeypatch.setattr(releases, "probe", lambda url: (False, "HTTP 503"))
    project = _factory(client, release_authority="autonomous", canary_max_failures=1)
    _with_prod(client, project)
    _shipped_change(client, project, gh)
    view = _view(client, project)
    assert [s["stage"] for s in view["rail"]] == [
        "Spec", "Build", "Check", "Review", "Release", "Run", "Maintain"]
    assert view["vitals"]["merged_week"] == 1 and view["vitals"]["merged_total"] == 1

    body = _propose(client, project, "0.1.0").json()
    _deployed(body["id"])
    _tick(project)  # the guard breaks and rolls back
    vitals = _view(client, project)["vitals"]
    assert vitals["deploys_month"] == 1 and vitals["change_failure_pct"] == 100
    run = next(s for s in _view(client, project)["rail"] if s["stage"] == "Run")
    assert run["state"] == "waiting" and "1 open incident" in run["detail"]


def test_a_project_that_is_not_a_factory_has_no_line(client, gh):
    project = _factory(client, mode="deliver", slug="plain")
    assert _view(client, project) == {"phase": None, "rail": [], "vitals": {}}
