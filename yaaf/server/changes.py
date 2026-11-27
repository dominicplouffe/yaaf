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
from datetime import UTC, datetime, timedelta

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
GATES = ("serves", "size", "secrets", "tests", "packages", "protected", "ci_config",
         "scan", "health", "checks", "coverage", "preview", "scenarios", "review")
#: Where CI is defined. The checks gate trusts CI, so a change to these is a
#: change to the judge, and needs more than the judged party's word.
CI_PATHS = [".github/workflows/**", ".github/workflows/*", ".github/actions/**",
            ".gitlab-ci.yml", ".circleci/**", "Jenkinsfile", ".buildkite/**",
            "azure-pipelines.yml", "CODEOWNERS", ".github/CODEOWNERS"]
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
    scan(session, project, change)
    queue_scenarios(session, project, change)
    queue_scan(session, project, change)
    queue_preview(session, project, change)
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
        change.waivers = {}
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


def scan(session: Session, project: Project, change: Change) -> None:
    """Read the diff from GitHub and keep what it says about itself. A diff
    the platform could not read fails closed: every gate that reads it waits."""
    from yaaf.server import diffscan

    cons = effective(session, project.id)
    try:
        files = github.list_pull_files(session, project.github_repo or "", change.number or 0)
    except github.GithubError as exc:
        change.findings = {"sha": change.head_sha, "error": str(exc)[:500]}
        return
    kept = {k: v for k, v in (change.findings or {}).items()
            if k in ("coverage", "scan", "preview", "health")
            and (v or {}).get("sha") == change.head_sha}
    change.findings = {"sha": change.head_sha,
                       **diffscan.inspect(files, diffscan.parse_globs(cons.protected_paths)),
                       "ci_config": diffscan.protected(files, CI_PATHS), **kept}



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

    findings = change.findings or {}
    unread = findings.get("sha") != change.head_sha or "error" in findings
    why_unread = findings.get("error") or "the diff of this commit has not been read yet"
    waivers = change.waivers if (change.waivers or {}).get("sha") == change.head_sha else {}

    leaked = findings.get("secrets") or []
    gates["secrets"] = {
        "ok": not unread and not leaked,
        "detail": (why_unread if unread
                   else "a credential in the diff — rotate it; this cannot be waived: "
                        + "; ".join(leaked[:3]) if leaked else "none found")}

    weakened = findings.get("weakened_tests") or []
    gates["tests"] = {
        "ok": not unread and (not weakened or bool(waivers.get("weaker_tests"))),
        "detail": (why_unread if unread
                   else "none weakened" if not weakened
                   else f"accepted by a challenge seat: {waivers['weaker_tests'][:200]}"
                   if waivers.get("weaker_tests")
                   else "weaker tests need a challenge seat's signed reason: "
                        + "; ".join(weakened[:3]))}

    packages = findings.get("new_packages") or []
    cleared = set(waivers.get("packages") or [])
    unchecked = [p for p in packages if p not in cleared]
    gates["packages"] = {
        "ok": not unread and not unchecked,
        "detail": (why_unread if unread
                   else "no new packages" if not packages
                   else f"held for a challenge seat to check: {', '.join(unchecked)}"
                   if unchecked else f"{len(packages)} new, each checked")}

    touched = findings.get("protected") or []
    approved = bool(change.chairman_sha) and change.chairman_sha == change.head_sha
    gates["protected"] = {
        "ok": not unread and (not touched or approved),
        "detail": (why_unread if unread
                   else "touches no path that needs a person" if not touched
                   else "approved by the chairman on this commit" if approved
                   else "needs the chairman's yes: " + ", ".join(touched[:5]))}

    ci = findings.get("ci_config") or []
    ci_ok = (not ci) or (change.chairman_sha == change.head_sha if cons.ci_changes_need_chairman
                         else bool(waivers.get("ci_config")))
    gates["ci_config"] = {
        "ok": not unread and ci_ok,
        "detail": (why_unread if unread
                   else "leaves CI as it is" if not ci
                   else "CI change accepted on this commit" if ci_ok
                   else ("changes CI, which judges every change — needs the chairman's "
                         "yes: " if cons.ci_changes_need_chairman
                         else "changes CI — needs a challenge seat's signed reason: ")
                   + ", ".join(ci[:4]))}

    gates["scan"] = scan_gate(change, cons)
    gates["health"] = health_gate(change, cons, waivers)
    gates["preview"] = preview_gate(project, change)
    gates["coverage"] = coverage_gate(change, cons)
    gates["scenarios"] = scenario_gate(session, change, cons)

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
                   if change.review_verdict == "returned" and change.reviewed_sha == change.head_sha
                   else "waiting for a challenge seat to read it")}
    # GitHub said it cannot merge this commit onto main (it conflicts, or main
    # moved under a rule that wants it up to date). Green everywhere else, it
    # would be queued and refused again every tick; a push clears it.
    refused = (change.findings or {}).get("merge_refused") or {}
    if refused.get("sha") and refused.get("sha") == change.head_sha:
        gates["mergeable"] = {"ok": False, "detail": (
            f"GitHub could not merge this commit onto main ({refused.get('status')}): "
            "rebase the branch onto main and push")}
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
    from yaaf.server import pressure

    for gate, detail in pressure.failures(change, gates, cons or effective(session, project.id)
                                          ).items():
        pressure.gate_failed(session, project, change, gate, detail)
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
    _ask_chairman_if_only_he_is_left(session, project, change, gates)
    return gates


#: The gates only the chairman's own yes can pass.
CHAIRMAN_GATES = ("protected", "ci_config")


#: A check that finished badly: the author's to fix.
_CHECK_FAILED = ("failure", "cancelled", "timed_out", "action_required", "startup_failure")


def author_can_act(change: Change) -> bool:
    """Whether anything holding this change is the author's to fix. A change
    signed and green but for the chairman, a challenge seat, CI still running
    or the platform still measuring waits on others; waking its author every
    tick buys a run that says "nothing I can do will move it" (Split app, five
    times in twenty minutes on one signed change)."""
    if change.status == ChangeStatus.MERGING:
        return False
    if change.status not in (ChangeStatus.OPEN, ChangeStatus.READY):
        return True
    findings = change.findings or {}
    head = change.head_sha

    def measured(key: str) -> bool:
        return (findings.get(key) or {}).get("sha") == head

    for gate, detail in (change.gates or {}).items():
        if detail.get("ok", True):
            continue
        if gate in ("protected", "ci_config", "packages"):
            continue                     # the chairman's, or a challenge seat's
        if gate == "review":
            if change.review_verdict == "returned" and change.reviewed_sha == head:
                return True
            continue
        if gate == "checks":
            if any(v in _CHECK_FAILED for v in (change.checks or {}).values()):
                return True
            continue
        if gate == "scenarios":
            results = change.scenario_results or {}
            if results.get("sha") == head and not all((results.get("results") or {}).values()):
                return True
            continue
        if gate in ("scan", "health", "preview"):
            if measured(gate):
                return True
            continue
        return True
    return False


def waits_on_chairman(change: Change) -> list[str]:
    """The gates on this change only the chairman's own yes can pass, as the
    last evaluation left them."""
    gates = change.gates or {}
    return [g for g in CHAIRMAN_GATES if not gates.get(g, {}).get("ok", True)]


def _ask_chairman_if_only_he_is_left(session: Session, project: Project, change: Change,
                                     gates: dict) -> None:
    """A change whose every other gate is green and which waits only on the
    chairman's approval of this commit, said to him once per commit. A new
    factory's first change is nearly always its CI setup, and it waited on a
    button on the Changes tab that nothing pointed at."""
    waiting = [g for g in CHAIRMAN_GATES if not gates.get(g, {}).get("ok", True)]
    if not waiting or any(not d["ok"] for g, d in gates.items() if g not in CHAIRMAN_GATES):
        return
    if (change.findings or {}).get("chairman_asked_sha") == change.head_sha:
        return
    change.findings = {**(change.findings or {}), "chairman_asked_sha": change.head_sha}
    from yaaf.server.notify import Attention, chairman_notify

    what = " and ".join(gates[g]["detail"] for g in waiting)
    chairman_notify(
        session, project, f"Change #{change.number} needs your approval",
        f"Change #{change.number} ({change.title}) has passed every other check and is "
        f"reviewed. It waits only on you: {what}. Approve it on the Changes tab — the "
        "approval holds for this exact commit only.",
        attention=Attention.ACTION)


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
        scan(session, project, change)
        queue_scenarios(session, project, change)
        queue_scan(session, project, change)
        queue_preview(session, project, change)
        evaluate(session, project, change)
        return {"change": change.id, "head": change.head_sha, "moved_from": was}
    if action == "closed" and change.status in (ChangeStatus.OPEN, ChangeStatus.READY):
        change.status = ChangeStatus.CLOSED
        change.status_reason = ("merged on GitHub, outside the platform" if pr.get("merged")
                                else "closed on GitHub")
        # The author's task is still claimed on this change; without a word it
        # waits on a pull request that no longer exists.
        _tell(session, project, change.author_agent_id, NoticeKind.CHANGE_RETURNED,
              f"Change #{change.number} ({change.title}) was {change.status_reason}. "
              + ("Its commit is on main without the platform's gates; the chairman is "
                 "asked about it." if pr.get("merged") else
                 "If the work is still wanted, push to the branch and it is reopened, or "
                 "open a new change for the task."))
        return {"change": change.id, "status": change.status}
    if action == "reopened" and change.status == ChangeStatus.CLOSED \
            and change.status_reason == "closed on GitHub":
        # Reopened on GitHub: the change is live again, or no change could ever
        # be opened for its branch (GitHub: "a pull request already exists").
        _take_pr(change, pr)
        change.status, change.status_reason = ChangeStatus.OPEN, None
        scan(session, project, change)
        queue_scenarios(session, project, change)
        queue_scan(session, project, change)
        evaluate(session, project, change)
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
    coverage_name = effective(session, project.id).coverage_check_name
    pct = _percent(run) if name == coverage_name and conclusion != "pending" else None
    for change in session.execute(
        select(Change).where(Change.project_id == project.id, Change.head_sha == sha,
                             Change.status.in_((ChangeStatus.OPEN, ChangeStatus.READY)))
    ).scalars():
        change.checks = {**(change.checks or {}), name: conclusion}
        if pct is not None:
            change.findings = {**(change.findings or {}),
                               "coverage": {"sha": sha, "pct": pct}}
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
           verdict: str, note: str, allow_weaker_tests: str | None = None,
           allow_packages: list[str] | None = None,
           allow_ci_change: str | None = None,
           allow_worse_health: str | None = None) -> dict:
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
    if verdict == "sign" and change.review_verdict == "returned" \
            and change.reviewed_sha == change.head_sha and change.reviewer_agent_id != agent.id:
        # One verdict slot per change: a signature here used to overwrite
        # another seat's return on the very commit it returned, and the change
        # merged without the fix (the Split app: security returned #2, the
        # code reviewer then signed a head security never re-read).
        returner = session.get(Agent, change.reviewer_agent_id)
        raise Refusal(
            f"{returner.name if returner else 'Another seat'} returned this commit: "
            f"{(change.review_note or '')[:300]} It waits on the author's push, which "
            "every seat then reads fresh. Add what you found to the thread instead.",
            status_code=409)
    change.reviewer_agent_id = agent.id
    change.review_note = note[:4000]
    change.reviewed_sha = change.head_sha
    change.reviewed_at = utcnow()
    if verdict == "sign":
        change.review_verdict = "signed"
        reason = (allow_weaker_tests or "").strip()
        if reason and len(reason) < 20:
            raise Refusal("Accepting weaker tests needs a real reason — what the removed "
                          "assertions checked, and why that no longer matters.",
                          status_code=422)
        ci_reason = (allow_ci_change or "").strip()
        if ci_reason and len(ci_reason) < 20:
            raise Refusal("Accepting a CI change needs a real reason: what changed in how "
                          "changes are judged, and why it is still a fair judge.",
                          status_code=422)
        health_reason = (allow_worse_health or "").strip()
        if health_reason and len(health_reason) < 20:
            raise Refusal("Accepting harder-to-maintain code needs a real reason: why the "
                          "complexity is inherent here, not convenient.", status_code=422)
        change.waivers = {"sha": change.head_sha, "by": agent.id,
                          "ci_config": ci_reason or None,
                          "health": health_reason or None,
                          "weaker_tests": reason or None,
                          "packages": sorted({p.strip().lower() for p in allow_packages or []
                                              if p.strip()})}
    else:
        change.review_verdict = "returned"
        change.returns = (change.returns or 0) + 1
        from yaaf.server import pressure

        pressure.gate_failed(session, project, change, "review", note)
        _tell(session, project, change.author_agent_id, NoticeKind.CHANGE_RETURNED,
              f"{agent.name} returned change #{change.number} ({change.title}): {note[:1500]}"
              " Push the fix to the same branch; the gates re-run on the new head.")
    _audit(session, project, "change.review", change,
           {"verdict": change.review_verdict, "by": agent.id, "sha": change.head_sha})
    gates = evaluate(session, project, change)
    return {"change": view(change), "gates": gates}


# ── the chairman's own yes ──────────────────────────────────────────────────────


def chairman_approve(session: Session, project: Project, change: Change) -> dict:
    """The chairman says yes to a change touching a path that needs a person,
    on the commit in front of him. A later push needs his yes again."""
    require_factory(project)
    if change.status not in (ChangeStatus.OPEN, ChangeStatus.READY) or not change.head_sha:
        raise Refusal(f"Change #{change.number} is {change.status}; there is nothing to "
                      "approve.", status_code=409)
    change.chairman_sha = change.head_sha
    _audit(session, project, "change.chairman_approved", change, {"sha": change.head_sha})
    gates = evaluate(session, project, change)
    return {"change": view(change), "gates": gates}


# ── coverage and the platform's own scan ────────────────────────────────────────


def _percent(run: dict) -> float | None:
    import re

    output = run.get("output") or {}
    for text in (output.get("title"), output.get("summary")):
        m = re.search(r"(\d{1,3}(?:\.\d+)?)\s*%", text or "")
        if m:
            return min(100.0, float(m.group(1)))
    return None


def coverage_gate(change: Change, cons: ConstitutionDoc) -> dict:
    floor = cons.min_changed_line_coverage
    if not floor:
        return {"ok": True, "detail": "off for this project"}
    reported = (change.findings or {}).get("coverage") or {}
    if reported.get("sha") != change.head_sha:
        return {"ok": False, "detail": f"CI has not reported '{cons.coverage_check_name}' "
                                       "with a percentage on this commit"}
    pct = float(reported.get("pct") or 0)
    return {"ok": pct >= floor, "detail": f"{pct:g}% of changed lines tested, floor {floor}%"}


def scan_gate(change: Change, cons: ConstitutionDoc) -> dict:
    if not cons.platform_scans:
        return {"ok": True, "detail": "off for this project"}
    result = (change.findings or {}).get("scan") or {}
    if result.get("sha") != change.head_sha:
        return {"ok": False, "detail": "the platform has not scanned this commit yet"}
    missing = [tool for tool, ok in (result.get("available") or {}).items() if not ok]
    if missing:
        return {"ok": False, "detail": f"this runner's sandbox image lacks {', '.join(missing)}:"
                                       " rebuild it (deploy/sandbox/Dockerfile)"}
    problems = []
    if result.get("advisories_high"):
        problems.append(f"{result['advisories_high']} high or critical advisory(ies): "
                        + ", ".join(result.get("advisories") or [])[:200])
    if result.get("secrets"):
        problems.append(f"{result['secrets']} secret(s) in the tree — rotate, then remove")
    return {"ok": not problems,
            "detail": "; ".join(problems) or "no high advisories, no secrets"}


def queue_scan(session: Session, project: Project, change: Change) -> bool:
    from yaaf.core.models import RunKind, RunQueue

    cons = effective(session, project.id)
    if not change.head_sha or not (cons.platform_scans or cons.code_health_ratchet):
        return False
    for queued in session.execute(
        select(RunQueue).where(RunQueue.project_id == project.id,
                               RunQueue.kind == RunKind.CHANGE_SCAN)
    ).scalars():
        trigger = queued.trigger or {}
        if trigger.get("change_id") == change.id:
            if trigger.get("sha") == change.head_sha:
                return False
            session.delete(queued)
    session.add(RunQueue(
        id=ids.new_id(ids.RUN), project_id=project.id, agent_id=None,
        kind=RunKind.CHANGE_SCAN,
        trigger={"type": "change_scan", "change_id": change.id, "sha": change.head_sha,
                 "branch": change.branch, "base": change.base}))
    return True


def health_gate(change: Change, cons: ConstitutionDoc, waivers: dict) -> dict:
    """The ratchet: the touched files may not get harder to maintain."""
    if not cons.code_health_ratchet:
        return {"ok": True, "detail": "off for this project"}
    seen = (change.findings or {}).get("health") or {}
    if seen.get("sha") != change.head_sha:
        return {"ok": False, "detail": "the platform has not measured this commit yet"}
    if seen.get("error"):
        return {"ok": False, "detail": seen["error"]}
    missing = [tool for tool, ok in (seen.get("available") or {}).items() if not ok]
    if missing:
        return {"ok": False, "detail": f"this runner's sandbox image lacks {', '.join(missing)}:"
                                       " rebuild it (deploy/sandbox/Dockerfile)"}
    if not seen.get("worse"):
        return {"ok": True, "detail": seen.get("detail") or "no worse"}
    if waivers.get("health"):
        return {"ok": True, "detail": f"worse, accepted by a challenge seat: "
                                      f"{waivers['health'][:200]}"}
    worst = "; ".join((seen.get("worst") or [])[:3])
    return {"ok": False, "detail": f"makes the code harder to maintain: {seen.get('detail')}"
                                   + (f" — {worst}" if worst else "")
                                   + ". Simplify it, or a challenge seat signs a reason."}


def record_health(session: Session, change: Change, sha: str, result: dict) -> None:
    project = session.get(Project, change.project_id)
    if project is None or sha != change.head_sha or not result:
        return
    change.findings = {**(change.findings or {}), "health": {"sha": sha, **result}}
    _audit(session, project, "change.health", change,
           {"sha": sha, "worse": result.get("worse"), "detail": result.get("detail")})
    evaluate(session, project, change)


def record_scan(session: Session, change: Change, sha: str, result: dict) -> None:
    """What the platform's scanners saw on one commit; ignored once it moved."""
    project = session.get(Project, change.project_id)
    if project is None or sha != change.head_sha or not result:
        return
    change.findings = {**(change.findings or {}), "scan": {"sha": sha, **result}}
    _audit(session, project, "change.scanned", change,
           {"sha": sha, "advisories_high": result.get("advisories_high"),
            "secrets": result.get("secrets")})
    evaluate(session, project, change)


# ── previews ────────────────────────────────────────────────────────────────────


def preview_gate(project: Project, change: Change) -> dict:
    from yaaf.server import releases

    if releases.environment(project, "preview") is None:
        return {"ok": True, "detail": "no preview environment set"}
    seen = (change.findings or {}).get("preview") or {}
    if seen.get("sha") != change.head_sha:
        return {"ok": False, "detail": "the preview of this commit is not up yet"}
    return {"ok": bool(seen.get("ok")), "detail": seen.get("detail") or ""}


def queue_preview(session: Session, project: Project, change: Change) -> bool:
    from yaaf.core.models import RunKind, RunQueue
    from yaaf.server import releases

    if not change.head_sha or releases.environment(project, "preview") is None:
        return False
    for queued in session.execute(
        select(RunQueue).where(RunQueue.project_id == project.id,
                               RunQueue.kind == RunKind.DEPLOY)
    ).scalars():
        trigger = queued.trigger or {}
        if trigger.get("environment") == "preview" and trigger.get("change_id") == change.id:
            if trigger.get("sha") == change.head_sha:
                return False
            session.delete(queued)
    session.add(RunQueue(
        id=ids.new_id(ids.RUN), project_id=project.id, agent_id=None, kind=RunKind.DEPLOY,
        trigger={"type": "deploy", "environment": "preview", "action": "deploy",
                 "change_id": change.id, "sha": change.head_sha, "branch": change.branch}))
    return True


def record_preview(session: Session, change: Change, sha: str, deployed: bool,
                   output: str, screenshot: bytes | None = None) -> None:
    """The preview's deploy, then one read of its health URL by the platform."""
    from yaaf.server import releases

    project = session.get(Project, change.project_id)
    if project is None or sha != change.head_sha:
        return
    env = releases.environment(project, "preview") or {}
    url = str(env.get("health_url") or "").replace("{number}", str(change.number or ""))
    if not deployed:
        result = {"ok": False, "detail": f"the preview did not deploy: {output[-200:]}"}
    else:
        healthy, said = releases.probe(url)
        result = {"ok": healthy, "url": url,
                  "detail": f"up at {url}" if healthy else f"deployed, but {url} said {said}"}
    if screenshot:
        from yaaf.core import artifacts

        result["screenshot_uri"] = artifacts.store().put_bytes(
            f"projects/{project.id}/changes/{change.id}/preview-{sha[:12]}.png", screenshot)
    change.findings = {**(change.findings or {}), "preview": {"sha": sha, **result}}
    evaluate(session, project, change)


# ── hidden scenarios ────────────────────────────────────────────────────────────


#: The words every EARS requirement shares, and the glue between words. A quote
#: made only of these ("THE SYSTEM SHALL") fits every requirement, so it shows
#: nothing about which one the scenario tests.
_SHARED_WORDS = frozenset(
    ["a", "an", "and", "any", "are", "as", "at", "be", "by", "each", "every", "for", "from", "if", "in", "into", "is", "it", "its", "of", "on", "or", "shall", "should", "system", "that", "the", "then", "this", "to", "when", "where", "which", "while", "with"])


def _content_words(text: str) -> list[str]:
    import re

    return [w for w in re.findall(r"[a-z0-9%$.]+", (text or "").lower().replace("'", ""))
            if w.strip(".") and w.strip(".") not in _SHARED_WORDS]


def _texts(value) -> list[str]:
    """Every string inside an acceptance test, which is structured data."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [t for v in value.values() for t in _texts(v)]
    if isinstance(value, list):
        return [t for v in value for t in _texts(v)]
    return []


def quotes_requirement(checks: str, requirement) -> bool:
    """Whether `checks` quotes three words running from the requirement's own
    statement or acceptance test, past the words every requirement shares."""
    quote = [w.strip(".") for w in _content_words(checks)]
    for source in (requirement.statement, *_texts(requirement.acceptance_test)):
        words = [w.strip(".") for w in _content_words(source or "")]
        runs = {tuple(words[i:i + 3]) for i in range(len(words) - 2)}
        if any(tuple(quote[i:i + 3]) in runs for i in range(len(quote) - 2)):
            return True
    return False


def live_scenarios(session: Session, requirement_id: str | None) -> list:
    from yaaf.core.models import Scenario

    if requirement_id is None:
        return []
    return list(session.execute(
        select(Scenario).where(Scenario.requirement_id == requirement_id,
                               Scenario.retired_at.is_(None)).order_by(Scenario.created_at)
    ).scalars())


def scenario_gate(session: Session, change: Change, cons: ConstitutionDoc) -> dict:
    if not cons.hidden_scenarios:
        return {"ok": True, "detail": "off for this project"}
    scenarios = live_scenarios(session, change.requirement_id)
    if not scenarios:
        return {"ok": False, "detail": "no hidden scenario has been written for this "
                                       "requirement yet — a challenge seat writes them"}
    run = change.scenario_results or {}
    if run.get("sha") != change.head_sha:
        return {"ok": False, "detail": f"{len(scenarios)} hidden, not yet run on this commit"}
    results = run.get("results") or {}
    passed = sum(1 for s in scenarios if results.get(s.id) is True)
    pending = sum(1 for s in scenarios if s.id not in results)
    return {"ok": passed == len(scenarios),
            "detail": (f"{passed} of {len(scenarios)} pass"
                       + (f", {pending} not yet run" if pending else ""))}


def queue_scenarios(session: Session, project: Project, change: Change) -> bool:
    """Ask the runner to run this change's hidden scenarios on its head. One
    queued run per change and commit; a newer push supersedes an older one."""
    from yaaf.core.models import RunKind, RunQueue

    if not change.head_sha or not live_scenarios(session, change.requirement_id):
        return False
    if not effective(session, project.id).hidden_scenarios:
        return False
    for queued in session.execute(
        select(RunQueue).where(RunQueue.project_id == project.id,
                               RunQueue.kind == RunKind.SCENARIO_CHECK)
    ).scalars():
        trigger = queued.trigger or {}
        if trigger.get("change_id") == change.id:
            if trigger.get("sha") == change.head_sha:
                return False
            session.delete(queued)
    session.add(RunQueue(
        id=ids.new_id(ids.RUN), project_id=project.id, agent_id=None,
        kind=RunKind.SCENARIO_CHECK, requirement_id=change.requirement_id,
        trigger={"type": "scenario_check", "change_id": change.id, "sha": change.head_sha,
                 "branch": change.branch}))
    return True


#: A scan or scenario job that finished without a result for the commit is
#: tried again after this long, up to LOST_JOB_TRIES times per commit.
LOST_JOB_AFTER = timedelta(minutes=3)
LOST_JOB_TRIES = 3


def requeue_lost_jobs(session: Session, project: Project) -> int:
    """Run again a change's scan or hidden scenarios whose job ended with no
    result for its head commit: a checkout fetch that hiccuped, a Docker error,
    a run reaped by a restart. Nothing else re-queues them; the gate said "not
    yet run on this commit" until somebody happened to push again."""
    from yaaf.core.models import Run, RunKind, RunQueue, RunStatus

    if not shape.is_factory(project):
        return 0
    cons = effective(session, project.id)
    live = list(session.execute(
        select(Change).where(Change.project_id == project.id,
                             Change.status.in_((ChangeStatus.OPEN, ChangeStatus.READY)),
                             Change.head_sha.is_not(None))).scalars())
    if not live:
        return 0
    since = utcnow() - timedelta(days=2)
    runs = list(session.execute(
        select(Run).where(Run.project_id == project.id, Run.created_at >= since,
                          Run.kind.in_((RunKind.SCENARIO_CHECK, RunKind.CHANGE_SCAN)))).scalars())
    queued = list(session.execute(
        select(RunQueue).where(RunQueue.project_id == project.id,
                               RunQueue.kind.in_((RunKind.SCENARIO_CHECK,
                                                  RunKind.CHANGE_SCAN)))).scalars())
    cutoff = utcnow() - LOST_JOB_AFTER
    requeued = 0
    for change in live:
        def lost(kind: str, change: Change = change) -> bool:
            mine = [r for r in runs if r.kind == kind
                    and (r.trigger or {}).get("change_id") == change.id
                    and (r.trigger or {}).get("sha") == change.head_sha]
            if any(q.kind == kind and (q.trigger or {}).get("change_id") == change.id
                   for q in queued):
                return False
            if not mine or len(mine) >= LOST_JOB_TRIES:
                return False   # never queued (nothing to recover), or tried enough
            if any(r.status == RunStatus.RUNNING for r in mine):
                return False
            ended = max((r.finished_at for r in mine if r.finished_at), default=None)
            return ended is not None and (ended if ended.tzinfo else ended.replace(tzinfo=UTC)) \
                <= cutoff

        results = change.scenario_results or {}
        if results.get("sha") != change.head_sha and lost(RunKind.SCENARIO_CHECK) \
                and queue_scenarios(session, project, change):
            requeued += 1
        findings = change.findings or {}
        wanted = [k for k, on in (("scan", cons.platform_scans),
                                  ("health", cons.code_health_ratchet)) if on]
        if any((findings.get(k) or {}).get("sha") != change.head_sha for k in wanted) \
                and lost(RunKind.CHANGE_SCAN) and queue_scan(session, project, change):
            requeued += 1
    return requeued


def record_scenarios(session: Session, change: Change, sha: str,
                     results: dict[str, bool], why: dict[str, str] | None = None) -> None:
    """What the runner saw. Ignored when the change has moved on since: a pass
    on an older commit is a pass on code that is no longer the change.

    A failure is told to the scenario's author, once per change, with the end
    of what it printed. Nobody could read why before: the Durations factory's
    R-01 scenario stubbed out a CLI that a later requirement builds, failed
    every CI change, and neither its author nor anyone else could tell the
    scenario was the one asking too much."""
    from yaaf.core.models import Scenario

    project = session.get(Project, change.project_id)
    if project is None or sha != change.head_sha:
        return
    why = {k: str(v)[:1500] for k, v in (why or {}).items() if results.get(k) is False}
    change.scenario_results = {"sha": sha, "results": dict(results), "why": why,
                               "at": utcnow().isoformat()}
    told = set((change.findings or {}).get("scenarios_told") or [])
    requirement = session.get(Requirement, change.requirement_id) if change.requirement_id \
        else None
    for scenario_id, ok in results.items():
        if ok or scenario_id in told:
            continue
        scenario = session.get(Scenario, scenario_id)
        if scenario is None or not scenario.author_agent_id:
            continue
        told.add(scenario_id)
        key = requirement.key if requirement else "its requirement"
        _tell(session, project, scenario.author_agent_id, NoticeKind.SCENARIO_FAILED,
              f"Your hidden scenario \"{scenario.name}\" fails on change #{change.number} "
              f"({change.title}), which serves {key}. It printed: "
              f"{why.get(scenario_id) or '(nothing)'}\n"
              f"If the change is wrong, say nothing: its author sees only the count. If the "
              f"change does what {key} says and your scenario asks for more (work a later "
              "requirement builds), retire it with scenario_retire and file it on the "
              "requirement it really tests.")
    if told:
        change.findings = {**(change.findings or {}), "scenarios_told": sorted(told)}
    _audit(session, project, "change.scenarios", change,
           {"sha": sha, "passed": sum(1 for ok in results.values() if ok),
            "total": len(results)})
    evaluate(session, project, change)


def scenario_counts(session: Session, project: Project) -> list[dict]:
    """What a seat that builds may know: how many scenarios guard each
    requirement. Never the text, the command or the name."""
    from yaaf.core.models import Scenario

    counts: dict[str, int] = {}
    for s in session.execute(
        select(Scenario).where(Scenario.project_id == project.id,
                               Scenario.retired_at.is_(None))
    ).scalars():
        counts[s.requirement_id] = counts.get(s.requirement_id, 0) + 1
    keys = {r.id: r.key for r in session.execute(
        select(Requirement).where(Requirement.id.in_(list(counts) or [""]))).scalars()}
    return [{"requirement": keys.get(rid, rid), "requirement_id": rid, "hidden": n}
            for rid, n in sorted(counts.items(), key=lambda kv: keys.get(kv[0], ""))]


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
    settle_merging(session, project)
    if merges_frozen(session, project) is not None:
        return 0
    cons = effective(session, project.id)
    merged = 0
    now = utcnow()
    ready = list(session.execute(
        select(Change).where(Change.project_id == project.id,
                             Change.status == ChangeStatus.READY)
        .order_by(Change.created_at)
    ).scalars())
    if ready and _main_moved_unseen(session, project):
        # Someone moved main since the platform last looked. Judge that push
        # before merging on top of it: once the platform's merge lands, the
        # poll sees only its own commit and the push is never judged at all.
        poll_github(session, project, force=True)
        reconcile_main(session, project)
        if merges_frozen(session, project) is not None:
            return 0
    for change in ready:
        retry_at = (change.findings or {}).get("merge_retry_at")
        if retry_at and datetime.fromisoformat(retry_at) > now:
            continue
        evaluate(session, project, change, cons)
        if change.status != ChangeStatus.READY:
            continue
        running = github.suites_running(session, project.github_repo or "",
                                        change.head_sha or "")
        if running:
            # Green on every run reported so far, but CI is not done: a job
            # waiting on another has not even reported yet. Merged now, it would
            # land code its own CI is still judging.
            change.status_reason = f"CI is still running on this commit ({running} suite(s))"
            continue
        change.status = ChangeStatus.MERGING
        session.commit()
        try:
            sha = github.merge_pull_request(
                session, project.github_repo or "", change.number or 0,
                sha=change.head_sha or "", title=f"{change.title} (#{change.number})")
        except github.GithubError as exc:
            if _merge_failed(session, project, change, exc):
                merged += 1
            session.commit()
            continue
        _finish_merge(session, project, change, sha)
        session.commit()
        merged += 1
    return merged


def _main_moved_unseen(session: Session, project: Project) -> bool:
    from yaaf.server import upkeep

    known = upkeep.main_head(session, project.id)
    if known is None or not project.github_repo:
        return False
    try:
        head = github.branch_head(session, project.github_repo, _default_branch(project))
    except github.GithubError:
        return False
    return bool(head) and head != known


#: How long a change may sit in MERGING before the platform asks GitHub what
#: became of it. The merge call itself times out in seconds; a change still
#: MERGING minutes later is one whose answer was lost (a restart mid-call).
MERGING_STALE = timedelta(minutes=5)
#: How long to wait before trying again after GitHub could not be reached.
MERGE_RETRY = timedelta(minutes=2)


def _finish_merge(session: Session, project: Project, change: Change, sha: str) -> None:
    change.status = ChangeStatus.MERGED
    change.merged_sha = sha
    change.merged_at = utcnow()
    change.status_reason = None
    _audit(session, project, "change.merged", change,
           {"sha": sha, "head": change.head_sha, "reviewer": change.reviewer_agent_id})
    policy.attest_shipped(
        session, project, f"Change #{change.number} merged to main: {change.title}",
        f"https://github.com/{project.github_repo}/pull/{change.number}"
        if project.github_repo and change.number else None, task_id=change.task_id)
    _tell(session, project, change.author_agent_id, NoticeKind.CHANGE_MERGED,
          f"Change #{change.number} ({change.title}) is merged: {sha[:12]}.")


def _merge_failed(session: Session, project: Project, change: Change,
                  exc: Exception) -> bool:
    """What a failed merge call really means, read back from the PR. True when
    it had in fact merged.

    A timeout after GitHub merged used to put the change back to OPEN; the poll
    then saw the PR merged, closed it as "merged on GitHub, outside the
    platform", and reconcile_main froze merging over the platform's own merge.
    A GitHub 502 left a green change OPEN with nothing to judge it again, and a
    merge GitHub cannot make (a conflict) told nobody."""
    try:
        pr = github.get_pull_request(session, project.github_repo or "", change.number or 0)
    except github.GithubError:
        pr = {}
    if pr.get("merged") and pr.get("merge_commit_sha"):
        _finish_merge(session, project, change, pr["merge_commit_sha"])
        return True
    said = str(exc)
    _audit(session, project, "change.merge_failed", change, {"error": said[:500]})
    status = next((code for code in ("405", "409", "422") if f": {code}" in said), None)
    if pr.get("state") == "closed":
        change.status = ChangeStatus.CLOSED
        change.status_reason = "closed on GitHub before it could merge"
        _tell(session, project, change.author_agent_id, NoticeKind.CHANGE_RETURNED,
              f"Change #{change.number} ({change.title}) was closed on GitHub before it "
              "merged.")
    elif status:
        change.status = ChangeStatus.OPEN
        change.status_reason = said[:1000]
        change.findings = {**(change.findings or {}),
                           "merge_refused": {"sha": change.head_sha, "status": status}}
        evaluate(session, project, change)
        _tell(session, project, change.author_agent_id, NoticeKind.CHANGE_RETURNED,
              f"Change #{change.number} ({change.title}) passed every gate but GitHub could "
              f"not merge it onto main ({status}). Rebase the branch onto main and push; "
              "the gates run again on the new commit.")
    else:
        # GitHub could not be reached or failed: still green, tried again soon.
        change.status = ChangeStatus.READY
        change.status_reason = f"merge not made yet, trying again: {said[:900]}"
        change.findings = {**(change.findings or {}),
                           "merge_retry_at": (utcnow() + MERGE_RETRY).isoformat()}
    return False


def settle_merging(session: Session, project: Project) -> int:
    """Changes left in MERGING by a call whose answer never came back (the
    process died mid-merge). Nothing else ever moves a MERGING change, and a
    MERGING change also stops its task from opening another."""
    settled = 0
    cutoff = utcnow() - MERGING_STALE
    for change in list(session.execute(
        select(Change).where(Change.project_id == project.id,
                             Change.status == ChangeStatus.MERGING)
    ).scalars()):
        at = change.updated_at
        if at is not None and (at if at.tzinfo else at.replace(tzinfo=UTC)) > cutoff:
            continue
        try:
            pr = github.get_pull_request(session, project.github_repo or "", change.number or 0)
        except github.GithubError:
            continue
        if pr.get("merged") and pr.get("merge_commit_sha"):
            _finish_merge(session, project, change, pr["merge_commit_sha"])
        elif pr.get("state") == "closed":
            change.status = ChangeStatus.CLOSED
            change.status_reason = "closed on GitHub before it could merge"
        else:
            change.status = ChangeStatus.READY
        settled += 1
    return settled


#: How often a factory asks GitHub for what its webhooks would have said.
POLL_EVERY_S = 60
_last_poll: dict[str, float] = {}


def poll_github(session: Session, project: Project, *, force: bool = False) -> dict:
    """Read from GitHub what the webhooks carry: each open change's head and
    state, the checks on that head, and main's head and its checks.

    A webhook needs GitHub to reach this install, and most installs are a box
    on a desk at localhost — there the checks gate waited forever on "no check
    has reported on this commit yet". Polling makes the webhook a speed-up
    rather than a requirement. Every handler it feeds is the webhook's own, and
    each is fed only what it has not seen, so a hook that does arrive and this
    poll never count the same thing twice."""
    import time

    if not shape.is_factory(project) or not project.github_repo:
        return {"skipped": "no repo"}
    now = time.monotonic()
    if not force and now - _last_poll.get(project.id, -1e9) < POLL_EVERY_S:
        return {"skipped": "polled recently"}
    _last_poll[project.id] = now
    from yaaf.server import upkeep

    repo = project.github_repo
    seen = {"changes": 0, "checks": 0, "main": None}
    try:
        for change in list(session.execute(
            select(Change).where(Change.project_id == project.id,
                                 Change.status.in_((ChangeStatus.OPEN, ChangeStatus.READY)),
                                 Change.number.is_not(None))
        ).scalars()):
            pr = github.get_pull_request(session, repo, change.number or 0)
            if pr.get("state") == "closed":
                pull_request_event(session, project, "closed", pr)
                continue
            if (pr.get("head") or {}).get("sha") not in (None, change.head_sha):
                pull_request_event(session, project, "synchronize", pr)
            seen["changes"] += 1
            for run in github.check_runs(session, repo, change.head_sha or ""):
                conclusion = (run.get("conclusion") or "pending") \
                    if run.get("status") == "completed" else "pending"
                if (change.checks or {}).get(str(run.get("name") or "check")[:120]) \
                        != conclusion:
                    check_run_event(session, project, run)
                    seen["checks"] += 1
        # A branch renamed on GitHub (main → trunk) answers the old name with
        # a redirect, read as "no head": main tracking would stop in silence.
        named = github.default_branch(session, repo)
        if named and named != _default_branch(project):
            project.integrations = {**(project.integrations or {}), "default_branch": named}
        head = github.branch_head(session, repo, _default_branch(project))
        known = upkeep.main_head(session, project.id)
        if head and known is None:
            # The first sighting is where main starts, not a push around the
            # gates: a new repo's initial commit would otherwise freeze merging
            # before the factory had made a single change.
            upkeep.on_push(session, project, head)
            seen["main"] = head
        elif head and head != known:
            pushed = push_event(session, project, {
                "ref": f"refs/heads/{_default_branch(project)}", "after": head,
                "pusher": {"name": "seen by polling"}})
            upkeep.on_push(session, project, pushed.get("main_push"))
            seen["main"] = head
        if head:
            recorded = {(r.key, (r.data or {}).get("conclusion")) for r in
                        upkeep._signals(session, project.id, "main_check")}
            for run in github.check_runs(session, repo, head):
                conclusion = (run.get("conclusion") or "pending") \
                    if run.get("status") == "completed" else "pending"
                if (f"{head}:{run.get('name')}", conclusion) not in recorded:
                    upkeep.on_check_run(session, project, run)
    except github.GithubError as exc:
        return {**seen, "error": str(exc)[:300]}
    return seen


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
        "findings": {k: v for k, v in (change.findings or {}).items() if k != "error"},
        "waivers": change.waivers or {},
        "chairman_sha": change.chairman_sha,
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
