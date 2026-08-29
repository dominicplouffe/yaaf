"""What the drill found, and what it is entitled to claim.

Two halves, kept apart on purpose.

**Observed** is what actually happened when the spec was run: the walk from
roles resolving to work closing with evidence, the walls the crew hit, and
every point at which the project stopped and waited for a human. These are
facts about the configuration, and the drill can state them flatly.

**Projected** is arithmetic over the plan the drill produced — what it will
cost, how long the caps say it takes, which of the four limits binds first.
This half is computed from the chairman's *real* numbers (not the three the
drill relaxed) using the same producers the Plan tab and the desk read, so it
can never quietly disagree with the screens he will look at afterwards.

And one thing that is neither: what a real model would do. The crew is
competent and dull by construction, so a clean drill is evidence about the
*configuration* and no evidence at all about the *project*. That sentence
appears in the rendered report rather than only in this docstring, because a
caveat nobody reads is a caveat that does not exist.

Money is reported in **runs**, and in dollars only where the box has actually
priced runs. `policy.avg_usd_per_run` needs five priced runs before it will
answer, and a drill produces none — so rather than invent a price the report
inverts the question and gives the break-even: *what a run has to cost for this
plan to fit your cap*. That is one number a chairman can check against a bill
he has already seen, and it is honest on a box that has never spent anything.
"""

from __future__ import annotations

from datetime import timedelta

#: The walk, in the order the platform takes it. Every step is a real gate: if
#: one does not happen, the ones under it cannot, and naming them in order is
#: what turns "it didn't work" into "it stopped here".
WALK = [
    ("roles", "Roles resolved"),
    ("created", "Project created"),
    ("relaxed", "Clock rules lifted for the drill"),
    ("started", "Project started"),
    ("woke", "An agent woke"),
    ("plan_proposed", "The CEO proposed a plan"),
    ("scope_accepted", "Scope entered the plan"),
    ("plan_gate", "The plan gate closed — the board opened"),
    ("work_created", "Work was created"),
    ("work_claimed", "Work was claimed"),
    ("evidence", "Evidence was filed"),
    ("work_closed", "Work closed with evidence"),
    ("commitment", "A delivery date was proposed"),
    ("commitment_signed", "The delivery date was signed"),
    ("requirement_met", "A requirement settled true"),
]

#: The steps an Explore project has no business reaching. It has no
#: requirements, no plan gate and no commitment BY DESIGN — its shape is bets
#: and evidence — so drawing them as misses would report the absence of a
#: feature as the failure of one.
NOT_IN_EXPLORE = {"plan_proposed", "scope_accepted", "plan_gate", "commitment",
                  "commitment_signed", "requirement_met"}


def _mark(steps: list[dict], key: str, ok: bool, detail: str, tick: int | None) -> None:
    """First occurrence wins. A drill records when a thing STARTED being true,
    because "work closed on beat 3" and "work closed on beats 3 through 11" are
    the same fact about a configuration."""
    if any(s["key"] == key and s["ok"] for s in steps):
        return
    steps[:] = [s for s in steps if s["key"] != key]
    steps.append({"key": key, "ok": ok, "detail": detail, "tick": tick})


def observe(client, project_id: str, tick: int, steps: list[dict]) -> None:
    """Record whatever became true on this beat."""
    runs = client.get(f"/observe/projects/{project_id}/runs?limit=200").json()
    if runs:
        woke = {r.get("agent_name") for r in runs if r.get("agent_name")}
        _mark(steps, "woke", True,
              f"{len(runs)} run(s) across {len(woke)} agent(s) — the dispatcher "
              "found a reason", tick)

    plan = client.get(f"/observe/projects/{project_id}/plan").json()
    requirements = [r for r in plan.get("requirements", []) if r["key"] != "R-00"]
    if requirements:
        _mark(steps, "plan_proposed", True,
              f"{len(requirements)} requirement(s), "
              f"{sum(r.get('est_runs') or 0 for r in requirements)} estimated runs", tick)
    accepted = [r for r in requirements if r["status"] != "proposed"]
    if accepted:
        _mark(steps, "scope_accepted", True, f"{len(accepted)} accepted", tick)
    # R-00 is not in `requirements` — the plan view gives the gate its own
    # block, because it is the thing in front of scope rather than part of it.
    gate = plan.get("plan") or {}
    if gate.get("holds"):
        _mark(steps, "plan_gate", True,
              "the plan is staged, estimated and fits the money", tick)
    elif gate.get("outstanding"):
        # Not a milestone, a diagnosis: the platform already writes the
        # sentence explaining what the plan is still missing, and a drill that
        # paraphrased it would be a second voice to drift.
        _mark(steps, "plan_gate", False, "; ".join(gate["outstanding"])[:300], tick)
    met = [r for r in requirements if r["status"] == "met"]
    if met:
        _mark(steps, "requirement_met", True,
              f"{', '.join(r['key'] for r in met)} — the platform ran the test", tick)
    commitment = plan.get("commitment")
    if commitment:
        _mark(steps, "commitment", True,
              f"platform-projected, delivering by {commitment['deliver_by'][:10]}", tick)
        if commitment["status"] != "proposed":
            _mark(steps, "commitment_signed", True, f"status {commitment['status']}", tick)

    board = client.get(f"/observe/projects/{project_id}/board").json()
    if board:
        _mark(steps, "work_created", True, f"{len(board)} task(s)", tick)
    if any(t["status"] in ("claimed", "checking", "done", "blocked") for t in board):
        _mark(steps, "work_claimed", True, "an agent picked it up", tick)
    done = [t for t in board if t["status"] == "done"]
    if done:
        _mark(steps, "work_closed", True, f"{len(done)} closed", tick)

    evidence = client.get(f"/observe/projects/{project_id}/evidence").json()
    if evidence:
        _mark(steps, "evidence", True, f"{len(evidence)} row(s) filed", tick)


# ── the projected half ──────────────────────────────────────────────────────────


def _real_constitution(project_id: str) -> dict:
    """The chairman's own numbers, not the drill's.

    The three clock rules were relaxed by an amendment, so the constitution in
    force at the end is not the one he wrote. Version 1 is, and the arithmetic
    below has to use it or the report would tell him his daily ceiling is 9999.
    """
    from sqlalchemy import select

    from yaaf.core.constitution import ConstitutionDoc
    from yaaf.core.db import db_session
    from yaaf.core.models import Constitution

    with db_session() as session:
        row = session.execute(
            select(Constitution).where(Constitution.project_id == project_id)
            .order_by(Constitution.version).limit(1)
        ).scalar_one_or_none()
        if row is None:
            return {}
        return ConstitutionDoc.model_validate(row.body).model_dump()


def _pace(project_id: str, cons_doc: dict, days: int) -> dict:
    """The four limits, over the chairman's real numbers.

    `capacity.py` exists because four separate rules decide whether a run
    starts and only one of them is a daily total — a chain of work owned by one
    agent is SERIAL at one wake per agent per hour, whatever the other ceilings
    say. That is the single most surprising thing about pacing here, and it is
    invisible on a static screen.
    """
    from yaaf.core.constitution import ConstitutionDoc
    from yaaf.core.db import db_session
    from yaaf.core.models import Project, utcnow
    from yaaf.server.capacity import capacity_forecast

    if not cons_doc:
        return {}
    cons = ConstitutionDoc.model_validate(cons_doc)
    with db_session() as session:
        project = session.get(Project, project_id)
        if project is None:
            return {}
        return capacity_forecast(session, project, cons, utcnow() + timedelta(days=days))


def _money(project_id: str, cons_doc: dict, plan: dict,
           usd_per_run: float | None) -> dict:
    """What it costs, in the only unit the drill can honestly produce.

    A drill spends nothing, so it prices nothing, and `avg_usd_per_run` will
    not answer under five priced runs. Rather than invent a rate, this reports
    the plan in runs and inverts the question: what a run has to cost for the
    plan to fit. A chairman knows roughly what he pays; he can check one number
    against it in a second, which is more than a fabricated total would give
    him.
    """
    from yaaf.core.constitution import ConstitutionDoc
    from yaaf.core.db import db_session
    from yaaf.core.models import Project
    from yaaf.server import policy

    if not cons_doc:
        return {}
    cons = ConstitutionDoc.model_validate(cons_doc)
    runs = plan.get("adjusted_runs") or plan.get("estimated_runs") or 0
    out: dict = {
        "runs": runs,
        "billing_mode": None,
        "cap": None,
        "unit": None,
        "usd_per_run": usd_per_run,
        "source": "the number you gave me" if usd_per_run else None,
        "total": None,
        "break_even_usd_per_run": None,
        "fits": None,
    }
    with db_session() as session:
        project = session.get(Project, project_id)
        if project is None:
            return out
        out["billing_mode"] = project.billing_mode
        if project.billing_mode != "api_key":
            out["cap"] = cons.monthly_token_cap
            out["unit"] = "tokens"
            out["why"] = ("subscription billing prices no individual run, so "
                          "there is no per-run cost to multiply")
            return out
        out["cap"] = cons.monthly_usd_cap
        out["unit"] = "usd"
        if usd_per_run is None:
            measured = policy.avg_usd_per_run(session, project.id)
            if measured:
                out["usd_per_run"] = round(measured, 4)
                out["source"] = "measured on this box"
    if out["usd_per_run"]:
        out["total"] = round(out["usd_per_run"] * runs, 2)
        out["fits"] = out["total"] <= (out["cap"] or 0)
    if runs and out["cap"]:
        out["break_even_usd_per_run"] = round(out["cap"] / runs, 2)
    return out


def _plan_arithmetic(project_id: str, cons_doc: dict) -> dict:
    """The plan as PROPOSED, not the plan as it stands at the end.

    `plan_arithmetic` counts live scope, which is exactly right for a running
    project and exactly wrong for a drill: a project that finished everything
    reports zero requirements and zero runs, which reads as "it planned
    nothing" when what happened is the opposite. So the totals are recomputed
    over all scope, settled included, at the FIRST estimate — the same number
    the platform keeps forever and scores the CEO against.
    """
    from yaaf.core.constitution import ConstitutionDoc
    from yaaf.core.db import db_session
    from yaaf.core.models import Project
    from yaaf.server import policy

    if not cons_doc:
        return {}
    with db_session() as session:
        project = session.get(Project, project_id)
        if project is None:
            return {}
        cons = ConstitutionDoc.model_validate(cons_doc)
        sums = policy.plan_arithmetic(session, project, cons)
        scope = policy.scope_requirements(session, project.id, all_cycles=True)
        planned = [r for r in scope if r.status != "cut"]
        estimated = sum(r.est_runs_first or r.est_runs or 0 for r in planned)
        sums["requirements"] = len(planned)
        sums["estimated_runs"] = estimated
        sums["adjusted_runs"] = int(round(estimated * sums.get("multiplier", 1.0)))
        sums["basis"] = "the plan as proposed, settled scope included"
        return sums


def _structural_findings(client, project_id: str, capacity: dict) -> list[str]:
    """Facts about the shape of the project that no wall reports.

    A refusal tells you what a project TRIED and was stopped from doing. Some
    configurations fail earlier than that: a CEO with nobody to give work to
    never files a task at all, so the walls list is empty and the drill would
    otherwise say "it stalled" without saying why. These are the cases where
    nothing happened *because there was nobody to make it happen*.
    """
    findings: list[str] = []
    try:
        org = client.get(f"/observe/projects/{project_id}/org").json()
    except Exception:  # pragma: no cover - a report is not worth a crash
        return findings
    teams = org.get("teams") or []
    if not teams:
        findings.append(
            "This project has no teams. The CEO is the only agent, and it is the "
            "one agent that may not do the work — it plans. Hire somebody, or "
            "run it solo so the CEO is also the hands.")
    else:
        empty = [t["name"] for t in teams if not t.get("agents")]
        if empty and len(empty) == len(teams):
            findings.append(
                f"No team has anybody in it ({', '.join(empty)}), so there is "
                "nobody the CEO can give work to.")
        elif empty:
            findings.append(
                f"{', '.join(empty)} has no employees — work assigned there can "
                "never be claimed.")
        leaderless = [t["name"] for t in teams
                      if t.get("agents") and not t.get("lead_agent_id")]
        if leaderless:
            findings.append(
                f"{', '.join(leaderless)} has nobody named as lead. The janitor "
                "elects the earliest member, but naming one yourself is cheaper "
                "than finding out which it picked.")
    if capacity.get("unowned_tasks"):
        findings.append(
            f"{capacity['unowned_tasks']} task(s) reached a state nobody on the "
            "roster can claim.")
    return findings


def _group_refusals(refusals: list[dict]) -> list[dict]:
    """Grouped, because a crew that hits the same wall nine times has one
    problem and not nine. The count is the interesting part: a wall hit once is
    a project learning its rules, a wall hit nine times is a project that
    cannot get past it."""
    grouped: dict[tuple[str, str], dict] = {}
    for refusal in refusals:
        error = str(refusal.get("error", ""))[:220]
        key = (refusal.get("path", ""), error[:80])
        row = grouped.setdefault(key, {
            "path": refusal.get("path", ""), "error": error, "count": 0,
            "agents": set(),
        })
        row["count"] += 1
        row["agents"].add(refusal.get("agent", "?"))
    out = [{**row, "agents": sorted(row["agents"])} for row in grouped.values()]
    return sorted(out, key=lambda r: -r["count"])


def _group_interventions(interventions: list[dict]) -> list[dict]:
    grouped: dict[str, dict] = {}
    for item in interventions:
        row = grouped.setdefault(item["kind"], {
            "kind": item["kind"], "count": 0, "first_tick": item["tick"],
            "examples": [],
        })
        row["count"] += 1
        if len(row["examples"]) < 3:
            row["examples"].append(item["detail"])
    return sorted(grouped.values(), key=lambda r: -r["count"])


def _verdict(steps: list[dict], refusals: list[dict]) -> tuple[bool, str]:
    reached = {s["key"] for s in steps if s["ok"]}
    if "started" not in reached:
        return False, "This project would not start."
    if "work_closed" in reached:
        return True, ("This project gets off the ground: it planned, opened its "
                      "board, did work and closed it with evidence.")
    if "work_created" in reached:
        return False, ("It plans and creates work, but nothing closed. The board "
                       "opened and then stalled.")
    if "plan_gate" in reached:
        return False, ("The plan holds and the board opened, but no work was ever "
                       "created."
                       + (" Something refused the CEO's tasks — see the walls below."
                          if refusals else
                          " Nothing was even attempted, which means there was "
                          "nobody to attempt it."))
    if "plan_proposed" in reached:
        return False, ("Scope was proposed and the board never opened — the plan "
                       "gate did not close.")
    if refusals:
        return False, ("The CEO could not even propose a plan; the first thing it "
                       "tried was refused.")
    return False, "It started and then did nothing at all."


# ── assembly ────────────────────────────────────────────────────────────────────


def _slack_off_in_spec(spec: dict) -> bool:
    """Whether this spec turns Slack off for the project it describes.

    A project inherits the install's workspace unless it says otherwise, so
    "already wired" is the wrong thing to tell a chairman about a spec carrying
    `integrations.slack.enabled: false` — that project mirrors nothing, and the
    drill stripping Slack before it ran is not why.
    """
    slack = ((spec.get("integrations") or {}).get("slack") or {})
    return isinstance(slack, dict) and slack.get("enabled") is False


def build(client, project_id: str | None, spec: dict, steps: list[dict],
          interventions: list[dict], days: int, usd_per_run: float | None,
          roles: dict, ticks: int, refusals: list[dict] | None = None,
          install: dict | None = None) -> dict:
    from yaaf.rehearsal import RELAXED
    from yaaf.rehearsal import install as install_facts

    refusals = refusals or []
    ok, verdict = _verdict(steps, refusals)
    explore = spec.get("mode", "explore") == "explore"
    walk = []
    by_key = {s["key"]: s for s in steps}
    for key, label in WALK:
        step = by_key.get(key)
        walk.append({
            "key": key, "label": label,
            "ok": bool(step and step["ok"]),
            "na": explore and key in NOT_IN_EXPLORE,
            "detail": step["detail"] if step else None,
            "tick": step["tick"] if step else None,
        })
    report: dict = {
        "ok": ok,
        "verdict": verdict,
        "spec": {
            "slug": spec.get("slug"), "name": spec.get("name"),
            "mode": spec.get("mode", "explore"),
            "goal": (spec.get("goal") or "")[:400],
            "teams": len(spec.get("teams") or []),
        },
        "ticks": ticks,
        "roles": roles,
        "walk": walk,
        "refusals": _group_refusals(refusals),
        "interventions": _group_interventions(interventions),
        "relaxed": ([{"rule": name, "why": why} for name, (_, why) in RELAXED.items()]
                    + [{"rule": "notice and message batching",
                        "why": "the dispatcher holds a wake for minutes until it is worth "
                               "a run; a drill has no minutes"}]),
        # Two of these depend on what the chairman has ALREADY wired, which
        # the drill cannot see from inside its throwaway database — see
        # `yaaf.rehearsal.install`. Stated unconditionally they told a chairman
        # with Slack connected app-wide that he had a dependency to go and
        # attach, which is the one kind of finding a preflight must not invent.
        "not_exercised": [
            "command success tests — the drill writes observables only, so no "
            "command out of a spec file is ever executed",
            "evidence verification — evidence carries no URL, so the verifier "
            "never reaches the network",
            "credentials and data sources — a drill reads no secret and opens "
            "no socket",
            install_facts.slack_line(
                install, disabled_by_spec=_slack_off_in_spec(spec)),
            install_facts.notification_line(install),
            "the workspace — the drill works in a directory it deletes, and a "
            "github_repo in the spec is not cloned into it",
        ],
        # The same facts as data, so a screen or the Founder can act on them
        # without parsing the prose above. None when the caller had no live
        # install to read — `yaaf rehearse` on a checkout.
        "install": install,
        "caveat": (
            "The crew that ran this is competent and dull by construction. A "
            "clean drill is evidence about your CONFIGURATION and no evidence "
            "at all about whether a real model does the job well."),
    }
    if project_id is None:
        report["plan"] = {}
        report["capacity"] = {}
        report["money"] = {}
        report["agenda"] = {}
        report["health"] = []
        report["findings"] = []
        return report

    cons_doc = _real_constitution(project_id)
    plan = _plan_arithmetic(project_id, cons_doc)
    report["plan"] = plan
    report["capacity"] = _pace(project_id, cons_doc, days)
    report["findings"] = _structural_findings(client, project_id, report["capacity"])
    report["money"] = _money(project_id, cons_doc, plan, usd_per_run)
    report["days"] = days
    try:
        report["agenda"] = client.get(
            f"/observe/projects/{project_id}/agenda?days={min(days, 30)}").json()
    except Exception:  # pragma: no cover - a report is not worth a crash
        report["agenda"] = {}
    try:
        report["health"] = client.get(
            f"/observe/projects/{project_id}/health").json().get("checks", [])
    except Exception:  # pragma: no cover
        report["health"] = []
    return report


# ── rendering ───────────────────────────────────────────────────────────────────


def render_text(report: dict) -> str:
    out: list[str] = []
    spec = report["spec"]
    out.append(f"Fire drill — {spec['name'] or spec['slug'] or 'your project'} "
               f"({spec['mode']})")
    out.append("=" * 72)
    out.append("")
    out.append(report["verdict"])
    out.append("")

    out.append("The walk")
    for step in report["walk"]:
        if step.get("na"):
            out.append(f"  – {step['label']} — not in an Explore project's shape")
            continue
        mark = "✓" if step["ok"] else "·"
        beat = f"  beat {step['tick']}" if step["tick"] else ""
        detail = f" — {step['detail']}" if step["detail"] else ""
        out.append(f"  {mark} {step['label']}{detail}{beat}")
    out.append("")

    if report["interventions"]:
        total = sum(row["count"] for row in report["interventions"])
        out.append(f"It stopped and waited for you {total} time(s)")
        for row in report["interventions"]:
            out.append(f"  · {row['count']}× {row['kind']}")
            for example in row["examples"]:
                out.append(f"      {example}")
        out.append("  Your autonomy dial decides which of these park. Every one is a")
        out.append("  morning the project is idle until you open the app.")
        out.append("")
    else:
        out.append("It never had to wait for you.")
        out.append("")

    if report.get("findings"):
        out.append("What the shape of this project means")
        for finding in report["findings"]:
            out.append(f"  ! {finding}")
        out.append("")

    if report["refusals"]:
        out.append("Walls it hit")
        for row in report["refusals"]:
            out.append(f"  {row['count']}× {row['path']}")
            out.append(f"      {row['error']}")
        out.append("")

    plan = report.get("plan") or {}
    money = report.get("money") or {}
    if plan and plan.get("requirements"):
        out.append("What it will cost")
        out.append(f"  the plan is {plan['requirements']} requirement(s), "
                   f"{plan['estimated_runs']} estimated run(s)"
                   + (f" (×{plan['multiplier']} for measured over-run "
                      f"= {plan['adjusted_runs']})" if plan["multiplier"] > 1 else ""))
        if money.get("unit") == "usd":
            if money.get("usd_per_run"):
                out.append(f"  at {money['source']}, ${money['usd_per_run']}/run: "
                           f"${money['total']:,.2f} of a ${money['cap']:,.0f} cap "
                           + ("— it fits" if money["fits"] else "— IT DOES NOT FIT"))
            else:
                out.append("  this box has never priced a run, so it cannot tell you "
                           "the total")
                if money.get("break_even_usd_per_run"):
                    out.append(f"  it fits your ${money['cap']:,.0f} cap if a run costs "
                               f"under ${money['break_even_usd_per_run']} — check that "
                               "against a bill you have seen")
        elif money.get("why"):
            out.append(f"  {money['why']}")
        out.append("")
    elif money.get("cap"):
        out.append("What it will cost")
        out.append("  an Explore project has no requirements to estimate, so there "
                   "is no plan total to price — its shape is bets and evidence, "
                   f"inside a {money['unit']} cap of {money['cap']:,.0f}")
        out.append("")

    capacity = report.get("capacity") or {}
    if capacity:
        out.append(f"What paces it, over {report.get('days', 30)} days")
        for line in _capacity_lines(capacity):
            out.append(f"  {line}")
        out.append("")

    health = report.get("health") or []
    bad = [c for c in health if c.get("status") != "ok"]
    if bad:
        out.append("What it still needs before it can really run")
        for check in bad:
            out.append(f"  ! {check.get('detail') or check.get('key')}")
        out.append("")

    out.append("Not exercised by this drill")
    for line in report["not_exercised"]:
        out.append(f"  · {line}")
    out.append("")
    out.append("Rules the drill lifted (yours are otherwise in force)")
    for row in report["relaxed"]:
        out.append(f"  · {row['rule']} — {row['why']}")
    out.append("")
    out.append(report["caveat"])
    return "\n".join(out)


def _capacity_lines(capacity: dict) -> list[str]:
    """`capacity_forecast` already writes the sentence; the drill quotes it
    rather than restating its arithmetic in a second voice that could drift."""
    if "why" not in capacity:
        return ["the platform returned no forecast for this window"]
    lines = [
        ("it fits" if capacity["feasible"] else "IT DOES NOT FIT") + f" — {capacity['why']}",
        f"{capacity['available']} run(s) can start, {capacity['needed']} needed, "
        f"{capacity['retry_reserve']} held back for the retries your measured "
        f"failure rate predicts, {capacity['slack']} spare",
    ]
    if capacity.get("chain_hours"):
        lines.append(
            f"the longest serial chain is {capacity['chain_hours']}h: one agent's "
            "work cannot overlap itself, whatever the project-wide ceilings allow")
    if capacity.get("unowned_tasks"):
        lines.append(f"{capacity['unowned_tasks']} task(s) nobody on the roster can "
                     "claim — that is a hiring problem, not a pacing one")
    return lines
