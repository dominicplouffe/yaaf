"""Code Factory releases: proposed by a seat, shipped and undone by the platform.

A release points at a commit the platform merged, and nothing else. Whether
it needs the chairman's yes is the `release_authority` rule: "approve_first"
keeps the first production release and every major one with him (as an
approval that silence never grants); "autonomous" ships them all and tells
him after. Either way the platform runs the deploy itself, with credentials
granted to the reserved scope "deploy" — a scope no seat's role is — then
reads the environment's health URL for the canary window. Enough failed
reads and it deploys the previous release again and opens an incident,
without asking anyone. Undoing damage is never a decision that waits.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import UTC, timedelta

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from yaaf.core import ids, shape
from yaaf.core.constitution import ConstitutionDoc, effective
from yaaf.core.models import (
    Agent,
    Approval,
    ApprovalKind,
    ApprovalStatus,
    AuditLog,
    Change,
    ChangeStatus,
    Incident,
    Notice,
    NoticeKind,
    Project,
    ProjectStatus,
    Release,
    ReleaseStatus,
    RunKind,
    RunQueue,
    utcnow,
)
from yaaf.server.policy import Refusal

log = logging.getLogger(__name__)

SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
#: The secret scope a deploy runs with. Reserved: no role may be named it, so
#: no seat's sandbox ever receives what production is deployed with.
DEPLOY_SCOPE = "deploy"
#: The scope a per-change preview deploys with. Reserved as well, and never
#: the production one: a preview builds code nobody has signed yet.
PREVIEW_SCOPE = "preview"
RESERVED_SCOPES = (DEPLOY_SCOPE, PREVIEW_SCOPE)
IN_FLIGHT = (ReleaseStatus.PROPOSED, ReleaseStatus.APPROVED, ReleaseStatus.DEPLOYING,
             ReleaseStatus.WATCHING)


def environment(project: Project, name: str = "production") -> dict | None:
    env = ((project.integrations or {}).get("environments") or {}).get(name)
    return env if isinstance(env, dict) and env.get("deploy") and env.get("health_url") else None


def _version(raw: str) -> tuple[int, int, int]:
    m = SEMVER.match(raw.strip())
    if not m:
        raise Refusal("A version is MAJOR.MINOR.PATCH, like 1.4.2 — what changed, said "
                      "in a form tools and people both read.", status_code=422)
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def last_live(session: Session, project_id: str) -> Release | None:
    return session.execute(
        select(Release).where(Release.project_id == project_id,
                              Release.status == ReleaseStatus.LIVE)
        .order_by(Release.live_at.desc())
    ).scalars().first()


def _ever_live(session: Session, project_id: str) -> bool:
    return session.execute(
        select(func.count(Release.id)).where(
            Release.project_id == project_id,
            Release.status.in_((ReleaseStatus.LIVE, ReleaseStatus.ROLLED_BACK)))
    ).scalar_one() > 0


def needs_chairman(session: Session, project: Project, cons: ConstitutionDoc,
                   version: tuple[int, int, int]) -> str | None:
    """Why this release waits for the chairman, or None. The reason is said
    on the approval, because "the dial says so" teaches him nothing."""
    if cons.release_authority == "autonomous":
        return None
    previous = last_live(session, project.id)
    if not _ever_live(session, project.id):
        return "it is the first release to production, which you keep"
    if previous is not None and version[0] > _version(previous.version)[0]:
        return f"it is a major release ({previous.version} → {'.'.join(map(str, version))})"
    return None


# ── proposing ───────────────────────────────────────────────────────────────────


def propose(session: Session, project: Project, agent: Agent, *, version: str,
            title: str) -> Release:
    from yaaf.server import changes

    changes.require_factory(project)
    if project.status != ProjectStatus.RUNNING:
        raise Refusal("The project is not running.", status_code=409)
    if environment(project) is None:
        raise Refusal(
            "There is no production environment to release to: the chairman sets its "
            "deploy command and health URL in Settings → Environments. Until then the "
            "factory ships a repo, not a running service.", status_code=409)
    if changes.merges_frozen(session, project) is not None:
        raise Refusal("Main moved without the platform and that is not answered yet; "
                      "nothing ships from a main nobody checked.", status_code=409)
    busy = session.execute(
        select(Release).where(Release.project_id == project.id,
                              Release.status.in_(IN_FLIGHT))
    ).scalars().first()
    if busy is not None:
        raise Refusal(f"Release {busy.version} is still {busy.status}. One release at a "
                      "time: production changes in steps a guard can tell apart.",
                      status_code=409)
    wanted = _version(version)
    previous = last_live(session, project.id)
    if previous is not None and wanted <= _version(previous.version):
        raise Refusal(f"{version} is not after {previous.version}, which is live.",
                      status_code=422)
    since = previous.created_at if previous is not None else None
    query = select(Change).where(Change.project_id == project.id,
                                 Change.status == ChangeStatus.MERGED)
    if since is not None:
        query = query.where(Change.merged_at > since)
    merged = list(session.execute(query.order_by(Change.merged_at)).scalars())
    if not merged:
        raise Refusal("Nothing has merged since the last release, so this one would "
                      "ship nothing new.", status_code=409)
    head = merged[-1]
    notes = "\n".join(f"- {c.title} (#{c.number})" for c in merged)
    release = Release(
        id=ids.new_id(ids.RELEASE), project_id=project.id,
        version=".".join(map(str, wanted)), title=title.strip()[:300], sha=head.merged_sha or "",
        notes=notes, change_ids=[c.id for c in merged], proposed_by_agent_id=agent.id,
        previous_release_id=previous.id if previous else None, probes={},
        status=ReleaseStatus.PROPOSED)
    session.add(release)
    session.flush()
    if environment(project, "staging") is not None:
        # Staging first, and without asking: nothing there is anyone's yet.
        # The chairman's question, when the dial asks one, comes after it
        # has run clean — so he is asked about something that has run.
        release.environment = "staging"
        release.status = ReleaseStatus.APPROVED
        _audit(session, project, "release.staging", release, {})
        queue_deploy(session, project, release, "deploy")
        return release
    _to_production(session, project, release)
    return release


def _to_production(session: Session, project: Project, release: Release) -> None:
    """Point a release at production: straight to the deploy when the dial
    allows, or to the chairman as an approval silence never grants."""
    cons = effective(session, project.id)
    release.environment = "production"
    why = needs_chairman(session, project, cons, _version(release.version))
    if why is None:
        _audit(session, project, "release.approved", release, {"by": "the dial"})
        release.status = ReleaseStatus.APPROVED
        queue_deploy(session, project, release, "deploy")
        return
    agent = session.get(Agent, release.proposed_by_agent_id)
    staged = (" It ran clean on staging for the canary window first."
              if (release.probes or {}).get("staging_ok") else "")
    approval = Approval(
        id=ids.new_id(ids.APPROVAL), project_id=project.id, kind=ApprovalKind.RELEASE,
        requested_by_agent_id=release.proposed_by_agent_id,
        title=f"Release {release.version} to production: {release.title}"[:300],
        body=(f"{agent.name if agent else 'A seat'} proposes releasing {release.version} "
              f"({len(release.change_ids or [])} change(s), commit {release.sha[:12]}). It "
              f"waits for you because {why}.{staged} Every change in it passed the merge "
              "gate; the canary guard will watch it and roll it back on its own."
              f"\n\n{release.notes}"),
        status=ApprovalStatus.PENDING,
        expires_at=utcnow() + timedelta(days=cons.approval_timeout_days))
    session.add(approval)
    release.approval_id = approval.id
    release.status = ReleaseStatus.PROPOSED
    _audit(session, project, "release.proposed", release, {"waits_because": why})


def decided(session: Session, project: Project, approval: Approval, approve: bool,
            note: str | None) -> Release | None:
    """The chairman's answer to a release approval."""
    release = session.execute(
        select(Release).where(Release.approval_id == approval.id)).scalars().first()
    if release is None or release.status != ReleaseStatus.PROPOSED:
        return release
    if approve:
        release.status = ReleaseStatus.APPROVED
        _audit(session, project, "release.approved", release, {"by": "the chairman"})
        queue_deploy(session, project, release, "deploy")
    else:
        release.status = ReleaseStatus.REFUSED
        release.status_reason = note or "the chairman said no"
        _audit(session, project, "release.refused", release, {"note": note})
    _tell(session, project, release.proposed_by_agent_id,
          f"Release {release.version} was {'approved' if approve else 'refused'} by the "
          "chairman" + (f": {note}" if note else "."))
    return release


# ── deploying ───────────────────────────────────────────────────────────────────


def queue_deploy(session: Session, project: Project, release: Release, action: str,
                 *, sha: str | None = None) -> None:
    session.add(RunQueue(
        id=ids.new_id(ids.RUN), project_id=project.id, agent_id=None, kind=RunKind.DEPLOY,
        trigger={"type": "deploy", "release_id": release.id, "action": action,
                 "sha": sha or release.sha,
                 "environment": "production" if action == "rollback" else release.environment}))


def deploy_started(session: Session, release: Release, action: str) -> None:
    if action == "deploy" and release.status == ReleaseStatus.APPROVED:
        release.status = ReleaseStatus.DEPLOYING


def deployed(session: Session, release: Release, action: str, ok: bool, output: str,
             sbom: bytes | None = None) -> None:
    """What the deploy command did. A rollback's own result is recorded on the
    incident's timeline; a release's starts the canary watch or fails it."""
    project = session.get(Project, release.project_id)
    if project is None:
        return
    if action == "rollback":
        incident = _incident_for(session, release)
        if incident is not None:
            _event(incident, "the previous release is back in production" if ok
                   else f"the rollback deploy FAILED: {output[-300:]}")
        if not ok:
            from yaaf.server.notify import Attention, chairman_notify

            chairman_notify(session, project, f"rollback of {release.version} failed",
                            "The platform could not put the previous release back. "
                            f"Production is still on {release.version}, which broke its "
                            "guard. Someone needs to act now.\n\n" + output[-1500:],
                            attention=Attention.ALARM)
        return
    release.deploy_output = output[-8000:]
    if sbom and release.environment == "production":
        from yaaf.core import artifacts

        release.sbom_uri = artifacts.store().put_bytes(
            f"projects/{project.id}/releases/{release.id}/sbom.cdx.json", sbom)
    if not ok:
        release.status = ReleaseStatus.FAILED
        release.status_reason = "the deploy command failed"
        _tell(session, project, release.proposed_by_agent_id,
              f"Release {release.version} did not deploy: the command failed. "
              f"{output[-1500:]}")
        _audit(session, project, "release.failed", release, {})
        return
    release.status = ReleaseStatus.WATCHING
    release.deployed_at = utcnow()
    release.probes = {"ok": 0, "failed": 0}
    _audit(session, project, "release.deployed", release, {})


# ── the canary guard ────────────────────────────────────────────────────────────


def probe(url: str) -> tuple[bool, str]:
    """One read of production health. The single seam tests replace."""
    try:
        response = httpx.get(url, timeout=10.0, follow_redirects=True)
    except httpx.HTTPError as exc:
        return False, type(exc).__name__
    return 200 <= response.status_code < 300, f"HTTP {response.status_code}"


def watch(session: Session, project: Project) -> None:
    """Each janitor tick: refuse releases whose approval lapsed, read health
    for the one being watched, and roll back or call it live."""
    if not shape.is_factory(project) or project.status != ProjectStatus.RUNNING:
        return
    for release in session.execute(
        select(Release).where(Release.project_id == project.id,
                              Release.status == ReleaseStatus.PROPOSED)
    ).scalars():
        approval = session.get(Approval, release.approval_id) if release.approval_id else None
        if approval is not None and approval.status not in (ApprovalStatus.PENDING,
                                                            ApprovalStatus.APPROVED):
            release.status = ReleaseStatus.REFUSED
            release.status_reason = f"the approval {approval.status}; silence ships nothing"
    release = session.execute(
        select(Release).where(Release.project_id == project.id,
                              Release.status == ReleaseStatus.WATCHING)
    ).scalars().first()
    if release is None:
        return
    env = environment(project, release.environment)
    cons = effective(session, project.id)
    if env is None:
        return
    ok, detail = probe(env["health_url"])
    probes = dict(release.probes or {})
    probes["ok" if ok else "failed"] = probes.get("ok" if ok else "failed", 0) + 1
    probes["last"] = {"ok": ok, "detail": detail, "at": utcnow().isoformat()}
    release.probes = probes
    if probes.get("failed", 0) >= cons.canary_max_failures and \
            release.environment == "staging":
        # Staging broke: production never saw it, so there is nothing to undo
        # and no incident — the release simply does not go further.
        release.status = ReleaseStatus.FAILED
        release.status_reason = (f"staging broke its guard: {probes['failed']} failed health "
                                 f"reads; last: {detail}. Production was never touched.")
        _audit(session, project, "release.staging_failed", release, {})
        _tell(session, project, release.proposed_by_agent_id,
              f"Release {release.version} failed on staging and stops there. "
              f"{release.status_reason}")
        return
    if probes.get("failed", 0) >= cons.canary_max_failures:
        roll_back(session, project, release,
                  f"{probes['failed']} failed health reads during the canary watch "
                  f"(limit {cons.canary_max_failures}); last: {detail}")
        return
    deployed_at = release.deployed_at
    if deployed_at is not None and deployed_at.tzinfo is None:
        deployed_at = deployed_at.replace(tzinfo=UTC)
    if deployed_at is not None and utcnow() - deployed_at >= timedelta(
            minutes=cons.canary_minutes):
        if release.environment == "staging":
            release.probes = {"staging_ok": probes.get("ok", 0)}
            release.deployed_at = None
            _audit(session, project, "release.staging_passed", release, {})
            _to_production(session, project, release)
            return
        _go_live(session, project, release)


def _go_live(session: Session, project: Project, release: Release) -> None:
    from yaaf.server import github, provenance

    release.status = ReleaseStatus.LIVE
    release.live_at = utcnow()
    provenance.sign_release(session, project, release, provenance.sbom_bytes(release))
    _audit(session, project, "release.live", release, {"probes": release.probes})
    digest = hashlib.sha256(provenance.canonical(release.provenance)).hexdigest()
    signed = ("\n\n---\nProvenance: signed by this yaaf install (Ed25519), statement "
              f"sha256 {digest[:16]}…"
              + ("; SBOM kept with the release record." if release.sbom_uri else "."))
    try:
        github.create_release(session, project.github_repo or "", tag=f"v{release.version}",
                              sha=release.sha, name=f"v{release.version} — {release.title}",
                              body=release.notes + signed)
    except github.GithubError as exc:
        log.warning("could not tag %s on GitHub: %s", release.version, exc)
    _tell(session, project, release.proposed_by_agent_id,
          f"Release {release.version} is live: the canary watch passed "
          f"({(release.probes or {}).get('ok', 0)} healthy reads).")


def roll_back(session: Session, project: Project, release: Release, why: str) -> Incident:
    """Put the previous release back, open an incident, tell everyone — in that
    order, and without waiting for anyone's yes."""
    from yaaf.server.notify import Attention, chairman_notify

    release.status = ReleaseStatus.ROLLED_BACK
    release.rolled_back_at = utcnow()
    release.status_reason = why
    previous = session.get(Release, release.previous_release_id) \
        if release.previous_release_id else None
    if previous is not None:
        queue_deploy(session, project, release, "rollback", sha=previous.sha)
    incident = open_incident(
        session, project, title=f"Release {release.version} broke its canary guard",
        opened_by="canary", release=release,
        first=(f"guard broken: {why}. " + (f"Rolling back to {previous.version}."
                                           if previous else
                                           "There is no earlier release to put back.")))
    _audit(session, project, "release.rolled_back", release, {"why": why})
    chairman_notify(session, project, f"{release.version} rolled back",
                    f"The canary guard broke on {release.version}: {why}. The platform "
                    + (f"is putting {previous.version} back" if previous
                       else "had no earlier release to put back")
                    + f" and opened incident {incident.number}. Nobody was asked first; "
                    "that is the rule.", attention=Attention.ALARM)
    return incident


# ── incidents ───────────────────────────────────────────────────────────────────


def open_incident(session: Session, project: Project, *, title: str, opened_by: str,
                  release: Release | None = None, first: str = "",
                  severity: int = 2) -> Incident:
    number = (session.execute(
        select(func.max(Incident.number)).where(Incident.project_id == project.id)
    ).scalar_one() or 0) + 1
    incident = Incident(
        id=ids.new_id(ids.INCIDENT), project_id=project.id,
        release_id=release.id if release else None, number=number, title=title[:300],
        severity=severity, status="open", opened_by=opened_by, timeline=[])
    _event(incident, first or "opened")
    session.add(incident)
    from yaaf.server import policy

    ceo = policy.ceo_of(session, project.id)
    if ceo is not None:
        session.add(Notice(
            id=ids.new_id(ids.NOTICE), project_id=project.id, agent_id=ceo.id,
            kind=NoticeKind.INCIDENT_OPENED,
            body=(f"Incident {number}: {title}. The platform has already acted on what it "
                  "can (see the timeline). Find the cause, ship the fix through the gates, "
                  "and write the postmortem with incident_postmortem — causes, not "
                  "blame.")))
    return incident


def write_postmortem(session: Session, project: Project, incident: Incident,
                     text: str) -> None:
    if len(text.strip()) < 80:
        raise Refusal("A postmortem says what happened, why, and what changes so it "
                      "cannot happen the same way again. That is more than a line.",
                      status_code=422)
    incident.postmortem = text.strip()[:20000]
    incident.status = "resolved"
    incident.resolved_at = utcnow()
    _event(incident, "postmortem written; resolved")


def _event(incident: Incident, what: str) -> None:
    incident.timeline = [*(incident.timeline or []), {"at": utcnow().isoformat(), "what": what}]


def _incident_for(session: Session, release: Release) -> Incident | None:
    return session.execute(
        select(Incident).where(Incident.release_id == release.id)
        .order_by(Incident.opened_at.desc())
    ).scalars().first()


# ── reading ─────────────────────────────────────────────────────────────────────


def view(release: Release) -> dict:
    return {"id": release.id, "version": release.version, "title": release.title,
            "sha": release.sha, "notes": release.notes, "changes": release.change_ids,
            "status": release.status, "status_reason": release.status_reason,
            "environment": release.environment, "sbom": bool(release.sbom_uri),
            "provenance": release.provenance, "provenance_sig": release.provenance_sig,
            "approval_id": release.approval_id, "probes": release.probes or {},
            "deployed_at": release.deployed_at.isoformat() if release.deployed_at else None,
            "live_at": release.live_at.isoformat() if release.live_at else None,
            "rolled_back_at": (release.rolled_back_at.isoformat()
                               if release.rolled_back_at else None),
            "created_at": release.created_at.isoformat() if release.created_at else None}


def incident_view(incident: Incident) -> dict:
    return {"id": incident.id, "number": incident.number, "title": incident.title,
            "severity": incident.severity, "status": incident.status,
            "opened_by": incident.opened_by, "release_id": incident.release_id,
            "timeline": incident.timeline or [], "postmortem": incident.postmortem,
            "opened_at": incident.opened_at.isoformat() if incident.opened_at else None,
            "resolved_at": incident.resolved_at.isoformat() if incident.resolved_at else None}


def listing(session: Session, project: Project) -> dict:
    return {
        "releases": [view(r) for r in session.execute(
            select(Release).where(Release.project_id == project.id)
            .order_by(Release.created_at.desc()).limit(100)).scalars()],
        "incidents": [incident_view(i) for i in session.execute(
            select(Incident).where(Incident.project_id == project.id)
            .order_by(Incident.opened_at.desc()).limit(100)).scalars()],
        "production": environment(project) is not None,
    }


def _audit(session: Session, project: Project, action: str, release: Release,
           detail: dict) -> None:
    session.add(AuditLog(
        id=ids.new_id(ids.AUDIT), project_id=project.id, actor="system:releases",
        action=action, entity="release", entity_id=release.id,
        detail={"version": release.version, **detail}))


def _tell(session: Session, project: Project, agent_id: str, body: str) -> None:
    session.add(Notice(id=ids.new_id(ids.NOTICE), project_id=project.id, agent_id=agent_id,
                       kind=NoticeKind.RELEASE_UPDATE, body=body))
