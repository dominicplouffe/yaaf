"""Invariant selftests: "What they did" is the platform's record of a run.

Under a CEO message or a result, the chairman can open what the run that made
it actually did. Attacked here: that the steps are the rows the run left —
the task it took on, the file it published, the message it wrote — in order,
and that another run's work, even the same agent's, is never in the list.
"""

from __future__ import annotations

import base64

from tests.conftest import ADMIN, bearer, channel_of, live_token, make_project
from yaaf.core.db import db_session
from yaaf.core.models import Run, RunStatus, utcnow


def _did(client, run_id: str) -> dict:
    response = client.get(f"/observe/runs/{run_id}/did", headers=ADMIN)
    assert response.status_code == 200, response.text
    return response.json()


def _finish(run_id: str) -> None:
    with db_session() as session:
        run = session.get(Run, run_id)
        run.status, run.finished_at = RunStatus.DONE, utcnow()


def test_the_steps_are_what_the_run_left_behind(client):
    project = make_project(client)
    run_id, token = live_token(project["id"], project["ceo_agent_id"])
    said = client.post("/agent/messages", headers=bearer(token), json={
        "channel_id": channel_of(client, project["id"], "boardroom"),
        "body": "Week 6 closed four points under the field."})
    assert said.status_code == 200, said.text
    published = client.post("/agent/artifacts", headers=bearer(token), json={
        "filename": "week-6.md", "content_base64": base64.b64encode(b"the week").decode()})
    assert published.status_code == 200, published.text
    _finish(run_id)

    did = _did(client, run_id)
    texts = [s["text"] for s in did["steps"]]
    assert any(t.startswith("wrote to you, in Chat") and "Week 6 closed" in t for t in texts)
    assert "published week-6.md (v1)" in texts
    assert [s["at"] for s in did["steps"]] == sorted(s["at"] for s in did["steps"])


def test_another_runs_work_is_not_in_the_list(client):
    project = make_project(client)
    first, token = live_token(project["id"], project["ceo_agent_id"])
    _finish(first)
    second, other = live_token(project["id"], project["ceo_agent_id"])
    client.post("/agent/artifacts", headers=bearer(other), json={
        "filename": "later.md", "content_base64": base64.b64encode(b"later").decode()})
    assert all("later.md" not in s["text"] for s in _did(client, first)["steps"])
    assert any("later.md" in s["text"] for s in _did(client, second)["steps"])


def test_an_unknown_run_is_404(client):
    assert client.get("/observe/runs/run_nope/did", headers=ADMIN).status_code == 404
