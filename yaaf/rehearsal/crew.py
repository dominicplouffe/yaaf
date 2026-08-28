"""A competent, generic crew — the drill's stand-in for real agents.

`evals/behaviors.py` has three scripted crews, and they are scripted on
purpose: a golden transcript has to be byte-stable, so those behaviours know
the team is called Alpha and the employee is called Emp2. None of that
survives contact with a chairman's own spec.

This crew knows nothing about the project it is hired into. It reads the
context pack the way a role's system prompt tells a real agent to read it —
what am I, what is my board, what did the platform just refuse — and takes the
next legal action. It is deliberately **competent and dull**: it proposes a
plausible plan, it works the board in order, it files real evidence, and it
never argues with a refusal.

That is the honest shape for a fire drill. A crew that behaved badly would
tell you about the guardrails, which the invariant suite already covers at
length. What a chairman needs to know before he spends anything is whether a
*well-behaved* project can get off the ground inside the rules **he** wrote —
so every wall this crew hits is a wall his configuration built, not one the
scenario arranged.

What it cannot tell you is whether a real model would do the job well. Nothing
here is a quality prediction, and the report says so in as many words.
"""

from __future__ import annotations

from datetime import timedelta

from yaaf.core.models import utcnow
from yaaf.runner.backends.mock import AgentApi
from yaaf.runner.types import RunJob

#: What the drill's CEO estimates a requirement will take. It is a plausible
#: number rather than a good one: the whole point of `est_runs` is that the
#: platform scores it against reality, and a drill has no reality to score
#: against. The report prints the plan's total in runs so the chairman can
#: substitute his own judgement for this constant.
EST_RUNS_PER_REQUIREMENT = 4

#: How much scope the drill's CEO proposes. Three is enough to exercise stages,
#: the queue ceiling and the plan arithmetic without turning a drill into a
#: long-running simulation.
REQUIREMENTS_TO_PROPOSE = 3

#: Evidence the platform will never fetch. Every success test and acceptance
#: test this crew writes is an `observable`, which means the drill runs no
#: commands and opens no sockets — a rehearsal that executed a command out of a
#: spec file would be a remote-code-execution feature with a friendly name.
_OBSERVABLE = "a stranger can load the published result and see the date on it"


def _reasons(pack: dict) -> dict:
    return {reason["kind"]: reason for reason in pack.get("trigger", {}).get("reasons", [])}


def _is_lead(pack: dict) -> bool:
    me = pack["you"]
    for team in pack.get("roster", []):
        if team["team_id"] == me.get("team_id"):
            return team.get("lead") == me["name"]
    return False


def _my_team(pack: dict) -> dict | None:
    me = pack["you"]
    for team in pack.get("roster", []):
        if team["team_id"] == me.get("team_id"):
            return team
    return None


def _first_working_team(pack: dict) -> dict | None:
    """A team with somebody in it. The CEO's own team in solo mode counts;
    an empty one never does, and hiring a team with no employees is a real
    configuration a chairman can save."""
    for team in pack.get("roster", []):
        if team.get("members"):
            return team
    return None


def _holding(pack: dict) -> dict | None:
    """The task this agent actually holds, or None.

    `pack["task"]` is *the task this run is about* — a `team_task_ready` wake
    carries a task id and the pack expands it — which is emphatically not the
    same as a task this agent has claimed. Reading the two as one is how a
    well-behaved crew ends up posting `complete` against a task it never picked
    up, thirteen times, burning a refusal cap that exists precisely to stop
    that. The status is right there; use it.
    """
    task = pack.get("task")
    if task and task.get("status") in ("claimed", "checking"):
        return task
    return None


# ── the CEO ─────────────────────────────────────────────────────────────────────


def ceo(api: AgentApi, job: RunJob, scope_project: bool | None = None) -> str:
    pack = job.context_pack
    did: list[str] = []

    # `plan` is R-00 — the gate — and it is present exactly while the project
    # owes a plan. `requirements` is the scope under it, and the pack omits the
    # key entirely when there is none. Either one means this project has scope;
    # a fresh Deliver project has the first and not the second, which is
    # precisely the moment the CEO is supposed to act.
    requirements = pack.get("requirements") or []
    gate = pack.get("plan")
    # `scope_project` is the spec's own mode, handed down by the drill because
    # the drill knows it. Inferring it from the pack works right up until every
    # requirement has settled, at which point a Deliver project looks exactly
    # like an Explore one and the crew files a task with no requirement — which
    # the platform correctly refuses, and which would read on the report as a
    # wall in the chairman's configuration rather than a guess in ours.
    scope_mode = (scope_project if scope_project is not None
                  else (gate is not None or bool(requirements)))

    # 1. Scope. Outside Explore the board stays shut until a plan exists, is
    #    staged, is estimated and fits the money — so this is always the first
    #    move, and if it is refused the drill has found the chairman's wall.
    # Only while the gate is open. A dull crew plans once and then works the
    # plan; a crew that topped its scope up every beat would spend the drill
    # discovering the commitment freeze it caused itself, which tells the
    # chairman about his CEO's manners rather than about his configuration.
    if gate is not None and len(requirements) < REQUIREMENTS_TO_PROPOSE:
        for n in range(len(requirements), REQUIREMENTS_TO_PROPOSE):
            response = api.post("/agent/requirements", json={
                "statement": (
                    f"Stage {n + 1} of the goal is complete and can be shown to "
                    "somebody outside this company"),
                "acceptance_test": {"type": "observable", "target": _OBSERVABLE},
                "stage": n + 1,
                "est_runs": EST_RUNS_PER_REQUIREMENT,
            })
            if response.status_code != 200:
                break
            did.append("proposed scope")

    # 2. A bet. Explore's root object, and harmless elsewhere — a project with
    #    no falsifiable claim on the table is one that cannot be wrong.
    if not pack.get("hypotheses"):
        response = api.post("/agent/hypotheses", json={
            "statement": "The goal as written can be reached with the team hired for it",
            "kill_condition": "no stage has been shown to an outsider by the kill date",
            "kill_date": (utcnow() + timedelta(days=21)).isoformat(),
        })
        if response.status_code == 200:
            did.append("placed a bet")

    # 3. Work, up to the ceiling. One task per accepted requirement that has
    #    none, because a task serving no requirement is refused outside Explore
    #    and a requirement with no task never settles.
    board = pack.get("board", {})
    open_tasks = board.get("open", [])
    ceiling = board.get("ceiling", 0) or 0
    team = _first_working_team(pack)
    # While the gate is open the board is shut by rule, and trying anyway burns
    # a refusal off a cap that exists to stop exactly that.
    if team is not None and gate is None and len(open_tasks) < ceiling:
        served = {t.get("requirement_id") for t in open_tasks}
        targets: list[str | None] = []
        if scope_mode:
            # Stages are the chairman's sequencing of his own scope and the
            # platform refuses work that jumps it, so a crew that asked for all
            # three stages at once would burn its refusal cap learning that.
            live = [r for r in requirements
                    if r["status"] in ("pending", "in_progress")]
            # The open stage is the lowest with anything unsettled in it —
            # across ALL live scope, not just the part nothing is working yet.
            # Computed off the unserved subset it walks one stage ahead of the
            # platform every time a task is created, and is refused for it.
            if live:
                open_stage = min(r.get("stage") or 1 for r in live)
                targets = [
                    r["id"] for r in live
                    if (r.get("stage") or 1) == open_stage
                    and not r["blocked_by"]
                    and r["id"] not in served
                ]
        elif not open_tasks:
            targets = [None]  # Explore: work hangs off the bet, not a requirement
        for requirement_id in targets:
            if len(open_tasks) + len(did) >= ceiling:
                break
            body = {
                "team_id": team["team_id"],
                "title": "Do the next piece and show it to somebody outside",
                "goal": ("Produce the next stage of the goal and leave proof a "
                         "stranger could check."),
                "success_test": {"type": "observable", "target": _OBSERVABLE},
                "produces_external_evidence": True,
            }
            if requirement_id is not None:
                body["requirement_id"] = requirement_id
            if api.post("/agent/tasks", json=body).status_code == 200:
                did.append("created work")

    # 4. The date. Proposing is free and the platform stamps it from its own
    #    projection, so a CEO that never proposes leaves the chairman with no
    #    promise to sign — which is itself worth finding in a drill.
    # Only while there is live scope to promise ON. `open_commitment` goes None
    # once the promise is MET, so a CEO that keyed only on its absence proposes
    # a fresh date every beat after delivery — and the drill's chairman signs
    # every one of them, turning "it asked you to sign once" into twenty-seven.
    if (scope_mode and gate is None and requirements
            and not pack.get("commitment")
            and api.post("/agent/commitments",
                         json={"note": "the plan as accepted"}).status_code == 200):
        did.append("proposed a delivery date")

    return "ceo: " + (", ".join(did) if did else "nothing legal to do")


# ── a team lead ─────────────────────────────────────────────────────────────────


def lead(api: AgentApi, job: RunJob) -> str:
    pack = job.context_pack
    reasons = _reasons(pack)

    # Finish what is in hand before picking anything up: one item at a time is
    # a rule, and a lead that collects work is a lead the platform refuses.
    held = _holding(pack)
    if held is not None:
        return _close(api, held["id"])

    if "team_task_ready" in reasons:
        task_id = reasons["team_task_ready"]["task_id"]
        if api.post(f"/agent/tasks/{task_id}/claim").status_code == 200:
            return _close(api, task_id)

    team = _my_team(pack)
    unclaimed = [
        t for t in pack.get("board", {}).get("team_open", [])
        if t["status"] == "ready" and not t.get("assignee")
    ]
    # Hand the work down where there is somebody to hand it to; a lead is not
    # the team's only pair of hands unless it is.
    others = [m for m in (team or {}).get("members", [])
              if m["name"] != pack["you"]["name"]]
    if unclaimed and others:
        task = unclaimed[0]
        if api.post(f"/agent/tasks/{task['id']}/claim").status_code == 200:
            response = api.post("/agent/tasks", json={
                "team_id": team["team_id"],
                "title": "Work the assignment and leave the proof",
                "goal": "Do the piece and record what you actually saw.",
                "success_test": {"type": "observable", "target": _OBSERVABLE},
                "parent_task_id": task["id"],
                "assignee_agent_id": others[0]["agent_id"],
            })
            if response.status_code == 200:
                return "lead: claimed and delegated"
            # Decomposition refused — the ceiling, the stage gate, something.
            # Do it myself rather than sitting on a claimed task.
            return _close(api, task["id"])
    if unclaimed:
        task = unclaimed[0]
        if api.post(f"/agent/tasks/{task['id']}/claim").status_code == 200:
            return _close(api, task["id"])
    return "lead: nothing ready"


# ── an employee ─────────────────────────────────────────────────────────────────


def employee(api: AgentApi, job: RunJob) -> str:
    pack = job.context_pack
    reasons = _reasons(pack)

    held = _holding(pack)
    if held is not None:
        return _close(api, held["id"])
    for kind in ("assignment_ready", "claimed_task"):
        if kind in reasons:
            task_id = reasons[kind]["task_id"]
            if api.post(f"/agent/tasks/{task_id}/claim").status_code == 200:
                return _close(api, task_id)
            return "employee: could not claim the task it was woken for"
    mine = [
        t for t in pack.get("board", {}).get("team_open", [])
        if t.get("assignee") == pack["you"]["agent_id"] and t["status"] == "ready"
    ]
    if mine and api.post(f"/agent/tasks/{mine[0]['id']}/claim").status_code == 200:
        return _close(api, mine[0]["id"])
    # An employee's only route to the backlog, and worth exercising: a project
    # whose suggestions are refused has a comms problem its chairman should
    # hear about before he funds it.
    api.post("/agent/suggestions", json={
        "text": "Nothing is assigned to me; there may be a stage worth opening."})
    return "employee: idle, filed a suggestion"


def _close(api: AgentApi, task_id: str) -> str:
    """File the proof, then close on it. In that order, because evidence
    written after the close is evidence written to justify one."""
    # INTERNAL, deliberately. External evidence is refused without a
    # `source_url` — correctly, since the platform verifies it by fetching it —
    # and a drill that reached the network would be neither offline nor
    # repeatable. Internal evidence needs no URL and is never fetched. The
    # report says the external lane is unexercised rather than pretending it
    # passed, which is the honest half of the trade.
    api.post("/agent/evidence", json={
        "kind": "internal",
        "task_id": task_id,
        "description": "Produced the stage and read the result back before closing.",
    })
    response = api.post(f"/agent/tasks/{task_id}/complete", json={
        "evidence": "Loaded the published stage back and read the date on it."})
    if response.status_code == 200:
        return "closed a task with evidence"
    if api.post(f"/agent/tasks/{task_id}/block", json={
            "reason": "Could not finish this within the rules as written."}).status_code == 200:
        return "blocked a task it could not finish"
    return "held a task it could neither close nor block"


def dispatch(api: AgentApi, job: RunJob, scope_project: bool | None = None) -> str:
    """One entry point for every agent, because the drill hires a crew it has
    never met. Role is read off the pack, exactly as an agent reads it."""
    pack = job.context_pack
    if pack["you"]["kind"] == "ceo":
        # Solo mode puts the CEO in the only team, where it is also the hands.
        summary = ceo(api, job, scope_project=scope_project)
        if pack.get("you", {}).get("team_id"):
            summary += " · " + lead(api, job)
        return summary
    return lead(api, job) if _is_lead(pack) else employee(api, job)
