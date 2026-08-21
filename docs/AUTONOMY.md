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
unverified closes stay spot-audit-eligible; money is capped and metered;
everything is audited; the constitution stays the chairman's; security
challenges always go to the human) and turns the authority rules into a new
constitution group — seven switches, three presets (Supervised / Advised /
Autonomous) on the New-project form. Autonomy is granted by the constitution,
never seized.

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
  call under `verdict_policy: ceo_judgment`, permanently spot-audit-eligible.
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
- **Needs arrive over chat** — Slack today, Telegram or WhatsApp when a
  connector exists — answerable remotely; fulfilling one stores and scopes
  the credential in one motion.
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
- Two bugs regardless of design: `POST /agent/commitments` has no sandbox
  tool (the CEO cannot actually propose a date), and `ceo.json` says
  `bash: false` while `data_processing: true` grants real Bash.
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
| A | Missing verbs (reopen · restage · reestimate · unsequence · dial-assignable cut · ask), the two bug fixes, removal of the egress allowlist, widened directive scope so chairman orders execute | not started |
| B | The Authority constitution group, seven switches, three presets, decision digest, `verdict_policy: ceo_judgment` with audit-sampled self-closes | not started |
| C | Spend envelope: `external_monthly_usd_cap` (always-on, any billing mode), `max_usd_per_purchase`, atomic purchase actuator over the side-effect ledger, optional virtual-card hardening | not started |
| D | Credentials become capabilities: recognizer library (packages + tooling), needs queue over chat (`tool_request` wired; Slack first, any messenger later), CEO delegation to team roles, `revenue` ledger kind fed by signed webhooks | not started |
| E | Browser lane on the open network, `AWAITING_HUMAN` suspension with live take-over (captcha/2FA), account registry (record-only, situational owner), external-event wakes and per-agent schedules | not started |

Order is forced: verbs before authority, authority before money, money
before self-provisioning, and the browser last.
