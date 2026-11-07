# Decide anywhere — design

*A decision yaaf asks of you reaches you wherever you are, can be made well in seconds, and is
never decided by your silence unless you were actually told.*

| | |
|---|---|
| **Status** | **Phase A built** — the item, preview/commit, the silence rule, the downtime pause, reach receipts, local metrics, and the Inbox drawing server items. Phases B–F are designed here, not built. |
| **Requirements** | [`REQUIREMENTS.md`](REQUIREMENTS.md) — Phase A, numbered, with acceptance tests |
| **Related** | [`../README.md`](../README.md) (IA redesign), [`../product-experience-audit/`](../product-experience-audit/README.md) (F2 decision budget, F3 one status protocol, G3 decision card), `docs/AUTH.md`, `docs/INTEGRATIONS.md`, `docs/MCP_SERVER.md` |

---

## 1. Why this exists

yaaf's pitch is *start it Sunday, read its work Friday.* Between those days the project files
decisions only a person may make — spend, legal, tool access, a 2FA code, scope, a date, a
verdict — and **silence is itself a decision**: an approval denies itself after seven days (or
*approves* itself, on a project whose rules say so), a question falls back to the CEO's stated
default, a finished delivery closes itself.

That is a sound rule only if the person was reached. Today, often, they are not:

| Finding (measured or read from code, 7 Nov 2026) | Consequence |
|---|---|
| The desktop install (`deploy/install_desktop.sh` → `yaaf setup`, branch `claude/beautiful-gates-rs63cl`) configures no notification channel. With none configured, `chairman_notify` only logs. | On the install path most new users will take, nobody is told anything, and every clock still runs. |
| A laptop install sleeps. The janitor does not run while it does, then wakes and expires every clock that passed. | A decision can be filed Friday, never seen, and decide itself the moment the lid opens on Monday. |
| The dashboard binds `127.0.0.1`. Notice links exist only when `YAAF_PUBLIC_BASE_URL` is set. | "Decide it in your Inbox" is a dead end away from the machine. |
| Notices go to the chairman only (`notify.py`: splitting the mail ration "is a design of its own"). | A co-chair covering a holiday hears nothing. |
| Slack's only inbound shape is a plain chairman message in the boardroom channel (`slack/socket.py:82`). | Slack shows the project but cannot carry a decision. |
| Inbox rows are built in the browser (`web/src/desk.tsx` `itemsFor`); notices compose their own text; MCP and the CLI count from `policy.needs_you`. | There is no server-side decision object for any new surface to render. `test_inbox_links.py` exists only to keep two spellings of one key in step. |
| Browser sessions idle out after 3 days (`identity.py:54`). | A phone opened from a notification after a quiet weekend lands on a password field. |
| The rehearsal's scripted crew files no approvals or questions; 11 of 12 examples run autonomous; only example 01 stopped for its chairman (7 times, all in the first ticks). | **Real decision volume is unknown.** Nothing in the product measures it. |

## 2. Principles

These are yaaf's existing rules, restated for every surface this design adds. A new surface that
cannot keep one of them is not built.

1. **Opening never decides.** A link, a notification tap or a prefetch shows a decision; only an
   authenticated POST makes one (RFC 9110 §9.2.1; RFC 8058 requires POST for one-click unsubscribe
   for the same reason; Microsoft Safe Links and similar scanners open links before people do).
2. **One decision, every surface.** The Inbox, email, Slack, push, MCP and the CLI render one
   server-side object. No surface composes its own summary or its own count.
3. **Nothing irreversible in one step.** Preview, then commit with a single-use ticket bound to
   exactly what was previewed — the pattern `mcp/confirm.py` already enforces for model clients.
4. **Every write names its door.** `user:<id>` from the dashboard, `via mcp:<client>`,
   `via token:<name>`, and — when they exist — `via slack`, `via device:<name>`.
5. **Silence counts only if you were told.** *(New, and the centre of Phase A.)* A clock may decide
   on a person's behalf only after the decision reached someone who could have decided it.
6. **Say what silence does.** Every rendering carries the default and its moment.
7. **No relay we run, no account you need.** Open source, self-hosted, no telemetry. Delivery uses
   infrastructure the user already has (their browser's push service, their Slack, their mail
   server), never a yaaf-operated service.

## 3. Architecture

```
                 ┌──────────────── one producer ────────────────┐
  approvals ─┐   │  yaaf/server/needs_you.py                     │
  asks      ─┤   │   items(session, project, cons) → [item v1]   │──► GET /observe/needs-you
  scope     ─┤   │   key · family · headline · case · options    │──► /observe/desk (per project)
  date      ─┼──►│   recommendation · silence · deadline · undo  │──► (MCP desk drops them: agent text)
  verdicts  ─┤   │   reached? · tier                             │──► (B–F) push, Slack, email card
  sign-off  ─┤   └──────────────────────┬────────────────────────┘
  bets      ─┤                          │ preview → ticket → commit
  frozen    ─┘                          ▼
                 POST /admin/projects/{id}/needs-you/preview  ─► about_to + single-use ticket
                 POST /admin/projects/{id}/needs-you/commit   ─► the existing verb, as the person

  chairman_notify(item_key=…) ─► reach receipt (delivered: email | webhook)
  a decider's read of the list ─► reach receipt (seen: dashboard | mcp | cli)
  janitor: expire_* / complete_itself ─► consult receipts; extend once; fail safe
  janitor: sweep gap > threshold ─► clocks paused for the time the box was down
```

### 3.1 The item (`yaaf.needs_you/1`)

One shape for every family. Named *needs-you* rather than *decision* because the `decisions`
table already holds the append-only record of bets, and because "Needs you" is the name the
Inbox, `policy.needs_you` and the badge already use.

| Field | Meaning |
|---|---|
| `key` | The Inbox key, unchanged: an approval or ask id, `{project}-plan`, `{project}-commitment`, `{project}-signoff`, `{project}-verdict-{R}`, `{project}-frozen`, a bet id. |
| `family` | `approval · ask · scope · commitment · verdict · signoff · bet · frozen` |
| `kind` | The approval kind (`spend`, `legal`, `tool_request`, `owner_decision`, `capacity`, `assist`, `brief`), else the family. |
| `label`, `headline`, `case` | The row's label, one line, and the full case in plain text. |
| `recommendation`, `amount_usd` | As filed. |
| `filed_at`, `deadline` | When it arrived; when silence decides (null where nothing decides itself). |
| `silence` | What happens at `deadline` with no answer, in words. |
| `undo` | Whether a yes can be taken back, where the platform knows. |
| `options` | `{id, label, consequence, style, irreversible, needs_note}` — what the person may choose. |
| `commit` | Whether the options can be committed through the protocol; when false, `path` is where the lever is. |
| `path` | The dashboard route that holds this decision. |
| `tier` | 1 stopped · 2 on a clock (the Inbox's existing ranking). |
| `reached` | `{ok, first_at, extended}` — whether silence may count yet, and why. |

### 3.2 The silence rule

> **A decision may decide itself by silence only once it has reached somebody who could have
> decided it, early enough to act.**

* **Reached** = a receipt exists for the item, from a *decider* (the chairman or a co-chair), dated
  at least `lead` before the deadline: `lead = min(6h, 25% of the window)`.
  * *delivered* — `chairman_notify` dispatched it to a configured channel (an email inside the
    ration, or the webhook). This is dispatch, not proof of reading; it is the strongest signal a
    self-hosted box can have without a read receipt.
  * *seen* — the item was in a list a decider loaded: the Inbox poll, the MCP desk, the CLI.
* **At the deadline, not reached:** the clock is extended once by 24 hours, and the platform raises
  an alarm on every channel it has and in the boardroom: *"yaaf could not reach you about …"*
* **At the extended deadline, still not reached:** it resolves in the *safe* direction, whatever
  the project's own default — an approval is **declined** (never approved on silence nobody heard),
  a question takes the CEO's stated default, and a finished delivery does **not** close itself.
* **Never a deadlock.** Extension happens once. The house rule "a vacationing chairman degrades,
  never deadlocks" holds; what changes is that degrading now requires having been told.

### 3.3 The box was asleep

The janitor records when it last swept. When the gap between sweeps exceeds a threshold, every
open approval and question on a running project has its deadline moved by the gap, with one audit
row per project. A clock measures time the box was able to tell you something.

## 4. Surfaces (Phases B–F)

| Surface | Built in? | Notes |
|---|---|---|
| **Phone web app + Web Push** | Core (C) | The universal surface. iOS 16.4+ only from the Home Screen; **iOS shows no action buttons**, so a notification opens the card and never decides. Android allows ~2 actions (`Notification.maxActions`): *Remind me at the next digest* and *Open*. Sending is outbound-only with per-install VAPID keys; payloads are end-to-end encrypted (RFC 8291). Every push must show a notification or iOS revokes the subscription. |
| **Push on the desktop itself** | Core (B) | `localhost` is a secure context, so the browser on the machine running yaaf gets real notifications with zero setup. |
| **Slack** | Core (D) | Socket Mode already in use; `block_actions` and `view_submission` arrive on the same socket, so no public URL. DM card → **Approve…** opens a modal (trigger_id, 3 s) → submit is the commit → `chat.update` every copy. App Home is the Inbox in Slack. A `manifest_json` link prefills the app. The clicking Slack user must map to a decider. |
| **Email** | Core | Plain text plus an HTML card. Links open the card. Gmail actions and AMP need sender registration — out. |
| **Claude (MCP)** | Exists | Already a surface; reads the same list once the desk carries it. |
| ntfy / UnifiedPush, Telegram, Discord, Matrix, Home Assistant | Community (F) | All work without a public URL; each touches a third party or one platform. Built against the published protocol and the signed webhook. |
| Native apps, Gmail actions, AMP, Teams buttons | **No** | Each needs a project-run push relay or a registration with Google or Microsoft. |

**Reach from a phone (B)** — `yaaf setup` gains *Where should I find you when you're away?*:
this computer (desktop push), your phone (Tailscale Serve → `*.ts.net` HTTPS → QR code that
installs the web app and pairs the device), Slack (manifest link + two tokens), or later.
`yaaf doctor` warns when nothing can reach the chairman away from the machine.

**Device tokens (B)** — a principal beside personal and MCP tokens: `ydt_…`, minted by scanning
a QR code, scoped to read and decide, stamped `user:<id> via device:<name>`, revocable in Account.
It removes the 3-day idle logout from the phone without weakening browser sessions.

**Attention (E)** — each item is *Now* (its consequence lands before the next digest; assist
requests; alarms), *Digest* (three a day by default — Fitz et al. 2019: 3×/day batching improved
attention and stress, hourly did little, none raised anxiety), or *never interrupts* (FYI). An
escalation ladder — push → Slack → email → co-chair → last call — is printed on the card.

**The card (C, D)** — decision first: what is asked, the recommendation and why, what silence does
and when, whether it can be undone. Friction scales with stakes: snooze from the notification;
every decision on the card; irreversible ones confirm with the amount on the button and a passkey
where the device has one; above a threshold, confirm the amount (number matching). An agent that
files something that does not fit one screen marks it a *long read*: digest only, never an
interruption.

## 5. Phases

| Phase | Scope | Depends on |
|---|---|---|
| **A** | The item, preview/commit, the silence rule, the downtime pause, reach receipts, local metrics, the Inbox rendering server items | — |
| **B** | `yaaf setup` step, desktop push, Tailscale detection, QR device pairing, device tokens | A; the desktop install branch |
| **C** | Installable web app, phone push, the phone decision layout, bottom bar, optional passkey | A, B |
| **D** | Slack cards, modal confirm, in-place updates, App Home, manifest link | A |
| **E** | Digests, the Now tier, escalation ladder, co-chair delivery | A; real usage from A's metrics |
| **F** | Published protocol, adapter kit, one reference adapter (ntfy) | A |

Every new door gets attack tests in `tests/selftest_invariants/` (house rule 2): a press from
someone else, a forged payload, a stale card pressed after the decision, a replayed press, a
revoked device.

## 6. What "world-class" is measured by

All local, on the person's own Account page; nothing leaves the box.

* decisions that decided themselves **without ever reaching anyone** — the rule makes this zero by
  construction, and the page proves it;
* median time from filed to decided;
* decisions taken by silence, by family;
* decisions per week — the number this design could not find anywhere when it was written;
* (with C/D) approvals committed without the card being opened — the rubber-stamp signal.

Targets are set after a month of dogfooding, not before.

## 7. Research notes

Platform facts were checked on 7 Nov 2026 through search summaries of primary sources (WebKit
blog, MDN, Chrome developers, Slack API docs, Tailscale KB, ntfy docs, Apple HIG); several
vendor sites could not be fetched directly. Prior art: Ramp and Navan (Slack approvals), GitHub
deployment reviews (fail closed after 30 days), PagerDuty and incident.io (urgency tiers, escalation,
"test your notifications" nudges), Home Assistant (`authenticationRequired` on sensitive actions,
a project-run relay with a 500/day cap), Mattermost and ntfy (ID-only pushes through a relay),
Windmill (per-event approval URLs — the counter-example to per-person links), n8n (a Teams node
that auto-decided: callbacks must be verified and idempotent), HumanLayer (approval enforced in the
function, not the prompt), Okta/Microsoft number matching (accidental approvals). Healthcare
decision-support alerts are overridden 50–95% of the time once they become noise; that is the
failure the attention engine exists to avoid.
