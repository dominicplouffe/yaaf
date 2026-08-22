# Autonomy — how much the CEO decides without you

Every yaaf project runs at an autonomy level you choose. At one end you sign
off on scope, dates, and judgement calls. At the other the CEO does all of
that itself and tells you afterwards. Nothing in between changes what the
platform *checks* — only who holds which verb.

The one sentence that explains the whole design:

> **Authority is configuration. Integrity is structure.**

Authority — who accepts scope, who signs the date, who judges an unverifiable
close, who hands out credentials — is five switches in the constitution, and
you move them whenever you like. Integrity — the platform verifies what it can
verify, never quietly trusts what it cannot, meters every dollar, records
everything, and hands security challenges to a human — does not move at any
setting. An autonomous CEO is not a less-audited CEO; it is a CEO with more
buttons of its own.

Autonomy is *granted by the constitution*, which the agents cannot edit. There
is no setting at which the CEO can widen its own authority.

---

## Picking a level

The New-project form offers two presets, each nothing more than a bundle of
the five switches below. Both are editable afterwards in **Settings → Rules in
force**, as an amendment with a written reason.

| Preset | What it means | Right when |
|---|---|---|
| **Supervised** | You accept scope, sign the delivery date, judge unverifiable closes, and grant credentials. The CEO proposes — and executes any order you give it in the boardroom. | Your first project, or real money is on the line. |
| **Autonomous** | The CEO accepts its own plan, adopts the platform's projected date, judges unverifiable closes itself, and re-scopes existing credentials across its team. Every decision is audited and sent to you; all of it is reversible; your buttons never close. | You want to chair the company rather than operate it. |

Budgets and loop brakes bind identically under both. "Autonomous" is not
"unlimited" — it is "does not need to ask".

## The five switches

Each lives in the constitution's **Autonomy** group.

| Rule | `chairman` (default) | `ceo` |
|---|---|---|
| `plan_authority` | Proposed requirements wait for your click. | The CEO accepts its own proposals. The plan gate still runs the same arithmetic; every acceptance lands in your Decisions record. |
| `scope_authority` | Cut, reopen, restage, reestimate, reword and unsequence are yours alone. The CEO can still *execute your boardroom orders* as directives. | The CEO wields them itself, on the record, reversibly. |
| `commitment_authority` | A proposed commitment waits for you to sign it. | Proposing adopts the platform's own projected date immediately. |
| `verdict_policy` | Observable acceptance tests a script cannot check park on your desk. | `ceo_judgment`: the CEO judges and keeps going — **and every self-judged close is disclosed to you in full.** |
| `capability_authority` | Credential grants are yours; the CEO files *needs*. | The CEO re-scopes credentials it already has across its own team. |

Two notes that matter more than they look:

- **The CEO never writes a date.** `commitment_authority: ceo` lets it accept
  the platform's projection, not invent one. The date is arithmetic in both
  settings; the switch only decides whose signature goes under it.
- **Creating a credential is always yours.** You hold the values. The
  capability switch is about *scoping* what already exists, never about
  minting something new.

## What never moves, at any level

- **The platform verifies everything it can.** `command` acceptance tests are
  run by the platform, not by the agent that wants to pass them. External
  evidence is fetched, hashed and snapshotted server-side.
- **Nothing unverifiable is quietly trusted.** A close the platform could not
  check either lands on your desk (`chairman`) or is disclosed to you in full
  (`ceo_judgment`). There is no third path where it disappears.
- **Money is capped and metered.** The monthly budget pauses the project at
  100%. The real-world spend envelope (`external_monthly_usd_cap`) is a hard
  wall in every billing mode — no approval overrides it, only an amendment.
  A single purchase over `max_usd_per_purchase` needs an approval even when it
  is under the approval threshold.
- **The constitution is yours.** No agent route writes a rule. Amendments
  carry your written reason, an audit entry, and disclosure to the CEO.
- **Security challenges go to a human.** A captcha or a 2FA prompt is handed
  to you through the *assist* protocol; the platform never tries to defeat one.
- **Everything is recorded.** Every acceptance, cut, verdict, grant and
  purchase is in the audit log and on the Decisions screen, whoever made it.
- **Your buttons never close.** Pause, stop, veto, amend, reopen and audit are
  chairman powers at every autonomy level.

## Money, in the world

Model spend and real-world spend are two different budgets.

| Rule | Default | What it does |
|---|---|---|
| `monthly_usd_cap` / `monthly_token_cap` | 1000 USD / 200M | Model spend. Warn at `budget_warn_ratio`; the project pauses itself at 100%. |
| `external_monthly_usd_cap` | 500 USD | Purchases, subscriptions, services. A hard wall in **every** billing mode; a purchase that would cross it is refused with the arithmetic shown. |
| `max_usd_per_purchase` | 100 USD | Above this, one purchase needs an approval regardless of the threshold. |

There is deliberately **no `spend_authority` switch**: the money dial *is*
these three numbers, and a switch would only duplicate them. Inside the
envelope an autonomous CEO buys without asking; outside it, the ordinary
approval machinery answers.

Purchases go through an idempotent two-step — `purchase_begin` clears the
envelope *before* money moves and reserves the headroom, `purchase_finish`
books the ledger row with the receipt. Finishing twice never double-books, and
a purchase that somehow overshoots is **recorded with an audit row and
reported to you** rather than refused: the money already moved, and refusing
the record would only hide it.

**Revenue** is a ledger kind written *only* by signed webhooks. No agent verb
creates one, so income cannot be invented — and revenue is excluded from the
budget sums, so a project is never paused for earning.

## Credentials as capabilities

Whatever you store, the company can use. yaaf does not police what you add to
your own deployment; its job is to make a stored credential *usable* and to
meter what it buys.

- A **recognizer library** means a granted environment variable arrives with
  what it is, how to speak to it, and what to install. Unrecognized
  credentials still inject — recognition is a convenience, never a fence.
- The **needs loop**: an agent that lacks a capability files a `need`, which
  rides the approval machinery *and* lands as a boardroom line the Slack
  bridge mirrors. You fulfil it in one motion on the Decisions screen — the
  credential is stored, scoped to roles, and the need approved together.
- With `capability_authority: ceo`, the CEO re-scopes existing service
  credentials across its own team. Never billing credentials, never values.

## The last mile: when a human is required

Some steps cannot be automated and should not be. The sandbox ships with
Chromium and Playwright, so a browser is an ordinary tool in the box — but a
captcha, a 2FA code, or any human-only challenge triggers the **assist**
protocol instead:

1. The agent saves its session state to the persistent workspace.
2. It hands the step to you, with context, as a decision on your desk.
3. Your answer rides back verbatim in the decision note and wakes the asker.

The platform never attempts to defeat a security challenge on someone else's
service. That is a rule, not a limitation to be engineered around.

## Waking on the world

Autonomy is not much use to a company that only wakes on a timer.

- **Signed webhooks** carry a `wake` field that wakes the CEO or a named
  agent, classed urgent, still under every wake-rate brake.
- **Per-agent daily schedules** (`agents.daily_at`) generalize the CEO's
  standup: a news agent at 07:00, a metrics agent at market close.
- The **account registry** records every identity the company creates in the
  world, with a situational owner-of-record — you, or the project itself. It
  is record-only by design: winding a project down is your decision, and the
  registry exists so you can see what needs settling.

## Related

- [GOVERNANCE.md](GOVERNANCE.md) — every rule, including these, and how to
  change one.
- [CREATING_A_PROJECT.md](CREATING_A_PROJECT.md) — where you pick a preset.
- [INTEGRATIONS.md](INTEGRATIONS.md) — credentials, webhooks, and the
  connectors that feed attested evidence.
