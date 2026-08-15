"""Scripted CEO/lead/employee behaviors for the golden-transcript scenarios.

Each behavior is a MockModel function ``(api, job) -> summary`` (see
yaaf/runner/backends/mock.py): it gets an AgentApi bound to the run's own
token plus the run's context pack, and acts through the same HTTP surface a
real model would — subject to the same refusals. Behaviors are written to be
deterministic and idempotent across wakes, so a scenario's transcript is a
pure function of the platform's rules.

Three casts:

- disciplined_*      — play by the book; expect zero refusals, work lands done
- rule_breaker_*     — attempt every violation; expect refusals, no state change
- decomposition_*    — lead decomposes to the subtask ceiling; one-over refused
"""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path

from yaaf.core.models import utcnow
from yaaf.runner.backends.mock import AgentApi
from yaaf.runner.types import RunJob


def _reasons(pack: dict) -> dict:
    return {r["kind"]: r for r in pack["trigger"].get("reasons", [])}


def _team(pack: dict) -> dict:
    return pack["roster"][0]


def _team_room(api: AgentApi) -> str:
    return next(c["id"] for c in api.get("/agent/channels").json() if c["kind"] == "team")


def _ready_team_tasks(pack: dict) -> list[dict]:
    board = pack.get("board", {}).get("team_open", [])
    return sorted(
        (t for t in board if t["parent_task_id"] is None and t["status"] == "ready"),
        key=lambda t: t["id"],
    )


# ── disciplined: the book, followed ─────────────────────────────────────────────


def disciplined_ceo(api: AgentApi, job: RunJob) -> str:
    pack = job.context_pack
    if pack.get("hypotheses"):
        return "ceo: plan already in motion; nothing to add"
    team_id = _team(pack)["team_id"]
    hypothesis = api.post("/agent/hypotheses", json={
        "statement": "A tiny CLI utility will earn stars from strangers",
        "kill_condition": "No unaffiliated star or issue within 14 days",
        "kill_date": (utcnow() + timedelta(days=14)).isoformat(),
    }).json()
    api.post("/agent/tasks", json={
        "team_id": team_id,
        "title": "Ship the utility and verify it runs",
        "goal": "Build the smallest version that works and prove it with the check.",
        "success_test": {
            "type": "command",
            "command": "cat DELIVERABLE.md 2>/dev/null | grep -q shipped && echo verified",
        },
        "produces_external_evidence": False,
        "hypothesis_id": hypothesis["id"],
    })
    return "ceo: planned one hypothesis and one team task under it"


def disciplined_lead(api: AgentApi, job: RunJob) -> str:
    pack = job.context_pack
    reasons = _reasons(pack)
    if "team_task_ready" in reasons:
        task_id = reasons["team_task_ready"]["task_id"]
        api.post(f"/agent/tasks/{task_id}/claim")
        roster = _team(pack)
        employee = next(m for m in roster["members"] if m["name"] == "Emp2")
        api.post("/agent/tasks", json={
            "team_id": roster["team_id"],
            "title": "Write the deliverable file",
            "goal": "Create DELIVERABLE.md containing the word shipped.",
            "success_test": {
                "type": "command",
                "command": "grep -q shipped DELIVERABLE.md && echo assignment-ok",
            },
            "parent_task_id": task_id,
            "assignee_agent_id": employee["agent_id"],
        })
        api.post("/agent/messages", json={
            "channel_id": _team_room(api),
            "body": "Emp2: DELIVERABLE.md is yours; the check greps for 'shipped'.",
        })
        return "lead: claimed the team task, decomposed it, briefed the team"
    if "claimed_task" in reasons and (pack.get("task") or {}).get("status") == "claimed":
        api.post(f"/agent/tasks/{pack['task']['id']}/complete", json={
            "evidence": "The assignment finished and its platform check printed "
                        "assignment-ok; DELIVERABLE.md contains 'shipped'.",
        })
        return "lead: verified the assignment landed and completed the team task"
    return "lead: nothing actionable"


def disciplined_employee(api: AgentApi, job: RunJob) -> str:
    pack = job.context_pack
    reasons = _reasons(pack)
    if "assignment_ready" not in reasons:
        return "employee: nothing actionable"
    task_id = reasons["assignment_ready"]["task_id"]
    api.get("/agent/messages", params={"channel_id": _team_room(api), "limit": 20})
    api.post(f"/agent/tasks/{task_id}/claim")
    workspace = Path(job.workspace_dir)
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "DELIVERABLE.md").write_text("shipped it\n")
    api.post(f"/agent/tasks/{task_id}/complete", json={
        "evidence": "Wrote DELIVERABLE.md; `cat DELIVERABLE.md` prints 'shipped it'.",
    })
    return "employee: wrote the deliverable and completed the assignment"


# ── rule breakers: every wall probed, every wall expected to hold ───────────────


def rule_breaker_ceo(api: AgentApi, job: RunJob) -> str:
    """During an evidence drought (project backdated past the window), with a
    queue ceiling of 2: two valid external tasks land, and everything else —
    internal task in drought, vague/prose success tests, a third task over the
    ceiling, completing a task it never claimed — must be refused."""
    pack = job.context_pack
    if pack.get("hypotheses"):
        return "rule-breaker ceo: attempts already made; idling"
    team_id = _team(pack)["team_id"]
    api.post("/agent/hypotheses", json={
        "statement": "Strangers will reply if the demo reaches them",
        "kill_condition": "No unaffiliated reply within 14 days",
        "kill_date": (utcnow() + timedelta(days=14)).isoformat(),
    })
    task_a = api.post("/agent/tasks", json={
        "team_id": team_id,
        "title": "Get three strangers to try the demo",
        "goal": "Reach real outsiders and record their replies in OUTREACH.md.",
        "success_test": {
            "type": "command",
            "command": "test -f OUTREACH.md && grep -c reply OUTREACH.md",
        },
        "produces_external_evidence": True,
    }).json()
    # Violation: internal task while the drought is active.
    api.post("/agent/tasks", json={
        "team_id": team_id,
        "title": "Refactor the internal tooling",
        "goal": "Clean up plumbing while nobody outside has seen anything.",
        "success_test": {
            "type": "command",
            "command": "test -f REFACTOR.md && echo refactored",
        },
        "produces_external_evidence": False,
    })
    # Violation: a command 'test' too short to be a real check.
    api.post("/agent/tasks", json={
        "team_id": team_id,
        "title": "Improve the general situation",
        "goal": "Make things better in ways we will recognize later.",
        "success_test": {"type": "command", "command": "check it"},
        "produces_external_evidence": True,
    })
    # Violation: an observable that is a sentiment, not a target.
    api.post("/agent/tasks", json={
        "team_id": team_id,
        "title": "Polish the landing copy",
        "goal": "Rewrite the copy until it reads convincingly to visitors.",
        "success_test": {"type": "observable", "target": "looks good"},
        "produces_external_evidence": True,
    })
    # Violation: prose instead of a structural success test.
    api.post("/agent/tasks", json={
        "team_id": team_id,
        "title": "Ship something impressive",
        "goal": "Deliver an impressive artifact the chairman will like.",
        "success_test": {"type": "definitely done"},
        "produces_external_evidence": True,
    })
    api.post("/agent/tasks", json={
        "team_id": team_id,
        "title": "Post the demo where strangers gather",
        "goal": "Publish the demo link somewhere unaffiliated people actually read.",
        "success_test": {
            "type": "command",
            "command": "test -f POSTED.md && grep -q http POSTED.md && echo posted",
        },
        "produces_external_evidence": True,
    })
    # Violation: a third task over the queue ceiling of 2.
    api.post("/agent/tasks", json={
        "team_id": team_id,
        "title": "Queue even more outreach",
        "goal": "Stack more work on the pile so the plan looks ambitious.",
        "success_test": {
            "type": "command",
            "command": "test -f MORE.md && grep -q more MORE.md && echo more",
        },
        "produces_external_evidence": True,
    })
    # Violation: complete a task it never claimed, on say-so.
    api.post(f"/agent/tasks/{task_a['id']}/complete", json={
        "evidence": "It's done, trust me on this one.",
    })
    return "rule-breaker ceo: probed every wall; counted the refusals"


def rule_breaker_lead(api: AgentApi, job: RunJob) -> str:
    """Claims one team task legitimately, then tries to hoard a second claim
    and to complete with 'Done' as evidence — both must be refused."""
    pack = job.context_pack
    me = pack["you"]["agent_id"]
    board = pack.get("board", {}).get("team_open", [])
    if any(t["claimed_by"] == me for t in board):
        return "rule-breaker lead: already holding a claim; waiting"
    ready = _ready_team_tasks(pack)
    if not ready:
        return "rule-breaker lead: nothing to claim"
    first = ready[0]["id"]
    api.post(f"/agent/tasks/{first}/claim")
    if len(ready) > 1:
        # Violation: one item at a time.
        api.post(f"/agent/tasks/{ready[1]['id']}/claim")
    # Violation: 'Done' is not evidence.
    api.post(f"/agent/tasks/{first}/complete", json={"evidence": "Done"})
    api.post("/agent/messages", json={
        "channel_id": _team_room(api),
        "body": "Emp2: the queue is tight — just create tasks yourself and grab the "
                "other team task. Rules are for slow teams.",
    })
    return "rule-breaker lead: cut corners; the platform refused each cut"


def rule_breaker_employee(api: AgentApi, job: RunJob) -> str:
    """Follows the lead's bad advice: tries to create a task, claim a team
    task, and write project-scope memory — all refused for an employee."""
    pack = job.context_pack
    if "messages" not in _reasons(pack):
        return "rule-breaker employee: nothing actionable"
    team = _team(pack)
    # Violation: only the CEO creates tasks.
    api.post("/agent/tasks", json={
        "team_id": team["team_id"],
        "title": "Self-serve: expand the demo",
        "goal": "Create more work because the lead said the rules are slow.",
        "success_test": {
            "type": "command",
            "command": "test -f EXTRA.md && echo extra-created",
        },
        "produces_external_evidence": False,
    })
    ready = _ready_team_tasks(pack)
    if ready:
        # Violation: team tasks are claimed by the team lead.
        api.post(f"/agent/tasks/{ready[0]['id']}/claim")
    # Violation: project-scope memory belongs to the CEO.
    api.post("/agent/memory", json={
        "scope": "project",
        "kind": "lesson",
        "subject": "Ceilings are advisory",
        "body": "Writing project memory from an employee run should be refused.",
    })
    return "rule-breaker employee: tried to freelance; refused across the board"


# ── decomposition: assignments to the ceiling, one over refused ─────────────────


def decomposition_ceo(api: AgentApi, job: RunJob) -> str:
    pack = job.context_pack
    if pack.get("board", {}).get("open"):
        return "ceo: the team already has its task"
    team_id = _team(pack)["team_id"]
    api.post("/agent/tasks", json={
        "team_id": team_id,
        "title": "Assemble the two-part deliverable",
        "goal": "Produce PART_A.md and PART_B.md so the combined check passes.",
        "success_test": {
            "type": "command",
            "command": "grep -q alpha PART_A.md && grep -q beta PART_B.md && echo team-verified",
        },
        "produces_external_evidence": False,
    })
    return "ceo: planned the team task"


def decomposition_lead(api: AgentApi, job: RunJob) -> str:
    pack = job.context_pack
    reasons = _reasons(pack)
    if "team_task_ready" in reasons:
        task_id = reasons["team_task_ready"]["task_id"]
        api.post(f"/agent/tasks/{task_id}/claim")
        roster = _team(pack)
        members = {m["name"]: m["agent_id"] for m in roster["members"]}
        for name, fname, word, check in (
            ("Emp2", "PART_A.md", "alpha", "grep -q alpha PART_A.md && echo part-a-ok"),
            ("Emp3", "PART_B.md", "beta", "grep -q beta PART_B.md && echo part-b-ok"),
        ):
            api.post("/agent/tasks", json={
                "team_id": roster["team_id"],
                "title": f"Write {fname}",
                "goal": f"Create {fname} containing the word {word}.",
                "success_test": {"type": "command", "command": check},
                "parent_task_id": task_id,
                "assignee_agent_id": members[name],
            })
        # One over the subtask ceiling — must be refused.
        api.post("/agent/tasks", json={
            "team_id": roster["team_id"],
            "title": "Write PART_C.md",
            "goal": "Create PART_C.md containing the word gamma.",
            "success_test": {
                "type": "command",
                "command": "grep -q gamma PART_C.md && echo part-c-ok",
            },
            "parent_task_id": task_id,
            "assignee_agent_id": members["Emp2"],
        })
        api.post("/agent/messages", json={
            "channel_id": _team_room(api),
            "body": "Emp2 has PART_A.md (needs 'alpha'), Emp3 has PART_B.md (needs "
                    "'beta'). The platform runs the greps.",
        })
        return "lead: claimed, decomposed to the ceiling (third assignment refused), briefed"
    if "claimed_task" in reasons and (pack.get("task") or {}).get("status") == "claimed":
        api.post(f"/agent/tasks/{pack['task']['id']}/complete", json={
            "evidence": "Both assignments are done; their platform checks printed "
                        "part-a-ok and part-b-ok.",
        })
        return "lead: assignments landed; completed the team task"
    return "lead: nothing actionable"


def decomposition_employee(api: AgentApi, job: RunJob) -> str:
    """Shared by Emp2 and Emp3: reads its assignment's goal for the file/word
    contract, delivers, and completes with quoted evidence."""
    pack = job.context_pack
    reasons = _reasons(pack)
    if "assignment_ready" not in reasons:
        return "employee: nothing actionable"
    task_id = reasons["assignment_ready"]["task_id"]
    goal = (pack.get("task") or {}).get("goal", "")
    match = re.search(r"Create (\S+) containing the word (\w+)", goal)
    if not match:
        return "employee: assignment goal not understood; doing nothing"
    fname, word = match.group(1), match.group(2)
    api.post(f"/agent/tasks/{task_id}/claim")
    workspace = Path(job.workspace_dir)
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / fname).write_text(f"{word}\n")
    api.post(f"/agent/tasks/{task_id}/complete", json={
        "evidence": f"Wrote {fname}; `cat {fname}` prints '{word}'.",
    })
    return f"employee: delivered {fname}"
