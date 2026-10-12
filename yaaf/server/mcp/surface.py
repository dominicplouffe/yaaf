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
    "POST /admin/flags/{flag_id}/ack": "flag_ack",
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
    "POST /admin/teams/{team_id}/stand_down": "lifecycle (stand_down)",
    "POST /admin/teams/{team_id}/recall": "lifecycle (recall)",
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
    "POST /admin/projects/{project_id}/regrade-goal":
        "grading goal prose calls a model, so this verb spends every time it is "
        "used — and unlike the rest of the drift picture it answers a question "
        "that only changes when the grammar does. The free half (doctrine-drift, "
        "no-adversary, no-standing-scope) is arithmetic over rows and already "
        "reaches every surface that reads project health, so a model holding "
        "this would be paying for an answer it can mostly already see. It stays "
        "a deliberate click by the person whose money it is.",
    "POST /admin/overruns/{overrun_id}/decide":
        "ruling on a month that went over is the chairman's half of the budget "
        "conversation, and it can move the budget in the same motion. A model "
        "holding this verb could approve its own overspend and raise its own "
        "budget, which is not a budget.",
    "GET /admin/projects/{project_id}/overruns":
        "the CEO already carries the overrun it owes an account for in every "
        "context pack; a second read path would be a second definition of the "
        "same conversation.",
    "GET /admin/projects/{project_id}/budget-advice":
        "advice about what the budget should be is for the person who sets it. "
        "The CEO recommends a number through overrun_account, where the "
        "recommendation is recorded as one.",
    "POST /admin/projects/{project_id}/spend-window":
        "funding a measurement window is the chairman committing his own money to "
        "a question. The whole object exists because a model produced three "
        "budget numbers in two hours; handing a model the verb that opens one "
        "would put the estimating back where it failed.",
    "PATCH /admin/projects/{project_id}/spend-window":
        "same reason as opening one — the allowance is his to move, and an "
        "adjustment the CEO could make itself is not an adjustment, it is a cap "
        "it set for itself.",
    "POST /admin/projects/{project_id}/spend-window/stop":
        "ending the measurement early is the chairman's call; a CEO that could "
        "stop a window could stop one that was about to prove it expensive.",
    "GET /admin/projects/{project_id}/spend-window":
        "the CEO already carries the live window and its measured progress in "
        "every context pack, so a second read path would be a second definition "
        "of the same numbers.",
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
    "POST /admin/approvals/{approval_id}/extend":
        "moving a pending approval's deadline is for the receiver that could not "
        "reach the chairman — an outage on its side, not a judgment. A model "
        "holding this verb could keep its own request alive past the default-deny "
        "clock, which is the clock's whole point.",
    "POST /admin/approvals/{approval_id}/fulfill":
        "fulfilling a need means supplying a secret value to satisfy an agent's "
        "request; that stays in the browser.",
    "POST /admin/projects/{project_id}/mcp_servers/{name}/oauth/start":
        "returns a sign-in URL a browser must open, carrying a single-use state. A "
        "model holding it can do nothing with it.",
    "POST /admin/users": "creating people is not a project decision.",
    "GET /admin/users":
        "the box's list of people is the instance admin's, and a client acts for one "
        "person rather than administering the rest.",
    "PATCH /admin/users/{user_id}":
        "promoting, demoting and deactivating people is running the box; a model "
        "that could do it could make its own person an admin.",
    "POST /admin/users/{user_id}/setup-link":
        "a setup link sets somebody's password, so a client holding one could take "
        "over the account it was issued for.",
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
    # App-wide settings, withheld as a block. Every client token is scoped to
    # projects; these routes are *above* every project, so there is no scoping
    # answer that would make one of them safe to hand over. Two of them also
    # carry credential values inbound, and one unwires every project at once.
    "GET /admin/settings/llm-credentials":
        "the inventory names other projects' assignments; `catalog` offers the names and "
        "modes without them.",
    "POST /admin/settings/llm-credentials": "shared credential values are managed by administrators.",
    "PATCH /admin/settings/llm-credentials/{credential_id}": "rotates shared credentials across projects.",
    "DELETE /admin/settings/llm-credentials/{credential_id}": "administrator-owned shared credential lifecycle.",
    "POST /admin/settings/llm-credentials/hide":
        "taking a credential off every menu decides what every other project may pay with.",
    "POST /admin/settings/llm-credentials/unhide":
        "putting one back on the menus is the same decision, pointed the other way.",
    "GET /admin/settings/llm-credentials/{credential_id}/usage":
        "a subscription's plan meter is the account owner's, not any one project's.",
    "GET /admin/settings":
        "the app-wide configuration screen, above every project a client is scoped to.",
    "PUT /admin/settings/anthropic": "a platform credential value, inbound.",
    "POST /admin/settings/anthropic/test": "reaches the platform credential.",
    "DELETE /admin/settings/anthropic":
        "product-wide, and destructive to every future draft.",
    "PUT /admin/settings/anthropic/model": "product-wide configuration, not a project's.",
    "PUT /admin/settings/slack": "Slack token values, inbound, for every project at once.",
    "POST /admin/settings/slack/test": "reaches the app-wide Slack credential.",
    "DELETE /admin/settings/slack":
        "unwires Slack for every project that inherits it, in one call.",
    "GET /admin/projects/{project_id}/slack":
        "which Slack rooms a project mirrors to is wiring the chairman reads in "
        "Settings; no tool has needed it.",
    "POST /admin/projects/{project_id}/slack/channels":
        "creates channels in a real Slack workspace — an outward side effect, and the "
        "chairman's to trigger.",
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
