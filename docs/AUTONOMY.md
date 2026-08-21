# The CEO Charter — autonomy plan of record

The chairman's brief: the CEO is a glorified agent. It should be able to take
any decision it wants, not pile demands on the chairman; autonomy should be a
switch at project creation; with a budget and a card it should be able to
register a domain or sign up for any service (or delegate that to its team);
when a captcha or 2FA blocks it, the human steps in; and it must be able to
change anything on the plan — including anything the chairman asks for.

**Full design (findings, pattern, architecture, rollout, open questions):**
https://claude.ai/code/artifact/5664c416-5ece-43cb-966f-4f36e20ca8b9

This file is a table of contents, not a copy. Edit the artifact; keep this
list of statuses honest.

## The one idea

**Authority is configuration; integrity is structure.** Today every gate
conflates "the record must be honest" with "the chairman must click". The
design keeps the integrity rules structural at every autonomy level (done is
never self-graded, money is capped and metered, everything is audited, the
constitution stays the chairman's, security challenges always go to the
human) and turns the authority rules into a new constitution group —
seven switches, three presets (Supervised / Advised / Autonomous) on the
New-project form. Autonomy is granted by the constitution, never seized.

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
  acquisition; blocked egress is an invisible squid 403.
- Rails that already exist and the design reuses: external-spend ledger
  folded into the monthly cap, spend-approval gate, idempotent side-effect
  ledger, per-role egress allowlists, scoped encrypted secrets with the
  service/billing refusal split, the unwired `tool_request` approval kind,
  the resident chat session (the one process that outlives a turn).

## Status

| | Stage | Status |
|---|---|---|
| A | Missing verbs (reopen · restage · reestimate · unsequence · dial-assignable cut), the two bug fixes, widened directive scope so chairman orders execute | not started |
| B | The Authority constitution group, seven switches, three presets, decision digest, `verdict_policy: platform_only` | not started |
| C | Spend envelope: `payment` secret kind, atomic purchase actuator over the side-effect ledger, `external_monthly_usd_cap` (always-on, any billing mode), virtual-card issuance | not started |
| D | Capability catalog over the data-source machinery, `tool_request` wired, egress denials visible, delegation to team roles | not started |
| E | Browser lane behind the proxy, `AWAITING_HUMAN` suspension with live take-over (captcha/2FA), account registry | not started |

Order is forced: verbs before authority, authority before money, money
before self-provisioning, and the open internet last.

## Open questions (the chairman's, not a session's)

1. Payment rail: virtual-card issuer vs chairman-stored card behind the
   same actuator.
2. Delegation depth: CEO-only external action first, or sub-envelopes for
   team roles immediately.
3. Under full autonomy, unverifiable observables are refused as acceptance
   tests (recommended) rather than CEO-self-verdicted — confirm.
4. Identity kit: per-project email, platform-held SMS, owner-of-record.
5. `reopen` as a chairman power in every mode (recommended) — confirm.
6. Advised mode's silence-is-consent inside the envelope — confirm.
