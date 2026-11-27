"""Invariant selftests: Code Factory releases. The platform ships, watches and
undoes; the dial decides only who says yes first.

Attacked here:

  * nothing ships without a production environment, from a main nobody
    checked, twice at once, backwards, or with nothing new in it
  * "approve_first": the first release and every major one wait for the
    chairman, and silence never approves one; minor releases after that ship
  * "autonomous": every release ships, and the chairman is told after
  * a broken canary guard rolls back to the previous release and opens an
    incident, with no approval asked of anyone
  * production's deploy credentials reach no seat: the scope is reserved, an
    agent cannot re-scope one, and the deploy output never prints one
  * the postmortem is the CEO's, and it is more than a line
"""

from __future__ import annotations

import subprocess
from datetime import timedelta

import pytest

from tests.conftest import ADMIN, bearer, live_token, seed_role
from tests.selftest_invariants.test_merge_gate import (
    FakeGitHub,
    _agent,
    _claimed_task,
    _factory,
    _green,
    _merge,
    _open,
    _sign,
)
from tests.selftest_invariants.test_quality_gates import _git
from yaaf.core.db import db_session
from yaaf.core.models import (
    Approval,
    ApprovalStatus,
    Incident,
    Project,
    Release,
    ReleaseStatus,
    RunKind,
    RunQueue,
    utcnow,
)
from yaaf.runner import daemon
from yaaf.runner.types import RunJob
from yaaf.server import github, releases

PROD = {"production": {"deploy": "fly deploy -a slotwise --strategy canary",
                       "health_url": "https://slotwise.example/healthz"}}


@pytest.fixture
def gh(monkeypatch):
    fake = FakeGitHub()
    monkeypatch.setattr(github, "_request", fake)
    monkeypatch.setattr(github, "platform_token", lambda session: "platform-token")
    return fake


@pytest.fixture
def health(monkeypatch):
    state = {"ok": True}
    monkeypatch.setattr(releases, "probe",
                        lambda url: (state["ok"], "HTTP 200" if state["ok"] else "HTTP 503"))
    return state


def _shipped_change(client, project, gh, n=1):
    """Merge one more change through every gate."""
    for _ in range(n):
        task = _claimed_task(client, project)
        change = _open(client, project, task, branch=f"mika/{task[-6:]}").json()
        _green(client, project, change["head_sha"], delivery=f"ci-{task}")
        _sign(client, project, change["id"])
        gh.merge_sha = f"m{task[-12:]}".ljust(40, "0")
        assert _merge(project) == 1
        _, mika = live_token(project["id"], _agent(project, "Mika"))
        client.post(f"/agent/tasks/{task}/complete", headers=bearer(mika),
                    json={"evidence": "Merged by the platform after every gate."})


def _with_prod(client, project):
    put = client.put(f"/admin/projects/{project['id']}/environments", json=PROD, headers=ADMIN)
    assert put.status_code == 200, put.text


def _propose(client, project, version, title="Booking and rescheduling"):
    _, ceo = live_token(project["id"], project["ceo_agent_id"])
    return client.post("/agent/releases", headers=bearer(ceo),
                       json={"version": version, "title": title})


def _tick(project):
    with db_session() as session:
        releases.watch(session, session.get(Project, project["id"]))


def _release(release_id) -> Release:
    with db_session() as session:
        row = session.get(Release, release_id)
        session.expunge(row)
        return row


def _deployed(release_id, ok=True, minutes_ago=0):
    with db_session() as session:
        row = session.get(Release, release_id)
        releases.deployed(session, row, "deploy", ok, "deployed" if ok else "boom")
        if row.deployed_at is not None:
            row.deployed_at = utcnow() - timedelta(minutes=minutes_ago)


def _deploys(project) -> list[RunQueue]:
    with db_session() as session:
        rows = session.query(RunQueue).filter_by(project_id=project["id"],
                                                 kind=RunKind.DEPLOY).all()
        for r in rows:
            session.expunge(r)
        return rows


def _live(client, project, gh, health, version) -> dict:
    """Propose, approve if it waits, deploy, and pass the watch."""
    body = _propose(client, project, version).json()
    if body["status"] == ReleaseStatus.PROPOSED:
        assert client.post(f"/admin/approvals/{body['approval_id']}/decide", headers=ADMIN,
                           json={"approve": True}).status_code == 200
    _deployed(body["id"], minutes_ago=60)
    _tick(project)
    assert _release(body["id"]).status == ReleaseStatus.LIVE
    return body


# ── what may ship ───────────────────────────────────────────────────────────────


def test_nothing_ships_without_production_or_without_anything_new(client, gh):
    project = _factory(client)
    _shipped_change(client, project, gh)
    no_env = _propose(client, project, "0.1.0")
    assert no_env.status_code == 409 and "Environments" in no_env.json()["error"]

    _with_prod(client, project)
    bad = _propose(client, project, "first")
    assert bad.status_code == 422
    assert _propose(client, project, "0.1.0").status_code == 200
    twice = _propose(client, project, "0.1.1")
    assert twice.status_code == 409 and "One release at a time" in twice.json()["error"]


def test_the_first_release_waits_for_the_chairman_and_silence_ships_nothing(client, gh, health):
    project = _factory(client)
    _shipped_change(client, project, gh)
    _with_prod(client, project)
    body = _propose(client, project, "0.1.0").json()
    assert body["status"] == ReleaseStatus.PROPOSED
    assert _deploys(project) == [], "nothing deploys before his yes"
    with db_session() as session:
        approval = session.get(Approval, body["approval_id"])
        assert "first release to production" in approval.body
        approval.expires_at = utcnow() - timedelta(minutes=1)

    from yaaf.core.constitution import effective
    from yaaf.server import janitor

    with db_session() as session:
        row = session.get(Project, project["id"])
        janitor.expire_approvals(session, row, effective(session, row.id))
    with db_session() as session:
        assert session.get(Approval, body["approval_id"]).status != ApprovalStatus.APPROVED
    _tick(project)
    assert _release(body["id"]).status == ReleaseStatus.REFUSED
    assert _deploys(project) == []


def test_the_chairmans_yes_queues_the_platforms_deploy(client, gh, health):
    project = _factory(client)
    _shipped_change(client, project, gh)
    _with_prod(client, project)
    body = _propose(client, project, "0.1.0").json()
    decided = client.post(f"/admin/approvals/{body['approval_id']}/decide", headers=ADMIN,
                          json={"approve": True})
    assert decided.status_code == 200, decided.text
    assert decided.json()["release"]["status"] == ReleaseStatus.APPROVED
    (queued,) = _deploys(project)
    assert queued.trigger["action"] == "deploy" and queued.trigger["sha"] == body["sha"]


def test_after_the_first_minor_releases_ship_and_majors_wait(client, gh, health):
    project = _factory(client)
    _with_prod(client, project)
    _shipped_change(client, project, gh)
    _live(client, project, gh, health, "0.1.0")
    assert any(c[1].endswith("/releases") for c in gh.calls), "tagged on GitHub when live"

    _shipped_change(client, project, gh)
    minor = _propose(client, project, "0.2.0").json()
    assert minor["status"] == ReleaseStatus.APPROVED, "the crew ships minors after the first"
    _deployed(minor["id"], minutes_ago=60)
    _tick(project)

    _shipped_change(client, project, gh)
    major = _propose(client, project, "1.0.0").json()
    assert major["status"] == ReleaseStatus.PROPOSED, "a major waits for him"

    backwards = _propose(client, project, "0.1.5")
    assert backwards.status_code == 409  # 1.0.0 is still in flight


def test_autonomous_ships_the_first_release_too(client, gh, health):
    project = _factory(client, release_authority="autonomous")
    _shipped_change(client, project, gh)
    _with_prod(client, project)
    body = _propose(client, project, "0.1.0").json()
    assert body["status"] == ReleaseStatus.APPROVED and body["approval_id"] is None
    assert len(_deploys(project)) == 1


# ── the guard ───────────────────────────────────────────────────────────────────


def test_a_broken_guard_rolls_back_and_opens_an_incident_without_asking(client, gh, health):
    project = _factory(client, release_authority="autonomous", canary_max_failures=2)
    _with_prod(client, project)
    _shipped_change(client, project, gh)
    first = _live(client, project, gh, health, "0.1.0")

    _shipped_change(client, project, gh)
    second = _propose(client, project, "0.2.0").json()
    _deployed(second["id"])
    health["ok"] = False
    _tick(project)
    assert _release(second["id"]).status == ReleaseStatus.WATCHING, "one bad read is noise"
    _tick(project)
    row = _release(second["id"])
    assert row.status == ReleaseStatus.ROLLED_BACK and "2 failed health reads" in row.status_reason

    rollback = [q for q in _deploys(project) if q.trigger["action"] == "rollback"]
    assert len(rollback) == 1 and rollback[0].trigger["sha"] == first["sha"]
    with db_session() as session:
        incident = session.query(Incident).filter_by(project_id=project["id"]).one()
        assert incident.opened_by == "canary" and incident.release_id == second["id"]
        assert session.query(Approval).filter_by(project_id=project["id"]).count() == 0


def test_a_failed_deploy_never_reaches_the_watch(client, gh, health):
    project = _factory(client, release_authority="autonomous")
    _with_prod(client, project)
    _shipped_change(client, project, gh)
    body = _propose(client, project, "0.1.0").json()
    _deployed(body["id"], ok=False)
    assert _release(body["id"]).status == ReleaseStatus.FAILED


def test_nothing_ships_from_a_main_nobody_checked(client, gh, health):
    from tests.selftest_invariants.test_merge_gate import _hook
    from yaaf.server import changes

    project = _factory(client, release_authority="autonomous")
    _with_prod(client, project)
    _shipped_change(client, project, gh)
    _hook(client, project, "push", {"ref": "refs/heads/main", "after": "b" * 40,
                                    "pusher": {"name": "someone"}}, "push-x")
    with db_session() as session:
        changes.reconcile_main(session, session.get(Project, project["id"]))
    refused = _propose(client, project, "0.1.0")
    assert refused.status_code == 409 and "Main moved" in refused.json()["error"]


# ── production's keys ───────────────────────────────────────────────────────────


def test_no_role_may_be_called_deploy(client):
    seed_role(client, "ceo", is_ceo=True)
    response = client.post("/admin/roles", headers=ADMIN, json={
        "slug": "deploy", "name": "Deploy", "is_ceo_role": False,
        "description": "x", "system_prompt": "x", "tool_permissions": {}})
    assert response.status_code == 422 and "reserved" in response.json()["detail"]


def test_a_deploy_credential_is_granted_alone_and_no_agent_can_rescope_it(client, gh):
    project = _factory(client, capability_authority="ceo")
    mixed = client.post("/admin/secrets", headers=ADMIN, json={
        "project_id": project["id"], "name": "fly-api-token", "value": "fo1_production",
        "role_scopes": ["deploy", "engineer"]})
    assert mixed.status_code == 422
    alone = client.post("/admin/secrets", headers=ADMIN, json={
        "project_id": project["id"], "name": "fly-api-token", "value": "fo1_production",
        "role_scopes": ["deploy"]})
    assert alone.status_code == 200, alone.text

    _, ceo = live_token(project["id"], project["ceo_agent_id"])
    grab = client.post("/agent/credentials/fly-api-token/scope", headers=bearer(ceo),
                       json={"add_roles": ["engineer"],
                             "reason": "the engineer needs to check the deploy works"})
    assert grab.status_code == 403 and "deploy credential" in grab.json()["error"]

    with db_session() as session:
        env = daemon._scoped_secret_env(session, session.get(Project, project["id"]), "deploy")
    assert list(env.values()) == ["fo1_production"]


def test_the_runner_deploys_the_commit_and_never_prints_the_key(tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    _git("init", "-q", "-b", "main", cwd=origin)
    (origin / "app.txt").write_text("v1\n")
    _git("add", ".", cwd=origin)
    _git("commit", "-q", "-m", "v1", cwd=origin)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=origin, capture_output=True,
                         text=True, check=True).stdout.strip()
    where = tmp_path / "workspaces" / ".deploys" / "run_d"
    job = RunJob(run_id="run_d", project_id="p", agent_id=None, kind=RunKind.DEPLOY,
                 trigger={"release_id": "r"}, task_id=None, timeout_s=60,
                 workspace_dir=str(where), check_sha=sha, check_branch=sha,
                 fetch_url=str(origin), deploy_action="deploy",
                 check_command='cat app.txt && echo "token=$FLY_API_TOKEN"',
                 deploy_env={"FLY_API_TOKEN": "fo1_production_secret"})
    result = daemon._run_deploy(job)
    assert result.check_exit_code == 0, result.check_output
    assert "v1" in result.check_output
    assert "fo1_production_secret" not in result.check_output
    assert "<secret>" in result.check_output
    assert not where.exists()


# ── the postmortem ──────────────────────────────────────────────────────────────


def test_the_postmortem_is_the_ceos_and_more_than_a_line(client, gh):
    project = _factory(client)
    with db_session() as session:
        incident = releases.open_incident(session, session.get(Project, project["id"]),
                                          title="PDFs failed for Swiss customers",
                                          opened_by="alert")
        incident_id = incident.id
    _, mika = live_token(project["id"], _agent(project, "Mika"))
    by_builder = client.post(f"/agent/incidents/{incident_id}/postmortem",
                             headers=bearer(mika), json={"text": "x" * 200})
    assert by_builder.status_code == 403
    _, ceo = live_token(project["id"], project["ceo_agent_id"])
    short = client.post(f"/agent/incidents/{incident_id}/postmortem", headers=bearer(ceo),
                        json={"text": "Fixed it."})
    assert short.status_code == 422
    done = client.post(f"/agent/incidents/{incident_id}/postmortem", headers=bearer(ceo),
                       json={"text": "Django 5.1 changed number formatting for fr-CH; the PDF "
                                     "code parsed formatted text back as numbers. Fixed in "
                                     "#1842; six locales now in the hidden scenarios."})
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "resolved"
    assert done.json()["timeline"][-1]["what"].startswith("postmortem written")


# ── staging, provenance, SBOM, previews ─────────────────────────────────────────

STAGED = {**PROD, "staging": {"deploy": "fly deploy -a slotwise-staging",
                              "health_url": "https://staging.slotwise.example/healthz"}}


def _with_staging(client, project):
    assert client.put(f"/admin/projects/{project['id']}/environments", json=STAGED,
                      headers=ADMIN).status_code == 200


def test_staging_runs_first_and_the_chairman_is_asked_after_it_ran_clean(client, gh, health):
    project = _factory(client)
    _with_staging(client, project)
    _shipped_change(client, project, gh)
    body = _propose(client, project, "0.1.0").json()
    assert body["environment"] == "staging" and body["status"] == ReleaseStatus.APPROVED
    (staging,) = _deploys(project)
    assert staging.trigger["environment"] == "staging"

    _deployed(body["id"], minutes_ago=60)
    _tick(project)
    row = _release(body["id"])
    assert row.environment == "production" and row.status == ReleaseStatus.PROPOSED
    with db_session() as session:
        assert "ran clean on staging" in session.get(Approval, row.approval_id).body
    assert len(_deploys(project)) == 1, "production waits for his yes"


def test_a_staging_failure_stops_there_with_no_incident(client, gh, health):
    project = _factory(client, release_authority="autonomous", canary_max_failures=1)
    _with_staging(client, project)
    _shipped_change(client, project, gh)
    body = _propose(client, project, "0.1.0").json()
    _deployed(body["id"])
    health["ok"] = False
    _tick(project)
    row = _release(body["id"])
    assert row.status == ReleaseStatus.FAILED and "Production was never touched" in row.status_reason
    with db_session() as session:
        assert session.query(Incident).filter_by(project_id=project["id"]).count() == 0
    assert all(q.trigger["environment"] == "staging" for q in _deploys(project))


def test_a_live_release_carries_signed_provenance_anyone_can_check(client, gh, health):
    from yaaf.server import provenance

    project = _factory(client, release_authority="autonomous")
    _with_prod(client, project)
    _shipped_change(client, project, gh)
    body = _propose(client, project, "0.1.0").json()
    with db_session() as session:
        row = session.get(Release, body["id"])
        releases.deployed(session, row, "deploy", True, "ok", sbom=b'{"bomFormat": "CycloneDX"}')
        row.deployed_at = utcnow() - timedelta(minutes=60)
    _tick(project)
    row = _release(body["id"])
    assert row.status == ReleaseStatus.LIVE and row.sbom_uri
    statement = row.provenance
    assert statement["subject"][0]["digest"]["gitCommit"] == row.sha
    inner = statement["predicate"]["buildDefinition"]["internalParameters"]
    assert inner["sbom_sha256"] and len(inner["changes"]) == 1
    key = client.get("/observe/provenance_key", headers=ADMIN).json()["public_key_pem"]
    assert provenance.verify(key, statement, row.provenance_sig)
    forged = {**statement, "subject": [{"name": "x", "digest": {"gitCommit": "0" * 40}}]}
    assert not provenance.verify(key, forged, row.provenance_sig)
    tag = next(c for c in gh.calls if c[1].endswith("/releases"))
    assert "Provenance: signed" in tag[2]["body"]


def test_a_preview_per_change_gates_the_merge_and_never_gets_production_keys(client, gh,
                                                                             health):
    from yaaf.core.models import Change, ChangeStatus
    from yaaf.server import changes

    project = _factory(client)
    envs = {**PROD, "preview": {"deploy": "fly deploy -a slotwise-pr-$YAAF_CHANGE_NUMBER",
                                "health_url": "https://pr-{number}.slotwise.example/healthz"}}
    assert client.put(f"/admin/projects/{project['id']}/environments", json=envs,
                      headers=ADMIN).status_code == 200
    task = _claimed_task(client, project)
    change = _open(client, project, task).json()
    assert not change["gates"]["preview"]["ok"]
    (queued,) = [q for q in _deploys(project) if q.trigger.get("environment") == "preview"]
    assert queued.trigger["change_id"] == change["id"]
    _green(client, project, change["head_sha"])
    _sign(client, project, change["id"])

    health["ok"] = False
    with db_session() as session:
        changes.record_preview(session, session.get(Change, change["id"]),
                               change["head_sha"], True, "deployed")
        row = session.get(Change, change["id"])
        assert not row.gates["preview"]["ok"] and "pr-41." in row.gates["preview"]["detail"]
    health["ok"] = True
    with db_session() as session:
        changes.record_preview(session, session.get(Change, change["id"]),
                               change["head_sha"], True, "deployed")
        assert session.get(Change, change["id"]).status == ChangeStatus.READY

    for scope, value in (("deploy", "prod-key"), ("preview", "preview-key")):
        assert client.post("/admin/secrets", headers=ADMIN, json={
            "project_id": project["id"], "name": f"{scope}-token", "value": value,
            "role_scopes": [scope]}).status_code == 200
    with db_session() as session:
        env = daemon._scoped_secret_env(session, session.get(Project, project["id"]), "preview")
    assert list(env.values()) == ["preview-key"]


def test_a_deploy_lost_mid_flight_fails_its_release_instead_of_wedging_it(client, gh, health):
    """A runner rebuilt during a deploy reaped the run; its release stayed in
    DEPLOYING, in flight, and every later release was refused."""
    from yaaf.core.constitution import effective
    from yaaf.core.models import Run, RunStatus
    from yaaf.server import janitor

    project = _factory(client)
    _shipped_change(client, project, gh)
    _with_prod(client, project)
    body = _propose(client, project, "0.1.0").json()
    client.post(f"/admin/approvals/{body['approval_id']}/decide", headers=ADMIN,
                json={"approve": True})
    (queued,) = _deploys(project)
    with db_session() as session:
        release = session.get(Release, queued.trigger["release_id"])
        releases.deploy_started(session, release, "deploy")
        session.add(Run(id=queued.id, project_id=project["id"], kind=RunKind.DEPLOY,
                        trigger=queued.trigger, status=RunStatus.RUNNING,
                        started_at=utcnow() - timedelta(hours=3),
                        heartbeat_at=utcnow() - timedelta(hours=3)))
        session.delete(session.get(RunQueue, queued.id))
    with db_session() as session:
        row = session.get(Project, project["id"])
        janitor.reap_stale_runs(session, row, effective(session, row.id))
    assert _release(queued.trigger["release_id"]).status == ReleaseStatus.FAILED


def test_the_ceo_is_shown_merged_work_waiting_to_go_out(client, gh, health):
    from tests.conftest import bearer, live_token

    project = _factory(client)
    _, ceo = live_token(project["id"], project["ceo_agent_id"])
    _shipped_change(client, project, gh)
    assert "release_owed" not in client.get("/agent/context", headers=bearer(ceo)).json(), \
        "no production environment: nothing could be released"
    _with_prod(client, project)
    owed = client.get("/agent/context", headers=bearer(ceo)).json()["release_owed"]
    assert owed["merged_since"] == 1 and owed["live"] is None
