"""Health-card advice a reader has read can be put away; a problem cannot.

The header badge sat lit on every project because most of what lit it was
standing advice — read once, true forever, never going away. Putting one away
has to hold three lines: only advice, never something broken; only for the
person who did it; and only while it is the same finding, so a different seat
going quiet, or one coming back after it cleared, is news again.
"""

from __future__ import annotations

from datetime import timedelta

from tests.conftest import ADMIN, agent_by_name, headers_of, make_project
from tests.selftest_invariants.test_auth_roles import seat
from tests.selftest_invariants.test_dormant_seats import _age_seats, _ran
from yaaf.core.db import db_session
from yaaf.core.models import Project, Run, utcnow


def _checks(client, project: dict, headers=ADMIN) -> dict[str, dict]:
    health = client.get(f"/observe/projects/{project['id']}/health", headers=headers)
    assert health.status_code == 200, health.text
    return {c["key"]: c for c in health.json()["checks"]}


def _ack(client, project: dict, sig: str, ack: bool = True, headers=ADMIN):
    return client.post(f"/admin/projects/{project['id']}/health/ack",
                       json={"sig": sig, "ack": ack}, headers=headers)


def _workers(project: dict) -> list[str]:
    return [a["id"] for t in project["teams"] for a in t["agents"]]


def test_advice_put_away_stays_away_while_only_its_numbers_change(client):
    project = make_project(client)
    _age_seats(project, 20)
    lead = agent_by_name(project, "Lead")
    for agent_id in [project["ceo_agent_id"], *_workers(project)]:
        _ran(project, agent_id, 9 if agent_id == lead else 1)

    finding = _checks(client, project)["dormant-seats"]
    assert "Lead (9 days)" in finding["detail"]
    assert finding["status"] == "warn" and finding["acked_at"] is None
    assert _ack(client, project, finding["sig"]).status_code == 200
    assert _checks(client, project)["dormant-seats"]["acked_at"]

    # A day later the same seat is still quiet. "9 days" reads "10 days", and
    # the reader has already said they know.
    with db_session() as session:
        session.query(Run).filter(Run.agent_id == lead).one().created_at = (
            utcnow() - timedelta(days=10))
    later = _checks(client, project)["dormant-seats"]
    assert "Lead (10 days)" in later["detail"]
    assert later["sig"] == finding["sig"] and later["acked_at"]

    # A different seat going quiet is a different finding, and it is news.
    other = next(a for a in _workers(project) if a != lead)
    with db_session() as session:
        session.query(Run).filter(Run.agent_id == other).one().created_at = (
            utcnow() - timedelta(days=8))
    wider = _checks(client, project)["dormant-seats"]
    assert wider["sig"] != finding["sig"] and wider["acked_at"] is None

    # And one put away can be brought back.
    assert _ack(client, project, wider["sig"]).status_code == 200
    assert _ack(client, project, wider["sig"], ack=False).status_code == 200
    assert _checks(client, project)["dormant-seats"]["acked_at"] is None


def test_a_problem_cannot_be_put_away(client):
    """Otherwise the badge reads "healthy" over a project that cannot run."""
    project = make_project(client)
    with db_session() as session:
        session.get(Project, project["id"]).billing_secret_id = None
    billing = _checks(client, project)["billing"]
    assert billing["status"] == "fail"
    refused = _ack(client, project, billing["sig"])
    assert refused.status_code == 409, refused.text
    assert "until it is fixed" in refused.json()["detail"]
    assert _checks(client, project)["billing"]["acked_at"] is None


def test_putting_advice_away_is_one_readers_business(client):
    """A co-chair tidying their card does not tidy the chairman's."""
    project = make_project(client)
    _age_seats(project, 20)
    co = headers_of(seat(client, project, "co@example.com", "co_chair"))
    finding = _checks(client, project)["dormant-seats"]

    assert _ack(client, project, finding["sig"], headers=co).status_code == 200
    assert _checks(client, project, headers=co)["dormant-seats"]["acked_at"]
    assert _checks(client, project)["dormant-seats"]["acked_at"] is None


def test_a_finding_that_is_gone_cannot_be_put_away(client):
    project = make_project(client)
    refused = _ack(client, project, "0123456789ab")
    assert refused.status_code == 404, refused.text
