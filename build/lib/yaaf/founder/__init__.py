"""The Founder: the agent that designs a company before one exists.

Every other part of yaaf has a company behind it. A CEO proposes a plan, teams
claim work, an adversary attacks the company's own output, the platform
refuses a success test it cannot check. The founding moment had a form.

This package is the other half of that: a conversation whose deliverable is a
`project_setup` bundle — the same object the importer accepts and the review
screen posts.
"""

from yaaf.founder.agent import briefing, say

__all__ = ["briefing", "say"]
