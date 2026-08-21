# The CEO Charter — autonomy plan of record

The chairman's brief: the CEO is a glorified agent. It should be able to take
any decision it wants, not pile demands on the chairman; autonomy should be a
switch at project creation; with a budget and stored credentials it should be
able to register a domain or sign up for any service (or delegate that to its
team); when a captcha or 2FA blocks it, the human steps in; and it must be
able to change anything on the plan — including anything the chairman asks
for.

**Full design (findings, pattern, architecture, rollout, decisions):**
https://claude.ai/code/artifact/5664c416-5ece-43cb-966f-4f36e20ca8b9

This file is a table of contents, not a copy. Edit the artifact; keep this
list of statuses honest. v3 — revised on two rounds of the chairman's answers, 21 Aug 2026.

## The one idea

**Authority is configuration; integrity is structure.** Today every gate
conflates "the record must be honest" with "the chairman must click". The
design keeps the integrity rules structural at every autonomy level (the
platform verifies everything it can and never quietly trusts what it can't —
a close it couldn't check is on the chairman's desk or, when the CEO judged
it under the dial, disclosed to him in full; money is capped and metered;
everything is audited; the constitution stays the chairman's; security
challenges always go to the human) and turns the authority rules into a new
constitution's Autonomy group — seven switches by stage E (four planning
switches shipped in B; spend and capability switches arrive with their
machinery in C and D), presets on the New-project form (Supervised and
Autonomous shipped; Advised when its review-window exists). Autonomy is
granted by the constitution, never seized.

## Decisions the chairman has made (21 Aug 2026)

- **Credentials are capabilities.** Whatever the chairman stores — a card,
  POP/SMTP, a registrar key — the company can use. yaaf never polices what
  somebody adds to their own open-source deployment. The platform's job is to
  make a stored credential *usable* (a recognizer library installs the
  packages and surfaces the tooling) and to meter and cap what
  it buys. Virtual-card issuance is recommended optional hardening, never a
  requirement.
- **Verify with the data at our disposal; never demand external validation
  the project can't produce.** Unverifiable closes are the CEO's judgment
  call under `verdict_policy: ceo_judgment` — as built, each self-judged
  close is disclosed to the chairman in full, stronger than the sampling
  first sketched.
  The CEO may ask the chairman when it feels strongly — allowed always,
  required never. Explore and Operate must never stall waiting on a human.
- **Owner-of-record is situational** — the chairman or the project itself,
  decided per account in the boardroom; the registry records it.
- **Delegation** rides the existing role-scoping mechanics, exercised by
  whoever holds `capability_authority`.
- **The egress allowlist is removed entirely** (round 2). No allowlist, no
  domain whitelisting; agents reach the open internet. Budgets, fresh
  containers, scoped secrets, audit and spot-checks are the containment.
- **Revenue** exists only as a signed-webhook-fed ledger kind so income can
  never be invented; the P&L itself is the CEO's to build with its agents.
- **Needs arrive over chat** — the yaaf dashboard, Slack, or any messenger
  with a connector (Telegram, WhatsApp) — answerable remotely; fulfilling
  one stores and scopes the credential in one motion.
- **Modes never change**: Explore adapts by itself; Deliver and Operate
  keep their rigid shapes on purpose.
- **External wakes**: signed webhooks and watched mailboxes wake the right
  agent, plus per-agent schedules, under the existing wake-rate brakes.
- **No teardown machinery**: winding down is a CEO decision; a project the
  chairman stops for good leaves its services his to settle. The account
  registry stays record-only.
- **Reopen** ships as a chairman power in every mode (today a wrong verdict
  is permanent for everyone).
- **Advised** stays as the middle preset: announce, wait a window, silence
  means go.

## Verified findings the design answers (file:line-cited in the artifact)

- There is no stage entity: a stage is an integer column; the open stage is
  `min(stage)` over open requirements. `MET`/`CUT` are terminal **for
  everyone, the chairman included** — no route re-opens settled scope.
- The CEO can only append: propose requirements (chairman accepts), add
  dependency edges (append-only for everyone). No `PATCH` exists; `stage`,
  `est_runs`, `statement`, and acceptance tests are write-once.
- `cut` is chairman-only; directives are scoped to three cadence fields, so
  chairman feedback cannot be executed by the CEO at all.
- Two suspected bugs: `POST /agent/commitments` had no sandbox tool (the
  CEO could not actually propose a date) — real, fixed in stage A. The
  `ceo.json` `bash: false` + `data_processing: true` pairing turned out to be
  documented design (sandbox_entry: data_processing is the same shell under a
  narrower charter; a python-only grant would be theatre) — no change.
- No actuator exists: no browser, no payment path, no capability
  acquisition; blocked egress is an invisible squid 403, unlogged.
- Rails that already exist and the design reuses: external-spend ledger
  folded into the monthly cap, spend-approval gate, idempotent side-effect
  ledger, scoped encrypted secrets, signed-webhook evidence, the unwired
  `tool_request` approval kind, the resident chat session (the one process
  that outlives a turn — the seed of the captcha/2FA human-assist protocol).

## Status

| | Stage | Status |
|---|---|---|
| A | Missing verbs (reopen · restage · reestimate · unsequence · ask), the commitments-tool fix, removal of the egress allowlist, widened directive scope so chairman orders execute (accept · cut · reopen · restage · reestimate · reword · unsequence, citing his boardroom message) | **done** — 433 tests green; dashboard buttons for the new verbs are stage B UI work |
| B | The authority dial: `plan_authority` · `scope_authority` · `commitment_authority` · `verdict_policy` in the constitution's Autonomy group; the CEO's `scope_act` self-exercise verb through the same apply helpers as the chairman's buttons; commitment self-signature (adopting the platform's date); CEO judgment on unverifiable closes with full per-close disclosure (stronger than sampling); every exercise audited + sent to the chairman | **core done** — 443 tests green. New-project preset cards (Supervised / Autonomous) and the chairman's Reopen button shipped with it. Remaining: the Advised preset's review-window machinery (`decision_review_hours`) — the card appears when the machinery exists |
| C | Spend envelope: `external_monthly_usd_cap` (a hard wall in every billing mode — no approval overrides it, only an amendment) and `max_usd_per_purchase` (over it, an approval even below the threshold); the purchase actuator (`purchase_begin` clears the envelope before money moves over an idempotent side-effect, `purchase_finish` books the ledger row with the receipt — finishing twice never double-books); `spend_book` faces the same walls; janitor warns at the budget ratio, pauses nothing | **done** — 453 tests green. Decision recorded: no `spend_authority` switch — the money dial is the three numbers, a switch would duplicate them. Virtual-card issuer integration deliberately unbuilt until someone brings an issuer: optional hardening, and a seam nothing uses is scaffolding |
| D | Credentials become capabilities: recognizer library (packages + tooling), needs queue over chat (`tool_request` wired; dashboard + Slack first, any messenger later), CEO delegation to team roles, `revenue` ledger kind fed by signed webhooks | not started |
| E | Browser lane on the open network, `AWAITING_HUMAN` suspension with live take-over (captcha/2FA), account registry (record-only, situational owner), external-event wakes and per-agent schedules | not started |

Order is forced: verbs before authority, authority before money, money
before self-provisioning, and the browser last.
