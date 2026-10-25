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
    "GET /admin/secrets": "credential (list) — names and scopes; plain values and hints "
                          "are stripped",
    "GET /admin/harnesses": "catalog (harnesses)",
    "GET /admin/providers": "catalog (providers)",
    "GET /admin/models": "catalog (models)",
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
    "POST /admin/heads_up/{heads_up_id}/ack": "heads_up_ack",
    "POST /admin/projects/{project_id}/start": "lifecycle",
    "POST /admin/projects/{project_id}/pause": "lifecycle",
    "POST /admin/projects/{project_id}/stop": "lifecycle",
    "POST /admin/projects/{project_id}/complete": "lifecycle",
    "POST /admin/projects/{project_id}/reopen": "lifecycle",
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
    "PATCH /admin/agents/{agent_id}/seat": "project_configure (set_seat)",
    "PUT /admin/projects/{project_id}/credentials/{provider_name}":
        "project_configure (pay_provider) — by credential name, never a value",
    "DELETE /admin/projects/{project_id}/credentials/{provider_name}":
        "project_configure (stop_paying_provider)",
    "PUT /admin/projects/{project_id}/fallback-credential":
        "project_configure (set_fallback) — by credential name, never a value",
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
    "POST /admin/projects/{project_id}/boardroom/seen":
        "the unread count says what the person has not seen, and a model "
        "reading the boardroom is not the person reading it — clearing the "
        "badge on his behalf would hide exactly the news it exists to show. "
        "Speaking through `boardroom_say` already moves the mark, because "
        "whoever answers the room has read it.",
    "POST /admin/projects/{project_id}/health/ack":
        "putting a health suggestion away tidies one person's card and nothing "
        "else — every finding still reaches a model through `project` view=health, "
        "with its put-away state beside it. A model clearing warnings off the "
        "chairman's card on his behalf is the one use this verb has that he did "
        "not ask for.",
    "POST /admin/projects/{project_id}/setup":
        "skipping a setup step or closing the checklist puts a reminder away for "
        "everyone on the project, and its only effect is on what people see. A "
        "model can read the list through `project` view=setup and do any step "
        "itself through `configure` and `credential`; doing a step ticks it off, "
        "which is the only way a model should make one go away.",
    "POST /admin/projects/{project_id}/tempo/keep":
        "keeping the pace is the chairman declining advice about how often his "
        "own money is spent, for everyone on the project at once. A model "
        "answering that for him is the one use of it he did not ask for; the "
        "other answer, switching, is an amendment `configure` already reaches.",
    "POST /admin/projects/{project_id}/regrade-goal":
        "grading goal prose calls a model, so this verb spends every time it is "
        "used — and unlike the rest of the drift picture it answers a question "
        "that only changes when the grammar does. The free half (no-adversary, "
        "no-standing-scope, tempo-vs-rhythm) is arithmetic over rows and already "
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
    "GET /admin/secrets/{secret_id}/history":
        "the history lists every reveal by name and time — who read which credential "
        "is the chairman's audit of the people on his project, and a model reading it "
        "learns exactly when a value was last looked at. The drawer shows it.",
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
    "POST /admin/projects/{project_id}/mcp_servers/{name}/roles":
        "the browser's access map moves one tick without re-sending the address; a "
        "model already changes who receives a source through `data_source` (add), "
        "which re-checks it in the same motion — one door for a model is enough.",
    "POST /admin/projects/{project_id}/mcp_servers/probe":
        "it is the add form's first step — reach an address and say how it signs in — "
        "and stores nothing. A model adding a source gets the same check, and the "
        "same answer, from `data_source` (add) and (check).",
    "POST /admin/projects/{project_id}/mcp_servers/{name}/oauth/start":
        "returns a sign-in URL a browser must open, carrying a single-use state. A "
        "model holding it can do nothing with it.",
    "POST /admin/users": "creating people is not a project decision.",
    "GET /admin/projects/{project_id}/members":
        "who sits on a project is read in the dashboard; no tool has needed it yet.",
    "POST /admin/projects/{project_id}/members":
        "a model does not decide who decides — that is the one verb the chairman "
        "keeps from co-chairs, and it is not handed to a machine either.",
    "PATCH /admin/projects/{project_id}/members/{user_id}":
        "changing somebody's role is deciding who decides.",
    "DELETE /admin/projects/{project_id}/members/{user_id}":
        "removing somebody is deciding who decides.",
    "POST /admin/projects/{project_id}/chairman":
        "handing on the chair changes who is accountable for the project; a person "
        "does that, never their model client.",
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
    "POST /admin/roles/{slug}/adopt-class":
        "moving a library role onto a model class is the update script's step, above "
        "every project.",
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
    "PUT /admin/founder/credential/llm":
        "points the product-wide Founder at a shared credential — an administrator's "
        "call about what pays for every future draft.",
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
    "GET /admin/settings/doctor":
        "the install's own wiring, above every project a client is scoped to.",
    "GET /admin/projects/{project_id}/credentials":
        "the same list rides the project's dashboard (`credentials`), which `project` "
        "already returns — a second read path would be a second definition of it.",
    "GET /admin/settings/models":
        "the install's lineups and ceilings are above every project a client is scoped "
        "to; `catalog (models)` says what each class resolves to, which is what a drafter "
        "needs.",
    "PUT /admin/settings/models/lineup":
        "a lineup moves every class seat on every project of that provider at once — "
        "an administrator's decision, made on the Models screen.",
    "PUT /admin/settings/models/default-provider":
        "where every unpaid project's classes resolve is install-wide; not a drafter's.",
    "PUT /admin/settings/models/ceiling":
        "concurrency per provider is the install's rate-limit budget, shared by every "
        "project on it.",
    "GET /admin/settings/providers/readiness":
        "how ready the install's providers are is an administrator's setup screen, "
        "above every project; a drafter reads `catalog (models)`.",
    "PUT /admin/settings/models/upkeep":
        "whether the install reaches out daily to every provider and the price feed is "
        "an administrator's decision.",
    "POST /admin/settings/models/upkeep/run":
        "it re-reads every key's model list and may move prices every project is "
        "metered by.",
    "GET /admin/settings/llm-credentials/{credential_id}/models":
        "it spends a call on the credential's own key to list a provider's models; the "
        "lineup it helps set is an administrator's.",
    "POST /admin/settings/llm-credentials/{credential_id}/smoke":
        "it spends the credential's own money on a real call; an administrator's check.",
    "POST /admin/settings/llm-credentials/{credential_id}/check":
        "it spends the credential's own money on tens of real calls; an administrator's "
        "check.",
    "GET /admin/settings/llm-credentials/{credential_id}/checks":
        "a credential's check results are about the install's keys, which a project "
        "client never sees.",
    "GET /admin/settings/provider-checks":
        "which models an install has checked is evidence for its lineups — an "
        "administrator's screen, above every project.",
    "GET /admin/settings/web-search":
        "which search backend the install pays for is above every project.",
    "PUT /admin/settings/web-search":
        "it installs a paid key every project's searches bill to — an administrator's.",
    "POST /admin/settings/web-search/test":
        "it spends a query on the install's key.",
    "DELETE /admin/settings/web-search":
        "removing it refuses every searching seat on providers without search of their "
        "own; an administrator's decision.",
    "POST /admin/settings/model-prices/import":
        "importing prices sets the meter every project on the install is stopped by — "
        "an administrator reviews the preview and applies it.",
    "GET /admin/settings/price-feed":
        "where the install reads prices from is install-wide.",
    "PUT /admin/settings/price-feed":
        "repointing the price feed changes what every future import trusts.",
    "PUT /admin/settings/model-context/{model}":
        "a model's window decides when every seat on it compacts its history — an "
        "administrator's setting, beside the prices.",
    "GET /admin/settings/model-prices":
        "the install's price table is above every project a client is scoped to; a "
        "seat refused for want of a price says so in the refusal.",
    "PUT /admin/settings/model-prices/{model}":
        "a price is the meter every project on the install is stopped by. A model "
        "that could set one could price its own runs at zero and spend without limit.",
    "DELETE /admin/settings/model-prices/{model}":
        "same reason as setting one: the meter is the administrator's.",
    "PUT /admin/settings/platform-credential": "a platform credential value, inbound.",
    "POST /admin/settings/platform-credential/test": "reaches the platform credential.",
    "DELETE /admin/settings/platform-credential":
        "removes the install's platform credential — an administrator's decision.",
    "PUT /admin/settings/platform-credential/model":
        "product-wide configuration, not a project's.",
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
