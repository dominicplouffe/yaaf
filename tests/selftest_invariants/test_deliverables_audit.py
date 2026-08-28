"""Invariant selftests: deliverables with receipts, and the sampled audit.

The audit found 126 completed tasks, 20 side effects, and 315 of 315 evidence
rows saying "an agent claimed it" — not because attestation was missing but
because no project ever had a webhook secret; and "published" meant a storage
key prefix plus a filesystem mtime a restore would rewrite.

Stage 6's answers, each attacked here:

  * an artifact is a ROW — name, version, sha256, real publish time, and the
    requirements it claims to satisfy, validated against the project
  * evidence.task_id is a real reference — a pasted id is refused, and in the
    modes where work is tasks, taskless agent evidence is refused too
  * webhook secrets exist from birth, and no agent route can mint
    platform_attested provenance
  * the sampled audit draws AFTER the close, server-side, at the chairman's
    rate — no run token can see the draw coming, an expiry records "not
    reviewed" and never blocks
"""

from __future__ import annotations

import base64
import hashlib
from datetime import timedelta

from tests.conftest import ADMIN, agent_by_name, bearer, live_token, make_project
from tests.selftest_invariants.test_requirements import (
    OBS_TEST,
    accept,
    ceo_token,
    deliver_project,
    make_task,
    propose,
)
from yaaf.core.db import db_session
from yaaf.core.models import (
    Artifact,
    AuditSample,
    AuditSampleStatus,
    Evidence,
    EvidenceProvenance,
    Notice,
    NoticeKind,
    Project,
    Task,
    utcnow,
)


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def run_audit_tick(project: dict) -> None:
    from yaaf.core.constitution import effective
    from yaaf.server import janitor

    with db_session() as session:
        row = session.get(Project, project["id"])
        cons = effective(session, row.id)
        janitor.draw_audit_samples(session, row, cons)
        janitor.expire_audit_samples(session, row)


def done_observable_task(client, project: dict, token: str, rid: str) -> str:
    """A task closed on an observable test — the kind taken on the claimant's
    word, and the only kind the sampled audit considers."""
    task_id = make_task(client, token, project, rid).json()["id"]
    lead = agent_by_name(project, "Lead")
    _, lead_token = live_token(project["id"], lead)
    assert client.post(f"/agent/tasks/{task_id}/claim",
                       headers=bearer(lead_token)).status_code == 200
    done = client.post(f"/agent/tasks/{task_id}/complete",
                       json={"evidence": "https://example.org/report.pdf is live, checked"},
                       headers=bearer(lead_token))
    assert done.status_code == 200, done.text
    return task_id


# ── the deliverable is a row ────────────────────────────────────────────────────


def test_publishing_writes_a_receipt_with_version_and_hash(client):
    project = deliver_project(client)
    token = ceo_token(project)
    rid = propose(client, token)["requirement_id"]
    accept(client, rid, project)

    payload = b"quarterly report, final"
    first = client.post("/agent/artifacts",
                        json={"filename": "report.pdf", "content_base64": b64(payload),
                              "satisfies": ["R-01"]},
                        headers=bearer(token))
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["version"] == 1
    assert body["sha256"] == hashlib.sha256(payload).hexdigest()
    assert body["satisfies"] == ["R-01"]

    second = client.post("/agent/artifacts",
                         json={"filename": "report.pdf", "content_base64": b64(b"v2")},
                         headers=bearer(token))
    assert second.json()["version"] == 2, "same name is a new version, never an overwrite"

    files = client.get(f"/observe/projects/{project['id']}/files", headers=ADMIN).json()
    published = {(p["name"], p["version"]): p for p in files["published"]}
    assert published[("report.pdf", 1)]["satisfies"] == ["R-01"]
    assert published[("report.pdf", 1)]["sha256"] == body["sha256"]


def test_satisfies_must_name_real_scope(client):
    project = deliver_project(client)
    token = ceo_token(project)
    accept(client, propose(client, token)["requirement_id"], project)

    refused = client.post("/agent/artifacts",
                          json={"filename": "x.txt", "content_base64": b64(b"bytes"),
                                "satisfies": ["R-99"]},
                          headers=bearer(token))
    assert refused.status_code == 404
    assert "R-99" in refused.json()["error"]
    with db_session() as session:
        assert session.query(Artifact).count() == 0, "a refused publish writes no receipt"


def test_a_run_stamped_with_a_requirement_publishes_against_it_by_default(client):
    from yaaf.core.models import Run

    project = deliver_project(client)
    token = ceo_token(project)
    rid = propose(client, token)["requirement_id"]
    accept(client, rid, project)

    run_id, stamped_token = live_token(project["id"], project["ceo_agent_id"])
    with db_session() as session:
        session.get(Run, run_id).requirement_id = rid
    response = client.post("/agent/artifacts",
                           json={"filename": "out.csv", "content_base64": b64(b"a,b")},
                           headers=bearer(stamped_token))
    assert response.json()["satisfies"] == ["R-01"]


# ── evidence joins the record through real work ─────────────────────────────────


def test_taskless_agent_evidence_is_refused_where_work_is_tasks(client):
    project = deliver_project(client)
    token = ceo_token(project)
    rid = propose(client, token)["requirement_id"]
    accept(client, rid, project)

    refused = client.post("/agent/evidence",
                          json={"kind": "internal", "description": "we did a great job"},
                          headers=bearer(token))
    assert refused.status_code == 422
    assert "task_id" in refused.json()["error"]

    forged = client.post("/agent/evidence",
                         json={"kind": "internal", "description": "credit where none is due",
                               "task_id": "task_doesnotexist"},
                         headers=bearer(token))
    assert forged.status_code == 404

    task_id = make_task(client, token, project, rid).json()["id"]
    honest = client.post("/agent/evidence",
                         json={"kind": "internal", "description": "the report now builds",
                               "task_id": task_id},
                         headers=bearer(token))
    assert honest.status_code == 200, honest.text


def test_explore_evidence_stays_taskless_friendly(client):
    project = make_project(client, slug="explorer-ev")
    token = ceo_token(project)
    allowed = client.post("/agent/evidence",
                          json={"kind": "internal",
                                "description": "a market signal, tied to no task"},
                          headers=bearer(token))
    assert allowed.status_code == 200, allowed.text


def test_no_agent_route_can_mint_attested_provenance(client):
    project = make_project(client, slug="attest-try")
    token = ceo_token(project)
    smuggled = client.post(
        "/agent/evidence",
        json={"kind": "internal", "description": "trust me, this is attested",
              "provenance": "platform_attested",
              "verification": "not_applicable"},
        headers=bearer(token))
    assert smuggled.status_code == 200
    with db_session() as session:
        row = session.get(Evidence, smuggled.json()["id"])
        assert row.provenance == EvidenceProvenance.AGENT_CLAIMED, \
            "provenance is the platform's field; input that names it is ignored"


# ── webhook secrets exist from birth ────────────────────────────────────────────


def test_projects_are_born_with_webhook_secrets(client):
    project = make_project(client, slug="born-signed", start=False)
    hooks = project["webhooks"]
    assert hooks["github"]["url"].endswith(project["id"])
    assert len(hooks["github"]["secret"]) == 64
    assert len(hooks["generic"]["secret"]) == 64
    with db_session() as session:
        integrations = session.get(Project, project["id"]).integrations
        assert integrations["github_webhook_secret"] == hooks["github"]["secret"]
        assert integrations["webhook_secret"] == hooks["generic"]["secret"]


# ── the sampled audit ───────────────────────────────────────────────────────────


def sampled_project(client, rate: float = 1.0):
    project = deliver_project(client, slug="sampled",
                              constitution={"audit_sample_rate": rate})
    token = ceo_token(project)
    rid = propose(client, token, test=OBS_TEST)["requirement_id"]
    accept(client, rid, project)
    return project, token, rid


def test_an_observable_close_is_drawn_and_the_chairman_decides(client):
    project, token, rid = sampled_project(client, rate=1.0)
    task_id = done_observable_task(client, project, token, rid)
    run_audit_tick(project)

    desk = client.get(f"/observe/projects/{project['id']}/audit_samples",
                      headers=ADMIN).json()
    assert len(desk["pending"]) == 1
    sample = desk["pending"][0]
    assert sample["task_id"] == task_id
    assert sample["expires_at"] is not None

    failed = client.post(f"/admin/audit_samples/{sample['id']}/decide",
                         json={"ok": False, "note": "the report is three bullet points"},
                         headers=ADMIN)
    assert failed.status_code == 200
    with db_session() as session:
        task = session.get(Task, task_id)
        assert task.audit_status == "failed"
        assert task.status == "done", "sampling never reopens work by itself"
        notices = session.query(Notice).filter_by(
            kind=NoticeKind.AUDIT_FAILED, project_id=project["id"]).count()
        assert notices == 1, "a failed sample lands in the CEO's next pack"


def test_the_draw_happens_once_and_command_closes_are_never_sampled(client, monkeypatch):
    from yaaf.server import janitor

    project, token, rid = sampled_project(client, rate=0.5)
    task_id = done_observable_task(client, project, token, rid)

    monkeypatch.setattr(janitor.random, "random", lambda: 0.99)  # above the rate
    run_audit_tick(project)
    run_audit_tick(project)  # a skipped close is recorded, never re-rolled
    with db_session() as session:
        rows = session.query(AuditSample).filter_by(project_id=project["id"]).all()
        assert len(rows) == 1
        assert rows[0].status == AuditSampleStatus.SKIPPED
        assert rows[0].task_id == task_id


def test_expiry_records_not_reviewed_and_blocks_nothing(client):
    project, token, rid = sampled_project(client, rate=1.0)
    done_observable_task(client, project, token, rid)
    run_audit_tick(project)

    with db_session() as session:
        sample = session.query(AuditSample).filter_by(project_id=project["id"]).one()
        sample.expires_at = utcnow() - timedelta(hours=1)
        sample_id = sample.id
    run_audit_tick(project)
    with db_session() as session:
        sample = session.get(AuditSample, sample_id)
        assert sample.status == AuditSampleStatus.EXPIRED
        assert sample.note == "not reviewed in time"

    late = client.post(f"/admin/audit_samples/{sample_id}/decide",
                       json={"ok": True}, headers=ADMIN)
    assert late.status_code == 409, "the moment passed; a late verdict would make coverage lie"

    # Never blocks: the project keeps working exactly as before.
    another = make_task(client, ceo_token(project), project, rid)
    assert another.status_code == 200, another.text


def test_the_draw_is_invisible_to_every_run_token(client):
    project, token, rid = sampled_project(client, rate=1.0)
    task_id = done_observable_task(client, project, token, rid)
    run_audit_tick(project)

    lead = agent_by_name(project, "Lead")
    _, lead_token = live_token(project["id"], lead)

    # No agent route serves samples — the verb does not exist.
    for path in ("/agent/audit_samples", f"/agent/audit_samples/{task_id}"):
        response = client.get(path, headers=bearer(lead_token))
        assert response.status_code in (404, 405)

    # And nothing about the draw leaks into what the audited agent reads.
    from yaaf.core.models import Agent, Run, RunStatus
    from yaaf.server.contextpack import build_context_pack

    run_id, _ = live_token(project["id"], lead)
    with db_session() as session:
        run = session.get(Run, run_id)
        run.status = RunStatus.RUNNING
        pack = build_context_pack(
            session, run, session.get(Agent, lead),
            session.get(Project, project["id"]))
    assert "audit_sample" not in str(pack).lower()
    assert "asmp_" not in str(pack)


# ── skills staging (host side; the sandbox side needs the SDK image) ───────────


def test_staged_skills_are_sanitized_and_platform_owned(tmp_path):
    from yaaf.runner.backends.agent_sdk import stage_skills

    stage_skills(tmp_path, [
        {"name": "Write Better Emails", "description": "tone guide", "body": "Be brief."},
        {"name": "../../etc/evil", "body": "x"},
        {"name": "", "body": "anonymous"},
        {"name": "no-body-no-skill", "body": ""},
    ])
    staged = sorted(p.name for p in (tmp_path / ".claude" / "skills").iterdir())
    assert staged == ["etcevil", "write-better-emails"], \
        "names are slugs inside the skills dir — traversal cannot escape it"
    text = (tmp_path / ".claude" / "skills" / "write-better-emails" / "SKILL.md").read_text()
    assert "tone guide" in text and "Be brief." in text

    # The platform owns the directory: restaging drops what the role no longer defines.
    stage_skills(tmp_path, [{"name": "only-this", "body": "one"}])
    staged = sorted(p.name for p in (tmp_path / ".claude" / "skills").iterdir())
    assert staged == ["only-this"]
