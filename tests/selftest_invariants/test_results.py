"""Invariant selftests: Results says who made a result and who checked it.

The Results page lists what the team published, one row per deliverable. The
rules under attack: the check it names is what happened, never what was
claimed (a judgement is not a verification, an unsettled claim checked
nothing), the maker is the agent that published it, one row stands for every
version of a name, and the page is one person's when asked for one person.
"""

from __future__ import annotations

import base64

from tests.conftest import ADMIN, bearer, live_token, make_project
from tests.selftest_invariants.test_deliverable_proof import _settle
from tests.selftest_invariants.test_requirements import (
    CMD_TEST,
    OBS_TEST,
    accept,
    ceo_token,
    deliver_project,
    propose,
)


def _publish(client, project: dict, name: str, body: bytes = b"the answer",
             satisfies: list[str] | None = None) -> None:
    _, token = live_token(project["id"], project["ceo_agent_id"])
    response = client.post("/agent/artifacts", headers=bearer(token), json={
        "filename": name, "content_base64": base64.b64encode(body).decode(),
        "satisfies": satisfies or []})
    assert response.status_code == 200, response.text


def _results(client, project: dict, **params) -> dict:
    response = client.get(f"/observe/projects/{project['id']}/results",
                          headers=ADMIN, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_one_row_per_result_with_its_maker_and_versions(client):
    project = make_project(client)
    _publish(client, project, "report.md", b"v1")
    _publish(client, project, "report.md", b"v2")
    _publish(client, project, "notes.md")
    view = _results(client, project)
    by_name = {r["name"]: r for r in view["results"]}
    assert set(by_name) == {"report.md", "notes.md"}
    assert by_name["report.md"]["version"] == 2
    assert by_name["report.md"]["versions"] == 2
    assert by_name["report.md"]["made_by"]["id"] == project["ceo_agent_id"]
    assert view["total"] == 2


def test_nothing_checked_reads_unchecked(client):
    project = make_project(client)
    _publish(client, project, "notes.md")
    row = _results(client, project)["results"][0]
    assert row["checked"] == "unchecked"
    assert row["checks"] == []
    assert _results(client, project)["counts"]["unchecked"] == 1


def test_an_unsettled_claim_is_not_a_check(client):
    project = deliver_project(client)
    req = propose(client, ceo_token(project), test=CMD_TEST)
    accept(client, req["requirement_id"], project)
    _publish(client, project, "report.md", satisfies=[req["key"]])
    row = _results(client, project)["results"][0]
    assert row["checked"] == "unchecked", "a claim waiting on its test checked nothing"
    assert [c["kind"] for c in row["checks"]] == ["pending"]
    assert row["serves"] == [{"key": req["key"], "statement": row["serves"][0]["statement"]}]


def test_a_judgement_is_never_shown_as_a_verification(client):
    project = deliver_project(client)
    req = propose(client, ceo_token(project), test=OBS_TEST)
    accept(client, req["requirement_id"], project)
    _publish(client, project, "deck.md", satisfies=[req["key"]])
    _settle(req["requirement_id"])
    row = _results(client, project)["results"][0]
    assert row["checked"] == "judged"
    assert all(c["kind"] != "platform" for c in row["checks"])


def test_a_test_the_platform_ran_reads_verified_by_yaaf(client):
    project = deliver_project(client)
    req = propose(client, ceo_token(project), test=CMD_TEST)
    accept(client, req["requirement_id"], project)
    _publish(client, project, "report.md", satisfies=[req["key"]])
    _settle(req["requirement_id"])
    row = _results(client, project)["results"][0]
    assert row["checked"] == "platform"
    assert row["checks"][0]["label"] == "verified by YAAF"


def test_one_persons_results(client):
    project = make_project(client)
    _publish(client, project, "notes.md")
    assert len(_results(client, project, agent_id=project["ceo_agent_id"])["results"]) == 1
    assert _results(client, project, agent_id="agt_nobody")["results"] == []


def test_a_read_names_the_reader_and_keeps_their_words(client):
    from tests.selftest_invariants.test_independent_read import OBSERVABLE, _agent
    from tests.selftest_invariants.test_independent_read import _project as review_project

    project = review_project(client, review="independent_read")
    _, ceo = live_token(project["id"], project["ceo_agent_id"])
    editorial = next(t for t in project["teams"] if t["name"] == "Editorial")
    task_id = client.post("/agent/tasks", headers=bearer(ceo), json={
        "team_id": editorial["id"], "title": "Write the launch post",
        "goal": "One post answering the question buyers actually ask.",
        "success_test": OBSERVABLE}).json()["id"]
    _, ada = live_token(project["id"], _agent(project, "Ada"))
    assert client.post(f"/agent/tasks/{task_id}/claim", headers=bearer(ada)).status_code == 200
    published = client.post("/agent/artifacts", headers=bearer(ada), json={
        "filename": "post.md", "content_base64": base64.b64encode(b"the post").decode()})
    assert published.status_code == 200, published.text
    client.post(f"/agent/tasks/{task_id}/complete", headers=bearer(ada),
                json={"evidence": "Wrote deliverables/post.md, 900 words, 6 sources."})
    _, juno = live_token(project["id"], _agent(project, "Juno"))
    client.post(f"/agent/tasks/{task_id}/review", headers=bearer(juno),
                json={"ok": True, "notes": "Checked both claims against the sources."})

    row = _results(client, project)["results"][0]
    assert row["made_by"]["name"] == "Ada"
    assert row["task"]["title"] == "Write the launch post"
    assert row["checked"] == "reviewer"
    read = row["checks"][0]
    assert read["label"] == "read by Juno"
    assert read["note"] == "Checked both claims against the sources."
    assert row["cost"]["runs"] >= 1, "a result always says what made it, even mid-run claims"
