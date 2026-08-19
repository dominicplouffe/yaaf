"""The topology experiment: the chairman's suspicion, made answerable.

Three org shapes — solo, ceo_team, ceo_leads_workers — get the same goal, the
same chairman-authored requirement set, and the same constitution, run
concurrently and compared on numbers no arm can flatter:

  * cost per requirement met — the headline
  * wall-clock to first platform-attested evidence — how fast the outside
    world first confirmed anything
  * share of spend that produced nothing — dollars into runs that died
  * internal words per delivered byte — how much talking it took per byte
    that actually shipped

One producer for the metrics (this module), read by the observe API and any
future report, so two screens can never grade the same arm differently. The
known asymmetry rides every reading instead of hiding: solo is exempt from
requester-owns-the-test, so a solo arm self-grades its observables.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from yaaf.core import ids
from yaaf.core.models import (
    Artifact,
    Evidence,
    EvidenceProvenance,
    Experiment,
    ExperimentArm,
    Message,
    Project,
    ProjectStatus,
    RequirementStatus,
    Run,
    RunKind,
    utcnow,
)
from yaaf.server import policy
from yaaf.server.notify import chairman_notify
from yaaf.server.velocity import DEAD_STATUSES

log = logging.getLogger("yaaf.experiments")

SOLO_ASYMMETRY = (
    "solo is exempt from requester-owns-the-test (with one agent there is nobody "
    "to hand work to), so every observable close in the solo arm is self-graded. "
    "Read its numbers with that discount — it is a real cost of the topology, "
    "not a tie."
)


def arm_metrics(session: Session, project: Project) -> dict:
    """The four numbers for one arm. None where a denominator is honestly
    zero — 'infinite cost per met' helps nobody, and a made-up number would
    be worse."""
    met = len([
        r for r in policy.scope_requirements(session, project.id, all_cycles=True)
        if r.status == RequirementStatus.MET
    ])
    runs = session.execute(
        select(Run.status, Run.cost_usd).where(
            Run.project_id == project.id, Run.kind != RunKind.CHAT_SESSION)
    ).all()
    total_usd = sum(cost or 0.0 for _, cost in runs)
    dead_usd = sum(cost or 0.0 for status, cost in runs if status in DEAD_STATUSES)

    first_attested = session.execute(
        select(func.min(Evidence.created_at)).where(
            Evidence.project_id == project.id,
            Evidence.provenance == EvidenceProvenance.PLATFORM_ATTESTED)
    ).scalar_one()
    started = project.created_at
    hours_to_attested = (
        round((first_attested - started).total_seconds() / 3600, 1)
        if first_attested is not None else None)

    internal_words = sum(
        len((body or "").split())
        for (body,) in session.execute(
            select(Message.body).where(Message.project_id == project.id,
                                       Message.sender_agent_id.is_not(None)))
    )
    delivered_bytes = session.execute(
        select(func.coalesce(func.sum(Artifact.size_bytes), 0)).where(
            Artifact.project_id == project.id)
    ).scalar_one()

    return {
        "requirements_met": met,
        "runs": len(runs),
        "spend_usd": round(total_usd, 2),
        "cost_per_met_usd": round(total_usd / met, 2) if met else None,
        "hours_to_first_attested_evidence": hours_to_attested,
        "share_of_spend_producing_nothing": (
            round(dead_usd / total_usd, 3) if total_usd else None),
        "internal_words": int(internal_words),
        "delivered_bytes": int(delivered_bytes),
        "words_per_delivered_byte": (
            round(internal_words / delivered_bytes, 3) if delivered_bytes else None),
    }


def check_experiment_arms(session: Session, project: Project) -> None:
    """The stop rule: an arm that breached its budget with nothing met is
    halted and reported, never left burning. Runs on every janitor tick for
    every project — including paused ones, because budget exhaustion pauses
    first and the stop rule is the verdict on top of it."""
    arm = session.execute(
        select(ExperimentArm).where(ExperimentArm.project_id == project.id,
                                    ExperimentArm.stopped_reason.is_(None))
    ).scalar_one_or_none()
    if arm is None:
        return
    if project.status != ProjectStatus.PAUSED:
        return
    if "budget exhausted" not in (project.status_reason or ""):
        return
    met = [
        r for r in policy.scope_requirements(session, project.id, all_cycles=True)
        if r.status == RequirementStatus.MET
    ]
    if met:
        return  # it earned its pause; the chairman decides, not the rule
    experiment = session.get(Experiment, arm.experiment_id)
    arm.stopped_reason = "budget breached with zero requirements met"
    project.status = ProjectStatus.STOPPED
    project.status_reason = f"experiment stop rule: {arm.stopped_reason}"
    from yaaf.core.models import AuditLog

    session.add(AuditLog(
        id=ids.new_id(ids.AUDIT), project_id=project.id, actor="system:janitor",
        action="experiment.arm_stopped", entity="project", entity_id=project.id,
        detail={"experiment_id": arm.experiment_id, "topology": arm.topology},
    ))
    chairman_notify(
        session, project,
        f"experiment arm stopped: {arm.topology}",
        f"The {arm.topology} arm of "
        f"'{experiment.name if experiment else arm.experiment_id}' breached its "
        "budget with zero requirements met, so the platform halted it. That is a "
        "result, not a failure of the experiment — an arm that burns its whole "
        "budget producing nothing has answered the question for its topology.",
    )
    log.warning("experiment arm %s (%s) stopped by the stop rule",
                arm.id, arm.topology)


def experiment_report(session: Session, experiment: Experiment) -> dict:
    """One experiment, all arms, same producer. The asymmetry note rides
    every report that includes a solo arm."""
    arms = list(session.execute(
        select(ExperimentArm).where(ExperimentArm.experiment_id == experiment.id)
        .order_by(ExperimentArm.created_at)
    ).scalars())
    out = []
    for arm in arms:
        project = session.get(Project, arm.project_id)
        if project is None:
            continue
        out.append({
            "arm_id": arm.id,
            "topology": arm.topology,
            "project_id": project.id,
            "project_slug": project.slug,
            "project_status": project.status,
            "status_reason": project.status_reason,
            "stopped_reason": arm.stopped_reason,
            "metrics": arm_metrics(session, project),
        })
    return {
        "id": experiment.id,
        "name": experiment.name,
        "goal": experiment.goal,
        "status": experiment.status,
        "created_at": experiment.created_at.isoformat(),
        "requirement_set": experiment.requirement_set,
        "arms": out,
        "asymmetries": ([SOLO_ASYMMETRY]
                        if any(a["topology"] == "solo" for a in out) else []),
        "as_of": utcnow().isoformat(),
    }
