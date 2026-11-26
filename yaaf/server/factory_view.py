"""A Code Factory's home-page summary: the line, stage by stage, and the
numbers that say whether it is a good factory. Every figure is counted from
rows the platform wrote — changes, releases, incidents, runs, cycles — so the
page can be read as the platform's account, not the crew's."""

from __future__ import annotations

from datetime import UTC, timedelta
from statistics import median

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from yaaf.core.models import (
    Change,
    ChangeStatus,
    Cycle,
    CycleStatus,
    Incident,
    Project,
    Release,
    ReleaseStatus,
    Requirement,
    RequirementStatus,
    Run,
    Task,
    TaskStatus,
    utcnow,
)
from yaaf.server import policy, upkeep


def _aware(at):
    return at if at is None or at.tzinfo else at.replace(tzinfo=UTC)


def summary(session: Session, project: Project) -> dict:
    now = utcnow()
    month = now - timedelta(days=30)
    week = now - timedelta(days=7)
    changes = list(session.execute(
        select(Change).where(Change.project_id == project.id)).scalars())
    merged = [c for c in changes if c.status == ChangeStatus.MERGED and c.merged_at]
    merged_week = [c for c in merged if _aware(c.merged_at) >= week]
    merged_month = [c for c in merged if _aware(c.merged_at) >= month]
    live = [c for c in changes if c.status in (ChangeStatus.OPEN, ChangeStatus.READY)]
    in_review = [c for c in live if not (c.gates or {}).get("review", {}).get("ok")
                 and all(g.get("ok") for k, g in (c.gates or {}).items() if k != "review")]
    checking = [c for c in live if c not in in_review and c.status == ChangeStatus.OPEN]

    reqs = [r for r in session.execute(select(Requirement).where(
        Requirement.project_id == project.id)).scalars()
        if r.key != policy.PLAN_KEY and not upkeep.is_upkeep_promise(r)]
    accepted = [r for r in reqs if r.status not in (RequirementStatus.PROPOSED,
                                                    RequirementStatus.CUT)]
    proposed = [r for r in reqs if r.status == RequirementStatus.PROPOSED]
    met = [r for r in accepted if r.status == RequirementStatus.MET]
    building = session.execute(select(func.count(Task.id)).where(
        Task.project_id == project.id, Task.status == TaskStatus.CLAIMED)).scalar_one()

    releases = list(session.execute(select(Release).where(
        Release.project_id == project.id).order_by(Release.created_at.desc())).scalars())
    in_flight = next((r for r in releases if r.status in (
        ReleaseStatus.PROPOSED, ReleaseStatus.APPROVED, ReleaseStatus.DEPLOYING,
        ReleaseStatus.WATCHING)), None)
    shipped = [r for r in releases if r.status in (ReleaseStatus.LIVE, ReleaseStatus.ROLLED_BACK)
               and r.deployed_at and _aware(r.deployed_at) >= month]
    rolled = [r for r in shipped if r.status == ReleaseStatus.ROLLED_BACK]
    incidents = list(session.execute(select(Incident).where(
        Incident.project_id == project.id)).scalars())
    open_incidents = [i for i in incidents if i.status == "open"]
    recover = [(_aware(i.resolved_at) - _aware(i.opened_at)).total_seconds() / 3600
               for i in incidents if i.resolved_at]

    last_cycle = session.execute(select(Cycle).where(
        Cycle.project_id == project.id, Cycle.status != CycleStatus.OPEN)
        .order_by(Cycle.ordinal.desc())).scalars().first()
    standing = ((last_cycle.scorecard or {}).get("standing") or []) if last_cycle else []
    promise_keys = {r.key for r in session.execute(
        select(Requirement).where(Requirement.project_id == project.id)).scalars()
        if upkeep.is_upkeep_promise(r)}
    promises = [row for row in standing if row.get("key") in promise_keys]
    kept = sum(1 for row in promises if row.get("verdict") == "honoured")

    hours = [(_aware(c.merged_at) - _aware(c.created_at)).total_seconds() / 3600
             for c in merged_month if c.created_at]
    reviewed = [c for c in changes if c.review_verdict or c.returns]
    cost = float(session.execute(select(func.coalesce(func.sum(Run.cost_usd), 0.0)).where(
        Run.project_id == project.id, Run.created_at >= month)).scalar_one())
    scenarios = {"pass": 0, "total": 0}
    for c in live:
        results = (c.scenario_results or {}).get("results") or {}
        scenarios["total"] += len(results)
        scenarios["pass"] += sum(1 for ok in results.values() if ok)

    def stage(name, state, count, detail):
        return {"stage": name, "state": state, "count": count, "detail": detail}

    upkeep_phase = project.factory_phase == "upkeep"
    rail = [
        stage("Spec", "waiting" if proposed else "done" if accepted else "idle",
              len(accepted), f"{len(accepted)} accepted · {len(proposed)} proposed"),
        stage("Build", "now" if building else "idle", building, f"{building} in work"),
        stage("Check", "now" if checking else "idle", len(checking),
              f"{len(checking)} waiting on checks"),
        stage("Review", "now" if in_review else "idle", len(in_review),
              f"{len(in_review)} waiting for a reader"),
        stage("Release", "waiting" if in_flight and in_flight.status == ReleaseStatus.PROPOSED
              else "now" if in_flight else "done" if shipped else "idle",
              1 if in_flight else 0,
              f"{in_flight.version} {in_flight.status.replace('_', ' ')}" if in_flight
              else f"{len(shipped)} shipped this month"),
        stage("Run", "waiting" if open_incidents else "done" if any(
            r.status == ReleaseStatus.LIVE for r in releases) else "idle",
              len(open_incidents),
              f"{len(open_incidents)} open incident(s)" if open_incidents
              else "healthy" if releases else "not running yet"),
        stage("Maintain", "now" if upkeep_phase else "idle", kept,
              (f"{kept} of {len(promises)} promises kept last cycle" if promises
               else "first cycle running" if upkeep_phase else "after v1.0")),
    ]
    return {
        "phase": project.factory_phase,
        "rail": rail,
        "vitals": {
            "requirements_met": len(met), "requirements": len(accepted),
            "merged_week": len(merged_week), "merged_total": len(merged),
            "branch_to_merge_hours": round(median(hours), 1) if hours else None,
            "returned_pct": (round(100 * sum(1 for c in reviewed if c.returns) / len(reviewed))
                             if reviewed else None),
            "cost_per_merged_change": (round(cost / len(merged_month), 2)
                                       if merged_month else None),
            "hidden_scenarios": scenarios,
            "deploys_month": len(shipped),
            "change_failure_pct": round(100 * len(rolled) / len(shipped)) if shipped else None,
            "recover_hours": round(median(recover), 1) if recover else None,
        },
    }
