"""The Founder: the agent that designs a project before one exists.

Every other part of yaaf has a project behind it. A CEO proposes a plan, teams
claim work, an adversary attacks the project's own output, the platform
refuses a success test it cannot check. The founding moment had a form.

This package is the other half of that: a conversation whose deliverable is a
`project_setup` bundle — the same object the importer accepts and the review
screen posts.

A turn is two halves on purpose. `start_turn` records what the chairman said,
on the request, and returns; `run_turn` is the five minutes of model work, on a
background thread with a session of its own (`yaaf.founder.runner`).
"""

from yaaf.founder.agent import briefing, run_turn, start_turn

__all__ = ["briefing", "run_turn", "start_turn"]
