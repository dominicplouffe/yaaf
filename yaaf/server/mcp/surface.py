"""Every chairman route, classified: reached by a tool, or deliberately not.

The denylist problem, stated plainly. This surface is a projection of
`/admin`, and `/admin` keeps growing. A tool list that is merely *missing* a
route is indistinguishable from one that has not caught up yet — so the next
person to add an admin route would have to remember that a second file exists,
and would not.

So the classification is exhaustive rather than negative. Every route is either
named here with the tool that reaches it, or named with the reason it is
withheld, and `test_mcp_secrets.py` fails on the first route that is neither.
The failure message is the design review: someone adding a route is asked, once,
whether a model should be able to call it.
"""

from __future__ import annotations

#: Routes an MCP tool reaches, and which tool reaches it.
REACHED = {
    # read
    "GET /admin/projects/{project_id}/requirements": "plan",
    "GET /admin/projects/{project_id}/amendments": "amend (list)",
    "GET /admin/constitution/schema": "amend (schema)",
    "GET /admin/secrets": "credential (list) — names and scopes only",
    # steer
    "POST /admin/projects/{project_id}/boardroom": "boardroom_say",
    "POST /admin/approvals/{approval_id}/decide": "approval_decide",
    "POST /admin/requirements/{requirement_id}/accept": "requirement_decide",
    "POST /admin/requirements/{requirement_id}/cut": "requirement_decide",
    "POST /admin/requirements/{requirement_id}/verdict": "requirement_decide",
    "POST /admin/requirements/{requirement_id}/reopen": "requirement_decide",
    "POST /admin/requirements/{requirement_id}/restage": "requirement_decide",
    "POST /admin/requirements/{requirement_id}/reestimate": "requirement_decide",
    "POST /admin/requirements/{requirement_id}/reword": "requirement_decide",
    "POST /admin/requirement_edges/remove": "requirement_decide (unsequence)",
    "POST /admin/commitments/{commitment_id}/accept": "commitment_decide",
    "POST /admin/commitments/{commitment_id}/decline": "commitment_decide",
    "POST /admin/commitments/{commitment_id}/move": "commitment_decide",
    "POST /admin/reviews/{review_id}/lever": "review_decide",
    "POST /admin/reviews/{review_id}/close": "review_decide",
    "POST /admin/audit_samples/{sample_id}/decide": "audit_decide",
    "POST /admin/evidence/{evidence_id}/audit": "audit_decide",
    "POST /admin/tasks/{task_id}/audit": "audit_decide",
    "POST /admin/projects/{project_id}/start": "lifecycle",
    "POST /admin/projects/{project_id}/pause": "lifecycle",
    "POST /admin/projects/{project_id}/stop": "lifecycle",
    "POST /admin/projects/{project_id}/complete": "lifecycle",
    "POST /admin/agents/{agent_id}/pause": "lifecycle",
    "PATCH /admin/agents/{agent_id}/schedule": "lifecycle",
    "POST /admin/threads/{message_id}/unfreeze": "lifecycle",
    "POST /admin/projects/{project_id}/briefing/seen": "lifecycle",
    # create
    "GET /admin/roles": "catalog",
    "GET /admin/crews": "catalog",
    "POST /admin/rehearse": "rehearse",
    "POST /admin/projects": "create_project",
    "GET /admin/projects/{project_id}/export": "clone_project (export), rehearse",
    "POST /admin/projects/{project_id}/clone": "clone_project",
    # configure
    "PATCH /admin/projects/{project_id}": "project_configure",
    "POST /admin/projects/{project_id}/teams": "project_configure (add_team)",
    "POST /admin/teams/{team_id}/repin": "project_configure (repin)",
    "POST /admin/projects/{project_id}/mcp_servers": "data_source (add)",
    "POST /admin/projects/{project_id}/mcp_servers/{name}/check": "data_source (check)",
    "DELETE /admin/projects/{project_id}/mcp_servers/{name}": "data_source (remove)",
    "POST /admin/secrets": "credential (save)",
    "POST /admin/projects/{project_id}/amendments": "amend (propose)",
    "POST /admin/amendments/{amendment_id}/cancel": "amend (cancel)",
    "POST /admin/projects/{project_id}/billing_check": "billing_check",
}

#: Routes no tool reaches, and the reason. A reason is required: "we did not
#: get to it" is a fine answer, but it has to be written down as one.
WITHHELD = {
    "GET /admin/secrets/{secret_id}/value":
        "reading a credential back into a model's context is exfiltration, whatever "
        "the scope. The only direction values move on this surface is in.",
    "DELETE /admin/secrets/{secret_id}":
        "deleting a credential can silently break a data source or a project's "
        "billing; the browser shows what depends on it and this cannot.",
    "POST /admin/projects/{project_id}/webhook_secrets":
        "returns the signing secrets in its response body.",
    "POST /admin/projects/{project_id}/github":
        "provisioning takes a GitHub token — a credential value, inbound, that the "
        "chairman should paste where only they can read it.",
    "POST /admin/approvals/{approval_id}/fulfill":
        "fulfilling a need means supplying a secret value to satisfy an agent's "
        "request; that stays in the browser.",
    "POST /admin/projects/{project_id}/mcp_servers/{name}/oauth/start":
        "returns a sign-in URL a browser must open, carrying a single-use state. A "
        "model holding it can do nothing with it.",
    "POST /admin/users": "creating people is not a project decision.",
    "POST /admin/users/password":
        "setting the chairman's own password from a model client would let a "
        "compromised client lock its chairman out of the consent screen.",
    "POST /admin/roles": "the role library is product-wide; a project client edits no part of it.",
    "PATCH /admin/roles/{slug}": "editing the shared role library changes every project pinned to it.",
    "GET /admin/roles/{slug}": "`catalog` serves what a drafting client needs of it.",
    "POST /admin/crews": "the crew library is shared the same way the role library is.",
    "GET /admin/projects/{project_id}/accounts":
        "the real-world account registry; nothing reads it yet and it names outside "
        "services, so it waits for a tool that has a reason.",
    "POST /admin/hypotheses/{hypothesis_id}/extend":
        "the chairman's one-time extension of an expired bet. Not withheld on "
        "principle — no tool has needed it yet.",
    "POST /admin/projects/{project_id}/requirements":
        "the chairman adding scope directly. `review_decide` covers the case that "
        "matters (a lever inside a review); this one waits for a reason.",
    "POST /admin/mcp_clients": "minting an MCP token from an MCP client is a bootstrap loop.",
    "GET /admin/mcp_clients": "a client does not enumerate its siblings, or the token names of any.",
    "DELETE /admin/mcp_clients/{client_id}":
        "a client that can revoke other clients can revoke the one watching it.",
    # The Founder's own surface, unproxied in full: `create` gives a client the
    # same primitives the Founder holds, so it drafts for itself rather than
    # talking to the drafting agent through a keyhole.
    "GET /admin/founder/status":
        "reports whether a platform credential is set; `grade_goal` already says so "
        "when it matters, in the place where it matters.",
    "POST /admin/founder/threads":
        "starting a Founder conversation from a model client means a model opening a "
        "chat with another model, at the chairman's expense.",
    "GET /admin/founder/threads":
        "the chairman's own drafting conversations are theirs to read, in the screen "
        "that renders them.",
    "GET /admin/founder/threads/{thread_id}":
        "same: a draft in progress is a conversation, not an API object.",
    "DELETE /admin/founder/threads/{thread_id}":
        "destroying a chairman's draft is not something a client should be able to do "
        "in passing.",
    "POST /admin/founder/threads/{thread_id}/say":
        "a model driving a conversation with another model is worse than a model "
        "drafting a bundle and being graded on it — which is what `create` offers.",
    "GET /admin/founder/threads/{thread_id}/progress":
        "progress on a turn nothing here can start is nothing to report.",
    "POST /admin/founder/threads/{thread_id}/attach":
        "attaching a draft to the new-project form is a browser action; the form is "
        "where it lands.",
    "PUT /admin/founder/credential": "a platform credential value, inbound.",
    "POST /admin/founder/credential/test": "reaches the platform credential.",
    "DELETE /admin/founder/credential": "product-wide, and destructive to every future draft.",
    "PUT /admin/founder/model": "product-wide configuration, not a project's.",
}


def classified() -> set[str]:
    return set(REACHED) | set(WITHHELD)


def unclassified(live: set[str]) -> set[str]:
    """Routes the app serves that nobody has decided about."""
    return {route for route in live if route not in classified()}


def stale(live: set[str]) -> set[str]:
    """Classifications naming routes that no longer exist — the other drift.

    A withheld entry for a deleted route reads as a considered decision and is
    really a fossil, and the next reader trusts it.
    """
    return classified() - live
