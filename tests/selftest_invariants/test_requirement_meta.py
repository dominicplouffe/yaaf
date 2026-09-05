"""Invariant selftests: a requirement's owner-facing copy.

The statement is written for the platform. The person who hired the project wants
a title, one line of what they get, when it lands and where — and none of that is
something the checker reads, so it rides in its own column and comes back whole
on the plan view. Bounded, because it is rendered to a person.
"""

from __future__ import annotations

from tests.conftest import ADMIN, bearer
from tests.selftest_invariants.test_requirements import ceo_token, deliver_project, propose

META = {"title": "Wasted-spend list",
        "description": "every search term with spend and no sales in the last 30 days",
        "cadence": None, "due": "2026-09-07", "lands": "table"}


def _plan_rows(client, project: dict) -> list[dict]:
    plan = client.get(f"/observe/projects/{project['id']}/plan", headers=ADMIN).json()
    return plan["requirements"]


def test_meta_round_trips_to_the_plan_view_with_recurrence_and_cycle(client):
    project = deliver_project(client, slug="meta")
    proposed = propose(client, ceo_token(project), meta=META)

    row = next(r for r in _plan_rows(client, project) if r["id"] == proposed["requirement_id"])
    assert row["meta"] == META
    assert row["recurrence"] == "terminal"
    assert "cycle_id" in row


def test_a_requirement_without_meta_lists_none(client):
    project = deliver_project(client, slug="nometa")
    proposed = propose(client, ceo_token(project))
    row = next(r for r in _plan_rows(client, project) if r["id"] == proposed["requirement_id"])
    assert row["meta"] is None


def test_meta_is_bounded_and_closed(client):
    project = deliver_project(client, slug="bounds")
    token = ceo_token(project)
    for bad in (
        {**META, "title": "x" * 121},
        {**META, "lands": "spreadsheet"},
        {**META, "cadence": "hourly"},
        {**META, "acceptance": "sneaked in"},
        {"description": "no title"},
    ):
        response = client.post(
            "/agent/requirements",
            json={"statement": "A report exists, sourced and dated",
                  "acceptance_test": {"type": "command", "command": "test -f done.txt"},
                  "est_runs": 5, "meta": bad},
            headers=bearer(token))
        assert response.status_code == 422, bad
