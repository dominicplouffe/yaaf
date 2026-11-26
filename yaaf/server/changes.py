"""The merge gate: a Code Factory's pull requests, and the only door to main.

An agent pushes a branch with its own token and asks for a change. Everything
after that is the platform's: it opens the pull request with its own token,
reads the size from GitHub rather than from the agent, takes CI results only
from signed webhooks against the exact head commit, takes the review only from
a challenge seat that did not write the code, and merges — pinned to that head
— only when every gate is green. A push after the review clears the review: a
signature on code that has since changed signs nothing.

Main moving any other way is a fact the repo reports (a push the platform did
not make), and it freezes merging until the chairman answers it. That is the
rule that still holds when GitHub's own branch protection could not be set.
"""

from __future__ import annotations

import logging
from datetime import UTC, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core import ids, shape
from yaaf.core.constitution import ConstitutionDoc, effective
from yaaf.core.models import (
    Agent,
    AuditLog,
    Change,
    ChangeStatus,
    Flag,
    FlagKind,
    FlagStatus,
    Notice,
    NoticeKind,
    Project,
    ProjectStatus,
    Requirement,
    RequirementStatus,
    Task,
    TaskStatus,
    utcnow,
)
from yaaf.server import github, policy
from yaaf.server.policy import Refusal

log = logging.getLogger(__name__)

#: A check run that finished in one of these let the change through.
GREEN = {"success", "neutral", "skipped"}
#: Statuses a change can still move out of.
LIVE = (ChangeStatus.OPEN, ChangeStatus.READY, ChangeStatus.MERGING)
#: The order the gates are shown in, and the order a person reads them.
GATES = ("serves", "size", "checks", "review")
#: How long a push to main may stay unexplained while a merge is in flight.
RECONCILE_GRACE = timedelta(minutes=2)


# ── who may ask ─────────────────────────────────────────────────────────────────


def require_factory(project: Project) -> None:
    if not shape.is_factory(project):
        raise Refusal(
            "Changes belong to Code Factory projects, where only the platform merges. "
            f"This one is {shape.label(project.mode)}: work here closes through its "
            "task's success test.", status_code=422)


def _repo(project: Project) -> str:
    if not project.github_repo:
        raise Refusal(
            "This project has no repository attached yet, so there is nothing to open "
            "a change against. The chairman attaches one in Settings.", status_code=409)
    return project.github_repo


def _default_branch(project: Project) -> str:
    return str((project.integrations or {}).get("default_branch") or "main")


# ── opening ─────────────────────────────────────────────────────────────────────


def open_change(session: Session, project: Project, agent: Agent, *, task_id: str,
                branch: str, title: str, summary: str) -> Change:
    """The author asks; the platform opens the pull request with its own token.

    The task must be the author's, claimed, and serving an accepted
    requirement — the same chain every other piece of work hangs from, so a
    change that serves nothing cannot exist rather than being merged and
    explained later."""
    require_factory(project)
    policy.require_project_running(project)
    repo = _repo(project)
    task = session.get(Task, task_id)
    if task is None or task.project_id != project.id:
        raise Refusal("Unknown task.", status_code=404)
    if task.status != TaskStatus.CLAIMED or task.claimed_by_agent_id != agent.id:
        raise Refusal(
            "A change is opened by the seat holding its task, while it holds it. Claim "
            "the task first; a change on somebody else's task would be work nobody "
            "assigned you.", status_code=409)
    if task.requirement_id is None:
        raise Refusal("This task serves no requirement, so a change for it would "
                      "too.", status_code=422)
    base = _default_branch(project)
    branch = branch.strip()
    if not branch or branch == base or branch.startswith("refs/"):
        raise Refusal(
            f"Push your work to a branch of its own and name that branch. {base!r} is "
            "the one branch only the platform moves.", status_code=422)
    already = session.execute(
        select(Change).where(Change.task_id == task.id, Change.status.in_(LIVE))
    ).scalars().first()
    if already is not None:
        raise Refusal(
            f"This task already has change #{already.number} open. One task, one "
            "change: push to its branch and the gates re-run.", status_code=409)

    body = (f"{summary.strip()}\n\n---\nServes {_requirement_key(session, task)} through "
            f"task {task.id}. Opened by yaaf for {agent.name}; only the platform merges "
            "it, after every gate is green.")
    try:
        pr = github.open_pull_request(session, repo, head=branch, base=base,
                                      title=title.strip()[:300], body=body)
    except github.GithubError as exc:
        raise Refusal(str(exc), status_code=502) from exc

    change = Change(
        id=ids.new_id(ids.CHANGE), project_id=project.id, task_id=task.id,
        requirement_id=task.requirement_id, author_agent_id=agent.id,
        number=pr.get("number"), branch=branch, base=base, title=title.strip()[:300],
        summary=summary.strip(), checks={}, gates={}, status=ChangeStatus.OPEN,
    )
    _take_pr(change, pr)
    session.add(change)
    session.flush()
    _audit(session, project, "change.open", change, {"number": change.number,
                                                     "branch": branch})
    evaluate(session, project, change)
    return change


def _requirement_key(session: Session, task: Task) -> str:
    requirement = session.get(Requirement, task.requirement_id) if task.requirement_id else None
    return requirement.key if requirement else "no requirement"


def _take_pr(change: Change, pr: dict) -> None:
    """Size and head from GitHub's own object. A new head clears the checks
    and the review: both were about code that is no longer the change."""
    head = (pr.get("head") or {}).get("sha")
    if head and head != change.head_sha:
        change.head_sha = head
        change.checks = {}
        if change.review_verdict == "signed":
            change.review_verdict = None
            change.review_note = None
            change.reviewer_agent_id = None
            change.reviewed_sha = None
            change.reviewed_at = None
    change.lines_added = int(pr.get("additions") or 0)
    change.lines_removed = int(pr.get("deletions") or 0)
    change.files_changed = int(pr.get("changed_files") or 0)


# ── the gates ───────────────────────────────────────────────────────────────────


def gates_for(session: Session, project: Project, change: Change,
              cons: ConstitutionDoc | None = None) -> dict:
    cons = cons or effective(session, project.id)
    gates: dict[str, dict] = {}

    requirement = session.get(Requirement, change.requirement_id) if change.requirement_id else None
    serves_ok = requirement is not None and requirement.status not in (
        RequirementStatus.PROPOSED, RequirementStatus.CUT)
    gates["serves"] = {
        "ok": serves_ok,
        "detail": (f"serves {requirement.key}" if serves_ok
                   else f"{requirement.key} is {requirement.status}" if requirement
                   else "serves no requirement")}

    lines = change.lines_added + change.lines_removed
    gates["size"] = {"ok": lines <= cons.max_change_lines,
                     "detail": f"{lines} lines of {cons.max_change_lines}"}

    checks = change.checks or {}
    failed = sorted(name for name, c in checks.items() if c not in GREEN and c != "pending")
    pending = sorted(name for name, c in checks.items() if c == "pending")
    gates["checks"] = {
        "ok": bool(checks) and not failed and not pending,
        "detail": (f"failed: {', '.join(failed)}" if failed
                   else f"running: {', '.join(pending)}" if pending
                   else f"{len(checks)} passed" if checks
                   else "no check has reported on this commit yet")}

    signed = (change.review_verdict == "signed" and change.reviewed_sha == change.head_sha
              and change.reviewer_agent_id not in (None, change.author_agent_id))
    gates["review"] = {
        "ok": signed,
        "detail": ("signed" if signed
                   else f"returned: {(change.review_note or '')[:200]}"
                   if change.review_verdict == "returned"
                   else "waiting for a challenge seat to read it")}
    return gates


def evaluate(session: Session, project: Project, change: Change,
             cons: ConstitutionDoc | None = None) -> dict:
    """Recompute every gate and move the change between OPEN and READY. Size
    is the one gate that refuses outright: a change too big to review stays
    too big however long it waits, so it is closed with the reason rather than
    left open to be argued with."""
    if change.status not in (ChangeStatus.OPEN, ChangeStatus.READY):
        return change.gates or {}
    gates = gates_for(session, project, change, cons)
    change.gates = gates
    if not gates["size"]["ok"]:
        change.status = ChangeStatus.REFUSED
        change.status_reason = (
            f"Refused: {gates['size']['detail']}. Split it into changes a reviewer can "
            "read whole, each serving its own task.")
        _audit(session, project, "change.refused", change, {"gate": "size",
                                                           "detail": gates["size"]["detail"]})
        _tell(session, project, change.author_agent_id, NoticeKind.CHANGE_RETURNED,
              f"Change #{change.number} ({change.title}) was refused and closed: "
              f"{change.status_reason}")
        try:
            github.close_pull_request(session, project.github_repo or "", change.number or 0,
                                      f"Closed by yaaf: {change.status_reason}")
        except github.GithubError as exc:
            log.warning("could not close refused PR #%s: %s", change.number, exc)
        return gates
    ready = all(g["ok"] for g in gates.values())
    change.status = ChangeStatus.READY if ready else ChangeStatus.OPEN
    if ready:
        change.status_reason = None
    return gates


# ── what arrives from GitHub ────────────────────────────────────────────────────


def by_number(session: Session, project: Project, number: int | None) -> Change | None:
    if number is None:
        return None
    return session.execute(
        select(Change).where(Change.project_id == project.id, Change.number == number)
        .order_by(Change.created_at.desc())
    ).scalars().first()


def pull_request_event(session: Session, project: Project, action: str, pr: dict) -> dict:
    change = by_number(session, project, pr.get("number"))
    if change is None:
        return {"ignored": "not a change the platform opened"}
    if action == "synchronize" and change.status in (ChangeStatus.OPEN, ChangeStatus.READY):
        was = change.head_sha
        _take_pr(change, pr)
        if change.status == ChangeStatus.READY:
            change.status = ChangeStatus.OPEN
        evaluate(session, project, change)
        return {"change": change.id, "head": change.head_sha, "moved_from": was}
    if action == "closed" and change.status in (ChangeStatus.OPEN, ChangeStatus.READY):
        change.status = ChangeStatus.CLOSED
        change.status_reason = ("merged on GitHub, outside the platform" if pr.get("merged")
                                else "closed on GitHub")
        return {"change": change.id, "status": change.status}
    return {"change": change.id, "ignored": f"action {action!r}"}


def check_run_event(session: Session, project: Project, run: dict) -> dict:
    """One CI job's result, for the commit it ran on. Recorded only against a
    change whose head is that commit: a green run on an older head is a green
    run on code that is no longer the change."""
    sha = run.get("head_sha")
    name = str(run.get("name") or "check")[:120]
    conclusion = (run.get("conclusion") or "pending") if run.get("status") == "completed" \
        else "pending"
    touched = []
    for change in session.execute(
        select(Change).where(Change.project_id == project.id, Change.head_sha == sha,
                             Change.status.in_((ChangeStatus.OPEN, ChangeStatus.READY)))
    ).scalars():
        change.checks = {**(change.checks or {}), name: conclusion}
        evaluate(session, project, change)
        touched.append(change.id)
    return {"changes": touched, "check": name, "conclusion": conclusion}


def push_event(session: Session, project: Project, payload: dict) -> dict:
    """A push to main. Written down now and judged by the janitor once nothing
    is mid-merge, because GitHub can announce the platform's own merge before
    the platform has finished recording it."""
    ref = payload.get("ref") or ""
    base = _default_branch(project)
    if ref != f"refs/heads/{base}":
        return {"ignored": f"push to {ref}"}
    sha = payload.get("after")
    pusher = ((payload.get("pusher") or {}).get("name")
              or (payload.get("sender") or {}).get("login") or "someone")
    session.add(AuditLog(
        id=ids.new_id(ids.AUDIT), project_id=project.id, actor="system:webhook",
        action="github.main_push", entity="project", entity_id=project.id,
        detail={"sha": sha, "pusher": pusher, "reconciled": False}))
    return {"main_push": sha}


# ── review ──────────────────────────────────────────────────────────────────────


def review(session: Session, project: Project, change: Change, agent: Agent, *,
           verdict: str, note: str) -> dict:
    """A challenge seat signs the head it read, or returns it with a reason.
    The author can never sign, and neither can a seat whose job is to make
    rather than to attack — the read is only worth something from a distance."""
    require_factory(project)
    if change.status not in (ChangeStatus.OPEN, ChangeStatus.READY):
        raise Refusal(f"Change #{change.number} is {change.status}; there is nothing "
                      "left to review.", status_code=409)
    if agent.id == change.author_agent_id:
        raise Refusal(
            "You wrote this change, so your read of it is the one read that does not "
            "count. A challenge seat signs it or returns it.", status_code=403)
    if not policy.is_review_seat(session, agent):
        raise Refusal(
            "Only a challenge seat — a reviewer, a tester, a security seat — signs a "
            "change. A seat that builds may comment through the author, not sign.",
            status_code=403)
    if not change.head_sha:
        raise Refusal("GitHub has not reported this change's head commit yet, so there "
                      "is nothing definite to sign.", status_code=409)
    if verdict not in ("sign", "return"):
        raise Refusal("verdict is 'sign' or 'return'.", status_code=422)
    note = note.strip()
    if len(note) < 10:
        raise Refusal("Say what you read and what you found; a verdict without a reason "
                      "teaches the author nothing and cannot be audited.", status_code=422)
    change.reviewer_agent_id = agent.id
    change.review_note = note[:4000]
    change.reviewed_sha = change.head_sha
    change.reviewed_at = utcnow()
    if verdict == "sign":
        change.review_verdict = "signed"
    else:
        change.review_verdict = "returned"
        change.returns = (change.returns or 0) + 1
        _tell(session, project, change.author_agent_id, NoticeKind.CHANGE_RETURNED,
              f"{agent.name} returned change #{change.number} ({change.title}): {note[:1500]}"
              " Push the fix to the same branch; the gates re-run on the new head.")
    _audit(session, project, "change.review", change,
           {"verdict": change.review_verdict, "by": agent.id, "sha": change.head_sha})
    gates = evaluate(session, project, change)
    return {"change": view(change), "gates": gates}


# ── merging ─────────────────────────────────────────────────────────────────────


def merges_frozen(session: Session, project: Project) -> Flag | None:
    return session.execute(
        select(Flag).where(Flag.project_id == project.id,
                           Flag.kind == FlagKind.MAIN_BYPASSED,
                           Flag.status != FlagStatus.RESOLVED)
    ).scalars().first()


def merge_ready(session: Session, project: Project) -> int:
    """Merge every READY change, each re-checked first and pinned to the head
    its gates ran on. MERGING is committed before GitHub is asked, so the push
    GitHub announces for this merge finds a merge in flight rather than an
    unexplained commit."""
    if not shape.is_factory(project) or project.status != ProjectStatus.RUNNING:
        return 0
    if merges_frozen(session, project) is not None:
        return 0
    cons = effective(session, project.id)
    merged = 0
    for change in list(session.execute(
        select(Change).where(Change.project_id == project.id,
                             Change.status == ChangeStatus.READY)
        .order_by(Change.created_at)
    ).scalars()):
        evaluate(session, project, change, cons)
        if change.status != ChangeStatus.READY:
            continue
        change.status = ChangeStatus.MERGING
        session.commit()
        try:
            sha = github.merge_pull_request(
                session, project.github_repo or "", change.number or 0,
                sha=change.head_sha or "", title=f"{change.title} (#{change.number})")
        except github.GithubError as exc:
            change.status = ChangeStatus.OPEN
            change.status_reason = str(exc)[:1000]
            _audit(session, project, "change.merge_failed", change, {"error": str(exc)[:500]})
            session.commit()
            continue
        change.status = ChangeStatus.MERGED
        change.merged_sha = sha
        change.merged_at = utcnow()
        change.status_reason = None
        _audit(session, project, "change.merged", change,
               {"sha": sha, "head": change.head_sha, "reviewer": change.reviewer_agent_id})
        _tell(session, project, change.author_agent_id, NoticeKind.CHANGE_MERGED,
              f"Change #{change.number} ({change.title}) is merged: {sha[:12]}.")
        session.commit()
        merged += 1
    return merged


def reconcile_main(session: Session, project: Project) -> int:
    """Judge every push to main the webhook recorded. A push whose commit is a
    merge the platform made is accounted for. Any other push is main moving
    around every gate: a flag, the chairman told, and merging frozen until he
    answers it — the automatic half of "only the platform merges"."""
    if not shape.is_factory(project):
        return 0
    rows = [r for r in session.execute(
        select(AuditLog).where(AuditLog.project_id == project.id,
                               AuditLog.action == "github.main_push")
    ).scalars() if not (r.detail or {}).get("reconciled")]
    if not rows:
        return 0
    in_flight = session.execute(
        select(Change.id).where(Change.project_id == project.id,
                                Change.status == ChangeStatus.MERGING)
    ).first() is not None
    ours = {sha for sha in session.execute(
        select(Change.merged_sha).where(Change.project_id == project.id,
                                        Change.merged_sha.is_not(None))
    ).scalars()}
    from yaaf.server.flags import open_flag
    from yaaf.server.notify import Attention, chairman_notify

    bypassed = 0
    now = utcnow()
    for row in rows:
        detail = dict(row.detail or {})
        sha = detail.get("sha")
        if sha in ours:
            row.detail = {**detail, "reconciled": "platform merge"}
            continue
        at = row.at if row.at.tzinfo else row.at.replace(tzinfo=UTC)
        if in_flight and now - at < RECONCILE_GRACE:
            continue
        row.detail = {**detail, "reconciled": "bypass"}
        bypassed += 1
        flag = open_flag(
            session, project, FlagKind.MAIN_BYPASSED,
            f"{detail.get('pusher', 'someone')} moved main to {str(sha)[:12]} without the "
            "platform. Only the platform merges, after every gate; this commit skipped all "
            "of them. Merging is frozen until this is answered.",
            arithmetic={"sha": sha, "pusher": detail.get("pusher")}, subject_id=sha)
        if flag is not None:
            chairman_notify(
                session, project, "main moved without the platform",
                f"{detail.get('pusher', 'Someone')} pushed {str(sha)[:12]} straight to main. "
                "Nothing the platform checks ran on it. No change will merge until you "
                "answer the flag: keep the commit and say why, or have it reverted.",
                attention=Attention.ALARM)
    return bypassed


# ── reading ─────────────────────────────────────────────────────────────────────


def view(change: Change) -> dict:
    return {
        "id": change.id, "number": change.number, "title": change.title,
        "summary": change.summary, "branch": change.branch, "base": change.base,
        "task_id": change.task_id, "requirement_id": change.requirement_id,
        "author_agent_id": change.author_agent_id, "head_sha": change.head_sha,
        "lines": change.lines_added + change.lines_removed,
        "lines_added": change.lines_added, "lines_removed": change.lines_removed,
        "files_changed": change.files_changed, "status": change.status,
        "status_reason": change.status_reason, "checks": change.checks or {},
        "gates": change.gates or {}, "review": {
            "by": change.reviewer_agent_id, "verdict": change.review_verdict,
            "note": change.review_note, "sha": change.reviewed_sha,
            "at": change.reviewed_at.isoformat() if change.reviewed_at else None,
            "returns": change.returns},
        "merged_sha": change.merged_sha,
        "merged_at": change.merged_at.isoformat() if change.merged_at else None,
        "created_at": change.created_at.isoformat() if change.created_at else None,
    }


def listing(session: Session, project: Project, *, live_only: bool = False) -> list[dict]:
    query = select(Change).where(Change.project_id == project.id)
    if live_only:
        query = query.where(Change.status.in_(LIVE))
    return [view(c) for c in session.execute(
        query.order_by(Change.created_at.desc()).limit(200)).scalars()]


def _audit(session: Session, project: Project, action: str, change: Change,
           detail: dict) -> None:
    session.add(AuditLog(
        id=ids.new_id(ids.AUDIT), project_id=project.id, actor="system:changes",
        action=action, entity="change", entity_id=change.id, detail=detail))


def _tell(session: Session, project: Project, agent_id: str, kind: str, body: str) -> None:
    session.add(Notice(id=ids.new_id(ids.NOTICE), project_id=project.id, agent_id=agent_id,
                       kind=kind, body=body))
