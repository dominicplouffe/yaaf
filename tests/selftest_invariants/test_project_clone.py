"""Invariant selftests: a clone is a *running* twin, not a design.

An export deliberately leaves every secret behind — it may travel to an
install whose master key cannot read them. A clone never leaves this one, so
the promise is the opposite and much stronger: the twin runs the same project
on the same credentials, pinned to the same role versions, with its data
sources proven before anyone looks at it. What it must NOT inherit is the
source's history or its claim on shared outside rooms."""

from __future__ import annotations

import pytest

from tests.conftest import ADMIN, make_project, seed_role
from yaaf.core.crypto import decrypt
from yaaf.core.db import db_session
from yaaf.core.models import (
    Agent,
    AgentKind,
    Channel,
    Notice,
    NoticeKind,
    Project,
    Secret,
    Team,
)
from yaaf.server.api import admin as admin_module


@pytest.fixture(autouse=True)
def offline_check(monkeypatch):
    """Reachability checks never leave the test box."""
    monkeypatch.setattr(admin_module, "_check_data_source",
                        lambda url, bearer: {"ok": True, "detail": f"reachable ({url})"})


def _clone(client, project_id: str, slug: str = "acme-twin", **body):
    return client.post(f"/admin/projects/{project_id}/clone",
                       json={"slug": slug, **body}, headers=ADMIN)


def test_credentials_come_across_with_their_values(client):
    """The whole reason this exists: no credential re-entry, so no chance to
    mistype one — including a data source address, which is a secret whose
    shape decides whether any tools mount at all."""
    project = make_project(client, start=False)
    client.post("/admin/secrets", json={
        "project_id": project["id"], "name": "wordpress",
        "value": "wp-secret-value", "role_scopes": ["worker"], "kind": "service",
    }, headers=ADMIN)
    client.post('/admin/settings/llm-credentials', json={'name': 'anthropic-api-key', 'value': 'sk-ant-not-real', 'mode': 'api_key'}, headers=ADMIN)
    client.post(f"/admin/projects/{project['id']}/mcp_servers", json={
        "name": "ppc", "url": "https://data.example.com:3845/mcp",
        "roles": ["worker"], "bearer": "tok-123",
    }, headers=ADMIN)

    cloned = _clone(client, project["id"])
    assert cloned.status_code == 200, cloned.text
    twin = cloned.json()
    assert twin["copied"]["secrets"] == 3

    with db_session() as session:
        rows = {s.name: s for s in session.query(Secret)
                .filter_by(project_id=twin["id"]).all()}
        assert set(rows) == {"wordpress", "mcp-ppc-url", "mcp-ppc-token"}
        # Values, not just names — and the grants that make them reachable.
        assert decrypt(rows["wordpress"].ciphertext) == "wp-secret-value"
        assert decrypt(rows["mcp-ppc-url"].ciphertext) == "https://data.example.com:3845/mcp"
        assert rows["mcp-ppc-url"].role_scopes == ["worker"]
        # Separate rows for the project's own secrets, so rotating one twin's
        # service key never touches the other. The LLM credential is the
        # exception by design: shared and assigned, not copied.
        source_ids = {s.id for s in session.query(Secret)
                      .filter_by(project_id=project["id"]).all()}
        assert source_ids.isdisjoint({s.id for s in rows.values()})

    # The twin's data source answered on the twin's own copy of the address —
    # a fresh verdict, not the source's stale one repeated about another project.
    assert [s["name"] for s in twin["copied"]["data_sources"]] == ["ppc"]
    check = twin["copied"]["data_sources"][0]["check"]
    assert check["ok"] and check["detail"] == "reachable (https://data.example.com:3845/mcp)"
    with db_session() as session:
        source_entry = session.get(Project, project["id"]).integrations["mcp_servers"][0]
        twin_entry = session.get(Project, twin["id"]).integrations["mcp_servers"][0]
        assert twin_entry["last_check"]["at"] > source_entry["last_check"]["at"]


def test_twin_pins_the_versions_the_source_runs(client):
    """A twin that silently upgrades its roles is not running the same project
    — the failure that makes A/B reruns meaningless."""
    project = make_project(client, start=False)
    with db_session() as session:
        source_pins = {t.name: t.role_version_id for t in session.query(Team)
                       .filter_by(project_id=project["id"]).all()}

    # The library moves on between the source's creation and the clone.
    seed_role(client, "worker", system_prompt="A rewritten worker prompt.")

    twin = _clone(client, project["id"]).json()
    with db_session() as session:
        twin_pins = {t.name: t.role_version_id for t in session.query(Team)
                     .filter_by(project_id=twin["id"]).all()}
        assert twin_pins == source_pins

        source_ceo = session.query(Agent).filter_by(
            project_id=project["id"], kind=AgentKind.CEO).one()
        twin_ceo = session.query(Agent).filter_by(
            project_id=twin["id"], kind=AgentKind.CEO).one()
        assert twin_ceo.role_version_id == source_ceo.role_version_id
        assert twin_ceo.name == source_ceo.name


def test_the_people_keep_their_settings(client):
    """A twin's people are the same people: chairman-set daily wakes, model
    overrides and pause state come across. Dropping daily_at here silently
    cancelled every standup the chairman had set — a project that looks
    identical and never checks its inbox at 09:00."""
    from tests.conftest import agent_by_name
    from yaaf.core.models import AgentStatus

    project = make_project(client, start=False)
    for agent_id, at in ((project["ceo_agent_id"], "08:30"),
                         (agent_by_name(project, "Emp2"), "17:45")):
        set_wake = client.patch(f"/admin/agents/{agent_id}/schedule",
                                json={"daily_at": at}, headers=ADMIN)
        assert set_wake.status_code == 200, set_wake.text
    with db_session() as session:
        emp = session.get(Agent, agent_by_name(project, "Emp2"))
        emp.model_override = "claude-haiku-4-5-20251001"
        emp.status = AgentStatus.PAUSED

    twin = _clone(client, project["id"]).json()
    with db_session() as session:
        twin_ceo = session.query(Agent).filter_by(project_id=twin["id"],
                                                  kind=AgentKind.CEO).one()
        assert twin_ceo.daily_at == "08:30"
        twin_emp = session.query(Agent).filter_by(project_id=twin["id"],
                                                  name="Emp2").one()
        assert twin_emp.daily_at == "17:45"
        assert twin_emp.model_override == "claude-haiku-4-5-20251001"
        assert twin_emp.status == AgentStatus.PAUSED


def test_the_org_is_rebuilt_whole_and_empty(client):
    """Same people and rooms, governed by the same rules; none of the source's
    history and none of its amendment argument."""
    project = make_project(client, constitution={"queue_ceiling": 5}, start=False)
    twin = _clone(client, project["id"]).json()

    assert [t["name"] for t in twin["teams"]] == ["Alpha"]
    assert [a["name"] for a in twin["teams"][0]["agents"]] == ["Lead", "Emp2"]
    assert twin["status"] == "draft"  # never starts itself

    dash = client.get(f"/observe/projects/{twin['id']}", headers=ADMIN).json()
    assert dash["constitution"]["queue_ceiling"] == 5  # the rules in force, carried
    assert dash["slug"] == "acme-twin"
    assert dash["runs"] == {"running": 0, "queued": 0} and dash["open_tasks"] == 0

    with db_session() as session:
        team = session.query(Team).filter_by(project_id=twin["id"]).one()
        lead = session.query(Agent).filter_by(project_id=twin["id"], name="Lead").one()
        assert team.lead_agent_id == lead.id  # resolved fresh, not a copied id
        # Its own rooms: boardroom, leadership, one team room.
        assert session.query(Channel).filter_by(project_id=twin["id"]).count() == 3


def test_slack_tokens_travel_but_rooms_do_not(client):
    """Two projects mirroring into one room is a mess nobody asked for — but
    re-pasting the tokens to avoid it is a chore nobody asked for either."""
    project = make_project(client, start=False)
    client.patch(f"/admin/projects/{project['id']}", json={
        "integrations": {
            "slack": {"bot_token_secret": "slack-bot",
                      "signing_secret": "slack-signing",
                      "chairman_slack_user": "U123",
                      "channel_map": {"boardroom": "C0BQ", "leadership": "C0BR"}},
            "webhook_secret": "whsec-not-portable"},
    }, headers=ADMIN)
    client.post("/admin/secrets", json={
        "project_id": project["id"], "name": "slack-bot", "value": "xoxb-token",
    }, headers=ADMIN)

    twin = _clone(client, project["id"]).json()
    assert twin["copied"]["slack_rooms_dropped"] is True

    with db_session() as session:
        wiring = session.get(Project, twin["id"]).integrations
        assert wiring["slack"]["channel_map"] == {}
        assert wiring["slack"]["bot_token_secret"] == "slack-bot"  # still wired
        assert wiring["slack"]["chairman_slack_user"] == "U123"
        # Its own signing key, never the source's — sharing one would let either
        # project's senders attest evidence for the other. Absent is not the
        # answer though: see test_a_twin_can_attest_inbound_evidence.
        assert wiring["webhook_secret"] != "whsec-not-portable"
        token = session.query(Secret).filter_by(project_id=twin["id"],
                                                name="slack-bot").one()
        assert decrypt(token.ciphertext) == "xoxb-token"  # no re-pasting


def test_a_twin_is_the_same_SHAPE_of_project(client):
    """Mode decides whether a project has requirements at all, and it is frozen
    at creation — so a twin that comes back in the wrong one cannot be
    corrected, only deleted and rebuilt. The clone left mode out of the
    constructor and the model default is Explore, so every twin of a Deliver
    project silently became an Explore project with no Plan tab and nowhere to
    put its scope. Seen live: a Deliver project twinned, and its plan was gone.
    """
    from datetime import timedelta

    from yaaf.core.models import ProjectMode, utcnow

    source = make_project(client, slug="deliverer", mode="deliver")
    twin = _clone(client, source["id"], slug="deliverer-twin")
    assert twin.status_code == 200, twin.text
    assert twin.json()["mode"] == "deliver"
    with db_session() as session:
        assert session.get(Project, twin.json()["id"]).mode == ProjectMode.DELIVER

    # An Explore window that is still open comes across as it is...
    future = utcnow() + timedelta(days=30)
    explorer = make_project(client, slug="explorer", mode="explore")
    with db_session() as session:
        session.get(Project, explorer["id"]).deadline = future
    live = _clone(client, explorer["id"], slug="explorer-twin")
    assert live.json()["copied"]["deadline_dropped"] is False
    with db_session() as session:
        assert session.get(Project, live.json()["id"]).deadline is not None

    # ...but a spent one is not inherited: a deadline in the past would have
    # the janitor close the window on a project that never opened it.
    with db_session() as session:
        session.get(Project, explorer["id"]).deadline = utcnow() - timedelta(days=1)
    stale = _clone(client, explorer["id"], slug="explorer-twin-2")
    assert stale.json()["copied"]["deadline_dropped"] is True
    with db_session() as session:
        twin_row = session.get(Project, stale.json()["id"])
        assert twin_row.deadline is None and twin_row.mode == ProjectMode.EXPLORE


def test_a_taken_slug_is_refused_before_anything_is_written(client):
    project = make_project(client, start=False)
    refused = _clone(client, project["id"], slug="acme")  # the source's own slug
    assert refused.status_code == 409
    with db_session() as session:
        assert session.query(Project).count() == 1
        assert session.query(Secret).filter_by(project_id=project["id"]).count() == 0


def test_the_twin_can_be_aimed_somewhere_new(client):
    """Reusing the machinery is the point; reusing the mandate usually isn't.
    A twin stuck on its source's goal would make the whole feature pointless."""
    project = make_project(client, start=False)
    twin = _clone(client, project["id"], goal="Audit the 2027 catalogue instead.").json()

    with db_session() as session:
        assert session.get(Project, twin["id"]).goal == "Audit the 2027 catalogue instead."
        assert session.get(Project, project["id"]).goal != "Audit the 2027 catalogue instead."

    # Omitting it keeps the source's mandate — duplicating to run the same
    # project again is just as valid a reason to clone.
    same = _clone(client, project["id"], slug="acme-third").json()
    with db_session() as session:
        assert session.get(Project, same["id"]).goal == session.get(
            Project, project["id"]).goal

    # A goal too short to mean anything is refused, as it is at creation.
    assert _clone(client, project["id"], slug="acme-4th", goal="nope").status_code == 422


def test_a_changed_goal_reaches_the_ceo(client):
    """Agents read the goal live, so the change lands on its own — but landing
    silently is how a CEO ends up defending a plan built for the old one."""
    project = make_project(client, start=False)
    patched = client.patch(f"/admin/projects/{project['id']}", json={
        "goal": "Pivot: win the enterprise segment by Q3.",
    }, headers=ADMIN)
    assert patched.status_code == 200, patched.text
    assert patched.json()["changed"]["goal"] == "Pivot: win the enterprise segment by Q3."

    with db_session() as session:
        assert session.get(Project, project["id"]).goal == "Pivot: win the enterprise segment by Q3."
        notice = session.query(Notice).filter_by(project_id=project["id"],
                                                 kind=NoticeKind.GOAL_CHANGED).one()
        assert notice.agent_id == project["ceo_agent_id"]
        assert "Pivot: win the enterprise segment by Q3." in notice.body
        assert "Prove the framework works end to end." in notice.body  # what it was

    # Saving the same text again is not a change, and must not nag the CEO.
    client.patch(f"/admin/projects/{project['id']}",
                 json={"goal": "Pivot: win the enterprise segment by Q3."}, headers=ADMIN)
    with db_session() as session:
        assert session.query(Notice).filter_by(project_id=project["id"],
                                               kind=NoticeKind.GOAL_CHANGED).count() == 1


def test_solo_twin_keeps_the_ceo_inside_its_team(client):
    project = make_project(
        client, slug="solo-src", solo=True,
        teams=[{"role_slug": "worker", "name": "Solo", "employees": []}], start=False,
    )
    twin = _clone(client, project["id"], slug="solo-twin").json()
    with db_session() as session:
        team = session.query(Team).filter_by(project_id=twin["id"]).one()
        ceo = session.query(Agent).filter_by(project_id=twin["id"],
                                             kind=AgentKind.CEO).one()
        assert ceo.team_id == team.id
    assert [a["name"] for a in twin["teams"][0]["agents"]] == ["CEO"]


def test_a_twin_records_the_shape_it_actually_has(client):
    """Topology is stored rather than inferred precisely so the org shape stays
    a recorded fact that cannot drift. The clone left it out of the constructor
    and the model default is 'ceo_leads_workers', so every solo twin came back
    claiming a hierarchy of one — the same omission that turned Deliver twins
    into Explore ones.

    The twin rebuilds the source's teams and people exactly, so the source's
    recorded shape is true of the twin by construction. All three shapes, since
    only the third one happened to match the default that was masking the bug.
    """
    solo = make_project(
        client, slug="solo-src", solo=True,
        teams=[{"role_slug": "worker", "name": "Solo", "employees": []}], start=False,
    )
    flat = make_project(
        client, slug="flat-src", start=False,
        teams=[{"role_slug": "worker", "name": "Alpha", "employees": ["One"]},
               {"role_slug": "worker", "name": "Beta", "employees": ["Two"]}],
    )
    deep = make_project(client, slug="deep-src", start=False)  # default: a real lead

    with db_session() as session:
        assert [session.get(Project, p["id"]).topology for p in (solo, flat, deep)] \
            == ["solo", "ceo_team", "ceo_leads_workers"]

    for source, slug in ((solo, "solo-twin"), (flat, "flat-twin"), (deep, "deep-twin")):
        twin = _clone(client, source["id"], slug=slug)
        assert twin.status_code == 200, twin.text
        with db_session() as session:
            assert session.get(Project, twin.json()["id"]).topology == \
                session.get(Project, source["id"]).topology


def test_a_twin_can_attest_inbound_evidence(client):
    """Not inheriting the source's signing keys is right — one key across two
    projects would let either one's senders attest evidence for the other. But
    the clone dropped them without minting replacements, and github_repo and the
    controlled domains *do* come across: the twin looked wired to GitHub while
    no inbound event could ever be attested, which is the whole point of the
    connector. Every other birth mints these; a twin is no less born."""
    source = make_project(client, start=False, controlled_domains=["github:acme-co"])
    provisioned = client.post(f"/admin/projects/{source['id']}/webhook_secrets",
                              headers=ADMIN).json()

    twin = _clone(client, source["id"]).json()
    with db_session() as session:
        wiring = session.get(Project, twin["id"]).integrations or {}
        assert wiring["github_webhook_secret"] and wiring["webhook_secret"]
        # Its own, not the source's — that is what "not inherited" has to mean.
        assert wiring["github_webhook_secret"] != provisioned["github_webhook_secret"]
        assert wiring["webhook_secret"] != provisioned["webhook_secret"]

    # And the twin is born ready rather than born with a finding against it.
    health = client.get(f"/observe/projects/{twin['id']}/health", headers=ADMIN).json()
    assert not [c for c in health["checks"] if c["key"] == "connectors"], health["checks"]
