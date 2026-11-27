"""A Code Factory's upkeep promises, measured by the platform.

When a factory enters upkeep the platform writes one standing promise per
upkeep rule the chairman left on — main green, issues answered, advisories
fixed in time, flaky checks, package age — each a METRIC requirement whose
source is `factory`. Its readings come from `FactorySignal` rows, which only
the platform writes: from signed GitHub webhooks (main's check runs, issues and
replies, Dependabot alerts) and from package registries it reads itself. No
agent verb writes a signal, so no agent's account of the week enters a verdict.

A promise is about the whole week, so it is not settled the moment a reading
looks good (a Monday-morning "100% green" proves nothing). It is judged once,
at the roll, over the cycle that is closing — honoured or missed — and owed
again the next cycle like every standing obligation.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core import ids, safefetch, shape
from yaaf.core.constitution import ConstitutionDoc, effective
from yaaf.core.models import (
    Cycle,
    FactorySignal,
    MetricSourceKind,
    Project,
    Recurrence,
    Requirement,
    RequirementStatus,
    SuccessTestKind,
    utcnow,
)

log = logging.getLogger(__name__)

#: Each measure, which way is winning, and the sentence its promise says.
MEASURES: dict[str, str] = {
    "main_green_pct": "increasing",
    "issue_wait_hours_max": "decreasing",
    "advisories_overdue": "decreasing",
    "flaky_checks": "decreasing",
    "packages_majors_behind": "decreasing",
    "health_regressions": "decreasing",
}
MEMBER = {"OWNER", "MEMBER", "COLLABORATOR"}
GREEN = {"success", "neutral", "skipped"}


def _aware(at: datetime) -> datetime:
    return at if at.tzinfo else at.replace(tzinfo=UTC)


# ── signals in ──────────────────────────────────────────────────────────────────


def signal(session: Session, project: Project, kind: str, key: str, data: dict,
           at: datetime | None = None) -> FactorySignal:
    row = FactorySignal(id=ids.new_id(ids.FACTORY_SIGNAL), project_id=project.id, kind=kind,
                        key=str(key)[:200], data=data, at=at or utcnow())
    session.add(row)
    return row


def _signals(session: Session, project_id: str, kind: str, until: datetime | None = None):
    query = select(FactorySignal).where(FactorySignal.project_id == project_id,
                                        FactorySignal.kind == kind)
    if until is not None:
        query = query.where(FactorySignal.at <= until)
    return list(session.execute(query.order_by(FactorySignal.at)).scalars())


def main_head(session: Session, project_id: str, at: datetime | None = None) -> str | None:
    heads = _signals(session, project_id, "main_head", until=at)
    return heads[-1].data.get("sha") if heads else None


def on_push(session: Session, project: Project, sha: str | None) -> None:
    if sha:
        signal(session, project, "main_head", sha, {"sha": sha})


def on_check_run(session: Session, project: Project, run: dict) -> bool:
    """A check that ran on main's head counts toward main green and flaky."""
    sha = run.get("head_sha")
    if not sha or sha != main_head(session, project.id):
        return False
    conclusion = (run.get("conclusion") or "pending") if run.get("status") == "completed" \
        else "pending"
    signal(session, project, "main_check", f"{sha}:{run.get('name')}",
           {"sha": sha, "name": str(run.get("name") or "check")[:120],
            "conclusion": conclusion})
    return True


def on_issue(session: Session, project: Project, action: str, payload: dict) -> str | None:
    issue = payload.get("issue") or {}
    number = issue.get("number")
    if number is None or issue.get("pull_request"):
        return None
    sender = payload.get("sender") or {}
    if action == "opened":
        signal(session, project, "issue_opened", str(number),
               {"number": number, "author": (issue.get("user") or {}).get("login"),
                "member": issue.get("author_association") in MEMBER})
        return "opened"
    if action in ("labeled", "closed", "assigned") and _is_member(project, payload, sender):
        signal(session, project, "issue_answered", str(number),
               {"number": number, "by": sender.get("login"), "how": action})
        return "answered"
    return None


def on_issue_comment(session: Session, project: Project, payload: dict) -> str | None:
    issue = payload.get("issue") or {}
    comment = payload.get("comment") or {}
    if issue.get("pull_request") or issue.get("number") is None:
        return None
    author = (comment.get("user") or {}).get("login")
    if author and author == (issue.get("user") or {}).get("login"):
        return None  # the reporter adding detail is not an answer
    if comment.get("author_association") in MEMBER or _controlled(project, author):
        signal(session, project, "issue_answered", str(issue["number"]),
               {"number": issue["number"], "by": author, "how": "comment"})
        return "answered"
    return None


def on_dependabot_alert(session: Session, project: Project, action: str, alert: dict) -> str:
    """GitHub's own advisory record, with GitHub's own dates."""
    advisory = alert.get("security_advisory") or {}
    vuln = alert.get("security_vulnerability") or {}
    severity = (vuln.get("severity") or advisory.get("severity") or "").lower()
    state = {"created": "open", "reopened": "open", "reintroduced": "open",
             "fixed": "fixed", "dismissed": "dismissed",
             "auto_dismissed": "dismissed"}.get(action, alert.get("state") or "open")
    signal(session, project, "advisory", str(alert.get("number")), {
        "number": alert.get("number"), "severity": severity, "state": state,
        "created_at": alert.get("created_at"),
        "package": ((vuln.get("package") or {}).get("name")),
        "ghsa": advisory.get("ghsa_id")})
    return state


def _controlled(project: Project, login: str | None) -> bool:
    return bool(login) and f"github:{login.lower()}" in {
        str(d).lower() for d in project.controlled_domains or []}


def _is_member(project: Project, payload: dict, sender: dict) -> bool:
    issue = payload.get("issue") or {}
    if _controlled(project, sender.get("login")):
        return True
    # GitHub only sends labels and closes from people with triage rights.
    return sender.get("login") != (issue.get("user") or {}).get("login")


# ── measures out ────────────────────────────────────────────────────────────────


def measure(session: Session, project: Project, name: str, start: datetime,
            end: datetime) -> tuple[float, str]:
    """One measure over [start, end]. Raises ValueError when the repo reported
    nothing to measure — a silent week is unchecked, not honoured."""
    start, end = _aware(start), _aware(end)
    if name == "main_green_pct":
        return _main_green(session, project, start, end)
    if name == "issue_wait_hours_max":
        return _issue_wait(session, project, start, end)
    if name == "advisories_overdue":
        return _advisories_overdue(session, project, end)
    if name == "flaky_checks":
        return _flaky(session, project, start, end)
    if name == "packages_majors_behind":
        return _majors_behind(session, project)
    if name == "health_regressions":
        return _health_regressions(session, project, start, end)
    raise ValueError(f"unknown upkeep measure {name!r}")


def _state_of(checks: dict[str, str]) -> str | None:
    if not checks:
        return None
    if any(c not in GREEN and c != "pending" for c in checks.values()):
        return "red"
    if any(c == "pending" for c in checks.values()):
        return None
    return "green"


def _main_green(session, project, start, end) -> tuple[float, str]:
    """Time-weighted: each stretch of the cycle takes the state of main's head
    then, by the last result of each check on it. A stretch whose checks are
    still running is left out rather than guessed."""
    heads = _signals(session, project.id, "main_head", until=end)
    checks: dict[str, dict[str, str]] = {}
    for row in _signals(session, project.id, "main_check", until=end):
        checks.setdefault(row.data["sha"], {})[row.data["name"]] = row.data["conclusion"]
    marks = [(max(_aware(h.at), start), h.data["sha"]) for h in heads]
    if not marks:
        raise ValueError("main reported no commit")
    # The head in force at `start` is the last one pushed before it.
    marks = [m for m in marks if m[0] > start] or [marks[-1]]
    before = [h for h in heads if _aware(h.at) <= start]
    if before:
        marks = [(start, before[-1].data["sha"])] + [m for m in marks if m[0] > start]
    green = known = 0.0
    for i, (at, sha) in enumerate(marks):
        stop = marks[i + 1][0] if i + 1 < len(marks) else end
        span = max(0.0, (stop - at).total_seconds())
        state = _state_of(checks.get(sha, {}))
        if state is None:
            continue
        known += span
        if state == "green":
            green += span
    if known <= 0:
        raise ValueError("no check finished on main this cycle")
    pct = round(100.0 * green / known, 1)
    return pct, f"main green {pct}% of {known / 3600:.1f} measured hours"


def _issue_wait(session, project, start, end) -> tuple[float, str]:
    answered: dict[str, datetime] = {}
    for row in _signals(session, project.id, "issue_answered", until=end):
        answered.setdefault(row.key, _aware(row.at))
    longest, count, worst = 0.0, 0, None
    for row in _signals(session, project.id, "issue_opened", until=end):
        if row.data.get("member"):
            continue  # the crew's own notes are not issues waiting on it
        opened = _aware(row.at)
        reply = answered.get(row.key)
        if reply is not None and reply < start:
            continue  # answered before this cycle began
        waited = ((reply or end) - opened).total_seconds() / 3600
        count += 1
        if waited > longest:
            longest, worst = waited, row.data.get("number")
    if count == 0:
        return 0.0, "no issue waited on the crew this cycle"
    return round(longest, 1), f"longest wait {longest:.1f}h (#{worst}) across {count} issue(s)"


def _advisories_overdue(session, project, end) -> tuple[float, str]:
    cons = effective(session, project.id)
    limits = {"critical": cons.upkeep_critical_days, "high": cons.upkeep_high_days}
    latest: dict[str, dict] = {}
    for row in _signals(session, project.id, "advisory", until=end):
        latest[row.key] = row.data
    overdue = []
    for data in latest.values():
        days = limits.get(data.get("severity") or "")
        if not days or data.get("state") != "open" or not data.get("created_at"):
            continue
        created = _aware(datetime.fromisoformat(str(data["created_at"]).replace("Z", "+00:00")))
        if end - created > timedelta(days=days):
            overdue.append(f"{data.get('severity')} {data.get('package') or ''}".strip())
    return float(len(overdue)), (f"{len(overdue)} overdue: {', '.join(overdue[:5])}"
                                 if overdue else f"{len(latest)} advisories seen, none overdue")


def _flaky(session, project, start, end) -> tuple[float, str]:
    seen: dict[tuple[str, str], set[str]] = {}
    any_check = False
    for row in _signals(session, project.id, "main_check", until=end):
        if _aware(row.at) < start:
            continue
        any_check = True
        seen.setdefault((row.data["sha"], row.data["name"]), set()).add(row.data["conclusion"])
    if not any_check:
        raise ValueError("no check ran on main this cycle")
    flaky = sorted({name for (_sha, name), results in seen.items()
                    if results & GREEN and results - GREEN - {"pending"}})
    return float(len(flaky)), (f"flaky: {', '.join(flaky[:5])}" if flaky else "none flaky")


def _health_regressions(session, project, start, end) -> tuple[float, str]:
    """How much harder to maintain main got over the cycle: over-limit
    functions plus lint findings, last reading against the one in force when
    the cycle began."""
    rows = _signals(session, project.id, "code_health", until=end)
    if not rows:
        raise ValueError("main's health has not been measured yet")
    before = [r for r in rows if _aware(r.at) <= start] or rows[:1]

    def score(row) -> int:
        return int(row.data.get("over", 0)) + int(row.data.get("lint", 0))

    first, last = before[-1], rows[-1]
    grew = max(0, score(last) - score(first))
    return float(grew), (f"{grew} more over-limit functions and lint findings on main"
                         if grew else f"no worse ({score(last)} now, {score(first)} at start)")


def record_main_health(session: Session, project: Project, sha: str, reading: dict) -> None:
    if not sha or not reading or not all((reading.get("available") or {}).values()):
        return
    signal(session, project, "code_health", sha,
           {"sha": sha, "over": reading.get("over", 0), "lint": reading.get("lint", 0),
            "functions": reading.get("functions", 0)})


def queue_main_health(session: Session, project: Project) -> bool:
    """Once a day per factory in upkeep, measure main's head."""
    from yaaf.core.models import RunKind, RunQueue

    if not shape.is_factory(project) or not shape.runs_on_cycles(project):
        return False
    if not effective(session, project.id).upkeep_health_enabled:
        return False
    sha = main_head(session, project.id)
    if not sha:
        return False
    measured = _signals(session, project.id, "code_health")
    if measured and (measured[-1].data.get("sha") == sha
                     or utcnow() - _aware(measured[-1].at) < timedelta(hours=24)):
        return False
    pending = [q for q in session.execute(select(RunQueue).where(
        RunQueue.project_id == project.id, RunQueue.kind == RunKind.CHANGE_SCAN)).scalars()
        if (q.trigger or {}).get("type") == "main_health"]
    if pending:
        return False
    session.add(RunQueue(id=ids.new_id(ids.RUN), project_id=project.id, agent_id=None,
                         kind=RunKind.CHANGE_SCAN,
                         trigger={"type": "main_health", "sha": sha,
                                  "branch": str((project.integrations or {}).get(
                                      "default_branch") or "main")}))
    return True


def _majors_behind(session, project) -> tuple[float, str]:
    rows = _signals(session, project.id, "package_lag")
    if not rows:
        raise ValueError("no package check has run yet")
    newest = max(_aware(r.at) for r in rows)
    batch = [r for r in rows if _aware(r.at) >= newest - timedelta(minutes=5)]
    worst = max(batch, key=lambda r: r.data.get("behind", 0))
    behind = int(worst.data.get("behind", 0))
    return float(behind), (f"{worst.data.get('name')} is {behind} major(s) behind"
                           if behind else f"{len(batch)} packages, all current")


# ── the promises ────────────────────────────────────────────────────────────────


def _wanted(cons: ConstitutionDoc) -> list[tuple[str, str, float]]:
    out = []
    if cons.upkeep_main_green_pct:
        out.append(("main_green_pct",
                    f"Main stays green at least {cons.upkeep_main_green_pct}% of every cycle",
                    float(cons.upkeep_main_green_pct)))
    if cons.upkeep_issue_hours:
        out.append(("issue_wait_hours_max",
                    f"Every new issue is answered within {cons.upkeep_issue_hours} hours",
                    float(cons.upkeep_issue_hours)))
    if cons.upkeep_critical_days or cons.upkeep_high_days:
        out.append(("advisories_overdue",
                    f"Critical advisories are fixed within {cons.upkeep_critical_days} days and "
                    f"high ones within {cons.upkeep_high_days}", 0.0))
    if cons.upkeep_flaky_enabled:
        out.append(("flaky_checks",
                    f"At most {cons.upkeep_flaky_max} flaky checks on main",
                    float(cons.upkeep_flaky_max)))
    if cons.upkeep_health_enabled:
        out.append(("health_regressions",
                    "Main is no harder to maintain at the end of every cycle than at its start",
                    0.0))
    if cons.upkeep_packages_enabled:
        out.append(("packages_majors_behind",
                    f"No direct dependency is more than {cons.upkeep_majors_behind} major "
                    "version(s) behind", float(cons.upkeep_majors_behind)))
    return out


def is_upkeep_promise(requirement: Requirement) -> bool:
    source = ((requirement.acceptance_test or {}).get("source") or {})
    return source.get("kind") == MetricSourceKind.FACTORY


def write_promises(session: Session, project: Project, cycle: Cycle | None) -> int:
    """The chairman's upkeep rules, as standing requirements. Accepted on
    creation for the reason R-00 is: he set them in the constitution, so this
    is the platform writing down what he already decided, not scope it chose."""
    from yaaf.server import policy

    if not shape.is_factory(project):
        return 0
    cons = effective(session, project.id)
    have = {(r.acceptance_test or {}).get("source", {}).get("measure"): r
            for r in session.execute(select(Requirement).where(
                Requirement.project_id == project.id)).scalars()
            if is_upkeep_promise(r) and r.status != RequirementStatus.CUT}
    wanted = _wanted(cons)
    # The rules as they stand now. A promise was written once and never again,
    # so moving "answered within 24 hours" to 48, or to 0 (off), changed
    # nothing while the settings screen said it had.
    for measure_name, row in have.items():
        now = next(((st, t) for m, st, t in wanted if m == measure_name), None)
        if now is None:
            row.status, row.cut_reason, row.cut_at = (RequirementStatus.CUT,
                                                      "the chairman turned this rule off",
                                                      utcnow())
        elif (row.statement, row.acceptance_test.get("target")) != now:
            row.statement = now[0]
            row.acceptance_test = {**row.acceptance_test, "target": now[1]}
            row.meta = {**(row.meta or {}), "title": now[0]}
    written = 0
    for measure_name, statement, target in wanted:
        if measure_name in have:
            continue
        requirement = Requirement(
            id=ids.new_id(ids.REQUIREMENT), project_id=project.id,
            key=policy.next_requirement_key(session, project.id), statement=statement,
            acceptance_test={"type": SuccessTestKind.METRIC,
                             "source": {"kind": MetricSourceKind.FACTORY,
                                        "measure": measure_name},
                             "direction": MEASURES[measure_name], "target": target,
                             "measure_every_hours": 6},
            status=RequirementStatus.PENDING, stage=1, recurrence=Recurrence.STANDING,
            cycle_id=cycle.id if cycle else None, est_runs=1, accepted_at=utcnow(),
            meta={"title": statement, "upkeep": measure_name})
        session.add(requirement)
        session.flush()
        policy.record_requirement_event(session, requirement, RequirementStatus.PENDING,
                                        "platform", note="upkeep promise from the rules",
                                        from_status=None)
        written += 1
    return written


def judge_at_roll(session: Session, project: Project, cycle: Cycle,
                  scope: list[Requirement], until: datetime) -> int:
    """The verdict on each upkeep promise for the cycle that is closing: MET
    when the week's number is on the right side of the target, otherwise left
    open, so the scorecard reads it as missed (or unchecked, if the repo said
    nothing all week)."""
    from yaaf.server import metrics, policy

    judged = 0
    for requirement in scope:
        if requirement.recurrence != Recurrence.STANDING or not is_upkeep_promise(requirement):
            continue
        if requirement.status in (RequirementStatus.CUT, RequirementStatus.PROPOSED):
            continue
        name = requirement.acceptance_test["source"].get("measure")
        try:
            value, note = measure(session, project, name, cycle.opened_at, until)
        except ValueError as exc:
            requirement.check_output = f"unchecked: {exc}"[:4000]
            continue
        requirement.check_output = note[:4000]
        if metrics.met(requirement, value) and requirement.status != RequirementStatus.MET:
            was = requirement.status
            requirement.status = RequirementStatus.MET
            requirement.met_at = until
            policy.record_requirement_event(session, requirement, RequirementStatus.MET,
                                            "platform", f"cycle {cycle.ordinal}: {note}",
                                            from_status=was)
        elif not metrics.met(requirement, value) and requirement.status == RequirementStatus.MET:
            requirement.status = RequirementStatus.IN_PROGRESS
            policy.record_requirement_event(
                session, requirement, RequirementStatus.IN_PROGRESS, "platform",
                f"cycle {cycle.ordinal} missed: {note}", from_status=RequirementStatus.MET)
        judged += 1
    return judged


# ── package lag, read from the registries ───────────────────────────────────────

_MAJOR = re.compile(r"(\d+)")


def _major(spec: str) -> int | None:
    m = _MAJOR.search(spec or "")
    return int(m.group(1)) if m else None


def _manifest_deps(name: str, text: str) -> dict[str, str]:
    deps: dict[str, str] = {}
    if name == "package.json":
        try:
            doc = json.loads(text)
        except ValueError:
            return deps
        for section in ("dependencies", "devDependencies"):
            for pkg, spec in (doc.get(section) or {}).items():
                if isinstance(spec, str) and _major(spec) is not None:
                    deps[f"npm:{pkg}"] = spec
    elif name == "requirements.txt":
        for line in text.splitlines():
            m = re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9_.-]*)\s*(==|>=|~=)\s*([0-9][^\s;,]*)", line)
            if m:
                deps[f"pypi:{m.group(1).lower()}"] = m.group(3)
    return deps


def _latest_major(kind_name: str) -> int | None:
    kind, name = kind_name.split(":", 1)
    url = (f"https://registry.npmjs.org/{name}/latest" if kind == "npm"
           else f"https://pypi.org/pypi/{name}/json")
    status, body = safefetch.fetch(url, timeout=15.0, max_bytes=5_000_000,
                                   user_agent="yaaf-upkeep/0.1")
    if status != 200:
        return None
    doc = json.loads(body)
    version = doc.get("version") if kind == "npm" else (doc.get("info") or {}).get("version")
    return _major(version or "")


def check_packages(session: Session, project: Project, files: dict[str, str]) -> int:
    """Record how far each direct dependency on main trails its registry.
    `files` is {manifest name: text}, read from main by the platform."""
    recorded = 0
    now = utcnow()
    for name, text in files.items():
        for dep, spec in _manifest_deps(name, text).items():
            current = _major(spec)
            try:
                latest = _latest_major(dep)
            except Exception:  # noqa: BLE001 — one bad registry read skips one package
                latest = None
            if current is None or latest is None:
                continue
            signal(session, project, "package_lag", dep,
                   {"name": dep, "current": current, "latest": latest,
                    "behind": max(0, latest - current)}, at=now)
            recorded += 1
    return recorded


def refresh_packages(session: Session, project: Project) -> int:
    """Once a day per factory in upkeep: read the manifests on main through the
    platform token and check each dependency against its registry."""
    from yaaf.server import github

    if not shape.runs_on_cycles(project) or not shape.is_factory(project):
        return 0
    if not effective(session, project.id).upkeep_packages_enabled or not project.github_repo:
        return 0
    last = _signals(session, project.id, "package_lag")
    if last and utcnow() - _aware(last[-1].at) < timedelta(hours=24):
        return 0
    files = {}
    for name in ("package.json", "requirements.txt"):
        try:
            text = github.read_file(session, project.github_repo, name)
        except github.GithubError:
            continue
        if text is not None:
            files[name] = text
    return check_packages(session, project, files) if files else 0
