"""Invariant selftests: a deliverable says how its claims were checked.

A file published "satisfying R-02" carried only that — the author's claim,
made at publish. Whether anything checked R-02 lived on another page. The
receipt now carries the platform's own word, and the rule under attack is the
one that keeps it honest: a judgement is never shown as a verification.
"""

from __future__ import annotations

import base64

from tests.conftest import ADMIN, bearer, live_token
from tests.selftest_invariants.test_requirements import (
    CMD_TEST,
    OBS_TEST,
    accept,
    ceo_token,
    deliver_project,
    propose,
)
from yaaf.core.db import db_session
from yaaf.core.models import Requirement, RequirementStatus


def _publish(client, project: dict, name: str, key: str) -> None:
    _, token = live_token(project["id"], project["ceo_agent_id"])
    response = client.post("/agent/artifacts", headers=bearer(token), json={
        "filename": name, "content_base64": base64.b64encode(b"the answer").decode(),
        "satisfies": [key]})
    assert response.status_code == 200, response.text


def _receipt(client, project: dict, name: str) -> dict:
    files = client.get(f"/observe/projects/{project['id']}/files", headers=ADMIN).json()
    return next(r for r in files["published"] if r["name"] == name)


def _settle(requirement_id: str) -> None:
    # Settling is the platform's verb and is attacked elsewhere; here only the
    # label read off a settled requirement is under test.
    with db_session() as session:
        session.get(Requirement, requirement_id).status = RequirementStatus.MET


def test_an_unsettled_claim_reads_not_yet_checked(client):
    project = deliver_project(client)
    req = propose(client, ceo_token(project), test=CMD_TEST)
    accept(client, req["requirement_id"], project)
    _publish(client, project, "report.md", req["key"])
    receipt = _receipt(client, project, "report.md")
    assert receipt["checked"] == "pending"
    assert req["key"] in receipt["checked_why"]


def test_a_test_the_platform_ran_reads_verified(client):
    project = deliver_project(client)
    req = propose(client, ceo_token(project), test=CMD_TEST)
    accept(client, req["requirement_id"], project)
    _publish(client, project, "report.md", req["key"])
    _settle(req["requirement_id"])
    receipt = _receipt(client, project, "report.md")
    assert receipt["checked"] == "verified"
    assert "the platform ran the acceptance test" in receipt["checked_why"]


def test_a_judgement_is_never_shown_as_a_verification(client):
    project = deliver_project(client)
    req = propose(client, ceo_token(project), test=OBS_TEST)
    accept(client, req["requirement_id"], project)
    _publish(client, project, "deck.md", req["key"])
    _settle(req["requirement_id"])
    assert _receipt(client, project, "deck.md")["checked"] == "judged"


def test_a_file_that_claims_nothing_carries_no_label(client):
    project = deliver_project(client)
    _, token = live_token(project["id"], project["ceo_agent_id"])
    client.post("/agent/artifacts", headers=bearer(token), json={
        "filename": "notes.md", "content_base64": base64.b64encode(b"x").decode()})
    assert _receipt(client, project, "notes.md")["checked"] is None
