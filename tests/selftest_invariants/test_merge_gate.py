"""Invariant selftests: the merge gate. Only the platform merges a Code
Factory's main, and only once every gate is green on the exact commit.

Attacked over HTTP, as an agent and as GitHub would:

  * a change exists only for a task its author holds, serving accepted scope,
    on a branch of its own — never main, never on a non-factory project
  * the size is GitHub's count, and over the limit is refused and closed
  * the author cannot sign, and neither can a seat that builds
  * a green check on an older commit, or a forged webhook, counts for nothing
  * a push after the review clears the review
  * the platform merges pinned to the reviewed head, and only from READY
  * main moving without the platform opens a flag and freezes merging; the
    platform's own merge arriving as a push does not
"""

from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest

from tests.conftest import ADMIN, bearer, live_token, seed_role
from tests.selftest_invariants.test_requirements import accept, propose, tick
from yaaf.core.db import db_session
from yaaf.core.models import (
    Change,
    ChangeStatus,
    Flag,
    FlagKind,
    Notice,
    NoticeKind,
    Project,
)
from yaaf.server import changes, github

CHAIR = {"email": "chair@example.com", "display_name": "Chair"}
REPO = "acme/slotwise"
OBS = {"type": "observable", "target": "the booking page holds a slot for 10 minutes"}


class FakeGitHub:
    """api.github.com, as far as the gate talks to it."""

    def __init__(self):
        self.prs: dict[int, dict] = {}
        self.calls: list[tuple[str, str, dict | None]] = []
        self.size = (120, 30)
        self.merge_sha = "m" * 40
        self.merge_status = 200
        self.files: list[dict] = [{"filename": "src/booking/hold.ts", "status": "modified",
                                   "patch": "@@\n+export const HOLD_MINUTES = 10"}]
        self.files_status = 200

    def __call__(self, method, path, token, json=None):
        self.calls.append((method, path, json))
        if method == "POST" and path == f"/repos/{REPO}/pulls":
            number = len(self.prs) + 41
            pr = {"number": number, "head": {"sha": f"h{number}" + "0" * 30, "ref": json["head"]},
                  "additions": self.size[0], "deletions": self.size[1], "changed_files": 4,
                  "html_url": f"https://github.com/{REPO}/pull/{number}"}
            self.prs[number] = pr
            return httpx.Response(201, json=pr)
        if method == "PUT" and path.endswith("/merge"):
            if self.merge_status != 200:
                return httpx.Response(self.merge_status, json={"message": "Head moved"})
            return httpx.Response(200, json={"sha": self.merge_sha, "merged": True})
        if method == "GET" and "/files" in path:
            return httpx.Response(self.files_status, json=self.files)
        if method == "PUT" and path.endswith("/protection"):
            return httpx.Response(200, json={})
        return httpx.Response(200, json={})

    def merges(self):
        return [c for c in self.calls if c[0] == "PUT" and c[1].endswith("/merge")]


@pytest.fixture
def gh(monkeypatch):
    fake = FakeGitHub()
    monkeypatch.setattr(github, "_request", fake)
    monkeypatch.setattr(github, "platform_token", lambda session: "platform-token")
    return fake


def _agent(project, name):
    return next(a["id"] for t in project["teams"] for a in t["agents"] if a["name"] == name)


def _factory(client, *, mode="code_factory", slug="slotwise", scenarios=False,
             **rules) -> dict:
    """Hidden scenarios are off unless a test asks: these tests attack the
    gates around them, and `test_quality_gates.py` attacks the scenarios."""
    client.post("/admin/users", json=CHAIR, headers=ADMIN)
    seed_role(client, "ceo", is_ceo=True, category="leadership")
    seed_role(client, "engineer", category="build")
    seed_role(client, "reviewer", category="challenge")
    created = client.post("/admin/projects", json={
        "slug": slug, "name": "Slotwise", "goal": "A booking app for physio clinics.",
        "chairman_email": CHAIR["email"], "mode": mode,
        "constitution": {"plan_authority": "chairman", "scope_authority": "chairman",
                         "hidden_scenarios": scenarios, **rules},
        "teams": [{"role_slug": "engineer", "name": "Build", "employees": ["Mika", "Dev"],
                   "lead": "Mika"},
                  {"role_slug": "reviewer", "name": "Review", "employees": ["Rhea"]}],
    }, headers=ADMIN)
    assert created.status_code == 200, created.text
    project = created.json()
    assert client.post(f"/admin/projects/{project['id']}/start", headers=ADMIN).status_code == 200
    with db_session() as session:
        session.get(Project, project["id"]).github_repo = REPO
    secret = client.post(f"/admin/projects/{project['id']}/webhook_secrets", headers=ADMIN)
    project["webhook_secret"] = secret.json()["github_webhook_secret"]
    return project


def _claimed_task(client, project) -> str:
    _, ceo = live_token(project["id"], project["ceo_agent_id"])
    rid = propose(client, ceo, statement="A patient can hold a free slot")["requirement_id"]
    accept(client, rid, project)
    tick(project)
    build = next(t for t in project["teams"] if t["name"] == "Build")
    task = client.post("/agent/tasks", headers=bearer(ceo), json={
        "team_id": build["id"], "title": "Hold a slot for ten minutes",
        "goal": "The hold survives a restart.", "success_test": OBS, "requirement_id": rid})
    assert task.status_code == 200, task.text
    _, mika = live_token(project["id"], _agent(project, "Mika"))
    assert client.post(f"/agent/tasks/{task.json()['id']}/claim",
                       headers=bearer(mika)).status_code == 200
    return task.json()["id"]


def _open(client, project, task_id, branch="mika/t-112-slot-hold"):
    _, mika = live_token(project["id"], _agent(project, "Mika"))
    return client.post("/agent/changes", headers=bearer(mika), json={
        "task_id": task_id, "branch": branch, "title": "Hold a slot for 10 minutes",
        "summary": "A slot is held for the patient for ten minutes while they book."})


def _hook(client, project, event, payload, delivery):
    raw = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(project["webhook_secret"].encode(), raw,
                               hashlib.sha256).hexdigest()
    return client.post(f"/webhooks/github/{project['id']}", content=raw, headers={
        "X-GitHub-Event": event, "X-GitHub-Delivery": delivery,
        "X-Hub-Signature-256": sig, "Content-Type": "application/json"})


def _green(client, project, sha, delivery="ci-1", name="test"):
    return _hook(client, project, "check_run", {
        "action": "completed", "sender": {"login": "github-actions"},
        "check_run": {"head_sha": sha, "name": name, "status": "completed",
                      "conclusion": "success"}}, delivery)


def _sign(client, project, change_id, who="Rhea", verdict="sign"):
    _, token = live_token(project["id"], _agent(project, who))
    return client.post(f"/agent/changes/{change_id}/review", headers=bearer(token), json={
        "verdict": verdict, "note": "Read the hold logic and its expiry; it survives a restart."})


def _merge(project) -> int:
    with db_session() as session:
        return changes.merge_ready(session, session.get(Project, project["id"]))


def _change(change_id) -> Change:
    with db_session() as session:
        row = session.get(Change, change_id)
        session.expunge(row)
        return row


# ── opening ─────────────────────────────────────────────────────────────────────


def test_the_platform_opens_the_pull_request_with_its_own_token(client, gh):
    project = _factory(client)
    task_id = _claimed_task(client, project)
    opened = _open(client, project, task_id)
    assert opened.status_code == 200, opened.text
    body = opened.json()
    assert body["number"] == 41 and body["status"] == ChangeStatus.OPEN
    assert body["lines"] == 150, "GitHub's count, not the agent's"
    assert body["gates"]["serves"]["ok"] and body["gates"]["size"]["ok"]
    assert not body["gates"]["checks"]["ok"] and not body["gates"]["review"]["ok"]
    post = next(c for c in gh.calls if c[0] == "POST" and c[1].endswith("/pulls"))
    assert post[2]["base"] == "main" and post[2]["head"] == "mika/t-112-slot-hold"


def test_a_change_needs_a_factory_its_own_claimed_task_and_its_own_branch(client, gh):
    project = _factory(client)
    task_id = _claimed_task(client, project)

    onto_main = _open(client, project, task_id, branch="main")
    assert onto_main.status_code == 422 and "only the platform moves" in onto_main.json()["error"]

    _, dev = live_token(project["id"], _agent(project, "Dev"))
    not_yours = client.post("/agent/changes", headers=bearer(dev), json={
        "task_id": task_id, "branch": "dev/sneak", "title": "Not mine",
        "summary": "A change on a task somebody else holds."})
    assert not_yours.status_code == 409

    assert _open(client, project, task_id).status_code == 200
    twice = _open(client, project, task_id, branch="mika/again")
    assert twice.status_code == 409 and "One task, one change" in twice.json()["error"]


def test_a_project_that_is_not_a_factory_has_no_changes(client, gh):
    project = _factory(client, mode="deliver", slug="plain")
    task_id = _claimed_task(client, project)
    refused = _open(client, project, task_id)
    assert refused.status_code == 422
    assert "Code Factory" in refused.json()["error"]


def test_a_change_over_the_size_limit_is_refused_and_closed(client, gh):
    gh.size = (900, 300)
    project = _factory(client)
    body = _open(client, project, _claimed_task(client, project)).json()
    assert body["status"] == ChangeStatus.REFUSED
    assert "1200 lines of 400" in body["status_reason"]
    assert any(c[0] == "PATCH" and c[2] == {"state": "closed"} for c in gh.calls)
    with db_session() as session:
        assert session.query(Notice).filter_by(
            agent_id=body["author_agent_id"], kind=NoticeKind.CHANGE_RETURNED).count() == 1


# ── the review ──────────────────────────────────────────────────────────────────


def test_the_author_cannot_sign_and_neither_can_a_seat_that_builds(client, gh):
    project = _factory(client)
    change = _open(client, project, _claimed_task(client, project)).json()
    own = _sign(client, project, change["id"], who="Mika")
    assert own.status_code == 403 and "You wrote this change" in own.json()["error"]
    builder = _sign(client, project, change["id"], who="Dev")
    assert builder.status_code == 403 and "challenge seat" in builder.json()["error"]
    assert _sign(client, project, change["id"]).status_code == 200


def test_a_returned_change_tells_its_author_and_does_not_merge(client, gh):
    project = _factory(client)
    change = _open(client, project, _claimed_task(client, project)).json()
    _green(client, project, change["head_sha"])
    returned = _sign(client, project, change["id"], verdict="return")
    assert returned.status_code == 200
    assert returned.json()["change"]["review"]["returns"] == 1
    assert _merge(project) == 0 and gh.merges() == []


# ── merging ─────────────────────────────────────────────────────────────────────


def test_every_gate_green_and_the_platform_merges_pinned_to_the_head(client, gh):
    project = _factory(client)
    change = _open(client, project, _claimed_task(client, project)).json()
    assert _green(client, project, change["head_sha"]).status_code == 200
    signed = _sign(client, project, change["id"]).json()
    assert signed["change"]["status"] == ChangeStatus.READY

    assert _merge(project) == 1
    (method, path, body), = gh.merges()
    assert path == f"/repos/{REPO}/pulls/41/merge"
    assert body["sha"] == change["head_sha"], "pinned to the commit that was checked"
    row = _change(change["id"])
    assert row.status == ChangeStatus.MERGED and row.merged_sha == gh.merge_sha


def test_a_green_check_on_an_older_commit_counts_for_nothing(client, gh):
    project = _factory(client)
    change = _open(client, project, _claimed_task(client, project)).json()
    _green(client, project, "an-older-commit-sha")
    _sign(client, project, change["id"])
    assert _change(change["id"]).status == ChangeStatus.OPEN
    assert _merge(project) == 0


def test_a_failed_or_running_check_blocks_the_merge(client, gh):
    project = _factory(client)
    change = _open(client, project, _claimed_task(client, project)).json()
    _green(client, project, change["head_sha"], delivery="ci-1", name="test")
    _hook(client, project, "check_run", {
        "action": "created", "sender": {"login": "github-actions"},
        "check_run": {"head_sha": change["head_sha"], "name": "lint",
                      "status": "in_progress", "conclusion": None}}, "ci-2")
    _sign(client, project, change["id"])
    assert "running: lint" in _change(change["id"]).gates["checks"]["detail"]
    _hook(client, project, "check_run", {
        "action": "completed", "sender": {"login": "github-actions"},
        "check_run": {"head_sha": change["head_sha"], "name": "lint",
                      "status": "completed", "conclusion": "failure"}}, "ci-3")
    assert "failed: lint" in _change(change["id"]).gates["checks"]["detail"]
    assert _merge(project) == 0


def test_a_push_after_the_review_clears_the_review(client, gh):
    project = _factory(client)
    change = _open(client, project, _claimed_task(client, project)).json()
    _green(client, project, change["head_sha"])
    _sign(client, project, change["id"])
    assert _change(change["id"]).status == ChangeStatus.READY

    moved = _hook(client, project, "pull_request", {
        "action": "synchronize", "sender": {"login": "mika-bot"},
        "pull_request": {"number": 41, "head": {"sha": "f" * 40},
                         "additions": 140, "deletions": 30, "changed_files": 5}}, "pr-sync")
    assert moved.status_code == 200, moved.text
    row = _change(change["id"])
    assert row.status == ChangeStatus.OPEN
    assert row.review_verdict is None and row.checks == {}
    assert _merge(project) == 0


def test_a_forged_webhook_moves_no_gate(client, gh):
    project = _factory(client)
    change = _open(client, project, _claimed_task(client, project)).json()
    raw = json.dumps({"check_run": {"head_sha": change["head_sha"], "name": "test",
                                    "status": "completed", "conclusion": "success"}}).encode()
    forged = client.post(f"/webhooks/github/{project['id']}", content=raw, headers={
        "X-GitHub-Event": "check_run", "X-GitHub-Delivery": "forged",
        "X-Hub-Signature-256": "sha256=" + "0" * 64, "Content-Type": "application/json"})
    assert forged.status_code == 403
    assert _change(change["id"]).checks == {}


def test_a_merge_github_refuses_goes_back_to_open_with_the_reason(client, gh):
    gh.merge_status = 409
    project = _factory(client)
    change = _open(client, project, _claimed_task(client, project)).json()
    _green(client, project, change["head_sha"])
    _sign(client, project, change["id"])
    assert _merge(project) == 0
    row = _change(change["id"])
    assert row.status == ChangeStatus.OPEN and "refused the merge" in row.status_reason


# ── main ────────────────────────────────────────────────────────────────────────


def _reconcile(project) -> int:
    with db_session() as session:
        return changes.reconcile_main(session, session.get(Project, project["id"]))


def test_main_moving_without_the_platform_freezes_merging(client, gh):
    project = _factory(client)
    change = _open(client, project, _claimed_task(client, project)).json()
    _green(client, project, change["head_sha"])
    _sign(client, project, change["id"])

    pushed = _hook(client, project, "push", {
        "ref": "refs/heads/main", "after": "b" * 40, "pusher": {"name": "dev-bot"},
        "sender": {"login": "dev-bot"}}, "push-1")
    assert pushed.status_code == 200
    assert _reconcile(project) == 1
    with db_session() as session:
        flag = session.query(Flag).filter_by(project_id=project["id"],
                                             kind=FlagKind.MAIN_BYPASSED).one()
        assert "dev-bot" in flag.summary
    assert _merge(project) == 0, "frozen until the chairman answers the flag"
    assert gh.merges() == []
    listed = client.get(f"/observe/projects/{project['id']}/changes", headers=ADMIN).json()
    assert listed["merges_frozen"] is not None


def test_the_platforms_own_merge_arriving_as_a_push_is_accounted_for(client, gh):
    project = _factory(client)
    change = _open(client, project, _claimed_task(client, project)).json()
    _green(client, project, change["head_sha"])
    _sign(client, project, change["id"])
    assert _merge(project) == 1
    _hook(client, project, "push", {
        "ref": "refs/heads/main", "after": gh.merge_sha, "pusher": {"name": "yaaf"},
        "sender": {"login": "yaaf"}}, "push-ours")
    assert _reconcile(project) == 0
    with db_session() as session:
        assert session.query(Flag).filter_by(project_id=project["id"],
                                             kind=FlagKind.MAIN_BYPASSED).count() == 0


def test_attaching_a_repo_to_a_factory_protects_main(client, gh, monkeypatch):
    project = _factory(client)
    monkeypatch.setattr(github, "provision_repo", lambda session, project, name: REPO)
    attached = client.post(f"/admin/projects/{project['id']}/github", json={"repo": REPO},
                           headers=ADMIN)
    assert attached.status_code == 200, attached.text
    assert attached.json()["branch_protection"]["ok"] is True
    protection = next(c for c in gh.calls if c[1].endswith("/branches/main/protection"))
    assert protection[2]["required_pull_request_reviews"] is not None
    assert protection[2]["allow_force_pushes"] is False
