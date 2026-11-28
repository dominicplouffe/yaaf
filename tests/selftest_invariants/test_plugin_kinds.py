"""A project of a plugin kind, created and run through core (build step 5).

Over HTTP, as the chairman and the seats reach it: creating a project of a
plugin kind (offered on this box, an open mode, its fixed lead, fields its
plugin accepts) and every way that is refused before a row exists. Then the
hook points that only add to core's answers: the brief under core's heading,
the plan and completion gates, the setup steps, the project-home view. Then
export, import and clone carrying the kind. And the one property a plugin may
never break: it only tightens. Core's own refusals still fire on its project.

Each test installs the example plugin and starts a real host (~0.3 s).
"""

from __future__ import annotations

import base64
import json
import shutil
from pathlib import Path

import pytest
from sqlalchemy import func, select

from tests.conftest import ADMIN, agent_by_name, bearer, live_token, make_project, seed_role
from yaaf.core.config import settings
from yaaf.core.db import db_session
from yaaf.core.models import Agent, Project
from yaaf.plugins import host as H
from yaaf.plugins import primitives, registry

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "packages/yaaf_plugin_api/examples/grant-factory"
KIND = "plugin:grant_application"


@pytest.fixture
def box(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings(), "plugins_root", str(tmp_path / "installed"))
    monkeypatch.setattr(settings(), "allow_local_plugins", True)
    monkeypatch.setattr(settings(), "allow_unverified_plugins", True)
    src = tmp_path / "checkout"
    shutil.copytree(EXAMPLE, src, ignore=shutil.ignore_patterns("__pycache__"))
    version = client.post("/admin/plugins/local", json={"path": str(src)}, headers=ADMIN)
    assert version.status_code == 200, version.text
    seed_role(client, "grant-lead", is_ceo=True)
    yield version.json()
    registry.close_all()


def grant_project(client, slug="garden", **extra) -> dict:
    return make_project(client, slug=slug, kind=KIND, ceo_role_slug="grant-lead",
                        kind_fields={"funder": "Example Foundation"}, **extra)


def projects() -> int:
    with db_session() as s:
        return s.execute(select(func.count()).select_from(Project)).scalar_one()


# ── creating one ───────────────────────────────────────────────────────────────


def test_a_plugin_kind_project_is_created_pinned_and_behaving_as_its_shape(client, box):
    project = grant_project(client)
    with db_session() as s:
        row = s.get(Project, project["id"])
        assert (row.kind, row.kind_mode, row.plugin_version_id) == (KIND, "draft", box["id"])
        assert row.mode == "deliver"  # the mode's shape, so every built-in reader works
        assert row.kind_state == {"create": {"funder": "Example Foundation", "amount": 10000}}
        from yaaf.core.models import Role, RoleVersion

        ceo = s.get(Agent, project["ceo_agent_id"])
        role = s.get(Role, s.get(RoleVersion, ceo.role_version_id).role_id)
        assert role.slug == "grant-lead"  # the plugin's fixed lead, filled by the server


@pytest.mark.parametrize(("extra", "why"), [
    ({"kind_mode": "keep"}, "not a mode of"),
    ({"mode": "operate"}, "behaves as deliver"),
    ({"ceo_role_slug": "ceo"}, "led by its plugin's lead"),
    ({"kind_fields": {}}, "funder (Funder) is required"),
    ({"kind_fields": {"funder": "F", "amount": "lots"}}, "must be a number"),
    ({"kind_fields": {"funder": "F", "colour": "red"}}, "not a field of this kind"),
    ({"kind_fields": {"funder": "F", "amount": 0}}, "more than zero"),
    ({"factory_start": "new"}, "Code Factory"),
], ids=["unknown-mode", "other-mode", "other-lead", "missing-field", "wrong-type",
        "unknown-field", "plugin-refuses", "factory-start"])
def test_a_plugin_kind_project_is_refused_before_a_row_exists(client, box, extra, why):
    make_project(client, slug="seed")  # so the chair and roles exist
    before = projects()
    body = {"slug": "garden", "name": "Garden", "goal": "Win the community garden grant.",
            "chairman_email": "chair@example.com", "kind": KIND, "ceo_name": "Lead",
            "kind_fields": {"funder": "Example Foundation"},
            "teams": [{"role_slug": "worker", "name": "Alpha", "employees": ["A", "B"],
                       "lead": "A"}], **extra}
    r = client.post("/admin/projects", json=body, headers=ADMIN)
    assert r.status_code == 422 and why in r.text, r.text
    assert projects() == before


def test_a_kind_that_is_not_offered_or_not_a_plugins_is_refused(client, box, monkeypatch):
    make_project(client, slug="seed")
    base = {"slug": "garden", "name": "Garden", "goal": "Win the community garden grant.",
            "chairman_email": "chair@example.com", "ceo_name": "Lead",
            "teams": [{"role_slug": "worker", "name": "Alpha", "employees": ["A", "B"],
                       "lead": "A"}]}
    for extra, why in (({"kind": "plugin:nothing_here"}, "not offered on this box"),
                       ({"kind": "deliver"}, "not a plugin kind"),
                       ({"kind_mode": "draft"}, "belong to a plugin kind")):
        r = client.post("/admin/projects", json={**base, **extra}, headers=ADMIN)
        assert r.status_code == 422 and why in r.text, (extra, r.text)
    monkeypatch.setattr(settings(), "allow_unverified_plugins", False)
    r = client.post("/admin/projects", json={**base, "kind": KIND, "kind_fields": {
        "funder": "F"}, "ceo_role_slug": "grant-lead"}, headers=ADMIN)
    assert r.status_code == 422 and "not offered on this box" in r.text


def test_a_plugin_that_cannot_be_asked_refuses_the_creation(client, box, monkeypatch):
    make_project(client, slug="seed")
    dead = H.HostPool(H.HostSpec.from_checkout(EXAMPLE),
                      H.BrokerLauncher(("127.0.0.1", 1), b"k" * 32))
    monkeypatch.setattr(registry, "pool_for", lambda version: dead)
    before = projects()
    r = client.post("/admin/projects", json={
        "slug": "garden", "name": "Garden", "goal": "Win the community garden grant.",
        "chairman_email": "chair@example.com", "ceo_name": "Lead", "kind": KIND,
        "ceo_role_slug": "grant-lead", "kind_fields": {"funder": "F"},
        "teams": [{"role_slug": "worker", "name": "A", "employees": ["A", "B"], "lead": "A"}]},
        headers=ADMIN)
    assert r.status_code == 422 and "could not be consulted" in r.text
    assert projects() == before


# ── the hook points ────────────────────────────────────────────────────────────


def test_every_seat_reads_the_plugins_brief_under_cores_heading(client, box):
    project = grant_project(client)
    plain = make_project(client, slug="plain")
    from yaaf.server.contextpack import build_system_prompt

    with db_session() as s:
        row = s.get(Project, project["id"])
        prompt = build_system_prompt(s, s.get(Agent, project["ceo_agent_id"]), row)
        assert "THIS PROJECT'S KIND: Grant Application Factory (plugin grant-factory@" in prompt
        assert "Every budget line names the rule of the call it meets" in prompt
        other = build_system_prompt(s, s.get(Agent, plain["ceo_agent_id"]),
                                    s.get(Project, plain["id"]))
        assert "THIS PROJECT'S KIND" not in other


def test_the_plugins_plan_and_completion_gates_add_to_cores(client, box):
    from yaaf.server import policy

    project = grant_project(client)
    with db_session() as s:
        row = s.get(Project, project["id"])
        missing = policy.evaluate_plan(s, row, policy.effective(s, row.id))
        assert any("read the funder's call first" in m for m in missing)
        owed = policy.delivery_outstanding(s, row)
        assert any("no application.json has been accepted yet" in o for o in owed)
        assert any("no passing independent review" in o for o in owed)
        primitives.store_platform(s, row, None, "calls.json", b"{}", role="output")
    with db_session() as s:
        row = s.get(Project, project["id"])
        missing = policy.evaluate_plan(s, row, policy.effective(s, row.id))
        assert not any("read the funder's call first" in m for m in missing)


def test_a_plugin_that_is_down_holds_the_plan_and_completion(client, box, monkeypatch):
    from yaaf.server import policy

    project = grant_project(client)
    dead = H.HostPool(H.HostSpec.from_checkout(EXAMPLE),
                      H.BrokerLauncher(("127.0.0.1", 1), b"k" * 32))
    monkeypatch.setattr(registry, "pool_for", lambda version: dead)
    with db_session() as s:
        row = s.get(Project, project["id"])
        assert H.UNAVAILABLE in policy.evaluate_plan(s, row, policy.effective(s, row.id))
        assert H.UNAVAILABLE in policy.delivery_outstanding(s, row)


def test_the_checklist_shows_the_plugins_steps(client, box):
    project = grant_project(client)
    steps = client.get(f"/observe/projects/{project['id']}/setup", headers=ADMIN).json()
    flat = json.dumps(steps)
    assert "plugin:funder_source" in flat
    assert "Connect the data source that holds the funder's calls." in flat


def test_the_project_home_draws_the_plugins_view(client, box):
    project = grant_project(client)
    dash = client.get(f"/observe/projects/{project['id']}", headers=ADMIN).json()
    assert dash["kind"] == KIND and dash["kind_mode"] == "draft"
    view = dash["kind_view"]
    assert view["plugin"] == "grant-factory" and view["error"] is None
    assert [s["id"] for s in view["doc"]["sections"]] == ["state", "ask", "draft"]
    assert view["data"]["state"] == {"state": "info", "text": "Not drafted yet"}
    plain = make_project(client, slug="plain")
    assert client.get(f"/observe/projects/{plain['id']}", headers=ADMIN).json()[
        "kind_view"] is None


# ── export, import, clone ──────────────────────────────────────────────────────


def test_export_import_and_clone_carry_the_kind(client, box):
    project = grant_project(client)
    exported = client.get(f"/admin/projects/{project['id']}/export", headers=ADMIN).json()
    spec = exported["project"]
    assert (spec["kind"], spec["kind_mode"]) == (KIND, "draft")
    assert spec["kind_fields"] == {"funder": "Example Foundation", "amount": 10000}
    imported = client.post("/admin/projects", headers=ADMIN, json={**spec, "slug": "again"})
    assert imported.status_code == 200, imported.text
    clone = client.post(f"/admin/projects/{project['id']}/clone", headers=ADMIN,
                        json={"slug": "twin"})
    assert clone.status_code == 200, clone.text
    with db_session() as s:
        for pid in (imported.json()["id"], clone.json()["id"]):
            row = s.get(Project, pid)
            assert (row.kind, row.kind_mode, row.plugin_version_id) == (KIND, "draft",
                                                                        box["id"])
    url = f"/admin/plugins/grant-factory/versions/{box['version']}/disable"
    assert client.post(url, headers=ADMIN).status_code == 200
    r = client.post(f"/admin/projects/{project['id']}/clone", headers=ADMIN,
                    json={"slug": "twin-two"})
    assert r.status_code == 409 and "may no longer be chosen" in r.text


# ── a plugin only tightens ─────────────────────────────────────────────────────


def test_cores_refusals_still_fire_on_a_plugin_projects_gated_artifact(client, box):
    """MIN-14, first slice: the plugin would accept this application, but core's
    own rules are asked first and still say no. The conformance suite (step 6)
    replays core's wider attacks against every plugin's project."""
    project = grant_project(client)
    token = bearer(live_token(project["id"], agent_by_name(project, "Lead"))[1])
    good = base64.b64encode(json.dumps(
        {"title": "Garden", "budget": [{"rule": "3.1", "amount": 5}]}).encode()).decode()
    cases = [
        ({"filename": "../application.json", "content_base64": good}, 422, "plain file name"),
        ({"filename": "application.json", "content_base64": "%%not base64%%"}, 422, "base64"),
        ({"filename": "application.json", "content_base64": good, "satisfies": ["R-99"]},
         404, "not a requirement of this project"),
    ]
    for body, status, why in cases:
        r = client.post("/agent/artifacts", headers=token, json=body)
        assert r.status_code == status and why in r.text, (body["filename"], r.text)
    assert client.post("/agent/artifacts", headers=token, json={
        "filename": "application.json", "content_base64": good}).status_code == 200
