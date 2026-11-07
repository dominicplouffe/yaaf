"""Invariant selftests: a decision from anywhere is two calls, and the second
can only be the first one's.

docs/ux-redesign/decide-anywhere/REQUIREMENTS.md, A3. The rules under attack:

1. Preview changes nothing; commit does exactly what the dashboard's own
   button does, through the same verb, attributed to the person.
2. A ticket is single-use, five minutes long, the previewer's own, for one
   project, one item, one option, one note and the item as it was shown.
3. An item that is no longer waiting answers 409 and is never decided twice.
4. Observers, strangers and agents cannot reach either call.
"""

from __future__ import annotations

from datetime import timedelta

from tests.conftest import ADMIN, bearer, headers_of, live_token, make_project, person
from tests.selftest_invariants.test_approval_notices import _file
from tests.selftest_invariants.test_asks import _ask, _project
from tests.selftest_invariants.test_delivered_stands_down import delivered
from yaaf.core.db import db_session
from yaaf.core.models import Approval, AuditLog, CommitTicket, Message, Project, utcnow


def _preview(client, project: dict, key: str, option: str, note: str = "", headers=ADMIN):
    return client.post(f"/admin/projects/{project['id']}/needs-you/preview", headers=headers,
                       json={"key": key, "option": option, "note": note})


def _commit(client, project: dict, key: str, option: str, ticket: str, note: str = "",
            headers=ADMIN):
    return client.post(f"/admin/projects/{project['id']}/needs-you/commit", headers=headers,
                       json={"key": key, "option": option, "note": note, "ticket": ticket})


def _status(approval_id: str) -> str:
    with db_session() as session:
        return session.get(Approval, approval_id).status


def test_preview_changes_nothing_and_commit_approves_as_the_person(client):
    project = make_project(client, slug="commit-approve")
    filed = _file(client, project)
    shown = _preview(client, project, filed["id"], "approve")
    assert shown.status_code == 200, shown.text
    about = shown.json()["about_to"]
    assert about["option"] == "Approve $199" and about["irreversible"] is True
    assert _status(filed["id"]) == "pending"

    done = _commit(client, project, filed["id"], "approve", shown.json()["ticket"])
    assert done.status_code == 200, done.text
    assert _status(filed["id"]) == "approved"
    with db_session() as session:
        (row,) = session.query(AuditLog).filter_by(action="needs_you.commit").all()
        assert row.entity_id == filed["id"]
        assert row.actor.startswith("user:")
        assert row.detail["option"] == "approve"


def test_a_ticket_is_spent_once(client):
    project = make_project(client, slug="commit-once")
    filed = _file(client, project)
    ticket = _preview(client, project, filed["id"], "decline").json()["ticket"]
    assert _commit(client, project, filed["id"], "decline", ticket).status_code == 200
    again = _commit(client, project, filed["id"], "decline", ticket)
    assert again.status_code == 409


def test_a_ticket_for_one_option_cannot_be_spent_on_another(client):
    project = make_project(client, slug="commit-other-option")
    filed = _file(client, project)
    ticket = _preview(client, project, filed["id"], "decline").json()["ticket"]
    swapped = _commit(client, project, filed["id"], "approve", ticket)
    assert swapped.status_code == 409
    assert _status(filed["id"]) == "pending"
    noted = _preview(client, project, filed["id"], "decline", note="not this month")
    changed = _commit(client, project, filed["id"], "decline", noted.json()["ticket"],
                      note="not ever")
    assert changed.status_code == 409


def test_a_ticket_for_one_item_cannot_be_spent_on_another(client):
    project = make_project(client, slug="commit-other-item")
    first, second = _file(client, project), _file(client, project)
    ticket = _preview(client, project, first["id"], "approve").json()["ticket"]
    assert _commit(client, project, second["id"], "approve", ticket).status_code == 409
    assert _status(second["id"]) == "pending"


def test_a_ticket_expires(client):
    project = make_project(client, slug="commit-expired")
    filed = _file(client, project)
    ticket = _preview(client, project, filed["id"], "approve").json()["ticket"]
    with db_session() as session:
        for row in session.query(CommitTicket):
            row.expires_at = utcnow() - timedelta(seconds=1)
    assert _commit(client, project, filed["id"], "approve", ticket).status_code == 409
    assert _status(filed["id"]) == "pending"


def test_a_ticket_is_not_somebody_elses(client):
    project = make_project(client, slug="commit-someone-else")
    filed = _file(client, project)
    ticket = _preview(client, project, filed["id"], "approve").json()["ticket"]
    deputy = person("deputy@example.com")
    assert client.post(f"/admin/projects/{project['id']}/members", headers=ADMIN,
                       json={"email": "deputy@example.com", "role": "co_chair"}
                       ).status_code == 200
    stolen = _commit(client, project, filed["id"], "approve", ticket,
                     headers=headers_of(deputy))
    assert stolen.status_code == 403
    assert _status(filed["id"]) == "pending"
    # Their own preview works: a co-chair decides as a peer.
    own = _preview(client, project, filed["id"], "approve", headers=headers_of(deputy))
    assert _commit(client, project, filed["id"], "approve", own.json()["ticket"],
                   headers=headers_of(deputy)).status_code == 200


def test_a_request_that_changed_after_the_preview_is_not_approved_on_it(client):
    project = make_project(client, slug="commit-changed")
    filed = _file(client, project)
    ticket = _preview(client, project, filed["id"], "approve").json()["ticket"]
    with db_session() as session:
        row = session.get(Approval, filed["id"])
        row.title = "Buy the $1,990 projections feed"
        row.amount_usd = 1990
    assert _commit(client, project, filed["id"], "approve", ticket).status_code == 409
    assert _status(filed["id"]) == "pending"


def test_deciding_on_the_dashboard_first_makes_the_ticket_a_409_not_a_second_decision(client):
    project = make_project(client, slug="commit-already")
    filed = _file(client, project)
    ticket = _preview(client, project, filed["id"], "approve").json()["ticket"]
    assert client.post(f"/admin/approvals/{filed['id']}/decide", headers=ADMIN,
                       json={"approve": False, "note": "no"}).status_code == 200
    late = _commit(client, project, filed["id"], "approve", ticket)
    assert late.status_code == 409
    assert "denied" in late.json()["detail"]
    assert _status(filed["id"]) == "denied"


def test_an_option_that_needs_a_note_is_refused_without_one(client):
    project = _project(client, slug="commit-needs-note")
    asked = _ask(client, project).json()
    assert _preview(client, project, asked["ask_id"], "reply").status_code == 422
    assert _preview(client, project, asked["ask_id"], "maybe").status_code == 422


def test_answering_a_question_is_the_boardroom_reply_the_inbox_sends(client):
    project = _project(client, slug="commit-ask")
    asked = _ask(client, project, options=["Number one", "Number three"]).json()
    ticket = _preview(client, project, asked["ask_id"], "option:1").json()["ticket"]
    assert _commit(client, project, asked["ask_id"], "option:1", ticket).status_code == 200
    with db_session() as session:
        said = [m.body for m in session.query(Message).filter(
            Message.project_id == project["id"], Message.sender_user_id.is_not(None))]
    assert any(body.endswith("— Number three") for body in said)


def test_scope_is_accepted_through_the_same_verb(client):
    from tests.selftest_invariants.test_requirements import ceo_token, deliver_project, propose

    project = deliver_project(client, slug="commit-scope")
    proposed = propose(client, ceo_token(project), statement="The archive exists")
    key = f"{project['id']}-plan"
    ticket = _preview(client, project, key, "accept").json()["ticket"]
    done = _commit(client, project, key, "accept", ticket)
    assert done.status_code == 200, done.text
    from yaaf.core.models import Requirement

    with db_session() as session:
        assert session.get(Requirement, proposed["requirement_id"]).status != "proposed"


def test_a_finished_delivery_is_completed_through_the_same_verb(client):
    project = delivered(client, "commit-signoff")
    key = f"{project['id']}-signoff"
    ticket = _preview(client, project, key, "complete").json()["ticket"]
    assert _commit(client, project, key, "complete", ticket).status_code == 200
    with db_session() as session:
        assert session.get(Project, project["id"]).status == "completed"


def test_observers_strangers_and_agents_cannot_preview(client):
    project = make_project(client, slug="commit-who")
    filed = _file(client, project)
    watcher = person("watcher@example.com")
    assert client.post(f"/admin/projects/{project['id']}/members", headers=ADMIN,
                       json={"email": "watcher@example.com", "role": "observer"}
                       ).status_code == 200
    assert _preview(client, project, filed["id"], "approve",
                    headers=headers_of(watcher)).status_code == 403
    stranger = person("stranger@example.com")
    assert _preview(client, project, filed["id"], "approve",
                    headers=headers_of(stranger)).status_code == 404
    _, token = live_token(project["id"], project["ceo_agent_id"])
    assert _preview(client, project, filed["id"], "approve",
                    headers=bearer(token)).status_code == 401
    assert _status(filed["id"]) == "pending"
