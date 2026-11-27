"""Forward pressure: what starts work in a Code Factory without anyone asking.

Two things should never wait for a person to notice them:

* **A release the guard rolled back.** By the time the chairman reads the
  alarm, a fix should already be on the board — a requirement that the
  failure does not recur, and a task for the build team carrying the
  incident's timeline. The platform rolled back; the platform opens the fix.
* **The same gate failing again and again.** When weakened tests, failing
  checks, a dirty scan or a worse codebase keep coming back across different
  changes, patching each change is the wrong fix. The platform opens work to
  remove the cause at the source — a lint rule, a test, a line in AGENTS.md —
  shipped through the gate like any other change. Improve the system, not the
  outputs.

Both are written by the platform, accepted on creation for the reason R-00 is
(they record a fact, not scope anybody chose), and both serve a requirement,
so the board's one rule — work serves accepted scope — still holds.
"""

from __future__ import annotations

from datetime import UTC, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core import ids, shape
from yaaf.core.models import (
    Change,
    FactorySignal,
    Notice,
    NoticeKind,
    Project,
    Recurrence,
    Requirement,
    RequirementStatus,
    Role,
    RoleVersion,
    Task,
    TaskStatus,
    Team,
    utcnow,
)
from yaaf.server import policy, upkeep

#: A gate failing on this many different changes inside the window is a cause.
RECURRING_CHANGES = 3
RECURRING_WINDOW = timedelta(days=14)
#: Gates whose failure says something about the codebase or the process, and
#: what the lesson task asks for.
LESSONS = {
    "tests": "tests keep being weakened to get green — find why the real fix is hard",
    "checks": "CI keeps failing on changes — find the recurring cause",
    "secrets": "credentials keep reaching diffs — stop them before the commit",
    "scan": "the platform scan keeps finding the same kind of problem",
    "coverage": "changes keep arriving under the coverage floor",
    "health": "changes keep making the codebase harder to maintain",
}
#: Not here: "review" and "scenarios". A return is the review working, and a
#: hidden scenario failing a first push is what hidden scenarios are for; three
#: of either in two weeks is a factory iterating, and a lesson for it opened a
#: requirement and a task ahead of the features (the Split app logged one on its
#: second change).


def _build_team(session: Session, project: Project) -> Team | None:
    """The team platform-opened work lands on: one that can take it now. The
    first build-category team found could be Release with its one seat stood
    down, and a fix would sit ready with nobody to claim it."""
    from yaaf.core.models import Agent, AgentStatus

    teams = list(session.execute(
        select(Team, Role.category, Role.slug)
        .join(RoleVersion, RoleVersion.id == Team.role_version_id)
        .join(Role, Role.id == RoleVersion.role_id).where(Team.project_id == project.id)))

    def staffed(team: Team) -> bool:
        return team.lead_agent_id is not None and session.execute(
            select(Agent.id).where(Agent.id == team.lead_agent_id,
                                   Agent.status == AgentStatus.ACTIVE)).first() is not None

    for wanted in (lambda c, slug, t: slug == "engineer" and staffed(t),
                   lambda c, slug, t: c == "build" and staffed(t),
                   lambda c, slug, t: c == "build",
                   lambda c, slug, t: c != "challenge"):
        for team, category, slug in teams:
            if wanted(category, slug, team):
                return team
    return teams[0][0] if teams else None


def open_work(session: Session, project: Project, *, title: str, statement: str,
              goal: str, why: str) -> Task | None:
    """An accepted requirement and a ready task serving it, on the build team."""
    team = _build_team(session, project)
    ceo = policy.ceo_of(session, project.id)
    if team is None or ceo is None:
        return None
    cycle = policy.current_cycle(session, project.id) if shape.runs_on_cycles(project) else None
    requirement = Requirement(
        id=ids.new_id(ids.REQUIREMENT), project_id=project.id,
        key=policy.next_requirement_key(session, project.id), statement=statement[:2000],
        acceptance_test={"type": "observable",
                         "target": "a change merged by the platform that removes the cause, "
                                   "with a test that would have caught it"},
        status=RequirementStatus.PENDING, stage=1, recurrence=Recurrence.TERMINAL,
        cycle_id=cycle.id if cycle else None, est_runs=3, accepted_at=utcnow(),
        meta={"title": title, "opened_by": "platform", "why": why})
    session.add(requirement)
    session.flush()
    policy.record_requirement_event(session, requirement, RequirementStatus.PENDING, "platform",
                                    note=f"opened by the platform: {why}", from_status=None)
    task = Task(
        id=ids.new_id(ids.TASK), project_id=project.id, team_id=team.id,
        created_by_agent_id=ceo.id, requirement_id=requirement.id, title=title[:300],
        goal=goal[:8000], status=TaskStatus.READY,
        success_test={"type": "observable",
                      "target": "the change that fixes it is merged by the platform and "
                                "carries a regression test"})
    session.add(task)
    session.flush()
    policy.record_task_event(session, task, TaskStatus.READY, "platform",
                             note=f"opened by the platform: {why}", from_status=None)
    session.add(Notice(
        id=ids.new_id(ids.NOTICE), project_id=project.id, agent_id=ceo.id,
        kind=NoticeKind.EXTERNAL_EVENT,
        body=(f"The platform opened {requirement.key} and a task for {team.name}: {title}. "
              f"Why: {why} It is accepted scope; staff it ahead of new features.")))
    return task


# ── incidents ───────────────────────────────────────────────────────────────────


def on_incident(session: Session, project: Project, incident, release=None) -> Task | None:
    if not shape.is_factory(project):
        return None
    timeline = "\n".join(f"- {e['at']}: {e['what']}" for e in incident.timeline or [])
    shipped = (f" Release {release.version} (commit {release.sha[:12]}) shipped:\n"
               f"{release.notes}\n" if release is not None else "")
    return open_work(
        session, project,
        title=f"Fix incident {incident.number}: {incident.title}",
        statement=(f"What broke in incident {incident.number} ({incident.title}) is fixed, and "
                   "a test fails if it ever comes back."),
        goal=(f"Production broke and the platform already acted.{shipped}\nTimeline:\n"
              f"{timeline}\n\nFind the cause in the release's changes, fix it in one change "
              "with a regression test, and let it ship through the gates. Then the CEO writes "
              "the postmortem."),
        why=f"incident {incident.number} was opened by {incident.opened_by}.")


# ── recurring gate failures ─────────────────────────────────────────────────────


def gate_failed(session: Session, project: Project, change: Change, gate: str,
                detail: str) -> None:
    """Remember once per change and gate that the gate failed on it."""
    key = f"{change.id}:{gate}"
    seen = session.execute(select(FactorySignal.id).where(
        FactorySignal.project_id == project.id, FactorySignal.kind == "gate_failed",
        FactorySignal.key == key)).first()
    if seen is None:
        upkeep.signal(session, project, "gate_failed", key,
                      {"gate": gate, "change": change.id, "number": change.number,
                       "detail": detail[:300]})


def failures(change: Change, gates: dict, cons) -> dict[str, str]:
    """Which gates FAILED on this commit, as distinct from still waiting."""
    findings = change.findings or {}
    out: dict[str, str] = {}
    if any(c not in ("success", "neutral", "skipped", "pending")
           for c in (change.checks or {}).values()):
        out["checks"] = gates.get("checks", {}).get("detail", "")
    if findings.get("sha") == change.head_sha:
        if findings.get("weakened_tests"):
            out["tests"] = "; ".join(findings["weakened_tests"][:2])
        if findings.get("secrets"):
            out["secrets"] = "credential in the diff"
    scan = findings.get("scan") or {}
    if scan.get("sha") == change.head_sha and (scan.get("advisories_high") or scan.get("secrets")):
        out["scan"] = gates.get("scan", {}).get("detail", "")
    coverage = findings.get("coverage") or {}
    if coverage.get("sha") == change.head_sha and cons.min_changed_line_coverage and \
            float(coverage.get("pct") or 0) < cons.min_changed_line_coverage:
        out["coverage"] = gates.get("coverage", {}).get("detail", "")
    results = (change.scenario_results or {})
    if results.get("sha") == change.head_sha and not all((results.get("results") or {}).values()):
        out["scenarios"] = gates.get("scenarios", {}).get("detail", "")
    health = findings.get("health") or {}
    if health.get("sha") == change.head_sha and health.get("worse"):
        out["health"] = gates.get("health", {}).get("detail", "")
    return out


def check_recurring(session: Session, project: Project) -> int:
    """Janitor: a gate that failed on RECURRING_CHANGES different changes in
    the window, with no lesson opened for it in the window, opens one."""
    if not shape.is_factory(project):
        return 0
    since = utcnow() - RECURRING_WINDOW
    rows = list(session.execute(select(FactorySignal).where(
        FactorySignal.project_id == project.id,
        FactorySignal.kind.in_(("gate_failed", "lesson_opened")))).scalars())

    def recent(row) -> bool:
        at = row.at if row.at.tzinfo else row.at.replace(tzinfo=UTC)
        return at >= since

    taught = {r.key for r in rows if r.kind == "lesson_opened" and recent(r)}
    by_gate: dict[str, dict[str, int | None]] = {}
    details: dict[str, list[str]] = {}
    for row in rows:
        if row.kind != "gate_failed" or not recent(row):
            continue
        gate = row.data.get("gate")
        by_gate.setdefault(gate, {})[row.data.get("change")] = row.data.get("number")
        details.setdefault(gate, []).append(row.data.get("detail") or "")
    opened = 0
    for gate, changed in by_gate.items():
        if len(changed) < RECURRING_CHANGES or gate in taught or gate not in LESSONS:
            continue
        numbers = ", ".join(f"#{n}" for n in sorted(n for n in changed.values() if n))
        examples = "\n".join(f"- {d}" for d in details[gate][:5] if d)
        task = open_work(
            session, project,
            title=f"Stop '{gate}' failures at the source",
            statement=(f"The cause behind repeated '{gate}' failures is removed: a lint rule, "
                       "a test, a helper or a line in AGENTS.md makes the mistake hard to "
                       "make again."),
            goal=(f"The '{gate}' gate failed on {len(changed)} different changes in two weeks "
                  f"({numbers}) — {LESSONS[gate]}.\nWhat it said:\n{examples}\n\nDo not fix "
                  "another change. Find what keeps producing this and change the system: add "
                  "the lint rule or test that catches it, the helper that makes the right way "
                  "easy, or the AGENTS.md line that says it. Ship it as one change."),
            why=f"'{gate}' failed on {len(changed)} changes in 14 days ({numbers}).")
        upkeep.signal(session, project, "lesson_opened", gate,
                      {"gate": gate, "changes": list(changed), "task": task.id if task else None})
        opened += 1
    return opened
