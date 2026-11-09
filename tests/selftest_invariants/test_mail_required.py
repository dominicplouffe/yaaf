"""Invariant selftests: a project whose work runs through email does not run without it.

"Monitor your inbox; when an email comes in, check its attachment and reply
with the results" describes a project that has no job until it has a mailbox.
Such a project can be created at once — from the Founder, the form, a setup
file, MCP — and cannot start until somebody gives it an address, its servers
and its recipients in Settings → Email. The rules under attack:

1. Every door that sets it running refuses without its mailbox on: start,
   resume from a pause, reopen — over HTTP, from the CLI and from MCP.
2. Every place that says what a project still needs says this: the setup
   checklist (a required step, which cannot be skipped), the status line,
   the health card, the CEO's own context, the CLI after creating it.
3. The need is design and travels — a setup file, an export, a clone, the
   Founder's draft — while the mailbox never does, so a twin waits for its own.
4. A drill still starts it, without a mailbox, and says what that leaves out.
"""

from __future__ import annotations

import contextlib
import json

import pytest
from starlette.testclient import TestClient
from typer.testing import CliRunner

from tests.conftest import ADMIN, live_token, make_project
from tests.selftest_invariants.test_mail import ADDRESS, _mailbox, fake_wire
from yaaf.core.db import db_session
from yaaf.core.models import Agent, Project, ProjectStatus, Run

runner = CliRunner()


def _needs_mail(client, slug: str = "inbox-watch", **over) -> dict:
    return make_project(client, slug=slug, start=False, needs_mail=True, **over)


def _start(client, project: dict):
    return client.post(f"/admin/projects/{project['id']}/start", headers=ADMIN)


def _status(project: dict) -> str:
    with db_session() as session:
        return session.get(Project, project["id"]).status



@pytest.fixture
def mail(monkeypatch):
    return fake_wire(monkeypatch)


# ── 1. no start without it ──────────────────────────────────────────────────────


def test_it_is_created_at_once_and_refused_a_start_until_its_mailbox_is_on(client, mail):
    project = _needs_mail(client)
    refused = _start(client, project)
    assert refused.status_code == 409, refused.text
    assert "Settings → Email" in refused.json()["detail"]
    assert "no email address yet" in refused.json()["detail"]
    assert _status(project) == ProjectStatus.DRAFT

    _mailbox(client, project, enabled=False)
    off = _start(client, project)
    assert off.status_code == 409 and "switched off" in off.json()["detail"]

    _mailbox(client, project)
    assert _start(client, project).status_code == 200
    assert _status(project) == ProjectStatus.RUNNING


def test_a_project_that_does_not_need_email_never_waits_for_it(client, mail):
    project = make_project(client, slug="no-mail", start=False)
    assert _start(client, project).status_code == 200


def test_resume_and_reopen_are_starts_too(client, mail):
    project = _needs_mail(client)
    _mailbox(client, project)
    assert _start(client, project).status_code == 200
    client.post(f"/admin/projects/{project['id']}/pause", headers=ADMIN, json={"reason": "x"})
    _mailbox(client, project, enabled=False)
    resumed = _start(client, project)
    assert resumed.status_code == 409 and "switched off" in resumed.json()["detail"]

    with db_session() as session:
        session.get(Project, project["id"]).status = ProjectStatus.COMPLETED
    client.delete(f"/admin/projects/{project['id']}/mail", headers=ADMIN)
    reopened = client.post(f"/admin/projects/{project['id']}/reopen", headers=ADMIN)
    assert reopened.status_code == 409 and "no email address" in reopened.json()["detail"]


def test_mcp_creates_it_and_says_so_but_does_not_start_it(client):
    """Asked to create and start, a client gets a project created and NOT
    started — and is told so in the preview, before the confirm."""
    from tests.selftest_invariants.test_mcp_founding import GOOD_SPEC, call, founder

    make_project(client, slug="seed", start=False)
    token = founder(client)["token"]
    arguments = {"start": True, "spec": {**GOOD_SPEC, "slug": "via-mcp", "needs_mail": True}}
    failed, preview = call(client, token, "create_project", arguments)
    assert failed is False, preview
    about = json.dumps(preview, ensure_ascii=False)
    assert "Settings → Email" in about and "NOT started" in about
    failed, created = call(client, token, "create_project",
                           {**arguments, "confirm": preview["confirm"]})
    assert failed is False, created
    made = next(p for p in client.get("/observe/projects", headers=ADMIN).json()
                if p["slug"] == "via-mcp")
    assert made["status"] == "draft"


# ── 2. everywhere that says what it needs ───────────────────────────────────────


def test_the_checklist_holds_it_as_a_step_that_cannot_be_skipped(client, mail):
    plain = make_project(client, slug="plain", start=False)
    keys = [s["key"] for s in client.get(f"/observe/projects/{plain['id']}/setup",
                                         headers=ADMIN).json()["steps"]]
    assert "email" not in keys, "a project that does not need email is not asked for it"

    project = _needs_mail(client)
    steps = client.get(f"/observe/projects/{project['id']}/setup", headers=ADMIN).json()["steps"]
    [email] = [s for s in steps if s["key"] == "email"]
    assert email["required"] and email["state"] == "todo" and email["section"] == "mail"
    assert [s["key"] for s in steps][:2] == ["key", "email"]
    skipped = client.post(f"/admin/projects/{project['id']}/setup", headers=ADMIN,
                          json={"action": "skip", "step": "email"})
    assert skipped.status_code == 422 and "email" in skipped.text

    _mailbox(client, project)
    steps = client.get(f"/observe/projects/{project['id']}/setup", headers=ADMIN).json()["steps"]
    assert next(s for s in steps if s["key"] == "email")["state"] == "done"


def test_the_status_line_and_the_health_card_say_why(client, mail):
    # Paid for (the mock runtime pays nobody), so the one thing missing is email:
    # with no key as well, "nothing is set to pay for it" rightly comes first.
    project = _needs_mail(client, integrations={"runtime": "mock"})

    def row():
        return next(p for p in client.get("/observe/desk", headers=ADMIN).json()["projects"]
                    if p["id"] == project["id"])

    assert row()["mail_missing"] is True
    assert row()["status_line"]["headline"] == "Not started — it needs its email set up first."
    health = client.get(f"/observe/projects/{project['id']}/health", headers=ADMIN).json()
    [check] = [c for c in health["checks"] if c["key"] == "mail"]
    assert check["status"] == "fail" and check["where"] == "mail"
    dash = client.get(f"/observe/projects/{project['id']}", headers=ADMIN).json()
    assert dash["needs_mail"] is True and "Settings → Email" in dash["mail_start_refusal"]

    _mailbox(client, project)
    _start(client, project)
    _mailbox(client, project, enabled=False)  # off while it runs
    assert row()["status_line"]["verdict"] == "blocked"
    assert "email" in row()["status_line"]["headline"]


def test_the_ceo_is_told_its_mailbox_is_off_rather_than_left_waiting(client, mail):
    from yaaf.runner.daemon import _offer_mail
    from yaaf.runner.types import RunJob
    from yaaf.server.contextpack import build_context_pack

    project = _needs_mail(client)
    _mailbox(client, project)
    _start(client, project)
    _mailbox(client, project, enabled=False)
    run_id, _ = live_token(project["id"], project["ceo_agent_id"])
    with db_session() as session:
        pack = build_context_pack(session, session.get(Run, run_id),
                                  session.get(Agent, project["ceo_agent_id"]),
                                  session.get(Project, project["id"]))
        job = RunJob.__new__(RunJob)
        job.tool_permissions = {}
        _offer_mail(session.get(Project, project["id"]),
                    session.get(Agent, project["ceo_agent_id"]), job)
    assert pack["mail"]["status"] == "off" and "Tell the chairman" in pack["mail"]["note"]
    assert not job.tool_permissions.get("mail"), "no tool that could only be refused"


def test_the_cli_names_it_links_the_dashboard_and_does_not_start(client, monkeypatch, tmp_path,
                                                                 mail):
    from tests.conftest import seed_role
    from tests.selftest_invariants.test_providers import _credential, _id
    from yaaf.cli import main as cli

    as_admin = TestClient(client.app, headers=ADMIN)
    monkeypatch.setattr(cli, "_client", lambda: contextlib.nullcontext(as_admin))
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.delenv("YAAF_PUBLIC_BASE_URL", raising=False)
    monkeypatch.setenv("YAAF_API", "http://yaaf.test:24247")
    client.post("/admin/users", json={"email": "chair@example.com", "display_name": "Chair"},
                headers=ADMIN)
    seed_role(client, "ceo", is_ceo=True)
    _id(_credential(client, "Main key"))
    spec = tmp_path / "bundle.json"
    spec.write_text(json.dumps({"yaaf_bundle": 1, "roles": [], "project": {
        "slug": "mailroom", "name": "Mailroom", "goal": "Answer every email with a check.",
        "ceo_role_slug": "ceo", "billing_secret_name": "Main key", "needs_mail": True}}))

    result = runner.invoke(cli.app, ["create-project", "--from-file", str(spec), "--start"])

    assert result.exit_code == 0, result.output
    project = next(p for p in client.get("/observe/projects", headers=ADMIN).json()
                   if p["slug"] == "mailroom")
    link = f"http://yaaf.test:24247/app/projects/{project['id']}/settings?s=mail"
    assert link in result.output
    assert "It cannot start until that is done (email)" in result.output
    assert "The mailbox is set up in the dashboard" in result.output
    assert "Started." not in result.output and project["status"] == "draft"

    started = runner.invoke(cli.app, ["start", "mailroom"])
    assert started.exit_code == 1 and link in started.output


def test_the_founders_draft_shows_it_in_the_terminal():
    from yaaf.cli.main import _draft_lines

    lines = "\n".join(_draft_lines({"project": {"name": "M", "slug": "m", "goal": "g",
                                                "needs_mail": True}}))
    assert "Email: its work runs through email" in lines


# ── 3. the need travels, the mailbox does not ───────────────────────────────────


def test_the_need_travels_with_a_setup_file_and_a_clone_and_the_mailbox_does_not(client, mail):
    project = _needs_mail(client)
    _mailbox(client, project)
    exported = client.get(f"/admin/projects/{project['id']}/export", headers=ADMIN).json()
    assert exported["project"]["needs_mail"] is True
    assert ADDRESS not in json.dumps(exported)

    twin = client.post(f"/admin/projects/{project['id']}/clone", headers=ADMIN,
                       json={"slug": "inbox-twin"}).json()
    with db_session() as session:
        copied = session.get(Project, twin["id"])
        assert copied.needs_mail and "mail" not in (copied.integrations or {})
    assert client.post(f"/admin/projects/{twin['id']}/start", headers=ADMIN).status_code == 409


def test_the_founders_draft_carries_it_through_the_platforms_own_contract():
    from yaaf.founder.tools import as_bundle, summarise, validate

    bundle = as_bundle({"slug": "watch", "name": "Watch", "goal": "Answer every email sent in.",
                        "needs_mail": True})
    assert validate(bundle) == []
    assert summarise(bundle)["needs_mail"] is True


def test_it_can_be_turned_on_later_and_the_switch_is_audited(client, mail):
    project = make_project(client, slug="later", start=False)
    patched = client.patch(f"/admin/projects/{project['id']}", headers=ADMIN,
                           json={"needs_mail": True})
    assert patched.status_code == 200, patched.text
    assert _start(client, project).status_code == 409


# ── 4. a drill ──────────────────────────────────────────────────────────────────


def test_a_drill_starts_it_without_a_mailbox_and_says_what_that_leaves_out(client):
    """Deliberately a whole drill (about three seconds): the claim is that the
    drill STARTS a project the real start would refuse, which only running it
    can show."""
    from tests.conftest import seed_role

    seed_role(client, "ceo", is_ceo=True)
    seed_role(client, "worker")
    client.post("/admin/users", json={"email": "chair@example.com", "display_name": "Chair"},
                headers=ADMIN)
    response = client.post("/admin/rehearse", headers=ADMIN, json={"spec": {"project": {
        "slug": "drill", "name": "Drill", "chairman_email": "chair@example.com",
        "goal": "Answer every email that arrives with a grammar check of its attachment.",
        "mode": "explore", "ceo_role_slug": "ceo", "needs_mail": True,
        "teams": [{"role_slug": "worker", "name": "Desk", "employees": ["Ada"], "lead": "Ada"}],
    }}, "days": 3})
    assert response.status_code == 200, response.text
    report = response.json()
    walk = {step["key"]: step for step in report["walk"]}
    assert walk["created"]["ok"], walk["created"]["detail"]
    assert any("a drill sends and reads none" in line for line in report["not_exercised"])
